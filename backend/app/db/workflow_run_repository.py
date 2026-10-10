"""Workflow Run Repository（Phase 6 Step 2 — Workflow Persistence）。

最小数据访问层（与 ``agent_run_repository`` / ``agent_checkpoint_repository``
同风格、同基础设施）：

```text
create(...)               —— 新建 run（重复 workflow_run_id ⇒ 显式失败，不静默覆盖）
get(workflow_run_id)      —— 只读单行
update_cas(...)           —— **单条 SQL** 乐观并发推进控制状态（state_version CAS）
request_cancel(...)       —— 取消信号 CAS（条件 UPDATE；不新增 status 枚举值）
delete_for_test(...)      —— **仅**测试 / cleanup（生产路径无 delete）
```

CAS 语义（Step 2 §10；Discovery §9）：

```sql
UPDATE ai_ops.workflow_run
   SET state_version = state_version + 1, status = ..., current_node_id = ...,
       next_action = ..., attempt_count = ...,
       started_at   = COALESCE(started_at, now())    -- 首次进入 RUNNING 时
       completed_at = COALESCE(completed_at, now())  -- 首次进入终态时
 WHERE workflow_run_id = :id AND state_version = :expected
RETURNING <row>
```

    * affected_rows == 1 ⇒ success（``state_version`` = expected + 1）；
    * affected_rows == 0 ⇒ concurrency conflict（``WORKFLOW_STATE_CONFLICT``）
      或 not found（``WORKFLOW_RUN_NOT_FOUND``）—— 诊断仅走失败路径的只读判定，
      **绝不** SELECT-then-UPDATE。

"Persistence only"（Step 2 §20）：**不**含 workflow transition 方向校验 /
retry / node 执行逻辑 —— 本层只提供持久化原语。

失败语义：``SQLAlchemyError`` → ``WorkflowRunRepositoryError``（事务已回滚）。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Final

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db.models.workflow_run import (
    WORKFLOW_NEXT_ACTION_NONE,
    WORKFLOW_NEXT_ACTIONS,
    WORKFLOW_RUN_ID_MAX_LENGTH,
    WORKFLOW_RUN_INITIAL_STATE_VERSION,
    WORKFLOW_RUN_NEXT_ACTION_MAX_LENGTH,
    WORKFLOW_RUN_NODE_ID_MAX_LENGTH,
    WORKFLOW_RUN_PROJECT_ID_MAX_LENGTH,
    WORKFLOW_RUN_REQUEST_ID_MAX_LENGTH,
    WORKFLOW_RUN_STATUS_MAX_LENGTH,
    WORKFLOW_RUN_STATUSES,
    WORKFLOW_RUN_TERMINAL_STATUSES,
    WORKFLOW_RUN_VERSION_MAX_LENGTH,
    WORKFLOW_RUN_WORKFLOW_ID_MAX_LENGTH,
    WorkflowRun as WorkflowRunModel,
)
from backend.app.db.session import get_session_factory
from backend.app.workflow.errors import (
    WF_ERROR_RUN_NOT_FOUND,
    WF_ERROR_STATE_CONFLICT,
    WorkflowOutputError,
)
from backend.app.workflow.models import (
    serialize_json_value,
    validate_json_value,
)
from backend.app.workflow.states import (
    WORKFLOW_STATUS_PENDING,
    WORKFLOW_STATUS_RUNNING,
)

__all__ = [
    "WorkflowRunRepository",
    "WorkflowRunRepositoryError",
    "WorkflowRunAlreadyExistsError",
    "WorkflowRunNotFoundError",
    "WorkflowRunConflictError",
    "WORKFLOW_RUN_ALREADY_EXISTS",
    "validate_state_version",
    "validate_run_status",
    "validate_next_action",
    "validate_attempt_count",
    "validate_input_json",
]

#: Repository 特有稳定码（创建冲突；不在域层 WF_ERROR_CODES —— 对齐
#: ``CHECKPOINT_ALREADY_EXISTS`` 先例：编外码只服务持久化原语层）。
WORKFLOW_RUN_ALREADY_EXISTS: Final[str] = "WORKFLOW_RUN_ALREADY_EXISTS"

#: 允许创建 run 时的初始 status（创建即终态无业务意义 ⇒ 拒绝；防误用）。
_CREATABLE_STATUSES: Final[frozenset[str]] = frozenset(
    {WORKFLOW_STATUS_PENDING, WORKFLOW_STATUS_RUNNING}
)


class WorkflowRunRepositoryError(Exception):
    """Workflow Run 持久化失败（事务已回滚）。"""


class WorkflowRunAlreadyExistsError(WorkflowRunRepositoryError):
    """该 ``workflow_run_id`` 已存在（``WORKFLOW_RUN_ALREADY_EXISTS``；不静默覆盖）。"""

    code: Final[str] = WORKFLOW_RUN_ALREADY_EXISTS


class WorkflowRunNotFoundError(WorkflowRunRepositoryError):
    """``workflow_run_id`` 不存在（``WORKFLOW_RUN_NOT_FOUND``）。"""

    code: Final[str] = WF_ERROR_RUN_NOT_FOUND


class WorkflowRunConflictError(WorkflowRunRepositoryError):
    """乐观并发冲突（``state_version`` 不匹配；``WORKFLOW_STATE_CONFLICT``）。

    ``reason`` 为诊断用（``version`` / ``terminal`` / ``already_requested`` /
    ``unknown``；不含任何业务数据）。调用方**不得**自动重试 / 强制覆盖。
    """

    code: Final[str] = WF_ERROR_STATE_CONFLICT

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


# ============================================================
# 校验（触达 DB 之前拦截；单一实现点）
# ============================================================


def _required_text(value: object, *, field: str, max_length: int) -> str:
    """必需非空文本（1..max_length；**不**截断 / 不生成）。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空 str")
    if len(value) > max_length:
        raise ValueError(f"{field} 超出长度上限（{len(value)} > {max_length}）")
    return value


def _optional_text(value: object, *, field: str, max_length: int) -> str | None:
    """可选非空文本（None 或 1..max_length；**不**截断）。"""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空 str 或 None")
    if len(value) > max_length:
        raise ValueError(f"{field} 超出长度上限（{len(value)} > {max_length}）")
    return value


def validate_state_version(value: object, *, field: str = "state_version") -> int:
    """校验 state_version（int >= 0；拒绝 bool / float / 负数）。

    初始 = 0（``WORKFLOW_RUN_INITIAL_STATE_VERSION``）；每次成功推进 + 1。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} 必须是 int（当前: {type(value).__name__}）")
    if value < WORKFLOW_RUN_INITIAL_STATE_VERSION:
        raise ValueError(
            f"{field} 必须 >= {WORKFLOW_RUN_INITIAL_STATE_VERSION}（当前: {value}）"
        )
    return value


def validate_run_status(value: object) -> str:
    """校验 run 状态（域层白名单；不新增状态 / 不改写）。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("status 必须是非空 str")
    if value not in WORKFLOW_RUN_STATUSES:
        raise ValueError(f"status 不在允许集合内: {value!r}")
    if len(value) > WORKFLOW_RUN_STATUS_MAX_LENGTH:
        raise ValueError(f"status 超出长度上限: {value!r}")
    return value


def validate_next_action(value: object) -> str:
    """校验 next_action（本表白名单；不新增动作）。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("next_action 必须是非空 str")
    if value not in WORKFLOW_NEXT_ACTIONS:
        raise ValueError(f"next_action 不在允许集合内: {value!r}")
    if len(value) > WORKFLOW_RUN_NEXT_ACTION_MAX_LENGTH:
        raise ValueError(f"next_action 超出长度上限: {value!r}")
    return value


def validate_attempt_count(value: object, *, field: str = "attempt_count") -> int:
    """校验 attempt 计数（int >= 0；拒绝 bool / float / 负数）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} 必须是 int（当前: {type(value).__name__}）")
    if value < 0:
        raise ValueError(f"{field} 不能为负数（当前: {value}）")
    return value


def validate_input_json(value: object) -> str | None:
    """校验并规范化启动输入（D1；**复用域层 JSON-safe 规则**——单一实现点）。

    与域层 ``serialize_json_value`` 同源：合法 JSON 值 + 非 NaN/Infinity +
    ``dict`` 键为 str + 嵌套深度 ≤ 8 + 序列化 ≤ 32 KiB（fail-closed 不截断）。
    不接受任意对象 / 不信任调用方已序列化；非法内容**绝不**写入数据库。

    Args:
        value: None 或 JSON 文本（str）。

    Returns:
        None，或**规范化** JSON 文本（域 ``serialize_json_value`` 产物）。

    Raises:
        ValueError: 非 str / 非法 JSON / 深度超限 / 序列化超界
            （触达 DB 之前拦截）。
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"input 必须是 JSON 文本或 None（当前: {type(value).__name__}）"
        )
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        raise ValueError("input 不是合法 JSON 文本") from None
    try:
        validate_json_value(parsed)
        return serialize_json_value(parsed)
    except WorkflowOutputError as exc:
        raise ValueError(
            "input 超过 JSON 安全边界（深度 / 大小；fail-closed 不截断）"
        ) from exc


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


class WorkflowRunRepository:
    """``ai_ops.workflow_run`` 的最小仓储（create / get / update_cas / request_cancel）。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        """构造仓储。

        Args:
            session_factory: Session 工厂；None 时用全局 ``get_session_factory()``
                （DATABASE_URL 为空 → 操作时抛 ``WorkflowRunRepositoryError``）。
        """
        self._session_factory = session_factory

    # ---------- 依赖解析 ----------

    def _get_session_factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise WorkflowRunRepositoryError(
                "DATABASE_URL 未配置，无法持久化 Workflow Run"
            )
        return factory

    # ---------- 写入（创建） ----------

    def create(
        self,
        *,
        workflow_run_id: str,
        workflow_id: str,
        workflow_version: str,
        request_id: str,
        project_id: str,
        input: str | None = None,
        status: str = WORKFLOW_STATUS_PENDING,
        current_node_id: str | None = None,
        next_action: str = WORKFLOW_NEXT_ACTION_NONE,
    ) -> WorkflowRunModel:
        """新建一个 Workflow Run（``state_version`` = 0；重复 ⇒ 显式失败）。

        Args:
            workflow_run_id:  执行身份（Runtime 签发；UNIQUE）。
            workflow_id:      Definition 逻辑身份（name）。
            workflow_version: Definition 版本（**必须**持久化 —— resume 按它解析）。
            request_id:       调用 / correlation identity（≠ workflow_run_id）。
            project_id:       项目作用域（**NOT NULL**；服务器端解析结果）。
            input:            启动输入（D1：JSON 文本或 None）；经域层
                              ``serialize_json_value`` 规则**解析 + 校验 +
                              规范化**（≤ 32 KiB / 深度 ≤ 8）；非法 / 超界
                              ⇒ ``ValueError``（**不**写入数据库）。
            status:           初始状态（仅 PENDING / RUNNING；默认 PENDING）。
            current_node_id:  初始节点指针（可 None）。
            next_action:      初始推进语义（白名单；默认 NONE）。

        Returns:
            新建的 :class:`WorkflowRunModel`（含服务端时间戳）。

        Raises:
            ValueError: 参数非法（触达 DB 之前拦截）。
            WorkflowRunAlreadyExistsError: 该 ``workflow_run_id`` 已存在
                （``WORKFLOW_RUN_ALREADY_EXISTS``；**不**静默覆盖）。
            WorkflowRunRepositoryError: DB 未配置或写入失败（事务已回滚）。
        """
        run_id = _required_text(
            workflow_run_id,
            field="workflow_run_id",
            max_length=WORKFLOW_RUN_ID_MAX_LENGTH,
        )
        workflow = _required_text(
            workflow_id,
            field="workflow_id",
            max_length=WORKFLOW_RUN_WORKFLOW_ID_MAX_LENGTH,
        )
        version = _required_text(
            workflow_version,
            field="workflow_version",
            max_length=WORKFLOW_RUN_VERSION_MAX_LENGTH,
        )
        request = _required_text(
            request_id,
            field="request_id",
            max_length=WORKFLOW_RUN_REQUEST_ID_MAX_LENGTH,
        )
        project = _required_text(
            project_id,
            field="project_id",
            max_length=WORKFLOW_RUN_PROJECT_ID_MAX_LENGTH,
        )
        validated_status = validate_run_status(status)
        if validated_status not in _CREATABLE_STATUSES:
            raise ValueError(
                "create 仅允许初始 status ∈ {PENDING, RUNNING}"
                f"（当前: {validated_status!r}）"
            )
        node = _optional_text(
            current_node_id,
            field="current_node_id",
            max_length=WORKFLOW_RUN_NODE_ID_MAX_LENGTH,
        )
        action = validate_next_action(next_action)
        input_text = validate_input_json(input)

        statement = (
            postgresql_insert(WorkflowRunModel)
            .values(
                workflow_run_id=run_id,
                workflow_id=workflow,
                workflow_version=version,
                request_id=request,
                project_id=project,
                input=input_text,
                status=validated_status,
                state_version=WORKFLOW_RUN_INITIAL_STATE_VERSION,
                current_node_id=node,
                next_action=action,
                attempt_count=0,
                cancel_requested=False,
                cancel_requested_at=None,
                # 首次进入 RUNNING 即记录 started_at（只写一次）
                started_at=(
                    func.now()
                    if validated_status == WORKFLOW_STATUS_RUNNING
                    else None
                ),
                completed_at=None,
            )
            # 重复 workflow_run_id ⇒ 0 行（**不**静默覆盖 / 不 UPDATE）
            .on_conflict_do_nothing(index_elements=("workflow_run_id",))
            .returning(WorkflowRunModel)
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(statement).scalars().one_or_none()
        except SQLAlchemyError as exc:
            raise WorkflowRunRepositoryError(
                f"Workflow Run 创建失败: {type(exc).__name__}"
            ) from exc
        if row is None:
            raise WorkflowRunAlreadyExistsError(
                f"workflow_run_id 已存在（{WORKFLOW_RUN_ALREADY_EXISTS}）: {run_id!r}"
            )
        return row

    # ---------- 读取 ----------

    def get(self, workflow_run_id: str) -> WorkflowRunModel | None:
        """按 ``workflow_run_id`` 读取（只读单行；无 ⇒ ``None``）。"""
        run_id = _required_text(
            workflow_run_id,
            field="workflow_run_id",
            max_length=WORKFLOW_RUN_ID_MAX_LENGTH,
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(
                    select(WorkflowRunModel).where(
                        WorkflowRunModel.workflow_run_id == run_id
                    )
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise WorkflowRunRepositoryError(
                f"Workflow Run 读取失败: {type(exc).__name__}"
            ) from exc
        return row

    # ---------- 控制状态推进（state_version CAS） ----------

    def update_cas(
        self,
        *,
        workflow_run_id: str,
        expected_state_version: int,
        status: str,
        current_node_id: str | None,
        next_action: str,
        attempt_count: int,
    ) -> WorkflowRunModel:
        """**单条 SQL** 乐观并发推进控制状态（全量目标状态语义）。

        ``UPDATE … WHERE workflow_run_id = ? AND state_version = ?
          SET state_version = state_version + 1, status / current_node_id /
              next_action / attempt_count / 时间戳（COALESCE 只写一次）``

        Returns:
            更新后的行（``state_version`` = expected + 1）。

        Raises:
            ValueError: 参数非法。
            WorkflowRunNotFoundError: 该 ``workflow_run_id`` 不存在。
            WorkflowRunConflictError: ``state_version`` 不匹配
                （``WORKFLOW_STATE_CONFLICT``；**不**自动重试 / 不强制覆盖）。
            WorkflowRunRepositoryError: DB 未配置或写入失败。
        """
        run_id = _required_text(
            workflow_run_id,
            field="workflow_run_id",
            max_length=WORKFLOW_RUN_ID_MAX_LENGTH,
        )
        expected = validate_state_version(
            expected_state_version, field="expected_state_version"
        )
        validated_status = validate_run_status(status)
        node = _optional_text(
            current_node_id,
            field="current_node_id",
            max_length=WORKFLOW_RUN_NODE_ID_MAX_LENGTH,
        )
        action = validate_next_action(next_action)
        attempts = validate_attempt_count(attempt_count)

        values: dict[str, Any] = {
            "state_version": WorkflowRunModel.state_version + 1,
            "status": validated_status,
            "current_node_id": node,
            "next_action": action,
            "attempt_count": attempts,
            "updated_at": func.now(),
        }
        # 时间戳：首次进入 RUNNING / 终态时写入一次（COALESCE 保证幂等推进不重写）
        if validated_status == WORKFLOW_STATUS_RUNNING:
            values["started_at"] = func.coalesce(
                WorkflowRunModel.started_at, func.now()
            )
        if validated_status in WORKFLOW_RUN_TERMINAL_STATUSES:
            values["completed_at"] = func.coalesce(
                WorkflowRunModel.completed_at, func.now()
            )

        statement = (
            update(WorkflowRunModel)
            .where(
                WorkflowRunModel.workflow_run_id == run_id,
                WorkflowRunModel.state_version == expected,
            )
            .values(**values)
            .returning(WorkflowRunModel)
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(statement).scalars().one_or_none()
        except SQLAlchemyError as exc:
            raise WorkflowRunRepositoryError(
                f"Workflow Run 控制状态推进失败: {type(exc).__name__}"
            ) from exc
        if row is None:
            raise self._conflict_error(
                run_id=run_id, expected_state_version=expected
            )
        return row

    # ---------- 取消信号（条件 UPDATE；CAS 语义） ----------

    def request_cancel(self, workflow_run_id: str) -> WorkflowRunModel:
        """**单条 SQL** 置位取消信号（协作式；**不**强杀 / **不**改终态）。

        ``UPDATE … WHERE workflow_run_id = ? AND cancel_requested = false
                        AND status NOT IN (终态)
          SET cancel_requested = true, cancel_requested_at = now(),
              state_version = state_version + 1, updated_at = now()``

        与 ``update_cas`` 共用 ``state_version`` 域 ⇒ 取消与推进彼此互斥
        （先到先赢；终态 run 取消为 no-op 判定）。

        Returns:
            更新后的行（``cancel_requested=true``）。

        Raises:
            ValueError: 参数非法。
            WorkflowRunNotFoundError: 该 run 不存在。
            WorkflowRunConflictError: 未命中（已取消 / 已终态；不做 force update）。
            WorkflowRunRepositoryError: DB 未配置或写入失败。
        """
        run_id = _required_text(
            workflow_run_id,
            field="workflow_run_id",
            max_length=WORKFLOW_RUN_ID_MAX_LENGTH,
        )
        statement = (
            update(WorkflowRunModel)
            .where(
                WorkflowRunModel.workflow_run_id == run_id,
                WorkflowRunModel.cancel_requested.is_(False),
                WorkflowRunModel.status.notin_(tuple(WORKFLOW_RUN_TERMINAL_STATUSES)),
            )
            .values(
                cancel_requested=True,
                cancel_requested_at=func.now(),
                state_version=WorkflowRunModel.state_version + 1,
                updated_at=func.now(),
            )
            .returning(WorkflowRunModel)
        )
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                row = session.execute(statement).scalars().one_or_none()
        except SQLAlchemyError as exc:
            raise WorkflowRunRepositoryError(
                f"Workflow Run 取消信号写入失败: {type(exc).__name__}"
            ) from exc
        if row is None:
            current = self.get(run_id)
            if current is None:
                raise WorkflowRunNotFoundError(
                    f"workflow_run_id 不存在（{WF_ERROR_RUN_NOT_FOUND}）: {run_id!r}"
                )
            raise WorkflowRunConflictError(
                "取消信号未置位（已取消 / 已终态；不做 force update）",
                reason=(
                    "terminal"
                    if current.status in WORKFLOW_RUN_TERMINAL_STATUSES
                    else "already_requested"
                    if current.cancel_requested
                    else "unknown"
                ),
            )
        return row

    # ---------- 删除（**仅**测试 / cleanup） ----------

    def delete_for_test(self, workflow_run_id: str) -> int:
        """删除该 run（FK CASCADE 连带清理其 node_run / approval）；**仅**测试。"""
        run_id = _required_text(
            workflow_run_id,
            field="workflow_run_id",
            max_length=WORKFLOW_RUN_ID_MAX_LENGTH,
        )
        table = WorkflowRunModel.__table__
        statement = table.delete().where(table.c.workflow_run_id == run_id)
        factory = self._get_session_factory()
        try:
            with factory() as session, session.begin():
                result = session.execute(statement)
                deleted = int(result.rowcount or 0)
        except SQLAlchemyError as exc:
            raise WorkflowRunRepositoryError(
                f"Workflow Run 删除失败: {type(exc).__name__}"
            ) from exc
        return deleted

    # ---------- 内部：失败诊断（只读；不影响原子性） ----------

    def _conflict_error(
        self,
        *,
        run_id: str,
        expected_state_version: int,
    ) -> WorkflowRunRepositoryError:
        """诊断 CAS 未命中原因（**仅**失败路径）。"""
        try:
            current = self.get(run_id)
        except WorkflowRunRepositoryError:  # pragma: no cover - 诊断失败
            current = None
        if current is None:
            return WorkflowRunNotFoundError(
                f"workflow_run_id 不存在（{WF_ERROR_RUN_NOT_FOUND}）: {run_id!r}"
            )
        if current.state_version != expected_state_version:
            return WorkflowRunConflictError(
                "Workflow Run 版本冲突（expected="
                f"{expected_state_version}, current={current.state_version}）",
                reason="version",
            )
        return WorkflowRunConflictError(
            "Workflow Run 并发冲突（无法归因；不做 force update）",
            reason="unknown",
        )
