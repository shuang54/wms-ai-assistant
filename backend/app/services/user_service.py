"""User Service（Phase 7 Recovery M1 — 用户创建与认证）。

职责（只做用户域；不碰会话 / 资源归属 / HTTP）：

    create_user(...)        受控创建用户（**无公开注册接口**；管理员 / CLI）
    get_user(...)           只读用户视图（**不含** password_hash）
    authenticate(...)       登录认证（统一失败语义 + 登录尝试限制）

安全语义：

    * **统一失败**：用户名不存在 / 密码错误 / 用户停用 / 账户锁定 ——
      一律 ``InvalidCredentialsError``（调用方返回同一 401 文案），
      不泄露用户名是否存在；
    * **耗时均衡**：用户名不存在时也执行一次真实 KDF（``dummy_verify``），
      避免计时侧信道枚举有效用户名；
    * **登录尝试限制**（持久化）：连续失败达到 ``auth.max_failed_logins``
      ⇒ ``locked_until = now + auth.lockout_minutes``；成功即清零；
    * 停用用户（``DISABLED``）一律拒绝登录（同样统一失败语义）；
    * **不**把数据库账号 / 业务库账号 / LLM API Key 当作用户凭据
      （本服务只认 ``app_user`` 表中的用户）。

组合根（测试可 monkeypatch ``get_user_service``）：
默认装配 ``UserService(UserRepository())``——构造期不连接数据库。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.app.auth.password import (
    PasswordPolicyError,
    dummy_verify,
    hash_password,
    validate_password_policy,
    verify_password,
)
from backend.app.config import settings
from backend.app.db.user_repository import (
    UserRepository,
    UserRepositoryError,
    UserRow,
    UsernameAlreadyExistsError,
    validate_username,
)

logger = logging.getLogger(__name__)

__all__ = [
    "UserService",
    "UserView",
    "InvalidCredentialsError",
    "UserAlreadyExistsError",
    "UserServiceError",
    "get_user_service",
]


@dataclass(frozen=True)
class UserView:
    """用户只读视图（**不含** password_hash / 内部计数）。"""

    user_id: str
    username: str
    display_name: str
    status: str


class UserServiceError(RuntimeError):
    """用户域服务错误（不含用户名 / 密码 / 哈希内容）。"""


class InvalidCredentialsError(UserServiceError):
    """认证失败（统一语义：用户名不存在 / 密码错误 / 停用 / 锁定）。"""


class UserAlreadyExistsError(UserServiceError):
    """用户名已被占用。"""


class UserService:
    """用户创建 / 查询 / 认证（最小实现）。"""

    def __init__(self, user_repository: UserRepository | None = None) -> None:
        self._users = user_repository or UserRepository()

    # ---------- 受控创建 ----------

    def create_user(
        self, *, username: str, password: str, display_name: str = ""
    ) -> UserView:
        """创建用户（管理员 / 初始化流程专用；**无公开注册接口**）。

        Raises:
            ValueError:                用户名 / 密码策略不合法。
            UserAlreadyExistsError:    用户名已存在。
            UserServiceError:          持久化失败（含 DB 未配置）。
        """
        normalized_username = validate_username(username)
        validate_password_policy(password)
        user_id = str(uuid.uuid4())
        try:
            row = self._users.create(
                user_id=user_id,
                username=normalized_username,
                password_hash=hash_password(password),
                display_name=display_name.strip(),
            )
        except UsernameAlreadyExistsError as exc:
            raise UserAlreadyExistsError("用户名已存在") from exc
        except UserRepositoryError as exc:
            raise UserServiceError(f"用户创建失败（{type(exc).__name__}）") from exc
        logger.info(
            "user created",
            extra={"user_id": row.user_id, "status": row.status},
        )
        return _to_view(row)

    # ---------- 只读 ----------

    def get_user(self, user_id: str) -> UserView | None:
        """按 ID 读用户视图（无 → None；**不含** password_hash）。"""
        row = self._users.get_by_user_id(user_id)
        return _to_view(row) if row is not None else None

    # ---------- 认证 ----------

    def authenticate(self, *, username: str, password: str) -> UserRow:
        """认证（成功返回用户行；失败一律 ``InvalidCredentialsError``）。

        顺序（先判存在与状态，再验密码；任何失败路径都走统一异常）：

            1. 用户名不存在 → dummy KDF（耗时均衡）→ 统一失败；
            2. 停用 → dummy KDF → 统一失败；
            3. 锁定中 → dummy KDF → 统一失败（**不**透露锁定状态）；
            4. 密码错误 → 记录失败（累计 / 达限锁定）→ 统一失败；
            5. 成功 → 清零失败计数 → 返回用户行。
        """
        if not isinstance(username, str) or not isinstance(password, str):
            raise InvalidCredentialsError("认证失败")
        row = self._users.get_by_username(username.strip())
        if row is None:
            dummy_verify(password)
            raise InvalidCredentialsError("认证失败")
        if row.status != "ACTIVE":
            dummy_verify(password)
            raise InvalidCredentialsError("认证失败")
        now = datetime.now(timezone.utc)
        if row.locked_until is not None and row.locked_until > now:
            dummy_verify(password)
            raise InvalidCredentialsError("认证失败")
        if not verify_password(password, row.password_hash):
            self._record_failure(row)
            raise InvalidCredentialsError("认证失败")
        try:
            self._users.apply_login_success(row.user_id)
        except UserRepositoryError:  # 计数清零失败不影响本次登录结果
            logger.warning(
                "login success bookkeeping failed",
                extra={"error_type": "UserRepositoryError"},
            )
        return row

    # ---------- 内部 ----------

    def _record_failure(self, row: UserRow) -> None:
        """累计失败计数（**数据库原子自增**；达限锁定由服务端配置决定）。

        M1 Security Review：计数与锁定判定下沉到单条 UPDATE（见
        ``UserRepository.apply_login_failure``），并发失败不会丢更新。
        best-effort：记账失败不影响本次"认证失败"的对外语义。
        """
        try:
            self._users.apply_login_failure(
                row.user_id,
                max_failed_logins=settings.auth.max_failed_logins,
                lockout_minutes=settings.auth.lockout_minutes,
            )
        except UserRepositoryError:
            logger.warning(
                "login failure bookkeeping failed",
                extra={"error_type": "UserRepositoryError"},
            )


def _to_view(row: UserRow) -> UserView:
    return UserView(
        user_id=row.user_id,
        username=row.username,
        display_name=row.display_name,
        status=row.status,
    )


def get_user_service() -> UserService:
    """Composition Root：用户服务（测试以 monkeypatch 替换本函数）。"""
    return _USER_SERVICE


_USER_SERVICE = UserService()
