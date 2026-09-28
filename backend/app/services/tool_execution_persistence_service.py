"""Tool Execution Persistence Service（Phase 3.11 Step 28）。

Application 层持久化操作（**最薄的一层**）：

    ToolExecutionRecord（内部执行事实；frozen；11 字段）
        ↓ persist(record)
    ToolExecutionRepository.create(record)          ← Step 27
        ↓
    ai_ops.tool_execution_record

职责边界（严格复刻 ``LLMUsagePersistenceService`` 的形态）：

    * **输入就是** ``ToolExecutionRecord`` —— 不转 dict / 不转 JSON /
      不经 Snapshot（Step 26 ADR：Record 是内部执行事实，
      Snapshot 是对外 Read Model，二者不得混用）；
    * 不做字段映射（映射已在 Repository 内逐字段显式完成）；
    * 不做业务校验（校验权威在 Record 自身）；
    * 不做 aggregation / metrics / 序列化 / 安全策略；
    * **不吞异常**：``ToolExecutionRepositoryError`` 原样上抛，
      由 Adapter（infrastructure boundary）收敛为 warning ——
      与 LLM Usage 的 "PersistenceService 抛 / Sink 收" 分工一致；
    * 无 retry / sleep / backoff / queue / outbox / 后台线程 /
      异步 / 批量写入：一次 Tool Execution = 一次同步单行写入。

分层（Step 26 ADR / Step 28 §六）：

    Adapter     = infrastructure boundary（Observer 实现 + 失败隔离）
    Service     = application persistence operation（本模块）
    Repository  = database access（Step 27）
"""
from __future__ import annotations

from backend.app.db.tool_execution_repository import (
    ToolExecutionRecordRow,
    ToolExecutionRepository,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord

__all__ = [
    "ToolExecutionPersistenceService",
]


class ToolExecutionPersistenceService:
    """``ToolExecutionRecord`` → Repository（一次同步单行写入）。"""

    def __init__(
        self,
        repository: ToolExecutionRepository | None = None,
    ) -> None:
        """构造持久化服务。

        Args:
            repository: Tool Execution 仓储；None 时构造默认
                ``ToolExecutionRepository()``（复用全局 session factory；
                **构造期不连接数据库** —— DATABASE_URL 未配置时在
                ``persist()`` 才抛 ``ToolExecutionRepositoryError``）。
                测试可注入 Fake。
        """
        self._repository = (
            repository if repository is not None else ToolExecutionRepository()
        )

    # ---------- 只读暴露（便于测试 / 装配断言） ----------

    @property
    def repository(self) -> ToolExecutionRepository:
        """底层仓储（数据库访问权威）。"""
        return self._repository

    # ---------- 对外 Contract ----------

    def persist(self, record: ToolExecutionRecord) -> ToolExecutionRecordRow:
        """持久化一条 Tool 执行事实（**原样写入 Record**）。

        Args:
            record: ``ToolExecutionRecord``（frozen；Step 14 契约已校验）。

        Returns:
            ``ToolExecutionRecordRow``（含数据库主键 id；内部只读 record，
            不返回 ORM 对象）。

        Raises:
            ToolExecutionRepositoryError: DB 未配置或写入失败（已回滚）。
                调用方（Adapter）负责收敛为 warning，绝不影响
                Tool 执行结果。
        """
        return self._repository.create(record)
