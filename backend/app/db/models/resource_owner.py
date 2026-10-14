"""ResourceOwner ORM Model（Phase 7 Recovery M1 — 用户 ↔ 资源归属映射）。

为什么用**映射表**而不是给 conversation / agent_run 加 ``user_id`` 列：

    * Phase 4.1 Step 2 冻结的 Conversation 五字段白名单**明确排除** user_id
      （``db/models/conversation.py`` docstring：不新增 user_id / tenant_id …）；
    * Phase 5 ``agent_run`` 模型标注"已冻结，不得就地重设计"（只承载 run 级
      状态 + counts + 稳定码）；
    * 两类资源的子表（turn / step / decision / tool_result / node_run）经
      **父资源 ID** 即可继承归属 ⇒ 只需在根资源上登记一次。

语义：

    * 一行 = 一个根资源（``resource_type`` + ``resource_id``）的**唯一**归属；
    * ``project_id`` 为**服务端**从资源本体复制的值（用于项目作用域交叉校验；
      客户端不可主张）；
    * 历史资源（M1 之前创建）**没有行** ⇒ 用户上下文访问一律拒绝（fail-closed）；
      既有 API-Key 路径不受影响（无用户上下文即不做归属校验）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

#: AI 内部运维 schema（与其它 ai_ops 表同轴）。
RESOURCE_OWNER_SCHEMA: Final[str] = "ai_ops"

#: 表名（``ai_ops.resource_owner``）。
RESOURCE_OWNER_TABLE: Final[str] = "resource_owner"

#: 资源类型（白名单；**不**接受任意字符串——防映射表被写入无关资源）。
RESOURCE_TYPE_CONVERSATION: Final[str] = "CONVERSATION"
RESOURCE_TYPE_AGENT_RUN: Final[str] = "AGENT_RUN"
RESOURCE_TYPE_WORKFLOW_RUN: Final[str] = "WORKFLOW_RUN"
RESOURCE_OWNER_RESOURCE_TYPES: Final[frozenset[str]] = frozenset(
    {
        RESOURCE_TYPE_CONVERSATION,
        RESOURCE_TYPE_AGENT_RUN,
        RESOURCE_TYPE_WORKFLOW_RUN,
    }
)

#: 列宽。
RESOURCE_OWNER_RESOURCE_TYPE_MAX_LENGTH: Final[int] = 32
RESOURCE_OWNER_RESOURCE_ID_MAX_LENGTH: Final[int] = 128
RESOURCE_OWNER_USER_ID_MAX_LENGTH: Final[int] = 64
RESOURCE_OWNER_PROJECT_ID_MAX_LENGTH: Final[int] = 128

#: 索引名。
RESOURCE_OWNER_RESOURCE_INDEX: Final[str] = "uq_resource_owner_resource"
RESOURCE_OWNER_USER_INDEX: Final[str] = "ix_resource_owner_user_id"


class ResourceOwner(Base):
    """根资源 → 归属用户的唯一映射（一行 = 一个资源一个归属）。"""

    __tablename__ = RESOURCE_OWNER_TABLE

    # ---- 代理主键（内部映射表；与观测表同风格，非业务可枚举数据）----
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="代理主键（内部映射行号；不是业务 ID）",
    )

    # ---- 资源身份（白名单类型 + 服务端签发的资源 ID）----
    resource_type: Mapped[str] = mapped_column(
        String(RESOURCE_OWNER_RESOURCE_TYPE_MAX_LENGTH),
        nullable=False,
        comment="资源类型（白名单：CONVERSATION / AGENT_RUN / WORKFLOW_RUN）",
    )
    resource_id: Mapped[str] = mapped_column(
        String(RESOURCE_OWNER_RESOURCE_ID_MAX_LENGTH),
        nullable=False,
        comment="资源 ID（服务端签发；如 conversation_id / run_id）",
    )

    # ---- 归属用户 + 服务端项目作用域副本 ----
    user_id: Mapped[str] = mapped_column(
        String(RESOURCE_OWNER_USER_ID_MAX_LENGTH),
        nullable=False,
        comment="归属用户 ID（ai_ops.app_user.user_id；无外键）",
    )
    project_id: Mapped[str] = mapped_column(
        String(RESOURCE_OWNER_PROJECT_ID_MAX_LENGTH),
        nullable=False,
        comment="项目作用域（服务端从资源本体复制；客户端不可主张）",
    )

    # ---- 时间戳 ----
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="归属登记时间（资源创建时刻）",
    )

    __table_args__ = (
        # 同一资源只允许一条归属记录（并发双写由唯一约束裁决）
        UniqueConstraint(
            "resource_type",
            "resource_id",
            name=RESOURCE_OWNER_RESOURCE_INDEX,
        ),
        # 按用户查资源（未来 Web MVP 历史列表）
        Index(RESOURCE_OWNER_USER_INDEX, "user_id"),
        {"schema": RESOURCE_OWNER_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ResourceOwner resource_type={self.resource_type} "
            f"resource_id={self.resource_id} user_id={self.user_id}>"
        )


__all__ = [
    "ResourceOwner",
    "RESOURCE_OWNER_SCHEMA",
    "RESOURCE_OWNER_TABLE",
    "RESOURCE_TYPE_CONVERSATION",
    "RESOURCE_TYPE_AGENT_RUN",
    "RESOURCE_TYPE_WORKFLOW_RUN",
    "RESOURCE_OWNER_RESOURCE_TYPES",
    "RESOURCE_OWNER_RESOURCE_TYPE_MAX_LENGTH",
    "RESOURCE_OWNER_RESOURCE_ID_MAX_LENGTH",
    "RESOURCE_OWNER_USER_ID_MAX_LENGTH",
    "RESOURCE_OWNER_PROJECT_ID_MAX_LENGTH",
    "RESOURCE_OWNER_RESOURCE_INDEX",
    "RESOURCE_OWNER_USER_INDEX",
]
