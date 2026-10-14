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

from typing import Any

import pytest

from backend.app.api import auth_dependency as auth_dep
from backend.app.config import settings
from backend.app.main import app
from tests.conversation_auth_support import (
    AutoCsrfTestClient,
    FakeSessionService,
    USER_PASSWORDS,
)

__all__ = [
    "SessionTestClient",
    "FakeSessionService",
    "api_auth_session",
    "install_fake_session_service",
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
