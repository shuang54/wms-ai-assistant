"""Workflow Runtime 领域包（Phase 6 ADR-01 — ACCEPTED）。

范围（严格，对齐 ADR Decision Summary）：
    * **Developer-Defined Deterministic Control Flow**（受限 DAG + 状态机 hybrid）；
    * 节点类型受限：AI / TOOL / APPROVAL / CONDITION（无任意代码执行）；
    * 独立 namespace：``workflow_run`` ≠ ``agent_run``；``node_id`` + ``attempt``
      ≠ ``step_index``（**禁止**复用 agent 语义）；
    * retry **不是** graph edge —— retry = 同一 node_id 的新 attempt（修正 1）。

本步 = Phase 6 Step 1（Domain；纯领域模型）。结构：

```text
errors.py      —— 稳定错误码（WF_ERROR_* 独立白名单）+ 装配/调用期异常
ids.py         —— new_workflow_run_id()（唯一身份生成点）
states.py      —— run / node attempt / approval 三组状态 + 转移表（fail-closed）
policy.py      —— NodeRetryPolicy（immediate only）+ WorkflowRuntimePolicy（有限预算）
models.py      —— JSON 值边界（JsonValue / NodeOutput）+ 身份 + 内存 DTO
definition.py  —— WorkflowDefinition / Node / Condition / Registry（version API）
```

依赖边界（AST 测试锁定）：
    * 本包 **零** import ``db / agent / services / tools / projects / api / llm``；
    * 与 Agent Runtime 双向 0 import（Sibling Runtimes —— 只经 Service API 交互，Step 3）。

明确不做（Phase 6 Deferred / Non-Goals）：
    Parallel · Scheduler · Worker · Queue · Backoff · Write Tool · Compensation ·
    Event Store · Checkpoint 表 · Multi-Agent · LangGraph · MCP · 任意代码节点。
"""
from __future__ import annotations

from backend.app.workflow.definition import (
    CONDITION_OPERATORS,
    NODE_TYPE_AI,
    NODE_TYPE_APPROVAL,
    NODE_TYPE_CONDITION,
    NODE_TYPE_TOOL,
    NODE_TYPES,
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRegistry,
)
from backend.app.workflow.errors import (
    WF_ERROR_APPROVAL_CONFLICT,
    WF_ERROR_APPROVAL_NOT_FOUND,
    WF_ERROR_APPROVAL_REJECTED,
    WF_ERROR_APPROVAL_REQUIRED,
    WF_ERROR_CANCEL_REQUESTED,
    WF_ERROR_CODES,
    WF_ERROR_DEFINITION_VERSION_MISMATCH,
    WF_ERROR_LIMIT_REACHED,
    WF_ERROR_NODE_EXECUTION_FAILURE,
    WF_ERROR_NODE_UNKNOWN,
    WF_ERROR_OUTPUT_UNAVAILABLE,
    WF_ERROR_PERSISTENCE_FAILURE,
    WF_ERROR_RETRY_EXHAUSTED,
    WF_ERROR_RUN_NOT_CANCELLABLE,
    WF_ERROR_RUN_NOT_FOUND,
    WF_ERROR_RUN_NOT_RESUMABLE,
    WF_ERROR_STATE_CONFLICT,
    WorkflowDefinitionAlreadyRegisteredError,
    WorkflowDefinitionError,
    WorkflowDefinitionNotFoundError,
    WorkflowDefinitionVersionMismatchError,
    WorkflowError,
    WorkflowInputError,
    WorkflowOutputError,
    WorkflowPolicyError,
    WorkflowStateError,
)
from backend.app.workflow.ids import new_workflow_run_id
from backend.app.workflow.models import (
    JSON_VALUE_MAX_BYTES,
    JSON_VALUE_MAX_DEPTH,
    JsonScalar,
    JsonValue,
    NodeExecutionIdentity,
    NodeOutput,
    NodeRunOutcome,
    WorkflowApprovalRecord,
    WorkflowRunResult,
    serialize_json_value,
    validate_json_value,
)
from backend.app.workflow.policy import (
    NODE_RETRY_MAX_ATTEMPTS_HARD_LIMIT,
    WORKFLOW_DEFAULT_MAX_NODES_EXECUTED,
    WORKFLOW_DEFAULT_MAX_TOTAL_ATTEMPTS,
    NodeRetryPolicy,
    WorkflowRuntimePolicy,
)
from backend.app.workflow.states import (
    APPROVAL_STATUSES,
    APPROVAL_TERMINAL_STATUSES,
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
    NODE_STATUSES,
    NODE_TERMINAL_STATUSES,
    NODE_STATUS_FAILED,
    NODE_STATUS_RUNNING,
    NODE_STATUS_SUCCEEDED,
    NODE_STATUS_WAITING_APPROVAL,
    WORKFLOW_STATUSES,
    WORKFLOW_TERMINAL_STATUSES,
    WORKFLOW_STATUS_CANCELLED,
    WORKFLOW_STATUS_COMPLETED,
    WORKFLOW_STATUS_FAILED,
    WORKFLOW_STATUS_PENDING,
    WORKFLOW_STATUS_RUNNING,
    WORKFLOW_STATUS_WAITING,
    assert_approval_transition,
    assert_node_transition,
    assert_workflow_transition,
    can_transition_approval,
    can_transition_node,
    can_transition_workflow,
    is_approval_status,
    is_node_status,
    is_terminal_node_status,
    is_terminal_workflow_status,
    is_workflow_status,
)

__all__ = [
    # definition
    "NODE_TYPE_AI",
    "NODE_TYPE_TOOL",
    "NODE_TYPE_APPROVAL",
    "NODE_TYPE_CONDITION",
    "NODE_TYPES",
    "CONDITION_OPERATORS",
    "WorkflowCondition",
    "WorkflowNode",
    "WorkflowDefinition",
    "WorkflowRegistry",
    # errors
    "WorkflowError",
    "WorkflowDefinitionError",
    "WorkflowDefinitionNotFoundError",
    "WorkflowDefinitionAlreadyRegisteredError",
    "WorkflowDefinitionVersionMismatchError",
    "WorkflowPolicyError",
    "WorkflowStateError",
    "WorkflowInputError",
    "WorkflowOutputError",
    "WF_ERROR_RUN_NOT_FOUND",
    "WF_ERROR_RUN_NOT_RESUMABLE",
    "WF_ERROR_RUN_NOT_CANCELLABLE",
    "WF_ERROR_STATE_CONFLICT",
    "WF_ERROR_DEFINITION_VERSION_MISMATCH",
    "WF_ERROR_NODE_EXECUTION_FAILURE",
    "WF_ERROR_NODE_UNKNOWN",
    "WF_ERROR_RETRY_EXHAUSTED",
    "WF_ERROR_APPROVAL_REQUIRED",
    "WF_ERROR_APPROVAL_NOT_FOUND",
    "WF_ERROR_APPROVAL_CONFLICT",
    "WF_ERROR_APPROVAL_REJECTED",
    "WF_ERROR_CANCEL_REQUESTED",
    "WF_ERROR_LIMIT_REACHED",
    "WF_ERROR_OUTPUT_UNAVAILABLE",
    "WF_ERROR_PERSISTENCE_FAILURE",
    "WF_ERROR_CODES",
    # ids
    "new_workflow_run_id",
    # models
    "JSON_VALUE_MAX_DEPTH",
    "JSON_VALUE_MAX_BYTES",
    "JsonScalar",
    "JsonValue",
    "NodeOutput",
    "validate_json_value",
    "serialize_json_value",
    "NodeExecutionIdentity",
    "NodeRunOutcome",
    "WorkflowRunResult",
    "WorkflowApprovalRecord",
    # policy
    "NODE_RETRY_MAX_ATTEMPTS_HARD_LIMIT",
    "WORKFLOW_DEFAULT_MAX_NODES_EXECUTED",
    "WORKFLOW_DEFAULT_MAX_TOTAL_ATTEMPTS",
    "NodeRetryPolicy",
    "WorkflowRuntimePolicy",
    # states
    "WORKFLOW_STATUS_PENDING",
    "WORKFLOW_STATUS_RUNNING",
    "WORKFLOW_STATUS_WAITING",
    "WORKFLOW_STATUS_COMPLETED",
    "WORKFLOW_STATUS_FAILED",
    "WORKFLOW_STATUS_CANCELLED",
    "WORKFLOW_STATUSES",
    "WORKFLOW_TERMINAL_STATUSES",
    "NODE_STATUS_RUNNING",
    "NODE_STATUS_WAITING_APPROVAL",
    "NODE_STATUS_SUCCEEDED",
    "NODE_STATUS_FAILED",
    "NODE_STATUSES",
    "NODE_TERMINAL_STATUSES",
    "APPROVAL_STATUS_PENDING",
    "APPROVAL_STATUS_APPROVED",
    "APPROVAL_STATUS_REJECTED",
    "APPROVAL_STATUSES",
    "APPROVAL_TERMINAL_STATUSES",
    "is_workflow_status",
    "is_terminal_workflow_status",
    "is_node_status",
    "is_terminal_node_status",
    "is_approval_status",
    "can_transition_workflow",
    "can_transition_node",
    "can_transition_approval",
    "assert_workflow_transition",
    "assert_node_transition",
    "assert_approval_transition",
]
