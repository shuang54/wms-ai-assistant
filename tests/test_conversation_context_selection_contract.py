"""Context Selection Contract（Phase 4.1 Step 17 —— **Contract only**）。

本文件性质：

    * **不实现** production Context Selector（本 Step 零 production 改动）；
    * 用测试内 **FakeContextSelector** 验证 Input / Output / Ordering /
      Determinism / Failure / Security 契约；
    * **纯离线**：DB = 0 · Network = 0 · LLM = 0 · Tokenizer = 0；
    * 冻结来源：``docs/evaluation/Phase 4.1 Step 17 — Context Selection Contract.md``。

冻结链路（未来形态；本 Step 只冻结 Contract）：

```text
ConversationTurnView[]（previous turns，已排除 current USER）
        ↓
Context Selector（未来新增；本 Step 不实现）
        ↓
ConversationTurnView[]（selected ⊆ previous；顺序保持）
        ↓
ConversationContextBuilder.build_context()
        ↓
str | None
```
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.services.conversation_context_builder import build_context
from backend.app.services.conversation_service import ConversationTurnView

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_selection_contract.py"
_SERVICE = "backend/app/services/chat_application_service.py"
_BUILDER = "backend/app/services/conversation_context_builder.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 17 — Context Selection Contract.md"

_BASE = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)

_REQUIRED_DOC_SECTIONS: tuple[str, ...] = (
    "## 1. Current Architecture",
    "## 2. Selection Responsibility",
    "## 3. Builder Responsibility",
    "## 4. ConversationService Responsibility",
    "## 5. ChatApplicationService Responsibility",
    "## 6. Selector Input",
    "## 7. Selector Output",
    "## 8. Current USER Boundary",
    "## 9. Ordering",
    "## 10. Determinism",
    "## 11. Failure Semantics",
    "## 12. Security Boundary",
    "## 13. System / Project / Tool / History Separation",
    "## 14. Strategy Comparison",
    "## 15. Tokenizer Deferred",
    "## 16. Implementation Deferred",
)

_REQUIRED_DOC_STATEMENTS: tuple[str, ...] = (
    "Context Selector = not implemented",
    "Truncation = not implemented",
    "Tokenizer = not introduced",
    "Production Code = 0",
)


# ============================================================
# Fake Selector（测试内 Contract 演示；**非 production**）
# ============================================================


class FakeContextSelector:
    """Contract 演示：从 previous turns 选子集（不修改、不重排、确定性）。

    ``keep_last`` / ``keep_first`` 仅为测试语义的**局部参数**，
    **不是**生产配置类型（Step 17 §9：不创建 max_tokens / max_chars / max_turns）。
    """

    def __init__(
        self,
        *,
        keep_last: int | None = None,
        keep_first: bool = False,
        error: Exception | None = None,
    ) -> None:
        self._keep_last = keep_last
        self._keep_first = keep_first
        self._error = error

    def select(self, turns: object) -> tuple[ConversationTurnView, ...]:
        if self._error is not None:
            raise self._error
        history = list(turns)  # type: ignore[arg-type]
        selected = history
        if self._keep_last is not None:
            selected = selected[-self._keep_last :] if self._keep_last > 0 else []
        if (
            self._keep_first
            and history
            and (not selected or selected[0] is not history[0])
        ):
            selected = [history[0], *selected]
        return tuple(selected)


def _history(labels: str) -> list[ConversationTurnView]:
    """labels 每个字符 = 一条 turn（交替 USER / ASSISTANT）。"""
    return [
        ConversationTurnView(
            turn_id=1000 + index,
            conversation_id="conv-selection",
            role="USER" if index % 2 == 0 else "ASSISTANT",
            content=f"{label}-content",
            assistant_request_id=None if index % 2 == 0 else f"req-{index}",
            created_at=_BASE,
        )
        for index, label in enumerate(labels)
    ]


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


# ============================================================
# 1. Selection Contract（§19 覆盖 1~10）
# ============================================================


class TestSelectionContract:
    def test_01_selected_keeps_original_order(self) -> None:
        """A B C → 选 B C（保持原顺序，不重排）。"""
        history = _history("ABC")
        selected = FakeContextSelector(keep_last=2).select(history)

        assert [turn.content for turn in selected] == [
            "B-content",
            "C-content",
        ]

    def test_02_selected_is_identity_subset_of_input(self) -> None:
        """selected ⊆ previous（同一对象身份，不复制、不新建）。"""
        history = _history("ABCD")
        selected = FakeContextSelector(keep_last=2, keep_first=True).select(history)

        assert [turn.content for turn in selected] == [
            "A-content",
            "C-content",
            "D-content",
        ]
        for turn in selected:
            assert any(turn is origin for origin in history)

    def test_03_turn_objects_are_immutable(self) -> None:
        history = _history("AB")
        selected = FakeContextSelector().select(history)

        with pytest.raises(dataclasses.FrozenInstanceError):
            selected[0].content = "mutated"  # type: ignore[misc]
        assert selected[0] is history[0]

    def test_04_content_is_unchanged(self) -> None:
        history = _history("ABC")
        selected = FakeContextSelector(keep_last=2).select(history)

        assert [turn.content for turn in selected] == [
            "B-content",
            "C-content",
        ]
        assert [turn.content for turn in history] == [
            "A-content",
            "B-content",
            "C-content",
        ]

    def test_05_role_is_unchanged(self) -> None:
        history = _history("ABC")
        selected = FakeContextSelector(keep_last=2).select(history)

        assert [turn.role for turn in selected] == ["ASSISTANT", "USER"]
        assert [turn.role for turn in selected] == [
            turn.role for turn in history[1:]
        ]

    def test_06_turn_ids_are_unchanged(self) -> None:
        history = _history("ABCD")
        selected = FakeContextSelector(keep_last=3).select(history)

        assert [turn.turn_id for turn in selected] == [1001, 1002, 1003]
        assert [turn.turn_id for turn in selected] == sorted(
            turn.turn_id for turn in selected
        )

    def test_07_empty_history_is_empty_selection(self) -> None:
        assert FakeContextSelector().select([]) == ()
        assert FakeContextSelector(keep_last=2, keep_first=True).select([]) == ()

    def test_08_selection_is_deterministic(self) -> None:
        history = _history("ABCDE")
        selector = FakeContextSelector(keep_last=2, keep_first=True)

        assert selector.select(history) == selector.select(history)
        assert [turn.content for turn in selector.select(history)] == [
            "A-content",
            "D-content",
            "E-content",
        ]

    def test_09_failure_is_business_failure(self) -> None:
        """selection failure → 原样上抛（不 fallback 全量、不静默跳过）。"""
        history = _history("ABC")
        selector = FakeContextSelector(
            error=RuntimeError("injected selection failure")
        )

        with pytest.raises(RuntimeError) as excinfo:
            selector.select(history)

        assert type(excinfo.value) is RuntimeError  # 不包装
        assert "injected selection failure" in str(excinfo.value)

    def test_10_selection_never_reorders(self) -> None:
        """任意 policy 组合下，selected 必须是 input 的子序列。"""
        history = _history("ABCDE")
        for selector in (
            FakeContextSelector(),
            FakeContextSelector(keep_last=3),
            FakeContextSelector(keep_last=2, keep_first=True),
        ):
            selected = selector.select(history)
            positions = [history.index(turn) for turn in selected]
            assert positions == sorted(positions), positions

    def test_11_subset_only_never_adds_turns(self) -> None:
        history = _history("ABC")
        selected = FakeContextSelector(keep_last=2).select(history)

        assert len(selected) <= len(history)
        assert all(any(turn is origin for origin in history) for turn in selected)


# ============================================================
# 2. Pipeline Composition（selected → builder 无损）
# ============================================================


class TestPipelineComposition:
    def test_12_selected_then_build_context_has_one_line_per_turn(self) -> None:
        history = _history("ABCD")
        selected = FakeContextSelector(keep_last=2).select(history)

        context = build_context(selected)

        assert context is not None
        assert len(context.split("\n")) == len(selected)

    def test_13_full_selection_matches_current_behavior(self) -> None:
        """全选（policy = 全部）时 pipeline 与现状完全一致（无损组合）。"""
        history = _history("ABC")

        assert build_context(FakeContextSelector().select(history)) == (
            build_context(history)
        )

    def test_14_builder_has_no_selection_parameter(self) -> None:
        """Builder 只格式化：签名只有 turns（选择不属于 Builder）。"""
        params = list(inspect.signature(build_context).parameters)
        assert params == ["turns"]


# ============================================================
# 3. Current USER Boundary（§11）
# ============================================================


class TestCurrentUserBoundary:
    def test_15_service_excludes_current_user_before_selection(self) -> None:
        """排除责任在 ChatApplicationService（AST；Selector 输入 = previous）。"""
        tree = ast.parse(_source(_SERVICE))
        comparisons = [
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
        ]
        assert any("turn.turn_id" in text for text in comparisons)
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(
                node.slice, ast.UnaryOp
            ):
                assert not isinstance(node.slice.op, ast.USub), ast.unparse(node)

    def test_16_selector_does_not_take_current_turn_parameters(self) -> None:
        params = list(inspect.signature(FakeContextSelector.select).parameters)
        assert params == ["self", "turns"]
        for forbidden in ("conversation_id", "current_turn_id", "turn_id"):
            assert forbidden not in params

    def test_17_selected_context_never_contains_current_question(self) -> None:
        current_question = "其中库存最低的 10 个是什么？"
        previous = FakeContextSelector().select(_history("AB"))

        assert current_question not in (build_context(previous) or "")


# ============================================================
# 4. Security / Deferred（§15 / §22）
# ============================================================


class TestSecurityAndDeferred:
    def test_18_selector_contract_has_no_infrastructure_dependency(self) -> None:
        """Selector Contract 不依赖 DB / LLM / RAG / Tool（本文件同样离线）。"""
        modules = _module_imports(_SELF)
        for module in modules:
            for forbidden in (
                "sqlalchemy",
                "psycopg",
                "backend.app.db",
                "backend.app.llm",
                "backend.app.rag",
                "backend.app.tools",
                "httpx",
                "openai",
            ):
                assert not module.startswith(forbidden), module

    def test_19_no_production_selector_module_exists(self) -> None:
        """本 Step **不实现** production Selector（文件不存在即契约）。"""
        candidate = (
            _REPO_ROOT / "backend/app/services/conversation_context_selector.py"
        )
        assert not candidate.exists()
        selector_files = sorted(
            path.name
            for path in (_REPO_ROOT / "backend/app/services").glob("*selector*.py")
        )
        assert not any("conversation" in name for name in selector_files), (
            selector_files
        )

    def test_20_no_tokenizer_or_budget_config_in_contract(self) -> None:
        """Tokenizer 仍 Deferred；不引入 max_tokens / max_chars 生产配置。"""
        identifiers = {
            node.id
            for node in ast.walk(ast.parse(_source(_SELF)))
            if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(ast.parse(_source(_SELF)))
            if isinstance(node, ast.Attribute)
        }
        for forbidden in ("tiktoken", "max_tokens", "token_budget"):
            assert forbidden not in identifiers, forbidden
        builder_identifiers = {
            node.attr
            for node in ast.walk(ast.parse(_source(_BUILDER)))
            if isinstance(node, ast.Attribute)
        } | {
            node.id
            for node in ast.walk(ast.parse(_source(_BUILDER)))
            if isinstance(node, ast.Name)
        }
        for forbidden in ("max_tokens", "max_chars", "tiktoken"):
            assert forbidden not in builder_identifiers, forbidden


# ============================================================
# 5. Documentation（§21）
# ============================================================


class TestContractDocument:
    def test_21_required_sections(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in _REQUIRED_DOC_SECTIONS:
            assert section in doc, section

    def test_22_required_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for statement in _REQUIRED_DOC_STATEMENTS:
            assert statement in doc, statement


__all__ = [
    "FakeContextSelector",
    "TestSelectionContract",
    "TestPipelineComposition",
    "TestCurrentUserBoundary",
    "TestSecurityAndDeferred",
    "TestContractDocument",
]
