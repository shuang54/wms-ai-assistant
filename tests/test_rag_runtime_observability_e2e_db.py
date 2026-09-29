"""RAG Runtime Observation → PostgreSQL 生产装配 E2E（Phase 3.12 Step 46）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

真实链路（**不**手工注入 Orchestrator / Router / RagService / Observer）：

    TestClient(create_app())
        ↓ POST /api/ai/chat
    Composition Root → AIOrchestratorService（真实）→ assistant_trace_scope
        ↓ Router（真实）→ RAG
    RagService（应用级实例；observer = CompositeRagExecutionObserver）
        ├── InMemoryRagExecutionCollector（Runtime）
        └── RagExecutionPersistenceAdapter → Service → Repository → PostgreSQL
                                                        ↓
                                    ai_ops.rag_execution_record（真实）

只替换**外部边界**：Vector Search（embedding + DB）· LLM transport ·
（Tool 用例）Tool handler 的 DB 边界 + Tool 持久化写边界。

覆盖：持久化 E2E / request_id 关联 / 跨请求隔离 / 非 RAG 隔离 /
旧 API 0 写入 / 失败隔离（200 + warning）/ restart-like（内存清空后仍在 DB）。

残留：只清理**本模块基线之后**新增的行（定向 DELETE by id > baseline；
不使用 TRUNCATE），teardown 断言 residue = 0。
"""
from __future__ import annotations

import logging
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import chat as chat_module
from backend.app.api import orchestrator_chat as orch_module
from backend.app.api import rag as rag_module
from backend.app.db.models.rag_execution_record import (
    RAG_EXECUTION_SCHEMA,
    RAG_EXECUTION_TABLE,
)
from backend.app.db.session import get_engine
from backend.app.main import app
from backend.app.services.context_builder import ContextBuildResult
from backend.app.services.rag_execution_persistent_query_service import (
    RagExecutionPersistentQueryService,
)
from backend.app.services.rag_observability_runtime import (
    get_observed_rag_service,
    get_rag_execution_collector,
    get_rag_execution_persistence_adapter,
    get_rag_observability_query_service,
)
from backend.app.services.rag_service import RagService
from backend.app.services.vector_search_service import VectorSearchResult

_TABLE = f"{RAG_EXECUTION_SCHEMA}.{RAG_EXECUTION_TABLE}"
_RAG_QUESTION = "采购入库的操作步骤是什么"
_TOOL_QUESTION = "查询物料 MAT-001 当前库存"
_LLM_ANSWER = "[e2e-db-mock-llm] 采购入库包括收货、质检与上架。"
_CHUNK_SENTINEL = "E2E-DB-SENTINEL-CHUNK"


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


def _max_id() -> int:
    with _engine().connect() as conn:
        return int(
            conn.execute(
                text(f"SELECT COALESCE(MAX(id), 0) FROM {_TABLE}")
            ).scalar_one()
        )


def _count_after(baseline: int) -> int:
    with _engine().connect() as conn:
        return int(
            conn.execute(
                text(f"SELECT COUNT(*) FROM {_TABLE} WHERE id > :baseline"),
                {"baseline": baseline},
            ).scalar_one()
        )


def _delete_after(baseline: int) -> int:
    with _engine().begin() as conn:
        return int(
            conn.execute(
                text(f"DELETE FROM {_TABLE} WHERE id > :baseline"),
                {"baseline": baseline},
            ).rowcount
        )


def _chunk(chunk_id: int, *, document_id: int = 10) -> VectorSearchResult:
    return VectorSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=0,
        content=_CHUNK_SENTINEL,
        distance=0.1,
        similarity=0.9,
        metadata={"heading": "采购入库"},
    )


class _FakeVectorSearch:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, top_k: int) -> list[VectorSearchResult]:
        self.calls.append((query, top_k))
        return [_chunk(101), _chunk(102, document_id=20)]


class _FakeLLM:
    async def chat(self, messages: list[dict[str, str]]) -> str:  # noqa: ARG002
        return _LLM_ANSWER


class _FakeInventoryHandler:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, arguments):  # noqa: ANN001, ANN204
        self.calls.append(dict(arguments))
        return {"material_code": arguments.get("material_code"), "qty": 120}


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


@pytest.fixture(scope="module", autouse=True)
def _schema():
    if not _env_flag("RUN_DB_TESTS"):
        yield
        return
    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()
    yield


@pytest.fixture()
def baseline():
    """记录基线并保证 teardown 后本模块新增行为 0（定向 DELETE）。"""
    if not _env_flag("RUN_DB_TESTS"):
        yield 0
        return
    before = _max_id()
    yield before
    _delete_after(before)
    assert _count_after(before) == 0, "RAG 表残留（Step 46 测试行未清理）"


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def rag_boundaries(monkeypatch):
    """替换 RagService 的外部边界（保留真实 pipeline / observer / DB 链路）。"""
    search = _FakeVectorSearch()
    llm = _FakeLLM()
    rag = get_observed_rag_service()
    monkeypatch.setattr(rag, "_vector_search_service", search)
    monkeypatch.setattr(rag, "_llm_client", llm)
    monkeypatch.setattr(
        rag, "_context_builder", _EchoContextBuilder()
    )
    get_rag_execution_collector().clear()
    yield search, llm
    get_rag_execution_collector().clear()


@pytest.fixture()
def tool_isolation(monkeypatch):
    """Tool 用例：替换 Tool 的 DB 边界（handler）+ 关闭 Tool 持久化写入。"""
    monkeypatch.setitem(
        orch_module._TOOL_REGISTRY._handlers,  # noqa: SLF001 —— 只换 DB 边界
        "get_inventory",
        _FakeInventoryHandler(),
    )
    monkeypatch.setattr(
        orch_module._TOOL_EXECUTION_PERSISTENCE_ADAPTER,  # noqa: SLF001
        "on_execution",
        lambda record: None,
    )
    orch_module._TOOL_EXECUTION_COLLECTOR.clear()


def _post_rag(client: TestClient):  # noqa: ANN202
    return client.post("/api/ai/chat", json={"question": _RAG_QUESTION})


# ============================================================
# 1. 持久化 E2E + request_id 关联
# ============================================================

@requires_db
class TestPersistenceE2E:
    def test_rag_request_persists_observation(self, client, rag_boundaries, baseline) -> None:
        response = _post_rag(client)

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "rag"
        request_id = payload["metadata"]["request_id"]

        rows = RagExecutionPersistentQueryService().list_by_request_id(request_id)

        assert len(rows) == 1
        row = rows[0]
        assert row.request_id == request_id                   # ← 关联
        assert row.result_count == 2
        assert row.used_chunks_count == 2
        assert row.chunk_ids == (101, 102)
        assert row.document_ids == (10, 20)
        assert row.rerank_elapsed_ms is None                  # NULL 不是 0
        assert row.reranker_used is False
        assert row.context_chars == 64
        assert _count_after(baseline) == 1                    # 恰好 1 行

    def test_runtime_and_persistent_views_coexist(self, client, rag_boundaries, baseline) -> None:
        request_id = _post_rag(client).json()["metadata"]["request_id"]

        runtime = get_rag_observability_query_service().observations_by_request_id(
            request_id
        )
        persisted = RagExecutionPersistentQueryService().list_by_request_id(
            request_id
        )

        assert len(runtime) == 1 and len(persisted) == 1
        assert runtime[0].request_id == persisted[0].request_id

    def test_http_response_has_no_new_fields(self, client, rag_boundaries, baseline) -> None:
        payload = _post_rag(client).json()

        assert list(payload) == ["route", "content", "data", "metadata"]
        assert set(payload["data"]) == {"sources", "used_chunks_count"}
        assert _CHUNK_SENTINEL not in str(payload)


# ============================================================
# 2. 跨请求隔离
# ============================================================

@requires_db
class TestCrossRequestIsolation:
    def test_each_request_persists_only_its_own_row(
        self, client, rag_boundaries, baseline
    ) -> None:
        a = _post_rag(client).json()["metadata"]["request_id"]
        b = _post_rag(client).json()["metadata"]["request_id"]

        assert a != b
        service = RagExecutionPersistentQueryService()
        rows_a = service.list_by_request_id(a)
        rows_b = service.list_by_request_id(b)

        assert [row.request_id for row in rows_a] == [a]
        assert [row.request_id for row in rows_b] == [b]
        assert _count_after(baseline) == 2


# ============================================================
# 3 / 4. 非 RAG 隔离 + 旧 API
# ============================================================

@requires_db
class TestNonRagPaths:
    def test_tool_route_persists_no_rag_record(
        self, client, tool_isolation, baseline
    ) -> None:
        response = client.post("/api/ai/chat", json={"question": _TOOL_QUESTION})

        assert response.status_code == 200
        assert response.json()["route"] == "tool"
        assert _count_after(baseline) == 0                    # 无 RAG 行

    def test_legacy_endpoints_persist_no_rag_record(
        self, client, monkeypatch, baseline
    ) -> None:
        for service in (
            rag_module._rag_service,                       # /api/rag/answer
            chat_module._chat_service._rag_service,        # /api/chat
        ):
            monkeypatch.setattr(service, "_vector_search_service", _FakeVectorSearch())
            monkeypatch.setattr(service, "_llm_client", _FakeLLM())
            monkeypatch.setattr(service, "_context_builder", _EchoContextBuilder())

        legacy_rag = client.post("/api/rag/answer", json={"query": _RAG_QUESTION})
        legacy_chat = client.post("/api/chat", json={"message": _RAG_QUESTION})

        assert legacy_rag.status_code == 200
        assert legacy_chat.status_code == 200
        assert _count_after(baseline) == 0                    # 旧链路 0 写入


# ============================================================
# 5. 失败隔离（真实装配）
# ============================================================

@requires_db
class TestFailureIsolationE2E:
    def test_persistence_failure_keeps_rag_successful(
        self, client, rag_boundaries, baseline, monkeypatch, caplog
    ) -> None:
        adapter = get_rag_execution_persistence_adapter()

        def _boom(observation):  # noqa: ANN001, ANN202
            raise RuntimeError("simulated persistence outage")

        monkeypatch.setattr(adapter, "record", _boom)

        with caplog.at_level(logging.WARNING):
            response = _post_rag(client)

        assert response.status_code == 200                    # 业务成功
        payload = response.json()
        assert payload["route"] == "rag"
        assert payload["content"] == _LLM_ANSWER
        assert payload["metadata"]["request_id"]
        assert _count_after(baseline) == 0                    # 未落行
        assert any(
            record.__dict__.get("error_type") == "RuntimeError"
            for record in caplog.records
        ), "必须留下 warning（不静默吞掉）"


# ============================================================
# 6. restart-like（Persistent ≠ Runtime Memory）
# ============================================================

@requires_db
class TestRestartLikePersistence:
    def test_persisted_observation_survives_memory_clear(
        self, client, rag_boundaries, baseline
    ) -> None:
        request_id = _post_rag(client).json()["metadata"]["request_id"]

        get_rag_execution_collector().clear()                 # 模拟进程重启

        runtime = get_rag_observability_query_service().observations_by_request_id(
            request_id
        )
        fresh_read = RagExecutionPersistentQueryService()      # 新实例 / 新仓储
        persisted = fresh_read.list_by_request_id(request_id)

        assert runtime == ()                                  # 内存已空
        assert [row.request_id for row in persisted] == [request_id]  # DB 仍在
        assert persisted[0].used_chunks_count == 2


__all__ = [
    "TestPersistenceE2E",
    "TestCrossRequestIsolation",
    "TestNonRagPaths",
    "TestFailureIsolationE2E",
    "TestRestartLikePersistence",
]
