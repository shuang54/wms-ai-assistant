"""Session Service（Phase 7 Recovery M1 — 服务端会话 / 登录登出）。

会话模型（**服务端管理**，浏览器只持不透明 Cookie）：

    login(username, password)
        → UserService.authenticate（统一失败语义）
        → 签发 session_id + 不透明令牌（只存 SHA-256）
        → 返回 LoginResult（令牌本体只在此处暴露一次）

    validate(token)
        → 按哈希查会话 → 校验吊销 / 过期 → 校验用户仍存在且 ACTIVE
        → Principal（**不**信任客户端任何身份字段）

    logout(token)
        → 吊销会话（幂等；重复登出仍成功）

失效语义（全部即时生效，无需等令牌自然过期）：

    * 登出即吊销（``revoked_at``）；
    * 用户被停用 ⇒ 既有会话在 ``validate`` 时失效（每次请求都查用户）；
    * 过期（``expires_at`` 绝对 TTL；**不**做滑动续期）。

组合根（测试可 monkeypatch ``get_session_service``）：默认装配
``SessionService(UserSessionRepository(), UserService())``——构造期不连 DB。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.app.auth.tokens import (
    generate_session_token,
    hash_session_token,
    sign_csrf_token,
)
from backend.app.config import settings
from backend.app.db.user_session_repository import (
    UserSessionRepository,
    UserSessionRepositoryError,
)
from backend.app.services.user_service import (
    InvalidCredentialsError,
    UserService,
    get_user_service,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SessionService",
    "SessionServiceError",
    "Principal",
    "LoginResult",
    "get_session_service",
]

@dataclass(frozen=True)
class Principal:
    """已认证主体（来自服务端会话；**不**含令牌 / 密码 / 项目主张）。"""

    user_id: str
    username: str
    display_name: str


@dataclass(frozen=True)
class LoginResult:
    """登录结果（``token`` 本体只在此对象与 Set-Cookie 中各出现一次）。"""

    session_id: str
    token: str
    principal: Principal
    expires_at: datetime
    #: 与本会话绑定的 CSRF 签名值（HMAC(token_hash, session_id)；
    #: 由服务端签发，放进**非** HttpOnly Cookie 供前端回显于请求头）。
    csrf_token: str


class SessionServiceError(RuntimeError):
    """会话服务错误（不含令牌 / 用户名 / 凭据内容）。"""


class SessionService:
    """登录 / 校验 / 登出（最小实现）。"""

    def __init__(
        self,
        session_repository: UserSessionRepository | None = None,
        user_service: UserService | None = None,
    ) -> None:
        self._sessions = session_repository or UserSessionRepository()
        self._users = user_service or get_user_service()

    # ---------- 登录 ----------

    def login(self, *, username: str, password: str) -> LoginResult:
        """认证并签发会话（失败抛 ``InvalidCredentialsError``——统一语义）。"""
        row = self._users.authenticate(username=username, password=password)
        session_id = str(uuid.uuid4())
        token = generate_session_token()
        token_hash = hash_session_token(token)
        expires_at = datetime.now(timezone.utc) + timedelta(
            hours=settings.auth.session_ttl_hours
        )
        try:
            self._sessions.create(
                session_id=session_id,
                user_id=row.user_id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        except UserSessionRepositoryError as exc:
            raise SessionServiceError(
                f"会话签发失败（{type(exc).__name__}）"
            ) from exc
        logger.info(
            "session created",
            extra={"user_id": row.user_id, "session_id": session_id},
        )
        return LoginResult(
            session_id=session_id,
            token=token,
            principal=Principal(
                user_id=row.user_id,
                username=row.username,
                display_name=row.display_name,
            ),
            expires_at=expires_at,
            # CSRF 签名：密钥 = 服务端独占的 token_hash（浏览器不可得）
            csrf_token=sign_csrf_token(session_id=session_id, key=token_hash),
        )

    # ---------- 校验 ----------

    def validate(self, token: str) -> Principal | None:
        """校验令牌 ⇒ Principal；任何无效情形（缺失 / 吊销 / 过期 /
        用户不存在 / 用户停用）一律 ``None``（fail-closed）。"""
        if not isinstance(token, str) or not token.strip():
            return None
        try:
            row = self._sessions.get_by_token_hash(hash_session_token(token))
        except UserSessionRepositoryError:
            logger.warning(
                "session lookup failed",
                extra={"error_type": "UserSessionRepositoryError"},
            )
            return None
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        if row.revoked_at is not None or row.expires_at <= now:
            return None
        user = self._users.get_user(row.user_id)
        if user is None or user.status != "ACTIVE":
            return None
        return Principal(
            user_id=user.user_id,
            username=user.username,
            display_name=user.display_name,
        )

    # ---------- CSRF（M1 Security Review：会话绑定签名） ----------

    def expected_csrf_token(self, token: str) -> str | None:
        """当前会话**应当**携带的 CSRF 签名值（无效会话 ⇒ ``None``）。

        供 ``require_csrf`` 重算期望值：请求头值必须等于用**本会话**的
        ``token_hash`` 对 ``session_id`` 的 HMAC——不知道服务端独占的
        token_hash 即无法伪造；会话吊销 / 过期后本函数返回 ``None``
        （旧 CSRF 值随之失效）。
        """
        if not isinstance(token, str) or not token.strip():
            return None
        try:
            row = self._sessions.get_by_token_hash(hash_session_token(token))
        except UserSessionRepositoryError as exc:
            raise SessionServiceError(
                f"会话查询失败（{type(exc).__name__}）"
            ) from exc
        if row is None:
            return None
        now = datetime.now(timezone.utc)
        if row.revoked_at is not None or row.expires_at <= now:
            return None
        return sign_csrf_token(session_id=row.session_id, key=row.token_hash)

    # ---------- 登出 ----------

    def logout(self, token: str) -> bool:
        """吊销会话（幂等：无效 / 缺失令牌也返回 False 而不抛错）。"""
        if not isinstance(token, str) or not token.strip():
            return False
        try:
            row = self._sessions.get_by_token_hash(hash_session_token(token))
        except UserSessionRepositoryError:
            return False
        if row is None:
            return False
        try:
            return self._sessions.revoke(
                row.session_id, revoked_at=datetime.now(timezone.utc)
            )
        except UserSessionRepositoryError:
            return False


def get_session_service() -> SessionService:
    """Composition Root：会话服务（测试以 monkeypatch 替换本函数）。"""
    return _SESSION_SERVICE


_SESSION_SERVICE = SessionService()
