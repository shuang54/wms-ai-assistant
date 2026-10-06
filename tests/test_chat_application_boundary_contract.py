"""Chat / Application Layer Boundary Contract（Phase 4.1 Step 10 — Audit Only）。

固化来源：
    `docs/evaluation/Phase 4.1 Step 10 — Chat Application Boundary Audit.md`

本文件性质：

    * **纯离线**：DB = 0 · Network = 0 · LLM = 0 · DB Writes = 0
      （不驱动 /api/ai/chat、不调用 AIOrchestrator、不连接 PostgreSQL）；
    * **Audit Only**：本 Step **不实现** Conversation → AI 集成 —— 只审计当前
      Chat / AI Application Layer，冻结未来 Integration Point 与依赖方向；
    * **以真实代码为据**：ConversationService 只 import ConversationRepository；
      AIOrchestratorService 不 import 任何 conversation 依赖；
      `POST /api/ai/chat` 的 request/response 仍为 `{question, project_id?}` /
      `{route, content, data, metadata}`（**无** conversation_id）；
    * 任何"ConversationService → AIOrchestrator"或"AIOrchestrator →
      ConversationRepository"的反向依赖都会使本文件失败 —— 有意的漂移报警。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

from backend.app.api.assistant_timeline import router as timeline_router
from backend.app.api.assistant_trace import (
    AssistantTraceResponse,
    router as trace_router,
)
from backend.app.api.orchestrator_chat import ChatRequest, ChatResponse
from backend.app.db.conversation_repository import ConversationRepository
from backend.app.dto.assistant_outcome import AssistantOutcome
from backend.app.services.conversation_service import (
    ConversationArchivedError,
    ConversationNotFoundError,
    ConversationService,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_chat_application_boundary_contract.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 10 — Chat Application Boundary Audit.md"

_ORCHESTRATOR = "backend/app/services/ai_orchestrator_service.py"
_CONVERSATION_SERVICE = "backend/app/services/conversation_service.py"
_CONVERSATION_REPOSITORY = "backend/app/db/conversation_repository.py"
_CONVERSATION_API = "backend/app/api/conversations.py"
_ORCHESTRATOR_API = "backend/app/api/orchestrator_chat.py"
_CONVERSATION_ORM = "backend/app/db/models/conversation.py"
_TURN_ORM = "backend/app/db/models/conversation_turn.py"

# ============================================================
# 冻结常量（Design / Audit 结论）
# ============================================================

#: 未来 Integration Point（推荐方案 B）。
RECOMMENDED_OPTION: str = "B"
FUTURE_INTEGRATION_POINT: str = "Chat Application Service"

#: 各层职责（冻结）。
CONVERSATION_SERVICE_RESPONSIBILITY: tuple[str, ...] = (
    "Conversation / Turn persistence",
)
AI_ORCHESTRATOR_RESPONSIBILITY: tuple[str, ...] = ("AI execution",)
APPLICATION_SERVICE_RESPONSIBILITY: tuple[str, ...] = ("workflow composition",)

#: 未来 Turn 生命周期（冻结；本 Step 不实现）。
TURN_LIFECYCLE: tuple[str, ...] = (
    "append USER turn",
    "AI execution",
    "append ASSISTANT turn",
)

#: 事务边界：AI 执行**不得**包在 DB 事务内。
TRANSACTION_BOUNDARY: tuple[str, ...] = (
    "TX1 USER turn",
    "AI execution outside transaction",
    "TX2 ASSISTANT turn",
)

#: ID 相关性（四者互不等价）。
ID_CORRELATION: tuple[str, ...] = (
    "conversation_id",
    "turn_id",
    "assistant_request_id",
    "provider request_id",
)

#: 失败语义（设计结论）。
FAILURE_SEMANTICS: dict[str, str] = {
    "user_turn": "USER turn 保留",
    "assistant_turn": "只有可展示给用户的最终内容才创建 ASSISTANT turn",
    "internal_error": "纯内部异常（无内容）→ 不创建 ASSISTANT turn",
}

#: Project binding（设计结论）。
PROJECT_BINDING_RULE: str = "conversation.project_id = AI execution project_id"
PROJECT_BINDING_NOT_AUTH: str = "project_id binding ≠ authorization"

#: 向后兼容（冻结）。
BACKWARD_COMPATIBILITY: str = "POST /api/ai/chat unchanged"

#: /api/ai/chat 当前 Contract（真实代码）。
AI_CHAT_REQUEST_FIELDS: tuple[str, ...] = ("question", "project_id")
AI_CHAT_RESPONSE_FIELDS: tuple[str, ...] = ("route", "content", "data", "metadata")

#: ConversationService / Repository 方法集合（Step 3/4 冻结；不得新增 AI 方法）。
CONVERSATION_SERVICE_METHODS: tuple[str, ...] = (
    "create_conversation",
    "get_conversation",
    "archive_conversation",
    "append_turn",
    "list_turns",
    # Phase 4.2 Step 6：消息幂等查询（读路径；不新增状态 / 不新增表）
    "find_turn_by_idempotency_key",
    "find_assistant_turn_after",
)
CONVERSATION_REPOSITORY_METHODS: tuple[str, ...] = (
    "create",
    "get_by_conversation_id",
    "update_status",
    "append_turn",
    "list_turns_by_conversation_id",
    # Phase 4.2 Step 6：幂等键查询 / ASSISTANT 归属查询（显式列 · 只读）
    "find_turn_by_idempotency_key",
    "find_next_assistant_turn",
)

#: Deferred（本 Step 不实现）。
DEFERRED_ITEMS: tuple[str, ...] = (
    "Message POST",
    "Chat Application Service implementation",
    "Context Builder",
    "Memory",
    "Summary",
    "Regenerate",
    "Auth",
    "Pagination",
)

_REQUIRED_DOC_SECTIONS: tuple[str, ...] = (
    "## 1. Current Architecture",
    "## 2. Current Responsibilities",
    "## 3. Future Integration Point",
    "## 4. Turn Lifecycle",
    "## 5. Transaction Boundary",
    "## 6. ID Correlation",
    "## 7. Failure Semantics",
    "## 8. Project Binding",
    "## 9. Backward Compatibility",
    "## 10. Deferred",
)

#: AI Core 依赖（Conversation 层**不得**出现）。
_AI_IMPORT_PREFIXES: tuple[str, ...] = (
    "backend.app.services.ai_orchestrator_service",
    "backend.app.services.ai_router_service",
    "backend.app.services.rag_service",
    "backend.app.services.tool_chat_service",
    "backend.app.services.text_to_sql_service",
    "backend.app.llm",
    "backend.app.rag",
    "backend.app.tools",
    "backend.app.embedding",
    "backend.app.reranker",
)

_AI_IDENTIFIERS: tuple[str, ...] = (
    "AIOrchestratorService",
    "AIRouterService",
    "RagService",
    "ToolChatService",
    "TextToSQLService",
    "LLMClient",
)

#: 网络 / LLM 依赖（Conversation 层**不得**出现）。
_NETWORK_IMPORT_PREFIXES: tuple[str, ...] = (
    "httpx",
    "requests",
    "aiohttp",
    "openai",
    "socket",
    "urllib",
)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _module_imports(relative: str) -> set[str]:
    tree = ast.parse(_source(relative))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _module_identifiers(relative: str) -> set[str]:
    tree = ast.parse(_source(relative))
    return {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }


def _module_level_assign_targets(relative: str) -> set[str]:
    """模块级**裸赋值**（非 AnnAssign 常量）目标名 —— 用于检测全局可变状态。

    忽略 ``__all__`` 等 dunder 目标（它们是不可变导出清单，不是状态容器）。
    """
    tree = ast.parse(_source(relative))
    targets: set[str] = set()
    for node in getattr(tree, "body", []):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and not target.id.startswith("__"):
                targets.add(target.id)
    return targets


def _public_methods(cls: type) -> set[str]:
    return {
        name
        for name in vars(cls)
        if not name.startswith("_") and callable(getattr(cls, name, None))
    }


# ============================================================
# 1：ConversationService 不依赖 AI Core
# ============================================================


class TestConversationServiceHasNoAiDependency:
    def test_1a_no_ai_imports(self) -> None:
        modules = _module_imports(_CONVERSATION_SERVICE)
        for module in modules:
            for prefix in _AI_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_1b_no_ai_identifiers(self) -> None:
        identifiers = _module_identifiers(_CONVERSATION_SERVICE)
        for forbidden in _AI_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden

    def test_1c_no_sqlalchemy_or_session(self) -> None:
        modules = _module_imports(_CONVERSATION_SERVICE)
        for module in modules:
            assert not module.startswith("sqlalchemy"), module
        identifiers = _module_identifiers(_CONVERSATION_SERVICE)
        for forbidden in ("Session", "session", "select", "insert", "update", "engine"):
            assert forbidden not in identifiers, forbidden

    def test_1d_no_network_or_llm(self) -> None:
        modules = _module_imports(_CONVERSATION_SERVICE)
        for module in modules:
            for prefix in _NETWORK_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_1e_service_methods_are_frozen(self) -> None:
        assert _public_methods(ConversationService) == set(
            CONVERSATION_SERVICE_METHODS
        )


# ============================================================
# 2：AIOrchestrator 不依赖 Conversation
# ============================================================


class TestAiOrchestratorHasNoConversationDependency:
    def test_2a_no_conversation_imports(self) -> None:
        modules = _module_imports(_ORCHESTRATOR)
        offenders = [m for m in modules if "conversation" in m.lower()]
        assert offenders == [], offenders

    def test_2b_no_conversation_identifiers(self) -> None:
        identifiers = _module_identifiers(_ORCHESTRATOR)
        offenders = [
            name for name in identifiers if "conversation" in name.lower()
        ]
        assert offenders == [], offenders

    def test_2c_orchestrator_does_not_import_repository_layer(self) -> None:
        modules = _module_imports(_ORCHESTRATOR)
        assert "backend.app.db.conversation_repository" not in modules


# ============================================================
# 3：Repository / ORM 不依赖 AI Core
# ============================================================


class TestPersistenceHasNoAiDependency:
    def test_3a_repository_has_no_ai_imports(self) -> None:
        modules = _module_imports(_CONVERSATION_REPOSITORY)
        for module in modules:
            for prefix in _AI_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_3b_repository_methods_are_frozen(self) -> None:
        """5 个契约方法冻结（``build_*`` 是 SQL 构造点，不计入对外契约）。"""
        methods = _public_methods(ConversationRepository)
        contract = {m for m in methods if not m.startswith("build_")}
        assert contract == set(CONVERSATION_REPOSITORY_METHODS)
        for forbidden in ("delete", "find_by_project", "search", "count"):
            assert forbidden not in contract, forbidden

    def test_3c_orm_models_have_no_ai_imports(self) -> None:
        for relative in (_CONVERSATION_ORM, _TURN_ORM):
            modules = _module_imports(relative)
            for module in modules:
                for prefix in _AI_IMPORT_PREFIXES:
                    assert not module.startswith(prefix), f"{relative}:{module}"


# ============================================================
# 4：/api/ai/chat 未被修改（不含 conversation_id）
# ============================================================


class TestAiChatContractUnchanged:
    def test_4a_request_fields(self) -> None:
        assert list(ChatRequest.model_fields) == list(AI_CHAT_REQUEST_FIELDS)
        assert "conversation_id" not in ChatRequest.model_fields

    def test_4b_response_fields(self) -> None:
        assert list(ChatResponse.model_fields) == list(AI_CHAT_RESPONSE_FIELDS)
        assert "conversation_id" not in ChatResponse.model_fields

    def test_4c_source_has_no_conversation_id(self) -> None:
        assert "conversation_id" not in _source(_ORCHESTRATOR_API)

    def test_4d_route_still_exists(self) -> None:
        from backend.app.main import app

        paths = app.openapi()["paths"]
        assert "/api/ai/chat" in paths
        assert "post" in {m.lower() for m in paths["/api/ai/chat"]}


# ============================================================
# 5：Conversation API 不含 AI execution
# ============================================================


class TestConversationApiHasNoAiExecution:
    def test_5a_no_ai_imports(self) -> None:
        modules = _module_imports(_CONVERSATION_API)
        for module in modules:
            for prefix in _AI_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_5b_no_ai_identifiers(self) -> None:
        identifiers = _module_identifiers(_CONVERSATION_API)
        for forbidden in _AI_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden
        for forbidden in ("execute", "answer"):
            assert forbidden not in identifiers, forbidden

    def test_5c_route_set_includes_message_post(self) -> None:
        """Step 12 起 Message POST 已注册（GET + POST）；无 DELETE / PATCH / PUT。"""
        from backend.app.main import app

        paths = {
            path: {m.upper() for m in operations}
            for path, operations in app.openapi()["paths"].items()
            if path.startswith("/api/conversations")
        }
        assert paths == {
            "/api/conversations": {"POST"},
            "/api/conversations/{conversation_id}": {"GET"},
            "/api/conversations/{conversation_id}/messages": {"GET", "POST"},
            "/api/conversations/{conversation_id}/archive": {"POST"},
        }
        for methods in paths.values():
            assert not methods & {"DELETE", "PATCH", "PUT"}


# ============================================================
# 6：无新的全局可变状态 / DB 依赖 / LLM
# ============================================================


class TestNoNewGlobalStateOrInfrastructure:
    def test_6a_conversation_api_global_state_is_minimal(self) -> None:
        """只有 logger / router 的模块级裸赋值（服务单例用 AnnAssign 声明）。"""
        targets = _module_level_assign_targets(_CONVERSATION_API)
        assert targets <= {"logger", "router"}, targets

    def test_6b_conversation_service_has_no_global_state(self) -> None:
        assert _module_level_assign_targets(_CONVERSATION_SERVICE) == set()

    def test_6c_repository_has_no_global_state(self) -> None:
        assert _module_level_assign_targets(_CONVERSATION_REPOSITORY) == set()

    def test_6d_no_new_db_dependency_in_service(self) -> None:
        modules = _module_imports(_CONVERSATION_SERVICE)
        for forbidden in (
            "sqlalchemy",
            "backend.app.db.session",
            "backend.app.db.base",
        ):
            assert forbidden not in modules, forbidden

    def test_6e_no_llm_or_network_in_conversation_stack(self) -> None:
        for relative in (
            _CONVERSATION_API,
            _CONVERSATION_SERVICE,
            _CONVERSATION_REPOSITORY,
        ):
            modules = _module_imports(relative)
            for module in modules:
                for prefix in _NETWORK_IMPORT_PREFIXES:
                    assert not module.startswith(prefix), f"{relative}:{module}"


# ============================================================
# 7：设计结论冻结（Integration Point / 生命周期 / 事务 / ID / 失败）
# ============================================================


class TestDesignConclusions:
    def test_7a_recommended_option_is_b(self) -> None:
        assert RECOMMENDED_OPTION == "B"
        assert FUTURE_INTEGRATION_POINT == "Chat Application Service"

    def test_7b_responsibilities(self) -> None:
        assert CONVERSATION_SERVICE_RESPONSIBILITY == (
            "Conversation / Turn persistence",
        )
        assert AI_ORCHESTRATOR_RESPONSIBILITY == ("AI execution",)
        assert APPLICATION_SERVICE_RESPONSIBILITY == ("workflow composition",)

    def test_7c_turn_lifecycle(self) -> None:
        assert TURN_LIFECYCLE == (
            "append USER turn",
            "AI execution",
            "append ASSISTANT turn",
        )

    def test_7d_transaction_boundary_excludes_ai(self) -> None:
        assert TRANSACTION_BOUNDARY == (
            "TX1 USER turn",
            "AI execution outside transaction",
            "TX2 ASSISTANT turn",
        )
        # AI 执行不在任何 DB 事务内
        assert "AI execution outside transaction" in TRANSACTION_BOUNDARY

    def test_7e_id_correlation_is_four_distinct_ids(self) -> None:
        assert ID_CORRELATION == (
            "conversation_id",
            "turn_id",
            "assistant_request_id",
            "provider request_id",
        )
        assert len(set(ID_CORRELATION)) == 4

    def test_7f_failure_semantics(self) -> None:
        assert FAILURE_SEMANTICS["user_turn"] == "USER turn 保留"
        assert "可展示" in FAILURE_SEMANTICS["assistant_turn"]
        assert "不创建" in FAILURE_SEMANTICS["internal_error"]

    def test_7g_project_binding_is_not_authorization(self) -> None:
        assert PROJECT_BINDING_RULE == (
            "conversation.project_id = AI execution project_id"
        )
        assert "authorization" in PROJECT_BINDING_NOT_AUTH


# ============================================================
# 8：既有边界未变化
# ============================================================


class TestExistingBoundaryUnchanged:
    def test_8a_outcome_contract_unchanged(self) -> None:
        assert [member.value for member in AssistantOutcome] == [
            "SUCCESS",
            "EMPTY",
            "REFUSED",
            "FAILED",
        ]

    def test_8b_trace_contract_unchanged(self) -> None:
        assert list(AssistantTraceResponse.model_fields) == [
            "assistant_request_id",
            "outcome",
            "llm_usage",
            "tool_executions",
            "rag_executions",
        ]
        paths = {getattr(r, "path", "") for r in trace_router.routes}
        assert "/observability/assistant-trace/{assistant_request_id}" in paths

    def test_8c_timeline_route_unchanged(self) -> None:
        paths = {getattr(r, "path", "") for r in timeline_router.routes}
        assert "/observability/assistant-timeline/{assistant_request_id}" in paths

    def test_8d_service_error_hierarchy(self) -> None:
        assert issubclass(ConversationNotFoundError, Exception)
        assert issubclass(ConversationArchivedError, Exception)

    def test_8e_service_signature_has_no_ai_parameters(self) -> None:
        for name in CONVERSATION_SERVICE_METHODS:
            params = inspect.signature(
                getattr(ConversationService, name)
            ).parameters
            assert "orchestrator" not in params, name
            assert "rag_service" not in params, name


# ============================================================
# 9：文档完整性 + 自身离线审计
# ============================================================


class TestDesignDocument:
    def test_9a_required_sections_present(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in _REQUIRED_DOC_SECTIONS:
            assert section in doc, section

    def test_9b_doc_records_backward_compatibility(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert BACKWARD_COMPATIBILITY in doc

    def test_9c_doc_records_deferred(self) -> None:
        doc = _source(_AUDIT_DOC)
        for item in ("Message POST", "Context Builder", "Memory", "Auth"):
            assert item in doc, item


class TestSelfAudit:
    def test_10a_self_import_is_offline(self) -> None:
        modules = _module_imports(_SELF)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in ("sqlalchemy",) + _NETWORK_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_10b_no_ai_execution_identifiers(self) -> None:
        identifiers = _module_identifiers(_SELF)
        for forbidden in ("AIOrchestratorService", "RagService", "LLMClient"):
            assert forbidden not in identifiers, forbidden


__all__ = [
    "AI_CHAT_REQUEST_FIELDS",
    "AI_CHAT_RESPONSE_FIELDS",
    "DEFERRED_ITEMS",
    "FAILURE_SEMANTICS",
    "FUTURE_INTEGRATION_POINT",
    "ID_CORRELATION",
    "RECOMMENDED_OPTION",
    "TRANSACTION_BOUNDARY",
    "TURN_LIFECYCLE",
    "TestConversationServiceHasNoAiDependency",
    "TestAiOrchestratorHasNoConversationDependency",
    "TestPersistenceHasNoAiDependency",
    "TestAiChatContractUnchanged",
    "TestConversationApiHasNoAiExecution",
    "TestNoNewGlobalStateOrInfrastructure",
    "TestDesignConclusions",
    "TestExistingBoundaryUnchanged",
    "TestDesignDocument",
    "TestSelfAudit",
]
