"""ChatService 单元测试。

使用 FakeLLMClient 注入，不依赖真实 LLM。
"""
from __future__ import annotations

from backend.app.llm.client import (
    LLMConfigError,
    LLMError,
    LLMRequestError,
)
from backend.app.services.chat_service import ChatService


class FakeLLMClient:
    """用于单元测试的假 LLM Client。

    支持：
        - 配置固定响应内容
        - 配置抛出异常
        - 记录被调用的 messages（用于断言）
    """

    def __init__(self, response: str = "fake answer", error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[list[dict[str, str]]] = []

    async def generate(self, prompt: str) -> str:
        return self._response

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(list(messages))
        if self._error is not None:
            raise self._error
        return self._response


# ============================================================
# 构造 messages
# ============================================================

async def test_chat_builds_messages_with_system_and_user() -> None:
    """传入 system_prompt 时 messages 必须包含 system + user。"""
    fake = FakeLLMClient(response="ok")
    service = ChatService(llm_client=fake, system_prompt="you are helpful")
    result = await service.chat("hello")

    assert result == "ok"
    assert len(fake.calls) == 1
    msgs = fake.calls[0]
    assert len(msgs) == 2
    assert msgs[0] == {"role": "system", "content": "you are helpful"}
    assert msgs[1] == {"role": "user", "content": "hello"}


async def test_chat_omits_system_when_prompt_empty() -> None:
    """system_prompt 为空时 messages 只包含 user。"""
    fake = FakeLLMClient(response="ok")
    service = ChatService(llm_client=fake, system_prompt="")
    await service.chat("hi")

    msgs = fake.calls[0]
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "hi"


# ============================================================
# 输入校验
# ============================================================

async def test_chat_rejects_empty_message() -> None:
    fake = FakeLLMClient()
    service = ChatService(llm_client=fake)
    try:
        await service.chat("")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty message")
    assert fake.calls == []  # LLM 不应被调用


async def test_chat_rejects_whitespace_only_message() -> None:
    fake = FakeLLMClient()
    service = ChatService(llm_client=fake)
    try:
        await service.chat("   \t\n  ")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for whitespace message")
    assert fake.calls == []


# ============================================================
# LLM 异常透传
# ============================================================

async def test_chat_propagates_llm_config_error() -> None:
    fake = FakeLLMClient(error=LLMConfigError("api key missing"))
    service = ChatService(llm_client=fake)
    try:
        await service.chat("hi")
    except LLMConfigError as exc:
        assert "api key missing" in str(exc)
    else:
        raise AssertionError("expected LLMConfigError")


async def test_chat_propagates_llm_request_error() -> None:
    fake = FakeLLMClient(error=LLMRequestError("connection refused"))
    service = ChatService(llm_client=fake)
    try:
        await service.chat("hi")
    except LLMRequestError as exc:
        assert "connection refused" in str(exc)
    else:
        raise AssertionError("expected LLMRequestError")


async def test_chat_wraps_unexpected_exception_into_llm_error() -> None:
    """非 LLMError 的异常必须被包装为 LLMError，避免泄露内部细节。"""
    fake = FakeLLMClient(error=RuntimeError("boom"))
    service = ChatService(llm_client=fake)
    try:
        await service.chat("hi")
    except LLMError as exc:
        assert "boom" in str(exc)
        assert isinstance(exc.__cause__, RuntimeError)
    else:
        raise AssertionError("expected LLMError")


# ============================================================
# 加载默认 system_prompt
# ============================================================

async def test_chat_loads_default_system_prompt_from_file(monkeypatch) -> None:
    """未显式传 system_prompt 时应从 prompts/system.txt 加载。"""
    fake = FakeLLMClient()
    service = ChatService(llm_client=fake)  # system_prompt=None → 加载文件
    await service.chat("hi")
    msgs = fake.calls[0]
    # 默认 system.txt 非空，应该至少有一条 system
    assert msgs[0]["role"] == "system"
    assert len(msgs[0]["content"]) > 0