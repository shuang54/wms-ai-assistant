"""Usage Analytics HTTP Read API（Phase 3.10.22 Step 2）。

边界：

    HTTP GET /api/usage/analytics
        ↓
    UsageAnalyticsQueryParams（HTTP Query DTO，只做类型解析）
        ↓
    LLMUsageQueryFilter（Domain Filter，唯一业务校验入口）
        ↓
    LLMUsageAnalyticsReadFacade.query_snapshot()   ← 唯一下游依赖
        ↓
    LLMUsageQueryRuntimeBridge → LLMUsageQueryService → PostgreSQL
        ↓
    LLMUsageAnalyticsSnapshot
        ↓
    UsageAnalyticsResponse（Pydantic API DTO，只读投影）

职责边界（务必阅读）：

    * 本 Router 只负责 HTTP / Schema / 异常映射：
        - Query 参数模型 → Domain Filter（字段一一对应，不遗漏、
          不增加）；
        - Snapshot（frozen dataclass）→ Pydantic Response DTO；
        - 领域异常 → HTTP 状态码。
    * **不复制 Filter Contract**：limit ∈ [1, 100]、offset >= 0、
      空白串拒绝、tz-aware datetime、created_at_from <= created_at_to
      全部由 `LLMUsageQueryFilter.__post_init__` 校验（单一事实来源）；
      本层只把 query string 解析成 datetime / int，最终由 Filter 拒绝。
    * **不重复实现统计**：无 sum / count / group-by / 分页计算——
      全部由 Analytics / Aggregation / Query 链路完成。
    * **单端点单查询**：直接调用 `query_snapshot()`（一次 Runtime
      查询 → 完整四视图），不调用四个便捷方法（各自会重复查询）。
    * **分页语义**：Analytics = 当前 Filter 命中的本页记录集合，
      **不是全库统计**；没有 global_total / total_count / COUNT(*)。
    * **只读**：无 DB write、无 LLM 调用、无缓存、无队列。

依赖方式：与 api/chat.py、api/rag.py 同风格——**模块级单例**
（测试用 monkeypatch 替换 `_usage_facade`）；构造期不触发 DB 连接
（session factory 延迟获取），DATABASE_URL 未配置时首次请求才以
`LLMUsageRepositoryError` → 502 暴露。

错误映射（沿用项目既有规范，未知异常原样上抛由 Starlette 兜底 500）：

    LLMUsageQueryInputError                       → 422
    LLMUsageRepositoryError                       → 502
    LLMUsageQueryError（其余）                     → 500
    LLMUsageAnalyticsInputError                    → 500
    LLMUsageAggregationInputError                  → 500
    LLMUsageAnalyticsError / LLMUsageAggregationError → 500

安全：响应与错误 detail 不含 API Key / Password / DATABASE_URL /
SQL / Session / traceback / prompt 原文；字段仅为 Snapshot 白名单。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.app.db.llm_usage_repository import LLMUsageRepositoryError
from backend.app.services.llm_usage_aggregation_service import (
    LLMUsageAggregate,
    LLMUsageAggregationError,
    LLMUsageAggregationInputError,
    ModelUsageAggregate,
    ProviderModelUsageAggregate,
    ProviderUsageAggregate,
)
from backend.app.services.llm_usage_analytics_facade import (
    LLMUsageAnalyticsReadFacade,
)
from backend.app.services.llm_usage_analytics_service import (
    LLMUsageAnalyticsError,
    LLMUsageAnalyticsInputError,
    LLMUsageAnalyticsSnapshot,
)
from backend.app.services.llm_usage_query_runtime import (
    LLMUsageQueryRuntimeBridge,
)
from backend.app.services.llm_usage_query_service import (
    DEFAULT_QUERY_LIMIT,
    DEFAULT_QUERY_OFFSET,
    LLMUsageQueryError,
    LLMUsageQueryFilter,
    LLMUsageQueryInputError,
    LLMUsageQueryService,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Query Parameters（HTTP Query DTO → Domain Filter）
# ============================================================

class UsageAnalyticsQueryParams(BaseModel):
    """GET /api/usage/analytics 的 Query 参数模型。

    只做 HTTP 类型解析（query string → datetime / int）；
    业务校验（空白串拒绝、tz-aware、from <= to、limit 范围、
    offset >= 0）全部由 `LLMUsageQueryFilter.__post_init__` 承担
    ——本模型不复制第二套 Filter 校验逻辑。

    字段与 `LLMUsageQueryFilter` 一一对应，缺省值引用 Query 层
    既有常量（DEFAULT_QUERY_LIMIT / DEFAULT_QUERY_OFFSET），
    不定义第二套分页常量。
    """

    request_id: str | None = Field(
        None,
        description="按 request_id 精确匹配；空白串由 Filter Contract 拒绝",
    )
    provider: str | None = Field(
        None,
        description="按 provider 精确匹配；空白串由 Filter Contract 拒绝",
    )
    model: str | None = Field(
        None,
        description="按 model 精确匹配；空白串由 Filter Contract 拒绝",
    )
    created_at_from: datetime | None = Field(
        None,
        description=(
            "created_at >= 此值（ISO 8601，必须带时区偏移，"
            "如 2026-09-01T00:00:00+07:00；含边界）"
        ),
    )
    created_at_to: datetime | None = Field(
        None,
        description=(
            "created_at <= 此值（ISO 8601，必须带时区偏移；含边界；"
            "早于 created_at_from 时被 Filter Contract 拒绝）"
        ),
    )
    limit: int = Field(
        DEFAULT_QUERY_LIMIT,
        description="分页大小；合法范围 [1, 100] 由 Filter Contract 校验",
    )
    offset: int = Field(
        DEFAULT_QUERY_OFFSET,
        description="分页偏移；>= 0 由 Filter Contract 校验",
    )


# ============================================================
# Response Schemas（Pydantic API DTO，镜像 frozen Domain DTO）
# ============================================================

class UsageAggregateResponse(BaseModel):
    """Usage 聚合统计（镜像 `LLMUsageAggregate`；Domain DTO 不变）。

    NULL 语义：token 字段是**已知值之和**（`None` 不计入也不当 0）；
    `*_known = False` 表示该列至少一条记录未知——原样返回，
    调用方据此理解求和只覆盖已知部分。
    """

    total_requests: int = Field(
        ...,
        ge=0,
        description="当前 Filter 命中的记录条数（不去重；受分页 limit 约束）",
    )
    prompt_tokens: int = Field(
        ...,
        ge=0,
        description="已知 prompt tokens 之和（NULL 不计入也不当 0）",
    )
    completion_tokens: int = Field(
        ...,
        ge=0,
        description="已知 completion tokens 之和（NULL 不计入也不当 0）",
    )
    total_tokens: int = Field(
        ...,
        ge=0,
        description=(
            "已知 total tokens 之和（独立观测值，"
            "绝不重算为 prompt + completion）"
        ),
    )
    prompt_tokens_known: bool = Field(
        ...,
        description="False 表示至少一条记录的 prompt_tokens 未知",
    )
    completion_tokens_known: bool = Field(
        ...,
        description="False 表示至少一条记录的 completion_tokens 未知",
    )
    total_tokens_known: bool = Field(
        ...,
        description="False 表示至少一条记录的 total_tokens 未知",
    )


class UsageProviderAggregateResponse(BaseModel):
    """按 provider 分组（镜像 `ProviderUsageAggregate`）。

    provider 为 null 表示该分组记录的 provider 未知——
    原样保留 null，不替换为 "unknown" 字符串，不过滤掉。
    """

    provider: str | None = Field(
        ...,
        description="provider；null 表示该分组记录的 provider 未知",
    )
    aggregate: UsageAggregateResponse = Field(
        ..., description="该分组的聚合统计",
    )


class UsageModelAggregateResponse(BaseModel):
    """按 model 分组（镜像 `ModelUsageAggregate`）。"""

    model: str | None = Field(
        ...,
        description="model；null 表示该分组记录的 model 未知",
    )
    aggregate: UsageAggregateResponse = Field(
        ..., description="该分组的聚合统计",
    )


class UsageProviderModelAggregateResponse(BaseModel):
    """按 (provider, model) 组合键分组（镜像 `ProviderModelUsageAggregate`）。"""

    provider: str | None = Field(
        ...,
        description="provider；null 表示该分组记录的 provider 未知",
    )
    model: str | None = Field(
        ...,
        description="model；null 表示该分组记录的 model 未知",
    )
    aggregate: UsageAggregateResponse = Field(
        ..., description="该分组的聚合统计",
    )


class UsageAnalyticsResponse(BaseModel):
    """Usage Analytics Snapshot 响应（镜像 `LLMUsageAnalyticsSnapshot`）。

    语义：全部视图基于**当前 Filter 命中的本页记录**（分页生效后），
    不是全库统计——没有 global_total / total_count / COUNT(*)。
    """

    total: UsageAggregateResponse = Field(
        ..., description="本页记录的全量聚合",
    )
    by_provider: list[UsageProviderAggregateResponse] = Field(
        ...,
        description="按 provider 升序分组（NULL 分组最后，原样保留 null）",
    )
    by_model: list[UsageModelAggregateResponse] = Field(
        ...,
        description="按 model 升序分组（NULL 分组最后，原样保留 null）",
    )
    by_provider_model: list[UsageProviderModelAggregateResponse] = Field(
        ...,
        description="按 (provider, model) 升序分组",
    )


# ============================================================
# Dependency（与 api/chat.py 同风格：模块级单例，测试用 monkeypatch 替换）
# ============================================================

# 构造期安全：LLMUsageQueryService 默认构造 Repository，但 session
# factory 延迟获取（DATABASE_URL 未配置时不创建 Engine），
# 应用导入不会触发 DB 连接。
_query_service = LLMUsageQueryService()
_query_runtime = LLMUsageQueryRuntimeBridge(_query_service)
_usage_facade = LLMUsageAnalyticsReadFacade(_query_runtime)


# ============================================================
# Mappers（只做 Service DTO → API DTO，无业务计算）
# ============================================================

def _to_usage_aggregate_response(
    aggregate: LLMUsageAggregate,
) -> UsageAggregateResponse:
    """`LLMUsageAggregate`（Service DTO）→ `UsageAggregateResponse`。"""
    return UsageAggregateResponse(
        total_requests=aggregate.total_requests,
        prompt_tokens=aggregate.prompt_tokens,
        completion_tokens=aggregate.completion_tokens,
        total_tokens=aggregate.total_tokens,
        prompt_tokens_known=aggregate.prompt_tokens_known,
        completion_tokens_known=aggregate.completion_tokens_known,
        total_tokens_known=aggregate.total_tokens_known,
    )


def _to_usage_analytics_response(
    snapshot: LLMUsageAnalyticsSnapshot,
) -> UsageAnalyticsResponse:
    """`LLMUsageAnalyticsSnapshot` → `UsageAnalyticsResponse`（只读投影）。

    NULL 分组原样映射为 null；不排序、不过滤、不重算——
    Snapshot 的顺序与值来自 Analytics 层，本函数逐一投影。
    """
    return UsageAnalyticsResponse(
        total=_to_usage_aggregate_response(snapshot.total),
        by_provider=[
            UsageProviderAggregateResponse(
                provider=group.provider,
                aggregate=_to_usage_aggregate_response(group.aggregate),
            )
            for group in snapshot.by_provider
        ],
        by_model=[
            UsageModelAggregateResponse(
                model=group.model,
                aggregate=_to_usage_aggregate_response(group.aggregate),
            )
            for group in snapshot.by_model
        ],
        by_provider_model=[
            UsageProviderModelAggregateResponse(
                provider=group.provider,
                model=group.model,
                aggregate=_to_usage_aggregate_response(group.aggregate),
            )
            for group in snapshot.by_provider_model
        ],
    )


def _to_query_filter(
    params: UsageAnalyticsQueryParams,
) -> LLMUsageQueryFilter:
    """HTTP Query DTO → Domain Filter（字段一一对应）。

    不做任何业务校验：`LLMUsageQueryFilter.__post_init__` 是唯一
    校验入口，非法参数在这里抛出 `LLMUsageQueryInputError`。
    """
    return LLMUsageQueryFilter(
        request_id=params.request_id,
        provider=params.provider,
        model=params.model,
        created_at_from=params.created_at_from,
        created_at_to=params.created_at_to,
        limit=params.limit,
        offset=params.offset,
    )


# ============================================================
# Endpoint
# ============================================================

@router.get("/usage/analytics", response_model=UsageAnalyticsResponse)
async def usage_analytics(
    params: Annotated[UsageAnalyticsQueryParams, Query()],
) -> UsageAnalyticsResponse:
    """查询 LLM Usage Analytics Snapshot（只读，Phase 3.10.22）。

    Pipeline：

        HTTP Query → LLMUsageQueryFilter → Facade.query_snapshot()
            → Snapshot（total / by_provider / by_model /
              by_provider_model，单次 Runtime 查询）

    语义：

        * Analytics = 当前 Filter 命中的**本页记录**的统计
          （分页生效后），不是全库统计；
        * provider / model 为 null 的分组原样返回（NULL ≠ 0 /
          未知信息不丢失）；
        * 空数据是 200 + 零值 Snapshot + 空数组，不是错误。

    异常映射（不吞异常、不伪装成功）：

        LLMUsageQueryInputError           → 422（Filter Contract 拒绝）
        LLMUsageRepositoryError           → 502（DB 未配置 / 查询失败）
        LLMUsageQueryError（其余）         → 500
        LLMUsageAnalyticsInputError        → 500（内部数据不一致）
        LLMUsageAggregationInputError      → 500（内部数据不一致）
        LLMUsageAnalyticsError / LLMUsageAggregationError → 500
        未知异常                           → 原样上抛（兜底 500）
    """
    try:
        snapshot = await _usage_facade.query_snapshot(_to_query_filter(params))
    except LLMUsageQueryInputError as exc:
        # Filter Contract 拒绝（空白串 / naive datetime / from > to /
        # limit 越界 / offset 负数）——校验逻辑只存在于 Filter 一处
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except LLMUsageRepositoryError as exc:
        logger.warning(
            "usage analytics repository error",
            extra={"error_type": "LLMUsageRepositoryError"},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Usage 查询失败: {exc}",
        )
    except LLMUsageQueryError as exc:
        logger.exception("usage analytics query service error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Usage 查询服务内部错误: {exc}",
        )
    except (LLMUsageAnalyticsInputError, LLMUsageAggregationInputError) as exc:
        # Analytics 输入来自内部 Runtime（非调用方可控）→ 内部错误
        logger.exception("usage analytics internal input error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Usage Analytics 内部错误: {exc}",
        )
    except (LLMUsageAnalyticsError, LLMUsageAggregationError) as exc:
        logger.exception("usage analytics service error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Usage Analytics 服务内部错误: {exc}",
        )

    # 未知异常：不 except Exception、不吞掉，原样上抛交由
    # Starlette ServerErrorMiddleware 兜底 500（与项目既有端点一致）。

    return _to_usage_analytics_response(snapshot)


__all__ = [
    "router",
    "UsageAnalyticsQueryParams",
    "UsageAnalyticsResponse",
    "UsageAggregateResponse",
    "UsageProviderAggregateResponse",
    "UsageModelAggregateResponse",
    "UsageProviderModelAggregateResponse",
]
