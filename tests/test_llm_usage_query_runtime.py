"""LLM Usage Query Async Runtime Boundary 测试（Phase 3.10.18）。

只验证：

    同步 Query Service
        ↓
    Runtime Bridge（asyncio.to_thread）
        ↓
    async caller

不重复 Phase 3.10.17 已验证的 filter validation / DTO security /
SQL compilation（§二十六）。

覆盖（§二十四 / §二十五）：

    1.  bridge 依赖校验（None / 缺少方法 → TypeError）
    2.  query_async / list_records_async / get_by_request_id_async 成功
    3.  同步 Service 不变（仍为 def，可同步调用）
    4.  Service 异常传播（参数非法 → LLMUsageQueryInputError）
    5.  Repository 异常传播（LLMUsageRepositoryError）
    6.  5 并发查询（结果不串线）
    7.  Session 隔离（5 查询 → 5 独立 Session，全部关闭）
    8.  event loop heartbeat（direct sync = BLOCKING / bridge = PASS）
    9.  no create_task / ensure_future（静态检查）
    10. no global Session / 全局状态（静态检查）
    11. READ ONLY（无 DB 写入）
    12. DTO identity / 内容一致（仍是 LLMUsageRecordView）
    13. Cancellation Contract（允许向 caller 传播）

DB 部分（RUN_DB_TESTS=1）追加：async get / list / filter /
pagination / stable ordering / 5 并发 async query —— 全部经 Bridge。
"""
from __future__ import annotations

import asyncio
import ast
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import func, select, text
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
from backend.app.services.llm_usage_query_runtime import (
    LLMUsageQueryRuntimeBridge,
)
from backend.app.services.llm_usage_query_service import (
    DEFAULT_QUERY_LIMIT,
    DEFAULT_QUERY_OFFSET,
    LLMUsageQueryFilter,
    LLMUsageQueryInputError,
    LLMUsageQueryService,
    LLMUsageRecordView,
)

# 复用 Phase 3.10.15 已有的 heartbeat helper（不复制一套 heartbeat 框架）
from tests.test_llm_usage_persistence_runtime import (  # noqa: E402
    _max_gap,
    _run_with_heartbeat,
)


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


_RUN_DB = _env_flag("RUN_DB_TESTS")

requires_db = pytest.mark.skipif(
    not _RUN_DB,
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)

_TRUNCATE_SQL = text(
    f"TRUNCATE TABLE {LLM_USAGE_SCHEMA}.llm_usage_record "
    "RESTART IDENTITY CASCADE"
)

_RUNTIME_MODULE = "backend/app/services/llm_usage_query_runtime.py"


# ============================================================
# Fakes（0 网络 / 0 DB）
# ============================================================

class _RowsResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class FakeRow:
    """SQLAlchemy Core Row 替身（Repository 用 `row._mapping`）。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self._mapping = data


class FakeSession:
    """Session 替身：可注入延迟，模拟"慢查询"。"""

    def __init__(
        self,
        rows: list[Any] | None = None,
        *,
        delay: float = 0.0,
        raise_error: Exception | None = None,
    ) -> None:
        self.rows = rows if rows is not None else []
        self.delay = delay
        self.raise_error = raise_error
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
        if self.delay:
            time.sleep(self.delay)
        if self.raise_error is not None:
            raise self.raise_error
        return _RowsResult(self.rows)

    def begin(self) -> Any:
        raise AssertionError("read path must not open a write transaction")

    def close(self) -> None:
        self.closed = True


class TrackingSessionFactory:
    """每次调用创建一个新 Session；记录线程与关闭状态。"""

    def __init__(self, rows: list[Any] | None = None, *, delay: float = 0.0) -> None:
        self.rows = [
            FakeRow(row) if isinstance(row, dict) else row for row in rows or []
        ]
        self.delay = delay
        self.sessions: list[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession(self.rows, delay=self.delay)
        self.sessions.append(session)
        return session


class ScriptedRepository:
    """按 request_id 返回不同行的仓储替身（并发不串线验证用）。"""

    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.list_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []
        self.threads: list[str] = []

    def _row(self, index: int) -> LLMUsageRecordRow:
        return LLMUsageRecordRow(
            id=index,
            request_id=f"req-{index:03d}",
            provider=f"provider-{index}",
            model=f"model-{index}",
            prompt_tokens=10 * index,
            completion_tokens=2 * index,
            total_tokens=12 * index,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc)
            + timedelta(hours=index),
        )

    def list_records(self, **kwargs: Any) -> list[LLMUsageRecordRow]:
        self.list_calls.append(dict(kwargs))
        self.threads.append(threading.current_thread().name)
        if self.delay:
            time.sleep(self.delay)
        request_id = kwargs.get("request_id")
        if request_id:
            index = int(str(request_id).split("-")[1])
            return [self._row(index)]
        return [self._row(i) for i in (1, 2, 3)]

    def get_by_request_id(self, *, request_id: str) -> LLMUsageRecordRow | None:
        self.get_calls.append(request_id)
        self.threads.append(threading.current_thread().name)
        if self.delay:
            time.sleep(self.delay)
        if request_id == "missing":
            return None
        return self._row(int(request_id.split("-")[1]))


class FailingRepository:
    """读失败（Repository 异常必须传播，不能被吞掉）。"""

    def list_records(self, **kwargs: Any) -> list[LLMUsageRecordRow]:
        raise LLMUsageRepositoryError("db query failed")

    def get_by_request_id(self, *, request_id: str) -> LLMUsageRecordRow | None:
        raise LLMUsageRepositoryError("db query failed")


def _service(repository: Any) -> LLMUsageQueryService:
    return LLMUsageQueryService(repository=repository)


def _bridge(repository: Any) -> LLMUsageQueryRuntimeBridge:
    return LLMUsageQueryRuntimeBridge(query_service=_service(repository))


def _run(coro_factory: Any) -> Any:
    return asyncio.run(coro_factory())


def _module_source(relative_path: str) -> str:
    path = os.path.join(REPO_ROOT, *relative_path.split("/"))
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# ============================================================
# 1 / 2. Bridge 依赖 + async 入口
# ============================================================

class TestBridgeContract:
    def test_rejects_invalid_service(self) -> None:
        with pytest.raises(TypeError):
            LLMUsageQueryRuntimeBridge(query_service=None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            LLMUsageQueryRuntimeBridge(query_service=object())  # type: ignore[arg-type]

    def test_exposes_injected_service(self) -> None:
        service = _service(ScriptedRepository())
        bridge = LLMUsageQueryRuntimeBridge(query_service=service)
        assert bridge.query_service is service

    def test_query_async_success(self) -> None:
        bridge = _bridge(ScriptedRepository())

        async def _query() -> Any:
            return await bridge.query_async(LLMUsageQueryFilter(limit=10))

        views = _run(_query)

        assert len(views) == 3
        assert all(isinstance(v, LLMUsageRecordView) for v in views)

    def test_query_async_default_filter(self) -> None:
        repository = ScriptedRepository()
        bridge = _bridge(repository)

        async def _query() -> Any:
            return await bridge.query_async()

        _run(_query)

        assert repository.list_calls[0]["limit"] == DEFAULT_QUERY_LIMIT
        assert repository.list_calls[0]["offset"] == DEFAULT_QUERY_OFFSET

    def test_list_records_async_success(self) -> None:
        repository = ScriptedRepository()
        bridge = _bridge(repository)

        async def _query() -> Any:
            return await bridge.list_records_async(
                provider="provider-1",
                limit=5,
                offset=2,
            )

        views = _run(_query)

        assert isinstance(views, list)
        assert repository.list_calls[0]["provider"] == "provider-1"
        assert repository.list_calls[0]["limit"] == 5
        assert repository.list_calls[0]["offset"] == 2

    def test_get_by_request_id_async_success(self) -> None:
        bridge = _bridge(ScriptedRepository())

        async def _query() -> Any:
            return await bridge.get_by_request_id_async("req-002")

        view = _run(_query)

        assert isinstance(view, LLMUsageRecordView)
        assert view.request_id == "req-002"
        assert view.provider == "provider-2"
        assert view.model == "model-2"
        assert view.total_tokens == 24

    def test_get_by_request_id_async_not_found(self) -> None:
        bridge = _bridge(ScriptedRepository())

        async def _query() -> Any:
            return await bridge.get_by_request_id_async("missing")

        assert _run(_query) is None

    def test_sync_service_stays_synchronous(self) -> None:
        """§七：同步 Service 仍然是 def，不因 async 边界被改造。"""
        service = _service(ScriptedRepository())
        assert not asyncio.iscoroutinefunction(service.query)
        assert not asyncio.iscoroutinefunction(service.list_records)
        assert not asyncio.iscoroutinefunction(service.get_by_request_id)
        assert asyncio.iscoroutinefunction(
            LLMUsageQueryRuntimeBridge.query_async
        )
        # 同步入口仍然可直接使用（sync caller 路径不变）
        assert len(service.list_records(limit=10)) == 3


# ============================================================
# 3. Query 执行发生在 worker thread（不在 event loop 线程）
# ============================================================

class TestThreadBoundary:
    def test_query_runs_outside_event_loop_thread(self) -> None:
        repository = ScriptedRepository()
        bridge = _bridge(repository)

        async def _query() -> str:
            loop_thread = threading.current_thread().name
            await bridge.query_async()
            return loop_thread

        loop_thread = _run(_query)

        assert repository.threads
        assert loop_thread not in repository.threads
        assert threading.main_thread().name not in repository.threads


# ============================================================
# 4 / 5. 错误传播（Query ≠ Persistence：失败必须可观察）
# ============================================================

class TestErrorPropagation:
    def test_repository_error_propagates(self) -> None:
        bridge = _bridge(FailingRepository())

        async def _query() -> Any:
            return await bridge.query_async()

        with pytest.raises(LLMUsageRepositoryError):
            _run(_query)

        async def _get() -> Any:
            return await bridge.get_by_request_id_async("req-001")

        with pytest.raises(LLMUsageRepositoryError):
            _run(_get)

    def test_service_input_error_propagates(self) -> None:
        bridge = _bridge(ScriptedRepository())

        async def _query() -> Any:
            return await bridge.list_records_async(limit=0)

        with pytest.raises(LLMUsageQueryInputError):
            _run(_query)

    def test_error_is_not_swallowed_into_empty_result(self) -> None:
        """§十八：禁止 catch Exception → return []。"""

        class BoomRepository:
            def list_records(self, **kwargs: Any) -> list[LLMUsageRecordRow]:
                raise SQLAlchemyError("boom")

            def get_by_request_id(self, *, request_id: str) -> Any:
                raise SQLAlchemyError("boom")

        bridge = _bridge(BoomRepository())

        async def _query() -> Any:
            return await bridge.query_async()

        with pytest.raises(SQLAlchemyError):
            _run(_query)


# ============================================================
# 6 / 7. 并发 + Session 隔离
# ============================================================

class TestConcurrencyAndSessionIsolation:
    def test_five_concurrent_queries_no_cross_contamination(self) -> None:
        """§十三：5 并发 query_async → 5 结果，互不串线。"""
        repository = ScriptedRepository(delay=0.05)
        bridge = _bridge(repository)

        async def _gather() -> list[Any]:
            return list(
                await asyncio.gather(
                    *[
                        bridge.get_by_request_id_async(f"req-{n:03d}")
                        for n in range(1, 6)
                    ]
                )
            )

        results = _run(_gather)

        assert len(results) == 5
        for n, view in zip(range(1, 6), results):
            assert isinstance(view, LLMUsageRecordView)
            assert view.request_id == f"req-{n:03d}"
            assert view.provider == f"provider-{n}"
            assert view.model == f"model-{n}"
            assert view.total_tokens == 12 * n
        assert len(repository.get_calls) == 5

    def test_five_concurrent_queries_five_independent_sessions(self) -> None:
        """§十：5 并发 → 5 次 worker 执行 → 5 个独立 Session（全部关闭）。"""
        factory = TrackingSessionFactory([])
        repository = LLMUsageRepository(session_factory=factory)
        bridge = LLMUsageQueryRuntimeBridge(
            query_service=LLMUsageQueryService(repository=repository)
        )

        async def _gather() -> str:
            loop_thread = threading.current_thread().name
            await asyncio.gather(*[bridge.query_async() for _ in range(5)])
            return loop_thread

        loop_thread = _run(_gather)

        assert len(factory.sessions) == 5
        assert len({id(s) for s in factory.sessions}) == 5
        assert all(s.closed for s in factory.sessions)
        assert all(s.created_in != loop_thread for s in factory.sessions)
        assert all(
            s.created_in != threading.main_thread().name
            for s in factory.sessions
        )


# ============================================================
# 8. Event loop non-blocking（复用 3.10.15 heartbeat helper）
# ============================================================

class TestEventLoopRemainsSchedulable:
    def test_direct_sync_query_blocks_the_loop(self) -> None:
        """§十二 基准：直接同步查询 → event loop 明显停摆。"""
        repository = LLMUsageRepository(
            session_factory=TrackingSessionFactory([], delay=0.2)
        )
        service = LLMUsageQueryService(repository=repository)

        async def _sync_query() -> Any:
            return service.list_records(limit=10)

        results, ticks = _run_with_heartbeat(_sync_query)

        assert results == []
        assert _max_gap(ticks) >= 0.15, f"expected blocking, gap={_max_gap(ticks)}"

    def test_runtime_bridge_keeps_loop_schedulable(self) -> None:
        """§十一：query_async 期间 heartbeat 继续推进。"""
        repository = LLMUsageRepository(
            session_factory=TrackingSessionFactory([], delay=0.2)
        )
        service = LLMUsageQueryService(repository=repository)
        bridge = LLMUsageQueryRuntimeBridge(query_service=service)

        async def _async_query() -> Any:
            return await bridge.query_async()

        results, ticks = _run_with_heartbeat(_async_query)

        assert results == []
        assert len(ticks) >= 4, f"heartbeat stalled: {len(ticks)}"
        assert _max_gap(ticks) < 0.1, f"loop blocked: {_max_gap(ticks)}"

    def test_bridged_beats_direct(self) -> None:
        """轻量对比（非 benchmark）：bridge tick 数 > 直接同步，
        bridge 最大间隔 < 直接同步。"""

        def _factory() -> TrackingSessionFactory:
            return TrackingSessionFactory([], delay=0.25)

        sync_service = LLMUsageQueryService(
            repository=LLMUsageRepository(session_factory=_factory())
        )

        async def _direct() -> Any:
            return sync_service.list_records(limit=10)

        _, direct_ticks = _run_with_heartbeat(_direct)

        bridge = LLMUsageQueryRuntimeBridge(query_service=sync_service)

        async def _bridged() -> Any:
            return await bridge.query_async()

        _, bridged_ticks = _run_with_heartbeat(_bridged)

        assert len(bridged_ticks) > len(direct_ticks)
        assert _max_gap(bridged_ticks) < _max_gap(direct_ticks)


# ============================================================
# 9 / 10 / 11. 静态检查：无 fire-and-forget / 无全局状态
# ============================================================

class TestStaticRuntimeGuards:
    def test_no_create_task_or_ensure_future(self) -> None:
        """§二十：禁止 fire-and-forget。"""
        tree = ast.parse(_module_source(_RUNTIME_MODULE))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(
                    node.func, "id", ""
                ))
                assert name not in {"create_task", "ensure_future"}, name

    def test_to_thread_is_the_only_boundary(self) -> None:
        """线程边界只用 asyncio.to_thread（无 AsyncSession / asyncpg）。"""
        source = _module_source(_RUNTIME_MODULE)
        tree = ast.parse(source)
        calls = [
            getattr(node.func, "attr", getattr(node.func, "id", ""))
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        ]
        assert "to_thread" in calls
        for forbidden in (
            "create_async_engine", "async_sessionmaker", "AsyncSession",
        ):
            assert forbidden not in calls

    def test_no_global_session_or_state(self) -> None:
        """§二十一：无全局 Session / Connection / dict / set / 缓存。"""
        tree = ast.parse(_module_source(_RUNTIME_MODULE))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = [
                    getattr(t, "id", getattr(t, "attr", "")) for t in node.targets
                ]
                if isinstance(
                    node.value, (ast.Dict, ast.Set, ast.ListComp,
                                 ast.DictComp, ast.SetComp)
                ):
                    assert "__all__" in targets, targets
                if isinstance(node.value, ast.Call):
                    func_name = getattr(
                        node.value.func, "id",
                        getattr(node.value.func, "attr", ""),
                    )
                    assert func_name not in {
                        "Session", "sessionmaker", "create_engine",
                        "Connection", "lru_cache",
                    }, func_name

    def test_no_new_dependency(self) -> None:
        tree = ast.parse(_module_source(_RUNTIME_MODULE))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        allowed = {"__future__", "asyncio", "typing", "datetime", "backend"}
        for name in imported:
            assert name.split(".")[0] in allowed, imported


# ============================================================
# 12. DTO identity / content + READ ONLY
# ============================================================

class TestDtoAndReadOnly:
    def test_bridge_returns_the_same_view_objects(self) -> None:
        """Bridge 不重建 DTO：返回值与同步 Service 一致（identity）。"""
        repository = ScriptedRepository()
        service = _service(repository)
        bridge = LLMUsageQueryRuntimeBridge(query_service=service)

        sync_views = service.list_records(limit=10)

        async def _query() -> Any:
            return await bridge.query_async(LLMUsageQueryFilter(limit=10))

        async_views = _run(_query)

        assert len(sync_views) == len(async_views)
        for sync_view, async_view in zip(sync_views, async_views):
            assert async_view == sync_view          # frozen dataclass 等值
            assert type(async_view) is LLMUsageRecordView
            assert not hasattr(async_view, "_sa_instance_state")

    def test_read_only_repository_is_never_written(self) -> None:
        """§二十二：Bridge 只触发读方法，绝不会调用写入方法。"""

        class ReadOnlyRepository(ScriptedRepository):
            def create(self, **kwargs: Any) -> Any:
                raise AssertionError("query runtime must not write")

        repository = ReadOnlyRepository()
        bridge = _bridge(repository)

        async def _query() -> Any:
            await bridge.query_async()
            await bridge.list_records_async(provider="provider-1")
            return await bridge.get_by_request_id_async("req-001")

        _run(_query)

        assert len(repository.get_calls) == 1
        assert len(repository.list_calls) == 2


# ============================================================
# 13. Cancellation Contract
# ============================================================

class TestCancellation:
    def test_cancellation_propagates_to_caller(self) -> None:
        """§十九：Query 是主动读取 → caller cancellation 允许传播
        （不复制 Persistence 的 absorb 语义）。"""
        repository = LLMUsageRepository(
            session_factory=TrackingSessionFactory([], delay=0.3)
        )
        bridge = LLMUsageQueryRuntimeBridge(
            query_service=LLMUsageQueryService(repository=repository)
        )

        async def _scenario() -> str:
            task = asyncio.create_task(bridge.query_async())
            await asyncio.sleep(0.05)      # 让查询进入 worker thread
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                return "cancelled"
            return "completed"

        assert _run(_scenario) == "cancelled"

    def test_cancellation_does_not_create_hidden_state(self) -> None:
        """取消后不残留未关闭的 Session（无隐藏状态）。"""
        factory = TrackingSessionFactory([], delay=0.3)
        repository = LLMUsageRepository(session_factory=factory)
        bridge = LLMUsageQueryRuntimeBridge(
            query_service=LLMUsageQueryService(repository=repository)
        )

        async def _scenario() -> None:
            task = asyncio.create_task(bridge.query_async())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.4)       # 等待 worker 收尾

        _run(_scenario)

        assert len(factory.sessions) == 1
        assert factory.sessions[0].closed is True


# ============================================================
# DB integration（RUN_DB_TESTS=1）—— 全部经 Runtime Bridge
# ============================================================

_FIXTURE_ROWS = [
    {"request_id": "req-001", "provider": "deepseek",
     "model": "deepseek-chat", "tokens": (10, 20, 30),
     "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc)},
    {"request_id": "req-002", "provider": "openai", "model": "gpt-4o",
     "tokens": (1, 2, 3),
     "created_at": datetime(2024, 1, 2, tzinfo=timezone.utc)},
    {"request_id": "req-003", "provider": "deepseek",
     "model": "deepseek-chat", "tokens": (5, 5, 10),
     "created_at": datetime(2024, 1, 3, tzinfo=timezone.utc)},
    {"request_id": "req-004", "provider": "deepseek",
     "model": "deepseek-reasoner", "tokens": (7, 7, 14),
     "created_at": datetime(2024, 1, 3, tzinfo=timezone.utc)},
    {"request_id": "req-005", "provider": "openai", "model": "gpt-4o-mini",
     "tokens": (2, 2, 4),
     "created_at": datetime(2024, 1, 4, tzinfo=timezone.utc)},
]


def _ensure_schema() -> Any:
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
        assert len(ids) == len(_FIXTURE_ROWS)
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
class TestAsyncQueryDatabase:
    def test_async_get_by_request_id(self) -> None:
        engine = _ensure_schema()
        session = _open_session()
        try:
            _seed_fixture_rows(engine)
            bridge = LLMUsageQueryRuntimeBridge(
                query_service=LLMUsageQueryService()
            )

            async def _get() -> Any:
                return await bridge.get_by_request_id_async("req-003")

            view = asyncio.run(_get())
            assert view is not None
            assert view.request_id == "req-003"
            assert view.total_tokens == 10
            assert _count(session) == 5
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_async_list_all(self) -> None:
        engine = _ensure_schema()
        session = _open_session()
        try:
            _seed_fixture_rows(engine)
            bridge = LLMUsageQueryRuntimeBridge(
                query_service=LLMUsageQueryService()
            )

            async def _list() -> Any:
                return await bridge.query_async(LLMUsageQueryFilter(limit=100))

            views = asyncio.run(_list())
            assert len(views) == 5
            timestamps = [v.created_at for v in views]
            assert timestamps == sorted(timestamps, reverse=True)
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_async_provider_and_model_filter(self) -> None:
        engine = _ensure_schema()
        session = _open_session()
        try:
            _seed_fixture_rows(engine)
            bridge = LLMUsageQueryRuntimeBridge(
                query_service=LLMUsageQueryService()
            )

            async def _list(**kwargs: Any) -> Any:
                return await bridge.list_records_async(**kwargs)

            provider_rows = asyncio.run(_list(provider="deepseek", limit=100))
            assert len(provider_rows) == 3
            assert all(v.provider == "deepseek" for v in provider_rows)

            model_rows = asyncio.run(_list(model="deepseek-chat", limit=100))
            assert len(model_rows) == 2
            assert all(v.model == "deepseek-chat" for v in model_rows)
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_async_pagination(self) -> None:
        engine = _ensure_schema()
        session = _open_session()
        try:
            _seed_fixture_rows(engine)
            bridge = LLMUsageQueryRuntimeBridge(
                query_service=LLMUsageQueryService()
            )

            async def _pages() -> tuple[Any, Any]:
                page1 = await bridge.query_async(
                    LLMUsageQueryFilter(limit=2, offset=0)
                )
                page2 = await bridge.query_async(
                    LLMUsageQueryFilter(limit=2, offset=2)
                )
                return page1, page2

            page1, page2 = asyncio.run(_pages())
            assert len(page1) == 2
            assert len(page2) == 2
            assert not ({v.id for v in page1} & {v.id for v in page2})
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_async_stable_ordering(self) -> None:
        engine = _ensure_schema()
        session = _open_session()
        try:
            _seed_fixture_rows(engine)
            bridge = LLMUsageQueryRuntimeBridge(
                query_service=LLMUsageQueryService()
            )

            async def _list() -> Any:
                return await bridge.query_async(LLMUsageQueryFilter(limit=100))

            views = asyncio.run(_list())
            same_time = [
                v for v in views
                if v.created_at == datetime(2024, 1, 3, tzinfo=timezone.utc)
            ]
            assert [v.request_id for v in same_time] == ["req-004", "req-003"]
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_five_concurrent_async_queries(self) -> None:
        """§十四：5 并发 async query（worker 线程同步执行）→ 结果正确、
        DB 数据不变。"""
        engine = _ensure_schema()
        session = _open_session()
        try:
            rows_before = _seed_fixture_rows(engine)
            bridge = LLMUsageQueryRuntimeBridge(
                query_service=LLMUsageQueryService()
            )

            async def _gather() -> list[Any]:
                return list(
                    await asyncio.gather(
                        *[
                            bridge.get_by_request_id_async(f"req-{n:03d}")
                            for n in range(1, 6)
                        ]
                    )
                )

            results = asyncio.run(_gather())

            assert len(results) == 5
            for n, view in zip(range(1, 6), results):
                assert view is not None
                assert view.request_id == f"req-{n:03d}"
            assert _count(session) == rows_before
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_read_only_after_async_queries(self) -> None:
        engine = _ensure_schema()
        session = _open_session()
        try:
            rows_before = _seed_fixture_rows(engine)
            bridge = LLMUsageQueryRuntimeBridge(
                query_service=LLMUsageQueryService()
            )

            async def _queries() -> None:
                await bridge.query_async()
                await bridge.list_records_async(provider="deepseek")
                await bridge.get_by_request_id_async("req-001")

            asyncio.run(_queries())

            assert _count(session) == rows_before == 5
        finally:
            session.close()
            assert _cleanup(engine) == 0
