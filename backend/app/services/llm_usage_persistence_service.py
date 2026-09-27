"""LLM Usage Persistence Boundary（Phase 3.10.14；
Phase 3.10.15 增加 Runtime Boundary）。

层次（任务书 §二十四）：

    LLM Client
        ↓
    LLMObservation
        ↓
    LLMAccountingSink
        ↓ DatabaseLLMAccountingSink（本模块）
        ↓ LLMUsagePersistenceService
        ↓ LLMUsageRepository
        ↓ PostgreSQL.llm_usage_record

Phase 3.10.15 Runtime Boundary（新增，但不是新的 persistence 层）：

    异步请求线程（event loop）
        ↓  await DatabaseLLMAccountingSink.arecord(observation)
    LLMUsagePersistenceRuntimeBridge（asyncio.to_thread → worker thread）
        ↓
    同步 LLMUsagePersistenceService / LLMUsageRepository（逻辑不变）

    * ``record()`` 同步契约保持原样（既有同步测试与调用方不变）；
    * ``arecord()`` 是新增的 **optional** async 入口，内部走线程边界，
      必须被 await（不使用 fire-and-forget / create_task）；
    * 两条入口异常语义一致：收敛为 warning，绝不改变业务结果。

为什么放在 services/（而不是 backend/app/llm/）：

    `backend/app/llm/*` 不引入 SQLAlchemy（任务书 §五）；本模块是
    LLM 领域 → 数据库之间的**适配边界**，因此 DB 依赖只出现在本模块
    与 `backend/app/db/` 内。

关键约束：

    * 复用 `consume_usage()`（Phase 3.10.13）取得 usage 事实——
      保持 identity，不复制 LLMUsage、不重算 total_tokens、
      不补全、不估算（任务书 §六）；
    * 只读取 observation 的 usage / request_id / provider / model
      ——不读 prompt / messages / SQL / RAG / tool 数据（§二十四）；
    * 不计算 cost、不聚合、不修改 observation（§二十一）；
    * usage=None → 不写入：本表是 **LLM Usage Storage**，不是
      request audit log；失败请求（usage=None）与无 usage 的成功
      请求都不产生行（§十五）；
    * 持久化失败 → warning + 业务结果不变；无 retry / sleep /
      backoff / 队列（§二十二 / §二十三）；
    * **默认不接入 Client**：`create_llm_client()` 默认仍是
      NoopAccountingSink，不会自动连数据库（§二十五）。
"""
from __future__ import annotations

import asyncio
import logging

from backend.app.db.llm_usage_repository import (
    LLMUsageRepository,
    LLMUsageRepositoryError,
)
from backend.app.llm.accounting_consumer import consume_usage
from backend.app.llm.observability import LLMObservation
from backend.app.services.llm_usage_persistence_runtime import (
    LLMUsagePersistenceRuntimeBridge,
)

logger = logging.getLogger(__name__)

__all__ = [
    "LLMUsagePersistenceService",
    "DatabaseLLMAccountingSink",
]


class LLMUsagePersistenceService:
    """LLMObservation → usage 事实 → Repository（§二十一）。

    职责：
        - 通过既有 Consumer 取 usage（identity，不改数值）
        - 传值给 repository.create()
        - **不**计算 cost / 不聚合 / 不 retry / 不修改 observation
    """

    def __init__(self, repository: LLMUsageRepository | None = None) -> None:
        """构造持久化服务。

        Args:
            repository: Usage 仓储；None 时构造默认 `LLMUsageRepository()`
                （复用全局 session factory）。测试可注入 Fake。
        """
        self._repository = (
            repository if repository is not None else LLMUsageRepository()
        )

    def persist(self, observation: LLMObservation | None) -> int | None:
        """持久化一次请求的 usage 事实。

        Args:
            observation: 一次 LLM 请求的 Observation。

        Returns:
            新记录 id；
            observation 为 None / usage 为 None 时返回 None（不写入）。

        Raises:
            LLMUsageRepositoryError: DB 未配置或写入失败（已回滚）。
        """
        if observation is None:
            return None

        # 复用 Phase 3.10.13 Consumer：identity 保持，不改任何 token 数值
        usage = consume_usage(observation)
        if usage is None:
            # 本表只保存 usage 事实 → 无 usage 不落库（不退化为 audit log）
            return None

        return self._repository.create(
            request_id=observation.request_id,
            provider=observation.provider,
            model=observation.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        )


class DatabaseLLMAccountingSink:
    """`LLMAccountingSink` 实现：把 request-level usage 事实写入数据库。

    用法（显式接入，默认不启用）：

        from backend.app.services.llm_usage_persistence_service import (
            DatabaseLLMAccountingSink,
        )
        client = create_llm_client(
            settings.llm,
            accounting_sink=DatabaseLLMAccountingSink(),
        )

    失败语义（§二十二 / §二十三）：
        record() 内部任何异常只记录 warning 后返回——
        LLM 业务结果不因 usage 持久化失败而改变，
        且不触发 retry / sleep / 队列。

    Phase 3.10.15 Runtime Boundary：

        * ``record(observation)``  —— 同步入口，契约与行为完全不变；
        * ``arecord(observation)`` —— 异步入口（optional）：经
          ``LLMUsagePersistenceRuntimeBridge`` 把**同一套同步持久化**
          放进 worker 线程执行后 await，避免阻塞 event loop；
          由 LLM Client 在 async 请求路径自动选用（getattr 探测），
          无 bridge 需求的同步调用方不受影响。
    """

    def __init__(
        self,
        persistence_service: LLMUsagePersistenceService | None = None,
        repository: LLMUsageRepository | None = None,
        runtime_bridge: LLMUsagePersistenceRuntimeBridge | None = None,
    ) -> None:
        """构造 sink。

        Args:
            persistence_service: 持久化服务；None 时按 repository 构造。
            repository:          Usage 仓储；None 时用默认仓储
                                 （persistence_service 为 None 时生效）。
            runtime_bridge:      Phase 3.10.15 异步运行时边界；None 时
                                 用默认 bridge（asyncio.to_thread →
                                 同一 persistence_service）。
        """
        self._service = (
            persistence_service
            if persistence_service is not None
            else LLMUsagePersistenceService(repository=repository)
        )
        self._bridge = (
            runtime_bridge
            if runtime_bridge is not None
            else LLMUsagePersistenceRuntimeBridge(
                persistence_service=self._service
            )
        )

    def record(self, observation: LLMObservation) -> None:
        """接收 Observation 并持久化其 usage 事实（**同步契约不变**）。

        只读取 usage / request_id / provider / model；
        不计算 cost、不聚合、不修改 observation；
        异常 → warning（绝不传播到业务链路）。

        Phase 3.10.15：既有同步入口保持 100% 向后兼容
        （仍然是同步调用、同步返回、吞掉异常）。异步请求路径请走
        :meth:`arecord` —— 两者持久化语义完全一致。
        """
        try:
            self._service.persist(observation)
        except Exception as exc:  # noqa: BLE001 —— 持久化失败绝不穿透为业务失败
            self._log_persistence_failure(exc)

    async def arecord(self, observation: LLMObservation) -> None:
        """异步入口：把**同步持久化**放进 worker 线程后 await。

        Runtime Boundary（Phase 3.10.15）：

            event loop thread
                ↓ await（必须由调用方 await；禁止 create_task / fire-and-forget）
            asyncio.to_thread(service.persist, observation)
                ↓
            worker thread：session_factory() → 新 Session → commit/rollback

        语义与 :meth:`record` 完全一致（同一 service / repository /
        字段白名单 / usage=None 不写入）。区别只在执行的线程。

        异常收敛（**不得让 persistence 影响业务结果**）：

            * LLMUsageRepositoryError → warning（事务已回滚）；
            * 其它 Exception          → warning（含 exc_info）；
            * asyncio.CancelledError  → warning 后返回
              —— 持久化期间的 caller cancellation 不得破坏**已经完成**
              的 LLM Business Result（任务书 §十八）。
        """
        try:
            await self._bridge.persist_async(observation)
        except asyncio.CancelledError:
            # 已完成的 LLM 业务结果不应被可选的持久化链路取消掉；
            # worker 线程内的写入会在自己的事务里自然结束（独立 Session）。
            logger.warning(
                "LLM usage 持久化被取消（不影响已完成的业务结果）",
                exc_info=False,
            )
        except Exception as exc:  # noqa: BLE001 —— 持久化失败绝不穿透为业务失败
            self._log_persistence_failure(exc)

    def _log_persistence_failure(self, exc: BaseException) -> None:
        """持久化故障统一降级为 warning（两条入口共用）。"""
        if isinstance(exc, LLMUsageRepositoryError):
            logger.warning(
                "LLM usage 持久化失败（不影响业务结果）: %s",
                exc,
                exc_info=False,
            )
            return
        logger.warning(
            "LLM usage 持久化失败（不影响业务结果）",
            exc_info=True,
        )
