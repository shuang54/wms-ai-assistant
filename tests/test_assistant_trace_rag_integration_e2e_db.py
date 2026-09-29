"""Assistant Trace × RAG 生产装配 E2E（DB）（Phase 3.12 Step 48）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

真实链路（**不**手工替换 Trace 组合层）：

    POST /api/ai/chat（真实 app / Orchestrator / Router / RagService /
            Composite Observer / Persistence Adapter / Repository / PostgreSQL）
        ↓ 产生 rag_execution_record（request_id = A）
    GET /api/observability/assistant-trace/{A}
        ↓ 真实 Trace API + AssistantTraceQueryService
          （LLM / Tool / RAG 三个**持久化**读边界）
        ↓ 200 AssistantTraceResponse{ …, rag_executions }

只 Fake 外部边界：Vector Search（embedding + DB）/ ContextBuilder /
LLM transport（Case 2 使用真实 Client + MockTransport + 真实 Accounting Sink）/
Tool handler（Case 3）。

Case：1 RAG only · 2 LLM + RAG · 3 Tool only · 4 empty ·
      5 A/B isolation · 6 RAG repository failure → 502

残留：定向 DELETE（按本模块产生的 request_id / provider request_id 前缀）；
**不使用 TRUNCATE**；teardown 断言 residue = 0。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import orchestrator_chat as orch_module
from backend.app.db.rag_execution_repository import (
    RagExecutionRepositoryError,
)
from backend.app.db.session import get_engine
from backend.app.llm.client import OpenAICompatibleClient
from backend.app.main import app
from backend.app.services.context_builder import ContextBuildResult
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
)
from backend.app.services.rag_observability_runtime import (
    get_observed_rag_service,
    get_rag_execution_collector,
    get_rag_execution_persistent_query_service,
)
from backend.app.services.rag_service import RagService
from backend.app.services.vector_search_service import VectorSearchResult

_ENDPOINT = "/api/observability/assistant-trace"
_RAG_QUESTION = "采购入库的操作步骤是什么"
_TOOL_QUESTION = "查询物料 MAT-001 当前库存"
_PROVIDER_PREFIX = "step48-llm-"
_STARTED = datetime(2026, 9, 29, 13, 0, 0, tzinfo=timezone.utc)

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


def _chunk(chunk_id: int, *, document_id: int = 10) -> VectorSearchResult:
    return VectorSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=0,
        content="STEP48-E2E-CHUNK",
        distance=0.1,
        similarity=0.9,
        metadata={"heading": "采购入库"},
    )


class _FakeVectorSearch:
    async def search(self, query: str, *, top_k: int) -> list[VectorSearchResult]:
        return [_chunk(101), _chunk(102, document_id=20)]


class _FakeLLM:
    """纯文本应答（不产生 LLM usage）。"""

    async def chat(self, messages: list[dict[str, str]]) -> str:  # noqa: ARG002
        return "[step48] 采购入库包括收货、质检与上架。"


class _EchoContextBuilder:
    def build(self, results):  # noqa: ANN001, ANN201
        used = tuple(results)
        return ContextBuildResult(
            text="\n".join(r.content for r in used),
            used_chunks=used,
            total_chars=64,
            truncated=False,
            dropped_count=0,
        )


class _FakeInventoryHandler:
    async def __call__(self, arguments):  # noqa: ANN001, ANN204
        return {"material_code": arguments.get("material_code"), "qty": 120}


def _response_body(request_id: str) -> dict[str, Any]:
    """OpenAI Chat Completions 兼容响应（provider request_id = P）。"""
    return {
        "id": request_id,
        "model": "step48-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "RAG 答案"},
            }
        ],
        "usage": {
            "prompt_tokens": 5,
            "completion_tokens": 6,
            "total_tokens": 11,
        },
    }


def _llm_client_with_accounting(provider_request_id: str) -> Any:
    """真实 Client + MockTransport（0 网络）+ 真实 Accounting Sink。"""
    return OpenAICompatibleClient(
        api_key="step48-key",
        base_url="https://step48.fake/v1",
        model="step48-model",
        provider="step48-provider",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=_response_body(provider_request_id)
            )
        ),
        accounting_sink=DatabaseLLMAccountingSink(),
    )


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
    if not _env_flag("RUN_DB_TESTS"):
        yield
        return
    _CREATED_REQUEST_IDS.clear()
    yield
    requests = list(_CREATED_REQUEST_IDS)
    with _engine().begin() as conn:
        conn.execute(
            text(
                "DELETE FROM ai_ops.rag_execution_record "
                "WHERE request_id = ANY(:ids)"
            ),
            {"ids": requests},
        )
        conn.execute(
            text(
                "DELETE FROM ai_ops.llm_usage_record "
                "WHERE assistant_request_id = ANY(:ids) "
                "OR request_id LIKE :prefix"
            ),
            {"ids": requests, "prefix": f"{_PROVIDER_PREFIX}%"},
        )
        conn.execute(
            text(
                "DELETE FROM ai_ops.tool_execution_record "
                "WHERE request_id = ANY(:ids)"
            ),
            {"ids": requests},
        )
    with _engine().connect() as conn:
        assert int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.rag_execution_record "
                    "WHERE request_id = ANY(:ids)"
                ),
                {"ids": requests},
            ).scalar_one()
        ) == 0, "RAG 表残留（Step 48 测试行未清理）"


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def rag_boundaries(monkeypatch):
    """替换 RagService 的外部边界（保留真实 pipeline / observer / 持久化）。"""
    rag = get_observed_rag_service()
    monkeypatch.setattr(rag, "_vector_search_service", _FakeVectorSearch())
    monkeypatch.setattr(rag, "_llm_client", _FakeLLM())
    monkeypatch.setattr(rag, "_context_builder", _EchoContextBuilder())
    get_rag_execution_collector().clear()
    return rag


def _post_rag(client: TestClient) -> str:
    response = client.post("/api/ai/chat", json={"question": _RAG_QUESTION})
    assert response.status_code == 200, response.text
    request_id = response.json()["metadata"]["request_id"]
    _CREATED_REQUEST_IDS.append(request_id)
    return request_id


def _trace(client: TestClient, request_id: str) -> dict[str, Any]:
    response = client.get(f"{_ENDPOINT}/{request_id}")
    assert response.status_code == 200, response.text
    return response.json()


# ============================================================
# Case 1：RAG only
# ============================================================

@requires_db
class TestRagOnlyE2E:
    def test_trace_contains_rag_execution(self, client, rag_boundaries) -> None:
        request_id = _post_rag(client)

        payload = _trace(client, request_id)

        assert payload["assistant_request_id"] == request_id
        assert len(payload["rag_executions"]) >= 1
        item = payload["rag_executions"][0]
        assert item["request_id"] == request_id
        assert item["result_count"] == 2
        assert item["used_chunks_count"] == 2
        assert item["chunk_ids"] == [101, 102]
        assert item["document_ids"] == [10, 20]
        assert item["rerank_elapsed_ms"] is None
        assert item["duration_ms"] >= 0.0
        assert "id" not in item
        assert payload["llm_usage"] == []            # 本 Case 的 LLM 不产生 usage


# ============================================================
# Case 2：LLM + RAG
# ============================================================

@requires_db
class TestLlmAndRagE2E:
    def test_llm_and_rag_share_one_assistant_request_id(
        self, client, monkeypatch
    ) -> None:
        provided = f"{_PROVIDER_PREFIX}chatcmpl-1"
        rag = get_observed_rag_service()
        monkeypatch.setattr(rag, "_vector_search_service", _FakeVectorSearch())
        monkeypatch.setattr(rag, "_context_builder", _EchoContextBuilder())
        monkeypatch.setattr(
            rag, "_llm_client", _llm_client_with_accounting(provided)
        )
        get_rag_execution_collector().clear()

        request_id = _post_rag(client)
        payload = _trace(client, request_id)

        assert len(payload["llm_usage"]) >= 1
        assert len(payload["rag_executions"]) >= 1
        assert {
            row["assistant_request_id"] for row in payload["llm_usage"]
        } == {request_id}
        assert {
            row["request_id"] for row in payload["rag_executions"]
        } == {request_id}


# ============================================================
# Case 3：Tool only（RAG 段为空）
# ============================================================

@requires_db
class TestToolOnlyE2E:
    def test_tool_request_has_empty_rag_section(self, client, monkeypatch) -> None:
        # 只替换 Tool handler 的 DB 边界；Tool 持久化保持真实
        # （Trace 的 Tool 段读 ai_ops.tool_execution_record；本 Case 需要真实行）
        monkeypatch.setitem(
            orch_module._TOOL_REGISTRY._handlers,  # noqa: SLF001 —— 只换 DB 边界
            "get_inventory",
            _FakeInventoryHandler(),
        )
        orch_module._TOOL_EXECUTION_COLLECTOR.clear()

        response = client.post("/api/ai/chat", json={"question": _TOOL_QUESTION})
        assert response.status_code == 200
        request_id = response.json()["metadata"]["request_id"]
        _CREATED_REQUEST_IDS.append(request_id)

        payload = _trace(client, request_id)

        assert len(payload["tool_executions"]) >= 1     # Tool 段有数据
        assert payload["rag_executions"] == []          # RAG 段为空


# ============================================================
# Case 4：Empty Trace
# ============================================================

@requires_db
class TestEmptyTraceE2E:
    def test_unknown_request_id_is_200_with_empty_sections(self, client) -> None:
        response = client.get(f"{_ENDPOINT}/step48-unknown-request")

        assert response.status_code == 200                 # 不是 404 / 502
        payload = response.json()
        assert payload["assistant_request_id"] == "step48-unknown-request"
        assert payload["llm_usage"] == []
        assert payload["tool_executions"] == []
        assert payload["rag_executions"] == []


# ============================================================
# Case 5：Cross Request Isolation
# ============================================================

@requires_db
class TestCrossRequestIsolationE2E:
    def test_traces_do_not_mix(self, client, rag_boundaries) -> None:
        a = _post_rag(client)
        b = _post_rag(client)

        assert a != b
        trace_a = _trace(client, a)
        trace_b = _trace(client, b)

        assert {row["request_id"] for row in trace_a["rag_executions"]} == {a}
        assert {row["request_id"] for row in trace_b["rag_executions"]} == {b}
        assert trace_a["assistant_request_id"] == a
        assert trace_b["assistant_request_id"] == b


# ============================================================
# Case 6：RAG Repository Failure → 502
# ============================================================

@requires_db
class TestRagRepositoryFailureE2E:
    def test_rag_query_failure_is_502_not_empty_trace(
        self, client, rag_boundaries, monkeypatch
    ) -> None:
        request_id = _post_rag(client)                    # 先确保有数据
        rag_query = get_rag_execution_persistent_query_service()

        def _boom(query_request_id: str) -> Any:
            raise RagExecutionRepositoryError("simulated db failure")

        monkeypatch.setattr(rag_query, "list_by_request_id", _boom)

        response = client.get(f"{_ENDPOINT}/{request_id}")

        assert response.status_code == 502                 # 不是 200 + 空 Trace
        assert set(response.json()) == {"detail"}
        for leaked in ("Traceback", "SELECT", "postgresql://", "ai_ops", "db failure"):
            assert leaked not in response.text, leaked


__all__ = [
    "TestRagOnlyE2E",
    "TestLlmAndRagE2E",
    "TestToolOnlyE2E",
    "TestEmptyTraceE2E",
    "TestCrossRequestIsolationE2E",
    "TestRagRepositoryFailureE2E",
]
