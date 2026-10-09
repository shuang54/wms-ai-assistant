"""Workflow Runtime 策略（Phase 6 ADR-01 §12 + 原则 P6 Explicit Budget）。

不变量（trusted control plane；对齐 ``agent/policy.py`` 风格）：
    * 策略由**服务器端**构造（frozen）；输入 / 节点输出 / definition 数据
      **都不能**在运行期修改它；
    * **缺省不得为无限**：全部字段显式有限、构造即校验（bool / float / None /
      非正数一律 ``WorkflowPolicyError`` —— fail-closed：不允许启动 Run）。

Retry 口径（ADR §12）：
    * Owner = Workflow（节点级）；**immediate only**（无 backoff —— R5）；
    * ``max_attempts`` 含首次执行（1 = 不重试）；
    * 硬上限由本模块强制（防配置失误导致无限放大）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from backend.app.workflow.errors import WorkflowPolicyError

__all__ = [
    "NODE_RETRY_MAX_ATTEMPTS_HARD_LIMIT",
    "NodeRetryPolicy",
    "WORKFLOW_DEFAULT_MAX_NODES_EXECUTED",
    "WORKFLOW_DEFAULT_MAX_TOTAL_ATTEMPTS",
    "WorkflowRuntimePolicy",
]

#: 节点级 attempt 硬上限（runtime 强制；超出配置 ⇒ 构造即拒绝）。
NODE_RETRY_MAX_ATTEMPTS_HARD_LIMIT: Final[int] = 5

#: run 级防御预算默认值（保守有限；可由 definition / 装配显式覆盖）。
WORKFLOW_DEFAULT_MAX_NODES_EXECUTED: Final[int] = 64
WORKFLOW_DEFAULT_MAX_TOTAL_ATTEMPTS: Final[int] = 64


def _validated_positive_int(name: str, value: object) -> int:
    """严格校验正整数（拒绝 bool / float / None / str —— 不静默转换）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkflowPolicyError(
            f"{name} 必须是 int（当前: {type(value).__name__}）"
            "—— 不接受 None / float / bool"
        )
    if value <= 0:
        raise WorkflowPolicyError(f"{name} 必须 > 0（当前: {value}）")
    return value


@dataclass(frozen=True)
class NodeRetryPolicy:
    """节点级重试策略（frozen；immediate only，无 backoff —— ADR §12 R5）。

    Attributes:
        max_attempts: 含首次执行的最大尝试次数（必须 1..HARD_LIMIT；
                      1 = 不重试）。fail-before-retry 纪律见 ADR §12.3。

    Raises:
        WorkflowPolicyError: 类型 / 范围非法（含超过硬上限）。
    """

    max_attempts: int = 1

    def __post_init__(self) -> None:
        max_attempts = _validated_positive_int(
            "NodeRetryPolicy.max_attempts", self.max_attempts
        )
        if max_attempts > NODE_RETRY_MAX_ATTEMPTS_HARD_LIMIT:
            raise WorkflowPolicyError(
                "NodeRetryPolicy.max_attempts 超过硬上限"
                f"（当前: {max_attempts}；上限: "
                f"{NODE_RETRY_MAX_ATTEMPTS_HARD_LIMIT}）"
            )


@dataclass(frozen=True)
class WorkflowRuntimePolicy:
    """Workflow Run 级预算（frozen；全部显式有限）。

    Attributes:
        max_nodes_executed: run **生命周期累计**的节点执行数上限
            （跨 start / resume 累计；防实现失控与意外放大）。
        max_total_attempts: run 生命周期累计 attempt 上限
            （retry 放大保护；关键预算 —— ADR P6）。

    Raises:
        WorkflowPolicyError: 任一字段非法（类型 / 范围）。
    """

    max_nodes_executed: int = WORKFLOW_DEFAULT_MAX_NODES_EXECUTED
    max_total_attempts: int = WORKFLOW_DEFAULT_MAX_TOTAL_ATTEMPTS

    def __post_init__(self) -> None:
        _validated_positive_int(
            "WorkflowRuntimePolicy.max_nodes_executed", self.max_nodes_executed
        )
        _validated_positive_int(
            "WorkflowRuntimePolicy.max_total_attempts", self.max_total_attempts
        )
