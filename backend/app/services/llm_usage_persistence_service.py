"""LLM Usage Persistence Boundary（Phase 3.10.14）。

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

import logging

from backend.app.db.llm_usage_repository import (
    LLMUsageRepository,
    LLMUsageRepositoryError,
)
from backend.app.llm.accounting_consumer import consume_usage
from backend.app.llm.observability import LLMObservation

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
    """

    def __init__(
        self,
        persistence_service: LLMUsagePersistenceService | None = None,
        repository: LLMUsageRepository | None = None,
    ) -> None:
        """构造 sink。

        Args:
            persistence_service: 持久化服务；None 时按 repository 构造。
            repository:          Usage 仓储；None 时用默认仓储
                                 （persistence_service 为 None 时生效）。
        """
        self._service = (
            persistence_service
            if persistence_service is not None
            else LLMUsagePersistenceService(repository=repository)
        )

    def record(self, observation: LLMObservation) -> None:
        """接收 Observation 并持久化其 usage 事实。

        只读取 usage / request_id / provider / model；
        不计算 cost、不聚合、不修改 observation；
        异常 → warning（绝不传播到业务链路）。
        """
        try:
            self._service.persist(observation)
        except LLMUsageRepositoryError as exc:
            logger.warning(
                "LLM usage 持久化失败（不影响业务结果）: %s",
                exc,
                exc_info=False,
            )
        except Exception:  # noqa: BLE001 —— 持久化失败绝不穿透为业务失败
            logger.warning(
                "LLM usage 持久化失败（不影响业务结果）",
                exc_info=True,
            )
