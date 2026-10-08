"""Agent Resume / Replay 契约（Phase 5 P-6）。

本模块只承载 **Resume 的返回契约 + 纯校验**（无 IO / 无 DB / 无 LLM / 无 Tool）：

```text
resume(run_id)
      │
      ▼
AgentCheckpoint（current recovery control state）
      │
      ├── TERMINAL ⇒ 历史终态（**不**调 LLM / **不**执行 Tool）
      │
      └── ACTIVE
             ├── pending_decision_ref = NULL ⇒ 下一步 = 新的 LLM Decision
             └── pending_decision_ref = "run:step"
                      ↓ AgentDecisionStore（**不**重新调用 LLM）
                   FINAL      ⇒ 返回历史内容
                   TOOL_CALL  ⇒ replay AgentToolResult（**不**重跑 handler）
                                  success ⇒ 继续下一次 LLM
                                  failure ⇒ FAILED
```

硬约束（§十六 / §二十二 / §二十七 / §二十八 / §四十一 / §四十二）：

    * 已完成的 LLM Decision **不重新调用**（Decision Replay）；
    * 已执行并持久化的 Tool **不重新执行**（Tool Result Replay；READ_ONLY 也
      **不**允许盲目重跑）；
    * 状态不确定（Decision / Tool Result 缺失或不可用）⇒ **FAIL CLOSED**；
    * ``run_id`` 保持不变（同一 Agent Run）；``request_id`` 是**新**的请求 correlation；
    * 不新增 Resume 生命周期状态（仍是 ACTIVE / TERMINAL；Resume 是**动作**）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from backend.app.agent.errors import (
    AGENT_ERROR_CHECKPOINT_DECISION_MISMATCH,
)
from backend.app.agent.models import AgentRuntimeResult

__all__ = [
    "AgentResumeResult",
    "decision_mismatch_code",
    "resume_result_of_agent_result",
    "RESUME_STATUSES",
]

#: Resume 结果的 status 取值（**复用** AgentRuntime 语义；不新增生命周期状态）。
RESUME_STATUSES: Final[frozenset[str]] = frozenset(
    {"COMPLETED", "FAILED", "LIMIT_REACHED", "CANCELLED", "WAITING_APPROVAL"}
)


@dataclass(frozen=True)
class AgentResumeResult:
    """一次 Resume 的结果（frozen）。

    * ``status``：``COMPLETED`` / ``FAILED``（``LIMIT_REACHED`` 亦可能出现——
      续跑时触限）；
    * ``content``：历史/新产生的最终内容（无 ⇒ ``None``）；
    * ``error_code``：稳定码（``AGENT_*``；成功 ⇒ ``None``）；
    * ``replayed_step_index``：被 Replay 的 Decision 的 step（无 Replay ⇒ ``None``）；
    * ``llm_calls`` / ``tool_calls``：**本次 Resume 内新增**的真实调用数
      （Replay 不计入；§四十一）；
    * ``steps``：含历史的逻辑步数（预算视角）；
    * ``agent_result``：可供上层写入 Step / Run / 终态 checkpoint 的 Runtime 结果
      （纯 replay 路径为合成结果；不伪造新的 Step / Run / Tool Result）。
    """

    run_id: str
    status: str
    content: str | None
    error_code: str | None
    replayed_step_index: int | None
    llm_calls: int
    tool_calls: int
    steps: int
    agent_result: AgentRuntimeResult
    approval_id: str | None = None
    """Phase 5 P-7：本次 Resume 涉及的审批身份（HITL 路径；否则 ``None``）。"""


def decision_mismatch_code(decision: Any, checkpoint: Any) -> str | None:
    """§十八 一致性校验（纯函数）：Decision 必须与 Checkpoint 指向同一身份。

    Returns:
        ``None``（一致）或 ``AGENT_CHECKPOINT_DECISION_MISMATCH``（不一致 / 缺失字段）。
    """
    decision_run_id = getattr(decision, "run_id", None)
    checkpoint_run_id = getattr(checkpoint, "run_id", None)
    decision_step = getattr(decision, "step_index", None)
    checkpoint_step = getattr(checkpoint, "pending_step_index", None)
    if (
        not isinstance(decision_run_id, str)
        or not isinstance(checkpoint_run_id, str)
        or decision_run_id != checkpoint_run_id
    ):
        return AGENT_ERROR_CHECKPOINT_DECISION_MISMATCH
    if decision_step is None or checkpoint_step is None or decision_step != checkpoint_step:
        return AGENT_ERROR_CHECKPOINT_DECISION_MISMATCH
    return None


def resume_result_of_agent_result(
    *,
    run_id: str,
    agent_result: AgentRuntimeResult,
    replayed_step_index: int | None,
    approval_id: str | None = None,
) -> AgentResumeResult:
    """``AgentRuntimeResult`` → ``AgentResumeResult``（续跑路径；纯映射）。"""
    return AgentResumeResult(
        run_id=run_id,
        status=agent_result.status,
        content=agent_result.content,
        error_code=agent_result.error,
        replayed_step_index=replayed_step_index,
        llm_calls=agent_result.llm_calls,
        tool_calls=agent_result.tool_calls,
        steps=agent_result.steps,
        agent_result=agent_result,
        approval_id=approval_id,
    )
