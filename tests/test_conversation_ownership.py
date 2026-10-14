"""Conversation 认证与资源所有权测试（Phase 7 Recovery M2.2）。

覆盖（任务书 §六 矩阵）：

    认证：未登录 / 无效 / 过期 / 已撤销 Session / 登出后重放 / 缺 CSRF /
          CSRF 不匹配（全部五个端点）
    所有权：自访 / 跨用户 metadata / 跨用户消息正文 / 跨用户追加 / 跨用户归档 /
          随机 ID / 无归属记录的历史会话 / ID 篡改 / 越权与不存在同构
    项目作用域（D1 加固）：配置项目放行 / 其他已注册项目拒绝 / 未注册拒绝 /
           伪造 project_id / 配置不可解析 fail-closed / vietnam-wms 兼容
    故障：绑定失败不留无主会话 / 归属查询异常不放行 / DB 异常受控 /
          失败路径零 AI 调用

隔离策略：

    * 离线部分：Fake Service + Fake 归属 + Fake 注册表 + Fake 会话
      （DB=0 · Network=0 · LLM=0）；
    * DB 部分：真实链路（RUN_DB_TESTS 门控），结束定向清理。

安全语义（与生产一致，**不**削弱）：CSRF 由 ConversationTestClient 按 M1
双提交规则自动附加；越权与不存在**同构 404**；故障 fail-closed。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import conversations as conversations_module
from backend.app.config import settings
from backend.app.db.conversation_repository import ConversationRepositoryError
from backend.app.db.session import get_engine
from backend.app.main import app
from backend.app.projects.context import get_default_project_context
from backend.app.projects.models import ProjectContext
from backend.app.projects.registry import (
    DEFAULT_BUSINESS_SCHEMA,
    ProjectRegistration,
    ProjectRegistryError,
    get_default_project_registry,
)
from backend.app.services.conversation_service import (
    ConversationTurnView,
    ConversationView,
)
from backend.app.services.resource_ownership_service import (
    RESOURCE_TYPE_CONVERSATION,
    ResourceOwnershipError,
)
from backend.app.services.user_service import get_user_service
from tests.conversation_auth_support import (
    USER_PASSWORDS,
    ConversationTestClient,
    FakeResourceOwnershipService,
    login_over_http,
    patch_conversation_api,
)
from tests.test_conversation_api import FakeConversationService

__all__ = [
    "test_unauthenticated_access_denied_on_all_five_endpoints",
    "test_invalid_session_denied",
    "test_expired_session_denied",
    "test_revoked_session_denied",
    "test_logout_replay_denied",
    "test_append_and_archive_require_csrf_header",
    "test_create_requires_csrf_header",
    "test_append_and_archive_reject_csrf_mismatch",
    "test_owner_can_use_all_five_endpoints",
    "test_cross_user_metadata_denied",
    "test_cross_user_messages_denied",
    "test_cross_user_append_denied_without_side_effects",
    "test_cross_user_archive_denied_without_side_effects",
    "test_random_conversation_id_denied",
    "test_legacy_conversation_without_owner_denied",
    "test_tampered_conversation_id_denied",
    "test_denied_and_missing_responses_are_identical",
    "test_configured_project_allowed",
    "test_other_registered_project_rejected",
    "test_other_registered_project_rejected_when_configured_is_other",
    "test_unregistered_project_rejected",
    "test_forged_project_id_rejected",
    "test_project_scope_unresolvable_fails_closed",
    "test_allowed_project_accessor_blank_or_unavailable_is_none",
    "test_allowed_project_unresolvable_in_registry_fails_closed",
    "test_project_registry_failure_fails_closed",
    "test_vietnam_wms_flow_compatible",
    "test_bind_failure_leaves_no_orphan_conversation",
    "test_ownership_service_failure_fails_closed",
    "test_repository_error_is_controlled",
    "test_failed_paths_do_not_call_ai",
]

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
needs_db = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable conversation ownership PostgreSQL tests",
)

_PROJECT = "vietnam-wms"
_BASE = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def _seeded_service() -> FakeConversationService:
    """预置一个属于 alice 的会话（含一条消息）。"""
    conversation_id = str(uuid.uuid4())
    service = FakeConversationService(
        conversations={
            conversation_id: ConversationView(
                conversation_id=conversation_id,
                project_id=_PROJECT,
                created_at=_BASE,
                updated_at=_BASE,
                status="ACTIVE",
            )
        },
        turns={
            conversation_id: (
                ConversationTurnView(
                    turn_id=1,
                    conversation_id=conversation_id,
                    role="USER",
                    content="你好",
                    assistant_request_id=None,
                    created_at=_BASE,
                ),
            )
        },
    )
    service.seed_conversation_id = conversation_id  # type: ignore[attr-defined]
    return service


def _second_user_client(client: ConversationTestClient, username: str):
    """同一 Fake 会话服务下的第二个用户客户端（跨用户用例）。"""
    return ConversationTestClient(
        app, session_service=client._session_service, username=username
    )


# ============================================================
# 认证
# ============================================================


def test_unauthenticated_access_denied_on_all_five_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _seeded_service()
    patch_conversation_api(monkeypatch, service)
    plain = TestClient(app, base_url="https://testserver")
    conversation_id = service.seed_conversation_id  # type: ignore[attr-defined]

    assert plain.post("/api/conversations", json={"project_id": _PROJECT}).status_code == 401
    assert plain.get(f"/api/conversations/{conversation_id}").status_code == 401
    assert plain.get(f"/api/conversations/{conversation_id}/messages").status_code == 401
    assert plain.post(f"/api/conversations/{conversation_id}/messages", json={"content": "hi"}).status_code == 401
    assert plain.post(f"/api/conversations/{conversation_id}/archive").status_code == 401
    # 未登录不产生任何业务调用
    assert service.calls == []


def test_invalid_session_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _seeded_service()
    patch_conversation_api(monkeypatch, service)
    plain = TestClient(app, base_url="https://testserver")
    plain.cookies.set(settings.auth.session_cookie_name, "garbage-token")
    assert plain.get(f"/api/conversations/{service.seed_conversation_id}").status_code == 401  # type: ignore[attr-defined]


def test_expired_session_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _seeded_service()
    client, _own = patch_conversation_api(monkeypatch, service)
    client._session_service.expire(client.session_token)
    assert client.get(f"/api/conversations/{service.seed_conversation_id}").status_code == 401  # type: ignore[attr-defined]


def test_revoked_session_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _seeded_service()
    client, _own = patch_conversation_api(monkeypatch, service)
    client._session_service.logout(client.session_token)
    assert client.get(f"/api/conversations/{service.seed_conversation_id}").status_code == 401  # type: ignore[attr-defined]


def test_logout_replay_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """登出（服务端吊销）后重放旧 Cookie（含旧 CSRF 头）⇒ 401。"""
    service = _seeded_service()
    client, _own = patch_conversation_api(monkeypatch, service)
    conversation_id = service.seed_conversation_id  # type: ignore[attr-defined]
    assert client.post(f"/api/conversations/{conversation_id}/archive").status_code == 200
    csrf = client.cookies.get(settings.auth.csrf_cookie_name) or ""
    # 登出：服务端吊销会话（旧 Cookie 值不再可用）
    assert client._session_service.logout(client.session_token) is True
    # 手动重放旧值（模拟旧标签页 / 攻击者）
    replay = TestClient(app, base_url="https://testserver")
    replay.cookies.set(settings.auth.session_cookie_name, client.session_token)
    replay.cookies.set(settings.auth.csrf_cookie_name, csrf)
    response = replay.post(
        f"/api/conversations/{conversation_id}/archive",
        headers={settings.auth.csrf_header_name: csrf},
    )
    assert response.status_code == 401


def test_append_and_archive_require_csrf_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺少 CSRF 请求头（仅 Cookie）⇒ 403；无任何写副作用。

    ``ConversationTestClient`` 会自动附加 CSRF 头；本用例显式构造**不**附加
    头的客户端（带有效会话 Cookie + CSRF Cookie，模拟前端的双提交通道）。
    """
    service = _seeded_service()
    client, _own = patch_conversation_api(monkeypatch, service)
    conversation_id = service.seed_conversation_id  # type: ignore[attr-defined]
    plain = TestClient(app, base_url="https://testserver")
    plain.cookies.set(settings.auth.session_cookie_name, client.session_token)
    plain.cookies.set(
        settings.auth.csrf_cookie_name,
        client.cookies.get(settings.auth.csrf_cookie_name) or "",
    )
    for path, body in (
        (f"/api/conversations/{conversation_id}/messages", {"content": "hi"}),
        (f"/api/conversations/{conversation_id}/archive", None),
    ):
        response = plain.post(path, json=body)
        assert response.status_code == 403, path
        assert response.json()["detail"] == "CSRF 校验失败"


def test_create_requires_csrf_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """创建（不安全方法）缺少 CSRF 头 ⇒ 403，且不产生任何会话 / 归属写入。"""
    service = FakeConversationService()
    client, own = patch_conversation_api(monkeypatch, service)
    plain = TestClient(app, base_url="https://testserver")
    plain.cookies.set(settings.auth.session_cookie_name, client.session_token)
    plain.cookies.set(
        settings.auth.csrf_cookie_name,
        client.cookies.get(settings.auth.csrf_cookie_name) or "",
    )
    response = plain.post("/api/conversations", json={"project_id": _PROJECT})
    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF 校验失败"
    assert service._conversations == {}  # 无会话创建
    assert own._rows == {}  # 无归属写入


def test_append_and_archive_reject_csrf_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _seeded_service()
    client, _own = patch_conversation_api(monkeypatch, service)
    conversation_id = service.seed_conversation_id  # type: ignore[attr-defined]
    forged = "a" * 64
    for path, body in (
        (f"/api/conversations/{conversation_id}/messages", {"content": "hi"}),
        (f"/api/conversations/{conversation_id}/archive", None),
    ):
        response = client.post(
            path, json=body, headers={settings.auth.csrf_header_name: forged}
        )
        assert response.status_code == 403, path


# ============================================================
# 所有权
# ============================================================


def test_owner_can_use_all_five_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _seeded_service()
    client, own = patch_conversation_api(monkeypatch, service)
    conversation_id = service.seed_conversation_id  # type: ignore[attr-defined]

    created = client.post("/api/conversations", json={"project_id": _PROJECT})
    assert created.status_code == 200
    new_id = created.json()["conversation_id"]
    # 创建即绑定所有权
    assert (
        own.get_owner(
            resource_type=RESOURCE_TYPE_CONVERSATION, resource_id=new_id
        )
        is not None
    )

    assert client.get(f"/api/conversations/{conversation_id}").status_code == 200
    assert client.get(f"/api/conversations/{conversation_id}/messages").status_code == 200
    assert client.post(f"/api/conversations/{conversation_id}/messages", json={"content": "hi"}).status_code == 200
    assert client.post(f"/api/conversations/{conversation_id}/archive").status_code == 200


def test_cross_user_metadata_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _seeded_service()
    alice, _own = patch_conversation_api(monkeypatch, service, username="alice")
    conversation_id = service.seed_conversation_id  # type: ignore[attr-defined]
    bob = _second_user_client(alice, "bob")
    assert bob.get(f"/api/conversations/{conversation_id}").status_code == 404
    assert alice.get(f"/api/conversations/{conversation_id}").status_code == 200


def test_cross_user_messages_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _seeded_service()
    alice, _own = patch_conversation_api(monkeypatch, service, username="alice")
    conversation_id = service.seed_conversation_id  # type: ignore[attr-defined]
    bob = _second_user_client(alice, "bob")
    response = bob.get(f"/api/conversations/{conversation_id}/messages")
    assert response.status_code == 404
    # 消息正文不泄露
    assert "你好" not in response.text


def test_cross_user_append_denied_without_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _seeded_service()
    alice, _own = patch_conversation_api(monkeypatch, service, username="alice")
    conversation_id = service.seed_conversation_id  # type: ignore[attr-defined]
    bob = _second_user_client(alice, "bob")
    calls_before = len(service.calls)
    response = bob.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "hi"}
    )
    assert response.status_code == 404
    # 越权不触发任何 Service 调用（零 AI / 零写）
    assert len(service.calls) == calls_before


def test_cross_user_archive_denied_without_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _seeded_service()
    alice, _own = patch_conversation_api(monkeypatch, service, username="alice")
    conversation_id = service.seed_conversation_id  # type: ignore[attr-defined]
    bob = _second_user_client(alice, "bob")
    assert bob.post(f"/api/conversations/{conversation_id}/archive").status_code == 404
    view = service._conversations[conversation_id]
    assert view.status == "ACTIVE"  # 无写副作用


def test_random_conversation_id_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _seeded_service()
    client, _own = patch_conversation_api(monkeypatch, service)
    assert client.get(f"/api/conversations/{uuid.uuid4()}").status_code == 404


def test_legacy_conversation_without_owner_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """历史会话（无 resource_owner 记录）⇒ 拒绝；**不**自动认领。"""
    service = _seeded_service()
    client, own = patch_conversation_api(monkeypatch, service)
    conversation_id = service.seed_conversation_id  # type: ignore[attr-defined]
    # 抹除归属记录（模拟 M2.2 之前创建的历史会话）
    own._rows.clear()
    assert client.get(f"/api/conversations/{conversation_id}").status_code == 404
    # 访问后仍无归属记录（未发生自动认领）
    assert own._rows == {}
    # 创建新会话不受影响
    assert client.post("/api/conversations", json={"project_id": _PROJECT}).status_code == 200


def test_tampered_conversation_id_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _seeded_service()
    client, _own = patch_conversation_api(monkeypatch, service)
    original = service.seed_conversation_id  # type: ignore[attr-defined]
    tampered = original[:-1] + ("0" if original[-1] != "0" else "1")
    assert client.get(f"/api/conversations/{tampered}").status_code == 404


def test_denied_and_missing_responses_are_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """越权与不存在：同 status + 同 body（不泄露存在性）。"""
    service = _seeded_service()
    alice, _own = patch_conversation_api(monkeypatch, service, username="alice")
    conversation_id = service.seed_conversation_id  # type: ignore[attr-defined]
    bob = _second_user_client(alice, "bob")
    denied = bob.get(f"/api/conversations/{conversation_id}")
    missing = bob.get(f"/api/conversations/{uuid.uuid4()}")
    assert denied.status_code == missing.status_code == 404
    assert denied.json()["detail"] == missing.json()["detail"]


# ============================================================
# 项目作用域（D1 加固：配置项目 = 唯一允许；已注册 ≠ 已授权）
# ============================================================


def test_configured_project_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置中的允许项目 ⇒ 创建成功，归属记录使用服务端已验证项目值。"""
    service = FakeConversationService()
    client, own = patch_conversation_api(
        monkeypatch,
        service,
        known_projects=(_PROJECT,),
        allowed_project_id=_PROJECT,
    )
    response = client.post("/api/conversations", json={"project_id": _PROJECT})
    assert response.status_code == 200, response.text
    assert response.json()["project_id"] == _PROJECT
    owner = own.get_owner(
        resource_type=RESOURCE_TYPE_CONVERSATION,
        resource_id=response.json()["conversation_id"],
    )
    assert owner is not None and owner.project_id == _PROJECT


def test_other_registered_project_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """其他项目**即使已注册**也不得创建（D1 加固；fail-closed + 零副作用）。"""
    service = FakeConversationService()
    client, own = patch_conversation_api(
        monkeypatch,
        service,
        known_projects=(_PROJECT, "project-a"),  # project-a 已注册
        allowed_project_id=_PROJECT,
    )
    response = client.post("/api/conversations", json={"project_id": "project-a"})
    assert response.status_code == 422
    assert response.json()["detail"] == "项目未注册或不可用"  # 与未注册同构
    assert service._conversations == {}  # 无会话创建
    assert own._rows == {}  # 无归属写入


def test_other_registered_project_rejected_when_configured_is_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """允许项目随部署配置变化（非硬编码）：配置 project-a 时只放行 project-a。"""
    service = FakeConversationService()
    client, _own = patch_conversation_api(
        monkeypatch,
        service,
        known_projects=(_PROJECT, "project-a"),
        allowed_project_id="project-a",
    )
    assert (
        client.post("/api/conversations", json={"project_id": "project-a"}).status_code
        == 200
    )
    assert (
        client.post("/api/conversations", json={"project_id": _PROJECT}).status_code
        == 422
    )


def test_unregistered_project_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """未注册项目 ⇒ 422（同一固定文案；不泄露项目存在性）+ 零副作用。"""
    service = FakeConversationService()
    client, own = patch_conversation_api(monkeypatch, service)
    response = client.post("/api/conversations", json={"project_id": "not-registered"})
    assert response.status_code == 422
    assert response.json()["detail"] == "项目未注册或不可用"
    assert own._rows == {}  # 无归属写入
    assert service._conversations == {}  # 无会话创建


def test_forged_project_id_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    service = FakeConversationService()
    client, own = patch_conversation_api(monkeypatch, service)
    # 尾随空格的项目 ID 与服务端配置值不精确一致
    response = client.post("/api/conversations", json={"project_id": f"{_PROJECT} "})
    assert response.status_code == 422
    assert service._conversations == {}
    assert own._rows == {}


def test_project_scope_unresolvable_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置无法解析 ⇒ 503（**绝不**回退默认项目 / 绝不匿名放行）+ 零副作用。"""
    service = FakeConversationService()
    client, own = patch_conversation_api(
        monkeypatch, service, allowed_project_id=None
    )
    response = client.post("/api/conversations", json={"project_id": _PROJECT})
    assert response.status_code == 503
    assert response.json()["detail"] == "项目校验服务不可用"
    assert service._conversations == {}
    assert own._rows == {}


def test_allowed_project_accessor_blank_or_unavailable_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """访问器自身 fail-closed：空白配置 / 取值异常 ⇒ None（不做默认回退）。"""
    blank = type("_Blank", (), {"project_id": "   "})()

    monkeypatch.setattr(
        conversations_module, "get_default_project_context", lambda: blank
    )
    assert conversations_module.get_conversation_allowed_project_id() is None

    def _boom() -> Any:
        raise RuntimeError("config broken")

    monkeypatch.setattr(conversations_module, "get_default_project_context", _boom)
    assert conversations_module.get_conversation_allowed_project_id() is None


def test_allowed_project_unresolvable_in_registry_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置项目在注册表中不可解析（部署配置错误）⇒ 503（非客户端错误）+ 零副作用。"""
    service = FakeConversationService()
    client, own = patch_conversation_api(
        monkeypatch, service, known_projects=(), allowed_project_id=_PROJECT
    )
    response = client.post("/api/conversations", json={"project_id": _PROJECT})
    assert response.status_code == 503
    assert response.json()["detail"] == "项目校验服务不可用"
    assert service._conversations == {}
    assert own._rows == {}


def test_project_registry_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """注册表故障 ⇒ 503（fail-closed；**绝不**因注册表异常放行）+ 零副作用。"""
    service = FakeConversationService()
    client, own = patch_conversation_api(
        monkeypatch, service, allowed_project_id=_PROJECT
    )

    class _BrokenRegistry:
        def get(self, project_id: str) -> Any:
            raise ProjectRegistryError("registry down")

    monkeypatch.setattr(
        conversations_module,
        "get_conversation_project_registry",
        lambda: _BrokenRegistry(),
    )
    response = client.post("/api/conversations", json={"project_id": _PROJECT})
    assert response.status_code == 503
    assert response.json()["detail"] == "项目校验服务不可用"
    assert service._conversations == {}
    assert own._rows == {}


def test_vietnam_wms_flow_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    service = FakeConversationService()
    client, _own = patch_conversation_api(monkeypatch, service)
    created = client.post("/api/conversations", json={"project_id": _PROJECT})
    assert created.status_code == 200
    assert created.json()["project_id"] == _PROJECT
    conversation_id = created.json()["conversation_id"]
    assert client.get(f"/api/conversations/{conversation_id}").json()["project_id"] == _PROJECT


# ============================================================
# 故障处理（fail-closed）
# ============================================================


def test_bind_failure_leaves_no_orphan_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """归属绑定失败 ⇒ 创建整体失败（原子性；真实库见 DB 用例）。"""
    service = FakeConversationService()
    client, own = patch_conversation_api(monkeypatch, service)
    own.fail_with(ResourceOwnershipError("bind down"))
    response = client.post("/api/conversations", json={"project_id": _PROJECT})
    assert response.status_code == 500
    assert response.json()["detail"] == "会话服务不可用"
    assert own._rows == {}  # 无归属写入


def test_ownership_service_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _seeded_service()
    client, own = patch_conversation_api(monkeypatch, service)
    conversation_id = service.seed_conversation_id  # type: ignore[attr-defined]
    own.fail_with(ResourceOwnershipError("db down"))
    response = client.get(f"/api/conversations/{conversation_id}")
    assert response.status_code == 503
    assert response.json()["detail"] == "所有权校验服务不可用"


def test_repository_error_is_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _seeded_service()
    client, _own = patch_conversation_api(monkeypatch, service)
    conversation_id = service.seed_conversation_id  # type: ignore[attr-defined]
    service.raise_on("get_conversation", ConversationRepositoryError("db down"))
    response = client.get(f"/api/conversations/{conversation_id}")
    assert response.status_code == 500
    assert response.json()["detail"] == "会话服务不可用"
    assert "db down" not in response.text  # 不暴露内部细节


def test_failed_paths_do_not_call_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有失败路径都不触发 AI 执行（零 LLM 调用 / 零写）。"""
    service = _seeded_service()
    alice, _own = patch_conversation_api(monkeypatch, service, username="alice")
    conversation_id = service.seed_conversation_id  # type: ignore[attr-defined]
    bob = _second_user_client(alice, "bob")

    # 越权追加
    assert bob.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "hi"}
    ).status_code == 404
    # 未登录追加
    plain = TestClient(app, base_url="https://testserver")
    assert plain.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "hi"}
    ).status_code == 401
    # 未授权 / 未注册项目创建（D1：拒绝先于任何业务调用）
    assert alice.post(
        "/api/conversations", json={"project_id": "nope"}
    ).status_code == 422
    # 无任何 AI 执行发生（Application 层零调用 —— 零 LLM / 零写）
    chat = conversations_module.get_chat_application_service()
    assert chat.calls == []


# ============================================================
# DB 真链（RUN_DB_TESTS）
# ============================================================


@pytest.fixture(scope="module")
def engine() -> Any:
    eng = get_engine()
    if eng is None:
        pytest.skip("DATABASE_URL 未配置，跳过 Conversation 归属 DB 测试")
    return eng


@pytest.fixture(scope="module", autouse=True)
def _created(engine: Any) -> Any:
    """记录本模块创建的 ID（模块结束定向清理；不用 TRUNCATE）。"""
    tracker: dict[str, set[str]] = {
        "conversation": set(),
        "user": set(),
    }
    yield tracker
    with engine.begin() as conn:
        for cid in tracker["conversation"]:
            conn.execute(
                text("DELETE FROM ai_ops.conversation_turn WHERE conversation_id = :c"),
                {"c": cid},
            )
            conn.execute(
                text(
                    "DELETE FROM ai_ops.resource_owner "
                    "WHERE resource_type = :t AND resource_id = :c"
                ),
                {"t": RESOURCE_TYPE_CONVERSATION, "c": cid},
            )
            conn.execute(
                text("DELETE FROM ai_ops.conversation WHERE conversation_id = :c"),
                {"c": cid},
            )
        for uid in tracker["user"]:
            conn.execute(
                text("DELETE FROM ai_ops.user_session WHERE user_id = :u"),
                {"u": uid},
            )
            conn.execute(
                text("DELETE FROM ai_ops.app_user WHERE user_id = :u"), {"u": uid}
            )


def _new_logged_in_client(username: str) -> tuple[TestClient, str]:
    """建用户 + HTTP 登录；返回 (client, user_id)。"""
    unique = f"m22-{username}-{uuid.uuid4().hex[:8]}"
    view = get_user_service().create_user(
        username=unique, password=USER_PASSWORDS[username]
    )
    client = TestClient(app, base_url="https://testserver")
    login_over_http(client, username=unique, password=USER_PASSWORDS[username])
    return client, view.user_id


def _csrf_headers(client: TestClient) -> dict[str, str]:
    return {
        settings.auth.csrf_header_name: client.cookies.get(
            settings.auth.csrf_cookie_name
        )
        or ""
    }


@needs_db
def test_db_create_binds_owner_and_is_atomic(
    monkeypatch: pytest.MonkeyPatch, engine: Any, _created: Any
) -> None:
    """DB：创建 ⇒ 归属行同事务存在；注入绑定故障 ⇒ 无孤儿会话。"""
    client, user_id = _new_logged_in_client("alice")
    _created["user"].add(user_id)

    response = client.post(
        "/api/conversations", json={"project_id": _PROJECT}, headers=_csrf_headers(client)
    )
    assert response.status_code == 200, response.text
    conversation_id = response.json()["conversation_id"]
    _created["conversation"].add(conversation_id)

    with engine.connect() as conn:
        owner = conn.execute(
            text(
                "SELECT user_id, project_id FROM ai_ops.resource_owner "
                "WHERE resource_type = :t AND resource_id = :c"
            ),
            {"t": RESOURCE_TYPE_CONVERSATION, "c": conversation_id},
        ).one()
    assert owner[0] == user_id and owner[1] == _PROJECT

    # 注入归属绑定故障（ResourceOwner 构造即抛）⇒ 事务回滚、无孤儿
    from backend.app.db import conversation_repository as repo_module

    class _Boom(Exception):
        pass

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise _Boom("bind failed")

    monkeypatch.setattr(repo_module, "ResourceOwner", _boom)
    failed = client.post(
        "/api/conversations", json={"project_id": _PROJECT}, headers=_csrf_headers(client)
    )
    assert failed.status_code == 500
    monkeypatch.undo()

    with engine.connect() as conn:
        orphans = conn.execute(
            text(
                "SELECT COUNT(*) FROM ai_ops.conversation c WHERE NOT EXISTS ("
                "SELECT 1 FROM ai_ops.resource_owner o "
                "WHERE o.resource_type = :t AND o.resource_id = c.conversation_id)"
            ),
            {"t": RESOURCE_TYPE_CONVERSATION},
        ).scalar_one()
    assert orphans == 0, "存在无主 Conversation（原子性被破坏）"


@needs_db
def test_db_cross_user_and_legacy_denied(
    engine: Any, _created: Any
) -> None:
    """DB：跨用户 404 同构且零副作用；无归属历史会话拒绝且不自动认领。"""
    alice, alice_id = _new_logged_in_client("alice")
    bob, bob_id = _new_logged_in_client("bob")
    _created["user"].update({alice_id, bob_id})

    created = alice.post(
        "/api/conversations", json={"project_id": _PROJECT}, headers=_csrf_headers(alice)
    )
    assert created.status_code == 200, created.text
    conversation_id = created.json()["conversation_id"]
    _created["conversation"].add(conversation_id)

    # B 越权：metadata / messages / append / archive 全部 404（与不存在同构）
    denied = bob.get(f"/api/conversations/{conversation_id}")
    missing = bob.get(f"/api/conversations/{uuid.uuid4()}")
    assert denied.status_code == missing.status_code == 404
    assert denied.json()["detail"] == missing.json()["detail"]

    assert bob.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "hi"},
        headers=_csrf_headers(bob),
    ).status_code == 404
    assert bob.post(
        f"/api/conversations/{conversation_id}/archive",
        headers=_csrf_headers(bob),
    ).status_code == 404

    with engine.connect() as conn:
        turns = conn.execute(
            text(
                "SELECT COUNT(*) FROM ai_ops.conversation_turn "
                "WHERE conversation_id = :c"
            ),
            {"c": conversation_id},
        ).scalar_one()
        status = conn.execute(
            text(
                "SELECT status FROM ai_ops.conversation WHERE conversation_id = :c"
            ),
            {"c": conversation_id},
        ).scalar_one()
    assert turns == 0, "越权追加产生了消息"
    assert status == "ACTIVE", "越权归档生效"

    # A 自访正常
    assert alice.get(f"/api/conversations/{conversation_id}").status_code == 200

    # 无归属记录的历史会话（绕过 API 直插）⇒ 拒绝
    legacy_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ai_ops.conversation (conversation_id, project_id, status) "
                "VALUES (:c, :p, 'ACTIVE')"
            ),
            {"c": legacy_id, "p": _PROJECT},
        )
    _created["conversation"].add(legacy_id)
    assert alice.get(f"/api/conversations/{legacy_id}").status_code == 404
    with engine.connect() as conn:
        owner_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM ai_ops.resource_owner "
                "WHERE resource_type = :t AND resource_id = :c"
            ),
            {"t": RESOURCE_TYPE_CONVERSATION, "c": legacy_id},
        ).scalar_one()
    assert owner_count == 0, "发生了自动认领"


@needs_db
def test_db_csrf_required_on_append(engine: Any, _created: Any) -> None:
    """DB：append 缺少 CSRF 头 ⇒ 403（真实会话 + 真实 CSRF 校验）。"""
    client, user_id = _new_logged_in_client("carol")
    _created["user"].add(user_id)
    created = client.post(
        "/api/conversations", json={"project_id": _PROJECT}, headers=_csrf_headers(client)
    )
    assert created.status_code == 200
    conversation_id = created.json()["conversation_id"]
    _created["conversation"].add(conversation_id)

    forged = "a" * 64
    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "hi"},
        headers={settings.auth.csrf_header_name: forged},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF 校验失败"

    with engine.connect() as conn:
        turns = conn.execute(
            text(
                "SELECT COUNT(*) FROM ai_ops.conversation_turn "
                "WHERE conversation_id = :c"
            ),
            {"c": conversation_id},
        ).scalar_one()
    assert turns == 0, "CSRF 失败仍产生了消息"


@needs_db
def test_db_unregistered_project_rejected(engine: Any, _created: Any) -> None:
    """DB：未注册项目 ⇒ 422（与"未授权"同一固定文案；fail-closed）。"""
    client, user_id = _new_logged_in_client("alice")
    _created["user"].add(user_id)
    response = client.post(
        "/api/conversations",
        json={"project_id": "definitely-not-registered"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "项目未注册或不可用"


@needs_db
def test_db_other_registered_project_rejected(
    engine: Any, _created: Any
) -> None:
    """DB：其他项目**即使真实注册**也不得创建（D1 加固）+ 零 DB 副作用。"""
    registry = get_default_project_registry()
    other = "m22-d1-other-project"
    default_context = get_default_project_context()
    registry.register(
        other,
        ProjectRegistration(
            context=ProjectContext(
                project_id=other,
                project_name=f"Project {other}",
                description=None,
                data_source=default_context.data_source,
            ),
            schema_name=DEFAULT_BUSINESS_SCHEMA,
        ),
    )

    client, user_id = _new_logged_in_client("alice")
    _created["user"].add(user_id)
    try:
        # 部署允许项目 = 真实配置项目（vietnam-wms）
        with engine.connect() as conn:
            before_conversations = conn.execute(
                text("SELECT COUNT(*) FROM ai_ops.conversation")
            ).scalar_one()
            before_owners = conn.execute(
                text("SELECT COUNT(*) FROM ai_ops.resource_owner")
            ).scalar_one()

        response = client.post(
            "/api/conversations",
            json={"project_id": other},
            headers=_csrf_headers(client),
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "项目未注册或不可用"

        with engine.connect() as conn:
            after_conversations = conn.execute(
                text("SELECT COUNT(*) FROM ai_ops.conversation")
            ).scalar_one()
            after_owners = conn.execute(
                text("SELECT COUNT(*) FROM ai_ops.resource_owner")
            ).scalar_one()
            rows = conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.conversation "
                    "WHERE project_id = :p"
                ),
                {"p": other},
            ).scalar_one()
        assert after_conversations == before_conversations, "被拒请求创建了会话"
        assert after_owners == before_owners, "被拒请求写入了归属行"
        assert rows == 0
    finally:
        registry.unregister(other)


@needs_db
def test_db_project_scope_unresolvable_fails_closed(
    engine: Any, _created: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DB：允许项目无法解析 ⇒ 503，且不产生 Conversation / resource_owner。"""
    monkeypatch.setattr(
        conversations_module, "get_conversation_allowed_project_id", lambda: None
    )
    client, user_id = _new_logged_in_client("alice")
    _created["user"].add(user_id)

    with engine.connect() as conn:
        before_conversations = conn.execute(
            text("SELECT COUNT(*) FROM ai_ops.conversation")
        ).scalar_one()
        before_owners = conn.execute(
            text("SELECT COUNT(*) FROM ai_ops.resource_owner")
        ).scalar_one()

    response = client.post(
        "/api/conversations",
        json={"project_id": _PROJECT},
        headers=_csrf_headers(client),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "项目校验服务不可用"

    with engine.connect() as conn:
        after_conversations = conn.execute(
            text("SELECT COUNT(*) FROM ai_ops.conversation")
        ).scalar_one()
        after_owners = conn.execute(
            text("SELECT COUNT(*) FROM ai_ops.resource_owner")
        ).scalar_one()
    assert after_conversations == before_conversations
    assert after_owners == before_owners
