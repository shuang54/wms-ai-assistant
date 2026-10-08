"""Agent Approval Repository（Phase 5 P-7 — HITL Tool Approval）。

最小数据访问层（与 agent_decision / agent_checkpoint 同风格）：

```text
create(...)                 —— 幂等写入（重复请求 ⇒ 返回**已存在**的同一行）
get(approval_id)            —— 按审批身份读取
get_for_step(run_id, step)  —— 按执行身份读取（Resume 主路径）
resolve(...)                —— **条件 UPDATE**：PENDING → APPROVED | REJECTED
delete_for_test(...)        —— **仅**测试 / cleanup（生产路径无 delete）
```

幂等 / 冲突策略（**由数据库保证**）：

    * ``UNIQUE(run_id, step_index)`` ⇒ ``INSERT … ON CONFLICT DO NOTHING RETURNING``；
      重复请求不产生第二行（返回既有行 —— 审批请求是**幂等**的）；
    * ``resolve`` 用 ``UPDATE … WHERE approval_id = ? AND status = 'PENDING'``：
      无行被更新 ⇒ 返回 ``None``（**绝不**覆盖终态；调用方判定幂等 / conflict，
      §十八 / §十九 / §五十一）；
    * 终态不可逆：``APPROVED`` / ``REJECTED`` 一旦写入，任何 UPDATE 都不可能命中。

写入前校验（触达 DB 之前拦截；§十 / §十一 / §十三）：

    approval_id 1..128 · run_id 1..64 · step_index >= 0 ·
    tool_name 1..128 · tool_call_id <= 128（或 None）·
    decision ∈ {APPROVED, REJECTED}（**不允许** retry / modify / edit_tool /
    change_arguments / change_tool）· reason_code（可选，稳定码，<= 32）

失败语义：``SQLAlchemyError`` → ``AgentApprovalRepositoryError``（事务已回滚）；
由 ``AgentApprovalStore`` 向上暴露为 fail-closed（``None`` / 异常）。
"""
from __future__ import annotations

from typing import Any, Final

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent.approval import (
    APPROVAL_STATUS_PENDING,
    is_valid_approval_decision,
)
from backend.app.db.models.agent_approval import (
    AGENT_APPROVAL_DECISION_MAX_LENGTH,
    AGENT_APPROVAL_ID_MAX_LENGTH,
    AGENT_APPROVAL_RUN_ID_MAX_LENGTH,
    AGENT_APPROVAL_TOOL_CALL_ID_MAX_LENGTH,
    AGENT_APPROVAL_TOOL_NAME_MAX_LENGTH,
    AgentApproval as AgentApprovalModel,
)
from backend.app.db.session import get_session_factory

__all__ = [
    "AgentApprovalRepository",
    "AgentApprovalRepositoryError",
    "validate_step_index",
    "validate_decision",
]


class AgentApprovalRepositoryError(Exception):
    """Approval 持久化失败（事务已回滚；由 Store 转换为 fail-closed 错误）。"""


def validate_step_index(value: object) -> int:
    """校验 step_index（int >= 0；拒绝 bool / float / 负数）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"step_index 必须是 int（当前: {type(value).__name__}）")
    if value < 0:
        raise ValueError(f"step_index 必须 >= 0（当前: {value}）")
    return value


def validate_decision(value: object) -> str:
    """校验裁决值（白名单：APPROVED / REJECTED；§十三）。"""
    if not is_valid_approval_decision(value):
        raise ValueError(
            "decision 只接受 APPROVED / REJECTED"
            f"（当前: {value!r}；不允许 retry / modify / change_arguments）"
        )
    return str(value)


def _optional_text(value: object, *, field: str, max_length: int) -> str | None:
    """可选非空文本（None 或 1..max_length；**不**截断）。"""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空 str 或 None")
    if len(value) > max_length:
        raise ValueError(f"{field} 超出长度上限（{len(value)} > {max_length}）")
    return value


def _required_text(value: object, *, field: str, max_length: int) -> str:
    """必需非空文本（1..max_length；**不**截断）。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空 str")
    if len(value) > max_length:
        raise ValueError(f"{field} 超出长度上限（{len(value)} > {max_length}）")
    return value


class AgentApprovalRepository:
    """``ai_ops.agent_approval`` 的最小仓储（create / get / get_for_step / resolve）。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """构造仓储。

        Args:
            session_factory: Session 工厂；None 时用全局 ``get_session_factory()``
                （DATABASE_URL 为空 → 操作时抛 ``AgentApprovalRepositoryError``）。
        """
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise AgentApprovalRepositoryError(
                "DATABASE_URL 未配置，无法持久化 Agent Approval"
            )
        return factory

    # ---------- 写入 ----------

    def create(
        self,
        *,
        approval_id: str,
        run_id: str,
        step_index: int,
        tool_name: str,
        tool_call_id: str | None = None,
    ) -> AgentApprovalModel:
        """新建（或返回已存在的）审批行 —— 重复请求**幂等**（不产生第二行）。

        Returns:
            :class:`AgentApprovalModel`（首写：``status=PENDING``；
            重复：返回已存在的同一行，**不**重置状态 / 不覆盖裁决）。

        Raises:
            ValueError: 参数非法（触达 DB 之前拦截）。
            AgentApprovalRepositoryError: DB 未配置或写入失败（事务已回滚）。
        """
        normalized_id = _required_text(
            approval_id, field="approval_id", max_length=AGENT_APPROVAL_ID_MAX_LENGTH
        )
        normalized_run_id = _required_text(
            run_id, field="run_id", max_length=AGENT_APPROVAL_RUN_ID_MAX_LENGTH
        )
        index = validate_step_index(step_index)
        normalized_tool_name = _required_text(
            tool_name,
            field="tool_name",
            max_length=AGENT_APPROVAL_TOOL_NAME_MAX_LENGTH,
        )
        normalized_tool_call_id = _optional_text(
            tool_call_id,
            field="tool_call_id",
            max_length=AGENT_APPROVAL_TOOL_CALL_ID_MAX_LENGTH,
        )
        statement = (
            postgresql_insert(AgentApprovalModel)
            .values(
                approval_id=normalized_id,
                run_id=normalized_run_id,
                step_index=index,
                tool_name=normalized_tool_name,
                tool_call_id=normalized_tool_call_id,
                status=APPROVAL_STATUS_PENDING,
                decision=None,
                reason_code=None,
                resolved_at=None,
            )
            # 执行身份唯一：重复请求 ⇒ 0 行（**不**覆盖已存在审批）
            .on_conflict_do_nothing(index_elements=("run_id", "step_index"))
            .returning(AgentApprovalModel)
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(statement).scalars().one_or_none()
                if row is None:
                    # 重复请求：返回既有行（幂等；绝不重启/重置审批）
                    row = session.execute(
                        select(AgentApprovalModel).where(
                            AgentApprovalModel.run_id == normalized_run_id,
                            AgentApprovalModel.step_index == index,
                        )
                    ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise AgentApprovalRepositoryError(
                f"Agent Approval 创建失败: {type(exc).__name__}"
            ) from exc
        if row is None:  # pragma: no cover - UNIQUE 语义下不可达（防御）
            raise AgentApprovalRepositoryError(
                "Agent Approval 创建后无法读取（并发删除？）；不做猜测"
            )
        return row

    # ---------- 读取 ----------

    def get(self, approval_id: str) -> AgentApprovalModel | None:
        """按审批身份读取（无 ⇒ ``None``）。"""
        normalized_id = _required_text(
            approval_id, field="approval_id", max_length=AGENT_APPROVAL_ID_MAX_LENGTH
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(
                    select(AgentApprovalModel).where(
                        AgentApprovalModel.approval_id == normalized_id
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise AgentApprovalRepositoryError(
                f"Agent Approval 读取失败: {type(exc).__name__}"
            ) from exc
        return row

    def get_for_step(
        self, *, run_id: str, step_index: int
    ) -> AgentApprovalModel | None:
        """按执行身份 ``(run_id, step_index)`` 读取（Resume 主路径）。"""
        normalized_run_id = _required_text(
            run_id, field="run_id", max_length=AGENT_APPROVAL_RUN_ID_MAX_LENGTH
        )
        index = validate_step_index(step_index)
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(
                    select(AgentApprovalModel).where(
                        AgentApprovalModel.run_id == normalized_run_id,
                        AgentApprovalModel.step_index == index,
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise AgentApprovalRepositoryError(
                f"Agent Approval 读取失败: {type(exc).__name__}"
            ) from exc
        return row

    # ---------- 裁决（条件 UPDATE；终态不可逆） ----------

    def resolve(
        self,
        *,
        approval_id: str,
        decision: str,
        reason_code: str | None = None,
    ) -> AgentApprovalModel | None:
        """**单条 SQL** 条件裁决（§十八 / §十九 / §五十一）。

        ``UPDATE … WHERE approval_id = ? AND status = 'PENDING'
          SET status = decision, decision = ?, reason_code = ?,
              resolved_at = now()``

        Returns:
            裁决后的行；**无转换** ⇒ ``None``（已 APPROVED / REJECTED / 不存在）
            —— 调用方判定幂等（同裁决）或 conflict（不同裁决），**绝不**force。

        Raises:
            ValueError: decision 非法。
            AgentApprovalRepositoryError: DB 未配置或写入失败。
        """
        normalized_id = _required_text(
            approval_id, field="approval_id", max_length=AGENT_APPROVAL_ID_MAX_LENGTH
        )
        normalized_decision = validate_decision(decision)
        normalized_reason = _optional_text(
            reason_code,
            field="reason_code",
            max_length=AGENT_APPROVAL_DECISION_MAX_LENGTH,
        )
        statement = (
            update(AgentApprovalModel)
            .where(
                AgentApprovalModel.approval_id == normalized_id,
                AgentApprovalModel.status == APPROVAL_STATUS_PENDING,
            )
            .values(
                status=normalized_decision,
                decision=normalized_decision,
                reason_code=normalized_reason,
                resolved_at=func.now(),
            )
            .returning(AgentApprovalModel)
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(statement).scalars().one_or_none()
        except SQLAlchemyError as exc:
            raise AgentApprovalRepositoryError(
                f"Agent Approval 裁决失败: {type(exc).__name__}"
            ) from exc
        return row

    # ---------- 删除（**仅**测试 / cleanup） ----------

    def delete_for_test(self, approval_id: str) -> int:
        """删除该 approval_id（或 run_id 前缀匹配）的行；**仅**测试 / cleanup。"""
        normalized_id = _required_text(
            approval_id, field="approval_id", max_length=AGENT_APPROVAL_ID_MAX_LENGTH
        )
        factory = self._get_session_factory()
        table = AgentApprovalModel.__table__
        statement = table.delete().where(
            (table.c.approval_id == normalized_id)
            | (table.c.run_id == normalized_id)
        )
        try:
            with factory() as session, session.begin():
                result = session.execute(statement)
                deleted = int(result.rowcount or 0)
        except SQLAlchemyError as exc:
            raise AgentApprovalRepositoryError(
                f"Agent Approval 删除失败: {type(exc).__name__}"
            ) from exc
        return deleted
