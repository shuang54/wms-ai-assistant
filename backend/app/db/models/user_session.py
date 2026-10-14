"""UserSession ORM Model（Phase 7 Recovery M1 — 服务端会话）。

会话模型（**服务端管理**；浏览器只持有不透明 Cookie 值）：

    * ``session_id``  = uuid4 文本（服务端签发；不可枚举）；
    * ``token_hash``  = 客户端 Cookie 中不透明令牌的 SHA-256（**只存哈希**，
      令牌本体只出现在 Set-Cookie 响应头一次）；
    * ``expires_at``  = 绝对过期时间（TTL 由 ``AuthSettings.session_ttl_hours``
      决定；**不**做滑动续期——最小实现，语义可测）；
    * ``revoked_at``  = 登出 / 吊销时间（NULL = 有效）。

设计约束：

    * 无外键（沿用 ``agent_*`` 表先例：correlation-only，无级联删除）；
    * 令牌**不**写入 URL / 查询参数 / 日志 / 异常文本；
    * ``token_hash`` 唯一（重复哈希 ⇒ 插入冲突 ⇒ 服务端错误，不静默复用）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

#: AI 内部运维 schema（与其它 ai_ops 表同轴）。
USER_SESSION_SCHEMA: Final[str] = "ai_ops"

#: 表名（``ai_ops.user_session``）。
USER_SESSION_TABLE: Final[str] = "user_session"

#: ``session_id`` 列宽（uuid4 文本 36 字符；留余量）。
USER_SESSION_ID_MAX_LENGTH: Final[int] = 64

#: ``user_id`` 列宽（与 app_user.user_id 对齐；correlation-only，无 FK）。
USER_SESSION_USER_ID_MAX_LENGTH: Final[int] = 64

#: ``token_hash`` 列宽（SHA-256 hex = 64 字符）。
USER_SESSION_TOKEN_HASH_MAX_LENGTH: Final[int] = 64

#: ``token_hash`` 唯一索引名。
USER_SESSION_TOKEN_HASH_INDEX: Final[str] = "uq_user_session_token_hash"

#: ``user_id`` 普通索引名（按用户查会话）。
USER_SESSION_USER_ID_INDEX: Final[str] = "ix_user_session_user_id"


class UserSession(Base):
    """一个服务端会话（一行 = 一个登录会话；可被吊销 / 会过期）。"""

    __tablename__ = USER_SESSION_TABLE

    # ---- 主键（服务端签发的 uuid4 文本）----
    session_id: Mapped[str] = mapped_column(
        String(USER_SESSION_ID_MAX_LENGTH),
        primary_key=True,
        nullable=False,
        comment="会话 ID（服务端生成 uuid4 文本；唯一 / 不可枚举）",
    )

    # ---- 归属用户（correlation-only；无 FK / 无级联）----
    user_id: Mapped[str] = mapped_column(
        String(USER_SESSION_USER_ID_MAX_LENGTH),
        nullable=False,
        index=True,
        comment="所属用户 ID（ai_ops.app_user.user_id；无外键）",
    )

    # ---- 令牌哈希（只存 SHA-256；本体只在 Set-Cookie 出现一次）----
    token_hash: Mapped[str] = mapped_column(
        String(USER_SESSION_TOKEN_HASH_MAX_LENGTH),
        unique=True,
        nullable=False,
        comment="会话令牌 SHA-256 hex（禁止存令牌本体 / 禁止进日志）",
    )

    # ---- 生命周期 ----
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="会话创建时间（登录成功时刻）",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="绝对过期时间（DB 端时间比较；过期即无效）",
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="吊销时间（NULL = 未吊销；登出即置值）",
    )

    __table_args__ = ({"schema": USER_SESSION_SCHEMA},)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<UserSession session_id={self.session_id} "
            f"user_id={self.user_id} expires_at={self.expires_at}>"
        )


__all__ = [
    "UserSession",
    "USER_SESSION_SCHEMA",
    "USER_SESSION_TABLE",
    "USER_SESSION_ID_MAX_LENGTH",
    "USER_SESSION_USER_ID_MAX_LENGTH",
    "USER_SESSION_TOKEN_HASH_MAX_LENGTH",
    "USER_SESSION_TOKEN_HASH_INDEX",
    "USER_SESSION_USER_ID_INDEX",
]
