"""Workflow Read API 响应 DTO（Phase 6 Step 7 · S7）。

纪律（S7 §七）：
    * Pydantic v2 显式逐字段声明（**不**用 vars / asdict / model_dump）；
      ``response_model`` 同时充当字段过滤边界；
    * **不**含 Workflow 启动输入 ``input`` / ``request_id`` / ORM / Session /
      Connection / 未筛选 metadata / 异常正文；
    * ``*_FIELDS`` 常量（顺序 ≡ 声明）供 API 测试做**精确字段集**断言
      （沿 ``agent_read_api`` 先例）；
    * 已知限制（S6）：``ApprovalRecord`` 无 ``created_at`` ⇒ 审批创建时刻
      **不伪造**（DTO 无该字段）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from pydantic import BaseModel, Field

__all__ = [
    "WORKFLOW_RUN_DETAIL_RESPONSE_FIELDS",
    "WORKFLOW_NODE_RUN_RESPONSE_FIELDS",
    "WORKFLOW_APPROVAL_RESPONSE_FIELDS",
    "WORKFLOW_TIMELINE_EVENT_RESPONSE_FIELDS",
    "WORKFLOW_DEFINITION_NODE_RESPONSE_FIELDS",
    "WORKFLOW_DEFINITION_RESPONSE_FIELDS",
    "WORKFLOW_DEFINITION_CATALOG_RESPONSE_FIELDS",
    "WorkflowRunDetailResponse",
    "WorkflowNodeRunResponse",
    "WorkflowApprovalResponse",
    "WorkflowTimelineEventResponse",
    "WorkflowDefinitionNodeResponse",
    "WorkflowDefinitionResponse",
    "WorkflowDefinitionCatalogResponse",
]

#: ``GET /api/workflow-runs/{run_id}`` 顶层字段（顺序 ≡ 声明）。
WORKFLOW_RUN_DETAIL_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "workflow_run_id",
    "workflow_id",
    "workflow_version",
    "project_id",
    "status",
    "current_node_id",
    "next_action",
    "state_version",
    "attempt_count",
    "cancel_requested",
    "cancel_requested_at",
    "created_at",
    "started_at",
    "completed_at",
    "updated_at",
)

#: ``GET /api/workflow-runs/{run_id}/nodes`` 元素字段（顺序 ≡ 声明）。
WORKFLOW_NODE_RUN_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "workflow_run_id",
    "node_id",
    "attempt",
    "node_type",
    "status",
    "output",
    "error_code",
    "elapsed_ms",
    "started_at",
    "completed_at",
)

#: ``GET /api/workflow-approvals/{approval_id}`` 顶层字段（顺序 ≡ 声明）。
WORKFLOW_APPROVAL_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "approval_id",
    "workflow_run_id",
    "node_id",
    "status",
    "resolved_at",
)

#: ``GET /api/workflow-runs/{run_id}/timeline`` 元素字段（顺序 ≡ 声明）。
WORKFLOW_TIMELINE_EVENT_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "kind",
    "timestamp",
    "workflow_run_id",
    "node_id",
    "attempt",
    "approval_id",
)


class WorkflowRunDetailResponse(BaseModel):
    """Run 只读详情（**无** ``input`` / ``request_id``）。

    字段全部来自真实 run 行（Read Service ``RunDetail`` 的显式投影）；
    时间戳可 ``null``（**不伪造**）。
    """

    workflow_run_id: str = Field(description="Workflow Run 身份")
    workflow_id: str = Field(description="Definition 逻辑身份（name）")
    workflow_version: str = Field(description="持久化 definition 版本")
    project_id: str = Field(description="项目作用域（服务器端解析结果）")
    status: str = Field(description="域白名单状态（PENDING/RUNNING/WAITING/终态）")
    current_node_id: str | None = Field(
        default=None, description="当前/下一节点指针（无 ⇒ null）"
    )
    next_action: str = Field(description="控制推进语义（NONE/NODE_EXECUTION/…）")
    state_version: int = Field(description="控制状态版本（CAS 基线）")
    attempt_count: int = Field(description="run 行持久化的累计 attempt 数")
    cancel_requested: bool = Field(description="协作式取消信号")
    cancel_requested_at: datetime | None = Field(default=None)
    created_at: datetime | None = Field(default=None)
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    updated_at: datetime | None = Field(default=None)


class WorkflowNodeRunResponse(BaseModel):
    """Node attempt 只读记录（**全历史**；不重建未持久化状态）。

    ``output`` = 已持久化 JSON 文本**原样**（不解析 / 不截断 / 不修复）；
    ``error_code`` = 稳定码白名单值（无 ⇒ null）。
    """

    workflow_run_id: str
    node_id: str = Field(description="节点标识")
    attempt: int = Field(description="attempt 序号（同节点历史保留）")
    node_type: str = Field(description="AI / TOOL / CONDITION / APPROVAL")
    status: str = Field(description="attempt 状态（RUNNING/WAITING_APPROVAL/终态）")
    output: str | None = Field(
        default=None, description="已持久化 JSON 文本（原样）"
    )
    error_code: str | None = Field(default=None)
    elapsed_ms: float | None = Field(default=None)
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)


class WorkflowApprovalResponse(BaseModel):
    """Workflow Approval 只读详情（**无**创建时刻——S6 已知限制，不伪造）。"""

    approval_id: str
    workflow_run_id: str
    node_id: str
    status: str = Field(description="PENDING / APPROVED / REJECTED")
    resolved_at: datetime | None = Field(
        default=None, description="裁决时间（PENDING ⇒ null）"
    )


class WorkflowTimelineEventResponse(BaseModel):
    """时间线事件（来自**真实持久化时间戳**；零推断 / 零伪造）。"""

    kind: str = Field(
        description=(
            "RUN_CREATED / RUN_STARTED / RUN_FINISHED / "
            "NODE_ATTEMPT_STARTED / NODE_ATTEMPT_FINISHED / APPROVAL_RESOLVED"
        )
    )
    timestamp: datetime
    workflow_run_id: str
    node_id: str | None = Field(default=None)
    attempt: int | None = Field(default=None)
    approval_id: str | None = Field(default=None)


# ============================================================
# Workflow 定义目录（Phase 7 Step 7）—— 只读发现面
# ============================================================

#: 定义目录中节点概要字段（顺序 ≡ 声明）。
WORKFLOW_DEFINITION_NODE_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "node_id",
    "node_type",
)

#: ``GET /api/workflow-definitions`` 元素字段（顺序 ≡ 声明）。
WORKFLOW_DEFINITION_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "name",
    "version",
    "start_node_id",
    "input_fields",
    "nodes",
)

#: 定义目录响应顶层字段（顺序 ≡ 声明）。
WORKFLOW_DEFINITION_CATALOG_RESPONSE_FIELDS: Final[tuple[str, ...]] = ("items",)


class WorkflowDefinitionNodeResponse(BaseModel):
    """定义中的节点**概要**（**无** 模板 / 参数映射 / 审批文案）。

    只暴露"图里有哪些节点、什么类型"这两项启动前必需信息；
    AI 模板、TOOL 参数映射、审批展示文案属服务器端定义内部，
    **不**通过本 API 暴露。
    """

    node_id: str
    node_type: str = Field(description="AI / TOOL / CONDITION / APPROVAL")


class WorkflowDefinitionResponse(BaseModel):
    """一个已注册 Workflow 定义的可发现信息（**只读**；服务器端静态定义）。"""

    name: str = Field(description="definition 名称（start 的 workflow_id）")
    version: str = Field(description="当前注册版本（Resume 按持久化 version 解析）")
    start_node_id: str
    input_fields: list[str] = Field(
        description=(
            "start input JSON 需要提供的顶层字段"
            "（由定义中的受限引用 input.<path> 推导；无 ⇒ 空列表）"
        )
    )
    nodes: list[WorkflowDefinitionNodeResponse]


class WorkflowDefinitionCatalogResponse(BaseModel):
    """``GET /api/workflow-definitions``：已注册定义列表（可能为空）。"""

    items: list[WorkflowDefinitionResponse] = Field(default_factory=list)
