"""RAG Execution Persistence Adapter（Phase 3.12 Step 46）。

``RagExecutionObserver``（Step 43 端口）的**持久化实现** ——
infrastructure boundary：把观测交给 Persistence Service，并把失败
收敛为 warning（严格复刻 ``ToolExecutionPersistenceAdapter``）：

    RagService._finish_observation()
        ↓ observer.record(observation)
    RagExecutionPersistenceAdapter.record()
        ↓
    RagExecutionPersistenceService.persist()
        ↓
    RagExecutionRepository.create()
        ↓
    ai_ops.rag_execution_record

失败隔离（Step 43/44 契约，本层是**最后一道**保障）：

    * ``record()`` **不抛出**（除 BaseException）：写入失败收敛为 warning；
      RAG 业务结果 / ``/api/ai/chat`` 响应不受影响；
    * **不静默**：warning 必须带 ``error_type``（异常类名）与 ``request_id``
      （可关联）；**不**使用 ``logger.exception``（不打印 traceback）；
    * 日志白名单：只允许 ``request_id`` 等安全字段 —— **绝不**记录
      query / answer / content / SQL / credentials / DATABASE_URL；
    * 无 retry / queue / 后台线程；不缓存 / 不聚合。
"""
from __future__ import annotations

import logging

from backend.app.db.rag_execution_repository import (
    RagExecutionRecordRow,
)
from backend.app.services.rag_execution_observation import (
    RagExecutionObservation,
)
from backend.app.services.rag_execution_persistence_service import (
    RagExecutionPersistenceService,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RagExecutionPersistenceAdapter",
]


def _safe_log_context(observation: object) -> dict[str, object]:
    """日志上下文白名单（只取允许字段；取值失败不影响日志）。"""
    context: dict[str, object] = {}
    for name in ("request_id",):
        try:
            value = getattr(observation, name, None)
        except Exception:  # noqa: BLE001 —— 取值失败不得引发新异常
            value = None
        if value is not None:
            context[name] = value
    return context


class RagExecutionPersistenceAdapter:
    """``RagExecutionObserver`` 实现：把 Observation 交给 Persistence Service。"""

    def __init__(
        self,
        persistence_service: RagExecutionPersistenceService | None = None,
        *,
        repository: object | None = None,
    ) -> None:
        """构造 Adapter。

        Args:
            persistence_service: 持久化服务；None 时按 ``repository`` 构造
                （默认 ``RagExecutionPersistenceService()``）。
            repository: 可选仓储（透传给默认 service；便于测试 / 装配）。
        """
        self._service = (
            persistence_service
            if persistence_service is not None
            else RagExecutionPersistenceService(repository=repository)  # type: ignore[arg-type]
        )

    # ---------- 只读暴露（便于装配断言） ----------

    @property
    def persistence_service(self) -> RagExecutionPersistenceService:
        """底层持久化服务（application persistence operation）。"""
        return self._service

    # ---------- Observer Contract ----------

    def record(self, observation: RagExecutionObservation) -> None:
        """接收一条观测并尝试持久化（失败**绝不**传播）。

        Args:
            observation: ``RagExecutionObservation``（frozen；只读）。

        Returns:
            ``RagExecutionRecordRow``（成功时；便于测试断言行已落库）；
            失败时返回 ``None``（已被隔离）。

        Note:
            本方法**不抛出**（除 BaseException）：写入失败收敛为 warning
            日志；RAG 执行结果不受影响（Step 43/44 契约）。
        """
        try:
            return self._service.persist(observation)
        except Exception as exc:  # noqa: BLE001 —— 持久化失败绝不穿透
            logger.warning(
                "RAG execution 持久化失败（不影响 RAG 结果）",
                extra={
                    **_safe_log_context(observation),
                    "error_type": type(exc).__name__,
                },
            )
            return None

    def persist_or_none(
        self, observation: RagExecutionObservation
    ) -> RagExecutionRecordRow | None:
        """显式别名（与 ``record`` 同语义；便于调用方表达"允许失败"）。

        仅用于内部装配 / 测试可读性；Observer 契约入口仍是 ``record()``。
        """
        return self.record(observation)
