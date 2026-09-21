"""Chat 接口（Phase 1：Mock）。

边界：
    HTTP Request
        ↓
    /api/chat
        ↓
    ChatService
        ↓
    LLMClient
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.services.chat_service import ChatService


router = APIRouter()


class ChatRequest(BaseModel):
    """Chat 请求体。"""

    message: str = Field(..., min_length=1, description="用户消息，不可为空")


class ChatResponse(BaseModel):
    """Chat 响应体。"""

    answer: str


# ChatService 是无状态单例；Phase 1 直接模块级构造即可。
_chat_service = ChatService()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """对话接口。

    Phase 1：返回 Mock 答案，用于打通工程链路。
    Phase 2：替换 ChatService 内部为真实 LLM 调用。
    """
    try:
        answer = await _chat_service.chat(request.message)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return ChatResponse(answer=answer)


__all__ = ["router", "ChatRequest", "ChatResponse"]
