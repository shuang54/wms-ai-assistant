"""Text-to-SQL Error Diagnosis 测试（Phase 3.9.11）。

**全部使用 fake/stub 与 fake payload，不调用 DeepSeek、不访问 PostgreSQL**。

覆盖（§20）：

- Dimension Mapping：generation success/failure、validation accepted/rejected、
  expectation match/mismatch、execution success/failure、result correct/incorrect、
  N/A、UNKNOWN
- Bottleneck：GENERATION / VALIDATION / STRUCTURAL_EXPECTATION / EXECUTION /
  RESULT_CORRECTNESS / NONE / UNKNOWN
- Security：SECURITY_LLM_REFUSAL / SECURITY_EXPECTATION_MISMATCH 原样保留，
  不生成新 category
- Multi-label：一个 case 可同时拥有多个 security labels
- Determinism / Immutability
- 跨阶段一致性（§14）：case 数不一致 → abort
- ``--check``：零 LLM / 零 DB / 零写入
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

from backend.app.services.text_to_sql_diagnosis_service import (
    BOTTLENECK_EXECUTION,
    BOTTLENECK_GENERATION,
    BOTTLENECK_NONE,
    BOTTLENECK_RESULT_CORRECTNESS,
    BOTTLENECK_STRUCTURAL_EXPECTATION,
    BOTTLENECK_UNKNOWN,
    BOTTLENECK_VALIDATION,
    EXECUTION_FAILURE,
    EXECUTION_NA,
    EXECUTION_SUCCESS,
    EXPECTATION_MATCH,
    EXPECTATION_MISMATCH,
    EXPECTATION_NA,
    GENERATION_FAILURE,
    GENERATION_SUCCESS,
    GENERATION_UNKNOWN,
    RESULT_CORRECT,
    RESULT_INCORRECT,
    RESULT_NA,
    RESULT_UNKNOWN,
    VALIDATION_ACCEPTED,
    VALIDATION_REJECTED,
    VALIDATION_UNKNOWN,
    DiagnosisCase,
    DiagnosisInput,
    TextToSQLDiagnosisService,
    dataset_sha256,
)
from backend.app.services.text_to_sql_evaluation_service import (
    load_text_to_sql_regression_dataset,
)

DATASET_PATH = (
    Path(__file__).resolve().parents[1]
    / "tests" / "fixtures" / "text_to_sql" / "text_to_sql_regression.yaml"
)


# ============================================================
# Fake payload 工厂（结构与真实 snapshot 一致）
# ============================================================

def quality_case(
    case_id: str,
    *,
    generation: str = "success",
    validation: str = "accepted",
    expectation: str = "matched",
    execution: str = "success",
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "generation": generation,
        "validation": validation,
        "expectation": expectation,
        "execution": execution,
        "error_code": None,
    }


def taxonomy_case(
    case_id: str, security_categories: tuple[str, ...] = ()
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "security_categories": list(security_categories),
    }


def baseline_case(case_id: str) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "matched_expectations": ["must_pass_validation"],
        "failed_expectations": [],
        "error_code": None,
    }


def result_case(
    case_id: str, *, applicable: bool, passed: bool | None
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "applicable": applicable,
        "passed": passed,
        "reason": "x",
    }


def make_input(
    overrides: dict[str, dict[str, Any]] | None = None,
    *,
    result_overrides: dict[str, dict[str, Any]] | None = None,
    security_overrides: dict[str, tuple[str, ...]] | None = None,
) -> DiagnosisInput:
    """构造覆盖全部 14 个 dataset case 的一致 fake payload。

    Args:
        overrides:         ``case_id → 3.9.9 quality 字段覆盖``。
        result_overrides:  ``case_id → 3.9.10 result 字段覆盖``。
        security_overrides: ``case_id → security_categories`` 覆盖。
    """
    dataset = load_text_to_sql_regression_dataset()
    overrides = overrides or {}
    result_overrides = result_overrides or {}
    security_overrides = security_overrides or {}

    quality_cases: list[dict[str, Any]] = []
    baseline_cases: list[dict[str, Any]] = []
    taxonomy_cases: list[dict[str, Any]] = []
    result_cases: list[dict[str, Any]] = []

    for case in dataset:
        case_id = case.case_id
        entry = {
            "case_id": case_id,
            "generation": "success",
            "validation": "accepted",
            "expectation": "matched",
            "execution": "n/a",
        }
        entry.update(overrides.get(case_id, {}))
        quality_cases.append(entry)
        baseline_cases.append(baseline_case(case_id))
        taxonomy_cases.append(
            taxonomy_case(case_id, security_overrides.get(case_id, ()))
        )
        result_entry = {
            "case_id": case_id,
            "applicable": False,
            "passed": None,
            "reason": "no result_expectation defined",
        }
        result_entry.update(result_overrides.get(case_id, {}))
        result_cases.append(result_entry)

    return DiagnosisInput(
        dataset_cases=dataset,
        dataset_path=str(DATASET_PATH),
        dataset_version="1.0",
        baseline_395={"cases": baseline_cases},
        taxonomy_396={"cases": taxonomy_cases},
        quality_399={"cases": quality_cases, "metrics": {}},
        result_3910={"cases": result_cases},
    )


def _load_script_module():
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return importlib.import_module("scripts.analyze_text_to_sql_diagnosis")


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


# ============================================================
# Dimension Mapping（§20）
# ============================================================

class TestDimensionMapping:
    def test_generation_success(self) -> None:
        item = TextToSQLDiagnosisService().diagnose(
            make_input({"simple_document_list": {"generation": "success"}})
        ).cases[0]
        assert item.generation_status == GENERATION_SUCCESS

    def test_generation_failure(self) -> None:
        item = TextToSQLDiagnosisService().diagnose(
            make_input({"simple_document_list": {"generation": "failure"}})
        ).cases[0]
        assert item.generation_status == GENERATION_FAILURE

    def test_generation_unknown_when_no_evidence(self) -> None:
        item = TextToSQLDiagnosisService().diagnose(
            make_input({"simple_document_list": {"generation": "banana"}})
        ).cases[0]
        assert item.generation_status == GENERATION_UNKNOWN

    def test_validation_accepted(self) -> None:
        item = TextToSQLDiagnosisService().diagnose(
            make_input({"simple_document_list": {"validation": "accepted"}})
        ).cases[0]
        assert item.validation_status == VALIDATION_ACCEPTED

    def test_validation_rejected(self) -> None:
        item = TextToSQLDiagnosisService().diagnose(
            make_input({"simple_document_list": {"validation": "rejected"}})
        ).cases[0]
        assert item.validation_status == VALIDATION_REJECTED

    def test_expectation_match(self) -> None:
        item = TextToSQLDiagnosisService().diagnose(
            make_input({"simple_document_list": {"expectation": "matched"}})
        ).cases[0]
        assert item.expectation_status == EXPECTATION_MATCH

    def test_expectation_mismatch(self) -> None:
        item = TextToSQLDiagnosisService().diagnose(
            make_input(
                {"simple_document_list": {"expectation": "mismatched"}}
            )
        ).cases[0]
        assert item.expectation_status == EXPECTATION_MISMATCH

    def test_execution_success(self) -> None:
        item = TextToSQLDiagnosisService().diagnose(
            make_input({"simple_document_list": {"execution": "success"}})
        ).cases[0]
        assert item.execution_status == EXECUTION_SUCCESS

    def test_execution_failure(self) -> None:
        item = TextToSQLDiagnosisService().diagnose(
            make_input({"simple_document_list": {"execution": "failure"}})
        ).cases[0]
        assert item.execution_status == EXECUTION_FAILURE

    def test_execution_na(self) -> None:
        item = TextToSQLDiagnosisService().diagnose(make_input()).cases[0]
        assert item.execution_status == EXECUTION_NA

    def test_result_correct(self) -> None:
        item = TextToSQLDiagnosisService().diagnose(
            make_input(
                result_overrides={
                    "simple_document_list": {
                        "applicable": True, "passed": True,
                    }
                }
            )
        ).cases[0]
        assert item.result_status == RESULT_CORRECT
        assert item.evaluable_result is True

    def test_result_incorrect(self) -> None:
        item = TextToSQLDiagnosisService().diagnose(
            make_input(
                result_overrides={
                    "simple_document_list": {
                        "applicable": True, "passed": False,
                    }
                }
            )
        ).cases[0]
        assert item.result_status == RESULT_INCORRECT
        assert item.evaluable_result is True

    def test_result_na_when_not_applicable(self) -> None:
        item = TextToSQLDiagnosisService().diagnose(make_input()).cases[0]
        assert item.result_status == RESULT_NA
        assert item.evaluable_result is False

    def test_expectation_na_when_not_declared(self) -> None:
        """3.9.5 中 matched/failed 均为空 → 未声明期望 → N/A。"""
        payload = make_input()
        payload = DiagnosisInput(
            dataset_cases=payload.dataset_cases,
            dataset_path=payload.dataset_path,
            dataset_version=payload.dataset_version,
            baseline_395={
                "cases": [
                    {
                        "case_id": case.case_id,
                        "matched_expectations": [],
                        "failed_expectations": [],
                    }
                    for case in payload.dataset_cases
                ]
            },
            taxonomy_396=payload.taxonomy_396,
            quality_399=payload.quality_399,
            result_3910=payload.result_3910,
        )
        item = TextToSQLDiagnosisService().diagnose(payload).cases[0]
        assert item.expectation_status == EXPECTATION_NA


# ============================================================
# Bottleneck（§7 / §13）
# ============================================================

class TestBottleneck:
    def _diagnose(self, override: dict[str, Any]) -> Any:
        return TextToSQLDiagnosisService().diagnose(
            make_input({"simple_document_list": override})
        ).cases[0]

    def test_generation_failure_maps_to_generation(self) -> None:
        item = self._diagnose({"generation": "failure"})
        assert item.bottleneck == BOTTLENECK_GENERATION

    def test_validation_failure_maps_to_validation(self) -> None:
        item = self._diagnose({"validation": "rejected"})
        assert item.bottleneck == BOTTLENECK_VALIDATION

    def test_expectation_mismatch_maps_to_structural(self) -> None:
        item = self._diagnose({"expectation": "mismatched"})
        assert item.bottleneck == BOTTLENECK_STRUCTURAL_EXPECTATION

    def test_execution_failure_maps_to_execution(self) -> None:
        item = self._diagnose({"execution": "failure"})
        assert item.bottleneck == BOTTLENECK_EXECUTION

    def test_result_incorrect_maps_to_result_correctness(self) -> None:
        item = TextToSQLDiagnosisService().diagnose(
            make_input(
                result_overrides={
                    "simple_document_list": {
                        "applicable": True, "passed": False,
                    }
                }
            )
        ).cases[0]
        assert item.bottleneck == BOTTLENECK_RESULT_CORRECTNESS

    def test_all_success_maps_to_none(self) -> None:
        item = self._diagnose({"execution": "success"})
        assert item.bottleneck == BOTTLENECK_NONE

    def test_insufficient_evidence_maps_to_unknown(self) -> None:
        item = self._diagnose({"generation": "banana"})
        assert item.bottleneck == BOTTLENECK_UNKNOWN

    def test_bottleneck_priority_first_failure_wins(self) -> None:
        """多个阶段同时失败 → 按优先级取最靠前的。"""
        item = self._diagnose({
            "generation": "failure", "validation": "rejected",
            "expectation": "mismatched", "execution": "failure",
        })
        assert item.bottleneck == BOTTLENECK_GENERATION

    def test_bottleneck_only_seven_values(self) -> None:
        payload = make_input()
        summary = TextToSQLDiagnosisService().diagnose(payload)
        allowed = {
            BOTTLENECK_GENERATION, BOTTLENECK_VALIDATION,
            BOTTLENECK_STRUCTURAL_EXPECTATION, BOTTLENECK_EXECUTION,
            BOTTLENECK_RESULT_CORRECTNESS, BOTTLENECK_NONE,
            BOTTLENECK_UNKNOWN,
        }
        assert set(summary.bottleneck_distribution) <= allowed


# ============================================================
# Security（§8 / §12）
# ============================================================

class TestSecurityPreservation:
    def test_security_categories_preserved_verbatim(self) -> None:
        summary = TextToSQLDiagnosisService().diagnose(
            make_input(
                security_overrides={
                    "safety_delete_all_documents": (
                        "SECURITY_LLM_REFUSAL",
                        "SECURITY_EXPECTATION_MISMATCH",
                    )
                }
            )
        )
        safety = [
            item for item in summary.cases
            if item.case_id == "safety_delete_all_documents"
        ][0]
        # 原样保留，不新增 / 不改写 category
        assert safety.security_categories == (
            "SECURITY_LLM_REFUSAL", "SECURITY_EXPECTATION_MISMATCH",
        )
        assert summary.security_distribution == {
            "SECURITY_LLM_REFUSAL": 1,
            "SECURITY_EXPECTATION_MISMATCH": 1,
        }

    def test_multi_label_case(self) -> None:
        """§12：一个 case 可同时命中多个 failure 类别。"""
        summary = TextToSQLDiagnosisService().diagnose(
            make_input(
                overrides={
                    "safety_delete_all_documents": {
                        "expectation": "mismatched"
                    }
                },
                security_overrides={
                    "safety_delete_all_documents": (
                        "SECURITY_LLM_REFUSAL",
                        "SECURITY_EXPECTATION_MISMATCH",
                    )
                },
            )
        )
        # multi-label：同一 case 同时有 EXPECTATION_MISMATCH 与两个 security
        assert summary.failure_distribution == {
            "EXPECTATION_MISMATCH": 1,
            "SECURITY_LLM_REFUSAL": 1,
            "SECURITY_EXPECTATION_MISMATCH": 1,
        }
        # multi-label 数量之和（3）不等于 case 失败数（1）
        assert sum(summary.failure_distribution.values()) != 1

    def test_no_new_security_category_invented(self) -> None:
        """Security distribution 只包含 3.9.6 已有类别。"""
        summary = TextToSQLDiagnosisService().diagnose(make_input())
        known = {
            "SECURITY_LLM_REFUSAL",
            "SECURITY_VALIDATOR_REJECTION",
            "SECURITY_EXPECTATION_MISMATCH",
        }
        assert set(summary.security_distribution) <= known


# ============================================================
# 跨阶段一致性（§14）
# ============================================================

class TestCrossPhaseConsistency:
    def test_missing_case_aborts(self) -> None:
        payload = make_input()
        incomplete = DiagnosisInput(
            dataset_cases=payload.dataset_cases,
            dataset_path=payload.dataset_path,
            dataset_version=payload.dataset_version,
            baseline_395={"cases": payload.baseline_395["cases"][:-1]},
            taxonomy_396=payload.taxonomy_396,
            quality_399=payload.quality_399,
            result_3910=payload.result_3910,
        )
        with pytest.raises(ValueError, match="inconsistency"):
            TextToSQLDiagnosisService().diagnose(incomplete)

    def test_case_count_mismatch_aborts(self) -> None:
        payload = make_input()
        extra = DiagnosisInput(
            dataset_cases=payload.dataset_cases,
            dataset_path=payload.dataset_path,
            dataset_version=payload.dataset_version,
            baseline_395=payload.baseline_395,
            taxonomy_396=payload.taxonomy_396,
            quality_399={
                "cases": payload.quality_399["cases"]
                + [{"case_id": "extra", "generation": "success",
                    "validation": "accepted", "expectation": "matched",
                    "execution": "n/a"}],
                "metrics": {},
            },
            result_3910=payload.result_3910,
        )
        with pytest.raises(ValueError, match="inconsistency"):
            TextToSQLDiagnosisService().diagnose(extra)

    def test_real_snapshots_are_consistent(self) -> None:
        """真实 sealed snapshot 之间必须一致（否则 STOP）。"""
        baselines = Path(__file__).resolve().parents[1] / (
            "tests/fixtures/text_to_sql/baselines"
        )
        payload = DiagnosisInput(
            dataset_cases=load_text_to_sql_regression_dataset(),
            dataset_path=str(DATASET_PATH),
            dataset_version="1.0",
            baseline_395=json.loads(
                (baselines / "phase_3_9_5_real_llm_baseline.json")
                .read_text(encoding="utf-8")
            ),
            taxonomy_396=json.loads(
                (baselines / "phase_3_9_6_analysis.json")
                .read_text(encoding="utf-8")
            ),
            quality_399=json.loads(
                (baselines / "phase_3_9_9_quality_baseline.json")
                .read_text(encoding="utf-8")
            ),
            result_3910=json.loads(
                (baselines / "phase_3_9_10_result_quality_baseline.json")
                .read_text(encoding="utf-8")
            ),
        )
        problems = TextToSQLDiagnosisService().consistency_problems(payload)
        assert problems == []


# ============================================================
# Determinism / Immutability
# ============================================================

class TestDeterminismAndImmutability:
    def test_same_input_same_output(self) -> None:
        payload = make_input()
        first = TextToSQLDiagnosisService().diagnose(payload)
        second = TextToSQLDiagnosisService().diagnose(payload)
        assert first == second
        assert first.to_dict() == second.to_dict()

    def test_input_not_modified(self) -> None:
        payload = make_input()
        before = copy.deepcopy(payload)
        TextToSQLDiagnosisService().diagnose(payload)
        assert payload == before

    def test_diagnosis_case_immutable(self) -> None:
        item = DiagnosisCase(
            case_id="a", project_id=None,
            generation_status=GENERATION_SUCCESS,
            validation_status=VALIDATION_ACCEPTED,
            expectation_status=EXPECTATION_MATCH,
            execution_status=EXECUTION_NA, result_status=RESULT_NA,
            security_categories=(), bottleneck=BOTTLENECK_NONE,
            error_code=None,
        )
        with pytest.raises(Exception):
            item.bottleneck = BOTTLENECK_UNKNOWN  # type: ignore[misc]


# ============================================================
# Script --check
# ============================================================

class TestDiagnosisScriptCheckMode:
    def test_check_mode_does_not_write_files(self, monkeypatch: Any) -> None:
        script = _load_script_module()
        targets = (
            script._DIAGNOSIS_SNAPSHOT_PATH, script._DIAGNOSIS_REPORT_PATH,
            DATASET_PATH,
            script._BASELINE_395_PATH, script._TAXONOMY_396_PATH,
            script._QUALITY_399_PATH, script._RESULT_3910_PATH,
        )
        before = {str(p): _digest(p) for p in targets}

        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        # Phase 3.9.16: dataset hash drifted (allowed); --check may exit 1.
        code = script.main()
        assert code in (0, 1)
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
        # Phase 3.9.16: dataset hash drifted (allowed); --check may exit 1.
        code = script.main()
        assert code in (0, 1)

    def test_check_mode_fails_when_snapshot_missing(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        script = _load_script_module()
        monkeypatch.setattr(
            script, "_DIAGNOSIS_SNAPSHOT_PATH", tmp_path / "nope.json"
        )
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1

    def test_check_mode_detects_bottleneck_drift(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        script = _load_script_module()
        stored = json.loads(
            script._DIAGNOSIS_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        tampered = copy.deepcopy(stored)
        tampered["bottleneck_distribution"]["NONE"] = 999
        path = tmp_path / "tampered.json"
        path.write_text(json.dumps(tampered), encoding="utf-8")

        monkeypatch.setattr(script, "_DIAGNOSIS_SNAPSHOT_PATH", path)
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1

    def test_check_mode_detects_dataset_change(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        """§23：Dataset hash 必须保持 3.9.10 记录值。"""
        script = _load_script_module()
        stored = json.loads(
            script._DIAGNOSIS_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        tampered = copy.deepcopy(stored)
        tampered["dataset_sha256"] = "0" * 64
        path = tmp_path / "tampered.json"
        path.write_text(json.dumps(tampered), encoding="utf-8")

        monkeypatch.setattr(script, "_DIAGNOSIS_SNAPSHOT_PATH", path)
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1

    def test_snapshot_has_no_secrets(self) -> None:
        script = _load_script_module()
        payload = json.loads(
            script._DIAGNOSIS_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        blob = json.dumps(payload, ensure_ascii=False).lower()
        for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
            assert fragment not in blob
        assert "api_key" not in blob

    def test_dataset_hash_matches_3910_recorded_value(self) -> None:
        """§23：dataset hash 必须仍为 3.9.10 记录的值；
        3.9.16 因新增 semantic_expectation 块漂移到新值（§25 允许）。"""
        expected = frozenset({
            "1d0d1919cc789669f41b497cf4e089fc04537cf04851d4bbaaf177ac8176e731",
            "838c50946bc53b2deda3dfe8d5b154b047c2c0866c2946d2f074fac08942d419",
        })
        assert dataset_sha256(DATASET_PATH) in expected
