"""User Repository（Phase 7 Recovery M1 — 用户持久化）。

最小数据访问层（与 ``conversation_repository`` 同风格 / 同基础设施）：

    create(...)                       插入用户（user_id 由 Service 生成）
    get_by_user_id(...)               只读：按 ID（无 → None）
    get_by_username(...)              只读：按登录名（无 → None）
    apply_login_success(...)          登录成功：清零失败计数 + 解除锁定
    apply_login_failure(...)          登录失败：累计失败计数 + 按需设置锁定

复用基础设施（不新增第二套机制）：

    * Session 工厂：``backend.app.db.session.get_session_factory()``
    * 事务：写 ``with factory() as session, session.begin():`` ·
      读 ``with factory() as session:``（Repository 持有事务——Step 4 方案 A）
    * ORM：``backend.app.db.models.AppUser``

不做的（最小实现，明确不做）：

    * 不提供 delete / 列表 / 分页 / 搜索（用户管理面属后续阶段）；
    * 不返回 ORM 对象（只返回 frozen Row）；
    * 不做用户名大小写折叠 / 归一化（精确比较；策略变更属后续）；
    * **不**记录密码明文（本层只碰 ``password_hash`` 列）。

失败语义：``SQLAlchemyError`` → ``UserRepositoryError``（事务已回滚）；
用户名唯一冲突 → ``UsernameAlreadyExistsError``（子类，供 Service 精确映射）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final

from sqlalchemy import case, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models.app_user import (
    APP_USER_STATUS_ACTIVE,
    APP_USER_TABLE,
    APP_USER_USERNAME_MAX_LENGTH,
    AppUser,
)
from backend.app.db.session import get_session_factory

__all__ = [
    "UserRepository",
    "UserRepositoryError",
    "UsernameAlreadyExistsError",
    "UserRow",
    "USER_READ_COLUMNS",
    "USERNAME_MAX_LENGTH",
    "validate_username",
]


#: 只读查询允许返回的字段（显式列；**不用** SELECT *）。
#: 注意：``password_hash`` **不在**通用只读列中——只有认证路径按需取用。
USER_READ_COLUMNS: Final[tuple[str, ...]] = (
    "user_id",
    "username",
    "display_name",
    "status",
    "failed_login_count",
    "locked_until",
    "created_at",
    "updated_at",
)

#: 登录名长度上限（与模型列宽一致；Service / API 共用）。
USERNAME_MAX_LENGTH: Final[int] = APP_USER_USERNAME_MAX_LENGTH


def validate_username(username: str) -> str:
    """登录名校验（非空 / 去空白 / 长度上限）。返回规范化后的值。"""
    if not isinstance(username, str):
        raise ValueError("username 必须是非空 str")
    normalized = username.strip()
    if not normalized:
        raise ValueError("username 不能为空白")
    if len(normalized) > USERNAME_MAX_LENGTH:
        raise ValueError(
            f"username 长度超限（最多 {USERNAME_MAX_LENGTH} 字符）"
        )
    return normalized


@dataclass(frozen=True)
class UserRow:
    """用户行（frozen；含认证所需字段，**不**含其它表数据）。"""

    user_id: str
    username: str
    password_hash: str
    display_name: str
    status: str
    failed_login_count: int
    locked_until: datetime | None
    created_at: datetime
    updated_at: datetime


class UserRepositoryError(RuntimeError):
    """用户持久化失败（事务已回滚；不含 SQL / 连接串 / 凭据）。"""


class UsernameAlreadyExistsError(UserRepositoryError):
    """用户名已被占用（唯一约束冲突）。"""


class UserRepository:
    """``ai_ops.app_user`` 最小数据访问层。"""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or get_session_factory()

    # ---------- 写 ----------

    def create(
        self,
        *,
        user_id: str,
        username: str,
        password_hash: str,
        display_name: str = "",
        status: str = APP_USER_STATUS_ACTIVE,
    ) -> UserRow:
        """插入用户（``user_id`` 由调用方生成；重复用户名 ⇒ 子类错误）。"""
        factory = self._require_factory()
        try:
            with factory() as session, session.begin():
                user = AppUser(
                    user_id=user_id,
                    username=username,
                    password_hash=password_hash,
                    display_name=display_name,
                    status=status,
                )
                session.add(user)
        except IntegrityError as exc:
            raise UsernameAlreadyExistsError(
                f"用户名已存在（{APP_USER_TABLE}）"
            ) from exc
        except SQLAlchemyError as exc:
            raise UserRepositoryError(
                f"用户创建失败（{type(exc).__name__}）"
            ) from exc
        return self.get_by_user_id(user_id)  # type: ignore[return-value]

    def apply_login_success(self, user_id: str) -> None:
        """登录成功：清零失败计数 + 解除锁定（best-effort 语义由调用方决定）。"""
        factory = self._require_factory()
        try:
            with factory() as session, session.begin():
                user = session.get(AppUser, user_id)
                if user is None:
                    return
                user.failed_login_count = 0
                user.locked_until = None
        except SQLAlchemyError as exc:
            raise UserRepositoryError(
                f"登录成功状态更新失败（{type(exc).__name__}）"
            ) from exc

    def apply_login_failure(
        self,
        user_id: str,
        *,
        max_failed_logins: int,
        lockout_minutes: int,
    ) -> None:
        """登录失败：**原子**累计计数；达到上限即设置锁定。

        M1 Security Review：原实现是"读行 → +1 → 写回"（read-modify-write），
        并发失败请求会互相覆盖计数 ⇒ 锁定阈值可被绕过。现改为**单条 UPDATE**
        （``failed_login_count = failed_login_count + 1`` + ``CASE`` 置锁），
        由数据库保证原子性；``locked_until`` 由服务端按配置计算（**不**接受
        客户端任何时间参数）。
        """
        factory = self._require_factory()
        threshold = int(max_failed_logins)
        if threshold < 1:
            raise ValueError("max_failed_logins 必须 ≥ 1")
        minutes = int(lockout_minutes)
        if minutes < 1:
            raise ValueError("lockout_minutes 必须 ≥ 1")
        locked_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        try:
            with factory() as session, session.begin():
                session.execute(
                    update(AppUser)
                    .where(AppUser.user_id == user_id)
                    .values(
                        failed_login_count=AppUser.failed_login_count + 1,
                        locked_until=case(
                            (
                                AppUser.failed_login_count + 1 >= threshold,
                                locked_until,
                            ),
                            else_=AppUser.locked_until,
                        ),
                    )
                )
        except SQLAlchemyError as exc:
            raise UserRepositoryError(
                f"登录失败状态更新失败（{type(exc).__name__}）"
            ) from exc

    # ---------- 读 ----------

    def get_by_user_id(self, user_id: str) -> UserRow | None:
        """按用户 ID 只读读取（含 ``password_hash``——认证路径专用）。"""
        factory = self._require_factory()
        try:
            with factory() as session:
                user = session.get(AppUser, user_id)
                return self._to_row(user) if user is not None else None
        except SQLAlchemyError as exc:
            raise UserRepositoryError(
                f"用户读取失败（{type(exc).__name__}）"
            ) from exc

    def get_by_username(self, username: str) -> UserRow | None:
        """按登录名只读读取（精确比较；含 ``password_hash``——认证路径专用）。"""
        factory = self._require_factory()
        try:
            with factory() as session:
                user = session.scalar(
                    select(AppUser).where(AppUser.username == username)
                )
                return self._to_row(user) if user is not None else None
        except SQLAlchemyError as exc:
            raise UserRepositoryError(
                f"用户读取失败（{type(exc).__name__}）"
            ) from exc

    # ---------- 内部 ----------

    def _require_factory(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            raise UserRepositoryError("DATABASE_URL 未配置；用户持久化不可用")
        return self._session_factory

    @staticmethod
    def _to_row(user: AppUser) -> UserRow:
        return UserRow(
            user_id=user.user_id,
            username=user.username,
            password_hash=user.password_hash,
            display_name=user.display_name,
            status=user.status,
            failed_login_count=int(user.failed_login_count),
            locked_until=user.locked_until,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
