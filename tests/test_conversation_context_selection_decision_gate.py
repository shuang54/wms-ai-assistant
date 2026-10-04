"""Selection Strategy Decision Gate（Phase 4.1 Step 22 —— **Audit only**）。

本文件性质：

    * 只建立 **test-local / documentation concept** 的 Decision Gate
      （**不是** production DTO / 不是 ContextSelector 实现）；
    * 冻结"何时才允许正式决定 Selection Strategy"的前置条件；
    * 当前结论（预期正确结果）：

```text
Selection Strategy Decision = BLOCKED
```

    * **纯离线**：DB = 0 · Network = 0 · LLM = 0 · Tokenizer = 0；
    * 冻结来源：``docs/evaluation/Phase 4.1 Step 22 — Selection Strategy Decision Gate.md``。

Gate 状态值域：``READY`` / ``INSUFFICIENT`` / ``BLOCKED``（无分数、无排名）。
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from backend.app.services.conversation_context_builder import build_context

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_selection_decision_gate.py"
_AUDIT_DOC = (
    "docs/evaluation/Phase 4.1 Step 22 — Selection Strategy Decision Gate.md"
)

_ORM = "backend/app/db/models/conversation.py"
_TURN_ORM = "backend/app/db/models/conversation_turn.py"
_REPOSITORY = "backend/app/db/conversation_repository.py"
_CONVERSATION_SERVICE = "backend/app/services/conversation_service.py"
_BUILDER = "backend/app/services/conversation_context_builder.py"
_APPLICATION_SERVICE = "backend/app/services/chat_application_service.py"

_PRODUCTION_AUDIT_DIRS: tuple[str, ...] = (
    "backend/app/services",
    "backend/app/db",
    "backend/app/dto",
)

#: Gate 状态值域（无分数 / 无排名）。
GATE_STATES: tuple[str, ...] = ("READY", "INSUFFICIENT", "BLOCKED")

#: 允许冻结 Strategy 前必须满足的 Gate（G8 视 token-level enforcement 需要而附加）。
_REQUIRED_FOR_STRATEGY_FREEZE: tuple[str, ...] = (
    "G1",
    "G2",
    "G3",
    "G4",
    "G5",
    "G6",
    "G7",
    "G9",
    "G10",
)

_REQUIRED_DOC_SECTIONS: tuple[str, ...] = (
    "## 1. Purpose",
    "## 2. Current State",
    "## 3. Gate Matrix",
    "## 4. G1–G10 Definitions",
    "## 5. Current Status",
    "## 6. Required Evidence",
    "## 7. Transition to READY",
    "## 8. Production Boundary",
    "## 9. Security",
    "## 10. Failure Semantics",
    "## 11. Deferred",
)

_REQUIRED_DOC_STATEMENTS: tuple[str, ...] = (
    "Selection Strategy Decision = BLOCKED",
    "Selection Strategy = BLOCKED",
    "synthetic dataset ≠ production evidence",
    "evidence ≠ policy",
    "Tokenizer = Deferred",
    "ContextSelector = Deferred",
    "Truncation = Deferred",
    "Budget Unit = Deferred",
)

_FORBIDDEN_SCORING_MARKERS: tuple[str, ...] = (
    "score",
    "ranking",
    "winner",
    "BEST",
    "%",
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
    "tokenizer",
    "tiktoken",
    "truncate",
    "summarizer",
    "memory",
)

_DEFERRED_PRODUCTION_MODULES: tuple[str, ...] = (
    "conversation_context_selector.py",
    "conversation_selection_policy.py",
    "conversation_context_budget.py",
    "conversation_context_tokenizer.py",
    "conversation_truncation.py",
    "conversation_summarizer.py",
    "conversation_memory.py",
)


# ============================================================
# Decision Gate（test-local 概念；**非 production DTO**）
# ============================================================


@dataclasses.dataclass(frozen=True)
class GateCondition:
    """一条 Decision Gate 条件（test-local / documentation concept）。"""

    gate_id: str
    condition: str
    state: str
    required_evidence: str


_CURRENT_GATES: tuple[GateCondition, ...] = (
    GateCondition(
        gate_id="G1",
        condition="Real WMS dataset",
        state="BLOCKED",
        required_evidence="真实脱敏 WMS 多轮会话样本（synthetic 12 案例不构成生产证据）",
    ),
    GateCondition(
        gate_id="G2",
        condition="Length distribution",
        state="INSUFFICIENT",
        required_evidence=(
            "turn count / character length 分布（P50 / P90 / P95 / P99 / Max）；"
            "仅字符口径，禁止字符→token 推算"
        ),
    ),
    GateCondition(
        gate_id="G3",
        condition="Constraint annotation",
        state="BLOCKED",
        required_evidence=(
            "人工标注：early_constraint / middle_decision / recent_context / "
            "old_topic / standalone（evaluation annotation，非 production DTO）"
        ),
    ),
    GateCondition(
        gate_id="G4",
        condition="Loss impact evidence",
        state="BLOCKED",
        required_evidence=(
            "full_history_answer 与 selected_history_answer 的离线影响比较"
            "（回答是否因丢失 turn 而不可用）"
        ),
    ),
    GateCondition(
        gate_id="G5",
        condition="Strategy comparison",
        state="BLOCKED",
        required_evidence=(
            "Recent N / Recent + First / Hybrid / Relevance 的离线比较"
            "（Relevance 需模型就绪；当前仅有结构性模拟）"
        ),
    ),
    GateCondition(
        gate_id="G6",
        condition="Budget unit",
        state="BLOCKED",
        required_evidence=(
            "guardrail（max_turns / max_chars）与 capacity constraint"
            "（max_tokens）的归属、优先级与超限处理"
        ),
    ),
    GateCondition(
        gate_id="G7",
        condition="Context window source",
        state="BLOCKED",
        required_evidence=(
            "可信 model → context_window 映射（provider 文档 / 能力元数据；"
            "禁止猜测或从 API 响应反推）"
        ),
    ),
    GateCondition(
        gate_id="G8",
        condition="Tokenizer",
        state="BLOCKED",
        required_evidence=(
            "仅当 token-level enforcement 进入生产 capacity constraint 时才引入"
        ),
    ),
    GateCondition(
        gate_id="G9",
        condition="Reserve allocation",
        state="BLOCKED",
        required_evidence=(
            "System / Project / Tools / User / RAG / Tool Results / Output Reserve "
            "的 reserve strategy（RAG 与 Tool 为动态容量）"
        ),
    ),
    GateCondition(
        gate_id="G10",
        condition="Failure semantics",
        state="READY",
        required_evidence=(
            "invalid policy ≠ runtime selection failure；"
            "No fallback full history / No ignore budget / No silent degradation"
        ),
    ),
)


def can_freeze_strategy(
    gates: tuple[GateCondition, ...],
    *,
    token_enforcement_required: bool = False,
) -> bool:
    """只有必需 Gate 全部 READY（且按需 G8 READY）才允许冻结 Strategy。"""
    by_id = {gate.gate_id: gate for gate in gates}
    for gate_id in _REQUIRED_FOR_STRATEGY_FREEZE:
        if by_id[gate_id].state != "READY":
            return False
    if token_enforcement_required and by_id["G8"].state != "READY":
        return False
    return True


def decide_strategy_state(
    gates: tuple[GateCondition, ...] = _CURRENT_GATES,
    *,
    token_enforcement_required: bool = False,
) -> str:
    """返回 ``READY`` / ``BLOCKED``（Gate 未满足时**不得**临时选择策略）。"""
    if can_freeze_strategy(
        gates, token_enforcement_required=token_enforcement_required
    ):
        return "READY"
    return "BLOCKED"


def _with_state(
    gates: tuple[GateCondition, ...], gate_id: str, state: str
) -> tuple[GateCondition, ...]:
    return tuple(
        dataclasses.replace(gate, state=state)
        if gate.gate_id == gate_id
        else gate
        for gate in gates
    )


def _all_ready() -> tuple[GateCondition, ...]:
    return tuple(
        dataclasses.replace(gate, state="READY") for gate in _CURRENT_GATES
    )


def _gate(gate_id: str) -> GateCondition:
    return next(gate for gate in _CURRENT_GATES if gate.gate_id == gate_id)


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
# 1. Decision Gate 当前状态（§二十一 1~11）
# ============================================================


class TestCurrentGateState:
    def test_01_current_decision_is_blocked(self) -> None:
        assert decide_strategy_state() == "BLOCKED"

    def test_02_g1_real_wms_dataset_blocked(self) -> None:
        gate = _gate("G1")
        assert gate.state == "BLOCKED"
        assert "真实" in gate.required_evidence

    def test_03_g2_length_distribution_insufficient(self) -> None:
        gate = _gate("G2")
        assert gate.state == "INSUFFICIENT"
        for marker in ("P50", "P90", "P95", "P99", "Max"):
            assert marker in gate.required_evidence, marker

    def test_04_g3_constraint_annotation_blocked(self) -> None:
        gate = _gate("G3")
        assert gate.state == "BLOCKED"
        for annotation_type in (
            "early_constraint",
            "middle_decision",
            "recent_context",
            "old_topic",
            "standalone",
        ):
            assert annotation_type in gate.required_evidence, annotation_type

    def test_05_g4_loss_impact_evidence_blocked(self) -> None:
        gate = _gate("G4")
        assert gate.state == "BLOCKED"
        assert "full_history_answer" in gate.required_evidence
        assert "selected_history_answer" in gate.required_evidence

    def test_06_g5_strategy_comparison_blocked(self) -> None:
        gate = _gate("G5")
        assert gate.state == "BLOCKED"
        assert "Relevance" in gate.required_evidence

    def test_07_g6_budget_unit_blocked(self) -> None:
        gate = _gate("G6")
        assert gate.state == "BLOCKED"
        for marker in ("guardrail", "max_turns", "max_chars", "max_tokens"):
            assert marker in gate.required_evidence, marker

    def test_08_g7_context_window_source_blocked(self) -> None:
        gate = _gate("G7")
        assert gate.state == "BLOCKED"
        assert "context_window" in gate.required_evidence
        assert "禁止猜测" in gate.required_evidence

    def test_09_g8_tokenizer_blocked(self) -> None:
        gate = _gate("G8")
        assert gate.state == "BLOCKED"
        assert "token-level enforcement" in gate.required_evidence

    def test_10_g9_reserve_allocation_blocked(self) -> None:
        gate = _gate("G9")
        assert gate.state == "BLOCKED"
        for segment in ("System", "Project", "Tools", "User", "RAG", "Output Reserve"):
            assert segment in gate.required_evidence, segment

    def test_11_g10_failure_semantics_ready(self) -> None:
        gate = _gate("G10")
        assert gate.state == "READY"
        assert "invalid policy" in gate.required_evidence


# ============================================================
# 2. 状态迁移（§十六 允许冻结的条件）
# ============================================================


class TestTransitionConditions:
    def test_12_cannot_transition_to_ready_when_blocked(self) -> None:
        # 只把 G1 置 READY：仍 BLOCKED
        partly = _with_state(_CURRENT_GATES, "G1", "READY")
        assert decide_strategy_state(partly) == "BLOCKED"

        # 除 G6 外全部 READY：仍 BLOCKED
        almost = _with_state(_all_ready(), "G6", "BLOCKED")
        assert decide_strategy_state(almost) == "BLOCKED"

        # 全部 READY：READY
        assert decide_strategy_state(_all_ready()) == "READY"

    def test_13_token_enforcement_requires_g8_ready(self) -> None:
        all_ready = _all_ready()

        # 需要 token-level enforcement 且 G8 BLOCKED → 仍 BLOCKED
        g8_blocked = _with_state(all_ready, "G8", "BLOCKED")
        assert (
            decide_strategy_state(g8_blocked, token_enforcement_required=True)
            == "BLOCKED"
        )
        # 不需要 token-level enforcement 时 G8 不影响
        assert (
            decide_strategy_state(g8_blocked, token_enforcement_required=False)
            == "READY"
        )

    def test_14_g10_alone_is_not_sufficient(self) -> None:
        gates = _with_state(_CURRENT_GATES, "G10", "READY")
        assert decide_strategy_state(gates) == "BLOCKED"

    def test_15_gate_states_domain_is_closed(self) -> None:
        for gate in _CURRENT_GATES:
            assert gate.state in GATE_STATES, gate.gate_id

    def test_16_gate_conditions_are_frozen(self) -> None:
        gate = _gate("G1")
        with pytest.raises(dataclasses.FrozenInstanceError):
            gate.state = "READY"  # type: ignore[misc]


# ============================================================
# 3. Production Boundary（§十九 / §二十）
# ============================================================


class TestProductionBoundary:
    def test_17_no_production_selector_budget_tokenizer_modules(self) -> None:
        for name in _DEFERRED_PRODUCTION_MODULES:
            for directory in ("services", "db", "dto"):
                path = _REPO_ROOT / "backend/app" / directory / name
                assert not path.exists(), f"{directory}/{name}"

    def test_18_existing_chain_has_no_forbidden_identifiers(self) -> None:
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

    def test_19_no_tokenizer_dependency_in_chain(self) -> None:
        for relative in (
            _BUILDER,
            _CONVERSATION_SERVICE,
            _REPOSITORY,
            _APPLICATION_SERVICE,
        ):
            modules = _module_imports(relative)
            assert modules, f"AST 未解析到 import: {relative}"
            for module in modules:
                for prefix in (
                    "tiktoken",
                    "transformers",
                    "tokenizers",
                    "sentencepiece",
                ):
                    assert not module.startswith(prefix), f"{relative}:{module}"

    def test_20_no_budget_or_policy_dto_module(self) -> None:
        dto_files = sorted(
            path.name for path in (_REPO_ROOT / "backend/app/dto").glob("*.py")
        )
        assert dto_files, "dto 目录为空（审计失效）"
        for name in dto_files:
            for forbidden in ("budget", "policy", "selector", "token"):
                assert forbidden not in name, name

    def test_21_builder_still_formats_full_input(self) -> None:
        """Builder 仍无选择 / 预算参数（Decision Gate 未改变生产边界）。"""
        assert list(inspect.signature(build_context).parameters) == ["turns"]

    def test_22_persistence_fields_unchanged(self) -> None:
        assert _mapped_field_names(_ORM) == {
            "conversation_id",
            "project_id",
            "created_at",
            "updated_at",
            "status",
        }
        assert _mapped_field_names(_TURN_ORM) == {
            "turn_id",
            "conversation_id",
            "role",
            "content",
            "assistant_request_id",
            "created_at",
        }


# ============================================================
# 4. Determinism / 离线 / 文档
# ============================================================


class TestDeterminismAndDocument:
    def test_23_gate_state_is_deterministic(self) -> None:
        assert decide_strategy_state() == decide_strategy_state()
        assert can_freeze_strategy(_CURRENT_GATES) is False
        assert can_freeze_strategy(_all_ready()) is True

    def test_24_no_db_network_llm_in_gate_file(self) -> None:
        modules = _module_imports(_SELF)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for forbidden in (
                "sqlalchemy",
                "psycopg",
                "httpx",
                "requests",
                "openai",
                "backend.app.db",
            ):
                assert not module.startswith(forbidden), module

    def test_25_gate_matrix_is_complete(self) -> None:
        gate_ids = [gate.gate_id for gate in _CURRENT_GATES]
        assert gate_ids == [f"G{index}" for index in range(1, 11)]
        assert len({gate.condition for gate in _CURRENT_GATES}) == 10

    def test_26_document_sections_and_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in _REQUIRED_DOC_SECTIONS:
            assert section in doc, section
        for statement in _REQUIRED_DOC_STATEMENTS:
            assert statement in doc, statement

    def test_27_document_has_no_scoring_or_ranking(self) -> None:
        doc = _source(_AUDIT_DOC)
        for forbidden in _FORBIDDEN_SCORING_MARKERS:
            assert forbidden not in doc, forbidden

    def test_28_document_records_gate_states(self) -> None:
        doc = _source(_AUDIT_DOC)
        for gate in _CURRENT_GATES:
            assert gate.gate_id in doc, gate.gate_id
            assert gate.state in doc, gate.gate_id
        assert "BLOCKED" in doc

    def test_29_statistics_are_evidence_not_policy(self) -> None:
        """§十七：统计结果（如 P95）只能作为 evidence，不能直接成为 policy。"""
        doc = _source(_AUDIT_DOC)
        assert "evidence ≠ policy" in doc
        for example in ("max_turns = 18", "max_chars = 12000"):
            assert example in doc, example

    def test_30_gate_concept_is_test_local_only(self) -> None:
        """Gate 概念只存在于 tests/ 与 docs/（无 production 模块）。"""
        for name in (
            "selection_decision_gate.py",
            "context_selection_gate.py",
        ):
            for directory in _PRODUCTION_AUDIT_DIRS:
                assert not (_REPO_ROOT / directory / name).exists(), (
                    f"{directory}/{name}"
                )
        assert "GateCondition" in _source(_SELF)


__all__ = [
    "GATE_STATES",
    "GateCondition",
    "can_freeze_strategy",
    "decide_strategy_state",
    "TestCurrentGateState",
    "TestTransitionConditions",
    "TestProductionBoundary",
    "TestDeterminismAndDocument",
]
