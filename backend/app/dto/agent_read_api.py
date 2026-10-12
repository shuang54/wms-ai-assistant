"""Agent Read **HTTP API DTO**（Phase 5 P-8）。

为什么单独一层（与既有 ``assistant_timeline_api`` 同风格）：

```text
内部读模型（frozen dataclass：AgentRunView / AgentTimelineEvent）
        ↓ **显式逐字段映射**（api/agent_read.py 的 mapper）
本模块（Pydantic ``response_model``）
        ↓
JSON
```

* 内部 DTO **不直接**作为 FastAPI response model；
* 不使用 ``model_dump()`` / ``dataclasses.asdict()`` 投影；
* ``response_model`` 同时充当**字段过滤边界**：内部 DTO 未来新增字段
  **不会**自动泄漏到 HTTP。

字段边界（§七 – §十二；**硬约束**）：

```text
AgentRunSummary      run_id · request_id · project_id · status · error_code ·
                     limit_reason · step_count · tool_call_count · llm_call_count ·
                     token_usage_unverifiable · started_at · finished_at · duration_ms
                     （无终态执行记录时这些字段为 null；**不**由 Checkpoint 推导）
AgentCheckpointView  state_version · status · next_action · pending_* ·
                     cancel_requested(_at) · updated_at   —— **无** claim_token
AgentApprovalView    approval_id · run_id · step_index · tool_call_id · tool_name ·
                     status · decision · reason_code · created_at · resolved_at
                     —— **无** arguments / prompt / context
AgentDecisionView    step_index · decision_type · tool_call_id · tool_name ·
                     finish_reason · decision_unavailable · created_at
                     —— **无** arguments / content
AgentToolResultView  step_index · tool_name · success · error_code · result_hash ·
                     result_size_bytes · result_truncated · result_unavailable ·
                     tool_call_id · created_at
                     —— **无** result_payload / result_ref
AgentTimelineEvent   sequence · event_type · occurred_at · run_id · step_index ·
                     tool_name · tool_call_id · status · error_code · duration_ms
```
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from pydantic import BaseModel, Field

__all__ = [
    "AgentRunResponse",
    "AgentCheckpointViewResponse",
    "AgentApprovalViewResponse",
    "AgentStepViewResponse",
    "AgentDecisionViewResponse",
    "AgentToolResultViewResponse",
    "AgentTimelineEventResponse",
    "AGENT_RUN_RESPONSE_FIELDS",
    "AGENT_TIMELINE_EVENT_RESPONSE_FIELDS",
]

#: ``GET /api/agent/runs/{run_id}`` 顶层字段（Run 摘要 + 四个视图；顺序 ≡ 声明）。
AGENT_RUN_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
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
    "checkpoint",
    "approval",
    "steps",
    "decisions",
    "tool_results",
)

#: Timeline 事件字段（严格 10 个；**无** event_id / payload）。
AGENT_TIMELINE_EVENT_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
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


class AgentCheckpointViewResponse(BaseModel):
    """Control State 视图（**无** ``claim_token``）。"""

    run_id: str = Field(description="Agent Run 身份")
    state_version: int = Field(description="乐观并发版本（CAS 用）")
    status: str = Field(description="Checkpoint 状态：ACTIVE / PAUSED / TERMINAL")
    next_action: str = Field(description="恢复控制动作（**不是** Run status）")
    pending_step_index: int | None = Field(default=None)
    pending_tool_call_id: str | None = Field(default=None)
    pending_decision_ref: str | None = Field(default=None)
    pending_tool_result_ref: str | None = Field(default=None)
    cancel_requested: bool = Field(description="协作式取消信号（P-7）")
    cancel_requested_at: datetime | None = Field(default=None)
    updated_at: datetime


class AgentApprovalViewResponse(BaseModel):
    """审批视图（控制状态；**无** arguments / prompt）。"""

    approval_id: str
    run_id: str
    step_index: int
    tool_call_id: str | None = Field(default=None)
    tool_name: str
    status: str = Field(description="PENDING / APPROVED / REJECTED")
    decision: str | None = Field(default=None)
    reason_code: str | None = Field(default=None)
    created_at: datetime
    resolved_at: datetime | None = Field(default=None)


class AgentStepViewResponse(BaseModel):
    """Step 元数据（**无** arguments / output）。"""

    step_index: int
    step_type: str = Field(description="FINAL / TOOL_CALL")
    status: str = Field(description="OK / FAILED")
    tool_name: str | None = Field(default=None)
    error_code: str | None = Field(default=None)
    duration_ms: float | None = Field(default=None)
    created_at: datetime


class AgentDecisionViewResponse(BaseModel):
    """Decision 元数据（**无** arguments / content）。"""

    step_index: int
    decision_type: str
    tool_call_id: str | None = Field(default=None)
    tool_name: str | None = Field(default=None)
    finish_reason: str | None = Field(default=None)
    decision_unavailable: bool
    created_at: datetime


class AgentToolResultViewResponse(BaseModel):
    """Tool Result 元数据（**无** payload / ref）。"""

    step_index: int
    tool_name: str
    success: bool
    error_code: str | None = Field(default=None)
    result_hash: str | None = Field(default=None)
    result_size_bytes: int
    result_truncated: bool
    result_unavailable: bool
    tool_call_id: str | None = Field(default=None)
    created_at: datetime


class AgentRunResponse(BaseModel):
    """``GET /api/agent/runs/{run_id}``：Run 摘要 + 组合只读视图。

    ``status`` 等 Run 生命周期字段来自 ``ai_ops.agent_run``；该 Run 尚无终态
    执行记录（HITL 暂停 / 崩溃窗口）时它们为 ``null`` —— **不**用
    ``checkpoint.next_action`` 冒充 Run status。
    """

    run_id: str
    request_id: str | None = Field(default=None)
    project_id: str | None = Field(default=None)
    status: str | None = Field(default=None, description="AgentRun.status（无记录 ⇒ null）")
    error_code: str | None = Field(default=None)
    limit_reason: str | None = Field(default=None)
    step_count: int | None = Field(default=None)
    tool_call_count: int | None = Field(default=None)
    llm_call_count: int | None = Field(default=None)
    token_usage_unverifiable: bool | None = Field(default=None)
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)
    duration_ms: float | None = Field(default=None)
    checkpoint: AgentCheckpointViewResponse | None = Field(default=None)
    approval: AgentApprovalViewResponse | None = Field(
        default=None, description="该 Run 的**最后一条**审批（其余在 timeline 中）"
    )
    steps: list[AgentStepViewResponse] = Field(default_factory=list)
    decisions: list[AgentDecisionViewResponse] = Field(default_factory=list)
    tool_results: list[AgentToolResultViewResponse] = Field(default_factory=list)


class AgentTimelineEventResponse(BaseModel):
    """Timeline 事件（只读展示模型；**不是**持久化 event id）。"""

    sequence: int = Field(
        description=(
            "**投影下标**（全量 deterministic 排序后的 0..n-1）；"
            "不是持久化 event id、不表示跨 Run 顺序、分页不重新编号"
        )
    )
    event_type: str = Field(description="§十四 事件类型白名单")
    occurred_at: datetime = Field(description="事实的持久化时刻（唯一时间来源）")
    run_id: str
    step_index: int | None = Field(default=None)
    tool_name: str | None = Field(default=None)
    tool_call_id: str | None = Field(default=None)
    status: str | None = Field(default=None)
    error_code: str | None = Field(default=None)
    duration_ms: float | None = Field(default=None)
