"""Chat 接口（Phase 3.5.6：Chat + RAG）。

边界：

    HTTP Request
        ↓
    /api/chat
        ↓
    ChatService
        ↓
    RagService
        ↓
    Vector Search → Context Builder → LLMClient
        ↓
    Chat Response（纯 DTO，不暴露 ORM Model / 内部字段）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.api._rag_error_mapping import rag_pipeline_error_to_http
from backend.app.services.chat_service import ChatService
from backend.app.services.rag_service import RagResponse


logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    """Chat 请求体（协议保持 Phase 2：message 单字段）。"""

    message: str = Field(..., min_length=1, description="用户消息，不可为空")


class ChatSourceResponse(BaseModel):
    """Chat 命中来源（仅元数据；不返回 chunk content，避免泄露大段知识库原文）。"""

    chunk_id: int = Field(..., description="KnowledgeChunk 主键")
    document_id: int = Field(..., description="所属文档主键")
    chunk_index: int = Field(..., description="Chunk 在原文中的顺序（从 0 开始）")
    similarity: float = Field(..., description="cosine 相似度（越大越相关）")
    metadata: dict = Field(..., description="知识片段元数据（如 heading_path）")


class ChatResponse(BaseModel):
    """Chat 响应体。

    Phase 3.5.6：向后兼容地新增 sources / used_chunks_count；
    answer 字段语义不变（来自 RAG 链路的 LLM 回答）。
    """

    answer: str = Field(..., description="AI 回答（经 RAG 知识库链路生成）")
    sources: list[ChatSourceResponse] = Field(
        default_factory=list,
        description="RAG 命中来源（仅元数据，不含 chunk content）",
    )
    used_chunks_count: int = Field(
        0,
        ge=0,
        description="实际纳入 Context 的片段数",
    )


# ChatService 无状态单例；保留模块级构造以保持既有风格。
# 测试中可通过 monkeypatch 直接替换本变量。
_chat_service = ChatService()


def _to_chat_response(result: RagResponse) -> ChatResponse:
    """RagResponse（Service DTO）→ ChatResponse（API DTO）。

    刻意不映射 source.content（前端仅需来源元数据）。
    """
    return ChatResponse(
        answer=result.answer,
        sources=[
            ChatSourceResponse(
                chunk_id=s.chunk_id,
                document_id=s.document_id,
                chunk_index=s.chunk_index,
                similarity=s.similarity,
                metadata=dict(s.metadata) if s.metadata else {},
            )
            for s in result.sources
        ],
        used_chunks_count=result.used_chunks_count,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """对话接口（Phase 3.5.6：基于 WMS 知识库的 RAG 问答）。

    Pipeline（全部委托 RagService）：

        ChatService → Vector Search → Context Builder → LLM

    异常映射（与 /api/rag/answer 共享 rag_pipeline_error_to_http）：

        ValueError        → 400  Bad Request        （空消息，防御性兜底）
        VectorSearchInputError       → 400
        VectorSearchParameterError   → 422
        EmbeddingConfigurationError   → 503
        EmbeddingAPIError / EmbeddingResponseError / VectorSearchError → 502
        EmbeddingError / RagError / LLMError → 500
        LLMConfigError               → 503
        LLMRequestError / LLMResponseError → 502

    空知识库（无检索命中）不是错误：200 + 固定提示语 + sources=[]。
    """
    try:
        result = await _chat_service.chat(request.message)
    except ValueError as exc:
        # 空消息：保持 Phase 2 的 400 语义（Pydantic 只拦截 ""，
        # 纯空白由本服务层校验拒绝）
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        raise rag_pipeline_error_to_http(exc)

    return _to_chat_response(result)


__all__ = ["router", "ChatRequest", "ChatResponse", "ChatSourceResponse"]
