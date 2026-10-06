"""ChatApplicationService 依赖方向审计（Phase 4.1 Step 14）—— AST 边界。

审计对象：

    1. ``backend/app/services/chat_application_service.py``
       —— 允许依赖：ConversationService / ConversationContextBuilder /
       Orchestrator Protocol + Factory（AIOrchestrationResult DTO）
    2. ``backend/app/services/conversation_context_builder.py``
       —— 反向依赖检查（不得 import ChatApplicationService /
       ConversationService / AIOrchestrator，避免循环依赖）

检查项（全部 AST，不做全文扫描）：

    1. service import 白名单 / 禁列表（sqlalchemy / psycopg / backend.app.db /
       rag_service / tool_chat_service / text_to_sql_service / ai_router_service /
       backend.app.llm / rag / tools / embedding / reranker / prompts / api）
    2. service 标识符黑名单（Repository / Session / Engine / SQL 原语 /
       LLMClient / RagService / ToolChatService / TextToSQLService / AIRouterService）
    3. service 运行时无 DB / AI 实现类绑定
    4. service 注入点恰为 3 个（conversation_service / orchestrator_factory /
       context_builder）；公共面恰为 3 属性 + execute_message
    5. 单向依赖：conversation_service / conversation_context_builder /
       ai_orchestrator_service 均不 import chat_application_service
    6. 模块级裸赋值最小（logger）
    7. 自身审计（本文件离线）

纯离线：DB = 0 · Network = 0 · LLM = 0（仅静态 AST）。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from backend.app.services import chat_application_service as service_module

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_chat_application_service_architecture_audit.py"
_SERVICE = "backend/app/services/chat_application_service.py"
_BUILDER = "backend/app/services/conversation_context_builder.py"
_CONVERSATION_SERVICE = "backend/app/services/conversation_service.py"
_ORCHESTRATOR = "backend/app/services/ai_orchestrator_service.py"

_ALLOWED_IMPORT_PREFIXES: tuple[str, ...] = (
    "__future__",
    "logging",
    "collections.abc",
    "dataclasses",
    "typing",
    "backend.app.services.conversation_service",
    "backend.app.services.conversation_context_builder",
    "backend.app.services.ai_orchestrator_service",
    "backend.app.services.project_orchestrator_factory",
)

_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "sqlalchemy",
    "psycopg",
    "backend.app.db",
    "backend.app.api",
    "backend.app.services.rag_service",
    "backend.app.services.tool_chat_service",
    "backend.app.services.text_to_sql_service",
    "backend.app.services.ai_router_service",
    "backend.app.llm",
    "backend.app.rag",
    "backend.app.tools",
    "backend.app.embedding",
    "backend.app.reranker",
    "backend.app.prompts",
)

_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "ConversationRepository",
    "Session",
    "sessionmaker",
    "Engine",
    "select",
    "insert",
    "update",
    "delete",
    "AIOrchestratorService",
    "AIRouterService",
    "LLMClient",
    "RagService",
    "ToolChatService",
    "TextToSQLService",
)

_EXPECTED_INIT_PARAMS: tuple[str, ...] = (
    "self",
    "conversation_service",
    "orchestrator_factory",
    "context_builder",
)

_EXPECTED_PUBLIC_SURFACE: set[str] = {
    "conversation_service",
    "orchestrator_factory",
    "context_builder",
    "execute_message",
}


def _parse(relative: str) -> ast.Module:
    return ast.parse((_REPO_ROOT / relative).read_text(encoding="utf-8"))


def _module_imports(relative: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_parse(relative)):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _identifiers(relative: str) -> set[str]:
    tree = _parse(relative)
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


def _module_level_assign_targets(relative: str) -> set[str]:
    targets: set[str] = set()
    for node in getattr(_parse(relative), "body", []):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and not target.id.startswith("__"):
                targets.add(target.id)
    return targets


# ============================================================
# 1. service import / 标识符边界
# ============================================================


class TestServiceDependencyBoundary:
    def test_1a_no_forbidden_imports(self) -> None:
        modules = _module_imports(_SERVICE)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_1b_all_imports_whitelisted(self) -> None:
        modules = _module_imports(_SERVICE)
        for module in modules:
            assert any(
                module.startswith(prefix) for prefix in _ALLOWED_IMPORT_PREFIXES
            ), module

    def test_1c_no_repository_or_ai_implementation_identifiers(self) -> None:
        identifiers = _identifiers(_SERVICE)
        for forbidden in _FORBIDDEN_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden

    def test_1d_dependencies_are_expected_three(self) -> None:
        modules = _module_imports(_SERVICE)
        assert "backend.app.services.conversation_service" in modules
        assert "backend.app.services.conversation_context_builder" in modules
        assert "backend.app.services.ai_orchestrator_service" in modules

    def test_1e_runtime_has_no_db_or_ai_implementation_attributes(self) -> None:
        for attribute in (
            "Session",
            "sessionmaker",
            "ConversationRepository",
            "AIOrchestratorService",
            "RagService",
            "LLMClient",
        ):
            assert not hasattr(service_module, attribute), attribute


# ============================================================
# 2. 注入点 / 公共面 / 状态
# ============================================================


class TestServiceShapeBoundary:
    def test_2a_init_parameters_are_frozen(self) -> None:
        params = list(
            inspect.signature(service_module.ChatApplicationService.__init__).parameters
        )
        assert tuple(params) == _EXPECTED_INIT_PARAMS
        for name in _EXPECTED_INIT_PARAMS[1:]:
            parameter = inspect.signature(
                service_module.ChatApplicationService.__init__
            ).parameters[name]
            assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    def test_2b_public_surface_is_frozen(self) -> None:
        public = {
            name
            for name in vars(service_module.ChatApplicationService)
            if not name.startswith("_")
        }
        assert public == _EXPECTED_PUBLIC_SURFACE

    def test_2c_module_level_assign_is_minimal(self) -> None:
        """裸赋值仅 logger + 类型别名（OrchestratorFactory）；无状态容器。"""
        assert _module_level_assign_targets(_SERVICE) <= {
            "logger",
            "OrchestratorFactory",
        }

    def test_2d_execute_message_is_async_keyword_only(self) -> None:
        assert inspect.iscoroutinefunction(
            service_module.ChatApplicationService.execute_message
        )
        params = {
            name: parameter.kind
            for name, parameter in inspect.signature(
                service_module.ChatApplicationService.execute_message
            ).parameters.items()
            if name != "self"
        }
        assert params == {
            "conversation_id": inspect.Parameter.KEYWORD_ONLY,
            "content": inspect.Parameter.KEYWORD_ONLY,
            # Phase 4.2 Step 6：可选幂等键（keyword-only）
            "idempotency_key": inspect.Parameter.KEYWORD_ONLY,
        }


# ============================================================
# 3. 单向依赖（无循环）
# ============================================================


class TestNoReverseDependency:
    def test_3a_builder_does_not_import_service_or_ai(self) -> None:
        modules = _module_imports(_BUILDER)
        for forbidden in (
            "backend.app.services.chat_application_service",
            "backend.app.services.conversation_service",
            "backend.app.services.ai_orchestrator_service",
        ):
            assert forbidden not in modules, forbidden

    def test_3b_conversation_service_does_not_import_application_layer(self) -> None:
        modules = _module_imports(_CONVERSATION_SERVICE)
        for forbidden in (
            "backend.app.services.chat_application_service",
            "backend.app.services.conversation_context_builder",
        ):
            assert forbidden not in modules, forbidden

    def test_3c_orchestrator_does_not_import_application_layer(self) -> None:
        modules = _module_imports(_ORCHESTRATOR)
        for forbidden in (
            "backend.app.services.chat_application_service",
            "backend.app.services.conversation_context_builder",
            "backend.app.services.conversation_service",
        ):
            assert forbidden not in modules, forbidden

    def test_3d_service_does_not_import_api_layer(self) -> None:
        modules = _module_imports(_SERVICE)
        for module in modules:
            assert not module.startswith("backend.app.api"), module


# ============================================================
# 4. 自身审计
# ============================================================


class TestSelfAudit:
    def test_4a_self_import_is_offline(self) -> None:
        modules = _module_imports(_SELF)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            assert not module.startswith("sqlalchemy"), module
            assert not module.startswith("httpx"), module
            assert not module.startswith("psycopg"), module

    def test_4b_audit_is_static_only(self) -> None:
        identifiers = _identifiers(_SELF)
        for forbidden in ("TestClient", "environ", "getenv"):
            assert forbidden not in identifiers, forbidden


__all__ = [
    "TestServiceDependencyBoundary",
    "TestServiceShapeBoundary",
    "TestNoReverseDependency",
    "TestSelfAudit",
]
