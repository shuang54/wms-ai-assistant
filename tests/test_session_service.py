"""SessionService 单元测试（Phase 7 Recovery M1；离线 Fake）。

覆盖任务书测试清单：登录成功 / 失败、登出后会话失效、会话过期、
未认证访问（``validate`` 对无效令牌一律 None——fail-closed）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.app.auth.tokens import hash_session_token
from backend.app.db.user_repository import UserRow
from backend.app.db.user_session_repository import UserSessionRow
from backend.app.services.session_service import (
    Principal,
    SessionService,
)
from backend.app.services.user_service import (
    InvalidCredentialsError,
    UserService,
)

__all__ = [
    "FakeSessionRepository",
    "FakeUserService",
    "test_login_issues_token_and_persists_only_hash",
    "test_validate_returns_principal_for_valid_token",
    "test_validate_rejects_unknown_token",
    "test_validate_rejects_revoked_session",
    "test_validate_rejects_expired_session",
    "test_validate_rejects_when_user_disabled",
    "test_logout_revokes_session",
    "test_logout_is_idempotent",
    "test_login_failure_propagates_uniform_error",
    "test_login_csrf_token_matches_expected",
    "test_csrf_token_differs_across_sessions",
    "test_expected_csrf_token_none_after_revoke",
    "test_expected_csrf_token_none_after_expiry",
    "test_expected_csrf_token_none_for_unknown_token",
]


class FakeSessionRepository:
    """进程内 Fake 会话存储（**只存哈希**）。"""

    def __init__(self) -> None:
        self.rows: dict[str, UserSessionRow] = {}

    def create(
        self,
        *,
        session_id: str,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
    ) -> UserSessionRow:
        now = datetime.now(timezone.utc)
        row = UserSessionRow(
            session_id=session_id,
            user_id=user_id,
            token_hash=token_hash,
            created_at=now,
            expires_at=expires_at,
            revoked_at=None,
        )
        self.rows[session_id] = row
        return row

    def get_by_token_hash(self, token_hash: str) -> UserSessionRow | None:
        for row in self.rows.values():
            if row.token_hash == token_hash:
                return row
        return None

    def revoke(self, session_id: str, *, revoked_at: datetime) -> bool:
        row = self.rows.get(session_id)
        if row is None:
            return False
        if row.revoked_at is None:
            self.rows[session_id] = UserSessionRow(
                **{**row.__dict__, "revoked_at": revoked_at}
            )
        return True


class FakeUserService:
    """进程内 Fake 用户服务（仅登录校验 + 视图读取）。"""

    def __init__(self) -> None:
        self._users: dict[str, UserRow] = {}

    def add(self, *, user_id: str, username: str, password_hash: str) -> None:
        now = datetime.now(timezone.utc)
        self._users[user_id] = UserRow(
            user_id=user_id,
            username=username,
            password_hash=password_hash,
            display_name="",
            status="ACTIVE",
            failed_login_count=0,
            locked_until=None,
            created_at=now,
            updated_at=now,
        )

    def disable(self, user_id: str) -> None:
        row = self._users[user_id]
        self._users[user_id] = UserRow(**{**row.__dict__, "status": "DISABLED"})

    def authenticate(self, *, username: str, password: str) -> UserRow:
        from backend.app.auth.password import verify_password

        for row in self._users.values():
            if row.username == username:
                if verify_password(password, row.password_hash):
                    return row
                raise InvalidCredentialsError("认证失败")
        raise InvalidCredentialsError("认证失败")

    def get_user(self, user_id: str):  # noqa: ANN201
        from backend.app.services.user_service import _to_view

        row = self._users.get(user_id)
        return _to_view(row) if row is not None else None


def _service() -> tuple[SessionService, FakeSessionRepository, FakeUserService]:
    sessions = FakeSessionRepository()
    users = FakeUserService()
    from backend.app.auth.password import hash_password

    users.add(
        user_id="user-1", username="alice", password_hash=hash_password("password-123")
    )
    return (
        SessionService(session_repository=sessions, user_service=users),  # type: ignore[arg-type]
        sessions,
        users,
    )


def test_login_issues_token_and_persists_only_hash() -> None:
    service, sessions, _ = _service()
    result = service.login(username="alice", password="password-123")
    assert result.token
    assert result.expires_at > datetime.now(timezone.utc)
    stored = list(sessions.rows.values())[0]
    assert stored.token_hash == hash_session_token(result.token)
    assert result.token not in str(stored.__dict__)  # 库内不出现令牌本体


def test_validate_returns_principal_for_valid_token() -> None:
    service, _, _ = _service()
    result = service.login(username="alice", password="password-123")
    principal = service.validate(result.token)
    assert principal == Principal(
        user_id="user-1", username="alice", display_name=""
    )


def test_validate_rejects_unknown_token() -> None:
    service, _, _ = _service()
    assert service.validate("not-a-real-token") is None
    assert service.validate("") is None


def test_validate_rejects_revoked_session() -> None:
    service, _, _ = _service()
    result = service.login(username="alice", password="password-123")
    assert service.logout(result.token) is True
    assert service.validate(result.token) is None


def test_validate_rejects_expired_session() -> None:
    service, sessions, _ = _service()
    result = service.login(username="alice", password="password-123")
    row = list(sessions.rows.values())[0]
    sessions.rows[row.session_id] = UserSessionRow(
        **{
            **row.__dict__,
            "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
    )
    assert service.validate(result.token) is None


def test_validate_rejects_when_user_disabled() -> None:
    service, _, users = _service()
    result = service.login(username="alice", password="password-123")
    users.disable("user-1")
    assert service.validate(result.token) is None


def test_logout_revokes_session() -> None:
    service, sessions, _ = _service()
    result = service.login(username="alice", password="password-123")
    assert service.logout(result.token) is True
    assert list(sessions.rows.values())[0].revoked_at is not None


def test_logout_is_idempotent() -> None:
    service, _, _ = _service()
    result = service.login(username="alice", password="password-123")
    assert service.logout(result.token) is True
    assert service.logout(result.token) is True  # 重复登出仍成功
    assert service.logout("unknown-token") is False


def test_login_failure_propagates_uniform_error() -> None:
    service, sessions, _ = _service()
    with pytest.raises(InvalidCredentialsError):
        service.login(username="alice", password="wrong-password-1")
    with pytest.raises(InvalidCredentialsError):
        service.login(username="ghost", password="password-123")
    assert sessions.rows == {}  # 失败不签发会话


# ---------- CSRF（M1 Security Review：会话绑定签名） ----------


def test_login_csrf_token_matches_expected() -> None:
    service, _, _ = _service()
    result = service.login(username="alice", password="password-123")
    assert len(result.csrf_token) == 64
    assert service.expected_csrf_token(result.token) == result.csrf_token


def test_csrf_token_differs_across_sessions() -> None:
    """不同会话的 CSRF 值互不通用（绑定 session_id + token_hash）。"""
    service, _, _ = _service()
    first = service.login(username="alice", password="password-123")
    second = service.login(username="alice", password="password-123")
    assert first.csrf_token != second.csrf_token
    assert service.expected_csrf_token(first.token) == first.csrf_token
    assert service.expected_csrf_token(second.token) == second.csrf_token
    # 用另一个会话的 CSRF 值不能通过当前会话的校验
    assert first.csrf_token != service.expected_csrf_token(second.token)


def test_expected_csrf_token_none_after_revoke() -> None:
    service, _, _ = _service()
    result = service.login(username="alice", password="password-123")
    assert service.logout(result.token) is True
    assert service.expected_csrf_token(result.token) is None


def test_expected_csrf_token_none_after_expiry() -> None:
    service, sessions, _ = _service()
    result = service.login(username="alice", password="password-123")
    row = list(sessions.rows.values())[0]
    sessions.rows[row.session_id] = UserSessionRow(
        **{
            **row.__dict__,
            "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
    )
    assert service.expected_csrf_token(result.token) is None


def test_expected_csrf_token_none_for_unknown_token() -> None:
    service, _, _ = _service()
    assert service.expected_csrf_token("not-a-real-token") is None
    assert service.expected_csrf_token("") is None
