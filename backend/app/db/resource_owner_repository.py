"""ResourceOwner Repository（Phase 7 Recovery M1 — 用户 ↔ 资源归属映射）。

最小数据访问层（与 ``conversation_repository`` 同风格 / 同基础设施）：

    bind(...)                 登记归属（同一资源重复绑定同用户 ⇒ 幂等；
                              不同用户 ⇒ ``ResourceOwnerConflictError``）
    get(...)                  只读：查归属（无 → None）
    list_for_user(...)        只读：某用户的资源列表（按创建时间倒序；
                              上限 ``max_limit``；供未来 Web MVP 历史列表）

复用基础设施：``backend.app.db.session.get_session_factory()``；
事务归属 = Repository（写 ``session.begin()`` / 读无事务）。

语义（fail-closed 读取的核心）：

    * ``get()`` 返回 ``None`` ⇒ 资源**没有归属登记**（M1 之前创建的历史资源，
      或 API-Key 服务路径创建）⇒ 用户上下文访问必须拒绝；
    * 绑定冲突（同资源不同用户）不覆盖、不静默改主——显式报错。

失败语义：``SQLAlchemyError`` → ``ResourceOwnerRepositoryError``。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models.resource_owner import (
    RESOURCE_OWNER_PROJECT_ID_MAX_LENGTH,
    RESOURCE_OWNER_RESOURCE_ID_MAX_LENGTH,
    RESOURCE_OWNER_RESOURCE_TYPES,
    RESOURCE_OWNER_TABLE,
    RESOURCE_OWNER_USER_ID_MAX_LENGTH,
    ResourceOwner,
)
from backend.app.db.session import get_session_factory

__all__ = [
    "ResourceOwnerRepository",
    "ResourceOwnerRepositoryError",
    "ResourceOwnerConflictError",
    "ResourceOwnerRow",
    "RESOURCE_TYPES",
    "USER_RESOURCE_LIST_MAX_LIMIT",
    "validate_resource_type",
    "validate_resource_id",
    "validate_owner_user_id",
    "validate_owner_project_id",
]

#: 资源类型白名单（与模型常量同源；Service / API 共用）。
RESOURCE_TYPES: Final[frozenset[str]] = RESOURCE_OWNER_RESOURCE_TYPES

#: ``list_for_user`` 硬上限（防无界查询）。
USER_RESOURCE_LIST_MAX_LIMIT: Final[int] = 200


def validate_resource_type(resource_type: str) -> str:
    """资源类型校验（白名单；拒绝任意字符串）。"""
    if resource_type not in RESOURCE_TYPES:
        raise ValueError(f"未知资源类型: {resource_type}")
    return resource_type


def validate_resource_id(resource_id: str) -> str:
    """资源 ID 校验（非空 / 长度上限）。"""
    if not isinstance(resource_id, str) or not resource_id.strip():
        raise ValueError("resource_id 必须是非空 str")
    if len(resource_id) > RESOURCE_OWNER_RESOURCE_ID_MAX_LENGTH:
        raise ValueError(
            "resource_id 长度超限"
            f"（最多 {RESOURCE_OWNER_RESOURCE_ID_MAX_LENGTH} 字符）"
        )
    return resource_id


def validate_owner_user_id(user_id: str) -> str:
    """归属用户 ID 校验（非空 / 长度上限）。"""
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("user_id 必须是非空 str")
    if len(user_id) > RESOURCE_OWNER_USER_ID_MAX_LENGTH:
        raise ValueError(
            f"user_id 长度超限（最多 {RESOURCE_OWNER_USER_ID_MAX_LENGTH} 字符）"
        )
    return user_id


def validate_owner_project_id(project_id: str) -> str:
    """项目 ID 校验（非空 / 长度上限；服务端来源）。"""
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("project_id 必须是非空 str")
    if len(project_id) > RESOURCE_OWNER_PROJECT_ID_MAX_LENGTH:
        raise ValueError(
            "project_id 长度超限"
            f"（最多 {RESOURCE_OWNER_PROJECT_ID_MAX_LENGTH} 字符）"
        )
    return project_id


@dataclass(frozen=True)
class ResourceOwnerRow:
    """归属映射行（frozen）。"""

    id: int
    resource_type: str
    resource_id: str
    user_id: str
    project_id: str
    created_at: datetime


class ResourceOwnerRepositoryError(RuntimeError):
    """归属持久化失败（事务已回滚；不含 SQL / 连接串）。"""


class ResourceOwnerConflictError(ResourceOwnerRepositoryError):
    """同一资源已被**其他用户**绑定（不覆盖 / 不静默改主）。"""


class ResourceOwnerRepository:
    """``ai_ops.resource_owner`` 最小数据访问层。"""

    def __init__(
        self, session_factory: sessionmaker[Session] | None = None
    ) -> None:
        self._session_factory = session_factory or get_session_factory()

    # ---------- 写 ----------

    def bind(
        self,
        *,
        resource_type: str,
        resource_id: str,
        user_id: str,
        project_id: str,
    ) -> ResourceOwnerRow:
        """登记归属（幂等：同用户重复绑定返回既有行；异用户 ⇒ 冲突错误）。"""
        resource_type = validate_resource_type(resource_type)
        resource_id = validate_resource_id(resource_id)
        user_id = validate_owner_user_id(user_id)
        project_id = validate_owner_project_id(project_id)

        existing = self.get(resource_type=resource_type, resource_id=resource_id)
        if existing is not None:
            if existing.user_id != user_id:
                raise ResourceOwnerConflictError(
                    f"资源已被其他用户绑定（{RESOURCE_OWNER_TABLE}）"
                )
            return existing

        factory = self._require_factory()
        try:
            with factory() as session, session.begin():
                session.add(
                    ResourceOwner(
                        resource_type=resource_type,
                        resource_id=resource_id,
                        user_id=user_id,
                        project_id=project_id,
                    )
                )
        except IntegrityError as exc:
            # 并发双写：唯一约束胜出 ⇒ 重读判定（同用户 ⇒ 幂等；异用户 ⇒ 冲突）
            existing = self.get(
                resource_type=resource_type, resource_id=resource_id
            )
            if existing is None:
                raise ResourceOwnerRepositoryError(
                    f"归属登记失败（{type(exc).__name__}）"
                ) from exc
            if existing.user_id != user_id:
                raise ResourceOwnerConflictError(
                    f"资源已被其他用户绑定（{RESOURCE_OWNER_TABLE}）"
                ) from exc
            return existing
        except SQLAlchemyError as exc:
            raise ResourceOwnerRepositoryError(
                f"归属登记失败（{type(exc).__name__}）"
            ) from exc
        bound = self.get(resource_type=resource_type, resource_id=resource_id)
        if bound is None:  # pragma: no cover - 防御性（写入后立即可读）
            raise ResourceOwnerRepositoryError("归属登记后读取失败")
        return bound

    # ---------- 读 ----------

    def get(
        self, *, resource_type: str, resource_id: str
    ) -> ResourceOwnerRow | None:
        """查归属（无 → None ⇒ 该资源没有归属登记）。"""
        factory = self._require_factory()
        resource_type = validate_resource_type(resource_type)
        resource_id = validate_resource_id(resource_id)
        try:
            with factory() as session:
                row = session.scalar(
                    select(ResourceOwner).where(
                        ResourceOwner.resource_type == resource_type,
                        ResourceOwner.resource_id == resource_id,
                    )
                )
                return self._to_row(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise ResourceOwnerRepositoryError(
                f"归属读取失败（{type(exc).__name__}）"
            ) from exc

    def list_for_user(
        self,
        user_id: str,
        *,
        resource_type: str | None = None,
        limit: int = 50,
    ) -> tuple[ResourceOwnerRow, ...]:
        """某用户的资源列表（创建时间倒序；``limit`` 钳制到硬上限内）。"""
        factory = self._require_factory()
        user_id = validate_owner_user_id(user_id)
        if resource_type is not None:
            resource_type = validate_resource_type(resource_type)
        safe_limit = max(1, min(int(limit), USER_RESOURCE_LIST_MAX_LIMIT))
        try:
            with factory() as session:
                stmt = select(ResourceOwner).where(
                    ResourceOwner.user_id == user_id
                )
                if resource_type is not None:
                    stmt = stmt.where(
                        ResourceOwner.resource_type == resource_type
                    )
                stmt = stmt.order_by(ResourceOwner.id.desc()).limit(safe_limit)
                rows = session.scalars(stmt).all()
                return tuple(self._to_row(row) for row in rows)
        except SQLAlchemyError as exc:
            raise ResourceOwnerRepositoryError(
                f"归属列表读取失败（{type(exc).__name__}）"
            ) from exc

    # ---------- 内部 ----------

    def _require_factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            raise ResourceOwnerRepositoryError(
                "DATABASE_URL 未配置；归属持久化不可用"
            )
        return self._session_factory

    @staticmethod
    def _to_row(row: ResourceOwner) -> ResourceOwnerRow:
        return ResourceOwnerRow(
            id=int(row.id),
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            user_id=row.user_id,
            project_id=row.project_id,
            created_at=row.created_at,
        )
