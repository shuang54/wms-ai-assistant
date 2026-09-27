"""LLM Usage Persistence Runtime Boundary（Phase 3.10.15）。

Phase 3.10.14 已经证明 Usage Persistence 的正确性：

    LLMObservation
        ↓
    DatabaseLLMAccountingSink
        ↓
    LLMUsagePersistenceService
        ↓
    LLMUsageRepository（同步 SQLAlchemy）
        ↓
    ai_ops.llm_usage_record

但其中的 **同步 SQLAlchemy 写入**发生在 async LLM 请求的 event loop
线程里：

    async LLM request
        ↓
    同步 DB write        ← 会短暂阻塞 event loop
        ↓
    await completion

本模块是**唯一新增的运行时构件**：把同步持久化推进一个线程边界，
使 event loop 在 DB 写入期间保持可调度。

    DatabaseLLMAccountingSink.arecord()        （await，非 fire-and-forget）
        ↓
    LLMUsagePersistenceRuntimeBridge.persist_async()
        ↓  asyncio.to_thread  ← 线程边界（唯一新增的一步）
    LLMUsagePersistenceService.persist()       （同步，逻辑完全不变）
        ↓
    LLMUsageRepository.create()
        ↓  with factory() as session, session.begin():
    ai_ops.llm_usage_record

约束（沿用 Phase 3.10.14，不得破坏）：

    * **不重新设计 Persistence / Repository**：Repository 保持同步，
      本模块只做线程边界，不引入 async SQLAlchemy / async driver；
    * **不共享 Session / Connection / Transaction**：每次持久化在
      worker 线程内部由 ``session_factory()`` 创建**新的 Session**
      （Repository 原实现即如此，本模块不持有任何 Session）；
    * **不使用 fire-and-forget**：``arecord()`` 必须由调用方 await
      ——没有 orphan task、没有 shutdown / 数据丢失 / 异常逃逸问题；
    * **不做 retry / sleep / backoff / queue / outbox**：失败只在
      边界内收敛为 warning，不影响 LLM 业务结果；
    * **不新增 Persistence DTO**：继续复用 ``LLMObservation`` /
      ``LLMUsage`` / ``LLMUsageRecord``；
    * **不引入第三方依赖**：只用标准库 ``asyncio``（项目已在
      SqlExecutorService / get_inventory 使用同一 ``to_thread`` 边界）。
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover —— 仅类型检查期，避免运行时循环 import
    from backend.app.llm.observability import LLMObservation
    from backend.app.services.llm_usage_persistence_service import (
        LLMUsagePersistenceService,
    )

__all__ = [
    "LLMUsagePersistenceRuntimeBridge",
]


class LLMUsagePersistenceRuntimeBridge:
    """把同步 usage 持久化移出 event loop 线程的最小 adapter。

    职责只有一个：**线程边界**。

        persist_async(observation)
            └─ await asyncio.to_thread(service.persist, observation)

    ``service.persist()`` 的全部语义（usage=None 不写入 / 字段白名单 /
    事务回滚 / 异常上抛）保持原样；本类不复制、不改写、不新增这些规则。

    线程安全（务必理解）：

        * ``asyncio.to_thread`` 每次调用把 ``service.persist`` 交给
          loop 的默认 ThreadPoolExecutor 执行；
        * **被搬运的只有 observation 数据**（frozen dataclass），不搬运
          Session / Connection / Transaction；
        * Session 由 Repository 在 **worker 线程内部**创建并关闭，
          因此不存在跨线程复用；
        * 并发请求各自进入各自的 worker thread + 各自的 Session。

    构造依赖：

        persistence_service: 已构造好的持久化服务（调用方注入，
            不在本模块内部 new —— 保持依赖显式、测试可替换 Fake）。
    """

    def __init__(
        self,
        persistence_service: "LLMUsagePersistenceService",
    ) -> None:
        """构造 runtime bridge。

        Args:
            persistence_service: 同步持久化服务（必须提供 ``persist()``）。

        Raises:
            TypeError: persistence_service 为 None 或没有 ``persist``。
        """
        if persistence_service is None:
            raise TypeError("persistence_service 不能为 None")
        persist = getattr(persistence_service, "persist", None)
        if not callable(persist):
            raise TypeError(
                "persistence_service 必须提供可调用的 persist()"
                f"（got {type(persistence_service).__name__}）"
            )
        self._service = persistence_service

    @property
    def persistence_service(self) -> "LLMUsagePersistenceService":
        """底层同步持久化服务（只读暴露，便于测试断言注入关系）。"""
        return self._service

    async def persist_async(
        self,
        observation: "LLMObservation | None",
    ) -> int | None:
        """在工作线程中执行同步持久化（调用方必须 await）。

        Args:
            observation: 一次 LLM 请求的 Observation。

        Returns:
            新记录 id；observation 为 None / usage 为 None → None
            （不写入，语义由 ``LLMUsagePersistenceService`` 决定）。

        Raises:
            Exception: 底层持久化的异常原样冒泡给 awaiter
                       （由 persistence boundary 收敛为 warning）。
        """
        # 唯一新增的一步：把同步 DB 写入搬离 event loop 线程。
        # 不使用 create_task —— 本协程必须由调用方 await，
        # 保证没有 fire-and-forget / task lifecycle 问题。
        return await asyncio.to_thread(self._service.persist, observation)
