"""Chat 业务逻辑（Phase 2：接入真实 LLM）。

调用边界：

    Chat API
        ↓
    ChatService
        ↓
    LLMClient  ← MockLLMClient / OpenAICompatibleClient
        ↓
    （Phase 3+）RAG Context
        ↓
    （Phase 4+）Tool Result
        ↓
    （Phase 6+）Conversation History

职责：
    - 校验入参
    - 加载 System Prompt
    - 构造 messages（system + user）
    - 调用 LLMClient.chat
    - 捕获并透传 LLM 异常（由 API 层映射为 HTTP 状态码）

不在本层做：
    - HTTP 请求细节（由 LLMClient 负责）
    - 数据库 / WMS 调用（由后续 Tool 负责）
    - RAG / Agent / 多轮上下文（后续阶段）
"""
from __future__ import annotations

import logging

from backend.app.llm.client import (
    LLMClient,
    LLMError,
    get_default_llm_client,
    load_system_prompt,
)

logger = logging.getLogger(__name__)


class ChatService:
    """协调用户问题与 LLM 之间的业务编排。"""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        system_prompt: str | None = None,
    ) -> None:
        """构造 ChatService。

        Args:
            llm_client: LLM 客户端；为 None 时使用模块默认。
                主要用于测试时注入 Fake LLM Client。
            system_prompt: 系统 Prompt；为 None 时从 prompts/system.txt 加载。
                主要用于测试时注入。
        """
        self._llm_client = llm_client or get_default_llm_client()
        self._system_prompt = (
            system_prompt if system_prompt is not None else load_system_prompt()
        )

    # --------------------------------------------------------
    # 内部方法
    # --------------------------------------------------------

    def _build_messages(self, message: str) -> list[dict[str, str]]:
        """构造发送给 LLM 的 messages。

        当前 Phase 2 仅包含 system + user；
        Phase 3+ 会在 system 与 user 之间注入 RAG context。
        """
        messages: list[dict[str, str]] = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": message})
        return messages

    # --------------------------------------------------------
    # 公共接口
    # --------------------------------------------------------

    async def chat(self, message: str) -> str:
        """处理单轮用户消息，返回 AI 回答。

        Raises:
            ValueError: message 为空或纯空白。
            LLMError: LLM 客户端抛出的任何错误（由调用方决定如何映射）。
        """
        if not message or not message.strip():
            raise ValueError("message 不能为空")

        messages = self._build_messages(message)

        try:
            answer = await self._llm_client.chat(messages)
        except LLMError:
            # 已知 LLM 异常，原样抛出（API 层映射为 HTTP 状态码）
            raise
        except Exception as exc:
            # 兜底：将未知异常包装为 LLMError，避免泄露内部细节
            logger.exception("Unexpected error from LLM client")
            raise LLMError(f"LLM 调用发生未预期错误: {exc}") from exc

        return answer


__all__ = ["ChatService"]