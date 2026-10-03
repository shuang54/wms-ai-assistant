"""Evidence Collection Procedure（Phase 4.1 Step 25 —— **Procedure only**）。

本文件冻结未来真实多轮 WMS 数据到达后，G1~G4 如何被客观转换为 evidence 的
**最小评测流程**（test-local；**非 production**）：

```text
Raw / De-identified Dataset
        ↓
Dataset Validation（EvidenceDatasetValidator）
        ↓
G1 Real Dataset Readiness
        ↓
G2 Length Distribution（仅字符口径）
        ↓
G3 Context Dependency Annotation（复用 Step 24 contract）
        ↓
G4 Full vs Selected Impact Evaluation（Reference vs Candidate）
        ↓
Evidence Report（selection_strategy_decision = BLOCKED）
```

核心原则：

```text
Evidence Collection ≠ Strategy Selection
* P95 = 18 turns          不能推出 max_turns = 18；
* 75% cases lost early constraints 不能推出"使用 Hybrid"；
* 本阶段只回答"发生了什么"，不回答"应该选择什么策略"。
```

边界：DB = 0 · Network = 0 · LLM = 0 · Tokenizer = 0 ·
不实现 ContextSelector / Recent N / Hybrid / Relevance / LLM Judge / 自动评分 ·
不修改 Step 21 dataset / Step 22 gate / Step 24 rubric。
"""
from __future__ import annotations

import ast
import dataclasses
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tests.test_conversation_context_evaluation_rubric import (
    ANNOTATION_TYPES,
    DISAGREEMENT,
    IMPACT_FIELDS,
    IMPACT_VALUES,
    OUTCOME_TYPES,
    RESOLVED,
    ContextDependencyAnnotation,
    ContextImpactAnnotation,
    EvaluationSchemaError,
    requires_domain_review,
    resolve_annotation_status,
    validate_annotation,
    validate_dataset_case,
    validate_impact,
)
from tests.test_conversation_selection_evidence_data_audit import (
    EvidenceDataReadiness,
    audit_current_readiness,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_conversation_context_evidence_collection_procedure.py"
_AUDIT_DOC = "docs/evaluation/Phase 4.1 Step 25 — Evidence Collection Procedure.md"

# ============================================================
# 常量（test-local）
# ============================================================

G1_STATES: tuple[str, ...] = ("READY", "BLOCKED")
G2_STATES: tuple[str, ...] = ("READY", "INSUFFICIENT")
G3_STATES: tuple[str, ...] = ("READY", "BLOCKED")
G4_STATES: tuple[str, ...] = ("READY", "BLOCKED")

#: Evidence 类型（Step 21 synthetic 永远只能是 SYNTHETIC）。
EVIDENCE_TYPES: tuple[str, ...] = ("REAL", "SYNTHETIC")

G4_RESULT_CLASSES: tuple[str, ...] = ("NO_IMPACT", "IMPACT", "UNKNOWN")

PERCENTILES: tuple[str, ...] = ("P50", "P90", "P95", "P99")
DISTRIBUTION_KEYS: tuple[str, ...] = ("count", *PERCENTILES, "Max")

#: G3 值域：只有 bool（**不允许 unknown**；unknown 只属于 G4）。
G3_VALUE_DOMAIN: tuple[str, ...] = ("false", "true")

#: outcome_type → 比较维度（§十五：禁止 answer string equality）。
OUTCOME_COMPARISON_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "SQL_SEMANTICS": ("sql_semantics",),
    "TOOL_SELECTION": ("tool_selection",),
    "TOOL_ARGUMENTS": ("tool_arguments",),
    "ANSWER_CONTENT": ("answer_content",),
    "ROUTE": ("route",),
    "REFUSAL": ("refusal",),
}

#: 报告禁止出现 score / ranking / weight / winner 语义（§十七）。
FORBIDDEN_AGGREGATION_KEYS: tuple[str, ...] = (
    "score",
    "ranking",
    "weight",
    "winner",
    "rank",
)

#: 生产 DB 连接形态（§五：dataset 不得包含生产 DB 连接信息）。
_DB_CONNECTION_MARKERS: tuple[str, ...] = (
    "postgresql://",
    "postgres://",
    "DATABASE_URL",
    "psycopg2.connect",
)

_SELECTION_STRATEGY_DECISION = "BLOCKED"

# ============================================================
# Dataset validation（§五）
# ============================================================


class EvidenceDatasetValidator:
    """test-local dataset validator（**不进入 production**）。

    复用 Step 24 的 schema 校验，并追加：

        * no secrets / no credentials（最小 secret pattern）；
        * no production DB connection（连接串 / DATABASE_URL 形态）。
    """

    SECRET_PATTERNS: tuple[str, ...] = (
        "api_key",
        "password",
        "Authorization",
        "DATABASE_URL",
    )

    @staticmethod
    def validate_case(case: Mapping[str, Any]) -> None:
        validate_dataset_case(case)

    @classmethod
    def validate_dataset(cls, cases: Sequence[Mapping[str, Any]]) -> None:
        if not cases:
            raise EvaluationSchemaError("dataset must contain at least one case")
        seen: set[str] = set()
        for case in cases:
            cls.validate_case(case)
            case_id = str(case["case_id"])
            if case_id in seen:
                raise EvaluationSchemaError(f"duplicate case_id: {case_id}")
            seen.add(case_id)

    @staticmethod
    def contains_db_connection(payload: Any) -> bool:
        if isinstance(payload, str):
            return any(marker in payload for marker in _DB_CONNECTION_MARKERS)
        if isinstance(payload, Mapping):
            return any(
                EvidenceDatasetValidator.contains_db_connection(key)
                or EvidenceDatasetValidator.contains_db_connection(value)
                for key, value in payload.items()
            )
        if isinstance(payload, (list, tuple, set)):
            return any(
                EvidenceDatasetValidator.contains_db_connection(item)
                for item in payload
            )
        return False

    @classmethod
    def validate_no_db_connection(cls, payload: Any) -> None:
        if cls.contains_db_connection(payload):
            raise EvaluationSchemaError(
                "dataset must not contain production DB connection info"
            )


def validate_dataset_with_security(cases: Sequence[Mapping[str, Any]]) -> None:
    """Dataset validation 全流程（schema + secrets + DB 连接）。"""
    EvidenceDatasetValidator.validate_dataset(cases)
    for case in cases:
        EvidenceDatasetValidator.validate_no_db_connection(case)
        for field in ("case_id", "project_id"):
            value = str(case[field])
            for marker in EvidenceDatasetValidator.SECRET_PATTERNS:
                if marker.lower() in value.lower():
                    raise EvaluationSchemaError(f"secret-like {field}: {value}")


# ============================================================
# G1 procedure（§六）
# ============================================================


def run_g1_procedure(readiness: EvidenceDataReadiness) -> str:
    """G1：REAL_DEIDENTIFIED + 三类计数 > 0 + 无敏感字段 → READY；否则 BLOCKED。

    注意：synthetic dataset **禁止**升级为 READY。
    """
    conditions = (
        readiness.source_type == "REAL_DEIDENTIFIED",
        readiness.sample_count > 0,
        readiness.conversation_count > 0,
        readiness.turn_count > 0,
        not readiness.contains_sensitive_fields,
    )
    return "READY" if all(conditions) else "BLOCKED"


# ============================================================
# G2 procedure（§七：仅字符 / turn 口径）
# ============================================================


def percentile_nearest_rank(values: Sequence[int], percentile: int) -> int:
    """最近秩百分位（确定性；输入非空）。"""
    if not values:
        raise ValueError("percentile requires non-empty values")
    ordered = sorted(values)
    if percentile >= 100:
        return ordered[-1]
    rank = math.ceil(percentile / 100 * len(ordered))
    return ordered[max(rank, 1) - 1]


def summarize_distribution(values: Sequence[int]) -> dict[str, int]:
    """conversation-level 分布（count / P50 / P90 / P95 / P99 / Max）。"""
    if not values:
        raise ValueError("distribution requires non-empty values")
    summary = {"count": len(values)}
    for percentile in (50, 90, 95, 99):
        summary[f"P{percentile}"] = percentile_nearest_rank(values, percentile)
    summary["Max"] = max(values)
    return summary


def run_g2_procedure(
    cases: Sequence[Mapping[str, Any]], *, g1_status: str
) -> dict[str, Any]:
    """G2：只有 G1 = READY 才允许计算；否则 INSUFFICIENT。

    口径：只统计 turn / character；**禁止 chars → tokens 推算**。
    """
    if g1_status != "READY":
        return {
            "status": "INSUFFICIENT",
            "conversation_count": 0,
            "turn_distribution": {},
            "character_distribution": {},
            "turn_level": {},
        }

    turn_counts = [len(case["turns"]) for case in cases]
    character_counts = [
        sum(len(turn["content"]) for turn in case["turns"]) for case in cases
    ]
    user_turns = [
        turn for case in cases for turn in case["turns"] if turn["role"] == "user"
    ]
    assistant_turns = [
        turn
        for case in cases
        for turn in case["turns"]
        if turn["role"] == "assistant"
    ]
    return {
        "status": "READY",
        "conversation_count": len(cases),
        "turn_distribution": summarize_distribution(turn_counts),
        "character_distribution": summarize_distribution(character_counts),
        "turn_level": {
            "user_turn_count": len(user_turns),
            "assistant_turn_count": len(assistant_turns),
            "user_character_count": sum(len(t["content"]) for t in user_turns),
            "assistant_character_count": sum(
                len(t["content"]) for t in assistant_turns
            ),
        },
    }


# ============================================================
# G3 procedure（§八 ~ §十一：复用 Step 24 contract）
# ============================================================


def run_g3_procedure(
    annotations: Sequence[ContextDependencyAnnotation], *, g1_status: str
) -> dict[str, Any]:
    """G3：annotation 只能在真实数据（G1 READY）上产生。

    单位：**当前 user turn 相对于历史上下文的依赖**（不是整段 conversation 一个 label）。
    """
    if g1_status != "READY":
        return {
            "status": "BLOCKED",
            "annotation_counts": {name: 0 for name in ANNOTATION_TYPES},
            "disagreement_count": 0,
            "annotated_turn_count": 0,
        }
    counts = {name: 0 for name in ANNOTATION_TYPES}
    for annotation in annotations:
        validate_annotation(annotation)
        for name in ANNOTATION_TYPES:
            if getattr(annotation, name):
                counts[name] += 1
    return {
        "status": "READY",
        "annotation_counts": counts,
        "disagreement_count": 0,
        "annotated_turn_count": len(annotations),
    }


def count_disagreements(
    pairs: Sequence[tuple[str, str]],
) -> int:
    """§十一：两名标注者不一致 → DISAGREEMENT（禁止自动 majority vote）。"""
    return sum(
        1
        for first, second in pairs
        if resolve_annotation_status(first, second) == DISAGREEMENT
    )


def needs_domain_review(status: str) -> bool:
    return requires_domain_review(status)


# ============================================================
# G4 procedure（§十二 ~ §十七）
# ============================================================


@dataclasses.dataclass(frozen=True)
class CandidateHistory:
    """未来实验的 candidate history（**只定义接口，不实现任何策略**）。"""

    case_id: str
    strategy_id: str
    history_turn_ids: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class G4ComparisonUnit:
    """一个 case 的 G4 比较输入：reference（Full）vs candidate（Selected）。"""

    case_id: str
    outcome_type: str
    reference_outcome: str
    candidate_outcome: str


def run_g4_procedure(
    impacts: Sequence[ContextImpactAnnotation], *, g1_status: str
) -> dict[str, Any]:
    """G4：Reference vs Candidate → Impact（true / false / unknown）。"""
    if g1_status != "READY":
        return {
            "status": "BLOCKED",
            "impact_counts": {name: 0 for name in G4_RESULT_CLASSES},
            "unknown_count": 0,
            "evaluated_case_count": 0,
        }
    counts = {name: 0 for name in G4_RESULT_CLASSES}
    unknowns = 0
    for impact in impacts:
        validate_impact(impact)
        classification = classify_g4_impact(impact)
        counts[classification] += 1
        if classification == "UNKNOWN":
            unknowns += 1
    return {
        "status": "READY",
        "impact_counts": counts,
        "unknown_count": unknowns,
        "evaluated_case_count": len(impacts),
    }


def classify_g4_impact(impact: ContextImpactAnnotation) -> str:
    """§十六：NO_IMPACT / IMPACT / UNKNOWN。"""
    values = tuple(getattr(impact, name) for name in IMPACT_FIELDS)
    if any(value == "true" for value in values):
        return "IMPACT"
    if all(value == "false" for value in values):
        return "NO_IMPACT"
    return "UNKNOWN"


def comparison_dimensions(outcome_type: str) -> tuple[str, ...]:
    """§十五：按 outcome_type 比较（**禁止** answer string equality）。"""
    if outcome_type not in OUTCOME_TYPES:
        raise ValueError(f"unknown outcome_type: {outcome_type}")
    return OUTCOME_COMPARISON_DIMENSIONS[outcome_type]


# ============================================================
# Evidence Report（§十八 / §二十 / §二十一）
# ============================================================


@dataclasses.dataclass(frozen=True)
class EvidenceCollectionReport:
    """Evidence Collection 报告（test-local；**不含真实业务内容**）。"""

    dataset_version: str
    source_type: str
    evidence_type: str
    g1_status: str
    g2_status: str
    g2_turn_distribution: Mapping[str, int]
    g2_character_distribution: Mapping[str, int]
    g3_status: str
    g3_annotation_counts: Mapping[str, int]
    g3_disagreement_count: int
    g4_status: str
    g4_impact_counts: Mapping[str, int]
    g4_unknown_count: int
    selection_strategy_decision: str

    def flat_counts(self) -> dict[str, int]:
        """扁平化 count 视图（只含计数，不含业务内容）。"""
        flat: dict[str, int] = {}
        for key, value in self.g3_annotation_counts.items():
            flat[f"g3.{key}"] = value
        for key, value in self.g4_impact_counts.items():
            flat[f"g4.{key}"] = value
        return flat


def _evidence_type_for(source_type: str) -> str:
    return "REAL" if source_type == "REAL_DEIDENTIFIED" else "SYNTHETIC"


def build_evidence_report(
    *,
    dataset_version: str,
    readiness: EvidenceDataReadiness,
    g2_result: Mapping[str, Any],
    g3_result: Mapping[str, Any],
    g4_result: Mapping[str, Any],
) -> EvidenceCollectionReport:
    """组装报告；``selection_strategy_decision`` 恒为 BLOCKED。"""
    g1_status = run_g1_procedure(readiness)
    return EvidenceCollectionReport(
        dataset_version=dataset_version,
        source_type=readiness.source_type,
        evidence_type=_evidence_type_for(readiness.source_type),
        g1_status=g1_status,
        g2_status=str(g2_result["status"]),
        g2_turn_distribution=dict(g2_result.get("turn_distribution", {})),
        g2_character_distribution=dict(g2_result.get("character_distribution", {})),
        g3_status=str(g3_result["status"]),
        g3_annotation_counts=dict(g3_result.get("annotation_counts", {})),
        g3_disagreement_count=int(g3_result.get("disagreement_count", 0)),
        g4_status=str(g4_result["status"]),
        g4_impact_counts=dict(g4_result.get("impact_counts", {})),
        g4_unknown_count=int(g4_result.get("unknown_count", 0)),
        selection_strategy_decision=_SELECTION_STRATEGY_DECISION,
    )


def validate_report(report: EvidenceCollectionReport) -> None:
    """报告结构校验（含：decision 必须保持 BLOCKED）。"""
    if report.g1_status not in G1_STATES:
        raise EvaluationSchemaError(f"invalid g1_status: {report.g1_status}")
    if report.g2_status not in G2_STATES:
        raise EvaluationSchemaError(f"invalid g2_status: {report.g2_status}")
    if report.g3_status not in G3_STATES:
        raise EvaluationSchemaError(f"invalid g3_status: {report.g3_status}")
    if report.g4_status not in G4_STATES:
        raise EvaluationSchemaError(f"invalid g4_status: {report.g4_status}")
    if report.evidence_type not in EVIDENCE_TYPES:
        raise EvaluationSchemaError(f"invalid evidence_type: {report.evidence_type}")
    if report.evidence_type == "REAL" and report.source_type != "REAL_DEIDENTIFIED":
        raise EvaluationSchemaError("synthetic dataset cannot be evidence_type=REAL")
    if report.selection_strategy_decision != _SELECTION_STRATEGY_DECISION:
        raise EvaluationSchemaError(
            "selection_strategy_decision must remain BLOCKED in Step 25"
        )
    for block in (
        report.g2_turn_distribution,
        report.g2_character_distribution,
        report.g3_annotation_counts,
        report.g4_impact_counts,
    ):
        for key in block:
            lowered = str(key).lower()
            for forbidden in FORBIDDEN_AGGREGATION_KEYS:
                if forbidden in lowered:
                    raise EvaluationSchemaError(f"forbidden aggregation key: {key}")


def run_procedure_on_synthetic_dataset() -> EvidenceCollectionReport:
    """对 Step 21 synthetic dataset 运行流程（**procedure simulation**）。

    输出必须标记 ``evidence_type = SYNTHETIC``（不能是 REAL）。
    """
    readiness = audit_current_readiness()
    g1_status = run_g1_procedure(readiness)
    g2_result = run_g2_procedure((), g1_status=g1_status)
    g3_result = run_g3_procedure((), g1_status=g1_status)
    g4_result = run_g4_procedure((), g1_status=g1_status)
    return build_evidence_report(
        dataset_version="step21-synthetic-v1",
        readiness=readiness,
        g2_result=g2_result,
        g3_result=g3_result,
        g4_result=g4_result,
    )


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


def _function_names(relative: str, *, include_tests: bool = True) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(_source(relative))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if include_tests or not node.name.startswith("test_"):
                names.add(node.name)
    return names


def _valid_case(case_id: str = "case_001") -> dict[str, Any]:
    return {
        "case_id": case_id,
        "project_id": "vietnam-wms",
        "turns": [
            {"role": "user", "content": "只看 A01 仓库"},
            {"role": "assistant", "content": "已限定 A01 仓库。"},
            {"role": "user", "content": "查询库存"},
        ],
        "annotation": {
            "early_constraint": True,
            "middle_decision": False,
            "recent_context": False,
            "old_topic": False,
            "standalone": False,
        },
        "reference": {
            "outcome_type": "SQL_SEMANTICS",
            "expected_behavior": "仅统计 A01 仓库库存",
            "reference_created_by_human": True,
        },
        "impact": {
            "business_outcome_changed": "true",
            "critical_constraint_lost": "true",
            "entity_changed": "unknown",
            "intent_changed": "false",
        },
    }


def _real_readiness() -> EvidenceDataReadiness:
    return EvidenceDataReadiness(
        source_type="REAL_DEIDENTIFIED",
        sample_count=30,
        conversation_count=30,
        turn_count=180,
        has_real_origin=True,
        is_deidentified=True,
        contains_sensitive_fields=False,
        usable_for_evaluation=True,
    )


def _impact(
    *,
    business_outcome_changed: str = "false",
    critical_constraint_lost: str = "false",
    entity_changed: str = "false",
    intent_changed: str = "false",
) -> ContextImpactAnnotation:
    return ContextImpactAnnotation(
        case_id="case_001",
        full_history_result="A01 库存 320",
        selected_history_result="全部仓库库存 1000",
        business_outcome_changed=business_outcome_changed,
        critical_constraint_lost=critical_constraint_lost,
        entity_changed=entity_changed,
        intent_changed=intent_changed,
    )


# ============================================================
# 1. Dataset Validation（§五 / §二十二）
# ============================================================


class TestDatasetValidation:
    def test_01_valid_dataset_and_case(self) -> None:
        cases = [_valid_case("case_001"), _valid_case("case_002")]
        validate_dataset_with_security(cases)
        EvidenceDatasetValidator.validate_case(_valid_case())

    def test_02_missing_case_id_or_project_id(self) -> None:
        for field in ("case_id", "project_id"):
            case = _valid_case()
            if field == "case_id":
                del case[field]
            else:
                case[field] = "  "
            try:
                EvidenceDatasetValidator.validate_case(case)
            except EvaluationSchemaError:
                continue
            raise AssertionError(f"invalid {field} must be rejected")  # pragma: no cover

    def test_03_empty_turns_rejected(self) -> None:
        case = _valid_case()
        case["turns"] = []
        try:
            EvidenceDatasetValidator.validate_case(case)
        except EvaluationSchemaError:
            pass
        else:  # pragma: no cover
            raise AssertionError("empty turns must be rejected")

    def test_04_invalid_role_and_empty_content(self) -> None:
        case = _valid_case()
        case["turns"][0] = {"role": "tool", "content": "x"}
        try:
            EvidenceDatasetValidator.validate_case(case)
        except EvaluationSchemaError:
            pass
        else:  # pragma: no cover
            raise AssertionError("invalid role must be rejected")

        case = _valid_case()
        case["turns"][1] = {"role": "assistant", "content": "   "}
        try:
            EvidenceDatasetValidator.validate_case(case)
        except EvaluationSchemaError:
            pass
        else:  # pragma: no cover
            raise AssertionError("empty content must be rejected")

    def test_05_last_turn_must_be_user(self) -> None:
        case = _valid_case()
        case["turns"] = case["turns"] + [
            {"role": "assistant", "content": "全部仓库库存 1000"}
        ]
        try:
            EvidenceDatasetValidator.validate_case(case)
        except EvaluationSchemaError:
            pass
        else:  # pragma: no cover
            raise AssertionError("last turn must be user")

    def test_06_empty_dataset_and_duplicate_case_id(self) -> None:
        for bad in ((), (_valid_case("dup"), _valid_case("dup"))):
            try:
                EvidenceDatasetValidator.validate_dataset(bad)
            except EvaluationSchemaError:
                pass
            else:  # pragma: no cover
                raise AssertionError("empty/duplicate dataset must be rejected")

    def test_07_no_db_connection_or_secret_markers(self) -> None:
        assert EvidenceDatasetValidator.contains_db_connection(_valid_case()) is False
        assert (
            EvidenceDatasetValidator.contains_db_connection(
                {"note": "postgresql://user:pass@prod-db:5432/wms"}
            )
            is True
        )
        with_secret = _valid_case()
        with_secret["case_id"] = "api_key_leak"
        try:
            validate_dataset_with_security([with_secret])
        except EvaluationSchemaError:
            pass
        else:  # pragma: no cover
            raise AssertionError("secret-like case_id must be rejected")

    def test_08_validator_is_test_local(self) -> None:
        for name in (
            "evidence_dataset_validator.py",
            "evidence_collection_procedure.py",
            "evidence_report.py",
        ):
            for directory in (
                "backend/app/dto",
                "backend/app/services",
                "backend/app/db",
            ):
                assert not (_REPO_ROOT / directory / name).exists(), (
                    f"{directory}/{name}"
                )


# ============================================================
# 2. G1 Procedure（§六）
# ============================================================


class TestG1Procedure:
    def test_09_synthetic_is_blocked(self) -> None:
        assert run_g1_procedure(audit_current_readiness()) == "BLOCKED"

    def test_10_real_deidentified_is_ready(self) -> None:
        assert run_g1_procedure(_real_readiness()) == "READY"

    def test_11_sensitive_fields_block_g1(self) -> None:
        assert (
            run_g1_procedure(
                dataclasses.replace(_real_readiness(), contains_sensitive_fields=True)
            )
            == "BLOCKED"
        )

    def test_12_zero_counts_block_g1(self) -> None:
        for field in ("sample_count", "conversation_count", "turn_count"):
            blocked = dataclasses.replace(_real_readiness(), **{field: 0})
            assert run_g1_procedure(blocked) == "BLOCKED", field

    def test_13_synthetic_cannot_become_ready(self) -> None:
        synthetic = dataclasses.replace(
            _real_readiness(), source_type="SYNTHETIC_ONLY"
        )
        assert run_g1_procedure(synthetic) == "BLOCKED"


# ============================================================
# 3. G2 Procedure（§七）
# ============================================================


class TestG2Procedure:
    def test_14_g1_blocked_means_insufficient(self) -> None:
        result = run_g2_procedure([], g1_status="BLOCKED")
        assert result["status"] == "INSUFFICIENT"
        assert result["turn_distribution"] == {}
        assert result["character_distribution"] == {}

    def test_15_g1_ready_yields_percentile_fields(self) -> None:
        cases = [_valid_case(f"case_{i:03d}") for i in range(1, 5)]
        result = run_g2_procedure(cases, g1_status="READY")
        assert result["status"] == "READY"
        assert set(result["turn_distribution"]) == set(DISTRIBUTION_KEYS)
        assert set(result["character_distribution"]) == set(DISTRIBUTION_KEYS)
        assert result["turn_level"]["user_turn_count"] == 8
        assert result["turn_level"]["assistant_turn_count"] == 4
        assert result["conversation_count"] == 4

    def test_16_percentiles_are_deterministic_nearest_rank(self) -> None:
        values = [4, 1, 3, 2]
        assert percentile_nearest_rank(values, 50) == 2
        assert percentile_nearest_rank(values, 90) == 4
        assert percentile_nearest_rank(values, 95) == 4
        assert percentile_nearest_rank(values, 99) == 4
        summary = summarize_distribution(values)
        assert summary["count"] == 4
        assert summary["Max"] == 4

    def test_17_no_token_estimation_anywhere(self) -> None:
        result = run_g2_procedure([_valid_case()], g1_status="READY")
        for key in (*result, *result["turn_distribution"], *result["turn_level"]):
            assert "token" not in str(key).lower(), key
        function_names = _function_names(_SELF, include_tests=False)
        for name in function_names:
            assert "token" not in name.lower(), name

    def test_18_statistics_are_not_policy(self) -> None:
        """§七：禁止 max_turns = P95 / max_chars = P95 推导（文档冻结）。"""
        doc = _source(_AUDIT_DOC)
        assert "max_turns = P95" in doc
        assert "max_chars = P95" in doc


# ============================================================
# 4. G3 Procedure（§八 ~ §十一）
# ============================================================


class TestG3Procedure:
    def test_19_g3_requires_g1_ready(self) -> None:
        result = run_g3_procedure((), g1_status="BLOCKED")
        assert result["status"] == "BLOCKED"
        assert result["annotated_turn_count"] == 0

    def test_20_five_dimensions_counted(self) -> None:
        annotations = [
            ContextDependencyAnnotation(
                case_id="c1", current_turn_id="t3", early_constraint=True
            ),
            ContextDependencyAnnotation(
                case_id="c2", current_turn_id="t5", recent_context=True
            ),
            ContextDependencyAnnotation(
                case_id="c3", current_turn_id="t7", standalone=True
            ),
        ]
        result = run_g3_procedure(annotations, g1_status="READY")
        assert result["status"] == "READY"
        assert set(result["annotation_counts"]) == set(ANNOTATION_TYPES)
        assert result["annotation_counts"]["early_constraint"] == 1
        assert result["annotation_counts"]["recent_context"] == 1
        assert result["annotation_counts"]["standalone"] == 1
        assert result["annotated_turn_count"] == 3

    def test_21_multi_label_allowed(self) -> None:
        annotation = ContextDependencyAnnotation(
            case_id="c1",
            current_turn_id="t5",
            early_constraint=True,
            old_topic=True,
        )
        result = run_g3_procedure([annotation], g1_status="READY")
        assert result["annotation_counts"]["early_constraint"] == 1
        assert result["annotation_counts"]["old_topic"] == 1

    def test_22_unknown_not_allowed_for_g3(self) -> None:
        assert G3_VALUE_DOMAIN == ("false", "true")
        bad = ContextDependencyAnnotation(case_id="c1", current_turn_id="t1")
        object.__setattr__(bad, "early_constraint", "unknown")
        try:
            validate_annotation(bad)
        except EvaluationSchemaError:
            pass
        else:  # pragma: no cover
            raise AssertionError("G3 does not allow unknown")

    def test_23_annotation_unit_is_current_turn(self) -> None:
        """§九：同一 conversation 的不同 current turn 可以有不同 annotation。"""
        annotations = [
            ContextDependencyAnnotation(
                case_id="convA", current_turn_id="t3", early_constraint=True
            ),
            ContextDependencyAnnotation(
                case_id="convA", current_turn_id="t5", recent_context=True
            ),
            ContextDependencyAnnotation(
                case_id="convA", current_turn_id="t7", standalone=True
            ),
        ]
        result = run_g3_procedure(annotations, g1_status="READY")
        assert result["annotated_turn_count"] == 3
        assert sum(result["annotation_counts"].values()) == 3

    def test_24_disagreement_requires_domain_review(self) -> None:
        assert count_disagreements([("true", "true")]) == 0
        assert count_disagreements([("true", "false")]) == 1
        assert needs_domain_review(DISAGREEMENT) is True
        assert needs_domain_review(RESOLVED) is False


# ============================================================
# 5. G4 Procedure（§十二 ~ §十七）
# ============================================================


class TestG4Procedure:
    def test_25_impact_value_domain_includes_unknown(self) -> None:
        assert IMPACT_VALUES == ("true", "false", "unknown")
        validate_impact(_impact(entity_changed="unknown"))

    def test_26_impact_classification(self) -> None:
        assert classify_g4_impact(_impact()) == "NO_IMPACT"
        assert (
            classify_g4_impact(_impact(critical_constraint_lost="true")) == "IMPACT"
        )
        assert classify_g4_impact(_impact(entity_changed="unknown")) == "UNKNOWN"
        assert (
            classify_g4_impact(
                _impact(business_outcome_changed="unknown", intent_changed="true")
            )
            == "IMPACT"
        )

    def test_27_g4_requires_g1_ready(self) -> None:
        result = run_g4_procedure((), g1_status="BLOCKED")
        assert result["status"] == "BLOCKED"
        assert result["impact_counts"] == {name: 0 for name in G4_RESULT_CLASSES}

    def test_28_g4_counts_and_unknowns(self) -> None:
        impacts = [
            _impact(),
            _impact(critical_constraint_lost="true"),
            _impact(intent_changed="unknown"),
        ]
        result = run_g4_procedure(impacts, g1_status="READY")
        assert result["status"] == "READY"
        assert result["impact_counts"]["NO_IMPACT"] == 1
        assert result["impact_counts"]["IMPACT"] == 1
        assert result["impact_counts"]["UNKNOWN"] == 1
        assert result["unknown_count"] == 1
        assert sum(result["impact_counts"].values()) == result["evaluated_case_count"]

    def test_29_comparison_is_by_outcome_type_not_string_equality(self) -> None:
        for outcome_type, dimensions in OUTCOME_COMPARISON_DIMENSIONS.items():
            assert outcome_type in OUTCOME_TYPES
            assert comparison_dimensions(outcome_type) == dimensions
            assert "string_equality" not in dimensions
        try:
            comparison_dimensions("NATURAL_LANGUAGE")
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("unknown outcome_type must be rejected")

    def test_30_candidate_interface_only_no_strategy_implementation(self) -> None:
        candidate = CandidateHistory(
            case_id="case_001",
            strategy_id="future_experiment",
            history_turn_ids=("t1", "t2"),
        )
        assert candidate.strategy_id == "future_experiment"
        function_names = _function_names(_SELF, include_tests=False)
        for forbidden in ("recent_n", "hybrid", "relevance", "select_context"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"

    def test_31_comparison_unit_holds_reference_and_candidate(self) -> None:
        unit = G4ComparisonUnit(
            case_id="case_001",
            outcome_type="SQL_SEMANTICS",
            reference_outcome="A01 库存 320",
            candidate_outcome="全部仓库库存 1000",
        )
        assert unit.outcome_type in OUTCOME_TYPES


# ============================================================
# 6. Evidence Report / Aggregation（§十七 / §十八 / §二十 / §二十一）
# ============================================================


class TestEvidenceReport:
    def test_32_synthetic_report_is_marked_synthetic_and_blocked(self) -> None:
        report = run_procedure_on_synthetic_dataset()
        validate_report(report)
        assert report.evidence_type == "SYNTHETIC"
        assert report.source_type == "SYNTHETIC_ONLY"
        assert report.g1_status == "BLOCKED"
        assert report.g2_status == "INSUFFICIENT"
        assert report.g3_status == "BLOCKED"
        assert report.g4_status == "BLOCKED"
        assert report.selection_strategy_decision == "BLOCKED"

    def test_33_synthetic_cannot_be_reported_as_real(self) -> None:
        report = run_procedure_on_synthetic_dataset()
        assert report.evidence_type != "REAL"
        forged = dataclasses.replace(
            report, evidence_type="REAL", source_type="SYNTHETIC_ONLY"
        )
        try:
            validate_report(forged)
        except EvaluationSchemaError:
            pass
        else:  # pragma: no cover
            raise AssertionError("synthetic → REAL must be rejected")

    def test_34_decision_cannot_leave_blocked(self) -> None:
        report = run_procedure_on_synthetic_dataset()
        forged = dataclasses.replace(report, selection_strategy_decision="READY")
        try:
            validate_report(forged)
        except EvaluationSchemaError:
            pass
        else:  # pragma: no cover
            raise AssertionError(
                "selection_strategy_decision must remain BLOCKED in Step 25"
            )

    def test_35_real_path_report_shape(self) -> None:
        cases = [_valid_case(f"case_{i:03d}") for i in range(1, 4)]
        g2 = run_g2_procedure(cases, g1_status="READY")
        annotations = [
            ContextDependencyAnnotation(
                case_id="case_001", current_turn_id="t3", early_constraint=True
            ),
            ContextDependencyAnnotation(
                case_id="case_002", current_turn_id="t3", standalone=True
            ),
        ]
        g3 = run_g3_procedure(annotations, g1_status="READY")
        g4 = run_g4_procedure(
            [_impact(), _impact(critical_constraint_lost="true")], g1_status="READY"
        )
        report = build_evidence_report(
            dataset_version="wms-real-v1",
            readiness=_real_readiness(),
            g2_result=g2,
            g3_result=g3,
            g4_result=g4,
        )
        validate_report(report)
        assert report.evidence_type == "REAL"
        assert report.g1_status == "READY"
        assert report.g2_status == "READY"
        assert report.g3_status == "READY"
        assert report.g4_status == "READY"
        # 仍不得冻结策略
        assert report.selection_strategy_decision == "BLOCKED"

    def test_36_aggregation_has_counts_only(self) -> None:
        report = run_procedure_on_synthetic_dataset()
        counts = report.flat_counts()
        assert counts, "aggregation 视图不应为空"
        for key in counts:
            lowered = key.lower()
            for forbidden in FORBIDDEN_AGGREGATION_KEYS:
                assert forbidden not in lowered, key

    def test_37_aggregation_can_express_ratio(self) -> None:
        """§十七：允许 count / percentage / distribution（例如 7 / 20）。"""
        impacts = [_impact(critical_constraint_lost="true")] + [_impact()] * 19
        result = run_g4_procedure(impacts, g1_status="READY")
        lost = result["impact_counts"]["IMPACT"]
        total = result["evaluated_case_count"]
        assert (lost, total) == (1, 20)
        ratio = lost / total
        assert 0.0 <= ratio <= 1.0
        # 但报告字段中不得出现 score / ranking / weight / winner
        for key in result:
            for forbidden in FORBIDDEN_AGGREGATION_KEYS:
                assert forbidden not in str(key).lower(), key

    def test_38_report_holds_no_business_content(self) -> None:
        fields = {field.name for field in dataclasses.fields(EvidenceCollectionReport)}
        for forbidden in ("content", "answer", "sql", "prompt", "chunk", "turn_text"):
            for name in fields:
                assert forbidden not in name.lower(), name
        report = run_procedure_on_synthetic_dataset()
        flat = report.flat_counts()
        assert all(isinstance(value, int) for value in flat.values())

    def test_39_determinism_same_input_same_report(self) -> None:
        assert run_procedure_on_synthetic_dataset() == run_procedure_on_synthetic_dataset()
        cases = [_valid_case("case_001")]
        assert run_g2_procedure(cases, g1_status="READY") == run_g2_procedure(
            cases, g1_status="READY"
        )

    def test_40_no_db_network_llm_in_procedure(self) -> None:
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
                "backend.app.services",
            ):
                assert not module.startswith(forbidden), module

    def test_41_no_llm_judge_or_scoring(self) -> None:
        """函数名中不得出现 judge / grader / scoring / ranking 语义。

        注："nearest rank"（最近秩百分位）是统计算法术语，不属评分语义，
        因此禁词使用 "ranking"（而非词干 "rank"）。
        """
        function_names = _function_names(_SELF, include_tests=False)
        for forbidden in ("judge", "grade", "score", "ranking", "winner", "weight"):
            for name in function_names:
                assert forbidden not in name.lower(), f"{name}:{forbidden}"

    def test_42_no_tokenizer_dependency(self) -> None:
        for module in _module_imports(_SELF):
            for prefix in ("tiktoken", "transformers", "tokenizers", "sentencepiece"):
                assert not module.startswith(prefix), module


# ============================================================
# 7. 文档完整性（§二十四）
# ============================================================


class TestDocument:
    def test_43_document_sections(self) -> None:
        doc = _source(_AUDIT_DOC)
        for section in (
            "## 1. Objective",
            "## 2. Evidence Pipeline",
            "## 3. Dataset Validation",
            "## 4. G1 Procedure",
            "## 5. G2 Procedure",
            "## 6. G3 Procedure",
            "## 7. G4 Procedure",
            "## 8. Annotation Quality",
            "## 9. Disagreement",
            "## 10. Outcome Comparison",
            "## 11. Evidence Aggregation",
            "## 12. Privacy",
            "## 13. Synthetic Dataset",
            "## 14. Current Gate State",
            "## 15. Deferred",
        ):
            assert section in doc, section

    def test_44_document_gate_state(self) -> None:
        doc = _source(_AUDIT_DOC)
        for statement in (
            "G1 = BLOCKED",
            "G2 = INSUFFICIENT",
            "G3 Contract = READY",
            "G3 Evidence = BLOCKED",
            "G4 = BLOCKED",
            "Selection Strategy = BLOCKED",
        ):
            assert statement in doc, statement

    def test_45_document_evidence_vs_strategy_principle(self) -> None:
        doc = _source(_AUDIT_DOC)
        assert "Evidence Collection" in doc
        assert "Strategy Selection" in doc
        for example in (
            "P95 = 18 turns",
            "max_turns = 18",
            "75% cases lost early constraints",
        ):
            assert example in doc, example

    def test_46_document_pipeline_and_privacy(self) -> None:
        doc = _source(_AUDIT_DOC)
        for marker in (
            "Dataset Validation",
            "Evidence Report",
            "仓库外完成脱敏",
            "de-identified evaluation artifact",
            "evidence_type = SYNTHETIC",
        ):
            assert marker in doc, marker

    def test_47_document_no_selector_or_judge(self) -> None:
        doc = _source(_AUDIT_DOC)
        for forbidden in ("ContextSelector implementation", "LLM judge = implemented"):
            assert forbidden not in doc, forbidden
        assert "LLM Judge" in doc


__all__ = [
    "G1_STATES",
    "G2_STATES",
    "G4_RESULT_CLASSES",
    "DISTRIBUTION_KEYS",
    "OUTCOME_COMPARISON_DIMENSIONS",
    "EvidenceDatasetValidator",
    "EvidenceCollectionReport",
    "CandidateHistory",
    "G4ComparisonUnit",
    "percentile_nearest_rank",
    "summarize_distribution",
    "run_g1_procedure",
    "run_g2_procedure",
    "run_g3_procedure",
    "run_g4_procedure",
    "classify_g4_impact",
    "comparison_dimensions",
    "build_evidence_report",
    "validate_report",
    "run_procedure_on_synthetic_dataset",
]
