"""Agent Runtime 包（Phase 5 — Bounded Agent Runtime Core Loop MVP）。

范围（严格）：
    * ONE Agent · 每轮 0/1 Tool 决策 · BOUNDED · READ-ONLY Tool · 有界授权；
    * IN-MEMORY ONLY（无 DB / 无 Redis / 无文件系统 / 无观测接入）；
    * **不接** AIOrchestrator / Router / Conversation Runtime（后续独立阶段）。

结构：
    errors.py    —— 错误分类 + 稳定错误码
    policy.py    —— AgentRuntimePolicy（frozen；fail-closed 校验）
    decision.py  —— AgentDecision + 严格 JSON 解析（无修复 / 无猜测）
    models.py    —— AgentLLM seam / AgentStep / AgentRuntimeResult（内存 DTO）
    runtime.py   —— AgentRuntime 主循环（授权 → ToolExecutionService → 回灌）

明确不做：Multi-Agent · Planning · Memory · Workflow · LangGraph · MCP ·
Streaming · Human Approval UI · Agent State 持久化。
"""
from __future__ import annotations

from backend.app.agent.decision import (
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentDecision,
    parse_agent_decision,
)
from backend.app.agent.errors import (
    AGENT_ERROR_INVALID_DECISION,
    AGENT_ERROR_LIMIT_REACHED,
    AGENT_ERROR_LLM_FAILURE,
    AGENT_ERROR_TOOL_EXECUTION_FAILURE,
    AGENT_ERROR_TOOL_NOT_ALLOWED,
    AgentDecisionParseError,
    AgentError,
    AgentLLMFailureError,
    AgentPolicyError,
    AgentToolExecutionFailureError,
    AgentToolNotAllowedError,
)
from backend.app.agent.models import (
    AGENT_STATUS_CANCELLED,
    AGENT_STATUS_COMPLETED,
    AGENT_STATUS_FAILED,
    AGENT_STATUS_LIMIT_REACHED,
    AGENT_STATUS_WAITING_APPROVAL,
    TOOL_RESULT_MARKER,
    AgentLLM,
    AgentLLMCompletion,
    AgentRuntimeResult,
    AgentStep,
)
from backend.app.agent.policy import AgentRuntimePolicy
from backend.app.agent.runtime import AGENT_SYSTEM_INSTRUCTION, AgentRuntime

__all__ = [
    "AgentRuntime",
    "AGENT_SYSTEM_INSTRUCTION",
    "AgentRuntimePolicy",
    "AgentRuntimeResult",
    "AgentStep",
    "AgentLLM",
    "AgentLLMCompletion",
    "AgentDecision",
    "parse_agent_decision",
    "DECISION_FINAL",
    "DECISION_TOOL_CALL",
    "TOOL_RESULT_MARKER",
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
    "AGENT_STATUS_COMPLETED",
    "AGENT_STATUS_FAILED",
    "AGENT_STATUS_LIMIT_REACHED",
    "AGENT_STATUS_CANCELLED",
    "AGENT_STATUS_WAITING_APPROVAL",
]
