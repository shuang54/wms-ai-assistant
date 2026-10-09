"""Workflow NodeRun ORM Model（Phase 6 Step 2 — Workflow Persistence）。

```text
ai_ops.workflow_node_run      一行 = 一个 Node 的**一次 attempt**
```

设计依据（已冻结，不得就地重设计）：
    `Phase 6 — Workflow Runtime Architecture Decision.md`（ACCEPTED）§8 / §16
    `Phase 6 — Workflow Runtime Architecture Discovery.md`（FROZEN）§7 / §10

职责边界（严格）：

    * 只承载 **执行身份 + 状态 + 输出边界 + 稳定码 + 时间**：
      (workflow_run_id, node_id, attempt) 身份 / **project_id（冗余携带）** /
      node_type / status / output（JSON 文本，内联 + 大小边界）/
      error_code（稳定码白名单）/ elapsed_ms / 时间戳；
    * ``project_id`` 为 **ADR §16 I1/I4 冗余列**（F-1 对齐）：创建时由 Repository
      从所属 ``workflow_run`` 解析复制（**不信任调用方传入**；非 FK；
      类型 / 长度 / 非空与 run 定义一致）；
    * **禁止**：prompt / messages / tool arguments / SQL / headers / 密钥 /
      exception message / stack trace / 自由文本错误原因；
    * retry 语义（修正 1 / Discovery §8）：FAILED attempt **不改写**；
      retry = 同一 node_id 的**新 attempt 行**（attempt 递增，历史保留）；
    * **域内 FK**：``workflow_run_id → ai_ops.workflow_run.workflow_run_id``
      （ON DELETE CASCADE —— 沿 ``conversation_turn → conversation`` 域内父子先例；
      生产路径无 delete，FK 用于 DB 级完整性 + 测试清理）；
    * ``node_id`` / ``attempt`` 与 AgentStep 的 ``step_index`` **不同 namespace**
      （WorkflowNodeRun ≠ AgentStep —— 禁止混表 / 混身份）。

字段对齐说明（Step 1 已冻结 Domain DTO ``NodeRunOutcome``）：
    identity / status / output / error_code / elapsed_ms → 一一对应；
    Domain DTO **没有** input 字段 ⇒ 本表**不建** input 列（避免多余存储面）；
    Domain DTO 亦无 project_id —— 该列为 ADR §16 I1/I4 要求的**安全冗余列**
    （持久化层携带，用于读侧独立归属证明；非 DTO 字段映射）。

白名单来源：``backend.app.workflow.definition``（NODE_TYPES）与
``backend.app.workflow.states``（NODE_STATUSES）（域层；单向依赖，无环）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.workflow_run import (
    WORKFLOW_RUN_PROJECT_ID_MAX_LENGTH,
    WORKFLOW_RUN_SCHEMA,
    WORKFLOW_RUN_TABLE,
)
from backend.app.workflow.definition import NODE_TYPES
from backend.app.workflow.states import NODE_STATUSES, NODE_TERMINAL_STATUSES

__all__ = [
    "WorkflowNodeRun",
    "WORKFLOW_NODE_RUN_SCHEMA",
    "WORKFLOW_NODE_RUN_TABLE",
    "WORKFLOW_NODE_RUN_IDENTITY_INDEX",
    "WORKFLOW_NODE_RUN_RUN_ID_INDEX",
    "WORKFLOW_NODE_RUN_PROJECT_ID_MAX_LENGTH",
    "WORKFLOW_NODE_RUN_ID_MAX_LENGTH",
    "WORKFLOW_NODE_RUN_NODE_ID_MAX_LENGTH",
    "WORKFLOW_NODE_RUN_TYPE_MAX_LENGTH",
    "WORKFLOW_NODE_RUN_STATUS_MAX_LENGTH",
    "WORKFLOW_NODE_RUN_ERROR_CODE_MAX_LENGTH",
    "WORKFLOW_NODE_RUN_TYPES",
    "WORKFLOW_NODE_RUN_STATUSES",
    "WORKFLOW_NODE_RUN_TERMINAL_STATUSES",
]

#: AI 内部运维 schema（与 workflow_run 同轴）。
WORKFLOW_NODE_RUN_SCHEMA: Final[str] = WORKFLOW_RUN_SCHEMA

#: 表名常量（DB 测试 / 残留守卫共用）。
WORKFLOW_NODE_RUN_TABLE: Final[str] = "workflow_node_run"

#: Attempt 身份唯一索引：**(workflow_run_id, node_id, attempt)**（Step 2 §14 冻结）。
WORKFLOW_NODE_RUN_IDENTITY_INDEX: Final[str] = (
    "uq_workflow_node_run_run_id_node_id_attempt"
)

#: ``workflow_run_id`` 查询索引（列表 / 聚合；独立于 UNIQUE 复合索引语义）。
WORKFLOW_NODE_RUN_RUN_ID_INDEX: Final[str] = "ix_workflow_node_run_workflow_run_id"

#: 列长度上限（对齐 agent 表列宽惯例；error_code 64 覆盖最长域层稳定码 37 字符）。
#: ``project_id`` **复用 workflow_run 定义**：类型 / 长度 / 非空必须一致
#: （ADR §16 I1/I4 / F-1 对齐）。
WORKFLOW_NODE_RUN_PROJECT_ID_MAX_LENGTH: Final[int] = (
    WORKFLOW_RUN_PROJECT_ID_MAX_LENGTH
)
WORKFLOW_NODE_RUN_ID_MAX_LENGTH: Final[int] = 64
WORKFLOW_NODE_RUN_NODE_ID_MAX_LENGTH: Final[int] = 64
WORKFLOW_NODE_RUN_TYPE_MAX_LENGTH: Final[int] = 32
WORKFLOW_NODE_RUN_STATUS_MAX_LENGTH: Final[int] = 32
WORKFLOW_NODE_RUN_ERROR_CODE_MAX_LENGTH: Final[int] = 64

#: 节点类型白名单（引用域层常量：单一权威；AI / TOOL / APPROVAL / CONDITION）。
WORKFLOW_NODE_RUN_TYPES: Final[frozenset[str]] = NODE_TYPES

#: attempt 状态白名单（引用域层常量；RUNNING / WAITING_APPROVAL / SUCCEEDED / FAILED）。
WORKFLOW_NODE_RUN_STATUSES: Final[frozenset[str]] = NODE_STATUSES

#: attempt 终态（不可逆；retry = 新行，不改写历史行）。
WORKFLOW_NODE_RUN_TERMINAL_STATUSES: Final[frozenset[str]] = NODE_TERMINAL_STATUSES


class WorkflowNodeRun(Base):
    """一个 Node 的一次 attempt（append-only per attempt；retry ⇒ 新行）。

    写入时机（Step 3 Runtime 纪律，本步只提供持久化）：
        "先记 RUNNING、后记完成" —— attempt 开始即写一行；完成后 UPDATE 该行终态；
        retry 产生新的 attempt 行（**绝不**改写 FAILED 行）。
    """

    __tablename__ = WORKFLOW_NODE_RUN_TABLE

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键（BIGINT 自增；项目既有规范）",
    )
    workflow_run_id: Mapped[str] = mapped_column(
        String(WORKFLOW_NODE_RUN_ID_MAX_LENGTH),
        ForeignKey(
            f"{WORKFLOW_RUN_SCHEMA}.{WORKFLOW_RUN_TABLE}.workflow_run_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        comment=(
            "所属 Workflow Run（FK → ai_ops.workflow_run.workflow_run_id；"
            "ON DELETE CASCADE；域内父子关系）"
        ),
    )
    project_id: Mapped[str] = mapped_column(
        String(WORKFLOW_NODE_RUN_PROJECT_ID_MAX_LENGTH),
        nullable=False,
        comment=(
            "项目作用域（**冗余携带** —— ADR §16 I1/I4）：创建时从所属 "
            "workflow_run 解析复制（Repository 保证；不信任调用方传入；非 FK）"
        ),
    )
    node_id: Mapped[str] = mapped_column(
        String(WORKFLOW_NODE_RUN_NODE_ID_MAX_LENGTH),
        nullable=False,
        comment="节点标识（来自 WorkflowDefinition；与 attempt 组成执行身份）",
    )
    attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="第几次尝试（1 起；retry ⇒ 新行 + attempt+1；**不是** step_index）",
    )
    node_type: Mapped[str] = mapped_column(
        String(WORKFLOW_NODE_RUN_TYPE_MAX_LENGTH),
        nullable=False,
        comment="节点类型：AI / TOOL / APPROVAL / CONDITION（域层白名单）",
    )
    status: Mapped[str] = mapped_column(
        String(WORKFLOW_NODE_RUN_STATUS_MAX_LENGTH),
        nullable=False,
        comment="RUNNING / WAITING_APPROVAL / SUCCEEDED / FAILED（域层白名单）",
    )
    output: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "节点输出（JSON 文本；已通过 JSON-safe 边界；"
            "Repository 强制 ≤ 32 KiB 内联上限；超限 fail-closed —— **不**截断）"
        ),
    )
    error_code: Mapped[str | None] = mapped_column(
        String(WORKFLOW_NODE_RUN_ERROR_CODE_MAX_LENGTH),
        nullable=True,
        comment=(
            "稳定错误码（域层 WF_ERROR_CODES 白名单）—— "
            "**禁止** exception message / stack trace / 自由文本"
        ),
    )
    elapsed_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="本 attempt 耗时毫秒（NULL 或 >= 0；禁止 negative / NaN / Infinity）",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="attempt 开始时间（执行方注入；未开始 ⇒ NULL）",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="attempt 完成时间（终态时；未完成 ⇒ NULL）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="本条 attempt 记录的创建时间（先记 RUNNING 纪律）",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="最近一次 attempt 状态更新时间",
    )

    __table_args__ = (
        # Step 2 §14：同 run + 同 node + 同 attempt **必须唯一**
        Index(
            WORKFLOW_NODE_RUN_IDENTITY_INDEX,
            "workflow_run_id",
            "node_id",
            "attempt",
            unique=True,
        ),
        # run 列表查询索引（最左前缀已被 UNIQUE 覆盖，本索引独立语义：
        # 未来按 run 过滤的宽查询，与 identity 复合索引解耦）
        Index(WORKFLOW_NODE_RUN_RUN_ID_INDEX, "workflow_run_id"),
        # status：**不预建**（沿 agent 先例；过滤查询尚未实现）
        # project_id：**不预建**（冗余列只服务读侧归属证明；Step 4 如需要再评估）
        {"schema": WORKFLOW_NODE_RUN_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<WorkflowNodeRun id={self.id} workflow_run_id={self.workflow_run_id} "
            f"node_id={self.node_id} attempt={self.attempt} "
            f"status={self.status}>"
        )
