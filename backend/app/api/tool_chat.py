"""Tool Chat API（Phase 3.6.2 引入；Phase 3.6.3 升级为 Multi-Step）。

边界：

    HTTP Request
        ↓
    /api/chat/with-tools
        ↓
    ToolChatService（预算内多轮循环）
        ↓
    LLMClient（tools）→ ToolExecutionService（执行边界）
                        → ToolRegistry（真实只读 get_inventory）
                        → PostgreSQL（READ ONLY）
                        → ... → LLMClient（最终回答）
        ↓
    Tool Chat Response（纯 DTO：answer + Tool 名称列表，按执行顺序）

Phase 3.11 Step 9：Tool 执行统一走 ``ToolExecutionService``
（应用层唯一执行入口）。

Phase 3.11 Step 10：可选 ``project_id`` → 服务器端 ProjectRegistry 解析
该项目 capabilities → **项目级** ToolExecutionService（capability +
project_id）逐次传入 ``chat()``：

    project_id
        ↓ ProjectRegistry.get(project_id)（未注册 → 404）
    ProjectRegistration.capabilities（服务器端决定，HTTP 不可注入）
        ↓
    ToolExecutionService(registry, capabilities, project_id)
        ↓ capability 拒绝 → AIOrchestratorCapabilityError → 403
    ToolRegistry → Tool

未提供 ``project_id`` → capabilities=None（不限制；不查注册表）。

Phase 3.11 Step 11：生产 Registry 注册**真实只读** ``get_inventory``
（Phase 3.7.12 实现，SQL 只读事务 + 绑定参数 + statement_timeout）：
本链路此前只挂 Phase 3.6.1 Mock Tools；Mock Tools 现仅供单元 /
characterization 测试使用（测试自行构造 Mock Registry 注入），
**不**出现在生产 Registry 中（避免同名 Tool 双定义）。

    ToolCall(get_inventory)
        ↓ ToolExecutionService（capability + project scope）
    ToolRegistry（真实 GET_INVENTORY_DEFINITION + GetInventoryHandler）
        ↓ PostgreSQL（SELECT SUM(qty) …，READ ONLY）
    ToolResult → role=tool 消息 → LLM → Final Answer

真实 ``get_work_order`` 接入 = DEFERRED（Step 12+）。

安全（Step 11 未削弱任何既有机制）：

    * Tool 参数只有 material_code（必填）/ warehouse_code（可选，当前库存表
      无 warehouse 维度 → Handler 显式拒绝，不静默全仓汇总）；
    * Tool 不接受任意 SQL / table / where_clause（Schema 拒绝未知字段）；
    * 只读：BEGIN READ ONLY + SET LOCAL statement_timeout + 绑定参数 + rollback；
    * ToolResult.error 不含 DATABASE_URL / SQL 原文 / traceback。

与 /api/chat 的关系：

    * /api/chat（RAG 链路）行为完全不变（Phase 3.5.6 协议保持）；
    * 本端点是独立的 Tool Calling 链路，**不**经过 RAG。

Multi-Step 约束（Phase 3.6.3）：

    * 顺序多步：LLM → Tool → LLM → Tool → ... → LLM 最终回答；
    * 每个 LLM 响应最多 1 个 Tool Call（多个 → 502）；
    * 总轮数硬上限 TOOL_MAX_ROUNDS（默认 5，钳制 [1, 20]）：
      预算耗尽后 LLM 仍请求 Tool → 502（不执行、不再请求 LLM）。

错误映射（沿用项目既有原则）：

    ValueError                                 → 400  （空消息，服务层校验）
    ProjectNotFoundError                       → 404  （project_id 未注册）
    AIOrchestratorCapabilityError              → 403  （项目能力未启用）
    MultipleToolCallsError                     → 502  （LLM 返回多个 Tool Call）
    ToolCallingBudgetExceededError             → 502  （Tool Calling 预算耗尽）
    LLMConfigError                             → 503
    LLMRequestError / LLMResponseError（含 LLMToolCallFormatError）→ 502
    ToolChatError                              → 500
    未知异常                                   → 原样上抛（全局中间件兜底 500）

安全：响应体与错误 detail 均不包含 API Key / Authorization /
DATABASE_URL / SQL / traceback / Tool handler / embedding。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.api._rag_error_mapping import rag_pipeline_error_to_http
from backend.app.projects.registry import (
    ProjectNotFoundError,
    ProjectRegistry,
    get_default_project_registry,
)
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorCapabilityError,
)
from backend.app.services.tool_chat_service import (
    MultipleToolCallsError,
    ToolCallingBudgetExceededError,
    ToolChatError,
    ToolChatResponse,
    ToolChatService,
)
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.get_inventory import register_get_inventory_tool
from backend.app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)
router = APIRouter()


class ToolChatRequest(BaseModel):
    """Tool Chat 请求体。

    Attributes:
        message:    必填，非空字符串（协议与 /api/chat 一致）。
        project_id: Phase 3.11 Step 10 —— 可选项目上下文 ID；
                    提供时由服务器端 ProjectRegistry 解析该项目的
                    Tool 能力白名单（HTTP 无法注入 / 覆盖能力字段）；
                    省略时保持旧行为（不限制 Tool 能力）。
    """

    message: str = Field(..., min_length=1, description="用户消息，不可为空")
    project_id: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "可选：项目上下文 ID（服务器端注册表解析该项目 Tool 能力）；"
            "省略时不限制 Tool 能力（旧行为）。"
        ),
    )


class ToolChatCallInfoResponse(BaseModel):
    """Tool 调用元信息（仅 Tool 名称，不含 arguments / 执行细节）。"""

    tool_name: str = Field(..., description="实际执行的 Tool 名称")


class ToolChatApiResponse(BaseModel):
    """Tool Chat 响应体。"""

    answer: str = Field(..., description="AI 最终回答（经 Tool Calling 链路生成）")
    tool_calls: list[ToolChatCallInfoResponse] = Field(
        default_factory=list,
        description=(
            "本次请求实际执行过的 Tool 调用（仅名称，按执行顺序；"
            "数量上限 = TOOL_MAX_ROUNDS，默认 5）"
        ),
    )


# 模块级单例（与 chat.py 的 _chat_service 风格一致，便于测试 monkeypatch）。
# Phase 3.11 Step 11：生产 Registry 注册**真实只读** get_inventory
# （唯一有效 Definition；Mock get_inventory 不再出现在本 Registry）。
# 真实 get_work_order = DEFERRED（本阶段不接入）。
# 测试若需 Mock Tools：自行构造 Mock Registry 并 monkeypatch `_tool_registry`。
_tool_registry = ToolRegistry()
register_get_inventory_tool(_tool_registry)
# Phase 3.11 Step 9：统一 Tool 执行边界（默认 capabilities=None → 不限制，
# 供未提供 project_id 的调用路径使用）。
_tool_execution_service = ToolExecutionService(registry=_tool_registry)
_tool_chat_service = ToolChatService(
    execution_service=_tool_execution_service
)


def _project_registry() -> ProjectRegistry:
    """项目注册表（默认进程级单例；测试可 monkeypatch 注入独立注册表）。"""
    return get_default_project_registry()


def _build_project_execution_service(project_id: str) -> ToolExecutionService:
    """按 ``project_id`` 构造项目级 Tool 执行边界（Phase 3.11 Step 10）。

    能力来源唯一权威 = 服务器端 ProjectRegistry（HTTP 不可注入能力字段）：

        project_id
            ↓ ProjectRegistry.get(project_id)
        ProjectRegistration.capabilities
            ↓
        ToolExecutionService(registry, capabilities, project_id)
            ↓ （capability 拒绝 → AIOrchestratorCapabilityError → 403）

    Args:
        project_id: 请求中的 project_id（Pydantic 已校验非空 / 长度）。

    Returns:
        绑定该项目能力白名单的执行边界（registry = 模块级真实 Tool Registry，
        即 Phase 3.11 Step 11 的 ``get_inventory`` 真实只读实现）。

    Raises:
        ProjectNotFoundError: project_id 未注册（调用方映射 404）。
    """
    registration = _project_registry().get(project_id)
    return ToolExecutionService(
        registry=_tool_registry,
        capabilities=registration.capabilities,
        project_id=project_id,
    )


@router.post(
    "/chat/with-tools",
    response_model=ToolChatApiResponse,
    responses={
        400: {"description": "请求参数非法（message 等）"},
        403: {"description": "项目能力未启用（Phase 3.11 Step 10 capability denied）"},
        404: {"description": "project_id 未注册（Phase 3.11 Step 10）"},
        422: {"description": "请求体校验失败（Pydantic）"},
        500: {"description": "Tool Chat 服务内部错误"},
        502: {"description": "LLM 多 Tool Call / 预算耗尽 / LLM 响应解析失败"},
        503: {"description": "LLM 配置错误"},
    },
)
async def chat_with_tools(request: ToolChatRequest) -> ToolChatApiResponse:
    """对话接口（Phase 3.6.3 Multi-Step + Phase 3.11 Step 11 真实 Tool）。

    Pipeline：

        ToolChatService → LLM（tools）
                       → [ToolExecutionService（capability + project scope）
                          → ToolRegistry.execute → tool message → LLM] × N
                       → answer（N <= TOOL_MAX_ROUNDS，默认 5）

    约束：每轮最多 1 个 Tool Call（顺序执行）；总轮数受
    TOOL_MAX_ROUNDS 硬限制。

    project_id（Phase 3.11 Step 10，可选）：

        * 提供 → 服务器端 ProjectRegistry 解析该项目 Tool 能力白名单，
          每次 Tool 执行前由执行边界校验；未注册 → 404；
          能力未启用 → 403（Handler 0 次调用）；
        * 省略 → 不限制 Tool 能力（旧行为，完全不变）。
    """
    try:
        if request.project_id:
            # Phase 3.11 Step 10：项目级执行边界（capability + project_id）
            # 逐次传入；未提供 project_id 时保持旧调用形态（完全旧行为）。
            execution_service = _build_project_execution_service(
                request.project_id
            )
            result: ToolChatResponse = await _tool_chat_service.chat(
                request.message,
                registry=_tool_registry,
                execution_service=execution_service,
            )
        else:
            result = await _tool_chat_service.chat(
                request.message, registry=_tool_registry
            )
    except ProjectNotFoundError as exc:
        # Phase 3.11 Step 10：project_id 只能选择服务器端已注册的项目
        logger.warning(
            "tool chat project not registered",
            extra={"project_id": request.project_id},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目未注册: {exc}",
        )
    except AIOrchestratorCapabilityError as exc:
        # Phase 3.11 Step 10：项目能力未启用（执行边界前置校验；
        # Handler 0 次调用）。授权失败**不**降级为 Tool 失败 / 200。
        logger.warning(
            "tool chat capability denied",
            extra={
                "project_id": request.project_id,
                "capability": exc.capability,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"项目能力未启用: {exc}",
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
    except ToolCallingBudgetExceededError as exc:
        logger.warning(
            "tool chat budget exceeded",
            extra={
                "error_type": "ToolCallingBudgetExceededError",
                "max_rounds": exc.max_rounds,
                "requested_tool": exc.requested_tool,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Tool Calling 预算耗尽（max_rounds={exc.max_rounds}），"
            f"LLM 仍请求调用 Tool {exc.requested_tool!r}；"
            "请缩小问题范围或调整 TOOL_MAX_ROUNDS",
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
