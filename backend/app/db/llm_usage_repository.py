"""LLM Usage Repository（Phase 3.10.14）。

最小数据访问层（项目此前**没有** repository pattern，本文件是
Phase 3.10.14 引入的第一个 repository，职责刻意保持最小）：

    create(...)  —— 写入一条 request-level Usage Record

复用现有数据库基础设施（任务书 §四）：

    - Session 工厂：`backend.app.db.session.get_session_factory()`
    - 事务：`with factory() as session, session.begin():`（与
      KnowledgeIngestionService 写库路径完全一致）
    - ORM Model：`backend.app.db.models.LLMUsageRecord`

不提供（本阶段不做）：

    list_by_user() / list_by_project() / daily_usage() /
    monthly_usage() / aggregate() / sum_tokens() / sum_cost()

失败语义：SQLAlchemyError → `LLMUsageRepositoryError`（事务已回滚），
由调用方（persistence sink）转 warning，**绝不影响 LLM 业务结果**。
"""
from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import LLMUsageRecord
from backend.app.db.session import get_session_factory

__all__ = [
    "LLMUsageRepository",
    "LLMUsageRepositoryError",
]


class LLMUsageRepositoryError(Exception):
    """LLM Usage 持久化失败（事务已回滚）。

    调用方（DatabaseLLMAccountingSink）捕获后仅记录 warning：
    LLM 业务结果不因 usage 持久化失败而改变。
    """


class LLMUsageRepository:
    """`llm_usage_record` 表的最小写入仓储。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """构造仓储。

        Args:
            session_factory: Session 工厂；None 时用全局
                `get_session_factory()`（DATABASE_URL 为空 → create 时抛
                LLMUsageRepositoryError）。测试可注入自定义工厂。
        """
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise LLMUsageRepositoryError(
                "DATABASE_URL 未配置，无法持久化 LLM Usage"
            )
        return factory

    # ---------- 写入 ----------

    def create(
        self,
        *,
        request_id: str | None,
        provider: str | None,
        model: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
    ) -> int:
        """写入一条 request-level Usage Record。

        值语义由调用方（Persistence Service）保证：
        request_id / provider / model / token 字段均来自
        LLMObservation 与 LLMUsage 原值（NULL 保持 NULL，不补 0）。

        Args:
            request_id:        Provider 请求 ID（可 None）。
            provider:          Provider 标识（可 None）。
            model:             实际响应模型（可 None）。
            prompt_tokens:     Prompt token（可 None）。
            completion_tokens: Completion token（可 None）。
            total_tokens:      总 token（原样，可 None）。

        Returns:
            新记录的 `id`。

        Raises:
            LLMUsageRepositoryError: DB 未配置或写入失败（事务已回滚）。
        """
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = LLMUsageRecord(
                    request_id=request_id,
                    provider=provider,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )
                session.add(row)
                session.flush()
                created_id: int = row.id
        except SQLAlchemyError as exc:
            raise LLMUsageRepositoryError(
                f"LLM Usage 写入失败: {type(exc).__name__}"
            ) from exc
        return created_id
