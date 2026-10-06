"""History Selection Strategy Decision Audit（Phase 4.1 Step 20 —— **Audit only**）。

本文件性质：

    * **不实现** ContextSelector / Truncation / Tokenizer / Budget DTO；
    * 用测试内策略函数（A / B / C / D）验证**定义与不变量**，并用 5 个离线 WMS 场景
      分析各策略的保留行为（**不打分 / 不排名**）；
    * **纯离线**：DB = 0 · Network = 0 · LLM = 0 · Tokenizer = 0；
    * 冻结来源：``docs/evaluation/Phase 4.1 Step 20 — History Selection Strategy Decision Audit.md``。

最终决策（本 Step）：**Final Selection Strategy = Deferred**（证据不足，不硬选）。
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
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
_SELF = "tests/test_conversation_context_selection_strategy_audit.py"
_ORM = "backend/app/db/models/conversation.py"
_REPOSITORY = "backend/app/db/conversation_repository.py"
_CONVERSATION_SERVICE = "backend/app/services/conversation_service.py"
_BUILDER = "backend/app/services/conversation_context_builder.py"
_APPLICATION_SERVICE = "backend/app/services/chat_application_service.py"
_AUDIT_DOC = (
    "docs/evaluation/Phase 4.1 Step 20 — History Selection Strategy Decision Audit.md"
)

_BASE = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)

#: 候选策略（冻结为四种，不新增第五种）。
_CANDIDATE_STRATEGIES: tuple[str, ...] = (
    "Strategy A: Recent N",
    "Strategy B: Recent N + First Turn",
    "Strategy C: Relevance / Token Budget Selection",
    "Strategy D: Hybrid",
)

#: Selection Quality Matrix 维度（不打分；只使用标签域）。
_MATRIX_DIMENSIONS: tuple[str, ...] = (
    "Early Constraint Preservation",
    "Recent Context Preservation",
    "Middle Decision Preservation",
    "Long Turn Safety",
    "Determinism",
    "Implementation Complexity",
    "Tokenizer Dependency",
)

#: 矩阵允许的标签（禁止分数 / 排名）。
_MATRIX_LABELS: tuple[str, ...] = (
    "Good fit",
    "Weak fit",
    "Requires future capability",
    "Risk",
)

#: WMS 场景名（文档必须覆盖 5 个）。
_WMS_SCENARIOS: tuple[str, ...] = (
    "Scenario 1：连续追问",
    "Scenario 2：早期业务约束",
    "Scenario 3：中间决策",
    "Scenario 4：长文本 Turn",
    "Scenario 5：重新回到早期主题",
)

_REQUIRED_DOC_SECTIONS: tuple[str, ...] = (
    "## 1. Current State",
    "## 2. Candidate Strategies",
    "## 3. Selection Quality Matrix",
    "## 4. WMS Scenarios",
    "## 5. Invariants",
    "## 6. Failure Semantics",
    "## 7. Budget Separation",
    "## 8. Deferred",
)

_REQUIRED_DOC_STATEMENTS: tuple[str, ...] = (
    "Current History Budget = Unlimited",
    "Strategy ≠ Budget Unit",
    "Invalid policy ≠ runtime selection failure",
    "No fallback full history",
    "No ignore budget",
    "No silent degradation",
    "ContextSelector = Deferred",
    "Truncation = Deferred",
    "Tokenizer = Deferred",
    "Budget Unit = Deferred",
    "Final Selection Strategy = Deferred",
)

#: 禁止出现在矩阵中的评分 / 排名词（架构决策不是 benchmark）。
_FORBIDDEN_SCORING_MARKERS: tuple[str, ...] = (
    "BEST",
    "WINNER",
    "/10",
    "score",
)

#: Production 边界文件（不得出现 selector / policy / budget / tokenizer）。
_PRODUCTION_FILES: tuple[str, ...] = (
    _ORM,
    "backend/app/db/models/conversation_turn.py",
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
    "context_budget",
    "max_turns",
    "max_chars",
    "max_tokens",
    "tokenizer",
    "tiktoken",
)


# ============================================================
# 测试内策略函数（**非 production Selector**）
# ============================================================


def _recent_n(
    history: list[ConversationTurnView], n: int
) -> tuple[ConversationTurnView, ...]:
    if n < 1:
        raise ValueError(f"recent_n 必须 >= 1（当前: {n}）")  # invalid policy
    return tuple(history[-n:])


def _recent_plus_first(
    history: list[ConversationTurnView], n: int
) -> tuple[ConversationTurnView, ...]:
    selected = _recent_n(history, n)
    if history and (not selected or selected[0] is not history[0]):
        return (history[0], *selected)
    return selected


def _relevance_token_selection(
    history: list[ConversationTurnView], query: str
) -> tuple[ConversationTurnView, ...]:
    """Strategy C：当前不具备实现条件（4 个前置全部缺失）。"""
    raise NotImplementedError(
        "Strategy C 需要 Tokenizer / Model Context Window / Budget Unit / Relevance Model"
    )


def _hybrid(
    history: list[ConversationTurnView],
    n: int,
    *,
    budget_defined: bool = False,
) -> tuple[ConversationTurnView, ...]:
    """Strategy D：先 A+B；若已定义 token budget，超限裁剪（未定义 → 不做裁剪）。"""
    selected = _recent_plus_first(history, n)
    if budget_defined:
        raise NotImplementedError(
            "Hybrid 裁剪需要 Token Budget（Budget Unit = Deferred）"
        )
    return selected


@dataclasses.dataclass(frozen=True)
class StrategySpec:
    """候选策略说明卡（测试内概念；非 production DTO）。"""

    name: str
    definition: str
    requires_tokenizer: bool
    requires_future_capability: bool


_STRATEGY_SPECS: tuple[StrategySpec, ...] = (
    StrategySpec(
        name="Strategy A: Recent N",
        definition="保留最近 N 个历史 Turn",
        requires_tokenizer=False,
        requires_future_capability=False,
    ),
    StrategySpec(
        name="Strategy B: Recent N + First Turn",
        definition="保留 First Turn + 最近 N 个历史 Turn",
        requires_tokenizer=False,
        requires_future_capability=False,
    ),
    StrategySpec(
        name="Strategy C: Relevance / Token Budget Selection",
        definition="按当前问题相关性 + Token Budget 选择历史",
        requires_tokenizer=True,
        requires_future_capability=True,
    ),
    StrategySpec(
        name="Strategy D: Hybrid",
        definition="Recent N + First Turn + 未来 Token Budget",
        requires_tokenizer=True,
        requires_future_capability=True,
    ),
)


def _turn(index: int, role: str, content: str) -> ConversationTurnView:
    return ConversationTurnView(
        turn_id=4000 + index,
        conversation_id="conv-strategy",
        role=role,
        content=content,
        assistant_request_id=None if role == "USER" else f"req-{index}",
        created_at=_BASE,
    )


def _dialog(pairs: list[tuple[str, str]]) -> list[ConversationTurnView]:
    """(user, assistant) 对 → 交替 turn 列表。"""
    turns: list[ConversationTurnView] = []
    for index, (user, assistant) in enumerate(pairs):
        turns.append(_turn(index * 2, "USER", user))
        turns.append(_turn(index * 2 + 1, "ASSISTANT", assistant))
    return turns


def _contents(turns: tuple[ConversationTurnView, ...] | list[ConversationTurnView]) -> str:
    return "\n".join(turn.content for turn in turns)


def _positions(
    history: list[ConversationTurnView],
    selected: tuple[ConversationTurnView, ...],
) -> list[int]:
    return [history.index(turn) for turn in selected]


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
# 1. 策略定义（§四 ~ §八）
# ============================================================


class TestStrategyDefinitions:
    def test_01_recent_n_definition(self) -> None:
        history = [_turn(index, "USER", f"T{index + 1}") for index in range(8)]

        selected = _recent_n(history, 4)

        assert [turn.content for turn in selected] == ["T5", "T6", "T7", "T8"]
        assert _STRATEGY_SPECS[0].requires_tokenizer is False

    def test_02_recent_plus_first_definition(self) -> None:
        history = [_turn(index, "USER", f"T{index + 1}") for index in range(8)]

        selected = _recent_plus_first(history, 3)

        assert [turn.content for turn in selected] == ["T1", "T6", "T7", "T8"]

    def test_03_recent_n_when_n_covers_all_turns(self) -> None:
        history = [_turn(index, "USER", f"T{index + 1}") for index in range(3)]

        assert _recent_plus_first(history, 5) == tuple(history)
        assert _recent_plus_first(history, 1)[0] is history[0]

    def test_04_strategy_c_prerequisites_are_missing(self) -> None:
        """Strategy C 的 4 个前置当前全部缺失（Tokenizer / Window / Unit / Relevance）。"""
        history = _dialog([("查询库存", "库存 100 件")])

        with pytest.raises(NotImplementedError) as excinfo:
            _relevance_token_selection(history, "查询库存")

        message = str(excinfo.value)
        for prerequisite in (
            "Tokenizer",
            "Model Context Window",
            "Budget Unit",
            "Relevance Model",
        ):
            assert prerequisite in message

        spec = _STRATEGY_SPECS[2]
        assert spec.requires_tokenizer is True
        assert spec.requires_future_capability is True

    def test_05_hybrid_definition_without_budget(self) -> None:
        history = [_turn(index, "USER", f"T{index + 1}") for index in range(8)]

        selected = _hybrid(history, 3, budget_defined=False)

        assert [turn.content for turn in selected] == ["T1", "T6", "T7", "T8"]

    def test_06_hybrid_requires_budget_for_pruning(self) -> None:
        history = [_turn(index, "USER", f"T{index + 1}") for index in range(8)]

        with pytest.raises(NotImplementedError):
            _hybrid(history, 3, budget_defined=True)

    def test_07_strategy_set_is_frozen_to_four(self) -> None:
        assert len(_CANDIDATE_STRATEGIES) == 4
        assert [spec.name for spec in _STRATEGY_SPECS] == list(
            _CANDIDATE_STRATEGIES
        )


# ============================================================
# 2. Invariants（§十二：5 项，适用于所有策略）
# ============================================================


class TestStrategyInvariants:
    def test_08_subsequence_invariant(self) -> None:
        history = _dialog(
            [("查询库存", "100 件"), ("按仓库统计", "统计完成"), ("只看 A01", "A01 60 件")]
        )

        for selected in (
            _recent_n(history, 2),
            _recent_plus_first(history, 2),
            _hybrid(history, 2),
        ):
            assert len(selected) <= len(history)
            for turn in selected:
                assert any(turn is origin for origin in history)

    def test_09_ordering_invariant(self) -> None:
        history = _dialog(
            [("U1", "A1"), ("U2", "A2"), ("U3", "A3"), ("U4", "A4")]
        )

        for selected in (
            _recent_n(history, 3),
            _recent_plus_first(history, 1),
        ):
            positions = _positions(history, selected)
            assert positions == sorted(positions), positions

    def test_10_current_user_exclusion_invariant(self) -> None:
        """排除责任在 ChatApplicationService（Selector 输入 = previous turns）。"""
        tree = ast.parse(_source(_APPLICATION_SERVICE))
        comparisons = [
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
        ]
        assert any("turn.turn_id" in text for text in comparisons)

        current_question = "那 B01 呢？"
        previous = _dialog([("查询当前库存", "100 件"), ("A01 多少", "60 件")])[2:]
        selected = _recent_n(previous, 2)
        assert current_question not in _contents(selected)

    def test_11_determinism_invariant(self) -> None:
        history = _dialog([("U1", "A1"), ("U2", "A2"), ("U3", "A3")])

        assert _recent_n(history, 2) == _recent_n(history, 2)
        assert _recent_plus_first(history, 1) == _recent_plus_first(history, 1)
        assert _hybrid(history, 2) == _hybrid(history, 2)

    def test_12_no_semantic_mutation_invariant(self) -> None:
        history = _dialog([("U1", "A1"), ("U2", "A2")])
        snapshot = tuple(history)

        for selected in (_recent_n(history, 2), _recent_plus_first(history, 1)):
            for original, chosen in zip(history, selected, strict=False):
                if original is chosen:
                    assert chosen.role == original.role
                    assert chosen.content == original.content
                    assert chosen.turn_id == original.turn_id
                    assert chosen.assistant_request_id == original.assistant_request_id
                    assert chosen.created_at == original.created_at

        assert tuple(history) == snapshot


# ============================================================
# 3. WMS 场景（§十：至少 5 个离线场景）
# ============================================================


class TestWmsScenarios:
    def test_13_scenario_1_continuous_follow_up(self) -> None:
        """连续追问：Recent N 必须保留直接语境（A01 仓库）。"""
        previous = _dialog(
            [
                ("查询当前库存", "当前库存 100 件"),
                ("其中 A01 仓库多少？", "A01 有 60 件"),
            ]
        )
        # current（不进入 previous）：那 B01 呢？

        selected = _recent_n(previous, 2)

        assert "A01" in _contents(selected)

    def test_14_scenario_2_early_business_constraint(self) -> None:
        """早期业务约束：仅 Recent N 可能丢失"越南一厂"；Recent+First 保留。"""
        previous = _dialog(
            [
                ("只查询越南一厂库存", "好的，仅越南一厂"),
                ("查询库存", "库存 80 件"),
            ]
        )

        recent_only = _recent_n(previous, 2)
        assert "越南一厂" not in _contents(recent_only)  # 约束在 U1（被裁掉）

        with_first = _recent_plus_first(previous, 2)
        assert "越南一厂" in _contents(with_first)

    def test_15_scenario_3_middle_decision(self) -> None:
        """中间决策：Recent+First(N=1) 可能丢失中间约束（A01/A02）。"""
        previous = _dialog(
            [
                ("查询库存", "库存 100 件"),
                ("按仓库统计", "统计完成"),
                ("只看 A01/A02", "A01 60 / A02 40"),
            ]
        )

        with_first = _recent_plus_first(previous, 1)

        assert "只看 A01/A02" not in _contents(with_first)  # 中间决策被裁掉
        assert "查询库存" in _contents(with_first)  # 首轮保留

    def test_16_scenario_4_long_turn_safety(self) -> None:
        """长文本 Turn：相同 turn 数下字符体量可相差巨大 → max_turns ≠ capacity。"""
        short_history = _dialog([("短问题一", "短回答一"), ("短问题二", "短回答二")])
        long_history = _dialog(
            [("短问题", "短回答"), ("长问题：" + "x" * 800, "长回答：" + "y" * 800)]
        )

        short_context = build_context(_recent_n(short_history, 2)) or ""
        long_context = build_context(_recent_n(long_history, 2)) or ""

        assert len(long_context) > len(short_context) * 10  # 同 N、体量差异巨大

    def test_17_scenario_5_return_to_early_topic(self) -> None:
        """回到早期主题：Recent N 小窗口无法带回早期主题。"""
        fillers = [(f"填充问题{index}", f"填充回答{index}") for index in range(9)]
        previous = _dialog([("我们讨论采购入库流程", "采购入库流程说明…")] + fillers)
        # current（不进入 previous）：回到刚才采购入库的问题

        selected = _recent_n(previous, 4)

        assert "采购入库" not in _contents(selected)
        assert len(selected) == 4

    def test_18_scenarios_are_documented(self) -> None:
        doc = _source(_AUDIT_DOC)
        for scenario in _WMS_SCENARIOS:
            assert scenario in doc, scenario


# ============================================================
# 4. 矩阵 / 边界 / 失败语义 / 文档
# ============================================================


class TestMatrixAndBoundaries:
    def test_19_selection_quality_matrix_is_complete(self) -> None:
        doc = _source(_AUDIT_DOC)

        for dimension in _MATRIX_DIMENSIONS:
            assert dimension in doc, dimension
        for label in _MATRIX_LABELS:
            assert label in doc, label
        for strategy in _CANDIDATE_STRATEGIES:
            assert strategy in doc, strategy
        for scenario in _WMS_SCENARIOS:
            assert scenario in doc, scenario

    def test_20_matrix_has_no_scoring_or_ranking(self) -> None:
        doc = _source(_AUDIT_DOC)
        for forbidden in _FORBIDDEN_SCORING_MARKERS:
            assert forbidden not in doc, forbidden

    def test_21_no_token_estimation_in_audit(self) -> None:
        source = _source(_SELF)
        forbidden_patterns = (
            "/" + " 4",
            "/" + " 2",
            "*" + " 0.25",
        )
        for pattern in forbidden_patterns:
            assert pattern not in source, pattern
        doc = _source(_AUDIT_DOC)
        for pattern in forbidden_patterns:
            assert pattern not in doc, pattern

    def test_22_no_tokenizer_dependency(self) -> None:
        modules = _module_imports(_SELF)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in (
                "tiktoken",
                "transformers",
                "tokenizers",
                "sentencepiece",
            ):
                assert not module.startswith(prefix), module

    def test_23_no_production_selector_budget_or_tokenizer_modules(self) -> None:
        for name in (
            "conversation_context_selector.py",
            "conversation_selection_policy.py",
            "conversation_context_budget.py",
            "conversation_context_tokenizer.py",
            "conversation_summary.py",
            "conversation_memory.py",
        ):
            assert not (_REPO_ROOT / "backend/app/services" / name).exists(), name

    def test_24_persistence_boundary(self) -> None:
        assert _mapped_field_names(_ORM) == {
            "conversation_id",
            "project_id",
            "created_at",
            "updated_at",
            "status",
        }
        for relative in (_ORM, _REPOSITORY, _CONVERSATION_SERVICE):
            identifiers = _identifiers(relative)
            for forbidden in _FORBIDDEN_IDENTIFIERS:
                assert forbidden not in identifiers, f"{relative}:{forbidden}"

        methods = {
            name
            for name in vars(ConversationRepository)
            if not name.startswith("_") and not name.startswith("build_")
        }
        assert methods == {
            "create",
            "get_by_conversation_id",
            "update_status",
            "append_turn",
            "list_turns_by_conversation_id",
            # Phase 4.2 Step 6：幂等查询（只读 · 显式列）
            "find_turn_by_idempotency_key",
            "find_next_assistant_turn",
        }

    def test_25_builder_boundary(self) -> None:
        assert list(inspect.signature(build_context).parameters) == ["turns"]
        identifiers = _identifiers(_BUILDER)
        for forbidden in _FORBIDDEN_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden

    def test_26_application_service_boundary(self) -> None:
        identifiers = _identifiers(_APPLICATION_SERVICE)
        for forbidden in _FORBIDDEN_IDENTIFIERS:
            assert forbidden not in identifiers, forbidden

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

    def test_27_failure_semantics(self) -> None:
        """invalid policy（ValueError）≠ runtime selection failure（NotImplementedError/RuntimeError）。"""
        history = _dialog([("U1", "A1")])

        with pytest.raises(ValueError) as invalid:
            _recent_n(history, -1)
        assert "recent_n" in str(invalid.value)

        class _BoomSelector:
            def select(self, turns: object) -> None:
                raise RuntimeError("runtime selection failure")

        with pytest.raises(RuntimeError) as failure:
            _BoomSelector().select(history)
        assert type(failure.value) is RuntimeError

        doc = _source(_AUDIT_DOC)
        assert "Invalid policy ≠ runtime selection failure" in doc
        for forbidden_rule in (
            "No fallback full history",
            "No ignore budget",
            "No silent degradation",
        ):
            assert forbidden_rule in doc, forbidden_rule

    def test_28_strategy_is_not_budget_unit(self) -> None:
        """Strategy（怎么选）与 Budget Unit（最多多少）是两个独立决策。"""
        doc = _source(_AUDIT_DOC)
        assert "Strategy ≠ Budget Unit" in doc
        assert "Recent N + future max_tokens" in doc

    def test_29_required_doc_sections_and_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in _REQUIRED_DOC_SECTIONS:
            assert section in doc, section
        for statement in _REQUIRED_DOC_STATEMENTS:
            assert statement in doc, statement

    def test_30_current_state_documents_unlimited_history(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "Current History Budget = Unlimited" in doc
        assert "Full History" in doc


__all__ = [
    "StrategySpec",
    "TestStrategyDefinitions",
    "TestStrategyInvariants",
    "TestWmsScenarios",
    "TestMatrixAndBoundaries",
]
