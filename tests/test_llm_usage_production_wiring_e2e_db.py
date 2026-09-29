"""LLM Usage Production Wiring E2E（Phase 3.12 Step 56）——**DB-gated**。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

真实链路（只 Fake 外部边界）：
    POST /api/ai/chat              （真实 FastAPI app / Router / Orchestrator）
        ↓ 真实 RagService / TextToSQLService / Tool 执行边界
    **生产默认 LLM Client**        ``get_default_llm_client()``（真实接线）
        ↓
    DatabaseLLMAccountingSink      （真实 sink；非 Noop）
        ↓
    ai_ops.llm_usage_record
        ↓
    GET /api/observability/assistant-trace/{A}   → llm_usage[]

Fake 仅限：
    * LLM HTTP transport（httpx.MockTransport；0 网络 / 0 DeepSeek）
    * Vector Search / Embedding（确定性 chunk）
    * Tool handler 的 DB 边界
**不 Fake**：Router / Orchestrator / RagService / TextToSQLService /
          默认 Client / Sink / Repository / Assistant Trace。

数据：只使用 synthetic 行（request_id 前缀 ``step56-``）；teardown 定向
      DELETE 三张表的本模块行（**不使用 TRUNCATE**）并断言 residue = 0。
"""
from __future__ import annotations

import dataclasses
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from backend.app.api import orchestrator_chat as orch_module
from backend.app.config import settings
from backend.app.db.models.llm_usage_record import LLMUsageRecord
from backend.app.db.session import get_engine, get_session_factory
from backend.app.llm import client as client_module
from backend.app.llm.client import (
    get_default_accounting_sink,
    get_default_llm_client,
    reset_default_llm_client,
)
from backend.app.llm.deepseek_provider import DeepSeekProvider
from backend.app.main import app
from backend.app.services.assistant_trace import assistant_trace_scope
from backend.app.services.context_builder import ContextBuildResult
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
)
from backend.app.services.rag_observability_runtime import (
    get_observed_rag_service,
)
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)
from backend.app.services.text_to_sql_service import (
    REFUSAL_MARKER,
    RESULT_STATUS_REFUSAL,
    TextToSQLService,
)
from backend.app.services.vector_search_service import VectorSearchResult

_ENDPOINT = "/api/observability/assistant-trace"
_RAG_QUESTION = "采购入库的操作步骤是什么"
_TOOL_QUESTION = "查询物料 MAT-001 当前库存"
_PREFIX = "step56-"
_TOOL_NAME = "get_inventory"
_RAG_ANSWER = "STEP56-RAG-ANSWER"

#: 本模块产生的 Assistant request_id（teardown 定向清理）
_CREATED_REQUEST_IDS: list[str] = []


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _engine():  # noqa: ANN202
    engine = get_engine()
    if engine is None:
        pytest.skip("DATABASE_URL 未配置")
    return engine


# ============================================================
# 外部边界 Fake
# ============================================================

def _chunk(chunk_id: int, *, document_id: int = 10) -> VectorSearchResult:
    return VectorSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=0,
        content="STEP56-E2E-CHUNK",
        distance=0.1,
        similarity=0.9,
        metadata={"heading": "采购入库"},
    )


class _FakeVectorSearch:
    def __init__(self, results: list[VectorSearchResult] | None = None) -> None:
        self._results = results if results is not None else [
            _chunk(101),
            _chunk(102, document_id=20),
        ]

    async def search(self, query: str, *, top_k: int) -> list[VectorSearchResult]:
        return list(self._results)


class _EchoContextBuilder:
    def build(self, results: Any) -> ContextBuildResult:  # noqa: ANN401
        used = tuple(results)
        return ContextBuildResult(
            text="\n".join(r.content for r in used),
            used_chunks=used,
            total_chars=64,
            truncated=False,
            dropped_count=0,
        )


class _FakeInventoryHandler:
    async def __call__(self, arguments: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"material_code": arguments.get("material_code"), "qty": 120}


def _chat_body(provider_request_id: str, content: str = _RAG_ANSWER) -> dict[str, Any]:
    return {
        "id": provider_request_id,
        "model": "step56-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 22,
            "total_tokens": 33,
        },
    }


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module", autouse=True)
def _schema():
    if not _env_flag("RUN_DB_TESTS"):
        yield
        return
    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()
    yield


@pytest.fixture(autouse=True)
def _cleanup():
    """定向清理 + residue 断言（仅删除本模块自己的行）。

    RAG 记录的特殊情况：**LLM 失败的请求**同样会留下 RAG 记录
    （Step 43：异常路径也产生 observation），但失败响应不会返回
    assistant_request_id → 无法按 ID 追踪。因此额外使用
    ``rag_execution_record.id`` **水位线**（用例开始前的最大 id）
    清理本用例期间新增的行（测试串行执行，水位线安全）。
    """
    if not _env_flag("RUN_DB_TESTS"):
        yield
        return
    _CREATED_REQUEST_IDS.clear()
    _delete_own_rows()
    rag_watermark = _max_rag_id()
    yield
    requests = list(_CREATED_REQUEST_IDS)
    _delete_own_rows(requests)
    _delete_rag_rows_after(rag_watermark)
    with _engine().connect() as conn:
        assert int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.rag_execution_record "
                    "WHERE id > :watermark"
                ),
                {"watermark": rag_watermark},
            ).scalar_one()
        ) == 0, "RAG 表残留（Step 56 测试行未清理）"
        assert int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.llm_usage_record "
                    "WHERE request_id LIKE :prefix OR provider = :provider"
                ),
                {"prefix": f"{_PREFIX}%", "provider": "step56-provider"},
            ).scalar_one()
        ) == 0, "LLM Usage 表残留（Step 56 测试行未清理）"
        if requests:
            assert int(
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM ai_ops.tool_execution_record "
                        "WHERE request_id = ANY(:ids)"
                    ),
                    {"ids": requests},
                ).scalar_one()
            ) == 0, "Tool 表残留"


def _max_rag_id() -> int:
    with _engine().connect() as conn:
        return int(
            conn.execute(
                text("SELECT COALESCE(MAX(id), 0) FROM ai_ops.rag_execution_record")
            ).scalar_one()
        )


def _delete_rag_rows_after(watermark: int) -> None:
    with _engine().begin() as conn:
        conn.execute(
            text("DELETE FROM ai_ops.rag_execution_record WHERE id > :watermark"),
            {"watermark": watermark},
        )


def _delete_own_rows(requests: list[str] | None = None) -> None:
    params: dict[str, Any] = {"prefix": f"{_PREFIX}%"}
    if requests:
        params["ids"] = requests
    with _engine().begin() as conn:
        conn.execute(
            text(
                "DELETE FROM ai_ops.llm_usage_record "
                "WHERE request_id LIKE :prefix "
                "OR assistant_request_id LIKE :prefix "
                + ("OR assistant_request_id = ANY(:ids) " if requests else "")
            ),
            params,
        )
        if requests:
            conn.execute(
                text(
                    "DELETE FROM ai_ops.tool_execution_record "
                    "WHERE request_id = ANY(:ids)"
                ),
                params,
            )
            conn.execute(
                text(
                    "DELETE FROM ai_ops.rag_execution_record "
                    "WHERE request_id = ANY(:ids)"
                ),
                params,
            )


@pytest.fixture()
def llm_transport(monkeypatch: pytest.MonkeyPatch):
    """把**默认 Client 的 HTTP transport** 换成确定性 Mock（0 网络）。

    只替换 transport，**不替换** Client / Sink / Repository 装配 ——
    因此默认 Client 的 accounting 接线保持真实（被测对象）。
    """
    state: dict[str, Any] = {
        "requests": [],
        "responder": None,          # None → 200 正常响应
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        responder: Callable[[int], httpx.Response] | None = state["responder"]
        index = len(state["requests"])
        if responder is not None:
            return responder(index)
        return httpx.Response(
            200, json=_chat_body(f"{_PREFIX}llm-{index}")
        )

    real_class = client_module.OpenAICompatibleClient

    class _MockTransportClient(real_class):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            kwargs.setdefault("transport", httpx.MockTransport(_handler))
            super().__init__(**kwargs)

    monkeypatch.setattr(
        client_module, "OpenAICompatibleClient", _MockTransportClient
    )
    return state


@pytest.fixture()
def default_client(monkeypatch: pytest.MonkeyPatch, llm_transport):
    """**生产默认 Client**（真实 sink 接线）+ 环境隔离（测试结束彻底复位）。"""
    monkeypatch.setattr(
        client_module,
        "settings",
        dataclasses.replace(
            settings,
            llm=dataclasses.replace(
                settings.llm,
                api_key="step56-key",
                base_url="https://step56.fake/v1",
                model="step56-model",
                provider="step56-provider",
            ),
        ),
    )
    reset_default_llm_client()
    yield get_default_llm_client()
    reset_default_llm_client()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def rag_boundaries(monkeypatch: pytest.MonkeyPatch):
    """只替换 RAG 的检索 / Context 边界；**LLM 保持生产默认 Client**（懒加载）。"""
    rag = get_observed_rag_service()
    monkeypatch.setattr(rag, "_vector_search_service", _FakeVectorSearch())
    monkeypatch.setattr(rag, "_context_builder", _EchoContextBuilder())
    monkeypatch.setattr(rag, "_llm_client", None)   # → get_default_llm_client()
    return rag


def _usage_rows(assistant_request_id: str) -> list[Any]:
    factory = get_session_factory()
    assert factory is not None
    with factory() as session:
        return list(
            session.scalars(
                select(LLMUsageRecord)
                .where(LLMUsageRecord.assistant_request_id == assistant_request_id)
                .order_by(LLMUsageRecord.created_at.asc(), LLMUsageRecord.id.asc())
            ).all()
        )


def _total_usage_rows() -> int:
    with _engine().connect() as conn:
        return int(
            conn.execute(
                text("SELECT COUNT(*) FROM ai_ops.llm_usage_record")
            ).scalar_one()
        )


def _post(client: TestClient, question: str) -> str:
    response = client.post("/api/ai/chat", json={"question": question})
    assert response.status_code == 200, response.text
    request_id = response.json()["metadata"]["request_id"]
    _CREATED_REQUEST_IDS.append(request_id)
    return request_id


def _trace(client: TestClient, request_id: str) -> dict[str, Any]:
    response = client.get(f"{_ENDPOINT}/{request_id}")
    assert response.status_code == 200, response.text
    return response.json()


# ============================================================
# 1. 生产默认 Client 真实接线
# ============================================================

@requires_db
class TestDefaultClientWiring:
    def test_default_client_uses_database_sink_singleton(
        self, default_client: Any
    ) -> None:
        inner = (
            default_client._client
            if isinstance(default_client, DeepSeekProvider)
            else default_client
        )
        sink = get_default_accounting_sink()

        assert isinstance(sink, DatabaseLLMAccountingSink)
        assert inner._accounting_sink is sink
        assert get_default_llm_client() is default_client       # 进程级单例
        assert get_default_accounting_sink() is sink            # 不按 request 重建


# ============================================================
# 2. RAG E2E：/api/ai/chat → usage row → Assistant Trace
# ============================================================

@requires_db
class TestRagUsageE2E:
    def test_rag_request_persists_one_usage_row_readable_by_trace(
        self, client: TestClient, default_client: Any, rag_boundaries: Any,
        llm_transport: dict[str, Any],
    ) -> None:
        request_id = _post(client, _RAG_QUESTION)
        provider_id = f"{_PREFIX}llm-1"

        # ① 真实 provider 请求恰好 1 次（默认 Client）
        assert len(llm_transport["requests"]) == 1

        # ② 数据库：恰好 1 条 usage，且关联到 Assistant Trace ID
        rows = _usage_rows(request_id)
        assert len(rows) == 1
        row = rows[0]
        assert row.assistant_request_id == request_id        # Assistant Trace ID
        assert row.request_id == provider_id                 # Provider 请求 ID
        assert row.request_id != row.assistant_request_id
        assert row.provider == "step56-provider"
        assert row.model == "step56-model"
        assert row.total_tokens == 33

        # ③ Assistant Trace 读回（HTTP contract 未改变）
        payload = _trace(client, request_id)
        assert payload["assistant_request_id"] == request_id
        assert len(payload["llm_usage"]) == 1
        item = payload["llm_usage"][0]
        assert item["id"] == row.id
        assert item["assistant_request_id"] == request_id
        assert item["request_id"] == provider_id
        assert item["total_tokens"] == 33
        assert len(payload["rag_executions"]) == 1           # RAG 段未受影响
        assert payload["tool_executions"] == []

        # ④ 仍在同一 sink 单例上（请求期间不重建 sink）
        assert get_default_accounting_sink() is get_default_accounting_sink()

    def test_empty_retrieval_makes_no_llm_call_and_no_usage_row(
        self, client: TestClient, default_client: Any,
        monkeypatch: pytest.MonkeyPatch, llm_transport: dict[str, Any],
    ) -> None:
        rag = get_observed_rag_service()
        monkeypatch.setattr(rag, "_vector_search_service", _FakeVectorSearch([]))
        monkeypatch.setattr(rag, "_context_builder", _EchoContextBuilder())
        monkeypatch.setattr(rag, "_llm_client", None)

        request_id = _post(client, _RAG_QUESTION)

        assert llm_transport["requests"] == []               # 空检索不调 LLM
        assert _usage_rows(request_id) == []                 # 因此无 usage 行
        payload = _trace(client, request_id)
        assert payload["llm_usage"] == []
        assert len(payload["rag_executions"]) == 1           # 空检索仍有 RAG 记录


# ============================================================
# 3. Tool E2E：Tool 路径不产生 LLM usage
# ============================================================

@requires_db
class TestToolPathUsage:
    def test_tool_request_writes_no_usage_row(
        self, client: TestClient, default_client: Any, monkeypatch: pytest.MonkeyPatch,
        llm_transport: dict[str, Any],
    ) -> None:
        monkeypatch.setitem(
            orch_module._TOOL_REGISTRY._handlers, _TOOL_NAME, _FakeInventoryHandler()
        )  # noqa: SLF001 —— 只替换 Tool 的 DB 边界

        request_id = _post(client, _TOOL_QUESTION)

        assert llm_transport["requests"] == []               # Tool 路径无 LLM 调用
        assert _usage_rows(request_id) == []
        payload = _trace(client, request_id)
        assert payload["llm_usage"] == []
        assert len(payload["tool_executions"]) == 1          # Tool 段（Step 41 行为）


# ============================================================
# 4. LLM 失败路径：不产生 usage 行（HTTP 语义不变）
# ============================================================

@requires_db
class TestFailurePath:
    def test_llm_http_error_writes_no_usage_row(
        self, client: TestClient, default_client: Any, rag_boundaries: Any,
        llm_transport: dict[str, Any],
    ) -> None:
        llm_transport["responder"] = lambda _index: httpx.Response(
            500, json={"error": {"message": "step56-upstream"}}
        )

        response = client.post("/api/ai/chat", json={"question": _RAG_QUESTION})

        assert response.status_code == 500                     # 既有错误语义
        assert len(llm_transport["requests"]) == 1             # 无 retry
        assert _total_usage_rows_for_prefix() == 0

    def test_llm_timeout_writes_no_usage_row(
        self, client: TestClient, default_client: Any, rag_boundaries: Any,
        llm_transport: dict[str, Any],
    ) -> None:
        def _timeout(_index: int) -> httpx.Response:
            raise httpx.ConnectTimeout("step56-timeout")

        llm_transport["responder"] = _timeout

        response = client.post("/api/ai/chat", json={"question": _RAG_QUESTION})

        assert response.status_code == 500
        assert len(llm_transport["requests"]) == 1
        assert _total_usage_rows_for_prefix() == 0


def _total_usage_rows_for_prefix() -> int:
    with _engine().connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.llm_usage_record "
                    "WHERE request_id LIKE :prefix OR provider = :provider"
                ),
                {"prefix": f"{_PREFIX}%", "provider": "step56-provider"},
            ).scalar_one()
        )


# ============================================================
# 5. Accounting 失败隔离（HTTP 仍成功 + 不 retry）
# ============================================================

@requires_db
class TestAccountingFailureIsolationE2E:
    def test_db_failure_keeps_http_success_and_no_retry(
        self, client: TestClient, default_client: Any, rag_boundaries: Any,
        llm_transport: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from backend.app.db.llm_usage_repository import (
            LLMUsageRepository,
            LLMUsageRepositoryError,
        )

        def _boom(self: Any, **kwargs: Any) -> int | None:
            raise LLMUsageRepositoryError("step56-forced-db-failure")

        monkeypatch.setattr(LLMUsageRepository, "create", _boom)

        request_id = _post(client, _RAG_QUESTION)

        assert len(llm_transport["requests"]) == 1             # 不因 accounting 失败 retry
        assert _usage_rows(request_id) == []                   # 未写入（DB 失败）
        payload = _trace(client, request_id)
        assert payload["llm_usage"] == []
        assert payload["rag_executions"][0]["request_id"] == request_id


# ============================================================
# 6. Text-to-SQL：每次真实 LLM 调用 = 1 条 usage（含 semantic retry / refusal）
# ============================================================

_GOOD_SQL = "SELECT id, title FROM public.knowledge_document LIMIT 10"
_ALLOWED = ("public.knowledge_document", "public.knowledge_chunk")
_CONTEXT = (
    "Project: Step56\nData Source: primary\nDatabase Type: postgresql\n"
    "\n## Database Schema\n"
    "public.knowledge_document(id bigint PK, title varchar, file_name varchar)\n"
    "public.knowledge_chunk(id bigint PK, document_id bigint, content text)"
)


def _schema_table(name: str, columns: tuple[str, ...]) -> SchemaTable:
    return SchemaTable(
        schema_name="public",
        name=name,
        description=None,
        columns=tuple(
            SchemaColumn(
                name=column,
                data_type="bigint",
                nullable=False,
                default=None,
                ordinal_position=index + 1,
                is_primary_key=(column == "id"),
                description=None,
            )
            for index, column in enumerate(columns)
        ),
        foreign_keys=(),
    )


def _schema() -> DatabaseSchema:
    return DatabaseSchema(
        schema_name="public",
        tables=(
            _schema_table("knowledge_document", ("id", "title", "file_name")),
            _schema_table("knowledge_chunk", ("id", "document_id", "content")),
        ),
    )


@requires_db
class TestTextToSqlUsage:
    def _generate(
        self, default_client: Any, scope_id: str, responder: Callable[[int], httpx.Response]
    ) -> tuple[Any, str]:
        from backend.app.db.session import get_session_factory as _factory

        assert _factory() is not None
        service = TextToSQLService(llm_client=default_client)   # 生产默认 Client
        with assistant_trace_scope(scope_id):
            result = _run(
                lambda: service.generate(
                    "知识文档有哪些",
                    database_context=_CONTEXT,
                    allowed_tables=_ALLOWED,
                    schema=_schema(),
                    max_rows=10,
                )
            )
        _CREATED_REQUEST_IDS.append(scope_id)
        return result, scope_id

    def test_semantic_retry_writes_one_row_per_actual_llm_call(
        self, default_client: Any, llm_transport: dict[str, Any]
    ) -> None:
        scope_id = f"{_PREFIX}assistant-t2sql-{uuid.uuid4().hex[:8]}"

        def responder(index: int) -> httpx.Response:
            # 第 1 次：非法 SQL → Validator 拒绝 → 预算内重试
            # 第 2 次：合法 SQL → 通过
            sql = "DROP TABLE x" if index == 1 else _GOOD_SQL
            return httpx.Response(
                200, json=_chat_body(f"{_PREFIX}llm-t2sql-{index}", sql)
            )

        llm_transport["responder"] = responder
        result, scope_id = self._generate(default_client, scope_id, responder)

        assert result.validated is True
        assert len(llm_transport["requests"]) == 2          # 2 次真实 LLM 请求
        rows = _usage_rows(scope_id)
        assert len(rows) == 2                               # → 恰好 2 条 usage
        assert {row.request_id for row in rows} == {
            f"{_PREFIX}llm-t2sql-1",
            f"{_PREFIX}llm-t2sql-2",
        }
        assert {row.assistant_request_id for row in rows} == {scope_id}

    def test_refusal_writes_exactly_one_row(
        self, default_client: Any, llm_transport: dict[str, Any]
    ) -> None:
        scope_id = f"{_PREFIX}assistant-refusal-{uuid.uuid4().hex[:8]}"
        llm_transport["responder"] = lambda index: httpx.Response(
            200, json=_chat_body(f"{_PREFIX}llm-refusal-{index}", REFUSAL_MARKER)
        )

        result, scope_id = self._generate(
            default_client, scope_id, llm_transport["responder"]
        )

        assert result.status == RESULT_STATUS_REFUSAL
        assert result.attempts == 1                         # refusal 不消耗重试预算
        assert result.validated is False
        assert result.sql is None                           # 未进入 Validator / Executor
        assert len(llm_transport["requests"]) == 1
        rows = _usage_rows(scope_id)
        assert len(rows) == 1                               # 1 次真实调用 → 1 条
        assert rows[0].request_id == f"{_PREFIX}llm-refusal-1"


def _run(coro_factory: Callable[[], Any]) -> Any:
    import asyncio

    return asyncio.run(coro_factory())


__all__ = [
    "TestDefaultClientWiring",
    "TestRagUsageE2E",
    "TestToolPathUsage",
    "TestFailurePath",
    "TestAccountingFailureIsolationE2E",
    "TestTextToSqlUsage",
]
