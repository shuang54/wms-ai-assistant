"""Assistant Outcome Persistence（Phase 3.12 Step 64）——request-level 终态持久化 + Trace 集成。

链路：

    POST /api/ai/chat
        ↓ 真实 app / 真实 Orchestrator（唯一判定点，Step 63）
    AIOrchestrationResult.metadata["outcome"]（SUCCESS / EMPTY / REFUSED / FAILED）
        ↓ best-effort recorder（DB 已配置时装配；失败只 warning）
    ai_ops.assistant_outcome_record（assistant_request_id UNIQUE · first-write-wins）
        ↓ Assistant Trace Read Model
    GET /api/observability/assistant-trace/{A} → outcome（无记录 → null）

两类测试互不混杂：

    * 离线（默认套件；RUN_DB_TESTS=0）：Fake Repository / Fake 读边界 —— 0 DB；
    * DB-gated（RUN_DB_TESTS=1）：真实 PostgreSQL + 真实 API E2E（逐步清理残留）。
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, text

from backend.app.api import orchestrator_chat as root
from backend.app.db.assistant_outcome_repository import (
    AssistantOutcomeRepository,
    AssistantOutcomeRepositoryError,
    AssistantOutcomeRow,
)
from backend.app.db.models.assistant_outcome_record import AssistantOutcomeRecord
from backend.app.db.session import get_engine, get_session_factory
from backend.app.dto.assistant_outcome import (
    AssistantOutcome,
    determine_assistant_outcome,
)
from backend.app.main import app
from backend.app.services.assistant_outcome_persistence_service import (
    AssistantOutcomePersistenceService,
    BestEffortAssistantOutcomeRecorder,
)
from backend.app.services.assistant_outcome_query_service import (
    AssistantOutcomeQueryService,
)
from backend.app.services.assistant_trace_query_service import (
    AssistantTraceQueryService,
    AssistantTraceView,
)
from backend.app.services.sql_executor_service import SQLExecutionResult
from backend.app.services.text_to_sql_service import REFUSAL_MARKER

# 复用既有装配 / Fake（不重造）
from tests.test_assistant_trace_correlation_e2e import (  # noqa: PLC2701
    _RAG_QUESTION,
    _TOOL_QUESTION,
    _response_body,
    e2e,
)
from tests.test_assistant_trace_multi_path_e2e import (  # noqa: PLC2701
    _install_t2sql_route,
    _retry_t2sql_client,
)

_T2SQL_QUESTION = "统计最近7天的入库单数量"
_TRACE = "/api/observability/assistant-trace"
_PREFIX = "step64-"
_SENTINEL = "STEP64-SECRET-SENTINEL"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _run(coro_factory: Any) -> Any:
    import asyncio

    return asyncio.run(coro_factory())


# ============================================================
# Fake（离线；0 DB）
# ============================================================

class _FakeOutcomeRepository:
    """内存仓储：复刻 UNIQUE + ON CONFLICT DO NOTHING（first-write-wins）。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.rows: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self._fail = fail
        self._next_id = 1

    def create(self, *, assistant_request_id: str, outcome: str) -> int | None:
        self.calls.append((assistant_request_id, outcome))
        if self._fail:
            raise AssistantOutcomeRepositoryError("step64-forced-failure")
        if assistant_request_id in self.rows:
            return None                      # DO NOTHING（不覆盖终态）
        self.rows[assistant_request_id] = outcome
        self._next_id += 1
        return self._next_id - 1

    def get_by_assistant_request_id(
        self, assistant_request_id: str
    ) -> AssistantOutcomeRow | None:
        if self._fail:
            raise AssistantOutcomeRepositoryError("step64-forced-failure")
        outcome = self.rows.get(assistant_request_id)
        if outcome is None:
            return None
        return AssistantOutcomeRow(
            assistant_request_id=assistant_request_id,
            outcome=outcome,
            created_at=datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc),
        )


class _RecordingOutcomeRecorder:
    """记录 (assistant_request_id, outcome)（离线 E2E 断言用）。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail = fail

    async def arecord(
        self, assistant_request_id: str, outcome: AssistantOutcome
    ) -> int | None:
        self.calls.append((assistant_request_id, str(outcome)))
        if self._fail:
            raise RuntimeError(_SENTINEL)
        return None


def _trace_service(outcome_service: Any) -> AssistantTraceQueryService:
    """Trace 组合服务（离线：三段空读边界 + 注入的 outcome 读边界）。"""

    class _Empty:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def list_by_assistant_request_id(self, request_id: str) -> list[Any]:
            self.calls.append(request_id)
            return []

        def list_by_request_id(self, request_id: str) -> list[Any]:
            self.calls.append(request_id)
            return []

    return AssistantTraceQueryService(
        tool_observability_query_service=_Empty(),
        llm_usage_query_service=_Empty(),
        rag_execution_query_service=_Empty(),
        outcome_query_service=outcome_service,
    )


# ============================================================
# 1~6. Persistence Contract（离线）
# ============================================================

class TestPersistenceContractOffline:
    @pytest.mark.parametrize(
        "outcome",
        [
            AssistantOutcome.SUCCESS,
            AssistantOutcome.EMPTY,
            AssistantOutcome.REFUSED,
            AssistantOutcome.FAILED,
        ],
    )
    def test_all_four_outcomes_persist(self, outcome: AssistantOutcome) -> None:
        repository = _FakeOutcomeRepository()
        service = AssistantOutcomePersistenceService(repository=repository)

        created_id = service.persist("step64-A", outcome)

        assert created_id == 1
        assert repository.rows == {"step64-A": outcome.value}

    def test_idempotent_same_outcome_one_row(self) -> None:
        repository = _FakeOutcomeRepository()
        service = AssistantOutcomePersistenceService(repository=repository)

        first = service.persist("step64-A", AssistantOutcome.SUCCESS)
        second = service.persist("step64-A", AssistantOutcome.SUCCESS)

        assert first == 1
        assert second is None                 # 幂等：正常结果，不是错误
        assert len(repository.rows) == 1

    def test_conflict_first_write_wins(self) -> None:
        repository = _FakeOutcomeRepository()
        service = AssistantOutcomePersistenceService(repository=repository)

        service.persist("step64-A", AssistantOutcome.SUCCESS)
        conflicting = service.persist("step64-A", AssistantOutcome.FAILED)

        assert conflicting is None
        assert repository.rows["step64-A"] == "SUCCESS"   # 终态不被覆盖

    def test_invalid_outcome_or_id_rejected_before_db(self) -> None:
        repository = _FakeOutcomeRepository()
        service = AssistantOutcomePersistenceService(repository=repository)

        with pytest.raises(ValueError):
            service.persist("step64-A", "SUCCESS")        # str 不接受
        with pytest.raises(ValueError):
            service.persist("", AssistantOutcome.SUCCESS)
        with pytest.raises(ValueError):
            service.persist("x" * 129, AssistantOutcome.SUCCESS)
        assert repository.calls == []                     # 未触达 DB

    def test_persistence_failure_is_isolated_by_recorder(self) -> None:
        failing = _FakeOutcomeRepository(fail=True)
        service = AssistantOutcomePersistenceService(repository=failing)
        recorder = BestEffortAssistantOutcomeRecorder(
            persistence_service=service
        )

        # 纯服务层：异常上抛（明确失败）
        with pytest.raises(AssistantOutcomeRepositoryError):
            service.persist("step64-A", AssistantOutcome.SUCCESS)

        # best-effort 层：永不抛出（业务隔离边界）
        assert recorder.record("step64-A", AssistantOutcome.SUCCESS) is None
        assert (
            _run(
                lambda: recorder.arecord(
                    "step64-A", AssistantOutcome.SUCCESS
                )
            )
            is None
        )
        assert len(failing.calls) == 3                    # 无 retry（恰好 1 次/调用）

    def test_query_service_maps_rows_and_unknown_values(self) -> None:
        repository = _FakeOutcomeRepository()
        service = AssistantOutcomePersistenceService(repository=repository)
        query = AssistantOutcomeQueryService(repository=repository)

        assert query.get_by_assistant_request_id("step64-missing") is None

        service.persist("step64-B", AssistantOutcome.REFUSED)
        assert (
            query.get_by_assistant_request_id("step64-B")
            is AssistantOutcome.REFUSED
        )

        repository.rows["step64-corrupt"] = "WHATEVER"
        with pytest.raises(ValueError):                   # 未知值 → 不猜、不降级
            query.get_by_assistant_request_id("step64-corrupt")


# ============================================================
# 8~9. Trace Read Model（离线）
# ============================================================

class TestTraceReadModelOffline:
    def test_historical_trace_without_outcome_is_none(self) -> None:
        service = _trace_service(AssistantOutcomeQueryService(
            repository=_FakeOutcomeRepository()
        ))

        view = service.get_trace("step64-historical")

        assert view.outcome is None                       # 不猜、不回填
        assert view.llm_usage == ()
        assert view.tool_executions == ()
        assert view.rag_executions == ()

    def test_trace_returns_stored_outcome(self) -> None:
        repository = _FakeOutcomeRepository()
        AssistantOutcomePersistenceService(repository=repository).persist(
            "step64-C", AssistantOutcome.EMPTY
        )
        service = _trace_service(AssistantOutcomeQueryService(repository=repository))

        view = service.get_trace("step64-C")

        assert view.outcome is AssistantOutcome.EMPTY

    def test_view_rejects_invalid_outcome_type(self) -> None:
        with pytest.raises(ValueError):
            AssistantTraceView(
                assistant_request_id="step64-A",
                llm_usage=(),
                tool_executions=(),
                outcome="SUCCESS",                        # type: ignore[arg-type]
            )

    def test_view_requires_outcome_query_boundary(self) -> None:
        with pytest.raises(TypeError):
            AssistantTraceQueryService(
                tool_observability_query_service=type(
                    "_Empty", (), {"list_by_request_id": lambda self, rid: []}
                )(),
                llm_usage_query_service=type(
                    "_Empty",
                    (),
                    {"list_by_assistant_request_id": lambda self, rid: []},
                )(),
                rag_execution_query_service=type(
                    "_Empty", (), {"list_by_request_id": lambda self, rid: []}
                )(),
                outcome_query_service=object(),           # 缺方法 → fail fast
            )


# ============================================================
# 7. Orchestrator：best-effort 记录（离线 E2E）
# ============================================================

class TestOrchestratorOutcomeRecordingOffline:
    def test_success_path_records_outcome(self, e2e) -> None:
        e2e()
        recorder = _RecordingOutcomeRecorder()
        root._default_orchestrator._outcome_recorder = recorder
        try:
            with TestClient(app) as http:
                payload = http.post(
                    "/api/ai/chat", json={"question": _RAG_QUESTION}
                ).json()
        finally:
            root._default_orchestrator._outcome_recorder = None

        assistant_request_id = payload["metadata"]["request_id"]
        assert recorder.calls == [
            (assistant_request_id, payload["metadata"]["outcome"])
        ]

    def test_failure_path_records_failed_then_raises(self, e2e, monkeypatch) -> None:
        repository = e2e()[0]
        recorder = _RecordingOutcomeRecorder()
        root._default_orchestrator._outcome_recorder = recorder
        monkeypatch.setattr(
            root._default_orchestrator,
            "_rag",
            _LlmRag(
                _client_with_handler(
                    lambda request: httpx.Response(
                        500, json={"error": {"message": _SENTINEL}}
                    ),
                    repository,
                )
            ),
        )
        try:
            with TestClient(app) as http:
                response = http.post(
                    "/api/ai/chat", json={"question": _RAG_QUESTION}
                )
        finally:
            root._default_orchestrator._outcome_recorder = None

        assert response.status_code == 500
        assert set(response.json()) == {"detail"}          # error body 未变
        assert len(recorder.calls) == 1
        assert recorder.calls[0][1] == "FAILED"

    def test_recorder_failure_does_not_break_business(self, e2e) -> None:
        e2e()
        root._default_orchestrator._outcome_recorder = _RecordingOutcomeRecorder(
            fail=True
        )
        try:
            with TestClient(app) as http:
                response = http.post(
                    "/api/ai/chat", json={"question": _TOOL_QUESTION}
                )
        finally:
            root._default_orchestrator._outcome_recorder = None

        assert response.status_code == 200                # 记录失败 ≠ 业务失败
        assert response.json()["metadata"]["outcome"] == "SUCCESS"


class _LlmRag:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def answer(self, query: str, **kwargs: Any):  # noqa: ANN003
        from backend.app.services.rag_service import RagResponse

        result = await self._client.chat([{"role": "user", "content": query}])
        return RagResponse(
            answer=getattr(result, "content", None) or "",
            sources=(),
            used_chunks_count=1,
        )


def _client_with_handler(handler: Any, repository: Any) -> Any:
    from backend.app.llm.client import OpenAICompatibleClient
    from backend.app.services.llm_usage_persistence_service import (
        DatabaseLLMAccountingSink,
        LLMUsagePersistenceService,
    )

    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://step64.fake/v1",
        model="step64-model",
        provider="step64-provider",
        transport=httpx.MockTransport(handler),
        accounting_sink=DatabaseLLMAccountingSink(
            persistence_service=LLMUsagePersistenceService(repository=repository)
        ),
    )


# ============================================================
# 10. Security / Contract（离线静态）
# ============================================================

class TestOutcomeContractAndSecurity:
    def test_table_columns_are_minimal_whitelist(self) -> None:
        columns = AssistantOutcomeRecord.__table__.columns

        assert set(columns.keys()) == {
            "id",
            "assistant_request_id",
            "outcome",
            "created_at",
        }
        assert isinstance(columns["id"].type, BigInteger)
        assert columns["id"].primary_key is True
        assert columns["assistant_request_id"].type.length == 128
        assert columns["assistant_request_id"].nullable is False
        assert columns["outcome"].type.length == 16
        assert columns["outcome"].nullable is False
        assert columns["created_at"].nullable is False
        assert columns["created_at"].type.timezone is True
        assert columns["created_at"].server_default is not None

    def test_assistant_request_id_is_unique(self) -> None:
        indexes = {
            index.name: index
            for index in AssistantOutcomeRecord.__table__.indexes
        }
        assert "uq_assistant_outcome_record_assistant_request_id" in indexes
        index = indexes["uq_assistant_outcome_record_assistant_request_id"]
        assert index.unique is True
        assert [c.name for c in index.columns] == ["assistant_request_id"]

    def test_no_error_class_or_extra_fields(self) -> None:
        columns = set(AssistantOutcomeRecord.__table__.columns.keys())
        for forbidden in (
            "route",
            "status",
            "error_class",
            "error_message",
            "content",
            "prompt",
            "question",
            "answer",
            "sql",
            "exception",
        ):
            assert forbidden not in columns, forbidden
        assert [m.value for m in AssistantOutcome] == [
            "SUCCESS",
            "EMPTY",
            "REFUSED",
            "FAILED",
        ]

    def test_trace_dto_exposes_only_the_five_fields(self) -> None:
        from backend.app.api.assistant_trace import AssistantTraceResponse

        assert list(AssistantTraceResponse.model_fields) == [
            "assistant_request_id",
            "outcome",
            "llm_usage",
            "tool_executions",
            "rag_executions",
        ]

    def test_outcome_values_are_deterministic(self) -> None:
        for _ in range(3):
            assert (
                determine_assistant_outcome(route="rag", rag_used_chunks=0)
                is AssistantOutcome.EMPTY
            )
            assert (
                determine_assistant_outcome(route="tool", tool_success=False)
                is AssistantOutcome.FAILED
            )
            assert (
                determine_assistant_outcome(route="text_to_sql", refused=True)
                is AssistantOutcome.REFUSED
            )

    def test_persisted_payload_has_no_sensitive_content(self) -> None:
        repository = _FakeOutcomeRepository()
        service = AssistantOutcomePersistenceService(repository=repository)

        service.persist("step64-secure", AssistantOutcome.SUCCESS)

        blob = json.dumps(repository.rows, default=str)
        for forbidden in (_SENTINEL, "sk-", "Bearer ", "postgresql://", "SELECT "):
            assert forbidden not in blob, forbidden


# ============================================================
# DB-gated：真实 PostgreSQL + 真实 API E2E
# ============================================================

def _own_outcome_rows() -> int:
    engine = get_engine()
    assert engine is not None
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.assistant_outcome_record "
                    "WHERE assistant_request_id LIKE :prefix"
                ),
                {"prefix": f"{_PREFIX}%"},
            ).scalar_one()
        )


def _delete_own_rows(request_ids: list[str]) -> None:
    engine = get_engine()
    assert engine is not None
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM ai_ops.assistant_outcome_record "
                "WHERE assistant_request_id LIKE :prefix "
                "OR assistant_request_id = ANY(:ids)"
            ),
            {"prefix": f"{_PREFIX}%", "ids": request_ids or [""]},
        )
        conn.execute(
            text(
                "DELETE FROM ai_ops.llm_usage_record "
                "WHERE provider = :provider OR request_id LIKE :prefix "
                "OR assistant_request_id = ANY(:ids)"
            ),
            {
                "provider": "step64-provider",
                "prefix": f"{_PREFIX}%",
                "ids": request_ids or [""],
            },
        )
        conn.execute(
            text(
                "DELETE FROM ai_ops.tool_execution_record "
                "WHERE request_id = ANY(:ids)"
            ),
            {"ids": request_ids or [""]},
        )
        conn.execute(
            text(
                "DELETE FROM ai_ops.rag_execution_record "
                "WHERE request_id = ANY(:ids)"
            ),
            {"ids": request_ids or [""]},
        )


@pytest.fixture()
def outcome_db(monkeypatch: pytest.MonkeyPatch):
    """真实 app + 真实 recorder 装配 + 定向清理（仅 synthetic step64-* 数据）。"""
    engine = get_engine()
    if engine is None or get_session_factory() is None:
        pytest.skip("DATABASE_URL 未配置")
    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()                              # 新表随 create_all 创建

    state: dict[str, Any] = {"request_ids": []}
    _delete_own_rows([])

    # ① RAG：真实 observed RagService + 假检索 + 假 LLM（MockTransport）
    from backend.app.services.rag_observability_runtime import (
        get_observed_rag_service,
    )

    rag = get_observed_rag_service()
    rag_client = _client_with_handler(
        lambda request: httpx.Response(
            200, json=_response_body(f"{_PREFIX}llm-rag-1")
        ),
        None,
    )
    monkeypatch.setattr(rag, "_llm_client", rag_client)
    monkeypatch.setattr(rag, "_vector_search_service", _FakeVectorSearch())
    monkeypatch.setattr(rag, "_context_builder", _EchoContextBuilder())

    # ② TOOL：真实执行边界 + 不调用 LLM 的 handler
    monkeypatch.setitem(
        root._TOOL_REGISTRY._handlers, "get_inventory", _RecordingHandler()
    )

    # ③ TEXT_TO_SQL：真实服务 + 真实 DB sink（重试 2 次 → 2 行 usage）
    executor = _install_t2sql_route(
        monkeypatch,
        _retry_t2sql_client(
            provider_ids=(f"{_PREFIX}llm-t2sql-1", f"{_PREFIX}llm-t2sql-2"),
            use_db_sink=True,
        ),
    )
    state["executor"] = executor
    yield state
    # Runtime Collector 也必须清空：既有 DB 测试断言其快照为空
    # （Step 64 的 Tool 路径会向应用级 Collector 扇出同一份 record）。
    root._TOOL_EXECUTION_COLLECTOR.clear()
    _clear_rag_collector()
    _delete_own_rows(list(state["request_ids"]))
    assert _own_outcome_rows() == 0, "Step 64 outcome 表残留"


def _clear_rag_collector() -> None:
    from backend.app.services.rag_observability_runtime import (
        get_rag_execution_collector,
    )

    get_rag_execution_collector().clear()


def _ask(http: TestClient, question: str, state: dict[str, Any]) -> tuple[int, Any]:
    response = http.post("/api/ai/chat", json={"question": question})
    if response.status_code == 200:
        state["request_ids"].append(response.json()["metadata"]["request_id"])
    return response.status_code, response


def _trace_outcome(http: TestClient, request_id: str) -> Any:
    response = http.get(f"{_TRACE}/{request_id}")
    assert response.status_code == 200, response.text
    return response.json()["outcome"]


@requires_db
class TestOutcomePersistenceDb:
    def test_success_and_idempotency_and_conflict(self, outcome_db) -> None:
        repository = AssistantOutcomeRepository()
        service = AssistantOutcomePersistenceService(repository=repository)
        request_id = f"{_PREFIX}repo-{uuid.uuid4().hex[:8]}"
        outcome_db["request_ids"].append(request_id)

        first = service.persist(request_id, AssistantOutcome.SUCCESS)
        assert isinstance(first, int)

        assert service.persist(request_id, AssistantOutcome.SUCCESS) is None
        assert service.persist(request_id, AssistantOutcome.FAILED) is None

        row = repository.get_by_assistant_request_id(request_id)
        assert row is not None
        assert row.outcome == "SUCCESS"                   # first-write-wins
        with get_engine().connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.assistant_outcome_record "
                    "WHERE assistant_request_id = :rid"
                ),
                {"rid": request_id},
            ).scalar_one()
        assert count == 1

    def test_historical_request_has_no_outcome(self, outcome_db) -> None:
        with TestClient(app) as http:
            unknown = f"{_PREFIX}historical-{uuid.uuid4().hex[:8]}"
            assert _trace_outcome(http, unknown) is None
            # 历史 Trace 无终态 → 空 Trace 依旧 200（不猜 SUCCESS）
            body = http.get(f"{_TRACE}/{unknown}").json()
        assert body["llm_usage"] == []
        assert body["tool_executions"] == []
        assert body["rag_executions"] == []


@requires_db
class TestOutcomeApiE2eDb:
    def test_rag_success(self, outcome_db) -> None:
        with TestClient(app) as http:
            status, response = _ask(http, _RAG_QUESTION, outcome_db)
            assert status == 200
            request_id = response.json()["metadata"]["request_id"]
            assert response.json()["metadata"]["outcome"] == "SUCCESS"
            assert _trace_outcome(http, request_id) == "SUCCESS"

    def test_rag_empty(self, outcome_db, monkeypatch: pytest.MonkeyPatch) -> None:
        from backend.app.services.rag_observability_runtime import (
            get_observed_rag_service,
        )
        from backend.app.services.rag_service import RagService

        monkeypatch.setattr(
            root._default_orchestrator,
            "_rag",
            RagService(vector_search_service=_EmptyVectorSearch(), llm_client=None),
        )
        with TestClient(app) as http:
            status, response = _ask(http, _RAG_QUESTION, outcome_db)
            assert status == 200
            assert response.json()["metadata"]["outcome"] == "EMPTY"
            assert (
                _trace_outcome(
                    http, response.json()["metadata"]["request_id"]
                )
                == "EMPTY"
            )
        assert get_observed_rag_service() is not None     # 未改 RAG 装配

    def test_tool_success_and_failure(self, outcome_db, monkeypatch) -> None:
        with TestClient(app) as http:
            status, response = _ask(http, _TOOL_QUESTION, outcome_db)
            assert status == 200
            assert response.json()["metadata"]["outcome"] == "SUCCESS"
            assert (
                _trace_outcome(
                    http, response.json()["metadata"]["request_id"]
                )
                == "SUCCESS"
            )

            class _BoomHandler:
                async def __call__(self, arguments: Any):  # noqa: ANN401
                    raise RuntimeError(_SENTINEL)

            monkeypatch.setitem(
                root._TOOL_REGISTRY._handlers, "get_inventory", _BoomHandler()
            )
            status, failed = _ask(http, _TOOL_QUESTION, outcome_db)
            assert status == 200                          # Tool 失败仍 200
            assert failed.json()["metadata"]["tool_success"] is False
            assert failed.json()["metadata"]["outcome"] == "FAILED"
            assert (
                _trace_outcome(
                    http, failed.json()["metadata"]["request_id"]
                )
                == "FAILED"
            )

    def test_refusal(self, outcome_db, monkeypatch) -> None:
        _install_t2sql_route(
            monkeypatch,
            _client_with_handler(
                lambda request: httpx.Response(
                    200, json=_response_body(f"{_PREFIX}refusal-1", REFUSAL_MARKER)
                ),
                None,
            ),
        )
        with TestClient(app) as http:
            status, response = _ask(http, _T2SQL_QUESTION, outcome_db)
            assert status == 200
            metadata = response.json()["metadata"]
            assert metadata["refused"] is True
            assert metadata["outcome"] == "REFUSED"
            assert _trace_outcome(http, metadata["request_id"]) == "REFUSED"

    def test_t2sql_success_after_retry(self, outcome_db) -> None:
        with TestClient(app) as http:
            status, response = _ask(http, _T2SQL_QUESTION, outcome_db)
            assert status == 200
            metadata = response.json()["metadata"]
            assert metadata["row_count"] == 3
            assert metadata["outcome"] == "SUCCESS"
            assert _trace_outcome(http, metadata["request_id"]) == "SUCCESS"

    def test_failure_paths_persist_failed(self, outcome_db, monkeypatch) -> None:
        """T2SQL 重试耗尽 / RAG runtime 失败 / SQL 执行失败 / 能力禁用 → FAILED。"""
        from backend.app.projects.capabilities import ProjectCapabilities
        from backend.app.services.rag_observability_runtime import (
            get_observed_rag_service,
        )

        with TestClient(app) as http:
            # ① T2SQL 重试耗尽（always invalid）
            _install_t2sql_route(
                monkeypatch,
                _client_with_handler(
                    lambda request: httpx.Response(
                        200, json=_response_body(f"{_PREFIX}invalid-1", "DROP TABLE x")
                    ),
                    None,
                ),
            )
            before = _outcome_ids()
            status, _ = _ask(http, _T2SQL_QUESTION, outcome_db)
            assert status == 500
            assert set(_.json()) == {"detail"}
            exhausted = _new_outcome_ids(before)
            assert len(exhausted) == 1
            assert _trace_outcome(http, exhausted[0]) == "FAILED"
            outcome_db["request_ids"].extend(exhausted)

            # ② SQL 执行失败（真实服务重试后第二次成功 → executor 抛错）
            _install_t2sql_route(
                monkeypatch,
                _retry_t2sql_client(
                    provider_ids=(
                        f"{_PREFIX}llm-exec-1",
                        f"{_PREFIX}llm-exec-2",
                    ),
                    use_db_sink=True,
                ),
            )
            monkeypatch.setattr(
                root._default_orchestrator, "_sql_executor", _BoomExecutor()
            )
            before = _outcome_ids()
            status, _ = _ask(http, _T2SQL_QUESTION, outcome_db)
            assert status == 500
            exec_failed = _new_outcome_ids(before)
            assert len(exec_failed) == 1
            assert _trace_outcome(http, exec_failed[0]) == "FAILED"
            outcome_db["request_ids"].extend(exec_failed)

            # ③ 能力禁用（403）
            monkeypatch.setattr(
                root._default_orchestrator,
                "_capabilities",
                ProjectCapabilities(
                    knowledge_enabled=False,
                    text_to_sql_enabled=False,
                    tool_names=(),
                ),
            )
            before = _outcome_ids()
            status, _ = _ask(http, _RAG_QUESTION, outcome_db)
            assert status == 403
            denied = _new_outcome_ids(before)
            assert len(denied) == 1
            assert _trace_outcome(http, denied[0]) == "FAILED"
            outcome_db["request_ids"].extend(denied)
            monkeypatch.setattr(
                root._default_orchestrator, "_capabilities", None
            )

            # ④ RAG runtime 失败（500）
            from backend.app.services.rag_service import RagService

            monkeypatch.setattr(
                root._default_orchestrator,
                "_rag",
                RagService(
                    vector_search_service=_BoomVectorSearch(), llm_client=None
                ),
            )
            before = _outcome_ids()
            status, _ = _ask(http, _RAG_QUESTION, outcome_db)
            assert status == 500
            rag_failed = _new_outcome_ids(before)
            assert len(rag_failed) == 1
            assert _trace_outcome(http, rag_failed[0]) == "FAILED"
            outcome_db["request_ids"].extend(rag_failed)
        assert get_observed_rag_service() is not None

    def test_cross_request_isolation(self, outcome_db, monkeypatch) -> None:
        from backend.app.services.rag_service import RagService

        with TestClient(app) as http:
            status_a, rag_ok = _ask(http, _RAG_QUESTION, outcome_db)
            status_b, tool_ok = _ask(http, _TOOL_QUESTION, outcome_db)
            status_c, refused = _ask(http, _T2SQL_QUESTION, outcome_db)

            assert (status_a, status_b, status_c) == (200, 200, 200)
            id_a = rag_ok.json()["metadata"]["request_id"]
            id_b = tool_ok.json()["metadata"]["request_id"]
            id_c = refused.json()["metadata"]["request_id"]

            assert _trace_outcome(http, id_a) == "SUCCESS"
            assert _trace_outcome(http, id_b) == "SUCCESS"
            assert _trace_outcome(http, id_c) == "SUCCESS"

            # EMPTY + FAILED 并存（隔离）
            monkeypatch.setattr(
                root._default_orchestrator,
                "_rag",
                RagService(
                    vector_search_service=_EmptyVectorSearch(), llm_client=None
                ),
            )
            _, empty = _ask(http, _RAG_QUESTION, outcome_db)
            id_d = empty.json()["metadata"]["request_id"]

            monkeypatch.setattr(
                root._default_orchestrator,
                "_rag",
                RagService(
                    vector_search_service=_BoomVectorSearch(), llm_client=None
                ),
            )
            before = _outcome_ids()
            status_e, _ = _ask(http, _RAG_QUESTION, outcome_db)
            assert status_e == 500
            id_e = _new_outcome_ids(before)[0]
            outcome_db["request_ids"].append(id_e)

            assert _trace_outcome(http, id_a) == "SUCCESS"
            assert _trace_outcome(http, id_b) == "SUCCESS"
            assert _trace_outcome(http, id_c) == "SUCCESS"
            assert _trace_outcome(http, id_d) == "EMPTY"
            assert _trace_outcome(http, id_e) == "FAILED"
            assert len({id_a, id_b, id_c, id_d, id_e}) == 5

    def test_five_concurrent_requests_do_not_cross_outcomes(
        self, outcome_db
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor

        def _run(_index: int) -> tuple[str, str]:
            with TestClient(app) as http:
                response = http.post(
                    "/api/ai/chat", json={"question": _TOOL_QUESTION}
                )
                request_id = response.json()["metadata"]["request_id"]
                return request_id, _trace_outcome(http, request_id)

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(_run, range(5)))

        assert {outcome for _rid, outcome in results} == {"SUCCESS"}
        assert len({rid for rid, _outcome in results}) == 5
        outcome_db["request_ids"].extend(rid for rid, _o in results)


def _outcome_ids() -> set[str]:
    engine = get_engine()
    assert engine is not None
    with engine.connect() as conn:
        return set(
            conn.execute(
                text(
                    "SELECT assistant_request_id "
                    "FROM ai_ops.assistant_outcome_record"
                )
            ).scalars()
        )


def _new_outcome_ids(before: set[str]) -> list[str]:
    return sorted(_outcome_ids() - before)


# ============================================================
# 共享 Fake（RAG / Tool 外部边界）
# ============================================================

class _FakeVectorSearch:
    async def search(self, query: str, *, top_k: int):  # noqa: ANN201
        from backend.app.services.vector_search_service import VectorSearchResult

        return [
            VectorSearchResult(
                chunk_id=401,
                document_id=50,
                chunk_index=0,
                content="STEP64-CHUNK",
                distance=0.1,
                similarity=0.9,
                metadata={"heading": "step64"},
            )
        ]


class _EmptyVectorSearch:
    async def search(self, query: str, *, top_k: int):  # noqa: ANN201
        return []


class _BoomVectorSearch:
    async def search(self, query: str, *, top_k: int):  # noqa: ANN201
        raise RuntimeError(_SENTINEL)


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
    async def __call__(self, arguments: Any):  # noqa: ANN401
        return {"material_code": arguments.get("material_code"), "qty": 5}


class _BoomExecutor:
    async def execute(self, sql: str, **kwargs: Any) -> SQLExecutionResult:  # noqa: ANN003
        raise RuntimeError(_SENTINEL)


__all__ = [
    "TestPersistenceContractOffline",
    "TestTraceReadModelOffline",
    "TestOrchestratorOutcomeRecordingOffline",
    "TestOutcomeContractAndSecurity",
    "TestOutcomePersistenceDb",
    "TestOutcomeApiE2eDb",
]
