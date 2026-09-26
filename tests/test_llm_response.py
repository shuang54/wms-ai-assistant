"""LLM Response Metadata / Usage Contract 测试（Phase 3.10.4）。

覆盖（任务书 §十三）：

    1. Normal response（content/model/finish_reason/usage 全映射）
    2. Missing usage → None，调用成功
    3. Partial usage（prompt_tokens=100，其余 None）合法
    4. Missing model → None，调用成功
    5. Missing finish_reason → None，调用成功
    6. Provider response isolation（SDK 字段不泄漏）
    7. No raw response leakage（metadata 不含 API Key / raw response）
    8. Usage validation（负数拒绝；total 一致性；partial 允许）
    9. Fake provider 可构造 LLMResponse（语义不变）
    10. Structured Response regression（content → parse_structured_response）

附加：Mock 契约（缺失字段不虚构）、Tool Calling 行为不变、
generate() 仍返回 str（Phase 2 行为）、tool_calls 提取不变。

全部使用 httpx.MockTransport / 内存对象：0 网络 / 0 LLM / 0 DB。
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from backend.app.llm.client import (
    LLMResponse,
    LLMToolCallFormatError,
    LLMUsage,
    MockLLMClient,
    OpenAICompatibleClient,
    ToolCall,
)
from backend.app.llm.structured import parse_structured_response

#: 最小合法 tool schema（非空即走 LLMResponse 路径）
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
# Helpers
# ============================================================

def _make_client(handler, api_key: str = "test-key") -> OpenAICompatibleClient:
    """用 httpx.MockTransport 构造真实 Client（零网络请求）。"""
    return OpenAICompatibleClient(
        api_key=api_key,
        base_url="https://api.example.com/v1",
        model="configured-model",
        provider="test",
        transport=httpx.MockTransport(handler),
    )


def _response_body(**overrides: Any) -> dict[str, Any]:
    """构造 OpenAI Chat Completions 兼容响应体（可覆盖任意顶层字段）。"""
    body: dict[str, Any] = {
        "id": "chatcmpl-test-123",
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "hello"},
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        },
    }
    body.update(overrides)
    return body


async def _chat(handler, api_key: str = "test-key") -> LLMResponse:
    """执行一次携带 tools 的 chat（走 LLMResponse 路径）。"""
    client = _make_client(handler, api_key=api_key)
    result = await client.chat([{"role": "user", "content": "hi"}], tools=_TOOLS)
    assert isinstance(result, LLMResponse)
    return result


def _json_handler(body: dict[str, Any]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)
    return handler


# ============================================================
# 1. Normal response：全字段映射
# ============================================================

async def test_normal_response_full_mapping() -> None:
    """content / model / finish_reason / usage / metadata 全部正确映射。"""
    response = await _chat(_json_handler(_response_body()))

    assert response.content == "hello"
    assert response.model == "deepseek-chat"      # 实际响应 model，非配置值
    assert response.finish_reason == "stop"
    assert response.usage == LLMUsage(
        prompt_tokens=100, completion_tokens=20, total_tokens=120,
    )
    assert response.metadata == {
        "provider": "test",
        "request_id": "chatcmpl-test-123",
    }


async def test_response_model_prefers_actual_not_configured() -> None:
    """model 取自实际响应（响应 model != 配置 model 时以响应为准）。"""
    response = await _chat(_json_handler(_response_body()))
    assert response.model == "deepseek-chat"


async def test_non_string_request_id_excluded_from_metadata() -> None:
    """id 非 str 时不得进入 metadata（不 coerce、不虚构）。"""
    body = _response_body(id=12345)
    response = await _chat(_json_handler(body))
    assert "request_id" not in response.metadata
    assert response.metadata == {"provider": "test"}


# ============================================================
# 2-5. 缺失字段：不是错误，不虚构
# ============================================================

async def test_missing_usage_ok() -> None:
    """场景 2：provider 未返回 usage → usage=None，调用成功。"""
    body = _response_body()
    del body["usage"]
    response = await _chat(_json_handler(body))
    assert response.usage is None
    assert response.content == "hello"


async def test_partial_usage_ok() -> None:
    """场景 3：partial usage（prompt_tokens=100，其余缺失）合法，不虚构。"""
    body = _response_body(usage={"prompt_tokens": 100})
    response = await _chat(_json_handler(body))
    assert response.usage == LLMUsage(
        prompt_tokens=100, completion_tokens=None, total_tokens=None,
    )


async def test_missing_model_ok() -> None:
    """场景 4：响应无 model → None，调用成功（mock 不强填）。"""
    body = _response_body()
    del body["model"]
    response = await _chat(_json_handler(body))
    assert response.model is None
    assert response.content == "hello"


async def test_missing_finish_reason_ok() -> None:
    """场景 5：响应无 finish_reason → None，调用成功。"""
    body = _response_body()
    del body["choices"][0]["finish_reason"]
    response = await _chat(_json_handler(body))
    assert response.finish_reason is None
    assert response.content == "hello"


async def test_malformed_metadata_fields_degrade_to_none() -> None:
    """metadata 字段类型异常 → 降级为 None（不影响主调用）。"""
    body = _response_body(
        model=42,                      # 非 str
        usage="not-a-dict",            # 非 dict
    )
    body["choices"][0]["finish_reason"] = 7
    response = await _chat(_json_handler(body))
    assert response.model is None
    assert response.finish_reason is None
    assert response.usage is None
    assert response.content == "hello"


# ============================================================
# 6-7. Provider response isolation / no raw leakage
# ============================================================

async def test_provider_response_isolation() -> None:
    """场景 6：SDK 响应字段（choices / secret_field）不得出现在
    LLMResponse 上层可见面——只暴露 contract 字段。"""

    body = _response_body(secret_field="TOP-SECRET-SDK-ONLY")
    response = await _chat(_json_handler(body))

    # 无 SDK-specific 属性
    for sdk_attr in ("choices", "raw", "http_response", "secret_field"):
        assert not hasattr(response, sdk_attr)
    # metadata 不含 SDK 专属字段
    assert "secret_field" not in response.metadata
    assert "TOP-SECRET-SDK-ONLY" not in str(response.metadata)


async def test_metadata_no_credentials_or_raw_response() -> None:
    """场景 7：metadata 不含 API Key / Authorization / 完整 raw response。"""
    body = _response_body(secret_field="LEAK-CANARY")
    response = await _chat(_json_handler(body), api_key="SK-SECRET-API-KEY-XYZ")

    assert "SK-SECRET-API-KEY-XYZ" not in str(response.metadata)
    assert "authorization" not in {k.lower() for k in response.metadata}
    assert "LEAK-CANARY" not in str(response.metadata)
    # metadata 只允许白名单键
    assert set(response.metadata) <= {"provider", "request_id"}
    # 值均为简单标量（不嵌套完整 response / headers）
    assert all(isinstance(v, str) for v in response.metadata.values())


# ============================================================
# 8. Usage validation
# ============================================================

def test_negative_tokens_rejected() -> None:
    """负数 token → reject（LLMUsage 构造层）。"""
    with pytest.raises(ValueError):
        LLMUsage(prompt_tokens=-1)
    with pytest.raises(ValueError):
        LLMUsage(completion_tokens=-5)
    with pytest.raises(ValueError):
        LLMUsage(total_tokens=-10)


def test_usage_total_consistency_validated() -> None:
    """三字段齐备：100 + 20 = 120 → pass；100 + 20 != 130 → reject。"""
    assert LLMUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120) is not None
    with pytest.raises(ValueError):
        LLMUsage(prompt_tokens=100, completion_tokens=20, total_tokens=130)


def test_partial_usage_allowed() -> None:
    """partial usage 合法（缺失字段不虚构、不参与一致性校验）。"""
    usage = LLMUsage(prompt_tokens=100)
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens is None
    assert usage.total_tokens is None


def test_non_int_token_rejected() -> None:
    """token 必须是 int 或 None（bool / str 拒绝，不 coerce）。"""
    with pytest.raises(ValueError):
        LLMUsage(prompt_tokens="100")
    with pytest.raises(ValueError):
        LLMUsage(prompt_tokens=True)


async def test_client_rejects_invalid_usage_without_failing_call() -> None:
    """provider 返回非法 usage（负数 / total 不一致）→ usage=None +
    调用成功（metadata 绝不破坏正常 LLM 调用，§十二）。"""
    body = _response_body(
        usage={"prompt_tokens": -1, "completion_tokens": 20, "total_tokens": 19},
    )
    response = await _chat(_json_handler(body))
    assert response.usage is None          # 非法 usage 被丢弃
    assert response.content == "hello"     # 主调用不受影响

    body2 = _response_body(
        usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 130},
    )
    response2 = await _chat(_json_handler(body2))
    assert response2.usage is None
    assert response2.content == "hello"


# ============================================================
# 9. Fake provider 构造 LLMResponse（语义不变）
# ============================================================

class MetadataFakeProvider:
    """极小 Fake：返回携带 metadata/usage 的 LLMResponse（仅测试）。"""

    def __init__(self, response: LLMResponse) -> None:
        self._response = response
        self.chat_calls = 0

    async def generate(self, prompt: str) -> str:
        return self._response.content or ""

    async def chat(self, messages, *, tools=None) -> LLMResponse:
        self.chat_calls += 1
        return self._response


async def test_fake_provider_constructs_llm_response() -> None:
    """场景 9：Fake provider 可构造完整 LLMResponse，
    上层拿到的仍是纯 DTO（无 SDK 对象）。"""
    fake = MetadataFakeProvider(
        LLMResponse(
            content="answer",
            tool_calls=(),
            model="deepseek-chat",
            finish_reason="stop",
            usage=LLMUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
            metadata={"provider": "fake", "request_id": "req-fake-1"},
        )
    )

    response = await fake.chat([{"role": "user", "content": "hi"}], tools=_TOOLS)

    assert isinstance(response, LLMResponse)
    assert response.content == "answer"
    assert response.metadata == {"provider": "fake", "request_id": "req-fake-1"}
    assert fake.chat_calls == 1


# ============================================================
# 10. Structured Response regression（content → parser）
# ============================================================

class _DemoStructuredAnswer(BaseModel):
    answer: str
    confidence: float


async def test_structured_response_regression_with_content() -> None:
    """场景 10：LLMResponse.content → parse_structured_response() 正常工作
    （Structured Parser 职责未被混合进 Provider Response）。"""
    body = _response_body(
        choices=[
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": '{"answer":"hello","confidence":0.9}',
                },
            }
        ],
    )
    response = await _chat(_json_handler(body))
    assert response.content is not None

    typed = parse_structured_response(response.content, _DemoStructuredAnswer)
    assert typed.answer == "hello"
    assert typed.confidence == 0.9


# ============================================================
# 兼容性：Mock 契约 / Tool Calling / generate() 行为
# ============================================================

async def test_mock_llm_client_response_contract() -> None:
    """Mock：缺失字段保持 None（不虚构 provider 数据），仅诚实标识 provider。"""
    client = MockLLMClient(system_prompt="")
    response = await client.chat(
        [{"role": "user", "content": "你好"}], tools=_TOOLS,
    )

    assert isinstance(response, LLMResponse)
    assert response.content is not None
    assert response.model is None           # mock 无 model → None（不强填）
    assert response.finish_reason is None
    assert response.usage is None
    assert response.metadata == {"provider": "mock"}


async def test_tool_calling_extraction_unchanged() -> None:
    """Tool Calling 行为不变：tool_calls 提取与 3.6.2 语义一致。"""
    body = _response_body(
        choices=[
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "get_inventory",
                                "arguments": '{"material_code": "MAT001"}',
                            },
                        }
                    ],
                },
            }
        ],
    )
    response = await _chat(_json_handler(body))

    assert response.content is None
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls == (
        ToolCall(
            id="call_1", name="get_inventory",
            arguments={"material_code": "MAT001"},
        ),
    )


async def test_tool_call_format_error_still_raised() -> None:
    """tool_calls 结构非法仍抛 LLMToolCallFormatError（错误边界不变）。"""
    body = _response_body(
        choices=[
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "get_inventory",
                                "arguments": "not-json",
                            },
                        }
                    ],
                },
            }
        ],
    )
    with pytest.raises(LLMToolCallFormatError):
        await _chat(_json_handler(body))


async def test_generate_returns_str_unchanged() -> None:
    """generate() / 无 tools chat 仍返回 str（Phase 2 行为不变）。"""
    client = _make_client(_json_handler(_response_body()))

    answer = await client.generate("hi")
    assert isinstance(answer, str)
    assert answer == "hello"

    no_tools = await client.chat([{"role": "user", "content": "hi"}])
    assert isinstance(no_tools, str)
