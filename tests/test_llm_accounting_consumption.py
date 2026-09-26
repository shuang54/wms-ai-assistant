"""LLM Usage & Cost Consumption Boundary 测试（Phase 3.10.13）。

覆盖（任务书 §二十 ~ §三十）：

    §二十   1. Full Usage  2. Partial Usage  3. None  4. Identity  5. Immutability
    §二十一 Cost Consumption（synthetic pricing，Decimal）
    §二十二 Cost=None（usage None，即使 pricing 存在也不产生 0）
    §二十三 Pricing=None（不猜价）
    §二十四 Security（毒化 Observation：Consumer 只读取 usage）
    §二十五 No Side Effects（AST：无 IO / 网络 / DB）
    §二十六 Concurrency（5 并发 observation，identity 一一对应）
    §二十七 Tool Calling（2 observations → 2 独立消费，不合并）
    §二十八 T2S Retry（request-level，无 total retry tokens）
    §二十九 RAG / Router 回归（不影响业务结果）
    §三十   Backward Compatibility（旧 API 继续工作；Consumer optional）

synthetic pricing：input=1 / output=2 / currency="TEST"（无真实价格）。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from backend.app.llm.accounting import LLMPricing, calculate_llm_cost
from backend.app.llm.accounting_consumer import consume_cost, consume_usage
from backend.app.llm.client import (
    LLMResponse,
    LLMUsage,
    OpenAICompatibleClient,
    create_llm_client,
)
from backend.app.llm.deepseek_provider import DeepSeekProvider
from backend.app.llm.observability import (
    LLMObservation,
    NoopAccountingSink,
)

# LLMSettings 定义于 backend.app.config
from backend.app.config import LLMSettings


# ============================================================
# Helpers
# ============================================================

def _pricing(currency: str = "TEST") -> LLMPricing:
    """synthetic pricing（测试专用，非真实价格）。"""
    return LLMPricing(
        input_price_per_1m_tokens=Decimal("1"),
        output_price_per_1m_tokens=Decimal("2"),
        currency=currency,
    )


def _observation(
    usage: LLMUsage | None = None, **overrides: Any
) -> LLMObservation:
    """手工构造 Observation（确定性消费测试）。"""
    kwargs: dict[str, Any] = {
        "provider": "deepseek-test",
        "model": "deepseek-chat",
        "success": True,
        "finish_reason": "stop",
        "usage": usage,
        "request_id": "chatcmpl-consume-1",
    }
    kwargs.update(overrides)
    return LLMObservation(**kwargs)


def _imported_modules(module: Any) -> set[str]:
    """解析模块实际 import 的顶层模块名（AST）。"""
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ============================================================
# §二十 1. Full Usage
# ============================================================

def test_consume_full_usage() -> None:
    """Full usage：Consumer 返回完整 LLMUsage（三字段原样）。"""
    usage = LLMUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120)
    observation = _observation(usage)

    result = consume_usage(observation)

    assert result is not None
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 20
    assert result.total_tokens == 120
    # 不复制：identity 保持（§九）
    assert result is observation.usage


# ============================================================
# §二十 2. Partial Usage
# ============================================================

@pytest.mark.parametrize(
    "usage_kwargs,expected",
    [
        ({"prompt_tokens": 100}, (100, None, None)),
        ({"completion_tokens": 200}, (None, 200, None)),
        ({"prompt_tokens": 100, "completion_tokens": 50}, (100, 50, None)),
    ],
)
def test_consume_partial_usage_verbatim(
    usage_kwargs: dict[str, Any],
    expected: tuple[int | None, int | None, int | None],
) -> None:
    """Partial usage 原样消费（不补全、不重算、不 round）。"""
    observation = _observation(LLMUsage(**usage_kwargs))

    result = consume_usage(observation)

    assert result is not None
    assert (
        result.prompt_tokens,
        result.completion_tokens,
        result.total_tokens,
    ) == expected


# ============================================================
# §二十 3. Usage=None
# ============================================================

def test_consume_usage_none() -> None:
    """usage=None → None（不是 0、不是 LLMUsage(0,0,0)、不估算）。"""
    observation = _observation(None)

    result = consume_usage(observation)

    assert result is None
    assert result != 0
    assert result != LLMUsage(0, 0, 0)


def test_consume_observation_none() -> None:
    """observation=None → None（Consumer 无状态、无异常）。"""
    assert consume_usage(None) is None
    assert consume_cost(None, _pricing()) is None


# ============================================================
# §二十 4. Identity
# ============================================================

def test_consume_usage_identity() -> None:
    """identity 契约：consumer_result is observation.usage
    （Provider → Response.usage → Observation.usage → Consumer 同一对象）。"""
    usage = LLMUsage(prompt_tokens=7, completion_tokens=3, total_tokens=10)
    observation = _observation(usage)

    assert consume_usage(observation) is usage
    assert consume_usage(observation) is observation.usage
    # 多次消费返回同一对象（无复制、无缓存失效）
    assert consume_usage(observation) is consume_usage(observation)


# ============================================================
# §二十 5. Immutability
# ============================================================

def test_consume_does_not_mutate_usage() -> None:
    """Consumer 不修改 LLMUsage（frozen；消费前后值完全一致）。"""
    usage = LLMUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120)
    before = (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)

    consume_usage(_observation(usage))
    consume_cost(_observation(usage), _pricing())

    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == before
    with pytest.raises(Exception):  # frozen dataclass：字段不可写
        usage.prompt_tokens = 999  # type: ignore[misc]


# ============================================================
# §二十一 Cost Consumption
# ============================================================

def test_consume_cost_with_synthetic_pricing() -> None:
    """Observation + synthetic pricing → LLMCost（Decimal，1/4/5）。"""
    usage = LLMUsage(
        prompt_tokens=1_000_000, completion_tokens=2_000_000,
        total_tokens=3_000_000,
    )
    observation = _observation(usage)

    cost = consume_cost(observation, _pricing())

    assert cost is not None
    # Decimal（非 float）
    assert isinstance(cost.input_cost, Decimal)
    assert isinstance(cost.output_cost, Decimal)
    assert isinstance(cost.total_cost, Decimal)
    assert cost.input_cost == Decimal("1")
    assert cost.output_cost == Decimal("4")
    assert cost.total_cost == Decimal("5")
    assert cost.currency == "TEST"


def test_consume_cost_reuses_existing_calculator() -> None:
    """Cost 消费复用现有 calculate_llm_cost（结果一致，非重复实现）。"""
    usage = LLMUsage(prompt_tokens=1_000_000, completion_tokens=2_000_000,
                     total_tokens=3_000_000)
    observation = _observation(usage)
    pricing = _pricing()

    assert consume_cost(observation, pricing) == calculate_llm_cost(usage, pricing)


# ============================================================
# §二十二 Cost=None（usage None）
# ============================================================

def test_consume_cost_none_when_usage_missing() -> None:
    """usage=None → cost=None（即使 pricing 存在，也不产生 0）。"""
    observation = _observation(None)

    cost = consume_cost(observation, _pricing())

    assert cost is None
    assert cost != 0


# ============================================================
# §二十三 Pricing=None
# ============================================================

def test_consume_cost_none_when_pricing_missing() -> None:
    """pricing=None → cost=None（不猜价；不修改 calculate_llm_cost
    的既有 Contract——None 判断只发生在消费层）。"""
    usage = LLMUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120)
    observation = _observation(usage)

    assert consume_cost(observation, None) is None


# ============================================================
# §二十四 Security：Consumer 只读取 observation.usage
# ============================================================

def test_consumer_only_reads_usage_field() -> None:
    """毒化 Observation：除 usage 外所有属性读取即失败 →
    consume_usage / consume_cost 正常工作（只读取 usage）。"""

    def _boom(_: Any) -> Any:
        raise AssertionError("Consumer 不得读取该字段")

    usage_value = LLMUsage(
        prompt_tokens=100, completion_tokens=20, total_tokens=120
    )

    class PoisonObservation:
        usage = usage_value

    # property 必须定义在类上（描述符协议）才会真正拦截访问
    for field in ("provider", "model", "success", "finish_reason",
                  "request_id", "error_type", "latency_ms", "metadata",
                  "content", "messages", "tool_calls"):
        setattr(PoisonObservation, field, property(_boom))

    poison = PoisonObservation()
    # 毒化生效校验：读取非 usage 字段必然失败
    with pytest.raises(AssertionError):
        _ = poison.model

    assert consume_usage(poison) is usage_value  # type: ignore[arg-type]
    cost = consume_cost(poison, _pricing())  # type: ignore[arg-type]
    assert cost is not None
    # 100/1M×1 + 20/1M×2 = 0.0001 + 0.00004 = 0.00014
    assert cost.input_cost == Decimal("0.0001")
    assert cost.output_cost == Decimal("0.00004")
    assert cost.total_cost == Decimal("0.00014")


# ============================================================
# §二十五 No Side Effects（AST）
# ============================================================

def test_consumer_has_no_io_side_effects() -> None:
    """Consumer 模块无网络 / DB / 队列 / 文件 / sleep 相关 import。"""
    import backend.app.llm.accounting_consumer as mod

    imported = _imported_modules(mod)
    forbidden = {
        "httpx", "requests", "aiohttp", "urllib", "sqlalchemy", "psycopg",
        "sqlite3", "redis", "kafka", "asyncio", "os", "time", "pathlib",
        "json", "pickle", "openai",
    }
    assert not any(name.split(".")[0] in forbidden for name in imported)


# ============================================================
# §二十六 Concurrency：5 并发 observation 无串线
# ============================================================

async def test_concurrent_consumption_no_cross_talk() -> None:
    """5 并发请求的 Observation → 各自消费到自己的 usage
    （无共享状态、无串线）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        tag = body["messages"][0]["content"]  # q-0 .. q-4
        n = int(tag.split("-")[1]) + 1
        return httpx.Response(
            200,
            json={
                "id": f"chatcmpl-consume-{n}",
                "model": "deepseek-chat",
                "choices": [{
                    "index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": f"a-{n}"},
                }],
                "usage": {
                    "prompt_tokens": 100 * n,
                    "completion_tokens": 10 * n,
                    "total_tokens": 110 * n,
                },
            },
        )

    class Sink:
        def __init__(self) -> None:
            self.observations: list[LLMObservation] = []

        def record(self, observation: LLMObservation) -> None:
            self.observations.append(observation)

    sink = Sink()
    client = OpenAICompatibleClient(
        api_key="k", base_url="https://api.example.com/v1",
        model="configured-model", provider="deepseek-test",
        transport=httpx.MockTransport(handler),
        observation_sink=sink,
    )
    provider = DeepSeekProvider(client)

    responses = await asyncio.gather(
        *[provider.chat([{"role": "user", "content": f"q-{i}"}])
          for i in range(5)]
    )
    assert all(isinstance(r, str) for r in responses)   # str 契约不变
    assert len(sink.observations) == 5

    # 并发消费：identity 与各自 usage 一一对应
    consumed = await asyncio.gather(
        *[asyncio.to_thread(consume_usage, obs) for obs in sink.observations]
    )
    for observation, usage in zip(sink.observations, consumed):
        assert usage is observation.usage
    totals = {usage.total_tokens for usage in consumed if usage is not None}
    assert totals == {110 * n for n in range(1, 6)}     # 无串线


# ============================================================
# §二十七 Tool Calling：独立消费（不合并）
# ============================================================

async def test_tool_calling_independent_consumption() -> None:
    """Tool Calling 两轮：2 observations → 2 次独立消费
    （usage 各自独立，不合并为 total）。"""
    first_body: dict[str, Any] = {
        "id": "chatcmpl-tc-1", "model": "deepseek-chat",
        "choices": [{
            "index": 0, "finish_reason": "tool_calls",
            "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {
                        "name": "get_inventory",
                        "arguments": '{"material_code": "MAT001"}',
                    },
                }],
            },
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                  "total_tokens": 110},
    }
    second_body: dict[str, Any] = {
        "id": "chatcmpl-tc-2", "model": "deepseek-chat",
        "choices": [{
            "index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": "final"},
        }],
        "usage": {"prompt_tokens": 200, "completion_tokens": 30,
                  "total_tokens": 230},
    }
    rounds = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        rounds["n"] += 1
        return httpx.Response(
            200, json=first_body if rounds["n"] == 1 else second_body
        )

    class Sink:
        def __init__(self) -> None:
            self.observations: list[LLMObservation] = []

        def record(self, observation: LLMObservation) -> None:
            self.observations.append(observation)

    sink = Sink()
    client = OpenAICompatibleClient(
        api_key="k", base_url="https://api.example.com/v1",
        model="configured-model", provider="deepseek-test",
        transport=httpx.MockTransport(handler),
        observation_sink=sink,
    )
    from backend.app.tools.mock_tools import register_mock_tools
    from backend.app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_mock_tools(registry)
    from backend.app.services.tool_chat_service import ToolChatService

    result = await ToolChatService(DeepSeekProvider(client)).chat(
        "帮我查询 MAT001 的库存", registry=registry,
    )
    assert result.tool_calls[0].tool_name == "get_inventory"
    assert rounds["n"] == 2
    assert len(sink.observations) == 2

    usage1 = consume_usage(sink.observations[0])
    usage2 = consume_usage(sink.observations[1])
    assert usage1 is not None and usage2 is not None
    assert usage1 is not usage2                       # 不合并
    assert usage1.total_tokens == 110
    assert usage2.total_tokens == 230
    # 各自 cost 独立
    cost1 = consume_cost(sink.observations[0], _pricing())
    cost2 = consume_cost(sink.observations[1], _pricing())
    assert cost1 is not None and cost2 is not None
    assert cost1.total_cost == Decimal("0.00012")    # 100×1 + 10×2 /1M
    assert cost2.total_cost == Decimal("0.00026")    # 200×1 + 30×2 /1M


# ============================================================
# §二十八 T2S Retry：request-level
# ============================================================

async def test_t2s_retry_request_level_consumption() -> None:
    """T2S semantic retry：2 requests → 2 observations → 2 次
    request-level 消费（无 total retry tokens / retry_count）。"""
    first = {
        "id": "chatcmpl-t2s-1", "model": "deepseek-chat",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "SELECT 1"}}],
    }
    second = {
        "id": "chatcmpl-t2s-2", "model": "deepseek-chat",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant",
                                 "content": "```sql\nSELECT 1 LIMIT 1\n```"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                  "total_tokens": 120},
    }
    bodies = [first, second]

    class Sink:
        def __init__(self) -> None:
            self.observations: list[LLMObservation] = []

        def record(self, observation: LLMObservation) -> None:
            self.observations.append(observation)

    sink = Sink()
    client = OpenAICompatibleClient(
        api_key="k", base_url="https://api.example.com/v1",
        model="configured-model", provider="deepseek-test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=bodies.pop(0))
        ),
        observation_sink=sink,
    )
    from backend.app.services.text_to_sql_service import TextToSQLService

    result = await TextToSQLService(llm_client=DeepSeekProvider(client)).generate(
        "知识文档有哪些？", database_context="ctx",
    )
    assert result.validated is True and result.attempts == 2
    assert len(sink.observations) == 2

    usage1 = consume_usage(sink.observations[0])
    usage2 = consume_usage(sink.observations[1])
    assert usage1 is None                             # resp #1 无 usage
    assert usage2 is not None
    assert usage2.total_tokens == 120
    # 无 retry 聚合字段
    for observation in sink.observations:
        assert not hasattr(observation, "retry_count")
        assert not hasattr(observation, "total_retry_tokens")


# ============================================================
# §二十九 RAG 回归：Consumer 不影响业务结果
# ============================================================

async def test_rag_consumption_does_not_affect_business() -> None:
    """RAG 链路：业务结果（str answer）不变；
    Usage 经 Consumer 可见（RAG 业务代码零修改）。"""
    from backend.app.services.rag_service import RagService
    from backend.app.services.vector_search_service import VectorSearchResult

    class FakeVectorSearch:
        async def search(self, query: str, *, top_k: int):
            return [VectorSearchResult(
                chunk_id=1, document_id=1, chunk_index=0, content="kb",
                distance=0.1, similarity=0.9, metadata={},
            )]

    class Sink:
        def __init__(self) -> None:
            self.observations: list[LLMObservation] = []

        def record(self, observation: LLMObservation) -> None:
            self.observations.append(observation)

    sink = Sink()
    client = OpenAICompatibleClient(
        api_key="k", base_url="https://api.example.com/v1",
        model="configured-model", provider="deepseek-test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={
                "id": "chatcmpl-rag", "model": "deepseek-chat",
                "choices": [{
                    "index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "rag answer"},
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                          "total_tokens": 120},
            })
        ),
        observation_sink=sink,
    )
    rag = RagService(
        vector_search_service=FakeVectorSearch(),
        llm_client=DeepSeekProvider(client),
    )

    response = await rag.answer("采购入库的流程是什么？")

    assert isinstance(response.answer, str)
    assert response.answer == "rag answer"            # 业务结果不变
    assert len(sink.observations) == 1
    usage = consume_usage(sink.observations[0])
    assert usage is not None and usage.total_tokens == 120


# ============================================================
# §三十 Backward Compatibility：Consumer 是 optional
# ============================================================

async def test_consumer_is_optional_and_backward_compatible() -> None:
    """不使用 Consumer 时，旧 API（create_llm_client / sinks / Client）
    行为完全不变；Consumer 只是 optional 纯函数入口。"""
    llm_settings = LLMSettings(
        api_key="k", base_url="https://api.example.com/v1", model="m",
    )
    provider = create_llm_client(llm_settings)       # 旧调用形式
    assert isinstance(provider, DeepSeekProvider)

    # 默认 accounting sink 仍为 No-op
    client = OpenAICompatibleClient(
        api_key="k", base_url="https://api.example.com/v1", model="m",
    )
    assert isinstance(client._accounting_sink, NoopAccountingSink)

    # Consumer 不影响 Observation / Sink 契约
    observation = _observation(LLMUsage(1, 2, 3))
    assert consume_usage(observation) is observation.usage
