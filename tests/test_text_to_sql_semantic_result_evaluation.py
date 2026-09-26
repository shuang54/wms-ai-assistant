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
    NA_CASE_IDS,
    PHASE_3_9_16,
    PHASE_3_9_17,
    RESULT_EVALUABLE_CASE_IDS,
    SNAPSHOT_3_9_16_PATH,
    SNAPSHOT_3_9_17_PATH,
    SemanticResultCaseOutcome,
    analyze_phase_3_9_14_for_semantic,
    analyze_phase_3_9_14_for_full_semantic,
    compute_semantic_summary,
    validate_semantic_consistency,
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
    def test_analyze_phase_3_9_14_for_semantic_3_9_16_subset(self) -> None:
        """The 3.9.16 snapshot file stays at 3 cases; this analyzer
        iterates over current YAML semantic_expectation entries (now 12)
        — verify the 3.9.16 cases are still all correct.
        """
        summary = analyze_phase_3_9_14_for_semantic()
        # Phase 3.9.17 extended semantic_expectation to 12 cases; the
        # analyzer therefore yields 12 outcomes, all passing.
        assert summary.total_cases == 12
        assert summary.semantic_evaluable_cases == 12
        assert summary.semantic_correct_cases == 12
        assert summary.semantic_incorrect_cases == 0
        assert summary.semantic_result_correctness == 1.0
        assert summary.source_snapshot == (
            "phase_3_9_14_result_llm_baseline.json"
        )
        # The 3.9.16 snapshot file remains a frozen 3-case artifact.
        from backend.app.services.text_to_sql_semantic_result_evaluation_service import (
            SNAPSHOT_3_9_16_PATH,
        )
        if SNAPSHOT_3_9_16_PATH.exists():
            import json as _json
            blob = _json.loads(
                SNAPSHOT_3_9_16_PATH.read_text(encoding="utf-8")
            )
            assert blob["total_cases"] == 3
            assert blob["semantic_result_correctness"] == 1.0





class TestParseSemanticExpectationRules3917:
    """Phase 3.9.17 consistency rules (#2-#4)."""

    def test_rule2_required_and_optional_must_be_disjoint(self) -> None:
        with pytest.raises(ResultExpectationError):
            parse_semantic_expectation({
                "required_columns": ["id", "count"],
                "optional_columns": ["id"],
            })

    def test_rule3_required_and_forbidden_must_be_disjoint(self) -> None:
        with pytest.raises(ResultExpectationError):
            parse_semantic_expectation({
                "required_columns": ["id"],
                "forbidden_columns": ["ID"],  # case-insensitive
            })

    def test_rule4_expected_rows_must_match_required_columns_width(self) -> None:
        with pytest.raises(ResultExpectationError):
            parse_semantic_expectation({
                "required_columns": ["id", "count"],
                "expected_rows": [[1]],  # 1 value, 2 required cols
            })

    def test_dto_post_init_enforces_rule2(self) -> None:
        with pytest.raises(ResultExpectationError):
            SemanticExpectation(
                required_columns=("id",),
                optional_columns=("id",),
            )

    def test_dto_post_init_enforces_rule3(self) -> None:
        with pytest.raises(ResultExpectationError):
            SemanticExpectation(
                required_columns=("id",),
                forbidden_columns=("Id",),
            )

    def test_dto_post_init_enforces_rule4(self) -> None:
        with pytest.raises(ResultExpectationError):
            SemanticExpectation(
                required_columns=("id", "count"),
                expected_rows=((1,),),
            )


class TestSemanticConsistency3917:
    """Phase 3.9.17 cross-check (#5-#6) of the dataset's semantic ground truth."""

    def test_no_consistency_violations(self) -> None:
        problems = validate_semantic_consistency()
        assert problems == (), problems

    def test_result_evaluable_case_ids_size(self) -> None:
        assert len(RESULT_EVALUABLE_CASE_IDS) == 12

    def test_na_case_ids_size(self) -> None:
        assert len(NA_CASE_IDS) == 2

    def test_evaluable_and_na_are_disjoint(self) -> None:
        assert RESULT_EVALUABLE_CASE_IDS.isdisjoint(NA_CASE_IDS)

    def test_load_semantic_covers_all_evaluable(self) -> None:
        from backend.app.services.text_to_sql_result_evaluation_service import (
            load_semantic_expectations,
        )
        truth = load_semantic_expectations()
        assert set(truth) == RESULT_EVALUABLE_CASE_IDS
        for na_id in NA_CASE_IDS:
            assert na_id not in truth


class TestPerCaseSemanticResult3917:
    """Phase 3.9.17: each of the 12 result-evaluable cases evaluated against
    the saved 3.9.14 actual result columns/rows (must remain unchanged)."""

    def test_all_12_evaluable_pass(self) -> None:
        summary = analyze_phase_3_9_14_for_full_semantic()
        assert summary.semantic_evaluable_cases == 12
        assert summary.semantic_correct_cases == 12
        assert summary.semantic_incorrect_cases == 0
        assert summary.semantic_result_correctness == 1.0
        assert summary.semantic_ground_truth_coverage == 1.0

    def test_simple_document_list_pass(self) -> None:
        # saved actual: [id, title, file_type, created_at] x 4
        self._assert_case(
            "simple_document_list",
            expected_columns=("id", "title", "file_type", "created_at"),
            expected_rows=(
                (1, "Alpha Report", "md", "2025-06-10"),
                (2, "Beta Notes", "md", "2026-02-14"),
                (3, "Gamma Guide", "md", "2026-05-20"),
                (4, "Delta Manual", "txt", "2025-11-05"),
            ),
            required=("id",),
        )

    def test_top_n_chunks_by_token_count_pass(self) -> None:
        # saved actual: [id, document_id, content, token_count] x 10, ordered DESC
        rows = (
            (10, 4, "delta summary", 100), (9, 4, "delta extra", 90),
            (8, 4, "delta details", 80), (7, 4, "delta intro", 70),
            (6, 3, "gamma intro", 60), (5, 2, "beta summary", 50),
            (4, 2, "beta details", 40), (3, 2, "beta intro", 30),
            (2, 1, "alpha details", 20), (1, 1, "alpha intro", 10),
        )
        self._assert_case(
            "top_n_chunks_by_token_count",
            expected_columns=("id", "document_id", "content", "token_count"),
            expected_rows=rows,
            required=("id", "token_count"),
        )

    def test_chunks_ordered_by_token_count_pass(self) -> None:
        rows = (
            (10, 4, "delta summary", 100), (9, 4, "delta extra", 90),
            (8, 4, "delta details", 80), (7, 4, "delta intro", 70),
            (6, 3, "gamma intro", 60), (5, 2, "beta summary", 50),
            (4, 2, "beta details", 40), (3, 2, "beta intro", 30),
            (2, 1, "alpha details", 20), (1, 1, "alpha intro", 10),
        )
        self._assert_case(
            "chunks_ordered_by_token_count",
            expected_columns=("id", "document_id", "content", "token_count"),
            expected_rows=rows,
            required=("id", "token_count"),
        )

    def test_aggregate_document_count_pass(self) -> None:
        self._assert_case(
            "aggregate_document_count",
            expected_columns=("count",),
            expected_rows=((4,),),
            required=("count",),
        )

    def test_group_by_chunk_count_per_document_pass(self) -> None:
        # saved actual: [id, title, chunk_count] x 4 (3.9.14 strict-fail)
        self._assert_case(
            "group_by_chunk_count_per_document",
            expected_columns=("id", "title", "chunk_count"),
            expected_rows=(
                (4, "Delta Manual", 4),
                (2, "Beta Notes", 3),
                (3, "Gamma Guide", 1),
                (1, "Alpha Report", 2),
            ),
            required=("id", "chunk_count"),
        )

    def test_having_chunk_count_greater_than_pass(self) -> None:
        self._assert_case(
            "having_chunk_count_greater_than",
            expected_columns=("id", "title"),
            expected_rows=((4, "Delta Manual"), (2, "Beta Notes")),
            required=("id",),
        )

    def test_join_chunk_with_parent_document_pass(self) -> None:
        rows = (
            (1, "alpha intro", "Alpha Report"),
            (2, "alpha details", "Alpha Report"),
            (3, "beta intro", "Beta Notes"),
            (4, "beta details", "Beta Notes"),
            (5, "beta summary", "Beta Notes"),
            (6, "gamma intro", "Gamma Guide"),
            (7, "delta intro", "Delta Manual"),
            (8, "delta details", "Delta Manual"),
            (9, "delta extra", "Delta Manual"),
            (10, "delta summary", "Delta Manual"),
        )
        self._assert_case(
            "join_chunk_with_parent_document",
            expected_columns=("id", "content", "title"),
            expected_rows=rows,
            required=("id", "title"),
        )

    def test_date_filter_created_after_pass(self) -> None:
        rows = (
            (2, "Beta Notes", "md", "2026-02-14"),
            (3, "Gamma Guide", "md", "2026-05-20"),
        )
        self._assert_case(
            "date_filter_created_after",
            expected_columns=("id", "title", "file_type", "created_at"),
            expected_rows=rows,
            required=("id",),
        )

    def test_limit_first_10_documents_pass(self) -> None:
        rows = (
            (1, "Alpha Report", "md", "2025-06-10"),
            (2, "Beta Notes", "md", "2026-02-14"),
            (3, "Gamma Guide", "md", "2026-05-20"),
            (4, "Delta Manual", "txt", "2025-11-05"),
        )
        self._assert_case(
            "limit_first_10_documents",
            expected_columns=("id", "title", "file_type", "created_at"),
            expected_rows=rows,
            required=("id",),
        )

    def test_semantic_dependent_document_and_chunk_pass(self) -> None:
        self._assert_case(
            "semantic_dependent_document_and_chunk",
            expected_columns=("id", "title", "chunk_count"),
            expected_rows=(
                (4, "Delta Manual", 4),
                (2, "Beta Notes", 3),
                (3, "Gamma Guide", 1),
                (1, "Alpha Report", 2),
            ),
            required=("id", "chunk_count"),
        )

    def test_project_a_inventory_pass(self) -> None:
        self._assert_case(
            "project_a_inventory",
            expected_columns=("item_code", "qty"),
            expected_rows=(("item_x", 100), ("item_y", 5)),
            required=("item_code", "qty"),
        )

    def test_project_b_inventory_pass(self) -> None:
        self._assert_case(
            "project_b_inventory",
            expected_columns=("item_code", "qty"),
            expected_rows=(("item_x", 999), ("item_y", 7)),
            required=("item_code", "qty"),
        )

    def _assert_case(self, case_id, expected_columns, expected_rows,
                     required) -> None:
        summary = analyze_phase_3_9_14_for_full_semantic()
        outcome = next(o for o in summary.cases if o.case_id == case_id)
        assert outcome.actual_columns == expected_columns
        assert outcome.actual_rows == expected_rows
        assert outcome.semantic_passed is True
        assert tuple(outcome.expected_required_columns) == required
        assert SEMANTIC_CATEGORY_MISSING_REQUIRED not in (
            outcome.semantic_categories
        )
        assert SEMANTIC_CATEGORY_FORBIDDEN not in (
            outcome.semantic_categories
        )


class Test3917Snapshot:
    def test_snapshot_exists_and_valid(self) -> None:
        if not SNAPSHOT_3_9_17_PATH.exists():
            pytest.skip("snapshot not yet generated")
        payload = json.loads(SNAPSHOT_3_9_17_PATH.read_text(encoding="utf-8"))
        assert payload["phase"] == PHASE_3_9_17
        assert payload["semantic_evaluable_cases"] == 12
        assert payload["semantic_correct_cases"] == 12
        assert payload["semantic_incorrect_cases"] == 0
        assert payload["semantic_result_correctness"] == 1.0
        assert payload["semantic_ground_truth_coverage"] == 1.0
        assert payload["phase_3_9_14_strict_result_accuracy"] == 0.75
        assert payload["phase_3_9_14_strict_result_correct"] == 9
        assert payload["phase_3_9_14_strict_result_total"] == 12
        assert sorted(payload["result_evaluable_case_ids"]) == sorted(
            RESULT_EVALUABLE_CASE_IDS
        )
        assert sorted(payload["na_case_ids"]) == sorted(NA_CASE_IDS)
        for key in (
            "source_snapshot", "source_snapshot_sha256",
            "result_evaluable_case_ids", "na_case_ids", "cases",
        ):
            assert key in payload


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
