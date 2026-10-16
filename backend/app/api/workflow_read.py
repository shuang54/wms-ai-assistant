"""Workflow Read API（Phase 6 Step 7 · 7.2）—— **只读 + 认证** HTTP 边界。

```text
HTTP GET /api/workflow-runs/{run_id}                            （Run 详情）
HTTP GET /api/workflow-runs/{run_id}/nodes                      （Node attempt 全历史）
HTTP GET /api/workflow-runs/{run_id}/timeline                   （持久化时间线）
HTTP GET /api/workflow-runs/{run_id}/approvals/{node_id}        （按 (Run, Node) 的审批）
HTTP GET /api/workflow-approvals/{approval_id}                  （按 ID 的审批详情）
        ↓  X-API-Key（服务端配置比对；恒定时间）
        ↓  授权 project_id（**仅**来自服务端配置——S7.2）
WorkflowReadService（S6；只读）
        ↓
Store Ports → Store Adapters → Repositories → PostgreSQL
```

安全投影（Phase 7 Workflow Read Layer 增强）：
    * ``/nodes`` 经 ``get_node_run_details``（**安全投影**）而非原始 Store
      Record ⇒ ``output`` 的 JSON 合法性 / 32 KiB 内联上限、``status`` ·
      ``node_type`` · ``error_code`` 的域白名单均在 Read Layer 校验，
      损坏 ⇒ **502 fail closed**（不截断 / 不修复 / 不以 200 返回损坏数据）；
    * ``/approvals/{node_id}`` 经 ``get_approval_for_node`` ⇒ 父 Run 归属 +
      服务端项目作用域 + 审批行 ``(run, node)`` 归属一致性三重校验。

边界（**只读**；S7 §四 / §六 / §七 / §九）：
    * **Query only**：不调用 Runtime 的 start / advance / request_cancel /
      submit_approval_decision；不调用 Tool / LLM / Orchestrator；
      不创建 / 不恢复 / 不取消 / 不裁决；无写入路径；
    * 不直接接触 ORM / Repository / Session / SQLAlchemy——只调用
      ``WorkflowReadService``（装配在 ``workflow_runtime.read_factory``，
      API 层不 import db）；
    * Resource 不存在 **或** 不属于授权项目 ⇒ 同一 404 固定文案
      （**故意同码**，不泄漏存在性 / 不返回成功空结果）；
    * 不吞异常：存储故障 / 数据契约损坏 ⇒ 502（不是 200 + 假数据）；
    * 响应只由 ``backend.app.dto.workflow_read_api`` 的 Pydantic DTO 组成
      （``response_model`` 同时充当字段过滤边界）；error detail 使用固定
      文案（**不**含异常正文 / SQL / 凭据 / 内部路径）；
    * 同步 Read Service 经 ``asyncio.to_thread`` 调用（S1 · D-F 冻结惯例）
      ——不在事件循环中直接阻塞。

认证与授权（Phase 6 Step 7.2 —— 修复 S7.1 审计确认的缺口）：
    * 四个端点**全部**挂载
      :func:`backend.app.api.workflow_read_auth.require_workflow_read_scope`
      （FastAPI ``Security``）——认证发生在任何数据读取**之前**；
    * 授权 ``project_id`` **仅**来自服务端配置
      （``WORKFLOW_READ_API_PROJECT_ID``）——客户端 Query / 普通请求头 /
      请求体**不能**指定或扩宽作用域（本 API **不**接受 project_id 参数；
      未知 query 参数被忽略）；
    * fail closed：未配置 key / 未配置 project ⇒ 503（**绝不**匿名放行、
      **绝不**回退默认项目）；缺失 / 错误凭据 ⇒ 401（固定文案）；
    * 客户端提供的 project_id **不**被记录为已验证身份或授权结论。

错误契约（沿用既有 Agent Read / Trace / Timeline API 风格）：
```text
400  参数非法（空白 run_id / approval_id——API 层前置校验）
401  缺少或无效的 X-API-Key（固定文案；不泄露目标资源是否存在）
404  Run / Approval 不存在 **或** 不属于授权项目（**故意同码**）
422  路径参数长度越界（FastAPI Path 校验）
502  持久化读边界不可用 / 数据契约损坏（不暴露内部细节）
503  服务端认证 / 作用域未配置（fail closed）
500  其它未预期错误（不暴露 traceback / SQL / 凭据）
```

部署状态（**如实记录，不隐藏**——S7.1 审计 + S7.2 §八）：
    应用层认证与项目作用域授权已实现（本模块 + ``workflow_read_auth``）；
    但 **HTTPS = NOT VERIFIED · 网络边界 = NOT VERIFIED**（仓库内无网关 /
    反向代理 / TLS 证据）——``PRODUCTION_DEPLOYMENT`` 仍为 **BLOCKED**，
    直到部署层验证完成（外部不可绕过网关 / 只有可信网关传递身份 /
    网关覆盖而非信任客户端身份头）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Security, status

from backend.app.api.workflow_read_auth import (
    WorkflowReadScope,
    require_workflow_read_scope,
)
from backend.app.dto.workflow_read_api import (
    WorkflowApprovalResponse,
    WorkflowNodeRunResponse,
    WorkflowRunDetailResponse,
    WorkflowTimelineEventResponse,
)
from backend.app.workflow_runtime.read_factory import (
    create_workflow_read_service,
)
from backend.app.workflow_runtime.read_service import (
    ApprovalDetail,
    ApprovalRecord,
    NodeRunDetail,
    RunDetail,
    TimelineEvent,
    WorkflowReadError,
    WorkflowReadNotFoundError,
    WorkflowReadService,
)

logger = logging.getLogger(__name__)
router = APIRouter()

#: 路径参数长度边界（与 db 模型列宽一致：run_id 64 / node_id 64 /
#: approval_id 128；API 测试锁定一致性）。
WORKFLOW_RUN_ID_MAX_LENGTH: int = 64
WORKFLOW_NODE_ID_MAX_LENGTH: int = 64
WORKFLOW_APPROVAL_ID_MAX_LENGTH: int = 128

#: 固定错误文案（**不**回传 DB 异常文本 / SQL / 路径 / 凭据）。
_RUN_NOT_FOUND_DETAIL: str = "Workflow Run 不存在"
_APPROVAL_NOT_FOUND_DETAIL: str = "Workflow 审批不存在"
_UNAVAILABLE_DETAIL: str = "Workflow 读取数据源不可用"


def get_workflow_read_service() -> WorkflowReadService:
    """Composition Root：装配只读服务（测试以 monkeypatch 替换本函数）。"""
    return create_workflow_read_service()


# ============================================================
# 显式逐字段映射（Read DTO → API DTO）
# ============================================================


def to_api_run_detail(detail: RunDetail) -> WorkflowRunDetailResponse:
    """``RunDetail`` → API DTO（**无** input / request_id——显式映射）。"""
    return WorkflowRunDetailResponse(
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


def to_api_node_run(detail: NodeRunDetail) -> WorkflowNodeRunResponse:
    """``NodeRunDetail``（**安全投影**）→ API DTO。

    输入是 Read Layer 已按代白名单 / 内联边界校验过的投影（而非未经校验的
    Store Record）⇒ 损坏的 ``output``（非法 JSON / 超 32 KiB）、非法
    ``status`` / ``node_type`` / ``error_code`` 在 Read Layer 即 fail closed，
    **不会**进入 HTTP 正常响应。

    字段映射是显式逐字段的：``output`` **原样**透传（不解析 / 不截断 / 不修复
    ——校验已在上游完成）；响应字段集与既有契约一致（10 项，未增未减）。
    """
    return WorkflowNodeRunResponse(
        workflow_run_id=detail.workflow_run_id,
        node_id=detail.node_id,
        attempt=detail.attempt,
        node_type=detail.node_type,
        status=detail.status,
        output=detail.output,
        error_code=detail.error_code,
        elapsed_ms=detail.elapsed_ms,
        started_at=detail.started_at,
        completed_at=detail.completed_at,
    )


def to_api_approval(
    record: ApprovalRecord | ApprovalDetail,
) -> WorkflowApprovalResponse:
    """``ApprovalRecord`` / ``ApprovalDetail`` → API DTO（**无**创建时刻）。

    两者字段面**完全一致**（approval_id / workflow_run_id / node_id / status /
    resolved_at）⇒ 同一显式映射可服务 by-id 与 by-(run, node) 两条只读路径，
    响应契约不变。``created_at`` 不由本层补造（Store Port 未暴露）。
    """
    return WorkflowApprovalResponse(
        approval_id=record.approval_id,
        workflow_run_id=record.workflow_run_id,
        node_id=record.node_id,
        status=record.status,
        resolved_at=record.resolved_at,
    )


def to_api_event(event: TimelineEvent) -> WorkflowTimelineEventResponse:
    """``TimelineEvent`` → API DTO（顺序 / 时间戳原样，不重排、不伪造）。"""
    return WorkflowTimelineEventResponse(
        kind=event.kind,
        timestamp=event.timestamp,
        workflow_run_id=event.workflow_run_id,
        node_id=event.node_id,
        attempt=event.attempt,
        approval_id=event.approval_id,
    )


# ============================================================
# 内部辅助（参数前置校验 / 错误映射——固定文案）
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


def _map_read_error(exc: BaseException) -> HTTPException:
    """WorkflowReadError（非 NotFound）⇒ 502（数据源不可用 / 数据损坏）。"""
    _log_failure("workflow read", exc)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=_UNAVAILABLE_DETAIL,
    )


async def _call_read(operation: str, fn: Any, **kwargs: Any) -> Any:
    """统一调用（asyncio.to_thread；异常 ⇒ 安全映射——不吞、不伪 200）。"""
    try:
        return await asyncio.to_thread(fn, **kwargs)
    except WorkflowReadNotFoundError:
        raise
    except WorkflowReadError as exc:
        raise _map_read_error(exc) from exc
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
# 端点（**只读 + 认证**；无写方法）
# ============================================================


@router.get(
    "/workflow-runs/{run_id}",
    response_model=WorkflowRunDetailResponse,
    responses={
        200: {"description": "Workflow Run 只读详情（无 input / request_id）"},
        400: {"description": "run_id 非法（空白）"},
        404: {"description": "Run 不存在**或**不属于授权项目（不泄露存在性）"},
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {"description": "持久化读边界不可用 / 数据损坏"},
        **_AUTH_RESPONSES,
    },
)
async def get_workflow_run(
    run_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=WORKFLOW_RUN_ID_MAX_LENGTH,
            description="Workflow Run 身份（ai_ops.workflow_run.workflow_run_id）",
        ),
    ],
    scope: Annotated[
        WorkflowReadScope, Security(require_workflow_read_scope)
    ],
) -> WorkflowRunDetailResponse:
    """读取一次 Workflow Run 的**只读**详情（Query only；不推进 / 不取消）。

    授权范围仅来自服务端的已认证作用域（``scope.project_id``）。
    """
    _reject_blank("run_id", run_id)
    service = get_workflow_read_service()
    try:
        detail = await _call_read(
            "workflow run detail",
            service.get_run_detail,
            workflow_run_id=run_id,
            project_id=scope.project_id,
        )
    except WorkflowReadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_RUN_NOT_FOUND_DETAIL,
        ) from None
    return to_api_run_detail(detail)


@router.get(
    "/workflow-runs/{run_id}/nodes",
    response_model=list[WorkflowNodeRunResponse],
    responses={
        200: {"description": "该 Run 的**全部** node attempt（历史保留；执行序）"},
        400: {"description": "run_id 非法（空白）"},
        404: {"description": "Run 不存在**或**不属于授权项目（不泄露存在性）"},
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {
            "description": (
                "持久化读边界不可用 / 数据契约损坏"
                "（非法 JSON 输出 / 越白名单状态·类型·错误码；fail closed）"
            )
        },
        **_AUTH_RESPONSES,
    },
)
async def get_workflow_run_nodes(
    run_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=WORKFLOW_RUN_ID_MAX_LENGTH,
            description="Workflow Run 身份",
        ),
    ],
    scope: Annotated[
        WorkflowReadScope, Security(require_workflow_read_scope)
    ],
) -> list[WorkflowNodeRunResponse]:
    """读取该 Run 的 node attempt **全历史**（安全投影；不重建 / 不推断）。

    经 ``WorkflowReadService.get_node_run_details``（**安全投影**）而非
    ``get_node_runs``（原始 Store Record）⇒ 损坏的持久化字段（非法 JSON
    ``output`` / 超内联上限 / 越白名单 ``status`` · ``node_type`` ·
    ``error_code``）在 Read Layer 即 fail closed，映射为 **502**，
    **不会**以 200 返回损坏数据。

    同节点**全部**历史 attempt 保留（不折叠为最新一条）。
    """
    _reject_blank("run_id", run_id)
    service = get_workflow_read_service()
    try:
        details = await _call_read(
            "workflow node run details",
            service.get_node_run_details,
            workflow_run_id=run_id,
            project_id=scope.project_id,
        )
    except WorkflowReadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_RUN_NOT_FOUND_DETAIL,
        ) from None
    return [to_api_node_run(detail) for detail in details]


@router.get(
    "/workflow-runs/{run_id}/timeline",
    response_model=list[WorkflowTimelineEventResponse],
    responses={
        200: {"description": "由持久化时间戳构建的时间线（稳定排序；零推断）"},
        400: {"description": "run_id 非法（空白）"},
        404: {"description": "Run 不存在**或**不属于授权项目（不泄露存在性）"},
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {"description": "持久化读边界不可用 / 数据损坏"},
        **_AUTH_RESPONSES,
    },
)
async def get_workflow_run_timeline(
    run_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=WORKFLOW_RUN_ID_MAX_LENGTH,
            description="Workflow Run 身份",
        ),
    ],
    scope: Annotated[
        WorkflowReadScope, Security(require_workflow_read_scope)
    ],
) -> list[WorkflowTimelineEventResponse]:
    """读取该 Run 的时间线（保留 S6 稳定排序；不伪造事件 / 时间戳）。"""
    _reject_blank("run_id", run_id)
    service = get_workflow_read_service()
    try:
        events = await _call_read(
            "workflow run timeline",
            service.get_run_timeline,
            workflow_run_id=run_id,
            project_id=scope.project_id,
        )
    except WorkflowReadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_RUN_NOT_FOUND_DETAIL,
        ) from None
    return [to_api_event(event) for event in events]


@router.get(
    "/workflow-approvals/{approval_id}",
    response_model=WorkflowApprovalResponse,
    responses={
        200: {"description": "Workflow Approval 只读详情（经父 Run 校验归属）"},
        400: {"description": "approval_id 非法（空白）"},
        404: {
            "description": (
                "审批不存在**或**父 Run 不属于授权项目（不泄露存在性）"
            )
        },
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {"description": "持久化读边界不可用 / 数据损坏"},
        **_AUTH_RESPONSES,
    },
)
async def get_workflow_approval(
    approval_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=WORKFLOW_APPROVAL_ID_MAX_LENGTH,
            description="Workflow 审批身份（ai_ops.workflow_approval.approval_id）",
        ),
    ],
    scope: Annotated[
        WorkflowReadScope, Security(require_workflow_read_scope)
    ],
) -> WorkflowApprovalResponse:
    """读取单个 Workflow Approval 的**只读**详情（**不**裁决 / 不推进）。"""
    _reject_blank("approval_id", approval_id)
    service = get_workflow_read_service()
    try:
        record = await _call_read(
            "workflow approval detail",
            service.get_approval_detail,
            approval_id=approval_id,
            project_id=scope.project_id,
        )
    except WorkflowReadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_APPROVAL_NOT_FOUND_DETAIL,
        ) from None
    return to_api_approval(record)


@router.get(
    "/workflow-runs/{run_id}/approvals/{node_id}",
    response_model=WorkflowApprovalResponse,
    responses={
        200: {"description": "该 (Run, Node) 的审批详情（经父 Run 校验归属 + 项目）"},
        400: {"description": "run_id / node_id 非法（空白）"},
        404: {
            "description": (
                "Run 不存在 / 不属于授权项目 / 该节点**尚无审批行**"
                "（**故意同码同文案**——不泄露存在性）"
            )
        },
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {"description": "持久化读边界不可用 / 数据契约损坏（fail closed）"},
        **_AUTH_RESPONSES,
    },
)
async def get_workflow_run_approval(
    run_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=WORKFLOW_RUN_ID_MAX_LENGTH,
            description="Workflow Run 身份",
        ),
    ],
    node_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=WORKFLOW_NODE_ID_MAX_LENGTH,
            description="APPROVAL 节点标识（ai_ops.workflow_approval.node_id）",
        ),
    ],
    scope: Annotated[
        WorkflowReadScope, Security(require_workflow_read_scope)
    ],
) -> WorkflowApprovalResponse:
    """按 ``(run_id, node_id)`` 读取审批的**只读**详情（**不**裁决 / 不推进）。

    复用 ``WorkflowReadService.get_approval_for_node``：先经父 Run 校验存在性与
    服务端项目作用域，再校验审批行的 Run / Node 归属一致；归属不一致 ⇒ Read
    Layer fail closed（**502**）。

    ``None``（该节点尚无审批行）与"Run 不存在 / 项目不匹配"均映射为 **404**
    + 同一固定文案 —— 与 by-id 路由的既有安全约定一致（不泄露存在性），
    **不**伪造审批创建时刻（Store Port 未暴露 ``created_at``）。
    """
    _reject_blank("run_id", run_id)
    _reject_blank("node_id", node_id)
    service = get_workflow_read_service()
    try:
        approval = await _call_read(
            "workflow approval for node",
            service.get_approval_for_node,
            workflow_run_id=run_id,
            node_id=node_id,
            project_id=scope.project_id,
        )
    except WorkflowReadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_APPROVAL_NOT_FOUND_DETAIL,
        ) from None
    if approval is None:
        # 尚无审批行 = 合法事实；与"不存在 / 跨项目"同构 ⇒ 同码同文案
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_APPROVAL_NOT_FOUND_DETAIL,
        )
    return to_api_approval(approval)


__all__ = [
    "router",
    "get_workflow_read_service",
    "to_api_run_detail",
    "to_api_node_run",
    "to_api_approval",
    "to_api_event",
    "WORKFLOW_RUN_ID_MAX_LENGTH",
    "WORKFLOW_NODE_ID_MAX_LENGTH",
    "WORKFLOW_APPROVAL_ID_MAX_LENGTH",
]
