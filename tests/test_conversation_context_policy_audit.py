"""Selection Policy / Budget Ownership Audit（Phase 4.1 Step 18 —— **Audit only**）。

本文件性质：

    * **不实现** production SelectionPolicy / ContextSelector / truncation / tokenizer；
    * 用测试内 **FakeSelectionPolicy / FakeBudget / FakeSelector** 表达概念并验证边界；
    * **纯离线**：DB = 0 · Network = 0 · LLM = 0 · Tokenizer = 0；
    * 冻结来源：``docs/evaluation/Phase 4.1 Step 18 — Selection Policy & Budget Ownership Audit.md``。

核心问题：**"Context 应该保留多少历史"这个决策由谁拥有？**
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.app.dto.conversation_api import ConversationMessageRequest
from backend.app.main import app
from backend.app.services.chat_application_service import ChatApplicationService
from backend.app.services.conversation_context_builder import build_context
from backend.app.services.conversation_service import (
    ConversationService,
    ConversationTurnView,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_policy_audit.py"
_ORM = "backend/app/db/models/conversation.py"
_CONVERSATION_SERVICE = "backend/app/services/conversation_service.py"
_APPLICATION_SERVICE = "backend/app/services/chat_application_service.py"
_AUDIT_DOC = (
    "docs/evaluation/Phase 4.1 Step 18 — Selection Policy & Budget Ownership Audit.md"
)

_BASE = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)

#: Conversation ORM 冻结字段（Step 2：不得出现 budget / policy 字段）。
_FROZEN_CONVERSATION_FIELDS: frozenset[str] = frozenset(
    {"conversation_id", "project_id", "created_at", "updated_at", "status"}
)

#: 未来 Budget / Policy 禁止落入的字段名（本 Step 只审计）。
_FORBIDDEN_BUDGET_FIELD_NAMES: tuple[str, ...] = (
    "context_budget",
    "selection_policy",
    "max_tokens",
    "max_turns",
    "max_chars",
    "token_budget",
)

#: Policy 允许描述的语义（"怎么选" / "最多多少"），不得包含执行依赖。
_ALLOWED_POLICY_FIELDS: frozenset[str] = frozenset(
    {"strategy", "recent_n", "keep_first", "budget_chars"}
)

#: Policy / Budget 禁止包含的敏感或执行依赖字段名。
_FORBIDDEN_POLICY_FIELDS: tuple[str, ...] = (
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
    "connection",
    "llm",
    "client",
)

_REQUIRED_DOC_SECTIONS: tuple[str, ...] = (
    "## 1. Current State",
    "## 2. SelectionPolicy Concept",
    "## 3. Policy vs Budget",
    "## 4. Conversation-level",
    "## 5. Project-level",
    "## 6. Model-level",
    "## 7. Global-level",
    "## 8. Request-level",
    "## 9. Recommended Boundary",
    "## 10. Model Window vs History Budget",
    "## 11. Override Concept",
    "## 12. Validation",
    "## 13. Failure Semantics",
    "## 14. Security",
    "## 15. Project Isolation",
    "## 16. Deferred Decisions",
)

_REQUIRED_DOC_STATEMENTS: tuple[str, ...] = (
    "SelectionPolicy = Deferred",
    "Budget = Deferred",
    "Tokenizer = Deferred",
    "Selector = Deferred",
)


# ============================================================
# 测试内概念表达（**非 production DTO**）
# ============================================================


@dataclasses.dataclass(frozen=True)
class FakeSelectionPolicy:
    """表达"怎么选"（概念演示；非 production DTO）。"""

    strategy: str
    recent_n: int | None = None
    keep_first: bool = False


@dataclasses.dataclass(frozen=True)
class FakeBudget:
    """表达"最多允许多少"（概念演示；非 production 配置）。"""

    budget_chars: int | None = None


@dataclasses.dataclass(frozen=True)
class FakeWindow:
    """概念演示：抽象预算单元（**非 token 估算**，无 tokenizer）。"""

    window_units: int
    reserved_units: int


class FakePolicySelector:
    """按 FakeSelectionPolicy 选子集（测试用；非 production Selector）。"""

    def __init__(self, policy: FakeSelectionPolicy) -> None:
        self._policy = policy

    def select(self, turns: object) -> tuple[ConversationTurnView, ...]:
        history = list(turns)  # type: ignore[arg-type]
        selected = history
        if self._policy.strategy in {"recent_n", "recent_plus_first"}:
            limit = self._policy.recent_n or 0
            selected = selected[-limit:] if limit > 0 else []
        if self._policy.keep_first and history and (
            not selected or selected[0] is not history[0]
        ):
            selected = [history[0], *selected]
        return tuple(selected)


def validate_policy(policy: FakeSelectionPolicy) -> None:
    """Policy 校验（演示 invalid policy；非 production validator）。"""
    allowed_strategies = {"all", "recent_n", "recent_plus_first"}
    if policy.strategy not in allowed_strategies:
        raise ValueError(f"非法 strategy: {policy.strategy!r}")
    if policy.strategy in {"recent_n", "recent_plus_first"}:
        if policy.recent_n is None or policy.recent_n < 1:
            raise ValueError(f"recent_n 必须是 >= 1 的整数（当前: {policy.recent_n}）")


def _history(count: int) -> list[ConversationTurnView]:
    return [
        ConversationTurnView(
            turn_id=2000 + index,
            conversation_id="conv-policy",
            role="USER" if index % 2 == 0 else "ASSISTANT",
            content=f"m{index:04d}",
            assistant_request_id=None if index % 2 == 0 else f"req-{index}",
            created_at=_BASE,
        )
        for index in range(count)
    ]


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def _identifiers(relative: str) -> set[str]:
    tree = ast.parse(_source(relative))
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


def _mapped_field_names(relative: str) -> set[str]:
    """提取 ``name: Mapped[...] = mapped_column(...)`` 的字段名。"""
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
# 1. Policy 与 Budget 概念独立
# ============================================================


class TestPolicyBudgetSeparation:
    def test_01_policy_and_budget_are_independent_concepts(self) -> None:
        """Policy 回答"怎么选"；Budget 回答"最多多少" —— 字段不相交。"""
        policy_fields = set(FakeSelectionPolicy.__dataclass_fields__)
        budget_fields = set(FakeBudget.__dataclass_fields__)

        assert policy_fields & budget_fields == set()
        assert "strategy" in policy_fields
        assert "budget_chars" in budget_fields

    def test_02_policy_plus_budget_compose_selection(self) -> None:
        """组合演示：policy 决定策略，budget 决定上限（本 Step 不实现融合逻辑）。"""
        policy = FakeSelectionPolicy(strategy="recent_n", recent_n=2)
        budget = FakeBudget(budget_chars=1000)

        selected = FakePolicySelector(policy).select(_history(4))

        assert [turn.content for turn in selected] == ["m0002", "m0003"]
        assert budget.budget_chars == 1000  # budget 独立存在（未参与本演示选择）

    def test_03_effective_budget_is_min_of_requested_and_available(self) -> None:
        """available_history_budget = window - reserves；effective = min(requested, available)。

        单位 = 抽象预算单元（**非 token 估算**；本 Step 不引入 tokenizer）。
        """
        window = FakeWindow(window_units=1000, reserved_units=400)
        available = window.window_units - window.reserved_units

        assert available == 600
        requested = 700
        assert min(requested, available) == available
        assert min(300, available) == 300


# ============================================================
# 2. Ownership（§八：Conversation 不拥有 Budget）
# ============================================================


class TestOwnershipBoundary:
    def test_04_conversation_orm_has_no_budget_fields(self) -> None:
        fields = _mapped_field_names(_ORM)

        assert fields == set(_FROZEN_CONVERSATION_FIELDS)
        for forbidden in _FORBIDDEN_BUDGET_FIELD_NAMES:
            assert forbidden not in fields, forbidden

    def test_05_conversation_service_has_no_policy(self) -> None:
        identifiers = _identifiers(_CONVERSATION_SERVICE)
        for forbidden in (
            "selection_policy",
            "SelectionPolicy",
            "context_budget",
            "max_turns",
            "ContextSelector",
        ):
            assert forbidden not in identifiers, forbidden

        methods = {
            name
            for name in vars(ConversationService)
            if not name.startswith("_") and callable(getattr(ConversationService, name))
        }
        assert methods == {
            "create_conversation",
            "get_conversation",
            "archive_conversation",
            "append_turn",
            "list_turns",
        }
        for name in methods:
            params = inspect.signature(getattr(ConversationService, name)).parameters
            for forbidden in ("policy", "budget", "max_turns"):
                assert forbidden not in params, f"{name}:{forbidden}"

    def test_06_application_service_is_future_policy_composition_boundary(self) -> None:
        """AppService 未来可组合 policy，但当前：无 policy 依赖、签名不泄漏 policy。"""
        identifiers = _identifiers(_APPLICATION_SERVICE)
        for forbidden in (
            "SelectionPolicy",
            "selection_policy",
            "ContextSelector",
            "context_budget",
        ):
            assert forbidden not in identifiers, forbidden

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
        }

    def test_07_no_production_policy_module_exists(self) -> None:
        for name in (
            "conversation_context_policy.py",
            "conversation_selection_policy.py",
            "conversation_context_selector.py",
        ):
            assert not (_REPO_ROOT / "backend/app/services" / name).exists(), name


# ============================================================
# 3. Request override / API 边界
# ============================================================


class TestRequestOverrideBoundary:
    def test_08_message_api_exposes_no_policy_field(self) -> None:
        assert list(ConversationMessageRequest.model_fields) == ["content"]

        schema = app.openapi()["paths"][
            "/api/conversations/{conversation_id}/messages"
        ]["post"]
        body_ref = schema["requestBody"]["content"]["application/json"]["schema"][
            "$ref"
        ]
        assert body_ref.endswith("/ConversationMessageRequest")
        properties = app.openapi()["components"]["schemas"][
            "ConversationMessageRequest"
        ]["properties"]
        for forbidden in _FORBIDDEN_BUDGET_FIELD_NAMES + ("policy", "budget", "strategy"):
            assert forbidden not in properties, forbidden

    def test_09_request_override_is_design_only(self) -> None:
        """优先级链（设计）：request explicit → project default → global default →
        model constraint（上限）。本 Step 只冻结概念，不存在任何实现。"""
        precedence = (
            "request explicit policy",
            "project default policy",
            "global default policy",
            "model capability constraint",
        )
        assert len(set(precedence)) == 4
        assert "model capability constraint" in precedence[-1]


# ============================================================
# 4. Validation / Failure Semantics
# ============================================================


class TestValidationAndFailure:
    def test_10_invalid_policy_is_not_selection_failure(self) -> None:
        """invalid policy（配置阶段）与 selection failure（运行阶段）必须区分。"""
        with pytest.raises(ValueError) as invalid:
            validate_policy(FakeSelectionPolicy(strategy="recent_n", recent_n=-1))
        assert "recent_n" in str(invalid.value)

        with pytest.raises(ValueError):
            validate_policy(FakeSelectionPolicy(strategy="unknown-strategy"))

        # valid policy + selector 运行时异常 → selection failure（另一类）
        class _BoomSelector:
            def select(self, turns: object) -> None:
                raise RuntimeError("runtime selection failure")

        with pytest.raises(RuntimeError) as failure:
            _BoomSelector().select(_history(2))
        assert type(failure.value) is RuntimeError

    def test_11_selection_failure_is_business_failure(self) -> None:
        """selection failure → 原样上抛（不 fallback 全量 / 不静默降级）。"""
        policy = FakeSelectionPolicy(strategy="all")
        selector = FakePolicySelector(policy)

        # 正常路径（全选）
        assert build_context(selector.select(_history(2))) is not None

        # 失败路径：原样上抛
        def _boom(turns: object) -> None:
            raise RuntimeError("selection failure")

        with pytest.raises(RuntimeError):
            _boom(_history(2))

    def test_12_policy_is_deterministic_and_serializable(self) -> None:
        policy = FakeSelectionPolicy(strategy="recent_plus_first", recent_n=2, keep_first=True)
        selector = FakePolicySelector(policy)
        history = _history(5)

        assert selector.select(history) == selector.select(history)
        assert dataclasses.is_dataclass(policy)
        payload: dict[str, Any] = dataclasses.asdict(policy)
        assert payload == {
            "strategy": "recent_plus_first",
            "recent_n": 2,
            "keep_first": True,
        }
        with pytest.raises(dataclasses.FrozenInstanceError):
            policy.recent_n = 9  # type: ignore[misc]

    def test_13_policy_forbids_callables_runtime_objects(self) -> None:
        """Policy 必须是纯数据（禁止 callable / lambda / DB / LLM 句柄）。"""
        policy = FakeSelectionPolicy(strategy="recent_n", recent_n=2)
        for value in dataclasses.astuple(policy):
            assert not callable(value), value
        for field_info in dataclasses.fields(policy):
            annotation = str(field_info.type)
            for forbidden in ("Callable", "callable", "Session", "Client"):
                assert forbidden not in annotation, forbidden


# ============================================================
# 5. Security / 概念边界
# ============================================================


class TestSecurityBoundary:
    def test_14_policy_fields_are_behavior_only(self) -> None:
        fields = set(FakeSelectionPolicy.__dataclass_fields__) | set(
            FakeBudget.__dataclass_fields__
        )
        assert fields <= set(_ALLOWED_POLICY_FIELDS), fields
        for forbidden in _FORBIDDEN_POLICY_FIELDS:
            assert forbidden not in fields, forbidden

    def test_15_audit_file_has_no_infrastructure_dependency(self) -> None:
        modules: set[str] = set()
        for node in ast.walk(ast.parse(_source(_SELF))):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        for module in modules:
            for forbidden in (
                "sqlalchemy",
                "psycopg",
                "httpx",
                "openai",
                "tiktoken",
                "backend.app.llm",
                "backend.app.rag",
                "backend.app.tools",
            ):
                assert not module.startswith(forbidden), module

    def test_16_project_isolation_is_not_authorization(self) -> None:
        """Policy 不得改变 project_id；Project binding ≠ Authorization。"""
        doc = _source(_AUDIT_DOC)
        assert "conversation.project_id" in doc
        assert "不等于授权" in doc or "≠" in doc


# ============================================================
# 6. Documentation（§十九）
# ============================================================


class TestAuditDocument:
    def test_17_required_sections(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in _REQUIRED_DOC_SECTIONS:
            assert section in doc, section

    def test_18_required_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for statement in _REQUIRED_DOC_STATEMENTS:
            assert statement in doc, statement

    def test_19_doc_records_existing_segment_budgets(self) -> None:
        """如实记录既有（非 History）预算先例，避免误认为 History 已有预算。"""
        doc = _source(_AUDIT_DOC)
        for evidence in (
            "DEFAULT_MAX_CONTEXT_CHARS",
            "DEFAULT_MAX_CHARS",
            "DEFAULT_CONTEXT_MAX_CHARS",
        ):
            assert evidence in doc, evidence


__all__ = [
    "FakeBudget",
    "FakePolicySelector",
    "FakeSelectionPolicy",
    "FakeWindow",
    "TestPolicyBudgetSeparation",
    "TestOwnershipBoundary",
    "TestRequestOverrideBoundary",
    "TestValidationAndFailure",
    "TestSecurityBoundary",
    "TestAuditDocument",
]
