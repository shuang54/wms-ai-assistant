"""Conversation Message API 契约测试（Phase 4.1 Step 12）。

隔离策略（与 ``tests/test_conversation_api.py`` 一致）：

    * FastAPI ``TestClient``（ASGI 内存调用；**不发起真实网络请求**）；
    * monkeypatch 注入 **Fake ChatApplicationService**（不改 production 代码）；
    * DB = 0 · Network = 0 · LLM = 0（所有请求都走 Fake）。

冻结来源：Phase 4.1 Step 12（Message API 最小接线）
        + Step 10（Chat / Application Layer Boundary Audit）
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import conversations as conversations_module
from backend.app.api.orchestrator_chat import ChatResponse
from backend.app.db.conversation_repository import ConversationRepositoryError
from backend.app.dto.conversation_api import (
    MESSAGE_CONTENT_MAX_LENGTH,
    ConversationMessageRequest,
    ConversationMessageResponse,
)
from backend.app.dto.assistant_outcome import AssistantOutcome
from backend.app.main import app
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorExecutionError,
    RouteType,
    TEXT_TO_SQL_REFUSAL_MESSAGE,
)
from backend.app.services.conversation_service import (
    ConversationArchivedError,
    ConversationNotFoundError,
)

_MESSAGES_PATH = "/api/conversations/{conversation_id}/messages"
_CONVERSATION_ID = "conv-1"

#: 客户端**不得**携带的字段（Step 12 §4）。
_FORBIDDEN_REQUEST_FIELDS: tuple[str, ...] = (
    "project_id",
    "assistant_request_id",
    "request_id",
    "route",
    "metadata",
    "context",
    "model",
    "provider",
)


def _result(
    *,
    route: RouteType = RouteType.TEXT_TO_SQL,
    content: str | None = "回答",
    data: Any = None,
    metadata: dict[str, Any] | None = None,
) -> AIOrchestrationResult:
    payload: dict[str, Any] = {
        "request_id": "req-0001",
        "outcome": AssistantOutcome.SUCCESS,
    }
    if metadata:
        payload.update(metadata)
    return AIOrchestrationResult(
        route=route, content=content, data=data, metadata=payload
    )


class FakeChatApplicationService:
    """ChatApplicationService 替身（仅测试；不改 Application Service）。"""

    def __init__(self, *, result: AIOrchestrationResult | None = None) -> None:
        self.result = result if result is not None else _result()
        self._errors: dict[str, Exception] = {}
        self.calls: list[dict[str, Any]] = []

    def raise_on(self, conversation_id: str, error: Exception) -> None:
        self._errors[conversation_id] = error

    async def execute_message(
        self,
        *,
        conversation_id: str,
        content: str,
        idempotency_key: str | None = None,
    ):
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "content": content,
                "idempotency_key": idempotency_key,
            }
        )
        error = self._errors.get(conversation_id)
        if error is not None:
            raise error
        return self.result


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeChatApplicationService:
    service = FakeChatApplicationService()
    monkeypatch.setattr(
        conversations_module,
        "_chat_application_service",
        service,  # type: ignore[arg-type]
    )
    return service


@pytest.fixture()
def client(fake: FakeChatApplicationService) -> TestClient:
    return TestClient(app)


def _post(client: TestClient, body: dict[str, Any], conversation_id: str = _CONVERSATION_ID):
    return client.post(
        f"/api/conversations/{conversation_id}/messages", json=body
    )


# ============================================================
# 1. Route / Schema
# ============================================================


class TestRouteAndSchema:
    def test_post_route_exists(self) -> None:
        operations = {m.upper() for m in app.openapi()["paths"][_MESSAGES_PATH]}
        assert "POST" in operations

    def test_existing_get_route_preserved(self) -> None:
        operations = app.openapi()["paths"][_MESSAGES_PATH]
        assert {m.upper() for m in operations} == {"GET", "POST"}

    def test_request_schema_only_content(self) -> None:
        assert list(ConversationMessageRequest.model_fields) == ["content"]
        for forbidden in _FORBIDDEN_REQUEST_FIELDS:
            assert forbidden not in ConversationMessageRequest.model_fields

    def test_response_envelope_matches_ai_chat(self) -> None:
        assert list(ConversationMessageResponse.model_fields) == [
            "route",
            "content",
            "data",
            "metadata",
        ]
        assert set(ConversationMessageResponse.model_fields) == set(
            ChatResponse.model_fields
        )

    def test_response_has_no_conversation_identifiers(self) -> None:
        for forbidden in ("conversation_id", "turn_id", "assistant_request_id"):
            assert forbidden not in ConversationMessageResponse.model_fields


# ============================================================
# 2. Success / Outcome → HTTP status
# ============================================================


class TestHttpStatusSemantics:
    def test_success_200(self, client: TestClient, fake: FakeChatApplicationService) -> None:
        fake.result = _result(content="查询完成", route=RouteType.TEXT_TO_SQL)

        response = _post(client, {"content": "查询库存"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "text_to_sql"
        assert payload["content"] == "查询完成"
        assert payload["data"] is None
        assert payload["metadata"]["request_id"] == "req-0001"
        assert payload["metadata"]["outcome"] == "SUCCESS"

    def test_empty_outcome_still_200(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        fake.result = _result(
            route=RouteType.RAG,
            content=None,
            metadata={"outcome": AssistantOutcome.EMPTY},
        )

        response = _post(client, {"content": "你好"})

        assert response.status_code == 200
        assert response.json()["content"] is None
        assert response.json()["metadata"]["outcome"] == "EMPTY"

    def test_refused_still_200_unchanged(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        fake.result = _result(
            route=RouteType.TEXT_TO_SQL,
            content=TEXT_TO_SQL_REFUSAL_MESSAGE,
            metadata={
                "refused": True,
                "outcome": AssistantOutcome.REFUSED,
            },
        )

        response = _post(client, {"content": "删掉所有订单"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "text_to_sql"
        assert payload["content"] == TEXT_TO_SQL_REFUSAL_MESSAGE
        assert payload["metadata"]["refused"] is True
        assert payload["metadata"]["outcome"] == "REFUSED"

    def test_tool_failure_still_200(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        fake.result = _result(
            route=RouteType.TOOL,
            content="工具执行失败",
            metadata={"tool_success": False, "outcome": AssistantOutcome.FAILED},
        )

        response = _post(client, {"content": "查库存"})

        assert response.status_code == 200
        assert response.json()["metadata"]["outcome"] == "FAILED"

    def test_api_calls_application_service_exactly_once(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        _post(client, {"content": "你好"})

        assert len(fake.calls) == 1
        # Phase 4.2 Step 6：未提供 Idempotency-Key → 透传 None（不生成键）
        assert fake.calls[0] == {
            "conversation_id": _CONVERSATION_ID,
            "content": "你好",
            "idempotency_key": None,
        }


# ============================================================
# 3. Error mapping（404 / 409 / 422 / 500）
# ============================================================


class TestErrorMapping:
    def test_not_found_404(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        fake.raise_on(
            _CONVERSATION_ID,
            ConversationNotFoundError(f"conversation 不存在: {_CONVERSATION_ID}"),
        )

        response = _post(client, {"content": "你好"})

        assert response.status_code == 404
        assert "detail" in response.json()

    def test_archived_409(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        fake.raise_on(
            _CONVERSATION_ID,
            ConversationArchivedError(f"conversation 已归档: {_CONVERSATION_ID}"),
        )

        response = _post(client, {"content": "你好"})

        assert response.status_code == 409

    def test_repository_error_500(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        fake.raise_on(
            _CONVERSATION_ID, ConversationRepositoryError("db down")
        )

        response = _post(client, {"content": "你好"})

        assert response.status_code == 500
        assert response.json()["detail"] == "会话服务不可用"

    def test_ai_failure_500_without_leak(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        fake.raise_on(
            _CONVERSATION_ID,
            AIOrchestratorExecutionError(
                "boom sk-secret SELECT * FROM users; stacktrace..."
            ),
        )

        response = _post(client, {"content": "你好"})

        assert response.status_code == 500
        assert response.json()["detail"] == "消息执行失败"
        body = response.text
        for leak in ("sk-secret", "SELECT", "stacktrace", "Traceback"):
            assert leak not in body

    def test_service_value_error_422(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        fake.raise_on(_CONVERSATION_ID, ValueError("content 非法"))

        response = _post(client, {"content": "你好"})

        assert response.status_code == 422


# ============================================================
# 4. Request validation（DTO 层拦截；AI = 0）
# ============================================================


class TestRequestValidation:
    def test_empty_content_422_without_service_call(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        response = _post(client, {"content": ""})

        assert response.status_code == 422
        assert fake.calls == []

    def test_blank_content_422_without_service_call(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        response = _post(client, {"content": "   "})

        assert response.status_code == 422
        assert fake.calls == []

    def test_missing_content_422(self, client: TestClient, fake: FakeChatApplicationService) -> None:
        response = _post(client, {})

        assert response.status_code == 422
        assert fake.calls == []

    def test_too_long_content_422(self, client: TestClient) -> None:
        response = _post(client, {"content": "x" * (MESSAGE_CONTENT_MAX_LENGTH + 1)})
        assert response.status_code == 422

    def test_max_length_content_accepted(self, client: TestClient) -> None:
        response = _post(client, {"content": "x" * MESSAGE_CONTENT_MAX_LENGTH})
        assert response.status_code == 200

    def test_non_string_content_422(self, client: TestClient) -> None:
        response = _post(client, {"content": 123})
        assert response.status_code == 422

    def test_conversation_id_too_long_422(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        response = _post(client, {"content": "你好"}, conversation_id="x" * 129)

        assert response.status_code == 422
        assert fake.calls == []


# ============================================================
# 5. 客户端不可覆盖 project / request id
# ============================================================


class TestClientCannotOverrideBinding:
    def test_extra_fields_ignored_and_binding_not_overridable(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        response = _post(
            client,
            {
                "content": "你好",
                "project_id": "evil-project",
                "assistant_request_id": "evil-request-id",
                "request_id": "evil",
                "route": "tool",
                "metadata": {"outcome": "SUCCESS"},
            },
        )

        assert response.status_code == 200
        # Application Service 只收到 conversation_id / content / idempotency_key
        # （无覆盖入口；幂等键只来自 Header，Body 字段被忽略）
        assert fake.calls == [
            {
                "conversation_id": _CONVERSATION_ID,
                "content": "你好",
                "idempotency_key": None,
            }
        ]

    def test_response_metadata_comes_from_ai_result(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        fake.result = _result(metadata={"request_id": "req-real"})

        payload = _post(client, {"content": "你好"}).json()

        assert payload["metadata"]["request_id"] == "req-real"


# ============================================================
# 6. data 安全投影（防泄露）
# ============================================================


class TestDataProjection:
    def test_primitive_mapping_is_passed_through(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        fake.result = _result(data={"used_chunks_count": 3, "refused": False})

        payload = _post(client, {"content": "你好"}).json()

        assert payload["data"] == {"used_chunks_count": 3, "refused": False}

    def test_rich_object_is_not_exposed(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        class _RichObject:
            chunk_content = "RAG chunk 正文"

        fake.result = _result(data=_RichObject())

        payload = _post(client, {"content": "你好"}).json()

        assert payload["data"] is None
        assert "RAG chunk 正文" not in str(payload)

    def test_mapping_with_rich_values_is_not_exposed(
        self, client: TestClient, fake: FakeChatApplicationService
    ) -> None:
        fake.result = _result(data={"chunk": object()})

        payload = _post(client, {"content": "你好"}).json()

        assert payload["data"] is None


__all__ = [
    "FakeChatApplicationService",
    "TestRouteAndSchema",
    "TestHttpStatusSemantics",
    "TestErrorMapping",
    "TestRequestValidation",
    "TestClientCannotOverrideBinding",
    "TestDataProjection",
]
