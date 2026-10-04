"""History Budget 架构审计（Phase 4.1 Step 19）—— AST 依赖边界。

审计对象（**本 Step 全部零修改**）：

    1. ``backend/app/db/models/conversation.py``（Conversation ORM）
       —— 不得出现 context_budget / max_turns / max_chars / max_tokens
    2. ``backend/app/db/conversation_repository.py``
       —— 不得出现 budget / tokenizer / ContextSelector
    3. ``backend/app/services/conversation_service.py``
       —— 不得出现 budget / tokenizer / LLM / ContextSelector
    4. ``backend/app/services/conversation_context_builder.py``
       —— 不得出现 budget / tokenizer / ContextSelector
    5. ``backend/app/services/chat_application_service.py``
       —— 未来 budget composition 边界；当前 production budget / selector **缺席**

检查项（全部 AST，不做全文扫描）；纯离线：DB = 0 · Network = 0 · LLM = 0 · Tokenizer = 0。
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_budget_architecture.py"

_CONVERSATION_ORM = "backend/app/db/models/conversation.py"
_TURN_ORM = "backend/app/db/models/conversation_turn.py"
_REPOSITORY = "backend/app/db/conversation_repository.py"
_CONVERSATION_SERVICE = "backend/app/services/conversation_service.py"
_BUILDER = "backend/app/services/conversation_context_builder.py"
_APPLICATION_SERVICE = "backend/app/services/chat_application_service.py"

_BUDGET_IDENTIFIERS: tuple[str, ...] = (
    "budget",
    "context_budget",
    "max_turns",
    "max_chars",
    "max_tokens",
    "token_budget",
    "limit_turns",
    "limit_chars",
)

_TOKENIZER_PREFIXES: tuple[str, ...] = (
    "tiktoken",
    "transformers",
    "tokenizers",
    "sentencepiece",
)

_SELECTOR_IDENTIFIERS: tuple[str, ...] = (
    "ContextSelector",
    "context_selector",
    "SelectionPolicy",
    "selection_policy",
)

_LLM_IMPORT_PREFIXES: tuple[str, ...] = (
    "openai",
    "deepseek",
    "siliconflow",
    "httpx",
    "backend.app.llm",
    "backend.app.rag",
    "backend.app.tools",
    "backend.app.embedding",
    "backend.app.reranker",
)

_APPLICATION_SERVICE_ALLOWED_PREFIXES: tuple[str, ...] = (
    "__future__",
    "logging",
    "collections.abc",
    "typing",
    "backend.app.services.conversation_service",
    "backend.app.services.conversation_context_builder",
    "backend.app.services.ai_orchestrator_service",
    "backend.app.services.project_orchestrator_factory",
)

_PRODUCTION_BUDGET_MODULES: tuple[str, ...] = (
    "conversation_context_budget.py",
    "conversation_context_policy.py",
    "conversation_selection_policy.py",
    "conversation_context_selector.py",
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


def _mapped_field_names(relative: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_parse(relative)):
        if not isinstance(node, ast.AnnAssign) or not isinstance(
            node.target, ast.Name
        ):
            continue
        annotation = node.annotation
        if (
            isinstance(annotation, ast.Subscript)
            and getattr(annotation.value, "id", None) == "Mapped"
        ):
            names.add(node.target.id)
    return names


# ============================================================
# 1. Conversation ORM / Turn ORM
# ============================================================


class TestOrmBoundary:
    def test_1a_conversation_has_no_budget_fields_or_identifiers(self) -> None:
        fields = _mapped_field_names(_CONVERSATION_ORM)
        assert fields == {
            "conversation_id",
            "project_id",
            "created_at",
            "updated_at",
            "status",
        }
        identifiers = _identifiers(_CONVERSATION_ORM)
        for forbidden in _BUDGET_IDENTIFIERS:
            assert forbidden not in fields, forbidden
            assert forbidden not in identifiers, f"identifier:{forbidden}"

    def test_1b_turn_orm_has_no_budget_fields(self) -> None:
        fields = _mapped_field_names(_TURN_ORM)
        assert fields == {
            "turn_id",
            "conversation_id",
            "role",
            "content",
            "assistant_request_id",
            "created_at",
        }
        for forbidden in _BUDGET_IDENTIFIERS:
            assert forbidden not in fields, forbidden


# ============================================================
# 2. Repository / Service / Builder
# ============================================================


class TestPersistenceAndBuilderBoundary:
    def test_2a_repository_has_no_budget_tokenizer_selector(self) -> None:
        identifiers = _identifiers(_REPOSITORY)
        for forbidden in _BUDGET_IDENTIFIERS + _SELECTOR_IDENTIFIERS + (
            "tokenizer",
            "tiktoken",
        ):
            assert forbidden not in identifiers, forbidden

        modules = _module_imports(_REPOSITORY)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in _TOKENIZER_PREFIXES + _LLM_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_2b_conversation_service_has_no_budget_tokenizer_llm_selector(self) -> None:
        identifiers = _identifiers(_CONVERSATION_SERVICE)
        for forbidden in _BUDGET_IDENTIFIERS + _SELECTOR_IDENTIFIERS + (
            "tokenizer",
            "LLMClient",
        ):
            assert forbidden not in identifiers, forbidden

        modules = _module_imports(_CONVERSATION_SERVICE)
        for module in modules:
            for prefix in _TOKENIZER_PREFIXES + _LLM_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_2c_builder_has_no_budget_tokenizer_selector(self) -> None:
        identifiers = _identifiers(_BUILDER)
        for forbidden in _BUDGET_IDENTIFIERS + _SELECTOR_IDENTIFIERS + (
            "tokenizer",
            "tiktoken",
        ):
            assert forbidden not in identifiers, forbidden

        modules = _module_imports(_BUILDER)
        for module in modules:
            for prefix in _TOKENIZER_PREFIXES + _LLM_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module


# ============================================================
# 3. ChatApplicationService（未来 budget composition 边界）
# ============================================================


class TestApplicationServiceBoundary:
    def test_3a_allowed_dependencies_only(self) -> None:
        modules = _module_imports(_APPLICATION_SERVICE)
        for module in modules:
            assert any(
                module.startswith(prefix)
                for prefix in _APPLICATION_SERVICE_ALLOWED_PREFIXES
            ), module

    def test_3b_no_budget_or_selector_identifiers_yet(self) -> None:
        identifiers = _identifiers(_APPLICATION_SERVICE)
        for forbidden in _BUDGET_IDENTIFIERS + _SELECTOR_IDENTIFIERS + (
            "tokenizer",
        ):
            assert forbidden not in identifiers, forbidden

    def test_3c_production_budget_and_selector_are_absent(self) -> None:
        for name in _PRODUCTION_BUDGET_MODULES:
            assert not (_REPO_ROOT / "backend/app/services" / name).exists(), name


# ============================================================
# 4. 全链路 tokenizer 缺席
# ============================================================


class TestTokenizerAbsenceAcrossChain:
    def test_4a_no_tokenizer_imports_in_conversation_chain(self) -> None:
        for relative in (
            _CONVERSATION_ORM,
            _TURN_ORM,
            _REPOSITORY,
            _CONVERSATION_SERVICE,
            _BUILDER,
            _APPLICATION_SERVICE,
        ):
            modules = _module_imports(relative)
            for module in modules:
                for prefix in _TOKENIZER_PREFIXES:
                    assert not module.startswith(prefix), f"{relative}:{module}"

    def test_4b_no_context_window_source_in_chain(self) -> None:
        for relative in (
            _BUILDER,
            _CONVERSATION_SERVICE,
            _APPLICATION_SERVICE,
        ):
            identifiers = _identifiers(relative)
            for forbidden in ("context_window", "model_context_window"):
                assert forbidden not in identifiers, f"{relative}:{forbidden}"


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
    "TestOrmBoundary",
    "TestPersistenceAndBuilderBoundary",
    "TestApplicationServiceBoundary",
    "TestTokenizerAbsenceAcrossChain",
    "TestSelfAudit",
]
