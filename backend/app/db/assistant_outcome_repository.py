"""Assistant Outcome Repository（Phase 3.12 Step 64）。

最小数据访问层（与 ``llm_usage_repository`` 同风格、同基础设施）：

    create(assistant_request_id, outcome)      —— 幂等写入（first-write-wins）
    get_by_assistant_request_id(...)           —— 只读：一条终态（Trace 读边界）

复用基础设施（不新增第二套机制）：

    * Session 工厂：``backend.app.db.session.get_session_factory()``
    * 事务：``with factory() as session, session.begin():``（写）·
      ``with factory() as session:``（读，无写事务）
    * ORM：``backend.app.db.models.AssistantOutcomeRecord``

幂等 / 冲突策略（**由数据库保证**，不是应用内存）：

    assistant_request_id UNIQUE
        → ``INSERT ... ON CONFLICT (assistant_request_id) DO NOTHING RETURNING id``
        → 重复写入：无异常、无 UPDATE、返回 **None**（first-write-wins）

    为什么 first-write-wins：Outcome 是 **terminal state**；
    ``SUCCESS → FAILED`` 通常意味着重复请求 / 生命周期 bug，
    本层不做状态机 / 不做 reconciliation（如需更正另开阶段设计）。

不做的：

    * 不用 SELECT-then-INSERT（并发下仍会重复）；
    * 不用 IntegrityError + rollback 作为正常控制流；
    * 不用 upsert / DO UPDATE（禁止覆盖终态）；
    * 不做 in-memory dict / set / lock 去重（进程重启失效、跨进程无效）；
    * 不新增 update / delete / list / aggregate 方法。

失败语义：``SQLAlchemyError`` → ``AssistantOutcomeRepositoryError``（事务已回滚），
由调用方（best-effort recorder）收敛为 warning —— **绝不影响 Assistant 业务结果**。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import AssistantOutcomeRecord
from backend.app.db.session import get_session_factory

__all__ = [
    "AssistantOutcomeRepository",
    "AssistantOutcomeRepositoryError",
    "AssistantOutcomeRow",
    "ASSISTANT_OUTCOME_READ_COLUMNS",
    "ASSISTANT_REQUEST_ID_MAX_LENGTH",
    "validate_assistant_request_id",
]

#: 只读查询允许返回的字段（显式列，**不用** SELECT *）。
#:
#: ``id``（Phase 3.12 Step 66 additive）：Assistant Timeline 分组投影需要
#: **真实数据库主键**作为 ``AssistantTimelineEvent.source_id``（Step 66 §九：
#: 禁止生成 / 伪造 ID）。Assistant Trace 的 ``outcome`` 字段（枚举）语义不变。
ASSISTANT_OUTCOME_READ_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "assistant_request_id",
    "outcome",
    "created_at",
)

#: Assistant Trace ID 长度上限（与 ORM 列 ``VARCHAR(128)`` 一致）。
ASSISTANT_REQUEST_ID_MAX_LENGTH: Final[int] = 128


@dataclass(frozen=True)
class AssistantOutcomeRow:
    """Repository 层内部只读记录（**不是** ORM 对象）。

    字段严格等于 :data:`ASSISTANT_OUTCOME_READ_COLUMNS`
    （无 prompt / 无 content / 无 route / 无 error 信息）。

    ``id``（Phase 3.12 Step 66 additive）：数据库主键，供 Assistant Timeline
    的 ``source_id`` 使用（真实主键；**不生成 / 不伪造** ID）。
    Assistant Trace 的 ``outcome`` 语义（枚举 / 无记录 → ``None``）不变。
    """

    id: int
    assistant_request_id: str
    outcome: str
    created_at: datetime


class AssistantOutcomeRepositoryError(Exception):
    """Assistant Outcome 持久化 / 读取失败（事务已回滚）。

    调用方必须把它当作**可选链路失败**处理：
    写路径收敛为 warning（不影响业务结果），读路径按 Trace 既有
    502 语义呈现（不降级为 ``None`` —— ``None`` 只表示"确实没有终态记录"）。
    """


def validate_assistant_request_id(value: object) -> str:
    """校验 Assistant Trace ID（不生成 / 不截断 / 不改写）。

    写路径 / 读路径 / Persistence Service **共用同一规则**
    （单一事实来源；触达 DB 之前拦截）。
    """
    if not isinstance(value, str):
        raise ValueError(
            "assistant_request_id 必须是 str"
            f"（当前: {type(value).__name__}）"
        )
    if not value.strip():
        raise ValueError("assistant_request_id 不能为空或纯空白")
    if len(value) > ASSISTANT_REQUEST_ID_MAX_LENGTH:
        raise ValueError(
            "assistant_request_id 超出长度上限 "
            f"（{len(value)} > {ASSISTANT_REQUEST_ID_MAX_LENGTH}）"
        )
    return value


class AssistantOutcomeRepository:
    """``ai_ops.assistant_outcome_record`` 的最小仓储。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """构造仓储。

        Args:
            session_factory: Session 工厂；None 时用全局
                ``get_session_factory()``（DATABASE_URL 为空 → 操作时抛
                ``AssistantOutcomeRepositoryError``）。测试可注入自定义工厂。
        """
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise AssistantOutcomeRepositoryError(
                "DATABASE_URL 未配置，无法持久化 Assistant Outcome"
            )
        return factory

    # ---------- 写入 ----------

    def build_idempotent_insert(
        self,
        *,
        assistant_request_id: str,
        outcome: str,
    ) -> Any:
        """构造幂等 INSERT（本仓储唯一一处写入 SQL 构造点）。

        ``INSERT INTO ai_ops.assistant_outcome_record (...)``
        ``ON CONFLICT (assistant_request_id) DO NOTHING RETURNING id``

        * ``DO NOTHING``（不是 ``DO UPDATE``）：first-write-wins；
        * 写入字段严格等于 ``(assistant_request_id, outcome)``
          （``created_at`` 由数据库 ``now()`` 生成；不写 route / error 信息）。
        """
        return (
            postgresql_insert(AssistantOutcomeRecord)
            .values(
                assistant_request_id=assistant_request_id,
                outcome=outcome,
            )
            .on_conflict_do_nothing(
                index_elements=("assistant_request_id",),
            )
            .returning(AssistantOutcomeRecord.id)
        )

    def create(
        self,
        *,
        assistant_request_id: str,
        outcome: str,
    ) -> int | None:
        """**幂等**写入一条 request-level Outcome（终态）。

        Args:
            assistant_request_id: Assistant Trace ID（必填、非空、≤128）；
                Orchestrator 生成（API 层不生成第二个 ID）。
            outcome:              终态字符串（``SUCCESS`` / ``EMPTY`` /
                ``REFUSED`` / ``FAILED``）；由 Persistence Service 校验后传入，
                本层只做基本形状校验。

        Returns:
            新记录的 ``id``；``assistant_request_id`` 已存在
            （``ON CONFLICT DO NOTHING``）→ ``None``
            —— 这是**正常幂等结果，不是错误**。

        Raises:
            ValueError:                     参数非法（触达 DB 之前拦截）。
            AssistantOutcomeRepositoryError: DB 未配置或写入失败（事务已回滚）。
        """
        validated_id = validate_assistant_request_id(assistant_request_id)
        if not isinstance(outcome, str) or not outcome.strip():
            raise ValueError("outcome 必须是非空 str")
        factory = self._get_session_factory()
        statement = self.build_idempotent_insert(
            assistant_request_id=validated_id,
            outcome=outcome,
        )
        try:
            with factory() as session, session.begin():
                created_id: int | None = session.execute(
                    statement
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise AssistantOutcomeRepositoryError(
                f"Assistant Outcome 写入失败: {type(exc).__name__}"
            ) from exc
        return created_id

    # ---------- 只读（Trace 读边界） ----------

    def get_by_assistant_request_id(
        self,
        assistant_request_id: str,
    ) -> AssistantOutcomeRow | None:
        """按 Assistant Trace ID 读取终态（**无匹配 → None，不是错误**）。

        Raises:
            ValueError:                      assistant_request_id 非法。
            AssistantOutcomeRepositoryError: DB 未配置或查询失败。
        """
        validated_id = validate_assistant_request_id(assistant_request_id)
        factory = self._get_session_factory()
        columns = tuple(
            getattr(AssistantOutcomeRecord, name)
            for name in ASSISTANT_OUTCOME_READ_COLUMNS
        )
        statement = select(*columns).where(
            AssistantOutcomeRecord.assistant_request_id == validated_id
        )
        try:
            with factory() as session:
                row = session.execute(statement).one_or_none()
        except SQLAlchemyError as exc:
            raise AssistantOutcomeRepositoryError(
                f"Assistant Outcome 读取失败: {type(exc).__name__}"
            ) from exc
        if row is None:
            return None
        return AssistantOutcomeRow(
            id=row.id,
            assistant_request_id=row.assistant_request_id,
            outcome=row.outcome,
            created_at=row.created_at,
        )
