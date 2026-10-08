"""Agent Decision Repository（Phase 5 P-6 — Decision Store）。

最小数据访问层（与 agent_step / agent_tool_result / agent_checkpoint 同风格）：

    create(...)            —— 幂等写入（first-write-wins：已存在 ⇒ ``None``）
    get(run_id, step)      —— 按执行身份读取
    get_by_ref(ref)        —— 按 ``decision_ref`` 读取（Resume 主路径；§十六）
    get_latest(run_id)     —— 该 Run 最后一条 Decision（仅终态内容恢复；§三十七）
    delete_for_test(...)   —— **仅**测试 / cleanup（生产路径无 delete）

幂等 / 冲突策略（**由数据库保证**）：``UNIQUE(run_id, step_index)`` +
``UNIQUE(decision_ref)`` ⇒ ``INSERT ... ON CONFLICT DO NOTHING RETURNING <row>``；
重复写入不产生第二行、**不 UPDATE**（历史 Decision 不可被覆盖 ⇒ Replay 确定性）。

写入前校验（触达 DB 之前拦截；§十一 / §十二）：

    run_id 1..64 · step_index >= 0 · decision_ref == "{run_id}:{step_index}" ·
    decision_type ∈ {FINAL, TOOL_CALL} · tool_call_id <= 128 · tool_name <= 128 ·
    arguments JSON-safe（dict/list/str/int/float/bool/None；**禁止** bytes /
    Session / Connection / 任意对象）· content <= 上限 · finish_reason <= 64 ·
    decision_unavailable ∈ {True, False}

状态不变式（fail-closed 语义）：

    FINAL      ⇒ tool_name / tool_call_id / arguments 必须为 None；content 非空
    TOOL_CALL  ⇒ tool_name 非空；content 必须为 None；arguments 非空
    decision_unavailable=True ⇒ arguments 与 content 必须为 None

失败语义：``SQLAlchemyError`` → ``AgentDecisionRepositoryError``（事务已回滚）；
由 ``AgentDecisionStore``（fail-closed）向上暴露为 Decision 持久化失败。
"""
from __future__ import annotations

import math
import re
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent.decision import DECISION_FINAL, DECISION_TOOL_CALL
from backend.app.agent.decision_store import (
    AgentDecisionRecord,
    parse_decision_ref,
)
from backend.app.db.models.agent_decision import (
    AGENT_DECISION_CONTENT_MAX_LENGTH,
    AGENT_DECISION_FINISH_REASON_MAX_LENGTH,
    AGENT_DECISION_REF_MAX_LENGTH,
    AGENT_DECISION_RUN_ID_MAX_LENGTH,
    AGENT_DECISION_TOOL_CALL_ID_MAX_LENGTH,
    AGENT_DECISION_TOOL_NAME_MAX_LENGTH,
    AGENT_DECISION_TYPES,
    AgentDecision as AgentDecisionModel,
)
from backend.app.db.session import get_session_factory

__all__ = [
    "AgentDecisionRepository",
    "AgentDecisionRepositoryError",
    "validate_decision_type",
    "validate_step_index",
    "validate_arguments",
]

#: payload 嵌套深度上限（防御性；不改变 JSONB 语义）。
MAX_ARGUMENTS_DEPTH: Final[int] = 64

class AgentDecisionRepositoryError(Exception):
    """Decision 持久化失败（事务已回滚；由 Store 转换为 fail-closed 错误）。"""


def validate_step_index(value: object) -> int:
    """校验 step_index（int >= 0；拒绝 bool / float / 负数）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"step_index 必须是 int（当前: {type(value).__name__}）")
    if value < 0:
        raise ValueError(f"step_index 必须 >= 0（当前: {value}）")
    return value


def validate_decision_type(value: object) -> str:
    """校验 decision_type（白名单：FINAL / TOOL_CALL）。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("decision_type 必须是非空 str")
    if value not in AGENT_DECISION_TYPES:
        raise ValueError(f"decision_type 不在允许集合内: {value!r}")
    return value


def validate_arguments(value: object) -> Any:
    """校验 arguments：None 或**已是** JSON 兼容结构（递归白名单）。"""
    if value is None:
        return None
    _assert_json_compatible(value, depth=0, path="arguments")
    return value


def _assert_json_compatible(value: object, *, depth: int, path: str) -> None:
    if depth > MAX_ARGUMENTS_DEPTH:
        raise ValueError(f"{path} 嵌套深度超出上限（{MAX_ARGUMENTS_DEPTH}）")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} 含非有限 float（NaN / Infinity 不允许）")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} 的 JSON key 必须是 str")
            _assert_json_compatible(item, depth=depth + 1, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_json_compatible(item, depth=depth + 1, path=f"{path}[{index}]")
        return
    raise ValueError(
        f"{path} 必须是 JSON 兼容结构（dict/list/str/int/float/bool/None），"
        f"当前: {type(value).__name__}"
    )


def _to_record(row: Any) -> AgentDecisionRecord:
    """ORM 行 → 域层 DTO（**事务内**调用）。"""
    return AgentDecisionRecord(
        run_id=row.run_id,
        step_index=row.step_index,
        decision_ref=row.decision_ref,
        decision_type=row.decision_type,
        tool_call_id=row.tool_call_id,
        tool_name=row.tool_name,
        arguments=row.arguments,
        content=row.content,
        finish_reason=row.finish_reason,
        decision_unavailable=bool(row.decision_unavailable),
        created_at=row.created_at,
    )


class AgentDecisionRepository:
    """``ai_ops.agent_decision`` 的最小仓储（写入幂等 + 最小读取）。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise AgentDecisionRepositoryError(
                "DATABASE_URL 未配置，无法持久化 Agent Decision"
            )
        return factory

    # ---------- 校验 ----------

    @staticmethod
    def _validated_values(
        *,
        run_id: object,
        step_index: object,
        decision_ref: object,
        decision_type: object,
        tool_call_id: object,
        tool_name: object,
        arguments: object,
        content: object,
        finish_reason: object,
        decision_unavailable: object,
    ) -> dict[str, Any]:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空 str")
        if len(run_id) > AGENT_DECISION_RUN_ID_MAX_LENGTH:
            raise ValueError("run_id 超出长度上限")
        index = validate_step_index(step_index)
        if not isinstance(decision_ref, str) or not decision_ref.strip():
            raise ValueError("decision_ref 必须是非空 str")
        if len(decision_ref) > AGENT_DECISION_REF_MAX_LENGTH:
            raise ValueError("decision_ref 超出长度上限")
        ref_run_id, ref_step_index = parse_decision_ref(decision_ref)
        if (ref_run_id, ref_step_index) != (run_id, index):
            raise ValueError(
                "decision_ref 必须等于 '{run_id}:{step_index}'"
                f"（ref={decision_ref!r}, 期望={run_id}:{index}）"
            )
        dtype = validate_decision_type(decision_type)
        if not isinstance(decision_unavailable, bool):
            raise ValueError("decision_unavailable 必须是 bool")

        def _optional_text(value: object, field: str, max_length: int) -> str | None:
            if value is None:
                return None
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} 必须是非空 str 或 None")
            if len(value) > max_length:
                raise ValueError(f"{field} 超出长度上限")
            return value

        tool_call_id = _optional_text(
            tool_call_id, "tool_call_id", AGENT_DECISION_TOOL_CALL_ID_MAX_LENGTH
        )
        tool_name = _optional_text(
            tool_name, "tool_name", AGENT_DECISION_TOOL_NAME_MAX_LENGTH
        )
        normalized_arguments = validate_arguments(arguments)
        if content is not None:
            if not isinstance(content, str) or not content.strip():
                raise ValueError("content 必须是非空 str 或 None")
            if len(content) > AGENT_DECISION_CONTENT_MAX_LENGTH:
                raise ValueError("content 超出长度上限")
        finish = _optional_text(
            finish_reason, "finish_reason", AGENT_DECISION_FINISH_REASON_MAX_LENGTH
        )

        # ---- 状态不变式（fail-closed 语义）----
        if dtype == DECISION_FINAL:
            if tool_name is not None or tool_call_id is not None:
                raise ValueError("FINAL Decision 不得携带 tool_name / tool_call_id")
            if normalized_arguments is not None:
                raise ValueError("FINAL Decision 不得携带 arguments")
        else:  # TOOL_CALL
            if tool_name is None:
                raise ValueError("TOOL_CALL Decision 必须携带 tool_name")
            if content is not None:
                raise ValueError("TOOL_CALL Decision 不得携带 content")
        if decision_unavailable:
            if normalized_arguments is not None or content is not None:
                raise ValueError(
                    "decision_unavailable=True 时 arguments 与 content 必须为 None"
                )
        else:
            if dtype == DECISION_TOOL_CALL and normalized_arguments is None:
                raise ValueError("可 Replay 的 TOOL_CALL 必须携带 arguments")
            if dtype == DECISION_FINAL and content is None:
                raise ValueError("可 Replay 的 FINAL 必须携带 content")

        return {
            "run_id": run_id,
            "step_index": index,
            "decision_ref": decision_ref,
            "decision_type": dtype,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": normalized_arguments,
            "content": content,
            "finish_reason": finish,
            "decision_unavailable": decision_unavailable,
        }

    # ---------- 写入 ----------

    def create(
        self,
        *,
        run_id: str,
        step_index: int,
        decision_ref: str,
        decision_type: str,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        arguments: Any | None = None,
        content: str | None = None,
        finish_reason: str | None = None,
        decision_unavailable: bool = False,
    ) -> AgentDecisionRecord | None:
        """幂等写入一行 Decision（已存在同身份 / 同 ref ⇒ ``None``）。

        Raises:
            ValueError:                   参数非法（触达 DB 之前拦截）。
            AgentDecisionRepositoryError: DB 未配置或写入失败（事务已回滚）。
        """
        values = self._validated_values(
            run_id=run_id,
            step_index=step_index,
            decision_ref=decision_ref,
            decision_type=decision_type,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
            content=content,
            finish_reason=finish_reason,
            decision_unavailable=decision_unavailable,
        )
        statement = (
            postgresql_insert(AgentDecisionModel)
            .values(values)
            # 任一唯一约束冲突（(run_id, step_index) / decision_ref）⇒ 0 行
            .on_conflict_do_nothing()
            .returning(AgentDecisionModel)
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(statement).scalars().one_or_none()
                record = _to_record(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise AgentDecisionRepositoryError(
                f"Agent Decision 写入失败: {type(exc).__name__}"
            ) from exc
        return record

    # ---------- 读取 ----------

    def get(self, run_id: str, step_index: int) -> AgentDecisionRecord | None:
        """按 ``(run_id, step_index)`` 读取（无 ⇒ ``None``）。"""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空 str")
        index = validate_step_index(step_index)
        return self._one(
            AgentDecisionModel.run_id == run_id,
            AgentDecisionModel.step_index == index,
        )

    def get_by_ref(self, decision_ref: str) -> AgentDecisionRecord | None:
        """按 ``decision_ref`` 读取（Resume 主路径）。"""
        parse_decision_ref(decision_ref)
        return self._one(AgentDecisionModel.decision_ref == decision_ref)

    def get_latest(self, run_id: str) -> AgentDecisionRecord | None:
        """该 Run 的**最后**一条 Decision（仅终态内容恢复使用；§三十七）。"""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空 str")
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(
                    select(AgentDecisionModel)
                    .where(AgentDecisionModel.run_id == run_id)
                    .order_by(AgentDecisionModel.step_index.desc())
                    .limit(1)
                ).scalar_one_or_none()
                record = _to_record(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise AgentDecisionRepositoryError(
                f"Agent Decision 读取失败: {type(exc).__name__}"
            ) from exc
        return record

    def _one(self, *criteria: Any) -> AgentDecisionRecord | None:
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(
                    select(AgentDecisionModel).where(*criteria)
                ).scalar_one_or_none()
                record = _to_record(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise AgentDecisionRepositoryError(
                f"Agent Decision 读取失败: {type(exc).__name__}"
            ) from exc
        return record

    def list_for_run(self, run_id: str) -> list[AgentDecisionRecord]:
        """该 Run 的**全部** Decision（按 ``step_index`` 升序；Phase 5 P-8）。

        只读列表查询（Read Model / Timeline 数据源）：显式 ORDER BY ⇒ 不依赖 DB
        自然顺序；一次查询取全量（Run 内 Decision 有界，避免 N+1）。
        """
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空 str")
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                rows = list(
                    session.execute(
                        select(AgentDecisionModel)
                        .where(AgentDecisionModel.run_id == run_id)
                        .order_by(
                            AgentDecisionModel.step_index.asc(),
                            AgentDecisionModel.id.asc(),
                        )
                    ).scalars()
                )
        except SQLAlchemyError as exc:
            raise AgentDecisionRepositoryError(
                f"Agent Decision 读取失败: {type(exc).__name__}"
            ) from exc
        return [_to_record(row) for row in rows]

    # ---------- 删除（**仅**测试 / cleanup） ----------

    def delete_for_test(self, run_id: str) -> int:
        """删除该 run_id 的 Decision（**仅**测试 / cleanup；生产路径不调用）。"""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空 str")
        factory = self._get_session_factory()
        statement = AgentDecisionModel.__table__.delete().where(
            AgentDecisionModel.__table__.c.run_id == run_id
        )
        try:
            with factory() as session, session.begin():
                deleted = int(session.execute(statement).rowcount or 0)
        except SQLAlchemyError as exc:
            raise AgentDecisionRepositoryError(
                f"Agent Decision 删除失败: {type(exc).__name__}"
            ) from exc
        return deleted
