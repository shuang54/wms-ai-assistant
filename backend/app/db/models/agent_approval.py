"""Agent Approval ORM Model（Phase 5 P-7 — HITL Tool Approval）。
```text
ai_ops.agent_approval      一行 = 一次 Tool Call 的**人工审批请求**
```

设计依据（已冻结，不得就地重设计）：
    Phase 5 P-7 任务书 §八–§十三 / §十七–§十九 / §四十二 / §五十一

职责边界（严格）：

    * 只回答一个问题：**人类是否允许执行这次 Tool Call**；
      **不是** AgentDecision（LLM 提案；arguments 在 ``agent_decision``）、
      **不是** AgentToolResult（实际结果；在 ``agent_tool_result``）、
      **不是** ToolExecutionRecord（观测）；
    * 只承载 **身份 + 状态 + 时间**：approval_id / run_id / step_index /
      tool_call_id / tool_name / status / decision / reason_code / 时间戳；
    * **禁止**：tool arguments（连 hash 都不需要 —— 执行参数由 Decision Store 提供）·
      prompt / messages / conversation context / RAG chunks / tool definitions ·
      SQL · API key / password / Authorization / database URL · Tool Result ·
      LLM raw response · exception body / stack trace；
    * **禁止**：conversation_id / turn_id / idempotency_key（Conversation 耦合）·
      evidence_id（Evidence 耦合）；
    * **无外键**：``run_id`` 为 correlation 字符串（沿用 ``agent_run`` /
      ``agent_step`` / ``agent_tool_result`` / ``agent_checkpoint`` 先例）；
    * **可更新对象（仅一次）**：``PENDING → APPROVED | REJECTED``（终态不可逆；
      重复同一裁决 ⇒ 幂等，不同裁决 ⇒ conflict；§十八 / §十九 / §五十一）。

唯一身份（§十 / §十二）：
    * ``UNIQUE(approval_id)``          —— 审批身份（本模块签发；≠ run_id /
      request_id / tool_call_id）；
    * ``UNIQUE(run_id, step_index)``   —— **一个 Tool 执行身份最多一个审批**
      （重复请求幂等返回同一行；与 agent_tool_result 的 execution identity 对齐）；
    * ``tool_call_id`` 是 Provider protocol correlation —— **不加唯一约束**。

常量来源：状态 / 裁决白名单与列长度上限的**唯一权威**在
``backend.app.agent.approval``（域层）；本模块只做 ORM 映射（单向依赖，无环）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.agent.approval import (
    APPROVAL_DECISION_MAX_LENGTH,
    APPROVAL_ID_MAX_LENGTH,
    APPROVAL_RUN_ID_MAX_LENGTH,
    APPROVAL_STATUS_MAX_LENGTH,
    APPROVAL_STATUSES,
    APPROVAL_TOOL_CALL_ID_MAX_LENGTH,
    APPROVAL_TOOL_NAME_MAX_LENGTH,
)
from backend.app.db.base import Base

__all__ = [
    "AgentApproval",
    "AGENT_APPROVAL_SCHEMA",
    "AGENT_APPROVAL_TABLE",
    "AGENT_APPROVAL_ID_INDEX",
    "AGENT_APPROVAL_IDENTITY_INDEX",
    "AGENT_APPROVAL_RUN_ID_INDEX",
    "AGENT_APPROVAL_ID_MAX_LENGTH",
    "AGENT_APPROVAL_RUN_ID_MAX_LENGTH",
    "AGENT_APPROVAL_TOOL_NAME_MAX_LENGTH",
    "AGENT_APPROVAL_TOOL_CALL_ID_MAX_LENGTH",
    "AGENT_APPROVAL_STATUS_MAX_LENGTH",
    "AGENT_APPROVAL_DECISION_MAX_LENGTH",
    "AGENT_APPROVAL_STATUSES",
]

#: AI 内部运维 schema（与业务 schema `public` 物理隔离）。
AGENT_APPROVAL_SCHEMA: Final[str] = "ai_ops"

#: 表名常量（DB 测试 / 残留守卫共用）。
AGENT_APPROVAL_TABLE: Final[str] = "agent_approval"

#: 审批身份唯一索引（approval_id）。
AGENT_APPROVAL_ID_INDEX: Final[str] = "uq_agent_approval_approval_id"

#: 执行身份唯一索引（**一个 Tool 执行身份最多一个审批**；重复请求幂等）。
AGENT_APPROVAL_IDENTITY_INDEX: Final[str] = "uq_agent_approval_run_id_step_index"

#: run_id 普通索引（按 Run 关联查询）。
AGENT_APPROVAL_RUN_ID_INDEX: Final[str] = "ix_agent_approval_run_id"

#: 列长度上限（引用域层常量：单一权威）。
AGENT_APPROVAL_ID_MAX_LENGTH: Final[int] = APPROVAL_ID_MAX_LENGTH
AGENT_APPROVAL_RUN_ID_MAX_LENGTH: Final[int] = APPROVAL_RUN_ID_MAX_LENGTH
AGENT_APPROVAL_TOOL_NAME_MAX_LENGTH: Final[int] = APPROVAL_TOOL_NAME_MAX_LENGTH
AGENT_APPROVAL_TOOL_CALL_ID_MAX_LENGTH: Final[int] = APPROVAL_TOOL_CALL_ID_MAX_LENGTH
AGENT_APPROVAL_STATUS_MAX_LENGTH: Final[int] = APPROVAL_STATUS_MAX_LENGTH
AGENT_APPROVAL_DECISION_MAX_LENGTH: Final[int] = APPROVAL_DECISION_MAX_LENGTH

#: 状态白名单（引用域层常量；PENDING / APPROVED / REJECTED）。
AGENT_APPROVAL_STATUSES: Final[frozenset[str]] = APPROVAL_STATUSES


class AgentApproval(Base):
    """一次 Tool Call 的人工审批请求（PENDING → APPROVED | REJECTED；终态不可逆）。"""

    __tablename__ = AGENT_APPROVAL_TABLE

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键（BIGINT 自增；项目既有规范）",
    )
    approval_id: Mapped[str] = mapped_column(
        String(AGENT_APPROVAL_ID_MAX_LENGTH),
        nullable=False,
        comment=(
            "审批身份（Agent 层签发；≠ run_id / request_id / tool_call_id；"
            "UNIQUE）"
        ),
    )
    run_id: Mapped[str] = mapped_column(
        String(AGENT_APPROVAL_RUN_ID_MAX_LENGTH),
        nullable=False,
        comment="所属 Agent Run 身份（= agent_run.run_id；correlation，**无外键**）",
    )
    step_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Agent step（0 起；= 该 Tool Call 的 execution identity；**不是** round）",
    )
    tool_call_id: Mapped[str | None] = mapped_column(
        String(AGENT_APPROVAL_TOOL_CALL_ID_MAX_LENGTH),
        nullable=True,
        comment="Provider protocol correlation（**不**唯一；**不**是审批身份）",
    )
    tool_name: Mapped[str] = mapped_column(
        String(AGENT_APPROVAL_TOOL_NAME_MAX_LENGTH),
        nullable=False,
        comment="待审批 Tool 名（非敏感；**不**保存 arguments / SQL / prompt）",
    )
    status: Mapped[str] = mapped_column(
        String(AGENT_APPROVAL_STATUS_MAX_LENGTH),
        nullable=False,
        comment="PENDING / APPROVED / REJECTED（终态不可逆；§五十一）",
    )
    decision: Mapped[str | None] = mapped_column(
        String(AGENT_APPROVAL_DECISION_MAX_LENGTH),
        nullable=True,
        comment="人类裁决（APPROVED / REJECTED；PENDING ⇒ NULL；**无**自由文本意见）",
    )
    reason_code: Mapped[str | None] = mapped_column(
        String(AGENT_APPROVAL_DECISION_MAX_LENGTH),
        nullable=True,
        comment=(
            "稳定码（可选；**禁止**自由文本 —— 本阶段取消/拒绝原因由稳定码体系承载）"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="审批创建时间（UTC）",
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="裁决时间（UTC；PENDING ⇒ NULL；终态不可逆 ⇒ 不重写）",
    )

    __table_args__ = (
        Index(AGENT_APPROVAL_ID_INDEX, "approval_id", unique=True),
        Index(AGENT_APPROVAL_IDENTITY_INDEX, "run_id", "step_index", unique=True),
        Index(AGENT_APPROVAL_RUN_ID_INDEX, "run_id"),
        {"schema": AGENT_APPROVAL_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentApproval id={self.id} approval_id={self.approval_id} "
            f"run_id={self.run_id} step_index={self.step_index} "
            f"status={self.status}>"
        )
