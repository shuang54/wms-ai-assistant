"""Assistant Outcome Query Service（Phase 3.12 Step 64）——只读读边界。

Trace 读路径（Step 64 §二十 / §二十二）：

    AssistantTraceQueryService.get_trace(A)
        ├── LLMUsageQueryService.list_by_assistant_request_id(A)
        ├── Tool Persistent 读边界.list_by_request_id(A)
        ├── RAG Persistent 读边界.list_by_request_id(A)
        └── AssistantOutcomeQueryService.get_by_assistant_request_id(A)   ← 本模块
                ↓
            ai_ops.assistant_outcome_record（request-level 终态）

契约：

* 返回 ``AssistantOutcome | None``；
* **``None`` 不猜**（Step 64 §二十一）：历史 Trace（本表上线前的请求）
  没有终态行 → ``None``。**绝不**根据 LLM usage / Tool / RAG / HTTP status /
  route 推断 SUCCESS / FAILED；
* 库中若出现未知字符串（人为写入 / 数据损坏）→ ``ValueError``
  （不降级为 None：None 只表示"确实没有记录"）；
* 只读：无 create / update / delete；不访问 SQLAlchemy Session / ORM Model
  （Repository 已把它们收在 db 层，本层只依赖其只读方法）。

不做：不聚合、不统计、不缓存、不做状态机、不引入 error_class。
"""
from __future__ import annotations

from typing import Any

from backend.app.db.assistant_outcome_repository import (
    AssistantOutcomeRepository,
)
from backend.app.dto.assistant_outcome import AssistantOutcome

__all__ = ["AssistantOutcomeQueryService"]


class AssistantOutcomeQueryService:
    """Assistant 终态只读查询（供 Assistant Trace Read Model 组合）。"""

    def __init__(self, repository: AssistantOutcomeRepository | None = None) -> None:
        """构造查询服务。

        Args:
            repository: Outcome 仓储；None → 默认仓储（构造期不连接数据库；
                查询时才解析 Session 工厂）。
        """
        self._repository = (
            repository if repository is not None else AssistantOutcomeRepository()
        )

    @property
    def repository(self) -> AssistantOutcomeRepository:
        """底层仓储（只读暴露，便于测试断言注入关系）。"""
        return self._repository

    def get_by_assistant_request_id(
        self,
        assistant_request_id: str,
    ) -> AssistantOutcome | None:
        """读取该 Assistant 请求的终态（无记录 → ``None``）。

        Raises:
            ValueError:                      assistant_request_id 非法，
                                             或库中出现未知 outcome 值。
            AssistantOutcomeRepositoryError: DB 未配置或查询失败
                                             （Trace 层按既有 502 语义呈现，
                                              **不**降级为 None）。
        """
        row: Any = self._repository.get_by_assistant_request_id(
            assistant_request_id
        )
        if row is None:
            return None
        try:
            return AssistantOutcome(row.outcome)
        except ValueError as exc:
            raise ValueError(
                "assistant_outcome_record.outcome 值非法（非四态之一）"
            ) from exc
