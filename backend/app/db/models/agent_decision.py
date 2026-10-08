"""Agent Decision ORM Model（Phase 5 P-6 — Decision Store）。

```text
ai_ops.agent_decision      一行 = 一次 Agent Run 中某一个 step 的 **LLM Decision**
```

设计依据（已冻结，不得就地重设计）：
    Phase 5 P-6 任务书 §八–§十二 / §六十七

职责边界（严格）：

    * **独立持久化对象**：Decision **不得**塞进 ``agent_checkpoint`` /
      ``agent_run`` / ``agent_step`` / ``agent_tool_result``；
    * 只保存**恢复控制流真正需要的 Decision**：decision_type / tool_name /
      tool_call_id / arguments / content / finish_reason；
    * **禁止**：system prompt / user prompt / 完整 messages / conversation
      context / RAG chunks / tool definitions / API Key / Authorization /
      Cookie / password / database URL / SQL / Tool Result body /
      LLM raw response / headers / stack trace / exception message；
    * **无外键**：``run_id`` 为 correlation（沿用 agent_run / agent_step /
      agent_tool_result / agent_checkpoint 的"correlation 不用 FK"先例）。

身份（§十二）：

    ``(run_id, step_index)`` = 执行身份（UNIQUE）；
    ``decision_ref`` = ``"{run_id}:{step_index}"`` 稳定引用（UNIQUE）；
    ``tool_call_id`` = Provider protocol correlation（**不是** 身份）。

不可用标记（§十一）：``decision_unavailable=True`` 表示该 Decision **不可安全
Replay**（arguments 超限 / 含敏感标记 / content 超长）⇒ Resume 必须 fail closed。

常量来源：类型白名单 / 长度上限的权威在 ``backend.app.agent.decision_store``
（域层）；本模块只做 ORM 映射（单向依赖，无环）。
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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.agent.decision_store import (
    DECISION_CONTENT_MAX_LENGTH,
    DECISION_FINISH_REASON_MAX_LENGTH,
    DECISION_REF_MAX_LENGTH,
    DECISION_TOOL_CALL_ID_MAX_LENGTH,
    DECISION_TOOL_NAME_MAX_LENGTH,
    DECISION_TYPE_MAX_LENGTH,
    DECISION_TYPES,
)
from backend.app.db.base import Base

__all__ = [
    "AgentDecision",
    "AGENT_DECISION_SCHEMA",
    "AGENT_DECISION_TABLE",
    "AGENT_DECISION_IDENTITY_INDEX",
    "AGENT_DECISION_REF_INDEX",
    "AGENT_DECISION_RUN_ID_MAX_LENGTH",
    "AGENT_DECISION_REF_MAX_LENGTH",
    "AGENT_DECISION_TYPE_MAX_LENGTH",
    "AGENT_DECISION_TOOL_NAME_MAX_LENGTH",
    "AGENT_DECISION_TOOL_CALL_ID_MAX_LENGTH",
    "AGENT_DECISION_CONTENT_MAX_LENGTH",
    "AGENT_DECISION_FINISH_REASON_MAX_LENGTH",
    "AGENT_DECISION_TYPES",
]

#: AI 内部运维 schema（与业务 schema `public` 物理隔离）。
AGENT_DECISION_SCHEMA: Final[str] = "ai_ops"

#: 表名常量（DB 测试 / 残留守卫共用）。
AGENT_DECISION_TABLE: Final[str] = "agent_decision"

#: 执行身份唯一索引：**(run_id, step_index)**。
AGENT_DECISION_IDENTITY_INDEX: Final[str] = "uq_agent_decision_run_id_step_index"

#: 稳定引用唯一索引：**decision_ref**。
AGENT_DECISION_REF_INDEX: Final[str] = "uq_agent_decision_decision_ref"

#: 列长度上限（引用域层常量：单一权威）。
AGENT_DECISION_RUN_ID_MAX_LENGTH: Final[int] = 64
AGENT_DECISION_REF_MAX_LENGTH: Final[int] = DECISION_REF_MAX_LENGTH
AGENT_DECISION_TYPE_MAX_LENGTH: Final[int] = DECISION_TYPE_MAX_LENGTH
AGENT_DECISION_TOOL_NAME_MAX_LENGTH: Final[int] = DECISION_TOOL_NAME_MAX_LENGTH
AGENT_DECISION_TOOL_CALL_ID_MAX_LENGTH: Final[int] = DECISION_TOOL_CALL_ID_MAX_LENGTH
AGENT_DECISION_CONTENT_MAX_LENGTH: Final[int] = DECISION_CONTENT_MAX_LENGTH
AGENT_DECISION_FINISH_REASON_MAX_LENGTH: Final[int] = DECISION_FINISH_REASON_MAX_LENGTH

#: 白名单（引用域层常量）。
AGENT_DECISION_TYPES: Final[frozenset[str]] = DECISION_TYPES


class AgentDecision(Base):
    """一个 step 的 LLM Decision（append-only；first-write-wins）。

    写入时机（§十三）：LLM Decision 通过校验后、**任何** Tool 执行之前；
    写失败 ⇒ Runtime FAILED（fail-closed），不执行 Tool、不再调 LLM。
    删除：生产路径**无** delete（只提供测试用删除）。
    """

    __tablename__ = AGENT_DECISION_TABLE

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="主键（BIGINT 自增；项目既有规范）",
    )
    run_id: Mapped[str] = mapped_column(
        String(AGENT_DECISION_RUN_ID_MAX_LENGTH),
        nullable=False,
        comment="所属 Agent Run 身份（correlation，**无外键**）",
    )
    step_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Agent Step 坐标（0 起；与 run_id 组成唯一身份）",
    )
    decision_ref: Mapped[str] = mapped_column(
        String(AGENT_DECISION_REF_MAX_LENGTH),
        nullable=False,
        comment="稳定引用 '{run_id}:{step_index}'（唯一；**不是** tool_call_id）",
    )
    decision_type: Mapped[str] = mapped_column(
        String(AGENT_DECISION_TYPE_MAX_LENGTH),
        nullable=False,
        comment="FINAL / TOOL_CALL（**复用** Runtime 既有类型）",
    )
    tool_call_id: Mapped[str | None] = mapped_column(
        String(AGENT_DECISION_TOOL_CALL_ID_MAX_LENGTH),
        nullable=True,
        comment="Provider ToolCall.id（protocol correlation；**不**生成）",
    )
    tool_name: Mapped[str | None] = mapped_column(
        String(AGENT_DECISION_TOOL_NAME_MAX_LENGTH),
        nullable=True,
        comment="TOOL_CALL 的 Tool 名称（FINAL ⇒ NULL）",
    )
    arguments: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "ToolCall.arguments（JSON-safe；≤ 32 KiB 且无敏感标记时非 NULL）—— "
            "**禁止** bytes / Session / Connection / 任意 Python 对象"
        ),
    )
    content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="FINAL 的最终答案（≤ 上限且无敏感标记时非 NULL；**不**截断）",
    )
    finish_reason: Mapped[str | None] = mapped_column(
        String(AGENT_DECISION_FINISH_REASON_MAX_LENGTH),
        nullable=True,
        comment="Provider finish_reason（诊断；不含 raw response）",
    )
    decision_unavailable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        comment=(
            "该 Decision 不可安全 Replay（arguments 超限 / 敏感 / content 超长）"
            " ⇒ Resume 必须 fail closed"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="本条记录的创建时间",
    )

    __table_args__ = (
        Index(AGENT_DECISION_IDENTITY_INDEX, "run_id", "step_index", unique=True),
        Index(AGENT_DECISION_REF_INDEX, "decision_ref", unique=True),
        {"schema": AGENT_DECISION_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentDecision id={self.id} run_id={self.run_id} "
            f"step_index={self.step_index} decision_type={self.decision_type}>"
        )
