"""Phase 6 Step 5 — GAP-2 Store Adapter 契约测试（**全离线**）。

覆盖（S5 GAP-2 授权 §三 + §五A）：
```text
· 三个 Adapter 的 ORM 行 ⇒ DTO **显式字段映射**（不外泄 ORM / Session / 行对象）
· 结构协议：isinstance(adapter, ports.Protocol)（runtime_checkable）
· 已知异常映射（S1 映射表）：NotFound / Conflict(reason) / AlreadyExists 家族
· 未知异常 ⇒ 安全 WorkflowPortsError（异常链保留；消息不含正文）
· resolve 的 None 语义保持（无转换）
· 构造期拒绝 None / 缺方法
```
不触 DB：以内存 Fake Repository（返回属性式行对象 / 抛真实 Repository 异常类）驱动。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

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
from backend.app.workflow_runtime.ports import (
    ApprovalRecord,
    NodeRunAlreadyExistsError,
    NodeRunConflictError,
    NodeRunNotFoundError,
    NodeRunRecord,
    RunConflictError,
    RunNotFoundError,
    RunRecord,
    WorkflowApprovalStore,
    WorkflowNodeRunStore,
    WorkflowPortsError,
    WorkflowRunStore,
)
from backend.app.workflow_runtime.store_adapters import (
    WorkflowApprovalStoreAdapter,
    WorkflowNodeRunStoreAdapter,
    WorkflowRunStoreAdapter,
)

__all__ = [
    "TestRunStoreAdapter",
    "TestNodeRunStoreAdapter",
    "TestApprovalStoreAdapter",
]

_SECRET = "postgresql://user:SUPER-SECRET@host/db"


class _Row:
    """属性式行对象（模拟 ORM 实体；含"危险"字段用于泄漏断言）。"""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)
        self._sa_instance_state = "ORM-INTERNAL"  # 模拟 ORM 内部状态
        self.connection = _SECRET  # 模拟不应外泄的对象


def _run_row(**overrides: Any) -> _Row:
    base = {
        "workflow_run_id": "wf-run-1",
        "workflow_id": "flow",
        "workflow_version": "v1",
        "request_id": "req-1",
        "project_id": "proj-a",
        "input": '{"a":1}',
        "status": "PENDING",
        "state_version": 0,
        "current_node_id": None,
        "next_action": "NONE",
        "attempt_count": 0,
        "cancel_requested": False,
        "cancel_requested_at": None,
        "created_at": None,
        "started_at": None,
        "completed_at": None,
        "updated_at": None,
    }
    base.update(overrides)
    return _Row(**base)


class _FakeRunRepo:
    def __init__(self, *, result: Any = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._result = result if result is not None else _run_row()
        self._error = error

    def _respond(self, name: str, kwargs: dict) -> Any:
        self.calls.append((name, kwargs))
        if self._error is not None:
            raise self._error
        return self._result

    def create(self, **kwargs):
        return self._respond("create", kwargs)

    def get(self, workflow_run_id):
        return self._respond("get", {"workflow_run_id": workflow_run_id})

    def update_cas(self, **kwargs):
        return self._respond("update_cas", kwargs)

    def request_cancel(self, workflow_run_id):
        return self._respond("request_cancel", {"workflow_run_id": workflow_run_id})


def _node_row(**overrides: Any) -> _Row:
    base = {
        "workflow_run_id": "wf-run-1",
        "node_id": "n1",
        "attempt": 1,
        "node_type": "TOOL",
        "status": "RUNNING",
        "output": None,
        "error_code": None,
        "elapsed_ms": None,
        "started_at": None,
        "completed_at": None,
    }
    base.update(overrides)
    return _Row(**base)


class _FakeNodeRepo:
    def __init__(self, *, result: Any = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._result = result if result is not None else _node_row()
        self._error = error

    def _respond(self, name: str, kwargs: dict) -> Any:
        self.calls.append((name, kwargs))
        if self._error is not None:
            raise self._error
        return self._result

    def create(self, **kwargs):
        return self._respond("create", kwargs)

    def get(self, **kwargs):
        return self._respond("get", kwargs)

    def get_latest_attempt(self, **kwargs):
        return self._respond("get_latest_attempt", kwargs)

    def list_for_run(self, workflow_run_id):
        self.calls.append(("list_for_run", {"workflow_run_id": workflow_run_id}))
        if self._error is not None:
            raise self._error
        return [self._result]

    def update(self, **kwargs):
        return self._respond("update", kwargs)


def _approval_row(**overrides: Any) -> _Row:
    base = {
        "approval_id": "wf-ap-1",
        "workflow_run_id": "wf-run-1",
        "node_id": "gate",
        "status": "PENDING",
        "resolved_at": None,
    }
    base.update(overrides)
    return _Row(**base)


class _FakeApprovalRepo:
    def __init__(self, *, result: Any = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._result = result if result is not None else _approval_row()
        self._error = error

    def _respond(self, name: str, kwargs: dict) -> Any:
        self.calls.append((name, kwargs))
        if self._error is not None:
            raise self._error
        return self._result

    def create(self, **kwargs):
        return self._respond("create", kwargs)

    def get(self, approval_id):
        self.calls.append(("get", {"approval_id": approval_id}))
        if self._error is not None:
            raise self._error
        return self._result

    def get_for_node(self, **kwargs):
        return self._respond("get_for_node", kwargs)

    def resolve(self, **kwargs):
        return self._respond("resolve", kwargs)


# ============================================================
# 1) Run Store Adapter
# ============================================================


class TestRunStoreAdapter:
    def test_protocol_structure(self) -> None:
        adapter = WorkflowRunStoreAdapter(_FakeRunRepo())
        assert isinstance(adapter, WorkflowRunStore)

    def test_constructor_rejects_invalid(self) -> None:
        with pytest.raises(TypeError):
            WorkflowRunStoreAdapter(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            WorkflowRunStoreAdapter(object())  # type: ignore[arg-type]

    def test_create_maps_explicit_fields_and_no_orm_leak(self) -> None:
        adapter = WorkflowRunStoreAdapter(_FakeRunRepo())
        record = adapter.create(
            workflow_run_id="wf-run-1",
            workflow_id="flow",
            workflow_version="v1",
            request_id="req-1",
            project_id="proj-a",
            input='{"a": 1}',
        )
        assert isinstance(record, RunRecord)
        assert record.workflow_run_id == "wf-run-1"
        assert record.input == '{"a":1}'
        assert record.project_id == "proj-a"
        # 不透传 ORM 内部对象 / 敏感字段
        assert not hasattr(record, "_sa_instance_state")
        assert not hasattr(record, "connection")

    def test_forwarded_arguments(self) -> None:
        repo = _FakeRunRepo()
        adapter = WorkflowRunStoreAdapter(repo)
        adapter.create(
            workflow_run_id="wf-run-1",
            workflow_id="flow",
            workflow_version="v1",
            request_id="req-1",
            project_id="proj-a",
            input='{"a":1}',
        )
        name, kwargs = repo.calls[0]
        assert name == "create"
        assert kwargs["input"] == '{"a":1}'  # GAP-1 参数已透传
        assert kwargs["project_id"] == "proj-a"

    def test_get_none_passthrough(self) -> None:
        adapter = WorkflowRunStoreAdapter(_FakeRunRepo(result=None))
        adapter._repository._result = None  # type: ignore[attr-defined]
        assert adapter.get("wf-run-1") is None

    def test_update_cas_and_cancel_map(self) -> None:
        adapter = WorkflowRunStoreAdapter(_FakeRunRepo())
        updated = adapter.update_cas(
            workflow_run_id="wf-run-1",
            expected_state_version=0,
            status="RUNNING",
            current_node_id="n1",
            next_action="NODE_EXECUTION",
            attempt_count=1,
        )
        assert isinstance(updated, RunRecord)
        cancelled = adapter.request_cancel("wf-run-1")
        assert isinstance(cancelled, RunRecord)

    def test_not_found_maps(self) -> None:
        adapter = WorkflowRunStoreAdapter(
            _FakeRunRepo(error=WorkflowRunNotFoundError("not found"))
        )
        with pytest.raises(RunNotFoundError) as info:
            adapter.update_cas(
                workflow_run_id="wf-run-9",
                expected_state_version=0,
                status="RUNNING",
                current_node_id=None,
                next_action="NODE_EXECUTION",
                attempt_count=0,
            )
        assert info.value.workflow_run_id == "wf-run-9"
        assert isinstance(info.value.__cause__, WorkflowRunNotFoundError)

    def test_conflict_maps_with_reason(self) -> None:
        adapter = WorkflowRunStoreAdapter(
            _FakeRunRepo(
                error=WorkflowRunConflictError("stale", reason="version")
            )
        )
        with pytest.raises(RunConflictError) as info:
            adapter.update_cas(
                workflow_run_id="wf-run-1",
                expected_state_version=0,
                status="RUNNING",
                current_node_id=None,
                next_action="NODE_EXECUTION",
                attempt_count=0,
            )
        assert info.value.reason == "version"

    def test_conflict_unexpected_reason_fails_closed_to_unknown(self) -> None:
        adapter = WorkflowRunStoreAdapter(
            _FakeRunRepo(
                error=WorkflowRunConflictError("weird", reason="weird")
            )
        )
        with pytest.raises(RunConflictError) as info:
            adapter.request_cancel("wf-run-1")
        assert info.value.reason == "unknown"

    def test_already_exists_maps_to_ports_error(self) -> None:
        adapter = WorkflowRunStoreAdapter(
            _FakeRunRepo(error=WorkflowRunAlreadyExistsError("dup"))
        )
        with pytest.raises(WorkflowPortsError):
            adapter.get("wf-run-1")

    def test_repository_error_and_unknown_map_safely(self) -> None:
        for error in (
            WorkflowRunRepositoryError(f"db down {_SECRET}"),
            RuntimeError(f"boom {_SECRET}"),
        ):
            adapter = WorkflowRunStoreAdapter(_FakeRunRepo(error=error))
            with pytest.raises(WorkflowPortsError) as info:
                adapter.get("wf-run-1")
            assert _SECRET not in str(info.value)  # 不泄漏正文
            assert isinstance(info.value.__cause__, type(error))


# ============================================================
# 2) NodeRun Store Adapter
# ============================================================


class TestNodeRunStoreAdapter:
    def test_protocol_structure(self) -> None:
        adapter = WorkflowNodeRunStoreAdapter(_FakeNodeRepo())
        assert isinstance(adapter, WorkflowNodeRunStore)

    def test_constructor_rejects_invalid(self) -> None:
        with pytest.raises(TypeError):
            WorkflowNodeRunStoreAdapter(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            WorkflowNodeRunStoreAdapter(object())  # type: ignore[arg-type]

    def test_create_maps_and_forwards(self) -> None:
        repo = _FakeNodeRepo()
        adapter = WorkflowNodeRunStoreAdapter(repo)
        record = adapter.create(
            workflow_run_id="wf-run-1",
            node_id="n1",
            attempt=2,
            node_type="AI",
            status="RUNNING",
        )
        assert isinstance(record, NodeRunRecord)
        assert record.attempt == 1  # 来自行对象（映射而非回造）
        assert not hasattr(record, "_sa_instance_state")
        _name, kwargs = repo.calls[0]
        assert kwargs["node_type"] == "AI"

    def test_get_latest_and_list(self) -> None:
        adapter = WorkflowNodeRunStoreAdapter(_FakeNodeRepo())
        latest = adapter.get_latest_attempt(
            workflow_run_id="wf-run-1", node_id="n1"
        )
        assert isinstance(latest, NodeRunRecord)
        rows = adapter.list_for_run("wf-run-1")
        assert isinstance(rows, tuple)
        assert all(isinstance(row, NodeRunRecord) for row in rows)

    def test_none_passthrough(self) -> None:
        repo = _FakeNodeRepo()
        repo._result = None
        adapter = WorkflowNodeRunStoreAdapter(repo)
        assert (
            adapter.get(workflow_run_id="wf-run-1", node_id="n1", attempt=1)
            is None
        )
        assert (
            adapter.get_latest_attempt(
                workflow_run_id="wf-run-1", node_id="n1"
            )
            is None
        )

    def test_already_exists_maps_with_identity(self) -> None:
        adapter = WorkflowNodeRunStoreAdapter(
            _FakeNodeRepo(
                error=WorkflowNodeRunAlreadyExistsError("wf-run-1", "n1", 1)
            )
        )
        with pytest.raises(NodeRunAlreadyExistsError) as info:
            adapter.create(
                workflow_run_id="wf-run-1",
                node_id="n1",
                attempt=1,
                node_type="TOOL",
            )
        assert info.value.workflow_run_id == "wf-run-1"
        assert info.value.node_id == "n1"
        assert info.value.attempt == 1

    def test_not_found_and_conflict_map(self) -> None:
        adapter = WorkflowNodeRunStoreAdapter(
            _FakeNodeRepo(error=WorkflowNodeRunNotFoundError("r", "n", 1))
        )
        with pytest.raises(NodeRunNotFoundError):
            adapter.update(
                workflow_run_id="wf-run-1",
                node_id="n1",
                attempt=1,
                status="SUCCEEDED",
            )
        adapter = WorkflowNodeRunStoreAdapter(
            _FakeNodeRepo(error=WorkflowNodeRunConflictError("terminal"))
        )
        with pytest.raises(NodeRunConflictError):
            adapter.update(
                workflow_run_id="wf-run-1",
                node_id="n1",
                attempt=1,
                status="FAILED",
                error_code="WF_ERROR_NODE_EXECUTION_FAILURE",
            )

    def test_repository_error_and_unknown_map_safely(self) -> None:
        for error in (
            WorkflowNodeRunRepositoryError(f"db {_SECRET}"),
            RuntimeError(f"boom {_SECRET}"),
        ):
            adapter = WorkflowNodeRunStoreAdapter(_FakeNodeRepo(error=error))
            with pytest.raises(WorkflowPortsError) as info:
                adapter.list_for_run("wf-run-1")
            assert _SECRET not in str(info.value)
            assert isinstance(info.value.__cause__, type(error))


# ============================================================
# 3) Approval Store Adapter
# ============================================================


class TestApprovalStoreAdapter:
    def test_protocol_structure(self) -> None:
        adapter = WorkflowApprovalStoreAdapter(_FakeApprovalRepo())
        assert isinstance(adapter, WorkflowApprovalStore)

    def test_constructor_rejects_invalid(self) -> None:
        with pytest.raises(TypeError):
            WorkflowApprovalStoreAdapter(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            WorkflowApprovalStoreAdapter(object())  # type: ignore[arg-type]

    def test_create_and_reads_map(self) -> None:
        adapter = WorkflowApprovalStoreAdapter(_FakeApprovalRepo())
        record = adapter.create(
            approval_id="wf-ap-1",
            workflow_run_id="wf-run-1",
            node_id="gate",
        )
        assert isinstance(record, ApprovalRecord)
        assert record.status == "PENDING"
        assert isinstance(adapter.get("wf-ap-1"), ApprovalRecord)
        assert isinstance(
            adapter.get_for_node(workflow_run_id="wf-run-1", node_id="gate"),
            ApprovalRecord,
        )

    def test_none_passthrough(self) -> None:
        repo = _FakeApprovalRepo()
        repo._result = None
        adapter = WorkflowApprovalStoreAdapter(repo)
        assert adapter.get("wf-ap-x") is None
        assert (
            adapter.get_for_node(workflow_run_id="wf-run-1", node_id="gate")
            is None
        )
        assert adapter.resolve(approval_id="wf-ap-1", decision="APPROVED") is None

    def test_resolve_maps_resolved_record(self) -> None:
        adapter = WorkflowApprovalStoreAdapter(
            _FakeApprovalRepo(
                result=_approval_row(
                    status="APPROVED",
                    resolved_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
                )
            )
        )
        record = adapter.resolve(approval_id="wf-ap-1", decision="APPROVED")
        assert record is not None
        assert record.status == "APPROVED"
        assert record.resolved_at is not None

    def test_repository_error_and_unknown_map_safely(self) -> None:
        for error in (
            WorkflowApprovalRepositoryError(f"db {_SECRET}"),
            RuntimeError(f"boom {_SECRET}"),
        ):
            adapter = WorkflowApprovalStoreAdapter(
                _FakeApprovalRepo(error=error)
            )
            with pytest.raises(WorkflowPortsError) as info:
                adapter.get("wf-ap-1")
            assert _SECRET not in str(info.value)
            assert isinstance(info.value.__cause__, type(error))
