"""Conversation HTTP API Contract（Phase 4.1 Step 7 —— Design / Freeze Only）。

固化来源：
    `docs/evaluation/Phase 4.1 Step 7 — Conversation API Contract Audit.md`

本文件性质：

    * **纯离线**：DB = 0 · Network = 0 · DeepSeek = 0 · DB Writes = 0
      （不注册路由、不启动 ASGI 请求、不连接 PostgreSQL、不调用 LLM）；
    * **Design Only**：本 Step **不实现** Conversation API —— 本文件用测试内
      Pydantic 设计模型（``_`` 前缀，非 production DTO）冻结
      4 个端点的 request / response 字段、HTTP 状态、错误映射与语义；
    * **复用现有 API 风格**（真实审计结论）：
      - Pydantic v2（`BaseModel` + `Field` + `field_validator`，项目要求 >=2.7）
      - `APIRouter(prefix 由 main.py 的 /api 提供)` + `response_model=` +
        `responses={...}` 显式状态码（既有 api 模块统一风格）
      - `HTTPException(status_code=..., detail=...)`（既有错误映射风格）
      - 只使用 GET / POST（项目现有 API 无 PUT / PATCH / DELETE）
    * 任何"注册 conversation 路由 / 扩大 DTO 字段 / 引入分页 / 暴露 Provider
      request_id 或敏感字段"的漂移都会使本文件失败 —— 有意的漂移报警。
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.app.api.assistant_timeline import router as timeline_router
from backend.app.api.assistant_trace import router as trace_router
from backend.app.api.orchestrator_chat import ChatRequest, ChatResponse
from backend.app.dto.assistant_outcome import AssistantOutcome
from backend.app.services.conversation_service import (
    ConversationArchivedError,
    ConversationNotFoundError,
    ConversationServiceError,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_api_contract.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 7 — Conversation API Contract Audit.md"
_API_DIR = "backend/app/api"
_SERVICE = "backend/app/services/conversation_service.py"
_MAIN = "backend/app/main.py"
_TRACE_API = "backend/app/api/assistant_trace.py"

# ============================================================
# 冻结常量（Design）
# ============================================================

#: 4 个 Conversation Management 端点（本 Step 冻结；**未注册**）。
PROPOSED_ENDPOINTS: tuple[str, ...] = (
    "POST /api/conversations",
    "GET /api/conversations/{conversation_id}",
    "GET /api/conversations/{conversation_id}/messages",
    "POST /api/conversations/{conversation_id}/archive",
)

#: conversation_id 路径参数边界（与既有 Trace API 一致）。
CONVERSATION_ID_MIN_LENGTH: int = 1
CONVERSATION_ID_MAX_LENGTH: int = 128

#: 状态码集合（冻结）。
HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_UNPROCESSABLE = 422
HTTP_INTERNAL = 500

#: 错误映射（Service → HTTP；冻结）。
ERROR_MAPPING: dict[str, int] = {
    "ConversationNotFoundError": HTTP_NOT_FOUND,
    "ConversationArchivedError": HTTP_CONFLICT,
    "ValueError": HTTP_UNPROCESSABLE,
    "ConversationRepositoryError": HTTP_INTERNAL,
}

#: ConversationResponse 字段（冻结；仅 metadata，不含 turns）。
CONVERSATION_RESPONSE_FIELDS: tuple[str, ...] = (
    "conversation_id",
    "project_id",
    "status",
    "created_at",
    "updated_at",
)

#: ConversationTurnResponse 字段（冻结）。
TURN_RESPONSE_FIELDS: tuple[str, ...] = (
    "turn_id",
    "role",
    "content",
    "assistant_request_id",
    "created_at",
)

#: 消息列表响应字段（冻结；无分页字段）。
MESSAGES_RESPONSE_FIELDS: tuple[str, ...] = ("conversation_id", "messages")

#: 明确不提供的字段 / 能力（Deferred）。
FORBIDDEN_REQUEST_FIELDS: tuple[str, ...] = (
    "conversation_id",
    "status",
    "created_at",
    "updated_at",
    "user_id",
    "tenant_id",
    "title",
    "name",
    "metadata",
    "context",
    "system_prompt",
)

FORBIDDEN_RESPONSE_FIELDS: tuple[str, ...] = (
    "api_key",
    "password",
    "authorization",
    "database_url",
    "sql",
    "prompt",
    "system_prompt",
    "messages_raw",
    "chunk_content",
    "embedding",
    "raw_response",
    "provider_request_id",
    "session",
    "engine",
    "orm",
    "traceback",
)

FORBIDDEN_PAGINATION_FIELDS: tuple[str, ...] = (
    "limit",
    "offset",
    "cursor",
    "page",
    "page_size",
    "before",
    "after",
    "total",
    "has_more",
)

_REQUIRED_DOC_SECTIONS: tuple[str, ...] = (
    "## 2. Proposed Endpoints",
    "## 3. Request / Response DTO",
    "## 4. HTTP Status",
    "## 5. Error Mapping",
    "## 6. Empty Semantics",
    "## 7. Archive Semantics",
    "## 8. Ordering",
    "## 9. Project Binding",
    "## 10. Security",
    "## 11. Authentication Limitation",
    "## 12. Backward Compatibility",
    "## 13. Pagination Deferred",
    "## 14. Message POST Deferred",
    "## 15. Implementation Deferred",
)

_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "sqlalchemy",
    "psycopg",
    "alembic",
    "redis",
    "celery",
    "backend.app.db",
    "httpx",
    "requests",
    "aiohttp",
    "openai",
    "socket",
    "urllib",
)


# ============================================================
# 设计 DTO（design-only；Pydantic v2；本文件私有）
# ============================================================


class _CreateConversationRequestDesign(BaseModel):
    """POST /api/conversations 请求体（只有 project_id）。"""

    project_id: str = Field(..., min_length=1, max_length=128)

    @field_validator("project_id")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("project_id 不能为空或纯空白")
        return value


class _ConversationResponseDesign(BaseModel):
    """会话 metadata 响应（**不含** turns / messages）。"""

    conversation_id: str
    project_id: str
    status: str
    created_at: datetime
    updated_at: datetime


class _ConversationTurnResponseDesign(BaseModel):
    """消息响应（暴露 assistant_request_id 作为 correlation；不暴露 Provider request_id）。"""

    turn_id: int
    role: str
    content: str
    assistant_request_id: str | None = None
    created_at: datetime


class _ConversationMessagesResponseDesign(BaseModel):
    """GET /api/conversations/{conversation_id}/messages 响应（无分页）。"""

    conversation_id: str
    messages: list[_ConversationTurnResponseDesign] = Field(default_factory=list)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _now(offset_seconds: int = 0) -> datetime:
    return datetime(2026, 10, 3, 12, 0, offset_seconds, tzinfo=timezone.utc)


def _payload(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


# ============================================================
# 1~5：DTO 字段契约
# ============================================================


class TestRequestResponseDto:
    def test_1_create_request_fields(self) -> None:
        assert list(_CreateConversationRequestDesign.model_fields) == ["project_id"]
        request = _CreateConversationRequestDesign(project_id="vietnam-wms")
        assert request.project_id == "vietnam-wms"
        for forbidden in FORBIDDEN_REQUEST_FIELDS:
            assert forbidden not in _CreateConversationRequestDesign.model_fields, (
                forbidden
            )
        with pytest.raises(ValidationError):
            _CreateConversationRequestDesign(project_id="")
        with pytest.raises(ValidationError):
            _CreateConversationRequestDesign(project_id="   ")
        with pytest.raises(ValidationError):
            _CreateConversationRequestDesign(project_id="x" * 129)

    def test_2_create_response_fields(self) -> None:
        response = _ConversationResponseDesign(
            conversation_id="c-1",
            project_id="vietnam-wms",
            status="ACTIVE",
            created_at=_now(),
            updated_at=_now(),
        )
        assert list(_ConversationResponseDesign.model_fields) == list(
            CONVERSATION_RESPONSE_FIELDS
        )
        payload = _payload(response)
        assert payload["status"] == "ACTIVE"
        assert payload["created_at"] == payload["updated_at"]

    def test_3_get_response_is_metadata_only(self) -> None:
        fields = set(_ConversationResponseDesign.model_fields)
        assert "turns" not in fields
        assert "messages" not in fields
        assert "content" not in fields

    def test_4_turn_response_fields(self) -> None:
        assert list(_ConversationTurnResponseDesign.model_fields) == list(
            TURN_RESPONSE_FIELDS
        )
        turn = _ConversationTurnResponseDesign(
            turn_id=1,
            role="USER",
            content="查询 A001 库存",
            assistant_request_id=None,
            created_at=_now(),
        )
        assert turn.assistant_request_id is None
        assistant = _ConversationTurnResponseDesign(
            turn_id=2,
            role="ASSISTANT",
            content="A001 库存 1250",
            assistant_request_id="req-A",
            created_at=_now(1),
        )
        assert assistant.assistant_request_id == "req-A"

    def test_5_messages_response_fields(self) -> None:
        assert list(_ConversationMessagesResponseDesign.model_fields) == list(
            MESSAGES_RESPONSE_FIELDS
        )
        empty = _ConversationMessagesResponseDesign(conversation_id="c-1")
        assert _payload(empty)["messages"] == []


# ============================================================
# 6~8：HTTP 状态与错误映射
# ============================================================


class TestStatusAndErrorMapping:
    def test_6_not_found_is_404(self) -> None:
        assert ERROR_MAPPING["ConversationNotFoundError"] == HTTP_NOT_FOUND
        assert issubclass(ConversationNotFoundError, ConversationServiceError)

    def test_7_archived_is_409(self) -> None:
        assert ERROR_MAPPING["ConversationArchivedError"] == HTTP_CONFLICT
        assert issubclass(ConversationArchivedError, ConversationServiceError)

    def test_8_validation_is_422_and_repository_is_500(self) -> None:
        assert ERROR_MAPPING["ValueError"] == HTTP_UNPROCESSABLE
        assert ERROR_MAPPING["ConversationRepositoryError"] == HTTP_INTERNAL
        # Repository 失败不得降级为 404 / 空列表
        assert ERROR_MAPPING["ConversationRepositoryError"] not in (
            HTTP_NOT_FOUND,
            HTTP_OK,
        )

    def test_8b_error_mapping_style_matches_existing_api(self) -> None:
        """既有 API 使用 HTTPException(status_code=..., detail=...)。"""
        source = _source(_TRACE_API)
        assert "HTTPException(" in source
        assert "status_code=status.HTTP_400_BAD_REQUEST" in source
        assert "status_code=status.HTTP_500_INTERNAL_SERVER_ERROR" in source

    def test_8c_status_code_set_declared(self) -> None:
        assert {
            HTTP_OK,
            HTTP_NOT_FOUND,
            HTTP_CONFLICT,
            HTTP_UNPROCESSABLE,
            HTTP_INTERNAL,
        } == {200, 404, 409, 422, 500}


# ============================================================
# 9~10：Empty / Ordering
# ============================================================


class TestEmptyAndOrdering:
    def test_9_empty_messages_is_empty_list_not_null(self) -> None:
        response = _ConversationMessagesResponseDesign(conversation_id="c-1")
        payload = _payload(response)
        assert payload["messages"] == []
        assert payload["messages"] is not None

    def test_10_ordering_is_created_at_then_turn_id(self) -> None:
        turns = [
            _ConversationTurnResponseDesign(
                turn_id=2,
                role="ASSISTANT",
                content="第二条",
                assistant_request_id="req-A",
                created_at=_now(1),
            ),
            _ConversationTurnResponseDesign(
                turn_id=1,
                role="USER",
                content="第一条",
                assistant_request_id=None,
                created_at=_now(0),
            ),
            _ConversationTurnResponseDesign(
                turn_id=3,
                role="USER",
                content="第三条（同刻）",
                assistant_request_id=None,
                created_at=_now(1),
            ),
        ]
        ordered = sorted(turns, key=lambda t: (t.created_at, t.turn_id))
        assert [t.turn_id for t in ordered] == [1, 2, 3]
        doc = _source(_AUDIT_DOC)
        assert "created_at ASC" in doc
        assert "turn_id ASC" in doc


# ============================================================
# 11~14：Project Binding / ID / correlation / security
# ============================================================


class TestBindingAndSecurity:
    def test_11_project_binding_no_switch_endpoint(self) -> None:
        for endpoint in PROPOSED_ENDPOINTS:
            assert "switch-project" not in endpoint
        assert not any(
            method in ("PATCH", "PUT") for method in (e.split()[0] for e in PROPOSED_ENDPOINTS)
        )
        doc = _source(_AUDIT_DOC)
        assert "不允许 PATCH project_id" in doc

    def test_12_conversation_id_is_server_generated(self) -> None:
        assert "conversation_id" not in _CreateConversationRequestDesign.model_fields
        doc = _source(_AUDIT_DOC)
        assert "服务端生成" in doc
        assert "uuid4" in doc.lower() or "UUID4" in doc

    def test_13_assistant_request_id_is_exposed_as_correlation(self) -> None:
        assert "assistant_request_id" in _ConversationTurnResponseDesign.model_fields
        assert "provider_request_id" not in _ConversationTurnResponseDesign.model_fields
        doc = _source(_AUDIT_DOC)
        assert "assistant_request_id" in doc
        assert "Provider request_id" in doc or "provider request_id" in doc

    def test_14_no_forbidden_response_fields(self) -> None:
        fields: set[str] = set()
        for model in (
            _ConversationResponseDesign,
            _ConversationTurnResponseDesign,
            _ConversationMessagesResponseDesign,
            _CreateConversationRequestDesign,
        ):
            fields |= set(model.model_fields)
        for forbidden in FORBIDDEN_RESPONSE_FIELDS:
            assert forbidden not in fields, forbidden


# ============================================================
# 15~17：SQLAlchemy / secrets / pagination
# ============================================================


class TestLeakageAndPagination:
    def test_15_no_sqlalchemy_or_orm_leakage(self) -> None:
        fields: set[str] = set()
        for model in (
            _ConversationResponseDesign,
            _ConversationTurnResponseDesign,
            _ConversationMessagesResponseDesign,
        ):
            fields |= set(model.model_fields)
        for forbidden in ("session", "engine", "orm", "repository", "connection"):
            assert forbidden not in fields, forbidden

    def test_16_no_secrets_in_payload(self) -> None:
        import json

        payload = _payload(
            _ConversationMessagesResponseDesign(
                conversation_id="c-1",
                messages=[
                    _ConversationTurnResponseDesign(
                        turn_id=1,
                        role="USER",
                        content="查询 A001 库存",
                        assistant_request_id=None,
                        created_at=_now(),
                    )
                ],
            )
        )
        blob = json.dumps(payload, ensure_ascii=False)
        for sentinel in ("sk-", "Bearer ", "postgresql://", "Traceback", "password"):
            assert sentinel not in blob, sentinel

    def test_17_no_pagination(self) -> None:
        fields = set(_ConversationMessagesResponseDesign.model_fields) | set(
            _ConversationTurnResponseDesign.model_fields
        )
        for forbidden in FORBIDDEN_PAGINATION_FIELDS:
            assert forbidden not in fields, forbidden
        doc = _source(_AUDIT_DOC)
        assert "不实现 Pagination" in doc or "Pagination = Deferred" in doc


# ============================================================
# 18~20：Auth / 向后兼容 / 未实现
# ============================================================


class TestCompatibilityAndImplementationState:
    def test_18_no_auth_claim(self) -> None:
        fields = set(_CreateConversationRequestDesign.model_fields) | set(
            _ConversationResponseDesign.model_fields
        )
        for forbidden in ("user_id", "tenant_id", "token", "authorization"):
            assert forbidden not in fields, forbidden
        doc = _source(_AUDIT_DOC)
        assert "Authentication = NOT IMPLEMENTED" in doc
        assert "Authorization = NOT IMPLEMENTED" in doc

    def test_19_ai_chat_contract_unchanged(self) -> None:
        assert list(ChatRequest.model_fields) == ["question", "project_id"]
        assert list(ChatResponse.model_fields) == [
            "route",
            "content",
            "data",
            "metadata",
        ]
        assert "conversation_id" not in ChatRequest.model_fields
        assert "conversation_id" not in ChatResponse.model_fields
        assert [member.value for member in AssistantOutcome] == [
            "SUCCESS",
            "EMPTY",
            "REFUSED",
            "FAILED",
        ]

    def test_20_conversation_api_is_implemented(self) -> None:
        """Step 8 实现 4 个端点；Step 12 新增 Message POST（4 条路径 / 5 个操作）。"""
        api_root = _REPO_ROOT / _API_DIR
        modules = sorted(
            str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
            for path in api_root.rglob("conversation*.py")
        )
        assert modules == ["backend/app/api/conversations.py"]
        assert "conversations" in _source(_MAIN)

        from backend.app.main import app  # 离线：仅读取 OpenAPI schema

        paths = {
            path: {method.upper() for method in operations}
            for path, operations in app.openapi()["paths"].items()
            if path.startswith("/api/conversations")
        }
        assert paths == {
            "/api/conversations": {"POST"},
            "/api/conversations/{conversation_id}": {"GET"},
            "/api/conversations/{conversation_id}/messages": {"GET", "POST"},
            "/api/conversations/{conversation_id}/archive": {"POST"},
        }
        # 不提供 DELETE / PATCH / PUT（Message POST 自 Step 12 起注册）
        for methods in paths.values():
            assert not methods & {"DELETE", "PATCH", "PUT"}

    def test_20b_existing_observability_routes_unchanged(self) -> None:
        trace_paths = {getattr(r, "path", "") for r in trace_router.routes}
        timeline_paths = {getattr(r, "path", "") for r in timeline_router.routes}
        assert "/observability/assistant-trace/{assistant_request_id}" in trace_paths
        assert "/observability/assistant-timeline/{assistant_request_id}" in (
            timeline_paths
        )


# ============================================================
# 21：文档完整性 + 自身审计
# ============================================================


class TestDesignDocument:
    def test_21a_required_sections_present(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in _REQUIRED_DOC_SECTIONS:
            assert section in doc, section

    def test_21b_endpoints_declared(self) -> None:
        doc = _source(_AUDIT_DOC)
        for endpoint in (
            "POST /api/conversations",
            "GET /api/conversations/{conversation_id}",
            "GET /api/conversations/{conversation_id}/messages",
            "POST /api/conversations/{conversation_id}/archive",
        ):
            assert endpoint in doc, endpoint


class TestSelfAudit:
    def test_22a_self_import_is_offline(self) -> None:
        tree = ast.parse(_source(_SELF))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_22b_service_does_not_import_fastapi(self) -> None:
        tree = ast.parse(_source(_SERVICE))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("fastapi"), node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("fastapi"), alias.name


__all__ = [
    "ERROR_MAPPING",
    "PROPOSED_ENDPOINTS",
    "TestRequestResponseDto",
    "TestStatusAndErrorMapping",
    "TestEmptyAndOrdering",
    "TestBindingAndSecurity",
    "TestLeakageAndPagination",
    "TestCompatibilityAndImplementationState",
    "TestDesignDocument",
    "TestSelfAudit",
]
