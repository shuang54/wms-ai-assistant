"""Agent Checkpoint Repository（Phase 5 P-5 — Checkpoint Persistence）。

最小数据访问层（与 ``agent_run_repository`` / ``agent_step_repository`` 同风格）：

    create(...)            —— 新建 current checkpoint（重复 run_id ⇒
                              ``CHECKPOINT_ALREADY_EXISTS``，**不**静默覆盖）
    get(run_id)            —— 读取 current checkpoint（无 ⇒ ``None``）
    update(...)            —— **单条 SQL** 乐观并发更新（version + claim 同时进 WHERE）
    delete_for_test(...)   —— **仅**测试/cleanup（生产路径无 delete；§二十六）

乐观并发（§十二 / §十三 / §十七 / §二十八）：

```sql
UPDATE ai_ops.agent_checkpoint
   SET state_version = state_version + 1,
       status = ..., next_action = ..., pending_* = ..., updated_at = now()
 WHERE run_id = :run_id
   AND state_version = :expected_state_version
   AND claim_token  = :claim_token
RETURNING <row>
```

    * **绝不** SELECT-then-compare-then-UPDATE（无行 ⇒ 冲突）；
    * 版本不匹配 ⇒ ``CHECKPOINT_VERSION_CONFLICT``；
    * claim 不匹配 ⇒ ``CHECKPOINT_CONFLICT``（**不**自动 takeover / **不**重新生成
      claim / **不** force update；§四十五）；
    * 成功更新**不**更换 ``claim_token``（§十八）；``state_version`` 只增（§十一）。

写入前校验（§二十七；触达 DB 之前拦截）：

    run_id 1..64 · claim_token 1..128 · state_version >= 1（create 必须 = 1）·
    status ∈ {ACTIVE, PAUSED, TERMINAL} · next_action ∈ {NONE, LLM_DECISION,
    TOOL_EXECUTION, WAITING_APPROVAL, COMPLETED, FAILED} · pending_step_index >= 0 ·
    pending_tool_call_id 仅在 next_action=TOOL_EXECUTION 时允许（§二十二）·
    pending_*_ref 合法且 **pending_tool_result_ref 必须指向同一 run**（§四十）。

失败语义：``SQLAlchemyError`` → ``AgentCheckpointRepositoryError``（事务已回滚）；
由调用方（BestEffort recorder）收敛为 warning —— 不改变 Tool / Agent 业务结果。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Final

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent.checkpoint import (
    CHECKPOINT_INITIAL_STATE_VERSION,
    CHECKPOINT_NEXT_ACTION_TOOL_EXECUTION,
    CHECKPOINT_NEXT_ACTION_WAITING_APPROVAL,
    CHECKPOINT_STATUS_TERMINAL,
    AgentCheckpoint,
    parse_tool_result_ref,
)
from backend.app.db.models.agent_checkpoint import (
    AGENT_CHECKPOINT_CLAIM_TOKEN_MAX_LENGTH,
    AGENT_CHECKPOINT_NEXT_ACTIONS,
    AGENT_CHECKPOINT_PENDING_REF_MAX_LENGTH,
    AGENT_CHECKPOINT_PENDING_TOOL_CALL_ID_MAX_LENGTH,
    AGENT_CHECKPOINT_RUN_ID_MAX_LENGTH,
    AGENT_CHECKPOINT_STATUSES,
    AgentCheckpoint as AgentCheckpointModel,
)
from backend.app.db.session import get_session_factory

logger = logging.getLogger(__name__)

__all__ = [
    "AgentCheckpointRepository",
    "AgentCheckpointRepositoryError",
    "AgentCheckpointAlreadyExistsError",
    "AgentCheckpointConflictError",
    "AgentCheckpointNotFoundError",
    "CHECKPOINT_ALREADY_EXISTS",
    "CHECKPOINT_CONFLICT",
    "CHECKPOINT_VERSION_CONFLICT",
    "CHECKPOINT_NOT_FOUND",
    "CHECKPOINT_CANCEL_CONFLICT",
    "validate_state_version",
    "validate_pending_step_index",
]

#: 稳定错误码（跨层一致；不引入自由文本）。
CHECKPOINT_ALREADY_EXISTS: Final[str] = "CHECKPOINT_ALREADY_EXISTS"
CHECKPOINT_CONFLICT: Final[str] = "CHECKPOINT_CONFLICT"
CHECKPOINT_VERSION_CONFLICT: Final[str] = "CHECKPOINT_VERSION_CONFLICT"
CHECKPOINT_NOT_FOUND: Final[str] = "CHECKPOINT_NOT_FOUND"
#: P-7：取消信号 CAS 未命中（已取消 / 已终态 / 其它转换先赢；**不**force update）。
CHECKPOINT_CANCEL_CONFLICT: Final[str] = "CHECKPOINT_CANCEL_CONFLICT"


class AgentCheckpointRepositoryError(Exception):
    """Checkpoint 持久化失败（事务已回滚；调用方按 best-effort 处理）。"""


class AgentCheckpointAlreadyExistsError(AgentCheckpointRepositoryError):
    """该 run_id 已有 current checkpoint（``CHECKPOINT_ALREADY_EXISTS``；§二十九）。"""

    code: Final[str] = CHECKPOINT_ALREADY_EXISTS


class AgentCheckpointNotFoundError(AgentCheckpointRepositoryError):
    """``update`` 时该 run_id 没有 current checkpoint（``CHECKPOINT_NOT_FOUND``）。"""

    code: Final[str] = CHECKPOINT_NOT_FOUND


class AgentCheckpointConflictError(AgentCheckpointRepositoryError):
    """乐观并发冲突（版本 / claim 不匹配；§十二 / §十七 / §四十一–§四十三）。

    ``code`` ∈ {``CHECKPOINT_VERSION_CONFLICT``, ``CHECKPOINT_CONFLICT``}；
    ``reason`` 为诊断用（``version`` / ``claim`` / ``not_found`` / ``unknown``；
    不含任何业务数据）。调用方**不得**自动重试 / 接管 / 强制覆盖。
    """

    def __init__(self, message: str, *, code: str, reason: str) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason


def validate_state_version(value: object, *, field: str = "state_version") -> int:
    """校验 state_version（int >= 1；拒绝 bool / float / 0 / 负数；§十一 / §二十七）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} 必须是 int（当前: {type(value).__name__}）")
    if value < CHECKPOINT_INITIAL_STATE_VERSION:
        raise ValueError(
            f"{field} 必须 >= {CHECKPOINT_INITIAL_STATE_VERSION}（当前: {value}）"
        )
    return value


def validate_pending_step_index(value: object) -> int | None:
    """校验 pending_step_index（``None`` 或 int >= 0；**不是** round；§二十一）。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"pending_step_index 必须是 int 或 None（当前: {type(value).__name__}）"
        )
    if value < 0:
        raise ValueError(f"pending_step_index 必须 >= 0（当前: {value}）")
    return value


def _validated_run_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("run_id 必须是非空 str")
    if len(value) > AGENT_CHECKPOINT_RUN_ID_MAX_LENGTH:
        raise ValueError(
            f"run_id 超出长度上限（{len(value)} > {AGENT_CHECKPOINT_RUN_ID_MAX_LENGTH}）"
        )
    return value


def _validated_claim_token(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("claim_token 必须是非空 str")
    if len(value) > AGENT_CHECKPOINT_CLAIM_TOKEN_MAX_LENGTH:
        raise ValueError("claim_token 超出长度上限")
    return value


def _validated_whitelist(value: object, *, field: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空 str")
    if value not in allowed:
        raise ValueError(f"{field} 不在允许集合内: {value!r}")
    return value


def _validated_optional_text(
    value: object, *, field: str, max_length: int
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空 str 或 None")
    if len(value) > max_length:
        raise ValueError(f"{field} 超出长度上限（{len(value)} > {max_length}）")
    return value


def _validated_cancel_requested(value: object) -> bool:
    """校验取消信号（严格 bool；拒绝 truthy 非 bool —— 不隐式转换）。"""
    if not isinstance(value, bool):
        raise ValueError(
            f"cancel_requested 必须是 bool（当前: {type(value).__name__}）"
        )
    return value


def _validated_cancel_requested_at(
    value: object, *, cancel_requested: bool
) -> datetime | None:
    """校验取消时间（None 或 datetime；信号为 false ⇒ 必须为 None）。"""
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ValueError(
            "cancel_requested_at 必须是 datetime 或 None"
            f"（当前: {type(value).__name__}）"
        )
    if not cancel_requested:
        raise ValueError("cancel_requested_at 只在 cancel_requested=true 时允许")
    return value


def _to_checkpoint(row: Any) -> AgentCheckpoint:
    """ORM 行 → 内部 DTO（**事务内**调用；含 claim_token，绝不外泄）。"""
    return AgentCheckpoint(
        run_id=row.run_id,
        state_version=row.state_version,
        claim_token=row.claim_token,
        status=row.status,
        next_action=row.next_action,
        pending_step_index=row.pending_step_index,
        pending_tool_call_id=row.pending_tool_call_id,
        pending_decision_ref=row.pending_decision_ref,
        pending_tool_result_ref=row.pending_tool_result_ref,
        created_at=row.created_at,
        updated_at=row.updated_at,
        cancel_requested=bool(getattr(row, "cancel_requested", False)),
        cancel_requested_at=getattr(row, "cancel_requested_at", None),
    )


class AgentCheckpointRepository:
    """``ai_ops.agent_checkpoint`` 的最小仓储（create / get / update）。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """构造仓储。

        Args:
            session_factory: Session 工厂；None 时用全局 ``get_session_factory()``
                （DATABASE_URL 为空 → 操作时抛 ``AgentCheckpointRepositoryError``）。
        """
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise AgentCheckpointRepositoryError(
                "DATABASE_URL 未配置，无法持久化 Agent Checkpoint"
            )
        return factory

    # ---------- 校验 ----------

    @staticmethod
    def _validated_state_values(
        *,
        run_id: object,
        status: object,
        next_action: object,
        pending_step_index: object,
        pending_tool_call_id: object,
        pending_decision_ref: object,
        pending_tool_result_ref: object,
    ) -> dict[str, Any]:
        """校验并归一"目标状态"字段（create / update 共用；单一校验点）。"""
        normalized_run_id = _validated_run_id(run_id)
        normalized_status = _validated_whitelist(
            status, field="status", allowed=AGENT_CHECKPOINT_STATUSES
        )
        normalized_next_action = _validated_whitelist(
            next_action, field="next_action", allowed=AGENT_CHECKPOINT_NEXT_ACTIONS
        )
        tool_call_id = _validated_optional_text(
            pending_tool_call_id,
            field="pending_tool_call_id",
            max_length=AGENT_CHECKPOINT_PENDING_TOOL_CALL_ID_MAX_LENGTH,
        )
        # Phase 5 P-7 最小扩展：HITL 暂停（PAUSED / WAITING_APPROVAL）同样必须
        # 携带 pending_tool_call_id（§十四：等待审批的就是**那一次** Tool Call）。
        if tool_call_id is not None and normalized_next_action not in (
            CHECKPOINT_NEXT_ACTION_TOOL_EXECUTION,
            CHECKPOINT_NEXT_ACTION_WAITING_APPROVAL,
        ):
            raise ValueError(
                "pending_tool_call_id 仅在 next_action=TOOL_EXECUTION 或"
                " WAITING_APPROVAL 时允许（§二十二 / P-7 §十四）"
            )
        if (
            normalized_next_action == CHECKPOINT_NEXT_ACTION_WAITING_APPROVAL
            and tool_call_id is None
        ):
            raise ValueError(
                "next_action=WAITING_APPROVAL 必须携带 pending_tool_call_id"
                "（P-7 §十四：暂停绑定具体 Tool Call）"
            )
        decision_ref = _validated_optional_text(
            pending_decision_ref,
            field="pending_decision_ref",
            max_length=AGENT_CHECKPOINT_PENDING_REF_MAX_LENGTH,
        )
        result_ref = _validated_optional_text(
            pending_tool_result_ref,
            field="pending_tool_result_ref",
            max_length=AGENT_CHECKPOINT_PENDING_REF_MAX_LENGTH,
        )
        if result_ref is not None:
            # §四十：引用必须逻辑定位**同一 run**（step_index 由纯函数校验）
            ref_run_id, _ref_step_index = parse_tool_result_ref(result_ref)
            if ref_run_id != normalized_run_id:
                raise ValueError(
                    "pending_tool_result_ref 必须指向同一 run"
                    f"（ref={ref_run_id!r}, run_id={normalized_run_id!r}）"
                )
        return {
            "run_id": normalized_run_id,
            "status": normalized_status,
            "next_action": normalized_next_action,
            "pending_step_index": validate_pending_step_index(pending_step_index),
            "pending_tool_call_id": tool_call_id,
            "pending_decision_ref": decision_ref,
            "pending_tool_result_ref": result_ref,
        }

    # ---------- 写入 ----------

    def create(
        self,
        *,
        run_id: str,
        state_version: int = CHECKPOINT_INITIAL_STATE_VERSION,
        claim_token: str,
        status: str,
        next_action: str,
        pending_step_index: int | None = None,
        pending_tool_call_id: str | None = None,
        pending_decision_ref: str | None = None,
        pending_tool_result_ref: str | None = None,
        cancel_requested: bool = False,
        cancel_requested_at: datetime | None = None,
    ) -> AgentCheckpoint:
        """新建 current checkpoint（``state_version`` 必须 = 1；§十一 / §二十九）。

        Returns:
            新建的 :class:`AgentCheckpoint`（含服务端时间戳）。

        Raises:
            ValueError: 参数非法（触达 DB 之前拦截）。
            AgentCheckpointAlreadyExistsError: 该 run_id 已有 checkpoint
                （``CHECKPOINT_ALREADY_EXISTS``；**不**覆盖）。
            AgentCheckpointRepositoryError: DB 未配置或写入失败（事务已回滚）。
        """
        version = validate_state_version(state_version)
        if version != CHECKPOINT_INITIAL_STATE_VERSION:
            raise ValueError(
                "create 的 state_version 必须"
                f" = {CHECKPOINT_INITIAL_STATE_VERSION}（创建即初版；§十一）"
            )
        token = _validated_claim_token(claim_token)
        values = self._validated_state_values(
            run_id=run_id,
            status=status,
            next_action=next_action,
            pending_step_index=pending_step_index,
            pending_tool_call_id=pending_tool_call_id,
            pending_decision_ref=pending_decision_ref,
            pending_tool_result_ref=pending_tool_result_ref,
        )
        cancel_flag = _validated_cancel_requested(cancel_requested)
        cancel_at = _validated_cancel_requested_at(
            cancel_requested_at, cancel_requested=cancel_flag
        )
        statement = (
            postgresql_insert(AgentCheckpointModel)
            .values(
                state_version=version,
                claim_token=token,
                cancel_requested=cancel_flag,
                cancel_requested_at=cancel_at,
                **values,
            )
            # 重复 run_id ⇒ 0 行（不静默覆盖；§二十九）
            .on_conflict_do_nothing(index_elements=("run_id",))
            .returning(AgentCheckpointModel)
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(statement).scalars().one_or_none()
                checkpoint = _to_checkpoint(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise AgentCheckpointRepositoryError(
                f"Agent Checkpoint 创建失败: {type(exc).__name__}"
            ) from exc
        if checkpoint is None:
            raise AgentCheckpointAlreadyExistsError(
                f"run_id 已有 current checkpoint（{CHECKPOINT_ALREADY_EXISTS}）:"
                f" {values['run_id']!r}"
            )
        return checkpoint

    # ---------- 读取 ----------

    def get(self, run_id: str) -> AgentCheckpoint | None:
        """读取 current checkpoint（无 ⇒ ``None``）。"""
        normalized_run_id = _validated_run_id(run_id)
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(
                    select(AgentCheckpointModel).where(
                        AgentCheckpointModel.run_id == normalized_run_id
                    )
                ).scalar_one_or_none()
                checkpoint = _to_checkpoint(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise AgentCheckpointRepositoryError(
                f"Agent Checkpoint 读取失败: {type(exc).__name__}"
            ) from exc
        return checkpoint

    # ---------- 更新（乐观并发） ----------

    def update(
        self,
        *,
        run_id: str,
        expected_state_version: int,
        claim_token: str,
        status: str,
        next_action: str,
        pending_step_index: int | None = None,
        pending_tool_call_id: str | None = None,
        pending_decision_ref: str | None = None,
        pending_tool_result_ref: str | None = None,
    ) -> AgentCheckpoint:
        """**单条 SQL** 乐观并发更新（§十二 / §二十八）。

        ``UPDATE ... WHERE run_id = ? AND state_version = ? AND claim_token = ?
          SET state_version = state_version + 1, updated_at = now(), ...``

        Returns:
            更新后的 :class:`AgentCheckpoint`（``state_version`` = expected + 1；
            ``claim_token`` **不变**）。

        Raises:
            ValueError: 参数非法。
            AgentCheckpointNotFoundError: 该 run_id 无 checkpoint。
            AgentCheckpointConflictError: 版本或 claim 不匹配
                （``CHECKPOINT_VERSION_CONFLICT`` / ``CHECKPOINT_CONFLICT``）。
            AgentCheckpointRepositoryError: DB 未配置或写入失败。
        """
        expected = validate_state_version(
            expected_state_version, field="expected_state_version"
        )
        token = _validated_claim_token(claim_token)
        values = self._validated_state_values(
            run_id=run_id,
            status=status,
            next_action=next_action,
            pending_step_index=pending_step_index,
            pending_tool_call_id=pending_tool_call_id,
            pending_decision_ref=pending_decision_ref,
            pending_tool_result_ref=pending_tool_result_ref,
        )
        statement = (
            update(AgentCheckpointModel)
            .where(
                AgentCheckpointModel.run_id == values["run_id"],
                AgentCheckpointModel.state_version == expected,
                AgentCheckpointModel.claim_token == token,
            )
            .values(
                state_version=AgentCheckpointModel.state_version + 1,
                status=values["status"],
                next_action=values["next_action"],
                pending_step_index=values["pending_step_index"],
                pending_tool_call_id=values["pending_tool_call_id"],
                pending_decision_ref=values["pending_decision_ref"],
                pending_tool_result_ref=values["pending_tool_result_ref"],
                # claim_token **不在** values 中（成功更新不更换 claim；§十八）
                updated_at=func.now(),
            )
            .returning(AgentCheckpointModel)
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(statement).scalars().one_or_none()
                checkpoint = _to_checkpoint(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise AgentCheckpointRepositoryError(
                f"Agent Checkpoint 更新失败: {type(exc).__name__}"
            ) from exc
        if checkpoint is None:
            raise self._conflict_error(
                run_id=values["run_id"],
                expected_state_version=expected,
                claim_token=token,
            )
        return checkpoint

    def request_cancel(self, run_id: str) -> AgentCheckpoint:
        """**单条 SQL** 置位取消信号（P-7；CAS；**不**强杀 / **不**重试）。

        ``UPDATE … WHERE run_id = ? AND cancel_requested = false
                        AND status != 'TERMINAL'
          SET cancel_requested = true, cancel_requested_at = now(),
              state_version = state_version + 1, updated_at = now()``

        与 ``update`` 共用 ``state_version`` 域 ⇒ 取消 / Resume claim / 终态写入
        彼此互斥（§三十六 / §五十四）。

        Returns:
            更新后的 :class:`AgentCheckpoint`（``cancel_requested=true``）。

        Raises:
            ValueError: run_id 非法。
            AgentCheckpointNotFoundError: 该 run_id 无 checkpoint（``CHECKPOINT_NOT_FOUND``）。
            AgentCheckpointConflictError: 未命中（已取消 / 已终态 / 被其它转换先赢；
                ``CHECKPOINT_CANCEL_CONFLICT``）—— 调用方按幂等 / no-op / conflict 判定，
                **绝不**force update。
            AgentCheckpointRepositoryError: DB 未配置或写入失败。
        """
        normalized_run_id = _validated_run_id(run_id)
        statement = (
            update(AgentCheckpointModel)
            .where(
                AgentCheckpointModel.run_id == normalized_run_id,
                AgentCheckpointModel.cancel_requested.is_(False),
                AgentCheckpointModel.status != CHECKPOINT_STATUS_TERMINAL,
            )
            .values(
                cancel_requested=True,
                cancel_requested_at=func.now(),
                state_version=AgentCheckpointModel.state_version + 1,
                updated_at=func.now(),
            )
            .returning(AgentCheckpointModel)
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(statement).scalars().one_or_none()
                checkpoint = _to_checkpoint(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise AgentCheckpointRepositoryError(
                f"Agent Checkpoint 取消信号写入失败: {type(exc).__name__}"
            ) from exc
        if checkpoint is None:
            current = self.get(normalized_run_id)
            if current is None:
                raise AgentCheckpointNotFoundError(
                    "run_id 没有 current checkpoint"
                    f"（{CHECKPOINT_NOT_FOUND}）: {normalized_run_id!r}"
                )
            raise AgentCheckpointConflictError(
                "取消信号未置位（已取消 / 已终态 / 其它转换先赢；不做 force update）",
                code=CHECKPOINT_CANCEL_CONFLICT,
                reason=(
                    "terminal"
                    if current.status == CHECKPOINT_STATUS_TERMINAL
                    else "already_requested"
                    if current.cancel_requested
                    else "version"
                ),
            )
        return checkpoint

    def _conflict_error(
        self,
        *,
        run_id: str,
        expected_state_version: int,
        claim_token: str,
    ) -> AgentCheckpointRepositoryError:
        """诊断冲突原因（**仅**失败路径的只读判定；不影响原子性）。"""
        try:
            current = self.get(run_id)
        except AgentCheckpointRepositoryError:  # pragma: no cover - 诊断失败
            current = None
        if current is None:
            return AgentCheckpointNotFoundError(
                f"run_id 没有 current checkpoint（{CHECKPOINT_NOT_FOUND}）: {run_id!r}"
            )
        if current.state_version != expected_state_version:
            return AgentCheckpointConflictError(
                "Checkpoint 版本冲突（expected="
                f"{expected_state_version}, current={current.state_version}）",
                code=CHECKPOINT_VERSION_CONFLICT,
                reason="version",
            )
        if current.claim_token != claim_token:
            return AgentCheckpointConflictError(
                "Checkpoint claim 冲突（claim 不匹配；不做自动接管；§四十五）",
                code=CHECKPOINT_CONFLICT,
                reason="claim",
            )
        return AgentCheckpointConflictError(
            "Checkpoint 并发冲突（无法归因；不做 force update）",
            code=CHECKPOINT_CONFLICT,
            reason="unknown",
        )

    # ---------- 删除（**仅**测试 / cleanup；§二十六） ----------

    def delete_for_test(self, run_id: str) -> int:
        """删除该 run_id 的 checkpoint（**仅**测试 / cleanup；生产路径不调用）。"""
        normalized_run_id = _validated_run_id(run_id)
        factory = self._get_session_factory()
        statement = AgentCheckpointModel.__table__.delete().where(
            AgentCheckpointModel.__table__.c.run_id == normalized_run_id
        )
        try:
            with factory() as session, session.begin():
                result = session.execute(statement)
                deleted = int(result.rowcount or 0)
        except SQLAlchemyError as exc:
            raise AgentCheckpointRepositoryError(
                f"Agent Checkpoint 删除失败: {type(exc).__name__}"
            ) from exc
        return deleted
