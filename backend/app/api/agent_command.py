"""Agent **命令** API（Phase 7 Step 4）—— **认证 + 项目作用域** 写边界。

```text
POST /api/agent-runs                              （start：启动新 Agent Run）
POST /api/agent-runs/{run_id}/resume              （恢复 / 审批后继续）
POST /api/agent-runs/{run_id}/cancel              （协作式取消，三态）
POST /api/agent-approvals/{approval_id}/decision  （审批裁决；只改控制状态）
        ↓  X-API-Key（服务端配置比对；恒定时间——agent_command_auth）
        ↓  授权 project_id（**仅**来自服务端配置 AGENT_COMMAND_API_PROJECT_ID）
AgentCommandService（归属前置校验 + 命令执行 + best-effort 持久化）
        ↓
既有 AgentRuntime（run / resume——**不修改**状态机）
        ↓
既有 AgentCancellationCoordinator（cancel）· AgentApprovalStore（decision）
```

边界（本步只适配、不重造——与 Workflow Command API 同形）：
    * **不**修改 Agent Runtime / Domain / Persistence 业务语义；
    * **不**新增数据库表 / **不**改 schema；**不**引入新认证机制
      （X-API-Key + 服务端唯一作用域；凭据独立可轮换）；
    * 客户端**不能**指定 project_id（无 Query / Header / Body 参数）；
      跨项目 / 不存在命令在执行前 ⇒ 同一 404（不泄露存在性）；
    * 审批**只改控制状态**（不调用 LLM / Tool / Resume）——执行由
      ``resume`` 命令承担（与 Orchestrator 的审批语义一致）；
    * 响应只由 ``backend.app.dto.agent_command_api`` 的 Pydantic DTO 组成
      （**无** prompt / arguments / claim_token / ORM / 异常正文）；
    * fail-closed：终态审批不可覆盖（409）；取消三态不二值化。

错误契约：
```text
200  命令完成（含业务性结果：COMPLETED / FAILED / LIMIT_REACHED /
     WAITING_APPROVAL / CANCELLED / CANCEL_REQUESTED / 幂等审批）
400  输入违反契约（空白 question / decision 非法等）
401  缺少或无效的 X-API-Key（固定文案；不泄露目标资源是否存在）
404  Run / Approval 不存在 **或** 不属于授权项目（**故意同码**）
409  状态冲突（APPROVAL_CONFLICT / CANCELLATION_CONFLICT /
     NOT_CANCELLABLE——固定文案）
422  请求体校验失败（FastAPI / Pydantic）
502  持久化边界不可用（不暴露内部细节）
503  服务端认证 / 作用域未配置（fail closed）
500  其它未预期错误（固定文案；不暴露 traceback / SQL / 凭据）
```

部署状态（**如实记录，不隐藏**——沿 S7.1 / S7.2 / S8A / Step 1）：
    应用层认证与项目作用域授权已实现（本模块 + ``agent_command_auth``）；
    但 **HTTPS = NOT VERIFIED · 网络边界 = NOT VERIFIED**——
    ``PRODUCTION_DEPLOYMENT`` 仍为 **BLOCKED**。
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Security, status

from backend.app.api.agent_command_auth import (
    AgentCommandScope,
    api_key_header,
    require_agent_command_scope,
)
from backend.app.db.agent_approval_repository import (
    AgentApprovalRepositoryError,
)
from backend.app.dto.agent_command_api import (
    AgentCommandResultResponse,
    AgentResumeRequest,
    AgentRunSummaryView,
    AgentStartRequest,
    ApprovalDecisionRequest,
)
from backend.app.services.agent_command_service import (
    AGENT_ERROR_APPROVAL_CONFLICT,
    AGENT_ERROR_CANCELLATION_CONFLICT,
    AGENT_ERROR_RUN_NOT_CANCELLABLE,
    AGENT_OUTCOME_CONFLICT,
    AGENT_OUTCOME_NOT_CANCELLABLE,
    AGENT_OUTCOME_NOT_FOUND,
    AgentCommandError,
    AgentCommandOutcome,
    AgentCommandService,
)

logger = logging.getLogger(__name__)
router = APIRouter()

#: 路径参数长度边界（与 db 模型列宽一致：run_id 64 / approval_id 128）。
AGENT_RUN_ID_MAX_LENGTH: int = 64
AGENT_APPROVAL_ID_MAX_LENGTH: int = 128

#: 固定错误文案（**不**回传 DB 异常文本 / SQL / 路径 / 凭据）。
_RUN_NOT_FOUND_DETAIL: str = "Agent Run 不存在"
_APPROVAL_NOT_FOUND_DETAIL: str = "Agent 审批不存在"
_INVALID_INPUT_DETAIL: str = "非法输入：命令参数不满足契约"
_UNAVAILABLE_DETAIL: str = "Agent 命令服务不可用"

#: 409 文案（按稳定 error_code 取值；**不**含资源存在性 / 内部细节）。
_CONFLICT_DETAILS: dict[str, str] = {
    AGENT_ERROR_APPROVAL_CONFLICT: "Agent 审批冲突（重复或相反的裁决）",
    AGENT_ERROR_CANCELLATION_CONFLICT: (
        "Agent Run 取消冲突（并发竞争无法确定，fail-closed）"
    ),
    AGENT_ERROR_RUN_NOT_CANCELLABLE: "Agent Run 不可取消（已终态或无控制状态）",
}
_CONFLICT_FALLBACK_DETAIL: str = "Agent 命令状态冲突（命令与当前状态不匹配）"

#: 四个端点共享的 responses 片段（认证 / 授权错误声明）。
_AUTH_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {
        "description": (
            "缺少或无效的 X-API-Key（固定文案；不泄露目标资源是否存在）"
        )
    },
    503: {"description": "服务端认证 / 作用域未配置（fail closed）"},
}


def get_agent_command_service() -> AgentCommandService:
    """Composition Root：装配命令服务（测试以 monkeypatch 替换本函数）。

    授权项目经 **auth 模块的 Composition Root**（``get_agent_command_settings``）
    解析——与认证依赖**同一配置源**（该凭据的唯一授权作用域），HTTP 不可
    注入。项目未启用 Agent / 未注册 ⇒ 503 fail-closed。
    """
    from backend.app.api.agent_command_auth import (
        get_agent_command_settings,
    )
    from backend.app.projects.registry import ProjectNotFoundError
    from backend.app.services.ai_orchestrator_service import (
        AIOrchestratorUnavailableError,
    )
    from backend.app.services.project_orchestrator_factory import (
        AgentCommandUnavailableError,
        build_agent_command_service,
    )

    project_id = get_agent_command_settings().api_project_id.strip()
    if not project_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        )
    try:
        return build_agent_command_service(project_id)
    except AgentCommandUnavailableError as exc:
        logger.error("agent command service unavailable", extra={"reason": "disabled"})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc
    except ProjectNotFoundError as exc:
        logger.error("agent command project not registered")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc
    except AIOrchestratorUnavailableError as exc:
        logger.error("agent command orchestrator unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc


async def _call_command(
    operation: str, awaitable: Any
) -> AgentCommandOutcome:
    """统一调用（await；异常 ⇒ 安全映射——不吞、不伪 200）。"""
    try:
        return await awaitable
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_INPUT_DETAIL,
        ) from exc
    except AgentApprovalRepositoryError as exc:
        logger.error(
            "%s unavailable", operation, extra={"error_type": type(exc).__name__}
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc
    except AgentCommandError as exc:
        logger.error(
            "%s unavailable", operation, extra={"error_type": type(exc).__name__}
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc
    except Exception as exc:  # noqa: BLE001 —— 不暴露 traceback / 内部路径
        logger.error(
            "%s unavailable", operation, extra={"error_type": type(exc).__name__}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc


def _to_response(
    outcome: AgentCommandOutcome,
    *,
    approval: bool = False,
) -> AgentCommandResultResponse:
    """AgentCommandOutcome ⇒ 响应 DTO（失败 / 冲突 ⇒ HTTP 错误）。"""
    kind = outcome.kind
    if kind == AGENT_OUTCOME_NOT_FOUND:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                _APPROVAL_NOT_FOUND_DETAIL if approval else _RUN_NOT_FOUND_DETAIL
            ),
        )
    if kind == AGENT_OUTCOME_NOT_CANCELLABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_CONFLICT_DETAILS[AGENT_ERROR_RUN_NOT_CANCELLABLE],
        )
    if kind == AGENT_OUTCOME_CONFLICT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_CONFLICT_DETAILS.get(
                outcome.error_code or "", _CONFLICT_FALLBACK_DETAIL
            ),
        )
    run_view: AgentRunSummaryView | None = None
    if outcome.run_view is not None:
        record = outcome.run_view
        run_view = AgentRunSummaryView(
            run_id=record.run_id,
            project_id=record.project_id,
            status=record.status,
            started_at=record.started_at,
            finished_at=record.finished_at,
        )
    return AgentCommandResultResponse(
        run_id=outcome.run_id,
        outcome=kind,
        status=outcome.status,
        error_code=outcome.error_code,
        approval_id=outcome.approval_id,
        approval_status=outcome.approval_status,
        decision=outcome.decision,
        content=outcome.content,
        run=run_view,
    )


# ============================================================
# 端点（**认证 + 项目作用域**；写方法）
# ============================================================


@router.post(
    "/agent-runs",
    response_model=AgentCommandResultResponse,
    status_code=status.HTTP_200_OK,
    responses={
        **_AUTH_RESPONSES,
        400: {"description": "question 非法（空白 / 越界）"},
        502: {"description": "持久化边界不可用"},
        500: {"description": "Agent 执行失败（固定文案）"},
    },
)
async def start_agent_run(
    body: AgentStartRequest,
    scope: Annotated[
        AgentCommandScope, Security(require_agent_command_scope)
    ],
) -> AgentCommandResultResponse:
    """启动一次新的 Agent Run（有界循环到终态 / 等待点）。

    授权作用域经 per-project 装配的 Runtime 兑现（allowed_tools /
    ToolExecutionService 均绑定 ``scope.project_id``）——客户端不能指定
    项目。持久化 best-effort（与 Orchestrator Agent 路径同序）。
    """
    service = get_agent_command_service()
    outcome = await _call_command(
        "agent start",
        service.start_agent(project_id=scope.project_id, question=body.question),
    )
    return _to_response(outcome)


@router.post(
    "/agent-runs/{run_id}/resume",
    response_model=AgentCommandResultResponse,
    responses={
        **_AUTH_RESPONSES,
        400: {"description": "run_id / question 非法"},
        404: {"description": "Run 不存在或不属于授权项目（故意同码）"},
        409: {"description": "恢复被拒绝（终态 / 已取消 / 冲突——固定文案）"},
        502: {"description": "持久化边界不可用"},
        500: {"description": "Agent 执行失败（固定文案）"},
    },
)
async def resume_agent_run(
    run_id: Annotated[
        str, Path(min_length=1, max_length=AGENT_RUN_ID_MAX_LENGTH)
    ],
    body: AgentResumeRequest,
    scope: Annotated[
        AgentCommandScope, Security(require_agent_command_scope)
    ],
) -> AgentCommandResultResponse:
    """恢复一个已存在的 Agent Run（TERMINAL ⇒ 历史终态；Replay 不重跑）。"""
    if not run_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_INPUT_DETAIL,
        )
    service = get_agent_command_service()
    outcome = await _call_command(
        "agent resume",
        service.resume_agent(
            project_id=scope.project_id,
            run_id=run_id,
            question=body.question,
        ),
    )
    return _to_response(outcome)


@router.post(
    "/agent-runs/{run_id}/cancel",
    response_model=AgentCommandResultResponse,
    responses={
        **_AUTH_RESPONSES,
        400: {"description": "run_id 非法（空白）"},
        404: {"description": "Run 不存在或不属于授权项目（故意同码）"},
        409: {"description": "不可取消 / 取消冲突（固定文案）"},
        502: {"description": "持久化边界不可用"},
    },
)
async def cancel_agent_run(
    run_id: Annotated[
        str, Path(min_length=1, max_length=AGENT_RUN_ID_MAX_LENGTH)
    ],
    scope: Annotated[
        AgentCommandScope, Security(require_agent_command_scope)
    ],
) -> AgentCommandResultResponse:
    """协作式取消（三态：CANCEL_REQUESTED / CANCELLED / NOT_CANCELLABLE→409）。

    **不**强杀运行中的 Tool：等它返回 ⇒ Tool Result 保留 ⇒ CANCELLED。
    """
    if not run_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_INPUT_DETAIL,
        )
    service = get_agent_command_service()
    outcome = await _call_command(
        "agent cancel",
        service.cancel_agent(project_id=scope.project_id, run_id=run_id),
    )
    return _to_response(outcome)


@router.post(
    "/agent-approvals/{approval_id}/decision",
    response_model=AgentCommandResultResponse,
    responses={
        **_AUTH_RESPONSES,
        400: {"description": "decision 非法（非 APPROVED / REJECTED）"},
        404: {"description": "审批不存在或不属于授权项目（故意同码）"},
        409: {"description": "重复 / 相反裁决（固定文案）"},
        502: {"description": "持久化边界不可用"},
    },
)
async def decide_agent_approval(
    approval_id: Annotated[
        str, Path(min_length=1, max_length=AGENT_APPROVAL_ID_MAX_LENGTH)
    ],
    body: ApprovalDecisionRequest,
    scope: Annotated[
        AgentCommandScope, Security(require_agent_command_scope)
    ],
) -> AgentCommandResultResponse:
    """审批裁决（APPROVED / REJECTED）——**只改控制状态**。

    执行由 ``POST /api/agent-runs/{run_id}/resume`` 承担（与 Orchestrator
    审批语义一致：批准后执行前仍重新授权）。
    """
    if not approval_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_INPUT_DETAIL,
        )
    service = get_agent_command_service()
    outcome = await _call_command(
        "agent approval decision",
        service.decide_approval(
            project_id=scope.project_id,
            approval_id=approval_id,
            decision=body.decision,
        ),
    )
    return _to_response(outcome, approval=True)


__all__ = [
    "router",
    "get_agent_command_service",
    "AGENT_RUN_ID_MAX_LENGTH",
    "AGENT_APPROVAL_ID_MAX_LENGTH",
]
