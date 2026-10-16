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
    * ``get_run_detail``        —— Run 只读详情（**不含 input / request_id**）
    * ``get_node_runs``         —— 某 Run 的**全部** node attempt（Port 记录本体；
                                   历史保留、写入序 = 执行序；既有 API 契约）
    * ``get_node_run_details``  —— 同上的**安全投影版**（:class:`NodeRunDetail`）：
                                   ``output`` / ``error_code`` / ``node_type`` /
                                   ``status`` / ``attempt`` / ``elapsed_ms`` 逐项
                                   按域白名单与内联边界校验，损坏 ⇒ fail closed
    * ``get_approval_detail``   —— 单个审批详情（经父 Run 做归属 + 项目校验）
    * ``get_approval_for_node`` —— **按 (run, node) 读取审批**（安全投影版，
                                   :class:`ApprovalDetail`）；无审批行 ⇒ ``None``
    * ``get_run_timeline``      —— 由**真实持久化时间字段**构建的稳定排序时间线

为什么同时保留"Port 记录本体"与"安全投影"两条只读入口：
    ``get_node_runs`` / ``get_approval_detail`` 已被 ``api/workflow_read.py`` 与
    ``command_service`` 消费（既有契约）⇒ **不改其返回类型**；安全投影以**新增
    方法**提供，避免破坏既有调用方，同时让需要严格输出边界的调用方有明确入口。

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

已知限制（报告记录，**不在本层绕过**——不改 Port / Repository / Schema）：
    * ``ApprovalRecord`` 无 ``created_at`` ⇒ 审批**创建时刻**不进入 Timeline /
      Detail（不推断、不伪造）。**仓库核实**：DB 列 ``ai_ops.workflow_approval.
      created_at`` **存在**，但 Port 未暴露 ⇒ 这是 **Store Port 能力缺口**，
      需 Port 最小扩展才能补齐（本层不擅自扩 Port）；
    * ``ApprovalRecord`` / ``NodeRunRecord`` 均**未暴露** ``project_id``，尽管
      DB 列 ``workflow_approval.project_id`` / ``workflow_node_run.project_id``
      存在（ADR §16 I1/I4 冗余列）⇒ 本层无法对子记录做**独立**归属证明，
      只能经父 Run 建立归属（S6 §五.4）；独立证明需 Port 扩展；
    * RunDetail 的节点统计仅含 run 行的 ``attempt_count``（可证明事实）；
      跨表节点计数由 ``get_node_runs`` / ``get_node_run_details`` 明确提供
      （不做隐式额外查询）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from backend.app.workflow.definition import NODE_TYPES
from backend.app.workflow.errors import WF_ERROR_CODES, WorkflowOutputError
from backend.app.workflow.models import validate_json_text
from backend.app.workflow.states import APPROVAL_STATUSES, NODE_STATUSES
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
    "NodeRunDetail",
    "ApprovalDetail",
    "TimelineEvent",
    "TIMELINE_EVENT_KINDS",
    "MAX_ERROR_CODE_LENGTH",
    "WorkflowReadService",
]

#: 稳定错误码列宽（镜像 ``ai_ops.workflow_node_run.error_code`` 的列宽）。
#: 读侧**防御性上界**：仅用于拒绝超限的持久化值，不构成 schema 定义。
MAX_ERROR_CODE_LENGTH: Final[int] = 64

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
class NodeRunDetail:
    """单次 node attempt 的**安全投影**（显式构造；**不**返回 Port 记录本体）。

    与 ``NodeRunDetail`` 对齐的持久化事实来自 ``workflow_node_run``；每个字段在
    构造时按**域层白名单 / 内联边界**校验 ⇒ 持久化数据损坏时 **fail closed**
    （不修复、不截断、不清空、不推断状态）。

    Attributes:
        workflow_run_id / node_id / attempt: 执行身份（attempt ≥ 1）。
        node_type:  ``NODE_TYPES`` 之一（域白名单）。
        status:     ``NODE_STATUSES`` 之一（域白名单；**不推断**未持久化状态）。
        output:     持久化的 **JSON 文本**（``None`` = 无输出）；
                    **必须**为合法 JSON 且 UTF-8 字节数
                    ≤ ``JSON_VALUE_MAX_BYTES``（32 KiB）—— 违规 ⇒ fail closed。
        error_code: ``None`` 或 ``WF_ERROR_CODES`` 域白名单内的稳定码；
                    **不含** exception message / stack trace（列契约如此）。
        elapsed_ms: ``None`` 或有限非负数。
        started_at / completed_at: 真实持久化时间戳（可 None——不伪造）。
    """

    workflow_run_id: str
    node_id: str
    attempt: int
    node_type: str
    status: str
    output: str | None
    error_code: str | None
    elapsed_ms: float | None
    started_at: datetime | None
    completed_at: datetime | None

    def __post_init__(self) -> None:
        _require_projected_text(
            "NodeRunDetail.workflow_run_id", self.workflow_run_id
        )
        _require_projected_text("NodeRunDetail.node_id", self.node_id)
        _require_projected_attempt(self.attempt)
        _require_projected_member(
            "NodeRunDetail.node_type", self.node_type, NODE_TYPES
        )
        _require_projected_member(
            "NodeRunDetail.status", self.status, NODE_STATUSES
        )
        _require_projected_output(self.output)
        _require_projected_error_code(self.error_code)
        _require_projected_elapsed_ms(self.elapsed_ms)
        _require_projected_timestamp("NodeRunDetail.started_at", self.started_at)
        _require_projected_timestamp(
            "NodeRunDetail.completed_at", self.completed_at
        )


@dataclass(frozen=True)
class ApprovalDetail:
    """单个审批的**安全投影**（显式构造；字段 = Port 已持久化事实）。

    **不含** ``created_at``：DB 列 ``workflow_approval.created_at`` 存在，但
    ``ApprovalRecord``（Store Port）**未暴露** ⇒ 本层不推断、不伪造（见模块
    docstring 已知限制）。补齐需 Port 最小扩展，属独立任务书。

    Attributes:
        approval_id / workflow_run_id / node_id: 审批身份与归属。
        status: ``APPROVAL_STATUSES`` 之一（域白名单；PENDING / APPROVED /
                REJECTED）。
        resolved_at: **真实持久化**的裁决时间；未裁决 ⇒ ``None``（不伪造）。
    """

    approval_id: str
    workflow_run_id: str
    node_id: str
    status: str
    resolved_at: datetime | None

    def __post_init__(self) -> None:
        _require_projected_text(
            "ApprovalDetail.approval_id", self.approval_id
        )
        _require_projected_text(
            "ApprovalDetail.workflow_run_id", self.workflow_run_id
        )
        _require_projected_text("ApprovalDetail.node_id", self.node_id)
        _require_projected_member(
            "ApprovalDetail.status", self.status, APPROVAL_STATUSES
        )
        _require_projected_timestamp(
            "ApprovalDetail.resolved_at", self.resolved_at
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

    def get_node_run_details(
        self, *, workflow_run_id: str, project_id: str
    ) -> tuple[NodeRunDetail, ...]:
        """某 Run 的**全部** node attempt 的**安全投影**（含 output 边界校验）。

        复用 :meth:`get_node_runs` ⇒ 父 Run 存在性 / 项目范围 / 归属一致性校验
        只有**一份**实现（不重复判定）；本方法只增加"逐条安全投影"这一步。

        Returns:
            空结果 ⇒ ``()``（合法事实——不是错误）。

        Raises:
            WorkflowReadNotFoundError: Run 不存在 / 不在项目范围内（同构）。
            WorkflowReadError: 存储故障 · 归属不一致 · **任何一条记录的持久化
                字段损坏**（output 非合法 JSON / 超内联上限 / status 或
                node_type 越白名单 / attempt 非法 / error_code 越白名单 /
                elapsed_ms 非法 / 时间戳类型非法）。
        """
        records = self.get_node_runs(
            workflow_run_id=workflow_run_id, project_id=project_id
        )
        return tuple(_project_node_run(record) for record in records)

    def get_approval_for_node(
        self, *, workflow_run_id: str, node_id: str, project_id: str
    ) -> ApprovalDetail | None:
        """按 ``(run, node)`` 读取该 APPROVAL 节点的审批（安全投影）。

        归属校验（**双保险**）：先经父 Run 校验存在性与项目范围；再校验返回行
        的 ``workflow_run_id`` / ``node_id`` 与请求一致——存储层若返回跨 Run /
        跨节点数据 ⇒ **fail closed**（不返回）。

        Returns:
            ``None``: 该节点**尚无审批行**（合法事实——不是错误，也不是权限
                问题；父 Run 已通过项目校验）。
            :class:`ApprovalDetail`: 已存在审批行（PENDING / 已裁决）。

        Raises:
            WorkflowReadNotFoundError: 父 Run 不存在 / 不在项目范围内（同构）。
            WorkflowReadError: 存储故障 · 归属不一致 · 持久化状态越域白名单。
        """
        run = self._require_run(workflow_run_id, project_id)
        node = _require_text("node_id", node_id)
        approval = self._store_call(
            "Workflow 审批节点读取",
            self._approval_store.get_for_node,
            workflow_run_id=run.workflow_run_id,
            node_id=node,
        )
        if approval is None:
            return None
        if (
            approval.workflow_run_id != run.workflow_run_id
            or approval.node_id != node
        ):
            # 存储层归属不一致 ⇒ fail closed（不返回跨 Run / 跨节点数据）
            raise WorkflowReadError(
                "审批归属与父 Run / 节点不一致（fail-closed）"
            )
        return _project_approval(approval)

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
# 投影（Port 记录 → Read DTO；显式逐字段构造）
# ============================================================


def _project_node_run(record: NodeRunRecord) -> NodeRunDetail:
    """``NodeRunRecord`` → :class:`NodeRunDetail`（显式逐字段；**不**透传对象）。

    注意：``NodeRunRecord`` **无** ``project_id``（Port 未暴露）与 **无** ``input``
    ⇒ 投影面天然不含这两类字段。字段值非法时由 DTO 构造 fail closed。
    """
    return NodeRunDetail(
        workflow_run_id=record.workflow_run_id,
        node_id=record.node_id,
        attempt=record.attempt,
        node_type=record.node_type,
        status=record.status,
        output=record.output,
        error_code=record.error_code,
        elapsed_ms=record.elapsed_ms,
        started_at=record.started_at,
        completed_at=record.completed_at,
    )


def _project_approval(record: ApprovalRecord) -> ApprovalDetail:
    """``ApprovalRecord`` → :class:`ApprovalDetail`（显式逐字段）。"""
    return ApprovalDetail(
        approval_id=record.approval_id,
        workflow_run_id=record.workflow_run_id,
        node_id=record.node_id,
        status=record.status,
        resolved_at=record.resolved_at,
    )


# ============================================================
# 投影字段校验（fail closed：拒绝、**不**修复 / 不截断 / 不清空）
# ============================================================


def _require_projected_text(field: str, value: object) -> None:
    """非空 str（去空白后非空）。"""
    if not isinstance(value, str) or not value.strip():
        raise WorkflowReadError(
            f"{field} 必须是非空 str（当前: {type(value).__name__}）"
        )


def _require_projected_attempt(attempt: object) -> None:
    """attempt 必须为 ≥ 1 的 int（**拒** bool —— bool 是 int 的子类）。"""
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        raise WorkflowReadError(
            f"NodeRunDetail.attempt 必须是 int（当前: {type(attempt).__name__}）"
        )
    if attempt < 1:
        raise WorkflowReadError(
            f"NodeRunDetail.attempt 必须 ≥ 1（当前: {attempt}）"
        )


def _require_projected_member(
    field: str, value: object, allowed: frozenset[str]
) -> None:
    """值必须属于域白名单（越界 ⇒ fail closed——不伪装成正常结果）。"""
    if not isinstance(value, str) or value not in allowed:
        raise WorkflowReadError(f"{field} 不在域白名单内: {value!r}")


def _require_projected_output(output: object) -> None:
    """``output``：``None`` 或**合法 JSON 文本**且 ≤ ``JSON_VALUE_MAX_BYTES``。

    校验**委托**域层 :func:`validate_json_text`（与写侧
    ``serialize_json_value`` 同一内联契约的单一事实来源）⇒ 读侧可对**不信任的
    持久化内容**做独立复验，而无需在本层重复 JSON 契约。

    损坏（非 str / 空白 / 非法 JSON / 超内联上限 / 无法 UTF-8 编码）
    ⇒ :class:`WorkflowReadError`：**不截断、不清空、不自动修复**。
    """
    if output is None:
        return
    try:
        validate_json_text(output)
    except WorkflowOutputError as exc:
        # 域层拒绝 ⇒ 读层 fail closed（异常链保留；消息不含内容本体）
        raise WorkflowReadError(
            "NodeRunDetail.output 不符合内联 JSON 契约"
            "（fail-closed：不截断 / 不自动修复）"
        ) from exc


def _require_projected_error_code(error_code: object) -> None:
    """``error_code``：``None`` 或 ``WF_ERROR_CODES`` 内的稳定码（≤ 列宽）。

    列契约明确**禁止** exception message / stack trace / 自由文本 ⇒ 越界即
    fail closed（不截断、不原样返回可能含内部信息的字符串）。
    """
    if error_code is None:
        return
    if not isinstance(error_code, str) or not error_code.strip():
        raise WorkflowReadError(
            "NodeRunDetail.error_code 必须是非空 str 或 None"
            f"（当前: {type(error_code).__name__}）"
        )
    if len(error_code) > MAX_ERROR_CODE_LENGTH:
        raise WorkflowReadError(
            f"NodeRunDetail.error_code 超过列宽上限"
            f"（{len(error_code)} > {MAX_ERROR_CODE_LENGTH}）"
        )
    if error_code not in WF_ERROR_CODES:
        raise WorkflowReadError(
            f"NodeRunDetail.error_code 不在域白名单内: {error_code!r}"
        )


def _require_projected_elapsed_ms(elapsed_ms: object) -> None:
    """``elapsed_ms``：``None`` 或有限非负数（**拒** NaN / inf / 负数 / bool）。

    Note:
        用 ``0 <= value < inf`` 单次比较同时拒绝 NaN（比较为 False）、
        ``±inf`` 与负数——因此本模块**无需** import ``math``
        （read_service 的 stdlib 白名单有意只含 ``dataclasses`` /
        ``datetime`` / ``typing``）。
    """
    if elapsed_ms is None:
        return
    if isinstance(elapsed_ms, bool) or not isinstance(elapsed_ms, (int, float)):
        raise WorkflowReadError(
            "NodeRunDetail.elapsed_ms 必须是数字或 None"
            f"（当前: {type(elapsed_ms).__name__}）"
        )
    if not (0 <= elapsed_ms < float("inf")):
        raise WorkflowReadError(
            f"NodeRunDetail.elapsed_ms 必须有限且 ≥ 0（当前: {elapsed_ms!r}）"
        )


def _require_projected_timestamp(field: str, value: object) -> None:
    """时间戳必须为 ``None`` 或 ``datetime``（不推断、不字符串化）。"""
    if value is None:
        return
    if not isinstance(value, datetime):
        raise WorkflowReadError(
            f"{field} 必须是 datetime 或 None（当前: {type(value).__name__}）"
        )


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
