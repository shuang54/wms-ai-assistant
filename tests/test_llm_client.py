"""OpenAICompatibleClient 单元测试。

通过 httpx.MockTransport 注入 HTTP 响应，不发起真实网络。
覆盖：成功路径 / 配置校验 / 异常路径。
"""
from __future__ import annotations

import json

import httpx

from backend.app.llm.client import (
    LLMConfigError,
    LLMRequestError,
    LLMResponseError,
    OpenAICompatibleClient,
)


# ============================================================
# Helpers
# ============================================================

def _ok_response(content: str = "hello") -> httpx.Response:
    """构造 OpenAI Chat Completions 兼容的成功响应。"""
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"role": "assistant", "content": content}}
            ]
        },
    )


def _make_client(handler) -> OpenAICompatibleClient:
    """用 httpx.MockTransport 构造 Client，避免真实网络。"""
    transport = httpx.MockTransport(handler)
    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="test-model",
        provider="test",
        transport=transport,
    )


# ============================================================
# 构造校验
# ============================================================

def test_constructor_rejects_missing_api_key() -> None:
    try:
        OpenAICompatibleClient(api_key="", base_url="https://x", model="m")
    except LLMConfigError as exc:
        assert "LLM_API_KEY" in str(exc)
    else:
        raise AssertionError("expected LLMConfigError")


def test_constructor_rejects_missing_base_url() -> None:
    try:
        OpenAICompatibleClient(api_key="k", base_url="", model="m")
    except LLMConfigError as exc:
        assert "LLM_BASE_URL" in str(exc)
    else:
        raise AssertionError("expected LLMConfigError")


def test_constructor_rejects_missing_model() -> None:
    try:
        OpenAICompatibleClient(api_key="k", base_url="https://x", model="")
    except LLMConfigError as exc:
        assert "LLM_MODEL" in str(exc)
    else:
        raise AssertionError("expected LLMConfigError")


# ============================================================
# 成功路径
# ============================================================

async def test_chat_returns_answer_and_sends_correct_request() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return _ok_response("hi")

    client = _make_client(handler)
    answer = await client.chat([{"role": "user", "content": "hello"}])

    assert answer == "hi"
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["authorization"] == "Bearer test-key"
    assert captured["body"]["model"] == "test-model"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hello"}]


async def test_chat_preserves_full_message_history() -> None:
    """system + user 必须按顺序原样发送。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ok_response("answer")

    client = _make_client(handler)
    answer = await client.chat(
        [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert answer == "answer"
    msgs = captured["body"]["messages"]
    assert len(msgs) == 2
    assert msgs[0] == {"role": "system", "content": "be brief"}
    assert msgs[1] == {"role": "user", "content": "hi"}


async def test_generate_wraps_prompt_in_user_message() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ok_response("generated")

    client = _make_client(handler)
    answer = await client.generate("hi prompt")
    assert answer == "generated"
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi prompt"}]


async def test_base_url_trailing_slash_normalized() -> None:
    """base_url 末尾的 / 必须被正确处理，不出现 //chat/completions。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return _ok_response("ok")

    transport = httpx.MockTransport(handler)
    client = OpenAICompatibleClient(
        api_key="k",
        base_url="https://api.example.com/v1/",  # 末尾带 /
        model="m",
        transport=transport,
    )
    await client.chat([{"role": "user", "content": "hi"}])
    assert "https://api.example.com/v1/chat/completions" in captured["url"]
    assert "//chat" not in captured["url"]


# ============================================================
# 异常路径
# ============================================================

async def test_chat_raises_request_error_on_401() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    client = _make_client(handler)
    try:
        await client.chat([{"role": "user", "content": "hi"}])
    except LLMRequestError as exc:
        assert "401" in str(exc)
    else:
        raise AssertionError("expected LLMRequestError")


async def test_chat_raises_request_error_on_429() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    client = _make_client(handler)
    try:
        await client.chat([{"role": "user", "content": "hi"}])
    except LLMRequestError as exc:
        assert "429" in str(exc)
    else:
        raise AssertionError("expected LLMRequestError")


async def test_chat_raises_request_error_on_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    client = _make_client(handler)
    try:
        await client.chat([{"role": "user", "content": "hi"}])
    except LLMRequestError:
        pass
    else:
        raise AssertionError("expected LLMRequestError")


async def test_chat_raises_request_error_on_connect_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _make_client(handler)
    try:
        await client.chat([{"role": "user", "content": "hi"}])
    except LLMRequestError as exc:
        # 必须包含原始异常信息但不暴露完整堆栈
        assert "无法连接" in str(exc) or "connection refused" in str(exc)
    else:
        raise AssertionError("expected LLMRequestError")


async def test_chat_raises_request_error_on_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout")

    client = _make_client(handler)
    try:
        await client.chat([{"role": "user", "content": "hi"}])
    except LLMRequestError as exc:
        assert "超时" in str(exc) or "timeout" in str(exc).lower()
    else:
        raise AssertionError("expected LLMRequestError")


async def test_chat_raises_response_error_on_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    client = _make_client(handler)
    try:
        await client.chat([{"role": "user", "content": "hi"}])
    except LLMResponseError as exc:
        assert "JSON" in str(exc)
    else:
        raise AssertionError("expected LLMResponseError")


async def test_chat_raises_response_error_on_empty_choices() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    client = _make_client(handler)
    try:
        await client.chat([{"role": "user", "content": "hi"}])
    except LLMResponseError as exc:
        assert "choices" in str(exc) or "结构" in str(exc)
    else:
        raise AssertionError("expected LLMResponseError")


async def test_chat_raises_response_error_on_missing_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop"}]})

    client = _make_client(handler)
    try:
        await client.chat([{"role": "user", "content": "hi"}])
    except LLMResponseError:
        pass
    else:
        raise AssertionError("expected LLMResponseError")


async def test_chat_raises_response_error_on_non_string_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": 123}}]})

    client = _make_client(handler)
    try:
        await client.chat([{"role": "user", "content": "hi"}])
    except LLMResponseError:
        pass
    else:
        raise AssertionError("expected LLMResponseError")


# ============================================================
# 敏感信息保护
# ============================================================

async def test_response_body_in_exception_is_truncated() -> None:
    """非 2xx 响应体过长时，异常信息必须截断，避免日志/响应爆炸。"""
    long_body = "x" * 5000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=long_body)

    client = _make_client(handler)
    try:
        await client.chat([{"role": "user", "content": "hi"}])
    except LLMRequestError as exc:
        # 异常字符串应远短于原始 body（截断 ≤ 200）
        assert len(str(exc)) < 500
    else:
        raise AssertionError("expected LLMRequestError")