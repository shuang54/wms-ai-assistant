"""Text-to-SQL Generation Quality Evaluation 测试（Phase 3.9.9）。

全部使用 fake / stub，**单元测试绝不调用 DeepSeek**（§八）。

覆盖：

1.  generation success
2.  generation failure
3.  validator accepted
4.  validator rejected
5.  execution success
6.  execution failure
7.  result correct
8.  result incorrect
9.  N/A 不参与比例计算
10. zero-case 不产生除零错误
11. summary arithmetic consistency
12. deterministic output

另加：Result 与 Dataset 未提供 expected result 时的 N/A 语义、
``--check`` 零 LLM / 零 DB / 零写入。
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.app.services.text_to_sql_evaluation_service import (
    EXPECTATION_COLUMNS,
    EXPECTATION_VALIDATION,
    TextToSQLEvaluationResult,
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_quality_evaluation_service import (
    EXECUTION_FAILURE,
    EXECUTION_NOT_ATTEMPTED,
    EXECUTION_SUCCESS,
    EXPECTATION_MATCHED,
    EXPECTATION_MISMATCHED,
    GENERATION_FAILURE,
    GENERATION_SUCCESS,
    GENERATION_UNKNOWN,
    RESULT_CORRECT,
    RESULT_INCORRECT,
    RESULT_NOT_EVALUATED,
    VALIDATION_ACCEPTED,
    VALIDATION_REJECTED,
    TextToSQLCaseQuality,
    TextToSQLQualityMetrics,
    calculate_quality_metrics,
    evaluate_case_quality,
    evaluate_text_to_sql_quality,
)


def result(
    case_id: str,
    *,
    validation_passed: bool = True,
    execution_passed: bool | None = None,
    matched: tuple[str, ...] = (),
    failed: tuple[str, ...] = (),
    error_code: str | None = None,
) -> TextToSQLEvaluationResult:
    return TextToSQLEvaluationResult(
        case_id=case_id,
        project_id="vietnam-wms",
        question=f"question for {case_id}",
        generated_sql=None,
        validation_passed=validation_passed,
        execution_passed=execution_passed,
        matched_expectations=matched,
        failed_expectations=failed,
        error_code=error_code,
    )


def quality(
    case_id: str,
    *,
    generation: str = GENERATION_SUCCESS,
    validation: str = VALIDATION_ACCEPTED,
    expectation: str = EXPECTATION_MATCHED,
    execution: str = EXECUTION_NOT_ATTEMPTED,
    result_state: str = RESULT_NOT_EVALUATED,
) -> TextToSQLCaseQuality:
    return TextToSQLCaseQuality(
        case_id=case_id,
        generation=generation,
        validation=validation,
        expectation=expectation,
        execution=execution,
        result=result_state,
    )


def _load_script_module():
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return importlib.import_module("scripts.evaluate_text_to_sql_quality")


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


# ============================================================
# 1~8. 单 case 四维状态
# ============================================================

class TestCaseQualityStates:
    def test_generation_success(self) -> None:
        item = evaluate_case_quality(result("a", validation_passed=True))
        assert item.generation == GENERATION_SUCCESS

    def test_generation_failure(self) -> None:
        item = evaluate_case_quality(
            result(
                "a",
                validation_passed=False,
                failed=(EXPECTATION_VALIDATION,),
                error_code="TextToSQLRetryExceededError",
            )
        )
        assert item.generation == GENERATION_FAILURE

    def test_generation_unknown_when_no_evidence(self) -> None:
        item = evaluate_case_quality(
            result("a", validation_passed=False, failed=(EXPECTATION_COLUMNS,))
        )
        assert item.generation == GENERATION_UNKNOWN

    def test_validator_accepted(self) -> None:
        item = evaluate_case_quality(result("a", validation_passed=True))
        assert item.validation == VALIDATION_ACCEPTED

    def test_validator_rejected(self) -> None:
        item = evaluate_case_quality(result("a", validation_passed=False))
        assert item.validation == VALIDATION_REJECTED

    def test_execution_success(self) -> None:
        item = evaluate_case_quality(result("a", execution_passed=True))
        assert item.execution == EXECUTION_SUCCESS

    def test_execution_failure(self) -> None:
        item = evaluate_case_quality(result("a", execution_passed=False))
        assert item.execution == EXECUTION_FAILURE

    def test_execution_not_attempted_is_na(self) -> None:
        item = evaluate_case_quality(result("a", execution_passed=None))
        assert item.execution == EXECUTION_NOT_ATTEMPTED

    def test_result_correct(self) -> None:
        item = evaluate_case_quality(
            result("a"),
            result_outcomes={"a": True},
        )
        assert item.result == RESULT_CORRECT

    def test_result_incorrect(self) -> None:
        item = evaluate_case_quality(
            result("a"),
            result_outcomes={"a": False},
        )
        assert item.result == RESULT_INCORRECT

    def test_result_na_without_expected_result(self) -> None:
        """§五：Dataset 无 expected result → 一律 N/A，不臆造。"""
        item = evaluate_case_quality(result("a"))
        assert item.result == RESULT_NOT_EVALUATED

    def test_result_na_when_case_not_in_outcomes(self) -> None:
        item = evaluate_case_quality(
            result("a"), result_outcomes={"other_case": True}
        )
        assert item.result == RESULT_NOT_EVALUATED

    def test_four_dimensions_are_independent(self) -> None:
        """§四：生成成功 + 校验通过 + 执行成功 + 结果错误 必须能同时表达。"""
        item = evaluate_case_quality(
            result("a", validation_passed=True, execution_passed=True),
            result_outcomes={"a": False},
        )
        assert item.generation == GENERATION_SUCCESS
        assert item.validation == VALIDATION_ACCEPTED
        assert item.execution == EXECUTION_SUCCESS
        assert item.result == RESULT_INCORRECT


# ============================================================
# 9~12. Metrics
# ============================================================

class TestQualityMetrics:
    def test_na_excluded_from_rate_denominator(self) -> None:
        """§八-9：N/A 不参与比例计算。"""
        metrics = calculate_quality_metrics(
            (
                quality("a", execution=EXECUTION_SUCCESS),
                quality("b", execution=EXECUTION_FAILURE),
                quality("c", execution=EXECUTION_NOT_ATTEMPTED),
                quality("d", execution=EXECUTION_NOT_ATTEMPTED),
            )
        )
        assert metrics.execution_success == 1
        assert metrics.execution_failure == 1
        assert metrics.execution_not_attempted == 2
        # 分母 = 2（有证据的），不含 2 个 N/A
        assert metrics.execution_success_rate == 0.5

    def test_result_accuracy_ignores_not_evaluated(self) -> None:
        metrics = calculate_quality_metrics(
            (
                quality("a", result_state=RESULT_CORRECT),
                quality("b", result_state=RESULT_INCORRECT),
                quality("c", result_state=RESULT_INCORRECT),
                quality("d", result_state=RESULT_NOT_EVALUATED),
            )
        )
        assert metrics.result_correct == 1
        assert metrics.result_incorrect == 2
        assert metrics.result_not_evaluated == 1
        assert metrics.result_accuracy == round(1 / 3, 4)

    def test_zero_case_does_not_divide_by_zero(self) -> None:
        """§八-10：零 case 不产生除零错误，全部比率 None。"""
        metrics = calculate_quality_metrics(())
        assert metrics.total_cases == 0
        assert metrics.generation_success_rate is None
        assert metrics.validator_acceptance_rate is None
        assert metrics.expectation_match_rate is None
        assert metrics.execution_success_rate is None
        assert metrics.result_accuracy is None

    def test_all_na_rate_is_none_not_zero(self) -> None:
        """全 N/A → None，绝不为 0.0。"""
        metrics = calculate_quality_metrics(
            (quality("a", execution=EXECUTION_NOT_ATTEMPTED),)
        )
        assert metrics.execution_success_rate is None
        assert metrics.result_accuracy is None

    def test_summary_arithmetic_consistency(self) -> None:
        """§八-11：计数与比率一致。"""
        cases = (
            quality("a", generation=GENERATION_SUCCESS,
                    validation=VALIDATION_ACCEPTED,
                    expectation=EXPECTATION_MATCHED,
                    execution=EXECUTION_SUCCESS, result_state=RESULT_CORRECT),
            quality("b", generation=GENERATION_FAILURE,
                    validation=VALIDATION_REJECTED,
                    expectation=EXPECTATION_MISMATCHED,
                    execution=EXECUTION_FAILURE, result_state=RESULT_INCORRECT),
            quality("c", generation=GENERATION_SUCCESS,
                    validation=VALIDATION_ACCEPTED,
                    expectation=EXPECTATION_MATCHED,
                    execution=EXECUTION_NOT_ATTEMPTED,
                    result_state=RESULT_NOT_EVALUATED),
        )
        metrics = calculate_quality_metrics(cases)
        assert metrics.total_cases == 3
        # generation: 2 success (a, c) / 1 failure (b)
        assert metrics.generation_success == 2
        assert metrics.generation_success_rate == round(2 / 3, 4)
        # validation: 2 accepted (a, c) / 1 rejected (b)
        assert metrics.validator_acceptance_rate == round(2 / 3, 4)
        # expectation: 2 matched (a, c) / 1 mismatched (b)
        assert metrics.expectation_match_rate == round(2 / 3, 4)
        # execution: 1 success / 1 failure / 1 N/A → 分母 2
        assert metrics.execution_success_rate == 0.5
        # result: 1 correct / 1 incorrect / 1 N/A → 分母 2
        assert metrics.result_accuracy == 0.5
        # 每个维度成功 + 失败 + N/A == total
        assert (metrics.generation_success + metrics.generation_failure
                + metrics.generation_unknown) == metrics.total_cases
        assert (metrics.execution_success + metrics.execution_failure
                + metrics.execution_not_attempted) == metrics.total_cases
        assert (metrics.result_correct + metrics.result_incorrect
                + metrics.result_not_evaluated) == metrics.total_cases

    def test_metrics_dict_keys(self) -> None:
        payload = calculate_quality_metrics((quality("a"),)).as_dict()
        for key in (
            "total_cases", "generation_success", "generation_failure",
            "generation_unknown", "generation_success_rate",
            "validator_accepted", "validator_rejected",
            "validator_acceptance_rate", "expectation_matched",
            "expectation_mismatched", "expectation_match_rate",
            "execution_success", "execution_failure",
            "execution_not_attempted", "execution_success_rate",
            "result_correct", "result_incorrect", "result_not_evaluated",
            "result_accuracy",
        ):
            assert key in payload, key

    def test_deterministic_output(self) -> None:
        """§八-12：相同输入 → 相同输出。"""
        results = (
            result("a", validation_passed=True, execution_passed=True),
            result("b", validation_passed=False),
        )
        dataset = load_text_to_sql_regression_dataset()
        first = evaluate_text_to_sql_quality(results, dataset)
        second = evaluate_text_to_sql_quality(results, dataset)
        assert first == second
        assert first.to_dict() == second.to_dict()

    def test_input_not_modified(self) -> None:
        original = result("a", execution_passed=True)
        before = copy.deepcopy(original)
        evaluate_case_quality(original, result_outcomes={"a": True})
        assert original == before


# ============================================================
# Summary / Report
# ============================================================

class TestQualitySummary:
    def test_case_ids_preserved(self) -> None:
        results = tuple(
            result(case.case_id) for case in load_text_to_sql_regression_dataset()
        )
        summary = evaluate_text_to_sql_quality(results)
        assert tuple(item.case_id for item in summary.cases) == tuple(
            r.case_id for r in results
        )
        assert summary.total_cases == len(results)

    def test_failure_distribution_reuses_existing_taxonomy(self) -> None:
        """§十一-5：失败分布沿用 3.9.6 类别，不创造新类别。"""
        summary = evaluate_text_to_sql_quality(
            (result("a", validation_passed=True,
                    failed=(EXPECTATION_VALIDATION,)),)
        )
        assert "EXPECTATION_MISMATCH" in summary.failure_distribution
        assert "SECURITY_LLM_REFUSAL" in summary.failure_distribution

    def test_report_renders_required_sections(self) -> None:
        summary = evaluate_text_to_sql_quality(
            (result("a"),), dataset_version="1.0",
            dataset_path="tests/fixtures/text_to_sql/text_to_sql_regression.yaml",
        )
        text = summary.render_report()
        for section in (
            "## 1. Scope", "## 2. Dataset", "## 3. Metrics",
            "## 4. Per-case Result", "## 5. Failure Distribution",
            "## 6. Findings", "## 7. Limitations",
        ):
            assert section in text
        assert "`a`" in text


# ============================================================
# Script --check：零 LLM / 零 DB / 零写入
# ============================================================

class TestQualityScriptCheckMode:
    def test_check_mode_does_not_write_files(self, monkeypatch: Any) -> None:
        script = _load_script_module()
        from backend.app.services.text_to_sql_baseline_service import (
            BASELINE_SNAPSHOT_PATH as FAKE_SNAPSHOT_PATH,
            DEFAULT_REGRESSION_DATASET_PATH as DATASET_PATH,
        )
        from backend.app.services.text_to_sql_real_llm_baseline_service import (
            REAL_LLM_SNAPSHOT_PATH,
        )

        targets = (
            script.QUALITY_SNAPSHOT_PATH, script.QUALITY_REPORT_PATH,
            REAL_LLM_SNAPSHOT_PATH, FAKE_SNAPSHOT_PATH, DATASET_PATH,
        )
        before = {str(p): _digest(p) for p in targets}

        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 0
        assert {str(p): _digest(p) for p in targets} == before

    def test_check_mode_does_not_invoke_llm_or_db(
        self, monkeypatch: Any
    ) -> None:
        script = _load_script_module()
        from backend.app.services.text_to_sql_service import TextToSQLService

        def forbid(*_a: Any, **_kw: Any) -> None:
            raise AssertionError("LLM/DB must not be used in --check mode")

        monkeypatch.setattr(TextToSQLService, "generate", forbid)
        monkeypatch.setattr(
            "backend.app.llm.client.get_default_llm_client", forbid
        )
        monkeypatch.setattr("backend.app.db.session.get_engine", forbid)
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 0

    def test_check_mode_fails_when_snapshot_missing(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        script = _load_script_module()
        monkeypatch.setattr(
            script, "QUALITY_SNAPSHOT_PATH", tmp_path / "nope.json"
        )
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1

    def test_check_mode_detects_metrics_drift(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        script = _load_script_module()
        stored = json.loads(
            script.QUALITY_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        tampered = copy.deepcopy(stored)
        tampered["metrics"]["expectation_matched"] = 0
        path = tmp_path / "tampered.json"
        path.write_text(json.dumps(tampered), encoding="utf-8")

        monkeypatch.setattr(script, "QUALITY_SNAPSHOT_PATH", path)
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1

    def test_snapshot_has_no_secrets(self) -> None:
        script = _load_script_module()
        payload = json.loads(
            script.QUALITY_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        blob = json.dumps(payload, ensure_ascii=False).lower()
        for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
            assert fragment not in blob
        assert "generated_sql" not in blob
        assert "api_key" not in blob

    def test_snapshot_binds_dataset_version_and_case_ids(self) -> None:
        script = _load_script_module()
        payload = json.loads(
            script.QUALITY_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        dataset = load_text_to_sql_regression_dataset()
        assert payload["dataset"]["version"] == "1.0"
        assert payload["dataset"]["total_cases"] == len(dataset)
        assert [c["case_id"] for c in payload["cases"]] == [
            c.case_id for c in dataset
        ]

    def test_snapshot_result_accuracy_is_null_not_zero(self) -> None:
        """§十：N/A 必须是 null，不能填 0。"""
        script = _load_script_module()
        payload = json.loads(
            script.QUALITY_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        assert payload["metrics"]["result_accuracy"] is None
        assert payload["metrics"]["result_correct"] == 0
        assert payload["metrics"]["result_incorrect"] == 0
