"""Agent Read Model DTO（Phase 5 P-8 —— Agent Runtime 的**只读**观察面）。

Command / Query 分离（本阶段核心设计原则）：

```text
Command（既有；本阶段**不**改动）
    execute · resume · approve(decide) · cancel
Query（本模块 + AgentReadService + Read API）
    get run · get checkpoint · get approval · get timeline · get status
```

数据来源（**全部**是既有持久化结构；P-8 **不**新增表、**不**改 schema）：

```text
AgentRun          → AgentRunSummary        （run 生命周期状态；唯一 status 来源）
AgentCheckpoint   → AgentCheckpointView    （where to resume；**不是** run status）
AgentApproval     → AgentApprovalView      （控制状态：是否允许执行）
AgentDecision     → AgentDecisionView      （**只**元数据；无 arguments / content）
AgentToolResult   → AgentToolResultView    （**只**元数据；无 payload / ref）
AgentStep         → AgentStepView + Timeline 事件
```

Timeline 是**只读展示模型**（Read Model），不是新的事件系统：

* 不新增 ``agent_timeline_event`` 表 / 不引入 event sourcing；
* 事件只表达"当前持久化数据能够**证明**的事实"；
* 某类事件若当前数据不足以可靠生成 ⇒ **不返回该事件**（绝不伪造）；
* 统一排序键 = ``occurred_at`` + **稳定 tie-breaker**（event rank / step_index /
  event_type）⇒ 同一 Run 的 Timeline 顺序 **deterministic**（不依赖 dict / set /
  DB 自然顺序）。

安全边界（硬约束；任何字段都不得违反）：

```text
禁入 Read Model
    claim_token                    （Checkpoint 控制状态凭据）
    tool arguments / Decision content（Recovery 数据，不是 UI 数据）
    Tool Result payload / ref       （untrusted data；Artifact 能力属未来阶段）
    prompt / system prompt / messages / conversation context / RAG chunks
    SQL / 数据库 URL / API key / Authorization / password
    LLM raw response / exception body / stack trace
```
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final, Mapping

__all__ = [
    "AgentRunSummary",
    "AgentStepView",
    "AgentDecisionView",
    "AgentToolResultView",
    "AgentCheckpointView",
    "AgentApprovalView",
    "AgentRunView",
    "AgentTimelineEvent",
    "AgentTimelineEventType",
    "AGENT_RUN_SUMMARY_FIELDS",
    "AGENT_STEP_VIEW_FIELDS",
    "AGENT_DECISION_VIEW_FIELDS",
    "AGENT_TOOL_RESULT_VIEW_FIELDS",
    "AGENT_CHECKPOINT_VIEW_FIELDS",
    "AGENT_APPROVAL_VIEW_FIELDS",
    "AGENT_TIMELINE_EVENT_FIELDS",
    "AGENT_TIMELINE_EVENT_TYPES",
    "TERMINAL_EVENT_TYPE_BY_STATUS",
    "TIMELINE_EVENT_RANK",
    "AGENT_READ_DEFAULT_LIMIT",
    "AGENT_READ_MAX_LIMIT",
    "AGENT_READ_RUN_ID_MAX_LENGTH",
    "AGENT_READ_PROJECT_ID_MAX_LENGTH",
]

#: Timeline 分页默认 / 上限（server-side cap；禁止 ``limit=1000000``）。
AGENT_READ_DEFAULT_LIMIT: Final[int] = 50
AGENT_READ_MAX_LIMIT: Final[int] = 100

#: 与 ORM 列长度一致（``agent_run.run_id`` / ``project_id``）。
AGENT_READ_RUN_ID_MAX_LENGTH: Final[int] = 64
AGENT_READ_PROJECT_ID_MAX_LENGTH: Final[int] = 128

#: 字段清单（供审计 / 测试；顺序 ≡ 构造顺序）。
AGENT_RUN_SUMMARY_FIELDS: Final[tuple[str, ...]] = (
    "run_id",
    "request_id",
    "project_id",
    "status",
    "error_code",
    "limit_reason",
    "step_count",
    "tool_call_count",
    "llm_call_count",
    "token_usage_unverifiable",
    "started_at",
    "finished_at",
    "duration_ms",
)

AGENT_STEP_VIEW_FIELDS: Final[tuple[str, ...]] = (
    "step_index",
    "step_type",
    "status",
    "tool_name",
    "error_code",
    "duration_ms",
    "created_at",
)

AGENT_DECISION_VIEW_FIELDS: Final[tuple[str, ...]] = (
    "step_index",
    "decision_type",
    "tool_call_id",
    "tool_name",
    "finish_reason",
    "decision_unavailable",
    "created_at",
)

AGENT_TOOL_RESULT_VIEW_FIELDS: Final[tuple[str, ...]] = (
    "step_index",
    "tool_name",
    "success",
    "error_code",
    "result_hash",
    "result_size_bytes",
    "result_truncated",
    "result_unavailable",
    "tool_call_id",
    "created_at",
)

AGENT_CHECKPOINT_VIEW_FIELDS: Final[tuple[str, ...]] = (
    "run_id",
    "state_version",
    "status",
    "next_action",
    "pending_step_index",
    "pending_tool_call_id",
    "pending_decision_ref",
    "pending_tool_result_ref",
    "cancel_requested",
    "cancel_requested_at",
    "updated_at",
)

AGENT_APPROVAL_VIEW_FIELDS: Final[tuple[str, ...]] = (
    "approval_id",
    "run_id",
    "step_index",
    "tool_call_id",
    "tool_name",
    "status",
    "decision",
    "reason_code",
    "created_at",
    "resolved_at",
)

AGENT_TIMELINE_EVENT_FIELDS: Final[tuple[str, ...]] = (
    "sequence",
    "event_type",
    "occurred_at",
    "run_id",
    "step_index",
    "tool_name",
    "tool_call_id",
    "status",
    "error_code",
    "duration_ms",
)


class AgentTimelineEventType(StrEnum):
    """Timeline 事件类型白名单（§十四）。

    ``STEP_STARTED`` 目前**不产生**（见 :func:`AgentReadService` 文档：
    ``agent_step`` 只持久化 ``created_at``（写入时刻）与 ``elapsed_ms``，
    没有可证明的"开始时刻" ⇒ 不伪造事件；类型保留以便未来有真实数据源时启用）。
    """

    RUN_STARTED = "RUN_STARTED"
    STEP_STARTED = "STEP_STARTED"
    DECISION_RECORDED = "DECISION_RECORDED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_RESOLVED = "APPROVAL_RESOLVED"
    TOOL_EXECUTED = "TOOL_EXECUTED"
    STEP_COMPLETED = "STEP_COMPLETED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"
    RUN_CANCELLED = "RUN_CANCELLED"
    RUN_LIMIT_REACHED = "RUN_LIMIT_REACHED"


AGENT_TIMELINE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    event_type.value for event_type in AgentTimelineEventType
)

#: Run 终态 → 终态事件（**逐字**映射；CANCELLED **不**降级成 FAILED）。
TERMINAL_EVENT_TYPE_BY_STATUS: Final[Mapping[str, str]] = {
    "COMPLETED": AgentTimelineEventType.RUN_COMPLETED.value,
    "FAILED": AgentTimelineEventType.RUN_FAILED.value,
    "CANCELLED": AgentTimelineEventType.RUN_CANCELLED.value,
    "LIMIT_REACHED": AgentTimelineEventType.RUN_LIMIT_REACHED.value,
}

#: 相同 ``occurred_at`` 时的**稳定** tie-breaker（因果顺序；不依赖 dict / set）。
TIMELINE_EVENT_RANK: Final[Mapping[str, int]] = {
    AgentTimelineEventType.RUN_STARTED.value: 0,
    AgentTimelineEventType.DECISION_RECORDED.value: 1,
    AgentTimelineEventType.APPROVAL_REQUIRED.value: 2,
    AgentTimelineEventType.APPROVAL_RESOLVED.value: 2,
    AgentTimelineEventType.TOOL_EXECUTED.value: 3,
    AgentTimelineEventType.STEP_COMPLETED.value: 4,
    AgentTimelineEventType.RUN_COMPLETED.value: 9,
    AgentTimelineEventType.RUN_FAILED.value: 9,
    AgentTimelineEventType.RUN_CANCELLED.value: 9,
    AgentTimelineEventType.RUN_LIMIT_REACHED.value: 9,
}


@dataclass(frozen=True)
class AgentRunSummary:
    """Agent Run 的只读摘要（**只**暴露 AgentRun 已有的字段；§七）。

    ``status`` 是 **Run 生命周期状态**（唯一来源 = ``ai_ops.agent_run``）；
    尚无终态记录（例如 HITL 暂停、进行中）时该 Run 视图的 ``status`` 为
    ``None`` —— 绝不用 ``Checkpoint.next_action`` 冒充（§八）。
    """

    run_id: str
    request_id: str
    project_id: str | None
    status: str
    error_code: str | None
    limit_reason: str | None
    step_count: int
    tool_call_count: int
    llm_call_count: int
    token_usage_unverifiable: bool
    started_at: datetime
    finished_at: datetime
    duration_ms: float


@dataclass(frozen=True)
class AgentStepView:
    """Step 元数据视图（无 arguments / output / prompt）。"""

    step_index: int
    step_type: str
    status: str
    tool_name: str | None
    error_code: str | None
    duration_ms: float | None
    created_at: datetime


@dataclass(frozen=True)
class AgentDecisionView:
    """Decision 元数据视图（§十一：**无** arguments / content）。"""

    step_index: int
    decision_type: str
    tool_call_id: str | None
    tool_name: str | None
    finish_reason: str | None
    decision_unavailable: bool
    created_at: datetime


@dataclass(frozen=True)
class AgentToolResultView:
    """Tool Result 元数据视图（§十二：**无** result_payload / result_ref）。"""

    step_index: int
    tool_name: str
    success: bool
    error_code: str | None
    result_hash: str | None
    result_size_bytes: int
    result_truncated: bool
    result_unavailable: bool
    tool_call_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class AgentCheckpointView:
    """Checkpoint 控制状态视图（§九：**无** claim_token）。"""

    run_id: str
    state_version: int
    status: str
    next_action: str
    pending_step_index: int | None
    pending_tool_call_id: str | None
    pending_decision_ref: str | None
    pending_tool_result_ref: str | None
    cancel_requested: bool
    cancel_requested_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True)
class AgentApprovalView:
    """审批视图（§十：身份 / 状态 / 时间；**无** arguments / prompt）。"""

    approval_id: str
    run_id: str
    step_index: int
    tool_call_id: str | None
    tool_name: str
    status: str
    decision: str | None
    reason_code: str | None
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True)
class AgentRunView:
    """一次 Agent Run 的**组合只读视图**（Read API ``GET /agent/runs/{run_id}``）。

    ``run`` 为 ``None`` ⇒ 该 Run 尚无终态执行记录（例如 HITL 暂停中）：
    此时 ``status`` 在 API 层为 ``null``，控制状态只在 ``checkpoint`` 里表达。
    """

    run: AgentRunSummary | None
    checkpoint: AgentCheckpointView | None
    approval: AgentApprovalView | None
    steps: tuple[AgentStepView, ...]
    decisions: tuple[AgentDecisionView, ...]
    tool_results: tuple[AgentToolResultView, ...]


@dataclass(frozen=True)
class AgentTimelineEvent:
    """Timeline 只读事件（§十三 – §十八）。

    ``sequence`` = **投影下标**（排序后 0..n-1），**不是**持久化 event id、
    不表示跨 Run 的顺序、不可用于去重或关联外部实体；
    ``occurred_at`` = 该事实的**持久化时刻**（唯一时间来源列，见字段注释）。
    """

    sequence: int
    event_type: str
    occurred_at: datetime
    run_id: str
    step_index: int | None
    tool_name: str | None
    tool_call_id: str | None
    status: str | None
    error_code: str | None
    duration_ms: float | None


def as_json_safe(value: Any) -> Any:
    """极小 helper：把 ``StrEnum`` 归一为 ``str``（其余类型原样返回）。

    API 层使用 Pydantic 映射；本函数只服务于测试 / 内部断言，
    **不**做序列化框架（不递归、不改写结构）。
    """
    return value.value if isinstance(value, StrEnum) else value
