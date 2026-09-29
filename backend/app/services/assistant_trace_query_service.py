"""Assistant Trace Query Service（Phase 3.12 Step 38）——只读 **Read Model 组合**。

职责（只有一件事）：

    同一个 assistant_request_id
        ├── LLMUsageQueryService.list_by_assistant_request_id()   （DB 读边界）
        └── ToolObservabilityQueryService.snapshots_by_request_id()（Runtime 读边界）
                ↓
        AssistantTraceView（frozen；tuple 集合）

    Assistant Trace A
            │
            ├── LLM Usage[]
            │
            └── Tool Execution[]
                    ↓
            AssistantTraceView

**这不是 Trace System**：不新增表 / 不新增 Repository / 不写库 / 不产生 Span、
Trace Parent、事件流；只把两个**已有**只读边界的结果按同一个 trace id 组合。

数据源（Phase 3.12 Step 41：两者均为**持久化**读边界）::

    LLM Usage  → ai_ops.llm_usage_record          （PostgreSQL）
    Tool       → ai_ops.tool_execution_record     （PostgreSQL）

    → 进程重启 / 多 worker / Runtime Collector 已淘汰的记录仍可查询
    （Assistant Trace 是**历史链路**读取，不是"当前进程快照"）

依赖方向（Architecture Boundary，本模块最重要的约束）::

    AssistantTraceQueryService
        ├── LLMUsageQueryService                    （只读 Query Service）
        └── Tool 观测读边界（Persistent；提供 list_by_request_id()）

    ✗ LLMUsageRepository / ToolExecutionRepository    （禁止依赖 Repository）
    ✗ SQLAlchemy Session / Engine / select            （禁止访问 DB）
    ✗ ORM Model                                       （禁止暴露 ORM）
    ✗ ToolExecutionService / AIRouter / RagService    （禁止执行业务逻辑）

不做：

    * 不按 route 猜测内容（**不接受 route 参数**）；Read Model 只读事实；
    * 不重新路由 / 不重新调用 LLM / 不重新执行 Tool / SQL / RAG；
    * 不排序（保留下游 Query Service 的稳定顺序）、不去重、不聚合、不补全；
    * 不把下游异常降级为 ``[]``：数据库不可用时必须抛出既有异常
      （``[]`` 只表示"确实没有记录"）；
    * 不合并 Runtime Tool Records 与 Persistent Tool Records
      （Tool 数据源 = **Persistent** 读边界；Runtime 内存 Collector 只服务
      ``/api/observability/tools``，Persistent History 只服务
      ``/api/observability/tools/history`` —— 两者绝不混用以避免
      duplicate Tool Execution）。

用法（测试 / 未来装配显式构造；本阶段不接 API、不做单例）::

    service = AssistantTraceQueryService(
        tool_observability_query_service=app_level_tool_query_service,
    )
    view = service.get_trace("assistant-request-id")
    view.llm_usage          # tuple[LLMUsageTraceRecordView, ...]
    view.tool_executions    # tuple[ToolExecutionSnapshot, ...]

Tool 读边界必须由调用方注入（**本模块不创建 Collector**：Collector 的
唯一创建点仍是 ``api/orchestrator_chat.py``，Phase 3.11 Step 23/24 契约）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.app.services.assistant_trace import (
    ASSISTANT_REQUEST_ID_MAX_LENGTH,
)
from backend.app.services.llm_usage_query_service import (
    LLMUsageQueryService,
    LLMUsageTraceRecordView,
)
from backend.app.services.rag_execution_persistent_query_service import (
    RagExecutionPersistentQueryService,
)
from backend.app.services.tool_observability_query_service import (
    ToolObservabilityQueryService,
)
from backend.app.services.tool_observability_snapshot import (
    ToolExecutionSnapshot,
)

__all__ = [
    "AssistantTraceView",
    "AssistantTraceQueryService",
    "RagExecutionTraceView",
]

#: RAG Trace 字段（Step 47/48 契约）：Persistent Record 的 13 个契约字段，
#: **不含**数据库主键 ``id``（排序由仓储 ``ORDER BY id ASC`` 保证，
#: 存储主键不暴露给 HTTP 客户端；与 ``ToolExecutionTraceResponse`` 一致）。
RAG_EXECUTION_TRACE_FIELDS: tuple[str, ...] = (
    "request_id",
    "started_at",
    "finished_at",
    "duration_ms",
    "result_count",
    "used_chunks_count",
    "top_k",
    "context_truncated",
    "context_chars",
    "reranker_used",
    "rerank_elapsed_ms",
    "chunk_ids",
    "document_ids",
)


def _validate_assistant_request_id(value: object) -> str:
    """校验 Assistant Trace ID（与 Step 37 语义一致；不生成 / 不截断 / 不改写）。

    * 必须是 ``str``（``None`` → ValueError）；
    * strip 后非空（``""`` / ``"   "`` → ValueError）；
    * 长度 ≤ ``ASSISTANT_REQUEST_ID_MAX_LENGTH``（128，与 ORM 列一致）；
    * 返回值与入参**逐字符一致**（只校验，不 normalize / 不生成 UUID 兜底）。

    为什么抛 ``ValueError``：本服务是**组合层**，不对下游错误体系做二次包装
    （§十四）；输入非法属调用方错误 → ``ValueError``，先于任何下游调用发生。
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


@dataclass(frozen=True)
class RagExecutionTraceView:
    """RAG Trace Read Model（frozen；Step 48；Persistent Record → Trace）。

    Attributes（13 字段；严格等于 ``RAG_EXECUTION_TRACE_FIELDS``）：
        request_id:          Assistant Trace ID（= 查询入参；与 Tool 段同名同义）。
        started_at / finished_at / duration_ms: RAG 执行时间与端到端耗时。
        result_count / used_chunks_count / top_k: 检索与 Context 事实（数值）。
        context_truncated / context_chars: Context 截断与规模。
        reranker_used / rerank_elapsed_ms: Reranker 参与情况与耗时
                                            （未使用 → ``None``，**不是 0**）。
        chunk_ids / document_ids: 命中 identifier（去重 + 首次出现顺序；**仅 ID**）。

    安全边界：
        * **不含**数据库主键 ``id``（排序由仓储保证；不暴露存储细节）；
        * **不含** query / answer / chunk content / document content /
          similarity / embedding / prompt / messages / raw_response / SQL /
          credentials / project_id；
        * 只由 :meth:`from_row` 显式逐字段构造（无 vars / __dict__ / asdict）。
    """

    request_id: str
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    result_count: int
    used_chunks_count: int
    top_k: int
    context_truncated: bool
    context_chars: int
    reranker_used: bool
    rerank_elapsed_ms: float | None
    chunk_ids: tuple[int, ...]
    document_ids: tuple[int, ...]

    @classmethod
    def from_row(cls, row: Any) -> "RagExecutionTraceView":
        """``RagExecutionRecordRow`` → Trace View（**显式逐字段映射**）。

        顺序 / 去重由 DTO 与仓储保证；本方法不排序、不去重、不改写；
        ``rerank_elapsed_ms`` 的 ``None`` 原样保留。
        """
        return cls(
            request_id=row.request_id,
            started_at=row.started_at,
            finished_at=row.finished_at,
            duration_ms=row.duration_ms,
            result_count=row.result_count,
            used_chunks_count=row.used_chunks_count,
            top_k=row.top_k,
            context_truncated=row.context_truncated,
            context_chars=row.context_chars,
            reranker_used=row.reranker_used,
            rerank_elapsed_ms=row.rerank_elapsed_ms,
            chunk_ids=row.chunk_ids,
            document_ids=row.document_ids,
        )


@dataclass(frozen=True)
class AssistantTraceView:
    """一次 Assistant 请求的只读 Trace Read Model（frozen；显式字段）。

    Attributes:
        assistant_request_id: 该 Trace 的 Assistant request_id（= 查询入参，
            逐字符一致；**不是** Provider 请求 ID）。
        llm_usage:            ``tuple[LLMUsageTraceRecordView, ...]``
            （来自 ``LLMUsageQueryService``；顺序 = Step 37 的
            ``created_at ASC, id ASC``，本层不重排）。
        tool_executions:      ``tuple[ToolExecutionSnapshot, ...]``
            （来自 **Persistent** Tool 读边界；顺序 = 落库顺序 ``id ASC``，
            本层不重排）。
        rag_executions:       ``tuple[RagExecutionTraceView, ...]``
            （Phase 3.12 Step 48：来自 **Persistent** RAG 读边界
            ``RagExecutionPersistentQueryService`` → ai_ops.rag_execution_record；
            顺序 = 落库顺序 ``id ASC``，本层不重排）。

    语义：

        * 使用 ``tuple``（read-only snapshot）：调用方无法 append / 就地修改；
        * 字段严格 = 两个既有安全 DTO 的并集：
          LLM（id / assistant_request_id / request_id / provider / model /
          prompt_tokens / completion_tokens / total_tokens / created_at）+
          ToolSnapshot 的 11 个字段；
        * **绝不**携带 prompt / messages / raw_response / tool arguments /
          ToolResult.data / SQL / DB 连接 / Session / API key / password /
          authorization / database URL；
        * 空 Trace 合法（``llm_usage=()`` 且 ``tool_executions=()``）——
          "没有记录"不是错误。
    """

    assistant_request_id: str
    llm_usage: tuple[LLMUsageTraceRecordView, ...]
    tool_executions: tuple[ToolExecutionSnapshot, ...]
    rag_executions: tuple[RagExecutionTraceView, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.assistant_request_id, str):
            raise ValueError(
                "assistant_request_id 必须是 str"
                f"（当前: {type(self.assistant_request_id).__name__}）"
            )
        if not self.assistant_request_id.strip():
            raise ValueError("assistant_request_id 不能为空或纯空白")
        if not isinstance(self.llm_usage, tuple):
            raise ValueError("llm_usage 必须是 tuple（read-only snapshot）")
        if not isinstance(self.tool_executions, tuple):
            raise ValueError(
                "tool_executions 必须是 tuple（read-only snapshot）"
            )
        if not isinstance(self.rag_executions, tuple):
            raise ValueError(
                "rag_executions 必须是 tuple（read-only snapshot）"
            )
        for item in self.rag_executions:
            if not isinstance(item, RagExecutionTraceView):
                raise ValueError(
                    "rag_executions 元素必须是 RagExecutionTraceView"
                    f"（got {type(item).__name__}）"
                )
        for item in self.llm_usage:
            if not isinstance(item, LLMUsageTraceRecordView):
                raise ValueError(
                    "llm_usage 元素必须是 LLMUsageTraceRecordView"
                    f"（got {type(item).__name__}）"
                )
        for item in self.tool_executions:
            if not isinstance(item, ToolExecutionSnapshot):
                raise ValueError(
                    "tool_executions 元素必须是 ToolExecutionSnapshot"
                    f"（got {type(item).__name__}）"
                )


class AssistantTraceQueryService:
    """Assistant Trace 只读组合服务（Read Model Composition）。

    Args:
        tool_observability_query_service: Tool 观测读边界（**必填**）——
            必须提供 ``list_by_request_id(request_id)``。Assistant Trace 固定
            使用 **Persistent** 读边界（``ToolExecutionPersistentQueryService``，
            由 ``api/orchestrator_chat.py`` 装配为模块级单例）；Runtime
            内存边界不参与 Trace。本服务**不创建 Collector**（沿用
            Step 23/24 契约：Collector 唯一创建点 = Composition Root），
            因此没有默认值。
        llm_usage_query_service: LLM Usage 读边界；``None`` → 构造默认
            ``LLMUsageQueryService()``（只创建 Query Service + Repository，
            不创建 Collector / 不访问 Session）。
        rag_execution_query_service: RAG 执行读边界（Phase 3.12 Step 48）；
            ``None`` → 构造默认 ``RagExecutionPersistentQueryService()``
            （**Persistent**：ai_ops.rag_execution_record；构造期不连接数据库）。
            生产装配由 Composition Root 显式注入
            （``get_rag_execution_persistent_query_service()``）。

    Raises:
        TypeError: 注入对象缺少所需方法（fail fast，不做 duck-typing 兜底）。

    Note:
        本类**不提供**任何写入 / 执行 / 路由能力（无 ``execute`` / ``record`` /
        ``clear`` / ``metrics``）；只组合三个 Query Service 的只读结果。
        **绝不**直接访问 SQLAlchemy / rag_execution_record / Runtime Collector。
    """

    def __init__(
        self,
        *,
        tool_observability_query_service: Any,
        llm_usage_query_service: Any = None,
        rag_execution_query_service: Any = None,
    ) -> None:
        self._llm_usage = (
            llm_usage_query_service
            if llm_usage_query_service is not None
            else LLMUsageQueryService()
        )
        if not callable(
            getattr(self._llm_usage, "list_by_assistant_request_id", None)
        ):
            raise TypeError(
                "llm_usage_query_service 必须提供可调用的 "
                "list_by_assistant_request_id()"
                f"（got {type(self._llm_usage).__name__}）"
            )
        self._tools = tool_observability_query_service
        if not callable(
            getattr(self._tools, "list_by_request_id", None)
        ):
            raise TypeError(
                "tool_observability_query_service 必须提供可调用的 "
                "list_by_request_id()（Persistent Tool 读边界；"
                "Runtime 内存边界不参与 Assistant Trace）"
                f"（got {type(self._tools).__name__}）"
            )
        self._rag = (
            rag_execution_query_service
            if rag_execution_query_service is not None
            else RagExecutionPersistentQueryService()
        )
        if not callable(getattr(self._rag, "list_by_request_id", None)):
            raise TypeError(
                "rag_execution_query_service 必须提供可调用的 "
                "list_by_request_id()（Persistent RAG 读边界；"
                "Runtime 内存 Collector 不参与 Assistant Trace）"
                f"（got {type(self._rag).__name__}）"
            )

    @property
    def llm_usage_query_service(self) -> Any:
        """LLM Usage 读边界（只读引用；不暴露其 Repository）。"""
        return self._llm_usage

    @property
    def tool_observability_query_service(self) -> Any:
        """Tool 观测读边界（**Persistent** 读边界；只读引用）。"""
        return self._tools

    @property
    def rag_execution_query_service(self) -> Any:
        """RAG 执行读边界（**Persistent** 读边界；只读引用）。"""
        return self._rag

    def get_trace(self, assistant_request_id: str) -> AssistantTraceView:
        """按 Assistant request_id 组装只读 Trace Read Model。

        流程：

            validate（先于任何下游调用）
                ↓
            llm_usage  ← LLMUsageQueryService.list_by_assistant_request_id(A)
            tools      ← Tool 观测读边界.list_by_request_id(A)
                         （Persistent：ai_ops.tool_execution_record）
            rag        ← RAG 读边界.list_by_request_id(A)（Step 48；
                         Persistent：ai_ops.rag_execution_record）
                ↓
            AssistantTraceView(assistant_request_id=A,
                               llm_usage=(...), tool_executions=(...),
                               rag_executions=(...))

        Args:
            assistant_request_id: 必填、非空（strip 后非空）、≤128 字符；
                即 ``POST /api/ai/chat`` 成功响应 ``metadata.request_id``。

        Returns:
            ``AssistantTraceView``（frozen；三个集合均为 tuple）。
            无匹配（LLM / Tool / RAG 任意一侧或多侧为空）→ 空 tuple，
            **不是错误**（Case A / B / C 均合法）。

        Raises:
            ValueError: assistant_request_id 非法（先于下游调用）。
            LLMUsageQueryInputError: 透传（下游读边界输入错误）。
            LLMUsageRepositoryError: 透传（LLM Usage 数据库不可用 ——
                **绝不**降级为 ``[]``）。
            ToolExecutionRepositoryError: 透传（Tool 持久化数据库不可用 ——
                **绝不**降级为 ``[]``）。
            RagExecutionRepositoryError: 透传（RAG 持久化数据库不可用 ——
                **绝不**降级为 ``[]``）。
            TypeError / Exception: 下游读边界抛出的其它异常原样透传。

        Note:
            不在本层排序 / 去重 / 聚合 / 补全（LLM / Tool / RAG 各自保持
            下游稳定顺序；**不**合并为统一 events 列表）；不按 route 猜测内容。
        """
        validated = _validate_assistant_request_id(assistant_request_id)
        llm_usage = tuple(
            self._llm_usage.list_by_assistant_request_id(validated)
        )
        tool_executions = tuple(
            self._tools.list_by_request_id(validated)
        )
        rag_executions = tuple(
            RagExecutionTraceView.from_row(row)
            for row in self._rag.list_by_request_id(validated)
        )
        return AssistantTraceView(
            assistant_request_id=validated,
            llm_usage=llm_usage,
            tool_executions=tool_executions,
            rag_executions=rag_executions,
        )
