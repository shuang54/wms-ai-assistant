"""Assistant Timeline **HTTP API DTO**（Phase 3.12 Step 68）。

为什么单独一层：

    内部读模型 ``AssistantTimeline`` / ``AssistantTimelineEvent``（frozen dataclass）
        ↓ **显式逐字段映射**（api/assistant_timeline.py 的 mapper）
    本模块（Pydantic ``response_model``）
        ↓
    JSON

* 内部 DTO **不直接**作为 FastAPI response model（Step 68 §六）；
* 不使用 ``model_dump()`` / ``dataclasses.asdict()`` 直接投影（§十）；
* ``response_model`` 同时充当字段过滤边界：未来内部 DTO 新增字段**不会**
  自动泄漏到 HTTP（与 ``AssistantTraceResponse`` 既有风格一致）。

事件字段**严格 9 个**（Step 68 §七）：

    assistant_request_id · source · event_type · source_id
    started_at · finished_at · duration_ms · created_at · status

禁止字段：event_id / sequence / span_id / parent_event_id / trace_id /
order / index / attempt —— 一个都没有（Step 65 已确认当前不可得）。

安全边界：只允许 identity / source / timing / status；不含 prompt /
messages / system_prompt / SQL / query / RAG chunk content / embedding /
similarity / tool arguments / tool result / API key / authorization /
password / database URL / raw response / exception message / stack trace。

``source_id`` 语义（Step 68 §八 —— **必须如实文档化**）：

    **内部事件标识**（对应某张运维表的 BIGINT 主键），
    仅用于**同一次 assistant_request_id 内**区分事件；
    **不是** global event id，**不**表示 timeline 顺序，
    **不得**作为跨请求 / 跨系统引用标识。
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from pydantic import BaseModel, Field

__all__ = [
    "AssistantTimelineEventResponse",
    "AssistantTimelineResponse",
    "TIMELINE_EVENT_RESPONSE_FIELDS",
    "TIMELINE_RESPONSE_FIELDS",
    "TIMELINE_SOURCE_VALUES",
    "TIMELINE_EVENT_TYPE_VALUES",
    "TIMELINE_STATUS_VALUES",
]

#: ``source`` 合法取值（对应四张运维表）。
TIMELINE_SOURCE_VALUES: Final[tuple[str, ...]] = (
    "llm_usage",
    "tool_execution",
    "rag_execution",
    "assistant_outcome",
)

#: ``event_type`` 合法取值（只有四个；无 ROUTER / VALIDATOR / EXECUTOR）。
TIMELINE_EVENT_TYPE_VALUES: Final[tuple[str, ...]] = (
    "LLM",
    "TOOL",
    "RAG",
    "OUTCOME",
)

#: ``status`` 合法取值（Outcome 原样四态 + Tool 的 success / failed；
#: LLM / RAG 无 success 事实 → ``null``）。
TIMELINE_STATUS_VALUES: Final[tuple[str, ...]] = (
    "SUCCESS",
    "EMPTY",
    "REFUSED",
    "FAILED",
    "success",
    "failed",
)

#: 事件响应字段白名单（**9 个**；任何新增字段都会使既有断言失败）。
TIMELINE_EVENT_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "assistant_request_id",
    "source",
    "event_type",
    "source_id",
    "started_at",
    "finished_at",
    "duration_ms",
    "created_at",
    "status",
)

#: Timeline 响应字段白名单（**5 个**；不含 merged ``events``）。
TIMELINE_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "assistant_request_id",
    "llm_events",
    "tool_events",
    "rag_events",
    "outcome_event",
)


class AssistantTimelineEventResponse(BaseModel):
    """一条 Timeline 事件的 HTTP 表示（**9 字段**；additive 变更需另开阶段）。

    Attributes:
        assistant_request_id: 所属 Assistant Request（回显；隔离键）。
        source:               来源表标识（见 :data:`TIMELINE_SOURCE_VALUES`）。
        event_type:           事件类型（见 :data:`TIMELINE_EVENT_TYPE_VALUES`）。
        source_id:            **内部事件标识**（对应来源表 BIGINT 主键）。
                              仅用于同一次 assistant_request_id 内区分事件；
                              **不是** global event id、**不**表示顺序、
                              **不得**跨请求 / 跨系统引用。
        started_at:           Tool / RAG 开始时间；其余来源 → ``null``。
        finished_at:          Tool / RAG 结束时间；其余来源 → ``null``。
        duration_ms:          Tool / RAG 耗时毫秒；其余来源 → ``null``。
        created_at:           LLM / Outcome 的**落库**时间（DB 钟）；
                              Tool / RAG → ``null``。
        status:               Tool → ``success`` / ``failed``；
                              Outcome → ``SUCCESS`` / ``EMPTY`` /
                              ``REFUSED`` / ``FAILED``；
                              LLM / RAG → ``null``（当前无 success 事实，
                              **不推断**）。

    Note:
        时间字段语义**不可互换**：``created_at`` 是落库时刻（DB 钟），
        与 ``started_at`` / ``finished_at``（App 钟）属于**不同钟域**；
        本 DTO 不提供跨组排序所需的任何字段（无 sequence / 无全局序）。
    """

    assistant_request_id: str = Field(
        description="所属 Assistant Request 的 Trace ID（= 路径参数）",
    )
    source: str = Field(
        description=(
            "事件来源表标识：llm_usage / tool_execution / rag_execution / "
            "assistant_outcome"
        ),
    )
    event_type: str = Field(
        description="事件类型：LLM / TOOL / RAG / OUTCOME",
    )
    source_id: int = Field(
        description=(
            "**内部事件标识**（来源表 BIGINT 主键）。仅用于同一次 "
            "assistant_request_id 内区分事件；不是 global event id，"
            "不表示 timeline 顺序，不得作为跨请求 / 跨系统引用标识"
        ),
    )
    started_at: datetime | None = Field(
        default=None,
        description="开始时间（仅 Tool / RAG；其余来源为 null）",
    )
    finished_at: datetime | None = Field(
        default=None,
        description="结束时间（仅 Tool / RAG；其余来源为 null）",
    )
    duration_ms: float | None = Field(
        default=None,
        description="耗时毫秒（仅 Tool / RAG；其余来源为 null）",
    )
    created_at: datetime | None = Field(
        default=None,
        description=(
            "落库时间（DB 时钟；仅 LLM / Outcome；Tool / RAG 为 null）。"
            "这是**写入时刻**，不是请求开始时刻；不得与 started_at / "
            "finished_at 互换或比较"
        ),
    )
    status: str | None = Field(
        default=None,
        description=(
            "状态：Tool → success / failed；Outcome → SUCCESS / EMPTY / "
            "REFUSED / FAILED；LLM / RAG → null（无 success 事实，不推断）"
        ),
    )


class AssistantTimelineResponse(BaseModel):
    """``GET /api/observability/assistant-timeline/{assistant_request_id}`` 响应。

    **这是 Grouped Timeline Projection**（Step 65/66/67），**不是** Unified
    Timeline：

        * 四段**独立数组**：``llm_events`` / ``tool_events`` / ``rag_events`` /
          ``outcome_event``（**无** merged ``events``）；
        * 组内有序（LLM ``created_at ASC, id ASC``；Tool / RAG ``id ASC``；
          Outcome 至多 1 条），**组间无全局顺序**；
        * 无 event_id / sequence / span_id / parent_event_id / pagination；
        * 空 Timeline（四段全空）是**合法**结果（未知 request_id → ``200``）。

    Attributes:
        assistant_request_id: 回显请求的 Assistant Trace ID（逐字符一致）。
        llm_events:           LLM 事件（组内 ``created_at, id`` 升序）。
        tool_events:          Tool 事件（组内 ``id`` 升序）。
        rag_events:           RAG 事件（组内 ``id`` 升序）。
        outcome_event:        Assistant 终态事件；无终态记录 → ``null``
                              （**不**从 HTTP status / 其它段推断）。
    """

    assistant_request_id: str = Field(
        description="Assistant Request 的 Trace ID（= 路径参数；逐字符一致）",
    )
    llm_events: list[AssistantTimelineEventResponse] = Field(
        default_factory=list,
        description="LLM 事件（组内 created_at ASC, id ASC）",
    )
    tool_events: list[AssistantTimelineEventResponse] = Field(
        default_factory=list,
        description="Tool 执行事件（组内 id ASC）",
    )
    rag_events: list[AssistantTimelineEventResponse] = Field(
        default_factory=list,
        description="RAG 执行事件（组内 id ASC）",
    )
    outcome_event: AssistantTimelineEventResponse | None = Field(
        default=None,
        description=(
            "Assistant 终态事件（0 或 1 条）；无终态记录（历史请求）→ null"
        ),
    )
