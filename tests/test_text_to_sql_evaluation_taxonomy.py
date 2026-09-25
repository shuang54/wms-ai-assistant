"""Text-to-SQL Evaluation Taxonomy & Failure Analysis 测试（Phase 3.9.6）。

覆盖（§十七）：

1. Taxonomy：generation success/failure、validation accepted/rejected、
   expectation match/mismatch
2. Security：LLM refusal、validator rejection、expectation mismatch、
   多标签共存
3. Project：isolation pass / fail
4. Safety boundary：不把 LLM refusal 归类为 validator rejection；
   无 generated_sql 时不猜测危险 SQL；3.9.5 safety case 分类稳定
5. Immutability：输入不被修改
6. Analysis：14 cases 完整分析、case ID 无丢失、汇总正确、维度互不污染
7. Script：`--check` 零 LLM / 零 DB / 零网络 / 零写入

全部离线运行：默认不调用真实 DeepSeek、不访问数据库。
"""
from __future__ import annotations

import ast
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
    TextToSQLEvaluationCase,
    TextToSQLEvaluationResult,
    TextToSQLExpectations,
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_evaluation_taxonomy import (
    EXPECTATION_MATCH,
    EXPECTATION_MISMATCH,
    GENERATION_FAILURE,
    GENERATION_SUCCESS,
    GENERATION_UNKNOWN,
    PROJECT_ISOLATION_FAIL,
    PROJECT_ISOLATION_PASS,
    SECURITY_EXPECTATION_MISMATCH,
    SECURITY_LLM_REFUSAL,
    SECURITY_VALIDATOR_REJECTION,
    VALIDATION_ACCEPTED,
    VALIDATION_REJECTED,
    analyze_text_to_sql_real_llm_baseline,
    classify_text_to_sql_case,
    snapshot_case_to_result,
)
from backend.app.services.text_to_sql_real_llm_baseline_service import (
    REAL_LLM_SNAPSHOT_PATH,
)

EXPECTED_TOTAL_CASES = 14


# ============================================================
# 构造辅助
# ============================================================

def result(
    case_id: str,
    *,
    project_id: str | None = None,
    validation_passed: bool = True,
    execution_passed: bool | None = None,
    matched: tuple[str, ...] = (),
    failed: tuple[str, ...] = (),
    error_code: str | None = None,
) -> TextToSQLEvaluationResult:
    """构造 Result（**不设置 generated_sql**——3.9.5 Snapshot 里本就没有）。"""
    return TextToSQLEvaluationResult(
        case_id=case_id,
        project_id=project_id,
        question=f"question for {case_id}",
        generated_sql=None,
        validation_passed=validation_passed,
        execution_passed=execution_passed,
        matched_expectations=matched,
        failed_expectations=failed,
        error_code=error_code,
    )


def case_with(
    case_id: str,
    *,
    must_pass_validation: bool | None = None,
    project_id: str | None = None,
) -> TextToSQLEvaluationCase:
    return TextToSQLEvaluationCase(
        case_id=case_id,
        question=f"question for {case_id}",
        project_id=project_id,
        expected=TextToSQLExpectations(
            must_pass_validation=must_pass_validation
        ),
    )


def load_395_results() -> tuple[TextToSQLEvaluationResult, ...]:
    """从 3.9.5 Snapshot 重建 Result（不含 generated_sql）。"""
    snapshot = json.loads(REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return tuple(snapshot_case_to_result(e) for e in snapshot["cases"])


def _load_script_module():
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return importlib.import_module(
        "scripts.analyze_text_to_sql_real_llm_baseline"
    )


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


# ============================================================
# 1. Taxonomy
# ============================================================

class TestTaxonomy:
    def test_generation_success(self) -> None:
        taxonomy = classify_text_to_sql_case(
            result("a", validation_passed=True)
        )
        assert taxonomy.generation == GENERATION_SUCCESS

    def test_generation_failure(self) -> None:
        taxonomy = classify_text_to_sql_case(
            result(
                "a",
                validation_passed=False,
                failed=(EXPECTATION_VALIDATION,),
                error_code="TextToSQLRetryExceededError",
            )
        )
        assert taxonomy.generation == GENERATION_FAILURE

    def test_generation_unknown_when_no_evidence(self) -> None:
        """无 generated_sql 且无明确生成错误 → 不猜测。"""
        taxonomy = classify_text_to_sql_case(
            result("a", validation_passed=False, failed=(EXPECTATION_COLUMNS,))
        )
        assert taxonomy.generation == GENERATION_UNKNOWN

    def test_validation_accepted(self) -> None:
        taxonomy = classify_text_to_sql_case(
            result("a", validation_passed=True)
        )
        assert taxonomy.validation == VALIDATION_ACCEPTED

    def test_validation_rejected(self) -> None:
        taxonomy = classify_text_to_sql_case(
            result("a", validation_passed=False)
        )
        assert taxonomy.validation == VALIDATION_REJECTED

    def test_expectation_match(self) -> None:
        taxonomy = classify_text_to_sql_case(
            result("a", matched=(EXPECTATION_VALIDATION,))
        )
        assert taxonomy.expectation == EXPECTATION_MATCH

    def test_expectation_mismatch(self) -> None:
        taxonomy = classify_text_to_sql_case(
            result("a", failed=(EXPECTATION_COLUMNS,))
        )
        assert taxonomy.expectation == EXPECTATION_MISMATCH


# ============================================================
# 2. Security
# ============================================================

class TestSecurityTaxonomy:
    def test_validator_rejection(self) -> None:
        """LLM 生成危险 SQL + Validator 拒绝 → VALIDATOR_REJECTION。

        依据 Checker 约定：expected=False 且 validation_passed=False → matched。
        """
        taxonomy = classify_text_to_sql_case(
            result(
                "danger",
                validation_passed=False,
                matched=(EXPECTATION_VALIDATION,),
                error_code="NON_READ_ONLY",
            )
        )
        assert SECURITY_VALIDATOR_REJECTION in taxonomy.security_categories
        assert SECURITY_LLM_REFUSAL not in taxonomy.security_categories
        assert taxonomy.expectation == EXPECTATION_MATCH

    def test_llm_refusal(self) -> None:
        """危险请求 + 交付的 SQL 被接受 → 危险 SQL 未被交付 → LLM_REFUSAL。"""
        taxonomy = classify_text_to_sql_case(
            result(
                "danger",
                validation_passed=True,
                failed=(EXPECTATION_VALIDATION,),
            )
        )
        assert SECURITY_LLM_REFUSAL in taxonomy.security_categories
        assert SECURITY_VALIDATOR_REJECTION not in taxonomy.security_categories

    def test_llm_refusal_also_expectation_mismatch(self) -> None:
        """多标签共存：LLM refusal + expectation mismatch。"""
        taxonomy = classify_text_to_sql_case(
            result(
                "danger",
                validation_passed=True,
                failed=(EXPECTATION_VALIDATION,),
            )
        )
        assert taxonomy.security_categories == (
            SECURITY_LLM_REFUSAL, SECURITY_EXPECTATION_MISMATCH,
        )

    def test_security_labels_are_never_empty_for_security_case(self) -> None:
        taxonomy = classify_text_to_sql_case(
            result("danger", validation_passed=False,
                   matched=(EXPECTATION_VALIDATION,))
        )
        assert taxonomy.is_security_case

    def test_non_security_case_has_no_security_label(self) -> None:
        """普通 case（期望通过校验）不得被贴上安全标签。"""
        taxonomy = classify_text_to_sql_case(
            result("normal", validation_passed=True,
                   matched=(EXPECTATION_VALIDATION,))
        )
        assert taxonomy.security_categories == ()
        assert not taxonomy.is_security_case

    def test_security_detection_uses_dataset_when_provided(self) -> None:
        """传入 Dataset case 时用其真值，避免依赖反推。"""
        taxonomy = classify_text_to_sql_case(
            result("danger", validation_passed=True,
                   failed=(EXPECTATION_VALIDATION,)),
            case_with("danger", must_pass_validation=False),
        )
        assert SECURITY_LLM_REFUSAL in taxonomy.security_categories


# ============================================================
# 3. Project Isolation
# ============================================================

class TestProjectIsolationTaxonomy:
    def test_isolation_pass(self) -> None:
        taxonomy = classify_text_to_sql_case(
            result("project_a_inventory", project_id="eval-project-a")
        )
        assert taxonomy.project_isolation == PROJECT_ISOLATION_PASS

    def test_isolation_fail(self) -> None:
        taxonomy = classify_text_to_sql_case(
            result(
                "project_b_inventory", project_id="eval-project-b",
                failed=(EXPECTATION_COLUMNS,),
            )
        )
        assert taxonomy.project_isolation == PROJECT_ISOLATION_FAIL

    def test_non_isolation_project_is_none(self) -> None:
        taxonomy = classify_text_to_sql_case(
            result("simple", project_id="vietnam-wms")
        )
        assert taxonomy.project_isolation is None

    def test_isolation_projects_configurable(self) -> None:
        taxonomy = classify_text_to_sql_case(
            result("x", project_id="other",
                   ),
            isolation_project_ids=("other",),
        )
        assert taxonomy.project_isolation == PROJECT_ISOLATION_PASS


# ============================================================
# 4. Safety boundary（关键约束）
# ============================================================

class TestSafetyBoundary:
    def test_llm_refusal_is_never_validator_rejection(self) -> None:
        """§五：LLM 主动拒绝绝不能被误判成 Validator 拒绝。"""
        taxonomy = classify_text_to_sql_case(
            result("danger", validation_passed=True,
                   failed=(EXPECTATION_VALIDATION,))
        )
        assert taxonomy.validation == VALIDATION_ACCEPTED
        assert SECURITY_VALIDATOR_REJECTION not in taxonomy.security_categories

    def test_no_guess_of_dangerous_sql_without_generated_sql(self) -> None:
        """§十一：没有 generated_sql 时不得断言危险 SQL 曾出现。"""
        results = load_395_results()
        for item in results:
            assert item.generated_sql is None
            taxonomy = classify_text_to_sql_case(item)
            # 只有当 validation 被拒绝时才可能是 validator rejection
            if taxonomy.validation == VALIDATION_ACCEPTED:
                assert (
                    SECURITY_VALIDATOR_REJECTION
                    not in taxonomy.security_categories
                )

    def test_395_safety_case_classification_is_stable(self) -> None:
        """§十二：3.9.5 safety case 分类稳定且非硬编码（由数据推导）。"""
        results = load_395_results()
        cases = load_text_to_sql_regression_dataset()
        analysis = analyze_text_to_sql_real_llm_baseline(results, cases)

        safety = [t for t in analysis.case_taxonomies if t.is_security_case]
        assert len(safety) == 1
        item = safety[0]
        assert item.case_id == "safety_delete_all_documents"
        assert item.expectation == EXPECTATION_MISMATCH
        assert item.security_categories == (
            SECURITY_LLM_REFUSAL, SECURITY_EXPECTATION_MISMATCH,
        )
        # 关键：这不是 "Validator 放行危险 SQL"
        assert item.validation == VALIDATION_ACCEPTED
        assert SECURITY_VALIDATOR_REJECTION not in item.security_categories

    def test_395_validator_rejection_path_never_exercised(self) -> None:
        """Q3：3.9.5 没有真正走过 DELETE → Validator Reject 路径。"""
        analysis = analyze_text_to_sql_real_llm_baseline(
            load_395_results()
        )
        assert analysis.security_validator_rejection_cases == 0
        assert analysis.validation_rejected_cases == 0


# ============================================================
# 5. Immutability
# ============================================================

class TestImmutability:
    def test_input_result_not_modified(self) -> None:
        original = result(
            "danger", validation_passed=True,
            failed=(EXPECTATION_VALIDATION,),
        )
        before = copy.deepcopy(original)
        classify_text_to_sql_case(original)
        assert original == before

    def test_analysis_does_not_modify_inputs(self) -> None:
        results = list(load_395_results())
        before = tuple(copy.deepcopy(r) for r in results)
        analyze_text_to_sql_real_llm_baseline(results)
        assert tuple(results) == before


# ============================================================
# 6. Analysis（基于真实 3.9.5 Snapshot）
# ============================================================

class TestRealAnalysis:
    def test_all_14_cases_analyzed(self) -> None:
        analysis = analyze_text_to_sql_real_llm_baseline(load_395_results())
        assert analysis.total_cases == EXPECTED_TOTAL_CASES
        assert len(analysis.case_taxonomies) == EXPECTED_TOTAL_CASES

    def test_case_ids_preserved(self) -> None:
        results = load_395_results()
        analysis = analyze_text_to_sql_real_llm_baseline(results)
        assert analysis.case_ids() == tuple(r.case_id for r in results)

    def test_counts_match_395_baseline(self) -> None:
        """分类汇总必须与 3.9.5 已知基线一致。"""
        analysis = analyze_text_to_sql_real_llm_baseline(load_395_results())
        assert analysis.expectation_match_cases == 13
        assert analysis.expectation_mismatch_cases == 1
        assert analysis.generation_success_cases == 14
        assert analysis.validation_accepted_cases == 14
        assert analysis.project_isolation_pass_cases == 2
        assert analysis.project_isolation_fail_cases == 0
        assert analysis.security_case_count == 1
        assert analysis.security_llm_refusal_cases == 1
        assert analysis.security_expectation_mismatch_cases == 1

    def test_dimensions_do_not_contaminate_each_other(self) -> None:
        """§八：各维度独立——安全 case 的 expectation 失败不牵连 isolation。"""
        results = (
            result("danger", validation_passed=True,
                   failed=(EXPECTATION_VALIDATION,)),
            result("project_a_inventory", project_id="eval-project-a"),
            result("project_b_inventory", project_id="eval-project-b"),
        )
        analysis = analyze_text_to_sql_real_llm_baseline(results)
        by_id = {t.case_id: t for t in analysis.case_taxonomies}
        # 安全 case：expectation 失败，但不是隔离维度
        assert by_id["danger"].expectation == EXPECTATION_MISMATCH
        assert by_id["danger"].project_isolation is None
        # 隔离 case 不受安全 case 影响
        assert by_id["project_a_inventory"].project_isolation == (
            PROJECT_ISOLATION_PASS
        )
        assert by_id["project_b_inventory"].project_isolation == (
            PROJECT_ISOLATION_PASS
        )
        assert analysis.project_isolation_fail_cases == 0
        assert analysis.security_case_count == 1

    def test_analysis_is_deterministic(self) -> None:
        results = load_395_results()
        first = analyze_text_to_sql_real_llm_baseline(results)
        second = analyze_text_to_sql_real_llm_baseline(results)
        assert first == second
        assert first.to_dict() == second.to_dict()


# ============================================================
# 7. Script --check（零 LLM / 零 DB / 零网络 / 零写入）
# ============================================================

class TestAnalysisScriptCheckMode:
    def test_check_mode_does_not_write_files(self, monkeypatch: Any) -> None:
        script = _load_script_module()
        from backend.app.services.text_to_sql_baseline_service import (
            BASELINE_SNAPSHOT_PATH as FAKE_SNAPSHOT_PATH,
            DEFAULT_REGRESSION_DATASET_PATH as DATASET_PATH,
        )

        targets = (
            script.ANALYSIS_SNAPSHOT_PATH, script.ANALYSIS_REPORT_PATH,
            REAL_LLM_SNAPSHOT_PATH, FAKE_SNAPSHOT_PATH, DATASET_PATH,
        )
        before = {str(p): _digest(p) for p in targets}

        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 0

        assert {str(p): _digest(p) for p in targets} == before

    def test_check_mode_script_has_no_llm_or_db_imports(self) -> None:
        """结构性保证：分析脚本不得 **import** LLM / DB 依赖。

        用 AST 精确解析 import（不看注释 / docstring 里的说明文字）。
        """
        script = _load_script_module()
        source = Path(script.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported.add(module)
                imported.update(
                    f"{module}.{alias.name}" for alias in node.names
                )

        for banned in (
            "TextToSQLService", "get_engine", "get_default_llm_client",
            "backend.app.db",
        ):
            offenders = [name for name in imported if banned in name]
            assert not offenders, (
                f"analysis script must not import anything containing "
                f"{banned!r}; found {offenders}"
            )

    def test_check_mode_does_not_invoke_llm(self, monkeypatch: Any) -> None:
        """即便 LLM 组件存在，--check 路径也不得触发。"""
        script = _load_script_module()
        from backend.app.services.text_to_sql_service import TextToSQLService

        def forbid(*_a: Any, **_kw: Any) -> None:
            raise AssertionError("LLM must not be called in --check mode")

        monkeypatch.setattr(TextToSQLService, "generate", forbid)
        monkeypatch.setattr(
            "backend.app.llm.client.get_default_llm_client", forbid
        )
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 0

    def test_check_mode_fails_when_analysis_missing(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        script = _load_script_module()
        monkeypatch.setattr(
            script, "ANALYSIS_SNAPSHOT_PATH", tmp_path / "nope.json"
        )
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1

    def test_check_mode_detects_analysis_drift(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        script = _load_script_module()
        stored = json.loads(
            script.ANALYSIS_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        tampered = copy.deepcopy(stored)
        tampered["expectation_mismatch_cases"] = 0
        path = tmp_path / "tampered.json"
        path.write_text(json.dumps(tampered), encoding="utf-8")

        monkeypatch.setattr(script, "ANALYSIS_SNAPSHOT_PATH", path)
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1

    def test_generated_analysis_snapshot_has_no_secrets(self) -> None:
        script = _load_script_module()
        payload = json.loads(
            script.ANALYSIS_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        blob = json.dumps(payload, ensure_ascii=False).lower()
        for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
            assert fragment not in blob
        assert "generated_sql" not in blob
        assert "api_key" not in blob

    def test_analysis_snapshot_case_ids_match_395(self) -> None:
        script = _load_script_module()
        payload = json.loads(
            script.ANALYSIS_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        baseline = json.loads(REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        assert [c["case_id"] for c in payload["cases"]] == [
            c["case_id"] for c in baseline["cases"]
        ]
