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
