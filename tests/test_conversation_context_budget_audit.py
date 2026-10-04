"""Conversation Context Budget / Truncation Audit（Phase 4.1 Step 16 —— **Audit only**）。

本文件性质：

    * **不做实现**：不引入 token budget / truncation / summary / memory / tokenizer；
    * **纯离线**：DB = 0 · Network = 0 · LLM = 0（直接构造 ConversationTurnView）；
    * 长度口径 = **character length**（``len(context)``）——
      **禁止**由字符数推算 token 数（无 len/4、无比例换算；不同 tokenizer 结果不同）；
    * 审计对象 = 当前真实 Context Builder（Step 13）与接线（Step 14），
      两者本 Step **零修改**。

冻结来源：``docs/evaluation/Phase 4.1 Step 16 — Context Budget Audit.md``
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.services.conversation_context_builder import (
    build_context,
    build_messages,
)
from backend.app.services.conversation_service import ConversationTurnView

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_budget_audit.py"
_BUILDER = "backend/app/services/conversation_context_builder.py"
_SERVICE = "backend/app/services/chat_application_service.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 16 — Context Budget Audit.md"

_BASE = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)

#: growth 采样点（§五 要求 1 / 10 / 50 / 100 / 500 / 1000）。
_GROWTH_SIZES: tuple[int, ...] = (1, 10, 50, 100, 500, 1000)

#: 每条消息内容长度（"m0000" + 15 个填充字符 = 20）。
_CONTENT_SIZE = 20
_FILLER = "x" * 15

#: builder 允许的 import 前缀（白名单）。
_ALLOWED_IMPORT_PREFIXES: tuple[str, ...] = (
    "__future__",
    "collections.abc",
    "typing",
    "dataclasses",
)

#: builder 禁止的 import 前缀（§十五 冻结；AST 模块名匹配，非全文扫描）。
_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "sqlalchemy",
    "psycopg",
    "openai",
    "deepseek",
    "siliconflow",
    "tiktoken",
    "transformers",
    "tokenizers",
    "redis",
    "celery",
    "kafka",
    "httpx",
    "requests",
    "backend.app.db",
)

#: builder 禁止出现的标识符（token 估算 / DB 原语 / 领域服务）。
_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "tiktoken",
    "encode",
    "encoding_for_model",
    "token_count",
    "count_tokens",
    "Session",
    "select",
    "insert",
    "update",
    "ConversationService",
    "ChatApplicationService",
)

_REQUIRED_DOC_SECTIONS: tuple[str, ...] = (
    "## 1. Current State",
    "## 2. Future State (Design)",
    "## 3. Context Contract (Frozen)",
    "## 4. Context Growth Evidence",
    "## 5. Token Counting = Deferred",
    "## 6. Future Budget Responsibility",
    "## 7. Truncation Strategies",
    "## 8. System / User / Assistant Boundary",
    "## 9. Current USER Boundary",
    "## 10. Failure Semantics",
    "## 11. Determinism",
    "## 12. Security",
    "## 13. Deferred Implementation",
)

_REQUIRED_DOC_STATEMENTS: tuple[str, ...] = (
    "Current context = full previous history",
    "Token budget = not implemented",
    "Truncation = not implemented",
    "Summary = not implemented",
    "Memory = not implemented",
    "Tokenizer = not introduced",
)


# ============================================================
# Helpers（离线构造 + 长度口径 = len()）
# ============================================================


def _history(count: int) -> list[ConversationTurnView]:
    """交替 USER / ASSISTANT 的可预测历史（内容 = "m0000" + 15 个 "x"）。

    turn_id 从 1000 起编号：确保 "turn_id 不进入 context" 的断言
    不会被内容中的数字误命中（内容只含 "m0000" 形式）。
    """
    return [
        ConversationTurnView(
            turn_id=1000 + index,
            conversation_id="conv-long-history",
            role="USER" if index % 2 == 0 else "ASSISTANT",
            content=f"m{index:04d}{_FILLER}",
            assistant_request_id=(
                None if index % 2 == 0 else f"req-{index}"
            ),
            created_at=_BASE,
        )
        for index in range(count)
    ]


def _expected_context_length(count: int) -> int:
    """精确公式：Σ("role: ".len + 20) + (count - 1) 个换行分隔符。"""
    total = 0
    for index in range(count):
        role = "user" if index % 2 == 0 else "assistant"
        total += len(f"{role}: ") + _CONTENT_SIZE
    return total + (count - 1)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def _module_imports(relative: str) -> set[str]:
    tree = ast.parse(_source(relative))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _identifiers(relative: str) -> set[str]:
    tree = ast.parse(_source(relative))
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


def _module_level_assign_targets(relative: str) -> set[str]:
    targets: set[str] = set()
    for node in getattr(ast.parse(_source(relative)), "body", []):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and not target.id.startswith("__"):
                targets.add(target.id)
    return targets


# ============================================================
# 1. Context Growth Audit（character length，无 token 估算）
# ============================================================


class TestContextGrowthAudit:
    @pytest.mark.parametrize("count", _GROWTH_SIZES)
    def test_context_length_is_exact_character_count(self, count: int) -> None:
        context = build_context(_history(count))

        assert context is not None
        assert len(context) == _expected_context_length(count)

    def test_context_length_growth_table(self) -> None:
        """记录 growth 表：1 / 10 / 50 / 100 / 500 / 1000 → 字符长度单调线性增长。"""
        lengths = {
            count: len(build_context(_history(count)) or "")
            for count in _GROWTH_SIZES
        }

        assert lengths == {
            1: 26,
            10: 294,
            50: 1474,
            100: 2949,
            500: 14749,
            1000: 29499,
        }
        ordered = [lengths[count] for count in _GROWTH_SIZES]
        assert ordered == sorted(ordered)
        assert lengths[1000] > lengths[100] * 9  # 线性增长（非亚线性截断）

    def test_no_truncation_parameter_exists(self) -> None:
        """当前 builder 无任何 budget / truncation 参数（全量历史）。"""
        for func in (build_context, build_messages):
            params = list(inspect.signature(func).parameters)
            assert params == ["turns"], func.__name__
        identifiers = _identifiers(_BUILDER)
        for forbidden in (
            "max_chars",
            "max_context_chars",
            "budget",
            "truncate",
            "truncation",
            "limit",
        ):
            assert forbidden not in identifiers, forbidden


# ============================================================
# 2. Contract Freeze（§四 10 条 + §十四 4~9）
# ============================================================


class TestContextContractFreeze:
    def test_01_empty_history_is_none(self) -> None:
        assert build_context([]) is None

    def test_02_role_mapping_is_frozen(self) -> None:
        context = build_context(_history(2))
        assert context == (
            f"user: m0000{_FILLER}\nassistant: m0001{_FILLER}"
        )

    def test_03_ordering_is_deterministic_and_not_sorted(self) -> None:
        turns = _history(4)
        reversed_turns = list(reversed(turns))

        assert build_context(turns) == build_context(turns)  # 确定性
        assert build_context(reversed_turns) != build_context(turns)  # 不排序
        assert build_context(reversed_turns) == "\n".join(
            [
                f"assistant: m0003{_FILLER}",
                f"user: m0002{_FILLER}",
                f"assistant: m0001{_FILLER}",
                f"user: m0000{_FILLER}",
            ]
        )

    def test_04_current_user_exclusion_contract(self) -> None:
        """排除责任在 ChatApplicationService（按 turn_id）；builder 只格式化输入。

        使用 AST 判定（docstring 中的历史说明文字不算实现）。
        """
        tree = ast.parse(_source(_SERVICE))
        comparisons = [
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
        ]
        assert any("turn.turn_id" in text for text in comparisons), comparisons
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(
                node.slice, ast.UnaryOp
            ):
                assert not isinstance(node.slice.op, ast.USub), ast.unparse(node)

        # builder 层面：传入"已排除 current"的历史 → current 内容不出现
        current_content = "其中库存最低的 10 个是什么？"
        previous = _history(2)  # previous = 前两条
        context = build_context(previous) or ""
        assert current_content not in context

    def test_05_no_metadata_in_context(self) -> None:
        """request_id / turn_id / conversation_id / created_at 均不进入 context。"""
        turns = _history(3)
        context = build_context(turns) or ""

        for turn in turns:
            assert str(turn.turn_id) not in context
            if turn.assistant_request_id is not None:
                assert turn.assistant_request_id not in context
        assert "conv-long-history" not in context
        assert "2026-10-03" not in context
        assert "T12:00:00" not in context

    def test_06_lines_only_contain_role_and_content(self) -> None:
        context = build_context(_history(4)) or ""
        for line in context.split("\n"):
            role, _, _content = line.partition(": ")
            assert role in {"user", "assistant"}

    def test_07_no_sql_rag_tool_payload_vocabulary(self) -> None:
        """context 只是历史文本：不引入 SQL / chunk / tool 相关结构。"""
        context = build_context(_history(4)) or ""
        for forbidden in (
            "SELECT",
            "chunk_id",
            "similarity",
            "tool_call",
            "arguments",
            "sql",
            "embedding",
        ):
            assert forbidden not in context


# ============================================================
# 3. No Token Estimation（§六）
# ============================================================


class TestNoTokenEstimation:
    def test_01_builder_has_no_tokenizer_dependency(self) -> None:
        modules = _module_imports(_BUILDER)
        for module in modules:
            for prefix in ("tiktoken", "transformers", "tokenizers", "sentencepiece"):
                assert not module.startswith(prefix), module

    def test_02_builder_has_no_token_estimation_identifiers(self) -> None:
        identifiers = _identifiers(_BUILDER)
        for forbidden in ("tiktoken", "encode", "token_count", "count_tokens"):
            assert forbidden not in identifiers, forbidden

    def test_03_audit_uses_character_length_only(self) -> None:
        """本审计文件自身也不引入 tokenizer / token 估算。"""
        modules = _module_imports(_SELF)
        for module in modules:
            for prefix in ("tiktoken", "transformers", "tokenizers"):
                assert not module.startswith(prefix), module
        # 长度断言 = len()（由 growth 表精确值锁定，无比例换算）
        assert _expected_context_length(2) == len(build_context(_history(2)) or "")


# ============================================================
# 4. Architecture Audit（AST；§十五）
# ============================================================


class TestArchitectureBoundary:
    def test_01_no_forbidden_imports(self) -> None:
        modules = _module_imports(_BUILDER)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in _FORBIDDEN_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_02_imports_are_whitelisted(self) -> None:
        modules = _module_imports(_BUILDER)
        for module in modules:
            assert any(
                module.startswith(prefix) for prefix in _ALLOWED_IMPORT_PREFIXES
            ), module

    def test_03_no_forbidden_identifiers(self) -> None:
        identifiers = _identifiers(_BUILDER)
        for forbidden in _FORBIDDEN_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden

    def test_04_builder_has_no_module_level_state(self) -> None:
        assert _module_level_assign_targets(_BUILDER) == set()


# ============================================================
# 5. Documentation（§十六）
# ============================================================


class TestAuditDocument:
    def test_01_required_sections(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in _REQUIRED_DOC_SECTIONS:
            assert section in doc, section

    def test_02_required_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for statement in _REQUIRED_DOC_STATEMENTS:
            assert statement in doc, statement

    def test_03_doc_records_growth_evidence(self) -> None:
        doc = _source(_AUDIT_DOC)
        for length in ("29499", "14749", "2949"):
            assert length in doc, length


__all__ = [
    "TestContextGrowthAudit",
    "TestContextContractFreeze",
    "TestNoTokenEstimation",
    "TestArchitectureBoundary",
    "TestAuditDocument",
]
