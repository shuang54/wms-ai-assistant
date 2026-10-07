"""Agent Capability Protocol（Phase 5 — Agent Runtime Integration §29.11）。

职责：定义 **AIOrchestrator 可见的唯一 Agent 端口** —— 一个 ``run()``。

边界（严格，见 Phase 5 Decision §29.11 / §29.15 R-10）：

    * Protocol **不暴露** LLMProvider / AgentLLM / ToolRegistry /
      ToolExecutionService / AgentRuntimePolicy / DB / ConversationRepository；
      这些全部属于 composition root 与 Agent 内部；
    * Orchestrator 只依赖本 Protocol（构造期可选注入），不 new AgentRuntime、
      不读取 .env、不创建 Provider；
    * ``AgentRuntime`` 天然满足本 Protocol（structural typing），无需继承。

依赖方向（禁止反向）：

    AIOrchestrator → AgentCapability（本 Protocol）
    AgentRuntime   →（不得反向依赖）AIOrchestrator / Router / Conversation / DB
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from backend.app.agent.models import AgentRuntimeResult

__all__ = ["AgentCapability"]


@runtime_checkable
class AgentCapability(Protocol):
    """Agent 能力端口（有界多步 Tool Calling 的执行入口）。

    Implementations: ``backend.app.agent.runtime.AgentRuntime``。
    """

    async def run(
        self,
        *,
        question: str,
        conversation_context: str | None = None,
    ) -> AgentRuntimeResult:
        """执行一次有界 Agent Run。

        Args:
            question:             当前问题（非空）。
            conversation_context: 会话上下文（**untrusted reference**；
                Phase 4.2 契约：由 Conversation 层选择后显式传入，
                Agent 只读、不查询 Conversation / 不重新选择窗口）。

        Returns:
            :class:`AgentRuntimeResult`（内存结果；无持久化）。
        """
        ...
