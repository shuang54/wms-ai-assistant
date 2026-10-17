"""Workflow Graph View 响应 DTO（Phase 7 Step 4；**只读拓扑**）。

设计约束（对应任务书 §四.2 "只有存在真实依赖关系时才绘制连接线"）：

* 拓扑**只**来自服务器端 :class:`WorkflowDefinition`（构造即经 DAG 校验：
  无环、全节点自 start 可达、边引用必存在）——**不**来自 attempt 的时间顺序；
* **不**返回模板 / 参数映射 / 审批文案 / tool 目标（与
  ``WorkflowDefinitionResponse`` 的既有边界一致：只暴露图形必需字段）；
* 无法可靠给出拓扑时（定义未注册 / 版本不匹配）⇒ ``status`` 明确标注，
  ``nodes`` 为空 ⇒ 前端**不**画边（fail-closed，不用可视化误导用户）。
"""
from __future__ import annotations

from typing import Final

from pydantic import BaseModel, Field

__all__ = [
    "WORKFLOW_GRAPH_NODE_RESPONSE_FIELDS",
    "WORKFLOW_GRAPH_RESPONSE_FIELDS",
    "WORKFLOW_GRAPH_STATUS_AVAILABLE",
    "WORKFLOW_GRAPH_STATUS_NOT_REGISTERED",
    "WORKFLOW_GRAPH_STATUS_VERSION_MISMATCH",
    "WORKFLOW_GRAPH_STATUSES",
    "WorkflowGraphNodeResponse",
    "WorkflowGraphResponse",
]

#: 拓扑可用（定义已注册且版本与 Run 持久化版本一致）。
WORKFLOW_GRAPH_STATUS_AVAILABLE: Final[str] = "available"

#: 该 workflow_id 未在服务器端注册表注册（无定义可用）。
WORKFLOW_GRAPH_STATUS_NOT_REGISTERED: Final[str] = "not_registered"

#: 定义已注册，但其**当前版本**与 Run 持久化的 ``workflow_version`` 不一致。
#: 此时**不得**用当前版本拓扑代替历史版本（fail-closed）。
WORKFLOW_GRAPH_STATUS_VERSION_MISMATCH: Final[str] = "version_mismatch"

#: 全部合法状态（前端据此分支；未知状态按"不可用"处理）。
WORKFLOW_GRAPH_STATUSES: Final[tuple[str, ...]] = (
    WORKFLOW_GRAPH_STATUS_AVAILABLE,
    WORKFLOW_GRAPH_STATUS_NOT_REGISTERED,
    WORKFLOW_GRAPH_STATUS_VERSION_MISMATCH,
)

#: 单节点拓扑字段（顺序即契约；不含任何执行状态——状态由 attempt 数据派生）。
WORKFLOW_GRAPH_NODE_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "node_id",
    "node_type",
    "next_node_id",
    "on_true_node_id",
    "on_false_node_id",
)

#: 顶层字段（顺序即契约）。
WORKFLOW_GRAPH_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "status",
    "workflow_id",
    "workflow_version",
    "definition_version",
    "start_node_id",
    "nodes",
)


class WorkflowGraphNodeResponse(BaseModel):
    """单个节点的**静态拓扑**（来自 WorkflowDefinition；无执行状态）。"""

    node_id: str = Field(description="节点标识（definition 内唯一）")
    node_type: str = Field(description="节点类型（NODE_TYPES 之一）")
    next_node_id: str | None = Field(
        default=None, description="顺序边目标（None = 该分支终点）"
    )
    on_true_node_id: str | None = Field(
        default=None, description="CONDITION 真分支目标（否则 None）"
    )
    on_false_node_id: str | None = Field(
        default=None, description="CONDITION 假分支目标（否则 None）"
    )


class WorkflowGraphResponse(BaseModel):
    """Run 的工作流拓扑（只读；可能不可用）。"""

    status: str = Field(
        description=(
            "available / not_registered / version_mismatch；"
            "非 available 时 nodes 为空（前端不得画边）"
        )
    )
    workflow_id: str = Field(description="Run 持久化的 workflow_id")
    workflow_version: str = Field(description="Run 持久化的 workflow_version")
    definition_version: str | None = Field(
        default=None,
        description="注册表中该 workflow 的当前版本（未注册 ⇒ None）",
    )
    start_node_id: str | None = Field(
        default=None, description="起始节点（拓扑不可用 ⇒ None）"
    )
    nodes: list[WorkflowGraphNodeResponse] = Field(
        default_factory=list,
        description="拓扑节点（拓扑不可用 ⇒ 空列表）",
    )
