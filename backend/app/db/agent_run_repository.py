"""AgentRun Repository（Phase 5 P-2）。

最小数据访问层（与 ``assistant_outcome_repository`` / ``llm_usage_repository``
同风格、同基础设施）：

    create(...)   —— **幂等**写入一行 Agent Run（first-write-wins）

复用基础设施（不新增第二套机制）：

    * Session 工厂：``backend.app.db.session.get_session_factory()``
    * 事务：``with factory() as session, session.begin():``
    * ORM：``backend.app.db.models.AgentRun``

幂等 / 冲突策略（**由数据库保证**，不是应用内存）：

    run_id UNIQUE
        → ``INSERT ... ON CONFLICT (run_id) DO NOTHING RETURNING id``
        → 重复写入：无异常、无 UPDATE、返回 **None**（first-write-wins）

    注意：``run_id`` UNIQUE 只是 **persistence safety**
    （防止同一 run 被重复落库），**不是** Conversation Idempotency ——
    ``Idempotency-Key`` 语义仍由 ConversationRuntime 独占（Phase 4.2 Step 6 冻结）。

不做（MVP 边界）：

    * 不新增 update / delete / list / aggregate；
    * 不做 SELECT-then-INSERT / upsert / in-memory 去重。

Phase 5 P-8（Agent Read API / Timeline）新增：

    get(run_id)   —— **只读**单行查询（Read Model 的唯一 Run 入口）
                     字段 ≡ 写入字段（不新增列 / 不聚合内容 / prompt / context）；
                     显式 ``WHERE run_id = …``（run_id UNIQUE ⇒ 至多 1 行）。

失败语义：``SQLAlchemyError`` → ``AgentRunRepositoryError``（事务已回滚），
由调用方（BestEffort recorder）收敛为 warning —— **绝不影响 Agent 业务结果**。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent.recorder import AgentRunRecord
from backend.app.db.models import AgentRun
from backend.app.db.models.agent_run import (
    AGENT_RUN_CODE_MAX_LENGTH,
    AGENT_RUN_ID_MAX_LENGTH,
    AGENT_RUN_PROJECT_ID_MAX_LENGTH,
    AGENT_RUN_STATUSES,
    AGENT_RUN_STATUS_MAX_LENGTH,
)
from backend.app.db.session import get_session_factory

__all__ = [
    "AgentRunRepository",
    "AgentRunRepositoryError",
    "AgentRunRecord",
    "AGENT_RUN_STATUSES",
    "validate_agent_run_id",
    "validate_agent_status",
    "validate_stable_code",
]


class AgentRunRepositoryError(Exception):
    """Agent Run 持久化失败（事务已回滚）。

    调用方必须把它当作**可选链路失败**处理：写路径收敛为 warning，
    不改变已经产生的 Agent / Assistant 业务结果，也不触发 retry。
    """


def validate_agent_run_id(value: object, *, field: str) -> str:
    """校验 run_id / request_id（不生成 / 不截断 / 不改写）。"""
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是 str（当前: {type(value).__name__}）")
    if not value.strip():
        raise ValueError(f"{field} 不能为空或纯空白")
    if len(value) > AGENT_RUN_ID_MAX_LENGTH:
        raise ValueError(
            f"{field} 超出长度上限（{len(value)} > {AGENT_RUN_ID_MAX_LENGTH}）"
        )
    return value


def validate_agent_status(value: object) -> str:
    """校验 run 状态（**白名单**；不新增状态、不把 LIMIT_REACHED 改写成 FAILED）。"""
    if not isinstance(value, str):
        raise ValueError(f"status 必须是 str（当前: {type(value).__name__}）")
    if value not in AGENT_RUN_STATUSES:
        raise ValueError(f"status 不在允许集合内: {value!r}")
    if len(value) > AGENT_RUN_STATUS_MAX_LENGTH:
        raise ValueError(f"status 超出长度上限: {value!r}")
    return value


def validate_stable_code(value: object, *, field: str) -> str | None:
    """校验稳定码列（``error_code`` / ``limit_reason``；NULL 允许）。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是 str 或 None（当前: {type(value).__name__}）")
    if not value.strip():
        raise ValueError(f"{field} 不能为空或纯空白")
    if len(value) > AGENT_RUN_CODE_MAX_LENGTH:
        raise ValueError(
            f"{field} 超出长度上限（{len(value)} > {AGENT_RUN_CODE_MAX_LENGTH}）"
        )
    return value


def _to_record(row: Any) -> AgentRunRecord:
    """ORM 行 → ``AgentRunRecord``（**显式逐字段映射**；不透传 ORM）。"""
    return AgentRunRecord(
        run_id=row.run_id,
        request_id=row.request_id,
        project_id=row.project_id,
        status=row.status,
        error_code=row.error_code,
        limit_reason=row.limit_reason,
        step_count=row.step_count,
        tool_call_count=row.tool_call_count,
        llm_call_count=row.llm_call_count,
        token_usage_unverifiable=row.token_usage_unverifiable,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_ms=row.duration_ms,
    )


class AgentRunRepository:
    """``ai_ops.agent_run`` 的最小仓储（幂等写入 + P-8 只读单行）。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """构造仓储。

        Args:
            session_factory: Session 工厂；None 时用全局
                ``get_session_factory()``（DATABASE_URL 为空 → 操作时抛
                ``AgentRunRepositoryError``）。测试可注入自定义工厂。
        """
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise AgentRunRepositoryError("DATABASE_URL 未配置，无法持久化 Agent Run")
        return factory

    # ---------- 读取（Phase 5 P-8 Read Model） ----------

    def get(self, run_id: str) -> AgentRunRecord | None:
        """按 ``run_id`` 读取一条 Agent Run（**只读**；无 ⇒ ``None``）。

        Phase 5 P-8：Read Model 的唯一 Run 读取入口（run_id UNIQUE ⇒ 至多 1 行）。
        返回写入字段的投影；**不**读取 / 聚合内容、prompt、context、tool 明细。

        Raises:
            ValueError:              run_id 非法（触达 DB 之前拦截）。
            AgentRunRepositoryError: DB 未配置或读取失败。
        """
        validated_run_id = validate_agent_run_id(run_id, field="run_id")
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(
                    select(AgentRun).where(AgentRun.run_id == validated_run_id)
                ).scalar_one_or_none()
                if row is None:
                    return None
                return _to_record(row)
        except SQLAlchemyError as exc:
            raise AgentRunRepositoryError(
                f"Agent Run 读取失败: {type(exc).__name__}"
            ) from exc

    # ---------- 读取（Phase 7 Step 3 项目范围列表；只读） ----------

    def _validate_project_id(self, project_id: object) -> str:
        """校验授权项目标识（触达 DB 之前拦截；不生成 / 不截断）。"""
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("project_id 必须是非空 str")
        if len(project_id) > AGENT_RUN_PROJECT_ID_MAX_LENGTH:
            raise ValueError("project_id 超出长度上限")
        return project_id

    def list_by_project(
        self, *, project_id: str, limit: int, offset: int
    ) -> list[AgentRunRecord]:
        """按**授权项目**分页读取 Run 概要（Phase 7 Step 3；**只读**）。

        纪律：

        * **DB 层过滤**：``WHERE project_id = :project`` 由 SQL 执行——
          **绝不**先查全部再在 Python 过滤（项目隔离是数据边界，不是
          展示层过滤）；
        * **稳定排序**：``started_at DESC, run_id DESC``（``run_id`` UNIQUE
          作 tiebreaker ⇒ 分页窗口稳定，不依赖 DB 自然序）；
        * 分页（``limit`` / ``offset``）由调用方（服务层）校验后传入——
          本方法不再重复校验业务边界，但拒绝负数等明显非法值。

        Returns:
            该项目的 Run 记录（写入字段投影；可能为空列表）。

        Raises:
            ValueError:              project_id / limit / offset 非法。
            AgentRunRepositoryError: DB 未配置或读取失败。
        """
        project = self._validate_project_id(project_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit 必须是非负 int")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset 必须是非负 int")
        statement = (
            select(AgentRun)
            .where(AgentRun.project_id == project)
            .order_by(AgentRun.started_at.desc(), AgentRun.run_id.desc())
            .limit(limit)
            .offset(offset)
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                rows = session.execute(statement).scalars().all()
        except SQLAlchemyError as exc:
            raise AgentRunRepositoryError(
                f"Agent Run 列表读取失败: {type(exc).__name__}"
            ) from exc
        return [_to_record(row) for row in rows]

    def count_by_project(self, *, project_id: str) -> int:
        """按**授权项目**统计 Run 总数（Phase 7 Step 3；**只读**）。

        与 :meth:`list_by_project` 同一过滤条件 ⇒ ``total`` 与分页结果
        一致（可靠计数，不伪造）。

        Raises:
            ValueError:              project_id 非法。
            AgentRunRepositoryError: DB 未配置或读取失败。
        """
        project = self._validate_project_id(project_id)
        statement = (
            select(func.count())
            .select_from(AgentRun)
            .where(AgentRun.project_id == project)
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                return int(session.execute(statement).scalar_one())
        except SQLAlchemyError as exc:
            raise AgentRunRepositoryError(
                f"Agent Run 计数失败: {type(exc).__name__}"
            ) from exc

    # ---------- 写入 ----------

    def build_idempotent_insert(
        self,
        *,
        run_id: str,
        request_id: str,
        project_id: str | None,
        status: str,
        error_code: str | None,
        limit_reason: str | None,
        step_count: int,
        tool_call_count: int,
        llm_call_count: int,
        token_usage_unverifiable: bool,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: float,
    ) -> Any:
        """构造幂等 INSERT（本仓储唯一一处写入 SQL 构造点）。

        ``INSERT INTO ai_ops.agent_run (...) ON CONFLICT (run_id) DO NOTHING
        RETURNING id``

        写入字段严格等于 Decision §30.9 的字段清单（**不写** prompt / context /
        content / tool 明细 / token 计费字段）。
        """
        return (
            postgresql_insert(AgentRun)
            .values(
                run_id=run_id,
                request_id=request_id,
                project_id=project_id,
                status=status,
                error_code=error_code,
                limit_reason=limit_reason,
                step_count=step_count,
                tool_call_count=tool_call_count,
                llm_call_count=llm_call_count,
                token_usage_unverifiable=token_usage_unverifiable,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
            )
            .on_conflict_do_nothing(index_elements=("run_id",))
            .returning(AgentRun.id)
        )

    def create(
        self,
        *,
        run_id: str,
        request_id: str,
        project_id: str | None,
        status: str,
        error_code: str | None,
        limit_reason: str | None,
        step_count: int,
        tool_call_count: int,
        llm_call_count: int,
        token_usage_unverifiable: bool,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: float,
    ) -> int | None:
        """**幂等**写入一条 Agent Run（append-only）。

        Returns:
            新记录 ``id``；``run_id`` 已存在 → ``None``（**正常幂等结果，不是错误**）。

        Raises:
            ValueError:                 参数非法（触达 DB 之前拦截）。
            AgentRunRepositoryError:    DB 未配置或写入失败（事务已回滚）。
        """
        validated_run_id = validate_agent_run_id(run_id, field="run_id")
        validated_request_id = validate_agent_run_id(
            request_id, field="request_id"
        )
        if project_id is not None:
            if not isinstance(project_id, str) or not project_id.strip():
                raise ValueError("project_id 必须是非空 str 或 None")
            if len(project_id) > AGENT_RUN_PROJECT_ID_MAX_LENGTH:
                raise ValueError("project_id 超出长度上限")
        validated_status = validate_agent_status(status)
        validated_error = validate_stable_code(error_code, field="error_code")
        validated_limit = validate_stable_code(limit_reason, field="limit_reason")
        counts = {
            "step_count": step_count,
            "tool_call_count": tool_call_count,
            "llm_call_count": llm_call_count,
        }
        for name, value in counts.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} 必须是 int（当前: {type(value).__name__}）")
            if value < 0:
                raise ValueError(f"{name} 不能为负数（当前: {value}）")
        if not isinstance(token_usage_unverifiable, bool):
            raise ValueError("token_usage_unverifiable 必须是 bool")
        if not isinstance(started_at, datetime) or not isinstance(
            finished_at, datetime
        ):
            raise ValueError("started_at / finished_at 必须是 datetime")
        if finished_at < started_at:
            raise ValueError("finished_at 必须 >= started_at")
        if isinstance(duration_ms, bool) or not isinstance(
            duration_ms, (int, float)
        ):
            raise ValueError("duration_ms 必须是数字")
        if duration_ms < 0:
            raise ValueError("duration_ms 不能为负数")

        factory = self._get_session_factory()
        statement = self.build_idempotent_insert(
            run_id=validated_run_id,
            request_id=validated_request_id,
            project_id=project_id,
            status=validated_status,
            error_code=validated_error,
            limit_reason=validated_limit,
            step_count=step_count,
            tool_call_count=tool_call_count,
            llm_call_count=llm_call_count,
            token_usage_unverifiable=token_usage_unverifiable,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )
        try:
            with factory() as session, session.begin():
                created_id: int | None = session.execute(
                    statement
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise AgentRunRepositoryError(
                f"Agent Run 写入失败: {type(exc).__name__}"
            ) from exc
        return created_id
