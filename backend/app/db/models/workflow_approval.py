"""Workflow Approval ORM Model（Phase 6 Step 2 — Workflow Persistence）。

```text
ai_ops.workflow_approval      一行 = 一个 Workflow Approval Gate 的审批请求
```

设计依据（已冻结，不得就地重设计）：
    `Phase 6 — Workflow Runtime Architecture Decision.md`（ACCEPTED）§14 / §16
    `Phase 6 — Workflow Runtime Architecture Discovery.md`（FROZEN）§13

职责边界（严格）：

    * 只回答一个问题：**人类是否允许该 Workflow Node（Approval Gate）继续**；
    * 与 **Agent Tool Approval（``agent_approval``，FROZEN）完全分离**：
      不复用表 / 不复用代码路径；仅是"条件 UPDATE 单赢"的实现模式对齐；
    * 只承载 **身份 + 状态 + 时间**：approval_id / workflow_run_id / node_id /
      **project_id（冗余携带）** / status / resolved_at / 时间戳（**不**凭空增加
      resolved_by / 自由文本 reason —— 沿 agent_approval 先例：无身份体系 /
      无自由文本）；
    * ``project_id`` 为 **ADR §16 I1/I4 冗余列**（F-1 对齐）：创建时由 Repository
      从所属 ``workflow_run`` 解析复制（**不信任调用方传入**；非 FK；
      类型 / 长度 / 非空与 run 定义一致）；
    * 状态白名单：PENDING → APPROVED | REJECTED（终态不可逆）；
      一致性约束（PENDING ⇒ resolved_at NULL；终态 ⇒ resolved_at 非 NULL）
      由 Repository（写入路径硬编码）+ DB tests 锁定（对齐 agent_approval 先例，
      不引入 CHECK 约束 —— 项目无 CHECK 先例）；
    * **域内 FK**：``workflow_run_id → ai_ops.workflow_run.workflow_run_id``
      （ON DELETE CASCADE —— 沿 ``conversation_turn → conversation`` 先例）。

字段对齐说明（Step 1 已冻结 Domain DTO ``WorkflowApprovalRecord``）：
    approval_id / workflow_run_id / node_id / status / resolved_at → 一一对应；
    Domain DTO 无 project_id —— 该列为 ADR §16 I1/I4 要求的**安全冗余列**
    （持久化层携带，用于读侧独立归属证明；非 DTO 字段映射）。

唯一身份：
    * ``UNIQUE(approval_id)``               —— 审批身份（Runtime 签发）；
    * ``UNIQUE(workflow_run_id, node_id)``  —— 一个 Node 的审批身份最多一个
      （重复请求幂等返回同一行；execution identity 对齐）。

白名单来源：``backend.app.workflow.states``（域层；单向依赖，无环）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.workflow_run import (
    WORKFLOW_RUN_PROJECT_ID_MAX_LENGTH,
    WORKFLOW_RUN_SCHEMA,
    WORKFLOW_RUN_TABLE,
)
from backend.app.workflow.states import APPROVAL_STATUSES

__all__ = [
    "WorkflowApproval",
    "WORKFLOW_APPROVAL_SCHEMA",
    "WORKFLOW_APPROVAL_TABLE",
    "WORKFLOW_APPROVAL_ID_INDEX",
    "WORKFLOW_APPROVAL_IDENTITY_INDEX",
    "WORKFLOW_APPROVAL_RUN_ID_INDEX",
    "WORKFLOW_APPROVAL_PROJECT_ID_MAX_LENGTH",
    "WORKFLOW_APPROVAL_ID_MAX_LENGTH",
    "WORKFLOW_APPROVAL_RUN_ID_MAX_LENGTH",
    "WORKFLOW_APPROVAL_NODE_ID_MAX_LENGTH",
    "WORKFLOW_APPROVAL_STATUS_MAX_LENGTH",
    "WORKFLOW_APPROVAL_STATUSES",
]

#: AI 内部运维 schema（与 workflow_run 同轴）。
WORKFLOW_APPROVAL_SCHEMA: Final[str] = WORKFLOW_RUN_SCHEMA

#: 表名常量（DB 测试 / 残留守卫共用）。
WORKFLOW_APPROVAL_TABLE: Final[str] = "workflow_approval"

#: 审批身份唯一索引（approval_id）。
WORKFLOW_APPROVAL_ID_INDEX: Final[str] = "uq_workflow_approval_approval_id"

#: Node 审批身份唯一索引（一个 Node 最多一个审批；重复请求幂等）。
WORKFLOW_APPROVAL_IDENTITY_INDEX: Final[str] = (
    "uq_workflow_approval_run_id_node_id"
)

#: ``workflow_run_id`` 普通索引（按 Run 关联查询）。
WORKFLOW_APPROVAL_RUN_ID_INDEX: Final[str] = "ix_workflow_approval_workflow_run_id"

#: 列长度上限（对齐 agent_approval 列宽惯例）。
#: ``project_id`` **复用 workflow_run 定义**：类型 / 长度 / 非空必须一致
#: （ADR §16 I1/I4 / F-1 对齐）。
WORKFLOW_APPROVAL_PROJECT_ID_MAX_LENGTH: Final[int] = (
    WORKFLOW_RUN_PROJECT_ID_MAX_LENGTH
)
WORKFLOW_APPROVAL_ID_MAX_LENGTH: Final[int] = 128
WORKFLOW_APPROVAL_RUN_ID_MAX_LENGTH: Final[int] = 64
WORKFLOW_APPROVAL_NODE_ID_MAX_LENGTH: Final[int] = 64
WORKFLOW_APPROVAL_STATUS_MAX_LENGTH: Final[int] = 32

#: 状态白名单（引用域层常量：PENDING / APPROVED / REJECTED）。
WORKFLOW_APPROVAL_STATUSES: Final[frozenset[str]] = APPROVAL_STATUSES


class WorkflowApproval(Base):
    """一个 Workflow Approval Gate 的审批请求（PENDING → APPROVED | REJECTED）。

    生命周期：create ⇒ PENDING（resolved_at = NULL）；
    resolve（条件 UPDATE，单赢）⇒ APPROVED | REJECTED（resolved_at = now()）；
    终态不可逆；生产路径**无** delete（``delete_for_test`` 仅供测试 / cleanup）。
    """

    __tablename__ = WORKFLOW_APPROVAL_TABLE

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键（BIGINT 自增；项目既有规范）",
    )
    approval_id: Mapped[str] = mapped_column(
        String(WORKFLOW_APPROVAL_ID_MAX_LENGTH),
        nullable=False,
        comment="审批身份（Runtime 签发；UNIQUE；≠ workflow_run_id / node_id）",
    )
    workflow_run_id: Mapped[str] = mapped_column(
        String(WORKFLOW_APPROVAL_RUN_ID_MAX_LENGTH),
        ForeignKey(
            f"{WORKFLOW_RUN_SCHEMA}.{WORKFLOW_RUN_TABLE}.workflow_run_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        comment=(
            "所属 Workflow Run（FK → ai_ops.workflow_run.workflow_run_id；"
            "ON DELETE CASCADE）"
        ),
    )
    project_id: Mapped[str] = mapped_column(
        String(WORKFLOW_APPROVAL_PROJECT_ID_MAX_LENGTH),
        nullable=False,
        comment=(
            "项目作用域（**冗余携带** —— ADR §16 I1/I4）：创建时从所属 "
            "workflow_run 解析复制（Repository 保证；不信任调用方传入；非 FK）"
        ),
    )
    node_id: Mapped[str] = mapped_column(
        String(WORKFLOW_APPROVAL_NODE_ID_MAX_LENGTH),
        nullable=False,
        comment="审批节点标识（= WorkflowDefinition 的 APPROVAL 节点；≠ Agent step）",
    )
    status: Mapped[str] = mapped_column(
        String(WORKFLOW_APPROVAL_STATUS_MAX_LENGTH),
        nullable=False,
        comment="PENDING / APPROVED / REJECTED（终态不可逆；单赢条件 UPDATE）",
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="裁决时间（UTC；PENDING ⇒ NULL；终态 ⇒ 非 NULL；不可逆 ⇒ 不重写）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="审批创建时间（UTC）",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="最近一次更新（仅 PENDING → 终态一次）",
    )

    __table_args__ = (
        Index(WORKFLOW_APPROVAL_ID_INDEX, "approval_id", unique=True),
        Index(
            WORKFLOW_APPROVAL_IDENTITY_INDEX,
            "workflow_run_id",
            "node_id",
            unique=True,
        ),
        Index(WORKFLOW_APPROVAL_RUN_ID_INDEX, "workflow_run_id"),
        # status：**不预建**（沿 agent_approval 先例；过滤查询尚未实现）
        # project_id：**不预建**（冗余列只服务读侧归属证明；Step 4 如需要再评估）
        {"schema": WORKFLOW_APPROVAL_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<WorkflowApproval id={self.id} approval_id={self.approval_id} "
            f"workflow_run_id={self.workflow_run_id} node_id={self.node_id} "
            f"status={self.status}>"
        )
