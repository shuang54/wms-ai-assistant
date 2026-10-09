"""Phase 6 Step 3 — S1 ports 层最小测试（全离线；零 DB / 零网络 / 零 LLM）。

覆盖（仅 ports 自身语义；执行 / 并发行为属 S2-S7）：
```text
Test 1  取消三态冻结语义（常量 / CancelOutcome 校验 / 字面量防漂移）
Test 2  RunRecord（含 D1 input 字段）构造与轻校验 / frozen
Test 3  NodeRunRecord / ApprovalRecord 构造与轻校验
Test 4  结果 DTO（OrchestrationOutcome / ToolCallContext / ToolExecutionOutcome）
Test 5  异常语义（继承 / reason 白名单 / AlreadyExists 字段）
Test 6  Store / 执行端口结构（create 含 input / expected_state_version /
        request_cancel / get_latest_attempt / resolve / 异步方法）
Test 7  ports import 边界（AST：仅 stdlib + backend.app.workflow 域）
Test 8  镜像常量契约（NEXT_ACTION_NONE ↔ db 模型；AGENT_STATUS_WAITING_APPROVAL
        ↔ agent.models —— 测试侧 import 属允许域）
```
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path
from typing import Any

import pytest

from backend.app.workflow_runtime.ports import (
    AGENT_STATUS_WAITING_APPROVAL,
    CANCEL_RESULT_CANCELLED,
    CANCEL_RESULT_NOT_CANCELLABLE,
    CANCEL_RESULT_REQUESTED,
    CANCEL_RESULTS,
    NEXT_ACTION_NONE,
    RUN_CONFLICT_REASON_ALREADY_REQUESTED,
    RUN_CONFLICT_REASON_TERMINAL,
    RUN_CONFLICT_REASON_UNKNOWN,
    RUN_CONFLICT_REASON_VERSION,
    RUN_CONFLICT_REASONS,
    ApprovalRecord,
    CancelOutcome,
    NodeRunAlreadyExistsError,
    NodeRunConflictError,
    NodeRunNotFoundError,
    NodeRunRecord,
    OrchestrationOutcome,
    OrchestratorPort,
    RunConflictError,
    RunNotFoundError,
    RunRecord,
    ToolCallContext,
    ToolExecutionOutcome,
    ToolExecutionPort,
    WorkflowApprovalStore,
    WorkflowNodeRunStore,
    WorkflowPortsError,
    WorkflowRunStore,
)

__all__ = [
    "TestCancelSemantics",
    "TestRunRecord",
    "TestChildRecords",
    "TestOutcomeDtos",
    "TestExceptions",
    "TestProtocolShape",
    "TestPortsImportBoundary",
    "TestMirrorConstants",
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PORTS_PATH = _REPO_ROOT / "backend" / "app" / "workflow_runtime" / "ports.py"


def _minimal_run_record(**overrides: Any) -> RunRecord:
    fields: dict[str, Any] = {
        "workflow_run_id": "run-1",
        "workflow_id": "wf_demo",
        "workflow_version": "v1",
        "request_id": "req-1",
        "project_id": "project-a",
        "status": "PENDING",
        "state_version": 0,
        "current_node_id": None,
        "next_action": NEXT_ACTION_NONE,
        "attempt_count": 0,
        "cancel_requested": False,
        "cancel_requested_at": None,
        "input": None,
    }
    fields.update(overrides)
    return RunRecord(**fields)


# ============================================================
# Test 1：取消三态（D3 冻结）
# ============================================================


class TestCancelSemantics:
    def test_three_state_literals_are_frozen(self) -> None:
        assert CANCEL_RESULT_CANCELLED == "CANCELLED"
        assert CANCEL_RESULT_REQUESTED == "CANCEL_REQUESTED"
        assert CANCEL_RESULT_NOT_CANCELLABLE == "NOT_CANCELLABLE"
        assert CANCEL_RESULTS == {
            "CANCELLED",
            "CANCEL_REQUESTED",
            "NOT_CANCELLABLE",
        }

    def test_cancel_outcome_accepts_each_state(self) -> None:
        for result in sorted(CANCEL_RESULTS):
            outcome = CancelOutcome(workflow_run_id="run-1", result=result)
            assert outcome.result == result
            assert outcome.run is None

    def test_cancel_outcome_rejects_unknown_state(self) -> None:
        with pytest.raises(WorkflowPortsError):
            CancelOutcome(workflow_run_id="run-1", result="CANCELED")

    def test_cancel_outcome_carries_run(self) -> None:
        outcome = CancelOutcome(
            workflow_run_id="run-1",
            result=CANCEL_RESULT_CANCELLED,
            run=_minimal_run_record(status="CANCELLED"),
        )
        assert outcome.run is not None
        assert outcome.run.status == "CANCELLED"

    def test_cancel_outcome_is_frozen(self) -> None:
        outcome = CancelOutcome(
            workflow_run_id="run-1", result=CANCEL_RESULT_REQUESTED
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            outcome.result = CANCEL_RESULT_CANCELLED  # type: ignore[misc]


# ============================================================
# Test 2：RunRecord（D1 input 字段）
# ============================================================


class TestRunRecord:
    def test_minimal_construction(self) -> None:
        record = _minimal_run_record()
        assert record.input is None
        assert record.state_version == 0

    def test_input_carries_json_text(self) -> None:
        record = _minimal_run_record(input='{"threshold":5}')
        assert record.input == '{"threshold":5}'

    def test_blank_fields_rejected(self) -> None:
        for field in (
            "workflow_run_id",
            "workflow_id",
            "workflow_version",
            "request_id",
            "project_id",
            "status",
            "next_action",
        ):
            with pytest.raises(WorkflowPortsError):
                _minimal_run_record(**{field: "  "})

    def test_bool_state_version_rejected(self) -> None:
        with pytest.raises(WorkflowPortsError):
            _minimal_run_record(state_version=True)

    def test_negative_attempt_count_rejected(self) -> None:
        with pytest.raises(WorkflowPortsError):
            _minimal_run_record(attempt_count=-1)

    def test_non_bool_cancel_requested_rejected(self) -> None:
        with pytest.raises(WorkflowPortsError):
            _minimal_run_record(cancel_requested=1)

    def test_frozen(self) -> None:
        record = _minimal_run_record()
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.status = "RUNNING"  # type: ignore[misc]


# ============================================================
# Test 3：NodeRunRecord / ApprovalRecord
# ============================================================


class TestChildRecords:
    def test_node_run_record_construction(self) -> None:
        record = NodeRunRecord(
            workflow_run_id="run-1",
            node_id="check_inventory",
            attempt=1,
            node_type="TOOL",
            status="RUNNING",
            output=None,
            error_code=None,
            elapsed_ms=None,
        )
        assert record.attempt == 1

    def test_node_run_attempt_must_be_positive(self) -> None:
        for bad in (0, -1, True):
            with pytest.raises(WorkflowPortsError):
                NodeRunRecord(
                    workflow_run_id="run-1",
                    node_id="n1",
                    attempt=bad,  # type: ignore[arg-type]
                    node_type="AI",
                    status="RUNNING",
                    output=None,
                    error_code=None,
                    elapsed_ms=None,
                )

    def test_node_run_elapsed_ms_rejects_nan_and_negative(self) -> None:
        for bad in (float("nan"), float("inf"), -1.0):
            with pytest.raises(WorkflowPortsError):
                NodeRunRecord(
                    workflow_run_id="run-1",
                    node_id="n1",
                    attempt=1,
                    node_type="AI",
                    status="RUNNING",
                    output=None,
                    error_code=None,
                    elapsed_ms=bad,
                )

    def test_approval_record_construction(self) -> None:
        record = ApprovalRecord(
            approval_id="ap-1",
            workflow_run_id="run-1",
            node_id="approve_step",
            status="PENDING",
        )
        assert record.resolved_at is None


# ============================================================
# Test 4：结果 DTO
# ============================================================


class TestOutcomeDtos:
    def test_orchestration_outcome_agent_fields(self) -> None:
        outcome = OrchestrationOutcome(
            route="agent",
            content=None,
            agent_run_id="agent-run-1",
            agent_status=AGENT_STATUS_WAITING_APPROVAL,
            agent_approval_id="agent-ap-1",
        )
        assert outcome.agent_status == "WAITING_APPROVAL"

    def test_orchestration_outcome_requires_route(self) -> None:
        with pytest.raises(WorkflowPortsError):
            OrchestrationOutcome(route="")

    def test_tool_call_context_round_must_be_positive(self) -> None:
        assert ToolCallContext(request_id="req-1").round == 1
        with pytest.raises(WorkflowPortsError):
            ToolCallContext(request_id="req-1", round=0)

    def test_tool_execution_outcome(self) -> None:
        outcome = ToolExecutionOutcome(
            success=False, error="tool not registered"
        )
        assert outcome.success is False
        with pytest.raises(WorkflowPortsError):
            ToolExecutionOutcome(success="yes")  # type: ignore[arg-type]


# ============================================================
# Test 5：异常语义
# ============================================================


class TestExceptions:
    def test_inheritance_chain(self) -> None:
        for exc in (
            RunNotFoundError,
            RunConflictError,
            NodeRunAlreadyExistsError,
            NodeRunNotFoundError,
            NodeRunConflictError,
        ):
            assert issubclass(exc, WorkflowPortsError)

    def test_run_conflict_reason_whitelist(self) -> None:
        assert RUN_CONFLICT_REASONS == {
            "version",
            "terminal",
            "already_requested",
            "unknown",
        }
        error = RunConflictError("boom", reason=RUN_CONFLICT_REASON_VERSION)
        assert error.reason == RUN_CONFLICT_REASON_VERSION
        with pytest.raises(ValueError):
            RunConflictError("boom", reason="bogus")

    def test_run_conflict_reason_constants_values(self) -> None:
        assert RUN_CONFLICT_REASON_VERSION == "version"
        assert RUN_CONFLICT_REASON_TERMINAL == "terminal"
        assert RUN_CONFLICT_REASON_ALREADY_REQUESTED == "already_requested"
        assert RUN_CONFLICT_REASON_UNKNOWN == "unknown"

    def test_node_run_already_exists_carries_identity(self) -> None:
        error = NodeRunAlreadyExistsError("run-1", "n1", 2)
        assert (error.workflow_run_id, error.node_id, error.attempt) == (
            "run-1",
            "n1",
            2,
        )

    def test_node_run_conflict_reason_is_terminal(self) -> None:
        assert NodeRunConflictError("boom").reason == "terminal"

    def test_node_run_not_found_carries_identity(self) -> None:
        error = NodeRunNotFoundError("run-1", "n1", 1)
        assert error.attempt == 1


# ============================================================
# Test 6：端口结构（鸭子适配可行性）
# ============================================================


class _FakeRunStore:
    def create(
        self,
        *,
        workflow_run_id: str,
        workflow_id: str,
        workflow_version: str,
        request_id: str,
        project_id: str,
        input: str | None,
        status: str = "PENDING",
        current_node_id: str | None = None,
        next_action: str = "NONE",
    ) -> RunRecord: ...

    def get(self, workflow_run_id: str) -> RunRecord | None: ...

    def update_cas(
        self,
        *,
        workflow_run_id: str,
        expected_state_version: int,
        status: str,
        current_node_id: str | None,
        next_action: str,
        attempt_count: int,
    ) -> RunRecord: ...

    def request_cancel(self, workflow_run_id: str) -> RunRecord: ...


class _IncompleteStore:
    def get(self, workflow_run_id: str) -> RunRecord | None: ...


class TestProtocolShape:
    def test_run_store_create_includes_input(self) -> None:
        signature = inspect.signature(WorkflowRunStore.create)
        assert "input" in signature.parameters

    def test_run_store_update_cas_requires_expected_version(self) -> None:
        signature = inspect.signature(WorkflowRunStore.update_cas)
        assert "expected_state_version" in signature.parameters

    def test_run_store_has_request_cancel(self) -> None:
        assert hasattr(WorkflowRunStore, "request_cancel")

    def test_node_store_recovery_surface(self) -> None:
        for name in (
            "create",
            "get",
            "get_latest_attempt",
            "list_for_run",
            "update",
        ):
            assert hasattr(WorkflowNodeRunStore, name)

    def test_approval_store_surface(self) -> None:
        for name in ("create", "get", "get_for_node", "resolve"):
            assert hasattr(WorkflowApprovalStore, name)

    def test_orchestrator_port_methods_are_async(self) -> None:
        for name in ("execute", "resume_agent_run", "cancel_agent_run"):
            method = getattr(OrchestratorPort, name)
            assert inspect.iscoroutinefunction(method)

    def test_tool_execution_port_execute_is_async(self) -> None:
        assert inspect.iscoroutinefunction(ToolExecutionPort.execute)

    def test_runtime_checkable_duck_typing(self) -> None:
        assert isinstance(_FakeRunStore(), WorkflowRunStore)
        assert not isinstance(_IncompleteStore(), WorkflowRunStore)


# ============================================================
# Test 7：ports import 边界（D-A）
# ============================================================


class TestPortsImportBoundary:
    _STDLIB_ALLOWED = frozenset(
        {"__future__", "dataclasses", "datetime", "math", "typing"}
    )

    def test_ports_imports_are_stdlib_or_domain_only(self) -> None:
        tree = ast.parse(_PORTS_PATH.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)

        assert modules, "ports.py import 收集为空（环境异常）"
        for module in modules:
            if module.split(".")[0] in self._STDLIB_ALLOWED:
                continue
            # 仅允许域包（注意：backend.app.workflow_runtime 自身不算——前缀带点区分）
            assert (
                module == "backend.app.workflow"
                or module.startswith("backend.app.workflow.")
            ), f"ports.py 出现边界外 import: {module!r}"

    def test_ports_does_not_import_forbidden_packages(self) -> None:
        tree = ast.parse(_PORTS_PATH.read_text(encoding="utf-8"))
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
            "sqlalchemy",
            "fastapi",
            "httpx",
        ):
            assert not any(
                m == forbidden or m.startswith(forbidden + ".") for m in modules
            ), f"ports.py 禁入 import: {forbidden!r}"


# ============================================================
# Test 8：镜像常量契约（防漂移；测试侧 import 属允许域）
# ============================================================


class TestMirrorConstants:
    def test_next_action_none_matches_db_model(self) -> None:
        from backend.app.db.models.workflow_run import (
            WORKFLOW_NEXT_ACTION_NONE,
        )

        assert NEXT_ACTION_NONE == WORKFLOW_NEXT_ACTION_NONE

    def test_agent_status_waiting_approval_matches_agent_model(self) -> None:
        from backend.app.agent.models import (
            AGENT_STATUS_WAITING_APPROVAL as AGENT_VALUE,
        )

        assert AGENT_STATUS_WAITING_APPROVAL == AGENT_VALUE
