"""Chat 接口（Phase 3.5.6：Chat + RAG）。

边界：

    HTTP Request
        ↓
    M1 用户会话认证（Phase 7 Recovery M2.3 A1：**不再允许匿名执行**）
        ↓
    CSRF 校验（Cookie 会话的状态变更请求）
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

认证（Phase 7 Recovery M2.3 A1；与 M1/M2.2 同机制，**不**新增身份系统）：

    * 身份只来自服务端会话（``wms_ai_session`` HttpOnly Cookie →
      ``ai_ops.user_session``）；本模块**不**接受 API Key / Query / Body 身份；
    * 认证先于任何 AI / RAG / LLM 执行：未认证 / 无效 / 过期 / 已撤销会话
      ⇒ **401**（固定文案），LLM 调用 = 0、RAG 调用 = 0；
    * 缺 / 不匹配 CSRF ⇒ **403**（固定文案），同样零业务执行；
    * 会话服务不可用 ⇒ **503**（fail-closed；**绝不**降级为匿名放行）。
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.api._rag_error_mapping import rag_pipeline_error_to_http
from backend.app.api.auth_dependency import CurrentPrincipal, require_csrf
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


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        401: {"description": "未认证 / 会话无效（固定文案；零业务执行）"},
        403: {"description": "CSRF 校验失败（固定文案；零业务执行）"},
        503: {"description": "认证服务不可用（fail-closed）"},
    },
)
async def chat(
    request: ChatRequest,
    principal: CurrentPrincipal,
    _csrf: Annotated[None, Depends(require_csrf)],
) -> ChatResponse:
    """对话接口（Phase 3.5.6：基于 WMS 知识库的 RAG 问答）。

    认证（Phase 7 Recovery M2.3 A1）：要求 M1 用户会话 + CSRF；
    **Breaking change：本端点不再允许匿名执行**。

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
