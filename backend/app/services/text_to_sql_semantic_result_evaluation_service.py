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
    "SNAPSHOT_3_9_16_PATH",
    "SemanticResultCaseOutcome",
    "SemanticResultSummary",
    "analyze_phase_3_9_14_for_semantic",
    "compute_semantic_summary",
    "render_phase_3_9_16_report",
]


PHASE_3_9_16: Final[str] = "3.9.16"

SNAPSHOT_3_9_16_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_16_semantic_result.json"
)
REPORT_3_9_16_PATH: Final[Path] = (
    _REPO_ROOT / "docs" / "evaluation"
    / "text-to-sql-semantic-result-evaluation-3.9.16.md"
)


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
