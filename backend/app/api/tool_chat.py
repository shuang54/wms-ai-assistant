"""Tool Chat API（Phase 3.6.2：LLM Function Calling）。

边界：

    HTTP Request
        ↓
    /api/chat/with-tools
        ↓
    ToolChatService
        ↓
    LLMClient（tools）→ ToolRegistry（Mock Tools）→ LLMClient（最终回答）
        ↓
    Tool Chat Response（纯 DTO：answer + tool 名称列表）

与 /api/chat 的关系：

    * /api/chat（RAG 链路）行为完全不变（Phase 3.5.6 协议保持）；
    * 本端点是独立的 Tool Calling 链路，**不**经过 RAG。

错误映射（沿用项目既有原则）：

    ValueError                      → 400  （空消息，服务层校验）
    MultipleToolCallsError          → 502  （LLM 返回多个 Tool Call）
    LLMConfigError                  → 503
    LLMRequestError / LLMResponseError（含 LLMToolCallFormatError）→ 502
    ToolChatError                   → 500
    未知异常                        → 原样上抛（全局中间件兜底 500）

安全：响应体与错误 detail 均不包含 API Key / Authorization /
DATABASE_URL / SQL / traceback / Tool handler / embedding。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.api._rag_error_mapping import rag_pipeline_error_to_http
from backend.app.services.tool_chat_service import (
    MultipleToolCallsError,
    ToolChatError,
    ToolChatResponse,
    ToolChatService,
)
from backend.app.tools.mock_tools import register_mock_tools
from backend.app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)
router = APIRouter()


class ToolChatRequest(BaseModel):
    """Tool Chat 请求体（协议与 /api/chat 一致：message 单字段）。"""

    message: str = Field(..., min_length=1, description="用户消息，不可为空")


class ToolChatCallInfoResponse(BaseModel):
    """Tool 调用元信息（仅 Tool 名称，不含 arguments / 执行细节）。"""

    tool_name: str = Field(..., description="本次请求实际调用的 Tool 名称")


class ToolChatApiResponse(BaseModel):
    """Tool Chat 响应体。"""

    answer: str = Field(..., description="AI 最终回答（经 Tool Calling 链路生成）")
    tool_calls: list[ToolChatCallInfoResponse] = Field(
        default_factory=list,
        description="本次请求实际发生的 Tool 调用（仅名称；最多 1 个）",
    )


# 模块级单例（与 chat.py 的 _chat_service 风格一致，便于测试 monkeypatch）。
# Registry 只注册 Phase 3.6.1 的两个 Mock Tool；接入真实 WMS Tool 时在此扩展。
_tool_registry = ToolRegistry()
register_mock_tools(_tool_registry)
_tool_chat_service = ToolChatService()


@router.post("/chat/with-tools", response_model=ToolChatApiResponse)
async def chat_with_tools(request: ToolChatRequest) -> ToolChatApiResponse:
    """对话接口（Phase 3.6.2：LLM Function Calling + Mock Tools）。

    Pipeline：

        ToolChatService → LLM #1（tools）→ [ToolRegistry.execute]
                       → LLM #2 → answer

    约束：单轮最多 1 次 Tool Call、最多 2 轮 LLM。
    """
    try:
        result: ToolChatResponse = await _tool_chat_service.chat(
            request.message, registry=_tool_registry
        )
    except ValueError as exc:
        # 纯空白消息：Pydantic 只拦截 ""，空白由服务层拒绝（与 /api/chat 一致）
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except MultipleToolCallsError as exc:
        logger.warning(
            "tool chat rejected multiple tool calls",
            extra={"error_type": "MultipleToolCallsError", "count": exc.count},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM 返回多个 Tool Call（当前不支持）: {exc}",
        )
    except ToolChatError as exc:
        logger.error(
            "tool chat service error",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Tool Chat 服务内部错误: {exc}",
        )
    except Exception as exc:
        # LLM 家族等已知异常沿用 RAG 管道映射；未知异常原样上抛
        raise rag_pipeline_error_to_http(exc)

    return ToolChatApiResponse(
        answer=result.answer,
        tool_calls=[
            ToolChatCallInfoResponse(tool_name=info.tool_name)
            for info in result.tool_calls
        ],
    )


__all__ = [
    "router",
    "ToolChatRequest",
    "ToolChatApiResponse",
    "ToolChatCallInfoResponse",
]
