"""Text-to-SQL Result-level Correctness Evaluation 测试（Phase 3.9.10）。

**全部使用 fake/stub，单元测试不访问 DeepSeek / PostgreSQL**（§27）。

覆盖（§26）：

- DTO：exact_rows / unordered_rows / scalar / column_values / invalid type / immutable
- Checker：exact match、row count mismatch、value mismatch、ordered mismatch、
  unordered success、unordered 重复行处理、scalar success、scalar shape mismatch、
  column_values success、column_values mismatch
- N/A：无 result_expectation → applicable=false / passed=None
- Metrics：3 correct / 1 incorrect → 75%；0 applicable → None
- Determinism / Immutability
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

from backend.app.services.text_to_sql_result_evaluation_service import (
    RESULT_TYPE_COLUMN_VALUES,
    RESULT_TYPE_EXACT_ROWS,
    RESULT_TYPE_SCALAR,
    RESULT_TYPE_UNORDERED_ROWS,
    ResultAccuracySummary,
    ResultCheckInput,
    ResultExpectation,
    ResultExpectationError,
    TextToSQLResultEvaluationService,
    load_result_expectations,
    parse_result_expectation,
)


def service() -> TextToSQLResultEvaluationService:
    return TextToSQLResultEvaluationService()


def _load_script_module():
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return importlib.import_module(
        "scripts.evaluate_text_to_sql_result_quality"
    )


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


# ============================================================
# DTO
# ============================================================

class TestResultExpectationDto:
    def test_valid_exact_rows(self) -> None:
        expectation = ResultExpectation(
            type=RESULT_TYPE_EXACT_ROWS, rows=((1, "a"), (2, "b"))
        )
        assert expectation.type == RESULT_TYPE_EXACT_ROWS
        assert expectation.rows == ((1, "a"), (2, "b"))

    def test_valid_unordered_rows(self) -> None:
        expectation = ResultExpectation(
            type=RESULT_TYPE_UNORDERED_ROWS, rows=((1, "a"),)
        )
        assert len(expectation.rows) == 1

    def test_valid_scalar(self) -> None:
        expectation = ResultExpectation(type=RESULT_TYPE_SCALAR, value=0)
        assert expectation.value == 0

    def test_valid_column_values(self) -> None:
        expectation = ResultExpectation(
            type=RESULT_TYPE_COLUMN_VALUES, column="code",
            values=("A", "B"), ordered=False,
        )
        assert expectation.column == "code"
        assert expectation.ordered is False

    def test_invalid_type(self) -> None:
        with pytest.raises(ResultExpectationError):
            ResultExpectation(type="fuzzy_match", rows=())

    def test_scalar_requires_value(self) -> None:
        with pytest.raises(ResultExpectationError):
            ResultExpectation(type=RESULT_TYPE_SCALAR)

    def test_column_values_requires_column(self) -> None:
        with pytest.raises(ResultExpectationError):
            ResultExpectation(type=RESULT_TYPE_COLUMN_VALUES, values=("A",))

    def test_immutable_input(self) -> None:
        expectation = ResultExpectation(
            type=RESULT_TYPE_EXACT_ROWS, rows=((1, "a"),)
        )
        before = copy.deepcopy(expectation)
        service().check(
            ResultCheckInput(
                case_id="c", columns=("id", "name"), rows=((1, "a"),),
                expectation=expectation,
            )
        )
        assert expectation == before


# ============================================================
# Parsing
# ============================================================

class TestParsing:
    def test_parse_exact_rows(self) -> None:
        expectation = parse_result_expectation(
            {"type": "exact_rows", "rows": [[1, "a"]]}
        )
        assert expectation.rows == ((1, "a"),)

    def test_parse_scalar_zero(self) -> None:
        expectation = parse_result_expectation({"type": "scalar", "value": 0})
        assert expectation.value == 0

    def test_parse_unknown_type_raises(self) -> None:
        with pytest.raises(ResultExpectationError):
            parse_result_expectation({"type": "semantic_similarity"})

    def test_parse_missing_type_raises(self) -> None:
        with pytest.raises(ResultExpectationError):
            parse_result_expectation({"rows": []})

    def test_parse_unknown_key_raises(self) -> None:
        with pytest.raises(ResultExpectationError):
            parse_result_expectation({"type": "scalar", "value": 1, "junk": 2})

    def test_load_expectations_from_dataset(self) -> None:
        expectations = load_result_expectations()
        assert set(expectations) == {
            "simple_document_list",
            "aggregate_document_count",
            "limit_first_10_documents",
        }
        # 未声明 result_expectation 的 case 不应出现
        assert "project_a_inventory" not in expectations


# ============================================================
# Checker
# ============================================================

class TestCheckers:
    def test_exact_match(self) -> None:
        result = service().check(
            ResultCheckInput(
                case_id="c", columns=("id", "name"), rows=((1, "a"), (2, "b")),
                expectation=ResultExpectation(
                    type=RESULT_TYPE_EXACT_ROWS, rows=((1, "a"), (2, "b"))
                ),
            )
        )
        assert result.passed is True
        assert "matched" in result.reason

    def test_row_count_mismatch(self) -> None:
        result = service().check(
            ResultCheckInput(
                case_id="c", columns=("id",), rows=((1,),),
                expectation=ResultExpectation(
                    type=RESULT_TYPE_EXACT_ROWS, rows=((1,), (2,))
                ),
            )
        )
        assert result.passed is False
        assert "row count mismatch" in result.reason

    def test_value_mismatch(self) -> None:
        result = service().check(
            ResultCheckInput(
                case_id="c", columns=("id",), rows=((1,),),
                expectation=ResultExpectation(
                    type=RESULT_TYPE_EXACT_ROWS, rows=((9,),)
                ),
            )
        )
        assert result.passed is False
        assert "mismatch" in result.reason

    def test_ordered_mismatch(self) -> None:
        """exact_rows 严格要求顺序：顺序不同即失败。"""
        result = service().check(
            ResultCheckInput(
                case_id="c", columns=("id",), rows=((2,), (1,)),
                expectation=ResultExpectation(
                    type=RESULT_TYPE_EXACT_ROWS, rows=((1,), (2,))
                ),
            )
        )
        assert result.passed is False

    def test_unordered_success(self) -> None:
        result = service().check(
            ResultCheckInput(
                case_id="c", columns=("id",), rows=((2,), (1,)),
                expectation=ResultExpectation(
                    type=RESULT_TYPE_UNORDERED_ROWS, rows=((1,), (2,))
                ),
            )
        )
        assert result.passed is True

    def test_unordered_duplicate_handling(self) -> None:
        """multiset 语义：重复行不可被 set 折叠掉。"""
        result = service().check(
            ResultCheckInput(
                case_id="c", columns=("id",), rows=((1,), (1,)),
                expectation=ResultExpectation(
                    type=RESULT_TYPE_UNORDERED_ROWS, rows=((1,), (1,))
                ),
            )
        )
        assert result.passed is True
        # 少一行重复 → 必须失败
        fewer = service().check(
            ResultCheckInput(
                case_id="c", columns=("id",), rows=((1,),),
                expectation=ResultExpectation(
                    type=RESULT_TYPE_UNORDERED_ROWS, rows=((1,), (1,))
                ),
            )
        )
        assert fewer.passed is False

    def test_scalar_success(self) -> None:
        result = service().check(
            ResultCheckInput(
                case_id="c", columns=("total",), rows=((0,),),
                expectation=ResultExpectation(
                    type=RESULT_TYPE_SCALAR, value=0
                ),
            )
        )
        assert result.passed is True

    def test_scalar_shape_mismatch(self) -> None:
        result = service().check(
            ResultCheckInput(
                case_id="c", columns=("a", "b"), rows=((1, 2),),
                expectation=ResultExpectation(
                    type=RESULT_TYPE_SCALAR, value=1
                ),
            )
        )
        assert result.passed is False
        assert "scalar shape mismatch" in result.reason

    def test_column_values_success(self) -> None:
        result = service().check(
            ResultCheckInput(
                case_id="c", columns=("id", "code"),
                rows=((1, "A"), (2, "B")),
                expectation=ResultExpectation(
                    type=RESULT_TYPE_COLUMN_VALUES, column="code",
                    values=("A", "B"),
                ),
            )
        )
        assert result.passed is True

    def test_column_values_mismatch(self) -> None:
        result = service().check(
            ResultCheckInput(
                case_id="c", columns=("code",), rows=(("A",),),
                expectation=ResultExpectation(
                    type=RESULT_TYPE_COLUMN_VALUES, column="code",
                    values=("Z",),
                ),
            )
        )
        assert result.passed is False

    def test_column_values_missing_column(self) -> None:
        result = service().check(
            ResultCheckInput(
                case_id="c", columns=("id",), rows=((1,),),
                expectation=ResultExpectation(
                    type=RESULT_TYPE_COLUMN_VALUES, column="code",
                    values=("A",),
                ),
            )
        )
        assert result.passed is False
        assert "not found" in result.reason

    def test_column_values_unordered(self) -> None:
        result = service().check(
            ResultCheckInput(
                case_id="c", columns=("code",), rows=(("B",), ("A",)),
                expectation=ResultExpectation(
                    type=RESULT_TYPE_COLUMN_VALUES, column="code",
                    values=("A", "B"), ordered=False,
                ),
            )
        )
        assert result.passed is True


# ============================================================
# N/A
# ============================================================

class TestNotApplicable:
    def test_no_expectation_is_na_not_failure(self) -> None:
        result = service().check(
            ResultCheckInput(case_id="c", columns=("id",), rows=((1,),))
        )
        assert result.applicable is False
        assert result.passed is None
        assert result.reason == "no result_expectation defined"

    def test_not_executed_is_na(self) -> None:
        result = service().check(
            ResultCheckInput(
                case_id="c",
                expectation=ResultExpectation(
                    type=RESULT_TYPE_SCALAR, value=1
                ),
                executed=False,
            )
        )
        assert result.applicable is True
        assert result.passed is None
        assert "no execution result" in result.reason


# ============================================================
# Metrics
# ============================================================

def _check(case_id: str, passed: bool | None, applicable: bool = True
           ) -> Any:
    from backend.app.services.text_to_sql_result_evaluation_service import (
        ResultCheckResult,
    )

    return ResultCheckResult(
        case_id=case_id, applicable=applicable, passed=passed,
        semantic_passed=None, reason="x",
    )


class TestMetrics:
    def test_accuracy_75_percent(self) -> None:
        summary = service().summarize(
            (
                _check("a", True),
                _check("b", True),
                _check("c", True),
                _check("d", False),
            )
        )
        assert summary.applicable_result_cases == 4
        assert summary.correct_result_cases == 3
        assert summary.incorrect_result_cases == 1
        assert summary.result_accuracy == 0.75

    def test_zero_applicable_is_none_not_zero(self) -> None:
        summary = service().summarize((_check("a", None, applicable=False),))
        assert summary.applicable_result_cases == 0
        assert summary.correct_result_cases == 0
        assert summary.incorrect_result_cases == 0
        assert summary.result_accuracy is None

    def test_not_evaluable_excluded_from_denominator(self) -> None:
        summary = service().summarize(
            (
                _check("a", True),
                _check("b", False),
                _check("c", None),
            )
        )
        assert summary.not_evaluable_cases == 1
        assert summary.result_accuracy == 0.5

    def test_summary_arithmetic_consistency(self) -> None:
        checks = (
            _check("a", True), _check("b", False),
            _check("c", None, applicable=False),
        )
        summary = service().summarize(checks)
        assert summary.total_cases == 3
        assert summary.applicable_result_cases == 2
        assert (
            summary.correct_result_cases
            + summary.incorrect_result_cases
            + summary.not_evaluable_cases
        ) == summary.applicable_result_cases

    def test_deterministic_output(self) -> None:
        payload = ResultCheckInput(
            case_id="c", columns=("id",), rows=((1,),),
            expectation=ResultExpectation(
                type=RESULT_TYPE_EXACT_ROWS, rows=((1,),)
            ),
        )
        first = service().check(payload)
        second = service().check(payload)
        assert first == second
        assert first.to_dict() == second.to_dict()

    def test_input_not_modified(self) -> None:
        payload = ResultCheckInput(
            case_id="c", columns=("id",), rows=((1,),),
            expectation=ResultExpectation(
                type=RESULT_TYPE_EXACT_ROWS, rows=((1,),)
            ),
        )
        before = copy.deepcopy(payload)
        service().check(payload)
        assert payload == before


# ============================================================
# Script --check
# ============================================================

class TestResultScriptCheckMode:
    def test_check_mode_does_not_write_files(self, monkeypatch: Any) -> None:
        script = _load_script_module()
        from backend.app.services.text_to_sql_baseline_service import (
            DEFAULT_REGRESSION_DATASET_PATH as DATASET_PATH,
        )
        from backend.app.services.text_to_sql_quality_evaluation_service import (
            TextToSQLQualitySummary,
        )

        del TextToSQLQualitySummary  # 仅确保模块可导入

        targets = (
            script.RESULT_SNAPSHOT_PATH, script.RESULT_REPORT_PATH,
            DATASET_PATH,
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
            script, "RESULT_SNAPSHOT_PATH", tmp_path / "nope.json"
        )
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1

    def test_check_mode_detects_accuracy_drift(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        script = _load_script_module()
        stored = json.loads(
            script.RESULT_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        tampered = copy.deepcopy(stored)
        tampered["correct_result_cases"] = 0
        path = tmp_path / "tampered.json"
        path.write_text(json.dumps(tampered), encoding="utf-8")

        monkeypatch.setattr(script, "RESULT_SNAPSHOT_PATH", path)
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1

    def test_snapshot_has_no_secrets(self) -> None:
        script = _load_script_module()
        payload = json.loads(
            script.RESULT_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        blob = json.dumps(payload, ensure_ascii=False).lower()
        for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
            assert fragment not in blob
        assert "api_key" not in blob

    def test_snapshot_na_cases_have_null_passed(self) -> None:
        script = _load_script_module()
        payload = json.loads(
            script.RESULT_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        for item in payload["cases"]:
            if not item["applicable"]:
                assert item["passed"] is None
