"""LLM Usage Production Wiring（Phase 3.12 Step 56）——**离线**契约测试。

覆盖（0 DB / 0 网络 / 0 真实 LLM）：
    1. 生产默认 Client 使用 ``DatabaseLLMAccountingSink``（DB 已配置时）
    2. ``create_llm_client()`` 默认语义**不变**（None → NoopAccountingSink）
    3. DB 未配置（``get_engine() is None``）→ 默认 sink 回落 Noop（旧行为）
    4. 默认 Client / sink **进程级单例**（reset 后重建，不按 request / call）
    5. 一次 LLM call → **恰好一次** accounting（Provider 不重复记账）
    6. ``assistant_request_id`` 关联（来自 assistant_trace_scope）·
       Provider ``request_id`` 独立保存（两者不同、不覆盖）
    7. 安全：写入字段严格白名单（无 prompt / messages / raw response / 凭据）
    8. 失败隔离：Accounting DB 失败 → LLM 业务结果不变 + **不重试**（handler 调用 1 次）

本文件**不连接数据库**：Repository 一律用 Fake（记录调用参数）。
"""
from __future__ import annotations

import ast
import dataclasses
import asyncio
import inspect
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend.app.config import settings
from backend.app.db import session as db_session
from backend.app.llm.client import (
    OpenAICompatibleClient,
    create_llm_client,
    get_default_accounting_sink,
    get_default_llm_client,
    reset_default_llm_client,
)
from backend.app.llm.deepseek_provider import DeepSeekProvider
from backend.app.llm.observability import NoopAccountingSink
from backend.app.services.assistant_trace import assistant_trace_scope
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
)
from backend.app.services.llm_usage_persistence_service import (
    LLMUsagePersistenceService,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PROVIDER_ID = "chatcmpl-step56-offline"
_SCOPE_ID = "step56-assistant-offline"

#: Repository 允许写入的字段（与 Step 55 审计白名单一致）
_APPROVED_WRITE_KEYS = {
    "request_id",
    "provider",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "assistant_request_id",
}


def _content_body(provider_request_id: str = _PROVIDER_ID) -> dict[str, Any]:
    return {
        "id": provider_request_id,
        "model": "step56-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "答案"},
            }
        ],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 22,
            "total_tokens": 33,
        },
    }


def _handler(requests: list[httpx.Request]) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_content_body())

    return handle


class FakeRepository:
    """最小仓储替身：记录调用参数（不触达 DB）。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    def create(self, **kwargs: Any) -> int | None:
        self.calls.append(dict(kwargs))
        if self.fail:
            from backend.app.db.llm_usage_repository import (
                LLMUsageRepositoryError,
            )

            raise LLMUsageRepositoryError("step56-fake-db-failure")
        return len(self.calls)


def _client_with_sink(
    sink: Any,
    requests: list[httpx.Request],
) -> DeepSeekProvider:
    """真实 Client + MockTransport（0 网络）+ 指定 sink。"""
    return DeepSeekProvider(
        OpenAICompatibleClient(
            api_key="step56-key",
            base_url="https://step56.fake/v1",
            model="step56-model",
            provider="step56-provider",
            transport=httpx.MockTransport(_handler(requests)),
            accounting_sink=sink,
        )
    )


def _run(coro_factory: Any) -> Any:
    return asyncio.run(coro_factory())


def _inner(client: Any) -> Any:
    """取 Provider 内层真实 Client（sink 挂在它上面）。"""
    return client._client if isinstance(client, DeepSeekProvider) else client


# ============================================================
# 1 / 2 / 3. 接线 + 默认语义不变 + 无 DB 回落
# ============================================================

class TestWiring:
    def test_default_accounting_sink_follows_db_availability(self) -> None:
        """生产默认 sink：DB 已配置 → Database sink；未配置 → Noop（无新配置项）。"""
        db_configured = db_session.get_engine() is not None

        reset_default_llm_client()
        sink = get_default_accounting_sink()

        if db_configured:
            assert isinstance(sink, DatabaseLLMAccountingSink)
            assert hasattr(sink, "arecord")          # 异步入口（线程边界）
        else:
            assert isinstance(sink, NoopAccountingSink)
        reset_default_llm_client()

    def test_default_client_uses_default_accounting_sink(self) -> None:
        reset_default_llm_client()
        client = get_default_llm_client()
        sink = get_default_accounting_sink()

        assert _inner(client)._accounting_sink is sink
        if isinstance(sink, DatabaseLLMAccountingSink):
            # 生产接线：默认 Client 的 sink **不是** Noop
            assert not isinstance(_inner(client)._accounting_sink, NoopAccountingSink)
        reset_default_llm_client()

    def test_no_db_environment_keeps_noop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DB 未配置 → 保持 Noop（与 Step 56 之前完全一致，无告警噪声）。"""
        import backend.app.db.session as db_session

        monkeypatch.setattr(db_session, "get_engine", lambda: None)
        reset_default_llm_client()

        assert isinstance(get_default_accounting_sink(), NoopAccountingSink)
        assert isinstance(
            _inner(get_default_llm_client())._accounting_sink,
            NoopAccountingSink,
        )
        reset_default_llm_client()

    def test_explicit_factory_default_is_unchanged(self) -> None:
        """``create_llm_client()``（显式构造）默认仍为 Noop（契约不变）。"""
        assert isinstance(
            _inner(create_llm_client(settings.llm))._accounting_sink,
            NoopAccountingSink,
        )
        assert "DatabaseLLMAccountingSink" not in inspect.getsource(
            create_llm_client
        )

    def test_source_wiring_is_only_in_default_client(self) -> None:
        """静态：接线只发生在默认 Client 工厂（模块级无 DB 资源构造）。"""
        source = (_REPO_ROOT / "backend/app/llm/client.py").read_text(
            encoding="utf-8"
        )

        assert "def get_default_accounting_sink(" in source
        assert "accounting_sink=get_default_accounting_sink()" in source
        tree = ast.parse(source)
        module_level_calls = {
            getattr(node.value.func, "id", None)
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and isinstance(node.value, ast.Call)
        }
        for forbidden in ("Session", "sessionmaker", "create_engine"):
            assert forbidden not in module_level_calls


# ============================================================
# 4. 单例语义
# ============================================================

class TestSingletons:
    def test_default_client_and_sink_are_process_singletons(self) -> None:
        reset_default_llm_client()
        first_client = get_default_llm_client()
        first_sink = get_default_accounting_sink()

        assert get_default_llm_client() is first_client
        assert get_default_accounting_sink() is first_sink
        assert _inner(first_client)._accounting_sink is first_sink
        reset_default_llm_client()

    def test_reset_rebuilds_both_singletons(self) -> None:
        reset_default_llm_client()
        client_before = get_default_llm_client()
        sink_before = get_default_accounting_sink()

        reset_default_llm_client()
        client_after = get_default_llm_client()
        sink_after = get_default_accounting_sink()

        assert client_after is not client_before
        assert sink_after is not sink_before
        assert _inner(client_after)._accounting_sink is sink_after
        reset_default_llm_client()


# ============================================================
# 5 / 6 / 7. 一次 call 一条记录 + 关联 + 安全
# ============================================================

class TestOneCallOneRecordAndCorrelation:
    def test_one_call_one_record_and_provider_id_preserved(self) -> None:
        repository = FakeRepository()
        sink = DatabaseLLMAccountingSink(repository=repository)
        requests: list[httpx.Request] = []
        client = _client_with_sink(sink, requests)

        with assistant_trace_scope(_SCOPE_ID):
            _run(lambda: client.chat([{"role": "user", "content": "hi"}]))

        assert len(requests) == 1                 # 1 次真实 provider 请求
        assert len(repository.calls) == 1         # 1 条 usage
        written = repository.calls[0]
        assert written["request_id"] == _PROVIDER_ID          # Provider 请求 ID
        assert written["assistant_request_id"] == _SCOPE_ID   # Assistant Trace ID
        assert written["request_id"] != written["assistant_request_id"]

    def test_two_calls_two_records_no_duplicate_emission(self) -> None:
        repository = FakeRepository()
        sink = DatabaseLLMAccountingSink(repository=repository)
        requests: list[httpx.Request] = []
        client = _client_with_sink(sink, requests)

        async def _two() -> None:
            await client.chat([{"role": "user", "content": "one"}])
            await client.chat([{"role": "user", "content": "two"}])

        _run(_two)

        assert len(requests) == 2
        assert len(repository.calls) == 2         # 恰好 one → one（无 Provider 二次记账）

    def test_outside_scope_assistant_request_id_is_none(self) -> None:
        repository = FakeRepository()
        sink = DatabaseLLMAccountingSink(repository=repository)
        requests: list[httpx.Request] = []
        client = _client_with_sink(sink, requests)

        _run(lambda: client.chat([{"role": "user", "content": "hi"}]))

        assert repository.calls[0]["assistant_request_id"] is None

    def test_written_fields_are_whitelisted_only(self) -> None:
        repository = FakeRepository()
        sink = DatabaseLLMAccountingSink(repository=repository)
        requests: list[httpx.Request] = []
        client = _client_with_sink(sink, requests)
        secret_prompt = "STEP56-SECRET-PROMPT"

        with assistant_trace_scope(_SCOPE_ID):
            _run(
                lambda: client.chat(
                    [{"role": "user", "content": secret_prompt}]
                )
            )

        written = repository.calls[0]
        assert set(written) == _APPROVED_WRITE_KEYS
        # 值层面：不得出现 prompt / messages / raw response / 凭据 等原文
        blob = " ".join(str(value) for value in written.values())
        for forbidden in (
            "SECRET",
            "hi",            # 用户消息原文
            "答案",           # LLM 响应原文
            "api_key",
            "authorization",
            "database_url",
            "password",
        ):
            assert forbidden not in blob, forbidden
        assert written["provider"] == "step56-provider"
        assert written["model"] == "step56-model"
        assert written["total_tokens"] == 33


# ============================================================
# 8. 失败隔离 + 不重试
# ============================================================

class TestFailureIsolation:
    def test_db_failure_keeps_llm_result_and_does_not_retry(self) -> None:
        repository = FakeRepository(fail=True)
        sink = DatabaseLLMAccountingSink(repository=repository)
        requests: list[httpx.Request] = []
        client = _client_with_sink(sink, requests)

        with assistant_trace_scope(_SCOPE_ID):
            answer = _run(
                lambda: client.chat([{"role": "user", "content": "hi"}])
            )

        assert answer == "答案"                 # LLM 业务结果不变
        assert len(requests) == 1               # **不重试**（provider 只被调用 1 次）
        assert len(repository.calls) == 1       # 尝试过 1 次写入（失败被收敛）

    def test_direct_sink_record_failure_is_swallowed(self) -> None:
        sink = DatabaseLLMAccountingSink(repository=FakeRepository(fail=True))

        from backend.app.llm.observability import LLMObservation
        from backend.app.llm.client import LLMUsage

        observation = LLMObservation(
            request_id=_PROVIDER_ID,
            provider="step56-provider",
            model="step56-model",
            usage=LLMUsage(1, 2, 3),
            success=True,
        )

        sink.record(observation)                        # 不抛出（收敛为 warning）
        _run(lambda: sink.arecord(observation))         # 异步入口同样不抛出


# ============================================================
# 附加：persist 语义（usage=None 不写入 —— 失败路径不产生 usage 行）
# ============================================================

class TestUsageNoneWritesNothing:
    def test_persistence_service_skips_none_usage(self) -> None:
        repository = FakeRepository()
        service = LLMUsagePersistenceService(repository=repository)

        assert service.persist(None) is None
        assert repository.calls == []


__all__ = [
    "TestWiring",
    "TestSingletons",
    "TestOneCallOneRecordAndCorrelation",
    "TestFailureIsolation",
    "TestUsageNoneWritesNothing",
]
