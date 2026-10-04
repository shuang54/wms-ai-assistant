"""WMS Conversation Selection Evidence Audit（Phase 4.1 Step 21 —— **Audit only**）。

本文件性质：

    * 建立 **offline-only / test-local** 的 selection 模拟（**不是** production ContextSelector）；
    * 数据集：``tests/fixtures/conversation_context/wms_multiturn_conversations.yaml``
      （synthetic WMS-realistic；非生产数据）；
    * 只统计**事实型 preservation 指标**（preserved / lost / risk / n/a）——
      **不做评分 / 排名 / winner / benchmark**；
    * **纯离线**：DB = 0 · Network = 0 · LLM = 0 · Tokenizer = 0（无 token 估算）。

冻结来源：``docs/evaluation/Phase 4.1 Step 21 — WMS Conversation Selection Evidence.md``
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_selection_evidence.py"
_DATASET = "tests/fixtures/conversation_context/wms_multiturn_conversations.yaml"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 21 — WMS Conversation Selection Evidence.md"

_ALLOWED_ROLES: tuple[str, ...] = ("user", "assistant")
_FORBIDDEN_ROLES: tuple[str, ...] = ("system", "tool")
_ALLOWED_CASE_KEYS: frozenset[str] = frozenset({"id", "project_id", "turns"})
_ALLOWED_TURN_KEYS: frozenset[str] = frozenset({"role", "content"})

#: 事实型结果值域（禁止分数）。
_RESULT_VALUES: tuple[str, ...] = ("preserved", "lost", "n/a")
_RISK_VALUES: tuple[str, ...] = ("risk", "n/a")

_STRATEGIES: tuple[str, ...] = ("Recent N", "Recent + First", "Hybrid")
_DEFAULT_N = 2

_GROUP_EXPECTATIONS: dict[str, int] = {
    "A": 2,
    "B": 2,
    "C": 2,
    "D": 2,
    "E": 2,
    "F": 2,
}

_REQUIRED_DOC_SECTIONS: tuple[str, ...] = (
    "## 1. Dataset Purpose",
    "## 2. Dataset Categories",
    "## 3. Selection Simulation",
    "## 4. Preservation Evidence",
    "## 5. Key Findings",
    "## 6. Final Decision",
)

_REQUIRED_DOC_STATEMENTS: tuple[str, ...] = (
    "synthetic WMS-realistic",
    "not production data",
    "Continuous Follow-up",
    "Early Constraint",
    "Middle Decision",
    "Long Turn",
    "Return to Old Topic",
    "No History Dependency",
    "Relevance/Token = Deferred",
    "Selection Strategy = Deferred",
)

#: 文档 / 代码禁止出现的评分与排名词汇。
_FORBIDDEN_SCORING_MARKERS: tuple[str, ...] = (
    "score",
    "ranking",
    "winner",
    "BEST",
    "benchmark",
    "%",
)


# ============================================================
# Loader（Dataset contract；§十七）
# ============================================================


@dataclasses.dataclass(frozen=True)
class WmsTurn:
    role: str
    content: str


@dataclasses.dataclass(frozen=True)
class WmsConversation:
    case_id: str
    project_id: str
    turns: tuple[WmsTurn, ...]


def parse_wms_conversations(raw: object) -> tuple[WmsConversation, ...]:
    """解析 + 校验（missing id / project_id / empty turns / invalid role /
    empty content / duplicate id / 末尾必须是 user）。"""
    if not isinstance(raw, list) or not raw:
        raise ValueError("dataset 必须是非空 list")

    cases: list[WmsConversation] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("case 必须是 mapping")
        case_id = entry.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("id 缺失或非法")
        if case_id in seen:
            raise ValueError(f"duplicate conversation id: {case_id}")
        seen.add(case_id)

        project_id = entry.get("project_id")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError(f"{case_id}: project_id 缺失或非法")

        turns_raw = entry.get("turns")
        if not isinstance(turns_raw, list) or not turns_raw:
            raise ValueError(f"{case_id}: turns 不能为空")

        turns: list[WmsTurn] = []
        for turn_raw in turns_raw:
            if not isinstance(turn_raw, dict):
                raise ValueError(f"{case_id}: turn 必须是 mapping")
            role = turn_raw.get("role")
            if role not in _ALLOWED_ROLES:
                raise ValueError(
                    f"{case_id}: 非法 role: {role!r}（允许: {_ALLOWED_ROLES}）"
                )
            content = turn_raw.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"{case_id}: content 不能为空")
            turns.append(WmsTurn(role=role, content=content))

        if turns[-1].role != "user":
            raise ValueError(f"{case_id}: 最后一个 turn 必须是 user（current）")
        cases.append(
            WmsConversation(
                case_id=case_id, project_id=project_id, turns=tuple(turns)
            )
        )
    return tuple(cases)


def load_wms_multiturn_conversations(
    path: Path | None = None,
) -> tuple[WmsConversation, ...]:
    dataset_path = path if path is not None else (_REPO_ROOT / _DATASET)
    raw = yaml.safe_load(dataset_path.read_text(encoding="utf-8"))
    return parse_wms_conversations(raw)


def candidate_history(case: WmsConversation) -> tuple[WmsTurn, ...]:
    """candidate history = turns[:-1]（当前问题不进入候选历史）。"""
    return case.turns[:-1]


def current_question(case: WmsConversation) -> str:
    return case.turns[-1].content


def history_char_count(case: WmsConversation) -> int:
    return sum(len(turn.content) for turn in candidate_history(case))


def history_turn_count(case: WmsConversation) -> int:
    return len(candidate_history(case))


# ============================================================
# Strategy 模拟（offline-only；§八 ~ §十一）
# ============================================================


def recent_n(
    history: tuple[WmsTurn, ...], n: int
) -> tuple[WmsTurn, ...]:
    if n < 1:
        raise ValueError(f"recent_n 必须 >= 1（当前: {n}）")  # invalid policy
    return tuple(history[-n:])


def recent_n_plus_first(
    history: tuple[WmsTurn, ...], n: int
) -> tuple[WmsTurn, ...]:
    """first historical turn + last N historical turns（不重复 first）。"""
    selected = recent_n(history, n)
    if history and (not selected or selected[0] is not history[0]):
        return (history[0], *selected)
    return selected


def hybrid_selection(
    history: tuple[WmsTurn, ...], n: int, *, budget_defined: bool = False
) -> tuple[WmsTurn, ...]:
    """只验证 hybrid 结构；overflow resolution 需要未来 Token Budget。"""
    selected = recent_n_plus_first(history, n)
    if budget_defined:
        raise NotImplementedError(
            "Hybrid overflow resolution 需要 Token Budget（Budget Unit = Deferred）"
        )
    return selected


def relevance_token_selection(*_args: Any, **_kwargs: Any) -> tuple[WmsTurn, ...]:
    """Strategy C：当前不具备实现条件（4 个前置全部缺失）。"""
    raise NotImplementedError(
        "需要 Tokenizer / Model Context Window / Relevance Model / Budget Unit"
    )


def select(
    strategy: str, history: tuple[WmsTurn, ...], *, n: int = _DEFAULT_N
) -> tuple[WmsTurn, ...]:
    if strategy == "Recent N":
        return recent_n(history, n)
    if strategy == "Recent + First":
        return recent_n_plus_first(history, n)
    if strategy == "Hybrid":
        return hybrid_selection(history, n)
    raise ValueError(f"未知 strategy: {strategy!r}")


# ============================================================
# Preservation Metrics（§十二 / §十四）
# ============================================================


@dataclasses.dataclass(frozen=True)
class CaseExpectation:
    group: str
    history_required: bool
    early_marker: str | None = None
    middle_marker: str | None = None
    recent_marker: str | None = None
    old_topic_marker: str | None = None


_CASE_EXPECTATIONS: dict[str, CaseExpectation] = {
    # Group A：连续追问（recent marker = 最后相关 history turn）
    "inventory_followup_001": CaseExpectation(
        group="A", history_required=True, recent_marker="A01 仓库约有 320 件"
    ),
    "inbound_followup_002": CaseExpectation(
        group="A", history_required=True, recent_marker="供应商甲有 4 张"
    ),
    # Group B：早期业务约束（early marker 在 U1）
    "constraint_factory_003": CaseExpectation(
        group="B",
        history_required=True,
        early_marker="只查询越南一厂库存",
        recent_marker="越南一厂当前库存约为 1000 件",
    ),
    "constraint_domestic_004": CaseExpectation(
        group="B",
        history_required=True,
        early_marker="只看国产原材料",
        recent_marker="国产原材料库存约为 500 件",
    ),
    # Group C：中间决策（middle marker 在 U3）
    "middle_scope_005": CaseExpectation(
        group="C",
        history_required=True,
        middle_marker="只看 A01 和 A02",
        recent_marker="A01 约 320 件 / A02 约 180 件",
    ),
    "middle_period_006": CaseExpectation(
        group="C",
        history_required=True,
        middle_marker="只看出库量前 10 的物料",
        recent_marker="出库量前 10 的物料已列出",
    ),
    # Group D：长 Turn（long turn 在 U1）
    "long_turn_007": CaseExpectation(
        group="D",
        history_required=True,
        early_marker="跨仓库的库存健康度分析",
        recent_marker="A01 仓库约为 320 件",
    ),
    "long_turn_008": CaseExpectation(
        group="D",
        history_required=True,
        early_marker="PMC 排产缺口分析",
        recent_marker="未来 7 天排产已汇总",
    ),
    # Group E：回到旧主题（old topic 在 U1；recent = 最后一条 history）
    "topic_return_009": CaseExpectation(
        group="E",
        history_required=True,
        old_topic_marker="我们讨论采购入库流程",
        recent_marker="库存盘点包含冻结、清点、差异处理三个环节",
    ),
    "topic_return_010": CaseExpectation(
        group="E",
        history_required=True,
        old_topic_marker="我们讨论批次追溯",
        recent_marker="退货单包含验收、上架、结算三个环节",
    ),
    # Group F：无历史依赖（当前问题自足）
    "standalone_011": CaseExpectation(group="F", history_required=False),
    "standalone_012": CaseExpectation(group="F", history_required=False),
}


def _preservation(
    selected: tuple[WmsTurn, ...], marker: str | None
) -> str:
    if marker is None:
        return "n/a"
    if any(marker in turn.content for turn in selected):
        return "preserved"
    return "lost"


@dataclasses.dataclass(frozen=True)
class EvidenceRow:
    case_id: str
    group: str
    strategy: str
    early: str
    middle: str
    recent: str
    old_topic: str
    long_turn_risk: str


def build_evidence_matrix(
    cases: tuple[WmsConversation, ...], *, n: int = _DEFAULT_N
) -> tuple[EvidenceRow, ...]:
    rows: list[EvidenceRow] = []
    for case in cases:
        expectation = _CASE_EXPECTATIONS[case.case_id]
        history = candidate_history(case)
        for strategy in _STRATEGIES:
            selected = select(strategy, history, n=n)
            rows.append(
                EvidenceRow(
                    case_id=case.case_id,
                    group=expectation.group,
                    strategy=strategy,
                    early=_preservation(selected, expectation.early_marker),
                    middle=_preservation(selected, expectation.middle_marker),
                    recent=_preservation(selected, expectation.recent_marker),
                    old_topic=_preservation(
                        selected, expectation.old_topic_marker
                    ),
                    long_turn_risk=(
                        "risk" if expectation.group == "D" else "n/a"
                    ),
                )
            )
    return tuple(rows)


def _rows_for(
    matrix: tuple[EvidenceRow, ...], *, group: str, strategy: str
) -> tuple[EvidenceRow, ...]:
    return tuple(
        row
        for row in matrix
        if row.group == group and row.strategy == strategy
    )


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(scope="module")
def cases() -> tuple[WmsConversation, ...]:
    return load_wms_multiturn_conversations()


@pytest.fixture(scope="module")
def matrix(cases: tuple[WmsConversation, ...]) -> tuple[EvidenceRow, ...]:
    return build_evidence_matrix(cases)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


# ============================================================
# 1. Dataset Loader Contract
# ============================================================


class TestDatasetLoader:
    def test_01_dataset_loads(self, cases: tuple[WmsConversation, ...]) -> None:
        assert len(cases) >= 12
        assert len({case.case_id for case in cases}) == len(cases)
        for case in cases:
            assert case.project_id == "vietnam-wms"
            assert case.turns
            assert case.turns[-1].role == "user"

    def test_02_dataset_covers_six_groups(self, cases: tuple[WmsConversation, ...]) -> None:
        group_counts: dict[str, int] = {}
        for case in cases:
            group = _CASE_EXPECTATIONS[case.case_id].group
            group_counts[group] = group_counts.get(group, 0) + 1

        assert group_counts == _GROUP_EXPECTATIONS

    def test_03_dataset_schema_is_minimal(self) -> None:
        raw = yaml.safe_load((_REPO_ROOT / _DATASET).read_text(encoding="utf-8"))

        assert isinstance(raw, list)
        for entry in raw:
            assert set(entry) == set(_ALLOWED_CASE_KEYS), entry.get("id")
            for turn in entry["turns"]:
                assert set(turn) == set(_ALLOWED_TURN_KEYS), entry.get("id")

    def test_04_dataset_has_no_derived_fields(self) -> None:
        raw = yaml.safe_load((_REPO_ROOT / _DATASET).read_text(encoding="utf-8"))

        for entry in raw:
            for forbidden in (
                "token_count",
                "embedding",
                "relevance_score",
                "expected_sql",
                "model",
                "estimated_tokens",
            ):
                assert forbidden not in entry, f"{entry.get('id')}:{forbidden}"
                for turn in entry["turns"]:
                    assert forbidden not in turn, f"{entry.get('id')}:{forbidden}"

    def test_05_roles_are_user_and_assistant_only(
        self, cases: tuple[WmsConversation, ...]
    ) -> None:
        for case in cases:
            for turn in case.turns:
                assert turn.role in _ALLOWED_ROLES
                assert turn.role not in _FORBIDDEN_ROLES

    def test_06_loader_rejects_missing_id(self) -> None:
        with pytest.raises(ValueError):
            parse_wms_conversations(
                [{"project_id": "p", "turns": [{"role": "user", "content": "x"}]}]
            )

    def test_07_loader_rejects_missing_project_id(self) -> None:
        with pytest.raises(ValueError):
            parse_wms_conversations(
                [{"id": "c1", "turns": [{"role": "user", "content": "x"}]}]
            )

    def test_08_loader_rejects_empty_turns(self) -> None:
        with pytest.raises(ValueError):
            parse_wms_conversations(
                [{"id": "c1", "project_id": "p", "turns": []}]
            )

    def test_09_loader_rejects_invalid_role(self) -> None:
        with pytest.raises(ValueError):
            parse_wms_conversations(
                [
                    {
                        "id": "c1",
                        "project_id": "p",
                        "turns": [{"role": "system", "content": "x"}],
                    }
                ]
            )

    def test_10_loader_rejects_empty_content(self) -> None:
        with pytest.raises(ValueError):
            parse_wms_conversations(
                [
                    {
                        "id": "c1",
                        "project_id": "p",
                        "turns": [{"role": "user", "content": "   "}],
                    }
                ]
            )

    def test_11_loader_rejects_duplicate_id(self) -> None:
        case = {
            "id": "dup",
            "project_id": "p",
            "turns": [{"role": "user", "content": "x"}],
        }
        with pytest.raises(ValueError) as excinfo:
            parse_wms_conversations([case, dict(case)])
        assert "duplicate" in str(excinfo.value)

    def test_12_candidate_history_excludes_current_question(
        self, cases: tuple[WmsConversation, ...]
    ) -> None:
        for case in cases:
            history = candidate_history(case)
            current = current_question(case)
            assert len(history) == len(case.turns) - 1
            # 当前问题所在 turn 不进入 candidate history（身份级排除）
            assert all(turn is not case.turns[-1] for turn in history)
            assert case.turns[-1].content == current


# ============================================================
# 2. Strategy Simulation
# ============================================================


class TestStrategySimulation:
    def test_13_recent_n_definition(self) -> None:
        history = tuple(WmsTurn("user", f"T{index}") for index in range(8))

        assert [turn.content for turn in recent_n(history, 4)] == [
            "T4",
            "T5",
            "T6",
            "T7",
        ]

    def test_14_recent_n_plus_first_definition_and_no_duplication(self) -> None:
        history = tuple(WmsTurn("user", f"T{index}") for index in range(8))

        selected = recent_n_plus_first(history, 3)

        assert [turn.content for turn in selected] == ["T0", "T5", "T6", "T7"]
        assert len({id(turn) for turn in selected}) == len(selected)

    def test_15_history_not_longer_than_n(self) -> None:
        history = tuple(WmsTurn("user", f"T{index}") for index in range(3))

        assert recent_n_plus_first(history, 5) == history  # 无重复 first
        assert recent_n_plus_first(history, 1)[0] is history[0]

    def test_16_hybrid_structure_matches_recent_plus_first(self) -> None:
        history = tuple(WmsTurn("user", f"T{index}") for index in range(6))

        assert hybrid_selection(history, 2) == recent_n_plus_first(history, 2)

    def test_17_hybrid_overflow_requires_future_budget(self) -> None:
        history = tuple(WmsTurn("user", f"T{index}") for index in range(6))

        with pytest.raises(NotImplementedError) as excinfo:
            hybrid_selection(history, 2, budget_defined=True)
        assert "Token Budget" in str(excinfo.value)

    def test_18_strategy_c_is_deferred_with_prerequisites(self) -> None:
        with pytest.raises(NotImplementedError) as excinfo:
            relevance_token_selection()

        message = str(excinfo.value)
        for prerequisite in (
            "Tokenizer",
            "Model Context Window",
            "Relevance Model",
            "Budget Unit",
        ):
            assert prerequisite in message

    def test_19_invalid_policy_is_value_error(self) -> None:
        history = (WmsTurn("user", "T0"),)

        with pytest.raises(ValueError):
            recent_n(history, 0)
        with pytest.raises(ValueError):
            recent_n(history, -1)


# ============================================================
# 3. Preservation Metrics & Evidence Matrix
# ============================================================


class TestPreservationEvidence:
    def test_20_matrix_shape_and_value_domains(
        self, cases: tuple[WmsConversation, ...], matrix: tuple[EvidenceRow, ...]
    ) -> None:
        assert len(matrix) == len(cases) * len(_STRATEGIES)

        for row in matrix:
            assert row.early in _RESULT_VALUES
            assert row.middle in _RESULT_VALUES
            assert row.recent in _RESULT_VALUES
            assert row.old_topic in _RESULT_VALUES
            assert row.long_turn_risk in _RISK_VALUES

    def test_21_group_a_recent_context_is_preserved(
        self, matrix: tuple[EvidenceRow, ...]
    ) -> None:
        for strategy in _STRATEGIES:
            rows = _rows_for(matrix, group="A", strategy=strategy)
            assert rows
            assert all(row.recent == "preserved" for row in rows), rows

    def test_22_group_b_early_constraint_recent_n_risk(
        self, matrix: tuple[EvidenceRow, ...]
    ) -> None:
        recent_rows = _rows_for(matrix, group="B", strategy="Recent N")
        with_first_rows = _rows_for(matrix, group="B", strategy="Recent + First")

        assert all(row.early == "lost" for row in recent_rows), recent_rows
        assert all(row.early == "preserved" for row in with_first_rows), (
            with_first_rows
        )

    def test_23_group_c_middle_decision_lost_in_small_window(
        self, matrix: tuple[EvidenceRow, ...]
    ) -> None:
        for strategy in ("Recent N", "Recent + First"):
            rows = _rows_for(matrix, group="C", strategy=strategy)
            assert all(row.middle == "lost" for row in rows), (strategy, rows)

    def test_24_group_d_long_turn_risk(
        self, matrix: tuple[EvidenceRow, ...]
    ) -> None:
        for strategy in _STRATEGIES:
            rows = _rows_for(matrix, group="D", strategy=strategy)
            assert all(row.long_turn_risk == "risk" for row in rows), rows

    def test_25_group_e_old_topic_recent_n_risk(
        self, matrix: tuple[EvidenceRow, ...]
    ) -> None:
        recent_rows = _rows_for(matrix, group="E", strategy="Recent N")
        with_first_rows = _rows_for(matrix, group="E", strategy="Recent + First")

        assert all(row.old_topic == "lost" for row in recent_rows), recent_rows
        assert all(row.old_topic == "preserved" for row in with_first_rows), (
            with_first_rows
        )

    def test_26_group_f_has_no_history_dependency(
        self, matrix: tuple[EvidenceRow, ...]
    ) -> None:
        rows = _rows_for(matrix, group="F", strategy="Recent N")

        assert rows
        for row in rows:
            expectation = _CASE_EXPECTATIONS[row.case_id]
            assert expectation.history_required is False
            assert row.early == "n/a"
            assert row.middle == "n/a"
            assert row.recent == "n/a"
            assert row.old_topic == "n/a"

    def test_27_long_turn_cases_have_large_char_footprint(
        self, cases: tuple[WmsConversation, ...]
    ) -> None:
        """字符证据（**非** token 估算）：长 Turn 案例的平均每 turn 字符显著更大。"""
        group_d_avg = [
            history_char_count(case) / history_turn_count(case)
            for case in cases
            if _CASE_EXPECTATIONS[case.case_id].group == "D"
        ]
        others_avg = [
            history_char_count(case) / history_turn_count(case)
            for case in cases
            if _CASE_EXPECTATIONS[case.case_id].group != "D"
        ]

        assert min(group_d_avg) > max(others_avg) * 3

    def test_28_long_turn_recent_n_selection_drops_most_characters(
        self, cases: tuple[WmsConversation, ...]
    ) -> None:
        """Recent N=2 在长 Turn 案例上裁掉绝大部分字符（turn 数 ≠ 体量）。"""
        for case in cases:
            if _CASE_EXPECTATIONS[case.case_id].group != "D":
                continue
            history = candidate_history(case)
            selected = recent_n(history, _DEFAULT_N)
            selected_chars = sum(len(turn.content) for turn in selected)

            assert history_char_count(case) > selected_chars * 3, case.case_id

    def test_29_evidence_matrix_is_documented(self, matrix: tuple[EvidenceRow, ...]) -> None:
        doc = _source(_AUDIT_DOC)
        for strategy in _STRATEGIES:
            assert strategy in doc, strategy
        for value in ("preserved", "lost", "risk"):
            assert value in doc, value
        for case_id in sorted({row.case_id for row in matrix}):
            assert case_id in doc, case_id


# ============================================================
# 4. Invariants（§十六）
# ============================================================


class TestSelectionInvariants:
    def test_30_subsequence_and_ordering(
        self, cases: tuple[WmsConversation, ...]
    ) -> None:
        for case in cases:
            history = candidate_history(case)
            for strategy in _STRATEGIES:
                selected = select(strategy, history)
                assert len(selected) <= len(history)
                positions = [history.index(turn) for turn in selected]
                assert positions == sorted(positions), (case.case_id, strategy)
                for turn in selected:
                    assert any(turn is origin for origin in history)

    def test_31_no_duplication(self, cases: tuple[WmsConversation, ...]) -> None:
        for case in cases:
            history = candidate_history(case)
            for strategy in _STRATEGIES:
                selected = select(strategy, history)
                assert len({id(turn) for turn in selected}) == len(selected)

    def test_32_determinism(self, cases: tuple[WmsConversation, ...]) -> None:
        for case in cases:
            history = candidate_history(case)
            for strategy in _STRATEGIES:
                assert select(strategy, history) == select(strategy, history)

    def test_33_no_mutation_of_dataset(
        self, cases: tuple[WmsConversation, ...]
    ) -> None:
        snapshot = tuple(
            (case.case_id, case.project_id, tuple(case.turns)) for case in cases
        )

        for case in cases:
            for strategy in _STRATEGIES:
                select(strategy, candidate_history(case))
        build_evidence_matrix(cases)

        assert tuple(
            (case.case_id, case.project_id, tuple(case.turns)) for case in cases
        ) == snapshot

    def test_34_project_isolation_is_metadata_only(
        self, cases: tuple[WmsConversation, ...]
    ) -> None:
        """project_id 只作 dataset metadata：策略不读取 / 不修改它。"""
        for case in cases:
            assert case.project_id == "vietnam-wms"
            history = candidate_history(case)
            for strategy in _STRATEGIES:
                selected = select(strategy, history)
                assert all(turn is not case for turn in selected)
        # 策略函数签名不接受 project_id / conversation 对象
        import inspect

        for func in (recent_n, recent_n_plus_first, hybrid_selection):
            params = set(inspect.signature(func).parameters)
            assert "project_id" not in params
            assert "conversation" not in params


# ============================================================
# 5. Tokenizer / 文档
# ============================================================


class TestTokenizerAndDocument:
    def test_35_no_token_estimation(self) -> None:
        source = _source(_SELF)
        forbidden_patterns = ("/" + " 4", "/" + " 2", "*" + " 0.25")
        for pattern in forbidden_patterns:
            assert pattern not in source, pattern

        doc = _source(_AUDIT_DOC)
        for pattern in forbidden_patterns:
            assert pattern not in doc, pattern
        for forbidden in ("estimated_tokens", "token_count"):
            assert forbidden not in doc, forbidden

    def test_36_no_tokenizer_dependency(self) -> None:
        modules: set[str] = set()
        for node in ast.walk(ast.parse(_source(_SELF))):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in (
                "tiktoken",
                "transformers",
                "tokenizers",
                "sentencepiece",
            ):
                assert not module.startswith(prefix), module

    def test_37_document_sections_and_statements(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in _REQUIRED_DOC_SECTIONS:
            assert section in doc, section
        for statement in _REQUIRED_DOC_STATEMENTS:
            assert statement in doc, statement

    def test_38_document_has_no_scoring_or_ranking(self) -> None:
        doc = _source(_AUDIT_DOC)
        for forbidden in _FORBIDDEN_SCORING_MARKERS:
            assert forbidden not in doc, forbidden

    def test_39_document_records_step16_length_evidence(self) -> None:
        doc = _source(_AUDIT_DOC)
        for evidence in ("26", "294", "1474", "2949", "14749", "29499"):
            assert evidence in doc, evidence


__all__ = [
    "CaseExpectation",
    "EvidenceRow",
    "WmsConversation",
    "WmsTurn",
    "build_evidence_matrix",
    "candidate_history",
    "current_question",
    "hybrid_selection",
    "load_wms_multiturn_conversations",
    "parse_wms_conversations",
    "recent_n",
    "recent_n_plus_first",
    "relevance_token_selection",
    "select",
    "TestDatasetLoader",
    "TestStrategySimulation",
    "TestPreservationEvidence",
    "TestSelectionInvariants",
    "TestTokenizerAndDocument",
]
