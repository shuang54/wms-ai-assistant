"""Conversation Message API 架构审计（Phase 4.1 Step 12）—— AST 边界检查。

审计对象：``backend/app/api/conversations.py``（Step 12 接线后）

检查项：

    1. 不得 import：sqlalchemy / psycopg / backend.app.llm / backend.app.rag /
       backend.app.tools / backend.app.embedding / backend.app.reranker /
       rag_service / tool_chat_service / text_to_sql_service /
       ai_router_service / ai_orchestrator_service（运行时零 AI Core import）
    2. 从 ``backend.app.db.conversation_repository`` 只允许 import 异常类型
       （``ConversationRepositoryError``）—— API 不持有 Repository 能力
    3. 不得出现标识符：ConversationRepository / AIOrchestratorService /
       AIRouterService / RagService / ToolChatService / TextToSQLService /
       LLMClient / Session / sessionmaker / Engine / select / insert / delete
    4. AI 执行唯一入口 = ``ChatApplicationService``（accessor + execute_message）；
       API **不**直接调用 AIOrchestrator
    5. 模块级裸赋值最小（logger / router），无新的全局可变状态
    6. 路由集合 = 4 条路径 / 5 个操作；无 DELETE / PATCH / PUT
    7. Message Request / Response 字段边界（只 content；envelope 4 字段）

纯离线：DB = 0 · Network = 0 · LLM = 0（仅静态 AST + OpenAPI schema 读取）。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

from backend.app.api import conversations as conversations_module
from backend.app.dto.conversation_api import (
    ConversationMessageRequest,
    ConversationMessageResponse,
)
from backend.app.main import app
from backend.app.services.chat_application_service import ChatApplicationService

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_message_api_architecture_audit.py"
_TARGET = "backend/app/api/conversations.py"

#: 允许的 import 前缀（白名单：API 只能依赖 HTTP / DTO / Application / 异常）。
_ALLOWED_IMPORT_PREFIXES: tuple[str, ...] = (
    "__future__",
    "logging",
    "typing",
    "collections.abc",
    "fastapi",
    "backend.app.db.conversation_repository",
    "backend.app.dto.conversation_api",
    "backend.app.services.chat_application_service",
    "backend.app.services.conversation_service",
)

#: 禁止的 import 前缀（DB / AI Core / 基础设施）。
_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "sqlalchemy",
    "psycopg",
    "alembic",
    "redis",
    "celery",
    "httpx",
    "openai",
    "backend.app.llm",
    "backend.app.rag",
    "backend.app.tools",
    "backend.app.embedding",
    "backend.app.reranker",
    "backend.app.services.rag_service",
    "backend.app.services.tool_chat_service",
    "backend.app.services.text_to_sql_service",
    "backend.app.services.ai_router_service",
    "backend.app.services.ai_orchestrator_service",
)

#: 禁止出现的标识符（Repository 能力 / AI 能力 / DB 原语）。
_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "ConversationRepository",
    "AIOrchestratorService",
    "AIRouterService",
    "RagService",
    "ToolChatService",
    "TextToSQLService",
    "LLMClient",
    "Session",
    "sessionmaker",
    "Engine",
    "select",
    "insert",
    "delete",
)

#: 允许从 db.conversation_repository import 的符号（只有异常类型）。
_ALLOWED_REPOSITORY_SYMBOLS: frozenset[str] = frozenset(
    {"ConversationRepositoryError"}
)

_MESSAGES_PATH = "/api/conversations/{conversation_id}/messages"

_EXPECTED_ROUTES: dict[str, set[str]] = {
    "/api/conversations": {"POST"},
    "/api/conversations/{conversation_id}": {"GET"},
    "/api/conversations/{conversation_id}/messages": {"GET", "POST"},
    "/api/conversations/{conversation_id}/archive": {"POST"},
}


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _parse(relative: str) -> ast.Module:
    return ast.parse(_source(relative))


def _module_imports(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _imported_symbols_from(tree: ast.Module, module_name: str) -> set[str]:
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module_name:
            symbols |= {alias.name for alias in node.names}
    return symbols


def _module_identifiers(tree: ast.Module) -> set[str]:
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


def _module_level_assign_targets(tree: ast.Module) -> set[str]:
    targets: set[str] = set()
    for node in getattr(tree, "body", []):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and not target.id.startswith("__"):
                targets.add(target.id)
    return targets


# ============================================================
# 1：无 DB / AI Core 依赖
# ============================================================


class TestNoDbOrAiDependency:
    def test_1a_no_forbidden_module_imports(self) -> None:
        modules = _module_imports(_parse(_TARGET))
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_1b_all_imports_are_whitelisted(self) -> None:
        modules = _module_imports(_parse(_TARGET))
        for module in modules:
            assert any(
                module.startswith(prefix) for prefix in _ALLOWED_IMPORT_PREFIXES
            ), module

    def test_1c_repository_symbol_whitelist(self) -> None:
        symbols = _imported_symbols_from(
            _parse(_TARGET), "backend.app.db.conversation_repository"
        )
        assert symbols <= _ALLOWED_REPOSITORY_SYMBOLS, symbols

    def test_1d_no_repository_or_ai_identifiers(self) -> None:
        identifiers = _module_identifiers(_parse(_TARGET))
        for forbidden in _FORBIDDEN_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden

    def test_1e_runtime_has_no_ai_module_attributes(self) -> None:
        for attribute in (
            "AIOrchestrationResult",
            "AIOrchestratorService",
            "RagService",
            "ToolChatService",
            "TextToSQLService",
            "Session",
        ):
            assert not hasattr(conversations_module, attribute), attribute


# ============================================================
# 2：AI 执行唯一入口 = ChatApplicationService
# ============================================================


class TestApplicationServiceEntrypoint:
    def test_2a_accessor_exists(self) -> None:
        accessor = getattr(conversations_module, "get_chat_application_service", None)
        assert callable(accessor)
        assert accessor() is conversations_module._chat_application_service

    def test_2b_default_is_chat_application_service(self) -> None:
        assert isinstance(
            conversations_module._chat_application_service,
            ChatApplicationService,
        )

    def test_2c_endpoint_delegates_to_execute_message(self) -> None:
        source = _source(_TARGET)
        assert "service.execute_message(" in source
        assert "await service.execute_message" in source

    def test_2d_no_direct_orchestrator_wiring(self) -> None:
        """以 AST 标识符为准（docstring / 注释中提及历史装配名不算依赖）。"""
        identifiers = _module_identifiers(_parse(_TARGET))
        for forbidden in (
            "AIOrchestratorService",
            "_default_orchestrator",
            "get_default_orchestrator",
            "build_orchestrator_for_project",
        ):
            assert forbidden not in identifiers, forbidden
        modules = _module_imports(_parse(_TARGET))
        for forbidden in (
            "backend.app.services.ai_orchestrator_service",
            "backend.app.services.project_orchestrator_factory",
        ):
            assert forbidden not in modules, forbidden

    def test_2e_endpoint_is_async_with_expected_signature(self) -> None:
        endpoint = conversations_module.execute_conversation_message
        assert inspect.iscoroutinefunction(endpoint)
        params = list(inspect.signature(endpoint).parameters)
        assert params == ["conversation_id", "request"]


# ============================================================
# 3：全局状态 / 路由
# ============================================================


class TestGlobalStateAndRoutes:
    def test_3a_module_level_assign_is_minimal(self) -> None:
        targets = _module_level_assign_targets(_parse(_TARGET))
        assert targets <= {"logger", "router"}, targets

    def test_3b_route_set(self) -> None:
        paths = {
            path: {method.upper() for method in operations}
            for path, operations in app.openapi()["paths"].items()
            if path.startswith("/api/conversations")
        }
        assert paths == _EXPECTED_ROUTES
        for methods in paths.values():
            assert not methods & {"DELETE", "PATCH", "PUT"}

    def test_3c_messages_path_has_get_and_post(self) -> None:
        operations = app.openapi()["paths"][_MESSAGES_PATH]
        assert {method.upper() for method in operations} == {"GET", "POST"}


# ============================================================
# 4：DTO 字段边界
# ============================================================


class TestSchemaBoundary:
    def test_4a_request_only_content(self) -> None:
        assert list(ConversationMessageRequest.model_fields) == ["content"]
        for forbidden in (
            "project_id",
            "assistant_request_id",
            "request_id",
            "route",
            "metadata",
            "context",
            "model",
            "provider",
        ):
            assert forbidden not in ConversationMessageRequest.model_fields

    def test_4b_response_envelope(self) -> None:
        assert list(ConversationMessageResponse.model_fields) == [
            "route",
            "content",
            "data",
            "metadata",
        ]

    def test_4c_no_secret_like_fields(self) -> None:
        fields = set(ConversationMessageRequest.model_fields) | set(
            ConversationMessageResponse.model_fields
        )
        for forbidden in (
            "api_key",
            "password",
            "authorization",
            "database_url",
            "prompt",
            "sql",
            "session",
            "engine",
        ):
            assert forbidden not in fields, forbidden


# ============================================================
# 5：自身审计
# ============================================================


class TestSelfAudit:
    def test_5a_self_import_is_offline(self) -> None:
        modules = _module_imports(_parse(_SELF))
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            assert not module.startswith("sqlalchemy"), module
            assert not module.startswith("httpx"), module
            assert not module.startswith("psycopg"), module

    def test_5b_audit_signature_is_static(self) -> None:
        """本审计文件不得引入 TestClient / 环境变量读取（纯静态审计）。"""
        modules = _module_imports(_parse(_SELF))
        for forbidden in ("os", "starlette", "fastapi.testclient", "httpx"):
            assert forbidden not in modules, forbidden
        identifiers = _module_identifiers(_parse(_SELF))
        for forbidden in ("TestClient", "environ", "getenv"):
            assert forbidden not in identifiers, forbidden


__all__ = [
    "TestNoDbOrAiDependency",
    "TestApplicationServiceEntrypoint",
    "TestGlobalStateAndRoutes",
    "TestSchemaBoundary",
    "TestSelfAudit",
]
