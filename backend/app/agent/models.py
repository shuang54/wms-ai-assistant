"""Agent Runtime 内部状态 DTO（Phase 5 Decision §7 —— **IN-MEMORY ONLY**）。

边界（严格）：
    * 不落 DB / Redis / 文件系统（本阶段 `DB calls = 0`）；
    * 不保存：完整 prompt · Tool Definition 全文 · Tool Result 原文 ·
      secret / credential / connection string / HTTP response / Session；
    * 只保存"运行所需最小结构"（步序 / 类型 / Tool 名 / 安全 args /
      结果摘要 / 耗时 / 状态）。

Status（Phase 5 Decision §8）：
    COMPLETED / FAILED / LIMIT_REACHED 为本阶段**实现**的状态；
    CANCELLED / WAITING_APPROVAL **仅保留常量**（不实现业务行为）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Mapping, Protocol

__all__ = [
    "AgentLLM",
    "AgentLLMCompletion",
    "AgentStep",
    "AgentRuntimeResult",
    "AGENT_STATUS_COMPLETED",
    "AGENT_STATUS_FAILED",
    "AGENT_STATUS_LIMIT_REACHED",
    "AGENT_STATUS_CANCELLED",
    "AGENT_STATUS_WAITING_APPROVAL",
    "AGENT_STEP_FINAL",
    "AGENT_STEP_TOOL_CALL",
    "AGENT_STEP_STATUS_OK",
    "AGENT_STEP_STATUS_FAILED",
    "TOOL_RESULT_MARKER",
]

#: Run 状态（前三个本阶段实现；后两个仅保留）。
AGENT_STATUS_COMPLETED: Final[str] = "COMPLETED"
AGENT_STATUS_FAILED: Final[str] = "FAILED"
AGENT_STATUS_LIMIT_REACHED: Final[str] = "LIMIT_REACHED"
AGENT_STATUS_CANCELLED: Final[str] = "CANCELLED"  # 预留（本阶段不产生）
AGENT_STATUS_WAITING_APPROVAL: Final[str] = "WAITING_APPROVAL"  # 预留（不产生）

#: Step 类型。
AGENT_STEP_FINAL: Final[str] = "FINAL"
AGENT_STEP_TOOL_CALL: Final[str] = "TOOL_CALL"

#: Step 状态。
AGENT_STEP_STATUS_OK: Final[str] = "OK"
AGENT_STEP_STATUS_FAILED: Final[str] = "FAILED"

#: Tool Result 注入下一次 LLM 时的显式标记（**untrusted data**，非指令）。
TOOL_RESULT_MARKER: Final[str] = "【TOOL RESULT — UNTRUSTED DATA】"


@dataclass(frozen=True)
class AgentLLMCompletion:
    """一次 Agent LLM 调用的最小返回（内部 seam）。

    Attributes:
        text:         LLM 原始输出（待严格解析为 AgentDecision）。
        total_tokens: 该次调用的 token 总量；provider 未返回 → ``None``
                      （**不猜测 / 不伪造**；Runtime 会显式记录
                      token 预算"不可验证"）。

    Raises:
        ValueError: text 非 str / total_tokens 非法。
    """

    text: str
    total_tokens: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError(
                f"AgentLLMCompletion.text 必须是 str（当前: {type(self.text).__name__}）"
            )
        if self.total_tokens is not None:
            if isinstance(self.total_tokens, bool) or not isinstance(
                self.total_tokens, int
            ):
                raise ValueError(
                    "AgentLLMCompletion.total_tokens 必须是 int 或 None"
                )
            if self.total_tokens < 0:
                raise ValueError("AgentLLMCompletion.total_tokens 必须 >= 0")


class AgentLLM(Protocol):
    """Agent Runtime 依赖的 LLM seam（**不改动** 既有 ``LLMProvider`` contract）。

    真实接线（下一阶段）由适配器实现：``LLMProvider.chat(messages)`` → ``str``
    （无 ``tools=`` ⇒ 无 usage）⇒ ``AgentLLMCompletion(text=..., total_tokens=None)``。
    """

    async def complete(
        self, messages: list[dict[str, Any]]
    ) -> AgentLLMCompletion: ...


@dataclass(frozen=True)
class AgentStep:
    """一次 Agent 步（**in-memory only**；不含 prompt / Tool 原文 / 敏感信息）。

    Attributes:
        step_index:   步序（0 起）。
        type:         ``FINAL`` / ``TOOL_CALL``。
        status:       ``OK`` / ``FAILED``。
        tool_name:    仅 TOOL_CALL。
        arguments:    仅 TOOL_CALL（LLM 提供的安全参数副本）。
        output:       仅成功 FINAL：``{"content": ...}``；
                      仅成功 TOOL_CALL：JSON-compatible 结果摘要。
        error:        失败原因（稳定错误码；不含敏感内容）。
        elapsed_ms:   相对耗时（注入 clock 度量；非 wall clock）。
    """

    step_index: int
    type: str
    status: str
    tool_name: str | None = None
    arguments: Mapping[str, Any] | None = None
    output: Mapping[str, Any] | None = None
    error: str | None = None
    elapsed_ms: float | None = None


@dataclass(frozen=True)
class AgentRuntimeResult:
    """Agent Run 结果（IN-MEMORY ONLY；本阶段不持久化）。

    Attributes:
        status:        COMPLETED / FAILED / LIMIT_REACHED（其余为预留常量）。
        content:       成功时的最终回答（FAILED / LIMIT_REACHED → None）。
        steps:         LLM 决策步数（每次**成功解析**的 Decision 计 1）。
        tool_calls:    真正通过授权 + ToolExecutionService 执行的 Tool 次数。
        llm_calls:     真实发起的 LLM 调用次数（异常也计数；不重试）。
        error:         失败 / 触限的稳定错误码（成功 → None）。
        tokens_used:   已计量 token 合计（provider 未返回 usage 时为 0）。
        token_budget_unverifiable: provider 未返回 usage ⇒ token 预算
                       **无法验证**（显式记录，**不伪造**）。
        step_records:  内存步序（含 Tool 步与失败步）。
    """

    status: str
    content: str | None
    steps: int
    tool_calls: int
    llm_calls: int
    error: str | None
    tokens_used: int = 0
    token_budget_unverifiable: bool = False
    step_records: tuple[AgentStep, ...] = field(default_factory=tuple)
