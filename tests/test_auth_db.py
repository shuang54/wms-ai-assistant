"""认证 + 归属 DB 真链测试（Phase 7 Recovery M1）。

**默认跳过**（沿用项目既有 ``RUN_DB_TESTS`` 门控；专用测试库 = 本地开发库）：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_auth_db.py
```

前置：新表由官方机制创建（``python -m backend.app.db.init_db`` ——
``Base.metadata.create_all`` 幂等补建 ``app_user`` / ``user_session`` /
``resource_owner``；不手动改库、不用自动建表掩盖 migration 缺失）。

覆盖（真实 PostgreSQL）：

    * 用户创建与唯一性 / 密码只存哈希；
    * HTTP 登录 → 会话 → /me → 登出 → 会话失效（真实 Cookie 往返）；
    * 登录尝试限制（锁定后即使密码正确也拒绝；``locked_until`` 落库）；
    * 跨用户 / 无归属（历史资源）AgentRun 读取 ⇒ 404 同构拒绝；
    * 归属登记后同用户可读；API-Key 路径（无 user_id）行为不变。

清理（**定向**；沿用 residue 纪律，不用 TRUNCATE / 不清全表）：

    * ``agent_run`` / ``agent_step`` / ``agent_tool_result`` /
      ``agent_checkpoint`` / ``agent_decision`` ⇒ 按本模块创建的 run_id 删；
    * ``app_user`` / ``user_session`` / ``resource_owner`` ⇒ 会话水印
      （只删本模块新增：自增 id > 水印 / user_id LIKE 测试前缀）。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.db.agent_run_repository import AgentRunRepository
from backend.app.db.session import get_engine
from backend.app.config import settings
from backend.app.main import app
from backend.app.services.agent_read_service import (
    AgentReadService,
    AgentRunNotFoundError,
)
from backend.app.services.resource_ownership_service import (
    RESOURCE_TYPE_AGENT_RUN,
    ResourceOwnershipService,
)
from backend.app.services.user_service import UserService, get_user_service

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

needs_db = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable authentication PostgreSQL tests",
)

#: 测试用户名前缀（残留清理按前缀定向）。
_USER_PREFIX = "m1-test-user-"

__all__ = [
    "test_create_user_and_uniqueness",
    "test_password_stored_only_as_hash",
    "test_http_login_session_me_logout_flow",
    "test_lockout_after_max_failed_logins",
    "test_csrf_signature_round_trip_through_real_db",
    "test_csrf_rejects_tampered_token_on_real_session",
    "test_csrf_rejects_other_sessions_token",
    "test_failure_counter_increments_atomically",
    "test_agent_run_ownership_denies_cross_user",
    "test_agent_run_ownership_denies_legacy_resource",
    "test_agent_run_readable_after_ownership_bind",
    "test_agent_run_api_key_path_unchanged",
]


@pytest.fixture(scope="module")
def engine() -> Any:
    eng = get_engine()
    if eng is None:
        pytest.skip("DATABASE_URL 未配置，跳过认证 DB 集成测试")
    return eng


@pytest.fixture(scope="module", autouse=True)
def _residue_cleanup(engine: Any) -> Any:
    """模块级定向清理（前缀 + 时间/自增水印；不用 TRUNCATE / 不清全表）。

    * ``app_user`` 主键是 uuid 文本 ⇒ 按 username 前缀删；
    * ``user_session`` 无自增 id ⇒ 按 DB 端 ``created_at`` 水印删；
    * ``resource_owner`` 有自增 id ⇒ 按 id 水印删。
    """
    with engine.begin() as conn:
        started = conn.execute(text("SELECT now()")).scalar_one()
        owner_mark = int(
            conn.execute(
                text("SELECT COALESCE(MAX(id), 0) FROM ai_ops.resource_owner")
            ).scalar_one()
        )
    yield
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM ai_ops.app_user WHERE username LIKE :p"),
            {"p": f"{_USER_PREFIX}%"},
        )
        conn.execute(
            text("DELETE FROM ai_ops.user_session WHERE created_at > :t"),
            {"t": started},
        )
        conn.execute(
            text("DELETE FROM ai_ops.resource_owner WHERE id > :w"),
            {"w": owner_mark},
        )


def _username(tag: str) -> str:
    return f"{_USER_PREFIX}{tag}-{uuid.uuid4().hex[:8]}"


def _make_user(tag: str, password: str = "a-valid-password-1") -> str:
    view = get_user_service().create_user(
        username=_username(tag), password=password
    )
    return view.user_id


@needs_db
def test_create_user_and_uniqueness(engine: Any) -> None:
    service = UserService()
    username = _username("unique")
    first = service.create_user(username=username, password="a-valid-password-1")
    assert first.user_id and first.status == "ACTIVE"
    from backend.app.services.user_service import UserAlreadyExistsError

    with pytest.raises(UserAlreadyExistsError):
        service.create_user(username=username, password="another-password-1")
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM ai_ops.app_user WHERE username = :u"),
            {"u": username},
        ).scalar_one()
    assert count == 1


@needs_db
def test_password_stored_only_as_hash(engine: Any) -> None:
    password = "a-valid-password-1"
    user_id = _make_user("hash")
    with engine.connect() as conn:
        stored = conn.execute(
            text(
                "SELECT password_hash FROM ai_ops.app_user WHERE user_id = :i"
            ),
            {"i": user_id},
        ).scalar_one()
    assert stored.startswith("scrypt$")
    assert password not in stored


@needs_db
def test_http_login_session_me_logout_flow(engine: Any) -> None:
    password = "a-valid-password-1"
    user_id = _make_user("http")
    with engine.connect() as conn:
        username = conn.execute(
            text("SELECT username FROM ai_ops.app_user WHERE user_id = :i"),
            {"i": user_id},
        ).scalar_one()

    client = TestClient(app, base_url="https://testserver")
    login = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert login.status_code == 200, login.text
    assert login.json()["user_id"] == user_id

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == username

    # 错误密码 ⇒ 统一 401（真实 DB）
    bad = client.post(
        "/api/auth/login", json={"username": username, "password": "wrong-password-1"}
    )
    assert bad.status_code == 401
    assert bad.json()["detail"] == "认证失败：用户名或密码错误"

    csrf = client.cookies.get("wms_ai_csrf")
    logout = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 200
    assert client.get("/api/auth/me").status_code == 401

    # 会话行已被吊销（真实持久化）
    with engine.connect() as conn:
        revoked = conn.execute(
            text(
                "SELECT COUNT(*) FROM ai_ops.user_session "
                "WHERE user_id = :i AND revoked_at IS NOT NULL"
            ),
            {"i": user_id},
        ).scalar_one()
    assert revoked >= 1


@needs_db
def test_lockout_after_max_failed_logins(engine: Any) -> None:
    from backend.app.config import settings

    password = "a-valid-password-1"
    user_id = _make_user("lock")
    with engine.connect() as conn:
        username = conn.execute(
            text("SELECT username FROM ai_ops.app_user WHERE user_id = :i"),
            {"i": user_id},
        ).scalar_one()

    service = UserService()
    for _ in range(settings.auth.max_failed_logins):
        with pytest.raises(Exception):  # noqa: BLE001 —— InvalidCredentialsError
            service.authenticate(username=username, password="wrong-password-1")

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT failed_login_count, locked_until FROM ai_ops.app_user "
                "WHERE user_id = :i"
            ),
            {"i": user_id},
        ).one()
    assert int(row[0]) >= settings.auth.max_failed_logins
    assert row[1] is not None

    # 锁定期间即使密码正确也拒绝
    with pytest.raises(Exception):  # noqa: BLE001
        service.authenticate(username=username, password=password)


# ---------- CSRF（M1 Security Review：真实 DB 回路） ----------


def _login_client(engine: Any, tag: str) -> tuple[TestClient, str, str]:
    """真实登录 ⇒ (client, session_token, csrf_cookie)。"""
    user_id = _make_user(tag)
    with engine.connect() as conn:
        username = conn.execute(
            text("SELECT username FROM ai_ops.app_user WHERE user_id = :i"),
            {"i": user_id},
        ).scalar_one()
    client = TestClient(app, base_url="https://testserver")
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "a-valid-password-1"},
    )
    assert response.status_code == 200, response.text
    return (
        client,
        client.cookies.get(settings.auth.session_cookie_name),
        client.cookies.get(settings.auth.csrf_cookie_name),
    )


@needs_db
def test_csrf_signature_round_trip_through_real_db(engine: Any) -> None:
    """Cookie 中的 CSRF 值 == 服务端用真实会话重算的签名。"""
    from backend.app.services.session_service import get_session_service

    client, token, csrf = _login_client(engine, "csrf-round")
    assert token and csrf
    assert get_session_service().expected_csrf_token(token) == csrf
    # 登出（合法 CSRF）成功
    response = client.post(
        "/api/auth/logout", headers={settings.auth.csrf_header_name: csrf}
    )
    assert response.status_code == 200
    # 吊销后期望签名消失（旧 CSRF 值随之失效）
    assert get_session_service().expected_csrf_token(token) is None


@needs_db
def test_csrf_rejects_tampered_token_on_real_session(engine: Any) -> None:
    """伪造 CSRF 值（Cookie==Header）在真实会话上也被拒绝。"""
    client, _token, _csrf = _login_client(engine, "csrf-tamper")
    forged = "c" * 64
    client.cookies.set(settings.auth.csrf_cookie_name, forged)
    response = client.post(
        "/api/auth/logout", headers={settings.auth.csrf_header_name: forged}
    )
    assert response.status_code == 403
    # 会话仍然有效（副作用未发生）
    assert client.get("/api/auth/me").status_code == 200


@needs_db
def test_csrf_rejects_other_sessions_token(engine: Any) -> None:
    """另一个真实会话的 CSRF 值不能吊销当前会话。"""
    first, _token_a, _csrf_a = _login_client(engine, "csrf-a")
    second, _token_b, csrf_b = _login_client(engine, "csrf-b")
    response = first.post(
        "/api/auth/logout", headers={settings.auth.csrf_header_name: csrf_b}
    )
    assert response.status_code == 403
    assert first.get("/api/auth/me").status_code == 200
    assert second.get("/api/auth/me").status_code == 200


@needs_db
def test_failure_counter_increments_atomically(engine: Any) -> None:
    """失败计数原子自增：N 次调用 ⇒ 计数 == N（无 read-modify-write 丢失）。"""
    from backend.app.config import settings
    from backend.app.db.user_repository import UserRepository

    user_id = _make_user("atomic")
    repository = UserRepository()
    for _ in range(4):
        repository.apply_login_failure(
            user_id,
            max_failed_logins=settings.auth.max_failed_logins,
            lockout_minutes=settings.auth.lockout_minutes,
        )
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT failed_login_count, locked_until FROM ai_ops.app_user "
                "WHERE user_id = :i"
            ),
            {"i": user_id},
        ).one()
    assert int(row[0]) == 4
    # 未达阈值（默认 5）⇒ 不锁定
    assert row[1] is None


def _create_run_row(engine: Any, project_id: str) -> str:
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    AgentRunRepository().create(
        run_id=run_id,
        request_id=str(uuid.uuid4()),
        project_id=project_id,
        status="COMPLETED",
        error_code=None,
        limit_reason=None,
        step_count=1,
        tool_call_count=0,
        llm_call_count=1,
        token_usage_unverifiable=False,
        started_at=now,
        finished_at=now,
        duration_ms=1.0,
    )
    return run_id


def _read_service() -> AgentReadService:
    from backend.app.db.agent_approval_repository import AgentApprovalRepository
    from backend.app.db.agent_checkpoint_repository import (
        AgentCheckpointRepository,
    )
    from backend.app.db.agent_decision_repository import AgentDecisionRepository
    from backend.app.db.agent_run_repository import AgentRunRepository
    from backend.app.db.agent_step_repository import AgentStepRepository
    from backend.app.db.agent_tool_result_repository import (
        AgentToolResultRepository,
    )

    return AgentReadService(
        run_repository=AgentRunRepository(),
        step_repository=AgentStepRepository(),
        decision_repository=AgentDecisionRepository(),
        tool_result_repository=AgentToolResultRepository(),
        checkpoint_repository=AgentCheckpointRepository(),
        approval_repository=AgentApprovalRepository(),
    )


@needs_db
def test_agent_run_ownership_denies_cross_user(engine: Any) -> None:
    project_id = "vietnam-wms"
    owner = _make_user("owner-a")
    other = _make_user("owner-b")
    run_id = _create_run_row(engine, project_id)
    try:
        ResourceOwnershipService().bind_owner(
            resource_type=RESOURCE_TYPE_AGENT_RUN,
            resource_id=run_id,
            user_id=owner,
            project_id=project_id,
        )
        with pytest.raises(AgentRunNotFoundError):
            _read_service().get_run(run_id, project_id, user_id=other)
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM ai_ops.agent_run WHERE run_id = :r"),
                {"r": run_id},
            )


@needs_db
def test_agent_run_ownership_denies_legacy_resource(engine: Any) -> None:
    """M1 之前创建的资源（无归属行）在用户上下文一律拒绝（fail-closed）。"""
    project_id = "vietnam-wms"
    user_id = _make_user("legacy")
    run_id = _create_run_row(engine, project_id)
    try:
        with pytest.raises(AgentRunNotFoundError):
            _read_service().get_run(run_id, project_id, user_id=user_id)
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM ai_ops.agent_run WHERE run_id = :r"),
                {"r": run_id},
            )


@needs_db
def test_agent_run_readable_after_ownership_bind(engine: Any) -> None:
    project_id = "vietnam-wms"
    user_id = _make_user("bind")
    run_id = _create_run_row(engine, project_id)
    try:
        ResourceOwnershipService().bind_owner(
            resource_type=RESOURCE_TYPE_AGENT_RUN,
            resource_id=run_id,
            user_id=user_id,
            project_id=project_id,
        )
        view = _read_service().get_run(run_id, project_id, user_id=user_id)
        assert view.run is not None
        assert view.run.status == "COMPLETED"
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM ai_ops.agent_run WHERE run_id = :r"),
                {"r": run_id},
            )


@needs_db
def test_agent_run_api_key_path_unchanged(engine: Any) -> None:
    """API-Key 服务路径（不传 user_id）行为不变——兼容性不回归。"""
    project_id = "vietnam-wms"
    run_id = _create_run_row(engine, project_id)
    try:
        view = _read_service().get_run(run_id, project_id)
        assert view.run is not None
        assert view.run.run_id == run_id
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM ai_ops.agent_run WHERE run_id = :r"),
                {"r": run_id},
            )
