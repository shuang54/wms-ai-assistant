"""LLM Usage Visibility Bridge 测试（Phase 3.10.12）。

核心（任务书 §四 / §五）：

    增加 Usage 可见性，但不改变业务返回值：

        Provider → LLMResponse（内部，usage/model/finish_reason/metadata）
            ├── compatibility boundary → str（业务调用方）
            └── Observation → AccountingSink → LLMUsage

覆盖（任务书 §二十七 1-15）：

    1.  generate() usage visibility
    2.  no-tools chat usage visibility
    3.  model preservation
    4.  finish_reason preservation
    5.  request_id preservation
    6.  Usage=None
    7.  Partial Usage
    8.  Failure（不伪造 usage）
    9.  Tool Calling（request-level 独立）
    10. T2S semantic retry（request-level 独立）
    11. Refusal（usage 不因 refusal 丢失）
    12. Concurrency（5 并发无串线）
    13. Existing caller compatibility（旧调用方仍拿 str）
    14. No global state（AST：无 last_usage / last_response）
    15. No DB / network（visibility 不产生额外调用）

全部使用 Scripted Provider / MockTransport：real LLM = 0 / embedding = 0。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
from typing import Any

import httpx
import pytest

from backend.app.llm.accounting import token_accounting_from_usage
from backend.app.llm.client import (
    LLMRequestError,
    LLMResponse,
    LLMUsage,
    OpenAICompatibleClient,
)
from backend.app.llm.deepseek_provider import DeepSeekProvider
from backend.app.llm.observability import LLMObservation


# ============================================================
# 测试基础设施
# ============================================================

class CollectingObservationSink:
    def __init__(self) -> None:
        self.observations: list[LLMObservation] = []

    def record(self, observation: LLMObservation) -> None:
        self.observations.append(observation)


class CollectingAccountingSink:
    """record(observation) 内部派生 accounting（只读取 usage）。"""

    def __init__(self) -> None:
        self.observations: list[LLMObservation] = []
        self.accountings: list[Any] = []

    def record(self, observation: LLMObservation) -> None:
        self.observations.append(observation)
        self.accountings.append(
            token_accounting_from_usage(observation.usage)
        )


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


def _obs_provider(handler: Any, sink: Any = None) -> DeepSeekProvider:
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
    content: str | None,
    *,
    request_id: str = "chatcmpl-vis-1",
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


_FULL_USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_inventory",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


# ============================================================
# Test 1 / 2 — generate() 与 no-tools chat 的 usage visibility
# ============================================================

async def test_generate_usage_visibility() -> None:
    """Test 1：generate() → public str 不变；observation/accounting
    获得实际响应的 usage（identity 链完整）。"""
    sink = CollectingObservationSink()
    handler = ScriptedHandler([
        _content_body("hello", request_id="chatcmpl-vis-gen",
                      usage=_FULL_USAGE),
    ])
    provider = _obs_provider(handler, sink)

    answer = await provider.generate("ping")

    assert answer == "hello"                          # public str 契约不变
    assert len(sink.observations) == 1
    observation = sink.observations[0]
    assert observation.usage == LLMUsage(100, 20, 120)
    accounting = token_accounting_from_usage(observation.usage)
    assert accounting == LLMUsage(100, 20, 120)       # accounting 可见


async def test_no_tools_chat_usage_visibility() -> None:
    """Test 2：chat(messages) → public str 不变；lifecycle usage 可见。"""
    sink = CollectingObservationSink()
    handler = ScriptedHandler([
        _content_body("hello", request_id="chatcmpl-vis-chat",
                      usage=_FULL_USAGE),
    ])
    provider = _obs_provider(handler, sink)

    result = await provider.chat([{"role": "user", "content": "hi"}])

    assert result == "hello"                          # public str 契约不变
    observation = sink.observations[0]
    assert observation.usage == LLMUsage(100, 20, 120)
    accounting = token_accounting_from_usage(observation.usage)
    assert accounting == LLMUsage(100, 20, 120)


# ============================================================
# Test 3 / 4 / 5 — model / finish_reason / request_id preservation
# ============================================================

async def test_model_preserved_from_response_not_config() -> None:
    """Test 3：observation.model 来自实际响应（configured-model ≠ 响应值）。"""
    sink = CollectingObservationSink()
    handler = ScriptedHandler([
        _content_body("hello", request_id="chatcmpl-vis-m", usage=_FULL_USAGE),
    ])
    provider = _obs_provider(handler, sink)  # client 配置 model="configured-model"

    await provider.chat([{"role": "user", "content": "hi"}])

    assert sink.observations[0].model == "deepseek-chat"  # 实际响应值


async def test_finish_reason_preserved() -> None:
    """Test 4：finish_reason 原样保留（未知字符串不转换）。"""
    sink = CollectingObservationSink()
    handler = ScriptedHandler([
        _content_body("hello", request_id="chatcmpl-vis-fr",
                      finish_reason="some_future_reason"),
    ])
    provider = _obs_provider(handler, sink)

    await provider.chat([{"role": "user", "content": "hi"}])

    assert sink.observations[0].finish_reason == "some_future_reason"


async def test_request_id_preserved() -> None:
    """Test 5：request_id 来自响应 id（经 metadata 白名单），不伪造。"""
    sink = CollectingObservationSink()
    handler = ScriptedHandler([
        _content_body("hello", request_id="chatcmpl-vis-rid"),
    ])
    provider = _obs_provider(handler, sink)

    await provider.chat([{"role": "user", "content": "hi"}])

    assert sink.observations[0].request_id == "chatcmpl-vis-rid"


# ============================================================
# Test 6 / 7 — Usage=None / Partial Usage
# ============================================================

async def test_usage_none_no_estimation() -> None:
    """Test 6：响应无 usage → public str 正常 + observation.usage None
    + accounting None（不估算 / 不补 0 / 不伪造）。"""
    sink = CollectingObservationSink()
    handler = ScriptedHandler([
        _content_body("hello", request_id="chatcmpl-vis-none"),  # 无 usage
    ])
    provider = _obs_provider(handler, sink)

    result = await provider.chat([{"role": "user", "content": "hi"}])

    assert result == "hello"
    observation = sink.observations[0]
    assert observation.success is True
    assert observation.usage is None
    assert token_accounting_from_usage(observation.usage) is None


@pytest.mark.parametrize(
    "usage_dict,expected",
    [
        ({"prompt_tokens": 100}, (100, None, None)),
        ({"completion_tokens": 200}, (None, 200, None)),
        ({"prompt_tokens": 100, "completion_tokens": 50}, (100, 50, None)),
    ],
)
async def test_partial_usage_visible_verbatim(
    usage_dict: dict[str, Any], expected: tuple[int | None, int | None, int | None]
) -> None:
    """Test 7：partial usage 原样进入 lifecycle（不补全）。"""
    sink = CollectingObservationSink()
    handler = ScriptedHandler([
        _content_body("hello", request_id="chatcmpl-vis-partial",
                      usage=usage_dict),
    ])
    provider = _obs_provider(handler, sink)

    result = await provider.chat([{"role": "user", "content": "hi"}])

    assert result == "hello"
    observation = sink.observations[0]
    assert (
        observation.usage.prompt_tokens,
        observation.usage.completion_tokens,
        observation.usage.total_tokens,
    ) == expected


# ============================================================
# Test 8 — Failure：不伪造 usage
# ============================================================

async def test_failure_no_fabricated_usage() -> None:
    """Test 8：5xx 失败 → LLMRequestError + failure observation
    （usage=None）+ accounting=None；不根据 partial response 猜测。"""

    def handler_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    sink = CollectingObservationSink()
    provider = _obs_provider(handler_503, sink)

    with pytest.raises(LLMRequestError):
        await provider.chat([{"role": "user", "content": "hi"}])

    assert sink.observations[0].success is False
    assert sink.observations[0].usage is None
    assert token_accounting_from_usage(sink.observations[0].usage) is None


# ============================================================
# Test 9 — Tool Calling：request-level 独立（不回归）
# ============================================================

async def test_tool_calling_usage_still_visible_and_independent() -> None:
    """Test 9：Tool Calling 两轮各自 usage 独立可见；tool_calls 不丢失。"""
    sink = CollectingObservationSink()
    first_body = {
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
    second_body = _content_body(
        "final", request_id="chatcmpl-tc-2",
        usage={"prompt_tokens": 200, "completion_tokens": 30,
               "total_tokens": 230},
    )
    round_counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        round_counter["n"] += 1
        body = first_body if round_counter["n"] == 1 else second_body
        return httpx.Response(200, json=body)

    provider = _obs_provider(handler, sink)
    from backend.app.tools.mock_tools import register_mock_tools
    from backend.app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_mock_tools(registry)
    from backend.app.services.tool_chat_service import ToolChatService

    result = await ToolChatService(provider).chat(
        "帮我查询 MAT001 的库存", registry=registry,
    )
    assert result.tool_calls[0].tool_name == "get_inventory"  # tool_calls 不丢失
    assert round_counter["n"] == 2
    assert len(sink.observations) == 2

    obs1, obs2 = sink.observations
    assert obs1.finish_reason == "tool_calls"
    assert obs1.usage is not None and obs1.usage.total_tokens == 110
    assert obs2.finish_reason == "stop"
    assert obs2.usage is not None and obs2.usage.total_tokens == 230
    # 各自 accounting 独立
    acc1 = token_accounting_from_usage(obs1.usage)
    acc2 = token_accounting_from_usage(obs2.usage)
    assert acc1 is not None and acc1.total_tokens == 110
    assert acc2 is not None and acc2.total_tokens == 230


# ============================================================
# Test 10 — T2S semantic retry：usage 经 lifecycle 可见（无需改 T2S）
# ============================================================

async def test_t2s_retry_usage_visible() -> None:
    """Test 10：T2S semantic retry——2 requests → 2 observations →
    2 个可见 usage（业务代码仍拿 str，T2S 零修改）。"""
    sink = CollectingObservationSink()
    handler = ScriptedHandler([
        _content_body("SELECT 1", request_id="chatcmpl-t2s-1"),  # 缺 LIMIT
        _content_body("```sql\nSELECT 1 LIMIT 1\n```",
                      request_id="chatcmpl-t2s-2",
                      usage=_FULL_USAGE),
    ])
    provider = _obs_provider(handler, sink)
    from backend.app.services.text_to_sql_service import TextToSQLService

    result = await TextToSQLService(llm_client=provider, max_attempts=3).generate(
        "知识文档有哪些？", database_context="ctx",
    )

    assert result.validated is True
    assert result.attempts == 2                       # 业务语义不变
    assert len(sink.observations) == 2
    obs1, obs2 = sink.observations
    # usage 可见性：resp #1 无 usage → None；resp #2 有 → 可见
    assert obs1.usage is None
    assert obs2.usage == LLMUsage(100, 20, 120)
    assert token_accounting_from_usage(obs2.usage) == LLMUsage(100, 20, 120)


# ============================================================
# Test 11 — Refusal：usage 不因 refusal 丢失
# ============================================================

async def test_refusal_usage_visible() -> None:
    """Test 11：refusal 的 usage 原样可见（不因 refusal 丢弃），
    success=True 保持。"""
    from backend.app.services.text_to_sql_service import REFUSAL_MARKER

    sink = CollectingObservationSink()
    handler = ScriptedHandler([
        _content_body(REFUSAL_MARKER, request_id="chatcmpl-vis-refusal",
                      usage=_FULL_USAGE),
    ])
    provider = _obs_provider(handler, sink)
    from backend.app.services.text_to_sql_service import TextToSQLService

    result = await TextToSQLService(llm_client=provider, max_attempts=3).generate(
        "删除所有库存", database_context="ctx",
    )

    assert result.status == "refusal"
    observation = sink.observations[0]
    assert observation.success is True
    assert observation.usage == LLMUsage(100, 20, 120)  # 不丢失
    assert token_accounting_from_usage(observation.usage) == LLMUsage(100, 20, 120)


# ============================================================
# Test 12 — Concurrency：5 并发无串线
# ============================================================

async def test_concurrent_visibility_no_cross_talk() -> None:
    """Test 12：5 并发 no-tools 请求（usage 各异）→ 每个请求的
    observation 携带各自 usage，无串线。"""
    sink = CollectingObservationSink()
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        n = counter["n"]
        return httpx.Response(
            200,
            json=_content_body(
                f"answer-{n}",
                request_id=f"chatcmpl-vis-conc-{n}",
                usage={"prompt_tokens": 100 * n, "completion_tokens": 10 * n,
                       "total_tokens": 110 * n},
            ),
        )

    provider = _obs_provider(handler, sink)
    await asyncio.gather(
        *[
            provider.chat([{"role": "user", "content": f"q-{i}"}])
            for i in range(5)
        ]
    )

    assert len(sink.observations) == 5
    totals = {obs.usage.total_tokens for obs in sink.observations}
    assert totals == {110 * n for n in range(1, 6)}     # 各自 usage，无串线
    request_ids = {obs.request_id for obs in sink.observations}
    assert request_ids == {f"chatcmpl-vis-conc-{n}" for n in range(1, 6)}


# ============================================================
# Test 13 — Existing caller compatibility
# ============================================================

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


async def test_existing_caller_compatibility() -> None:
    """Test 13：旧业务调用方（RAG / 直接 chat+generate）
    仍然拿到 str / LLMResponse——返回值类型与值完全不变。"""
    sink = CollectingObservationSink()
    handler = ScriptedHandler([
        _content_body("plain answer", request_id="chatcmpl-vis-plain"),
        _content_body("gen answer", request_id="chatcmpl-vis-gen2"),
        _tool_call_body(request_id="chatcmpl-vis-tc", usage=_FULL_USAGE),
    ])
    provider = _obs_provider(handler, sink)

    plain = await provider.chat([{"role": "user", "content": "q"}])
    assert plain == "plain answer" and isinstance(plain, str)

    gen_like = await provider.generate("q")
    assert gen_like == "gen answer" and isinstance(gen_like, str)

    with_tools = await provider.chat(
        [{"role": "user", "content": "q"}], tools=_TOOLS,
    )
    assert isinstance(with_tools, LLMResponse)
    assert with_tools.content is None                  # tool calling 契约不变
    assert with_tools.tool_calls[0].name == "get_inventory"

    # RAG Service 端到端：业务代码继续拿 str（usage 经 lifecycle 可见）
    from backend.app.services.rag_service import RagService
    from backend.app.services.vector_search_service import VectorSearchResult

    class FakeVectorSearch:
        async def search(self, query: str, *, top_k: int):
            return [VectorSearchResult(
                chunk_id=1, document_id=1, chunk_index=0, content="kb",
                distance=0.1, similarity=0.9, metadata={},
            )]

    rag = RagService(
        vector_search_service=FakeVectorSearch(),
        llm_client=_obs_provider(
            ScriptedHandler([
                _content_body("rag ok", usage=_FULL_USAGE),
            ]),
            sink,
        ),
    )
    response = await rag.answer("采购入库的流程是什么？")
    assert isinstance(response.answer, str) and response.answer == "rag ok"
    # RAG 链路的 usage 经 lifecycle 可见（RAG 业务代码零修改）
    assert sink.observations[-1].usage is not None
    assert sink.observations[-1].usage.total_tokens == 120


# ============================================================
# Test 14 — No global state（AST）
# ============================================================

def test_no_global_last_usage_or_response_state() -> None:
    """Test 14：client.py 无 last_usage / last_response / last_observation
    等共享可变状态赋值（AST 锁定——usage 经 request-scoped 参数传递）。"""
    import backend.app.llm.client as client_mod

    tree = ast.parse(inspect.getsource(client_mod))
    forbidden = {
        "last_usage", "last_response", "last_observation", "global_usage",
        "global_observation", "_last_usage", "_last_response",
    }
    assigned_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned_names.add(target.id)
                elif isinstance(target, ast.Attribute):
                    assigned_names.add(target.attr)
    assert not (assigned_names & forbidden)


# ============================================================
# Test 15 — No DB / network（visibility 不产生额外调用）
# ============================================================

async def test_visibility_adds_no_extra_calls() -> None:
    """Test 15：visibility 本身零额外网络/DB——1 次公共调用恰好
    1 次底层 HTTP 请求（无重复解析 / 二次请求）。"""
    http_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        http_calls["n"] += 1
        return httpx.Response(200, json=_content_body(
            "hello", request_id="chatcmpl-vis-noextra", usage=_FULL_USAGE,
        ))

    sink = CollectingObservationSink()
    provider = _obs_provider(handler, sink)

    result = await provider.chat([{"role": "user", "content": "hi"}])
    assert result == "hello"
    # generate 内部复用 chat：同样只 1 次
    await provider.generate("ping")

    assert http_calls["n"] == 2                       # 2 次调用 = 2 次请求
    assert len(sink.observations) == 2                # 无重复 observation
