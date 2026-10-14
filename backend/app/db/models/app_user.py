"""AppUser ORM Model（Phase 7 Recovery M1 — 用户身份地基）。

设计约束（**不修改任何既有冻结表**——Phase 4.1 Conversation 五字段白名单 /
Phase 5 agent_run 冻结均明确排除 user_id）：

    * 用户身份以**独立新表**落地（``ai_ops.app_user``），与既有资源表**零耦合**；
      资源归属经 ``ai_ops.resource_owner`` 映射表表达（见
      ``resource_owner.py``），不向 conversation / agent_run 等冻结表加列。

安全约束：

    * ``user_id`` = uuid4 文本（不可预测；**不**用自增序号——可枚举）；
    * ``username`` 唯一（服务端大小写敏感比较；不做大小写折叠）；
    * ``password_hash`` 只存 scrypt 哈希结果（``backend.app.auth.password``），
      **永不存明文**；本列**不**进入任何 API 响应 / 日志 / 异常文本；
    * ``locked_until`` / ``failed_login_count`` = 登录尝试限制的持久化状态
      （配置见 ``AuthSettings``；重启后仍生效）；
    * **无** email / phone / role / tenant / org 列——RBAC 与组织模型属后续
      阶段（``docs/security.md`` §4 记录的计划项），本阶段不预埋。

建表方式（沿用项目既有机制，无 Alembic）：

    ``db/models/__init__.py`` 的 ``_MODELS`` 注册
        ↓
    ``init_db()`` → ``Base.metadata.create_all(bind=conn)``（幂等）
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import DateTime, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

#: AI 内部运维 schema（与其它 ai_ops 表同轴；**不得**进入业务 schema public）。
APP_USER_SCHEMA: Final[str] = "ai_ops"

#: 表名（``ai_ops.app_user``）。
APP_USER_TABLE: Final[str] = "app_user"

#: ``user_id`` 列宽（uuid4 文本 36 字符；留余量，写入前由 Repository 校验）。
APP_USER_ID_MAX_LENGTH: Final[int] = 64

#: ``username`` 列宽（登录名；唯一）。
APP_USER_USERNAME_MAX_LENGTH: Final[int] = 64

#: ``password_hash`` 列宽（scrypt 格式串：``scrypt$N$r$p$salt$hash``）。
APP_USER_PASSWORD_HASH_MAX_LENGTH: Final[int] = 255

#: ``display_name`` 列宽（展示名；可空语义由 NOT NULL default '' 表达）。
APP_USER_DISPLAY_NAME_MAX_LENGTH: Final[int] = 128

#: ``status`` 列宽。
APP_USER_STATUS_MAX_LENGTH: Final[int] = 32

#: 状态取值（仅两态；**不**新增 DELETED——停用即 DISABLED）。
APP_USER_STATUS_ACTIVE: Final[str] = "ACTIVE"
APP_USER_STATUS_DISABLED: Final[str] = "DISABLED"
APP_USER_STATUSES: Final[frozenset[str]] = frozenset(
    {APP_USER_STATUS_ACTIVE, APP_USER_STATUS_DISABLED}
)

#: ``username`` 唯一索引名（标准 UNIQUE；大小写敏感）。
APP_USER_USERNAME_INDEX: Final[str] = "uq_app_user_username"


class AppUser(Base):
    """一个可登录的用户（一行 = 一个用户；不含角色 / 组织 / 租户信息）。"""

    __tablename__ = APP_USER_TABLE

    # ---- 主键（服务端签发的 uuid4 文本；不可枚举）----
    user_id: Mapped[str] = mapped_column(
        String(APP_USER_ID_MAX_LENGTH),
        primary_key=True,
        nullable=False,
        comment="用户 ID（服务端生成 uuid4 文本；唯一 / 不可变 / 不可枚举）",
    )

    # ---- 登录名（唯一；不做大小写折叠）----
    username: Mapped[str] = mapped_column(
        String(APP_USER_USERNAME_MAX_LENGTH),
        unique=True,
        nullable=False,
        comment="登录名（唯一；服务端精确比较；禁止空白-only）",
    )

    # ---- 密码哈希（scrypt；**永不存明文**）----
    password_hash: Mapped[str] = mapped_column(
        String(APP_USER_PASSWORD_HASH_MAX_LENGTH),
        nullable=False,
        comment=(
            "密码哈希（backend.app.auth.password 的 scrypt 格式串；"
            "禁止明文；禁止进入响应 / 日志 / 异常）"
        ),
    )

    # ---- 展示名（可空语义由默认空串表达；NOT NULL 保持列简单）----
    display_name: Mapped[str] = mapped_column(
        String(APP_USER_DISPLAY_NAME_MAX_LENGTH),
        nullable=False,
        default="",
        server_default="",
        comment="展示名（默认空串；不等于登录名）",
    )

    # ---- 状态（ACTIVE / DISABLED；停用即拒绝登录）----
    status: Mapped[str] = mapped_column(
        String(APP_USER_STATUS_MAX_LENGTH),
        nullable=False,
        default=APP_USER_STATUS_ACTIVE,
        server_default=APP_USER_STATUS_ACTIVE,
        comment="用户状态：ACTIVE / DISABLED（DISABLED 一律拒绝登录）",
    )

    # ---- 登录尝试限制状态（持久化；重启后仍生效）----
    failed_login_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="连续登录失败次数（成功登录即清零）",
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="锁定截止时间（NULL = 未锁定；达到失败上限时由服务端设置）",
    )

    # ---- 时间戳（DB 端；timezone-aware）----
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="用户创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="最近一次变更时间（登录成功 / 失败计数 / 锁定 / 状态）",
    )

    __table_args__ = ({"schema": APP_USER_SCHEMA},)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AppUser user_id={self.user_id} "
            f"username={self.username} status={self.status}>"
        )


__all__ = [
    "AppUser",
    "APP_USER_SCHEMA",
    "APP_USER_TABLE",
    "APP_USER_ID_MAX_LENGTH",
    "APP_USER_USERNAME_MAX_LENGTH",
    "APP_USER_PASSWORD_HASH_MAX_LENGTH",
    "APP_USER_DISPLAY_NAME_MAX_LENGTH",
    "APP_USER_STATUS_MAX_LENGTH",
    "APP_USER_STATUS_ACTIVE",
    "APP_USER_STATUS_DISABLED",
    "APP_USER_STATUSES",
    "APP_USER_USERNAME_INDEX",
]
