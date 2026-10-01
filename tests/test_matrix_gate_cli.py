"""Phase 3.12 Step 96 — 本地 Matrix Gate CLI 测试。

覆盖 §十二 1～10：PASS→0 · DRIFT→1 · SystemExit 语义 · Adapter 复用 · Gate 复用 ·
无 Baseline 刷新 · 无隐藏依赖 · 无 secrets · 确定性 · 无 CLI 参数。

本文件为**纯合成 + 静态**：不执行 18 offline / 15 DB 文件（Gate 结果用 fake 注入）。
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from backend.app.services.matrix_ci_adapter import (
    CI_ADAPTER_EXIT_CODES,
    adapt_gate_result_to_exit_code,
)
from tests.test_assistant_trace_timeline_regression import (  # noqa: PLC2701
    CI_GATE_CLI,
    MatrixBaselineGateResult,
    _synthetic_baseline,
    _synthetic_db,
    _synthetic_offline,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "run_matrix_gate.py"


def _load_cli_module():
    """按文件路径加载 CLI（不触发 `__main__` 分支）。"""
    spec = importlib.util.spec_from_file_location("run_matrix_gate", _SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_result(status: str, drifts: tuple[str, ...]) -> MatrixBaselineGateResult:
    baseline = _synthetic_baseline(
        offline=_synthetic_offline(), db=_synthetic_db(), residue=0
    )
    return MatrixBaselineGateResult(
        status=status, drifts=drifts, current=baseline, baseline=baseline
    )


def _executable_source() -> str:
    """CLI 的可执行源码（**排除** docstring 与注释）。"""
    tree = ast.parse(_SCRIPT_PATH.read_text(encoding="utf-8"))
    parts: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for statement in node.body:
                if (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                ):
                    continue
                parts.append(ast.unparse(statement))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            parts.append(ast.unparse(node))
        elif isinstance(node, ast.If):
            parts.append(ast.unparse(node))
    return "\n".join(parts)


# ============================================================
# 1 ~ 3：PASS / DRIFT / SystemExit
# ============================================================

class TestCliExitCodes:
    def test_1_pass_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        module = _load_cli_module()
        module._gate_result = lambda: _fake_result("PASS", ("NO_BASELINE_DRIFT",))

        assert module.main() == 0
        out = capsys.readouterr().out
        assert "Status: PASS" in out and "Exit code: 0" in out

    def test_2_drift_returns_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        module = _load_cli_module()
        module._gate_result = lambda: _fake_result("DRIFT", ("DB_EXECUTION_DRIFT",))

        assert module.main() == 1
        out = capsys.readouterr().out
        assert "Status: DRIFT" in out
        assert "- DB_EXECUTION_DRIFT" in out
        assert "Exit code: 1" in out

    def test_3_main_guard_raises_system_exit(self) -> None:
        """`__main__` 分支必须是 `raise SystemExit(main())`（静态 + 语义双重验证）。"""
        source = _SCRIPT_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        guards = [
            ast.unparse(node)
            for node in tree.body
            if isinstance(node, ast.If)
        ]

        assert any(
            'raise SystemExit(main())' in guard for guard in guards
        ), guards
        for code, expected in ((0, 0), (1, 1)):
            namespace: dict[str, object] = {
                "__name__": "__main__",
                "main": (lambda value=code: value),
            }
            with pytest.raises(SystemExit) as excinfo:
                exec("raise SystemExit(main())", namespace)
            assert excinfo.value.code == expected

    def test_3b_adapter_is_the_only_mapping_source(self) -> None:
        """CLI 输出的 exit code 必须来自 Adapter（与映射表一致）。"""
        module = _load_cli_module()
        for (status, drift), expected in (
            (("PASS", "NO_BASELINE_DRIFT"), 0),
            (("DRIFT", "DB_RESIDUE_DRIFT"), 1),
        ):
            result = _fake_result(status, (drift,))
            module._gate_result = lambda value=result: value

            assert module.main() == expected == adapt_gate_result_to_exit_code(result)
        assert CI_ADAPTER_EXIT_CODES == {"PASS": 0, "DRIFT": 1}


# ============================================================
# 4 ~ 5：Adapter / Gate 复用
# ============================================================

class TestReuse:
    def test_4_cli_calls_adapter_not_own_mapping(self) -> None:
        source = _executable_source()

        assert "adapt_gate_result_to_exit_code(" in source
        # CLI 不得自带第二份映射（PASS/DRIFT → 0/1 字面量）
        for forbidden in ('"PASS": 0', '"PASS":0', '"DRIFT": 1', '"DRIFT":1'):
            assert forbidden not in source, forbidden
        # 唯一返回来自 Adapter（另允许 DB 不可用时的 fail-closed `return 1`）
        assert "return exit_code" in source
        assert "return 0" not in source

    def test_5_cli_calls_gate_and_does_not_compare(self) -> None:
        source = _executable_source()

        assert "evaluate_matrix_baseline_gate(" in source
        for forbidden in ("!=", "=="):
            lines = [
                line
                for line in source.splitlines()
                if forbidden in line and "baseline" in line
            ]
            assert not lines, lines
        for forbidden in (".failed", ".errors", "db_residue", "matrix_total"):
            assert forbidden not in source, forbidden


# ============================================================
# 6 ~ 8：Baseline 刷新 / 隐藏依赖 / secrets
# ============================================================

class TestStaticBoundaries:
    def test_6_no_baseline_refresh(self) -> None:
        source = _executable_source()

        assert "MATRIX_" + "EXECUTION_BASELINE" in source      # 只读引用存在
        for forbidden in (
            "json.dump",
            ".write(",
            "save(",
            "dump(",
            "overwrite",
            "MATRIX_" + "EXECUTION_BASELINE =",
            "refresh",
            "update_baseline",
        ):
            assert forbidden not in source, forbidden

    def test_7_no_hidden_dependencies(self) -> None:
        tree = ast.parse(_SCRIPT_PATH.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)

        for forbidden in (
            "openai",
            "deepseek",
            "sqlalchemy",
            "psycopg",
            "redis",
            "kafka",
            "httpx",
            "requests",
        ):
            assert not any(
                module.startswith(forbidden) for module in modules
            ), (forbidden, sorted(modules))

    def test_8_no_secrets_in_executable_source(self) -> None:
        source = _executable_source()

        for forbidden in ("api_key", "authorization", "password", "database_url"):
            assert forbidden not in source, forbidden

    def test_9_determinism(self) -> None:
        module = _load_cli_module()
        module._gate_result = lambda: _fake_result("PASS", ("NO_BASELINE_DRIFT",))

        assert [module.main() for _ in range(5)] == [0, 0, 0, 0, 0]

        module._gate_result = lambda: _fake_result("DRIFT", ("DB_RESIDUE_DRIFT",))

        assert [module.main() for _ in range(5)] == [1, 1, 1, 1, 1]


# ============================================================
# 10：CLI 参数契约 + 输出契约 + Matrix 注册
# ============================================================

class TestCliContract:
    def test_10_no_arguments(self) -> None:
        source = _SCRIPT_PATH.read_text(encoding="utf-8")
        module = _load_cli_module()

        for forbidden in (
            "argparse",
            "click",
            "typer",
            "sys.argv",
            "--refresh",
            "--update-baseline",
            "--db",
            "--project",
            "--environment",
        ):
            assert forbidden not in source, forbidden
        assert callable(module.main)

    def test_10b_output_contract(self, capsys: pytest.CaptureFixture[str]) -> None:
        module = _load_cli_module()
        module._gate_result = lambda: _fake_result("DRIFT", ("DB_RESIDUE_DRIFT",))
        module.main()
        out = capsys.readouterr().out

        assert out.startswith("Matrix Gate")
        for forbidden in (
            "MatrixExecutionBaseline(",
            "RegressionExecutionSummary(",
            "duration_seconds",
            "current=",
            "baseline=",
            "postgresql://",
        ):
            assert forbidden not in out, forbidden

    def test_10c_cli_is_registered_in_matrix_contract(self) -> None:
        assert CI_GATE_CLI == "scripts/run_matrix_gate.py"
        assert (_REPO_ROOT / CI_GATE_CLI).is_file()

    def test_10d_db_unavailable_is_fail_closed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """DB 不可用时**不**伪造 PASS：返回 1 且不输出任何 Status。"""
        module = _load_cli_module()
        module._gate_result = lambda: None

        assert module.main() == 1
        out = capsys.readouterr().out
        assert "Status:" not in out
        assert "not evaluated" in out
