"""LLM Accounting Integration — request-level 关联验证（Phase 3.10.10）。

核心目标（任务书 §一）：

    一次 LLM request 产生的 Observation，可以可靠地关联到
    对应的 Token Accounting。

关联语义（§八）：

    response.usage ──┬──→ observation.usage        （is 同一对象）
                     └──→ token_accounting_from_usage()
                                                  （is 同一对象）

覆盖（任务书 §二十一）：

    1.  Success + Full Usage      1 request / 1 observation / 1 accounting
    2.  Success + Partial Usage   不补全（A / B / C 组合）
    3.  Success + Usage None      accounting=None（不估算 / 不补 0 / 不伪造）
    4.  Tool Calling              2 requests → 2 observations → 2 accountings
    5.  T2S Semantic Retry        request-level（str 契约 → usage=None 正常）
    6.  Refusal                   1 request / 1 observation / 1 accounting
    7.  Failure                   1 failure observation / 0 accounting data
    8.  Generate                  str 契约不虚构 usage / accounting
    9.  Isolation                 accounting 故障不影响业务 / observation
    10. No Cost Auto Calculation  生产 Client 不自动调用 calculate_llm_cost

附加：synthetic pricing 链（§十九，纯函数测试）、Security（§二十二）、
Concurrency（§二十三）。

全部使用 Fake / MockTransport / 内存对象：0 网络 / 0 DB。
生产代码零修改。
"""
from __future__ import annotations

import asyncio
import dataclasses
from decimal import Decimal
from typing import Any

import httpx
import pytest

from backend.app.llm.accounting import (
    LLMPricing,
    calculate_llm_cost,
    token_accounting_from_usage,
)
from backend.app.llm.client import LLMRequestError, LLMResponse, OpenAICompatibleClient
from backend.app.llm.deepseek_provider import DeepSeekProvider
from backend.app.llm.observability import LLMObservation
from backend.app.services.text_to_sql_service import TextToSQLService


# ============================================================
# 测试基础设施（仅测试使用）
# ============================================================

class CollectingObservationSink:
    """内存收集 sink（仅测试断言用）。"""

    def __init__(self) -> None:
        self.observations: list[LLMObservation] = []

    def record(self, observation: LLMObservation) -> None:
        self.observations.append(observation)


class ScriptedHandler:
    """按脚本回放 OpenAI 兼容响应；记录请求供计数。"""

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


import json  # noqa: E402

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_inventory",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def _obs_provider(handler: Any, sink: Any = None) -> DeepSeekProvider:
    """真实 Client + MockTransport + sink → DeepSeekProvider（生产同构）。"""
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="configured-model",
        provider="deepseek-test",
        transport=httpx.MockTransport(handler),
        observation_sink=sink,
    )
    return DeepSeekProvider(client)


def _content_body(
    content: str,
    *,
    request_id: str = "chatcmpl-acc-1",
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


def _assert_accounting_matches(
    accounting: Any, usage: Any
) -> None:
    """Accounting 与 usage 的 request-level 对齐断言（§八 / §九）。"""
    assert accounting is usage  # 同一对象（token_accounting_from_usage 透传）
    assert accounting is not None
    assert accounting.prompt_tokens == usage.prompt_tokens
    assert accounting.completion_tokens == usage.completion_tokens
    assert accounting.total_tokens == usage.total_tokens


# ============================================================
# Test 1 — Success + Full Usage
# ============================================================

async def test_success_full_usage_alignment() -> None:
    """1 request → 1 observation → 1 accounting；
    usage identity 链完整（observation.usage is response.usage，
    accounting is response.usage），三字段数值一致。"""
    handler = ScriptedHandler([
        _content_body(
            "answer",
            request_id="chatcmpl-full-1",
            usage={"prompt_tokens": 100, "completion_tokens": 20,
                   "total_tokens": 120},
        ),
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)

    response = await provider.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    assert isinstance(response, LLMResponse)
    assert handler.request_count == 1
    assert len(sink.observations) == 1

    observation = sink.observations[0]
    assert observation.success is True
    assert observation.usage is response.usage          # Observation 侧 identity

    accounting = token_accounting_from_usage(observation.usage)
    _assert_accounting_matches(accounting, response.usage)  # Accounting 侧 identity
    assert accounting.prompt_tokens == 100
    assert accounting.completion_tokens == 20
    assert accounting.total_tokens == 120


# ============================================================
# Test 2 — Success + Partial Usage（不补全）
# ============================================================

@pytest.mark.parametrize(
    "usage_dict,expected",
    [
        # A：仅 prompt
        ({"prompt_tokens": 100}, (100, None, None)),
        # B：仅 completion
        ({"completion_tokens": 200}, (None, 200, None)),
        # C：prompt + completion 无 total（LLMUsage Contract 允许）
        ({"prompt_tokens": 100, "completion_tokens": 50}, (100, 50, None)),
    ],
)
async def test_success_partial_usage_preserved(
    usage_dict: dict[str, Any], expected: tuple[int | None, int | None, int | None]
) -> None:
    """partial usage 原样保留：Observation 与 Accounting 均不补全
    （缺失字段保持 None，不当 0、不推断 total）。"""
    handler = ScriptedHandler([
        _content_body("answer", request_id="chatcmpl-partial", usage=usage_dict),
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)

    response = await provider.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    observation = sink.observations[0]
    accounting = token_accounting_from_usage(observation.usage)

    assert accounting is not None
    assert (
        accounting.prompt_tokens,
        accounting.completion_tokens,
        accounting.total_tokens,
    ) == expected
    assert observation.usage is response.usage
    assert accounting is response.usage


# ============================================================
# Test 3 — Usage=None
# ============================================================

async def test_success_usage_none_means_no_accounting() -> None:
    """响应无 usage → observation.usage is None → accounting=None；
    不估算 token、不补 0、不伪造 usage。"""
    handler = ScriptedHandler([
        _content_body("answer", request_id="chatcmpl-nousage"),  # 无 usage
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)

    response = await provider.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    assert isinstance(response, LLMResponse)
    assert response.usage is None

    assert len(sink.observations) == 1
    observation = sink.observations[0]
    assert observation.success is True           # 调用本身成功
    assert observation.usage is None             # 无 usage 数据

    accounting = token_accounting_from_usage(observation.usage)
    assert accounting is None                    # 无 accounting data
    assert token_accounting_from_usage(None) is None


# ============================================================
# Test 4 — Tool Calling：2 requests → 2 独立 accounting
# ============================================================

async def test_tool_calling_two_independent_accountings() -> None:
    """LLM → Tool → LLM（2 requests）→ 2 observations → 2 个独立
    accounting record；两次 usage 不被合并（无 aggregation）。"""
    handler = ScriptedHandler([
        _tool_call_body(
            request_id="chatcmpl-tool-1",
            usage={"prompt_tokens": 100, "completion_tokens": 10,
                   "total_tokens": 110},
        ),
        _content_body(
            "final answer",
            request_id="chatcmpl-tool-2",
            usage={"prompt_tokens": 200, "completion_tokens": 30,
                   "total_tokens": 230},
        ),
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)
    from backend.app.tools.mock_tools import register_mock_tools
    from backend.app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_mock_tools(registry)
    from backend.app.services.tool_chat_service import ToolChatService

    result = await ToolChatService(provider).chat(
        "帮我查询 MAT001 的库存", registry=registry,
    )
    assert result.tool_calls[0].tool_name == "get_inventory"
    assert handler.request_count == 2
    assert len(sink.observations) == 2

    obs1, obs2 = sink.observations
    accounting1 = token_accounting_from_usage(obs1.usage)
    accounting2 = token_accounting_from_usage(obs2.usage)

    # request-level：各自独立、不合并
    assert accounting1 is not None and accounting2 is not None
    assert accounting1 is not accounting2
    assert accounting1.prompt_tokens == 100
    assert accounting2.prompt_tokens == 200
    assert accounting1.total_tokens == 110
    assert accounting2.total_tokens == 230
    # 无 "Accounting total" 对象产生（无 aggregation）
    assert not hasattr(token_accounting_from_usage, "aggregate")


# ============================================================
# Test 5 — T2S Semantic Retry：request-level（str 契约）
# ============================================================

async def test_t2s_retry_request_level_accounting() -> None:
    """T2S semantic retry：2 requests → 2 observations。
    T2S 走 str 契约（无 usage）→ 每个 request 的 accounting data 为
    None（任务书 §十五：无 usage 时 0 accounting data 完全正常）；
    2 个独立 observation / 2 次显式 accounting 调用，不合并、
    不虚构 usage、不新增 retry_count / total_retry_tokens 字段。"""
    handler = ScriptedHandler([
        {"id": "r1", "model": "deepseek-chat",
         "choices": [{"index": 0, "finish_reason": "stop",
                      "message": {"role": "assistant", "content": "SELECT 1"}}]},
        {"id": "r2", "model": "deepseek-chat",
         "choices": [{"index": 0, "finish_reason": "stop",
                      "message": {"role": "assistant",
                                  "content": "```sql\nSELECT 1 LIMIT 1\n```"}}]},
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)
    t2s = TextToSQLService(llm_client=provider, max_attempts=3)

    result = await t2s.generate("知识文档有哪些？", database_context="ctx")

    assert result.validated is True
    assert result.attempts == 2
    assert handler.request_count == 2
    assert len(sink.observations) == 2           # request-level 对齐

    # 每个实际 request 一次显式 accounting 调用（str 契约 → None）
    accountings = [
        token_accounting_from_usage(obs.usage) for obs in sink.observations
    ]
    assert accountings == [None, None]           # 不虚构 usage
    assert len({id(obs) for obs in sink.observations}) == 2
    assert not hasattr(sink.observations[0], "retry_count")
    assert not hasattr(sink.observations[0], "total_retry_tokens")


# ============================================================
# Test 6 — Refusal：1 request / 1 observation / 1 accounting
# ============================================================

async def test_refusal_accounting_str_contract() -> None:
    """Refusal：1 request → 1 observation（success=True）。
    T2S 走 str 契约（chat 无 tools）→ 无 usage → 0 accounting data
    （任务书 §十五：完全正常；usage 存在时的对齐行为已由 Test 1
    的 LLMResponse 契约链路锁定）。"""
    from backend.app.services.text_to_sql_service import REFUSAL_MARKER

    handler = ScriptedHandler([
        _content_body(REFUSAL_MARKER, request_id="chatcmpl-refusal-1"),
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)
    t2s = TextToSQLService(llm_client=provider, max_attempts=3)

    result = await t2s.generate("删除所有库存", database_context="ctx")

    assert result.status == "refusal"
    assert result.attempts == 1
    assert handler.request_count == 1
    assert len(sink.observations) == 1
    observation = sink.observations[0]
    assert observation.success is True           # refusal 是业务结果
    # str 契约：无 usage → 0 accounting data（不虚构）
    assert observation.usage is None
    assert token_accounting_from_usage(observation.usage) is None


async def test_refusal_without_usage_zero_accounting_data() -> None:
    """Refusal + 无 usage：1 observation、0 accounting data——同样正常。"""
    from backend.app.services.text_to_sql_service import REFUSAL_MARKER

    handler = ScriptedHandler([
        _content_body(REFUSAL_MARKER, request_id="chatcmpl-refusal-2"),
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)
    t2s = TextToSQLService(llm_client=provider, max_attempts=3)

    result = await t2s.generate("删除所有库存", database_context="ctx")

    assert result.status == "refusal"
    assert len(sink.observations) == 1
    assert sink.observations[0].success is True
    assert token_accounting_from_usage(sink.observations[0].usage) is None


# ============================================================
# Test 7 — Failure：failure observation / 0 accounting
# ============================================================

async def test_failure_request_no_accounting() -> None:
    """LLM request 失败：无 LLMResponse → usage=None → accounting=None；
    failure observation 契约保持；不根据异常估算 token。"""

    def handler_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "upstream down"})

    sink = CollectingObservationSink()
    provider = _obs_provider(handler_503, sink)

    with pytest.raises(LLMRequestError):
        await provider.chat([{"role": "user", "content": "hi"}], tools=_TOOLS)

    assert len(sink.observations) == 1
    observation = sink.observations[0]
    assert observation.success is False
    assert observation.usage is None             # 无 response → 无 usage
    assert token_accounting_from_usage(observation.usage) is None
    assert observation.error_type == "LLMRequestError"


# ============================================================
# Test 8 — Generate：str 契约不虚构 usage / accounting
# ============================================================

async def test_generate_contract_usage_visible_not_fabricated() -> None:
    """generate() → str 不变；Usage Visibility（Phase 3.10.12）：
    usage 经 lifecycle 可见且来自实际响应（非虚构——响应里的
    数值原样透传；仍不从字符串估算）。"""
    handler = ScriptedHandler([
        _content_body(
            "hello",
            request_id="chatcmpl-gen-1",
            usage={"prompt_tokens": 999, "completion_tokens": 1,
                   "total_tokens": 1000},
        ),
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)

    answer = await provider.generate("ping")

    assert answer == "hello"                     # 返回契约不变
    assert len(sink.observations) == 1
    observation = sink.observations[0]
    assert observation.success is True
    # Usage Visibility：实际响应的 usage 原样可见（非虚构、非估算）
    assert observation.usage is not None
    assert observation.usage.prompt_tokens == 999
    assert observation.usage.total_tokens == 1000
    assert observation.model == "deepseek-chat"
    accounting = token_accounting_from_usage(observation.usage)
    assert accounting is observation.usage


# ============================================================
# Test 9 — Isolation：accounting 故障不影响业务 / observation
# ============================================================

async def test_accounting_failure_does_not_affect_business(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """故意破坏 accounting（token_accounting_from_usage 抛异常）：
    LLM response / business result / observation 完全不受影响
    （accounting 是显式纯函数调用，不在生产关键路径上）。"""
    import backend.app.llm.accounting as accounting_mod

    def boom(usage: Any) -> Any:
        raise RuntimeError("accounting failure")

    monkeypatch.setattr(accounting_mod, "token_accounting_from_usage", boom)

    handler = ScriptedHandler([
        _content_body(
            "business answer",
            request_id="chatcmpl-iso-1",
            usage={"prompt_tokens": 10, "completion_tokens": 2,
                   "total_tokens": 12},
        ),
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)

    # 业务调用完全不受 accounting 故障影响
    response = await provider.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    assert isinstance(response, LLMResponse)
    assert response.content == "business answer"
    assert response.usage is not None and response.usage.total_tokens == 12

    # observation 照常产生
    assert len(sink.observations) == 1
    assert sink.observations[0].success is True
    assert sink.observations[0].usage is response.usage


# ============================================================
# Test 10 — No Cost Auto Calculation（结构性 + 行为性）
# ============================================================

def test_client_structurally_cannot_calculate_cost() -> None:
    """结构性隔离：client.py 不 import accounting 模块（AST 锁定）——
    生产 Client 不可能自动调用 calculate_llm_cost。"""
    import ast
    import inspect

    import backend.app.llm.client as client_mod

    tree = ast.parse(inspect.getsource(client_mod))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(
        name.split(".")[0] == "accounting" for name in imported
    )


async def test_no_automatic_cost_calculation_during_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """行为性验证：普通 LLM request 全程不自动调用 calculate_llm_cost
    （spy 零调用）。"""
    import backend.app.llm.accounting as accounting_mod

    calls: list[Any] = []

    def spy(usage: Any, pricing: Any) -> None:
        calls.append((usage, pricing))
        raise AssertionError("生产 Client 不得自动计算 Cost")

    monkeypatch.setattr(accounting_mod, "calculate_llm_cost", spy)

    handler = ScriptedHandler([
        _content_body(
            "answer",
            request_id="chatcmpl-spy-1",
            usage={"prompt_tokens": 10, "completion_tokens": 2,
                   "total_tokens": 12},
        ),
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)

    response = await provider.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    assert isinstance(response, LLMResponse)
    assert len(sink.observations) == 1
    assert calls == []                           # spy 零调用


# ============================================================
# §十九 — synthetic pricing 链（纯函数测试，currency="TEST"）
# ============================================================

async def test_observation_usage_with_synthetic_pricing_chain() -> None:
    """Observation + Usage + Synthetic Pricing → LLMCost（显式纯函数调用，
    仅发生在测试中；currency="TEST" 显式）。"""
    handler = ScriptedHandler([
        _content_body(
            "answer",
            request_id="chatcmpl-price-1",
            usage={"prompt_tokens": 1_000_000, "completion_tokens": 2_000_000,
                   "total_tokens": 3_000_000},
        ),
    ])
    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)

    response = await provider.chat(
        [{"role": "user", "content": "hi"}], tools=_TOOLS,
    )
    observation = sink.observations[0]
    assert observation.usage is response.usage

    pricing = LLMPricing(
        input_price_per_1m_tokens=Decimal("1"),
        output_price_per_1m_tokens=Decimal("2"),
        currency="TEST",
    )
    cost = calculate_llm_cost(observation.usage, pricing)

    assert cost is not None
    assert cost.input_cost == Decimal("1")
    assert cost.output_cost == Decimal("4")
    assert cost.total_cost == Decimal("5")
    assert cost.currency == "TEST"


# ============================================================
# §二十二 — Security：Accounting 只接触 usage 数值字段
# ============================================================

def test_accounting_touches_only_usage_token_fields() -> None:
    """毒化对象：usage 携带 secrets 属性 → calculate_llm_cost 只读
    token 字段，LLMCost 不含任何 secrets / prompt / messages。"""
    import dataclasses

    class PoisonUsage:
        """带 secrets 的毒 usage 对象（模拟不可信输入）。"""

        prompt_tokens = 100
        completion_tokens = 20
        total_tokens = 120
        api_key = "SK-SECRET-KEY"
        prompt = "SECRET-PROMPT-CONTENT"
        messages = '[{"role":"user","content":"SECRET"}]'
        raw_response = '{"secret": true}'

    cost = calculate_llm_cost(PoisonUsage(), _synthetic_pricing())

    assert cost is not None
    as_dict = dataclasses.asdict(cost)
    assert set(as_dict) <= {
        "input_cost", "output_cost", "total_cost", "currency",
    }
    for value in as_dict.values():
        if isinstance(value, str):
            assert "SECRET" not in value


def _synthetic_pricing() -> LLMPricing:
    return LLMPricing(
        input_price_per_1m_tokens=Decimal("1"),
        output_price_per_1m_tokens=Decimal("2"),
        currency="TEST",
    )


# ============================================================
# §二十三 — Concurrency：usage 关联无交叉
# ============================================================

async def test_concurrent_usage_association_no_cross_talk() -> None:
    """5 个并发请求（各自 usage 不同）→ 5 observations，
    observation.usage 与各自 response.usage 一一对应
    （request-scoped 参数传递，无共享状态 → 天然无交叉）。"""
    sink = CollectingObservationSink()
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        n = counter["n"]  # asyncio 单线程内同步分配
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

    provider = _obs_provider(handler, sink)
    responses = await asyncio.gather(
        *[
            provider.chat([{"role": "user", "content": f"q-{i}"}],
                          tools=_TOOLS)
            for i in range(5)
        ]
    )

    assert len(sink.observations) == 5
    # 每个 observation 关联各自 request 的 usage（identity 一一对应）
    usage_totals = set()
    for observation, response in zip(sink.observations, responses):
        assert isinstance(response, LLMResponse)
        assert observation.usage is response.usage
        accounting = token_accounting_from_usage(observation.usage)
        assert accounting is response.usage
        assert accounting is not None
        usage_totals.add(accounting.total_tokens)
    # 5 组互不交叉的 usage 关联（110/220/.../550）
    assert usage_totals == {110 * n for n in range(1, 6)}
    assert all(obs.success for obs in sink.observations)
