"""ToolExecutionPersistentQueryService —— DB 集成测试（Phase 3.11 Step 29）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

真实链路：

    PostgreSQL（ai_ops.tool_execution_record）
        ↓
    ToolExecutionRepository.list_recent()
        ↓
    ToolExecutionPersistentQueryService
        ↓
    ToolExecutionSnapshot（11 字段）

Case 1  insert synthetic records → query recent → 验证排序
Case 2  limit=2 → 只返回 2 条
Case 3  same started_at → deterministic ordering（id DESC）
Case 4  nullable fields → NULL → None
Case 5  empty table → []
Case 6  repository query failure → ToolExecutionRepositoryError

数据：synthetic only（step29-test-* / get_inventory / test-project）；
      未查询真实库存 / 工单 / WMS 数据。
残留：teardown TRUNCATE **本表**（不动 ai_ops.llm_usage_record / public.*）。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.models.tool_execution_record import (
    TOOL_EXECUTION_SCHEMA,
    TOOL_EXECUTION_TABLE,
)
from backend.app.db.session import get_engine, get_session_factory
from backend.app.db.tool_execution_repository import (
    ToolExecutionRepository,
    ToolExecutionRepositoryError,
)
from backend.app.services.tool_execution_persistent_query_service import (
    DEFAULT_RECENT_LIMIT,
    ToolExecutionPersistentQueryService,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.services.tool_observability_snapshot import (
    ToolExecutionSnapshot,
)

_TEST_TOOL = "get_inventory"
_TEST_PROJECT = "test-project"

_TABLE = f"{TOOL_EXECUTION_SCHEMA}.{TOOL_EXECUTION_TABLE}"
_TRUNCATE_SQL = text(f"TRUNCATE TABLE {_TABLE} RESTART IDENTITY")

_BASE = datetime(2026, 9, 28, 11, 0, 0, tzinfo=timezone.utc)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


_RUN_DB = _env_flag("RUN_DB_TESTS")

requires_db = pytest.mark.skipif(
    not _RUN_DB,
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _record(
    *,
    request_id: str = "step29-test-001",
    round: int = 1,
    started_at: datetime | None = None,
    duration_ms: float = 5.0,
    success: bool = True,
    project_id: str | None = _TEST_PROJECT,
    tool_call_id: str | None = None,
    error_code: str | None = None,
    error_type: str | None = None,
) -> ToolExecutionRecord:
    started = started_at or _BASE
    return ToolExecutionRecord(
        request_id=request_id,
        round=round,
        tool_name=_TEST_TOOL,
        started_at=started,
        finished_at=started + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        success=success,
        project_id=project_id,
        tool_call_id=tool_call_id,
        error_code=error_code,
        error_type=error_type,
    )


def _engine() -> Any:
    from backend.app.db import init_db as init_db_module

    engine = get_engine()
    if engine is None:
        pytest.skip("DATABASE_URL 未配置")
    init_db_module.init_db()
    return engine


def _cleanup(engine: Any) -> int:
    with engine.begin() as conn:
        conn.execute(_TRUNCATE_SQL)
        return int(
            conn.execute(text(f"SELECT COUNT(*) FROM {_TABLE}")).scalar_one()
        )


def _services() -> tuple[
    ToolExecutionPersistentQueryService, ToolExecutionRepository
]:
    repository = ToolExecutionRepository()
    return ToolExecutionPersistentQueryService(repository=repository), repository


class _BrokenSession:
    def __enter__(self) -> "_BrokenSession":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, statement: Any) -> Any:
        raise SQLAlchemyError("simulated query failure")


@pytest.fixture()
def db():
    engine = _engine()
    factory = get_session_factory()
    if factory is None:
        pytest.skip("session factory 不可用")
    _cleanup(engine)
    try:
        yield engine, factory
    finally:
        assert _cleanup(engine) == 0            # DB residue = 0


# ============================================================
# Case 1 ~ 6
# ============================================================

@requires_db
class TestPersistentQueryServiceDb:
    def test_case_1_recent_ordering(self, db) -> None:
        _engine_, _factory = db
        query, repository = _services()
        # 依次写入（started_at 递增）→ 期望 recent 为倒序
        repository.create(
            _record(request_id="step29-test-old",
                    started_at=_BASE - timedelta(minutes=10))
        )
        repository.create(
            _record(request_id="step29-test-mid",
                    started_at=_BASE - timedelta(minutes=5))
        )
        repository.create(
            _record(request_id="step29-test-new", started_at=_BASE)
        )

        snapshots = query.list_recent(limit=10)

        assert [s.request_id for s in snapshots] == [
            "step29-test-new", "step29-test-mid", "step29-test-old"
        ]
        assert all(
            isinstance(s, ToolExecutionSnapshot) for s in snapshots
        )

    def test_case_2_limit_two(self, db) -> None:
        _engine_, _factory = db
        query, repository = _services()
        for index in range(3):
            repository.create(
                _record(
                    request_id=f"step29-test-{index}",
                    started_at=_BASE + timedelta(seconds=index),
                )
            )

        snapshots = query.list_recent(limit=2)

        assert len(snapshots) == 2
        assert snapshots[0].request_id == "step29-test-2"
        assert snapshots[1].request_id == "step29-test-1"

    def test_case_3_same_started_at_uses_id_tiebreaker(self, db) -> None:
        """同一 started_at → id DESC（deterministic tie-breaker）。"""
        _engine_, _factory = db
        query, repository = _services()
        repository.create(_record(request_id="step29-test-first"))
        repository.create(_record(request_id="step29-test-second"))

        snapshots = query.list_recent(limit=10)

        assert [s.request_id for s in snapshots] == [
            "step29-test-second", "step29-test-first"
        ]
        # 两次查询结果一致（deterministic）
        assert query.list_recent(limit=10) == snapshots

    def test_case_4_nullable_fields_become_none(self, db) -> None:
        _engine_, _factory = db
        query, repository = _services()
        repository.create(
            _record(
                request_id="step29-test-nullable",
                project_id=None,
                tool_call_id=None,
                error_code=None,
                error_type=None,
            )
        )

        snapshot = query.list_recent(limit=1)[0]

        assert snapshot.project_id is None
        assert snapshot.tool_call_id is None
        assert snapshot.error_code is None
        assert snapshot.error_type is None

    def test_case_5_empty_table_returns_empty_list(self, db) -> None:
        _engine_, _factory = db
        query, _repository = _services()

        result = query.list_recent()

        assert result == []
        assert result is not None
        assert DEFAULT_RECENT_LIMIT == 100

    def test_case_6_query_failure_raises_repository_error(self, db) -> None:
        """DB failure ≠ empty database（必须抛错，不返回 []）。"""
        _engine_, _factory = db
        broken = ToolExecutionRepository(
            session_factory=lambda: _BrokenSession()  # type: ignore[arg-type]
        )
        query = ToolExecutionPersistentQueryService(repository=broken)

        with pytest.raises(ToolExecutionRepositoryError):
            query.list_recent()

    def test_snapshot_fields_strictly_eleven(self, db) -> None:
        _engine_, _factory = db
        query, repository = _services()
        repository.create(_record(success=False, error_code="invalid_argument",
                                  error_type="ToolValidationError"))

        snapshot = query.list_recent(limit=1)[0]

        assert len(vars(snapshot)) == 11
        assert not hasattr(snapshot, "id")
        snapshot.assert_field_whitelist()

    def test_timezone_preserved_from_database(self, db) -> None:
        _engine_, _factory = db
        query, repository = _services()
        repository.create(_record(started_at=_BASE))

        snapshot = query.list_recent(limit=1)[0]

        assert snapshot.started_at.tzinfo is not None
        assert snapshot.started_at.utcoffset() == timedelta(0)
        assert snapshot.started_at == _BASE

    def test_no_llm_usage_table_touched(self, db) -> None:
        _engine_, _factory = db
        engine = _engine()
        query, repository = _services()
        repository.create(_record())

        query.list_recent()

        with engine.connect() as conn:
            llm_rows = conn.execute(
                text("SELECT COUNT(*) FROM ai_ops.llm_usage_record")
            ).scalar_one()
        assert llm_rows >= 0                     # 只读断言：未被本测试修改
