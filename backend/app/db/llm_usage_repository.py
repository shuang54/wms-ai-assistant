"""LLM Usage Repository（Phase 3.10.14；Phase 3.10.16 增加幂等写入）。

最小数据访问层（项目此前**没有** repository pattern，本文件是
Phase 3.10.14 引入的第一个 repository，职责刻意保持最小）：

    create(...)  —— 写入一条 request-level Usage Record（幂等）

复用现有数据库基础设施（任务书 §四）：

    - Session 工厂：`backend.app.db.session.get_session_factory()`
    - 事务：`with factory() as session, session.begin():`（与
      KnowledgeIngestionService 写库路径完全一致）
    - ORM Model：`backend.app.db.models.LLMUsageRecord`

Phase 3.10.16 幂等语义（由**数据库**保证，不是应用内存）：

    request_id IS NOT NULL
        → UNIQUE PARTIAL INDEX 约束一个 request_id 最多一行
        → 写入用 `INSERT ... ON CONFLICT (request_id)
            WHERE request_id IS NOT NULL DO NOTHING RETURNING id`
        → 重复一次 Overservation：无异常、无 UPDATE、**返回 None**
          （first-write-wins，已有行保持不变）

    request_id IS NULL
        → partial index 不覆盖 NULL → 每次写入都产生新行
          （有意设计：NULL = 没有幂等身份）

不做的（§六 ~ §十二 / §二十四）：

    * 不用 SELECT-then-INSERT 做幂等（并发下仍会重复）；
    * 不用 IntegrityError + rollback 作为正常控制流；
    * 不用 DO UPDATE / upsert（禁止覆盖已有 usage 事实）；
    * 不在本层 / runtime bridge / service 里放 in-memory set / dict /
      lock 做去重（进程重启失效且无法跨进程）。

不提供（本阶段不做）：

    list_by_user() / list_by_project() / daily_usage() /
    monthly_usage() / aggregate() / sum_tokens() / sum_cost()

失败语义：SQLAlchemyError → `LLMUsageRepositoryError`（事务已回滚），
由调用方（persistence sink）转 warning，**绝不影响 LLM 业务结果**。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import LLMUsageRecord
from backend.app.db.models.llm_usage_record import LLM_USAGE_REQUEST_ID_PREDICATE
from backend.app.db.session import get_session_factory

__all__ = [
    "LLMUsageRepository",
    "LLMUsageRepositoryError",
]

#: ON CONFLICT target —— 必须与 partial unique index 的谓词一致。
_REQUEST_ID_CONFLICT_TARGET = ("request_id",)
_REQUEST_ID_INDEX_WHERE = text(LLM_USAGE_REQUEST_ID_PREDICATE)


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

    def build_idempotent_insert(
        self,
        *,
        request_id: str | None,
        provider: str | None,
        model: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
    ) -> Any:
        """构造幂等 INSERT（本仓储唯一一处写入 SQL 构造点）。

        INSERT INTO ai_ops.llm_usage_record (...)
        ON CONFLICT (request_id) WHERE request_id IS NOT NULL
        DO NOTHING
        RETURNING id

        * `DO NOTHING`（不是 `DO UPDATE`）：first-write-wins，
          重复写入不得覆盖已有 provider / model / token；
        * `index_where` 必须与 partial unique index 谓词一致；
        * 写入字段严格等于 `_WRITE_FIELDS`（§二十三）。
        """
        return (
            postgresql_insert(LLMUsageRecord)
            .values(
                request_id=request_id,
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )
            .on_conflict_do_nothing(
                index_elements=_REQUEST_ID_CONFLICT_TARGET,
                index_where=_REQUEST_ID_INDEX_WHERE,
            )
            .returning(LLMUsageRecord.id)
        )

    def create(
        self,
        *,
        request_id: str | None,
        provider: str | None,
        model: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
    ) -> int | None:
        """**幂等**写入一条 request-level Usage Record。

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
            新记录的 `id`；
            request_id 已存在（ON CONFLICT DO NOTHING）→ `None`
            —— 这是**正常幂等结果，不是错误**，调用方不得把它当失败。

        Raises:
            LLMUsageRepositoryError: DB 未配置或写入失败（事务已回滚）。
        """
        factory = self._get_session_factory()
        statement = self.build_idempotent_insert(
            request_id=request_id,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        try:
            with factory() as session, session.begin():
                created_id: int | None = session.execute(
                    statement
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise LLMUsageRepositoryError(
                f"LLM Usage 写入失败: {type(exc).__name__}"
            ) from exc
        return created_id
