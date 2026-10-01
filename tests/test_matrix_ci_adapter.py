"""CI Adapter（Phase 3.12 Step 95）专用测试。

覆盖 §十四 Test 1～12：PASS→0 · DRIFT→1 · 所有 drift→1 · 非法输入拒绝 · 身份/不可变 ·
100 次确定性 · 安全静态审计 · Baseline 隔离 · 不重算 drift · 复用 Step 94 映射 · 无隐藏执行。

本文件为**纯静态 / 合成**：无 Regression Matrix 执行、无 DB、无网络、无 LLM。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.app.services.matrix_ci_adapter import (
    CI_ADAPTER_EXIT_CODES,
    adapt_gate_result_to_exit_code,
)
from tests.test_assistant_trace_timeline_regression import (  # noqa: PLC2701
    CI_ADAPTER_EXIT_CODES as COLLECTOR_CI_ADAPTER_EXIT_CODES,
    MatrixBaselineGateResult,
    MatrixExecutionBaseline,
    MatrixExecutionSummary,
    RegressionExecutionSummary,
    _synthetic_baseline,
    _synthetic_db,
    _synthetic_offline,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ADAPTER_MODULE = "backend/app/services/matrix_ci_adapter.py"


def _gate_result(status: str, drifts: tuple[str, ...]) -> MatrixBaselineGateResult:
    baseline = _synthetic_baseline(
        offline=_synthetic_offline(), db=_synthetic_db(), residue=0
    )
    return MatrixBaselineGateResult(
        status=status, drifts=drifts, current=baseline, baseline=baseline
    )


def _adapter_source() -> str:
    return (_REPO_ROOT / _ADAPTER_MODULE).read_text(encoding="utf-8")


# ============================================================
# Test 1 ~ 3：PASS / DRIFT / 所有 drift
# ============================================================

class TestExitCodeMapping:
    def test_1_pass_maps_to_zero(self) -> None:
        assert adapt_gate_result_to_exit_code(
            _gate_result("PASS", ("NO_BASELINE_DRIFT",))
        ) == 0

    def test_2_drift_maps_to_one(self) -> None:
        assert adapt_gate_result_to_exit_code(
            _gate_result("DRIFT", ("DB_RESIDUE_DRIFT",))
        ) == 1

    def test_3_all_drift_types_map_to_one(self) -> None:
        """Adapter 完全不关心 drift 类型：一律 1（不得按类型选不同退出码）。"""
        for drift in (
            "OFFLINE_EXECUTION_DRIFT",
            "DB_EXECUTION_DRIFT",
            "MATRIX_TOTAL_DRIFT",
            "MATRIX_STATUS_DRIFT",
            "DB_RESIDUE_DRIFT",
        ):
            assert drift not in CI_ADAPTER_EXIT_CODES
            assert adapt_gate_result_to_exit_code(
                _gate_result("DRIFT", (drift,))
            ) == 1


# ============================================================
# Test 4：非法输入
# ============================================================

class TestInputContract:
    def test_4_illegal_inputs_are_rejected(self) -> None:
        summary = MatrixExecutionSummary(
            offline=_synthetic_offline(), db=_synthetic_db()
        )
        baseline: MatrixExecutionBaseline = _synthetic_baseline(
            offline=_synthetic_offline(), db=_synthetic_db(), residue=0
        )

        for bad in (
            None,
            {"status": "PASS"},
            "PASS",
            ("PASS",),
            summary,
            baseline,
            object(),
        ):
            with pytest.raises(TypeError):
                adapt_gate_result_to_exit_code(bad)  # type: ignore[arg-type]

    def test_4b_no_automatic_conversion(self) -> None:
        """不做 dict / str → Gate Result 的自动转换（严格类型契约）。"""
        with pytest.raises(TypeError):
            adapt_gate_result_to_exit_code({"status": "PASS", "drifts": ()})  # type: ignore[arg-type]


# ============================================================
# Test 5 ~ 7：Identity / 不可变 / 100 次确定性
# ============================================================

class TestPurity:
    def test_5_adapter_does_not_modify_result(self) -> None:
        result = _gate_result("DRIFT", ("DB_RESIDUE_DRIFT",))
        before = (result, result.status, result.drifts, result.current, result.baseline)

        adapt_gate_result_to_exit_code(result)

        assert (
            result,
            result.status,
            result.drifts,
            result.current,
            result.baseline,
        ) == before

    def test_6_gate_result_stays_frozen(self) -> None:
        result = _gate_result("PASS", ("NO_BASELINE_DRIFT",))

        for field_name in ("status", "drifts", "current", "baseline"):
            with pytest.raises(Exception) as excinfo:
                setattr(result, field_name, None)
            assert "frozen" in type(excinfo.value).__name__.lower()

    def test_7_determinism_over_one_hundred_calls(self) -> None:
        passing = _gate_result("PASS", ("NO_BASELINE_DRIFT",))
        drifting = _gate_result("DRIFT", ("OFFLINE_EXECUTION_DRIFT",))

        assert {
            adapt_gate_result_to_exit_code(passing) for _ in range(100)
        } == {0}
        assert {
            adapt_gate_result_to_exit_code(drifting) for _ in range(100)
        } == {1}


# ============================================================
# Test 8 ~ 12：静态边界
# ============================================================

class TestStaticBoundaries:
    @staticmethod
    def _executable_source() -> str:
        """模块的可执行源码（**排除** docstring / 注释）。"""
        tree = ast.parse(_adapter_source())
        parts: list[str] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for statement in node.body:
                    if (
                        isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Constant)
                        and isinstance(statement.value.value, str)
                    ):
                        continue                      # 跳过 docstring
                    parts.append(ast.unparse(statement))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                parts.append(ast.unparse(node))
        return "\n".join(parts)

    def test_8_no_side_effect_or_infra_dependencies(self) -> None:
        source = self._executable_source()

        for forbidden in (
            "sys" + ".exit",
            "sub" + "process",
            "sql" + "alchemy",
            "psyc" + "opg",
            "htt" + "px",
            "open" + "ai",
            "deep" + "seek",
            "get_" + "engine",
        ):
            assert forbidden not in source, forbidden

    def test_9_baseline_isolation(self) -> None:
        source = self._executable_source()

        for forbidden in (
            "MATRIX_" + "EXECUTION_BASELINE",
            "Matrix" + "ExecutionBaseline",
            "compare_" + "matrix_execution_baseline",
            "evaluate_" + "matrix_baseline_gate",
        ):
            assert forbidden not in source, forbidden

    def test_10_no_drift_recalculation(self) -> None:
        """只读取 status；不检查 current / baseline / offline / db / total / residue。"""
        source = self._executable_source()

        for forbidden in (
            ".current",
            ".baseline",
            ".offline",
            ".db",
            "matrix_" + "total",
            "matrix_" + "status",
            "db_" + "residue",
        ):
            assert forbidden not in source, forbidden
        assert "result.status" in source

    def test_11_mapping_is_reused_from_step94(self) -> None:
        """复用 Step 94 的映射（**同一个对象**），不创建第二份 PASS→0 / DRIFT→1。"""
        assert CI_ADAPTER_EXIT_CODES is COLLECTOR_CI_ADAPTER_EXIT_CODES
        assert dict(CI_ADAPTER_EXIT_CODES) == {"PASS": 0, "DRIFT": 1}

    def test_12_no_hidden_execution(self) -> None:
        """调用 Adapter 不触发 Matrix / DB / Network / LLM（静态 + 运行时双重证明）。"""
        source = self._executable_source()

        for forbidden in (
            "_run_" + "pytest",
            "_matrix_" + "execution_summary",
            "_db_" + "residue_total",
            "pytest",
            "engine",
            "connect",
        ):
            assert forbidden not in source, forbidden
        # 运行时：合成 result 上连续调用，输入不变、结果恒定
        result = _gate_result("PASS", ("NO_BASELINE_DRIFT",))
        assert [adapt_gate_result_to_exit_code(result) for _ in range(5)] == [
            0,
            0,
            0,
            0,
            0,
        ]


# ============================================================
# 附加：模块级依赖边界（§十三）
# ============================================================

class TestModuleDependencies:
    def test_module_imports_only_stdlib(self) -> None:
        tree = ast.parse(_adapter_source())
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)

        assert modules <= {"__future__", "types", "typing"}, sorted(modules)

    def test_no_cli_or_entrypoint(self) -> None:
        tree = ast.parse(_adapter_source())
        names = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]

        for forbidden in ("main", "cli", "run_gate", "run_matrix_gate"):
            assert forbidden not in names, forbidden
        assert names == ["adapt_gate_result_to_exit_code"], names

    def test_project_ci_surface_is_minimal(self) -> None:
        """Step 96 授权本地 CLI；Step 97 授权**一个** workflow；其它入口仍禁止。"""
        github_dir = _REPO_ROOT / ".github"

        assert github_dir.is_dir()
        workflows = sorted(
            path.name
            for path in (github_dir / "workflows").iterdir()
            if path.is_file()
        )
        assert workflows == ["observability-matrix-gate.yml"], workflows
        assert not (_REPO_ROOT / "scripts/check_baseline.py").exists()

        cli = _REPO_ROOT / "scripts" / "run_matrix_gate.py"

        assert cli.is_file()
        source = cli.read_text(encoding="utf-8")
        for forbidden in ("argparse", "click", "typer", "sys.argv"):
            assert forbidden not in source, forbidden

    def test_synthetic_helpers_are_pure(self) -> None:
        """本文件使用的合成 helper 不触发执行（断言其构造无副作用）。"""
        summary = RegressionExecutionSummary(
            total=1, passed=1, skipped=0, failed=0, errors=0, exit_code=0
        )

        assert summary.status == "PASS"
        assert _synthetic_offline().total == 375
