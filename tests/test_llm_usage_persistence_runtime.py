"""LLM Usage Persistence Runtime Boundary 测试（Phase 3.10.15）。

目标：证明 Phase 3.10.14 的 Usage Persistence 在**异步请求链路**上
运行时安全（不过度阻塞 event loop、Session 不跨线程、失败不外泄）。

覆盖（任务书 §二十九）：

    1.  sync persistence compatibility      （record() 同步契约不变）
    2.  async runtime boundary              （arecord() 走线程边界）
    3.  event loop remains schedulable      （heartbeat 对比测试）
    4.  5 concurrent requests               （5 observations / 5 writes）
    5.  session isolation                   （每 request 独立 Session）
    6.  persistence failure isolation       （业务结果不变）
    7.  persistence failure no retry        （repository calls == 1）
    8.  cancellation behavior               （已完成的业务结果不被破坏）
    9.  default NoopAccountingSink unchanged
    10. DatabaseLLMAccountingSink contract unchanged
    11. security fields unchanged           （字段白名单）
    12. no global Session
    13. no global Connection
    14. no global transaction
    15. deterministic behavior

外加：

    16. DB regression（RUN_DB_TESTS=1）：5 并发真实写入 + 零残留

非 DB 部分全部使用 Fake Repository / Fake Session / MockTransport：
0 网络 / 0 DB。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import os
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.app.config import LLMSettings
from backend.app.db.llm_usage_repository import (
    LLMUsageRepository,
    LLMUsageRepositoryError,
)
from backend.app.db.models import LLMUsageRecord
from backend.app.db.models.llm_usage_record import LLM_USAGE_SCHEMA
from backend.app.db.session import get_engine, get_session_factory
from backend.app.llm.client import (
    LLMResponse,
    LLMUsage,
    MockLLMClient,
    NoopAccountingSink,
    OpenAICompatibleClient,
    create_llm_client,
)
from backend.app.llm.deepseek_provider import DeepSeekProvider
from backend.app.llm.observability import LLMObservation
from backend.app.services.llm_usage_persistence_runtime import (
    LLMUsagePersistenceRuntimeBridge,
)
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
    LLMUsagePersistenceService,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


_RUN_DB = _env_flag("RUN_DB_TESTS")

requires_db = pytest.mark.skipif(
    not _RUN_DB,
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)

#: Persistence Service 允许传给 Repository 的字段（精确白名单）。
#: Phase 3.12 Step 36：+ assistant_request_id（Assistant Trace 关联；
#: 未绑定 Scope → None，仍然是"白名单内的字段"，不引入任何 payload）。
_APPROVED_WRITE_KEYS = {
    "request_id",
    "provider",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "assistant_request_id",
}

_RUNTIME_MODULES = [
    "backend/app/llm/client.py",
    "backend/app/services/llm_usage_persistence_runtime.py",
    "backend/app/services/llm_usage_persistence_service.py",
    "backend/app/db/llm_usage_repository.py",
]


# ============================================================
# Fakes（仅测试使用；0 网络 / 0 DB）
# ============================================================

class FakeRepository:
    """最小仓储替身：记录调用参数 + 执行线程；可注入延迟 / 失败。"""

    def __init__(
        self,
        *,
        delay: float = 0.0,
        fail: bool = False,
        on_call: Callable[[], None] | None = None,
    ) -> None:
        self.delay = delay
        self.fail = fail
        self.on_call = on_call
        self.calls: list[dict[str, Any]] = []
        self.threads: list[str] = []
        self._next_id = 1

    def create(
        self,
        *,
        request_id: str | None,
        provider: str | None,
        model: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        assistant_request_id: str | None = None,
    ) -> int:
        thread_name = threading.current_thread().name
        if self.on_call is not None:
            self.on_call()
        if self.delay:
            time.sleep(self.delay)
        call = {
            "request_id": request_id,
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "assistant_request_id": assistant_request_id,
        }
        self.calls.append(call)
        self.threads.append(thread_name)
        if self.fail:
            raise LLMUsageRepositoryError("simulated persistence failure")
        created = self._next_id
        self._next_id += 1
        return created

    @property
    def call_count(self) -> int:
        return len(self.calls)


class FailingRepository(FakeRepository):
    """始终失败的仓储（失败隔离 / no-retry 测试）。"""

    def __init__(self) -> None:
        super().__init__(fail=True)


class ProbeAccountingSink:
    """探测 Client 如何选择 sink 入口（record vs arecord）。"""

    def __init__(self) -> None:
        self.record_calls = 0
        self.arecord_calls = 0
        self.observations: list[LLMObservation] = []

    def record(self, observation: LLMObservation) -> None:
        self.record_calls += 1
        self.observations.append(observation)

    async def arecord(self, observation: LLMObservation) -> None:
        self.arecord_calls += 1
        self.observations.append(observation)


class SyncOnlySlowSink:
    """只有同步 record() 的 sink（基准：持久化直接在 event loop 跑）。"""

    def __init__(self, delay: float = 0.2) -> None:
        self.delay = delay
        self.calls = 0

    def record(self, observation: LLMObservation) -> None:
        self.calls += 1
        time.sleep(self.delay)


class BoomRepository:
    """抛出非仓储类异常（验证边界同样收敛）。"""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs: Any) -> int:
        self.calls += 1
        raise RuntimeError("unexpected boom")


class FakeTransaction:
    def __enter__(self) -> "FakeTransaction":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class FakeResult:
    """`session.execute(...)` 结果的替身。"""

    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class FakeSession:
    """最小 Session 替身：只提供 Repository 用到的能力。

    Phase 3.10.16 起 Repository 用 Core `INSERT ... ON CONFLICT`
    （`session.execute`），Session 隔离语义不变：
    仍然是一个 Session 一次写入、用完即关。
    """

    def __init__(
        self,
        next_id: Callable[[], int],
        *,
        conflicts: bool = False,
    ) -> None:
        self.created_in = threading.current_thread().name
        self.added: list[Any] = []
        self.executed: list[Any] = []
        self.closed = False
        self.conflicts = conflicts
        self._next_id = next_id

    # Repository 用法：with factory() as session, session.begin():
    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.close()
        return False

    def begin(self) -> FakeTransaction:
        return FakeTransaction()

    def execute(self, statement: Any) -> FakeResult:
        """执行写入语句；conflicts=True 模拟 ON CONFLICT DO NOTHING
        （PostgreSQL 未返回 id → repository 返回 None）。"""
        self.executed.append(statement)
        if self.conflicts:
            return FakeResult(None)
        return FakeResult(self._next_id())

    def add(self, row: Any) -> None:
        self.added.append(row)

    def flush(self) -> None:
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = self._next_id()

    def close(self) -> None:
        self.closed = True


class TrackingSessionFactory:
    """记录每次调用创建了怎样的 Session（隔离断言用）。"""

    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []
        self._lock = threading.Lock()
        self._next = 1000

    def _next_id(self) -> int:
        with self._lock:
            self._next += 1
            return self._next

    def __call__(self) -> FakeSession:
        # 关键：Session 在**调用方线程**（= worker thread）里创建
        session = FakeSession(self._next_id)
        self.sessions.append(session)
        return session


# ============================================================
# Helpers
# ============================================================

def _observation(
    *,
    usage: LLMUsage | None = None,
    request_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    success: bool = True,
) -> LLMObservation:
    return LLMObservation(
        provider=provider,
        model=model,
        success=success,
        usage=usage,
        request_id=request_id,
    )


def _content_body(
    content: str | None,
    *,
    request_id: str = "chatcmpl-rt-1",
    finish_reason: str = "stop",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": request_id,
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
    }
    if usage is not None:
        body["usage"] = usage
    return body


def _ok_handler(
    content: str = "answer",
    *,
    request_id: str = "chatcmpl-rt-1",
    prompt: int = 1,
    completion: int = 1,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_content_body(
                content,
                request_id=request_id,
                usage={
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                },
            ),
        )

    return handler


def _provider(handler: Any, sink: Any = None) -> DeepSeekProvider:
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="configured-model",
        provider="deepseek-test",
        transport=httpx.MockTransport(handler),
        accounting_sink=sink,
    )
    return DeepSeekProvider(client)


def _sink(repository: Any) -> DatabaseLLMAccountingSink:
    return DatabaseLLMAccountingSink(
        persistence_service=LLMUsagePersistenceService(repository=repository)
    )


def _run(coro_factory: Callable[[], Any]) -> Any:
    """在一次独立 event loop 里执行 coroutine（与既有测试风格一致）。"""
    return asyncio.run(coro_factory())


def _max_gap(ticks: list[float]) -> float:
    if len(ticks) < 2:
        return 0.0
    return max(b - a for a, b in zip(ticks, ticks[1:]))


def _run_with_heartbeat(
    coro: Callable[[], Any],
    *,
    interval: float = 0.02,
) -> tuple[Any, list[float]]:
    """并发跑一个 heartbeat task，返回业务结果 + heartbeat tick 时间戳。"""

    async def _main() -> tuple[Any, list[float]]:
        ticks: list[float] = []
        stop = asyncio.Event()

        async def heartbeat() -> None:
            while not stop.is_set():
                ticks.append(time.perf_counter())
                await asyncio.sleep(interval)

        hb = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)          # 确保 heartbeat 先起来
        result = await coro()
        # 最后补一个采样点：业务协程结束时刻 —— 与上一个 heartbeat tick
        # 的间隔即"event loop 最长停止调度时间"（无需精确 benchmark）。
        ticks.append(time.perf_counter())
        stop.set()
        hb.cancel()
        with suppress(asyncio.CancelledError):
            await hb
        return result, ticks

    return asyncio.run(_main())


def _module_source(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _statement_values(statement: Any) -> dict[str, Any]:
    """把 Repository 交给 session.execute 的 INSERT 语句还原成字段 dict
    （Phase 3.10.16 起写入走 Core `INSERT ... ON CONFLICT`）。"""
    from sqlalchemy.dialects import postgresql

    compiled = statement.compile(dialect=postgresql.dialect())
    return dict(compiled.params)


# ============================================================
# 1. sync persistence compatibility
# ============================================================

class TestSyncPersistenceCompatibility:
    def test_record_still_works_synchronously(self) -> None:
        """§十三：既有同步入口 sink.record(observation) 行为完全不变
        （同步调用、立即写入、返回 None）。"""
        repo = FakeRepository()
        sink = _sink(repo)

        result = sink.record(
            _observation(
                usage=LLMUsage(10, 2, 12),
                request_id="chatcmpl-sync",
                provider="deepseek-test",
                model="deepseek-chat",
            )
        )

        assert result is None
        assert repo.call_count == 1
        assert repo.calls[0]["request_id"] == "chatcmpl-sync"
        # 同步入口仍在当前线程内执行（不进入线程边界）
        assert repo.threads[0] == threading.current_thread().name

    def test_record_is_not_coroutine_function(self) -> None:
        """同步契约不得变成 await-only API（禁止 breaking change）。"""
        sink = _sink(FakeRepository())
        assert callable(sink.record)
        assert not inspect.iscoroutinefunction(sink.record)
        assert inspect.iscoroutinefunction(sink.arecord)

    def test_sync_sink_still_used_by_client(self) -> None:
        """Client 对只有 record() 的 sink 行为不变（仍恰好一次 emit）。"""
        slow_sink = SyncOnlySlowSink(delay=0.0)
        provider = _provider(_ok_handler("answer"), slow_sink)

        async def _chat() -> str:
            return await provider.chat([{"role": "user", "content": "hi"}])

        answer = _run(_chat)

        assert answer == "answer"
        assert slow_sink.calls == 1


# ============================================================
# 2. async runtime boundary
# ============================================================

class TestAsyncRuntimeBoundary:
    def test_arecord_persists_same_values_as_record(self) -> None:
        repos = (FakeRepository(), FakeRepository())
        sync_sink, async_sink = (_sink(r) for r in repos)
        observation = _observation(
            usage=LLMUsage(100, 20, 120),
            request_id="chatcmpl-async",
            provider="deepseek-test",
            model="deepseek-chat",
        )

        def _sync() -> None:
            sync_sink.record(observation)

        async def _async() -> None:
            await async_sink.arecord(observation)

        _sync()
        _run(_async)

        assert repos[0].calls == repos[1].calls
        assert repos[0].call_count == 1
        assert repos[1].call_count == 1

    def test_arecord_runs_outside_event_loop_thread(self) -> None:
        """线程边界：同步 DB 写入不得发生在 event loop 线程。"""
        repo = FakeRepository()
        sink = _sink(repo)

        async def _record() -> str:
            loop_thread = threading.current_thread().name
            await sink.arecord(
                _observation(usage=LLMUsage(1, 1, 2), request_id="r-th")
            )
            return loop_thread

        loop_thread = _run(_record)

        assert len(repo.threads) == 1
        assert repo.threads[0] != loop_thread
        assert repo.threads[0] != threading.main_thread().name

    def test_usage_none_writes_nothing_on_async_path(self) -> None:
        """usage=None → 不写入（与同步入口语义一致，本表不是 audit log）。"""
        repo = FakeRepository()
        sink = _sink(repo)

        async def _record() -> None:
            await sink.arecord(_observation(usage=None, request_id="r-none"))

        _run(_record)
        assert repo.call_count == 0

    def test_client_prefers_arecord_when_available(self) -> None:
        """Client 在 sink 提供 arecord() 时走异步边界（不再调 record()）。"""
        probe = ProbeAccountingSink()
        provider = _provider(_ok_handler("answer"), probe)

        async def _chat() -> str:
            return await provider.chat([{"role": "user", "content": "hi"}])

        answer = _run(_chat)

        assert answer == "answer"
        assert probe.arecord_calls == 1      # 走线程边界
        assert probe.record_calls == 0       # 未退化为同步调用
        assert len(probe.observations) == 1  # 仍然恰好一次 emit

    def test_bridge_uses_the_same_persistence_service(self) -> None:
        """Bridge 不做第二套持久化：内部就是同一个 service。"""
        service = LLMUsagePersistenceService(repository=FakeRepository())
        sink = DatabaseLLMAccountingSink(persistence_service=service)
        assert sink._bridge.persistence_service is service

        bridge = LLMUsagePersistenceRuntimeBridge(persistence_service=service)
        assert bridge.persistence_service is service

    def test_bridge_rejects_invalid_service(self) -> None:
        """依赖显式：没有 persist() 的对象不得注入 bridge。"""
        with pytest.raises(TypeError):
            LLMUsagePersistenceRuntimeBridge(
                persistence_service=None  # type: ignore[arg-type]
            )
        with pytest.raises(TypeError):
            LLMUsagePersistenceRuntimeBridge(
                persistence_service=object()  # type: ignore[arg-type]
            )


# ============================================================
# 3. event loop remains schedulable（本阶段核心测试）
# ============================================================

class TestEventLoopRemainsSchedulable:
    def test_direct_sync_persistence_blocks_the_loop(self) -> None:
        """基准：持久化直接在 event loop 里跑 → heartbeat 明显停摆。"""
        slow_sink = SyncOnlySlowSink(delay=0.2)
        provider = _provider(_ok_handler("answer"), slow_sink)

        async def _chat() -> str:
            return await provider.chat([{"role": "user", "content": "hi"}])

        answer, ticks = _run_with_heartbeat(_chat)

        assert answer == "answer"
        # 同步 200ms 期间 heartbeat 无法推进 → 最大间隔显著变大
        assert _max_gap(ticks) >= 0.15, f"expected blocking, got {_max_gap(ticks)}"

    def test_runtime_bridge_keeps_loop_schedulable(self) -> None:
        """Phase 3.10.15：同步持久化进入线程边界 → heartbeat 继续跑。"""
        repo = FakeRepository(delay=0.2)
        provider = _provider(_ok_handler("answer"), _sink(repo))

        async def _chat() -> str:
            return await provider.chat([{"role": "user", "content": "hi"}])

        answer, ticks = _run_with_heartbeat(_chat)

        assert answer == "answer"
        assert repo.call_count == 1
        # 持久化期间 event loop 仍可调度：heartbeat 多次推进
        assert len(ticks) >= 4, f"heartbeat stalled: {len(ticks)} ticks"
        assert _max_gap(ticks) < 0.1, f"loop blocked too long: {_max_gap(ticks)}"

    def test_bridged_is_better_than_direct(self) -> None:
        """轻量验证（非 benchmark）：同一 persistence 延迟下，
        bridge 的心跳推进明显好于直接同步写入。"""
        direct_provider = _provider(
            _ok_handler("answer"), SyncOnlySlowSink(delay=0.25)
        )

        async def _direct() -> Any:
            return await direct_provider.chat(
                [{"role": "user", "content": "hi"}]
            )

        _, direct_ticks = _run_with_heartbeat(_direct)

        repo = FakeRepository(delay=0.25)
        bridged_provider = _provider(_ok_handler("answer"), _sink(repo))

        async def _bridged() -> Any:
            return await bridged_provider.chat(
                [{"role": "user", "content": "hi"}]
            )

        _, bridged_ticks = _run_with_heartbeat(_bridged)

        assert len(bridged_ticks) > len(direct_ticks)
        assert _max_gap(bridged_ticks) < _max_gap(direct_ticks)


# ============================================================
# 4. concurrency（5 requests）
# ============================================================

class TestConcurrency:
    def test_five_concurrent_requests_five_persistence_ops(self) -> None:
        """5 并发 LLM 请求 → 5 observations / 5 persistence ops，
        request_id 与 token 各自正确，无串线。"""
        counter = {"n": 0}
        lock = threading.Lock()

        def handler(request: httpx.Request) -> httpx.Response:
            with lock:
                counter["n"] += 1
                n = counter["n"]
            return httpx.Response(
                200,
                json=_content_body(
                    f"answer-{n}",
                    request_id=f"chatcmpl-conc-{n}",
                    usage={"prompt_tokens": 100 * n,
                           "completion_tokens": 10 * n,
                           "total_tokens": 110 * n},
                ),
            )

        repo = FakeRepository(delay=0.05)
        provider = _provider(handler, _sink(repo))

        async def _gather() -> list[Any]:
            return list(
                await asyncio.gather(
                    *[
                        provider.chat([{"role": "user", "content": f"q-{i}"}])
                        for i in range(5)
                    ]
                )
            )

        answers = _run(_gather)

        assert len(answers) == 5
        assert repo.call_count == 5

        by_request_id = {c["request_id"]: c for c in repo.calls}
        assert set(by_request_id) == {
            f"chatcmpl-conc-{n}" for n in range(1, 6)
        }
        for n in range(1, 6):
            call = by_request_id[f"chatcmpl-conc-{n}"]
            assert call["prompt_tokens"] == 100 * n
            assert call["completion_tokens"] == 10 * n
            assert call["total_tokens"] == 110 * n
            assert call["model"] == "deepseek-chat"

    def test_concurrent_persistence_never_runs_on_loop_thread(self) -> None:
        """并发路径下，所有写入都在 worker 线程（不在 event loop 线程）。"""
        repo = FakeRepository(delay=0.05)
        provider = _provider(_ok_handler("answer"), _sink(repo))

        async def _gather() -> tuple[str, list[Any]]:
            loop_thread = threading.current_thread().name
            results = await asyncio.gather(
                *[
                    provider.chat([{"role": "user", "content": f"q-{i}"}])
                    for i in range(5)
                ]
            )
            return loop_thread, list(results)

        loop_thread, results = _run(_gather)

        assert len(results) == 5
        assert repo.call_count == 5
        assert loop_thread not in repo.threads
        assert threading.main_thread().name not in repo.threads


# ============================================================
# 5. session / thread isolation
# ============================================================

class TestSessionIsolation:
    def test_each_persistence_uses_its_own_session(self) -> None:
        """每次持久化 → 独立 Session（在 worker 线程内创建 + 关闭）。"""
        factory = TrackingSessionFactory()
        repo = LLMUsageRepository(session_factory=factory)
        sink = DatabaseLLMAccountingSink(
            persistence_service=LLMUsagePersistenceService(repository=repo)
        )

        async def _record() -> None:
            for n in range(1, 6):
                await sink.arecord(
                    _observation(
                        usage=LLMUsage(n, n, 2 * n),
                        request_id=f"chatcmpl-sess-{n}",
                        provider="deepseek-test",
                        model="deepseek-chat",
                    )
                )

        _run(_record)

        assert len(factory.sessions) == 5
        # 5 个互不相同的 Session 对象（无共享 / 无复用）
        assert len({id(s) for s in factory.sessions}) == 5
        for session in factory.sessions:
            assert len(session.executed) == 1     # 一个 Session 一次写入
            assert session.closed is True         # 已关闭（无泄漏）
            assert session.created_in != threading.main_thread().name

    def test_concurrent_requests_do_not_share_session(self) -> None:
        """并发：request A → Session A / request B → Session B。"""
        factory = TrackingSessionFactory()
        repo = LLMUsageRepository(session_factory=factory)
        sink = DatabaseLLMAccountingSink(
            persistence_service=LLMUsagePersistenceService(repository=repo)
        )

        async def _gather() -> None:
            await asyncio.gather(
                *[
                    sink.arecord(
                        _observation(
                            usage=LLMUsage(n, n, 2 * n),
                            request_id=f"chatcmpl-par-{n}",
                            provider="deepseek-test",
                            model="deepseek-chat",
                        )
                    )
                    for n in range(1, 6)
                ]
            )

        _run(_gather)

        assert len(factory.sessions) == 5
        assert len({id(s) for s in factory.sessions}) == 5
        for session in factory.sessions:
            assert session.created_in != threading.main_thread().name
            assert session.closed is True

    def test_no_cross_contamination_between_concurrent_requests(self) -> None:
        """并发写入互不污染：每行与该次 observation 严格对应。"""
        factory = TrackingSessionFactory()
        repo = LLMUsageRepository(session_factory=factory)
        sink = DatabaseLLMAccountingSink(
            persistence_service=LLMUsagePersistenceService(repository=repo)
        )

        async def _gather() -> None:
            await asyncio.gather(
                *[
                    sink.arecord(
                        _observation(
                            usage=LLMUsage(10 * n, 2 * n, 12 * n),
                            request_id=f"chatcmpl-clean-{n}",
                            provider=f"provider-{n}",
                            model=f"model-{n}",
                        )
                    )
                    for n in range(1, 6)
                ]
            )

        _run(_gather)

        written = [
            (
                values["request_id"],
                values["provider"],
                values["model"],
                values["prompt_tokens"],
                values["completion_tokens"],
                values["total_tokens"],
            )
            for values in (
                _statement_values(session.executed[0])
                for session in factory.sessions
            )
        ]
        expected = [
            (
                f"chatcmpl-clean-{n}",
                f"provider-{n}",
                f"model-{n}",
                10 * n,
                2 * n,
                12 * n,
            )
            for n in range(1, 6)
        ]
        assert sorted(written) == sorted(expected)


# ============================================================
# 6 / 7. persistence failure isolation + no retry
# ============================================================

class TestPersistenceFailure:
    def test_repository_failure_keeps_business_result(self) -> None:
        """Repository 抛错 → LLM 业务结果不变，异常不外泄。"""
        http_calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            http_calls["n"] += 1
            return httpx.Response(
                200,
                json=_content_body(
                    "business answer",
                    request_id="chatcmpl-fail",
                    usage={"prompt_tokens": 10, "completion_tokens": 2,
                           "total_tokens": 12},
                ),
            )

        repo = FailingRepository()
        provider = _provider(handler, _sink(repo))

        async def _chat() -> str:
            return await provider.chat([{"role": "user", "content": "hi"}])

        answer = _run(_chat)

        assert answer == "business answer"    # 业务结果不变
        assert http_calls["n"] == 1           # 无 LLM retry
        assert repo.call_count == 1           # 无 persistence retry

    def test_arecord_does_not_raise(self) -> None:
        """线程边界内的异常必须被 persistence boundary 捕获。"""
        sink = _sink(FailingRepository())

        async def _record() -> None:
            await sink.arecord(
                _observation(usage=LLMUsage(1, 1, 2), request_id="r-fail")
            )

        _run(_record)   # 不抛异常（warning 级别）

    def test_unexpected_exception_also_isolated(self) -> None:
        """非仓储异常（如未知 RuntimeError）同样不外泄，且无 retry。"""
        boom = BoomRepository()

        async def _record() -> None:
            await _sink(boom).arecord(
                _observation(usage=LLMUsage(1, 1, 2), request_id="r-boom")
            )

        _run(_record)
        assert boom.calls == 1                # 恰好一次，无 backoff / 重试

    def test_failure_does_not_trigger_llm_retry(self) -> None:
        """§十七 / §十九：DB 失败不得进入 LLM retry / persistence retry。"""
        http_calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            http_calls["n"] += 1
            return httpx.Response(
                200,
                json=_content_body(
                    "answer",
                    request_id="chatcmpl-retry",
                    usage={"prompt_tokens": 3, "completion_tokens": 1,
                           "total_tokens": 4},
                ),
            )

        repo = FailingRepository()
        provider = _provider(handler, _sink(repo))

        async def _chat() -> str:
            return await provider.chat([{"role": "user", "content": "hi"}])

        answer = _run(_chat)

        assert answer == "answer"
        assert http_calls["n"] == 1           # LLM retry = 0
        assert repo.call_count == 1           # persistence retry = 0


# ============================================================
# 8. cancellation behavior
# ============================================================

class TestCancellation:
    def test_sink_absorbs_cancellation(self) -> None:
        """持久化过程中被取消 → 不向外抛 CancelledError
        （persistence boundary 内收敛为 warning）。"""
        sink = _sink(FakeRepository(delay=0.3))

        async def _record() -> str:
            task = asyncio.create_task(
                sink.arecord(
                    _observation(usage=LLMUsage(1, 1, 2), request_id="r-cancel")
                )
            )
            await asyncio.sleep(0.05)        # 让持久化进入 worker thread
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                return "cancelled"
            return "completed"

        assert _run(_record) == "completed"

    def test_cancellation_does_not_destroy_business_result(self) -> None:
        """§十八：LLM 结果已完成 + persistence 运行中 + caller cancel
        → 已完成的 LLM Business Result 不被破坏（仍返回业务答案）。"""

        async def _scenario() -> tuple[str | None, bool, int]:
            loop = asyncio.get_running_loop()
            started = asyncio.Event()

            repo = FakeRepository(
                delay=0.3,
                on_call=lambda: loop.call_soon_threadsafe(started.set),
            )
            provider = _provider(_ok_handler("answer-cancel"), _sink(repo))

            task = asyncio.create_task(
                provider.chat([{"role": "user", "content": "hi"}])
            )
            await asyncio.wait_for(started.wait(), timeout=2.0)  # 持久化已开始
            task.cancel()
            answer: str | None
            cancelled = False
            try:
                answer = await task
            except asyncio.CancelledError:
                answer = None
                cancelled = True
            # worker thread 在自己独立的事务里自然结束
            await asyncio.sleep(0.5)
            return answer, cancelled, repo.call_count

        answer, cancelled, calls = _run(_scenario)

        assert cancelled is False
        assert answer == "answer-cancel"      # 业务结果未被持久化取消破坏
        assert calls == 1                     # 写入没有被重复执行

    def test_llm_response_object_is_not_mutated_by_persistence(self) -> None:
        """取消 / 失败都不修改 LLMResponse（business result 保持原值）。"""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_inventory",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        handler = lambda request: httpx.Response(  # noqa: E731
            200,
            json=_content_body(
                "tool path",
                request_id="chatcmpl-identity",
                finish_reason="tool_calls",
                usage={"prompt_tokens": 4, "completion_tokens": 4,
                       "total_tokens": 8},
            ),
        )
        provider = _provider(handler, _sink(FailingRepository()))

        async def _chat() -> Any:
            return await provider.chat(
                [{"role": "user", "content": "hi"}], tools=tools
            )

        response = _run(_chat)

        assert isinstance(response, LLMResponse)
        assert response.content == "tool path"
        assert response.usage is not None
        assert response.usage.total_tokens == 8   # usage 未被持久化改写


# ============================================================
# 9 / 10. default path + sink contract unchanged
# ============================================================

class TestDefaultAndContract:
    def test_default_accounting_sink_is_still_noop(self) -> None:
        """§二十二：默认 Client / 工厂仍是 NoopAccountingSink
        （普通开发运行不会写 ai_ops.llm_usage_record）。"""
        client = OpenAICompatibleClient(
            api_key="k",
            base_url="https://api.example.com/v1",
            model="m",
            provider="deepseek-test",
        )
        assert isinstance(client._accounting_sink, NoopAccountingSink)
        # Noop sink 不提供异步入口 → Client 退回原同步路径（行为不变）
        assert not hasattr(client._accounting_sink, "arecord")

        mock_provider = create_llm_client(
            LLMSettings(api_key="", base_url="b", model="m")
        )
        assert isinstance(mock_provider, MockLLMClient)

    def test_noop_sink_writes_nothing(self) -> None:
        """默认路径下不会触碰任何持久化构造。"""
        provider = _provider(_ok_handler("answer"))   # 不注入 sink

        async def _chat() -> str:
            return await provider.chat([{"role": "user", "content": "hi"}])

        assert _run(_chat) == "answer"

    def test_mock_llm_client_behavior_unchanged(self) -> None:
        """§二十三：MockLLMClient 不因本阶段新增 fake usage / request id /
        cost 等数据。"""
        mock = MockLLMClient()

        async def _chat() -> Any:
            return await mock.chat(
                [{"role": "user", "content": "hi"}], tools=[]
            )

        response = _run(_chat)

        assert isinstance(response, LLMResponse)
        assert response.usage is None           # 不伪造 usage
        assert response.model is None           # 不伪造 model
        assert response.metadata == {"provider": "mock"}

    def test_sink_accepts_positional_observation(self) -> None:
        """Contract：record / arecord 都是单参数 observation 入口。"""
        repo = FakeRepository()
        sink = _sink(repo)
        observation = _observation(
            usage=LLMUsage(1, 1, 2), request_id="r-pos"
        )

        sink.record(observation)

        async def _record() -> None:
            await sink.arecord(observation)

        _run(_record)

        assert repo.call_count == 2
        assert all(c["request_id"] == "r-pos" for c in repo.calls)


# ============================================================
# 11. security fields unchanged
# ============================================================

class TestSecurityFields:
    def test_persistence_receives_only_approved_fields(self) -> None:
        """两条入口传给 Repository 的字段集完全相同且精确等于白名单。"""
        sync_repo = FakeRepository()
        async_repo = FakeRepository()
        observation = _observation(
            usage=LLMUsage(11, 22, 33),
            request_id="chatcmpl-sec",
            provider="deepseek-test",
            model="deepseek-chat",
        )

        async def _record() -> None:
            await _sink(async_repo).arecord(observation)

        _sink(sync_repo).record(observation)
        _run(_record)

        assert set(sync_repo.calls[0]) == _APPROVED_WRITE_KEYS
        assert sync_repo.calls[0] == async_repo.calls[0]

    def test_no_sensitive_fields_reach_repository(self) -> None:
        """不得出现 prompt / SQL / RAG / tool / secret / cost 等字段。"""
        repo = FakeRepository()
        sink = _sink(repo)

        async def _record() -> None:
            await sink.arecord(
                _observation(
                    usage=LLMUsage(1, 2, 3),
                    request_id="chatcmpl-sec2",
                    provider="deepseek-test",
                    model="deepseek-chat",
                )
            )

        _run(_record)

        forbidden = {
            "prompt", "system_prompt", "messages", "sql", "query",
            "rag_content", "chunk", "tool_args", "tool_result",
            "raw_response", "headers", "api_key", "password",
            "authorization", "database_url", "exception", "error_message",
            "cost", "price", "currency", "input_cost", "output_cost",
        }
        assert not (set(repo.calls[0]) & forbidden)

    def test_runtime_bridge_has_no_db_dependency(self) -> None:
        """Runtime boundary 不得让 llm 层反向依赖 DB：
        新模块不 import sqlalchemy / psycopg / repository。"""
        runtime_source = _module_source(
            "backend/app/services/llm_usage_persistence_runtime.py"
        )
        tree = ast.parse(runtime_source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {"sqlalchemy", "psycopg", "redis", "kafka", "celery"}
        assert not any(name.split(".")[0] in forbidden for name in imported)

        # observability 仍然不知道数据库存在（§二十四）
        observability_source = _module_source("backend/app/llm/observability.py")
        obs_tree = ast.parse(observability_source)
        obs_imported: set[str] = set()
        for node in ast.walk(obs_tree):
            if isinstance(node, ast.Import):
                obs_imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                obs_imported.add(node.module)
        assert not any(
            name.split(".")[0] in forbidden | {"backend.app.db"}
            for name in obs_imported
        )


# ============================================================
# 12 / 13 / 14. no global Session / Connection / transaction
#              + no fire-and-forget
# ============================================================

class TestNoSharedRuntimeResources:
    def test_no_global_session_or_connection_or_transaction(self) -> None:
        """静态检查：persistence 运行时链路上不存在模块级
        Session / Connection / transaction 对象。"""
        forbidden_factories = {
            "Session", "SessionLocal", "sessionmaker", "create_engine",
            "Connection", "Transaction", "AsyncSession", "create_async_engine",
        }
        for relative_path in _RUNTIME_MODULES:
            tree = ast.parse(_module_source(relative_path))
            calls: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    calls.append(node.func.id)
                if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Attribute
                ):
                    calls.append(node.func.attr)
            offenders = {c for c in calls if c in forbidden_factories}
            assert not offenders, f"{relative_path} 存在全局 DB 资源: {offenders}"

    def test_no_module_level_db_object_assignment(self) -> None:
        """模块级（global）不得绑定 Session / engine 实例。"""
        forbidden_factories = {
            "Session", "SessionLocal", "sessionmaker", "create_engine",
            "Connection", "create_async_engine",
        }
        for relative_path in _RUNTIME_MODULES:
            tree = ast.parse(_module_source(relative_path))
            for node in tree.body:  # 只看模块级语句
                targets: list[ast.expr] = []
                value: ast.expr | None = None
                if isinstance(node, ast.Assign):
                    targets = list(node.targets)
                    value = node.value
                elif isinstance(node, ast.AnnAssign):
                    targets = [node.target]
                    value = node.value
                if value is None or not isinstance(value, ast.Call):
                    continue
                func_name = getattr(value.func, "id", getattr(
                    value.func, "attr", ""
                ))
                if func_name in forbidden_factories:
                    names = [
                        getattr(t, "id", getattr(t, "attr", ""))
                        for t in targets
                    ]
                    raise AssertionError(
                        f"{relative_path} 定义了模块级 DB 对象: {names}"
                    )

    def test_no_fire_and_forget_persistence(self) -> None:
        """禁止 create_task / ensure_future 偷偷后台持久化。"""
        for relative_path in _RUNTIME_MODULES:
            tree = ast.parse(_module_source(relative_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    attr = getattr(func, "attr", None)
                    name = getattr(func, "id", None)
                    assert attr not in {"create_task", "ensure_future"}, (
                        f"{relative_path} 使用了 fire-and-forget: {attr}"
                    )
                    assert name not in {"create_task", "ensure_future"}, (
                        f"{relative_path} 使用了 fire-and-forget: {name}"
                    )

    def test_thread_boundary_is_only_in_runtime_bridge(self) -> None:
        """线程边界集中在 runtime bridge：其余模块不得自行 to_thread。"""

        def _has_to_thread_call(relative_path: str) -> bool:
            tree = ast.parse(_module_source(relative_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(
                    node.func, ast.Attribute
                ):
                    if node.func.attr == "to_thread":
                        return True
            return False

        assert _has_to_thread_call(
            "backend/app/services/llm_usage_persistence_runtime.py"
        )
        for relative_path in (
            "backend/app/llm/client.py",
            "backend/app/services/llm_usage_persistence_service.py",
            "backend/app/db/llm_usage_repository.py",
        ):
            assert not _has_to_thread_call(relative_path), (
                f"{relative_path} 不应自行建立线程边界"
            )


# ============================================================
# 15. deterministic behavior
# ============================================================

class TestDeterministicBehavior:
    def test_same_observation_yields_same_written_values(self) -> None:
        """同一 observation 多次持久化 → 写入值完全一致（无随机性）。"""
        observation = _observation(
            usage=LLMUsage(None, 200, None),
            request_id="chatcmpl-det",
            provider="deepseek-test",
            model="deepseek-chat",
        )
        first = FakeRepository()
        second = FakeRepository()

        async def _record(repo: FakeRepository) -> None:
            for _ in range(3):
                await _sink(repo).arecord(observation)

        _run(lambda: _record(first))
        _run(lambda: _record(second))

        assert first.calls == second.calls
        assert len(first.calls) == 3
        for call in first.calls:
            assert call == {
                "request_id": "chatcmpl-det",
                "provider": "deepseek-test",
                "model": "deepseek-chat",
                "prompt_tokens": None,     # NULL ≠ 0（partial usage 原样）
                "completion_tokens": 200,
                "total_tokens": None,
                # Step 36：未绑定 Assistant Trace Scope → NULL（不生成 / 不回填）
                "assistant_request_id": None,
            }

    def test_partial_usage_stays_null_on_async_path(self) -> None:
        """异步路径同样保持 NULL ≠ 0 语义（不补 0、不重算 total）。"""
        repo = FakeRepository()
        sink = _sink(repo)

        async def _record() -> None:
            await sink.arecord(
                _observation(
                    usage=LLMUsage(completion_tokens=200),
                    request_id="chatcmpl-partial",
                    provider="deepseek-test",
                    model="deepseek-chat",
                )
            )

        _run(_record)

        call = repo.calls[0]
        assert call["prompt_tokens"] is None
        assert call["total_tokens"] is None
        assert call["completion_tokens"] == 200


# ============================================================
# 16. DB regression（RUN_DB_TESTS=1）
# ============================================================

_TRUNCATE_SQL = text(
    f"TRUNCATE TABLE {LLM_USAGE_SCHEMA}.llm_usage_record "
    "RESTART IDENTITY CASCADE"
)


@requires_db
class TestRuntimeBridgeWithDatabase:
    """真实 PostgreSQL 上的 runtime boundary 回归（默认跳过）。

    自带 engine / schema 准备 + 测试后 TRUNCATE：
    不定义 module-level fixture，避免影响本文件以外的测试。
    """

    @staticmethod
    def _engine() -> Any:
        """确保 ai_ops schema + 表存在（幂等）。"""
        from backend.app.db import models  # noqa: F401
        from backend.app.db.base import Base

        engine = get_engine()
        assert engine is not None, (
            "DATABASE_URL 未配置；请配置后启用 RUN_DB_TESTS=1"
        )
        with engine.begin() as conn:
            conn.execute(
                text(f"CREATE SCHEMA IF NOT EXISTS {LLM_USAGE_SCHEMA}")
            )
        Base.metadata.create_all(engine)
        return engine

    @staticmethod
    def _rows(session: Session) -> list[Any]:
        return list(
            session.scalars(
                select(LLMUsageRecord).order_by(LLMUsageRecord.id)
            ).all()
        )

    @staticmethod
    def _count(session: Session) -> int:
        return int(
            session.scalar(
                select(func.count()).select_from(LLMUsageRecord)
            )
            or 0
        )

    def test_five_concurrent_persistence_rows(self) -> None:
        """5 并发持久化（经 runtime bridge）→ 5 行，
        request_id / token 全部对应，收尾 DB residue = 0。"""
        engine = self._engine()
        factory = get_session_factory()
        assert factory is not None
        sink = DatabaseLLMAccountingSink()

        async def _gather() -> None:
            await asyncio.gather(
                *[
                    sink.arecord(
                        _observation(
                            usage=LLMUsage(100 * n, 10 * n, 110 * n),
                            request_id=f"chatcmpl-db-{n}",
                            provider="deepseek-test",
                            model="deepseek-chat",
                        )
                    )
                    for n in range(1, 6)
                ]
            )

        session = factory()
        try:
            asyncio.run(_gather())

            rows = self._rows(session)
            assert len(rows) == 5
            by_request_id = {r.request_id: r for r in rows}
            assert set(by_request_id) == {
                f"chatcmpl-db-{n}" for n in range(1, 6)
            }
            for n in range(1, 6):
                row = by_request_id[f"chatcmpl-db-{n}"]
                assert row.prompt_tokens == 100 * n
                assert row.completion_tokens == 10 * n
                assert row.total_tokens == 110 * n
                assert row.model == "deepseek-chat"
        finally:
            session.close()
            # DB residue = 0
            with engine.begin() as conn:
                conn.execute(_TRUNCATE_SQL)

        with engine.begin() as conn:
            remaining = int(
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM "
                        f"{LLM_USAGE_SCHEMA}.llm_usage_record"
                    )
                ).scalar_one()
            )
        assert remaining == 0
