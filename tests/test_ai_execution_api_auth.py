"""M2.3 A1：四个 AI 执行入口的认证 / CSRF 边界（Phase 7 Recovery M2.3）。

范围（任务书 §八）：

    POST /api/ai/chat · POST /api/chat · POST /api/chat/with-tools ·
    POST /api/rag/answer

    1. 认证矩阵：未登录 / 无效 / 过期 / 已撤销 / 合法 / 认证服务故障；
    2. CSRF 矩阵：缺头 / 伪造 / 换会话（不匹配）/ 正确；
    3. 四个成功路径（既有 Service Fake，零真实 LLM / Tool / DB）；
    4. 副作用断言：所有失败路径 **LLM 调用 = 0、Tool 执行 = 0**
       （四个 Service Fake 的调用记录必须全部为空）。

安全语义（**不削弱**）：

    * 生产依赖 ``require_session`` / ``require_csrf`` **真实执行**——本文件
      **不**使用 ``dependency_overrides``（并以显式断言锁定该事实）；
    * 只替换**会话存储**（``tests/api_session_auth`` 的 Fake）与四个**业务
      Service**（既有 Fake 模式），不改认证代码路径；
    * 未登录 / 无效 / 过期 / 已撤销一律 401（固定文案，不泄露内部信息），
      认证服务故障 503（fail-closed，**绝不**降级为匿名）。

Breaking change（本阶段）：
    The four AI execution endpoints no longer permit anonymous execution.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import auth_dependency as auth_dep
from backend.app.api import chat as chat_module
from backend.app.api import orchestrator_chat as orch_module
from backend.app.api import rag as rag_module
from backend.app.api import tool_chat as tool_chat_module
from backend.app.config import settings
from backend.app.main import app
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    RouteType,
)
from backend.app.services.rag_service import RagResponse
from backend.app.services.session_service import SessionServiceError
from backend.app.services.tool_chat_service import ToolChatResponse
from tests.api_session_auth import (
    FakeSessionService,
    SessionTestClient,
    install_fake_session_service,
)

__all__ = [
    "test_anonymous_denied_on_all_four_endpoints",
    "test_invalid_session_denied_on_all_four_endpoints",
    "test_expired_session_denied_on_all_four_endpoints",
    "test_revoked_session_denied_on_all_four_endpoints",
    "test_missing_csrf_denied_on_all_four_endpoints",
    "test_forged_csrf_denied_on_all_four_endpoints",
    "test_csrf_from_another_session_denied",
    "test_valid_session_and_csrf_succeeds_on_all_four_endpoints",
    "test_auth_service_failure_fails_closed",
    "test_invalid_request_params_keep_existing_semantics",
    "test_no_dependency_overrides_are_used",
]

_NOT_AUTHENTICATED_DETAIL = "认证失败：缺少或无效的会话"
_CSRF_FAILED_DETAIL = "CSRF 校验失败"
_AUTH_UNAVAILABLE_DETAIL = "认证服务不可用"

#: (路径, 合法请求体, 该端点对应的业务 Service Fake 名称)
_ENDPOINTS: tuple[tuple[str, dict[str, Any], str], ...] = (
    ("/api/ai/chat", {"question": "查询物料 A001 库存"}, "orchestrator"),
    ("/api/chat", {"message": "采购入库怎么操作？"}, "chat"),
    ("/api/chat/with-tools", {"message": "查询物料 A001 库存"}, "tool_chat"),
    ("/api/rag/answer", {"query": "采购入库怎么操作？"}, "rag"),
)

_ENDPOINT_IDS = tuple(path for path, _body, _name in _ENDPOINTS)


# ============================================================
# 业务 Service Fake（零真实 LLM / Tool / DB）
# ============================================================

class _FakeOrchestrator:
    """``/api/ai/chat`` 的 Orchestrator 替身（记录调用）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(
        self, question: str, *, context: str | None = None
    ) -> AIOrchestrationResult:
        self.calls.append(question)
        return AIOrchestrationResult(
            route=RouteType.RAG,
            content="orchestrator-ok",
            data=None,
            metadata={"request_id": "req-a1"},
        )


class _FakeChatService:
    """``/api/chat`` 的 ChatService 替身（记录调用）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def chat(self, message: str) -> RagResponse:
        self.calls.append(message)
        return RagResponse(
            answer="chat-ok", sources=(), used_chunks_count=1
        )


class _FakeRagService:
    """``/api/rag/answer`` 的 RagService 替身（记录调用）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    async def answer(self, query: str, top_k: int | None = None) -> RagResponse:
        self.calls.append((query, top_k))
        return RagResponse(
            answer="rag-ok", sources=(), used_chunks_count=1
        )


class _FakeToolChatService:
    """``/api/chat/with-tools`` 的 ToolChatService 替身（记录调用）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def chat(
        self,
        message: str,
        *,
        registry: Any = None,
        execution_service: Any = None,
    ) -> ToolChatResponse:
        self.calls.append(message)
        return ToolChatResponse(answer="tool-chat-ok", tool_calls=())


@pytest.fixture()
def fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """安装四个业务 Service Fake（**不**改认证路径）。"""
    recorders: dict[str, Any] = {
        "orchestrator": _FakeOrchestrator(),
        "chat": _FakeChatService(),
        "rag": _FakeRagService(),
        "tool_chat": _FakeToolChatService(),
    }
    monkeypatch.setattr(orch_module, "_default_orchestrator", recorders["orchestrator"])
    monkeypatch.setattr(chat_module, "_chat_service", recorders["chat"])
    monkeypatch.setattr(rag_module, "_rag_service", recorders["rag"])
    monkeypatch.setattr(
        tool_chat_module, "_tool_chat_service", recorders["tool_chat"]
    )
    return recorders


def _assert_no_business_calls(fakes: dict[str, Any]) -> None:
    """失败路径必须零业务执行（LLM / Tool / RAG / Orchestrator 全部为 0）。"""
    for name, recorder in fakes.items():
        assert recorder.calls == [], f"{name} 被调用: {recorder.calls!r}"


def _login(service: FakeSessionService, username: str = "alice") -> Any:
    from tests.conversation_auth_support import USER_PASSWORDS

    return service.login(username=username, password=USER_PASSWORDS[username])


def _cookie_client(token: str, csrf: str | None = None) -> TestClient:
    """裸客户端（**不**自动带凭证）：携带给定 Cookie，可显式控制 CSRF 头。"""
    client = TestClient(app, base_url="https://testserver")
    client.cookies.set(settings.auth.session_cookie_name, token)
    if csrf is not None:
        client.cookies.set(settings.auth.csrf_cookie_name, csrf)
    return client


# ============================================================
# 1. 认证矩阵
# ============================================================

@pytest.mark.parametrize("path, body, _name", _ENDPOINTS, ids=_ENDPOINT_IDS)
def test_anonymous_denied_on_all_four_endpoints(
    path: str, body: dict[str, Any], _name: str, fakes: dict[str, Any]
) -> None:
    """未登录 ⇒ 401（固定文案）+ 零业务执行。"""
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post(path, json=body)

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == _NOT_AUTHENTICATED_DETAIL
    _assert_no_business_calls(fakes)


@pytest.mark.parametrize("path, body, _name", _ENDPOINTS, ids=_ENDPOINT_IDS)
def test_invalid_session_denied_on_all_four_endpoints(
    path: str, body: dict[str, Any], _name: str, fakes: dict[str, Any]
) -> None:
    """无效（伪造）会话 ⇒ 401 + 零业务执行。"""
    service = FakeSessionService()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(auth_dep, "get_session_service", lambda: service)
        client = _cookie_client("forged-token", csrf="forged-csrf")
        response = client.post(
            path, json=body, headers={settings.auth.csrf_header_name: "forged-csrf"}
        )

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == _NOT_AUTHENTICATED_DETAIL
    _assert_no_business_calls(fakes)


@pytest.mark.parametrize("path, body, _name", _ENDPOINTS, ids=_ENDPOINT_IDS)
def test_expired_session_denied_on_all_four_endpoints(
    path: str, body: dict[str, Any], _name: str, fakes: dict[str, Any]
) -> None:
    """过期会话 ⇒ 401 + 零业务执行。"""
    service = FakeSessionService()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(auth_dep, "get_session_service", lambda: service)
        result = _login(service)
        service.expire(result.token)
        client = _cookie_client(result.token, csrf=result.csrf_token)
        response = client.post(
            path,
            json=body,
            headers={settings.auth.csrf_header_name: result.csrf_token},
        )

    assert response.status_code == 401, response.text
    _assert_no_business_calls(fakes)


@pytest.mark.parametrize("path, body, _name", _ENDPOINTS, ids=_ENDPOINT_IDS)
def test_revoked_session_denied_on_all_four_endpoints(
    path: str, body: dict[str, Any], _name: str, fakes: dict[str, Any]
) -> None:
    """已撤销（登出后）会话 ⇒ 401 + 零业务执行。"""
    service = FakeSessionService()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(auth_dep, "get_session_service", lambda: service)
        result = _login(service)
        assert service.logout(result.token) is True
        client = _cookie_client(result.token, csrf=result.csrf_token)
        response = client.post(
            path,
            json=body,
            headers={settings.auth.csrf_header_name: result.csrf_token},
        )

    assert response.status_code == 401, response.text
    _assert_no_business_calls(fakes)


# ============================================================
# 2. CSRF 矩阵（已认证，但 CSRF 非法）
# ============================================================

@pytest.mark.parametrize("path, body, _name", _ENDPOINTS, ids=_ENDPOINT_IDS)
def test_missing_csrf_denied_on_all_four_endpoints(
    path: str, body: dict[str, Any], _name: str, fakes: dict[str, Any]
) -> None:
    """已认证但缺 CSRF（头与 Cookie 都不带）⇒ 403 + 零业务执行。"""
    with pytest.MonkeyPatch.context() as mp:
        service = install_fake_session_service(mp)
        result = _login(service)
        client = _cookie_client(result.token)  # 无 CSRF Cookie / 头
        response = client.post(path, json=body)

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == _CSRF_FAILED_DETAIL
    _assert_no_business_calls(fakes)


@pytest.mark.parametrize("path, body, _name", _ENDPOINTS, ids=_ENDPOINT_IDS)
def test_forged_csrf_denied_on_all_four_endpoints(
    path: str, body: dict[str, Any], _name: str, fakes: dict[str, Any]
) -> None:
    """已认证但 CSRF 为伪造值（Cookie == 头，但非服务端签名）⇒ 403 + 零业务执行。"""
    with pytest.MonkeyPatch.context() as mp:
        service = install_fake_session_service(mp)
        result = _login(service)
        client = _cookie_client(result.token, csrf="forged-csrf")
        response = client.post(
            path, json=body, headers={settings.auth.csrf_header_name: "forged-csrf"}
        )

    assert response.status_code == 403, response.text
    _assert_no_business_calls(fakes)


def test_csrf_from_another_session_denied(fakes: dict[str, Any]) -> None:
    """换会话（用 B 的 CSRF 值访问 A 的会话）⇒ 403（会话绑定签名）。"""
    with pytest.MonkeyPatch.context() as mp:
        service = install_fake_session_service(mp)
        alice = _login(service, "alice")
        bob = _login(service, "bob")
        client = _cookie_client(alice.token, csrf=bob.csrf_token)
        response = client.post(
            "/api/ai/chat",
            json={"question": "q"},
            headers={settings.auth.csrf_header_name: bob.csrf_token},
        )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == _CSRF_FAILED_DETAIL
    _assert_no_business_calls(fakes)


# ============================================================
# 3. 成功路径（既有业务契约不变）
# ============================================================

@pytest.mark.parametrize("path, body, _name", _ENDPOINTS, ids=_ENDPOINT_IDS)
def test_valid_session_and_csrf_succeeds_on_all_four_endpoints(
    path: str, body: dict[str, Any], _name: str, fakes: dict[str, Any]
) -> None:
    """合法会话 + 合法 CSRF ⇒ 200（既有响应契约），且对应 Service 恰好被调用一次。"""
    with pytest.MonkeyPatch.context() as mp:
        install_fake_session_service(mp)
        with SessionTestClient(app) as client:
            response = client.post(path, json=body)

    assert response.status_code == 200, response.text
    payload = response.json()

    if path == "/api/ai/chat":
        assert set(payload) == {"route", "content", "data", "metadata"}
        assert payload["content"] == "orchestrator-ok"
    elif path == "/api/chat":
        assert set(payload) == {"answer", "sources", "used_chunks_count"}
        assert payload["answer"] == "chat-ok"
    elif path == "/api/chat/with-tools":
        assert set(payload) == {"answer", "tool_calls"}
        assert payload["answer"] == "tool-chat-ok"
    else:
        assert set(payload) == {"answer", "sources", "used_chunks_count"}
        assert payload["answer"] == "rag-ok"

    assert len(fakes[_name].calls) == 1


def test_invalid_request_params_keep_existing_semantics(
    fakes: dict[str, Any],
) -> None:
    """已认证下参数校验语义不变：纯空白 question ⇒ 422；缺字段 ⇒ 422（零业务执行）。"""
    with pytest.MonkeyPatch.context() as mp:
        install_fake_session_service(mp)
        with SessionTestClient(app) as client:
            blank = client.post("/api/ai/chat", json={"question": "   "})
            missing = client.post("/api/ai/chat", json={})
            bad_top_k = client.post("/api/rag/answer", json={"query": "q", "top_k": 0})

    assert blank.status_code == 422
    assert missing.status_code == 422
    assert bad_top_k.status_code == 422
    _assert_no_business_calls(fakes)


# ============================================================
# 4. fail-closed 与"不绕过认证"元断言
# ============================================================

def test_auth_service_failure_fails_closed(fakes: dict[str, Any]) -> None:
    """认证服务异常 ⇒ 503（fail-closed；**绝不**匿名放行）+ 零业务执行。"""

    class _BrokenSessionService:
        def validate(self, token: str) -> Any:  # pragma: no cover - 注入
            raise SessionServiceError("session store down")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            auth_dep, "get_session_service", lambda: _BrokenSessionService()
        )
        client = _cookie_client("some-token", csrf="some-csrf")
        response = client.post(
            "/api/ai/chat",
            json={"question": "q"},
            headers={settings.auth.csrf_header_name: "some-csrf"},
        )

    assert response.status_code == 503, response.text
    assert response.json()["detail"] == _AUTH_UNAVAILABLE_DETAIL
    _assert_no_business_calls(fakes)


def test_no_dependency_overrides_are_used() -> None:
    """元断言：本阶段**不**通过 ``dependency_overrides`` 绕过认证。"""
    assert app.dependency_overrides == {}
