"""LLM Usage Query Read Boundary 测试（Phase 3.10.17）。

覆盖任务书 §二十八 ~ §三十三 / §四十一：

    1.  Filter validation   （limit / offset / 空串 / naive datetime / from>to）
    2.  Query DTO           （frozen + 字段白名单 + 无敏感字段）
    3.  Repository → Row    （显式列、WHERE 下推、稳定排序、无 SELECT *）
    4.  get_by_request_id   （命中 / 未命中 / 契约破坏报错）
    5.  Repository Session isolation（5 queries → 5 sessions）
    6.  Repository error mapping（SQLAlchemyError → LLMUsageRepositoryError）
    7.  Service boundary    （Row → View，ORM / Session 不外泄）

DB 集成部分（RUN_DB_TESTS=1）另外覆盖：

    Empty / All / request_id / Not Found / provider / model /
    Combined filter / Time range（含边界）/ Pagination / Stable ordering /
    Read only（count before == after）
"""
from __future__ import annotations

import os
import threading
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.init_db import ensure_request_id_idempotency_index
from backend.app.db.llm_usage_repository import (
    LLMUsageRecordRow,
    LLMUsageRepository,
    LLMUsageRepositoryError,
)
from backend.app.db.models import LLMUsageRecord
from backend.app.db.models.llm_usage_record import LLM_USAGE_SCHEMA
from backend.app.db.session import get_engine, get_session_factory
from backend.app.llm.client import LLMUsage
from backend.app.llm.observability import LLMObservation
from backend.app.services.llm_usage_persistence_service import (
    LLMUsagePersistenceService,
)
from backend.app.services.llm_usage_query_service import (
    DEFAULT_QUERY_LIMIT,
    DEFAULT_QUERY_OFFSET,
    MAX_QUERY_LIMIT,
    MIN_QUERY_LIMIT,
    LLMUsageQueryFilter,
    LLMUsageQueryInputError,
    LLMUsageQueryService,
    LLMUsageRecordView,
)


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


_RUN_DB = _env_flag("RUN_DB_TESTS")

requires_db = pytest.mark.skipif(
    not _RUN_DB,
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)

_APPROVED_VIEW_FIELDS = {
    "id",
    "request_id",
    "provider",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "created_at",
}

_FORBIDDEN_FRAGMENTS = (
    "session", "connection", "engine", "password", "api_key", "authorization",
    "prompt", "messages", "response", "sql", "rag", "tool", "cost", "price",
    "currency", "database_url",
)

_TRUNCATE_SQL = text(
    f"TRUNCATE TABLE {LLM_USAGE_SCHEMA}.llm_usage_record "
    "RESTART IDENTITY CASCADE"
)


# ============================================================
# Fakes（0 网络 / 0 DB）
# ============================================================

class _RowsResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    """Session 替身：execute() 返回脚本化的 Core Row 列表。"""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.executed: list[Any] = []
        self.closed = False
        self.created_in = threading.current_thread().name

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.close()
        return False

    def execute(self, statement: Any) -> Any:
        self.executed.append(statement)
        return _RowsResult(self.rows)

    def begin(self) -> Any:  # 读路径不应调用；调用即测试失败信号
        raise AssertionError("read path must not open a write transaction")

    def close(self) -> None:
        self.closed = True


class FakeRow:
    """SQLAlchemy Core Row 的最小替身（Repository 用 `row._mapping`）。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self._mapping = data


class TrackingSessionFactory:
    """每次调用创建一个新 Session（Phase 3.10.15 的统计式替身）。"""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = [
            FakeRow(row) if isinstance(row, dict) else row for row in rows or []
        ]
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession(self.rows)
        self.sessions.append(session)
        return session


class FakeRepository:
    """Repository 替身：记录 list_records / get_by_request_id 调用。"""

    def __init__(
        self,
        rows: list[LLMUsageRecordRow] | None = None,
        *,
        single: LLMUsageRecordRow | None = None,
        raise_error: Exception | None = None,
    ) -> None:
        self.rows = rows if rows is not None else []
        self.single = single
        self.raise_error = raise_error
        self.list_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []

    def list_records(self, **kwargs: Any) -> list[LLMUsageRecordRow]:
        self.list_calls.append(dict(kwargs))
        if self.raise_error is not None:
            raise self.raise_error
        return self.rows

    def get_by_request_id(self, *, request_id: str) -> LLMUsageRecordRow | None:
        self.get_calls.append(request_id)
        if self.raise_error is not None:
            raise self.raise_error
        return self.single


class RowFactory:
    """生成测试用 `LLMUsageRecordRow`（tz-aware created_at）。"""

    def __init__(self, base_time: datetime | None = None) -> None:
        self.base = base_time or datetime(2024, 1, 1, tzinfo=timezone.utc)

    def row(
        self,
        *,
        row_id: int = 1,
        request_id: str | None = "req-001",
        provider: str | None = "deepseek",
        model: str | None = "deepseek-chat",
        prompt_tokens: int | None = 10,
        completion_tokens: int | None = 20,
        total_tokens: int | None = 30,
        hours: int = 0,
    ) -> LLMUsageRecordRow:
        return LLMUsageRecordRow(
            id=row_id,
            request_id=request_id,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            created_at=self.base + timedelta(hours=hours),
        )


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _repository(session_factory: Any = None) -> LLMUsageRepository:
    return LLMUsageRepository(session_factory=session_factory)


def _compiled_sql(statement: Any) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


# ============================================================
# 1. Query Filter validation
# ============================================================

class TestQueryFilterValidation:
    def test_default_filter(self) -> None:
        query_filter = LLMUsageQueryFilter()
        assert query_filter.request_id is None
        assert query_filter.provider is None
        assert query_filter.model is None
        assert query_filter.created_at_from is None
        assert query_filter.created_at_to is None
        assert query_filter.limit == DEFAULT_QUERY_LIMIT == 50
        assert query_filter.offset == DEFAULT_QUERY_OFFSET == 0

    @pytest.mark.parametrize("name", ["request_id", "provider", "model"])
    def test_blank_text_rejected(self, name: str) -> None:
        with pytest.raises(LLMUsageQueryInputError):
            LLMUsageQueryFilter(**{name: ""})
        with pytest.raises(LLMUsageQueryInputError):
            LLMUsageQueryFilter(**{name: "   "})

    @pytest.mark.parametrize("name", ["request_id", "provider", "model"])
    def test_non_string_rejected(self, name: str) -> None:
        with pytest.raises(LLMUsageQueryInputError):
            LLMUsageQueryFilter(**{name: 123})

    @pytest.mark.parametrize(
        "bad_limit", [0, -1, MAX_QUERY_LIMIT + 1, 999999999, True, "50", 1.5]
    )
    def test_invalid_limit_rejected(self, bad_limit: Any) -> None:
        with pytest.raises(LLMUsageQueryInputError):
            LLMUsageQueryFilter(limit=bad_limit)

    @pytest.mark.parametrize(
        "good_limit",
        [MIN_QUERY_LIMIT, 2, DEFAULT_QUERY_LIMIT, MAX_QUERY_LIMIT],
    )
    def test_valid_limit_accepted(self, good_limit: int) -> None:
        assert LLMUsageQueryFilter(limit=good_limit).limit == good_limit

    @pytest.mark.parametrize("bad_offset", [-1, -999999, True, "0", 1.5])
    def test_invalid_offset_rejected(self, bad_offset: Any) -> None:
        with pytest.raises(LLMUsageQueryInputError):
            LLMUsageQueryFilter(offset=bad_offset)

    def test_large_offset_allowed(self) -> None:
        assert LLMUsageQueryFilter(offset=999999).offset == 999999

    def test_naive_datetime_rejected(self) -> None:
        naive = datetime(2024, 1, 1, 0, 0, 0)  # no tzinfo
        with pytest.raises(LLMUsageQueryInputError):
            LLMUsageQueryFilter(created_at_from=naive)
        with pytest.raises(LLMUsageQueryInputError):
            LLMUsageQueryFilter(created_at_to=naive)

    def test_non_datetime_rejected(self) -> None:
        with pytest.raises(LLMUsageQueryInputError):
            LLMUsageQueryFilter(created_at_from="2024-01-01T00:00:00Z")

    def test_time_range_reversed_rejected_not_swapped(self) -> None:
        """§十四：from > to 必须 reject（不交换 / 不修正 / 不静默空结果）。"""
        with pytest.raises(LLMUsageQueryInputError) as excinfo:
            LLMUsageQueryFilter(
                created_at_from=_utc(2024, 1, 5),
                created_at_to=_utc(2024, 1, 1),
            )
        assert "created_at_from" in str(excinfo.value)

    def test_equal_time_range_allowed(self) -> None:
        moment = _utc(2024, 1, 1)
        query_filter = LLMUsageQueryFilter(
            created_at_from=moment, created_at_to=moment
        )
        assert query_filter.created_at_from == query_filter.created_at_to

    def test_filter_is_frozen(self) -> None:
        query_filter = LLMUsageQueryFilter()
        with pytest.raises(FrozenInstanceError):
            query_filter.limit = 10  # type: ignore[misc]


# ============================================================
# 2. Query DTO
# ============================================================

class TestUsageRecordView:
    def test_fields_match_security_whitelist(self) -> None:
        from backend.app.services.llm_usage_query_service import (
            LLM_USAGE_VIEW_FIELDS,
        )

        assert set(LLM_USAGE_VIEW_FIELDS) == _APPROVED_VIEW_FIELDS
        for name in LLM_USAGE_VIEW_FIELDS:
            assert not any(frag in name for frag in (
                "session", "engine", "password", "api_key", "sql",
                "cost", "price", "currency",
            ))

    def test_from_row_maps_all_fields(self) -> None:
        row = RowFactory().row(row_id=3, request_id="req-003")

        view = LLMUsageRecordView.from_row(row)

        assert isinstance(view, LLMUsageRecordView)
        assert view.id == 3
        assert view.request_id == "req-003"
        assert view.provider == "deepseek"
        assert view.model == "deepseek-chat"
        assert view.prompt_tokens == 10
        assert view.completion_tokens == 20
        assert view.total_tokens == 30
        assert view.created_at == datetime(2024, 1, 1, tzinfo=timezone.utc)

    def test_view_is_not_orm_model(self) -> None:
        view = LLMUsageRecordView.from_row(RowFactory().row())
        assert not hasattr(view, "_sa_instance_state")
        assert type(view).__name__ == "LLMUsageRecordView"

    def test_view_is_immutable(self) -> None:
        """§三十一：DTO 不可被修改。"""
        view = LLMUsageRecordView.from_row(RowFactory().row())
        with pytest.raises(FrozenInstanceError):
            view.provider = "x"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            view.total_tokens = 1  # type: ignore[misc]

    def test_view_rejects_invalid_data(self) -> None:
        base = RowFactory().row()
        with pytest.raises(ValueError):
            LLMUsageRecordView.from_row(
                LLMUsageRecordRow(**{**base.__dict__, "prompt_tokens": -1})
            )
        with pytest.raises(ValueError):
            LLMUsageRecordView.from_row(
                LLMUsageRecordRow(**{**base.__dict__, "created_at": "x"})
            )

    def test_view_has_no_sensitive_attributes(self) -> None:
        view = LLMUsageRecordView.from_row(RowFactory().row())
        public = {name for name in dir(view) if not name.startswith("__")}
        lowered = {name.lower() for name in public}
        for fragment in _FORBIDDEN_FRAGMENTS:
            assert fragment not in lowered


# ============================================================
# 3 / 4 / 5 / 6. Repository 只读 SQL Contract
# ============================================================

class TestRepositoryReadContract:
    def test_select_uses_explicit_columns_not_star(self) -> None:
        """§十八：显式列，不能 SELECT *。"""
        repository = _repository()
        sql = _compiled_sql(repository.build_record_select(limit=10, offset=0))

        assert sql.startswith("SELECT ")
        assert "SELECT *" not in sql
        for column in (
            "id", "request_id", "provider", "model",
            "prompt_tokens", "completion_tokens", "total_tokens",
            "created_at",
        ):
            assert f"llm_usage_record.{column}" in sql

    def test_default_ordering_is_stable(self) -> None:
        """§十 / §二十九：created_at DESC, id DESC。"""
        repository = _repository()
        sql = _compiled_sql(repository.build_record_select(limit=10, offset=0))
        assert "ORDER BY" in sql
        assert "created_at DESC" in sql
        assert "id DESC" in sql

    def test_filters_are_pushed_to_sql(self) -> None:
        """§十七：过滤必须由 PostgreSQL 执行（不在 Python 里二次筛选）。"""
        repository = _repository()
        sql = _compiled_sql(
            repository.build_record_select(
                request_id="req-1",
                provider="deepseek",
                model="deepseek-chat",
                created_at_from=_utc(2024, 1, 1),
                created_at_to=_utc(2024, 1, 31),
                limit=5,
                offset=10,
            )
        )
        assert "WHERE" in sql
        assert "request_id =" in sql
        assert "provider =" in sql
        assert "model =" in sql
        assert "created_at >=" in sql
        assert "created_at <=" in sql
        assert "LIMIT" in sql
        assert "OFFSET" in sql

    def test_no_filter_means_no_where_but_still_ordered(self) -> None:
        repository = _repository()
        sql = _compiled_sql(repository.build_record_select(limit=50, offset=0))
        assert "WHERE" not in sql
        assert "ORDER BY" in sql

    def test_get_by_request_id_returns_single_row(self) -> None:
        rows = [{"id": 9, "request_id": "req-1", "provider": "p",
                 "model": "m", "prompt_tokens": 1, "completion_tokens": 2,
                 "total_tokens": 3, "created_at": _utc(2024, 1, 1)}]
        factory = TrackingSessionFactory(rows)
        repository = _repository(factory)

        result = repository.get_by_request_id(request_id="req-1")

        assert isinstance(result, LLMUsageRecordRow)
        assert result.id == 9
        assert len(factory.sessions) == 1
        # 不用 .first() 静默丢弃：取 2 行用于发现契约破坏
        assert "LIMIT" in _compiled_sql(factory.sessions[0].executed[0])

    def test_get_by_request_id_not_found_returns_none(self) -> None:
        repository = _repository(TrackingSessionFactory([]))
        assert repository.get_by_request_id(request_id="missing") is None

    def test_get_by_request_id_rejects_invalid_input(self) -> None:
        repository = _repository(TrackingSessionFactory([]))
        with pytest.raises(ValueError):
            repository.get_by_request_id(request_id="")
        with pytest.raises(ValueError):
            repository.get_by_request_id(request_id=None)  # type: ignore[arg-type]

    def test_get_by_request_id_detects_broken_uniqueness_contract(self) -> None:
        """§十六：命中多行 = 数据库契约被破坏 → 显式报错。"""
        rows = [
            {"id": 1, "request_id": "dup", "provider": "p", "model": "m",
             "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
             "created_at": _utc(2024, 1, 1)},
            {"id": 2, "request_id": "dup", "provider": "p", "model": "m",
             "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
             "created_at": _utc(2024, 1, 2)},
        ]
        repository = _repository(TrackingSessionFactory(rows))

        with pytest.raises(LLMUsageRepositoryError) as excinfo:
            repository.get_by_request_id(request_id="dup")
        assert "唯一契约" in str(excinfo.value)

    def test_list_records_maps_rows_to_internal_record(self) -> None:
        factory = TrackingSessionFactory(
            [
                {"id": 1, "request_id": None, "provider": None, "model": None,
                 "prompt_tokens": None, "completion_tokens": None,
                 "total_tokens": None, "created_at": _utc(2024, 1, 2)}
            ]
        )
        repository = _repository(factory)

        rows = repository.list_records(limit=50, offset=0)

        assert len(rows) == 1
        row = rows[0]
        assert isinstance(row, LLMUsageRecordRow)
        assert row.request_id is None        # NULL 保持 NULL
        assert row.prompt_tokens is None     # NULL ≠ 0
        assert factory.sessions[0].closed is True

    def test_five_queries_use_five_independent_sessions(self) -> None:
        """§二十四：5 次查询 → 5 个独立 Session（无 global Session）。"""
        factory = TrackingSessionFactory([])
        repository = _repository(factory)

        for _ in range(5):
            repository.list_records(limit=10, offset=0)

        assert len(factory.sessions) == 5
        assert len({id(s) for s in factory.sessions}) == 5
        assert all(s.closed for s in factory.sessions)

    def test_db_error_maps_to_repository_error(self) -> None:
        """§二十五：SQLAlchemy 异常不得泄露到 Service。"""

        class BoomSession(FakeSession):
            def execute(self, statement: Any) -> Any:
                raise SQLAlchemyError("boom")

        class BoomFactory:
            def __call__(self) -> BoomSession:
                return BoomSession([])

        repository = _repository(BoomFactory())

        with pytest.raises(LLMUsageRepositoryError):
            repository.list_records(limit=10, offset=0)


# ============================================================
# 7. Service boundary
# ============================================================

class TestQueryServiceBoundary:
    def test_list_returns_view_dto_not_rows(self) -> None:
        repository = FakeRepository(
            rows=[
                RowFactory().row(row_id=1, request_id="req-001"),
                RowFactory().row(row_id=2, request_id="req-002", hours=1),
            ]
        )
        service = LLMUsageQueryService(repository=repository)

        views = service.list_records(provider="deepseek")

        assert len(views) == 2
        assert all(isinstance(v, LLMUsageRecordView) for v in views)
        assert all(not isinstance(v, LLMUsageRecordRow) for v in views)
        assert repository.list_calls[0]["limit"] == DEFAULT_QUERY_LIMIT
        assert repository.list_calls[0]["offset"] == 0
        assert repository.list_calls[0]["provider"] == "deepseek"

    def test_list_passes_all_filters(self) -> None:
        repository = FakeRepository()
        service = LLMUsageQueryService(repository=repository)

        service.list_records(
            request_id="req-1",
            provider="deepseek",
            model="deepseek-chat",
            created_at_from=_utc(2024, 1, 1),
            created_at_to=_utc(2024, 1, 31),
            limit=10,
            offset=20,
        )

        call = repository.list_calls[0]
        assert call["request_id"] == "req-1"
        assert call["provider"] == "deepseek"
        assert call["model"] == "deepseek-chat"
        assert call["created_at_from"] == _utc(2024, 1, 1)
        assert call["created_at_to"] == _utc(2024, 1, 31)
        assert call["limit"] == 10
        assert call["offset"] == 20

    def test_query_accepts_filter_object(self) -> None:
        repository = FakeRepository(rows=[RowFactory().row()])
        service = LLMUsageQueryService(repository=repository)

        views = service.query(LLMUsageQueryFilter(provider="deepseek", limit=5))

        assert len(views) == 1
        assert repository.list_calls[0]["limit"] == 5
        assert repository.list_calls[0]["provider"] == "deepseek"

    def test_query_default_filter_when_none(self) -> None:
        repository = FakeRepository()
        service = LLMUsageQueryService(repository=repository)

        result = service.query()

        assert result == []
        assert repository.list_calls[0]["limit"] == DEFAULT_QUERY_LIMIT
        assert repository.list_calls[0]["offset"] == 0

    def test_get_by_request_id_maps_to_view(self) -> None:
        repository = FakeRepository(
            single=RowFactory().row(row_id=7, request_id="req-007")
        )
        service = LLMUsageQueryService(repository=repository)

        view = service.get_by_request_id("req-007")

        assert isinstance(view, LLMUsageRecordView)
        assert view.id == 7
        assert repository.get_calls == ["req-007"]

    def test_get_by_request_id_not_found(self) -> None:
        service = LLMUsageQueryService(repository=FakeRepository(single=None))
        assert service.get_by_request_id("req-missing") is None

    def test_get_by_request_id_rejects_blank(self) -> None:
        service = LLMUsageQueryService(repository=FakeRepository())
        with pytest.raises(LLMUsageQueryInputError):
            service.get_by_request_id("   ")

    def test_repository_error_propagates_as_is(self) -> None:
        """§二十五：Repository 错误不被降级为 warning / 不被吞掉。"""
        repository = FakeRepository(
            raise_error=LLMUsageRepositoryError("db down")
        )
        service = LLMUsageQueryService(repository=repository)

        with pytest.raises(LLMUsageRepositoryError):
            service.list_records()
        with pytest.raises(LLMUsageRepositoryError):
            service.get_by_request_id("req-1")

    def test_service_does_not_expose_session_or_orm(self) -> None:
        service = LLMUsageQueryService(repository=FakeRepository())
        public = {name for name in dir(service) if not name.startswith("_")}
        lowered = {name.lower() for name in public}
        for fragment in ("session", "engine", "connection", "metadata"):
            assert fragment not in lowered


# ============================================================
# DB integration（RUN_DB_TESTS=1）
# ============================================================

_FIXTURE_ROWS: list[dict[str, Any]] = [
    {"request_id": "req-001", "provider": "deepseek",
     "model": "deepseek-chat", "tokens": (10, 20, 30),
     "created_at": _utc(2024, 1, 1)},
    {"request_id": "req-002", "provider": "openai", "model": "gpt-4o",
     "tokens": (1, 2, 3), "created_at": _utc(2024, 1, 2)},
    {"request_id": "req-003", "provider": "deepseek",
     "model": "deepseek-chat", "tokens": (5, 5, 10),
     "created_at": _utc(2024, 1, 3)},
    {"request_id": "req-004", "provider": "deepseek",
     "model": "deepseek-reasoner", "tokens": (7, 7, 14),
     "created_at": _utc(2024, 1, 3)},   # 与 req-003 同 created_at → id DESC
    {"request_id": "req-005", "provider": "openai", "model": "gpt-4o-mini",
     "tokens": (2, 2, 4), "created_at": _utc(2024, 1, 4)},
    {"request_id": None, "provider": "deepseek", "model": "deepseek-chat",
     "tokens": (3, 3, 6), "created_at": _utc(2024, 1, 5)},
    {"request_id": "req-007", "provider": "anthropic", "model": "claude-3",
     "tokens": (4, 4, 8), "created_at": _utc(2024, 1, 6)},
    {"request_id": None, "provider": "openai", "model": "gpt-4o",
     "tokens": (6, 6, 12), "created_at": _utc(2024, 1, 7)},
]


def _ensure_schema() -> Any:
    """确保 schema / 表 / 幂等索引存在（幂等，不删数据）。"""
    from backend.app.db import models  # noqa: F401
    from backend.app.db.base import Base

    engine = get_engine()
    assert engine is not None, (
        "DATABASE_URL 未配置；请配置后启用 RUN_DB_TESTS=1"
    )
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {LLM_USAGE_SCHEMA}"))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        ensure_request_id_idempotency_index(conn)
    return engine


def _seed_fixture_rows(engine: Any) -> int:
    """用**正式写入路径**造 8 行测试数据（测试后完全清理）。

    created_at 由 DB `server_default` 生成，这里再刷成确定性取值
    （时间范围 / 稳定排序断言需要确定性数据）。
    """
    service = LLMUsagePersistenceService()
    for spec in _FIXTURE_ROWS:
        prompt, completion, total = spec["tokens"]
        service.persist(
            LLMObservation(
                provider=spec["provider"],
                model=spec["model"],
                success=True,
                usage=LLMUsage(prompt, completion, total),
                request_id=spec["request_id"],
            )
        )

    with engine.begin() as conn:
        ids = [
            int(row_id)
            for row_id in conn.execute(
                text(
                    f"SELECT id FROM {LLM_USAGE_SCHEMA}.llm_usage_record "
                    "ORDER BY id"
                )
            ).scalars().all()
        ]
        assert len(ids) == len(_FIXTURE_ROWS), f"fixture 行数异常: {len(ids)}"
        for row_id, spec in zip(ids, _FIXTURE_ROWS):
            conn.execute(
                text(
                    f"UPDATE {LLM_USAGE_SCHEMA}.llm_usage_record "
                    "SET created_at = :created_at WHERE id = :row_id"
                ),
                {"created_at": spec["created_at"], "row_id": row_id},
            )
    return len(ids)


def _open_session() -> Session:
    factory = get_session_factory()
    assert factory is not None
    return factory()


def _count(session: Session) -> int:
    return int(
        session.scalar(select(func.count()).select_from(LLMUsageRecord)) or 0
    )


def _cleanup(engine: Any) -> int:
    with engine.begin() as conn:
        conn.execute(_TRUNCATE_SQL)
        return int(
            conn.execute(
                text(
                    f"SELECT COUNT(*) FROM {LLM_USAGE_SCHEMA}.llm_usage_record"
                )
            ).scalar_one()
        )


@requires_db
class TestUsageQueryDatabase:
    def test_empty_database_returns_empty_list(self) -> None:
        """§二十八.1 Empty。"""
        engine = _ensure_schema()
        session = _open_session()
        try:
            assert _count(session) == 0
            service = LLMUsageQueryService()
            assert service.list_records() == []
            assert service.get_by_request_id("req-not-exist") is None
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_query_all_rows(self) -> None:
        """§二十八.2 All + 默认排序（created_at DESC, id DESC）。"""
        engine = _ensure_schema()
        session = _open_session()
        try:
            assert _seed_fixture_rows(engine) == 8
            service = LLMUsageQueryService()

            views = service.list_records(limit=100)

            assert len(views) == 8
            timestamps = [v.created_at for v in views]
            assert timestamps == sorted(timestamps, reverse=True)
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_request_id_filter_and_not_found(self) -> None:
        """§二十八.3 / 4。"""
        engine = _ensure_schema()
        session = _open_session()
        try:
            _seed_fixture_rows(engine)
            service = LLMUsageQueryService()

            view = service.get_by_request_id("req-003")
            assert view is not None
            assert view.request_id == "req-003"
            assert view.model == "deepseek-chat"
            assert view.total_tokens == 10

            listed = service.list_records(request_id="req-003")
            assert len(listed) == 1
            assert listed[0].id == view.id

            assert service.get_by_request_id("req-not-exist") is None
            assert service.list_records(request_id="req-not-exist") == []
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_provider_filter(self) -> None:
        """§二十八.5。"""
        engine = _ensure_schema()
        session = _open_session()
        try:
            _seed_fixture_rows(engine)
            views = LLMUsageQueryService().list_records(
                provider="deepseek", limit=100
            )

            assert len(views) == 4
            assert all(v.provider == "deepseek" for v in views)
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_model_filter(self) -> None:
        """§二十八.6。"""
        engine = _ensure_schema()
        session = _open_session()
        try:
            _seed_fixture_rows(engine)
            views = LLMUsageQueryService().list_records(
                model="deepseek-chat", limit=100
            )

            assert len(views) == 3
            assert all(v.model == "deepseek-chat" for v in views)
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_combined_provider_and_model_filter(self) -> None:
        """§二十八.7 组合过滤（AND 语义，由 PostgreSQL 执行）。"""
        engine = _ensure_schema()
        session = _open_session()
        try:
            _seed_fixture_rows(engine)
            views = LLMUsageQueryService().list_records(
                provider="deepseek",
                model="deepseek-chat",
                limit=100,
            )

            assert len(views) == 3
            assert all(
                v.provider == "deepseek" and v.model == "deepseek-chat"
                for v in views
            )
            assert {v.request_id for v in views} == {"req-001", "req-003", None}
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_time_range_includes_boundaries(self) -> None:
        """§二十八.8 / §十三：>= from 且 <= to（含边界）。"""
        engine = _ensure_schema()
        session = _open_session()
        try:
            _seed_fixture_rows(engine)
            service = LLMUsageQueryService()

            views = service.list_records(
                created_at_from=_utc(2024, 1, 3),
                created_at_to=_utc(2024, 1, 4),
                limit=100,
            )
            assert len(views) == 3      # req-003 / req-004 / req-005
            assert {v.created_at for v in views} == {
                _utc(2024, 1, 3), _utc(2024, 1, 4),
            }

            only_from = service.list_records(
                created_at_from=_utc(2024, 1, 6), limit=100
            )
            assert len(only_from) == 2
            assert all(v.created_at >= _utc(2024, 1, 6) for v in only_from)

            only_to = service.list_records(
                created_at_to=_utc(2024, 1, 2), limit=100
            )
            assert len(only_to) == 2
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_reversed_time_range_is_rejected(self) -> None:
        """§十四：from > to → 在进入数据库之前被拒绝。"""
        engine = _ensure_schema()
        session = _open_session()
        try:
            _seed_fixture_rows(engine)
            service = LLMUsageQueryService()

            with pytest.raises(LLMUsageQueryInputError):
                service.list_records(
                    created_at_from=_utc(2024, 1, 5),
                    created_at_to=_utc(2024, 1, 1),
                )
            assert _count(session) == 8   # 数据没有任何变化
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_pagination_pages_do_not_overlap(self) -> None:
        """§二十八.9 分页。"""
        engine = _ensure_schema()
        session = _open_session()
        try:
            _seed_fixture_rows(engine)
            service = LLMUsageQueryService()

            page1 = service.list_records(limit=2, offset=0)
            page2 = service.list_records(limit=2, offset=2)
            page3 = service.list_records(limit=2, offset=4)

            assert len(page1) == len(page2) == len(page3) == 2
            assert not ({v.id for v in page1} & {v.id for v in page2})
            assert not ({v.id for v in page2} & {v.id for v in page3})

            assert len(service.list_records(limit=100)) == 8
            assert service.list_records(limit=100, offset=100) == []
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_stable_ordering_with_identical_created_at(self) -> None:
        """§二十九：created_at 相同 → id DESC 稳定排序。"""
        engine = _ensure_schema()
        session = _open_session()
        try:
            _seed_fixture_rows(engine)
            service = LLMUsageQueryService()

            views = service.list_records(limit=100)
            same_time = [v for v in views if v.created_at == _utc(2024, 1, 3)]

            assert len(same_time) == 2
            assert same_time[0].request_id == "req-004"
            assert same_time[1].request_id == "req-003"
            assert same_time[0].id > same_time[1].id
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_query_is_read_only(self) -> None:
        """§二十六 / §三十三：get / list 全程 READ ONLY。"""
        engine = _ensure_schema()
        session = _open_session()
        try:
            rows_before = _seed_fixture_rows(engine)
            service = LLMUsageQueryService()

            service.list_records(limit=100)
            service.list_records(provider="deepseek", limit=10, offset=1)
            service.get_by_request_id("req-001")
            service.get_by_request_id("req-not-exist")

            rows_after = _count(session)
            assert rows_before == rows_after == 8
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_public_schema_untouched(self) -> None:
        """public schema 不得因本阶段产生新表 / 数据变化。"""
        engine = _ensure_schema()

        def _counts() -> dict[str, int]:
            counts: dict[str, int] = {}
            with engine.connect() as conn:
                for table in ("knowledge_document", "knowledge_chunk"):
                    exists = conn.execute(
                        text(
                            "SELECT EXISTS("
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema='public' AND table_name=:t)"
                        ),
                        {"t": table},
                    ).scalar_one()
                    if exists:
                        counts[table] = int(
                            conn.execute(
                                text(f"SELECT COUNT(*) FROM public.{table}")
                            ).scalar_one()
                        )
            return counts

        before = _counts()
        session = _open_session()
        try:
            _seed_fixture_rows(engine)
            LLMUsageQueryService().list_records(limit=100)
        finally:
            session.close()
            _cleanup(engine)
        after = _counts()

        assert before == after
