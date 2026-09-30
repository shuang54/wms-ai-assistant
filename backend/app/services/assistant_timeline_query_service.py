"""Assistant Timeline Query Service（Phase 3.12 Step 66）——只读**分组投影**。

职责（只有一件事）：

    同一个 assistant_request_id
        ├── LLMUsageQueryService.list_by_assistant_request_id()     （DB 读边界）
        ├── ToolExecutionPersistentQueryService.list_rows_by_request_id()
        ├── RagExecutionPersistentQueryService.list_by_request_id()
        └── AssistantOutcomeQueryService.get_row_by_assistant_request_id()
                ↓ 显式逐字段映射（**不** merge）
        AssistantTimeline（frozen；四段独立；组内真实排序键）

**这不是 Unified Timeline**（Step 65 已确认 BLOCKED）：

    * 不合并成一个 ``events[]``；
    * 不做跨组排序（LLM/Outcome = DB 钟，Tool/RAG = App 钟 ⇒ 跨钟域
      ``UNSAFE_TO_DERIVE``）；
    * 不生成 ``event_id`` / ``sequence`` / ``span_id`` / ``parent_event_id``
      —— 身份只用**真实数据库主键** ``source_id``；
    * 不补造事件（Router / Validator / Executor **没有**事件源）；
    * 不从 HTTP status / 其它段推断 Outcome。

排序（严格沿用 Step 65 已验证语义）：

    llm_events   ``created_at ASC, id ASC``   （Step 37 读路径排序键）
    tool_events  ``id ASC``                   （落库顺序）
    rag_events   ``id ASC``
    outcome_event 至多 1 条（UNIQUE 键）

边界：

    * 只读：无写入 / 无事务 / 无缓存 / 无聚合；
    * 不接触 SQLAlchemy / Session / Engine / ORM（DB 访问**只**经由既有
      Query Service；本模块不 ``create_engine`` / 不 ``Session()``）；
    * 不新增 HTTP 端点（Step 66 §二十三：只建立 DTO + Service + 测试）；
    * 下游失败**原样透传**（DB 故障 ≠ 空 Timeline）；
    * 隔离：只取入参 request_id 的事件（并对每段做**防御性**同 id 过滤）。

用法（测试 / 未来装配显式构造）：:

    service = AssistantTimelineQueryService(
        llm_usage_query_service=llm_query,
        tool_execution_query_service=tool_query,
        rag_execution_query_service=rag_query,
        outcome_query_service=outcome_query,
    )
    timeline = service.get_timeline("assistant-request-id")
"""
from __future__ import annotations

from datetime import datetime
from typing import Final

from backend.app.dto.assistant_timeline import (
    ASSISTANT_REQUEST_ID_MAX_LENGTH,
    AssistantTimeline,
    AssistantTimelineEvent,
    AssistantTimelineEventStatus,
    AssistantTimelineEventType,
    AssistantTimelineSource,
)
from backend.app.services.assistant_outcome_query_service import (
    AssistantOutcomeQueryService,
)
from backend.app.services.llm_usage_query_service import LLMUsageQueryService
from backend.app.services.rag_execution_persistent_query_service import (
    RagExecutionPersistentQueryService,
)
from backend.app.services.tool_execution_persistent_query_service import (
    ToolExecutionPersistentQueryService,
)

__all__ = [
    "AssistantTimelineQueryService",
    "TIMELINE_EVENT_FIELDS_COUNT",
]

#: 事件字段数（供安全 / 契约断言复用）。
TIMELINE_EVENT_FIELDS_COUNT: Final[int] = 9


def _validate_assistant_request_id(value: object) -> str:
    """校验 Assistant Trace ID（与 Step 38 / Step 64 语义一致）。

    * 必须是 ``str``；strip 后非空；长度 ≤ 128（与 ORM 列一致）；
    * 返回值与入参**逐字符一致**（只校验，不 normalize / 不生成）。
    """
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


class AssistantTimelineQueryService:
    """Assistant Timeline 只读投影服务（**分组**，不合并）。

    Args:
        llm_usage_query_service:      LLM Usage 读边界（``list_by_assistant_request_id``）；
                                      None → 默认 ``LLMUsageQueryService()``。
        tool_execution_query_service: Tool 持久化读边界
                                      （``list_rows_by_request_id``，**行含主键**）；
                                      None → 默认 ``ToolExecutionPersistentQueryService()``。
        rag_execution_query_service:  RAG 持久化读边界（``list_by_request_id``）；
                                      None → 默认 ``RagExecutionPersistentQueryService()``。
        outcome_query_service:        Assistant 终态读边界
                                      （``get_row_by_assistant_request_id``）；
                                      None → 默认 ``AssistantOutcomeQueryService()``。

    Raises:
        TypeError: 注入对象缺少所需方法（fail fast，不 duck-typing 兜底）。

    Note:
        本类**不提供**写入 / 执行 / 合并 / 排序-parameter 能力；
        排序键由本模块固定（Step 65 已验证的组内排序），不接受外部传入。
    """

    def __init__(
        self,
        *,
        llm_usage_query_service: object | None = None,
        tool_execution_query_service: object | None = None,
        rag_execution_query_service: object | None = None,
        outcome_query_service: object | None = None,
    ) -> None:
        self._llm = (
            llm_usage_query_service
            if llm_usage_query_service is not None
            else LLMUsageQueryService()
        )
        self._tools = (
            tool_execution_query_service
            if tool_execution_query_service is not None
            else ToolExecutionPersistentQueryService()
        )
        self._rag = (
            rag_execution_query_service
            if rag_execution_query_service is not None
            else RagExecutionPersistentQueryService()
        )
        self._outcome = (
            outcome_query_service
            if outcome_query_service is not None
            else AssistantOutcomeQueryService()
        )
        self._require(self._llm, "list_by_assistant_request_id", "llm_usage_query_service")
        self._require(self._tools, "list_rows_by_request_id", "tool_execution_query_service")
        self._require(self._rag, "list_by_request_id", "rag_execution_query_service")
        self._require(self._outcome, "get_row_by_assistant_request_id", "outcome_query_service")

    @staticmethod
    def _require(service: object, method: str, label: str) -> None:
        if not callable(getattr(service, method, None)):
            raise TypeError(
                f"{label} 必须提供可调用的 {method}()"
                f"（got {type(service).__name__}）"
            )

    # ---------- 只读暴露（便于测试断言注入关系） ----------

    @property
    def llm_usage_query_service(self) -> object:
        return self._llm

    @property
    def tool_execution_query_service(self) -> object:
        return self._tools

    @property
    def rag_execution_query_service(self) -> object:
        return self._rag

    @property
    def outcome_query_service(self) -> object:
        return self._outcome

    # ---------- 映射（严格按 Step 66 §十 / §十一） ----------

    @staticmethod
    def _llm_event(row: object, *, assistant_request_id: str) -> AssistantTimelineEvent:
        """LLM Usage → 事件（**只有** DB 钟 ``created_at``；status = None）。"""
        return AssistantTimelineEvent(
            assistant_request_id=assistant_request_id,
            source=AssistantTimelineSource.LLM_USAGE,
            event_type=AssistantTimelineEventType.LLM,
            source_id=row.id,                       # 真实主键（不生成）
            started_at=None,
            finished_at=None,
            duration_ms=None,
            created_at=row.created_at,              # DB now()（落库时刻）
            status=None,                            # LLM 表无 success 字段 → 不推断
        )

    @staticmethod
    def _tool_event(row: object, *, assistant_request_id: str) -> AssistantTimelineEvent:
        """Tool Execution → 事件（App 钟三件套；``created_at = None``）。"""
        return AssistantTimelineEvent(
            assistant_request_id=assistant_request_id,
            source=AssistantTimelineSource.TOOL_EXECUTION,
            event_type=AssistantTimelineEventType.TOOL,
            source_id=row.id,
            started_at=row.started_at,
            finished_at=row.finished_at,
            duration_ms=row.duration_ms,
            created_at=None,                        # Tool 表无 created_at
            status=(
                AssistantTimelineEventStatus.TOOL_SUCCESS
                if row.success
                else AssistantTimelineEventStatus.TOOL_FAILED
            ),
        )

    @staticmethod
    def _rag_event(row: object, *, assistant_request_id: str) -> AssistantTimelineEvent:
        """RAG Execution → 事件（App 钟三件套；status = None）。

        RAG 表**没有** success 字段 ⇒ **不推断**成功 / 失败
        （运行期失败可能根本没有 RAG 行 —— 不补造）。
        """
        return AssistantTimelineEvent(
            assistant_request_id=assistant_request_id,
            source=AssistantTimelineSource.RAG_EXECUTION,
            event_type=AssistantTimelineEventType.RAG,
            source_id=row.id,
            started_at=row.started_at,
            finished_at=row.finished_at,
            duration_ms=row.duration_ms,
            created_at=None,
            status=None,
        )

    @staticmethod
    def _outcome_event(
        row: object,
        *,
        assistant_request_id: str,
    ) -> AssistantTimelineEvent:
        """Assistant Outcome → 事件（DB 钟 ``created_at``；status = 终态原样）。"""
        return AssistantTimelineEvent(
            assistant_request_id=assistant_request_id,
            source=AssistantTimelineSource.ASSISTANT_OUTCOME,
            event_type=AssistantTimelineEventType.OUTCOME,
            source_id=row.id,
            started_at=None,
            finished_at=None,
            duration_ms=None,
            created_at=row.created_at,
            status=AssistantTimelineEventStatus(row.outcome),
        )

    # ---------- 查询 ----------

    def get_timeline(self, assistant_request_id: str) -> AssistantTimeline:
        """按 Assistant request_id 组装 Timeline **分组投影**。

        流程::

            validate（先于任何下游调用）
                ↓ 四段各自读取（每段 1 次查询；**不** join / **不** merge）
                ↓ 组内按真实排序键排序（LLM created_at,id；Tool/RAG id）
                ↓ 防御性同 id 过滤（隔离不变量）
            AssistantTimeline

        Args:
            assistant_request_id: 必填、非空（strip 后非空）、≤128 字符。

        Returns:
            ``AssistantTimeline``（frozen；四段 tuple）。
            未知 request_id / 该请求没有任何观测 → **空 Timeline**
            （四段全空 + ``outcome_event=None``），**不是错误**。

        Raises:
            ValueError: 入参非法（先于下游调用）；未知 outcome 值。
            下游 RepositoryError: 原样透传（**绝不**降级为空 Timeline）。
        """
        validated = _validate_assistant_request_id(assistant_request_id)

        llm_rows = [
            row
            for row in self._llm.list_by_assistant_request_id(validated)
            if getattr(row, "assistant_request_id", None) == validated
        ]
        tool_rows = [
            row
            for row in self._tools.list_rows_by_request_id(validated)
            if getattr(row, "request_id", None) == validated
        ]
        rag_rows = [
            row
            for row in self._rag.list_by_request_id(validated)
            if getattr(row, "request_id", None) == validated
        ]
        outcome_row = self._outcome.get_row_by_assistant_request_id(validated)
        if outcome_row is not None and getattr(
            outcome_row, "assistant_request_id", None
        ) != validated:
            outcome_row = None

        def _llm_key(event: AssistantTimelineEvent) -> tuple[datetime, int]:
            assert event.created_at is not None
            return (event.created_at, event.source_id)

        llm_events = tuple(
            sorted(
                (
                    self._llm_event(row, assistant_request_id=validated)
                    for row in llm_rows
                ),
                key=_llm_key,
            )
        )
        tool_events = tuple(
            sorted(
                (
                    self._tool_event(row, assistant_request_id=validated)
                    for row in tool_rows
                ),
                key=lambda event: event.source_id,
            )
        )
        rag_events = tuple(
            sorted(
                (
                    self._rag_event(row, assistant_request_id=validated)
                    for row in rag_rows
                ),
                key=lambda event: event.source_id,
            )
        )
        outcome_event = (
            None
            if outcome_row is None
            else self._outcome_event(outcome_row, assistant_request_id=validated)
        )
        return AssistantTimeline(
            assistant_request_id=validated,
            llm_events=llm_events,
            tool_events=tool_events,
            rag_events=rag_events,
            outcome_event=outcome_event,
        )
