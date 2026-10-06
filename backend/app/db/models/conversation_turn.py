"""ConversationTurn ORM Model（Phase 4.1 Step 5 —— 最小持久化层）。

Step 2 冻结的 6 字段模型 + Phase 4.2 Step 6 新增 1 个幂等列：

    ConversationTurn（一行 = 一条消息；一次对话回合 = USER + ASSISTANT 两行）
    ├── turn_id               BigInteger PK 自增
    ├── conversation_id       String(128) NOT NULL FK → conversation.conversation_id（ON DELETE CASCADE）
    ├── role                  String(32) NOT NULL（USER / ASSISTANT）
    ├── content               Text NOT NULL（仅用户可见文本）
    ├── assistant_request_id  String(128) NULL（correlation；**不是** ForeignKey）
    ├── idempotency_key       String(128) NULL（Phase 4.2 Step 6；仅 USER Turn；不进入任何 ID 体系）
    └── created_at            DateTime(timezone=True) server_default=now()

关键边界：

* ``turn_id`` 必须使用项目 BigInteger 自增实践（禁止 UUID / String /
  assistant_request_id 作为 Turn 主键）；
* ``assistant_request_id`` **绝不建立数据库外键** —— 它只是 correlation ID：
  Observability（llm_usage / tool_execution / rag_execution / outcome）的
  生命周期与 Conversation 生命周期相互独立（Step 1 §15 / Step 2 §11）；
* 唯一外键 = ``conversation_id → conversation``（ON DELETE CASCADE）：
  物理删除会话时连带删除其 Turns，且**只**作用于 conversation_turn 一张表；
* 索引只有：主键 turn_id + ``ix_conversation_turn_conversation_id_created_at``
  （``(conversation_id, created_at)``，服务 ``list_turns_by_conversation_id``）；
* 读取顺序固定 ``created_at ASC, turn_id ASC``（Repository 保证）。
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
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.conversation import (
    CONVERSATION_ID_MAX_LENGTH,
    CONVERSATION_SCHEMA,
    CONVERSATION_TABLE,
)

#: 表名（``ai_ops.conversation_turn``；与 conversation 同一 schema）。
CONVERSATION_TURN_TABLE: Final[str] = "conversation_turn"

#: role 列宽（与 status 同风格：String(32)）。
TURN_ROLE_MAX_LENGTH: Final[int] = 32

#: role 取值（仅两态；Step 2 §4 冻结）。
TURN_ROLE_USER: Final[str] = "USER"
TURN_ROLE_ASSISTANT: Final[str] = "ASSISTANT"

#: assistant_request_id 列宽（与观测表同名的 VARCHAR(128) 一致）。
ASSISTANT_REQUEST_ID_MAX_LENGTH: Final[int] = 128

#: idempotency_key 列宽（Phase 4.2 Step 6；客户端请求幂等键，服务端不生成）。
IDEMPOTENCY_KEY_MAX_LENGTH: Final[int] = 128

#: 唯一复合索引（列表 + 时间排序；Step 2 §14 / Step 4 §8 冻结）。
CONVERSATION_TURN_INDEX: Final[str] = (
    "ix_conversation_turn_conversation_id_created_at"
)

#: 幂等唯一索引（Phase 4.2 Step 6；``UNIQUE(conversation_id, idempotency_key)``）。
#:
#: * **非 partial · 非 NULLS NOT DISTINCT**：PostgreSQL 标准 UNIQUE 下 NULL
#:   互不冲突 ⇒ 历史行（``idempotency_key IS NULL``）与无幂等请求**不受约束**；
#: * 幂等键**只写 USER Turn**（ASSISTANT 恒 NULL）⇒ 同一请求在库内只有一行带键；
#: * scope = conversation ⇒ 不同会话的同名 key 互相独立。
CONVERSATION_TURN_IDEMPOTENCY_INDEX: Final[str] = (
    "uq_conversation_turn_conversation_id_idempotency_key"
)


class ConversationTurn(Base):
    """会话消息行（一条消息一行；USER 与 ASSISTANT 各占一行）。"""

    __tablename__ = CONVERSATION_TURN_TABLE

    # ---- 主键（BIGINT 自增；项目既有规范）----
    turn_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="Turn 主键（BIGINT 自增；不是 assistant_request_id）",
    )

    # ---- 外键（唯一一个 FK；删除会话 → CASCADE 删除 Turns）----
    conversation_id: Mapped[str] = mapped_column(
        String(CONVERSATION_ID_MAX_LENGTH),
        ForeignKey(
            f"{CONVERSATION_SCHEMA}.{CONVERSATION_TABLE}.conversation_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        comment=(
            "所属会话（FK → ai_ops.conversation.conversation_id；"
            "ON DELETE CASCADE）"
        ),
    )

    # ---- 角色（string；仅 USER / ASSISTANT）----
    role: Mapped[str] = mapped_column(
        String(TURN_ROLE_MAX_LENGTH),
        nullable=False,
        comment="消息角色：USER / ASSISTANT",
    )

    # ---- 正文（仅用户可见文本；不保存 prompt / SQL / chunks / embedding）----
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="消息正文（用户输入 / Assistant 最终回答；不含 LLM prompt）",
    )

    # ---- 关联（correlation only；**不是**外键）----
    assistant_request_id: Mapped[str | None] = mapped_column(
        String(ASSISTANT_REQUEST_ID_MAX_LENGTH),
        nullable=True,
        comment=(
            "Assistant Trace ID（correlation；USER 为 NULL；"
            "对观测表**不**建 FK）"
        ),
    )

    # ---- 请求幂等键（Phase 4.2 Step 6；**只写 USER Turn**）----
    idempotency_key: Mapped[str | None] = mapped_column(
        String(IDEMPOTENCY_KEY_MAX_LENGTH),
        nullable=True,
        comment=(
            "客户端请求幂等键（Header Idempotency-Key；仅 USER Turn；"
            "NULL = 无幂等；UNIQUE(conversation_id, idempotency_key)）"
        ),
    )

    # ---- 时间戳 ----
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="本条消息的写入时间",
    )

    __table_args__ = (
        Index(CONVERSATION_TURN_INDEX, "conversation_id", "created_at"),
        Index(
            CONVERSATION_TURN_IDEMPOTENCY_INDEX,
            "conversation_id",
            "idempotency_key",
            unique=True,
        ),
        {"schema": CONVERSATION_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ConversationTurn turn_id={self.turn_id} "
            f"conversation_id={self.conversation_id} role={self.role}>"
        )


__all__ = [
    "ConversationTurn",
    "CONVERSATION_TURN_TABLE",
    "CONVERSATION_TURN_INDEX",
    "CONVERSATION_TURN_IDEMPOTENCY_INDEX",
    "TURN_ROLE_MAX_LENGTH",
    "TURN_ROLE_USER",
    "TURN_ROLE_ASSISTANT",
    "ASSISTANT_REQUEST_ID_MAX_LENGTH",
    "IDEMPOTENCY_KEY_MAX_LENGTH",
]
