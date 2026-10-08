"""Agent Runtime 错误边界（Phase 5 — Bounded Agent Runtime Core Loop MVP）。

设计（Phase 5 Decision §9 / §18）：
    * 错误**只有分类**，没有自动恢复：
      LLM failure / (decision) parse failure / Tool not allowed /
      Tool execution failure / Runtime limit；
    * 禁止：自动换模型 · 自动重新规划 · 无限 retry；
    * 错误码是**稳定字符串常量**（供 in-memory result / 测试 / 未来观测使用），
      不含 prompt / 参数 / 密钥等敏感内容。

边界：
    本模块**零依赖**（不 import DB / LLM / Tool / Conversation / 观测）。
"""
from __future__ import annotations

from typing import Final

__all__ = [
    "AgentError",
    "AgentPolicyError",
    "AgentDecisionParseError",
    "AgentLLMFailureError",
    "AgentToolNotAllowedError",
    "AgentToolExecutionFailureError",
    "AGENT_ERROR_LLM_FAILURE",
    "AGENT_ERROR_INVALID_DECISION",
    "AGENT_ERROR_TOOL_NOT_ALLOWED",
    "AGENT_ERROR_TOOL_EXECUTION_FAILURE",
    "AGENT_ERROR_LIMIT_REACHED",
    "AGENT_ERROR_FAILED",
    "AGENT_ERROR_APPROVAL_REQUIRED",
    "AGENT_ERROR_APPROVAL_NOT_FOUND",
    "AGENT_ERROR_APPROVAL_CONFLICT",
    "AGENT_ERROR_APPROVAL_REJECTED",
    "AGENT_ERROR_APPROVAL_STORE_FAILURE",
    "AGENT_ERROR_TOOL_AUTHORIZATION_FAILED",
    "AGENT_ERROR_CANCEL_REQUESTED",
    "AGENT_ERROR_CANCELLED",
    "AGENT_ERROR_RUN_NOT_CANCELLABLE",
    "AGENT_ERROR_RUN_NOT_RESUMABLE",
    "AGENT_ERROR_CANCELLATION_CONFLICT",
    "AGENT_ERROR_CODES",
]

#: LLM Provider 调用失败（provider / transport / generation error）。
AGENT_ERROR_LLM_FAILURE: Final[str] = "LLM_FAILURE"

#: LLM 输出无法严格解析为 AgentDecision（非法 JSON / 结构不符）。
AGENT_ERROR_INVALID_DECISION: Final[str] = "INVALID_DECISION"

#: LLM 请求的 Tool 未被授权（Runtime 授权拒绝；Tool 0 次执行）。
AGENT_ERROR_TOOL_NOT_ALLOWED: Final[str] = "TOOL_NOT_ALLOWED"

#: Tool 执行失败（Registry 归一化失败 / ToolExecutionService 抛异常 /
#: Tool Result 不是 JSON-compatible 数据）。
AGENT_ERROR_TOOL_EXECUTION_FAILURE: Final[str] = "TOOL_EXECUTION_FAILURE"

#: 达到 RuntimePolicy 预算上限（**不是**失败）。
AGENT_ERROR_LIMIT_REACHED: Final[str] = "LIMIT_REACHED"

#: 未归类的 Agent 失败（**仅**用于跨层归一化：上层收到的 error 不是
#: 已知稳定码时使用；不用于 Runtime 内部构造）。
AGENT_ERROR_FAILED: Final[str] = "AGENT_FAILED"

# ---- Phase 5 P-6（Decision Store / Resume）稳定码 ----
#: LLM 已给出合法 Decision，但 Decision Store 写入失败 ⇒ **fail closed**
#: （不执行 Tool、不再调 LLM；§十四）。
AGENT_ERROR_DECISION_PERSISTENCE_FAILURE: Final[str] = (
    "AGENT_DECISION_PERSISTENCE_FAILURE"
)
#: Checkpoint 的 pending_decision_ref 指向的 Decision 不存在（§十七）。
AGENT_ERROR_DECISION_NOT_FOUND: Final[str] = "AGENT_DECISION_NOT_FOUND"
#: Decision 存在但**不可安全 Replay**（arguments 超限 / 含敏感标记）⇒ fail closed。
AGENT_ERROR_DECISION_UNAVAILABLE: Final[str] = "AGENT_DECISION_UNAVAILABLE"
#: run_id 没有 current checkpoint ⇒ 无法 Resume（fail closed）。
AGENT_ERROR_CHECKPOINT_NOT_FOUND: Final[str] = "AGENT_CHECKPOINT_NOT_FOUND"
#: Checkpoint 与 Decision 不一致（run_id / step_index 不符；§十八）。
AGENT_ERROR_CHECKPOINT_DECISION_MISMATCH: Final[str] = (
    "AGENT_CHECKPOINT_DECISION_MISMATCH"
)
#: Decision = TOOL_CALL，但 (run_id, step_index) 没有 Tool Result ⇒ fail closed
#: （**绝不**盲目重跑 Tool；§二十三）。
AGENT_ERROR_TOOL_RESULT_NOT_FOUND: Final[str] = "AGENT_TOOL_RESULT_NOT_FOUND"
#: Tool Result 存在但结果体不可 Replay（超限 / 敏感 / 未持久化 payload）。
AGENT_ERROR_TOOL_RESULT_UNAVAILABLE: Final[str] = "AGENT_TOOL_RESULT_UNAVAILABLE"
#: 并发 Resume 冲突（state_version 已被其它 worker 推进；§三十五）。
AGENT_ERROR_RESUME_CONFLICT: Final[str] = "AGENT_RESUME_CONFLICT"
#: 终态 Resume 但最终内容不可恢复（**不**重新调用 LLM；§三十七）。
AGENT_ERROR_FINAL_RESULT_UNAVAILABLE: Final[str] = "AGENT_FINAL_RESULT_UNAVAILABLE"

# ---- Phase 5 P-7（HITL / 协作式取消）稳定码 ----
#: Tool 需要人工审批 ⇒ Run 暂停（WAITING_APPROVAL；**不**写 AgentRun 终态）。
#: 同时用于 Resume 时 Approval 仍为 PENDING 的返回码（幂等暂停）。
AGENT_ERROR_APPROVAL_REQUIRED: Final[str] = "AGENT_APPROVAL_REQUIRED"
#: 该 step 没有 Approval 记录（身份不一致 / 未创建 / 读取失败）⇒ fail closed。
AGENT_ERROR_APPROVAL_NOT_FOUND: Final[str] = "AGENT_APPROVAL_NOT_FOUND"
#: Approval 与 Run / Decision 状态冲突（并发裁决、已终态、身份不符）⇒ fail closed。
AGENT_ERROR_APPROVAL_CONFLICT: Final[str] = "AGENT_APPROVAL_CONFLICT"
#: 人工明确拒绝 ⇒ Agent Run FAILED（**不**重问 LLM / **不**换 Tool / Tool = 0）。
AGENT_ERROR_APPROVAL_REJECTED: Final[str] = "AGENT_APPROVAL_REJECTED"
#: Approval 无法持久化（写失败 / Decision 未 durable）⇒ 不暂停、不执行 Tool。
AGENT_ERROR_APPROVAL_STORE_FAILURE: Final[str] = "AGENT_APPROVAL_STORE_FAILURE"
#: 审批通过后的**再次**授权失败（Approval ≠ Authorization；§二十 / §四十一）。
AGENT_ERROR_TOOL_AUTHORIZATION_FAILED: Final[str] = "AGENT_TOOL_AUTHORIZATION_FAILED"
#: Agent 在安全边界观察到取消请求 ⇒ Run CANCELLED（Tool Result 保留；§二十五 / §三十一）。
AGENT_ERROR_CANCEL_REQUESTED: Final[str] = "AGENT_CANCEL_REQUESTED"
#: 取消请求已生效（幂等重复取消 / 已取消的 Run）。
AGENT_ERROR_CANCELLED: Final[str] = "AGENT_CANCELLED"
#: Run 已终态（COMPLETED / FAILED / LIMIT_REACHED）⇒ 取消为 **no-op**（§三十八）。
AGENT_ERROR_RUN_NOT_CANCELLABLE: Final[str] = "AGENT_RUN_NOT_CANCELLABLE"
#: CANCELLED 的 Run **禁止** Resume（§三十三 / §六十五）。
AGENT_ERROR_RUN_NOT_RESUMABLE: Final[str] = "AGENT_RUN_NOT_RESUMABLE"
#: 取消与其它控制状态转换竞争且无法确定唯一赢家 ⇒ fail closed（§三十六 / §五十四）。
AGENT_ERROR_CANCELLATION_CONFLICT: Final[str] = "AGENT_CANCELLATION_CONFLICT"

#: 全部稳定错误码（供上层白名单校验 / 观测归一化使用；**不含**自由文本）。
AGENT_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        AGENT_ERROR_LLM_FAILURE,
        AGENT_ERROR_INVALID_DECISION,
        AGENT_ERROR_TOOL_NOT_ALLOWED,
        AGENT_ERROR_TOOL_EXECUTION_FAILURE,
        AGENT_ERROR_LIMIT_REACHED,
        AGENT_ERROR_FAILED,
        # Phase 5 P-6
        AGENT_ERROR_DECISION_PERSISTENCE_FAILURE,
        AGENT_ERROR_DECISION_NOT_FOUND,
        AGENT_ERROR_DECISION_UNAVAILABLE,
        AGENT_ERROR_CHECKPOINT_NOT_FOUND,
        AGENT_ERROR_CHECKPOINT_DECISION_MISMATCH,
        AGENT_ERROR_TOOL_RESULT_NOT_FOUND,
        AGENT_ERROR_TOOL_RESULT_UNAVAILABLE,
        AGENT_ERROR_RESUME_CONFLICT,
        AGENT_ERROR_FINAL_RESULT_UNAVAILABLE,
        # Phase 5 P-7
        AGENT_ERROR_APPROVAL_REQUIRED,
        AGENT_ERROR_APPROVAL_NOT_FOUND,
        AGENT_ERROR_APPROVAL_CONFLICT,
        AGENT_ERROR_APPROVAL_REJECTED,
        AGENT_ERROR_APPROVAL_STORE_FAILURE,
        AGENT_ERROR_TOOL_AUTHORIZATION_FAILED,
        AGENT_ERROR_CANCEL_REQUESTED,
        AGENT_ERROR_CANCELLED,
        AGENT_ERROR_RUN_NOT_CANCELLABLE,
        AGENT_ERROR_RUN_NOT_RESUMABLE,
        AGENT_ERROR_CANCELLATION_CONFLICT,
    }
)


class AgentError(Exception):
    """Agent 边界错误基类（本模块只用于构造期 / 解析期错误）。"""


class AgentPolicyError(AgentError, ValueError):
    """RuntimePolicy / Agent 授权配置非法（fail-closed：拒绝启动）。

    与项目既有 DTO 校验风格一致（ValueError 家族；见 ``ToolDefinition`` /
    ``ProjectCapabilities``），便于调用方在装配期立刻失败。
    """


class AgentDecisionParseError(AgentError):
    """LLM 输出不是合法 AgentDecision（**不**猜测 / **不**修复 / **不**重试）。

    消息中**不包含**原始 LLM 输出（可能含敏感内容）。
    """


class AgentLLMFailureError(AgentError):
    """LLM Provider 调用失败（原始异常链保留在 ``__cause__``）。"""


class AgentToolNotAllowedError(AgentError):
    """Tool 未被 Runtime 授权（``TOOL_NOT_ALLOWED``）。

    Attributes:
        tool_name: 被拒绝的 Tool 名称（非敏感）。
    """

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"Tool {tool_name!r} 未被 Agent Runtime 授权")
        self.tool_name = tool_name


class AgentToolExecutionFailureError(AgentError):
    """Tool 执行失败（不 retry / 不 fallback）。

    Attributes:
        tool_name: 失败的 Tool 名称（非敏感）。
    """

    def __init__(self, tool_name: str, reason: str) -> None:
        super().__init__(f"Tool {tool_name!r} 执行失败: {reason}")
        self.tool_name = tool_name
