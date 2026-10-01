"""Matrix Baseline Gate → CI Adapter（Phase 3.12 Step 95 · **最小实现**）。

职责（**唯一**）：

    ```text
    MatrixBaselineGateResult
            ↓
    adapt_gate_result_to_exit_code()
            ↓
    process exit code（PASS → 0 · DRIFT → 1）
    ```

边界（Step 94 冻结；本模块不得越过）：

    * **不**执行 Regression Matrix；
    * **不**读取 PostgreSQL / SQLAlchemy / psycopg；
    * **不**重新计算 Baseline / Drift；
    * **不**读取 ``MATRIX_EXECUTION_BASELINE``（Baseline 所有权在 Gate）；
    * **不**调用 ``evaluate_matrix_baseline_gate()`` / ``compare_matrix_execution_baseline()``；
    * **不**刷新 Baseline（DRIFT → 1，绝不 refresh → 0）；
    * **不**调用 LLM / 网络；**不**``sys.exit()``；**不**持久化任何输出；
    * **不**实现 CLI / GitHub Actions（留给未来阶段）。

依赖：``MatrixBaselineGateResult``（结构契约）+ Python stdlib。
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable

__all__ = [
    "CI_ADAPTER_EXIT_CODES",
    "MatrixBaselineGateResultLike",
    "adapt_gate_result_to_exit_code",
]

#: **唯一**退出码映射（Step 94 冻结；本模块持有，测试侧复用同一对象）。
#: 只允许 0 / 1；只读（写入抛 ``TypeError`` ⇒ 无自动新增业务退出码）。
CI_ADAPTER_EXIT_CODES: Mapping[str, int] = MappingProxyType(
    {"PASS": 0, "DRIFT": 1}
)

#: Gate Result 的**结构契约**：Adapter 只关心这两项（且实际只读 ``status``）。
@runtime_checkable
class MatrixBaselineGateResultLike(Protocol):
    """Adapter 接受的最小形状（由 Gate Result 实现；不引入反向依赖）。"""

    status: str
    drifts: tuple[str, ...]


#: Gate Result 的具体类型名（严格输入契约：不接受 Summary / dict / str / tuple …）。
_GATE_RESULT_TYPE_NAME = "MatrixBaselineGateResult"


def adapt_gate_result_to_exit_code(
    result: MatrixBaselineGateResultLike,
) -> int:
    """把 Gate Result 转换为进程 exit code（**纯函数**；无副作用）。

    Args:
        result: ``MatrixBaselineGateResult``（Gate 已判定完毕）。

    Returns:
        ``0``（status == PASS）或 ``1``（status == DRIFT）。

    Raises:
        TypeError: 输入不是 Gate Result（严格类型契约；**不**做任何自动转换）。
        ValueError: status 不在合法域（理论不可达：Gate Result 构造期已保证）。
    """
    if type(result).__name__ != _GATE_RESULT_TYPE_NAME:
        raise TypeError(
            "CI Adapter 只接受 MatrixBaselineGateResult"
            f"（当前: {type(result).__name__}）"
        )
    if not isinstance(result, MatrixBaselineGateResultLike):
        raise TypeError(
            "CI Adapter 输入缺少 status / drifts（不是 Gate Result）"
        )

    status = result.status
    if status not in CI_ADAPTER_EXIT_CODES:
        raise ValueError(f"Unsupported gate status: {status!r}")
    return CI_ADAPTER_EXIT_CODES[status]
