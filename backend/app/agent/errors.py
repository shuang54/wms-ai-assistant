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

#: 全部稳定错误码（供上层白名单校验 / 观测归一化使用；**不含**自由文本）。
AGENT_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        AGENT_ERROR_LLM_FAILURE,
        AGENT_ERROR_INVALID_DECISION,
        AGENT_ERROR_TOOL_NOT_ALLOWED,
        AGENT_ERROR_TOOL_EXECUTION_FAILURE,
        AGENT_ERROR_LIMIT_REACHED,
        AGENT_ERROR_FAILED,
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
