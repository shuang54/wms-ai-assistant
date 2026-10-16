"""AuthorizationGrant ORM Model（Phase 7 Recovery M2.4 — 最小 RBAC 授权关系）。

设计目标（任务书 §四 / §五）：

    (user_id, project_id) → role         项目级授权
    (user_id, system_scope) → SYSTEM_OPS  系统级授权

**单表 + 显式 ``scope_type``**（M2.4 D1 决策）：

    唯一合法组合（**数据库层强制**，不是应用层约定）：

        PROJECT + MEMBER      + 非空 project_id
        PROJECT + PROJECT_OPS + 非空 project_id
        SYSTEM  + SYSTEM_OPS  + NULL project_id

    其他组合一律被 ``ck_authorization_grant_scope_role`` 拒绝写入。

安全语义（**本项目最重要的约束**）：

    * ``project_id IS NULL`` 的**唯一**合法解释是 ``scope_type = 'SYSTEM'``；
      ``PROJECT`` 作用域行的 ``project_id`` 为 NULL 会在**写入侧**被拒绝 ⇒
      "项目级权限通过 NULL 或缺失记录隐式升级为系统级"在结构上不可能；
    * ``PROJECT_OPS`` **绝不**推导为 ``SYSTEM_OPS``（不同 scope_type 的行）；
    * ``SYSTEM_OPS`` **绝不**推导为任意项目成员（系统级行没有 project_id，
      项目级判定查不到它）；
    * 缺少授权行 = **无授权**（绝不回退默认放行）。

唯一性（防重复授权；沿用 ``llm_usage_record`` 的 partial unique 先例）：

    PostgreSQL 的 ``UNIQUE`` 视 NULL 为互不相等 ⇒ 单一 ``UNIQUE(user_id,
    project_id, role)`` **无法**阻止重复的 SYSTEM 行（project_id 全为 NULL）。
    因此使用两个 **partial unique index**：

        uq_authorization_grant_project  UNIQUE(user_id, project_id, role)
                                          WHERE scope_type = 'PROJECT'
        uq_authorization_grant_system   UNIQUE(user_id, role)
                                          WHERE scope_type = 'SYSTEM'

独立 schema（与其它 ai_ops 表同轴）：

    本表位于 ``ai_ops``，**不**在业务 schema ``public`` —— AI 内部运维 /
    授权数据不得进入 Text-to-SQL 的业务 schema 发现范围。

建表方式（沿用项目既有机制，**无** Alembic、**无** Migration Framework）：

    ``db/models/__init__.py`` 的 ``_MODELS`` 注册
        ↓
    ``init_db()`` → ``Base.metadata.create_all(bind=conn)``（幂等：
    既有库只补缺失的新表，不改任何既有表结构）
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

#: AI 内部运维 schema（与其它 ai_ops 表同轴；**不得**进入业务 schema public）。
AUTHORIZATION_SCHEMA: Final[str] = "ai_ops"

#: 表名（``ai_ops.authorization_grant``）。
AUTHORIZATION_GRANT_TABLE: Final[str] = "authorization_grant"

# ---- scope_type ----------------------------------------------------------

#: 项目级作用域（必须携带非空 project_id）。
SCOPE_PROJECT: Final[str] = "PROJECT"

#: 系统级作用域（project_id 必须为 NULL）。
SCOPE_SYSTEM: Final[str] = "SYSTEM"

AUTHORIZATION_SCOPE_TYPES: Final[frozenset[str]] = frozenset(
    {SCOPE_PROJECT, SCOPE_SYSTEM}
)

# ---- 角色 ----------------------------------------------------------------

#: 项目成员（最小项目级身份）。
PROJECT_ROLE_MEMBER: Final[str] = "MEMBER"

#: 项目运维（项目级；**不**等于系统级运维）。
PROJECT_ROLE_PROJECT_OPS: Final[str] = "PROJECT_OPS"

#: 系统级运维（全局 Usage / Trace / Timeline）。
SYSTEM_ROLE_SYSTEM_OPS: Final[str] = "SYSTEM_OPS"

PROJECT_ROLES: Final[frozenset[str]] = frozenset(
    {PROJECT_ROLE_MEMBER, PROJECT_ROLE_PROJECT_OPS}
)

SYSTEM_ROLES: Final[frozenset[str]] = frozenset({SYSTEM_ROLE_SYSTEM_OPS})

ALL_ROLES: Final[frozenset[str]] = PROJECT_ROLES | SYSTEM_ROLES

# ---- 列宽（与既有表对齐，避免跨表比较出现隐式转换）---------------------

#: 与 ``ai_ops.app_user.user_id`` 列宽一致。
AUTHORIZATION_USER_ID_MAX_LENGTH: Final[int] = 64

#: 与 ``tool_execution_record.project_id`` / ``resource_owner.project_id`` 一致。
AUTHORIZATION_PROJECT_ID_MAX_LENGTH: Final[int] = 128

AUTHORIZATION_SCOPE_TYPE_MAX_LENGTH: Final[int] = 16
AUTHORIZATION_ROLE_MAX_LENGTH: Final[int] = 32

# ---- 约束 / 索引名 -------------------------------------------------------

#: scope ↔ role ↔ project_id 三者一致性（**数据库级**强约束）。
AUTHORIZATION_SCOPE_ROLE_CHECK: Final[str] = "ck_authorization_grant_scope_role"

#: 项目级唯一（partial）。
AUTHORIZATION_PROJECT_UNIQUE_INDEX: Final[str] = "uq_authorization_grant_project"

#: 系统级唯一（partial）。
AUTHORIZATION_SYSTEM_UNIQUE_INDEX: Final[str] = "uq_authorization_grant_system"

#: 按用户查询（授权判定入口；避免全表扫描）。
AUTHORIZATION_USER_INDEX: Final[str] = "ix_authorization_grant_user_id"

#: 复合 CHECK 谓词（模型与测试共用同一字面量，避免两套真相）。
#:
#: 读法：要么是一个合法的 PROJECT 行，要么是一个合法的 SYSTEM 行。
AUTHORIZATION_SCOPE_ROLE_PREDICATE: Final[str] = (
    "(scope_type = 'PROJECT'"
    " AND project_id IS NOT NULL"
    " AND length(btrim(project_id)) > 0"
    " AND role IN ('MEMBER', 'PROJECT_OPS'))"
    " OR "
    "(scope_type = 'SYSTEM'"
    " AND project_id IS NULL"
    " AND role = 'SYSTEM_OPS')"
)


class AuthorizationGrant(Base):
    """一行 = 一个显式授权（用户 × 作用域 × 角色；**无**隐式推导）。

    位于 ``ai_ops`` schema（**不是** public）：AI 内部授权数据。
    """

    __tablename__ = AUTHORIZATION_GRANT_TABLE

    # ---- 代理主键（内部映射行；不是业务 ID）----
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="代理主键（内部行号；不是业务 ID）",
    )

    # ---- 主体（服务端签发的 user_id；无 FK，沿用 ai_ops 表风格）----
    user_id: Mapped[str] = mapped_column(
        String(AUTHORIZATION_USER_ID_MAX_LENGTH),
        nullable=False,
        comment="被授权用户（ai_ops.app_user.user_id；无 FK）",
    )

    # ---- 作用域（显式；**不**靠 project_id 是否为 NULL 推断）----
    scope_type: Mapped[str] = mapped_column(
        String(AUTHORIZATION_SCOPE_TYPE_MAX_LENGTH),
        nullable=False,
        comment="作用域类型：PROJECT / SYSTEM（判定依据，禁止从 NULL 推断）",
    )

    # ---- 项目（PROJECT ⇒ 非空；SYSTEM ⇒ 必须 NULL）----
    project_id: Mapped[str | None] = mapped_column(
        String(AUTHORIZATION_PROJECT_ID_MAX_LENGTH),
        nullable=True,
        comment=(
            "项目 ID；PROJECT 作用域必须非空，SYSTEM 作用域必须为 NULL"
            "（由复合 CHECK 强制）"
        ),
    )

    # ---- 角色（与 scope_type 组合受 CHECK 约束）----
    role: Mapped[str] = mapped_column(
        String(AUTHORIZATION_ROLE_MAX_LENGTH),
        nullable=False,
        comment=(
            "角色：PROJECT → MEMBER / PROJECT_OPS；SYSTEM → SYSTEM_OPS"
            "（组合由复合 CHECK 强制）"
        ),
    )

    # ---- 时间戳（DB 端；timezone-aware）----
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="授权登记时间",
    )

    __table_args__ = (
        CheckConstraint(
            AUTHORIZATION_SCOPE_ROLE_PREDICATE,
            name=AUTHORIZATION_SCOPE_ROLE_CHECK,
        ),
        # 项目级唯一（partial）：同一用户在同一项目的同一角色只允许一行。
        Index(
            AUTHORIZATION_PROJECT_UNIQUE_INDEX,
            "user_id",
            "project_id",
            "role",
            unique=True,
            postgresql_where=text("scope_type = 'PROJECT'"),
        ),
        # 系统级唯一（partial）：PG 视 NULL 互不相等 ⇒ 必须用 partial index。
        Index(
            AUTHORIZATION_SYSTEM_UNIQUE_INDEX,
            "user_id",
            "role",
            unique=True,
            postgresql_where=text("scope_type = 'SYSTEM'"),
        ),
        # 授权判定按 user_id 入口（避免全表扫描）。
        Index(AUTHORIZATION_USER_INDEX, "user_id"),
        {"schema": AUTHORIZATION_SCHEMA},
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AuthorizationGrant user_id={self.user_id} "
            f"scope_type={self.scope_type} project_id={self.project_id} "
            f"role={self.role}>"
        )


__all__ = [
    "AuthorizationGrant",
    "AUTHORIZATION_SCHEMA",
    "AUTHORIZATION_GRANT_TABLE",
    "SCOPE_PROJECT",
    "SCOPE_SYSTEM",
    "AUTHORIZATION_SCOPE_TYPES",
    "PROJECT_ROLE_MEMBER",
    "PROJECT_ROLE_PROJECT_OPS",
    "SYSTEM_ROLE_SYSTEM_OPS",
    "PROJECT_ROLES",
    "SYSTEM_ROLES",
    "ALL_ROLES",
    "AUTHORIZATION_USER_ID_MAX_LENGTH",
    "AUTHORIZATION_PROJECT_ID_MAX_LENGTH",
    "AUTHORIZATION_SCOPE_TYPE_MAX_LENGTH",
    "AUTHORIZATION_ROLE_MAX_LENGTH",
    "AUTHORIZATION_SCOPE_ROLE_CHECK",
    "AUTHORIZATION_SCOPE_ROLE_PREDICATE",
    "AUTHORIZATION_PROJECT_UNIQUE_INDEX",
    "AUTHORIZATION_SYSTEM_UNIQUE_INDEX",
    "AUTHORIZATION_USER_INDEX",
]
