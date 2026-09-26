"""LLMProvider — AI Core 面向的 LLM 抽象（Phase 3.10.1）。

抽象边界：

    AI Core
    （RagService / TextToSQLService / ToolChatService / AIRouterService）
        ↓  依赖抽象（依赖注入；不 import 具体实现）
    LLMProvider（本模块 Protocol）
        ↓
    DeepSeekProvider（deepseek_provider.py，纯 delegation）
        ↓
    OpenAICompatibleClient（client.py，现有 DeepSeek
                            OpenAI-compatible Client）

设计约束（Phase 3.10.1 §四）：

    * Provider 是 AI Core 面向的抽象，不暴露 DeepSeek 专属概念；
    * 返回稳定、简单的结果：``str`` / ``LLMResponse``
      （纯 Python DTO，无 HTTP / OpenAI SDK 类型泄漏到 AI Core）；
    * 不把 API Key 放进 Provider 接口（Key 由具体 Client 从 settings 持有）；
    * 不把 Project Context 放进 LLM Provider；
    * 不把 RAG / Tool / Text-to-SQL 逻辑放进 Provider。

历史说明：

    Phase 2 ~ Phase 3.9，各核心服务通过 ``client.LLMClient`` Protocol
    依赖 LLM 抽象。Phase 3.10.1 起该抽象正式定义于本模块并命名为
    ``LLMProvider``；``client.LLMClient`` 保留为同一对象的向后兼容别名
    （``LLMClient is LLMProvider``），旧 import 路径继续有效。

本阶段只定义抽象，不增加 Provider Factory / Registry（未来需求）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover
    # 仅类型检查期引用 DTO；运行时本模块零依赖（避免与 client.py 循环 import）。
    from backend.app.llm.client import LLMResponse

__all__ = [
    "LLMProvider",
]


class LLMProvider(Protocol):
    """LLM Provider 抽象接口（AI Core 仅依赖此协议）。

    与 Phase 3.6.2 起 ``client.LLMClient`` 的语义完全一致：

        generate(prompt)                 → str           （单轮文本生成）
        chat(messages)                   → str           （无 Tool，向后兼容）
        chat(messages, tools=[...])      → LLMResponse   （content + tool_calls）
    """

    async def generate(self, prompt: str) -> str:
        """单轮文本生成（无 system prompt）。"""
        ...

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> "str | LLMResponse":
        """多轮对话生成。

        messages 每项形如 {"role": "system|user|assistant|tool", ...}；
        Tool Calling 链路中 content 可能为 None、arguments 为 JSON 字符串。

        Args:
            messages: 对话消息列表。
            tools:    OpenAI-compatible tool schema 列表
                      （由 ``llm.tool_schema.definitions_to_openai_tools``
                      转换；本抽象不感知具体 Tool 业务）。

        Returns:
            tools 为空 / None 时：assistant 的 content（``str``）。
            tools 非空时：``LLMResponse``（content + tool_calls）。
        """
        ...
