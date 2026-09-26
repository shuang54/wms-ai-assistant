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

import json
import yaml
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from backend.app.services.text_to_sql_column_alias_evaluation_service import (
    MATCH_ALIAS,
    MATCH_EXACT,
    MATCH_NONE,
    ROLE_FORBIDDEN,
    ROLE_OPTIONAL,
    ROLE_REQUIRED,
    ColumnAliasContext,
    ColumnAliasResolution,
    resolve_column_group,
)
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
    # Phase 3.9.18 — alias-aware re-analysis of the same 3.9.14 snapshot.
    "PHASE_3_9_18",
    "SNAPSHOT_3_9_18_PATH",
    "REPORT_3_9_18_PATH",
    "AliasAuditEntry",
    "AliasAuditSummary",
    "analyze_phase_3_9_14_for_alias_audit",
    # Phase 3.9.19 — offline projection variants that truly trigger alias.
    "PHASE_3_9_19",
    "PROJECTION_VARIANT_PATH",
    "SNAPSHOT_3_9_19_PATH",
    "REPORT_3_9_19_PATH",
    "ProjectionVariant",
    "ProjectionVariantOutcome",
    "ProjectionVariantSummary",
    "load_projection_variants",
    "run_projection_variants",
]


PHASE_3_9_16: Final[str] = "3.9.16"
PHASE_3_9_17: Final[str] = "3.9.17"
PHASE_3_9_18: Final[str] = "3.9.18"
PHASE_3_9_19: Final[str] = "3.9.19"

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
#: Phase 3.9.18 — alias audit snapshot (NEW file; 3.9.17 is never overwritten).
SNAPSHOT_3_9_18_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_18_alias_evaluation.json"
)
REPORT_3_9_18_PATH: Final[Path] = (
    _REPO_ROOT / "docs" / "evaluation"
    / "text-to-sql-semantic-column-alias-3.9.18.md"
)

#: Phase 3.9.19 — offline projection variants (derived from 3.9.14, NOT
#: fresh LLM output).
PROJECTION_VARIANT_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql"
    / "projection_variants_3_9_19.yaml"
)
SNAPSHOT_3_9_19_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_19_alias_trigger.json"
)
REPORT_3_9_19_PATH: Final[Path] = (
    _REPO_ROOT / "docs" / "evaluation"
    / "text-to-sql-alias-trigger-3.9.19.md"
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
    #: Phase 3.9.18 — per required-column alias diagnostics.
    column_matches: tuple[ColumnAliasResolution, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "actual_columns": list(self.actual_columns),
            "actual_rows": [list(row) for row in self.actual_rows],
            "semantic_passed": self.semantic_passed,
            "semantic_reason": self.semantic_reason,
            "semantic_categories": list(self.semantic_categories),
            "column_matches": [
                item.to_dict() for item in self.column_matches
            ],
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
        passed, reason, categories, matches = _check_semantic_result(
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
                column_matches=matches,
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
        passed, reason, categories, matches = _check_semantic_result(
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
                column_matches=matches,
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


# ============================================================
# Phase 3.9.18 — Semantic Column Alias Audit
# ============================================================


@dataclass(frozen=True)
class AliasAuditEntry:
    """One (case, required column) alias audit row (section §十一)."""
    case_id: str
    expected_column: str
    actual_column: str | None
    matched_by: str
    semantic_name: str | None
    entity: str | None
    diagnostic: str
    alias_needed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "expected_column": self.expected_column,
            "actual_column": self.actual_column,
            "matched_by": self.matched_by,
            "semantic_name": self.semantic_name,
            "entity": self.entity,
            "diagnostic": self.diagnostic,
            "alias_needed": self.alias_needed,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AliasAuditSummary:
    """Phase 3.9.18 — alias audit over the 12 result-evaluable cases."""
    phase: str
    source_snapshot: str
    phase_3_9_17_semantic_accuracy: float | None
    phase_3_9_18_semantic_accuracy: float | None
    phase_3_9_17_semantic_correct: int
    phase_3_9_18_semantic_correct: int
    semantic_evaluable_cases: int
    exact_matches: int
    alias_matches: int
    unmatched_columns: int
    alias_needed_cases: int
    entries: tuple[AliasAuditEntry, ...] = ()
    cases: tuple[SemanticResultCaseOutcome, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "source_snapshot": self.source_snapshot,
            "phase_3_9_17_semantic_accuracy":
                self.phase_3_9_17_semantic_accuracy,
            "phase_3_9_18_semantic_accuracy":
                self.phase_3_9_18_semantic_accuracy,
            "phase_3_9_17_semantic_correct":
                self.phase_3_9_17_semantic_correct,
            "phase_3_9_18_semantic_correct":
                self.phase_3_9_18_semantic_correct,
            "semantic_evaluable_cases": self.semantic_evaluable_cases,
            "exact_matches": self.exact_matches,
            "alias_matches": self.alias_matches,
            "unmatched_columns": self.unmatched_columns,
            "alias_needed_cases": self.alias_needed_cases,
            "entries": [item.to_dict() for item in self.entries],
            "cases": [item.to_dict() for item in self.cases],
        }


def _load_phase_3_9_17_accuracy() -> tuple[float | None, int]:
    """Read the FROZEN 3.9.17 snapshot (never recomputed, never written)."""
    import json

    if not SNAPSHOT_3_9_17_PATH.exists():
        return None, 0
    raw = json.loads(SNAPSHOT_3_9_17_PATH.read_text(encoding="utf-8"))
    return raw.get("semantic_result_correctness"), int(
        raw.get("semantic_correct_cases", 0)
    )


def analyze_phase_3_9_14_for_alias_audit() -> AliasAuditSummary:
    """Phase 3.9.18 — offline alias audit of the 12 result-evaluable cases.

    Re-evaluates the SAVED Phase 3.9.14 actual results with the alias-aware
    semantic checker and compares the outcome with the frozen 3.9.17
    snapshot. No DeepSeek call, no DB, no mutation of any 3.9.14 / 3.9.17
    artifact, and no ground-truth change (sections §十二 / §十六).
    """
    summary = analyze_phase_3_9_14_for_full_semantic()

    entries: list[AliasAuditEntry] = []
    alias_needed_cases: set[str] = set()
    for outcome in summary.cases:
        # Phase 3.9.19: column_matches now covers required + optional +
        # forbidden. The 3.9.18 audit is defined over REQUIRED columns only,
        # so we filter by role to keep the frozen 3.9.18 snapshot stable.
        for match in outcome.column_matches:
            if match.role != ROLE_REQUIRED:
                continue
            alias_needed = match.match_kind == MATCH_ALIAS
            if alias_needed:
                alias_needed_cases.add(outcome.case_id)
            entries.append(
                AliasAuditEntry(
                    case_id=outcome.case_id,
                    expected_column=match.expected_column,
                    actual_column=match.actual_column,
                    matched_by=match.match_kind,
                    semantic_name=match.semantic_name,
                    entity=match.entity,
                    diagnostic=match.diagnostic,
                    alias_needed=alias_needed,
                    detail=match.detail,
                )
            )

    exact = sum(1 for e in entries if e.matched_by == MATCH_EXACT)
    alias = sum(1 for e in entries if e.matched_by == MATCH_ALIAS)
    unmatched = sum(1 for e in entries if e.matched_by == MATCH_NONE)

    accuracy_3_9_17, correct_3_9_17 = _load_phase_3_9_17_accuracy()

    return AliasAuditSummary(
        phase=PHASE_3_9_18,
        source_snapshot=summary.source_snapshot,
        phase_3_9_17_semantic_accuracy=accuracy_3_9_17,
        phase_3_9_18_semantic_accuracy=summary.semantic_result_correctness,
        phase_3_9_17_semantic_correct=correct_3_9_17,
        phase_3_9_18_semantic_correct=summary.semantic_correct_cases,
        semantic_evaluable_cases=summary.semantic_evaluable_cases,
        exact_matches=exact,
        alias_matches=alias,
        unmatched_columns=unmatched,
        alias_needed_cases=len(alias_needed_cases),
        entries=tuple(entries),
        cases=summary.cases,
    )


# ============================================================
# Phase 3.9.19 — Offline projection variants (alias path trigger)
# ============================================================

def _load_phase_3_9_14_actual_by_case() -> dict[str, dict[str, Any]]:
    """case_id -> {actual_columns, actual_rows} from the SAVED 3.9.14
    baseline. These are real historical results; they are never re-run."""
    raw = json.loads(REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for case in raw.get("cases", ()):
        out[str(case["case_id"])] = {
            "actual_columns": tuple(case.get("actual_columns", ())),
            "actual_rows": tuple(tuple(r) for r in case.get("actual_rows", ())),
        }
    return out


@dataclass(frozen=True)
class ProjectionVariant:
    """One offline projection variant (derived from 3.9.14, NOT an LLM run)."""
    variant_id: str
    description: str
    base_case: str
    columns: tuple[str, ...]
    column_renames: Mapping[str, str]
    extra_columns: tuple[str, ...]
    alias_context: Mapping[str, ColumnAliasContext]
    required_columns: tuple[str, ...]
    optional_columns: tuple[str, ...]
    forbidden_columns: tuple[str, ...]
    row_matching: str
    expect: Mapping[str, Any]


@dataclass(frozen=True)
class ProjectionVariantOutcome:
    variant_id: str
    base_case: str
    kind: str
    columns: tuple[str, ...]
    semantic_passed: bool
    expected_passed: bool | None
    semantic_reason: str
    categories: tuple[str, ...]
    required_matches: tuple[ColumnAliasResolution, ...]
    optional_matches: tuple[ColumnAliasResolution, ...]
    forbidden_matches: tuple[ColumnAliasResolution, ...]
    expectation_met: bool
    problems: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "base_case": self.base_case,
            "kind": self.kind,
            "columns": list(self.columns),
            "semantic_passed": self.semantic_passed,
            "expected_passed": self.expected_passed,
            "semantic_reason": self.semantic_reason,
            "categories": list(self.categories),
            "required_matches": [m.to_dict() for m in self.required_matches],
            "optional_matches": [m.to_dict() for m in self.optional_matches],
            "forbidden_matches": [
                m.to_dict() for m in self.forbidden_matches
            ],
            "expectation_met": self.expectation_met,
            "problems": list(self.problems),
        }


@dataclass(frozen=True)
class ProjectionVariantSummary:
    """Phase 3.9.19 — alias-trigger audit over offline projection variants.

    `real_historical_result_count` / accuracy fields come from the frozen
    3.9.18 audit (the REAL 3.9.14 results). The `variants` list is what
    this phase ADDS: synthetic projections that genuinely exercise the
    alias path. They are labelled `kind="projection_variant"` and must
    never be reported as real LLM outputs (section §十三).
    """
    phase: str
    parent_baseline: str
    source_snapshot: str
    llm_calls: int
    db_calls: int
    network_calls: int
    real_historical_result_count: int
    phase_3_9_17_semantic_accuracy: float | None
    phase_3_9_18_semantic_accuracy: float | None
    phase_3_9_18_semantic_correct: int
    exact_matches: int
    alias_matches: int
    unknown_matches: int
    variant_count: int
    variants_expecting_pass: int
    variants_pass_expected: int
    variants_expectation_met: int
    required_matched_by: Mapping[str, int]
    optional_matched_by: Mapping[str, int]
    forbidden_matched_by: Mapping[str, int]
    undeclared_extra_variants: tuple[str, ...]
    variants: tuple[ProjectionVariantOutcome, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "parent_baseline": self.parent_baseline,
            "source_snapshot": self.source_snapshot,
            "llm_calls": self.llm_calls,
            "db_calls": self.db_calls,
            "network_calls": self.network_calls,
            "real_historical_result_count": self.real_historical_result_count,
            "phase_3_9_17_semantic_accuracy": self.phase_3_9_17_semantic_accuracy,
            "phase_3_9_18_semantic_accuracy": self.phase_3_9_18_semantic_accuracy,
            "phase_3_9_18_semantic_correct": self.phase_3_9_18_semantic_correct,
            "exact_matches": self.exact_matches,
            "alias_matches": self.alias_matches,
            "unknown_matches": self.unknown_matches,
            "variant_count": self.variant_count,
            "variants_expecting_pass": self.variants_expecting_pass,
            "variants_pass_expected": self.variants_pass_expected,
            "variants_expectation_met": self.variants_expectation_met,
            "required_matched_by": dict(self.required_matched_by),
            "optional_matched_by": dict(self.optional_matched_by),
            "forbidden_matched_by": dict(self.forbidden_matched_by),
            "undeclared_extra_variants": list(self.undeclared_extra_variants),
            "variants": [v.to_dict() for v in self.variants],
        }


def _parse_variant(raw: Mapping[str, Any]) -> ProjectionVariant:
    ctx_raw = raw.get("alias_context") or {}
    alias_context = {
        str(k): ColumnAliasContext(
            entity=str(v["entity"]),
            aggregate=str(v["aggregate"]) if v.get("aggregate") else None,
        )
        for k, v in ctx_raw.items()
    }
    exp = raw["expectation"]
    expect = raw.get("expect") or {}
    return ProjectionVariant(
        variant_id=str(raw["id"]),
        description=str(raw.get("description", "")),
        base_case=str(raw["base_case"]),
        columns=tuple(raw.get("columns", ()) or ()),
        column_renames=dict(raw.get("column_renames", {}) or {}),
        extra_columns=tuple(raw.get("extra_columns", ()) or ()),
        alias_context=alias_context,
        required_columns=tuple(exp.get("required_columns", ()) or ()),
        optional_columns=tuple(exp.get("optional_columns", ()) or ()),
        forbidden_columns=tuple(exp.get("forbidden_columns", ()) or ()),
        row_matching=str(exp.get("row_matching", SEMANTIC_ROW_MATCHING_UNORDERED)),
        expect=expect,
    )


def load_projection_variants(
    path: Path | None = None,
) -> tuple[ProjectionVariant, ...]:
    """Load the offline projection-variant definitions (section §十)."""
    target = Path(path) if path is not None else PROJECTION_VARIANT_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        return ()
    variants = data.get("variants")
    if not isinstance(variants, list):
        return ()
    return tuple(_parse_variant(v) for v in variants)


def _apply_variant(
    base: Mapping[str, Any], variant: ProjectionVariant
) -> tuple[tuple[str, ...], tuple[tuple[Any, ...], ...]]:
    """Return (final_columns, final_rows) after selection/rename/extra."""
    base_cols = list(base["actual_columns"])
    base_rows = [list(r) for r in base["actual_rows"]]

    # 1. selection (subset / reorder), default = all.
    if variant.columns:
        keep = [c for c in variant.columns if c in base_cols]
        idx = [base_cols.index(c) for c in keep]
        cols = list(keep)
        rows = [[row[i] for i in idx] for row in base_rows]
    else:
        cols = list(base_cols)
        rows = [list(row) for row in base_rows]

    # 2. rename (old -> new).
    rename = variant.column_renames or {}
    if rename:
        cols = [rename.get(c, c) for c in cols]

    # 3. extra columns (appended, value None).
    for extra in variant.extra_columns:
        cols.append(extra)
        for row in rows:
            row.append(None)
    return tuple(cols), tuple(tuple(r) for r in rows)


def run_projection_variants(
    path: Path | None = None,
) -> ProjectionVariantSummary:
    """Phase 3.9.19 — truly trigger the alias evaluation path.

    Builds offline projection variants from the SAVED 3.9.14 results,
    evaluates each with the unified alias resolver, and checks every
    variant against its declared expectation. No DeepSeek, no DB, no
    network (section §十二).
    """
    actual_by_case = _load_phase_3_9_14_actual_by_case()
    variants = load_projection_variants(path)

    service = TextToSQLResultEvaluationService()
    outcomes: list[ProjectionVariantOutcome] = []

    required_dist: Counter = Counter()
    optional_dist: Counter = Counter()
    forbidden_dist: Counter = Counter()
    undeclared_extra: list[str] = []

    for variant in variants:
        base = actual_by_case[variant.base_case]
        final_columns, final_rows = _apply_variant(base, variant)

        # Derive expected_rows from the finalized (renamed) rows.
        required_res = resolve_column_group(
            variant.required_columns, final_columns,
            variant.alias_context, ROLE_REQUIRED,
        )
        if all(r.actual_column is not None for r in required_res):
            lowered = [str(c).lower() for c in final_columns]
            indices = [
                lowered.index(r.actual_column.lower()) for r in required_res
            ]
            expected_rows = tuple(
                tuple(row[i] for i in indices) for row in final_rows
            )
        else:
            expected_rows = ()

        sem = SemanticExpectation(
            required_columns=variant.required_columns,
            optional_columns=variant.optional_columns,
            forbidden_columns=variant.forbidden_columns,
            expected_rows=expected_rows,
            row_matching=variant.row_matching,
        )
        result = service.check(
            ResultCheckInput(
                case_id=variant.variant_id,
                columns=final_columns,
                rows=final_rows,
                semantic_expectation=sem,
                alias_context=variant.alias_context,
            )
        )

        required_matches = tuple(
            m for m in result.semantic_column_matches
            if m.role == ROLE_REQUIRED
        )
        optional_matches = tuple(
            m for m in result.semantic_column_matches
            if m.role == ROLE_OPTIONAL
        )
        forbidden_matches = tuple(
            m for m in result.semantic_column_matches
            if m.role == ROLE_FORBIDDEN
        )

        for m in required_matches:
            required_dist[m.match_kind] += 1
        for m in optional_matches:
            optional_dist[m.match_kind] += 1
        for m in forbidden_matches:
            forbidden_dist[m.match_kind] += 1

        # ---- expectation check ----
        problems: list[str] = []
        expect = variant.expect
        exp_passed = expect.get("semantic_passed")
        if exp_passed is not None and result.semantic_passed != exp_passed:
            problems.append(
                f"semantic_passed expected {exp_passed!r}, "
                f"got {result.semantic_passed!r}"
            )
        for role, matches, key in (
            (ROLE_REQUIRED, required_matches, "required_matched_by"),
            (ROLE_OPTIONAL, optional_matches, "optional_matched_by"),
            (ROLE_FORBIDDEN, forbidden_matches, "forbidden_matched_by"),
        ):
            want = expect.get(key)
            if not want:
                continue
            got = {m.expected_column: m.match_kind for m in matches}
            for col, kind in want.items():
                if got.get(col) != kind:
                    problems.append(
                        f"{key}[{col}] expected {kind!r}, got {got.get(col)!r}"
                    )
        for cat in expect.get("categories_include", ()) or ():
            if cat not in result.semantic_categories:
                problems.append(f"expected category {cat!r} missing")
        for cat in expect.get("categories_exclude", ()) or ():
            if cat in result.semantic_categories:
                problems.append(f"unexpected category {cat!r} present")

        if (
            SEMANTIC_CATEGORY_UNDECLARED_EXTRA in result.semantic_categories
            and result.semantic_passed
        ):
            undeclared_extra.append(variant.variant_id)

        outcomes.append(
            ProjectionVariantOutcome(
                variant_id=variant.variant_id,
                base_case=variant.base_case,
                kind="projection_variant",
                columns=final_columns,
                semantic_passed=bool(result.semantic_passed),
                expected_passed=exp_passed,
                semantic_reason=result.semantic_reason,
                categories=tuple(result.semantic_categories),
                required_matches=required_matches,
                optional_matches=optional_matches,
                forbidden_matches=forbidden_matches,
                expectation_met=not problems,
                problems=tuple(problems),
            )
        )

    audit = analyze_phase_3_9_14_for_alias_audit()
    alias_total = (
        sum(required_dist.values())
        + sum(optional_dist.values())
        + sum(forbidden_dist.values())
    )
    return ProjectionVariantSummary(
        phase=PHASE_3_9_19,
        parent_baseline=PHASE_3_9_18,
        source_snapshot=audit.source_snapshot,
        llm_calls=0,
        db_calls=0,
        network_calls=0,
        real_historical_result_count=audit.semantic_evaluable_cases,
        phase_3_9_17_semantic_accuracy=audit.phase_3_9_17_semantic_accuracy,
        phase_3_9_18_semantic_accuracy=audit.phase_3_9_18_semantic_accuracy,
        phase_3_9_18_semantic_correct=audit.phase_3_9_18_semantic_correct,
        exact_matches=audit.exact_matches,
        alias_matches=audit.alias_matches,
        unknown_matches=audit.unmatched_columns,
        variant_count=len(variants),
        variants_expecting_pass=sum(
            1 for v in variants
            if v.expect.get("semantic_passed") is True
        ),
        variants_pass_expected=sum(
            1 for o in outcomes
            if o.semantic_passed == (o.expected_passed is True)
        ),
        variants_expectation_met=sum(
            1 for o in outcomes if o.expectation_met
        ),
        required_matched_by=dict(required_dist),
        optional_matched_by=dict(optional_dist),
        forbidden_matched_by=dict(forbidden_dist),
        undeclared_extra_variants=tuple(undeclared_extra),
        variants=tuple(outcomes),
    )
