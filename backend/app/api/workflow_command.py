"""Workflow **命令** API（Phase 7 Step 1）—— **认证 + 项目作用域** 写边界。

```text
POST /api/workflow-runs                              （start：启动新 Run）
POST /api/workflow-runs/{run_id}/advance             （推进 / 恢复）
POST /api/workflow-runs/{run_id}/cancel              （协作式取消，三态）
POST /api/workflow-approvals/{approval_id}/decision  （审批裁决 + 恢复）
        ↓  X-API-Key（服务端配置比对；恒定时间——workflow_command_auth）
        ↓  授权 project_id（**仅**来自服务端配置 WORKFLOW_COMMAND_API_PROJECT_ID）
WorkflowCommandService（归属前置校验 + 命令执行 + 结果读回）
        ↓
既有 WorkflowRuntime（start / advance / submit_approval_decision / request_cancel）
        ↓
既有 Repository / Store Adapter（每操作独立事务 + state_version CAS）
```

边界（本步只适配、不重造）：
    * **不**修改 Runtime / Domain / Persistence / Read Service 业务语义；
    * **不**新增数据库表 / **不**改 schema；**不**引入新认证机制
      （与 read 面同形：X-API-Key + 服务端唯一作用域；凭据独立可轮换）；
    * 客户端**不能**指定 project_id（无 Query / Header / Body 参数）；
      跨项目命令在任何执行前 ⇒ 与"不存在"同一 404（经 read service 的
      项目过滤——**同构**，不泄露存在性）；
    * 非法状态转换 / 重复命令 / CAS 冲突 ⇒ 409（固定文案，按 error_code
      稳定取值；不外泄内部细节）；
    * 响应只由 ``backend.app.dto.workflow_command_api`` 的 Pydantic DTO
      组成（run 视图复用只读 DTO 字段边界——**无** input / request_id）；
    * 不绕过既有的持久化 / 事务边界（服务层不介入事务；Repository 内
      ``session.begin()`` 每操作一事务）；
    * UNKNOWN 明确不重跑（fail-closed）；终态不可覆盖；取消三态不二值化。

错误契约：
```text
200  命令完成（含业务性终态：COMPLETED / FAILED / CANCELLED /
     WAITING_APPROVAL / RUNNING / CANCEL_REQUESTED / LIMIT_REACHED）
400  输入违反契约（空白字段 / input JSON 非法等）
401  缺少或无效的 X-API-Key（固定文案；不泄露目标资源是否存在）
404  Run / Approval 不存在 **或** 不属于授权项目（**故意同码**）；
     Workflow 定义未注册
409  状态冲突（STATE_CONFLICT / APPROVAL_CONFLICT / NODE_UNKNOWN /
     DEFINITION_VERSION_MISMATCH / NOT_CANCELLABLE——固定文案）
422  请求体校验失败（FastAPI / Pydantic）
502  持久化边界不可用（读回失败；不暴露内部细节）
503  服务端认证 / 作用域未配置（fail closed）
500  其它未预期错误（固定文案；不暴露 traceback / SQL / 凭据）
```

部署状态（**如实记录，不隐藏**——沿 S7.1 / S7.2 / S8A）：
    应用层认证与项目作用域授权已实现（本模块 + ``workflow_command_auth``）；
    但 **HTTPS = NOT VERIFIED · 网络边界 = NOT VERIFIED**（仓库内无网关 /
    反向代理 / TLS 证据）——``PRODUCTION_DEPLOYMENT`` 仍为 **BLOCKED**。
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Security, status

from backend.app.api.workflow_command_auth import (
    WorkflowCommandScope,
    require_workflow_command_scope,
)
from backend.app.dto.workflow_command_api import (
    ApprovalDecisionRequest,
    WorkflowCommandResultResponse,
    WorkflowStartRequest,
)
from backend.app.dto.workflow_read_api import WorkflowRunDetailResponse
from backend.app.workflow.errors import (
    WF_ERROR_APPROVAL_CONFLICT,
    WF_ERROR_DEFINITION_VERSION_MISMATCH,
    WF_ERROR_NODE_UNKNOWN,
    WF_ERROR_RUN_NOT_CANCELLABLE,
    WF_ERROR_STATE_CONFLICT,
    WorkflowDefinitionError,
    WorkflowDefinitionNotFoundError,
    WorkflowDefinitionVersionMismatchError,
)
from backend.app.workflow_runtime.command_service import (
    CommandResult,
    WorkflowCommandInputError,
    WorkflowCommandService,
    create_workflow_command_service,
)
from backend.app.workflow_runtime.read_service import (
    WorkflowReadError,
    WorkflowReadNotFoundError,
)
from backend.app.workflow_runtime.runtime import (
    OUTCOME_CONFLICT,
    OUTCOME_DEFINITION_MISMATCH,
    OUTCOME_NOT_CANCELLABLE,
    OUTCOME_NOT_FOUND,
    OUTCOME_UNKNOWN,
    RunOutcome,
)

logger = logging.getLogger(__name__)
router = APIRouter()

#: 路径参数长度边界（与 db 模型列宽一致：run_id 64 / approval_id 128）。
WORKFLOW_RUN_ID_MAX_LENGTH: int = 64
WORKFLOW_APPROVAL_ID_MAX_LENGTH: int = 128

#: 固定错误文案（**不**回传 DB 异常文本 / SQL / 路径 / 凭据）。
_RUN_NOT_FOUND_DETAIL: str = "Workflow Run 不存在"
_APPROVAL_NOT_FOUND_DETAIL: str = "Workflow 审批不存在"
_DEFINITION_NOT_FOUND_DETAIL: str = "Workflow 定义不存在"
_INVALID_INPUT_DETAIL: str = "非法输入：命令参数不满足契约"
_UNAVAILABLE_DETAIL: str = "Workflow 命令服务不可用"

#: 409 文案（按稳定 error_code 取值；**不**含资源存在性 / 内部细节）。
_CONFLICT_DETAILS: dict[str, str] = {
    WF_ERROR_STATE_CONFLICT: "Workflow Run 状态冲突（命令与当前状态不匹配）",
    WF_ERROR_APPROVAL_CONFLICT: "Workflow 审批冲突（重复或相反的裁决）",
    WF_ERROR_NODE_UNKNOWN: "Workflow Run 处于未知状态（不自动重跑，需人工介入）",
    WF_ERROR_DEFINITION_VERSION_MISMATCH: (
        "Workflow 定义版本不匹配（fail-closed，需人工介入）"
    ),
}
_NOT_CANCELLABLE_DETAIL: str = (
    "Workflow Run 不可取消（已终态或不符合取消条件）"
)
_CONFLICT_FALLBACK_DETAIL: str = "Workflow 状态冲突（命令与当前状态不匹配）"


def get_workflow_command_service() -> WorkflowCommandService:
    """Composition Root：装配命令服务（测试以 monkeypatch 替换本函数）。"""
    return create_workflow_command_service()


# ============================================================
# 结果映射（RunOutcome → HTTP；显式、白名单驱动）
# ============================================================


def _reject_blank(field: str, value: str) -> None:
    """API 层前置参数校验（空白 ⇒ 400，固定文案前缀；不触服务层）。"""
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"非法输入: {field} 不得为空或纯空白",
        )


def _log_failure(operation: str, exc: BaseException) -> None:
    """仅记录异常**类型名**（不写正文 / 凭据 / SQL）。"""
    logger.error(
        "%s unavailable", operation, extra={"error_type": type(exc).__name__}
    )


def _not_found_or_none(outcome: RunOutcome, *, approval: bool) -> None:
    """NOT_FOUND ⇒ 固定 404（与"不属于授权项目"同码同文案）。"""
    detail = _APPROVAL_NOT_FOUND_DETAIL if approval else _RUN_NOT_FOUND_DETAIL
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _conflict_detail(outcome: RunOutcome) -> str:
    if outcome.error_code is not None:
        return _CONFLICT_DETAILS.get(
            outcome.error_code, _CONFLICT_FALLBACK_DETAIL
        )
    return _CONFLICT_FALLBACK_DETAIL


def _raise_for_outcome(outcome: RunOutcome, *, approval: bool) -> None:
    """把**失败/冲突**类 outcome 映射为 HTTP 错误（业务结果 ⇒ 200 放行）。"""
    kind = outcome.outcome
    if kind == OUTCOME_NOT_FOUND:
        _not_found_or_none(outcome, approval=approval)
    if kind == OUTCOME_NOT_CANCELLABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_NOT_CANCELLABLE_DETAIL,
        )
    if kind == OUTCOME_UNKNOWN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_CONFLICT_DETAILS[WF_ERROR_NODE_UNKNOWN],
        )
    if kind == OUTCOME_DEFINITION_MISMATCH:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_CONFLICT_DETAILS[WF_ERROR_DEFINITION_VERSION_MISMATCH],
        )
    if kind == OUTCOME_CONFLICT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_conflict_detail(outcome),
        )
    # 其余（COMPLETED / FAILED / CANCELLED / WAITING_APPROVAL / RUNNING /
    # CANCEL_REQUESTED / LIMIT_REACHED）⇒ 200 业务结果。


def _to_response(result: CommandResult) -> WorkflowCommandResultResponse:
    """CommandResult → 响应 DTO（显式逐字段；run 视图复用只读 DTO）。"""
    outcome = result.outcome
    run_view: WorkflowRunDetailResponse | None = None
    if result.run_detail is not None:
        detail = result.run_detail
        run_view = WorkflowRunDetailResponse(
            workflow_run_id=detail.workflow_run_id,
            workflow_id=detail.workflow_id,
            workflow_version=detail.workflow_version,
            project_id=detail.project_id,
            status=detail.status,
            current_node_id=detail.current_node_id,
            next_action=detail.next_action,
            state_version=detail.state_version,
            attempt_count=detail.attempt_count,
            cancel_requested=detail.cancel_requested,
            cancel_requested_at=detail.cancel_requested_at,
            created_at=detail.created_at,
            started_at=detail.started_at,
            completed_at=detail.completed_at,
            updated_at=detail.updated_at,
        )
    return WorkflowCommandResultResponse(
        workflow_run_id=outcome.workflow_run_id,
        outcome=outcome.outcome,
        error_code=outcome.error_code,
        approval_id=outcome.approval_id,
        approval_status=outcome.approval_status,
        run=run_view,
    )


async def _call_command(operation: str, awaitable: Any, *, approval: bool) -> CommandResult:
    """统一调用（await；异常 ⇒ 安全映射——不吞、不伪 200）。"""
    try:
        return await awaitable
    except WorkflowReadNotFoundError:
        _log_failure(operation, WorkflowReadNotFoundError())
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                _APPROVAL_NOT_FOUND_DETAIL if approval else _RUN_NOT_FOUND_DETAIL
            ),
        ) from None
    except WorkflowReadError as exc:
        _log_failure(operation, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc
    except WorkflowCommandInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_INPUT_DETAIL,
        ) from exc
    except WorkflowDefinitionNotFoundError as exc:
        _log_failure(operation, exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_DEFINITION_NOT_FOUND_DETAIL,
        ) from exc
    except WorkflowDefinitionVersionMismatchError as exc:
        _log_failure(operation, exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_CONFLICT_DETAILS[WF_ERROR_DEFINITION_VERSION_MISMATCH],
        ) from exc
    except WorkflowDefinitionError as exc:
        _log_failure(operation, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_INPUT_DETAIL,
        ) from exc
    except Exception as exc:  # noqa: BLE001 —— 不暴露 traceback / 内部路径
        _log_failure(operation, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_UNAVAILABLE_DETAIL,
        ) from exc


#: 四个端点共享的 responses 片段（认证 / 授权错误声明）。
_AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "description": (
            "缺少或无效的 X-API-Key（固定文案；不泄露目标资源是否存在）"
        )
    },
    503: {"description": "服务端认证 / 作用域未配置（fail closed）"},
}


# ============================================================
# 端点（**认证 + 项目作用域**；写方法）
# ============================================================


@router.post(
    "/workflow-runs",
    response_model=WorkflowCommandResultResponse,
    status_code=status.HTTP_200_OK,
    responses={
        **_AUTH_RESPONSES,
        400: {"description": "输入非法（如 input JSON 非法）"},
        404: {"description": "Workflow 定义未注册"},
        409: {"description": "状态冲突（固定文案）"},
    },
)
async def start_workflow_run(
    body: WorkflowStartRequest,
    scope: Annotated[
        WorkflowCommandScope, Security(require_workflow_command_scope)
    ],
) -> WorkflowCommandResultResponse:
    """启动工作流（新 Run；inline 推进到终态 / 等待点）。"""
    service = get_workflow_command_service()
    result = await _call_command(
        "workflow start",
        service.start_workflow(
            project_id=scope.project_id,
            workflow_id=body.workflow_id,
            input=body.input,
            workflow_run_id=body.workflow_run_id,
        ),
        approval=False,
    )
    _raise_for_outcome(result.outcome, approval=False)
    return _to_response(result)


@router.post(
    "/workflow-runs/{run_id}/advance",
    response_model=WorkflowCommandResultResponse,
    responses={
        **_AUTH_RESPONSES,
        404: {"description": "Run 不存在或不属于授权项目（故意同码）"},
        409: {"description": "状态冲突 / UNKNOWN（固定文案）"},
    },
)
async def advance_workflow_run(
    run_id: Annotated[
        str, Path(min_length=1, max_length=WORKFLOW_RUN_ID_MAX_LENGTH)
    ],
    scope: Annotated[
        WorkflowCommandScope, Security(require_workflow_command_scope)
    ],
) -> WorkflowCommandResultResponse:
    """推进 / 恢复工作流（幂等可重入；终态 ⇒ 返回终态）。"""
    _reject_blank("run_id", run_id)
    service = get_workflow_command_service()
    result = await _call_command(
        "workflow advance",
        service.advance_workflow(
            project_id=scope.project_id, workflow_run_id=run_id
        ),
        approval=False,
    )
    _raise_for_outcome(result.outcome, approval=False)
    return _to_response(result)


@router.post(
    "/workflow-runs/{run_id}/cancel",
    response_model=WorkflowCommandResultResponse,
    responses={
        **_AUTH_RESPONSES,
        404: {"description": "Run 不存在或不属于授权项目（故意同码）"},
        409: {"description": "不可取消（已终态）——固定文案"},
    },
)
async def cancel_workflow_run(
    run_id: Annotated[
        str, Path(min_length=1, max_length=WORKFLOW_RUN_ID_MAX_LENGTH)
    ],
    scope: Annotated[
        WorkflowCommandScope, Security(require_workflow_command_scope)
    ],
) -> WorkflowCommandResultResponse:
    """协作式取消（三态：CANCELLED / CANCEL_REQUESTED / NOT_CANCELLABLE→409）。"""
    _reject_blank("run_id", run_id)
    service = get_workflow_command_service()
    result = await _call_command(
        "workflow cancel",
        service.cancel_workflow(
            project_id=scope.project_id, workflow_run_id=run_id
        ),
        approval=False,
    )
    _raise_for_outcome(result.outcome, approval=False)
    return _to_response(result)


@router.post(
    "/workflow-approvals/{approval_id}/decision",
    response_model=WorkflowCommandResultResponse,
    responses={
        **_AUTH_RESPONSES,
        404: {"description": "审批不存在或不属于授权项目（故意同码）"},
        409: {"description": "重复 / 相反裁决或状态冲突（固定文案）"},
    },
)
async def decide_workflow_approval(
    approval_id: Annotated[
        str, Path(min_length=1, max_length=WORKFLOW_APPROVAL_ID_MAX_LENGTH)
    ],
    body: ApprovalDecisionRequest,
    scope: Annotated[
        WorkflowCommandScope, Security(require_workflow_command_scope)
    ],
) -> WorkflowCommandResultResponse:
    """审批裁决（APPROVED / REJECTED）+ 恢复执行（两段式组合，见服务层）。"""
    _reject_blank("approval_id", approval_id)
    service = get_workflow_command_service()
    result = await _call_command(
        "workflow approval decision",
        service.decide_approval(
            project_id=scope.project_id,
            approval_id=approval_id,
            decision=body.decision,
        ),
        approval=True,
    )
    _raise_for_outcome(result.outcome, approval=True)
    return _to_response(result)
