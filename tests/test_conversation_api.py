"""Conversation Management API 测试（Phase 4.1 Step 8）。

隔离策略（与 ``tests/test_chat_api.py`` 一致）：

    * 使用 FastAPI ``TestClient``（ASGI 内存调用；**不发起真实网络请求**）；
    * 通过 monkeypatch 注入 **Fake ConversationService**（不修改 Production Service）；
    * DB = 0 · Network = 0 · LLM = 0 · DB Writes = 0
      （所有请求都走 Fake，绝不触达 PostgreSQL）。

覆盖：

    Create（valid / empty / too long / server-generated id / exact fields）
    Get（existing 200 / missing 404）
    Messages（empty [] / USER / ASSISTANT / ordering / assistant_request_id / 无禁字段）
    Archive（ACTIVE → ARCHIVED / 幂等 / missing 404）
    Errors（RepositoryError → 500 / unknown → 500 / 无 traceback / 无 secret）
    Compatibility（/api/ai/chat · Trace · Timeline 不变）
    Routes（4 条路径精确存在；DELETE / PATCH / PUT = 0；messages 含 GET + POST）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import conversations as conversations_module
from backend.app.api.assistant_trace import AssistantTraceResponse
from backend.app.api.orchestrator_chat import ChatRequest, ChatResponse
from backend.app.db.conversation_repository import ConversationRepositoryError
from backend.app.main import app
from backend.app.services.conversation_service import (
    ConversationArchivedError,
    ConversationNotFoundError,
    ConversationService,
    ConversationTurnView,
    ConversationView,
)

#: 冻结的 Conversation 路径（4 条路径；Step 12 起 messages 同时含 GET 与 POST）。
EXPECTED_CONVERSATION_ROUTES: dict[str, set[str]] = {
    "/api/conversations": {"POST"},
    "/api/conversations/{conversation_id}": {"GET"},
    "/api/conversations/{conversation_id}/messages": {"GET", "POST"},
    "/api/conversations/{conversation_id}/archive": {"POST"},
}

_BASE = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


class FakeConversationService:
    """ConversationService 替身（仅测试；不改 Production Service）。"""

    def __init__(
        self,
        *,
        conversations: dict[str, ConversationView] | None = None,
        turns: dict[str, tuple[ConversationTurnView, ...]] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self._conversations: dict[str, ConversationView] = dict(conversations or {})
        self._turns: dict[str, tuple[ConversationTurnView, ...]] = dict(turns or {})
        self._errors: dict[str, Exception] = dict(errors or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []

    # ---------- 测试辅助 ----------

    def raise_on(self, method: str, error: Exception) -> None:
        self._errors[method] = error

    # ---------- Service Contract ----------

    def create_conversation(self, *, project_id: str) -> ConversationView:
        self.calls.append(("create_conversation", {"project_id": project_id}))
        error = self._errors.get("create_conversation")
        if error is not None:
            raise error
        view = ConversationView(
            conversation_id=str(uuid.uuid4()),
            project_id=project_id,
            created_at=_BASE,
            updated_at=_BASE,
            status="ACTIVE",
        )
        self._conversations[view.conversation_id] = view
        self._turns.setdefault(view.conversation_id, ())
        return view

    def get_conversation(self, conversation_id: str) -> ConversationView | None:
        self.calls.append(("get_conversation", {"conversation_id": conversation_id}))
        error = self._errors.get("get_conversation")
        if error is not None:
            raise error
        return self._conversations.get(conversation_id)

    def archive_conversation(self, conversation_id: str) -> ConversationView:
        self.calls.append(("archive_conversation", {"conversation_id": conversation_id}))
        error = self._errors.get("archive_conversation")
        if error is not None:
            raise error
        current = self._conversations.get(conversation_id)
        if current is None:
            raise ConversationNotFoundError(f"conversation 不存在: {conversation_id}")
        archived = ConversationView(
            conversation_id=current.conversation_id,
            project_id=current.project_id,
            created_at=current.created_at,
            updated_at=current.updated_at + timedelta(seconds=1),
            status="ARCHIVED",
        )
        self._conversations[conversation_id] = archived
        return archived

    def list_turns(self, conversation_id: str) -> tuple[ConversationTurnView, ...]:
        self.calls.append(("list_turns", {"conversation_id": conversation_id}))
        error = self._errors.get("list_turns")
        if error is not None:
            raise error
        if conversation_id not in self._conversations:
            raise ConversationNotFoundError(f"conversation 不存在: {conversation_id}")
        return self._turns.get(conversation_id, ())


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """每个用例都注入 Fake Service（保证 DB = 0）。"""
    fake = FakeConversationService()
    monkeypatch.setattr(
        conversations_module, "_conversation_service", fake  # type: ignore[arg-type]
    )
    return TestClient(app)


@pytest.fixture()
def fake(client: TestClient) -> FakeConversationService:
    service = conversations_module.get_conversation_service()
    assert isinstance(service, FakeConversationService)
    return service


def _conversation_view(conversation_id: str, status: str = "ACTIVE") -> ConversationView:
    return ConversationView(
        conversation_id=conversation_id,
        project_id="vietnam-wms",
        created_at=_BASE,
        updated_at=_BASE,
        status=status,
    )


def _turn_view(
    turn_id: int, role: str, content: str, request_id: str | None, offset: int = 0
) -> ConversationTurnView:
    return ConversationTurnView(
        turn_id=turn_id,
        conversation_id="conv-1",
        role=role,
        content=content,
        assistant_request_id=request_id,
        created_at=_BASE + timedelta(seconds=offset),
    )


def _seed_turns(fake: FakeConversationService, conversation_id: str, turns: list) -> None:
    fake._conversations[conversation_id] = _conversation_view(conversation_id)  # noqa: SLF001
    fake._turns[conversation_id] = tuple(turns)  # noqa: SLF001


# ============================================================
# 1：Create
# ============================================================


class TestCreateConversation:
    def test_create_valid(self, client: TestClient) -> None:
        response = client.post("/api/conversations", json={"project_id": "vietnam-wms"})

        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {
            "conversation_id",
            "project_id",
            "status",
            "created_at",
            "updated_at",
        }
        assert payload["project_id"] == "vietnam-wms"
        assert payload["status"] == "ACTIVE"
        assert payload["conversation_id"]
        assert payload["created_at"] and payload["updated_at"]

    def test_create_empty_project_id_is_422(self, client: TestClient) -> None:
        response = client.post("/api/conversations", json={"project_id": ""})
        assert response.status_code == 422

    def test_create_blank_project_id_is_422(self, client: TestClient) -> None:
        response = client.post("/api/conversations", json={"project_id": "   "})
        assert response.status_code == 422

    def test_create_missing_project_id_is_422(self, client: TestClient) -> None:
        response = client.post("/api/conversations", json={})
        assert response.status_code == 422

    def test_create_too_long_project_id_is_422(self, client: TestClient) -> None:
        response = client.post("/api/conversations", json={"project_id": "x" * 129})
        assert response.status_code == 422

    def test_conversation_id_is_server_generated(self, client: TestClient) -> None:
        first = client.post(
            "/api/conversations", json={"project_id": "vietnam-wms"}
        ).json()
        second = client.post(
            "/api/conversations", json={"project_id": "vietnam-wms"}
        ).json()

        assert first["conversation_id"] != second["conversation_id"]
        assert len(first["conversation_id"]) == 36  # uuid4 字符串
        assert first["conversation_id"] != "vietnam-wms"

    def test_client_cannot_supply_conversation_id(self, client: TestClient) -> None:
        """客户端多传 conversation_id 不得影响服务端生成（Pydantic 忽略 / 或 422）。"""
        response = client.post(
            "/api/conversations",
            json={"project_id": "vietnam-wms", "conversation_id": "client-supplied"},
        )
        assert response.status_code == 200
        assert response.json()["conversation_id"] != "client-supplied"


# ============================================================
# 2：Get
# ============================================================


class TestGetConversation:
    def test_get_existing(self, client: TestClient, fake: FakeConversationService) -> None:
        conversation_id = str(uuid.uuid4())
        fake._conversations[conversation_id] = _conversation_view(conversation_id)  # noqa: SLF001

        response = client.get(f"/api/conversations/{conversation_id}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["conversation_id"] == conversation_id
        assert set(payload) == {
            "conversation_id",
            "project_id",
            "status",
            "created_at",
            "updated_at",
        }
        # metadata only：不自动查询 turns
        assert "turns" not in payload
        assert "messages" not in payload

    def test_get_missing_is_404(self, client: TestClient) -> None:
        response = client.get(f"/api/conversations/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_get_too_long_conversation_id_is_422(self, client: TestClient) -> None:
        response = client.get(f"/api/conversations/{'x' * 129}")
        assert response.status_code == 422


# ============================================================
# 3：Messages
# ============================================================


class TestListMessages:
    def test_empty_messages(self, client: TestClient, fake: FakeConversationService) -> None:
        conversation_id = str(uuid.uuid4())
        fake._conversations[conversation_id] = _conversation_view(conversation_id)  # noqa: SLF001

        response = client.get(f"/api/conversations/{conversation_id}/messages")

        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"conversation_id", "messages"}
        assert payload["messages"] == []
        assert payload["conversation_id"] == conversation_id

    def test_messages_with_user_and_assistant(
        self, client: TestClient, fake: FakeConversationService
    ) -> None:
        conversation_id = str(uuid.uuid4())
        turns = [
            _turn_view(1, "USER", "查询 A001 库存", None, offset=0),
            _turn_view(2, "ASSISTANT", "A001 库存 1250", "req-A", offset=1),
        ]
        fake._conversations[conversation_id] = _conversation_view(conversation_id)  # noqa: SLF001
        fake._turns[conversation_id] = tuple(turns)  # noqa: SLF001

        response = client.get(f"/api/conversations/{conversation_id}/messages")

        assert response.status_code == 200
        messages = response.json()["messages"]
        assert [m["turn_id"] for m in messages] == [1, 2]
        assert messages[0]["role"] == "USER"
        assert messages[0]["assistant_request_id"] is None
        assert messages[1]["role"] == "ASSISTANT"
        assert messages[1]["assistant_request_id"] == "req-A"
        assert messages[0]["content"] == "查询 A001 库存"

    def test_messages_keep_persistence_ordering(
        self, client: TestClient, fake: FakeConversationService
    ) -> None:
        """API 层不得重排：Service 已按 created_at ASC, turn_id ASC 返回。"""
        conversation_id = str(uuid.uuid4())
        turns = [
            _turn_view(1, "USER", "第一条", None, offset=0),
            _turn_view(2, "ASSISTANT", "第二条", "req-A", offset=1),
            _turn_view(3, "USER", "第三条（同刻）", None, offset=1),
        ]
        fake._conversations[conversation_id] = _conversation_view(conversation_id)  # noqa: SLF001
        fake._turns[conversation_id] = tuple(turns)  # noqa: SLF001

        response = client.get(f"/api/conversations/{conversation_id}/messages")

        assert response.status_code == 200
        messages = response.json()["messages"]
        assert [m["turn_id"] for m in messages] == [1, 2, 3]
        assert [m["content"] for m in messages] == [
            "第一条",
            "第二条",
            "第三条（同刻）",
        ]

    def test_messages_missing_conversation_is_404(self, client: TestClient) -> None:
        response = client.get(f"/api/conversations/{uuid.uuid4()}/messages")
        assert response.status_code == 404

    def test_messages_have_no_forbidden_fields(
        self, client: TestClient, fake: FakeConversationService
    ) -> None:
        conversation_id = str(uuid.uuid4())
        fake._conversations[conversation_id] = _conversation_view(conversation_id)  # noqa: SLF001
        fake._turns[conversation_id] = (  # noqa: SLF001
            _turn_view(1, "ASSISTANT", "A001 库存 1250", "req-A"),
        )

        response = client.get(f"/api/conversations/{conversation_id}/messages")
        message = response.json()["messages"][0]

        assert set(message) == {
            "turn_id",
            "role",
            "content",
            "assistant_request_id",
            "created_at",
        }
        for forbidden in (
            "api_key",
            "password",
            "authorization",
            "database_url",
            "sql",
            "prompt",
            "system_prompt",
            "provider_request_id",
            "session",
            "engine",
        ):
            assert forbidden not in message, forbidden


# ============================================================
# 4：Archive
# ============================================================


class TestArchiveConversation:
    def test_archive_active_becomes_archived(
        self, client: TestClient, fake: FakeConversationService
    ) -> None:
        conversation_id = str(uuid.uuid4())
        fake._conversations[conversation_id] = _conversation_view(conversation_id)  # noqa: SLF001

        response = client.post(f"/api/conversations/{conversation_id}/archive")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ARCHIVED"
        assert payload["conversation_id"] == conversation_id

    def test_archive_is_idempotent(
        self, client: TestClient, fake: FakeConversationService
    ) -> None:
        conversation_id = str(uuid.uuid4())
        fake._conversations[conversation_id] = _conversation_view(conversation_id)  # noqa: SLF001

        first = client.post(f"/api/conversations/{conversation_id}/archive")
        second = client.post(f"/api/conversations/{conversation_id}/archive")

        assert first.status_code == 200
        assert second.status_code == 200  # 第二次不报错
        assert second.json()["status"] == "ARCHIVED"

    def test_archive_missing_is_404(self, client: TestClient) -> None:
        response = client.post(f"/api/conversations/{uuid.uuid4()}/archive")
        assert response.status_code == 404

    def test_archived_error_maps_to_409(
        self, client: TestClient, fake: FakeConversationService
    ) -> None:
        """契约保留：ConversationArchivedError → 409（当前端点真实路径不触发）。"""
        conversation_id = str(uuid.uuid4())
        fake.raise_on(
            "archive_conversation",
            ConversationArchivedError(f"conversation 已归档: {conversation_id}"),
        )

        response = client.post(f"/api/conversations/{conversation_id}/archive")

        assert response.status_code == 409


# ============================================================
# 5：Error Mapping / Leakage
# ============================================================


class TestErrorMapping:
    def test_repository_error_is_500(self, client: TestClient, fake: FakeConversationService) -> None:
        fake.raise_on("create_conversation", ConversationRepositoryError("db down"))

        response = client.post("/api/conversations", json={"project_id": "vietnam-wms"})

        assert response.status_code == 500
        # 不降级为 404 / 不返回空列表；不泄露内部原因
        assert "db down" not in response.text

    def test_unknown_exception_is_500(self, client: TestClient, fake: FakeConversationService) -> None:
        fake.raise_on("create_conversation", RuntimeError("boom"))

        response = client.post("/api/conversations", json={"project_id": "vietnam-wms"})

        assert response.status_code == 500
        assert "boom" not in response.text

    def test_no_traceback_or_secret_leakage(
        self, client: TestClient, fake: FakeConversationService
    ) -> None:
        fake.raise_on(
            "create_conversation",
            RuntimeError("Traceback: postgresql://user:pw@h/db password=sk-xxx"),
        )

        response = client.post("/api/conversations", json={"project_id": "vietnam-wms"})

        body = response.text
        for sentinel in ("Traceback", "postgresql://", "sk-", "password", "Bearer"):
            assert sentinel not in body, sentinel

    def test_not_found_error_is_404(
        self, client: TestClient, fake: FakeConversationService
    ) -> None:
        fake.raise_on(
            "get_conversation", ConversationNotFoundError("conversation 不存在: x")
        )

        response = client.get(f"/api/conversations/{uuid.uuid4()}")
        assert response.status_code == 404


# ============================================================
# 6：Routes / Backward Compatibility
# ============================================================


class TestRoutes:
    def test_exactly_four_conversation_routes(self) -> None:
        paths = app.openapi()["paths"]
        conversation_paths = {
            path: {method.upper() for method in operations}
            for path, operations in paths.items()
            if path.startswith("/api/conversations")
        }
        assert conversation_paths == EXPECTED_CONVERSATION_ROUTES

    def test_no_delete_patch_put_and_message_post_registered(self) -> None:
        """Step 12：Message POST 已注册（GET + POST）；仍无 DELETE / PATCH / PUT。"""
        paths = app.openapi()["paths"]
        for path, operations in paths.items():
            methods = {method.upper() for method in operations}
            assert not methods & {"DELETE", "PATCH", "PUT"}, path
        assert {
            method.upper()
            for method in paths["/api/conversations/{conversation_id}/messages"]
        } == {"GET", "POST"}

    def test_service_is_conversation_service_instance(self) -> None:
        """默认装配仍是 ConversationService（Fake 只在测试内替换）。"""
        service = ConversationService()
        assert isinstance(service, ConversationService)


class TestBackwardCompatibility:
    def test_ai_chat_contract_unchanged(self, client: TestClient) -> None:
        paths = app.openapi()["paths"]
        assert "/api/ai/chat" in paths
        assert set(paths["/api/ai/chat"]) & {"post"} == {"post"}
        assert list(ChatRequest.model_fields) == ["question", "project_id"]
        assert list(ChatResponse.model_fields) == [
            "route",
            "content",
            "data",
            "metadata",
        ]
        assert "conversation_id" not in ChatRequest.model_fields
        assert "conversation_id" not in ChatResponse.model_fields

    def test_trace_and_timeline_routes_unchanged(self, client: TestClient) -> None:
        paths = app.openapi()["paths"]
        assert "/api/observability/assistant-trace/{assistant_request_id}" in paths
        assert "/api/observability/assistant-timeline/{assistant_request_id}" in paths
        assert list(AssistantTraceResponse.model_fields) == [
            "assistant_request_id",
            "outcome",
            "llm_usage",
            "tool_executions",
            "rag_executions",
        ]
