"""Workflow Run ORM Model（Phase 6 Step 2 — Workflow Persistence）。

```text
ai_ops.workflow_run       一行 = 一次 Workflow 执行实例
                          （含 Recovery Control State 承载 —— **无** workflow_checkpoint 表）
```

设计依据（已冻结，不得就地重设计）：
    `docs/decisions/Phase/Phase 6 — Workflow Runtime Architecture Decision.md`（ACCEPTED）§8
    `docs/decisions/Phase/Phase 6 — Workflow Runtime Architecture Discovery.md`（FROZEN）§6 / §9

职责边界（严格）：

    * Run Lifecycle + Execution State：WorkflowCheckpoint **概念**（current_node_id /
      next_action / state_version / cancel 信号 / attempt 累计）承载于本表；
    * 只承载 **身份 + 状态 + 控制指针 + 计数 + 时间戳**；
    * **禁止**：prompt / messages / conversation context / RAG chunks /
      tool arguments / tool result body / SQL / headers / 密钥 /
      exception message / stack trace（安全边界 —— Discovery §25）；
    * **无跨模块外键**：``request_id`` 是 correlation 字符串、
      ``project_id`` 是服务器端解析结果 —— 均非 FK（沿 ``agent_run`` 先例）；
    * 独立 namespace：``workflow_run_id`` ≠ agent ``run_id`` ≠ ``request_id``
      （Discovery §6：不是同一个身份，禁止互相充当代替）。

命名说明（对齐决策）：域层（Step 1 已冻结）使用 ``status``
（``WorkflowRunResult.status`` / ``WORKFLOW_STATUSES``）—— 本表以 ``status``
列承载任务书 "state" 的同一语义（对齐全项目惯例：
``agent_run.status`` / ``agent_step.status`` / ``agent_checkpoint.status``）。

``next_action``：控制推进语义（本模块本地白名单 —— 域层 Step 1 未冻结该集合；
Step 3 Runtime 落地时如需要可正式化，属后续 step 决策）。

状态白名单来源：``backend.app.workflow.states``（域层；单向依赖，无环）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.workflow.states import (
    WORKFLOW_TERMINAL_STATUSES,
    WORKFLOW_STATUSES,
)

__all__ = [
    "WorkflowRun",
    "WORKFLOW_RUN_SCHEMA",
    "WORKFLOW_RUN_TABLE",
    "WORKFLOW_RUN_IDENTITY_INDEX",
    "WORKFLOW_RUN_REQUEST_ID_INDEX",
    "WORKFLOW_RUN_PROJECT_ID_INDEX",
    "WORKFLOW_RUN_CREATED_AT_INDEX",
    "WORKFLOW_RUN_ID_MAX_LENGTH",
    "WORKFLOW_RUN_WORKFLOW_ID_MAX_LENGTH",
    "WORKFLOW_RUN_VERSION_MAX_LENGTH",
    "WORKFLOW_RUN_REQUEST_ID_MAX_LENGTH",
    "WORKFLOW_RUN_PROJECT_ID_MAX_LENGTH",
    "WORKFLOW_RUN_STATUS_MAX_LENGTH",
    "WORKFLOW_RUN_NODE_ID_MAX_LENGTH",
    "WORKFLOW_RUN_NEXT_ACTION_MAX_LENGTH",
    "WORKFLOW_RUN_INITIAL_STATE_VERSION",
    "WORKFLOW_RUN_STATUSES",
    "WORKFLOW_RUN_TERMINAL_STATUSES",
    "WORKFLOW_NEXT_ACTION_NONE",
    "WORKFLOW_NEXT_ACTION_NODE_EXECUTION",
    "WORKFLOW_NEXT_ACTION_WAITING_APPROVAL",
    "WORKFLOW_NEXT_ACTION_COMPLETED",
    "WORKFLOW_NEXT_ACTION_FAILED",
    "WORKFLOW_NEXT_ACTION_CANCELLED",
    "WORKFLOW_NEXT_ACTIONS",
]

#: AI 内部运维 schema（与业务 schema `public` 物理隔离；与其它 ai_ops 表同轴）。
WORKFLOW_RUN_SCHEMA: Final[str] = "ai_ops"

#: 表名常量（DB 测试 / 残留守卫共用）。
WORKFLOW_RUN_TABLE: Final[str] = "workflow_run"

#: 执行身份唯一索引（一次 Workflow 执行实例一行）。
WORKFLOW_RUN_IDENTITY_INDEX: Final[str] = "uq_workflow_run_workflow_run_id"

#: ``request_id`` 普通索引（调用 / correlation；一次调用可关联多次 resume 推进）。
WORKFLOW_RUN_REQUEST_ID_INDEX: Final[str] = "ix_workflow_run_request_id"

#: ``project_id`` 普通索引（project-scoped 隔离查询；Discovery §16 / ADR §16）。
WORKFLOW_RUN_PROJECT_ID_INDEX: Final[str] = "ix_workflow_run_project_id"

#: ``created_at`` 索引（时间序；与既有观测表 / agent_run 先例一致）。
WORKFLOW_RUN_CREATED_AT_INDEX: Final[str] = "ix_workflow_run_created_at"

#: 列长度上限（对齐 agent 表列宽惯例：run_id 64 / project_id 128 / status 32）。
WORKFLOW_RUN_ID_MAX_LENGTH: Final[int] = 64
WORKFLOW_RUN_WORKFLOW_ID_MAX_LENGTH: Final[int] = 64
WORKFLOW_RUN_VERSION_MAX_LENGTH: Final[int] = 64
WORKFLOW_RUN_REQUEST_ID_MAX_LENGTH: Final[int] = 64
WORKFLOW_RUN_PROJECT_ID_MAX_LENGTH: Final[int] = 128
WORKFLOW_RUN_STATUS_MAX_LENGTH: Final[int] = 32
WORKFLOW_RUN_NODE_ID_MAX_LENGTH: Final[int] = 64
WORKFLOW_RUN_NEXT_ACTION_MAX_LENGTH: Final[int] = 32

#: 初始 state_version（创建 = 0；每次成功控制状态推进 + 1 —— Discovery §9 / Step 2 §10）。
WORKFLOW_RUN_INITIAL_STATE_VERSION: Final[int] = 0

#: 状态白名单（引用域层常量：单一权威；含 PENDING/RUNNING/WAITING/3 终态）。
WORKFLOW_RUN_STATUSES: Final[frozenset[str]] = WORKFLOW_STATUSES

#: 终态（不可逆；cancel 只作用于非终态行 —— 沿 agent 语义）。
WORKFLOW_RUN_TERMINAL_STATUSES: Final[frozenset[str]] = WORKFLOW_TERMINAL_STATUSES

# ---- next_action 白名单（控制推进语义；本模块本地定义） ----

#: 无待推进动作（初始态）。
WORKFLOW_NEXT_ACTION_NONE: Final[str] = "NONE"

#: 下一步 = 执行/推进节点。
WORKFLOW_NEXT_ACTION_NODE_EXECUTION: Final[str] = "NODE_EXECUTION"

#: run = WAITING，等待人工审批裁决（approval gate）。
WORKFLOW_NEXT_ACTION_WAITING_APPROVAL: Final[str] = "WAITING_APPROVAL"

#: 终态对齐（与 status 同时写入，便于读侧单列判定）。
WORKFLOW_NEXT_ACTION_COMPLETED: Final[str] = "COMPLETED"
WORKFLOW_NEXT_ACTION_FAILED: Final[str] = "FAILED"
WORKFLOW_NEXT_ACTION_CANCELLED: Final[str] = "CANCELLED"

#: 全部合法 next_action。
WORKFLOW_NEXT_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        WORKFLOW_NEXT_ACTION_NONE,
        WORKFLOW_NEXT_ACTION_NODE_EXECUTION,
        WORKFLOW_NEXT_ACTION_WAITING_APPROVAL,
        WORKFLOW_NEXT_ACTION_COMPLETED,
        WORKFLOW_NEXT_ACTION_FAILED,
        WORKFLOW_NEXT_ACTION_CANCELLED,
    }
)


class WorkflowRun(Base):
    """一次 Workflow 执行实例（当前控制状态载体；可 UPDATE + state_version CAS）。

    生命周期：创建 = PENDING / state_version = 0；每次成功控制状态推进 +1；
    终态不可逆；生产路径**无** delete（``delete_for_test`` 仅供测试 / cleanup）。
    """

    __tablename__ = WORKFLOW_RUN_TABLE

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键（BIGINT 自增；项目既有规范）",
    )
    workflow_run_id: Mapped[str] = mapped_column(
        String(WORKFLOW_RUN_ID_MAX_LENGTH),
        nullable=False,
        comment=(
            "Workflow 执行身份（Runtime 签发；UNIQUE；"
            "≠ agent run_id / ≠ request_id —— 独立 namespace）"
        ),
    )
    workflow_id: Mapped[str] = mapped_column(
        String(WORKFLOW_RUN_WORKFLOW_ID_MAX_LENGTH),
        nullable=False,
        comment="Workflow Definition 逻辑身份（= definition name；非 FK）",
    )
    workflow_version: Mapped[str] = mapped_column(
        String(WORKFLOW_RUN_VERSION_MAX_LENGTH),
        nullable=False,
        comment=(
            "Definition 版本（**必须**持久化：resume 按本值解析；"
            "禁止隐式取 current version —— Discovery §8）"
        ),
    )
    request_id: Mapped[str] = mapped_column(
        String(WORKFLOW_RUN_REQUEST_ID_MAX_LENGTH),
        nullable=False,
        comment="调用 / correlation identity（≠ workflow_run_id；非 FK）",
    )
    project_id: Mapped[str] = mapped_column(
        String(WORKFLOW_RUN_PROJECT_ID_MAX_LENGTH),
        nullable=False,
        comment="项目作用域（服务器端解析结果；**NOT NULL**；非 FK）",
    )
    input: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "启动输入（D1：结构化 JSON 文本；应用层 serialize_json_value 边界 "
            "≤ 32 KiB / 深度 ≤ 8；None = 无输入；创建后**不可变**）"
        ),
    )
    status: Mapped[str] = mapped_column(
        String(WORKFLOW_RUN_STATUS_MAX_LENGTH),
        nullable=False,
        comment=(
            "PENDING / RUNNING / WAITING / COMPLETED / FAILED / CANCELLED"
            "（域层白名单；终态不可逆；CANCELLED ≠ FAILED）"
        ),
    )
    state_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment=(
            "控制状态版本（创建 = 0；每次成功推进 + 1；"
            "CAS 的 expected_state_version —— Discovery §9）"
        ),
    )
    current_node_id: Mapped[str | None] = mapped_column(
        String(WORKFLOW_RUN_NODE_ID_MAX_LENGTH),
        nullable=True,
        comment="当前/下一节点指针（Recovery Control State；无 ⇒ NULL）",
    )
    next_action: Mapped[str] = mapped_column(
        String(WORKFLOW_RUN_NEXT_ACTION_MAX_LENGTH),
        nullable=False,
        comment="控制推进语义（白名单；NONE / NODE_EXECUTION / WAITING_APPROVAL / 终态）",
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="run 级累计 attempt 数（跨 resume 累计；预算保护；>= 0）",
    )
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment=(
            "协作式取消**信号**（不是 status 枚举成员）："
            "true ⇒ Runtime 在安全边界观察后 CANCELLED；只由 request_cancel 的 CAS 置位"
        ),
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="取消信号置位时间（UTC；**不**存 reason / 自由文本）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="run 记录创建时间（不是执行开始时间）",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="首次进入 RUNNING 的时间（**只写一次**；未开始 ⇒ NULL）",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="进入终态的时间（**只写一次**；非终态 ⇒ NULL）",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="最近一次成功控制状态推进时间",
    )

    __table_args__ = (
        Index(WORKFLOW_RUN_IDENTITY_INDEX, "workflow_run_id", unique=True),
        Index(WORKFLOW_RUN_REQUEST_ID_INDEX, "request_id"),
        Index(WORKFLOW_RUN_PROJECT_ID_INDEX, "project_id"),
        Index(WORKFLOW_RUN_CREATED_AT_INDEX, "created_at"),
        # status / next_action：**不预建**（沿 agent_run 先例：过滤查询尚未实现，
        # 等 Step 4 Read Model 真实需要时再评估；不对小基数列堆无意义索引）
        {"schema": WORKFLOW_RUN_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<WorkflowRun id={self.id} workflow_run_id={self.workflow_run_id} "
            f"workflow_id={self.workflow_id} status={self.status} "
            f"state_version={self.state_version}>"
        )
