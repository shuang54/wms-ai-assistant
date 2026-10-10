"""Workflow Runtime Store 适配器（Phase 6 Step 5 · GAP-2；装配层）。

把真实 Workflow Repository 适配到 S1 冻结 Store 协议（鸭子类型；
**不修改**被适配者）：

```text
WorkflowRunStoreAdapter       : WorkflowRunRepository       → WorkflowRunStore
WorkflowNodeRunStoreAdapter   : WorkflowNodeRunRepository   → WorkflowNodeRunStore
WorkflowApprovalStoreAdapter  : WorkflowApprovalRepository  → WorkflowApprovalStore
```

纪律（S5 GAP-2 授权 + S1 ports docstring 映射表）：
    * 方法签名 = S1 Ports 真实签名（**不**新增 / 不修改 ports 公共面）；
    * ORM 行 ⇒ **显式字段映射**为 frozen DTO（``RunRecord`` / ``NodeRunRecord``
      / ``ApprovalRecord``）；禁止 ``dict(row)`` / ``row.__dict__`` / 直接透传
      ORM 对象；Session / Connection / Engine **不**进入返回值；
    * 已知 Repository 异常按 S1 映射表映射；其余（含意外类型）⇒ 安全
      ``WorkflowPortsError``（``from exc`` 保留异常链；消息只含类型名或
      稳定白名单值——**不**含凭证 / URL / 完整异常正文）；
    * 不重试 / 不吞失败 / 不改事务边界（每次调用 = Repository 的独立事务）。

边界（AST 测试锁定）：本模块是**装配层**，允许 import db（Repository）；
Domain / ports / binding / execution / runtime 不 import 本模块。
"""
from __future__ import annotations

from typing import Any

from backend.app.db.workflow_approval_repository import (
    WorkflowApprovalRepositoryError,
)
from backend.app.db.workflow_node_run_repository import (
    WorkflowNodeRunAlreadyExistsError,
    WorkflowNodeRunConflictError,
    WorkflowNodeRunNotFoundError,
    WorkflowNodeRunRepositoryError,
)
from backend.app.db.workflow_run_repository import (
    WorkflowRunAlreadyExistsError,
    WorkflowRunConflictError,
    WorkflowRunNotFoundError,
    WorkflowRunRepositoryError,
)
from backend.app.workflow.states import WORKFLOW_STATUS_PENDING
from backend.app.workflow_runtime.ports import (
    NEXT_ACTION_NONE,
    RUN_CONFLICT_REASON_UNKNOWN,
    RUN_CONFLICT_REASONS,
    ApprovalRecord,
    NodeRunAlreadyExistsError,
    NodeRunConflictError,
    NodeRunNotFoundError,
    NodeRunRecord,
    RunConflictError,
    RunNotFoundError,
    RunRecord,
    WorkflowPortsError,
)

__all__ = [
    "WorkflowRunStoreAdapter",
    "WorkflowNodeRunStoreAdapter",
    "WorkflowApprovalStoreAdapter",
]


# ============================================================
# 显式字段映射（ORM 行 → frozen DTO；不透传任何 ORM 内部对象）
# ============================================================


def _to_run_record(row: Any) -> RunRecord:
    """``WorkflowRunModel`` ⇒ :class:`RunRecord`（**显式**字段映射）。"""
    return RunRecord(
        workflow_run_id=row.workflow_run_id,
        workflow_id=row.workflow_id,
        workflow_version=row.workflow_version,
        request_id=row.request_id,
        project_id=row.project_id,
        status=row.status,
        state_version=row.state_version,
        current_node_id=row.current_node_id,
        next_action=row.next_action,
        attempt_count=row.attempt_count,
        cancel_requested=row.cancel_requested,
        cancel_requested_at=row.cancel_requested_at,
        input=row.input,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        updated_at=row.updated_at,
    )


def _to_node_run_record(row: Any) -> NodeRunRecord:
    """``WorkflowNodeRunModel`` ⇒ :class:`NodeRunRecord`（显式字段映射）。"""
    return NodeRunRecord(
        workflow_run_id=row.workflow_run_id,
        node_id=row.node_id,
        attempt=row.attempt,
        node_type=row.node_type,
        status=row.status,
        output=row.output,
        error_code=row.error_code,
        elapsed_ms=row.elapsed_ms,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _to_approval_record(row: Any) -> ApprovalRecord:
    """``WorkflowApprovalModel`` ⇒ :class:`ApprovalRecord`（显式字段映射）。"""
    return ApprovalRecord(
        approval_id=row.approval_id,
        workflow_run_id=row.workflow_run_id,
        node_id=row.node_id,
        status=row.status,
        resolved_at=row.resolved_at,
    )


def _unknown_error(operation: str, exc: BaseException) -> WorkflowPortsError:
    """未知 / 未映射异常 ⇒ 安全端口错误（仅类型名；异常链保留）。"""
    return WorkflowPortsError(f"{operation} 失败: {type(exc).__name__}")


def _run_conflict(exc: WorkflowRunConflictError) -> RunConflictError:
    """run 冲突 ⇒ 端口分类冲突（reason 白名单校验；意外值 ⇒ unknown）。"""
    reason = (
        exc.reason if exc.reason in RUN_CONFLICT_REASONS else RUN_CONFLICT_REASON_UNKNOWN
    )
    return RunConflictError(
        f"Workflow Run 状态冲突（reason={reason}）", reason=reason
    )


# ============================================================
# 1) Run Store
# ============================================================


class WorkflowRunStoreAdapter:
    """``WorkflowRunRepository`` → :class:`WorkflowRunStore`。"""

    def __init__(self, repository: Any) -> None:
        if repository is None:
            raise TypeError("repository 不能为 None")
        for name in ("create", "get", "update_cas", "request_cancel"):
            if not callable(getattr(repository, name, None)):
                raise TypeError(
                    f"repository 必须提供可调用的 {name}()"
                    f"（got {type(repository).__name__}）"
                )
        self._repository = repository

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
        next_action: str = NEXT_ACTION_NONE,
    ) -> RunRecord:
        try:
            row = self._repository.create(
                workflow_run_id=workflow_run_id,
                workflow_id=workflow_id,
                workflow_version=workflow_version,
                request_id=request_id,
                project_id=project_id,
                input=input,
                status=status,
                current_node_id=current_node_id,
                next_action=next_action,
            )
        except WorkflowRunAlreadyExistsError as exc:
            raise WorkflowPortsError(
                f"Workflow Run 已存在（{type(exc).__name__}）"
            ) from exc
        except WorkflowRunConflictError as exc:
            raise _run_conflict(exc) from exc
        except WorkflowRunNotFoundError as exc:
            raise RunNotFoundError(workflow_run_id) from exc
        except WorkflowRunRepositoryError as exc:
            raise _unknown_error("Workflow Run 创建", exc) from exc
        except Exception as exc:  # 未知异常 ⇒ 安全映射（保留异常链）
            raise _unknown_error("Workflow Run 创建", exc) from exc
        return _to_run_record(row)

    def get(self, workflow_run_id: str) -> RunRecord | None:
        try:
            row = self._repository.get(workflow_run_id)
        except WorkflowRunNotFoundError as exc:
            raise RunNotFoundError(workflow_run_id) from exc
        except WorkflowRunConflictError as exc:
            raise _run_conflict(exc) from exc
        except WorkflowRunRepositoryError as exc:
            raise _unknown_error("Workflow Run 读取", exc) from exc
        except Exception as exc:
            raise _unknown_error("Workflow Run 读取", exc) from exc
        return _to_run_record(row) if row is not None else None

    def update_cas(
        self,
        *,
        workflow_run_id: str,
        expected_state_version: int,
        status: str,
        current_node_id: str | None,
        next_action: str,
        attempt_count: int,
    ) -> RunRecord:
        try:
            row = self._repository.update_cas(
                workflow_run_id=workflow_run_id,
                expected_state_version=expected_state_version,
                status=status,
                current_node_id=current_node_id,
                next_action=next_action,
                attempt_count=attempt_count,
            )
        except WorkflowRunNotFoundError as exc:
            raise RunNotFoundError(workflow_run_id) from exc
        except WorkflowRunConflictError as exc:
            raise _run_conflict(exc) from exc
        except WorkflowRunRepositoryError as exc:
            raise _unknown_error("Workflow Run CAS 推进", exc) from exc
        except Exception as exc:
            raise _unknown_error("Workflow Run CAS 推进", exc) from exc
        return _to_run_record(row)

    def request_cancel(self, workflow_run_id: str) -> RunRecord:
        try:
            row = self._repository.request_cancel(workflow_run_id)
        except WorkflowRunNotFoundError as exc:
            raise RunNotFoundError(workflow_run_id) from exc
        except WorkflowRunConflictError as exc:
            raise _run_conflict(exc) from exc
        except WorkflowRunRepositoryError as exc:
            raise _unknown_error("Workflow Run 取消信号", exc) from exc
        except Exception as exc:
            raise _unknown_error("Workflow Run 取消信号", exc) from exc
        return _to_run_record(row)


# ============================================================
# 2) NodeRun Store
# ============================================================


class WorkflowNodeRunStoreAdapter:
    """``WorkflowNodeRunRepository`` → :class:`WorkflowNodeRunStore`。"""

    def __init__(self, repository: Any) -> None:
        if repository is None:
            raise TypeError("repository 不能为 None")
        for name in (
            "create",
            "get",
            "get_latest_attempt",
            "list_for_run",
            "update",
        ):
            if not callable(getattr(repository, name, None)):
                raise TypeError(
                    f"repository 必须提供可调用的 {name}()"
                    f"（got {type(repository).__name__}）"
                )
        self._repository = repository

    def create(
        self,
        *,
        workflow_run_id: str,
        node_id: str,
        attempt: int,
        node_type: str,
        status: str = "RUNNING",
        output: str | None = None,
        error_code: str | None = None,
        elapsed_ms: float | None = None,
        started_at: Any = None,
        completed_at: Any = None,
    ) -> NodeRunRecord:
        try:
            row = self._repository.create(
                workflow_run_id=workflow_run_id,
                node_id=node_id,
                attempt=attempt,
                node_type=node_type,
                status=status,
                output=output,
                error_code=error_code,
                elapsed_ms=elapsed_ms,
                started_at=started_at,
                completed_at=completed_at,
            )
        except WorkflowNodeRunAlreadyExistsError as exc:
            raise NodeRunAlreadyExistsError(
                workflow_run_id, node_id, attempt
            ) from exc
        except WorkflowNodeRunConflictError as exc:
            raise NodeRunConflictError("attempt 已终态（终态不可逆）") from exc
        except WorkflowNodeRunNotFoundError as exc:
            raise NodeRunNotFoundError(
                workflow_run_id, node_id, attempt
            ) from exc
        except WorkflowNodeRunRepositoryError as exc:
            raise _unknown_error("Node attempt 创建", exc) from exc
        except Exception as exc:
            raise _unknown_error("Node attempt 创建", exc) from exc
        return _to_node_run_record(row)

    def get(
        self, *, workflow_run_id: str, node_id: str, attempt: int
    ) -> NodeRunRecord | None:
        try:
            row = self._repository.get(
                workflow_run_id=workflow_run_id,
                node_id=node_id,
                attempt=attempt,
            )
        except WorkflowNodeRunAlreadyExistsError as exc:
            raise NodeRunAlreadyExistsError(
                workflow_run_id, node_id, attempt
            ) from exc
        except WorkflowNodeRunConflictError as exc:
            raise NodeRunConflictError("attempt 已终态（终态不可逆）") from exc
        except WorkflowNodeRunNotFoundError as exc:
            raise NodeRunNotFoundError(
                workflow_run_id, node_id, attempt
            ) from exc
        except WorkflowNodeRunRepositoryError as exc:
            raise _unknown_error("Node attempt 读取", exc) from exc
        except Exception as exc:
            raise _unknown_error("Node attempt 读取", exc) from exc
        return _to_node_run_record(row) if row is not None else None

    def get_latest_attempt(
        self, *, workflow_run_id: str, node_id: str
    ) -> NodeRunRecord | None:
        try:
            row = self._repository.get_latest_attempt(
                workflow_run_id=workflow_run_id, node_id=node_id
            )
        except WorkflowNodeRunRepositoryError as exc:
            raise _unknown_error("Node attempt 最新读取", exc) from exc
        except Exception as exc:
            raise _unknown_error("Node attempt 最新读取", exc) from exc
        return _to_node_run_record(row) if row is not None else None

    def list_for_run(
        self, workflow_run_id: str
    ) -> tuple[NodeRunRecord, ...]:
        try:
            rows = self._repository.list_for_run(workflow_run_id)
        except WorkflowNodeRunRepositoryError as exc:
            raise _unknown_error("Node attempt 列表读取", exc) from exc
        except Exception as exc:
            raise _unknown_error("Node attempt 列表读取", exc) from exc
        return tuple(_to_node_run_record(row) for row in rows)

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
        completed_at: Any = None,
    ) -> NodeRunRecord:
        try:
            row = self._repository.update(
                workflow_run_id=workflow_run_id,
                node_id=node_id,
                attempt=attempt,
                status=status,
                output=output,
                error_code=error_code,
                elapsed_ms=elapsed_ms,
                completed_at=completed_at,
            )
        except WorkflowNodeRunConflictError as exc:
            raise NodeRunConflictError("attempt 已终态（终态不可逆）") from exc
        except WorkflowNodeRunNotFoundError as exc:
            raise NodeRunNotFoundError(
                workflow_run_id, node_id, attempt
            ) from exc
        except WorkflowNodeRunAlreadyExistsError as exc:
            raise NodeRunAlreadyExistsError(
                workflow_run_id, node_id, attempt
            ) from exc
        except WorkflowNodeRunRepositoryError as exc:
            raise _unknown_error("Node attempt 更新", exc) from exc
        except Exception as exc:
            raise _unknown_error("Node attempt 更新", exc) from exc
        return _to_node_run_record(row)


# ============================================================
# 3) Approval Store
# ============================================================


class WorkflowApprovalStoreAdapter:
    """``WorkflowApprovalRepository`` → :class:`WorkflowApprovalStore`。"""

    def __init__(self, repository: Any) -> None:
        if repository is None:
            raise TypeError("repository 不能为 None")
        for name in ("create", "get", "get_for_node", "resolve"):
            if not callable(getattr(repository, name, None)):
                raise TypeError(
                    f"repository 必须提供可调用的 {name}()"
                    f"（got {type(repository).__name__}）"
                )
        self._repository = repository

    def create(
        self, *, approval_id: str, workflow_run_id: str, node_id: str
    ) -> ApprovalRecord:
        try:
            row = self._repository.create(
                approval_id=approval_id,
                workflow_run_id=workflow_run_id,
                node_id=node_id,
            )
        except WorkflowApprovalRepositoryError as exc:
            raise _unknown_error("Workflow 审批创建", exc) from exc
        except Exception as exc:
            raise _unknown_error("Workflow 审批创建", exc) from exc
        return _to_approval_record(row)

    def get(self, approval_id: str) -> ApprovalRecord | None:
        try:
            row = self._repository.get(approval_id)
        except WorkflowApprovalRepositoryError as exc:
            raise _unknown_error("Workflow 审批读取", exc) from exc
        except Exception as exc:
            raise _unknown_error("Workflow 审批读取", exc) from exc
        return _to_approval_record(row) if row is not None else None

    def get_for_node(
        self, *, workflow_run_id: str, node_id: str
    ) -> ApprovalRecord | None:
        try:
            row = self._repository.get_for_node(
                workflow_run_id=workflow_run_id, node_id=node_id
            )
        except WorkflowApprovalRepositoryError as exc:
            raise _unknown_error("Workflow 审批节点读取", exc) from exc
        except Exception as exc:
            raise _unknown_error("Workflow 审批节点读取", exc) from exc
        return _to_approval_record(row) if row is not None else None

    def resolve(
        self, *, approval_id: str, decision: str
    ) -> ApprovalRecord | None:
        """条件裁决；**无转换** ⇒ ``None``（不存在 / 已裁决——语义保持）。"""
        try:
            row = self._repository.resolve(
                approval_id=approval_id, decision=decision
            )
        except WorkflowApprovalRepositoryError as exc:
            raise _unknown_error("Workflow 审批裁决", exc) from exc
        except Exception as exc:
            raise _unknown_error("Workflow 审批裁决", exc) from exc
        return _to_approval_record(row) if row is not None else None
