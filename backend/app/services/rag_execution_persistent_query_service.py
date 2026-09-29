"""RAG Execution Persistent Query Service（Phase 3.12 Step 46）——只读边界。

Step 45 契约 §七 的落地实现（**不接 HTTP API**：本阶段只提供内部读边界）：

    RagService → RagExecutionObservation → Adapter → Repository
        ↓ ai_ops.rag_execution_record
    RagExecutionPersistentQueryService（本模块：Read Facade）
        └── list_by_request_id(request_id) → list[RagExecutionRecordRow]

职责边界：

* **只读**：无 create / update / delete / clear / retention；
* **不持有 Session**：只有 repository 引用；不创建 Engine / 不管理事务；
* **不合并 Runtime Collector**（避免重复记录）：只读 PostgreSQL；
* **不聚合 / 不做业务判断**：metrics 属后续阶段；
* **校验先于查询**：request_id 非法（None / "" / 纯空白 / > 128）
  → TypeError / ValueError，且**不访问 Repository**；
* **错误语义（Step 45 §六）**：
      无匹配 → ``[]``（不是错误）
      DB 故障 → ``RagExecutionRepositoryError``（**绝不**降级为空列表）
* 返回的是 Repository 的 frozen 只读 ``RagExecutionRecordRow``
  （14 字段 = 主键 + 13 契约字段；不含任何敏感字段）。
"""
from __future__ import annotations

from typing import Final

from backend.app.db.rag_execution_repository import (
    RagExecutionRecordRow,
    RagExecutionRepository,
)

__all__ = [
    "RagExecutionPersistentQueryService",
]

#: 与 ORM 列 VARCHAR(128) / assistant_trace 长度上限一致。
_REQUEST_ID_MAX_LENGTH: Final[int] = 128


def _validate_request_id(value: object) -> str:
    """读路径 request_id 校验（**触达 Repository 之前**；不生成 / 不改写）。"""
    if not isinstance(value, str):
        raise TypeError(
            "request_id 必须是 str"
            f"（当前: {type(value).__name__}）"
        )
    if not value.strip():
        raise ValueError("request_id 不能为空或纯空白")
    if len(value) > _REQUEST_ID_MAX_LENGTH:
        raise ValueError(
            "request_id 超出长度上限 "
            f"（{len(value)} > {_REQUEST_ID_MAX_LENGTH}）"
        )
    return value


class RagExecutionPersistentQueryService:
    """``ai_ops.rag_execution_record`` 的**只读**查询边界（Read Facade）。

    Args:
        repository: 数据来源；None 时构造默认 ``RagExecutionRepository()``
            （复用全局 session factory；构造期不连接数据库）。

    Note:
        本类**故意不提供**写入 / 清空能力（Read-only Boundary）：
        写入只能经 ``RagExecutionObserver``（Adapter → Service → Repository）。
    """

    def __init__(self, repository: RagExecutionRepository | None = None) -> None:
        self._repository = (
            repository if repository is not None else RagExecutionRepository()
        )

    # ---------- 只读暴露（便于装配断言） ----------

    @property
    def repository(self) -> RagExecutionRepository:
        """底层仓储（数据库访问权威）。"""
        return self._repository

    # ---------- 对外只读查询 ----------

    def list_by_request_id(
        self, request_id: str
    ) -> list[RagExecutionRecordRow]:
        """取**同一 request_id** 的全部已持久化 RAG 执行观测。

        Pipeline：

            validate request_id（Repository 之前）
                ↓
            Repository.get_by_request_id()（显式列 + 精确匹配 + id ASC）
                ↓
            list[RagExecutionRecordRow]（14 字段；无敏感信息）

        Args:
            request_id: 必填、非空（strip 后非空）、≤128 字符
                （通常是 Assistant request_id）。

        Returns:
            ``list[RagExecutionRecordRow]``；无匹配 → ``[]``（**不是错误**）。

        Raises:
            TypeError:  request_id 非 str。
            ValueError: request_id 空 / 纯空白 / 超长（均不触达 DB）。
            RagExecutionRepositoryError: DB 未配置 / 查询失败
                （**不降级为空列表**：DB 故障 ≠ 没有记录）。
        """
        validated = _validate_request_id(request_id)
        return self._repository.get_by_request_id(validated)
