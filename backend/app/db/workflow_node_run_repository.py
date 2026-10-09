"""Workflow NodeRun Repository（Phase 6 Step 2 — Workflow Persistence）。

最小数据访问层（与 ``agent_step_repository`` / ``agent_approval_repository``
同风格）：

```text
create(...)                    —— 新建一个 attempt（RUNNING；重复身份 ⇒ 显式失败；
                                  project_id 从所属 workflow_run 解析复制）
get(...)                       —— 按执行身份 (run_id, node_id, attempt) 读取
get_latest_attempt(...)        —— 该 node 的最新 attempt（attempt DESC）
list_for_run(...)              —— 该 run 的全部 attempt（id ASC = 写入序）
update(...)                    —— 条件 UPDATE（终态不可逆；retry 不改写历史）
delete_for_test(...)           —— 仅测试 / cleanup
```

Attempt 语义（Step 2 §13；Discovery §8）：

```text
node A attempt=1 → FAILED
node A attempt=2 → SUCCEEDED     两个 attempt **都保留**（新行，不 UPDATE 旧行）
```

    * 身份 UNIQUE(run_id, node_id, attempt)（Step 2 §14）；
    * ``update`` 条件 = 身份命中 **且** 当前 status 非终态
      （终态行不可再改写 —— 任何后续推进都不可能命中）。

JSON-safe 边界（Step 2 §25）：
    ``output`` 只接受**已序列化的 JSON 文本**（str）；Repository 校验
    合法 JSON + ≤ ``JSON_VALUE_MAX_BYTES``（32 KiB，域层值边界）——
    **绝不**接受任意 Python 对象 / ORM 实体 / Session。

"Persistence only"：**不**含 retry 决策 / 节点执行逻辑。

失败语义：``SQLAlchemyError`` → ``WorkflowNodeRunRepositoryError``（事务已回滚）。
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Final

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models.workflow_node_run import (
    WORKFLOW_NODE_RUN_ID_MAX_LENGTH,
    WORKFLOW_NODE_RUN_NODE_ID_MAX_LENGTH,
    WORKFLOW_NODE_RUN_STATUSES,
    WORKFLOW_NODE_RUN_STATUS_MAX_LENGTH,
    WORKFLOW_NODE_RUN_TERMINAL_STATUSES,
    WORKFLOW_NODE_RUN_TYPE_MAX_LENGTH,
    WORKFLOW_NODE_RUN_TYPES,
    WorkflowNodeRun as WorkflowNodeRunModel,
)
from backend.app.db.models.workflow_run import (
    WorkflowRun as WorkflowRunModel,
)
from backend.app.db.session import get_session_factory
from backend.app.workflow.errors import WF_ERROR_CODES, WF_ERROR_STATE_CONFLICT
from backend.app.workflow.models import JSON_VALUE_MAX_BYTES
from backend.app.workflow.states import NODE_STATUS_RUNNING

__all__ = [
    "WorkflowNodeRunRepository",
    "WorkflowNodeRunRepositoryError",
    "WorkflowNodeRunAlreadyExistsError",
    "WorkflowNodeRunNotFoundError",
    "WorkflowNodeRunConflictError",
    "WORKFLOW_NODE_RUN_ALREADY_EXISTS",
    "WORKFLOW_NODE_RUN_NOT_FOUND",
    "validate_attempt",
    "validate_node_type",
    "validate_node_status",
    "validate_error_code",
    "validate_elapsed_ms",
]

#: Repository 特有稳定码（创建冲突 / 读取未命中；编外码只服务持久化原语层 ——
#: 对齐 ``CHECKPOINT_ALREADY_EXISTS`` / agent checkpoint 先例）。
WORKFLOW_NODE_RUN_ALREADY_EXISTS: Final[str] = "WORKFLOW_NODE_RUN_ALREADY_EXISTS"
WORKFLOW_NODE_RUN_NOT_FOUND: Final[str] = "WORKFLOW_NODE_RUN_NOT_FOUND"


class WorkflowNodeRunRepositoryError(Exception):
    """Workflow NodeRun 持久化失败（事务已回滚）。"""


class WorkflowNodeRunAlreadyExistsError(WorkflowNodeRunRepositoryError):
    """该 ``(workflow_run_id, node_id, attempt)`` 已存在（不静默覆盖同 attempt）。"""

    code: Final[str] = WORKFLOW_NODE_RUN_ALREADY_EXISTS


class WorkflowNodeRunNotFoundError(WorkflowNodeRunRepositoryError):
    """执行身份不存在（``WORKFLOW_NODE_RUN_NOT_FOUND``）。"""

    code: Final[str] = WORKFLOW_NODE_RUN_NOT_FOUND


class WorkflowNodeRunConflictError(WorkflowNodeRunRepositoryError):
    """终态 attempt 不可再改写（``WORKFLOW_STATE_CONFLICT``；reason=``terminal``）。"""

    code: Final[str] = WF_ERROR_STATE_CONFLICT

    def __init__(self, message: str, *, reason: str = "terminal") -> None:
        super().__init__(message)
        self.reason = reason


# ============================================================
# 校验
# ============================================================


def _required_text(value: object, *, field: str, max_length: int) -> str:
    """必需非空文本（1..max_length；**不**截断 / 不生成）。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空 str")
    if len(value) > max_length:
        raise ValueError(f"{field} 超出长度上限（{len(value)} > {max_length}）")
    return value


def validate_attempt(value: object, *, field: str = "attempt") -> int:
    """校验 attempt（int >= 1；拒绝 bool / float / 0 / 负数）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} 必须是 int（当前: {type(value).__name__}）")
    if value < 1:
        raise ValueError(f"{field} 必须 >= 1（当前: {value}）")
    return value


def validate_node_type(value: object) -> str:
    """校验节点类型（域层白名单：AI / TOOL / APPROVAL / CONDITION）。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("node_type 必须是非空 str")
    if value not in WORKFLOW_NODE_RUN_TYPES:
        raise ValueError(f"node_type 不在允许集合内: {value!r}")
    if len(value) > WORKFLOW_NODE_RUN_TYPE_MAX_LENGTH:
        raise ValueError(f"node_type 超出长度上限: {value!r}")
    return value


def validate_node_status(value: object) -> str:
    """校验 attempt 状态（域层白名单）。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("status 必须是非空 str")
    if value not in WORKFLOW_NODE_RUN_STATUSES:
        raise ValueError(f"status 不在允许集合内: {value!r}")
    if len(value) > WORKFLOW_NODE_RUN_STATUS_MAX_LENGTH:
        raise ValueError(f"status 超出长度上限: {value!r}")
    return value


def validate_output(value: object) -> str | None:
    """校验节点输出：None 或**已序列化**的 JSON 文本（≤ 32 KiB）。

    拒绝：非 str / 空白 / 非法 JSON / 超限（fail-closed；**不**截断）。
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("output 必须是 JSON 文本（str）或 None")
    size = len(value.encode("utf-8"))
    if size > JSON_VALUE_MAX_BYTES:
        raise ValueError(
            f"output 超过内联上限（{size} > {JSON_VALUE_MAX_BYTES} bytes；"
            "fail-closed：不截断 / 不静默丢弃）"
        )
    try:
        json.loads(value)
    except ValueError as exc:  # json.JSONDecodeError ⊂ ValueError
        raise ValueError("output 必须是合法 JSON 文本") from exc
    return value


def validate_error_code(value: object) -> str | None:
    """校验 error_code：None 或域层 ``WF_ERROR_CODES`` 白名单成员。"""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("error_code 必须是非空 str 或 None")
    if value not in WF_ERROR_CODES:
        raise ValueError(f"error_code 不在允许集合内: {value!r}")
    return value


def validate_elapsed_ms(value: object) -> float | None:
    """校验 elapsed_ms：None 或有限非负数（拒绝 bool / NaN / Infinity）。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"elapsed_ms 必须是数值或 None（当前: {type(value).__name__}）")
    number = float(value)
    if math.isnan(number) or math.isinf(number) or number < 0:
        raise ValueError(f"elapsed_ms 必须是有限非负数（当前: {value!r}）")
    return number


def _optional_datetime(value: object, *, field: str) -> datetime | None:
    """可选 datetime（None 或 datetime）。"""
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ValueError(f"{field} 必须是 datetime 或 None（当前: {type(value).__name__}）")
    return value


# ============================================================
# Repository
# ============================================================


class WorkflowNodeRunRepository:
    """``ai_ops.workflow_node_run`` 的最小仓储（per-attempt 行）。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """构造仓储。

        Args:
            session_factory: Session 工厂；None 时用全局 ``get_session_factory()``
                （DATABASE_URL 为空 → 操作时抛 ``WorkflowNodeRunRepositoryError``）。
        """
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise WorkflowNodeRunRepositoryError(
                "DATABASE_URL 未配置，无法持久化 Workflow NodeRun"
            )
        return factory

    # ---------- 写入（attempt 行） ----------

    def create(
        self,
        *,
        workflow_run_id: str,
        node_id: str,
        attempt: int,
        node_type: str,
        status: str = NODE_STATUS_RUNNING,
        output: str | None = None,
        error_code: str | None = None,
        elapsed_ms: float | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> WorkflowNodeRunModel:
        """新建一个 attempt 行（默认 RUNNING；重复身份 ⇒ 显式失败）。

        Args:
            workflow_run_id: 所属 Run（FK → workflow_run；不存在 ⇒ IntegrityError
                被归一为 ``WorkflowNodeRunRepositoryError``）。
            node_id / attempt: 执行身份（attempt >= 1）。
            node_type:       节点类型（域层白名单）。
            status:          初始 attempt 状态（默认 RUNNING）。
            output:          **已序列化** JSON 文本（None 或 ≤ 32 KiB）。
            error_code:      稳定码（None 或 ``WF_ERROR_CODES``）。
            elapsed_ms:      None 或有限非负。
            started_at / completed_at: 执行方注入的时间戳（可 None）。

        Raises:
            ValueError: 参数非法（触达 DB 之前拦截）。
            WorkflowNodeRunAlreadyExistsError: 该执行身份已存在（不覆盖）。
            WorkflowNodeRunRepositoryError: DB 未配置 / workflow_run 不存在
                （无法解析 project_id）/ FK 违约 / 写入失败。
        """
        run_id = _required_text(
            workflow_run_id,
            field="workflow_run_id",
            max_length=WORKFLOW_NODE_RUN_ID_MAX_LENGTH,
        )
        node = _required_text(
            node_id,
            field="node_id",
            max_length=WORKFLOW_NODE_RUN_NODE_ID_MAX_LENGTH,
        )
        index = validate_attempt(attempt)
        node_type_value = validate_node_type(node_type)
        status_value = validate_node_status(status)
        output_value = validate_output(output)
        error_value = validate_error_code(error_code)
        elapsed_value = validate_elapsed_ms(elapsed_ms)
        started = _optional_datetime(started_at, field="started_at")
        completed = _optional_datetime(completed_at, field="completed_at")

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
                    raise WorkflowNodeRunRepositoryError(
                        "workflow_run 不存在（无法解析 project_id）:"
                        f" {run_id!r}"
                    )
                statement = (
                    postgresql_insert(WorkflowNodeRunModel)
                    .values(
                        workflow_run_id=run_id,
                        project_id=project_id_value,
                        node_id=node,
                        attempt=index,
                        node_type=node_type_value,
                        status=status_value,
                        output=output_value,
                        error_code=error_value,
                        elapsed_ms=elapsed_value,
                        started_at=started,
                        completed_at=completed,
                    )
                    # 同 run + 同 node + 同 attempt ⇒ 0 行（**不**覆盖既有 attempt）
                    .on_conflict_do_nothing(
                        index_elements=("workflow_run_id", "node_id", "attempt")
                    )
                    .returning(WorkflowNodeRunModel)
                )
                row = session.execute(statement).scalars().one_or_none()
        except SQLAlchemyError as exc:
            raise WorkflowNodeRunRepositoryError(
                f"Workflow NodeRun 创建失败: {type(exc).__name__}"
            ) from exc
        if row is None:
            raise WorkflowNodeRunAlreadyExistsError(
                f"attempt 已存在（{WORKFLOW_NODE_RUN_ALREADY_EXISTS}）:"
                f" run={run_id!r} node={node!r} attempt={index}"
            )
        return row

    # ---------- 读取 ----------

    def get(
        self, *, workflow_run_id: str, node_id: str, attempt: int
    ) -> WorkflowNodeRunModel | None:
        """按执行身份 ``(workflow_run_id, node_id, attempt)`` 读取（无 ⇒ ``None``）。"""
        run_id = _required_text(
            workflow_run_id,
            field="workflow_run_id",
            max_length=WORKFLOW_NODE_RUN_ID_MAX_LENGTH,
        )
        node = _required_text(
            node_id,
            field="node_id",
            max_length=WORKFLOW_NODE_RUN_NODE_ID_MAX_LENGTH,
        )
        index = validate_attempt(attempt)
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(
                    select(WorkflowNodeRunModel).where(
                        WorkflowNodeRunModel.workflow_run_id == run_id,
                        WorkflowNodeRunModel.node_id == node,
                        WorkflowNodeRunModel.attempt == index,
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise WorkflowNodeRunRepositoryError(
                f"Workflow NodeRun 读取失败: {type(exc).__name__}"
            ) from exc
        return row

    def get_latest_attempt(
        self, *, workflow_run_id: str, node_id: str
    ) -> WorkflowNodeRunModel | None:
        """该 node 的最新 attempt（``attempt`` DESC；无 ⇒ ``None``）。"""
        run_id = _required_text(
            workflow_run_id,
            field="workflow_run_id",
            max_length=WORKFLOW_NODE_RUN_ID_MAX_LENGTH,
        )
        node = _required_text(
            node_id,
            field="node_id",
            max_length=WORKFLOW_NODE_RUN_NODE_ID_MAX_LENGTH,
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(
                    select(WorkflowNodeRunModel)
                    .where(
                        WorkflowNodeRunModel.workflow_run_id == run_id,
                        WorkflowNodeRunModel.node_id == node,
                    )
                    .order_by(WorkflowNodeRunModel.attempt.desc())
                    .limit(1)
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise WorkflowNodeRunRepositoryError(
                f"Workflow NodeRun 读取失败: {type(exc).__name__}"
            ) from exc
        return row

    def list_for_run(self, workflow_run_id: str) -> list[WorkflowNodeRunModel]:
        """该 run 的全部 attempt（按 ``id`` ASC = 写入序；显式 ORDER BY）。"""
        run_id = _required_text(
            workflow_run_id,
            field="workflow_run_id",
            max_length=WORKFLOW_NODE_RUN_ID_MAX_LENGTH,
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                rows = list(
                    session.execute(
                        select(WorkflowNodeRunModel)
                        .where(WorkflowNodeRunModel.workflow_run_id == run_id)
                        .order_by(WorkflowNodeRunModel.id.asc())
                    ).scalars()
                )
        except SQLAlchemyError as exc:
            raise WorkflowNodeRunRepositoryError(
                f"Workflow NodeRun 读取失败: {type(exc).__name__}"
            ) from exc
        return rows

    # ---------- 状态推进（条件 UPDATE；终态不可逆） ----------

    def update(
        self,
        *,
        workflow_run_id: str,
        node_id: str,
        attempt: int,
        status: str,
        output: str | None = None,
        error_code: str | None = None,
        elapsed_ms: float | None = None,
        completed_at: datetime | None = None,
    ) -> WorkflowNodeRunModel:
        """**单条 SQL** 条件更新一个 attempt（全量目标状态语义）。

        ``UPDATE … WHERE (run_id, node_id, attempt) = ? AND status NOT IN 终态
          SET status / output / error_code / elapsed_ms / completed_at / updated_at``

        条件式更新（RUNNING ⇄ WAITING_APPROVAL → 终态）允许多次推进；
        一旦进入终态（SUCCEEDED / FAILED）⇒ 后续任何更新都不可能命中
        （终态不可逆；retry 必须创建新 attempt 行）。

        Returns:
            更新后的行。

        Raises:
            ValueError: 参数非法。
            WorkflowNodeRunNotFoundError: 执行身份不存在。
            WorkflowNodeRunConflictError: 当前 attempt 已终态（不可改写）。
            WorkflowNodeRunRepositoryError: DB 未配置或写入失败。
        """
        run_id = _required_text(
            workflow_run_id,
            field="workflow_run_id",
            max_length=WORKFLOW_NODE_RUN_ID_MAX_LENGTH,
        )
        node = _required_text(
            node_id,
            field="node_id",
            max_length=WORKFLOW_NODE_RUN_NODE_ID_MAX_LENGTH,
        )
        index = validate_attempt(attempt)
        status_value = validate_node_status(status)
        output_value = validate_output(output)
        error_value = validate_error_code(error_code)
        elapsed_value = validate_elapsed_ms(elapsed_ms)
        completed = _optional_datetime(completed_at, field="completed_at")

        stmt = (
            update(WorkflowNodeRunModel)
            .where(
                WorkflowNodeRunModel.workflow_run_id == run_id,
                WorkflowNodeRunModel.node_id == node,
                WorkflowNodeRunModel.attempt == index,
                WorkflowNodeRunModel.status.notin_(
                    tuple(WORKFLOW_NODE_RUN_TERMINAL_STATUSES)
                ),
            )
            .values(
                status=status_value,
                output=output_value,
                error_code=error_value,
                elapsed_ms=elapsed_value,
                completed_at=completed,
                updated_at=func.now(),
            )
            .returning(WorkflowNodeRunModel)
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(stmt).scalars().one_or_none()
        except SQLAlchemyError as exc:
            raise WorkflowNodeRunRepositoryError(
                f"Workflow NodeRun 更新失败: {type(exc).__name__}"
            ) from exc
        if row is None:
            current = self.get(
                workflow_run_id=run_id, node_id=node, attempt=index
            )
            if current is None:
                raise WorkflowNodeRunNotFoundError(
                    f"attempt 不存在（{WORKFLOW_NODE_RUN_NOT_FOUND}）:"
                    f" run={run_id!r} node={node!r} attempt={index}"
                )
            raise WorkflowNodeRunConflictError(
                "attempt 已终态（终态不可逆；retry 必须创建新 attempt 行）"
            )
        return row

    # ---------- 删除（**仅**测试 / cleanup） ----------

    def delete_for_test(self, workflow_run_id: str) -> int:
        """删除该 run 的全部 attempt 行；**仅**测试 / cleanup。"""
        run_id = _required_text(
            workflow_run_id,
            field="workflow_run_id",
            max_length=WORKFLOW_NODE_RUN_ID_MAX_LENGTH,
        )
        table = WorkflowNodeRunModel.__table__
        statement = table.delete().where(table.c.workflow_run_id == run_id)
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                result = session.execute(statement)
                deleted = int(result.rowcount or 0)
        except SQLAlchemyError as exc:
            raise WorkflowNodeRunRepositoryError(
                f"Workflow NodeRun 删除失败: {type(exc).__name__}"
            ) from exc
        return deleted
