"""LLM Usage Persistence Idempotency 测试（Phase 3.10.16）。

目标：证明 Usage Persistence 在**存在 request_id** 时具备
**数据库级幂等**（不是应用内存级）：

    request_id IS NOT NULL → 一个 request_id 最多一行
    request_id IS NULL     → 没有幂等身份，允许每次写入产生新行

覆盖（任务书 §四 ~ §二十九）：

    1.  Idempotency Contract   （幂等 / 不幂等边界）
    2.  Database Index         （UNIQUE PARTIAL INDEX 存在且 constrain NULL 外）
    3.  Sequential Idempotency （3 次相同 request_id → 1 row）
    4.  Concurrent Idempotency （5 并发相同 request_id → 1 row）
    5.  Different request_id   （3 个不同 → 3 rows）
    6.  NULL request_id        （3 次 NULL → 3 rows）
    7.  First-write-wins       （重复不 UPDATE 已有行）
    8.  No DO UPDATE           （编译后的 SQL 断言）
    9.  Persistence Failure    （duplicate 不是错误、不影响业务结果）
    10. Security Whitelist     （写入字段不变）
    11. Runtime unchanged      （bridge / service 不做内存去重）
    12. init_db 重复数据保护    （检测 + 报告 + 停止，不自动清理）

非 DB 部分使用 Fake Repository / Fake Session / 编译后的 SQL：
0 网络 / 0 DB。DB 部分需要 `RUN_DB_TESTS=1`（默认跳过）。
"""
from __future__ import annotations

import asyncio
import ast
import os
import threading
from typing import Any, Callable

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db import init_db as init_db_module
from backend.app.db.init_db import ensure_request_id_idempotency_index
from backend.app.db.llm_usage_repository import (
    LLMUsageRepository,
    LLMUsageRepositoryError,
)
from backend.app.db.models import LLMUsageRecord
from backend.app.db.models.llm_usage_record import (
    LLM_USAGE_REQUEST_ID_INDEX,
    LLM_USAGE_REQUEST_ID_PREDICATE,
    LLM_USAGE_SCHEMA,
)
from backend.app.db.session import get_engine, get_session_factory
from backend.app.llm.client import LLMUsage, OpenAICompatibleClient
from backend.app.llm.deepseek_provider import DeepSeekProvider
from backend.app.llm.observability import LLMObservation
from backend.app.services.llm_usage_persistence_runtime import (
    LLMUsagePersistenceRuntimeBridge,
)
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
    LLMUsagePersistenceService,
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

#: §二十三：Repository 允许写入的字段（精确白名单）。
_APPROVED_WRITE_KEYS = {
    "request_id",
    "provider",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
}

_TRUNCATE_SQL = text(
    f"TRUNCATE TABLE {LLM_USAGE_SCHEMA}.llm_usage_record "
    "RESTART IDENTITY CASCADE"
)


# ============================================================
# Fakes（仅测试使用；0 网络 / 0 DB）
# ============================================================

class FakeResult:
    """`session.execute(...)` 结果替身。"""

    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class FakeTransaction:
    def __enter__(self) -> "FakeTransaction":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class FakeSession:
    """Session 替身：可模拟 INSERT 成功 / ON CONFLICT DO NOTHING / 失败。"""

    def __init__(
        self,
        *,
        return_ids: list[int | None] | None = None,
        raise_error: Exception | None = None,
    ) -> None:
        self.return_ids = list(return_ids) if return_ids is not None else [7]
        self.raise_error = raise_error
        self.executed: list[Any] = []
        self.closed = False

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.close()
        return False

    def begin(self) -> FakeTransaction:
        return FakeTransaction()

    def execute(self, statement: Any) -> FakeResult:
        self.executed.append(statement)
        if self.raise_error is not None:
            raise self.raise_error
        return FakeResult(self.return_ids.pop(0) if self.return_ids else None)

    def close(self) -> None:
        self.closed = True


class RecordingRepository:
    """记录 create() 调用的仓储替身（返回值可脚本化）。"""

    def __init__(self, return_ids: list[int | None] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.threads: list[str] = []
        self.return_ids = list(return_ids) if return_ids is not None else [1]

    def create(self, **kwargs: Any) -> int | None:
        self.calls.append(dict(kwargs))
        self.threads.append(threading.current_thread().name)
        return self.return_ids.pop(0) if self.return_ids else None


# ============================================================
# Helpers
# ============================================================

def _observation(
    *,
    usage: LLMUsage | None = None,
    request_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> LLMObservation:
    return LLMObservation(
        provider=provider,
        model=model,
        success=True,
        usage=usage,
        request_id=request_id,
    )


def _repository(session: FakeSession) -> LLMUsageRepository:
    """用 FakeSession 构造 Repository（factory 必须可调用 → lambda）。"""
    return LLMUsageRepository(
        session_factory=lambda: session  # type: ignore[arg-type]
    )


def _compiled(repository: LLMUsageRepository) -> tuple[str, dict[str, Any]]:
    """取 (编译后的 PostgreSQL SQL, 绑定参数)。"""
    statement = repository.build_idempotent_insert(
        request_id="req-001",
        provider="deepseek-test",
        model="deepseek-chat",
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
    )
    compiled = statement.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


def _ok_body(
    content: str = "answer",
    *,
    request_id: str = "chatcmpl-idem-1",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": request_id,
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }
    body["usage"] = usage or {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
    }
    return body


def _provider(handler: Any, sink: Any) -> DeepSeekProvider:
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="configured-model",
        provider="deepseek-test",
        transport=httpx.MockTransport(handler),
        accounting_sink=sink,
    )
    return DeepSeekProvider(client)


def _run(coro_factory: Callable[[], Any]) -> Any:
    return asyncio.run(coro_factory())


def _module_source(relative_path: str) -> str:
    path = os.path.join(REPO_ROOT, *relative_path.split("/"))
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# ============================================================
# 1 / 8. Idempotency SQL Contract（Fake，0 DB）
# ============================================================

class TestIdempotentInsertContract:
    def test_insert_uses_on_conflict_do_nothing(self) -> None:
        """§六 / §九：必须用 PostgreSQL ON CONFLICT DO NOTHING，
        而不是 SELECT-then-INSERT。"""
        sql, _ = _compiled(LLMUsageRepository(session_factory=None))

        assert "INSERT INTO ai_ops.llm_usage_record" in sql
        assert "ON CONFLICT (request_id)" in sql
        assert LLM_USAGE_REQUEST_ID_PREDICATE in sql      # index 谓词
        assert "DO NOTHING" in sql
        assert "DO UPDATE" not in sql                     # §十：禁止 upsert

    def test_insert_columns_match_security_whitelist(self) -> None:
        """§二十三：幂等不得扩大写入字段（INSERT 列 = 白名单）。"""
        sql, params = _compiled(LLMUsageRepository(session_factory=None))

        assert set(params) == _APPROVED_WRITE_KEYS
        columns_part = sql.split("(", 1)[1].split(")", 1)[0]
        columns = {c.strip() for c in columns_part.split(",")}
        assert columns == _APPROVED_WRITE_KEYS

        forbidden = {
            "prompt", "messages", "system_prompt", "sql", "content",
            "rag_content", "tool_args", "tool_result", "raw_response",
            "headers", "api_key", "password", "authorization",
            "database_url", "cost", "price", "currency",
        }
        assert not (columns & forbidden)

    def test_returning_id_for_conflict_detection(self) -> None:
        """RETURNING id —— 冲突时 PostgreSQL 不返回行（→ None）。"""
        sql, _ = _compiled(LLMUsageRepository(session_factory=None))
        assert "RETURNING" in sql


# ============================================================
# Repository / Service 幂等行为（Fake Session，0 DB）
# ============================================================

class TestRepositoryIdempotencyBehavior:
    def test_create_returns_id_when_row_inserted(self) -> None:
        session = FakeSession(return_ids=[11])
        repository = _repository(session)

        created = repository.create(
            request_id="req-001",
            provider="deepseek-test",
            model="deepseek-chat",
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=3,
        )

        assert created == 11
        assert session.closed is True

    def test_create_returns_none_on_duplicate_request_id(self) -> None:
        """§四：重复 request_id → DO NOTHING → 返回 None（不是异常）。"""
        session = FakeSession(return_ids=[None])
        repository = _repository(session)

        created = repository.create(
            request_id="req-001",
            provider="deepseek-test",
            model="deepseek-chat",
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=3,
        )

        assert created is None
        assert len(session.executed) == 1

    def test_create_still_raises_on_db_failure(self) -> None:
        """非幂等冲突类错误（真失败）仍映射到 LLMUsageRepositoryError。"""
        session = FakeSession(
            raise_error=SQLAlchemyError("simulated db failure")
        )
        repository = _repository(session)

        with pytest.raises(LLMUsageRepositoryError):
            repository.create(
                request_id="req-001",
                provider=None,
                model=None,
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
            )

    def test_service_passes_duplicate_result_through(self) -> None:
        """Service 不把 duplicate 当错误：直接透传 None。"""
        repository = RecordingRepository(return_ids=[1, None, None])
        service = LLMUsagePersistenceService(repository=repository)
        observation = _observation(
            usage=LLMUsage(10, 20, 30), request_id="req-001"
        )

        results = [service.persist(observation) for _ in range(3)]

        assert results == [1, None, None]
        assert len(repository.calls) == 3
        # 三次调用的传值完全一致（幂等不改变 request-scoped 语义）
        assert repository.calls[0] == repository.calls[2]
        assert set(repository.calls[0]) == _APPROVED_WRITE_KEYS

    def test_sink_entries_do_not_raise_on_duplicate(self) -> None:
        """`record()` / `arecord()` 遇到 duplicate 都不抛异常。"""
        repository = RecordingRepository(return_ids=[1, None])
        sink = DatabaseLLMAccountingSink(
            persistence_service=LLMUsagePersistenceService(
                repository=repository
            )
        )
        observation = _observation(
            usage=LLMUsage(1, 2, 3), request_id="req-001"
        )

        sink.record(observation)

        async def _record() -> None:
            await sink.arecord(observation)

        _run(_record)
        assert len(repository.calls) == 2

    def test_duplicate_does_not_change_business_result(self) -> None:
        """§十八：duplicate persistence 不得影响 LLM 业务结果。"""
        http_calls = {"n": 0}
        repository = RecordingRepository(return_ids=[1, None])

        def handler(request: httpx.Request) -> httpx.Response:
            http_calls["n"] += 1
            return httpx.Response(200, json=_ok_body("business answer"))

        sink = DatabaseLLMAccountingSink(
            persistence_service=LLMUsagePersistenceService(
                repository=repository
            )
        )
        provider = _provider(handler, sink)

        async def _chat() -> str:
            return await provider.chat([{"role": "user", "content": "hi"}])

        first = _run(_chat)
        second = _run(_chat)

        assert first == "business answer"
        assert second == "business answer"
        assert http_calls["n"] == 2          # 无 LLM retry
        assert len(repository.calls) == 2    # 重复请求仍然尝试写入，由 DB 忽略


# ============================================================
# 11. Runtime / Application-layer 不得内存去重
# ============================================================

class TestNoApplicationMemoryIdempotency:
    def test_application_layer_does_not_dedupe(self) -> None:
        """§十二 / §二十四：多次相同 request_id 都会到达 Repository
        （幂等判官是 PostgreSQL，不是进程内存）。"""
        repository = RecordingRepository(return_ids=[1] + [None] * 4)
        sink = DatabaseLLMAccountingSink(
            persistence_service=LLMUsagePersistenceService(
                repository=repository
            )
        )
        observation = _observation(
            usage=LLMUsage(1, 1, 2), request_id="req-001"
        )

        async def _record() -> None:
            await asyncio.gather(*[sink.arecord(observation) for _ in range(5)])

        _run(_record)

        assert len(repository.calls) == 5   # 应用层一次都没拦
        assert repository.calls[0]["request_id"] == "req-001"

    def test_no_process_state_in_persistence_modules(self) -> None:
        """静态检查：persistence 链路上没有 request_id set / dict cache /
        lock / lru_cache 之类的进程内状态。"""

        for relative_path in (
            "backend/app/db/llm_usage_repository.py",
            "backend/app/services/llm_usage_persistence_service.py",
            "backend/app/services/llm_usage_persistence_runtime.py",
        ):
            tree = ast.parse(_module_source(relative_path))
            for node in tree.body:  # 只看模块级语句
                if isinstance(node, ast.Assign):
                    targets = [
                        getattr(t, "id", getattr(t, "attr", ""))
                        for t in node.targets
                    ]
                    value = node.value
                    forbidden = isinstance(
                        value, (ast.Dict, ast.Set, ast.ListComp, ast.SetComp,
                                ast.DictComp)
                    )
                    if forbidden and "__all__" not in targets:
                        raise AssertionError(
                            f"{relative_path} 定义了模块级进程状态: {targets}"
                        )
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", getattr(
                        node.func, "attr", ""
                    ))
                    assert name not in {
                        "lru_cache", "Lock", "RLock", "cache",
                    }, f"{relative_path} 引入了进程级缓存/锁: {name}"

    def test_no_cache_dependency(self) -> None:
        """禁止 Redis / cache 依赖（无新依赖）。"""
        for relative_path in (
            "backend/app/db/llm_usage_repository.py",
            "backend/app/services/llm_usage_persistence_service.py",
            "backend/app/services/llm_usage_persistence_runtime.py",
            "backend/app/db/init_db.py",
        ):
            tree = ast.parse(_module_source(relative_path))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
            forbidden = {"redis", "cachetools", "celery", "kafka"}
            assert not any(
                name.split(".")[0] in forbidden for name in imported
            ), imported

    def test_runtime_bridge_contract_unchanged(self) -> None:
        """Runtime Bridge 仍然是"线程边界"，不参与幂等判断。

        * 写入仍然发生在 worker thread（Phase 3.10.15 语义不变）；
        * 重复 request_id 仍然抵达 Repository（由 PostgreSQL 忽略）；
        * bridge 委托的还是同一个 persistence service。
        """
        repository = RecordingRepository(return_ids=[1, None])
        service = LLMUsagePersistenceService(repository=repository)
        bridge = LLMUsagePersistenceRuntimeBridge(persistence_service=service)
        sink = DatabaseLLMAccountingSink(
            persistence_service=service,
            runtime_bridge=bridge,
        )
        observation = _observation(
            usage=LLMUsage(1, 1, 2), request_id="req-001"
        )

        async def _record() -> str:
            loop_thread = threading.current_thread().name
            await sink.arecord(observation)
            await sink.arecord(observation)
            return loop_thread

        loop_thread = _run(_record)

        assert len(repository.calls) == 2
        assert bridge.persistence_service is service
        assert set(repository.threads) != {loop_thread}  # 不在 event loop 线程
        assert hasattr(sink, "record") and hasattr(sink, "arecord")


# ============================================================
# 12. init_db：已有重复数据 → 检测 + 报告 + 停止（不自动清理）
# ============================================================

class _FakeRows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeInspector:
    def __init__(self, has_table: bool) -> None:
        self._has_table = has_table

    def has_table(self, name: str, schema: str | None = None) -> bool:
        return self._has_table


class _FakeConnection:
    def __init__(self, duplicates: list[tuple[Any, ...]]) -> None:
        self.duplicates = duplicates
        self.executed: list[str] = []

    def execute(self, statement: Any) -> _FakeRows:
        sql = str(statement)
        if "COUNT(*)" in sql or "count(*)" in sql:
            return _FakeRows(self.duplicates)
        self.executed.append(sql)
        return _FakeRows([])


class TestExistingDatabaseProtection:
    def test_duplicates_are_reported_and_stop(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """§二十一：已有重复 request_id → 明确报告 + 停止，
        绝不 DELETE / MERGE / UPDATE。"""
        conn = _FakeConnection([("dup-001", 2), ("dup-002", 3)])
        monkeypatch.setattr(
            init_db_module, "inspect", lambda _conn: _FakeInspector(True)
        )

        with pytest.raises(RuntimeError) as excinfo:
            ensure_request_id_idempotency_index(conn)

        message = str(excinfo.value)
        assert "dup-001" in message
        assert "2 条" in message
        assert conn.executed == []        # 没有执行任何 DDL / DELETE

    def test_index_created_when_no_duplicates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """无重复 → 创建 UNIQUE PARTIAL INDEX（幂等 DDL）。"""
        conn = _FakeConnection([])
        monkeypatch.setattr(
            init_db_module, "inspect", lambda _conn: _FakeInspector(True)
        )

        created = ensure_request_id_idempotency_index(conn)

        assert created is True
        assert len(conn.executed) == 1
        sql = conn.executed[0]
        assert "CREATE UNIQUE INDEX IF NOT EXISTS" in sql
        assert LLM_USAGE_REQUEST_ID_INDEX in sql
        assert LLM_USAGE_REQUEST_ID_PREDICATE in sql

    def test_missing_table_skips_ddl(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """表还不存在 → 交给 create_all 随表创建 index（返回 False）。"""
        conn = _FakeConnection([])
        monkeypatch.setattr(
            init_db_module, "inspect", lambda _conn: _FakeInspector(False)
        )

        assert ensure_request_id_idempotency_index(conn) is False
        assert conn.executed == []


# ============================================================
# 2 ~ 10. DB integration（RUN_DB_TESTS=1）
# ============================================================

def _ensure_schema_and_index() -> Any:
    """确保 ai_ops schema + 表 + 幂等 index 存在（幂等，不删数据）。"""
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


def _open_session() -> Session:
    factory = get_session_factory()
    assert factory is not None
    return factory()


def _rows(session: Session) -> list[Any]:
    return list(
        session.scalars(select(LLMUsageRecord).order_by(LLMUsageRecord.id)).all()
    )


def _count(session: Session) -> int:
    return int(
        session.scalar(select(func.count()).select_from(LLMUsageRecord)) or 0
    )


def _cleanup(engine: Any) -> int:
    with engine.begin() as conn:
        conn.execute(_TRUNCATE_SQL)
        return int(
            conn.execute(
                text(f"SELECT COUNT(*) FROM {LLM_USAGE_SCHEMA}.llm_usage_record")
            ).scalar_one()
        )


def _persist_async(
    sink: DatabaseLLMAccountingSink,
    observation: LLMObservation,
) -> None:
    """经 runtime bridge 异步持久化（await → to_thread → DB）。"""

    async def _record() -> None:
        await sink.arecord(observation)

    asyncio.run(_record())


@requires_db
class TestIdempotencyDatabaseStructure:
    def test_unique_partial_index_exists(self) -> None:
        """§二十：必须验证数据库结构（不是只验证业务结果）。"""
        engine = _ensure_schema_and_index()
        with engine.connect() as conn:
            definition = conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = :schema "
                    "AND tablename = :table AND indexname = :index"
                ),
                {
                    "schema": LLM_USAGE_SCHEMA,
                    "table": "llm_usage_record",
                    "index": LLM_USAGE_REQUEST_ID_INDEX,
                },
            ).scalar_one_or_none()
            unique = conn.execute(
                text(
                    "SELECT i.indisunique FROM pg_index i "
                    "JOIN pg_class c ON c.oid = i.indexrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :schema AND c.relname = :index"
                ),
                {"schema": LLM_USAGE_SCHEMA, "index": LLM_USAGE_REQUEST_ID_INDEX},
            ).scalar_one_or_none()

        assert definition is not None, "幂等唯一索引不存在"
        assert unique is True
        assert "UNIQUE" in definition
        assert LLM_USAGE_REQUEST_ID_PREDICATE in definition
        assert LLM_USAGE_REQUEST_ID_INDEX in definition

    def test_table_still_outside_public_schema(self) -> None:
        """§三：不得把 llm_usage_record 移回 public，也不得新建 public 表。"""
        engine = _ensure_schema_and_index()
        with engine.connect() as conn:
            in_public = conn.execute(
                text(
                    "SELECT EXISTS("
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' "
                    "AND table_name='llm_usage_record')"
                )
            ).scalar_one()
        assert in_public is False


@requires_db
class TestIdempotencyDatabaseBehavior:
    def test_sequential_duplicate_keeps_one_row(self) -> None:
        """§十四：3 次相同 request_id → 1 row。"""
        engine = _ensure_schema_and_index()
        session = _open_session()
        try:
            sink = DatabaseLLMAccountingSink()
            for _ in range(3):
                sink.record(
                    _observation(
                        usage=LLMUsage(10, 20, 30),
                        request_id="chatcmpl-idem-seq",
                        provider="deepseek-test",
                        model="deepseek-chat",
                    )
                )
            assert _count(session) == 1
            assert _rows(session)[0].request_id == "chatcmpl-idem-seq"
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_concurrent_duplicate_keeps_one_row(self) -> None:
        """§十三：5 并发相同 request_id（经 runtime bridge）→ 1 row。"""
        engine = _ensure_schema_and_index()
        session = _open_session()
        try:
            sink = DatabaseLLMAccountingSink()
            observation = _observation(
                usage=LLMUsage(10, 20, 30),
                request_id="chatcmpl-idem-concurrent",
                provider="deepseek-test",
                model="deepseek-chat",
            )

            async def _gather() -> None:
                await asyncio.gather(
                    *[sink.arecord(observation) for _ in range(5)]
                )

            asyncio.run(_gather())

            assert _count(session) == 1
            row = _rows(session)[0]
            assert row.request_id == "chatcmpl-idem-concurrent"
            assert row.total_tokens == 30
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_different_request_ids_keep_three_rows(self) -> None:
        """§十五：3 个不同 request_id → 3 rows（不会误判重复）。"""
        engine = _ensure_schema_and_index()
        session = _open_session()
        try:
            sink = DatabaseLLMAccountingSink()
            for n in (1, 2, 3):
                sink.record(
                    _observation(
                        usage=LLMUsage(n, n, 2 * n),
                        request_id=f"chatcmpl-idem-diff-{n}",
                        provider="deepseek-test",
                        model="deepseek-chat",
                    )
                )
            assert _count(session) == 3
            assert {r.request_id for r in _rows(session)} == {
                "chatcmpl-idem-diff-1",
                "chatcmpl-idem-diff-2",
                "chatcmpl-idem-diff-3",
            }
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_null_request_ids_keep_three_rows(self) -> None:
        """§十六：3 次 NULL request_id → 3 rows（NULL 不参与幂等）。"""
        engine = _ensure_schema_and_index()
        session = _open_session()
        try:
            sink = DatabaseLLMAccountingSink()
            for _ in range(3):
                sink.record(
                    _observation(
                        usage=LLMUsage(1, 1, 2),
                        request_id=None,
                        provider="deepseek-test",
                        model="deepseek-chat",
                    )
                )
            assert _count(session) == 3
            assert all(r.request_id is None for r in _rows(session))
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_first_write_wins_no_update(self) -> None:
        """§十七：重复写入不得 UPDATE 已有行数据。"""
        engine = _ensure_schema_and_index()
        session = _open_session()
        try:
            sink = DatabaseLLMAccountingSink()
            first = _observation(
                usage=LLMUsage(10, 20, 30),
                request_id="chatcmpl-idem-fww",
                provider="deepseek-test",
                model="deepseek-chat",
            )
            second = _observation(
                usage=LLMUsage(100, 200, 300),
                request_id="chatcmpl-idem-fww",
                provider="another-provider",
                model="another-model",
            )

            sink.record(first)
            _persist_async(sink, second)   # 异步路径重复写入

            rows = _rows(session)
            assert len(rows) == 1
            row = rows[0]
            assert row.prompt_tokens == 10
            assert row.completion_tokens == 20
            assert row.total_tokens == 30
            assert row.provider == "deepseek-test"
            assert row.model == "deepseek-chat"
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_duplicate_does_not_break_llm_business_result(self) -> None:
        """§十八：duplicate persistence 不破坏 LLM 业务结果。"""
        engine = _ensure_schema_and_index()
        session = _open_session()
        try:
            http_calls = {"n": 0}

            def handler(request: httpx.Request) -> httpx.Response:
                http_calls["n"] += 1
                return httpx.Response(
                    200,
                    json=_ok_body(
                        "business answer",
                        request_id="chatcmpl-idem-biz",
                    ),
                )

            provider = _provider(handler, DatabaseLLMAccountingSink())

            async def _chat() -> str:
                return await provider.chat(
                    [{"role": "user", "content": "hi"}]
                )

            first = asyncio.run(_chat())
            second = asyncio.run(_chat())

            assert first == "business answer"
            assert second == "business answer"
            assert http_calls["n"] == 2        # 无 LLM retry
            assert _count(session) == 1        # DB 级幂等
        finally:
            session.close()
            assert _cleanup(engine) == 0

    def test_public_schema_knowledge_tables_untouched(self) -> None:
        """§二十二：本阶段不得改动 public schema 的业务表数据。"""
        engine = _ensure_schema_and_index()
        before: dict[str, int] = {}
        after: dict[str, int] = {}

        def _counts(target: dict[str, int]) -> None:
            with engine.connect() as conn:
                for table in ("knowledge_document", "knowledge_chunk"):
                    exists = conn.execute(
                        text(
                            "SELECT EXISTS("
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema='public' "
                            "AND table_name=:t)"
                        ),
                        {"t": table},
                    ).scalar_one()
                    if exists:
                        target[table] = int(
                            conn.execute(
                                text(f"SELECT COUNT(*) FROM public.{table}")
                            ).scalar_one()
                        )

        _counts(before)

        session = _open_session()
        try:
            sink = DatabaseLLMAccountingSink()
            sink.record(
                _observation(
                    usage=LLMUsage(1, 1, 2),
                    request_id="chatcmpl-idem-public",
                    provider="deepseek-test",
                    model="deepseek-chat",
                )
            )
            assert _count(session) == 1
        finally:
            session.close()
            _cleanup(engine)
            _counts(after)

        assert before == after
