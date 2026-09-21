"""Chat 业务逻辑（Phase 1）。

调用边界（保留给 Phase 2+ 扩展）：

    Chat API
        ↓
    ChatService
        ↓
    LLMClient  ← Phase 1 = MockLLMClient
        ↓
    （Phase 4+）RAG / Tool
"""
from __future__ import annotations

from backend.app.llm.client import LLMClient, get_default_llm_client


class ChatService:
    """协调用户问题与 LLM 之间的业务编排。"""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client or get_default_llm_client()

    async def chat(self, message: str) -> str:
        """处理单轮用户消息，返回 AI 回答。

        Phase 1：直接由 LLMClient.generate 返回 Mock。
        Phase 2：可在此处注入会话历史 / 系统 Prompt。
        """
        if not message or not message.strip():
            raise ValueError("message 不能为空")
        return await self._llm_client.generate(message)


__all__ = ["ChatService"]
