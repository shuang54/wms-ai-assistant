"""Context Selection 架构审计（Phase 4.1 Step 17）—— AST 依赖边界。

审计对象（**本 Step 全部零修改**）：

    1. ``backend/app/services/conversation_context_builder.py``
       —— 无 tokenizer / DB / LLM / RAG / Tool
    2. ``backend/app/services/conversation_service.py``
       —— 无 LLM / tokenizer / RAG / Tool
    3. ``backend/app/db/conversation_repository.py``
       —— 无 LLM / Context Builder / AI
    4. ``backend/app/services/chat_application_service.py``
       —— 允许 {ConversationService, ContextBuilder, AIOrchestrator DTO/Factory}；
          **当前不得**存在 ContextSelector 依赖（本 Step 不实现 production Selector）

检查项（全部 AST，不做全文扫描）：

    * import 禁列表 / 白名单；
    * 标识符黑名单（tokenizer / truncation / selector / LLM / SQL 原语）；
    * 无 production selector 模块（文件存在性）；
    * 自身审计（本文件离线）。

纯离线：DB = 0 · Network = 0 · LLM = 0 · Tokenizer = 0。
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_selection_architecture.py"

_BUILDER = "backend/app/services/conversation_context_builder.py"
_CONVERSATION_SERVICE = "backend/app/services/conversation_service.py"
_REPOSITORY = "backend/app/db/conversation_repository.py"
_APPLICATION_SERVICE = "backend/app/services/chat_application_service.py"

#: tokenizer / 基础设施禁列表（各审计对象通用）。
_TOOLING_FORBIDDEN: tuple[str, ...] = (
    "tiktoken",
    "transformers",
    "tokenizers",
    "sentencepiece",
    "openai",
    "deepseek",
    "siliconflow",
    "httpx",
    "requests",
    "redis",
    "celery",
    "kafka",
)

#: builder 禁列表（DB / LLM / RAG / Tool）。
_BUILDER_FORBIDDEN: tuple[str, ...] = _TOOLING_FORBIDDEN + (
    "sqlalchemy",
    "psycopg",
    "backend.app.db",
    "backend.app.llm",
    "backend.app.rag",
    "backend.app.tools",
    "backend.app.embedding",
    "backend.app.reranker",
    "backend.app.services.conversation_service",
)

#: ConversationService 禁列表（LLM / tokenizer / RAG / Tool）。
_CONVERSATION_SERVICE_FORBIDDEN: tuple[str, ...] = _TOOLING_FORBIDDEN + (
    "sqlalchemy",
    "psycopg",
    "backend.app.llm",
    "backend.app.rag",
    "backend.app.tools",
    "backend.app.embedding",
    "backend.app.reranker",
    "backend.app.services.ai_orchestrator_service",
    "backend.app.services.chat_application_service",
)

#: Repository 禁列表（LLM / Context Builder / AI）。
_REPOSITORY_FORBIDDEN: tuple[str, ...] = _TOOLING_FORBIDDEN + (
    "backend.app.llm",
    "backend.app.rag",
    "backend.app.tools",
    "backend.app.services.ai_orchestrator_service",
    "backend.app.services.ai_router_service",
    "backend.app.services.conversation_context_builder",
    "backend.app.services.chat_application_service",
    "backend.app.prompts",
)

#: 禁止标识符（tokenizer / truncation / selector / LLM）。
_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "tiktoken",
    "max_tokens",
    "max_chars",
    "token_budget",
    "truncate",
    "ContextSelector",
    "context_selector",
    "LLMClient",
)

_BUILDER_ALLOWED_IMPORT_PREFIXES: tuple[str, ...] = (
    "__future__",
    "collections.abc",
    "typing",
    "dataclasses",
)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def _parse(relative: str) -> ast.Module:
    return ast.parse(_source(relative))


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


def _assert_no_forbidden_imports(relative: str, prefixes: tuple[str, ...]) -> None:
    modules = _module_imports(relative)
    assert modules, f"AST 未解析到 import（审计失效）: {relative}"
    for module in modules:
        for prefix in prefixes:
            assert not module.startswith(prefix), f"{relative}: {module}"


# ============================================================
# 1. Builder（无 tokenizer / DB / LLM / RAG / Tool）
# ============================================================


class TestBuilderBoundary:
    def test_1a_no_forbidden_imports(self) -> None:
        _assert_no_forbidden_imports(_BUILDER, _BUILDER_FORBIDDEN)

    def test_1b_imports_whitelisted(self) -> None:
        modules = _module_imports(_BUILDER)
        for module in modules:
            assert any(
                module.startswith(prefix)
                for prefix in _BUILDER_ALLOWED_IMPORT_PREFIXES
            ), module

    def test_1c_no_selection_or_budget_identifiers(self) -> None:
        identifiers = _identifiers(_BUILDER)
        for forbidden in _FORBIDDEN_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden

    def test_1d_builder_module_has_no_services(self) -> None:
        from backend.app.services import conversation_context_builder as module

        for attribute in (
            "ContextSelector",
            "ConversationService",
            "tiktoken",
            "max_tokens",
        ):
            assert not hasattr(module, attribute), attribute


# ============================================================
# 2. ConversationService（无 LLM / tokenizer / RAG / Tool）
# ============================================================


class TestConversationServiceBoundary:
    def test_2a_no_forbidden_imports(self) -> None:
        _assert_no_forbidden_imports(
            _CONVERSATION_SERVICE, _CONVERSATION_SERVICE_FORBIDDEN
        )

    def test_2b_no_selector_or_tokenizer_identifiers(self) -> None:
        identifiers = _identifiers(_CONVERSATION_SERVICE)
        for forbidden in _FORBIDDEN_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden

    def test_2c_service_dependency_direction(self) -> None:
        """Service 只依赖 Repository（持久化），不依赖 Application / Builder。"""
        modules = _module_imports(_CONVERSATION_SERVICE)
        assert "backend.app.db.conversation_repository" in modules
        for forbidden in (
            "backend.app.services.chat_application_service",
            "backend.app.services.conversation_context_builder",
        ):
            assert forbidden not in modules, forbidden


# ============================================================
# 3. Repository（无 LLM / Context Builder / AI）
# ============================================================


class TestRepositoryBoundary:
    def test_3a_no_forbidden_imports(self) -> None:
        _assert_no_forbidden_imports(_REPOSITORY, _REPOSITORY_FORBIDDEN)

    def test_3b_no_selector_or_budget_identifiers(self) -> None:
        identifiers = _identifiers(_REPOSITORY)
        for forbidden in _FORBIDDEN_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden

    def test_3c_repository_only_depends_on_db_layer(self) -> None:
        modules = _module_imports(_REPOSITORY)
        for module in modules:
            assert module.startswith(
                (
                    "__future__",
                    "dataclasses",
                    "datetime",
                    "typing",
                    "sqlalchemy",
                    "backend.app.db",
                )
            ) or module in {"collections.abc"}, module


# ============================================================
# 4. ChatApplicationService（允许依赖 + 当前无 Selector）
# ============================================================


class TestChatApplicationServiceBoundary:
    def test_4a_allowed_dependencies_only(self) -> None:
        modules = _module_imports(_APPLICATION_SERVICE)
        allowed = (
            "__future__",
            "logging",
            "collections.abc",
            "typing",
            "backend.app.services.conversation_service",
            "backend.app.services.conversation_context_builder",
            "backend.app.services.ai_orchestrator_service",
            "backend.app.services.project_orchestrator_factory",
        )
        for module in modules:
            assert any(module.startswith(prefix) for prefix in allowed), module

    def test_4b_no_tokenizer_or_truncation_identifiers(self) -> None:
        identifiers = _identifiers(_APPLICATION_SERVICE)
        for forbidden in (
            "tiktoken",
            "max_tokens",
            "max_chars",
            "token_budget",
            "LLMClient",
            "ConversationRepository",
            "Session",
        ):
            assert forbidden not in identifiers, forbidden

    def test_4c_production_selector_not_implemented_yet(self) -> None:
        """本 Step 不要求 production Selector 存在；若不存在，则契约成立。"""
        identifiers = _identifiers(_APPLICATION_SERVICE)
        assert "ContextSelector" not in identifiers
        assert "context_selector" not in identifiers

        candidate = (
            _REPO_ROOT / "backend/app/services/conversation_context_selector.py"
        )
        assert not candidate.exists()


# ============================================================
# 5. 自身审计
# ============================================================


class TestSelfAudit:
    def test_5a_self_import_is_offline(self) -> None:
        modules = _module_imports(_SELF)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for forbidden in (
                "sqlalchemy",
                "psycopg",
                "httpx",
                "tiktoken",
                "openai",
                "backend.app.db",
            ):
                assert not module.startswith(forbidden), module

    def test_5b_audit_is_static_only(self) -> None:
        identifiers = _identifiers(_SELF)
        for forbidden in ("TestClient", "environ", "getenv"):
            assert forbidden not in identifiers, forbidden


__all__ = [
    "TestBuilderBoundary",
    "TestConversationServiceBoundary",
    "TestRepositoryBoundary",
    "TestChatApplicationServiceBoundary",
    "TestSelfAudit",
]
