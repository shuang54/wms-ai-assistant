"""RAG Runtime Observation 生产装配 E2E（Phase 3.12 Step 44）。

真实链路（**不**手工注入 Orchestrator / Router / RagService / Collector）：

    TestClient(create_app())
        ↓
    /api/ai/chat（真实 FastAPI 路由）
        ↓
    Composition Root（api/orchestrator_chat._default_orchestrator）
        ↓
    AIOrchestratorService（真实）→ assistant_trace_scope（真实）
        ↓
    AIRouterService（真实，规则命中 RAG）
        ↓
    RagService（**应用级已接线实例**；observer = 应用级 Collector）
        ↓
    InMemoryRagExecutionCollector（真实）→ RagExecutionObservation

只替换**外部边界**（与 Step 43 的 Fake 原则一致）：

    * Vector Search / Embedding 边界（DB + embedding）→ 确定性 Fake
    * LLM transport → 确定性 Fake
    * （仅 Tool 用例）Tool Handler 的 DB 边界 → 确定性 Fake handler

0 DeepSeek / 0 网络 / 0 数据库写入 / 0 新 HTTP API / 0 Assistant Trace 修改。
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api import chat as chat_module
from backend.app.api import orchestrator_chat as orch_module
from backend.app.api import rag as rag_module
from backend.app.config import settings
from backend.app.main import app
from backend.app.services.rag_execution_observation import (
    RagExecutionObservation,
)
from backend.app.services.composite_rag_execution_observer import (
    CompositeRagExecutionObserver,
)
from backend.app.services.rag_observability_runtime import (
    get_observed_rag_service,
    get_rag_execution_collector,
    get_rag_execution_persistence_adapter,
    get_rag_observability_query_service,
)
from backend.app.services.vector_search_service import VectorSearchResult

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RAG_QUESTION = "采购入库的操作步骤是什么"
_TOOL_QUESTION = "查询物料 MAT-001 当前库存"
_LLM_ANSWER = "[e2e-mock-llm] 采购入库包括收货、质检与上架三个步骤。"
_CHUNK_SENTINEL = "E2E-SENTINEL-CHUNK-CONTENT"

_RAG_METADATA_KEYS = {
    "decision_source", "route_reason", "knowledge_scope",
    "rag_used_chunks", "request_id",
}
_FORBIDDEN_RESPONSE_KEYS = (
    "query", "answer_duplicate", "embedding", "prompt", "messages",
    "raw_response", "sql", "password", "api_key", "authorization",
    "database_url", "session", "connection", "traceback", "chunks",
    "retrieval", "observations", "latency",
)


def _chunk(
    chunk_id: int, *, document_id: int = 10, chunk_index: int = 0
) -> VectorSearchResult:
    return VectorSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=chunk_index,
        content=_CHUNK_SENTINEL,
        distance=0.1,
        similarity=0.9,
        metadata={"heading": "采购入库"},
    )


class _FakeVectorSearch:
    """Vector Search 边界（替代 Embedding + knowledge DB 查询）。"""

    def __init__(self, results: list[VectorSearchResult]) -> None:
        self._results = results
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, top_k: int) -> list[VectorSearchResult]:
        self.calls.append((query, top_k))
        return list(self._results)


class _FakeLLM:
    """LLM transport 边界（0 网络）。"""

    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return _LLM_ANSWER


class _FakeInventoryHandler:
    """Tool Handler 的 DB 边界（0 数据库）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, arguments):  # noqa: ANN001, ANN204
        self.calls.append(dict(arguments))
        return {"material_code": arguments.get("material_code"), "qty": 120}


@pytest.fixture()
def collector():
    """应用级 RAG Collector（清理前后各一次；不新建实例）。"""
    target = get_rag_execution_collector()
    target.clear()
    yield target
    target.clear()


@pytest.fixture(autouse=True)
def no_db_writes(monkeypatch):
    """本文件（非 DB-gated）**不得写数据库**：

    只替换 RAG 持久化 Adapter 的 **DB 写边界**（计数 no-op）——
    Composite fan-out / Collector / RagService / 装配仍全部真实。
    真正的持久化链路由 ``tests/test_rag_runtime_observability_e2e_db.py``
    （RUN_DB_TESTS=1）验证。
    """
    persisted: list[str] = []
    adapter = get_rag_execution_persistence_adapter()
    monkeypatch.setattr(
        adapter,
        "record",
        lambda observation: persisted.append(observation.request_id),
    )
    return persisted


@pytest.fixture()
def rag_boundaries(monkeypatch, collector):
    """只替换 RagService 的**外部边界**（保留真实 pipeline / observer / 装配）。"""
    search = _FakeVectorSearch([_chunk(101), _chunk(102, document_id=20)])
    llm = _FakeLLM()
    rag = get_observed_rag_service()
    monkeypatch.setattr(rag, "_vector_search_service", search)
    monkeypatch.setattr(rag, "_llm_client", llm)
    return search, llm


@pytest.fixture()
def client():
    """真实应用（create_app 无 lifespan → 不触碰 DB）。"""
    return TestClient(app)


def _post_rag(client: TestClient):
    return client.post("/api/ai/chat", json={"question": _RAG_QUESTION})


def _stable_fields(observation: RagExecutionObservation) -> tuple:
    return (
        observation.request_id,
        observation.result_count,
        observation.used_chunks_count,
        observation.top_k,
        observation.context_truncated,
        observation.reranker_used,
        observation.rerank_elapsed_ms,
        observation.chunk_ids,
        observation.document_ids,
    )


# ============================================================
# 1 / 2. 真实装配链路 + request_id 关联
# ============================================================

class TestProductionWiringE2E:
    def test_rag_request_produces_observation_end_to_end(
        self, client, rag_boundaries, collector
    ) -> None:
        search, llm = rag_boundaries

        response = _post_rag(client)

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "rag"
        request_id = payload["metadata"]["request_id"]
        assert isinstance(request_id, str) and request_id

        records = collector.records()
        assert len(records) == 1                      # 真实装配自动产生
        observation = records[0]
        assert observation.request_id == request_id   # ← 关联
        assert search.calls == [(_RAG_QUESTION, settings.rag.default_top_k)]
        assert len(llm.calls) == 1
        assert observation.result_count == 2
        assert observation.used_chunks_count == 2
        assert observation.chunk_ids == (101, 102)
        assert observation.document_ids == (10, 20)
        assert observation.top_k == settings.rag.default_top_k
        assert observation.context_chars > 0
        assert observation.reranker_used is False
        assert observation.rerank_elapsed_ms is None
        assert observation.duration_ms >= 0.0

    def test_request_id_correlation_via_query_service(
        self, client, rag_boundaries, collector
    ) -> None:
        _post_rag(client)
        request_id = collector.records()[0].request_id
        query = get_rag_observability_query_service()

        matched = query.observations_by_request_id(request_id)
        latest = query.latest_observation_by_request_id(request_id)

        assert len(matched) == 1
        assert latest is not None
        assert latest.request_id == request_id
        assert latest.to_dict()["request_id"] == request_id
        assert query.collector is collector

    def test_wiring_identity_is_the_app_level_instance(
        self, client, rag_boundaries, collector, no_db_writes
    ) -> None:
        rag = get_observed_rag_service()

        # Composition Root 注入的就是应用级已接线实例（单实例 / 单 Composite）
        assert orch_module._default_orchestrator._rag is rag
        observer = rag._observer
        assert isinstance(observer, CompositeRagExecutionObserver)
        assert observer.observers == (
            collector,
            get_rag_execution_persistence_adapter(),
        )
        # 真实 Router（未替换）
        assert orch_module._default_orchestrator._router is not None
        # 真实 fan-out 确实触达持久化边界（DB 写在非 DB 测试中被替换）
        request_id = _post_rag(client).json()["metadata"]["request_id"]
        assert no_db_writes == [request_id]
        # 旧链路仍是**未观测**实例（Step 44 §六：不给旧 API 伪造 trace）
        assert rag_module._rag_service is not rag
        assert rag_module._rag_service._observer is None
        assert chat_module._chat_service._rag_service._observer is None


# ============================================================
# 3 / 4. 生命周期 / 隔离
# ============================================================

class TestCollectorLifecycleE2E:
    def test_same_collector_reused_across_requests(
        self, client, rag_boundaries, collector
    ) -> None:
        first = _post_rag(client).json()["metadata"]["request_id"]
        second = _post_rag(client).json()["metadata"]["request_id"]

        assert first != second
        assert get_rag_execution_collector() is collector      # 非每请求新建
        records = collector.records()
        assert [r.request_id for r in records] == [first, second]

    def test_cross_request_isolation(
        self, client, rag_boundaries, collector
    ) -> None:
        a = _post_rag(client).json()["metadata"]["request_id"]
        b = _post_rag(client).json()["metadata"]["request_id"]
        query = get_rag_observability_query_service()

        observations_a = query.observations_by_request_id(a)
        observations_b = query.observations_by_request_id(b)

        assert len(observations_a) == 1 and len(observations_b) == 1
        assert observations_a[0].request_id == a
        assert observations_b[0].request_id == b
        assert observations_a[0].request_id != observations_b[0].request_id

    def test_clear_empties_runtime_window(
        self, client, rag_boundaries, collector
    ) -> None:
        request_id = _post_rag(client).json()["metadata"]["request_id"]
        assert len(collector.records_by_request_id(request_id)) == 1

        collector.clear()

        assert collector.records() == ()
        assert (
            get_rag_observability_query_service().observations_by_request_id(
                request_id
            )
            == ()
        )

    def test_repeated_requests_are_deterministic(
        self, client, rag_boundaries, collector
    ) -> None:
        _post_rag(client)
        _post_rag(client)

        first, second = collector.records()
        assert _stable_fields(first)[1:] == _stable_fields(second)[1:]


# ============================================================
# 5 / 6. 非 RAG 路径 + 旧 API 回归
# ============================================================

class TestNonRagPathsE2E:
    def test_tool_route_produces_no_rag_observation(
        self, client, collector, monkeypatch
    ) -> None:
        """TOOL 请求：RAG Observation = 0；Tool 观测仍正常。

        只替换两个**外部边界**（与 Fake LLM / Fake retrieval 同性质）：
            * Tool Handler 的 DB 边界 → Fake handler
            * Tool 持久化 Adapter 的 DB 写边界 → 计数 no-op
              （本阶段禁止 DB 写；fan-out 仍然真实发生）
        """
        handler = _FakeInventoryHandler()
        persisted: list[str] = []
        monkeypatch.setitem(
            orch_module._TOOL_REGISTRY._handlers,  # noqa: SLF001 —— 只换 DB 边界
            "get_inventory",
            handler,
        )
        monkeypatch.setattr(
            orch_module._TOOL_EXECUTION_PERSISTENCE_ADAPTER,  # noqa: SLF001
            "on_execution",
            lambda record: persisted.append(record.request_id),
        )
        orch_module._TOOL_EXECUTION_COLLECTOR.clear()

        response = client.post("/api/ai/chat", json={"question": _TOOL_QUESTION})

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "tool"
        request_id = payload["metadata"]["request_id"]
        assert collector.records() == ()              # 无假的 RAG 观测
        tool_records = orch_module._TOOL_EXECUTION_COLLECTOR.records_by_request_id(
            request_id
        )
        assert len(tool_records) == 1                 # Tool 链路正常
        assert tool_records[0].tool_name == "get_inventory"
        assert handler.calls, "真实 Tool 执行边界应被调用（handler 为 fake）"
        # 复合 observer 仍真实 fan-out（持久化边界被 fake → 0 DB 写）
        assert persisted == [request_id]
        orch_module._TOOL_EXECUTION_COLLECTOR.clear()

    def test_legacy_rag_answer_has_no_observation(
        self, client, collector, monkeypatch
    ) -> None:
        legacy = rag_module._rag_service
        monkeypatch.setattr(
            legacy, "_vector_search_service", _FakeVectorSearch([_chunk(1)])
        )
        monkeypatch.setattr(legacy, "_llm_client", _FakeLLM())

        response = client.post("/api/rag/answer", json={"query": _RAG_QUESTION})

        assert response.status_code == 200
        assert response.json()["answer"] == _LLM_ANSWER
        assert collector.records() == ()              # 旧链路 0 观测

    def test_legacy_chat_has_no_observation(
        self, client, collector, monkeypatch
    ) -> None:
        legacy = chat_module._chat_service._rag_service
        monkeypatch.setattr(
            legacy, "_vector_search_service", _FakeVectorSearch([_chunk(1)])
        )
        monkeypatch.setattr(legacy, "_llm_client", _FakeLLM())

        response = client.post("/api/chat", json={"message": _RAG_QUESTION})

        assert response.status_code == 200
        assert response.json()["answer"] == _LLM_ANSWER
        assert collector.records() == ()


# ============================================================
# 7. Observer failure isolation（生产装配下）
# ============================================================

class TestObserverFailureIsolationE2E:
    def test_collector_failure_does_not_break_rag(
        self, client, rag_boundaries, collector, monkeypatch
    ) -> None:
        def _boom(observation):  # noqa: ANN001, ANN202
            raise RuntimeError("collector boom")

        monkeypatch.setattr(collector, "record", _boom)

        response = _post_rag(client)

        assert response.status_code == 200           # RAG success 不受影响
        payload = response.json()
        assert payload["route"] == "rag"
        assert payload["content"] == _LLM_ANSWER
        assert payload["metadata"]["request_id"]
        assert collector.records() == ()              # 观测确实失败但被隔离


# ============================================================
# 8 / 9 / 10. HTTP contract / Security / 无新 API
# ============================================================

class TestContractAndSecurityE2E:
    def test_http_response_contract_unchanged(
        self, client, rag_boundaries, collector
    ) -> None:
        payload = _post_rag(client).json()

        assert list(payload) == ["route", "content", "data", "metadata"]
        assert set(payload["metadata"]) == _RAG_METADATA_KEYS
        assert set(payload["data"]) == {"sources", "used_chunks_count"}
        assert list(payload["data"]["sources"][0]) == [
            "chunk_id", "document_id", "chunk_index", "similarity", "metadata",
        ]
        # 未把 RAG 观测 / 检索细节写进响应
        for forbidden in _FORBIDDEN_RESPONSE_KEYS:
            assert forbidden not in payload, forbidden
            assert forbidden not in payload["data"], forbidden
            assert forbidden not in payload["metadata"], forbidden

    def test_no_sensitive_values_in_http_response(
        self, client, rag_boundaries, collector
    ) -> None:
        response = _post_rag(client)

        assert _CHUNK_SENTINEL not in response.text   # chunk 正文不外泄
        for secret in ("postgresql://", "api_key", "Authorization"):
            assert secret not in response.text, secret

    def test_observation_fields_still_thirteen(
        self, client, rag_boundaries, collector
    ) -> None:
        _post_rag(client)
        observation = collector.records()[0]

        assert tuple(observation.to_dict()) == (
            "request_id", "started_at", "finished_at", "duration_ms",
            "result_count", "used_chunks_count", "top_k", "context_truncated",
            "context_chars", "reranker_used", "rerank_elapsed_ms",
            "chunk_ids", "document_ids",
        )
        for forbidden in ("similarity", "query", "content", "prompt", "sql"):
            assert forbidden not in observation.to_dict(), forbidden

    def test_no_new_http_api_for_rag_observability(self) -> None:
        paths = sorted(app.openapi()["paths"])

        assert [p for p in paths if "observability" in p] == [
            "/api/observability/assistant-trace/{assistant_request_id}",
            "/api/observability/tools",
            "/api/observability/tools/history",
            "/api/observability/tools/metrics",
            "/api/observability/tools/metrics/persistent",
        ]
        assert [p for p in paths if "rag" in p.lower()] == ["/api/rag/answer"]

    def test_runtime_object_has_no_persistence_capability(self) -> None:
        """Step 46：持久化在**并列的 Adapter** 上；RagService / Collector 本身
        仍不持有 Repository / Session（Runtime 与 Persistent 边界清晰）。"""
        from backend.app.db.base import Base

        rag_tables = sorted(n for n in Base.metadata.tables if "rag" in n.lower())
        assert rag_tables == ["ai_ops.rag_execution_record"]   # 仅契约定义的 1 张表
        rag = get_observed_rag_service()
        for forbidden in ("_repository", "_session", "_engine"):
            assert not hasattr(rag, forbidden), forbidden
        assert not hasattr(get_rag_execution_collector(), "_repository")


__all__ = [
    "TestProductionWiringE2E",
    "TestCollectorLifecycleE2E",
    "TestNonRagPathsE2E",
    "TestObserverFailureIsolationE2E",
    "TestContractAndSecurityE2E",
]
