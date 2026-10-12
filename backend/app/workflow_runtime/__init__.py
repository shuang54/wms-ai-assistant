"""Workflow Runtime 包（Phase 6 Step 3；ADR-01 ACCEPTED）。

结构（S 轮递增）：
```text
S1  ports.py      —— 注入面 Protocol + 跨边界 DTO（**本步**；零 IO / 零实现）
S2  binding.py    —— 受限引用求值（占位符 / condition / arguments_map；纯函数）
S3  execution.py  —— 4 类节点执行器（ai / tool / approval / condition）
S4  runtime.py    —— WorkflowRuntime 主类（start / resume / cancel / resolve_approval）
S5  adapters.py / factory.py —— 生产适配与装配（agent_enabled=True 决策 B 落地）
```

冻结边界（本包与域包 `backend.app.workflow` 分离）：
    * runtime 核心只允许 import stdlib + `backend.app.workflow`（域）；
      db / services / tools / agent / api / llm 一律经 ports 注入（D-A）；
    * 一切 run 状态写入均经 CAS；终态不可逆（D-B）；
    * 取消三态（D-C）：CANCELLED / CANCEL_REQUESTED / NOT_CANCELLABLE；
    * attempt create 先于外部执行器调用；唯一约束 = 执行权原子声明（D-D）。
"""
from __future__ import annotations

from backend.app.workflow_runtime.ports import (
    AGENT_STATUS_WAITING_APPROVAL,
    CANCEL_RESULT_CANCELLED,
    CANCEL_RESULT_NOT_CANCELLABLE,
    CANCEL_RESULT_REQUESTED,
    CANCEL_RESULTS,
    NEXT_ACTION_NONE,
    RUN_CONFLICT_REASONS,
    RUN_CONFLICT_REASON_ALREADY_REQUESTED,
    RUN_CONFLICT_REASON_TERMINAL,
    RUN_CONFLICT_REASON_UNKNOWN,
    RUN_CONFLICT_REASON_VERSION,
    ApprovalIdFactory,
    ApprovalRecord,
    CancelOutcome,
    Clock,
    NodeRunAlreadyExistsError,
    NodeRunConflictError,
    NodeRunNotFoundError,
    NodeRunRecord,
    OrchestrationOutcome,
    OrchestratorPort,
    OrchestratorProvider,
    RequestIdFactory,
    RetryableClassifier,
    RunConflictError,
    RunNotFoundError,
    RunRecord,
    ToolCallContext,
    ToolExecutionOutcome,
    ToolExecutionPort,
    ToolExecutionProvider,
    WorkflowApprovalStore,
    WorkflowNodeRunStore,
    WorkflowPortsError,
    WorkflowRunStore,
)

__all__ = [
    # 常量
    "CANCEL_RESULT_CANCELLED",
    "CANCEL_RESULT_REQUESTED",
    "CANCEL_RESULT_NOT_CANCELLABLE",
    "CANCEL_RESULTS",
    "AGENT_STATUS_WAITING_APPROVAL",
    "NEXT_ACTION_NONE",
    "RUN_CONFLICT_REASON_VERSION",
    "RUN_CONFLICT_REASON_TERMINAL",
    "RUN_CONFLICT_REASON_ALREADY_REQUESTED",
    "RUN_CONFLICT_REASON_UNKNOWN",
    "RUN_CONFLICT_REASONS",
    # DTO
    "RunRecord",
    "NodeRunRecord",
    "ApprovalRecord",
    "OrchestrationOutcome",
    "ToolCallContext",
    "ToolExecutionOutcome",
    "CancelOutcome",
    # 异常
    "WorkflowPortsError",
    "RunNotFoundError",
    "RunConflictError",
    "NodeRunAlreadyExistsError",
    "NodeRunNotFoundError",
    "NodeRunConflictError",
    # 工厂类型
    "Clock",
    "RequestIdFactory",
    "ApprovalIdFactory",
    "RetryableClassifier",
    "OrchestratorProvider",
    "ToolExecutionProvider",
    # Protocol
    "WorkflowRunStore",
    "WorkflowNodeRunStore",
    "WorkflowApprovalStore",
    "OrchestratorPort",
    "ToolExecutionPort",
]
