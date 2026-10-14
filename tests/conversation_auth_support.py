"""Conversation API 认证/归属测试支撑（Phase 7 Recovery M2.2）。

**非测试文件**（pytest 不收集）：为 Conversation HTTP 测试提供统一的
"已登录客户端 + Fake 归属/项目注册表"装配，避免每个测试文件重复造轮子。

离线路径（Fake，零 DB / 零网络 / 零 LLM）：

    patch_conversation_api(monkeypatch, service, ...) → (client, ownership)
        · monkeypatch ``conversations_module.get_conversation_service`` → 测试自带 Fake；
        * monkeypatch ``get_conversation_ownership_service`` → FakeResourceOwnershipService
          （预绑定 seed 的会话；create 经 API 时自动绑定——**不**自动认领未绑定资源）；
        * monkeypatch ``get_conversation_project_registry`` → FakeProjectRegistry；
        * monkeypatch ``auth_dependency.get_session_service`` → FakeSessionService；
        * 返回的 ConversationTestClient 已登录并自动附带 CSRF 头（不安全方法）。

DB 路径（真实链路）：

    ensure_user(...) / login_over_http(...)  —— 真实建用户 + 真实 HTTP 登录
    （与 tests/test_auth_db.py 同模式；RUN_DB_TESTS 门控）。

复用生产安全语义（**不**削弱）：FakeSessionService 复用生产
``sign_csrf_token`` / ``hash_session_token``；CSRF 头由客户端按 M1 双提交
规则自动附加——测试因此**必须**像真实前端一样携带 CSRF 头。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import auth_dependency as auth_dep
from backend.app.api import conversations as conversations_module
from backend.app.auth.password import hash_password
from backend.app.auth.tokens import (
    generate_session_token,
    hash_session_token,
    sign_csrf_token,
)
from backend.app.config import settings
from backend.app.db.resource_owner_repository import ResourceOwnerRow
from backend.app.db.session import get_session_factory
from backend.app.dto.assistant_outcome import AssistantOutcome
from backend.app.main import app
from backend.app.projects.models import DataSource, ProjectContext
from backend.app.projects.registry import (
    DEFAULT_BUSINESS_SCHEMA,
    ProjectNotFoundError,
    ProjectRegistration,
    get_default_project_registry,
)
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    RouteType,
)
from backend.app.services.resource_ownership_service import (
    RESOURCE_TYPE_CONVERSATION,
    ResourceOwnershipDeniedError,
    ResourceOwnershipError,
)
from backend.app.services.session_service import (
    LoginResult,
    Principal,
    SessionServiceError,
)
from backend.app.services.user_service import (
    UserAlreadyExistsError,
    get_user_service,
)

__all__ = [
    "FakeSessionService",
    "FakeResourceOwnershipService",
    "FakeProjectRegistry",
    "FakeChatApplicationService",
    "AutoCsrfTestClient",
    "ConversationTestClient",
    "patch_conversation_api",
    "patch_message_api_auth",
    "ensure_user",
    "login_over_http",
    "create_and_login",
    "delete_users",
    "register_test_projects",
    "unregister_test_projects",
    "USER_PASSWORDS",
]

#: 测试用户 → 口令（显式夹具值；**非**真实凭据）。
USER_PASSWORDS: dict[str, str] = {
    "alice": "password-alice-1",
    "bob": "password-bob-12",
    "carol": "password-carol-1",
}

#: 测试用户的稳定 user_id（Fake 会话路径使用；DB 路径用真实 uuid）。
USER_IDS: dict[str, str] = {
    "alice": "11111111-1111-4111-8111-111111111111",
    "bob": "22222222-2222-4222-8222-222222222222",
    "carol": "33333333-3333-4333-8333-333333333333",
}

#: 口令哈希缓存（scrypt 昂贵；同一进程内复用——仅测试固定夹具值）。
_PASSWORD_HASH_CACHE: dict[str, str] = {}


def _cached_password_hash(username: str) -> str:
    if username not in _PASSWORD_HASH_CACHE:
        _PASSWORD_HASH_CACHE[username] = hash_password(USER_PASSWORDS[username])
    return _PASSWORD_HASH_CACHE[username]


class FakeSessionService:
    """进程内 Fake 会话服务（多用户；复用生产签名函数）。"""

    def __init__(self) -> None:
        # 口令哈希进程内缓存复用（scrypt 昂贵；语义仍走生产 verify_password）
        self._password_hashes = {
            name: _cached_password_hash(name) for name in USER_PASSWORDS
        }
        self._sessions: dict[str, dict[str, Any]] = {}
        self._next = 0

    # ---------- 生产契约 ----------

    def login(self, *, username: str, password: str) -> LoginResult:
        from backend.app.auth.password import verify_password

        stored = self._password_hashes.get(username)
        if stored is None or not verify_password(password, stored):
            from backend.app.services.user_service import InvalidCredentialsError

            raise InvalidCredentialsError("认证失败")
        self._next += 1
        session_id = f"session-{self._next}"
        token = f"token-{self._next}"
        token_hash = hash_session_token(token)
        self._sessions[token] = {
            "session_id": session_id,
            "token_hash": token_hash,
            "username": username,
            "revoked": False,
            "expired": False,
        }
        return LoginResult(
            session_id=session_id,
            token=token,
            principal=Principal(
                user_id=USER_IDS[username],
                username=username,
                display_name=username.title(),
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
            csrf_token=sign_csrf_token(session_id=session_id, key=token_hash),
        )

    def validate(self, token: str) -> Principal | None:
        row = self._live_row(token)
        if row is None:
            return None
        return Principal(
            user_id=USER_IDS[row["username"]],
            username=row["username"],
            display_name=row["username"].title(),
        )

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

    # ---------- 测试辅助 ----------

    def expire(self, token: str) -> None:
        row = self._sessions.get(token)
        if row is not None:
            row["expired"] = True

    def _live_row(self, token: str) -> dict[str, Any] | None:
        row = self._sessions.get(token)
        if row is None or row["revoked"] or row["expired"]:
            return None
        return row


class FakeResourceOwnershipService:
    """进程内 Fake 归属服务（显式绑定；**不**自动认领）。

    ``mirror_service``（可选）：**仅**供 HTTP 契约测试使用——当归属表中无行时，
    以 ``FakeConversationService._conversations`` 的存在性作为"该会话属于当前
    测试用户"的替身（契约测试不测授权语义；授权语义由
    ``tests/test_conversation_ownership.py`` 的 strict 模式覆盖）。
    **不**在安全语义测试中使用（缺省为 None ⇒ 严格 fail-closed）。
    """

    def __init__(self, *, mirror_service: Any | None = None) -> None:
        self._rows: dict[tuple[str, str], ResourceOwnerRow] = {}
        self._next = 0
        self.failure: Exception | None = None
        self._mirror_service = mirror_service

    def fail_with(self, error: Exception) -> None:
        """让后续调用抛出（模拟归属服务 / DB 故障）。"""
        self.failure = error

    def bind_owner(
        self,
        *,
        resource_type: str,
        resource_id: str,
        user_id: str,
        project_id: str,
    ) -> ResourceOwnerRow:
        if self.failure is not None:
            raise self.failure
        self._next += 1
        row = ResourceOwnerRow(
            id=self._next,
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=user_id,
            project_id=project_id,
            created_at=datetime.now(timezone.utc),
        )
        self._rows[(resource_type, resource_id)] = row
        return row

    def get_owner(
        self, *, resource_type: str, resource_id: str
    ) -> ResourceOwnerRow | None:
        if self.failure is not None:
            raise self.failure
        return self._rows.get((resource_type, resource_id))

    def assert_can_read(
        self, *, resource_type: str, resource_id: str, user_id: str
    ) -> None:
        if self.failure is not None:
            raise self.failure
        row = self._rows.get((resource_type, resource_id))
        if row is not None:
            if row.user_id != user_id:
                raise ResourceOwnershipDeniedError("资源不可访问")
            return
        if self._mirror_exists(resource_id):
            # 契约测试模式：Fake ConversationService 中存在的会话视为当前用户所有
            return
        raise ResourceOwnershipDeniedError("资源不可访问")

    def _mirror_exists(self, resource_id: str) -> bool:
        """镜像 Fake ConversationService 的存在性（直接读属性；无副作用）。"""
        if self._mirror_service is None:
            return False
        conversations = getattr(self._mirror_service, "_conversations", None)
        return isinstance(conversations, dict) and resource_id in conversations

    def list_user_resources(
        self,
        user_id: str,
        *,
        resource_type: str | None = None,
        limit: int = 50,
    ) -> tuple[ResourceOwnerRow, ...]:
        rows = [
            row
            for row in self._rows.values()
            if row.user_id == user_id
            and (resource_type is None or row.resource_type == resource_type)
        ]
        return tuple(rows[: max(1, min(int(limit), 200))])


class FakeProjectRegistry:
    """进程内 Fake 项目注册表（仅存在性校验）。"""

    def __init__(self, known_projects: tuple[str, ...] = ("vietnam-wms",)) -> None:
        self._known = set(known_projects)

    def get(self, project_id: str) -> Any:
        if project_id not in self._known:
            raise ProjectNotFoundError(f"项目 '{project_id}' 未注册（测试 Fake）")
        return None


class FakeChatApplicationService:
    """ChatApplicationService 替身（仅测试；零 AI / 零 DB）。

    与 ``tests/test_conversation_message_api_contract.py`` 的 Fake 同构：
    记录调用并返回固定 ``AIOrchestrationResult``（**不**执行任何 AI、
    不触达 DB）。认证 / 归属测试只关心"请求是否抵达 Application 层"。
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute_message(
        self,
        *,
        conversation_id: str,
        content: str,
        idempotency_key: str | None = None,
    ) -> AIOrchestrationResult:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "content": content,
                "idempotency_key": idempotency_key,
            }
        )
        return AIOrchestrationResult(
            route=RouteType.TEXT_TO_SQL,
            content="回答",
            data=None,
            metadata={
                "request_id": "req-fake-0001",
                "outcome": AssistantOutcome.SUCCESS,
            },
        )


class AutoCsrfTestClient(TestClient):
    """自动携带 CSRF 头的 TestClient（真实会话 / Fake 会话通用）。

    模拟真实前端：不安全方法自动附加 ``X-CSRF-Token`` = 当前 CSRF Cookie 值
    （双提交）；需要验证 CSRF 缺失 / 不匹配的用例请显式不带该头。
    """

    def request(self, method: str, url: str, **kwargs: Any) -> Any:  # type: ignore[override]
        headers = dict(kwargs.pop("headers", None) or {})
        if (
            method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
            and settings.auth.csrf_header_name not in headers
        ):
            csrf = self.cookies.get(settings.auth.csrf_cookie_name)
            if csrf:
                headers[settings.auth.csrf_header_name] = csrf
        kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


class ConversationTestClient(AutoCsrfTestClient):
    """已登录的 TestClient（Fake 会话）：自动携带会话 Cookie 与 CSRF 头。"""

    def __init__(
        self,
        application: Any = app,
        *,
        session_service: FakeSessionService,
        username: str = "alice",
        base_url: str = "https://testserver",
    ) -> None:
        super().__init__(application, base_url=base_url)
        self._session_service = session_service
        self._username = username
        result = session_service.login(
            username=username, password=USER_PASSWORDS[username]
        )
        self.cookies.set(settings.auth.session_cookie_name, result.token)
        self.cookies.set(settings.auth.csrf_cookie_name, result.csrf_token)
        self.session_token = result.token


#: ``allowed_project_id`` 未提供 → **不**替换部署配置（使用真实配置项目）
_UNSET: Any = object()


def patch_conversation_api(
    monkeypatch: Any,
    service: Any,
    *,
    username: str = "alice",
    known_projects: tuple[str, ...] = ("vietnam-wms",),
    allowed_project_id: Any = _UNSET,
    ownership: FakeResourceOwnershipService | None = None,
    mirror_conversations: bool = False,
    patch_chat_service: bool = True,
) -> tuple[ConversationTestClient, FakeResourceOwnershipService]:
    """装配 Conversation API 的认证 / 归属 / 项目校验（全部 Fake）。

    Args:
        allowed_project_id: **部署允许项目**（D1 加固）的替身；缺省
            （``_UNSET``）不替换 ⇒ 使用真实配置项目。显式传 ``None`` 表示
            "配置不可解析"（验证 fail-closed 503）。注意它与 ``known_projects``
            （注册表）**语义独立**：已注册 ≠ 已授权。
        mirror_conversations: **契约测试模式**——归属缺省以 Fake Service 中
            会话的存在性为准（见 ``FakeResourceOwnershipService``）；安全语义
            测试必须保持默认 False（严格 fail-closed）。
        patch_chat_service: 是否替换消息端点的 Application 层（默认 True）；
            消息契约测试自带 ChatApplicationService Fake 时置 False。
    """
    if allowed_project_id is not _UNSET:
        monkeypatch.setattr(
            conversations_module,
            "get_conversation_allowed_project_id",
            lambda: allowed_project_id,
        )
    own = (
        ownership
        if ownership is not None
        else FakeResourceOwnershipService(
            mirror_service=service if mirror_conversations else None
        )
    )
    user_id = USER_IDS[username]

    # seed 的会话预绑定给当前用户（**不**自动认领——显式绑定）
    for conversation_id, view in list(getattr(service, "_conversations", {}).items()):
        own.bind_owner(
            resource_type=RESOURCE_TYPE_CONVERSATION,
            resource_id=conversation_id,
            user_id=user_id,
            project_id=view.project_id,
        )

    original_create = service.create_conversation

    def create_conversation(*, project_id: str, owner_user_id: str | None = None):
        view = original_create(project_id=project_id)
        if owner_user_id is not None:
            own.bind_owner(
                resource_type=RESOURCE_TYPE_CONVERSATION,
                resource_id=view.conversation_id,
                user_id=owner_user_id,
                project_id=view.project_id,
            )
        return view

    monkeypatch.setattr(service, "create_conversation", create_conversation)
    monkeypatch.setattr(
        conversations_module, "get_conversation_service", lambda: service
    )
    # 消息端点：替换 Application 层（零 AI / 零 DB）；测试可经模块 accessor 取回
    if patch_chat_service:
        chat = FakeChatApplicationService()
        monkeypatch.setattr(
            conversations_module, "get_chat_application_service", lambda: chat
        )
    monkeypatch.setattr(
        conversations_module,
        "get_conversation_ownership_service",
        lambda: own,
    )
    monkeypatch.setattr(
        conversations_module,
        "get_conversation_project_registry",
        lambda: FakeProjectRegistry(known_projects),
    )
    sessions = FakeSessionService()
    monkeypatch.setattr(auth_dep, "get_session_service", lambda: sessions)
    client = ConversationTestClient(app, session_service=sessions, username=username)
    return client, own


def patch_message_api_auth(
    monkeypatch: Any,
    *,
    conversation_ids: tuple[str, ...],
    username: str = "alice",
    known_projects: tuple[str, ...] = ("vietnam-wms",),
) -> ConversationTestClient:
    """消息端点契约测试的**最小认证装配**（不替换任何业务 Service）。

    只装配 M2.2 的三项前置：M1 会话认证 + CSRF、归属（给定会话 ID 预绑定到
    测试用户）、服务端项目注册表。ConversationService / ChatApplicationService
    由调用方按各文件既有方式 Fake（保持原语义）。
    """
    ownership = FakeResourceOwnershipService()
    user_id = USER_IDS[username]
    for conversation_id in conversation_ids:
        ownership.bind_owner(
            resource_type=RESOURCE_TYPE_CONVERSATION,
            resource_id=conversation_id,
            user_id=user_id,
            project_id="vietnam-wms",
        )
    monkeypatch.setattr(
        conversations_module, "get_conversation_ownership_service", lambda: ownership
    )
    monkeypatch.setattr(
        conversations_module,
        "get_conversation_project_registry",
        lambda: FakeProjectRegistry(known_projects),
    )
    sessions = FakeSessionService()
    monkeypatch.setattr(auth_dep, "get_session_service", lambda: sessions)
    return ConversationTestClient(app, session_service=sessions, username=username)


# ---------- DB 路径（真实链路；RUN_DB_TESTS 门控由调用方负责） ----------


def ensure_user(username: str) -> tuple[str, str]:
    """建测试用户（真实 DB；用户名带 ``m22-`` 前缀，便于残留识别）。

    Returns:
        (实际用户名, user_id) —— 用户名含随机后缀，登录必须使用返回值。
    """
    password = USER_PASSWORDS.get(username, "password-default-1")
    service = get_user_service()
    try:
        view = service.create_user(
            username=f"m22-{username}-{uuid.uuid4().hex[:8]}",
            password=password,
        )
    except UserAlreadyExistsError:  # pragma: no cover - 随机后缀不可能冲突
        raise
    return _username_of(view.user_id), view.user_id


def _username_of(user_id: str) -> str:
    factory = get_session_factory()
    assert factory is not None, "创建用户后必须能读回用户名（DB 未配置）"
    with factory() as session:
        return str(
            session.execute(
                text("SELECT username FROM ai_ops.app_user WHERE user_id = :i"),
                {"i": user_id},
            ).scalar_one()
        )


def login_over_http(client: TestClient, *, username: str, password: str) -> str:
    """真实 HTTP 登录（真实 DB）；返回会话令牌。"""
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    token = client.cookies.get(settings.auth.session_cookie_name)
    assert token
    return token


def create_and_login(client: TestClient, username: str) -> str:
    """建用户 + HTTP 登录（DB 路径）；返回 user_id。

    调用方负责把 user_id 登记到自己的残留清理集合（``delete_users``）。
    """
    password = USER_PASSWORDS.get(username, "password-default-1")
    actual_username, user_id = ensure_user(username)
    login_over_http(client, username=actual_username, password=password)
    return user_id


def delete_users(user_ids: list[str] | tuple[str, ...] | set[str]) -> None:
    """定向删除测试用户及其会话（**仅**本模块记录的真实 user_id）。"""
    if not user_ids:
        return
    factory = get_session_factory()
    assert factory is not None, "用户清理需要 DB（配置丢失）"
    with factory() as session, session.begin():
        for user_id in user_ids:
            session.execute(
                text("DELETE FROM ai_ops.user_session WHERE user_id = :u"),
                {"u": user_id},
            )
            session.execute(
                text("DELETE FROM ai_ops.app_user WHERE user_id = :u"),
                {"u": user_id},
            )


def register_test_projects(project_ids: tuple[str, ...]) -> tuple[str, ...]:
    """在**服务端默认注册表**登记测试项目（M2.2 D1）。

    Returns:
        本次实际注册的 project_id（已存在的项目不重复注册、也不返回）——
        调用方在模块结束时对返回值执行 ``unregister_test_projects``。
    """
    registry = get_default_project_registry()
    existing = set(registry.list_project_ids())
    registered: list[str] = []
    for project_id in project_ids:
        if project_id in existing:
            continue
        registry.register(
            project_id,
            ProjectRegistration(
                context=ProjectContext(
                    project_id=project_id,
                    project_name=f"Project {project_id}",
                    description=None,
                    data_source=DataSource(name="primary", type="postgresql"),
                ),
                schema_name=DEFAULT_BUSINESS_SCHEMA,
            ),
        )
        registered.append(project_id)
    return tuple(registered)


def unregister_test_projects(project_ids: tuple[str, ...]) -> None:
    """卸载 ``register_test_projects`` 实际注册的项目（不触碰既有注册）。"""
    registry = get_default_project_registry()
    for project_id in project_ids:
        registry.unregister(project_id)
