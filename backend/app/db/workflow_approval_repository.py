"""Workflow Approval Repository（Phase 6 Step 2 — Workflow Persistence）。

最小数据访问层（与 ``agent_approval_repository`` 同风格；**不**共享语义模型）：

```text
create(...)                 —— 幂等写入（重复 (run, node) ⇒ 返回**已存在**的同一行；
                               project_id 从所属 workflow_run 解析复制）
get(approval_id)            —— 按审批身份读取
get_for_node(...)           —— 按 node 执行身份读取（resume 主路径）
resolve(...)                —— **条件 UPDATE**：PENDING → APPROVED | REJECTED
delete_for_test(...)        —— 仅测试 / cleanup
```

单赢 / 幂等（Step 2 §17；Discovery §13）：

    * ``resolve`` 用 ``UPDATE … WHERE approval_id = ? AND status = 'PENDING'``：
      无行被更新 ⇒ 返回 ``None``（**绝不**覆盖终态；调用方判定幂等 / conflict）；
    * 两个并发 resolver 只有一个能命中 PENDING（数据库行级原子性）；
    * 终态不可逆：``APPROVED`` / ``REJECTED`` 一旦写入，任何 UPDATE 不可能命中。

状态-字段一致性（Step 2 §16；硬编码保证）：
    * create ⇒ ``status=PENDING`` 且 ``resolved_at=None``（硬编码）；
    * resolve ⇒ ``status=decision`` 且 ``resolved_at=now()``（同语句原子写入）。

**与 Agent Approval 的分离**：本模块不复用 ``agent_approval`` 的任何表 /
代码路径；仅对齐"条件 UPDATE 单赢"的实现模式（Discovery §13）。

失败语义：``SQLAlchemyError`` → ``WorkflowApprovalRepositoryError``（事务已回滚）。
"""
from __future__ import annotations

from typing import Final

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models.workflow_approval import (
    WORKFLOW_APPROVAL_ID_MAX_LENGTH,
    WORKFLOW_APPROVAL_NODE_ID_MAX_LENGTH,
    WORKFLOW_APPROVAL_RUN_ID_MAX_LENGTH,
    WorkflowApproval as WorkflowApprovalModel,
)
from backend.app.db.models.workflow_run import (
    WorkflowRun as WorkflowRunModel,
)
from backend.app.db.session import get_session_factory
from backend.app.workflow.states import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
)

__all__ = [
    "WorkflowApprovalRepository",
    "WorkflowApprovalRepositoryError",
    "validate_approval_decision",
]

#: 合法裁决值（严格两态；**不**接受 retry / modify / change_arguments 等）。
_WORKFLOW_APPROVAL_DECISIONS: Final[frozenset[str]] = frozenset(
    {APPROVAL_STATUS_APPROVED, APPROVAL_STATUS_REJECTED}
)


class WorkflowApprovalRepositoryError(Exception):
    """Workflow Approval 持久化失败（事务已回滚）。"""


def _required_text(value: object, *, field: str, max_length: int) -> str:
    """必需非空文本（1..max_length；**不**截断 / 不生成）。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空 str")
    if len(value) > max_length:
        raise ValueError(f"{field} 超出长度上限（{len(value)} > {max_length}）")
    return value


def validate_approval_decision(value: object) -> str:
    """校验裁决值（白名单：APPROVED / REJECTED；严格两态）。"""
    if not isinstance(value, str) or value not in _WORKFLOW_APPROVAL_DECISIONS:
        raise ValueError(
            "decision 只接受 APPROVED / REJECTED"
            f"（当前: {value!r}；不允许 retry / modify / change_arguments）"
        )
    return value


class WorkflowApprovalRepository:
    """``ai_ops.workflow_approval`` 的最小仓储（create / get / resolve）。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """构造仓储。

        Args:
            session_factory: Session 工厂；None 时用全局 ``get_session_factory()``
                （DATABASE_URL 为空 → 操作时抛 ``WorkflowApprovalRepositoryError``）。
        """
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise WorkflowApprovalRepositoryError(
                "DATABASE_URL 未配置，无法持久化 Workflow Approval"
            )
        return factory

    # ---------- 写入 ----------

    def create(
        self,
        *,
        approval_id: str,
        workflow_run_id: str,
        node_id: str,
    ) -> WorkflowApprovalModel:
        """新建（或返回已存在的）审批行 —— 重复请求**幂等**（不产生第二行）。

        硬编码首写态：``status=PENDING`` / ``resolved_at=None``
        （状态-字段一致性由写入路径保证；**不**接受调用方传状态）。

        Returns:
            :class:`WorkflowApprovalModel`（首写：PENDING；
            重复：返回已存在的同一行，**不**重置状态 / 不覆盖裁决）。

        Raises:
            ValueError: 参数非法（触达 DB 之前拦截）。
            WorkflowApprovalRepositoryError: DB 未配置 / workflow_run 不存在
                （无法解析 project_id）/ FK 违约 / 写入失败。
        """
        approval = _required_text(
            approval_id,
            field="approval_id",
            max_length=WORKFLOW_APPROVAL_ID_MAX_LENGTH,
        )
        run_id = _required_text(
            workflow_run_id,
            field="workflow_run_id",
            max_length=WORKFLOW_APPROVAL_RUN_ID_MAX_LENGTH,
        )
        node = _required_text(
            node_id,
            field="node_id",
            max_length=WORKFLOW_APPROVAL_NODE_ID_MAX_LENGTH,
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                # F-1（ADR §16 I1/I4）：project_id **从所属 workflow_run 解析**
                # （同事务内读取；run.project_id 创建后不可变），
                # **不**接受调用方传入。
                project_id_value = session.execute(
                    select(WorkflowRunModel.project_id).where(
                        WorkflowRunModel.workflow_run_id == run_id
                    )
                ).scalar_one_or_none()
                if project_id_value is None:
                    raise WorkflowApprovalRepositoryError(
                        "workflow_run 不存在（无法解析 project_id）:"
                        f" {run_id!r}"
                    )
                statement = (
                    postgresql_insert(WorkflowApprovalModel)
                    .values(
                        approval_id=approval,
                        workflow_run_id=run_id,
                        project_id=project_id_value,
                        node_id=node,
                        status=APPROVAL_STATUS_PENDING,
                        resolved_at=None,
                    )
                    # Node 审批身份唯一：重复请求 ⇒ 0 行（**不**覆盖已存在审批）
                    .on_conflict_do_nothing(
                        index_elements=("workflow_run_id", "node_id")
                    )
                    .returning(WorkflowApprovalModel)
                )
                row = session.execute(statement).scalars().one_or_none()
                if row is None:
                    # 重复请求：返回既有行（幂等；绝不重启/重置审批）
                    row = session.execute(
                        select(WorkflowApprovalModel).where(
                            WorkflowApprovalModel.workflow_run_id == run_id,
                            WorkflowApprovalModel.node_id == node,
                        )
                    ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise WorkflowApprovalRepositoryError(
                f"Workflow Approval 创建失败: {type(exc).__name__}"
            ) from exc
        if row is None:  # pragma: no cover - UNIQUE 语义下不可达（防御）
            raise WorkflowApprovalRepositoryError(
                "Workflow Approval 创建后无法读取（并发删除？）；不做猜测"
            )
        return row

    # ---------- 读取 ----------

    def get(self, approval_id: str) -> WorkflowApprovalModel | None:
        """按审批身份读取（无 ⇒ ``None``）。"""
        approval = _required_text(
            approval_id,
            field="approval_id",
            max_length=WORKFLOW_APPROVAL_ID_MAX_LENGTH,
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(
                    select(WorkflowApprovalModel).where(
                        WorkflowApprovalModel.approval_id == approval
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise WorkflowApprovalRepositoryError(
                f"Workflow Approval 读取失败: {type(exc).__name__}"
            ) from exc
        return row

    def get_for_node(
        self, *, workflow_run_id: str, node_id: str
    ) -> WorkflowApprovalModel | None:
        """按 ``(workflow_run_id, node_id)`` 读取（resume 主路径；无 ⇒ ``None``）。"""
        run_id = _required_text(
            workflow_run_id,
            field="workflow_run_id",
            max_length=WORKFLOW_APPROVAL_RUN_ID_MAX_LENGTH,
        )
        node = _required_text(
            node_id,
            field="node_id",
            max_length=WORKFLOW_APPROVAL_NODE_ID_MAX_LENGTH,
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(
                    select(WorkflowApprovalModel).where(
                        WorkflowApprovalModel.workflow_run_id == run_id,
                        WorkflowApprovalModel.node_id == node,
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise WorkflowApprovalRepositoryError(
                f"Workflow Approval 读取失败: {type(exc).__name__}"
            ) from exc
        return row

    # ---------- 裁决（条件 UPDATE；单赢 / 终态不可逆） ----------

    def resolve(
        self,
        *,
        approval_id: str,
        decision: str,
    ) -> WorkflowApprovalModel | None:
        """**单条 SQL** 条件裁决（Step 2 §16 / §17）。

        ``UPDATE … WHERE approval_id = ? AND status = 'PENDING'
          SET status = decision, resolved_at = now(), updated_at = now()``

        Returns:
            裁决后的行；**无转换** ⇒ ``None``（不存在 / 已 APPROVED / 已 REJECTED）
            —— 调用方判定幂等（同裁决）或 conflict（不同裁决），**绝不**force。

        Raises:
            ValueError: 参数非法。
            WorkflowApprovalRepositoryError: DB 未配置或写入失败。
        """
        approval = _required_text(
            approval_id,
            field="approval_id",
            max_length=WORKFLOW_APPROVAL_ID_MAX_LENGTH,
        )
        validated_decision = validate_approval_decision(decision)
        stmt = (
            update(WorkflowApprovalModel)
            .where(
                WorkflowApprovalModel.approval_id == approval,
                WorkflowApprovalModel.status == APPROVAL_STATUS_PENDING,
            )
            .values(
                status=validated_decision,
                resolved_at=func.now(),
                updated_at=func.now(),
            )
            .returning(WorkflowApprovalModel)
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(stmt).scalars().one_or_none()
        except SQLAlchemyError as exc:
            raise WorkflowApprovalRepositoryError(
                f"Workflow Approval 裁决失败: {type(exc).__name__}"
            ) from exc
        return row

    # ---------- 删除（**仅**测试 / cleanup） ----------

    def delete_for_test(self, workflow_run_id: str) -> int:
        """删除该 run 的全部批准行；**仅**测试 / cleanup。"""
        run_id = _required_text(
            workflow_run_id,
            field="workflow_run_id",
            max_length=WORKFLOW_APPROVAL_RUN_ID_MAX_LENGTH,
        )
        table = WorkflowApprovalModel.__table__
        statement = table.delete().where(table.c.workflow_run_id == run_id)
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                result = session.execute(statement)
                deleted = int(result.rowcount or 0)
        except SQLAlchemyError as exc:
            raise WorkflowApprovalRepositoryError(
                f"Workflow Approval 删除失败: {type(exc).__name__}"
            ) from exc
        return deleted
