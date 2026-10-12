"""Agent Runtime 包（Phase 5 — Bounded Agent Runtime Core Loop MVP）。

范围（严格）：
    * ONE Agent · 每轮 0/1 Tool 决策 · BOUNDED · READ-ONLY Tool · 有界授权；
    * IN-MEMORY ONLY（无 DB / 无 Redis / 无文件系统 / 无观测接入）；
    * **不接** AIOrchestrator / Router / Conversation Runtime（后续独立阶段）。

结构：
    errors.py     —— 错误分类 + 稳定错误码
    policy.py     —— AgentRuntimePolicy（frozen；fail-closed 校验）
    decision.py   —— AgentDecision + 严格 JSON 解析（文本 fallback 协议预留）
    models.py     —— AgentLLM 端口 DTO / AgentStep / AgentRuntimeResult（内存 DTO）
    llm.py        —— ProviderAgentLLM 适配器（provider-native function calling）
    runtime.py    —— AgentRuntime 主循环（授权 → ToolExecutionService → 回灌）
    capability.py —— AgentCapability Protocol（AIOrchestrator 可见的唯一端口；
                     Phase 5 Agent Runtime Integration §29.11）

Phase 5 P-7（HITL / 协作式取消）新增：
    approval.py      —— 审批域（PENDING → APPROVED | REJECTED；**不**存 arguments）
    cancellation.py  —— 协作式取消（信号 + 协调器；**不**强杀运行中的 Tool）

明确不做：Multi-Agent · Planning · Memory · Workflow · LangGraph · MCP ·
Streaming · Human Approval UI · Agent Read API（审批/取消只经内部 Service API）。
"""
from __future__ import annotations

from backend.app.agent.capability import AgentCapability
from backend.app.agent.decision import (
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentDecision,
    parse_agent_decision,
)
from backend.app.agent.approval import (
    APPROVAL_DECISIONS,
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
    AgentApprovalRequest,
    AgentApprovalResolution,
    AgentApprovalStore,
    approval_request_of,
    new_approval_id,
)
from backend.app.agent.cancellation import (
    AgentCancellationCoordinator,
    AgentCancellationError,
    AgentCancellationResult,
)
from backend.app.agent.errors import (
    AGENT_ERROR_APPROVAL_CONFLICT,
    AGENT_ERROR_APPROVAL_NOT_FOUND,
    AGENT_ERROR_APPROVAL_REJECTED,
    AGENT_ERROR_APPROVAL_REQUIRED,
    AGENT_ERROR_APPROVAL_STORE_FAILURE,
    AGENT_ERROR_CANCELLED,
    AGENT_ERROR_CANCELLATION_CONFLICT,
    AGENT_ERROR_CANCEL_REQUESTED,
    AGENT_ERROR_CHECKPOINT_DECISION_MISMATCH,
    AGENT_ERROR_CHECKPOINT_NOT_FOUND,
    AGENT_ERROR_CODES,
    AGENT_ERROR_DECISION_NOT_FOUND,
    AGENT_ERROR_DECISION_PERSISTENCE_FAILURE,
    AGENT_ERROR_DECISION_UNAVAILABLE,
    AGENT_ERROR_FINAL_RESULT_UNAVAILABLE,
    AGENT_ERROR_RESUME_CONFLICT,
    AGENT_ERROR_RUN_NOT_CANCELLABLE,
    AGENT_ERROR_RUN_NOT_RESUMABLE,
    AGENT_ERROR_TOOL_AUTHORIZATION_FAILED,
    AGENT_ERROR_TOOL_RESULT_NOT_FOUND,
    AGENT_ERROR_TOOL_RESULT_UNAVAILABLE,
    AGENT_ERROR_FAILED,
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
from backend.app.agent.llm import ProviderAgentLLM
from backend.app.agent.policy import (
    AGENT_DEFAULT_MAX_COST_TOKENS,
    AGENT_DEFAULT_MAX_EXECUTION_TIME_MS,
    AgentRuntimePolicy,
)
from backend.app.agent.resume import AgentResumeResult
from backend.app.agent.runtime import AGENT_SYSTEM_INSTRUCTION, AgentRuntime

__all__ = [
    "AgentRuntime",
    "AgentCapability",
    "AGENT_SYSTEM_INSTRUCTION",
    "ProviderAgentLLM",
    "AgentRuntimePolicy",
    "AGENT_DEFAULT_MAX_EXECUTION_TIME_MS",
    "AGENT_DEFAULT_MAX_COST_TOKENS",
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
    "AGENT_ERROR_FAILED",
    "AGENT_ERROR_CODES",
    "AGENT_ERROR_DECISION_PERSISTENCE_FAILURE",
    "AGENT_ERROR_DECISION_NOT_FOUND",
    "AGENT_ERROR_DECISION_UNAVAILABLE",
    "AGENT_ERROR_CHECKPOINT_NOT_FOUND",
    "AGENT_ERROR_CHECKPOINT_DECISION_MISMATCH",
    "AGENT_ERROR_TOOL_RESULT_NOT_FOUND",
    "AGENT_ERROR_TOOL_RESULT_UNAVAILABLE",
    "AGENT_ERROR_RESUME_CONFLICT",
    "AGENT_ERROR_FINAL_RESULT_UNAVAILABLE",
    "AGENT_STATUS_COMPLETED",
    "AGENT_STATUS_FAILED",
    "AGENT_STATUS_LIMIT_REACHED",
    "AGENT_STATUS_CANCELLED",
    "AGENT_STATUS_WAITING_APPROVAL",
    "AgentResumeResult",
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
    "AgentApprovalStore",
    "AgentApprovalRequest",
    "AgentApprovalResolution",
    "approval_request_of",
    "new_approval_id",
    "APPROVAL_DECISIONS",
    "APPROVAL_STATUS_PENDING",
    "APPROVAL_STATUS_APPROVED",
    "APPROVAL_STATUS_REJECTED",
    "AgentCancellationCoordinator",
    "AgentCancellationResult",
    "AgentCancellationError",
]
