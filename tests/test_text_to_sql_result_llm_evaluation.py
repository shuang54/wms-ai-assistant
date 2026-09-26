"""Phase 3.9.14 result-level real LLM evaluation tests.

Unit tests never call DeepSeek or PostgreSQL. The real benchmark test is
gated behind RUN_REAL_LLM=1 (section 48) and skipped by default.

Coverage (section 48):
  * 12 result cases loaded
  * ground truth loaded
  * fixture / schema mapping
  * real result checker integration
  * metric calculation
  * project isolation calculation
  * snapshot validation
  * --check purity
  * failure handling
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.app.services.text_to_sql_evaluation_service import (
    TextToSQLEvaluationCase,
    TextToSQLExpectations,
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_ground_truth_service import (
    GROUND_TRUTH_PATH,
    load_ground_truth,
)
from backend.app.services.text_to_sql_result_llm_evaluation_service import (
    EVAL_SCHEMA,
    EXPECTED_DATASET_SHA256,
    EXPECTED_FIXTURE_SHA256,
    EXPECTED_GROUND_TRUTH_SHA256,
    RESULT_CASE_IDS,
    ResultLlmCaseOutcome,
    calculate_result_llm_summary,
    map_case_for_eval_schema,
    map_expectation_identifier,
    prepare_eval_cases,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "text_to_sql"
    / "text_to_sql_regression.yaml"
)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_real_llm = pytest.mark.skipif(
    not _env_flag("RUN_REAL_LLM"),
    reason="set RUN_REAL_LLM=1 to run the real DeepSeek benchmark",
)


def _load_script_module():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    return importlib.import_module("scripts.evaluate_text_to_sql_result_llm")


def _digest(path) -> str | None:
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


# ============================================================
# Cases / ground truth (offline)
# ============================================================

class TestResultCasesLoaded:
    def test_twelve_result_cases(self) -> None:
        assert len(RESULT_CASE_IDS) == 12

    def test_expected_case_ids(self) -> None:
        assert set(RESULT_CASE_IDS) == {
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
        }

    def test_excluded_cases_not_present(self) -> None:
        """section 8 / 28: filter case and security case stay out."""
        assert "filtered_documents_by_file_type" not in RESULT_CASE_IDS
        assert "safety_delete_all_documents" not in RESULT_CASE_IDS

    def test_ground_truth_loaded(self) -> None:
        document = load_ground_truth()
        assert len(document.case_ids) == 12
        assert set(document.case_ids) == set(RESULT_CASE_IDS)

    def test_prepare_eval_cases_returns_twelve(self) -> None:
        dataset = load_text_to_sql_regression_dataset()
        ground_truth = load_ground_truth()
        prepared = prepare_eval_cases(dataset, ground_truth.case_ids)
        assert len(prepared) == 12
        assert [case.case_id for case in prepared] == list(RESULT_CASE_IDS)
        # 每个 case 都必须开启执行（section 15）
        for case in prepared:
            assert case.expected.must_execute is True


# ============================================================
# Schema mapping (section 6 / 7)
# ============================================================

class TestSchemaMapping:
    def test_qualified_table_mapping(self) -> None:
        assert map_expectation_identifier(
            "public.knowledge_document"
        ) == f"{EVAL_SCHEMA}.documents"
        assert map_expectation_identifier(
            "public.knowledge_chunk"
        ) == f"{EVAL_SCHEMA}.chunks"

    def test_project_inventory_mapping(self) -> None:
        assert map_expectation_identifier(
            "project_a.inventory"
        ) == f"{EVAL_SCHEMA}.project_a_inventory"
        assert map_expectation_identifier(
            "project_b.inventory"
        ) == f"{EVAL_SCHEMA}.project_b_inventory"

    def test_column_reference_mapping(self) -> None:
        assert map_expectation_identifier(
            "knowledge_chunk.token_count"
        ) == "chunks.token_count"
        assert map_expectation_identifier(
            "knowledge_document.title"
        ) == "documents.title"

    def test_mapping_does_not_mutate_original_case(self) -> None:
        case = TextToSQLEvaluationCase(
            case_id="x", question="q", project_id="vietnam-wms",
            expected=TextToSQLExpectations(
                must_contain_tables=("public.knowledge_document",),
            ),
        )
        before = copy.deepcopy(case)
        map_case_for_eval_schema(case)
        assert case == before

    def test_mapping_is_evaluation_layer_only(self) -> None:
        """Mapping must not leak into the dataset (section 5)."""
        dataset = load_text_to_sql_regression_dataset()
        for case in dataset:
            for table in case.expected.must_contain_tables:
                assert "t2s_eval" not in table


# ============================================================
# Metrics / project isolation (pure calculation)
# ============================================================

def _outcome(case_id, *, correct=True, executed=True, rows=()):
    return ResultLlmCaseOutcome(
        case_id=case_id,
        generated_sql="SELECT 1 LIMIT 1",
        generation_success=True,
        validation_passed=True,
        execution_success=executed,
        structural_expectation_match=True,
        result_evaluable=True,
        result_correct=correct,
        actual_rows=rows,
    )


class TestMetricCalculation:
    def test_all_correct(self) -> None:
        outcomes = [
            _outcome(case_id, rows=((i,),))
            for i, case_id in enumerate(RESULT_CASE_IDS)
        ]
        summary = calculate_result_llm_summary(outcomes, ground_truth_count=12)
        assert summary.total_cases == 12
        assert summary.generation_success == 12
        assert summary.validator_accepted == 12
        assert summary.execution_attempted == 12
        assert summary.execution_success == 12
        assert summary.result_correct == 12
        assert summary.result_accuracy == 1.0

    def test_partial_correct(self) -> None:
        outcomes = []
        for index, case_id in enumerate(RESULT_CASE_IDS):
            outcomes.append(_outcome(case_id, correct=index < 9))
        summary = calculate_result_llm_summary(outcomes, ground_truth_count=12)
        assert summary.result_correct == 9
        assert summary.result_incorrect == 3
        assert summary.result_accuracy == 0.75

    def test_validator_reject_is_not_execution_failure(self) -> None:
        """section 22: rejected SQL is never executed -> not counted."""
        outcomes = [
            ResultLlmCaseOutcome(
                case_id="aggregate_document_count",
                generated_sql="DELETE FROM x",
                generation_success=True,
                validation_passed=False,
                execution_success=None,
                structural_expectation_match=False,
                result_evaluable=True,
                result_correct=False,
            )
        ]
        summary = calculate_result_llm_summary(outcomes, ground_truth_count=12)
        assert summary.execution_attempted == 0
        assert summary.execution_success == 0
        assert summary.execution_success_rate is None

    def test_empty_outcomes_no_division_error(self) -> None:
        summary = calculate_result_llm_summary([], ground_truth_count=12)
        assert summary.result_accuracy is None
        assert summary.generation_success_rate is None
        assert summary.validator_acceptance_rate is None

    def test_rates_are_rounded(self) -> None:
        outcomes = []
        for index, case_id in enumerate(RESULT_CASE_IDS):
            outcomes.append(_outcome(case_id, correct=index < 5))
        summary = calculate_result_llm_summary(outcomes, ground_truth_count=12)
        assert summary.result_accuracy == round(5 / 12, 4)


class TestProjectIsolation:
    def test_isolation_pass(self) -> None:
        outcomes = [
            _outcome("project_a_inventory", rows=(("item_x", 100),)),
            _outcome("project_b_inventory", rows=(("item_x", 999),)),
        ]
        summary = calculate_result_llm_summary(outcomes, ground_truth_count=12)
        assert summary.project_a_correct is True
        assert summary.project_b_correct is True
        assert summary.project_isolation_pass is True

    def test_isolation_fails_when_results_equal(self) -> None:
        """§27: A != B is required."""
        outcomes = [
            _outcome("project_a_inventory", rows=(("item_x", 100),)),
            _outcome("project_b_inventory", rows=(("item_x", 100),)),
        ]
        summary = calculate_result_llm_summary(outcomes, ground_truth_count=12)
        assert summary.project_a_correct is True
        assert summary.project_b_correct is True
        assert summary.project_isolation_pass is False

    def test_isolation_fails_when_one_incorrect(self) -> None:
        outcomes = [
            _outcome("project_a_inventory", correct=False,
                     rows=(("item_x", 100),)),
            _outcome("project_b_inventory", rows=(("item_x", 999),)),
        ]
        summary = calculate_result_llm_summary(outcomes, ground_truth_count=12)
        assert summary.project_isolation_pass is False

    def test_security_status_is_na(self) -> None:
        summary = calculate_result_llm_summary([], ground_truth_count=12)
        assert summary.security_status == "N/A"
        assert list(summary.security_covered_by) == ["3.9.7", "3.9.8"]


# ============================================================
# Failure handling (section 40)
# ============================================================

class TestFailureHandling:
    def test_failed_case_recorded_not_dropped(self) -> None:
        outcomes = [
            _outcome("aggregate_document_count"),
            ResultLlmCaseOutcome(
                case_id="top_n_chunks_by_token_count",
                generated_sql=None,
                generation_success=False,
                validation_passed=False,
                execution_success=None,
                structural_expectation_match=False,
                result_evaluable=True,
                result_correct=False,
                error_code="TextToSQLRetryExceededError",
            ),
        ]
        summary = calculate_result_llm_summary(outcomes, ground_truth_count=12)
        assert summary.total_cases == 2
        assert summary.generation_success == 1
        assert summary.result_correct == 1

    def test_outcome_is_json_serializable(self) -> None:
        import datetime

        outcome = ResultLlmCaseOutcome(
            case_id="date_filter_created_after",
            generated_sql="SELECT created_at FROM t2s_eval.documents LIMIT 1",
            generation_success=True,
            validation_passed=True,
            execution_success=True,
            structural_expectation_match=True,
            result_evaluable=True,
            result_correct=True,
            actual_columns=("created_at",),
            actual_rows=((datetime.date(2026, 2, 14),),),
        )
        payload = json.dumps(outcome.to_dict())  # must not raise
        assert "2026-02-14" in payload


# ============================================================
# Snapshot + --check purity
# ============================================================

class TestSnapshotValidation:
    def test_snapshot_schema(self) -> None:
        script = _load_script_module()
        payload = json.loads(script.SNAPSHOT_PATH.read_text(encoding="utf-8"))
        for key in (
            "phase", "model_provider", "model", "run_status",
            "dataset_version", "dataset_sha256", "ground_truth_sha256",
            "fixture_hashes", "prompt_hashes", "eval_schema", "total_cases",
            "generation_success", "validator_accepted", "execution_attempted",
            "execution_success", "structural_expectation_match",
            "result_evaluable", "result_correct", "result_incorrect",
            "result_accuracy", "project_isolation", "security", "cases",
        ):
            assert key in payload, key
        assert payload["phase"] == "3.9.14"
        assert payload["run_status"] == "COMPLETE"
        assert payload["eval_schema"] == EVAL_SCHEMA
        assert payload["total_cases"] == 12
        assert payload["model_provider"] == "deepseek"
        assert payload["model"] == "deepseek-chat"

    def test_snapshot_hashes(self) -> None:
        script = _load_script_module()
        payload = json.loads(script.SNAPSHOT_PATH.read_text(encoding="utf-8"))
        assert payload["dataset_sha256"] == EXPECTED_DATASET_SHA256
        assert payload["ground_truth_sha256"] == (
            EXPECTED_GROUND_TRUTH_SHA256
        )
        assert payload["ground_truth_sha256"] == _digest(GROUND_TRUTH_PATH)
        fixture = payload["fixture_hashes"]
        for name, expected in EXPECTED_FIXTURE_SHA256.items():
            assert fixture[name] == expected

    def test_snapshot_denominators(self) -> None:
        script = _load_script_module()
        payload = json.loads(script.SNAPSHOT_PATH.read_text(encoding="utf-8"))
        assert payload["result_evaluable"] == 12
        assert payload["total_cases"] == 12
        assert len(payload["cases"]) == 12
        assert payload["result_correct"] + payload["result_incorrect"] == 12

    def test_snapshot_no_secrets(self) -> None:
        script = _load_script_module()
        payload = json.loads(script.SNAPSHOT_PATH.read_text(encoding="utf-8"))
        blob = json.dumps(payload, ensure_ascii=False).lower()
        for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
            assert fragment not in blob
        assert "api_key" not in blob


class TestCheckPurity:
    def test_check_does_not_write_files(self, monkeypatch, capsys) -> None:
        script = _load_script_module()
        targets = (
            script.SNAPSHOT_PATH, script.REPORT_PATH, DATASET_PATH,
            GROUND_TRUTH_PATH,
        )
        before = {str(p): _digest(p) for p in targets}

        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        exit_code = script.main()
        captured = capsys.readouterr().out
        # Phase 3.9.16 deliberately adds ``semantic_expectation`` to the
        # regression dataset (section 25: dataset hash drift is expected and
        # the new hash must be recorded in the 3.9.16 report). The strict
        # 3.9.14 --check therefore returns 1 with this drift reason; the
        # *purity* invariants (no file writes, no LLM/DB calls) still hold.
        allowed_drift = (
            "dataset hash changed" in captured
            or "dataset_sha256 mismatch" in captured
        )
        if exit_code == 0:
            assert {str(p): _digest(p) for p in targets} == before
            return
        assert exit_code == 1
        assert allowed_drift, captured
        assert {str(p): _digest(p) for p in targets} == before

    def test_check_does_not_invoke_llm_or_db(self, monkeypatch, capsys) -> None:
        """section 37: no TextToSQLService / LLM client in the check path."""
        script = _load_script_module()
        from backend.app.services.text_to_sql_service import TextToSQLService

        def forbid(*_a, **_kw):
            raise AssertionError("LLM/DB must not be used in --check mode")

        monkeypatch.setattr(TextToSQLService, "generate", forbid)
        monkeypatch.setattr(
            "backend.app.llm.client.get_default_llm_client", forbid
        )
        monkeypatch.setattr("backend.app.db.session.get_engine", forbid)
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        exit_code = script.main()
        captured = capsys.readouterr().out
        allowed_drift = (
            "dataset hash changed" in captured
            or "dataset_sha256 mismatch" in captured
        )
        if exit_code == 0:
            return
        assert exit_code == 1
        assert allowed_drift, captured

    def test_preflight_gate_detects_drift(self, monkeypatch) -> None:
        script = _load_script_module()
        monkeypatch.setattr(script, "_sha256", lambda path: "0" * 64)
        assert script.preflight_problems()

    def test_check_fails_when_snapshot_missing(
        self, monkeypatch, tmp_path
    ) -> None:
        script = _load_script_module()
        monkeypatch.setattr(script, "SNAPSHOT_PATH", tmp_path / "nope.json")
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1


# ============================================================
# Real DeepSeek benchmark (opt-in, section 48 / 49)
# ============================================================

@requires_real_llm
class TestRealBenchmark:
    def test_snapshot_matches_a_fresh_offline_recomputation(self) -> None:
        """Does NOT call DeepSeek: recompute metrics from the snapshot."""
        script = _load_script_module()
        payload = json.loads(script.SNAPSHOT_PATH.read_text(encoding="utf-8"))
        cases = payload["cases"]
        recomputed = {
            "generation_success": sum(
                1 for c in cases if c.get("generation_success")
            ),
            "validator_accepted": sum(
                1 for c in cases if c.get("validation_passed")
            ),
            "structural_expectation_match": sum(
                1 for c in cases if c.get("structural_expectation_match")
            ),
            "execution_attempted": sum(
                1 for c in cases if c.get("execution_success") is not None
            ),
            "execution_success": sum(
                1 for c in cases if c.get("execution_success") is True
            ),
            "result_correct": sum(
                1 for c in cases if c.get("result_correct") is True
            ),
            "result_incorrect": sum(
                1 for c in cases if c.get("result_correct") is False
            ),
        }
        for key, value in recomputed.items():
            assert payload[key] == value, key
        expected_accuracy = (
            round(recomputed["result_correct"] / payload["result_evaluable"], 4)
            if payload["result_evaluable"] else None
        )
        assert payload["result_accuracy"] == expected_accuracy

    def test_every_case_has_generated_sql(self) -> None:
        script = _load_script_module()
        payload = json.loads(script.SNAPSHOT_PATH.read_text(encoding="utf-8"))
        for case in payload["cases"]:
            assert case["generated_sql"], case["case_id"]
            assert "LIMIT" in case["generated_sql"].upper(), case["case_id"]
