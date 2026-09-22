"""RAG Pipeline 异常 → HTTP 映射（Phase 3.5.6：chat / rag Router 共用）。

RagService.answer() 链路上的已知异常（Vector Search / Embedding / LLM）
统一映射为 HTTPException；未知异常原样抛出，交由 Starlette
ServerErrorMiddleware 兜底为 500。

设计约束：

- 状态码语义与既有 /api/chat、/api/rag/answer 保持一致：
        * 调用方输入错误（query / 空白）     → 400 / 422
        * 依赖配置缺失（Embedding / LLM Key）  → 503
        * 上游故障（网络 / HTTP 非 2xx / DB）  → 502
        * 未知 / 内部错误                      → 500
- 响应 detail **不得**包含 API Key / Authorization / SQL /
  embedding vector / 数据库连接串 / traceback。
- 子类必须在基类之前判断（except 顺序敏感）。
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, status

from backend.app.embedding.exceptions import (
    EmbeddingAPIError,
    EmbeddingConfigurationError,
    EmbeddingError,
    EmbeddingResponseError,
)
from backend.app.llm.client import (
    LLMConfigError,
    LLMError,
    LLMRequestError,
    LLMResponseError,
)
from backend.app.services.rag_service import RagError
from backend.app.services.vector_search_service import (
    VectorSearchError,
    VectorSearchInputError,
    VectorSearchParameterError,
)

logger = logging.getLogger(__name__)


def rag_pipeline_error_to_http(exc: Exception) -> HTTPException:
    """将 RAG 链路已知异常映射为 HTTPException；未知异常原样 re-raise。

    Args:
        exc: RagService.answer() 抛出的异常。

    Returns:
        对应的 HTTPException（已知异常）。

    Raises:
        Exception: 未匹配的异常原样抛出（由全局错误中间件兜底为 500）。
    """
    if isinstance(exc, VectorSearchInputError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    if isinstance(exc, VectorSearchParameterError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    if isinstance(exc, EmbeddingConfigurationError):
        logger.warning(
            "Embedding config error: %s",
            exc,
            extra={"error_type": "EmbeddingConfigurationError"},
        )
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Embedding 配置错误: {exc}",
        )
    if isinstance(exc, EmbeddingAPIError):
        logger.warning(
            "Embedding API error: %s",
            exc,
            extra={"error_type": "EmbeddingAPIError"},
        )
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Embedding 请求失败: {exc}",
        )
    if isinstance(exc, EmbeddingResponseError):
        logger.warning(
            "Embedding response error: %s",
            exc,
            extra={"error_type": "EmbeddingResponseError"},
        )
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Embedding 响应解析失败: {exc}",
        )
    if isinstance(exc, EmbeddingError):
        logger.exception("Embedding unknown error")
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Embedding 调用异常: {exc}",
        )
    if isinstance(exc, VectorSearchError):
        logger.warning(
            "Vector search error: %s",
            exc,
            extra={"error_type": "VectorSearchError"},
        )
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"向量检索失败: {exc}",
        )
    if isinstance(exc, LLMConfigError):
        logger.warning(
            "LLM config error: %s",
            exc,
            extra={"error_type": "LLMConfigError"},
        )
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM 配置错误: {exc}",
        )
    if isinstance(exc, LLMRequestError):
        logger.warning(
            "LLM request error: %s",
            exc,
            extra={"error_type": "LLMRequestError"},
        )
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM 请求失败: {exc}",
        )
    if isinstance(exc, LLMResponseError):
        logger.warning(
            "LLM response error: %s",
            exc,
            extra={"error_type": "LLMResponseError"},
        )
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM 响应解析失败: {exc}",
        )
    if isinstance(exc, RagError):
        logger.exception("RAG service error")
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"RAG 服务内部错误: {exc}",
        )
    if isinstance(exc, LLMError):
        logger.exception("LLM unknown error")
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LLM 调用异常: {exc}",
        )

    # 未知异常：不吞掉、不包装，原样抛出（生产环境由
    # ServerErrorMiddleware 返回 500；测试可用
    # TestClient(raise_server_exceptions=False) 验证不泄露 traceback）。
    raise exc


__all__ = ["rag_pipeline_error_to_http"]
