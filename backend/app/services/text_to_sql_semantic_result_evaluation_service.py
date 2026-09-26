"""Phase 3.9.16 — Semantic Result Evaluation Analysis.

Off-line re-analysis of Phase 3.9.14 result snapshot against the new
``semantic_expectation`` ground truth. Does NOT re-run DeepSeek, does
NOT touch 3.9.14 artifact, does NOT modify 3.9.14 snapshot.

Pipeline:
    1. Load ``phase_3_9_14_result_llm_baseline.json`` (saved actual_columns /
       actual_rows per case).
    2. Load ``result_ground_truth.yaml`` (with new ``semantic_expectation``).
    3. For each case: run ``_check_semantic_result`` against the saved actual.
    4. Aggregate into a separate 3.9.16 snapshot + report.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from backend.app.services.text_to_sql_result_evaluation_service import (
    SEMANTIC_CATEGORY_DUPLICATE_ROW,
    SEMANTIC_CATEGORY_FORBIDDEN,
    SEMANTIC_CATEGORY_MISSING_REQUIRED,
    SEMANTIC_CATEGORY_UNDECLARED_EXTRA,
    SEMANTIC_CATEGORY_WRONG_ORDER,
    SEMANTIC_CATEGORY_WRONG_ROW_SET,
    SEMANTIC_CATEGORY_WRONG_VALUE,
    SEMANTIC_ROW_MATCHING_ORDERED,
    SEMANTIC_ROW_MATCHING_UNORDERED,
    SEMANTIC_ROW_MATCHING_VALUES,
    ResultCheckInput,
    SemanticExpectation,
    TextToSQLResultEvaluationService,
    _check_semantic_result,
    load_semantic_expectations,
    parse_semantic_expectation,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_LLM_SNAPSHOT_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_14_result_llm_baseline.json"
)
from backend.app.services.text_to_sql_ground_truth_service import (
    GROUND_TRUTH_PATH as PHASE_3_9_13_GT_PATH,
    load_ground_truth as load_phase_3_9_13_ground_truth,
)


__all__ = [
    "PHASE_3_9_16",
    "PHASE_3_9_17",
    "SNAPSHOT_3_9_16_PATH",
    "SNAPSHOT_3_9_17_PATH",
    "RESULT_EVALUABLE_CASE_IDS",
    "NA_CASE_IDS",
    "SemanticResultCaseOutcome",
    "SemanticResultFullSummary",
    "SemanticResultSummary",
    "analyze_phase_3_9_14_for_semantic",
    "analyze_phase_3_9_14_for_full_semantic",
    "compute_semantic_summary",
    "validate_semantic_consistency",
]


PHASE_3_9_16: Final[str] = "3.9.16"
PHASE_3_9_17: Final[str] = "3.9.17"

SNAPSHOT_3_9_16_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_16_semantic_result.json"
)
REPORT_3_9_16_PATH: Final[Path] = (
    _REPO_ROOT / "docs" / "evaluation"
    / "text-to-sql-semantic-result-evaluation-3.9.16.md"
)
SNAPSHOT_3_9_17_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_17_semantic_result_full.json"
)
REPORT_3_9_17_PATH: Final[Path] = (
    _REPO_ROOT / "docs" / "evaluation"
    / "text-to-sql-semantic-result-full-3.9.17.md"
)

#: Result-evaluable case IDs (Phase 3.9.17; excludes 2 N/A cases).
RESULT_EVALUABLE_CASE_IDS: Final[frozenset[str]] = frozenset({
    "simple_document_list",
    "top_n_chunks_by_token_count",
    "chunks_ordered_by_token_count",
    "aggregate_document_count",
    "group_by_chunk_count_per_document",
    "having_chunk_count_greater_than",
    "join_chunk_with_parent_document",
    "date_filter_created_after",
    "limit_first_10_documents",
    "semantic_dependent_document_and_chunk",
    "project_a_inventory",
    "project_b_inventory",
})
#: N/A case IDs (Phase 3.9.17; semantic_expectation must NOT be added).
NA_CASE_IDS: Final[frozenset[str]] = frozenset({
    "filtered_documents_by_file_type",
    "safety_delete_all_documents",
})


@dataclass(frozen=True)
class SemanticResultCaseOutcome:
    case_id: str
    actual_columns: tuple[str, ...]
    actual_rows: tuple[tuple[Any, ...], ...]
    semantic_passed: bool | None
    semantic_reason: str
    semantic_categories: tuple[str, ...]
    expected_required_columns: tuple[str, ...]
    expected_optional_columns: tuple[str, ...]
    expected_forbidden_columns: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "actual_columns": list(self.actual_columns),
            "actual_rows": [list(row) for row in self.actual_rows],
            "semantic_passed": self.semantic_passed,
            "semantic_reason": self.semantic_reason,
            "semantic_categories": list(self.semantic_categories),
            "expected_required_columns": list(
                self.expected_required_columns
            ),
            "expected_optional_columns": list(
                self.expected_optional_columns
            ),
            "expected_forbidden_columns": list(
                self.expected_forbidden_columns
            ),
        }


@dataclass(frozen=True)
class SemanticResultSummary:
    source_snapshot: str
    total_cases: int
    semantic_evaluable_cases: int
    semantic_correct_cases: int
    semantic_incorrect_cases: int

    cases: tuple[SemanticResultCaseOutcome, ...] = ()

    @property
    def semantic_result_correctness(self) -> float | None:
        denom = self.semantic_correct_cases + self.semantic_incorrect_cases
        if denom <= 0:
            return None
        return round(self.semantic_correct_cases / denom, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_snapshot": self.source_snapshot,
            "total_cases": self.total_cases,
            "semantic_evaluable_cases": self.semantic_evaluable_cases,
            "semantic_correct_cases": self.semantic_correct_cases,
            "semantic_incorrect_cases": self.semantic_incorrect_cases,
            "semantic_result_correctness": self.semantic_result_correctness,
            "cases": [item.to_dict() for item in self.cases],
        }


def analyze_phase_3_9_14_for_semantic() -> SemanticResultSummary:
    """读取 3.9.14 snapshot + 新 semantic ground truth，离线计算 semantic。"""
    if not REAL_LLM_SNAPSHOT_PATH.exists():
        raise FileNotFoundError(
            f"Phase 3.9.14 snapshot missing: {REAL_LLM_SNAPSHOT_PATH}"
        )
    import json
    raw = json.loads(REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8"))

    semantic_truth = load_semantic_expectations()

    case_outcomes: list[SemanticResultCaseOutcome] = []
    for case_data in raw.get("cases", []):
        case_id = case_data.get("case_id", "")
        semantic = semantic_truth.get(case_id)
        if semantic is None:
            continue
        actual_columns = tuple(case_data.get("actual_columns", ()))
        actual_rows = tuple(tuple(r) for r in case_data.get("actual_rows", ()))
        passed, reason, categories = _check_semantic_result(
            ResultCheckInput(
                case_id=case_id,
                columns=actual_columns,
                rows=actual_rows,
                semantic_expectation=semantic,
                executed=True,
            ),
            semantic,
        )
        case_outcomes.append(
            SemanticResultCaseOutcome(
                case_id=case_id,
                actual_columns=actual_columns,
                actual_rows=actual_rows,
                semantic_passed=passed,
                semantic_reason=reason,
                semantic_categories=categories,
                expected_required_columns=semantic.required_columns,
                expected_optional_columns=semantic.optional_columns,
                expected_forbidden_columns=semantic.forbidden_columns,
            )
        )

    return compute_semantic_summary(
        case_outcomes, source_snapshot=str(REAL_LLM_SNAPSHOT_PATH.name)
    )


def compute_semantic_summary(
    outcomes,
    *,
    source_snapshot: str,
) -> SemanticResultSummary:
    outcomes = tuple(outcomes)
    evaluable = sum(1 for item in outcomes if item.semantic_passed is not None)
    correct = sum(1 for item in outcomes if item.semantic_passed is True)
    incorrect = sum(1 for item in outcomes if item.semantic_passed is False)
    return SemanticResultSummary(
        source_snapshot=source_snapshot,
        total_cases=len(outcomes),
        semantic_evaluable_cases=evaluable,
        semantic_correct_cases=correct,
        semantic_incorrect_cases=incorrect,
        cases=outcomes,
    )



@dataclass(frozen=True)
class SemanticResultFullSummary:
    """Phase 3.9.17 — full 12-case semantic summary.

    Mirrors ``SemanticResultSummary`` but adds per-case ``expected_rows``
    plus the strict projection reference (3.9.14) for comparison.
    """
    source_snapshot: str
    phase_3_9_14_result_accuracy: float
    phase_3_9_14_result_correct: int
    phase_3_9_14_result_incorrect: int
    phase_3_9_14_result_total: int
    semantic_evaluable_cases: int
    semantic_correct_cases: int
    semantic_incorrect_cases: int
    cases: tuple[SemanticResultCaseOutcome, ...] = ()

    @property
    def semantic_result_correctness(self) -> float | None:
        denom = self.semantic_correct_cases + self.semantic_incorrect_cases
        if denom <= 0:
            return None
        return round(self.semantic_correct_cases / denom, 4)

    @property
    def semantic_ground_truth_coverage(self) -> float | None:
        """Coverage = |result-evaluable cases with semantic_expectation| /
        |all result-evaluable cases|.
        """
        total = len(RESULT_EVALUABLE_CASE_IDS)
        if total <= 0:
            return None
        return round(len(self.cases) / total, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": PHASE_3_9_17,
            "source_snapshot": self.source_snapshot,
            "phase_3_9_14_strict_result_accuracy":
                self.phase_3_9_14_result_accuracy,
            "phase_3_9_14_strict_result_total":
                self.phase_3_9_14_result_total,
            "phase_3_9_14_strict_result_correct":
                self.phase_3_9_14_result_correct,
            "phase_3_9_14_strict_result_incorrect":
                self.phase_3_9_14_result_incorrect,
            "semantic_ground_truth_coverage":
                self.semantic_ground_truth_coverage,
            "semantic_evaluable_cases": self.semantic_evaluable_cases,
            "semantic_correct_cases": self.semantic_correct_cases,
            "semantic_incorrect_cases": self.semantic_incorrect_cases,
            "semantic_result_correctness": self.semantic_result_correctness,
            "result_evaluable_case_ids": sorted(RESULT_EVALUABLE_CASE_IDS),
            "na_case_ids": sorted(NA_CASE_IDS),
            "cases": [item.to_dict() for item in self.cases],
        }


def analyze_phase_3_9_14_for_full_semantic() -> SemanticResultFullSummary:
    """Phase 3.9.17 — re-evaluate the FULL set of 12 result-evaluable cases.

    Reads the saved 3.9.14 actual results and runs ``_check_semantic_result``
    against the (now expanded) 12-case semantic_expectation ground truth.
    Does NOT re-run DeepSeek. Does NOT touch the 3.9.14 artifact.

    Coverage invariant: every case in ``RESULT_EVALUABLE_CASE_IDS`` must have
    a ``semantic_expectation`` entry in the dataset. Violations are reported
    via the script's check_existing() but do NOT block this analysis.
    """
    import json

    if not REAL_LLM_SNAPSHOT_PATH.exists():
        raise FileNotFoundError(
            f"Phase 3.9.14 snapshot missing: {REAL_LLM_SNAPSHOT_PATH}"
        )
    raw = json.loads(REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8"))

    semantic_truth = load_semantic_expectations()
    cases_by_id = {c["case_id"]: c for c in raw.get("cases", [])}

    outcomes: list[SemanticResultCaseOutcome] = []
    for case_id in sorted(RESULT_EVALUABLE_CASE_IDS):
        semantic = semantic_truth.get(case_id)
        if semantic is None:
            # Missing semantic ground truth for an evaluable case.
            # Append a placeholder so coverage is visible.
            outcomes.append(
                SemanticResultCaseOutcome(
                    case_id=case_id,
                    actual_columns=(),
                    actual_rows=(),
                    semantic_passed=None,
                    semantic_reason="no semantic_expectation defined",
                    semantic_categories=(),
                    expected_required_columns=(),
                    expected_optional_columns=(),
                    expected_forbidden_columns=(),
                )
            )
            continue
        actual = cases_by_id.get(case_id)
        if actual is None:
            outcomes.append(
                SemanticResultCaseOutcome(
                    case_id=case_id,
                    actual_columns=(),
                    actual_rows=(),
                    semantic_passed=None,
                    semantic_reason="no 3.9.14 actual result",
                    semantic_categories=(),
                    expected_required_columns=semantic.required_columns,
                    expected_optional_columns=semantic.optional_columns,
                    expected_forbidden_columns=semantic.forbidden_columns,
                )
            )
            continue
        actual_columns = tuple(actual.get("actual_columns", ()))
        actual_rows = tuple(tuple(r) for r in actual.get("actual_rows", ()))
        passed, reason, categories = _check_semantic_result(
            ResultCheckInput(
                case_id=case_id,
                columns=actual_columns,
                rows=actual_rows,
                semantic_expectation=semantic,
                executed=True,
            ),
            semantic,
        )
        outcomes.append(
            SemanticResultCaseOutcome(
                case_id=case_id,
                actual_columns=actual_columns,
                actual_rows=actual_rows,
                semantic_passed=passed,
                semantic_reason=reason,
                semantic_categories=categories,
                expected_required_columns=semantic.required_columns,
                expected_optional_columns=semantic.optional_columns,
                expected_forbidden_columns=semantic.forbidden_columns,
            )
        )

    total = raw.get("total_cases", 0)
    correct = raw.get("result_correct", 0)
    incorrect = raw.get("result_incorrect", 0)
    accuracy = raw.get("result_accuracy", 0.0)

    evaluable = sum(1 for o in outcomes if o.semantic_passed is not None)
    sem_correct = sum(1 for o in outcomes if o.semantic_passed is True)
    sem_incorrect = sum(1 for o in outcomes if o.semantic_passed is False)

    return SemanticResultFullSummary(
        source_snapshot=str(REAL_LLM_SNAPSHOT_PATH.name),
        phase_3_9_14_result_accuracy=float(accuracy),
        phase_3_9_14_result_correct=int(correct),
        phase_3_9_14_result_incorrect=int(incorrect),
        phase_3_9_14_result_total=int(total),
        semantic_evaluable_cases=evaluable,
        semantic_correct_cases=sem_correct,
        semantic_incorrect_cases=sem_incorrect,
        cases=tuple(outcomes),
    )


def validate_semantic_consistency(
    semantic_truth: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Phase 3.9.17 — cross-check the semantic ground truth.

    Implements Rules 5 & 6 (case section §十二) of the 3.9.17 spec:

    - Rule 5: semantic_expectation only appears on result-evaluable cases.
    - Rule 6: N/A cases never gain a semantic_expectation.

    Returns a tuple of human-readable problems (empty = consistent).
    Per-row validation (Rules 1-4) is enforced by
    ``parse_semantic_expectation`` itself.
    """
    if semantic_truth is None:
        semantic_truth = load_semantic_expectations()

    problems: list[str] = []
    evaluable = set(RESULT_EVALUABLE_CASE_IDS)
    na = set(NA_CASE_IDS)

    # Rule 5: every case with semantic_expectation must be result-evaluable.
    for case_id in semantic_truth:
        if case_id not in evaluable:
            problems.append(
                f"semantic_expectation defined for non-evaluable case "
                f"{case_id!r} (Rule 5: only result-evaluable cases allowed)"
            )
    # Rule 6: every result-evaluable case MUST have a semantic_expectation.
    missing = sorted(evaluable - set(semantic_truth))
    if missing:
        problems.append(
            "result-evaluable cases missing semantic_expectation "
            f"(Rule 6: N/A cases must not gain semantic_expectation): "
            f"{missing}"
        )
    # Symmetry check: N/A cases should not have semantic_expectation.
    for case_id in na:
        if case_id in semantic_truth:
            problems.append(
                f"N/A case {case_id!r} gained a semantic_expectation "
                f"(Rule 6 violation)"
            )
    return tuple(problems)
