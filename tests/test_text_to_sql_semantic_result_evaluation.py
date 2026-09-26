"""Phase 3.9.16 - Semantic result evaluation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.services.text_to_sql_result_evaluation_service import (
    SEMANTIC_CATEGORY_FORBIDDEN,
    SEMANTIC_CATEGORY_MISSING_REQUIRED,
    SEMANTIC_CATEGORY_UNDECLARED_EXTRA,
    SEMANTIC_CATEGORY_WRONG_ROW_SET,
    SEMANTIC_CATEGORY_WRONG_VALUE,
    SEMANTIC_ROW_MATCHING_ORDERED,
    SEMANTIC_ROW_MATCHING_UNORDERED,
    ResultCheckInput,
    ResultExpectationError,
    SemanticExpectation,
    TextToSQLResultEvaluationService,
    parse_semantic_expectation,
)
from backend.app.services.text_to_sql_result_evaluation_service import (
    RESULT_TYPE_EXACT_ROWS,
    RESULT_TYPE_SCALAR,
    RESULT_TYPE_UNORDERED_ROWS,
    RESULT_TYPE_COLUMN_VALUES,
    ResultExpectation,
)
from backend.app.services.text_to_sql_semantic_result_evaluation_service import (
    PHASE_3_9_16,
    SNAPSHOT_3_9_16_PATH,
    SemanticResultCaseOutcome,
    analyze_phase_3_9_14_for_semantic,
    compute_semantic_summary,
)


def service() -> TextToSQLResultEvaluationService:
    return TextToSQLResultEvaluationService()


def _sem(required=(), optional=(), forbidden=(), rows=(),
          matching=SEMANTIC_ROW_MATCHING_UNORDERED):
    return SemanticExpectation(
        required_columns=tuple(required),
        optional_columns=tuple(optional),
        forbidden_columns=tuple(forbidden),
        expected_rows=tuple(tuple(r) for r in rows),
        row_matching=matching,
    )


class TestSemanticExpectationDto:
    def test_valid(self) -> None:
        s = _sem(required=("id",))
        assert s.required_columns == ("id",)

    def test_requires_at_least_one_required_column(self) -> None:
        with pytest.raises(ResultExpectationError):
            _sem(required=())

    def test_invalid_row_matching_rejected(self) -> None:
        with pytest.raises(ResultExpectationError):
            _sem(required=("id",), matching="sort-of-unordered")


class TestParseSemanticExpectation:
    def test_full_payload(self) -> None:
        s = parse_semantic_expectation({
            "required_columns": ["id", "count"],
            "optional_columns": ["title"],
            "forbidden_columns": [],
            "expected_rows": [[1, 2], [2, 3]],
            "row_matching": "unordered",
        })
        assert s.required_columns == ("id", "count")
        assert s.expected_rows == ((1, 2), (2, 3))

    def test_minimal_payload(self) -> None:
        s = parse_semantic_expectation({"required_columns": ["id"]})
        assert s.row_matching == SEMANTIC_ROW_MATCHING_UNORDERED
        assert s.expected_rows == ()

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ResultExpectationError):
            parse_semantic_expectation({
                "required_columns": ["id"],
                "type": "exact_rows",
            })


class TestSemanticChecker:
    """Tests 1-9 from task section 7."""

    def test_t1_required_columns_exact_match(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="t", columns=("id", "count"),
            rows=((1, 2), (2, 3)),
            semantic_expectation=_sem(
                required=("id", "count"), rows=[(1, 2), (2, 3)]),
        ))
        assert r.semantic_passed is True
        assert r.semantic_categories == ()

    def test_t2_required_plus_optional(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="t", columns=("id", "title", "count"),
            rows=((1, "A", 2), (2, "B", 3)),
            semantic_expectation=_sem(
                required=("id", "count"), optional=("title",),
                rows=[(1, 2), (2, 3)]),
        ))
        assert r.semantic_passed is True

    def test_t3_missing_required(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="t", columns=("id", "title"), rows=((1, "A"),),
            semantic_expectation=_sem(required=("id", "count")),
        ))
        assert r.semantic_passed is False
        assert SEMANTIC_CATEGORY_MISSING_REQUIRED in r.semantic_categories
        assert SEMANTIC_CATEGORY_UNDECLARED_EXTRA in r.semantic_categories

    def test_t4_wrong_value(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="t", columns=("id", "count"), rows=((1, 99),),
            semantic_expectation=_sem(
                required=("id", "count"), rows=[(1, 2)]),
        ))
        assert r.semantic_passed is False
        assert SEMANTIC_CATEGORY_WRONG_VALUE in r.semantic_categories

    def test_t5_wrong_row_set(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="t", columns=("id", "count"),
            rows=((1, 2), (3, 4)),
            semantic_expectation=_sem(
                required=("id", "count"), rows=[(1, 2), (2, 3)]),
        ))
        assert r.semantic_passed is False
        assert SEMANTIC_CATEGORY_WRONG_ROW_SET in r.semantic_categories

    def test_t6_forbidden_column(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="t", columns=("id", "count", "salary"),
            rows=((1, 2, 500),),
            semantic_expectation=_sem(
                required=("id", "count"), forbidden=("salary",),
                rows=[(1, 2)]),
        ))
        assert r.semantic_passed is False
        assert SEMANTIC_CATEGORY_FORBIDDEN in r.semantic_categories

    def test_t7_strict_rejects_extra(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="t", columns=("id", "title", "count"),
            rows=((1, "A", 2), (2, "B", 3)),
            expectation=ResultExpectation(
                type=RESULT_TYPE_UNORDERED_ROWS,
                rows=((1, 2), (2, 3)),
            ),
        ))
        assert r.passed is False
        assert r.semantic_passed is None

    def test_t8_semantic_optional_column_passes(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="t", columns=("id", "title", "count"),
            rows=((1, "A", 2), (2, "B", 3)),
            semantic_expectation=_sem(
                required=("id", "count"), optional=("title",),
                rows=[(1, 2), (2, 3)]),
        ))
        assert r.semantic_passed is True

    def test_t9_undeclared_extra_is_warning_only(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="t", columns=("id", "title", "count"),
            rows=((1, "A", 2), (2, "B", 3)),
            semantic_expectation=_sem(
                required=("id", "count"),  # title NOT declared
                rows=[(1, 2), (2, 3)]),
        ))
        assert r.semantic_passed is True
        assert SEMANTIC_CATEGORY_UNDECLARED_EXTRA in r.semantic_categories


class TestBackwardCompatibility:
    def test_strict_modes_unchanged(self) -> None:
        # exact_rows
        r = service().check(ResultCheckInput(
            case_id="t", columns=("a",), rows=((1,),),
            expectation=ResultExpectation(
                type=RESULT_TYPE_EXACT_ROWS, rows=((1,),)),
        ))
        assert r.passed is True
        # unordered_rows
        r = service().check(ResultCheckInput(
            case_id="t", columns=("a",), rows=((1,), (1,)),
            expectation=ResultExpectation(
                type=RESULT_TYPE_UNORDERED_ROWS, rows=((1,), (1,),)),
        ))
        assert r.passed is True
        # scalar
        r = service().check(ResultCheckInput(
            case_id="t", columns=("c",), rows=((7,),),
            expectation=ResultExpectation(
                type=RESULT_TYPE_SCALAR, value=7),
        ))
        assert r.passed is True
        # column_values
        r = service().check(ResultCheckInput(
            case_id="t", columns=("c",), rows=((7,),),
            expectation=ResultExpectation(
                type=RESULT_TYPE_COLUMN_VALUES, column="c", values=(7,)),
        ))
        assert r.passed is True

    def test_both_pass_independently(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="t", columns=("id", "count"),
            rows=((1, 2), (2, 3)),
            expectation=ResultExpectation(
                type=RESULT_TYPE_EXACT_ROWS, rows=((1, 2), (2, 3))),
            semantic_expectation=_sem(
                required=("id", "count"), rows=[(1, 2), (2, 3)]),
        ))
        assert r.passed is True
        assert r.semantic_passed is True

    def test_input_immutability(self) -> None:
        inp = ResultCheckInput(
            case_id="t", columns=("id",), rows=((1,),),
            expectation=ResultExpectation(
                type=RESULT_TYPE_EXACT_ROWS, rows=((1,),)),
            semantic_expectation=_sem(required=("id",), rows=[(1,)]),
        )
        before_cols = inp.columns
        before_rows = inp.rows
        service().check(inp)
        assert inp.columns == before_cols
        assert inp.rows == before_rows


class TestNAHandling:
    def test_no_expectations_n_a(self) -> None:
        r = service().check(ResultCheckInput(case_id="t"))
        assert r.applicable is False
        assert r.passed is None
        assert r.semantic_passed is None

    def test_strict_only(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="t", columns=("c",), rows=((7,),),
            expectation=ResultExpectation(
                type=RESULT_TYPE_SCALAR, value=7),
        ))
        assert r.passed is True
        assert r.semantic_passed is None

    def test_not_executed_n_a(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="t", executed=False,
            semantic_expectation=_sem(required=("id",)),
        ))
        assert r.passed is None
        assert r.semantic_passed is None


class TestSemanticSummary:
    def test_compute_summary(self) -> None:
        outcomes = (
            SemanticResultCaseOutcome(
                case_id="a", actual_columns=("id",), actual_rows=((1,),),
                semantic_passed=True, semantic_reason="ok",
                semantic_categories=(),
                expected_required_columns=("id",),
                expected_optional_columns=(),
                expected_forbidden_columns=(),
            ),
            SemanticResultCaseOutcome(
                case_id="b", actual_columns=("id",), actual_rows=((2,),),
                semantic_passed=False, semantic_reason="missing",
                semantic_categories=("MISSING_REQUIRED_COLUMN",),
                expected_required_columns=("count",),
                expected_optional_columns=(),
                expected_forbidden_columns=(),
            ),
            SemanticResultCaseOutcome(
                case_id="c", actual_columns=("id",), actual_rows=((3,),),
                semantic_passed=None, semantic_reason="",
                semantic_categories=(),
                expected_required_columns=("id",),
                expected_optional_columns=(),
                expected_forbidden_columns=(),
            ),
        )
        s = compute_semantic_summary(outcomes, source_snapshot="x.json")
        assert s.semantic_evaluable_cases == 2
        assert s.semantic_correct_cases == 1
        assert s.semantic_incorrect_cases == 1
        assert s.semantic_result_correctness == 0.5

    def test_empty_no_division_error(self) -> None:
        s = compute_semantic_summary((), source_snapshot="x")
        assert s.semantic_result_correctness is None


class TestOfflineAnalysis:
    def test_analyze_phase_3_9_14_for_semantic(self) -> None:
        summary = analyze_phase_3_9_14_for_semantic()
        assert summary.total_cases == 3
        assert summary.semantic_evaluable_cases == 3
        assert summary.semantic_correct_cases == 3
        assert summary.semantic_incorrect_cases == 0
        assert summary.semantic_result_correctness == 1.0
        assert summary.source_snapshot == (
            "phase_3_9_14_result_llm_baseline.json"
        )


class TestSnapshot:
    def test_snapshot_exists_and_valid(self) -> None:
        if not SNAPSHOT_3_9_16_PATH.exists():
            pytest.skip("snapshot not yet generated")
        payload = json.loads(SNAPSHOT_3_9_16_PATH.read_text(encoding="utf-8"))
        assert payload["phase"] == PHASE_3_9_16
        assert payload["total_cases"] == 3
        assert payload["semantic_result_correctness"] == 1.0
        for key in (
            "source_snapshot", "source_snapshot_sha256",
            "semantic_evaluable_cases", "semantic_correct_cases",
            "semantic_incorrect_cases", "cases",
        ):
            assert key in payload
