"""Assistant Timeline DTO（Phase 3.12 Step 66 —— 只读**分组投影** Read Model）。

定位（严格基于 Step 65 审计结论）：

    Assistant Trace（四段事实）
        ↓ 只读投影（**不 merge / 不跨组排序 / 不造序列**）
    AssistantTimeline
        ├── llm_events   （LLM Usage；``created_at ASC, id ASC``）
        ├── tool_events  （Tool Execution；``id ASC``）
        ├── rag_events   （RAG Execution；``id ASC``）
        └── outcome_event（Assistant Outcome；0 或 1 条）

**这不是 Unified Timeline**：

    * 没有 ``event_id`` / ``sequence`` / ``span_id`` / ``parent_event_id``
      —— Step 65 已确认这些字段当前**不存在**，禁止伪造；
    * 没有单一全局有序事件流 —— LLM/Outcome 用 **DB 钟**、Tool/RAG 用 **App 钟**，
      跨钟域不可安全比较（``UNSAFE_TO_DERIVE``）；
    * 只做「按来源分组 + 组内真实排序键」：排序键全部来自既有读边界
      （LLM ``created_at, id``；Tool/RAG ``id``），**不是**查询下标。

身份与状态：

    * ``source_id`` = **真实数据库主键**（llm_usage_record.id /
      tool_execution_record.id / rag_execution_record.id /
      assistant_outcome_record.id）—— 禁止生成 / 伪造任何 ID；
    * ``status``：LLM / RAG 当前**没有** success 字段 → ``None``（不推断）；
      Tool 按 ``success`` 映射小写 ``success`` / ``failed``；
      Outcome **原样**映射四态（``SUCCESS`` / ``EMPTY`` / ``REFUSED`` / ``FAILED``）。

时间字段（严格按 Step 65 §十；**不改名**：created_at 绝不写成 finished_at）：

    LLM     created_at ✅ · started_at/finished_at/duration_ms = None
    Tool    started_at/finished_at/duration_ms ✅ · created_at = None
    RAG     started_at/finished_at/duration_ms ✅ · created_at = None
    OUTCOME created_at ✅ · started_at/finished_at/duration_ms = None

安全边界：只允许 metadata / identity / timing / status —— 不含 prompt / messages /
answer / SQL / chunk 正文 / similarity / tool arguments / ToolResult.data /
raw response / API key / authorization / password / database URL /
exception message / stacktrace。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

__all__ = [
    "AssistantTimeline",
    "AssistantTimelineEvent",
    "AssistantTimelineEventStatus",
    "AssistantTimelineEventType",
    "AssistantTimelineSource",
    "ASSISTANT_TIMELINE_EVENT_FIELDS",
    "ASSISTANT_TIMELINE_FIELDS",
    "EVENT_TYPE_BY_SOURCE",
    "SOURCE_BY_EVENT_TYPE",
    "ASSISTANT_REQUEST_ID_MAX_LENGTH",
]

#: Assistant Trace ID 长度上限（与 ORM 列 ``VARCHAR(128)`` / 既有读边界一致）。
ASSISTANT_REQUEST_ID_MAX_LENGTH: Final[int] = 128


class AssistantTimelineEventType(StrEnum):
    """Timeline 事件类型（**只有四个**；无 ROUTER / VALIDATOR / EXECUTOR）。

    为什么只有四个：Step 65 审计确认 Router 决策 / Validator 判定 /
    Executor 执行 / Tool 参数提取**没有任何持久化事件源** —— 没有源就没有事件。
    """

    LLM = "LLM"
    TOOL = "TOOL"
    RAG = "RAG"
    OUTCOME = "OUTCOME"


class AssistantTimelineSource(StrEnum):
    """事件**来源表**（与 ``event_type`` 一一对应；不只用 event_type 表达来源）。"""

    LLM_USAGE = "llm_usage"
    TOOL_EXECUTION = "tool_execution"
    RAG_EXECUTION = "rag_execution"
    ASSISTANT_OUTCOME = "assistant_outcome"


class AssistantTimelineEventStatus(StrEnum):
    """事件状态（**只表达当前真实可得的事实**；无值 → ``None``）。

    大小写刻意不同（Step 66 §十一）：

        * Outcome：``SUCCESS`` / ``EMPTY`` / ``REFUSED`` / ``FAILED``（**原样**映射）；
        * Tool：``success`` / ``failed``（来自 ``tool_execution_record.success`` 布尔）；
        * LLM / RAG：当前**没有** success 字段 ⇒ 事件 ``status = None``（**不推断**）。
    """

    SUCCESS = "SUCCESS"
    EMPTY = "EMPTY"
    REFUSED = "REFUSED"
    FAILED = "FAILED"
    TOOL_SUCCESS = "success"
    TOOL_FAILED = "failed"


#: source ↔ event_type 双向映射（1:1）。
EVENT_TYPE_BY_SOURCE: Final[
    dict[AssistantTimelineSource, AssistantTimelineEventType]
] = {
    AssistantTimelineSource.LLM_USAGE: AssistantTimelineEventType.LLM,
    AssistantTimelineSource.TOOL_EXECUTION: AssistantTimelineEventType.TOOL,
    AssistantTimelineSource.RAG_EXECUTION: AssistantTimelineEventType.RAG,
    AssistantTimelineSource.ASSISTANT_OUTCOME: AssistantTimelineEventType.OUTCOME,
}

SOURCE_BY_EVENT_TYPE: Final[
    dict[AssistantTimelineEventType, AssistantTimelineSource]
] = {
    event_type: source for source, event_type in EVENT_TYPE_BY_SOURCE.items()
}

#: 事件字段白名单（**显式锁定**；任何新增字段都会使既有断言失败）。
ASSISTANT_TIMELINE_EVENT_FIELDS: Final[tuple[str, ...]] = (
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

#: Timeline 字段白名单。
ASSISTANT_TIMELINE_FIELDS: Final[tuple[str, ...]] = (
    "assistant_request_id",
    "llm_events",
    "tool_events",
    "rag_events",
    "outcome_event",
)


def _validate_assistant_request_id(value: object) -> str:
    """校验 Assistant Trace ID（与 Step 38 / Step 64 语义一致）。"""
    if not isinstance(value, str):
        raise ValueError(
            "assistant_request_id 必须是 str"
            f"（当前: {type(value).__name__}）"
        )
    if not value.strip():
        raise ValueError("assistant_request_id 不能为空或纯空白")
    if len(value) > ASSISTANT_REQUEST_ID_MAX_LENGTH:
        raise ValueError(
            "assistant_request_id 超出长度上限 "
            f"（{len(value)} > {ASSISTANT_REQUEST_ID_MAX_LENGTH}）"
        )
    return value


def _validate_optional_datetime(
    value: object,
    *,
    name: str,
) -> datetime | None:
    """可选时间戳：必须是 tz-aware ``datetime`` 或 ``None``。"""
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ValueError(
            f"{name} 必须是 tz-aware datetime | None"
            f"（当前: {type(value).__name__}）"
        )
    if value.tzinfo is None:
        raise ValueError(f"{name} 必须是 tz-aware datetime（当前 naive）")
    return value


@dataclass(frozen=True)
class AssistantTimelineEvent:
    """一条 Timeline 事件（frozen；**只读投影**，不含任何业务内容）。

    Attributes:
        assistant_request_id: 所属 Assistant Request（**隔离键**）。
        source:               来源表（``llm_usage`` / ``tool_execution`` /
                              ``rag_execution`` / ``assistant_outcome``）。
        event_type:           ``LLM`` / ``TOOL`` / ``RAG`` / ``OUTCOME``。
        source_id:            **真实数据库主键**（不生成、不伪造）。
        started_at:           Tool / RAG 开始时间；其余来源 → ``None``。
        finished_at:          Tool / RAG 结束时间；其余来源 → ``None``。
        duration_ms:          Tool / RAG 耗时；其余来源 → ``None``。
        created_at:           LLM / Outcome 的**落库**时间（DB 钟）；
                              Tool / RAG → ``None``。
        status:               Tool → ``success`` / ``failed``；
                              Outcome → ``SUCCESS`` / ``EMPTY`` /
                              ``REFUSED`` / ``FAILED``；
                              LLM / RAG → ``None``（当前无 success 事实）。

    Note:
        * **无** ``event_id`` / ``sequence`` / ``span_id`` / ``parent_event_id``；
        * ``created_at`` 与 ``started_at/finished_at`` **互斥**（钟域不同），
          由 :meth:`__post_init__` 强制（禁止把 created_at 改名成 finished_at）。
    """

    assistant_request_id: str
    source: AssistantTimelineSource
    event_type: AssistantTimelineEventType
    source_id: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = None
    created_at: datetime | None = None
    status: AssistantTimelineEventStatus | None = None

    def __post_init__(self) -> None:
        _validate_assistant_request_id(self.assistant_request_id)

        if not isinstance(self.source, AssistantTimelineSource):
            raise ValueError(
                "source 必须是 AssistantTimelineSource"
                f"（当前: {type(self.source).__name__}）"
            )
        if not isinstance(self.event_type, AssistantTimelineEventType):
            raise ValueError(
                "event_type 必须是 AssistantTimelineEventType"
                f"（当前: {type(self.event_type).__name__}）"
            )
        if EVENT_TYPE_BY_SOURCE[self.source] is not self.event_type:
            raise ValueError(
                f"source({self.source}) 与 event_type({self.event_type}) 不匹配"
            )

        # source_id = **真实主键**：必须是 int（不接受 bool / str / None）
        if isinstance(self.source_id, bool) or not isinstance(
            self.source_id, int
        ):
            raise ValueError(
                "source_id 必须是 int（真实数据库主键；禁止生成 / 伪造）"
                f"（当前: {type(self.source_id).__name__}）"
            )

        if self.status is not None and not isinstance(
            self.status, AssistantTimelineEventStatus
        ):
            raise ValueError(
                "status 必须是 AssistantTimelineEventStatus | None"
                f"（当前: {type(self.status).__name__}）"
            )

        started = _validate_optional_datetime(
            self.started_at, name="started_at"
        )
        finished = _validate_optional_datetime(
            self.finished_at, name="finished_at"
        )
        created = _validate_optional_datetime(
            self.created_at, name="created_at"
        )
        if started is not None and finished is not None:
            if finished < started:
                raise ValueError("finished_at 必须 >= started_at")
        if self.duration_ms is not None:
            if not isinstance(self.duration_ms, (int, float)) or isinstance(
                self.duration_ms, bool
            ):
                raise ValueError(
                    "duration_ms 必须是 float | None"
                    f"（当前: {type(self.duration_ms).__name__}）"
                )
            if self.duration_ms < 0:
                raise ValueError("duration_ms 必须 >= 0")

        # 钟域互斥（Step 65 §五 / Step 66 §十）：App 钟与 DB 钟不得混在同一事件里
        app_clock = (started, finished, self.duration_ms)
        if any(value is not None for value in app_clock) and created is not None:
            raise ValueError(
                "同一事件不得同时携带 App 钟（started/finished/duration）"
                "与 DB 钟（created_at）—— 钟域互斥"
            )


@dataclass(frozen=True)
class AssistantTimeline:
    """一次 Assistant 请求的 Timeline **分组投影**（frozen；只读）。

    Attributes:
        assistant_request_id: 查询键（逐字符一致）。
        llm_events:           LLM 事件（``created_at ASC, id ASC``）。
        tool_events:          Tool 事件（``id ASC``）。
        rag_events:           RAG 事件（``id ASC``）。
        outcome_event:        Assistant 终态事件（0 或 1 条；无终态记录 → ``None``）。

    语义：
        * **四段独立**：不合并成 ``events[]``；不产生全局顺序；
        * 空 Timeline 合法（未知 request_id / 该请求没有任何观测记录）；
        * 组内顺序来自**真实排序键**（不是查询下标、不是 enumerate）；
        * 所有事件的 ``assistant_request_id`` 必须与本 Timeline 一致（隔离不变量）。
    """

    assistant_request_id: str
    llm_events: tuple[AssistantTimelineEvent, ...] = ()
    tool_events: tuple[AssistantTimelineEvent, ...] = ()
    rag_events: tuple[AssistantTimelineEvent, ...] = ()
    outcome_event: AssistantTimelineEvent | None = None

    def __post_init__(self) -> None:
        _validate_assistant_request_id(self.assistant_request_id)

        for name, group, expected in (
            ("llm_events", self.llm_events, AssistantTimelineEventType.LLM),
            ("tool_events", self.tool_events, AssistantTimelineEventType.TOOL),
            ("rag_events", self.rag_events, AssistantTimelineEventType.RAG),
        ):
            if not isinstance(group, tuple):
                raise ValueError(f"{name} 必须是 tuple（read-only snapshot）")
            for event in group:
                if not isinstance(event, AssistantTimelineEvent):
                    raise ValueError(
                        f"{name} 元素必须是 AssistantTimelineEvent"
                        f"（got {type(event).__name__}）"
                    )
                if event.event_type is not expected:
                    raise ValueError(
                        f"{name} 元素 event_type 必须是 {expected}"
                        f"（got {event.event_type}）"
                    )
                if event.assistant_request_id != self.assistant_request_id:
                    raise ValueError(
                        "事件 assistant_request_id 与 Timeline 不一致"
                        "（跨请求污染）"
                    )

        if self.outcome_event is not None:
            if not isinstance(self.outcome_event, AssistantTimelineEvent):
                raise ValueError(
                    "outcome_event 必须是 AssistantTimelineEvent | None"
                    f"（got {type(self.outcome_event).__name__}）"
                )
            if self.outcome_event.event_type is not (
                AssistantTimelineEventType.OUTCOME
            ):
                raise ValueError(
                    "outcome_event.event_type 必须是 OUTCOME"
                    f"（got {self.outcome_event.event_type}）"
                )
            if (
                self.outcome_event.assistant_request_id
                != self.assistant_request_id
            ):
                raise ValueError(
                    "outcome_event 的 assistant_request_id 与 Timeline 不一致"
                )

    def is_empty(self) -> bool:
        """是否为空 Timeline（四段全空；未知 request_id 的合法结果）。"""
        return not (
            self.llm_events
            or self.tool_events
            or self.rag_events
            or self.outcome_event is not None
        )
