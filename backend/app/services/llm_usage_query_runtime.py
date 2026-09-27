"""LLM Usage Query Runtime Boundary（Phase 3.10.18）。

Phase 3.10.17 已经建立 Usage 只读查询边界：

    PostgreSQL
        ↓
    LLMUsageRepository（同步 SQLAlchemy）
        ↓
    LLMUsageQueryService（同步：validate → repository → DTO）
        ↓
    LLMUsageRecordView

但它是**同步**的：在 async 业务链路里直接调用会让 event loop
被同步 DB I/O 阻塞：

    async caller
        ↓
    同步 SQLAlchemy Session      ← 阻塞 event loop
        ↓
    PostgreSQL

本模块是唯一新增的运行时构件：**线程边界**（与 Phase 3.10.15 的
Persistence Runtime Bridge 完全同构）：

    async caller
        ↓ await query_async(...)          （必须 await，禁止 fire-and-forget）
    LLMUsageQueryRuntimeBridge
        ↓ asyncio.to_thread               ← 线程边界（唯一新增的一步）
    worker thread
        ↓
    LLMUsageQueryService（同步，逻辑完全不变）
        ↓
    LLMUsageRepository（同步，SQL 完全不变）
        ↓
    PostgreSQL

严格约束（§二 / §六 / §七 / §九 / §二十）：

    * **不引入第二套数据库访问体系**：无 AsyncEngine / AsyncSession /
      async_sessionmaker / asyncpg；Repository 与数据库驱动保持同步不变；
    * **不复制 Query 逻辑**：本模块不写 SQL、不持有 Session、不感知
      Repository，只调用 `LLMUsageQueryService` 的现有方法
      （Repository = DB，Service = Query Contract，Bridge = Async Boundary）；
    * **同步 Service 保持 `def`**：`query()` / `list_records()` /
      `get_by_request_id()` 仍是同步方法（CLI / Test / sync code 可直接用），
      本模块只在之上加 async 入口；
    * **Session 在 worker 线程内部创建**：Repository 仍是
      `with factory() as session:`；绝不把 Session 创建在 event loop 线程，
      也绝不跨线程 / 跨 task 共享 Session（一次查询一个独立 Session）；
    * **不使用 fire-and-forget**：无 `asyncio.create_task()` /
      `ensure_future()` / background task；
    * **无进程级状态**：无 global Session / Connection / 结果缓存 /
      LRU / dict / set（本阶段不做缓存）；
    * **READ ONLY**：仍然只有 SELECT，无 INSERT / UPDATE / DELETE；
    * **错误可观察**：与 Persistence（best-effort，失败降级为 warning）
      不同，Query 是显式读操作——异常（含 Repository 错误）**原样传播**
      给 awaiter，绝不吞掉、绝不返回空结果伪装成功；
    * **Cancellation**：Query 是主动读取请求，caller cancellation
      **允许向调用方传播**（不复制 Persistence 的 absorb 语义）；
    * 不新增依赖（仅标准库 `asyncio`，与项目既有 `to_thread` 用法一致）。
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from backend.app.services.llm_usage_query_service import (
    DEFAULT_QUERY_LIMIT,
    DEFAULT_QUERY_OFFSET,
)

if TYPE_CHECKING:  # pragma: no cover —— 仅类型检查期
    from datetime import datetime

    from backend.app.services.llm_usage_query_service import (
        LLMUsageQueryFilter,
        LLMUsageQueryService,
        LLMUsageRecordView,
    )

__all__ = [
    "LLMUsageQueryRuntimeBridge",
]

_REQUIRED_SERVICE_METHODS = ("query", "list_records", "get_by_request_id")


class LLMUsageQueryRuntimeBridge:
    """把**同步** Usage Query 移出 event loop 线程的最小 adapter。

    职责只有一个：**线程边界**。

        await bridge.query_async(filter)
            └─ await asyncio.to_thread(service.query, filter)

    不复制 / 不改写 Query Contract：过滤校验、字段白名单、稳定排序、
    分页边界、DTO 语义全部由 `LLMUsageQueryService` 与 Repository 决定。

    线程安全（务必理解）：

        * `asyncio.to_thread` 每次调用把同步方法交给 loop 的默认
          ThreadPoolExecutor 执行；
        * 被搬运的只有入参（frozen filter / 基本类型）与返回值
          （frozen DTO），**不搬运** Session / Connection / Transaction；
        * Session 由 Repository 在 **worker 线程内部**创建并关闭 →
          5 并发查询 = 5 次 worker 执行 = 5 个独立 Session。

    构造依赖：

        query_service: 已构造好的同步查询服务（调用方注入，
            不在本模块内部 new —— 依赖显式、测试可替换 Fake）。
    """

    def __init__(self, query_service: "LLMUsageQueryService") -> None:
        """构造 runtime bridge。

        Args:
            query_service: 同步查询服务（必须提供 `query()` /
                `list_records()` / `get_by_request_id()`）。

        Raises:
            TypeError: query_service 为 None 或缺少上述方法。
        """
        if query_service is None:
            raise TypeError("query_service 不能为 None")
        missing = [
            name
            for name in _REQUIRED_SERVICE_METHODS
            if not callable(getattr(query_service, name, None))
        ]
        if missing:
            raise TypeError(
                "query_service 必须提供可调用的 "
                f"{' / '.join(_REQUIRED_SERVICE_METHODS)}"
                f"（缺少: {', '.join(missing)}；"
                f"got {type(query_service).__name__}）"
            )
        self._service = query_service

    @property
    def query_service(self) -> "LLMUsageQueryService":
        """底层同步查询服务（只读暴露，便于测试断言注入关系）。"""
        return self._service

    # ---------- async 入口（必须 await） ----------

    async def query_async(
        self,
        query_filter: "LLMUsageQueryFilter | None" = None,
    ) -> "list[LLMUsageRecordView]":
        """异步执行 `LLMUsageQueryService.query()`（线程边界）。

        Args:
            query_filter: 已构造（并已校验）的 Filter；None → 默认 Filter。

        Returns:
            `list[LLMUsageRecordView]`（与同步调用完全一致）。

        Raises:
            LLMUsageQueryError:      参数非法（由 Service 校验）。
            LLMUsageRepositoryError: DB 查询失败（原样传播）。
            asyncio.CancelledError:  caller 取消（原样传播）。
        """
        return await asyncio.to_thread(self._service.query, query_filter)

    async def list_records_async(
        self,
        *,
        request_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        created_at_from: "datetime | None" = None,
        created_at_to: "datetime | None" = None,
        limit: int = DEFAULT_QUERY_LIMIT,
        offset: int = DEFAULT_QUERY_OFFSET,
    ) -> "list[LLMUsageRecordView]":
        """异步执行 `LLMUsageQueryService.list_records()`（线程边界）。

        参数语义与同步版本完全一致（过滤 / 分页 / 稳定排序）。

        Raises:
            同 :meth:`query_async`。
        """
        return await asyncio.to_thread(
            self._service.list_records,
            request_id=request_id,
            provider=provider,
            model=model,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            limit=limit,
            offset=offset,
        )

    async def get_by_request_id_async(
        self,
        request_id: str,
    ) -> "LLMUsageRecordView | None":
        """异步执行 `LLMUsageQueryService.get_by_request_id()`（线程边界）。

        Args:
            request_id: Provider 请求 ID（非空字符串）。

        Returns:
            命中 → `LLMUsageRecordView`；未命中 → `None`。

        Raises:
            同 :meth:`query_async`。
        """
        return await asyncio.to_thread(
            self._service.get_by_request_id, request_id
        )
