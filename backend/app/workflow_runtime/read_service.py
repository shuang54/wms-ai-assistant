"""Workflow Read Layer（Phase 6 Step 6 · S6；**只读**）。

目标架构（S6 任务书 §一）：

```text
Application / Future API
      ↓            （未来 API 层自行经 asyncio.to_thread 调用——本层同步）
WorkflowReadService（本模块）
      ↓            （**只经** Store Ports；不触 ORM / Repository / Adapter）
WorkflowRunStore / WorkflowNodeRunStore / WorkflowApprovalStore
      ↓
现有 Store Adapters → Repositories → PostgreSQL
```

职责（**只读 + 授权范围校验 + DTO 投影 + 结果组装**）：
    * ``get_run_detail``       —— Run 只读详情（**不含 input / request_id**）
    * ``get_node_runs``        —— 某 Run 的**全部** node attempt（历史保留、
                                  写入序 = 执行序；不重建未持久化状态）
    * ``get_approval_detail``  —— 单个审批详情（经父 Run 做归属 + 项目校验）
    * ``get_run_timeline``     —— 由**真实持久化时间字段**构建的稳定排序时间线

硬约束（S6 任务书 §三/§六/§七/§九）：
    * 不做任何写入：不调用 Runtime 的 start / advance / request_cancel；
      不调用 Tool / LLM / Orchestrator；查询失败不改变数据库状态；
    * 不直接触达 ORM / Repository / Adapter / Session——只经 Store Ports；
    * Read DTO **显式构造**：不返回 ORM 对象、不 ``dict()`` / ``__dict__`` 透传；
      ``input`` / 凭证 / URL / 内部异常正文 **不进入**任何 DTO；
    * Project Isolation：所有公开入口要求 ``project_id``；Run 校验
      ``run.project_id``；子记录（Node / Approval 均**不携带** project_id）
      经**父 Run** 建立归属校验——不虚构字段；
      "不存在"与"项目不匹配"返回**同一** :class:`WorkflowReadNotFoundError`
      （不泄漏其它项目资源是否存在）——fail closed；
    * Timeline 只记录**持久化的时间戳事实**（不推断状态转变、不伪造时间）；
      同一时间使用稳定次级排序（kind rank → 身份键）；
    * 不实现全局 Run 列表（现有 Store 无项目范围列表能力——未经检查不放宽）。

边界（AST 测试锁定）：仅 import stdlib + ``backend.app.workflow``（域状态
白名单）+ ``backend.app.workflow_runtime.ports``；不 import db / services /
tools / api / llm / agent / projects；不 import 同包 runtime / execution /
binding / factory / adapters / store_adapters（无反向 / 循环依赖）。

已知限制（报告记录，不在本层绕过）：
    * ``ApprovalRecord`` 无 ``created_at`` ⇒ 审批**创建时刻**不进入 Timeline /
      Detail（不推断、不伪造；如未来需要属 ports 最小扩展）；
    * RunDetail 的节点统计仅含 run 行的 ``attempt_count``（可证明事实）；
      跨表节点计数由 ``get_node_runs`` 明确提供（不做隐式额外查询）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from backend.app.workflow.states import WORKFLOW_STATUSES
from backend.app.workflow_runtime.ports import (
    ApprovalRecord,
    NodeRunRecord,
    RunRecord,
    WorkflowApprovalStore,
    WorkflowNodeRunStore,
    WorkflowPortsError,
    WorkflowRunStore,
)

__all__ = [
    "WorkflowReadError",
    "WorkflowReadNotFoundError",
    "RunDetail",
    "TimelineEvent",
    "TIMELINE_EVENT_KINDS",
    "WorkflowReadService",
]

#: 时间线事件类型白名单（只描述**持久化时间戳事实**——不含状态推断）。
TIMELINE_EVENT_KINDS: Final[frozenset[str]] = frozenset(
    {
        "RUN_CREATED",
        "RUN_STARTED",
        "RUN_FINISHED",
        "NODE_ATTEMPT_STARTED",
        "NODE_ATTEMPT_FINISHED",
        "APPROVAL_RESOLVED",
    }
)

#: 同时间事件的稳定次级排序（rank 越小越先；随后按身份键）。
_KIND_RANK: Final[dict[str, int]] = {
    "RUN_CREATED": 0,
    "RUN_STARTED": 1,
    "NODE_ATTEMPT_STARTED": 2,
    "NODE_ATTEMPT_FINISHED": 3,
    "APPROVAL_RESOLVED": 4,
    "RUN_FINISHED": 5,
}


class WorkflowReadError(Exception):
    """Read Layer 错误（**明确失败**；不返回虚构默认对象）。

    存储层异常经 ``from exc`` 包装（消息只含类型名——不泄漏异常正文）；
    数据契约损坏（归属不一致 / 状态白名单外）同样以本家族 fail closed。
    """


class WorkflowReadNotFoundError(WorkflowReadError):
    """资源不存在 **或** 不在请求的 ``project_id`` 授权范围内。

    两种情形**同构**（同一异常 / 同一消息模式）——不泄漏其它项目资源是否
    存在（S6 §五.5）。调用方不得据本异常推断存在性。
    """


@dataclass(frozen=True)
class RunDetail:
    """Run 只读详情（显式投影；**不含** ``input`` / ``request_id`` / ORM 内部）。

    Attributes:
        workflow_run_id / workflow_id / workflow_version / project_id:
            身份与作用域（服务器端解析结果）。
        status:         域白名单状态（``WORKFLOW_STATUSES``；构造即校验）。
        current_node_id / next_action / state_version: 控制指针事实。
        attempt_count:  run 行持久化的累计 attempt 数（可证明统计）。
        cancel_requested / cancel_requested_at: 协作式取消信号事实。
        created_at / started_at / completed_at / updated_at:
            真实持久化时间戳（可 None——不伪造）。
    """

    workflow_run_id: str
    workflow_id: str
    workflow_version: str
    project_id: str
    status: str
    current_node_id: str | None
    next_action: str
    state_version: int
    attempt_count: int
    cancel_requested: bool
    cancel_requested_at: datetime | None
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime | None

    def __post_init__(self) -> None:
        for field_name in (
            "workflow_run_id",
            "workflow_id",
            "workflow_version",
            "project_id",
            "next_action",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise WorkflowReadError(
                    f"RunDetail.{field_name} 必须是非空 str"
                )
        if not isinstance(self.status, str) or self.status not in (
            WORKFLOW_STATUSES
        ):
            # 数据违反域白名单 ⇒ fail closed（不伪装成正常结果）
            raise WorkflowReadError(
                f"RunDetail.status 不在域白名单内: {self.status!r}"
            )


@dataclass(frozen=True)
class TimelineEvent:
    """由持久化时间戳构建的时间线事件（**零推断**；frozen）。

    Attributes:
        kind:             ``TIMELINE_EVENT_KINDS`` 之一（构造即校验）。
        timestamp:        真实持久化时间字段值（非构造 / 非推断）。
        workflow_run_id:  所属 Run。
        node_id / attempt / approval_id: 事件身份（按 kind 语义取值，其余 None）。
    """

    kind: str
    timestamp: datetime
    workflow_run_id: str
    node_id: str | None = None
    attempt: int | None = None
    approval_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in (
            TIMELINE_EVENT_KINDS
        ):
            raise WorkflowReadError(
                f"TimelineEvent.kind 非法: {self.kind!r}"
            )
        if not isinstance(self.timestamp, datetime):
            raise WorkflowReadError("TimelineEvent.timestamp 必须是 datetime")
        if not isinstance(self.workflow_run_id, str) or not (
            self.workflow_run_id.strip()
        ):
            raise WorkflowReadError("TimelineEvent.workflow_run_id 必须是非空 str")


class WorkflowReadService:
    """Workflow 只读查询服务（**无状态**；仅注入三个 Store Port）。"""

    def __init__(
        self,
        *,
        run_store: WorkflowRunStore,
        node_run_store: WorkflowNodeRunStore,
        approval_store: WorkflowApprovalStore,
    ) -> None:
        self._run_store = run_store
        self._node_run_store = node_run_store
        self._approval_store = approval_store

    # ========================================================
    # 公开查询（全部只读）
    # ========================================================

    def get_run_detail(
        self, *, workflow_run_id: str, project_id: str
    ) -> RunDetail:
        """Run 只读详情（项目范围校验后显式投影；不含 input）。

        Raises:
            WorkflowReadNotFoundError: 不存在 / 不在项目授权范围内（同构）。
            WorkflowReadError:          存储故障 / 参数非法 / 数据损坏。
        """
        run = self._require_run(workflow_run_id, project_id)
        return RunDetail(
            workflow_run_id=run.workflow_run_id,
            workflow_id=run.workflow_id,
            workflow_version=run.workflow_version,
            project_id=run.project_id,
            status=run.status,
            current_node_id=run.current_node_id,
            next_action=run.next_action,
            state_version=run.state_version,
            attempt_count=run.attempt_count,
            cancel_requested=run.cancel_requested,
            cancel_requested_at=run.cancel_requested_at,
            created_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
            updated_at=run.updated_at,
        )

    def get_node_runs(
        self, *, workflow_run_id: str, project_id: str
    ) -> tuple[NodeRunRecord, ...]:
        """某 Run 的**全部** node attempt（历史保留；写入序 = 执行序）。

        每条记录的 ``workflow_run_id`` 必须与父 Run 一致（归属校验；子记录
        不携带 project_id —— S6 §五.4：经父 Run 建立归属，不虚构字段）。

        Returns:
            空结果 ⇒ ``()``（合法事实——不是错误）。
        """
        run = self._require_run(workflow_run_id, project_id)
        records = self._store_call(
            "Node attempt 列表读取", self._node_run_store.list_for_run,
            run.workflow_run_id,
        )
        for record in records:
            if record.workflow_run_id != run.workflow_run_id:
                # 存储层归属不一致 ⇒ fail closed（不返回跨 Run 数据）
                raise WorkflowReadError(
                    "Node attempt 归属与父 Run 不一致（fail-closed）"
                )
        return tuple(records)

    def get_approval_detail(
        self, *, approval_id: str, project_id: str
    ) -> ApprovalRecord:
        """单个审批详情（经父 Run 做归属 + 项目校验）。

        Raises:
            WorkflowReadNotFoundError: 审批不存在 / 父 Run 不存在 /
                父 Run 不在项目授权范围内（同构——不泄漏存在性）。
        """
        approval_id_value = _require_text("approval_id", approval_id)
        approval = self._store_call(
            "Workflow 审批读取", self._approval_store.get, approval_id_value
        )
        if approval is None:
            raise _not_found("approval", approval_id_value)
        run = self._require_run(approval.workflow_run_id, project_id)
        if approval.workflow_run_id != run.workflow_run_id:
            raise WorkflowReadError(
                "审批归属与父 Run 不一致（fail-closed）"
            )
        return approval

    def get_run_timeline(
        self, *, workflow_run_id: str, project_id: str
    ) -> tuple[TimelineEvent, ...]:
        """由**真实持久化时间字段**构建的时间线（稳定排序；零推断）。

        事件来源（仅当时间字段非空——不伪造 / 不推断）：
            run.created_at → RUN_CREATED；run.started_at → RUN_STARTED；
            run.completed_at → RUN_FINISHED；
            attempt.started_at → NODE_ATTEMPT_STARTED；
            attempt.completed_at → NODE_ATTEMPT_FINISHED；
            approval.resolved_at → APPROVAL_RESOLVED
            （审批经每个 APPROVAL 节点的 ``get_for_node`` 枚举——
             ``(run, node)`` 唯一 ⇒ 完整；``ApprovalRecord`` 无 created_at
             ⇒ 创建时刻不在本时间线——见模块 docstring 已知限制）。

        排序：``(timestamp, kind rank, 身份键)`` —— 同一时间有确定次级序。
        """
        run = self._require_run(workflow_run_id, project_id)
        attempts = self.get_node_runs(
            workflow_run_id=run.workflow_run_id, project_id=project_id
        )

        events: list[TimelineEvent] = []
        _append_if_time(
            events, "RUN_CREATED", run.created_at, run.workflow_run_id
        )
        _append_if_time(
            events, "RUN_STARTED", run.started_at, run.workflow_run_id
        )
        for record in attempts:
            _append_if_time(
                events,
                "NODE_ATTEMPT_STARTED",
                record.started_at,
                run.workflow_run_id,
                node_id=record.node_id,
                attempt=record.attempt,
            )
            _append_if_time(
                events,
                "NODE_ATTEMPT_FINISHED",
                record.completed_at,
                run.workflow_run_id,
                node_id=record.node_id,
                attempt=record.attempt,
            )
        for node_id in _approval_node_ids(attempts):
            approval = self._store_call(
                "Workflow 审批节点读取",
                self._approval_store.get_for_node,
                workflow_run_id=run.workflow_run_id,
                node_id=node_id,
            )
            if approval is None:
                continue  # 尚无审批行的 APPROVAL attempt——无时间事实可记
            _append_if_time(
                events,
                "APPROVAL_RESOLVED",
                approval.resolved_at,
                run.workflow_run_id,
                node_id=approval.node_id,
                approval_id=approval.approval_id,
            )
        _append_if_time(
            events, "RUN_FINISHED", run.completed_at, run.workflow_run_id
        )

        try:
            events.sort(key=_timeline_sort_key)
        except TypeError as exc:
            # 时间戳混合 naive / aware（数据损坏）⇒ fail closed（不修复）
            raise WorkflowReadError(
                "Timeline 时间戳类型不一致（fail-closed；不自动修复）"
            ) from exc
        return tuple(events)

    # ========================================================
    # 内部：授权范围 + 存储边界
    # ========================================================

    def _require_run(
        self, workflow_run_id: str, project_id: str
    ) -> RunRecord:
        """读取 Run 并校验项目授权范围（同构 NotFound；fail closed）。"""
        run_id = _require_text("workflow_run_id", workflow_run_id)
        scope = _require_text("project_id", project_id)
        run = self._store_call(
            "Workflow Run 读取", self._run_store.get, run_id
        )
        if run is None:
            raise _not_found("run", run_id)
        if run.project_id != scope:
            # 与"不存在"同构——不泄漏其它项目资源是否存在（S6 §五.5）
            raise _not_found("run", run_id)
        return run

    def _store_call(
        self, operation: str, fn: Any, *args: Any, **kwargs: Any
    ) -> Any:
        try:
            return fn(*args, **kwargs)
        except WorkflowPortsError as exc:
            raise WorkflowReadError(
                f"{operation}失败: {type(exc).__name__}"
            ) from exc
        except WorkflowReadError:
            raise
        except Exception as exc:  # 未知异常 ⇒ 安全包装（异常链保留）
            raise WorkflowReadError(
                f"{operation}失败: {type(exc).__name__}"
            ) from exc


# ============================================================
# 纯函数辅助
# ============================================================


def _require_text(field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowReadError(
            f"{field} 必须是非空 str（当前: {type(value).__name__}）"
        )
    return value


def _not_found(kind: str, identity: str) -> WorkflowReadNotFoundError:
    """不存在 / 不匹配的同构错误（同一消息模式——不泄漏存在性）。"""
    label = "Workflow Run" if kind == "run" else "Workflow 审批"
    return WorkflowReadNotFoundError(
        f"{label}不存在或不在请求的项目范围内: {identity!r}"
    )


def _append_if_time(
    events: list[TimelineEvent],
    kind: str,
    timestamp: datetime | None,
    workflow_run_id: str,
    *,
    node_id: str | None = None,
    attempt: int | None = None,
    approval_id: str | None = None,
) -> None:
    """仅当**持久化时间字段非空**时追加事件（零推断 / 零伪造）。"""
    if timestamp is None:
        return
    events.append(
        TimelineEvent(
            kind=kind,
            timestamp=timestamp,
            workflow_run_id=workflow_run_id,
            node_id=node_id,
            attempt=attempt,
            approval_id=approval_id,
        )
    )


def _approval_node_ids(
    attempts: tuple[NodeRunRecord, ...],
) -> tuple[str, ...]:
    """出现过的 APPROVAL 节点（写入序去重；``(run, node)`` 唯一 ⇒ 枚举完整）。"""
    seen: list[str] = []
    for record in attempts:
        if record.node_type != "APPROVAL":
            continue
        if record.node_id not in seen:
            seen.append(record.node_id)
    return tuple(seen)


def _timeline_sort_key(event: TimelineEvent) -> tuple:
    """稳定排序键：时间 → kind rank → 身份键（node_id / attempt / approval）。"""
    return (
        event.timestamp,
        _KIND_RANK.get(event.kind, len(_KIND_RANK)),
        event.node_id or "",
        event.attempt if event.attempt is not None else 0,
        event.approval_id or "",
    )
