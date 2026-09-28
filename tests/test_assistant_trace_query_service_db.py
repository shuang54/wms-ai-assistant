"""Assistant Trace Query Service：DB 集成测试（Phase 3.12 Step 38）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

数据源（Phase 3.12 Step 41：两者**均为 PostgreSQL**，且不合并）：

    AssistantTraceQueryService
        ├── LLMUsageQueryService            → ai_ops.llm_usage_record
        └── ToolExecutionPersistentQueryService → ai_ops.tool_execution_record

真实链路：

    写入 LLM usage（Step 36 correlation，真实 Repository → PostgreSQL）
        +
    写入 Tool record（真实 ToolExecutionRepository.create → PostgreSQL）
        ↓
    AssistantTraceQueryService.get_trace(A)（真实两个读边界）
        ↓
    AssistantTraceView

数据：synthetic（provider / request_id 前缀 ``step38-``）；未读取真实数据。
清理（不用 TRUNCATE）：按 provider / request_id 前缀定向 ``DELETE``（LLM Usage 与 Tool Execution 两张表）并断言归零。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from backend.app.db import init_db as init_db_module
from backend.app.db.llm_usage_repository import LLMUsageRepository
from backend.app.db.models.llm_usage_record import LLM_USAGE_SCHEMA
from backend.app.db.session import get_engine, get_session_factory
from backend.app.llm.client import LLMUsage
from backend.app.llm.observability import LLMObservation
from backend.app.services.assistant_trace import assistant_trace_scope
from backend.app.services.assistant_trace_query_service import (
    AssistantTraceQueryService,
)
from backend.app.services.llm_usage_persistence_service import (
    LLMUsagePersistenceService,
)
from backend.app.services.llm_usage_query_service import LLMUsageQueryService
from backend.app.db.tool_execution_repository import (
    ToolExecutionRepository,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.services.tool_execution_persistent_query_service import (
    ToolExecutionPersistentQueryService,
)

_TABLE = f"{LLM_USAGE_SCHEMA}.llm_usage_record"
_PREFIX = "step38-%"
_DELETE_SQL = text(f"DELETE FROM {_TABLE} WHERE provider LIKE :prefix")
_COUNT_SQL = text(f"SELECT COUNT(*) FROM {_TABLE} WHERE provider LIKE :prefix")
_TOTAL_SQL = text(f"SELECT COUNT(*) FROM {_TABLE}")
_BASE = datetime(2026, 9, 28, 17, 0, 0, tzinfo=timezone.utc)

#: Phase 3.12 Step 41：Tool Execution 表（本文件同时写入该表）
_TOOL_TABLE = "ai_ops.tool_execution_record"
_TOOL_DELETE_SQL = text(
    f"DELETE FROM {_TOOL_TABLE} WHERE request_id LIKE :prefix"
)
_TOOL_COUNT_SQL = text(
    f"SELECT COUNT(*) FROM {_TOOL_TABLE} WHERE request_id LIKE :prefix"
)
_TOOL_TOTAL_SQL = text(f"SELECT COUNT(*) FROM {_TOOL_TABLE}")


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _observation(provider_request_id: str) -> LLMObservation:
    return LLMObservation(
        provider="step38-provider",
        model="step38-model",
        latency_ms=1.0,
        success=True,
        finish_reason="stop",
        usage=LLMUsage(11, 22, 33),
        request_id=provider_request_id,
    )


def _persist_usage(assistant_request_id: str, provider_request_id: str) -> None:
    service = LLMUsagePersistenceService(repository=LLMUsageRepository())
    with assistant_trace_scope(assistant_request_id):
        service.persist(_observation(provider_request_id))


def _persist_tool(record: ToolExecutionRecord) -> None:
    """把 Tool 执行写入 PostgreSQL（真实 Repository；Step 41）。"""
    ToolExecutionRepository().create(record)


def _tool_record(*, request_id: str, round_: int) -> ToolExecutionRecord:
    started = _BASE + timedelta(seconds=round_)
    return ToolExecutionRecord(
        request_id=request_id,
        round=round_,
        tool_name="get_inventory",
        started_at=started,
        finished_at=started + timedelta(milliseconds=5),
        duration_ms=5.0,
        success=True,
        project_id="project-a",
        tool_call_id=None,
        error_type=None,
    )


@pytest.fixture()
def trace_env():
    engine = get_engine()
    if engine is None or get_session_factory() is None:
        pytest.skip("DATABASE_URL 未配置")
    init_db_module.init_db()
    with engine.begin() as conn:
        conn.execute(_DELETE_SQL, {"prefix": _PREFIX})
        baseline = int(conn.execute(_TOTAL_SQL).scalar_one())
        conn.execute(_TOOL_DELETE_SQL, {"prefix": _PREFIX})
        tool_baseline = int(conn.execute(_TOOL_TOTAL_SQL).scalar_one())
    yield engine
    with engine.begin() as conn:
        conn.execute(_DELETE_SQL, {"prefix": _PREFIX})
        assert int(conn.execute(_COUNT_SQL, {"prefix": _PREFIX}).scalar_one()) == 0
        assert int(conn.execute(_TOTAL_SQL).scalar_one()) == baseline
        conn.execute(_TOOL_DELETE_SQL, {"prefix": _PREFIX})
        assert int(conn.execute(_TOOL_COUNT_SQL, {"prefix": _PREFIX}).scalar_one()) == 0
        assert int(conn.execute(_TOOL_TOTAL_SQL).scalar_one()) == tool_baseline


def _trace_service() -> AssistantTraceQueryService:
    """两个数据源**均为 PostgreSQL**（Step 41）。"""
    return AssistantTraceQueryService(
        llm_usage_query_service=LLMUsageQueryService(
            repository=LLMUsageRepository()
        ),
        tool_observability_query_service=(
            ToolExecutionPersistentQueryService(
                repository=ToolExecutionRepository()
            )
        ),
    )


@requires_db
class TestAssistantTraceQueryDb:
    def test_llm_only_trace(self, trace_env) -> None:
        _engine = trace_env
        _persist_usage("step38-A", "step38-P1")
        _persist_usage("step38-A", "step38-P2")

        view = _trace_service().get_trace("step38-A")

        assert view.assistant_request_id == "step38-A"
        assert [v.request_id for v in view.llm_usage] == [
            "step38-P1", "step38-P2",
        ]
        assert [v.id for v in view.llm_usage] == sorted(
            v.id for v in view.llm_usage
        )
        assert view.tool_executions == ()             # Tool 侧 0（合法）

    def test_llm_and_tool_trace(self, trace_env) -> None:
        _engine = trace_env
        _persist_usage("step38-A", "step38-P3")
        _persist_tool(_tool_record(request_id="step38-A", round_=1))

        view = _trace_service().get_trace("step38-A")

        assert len(view.llm_usage) == 1
        assert len(view.tool_executions) == 1
        assert view.llm_usage[0].assistant_request_id == "step38-A"
        assert view.tool_executions[0].request_id == "step38-A"
        assert view.tool_executions[0].round == 1
        assert view.tool_executions[0].tool_call_id is None

    def test_tool_only_trace(self, trace_env) -> None:
        _engine = trace_env
        _persist_tool(_tool_record(request_id="step38-A", round_=1))

        view = _trace_service().get_trace("step38-A")

        assert view.llm_usage == ()
        assert len(view.tool_executions) == 1

    def test_empty_trace_is_not_an_error(self, trace_env) -> None:
        _engine = trace_env

        view = _trace_service().get_trace("step38-not-exist")

        assert view.assistant_request_id == "step38-not-exist"
        assert view.llm_usage == () and view.tool_executions == ()

    def test_traces_do_not_mix(self, trace_env) -> None:
        _engine = trace_env
        _persist_usage("step38-A", "step38-P4")
        _persist_usage("step38-B", "step38-P5")
        _persist_tool(_tool_record(request_id="step38-A", round_=1))
        _persist_tool(_tool_record(request_id="step38-B", round_=2))

        service = _trace_service()
        view_a = service.get_trace("step38-A")
        view_b = service.get_trace("step38-B")

        assert [v.request_id for v in view_a.llm_usage] == ["step38-P4"]
        assert [s.request_id for s in view_a.tool_executions] == ["step38-A"]
        assert [v.request_id for v in view_b.llm_usage] == ["step38-P5"]
        assert [s.request_id for s in view_b.tool_executions] == ["step38-B"]

    def test_historical_null_usage_not_attached(self, trace_env) -> None:
        """历史 / 旧链路 usage（NULL correlation）不会被粘到任何 Trace。"""
        _engine = trace_env
        LLMUsagePersistenceService(
            repository=LLMUsageRepository()
        ).persist(_observation("step38-legacy"))
        _persist_usage("step38-A", "step38-P6")

        view = _trace_service().get_trace("step38-A")

        assert [v.request_id for v in view.llm_usage] == ["step38-P6"]


__all__ = ["TestAssistantTraceQueryDb"]
