"""Conversation ORM Model（Phase 4.1 Step 5 —— 最小持久化层）。

严格对应 Step 2 冻结的 5 字段模型：

    Conversation
    ├── conversation_id   String(128) PK（服务端签发；唯一 / 不可变）
    ├── project_id        String(128) NOT NULL（创建时绑定；不可修改）
    ├── created_at        DateTime(timezone=True) server_default=now()
    ├── updated_at        DateTime(timezone=True) server_default=now()
    └── status            String(32)  server_default='ACTIVE'（ACTIVE / ARCHIVED）

设计约束（沿用现有 DB 风格，不重新发明）：

* Base = ``backend.app.db.base.Base``（``DeclarativeBase``）；
* 列声明 = ``Mapped[...]`` + ``mapped_column(...)``，每列带 ``comment``；
* 时间列 = ``DateTime(timezone=True)`` + ``server_default=func.now()``（DB 端时间）；
* 状态/角色一律 **string**，不使用 PostgreSQL ENUM；
* schema = ``ai_ops``（与 4 张观测表同轴）：Conversation 属 AI application
  infrastructure，**不得**进入业务 schema ``public``
  （否则会被 SchemaExplorer / Text-to-SQL 当作业务表）；
* 主键 = ``conversation_id``（本项目首个字符串主键）：原因见 Step 4 §5.2
  （Step 2 字段白名单禁止 surrogate id；自增序号会被枚举）；
* 不新增 title / user_id / tenant_id / metadata / last_message / message_count。

建表方式（本阶段无 migration）：

    db/models/__init__.py 的 _MODELS 注册
        ↓
    init_db() → Base.metadata.create_all(bind=conn)（幂等；ai_ops schema 已存在）
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

#: Conversation / ConversationTurn 所在 schema（与 4 张观测表同轴；非 public）。
CONVERSATION_SCHEMA: Final[str] = "ai_ops"

#: 表名（``ai_ops.conversation``）。
CONVERSATION_TABLE: Final[str] = "conversation"

#: conversation_id 长度上限（与 assistant_request_id 的 VARCHAR(128) 同风格）。
CONVERSATION_ID_MAX_LENGTH: Final[int] = 128

#: project_id 长度上限（与 tool_execution_record.project_id 同风格）。
PROJECT_ID_MAX_LENGTH: Final[int] = 128

#: status 列宽（与 knowledge_document.status 的 String(32) 同风格）。
CONVERSATION_STATUS_MAX_LENGTH: Final[int] = 32

#: 状态取值（仅两态；Step 2 §1.4 冻结）。
CONVERSATION_STATUS_ACTIVE: Final[str] = "ACTIVE"
CONVERSATION_STATUS_ARCHIVED: Final[str] = "ARCHIVED"


class Conversation(Base):
    """会话容器（一行 = 一个多轮会话；不含任何消息正文）。"""

    __tablename__ = CONVERSATION_TABLE

    # ---- 主键（服务端签发的会话 ID；不是自增，不是 UUID 列类型）----
    conversation_id: Mapped[str] = mapped_column(
        String(CONVERSATION_ID_MAX_LENGTH),
        primary_key=True,
        nullable=False,
        comment=(
            "会话 ID（服务端生成；唯一 / 不可变；"
            "不等于 assistant_request_id）"
        ),
    )

    # ---- 项目绑定（创建时确定，之后不可修改）----
    project_id: Mapped[str] = mapped_column(
        String(PROJECT_ID_MAX_LENGTH),
        nullable=False,
        comment="项目 ID（创建时绑定；非空；不可切换）",
    )

    # ---- 时间戳（DB 端；timezone-aware）----
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="会话创建时间（不是第一条消息时间）",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="最近一次有效变更时间（新增 Turn / 归档时更新）",
    )

    # ---- 状态（string；仅 ACTIVE / ARCHIVED）----
    status: Mapped[str] = mapped_column(
        String(CONVERSATION_STATUS_MAX_LENGTH),
        nullable=False,
        default=CONVERSATION_STATUS_ACTIVE,
        server_default=CONVERSATION_STATUS_ACTIVE,
        comment="会话状态：ACTIVE / ARCHIVED",
    )

    __table_args__ = ({"schema": CONVERSATION_SCHEMA},)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Conversation conversation_id={self.conversation_id} "
            f"project_id={self.project_id} status={self.status}>"
        )


__all__ = [
    "Conversation",
    "CONVERSATION_SCHEMA",
    "CONVERSATION_TABLE",
    "CONVERSATION_ID_MAX_LENGTH",
    "PROJECT_ID_MAX_LENGTH",
    "CONVERSATION_STATUS_MAX_LENGTH",
    "CONVERSATION_STATUS_ACTIVE",
    "CONVERSATION_STATUS_ARCHIVED",
]
