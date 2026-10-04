"""Conversation Context Builder 架构审计（Phase 4.1 Step 13）—— AST 依赖边界。

审计对象：``backend/app/services/conversation_context_builder.py``

检查项（全部基于 AST，不做全文字符串扫描）：

    1. 禁止 import：sqlalchemy / psycopg / backend.app.db /
       conversation_service / chat_application_service /
       ai_orchestrator_service / ai_router_service / rag_service /
       tool_chat_service / text_to_sql_service / backend.app.llm /
       backend.app.rag / backend.app.tools / backend.app.embedding /
       backend.app.reranker / backend.app.prompts / services.context_builder（RAG）
    2. import 白名单：__future__ / collections.abc / typing / dataclasses
    3. 禁止标识符：ConversationService / ConversationRepository / Session /
       sessionmaker / Engine / select / insert / update / AIOrchestratorService /
       LLMClient / RagService / ToolChatService / TextToSQLService /
       VectorSearchResult / ContextBuildResult
    4. 无 IO / 环境依赖：os / sys / pathlib / time / datetime / random /
       socket / logging
    5. 纯函数：build_context / build_messages 为同步模块级函数（单参数 turns）
    6. 只读取 role / content：getattr 字段名 ⊆ {"role", "content"}；
       模块 Attribute 访问 ⊆ {"format", "join"}（无 turn 字段属性访问）
    7. 无状态：模块级裸赋值 = 0；类未定义 __init__；无 async def
    8. 自身审计（本文件离线）

纯离线：DB = 0 · Network = 0 · LLM = 0（仅静态 AST）。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from backend.app.services import conversation_context_builder as builder_module

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_builder_architecture_audit.py"
_TARGET = "backend/app/services/conversation_context_builder.py"

_ALLOWED_IMPORT_PREFIXES: tuple[str, ...] = (
    "__future__",
    "collections.abc",
    "typing",
    "dataclasses",
)

_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "sqlalchemy",
    "psycopg",
    "backend.app.db",
    "backend.app.services.conversation_service",
    "backend.app.services.chat_application_service",
    "backend.app.services.ai_orchestrator_service",
    "backend.app.services.ai_router_service",
    "backend.app.services.rag_service",
    "backend.app.services.tool_chat_service",
    "backend.app.services.text_to_sql_service",
    "backend.app.services.context_builder",
    "backend.app.llm",
    "backend.app.rag",
    "backend.app.tools",
    "backend.app.embedding",
    "backend.app.reranker",
    "backend.app.prompts",
)

_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "ConversationService",
    "ConversationRepository",
    "Session",
    "sessionmaker",
    "Engine",
    "select",
    "insert",
    "update",
    "AIOrchestratorService",
    "LLMClient",
    "RagService",
    "ToolChatService",
    "TextToSQLService",
    "VectorSearchResult",
    "ContextBuildResult",
)

_FORBIDDEN_IO_MODULES: tuple[str, ...] = (
    "os",
    "sys",
    "pathlib",
    "time",
    "datetime",
    "random",
    "socket",
    "logging",
)

#: 允许的 Attribute 访问（全部是内置 str / list / type 操作；
#: turn 的任何字段都只能经 getattr(..., "role" | "content") 读取）。
_ALLOWED_ATTRIBUTE_NAMES: frozenset[str] = frozenset(
    {"format", "join", "append", "__name__"}
)
_ALLOWED_GETATTR_FIELDS: frozenset[str] = frozenset({"role", "content"})


def _parse(relative: str) -> ast.Module:
    return ast.parse(
        (_REPO_ROOT / relative).read_text(encoding="utf-8")
    )


def _module_imports(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _identifiers(tree: ast.Module) -> set[str]:
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


def _attribute_names(tree: ast.Module) -> set[str]:
    return {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


def _getattr_field_names(tree: ast.Module) -> set[str]:
    fields: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
        ):
            field = node.args[1]
            if isinstance(field, ast.Constant) and isinstance(field.value, str):
                fields.add(field.value)
    return fields


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
# 1. import 边界
# ============================================================


class TestImportBoundary:
    def test_1a_no_forbidden_imports(self) -> None:
        modules = _module_imports(_parse(_TARGET))
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_1b_all_imports_whitelisted(self) -> None:
        modules = _module_imports(_parse(_TARGET))
        for module in modules:
            assert any(
                module.startswith(prefix) for prefix in _ALLOWED_IMPORT_PREFIXES
            ), module

    def test_1c_no_io_or_environment_imports(self) -> None:
        modules = _module_imports(_parse(_TARGET))
        for module in modules:
            assert module not in _FORBIDDEN_IO_MODULES, module

    def test_1d_runtime_module_has_no_bound_services(self) -> None:
        for attribute in (
            "ConversationService",
            "ConversationRepository",
            "AIOrchestratorService",
            "RagService",
            "LLMClient",
            "Session",
            "ContextBuilder",
        ):
            assert not hasattr(builder_module, attribute), attribute


# ============================================================
# 2. 标识符边界
# ============================================================


class TestIdentifierBoundary:
    def test_2a_no_forbidden_identifiers(self) -> None:
        identifiers = _identifiers(_parse(_TARGET))
        for forbidden in _FORBIDDEN_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden

    def test_2b_only_role_and_content_are_read(self) -> None:
        """turn 字段访问只能经 getattr(..., "role" | "content")。"""
        tree = _parse(_TARGET)
        assert _getattr_field_names(tree) == {"role", "content"}

    def test_2c_no_turn_attribute_access(self) -> None:
        """模块 Attribute 访问仅 format / join（无 turn.turn_id 之类）。"""
        names = _attribute_names(_parse(_TARGET))
        assert names <= _ALLOWED_ATTRIBUTE_NAMES, names

    def test_2d_no_async_definitions(self) -> None:
        tree = _parse(_TARGET)
        for node in ast.walk(tree):
            assert not isinstance(node, ast.AsyncFunctionDef), node.name


# ============================================================
# 3. 纯函数 / 无状态
# ============================================================


class TestPurityBoundary:
    def test_3a_functions_are_module_level_sync(self) -> None:
        assert inspect.isfunction(builder_module.build_context)
        assert inspect.isfunction(builder_module.build_messages)
        assert not inspect.iscoroutinefunction(builder_module.build_context)
        assert not inspect.iscoroutinefunction(builder_module.build_messages)

    def test_3b_signature_is_single_turns_parameter(self) -> None:
        for func in (builder_module.build_context, builder_module.build_messages):
            params = list(inspect.signature(func).parameters)
            assert params == ["turns"], func.__name__

    def test_3c_no_module_level_mutable_state(self) -> None:
        assert _module_level_assign_targets(_parse(_TARGET)) == set()

    def test_3d_class_has_no_instance_state(self) -> None:
        cls = builder_module.ConversationContextBuilder
        assert "__init__" not in vars(cls), "Builder 类不应持有实例状态"
        public = {
            name
            for name in vars(cls)
            if not name.startswith("_") and callable(getattr(cls, name))
        }
        assert public == {"build_messages", "build_context"}

    def test_3e_no_print_or_open_calls(self) -> None:
        identifiers = _identifiers(_parse(_TARGET))
        for forbidden in ("open", "print", "input", "eval", "exec"):
            assert forbidden not in identifiers, forbidden


# ============================================================
# 4. 自身审计
# ============================================================


class TestSelfAudit:
    def test_4a_self_import_is_offline(self) -> None:
        modules = _module_imports(_parse(_SELF))
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            assert not module.startswith("sqlalchemy"), module
            assert not module.startswith("httpx"), module
            assert not module.startswith("psycopg"), module

    def test_4b_audit_is_static_only(self) -> None:
        identifiers = _identifiers(_parse(_SELF))
        for forbidden in ("TestClient", "environ", "getenv"):
            assert forbidden not in identifiers, forbidden


__all__ = [
    "TestImportBoundary",
    "TestIdentifierBoundary",
    "TestPurityBoundary",
    "TestSelfAudit",
]
