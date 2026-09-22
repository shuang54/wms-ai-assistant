"""Chat 业务逻辑（Phase 3.5.6：接入 RAG）。

调用边界：

    Chat API
        ↓
    ChatService
        ↓
    RagService（Phase 3.5.4）
        ↓
    VectorSearchService → ContextBuilder → LLMClient

职责：
    - 校验入参（空 / 纯空白 → ValueError，由 API 层映射 400）
    - 委托 RagService.answer（不在本层重复实现 RAG）
    - 透传 RagService 抛出的异常（由 API 层映射为 HTTP 状态码）

不在本层做：
    - Prompt 拼接（由 ContextBuilder 负责）
    - Embedding / pgvector / LLM HTTP 细节
    - 直接调用 LLMClient（避免 RAG 之外的第二条 LLM 调用路径）
    - HTTP 请求细节（由 API 层负责）

Phase 2 的"构造 messages + 直调 LLM"路径已被 RAG 链路取代；
Prompt 组装职责移交给 ContextBuilder（见 rag_service.py）。
"""
from __future__ import annotations

import logging

from backend.app.services.rag_service import RagResponse, RagService

logger = logging.getLogger(__name__)


class ChatService:
    """协调用户问题与 RAG / LLM 之间的业务编排（Phase 3.5.6）。"""

    def __init__(self, rag_service: RagService | None = None) -> None:
        """构造 ChatService。

        Args:
            rag_service: RAG 服务；为 None 时使用模块默认实例。
                主要用于测试时注入 Fake RagService。
        """
        self._rag_service = rag_service or RagService()

    # --------------------------------------------------------
    # 公共接口
    # --------------------------------------------------------

    async def chat(self, message: str) -> RagResponse:
        """处理单轮用户消息，经 RAG 知识库链路返回回答。

        不传 top_k：由 RagService 使用 settings.rag.default_top_k，
        本层不复制默认配置逻辑。

        Returns:
            RagResponse: RAG 回答（answer / sources / used_chunks_count），
            由 API 层转换为 ChatResponse DTO。

        Raises:
            ValueError: message 为空或纯空白。
            RagError / VectorSearchError 家族 / EmbeddingError 家族 / LLMError 家族:
                由 RagService 原样透传（由 API 层映射为 HTTP 状态码）。
        """
        if not message or not message.strip():
            raise ValueError("message 不能为空")

        return await self._rag_service.answer(message)


__all__ = ["ChatService"]
