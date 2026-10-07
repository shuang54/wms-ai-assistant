"""Agent Checkpoint ORM Model（Phase 5 P-5 — Checkpoint Persistence）。

```text
ai_ops.agent_checkpoint      一行 = 一个 Agent Run 的**当前恢复控制状态**
```

设计依据（已冻结，不得就地重设计）：
    Phase 5 P-5 任务书 §七–§十一 / §十九 / §二十 / §二十五 / §四十八–§五十一

职责边界（严格）：

    * **独立持久化对象**：Checkpoint 字段**不得**塞进 ``agent_run`` /
      ``agent_step`` / ``agent_tool_result`` / ToolExecutionContext；
    * 只承载 **控制状态 + 引用 + 标识**：status / next_action / pending_* /
      state_version / claim_token；
    * **禁止**：prompt / messages / conversation context / RAG chunks /
      tool definitions / tool arguments / tool result body / SQL /
      LLM response body / token 计量 / provider / model / headers / 密钥；
    * **禁止**：conversation_id / turn_id / idempotency_key（Conversation 耦合）·
      evidence_id（Evidence 耦合）· result_payload / result_hash /
      result_size_bytes（P-4 已有）· prompt_tokens / model / finish_reason
      （LLM observation 已有）；
    * **无外键**：``run_id`` 为 correlation 字符串（沿用 ``agent_run`` /
      ``agent_step`` / ``agent_tool_result`` 的"correlation 不用 FK"先例；§七）；
    * **可更新对象**：与 append-only 的 run / step / tool_result 不同，
      Checkpoint 允许 UPDATE（current control state；§九），
      乐观并发由 ``state_version`` + ``claim_token`` 保证（§十二 / §十七）。

唯一身份（§十）：``UNIQUE(run_id)`` ⇒ 一个 Run 最多一个 current checkpoint。
**不是** ``UNIQUE(run_id, state_version)``（state_version 是版本号，不是行身份）。

常量来源：状态 / next_action 白名单与列长度上限的**唯一权威**在
``backend.app.agent.checkpoint``（域层）；本模块只做 ORM 映射（单向依赖，无环）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.agent.checkpoint import (
    CHECKPOINT_CLAIM_TOKEN_MAX_LENGTH,
    CHECKPOINT_NEXT_ACTION_MAX_LENGTH,
    CHECKPOINT_NEXT_ACTIONS,
    CHECKPOINT_PENDING_REF_MAX_LENGTH,
    CHECKPOINT_PENDING_TOOL_CALL_ID_MAX_LENGTH,
    CHECKPOINT_RUN_ID_MAX_LENGTH,
    CHECKPOINT_STATUS_MAX_LENGTH,
    CHECKPOINT_STATUSES,
)
from backend.app.db.base import Base

__all__ = [
    "AgentCheckpoint",
    "AGENT_CHECKPOINT_SCHEMA",
    "AGENT_CHECKPOINT_TABLE",
    "AGENT_CHECKPOINT_IDENTITY_INDEX",
    "AGENT_CHECKPOINT_RUN_ID_MAX_LENGTH",
    "AGENT_CHECKPOINT_CLAIM_TOKEN_MAX_LENGTH",
    "AGENT_CHECKPOINT_STATUS_MAX_LENGTH",
    "AGENT_CHECKPOINT_NEXT_ACTION_MAX_LENGTH",
    "AGENT_CHECKPOINT_PENDING_TOOL_CALL_ID_MAX_LENGTH",
    "AGENT_CHECKPOINT_PENDING_REF_MAX_LENGTH",
    "AGENT_CHECKPOINT_STATUSES",
    "AGENT_CHECKPOINT_NEXT_ACTIONS",
]

#: AI 内部运维 schema（与业务 schema `public` 物理隔离）。
AGENT_CHECKPOINT_SCHEMA: Final[str] = "ai_ops"

#: 表名常量（DB 测试 / 残留守卫共用）。
AGENT_CHECKPOINT_TABLE: Final[str] = "agent_checkpoint"

#: 行身份唯一索引：**UNIQUE(run_id)**（一个 Run 最多一个 current checkpoint；§十）。
AGENT_CHECKPOINT_IDENTITY_INDEX: Final[str] = "uq_agent_checkpoint_run_id"

#: 列长度上限（引用域层常量：单一权威）。
AGENT_CHECKPOINT_RUN_ID_MAX_LENGTH: Final[int] = CHECKPOINT_RUN_ID_MAX_LENGTH
AGENT_CHECKPOINT_CLAIM_TOKEN_MAX_LENGTH: Final[int] = CHECKPOINT_CLAIM_TOKEN_MAX_LENGTH
AGENT_CHECKPOINT_STATUS_MAX_LENGTH: Final[int] = CHECKPOINT_STATUS_MAX_LENGTH
AGENT_CHECKPOINT_NEXT_ACTION_MAX_LENGTH: Final[int] = CHECKPOINT_NEXT_ACTION_MAX_LENGTH
AGENT_CHECKPOINT_PENDING_TOOL_CALL_ID_MAX_LENGTH: Final[int] = (
    CHECKPOINT_PENDING_TOOL_CALL_ID_MAX_LENGTH
)
AGENT_CHECKPOINT_PENDING_REF_MAX_LENGTH: Final[int] = CHECKPOINT_PENDING_REF_MAX_LENGTH

#: 白名单（引用域层常量）。
AGENT_CHECKPOINT_STATUSES: Final[frozenset[str]] = CHECKPOINT_STATUSES
AGENT_CHECKPOINT_NEXT_ACTIONS: Final[frozenset[str]] = CHECKPOINT_NEXT_ACTIONS


class AgentCheckpoint(Base):
    """一个 Agent Run 的当前恢复控制状态（可 UPDATE；乐观并发）。

    写入时机（P-5）：
        * Runtime：Tool Result durable 之后立即写入 ACTIVE / LLM_DECISION
          （``pending_step_index = next step``，``pending_tool_result_ref`` 指向
          P-4 结果行）；
        * Orchestrator：Agent 终态（COMPLETED / FAILED / LIMIT_REACHED）之后写入
          TERMINAL / COMPLETED|FAILED。
    删除：生产路径**无** delete（§二十六：只提供 ``delete_for_test``）。
    """

    __tablename__ = AGENT_CHECKPOINT_TABLE

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键（BIGINT 自增；项目既有规范）",
    )
    run_id: Mapped[str] = mapped_column(
        String(AGENT_CHECKPOINT_RUN_ID_MAX_LENGTH),
        nullable=False,
        comment=(
            "所属 Agent Run 身份（= agent_run.run_id；correlation，**无外键**；"
            "UNIQUE ⇒ 一个 Run 最多一个 current checkpoint）"
        ),
    )
    state_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="状态版本（创建=1；每次成功更新 +1；乐观并发的 expected 值）",
    )
    claim_token: Mapped[str] = mapped_column(
        String(AGENT_CHECKPOINT_CLAIM_TOKEN_MAX_LENGTH),
        nullable=False,
        comment=(
            "内部并发/所有权令牌（secrets.token_urlsafe；**绝不**对外暴露 / "
            "**绝不**进入 API / Conversation / AIOrchestrationResult）"
        ),
    )
    status: Mapped[str] = mapped_column(
        String(AGENT_CHECKPOINT_STATUS_MAX_LENGTH),
        nullable=False,
        comment="ACTIVE / PAUSED（预留）/ TERMINAL",
    )
    next_action: Mapped[str] = mapped_column(
        String(AGENT_CHECKPOINT_NEXT_ACTION_MAX_LENGTH),
        nullable=False,
        comment=(
            "NONE / LLM_DECISION / TOOL_EXECUTION / WAITING_APPROVAL（预留）/ "
            "COMPLETED / FAILED（**不新增** LIMIT_REACHED / CANCELLED）"
        ),
    )
    pending_step_index: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="待继续处理的 Agent step（0 起；**不是** round）；无 pending ⇒ NULL",
    )
    pending_tool_call_id: Mapped[str | None] = mapped_column(
        String(AGENT_CHECKPOINT_PENDING_TOOL_CALL_ID_MAX_LENGTH),
        nullable=True,
        comment=(
            "仅 next_action=TOOL_EXECUTION 时允许（Provider ToolCall.id；"
            "**禁止** uuid4）；其它情况必须为 NULL"
        ),
    )
    pending_decision_ref: Mapped[str | None] = mapped_column(
        String(AGENT_CHECKPOINT_PENDING_REF_MAX_LENGTH),
        nullable=True,
        comment=(
            "已持久化 LLM Decision 的引用（**不**存 message / prompt / reasoning）；"
            "当前无 Decision Store ⇒ 恒为 NULL（P-6 prerequisite）"
        ),
    )
    pending_tool_result_ref: Mapped[str | None] = mapped_column(
        String(AGENT_CHECKPOINT_PENDING_REF_MAX_LENGTH),
        nullable=True,
        comment=(
            "P-4 结果引用 '{run_id}:{step_index}'（逻辑定位 agent_tool_result；"
            "**不**新增 result_ref API、**不**复制 result body）"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="首次创建时间（更新时不改变）",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="最近一次成功更新的时间（create ⇒ = created_at）",
    )

    __table_args__ = (
        # 行身份：一个 Run 最多一个 current checkpoint（§十）
        Index(AGENT_CHECKPOINT_IDENTITY_INDEX, "run_id", unique=True),
        {"schema": AGENT_CHECKPOINT_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentCheckpoint id={self.id} run_id={self.run_id} "
            f"state_version={self.state_version} status={self.status} "
            f"next_action={self.next_action}>"
        )
