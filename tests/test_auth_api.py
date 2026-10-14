"""认证 API HTTP 契约测试（Phase 7 Recovery M1；离线 Fake；零 DB / 零 LLM）。

覆盖任务书测试清单：登录成功 / 失败（统一响应）、登出后会话失效、未认证访问、
伪造 user_id、敏感信息不进入日志/响应、CSRF 双提交、Secure Cookie 语义。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import auth as auth_module
from backend.app.api import auth_dependency as auth_dep
from backend.app.auth.tokens import hash_session_token, sign_csrf_token
from backend.app.config import settings
from backend.app.dto.auth_api import CURRENT_USER_RESPONSE_FIELDS
from backend.app.main import app
from backend.app.services.session_service import (
    LoginResult,
    Principal,
    SessionServiceError,
)
from backend.app.services.user_service import InvalidCredentialsError

__all__ = [
    "FakeSessionService",
    "test_login_success_sets_cookies_and_returns_user",
    "test_login_wrong_password_uniform_401",
    "test_login_unknown_user_same_detail_as_wrong_password",
    "test_login_missing_field_422",
    "test_login_service_unavailable_503",
    "test_me_with_valid_session",
    "test_me_without_cookie_401",
    "test_me_with_garbage_cookie_401",
    "test_me_ignores_forged_identity_fields",
    "test_get_me_does_not_require_csrf_header",
    "test_logout_requires_csrf_header",
    "test_logout_rejects_missing_csrf_cookie",
    "test_logout_rejects_cookie_header_mismatch",
    "test_logout_rejects_forged_csrf_value",
    "test_logout_rejects_other_sessions_csrf_token",
    "test_logout_revokes_session_and_clears_cookies",
    "test_csrf_token_invalid_after_session_revoked",
    "test_csrf_token_invalid_after_session_expired",
    "test_duplicate_cookies_do_not_bypass_csrf",
    "test_csrf_failure_leaves_session_intact",
    "test_logout_without_session_is_idempotent",
    "test_secure_cookie_not_replayed_over_plain_http",
    "test_no_secrets_in_logs_or_responses",
]


def _login(client: TestClient) -> str:
    """登录并返回会话令牌（测试辅助）。"""
    response = client.post(
        "/api/auth/login", json={"username": "alice", "password": _PASSWORD}
    )
    assert response.status_code == 200, response.text
    token = client.cookies.get(settings.auth.session_cookie_name)
    assert token
    return token


def _csrf_header(client: TestClient) -> dict[str, str]:
    return {settings.auth.csrf_header_name: client.cookies.get(settings.auth.csrf_cookie_name)}

_PASSWORD = "a-valid-password-1"


class FakeSessionService:
    """进程内 Fake（模拟用户表 + 会话表；不触 DB）。

    CSRF 语义与生产一致（复用生产签名函数）：``expected_csrf_token`` 用
    会话的 ``token_hash`` 对 ``session_id`` 重算 HMAC——测试只替换存储，
    **不**替换安全语义。
    """

    def __init__(self) -> None:
        self._password_hash = _hash(_PASSWORD)
        self._sessions: dict[str, dict[str, Any]] = {}
        self._next = 0

    def login(self, *, username: str, password: str) -> LoginResult:
        from backend.app.auth.password import verify_password

        if username != "alice" or not verify_password(password, self._password_hash):
            raise InvalidCredentialsError("认证失败")
        self._next += 1
        session_id = f"session-{self._next}"
        token = f"token-{self._next}"
        token_hash = hash_session_token(token)
        self._sessions[token] = {
            "session_id": session_id,
            "token_hash": token_hash,
            "revoked": False,
            "expired": False,
        }
        return LoginResult(
            session_id=session_id,
            token=token,
            principal=Principal(
                user_id="user-1", username="alice", display_name="Alice"
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
            csrf_token=sign_csrf_token(session_id=session_id, key=token_hash),
        )

    def validate(self, token: str) -> Principal | None:
        row = self._live_row(token)
        if row is None:
            return None
        return Principal(user_id="user-1", username="alice", display_name="Alice")

    def expected_csrf_token(self, token: str) -> str | None:
        row = self._live_row(token)
        if row is None:
            return None
        return sign_csrf_token(
            session_id=row["session_id"], key=row["token_hash"]
        )

    def logout(self, token: str) -> bool:
        row = self._sessions.get(token)
        if row is None:
            return False
        row["revoked"] = True
        return True

    # ---- 测试辅助（仅测试用；生产无对应入口）----
    def expire(self, token: str) -> None:
        row = self._sessions.get(token)
        if row is not None:
            row["expired"] = True

    def _live_row(self, token: str) -> dict[str, Any] | None:
        row = self._sessions.get(token)
        if row is None or row["revoked"] or row["expired"]:
            return None
        return row


def _hash(password: str) -> str:
    from backend.app.auth.password import hash_password

    return hash_password(password)


@pytest.fixture()
def fake_service() -> FakeSessionService:
    return FakeSessionService()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, fake_service: FakeSessionService):
    # 两个命名空间都要替换（端点与依赖各自持有组合根引用）
    monkeypatch.setattr(auth_module, "get_session_service", lambda: fake_service)
    monkeypatch.setattr(auth_dep, "get_session_service", lambda: fake_service)
    # Secure Cookie 需要 https 基址才能在测试客户端回传（与生产语义一致）
    return TestClient(app, base_url="https://testserver")


def test_login_success_sets_cookies_and_returns_user(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login", json={"username": "alice", "password": _PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert tuple(body) == CURRENT_USER_RESPONSE_FIELDS
    assert body["username"] == "alice"
    assert body["user_id"] == "user-1"

    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(
        c for c in cookies if c.startswith(settings.auth.session_cookie_name + "=")
    )
    csrf_cookie = next(
        c for c in cookies if c.startswith(settings.auth.csrf_cookie_name + "=")
    )
    assert "HttpOnly" in session_cookie
    assert "Secure" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert "HttpOnly" not in csrf_cookie  # 双提交要求 JS 可读
    # 令牌不出现在响应体
    assert "token-" not in response.text


def test_login_wrong_password_uniform_401(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login", json={"username": "alice", "password": "wrong-password-1"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "认证失败：用户名或密码错误"


def test_login_unknown_user_same_detail_as_wrong_password(
    client: TestClient,
) -> None:
    wrong = client.post(
        "/api/auth/login", json={"username": "alice", "password": "wrong-password-1"}
    )
    unknown = client.post(
        "/api/auth/login", json={"username": "ghost", "password": _PASSWORD}
    )
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_login_missing_field_422(client: TestClient) -> None:
    assert client.post("/api/auth/login", json={"username": "alice"}).status_code == 422
    assert (
        client.post("/api/auth/login", json={"password": _PASSWORD}).status_code == 422
    )


def test_login_service_unavailable_503(
    monkeypatch: pytest.MonkeyPatch, fake_service: FakeSessionService
) -> None:
    def _boom() -> Any:
        raise SessionServiceError("db down")

    monkeypatch.setattr(auth_module, "get_session_service", _boom)
    client = TestClient(app, base_url="https://testserver")
    response = client.post(
        "/api/auth/login", json={"username": "alice", "password": _PASSWORD}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "认证服务不可用"


def test_me_with_valid_session(client: TestClient) -> None:
    client.post("/api/auth/login", json={"username": "alice", "password": _PASSWORD})
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["username"] == "alice"


def test_me_without_cookie_401(client: TestClient) -> None:
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "认证失败：缺少或无效的会话"


def test_me_with_garbage_cookie_401(client: TestClient) -> None:
    client.cookies.set(settings.auth.session_cookie_name, "garbage-token")
    assert client.get("/api/auth/me").status_code == 401


def test_me_ignores_forged_identity_fields(client: TestClient) -> None:
    client.post("/api/auth/login", json={"username": "alice", "password": _PASSWORD})
    response = client.get(
        "/api/auth/me",
        params={"user_id": "user-999", "project_id": "other-project"},
        headers={"X-User-Id": "user-999", "X-Project-Id": "other-project"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "user-1"  # 身份只来自服务端会话
    assert "project_id" not in body


def test_get_me_does_not_require_csrf_header(client: TestClient) -> None:
    """安全方法（GET）不需要 CSRF 头，也不隐式执行写操作。"""
    _login(client)
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["username"] == "alice"


def test_logout_requires_csrf_header(client: TestClient) -> None:
    """缺少 CSRF 请求头 ⇒ 403；会话仍然有效（副作用未发生）。"""
    _login(client)
    response = client.post("/api/auth/logout")
    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF 校验失败"
    assert client.get("/api/auth/me").status_code == 200


def test_logout_rejects_missing_csrf_cookie(client: TestClient) -> None:
    """缺少 CSRF Cookie（只有请求头）⇒ 403。"""
    _login(client)
    client.cookies.pop(settings.auth.csrf_cookie_name, None)
    response = client.post(
        "/api/auth/logout",
        headers={settings.auth.csrf_header_name: "0" * 64},
    )
    assert response.status_code == 403


def test_logout_rejects_cookie_header_mismatch(client: TestClient) -> None:
    """Cookie 与请求头不一致 ⇒ 403（双提交的第一道关）。"""
    _login(client)
    response = client.post(
        "/api/auth/logout",
        headers={settings.auth.csrf_header_name: "f" * 64},
    )
    assert response.status_code == 403


def test_logout_rejects_forged_csrf_value(client: TestClient) -> None:
    """Cookie==Header 但值是被伪造的（非本会话签名）⇒ 403。

    这正是朴素 Double-Submit 的被绕过点：攻击者注入同名 Cookie 并让
    header 等于它——现在服务端用当前会话重算签名，伪造值不可通过。
    """
    _login(client)
    forged = "a" * 64
    client.cookies.set(settings.auth.csrf_cookie_name, forged)
    response = client.post(
        "/api/auth/logout", headers={settings.auth.csrf_header_name: forged}
    )
    assert response.status_code == 403


def test_logout_rejects_other_sessions_csrf_token(
    monkeypatch: pytest.MonkeyPatch, fake_service: FakeSessionService
) -> None:
    """其他会话的 CSRF Token 不能用于当前会话（绑定 session_id + token_hash）。"""
    monkeypatch.setattr(auth_module, "get_session_service", lambda: fake_service)
    monkeypatch.setattr(auth_dep, "get_session_service", lambda: fake_service)
    first = TestClient(app, base_url="https://testserver")
    second = TestClient(app, base_url="https://testserver")
    _login(first)
    _login(second)
    other_csrf = second.cookies.get(settings.auth.csrf_cookie_name)
    response = first.post(
        "/api/auth/logout", headers={settings.auth.csrf_header_name: other_csrf}
    )
    assert response.status_code == 403


def test_logout_revokes_session_and_clears_cookies(client: TestClient) -> None:
    """合法 Session + 合法 CSRF ⇒ 登出成功；会话随即失效。"""
    _login(client)
    response = client.post("/api/auth/logout", headers=_csrf_header(client))
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert client.get("/api/auth/me").status_code == 401
    cleared = " ".join(response.headers.get_list("set-cookie"))
    assert settings.auth.session_cookie_name in cleared


def test_csrf_token_invalid_after_session_revoked(client: TestClient) -> None:
    """Session 撤销后，旧 CSRF Token 不再有效（fail-closed）。

    登出会清理客户端 Cookie；这里**手动重放**旧值（模拟持有旧令牌的
    攻击者 / 旧标签页）⇒ 无有效会话 ⇒ 无期望签名 ⇒ 403。
    """
    token = _login(client)
    csrf = client.cookies.get(settings.auth.csrf_cookie_name)
    assert (
        client.post(
            "/api/auth/logout", headers={settings.auth.csrf_header_name: csrf}
        ).status_code
        == 200
    )
    replay_cookie = (
        f"{settings.auth.session_cookie_name}={token}; "
        f"{settings.auth.csrf_cookie_name}={csrf}"
    )
    response = client.post(
        "/api/auth/logout",
        headers={
            "Cookie": replay_cookie,
            settings.auth.csrf_header_name: csrf,
        },
    )
    assert response.status_code == 403


def test_csrf_token_invalid_after_session_expired(
    client: TestClient, fake_service: FakeSessionService
) -> None:
    """Session 过期后，旧 CSRF Token 不再有效。"""
    token = _login(client)
    fake_service.expire(token)
    response = client.post("/api/auth/logout", headers=_csrf_header(client))
    assert response.status_code == 403
    assert client.get("/api/auth/me").status_code == 401


def test_duplicate_cookies_do_not_bypass_csrf(client: TestClient) -> None:
    """重复（注入的）CSRF Cookie 不造成校验歧义（两种顺序均拒绝）。"""
    token = _login(client)
    real = client.cookies.get(settings.auth.csrf_cookie_name)
    forged = "b" * 64
    for cookie_value in (forged, real):
        cookie_header = (
            f"{settings.auth.session_cookie_name}={token}; "
            f"{settings.auth.csrf_cookie_name}={cookie_value}; "
            f"{settings.auth.csrf_cookie_name}={forged}"
        )
        response = client.post(
            "/api/auth/logout",
            headers={
                "Cookie": cookie_header,
                settings.auth.csrf_header_name: forged,
            },
        )
        assert response.status_code == 403, cookie_value
    # 会话仍然有效（未被上述任何请求吊销）
    assert client.get("/api/auth/me").status_code == 200


def test_csrf_failure_leaves_session_intact(client: TestClient) -> None:
    """CSRF 失败发生在任何副作用之前：会话未被吊销、身份不变。"""
    _login(client)
    before = client.get("/api/auth/me")
    assert before.status_code == 200
    assert client.post("/api/auth/logout").status_code == 403  # 缺 CSRF 头
    after = client.get("/api/auth/me")
    assert after.status_code == 200
    assert after.json() == before.json()


def test_logout_without_session_is_idempotent(client: TestClient) -> None:
    """无会话 Cookie 的登出保持幂等 200（无可被滥用的已认证会话）。"""
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_secure_cookie_not_replayed_over_plain_http(
    monkeypatch: pytest.MonkeyPatch, fake_service: FakeSessionService
) -> None:
    """Secure 标志的真实语义（Set-Cookie → 回放路径）。

    纯 HTTP 基址下：登录可以成功（服务端签发了 Cookie），但客户端**不会**
    把 Secure Cookie 回传给 http 端点 ⇒ 会话无法在纯 HTTP 上维持。

    部署含义（如实记录）：``AUTH_COOKIE_SECURE`` 默认 true ⇒ 内网若以纯
    HTTP 访问，必须显式 ``AUTH_COOKIE_SECURE=0``（知悉风险）或启用 TLS。
    """
    monkeypatch.setattr(auth_module, "get_session_service", lambda: fake_service)
    monkeypatch.setattr(auth_dep, "get_session_service", lambda: fake_service)
    plain = TestClient(app)  # 默认 http://testserver
    login = plain.post(
        "/api/auth/login", json={"username": "alice", "password": _PASSWORD}
    )
    assert login.status_code == 200
    assert any(
        "Secure" in c
        for c in login.headers.get_list("set-cookie")
        if c.startswith(settings.auth.session_cookie_name + "=")
    )
    # Secure Cookie 不经纯 HTTP 回传 ⇒ 会话不可用
    assert plain.get("/api/auth/me").status_code == 401


def test_no_secrets_in_logs_or_responses(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        client.post(
            "/api/auth/login", json={"username": "alice", "password": _PASSWORD}
        )
        client.post(
            "/api/auth/login", json={"username": "alice", "password": "wrong-password-1"}
        )
        client.get("/api/auth/me")
    logged = caplog.text
    assert _PASSWORD not in logged
    assert "wrong-password-1" not in logged
    assert "token-" not in logged
    assert "scrypt$" not in logged
