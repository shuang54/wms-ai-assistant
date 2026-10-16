"""A1 认证测试支撑（Phase 7 Recovery M2.3 —— **测试专用**，无生产代码）。

背景：M2.3 A1 起，四个 AI 执行入口（``POST /api/ai/chat`` · ``/api/chat`` ·
``/api/chat/with-tools`` · ``/api/rag/answer``）**不再允许匿名执行**。
既有调用这四个端点的测试必须携带 M1 用户会话（+ CSRF），本模块提供最小
可复用的客户端，**不**改写任何断言。

安全语义（**不削弱**，与生产一致）：

    * 生产依赖 ``require_session`` / ``require_csrf`` **真实执行**——本模块
      **不**使用 ``dependency_overrides``，也不绕过认证；
    * 只替换**会话存储**（``auth_dep.get_session_service`` → ``FakeSessionService``，
      与 ``tests/conversation_auth_support.py`` 同一实现）——测试因此不需要
      在 DB 建用户 / 会话，也不产生 ``app_user`` / ``user_session`` 残留；
    * 未携带 Cookie 的请求仍然 401（匿名语义不变），错误 Cookie 仍然 401。

用法（既有测试文件的最小改造）：

    from tests.api_session_auth import SessionTestClient as TestClient

    pytestmark = pytest.mark.usefixtures("api_auth_session")   # conftest 提供

    with TestClient(app) as client:      # 自动携带会话 Cookie + CSRF 头
        client.post("/api/ai/chat", json={"question": "q"})

需要显式构造客户端时（不经 fixture）：先安装 Fake 会话服务，再构造
``SessionTestClient``（未安装时构造会**立即报错**，避免误用真实 DB 会话）。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pytest

from backend.app.api import auth_dependency as auth_dep
from backend.app.api import authorization_dependency as authorization_dependency_module
from backend.app.api import observability_read_auth as observability_read_auth_module
from backend.app.config import ObservabilityReadSettings, settings
from backend.app.db.models.authorization_grant import (
    PROJECT_ROLE_MEMBER,
    PROJECT_ROLE_PROJECT_OPS,
)
from backend.app.main import app
from backend.app.services.authorization_service import (
    AuthorizationStoreUnavailableError,
)
from tests.conversation_auth_support import (
    AutoCsrfTestClient,
    FakeSessionService,
    USER_PASSWORDS,
    USER_IDS,
)

__all__ = [
    "SessionTestClient",
    "FakeSessionService",
    "FakeAuthorizationService",
    "api_auth_session",
    "observability_ops_session",
    "observability_project_scope_session",
    "install_fake_session_service",
    "install_observability_ops_allowlist",
    "install_observability_project_scope",
    "install_authorization_grants",
]


def install_fake_session_service(monkeypatch: Any) -> FakeSessionService:
    """把会话存储替换为进程内 Fake（**仅**会话存储；依赖链不变）。

    Args:
        monkeypatch: ``pytest.MonkeyPatch``（fixture 或 ``MonkeyPatch.context()``）。

    Returns:
        已安装的 ``FakeSessionService``（用于断言 / 直接登录）。
    """
    service = FakeSessionService()
    monkeypatch.setattr(auth_dep, "get_session_service", lambda: service)
    return service


@pytest.fixture()
def api_auth_session(monkeypatch: pytest.MonkeyPatch) -> FakeSessionService:
    """安装 A1 测试用 Fake 会话服务（非 autouse：仅显式请求的测试生效）。

    生产依赖 ``require_session`` / ``require_csrf`` 仍然真实执行；本夹具
    只让"会话存储"变成进程内实现（无 DB / 无残留）。
    """
    return install_fake_session_service(monkeypatch)


def install_observability_ops_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    user_ids: Iterable[str],
) -> None:
    """安装 M2.3 A2 运维白名单（**仅**会话存储之上的授权配置）。

    只替换配置来源（``get_observability_read_settings``）；不触碰
    ``require_session`` / ``require_observability_ops`` 本身——生产认证与
    授权链路仍然真实执行。
    """
    monkeypatch.setattr(
        observability_read_auth_module,
        "get_observability_read_settings",
        lambda: ObservabilityReadSettings(
            ops_user_ids=",".join(sorted(set(user_ids)))
        ),
    )


def install_observability_project_scope(
    monkeypatch: pytest.MonkeyPatch,
    project_id: str | None,
) -> None:
    """安装 M2.3 A2 服务端项目作用域（``None`` = 模拟"配置不可解析"）。"""
    monkeypatch.setattr(
        observability_read_auth_module,
        "get_observability_project_id",
        lambda: project_id,
    )


class FakeAuthorizationService:
    """M2.4 授权服务替身（进程内；零 DB / 零残留）。

    只替换**授权数据来源**；``require_observability_ops`` /
    ``require_observability_project_scope`` 与 ``AuthorizationService`` 的
    判定语义（scope ↔ role 一致性、项目级 ≠ 系统级）仍由真实代码执行。
    """

    def __init__(
        self,
        *,
        project_roles: Mapping[tuple[str, str], Iterable[str]] | None = None,
        system_ops: Iterable[str] | None = None,
        failure: str | None = None,
    ) -> None:
        """
        Args:
            project_roles: ``{(user_id, project_id): {role, ...}}``。
            system_ops: 持有 ``SYSTEM_OPS`` 的 user_id 集合。
            failure: ``"unavailable"`` = 模拟授权存储故障（⇒ 503）；
                ``"not_configured"`` = 模拟未配置（⇒ 无授权，不报错）。
        """
        self._project_roles = {
            key: frozenset(value)
            for key, value in (project_roles or {}).items()
        }
        self._system_ops = frozenset(system_ops or ())
        self._failure = failure

    def _guard(self) -> None:
        """模拟 ``AuthorizationService`` 的**对外契约**。

        * ``unavailable`` ⇒ 抛 :class:`AuthorizationStoreUnavailableError`
          （调用方映射 503）；
        * ``not_configured`` ⇒ **不**抛异常、返回"无授权" —— 与真实
          ``AuthorizationService`` 把 ``AuthorizationGrantStoreNotConfiguredError``
          转为 ``None``/``False`` 的语义一致（无 DB 部署的既有形态）。
        """
        if self._failure == "unavailable":
            raise AuthorizationStoreUnavailableError("authorization store down")
        if self._failure == "not_configured":
            self._project_roles = {}
            self._system_ops = frozenset()

    def project_role(self, user_id: str, project_id: str) -> str | None:
        self._guard()
        roles = self._project_roles.get((user_id, project_id), frozenset())
        if PROJECT_ROLE_PROJECT_OPS in roles:
            return PROJECT_ROLE_PROJECT_OPS
        if PROJECT_ROLE_MEMBER in roles:
            return PROJECT_ROLE_MEMBER
        return None

    def is_project_member(self, user_id: str, project_id: str) -> bool:
        return self.project_role(user_id, project_id) is not None

    def is_project_ops(self, user_id: str, project_id: str) -> bool:
        return self.project_role(user_id, project_id) == PROJECT_ROLE_PROJECT_OPS

    def is_system_ops(self, user_id: str) -> bool:
        self._guard()
        return user_id in self._system_ops


def _install_authorization_service(
    monkeypatch: pytest.MonkeyPatch,
    service: object,
) -> None:
    """把授权组合根替换为 ``service``（**单一补丁点**）。

    ``authorization_dependency`` 是授权服务的组合根；``observability_read_auth``
    经其模块属性取服务，因此只需替换 ``authorization_dependency`` 上的函数。
    """
    monkeypatch.setattr(
        authorization_dependency_module,
        "get_authorization_service",
        lambda: service,
    )


def install_authorization_grants(
    monkeypatch: pytest.MonkeyPatch,
    *,
    project_roles: Mapping[tuple[str, str], Iterable[str]] | None = None,
    system_ops: Iterable[str] | None = None,
    failure: str | None = None,
) -> FakeAuthorizationService:
    """安装 M2.4 授权关系（进程内 Fake；不触达 ``ai_ops.authorization_grant``）。

    Args:
        monkeypatch: ``pytest.MonkeyPatch``。
        project_roles: ``{(user_id, project_id): {role, ...}}``。
        system_ops: 持有 ``SYSTEM_OPS`` 的 user_id 集合。
        failure: ``"unavailable"`` 模拟存储故障；``"not_configured"`` 模拟未配置。

    Returns:
        已安装的 :class:`FakeAuthorizationService`。
    """
    service = FakeAuthorizationService(
        project_roles=project_roles,
        system_ops=system_ops,
        failure=failure,
    )
    _install_authorization_service(monkeypatch, service)
    return service


@pytest.fixture()
def observability_ops_session(monkeypatch: pytest.MonkeyPatch) -> FakeSessionService:
    """A2/M2.4 运维专用端点的测试装配。

    语义：alice 是**旧白名单**运维用户（``OBSERVABILITY_READ_OPS_USER_IDS``），
    且**没有** ``SYSTEM_OPS`` 授权行 —— 因此本夹具覆盖的是过渡期并集的
    "旧白名单"分支（M2.4 的 ``SYSTEM_OPS`` 分支由专门的测试安装）。

    同时安装"授权存储已配置但为空"的替身：判定仍会经过 M2.4 的授权表分支
    （走真实代码），只是没有授予行 ⇒ 由旧白名单兜住。
    """
    service = install_fake_session_service(monkeypatch)
    install_observability_ops_allowlist(monkeypatch, {USER_IDS["alice"]})
    install_authorization_grants(monkeypatch)
    return service


@pytest.fixture()
def observability_project_scope_session(
    monkeypatch: pytest.MonkeyPatch,
) -> FakeSessionService:
    """A2/M2.4 项目作用域端点的测试装配。

    语义：已登录会话 + 服务端项目作用域 ``vietnam-wms`` + 该项目 ``MEMBER``
    授权（M2.4 D2 后项目级端点要求成员授权）。

    需要验证"作用域不可用"的用例请显式调用
    ``install_observability_project_scope(monkeypatch, None)``；
    需要验证"非成员 ⇒ 403"的用例请用 ``install_authorization_grants``
    覆盖成员集合。
    """
    service = install_fake_session_service(monkeypatch)
    install_observability_project_scope(monkeypatch, "vietnam-wms")
    install_authorization_grants(
        monkeypatch,
        project_roles={(USER_IDS["alice"], "vietnam-wms"): {PROJECT_ROLE_MEMBER}},
    )
    return service


class SessionTestClient(AutoCsrfTestClient):
    """已登录的 TestClient：自动携带 M1 会话 Cookie + CSRF 头。

    依赖已安装的会话服务（测试中由 ``api_auth_session`` 安装 Fake）——
    未安装时构造立即抛 ``AssertionError``（避免误连真实数据库会话）。
    """

    def __init__(
        self,
        application: Any = app,
        *,
        username: str = "alice",
        base_url: str = "https://testserver",
        **kwargs: Any,
    ) -> None:
        """构造已登录客户端（``**kwargs`` 原样透传 ``TestClient``）。

        例：``SessionTestClient(app, raise_server_exceptions=False)``。
        """
        super().__init__(application, base_url=base_url, **kwargs)
        service = auth_dep.get_session_service()
        if not isinstance(service, FakeSessionService):
            raise AssertionError(
                "SessionTestClient 需要先安装 Fake 会话服务："
                "在测试文件声明 pytestmark = pytest.mark.usefixtures("
                "'api_auth_session')，或显式调用 install_fake_session_service()。"
            )
        result = service.login(
            username=username, password=USER_PASSWORDS[username]
        )
        self.cookies.set(settings.auth.session_cookie_name, result.token)
        self.cookies.set(settings.auth.csrf_cookie_name, result.csrf_token)
        self.session_token = result.token
