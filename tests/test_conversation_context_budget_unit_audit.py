"""History Budget Unit Decision Audit（Phase 4.1 Step 19 —— **Audit only**）。

本文件性质：

    * **不实现** Budget / ContextSelector / truncation / tokenizer；
    * 比较 max_turns / max_chars / max_tokens / Hybrid 四种候选度量单位；
    * **纯离线**：DB = 0 · Network = 0 · LLM = 0 · Tokenizer = 0；
    * 冻结来源：``docs/evaluation/Phase 4.1 Step 19 — History Budget Unit Decision Audit.md``。

结论（本 Step）：**History Budget Unit = Deferred**（不选择最终单位）。
"""
from __future__ import annotations

import ast
import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.db.conversation_repository import ConversationRepository
from backend.app.services.conversation_context_builder import build_context
from backend.app.services.conversation_service import (
    ConversationService,
    ConversationTurnView,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_budget_unit_audit.py"
_ORM = "backend/app/db/models/conversation.py"
_REPOSITORY = "backend/app/db/conversation_repository.py"
_CONVERSATION_SERVICE = "backend/app/services/conversation_service.py"
_BUILDER = "backend/app/services/conversation_context_builder.py"
_APPLICATION_SERVICE = "backend/app/services/chat_application_service.py"
_AUDIT_DOC = (
    "docs/evaluation/Phase 4.1 Step 19 — History Budget Unit Decision Audit.md"
)

_BASE = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)

#: 四个候选度量单位（概念名；非 production 字段）。
_CANDIDATE_UNITS: tuple[str, ...] = ("turns", "chars", "tokens", "hybrid")

#: Conversation ORM 冻结字段（不得出现任何 budget 字段）。
_FROZEN_CONVERSATION_FIELDS: frozenset[str] = frozenset(
    {"conversation_id", "project_id", "created_at", "updated_at", "status"}
)

#: Budget 允许表达的概念字段（Security：unit / limit / strategy）。
_ALLOWED_BUDGET_FIELDS: frozenset[str] = frozenset(
    {"unit", "limit", "strategy", "limit_turns", "limit_chars"}
)

_FORBIDDEN_BUDGET_FIELDS: tuple[str, ...] = (
    "api_key",
    "authorization",
    "password",
    "database_url",
    "sql",
    "prompt",
    "messages",
    "chunks",
    "tool",
    "session",
    "llm_response",
    "model",
)

#: conversation 链路文件（History Budget / tokenizer 必须缺席的位置）。
_CONVERSATION_CHAIN_FILES: tuple[str, ...] = (
    _BUILDER,
    _CONVERSATION_SERVICE,
    _REPOSITORY,
    _APPLICATION_SERVICE,
    "backend/app/services/ai_orchestrator_service.py",
)

_TOKENIZER_IMPORT_PREFIXES: tuple[str, ...] = (
    "tiktoken",
    "transformers",
    "tokenizers",
    "sentencepiece",
)

_REQUIRED_DOC_SECTIONS: tuple[str, ...] = (
    "## 1. Current State",
    "## 2. max_turns",
    "## 3. max_chars",
    "## 4. max_tokens",
    "## 5. Hybrid",
    "## 6. Engineering Guardrail vs Capacity Constraint",
    "## 7. Step 16 Evidence",
    "## 8. Tokenizer Decision",
    "## 9. Budget Ownership",
    "## 10. Policy / Budget Composition",
    "## 11. Zero / Negative",
    "## 12. Oversized Budget",
    "## 13. Builder Boundary",
    "## 14. Persistence Boundary",
    "## 15. Failure Semantics",
    "## 16. Security",
    "## 17. Determinism",
    "## 18. Deferred Decision",
)

_REQUIRED_DOC_STATEMENTS: tuple[str, ...] = (
    "History Budget Unit = Deferred",
    "Tokenizer = Deferred",
    "ContextSelector = Deferred",
    "Truncation = Deferred",
    "max_chars 可以作为工程保护阈值，但不能声称它是 Token Budget",
    "zero → valid but selects empty history",
    "negative → invalid policy",
    "effective budget = min(requested, available)",
)


# ============================================================
# 测试内概念表达（**非 production DTO**）
# ============================================================


@dataclasses.dataclass(frozen=True)
class FakeBudget:
    """概念演示：unit + limit（非 production 配置）。"""

    unit: str
    limit: int


def validate_budget(budget: FakeBudget) -> None:
    """演示 future 校验语义（非 production validator）。

    * negative → invalid policy（ValueError）；
    * zero → valid（选择空历史，由调用方表达）；
    * tokens 单位 → 需要 tokenizer → 当前 NotImplemented（Deferred）。
    """
    if budget.unit == "tokens":
        raise NotImplementedError("token 单位需要 tokenizer（Tokenizer = Deferred）")
    if budget.unit not in {"turns", "chars", "hybrid"}:
        raise ValueError(f"非法 unit: {budget.unit!r}")
    if budget.limit < 0:
        raise ValueError(f"limit 不可为负（当前: {budget.limit}）")


def select_with_unit(
    turns: list[ConversationTurnView], budget: FakeBudget
) -> tuple[ConversationTurnView, ...]:
    """演示 turns 单位的选择（测试语义；非 production Selector）。"""
    validate_budget(budget)
    if budget.unit == "turns":
        if budget.limit == 0:
            return ()
        return tuple(turns[-budget.limit :])
    raise NotImplementedError("本审计不实现 chars / hybrid 单位选择")


def _history(count: int) -> list[ConversationTurnView]:
    return [
        ConversationTurnView(
            turn_id=3000 + index,
            conversation_id="conv-budget-unit",
            role="USER" if index % 2 == 0 else "ASSISTANT",
            content=f"m{index:04d}",
            assistant_request_id=None if index % 2 == 0 else f"req-{index}",
            created_at=_BASE,
        )
        for index in range(count)
    ]


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def _module_imports(relative: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(_source(relative))):
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


def _mapped_field_names(relative: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(_source(relative))):
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
# 1. 四种单位是不同概念
# ============================================================


class TestBudgetUnitsAreDistinct:
    def test_01_four_candidate_units_are_distinct(self) -> None:
        assert len(set(_CANDIDATE_UNITS)) == 4
        assert set(_CANDIDATE_UNITS) == {"turns", "chars", "tokens", "hybrid"}

    def test_02_max_chars_is_not_a_token_budget(self) -> None:
        """max_chars = Engineering Guardrail；不得声称是 Token Budget。"""
        doc = _source(_AUDIT_DOC)
        assert (
            "max_chars 可以作为工程保护阈值，但不能声称它是 Token Budget" in doc
        )

        # 本审计文件不得出现 chars → tokens 换算（无比例推算）。
        source = _source(_SELF)
        forbidden_patterns = (
            "/" + " 4",
            "/" + " 3",
            "*" + " 0.25",
            "0.25" + " *",
        )
        for pattern in forbidden_patterns:
            assert pattern not in source, pattern

    def test_03_step16_growth_evidence_is_reused_not_recomputed(self) -> None:
        """引用 Step 16 实测（1→26 … 1000→29499），不重建 tokenizer / benchmark。"""
        doc = _source(_AUDIT_DOC)
        for evidence in ("26", "294", "1474", "2949", "14749", "29499"):
            assert evidence in doc, evidence

    def test_04_token_unit_requires_tokenizer(self) -> None:
        """tokens 单位当前不可校验（Tokenizer = Deferred）。"""
        with pytest.raises(NotImplementedError):
            validate_budget(FakeBudget(unit="tokens", limit=6000))


# ============================================================
# 2. Ownership / Persistence / Builder 边界
# ============================================================


class TestBoundaryPreservation:
    def test_05_conversation_orm_has_no_budget_fields(self) -> None:
        fields = _mapped_field_names(_ORM)
        assert fields == set(_FROZEN_CONVERSATION_FIELDS)
        for forbidden in ("context_budget", "max_turns", "max_chars", "max_tokens"):
            assert forbidden not in fields, forbidden

    def test_06_conversation_service_has_no_budget_parameters(self) -> None:
        import inspect

        for name in (
            "create_conversation",
            "get_conversation",
            "archive_conversation",
            "append_turn",
            "list_turns",
        ):
            params = inspect.signature(getattr(ConversationService, name)).parameters
            for forbidden in ("budget", "max_turns", "max_chars", "limit"):
                assert forbidden not in params, f"{name}:{forbidden}"

        identifiers = _identifiers(_CONVERSATION_SERVICE)
        for forbidden in ("context_budget", "max_turns", "max_chars", "max_tokens"):
            assert forbidden not in identifiers, forbidden

    def test_07_application_service_is_future_budget_composition_boundary(self) -> None:
        identifiers = _identifiers(_APPLICATION_SERVICE)
        for forbidden in (
            "context_budget",
            "max_turns",
            "max_chars",
            "max_tokens",
            "ContextSelector",
        ):
            assert forbidden not in identifiers, forbidden

        import inspect

        from backend.app.services.chat_application_service import (
            ChatApplicationService,
        )

        params = {
            name: parameter.kind
            for name, parameter in inspect.signature(
                ChatApplicationService.execute_message
            ).parameters.items()
            if name != "self"
        }
        assert params == {
            "conversation_id": inspect.Parameter.KEYWORD_ONLY,
            "content": inspect.Parameter.KEYWORD_ONLY,
            # Phase 4.2 Step 6：可选幂等键（keyword-only）
            "idempotency_key": inspect.Parameter.KEYWORD_ONLY,
        }

    def test_08_budget_does_not_enter_builder(self) -> None:
        import inspect

        params = list(inspect.signature(build_context).parameters)
        assert params == ["turns"]

        identifiers = _identifiers(_BUILDER)
        for forbidden in (
            "budget",
            "context_budget",
            "max_turns",
            "max_chars",
            "max_tokens",
            "tokenizer",
        ):
            assert forbidden not in identifiers, forbidden

    def test_09_budget_does_not_enter_persistence_layer(self) -> None:
        for relative in (_ORM, _REPOSITORY):
            identifiers = _identifiers(relative)
            for forbidden in (
                "context_budget",
                "max_turns",
                "max_chars",
                "max_tokens",
                "SelectionPolicy",
            ):
                assert forbidden not in identifiers, f"{relative}:{forbidden}"

        repository_methods = {
            name
            for name in vars(ConversationRepository)
            if not name.startswith("_") and not name.startswith("build_")
        }
        assert repository_methods == {
            "create",
            "get_by_conversation_id",
            "update_status",
            "append_turn",
            "list_turns_by_conversation_id",
            # Phase 4.2 Step 6：幂等查询（只读 · 显式列）
            "find_turn_by_idempotency_key",
            "find_next_assistant_turn",
        }


# ============================================================
# 3. Zero / Negative / Oversized
# ============================================================


class TestZeroNegativeOversized:
    def test_10_zero_is_valid_and_selects_empty_history(self) -> None:
        assert validate_budget(FakeBudget(unit="turns", limit=0)) is None
        assert select_with_unit(_history(3), FakeBudget(unit="turns", limit=0)) == ()

    def test_11_negative_limit_is_invalid_policy(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            validate_budget(FakeBudget(unit="turns", limit=-1))
        assert "不可为负" in str(excinfo.value)

    def test_12_oversized_budget_entry_uses_min_requested_available(self) -> None:
        """effective = min(requested, available)（抽象预算单元；非 token 估算）。"""
        requested = 1_000_000
        available = 6_000

        assert min(requested, available) == available
        assert min(4_000, available) == 4_000

        doc = _source(_AUDIT_DOC)
        assert "effective budget = min(requested, available)" in doc
        assert "min(requested, available)" in doc


# ============================================================
# 4. Tokenizer / 链路依赖事实
# ============================================================


class TestTokenizerDecision:
    def test_13_tokenizer_absent_from_conversation_chain(self) -> None:
        for relative in _CONVERSATION_CHAIN_FILES:
            modules = _module_imports(relative)
            assert modules, f"AST 未解析到 import: {relative}"
            for module in modules:
                for prefix in _TOKENIZER_IMPORT_PREFIXES:
                    assert not module.startswith(prefix), f"{relative}:{module}"

    def test_14_reranker_optional_transformers_is_not_history_dependency(self) -> None:
        """如实记录：reranker 有可选懒加载 transformers；conversation 链路不引用它。"""
        reranker_files = sorted(
            str(path.relative_to(_REPO_ROOT)).replace("\\", "/")
            for path in (_REPO_ROOT / "backend/app/reranker").glob("*.py")
        )
        assert reranker_files, "reranker 模块应存在（事实记录）"

        for relative in _CONVERSATION_CHAIN_FILES:
            modules = _module_imports(relative)
            for module in modules:
                assert "reranker" not in module, f"{relative}:{module}"

    def test_15_no_token_unit_infrastructure_for_history(self) -> None:
        """无 model context window source / 无 tokenizer 依赖（事实记录）。"""
        for relative in _CONVERSATION_CHAIN_FILES:
            identifiers = _identifiers(relative)
            for forbidden in ("context_window", "tiktoken", "token_count"):
                assert forbidden not in identifiers, f"{relative}:{forbidden}"


# ============================================================
# 5. Security / Failure / 文档
# ============================================================


class TestSecurityAndFailure:
    def test_16_budget_fields_are_behavior_only(self) -> None:
        fields = set(FakeBudget.__dataclass_fields__)
        assert fields <= set(_ALLOWED_BUDGET_FIELDS), fields
        for forbidden in _FORBIDDEN_BUDGET_FIELDS:
            assert forbidden not in fields, forbidden

    def test_17_failure_semantics_are_documented(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "invalid policy" in doc
        assert "business failure" in doc
        for forbidden in (
            "fallback full history",
            "ignore budget",
            "silent degradation",
        ):
            assert forbidden in doc, forbidden

    def test_18_determinism_is_documented(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "same history" in doc
        assert "same policy" in doc
        assert "same budget" in doc


class TestAuditDocument:
    def test_19_required_sections(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in _REQUIRED_DOC_SECTIONS:
            assert section in doc, section

    def test_20_required_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for statement in _REQUIRED_DOC_STATEMENTS:
            assert statement in doc, statement

    def test_21_doc_records_existing_constraints(self) -> None:
        """如实记录既有 segment 约束（非 History budget）。"""
        doc = _source(_AUDIT_DOC)
        for evidence in (
            "DEFAULT_MAX_CONTEXT_CHARS",
            "DEFAULT_MAX_CHARS",
            "DEFAULT_CONTEXT_MAX_CHARS",
            "DEFAULT_MAX_ROWS",
        ):
            assert evidence in doc, evidence


__all__ = [
    "FakeBudget",
    "TestBudgetUnitsAreDistinct",
    "TestBoundaryPreservation",
    "TestZeroNegativeOversized",
    "TestTokenizerDecision",
    "TestSecurityAndFailure",
    "TestAuditDocument",
]
