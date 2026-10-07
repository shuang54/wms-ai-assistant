"""Agent Step ORM Model（Phase 5 — AgentStep Persistence）。

```text
ai_ops.agent_step      一行 = 一次 Agent Run 内的一个**逻辑步**（step projection）
```

设计依据（已冻结，不得就地重设计）：
    docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md §30.10（OD-P5-27 迁移路径）
    docs/decisions/Phase/Phase 5 — Agent State & Resume Boundary Decision.md
        §4  AgentStep = Runtime State + Audit Projection · §19 不复制 observation payload

职责边界（严格）：

    * 只承载 **step identity + type + status + tool name + 稳定错误码 + timing**；
    * **禁止**：prompt / messages / user question / system prompt / context /
      RAG chunks / tool **arguments** / tool **result** / SQL / final answer /
      headers / 密钥 / exception message / stack trace / raw LLM response；
    * **不**复制 `ToolExecutionRecord`（tool_call_id / round / project_id / success）
      与 `LLMUsageRecord`（provider / model / tokens / request_id）；
    * **不是 checkpoint**：不承载"下一步做什么 / 挂起了什么"（Resume 属未来独立载体）；
    * **无外键**：`run_id` 为 correlation 字符串（沿用 `agent_run` / `conversation_turn`
      "correlation 不用 FK" 的先例；OD-P5-25）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

#: AI 内部运维 schema（与业务 schema `public` 物理隔离；与其它 ai_ops 表同 schema）。
AGENT_STEP_SCHEMA: Final[str] = "ai_ops"

#: 表名常量（DB 测试 / 残留守卫共用）。
AGENT_STEP_TABLE: Final[str] = "agent_step"

#: Step identity 唯一索引：**(run_id, step_index)**（不同 Run 可各有 step_index=0）。
AGENT_STEP_IDENTITY_INDEX: Final[str] = "uq_agent_step_run_id_step_index"

#: ``run_id`` 长度上限（与 agent_run.run_id 一致）。
AGENT_STEP_RUN_ID_MAX_LENGTH: Final[int] = 64

#: ``step_type`` 列长度（FINAL / TOOL_CALL 均 ≤ 16）。
AGENT_STEP_TYPE_MAX_LENGTH: Final[int] = 32

#: ``status`` 列长度（OK / FAILED 均 ≤ 16）。
AGENT_STEP_STATUS_MAX_LENGTH: Final[int] = 32

#: ``tool_name`` 列长度（Tool 名称白名单为 snake_case 标识符）。
AGENT_STEP_TOOL_NAME_MAX_LENGTH: Final[int] = 128

#: 稳定错误码列长度（复用 Agent 错误码白名单，≤ 64）。
AGENT_STEP_ERROR_CODE_MAX_LENGTH: Final[int] = 64

#: 允许的 Step 类型（**严格等于** Runtime 已存在的类型；不新增 LLM_DECISION / CHECKPOINT /
#: APPROVAL / RESUME —— D-2 不在本阶段解决）。
AGENT_STEP_TYPES: Final[frozenset[str]] = frozenset({"FINAL", "TOOL_CALL"})

#: 允许的 Step 状态（**严格复用** Runtime 既有状态；不新增 WAITING_APPROVAL /
#: CANCELLED / RESUMING / CHECKPOINTED）。
AGENT_STEP_STATUSES: Final[frozenset[str]] = frozenset({"OK", "FAILED"})


class AgentStep(Base):
    """一次 Agent 逻辑步的持久化投影（append-only；best-effort 写入）。

    写入时机：Agent Run 结束（终态确定）后、随 ``AgentRuntimeResult.step_records``
    一次性落库（**不是**运行中逐步写；不是 checkpoint）。
    删除：本阶段无 delete / TTL / 级联（OD-P5-28）。
    """

    __tablename__ = AGENT_STEP_TABLE

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="Agent Step 主键（BIGINT 自增；项目既有规范）",
    )
    run_id: Mapped[str] = mapped_column(
        String(AGENT_STEP_RUN_ID_MAX_LENGTH),
        nullable=False,
        comment=(
            "所属 Agent Run 身份（= agent_run.run_id；correlation，**无外键**）"
        ),
    )
    step_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="步序（0 起；与 run_id 组成唯一身份）",
    )
    step_type: Mapped[str] = mapped_column(
        String(AGENT_STEP_TYPE_MAX_LENGTH),
        nullable=False,
        comment="Step 类型：FINAL / TOOL_CALL（**不新增**其它类型）",
    )
    status: Mapped[str] = mapped_column(
        String(AGENT_STEP_STATUS_MAX_LENGTH),
        nullable=False,
        comment="Step 状态：OK / FAILED（**复用** Runtime 既有状态）",
    )
    tool_name: Mapped[str | None] = mapped_column(
        String(AGENT_STEP_TOOL_NAME_MAX_LENGTH),
        nullable=True,
        comment="仅 TOOL_CALL：Tool 名称；无 Tool ⇒ NULL（**禁止** arguments / result）",
    )
    error_code: Mapped[str | None] = mapped_column(
        String(AGENT_STEP_ERROR_CODE_MAX_LENGTH),
        nullable=True,
        comment=(
            "稳定错误码（Agent 错误码白名单）—— "
            "**禁止** exception message / stack trace / raw provider error"
        ),
    )
    elapsed_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment=(
            "AgentStep.elapsed_ms 原值（Runtime 注入 clock 度量）—— "
            "NULL 或 >= 0；禁止 negative / NaN / Infinity"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="本条持久化记录的创建时间（不是 Step 开始时间）",
    )

    __table_args__ = (
        # Step identity：同一 Run 内 step_index 唯一（不同 Run 可重复）
        Index(
            AGENT_STEP_IDENTITY_INDEX,
            "run_id",
            "step_index",
            unique=True,
        ),
        {"schema": AGENT_STEP_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentStep id={self.id} run_id={self.run_id} "
            f"step_index={self.step_index} step_type={self.step_type} "
            f"status={self.status}>"
        )
