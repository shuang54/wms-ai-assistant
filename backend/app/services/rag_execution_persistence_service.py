"""RAG Execution Persistence Service（Phase 3.12 Step 46）。

Application 层持久化操作（**最薄的一层**；严格复刻
``ToolExecutionPersistenceService`` / ``LLMUsagePersistenceService`` 的形态）：

    RagExecutionObservation（frozen；13 字段）
        ↓ persist(observation)
    RagExecutionRepository.create(observation)
        ↓
    ai_ops.rag_execution_record

职责边界：

    * **输入就是** ``RagExecutionObservation`` —— 不转 dict / 不经 Snapshot；
    * 不做字段映射（映射已在 Repository 内逐字段显式完成）；
    * 不做业务校验（校验权威在 DTO 自身）；不重新计算 duration / 计数；
    * 不做 aggregation / metrics / 序列化 / 安全策略；
    * **不吞异常**：``RagExecutionRepositoryError`` 原样上抛，
      由 Adapter（infrastructure boundary）收敛为 warning ——
      与 Tool / LLM Usage 的"Service 抛 / Adapter 收"分工一致；
    * 无 retry / sleep / backoff / queue / outbox / 后台线程 / 批量写入：
      一次 RAG 执行 = 一次同步单行写入。

分层（与 Tool / LLM 一致）：

    Adapter     = infrastructure boundary（Observer 实现 + 失败隔离）
    Service     = application persistence operation（本模块）
    Repository  = database access
"""
from __future__ import annotations

from backend.app.db.rag_execution_repository import (
    RagExecutionRecordRow,
    RagExecutionRepository,
)
from backend.app.services.rag_execution_observation import (
    RagExecutionObservation,
)

__all__ = [
    "RagExecutionPersistenceService",
]


class RagExecutionPersistenceService:
    """``RagExecutionObservation`` → Repository（一次同步单行写入）。"""

    def __init__(
        self,
        repository: RagExecutionRepository | None = None,
    ) -> None:
        """构造持久化服务。

        Args:
            repository: RAG Execution 仓储；None 时构造默认
                ``RagExecutionRepository()``（复用全局 session factory；
                **构造期不连接数据库** —— DATABASE_URL 未配置时在
                ``persist()`` 才抛 ``RagExecutionRepositoryError``）。
                测试可注入 Fake。
        """
        self._repository = (
            repository
            if repository is not None
            else RagExecutionRepository()
        )

    # ---------- 只读暴露（便于测试 / 装配断言） ----------

    @property
    def repository(self) -> RagExecutionRepository:
        """底层仓储（数据库访问权威）。"""
        return self._repository

    # ---------- 对外 Contract ----------

    def persist(
        self, observation: RagExecutionObservation
    ) -> RagExecutionRecordRow:
        """持久化一条 RAG 执行观测（**原样写入**）。

        Args:
            observation: ``RagExecutionObservation``（frozen；13 字段已校验）。

        Returns:
            ``RagExecutionRecordRow``（含数据库主键 id；内部只读 record）。

        Raises:
            TypeError: ``observation`` 类型非法。
            RagExecutionRepositoryError: DB 未配置 / 写入失败（原样上抛，
                由 Adapter 收敛为 warning）。
        """
        return self._repository.create(observation)
