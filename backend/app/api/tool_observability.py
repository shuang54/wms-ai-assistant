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
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from backend.app.api.orchestrator_chat import (
    get_tool_observability_query_service,
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


__all__ = [
    "router",
    "ToolObservabilityRecordsResponse",
    "ToolObservabilityMetricsResponse",
]
