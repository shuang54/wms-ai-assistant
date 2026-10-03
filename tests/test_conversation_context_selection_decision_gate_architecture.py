"""Decision Gate 架构审计（Phase 4.1 Step 22）—— AST / 模块存在性边界。

审计目标：

    1. backend/app/services · db · dto 中**不得出现** Conversation 域的
       Selector / Policy / Budget / Tokenizer / Truncation / Summarizer /
       Memory / DecisionGate 模块；
    2. 全局（三目录）不得出现 tokenizer / truncation / summarizer / budget /
       policy 命名模块；
    3. Conversation 链路六文件（ORM×2 / Repository / Service / Builder /
       Application Service）AST 无禁区标识符、无 tokenizer 依赖、
       Builder 签名仍为单参数（不引入选择 / 预算）。

既有文件说明（**非本决策域，不得误判**）：

```text
services/relevant_table_selector.py                     （Text-to-SQL 相关表选择器；既有）
services/in_memory_rag_execution_collector.py           （观测 collector；既有）
services/in_memory_tool_execution_collector.py          （观测 collector；既有）
services/text_to_sql_prompt_v2_promotion_gate_service.py（prompt 评估 gate；既有）
```

以上模块与 Conversation History Selection 无关；本审计只针对
"Conversation 域新增决策/选择/预算实现"（全部应为 absent）。

纯离线：DB = 0 · Network = 0 · LLM = 0 · Tokenizer = 0。
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_selection_decision_gate_architecture.py"

_ORM = "backend/app/db/models/conversation.py"
_TURN_ORM = "backend/app/db/models/conversation_turn.py"
_REPOSITORY = "backend/app/db/conversation_repository.py"
_CONVERSATION_SERVICE = "backend/app/services/conversation_service.py"
_BUILDER = "backend/app/services/conversation_context_builder.py"
_APPLICATION_SERVICE = "backend/app/services/chat_application_service.py"

_AUDITED_DIRS: tuple[str, ...] = (
    "backend/app/services",
    "backend/app/db",
    "backend/app/dto",
)

#: Conversation 域禁止的模块关键词（与 "conversation" 组合才判定；避免误判既有模块）。
_CONVERSATION_SCOPED_FORBIDDEN: tuple[str, ...] = (
    "selector",
    "policy",
    "budget",
    "tokenizer",
    "truncation",
    "summarizer",
    "memory",
    "gate",
)

#: 全局禁止的模块关键词（三目录中当前完全不存在；未来需单独决定）。
_GLOBALLY_FORBIDDEN_MODULE_KEYWORDS: tuple[str, ...] = (
    "tokenizer",
    "truncation",
    "summarizer",
    "budget",
    "policy",
)

_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "ContextSelector",
    "context_selector",
    "SelectionPolicy",
    "selection_policy",
    "context_budget",
    "max_turns",
    "max_chars",
    "max_tokens",
    "token_budget",
    "tokenizer",
    "tiktoken",
    "truncate",
    "summarizer",
)

_TOKENIZER_IMPORT_PREFIXES: tuple[str, ...] = (
    "tiktoken",
    "transformers",
    "tokenizers",
    "sentencepiece",
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


def _modules_matching(*keywords: str) -> list[str]:
    matches: list[str] = []
    for directory in _AUDITED_DIRS:
        for path in (_REPO_ROOT / directory).rglob("*.py"):
            name = path.name
            if all(keyword in name for keyword in keywords):
                matches.append(
                    str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
                )
    return sorted(matches)


def _call_parameter_names(relative: str, function_name: str) -> set[str]:
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
# 1. 模块存在性（Conversation 域 + 全局）
# ============================================================


class TestModuleExistenceBoundary:
    def test_1a_no_conversation_scoped_decision_modules(self) -> None:
        for keyword in _CONVERSATION_SCOPED_FORBIDDEN:
            offenders = _modules_matching("conversation", keyword)
            assert offenders == [], f"{keyword}: {offenders}"

    def test_1b_no_global_tokenizer_truncation_summarizer_budget_policy(self) -> None:
        for keyword in _GLOBALLY_FORBIDDEN_MODULE_KEYWORDS:
            offenders = _modules_matching(keyword)
            assert offenders == [], f"{keyword}: {offenders}"

    def test_1c_audit_directories_are_not_empty(self) -> None:
        for directory in _AUDITED_DIRS:
            assert list((_REPO_ROOT / directory).rglob("*.py")), directory

    def test_1d_selector_modules_are_only_preexisting_unrelated(self) -> None:
        """三目录中仅允许既有的非 Conversation selector（relevant_table_selector）。"""
        offenders = _modules_matching("selector")
        assert offenders == ["backend/app/services/relevant_table_selector.py"], (
            offenders
        )


# ============================================================
# 2. Conversation 链路六文件（AST）
# ============================================================


class TestConversationChainBoundary:
    def test_2a_no_forbidden_identifiers(self) -> None:
        for relative in (
            _ORM,
            _TURN_ORM,
            _REPOSITORY,
            _CONVERSATION_SERVICE,
            _BUILDER,
            _APPLICATION_SERVICE,
        ):
            identifiers = _identifiers(relative)
            for forbidden in _FORBIDDEN_IDENTIFIERS:
                assert forbidden not in identifiers, f"{relative}:{forbidden}"

    def test_2b_no_tokenizer_imports(self) -> None:
        for relative in (
            _BUILDER,
            _CONVERSATION_SERVICE,
            _REPOSITORY,
            _APPLICATION_SERVICE,
        ):
            modules = _module_imports(relative)
            assert modules, f"AST 未解析到 import: {relative}"
            for module in modules:
                for prefix in _TOKENIZER_IMPORT_PREFIXES:
                    assert not module.startswith(prefix), f"{relative}:{module}"

    def test_2c_builder_signature_still_single_parameter(self) -> None:
        params = _call_parameter_names(_BUILDER, "build_context")
        assert params - {"self"} == {"turns"}

    def test_2d_application_service_execute_signature_unchanged(self) -> None:
        params = _call_parameter_names(_APPLICATION_SERVICE, "execute_message")
        assert params - {"self"} == {"conversation_id", "content"}

    def test_2e_conversation_service_methods_have_no_policy(self) -> None:
        for function_name in (
            "create_conversation",
            "get_conversation",
            "archive_conversation",
            "append_turn",
            "list_turns",
        ):
            params = _call_parameter_names(_CONVERSATION_SERVICE, function_name)
            for forbidden in ("policy", "budget", "max_turns", "strategy", "gate"):
                assert forbidden not in params, f"{function_name}:{forbidden}"


# ============================================================
# 3. 自身审计
# ============================================================


class TestSelfAudit:
    def test_3a_self_import_is_offline(self) -> None:
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

    def test_3b_audit_is_static_only(self) -> None:
        identifiers = _identifiers(_SELF)
        for forbidden in ("TestClient", "environ", "getenv"):
            assert forbidden not in identifiers, forbidden


__all__ = [
    "TestModuleExistenceBoundary",
    "TestConversationChainBoundary",
    "TestSelfAudit",
]
