"""Chat 接口（Phase 2：调用真实 LLM）。

边界：
    HTTP Request
        ↓
    /api/chat
        ↓
    ChatService
        ↓
    LLMClient
        ↓
    OpenAICompatibleClient / MockLLMClient
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.llm.client import (
    LLMConfigError,
    LLMError,
    LLMRequestError,
    LLMResponseError,
)
from backend.app.services.chat_service import ChatService


logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    """Chat 请求体。"""

    message: str = Field(..., min_length=1, description="用户消息，不可为空")


class ChatResponse(BaseModel):
    """Chat 响应体。"""

    answer: str


# ChatService 是无状态单例；Phase 2 保留模块级构造以保持 Phase 1 风格。
# 测试中可通过 monkeypatch 直接替换本变量。
_chat_service = ChatService()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """对话接口。

    Phase 2：根据环境变量 LLM_* 配置调用真实 LLM，
    或在 LLM_API_KEY 未配置时回退 Mock。

    异常映射：
        ValueError        → 400  Bad Request       （空消息）
        LLMConfigError    → 503  Service Unavailable  （配置错误）
        LLMRequestError   → 502  Bad Gateway         （上游错误 / 超时）
        LLMResponseError  → 502  Bad Gateway         （响应解析失败）
        LLMError          → 500  Internal Error      （兜底）
    """
    try:
        answer = await _chat_service.chat(request.message)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except LLMConfigError as exc:
        logger.warning(
            "LLM config error: %s",
            exc,
            extra={"error_type": "LLMConfigError"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM 配置错误: {exc}",
        )
    except LLMRequestError as exc:
        logger.warning(
            "LLM request error: %s",
            exc,
            extra={"error_type": "LLMRequestError"},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM 请求失败: {exc}",
        )
    except LLMResponseError as exc:
        logger.warning(
            "LLM response error: %s",
            exc,
            extra={"error_type": "LLMResponseError"},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM 响应解析失败: {exc}",
        )
    except LLMError as exc:
        logger.exception("LLM unknown error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LLM 调用异常: {exc}",
        )
    return ChatResponse(answer=answer)


__all__ = ["router", "ChatRequest", "ChatResponse"]