"""History Selection Strategy 架构审计（Phase 4.1 Step 20）—— AST 六文件边界。

审计对象（**本 Step 全部零修改**）：

```text
backend/app/db/models/conversation.py
backend/app/db/models/conversation_turn.py
backend/app/db/conversation_repository.py
backend/app/services/conversation_service.py
backend/app/services/conversation_context_builder.py
backend/app/services/chat_application_service.py
```

确认未新增（production）：

```text
ContextSelector / SelectionPolicy / Budget / Tokenizer
max_turns / max_chars / max_tokens（不得进入 ORM / DTO / 服务签名）
```

注意：**docstring / comments 中的设计文字不算 executable dependency**
（全部断言基于 AST 节点，不做全文扫描）。

纯离线：DB = 0 · Network = 0 · LLM = 0 · Tokenizer = 0。
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_selection_strategy_architecture.py"

_CONVERSATION_ORM = "backend/app/db/models/conversation.py"
_TURN_ORM = "backend/app/db/models/conversation_turn.py"
_REPOSITORY = "backend/app/db/conversation_repository.py"
_CONVERSATION_SERVICE = "backend/app/services/conversation_service.py"
_BUILDER = "backend/app/services/conversation_context_builder.py"
_APPLICATION_SERVICE = "backend/app/services/chat_application_service.py"

_AUDITED_FILES: tuple[str, ...] = (
    _CONVERSATION_ORM,
    _TURN_ORM,
    _REPOSITORY,
    _CONVERSATION_SERVICE,
    _BUILDER,
    _APPLICATION_SERVICE,
)

_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "ContextSelector",
    "context_selector",
    "SelectionPolicy",
    "selection_policy",
    "Budget",
    "budget",
    "context_budget",
    "max_turns",
    "max_chars",
    "max_tokens",
    "token_budget",
    "tokenizer",
    "tiktoken",
)

_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "tiktoken",
    "transformers",
    "tokenizers",
    "sentencepiece",
    "openai",
    "deepseek",
    "siliconflow",
    "backend.app.llm",
    "backend.app.rag",
    "backend.app.tools",
)

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
    }
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

_DEFERRED_PRODUCTION_MODULES: tuple[str, ...] = (
    "conversation_context_selector.py",
    "conversation_selection_policy.py",
    "conversation_context_budget.py",
    "conversation_context_tokenizer.py",
    "conversation_summarizer.py",
    "conversation_summary.py",
    "conversation_memory.py",
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


def _call_parameter_names(relative: str, function_name: str) -> set[str]:
    """函数（或方法）级：提取所有 def 形参名（含 keyword-only）。"""
    params: set[str] = set()
    for node in ast.walk(_parse(relative)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != function_name:
            continue
        arguments = node.args
        for group in (
            arguments.posonlyargs,
            arguments.args,
            arguments.kwonlyargs,
        ):
            params |= {arg.arg for arg in group}
    return params


# ============================================================
# 1. ORM（字段冻结 + 无 selector / policy / budget / tokenizer）
# ============================================================


class TestOrmBoundary:
    def test_1a_conversation_orm_fields_frozen(self) -> None:
        assert _mapped_field_names(_CONVERSATION_ORM) == set(
            _FROZEN_CONVERSATION_FIELDS
        )

    def test_1b_turn_orm_fields_frozen(self) -> None:
        assert _mapped_field_names(_TURN_ORM) == set(_FROZEN_TURN_FIELDS)

    def test_1c_no_forbidden_identifiers_in_orm(self) -> None:
        for relative in (_CONVERSATION_ORM, _TURN_ORM):
            identifiers = _identifiers(relative)
            for forbidden in _FORBIDDEN_IDENTIFIERS:
                assert forbidden not in identifiers, f"{relative}:{forbidden}"

    def test_1d_orm_module_constants_frozen(self) -> None:
        """模块级 AnnAssign 目标名（常量 + 字段）不含预算 / 选择概念。"""
        for relative in (_CONVERSATION_ORM, _TURN_ORM):
            targets: set[str] = set()
            for node in getattr(_parse(relative), "body", []):
                if isinstance(node, ast.AnnAssign) and isinstance(
                    node.target, ast.Name
                ):
                    targets.add(node.target.id)
            for forbidden in _FORBIDDEN_IDENTIFIERS:
                assert forbidden not in targets, f"{relative}:{forbidden}"


# ============================================================
# 2. Repository / ConversationService / Builder
# ============================================================


class TestServiceLayerBoundary:
    def test_2a_no_forbidden_identifiers(self) -> None:
        for relative in (_REPOSITORY, _CONVERSATION_SERVICE, _BUILDER):
            identifiers = _identifiers(relative)
            for forbidden in _FORBIDDEN_IDENTIFIERS:
                assert forbidden not in identifiers, f"{relative}:{forbidden}"

    def test_2b_no_tokenizer_or_ai_imports(self) -> None:
        for relative in (_REPOSITORY, _CONVERSATION_SERVICE, _BUILDER):
            modules = _module_imports(relative)
            assert modules, f"AST 未解析到 import: {relative}"
            for module in modules:
                for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                    assert not module.startswith(prefix), f"{relative}:{module}"

    def test_2c_method_signatures_have_no_budget_or_policy(self) -> None:
        service = _CONVERSATION_SERVICE
        for function_name in (
            "create_conversation",
            "get_conversation",
            "archive_conversation",
            "append_turn",
            "list_turns",
        ):
            params = _call_parameter_names(service, function_name)
            for forbidden in ("budget", "policy", "max_turns", "max_chars"):
                assert forbidden not in params, f"{function_name}:{forbidden}"

        # 模块级函数 + 类方法同名 → 合并形参；去掉 self 后必须恰为 {turns}。
        builder_params = _call_parameter_names(_BUILDER, "build_context")
        assert builder_params - {"self"} == {"turns"}


# ============================================================
# 3. ChatApplicationService
# ============================================================


class TestApplicationServiceBoundary:
    def test_3a_allowed_dependencies_only(self) -> None:
        modules = _module_imports(_APPLICATION_SERVICE)
        for module in modules:
            assert any(
                module.startswith(prefix)
                for prefix in _APPLICATION_SERVICE_ALLOWED_PREFIXES
            ), module

    def test_3b_no_forbidden_identifiers(self) -> None:
        identifiers = _identifiers(_APPLICATION_SERVICE)
        for forbidden in _FORBIDDEN_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden

    def test_3c_execute_message_signature_unchanged(self) -> None:
        params = _call_parameter_names(_APPLICATION_SERVICE, "execute_message")
        assert params - {"self"} == {"conversation_id", "content"}


# ============================================================
# 4. Deferred production 模块缺席
# ============================================================


class TestDeferredModulesAbsent:
    def test_4a_no_deferred_production_modules(self) -> None:
        for name in _DEFERRED_PRODUCTION_MODULES:
            assert not (_REPO_ROOT / "backend/app/services" / name).exists(), name

    def test_4b_no_budget_module_in_db_layer(self) -> None:
        for name in ("conversation_budget.py", "conversation_policy.py"):
            assert not (_REPO_ROOT / "backend/app/db" / name).exists(), name


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
    "TestServiceLayerBoundary",
    "TestApplicationServiceBoundary",
    "TestDeferredModulesAbsent",
    "TestSelfAudit",
]
