"""DeepSeekProvider — DeepSeek 的 LLMProvider 具体实现（Phase 3.10.1）。

结构（复用现有 Client，不重新实现 HTTP）：

    DeepSeekProvider
        ↓ 纯 delegation
    OpenAICompatibleClient（backend/app/llm/client.py，
        现有 DeepSeek OpenAI-compatible Client —— DeepSeek 通过
        LLM_BASE_URL=https://api.deepseek.com/v1 配置，无专属 SDK）

职责边界（Phase 3.10.1 §五）：

    * 纯 delegation：参数原样透传、返回结果原样转发，
      不实现任何新 HTTP / 重试 / fallback 逻辑；
    * 不读取 / 不输出 API Key（Key 仍由 Client 从 settings 持有）；
    * 不改变 DeepSeek endpoint / 默认 model / 生产行为；
    * 实例由 application composition/root 层构造（当前为
      ``client.create_llm_client()``）；本阶段不引入 Provider Factory。

本模块运行时仅依赖 ``provider.LLMProvider``（文档性 duck-typing 注解）；
对 ``client.OpenAICompatibleClient`` 的引用仅在类型检查期生效，
避免 client.py（工厂）→ 本模块 的模块级循环 import。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from backend.app.llm.client import LLMResponse, OpenAICompatibleClient

__all__ = [
    "DeepSeekProvider",
]


class DeepSeekProvider:
    """DeepSeek LLM Provider（包装现有 ``OpenAICompatibleClient``）。

    满足 ``LLMProvider`` Protocol（structural typing，与既有
    MockLLMClient / OpenAICompatibleClient 实现风格一致，不显式继承）。
    """

    def __init__(self, client: "OpenAICompatibleClient") -> None:
        """构造 Provider。

        Args:
            client: 现有 DeepSeek OpenAI-compatible Client
                    （由 composition/root 层按 settings 构造）。
        """
        self._client = client

    async def generate(self, prompt: str) -> str:
        """单轮文本生成（透传现有 Client 行为）。"""
        return await self._client.generate(prompt)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> "str | LLMResponse":
        """多轮对话生成（透传现有 Client 行为，含 Tool Calling）。"""
        return await self._client.chat(messages, tools=tools)
