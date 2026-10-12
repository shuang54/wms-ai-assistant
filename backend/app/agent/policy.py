"""AgentRuntimePolicy —— Agent Runtime 预算上限（Phase 5 Decision §6 / OD-P5-05）。

不变量（trusted control plane）：
    * Policy 由**服务端**构造（frozen）；LLM 输出 / Conversation Context /
      Tool Result **都不能**修改它（未知字段一律忽略，不解析、不应用）；
    * **缺省不得为无限**：五个字段全部必填且必须显式合法
      （``max_steps`` / ``max_tool_calls`` / ``max_llm_calls`` 有 MVP 默认 8；
      ``max_execution_time_ms`` / ``max_cost_tokens`` **无默认值** ——
      必须由调用方显式给出有限预算，避免"忘记配置 = 无限预算"）；
    * 任何非法值（None / NaN / Infinity / float / negative / 零超时）→
      :class:`AgentPolicyError`（fail-closed：不允许启动 Run）。

成本口径（Phase 5 Decision §6.3 冻结）：
    ``max_cost_tokens`` = **token 预算**（不是金额）；不引入 price / currency。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from backend.app.agent.errors import AgentPolicyError

__all__ = [
    "AgentRuntimePolicy",
    "AGENT_DEFAULT_MAX_STEPS",
    "AGENT_DEFAULT_MAX_TOOL_CALLS",
    "AGENT_DEFAULT_MAX_LLM_CALLS",
    "AGENT_DEFAULT_MAX_EXECUTION_TIME_MS",
    "AGENT_DEFAULT_MAX_COST_TOKENS",
]

#: MVP 默认（Phase 5 Decision §6.2：steps = tool_calls = llm_calls = 8）。
AGENT_DEFAULT_MAX_STEPS: Final[int] = 8
AGENT_DEFAULT_MAX_TOOL_CALLS: Final[int] = 8
AGENT_DEFAULT_MAX_LLM_CALLS: Final[int] = 8

#: Composition Root 默认墙钟 / token 预算（Phase 5 Integration §29.11）：
#: 这两个字段在 ``AgentRuntimePolicy`` 中**无默认值**（必须显式给出），
#: 生产装配（per-project factory）使用本常量；测试 / 评估可显式覆盖。
#: 值 = AgentRuntime 文档示例（30s / 50k tokens），**不改变** 8/8/8 默认。
AGENT_DEFAULT_MAX_EXECUTION_TIME_MS: Final[int] = 30_000
AGENT_DEFAULT_MAX_COST_TOKENS: Final[int] = 50_000


def _validated_int(name: str, value: object) -> int:
    """严格校验 int 预算（拒绝 bool / float / None / str / NaN / Infinity）。

    ``bool`` 是 ``int`` 子类，必须先排除；``float``（含 NaN / inf）一律拒绝，
    避免"看似合法的小数预算"绕过整数比较。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgentPolicyError(
            f"{name} 必须是 int（当前: {type(value).__name__}）"
            "—— 不接受 None / float / NaN / Infinity"
        )
    return value


@dataclass(frozen=True)
class AgentRuntimePolicy:
    """Agent Runtime 预算上限（frozen；构造即校验）。

    Attributes:
        max_execution_time_ms: 墙钟预算（毫秒；必须 > 0）。Runtime 使用
            **注入的 monotonic clock** 度量，不使用 wall clock。
        max_cost_tokens:       LLM token 预算（必须 > 0；不是金额）。
        max_steps:             LLM 决策步数上限（必须 > 0；默认 8）。
        max_tool_calls:        Tool 执行次数上限（必须 >= 0；默认 8；
                               0 = 禁止任何 Tool 执行）。
        max_llm_calls:         LLM 调用次数上限（必须 > 0；默认 8）。

    Raises:
        AgentPolicyError: 任一字段非法（类型 / 范围）。
    """

    max_execution_time_ms: int
    max_cost_tokens: int
    max_steps: int = AGENT_DEFAULT_MAX_STEPS
    max_tool_calls: int = AGENT_DEFAULT_MAX_TOOL_CALLS
    max_llm_calls: int = AGENT_DEFAULT_MAX_LLM_CALLS

    def __post_init__(self) -> None:
        max_execution_time_ms = _validated_int(
            "max_execution_time_ms", self.max_execution_time_ms
        )
        max_cost_tokens = _validated_int("max_cost_tokens", self.max_cost_tokens)
        max_steps = _validated_int("max_steps", self.max_steps)
        max_tool_calls = _validated_int("max_tool_calls", self.max_tool_calls)
        max_llm_calls = _validated_int("max_llm_calls", self.max_llm_calls)

        if max_execution_time_ms <= 0:
            raise AgentPolicyError(
                f"max_execution_time_ms 必须 > 0（当前: {max_execution_time_ms}）"
            )
        if max_cost_tokens <= 0:
            raise AgentPolicyError(
                f"max_cost_tokens 必须 > 0（当前: {max_cost_tokens}）"
            )
        if max_steps <= 0:
            raise AgentPolicyError(f"max_steps 必须 > 0（当前: {max_steps}）")
        if max_tool_calls < 0:
            raise AgentPolicyError(
                f"max_tool_calls 必须 >= 0（当前: {max_tool_calls}）"
            )
        if max_llm_calls <= 0:
            raise AgentPolicyError(
                f"max_llm_calls 必须 > 0（当前: {max_llm_calls}）"
            )
