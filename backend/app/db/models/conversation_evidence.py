"""Conversation ↔ Evidence 关联表 ORM（Phase 4.1 Step 45）。

Step 44 冻结的边界（本表必须遵守）：

```text
Conversation ≠ Evidence Owner        → 本表是 **Reference**，不是 ownership
Conversation ≠ Evidence Identity     → 不得把 conversation_id 写进 evidence_record
Conversation ≠ Evidence Provenance   → 本表**不含** dataset_version / source_type
No cascade lifecycle coupling        → ON DELETE 只删关联行，绝不删对方父对象
```

OD 决策（Step 45 前置冻结）：

* **OD-11 = FROZEN**：`Conversation → 0..N Evidence`、`Evidence → 0..N Conversation`
  （Evidence 是 Reusable Artifact；双向 0..N 只能通过关联表表达 → Step 44 Candidate B）；
* **OD-12 = DEFERRED TO STEP 46**：本表**不含** `turn_id` / `assistant_request_id`
  （turn-level 关联待真实 Runtime E2E 决策，不是永久拒绝）；
* **OD-13 = IDEMPOTENT**：`(conversation_id, evidence_id)` 唯一
  （复合主键即唯一约束；重复写入 → 返回既有行，不产生第二条）。

字段（最小）：

```text
conversation_id  FK → ai_ops.conversation.conversation_id      ON DELETE CASCADE
evidence_id      FK → ai_ops.evidence_record.evidence_id       ON DELETE CASCADE
created_at       关联建立时间（server_default now()；无 updated_at —— 关联不可变）
```

**不含**：独立自增/字符串 ID（关联记录无独立身份）、turn_id、assistant_request_id、
dataset_version、source_type、raw_content 等任何 Evidence 内容字段。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import DateTime, ForeignKey, Index, PrimaryKeyConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.conversation import (
    CONVERSATION_SCHEMA,
    CONVERSATION_TABLE,
)
from backend.app.db.models.evidence_record import EVIDENCE_SCHEMA, EVIDENCE_TABLE

#: 关联表名（ai_ops schema）。
CONVERSATION_EVIDENCE_TABLE: Final[str] = "conversation_evidence"

#: schema（与 Conversation / Evidence 同为 ai_ops）。
CONVERSATION_EVIDENCE_SCHEMA: Final[str] = CONVERSATION_SCHEMA

#: 复合主键名（同时承担 OD-13 的唯一约束语义）。
CONVERSATION_EVIDENCE_PK: Final[str] = "pk_conversation_evidence"

#: evidence_id 反向查询索引（支持 Evidence → Conversations）。
CONVERSATION_EVIDENCE_EVIDENCE_INDEX: Final[str] = "ix_conversation_evidence_evidence_id"

_CONVERSATION_ID_MAX_LENGTH: Final[int] = 128
_EVIDENCE_ID_MAX_LENGTH: Final[int] = 128


class ConversationEvidenceRecord(Base):
    """Conversation 对 Evidence 的**引用**（一条 = 一次引用关系）。

    不是 Evidence 本体、不是 Evidence 副本、不是 Provenance。
    """

    __tablename__ = CONVERSATION_EVIDENCE_TABLE

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey(
            f"{CONVERSATION_SCHEMA}.{CONVERSATION_TABLE}.conversation_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
        nullable=False,
        comment=(
            "引用方 Conversation（FK → ai_ops.conversation.conversation_id；"
            "ON DELETE CASCADE 只删关联行，**不**删 Evidence）"
        ),
    )

    evidence_id: Mapped[str] = mapped_column(
        ForeignKey(
            f"{EVIDENCE_SCHEMA}.{EVIDENCE_TABLE}.evidence_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
        nullable=False,
        comment=(
            "被引用的 Evidence（FK → ai_ops.evidence_record.evidence_id；"
            "ON DELETE CASCADE 只删关联行，**不**删 Conversation；"
            "Evidence 可被多个 Conversation 引用 = Reusable）"
        ),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="关联建立时间（关联不可变 → 无 updated_at）",
    )

    __table_args__ = (
        PrimaryKeyConstraint(
            "conversation_id",
            "evidence_id",
            name=CONVERSATION_EVIDENCE_PK,
        ),
        # conversation_id 为复合主键左列（自带索引）；evidence_id 需独立索引
        # 以支持 Evidence → Conversation 反向查询。
        Index(CONVERSATION_EVIDENCE_EVIDENCE_INDEX, "evidence_id"),
        {"schema": CONVERSATION_EVIDENCE_SCHEMA},
    )


__all__ = [
    "CONVERSATION_EVIDENCE_TABLE",
    "CONVERSATION_EVIDENCE_SCHEMA",
    "CONVERSATION_EVIDENCE_PK",
    "CONVERSATION_EVIDENCE_EVIDENCE_INDEX",
    "ConversationEvidenceRecord",
]
