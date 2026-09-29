"""Assistant Trace 多路径关联回归审计（Phase 3.12 Step 60）——RAG / TOOL / TEXT_TO_SQL。

入口统一：**POST /api/ai/chat**（真实 FastAPI app + 真实 Orchestrator + 真实 Router +
真实 Trace API）；只替换外部边界（LLM transport / 检索 / Tool handler / SQL 执行）。

两类测试（互不混杂）：
    * 离线（默认套件；RUN_DB_TESTS=0）：复用 Step 40 的既有离线装配
      （`tests/test_assistant_trace_correlation_e2e.py` 的 `e2e` fixture + 内存
      Usage/Tool Trace 替身）→ 验证路由 / request_id 关联 / 跨请求隔离 / payload 安全
    * DB-gated（RUN_DB_TESTS=1）：真实生产装配 + 真实 PostgreSQL 持久化
      → 验证 Assistant Trace 三段（llm_usage / tool_executions / rag_executions）关联
      与 **A / B / C 三条请求互不污染**

关键断言（两条路径都覆盖）：
    1. RAG    : route=rag · llm_usage ≥1 · rag_executions=1 · tool_executions=0
    2. TOOL   : route=tool · tool_executions=1（request_id == assistant_request_id）
    3. T2SQL  : route=text_to_sql · llm_usage == 真实重试次数（2）· tool=0 · rag=0
    4. 隔离   : Trace(X) 只含 X 的 records（严格集合相等）
    5. ID     : assistant_request_id ≠ provider request_id（互不覆盖）
    6. Empty  : 未知 id → 200 + 三段皆 []
    7. Failure: 下游失败 → HTTP error；失败请求不把其他请求的 record 挂到自己名下
    8. 安全   : trace payload 不含 SQL / chunk 正文 / 提问原文 / 凭据

真实 LLM：**OFF**（全部 MockTransport；不依赖 RUN_REAL_LLM_TEST）。
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import orchestrator_chat as root
from backend.app.db.session import get_engine, get_session_factory
from backend.app.llm.client import OpenAICompatibleClient
from backend.app.main import app
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
    LLMUsagePersistenceService,
)
from backend.app.services.rag_observability_runtime import get_observed_rag_service
from backend.app.services.relevant_table_selector import (
    TableSelection,
    TableSelectionResult,
)
from backend.app.services.schema_explorer_service import (
    DatabaseSchema,
    SchemaColumn,
    SchemaTable,
)
from backend.app.services.sql_executor_service import SQLExecutionResult
from backend.app.services.text_to_sql_service import TextToSQLService

# 复用 Step 40 既有离线装配（不重造 Fake；`e2e` 为可导入的 fixture）
from tests.test_assistant_trace_correlation_e2e import (  # noqa: PLC2701
    _RAG_QUESTION,
    _TOOL_QUESTION,
    _TRACE_ENDPOINT,
    _response_body,
    e2e,
)

_TRACE = _TRACE_ENDPOINT
_T2SQL_QUESTION = "统计最近7天的入库单数量"
_PREFIX = "step60-"
_SQL_SENTINEL = "STEP60-SQL-SENTINEL"
_CHUNK_SENTINEL = "STEP60-CHUNK-CONTENT"
_GOOD_SQL = (
    "SELECT id, title FROM public.knowledge_document LIMIT 10 "
    f"-- {_SQL_SENTINEL}"
)
_ALLOWED_TABLES = ("public.knowledge_document",)
_CONTEXT_TEXT = (
    "Project: Step60\nData Source: primary\nDatabase Type: postgresql\n"
    "\n## Database Schema\n"
    "public.knowledge_document(id bigint PK, title varchar, file_name varchar)"
)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _ask(client: TestClient, question: str) -> dict[str, Any]:
    response = client.post("/api/ai/chat", json={"question": question})
    assert response.status_code == 200, response.text
    return response.json()


def _trace(client: TestClient, request_id: str) -> dict[str, Any]:
    response = client.get(f"{_TRACE}/{request_id}")
    assert response.status_code == 200, response.text
    return response.json()


# ============================================================
# 外部边界 Fake（LLM transport / 检索 / SQL 执行）
# ============================================================

def _sink_for(repository: Any) -> DatabaseLLMAccountingSink:
    """真实 DatabaseLLMAccountingSink（离线 → 内存仓储；DB → 真实仓储）。"""
    return DatabaseLLMAccountingSink(
        persistence_service=LLMUsagePersistenceService(repository=repository)
    )


def _retry_t2sql_client(
    *,
    repository: Any = None,
    provider_ids: tuple[str, str] | None = None,
    use_db_sink: bool = False,
) -> OpenAICompatibleClient:
    """Text-to-SQL 专用 Client：第 1 次非法 SQL → 第 2 次合法 SQL（真实重试）。"""
    ids = provider_ids or (f"{_PREFIX}t2sql-1", f"{_PREFIX}t2sql-2")
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        index = min(state["n"], 1)
        state["n"] += 1
        sql = "DROP TABLE x" if index == 0 else _GOOD_SQL
        return httpx.Response(200, json=_response_body(ids[index], sql))

    if use_db_sink:
        sink: Any = DatabaseLLMAccountingSink()
    elif repository is not None:
        sink = _sink_for(repository)
    else:
        sink = None
    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://step60.fake/v1",
        model="step60-model",
        provider="step60-provider",
        transport=httpx.MockTransport(handler),
        accounting_sink=sink,
    )


def _failing_client(*, repository: Any = None) -> OpenAICompatibleClient:
    """LLM 上游 500（用于 Failure Trace 审计）。"""
    sink = _sink_for(repository) if repository is not None else None
    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://step60.fake/v1",
        model="step60-model",
        provider="step60-provider",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(500, json={"error": {"message": "boom"}})
        ),
        accounting_sink=sink,
    )


class _FakeVectorSearch:
    async def search(self, query: str, *, top_k: int):  # noqa: ANN201
        from backend.app.services.vector_search_service import VectorSearchResult

        return [
            VectorSearchResult(
                chunk_id=301,
                document_id=40,
                chunk_index=0,
                content=_CHUNK_SENTINEL,
                distance=0.1,
                similarity=0.9,
                metadata={"heading": "step60"},
            )
        ]


class _EchoContextBuilder:
    def build(self, results: Any):  # noqa: ANN401
        from backend.app.services.context_builder import ContextBuildResult

        used = tuple(results)
        return ContextBuildResult(
            text="\n".join(r.content for r in used),
            used_chunks=used,
            total_chars=32,
            truncated=False,
            dropped_count=0,
        )


class _RecordingHandler:
    """Tool Handler 替身（**不调用 LLM**；只读）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, arguments: Any):  # noqa: ANN401
        self.calls += 1
        return {"material_code": arguments.get("material_code"), "qty": 7}


class _OkSqlExecutor:
    """SQL 执行器替身：返回真实 SQLExecutionResult（不触达数据库）。"""

    def __init__(self) -> None:
        self.sqls: list[str] = []

    async def execute(self, sql: str, **kwargs: Any):  # noqa: ANN003
        self.sqls.append(sql)
        return SQLExecutionResult(
            columns=("id", "title"),
            rows=((1, "a"), (2, "b"), (3, "c")),
            row_count=3,
            truncated=False,
            execution_time_ms=1.5,
        )


class _T2SqlSelector:
    def select(self, question: str, *, schema: Any, semantic: Any, top_k: int = 5):  # noqa: ANN401
        return TableSelectionResult(
            question=question,
            selections=(
                TableSelection(
                    table="public.knowledge_document",
                    score=1.0,
                    matched_terms=("knowledge",),
                ),
            ),
        )


class _T2SqlComposer:
    def compose(self, **kwargs: Any) -> str:  # noqa: ANN003
        return _CONTEXT_TEXT


class _T2SqlProjectProvider:
    def resolve(self):  # noqa: ANN201
        from backend.app.projects.context import DataSource, ProjectContext
        from backend.app.projects.semantic import ProjectSemantic

        schema = DatabaseSchema(
            schema_name="public",
            tables=(
                SchemaTable(
                    schema_name="public",
                    name="knowledge_document",
                    description=None,
                    columns=tuple(
                        SchemaColumn(
                            name=name,
                            data_type="bigint" if name == "id" else "varchar",
                            nullable=False,
                            default=None,
                            ordinal_position=index + 1,
                            is_primary_key=(name == "id"),
                            description=None,
                        )
                        for index, name in enumerate(("id", "title"))
                    ),
                    foreign_keys=(),
                ),
            ),
        )
        project = ProjectContext(
            project_id="project-step60",
            project_name="Step60",
            description=None,
            data_source=DataSource(name="primary", type="postgresql"),
        )
        return project, schema, ProjectSemantic()


def _install_t2sql_route(monkeypatch: pytest.MonkeyPatch, client: Any) -> Any:
    """把生产默认 Orchestrator 的 TEXT_TO_SQL 依赖换成真实服务 + 测试边界。"""
    orchestrator = root._default_orchestrator
    executor = _OkSqlExecutor()
    monkeypatch.setattr(
        orchestrator, "_text_to_sql", TextToSQLService(llm_client=client)
    )
    monkeypatch.setattr(orchestrator, "_sql_executor", executor)
    monkeypatch.setattr(orchestrator, "_table_selector", _T2SqlSelector())
    monkeypatch.setattr(orchestrator, "_context_composer", _T2SqlComposer())
    monkeypatch.setattr(orchestrator, "_project_provider", _T2SqlProjectProvider())
    return executor


def _assert_payload_clean(payload_text: str) -> None:
    for forbidden in (
        _SQL_SENTINEL,          # 生成的 SQL（含哨兵）不入 Trace
        _CHUNK_SENTINEL,        # chunk 正文不入 Trace
        "test-key",             # 凭据
        "step60.fake",          # base_url
        "step40.fake",
        "postgresql://",
    ):
        assert forbidden not in payload_text, f"Trace 泄露: {forbidden}"


# ============================================================
# 离线：TEXT_TO_SQL 路由（既有离线 fixture 复用）
# ============================================================

class TestTextToSqlRouteOffline:
    def test_route_retry_correlation_and_isolation(self, e2e, monkeypatch) -> None:
        repository = e2e()[0]
        client = _retry_t2sql_client(repository=repository)
        executor = _install_t2sql_route(monkeypatch, client)

        with TestClient(app) as http:
            payload = _ask(http, _T2SQL_QUESTION)
            assert payload["route"] == "text_to_sql"
            request_id = payload["metadata"]["request_id"]
            assert request_id
            assert payload["metadata"]["row_count"] == 3

            rows = [
                row
                for row in repository.rows
                if row.get("assistant_request_id") == request_id
            ]
            assert len(rows) == 2                      # 真实重试 → 2 次 LLM 调用
            assert [row["request_id"] for row in rows] == [
                f"{_PREFIX}t2sql-1",
                f"{_PREFIX}t2sql-2",
            ]
            assert all(
                row["assistant_request_id"] == request_id for row in rows
            )
            assert all(row["request_id"] != request_id for row in rows)  # 两种 ID 不同

            trace = _trace(http, request_id)
            assert trace["assistant_request_id"] == request_id
            assert len(trace["llm_usage"]) == 2
            assert {item["assistant_request_id"] for item in trace["llm_usage"]} == {
                request_id
            }
            assert trace["tool_executions"] == []
            assert trace["rag_executions"] == []
            assert len(executor.sqls) == 1             # 只有第 2 次进入执行器
            _assert_payload_clean(http.get(f"{_TRACE}/{request_id}").text)

        # 其他 request 不受影响（无跨请求污染）
        assert not [
            row
            for row in repository.rows
            if row.get("assistant_request_id") not in {request_id}
        ]


class TestThreePathsOffline:
    def test_three_paths_are_isolated(self, e2e, monkeypatch) -> None:
        repository = e2e()[0]
        client = _retry_t2sql_client(repository=repository)
        _install_t2sql_route(monkeypatch, client)

        with TestClient(app) as http:
            rag = _ask(http, _RAG_QUESTION)
            tool = _ask(http, _TOOL_QUESTION)
            t2sql = _ask(http, _T2SQL_QUESTION)

            assert rag["route"] == "rag"
            assert tool["route"] == "tool"
            assert t2sql["route"] == "text_to_sql"
            id_a = rag["metadata"]["request_id"]
            id_b = tool["metadata"]["request_id"]
            id_c = t2sql["metadata"]["request_id"]
            assert len({id_a, id_b, id_c}) == 3

            # LLM usage：A 一行（RAG 内 1 次调用）· B 零行 · C 两行（真实重试）
            usage = {}
            for row in repository.rows:
                usage.setdefault(row["assistant_request_id"], []).append(row)
            assert len(usage[id_a]) == 1
            assert id_b not in usage
            assert len(usage[id_c]) == 2

            # Trace 隔离：每段只归属自己的 request_id
            trace_a = _trace(http, id_a)
            trace_b = _trace(http, id_b)
            trace_c = _trace(http, id_c)
            assert {item["assistant_request_id"] for item in trace_a["llm_usage"]} == {
                id_a
            }
            assert trace_b["llm_usage"] == []
            assert {
                item["request_id"] for item in trace_b["tool_executions"]
            } == {id_b}
            assert {
                item["assistant_request_id"] for item in trace_c["llm_usage"]
            } == {id_c}
            assert trace_a["tool_executions"] == []
            assert trace_c["tool_executions"] == []
            assert trace_c["rag_executions"] == []
            for payload in (trace_a, trace_b, trace_c):
                assert payload["assistant_request_id"] in {id_a, id_b, id_c}


# ============================================================
# DB-gated：真实生产装配 + 真实 PostgreSQL
# ============================================================

class _DbState:
    def __init__(self) -> None:
        self.request_ids: list[str] = []
        self.rag_watermark = 0
        self.tool_watermark = 0
        self.usage_baseline = 0


def _own_usage_rows(conn: Any) -> int:  # noqa: ANN401
    return int(
        conn.execute(
            text(
                "SELECT COUNT(*) FROM ai_ops.llm_usage_record "
                "WHERE request_id LIKE :prefix OR provider = :provider"
            ),
            {"prefix": f"{_PREFIX}%", "provider": "step60-provider"},
        ).scalar_one()
    )


@pytest.fixture()
def multi_path_db(monkeypatch: pytest.MonkeyPatch):
    """真实装配（app / orchestrator / Trace API）+ 测试边界 + 定向清理。"""
    engine = get_engine()
    if engine is None or get_session_factory() is None:
        pytest.skip("DATABASE_URL 未配置")
    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()

    state = _DbState()
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM ai_ops.llm_usage_record "
                "WHERE request_id LIKE :prefix OR provider = :provider"
            ),
            {"prefix": f"{_PREFIX}%", "provider": "step60-provider"},
        )
        state.usage_baseline = int(
            conn.execute(
                text("SELECT COUNT(*) FROM ai_ops.llm_usage_record")
            ).scalar_one()
        )
        state.rag_watermark = int(
            conn.execute(
                text("SELECT COALESCE(MAX(id), 0) FROM ai_ops.rag_execution_record")
            ).scalar_one()
        )
        state.tool_watermark = int(
            conn.execute(
                text("SELECT COALESCE(MAX(id), 0) FROM ai_ops.tool_execution_record")
            ).scalar_one()
        )

    # ① RAG：真实 observed RagService + 假检索 + 真实 DB sink 的 LLM Client
    rag = get_observed_rag_service()
    rag_client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://step60.fake/v1",
        model="step60-model",
        provider="step60-provider",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=_response_body(f"{_PREFIX}llm-rag-1")
            )
        ),
        accounting_sink=DatabaseLLMAccountingSink(),
    )
    monkeypatch.setattr(rag, "_vector_search_service", _FakeVectorSearch())
    monkeypatch.setattr(rag, "_context_builder", _EchoContextBuilder())
    monkeypatch.setattr(rag, "_llm_client", rag_client)

    # ② TOOL：真实 Tool 执行边界 + 不调用 LLM 的 handler
    handler = _RecordingHandler()
    monkeypatch.setitem(root._TOOL_REGISTRY._handlers, "get_inventory", handler)

    # ③ TEXT_TO_SQL：真实服务 + 真实 DB sink（2 次尝试 → 2 行 usage）
    t2sql_client = _retry_t2sql_client(
        provider_ids=(f"{_PREFIX}llm-t2sql-1", f"{_PREFIX}llm-t2sql-2"),
        use_db_sink=True,
    )
    executor = _install_t2sql_route(monkeypatch, t2sql_client)

    ctx = {"state": state, "executor": executor, "handler": handler}

    def _ask_and_track(http: TestClient, question: str) -> tuple[str, dict[str, Any]]:
        payload = _ask(http, question)
        request_id = payload["metadata"]["request_id"]
        state.request_ids.append(request_id)
        return request_id, payload

    ctx["ask"] = _ask_and_track
    yield ctx

    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM ai_ops.llm_usage_record "
                "WHERE request_id LIKE :prefix OR provider = :provider "
                "OR assistant_request_id = ANY(:ids)"
            ),
            {
                "prefix": f"{_PREFIX}%",
                "provider": "step60-provider",
                "ids": state.request_ids or [""],
            },
        )
        conn.execute(
            text(
                "DELETE FROM ai_ops.tool_execution_record WHERE id > :watermark"
            ),
            {"watermark": state.tool_watermark},
        )
        conn.execute(
            text(
                "DELETE FROM ai_ops.rag_execution_record WHERE id > :watermark"
            ),
            {"watermark": state.rag_watermark},
        )
        assert _own_usage_rows(conn) == 0, "Step 60 usage 残留"
        assert int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.llm_usage_record WHERE id IS NOT NULL"
                )
            ).scalar_one()
        ) == state.usage_baseline, "usage 行数未回到基线"
        assert int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.tool_execution_record "
                    "WHERE id > :watermark"
                ),
                {"watermark": state.tool_watermark},
            ).scalar_one()
        ) == 0, "Tool 表残留"
        assert int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.rag_execution_record "
                    "WHERE id > :watermark"
                ),
                {"watermark": state.rag_watermark},
            ).scalar_one()
        ) == 0, "RAG 表残留"


@requires_db
class TestMultiPathDb:
    def test_path_a_rag_correlation(self, multi_path_db) -> None:
        with TestClient(app) as http:
            request_id, payload = multi_path_db["ask"](http, _RAG_QUESTION)
            assert payload["route"] == "rag"

            trace = _trace(http, request_id)
            assert trace["assistant_request_id"] == request_id
            assert len(trace["llm_usage"]) == 1
            assert trace["llm_usage"][0]["request_id"] == f"{_PREFIX}llm-rag-1"
            assert trace["llm_usage"][0]["assistant_request_id"] == request_id
            assert len(trace["rag_executions"]) == 1
            assert trace["rag_executions"][0]["request_id"] == request_id
            assert trace["tool_executions"] == []

    def test_path_b_tool_correlation(self, multi_path_db) -> None:
        with TestClient(app) as http:
            request_id, payload = multi_path_db["ask"](http, _TOOL_QUESTION)
            assert payload["route"] == "tool"

            trace = _trace(http, request_id)
            assert trace["assistant_request_id"] == request_id
            assert len(trace["tool_executions"]) == 1
            assert trace["tool_executions"][0]["request_id"] == request_id
            assert trace["llm_usage"] == []          # Tool 路径不调用 LLM
            assert trace["rag_executions"] == []
            assert multi_path_db["handler"].calls == 1

    def test_path_c_text_to_sql_correlation(self, multi_path_db) -> None:
        with TestClient(app) as http:
            request_id, payload = multi_path_db["ask"](http, _T2SQL_QUESTION)
            assert payload["route"] == "text_to_sql"

            trace = _trace(http, request_id)
            assert trace["assistant_request_id"] == request_id
            assert len(trace["llm_usage"]) == 2       # 真实重试 = 2 次实际 LLM 调用
            assert {item["request_id"] for item in trace["llm_usage"]} == {
                f"{_PREFIX}llm-t2sql-1",
                f"{_PREFIX}llm-t2sql-2",
            }
            assert {
                item["assistant_request_id"] for item in trace["llm_usage"]
            } == {request_id}
            assert trace["tool_executions"] == []
            assert trace["rag_executions"] == []
            assert multi_path_db["executor"].sqls == [_GOOD_SQL]

    def test_three_paths_cross_request_isolation_db(self, multi_path_db) -> None:
        with TestClient(app) as http:
            id_a, _ = multi_path_db["ask"](http, _RAG_QUESTION)
            id_b, _ = multi_path_db["ask"](http, _TOOL_QUESTION)
            id_c, _ = multi_path_db["ask"](http, _T2SQL_QUESTION)
            assert len({id_a, id_b, id_c}) == 3

            trace_a = _trace(http, id_a)
            trace_b = _trace(http, id_b)
            trace_c = _trace(http, id_c)

            # 严格集合：每段只归属自己的 request_id
            assert {i["assistant_request_id"] for i in trace_a["llm_usage"]} == {id_a}
            assert {i["request_id"] for i in trace_a["rag_executions"]} == {id_a}
            assert trace_a["tool_executions"] == []

            assert {i["request_id"] for i in trace_b["tool_executions"]} == {id_b}
            assert trace_b["llm_usage"] == []
            assert trace_b["rag_executions"] == []

            assert {i["assistant_request_id"] for i in trace_c["llm_usage"]} == {id_c}
            assert trace_c["tool_executions"] == []
            assert trace_c["rag_executions"] == []

        # DB 侧：所有本模块 usage 行的 assistant_request_id ∈ {A, B, C}
        with get_engine().connect() as conn:
            owners = set(
                conn.execute(
                    text(
                        "SELECT DISTINCT assistant_request_id "
                        "FROM ai_ops.llm_usage_record "
                        "WHERE provider = 'step60-provider'"
                    )
                ).scalars()
            )
        assert owners <= {id_a, id_b, id_c}
        assert id_b not in owners                     # Tool 路径不产生 usage

    def test_empty_trace_from_real_wiring(self, multi_path_db) -> None:
        with TestClient(app) as http:
            unknown = f"{_PREFIX}not-exist-{uuid.uuid4().hex[:8]}"
            response = http.get(f"{_TRACE}/{unknown}")

            assert response.status_code == 200
            payload = response.json()
            assert payload["assistant_request_id"] == unknown
            assert payload["llm_usage"] == []
            assert payload["tool_executions"] == []
            assert payload["rag_executions"] == []

    def test_failed_rag_request_does_not_leak_other_records(
        self, multi_path_db, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with TestClient(app) as http:
            ok_id, _ = multi_path_db["ask"](http, _RAG_QUESTION)   # 先成功一次
            # 再让 LLM 上游失败（只影响后续请求）
            monkeypatch.setattr(
                get_observed_rag_service(), "_llm_client", _failing_client()
            )
            failed = http.post("/api/ai/chat", json={"question": _RAG_QUESTION})
            assert failed.status_code == 500                       # 失败语义不变

            # 失败请求的 request_id：从 RAG 记录（异常路径仍记录）取回
            with get_engine().connect() as conn:
                failed_id = conn.execute(
                    text(
                        "SELECT request_id FROM ai_ops.rag_execution_record "
                        "WHERE id > :watermark ORDER BY id"
                    ),
                    {"watermark": multi_path_db["state"].rag_watermark},
                ).scalars().all()
            assert len(failed_id) == 2                             # 成功 + 失败各 1 条
            failed_request_id = failed_id[1]

            trace_failed = _trace(http, failed_request_id)
            assert trace_failed["llm_usage"] == []                 # usage=None → 不写
            assert len(trace_failed["rag_executions"]) == 1
            assert trace_failed["tool_executions"] == []

            trace_ok = _trace(http, ok_id)
            assert {i["assistant_request_id"] for i in trace_ok["llm_usage"]} == {
                ok_id
            }
            assert {i["request_id"] for i in trace_ok["rag_executions"]} == {ok_id}

    def test_usage_rows_expose_no_sensitive_content(self, multi_path_db) -> None:
        with TestClient(app) as http:
            request_id, _ = multi_path_db["ask"](http, _T2SQL_QUESTION)

            with get_engine().connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT id, request_id, provider, model, prompt_tokens, "
                        "completion_tokens, total_tokens, created_at, "
                        "assistant_request_id FROM ai_ops.llm_usage_record "
                        "WHERE assistant_request_id = :request_id"
                    ),
                    {"request_id": request_id},
                ).all()

            assert len(row) == 2
            for item in row:
                assert item[1] != request_id                       # provider ≠ assistant
                assert item[8] == request_id
                assert item[2] == "step60-provider"
            for forbidden in (_SQL_SENTINEL, _CHUNK_SENTINEL, "test-key"):
                assert forbidden not in str(row), f"usage 行泄露: {forbidden}"

            response = http.get(f"{_TRACE}/{request_id}")
            _assert_payload_clean(response.text)


__all__ = [
    "TestTextToSqlRouteOffline",
    "TestThreePathsOffline",
    "TestMultiPathDb",
]
