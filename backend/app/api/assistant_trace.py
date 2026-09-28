"""Assistant Trace Read API（Phase 3.12 Step 39）——只读 HTTP 边界。

    HTTP GET /api/observability/assistant-trace/{assistant_request_id}
        ↓
    AssistantTraceQueryService.get_trace()          （Step 38 Read Model Composition）
        ├── LLMUsageQueryService        → PostgreSQL（ai_ops.llm_usage_record）
        └── ToolObservabilityQueryService → Runtime Memory（InMemory Collector）
        ↓
    AssistantTraceView
        ↓
    AssistantTraceResponse（显式字段映射）
        ↓
    JSON

边界（本模块**只**做 HTTP 适配）：

* **不新增数据存储**：无 Trace Table / Trace Repository / Trace ORM / migration；
* **不重新实现查询**：只调用 ``AssistantTraceQueryService.get_trace()``；
  不直接接触 Repository / SQLAlchemy / Collector / ToolExecutionService /
  LLM Provider；
* **不排序 / 不去重 / 不聚合 / 不过滤 / 不合并**：
  ``llm_usage`` 保持 Step 37 的 ``created_at ASC, id ASC``；
  ``tool_executions`` 保持 Collector 写入顺序；本层只做 ``tuple → list``；
* **不吞异常**：下游失败必须抛出（DB 故障 ≠ "没有 Trace"）；
* **不放缓存**（无 Redis / LRU / TTL / in-memory trace cache）；
* **无分页**（``assistant_request_id`` 本身就是一个 Trace scope）；
* **显式映射**：不使用 vars / asdict / __dict__ / model_dump；
  ``response_model`` 同时充当字段过滤边界。

不新增（本阶段明确排除）：Trace Span / OpenTelemetry / Prometheus /
Conversation / Memory / Agent / MCP / Streaming / WebSocket / Dashboard。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, Field

from backend.app.api.orchestrator_chat import (
    get_assistant_trace_query_service,
)
from backend.app.db.llm_usage_repository import LLMUsageRepositoryError
from backend.app.services.assistant_trace_query_service import (
    AssistantTraceQueryService,
    AssistantTraceView,
)
from backend.app.services.llm_usage_query_service import (
    LLMUsageTraceRecordView,
)
from backend.app.services.tool_observability_snapshot import (
    ToolExecutionSnapshot,
)

logger = logging.getLogger(__name__)
router = APIRouter()

#: Assistant Trace ID 长度边界（与 ORM 列 VARCHAR(128) / 服务层校验一致）。
MIN_ASSISTANT_REQUEST_ID_LENGTH: int = 1
MAX_ASSISTANT_REQUEST_ID_LENGTH: int = 128


class LLMUsageTraceResponse(BaseModel):
    """Trace 中的一条 LLM Usage（**严格**对应 Step 37 的 9 字段视图）。

    ``request_id`` 语义 = **Provider 请求 ID**（未改名）；
    Assistant Trace 身份由 ``assistant_request_id`` 表达。

    不含 prompt / messages / raw_response / 凭据 / DB 对象。
    """

    id: int
    assistant_request_id: str
    request_id: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    created_at: datetime


class ToolExecutionTraceResponse(BaseModel):
    """Trace 中的一条 Tool 执行（**严格**对应 ``ToolExecutionSnapshot`` 11 字段）。

    不含 Tool arguments / ToolResult.data / SQL / DB 对象 / traceback。
    """

    request_id: str
    round: int
    tool_name: str
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    success: bool
    project_id: str | None = None
    tool_call_id: str | None = None
    error_code: str | None = None
    error_type: str | None = None


class AssistantTraceResponse(BaseModel):
    """``GET /api/observability/assistant-trace/{assistant_request_id}`` 响应。

    Attributes:
        assistant_request_id: 回显请求的 Assistant Trace ID（逐字符一致）。
        llm_usage:            该 Trace 的 LLM Usage（``created_at ASC, id ASC``）。
        tool_executions:      该 Trace 的 Tool 执行（Collector 写入顺序）。

    Note:
        LLM 与 Tool 当前来自**不同数据源**（PostgreSQL / Runtime Memory），
        本层不做合并 / 去重 / 聚合；空 Trace（两个数组均为 ``[]``）是合法结果，
        不代表 HTTP resource-not-found。
    """

    assistant_request_id: str
    llm_usage: list[LLMUsageTraceResponse] = Field(default_factory=list)
    tool_executions: list[ToolExecutionTraceResponse] = Field(
        default_factory=list
    )


def _llm_usage_response(
    view: LLMUsageTraceRecordView,
) -> LLMUsageTraceResponse:
    """``LLMUsageTraceRecordView`` → API DTO（显式逐字段映射）。"""
    return LLMUsageTraceResponse(
        id=view.id,
        assistant_request_id=view.assistant_request_id,
        request_id=view.request_id,
        provider=view.provider,
        model=view.model,
        prompt_tokens=view.prompt_tokens,
        completion_tokens=view.completion_tokens,
        total_tokens=view.total_tokens,
        created_at=view.created_at,
    )


def _tool_execution_response(
    snapshot: ToolExecutionSnapshot,
) -> ToolExecutionTraceResponse:
    """``ToolExecutionSnapshot`` → API DTO（显式逐字段映射）。"""
    return ToolExecutionTraceResponse(
        request_id=snapshot.request_id,
        round=snapshot.round,
        tool_name=snapshot.tool_name,
        started_at=snapshot.started_at,
        finished_at=snapshot.finished_at,
        duration_ms=snapshot.duration_ms,
        success=snapshot.success,
        project_id=snapshot.project_id,
        tool_call_id=snapshot.tool_call_id,
        error_code=snapshot.error_code,
        error_type=snapshot.error_type,
    )


def _to_trace_response(trace: AssistantTraceView) -> AssistantTraceResponse:
    """``AssistantTraceView`` → API DTO（顺序保持，只做 tuple → list）。"""
    return AssistantTraceResponse(
        assistant_request_id=trace.assistant_request_id,
        llm_usage=[_llm_usage_response(v) for v in trace.llm_usage],
        tool_executions=[
            _tool_execution_response(s) for s in trace.tool_executions
        ],
    )


@router.get(
    "/observability/assistant-trace/{assistant_request_id}",
    response_model=AssistantTraceResponse,
    responses={
        200: {"description": "该 Assistant Trace 的只读 Read Model（可为空）"},
        400: {"description": "assistant_request_id 非法（如纯空白）"},
        422: {"description": "路径参数校验失败（长度越界）"},
        502: {"description": "LLM Usage 数据源不可用（数据库未配置 / 查询失败）"},
        500: {"description": "Trace 观测数据不可用"},
    },
)
async def get_assistant_trace(
    assistant_request_id: Annotated[
        str,
        Path(
            min_length=MIN_ASSISTANT_REQUEST_ID_LENGTH,
            max_length=MAX_ASSISTANT_REQUEST_ID_LENGTH,
            description=(
                "Assistant Trace ID（即 POST /api/ai/chat 成功响应 "
                "metadata.request_id；不得为纯空白）"
            ),
        ),
    ],
) -> AssistantTraceResponse:
    """按 Assistant request_id 读取只读 Trace Read Model（Phase 3.12 Step 39）。

    Pipeline：

        HTTP Path → AssistantTraceQueryService.get_trace()
            → AssistantTraceView → AssistantTraceResponse → JSON

    语义：

        * **只读**：不写库 / 不触发 Tool / 不调用 LLM / 不重新路由；
        * **空 Trace 合法**：LLM 与 Tool 任意一侧或两侧为空 → ``[]``，
          仍返回 ``200``（不返回 404）；
        * 顺序保持下游语义（LLM ``created_at ASC, id ASC``；
          Tool Collector 写入顺序），HTTP 层不重排；
        * 下游失败**原样呈现**为 5xx（绝不降级成 ``200`` + 空 Trace）。

    错误映射：

        400  assistant_request_id 非法（服务层校验：纯空白等）
        422  路径参数长度越界（FastAPI Path 校验）
        502  LLM Usage 数据源不可用（LLMUsageRepositoryError）
        500  其它未预期错误（不暴露 traceback / SQL / 凭据 / 内部模块路径）

    安全：响应只包含 ``assistant_request_id`` / ``llm_usage[]``（9 字段）/
        ``tool_executions[]``（11 字段），不含 prompt / messages / arguments /
        ToolResult.data / SQL / DB 连接 / Session / 凭据。
    """
    try:
        service: AssistantTraceQueryService = (
            get_assistant_trace_query_service()
        )
        trace: AssistantTraceView = service.get_trace(assistant_request_id)
    except ValueError as exc:
        # 服务层输入校验（纯空白等）—— 与项目既有 400「非法输入」语义一致
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"非法输入: {exc}",
        )
    except LLMUsageRepositoryError:
        logger.error(
            "assistant trace llm usage unavailable",
            extra={"error_type": "LLMUsageRepositoryError"},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="助手链路观测数据不可用",
        )
    except Exception:  # noqa: BLE001 —— 不暴露 traceback / 内部模块路径
        logger.error(
            "assistant trace unavailable",
            extra={"error_type": "unknown"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="助手链路观测数据不可用",
        )
    return _to_trace_response(trace)


__all__ = [
    "router",
    "AssistantTraceResponse",
    "LLMUsageTraceResponse",
    "ToolExecutionTraceResponse",
    "MIN_ASSISTANT_REQUEST_ID_LENGTH",
    "MAX_ASSISTANT_REQUEST_ID_LENGTH",
]
