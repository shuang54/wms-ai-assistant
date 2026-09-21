"""LLM Client 抽象层（Phase 1：仅 Mock）。

抽象边界：

    ChatService
        ↓
    LLMClient（Protocol）
        ↓
    MockLLMClient  ← 当前默认
        ↓
    （Phase 2）OpenAICompatibleLLMClient / QwenLLMClient / DeepSeekLLMClient / OllamaLLMClient

详见 docs/architecture.md §8、AGENTS.md §9。
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

_PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "system.txt"


def _load_system_prompt() -> str:
    """读取系统 Prompt；缺失返回空串。"""
    try:
        return _PROMPT_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


class LLMClient(Protocol):
    """LLM Client 抽象接口。

    任何 Provider 实现都必须提供异步的 generate / chat 方法。
    """

    async def generate(self, prompt: str) -> str:
        """单轮文本生成。"""
        ...

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """多轮对话生成。

        messages 每项形如 {"role": "system|user|assistant", "content": "..."}。
        """
        ...


class MockLLMClient:
    """Phase 1 默认实现：返回 Mock 响应，不发起任何网络请求。

    仅用于打通工程链路与测试。
    """

    def __init__(self, system_prompt: str | None = None) -> None:
        self._system_prompt = (
            system_prompt if system_prompt is not None else _load_system_prompt()
        )

    async def generate(self, prompt: str) -> str:
        return f"[mock] 已收到消息：{prompt}"

    async def chat(self, messages: list[dict[str, str]]) -> str:
        last_user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_message = msg.get("content", "")
                break
        return f"[mock] 已收到消息：{last_user_message}"


_default_client: LLMClient | None = None


def get_default_llm_client() -> LLMClient:
    """获取默认 LLM Client。

    Phase 1 固定返回 MockLLMClient。
    Phase 2 将根据 settings.llm.provider 切换真实 Provider。
    """
    global _default_client
    if _default_client is None:
        _default_client = MockLLMClient()
    return _default_client


def reset_default_llm_client() -> None:
    """测试辅助：重置默认客户端缓存，便于注入 mock。"""
    global _default_client
    _default_client = None


__all__ = ["LLMClient", "MockLLMClient", "get_default_llm_client", "reset_default_llm_client"]
