"""AuthorizationGrant Repository（Phase 7 Recovery M2.4 — 授权关系持久化）。

最小数据访问层（与 ``resource_owner_repository`` / ``user_repository``
同风格 / 同基础设施）：

    grant_project_role(...)      授予项目级角色（幂等：已存在 ⇒ 不重复插入）
    grant_system_role(...)       授予系统级角色（幂等）
    get_project_roles(...)       只读：某用户在指定项目的角色集合（无 → 空）
    has_system_role(...)         只读：某用户是否持有指定系统级角色

复用基础设施（不新增第二套机制）：

    * Session 工厂：``backend.app.db.session.get_session_factory()``
    * 事务：写 ``with factory() as session, session.begin():`` ·
      读 ``with factory() as session:``（Repository 持有事务）
    * ORM：``backend.app.db.models.AuthorizationGrant``

**失败语义（两档，区分"未配置"与"不可用"）**：

    * ``AuthorizationGrantStoreNotConfiguredError`` —— ``DATABASE_URL`` 未配置
      （``get_session_factory()`` 返回 ``None``）。语义 = "本部署没有授权存储"，
      **不是**故障；调用方据此跳过持久化授权判定（仅使用显式配置来源），
      从而保持 M2.3 A2 在无 DB 部署下的既有行为；
    * ``AuthorizationGrantRepositoryError`` —— 已配置但查询 / 写入失败
      （``SQLAlchemyError``）。语义 = "授权存储不可用" ⇒ 调用方必须 fail closed。

安全语义：

    * 授予前在**应用层**做 scope/role/project_id 一致性校验（快速失败、
      错误可读）；数据库复合 CHECK 是**最终**防线（防止绕过 Repository 的写入）；
    * 不返回 ORM 对象（只返回 frozen Row / 值）；
    * 冲突（唯一索引）⇒ 幂等语义（``ON CONFLICT DO NOTHING``），不覆盖已有行。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models.authorization_grant import (
    ALL_ROLES,
    AUTHORIZATION_GRANT_TABLE,
    AUTHORIZATION_PROJECT_ID_MAX_LENGTH,
    AUTHORIZATION_USER_ID_MAX_LENGTH,
    PROJECT_ROLE_MEMBER,
    PROJECT_ROLE_PROJECT_OPS,
    PROJECT_ROLES,
    SCOPE_PROJECT,
    SCOPE_SYSTEM,
    SYSTEM_ROLE_SYSTEM_OPS,
    SYSTEM_ROLES,
    AuthorizationGrant,
)
from backend.app.db.session import get_session_factory

__all__ = [
    "AuthorizationGrantRepository",
    "AuthorizationGrantRepositoryError",
    "AuthorizationGrantStoreNotConfiguredError",
    "AuthorizationGrantRow",
    "GRANT_READ_COLUMNS",
    "validate_user_id",
    "validate_project_id",
    "validate_project_role",
    "validate_system_role",
]


#: 只读查询允许返回的字段（显式列；**不用** SELECT *）。
GRANT_READ_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "user_id",
    "scope_type",
    "project_id",
    "role",
    "created_at",
)


class AuthorizationGrantRepositoryError(RuntimeError):
    """授权存储已配置但不可用（查询 / 写入失败）⇒ 调用方 fail closed。"""


class AuthorizationGrantStoreNotConfiguredError(
    AuthorizationGrantRepositoryError
):
    """授权存储**未配置**（``DATABASE_URL`` 为空）。

    语义是"本部署没有授权存储"，**不是**故障 —— 与"已配置但不可用"必须区分，
    否则无 DB 部署（及离线测试）会被误判为授权故障。
    """


# ---- 校验（应用层快速失败；数据库 CHECK 是最终防线）----------------------


def validate_user_id(user_id: str) -> str:
    """user_id 校验（非空 / 去空白 / 长度上限）。返回规范化后的值。"""
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id 必须是非空 str")
    normalized = user_id.strip()
    if len(normalized) > AUTHORIZATION_USER_ID_MAX_LENGTH:
        raise ValueError(
            "user_id 长度超限"
            f"（最多 {AUTHORIZATION_USER_ID_MAX_LENGTH} 字符）"
        )
    return normalized


def validate_project_id(project_id: str) -> str:
    """project_id 校验（非空 / 去空白 / 长度上限）。返回规范化后的值。"""
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("project_id 必须是非空 str")
    normalized = project_id.strip()
    if len(normalized) > AUTHORIZATION_PROJECT_ID_MAX_LENGTH:
        raise ValueError(
            "project_id 长度超限"
            f"（最多 {AUTHORIZATION_PROJECT_ID_MAX_LENGTH} 字符）"
        )
    return normalized


def validate_project_role(role: str) -> str:
    """项目级角色校验（仅 ``MEMBER`` / ``PROJECT_OPS``）。"""
    if role not in PROJECT_ROLES:
        raise ValueError(f"未知项目级角色: {role!r}")
    return role


def validate_system_role(role: str) -> str:
    """系统级角色校验（仅 ``SYSTEM_OPS``）。"""
    if role not in SYSTEM_ROLES:
        raise ValueError(f"未知系统级角色: {role!r}")
    return role


@dataclass(frozen=True)
class AuthorizationGrantRow:
    """授权行（frozen；只承载列值，不持有 ORM / Session）。"""

    id: int
    user_id: str
    scope_type: str
    project_id: str | None
    role: str
    created_at: datetime


def _validate_grant_row(row: AuthorizationGrant) -> None:
    """读侧防御：拒绝任何不符合 scope/role 一致性的既有行。

    Raises:
        AuthorizationGrantRepositoryError: 存储中存在非法组合（数据被绕过
            Repository 写入 / 约束被删）——**不猜测**、不静默修正。
    """
    scope = row.scope_type
    role = row.role
    project_id = row.project_id

    if scope == SCOPE_PROJECT:
        ok = (
            project_id is not None
            and bool(project_id.strip())
            and role in PROJECT_ROLES
        )
    elif scope == SCOPE_SYSTEM:
        ok = project_id is None and role in SYSTEM_ROLES
    else:
        ok = False

    if not ok:
        raise AuthorizationGrantRepositoryError(
            "授权存储中存在非法 scope/role/project_id 组合（拒绝读取）"
        )


class AuthorizationGrantRepository:
    """授权关系仓储（最小读写；不提供列表 / 分页 / 删除）。"""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        """构造仓储。

        Args:
            session_factory: 可注入的 Session 工厂（测试用）；``None`` ⇒
                延迟从 :func:`get_session_factory` 获取（构造期不连库）。
        """
        self._session_factory = session_factory

    # ---- 基础设施 --------------------------------------------------------

    def _factory(self) -> sessionmaker[Session]:
        """获取 Session 工厂。

        Raises:
            AuthorizationGrantStoreNotConfiguredError: ``DATABASE_URL`` 未配置。
        """
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise AuthorizationGrantStoreNotConfiguredError(
                "DATABASE_URL 未配置，授权存储不可用"
            )
        return factory

    # ---- 写入（幂等；不覆盖既有行）---------------------------------------

    def grant_project_role(
        self, *, user_id: str, project_id: str, role: str
    ) -> bool:
        """授予项目级角色（幂等：已存在 ⇒ 返回 ``False``，不报错、不覆盖）。

        Args:
            user_id: 被授权用户（``ai_ops.app_user.user_id``）。
            project_id: 项目 ID（非空）。
            role: ``MEMBER`` / ``PROJECT_OPS``。

        Returns:
            True: 本次新增了一行。
            False: 该授权已存在（幂等）。

        Raises:
            ValueError: 参数非法（空 / 超长 / 未知角色）。
            AuthorizationGrantStoreNotConfiguredError: ``DATABASE_URL`` 未配置。
            AuthorizationGrantRepositoryError: 写入失败。
        """
        uid = validate_user_id(user_id)
        pid = validate_project_id(project_id)
        normalized_role = validate_project_role(role)

        statement = (
            pg_insert(AuthorizationGrant)
            .values(
                user_id=uid,
                scope_type=SCOPE_PROJECT,
                project_id=pid,
                role=normalized_role,
            )
            .on_conflict_do_nothing(
                index_elements=["user_id", "project_id", "role"],
                index_where=(
                    AuthorizationGrant.__table__.c.scope_type == SCOPE_PROJECT
                ),
            )
            .returning(AuthorizationGrant.id)
        )
        return self._execute_insert(statement)

    def grant_system_role(self, *, user_id: str, role: str) -> bool:
        """授予系统级角色（幂等：已存在 ⇒ 返回 ``False``）。

        Args:
            user_id: 被授权用户。
            role: 仅 ``SYSTEM_OPS``。

        Returns:
            True: 本次新增了一行。
            False: 该授权已存在（幂等）。

        Raises:
            ValueError: 参数非法。
            AuthorizationGrantStoreNotConfiguredError: ``DATABASE_URL`` 未配置。
            AuthorizationGrantRepositoryError: 写入失败。
        """
        uid = validate_user_id(user_id)
        normalized_role = validate_system_role(role)

        statement = (
            pg_insert(AuthorizationGrant)
            .values(
                user_id=uid,
                scope_type=SCOPE_SYSTEM,
                project_id=None,
                role=normalized_role,
            )
            .on_conflict_do_nothing(
                index_elements=["user_id", "role"],
                index_where=(
                    AuthorizationGrant.__table__.c.scope_type == SCOPE_SYSTEM
                ),
            )
            .returning(AuthorizationGrant.id)
        )
        return self._execute_insert(statement)

    def _execute_insert(self, statement: object) -> bool:
        """执行插入；返回是否真的新增一行。"""
        factory = self._factory()
        try:
            with factory() as session, session.begin():
                inserted = session.execute(statement).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise AuthorizationGrantRepositoryError(
                f"授权写入失败: {type(exc).__name__}"
            ) from exc
        return inserted is not None

    # ---- 读取 ------------------------------------------------------------

    def get_project_roles(
        self, *, user_id: str, project_id: str
    ) -> frozenset[str]:
        """某用户在指定项目持有的角色集合（无授权 ⇒ 空集合）。

        Raises:
            ValueError: 参数非法。
            AuthorizationGrantStoreNotConfiguredError: ``DATABASE_URL`` 未配置。
            AuthorizationGrantRepositoryError: 查询失败，或存储中存在非法行。
        """
        uid = validate_user_id(user_id)
        pid = validate_project_id(project_id)

        statement = (
            select(AuthorizationGrant)
            .where(
                AuthorizationGrant.user_id == uid,
                AuthorizationGrant.scope_type == SCOPE_PROJECT,
                AuthorizationGrant.project_id == pid,
            )
            .order_by(AuthorizationGrant.id)
        )
        return frozenset(row.role for row in self._fetch(statement))

    def has_system_role(self, *, user_id: str, role: str) -> bool:
        """某用户是否持有指定系统级角色。

        **只**匹配 ``scope_type = 'SYSTEM'`` 且 ``project_id IS NULL`` 的行；
        ``PROJECT`` 作用域的任何行都不参与判定（禁止隐式升级）。

        Raises:
            ValueError: 参数非法。
            AuthorizationGrantStoreNotConfiguredError: ``DATABASE_URL`` 未配置。
            AuthorizationGrantRepositoryError: 查询失败，或存储中存在非法行。
        """
        uid = validate_user_id(user_id)
        normalized_role = validate_system_role(role)

        statement = (
            select(AuthorizationGrant)
            .where(
                AuthorizationGrant.user_id == uid,
                AuthorizationGrant.scope_type == SCOPE_SYSTEM,
                AuthorizationGrant.project_id.is_(None),
                AuthorizationGrant.role == normalized_role,
            )
            .limit(1)
        )
        return len(self._fetch(statement)) > 0

    def _fetch(self, statement: object) -> list[AuthorizationGrant]:
        """执行只读查询并做读侧一致性防御。"""
        factory = self._factory()
        try:
            with factory() as session:
                rows = list(session.execute(statement).scalars().all())
        except SQLAlchemyError as exc:
            raise AuthorizationGrantRepositoryError(
                f"授权查询失败: {type(exc).__name__}"
            ) from exc
        for row in rows:
            _validate_grant_row(row)
        return rows


#: 供 Service / 测试引用的常量再导出（单一事实来源在 model）。
_MEMBER_ROLE: Final[str] = PROJECT_ROLE_MEMBER
_PROJECT_OPS_ROLE: Final[str] = PROJECT_ROLE_PROJECT_OPS
_SYSTEM_OPS_ROLE: Final[str] = SYSTEM_ROLE_SYSTEM_OPS
_KNOWN_ROLES: Final[frozenset[str]] = ALL_ROLES
_TABLE_NAME: Final[str] = AUTHORIZATION_GRANT_TABLE
