"""Assistant Trace → LLM Usage 关联：DB 集成测试（Phase 3.12 Step 36）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

真实链路（无 Mock Repository / 无真实 LLM）：

    assistant_trace_scope("assistant-A")               ← Orchestrator 绑定
        ↓
    DatabaseLLMAccountingSink → LLMUsagePersistenceService
        ↓ LLMUsageRepository.create()（INSERT ... ON CONFLICT DO NOTHING）
    ai_ops.llm_usage_record
        · assistant_request_id = 'assistant-A'（Assistant Trace）
        · request_id           = 'step36-…'（Provider 请求 ID）

数据：synthetic（provider / request_id 前缀 ``step36-``）；未读取真实数据。
清理（不用 TRUNCATE）：按 provider 前缀定向 ``DELETE`` 并断言归零。
"""
from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text

from backend.app.db import init_db as init_db_module
from backend.app.db.llm_usage_repository import LLMUsageRepository
from backend.app.db.models.llm_usage_record import LLM_USAGE_SCHEMA
from backend.app.db.session import get_engine, get_session_factory
from backend.app.llm.client import LLMUsage
from backend.app.llm.observability import LLMObservation
from backend.app.services.assistant_trace import assistant_trace_scope
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
    LLMUsagePersistenceService,
)

_TABLE = f"{LLM_USAGE_SCHEMA}.llm_usage_record"
_PREFIX = "step36-%"
_DELETE_SQL = text(f"DELETE FROM {_TABLE} WHERE provider LIKE :prefix")
_COUNT_SQL = text(f"SELECT COUNT(*) FROM {_TABLE} WHERE provider LIKE :prefix")
_TOTAL_SQL = text(f"SELECT COUNT(*) FROM {_TABLE}")
_ROW_SQL = text(
    f"SELECT assistant_request_id, request_id, provider, model, "
    f"prompt_tokens, completion_tokens, total_tokens FROM {_TABLE} "
    f"WHERE request_id = :request_id"
)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _observation(
    *, provider_request_id: str, provider: str = "step36-provider"
) -> LLMObservation:
    return LLMObservation(
        provider=provider,
        model="step36-model",
        latency_ms=1.0,
        success=True,
        finish_reason="stop",
        usage=LLMUsage(11, 22, 33),
        request_id=provider_request_id,
    )


@pytest.fixture()
def usage_db():
    engine = get_engine()
    if engine is None or get_session_factory() is None:
        pytest.skip("DATABASE_URL 未配置")
    init_db_module.init_db()            # 幂等：确保 assistant_request_id 列存在
    with engine.begin() as conn:
        conn.execute(_DELETE_SQL, {"prefix": _PREFIX})     # 清残留（本测试前缀）
        baseline = int(conn.execute(_TOTAL_SQL).scalar_one())
    yield engine
    with engine.begin() as conn:
        conn.execute(_DELETE_SQL, {"prefix": _PREFIX})
        assert int(conn.execute(_COUNT_SQL, {"prefix": _PREFIX}).scalar_one()) == 0
        assert int(conn.execute(_TOTAL_SQL).scalar_one()) == baseline   # 0 residue


def _row(engine, request_id: str):
    with engine.connect() as conn:
        return conn.execute(_ROW_SQL, {"request_id": request_id}).one()


@requires_db
class TestUsageTraceCorrelationDb:
    def test_bound_scope_persists_assistant_request_id(self, usage_db) -> None:
        service = LLMUsagePersistenceService(repository=LLMUsageRepository())

        with assistant_trace_scope("step36-assistant-A"):
            created = service.persist(
                _observation(provider_request_id="step36-P1")
            )

        assert created is not None
        row = _row(usage_db, "step36-P1")
        assert row.assistant_request_id == "step36-assistant-A"
        assert row.request_id == "step36-P1"             # Provider ID 未被覆盖
        assert row.assistant_request_id != row.request_id
        assert (row.provider, row.model) == ("step36-provider", "step36-model")
        assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (
            11, 22, 33,
        )

    def test_unbound_scope_persists_null(self, usage_db) -> None:
        service = LLMUsagePersistenceService(repository=LLMUsageRepository())

        service.persist(_observation(provider_request_id="step36-P2"))

        row = _row(usage_db, "step36-P2")
        assert row.assistant_request_id is None          # 旧链路 / 未绑定
        assert row.request_id == "step36-P2"

    def test_async_path_correlates_in_worker_thread(self, usage_db) -> None:
        """arecord（asyncio.to_thread）同样带 Assistant Trace。"""
        sink = DatabaseLLMAccountingSink(repository=LLMUsageRepository())

        with assistant_trace_scope("step36-assistant-B"):
            asyncio.run(sink.arecord(_observation(
                provider_request_id="step36-P3"
            )))

        row = _row(usage_db, "step36-P3")
        assert row.assistant_request_id == "step36-assistant-B"

    def test_idempotency_unaffected_by_correlation(self, usage_db) -> None:
        """幂等仍只由 request_id 决定；correlation 不参与、不覆盖。"""
        repository = LLMUsageRepository()
        service = LLMUsagePersistenceService(repository=repository)

        with assistant_trace_scope("step36-assistant-C"):
            first = service.persist(_observation(provider_request_id="step36-P4"))
            second = service.persist(_observation(provider_request_id="step36-P4"))

        assert first is not None and second is None      # DO NOTHING
        row = _row(usage_db, "step36-P4")
        assert row.assistant_request_id == "step36-assistant-C"

    def test_legacy_null_rows_stay_readable(self, usage_db) -> None:
        """历史 / 旧链路行（NULL correlation）读路径完全不受影响。"""
        service = LLMUsagePersistenceService(repository=LLMUsageRepository())
        service.persist(_observation(provider_request_id="step36-P5"))   # legacy

        repository = LLMUsageRepository()
        row = repository.get_by_request_id(request_id="step36-P5")
        rows = repository.list_records(
            request_id="step36-P5", limit=10, offset=0
        )

        assert row is not None and len(rows) == 1
        # 只读 Row 契约未变（correlation 不进入读模型）
        assert not hasattr(row, "assistant_request_id")
        assert row.request_id == "step36-P5"
        assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (
            11, 22, 33,
        )

    def test_two_assistant_requests_do_not_mix(self, usage_db) -> None:
        service = LLMUsagePersistenceService(repository=LLMUsageRepository())

        with assistant_trace_scope("step36-assistant-D"):
            service.persist(_observation(provider_request_id="step36-P6"))
        with assistant_trace_scope("step36-assistant-E"):
            service.persist(_observation(provider_request_id="step36-P7"))

        assert _row(usage_db, "step36-P6").assistant_request_id == (
            "step36-assistant-D"
        )
        assert _row(usage_db, "step36-P7").assistant_request_id == (
            "step36-assistant-E"
        )


__all__ = ["TestUsageTraceCorrelationDb"]
