"""Assistant Trace LLM Usage 只读边界：DB 集成测试（Phase 3.12 Step 37）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

真实链路（无 Mock Repository）：

    assistant_request_id = A
        ↓ LLMUsageQueryService.list_by_assistant_request_id("A")
        ↓ LLMUsageRepository.build_trace_select()（精确匹配 + bound parameter）
    ai_ops.llm_usage_record
        ↓ [LLMUsageTraceRecordView]

数据：synthetic（provider 前缀 ``step37-``）；未读取真实数据。
清理（不用 TRUNCATE）：按 provider 前缀定向 ``DELETE`` 并断言归零。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from backend.app.db import init_db as init_db_module
from backend.app.db.llm_usage_repository import (
    LLM_USAGE_READ_COLUMNS,
    LLMUsageRepository,
)
from backend.app.db.models.llm_usage_record import LLM_USAGE_SCHEMA
from backend.app.db.session import get_engine, get_session_factory
from backend.app.llm.client import LLMUsage
from backend.app.llm.observability import LLMObservation
from backend.app.services.assistant_trace import assistant_trace_scope
from backend.app.services.llm_usage_persistence_service import (
    LLMUsagePersistenceService,
)
from backend.app.services.llm_usage_query_service import (
    LLM_USAGE_TRACE_VIEW_FIELDS,
    LLMUsageQueryService,
    LLMUsageTraceRecordView,
)

_TABLE = f"{LLM_USAGE_SCHEMA}.llm_usage_record"
_PREFIX = "step37-%"
_DELETE_SQL = text(f"DELETE FROM {_TABLE} WHERE provider LIKE :prefix")
_COUNT_SQL = text(f"SELECT COUNT(*) FROM {_TABLE} WHERE provider LIKE :prefix")
_TOTAL_SQL = text(f"SELECT COUNT(*) FROM {_TABLE}")
_RAW_INSERT_SQL = text(
    f"INSERT INTO {_TABLE} "
    "(request_id, assistant_request_id, provider, model, "
    " prompt_tokens, completion_tokens, total_tokens, created_at) "
    "VALUES (:request_id, :assistant_request_id, :provider, :model, "
    " :prompt_tokens, :completion_tokens, :total_tokens, :created_at)"
)

_BASE = datetime(2026, 9, 28, 15, 0, 0, tzinfo=timezone.utc)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _observation(provider_request_id: str) -> LLMObservation:
    return LLMObservation(
        provider="step37-provider",
        model="step37-model",
        latency_ms=1.0,
        success=True,
        finish_reason="stop",
        usage=LLMUsage(11, 22, 33),
        request_id=provider_request_id,
    )


def _persist(assistant_request_id: str | None, provider_request_id: str) -> None:
    """写入一行（assistant_request_id=None → 走未绑定 Scope 的旧链路形态）。"""
    service = LLMUsagePersistenceService(repository=LLMUsageRepository())
    if assistant_request_id is None:
        service.persist(_observation(provider_request_id))
        return
    with assistant_trace_scope(assistant_request_id):
        service.persist(_observation(provider_request_id))


def _raw_insert(
    *, provider_request_id: str, assistant_request_id: str, created_at: datetime
) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            _RAW_INSERT_SQL,
            {
                "request_id": provider_request_id,
                "assistant_request_id": assistant_request_id,
                "provider": "step37-provider",
                "model": "step37-model",
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "total_tokens": 3,
                "created_at": created_at,
            },
        )


@pytest.fixture()
def usage_db():
    engine = get_engine()
    if engine is None or get_session_factory() is None:
        pytest.skip("DATABASE_URL 未配置")
    init_db_module.init_db()
    with engine.begin() as conn:
        conn.execute(_DELETE_SQL, {"prefix": _PREFIX})
        baseline = int(conn.execute(_TOTAL_SQL).scalar_one())
    yield engine
    with engine.begin() as conn:
        conn.execute(_DELETE_SQL, {"prefix": _PREFIX})
        assert int(conn.execute(_COUNT_SQL, {"prefix": _PREFIX}).scalar_one()) == 0
        assert int(conn.execute(_TOTAL_SQL).scalar_one()) == baseline


def _service() -> LLMUsageQueryService:
    return LLMUsageQueryService(repository=LLMUsageRepository())


@requires_db
class TestTraceReadDb:
    def test_01_exact_match_only(self, usage_db) -> None:
        """A → Usage 1、Usage 2；B → Usage 3；NULL → Usage 4。"""
        _persist("A", "step37-P1")
        _persist("A", "step37-P2")
        _persist("B", "step37-P3")
        _persist(None, "step37-P4")          # 历史 / 旧链路（NULL）

        views = _service().list_by_assistant_request_id("A")

        assert [v.request_id for v in views] == ["step37-P1", "step37-P2"]
        assert all(v.assistant_request_id == "A" for v in views)
        assert {type(v) for v in views} == {LLMUsageTraceRecordView}

    def test_02_unknown_id_returns_empty(self, usage_db) -> None:
        _persist("A", "step37-P5")

        assert _service().list_by_assistant_request_id("not-exist") == []

    def test_03_null_rows_never_match(self, usage_db) -> None:
        _persist(None, "step37-P6")
        _persist("A", "step37-P7")

        views = _service().list_by_assistant_request_id("A")

        assert [v.request_id for v in views] == ["step37-P7"]
        assert "step37-P6" not in [v.request_id for v in views]

    def test_04_deterministic_order_created_at_then_id(self, usage_db) -> None:
        """created_at ASC, id ASC（即使写入顺序与时间顺序不同）。"""
        _raw_insert(
            provider_request_id="step37-P10",
            assistant_request_id="order-A",
            created_at=_BASE + timedelta(seconds=2),
        )
        _raw_insert(
            provider_request_id="step37-P11",
            assistant_request_id="order-A",
            created_at=_BASE + timedelta(seconds=1),
        )
        _raw_insert(
            provider_request_id="step37-P12",
            assistant_request_id="order-A",
            created_at=_BASE + timedelta(seconds=2),
        )

        views = _service().list_by_assistant_request_id("order-A")

        # t1 的行最先；两条 t2 的行按 id ASC（先写入的 id 更小）
        assert [v.request_id for v in views] == [
            "step37-P11", "step37-P10", "step37-P12",
        ]
        assert [v.created_at for v in views] == sorted(
            v.created_at for v in views
        )
        # 同 created_at 的两条按 id ASC（写入顺序）
        assert [views[1].id, views[2].id] == sorted(
            [views[1].id, views[2].id]
        )

    def test_05_injection_strings_are_literal(self, usage_db) -> None:
        _persist("A", "step37-P8")

        service = _service()
        for payload in ("'A' OR '1'='1", "A'; DROP TABLE ai_ops.llm_usage_record; --"):
            assert service.list_by_assistant_request_id(payload) == []

        # 表未被破坏：原有数据仍在
        assert [v.request_id for v in service.list_by_assistant_request_id("A")] == [
            "step37-P8"
        ]

    def test_06_historical_null_rows_still_readable(self, usage_db) -> None:
        _persist(None, "step37-P9")
        repository = LLMUsageRepository()

        # analytics 读路径（8 列 Row）不受新列影响
        row = repository.get_by_request_id(request_id="step37-P9")
        assert row is not None
        assert not hasattr(row, "assistant_request_id")
        assert LLM_USAGE_READ_COLUMNS == (
            "id", "request_id", "provider", "model", "prompt_tokens",
            "completion_tokens", "total_tokens", "created_at",
        )
        # Trace 读路径对该 Trace ID 返回空（NULL 不匹配），且不报错
        assert _service().list_by_assistant_request_id("step37-legacy") == []

    def test_07_multiple_provider_requests_preserved(self, usage_db) -> None:
        for index in (1, 2, 3):
            _persist("multi-A", f"step37-M{index}")

        views = _service().list_by_assistant_request_id("multi-A")

        assert [v.request_id for v in views] == [
            "step37-M1", "step37-M2", "step37-M3",
        ]
        assert all(v.assistant_request_id == "multi-A" for v in views)
        assert set(LLM_USAGE_TRACE_VIEW_FIELDS) == {
            "id", "assistant_request_id", "request_id", "provider", "model",
            "prompt_tokens", "completion_tokens", "total_tokens", "created_at",
        }
        assert (views[0].prompt_tokens, views[0].completion_tokens,
                views[0].total_tokens) == (11, 22, 33)

    def test_08_two_assistant_requests_do_not_mix(self, usage_db) -> None:
        _persist("A", "step37-N1")
        _persist("B", "step37-N2")

        service = _service()

        assert [v.request_id for v in service.list_by_assistant_request_id("A")] == [
            "step37-N1"
        ]
        assert [v.request_id for v in service.list_by_assistant_request_id("B")] == [
            "step37-N2"
        ]


__all__ = ["TestTraceReadDb"]
