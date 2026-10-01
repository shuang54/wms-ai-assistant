"""Phase 3.12 Step 96 — 本地 Matrix Gate CLI（**最小入口 / orchestration layer**）。

职责（**只编排，不重新实现**）：

```text
Regression Matrix（既有执行）
        ↓
MatrixExecutionSummary                （Step 89）
        ↓
MatrixExecutionBaseline 视图 + residue （Step 89/90）
        ↓
evaluate_matrix_baseline_gate()        （Step 91/92 —— Gate 拥有判断）
        ↓
MatrixBaselineGateResult
        ↓
adapt_gate_result_to_exit_code()       （Step 95 —— Adapter 拥有映射）
        ↓
0 / 1   →   __main__: raise SystemExit(exit_code)
```

禁止（本脚本不得）：

* 重新实现 offline / DB PASS 判断 · matrix_total 计算 · baseline 比较 ·
  drift 分类 · status 判断 · exit code 映射；
* 判断 `current != baseline` / `summary.failed > 0` / `residue != 0`（全部属于 Gate）；
* 刷新 / 写入 / 生成 Baseline（`MATRIX_EXECUTION_BASELINE` **只读**，所有权在 Gate）；
* 输出 current / baseline DTO、凭据、prompt、SQL、RAG chunks、tool args、LLM 输出。

运行：

```powershell
python scripts/run_matrix_gate.py     # PASS → exit 0 · DRIFT → exit 1
```
"""
from __future__ import annotations

import sys
from pathlib import Path

#: 仓库根加入 sys.path（`tests` 是包，Gate / Baseline / Matrix 执行器位于其中）。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _gate_result() -> object | None:
    """执行既有 Regression Matrix → Gate（**全部复用**，不重新判断）。

    Returns:
        ``MatrixBaselineGateResult``；DB 环境不满足时返回 ``None``（不伪造结果）。
    """
    from tests.test_assistant_trace_timeline_regression import (  # noqa: PLC2701
        MATRIX_EXECUTION_BASELINE,
        _current_matrix_baseline,
        evaluate_matrix_baseline_gate,
    )

    current = _current_matrix_baseline()      # Step 89：summary + 只读 residue
    if current is None:
        return None
    return evaluate_matrix_baseline_gate(current, MATRIX_EXECUTION_BASELINE)


def main() -> int:
    """执行 Gate 并输出最小文本；返回 0 / 1（**不**在此处 sys.exit）。"""
    from backend.app.services.matrix_ci_adapter import (  # noqa: PLC2701
        adapt_gate_result_to_exit_code,
    )

    result = _gate_result()
    if result is None:  # DB 环境不满足：不构造新状态、不伪造 PASS
        print("Matrix Gate")
        print("Gate: not evaluated (DB execution unavailable)")
        print("Exit code: 1")
        return 1

    exit_code = adapt_gate_result_to_exit_code(result)
    status = getattr(result, "status")

    print("Matrix Gate")
    print(f"Status: {status}")
    if exit_code != 0:                        # 只使用 Adapter 的映射结果
        print("Drifts:")
        for drift in getattr(result, "drifts", ()):
            print(f"- {drift}")
    print(f"Exit code: {exit_code}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
