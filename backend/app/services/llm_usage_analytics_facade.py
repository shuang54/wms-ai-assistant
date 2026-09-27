"""LLM Usage Analytics Read Facade（Phase 3.10.21）。

Phase 3.10.17 ~ 3.10.20 已经把 Usage 只读链路拆成清晰的层：

    PostgreSQL
        ↓
    LLMUsageRepository（同步 SQLAlchemy）
        ↓
    LLMUsageQueryService（同步 Query Contract）
        ↓
    LLMUsageQueryRuntimeBridge（async 线程边界）
        ↓
    list[LLMUsageRecordView]
        ↓
    LLMUsageAnalyticsService（纯内存组合层）
        ↓
    LLMUsageAnalyticsSnapshot

但上层调用者仍需自己组合 Runtime 与 Analytics。本模块只做一件事：
**Application Read Facade** —— 把"查询 + 统计"组合成一个稳定入口：

    await facade.query_snapshot(filter)          # 一次查询 → 完整四视图
    await facade.query_summary(filter)           # 单视图快捷方式
    await facade.query_by_provider(filter)
    await facade.query_by_model(filter)
    await facade.query_by_provider_model(filter)

职责边界（务必阅读）：

    * **不是新的 Analytics Engine**：sum / group-by / 排序 / NULL 处理
      全部由 `LLMUsageAggregationService` 与 `LLMUsageAnalyticsService`
      完成，本模块不实现任何统计算法；
    * **不接触数据库**：无 Session / Engine / Connection / SQL /
      Repository——DB 访问只经 `LLMUsageQueryRuntimeBridge`
      （Facade → Query Runtime 允许；Facade → Repository 禁止）；
    * **一次查询 → 完整 Snapshot**（默认推荐入口）：`query_snapshot()`
      只执行一次 Runtime 查询，基于同一份 records 生成 total /
      by_provider / by_model / by_provider_model 四个视图——上层
      不需要为四个视图查询数据库四次；
    * **Snapshot 语义 = 当前 Filter 命中的记录集合**：分页
      （limit / offset）生效后，Analytics 覆盖的是**本页记录**，
      不是全库统计；不偷偷追加 COUNT(*) / total_count；
    * **复用 Pagination Contract**：直接接受 `LLMUsageQueryFilter`，
      不定义第二套 MAX_PAGE_SIZE / DEFAULT_PAGE_SIZE；
    * **错误原样传播**：Runtime / Analytics 异常不包装、不吞掉、
      不返回 None 伪装成功；
    * **Immutability**：原样返回 frozen DTO / tuple，绝不重新包装成
      dict / list 可变结构；
    * **无状态**：无缓存 / 无全局可变结果。

明确不做：

    HTTP API / FastAPI Router / Dashboard / Frontend / Billing /
    Cost / Cache / Redis / Queue / Worker / 新数据库表 / Schema 修改。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.app.services.llm_usage_analytics_service import (
    LLMUsageAnalyticsService,
)

if TYPE_CHECKING:  # pragma: no cover —— 仅类型检查期
    from backend.app.services.llm_usage_aggregation_service import (
        LLMUsageAggregate,
        ModelUsageAggregate,
        ProviderModelUsageAggregate,
        ProviderUsageAggregate,
    )
    from backend.app.services.llm_usage_analytics_service import (
        LLMUsageAnalyticsSnapshot,
    )
    from backend.app.services.llm_usage_query_runtime import (
        LLMUsageQueryRuntimeBridge,
    )
    from backend.app.services.llm_usage_query_service import (
        LLMUsageQueryFilter,
    )

__all__ = [
    "LLMUsageAnalyticsReadFacade",
]

#: Facade 依赖的 Runtime 最小能力面（与 Query Runtime Bridge 的
#: `_REQUIRED_SERVICE_METHODS` 同构的 duck-typing 校验约定）。
_REQUIRED_RUNTIME_METHODS = ("query_async",)


class LLMUsageAnalyticsReadFacade:
    """Usage Analytics 只读组合入口（Application Read Boundary）。

    内部执行链：

        query_filter
            ↓ await query_runtime.query_async(query_filter)
        list[LLMUsageRecordView]
            ↓ analytics_service.<view>(records)
        LLMUsageAnalyticsSnapshot / Aggregate / 分组 tuple

    本模块不做任何统计计算、不做任何数据库访问、不吞掉任何异常。

    构造依赖：

        query_runtime:     async 查询运行时（必须提供 `query_async()`；
                            生产环境注入 `LLMUsageQueryRuntimeBridge`，
                            测试注入 Fake）。
        analytics_service: 统计服务；None 时构造默认
                            `LLMUsageAnalyticsService()`（复用 3.10.20
                            Contract）。测试可注入 Fake。
    """

    def __init__(
        self,
        query_runtime: LLMUsageQueryRuntimeBridge,
        analytics_service: LLMUsageAnalyticsService | None = None,
    ) -> None:
        """构造 Usage Analytics Read Facade。

        Args:
            query_runtime: async 查询运行时（必须提供 `query_async()`）。
            analytics_service: 统计服务；None → 默认实例。

        Raises:
            TypeError: query_runtime 为 None 或缺少 `query_async()`。
        """
        if query_runtime is None:
            raise TypeError("query_runtime 不能为 None")
        missing = [
            name
            for name in _REQUIRED_RUNTIME_METHODS
            if not callable(getattr(query_runtime, name, None))
        ]
        if missing:
            raise TypeError(
                "query_runtime 必须提供可调用的 "
                f"{' / '.join(_REQUIRED_RUNTIME_METHODS)}"
                f"（缺少: {', '.join(missing)}；"
                f"got {type(query_runtime).__name__}）"
            )
        self._query_runtime = query_runtime
        self._analytics = (
            analytics_service
            if analytics_service is not None
            else LLMUsageAnalyticsService()
        )

    @property
    def query_runtime(self) -> LLMUsageQueryRuntimeBridge:
        """底层 async 查询运行时（只读暴露，便于测试断言注入关系）。"""
        return self._query_runtime

    @property
    def analytics_service(self) -> LLMUsageAnalyticsService:
        """底层统计服务（只读暴露，便于测试断言组合关系）。"""
        return self._analytics

    # ---------- 对外 Contract ----------

    async def query_snapshot(
        self,
        query_filter: LLMUsageQueryFilter | None = None,
    ) -> LLMUsageAnalyticsSnapshot:
        """一次查询 → 完整 Analytics Snapshot（默认推荐入口）。

        执行链：Runtime 只查询一次 → Analytics 基于同一份 records
        生成 total / by_provider / by_model / by_provider_model
        四个视图。

        Args:
            query_filter: 已构造（并已校验）的 Filter；None → Runtime
                的默认 Filter（复用现有 Pagination Contract）。

        Returns:
            `LLMUsageAnalyticsSnapshot`（frozen；覆盖当前 Filter 命中
            的记录集合，即分页生效后的本页数据，不是全库统计）。

        Raises:
            底层 Runtime / Analytics 异常原样传播（不包装 / 不吞掉）。
        """
        records = await self._query_runtime.query_async(query_filter)
        return self._analytics.snapshot(records)

    async def query_summary(
        self,
        query_filter: LLMUsageQueryFilter | None = None,
    ) -> LLMUsageAggregate:
        """全量 Summary（复用 `LLMUsageAggregate`，单次 Runtime 查询）。"""
        records = await self._query_runtime.query_async(query_filter)
        return self._analytics.summary(records)

    async def query_by_provider(
        self,
        query_filter: LLMUsageQueryFilter | None = None,
    ) -> tuple[ProviderUsageAggregate, ...]:
        """按 provider 分组（单次 Runtime 查询）。"""
        records = await self._query_runtime.query_async(query_filter)
        return self._analytics.by_provider(records)

    async def query_by_model(
        self,
        query_filter: LLMUsageQueryFilter | None = None,
    ) -> tuple[ModelUsageAggregate, ...]:
        """按 model 分组（单次 Runtime 查询）。"""
        records = await self._query_runtime.query_async(query_filter)
        return self._analytics.by_model(records)

    async def query_by_provider_model(
        self,
        query_filter: LLMUsageQueryFilter | None = None,
    ) -> tuple[ProviderModelUsageAggregate, ...]:
        """按 (provider, model) 组合键分组（单次 Runtime 查询）。"""
        records = await self._query_runtime.query_async(query_filter)
        return self._analytics.by_provider_model(records)
