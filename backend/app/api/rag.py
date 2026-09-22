"""RAG 接口（Phase 3.5.5：暴露 RagService）。

边界：

    HTTP Request
        ↓
    /api/rag/answer
        ↓
    RagService（Phase 3.5.4）
        ↓
    VectorSearchService → ContextBuilder → LLMClient
        ↓
    HTTP Response（纯 DTO，不暴露 ORM Model）

本 Router 只负责 HTTP / Schema / 异常映射：

    - 入参校验（Pydantic：query 非空、top_k ∈ [1, 50]）
    - RagResponse → RagAnswerResponse 字段映射
    - 已知异常 → HTTP 状态码（映射规则与 /api/chat 共享，
      见 _rag_error_mapping.rag_pipeline_error_to_http）

不做：Embedding / pgvector / Prompt 拼接 / LLM 调用 / 数据库查询。

与 /api/chat 的关系：Phase 3.5.6 起 /api/chat 也经由 RagService；
本接口保留独立的 RAG 问答入口（可显式指定 top_k）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from backend.app.api._rag_error_mapping import rag_pipeline_error_to_http
from backend.app.services.rag_service import RagResponse, RagService
from backend.app.services.vector_search_service import VectorSearchService

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Schemas
# ============================================================

class RagAnswerRequest(BaseModel):
    """RAG 问答请求体。"""

    query: str = Field(
        ...,
        min_length=1,
        description="用户问题，不可为空或纯空白",
    )
    top_k: int | None = Field(
        None,
        ge=VectorSearchService.MIN_TOP_K,
        le=VectorSearchService.MAX_TOP_K,
        description=(
            "召回片段数，范围 [1, 50]；"
            "省略时由 RagService 使用 RAG_TOP_K 默认值"
        ),
    )

    @field_validator("query")
    @classmethod
    def _reject_blank_query(cls, value: str) -> str:
        """空字符串 / 纯空白在 API 层直接拒绝（422）。"""
        if not value.strip():
            raise ValueError("query 不能为空或纯空白")
        return value


class RagSourceResponse(BaseModel):
    """RAG 命中来源（API DTO；对应 RagService.RagSource）。"""

    chunk_id: int = Field(..., description="KnowledgeChunk 主键")
    document_id: int = Field(..., description="所属文档主键")
    chunk_index: int = Field(..., description="Chunk 在原文中的顺序（从 0 开始）")
    content: str = Field(..., description="Chunk 文本内容")
    similarity: float = Field(..., description="cosine 相似度（越大越相关）")
    metadata: dict = Field(..., description="知识片段元数据（如 heading_path）")


class RagAnswerResponse(BaseModel):
    """RAG 问答响应体。"""

    answer: str = Field(..., description="LLM 生成的回答；空检索时为固定提示语")
    sources: list[RagSourceResponse] = Field(
        default_factory=list,
        description="实际纳入回答的知识来源（与 Context 一致）",
    )
    used_chunks_count: int = Field(
        ...,
        ge=0,
        description="实际纳入 Context 的片段数（≤ top_k，可能因长度限制裁剪）",
    )


# ============================================================
# Dependency（与 api/chat.py 同风格：模块级单例，测试用 monkeypatch 替换）
# ============================================================

_rag_service = RagService()


def _to_answer_response(result: RagResponse) -> RagAnswerResponse:
    """RagResponse（Service DTO）→ RagAnswerResponse（API DTO）。"""
    return RagAnswerResponse(
        answer=result.answer,
        sources=[
            RagSourceResponse(
                chunk_id=s.chunk_id,
                document_id=s.document_id,
                chunk_index=s.chunk_index,
                content=s.content,
                similarity=s.similarity,
                metadata=dict(s.metadata) if s.metadata else {},
            )
            for s in result.sources
        ],
        used_chunks_count=result.used_chunks_count,
    )


# ============================================================
# Endpoint
# ============================================================

@router.post("/rag/answer", response_model=RagAnswerResponse)
async def rag_answer(request: RagAnswerRequest) -> RagAnswerResponse:
    """基于知识库的 RAG 问答接口。

    Pipeline（全部由 RagService 承担）：

        Vector Search → Context Builder → LLM → Answer + Sources

    异常映射（与 /api/chat 风格一致：依赖故障不伪装成功）：

        VectorSearchInputError       → 400  Bad Request        （query 非法，防御性兜底）
        VectorSearchParameterError   → 422  Unprocessable       （top_k 越界，防御性兜底）
        EmbeddingConfigurationError  → 503  Service Unavailable （Embedding 配置缺失）
        EmbeddingAPIError            → 502  Bad Gateway        （Embedding 上游失败）
        EmbeddingResponseError       → 502  Bad Gateway        （Embedding 响应异常）
        EmbeddingError               → 500  Internal Error      （其余 Embedding 错误）
        VectorSearchError            → 502  Bad Gateway        （pgvector / DB 检索失败）
        LLMConfigError               → 503  Service Unavailable
        LLMRequestError              → 502  Bad Gateway
        LLMResponseError             → 502  Bad Gateway
        RagError / LLMError          → 500  Internal Error

    空知识库（无检索命中）不是错误：返回 200 + 固定提示语 + sources=[]。
    """
    try:
        result = await _rag_service.answer(request.query, top_k=request.top_k)
    except Exception as exc:
        # 已知异常 → 统一映射（与 /api/chat 共享）；未知异常原样抛出
        raise rag_pipeline_error_to_http(exc)

    return _to_answer_response(result)


__all__ = [
    "router",
    "RagAnswerRequest",
    "RagAnswerResponse",
    "RagSourceResponse",
]
