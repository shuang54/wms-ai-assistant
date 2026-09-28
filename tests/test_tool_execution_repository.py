"""Tool Execution Repository 单元测试（Phase 3.11 Step 27）。

非 DB：全部使用 Fake Session / Fake Result（0 网络 / 0 DB / 0 LLM）。
DB 集成见 ``tests/test_tool_execution_repository_db.py``（RUN_DB_TESTS=1）。

覆盖（§十九 + §十六）：

    1  Record → Row mapping            2  11 字段完整持久化
    3  None 字段 → NULL                4  datetime timezone 保留
    5  success=True                    6  success=False
    7  error_code                      8  error_type
    9  project_id=None                 10 tool_call_id=None
    11 duration_ms                     12 异常映射（SQLAlchemyError → RepositoryError）
    13 不返回 ORM object                14 不做 JSON serialization
    15 不做 aggregation
    +  事务边界（create 用 begin()；读不开写事务；不手动 commit/rollback）
    +  get_by_request_id（稳定排序 / 空结果 / 异常映射）
    +  安全：ORM 列 == 白名单 + 主键；无敏感列；schema = ai_ops
"""
from __future__ import annotations

import ast
import contextlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db import tool_execution_repository as repository_module
from backend.app.db.models.tool_execution_record import (
    TOOL_EXECUTION_PERSISTED_FIELDS,
    TOOL_EXECUTION_SCHEMA,
    TOOL_EXECUTION_TABLE,
    ToolExecutionRecordModel,
)
from backend.app.db.tool_execution_repository import (
    MAX_RECENT_LIMIT,
    TOOL_EXECUTION_READ_COLUMNS,
    ToolExecutionMetricsRow,
    ToolExecutionRecordRow,
    ToolExecutionRepository,
    ToolExecutionRepositoryError,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord

_MODULE = "backend/app/db/tool_execution_repository.py"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STARTED = datetime(2026, 9, 26, 10, 20, 30, 123456, tzinfo=timezone.utc)

_SENSITIVE_COLUMN_TOKENS = (
    "arguments", "result", "sql", "prompt", "response", "api_key",
    "password", "authorization", "token", "database_url", "dsn",
    "connection", "session", "traceback", "exception",
)


# ============================================================
# Fakes（0 网络 / 0 DB）
# ============================================================

class FakeRow:
    """SQLAlchemy Core Row 替身（只提供 ``_mapping``）。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self._mapping = data


class FakeResult:
    def __init__(self, rows: list[FakeRow]) -> None:
        self._rows = rows

    def one(self) -> FakeRow:
        assert len(self._rows) == 1
        return self._rows[0]

    def all(self) -> list[FakeRow]:
        return self._rows


class FakeSession:
    """记录执行语句 + 是否进入写事务。"""

    def __init__(
        self,
        rows: list[FakeRow],
        *,
        error: Exception | None = None,
    ) -> None:
        self._rows = rows
        self._error = error
        self.statements: list[Any] = []
        self.begun = 0
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def begin(self) -> Any:
        self.begun += 1
        return contextlib.nullcontext()

    def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        if self._error is not None:
            raise self._error
        return FakeResult(self._rows)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _factory(session: FakeSession) -> Any:
    return lambda: session


def _row_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": 1,
        "request_id": "req-1",
        "round": 1,
        "tool_name": "get_inventory",
        "started_at": _STARTED,
        "finished_at": _STARTED + timedelta(milliseconds=12.5),
        "duration_ms": 12.5,
        "success": True,
        "project_id": "project-a",
        "tool_call_id": None,
        "error_code": None,
        "error_type": None,
    }
    data.update(overrides)
    return data


def _record(**overrides: Any) -> ToolExecutionRecord:
    fields: dict[str, Any] = {
        "request_id": "req-1",
        "round": 1,
        "tool_name": "get_inventory",
        "started_at": _STARTED,
        "finished_at": _STARTED + timedelta(milliseconds=12.5),
        "duration_ms": 12.5,
        "success": True,
        "project_id": "project-a",
        "tool_call_id": None,
    }
    fields.update(overrides)
    return ToolExecutionRecord(**fields)


def _repository(**row_overrides: Any) -> tuple[ToolExecutionRepository, FakeSession]:
    session = FakeSession([FakeRow(_row_data(**row_overrides))])
    return ToolExecutionRepository(session_factory=_factory(session)), session


# ============================================================
# 1 ~ 11: Record → Row mapping / 字段完整性
# ============================================================

class TestCreateMapping:
    def test_1_record_to_row_mapping(self) -> None:
        repository, _session = _repository()

        row = repository.create(_record())

        assert isinstance(row, ToolExecutionRecordRow)
        assert row.request_id == "req-1"
        assert row.round == 1
        assert row.tool_name == "get_inventory"
        assert row.duration_ms == 12.5
        assert row.success is True
        assert row.project_id == "project-a"

    def test_2_all_eleven_fields_persisted(self) -> None:
        """写入值 = ToolExecutionRecord 的 11 个字段（不多不少）。"""
        repository, session = _repository()
        repository.create(_record())
        statement = session.statements[0]

        values = dict(statement.compile().params) if hasattr(
            statement, "compile"
        ) else {}
        # INSERT 的 values 从 statement 结构读取（跨 SQLAlchemy 版本友好）
        if not values:
            values = _extract_insert_values(statement)

        assert set(values) == set(TOOL_EXECUTION_PERSISTED_FIELDS)

    def test_3_none_fields_become_null(self) -> None:
        repository, session = _repository(
            project_id=None, tool_call_id=None, error_code=None,
            error_type=None,
        )

        row = repository.create(
            _record(project_id=None, tool_call_id=None)
        )

        assert row.project_id is None
        assert row.tool_call_id is None
        assert row.error_code is None
        assert row.error_type is None
        assert session.statements

    def test_4_timezone_preserved(self) -> None:
        repository, _session = _repository()

        row = repository.create(_record())

        assert row.started_at.tzinfo is not None
        assert row.started_at.utcoffset() == timedelta(0)
        assert row.finished_at.tzinfo is not None

    def test_5_success_true(self) -> None:
        repository, _session = _repository(success=True)

        assert repository.create(_record(success=True)).success is True

    def test_6_success_false(self) -> None:
        repository, session = _repository(success=False, error_type="ToolValidationError")

        row = repository.create(
            _record(success=False),
        )

        assert row.success is False
        assert row.error_type == "ToolValidationError"
        assert session.statements

    def test_7_error_code(self) -> None:
        repository, _session = _repository(
            success=False, error_code="invalid_argument"
        )

        assert repository.create(_record()).error_code == "invalid_argument"

    def test_8_error_type(self) -> None:
        repository, _session = _repository(
            success=False, error_type="ToolExecutionError"
        )

        assert repository.create(_record()).error_type == "ToolExecutionError"

    def test_9_project_id_none(self) -> None:
        repository, _session = _repository(project_id=None)

        assert repository.create(_record(project_id=None)).project_id is None

    def test_10_tool_call_id_none(self) -> None:
        repository, _session = _repository(tool_call_id=None)

        assert repository.create(_record()).tool_call_id is None

    def test_11_duration_ms(self) -> None:
        repository, _session = _repository(duration_ms=123.456)

        assert repository.create(_record(duration_ms=123.456)).duration_ms == 123.456


def _extract_insert_values(statement: Any) -> dict[str, Any]:
    """从 SQLAlchemy Insert 构造中取字段名（不依赖编译参数类型）。"""
    names: list[str] = []
    for entry in getattr(statement, "_values", None) or []:
        names.extend(getattr(entry, "keys", lambda: [])())
    select_names = [
        str(column.name)
        for column in getattr(statement, "returning", ()) or ()
    ]
    if names:
        return {name: None for name in names}
    return {name: None for name in select_names if name != "id"}


# ============================================================
# 12 ~ 15: 边界
# ============================================================

class TestRepositoryBoundaries:
    def test_12_exception_mapping_on_write(self) -> None:
        session = FakeSession([], error=SQLAlchemyError("insert failed"))
        repository = ToolExecutionRepository(session_factory=_factory(session))

        with pytest.raises(ToolExecutionRepositoryError):
            repository.create(_record())

    def test_12_exception_mapping_on_read(self) -> None:
        session = FakeSession([], error=SQLAlchemyError("select failed"))
        repository = ToolExecutionRepository(session_factory=_factory(session))

        with pytest.raises(ToolExecutionRepositoryError):
            repository.get_by_request_id("req-1")

    def test_12_db_not_configured(self, monkeypatch) -> None:
        monkeypatch.setattr(
            repository_module, "get_session_factory", lambda: None
        )

        with pytest.raises(ToolExecutionRepositoryError):
            ToolExecutionRepository().create(_record())

    def test_13_no_orm_object_returned(self) -> None:
        repository, _session = _repository()

        row = repository.create(_record())

        assert not isinstance(row, ToolExecutionRecordModel)
        assert type(row) is ToolExecutionRecordRow

    def test_14_no_json_serialization(self) -> None:
        with open(
            os.path.join(_REPO_ROOT, *_MODULE.split("/")), encoding="utf-8"
        ) as handle:
            tree = ast.parse(handle.read())
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert "json" not in imported
        identifiers = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in ("dumps", "loads", "asdict", "model_dump"):
            assert forbidden not in identifiers, forbidden

    def test_15_no_unwanted_capabilities(self) -> None:
        """公共 API 白名单（**只增不改语义**）：

            Step 27：create / get_by_request_id（+ 两个 build_*）
            Step 29：list_recent / build_recent_select（分页）
            Step 33：get_metrics / build_metrics_select（SQL 聚合）

        仍然**没有**：delete / cleanup / pagination cursor / filter DSL /
        Python 侧聚合 helper。
        """
        public = {
            name
            for name in dir(ToolExecutionRepository)
            if not name.startswith("_")
        }
        assert public == {
            "create", "get_by_request_id",
            "list_recent", "build_recent_select",
            "get_metrics", "build_metrics_select",      # Step 33：SQL 聚合
            "build_insert", "build_request_select",
        }, public
        for forbidden in ("delete", "cleanup", "truncate", "cursor", "search"):
            assert forbidden not in public, forbidden
        with open(
            os.path.join(_REPO_ROOT, *_MODULE.split("/")), encoding="utf-8"
        ) as handle:
            tree = ast.parse(handle.read())
        # Step 33：SQL 聚合（count / sum / avg / max）只允许出现在
        # ``build_metrics_select``（唯一聚合构造点）；其它方法仍无聚合语义。
        aggregate_attrs = {"count", "sum", "avg", "max", "group_by"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            used = {
                inner.attr
                for inner in ast.walk(node)
                if isinstance(inner, ast.Attribute)
            }
            if node.name == "build_metrics_select":
                assert used & aggregate_attrs, node.name  # 聚合确实在这里
                continue
            assert not (used & aggregate_attrs), (node.name, used & aggregate_attrs)


# ============================================================
# 事务边界（§十二）
# ============================================================

class TestTransactionBoundary:
    def test_create_uses_internal_transaction(self) -> None:
        repository, session = _repository()

        repository.create(_record())

        assert session.begun == 1
        assert session.commits == 0      # 由 begin() 上下文管理器提交
        assert session.rollbacks == 0

    def test_read_does_not_open_write_transaction(self) -> None:
        repository, session = _repository()

        repository.get_by_request_id("req-1")

        assert session.begun == 0

    def test_rollback_is_handled_by_context_manager(self) -> None:
        session = FakeSession([], error=SQLAlchemyError("boom"))
        repository = ToolExecutionRepository(session_factory=_factory(session))

        with pytest.raises(ToolExecutionRepositoryError):
            repository.create(_record())

        assert session.begun == 1
        assert session.rollbacks == 0    # 回滚由 begin() 上下文执行


# ============================================================
# get_by_request_id
# ============================================================

class TestGetByRequestId:
    def test_returns_rows_in_stable_order(self) -> None:
        session = FakeSession(
            [
                FakeRow(_row_data(id=1)),
                FakeRow(_row_data(id=2, round=2)),
            ]
        )
        repository = ToolExecutionRepository(session_factory=_factory(session))

        rows = repository.get_by_request_id("req-1")

        assert [row.id for row in rows] == [1, 2]
        compiled = str(
            repository.build_request_select("req-1").compile(
                compile_kwargs={"literal_binds": True}
            )
        )
        assert "ORDER BY" in compiled
        assert "tool_execution_record.id ASC".replace(
            "tool_execution_record.", "ai_ops.tool_execution_record."
        ) in compiled.replace("ai_ops.", "").replace(
            "tool_execution_record.id ASC", "tool_execution_record.id ASC"
        ) or "id ASC" in compiled

    def test_empty_result(self) -> None:
        session = FakeSession([])
        repository = ToolExecutionRepository(session_factory=_factory(session))

        assert repository.get_by_request_id("missing") == []

    def test_select_uses_explicit_columns(self) -> None:
        """不用 SELECT *：列集合 = 主键 + 11 字段。"""
        repository = ToolExecutionRepository(
            session_factory=_factory(FakeSession([]))
        )
        statement = repository.build_request_select("req-1")

        names = [column.name for column in statement.selected_columns]
        assert tuple(names) == TOOL_EXECUTION_READ_COLUMNS


# ============================================================
# 安全：列白名单 / schema / 无敏感列（§十六 / §十七）
# ============================================================

class TestPersistenceSecurityContract:
    def test_orm_columns_equal_whitelist_plus_pk(self) -> None:
        columns = {
            column.name
            for column in ToolExecutionRecordModel.__table__.columns
        }
        assert columns == {"id", *TOOL_EXECUTION_PERSISTED_FIELDS}

    def test_no_sensitive_columns(self) -> None:
        columns = {
            column.name.lower()
            for column in ToolExecutionRecordModel.__table__.columns
        }
        for token in _SENSITIVE_COLUMN_TOKENS:
            assert not any(token in name for name in columns), token

    def test_table_lives_in_ai_ops_schema(self) -> None:
        table = ToolExecutionRecordModel.__table__

        assert table.schema == TOOL_EXECUTION_SCHEMA
        assert table.name == TOOL_EXECUTION_TABLE
        assert TOOL_EXECUTION_SCHEMA == "ai_ops"

    def test_request_id_is_not_unique(self) -> None:
        """request_id 不是主键 / 不是唯一键（一次 request 可多条执行）。"""
        table = ToolExecutionRecordModel.__table__
        request_column = table.columns["request_id"]

        assert request_column.primary_key is False
        assert request_column.unique is None or request_column.unique is False
        unique_indexes = [
            index
            for index in table.indexes
            if index.unique
        ]
        assert unique_indexes == []

    def test_minimal_indexes(self) -> None:
        index_names = {
            index.name for index in ToolExecutionRecordModel.__table__.indexes
        }
        assert index_names == {
            "ix_tool_execution_record_request_id",
            "ix_tool_execution_record_started_at",
        }

    def test_model_is_registered_for_create_all(self) -> None:
        """init_db() 的 create_all 能发现本 Model（models 包已导出）。"""
        from backend.app.db.models import get_all_models

        assert ToolExecutionRecordModel in get_all_models()
        assert ToolExecutionRecordModel.__table__ is not None
        assert isinstance(select(ToolExecutionRecordModel.id), object)


# ============================================================
# Step 29：list_recent（Case A ~ F）
# ============================================================

class TestListRecent:
    @staticmethod
    def _repository(rows: list[FakeRow], **kwargs: Any):
        session = FakeSession(rows, **kwargs)
        return (
            ToolExecutionRepository(session_factory=_factory(session)),
            session,
        )

    def test_case_a_returns_rows_as_given_by_db(self) -> None:
        """Case A：DB 返回（已按 started_at DESC, id DESC 排序）→ 原序透传。"""
        repository, _session = self._repository([
            FakeRow(_row_data(id=3)),
            FakeRow(_row_data(id=2)),
        ])

        rows = repository.list_recent(limit=2)

        assert [row.id for row in rows] == [3, 2]

    def test_case_b_ordering_sql_has_started_at_and_id_desc(self) -> None:
        """Case B：同一 started_at → id 提供 deterministic tie-breaker。"""
        repository, _session = self._repository([])
        compiled = str(
            repository.build_recent_select(limit=10).compile(
                compile_kwargs={"literal_binds": True}
            )
        )

        assert "ORDER BY" in compiled
        assert "started_at DESC" in compiled
        assert "id DESC" in compiled
        assert "LIMIT 10" in compiled

    def test_case_c_limit_one(self) -> None:
        repository, session = self._repository([FakeRow(_row_data(id=9))])

        rows = repository.list_recent(limit=1)

        assert [row.id for row in rows] == [9]
        assert "LIMIT 1" in str(
            session.statements[0].compile(
                compile_kwargs={"literal_binds": True}
            )
        )

    def test_case_d_limit_at_max(self) -> None:
        repository, _session = self._repository([])

        assert repository.list_recent(limit=MAX_RECENT_LIMIT) == []

    def test_case_e_invalid_limit(self) -> None:
        repository, session = self._repository([])

        for bad in (0, -1, MAX_RECENT_LIMIT + 1, 999_999_999, "10", 1.5, True):
            with pytest.raises(ValueError):
                repository.list_recent(limit=bad)  # type: ignore[arg-type]
        assert session.statements == []          # 非法参数不触达数据库

    def test_case_f_empty_result_is_empty_list(self) -> None:
        repository, _session = self._repository([])

        assert repository.list_recent() == []

    def test_default_limit_is_100(self) -> None:
        repository, session = self._repository([])

        repository.list_recent()

        assert "LIMIT 100" in str(
            session.statements[0].compile(
                compile_kwargs={"literal_binds": True}
            )
        )

    def test_query_failure_is_not_empty_result(self) -> None:
        """DB failure ≠ empty database：失败必须抛 RepositoryError。"""
        repository, _session = self._repository(
            [], error=SQLAlchemyError("boom")
        )

        with pytest.raises(ToolExecutionRepositoryError):
            repository.list_recent()

    def test_read_does_not_open_write_transaction(self) -> None:
        repository, session = self._repository([])

        repository.list_recent()

        assert session.begun == 0
        assert session.commits == 0

    def test_explicit_columns_only(self) -> None:
        repository, _session = self._repository([])
        statement = repository.build_recent_select(limit=5)

        assert [
            column.name for column in statement.selected_columns
        ] == list(TOOL_EXECUTION_READ_COLUMNS)

    def test_no_orm_object_returned(self) -> None:
        repository, _session = self._repository([FakeRow(_row_data())])

        row = repository.list_recent(limit=1)[0]

        assert type(row) is ToolExecutionRecordRow

    # ---- Step 31：offset（分页） ----

    def test_offset_defaults_to_zero_in_sql(self) -> None:
        repository, session = self._repository([])

        repository.list_recent(limit=10)

        compiled = str(
            session.statements[0].compile(
                compile_kwargs={"literal_binds": True}
            )
        )
        assert "LIMIT 10" in compiled
        assert "OFFSET 0" in compiled

    def test_offset_appears_in_sql_and_keeps_ordering(self) -> None:
        repository, session = self._repository([])

        repository.list_recent(limit=2, offset=4)

        compiled = str(
            session.statements[0].compile(
                compile_kwargs={"literal_binds": True}
            )
        )
        assert "ORDER BY" in compiled
        assert "started_at DESC" in compiled
        assert "id DESC" in compiled
        assert "LIMIT 2" in compiled
        assert "OFFSET 4" in compiled

    def test_invalid_offset_rejected_without_touching_database(self) -> None:
        repository, session = self._repository([])

        for bad in (-1, -100, "0", 1.5, True):
            with pytest.raises(ValueError):
                repository.list_recent(offset=bad)  # type: ignore[arg-type]
        assert session.statements == []

    def test_large_offset_allowed(self) -> None:
        repository, _session = self._repository([])

        assert repository.list_recent(limit=1, offset=999_999_999) == []

    def test_offset_beyond_rows_compiles_to_offset_clause(self) -> None:
        """空页语义由数据库 OFFSET 决定（DB-gated 测试验证真实空页）。"""
        repository, session = self._repository([FakeRow(_row_data(id=1))])

        repository.list_recent(limit=100, offset=100)

        compiled = str(
            session.statements[0].compile(
                compile_kwargs={"literal_binds": True}
            )
        )
        assert "OFFSET 100" in compiled

    # ---- Step 32：精确过滤（project_id / tool_name / success） ----

    @staticmethod
    def _compiled(repository: ToolExecutionRepository, **kwargs: Any) -> str:
        return str(
            repository.build_recent_select(**kwargs).compile(
                compile_kwargs={"literal_binds": True}
            )
        )

    def test_no_filter_adds_no_where_clause(self) -> None:
        repository, _session = self._repository([])

        compiled = self._compiled(repository, limit=10)

        assert "WHERE" not in compiled.upper()

    def test_project_id_filter_is_exact_bound_parameter(self) -> None:
        repository, _session = self._repository([])

        statement = repository.build_recent_select(
            limit=10, project_id="project-a"
        )
        compiled = str(statement)
        params = statement.compile().params

        assert "project_id" in compiled.lower()
        assert params["project_id_1"] == "project-a"     # bound parameter
        assert "LIKE" not in compiled.upper()
        assert "ILIKE" not in compiled.upper()

    def test_tool_name_filter_is_exact_bound_parameter(self) -> None:
        repository, _session = self._repository([])

        statement = repository.build_recent_select(
            limit=10, tool_name="get_inventory"
        )

        assert statement.compile().params["tool_name_1"] == "get_inventory"

    def test_success_true_and_false_are_distinct(self) -> None:
        repository, _session = self._repository([])

        sql_true = self._compiled(repository, limit=10, success=True)
        sql_false = self._compiled(repository, limit=10, success=False)
        sql_none = self._compiled(repository, limit=10, success=None)

        assert "success = true" in sql_true
        assert "success = false" in sql_false
        assert "success = " not in sql_none              # None = 不过滤

    def test_three_filters_are_anded(self) -> None:
        repository, _session = self._repository([])

        statement = repository.build_recent_select(
            limit=5,
            offset=2,
            project_id="project-a",
            tool_name="get_inventory",
            success=True,
        )
        compiled = self._compiled(
            repository,
            limit=5,
            offset=2,
            project_id="project-a",
            tool_name="get_inventory",
            success=True,
        )
        params = statement.compile().params

        assert compiled.count("WHERE") == 1              # 单个 WHERE（AND 连接）
        assert " AND " in compiled
        assert params["project_id_1"] == "project-a"
        assert params["tool_name_1"] == "get_inventory"
        # 过滤 + 排序 + 分页同一条语句
        assert "ORDER BY" in compiled
        assert "LIMIT 5" in compiled
        assert "OFFSET 2" in compiled

    def test_filter_order_is_where_order_limit_offset(self) -> None:
        repository, _session = self._repository([])

        compiled = self._compiled(
            repository,
            limit=2,
            offset=4,
            project_id="project-a",
            tool_name="get_inventory",
            success=False,
        )
        upper = compiled.upper()

        assert upper.index("WHERE") < upper.index("ORDER BY")
        assert upper.index("ORDER BY") < upper.index("LIMIT")
        assert upper.index("LIMIT") < upper.index("OFFSET")
        assert "COUNT(" not in upper
        assert "SELECT *" not in upper

    def test_injection_like_input_stays_bound_parameter(self) -> None:
        """SQL 注入串必须作为普通参数（不进入 SQL 结构）。"""
        repository, _session = self._repository([])

        for value in ("' OR 1=1 --", "get_inventory' OR 1=1 --"):
            statement = repository.build_recent_select(
                limit=1, project_id=value, tool_name=value
            )
            params = statement.compile().params
            compiled = str(statement)

            assert params["project_id_1"] == value
            assert params["tool_name_1"] == value
            assert "OR 1=1" not in compiled              # 未拼进 SQL 文本
            assert compiled.count(";") == 0              # 无 multi-statement

    def test_invalid_filter_types_rejected(self) -> None:
        repository, session = self._repository([])

        with pytest.raises(ValueError):
            repository.list_recent(project_id=123)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            repository.list_recent(tool_name=123)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            repository.list_recent(success=1)  # type: ignore[arg-type]
        assert session.statements == []              # 未触达数据库

    def test_empty_string_is_a_literal_value(self) -> None:
        repository, _session = self._repository([])

        params = repository.build_recent_select(
            limit=1, project_id=""
        ).compile().params

        assert params["project_id_1"] == ""          # "" ≠ None

    # ---- Step 33：Metrics 聚合（SQL 内聚合；不逐行扫描） ----

    @staticmethod
    def _metrics_repository(
        row: dict[str, Any] | None = None, *, error: Exception | None = None
    ) -> tuple[ToolExecutionRepository, FakeSession]:
        data: dict[str, Any] = {
            "total_count": 5,
            "success_count": 3,
            "failure_count": 2,
            "total_duration_ms": 600.0,
            "average_duration_ms": 120.0,
            "max_duration_ms": 300.0,
        }
        data.update(row or {})
        session = FakeSession([FakeRow(data)], error=error)
        return (
            ToolExecutionRepository(session_factory=_factory(session)),
            session,
        )

    def test_metrics_sql_uses_aggregates_only(self) -> None:
        repository, _session = self._metrics_repository()

        sql = str(
            repository.build_metrics_select().compile(
                compile_kwargs={"literal_binds": True}
            )
        )

        assert "count(*) AS total_count" in sql
        assert "count(*) FILTER (WHERE" in sql
        assert "sum(" in sql and "avg(" in sql and "max(" in sql
        assert "GROUP BY" not in sql.upper()
        assert "ORDER BY" not in sql.upper()
        assert "LIMIT" not in sql.upper()
        assert "SELECT *" not in sql.upper()

    @staticmethod
    def _where_clause(statement: Any) -> str:
        """SQL 中 ``FROM ...`` 之后的片段（排除 FILTER (WHERE ...) 干扰）。"""
        sql = str(statement).upper()
        return sql.split("FROM ", 1)[1]

    def test_metrics_no_filter_generates_no_where(self) -> None:
        repository, _session = self._metrics_repository()

        tail = self._where_clause(repository.build_metrics_select())

        assert "WHERE" not in tail               # 无过滤 → 不生成 WHERE

    def test_metrics_filters_are_bound_and_anded(self) -> None:
        repository, _session = self._metrics_repository()

        statement = repository.build_metrics_select(
            project_id="project-a",
            tool_name="get_inventory",
            success=True,
        )
        sql = str(statement)
        tail = self._where_clause(statement)
        params = statement.compile().params

        assert tail.count("WHERE") == 1
        assert " AND " in sql
        assert params["project_id_1"] == "project-a"
        assert params["tool_name_1"] == "get_inventory"
        assert "LIKE" not in sql.upper() and "ILIKE" not in sql.upper()

    def test_metrics_success_false_is_a_filter(self) -> None:
        repository, _session = self._metrics_repository()

        sql_true = str(
            repository.build_metrics_select(
                success=True
            ).compile(compile_kwargs={"literal_binds": True})
        )
        sql_false = str(
            repository.build_metrics_select(
                success=False
            ).compile(compile_kwargs={"literal_binds": True})
        )
        sql_none = str(
            repository.build_metrics_select(
                success=None
            ).compile(compile_kwargs={"literal_binds": True})
        )

        assert "success = true" in sql_true
        assert "success = false" in sql_false
        assert "success = " not in sql_none

    def test_metrics_injection_input_stays_bound(self) -> None:
        repository, _session = self._metrics_repository()

        statement = repository.build_metrics_select(
            project_id="' OR 1=1 --", tool_name="get_inventory' OR 1=1 --"
        )
        sql = str(statement)
        params = statement.compile().params

        assert params["project_id_1"] == "' OR 1=1 --"
        assert params["tool_name_1"] == "get_inventory' OR 1=1 --"
        assert "OR 1=1" not in sql
        assert ";" not in sql

    def test_get_metrics_maps_row_without_orm(self) -> None:
        repository, session = self._metrics_repository()

        metrics = repository.get_metrics()

        assert type(metrics) is ToolExecutionMetricsRow
        assert not isinstance(metrics, ToolExecutionRecordModel)
        assert metrics.total_count == 5
        assert metrics.success_count == 3
        assert metrics.failure_count == 2
        assert metrics.total_duration_ms == 600.0
        assert metrics.average_duration_ms == 120.0
        assert metrics.max_duration_ms == 300.0
        assert session.commits == 0            # 读路径不写

    def test_get_metrics_empty_row_keeps_none(self) -> None:
        repository, _session = self._metrics_repository({
            "total_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "total_duration_ms": None,
            "average_duration_ms": None,
            "max_duration_ms": None,
        })

        metrics = repository.get_metrics()

        assert metrics.total_count == 0
        assert metrics.total_duration_ms is None
        assert metrics.average_duration_ms is None
        assert metrics.max_duration_ms is None

    def test_get_metrics_invalid_filters_rejected(self) -> None:
        repository, session = self._metrics_repository()

        with pytest.raises(ValueError):
            repository.get_metrics(project_id=123)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            repository.get_metrics(tool_name=123)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            repository.get_metrics(success=1)  # type: ignore[arg-type]
        assert session.statements == []

    def test_get_metrics_failure_is_not_zero_metrics(self) -> None:
        """DB failure ≠ empty database（必须抛错，不返回 0 指标）。"""
        repository, _session = self._metrics_repository(
            error=SQLAlchemyError("boom")
        )

        with pytest.raises(ToolExecutionRepositoryError):
            repository.get_metrics()

    def test_get_metrics_reads_without_write_transaction(self) -> None:
        repository, session = self._metrics_repository()

        repository.get_metrics()

        assert session.begun == 0
        assert session.commits == 0 and session.rollbacks == 0

    def test_no_count_query_in_recent_select(self) -> None:
        """无 total_count / COUNT(*)（属后续 Metrics 范围）。"""
        repository, session = self._repository([])

        repository.list_recent(limit=5, offset=5)
        compiled = str(
            session.statements[0].compile(
                compile_kwargs={"literal_binds": True}
            )
        ).upper()

        assert "COUNT(" not in compiled
