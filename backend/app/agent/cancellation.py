"""Agent Cooperative Cancellation —— 协作式取消（Phase 5 P-7）。

原则（§二十四 / §二十五 / §二十六）：

```text
cancel(run_id)  只设置 **持久化控制状态**：cancel_requested = true
        │
        ▼
Agent Runtime 在**安全边界**检查（LLM 前 / Tool 前 / Tool 返回后）
        │
        ├── 未开始 LLM ⇒ CANCELLED（LLM = 0）
        ├── 未开始 Tool ⇒ CANCELLED（Tool = 0）
        └── Tool 已开始 ⇒ **不强杀**：等待 Tool 返回
                             → 持久化 Tool Result（不丢失）
                             → CANCELLED
```

硬约束：

    * **协作式**，不做 force kill / ``task.cancel`` / ``process.kill`` /
      thread 强杀（§二十六）；运行中的 Tool 一律等它返回；
    * 取消信号 = **Checkpoint 行上的 cancel_requested**（§二十七 / §二十八）：
      与 ``state_version`` 同一 CAS 域 ⇒ 与 Resume / Approval / 终态写入
      天然互斥（§三十六 / §五十四），**不新增** ``agent_cancellation`` 表、
      **不新增** ``cancel_id``（身份 = run_id；§五十三）；
    * ``status`` 枚举**不新增** CANCEL_REQUESTED：它是**信号**（布尔列），
      不是生命周期状态（§二十八）；终态表达为
      ``AgentRun.status = CANCELLED``（§三十四）+
      ``Checkpoint = TERMINAL / CANCELLED``（§三十五）；
    * 终态 Run（COMPLETED / FAILED / LIMIT_REACHED）取消 = **no-op**（§三十八）；
    * 重复取消 = **幂等**（返回当前状态；不再调 LLM / Tool；§三十七）；
    * ``CANCELLED`` 的 Run **禁止** Resume（§三十三；Resume 完成
      ``AGENT_RUN_NOT_RESUMABLE``）。

本模块只承载 **DTO + 协调逻辑 + 纯判定**（无 LLM / 无 Tool / 无 Session）：
持久化经由注入的 Checkpoint 记录器（``get`` / ``request_cancel`` /
``record_terminal``）——同包 ``checkpoint`` 模块保持"状态容器"职责。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final, Protocol

from backend.app.agent.checkpoint import (
    CHECKPOINT_NEXT_ACTION_CANCELLED,
    CHECKPOINT_STATUS_PAUSED,
    CHECKPOINT_STATUS_TERMINAL,
)
from backend.app.agent.errors import (
    AGENT_ERROR_CANCELLED,
    AGENT_ERROR_CANCELLATION_CONFLICT,
    AGENT_ERROR_CANCEL_REQUESTED,
    AGENT_ERROR_RUN_NOT_CANCELLABLE,
)
from backend.app.agent.models import AGENT_STATUS_CANCELLED

logger = logging.getLogger(__name__)

__all__ = [
    "AgentCancellationResult",
    "AgentCancellationError",
    "AgentCancellationCoordinator",
    "cancellation_result_of_checkpoint",
]

#: 取消信号列名（Checkpoint 行上的布尔信号；**不是** status 枚举成员）。
CANCELLATION_SIGNAL_FIELD: Final[str] = "cancel_requested"


class AgentCancellationError(Exception):
    """取消操作的基础设施失败（读取 / 写入不可判定）⇒ 调用方 fail-closed。

    与"业务 no-op"（终态 Run / 不存在）不同：本异常表示**无法确定**取消状态，
    因此**绝不**猜测、**绝不**重试、**绝不**接管。
    """


@dataclass(frozen=True)
class AgentCancellationResult:
    """一次取消请求的结果（frozen；只含非敏感事实）。

    Attributes:
        run_id:             Agent Run 身份（唯一取消身份；无 cancel_id）。
        cancel_requested:   当前持久化信号是否为 true（幂等重复取消 ⇒ True）。
        finalized:          本次调用是否**落地终态**（PAUSED ⇒ TERMINAL / CANCELLED）。
        checkpoint_status:  当前 Checkpoint 状态（ACTIVE / PAUSED / TERMINAL / None）。
        code:               稳定码：``AGENT_CANCEL_REQUESTED``（信号已置，运行中的
                            Agent 将在安全边界停止）· ``AGENT_CANCELLED``（已取消 /
                            本次取消完成）· ``AGENT_RUN_NOT_CANCELLABLE``（终态 no-op /
                            无 Checkpoint）· ``AGENT_CANCELLATION_CONFLICT``（并发竞争
                            无法确定赢家 ⇒ fail-closed）。

    Note:
        **不含** claim_token / 计数 / 自由文本 / 异常消息。
    """

    run_id: str
    cancel_requested: bool
    finalized: bool
    checkpoint_status: str | None
    code: str | None


def cancellation_result_of_checkpoint(
    *,
    run_id: str,
    checkpoint: Any,
    finalized: bool = False,
    code: str | None = None,
) -> AgentCancellationResult:
    """Checkpoint → 取消结果投影（纯函数；只读非敏感字段）。"""
    return AgentCancellationResult(
        run_id=run_id,
        cancel_requested=bool(getattr(checkpoint, "cancel_requested", False)),
        finalized=finalized,
        checkpoint_status=getattr(checkpoint, "status", None),
        code=code,
    )


class AgentCancellationCoordinator:
    """取消请求的**唯一**协调点（Orchestrator 调用；同步、无 await 需求）。

    依赖面（注入；本模块不 import DB / Session）：

        checkpoints.get(run_id)                   -> row | None（读失败抛异常）
        checkpoints.request_cancel(run_id)        -> row（CAS：cancel_requested=false
                                                          → true；state_version + 1）
        checkpoints.record_terminal(run_id=…, agent_status=CANCELLED) -> row | None
                                                  （best-effort；PAUSED 直接终态化）

    边界（§二十三同构）：本类**不**调用 LLM / ToolExecutionService / Resume。
    """

    def __init__(self, *, checkpoints: Any) -> None:
        """构造协调器（checkpoints = Checkpoint 记录器；必须提供 get/request_cancel）。"""
        for method in ("get", "request_cancel"):
            if not callable(getattr(checkpoints, method, None)):
                raise ValueError(
                    f"checkpoints 必须提供可调用的 {method}()"
                    f"（got {type(checkpoints).__name__}）"
                )
        self._checkpoints = checkpoints

    @property
    def checkpoints(self) -> Any:
        """底层 Checkpoint 记录器（只读暴露，便于测试断言注入关系）。"""
        return self._checkpoints

    def cancel(self, run_id: str) -> AgentCancellationResult:
        """请求取消一个 Agent Run（幂等 / no-op / fail-closed；**绝不**强杀）。

        Raises:
            ValueError: run_id 非法。
            AgentCancellationError: 读取失败或无法判定取消状态（fail-closed）。
        """
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空 str")

        checkpoint = self._read(run_id)
        if checkpoint is None:
            # 该 Run 尚未产生控制状态 ⇒ 本阶段无法表达取消（不伪造行）
            return AgentCancellationResult(
                run_id=run_id,
                cancel_requested=False,
                finalized=False,
                checkpoint_status=None,
                code=AGENT_ERROR_RUN_NOT_CANCELLABLE,
            )

        if getattr(checkpoint, "status", None) == CHECKPOINT_STATUS_TERMINAL:
            return self._terminal_result(run_id, checkpoint)

        if bool(getattr(checkpoint, "cancel_requested", False)):
            # 幂等：信号已存在（§三十七）——PAUSED ⇒ 顺手终态化，ACTIVE ⇒ 交给运行中的 Agent
            if getattr(checkpoint, "status", None) == CHECKPOINT_STATUS_PAUSED:
                return self._finalize(run_id, checkpoint)
            return cancellation_result_of_checkpoint(
                run_id=run_id, checkpoint=checkpoint, code=AGENT_ERROR_CANCEL_REQUESTED
            )

        updated = self._request_cancel(run_id, checkpoint)
        if updated is None:
            # CAS 竞争且无法确认取消信号 ⇒ fail-closed（不覆盖、不重试）
            return AgentCancellationResult(
                run_id=run_id,
                cancel_requested=False,
                finalized=False,
                checkpoint_status=getattr(checkpoint, "status", None),
                code=AGENT_ERROR_CANCELLATION_CONFLICT,
            )

        if getattr(updated, "status", None) == CHECKPOINT_STATUS_PAUSED:
            # 暂停中的 Run 没有活着的循环 ⇒ 由本调用直接终态化（Tool = 0）
            return self._finalize(run_id, updated)
        return cancellation_result_of_checkpoint(
            run_id=run_id, checkpoint=updated, code=AGENT_ERROR_CANCEL_REQUESTED
        )

    # ---------- 内部 ----------

    def _read(self, run_id: str) -> Any | None:
        """读取 current checkpoint（失败 ⇒ ``AgentCancellationError`` = fail-closed）。"""
        try:
            return self._checkpoints.get(run_id)
        except Exception as exc:  # noqa: BLE001 —— 取消状态不可判定 ⇒ fail-closed
            raise AgentCancellationError(
                f"无法读取 Agent Run 的取消状态（fail-closed）: {type(exc).__name__}"
            ) from exc

    def _request_cancel(self, run_id: str, checkpoint: Any) -> Any | None:
        """CAS 置位取消信号；竞争 ⇒ ``None``（绝不 force update）。"""
        try:
            return self._checkpoints.request_cancel(run_id=run_id)
        except Exception as exc:  # noqa: BLE001 —— 冲突 / 读失败一律记录后判定
            logger.info(
                "取消信号 CAS 未成功（继续判定当前状态）: %s",
                type(exc).__name__,
                extra={"error_type": type(exc).__name__, "run_id": run_id},
            )
            latest = self._read(run_id)
            if latest is None:
                return None
            if bool(getattr(latest, "cancel_requested", False)):
                return latest
            if getattr(latest, "status", None) == CHECKPOINT_STATUS_TERMINAL:
                # 读回时已终态：交由调用方按 no-op / 已取消处理 ⇒ 用原 checkpoint 语义
                return None
            logger.info(
                "取消与其它控制状态转换竞争（fail-closed）",
                extra={"run_id": run_id, "checkpoint_status": checkpoint.status},
            )
            return None

    def _terminal_result(self, run_id: str, checkpoint: Any) -> AgentCancellationResult:
        """终态 Checkpoint 的取消结果（§三十七 / §三十八：幂等或 no-op）。"""
        already_cancelled = (
            getattr(checkpoint, "next_action", None) == CHECKPOINT_NEXT_ACTION_CANCELLED
        )
        return AgentCancellationResult(
            run_id=run_id,
            cancel_requested=bool(getattr(checkpoint, "cancel_requested", False))
            or already_cancelled,
            finalized=False,
            checkpoint_status=CHECKPOINT_STATUS_TERMINAL,
            code=(
                AGENT_ERROR_CANCELLED
                if already_cancelled
                else AGENT_ERROR_RUN_NOT_CANCELLABLE
            ),
        )

    def _finalize(self, run_id: str, checkpoint: Any) -> AgentCancellationResult:
        """把暂停中的 Run 终态化（TERMINAL / CANCELLED；best-effort 控制状态）。

        Note:
            **不**写 AgentRun 行：该 Run 从未产生终态执行记录，且取消请求
            只携带 run_id（无原请求的 request_id / 计数）——本阶段**不伪造**
            执行记录（Known Limitation；由 Read API 阶段评估补记）。
        """
        terminal = None
        try:
            terminal = self._checkpoints.record_terminal(
                run_id=run_id, agent_status=AGENT_STATUS_CANCELLED
            )
        except Exception as exc:  # noqa: BLE001 —— best-effort（信号已置位；不改变结论）
            logger.warning(
                "取消终态 Checkpoint 写入失败（best-effort）: %s",
                type(exc).__name__,
                extra={"error_type": type(exc).__name__, "run_id": run_id},
            )
        return AgentCancellationResult(
            run_id=run_id,
            cancel_requested=True,
            finalized=terminal is not None,
            checkpoint_status=(
                getattr(terminal, "status", None) or checkpoint.status
            ),
            code=AGENT_ERROR_CANCELLED,
        )
