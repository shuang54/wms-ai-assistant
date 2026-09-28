"""Tool Observability Read API（Phase 3.11 Step 25）。

Tool Observability 第一次暴露给 HTTP 客户端 —— 但**只有 Read API**：

    HTTP GET
        ↓
    Tool Observability API（仅校验 / DTO 转换 / 异常映射）
        ↓
    ToolObservabilityQueryService（Step 21 只读查询边界）
        ↓
    Snapshot / Metrics Read Model
        ↓
    Serialization Boundary（Step 24）
        ↓
    JSON

Architecture Boundary（§七 —— 本阶段最重要的约束）：

    API  →  QueryService  →  Read Model        ✅
    API  ↛  InMemoryToolExecutionCollector     ❌
    API  ↛  ToolExecutionRecord                ❌
    API  ↛  ToolExecutionService               ❌
    API  ↛  ToolRegistry / Tool Handler        ❌
    API  ↛  AIOrchestrator                     ❌

    * Collector **只**由 Composition Root（``api/orchestrator_chat.py``）
      创建；本模块通过 ``get_tool_observability_query_service()``
      复用 **Application 级同一个 Collector**，不创建、不持有、不 clear；
    * API 不计算 success_rate / failure_rate / average / max
      （全部来自 Metrics Read Model）；不排序 / 不过滤 / 不分页 /
      不聚合（§十一）；
    * 第一版**无** query parameters（§十二：不设计 Query DSL）；
    * 序列化**只**用 Step 24 的 ``snapshot_to_dict()`` / ``metrics_to_dict()``
      （§十）：不自己 ``isoformat()``、不复制字段映射、不用
      ``vars()`` / ``asdict()`` / ``__dict__``；
    * 空数据语义不变（§六）：``success_rate`` / ``failure_rate`` /
      ``average_duration_ms`` / ``max_duration_ms`` 为 ``null``（**不是** ``0``）。

安全（§十四）：response 只含 Snapshot 的 11 个字段 / Metrics 的 8 个字段；
    不含 Tool arguments / ToolResult.data / SQL / prompt / LLM response /
    凭据 / 连接信息 / traceback / Collector 内部状态。

明确不做（§二）：

    Database Persistence / Redis / Kafka / Prometheus / OpenTelemetry /
    Dashboard / Frontend / WebSocket / SSE / 鉴权重设计 / Audit 持久化 /
    Event Bus / Background worker / Celery / Agent / MCP / LangGraph。
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.app.api.orchestrator_chat import (
    get_tool_execution_persistent_query_service,
    get_tool_observability_query_service,
)
from backend.app.db.tool_execution_repository import (
    ToolExecutionRepositoryError,
)
from backend.app.services.tool_execution_persistent_query_service import (
    DEFAULT_RECENT_LIMIT,
    DEFAULT_RECENT_OFFSET,
    MAX_RECENT_LIMIT,
    MIN_RECENT_LIMIT,
)
from backend.app.services.tool_observability_serialization import (
    metrics_to_dict,
    snapshot_to_dict,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Response DTO
# ============================================================

class ToolObservabilityRecordsResponse(BaseModel):
    """``GET /api/observability/tools`` 响应（``ToolExecutionSnapshot`` 列表）。

    Attributes:
        records: 当前 retention window 内的 Tool 执行快照（写入顺序）。
                 每项**严格**是 Step 24 ``snapshot_to_dict()`` 的 11 个字段
                 （不新增 / 不删除 / 不重命名）；时间戳为 ISO 8601 字符串。
    """

    records: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "ToolExecutionSnapshot 序列化结果（11 字段；ISO 8601 时间戳）"
        ),
    )


class ToolObservabilityMetricsResponse(BaseModel):
    """``GET /api/observability/tools/metrics`` 响应（聚合统计，无维度）。

    字段与 ``ToolExecutionMetricsSnapshot`` **逐一同名**；``None`` 语义保留
    （空数据集 → 比率为 ``null``，**不是** ``0``）。Metrics 不含
    request_id / project_id / tool_name 等 identifier。
    """

    total_count: int
    success_count: int
    failure_count: int
    success_rate: float | None
    failure_rate: float | None
    total_duration_ms: float
    average_duration_ms: float | None
    max_duration_ms: float | None


class ToolExecutionHistoryItemResponse(BaseModel):
    """Persistent History 单条（**严格**对应 ``ToolExecutionSnapshot`` 11 字段）。

    不含数据库主键 ``id`` / ``created_at`` / 任何 arguments·result·SQL·
    prompt·traceback 等敏感字段；时间戳为 ISO 8601 字符串
    （由 Step 24 Serialization Boundary 产出）。
    """

    request_id: str
    round: int
    tool_name: str
    started_at: str
    finished_at: str
    duration_ms: float
    success: bool
    project_id: str | None = None
    tool_call_id: str | None = None
    error_code: str | None = None
    error_type: str | None = None


class ToolExecutionHistoryResponse(BaseModel):
    """``GET /api/observability/tools/history`` 响应（数据库长期历史）。

    Attributes:
        items:  当前页的 Tool 执行快照（``started_at DESC, id DESC``；
                来自 Persistent Query Service，本层**不重排 / 不过滤 /
                不聚合 / 不去重**）。
        limit:  本页请求的条数（回显请求参数）。
        offset: 本页请求的起始偏移（回显请求参数）。

    Note:
        **不返回** ``total_count``：那需要额外 ``COUNT(*)``，
        属后续 Metrics / Query metadata 范围（本阶段显式排除）。
    """

    items: list[ToolExecutionHistoryItemResponse] = Field(
        default_factory=list,
        description=(
            "ToolExecutionSnapshot 序列化结果（11 字段；ISO 8601 时间戳）"
        ),
    )
    limit: int = Field(
        ...,
        ge=MIN_RECENT_LIMIT,
        le=MAX_RECENT_LIMIT,
        description="本页条数（回显请求参数）",
    )
    offset: int = Field(
        ...,
        ge=0,
        description="本页起始偏移（回显请求参数）",
    )


# ============================================================
# Endpoints
# ============================================================

@router.get(
    "/observability/tools",
    response_model=ToolObservabilityRecordsResponse,
    responses={
        200: {"description": "当前 retention window 内的 Tool 执行快照"},
        500: {"description": "观测数据不可用（不暴露内部细节）"},
    },
)
async def list_tool_executions() -> ToolObservabilityRecordsResponse:
    """Tool 执行快照只读列表（``ToolExecutionSnapshot`` → JSON）。

    Pipeline：

        QueryService.snapshots()          （只读；retention window）
            → Snapshot（Step 22 Read Model）
            → snapshot_to_dict()          （Step 24 显式映射）
            → ToolObservabilityRecordsResponse

    语义：

        * **只读**：不触发任何 Tool 执行 / 不写 Collector / 不落库；
        * 数据与 Application 级 Collector **同源**（同进程内存；
          多进程各自独立）；
        * 顺序 / 内容完全由 QueryService 决定：本层**不排序、不过滤、
          不分页、不聚合**；
        * 无 query parameter（第一版不提供 Query DSL）。
    """
    try:
        query = get_tool_observability_query_service()
        records = [snapshot_to_dict(s) for s in query.snapshots()]
    except Exception:  # noqa: BLE001 — 不暴露 traceback / 内部模块路径
        logger.error(
            "tool observability records unavailable",
            extra={"error_type": "unknown"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tool 观测数据不可用",
        )
    return ToolObservabilityRecordsResponse(records=records)


@router.get(
    "/observability/tools/metrics",
    response_model=ToolObservabilityMetricsResponse,
    responses={
        200: {"description": "当前 retention window 的聚合统计（无维度）"},
        500: {"description": "观测数据不可用（不暴露内部细节）"},
    },
)
async def tool_execution_metrics() -> ToolObservabilityMetricsResponse:
    """Tool 执行聚合统计（``ToolExecutionMetricsSnapshot`` → JSON）。

    Pipeline：

        QueryService.metrics()             （复用 Step 17 Metrics Service）
            → MetricsSnapshot              （只读 Read Model）
            → metrics_to_dict()            （Step 24 显式映射）
            → ToolObservabilityMetricsResponse

    语义：

        * API **不计算**任何指标（成功率为 Metrics 层语义）；
        * 空数据集：比率 / 均值 / 最大值为 ``null``（**不是** ``0``）；
        * 无维度（by_tool / by_project / by_request 均未实现）。
    """
    try:
        query = get_tool_observability_query_service()
        payload = metrics_to_dict(query.metrics())
    except Exception:  # noqa: BLE001 — 不暴露 traceback / 内部模块路径
        logger.error(
            "tool observability metrics unavailable",
            extra={"error_type": "unknown"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tool 观测数据不可用",
        )
    return ToolObservabilityMetricsResponse(**payload)


@router.get(
    "/observability/tools/history",
    response_model=ToolExecutionHistoryResponse,
    responses={
        200: {"description": "数据库中的 Tool 执行历史（最近 N 条）"},
        422: {"description": "limit 非法（FastAPI Query 校验）"},
        502: {"description": "持久历史不可用（数据库未配置 / 查询失败）"},
    },
)
async def list_tool_execution_history(
    limit: Annotated[
        int,
        Query(
            ge=MIN_RECENT_LIMIT,
            le=MAX_RECENT_LIMIT,
            description=(
                f"返回条数（{MIN_RECENT_LIMIT}~{MAX_RECENT_LIMIT}；"
                f"默认 {DEFAULT_RECENT_LIMIT}）"
            ),
        ),
    ] = DEFAULT_RECENT_LIMIT,
    offset: Annotated[
        int,
        Query(
            ge=0,
            description=(
                f"起始偏移（>= 0；默认 {DEFAULT_RECENT_OFFSET}；无上限，"
                "超出记录数即空页）"
            ),
        ),
    ] = DEFAULT_RECENT_OFFSET,
    project_id: Annotated[
        str | None,
        Query(description="精确匹配 project_id（省略 = 不过滤）"),
    ] = None,
    tool_name: Annotated[
        str | None,
        Query(description="精确匹配 tool_name（省略 = 不过滤）"),
    ] = None,
    success: Annotated[
        bool | None,
        Query(
            description=(
                "精确匹配 success（true / false；省略 = 不过滤 —— "
                "false 与省略语义不同）"
            )
        ),
    ] = None,
) -> ToolExecutionHistoryResponse:
    """Tool 执行**持久历史**（数据库）只读列表（Phase 3.11 Step 30）。

    Pipeline：

        ToolExecutionPersistentQueryService.list_recent(limit)   （只读；Step 29）
            → list[ToolExecutionSnapshot]     （started_at DESC, id DESC）
            → snapshot_to_dict()              （Step 24 显式映射）
            → ToolExecutionHistoryResponse（items）

    与 Runtime 端点（``GET /api/observability/tools``）的关系：

        Runtime      = 本进程内存 Collector（近期观测）
        Persistent   = 数据库长期历史（跨进程 / 跨重启）
        **并列**：不 merge、不 fallback、不去重；
        DB 失败 → 502（**绝不**回退到内存视图）。

    语义：

        * **只读**：不写库 / 不触发 Tool 执行 / 不修改 Collector；
        * 顺序完全由 Repository 决定（``started_at DESC, id DESC``）：
          本层不排序、不过滤、不聚合、不去重；分页 = 仅 LIMIT + OFFSET
          （所有页共用同一套稳定排序 → 跨页无重复 / 无遗漏）；
        * 唯一 query parameters = ``limit`` + ``offset``
          （无 total_count / COUNT(*)；无 cursor / keyset / filter）；
        * 空库 / offset 超界 → ``{"items": [], "limit": …, "offset": …}``
          （200，不是 404 / 不是异常）。
        * DB 失败 → 502（**绝不**回退内存视图）。
    """
    try:
        query = get_tool_execution_persistent_query_service()
        snapshots = query.list_recent(
            limit=limit,
            offset=offset,
            project_id=project_id,
            tool_name=tool_name,
            success=success,
        )
        items = [snapshot_to_dict(snapshot) for snapshot in snapshots]
    except ToolExecutionRepositoryError:
        # 持久化层不可用（DATABASE_URL 未配置 / 查询失败）：
        # 明确 502 —— 不回退到内存视图、不吞异常、不返回 []。
        logger.error(
            "tool execution history unavailable",
            extra={"error_type": "ToolExecutionRepositoryError"},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Tool 观测历史数据不可用",
        )
    except Exception:  # noqa: BLE001 — 不暴露 traceback / 内部模块路径
        logger.error(
            "tool execution history failed",
            extra={"error_type": "unknown"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tool 观测数据不可用",
        )
    return ToolExecutionHistoryResponse(
        items=[
            ToolExecutionHistoryItemResponse(**item) for item in items
        ],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/observability/tools/metrics/persistent",
    response_model=ToolObservabilityMetricsResponse,
    responses={
        200: {"description": "数据库中的 Tool 执行聚合指标（可精确过滤）"},
        422: {"description": "过滤参数非法（FastAPI Query 校验）"},
        502: {"description": "持久指标不可用（数据库未配置 / 查询失败）"},
    },
)
async def persistent_tool_execution_metrics(
    project_id: Annotated[
        str | None,
        Query(description="精确匹配 project_id（省略 = 不过滤）"),
    ] = None,
    tool_name: Annotated[
        str | None,
        Query(description="精确匹配 tool_name（省略 = 不过滤）"),
    ] = None,
    success: Annotated[
        bool | None,
        Query(
            description=(
                "精确匹配 success（true / false；省略 = 不过滤 —— "
                "false 与省略语义不同）"
            )
        ),
    ] = None,
) -> ToolObservabilityMetricsResponse:
    """Tool 执行**持久指标**（数据库聚合；Phase 3.11 Step 33）。

    Pipeline：

        ToolExecutionPersistentQueryService.metrics(...)   （只读；SQL 聚合）
            → ToolExecutionMetricsSnapshot（8 字段）
            → metrics_to_dict()                （Step 24 显式映射）
            → ToolObservabilityMetricsResponse

    与 Runtime 端点（``GET /api/observability/tools/metrics``）的关系：

        Runtime      = 本进程内存 Collector 聚合
        Persistent   = 数据库聚合（跨进程 / 跨重启）
        **并列**：不 merge、不 fallback；
        DB 失败 → 502（**绝不**回退到内存指标）。

    语义：

        * **只读**：不写库 / 不触发 Tool 执行 / 不修改 Collector；
        * 精确过滤（project_id / tool_name / success；条件 AND）；
          过滤**全部下推 SQL**，本层不做 Python 过滤 / 扫描 / 排序；
        * 无 ``limit`` / ``offset``（聚合结果是单行，不是列表）；
        * 空数据集 → 计数 0；``success_rate`` / ``failure_rate`` /
          ``average_duration_ms`` / ``max_duration_ms`` 为 ``null``
          （**不是** 0）；``total_duration_ms`` 为 ``0.0``；
        * 响应**不含** request_id / project_id / tool_name / args / SQL 等。
    """
    try:
        query = get_tool_execution_persistent_query_service()
        snapshot = query.metrics(
            project_id=project_id,
            tool_name=tool_name,
            success=success,
        )
        payload = metrics_to_dict(snapshot)
    except ToolExecutionRepositoryError:
        logger.error(
            "persistent tool execution metrics unavailable",
            extra={"error_type": "ToolExecutionRepositoryError"},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Tool 持久观测指标不可用",
        )
    except Exception:  # noqa: BLE001 — 不暴露 traceback / 内部模块路径
        logger.error(
            "persistent tool execution metrics failed",
            extra={"error_type": "unknown"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tool 观测数据不可用",
        )
    return ToolObservabilityMetricsResponse(**payload)


__all__ = [
    "router",
    "ToolObservabilityRecordsResponse",
    "ToolObservabilityMetricsResponse",
    "ToolExecutionHistoryItemResponse",
    "ToolExecutionHistoryResponse",
]
