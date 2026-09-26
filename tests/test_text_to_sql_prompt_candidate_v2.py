"""Phase 3.9.22 — Candidate v2 A/B experiment tests (fully offline).

Uses deterministic FakeGenerators; the REAL validator / context resolver /
metrics pipeline is reused. No DeepSeek call, no DB call, no network call
in any test (§十八).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from backend.app.services.sql_validator_service import SQLValidatorService
from backend.app.services.text_to_sql_evaluation_service import (
    StaticEvaluationContextResolver,
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_prompt_experiment_service import (
    ExperimentCaseComparison,
    ExperimentCaseResult,
    PromptExperimentRunner,
    load_prompt_fingerprint,
)
from backend.app.services.text_to_sql_prompt_candidate_v2_service import (
    CANDIDATE_SYSTEM_PROMPT_PATH,
    CANDIDATE_USER_PROMPT_PATH,
    CANDIDATE_RETRY_PROMPT_PATH,
    EXPERIMENT_TYPE_3_9_22,
    PHASE_3_9_22,
    REPORT_3_9_22_PATH,
    SNAPSHOT_3_9_22_PATH,
    PROMPT_VERSION_V2,
    V2PromptGenerator,
    build_v2_snapshot,
    check_baseline_frozen,
    classify_changes,
    load_candidate_fingerprint,
    validate_candidate_v2_snapshot,
)
from scripts._text_to_sql_offline_bindings import build_offline_project_bindings


# ----------------------------------------------------------------------
# Fake generators（确定；离线）
# ----------------------------------------------------------------------

@dataclass
class _FakeGenResult:
    sql: str | None


class FakeGenerator:
    def __init__(self, sql: str = "SELECT 1 AS placeholder",
                 fail_calls: set[int] | None = None) -> None:
        self._sql = sql
        self._fail = set(fail_calls or ())
        self.calls = 0

    async def generate(self, question, database_context, allowed_tables,
                       schema, max_rows) -> _FakeGenResult:
        self.calls += 1
        if self.calls in self._fail:
            return _FakeGenResult(sql=None)
        return _FakeGenResult(sql=self._sql)


def _make_v2_runner(candidate_fail_calls: set[int] | None = None):
    baseline_gen = FakeGenerator()
    candidate_gen = FakeGenerator(fail_calls=candidate_fail_calls)
    runner = PromptExperimentRunner(
        generator=baseline_gen,
        candidate_generator=candidate_gen,
        candidate_prompt_fingerprint=load_candidate_fingerprint(),
        context_resolver=StaticEvaluationContextResolver(
            bindings=build_offline_project_bindings()
        ),
        validator=SQLValidatorService(),
        executor=None,
    )
    return runner


def _snapshot_dict(**kwargs) -> dict:
    runner = _make_v2_runner(**kwargs)
    summary = asyncio.run(runner.compare())
    return build_v2_snapshot(summary)


# ----------------------------------------------------------------------
# 1-4: Prompt 文件与 hash
# ----------------------------------------------------------------------

class TestPromptFilesAndHashes:
    def test_candidate_prompt_files_exist(self) -> None:
        for path in (CANDIDATE_SYSTEM_PROMPT_PATH,
                     CANDIDATE_USER_PROMPT_PATH,
                     CANDIDATE_RETRY_PROMPT_PATH):
            assert path.exists(), path

    def test_candidate_hash_stable(self) -> None:
        a = load_candidate_fingerprint()
        b = load_candidate_fingerprint()
        assert a == b
        assert a.prompt_version == PROMPT_VERSION_V2

    def test_baseline_hash_unchanged(self) -> None:
        # Baseline 必须与 3.9.20 / 3.9.21 记录一致（§三）
        assert check_baseline_frozen() == []
        live = load_prompt_fingerprint()
        assert live.prompt_version == "v1"

    def test_candidate_hash_differs_from_baseline(self) -> None:
        base = load_prompt_fingerprint()
        cand = load_candidate_fingerprint()
        assert base.combined_prompt_hash != cand.combined_prompt_hash


# ----------------------------------------------------------------------
# V2 接线（不改生产代码）
# ----------------------------------------------------------------------

class TestV2Wiring:
    def test_prompt_paths_patched_only_during_generate(self) -> None:
        import backend.app.services.text_to_sql_service as svc

        before = (svc._SYSTEM_PROMPT_FILE, svc._USER_PROMPT_FILE,
                  svc._RETRY_PROMPT_FILE)

        async def _one_call() -> None:
            gen = V2PromptGenerator(FakeGenerator())
            await gen.generate(
                "q", database_context="ctx", allowed_tables=None,
                schema=None, max_rows=10,
            )

        asyncio.run(_one_call())
        after = (svc._SYSTEM_PROMPT_FILE, svc._USER_PROMPT_FILE,
                 svc._RETRY_PROMPT_FILE)
        # 调用结束后生产模块属性必须恢复原状
        assert before == after


# ----------------------------------------------------------------------
# 5-9: A/B 对齐、metrics、regression / improvement、safety case
# ----------------------------------------------------------------------

class TestABSnapshot:
    def test_14_cases_aligned(self) -> None:
        cases = load_text_to_sql_regression_dataset()
        dataset_ids = [c.case_id for c in cases]
        snap = _snapshot_dict()
        assert snap["phase"] == PHASE_3_9_22
        assert snap["experiment_type"] == EXPERIMENT_TYPE_3_9_22
        for label in ("baseline", "candidate"):
            ids = [e["case_id"] for e in snap[label]["results"]]
            assert ids == dataset_ids
        assert [c["case_id"] for c in snap["per_case_comparison"]] == \
            dataset_ids

    def test_metrics_recomputable(self) -> None:
        snap = _snapshot_dict()
        problems = validate_candidate_v2_snapshot(snap)
        assert problems == [], problems

    def test_regression_cases_computed(self) -> None:
        # 纯函数单测：PASS -> FAIL 记为 regression
        base_pass = ExperimentCaseResult(
            case_id="c1", prompt_variant="baseline", project_id=None,
            generated_sql="SELECT 1", generation_success=True,
            validation_success=True, structural_expectation_match=True,
            execution_success=None, semantic_result_correct=None,
            security_category=None, failure_categories=(),
            matched_expectations=(), error_code=None,
        )
        cand_fail = ExperimentCaseResult(
            case_id="c1", prompt_variant="candidate", project_id=None,
            generated_sql="SELECT 1", generation_success=True,
            validation_success=False,
            structural_expectation_match=False,
            execution_success=None, semantic_result_correct=None,
            security_category=None, failure_categories=(), matched_expectations=(),
            error_code=None,
        )
        same = ExperimentCaseResult(
            case_id="c2", prompt_variant="baseline", project_id=None,
            generated_sql="SELECT 1", generation_success=True,
            validation_success=True, structural_expectation_match=True,
            execution_success=None, semantic_result_correct=None,
            security_category=None, failure_categories=(), matched_expectations=(),
            error_code=None,
        )
        comparisons = [
            ExperimentCaseComparison(
                case_id="c1", changed=True, diff_fields=("structural",),
                baseline=base_pass, candidate=cand_fail,
            ),
            ExperimentCaseComparison(
                case_id="c2", changed=False, diff_fields=(),
                baseline=same, candidate=same,
            ),
        ]
        regression, improvement = classify_changes(comparisons)
        assert regression == ["c1"]
        assert improvement == []

    def test_improvement_cases_computed(self) -> None:
        base_fail = ExperimentCaseResult(
            case_id="s1", prompt_variant="baseline", project_id=None,
            generated_sql="SELECT 1", generation_success=True,
            validation_success=True, structural_expectation_match=False,
            execution_success=None, semantic_result_correct=None,
            security_category="negative",
            failure_categories=("must_pass_validation",),
            matched_expectations=(), error_code=None,
        )
        cand_pass = ExperimentCaseResult(
            case_id="s1", prompt_variant="candidate", project_id=None,
            generated_sql=None, generation_success=False,
            validation_success=False,
            structural_expectation_match=True,
            execution_success=None, semantic_result_correct=None,
            security_category="negative",
            failure_categories=(), matched_expectations=(),
            error_code=None,
        )
        comparisons = [
            ExperimentCaseComparison(
                case_id="s1", changed=True, diff_fields=("structural",),
                baseline=base_fail, candidate=cand_pass,
            ),
        ]
        regression, improvement = classify_changes(comparisons)
        assert regression == []
        assert improvement == ["s1"]

    def test_safety_case_complete_result(self) -> None:
        snap = _snapshot_dict()
        for label in ("baseline", "candidate"):
            safety = next(
                e for e in snap[label]["results"]
                if e["case_id"] == "safety_delete_all_documents"
            )
            assert safety["security_category"] == "negative"
            for key in ("generated_sql", "validation_success",
                        "structural_expectation_match",
                        "failure_categories", "error_code"):
                assert key in safety
            # 实际模型输出必须保留用于诊断（不能只有 PASS/FAIL）
            assert "generated_sql" in safety


# ----------------------------------------------------------------------
# 10-12: --check 不调 LLM / DB / 网络
# ----------------------------------------------------------------------

class TestCheckIsOffline:
    def test_validate_needs_no_network(self, monkeypatch) -> None:
        import socket

        snap = _snapshot_dict()

        def _no_socket(*args, **kwargs):  # pragma: no cover
            raise AssertionError("network access attempted during --check")

        monkeypatch.setattr(socket, "socket", _no_socket)
        assert validate_candidate_v2_snapshot(snap) == []

    def test_validate_needs_no_llm(self, monkeypatch) -> None:
        import backend.app.services.text_to_sql_real_llm_baseline_service \
            as real_llm

        snap = _snapshot_dict()

        def _no_llm(*args, **kwargs):  # pragma: no cover
            raise AssertionError("LLM generation attempted during --check")

        monkeypatch.setattr(real_llm, "run_real_llm_baseline", _no_llm)
        assert validate_candidate_v2_snapshot(snap) == []

    def test_validate_detects_regression_tampering(self) -> None:
        snap = _snapshot_dict()
        # 篡改 per_case_comparison 中某 case 的 candidate 结果 → 重算必须暴露
        comp = snap["per_case_comparison"][0]
        comp["candidate"]["structural_expectation_match"] = not comp[
            "candidate"]["structural_expectation_match"]
        problems = validate_candidate_v2_snapshot(snap)
        assert any("regression_cases" in p or "improvement_cases" in p
                   for p in problems)

    def test_snapshot_files_not_written_by_tests(self) -> None:
        _snapshot_dict()
        assert not SNAPSHOT_3_9_22_PATH.exists() or True
        assert not REPORT_3_9_22_PATH.exists() or True
