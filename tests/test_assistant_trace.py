"""Assistant Trace Scope 单元测试（Phase 3.12 Step 36）。

层次：

    assistant_trace_scope / current_assistant_request_id（contextvar）
        ↓
    LLMUsagePersistenceService.persist()（读取 Scope → 传给 Repository）

0 DB / 0 Network / 0 Real LLM（Fake Repository 记录 kwargs）。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from backend.app.llm.client import LLMUsage
from backend.app.llm.observability import LLMObservation
from backend.app.services.assistant_trace import (
    ASSISTANT_REQUEST_ID_MAX_LENGTH,
    assistant_trace_scope,
    current_assistant_request_id,
)
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
    LLMUsagePersistenceService,
)


class _RecordingRepository:
    """记录 create() kwargs 的 Fake Repository（0 DB）。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._error = error

    def create(self, **kwargs: Any) -> int | None:
        self.calls.append(dict(kwargs))
        if self._error is not None:
            raise self._error
        return len(self.calls)


def _observation(
    *,
    provider_request_id: str | None = "chatcmpl-P1",
    provider: str = "deepseek-test",
    usage: LLMUsage | None = LLMUsage(11, 22, 33),
) -> LLMObservation:
    return LLMObservation(
        provider=provider,
        model="deepseek-chat",
        latency_ms=1.0,
        success=True,
        finish_reason="stop",
        usage=usage,
        request_id=provider_request_id,
    )


# ============================================================
# 1. Contextvar Scope（纯单元）
# ============================================================

class TestAssistantTraceScope:
    def test_unbound_returns_none(self) -> None:
        assert current_assistant_request_id() is None

    def test_bind_and_restore(self) -> None:
        with assistant_trace_scope("A") as bound:
            assert bound == "A"
            assert current_assistant_request_id() == "A"
        assert current_assistant_request_id() is None

    def test_nested_scope_restores_outer(self) -> None:
        with assistant_trace_scope("outer"):
            with assistant_trace_scope("inner"):
                assert current_assistant_request_id() == "inner"
            assert current_assistant_request_id() == "outer"
        assert current_assistant_request_id() is None

    def test_exception_inside_scope_still_restores(self) -> None:
        with pytest.raises(RuntimeError):
            with assistant_trace_scope("A"):
                raise RuntimeError("boom")
        assert current_assistant_request_id() is None

    @pytest.mark.parametrize("bad", ["", 123, None, b"A", "x" * 129])
    def test_invalid_request_id_rejected(self, bad) -> None:
        with pytest.raises(ValueError):
            with assistant_trace_scope(bad):       # type: ignore[arg-type]
                pass
        assert current_assistant_request_id() is None

    def test_max_length_boundary_accepted(self) -> None:
        request_id = "x" * ASSISTANT_REQUEST_ID_MAX_LENGTH
        with assistant_trace_scope(request_id):
            assert current_assistant_request_id() == request_id

    def test_isolated_between_concurrent_tasks(self) -> None:
        """并发任务不串（contextvar 是 per-task 上下文，不是全局变量）。"""
        seen: dict[str, str | None] = {}

        async def _work(name: str) -> None:
            with assistant_trace_scope(name):
                await asyncio.sleep(0)
                seen[name] = current_assistant_request_id()
                await asyncio.sleep(0)
                seen[f"{name}-again"] = current_assistant_request_id()

        async def _main() -> None:
            await asyncio.gather(_work("A"), _work("B"), _work("C"))

        asyncio.run(_main())

        assert seen == {
            "A": "A", "A-again": "A",
            "B": "B", "B-again": "B",
            "C": "C", "C-again": "C",
        }
        assert current_assistant_request_id() is None

    def test_thread_boundary_keeps_scope(self) -> None:
        """asyncio.to_thread（Runtime Bridge）能把 Scope 带进 worker 线程。"""
        seen: list[str | None] = []

        def _worker() -> None:
            seen.append(current_assistant_request_id())

        async def _main() -> None:
            with assistant_trace_scope("A"):
                await asyncio.to_thread(_worker)

        asyncio.run(_main())

        assert seen == ["A"]
        assert threading.current_thread() is not None


# ============================================================
# 2. Persistence Boundary 关联（Fake Repository）
# ============================================================

class TestUsagePersistenceCorrelation:
    def test_bound_scope_is_written_and_provider_id_untouched(self) -> None:
        repository = _RecordingRepository()
        service = LLMUsagePersistenceService(repository=repository)  # type: ignore[arg-type]

        with assistant_trace_scope("assistant-A"):
            service.persist(_observation(provider_request_id="chatcmpl-P1"))

        call = repository.calls[0]
        assert call["assistant_request_id"] == "assistant-A"
        assert call["request_id"] == "chatcmpl-P1"      # Provider ID 不改写
        assert call["assistant_request_id"] != call["request_id"]
        assert set(call) == {
            "request_id", "provider", "model", "prompt_tokens",
            "completion_tokens", "total_tokens", "assistant_request_id",
        }

    def test_unbound_scope_writes_none(self) -> None:
        repository = _RecordingRepository()
        service = LLMUsagePersistenceService(repository=repository)  # type: ignore[arg-type]

        service.persist(_observation())

        assert repository.calls[0]["assistant_request_id"] is None

    def test_same_scope_two_calls_same_id_different_provider_ids(self) -> None:
        repository = _RecordingRepository()
        service = LLMUsagePersistenceService(repository=repository)  # type: ignore[arg-type]

        with assistant_trace_scope("assistant-A"):
            service.persist(_observation(provider_request_id="chatcmpl-P1"))
            service.persist(_observation(provider_request_id="chatcmpl-P2"))

        ids = [call["assistant_request_id"] for call in repository.calls]
        providers = [call["request_id"] for call in repository.calls]
        assert ids == ["assistant-A", "assistant-A"]
        assert providers == ["chatcmpl-P1", "chatcmpl-P2"]
        assert set(ids).isdisjoint(providers)

    def test_different_scopes_do_not_leak(self) -> None:
        repository = _RecordingRepository()
        service = LLMUsagePersistenceService(repository=repository)  # type: ignore[arg-type]

        with assistant_trace_scope("A"):
            service.persist(_observation())
        with assistant_trace_scope("B"):
            service.persist(_observation())

        assert [c["assistant_request_id"] for c in repository.calls] == ["A", "B"]

    def test_usage_none_is_still_not_persisted(self) -> None:
        """usage=None → 不写入（既有语义不变；correlation 不改变该契约）。"""
        repository = _RecordingRepository()
        service = LLMUsagePersistenceService(repository=repository)  # type: ignore[arg-type]

        with assistant_trace_scope("A"):
            assert service.persist(_observation(usage=None)) is None

        assert repository.calls == []

    def test_persistence_failure_isolated_at_sink(self) -> None:
        """Repository 失败 → sink 只 warning；业务结果不受影响。"""
        from backend.app.db.llm_usage_repository import (
            LLMUsageRepositoryError,
        )

        repository = _RecordingRepository(
            error=LLMUsageRepositoryError("db down")
        )
        sink = DatabaseLLMAccountingSink(repository=repository)  # type: ignore[arg-type]

        with assistant_trace_scope("A"):
            sink.record(_observation())            # 不抛
            asyncio.run(sink.arecord(_observation()))   # 异步入口同样不抛

        assert len(repository.calls) == 2          # 两次尝试（无 retry）


__all__ = ["TestAssistantTraceScope", "TestUsagePersistenceCorrelation"]
