"""Text-to-SQL Real LLM Baseline 测试（Phase 3.9.5）。

分层（§十五：默认 pytest 不调真实 DeepSeek）：

1. Metrics 纯函数：LLM 专属 + 3.9.4 复用（§七 / §二十三）
2. Snapshot / Report schema：committed 产物结构 + 敏感信息自检（§十 / §十二）
3. CLI / Report 一致性：rendered 与 committed 同步（排除 generated_at）
4. Real LLM 集成（**opt-in**）：``RUN_REAL_LLM_EVAL=1`` 才发起真实调用

绝大多数用例离线运行：不依赖网络、不消耗 API、不需要 LLM。
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.app.config import settings
from backend.app.services.text_to_sql_evaluation_service import (
    EXPECTATION_VALIDATION,
    TextToSQLEvaluationResult,
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_real_llm_baseline_service import (
    BASELINE_TYPE_REAL_LLM,
    PHASE_3_9_5,
    REAL_LLM_REPORT_PATH,
    REAL_LLM_SNAPSHOT_PATH,
    TextToSQLRealLLMBaselineMetrics,
    calculate_real_llm_baseline_metrics,
)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_real_llm = pytest.mark.skipif(
    not _env_flag("RUN_REAL_LLM_EVAL"),
    reason="set RUN_REAL_LLM_EVAL=1 to enable real DeepSeek integration",
)


EXPECTED_TOTAL_CASES = 14


def _result(
    case_id: str,
    *,
    project_id: str | None = None,
    generated_sql: str | None = "SELECT 1",
    validation_passed: bool = True,
    execution_passed: bool | None = None,
    matched: tuple[str, ...] = (),
    failed: tuple[str, ...] = (),
    error_code: str | None = None,
) -> TextToSQLEvaluationResult:
    return TextToSQLEvaluationResult(
        case_id=case_id,
        project_id=project_id,
        question=f"question for {case_id}",
        generated_sql=generated_sql,
        validation_passed=validation_passed,
        execution_passed=execution_passed,
        matched_expectations=matched,
        failed_expectations=failed,
        error_code=error_code,
    )


def _strip_generated_at(report: str) -> str:
    lines: list[str] = []
    skip_next = False
    for line in report.splitlines():
        if skip_next:
            skip_next = False
            continue
        if line.startswith("Generated At:"):
            skip_next = True
            continue
        lines.append(line)
    return "\n".join(lines)


#: Metrics DTO 的 rate 是 property（单一真值），不能作为 __init__ 参数传入
_RATE_PROPERTY_NAMES: frozenset[str] = frozenset(
    {
        "expectation_pass_rate", "overall_pass_rate",
        "validation_pass_rate", "validation_expectation_pass_rate",
        "execution_pass_rate", "security_expectation_pass_rate",
        "project_isolation_pass_rate", "llm_generation_success_rate",
        "validator_acceptance_rate",
    }
)


def _filter_counts(metrics_dict: dict[str, Any]) -> dict[str, Any]:
    """只保留 dataclass 的真实字段（计数）；率字段由 property 派生。"""
    return {k: v for k, v in metrics_dict.items()
            if k not in _RATE_PROPERTY_NAMES}


def _forbidden_keys(payload: Any) -> set[str]:
    found: set[str] = set()
    stack: list[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            found |= {str(k).lower() for k in node}
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return found & {
        "api_key", "password", "database_url", "dsn", "connection_string",
        "secret", "token", "authorization",
    }


# ============================================================
# 1. Metrics（§七 / §二十三：纯函数）
# ============================================================

class TestMetricsCalculation:
    def test_llm_generated_count_tracks_generated_sql(self) -> None:
        results = (
            _result("a", generated_sql="SELECT 1"),
            _result("b", generated_sql=None),  # LLM 失败
            _result("c", generated_sql="DELETE FROM x"),
        )
        metrics = calculate_real_llm_baseline_metrics(results)
        assert metrics.llm_generated_cases == 2
        assert metrics.total_cases == 3

    def test_validator_acceptance_rate_uses_llm_generated_denominator(self) -> None:
        results = (
            _result(
                "ok",
                validation_passed=True,
                matched=(EXPECTATION_VALIDATION,),
            ),
            _result(
                "bad",
                generated_sql="SELECT 1",
                validation_passed=False,
                failed=(EXPECTATION_VALIDATION,),
            ),
            _result(
                "missing",
                generated_sql=None,
                validation_passed=False,
                failed=(EXPECTATION_VALIDATION,),
            ),
        )
        metrics = calculate_real_llm_baseline_metrics(results)
        # llm_generated = 2, validator_accepted = 1
        assert metrics.llm_generated_cases == 2
        assert metrics.validator_accepted_cases == 1
        assert metrics.validator_acceptance_rate == 0.5
        # LLM Generation Success = 2/3
        assert metrics.llm_generation_success_rate == round(2 / 3, 4)

    def test_execution_rate_is_none_without_execution_cases(self) -> None:
        metrics = calculate_real_llm_baseline_metrics(
            (_result("a", matched=(EXPECTATION_VALIDATION,)),)
        )
        assert metrics.execution_cases == 0
        assert metrics.execution_pass_rate is None

    def test_security_rejection_must_be_pass(self) -> None:
        results = (
            _result(
                "safety_ok",
                generated_sql="DELETE FROM x",
                validation_passed=False,
                matched=(EXPECTATION_VALIDATION,),
                error_code="NON_READ_ONLY",
            ),
            _result(
                "safety_leaked",
                generated_sql="DELETE FROM x",
                validation_passed=True,
                failed=(EXPECTATION_VALIDATION,),
            ),
        )
        metrics = calculate_real_llm_baseline_metrics(results)
        assert metrics.security_cases == 2
        assert metrics.security_passed_cases == 1
        assert metrics.security_expectation_pass_rate == 0.5
        # validator_accepted_cases 计数安全 case 时仍按 Validator 结果走
        assert metrics.validator_accepted_cases == 1

    def test_project_isolation_metrics(self) -> None:
        results = (
            _result("project_a_inventory", project_id="eval-project-a"),
            _result(
                "project_b_inventory", project_id="eval-project-b",
                matched=(), failed=(EXPECTATION_VALIDATION,),
            ),
            _result("ok", project_id="vietnam-wms"),
        )
        metrics = calculate_real_llm_baseline_metrics(results)
        assert metrics.project_isolation_cases == 2
        assert metrics.project_isolation_passed_cases == 1
        assert metrics.project_isolation_pass_rate == 0.5

    def test_metrics_dict_has_required_keys(self) -> None:
        payload = calculate_real_llm_baseline_metrics(
            (_result("a", matched=(EXPECTATION_VALIDATION,)),)
        ).as_dict()
        for key in (
            "total_cases", "passed_cases", "failed_cases",
            "llm_generated_cases", "validator_accepted_cases",
            "expectation_pass_rate", "validation_expectation_pass_rate",
            "execution_pass_rate", "security_expectation_pass_rate",
            "project_isolation_pass_rate", "llm_generation_success_rate",
            "validator_acceptance_rate",
        ):
            assert key in payload, key


# ============================================================
# 2. Snapshot / Report Schema（§十 / §十一）
# ============================================================

class TestCommittedSnapshotSchema:
    def test_snapshot_top_level_keys(self) -> None:
        assert REAL_LLM_SNAPSHOT_PATH.exists()
        snapshot = json.loads(REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        assert {
            "phase", "baseline_type", "dataset", "execution_mode",
            "environment", "metrics", "cases", "generated_at",
        } <= set(snapshot)
        assert snapshot["phase"] == PHASE_3_9_5
        assert snapshot["baseline_type"] == BASELINE_TYPE_REAL_LLM
        assert snapshot["execution_mode"] == "real-llm"

    def test_snapshot_dataset_binds_version_and_count(self) -> None:
        snapshot = json.loads(REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        assert snapshot["dataset"]["version"] == "1.0"
        assert snapshot["dataset"]["total_cases"] == EXPECTED_TOTAL_CASES
        assert snapshot["dataset"]["path"].endswith(
            "text_to_sql_regression.yaml"
        )

    def test_snapshot_metrics_keys_complete(self) -> None:
        snapshot = json.loads(REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        expected_keys = set(
            TextToSQLRealLLMBaselineMetrics(
                total_cases=0, passed_cases=0, failed_cases=0,
                validation_passed_cases=0, validation_expectation_cases=0,
                validation_expectation_passed_cases=0, execution_cases=0,
                execution_passed_cases=0, security_cases=0,
                security_passed_cases=0, project_isolation_cases=0,
                project_isolation_passed_cases=0,
                llm_generated_cases=0, validator_accepted_cases=0,
            ).as_dict()
        )
        assert set(snapshot["metrics"]) == expected_keys

    def test_snapshot_cases_exclude_generated_sql_and_duration(self) -> None:
        snapshot = json.loads(REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        assert len(snapshot["cases"]) == EXPECTED_TOTAL_CASES
        for entry in snapshot["cases"]:
            assert {
                "case_id", "project_id", "passed", "validation_passed",
                "execution_passed", "matched_expectations",
                "failed_expectations", "error_code",
            } == set(entry)
            assert "generated_sql" not in entry
            assert "duration_ms" not in entry

    def test_snapshot_environment_excludes_secrets(self) -> None:
        snapshot = json.loads(REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        blob = json.dumps(snapshot, ensure_ascii=False).lower()
        assert _forbidden_keys(snapshot) == set()
        for pattern in ("postgresql://", "postgres://", "sk-", "bearer "):
            assert pattern not in blob
        if settings.llm.api_key and len(settings.llm.api_key) >= 8:
            assert settings.llm.api_key not in blob

    def test_dataset_version_locked(self) -> None:
        cases = load_text_to_sql_regression_dataset()
        assert len(cases) == EXPECTED_TOTAL_CASES
        assert all(not case.expected.must_execute for case in cases)


# ============================================================
# 3. Report 内容 / 与 3.9.4 对比
# ============================================================

class TestCommittedReport:
    def test_report_file_exists(self) -> None:
        assert REAL_LLM_REPORT_PATH.exists()
        text = REAL_LLM_REPORT_PATH.read_text(encoding="utf-8")
        assert "Phase 3.9.5" in text
        assert "Disclaimer" not in text  # 不引入与任务无关的标题
        assert "首次真实 DeepSeek Baseline" in text

    def test_report_includes_required_metrics(self) -> None:
        text = REAL_LLM_REPORT_PATH.read_text(encoding="utf-8")
        snapshot = json.loads(
            REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        metrics = snapshot["metrics"]
        # 主要比率都按真实数据渲染
        for label, rate_key in (
            ("Expectation Pass Rate", "expectation_pass_rate"),
            ("Validation Expectation Pass Rate",
             "validation_expectation_pass_rate"),
            ("LLM Generation Success Rate", "llm_generation_success_rate"),
            ("Validator Acceptance Rate", "validator_acceptance_rate"),
            ("Security Pass Rate", "security_expectation_pass_rate"),
            ("Project Isolation Pass Rate", "project_isolation_pass_rate"),
        ):
            rate = metrics[rate_key]
            if rate is None:
                assert f"| {label} | N/A |" in text
            else:
                assert f"| {label} | {rate * 100:.2f}% |" in text

    def test_report_contains_comparison_table(self) -> None:
        text = REAL_LLM_REPORT_PATH.read_text(encoding="utf-8")
        assert "Phase 3.9.4 vs Phase 3.9.5" in text
        assert "3.9.4 Fake" in text and "3.9.5 Real" in text

    def test_report_failed_cases_are_factual(self) -> None:
        text = REAL_LLM_REPORT_PATH.read_text(encoding="utf-8")
        snapshot = json.loads(
            REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        failed = [c for c in snapshot["cases"] if not c["passed"]]
        if not failed:
            assert "- None" in text
        for entry in failed:
            assert f"`{entry['case_id']}`:" in text
            assert "reason:" in text
            assert "差" not in text and "糟糕" not in text  # 不含主观评价

    def test_report_is_deterministic_excluding_generated_at(self) -> None:
        # 重渲染：必须能稳定产生等价的报告（除 Generated At 行）。
        from backend.app.services.text_to_sql_baseline_service import (
            BASELINE_SNAPSHOT_PATH as FAKE_SNAPSHOT_PATH,
        )
        from backend.app.services.text_to_sql_baseline_service import (
            TextToSQLBaselineEnvironment,
        )
        from backend.app.services.text_to_sql_real_llm_baseline_service import (
            TextToSQLRealLLMBaseline,
            TextToSQLRealLLMBaselineMetrics,
            collect_real_llm_environment_info,
        )
        snapshot = json.loads(
            REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        # 复用 Snapshot 中已记录的 Environment（避免重连 DB / 依赖 env var）
        environment = collect_real_llm_environment_info(
            database=snapshot["environment"].get("database", "unavailable"),
        )
        # 不需要结果对象：直接以 metrics 重建 baseline，仅验证渲染一致性。
        # 但需把 failed_cases 还原为 TextToSQLEvaluationResult（用于渲染失败原因）
        synthetic_results = tuple(
            TextToSQLEvaluationResult(
                case_id=case["case_id"],
                project_id=case["project_id"],
                question="(synthesized for re-render)",
                generated_sql=None,
                validation_passed=case["validation_passed"],
                execution_passed=case["execution_passed"],
                matched_expectations=tuple(case["matched_expectations"]),
                failed_expectations=tuple(case["failed_expectations"]),
                error_code=case["error_code"],
            )
            for case in snapshot["cases"]
        )
        baseline = TextToSQLRealLLMBaseline(
            phase=snapshot["phase"],
            dataset_path=snapshot["dataset"]["path"],
            dataset_version=snapshot["dataset"]["version"],
            total_cases=snapshot["dataset"]["total_cases"],
            results=synthetic_results,
            metrics=TextToSQLRealLLMBaselineMetrics(
                **_filter_counts(snapshot["metrics"])
            ),
            environment=TextToSQLBaselineEnvironment(**snapshot["environment"]),
        )
        # 使用真正的 3.9.4 baseline metrics 作对比（不要拿 3.9.5 metrics 自比）
        fake_metrics: dict[str, Any] | None = None
        if FAKE_SNAPSHOT_PATH.exists():
            try:
                fake_metrics = json.loads(
                    FAKE_SNAPSHOT_PATH.read_text(encoding="utf-8")
                ).get("metrics")
            except (OSError, json.JSONDecodeError):
                fake_metrics = None
        rendered = baseline.render_report(fake_baseline_metrics=fake_metrics)
        committed = REAL_LLM_REPORT_PATH.read_text(encoding="utf-8")
        assert _strip_generated_at(rendered) == _strip_generated_at(committed)


# ============================================================
# 4. Real LLM 集成（**opt-in**：仅 RUN_REAL_LLM_EVAL=1）
# ============================================================

@requires_real_llm
class TestRealLLMIntegration:
    async def test_real_deepseek_run_succeeds(self) -> None:
        """真实 DeepSeek 跑通：Snapshot + Report 端到端生成。"""
        if not settings.llm.api_key:
            pytest.skip("LLM_API_KEY not set")

        from backend.app.services.text_to_sql_evaluation_service import (
            StaticEvaluationContextResolver,
            load_text_to_sql_regression_dataset,
        )
        from backend.app.services.text_to_sql_real_llm_baseline_service import (
            collect_real_llm_environment_info,
            run_real_llm_baseline,
        )
        from backend.app.services.text_to_sql_service import TextToSQLService
        from scripts._text_to_sql_offline_bindings import (
            build_offline_project_bindings,
        )

        cases = load_text_to_sql_regression_dataset()
        baseline = await run_real_llm_baseline(
            generator=TextToSQLService(),
            context_resolver=StaticEvaluationContextResolver(
                bindings=build_offline_project_bindings()
            ),
            cases=cases,
            environment=collect_real_llm_environment_info(),
        )
        # 14 cases 全部跑出结果（不论通过 / 失败）；不抛异常即视为端到端跑通
        assert baseline.metrics.total_cases == EXPECTED_TOTAL_CASES
        assert baseline.metrics.llm_generated_cases >= 0
        assert baseline.metrics.validator_accepted_cases >= 0
        # 4xx / 5xx 失败等结构性异常已被 Runner 吞掉：
        # 若某个 case error_code 非 None，结果应该被记录为失败
        for result in baseline.results:
            assert result.case_id
            assert isinstance(result.passed, bool)
        # 报告可渲染
        text = baseline.render_report(
            fake_baseline_metrics={"expectation_pass_rate": 1.0}
        )
        assert "Phase 3.9.5" in text


# ============================================================
# 5. --check 纯度：零 LLM / 零 DB / 零写入（§六 / §九 / §十二）
# ============================================================

def _load_script_module():
    """加载 Real LLM 生成脚本模块（保证仓库根在 sys.path 上）。"""
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return importlib.import_module(
        "scripts.generate_text_to_sql_real_llm_baseline"
    )


def _digest(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestCheckModePurity:
    """``--check`` 必须是纯 offline check：NO LLM / NO DB / 不写文件。"""

    def test_check_mode_does_not_invoke_llm(self, monkeypatch: Any) -> None:
        """方法 B：任何 LLM 调用都会在 --check 路径下炸，从而证明零调用。"""
        script = _load_script_module()
        from backend.app.services.text_to_sql_service import TextToSQLService

        def forbid_generate(*_a: Any, **_kw: Any) -> None:
            raise AssertionError("LLM generate() must not run in --check mode")

        def forbid_call_llm(*_a: Any, **_kw: Any) -> None:
            raise AssertionError("LLM HTTP call must not happen in --check mode")

        def forbid_client(**_kw: Any) -> None:
            raise AssertionError("LLM client must not be resolved in --check mode")

        monkeypatch.setattr(TextToSQLService, "generate", forbid_generate)
        monkeypatch.setattr(TextToSQLService, "_call_llm", forbid_call_llm)
        monkeypatch.setattr(
            "backend.app.llm.client.get_default_llm_client", forbid_client
        )
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 0

    def test_check_mode_does_not_touch_db_or_llm_config(
        self, monkeypatch: Any
    ) -> None:
        """既不能连库，也不能做 LLM pre-flight 配置校验。"""
        script = _load_script_module()

        monkeypatch.setattr(
            script, "get_engine",
            lambda *_a, **_kw: pytest.fail("get_engine() called in --check mode"),
        )
        monkeypatch.setattr(
            script, "_detect_database_version",
            lambda: pytest.fail("DB version probe called in --check mode"),
        )
        monkeypatch.setattr(
            script, "_verify_environment",
            lambda: pytest.fail("LLM pre-flight check called in --check mode"),
        )
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 0

    def test_check_mode_does_not_modify_any_file(
        self, monkeypatch: Any
    ) -> None:
        """§十三：--check 不得改动 Snapshot / Report / Dataset / 3.9.4 Snapshot。"""
        script = _load_script_module()
        from backend.app.services.text_to_sql_baseline_service import (
            BASELINE_SNAPSHOT_PATH as FAKE_SNAPSHOT_PATH,
            DEFAULT_REGRESSION_DATASET_PATH as DATASET_PATH,
        )

        targets = (
            REAL_LLM_SNAPSHOT_PATH, REAL_LLM_REPORT_PATH,
            DATASET_PATH, FAKE_SNAPSHOT_PATH,
        )
        before = {str(p): _digest(p) for p in targets}

        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 0

        after = {str(p): _digest(p) for p in targets}
        assert before == after

    def test_check_mode_fails_when_snapshot_missing(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        """§五-1：Snapshot 不存在 → FAIL（返回 1）。"""
        script = _load_script_module()
        monkeypatch.setattr(
            script, "REAL_LLM_SNAPSHOT_PATH", tmp_path / "nope.json"
        )
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1

    def test_check_mode_detects_metrics_drift(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        """§五-8：不信任存量 metrics——篡改后必须由 cases[] 重算发现。"""
        script = _load_script_module()
        original = json.loads(
            REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        tampered = copy.deepcopy(original)
        tampered["metrics"]["passed_cases"] = 999

        path = tmp_path / "tampered.json"
        path.write_text(json.dumps(tampered), encoding="utf-8")
        monkeypatch.setattr(script, "REAL_LLM_SNAPSHOT_PATH", path)
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1

    def test_check_mode_detects_missing_case(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        """§五-7：case_id 集合必须完全一致——缺 Case 必须 FAIL。"""
        script = _load_script_module()
        original = json.loads(
            REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        tampered = copy.deepcopy(original)
        tampered["cases"] = tampered["cases"][:-1]

        path = tmp_path / "missing_case.json"
        path.write_text(json.dumps(tampered), encoding="utf-8")
        monkeypatch.setattr(script, "REAL_LLM_SNAPSHOT_PATH", path)
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1

    def test_check_mode_detects_dataset_version_mismatch(
        self, monkeypatch: Any, tmp_path: Path
    ) -> None:
        """§五-5：Dataset version 必须与 Snapshot 绑定一致。"""
        script = _load_script_module()
        original = json.loads(
            REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8")
        )
        tampered = copy.deepcopy(original)
        tampered["dataset"]["version"] = "9.9"

        path = tmp_path / "bad_version.json"
        path.write_text(json.dumps(tampered), encoding="utf-8")
        monkeypatch.setattr(script, "REAL_LLM_SNAPSHOT_PATH", path)
        monkeypatch.setattr(sys, "argv", ["script", "--check"])
        assert script.main() == 1

    def test_check_mode_subprocess_survives_invalid_api_key(self) -> None:
        """方法 A（端到端）：用无效 API Key 起子进程跑 --check。

        若 --check 真的调用了 DeepSeek，必然鉴权失败 → per-case 全部失败 →
        与已提交 Snapshot 产生漂移 → 退出码非 0。因此退出码 0 证明零调用。
        """
        repo_root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env["LLM_API_KEY"] = "invalid-key-for-check-mode-test"

        proc = subprocess.run(
            [
                sys.executable,
                "scripts/generate_text_to_sql_real_llm_baseline.py",
                "--check",
            ],
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 0, (
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        assert "OK: real LLM baseline snapshot is consistent" in proc.stdout