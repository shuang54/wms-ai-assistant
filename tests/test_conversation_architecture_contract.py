"""Conversation Architecture Contract（Phase 4.1 Step 1 — Audit Only）。

固化来源：
    `docs/evaluation/Phase 4.1 Step 1 — Conversation Architecture Audit.md`

本文件性质：

    * **纯离线**：DB reads = 0 · DB writes = 0 · network = 0 · LLM = 0
      （不连接 PostgreSQL / 不启动 ASGI app / 不发起任何真实请求）；
    * **Audit Only → 范围守卫**：Phase 4.1 Step 1 本文件登记
      「Conversation 容器 ≠ Assistant Request」的设计边界（当时
      Conversation 尚未实现）；Step 5~8 实现后，同一防漂移意图升级为
      「恰好等于冻结的已声明集合」（见 ``_DECLARED_CONVERSATION_MODULES``
      与 ``_DECLARED_CONVERSATION_ROUTE_PATHS``），现有 /api/ai/chat ·
      Trace API · Timeline API · Outcome contract 的现状行为仍不被改变；
    * 设计 DTO 只存在于本文件内部（``_`` 前缀；**不进入** production
      ``backend/app``）—— 对应任务 §十九「这只是设计 DTO」的约束；
    * 任何"未声明的 conversation 生产模块 / 路由散落到 conversations.py
      之外 / 给现有 ID 体系塞第二个 ID / 把敏感字段写进 conversation
      metadata"的漂移都会使本文件失败 —— 这是**有意的漂移报警**。

边界（与 Phase 3.12 既有 contract 测试同风格）：
    * 本文件自身 executable import 不得出现
      ``sqlalchemy`` / ``psycopg`` / ``backend.app.db`` / 网络库
      （由 ``TestSelfAudit`` 用 AST 校验）；
    * Step 8 起 Conversation API 已注册：由「唯一属主 + 冻结路径集合」校验。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.app.api.assistant_timeline import router as timeline_router
from backend.app.api.assistant_trace import (
    AssistantTraceResponse,
    router as trace_router,
)
from backend.app.api.conversations import router as conversations_router
from backend.app.api.orchestrator_chat import (
    ChatRequest,
    ChatResponse,
    router as orchestrator_router,
)
from backend.app.dto.assistant_outcome import AssistantOutcome
from backend.app.services.assistant_trace import (
    ASSISTANT_REQUEST_ID_MAX_LENGTH,
    current_assistant_request_id,
)
from backend.app.services.tool_execution_context import (
    ToolExecutionContext,
    new_request_id,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_architecture_contract.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 1 — Conversation Architecture Audit.md"
_API_DIR = "backend/app/api"
_TRACE_API = "backend/app/api/assistant_trace.py"
_TIMELINE_API = "backend/app/api/assistant_timeline.py"
_ORCHESTRATOR = "backend/app/services/ai_orchestrator_service.py"
_OUTCOME_MODEL = "backend/app/db/models/assistant_outcome_record.py"

_TRACE_PATH = "/observability/assistant-trace/{assistant_request_id}"
_TIMELINE_PATH = "/observability/assistant-timeline/{assistant_request_id}"
_CHAT_PATH = "/ai/chat"

#: 设计记录的未来 API（§13；Step 8 已在 conversations.py 落地）。
PROPOSED_API_PATHS: tuple[str, ...] = (
    "/api/conversations",
    "/api/conversations/{conversation_id}",
    "/api/conversations/{conversation_id}/messages",
)

#: Step 5~8 已实现并冻结的 Conversation 生产模块（范围守卫；未声明 = 漂移）。
_DECLARED_CONVERSATION_MODULES: tuple[str, ...] = (
    "backend/app/api/conversations.py",
    "backend/app/db/conversation_repository.py",
    "backend/app/db/models/conversation.py",
    "backend/app/db/models/conversation_turn.py",
    "backend/app/dto/conversation_api.py",
    "backend/app/services/conversation_context_builder.py",
    "backend/app/services/conversation_service.py",
)

#: Step 8 已注册的 Conversation 路由路径（conversations.py router 内；
#: main.py 统一挂载 /api 前缀）。
_DECLARED_CONVERSATION_ROUTE_PATHS: tuple[str, ...] = (
    "/conversations",
    "/conversations/{conversation_id}",
    "/conversations/{conversation_id}/archive",
    "/conversations/{conversation_id}/messages",
)

#: Conversation metadata 中**禁止**出现的敏感键（§10 Security）。
FORBIDDEN_METADATA_KEYS: tuple[str, ...] = (
    "api_key",
    "password",
    "database_url",
    "authorization",
    "connection_string",
    "token",
    "secret",
)

#: 禁止出现在本文件自身 import 中的前缀（self-audit）。
_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "sqlalchemy",
    "psycopg",
    "psycopg2",
    "alembic",
    "redis",
    "celery",
    "kafka",
    "backend.app.db",
    "httpx",
    "requests",
    "aiohttp",
    "openai",
    "socket",
    "urllib",
)


# ============================================================
# 设计 DTO（design-only；本文件私有，不进入 production）
# ============================================================


@dataclass(frozen=True)
class _ConversationDesign:
    """Conversation 最小概念字段（任务 §十九；仅设计记录）。"""

    conversation_id: str
    project_id: str
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, str) or not self.conversation_id.strip():
            raise ValueError("conversation_id 必须是非空 str")
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ValueError("project_id 必须是非空 str")


@dataclass(frozen=True)
class _ConversationTurnDesign:
    """ConversationTurn 最小概念字段（任务 §十九；仅设计记录）。

    ``assistant_request_id`` 是**服务端单次请求 ID**（由 Orchestrator
    生成）—— 不是 message_id，也不新增 message_id。
    """

    conversation_id: str
    assistant_request_id: str
    role: str
    content: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.conversation_id, str) or not self.conversation_id.strip():
            raise ValueError("conversation_id 必须是非空 str")
        if (
            not isinstance(self.assistant_request_id, str)
            or not self.assistant_request_id.strip()
        ):
            raise ValueError("assistant_request_id 必须是非空 str")
        if self.role not in ("user", "assistant"):
            raise ValueError(f"role 非法: {self.role!r}")


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _find_route(router: Any, path: str) -> Any:
    """按路径取 APIRoute（离线；不启动 ASGI app）。"""
    for route in router.routes:
        if getattr(route, "path", None) == path:
            return route
    raise AssertionError(f"route not found: {path}")


def _conversation(**overrides: Any) -> _ConversationDesign:
    payload: dict[str, Any] = {
        "conversation_id": "conv-001",
        "project_id": "vietnam-wms",
        "created_at": datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
    }
    payload.update(overrides)
    return _ConversationDesign(**payload)


def _turn(**overrides: Any) -> _ConversationTurnDesign:
    payload: dict[str, Any] = {
        "conversation_id": "conv-001",
        "assistant_request_id": "req-001",
        "role": "user",
        "content": "查询 A001 库存",
        "created_at": datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
    }
    payload.update(overrides)
    return _ConversationTurnDesign(**payload)


def _assert_metadata_safe(metadata: dict[str, Any]) -> None:
    """设计守卫：敏感键 / 敏感值片段不得进入 Conversation metadata。"""
    for key, value in metadata.items():
        assert key.lower() not in FORBIDDEN_METADATA_KEYS, key
        blob = str(value)
        for sentinel in ("postgresql://", "postgres://", "sk-", "Bearer "):
            assert sentinel not in blob, (key, sentinel)


# ============================================================
# 1：Conversation ID 与 Assistant Request ID 是不同概念
# ============================================================


class TestIdBoundary:
    def test_1a_design_dtos_keep_ids_separate(self) -> None:
        conversation = _conversation()
        first = _turn(assistant_request_id="req-001")
        second = _turn(assistant_request_id="req-002")

        # 一个 Conversation 承载 N 个 Assistant Request（ID 各自独立）
        assert first.conversation_id == second.conversation_id
        assert first.conversation_id == conversation.conversation_id
        assert first.assistant_request_id != second.assistant_request_id
        assert first.assistant_request_id != conversation.conversation_id

        # 字段集合精确锁定（4 / 5）—— 不新增 message_id / turn_id / seq
        assert [f.name for f in fields(_ConversationDesign)] == [
            "conversation_id",
            "project_id",
            "created_at",
            "updated_at",
        ]
        assert [f.name for f in fields(_ConversationTurnDesign)] == [
            "conversation_id",
            "assistant_request_id",
            "role",
            "content",
            "created_at",
        ]
        names = {f.name for f in fields(_ConversationTurnDesign)}
        for forbidden in ("message_id", "client_message_id", "turn_id", "seq"):
            assert forbidden not in names, forbidden

    def test_1b_request_context_today_carries_single_id(self) -> None:
        # request context 现状 = contextvar 只承载一个 Assistant Trace ID
        assert current_assistant_request_id() is None  # 未绑定 → None

        generated = new_request_id()
        assert isinstance(generated, str)
        assert len(generated) == 36  # uuid4 字符串
        assert new_request_id() != new_request_id()
        assert ASSISTANT_REQUEST_ID_MAX_LENGTH == 128

    def test_1c_chat_api_contract_has_no_conversation_id(self) -> None:
        assert list(ChatRequest.model_fields) == ["question", "project_id"]
        assert list(ChatResponse.model_fields) == [
            "route",
            "content",
            "data",
            "metadata",
        ]
        for model in (ChatRequest, ChatResponse):
            assert "conversation_id" not in model.model_fields

    def test_1d_tool_execution_context_whitelist_unchanged(self) -> None:
        context = ToolExecutionContext(
            request_id="req-001", round=1, project_id="vietnam-wms"
        )
        context.assert_field_whitelist()
        assert {f.name for f in fields(ToolExecutionContext)} == {
            "request_id",
            "round",
            "project_id",
            "tool_call_id",
        }
        assert "conversation_id" not in {
            f.name for f in fields(ToolExecutionContext)
        }

    def test_1e_orchestrator_generates_exactly_one_id_per_execute(self) -> None:
        """单次 execute 只生成一个 request_id（未来 Conversation 不得改变它）。"""
        tree = ast.parse(_source(_ORCHESTRATOR))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "new_request_id"
        ]
        assert len(calls) == 1

    def test_1f_design_doc_declares_id_boundary(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "conversation_id ≠ assistant_request_id" in doc
        assert "assistant_request_id" in doc


# ============================================================
# 2：现有 Trace API 不被修改
# ============================================================


class TestTraceApiUnchanged:
    def test_2a_trace_route_unchanged(self) -> None:
        route = _find_route(trace_router, _TRACE_PATH)
        assert set(route.methods) == {"GET"}
        assert route.dependant.path_params[0].name == "assistant_request_id"
        assert route.dependant.query_params == []

    def test_2b_trace_response_fields_unchanged(self) -> None:
        assert list(AssistantTraceResponse.model_fields) == [
            "assistant_request_id",
            "outcome",
            "llm_usage",
            "tool_executions",
            "rag_executions",
        ]

    def test_2c_trace_api_source_has_no_conversation_binding(self) -> None:
        source = _source(_TRACE_API)
        assert "conversation_id" not in source
        assert "assistant_request_id" in source
        for route in trace_router.routes:
            assert "conversation" not in getattr(route, "path", "")


# ============================================================
# 3：现有 Timeline API 不被修改
# ============================================================


class TestTimelineApiUnchanged:
    def test_3a_timeline_route_unchanged(self) -> None:
        route = _find_route(timeline_router, _TIMELINE_PATH)
        assert set(route.methods) == {"GET"}
        assert route.dependant.path_params[0].name == "assistant_request_id"
        assert route.dependant.query_params == []

    def test_3b_timeline_api_source_has_no_conversation_binding(self) -> None:
        source = _source(_TIMELINE_API)
        assert "conversation_id" not in source
        assert "assistant_request_id" in source
        for route in timeline_router.routes:
            assert "conversation" not in getattr(route, "path", "")


# ============================================================
# 4：现有 Outcome contract 不被修改
# ============================================================


class TestOutcomeContractUnchanged:
    def test_4a_outcome_enum_values_unchanged(self) -> None:
        assert [member.value for member in AssistantOutcome] == [
            "SUCCESS",
            "EMPTY",
            "REFUSED",
            "FAILED",
        ]
        assert not any(
            "CONVERSATION" in name for name in AssistantOutcome.__members__
        )

    def test_4b_outcome_identity_still_assistant_request_id(self) -> None:
        source = _source(_OUTCOME_MODEL)
        assert "assistant_request_id" in source
        assert "conversation_id" not in source
        assert "UNIQUE" in source  # first-write-wins 语义保持

    def test_4c_trace_outcome_field_untouched_nullable(self) -> None:
        field = AssistantTraceResponse.model_fields["outcome"]
        assert field.default is None  # 无记录 → null，不推断


# ============================================================
# 5：Conversation 不应直接访问 SQLAlchemy / PostgreSQL / RAG / Tool / LLM
# ============================================================


class TestConversationRuntimeBoundaries:
    def test_5a_conversation_production_modules_are_declared(self) -> None:
        """Step 5~8 起 Conversation 已实现：防漂移守卫 = 冻结范围。

        设计阶段（Step 1）断言"不存在 production 模块"；实现冻结后，
        同一意图由"恰好等于已声明集合"承担 —— 任何未登记的新
        conversation 模块（例如未评审的拆分 Service）仍会使本测试失败。
        """
        app_root = _REPO_ROOT / "backend" / "app"
        modules = sorted(
            str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
            for path in app_root.rglob("conversation*.py")
        )
        assert modules == sorted(_DECLARED_CONVERSATION_MODULES)

    def test_5b_no_conversation_package_directory_exists(self) -> None:
        app_root = _REPO_ROOT / "backend" / "app"
        directories = sorted(
            str(path.relative_to(_REPO_ROOT))
            for path in app_root.rglob("conversation*")
            if path.is_dir()
        )
        assert directories == []

    def test_5c_design_doc_declares_runtime_access_boundary(self) -> None:
        doc = _source(_AUDIT_DOC)
        for marker in (
            "不得 import SQLAlchemy",
            "PostgreSQL",
            "RagService",
            "ToolRegistry",
            "LLMClient",
        ):
            assert marker in doc, marker


# ============================================================
# 6：Conversation 不应依赖 DeepSeek / network
# ============================================================


class TestNoNetworkOrLlmDependency:
    def test_6a_self_import_is_offline(self) -> None:
        modules = self._imported_modules()
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_6b_no_network_call_identifiers_in_self(self) -> None:
        tree = ast.parse(_source(_SELF))
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in ("urlopen", "HTTPConnection", "ClientSession", "DeepSeek"):
            assert forbidden not in identifiers, forbidden

    def test_6c_design_doc_declares_no_network_no_llm(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "network = 0" in doc
        assert "DeepSeek" in doc

    @staticmethod
    def _imported_modules() -> set[str]:
        tree = ast.parse(_source(_SELF))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules


# ============================================================
# 7：未来 API 为设计记录，不实际注册路由
# ============================================================


class TestProposedApisAreDesignOnly:
    """Step 1 记录的未来 API 已在 Step 8 注册：守卫 = 唯一属主 + 冻结路径。

    设计阶段断言"路由尚未注册"；实现后防漂移意图由"只有 conversations.py
    可以持有 /conversations、且路径集合精确等于冻结清单"承担 —— 路由散落
    到其他模块或新增未登记路径仍会使本文件失败。
    """

    def test_7a_conversation_routes_only_in_declared_module(self) -> None:
        api_root = _REPO_ROOT / _API_DIR
        sources = sorted(api_root.glob("*.py"))
        assert sources, "api 目录为空（审计失效）"
        for path in sources:
            source = path.read_text(encoding="utf-8")
            if "/conversations" in source:
                assert path.name == "conversations.py", path.name

        paths = {
            route.path
            for route in conversations_router.routes
            if getattr(route, "path", "")
        }
        assert paths == set(_DECLARED_CONVERSATION_ROUTE_PATHS)

    def test_7b_ai_chat_endpoint_unchanged(self) -> None:
        route = _find_route(orchestrator_router, _CHAT_PATH)
        assert set(route.methods) == {"POST"}
        assert route.dependant.path_params == []
        assert route.dependant.body_params[0].name == "request"
        for route in orchestrator_router.routes:
            assert "conversation" not in getattr(route, "path", "")

    def test_7c_design_doc_records_all_proposed_apis(self) -> None:
        doc = _source(_AUDIT_DOC)
        for path in PROPOSED_API_PATHS:
            assert path in doc, path


# ============================================================
# 8：Security fields 不进入设计 metadata
# ============================================================


class TestSecurityMetadata:
    def test_8a_metadata_guard_rejects_sensitive_fields(self) -> None:
        _assert_metadata_safe(
            {
                "conversation_id": "conv-001",
                "assistant_request_id": "req-001",
                "route": "tool",
            }
        )
        for key in FORBIDDEN_METADATA_KEYS:
            with pytest.raises(AssertionError):
                _assert_metadata_safe({key: "x"})
        with pytest.raises(AssertionError):
            _assert_metadata_safe({"endpoint": "postgresql://user:pw@host/db"})

    def test_8b_design_doc_declares_forbidden_metadata_fields(self) -> None:
        doc = _source(_AUDIT_DOC)
        for key in ("api_key", "password", "database_url", "authorization"):
            assert key in doc, key

    def test_8c_design_dtos_have_no_sensitive_fields(self) -> None:
        names = {f.name for f in fields(_ConversationDesign)} | {
            f.name for f in fields(_ConversationTurnDesign)
        }
        for forbidden in FORBIDDEN_METADATA_KEYS:
            assert forbidden not in names, forbidden


# ============================================================
# 9：自身 import 的 AST 审计（纯离线守卫）
# ============================================================


class TestSelfAudit:
    def test_9_self_file_has_no_db_engine_identifiers(self) -> None:
        tree = ast.parse(_source(_SELF))
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "get_" + "engine",
            "get_session_" + "factory",
            "create_" + "engine",
            "session" + "maker",
        ):
            assert forbidden not in identifiers, forbidden


__all__ = [
    "FORBIDDEN_METADATA_KEYS",
    "PROPOSED_API_PATHS",
    "TestIdBoundary",
    "TestTraceApiUnchanged",
    "TestTimelineApiUnchanged",
    "TestOutcomeContractUnchanged",
    "TestConversationRuntimeBoundaries",
    "TestNoNetworkOrLlmDependency",
    "TestProposedApisAreDesignOnly",
    "TestSecurityMetadata",
    "TestSelfAudit",
]
