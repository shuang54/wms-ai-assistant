"""Tool Execution Repository —— DB 集成测试（Phase 3.11 Step 27）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

    Case 1  insert one record               → 行存在
    Case 2  同一 request_id 多条            → 全部存在（request_id ≠ 主键）
    Case 3  success record
    Case 4  failure record
    Case 5  nullable fields → NULL
    Case 6  timezone 保留（TIMESTAMP WITH TIME ZONE）
    Case 7  get_by_request_id
    Case 8  rollback（写入失败 → 不留半条记录）

数据（§二十一）：**只使用 synthetic ToolExecutionRecord**
    request_id = "test-request-001" / tool_name = "get_inventory" /
    project_id = "test-project"
不读取真实库存 / 工单 / WMS 数据；不调用 get_inventory / get_work_order。

残留（§二十二）：每个用例结束清理本表测试数据（TRUNCATE 本表，
不清整个 ai_ops），并断言 DB residue = 0。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.models.tool_execution_record import (
    TOOL_EXECUTION_SCHEMA,
    TOOL_EXECUTION_TABLE,
    ToolExecutionRecordModel,
)
from backend.app.db.session import get_engine, get_session_factory
from backend.app.db.tool_execution_repository import (
    ToolExecutionRecordRow,
    ToolExecutionRepository,
    ToolExecutionRepositoryError,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord

_TEST_REQUEST = "test-request-001"
_TEST_REQUEST_MULTI = "test-request-multi"
_TEST_TOOL = "get_inventory"
_TEST_PROJECT = "test-project"

_TABLE = f"{TOOL_EXECUTION_SCHEMA}.{TOOL_EXECUTION_TABLE}"
_TRUNCATE_SQL = text(f"TRUNCATE TABLE {_TABLE} RESTART IDENTITY")


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


_RUN_DB = _env_flag("RUN_DB_TESTS")

requires_db = pytest.mark.skipif(
    not _RUN_DB,
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)

_STARTED = datetime(2026, 9, 26, 10, 20, 30, 123456, tzinfo=timezone.utc)


def _record(**overrides: Any) -> ToolExecutionRecord:
    fields: dict[str, Any] = {
        "request_id": _TEST_REQUEST,
        "round": 1,
        "tool_name": _TEST_TOOL,
        "started_at": _STARTED,
        "finished_at": _STARTED + timedelta(milliseconds=12.5),
        "duration_ms": 12.5,
        "success": True,
        "project_id": _TEST_PROJECT,
        "tool_call_id": None,
    }
    fields.update(overrides)
    return ToolExecutionRecord(**fields)


def _engine() -> Any:
    engine = get_engine()
    if engine is None:
        pytest.skip("DATABASE_URL 未配置")
    return engine


def _ensure_schema() -> Any:
    """确保 ai_ops schema 与表存在（沿用项目 init_db 的方式）。"""
    from backend.app.db import init_db as init_db_module

    engine = _engine()
    init_db_module.init_db()
    return engine


def _cleanup(engine: Any) -> int:
    with engine.begin() as conn:
        conn.execute(_TRUNCATE_SQL)
        return int(
            conn.execute(text(f"SELECT COUNT(*) FROM {_TABLE}")).scalar_one()
        )


def _count(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(ToolExecutionRecordModel)
        )
        or 0
    )


@pytest.fixture()
def db():
    engine = _ensure_schema()
    factory = get_session_factory()
    if factory is None:
        pytest.skip("session factory 不可用")
    repository = ToolExecutionRepository(session_factory=factory)
    try:
        yield repository, factory
    finally:
        _cleanup(engine)


@requires_db
class TestToolExecutionRepositoryDb:
    def test_case_1_insert_one_record(self, db) -> None:
        repository, factory = db

        row = repository.create(_record())

        assert isinstance(row, ToolExecutionRecordRow)
        assert row.id > 0
        with factory() as session:
            assert _count(session) == 1

    def test_case_2_same_request_id_multiple_rows(self, db) -> None:
        """request_id **不是**主键：同一 request 可有多条执行。"""
        repository, factory = db

        first = repository.create(_record(request_id=_TEST_REQUEST_MULTI))
        second = repository.create(
            _record(request_id=_TEST_REQUEST_MULTI, round=2)
        )

        assert first.id != second.id
        with factory() as session:
            assert _count(session) == 2

    def test_case_3_success_record(self, db) -> None:
        repository, factory = db

        repository.create(_record(success=True))

        with factory() as session:
            stored = session.scalar(
                select(ToolExecutionRecordModel.success)
            )
        assert stored is True

    def test_case_4_failure_record(self, db) -> None:
        repository, factory = db

        repository.create(
            _record(success=False, error_type="ToolValidationError")
        )

        with factory() as session:
            row = session.execute(
                select(
                    ToolExecutionRecordModel.success,
                    ToolExecutionRecordModel.error_type,
                )
            ).one()
        assert row.success is False
        assert row.error_type == "ToolValidationError"

    def test_case_5_nullable_fields_are_null(self, db) -> None:
        repository, factory = db

        repository.create(
            _record(project_id=None, tool_call_id=None)
        )

        with factory() as session:
            row = session.execute(
                select(
                    ToolExecutionRecordModel.project_id,
                    ToolExecutionRecordModel.tool_call_id,
                    ToolExecutionRecordModel.error_code,
                    ToolExecutionRecordModel.error_type,
                )
            ).one()
        assert row.project_id is None
        assert row.tool_call_id is None
        assert row.error_code is None
        assert row.error_type is None

    def test_case_6_timezone_preserved(self, db) -> None:
        repository, factory = db

        repository.create(_record())

        with factory() as session:
            stored = session.scalar(
                select(ToolExecutionRecordModel.started_at)
            )
        assert stored.tzinfo is not None
        assert stored.utcoffset() == timedelta(0)
        assert stored == _STARTED

    def test_case_7_get_by_request_id(self, db) -> None:
        repository, _factory = db

        repository.create(_record(request_id=_TEST_REQUEST_MULTI, round=1))
        repository.create(_record(request_id=_TEST_REQUEST_MULTI, round=2))
        repository.create(_record(request_id="test-request-other"))

        rows = repository.get_by_request_id(_TEST_REQUEST_MULTI)

        assert len(rows) == 2
        assert [row.round for row in rows] == [1, 2]
        assert {row.tool_name for row in rows} == {_TEST_TOOL}
        assert repository.get_by_request_id("test-request-none") == []

    def test_case_8_rollback_leaves_no_partial_row(self, db) -> None:
        """写入失败 → 事务回滚 → 表中不留任何行。"""
        repository, factory = db
        engine = _engine()

        class _BrokenRecord:
            """触发 SQLAlchemyError 的非法输入（非 Record 字段类型）。"""

            request_id = _TEST_REQUEST
            round = "not-an-int"          # 类型非法 → DB 拒绝
            tool_name = _TEST_TOOL
            started_at = object()          # 非法 datetime
            finished_at = object()
            duration_ms = object()
            success = object()
            project_id = None
            tool_call_id = None
            error_code = None
            error_type = None

        with pytest.raises(ToolExecutionRepositoryError):
            repository.create(_BrokenRecord())  # type: ignore[arg-type]

        with factory() as session:
            assert _count(session) == 0
        assert _cleanup(engine) == 0

    def test_table_is_in_ai_ops_not_public(self, db) -> None:
        engine = _engine()
        with engine.connect() as conn:
            in_public = conn.execute(
                text(
                    "SELECT EXISTS("
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' "
                    "AND table_name=:table)"
                ),
                {"table": TOOL_EXECUTION_TABLE},
            ).scalar_one()
            in_ai_ops = conn.execute(
                text(
                    "SELECT EXISTS("
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='ai_ops' "
                    "AND table_name=:table)"
                ),
                {"table": TOOL_EXECUTION_TABLE},
            ).scalar_one()
        assert in_public is False
        assert in_ai_ops is True

    def test_no_sensitive_columns_in_database(self, db) -> None:
        engine = _engine()
        with engine.connect() as conn:
            columns = [
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema=:schema AND table_name=:table"
                    ),
                    {"schema": TOOL_EXECUTION_SCHEMA,
                     "table": TOOL_EXECUTION_TABLE},
                ).all()
            ]
        forbidden_tokens = (
            "arguments", "result", "sql", "prompt", "response", "api_key",
            "password", "authorization", "token", "database_url", "dsn",
            "traceback", "exception", "message",
        )
        for token in forbidden_tokens:
            assert not any(token in column for column in columns), token
