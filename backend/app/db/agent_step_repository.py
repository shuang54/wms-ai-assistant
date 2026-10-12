"""AgentStep Repository（Phase 5 — AgentStep Persistence）。

最小数据访问层（与 ``agent_run_repository`` / ``assistant_outcome_repository``
同风格、同基础设施）：

    create(...)        —— **幂等**写入一行 Step（first-write-wins）
    create_many(...)   —— **单事务**批量写入（减少 N 次 DB round trip）

复用基础设施（不新增第二套机制）：

    * Session 工厂：``backend.app.db.session.get_session_factory()``
    * 事务：``with factory() as session, session.begin():``（一次 begin = 一批）
    * ORM：``backend.app.db.models.AgentStep``

幂等 / 冲突策略（**由数据库保证**，不是应用内存）：

    **UNIQUE(run_id, step_index)**
        → ``INSERT ... ON CONFLICT (run_id, step_index) DO NOTHING RETURNING id``
        → 重复写入：无异常、无 UPDATE、返回 **None** / 空列表（first-write-wins）

    注意：这只是 **Persistence safety**（防止同一 Step 重复落库），
    **不是** Conversation ``Idempotency-Key``，**也不是** Resume Idempotency
    （Resume 尚未实现；未来需要 state_version / claim_token，属独立 Decision）。

不做（本阶段边界）：

    * 不新增 update / delete / **find** / list / timeline（Read Model 属后续阶段）；
    * 不做 SELECT-then-INSERT / upsert / in-memory 去重；
    * 不引入 Unit of Work / 跨表事务（Step 与 Run 均为 best-effort 独立写入）。

失败语义：``SQLAlchemyError`` → ``AgentStepRepositoryError``（事务已回滚），
由调用方（BestEffort recorder）收敛为 warning —— **绝不影响 Agent 业务结果**。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models import AgentStep
from backend.app.db.models.agent_step import (
    AGENT_STEP_ERROR_CODE_MAX_LENGTH,
    AGENT_STEP_RUN_ID_MAX_LENGTH,
    AGENT_STEP_STATUSES,
    AGENT_STEP_STATUS_MAX_LENGTH,
    AGENT_STEP_TOOL_NAME_MAX_LENGTH,
    AGENT_STEP_TYPES,
    AGENT_STEP_TYPE_MAX_LENGTH,
)
from backend.app.db.session import get_session_factory

__all__ = [
    "AgentStepRepository",
    "AgentStepRepositoryError",
    "AgentStepRow",
    "AgentStepRead",
    "AGENT_STEP_TYPES",
    "AGENT_STEP_STATUSES",
    "validate_step_type",
    "validate_step_status",
    "validate_step_index",
    "validate_elapsed_ms",
]

#: Step 数量上限（一次 Run 的有界保护；Runtime 预算 ≤ 8 步，留足余量）。
MAX_STEPS_PER_BATCH: Final[int] = 64


class AgentStepRepositoryError(Exception):
    """Agent Step 持久化失败（事务已回滚）。

    调用方必须把它当作**可选链路失败**处理：写路径收敛为 warning，
    不改变已经产生的 Agent / Assistant 业务结果，也不触发 retry。
    """


@dataclass(frozen=True)
class AgentStepRow:
    """Repository 层写入行（frozen；字段 ≡ Decision/任务书 最小字段集）。

    注意：**不含** arguments / output / prompt / tool result / request_id /
    tool_call_id / provider / tokens（分别属不落盘或既有 observation 表）。
    """

    run_id: str
    step_index: int
    step_type: str
    status: str
    tool_name: str | None = None
    error_code: str | None = None
    elapsed_ms: float | None = None


@dataclass(frozen=True)
class AgentStepRead:
    """Step 的**只读**投影（Phase 5 P-8 Read Model）。

    比写入行 ``AgentStepRow`` 多 ``created_at``（Timeline 排序 / ``occurred_at``
    的唯一时间来源）；字段仍严格限于已落盘列 —— **不含** arguments / output /
    prompt / tool result 正文。
    """

    run_id: str
    step_index: int
    step_type: str
    status: str
    tool_name: str | None
    error_code: str | None
    elapsed_ms: float | None
    created_at: datetime


def _to_read(row: Any) -> AgentStepRead:
    """ORM 行 → ``AgentStepRead``（**显式逐字段映射**；不透传 ORM）。"""
    return AgentStepRead(
        run_id=row.run_id,
        step_index=row.step_index,
        step_type=row.step_type,
        status=row.status,
        tool_name=row.tool_name,
        error_code=row.error_code,
        elapsed_ms=row.elapsed_ms,
        created_at=row.created_at,
    )


def validate_step_type(value: object) -> str:
    """校验 step_type（**白名单**：FINAL / TOOL_CALL；不新增类型）。"""
    if not isinstance(value, str):
        raise ValueError(f"step_type 必须是 str（当前: {type(value).__name__}）")
    if value not in AGENT_STEP_TYPES:
        raise ValueError(f"step_type 不在允许集合内: {value!r}")
    if len(value) > AGENT_STEP_TYPE_MAX_LENGTH:
        raise ValueError(f"step_type 超出长度上限: {value!r}")
    return value


def validate_step_status(value: object) -> str:
    """校验 status（**白名单**：OK / FAILED；不新增状态）。"""
    if not isinstance(value, str):
        raise ValueError(f"status 必须是 str（当前: {type(value).__name__}）")
    if value not in AGENT_STEP_STATUSES:
        raise ValueError(f"status 不在允许集合内: {value!r}")
    if len(value) > AGENT_STEP_STATUS_MAX_LENGTH:
        raise ValueError(f"status 超出长度上限: {value!r}")
    return value


def validate_step_index(value: object) -> int:
    """校验 step_index（int >= 0；拒绝 bool / float / 负数）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"step_index 必须是 int（当前: {type(value).__name__}）")
    if value < 0:
        raise ValueError(f"step_index 必须 >= 0（当前: {value}）")
    return value


def validate_elapsed_ms(value: object) -> float | None:
    """校验 elapsed_ms（``None`` 或有限且 >= 0 的数值；拒绝 NaN / Infinity）。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"elapsed_ms 必须是数字或 None（当前: {type(value).__name__}）")
    if not math.isfinite(float(value)):
        raise ValueError("elapsed_ms 必须是有限数值（禁止 NaN / Infinity）")
    if float(value) < 0:
        raise ValueError(f"elapsed_ms 不能为负数（当前: {value}）")
    return float(value)


class AgentStepRepository:
    """``ai_ops.agent_step`` 的最小仓储（MVP：幂等写入，无读模型）。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """构造仓储。

        Args:
            session_factory: Session 工厂；None 时用全局
                ``get_session_factory()``（DATABASE_URL 为空 → 操作时抛
                ``AgentStepRepositoryError``）。测试可注入自定义工厂。
        """
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise AgentStepRepositoryError("DATABASE_URL 未配置，无法持久化 Agent Step")
        return factory

    # ---------- 校验 ----------

    @staticmethod
    def _validated_values(row: AgentStepRow) -> dict[str, Any]:
        """逐字段校验并归一为 INSERT values（**单一校验点**）。"""
        run_id = row.run_id
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空 str")
        if len(run_id) > AGENT_STEP_RUN_ID_MAX_LENGTH:
            raise ValueError(
                f"run_id 超出长度上限（{len(run_id)} > {AGENT_STEP_RUN_ID_MAX_LENGTH}）"
            )
        if row.tool_name is not None:
            if not isinstance(row.tool_name, str) or not row.tool_name.strip():
                raise ValueError("tool_name 必须是非空 str 或 None")
            if len(row.tool_name) > AGENT_STEP_TOOL_NAME_MAX_LENGTH:
                raise ValueError("tool_name 超出长度上限")
        if row.error_code is not None:
            if not isinstance(row.error_code, str) or not row.error_code.strip():
                raise ValueError("error_code 必须是非空 str 或 None")
            if len(row.error_code) > AGENT_STEP_ERROR_CODE_MAX_LENGTH:
                raise ValueError("error_code 超出长度上限")
        return {
            "run_id": run_id,
            "step_index": validate_step_index(row.step_index),
            "step_type": validate_step_type(row.step_type),
            "status": validate_step_status(row.status),
            "tool_name": row.tool_name,
            "error_code": row.error_code,
            "elapsed_ms": validate_elapsed_ms(row.elapsed_ms),
        }

    # ---------- 读取（Phase 5 P-8 Read Model） ----------

    def list_for_run(self, run_id: str) -> list[AgentStepRead]:
        """该 Run 的**全部** Step（按 ``step_index`` 升序、``id`` 兜底）。

        Phase 5 P-8：Read Model 列表查询（**只读**；显式 ORDER BY ⇒ 不依赖 DB
        自然顺序）；一次查询取全量（Run 内 Step 有界，避免 N+1）。

        Raises:
            ValueError:               run_id 非法（触达 DB 之前拦截）。
            AgentStepRepositoryError: DB 未配置或读取失败。
        """
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空 str")
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                rows = list(
                    session.execute(
                        select(AgentStep)
                        .where(AgentStep.run_id == run_id)
                        .order_by(AgentStep.step_index.asc(), AgentStep.id.asc())
                    ).scalars()
                )
                return [_to_read(row) for row in rows]
        except SQLAlchemyError as exc:
            raise AgentStepRepositoryError(
                f"Agent Step 读取失败: {type(exc).__name__}"
            ) from exc

    # ---------- 写入 ----------

    def build_idempotent_insert(self, rows: Sequence[AgentStepRow]) -> Any:
        """构造幂等 INSERT（本仓储唯一一处写入 SQL 构造点）。

        ``INSERT INTO ai_ops.agent_step (...) VALUES (...) [, (...)]
          ON CONFLICT (run_id, step_index) DO NOTHING RETURNING id``

        * 一行与多行共用同一语句（多行 = 单次 round trip）；
        * 写入字段严格等于最小字段集（**不写** arguments / output / prompt /
          tool result / request_id / tool_call_id）。
        """
        values = [self._validated_values(row) for row in rows]
        return (
            postgresql_insert(AgentStep)
            .values(values)
            .on_conflict_do_nothing(index_elements=("run_id", "step_index"))
            .returning(AgentStep.id)
        )

    def create(self, row: AgentStepRow) -> int | None:
        """**幂等**写入一行 Step。

        Returns:
            新记录 ``id``；``(run_id, step_index)`` 已存在 → ``None``
            （**正常幂等结果，不是错误**）。

        Raises:
            ValueError:               参数非法（触达 DB 之前拦截）。
            AgentStepRepositoryError: DB 未配置或写入失败（事务已回滚）。
        """
        created = self.create_many((row,))
        return created[0] if created else None

    def create_many(self, rows: Sequence[AgentStepRow]) -> list[int]:
        """**单事务 / 单语句**批量幂等写入。

        Args:
            rows: Step 行序列（空序列 → 直接返回 ``[]``，不触达 DB）。

        Returns:
            本次**实际新建**的 id 列表（冲突跳过的行不出现；顺序 = 输入顺序）。

        Raises:
            ValueError:               参数非法（含空 run_id / 非法类型等）。
            AgentStepRepositoryError: DB 未配置或写入失败（事务已回滚）。
        """
        if not rows:
            return []
        if len(rows) > MAX_STEPS_PER_BATCH:
            raise ValueError(
                f"单批 Step 数量超出上限（{len(rows)} > {MAX_STEPS_PER_BATCH}）"
            )
        factory = self._get_session_factory()
        statement = self.build_idempotent_insert(rows)
        try:
            with factory() as session, session.begin():
                created_ids = list(session.execute(statement).scalars())
        except SQLAlchemyError as exc:
            raise AgentStepRepositoryError(
                f"Agent Step 写入失败: {type(exc).__name__}"
            ) from exc
        return created_ids

    # 说明：本阶段**不提供**任何读方法（无 find_steps / list_steps / timeline）
    # —— Read Model 属后续阶段；测试直接经 SQLAlchemy 查询（不改生产读边界）。
