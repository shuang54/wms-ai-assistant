"""Selection Policy / Budget Ownership 架构审计（Phase 4.1 Step 18）—— AST 边界。

审计对象（**本 Step 全部零修改**）：

    1. ``backend/app/db/models/conversation.py``（Conversation ORM）
       —— 不得出现 context_budget / selection_policy / max_tokens / max_turns
    2. ``backend/app/db/conversation_repository.py``
       —— 不得出现 LLM / tokenizer / ContextBuilder / ContextSelector
    3. ``backend/app/services/conversation_service.py``
       —— 不得出现 tokenizer / LLM / RAG / Tool / ContextSelector
    4. ``backend/app/services/chat_application_service.py``
       —— 未来 policy composition 边界；当前 production selector / policy **缺席**

检查项（全部 AST，不做全文扫描）；纯离线：DB = 0 · Network = 0 · LLM = 0。
"""
from __future__ import annotations

import ast
from pathlib import Path

from backend.app.dto.conversation_api import ConversationMessageRequest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_policy_architecture.py"

_CONVERSATION_ORM = "backend/app/db/models/conversation.py"
_TURN_ORM = "backend/app/db/models/conversation_turn.py"
_REPOSITORY = "backend/app/db/conversation_repository.py"
_CONVERSATION_SERVICE = "backend/app/services/conversation_service.py"
_APPLICATION_SERVICE = "backend/app/services/chat_application_service.py"

_FROZEN_CONVERSATION_FIELDS: frozenset[str] = frozenset(
    {"conversation_id", "project_id", "created_at", "updated_at", "status"}
)
_FROZEN_TURN_FIELDS: frozenset[str] = frozenset(
    {
        "turn_id",
        "conversation_id",
        "role",
        "content",
        "assistant_request_id",
        "created_at",
        # Phase 4.2 Step 6：消息幂等键（只写 USER Turn）
        "idempotency_key",
    }
)

_BUDGET_FIELD_NAMES: tuple[str, ...] = (
    "context_budget",
    "selection_policy",
    "max_tokens",
    "max_turns",
    "max_chars",
    "token_budget",
    "budget_units",
)

_TOKENIZER_AND_LLM_PREFIXES: tuple[str, ...] = (
    "tiktoken",
    "transformers",
    "tokenizers",
    "sentencepiece",
    "openai",
    "deepseek",
    "siliconflow",
    "httpx",
    "redis",
    "celery",
    "backend.app.llm",
    "backend.app.rag",
    "backend.app.tools",
)

_POLICY_IDENTIFIERS: tuple[str, ...] = (
    "SelectionPolicy",
    "selection_policy",
    "ContextSelector",
    "context_selector",
    "context_budget",
    "max_turns",
    "max_tokens",
    "token_budget",
)

_APPLICATION_SERVICE_ALLOWED_PREFIXES: tuple[str, ...] = (
    "__future__",
    "logging",
    "collections.abc",
    # Phase 4.2 Step 6：MessageReplay（frozen DTO）
    "dataclasses",
    "typing",
    # Phase 4.2 Step 7E：Selection 层（Step 7C 契约实现）
    "backend.app.services.conversation_context_selection_service",
    "backend.app.services.conversation_service",
    "backend.app.services.conversation_context_builder",
    "backend.app.services.ai_orchestrator_service",
    "backend.app.services.project_orchestrator_factory",
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


def _module_level_string_targets(relative: str) -> set[str]:
    """模块级 Final 常量的名字（用于审计 ORM 是否新增预算常量）。"""
    names: set[str] = set()
    for node in getattr(_parse(relative), "body", []):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


# ============================================================
# 1. Conversation ORM（无 budget / policy 字段）
# ============================================================


class TestConversationOrmBoundary:
    def test_1a_conversation_fields_are_frozen(self) -> None:
        fields = _mapped_field_names(_CONVERSATION_ORM)
        assert fields == set(_FROZEN_CONVERSATION_FIELDS)

    def test_1b_conversation_has_no_budget_fields(self) -> None:
        fields = _mapped_field_names(_CONVERSATION_ORM)
        for forbidden in _BUDGET_FIELD_NAMES:
            assert forbidden not in fields, forbidden

    def test_1c_turn_fields_are_frozen(self) -> None:
        fields = _mapped_field_names(_TURN_ORM)
        assert fields == set(_FROZEN_TURN_FIELDS)
        for forbidden in _BUDGET_FIELD_NAMES:
            assert forbidden not in fields, forbidden

    def test_1d_orm_modules_have_no_policy_identifiers(self) -> None:
        for relative in (_CONVERSATION_ORM, _TURN_ORM):
            identifiers = _identifiers(relative)
            for forbidden in _POLICY_IDENTIFIERS:
                assert forbidden not in identifiers, f"{relative}:{forbidden}"

    def test_1e_orm_module_constants_have_no_budget_names(self) -> None:
        for relative in (_CONVERSATION_ORM, _TURN_ORM):
            constants = _module_level_string_targets(relative)
            for forbidden in _BUDGET_FIELD_NAMES:
                assert forbidden not in constants, f"{relative}:{forbidden}"


# ============================================================
# 2. ConversationRepository（无 LLM / tokenizer / Builder / Selector）
# ============================================================


class TestRepositoryBoundary:
    def test_2a_no_llm_or_tokenizer_imports(self) -> None:
        modules = _module_imports(_REPOSITORY)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in _TOKENIZER_AND_LLM_PREFIXES:
                assert not module.startswith(prefix), module

    def test_2b_no_builder_or_selector_imports(self) -> None:
        modules = _module_imports(_REPOSITORY)
        for forbidden in (
            "backend.app.services.conversation_context_builder",
            "backend.app.services.chat_application_service",
        ):
            assert forbidden not in modules, forbidden

    def test_2c_no_policy_identifiers(self) -> None:
        identifiers = _identifiers(_REPOSITORY)
        for forbidden in _POLICY_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden
        for forbidden in ("ContextBuilder", "ContextSelector"):
            assert forbidden not in identifiers, forbidden


# ============================================================
# 3. ConversationService（无 tokenizer / LLM / RAG / Tool / Selector）
# ============================================================


class TestConversationServiceBoundary:
    def test_3a_no_forbidden_imports(self) -> None:
        modules = _module_imports(_CONVERSATION_SERVICE)
        for module in modules:
            for prefix in _TOKENIZER_AND_LLM_PREFIXES:
                assert not module.startswith(prefix), module
            for forbidden in (
                "backend.app.services.conversation_context_builder",
                "backend.app.services.chat_application_service",
            ):
                assert not module.startswith(forbidden), module

    def test_3b_no_policy_identifiers(self) -> None:
        identifiers = _identifiers(_CONVERSATION_SERVICE)
        for forbidden in _POLICY_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden

    def test_3c_service_methods_have_no_policy_parameters(self) -> None:
        import inspect

        from backend.app.services.conversation_service import ConversationService

        for name in (
            "create_conversation",
            "get_conversation",
            "archive_conversation",
            "append_turn",
            "list_turns",
        ):
            params = inspect.signature(getattr(ConversationService, name)).parameters
            for forbidden in ("policy", "budget", "max_turns", "max_chars"):
                assert forbidden not in params, f"{name}:{forbidden}"


# ============================================================
# 4. ChatApplicationService（未来 policy composition 边界）
# ============================================================


class TestApplicationServiceBoundary:
    def test_4a_allowed_dependencies_only(self) -> None:
        modules = _module_imports(_APPLICATION_SERVICE)
        for module in modules:
            assert any(
                module.startswith(prefix)
                for prefix in _APPLICATION_SERVICE_ALLOWED_PREFIXES
            ), module

    def test_4b_no_policy_or_selector_identifiers_yet(self) -> None:
        identifiers = _identifiers(_APPLICATION_SERVICE)
        for forbidden in _POLICY_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden

    def test_4c_no_production_policy_or_selector_modules(self) -> None:
        for name in (
            "conversation_context_policy.py",
            "conversation_selection_policy.py",
            "conversation_context_selector.py",
        ):
            assert not (_REPO_ROOT / "backend/app/services" / name).exists(), name

    def test_4d_message_api_has_no_policy_field(self) -> None:
        assert list(ConversationMessageRequest.model_fields) == ["content"]
        for forbidden in _BUDGET_FIELD_NAMES + ("policy", "strategy"):
            assert forbidden not in ConversationMessageRequest.model_fields, forbidden


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
    "TestConversationOrmBoundary",
    "TestRepositoryBoundary",
    "TestConversationServiceBoundary",
    "TestApplicationServiceBoundary",
    "TestSelfAudit",
]
