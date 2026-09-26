"""LLM Accounting Lifecycle Integration 测试（Phase 3.10.11）。

核心（任务书 §一）：

    LLM request 完成后，在现有 Observation 生命周期中同时产生
    request-level Accounting 信息：

        LLM Request → LLMResponse / str
            → LLMObservation ──→ ObservationSink
                              └→ AccountingSink（派生 LLMUsage）

覆盖（任务书 §二十一 1-15）：

    1.  LLMResponse → Observation → Accounting（full usage identity 链）
    2.  Usage=None（不生成 0 tokens / 不估算 / 不抛异常）
    3.  Partial Usage（原样保留）
    4.  generate() str（无 usage → 无 accounting data）
    5.  Failure（failure observation / accounting None / 异常正常传播）
    6.  AccountingSink failure isolation
    7.  ObservationSink failure isolation
    8.  Observation + Accounting sink independence
    9.  Tool Calling（2 requests → 2 accounting events，不合并）
    10. T2S Retry（request-level，str 契约 usage=None 正常）
    11. Refusal（success=True；usage=None → 0 accounting data）
    12. Concurrent requests（A usage → A accounting，无交叉）
    13. No automatic cost（spy 零调用）
    14. No persistence（结构性：无 DB import / 无 SQL 写入语义）
    15. No aggregation（无 sum/aggregate 语义进入 sink 契约）

附加：AccountingSink 只读取 observation.usage（§二十二）、
默认行为不变（§六）、factory 向后兼容（§二十七/二十八）。

全部使用 Fake / MockTransport / 内存 sink：0 网络 / 0 DB。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
from typing import Any

import httpx
import pytest

from backend.app.llm.accounting import (
    calculate_llm_cost,
    token_accounting_from_usage,
)
from backend.app.config import LLMSettings
from backend.app.llm.client import (
    LLMRequestError,
    LLMResponse,
    LLMUsage,
    NoopAccountingSink,
    NoopObservationSink,
    OpenAICompatibleClient,
    create_llm_client,
)
from backend.app.llm.deepseek_provider import DeepSeekProvider
from backend.app.llm.observability import (
    LLMAccountingSink,
    LLMObservation,
)


# ============================================================
# 测试基础设施（仅测试使用；不持久化、不外发）
# ============================================================

class CollectingObservationSink:
    """内存收集 Observation。"""

    def __init__(self) -> None:
        self.observations: list[LLMObservation] = []

    def record(self, observation: LLMObservation) -> None:
        self.observations.append(observation)


class FailingObservationSink:
    """故意失败的 Observation sink。"""

    def record(self, observation: LLMObservation) -> None:
        raise RuntimeError("observation sink failure")


class CollectingAccountingSink:
    """内存 Accounting sink：record(observation) 内部派生 accounting
    （只读取 observation.usage → token_accounting_from_usage，
    任务书 §二十二）。"""

    def __init__(self) -> None:
        self.observations: list[LLMObservation] = []
        self.accountings: list[Any] = []

    def record(self, observation: LLMObservation) -> None:
        self.observations.append(observation)
        # 只读取 observation.usage（其余字段不触碰）
        self.accountings.append(
            token_accounting_from_usage(observation.usage)
        )


class FailingAccountingSink:
    """故意失败的 Accounting sink。"""

    def record(self, observation: LLMObservation) -> None:
        raise RuntimeError("accounting sink failure")


class ScriptedHandler:
    """按脚本回放响应；记录请求供计数。"""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        body = (
            self._responses.pop(0)
            if len(self._responses) > 1
            else self._responses[0]
        )
        return httpx.Response(200, json=body)

    @property
    def request_count(self) -> int:
        return len(self.requests)


def _obs_client(
    handler: Any,
    observation_sink: Any = None,
    accounting_sink: Any = None,
) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="configured-model",
        provider="deepseek-test",
        transport=httpx.MockTransport(handler),
        observation_sink=observation_sink,
        accounting_sink=accounting_sink,
    )


def _provider(
    handler: Any,
    observation_sink: Any = None,
    accounting_sink: Any = None,
) -> DeepSeekProvider:
    return DeepSeekProvider(
        _obs_client(handler, observation_sink, accounting_sink)
    )


_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_inventory",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def _content_body(
    content: str | None,
    *,
    request_id: str = "chatcmpl-lc-1",
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


def _tool_call_body(*, request_id: str, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    body = _content_body(
        None, request_id=request_id, finish_reason="tool_calls", usage=usage,
    )
    body["choices"][0]["message"]["tool_calls"] = [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "get_inventory",
                "arguments": '{"material_code": "MAT001"}',
            },
        }
    ]
    return body


# ============================================================
# 1. LLMResponse → Observation → Accounting（full usage）
# ============================================================

async def test_response_to_observation_to_accounting() -> None:
    """full usage：1 request → 1 observation → 1 accounting event；
    identity 链：observation.usage is response.usage、
    accounting（派生）is response.usage。"""
    usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    handler = ScriptedHandler([
        _content_body("answer", request_id="chatcmpl-lc-full", usage=usage),
    ])
    obs_sink = CollectingObservationSink()
    acc_sink = CollectingAccountingSink()
    provider = _provider(handler, obs_sink, acc_sink)

    response = await provider.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    assert isinstance(response, LLMResponse)

    assert handler.request_count == 1
    assert len(obs_sink.observations) == 1
    assert len(acc_sink.accountings) == 1            # 1 request = 1 accounting event

    observation = obs_sink.observations[0]
    assert observation.usage is response.usage       # Observation 侧 identity
    accounting = acc_sink.accountings[0]
    assert accounting is response.usage              # Accounting 侧 identity
    assert accounting.prompt_tokens == 100
    assert accounting.completion_tokens == 20
    assert accounting.total_tokens == 120


# ============================================================
# 2 / 3. Usage=None / Partial Usage
# ============================================================

async def test_usage_none_yields_no_accounting_data() -> None:
    """usage=None：accounting sink 被调用（收到 observation）但派生
    None——不生成 0 tokens、不估算、不抛异常。"""
    handler = ScriptedHandler([_content_body("answer", request_id="chatcmpl-nu")])
    acc_sink = CollectingAccountingSink()
    provider = _provider(handler, None, acc_sink)

    response = await provider.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    assert isinstance(response, LLMResponse) and response.usage is None

    assert len(acc_sink.accountings) == 1            # event 发生
    assert acc_sink.accountings[0] is None           # 无 accounting data
    assert acc_sink.accountings[0] != 0              # 不是 0 tokens


@pytest.mark.parametrize(
    "usage_dict,expected",
    [
        ({"prompt_tokens": 100}, (100, None, None)),
        ({"completion_tokens": 200}, (None, 200, None)),
        ({"prompt_tokens": 100, "completion_tokens": 50}, (100, 50, None)),
    ],
)
async def test_partial_usage_preserved_in_lifecycle(
    usage_dict: dict[str, Any], expected: tuple[int | None, int | None, int | None]
) -> None:
    """partial usage：lifecycle 派生原样保留（不补全 / 不推断）。"""
    handler = ScriptedHandler([
        _content_body("answer", request_id="chatcmpl-partial", usage=usage_dict),
    ])
    acc_sink = CollectingAccountingSink()
    provider = _provider(handler, None, acc_sink)

    await provider.chat([{"role": "user", "content": "hi"}], tools=_TOOLS)

    accounting = acc_sink.accountings[0]
    assert accounting is not None
    assert (
        accounting.prompt_tokens,
        accounting.completion_tokens,
        accounting.total_tokens,
    ) == expected


# ============================================================
# 4. generate() str：无 usage → 无 accounting data
# ============================================================

async def test_generate_str_no_accounting_data() -> None:
    """generate() → str 契约不变；str 路径无 usage → accounting=None
    （不从字符串估算 token）。"""
    handler = ScriptedHandler([
        _content_body(
            "hello",
            request_id="chatcmpl-gen",
            usage={"prompt_tokens": 999, "completion_tokens": 1,
                   "total_tokens": 1000},  # 真实响应带 usage 也不暴露
        ),
    ])
    obs_sink = CollectingObservationSink()
    acc_sink = CollectingAccountingSink()
    provider = _provider(handler, obs_sink, acc_sink)

    answer = await provider.generate("ping")

    assert answer == "hello"                          # 返回契约不变
    assert len(acc_sink.accountings) == 1             # event 发生
    assert acc_sink.accountings[0] is None            # 无 usage → 无 accounting
    assert obs_sink.observations[0].usage is None


# ============================================================
# 5. Failure：failure observation / accounting None / 异常传播
# ============================================================

async def test_failure_request_accounting_none() -> None:
    """LLM request 失败：failure observation（success=False / usage=None）
    → accounting None；不估算 usage、不伪造 accounting；
    LLMRequestError 原样传播。"""

    def handler_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "x"})

    obs_sink = CollectingObservationSink()
    acc_sink = CollectingAccountingSink()
    provider = _provider(handler_503, obs_sink, acc_sink)

    with pytest.raises(LLMRequestError):
        await provider.chat([{"role": "user", "content": "hi"}], tools=_TOOLS)

    assert len(obs_sink.observations) == 1
    assert len(acc_sink.accountings) == 1             # event 发生（1 request）
    assert obs_sink.observations[0].success is False
    assert obs_sink.observations[0].usage is None
    assert acc_sink.accountings[0] is None            # 无 usage → 无 accounting data


# ============================================================
# 6 / 7 / 8. Sink failure isolation + independence
# ============================================================

async def test_accounting_sink_failure_isolation() -> None:
    """AccountingSink 故障：不得影响 LLM response / business result
    （成功调用仍返回原 result）。"""
    handler = ScriptedHandler([_content_body("ok-answer", request_id="chatcmpl-iso-a")])
    provider = _provider(handler, None, FailingAccountingSink())

    result = await provider.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    assert isinstance(result, LLMResponse)
    assert result.content == "ok-answer"              # 业务结果原样


async def test_observation_sink_failure_isolation_with_accounting() -> None:
    """ObservationSink 故障 + AccountingSink 正常：业务不受影响；
    accounting event 照常发生（两个 sink 相互独立）。"""
    handler = ScriptedHandler([
        _content_body(
            "ok", request_id="chatcmpl-iso-b",
            usage={"prompt_tokens": 10, "completion_tokens": 2,
                   "total_tokens": 12},
        ),
    ])
    acc_sink = CollectingAccountingSink()
    provider = _provider(handler, FailingObservationSink(), acc_sink)

    result = await provider.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    assert isinstance(result, LLMResponse) and result.content == "ok"
    assert len(acc_sink.accountings) == 1
    assert acc_sink.accountings[0] is not None
    assert acc_sink.accountings[0].total_tokens == 12


async def test_both_sinks_failing_business_unaffected() -> None:
    """两个 sink 同时故障：成功调用仍成功；失败调用仍抛原始异常。"""
    handler_ok = ScriptedHandler([_content_body("still-ok", request_id="chatcmpl-iso-c")])
    provider_ok = _provider(handler_ok, FailingObservationSink(), FailingAccountingSink())
    result = await provider_ok.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    assert isinstance(result, LLMResponse) and result.content == "still-ok"

    def handler_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "e"})

    provider_fail = _provider(handler_503, FailingObservationSink(), FailingAccountingSink())
    with pytest.raises(LLMRequestError):
        await provider_fail.chat([{"role": "user", "content": "hi"}], tools=_TOOLS)


# ============================================================
# 9. Tool Calling：2 requests → 2 accounting events（不合并）
# ============================================================

async def test_tool_calling_two_accounting_events() -> None:
    """Tool Calling 两轮：2 observations → 2 accounting events；
    usage 各自独立，禁止合并 total_tokens（无 aggregation）。"""
    handler = ScriptedHandler([
        _tool_call_body(
            request_id="chatcmpl-tc-1",
            usage={"prompt_tokens": 100, "completion_tokens": 10,
                   "total_tokens": 110},
        ),
        _content_body(
            "final", request_id="chatcmpl-tc-2",
            usage={"prompt_tokens": 200, "completion_tokens": 30,
                   "total_tokens": 230},
        ),
    ])
    obs_sink = CollectingObservationSink()
    acc_sink = CollectingAccountingSink()
    provider = _provider(handler, obs_sink, acc_sink)
    from backend.app.tools.mock_tools import register_mock_tools
    from backend.app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_mock_tools(registry)
    from backend.app.services.tool_chat_service import ToolChatService

    await ToolChatService(provider).chat(
        "帮我查询 MAT001 的库存", registry=registry,
    )

    assert handler.request_count == 2
    assert len(acc_sink.accountings) == 2             # 2 events（非合并 1 个）
    acc1, acc2 = acc_sink.accountings
    assert acc1 is not None and acc2 is not None
    assert acc1 is not acc2
    assert (acc1.prompt_tokens, acc1.total_tokens) == (100, 110)
    assert (acc2.prompt_tokens, acc2.total_tokens) == (200, 230)
    # 无聚合语义：sink 契约无 sum / aggregate
    assert not hasattr(acc_sink, "aggregate")
    assert not hasattr(acc_sink, "sum")


# ============================================================
# 10. T2S Retry：request-level accounting events
# ============================================================

async def test_t2s_retry_accounting_events() -> None:
    """T2S semantic retry：2 requests → 2 observations → 2 accounting
    events（str 契约 usage=None → 派生 None ×2；不虚构 / 不新增字段）。"""
    handler = ScriptedHandler([
        {"id": "r1", "model": "deepseek-chat",
         "choices": [{"index": 0, "finish_reason": "stop",
                      "message": {"role": "assistant", "content": "SELECT 1"}}]},
        {"id": "r2", "model": "deepseek-chat",
         "choices": [{"index": 0, "finish_reason": "stop",
                      "message": {"role": "assistant",
                                  "content": "```sql\nSELECT 1 LIMIT 1\n```"}}]},
    ])
    obs_sink = CollectingObservationSink()
    acc_sink = CollectingAccountingSink()
    provider = _provider(handler, obs_sink, acc_sink)
    from backend.app.services.text_to_sql_service import TextToSQLService

    result = await TextToSQLService(llm_client=provider, max_attempts=3).generate(
        "知识文档有哪些？", database_context="ctx",
    )

    assert result.validated is True
    assert result.attempts == 2
    assert handler.request_count == 2
    assert len(obs_sink.observations) == 2
    assert len(acc_sink.accountings) == 2             # 1 request = 1 accounting event
    assert acc_sink.accountings == [None, None]       # str 契约：无 usage
    for observation in obs_sink.observations:
        assert not hasattr(observation, "retry_count")
        assert not hasattr(observation, "retry_tokens")
        assert not hasattr(observation, "total_retry_cost")


# ============================================================
# 11. Refusal：success=True；str 契约 usage=None → 0 accounting data
# ============================================================

async def test_refusal_accounting_lifecycle() -> None:
    """Refusal：1 request → 1 observation（success=True）→ 1 accounting
    event（派生 None——str 契约无 usage；不把 refusal 当成 LLM failure）。"""
    from backend.app.services.text_to_sql_service import REFUSAL_MARKER

    handler = ScriptedHandler([
        _content_body(REFUSAL_MARKER, request_id="chatcmpl-refusal"),
    ])
    obs_sink = CollectingObservationSink()
    acc_sink = CollectingAccountingSink()
    provider = _provider(handler, obs_sink, acc_sink)
    from backend.app.services.text_to_sql_service import TextToSQLService

    result = await TextToSQLService(llm_client=provider, max_attempts=3).generate(
        "删除所有库存", database_context="ctx",
    )

    assert result.status == "refusal"
    assert handler.request_count == 1
    assert len(obs_sink.observations) == 1
    assert obs_sink.observations[0].success is True   # refusal 是业务结果
    assert len(acc_sink.accountings) == 1
    assert acc_sink.accountings[0] is None            # str 契约：无 usage


# ============================================================
# 12. Concurrency：A usage → A accounting，无交叉
# ============================================================

async def test_concurrent_accounting_no_cross_talk() -> None:
    """5 并发请求（usage 各异）→ 5 observations + 5 accounting events，
    一一对应（A usage → A accounting），无共享状态交叉。"""
    obs_sink = CollectingObservationSink()
    acc_sink = CollectingAccountingSink()
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        n = counter["n"]
        return httpx.Response(
            200,
            json=_content_body(
                f"answer-{n}",
                request_id=f"chatcmpl-conc-{n}",
                usage={
                    "prompt_tokens": 100 * n,
                    "completion_tokens": 10 * n,
                    "total_tokens": 110 * n,
                },
            ),
        )

    provider = _provider(handler, obs_sink, acc_sink)
    responses = await asyncio.gather(
        *[
            provider.chat([{"role": "user", "content": f"q-{i}"}], tools=_TOOLS)
            for i in range(5)
        ]
    )

    assert len(obs_sink.observations) == 5
    assert len(acc_sink.accountings) == 5
    # Observation 与 Accounting 一一对应（顺序一致、usage 一致）
    for observation, accounting in zip(obs_sink.observations, acc_sink.accountings):
        assert accounting is observation.usage
    totals = {acc.total_tokens for acc in acc_sink.accountings}
    assert totals == {110 * n for n in range(1, 6)}   # 无交叉
    assert all(acc is not None and acc.prompt_tokens >= 100
               for acc in acc_sink.accountings)
    assert all(r.content == f"answer-{i + 1}" for i, r in enumerate(responses))


# ============================================================
# 13 / 14 / 15. No automatic cost / No persistence / No aggregation
# ============================================================

async def test_no_automatic_cost_calculation_in_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accounting lifecycle 全程不自动调用 calculate_llm_cost
    （spy 零调用；Cost = explicit calculation）。"""
    import backend.app.llm.accounting as accounting_mod

    cost_calls: list[Any] = []

    def spy(usage: Any, pricing: Any) -> None:
        cost_calls.append((usage, pricing))
        raise AssertionError("lifecycle 不得自动计算 Cost")

    monkeypatch.setattr(accounting_mod, "calculate_llm_cost", spy)

    handler = ScriptedHandler([
        _content_body(
            "answer", request_id="chatcmpl-nocost",
            usage={"prompt_tokens": 10, "completion_tokens": 2,
                   "total_tokens": 12},
        ),
    ])
    provider = _provider(handler, None, CollectingAccountingSink())
    await provider.chat([{"role": "user", "content": "hi"}], tools=_TOOLS)

    assert cost_calls == []                           # spy 零调用


def test_no_persistence_and_no_aggregation_structurally() -> None:
    """结构性验证：observability / accounting 模块无 DB / 持久化 /
    aggregation import；Accounting sink 契约不含 sum / aggregate。"""
    import backend.app.llm.accounting as accounting_mod
    import backend.app.llm.observability as observability_mod

    for module in (observability_mod, accounting_mod):
        tree = ast.parse(inspect.getsource(module))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {
            "sqlalchemy", "psycopg", "sqlite3", "redis", "kafka",
            "httpx", "requests", "aiohttp", "urllib",
        }
        assert not any(name.split(".")[0] in forbidden for name in imported)

    # Accounting sink 协议只有 record；无 aggregate / flush / persist
    sink_methods = [
        name for name in dir(CollectingAccountingSink)
        if not name.startswith("_")
    ]
    assert "record" in sink_methods
    for forbidden in ("aggregate", "sum", "flush", "persist", "save"):
        assert forbidden not in sink_methods


# ============================================================
# §二十二 — AccountingSink 只读取 observation.usage
# ============================================================

def test_accounting_sink_only_reads_usage_field() -> None:
    """毒化 Observation：除 usage 外全部属性抛异常 →
    CollectingAccountingSink.record 正常工作（只读取 usage）。"""

    def _boom(_: Any) -> Any:
        raise AssertionError("Accounting sink 不得读取该字段")

    sentinel_usage = LLMUsage(prompt_tokens=7, completion_tokens=3,
                              total_tokens=10)

    class PoisonObservation:
        """除 usage 外全部毒化（读取即失败）。"""

        usage = sentinel_usage

    poison = PoisonObservation()
    poison.model = property(_boom)  # type: ignore[assignment]
    poison.success = property(_boom)  # type: ignore[assignment]
    poison.metadata = property(_boom)  # type: ignore[assignment]
    poison.finish_reason = property(_boom)  # type: ignore[assignment]
    poison.request_id = property(_boom)  # type: ignore[assignment]
    poison.provider = property(_boom)  # type: ignore[assignment]
    poison.error_type = property(_boom)  # type: ignore[assignment]
    poison.latency_ms = property(_boom)  # type: ignore[assignment]

    sink = CollectingAccountingSink()
    sink.record(poison)  # type: ignore[arg-type]

    assert len(sink.accountings) == 1
    assert sink.accountings[0] is sentinel_usage


# ============================================================
# §六 / §二十七 / §二十八 — 默认行为不变 + 向后兼容
# ============================================================

def test_default_accounting_sink_is_noop() -> None:
    """未配置 accounting_sink → NoopAccountingSink（默认行为与
    3.10.10 完全一致）；observation_sink 默认同样 No-op。"""
    handler = ScriptedHandler([_content_body("x", request_id="chatcmpl-def")])
    client = _obs_client(handler)
    assert isinstance(client._observation_sink, NoopObservationSink)
    assert isinstance(client._accounting_sink, NoopAccountingSink)


def test_factory_accepts_accounting_sink_and_backward_compatible() -> None:
    """工厂：accounting_sink optional 透传；旧调用（不传/仅传部分参数）
    继续工作（§二十七 / §二十八）。"""
    llm_settings = LLMSettings(
        api_key="k", base_url="https://api.example.com/v1", model="m",
    )
    acc_sink = CollectingAccountingSink()

    # 旧形式：无 sink 参数
    provider_old = create_llm_client(llm_settings)
    assert isinstance(provider_old, DeepSeekProvider)

    # 新形式：两个 sink 显式注入
    provider_new = create_llm_client(
        llm_settings,
        observation_sink=CollectingObservationSink(),
        accounting_sink=acc_sink,
    )
    inner = provider_new._client
    assert inner._accounting_sink is acc_sink          # type: ignore[attr-defined]
    # 协议满足（structural）：record 可调用（LLMAccountingSink 非
    # runtime_checkable，不做 isinstance）
    assert callable(inner._accounting_sink.record)     # type: ignore[attr-defined]


async def test_default_path_identical_to_phase_3_10_10() -> None:
    """无 Accounting 配置：同一请求在（无 sink）与（collecting sinks）
    下业务结果一致（§六 默认行为不变）。"""
    def make_handler(content: str) -> ScriptedHandler:
        return ScriptedHandler([_content_body(content, request_id="chatcmpl-eq")])

    provider_noop = _provider(make_handler("same-answer"))
    provider_sinks = _provider(
        make_handler("same-answer"),
        CollectingObservationSink(),
        CollectingAccountingSink(),
    )

    r1 = await provider_noop.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    r2 = await provider_sinks.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    assert isinstance(r1, LLMResponse) and isinstance(r2, LLMResponse)
    assert r1.content == r2.content == "same-answer"
    assert r1.usage is None and r2.usage is None      # 同为 str 契约结果
