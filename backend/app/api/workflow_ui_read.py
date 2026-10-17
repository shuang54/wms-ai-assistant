"""Workflow UI Read API（Phase 7 Workflow UI Step 2）——**浏览器**只读边界。

与既有服务间读面（``workflow_read.py``，``X-API-Key``）**并存不替代**，
两者复用**同一个** :class:`WorkflowReadService` 与**同一套**响应 DTO：

```text
HTTP GET /api/ui/projects/{project_id}/workflow-runs/{run_id}
HTTP GET /api/ui/projects/{project_id}/workflow-runs/{run_id}/nodes
HTTP GET /api/ui/projects/{project_id}/workflow-runs/{run_id}/timeline
HTTP GET /api/ui/projects/{project_id}/workflow-runs/{run_id}/approvals/{node_id}
HTTP GET /api/ui/projects/{project_id}/workflow-approvals/{approval_id}
        ↓  M1 会话 Cookie（require_session）          401 / 503
Principal（user_id 由服务端会话签发；客户端不可主张）
        ↓  M2.4 项目成员授权（enforce_project_access）  403 / 503
        ↓  服务端再校验 run.project_id == 路径 project_id
WorkflowReadService（只读；经 Store Ports）
        ↓
Store Adapters → Repositories → PostgreSQL
```

授权判定链（**每一跳都必须通过**，见 Phase 7 Workflow UI Step 2 §四）：

    1. 会话认证（401 / 503）；
    2. **项目成员授权**——路径中的 ``project_id`` 只是**请求目标**，
       **不**构成授权结论；授权结论来自 ``ai_ops.authorization_grant``
       中该 ``(user_id, project_id)`` 的显式成员授权（403 / 503）；
    3. ``WorkflowReadService`` 校验 ``run.project_id`` 与请求项目一致
       （不一致 ⇒ 与"不存在"**同构**的 404 —— 不泄漏跨项目资源是否存在）；
    4. 子记录（Node / Approval）归属由 Read Service 校验（跨 Run / 跨节点
       ⇒ fail closed）。

安全纪律（**与既有读面完全一致**）：

    * **只读**：不调用 Runtime 的 start / advance / request_cancel /
      submit_approval_decision；不调用 Tool / LLM / Orchestrator；无写入路径；
    * 不接触 ORM / Repository / Session / SQLAlchemy——只调用
      ``WorkflowReadService``（装配在 ``workflow_runtime.read_factory``）；
    * 不返回 ORM 对象 / Session / Connection / ``input`` / 凭证 / 未筛选 metadata；
    * 客户端传入的 ``project_id`` **不**被记录为已验证身份或授权结论；
    * 错误文案固定（**不**含异常正文 / SQL / 凭据 / 内部路径）；
    * 同步 Read Service 经 ``asyncio.to_thread`` 调用（不在事件循环内阻塞）。

响应 DTO 与转换器**直接复用** ``workflow_read`` 的公开实现（同一映射、
同一字段集、同一过滤边界）——避免两套投影产生分歧。

端点错误契约：

```text
400  路径参数非法（空白）
401  缺少 / 无效 / 过期 / 已吊销会话（固定文案；与资源无关）
403  已认证但**不是**该项目的成员（固定文案；不泄漏项目 / 资源存在性）
404  Run / Approval 不存在 **或** 不属于请求项目（**故意同码同文案**）
422  路径参数长度越界（FastAPI Path 校验）
502  持久化读边界不可用 / 数据契约损坏（不暴露内部细节；不尝试修复）
503  会话服务 / 授权存储不可用（fail closed）
500  其它未预期错误（不暴露 traceback / SQL / 凭据）
```

CSRF：本模块**只有 GET**（安全方法）⇒ 不需要 CSRF 校验（M1 的
``require_csrf`` 语义亦不强制 GET）。本阶段**不**新增任何写接口。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, status

from backend.app.api.auth_dependency import CurrentPrincipal
from backend.app.api.authorization_dependency import enforce_project_access
from backend.app.api.workflow_read import (
    WORKFLOW_APPROVAL_ID_MAX_LENGTH,
    WORKFLOW_NODE_ID_MAX_LENGTH,
    WORKFLOW_RUN_ID_MAX_LENGTH,
    to_api_approval,
    to_api_event,
    to_api_node_run,
    to_api_run_detail,
)
from backend.app.dto.workflow_graph_api import (
    WORKFLOW_GRAPH_STATUS_AVAILABLE,
    WORKFLOW_GRAPH_STATUS_NOT_REGISTERED,
    WORKFLOW_GRAPH_STATUS_VERSION_MISMATCH,
    WorkflowGraphNodeResponse,
    WorkflowGraphResponse,
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
from backend.app.workflow.errors import (
    WorkflowDefinitionNotFoundError,
)
from backend.app.workflow_runtime.read_service import (
    WorkflowReadError,
    WorkflowReadNotFoundError,
    WorkflowReadService,
)

logger = logging.getLogger(__name__)
router = APIRouter()

#: 路径参数长度边界（与 db 模型列宽一致：project_id 128 / run_id 64 /
#: node_id 64 / approval_id 128；API 测试锁定一致性）。
PROJECT_ID_MAX_LENGTH: int = 128

#: 固定错误文案（与 ``workflow_read`` 的服务间读面**保持一致**；
#: 由 ``tests/test_workflow_ui_read_api.py`` 断言两处逐字相同以防漂移）。
RUN_NOT_FOUND_DETAIL: str = "Workflow Run 不存在"
APPROVAL_NOT_FOUND_DETAIL: str = "Workflow 审批不存在"
UNAVAILABLE_DETAIL: str = "Workflow 读取数据源不可用"


def get_workflow_ui_read_service() -> WorkflowReadService:
    """Composition Root：装配只读服务（测试以 monkeypatch 替换本函数）。"""
    return create_workflow_read_service()


def get_workflow_definition_registry() -> Any:
    """Composition Root：生产 Workflow 定义注册表（与命令 API **同一实例**）。

    延迟 import：保持 API 层与 runtime 装配的弱耦合（沿既有惯例）；
    测试以 monkeypatch 替换本函数注入自有注册表。
    """
    from backend.app.workflow_runtime.command_service import (
        get_workflow_registry,
    )

    return get_workflow_registry()


# ============================================================
# Graph View 拓扑解析（Phase 7 Step 4）
# ============================================================


def _resolve_run_graph(
    workflow_id: str, workflow_version: str
) -> WorkflowGraphResponse:
    """按 Run 持久化的 ``(workflow_id, workflow_version)`` 解析**真实拓扑**。

    Fail-closed 语义（**不**用其它版本的定义冒充当前 Run 的拓扑）：

    ```text
    已注册且版本一致            ⇒ available（含节点 + 边）
    未注册                     ⇒ not_registered（nodes 为空）
    已注册但版本与 Run 不一致   ⇒ version_mismatch（nodes 为空）
    ```

    Raises:
        HTTPException 500: 注册表读取未预期失败（固定文案）。
    """
    try:
        registry = get_workflow_definition_registry()
        current = registry.resolve_current(workflow_id)
    except WorkflowDefinitionNotFoundError:
        return WorkflowGraphResponse(
            status=WORKFLOW_GRAPH_STATUS_NOT_REGISTERED,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            definition_version=None,
            start_node_id=None,
            nodes=[],
        )
    except Exception as exc:  # noqa: BLE001 —— 固定文案，不暴露内部细节
        _log_failure("workflow graph registry", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=UNAVAILABLE_DETAIL,
        ) from exc

    definition_version = current.version
    if definition_version != workflow_version:
        # Run 的版本与当前注册版本不一致 ⇒ **不**展示可能已变化的拓扑
        return WorkflowGraphResponse(
            status=WORKFLOW_GRAPH_STATUS_VERSION_MISMATCH,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            definition_version=definition_version,
            start_node_id=None,
            nodes=[],
        )

    return WorkflowGraphResponse(
        status=WORKFLOW_GRAPH_STATUS_AVAILABLE,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        definition_version=definition_version,
        start_node_id=current.start_node_id,
        nodes=[
            WorkflowGraphNodeResponse(
                node_id=node.node_id,
                node_type=node.node_type,
                next_node_id=node.next_node_id,
                on_true_node_id=node.on_true_node_id,
                on_false_node_id=node.on_false_node_id,
            )
            for node in current.nodes
        ],
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


async def _call_read(operation: str, fn: Any, **kwargs: Any) -> Any:
    """统一调用（asyncio.to_thread；异常 ⇒ 安全映射——不吞、不伪 200）。"""
    try:
        return await asyncio.to_thread(fn, **kwargs)
    except WorkflowReadNotFoundError:
        raise
    except WorkflowReadError as exc:
        _log_failure(operation, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=UNAVAILABLE_DETAIL,
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 —— 不暴露 traceback / 内部路径
        _log_failure(operation, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=UNAVAILABLE_DETAIL,
        ) from exc


def _require_project_membership(
    principal: CurrentPrincipal, project_id: str
) -> str:
    """**授权结论**：该用户必须是 ``project_id`` 的成员（MEMBER / PROJECT_OPS）。

    ``project_id`` 来自路径（= 请求目标）；本函数把它交给 M2.4 的**同一份**
    授权决策实现（``enforce_project_access``）去判定——**不**因为客户端
    提供了项目 ID 就视为已授权。

    Returns:
        规范化后的 ``project_id``（trim 后；后续服务端校验与查询使用）。

    Raises:
        HTTPException 403: 不是该项目成员（固定文案）。
        HTTPException 503: 授权存储不可用（fail closed）。
    """
    _reject_blank("project_id", project_id)
    scope = project_id.strip()
    enforce_project_access(principal.user_id, scope, ops_only=False)
    return scope


#: 五个端点共享的 responses 片段（认证 / 授权错误声明）。
_AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "description": (
            "缺少或无效的会话 Cookie（固定文案；不泄露目标资源是否存在）"
        )
    },
    403: {
        "description": (
            "已认证但**不是**该项目成员（M2.4 项目授权；固定文案）"
        )
    },
    503: {
        "description": (
            "会话服务 / 授权存储不可用，或授权作用域未配置（fail closed）"
        )
    },
}

#: 路径参数声明（复用同一组定义，避免各端点漂移）。
_ProjectId = Annotated[
    str,
    Path(
        min_length=1,
        max_length=PROJECT_ID_MAX_LENGTH,
        description="项目作用域（**请求目标**；授权结论来自服务端授权表）",
    ),
]
_RunId = Annotated[
    str,
    Path(
        min_length=1,
        max_length=WORKFLOW_RUN_ID_MAX_LENGTH,
        description="Workflow Run 身份（ai_ops.workflow_run.workflow_run_id）",
    ),
]
_NodeId = Annotated[
    str,
    Path(
        min_length=1,
        max_length=WORKFLOW_NODE_ID_MAX_LENGTH,
        description="节点标识（ai_ops.workflow_node_run.node_id）",
    ),
]
_ApprovalId = Annotated[
    str,
    Path(
        min_length=1,
        max_length=WORKFLOW_APPROVAL_ID_MAX_LENGTH,
        description="Workflow 审批身份（ai_ops.workflow_approval.approval_id）",
    ),
]


# ============================================================
# 端点（**只读 + 会话认证 + 项目成员授权**；无写方法）
# ============================================================


@router.get(
    "/ui/projects/{project_id}/workflow-runs/{run_id}",
    response_model=WorkflowRunDetailResponse,
    responses={
        200: {"description": "Run 只读详情（无 input / request_id）"},
        400: {"description": "project_id / run_id 非法（空白）"},
        404: {
            "description": (
                "Run 不存在**或**不属于请求项目（不泄露存在性）"
            )
        },
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {"description": "持久化读边界不可用 / 数据损坏"},
        **_AUTH_RESPONSES,
    },
)
async def get_ui_workflow_run(
    project_id: _ProjectId,
    run_id: _RunId,
    principal: CurrentPrincipal,
) -> WorkflowRunDetailResponse:
    """读取一次 Workflow Run 的**只读**详情（浏览器会话 + 项目成员授权）。"""
    scope = _require_project_membership(principal, project_id)
    _reject_blank("run_id", run_id)
    service = get_workflow_ui_read_service()
    try:
        detail = await _call_read(
            "workflow ui run detail",
            service.get_run_detail,
            workflow_run_id=run_id,
            project_id=scope,
        )
    except WorkflowReadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=RUN_NOT_FOUND_DETAIL,
        ) from None
    return to_api_run_detail(detail)


@router.get(
    "/ui/projects/{project_id}/workflow-runs/{run_id}/nodes",
    response_model=list[WorkflowNodeRunResponse],
    responses={
        200: {"description": "该 Run 的**全部** node attempt（安全投影；执行序）"},
        400: {"description": "project_id / run_id 非法（空白）"},
        404: {"description": "Run 不存在**或**不属于请求项目（不泄露存在性）"},
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
async def get_ui_workflow_run_nodes(
    project_id: _ProjectId,
    run_id: _RunId,
    principal: CurrentPrincipal,
) -> list[WorkflowNodeRunResponse]:
    """读取该 Run 的 node attempt **全历史**（安全投影；不重建未持久化状态）。"""
    scope = _require_project_membership(principal, project_id)
    _reject_blank("run_id", run_id)
    service = get_workflow_ui_read_service()
    try:
        details = await _call_read(
            "workflow ui node run details",
            service.get_node_run_details,
            workflow_run_id=run_id,
            project_id=scope,
        )
    except WorkflowReadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=RUN_NOT_FOUND_DETAIL,
        ) from None
    return [to_api_node_run(detail) for detail in details]


@router.get(
    "/ui/projects/{project_id}/workflow-runs/{run_id}/timeline",
    response_model=list[WorkflowTimelineEventResponse],
    responses={
        200: {"description": "由持久化时间戳构建的时间线（稳定排序；零推断）"},
        400: {"description": "project_id / run_id 非法（空白）"},
        404: {"description": "Run 不存在**或**不属于请求项目（不泄露存在性）"},
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {"description": "持久化读边界不可用 / 数据损坏"},
        **_AUTH_RESPONSES,
    },
)
async def get_ui_workflow_run_timeline(
    project_id: _ProjectId,
    run_id: _RunId,
    principal: CurrentPrincipal,
) -> list[WorkflowTimelineEventResponse]:
    """读取该 Run 的时间线（保留后端稳定排序；不伪造事件 / 时间戳）。"""
    scope = _require_project_membership(principal, project_id)
    _reject_blank("run_id", run_id)
    service = get_workflow_ui_read_service()
    try:
        events = await _call_read(
            "workflow ui run timeline",
            service.get_run_timeline,
            workflow_run_id=run_id,
            project_id=scope,
        )
    except WorkflowReadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=RUN_NOT_FOUND_DETAIL,
        ) from None
    return [to_api_event(event) for event in events]


@router.get(
    "/ui/projects/{project_id}/workflow-runs/{run_id}/approvals/{node_id}",
    response_model=WorkflowApprovalResponse,
    responses={
        200: {"description": "该 (Run, Node) 的审批详情（经父 Run 校验归属 + 项目）"},
        400: {"description": "project_id / run_id / node_id 非法（空白）"},
        404: {
            "description": (
                "Run 不存在 / 不属于请求项目 / 该节点**尚无审批行**"
                "（**故意同码同文案**——不泄露存在性）"
            )
        },
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {"description": "持久化读边界不可用 / 数据契约损坏（fail closed）"},
        **_AUTH_RESPONSES,
    },
)
async def get_ui_workflow_run_approval(
    project_id: _ProjectId,
    run_id: _RunId,
    node_id: _NodeId,
    principal: CurrentPrincipal,
) -> WorkflowApprovalResponse:
    """按 ``(run_id, node_id)`` 读取审批的**只读**详情（**不**裁决 / 不推进）。"""
    scope = _require_project_membership(principal, project_id)
    _reject_blank("run_id", run_id)
    _reject_blank("node_id", node_id)
    service = get_workflow_ui_read_service()
    try:
        approval = await _call_read(
            "workflow ui approval for node",
            service.get_approval_for_node,
            workflow_run_id=run_id,
            node_id=node_id,
            project_id=scope,
        )
    except WorkflowReadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=APPROVAL_NOT_FOUND_DETAIL,
        ) from None
    if approval is None:
        # 尚无审批行 = 合法事实；与"不存在 / 跨项目"同构 ⇒ 同码同文案
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=APPROVAL_NOT_FOUND_DETAIL,
        )
    return to_api_approval(approval)


@router.get(
    "/ui/projects/{project_id}/workflow-approvals/{approval_id}",
    response_model=WorkflowApprovalResponse,
    responses={
        200: {"description": "Workflow Approval 只读详情（经父 Run 校验归属）"},
        400: {"description": "project_id / approval_id 非法（空白）"},
        404: {
            "description": (
                "审批不存在**或**父 Run 不属于请求项目（不泄露存在性）"
            )
        },
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {"description": "持久化读边界不可用 / 数据损坏"},
        **_AUTH_RESPONSES,
    },
)
async def get_ui_workflow_approval(
    project_id: _ProjectId,
    approval_id: _ApprovalId,
    principal: CurrentPrincipal,
) -> WorkflowApprovalResponse:
    """读取单个 Workflow Approval 的**只读**详情（**不**裁决 / 不推进）。"""
    scope = _require_project_membership(principal, project_id)
    _reject_blank("approval_id", approval_id)
    service = get_workflow_ui_read_service()
    try:
        record = await _call_read(
            "workflow ui approval detail",
            service.get_approval_detail,
            approval_id=approval_id,
            project_id=scope,
        )
    except WorkflowReadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=APPROVAL_NOT_FOUND_DETAIL,
        ) from None
    return to_api_approval(record)


@router.get(
    "/ui/projects/{project_id}/workflow-runs/{run_id}/graph",
    response_model=WorkflowGraphResponse,
    responses={
        200: {
            "description": (
                "该 Run 的**只读**工作流拓扑（status=available 时含节点与边；"
                "not_registered / version_mismatch 时 nodes 为空——不画边）"
            )
        },
        400: {"description": "project_id / run_id 非法（空白）"},
        404: {"description": "Run 不存在**或**不属于请求项目（不泄露存在性）"},
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {"description": "持久化读边界不可用 / 数据损坏"},
        500: {"description": "定义注册表读取未预期失败（固定文案）"},
        **_AUTH_RESPONSES,
    },
)
async def get_ui_workflow_run_graph(
    project_id: _ProjectId,
    run_id: _RunId,
    principal: CurrentPrincipal,
) -> WorkflowGraphResponse:
    """读取该 Run 的**只读**工作流拓扑（Graph View 数据源）。

    链路：会话认证 → 项目成员授权 → 读 Run（项目隔离）→ 按 Run 持久化的
    ``(workflow_id, workflow_version)`` 解析服务器端 :class:`WorkflowDefinition`。

    **拓扑真实性保证**（任务书 §四.2）：

    * 边**只**来自定义（构造即经 DAG 校验：无环 / 全节点自 start 可达 /
      边引用必存在）——**绝不**按 attempt 的先后顺序推断；
    * 版本不匹配 ⇒ ``version_mismatch`` 且 ``nodes`` 为空 ⇒ 前端**不**画边
      （不用当前版本拓扑冒充历史版本的拓扑）；
    * 未注册 ⇒ ``not_registered`` 且 ``nodes`` 为空；
    * **不**返回模板 / 参数映射 / 审批文案 / tool 目标（只暴露图形必需字段）。

    本端点只读；不触发任何 Runtime 执行入口。
    """
    scope = _require_project_membership(principal, project_id)
    _reject_blank("run_id", run_id)
    service = get_workflow_ui_read_service()
    try:
        detail = await _call_read(
            "workflow ui run graph",
            service.get_run_detail,
            workflow_run_id=run_id,
            project_id=scope,
        )
    except WorkflowReadNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=RUN_NOT_FOUND_DETAIL,
        ) from None
    return _resolve_run_graph(detail.workflow_id, detail.workflow_version)


__all__ = [
    "router",
    "get_workflow_ui_read_service",
    "get_workflow_definition_registry",
    "PROJECT_ID_MAX_LENGTH",
    "RUN_NOT_FOUND_DETAIL",
    "APPROVAL_NOT_FOUND_DETAIL",
    "UNAVAILABLE_DETAIL",
]
