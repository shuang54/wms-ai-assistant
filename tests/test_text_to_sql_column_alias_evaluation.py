"""Phase 3.9.18 — Semantic column alias mapping tests.

Scope: evaluation layer only. No DeepSeek, no DB, no network.

Covers (spec sections §十 / §十四):

- exact match
- alias match (same business entity)
- wrong-entity rejection (`documents.id` must NOT match `chunk_id`)
- aggregate alias with correct entity context
- aggregate alias with WRONG entity context (must fail)
- unknown alias rejection (`document_code` != `document_id`)
- strict projection stays alias-free
- semantic mode accepts a provable alias
"""
from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from backend.app.services.text_to_sql_column_alias_evaluation_service import (
    AGGREGATE_COUNT,
    DIAGNOSTIC_ALIAS_MATCH,
    DIAGNOSTIC_EXACT_MATCH,
    DIAGNOSTIC_UNKNOWN_COLUMN,
    ENTITY_CHUNK,
    ENTITY_DOCUMENT,
    ENTITY_INVENTORY_ITEM,
    MATCH_ALIAS,
    MATCH_EXACT,
    MATCH_NONE,
    CASE_ALIAS_CONTEXTS,
    NA_CASE_ALIAS_CONTEXTS,
    ColumnAliasContext,
    alias_context_for_case,
    resolve_required_columns,
    resolve_semantic_column,
    validate_alias_registry,
)
from backend.app.services.text_to_sql_result_evaluation_service import (
    RESULT_TYPE_COLUMN_VALUES,
    ResultCheckInput,
    ResultExpectation,
    SemanticExpectation,
    TextToSQLResultEvaluationService,
)
from backend.app.services.text_to_sql_semantic_result_evaluation_service import (
    RESULT_EVALUABLE_CASE_IDS,
    analyze_phase_3_9_14_for_alias_audit,
    analyze_phase_3_9_14_for_full_semantic,
)


def service() -> TextToSQLResultEvaluationService:
    return TextToSQLResultEvaluationService()


def _sem(required=(), optional=(), forbidden=(), rows=()):
    return SemanticExpectation(
        required_columns=tuple(required),
        optional_columns=tuple(optional),
        forbidden_columns=tuple(forbidden),
        expected_rows=tuple(tuple(r) for r in rows),
    )


def _ctx_document() -> ColumnAliasContext:
    return ColumnAliasContext(entity=ENTITY_DOCUMENT)


def _ctx_chunk() -> ColumnAliasContext:
    return ColumnAliasContext(entity=ENTITY_CHUNK)


def _ctx_document_count() -> ColumnAliasContext:
    return ColumnAliasContext(entity=ENTITY_DOCUMENT, aggregate=AGGREGATE_COUNT)


def _ctx_chunk_count() -> ColumnAliasContext:
    return ColumnAliasContext(entity=ENTITY_CHUNK, aggregate=AGGREGATE_COUNT)


# ============================================================
# Registry integrity
# ============================================================

class TestAliasRegistry:
    def test_registry_is_internally_consistent(self) -> None:
        assert validate_alias_registry() == ()

    def test_entity_scope_is_declared_for_every_concept(self) -> None:
        from backend.app.services.text_to_sql_column_alias_evaluation_service import (
            SEMANTIC_COLUMN_ALIASES,
        )
        for concept in SEMANTIC_COLUMN_ALIASES:
            assert concept.entity
            assert concept.source_table
            assert concept.source_column
            assert concept.semantic_name in concept.aliases

    def test_id_is_never_a_global_alias(self) -> None:
        """`id` must be claimed by more than one entity — otherwise the
        model collapses back into unsafe global substitution."""
        from backend.app.services.text_to_sql_column_alias_evaluation_service import (
            SEMANTIC_COLUMN_ALIASES,
        )
        owners = {
            c.entity for c in SEMANTIC_COLUMN_ALIASES if "id" in c.aliases
        }
        assert ENTITY_DOCUMENT in owners
        assert ENTITY_CHUNK in owners

    def test_case_contexts_cover_exactly_the_evaluable_cases(self) -> None:
        assert set(CASE_ALIAS_CONTEXTS) == set(RESULT_EVALUABLE_CASE_IDS)

    def test_na_cases_never_declare_context(self) -> None:
        for case_id in NA_CASE_ALIAS_CONTEXTS:
            assert alias_context_for_case(case_id) == {}
            assert case_id not in CASE_ALIAS_CONTEXTS

    def test_unknown_case_has_no_context(self) -> None:
        assert alias_context_for_case("does_not_exist") == {}
        assert alias_context_for_case(None) == {}


# ============================================================
# Level 1 — exact match (§七 priority 1)
# ============================================================

class TestExactMatch:
    def test_expected_document_id_actual_document_id_is_exact(self) -> None:
        r = resolve_semantic_column(
            expected_column="document_id",
            actual_columns=("document_id", "title"),
            context=_ctx_document(),
        )
        assert r.match_kind == MATCH_EXACT
        assert r.diagnostic == DIAGNOSTIC_EXACT_MATCH
        assert r.actual_column == "document_id"

    def test_exact_match_needs_no_context(self) -> None:
        r = resolve_semantic_column(
            expected_column="count",
            actual_columns=("count",),
            context=None,
        )
        assert r.match_kind == MATCH_EXACT

    def test_exact_match_is_case_insensitive(self) -> None:
        r = resolve_semantic_column(
            expected_column="Document_ID",
            actual_columns=("document_id",),
        )
        assert r.match_kind == MATCH_EXACT
        assert r.actual_column == "document_id"


# ============================================================
# Level 2 — explicit semantic alias (§十 Test 1 / 2 / 3 / 5)
# ============================================================

class TestAliasMatchSameEntity:
    def test_documents_id_matches_document_id(self) -> None:
        """Test 1: documents.id may be projected as `document_id`."""
        r = resolve_semantic_column(
            expected_column="document_id",
            actual_columns=("id", "title"),
            context=_ctx_document(),
        )
        assert r.match_kind == MATCH_ALIAS
        assert r.diagnostic == DIAGNOSTIC_ALIAS_MATCH
        assert r.actual_column == "id"
        assert r.semantic_name == "document_id"
        assert r.entity == ENTITY_DOCUMENT

    def test_chunks_id_matches_chunk_id(self) -> None:
        """Test 2: chunks.id may be projected as `chunk_id`."""
        r = resolve_semantic_column(
            expected_column="chunk_id",
            actual_columns=("id", "content"),
            context=_ctx_chunk(),
        )
        assert r.match_kind == MATCH_ALIAS
        assert r.actual_column == "id"
        assert r.semantic_name == "chunk_id"

    def test_documents_id_does_not_match_chunk_id(self) -> None:
        """Test 3: both are called `id`, but they are different entities."""
        r = resolve_semantic_column(
            expected_column="document_id",
            actual_columns=("chunk_id", "content"),
            context=_ctx_document(),
        )
        assert r.match_kind == MATCH_NONE
        assert r.diagnostic == DIAGNOSTIC_UNKNOWN_COLUMN
        assert r.actual_column is None

    def test_bare_id_is_never_resolved_without_context(self) -> None:
        r = resolve_semantic_column(
            expected_column="id",
            actual_columns=("chunk_id",),
            context=None,
        )
        assert r.match_kind == MATCH_NONE

    def test_bare_id_under_document_context_does_not_match_chunk_id(
        self,
    ) -> None:
        r = resolve_semantic_column(
            expected_column="id",
            actual_columns=("chunk_id",),
            context=_ctx_document(),
        )
        assert r.match_kind == MATCH_NONE

    def test_unknown_alias_is_rejected(self) -> None:
        """Test 5: `document_code` is NOT `document_id`."""
        r = resolve_semantic_column(
            expected_column="document_id",
            actual_columns=("document_code",),
            context=_ctx_document(),
        )
        assert r.match_kind == MATCH_NONE
        assert r.actual_column is None

    def test_undeclared_column_name_is_rejected(self) -> None:
        r = resolve_semantic_column(
            expected_column="document_uuid",
            actual_columns=("id",),
            context=_ctx_document(),
        )
        assert r.match_kind == MATCH_NONE


# ============================================================
# Level 3 — context-aware aggregate alias (§十 Test 4, §十三)
# ============================================================

class TestAggregateAlias:
    def test_total_documents_matches_count_under_document_context(
        self,
    ) -> None:
        r = resolve_semantic_column(
            expected_column="total_documents",
            actual_columns=("count",),
            context=_ctx_document_count(),
        )
        assert r.match_kind == MATCH_ALIAS
        assert r.actual_column == "count"
        assert r.semantic_name == "document_count"
        assert r.entity == ENTITY_DOCUMENT

    def test_count_matches_total_documents_under_document_context(
        self,
    ) -> None:
        r = resolve_semantic_column(
            expected_column="count",
            actual_columns=("total_documents",),
            context=_ctx_document_count(),
        )
        assert r.match_kind == MATCH_ALIAS
        assert r.actual_column == "total_documents"

    def test_total_documents_never_matches_count_under_chunk_context(
        self,
    ) -> None:
        """COUNT(chunks) is NOT a document count."""
        r = resolve_semantic_column(
            expected_column="total_documents",
            actual_columns=("count",),
            context=_ctx_chunk_count(),
        )
        assert r.match_kind == MATCH_NONE
        assert r.diagnostic == DIAGNOSTIC_UNKNOWN_COLUMN

    def test_count_is_ambiguous_without_entity_context(self) -> None:
        """`count` alone belongs to several entities -> UNKNOWN, not guess."""
        r = resolve_semantic_column(
            expected_column="count",
            actual_columns=("total_documents",),
            context=None,
        )
        assert r.match_kind == MATCH_NONE

    def test_chunk_count_does_not_match_total_documents(self) -> None:
        r = resolve_semantic_column(
            expected_column="count",
            actual_columns=("total_documents",),
            context=_ctx_chunk_count(),
        )
        assert r.match_kind == MATCH_NONE

    def test_chunk_count_alias_under_chunk_context(self) -> None:
        r = resolve_semantic_column(
            expected_column="count",
            actual_columns=("chunk_count",),
            context=_ctx_chunk_count(),
        )
        assert r.match_kind == MATCH_ALIAS
        assert r.semantic_name == "chunk_count"

    def test_count_star_default_column_stays_exact_when_present(self) -> None:
        """The saved 3.9.14 projection `count` still matches exactly."""
        r = resolve_semantic_column(
            expected_column="count",
            actual_columns=("count",),
            context=_ctx_document_count(),
        )
        assert r.match_kind == MATCH_EXACT


# ============================================================
# Strict vs Semantic (§八 / §十四)
# ============================================================

class TestStrictProjectionUnaffected:
    def test_strict_column_values_rejects_alias(self) -> None:
        """Strict mode: expected document_id / actual id -> FAIL."""
        r = service().check(ResultCheckInput(
            case_id="simple_document_list",
            columns=("id",),
            rows=((1,), (2,)),
            expectation=ResultExpectation(
                type=RESULT_TYPE_COLUMN_VALUES,
                column="document_id",
                values=(1, 2),
                ordered=False,
            ),
        ))
        assert r.passed is False
        assert "not found" in r.reason

    def test_strict_unordered_rows_rejects_alias_column(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="simple_document_list",
            columns=("id",),
            rows=((1,), (2,)),
            expectation=ResultExpectation(
                type=RESULT_TYPE_COLUMN_VALUES,
                column="document_id",
                values=(1, 2),
            ),
        ))
        assert r.passed is False
        # Semantic side was not requested -> stays N/A.
        assert r.semantic_passed is None


class TestSemanticAcceptsProvableAlias:
    def test_semantic_mode_accepts_alias(self) -> None:
        """Semantic mode: expected document_id / actual id -> PASS."""
        r = service().check(ResultCheckInput(
            case_id="simple_document_list",
            columns=("id",),
            rows=((1,), (2,)),
            semantic_expectation=_sem(required=("document_id",),
                                      rows=[(1,), (2,)]),
        ))
        assert r.semantic_passed is True
        assert len(r.semantic_column_matches) == 1
        match = r.semantic_column_matches[0]
        assert match.match_kind == MATCH_ALIAS
        assert match.diagnostic == DIAGNOSTIC_ALIAS_MATCH
        assert match.to_dict()["matched_by"] == MATCH_ALIAS

    def test_alias_match_is_not_a_failure_category(self) -> None:
        """ALIAS_MATCH is a diagnostic, never a hard-failure category."""
        r = service().check(ResultCheckInput(
            case_id="simple_document_list",
            columns=("id",),
            rows=((1,), (2,)),
            semantic_expectation=_sem(required=("document_id",),
                                      rows=[(1,), (2,)]),
        ))
        assert r.semantic_categories == ()

    def test_semantic_rejects_wrong_entity_alias(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="simple_document_list",
            columns=("chunk_id",),
            rows=((1,), (2,)),
            semantic_expectation=_sem(required=("document_id",),
                                      rows=[(1,), (2,)]),
        ))
        assert r.semantic_passed is False
        assert "MISSING_REQUIRED_COLUMN" in r.semantic_categories

    def test_semantic_rejects_unknown_alias(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="simple_document_list",
            columns=("document_code",),
            rows=((1,), (2,)),
            semantic_expectation=_sem(required=("document_id",),
                                      rows=[(1,), (2,)]),
        ))
        assert r.semantic_passed is False

    def test_alias_matched_column_is_not_undeclared_extra(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="simple_document_list",
            columns=("id",),
            rows=((1,), (2,)),
            semantic_expectation=_sem(required=("document_id",),
                                      rows=[(1,), (2,)]),
        ))
        assert "UNDECLARED_EXTRA_COLUMN" not in r.semantic_categories

    def test_aggregate_alias_through_the_service(self) -> None:
        """`aggregate_document_count` with a `total_documents` projection
        now passes semantically (the 3.9.17 known limitation)."""
        r = service().check(ResultCheckInput(
            case_id="aggregate_document_count",
            columns=("total_documents",),
            rows=((4,),),
            semantic_expectation=_sem(required=("count",), rows=[(4,)]),
        ))
        assert r.semantic_passed is True
        assert r.semantic_column_matches[0].match_kind == MATCH_ALIAS

    def test_aggregate_wrong_entity_through_the_service(self) -> None:
        r = service().check(ResultCheckInput(
            case_id="aggregate_document_count",
            columns=("total_chunks",),
            rows=((4,),),
            semantic_expectation=_sem(required=("count",), rows=[(4,)]),
        ))
        assert r.semantic_passed is False


# ============================================================
# Determinism / purity (§六 / §七)
# ============================================================

class TestDeterminismAndPurity:
    def test_repeated_resolution_is_identical(self) -> None:
        args = {
            "expected_column": "document_id",
            "actual_columns": ("id", "title"),
            "context": _ctx_document(),
        }
        first = resolve_semantic_column(**args)
        second = resolve_semantic_column(**args)
        assert first == second
        assert first.to_dict() == second.to_dict()

    def test_actual_column_order_is_stable(self) -> None:
        r = resolve_semantic_column(
            expected_column="document_id",
            actual_columns=("id", "document_id"),
        )
        assert r.match_kind == MATCH_EXACT
        assert r.actual_column == "document_id"

    def test_no_fuzzy_matching(self) -> None:
        """Near-miss strings must NOT be matched."""
        for actual in ("document_ids", "doc_id", "documentid", "doc"):
            r = resolve_semantic_column(
                expected_column="document_id",
                actual_columns=(actual,),
                context=_ctx_document(),
            )
            assert r.match_kind == MATCH_NONE, actual

    def test_resolve_required_columns_returns_one_entry_per_required(
        self,
    ) -> None:
        out = resolve_required_columns(
            ("id", "chunk_count"),
            ("id", "title", "chunk_count"),
            {
                "id": _ctx_document(),
                "chunk_count": _ctx_chunk_count(),
            },
        )
        assert len(out) == 2
        assert [item.match_kind for item in out] == [MATCH_EXACT, MATCH_EXACT]


# ============================================================
# 12-case alias audit regression guard (§十一 / §十五)
# ============================================================

class TestAliasAudit:
    def test_semantic_accuracy_not_reduced(self) -> None:
        audit = analyze_phase_3_9_14_for_alias_audit()
        assert audit.phase_3_9_17_semantic_accuracy == 1.0
        assert audit.phase_3_9_18_semantic_accuracy == 1.0
        assert audit.phase_3_9_18_semantic_correct == 12
        assert audit.semantic_evaluable_cases == 12

    def test_no_alias_is_needed_for_the_current_12_cases(self) -> None:
        """The saved 3.9.14 projections already use the expected names."""
        audit = analyze_phase_3_9_14_for_alias_audit()
        assert audit.alias_matches == 0
        assert audit.alias_needed_cases == 0
        assert audit.unmatched_columns == 0
        assert audit.exact_matches > 0

    def test_full_semantic_summary_unchanged(self) -> None:
        summary = analyze_phase_3_9_14_for_full_semantic()
        assert summary.semantic_correct_cases == 12
        assert summary.semantic_incorrect_cases == 0
        assert summary.semantic_result_correctness == 1.0

    def test_every_required_column_is_resolved(self) -> None:
        for outcome in analyze_phase_3_9_14_for_full_semantic().cases:
            assert len(outcome.column_matches) == len(
                outcome.expected_required_columns
            )
            for item in outcome.column_matches:
                assert item.match_kind in (MATCH_EXACT, MATCH_ALIAS)


class Test3918Snapshot:
    def test_snapshot_exists_and_valid(self) -> None:
        from backend.app.services.text_to_sql_semantic_result_evaluation_service import (
            PHASE_3_9_18,
            SNAPSHOT_3_9_18_PATH,
        )
        if not SNAPSHOT_3_9_18_PATH.exists():
            pytest.skip("snapshot not yet generated")
        payload = json.loads(SNAPSHOT_3_9_18_PATH.read_text(encoding="utf-8"))
        assert payload["phase"] == PHASE_3_9_18
        assert payload["phase_3_9_17_semantic_accuracy"] == 1.0
        assert payload["phase_3_9_18_semantic_accuracy"] == 1.0
        assert payload["semantic_evaluable_cases"] == 12
        assert payload["alias_matches"] == 0
        assert payload["unmatched_columns"] == 0
        assert payload["exact_matches"] > 0
        blob = json.dumps(payload, ensure_ascii=False).lower()
        for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
            assert fragment not in blob
