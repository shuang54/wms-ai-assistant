"""Phase 6 Step 6 — S6 Read Layer 单元测试（**全离线**；Fake Store）。

覆盖（S6 任务书 §八A 全部）：
```text
正常读取 Run / Node Attempt 历史 / Approval · Timeline 稳定排序 ·
Run 不存在 · Project 不匹配（与不存在同构——不泄漏） · 子记录归属不一致 ·
input 不出现在 Read DTO · ORM / Session 不泄漏 · 敏感 metadata 不泄漏 ·
查询不调用 Runtime 执行方法 · 不调用 Tool / LLM（结构 + 写方法零调用） ·
查询失败不写入状态（Fake 写方法计数 = 0） · 损坏输出 fail closed ·
空结果与多条记录 · 不支持的能力明确缺失（无全局列表） · AST 边界
```
"""
from __future__ import annotations

import ast
import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.workflow.errors import WF_ERROR_NODE_EXECUTION_FAILURE
from backend.app.workflow_runtime.ports import (
    ApprovalRecord,
    NodeRunRecord,
    RunRecord,
    WorkflowPortsError,
)
from backend.app.workflow_runtime.read_service import (
    TIMELINE_EVENT_KINDS,
    RunDetail,
    TimelineEvent,
    WorkflowReadError,
    WorkflowReadNotFoundError,
    WorkflowReadService,
)

__all__ = [
    "TestRunDetail",
    "TestNodeRuns",
    "TestApprovalDetail",
    "TestTimeline",
    "TestBoundaries",
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_READ_PATH = (
    _REPO_ROOT / "backend" / "app" / "workflow_runtime" / "read_service.py"
)

_T0 = datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)
_SECRET = "postgresql://user:SUPER-SECRET@host/db"

_RUN_ID = "wf-run-1"
_PROJECT = "proj-a"


# ============================================================
# 记录构造 + Fake Store（读实现；写方法追踪——必须零调用）
# ============================================================


def _run_record(**overrides) -> RunRecord:
    base = {
        "workflow_run_id": _RUN_ID,
        "workflow_id": "flow",
        "workflow_version": "v1",
        "request_id": "req-1",
        "project_id": _PROJECT,
        "status": "RUNNING",
        "state_version": 1,
        "current_node_id": "n1",
        "next_action": "NODE_EXECUTION",
        "attempt_count": 1,
        "cancel_requested": False,
        "cancel_requested_at": None,
        "input": '{"secret_input": "DO-NOT-EXPOSE"}',
        "created_at": _T0,
        "started_at": _T0 + timedelta(seconds=1),
        "completed_at": None,
        "updated_at": _T0 + timedelta(seconds=5),
    }
    base.update(overrides)
    return RunRecord(**base)


def _node_record(**overrides) -> NodeRunRecord:
    base = {
        "workflow_run_id": _RUN_ID,
        "node_id": "n1",
        "attempt": 1,
        "node_type": "TOOL",
        "status": "RUNNING",
        "output": None,
        "error_code": None,
        "elapsed_ms": None,
        "started_at": _T0 + timedelta(seconds=1),
        "completed_at": None,
    }
    base.update(overrides)
    return NodeRunRecord(**base)


def _approval_record(**overrides) -> ApprovalRecord:
    base = {
        "approval_id": "wf-ap-1",
        "workflow_run_id": _RUN_ID,
        "node_id": "gate",
        "status": "PENDING",
        "resolved_at": None,
    }
    base.update(overrides)
    return ApprovalRecord(**base)


class _FakeRunStore:
    def __init__(self, rows: dict | None = None) -> None:
        self.rows = dict(rows or {})
        self.error: Exception | None = None
        self.get_calls = 0
        self.write_calls = 0

    def get(self, workflow_run_id):
        self.get_calls += 1
        if self.error is not None:
            raise self.error
        return self.rows.get(workflow_run_id)

    def create(self, **kwargs):  # pragma: no cover - 被调即失败
        self.write_calls += 1
        raise AssertionError("Read 层不得写入（create）")

    def update_cas(self, **kwargs):  # pragma: no cover
        self.write_calls += 1
        raise AssertionError("Read 层不得写入（update_cas）")

    def request_cancel(self, workflow_run_id):  # pragma: no cover
        self.write_calls += 1
        raise AssertionError("Read 层不得写入（request_cancel）")


class _FakeNodeStore:
    def __init__(self, rows: list | None = None) -> None:
        self.rows = list(rows or [])
        self.error: Exception | None = None
        self.list_calls = 0
        self.write_calls = 0

    def list_for_run(self, workflow_run_id):
        self.list_calls += 1
        if self.error is not None:
            raise self.error
        return tuple(self.rows)

    def get(self, **kwargs):  # pragma: no cover - Read 层当前不使用
        return None

    def get_latest_attempt(self, **kwargs):  # pragma: no cover
        return None

    def create(self, **kwargs):  # pragma: no cover
        self.write_calls += 1
        raise AssertionError("Read 层不得写入（node create）")

    def update(self, **kwargs):  # pragma: no cover
        self.write_calls += 1
        raise AssertionError("Read 层不得写入（node update）")


class _FakeApprovalStore:
    def __init__(
        self,
        by_id: dict | None = None,
        by_node: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        self.by_id = dict(by_id or {})
        self.by_node = dict(by_node or {})
        self.error = error
        self.get_calls = 0
        self.for_node_calls = 0
        self.write_calls = 0

    def get(self, approval_id):
        self.get_calls += 1
        if self.error is not None:
            raise self.error
        return self.by_id.get(approval_id)

    def get_for_node(self, *, workflow_run_id, node_id):
        self.for_node_calls += 1
        if self.error is not None:
            raise self.error
        return self.by_node.get((workflow_run_id, node_id))

    def create(self, **kwargs):  # pragma: no cover
        self.write_calls += 1
        raise AssertionError("Read 层不得写入（approval create）")

    def resolve(self, **kwargs):  # pragma: no cover
        self.write_calls += 1
        raise AssertionError("Read 层不得写入（approval resolve）")


class _Env:
    def __init__(
        self,
        *,
        run: RunRecord | None = None,
        nodes: list | None = None,
        approvals: dict | None = None,
        by_node: dict | None = None,
    ) -> None:
        self.run_store = _FakeRunStore(
            {run.workflow_run_id: run} if run is not None else {}
        )
        self.node_store = _FakeNodeStore(nodes)
        self.approval_store = _FakeApprovalStore(approvals, by_node)
        self.service = WorkflowReadService(
            run_store=self.run_store,
            node_run_store=self.node_store,
            approval_store=self.approval_store,
        )

    def assert_no_writes(self) -> None:
        assert self.run_store.write_calls == 0
        assert self.node_store.write_calls == 0
        assert self.approval_store.write_calls == 0


# ============================================================
# Test 1：Run Detail
# ============================================================


class TestRunDetail:
    def test_normal_read_projects_expected_fields(self) -> None:
        run = _run_record()
        env = _Env(run=run)
        detail = env.service.get_run_detail(
            workflow_run_id=_RUN_ID, project_id=_PROJECT
        )

        assert isinstance(detail, RunDetail)
        assert detail.workflow_run_id == _RUN_ID
        assert detail.workflow_id == "flow"
        assert detail.workflow_version == "v1"
        assert detail.project_id == _PROJECT
        assert detail.status == "RUNNING"
        assert detail.current_node_id == "n1"
        assert detail.next_action == "NODE_EXECUTION"
        assert detail.state_version == 1
        assert detail.attempt_count == 1
        assert detail.cancel_requested is False
        assert detail.created_at == _T0
        assert detail.started_at == run.started_at
        assert detail.completed_at is None
        assert detail.updated_at == run.updated_at
        env.assert_no_writes()

    def test_input_and_request_id_never_exposed(self) -> None:
        env = _Env(run=_run_record())
        detail = env.service.get_run_detail(
            workflow_run_id=_RUN_ID, project_id=_PROJECT
        )
        assert not hasattr(detail, "input")
        assert not hasattr(detail, "request_id")
        # 启动输入中的敏感内容不出现在任何字段值里
        assert "DO-NOT-EXPOSE" not in repr(detail)

    def test_no_orm_or_session_or_metadata_leak(self) -> None:
        env = _Env(run=_run_record())
        detail = env.service.get_run_detail(
            workflow_run_id=_RUN_ID, project_id=_PROJECT
        )
        for attribute in (
            "_sa_instance_state",
            "session",
            "connection",
            "metadata",
            "__dict__",
        ):
            assert not hasattr(detail, attribute) or attribute == "__dict__"
        assert isinstance(detail, RunDetail)  # 显式 DTO，而非 ORM 行

    def test_run_not_found(self) -> None:
        env = _Env()
        with pytest.raises(WorkflowReadNotFoundError):
            env.service.get_run_detail(
                workflow_run_id="wf-missing", project_id=_PROJECT
            )
        env.assert_no_writes()

    def test_project_mismatch_is_isomorphic_with_not_found(self) -> None:
        """项目不匹配与不存在：同类型 + 同消息模式（不泄漏存在性）。"""
        env = _Env(run=_run_record())
        with pytest.raises(WorkflowReadNotFoundError) as mismatch:
            env.service.get_run_detail(
                workflow_run_id=_RUN_ID, project_id="proj-B"
            )
        with pytest.raises(WorkflowReadNotFoundError) as missing:
            env.service.get_run_detail(
                workflow_run_id="wf-missing", project_id="proj-B"
            )
        # 消息模式一致（仅身份不同）——无法据消息区分"存在但属于他项目"
        assert "不存在或不在请求的项目范围内" in str(mismatch.value)
        assert "不存在或不在请求的项目范围内" in str(missing.value)
        env.assert_no_writes()

    def test_invalid_arguments_fail_closed(self) -> None:
        env = _Env(run=_run_record())
        with pytest.raises(WorkflowReadError):
            env.service.get_run_detail(workflow_run_id="  ", project_id=_PROJECT)
        with pytest.raises(WorkflowReadError):
            env.service.get_run_detail(workflow_run_id=_RUN_ID, project_id=" ")

    def test_store_failure_wrapped_without_body_leak(self) -> None:
        env = _Env(run=_run_record())
        env.run_store.error = WorkflowPortsError(f"db down {_SECRET}")
        with pytest.raises(WorkflowReadError) as info:
            env.service.get_run_detail(
                workflow_run_id=_RUN_ID, project_id=_PROJECT
            )
        assert isinstance(info.value.__cause__, WorkflowPortsError)
        assert _SECRET not in str(info.value)  # 不泄漏异常正文

    def test_corrupted_status_fails_closed(self) -> None:
        env = _Env(run=_run_record(status="NOT_A_STATUS"))
        with pytest.raises(WorkflowReadError):
            env.service.get_run_detail(
                workflow_run_id=_RUN_ID, project_id=_PROJECT
            )


# ============================================================
# Test 2：Node Runs
# ============================================================


class TestNodeRuns:
    def test_full_attempt_history_in_write_order(self) -> None:
        first = _node_record(
            attempt=1,
            status="FAILED",
            error_code=WF_ERROR_NODE_EXECUTION_FAILURE,
            completed_at=_T0 + timedelta(seconds=3),
        )
        second = _node_record(
            attempt=2,
            status="SUCCEEDED",
            output='{"qty":3}',
            completed_at=_T0 + timedelta(seconds=5),
        )
        later_node = _node_record(
            node_id="n2",
            attempt=1,
            node_type="AI",
            status="SUCCEEDED",
            output='{"route":"rag","content":"ok"}',
            started_at=_T0 + timedelta(seconds=6),
            completed_at=_T0 + timedelta(seconds=7),
        )
        env = _Env(run=_run_record(), nodes=[first, second, later_node])
        records = env.service.get_node_runs(
            workflow_run_id=_RUN_ID, project_id=_PROJECT
        )

        # 历史 attempt 全部保留；写入序 = 执行序（Store 契约）
        assert [(r.node_id, r.attempt) for r in records] == [
            ("n1", 1),
            ("n1", 2),
            ("n2", 1),
        ]
        assert records[0].status == "FAILED"
        assert records[0].error_code == WF_ERROR_NODE_EXECUTION_FAILURE
        assert records[1].output == '{"qty":3}'  # 已持久化输出原样（不解析/不修复）
        for record in records:
            assert isinstance(record, NodeRunRecord)
            assert not hasattr(record, "_sa_instance_state")
        env.assert_no_writes()

    def test_empty_result_is_legal(self) -> None:
        env = _Env(run=_run_record(), nodes=[])
        assert (
            env.service.get_node_runs(
                workflow_run_id=_RUN_ID, project_id=_PROJECT
            )
            == ()
        )
        env.assert_no_writes()

    def test_cross_run_record_fails_closed(self) -> None:
        """子记录归属不一致（Store 返回他 Run 的行）⇒ fail closed。"""
        foreign = _node_record(workflow_run_id="wf-OTHER")
        env = _Env(run=_run_record(), nodes=[foreign])
        with pytest.raises(WorkflowReadError):
            env.service.get_node_runs(
                workflow_run_id=_RUN_ID, project_id=_PROJECT
            )
        env.assert_no_writes()

    def test_project_mismatch_not_found(self) -> None:
        env = _Env(run=_run_record(), nodes=[_node_record()])
        with pytest.raises(WorkflowReadNotFoundError):
            env.service.get_node_runs(
                workflow_run_id=_RUN_ID, project_id="proj-B"
            )

    def test_corrupted_store_payload_fails_closed(self) -> None:
        """Store 层契约损坏（DTO 构造拒绝）⇒ Read 层不返回假数据。"""
        env = _Env(run=_run_record(), nodes=[_node_record()])
        env.node_store.error = WorkflowPortsError("output 损坏（Fake 注入）")
        with pytest.raises(WorkflowReadError) as info:
            env.service.get_node_runs(
                workflow_run_id=_RUN_ID, project_id=_PROJECT
            )
        assert isinstance(info.value.__cause__, WorkflowPortsError)
        env.assert_no_writes()


# ============================================================
# Test 3：Approval Detail
# ============================================================


class TestApprovalDetail:
    def test_normal_read(self) -> None:
        approval = _approval_record(
            status="APPROVED",
            resolved_at=_T0 + timedelta(seconds=20),
        )
        env = _Env(
            run=_run_record(), approvals={"wf-ap-1": approval}
        )
        record = env.service.get_approval_detail(
            approval_id="wf-ap-1", project_id=_PROJECT
        )
        assert isinstance(record, ApprovalRecord)
        assert record.approval_id == "wf-ap-1"
        assert record.workflow_run_id == _RUN_ID
        assert record.node_id == "gate"
        assert record.status == "APPROVED"
        assert record.resolved_at == approval.resolved_at
        env.assert_no_writes()

    def test_approval_not_found(self) -> None:
        env = _Env(run=_run_record())
        with pytest.raises(WorkflowReadNotFoundError):
            env.service.get_approval_detail(
                approval_id="wf-ap-missing", project_id=_PROJECT
            )

    def test_parent_run_missing_fails_closed(self) -> None:
        approval = _approval_record(workflow_run_id="wf-ghost")
        env = _Env(run=_run_record(), approvals={"wf-ap-1": approval})
        with pytest.raises(WorkflowReadNotFoundError):
            env.service.get_approval_detail(
                approval_id="wf-ap-1", project_id=_PROJECT
            )

    def test_parent_run_project_mismatch_not_found(self) -> None:
        approval = _approval_record()
        env = _Env(run=_run_record(), approvals={"wf-ap-1": approval})
        with pytest.raises(WorkflowReadNotFoundError):
            env.service.get_approval_detail(
                approval_id="wf-ap-1", project_id="proj-B"
            )
        env.assert_no_writes()


# ============================================================
# Test 4：Timeline
# ============================================================


class TestTimeline:
    def _full_env(self) -> _Env:
        attempts = [
            _node_record(
                node_id="n1",
                attempt=1,
                node_type="TOOL",
                status="SUCCEEDED",
                output='{"qty":3}',
                started_at=_T0 + timedelta(seconds=1),
                completed_at=_T0 + timedelta(seconds=2),
            ),
            _node_record(
                node_id="gate",
                attempt=1,
                node_type="APPROVAL",
                status="SUCCEEDED",
                started_at=_T0 + timedelta(seconds=3),
                completed_at=_T0 + timedelta(seconds=8),
            ),
        ]
        approvals = {
            ("wf-run-1", "gate"): _approval_record(
                status="APPROVED", resolved_at=_T0 + timedelta(seconds=7)
            )
        }
        run = _run_record(
            completed_at=_T0 + timedelta(seconds=10),
            status="COMPLETED",
        )
        return _Env(run=run, nodes=attempts, by_node=approvals)

    def test_timeline_from_persisted_timestamps_sorted(self) -> None:
        env = self._full_env()
        events = env.service.get_run_timeline(
            workflow_run_id=_RUN_ID, project_id=_PROJECT
        )
        assert [e.kind for e in events] == [
            "RUN_CREATED",
            "RUN_STARTED",
            "NODE_ATTEMPT_STARTED",  # n1 @ +1s
            "NODE_ATTEMPT_FINISHED",  # n1 @ +2s
            "NODE_ATTEMPT_STARTED",  # gate @ +3s
            "APPROVAL_RESOLVED",  # gate 审批 @ +7s
            "NODE_ATTEMPT_FINISHED",  # gate @ +8s
            "RUN_FINISHED",  # +10s
        ]
        stamps = [e.timestamp for e in events]
        assert stamps == sorted(stamps)
        assert all(isinstance(e, TimelineEvent) for e in events)
        assert all(e.kind in TIMELINE_EVENT_KINDS for e in events)
        env.assert_no_writes()

    def test_timeline_zero_inference_skips_none_timestamps(self) -> None:
        """时间字段为 None ⇒ 不产生事件（不伪造）。"""
        env = _Env(
            run=_run_record(
                started_at=None, completed_at=None, cancel_requested_at=None
            ),
            nodes=[
                _node_record(started_at=None, completed_at=None, status="RUNNING")
            ],
        )
        events = env.service.get_run_timeline(
            workflow_run_id=_RUN_ID, project_id=_PROJECT
        )
        assert [e.kind for e in events] == ["RUN_CREATED"]  # 仅真实时间

    def test_pending_approval_has_no_resolved_event(self) -> None:
        env = _Env(
            run=_run_record(),
            nodes=[
                _node_record(
                    node_id="gate",
                    node_type="APPROVAL",
                    status="WAITING_APPROVAL",
                    started_at=_T0 + timedelta(seconds=1),
                    completed_at=None,
                )
            ],
            by_node={("wf-run-1", "gate"): _approval_record()},  # PENDING
        )
        events = env.service.get_run_timeline(
            workflow_run_id=_RUN_ID, project_id=_PROJECT
        )
        assert "APPROVAL_RESOLVED" not in [e.kind for e in events]

    def test_stable_secondary_order_for_equal_timestamps(self) -> None:
        """同一时间戳 ⇒ 稳定次级排序（kind rank → 身份键）；输入顺序无关。"""
        same = _T0 + timedelta(seconds=5)
        attempts = [
            _node_record(
                node_id="b", attempt=1, started_at=same, completed_at=same
            ),
            _node_record(
                node_id="a", attempt=1, started_at=same, completed_at=same
            ),
        ]
        reversed_attempts = list(reversed(attempts))

        first = _Env(
            run=_run_record(
                created_at=same, started_at=None, completed_at=None
            ),
            nodes=attempts,
        ).service.get_run_timeline(workflow_run_id=_RUN_ID, project_id=_PROJECT)
        second = _Env(
            run=_run_record(
                created_at=same, started_at=None, completed_at=None
            ),
            nodes=reversed_attempts,
        ).service.get_run_timeline(workflow_run_id=_RUN_ID, project_id=_PROJECT)

        keys = [
            (e.kind, e.node_id or "", e.attempt) for e in first
        ]
        assert keys == [(e.kind, e.node_id or "", e.attempt) for e in second]
        # kind rank（created < started < finished）+ 身份键（node_id 升序）：
        # 同一时刻的确定序列（与 attempts 输入顺序无关）
        assert keys == [
            ("RUN_CREATED", "", None),
            ("NODE_ATTEMPT_STARTED", "a", 1),
            ("NODE_ATTEMPT_STARTED", "b", 1),
            ("NODE_ATTEMPT_FINISHED", "a", 1),
            ("NODE_ATTEMPT_FINISHED", "b", 1),
        ]

    def test_mixed_naive_aware_timestamps_fail_closed(self) -> None:
        naive = datetime(2026, 10, 9, 10, 0)
        env = _Env(
            run=_run_record(created_at=_T0),
            nodes=[
                _node_record(
                    started_at=naive,  # naive —— 数据损坏
                    completed_at=naive,
                )
            ],
        )
        with pytest.raises(WorkflowReadError):
            env.service.get_run_timeline(
                workflow_run_id=_RUN_ID, project_id=_PROJECT
            )

    def test_timeline_project_mismatch(self) -> None:
        env = self._full_env()
        with pytest.raises(WorkflowReadNotFoundError):
            env.service.get_run_timeline(
                workflow_run_id=_RUN_ID, project_id="proj-B"
            )

    def test_event_dto_validation_and_frozen(self) -> None:
        with pytest.raises(WorkflowReadError):
            TimelineEvent(kind="WEIRD", timestamp=_T0, workflow_run_id=_RUN_ID)
        with pytest.raises(WorkflowReadError):
            TimelineEvent(
                kind="RUN_CREATED", timestamp=None, workflow_run_id=_RUN_ID  # type: ignore[arg-type]
            )
        event = TimelineEvent(
            kind="RUN_CREATED", timestamp=_T0, workflow_run_id=_RUN_ID
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.kind = "RUN_STARTED"  # type: ignore[misc]


# ============================================================
# Test 5：边界（能力缺失 / 无执行入口 / AST）
# ============================================================


class TestBoundaries:
    def test_no_execution_or_global_listing_capability(self) -> None:
        env = _Env(run=_run_record())
        service = env.service
        for forbidden in (
            "start",
            "advance",
            "request_cancel",
            "submit_approval_decision",
            "list_runs",
            "list_all_runs",
            "execute",
        ):
            assert not hasattr(service, forbidden), (
                f"Read Service 不得提供 {forbidden}（能力缺失必须显式，"
                "不得静默返回假数据）"
            )

    def test_all_queries_leave_write_methods_untouched(self) -> None:
        env = _Env(run=_run_record(), nodes=[_node_record()])
        env.service.get_run_detail(
            workflow_run_id=_RUN_ID, project_id=_PROJECT
        )
        env.service.get_node_runs(
            workflow_run_id=_RUN_ID, project_id=_PROJECT
        )
        try:
            env.service.get_run_timeline(
                workflow_run_id=_RUN_ID, project_id=_PROJECT
            )
        except WorkflowReadError:  # pragma: no cover - 本组数据不触发
            pass
        env.assert_no_writes()

    def test_import_boundary(self) -> None:
        tree = ast.parse(_READ_PATH.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        assert modules, "read_service.py import 收集为空（环境异常）"
        stdlib = {"__future__", "dataclasses", "datetime", "typing"}
        for module in modules:
            if module.split(".")[0] in stdlib:
                continue
            if module == "backend.app.workflow" or module.startswith(
                "backend.app.workflow."
            ):
                continue
            assert module == "backend.app.workflow_runtime.ports", (
                f"read_service.py 出现边界外 import: {module!r}"
            )

    def test_forbidden_imports_absent(self) -> None:
        tree = ast.parse(_READ_PATH.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        for forbidden in (
            "backend.app.db",
            "backend.app.services",
            "backend.app.tools",
            "backend.app.agent",
            "backend.app.api",
            "backend.app.llm",
            "backend.app.projects",
            "backend.app.workflow_runtime.runtime",
            "backend.app.workflow_runtime.execution",
            "backend.app.workflow_runtime.binding",
            "backend.app.workflow_runtime.factory",
            "backend.app.workflow_runtime.adapters",
            "backend.app.workflow_runtime.store_adapters",
            "sqlalchemy",
            "fastapi",
            "httpx",
        ):
            assert not any(
                m == forbidden or m.startswith(forbidden + ".")
                for m in modules
            ), f"read_service.py 禁入 import: {forbidden!r}"
