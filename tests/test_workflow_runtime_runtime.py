"""Phase 6 Step 4 — S4 runtime 测试（**全离线 Fake Port + Fake NodeExecutor**）。

零真实 DB / 零真实 LLM / 零真实 Tool（Fake 端口 + Fake Executor + Fake Orchestrator）。

```text
覆盖（用户 S4 任务书 §9）：
    Run 启动成功与输入校验失败 · 正常 DAG 顺序执行 · CONDITION true/false ·
    成功/失败/终态 · Approval 等待/批准/拒绝/重复裁决 · AI 等待与恢复 ·
    合法重试与禁止重试 · 取消及并发取消 · CAS 冲突与重新读取分类 ·
    UNKNOWN Attempt 不重跑 · 并发 Resume 不重复调用外部能力 ·
    外部执行成功但持久化失败 · project_id 传播 · 终态不可覆盖 ·
    不访问真实 DB/LLM/Tool · import boundary

并发测试：**Barrier / Once-Gate 控制竞态**（无随机 sleep；两个真实线程各自
asyncio.run ⇒ 端口经 asyncio.to_thread 落在真实 worker 线程，闸门同步双方
首读快照）。
```

**重要（Fake ≠ DB）**：本文件证明的是**协议正确性**（失败竞争者零外部调用 /
相同节点不重复执行）；单赢语义的**原子性**（唯一约束 / CAS 的 WHERE 语义）
最终由 PostgreSQL 保证 ⇒ 必须叠加 DB-gated 集成测试（Step 2 已覆盖
Repository 层的 CAS/唯一约束/FK 行为；S4 的 runtime×真实 Repository 组合
验证标注为**待办**——见 S4 报告「未验证的 DB 行为」）。
"""
from __future__ import annotations

import ast
import asyncio
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.workflow.definition import (
    NODE_TYPE_AI,
    NODE_TYPE_APPROVAL,
    NODE_TYPE_CONDITION,
    NODE_TYPE_TOOL,
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRegistry,
)
from backend.app.workflow.errors import (
    WF_ERROR_APPROVAL_CONFLICT,
    WF_ERROR_APPROVAL_NOT_FOUND,
    WF_ERROR_APPROVAL_REJECTED,
    WF_ERROR_DEFINITION_VERSION_MISMATCH,
    WF_ERROR_LIMIT_REACHED,
    WF_ERROR_NODE_EXECUTION_FAILURE,
    WF_ERROR_NODE_UNKNOWN,
    WF_ERROR_OUTPUT_UNAVAILABLE,
    WF_ERROR_PERSISTENCE_FAILURE,
    WF_ERROR_RUN_NOT_FOUND,
    WF_ERROR_STATE_CONFLICT,
)
from backend.app.workflow.policy import (
    NodeRetryPolicy,
    WorkflowRuntimePolicy,
)
from backend.app.workflow.states import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
    NODE_STATUS_FAILED,
    NODE_STATUS_RUNNING,
    NODE_STATUS_SUCCEEDED,
    NODE_STATUS_WAITING_APPROVAL,
    WORKFLOW_STATUS_CANCELLED,
    WORKFLOW_STATUS_COMPLETED,
    WORKFLOW_STATUS_FAILED,
    WORKFLOW_STATUS_PENDING,
    WORKFLOW_STATUS_RUNNING,
    WORKFLOW_STATUS_WAITING,
    WORKFLOW_TERMINAL_STATUSES,
)
from backend.app.workflow_runtime.execution import NodeExecutionOutcome
from backend.app.workflow_runtime.ports import (
    AGENT_STATUS_WAITING_APPROVAL,
    NEXT_ACTION_NONE,
    ApprovalRecord,
    NodeRunAlreadyExistsError,
    NodeRunConflictError,
    NodeRunNotFoundError,
    NodeRunRecord,
    OrchestrationOutcome,
    RunConflictError,
    RunNotFoundError,
    RunRecord,
    WorkflowPortsError,
)
from backend.app.workflow_runtime import runtime as runtime_module
from backend.app.workflow_runtime.runtime import (
    OUTCOME_CANCEL_REQUESTED,
    OUTCOME_CANCELLED,
    OUTCOME_COMPLETED,
    OUTCOME_CONFLICT,
    OUTCOME_DEFINITION_MISMATCH,
    OUTCOME_FAILED,
    OUTCOME_LIMIT_REACHED,
    OUTCOME_NOT_CANCELLABLE,
    OUTCOME_NOT_FOUND,
    OUTCOME_RUNNING,
    OUTCOME_UNKNOWN,
    OUTCOME_WAITING_APPROVAL,
    RUN_OUTCOMES,
    RunOutcome,
    RuntimeContractError,
    WorkflowRuntime,
)

__all__ = [
    "TestStart",
    "TestDagAndCondition",
    "TestTerminalAndBudget",
    "TestApprovalFlow",
    "TestAgentWaitAndResume",
    "TestRetryAndUnknown",
    "TestPersistenceEdges",
    "TestCancellation",
    "TestConcurrency",
    "TestBoundaries",
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_PATH = (
    _REPO_ROOT / "backend" / "app" / "workflow_runtime" / "runtime.py"
)

_INPUT_JSON = json.dumps({"q": "库存是否充足", "amount": 42}, ensure_ascii=False)
_PROJECT_ID = "proj-a"
_RUN_ID = "wf-run-1"


# ============================================================
# 通用工具
# ============================================================


class _FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return self._now


class _OnceGate:
    """一次性竞态闸门：前 ``parties`` 个到达者互等（Barrier）；之后直接通过。

    （确定性并发测试用——不依赖随机 sleep。）
    """

    def __init__(self, parties: int = 2) -> None:
        self._barrier = threading.Barrier(parties)
        self._parties = parties
        self._arrived = 0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            if self._arrived >= self._parties:
                return
            self._arrived += 1
        self._barrier.wait(timeout=30)


def _run_parallel(callables: list) -> list:
    """在两个（或更多）真实线程中分别 asyncio.run 调用（收集结果）。"""
    results: list = [None] * len(callables)
    errors: list = []
    barrier = threading.Barrier(len(callables))  # 确保同时起跑

    def worker(index: int, fn) -> None:
        try:
            barrier.wait(timeout=30)
            results[index] = fn()
        except Exception as exc:  # pragma: no cover - 失败时上报
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(i, fn), daemon=True)
        for i, fn in enumerate(callables)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, f"并发调用出现异常: {errors!r}"
    return results


# ============================================================
# Fake 端口
# ============================================================


def _run_record(row: dict) -> RunRecord:
    return RunRecord(**row)


def _node_record(row: dict) -> NodeRunRecord:
    return NodeRunRecord(**row)


class _FakeRunStore:
    """内存 run 表（线程安全；CAS / request_cancel 语义与真实 Repository 对齐）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.rows: dict[str, dict] = {}
        self.get_gate: _OnceGate | None = None
        self.get_threads: list[str] = []
        self.cas_error_once: Exception | None = None
        self.cas_calls = 0
        self.cancel_calls = 0

    # ---- 测试辅助 ----

    def seed(self, **overrides) -> dict:
        row = {
            "workflow_run_id": _RUN_ID,
            "workflow_id": "linear_flow",
            "workflow_version": "v1",
            "request_id": "req-1",
            "project_id": _PROJECT_ID,
            "status": WORKFLOW_STATUS_RUNNING,
            "state_version": 1,
            "current_node_id": "collect",
            "next_action": "NODE_EXECUTION",
            "attempt_count": 0,
            "cancel_requested": False,
            "cancel_requested_at": None,
            "input": None,
            "created_at": datetime(2026, 10, 9, tzinfo=timezone.utc),
            "started_at": datetime(2026, 10, 9, tzinfo=timezone.utc),
            "completed_at": None,
            "updated_at": datetime(2026, 10, 9, tzinfo=timezone.utc),
        }
        row.update(overrides)
        with self._lock:
            self.rows[row["workflow_run_id"]] = row
        return row

    def force_complete(self, workflow_run_id: str) -> None:
        """模拟“他人”把 run 终态化（CAS 竞争场景）。"""
        with self._lock:
            row = self.rows[workflow_run_id]
            row["status"] = WORKFLOW_STATUS_COMPLETED
            row["next_action"] = "COMPLETED"
            row["state_version"] += 1

    def set_cancel(self, workflow_run_id: str) -> None:
        """直接置位取消信号（模拟“信号已持久化”状态）。"""
        with self._lock:
            row = self.rows[workflow_run_id]
            row["cancel_requested"] = True
            row["state_version"] += 1

    # ---- 端口实现 ----

    def create(
        self,
        *,
        workflow_run_id: str,
        workflow_id: str,
        workflow_version: str,
        request_id: str,
        project_id: str,
        input: str | None,
        status: str = WORKFLOW_STATUS_PENDING,
        current_node_id: str | None = None,
        next_action: str = NEXT_ACTION_NONE,
    ) -> RunRecord:
        with self._lock:
            if workflow_run_id in self.rows:
                raise WorkflowPortsError(
                    f"Workflow Run 已存在: {workflow_run_id!r}"
                )
            row = {
                "workflow_run_id": workflow_run_id,
                "workflow_id": workflow_id,
                "workflow_version": workflow_version,
                "request_id": request_id,
                "project_id": project_id,
                "status": status,
                "state_version": 0,
                "current_node_id": current_node_id,
                "next_action": next_action,
                "attempt_count": 0,
                "cancel_requested": False,
                "cancel_requested_at": None,
                "input": input,
                "created_at": datetime(2026, 10, 9, tzinfo=timezone.utc),
                "started_at": None,
                "completed_at": None,
                "updated_at": datetime(2026, 10, 9, tzinfo=timezone.utc),
            }
            self.rows[workflow_run_id] = row
            return _run_record(row)

    def get(self, workflow_run_id: str) -> RunRecord | None:
        self.get_threads.append(threading.current_thread().name)
        if self.get_gate is not None:
            self.get_gate.wait()
        with self._lock:
            row = self.rows.get(workflow_run_id)
            return _run_record(dict(row)) if row is not None else None

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
        if self.cas_error_once is not None:
            error, self.cas_error_once = self.cas_error_once, None
            raise error
        self.cas_calls += 1
        with self._lock:
            row = self.rows.get(workflow_run_id)
            if row is None:
                raise RunNotFoundError(workflow_run_id)
            if row["status"] in WORKFLOW_TERMINAL_STATUSES:
                raise RunConflictError("terminal", reason="terminal")
            if row["state_version"] != expected_state_version:
                raise RunConflictError("version", reason="version")
            row["status"] = status
            row["current_node_id"] = current_node_id
            row["next_action"] = next_action
            row["attempt_count"] = attempt_count
            row["state_version"] += 1
            if status == WORKFLOW_STATUS_RUNNING and row["started_at"] is None:
                row["started_at"] = datetime(2026, 10, 9, tzinfo=timezone.utc)
            if status in WORKFLOW_TERMINAL_STATUSES:
                row["completed_at"] = datetime(2026, 10, 9, tzinfo=timezone.utc)
            return _run_record(dict(row))

    def request_cancel(self, workflow_run_id: str) -> RunRecord:
        self.cancel_calls += 1
        with self._lock:
            row = self.rows.get(workflow_run_id)
            if row is None:
                raise RunNotFoundError(workflow_run_id)
            if row["status"] in WORKFLOW_TERMINAL_STATUSES:
                raise RunConflictError("terminal", reason="terminal")
            if row["cancel_requested"]:
                raise RunConflictError(
                    "already_requested", reason="already_requested"
                )
            row["cancel_requested"] = True
            row["cancel_requested_at"] = datetime(
                2026, 10, 9, tzinfo=timezone.utc
            )
            row["state_version"] += 1  # 与真实 Repository 对齐（互斥语义）
            return _run_record(dict(row))


class _FakeNodeRunStore:
    """内存 node_run 表（唯一约束 / 终态不可逆语义与真实 Repository 对齐）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.rows: dict[tuple[str, str, int], dict] = {}
        self.create_calls = 0
        self.update_calls = 0
        self.fail_update = False

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
    ) -> NodeRunRecord:
        self.create_calls += 1
        key = (workflow_run_id, node_id, attempt)
        with self._lock:
            if key in self.rows:
                raise NodeRunAlreadyExistsError(workflow_run_id, node_id, attempt)
            row = {
                "workflow_run_id": workflow_run_id,
                "node_id": node_id,
                "attempt": attempt,
                "node_type": node_type,
                "status": status,
                "output": output,
                "error_code": error_code,
                "elapsed_ms": elapsed_ms,
                "started_at": started_at,
                "completed_at": completed_at,
            }
            self.rows[key] = row
            return _node_record(dict(row))

    def get(
        self, *, workflow_run_id: str, node_id: str, attempt: int
    ) -> NodeRunRecord | None:
        with self._lock:
            row = self.rows.get((workflow_run_id, node_id, attempt))
            return _node_record(dict(row)) if row is not None else None

    def get_latest_attempt(
        self, *, workflow_run_id: str, node_id: str
    ) -> NodeRunRecord | None:
        with self._lock:
            # dict 迭代 = 插入序（写入序）⇒ 最后一个匹配即最新
            matches = [
                row
                for (rid, nid, _attempt), row in self.rows.items()
                if rid == workflow_run_id and nid == node_id
            ]
            return _node_record(dict(matches[-1])) if matches else None

    def list_for_run(
        self, workflow_run_id: str
    ) -> tuple[NodeRunRecord, ...]:
        with self._lock:
            matches = [
                row
                for (rid, _nid, _attempt), row in self.rows.items()
                if rid == workflow_run_id
            ]
            return tuple(_node_record(dict(row)) for row in matches)

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
    ) -> NodeRunRecord:
        self.update_calls += 1
        if self.fail_update:
            raise WorkflowPortsError("node_run update failed（Fake 注入）")
        key = (workflow_run_id, node_id, attempt)
        with self._lock:
            row = self.rows.get(key)
            if row is None:
                raise NodeRunNotFoundError(workflow_run_id, node_id, attempt)
            if row["status"] in (NODE_STATUS_SUCCEEDED, NODE_STATUS_FAILED):
                raise NodeRunConflictError(
                    "attempt 已终态（终态不可逆）"
                )
            row["status"] = status
            row["output"] = output if output is not None else row["output"]
            row["error_code"] = (
                error_code if error_code is not None else row["error_code"]
            )
            row["elapsed_ms"] = (
                elapsed_ms if elapsed_ms is not None else row["elapsed_ms"]
            )
            row["completed_at"] = (
                completed_at if completed_at is not None else row["completed_at"]
            )
            return _node_record(dict(row))


class _FakeApprovalStore:
    """内存审批表（幂等创建 / 条件 UPDATE 单赢）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.rows: dict[str, dict] = {}
        self._by_node: dict[tuple[str, str], str] = {}
        self.resolve_calls = 0
        self.fail_create = False

    def create(
        self, *, approval_id: str, workflow_run_id: str, node_id: str
    ) -> ApprovalRecord:
        if self.fail_create:
            raise WorkflowPortsError("approval create failed（Fake 注入）")
        with self._lock:
            existing_id = self._by_node.get((workflow_run_id, node_id))
            if existing_id is not None:
                return ApprovalRecord(**self.rows[existing_id])
            row = {
                "approval_id": approval_id,
                "workflow_run_id": workflow_run_id,
                "node_id": node_id,
                "status": APPROVAL_STATUS_PENDING,
                "resolved_at": None,
            }
            self.rows[approval_id] = row
            self._by_node[(workflow_run_id, node_id)] = approval_id
            return ApprovalRecord(**row)

    def get(self, approval_id: str) -> ApprovalRecord | None:
        with self._lock:
            row = self.rows.get(approval_id)
            return ApprovalRecord(**row) if row is not None else None

    def get_for_node(
        self, *, workflow_run_id: str, node_id: str
    ) -> ApprovalRecord | None:
        with self._lock:
            approval_id = self._by_node.get((workflow_run_id, node_id))
            if approval_id is None:
                return None
            return ApprovalRecord(**self.rows[approval_id])

    def resolve(
        self, *, approval_id: str, decision: str
    ) -> ApprovalRecord | None:
        self.resolve_calls += 1
        with self._lock:
            row = self.rows.get(approval_id)
            if row is None or row["status"] != APPROVAL_STATUS_PENDING:
                return None
            row["status"] = decision
            row["resolved_at"] = datetime(2026, 10, 9, tzinfo=timezone.utc)
            return ApprovalRecord(**row)


class _FakeExecutor:
    """Fake NodeExecutor（鸭子类型）：可编程结果队列 + 调用记录 + 审批节点模拟。"""

    def __init__(
        self, approval_store: _FakeApprovalStore | None = None
    ) -> None:
        self.calls: list[dict] = []
        self.results: list = []
        self.on_call = None
        self._approval_store = approval_store

    def push(self, result) -> "_FakeExecutor":
        self.results.append(result)
        return self

    async def execute_node(
        self,
        node,
        *,
        workflow_run_id,
        project_id,
        request_id,
        input,
        previous,
        attempt,
    ) -> NodeExecutionOutcome:
        index = len(self.calls)
        self.calls.append(
            {
                "node_id": node.node_id,
                "node_type": node.node_type,
                "workflow_run_id": workflow_run_id,
                "project_id": project_id,
                "request_id": request_id,
                "input": input,
                "previous": previous,
                "attempt": attempt,
            }
        )
        if self.on_call is not None:
            self.on_call(index, node)

        if index < len(self.results):
            result = self.results[index]
            if isinstance(result, Exception):
                raise result
            return result

        if node.node_type == NODE_TYPE_APPROVAL and self._approval_store is not None:
            record = self._approval_store.create(
                approval_id=f"wf-ap-{node.node_id}",
                workflow_run_id=workflow_run_id,
                node_id=node.node_id,
            )
            return NodeExecutionOutcome(
                status=NODE_STATUS_WAITING_APPROVAL,
                output=_json({"approval_id": record.approval_id}),
                approval_id=record.approval_id,
            )

        return NodeExecutionOutcome(
            status=NODE_STATUS_SUCCEEDED,
            output=_json({"node": node.node_id, "attempt": attempt}),
        )


class _FakeOrchestrator:
    """Fake OrchestratorPort（仅 resume / cancel 被 runtime 使用）。"""

    def __init__(self, resume_results: list | None = None) -> None:
        self.resume_results = list(resume_results or [])
        self.resume_calls: list[dict] = []
        self.cancel_calls: list[str] = []

    async def execute(self, question, *, context=None):  # pragma: no cover
        raise AssertionError("runtime 不得直接调用 execute（AI 首执行走 NodeExecutor）")

    async def resume_agent_run(
        self, run_id, *, question, context=None
    ) -> OrchestrationOutcome:
        self.resume_calls.append({"run_id": run_id, "question": question})
        if self.resume_results:
            result = self.resume_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return OrchestrationOutcome(route="agent", content="done")

    async def cancel_agent_run(self, run_id: str) -> None:
        self.cancel_calls.append(run_id)


# ============================================================
# 定义与执行环境
# ============================================================


def _json(payload) -> str:
    from backend.app.workflow.models import serialize_json_value

    return serialize_json_value(payload)


def _ok(payload) -> NodeExecutionOutcome:
    return NodeExecutionOutcome(
        status=NODE_STATUS_SUCCEEDED, output=_json(payload)
    )


def _failed(code: str = WF_ERROR_NODE_EXECUTION_FAILURE) -> NodeExecutionOutcome:
    return NodeExecutionOutcome(status=NODE_STATUS_FAILED, error_code=code)


def _waiting_agent(
    run_id: str = "agent-run-9",
    approval_id: str | None = "agent-ap-9",
) -> NodeExecutionOutcome:
    return NodeExecutionOutcome(
        status=NODE_STATUS_WAITING_APPROVAL,
        output=_json(
            {"agent_run_id": run_id, "agent_approval_id": approval_id}
        ),
        agent_run_id=run_id,
        agent_approval_id=approval_id,
    )


def _linear_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="linear_flow",
        version="v1",
        start_node_id="collect",
        nodes=(
            WorkflowNode(
                node_id="collect",
                node_type=NODE_TYPE_TOOL,
                tool_name="get_inventory",
                next_node_id="summarize",
            ),
            WorkflowNode(
                node_id="summarize",
                node_type=NODE_TYPE_AI,
                question_template="{input.q}",
                next_node_id="report",
            ),
            WorkflowNode(
                node_id="report",
                node_type=NODE_TYPE_TOOL,
                tool_name="get_work_order",
                next_node_id=None,
            ),
        ),
    )


def _branch_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="branch_flow",
        version="v1",
        start_node_id="check",
        nodes=(
            WorkflowNode(
                node_id="check",
                node_type=NODE_TYPE_CONDITION,
                condition=WorkflowCondition(
                    source="amount", operator="GT", expected=10
                ),
                on_true_node_id="hot_path",
                on_false_node_id="cold_path",
            ),
            WorkflowNode(
                node_id="hot_path",
                node_type=NODE_TYPE_TOOL,
                tool_name="get_inventory",
                next_node_id=None,
            ),
            WorkflowNode(
                node_id="cold_path",
                node_type=NODE_TYPE_TOOL,
                tool_name="get_work_order",
                next_node_id=None,
            ),
        ),
    )


def _approval_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="approval_flow",
        version="v1",
        start_node_id="draft",
        nodes=(
            WorkflowNode(
                node_id="draft",
                node_type=NODE_TYPE_AI,
                question_template="{input.q}",
                next_node_id="gate",
            ),
            WorkflowNode(
                node_id="gate",
                node_type=NODE_TYPE_APPROVAL,
                approval_prompt="请审批发布",
                next_node_id="publish",
            ),
            WorkflowNode(
                node_id="publish",
                node_type=NODE_TYPE_TOOL,
                tool_name="get_inventory",
                next_node_id=None,
            ),
        ),
    )


def _agent_definition(with_downstream: bool = True) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="agent_flow",
        version="v1",
        start_node_id="consult",
        nodes=(
            WorkflowNode(
                node_id="consult",
                node_type=NODE_TYPE_AI,
                question_template="{input.q}",
                next_node_id="finalize" if with_downstream else None,
            ),
            WorkflowNode(
                node_id="finalize",
                node_type=NODE_TYPE_TOOL,
                tool_name="get_work_order",
                next_node_id=None,
            ),
        )
        if with_downstream
        else (
            WorkflowNode(
                node_id="consult",
                node_type=NODE_TYPE_AI,
                question_template="{input.q}",
                next_node_id=None,
            ),
        ),
    )


def _retry_definition(max_attempts: int = 2) -> WorkflowDefinition:
    return WorkflowDefinition(
        name="retry_flow",
        version="v1",
        start_node_id="flaky",
        nodes=(
            WorkflowNode(
                node_id="flaky",
                node_type=NODE_TYPE_AI,
                question_template="{input.q}",
                retry_policy=NodeRetryPolicy(max_attempts=max_attempts),
                next_node_id=None,
            ),
        ),
    )


class _Env:
    """最小执行环境（Fake 端口 + 真实 WorkflowRuntime）。"""

    def __init__(
        self,
        definition: WorkflowDefinition,
        *,
        policy: WorkflowRuntimePolicy | None = None,
        executor: _FakeExecutor | None = None,
        orchestrator: _FakeOrchestrator | None = None,
        run_id: str = _RUN_ID,
    ) -> None:
        self.run_store = _FakeRunStore()
        self.node_store = _FakeNodeRunStore()
        self.approval_store = _FakeApprovalStore()
        self.registry = WorkflowRegistry()
        self.registry.register(definition)
        self.definition = definition
        self.executor = executor or _FakeExecutor(self.approval_store)
        self.executor._approval_store = self.approval_store
        self.orchestrator = orchestrator or _FakeOrchestrator()
        self.clock = _FakeClock()
        self.runtime = WorkflowRuntime(
            run_store=self.run_store,
            node_run_store=self.node_store,
            approval_store=self.approval_store,
            registry=self.registry,
            executor=self.executor,
            policy=policy
            or WorkflowRuntimePolicy(
                max_nodes_executed=16, max_total_attempts=16
            ),
            clock=self.clock,
            orchestrator_provider=lambda project_id: self.orchestrator,
            request_id_factory=lambda: "req-1",
            run_id_factory=lambda: run_id,
        )

    # ---- 同步包装（asyncio.run）----

    def start(self, **overrides) -> RunOutcome:
        kwargs = {
            "workflow_id": self.definition.name,
            "project_id": _PROJECT_ID,
            "input": _INPUT_JSON,
            "workflow_run_id": _RUN_ID,
        }
        kwargs.update(overrides)
        return asyncio.run(self.runtime.start(**kwargs))

    def advance(self, run_id: str = _RUN_ID) -> RunOutcome:
        return asyncio.run(self.runtime.advance(run_id))

    def decide(self, approval_id: str, decision: str) -> RunOutcome:
        return asyncio.run(
            self.runtime.submit_approval_decision(
                approval_id=approval_id, decision=decision
            )
        )

    def cancel(self, run_id: str = _RUN_ID) -> RunOutcome:
        return asyncio.run(self.runtime.request_cancel(run_id))

    # ---- 断言辅助 ----

    def node_row(
        self, node_id: str, attempt: int = 1
    ) -> dict | None:
        return self.node_store.rows.get((_RUN_ID, node_id, attempt))

    def executed_node_ids(self) -> list[str]:
        return [call["node_id"] for call in self.executor.calls]


# ============================================================
# Test 1：启动
# ============================================================


class TestStart:
    def test_start_completes_linear_run_in_order(self) -> None:
        env = _Env(_linear_definition())
        outcome = env.start()

        assert outcome.outcome == OUTCOME_COMPLETED
        assert outcome.run is not None
        assert outcome.run.status == WORKFLOW_STATUS_COMPLETED
        assert outcome.run.next_action == "COMPLETED"
        assert env.executed_node_ids() == ["collect", "summarize", "report"]
        assert len(env.node_store.rows) == 3

        # previous 传递：第 2 / 3 次调用拿到上一个成功节点的 output
        first_output = env.node_row("collect")["output"]
        second_output = env.node_row("summarize")["output"]
        assert env.executor.calls[1]["previous"] == first_output
        assert env.executor.calls[2]["previous"] == second_output
        assert env.executor.calls[0]["previous"] is None

        # attempt 编号（runtime 决定 + 记录）
        assert [call["attempt"] for call in env.executor.calls] == [1, 1, 1]

    def test_start_persists_input_and_identity(self) -> None:
        env = _Env(_linear_definition())
        env.start()

        row = env.run_store.rows[_RUN_ID]
        assert row["workflow_id"] == "linear_flow"
        assert row["workflow_version"] == "v1"
        assert row["request_id"] == "req-1"
        assert row["project_id"] == _PROJECT_ID
        assert json.loads(row["input"]) == {
            "q": "库存是否充足",
            "amount": 42,
        }

    def test_start_invalid_input_raises_and_creates_nothing(self) -> None:
        env = _Env(_linear_definition())
        with pytest.raises(RuntimeContractError):
            env.start(input="{bad json")
        assert env.run_store.rows == {}
        assert env.executor.calls == []

    def test_start_input_over_size_boundary_rejected(self) -> None:
        env = _Env(_linear_definition())
        with pytest.raises(RuntimeContractError):
            env.start(input=json.dumps({"x": "a" * 40_000}))
        assert env.run_store.rows == {}

    def test_start_input_none_is_allowed(self) -> None:
        env = _Env(_linear_definition())
        outcome = env.start(input=None)
        assert outcome.outcome == OUTCOME_COMPLETED
        assert env.run_store.rows[_RUN_ID]["input"] is None

    def test_start_unknown_workflow_raises(self) -> None:
        env = _Env(_linear_definition())
        with pytest.raises(Exception) as info:
            env.start(workflow_id="no_such_flow")
        assert "no_such_flow" in str(info.value)
        assert env.run_store.rows == {}

    def test_start_duplicate_run_id_returns_conflict(self) -> None:
        env = _Env(_linear_definition())
        first = env.start()
        assert first.outcome == OUTCOME_COMPLETED

        second = env.start()  # 同一 run_id 再 start
        assert second.outcome == OUTCOME_CONFLICT
        assert second.error_code == WF_ERROR_STATE_CONFLICT
        assert second.run is not None
        assert second.run.status == WORKFLOW_STATUS_COMPLETED
        assert len(env.executor.calls) == 3  # 不重复执行


# ============================================================
# Test 2：DAG 与 CONDITION
# ============================================================


class TestDagAndCondition:
    def test_condition_true_branch(self) -> None:
        env = _Env(_branch_definition())
        env.executor.push(_ok(True))
        outcome = env.start()

        assert outcome.outcome == OUTCOME_COMPLETED
        assert env.executed_node_ids() == ["check", "hot_path"]

    def test_condition_false_branch(self) -> None:
        env = _Env(_branch_definition())
        env.executor.push(_ok(False))
        outcome = env.start()

        assert outcome.outcome == OUTCOME_COMPLETED
        assert env.executed_node_ids() == ["check", "cold_path"]

    def test_condition_invalid_output_fails_run(self) -> None:
        env = _Env(_branch_definition())
        env.executor.push(_ok("maybe"))
        outcome = env.start()

        assert outcome.outcome == OUTCOME_FAILED
        assert outcome.error_code == WF_ERROR_NODE_EXECUTION_FAILURE
        assert env.executed_node_ids() == ["check"]  # 不静默跳过 / 不猜分支
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_FAILED

    def test_condition_non_boolean_value_fails_run(self) -> None:
        env = _Env(_branch_definition())
        env.executor.push(_ok(1))  # 非 bool（JSON int）
        outcome = env.start()

        assert outcome.outcome == OUTCOME_FAILED
        assert outcome.error_code == WF_ERROR_NODE_EXECUTION_FAILURE


# ============================================================
# Test 3：失败 / 终态 / 预算
# ============================================================


class TestTerminalAndBudget:
    def test_executor_failure_fails_run_and_keeps_code(self) -> None:
        env = _Env(_linear_definition())
        env.executor.push(_failed())
        outcome = env.start()

        assert outcome.outcome == OUTCOME_FAILED
        assert outcome.error_code == WF_ERROR_NODE_EXECUTION_FAILURE
        assert env.executed_node_ids() == ["collect"]  # 下游未执行
        assert env.node_row("collect")["status"] == NODE_STATUS_FAILED
        assert (
            env.node_row("collect")["error_code"]
            == WF_ERROR_NODE_EXECUTION_FAILURE
        )

    def test_completed_run_is_not_advanced_again(self) -> None:
        env = _Env(_linear_definition())
        env.start()
        calls_before = len(env.executor.calls)

        outcome = env.advance()
        assert outcome.outcome == OUTCOME_COMPLETED
        assert len(env.executor.calls) == calls_before  # 终态不可覆盖
        # 4 次 CAS：启动 + 3 个节点各推进一次（终态后不再新增）
        assert env.run_store.rows[_RUN_ID]["state_version"] == 4

    def test_failed_run_advance_returns_failed_with_attempt_code(self) -> None:
        env = _Env(_linear_definition())
        env.executor.push(_failed(WF_ERROR_OUTPUT_UNAVAILABLE))
        env.start()

        outcome = env.advance()
        assert outcome.outcome == OUTCOME_FAILED
        assert outcome.error_code == WF_ERROR_OUTPUT_UNAVAILABLE

    def test_nodes_budget_limit_reached(self) -> None:
        env = _Env(
            _linear_definition(),
            policy=WorkflowRuntimePolicy(
                max_nodes_executed=1, max_total_attempts=16
            ),
        )
        outcome = env.start()

        assert outcome.outcome == OUTCOME_FAILED
        assert outcome.error_code == WF_ERROR_LIMIT_REACHED
        assert env.executed_node_ids() == ["collect"]  # 第二节点前拦截

    def test_attempts_budget_limit_reached(self) -> None:
        env = _Env(
            _linear_definition(),
            policy=WorkflowRuntimePolicy(
                max_nodes_executed=16, max_total_attempts=1
            ),
        )
        outcome = env.start()

        assert outcome.outcome == OUTCOME_FAILED
        assert outcome.error_code == WF_ERROR_LIMIT_REACHED
        assert env.executed_node_ids() == ["collect"]

    def test_not_found_run(self) -> None:
        env = _Env(_linear_definition())
        outcome = env.advance(run_id="wf-missing")
        assert outcome.outcome == OUTCOME_NOT_FOUND
        assert outcome.error_code == WF_ERROR_RUN_NOT_FOUND


# ============================================================
# Test 4：Approval 等待 / 裁决 / 恢复
# ============================================================


class TestApprovalFlow:
    def test_run_waits_at_approval_gate(self) -> None:
        env = _Env(_approval_definition())
        outcome = env.start()

        assert outcome.outcome == OUTCOME_WAITING_APPROVAL
        assert outcome.approval_id == "wf-ap-gate"
        assert outcome.approval_status == APPROVAL_STATUS_PENDING

        run = env.run_store.rows[_RUN_ID]
        assert run["status"] == WORKFLOW_STATUS_WAITING
        assert run["next_action"] == "WAITING_APPROVAL"
        assert run["current_node_id"] == "gate"
        assert env.node_row("gate")["status"] == NODE_STATUS_WAITING_APPROVAL
        assert env.executed_node_ids() == ["draft", "gate"]  # publish 未执行

    def test_pending_approval_advance_is_idempotent_wait(self) -> None:
        env = _Env(_approval_definition())
        env.start()
        calls_before = len(env.executor.calls)

        outcome = env.advance()
        assert outcome.outcome == OUTCOME_WAITING_APPROVAL
        assert outcome.approval_id == "wf-ap-gate"
        assert outcome.approval_status == APPROVAL_STATUS_PENDING
        assert len(env.executor.calls) == calls_before  # 仍等待；不推进

    def test_approved_then_advance_completes_run(self) -> None:
        env = _Env(_approval_definition())
        env.start()

        decided = env.decide("wf-ap-gate", APPROVAL_STATUS_APPROVED)
        assert decided.outcome == OUTCOME_WAITING_APPROVAL
        assert decided.approval_status == APPROVAL_STATUS_APPROVED

        outcome = env.advance()
        assert outcome.outcome == OUTCOME_COMPLETED
        assert env.executed_node_ids() == ["draft", "gate", "publish"]

        gate_row = env.node_row("gate")
        assert gate_row["status"] == NODE_STATUS_SUCCEEDED
        assert json.loads(gate_row["output"]) == {
            "approval_id": "wf-ap-gate",
            "decision": APPROVAL_STATUS_APPROVED,
        }

    def test_repeated_same_decision_is_idempotent(self) -> None:
        env = _Env(_approval_definition())
        env.start()

        first = env.decide("wf-ap-gate", APPROVAL_STATUS_APPROVED)
        second = env.decide("wf-ap-gate", APPROVAL_STATUS_APPROVED)

        assert first.outcome == OUTCOME_WAITING_APPROVAL
        assert second.outcome == OUTCOME_WAITING_APPROVAL
        assert second.approval_status == APPROVAL_STATUS_APPROVED

    def test_opposite_decision_conflicts(self) -> None:
        env = _Env(_approval_definition())
        env.start()
        env.decide("wf-ap-gate", APPROVAL_STATUS_APPROVED)

        outcome = env.decide("wf-ap-gate", APPROVAL_STATUS_REJECTED)
        assert outcome.outcome == OUTCOME_CONFLICT
        assert outcome.error_code == WF_ERROR_APPROVAL_CONFLICT

    def test_rejected_then_advance_fails_run(self) -> None:
        env = _Env(_approval_definition())
        env.start()
        env.decide("wf-ap-gate", APPROVAL_STATUS_REJECTED)

        outcome = env.advance()
        assert outcome.outcome == OUTCOME_FAILED
        assert outcome.error_code == WF_ERROR_APPROVAL_REJECTED
        assert env.executed_node_ids() == ["draft", "gate"]  # publish 未执行

        gate_row = env.node_row("gate")
        assert gate_row["status"] == NODE_STATUS_FAILED
        assert gate_row["error_code"] == WF_ERROR_APPROVAL_REJECTED
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_FAILED

    def test_unknown_approval_not_found(self) -> None:
        env = _Env(_approval_definition())
        outcome = env.decide("wf-ap-missing", APPROVAL_STATUS_APPROVED)
        assert outcome.outcome == OUTCOME_NOT_FOUND
        assert outcome.error_code == WF_ERROR_APPROVAL_NOT_FOUND

    def test_invalid_decision_raises(self) -> None:
        env = _Env(_approval_definition())
        with pytest.raises(RuntimeContractError):
            env.decide("wf-ap-gate", "MAYBE")

    def test_decision_after_cancel_is_rejected(self) -> None:
        env = _Env(_approval_definition())
        env.start()  # 停在 gate
        env.cancel()
        outcome = env.decide("wf-ap-gate", APPROVAL_STATUS_APPROVED)
        assert outcome.outcome == OUTCOME_CONFLICT
        assert outcome.error_code == WF_ERROR_STATE_CONFLICT
        assert (
            env.approval_store.rows["wf-ap-gate"]["status"]
            == APPROVAL_STATUS_PENDING
        )  # 未被裁决（cancel 使 approval 失效）

    def test_approval_conflict_fails_run_with_stable_code(self) -> None:
        """S3 契约：审批创建冲突 ⇒ 节点 FAILED(APPROVAL_CONFLICT) ⇒ run FAILED。"""
        env = _Env(_approval_definition())
        env.executor.push(_ok({"node": "draft"}))
        env.executor.push(_failed(WF_ERROR_APPROVAL_CONFLICT))
        outcome = env.start()

        assert outcome.outcome == OUTCOME_FAILED
        assert outcome.error_code == WF_ERROR_APPROVAL_CONFLICT
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_FAILED


# ============================================================
# Test 5：AI 等待与恢复
# ============================================================


class TestAgentWaitAndResume:
    def test_agent_wait_keeps_recovery_identity(self) -> None:
        env = _Env(_agent_definition())
        env.executor.push(_waiting_agent())
        outcome = env.start()

        assert outcome.outcome == OUTCOME_WAITING_APPROVAL
        assert outcome.agent_run_id == "agent-run-9"
        assert outcome.agent_approval_id == "agent-ap-9"
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_WAITING
        attempt = env.node_row("consult")
        assert attempt["status"] == NODE_STATUS_WAITING_APPROVAL
        assert json.loads(attempt["output"]) == {
            "agent_run_id": "agent-run-9",
            "agent_approval_id": "agent-ap-9",
        }
        assert env.orchestrator.resume_calls == []  # 不自动恢复

    def test_advance_resumes_agent_and_continues_downstream(self) -> None:
        env = _Env(_agent_definition())
        env.executor.push(_waiting_agent())
        env.start()

        outcome = env.advance()
        assert outcome.outcome == OUTCOME_COMPLETED
        assert env.executed_node_ids() == ["consult", "finalize"]
        assert len(env.orchestrator.resume_calls) == 1
        assert env.orchestrator.resume_calls[0]["run_id"] == "agent-run-9"
        assert env.orchestrator.resume_calls[0]["question"] == "库存是否充足"
        assert env.node_row("consult")["status"] == NODE_STATUS_SUCCEEDED

    def test_advance_resume_still_waiting_updates_keys(self) -> None:
        env = _Env(_agent_definition())
        env.executor.push(_waiting_agent())
        env.start()

        env.orchestrator.resume_results.append(
            OrchestrationOutcome(
                route="agent",
                content=None,
                agent_run_id="agent-run-9",
                agent_status=AGENT_STATUS_WAITING_APPROVAL,
                agent_approval_id="agent-ap-10",
            )
        )
        outcome = env.advance()

        assert outcome.outcome == OUTCOME_WAITING_APPROVAL
        assert outcome.agent_approval_id == "agent-ap-10"
        attempt = env.node_row("consult")
        assert attempt["status"] == NODE_STATUS_WAITING_APPROVAL
        assert json.loads(attempt["output"])["agent_approval_id"] == "agent-ap-10"
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_WAITING
        assert env.executed_node_ids() == ["consult"]  # 不重复执行

    def test_resume_failure_marks_attempt_failed_and_fails_run(self) -> None:
        env = _Env(_agent_definition())
        env.executor.push(_waiting_agent())
        env.start()

        env.orchestrator.resume_results.append(RuntimeError("agent down"))
        outcome = env.advance()

        assert outcome.outcome == OUTCOME_FAILED
        assert outcome.error_code == WF_ERROR_NODE_EXECUTION_FAILURE
        assert env.node_row("consult")["status"] == NODE_STATUS_FAILED
        assert env.executed_node_ids() == ["consult"]  # 不自动重跑恢复

    def test_resume_failure_retries_when_policy_allows(self) -> None:
        definition = WorkflowDefinition(
            name="agent_flow",
            version="v1",
            start_node_id="consult",
            nodes=(
                WorkflowNode(
                    node_id="consult",
                    node_type=NODE_TYPE_AI,
                    question_template="{input.q}",
                    retry_policy=NodeRetryPolicy(max_attempts=2),
                    next_node_id=None,
                ),
            ),
        )
        env = _Env(definition)
        env.executor.push(_waiting_agent())
        env.start()

        env.orchestrator.resume_results.append(RuntimeError("agent down"))
        outcome = env.advance()

        # retry = 新 attempt（executor 全新执行 ⇒ 默认成功）
        assert outcome.outcome == OUTCOME_COMPLETED
        assert [call["attempt"] for call in env.executor.calls] == [1, 2]
        assert env.node_row("consult", 1)["status"] == NODE_STATUS_FAILED
        assert env.node_row("consult", 2)["status"] == NODE_STATUS_SUCCEEDED


# ============================================================
# Test 6：Retry 与 UNKNOWN
# ============================================================


class TestRetryAndUnknown:
    def test_retry_fail_before_retry_discipline(self) -> None:
        env = _Env(_retry_definition(max_attempts=2))
        node_store = env.node_store

        def on_call(index: int, node) -> None:
            if index == 1:  # 第二次（重试）执行前：attempt1 必须已持久化 FAILED
                row = node_store.rows[(_RUN_ID, node.node_id, 1)]
                assert row["status"] == NODE_STATUS_FAILED
                assert row["error_code"] == WF_ERROR_NODE_EXECUTION_FAILURE

        env.executor.on_call = on_call
        env.executor.push(_failed())
        outcome = env.start()

        assert outcome.outcome == OUTCOME_COMPLETED
        assert [call["attempt"] for call in env.executor.calls] == [1, 2]
        assert env.node_row("flaky", 1)["status"] == NODE_STATUS_FAILED
        assert env.node_row("flaky", 2)["status"] == NODE_STATUS_SUCCEEDED

    def test_retry_exhausted_fails_run(self) -> None:
        env = _Env(_retry_definition(max_attempts=2))
        env.executor.push(_failed())
        env.executor.push(_failed())
        outcome = env.start()

        assert outcome.outcome == OUTCOME_FAILED
        assert len(env.executor.calls) == 2  # 恰好 max_attempts 次
        assert env.node_row("flaky", 2)["status"] == NODE_STATUS_FAILED

    def test_tool_without_retry_policy_does_not_retry(self) -> None:
        env = _Env(_linear_definition())
        env.executor.push(_failed())
        env.start()
        assert len(env.executor.calls) == 1  # TOOL 默认不重试

    def test_non_retryable_error_code_does_not_retry(self) -> None:
        env = _Env(_retry_definition(max_attempts=3))
        env.executor.push(_failed(WF_ERROR_OUTPUT_UNAVAILABLE))
        outcome = env.start()

        assert outcome.outcome == OUTCOME_FAILED
        assert outcome.error_code == WF_ERROR_OUTPUT_UNAVAILABLE
        assert len(env.executor.calls) == 1  # 白名单外不重试

    def test_unknown_running_attempt_is_never_rerun(self) -> None:
        env = _Env(_linear_definition())
        env.run_store.seed(current_node_id="collect")
        env.node_store.create(
            workflow_run_id=_RUN_ID,
            node_id="collect",
            attempt=1,
            node_type=NODE_TYPE_TOOL,
            status=NODE_STATUS_RUNNING,  # 未决（在途或崩溃——不可区分）
        )

        outcome = env.advance()
        assert outcome.outcome == OUTCOME_UNKNOWN
        assert outcome.error_code == WF_ERROR_NODE_UNKNOWN
        assert env.executor.calls == []  # 不重跑、不调用外部
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_RUNNING

    def test_succeeded_attempt_persisted_but_run_not_advanced_resumes(self) -> None:
        """崩溃残留：attempt 已 SUCCEEDED、run 未推进 ⇒ 用持久化结果接续（不重执行）。"""
        env = _Env(_linear_definition())
        env.run_store.seed(current_node_id="collect")
        env.node_store.create(
            workflow_run_id=_RUN_ID,
            node_id="collect",
            attempt=1,
            node_type=NODE_TYPE_TOOL,
            status=NODE_STATUS_SUCCEEDED,
            output=_json({"qty": 3}),
            completed_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
        )
        # report 节点此前未执行；advance 将接续 summarize → report
        outcome = env.advance()

        assert outcome.outcome == OUTCOME_COMPLETED
        assert env.executed_node_ids() == ["summarize", "report"]
        assert env.executor.calls[0]["previous"] == _json({"qty": 3})


# ============================================================
# Test 7：持久化边界与 CAS 冲突分类
# ============================================================


class TestPersistenceEdges:
    def test_external_success_but_attempt_write_fails(self) -> None:
        env = _Env(_linear_definition())
        env.node_store.fail_update = True
        outcome = env.start()

        assert outcome.outcome == OUTCOME_UNKNOWN
        assert outcome.error_code == WF_ERROR_PERSISTENCE_FAILURE
        assert len(env.executor.calls) == 1  # 外部成功但写入失败 ⇒ 不重跑
        assert env.node_row("collect")["status"] == NODE_STATUS_RUNNING
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_RUNNING

        # 人工边界：未决 RUNNING attempt ⇒ 后续 advance 亦不重跑
        env.node_store.fail_update = False
        again = env.advance()
        assert again.outcome == OUTCOME_UNKNOWN
        assert again.error_code == WF_ERROR_NODE_UNKNOWN
        assert len(env.executor.calls) == 1

    def test_cas_conflict_reclassifies_and_lets_other_win(self) -> None:
        env = _Env(_linear_definition())

        def on_call(index: int, node) -> None:
            if index == 0:
                # 本次执行后推进 CAS 将遭遇版本竞争（模拟并发推进者）
                env.run_store.cas_error_once = RunConflictError(
                    "version", reason="version"
                )

        env.executor.on_call = on_call
        outcome = env.start()

        assert outcome.outcome == OUTCOME_RUNNING  # 重读分类：让位
        assert len(env.executor.calls) == 1
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_RUNNING
        assert env.node_row("collect")["status"] == NODE_STATUS_SUCCEEDED

        # 接续（不重跑外部）：用已持久化结果继续
        resumed = env.advance()
        assert resumed.outcome == OUTCOME_COMPLETED
        assert env.executed_node_ids() == ["collect", "summarize", "report"]

    def test_cas_conflict_after_other_terminalizes_run(self) -> None:
        env = _Env(_linear_definition())

        def on_call(index: int, node) -> None:
            if index == 0:
                env.run_store.force_complete(_RUN_ID)

        env.executor.on_call = on_call
        outcome = env.start()

        assert outcome.outcome == OUTCOME_COMPLETED
        assert len(env.executor.calls) == 1  # 不重跑外部
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_COMPLETED

    def test_definition_version_mismatch_is_reported(self) -> None:
        env = _Env(_linear_definition())
        env.run_store.seed(workflow_version="v0")  # 持久化版本与注册版本不符

        outcome = env.advance()
        assert outcome.outcome == OUTCOME_DEFINITION_MISMATCH
        assert outcome.error_code == WF_ERROR_DEFINITION_VERSION_MISMATCH
        assert env.executor.calls == []

    def test_project_id_propagates_to_executor_and_rows(self) -> None:
        env = _Env(_linear_definition())
        env.start(project_id="proj-west")
        assert all(
            call["project_id"] == "proj-west" for call in env.executor.calls
        )
        # Fake node store 模拟 F-1：project_id 从 run 继承（runtime 不传）
        assert env.run_store.rows[_RUN_ID]["project_id"] == "proj-west"


# ============================================================
# Test 8：取消
# ============================================================


class TestCancellation:
    def test_cancel_running_run_switches_to_cancelled(self) -> None:
        env = _Env(_linear_definition())
        env.run_store.seed()
        outcome = env.cancel()

        assert outcome.outcome == OUTCOME_CANCELLED
        row = env.run_store.rows[_RUN_ID]
        assert row["status"] == WORKFLOW_STATUS_CANCELLED
        assert row["next_action"] == "CANCELLED"
        assert row["cancel_requested"] is True
        assert env.executor.calls == []

    def test_cancel_completed_run_is_not_cancellable(self) -> None:
        env = _Env(_linear_definition())
        env.start()
        outcome = env.cancel()

        assert outcome.outcome == OUTCOME_NOT_CANCELLABLE
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_COMPLETED

    def test_cancel_after_cancel_is_not_cancellable(self) -> None:
        env = _Env(_linear_definition())
        env.run_store.seed()
        first = env.cancel()
        second = env.cancel()

        assert first.outcome == OUTCOME_CANCELLED
        assert second.outcome == OUTCOME_NOT_CANCELLABLE

    def test_cancel_while_waiting_approval(self) -> None:
        env = _Env(_approval_definition())
        env.start()  # 停在 gate
        outcome = env.cancel()

        assert outcome.outcome == OUTCOME_CANCELLED
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_CANCELLED

    def test_cancel_propagates_to_waiting_agent(self) -> None:
        env = _Env(_agent_definition())
        env.executor.push(_waiting_agent())
        env.start()  # AI 等待中

        outcome = env.cancel()
        assert outcome.outcome == OUTCOME_CANCELLED
        assert env.orchestrator.cancel_calls == ["agent-run-9"]

    def test_cancel_signal_blocks_inline_advance(self) -> None:
        env = _Env(_linear_definition())
        env.run_store.seed(status=WORKFLOW_STATUS_RUNNING)
        env.run_store.set_cancel(_RUN_ID)  # 信号已持久化（未收尾）

        outcome = env.advance()
        assert outcome.outcome == OUTCOME_CANCELLED  # 节点边界检查 ⇒ 收尾
        assert env.executor.calls == []  # 不调度新节点

    def test_cancel_not_found(self) -> None:
        env = _Env(_linear_definition())
        outcome = env.cancel(run_id="wf-missing")
        assert outcome.outcome == OUTCOME_NOT_FOUND
        assert outcome.error_code == WF_ERROR_RUN_NOT_FOUND

    def test_cancel_agent_propagation_failure_does_not_block(self) -> None:
        env = _Env(_agent_definition())
        env.executor.push(_waiting_agent())
        env.start()

        class _BoomOrchestrator(_FakeOrchestrator):
            async def cancel_agent_run(self, run_id):  # type: ignore[override]
                raise RuntimeError("agent cancel down")

        env.runtime._orchestrator_provider = lambda project_id: _BoomOrchestrator()
        outcome = env.cancel()
        assert outcome.outcome == OUTCOME_CANCELLED  # 传播失败不阻断收尾


# ============================================================
# Test 9：并发（Barrier / Once-Gate 确定性竞态）
# ============================================================


class TestConcurrency:
    def test_concurrent_advance_single_execution(self) -> None:
        """两个并发 advance：唯一约束单赢 ⇒ 外部执行恰好一次。"""
        env = _Env(_linear_definition())
        env.run_store.seed(current_node_id="collect")
        env.run_store.get_gate = _OnceGate(2)

        results = _run_parallel(
            [
                lambda: asyncio.run(env.runtime.advance(_RUN_ID)),
                lambda: asyncio.run(env.runtime.advance(_RUN_ID)),
            ]
        )

        statuses = sorted(result.outcome for result in results)
        assert statuses.count(OUTCOME_UNKNOWN) == 1  # 竞争者 fail-closed
        collect_calls = [
            call for call in env.executor.calls if call["node_id"] == "collect"
        ]
        assert len(collect_calls) == 1  # 相同节点不重复执行（单赢）
        assert len(env.node_store.rows) == 3  # 1 + 下游 2（赢者推进完整条链）
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_COMPLETED

    def test_concurrent_advance_executor_never_double_invoked(self) -> None:
        """全部并发结果都不允许出现“同一 attempt 两次外部调用”。"""
        env = _Env(_linear_definition())
        env.run_store.seed(current_node_id="collect")
        env.run_store.get_gate = _OnceGate(2)

        results = _run_parallel(
            [
                lambda: asyncio.run(env.runtime.advance(_RUN_ID)),
                lambda: asyncio.run(env.runtime.advance(_RUN_ID)),
            ]
        )

        attempts = [
            call["attempt"]
            for call in env.executor.calls
            if call["node_id"] == "collect"
        ]
        assert attempts == [1]  # 仅一个执行权声明
        assert all(
            result.outcome in (OUTCOME_UNKNOWN, OUTCOME_RUNNING, OUTCOME_COMPLETED)
            for result in results
        )

    def test_concurrent_waiting_resume_single_winner(self) -> None:
        """WAITING 恢复：WAITING→RUNNING CAS 单赢 ⇒ 恢复推进只发生一次。"""
        env = _Env(_approval_definition())
        env.run_store.seed(
            workflow_id=env.definition.name,
            workflow_version=env.definition.version,
            status=WORKFLOW_STATUS_WAITING,
            current_node_id="gate",
            next_action="WAITING_APPROVAL",
        )
        env.node_store.create(
            workflow_run_id=_RUN_ID,
            node_id="gate",
            attempt=1,
            node_type=NODE_TYPE_APPROVAL,
            status=NODE_STATUS_WAITING_APPROVAL,
            output=_json({"approval_id": "wf-ap-gate"}),
            started_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
        )
        env.approval_store.create(
            approval_id="wf-ap-gate", workflow_run_id=_RUN_ID, node_id="gate"
        )
        env.approval_store.resolve(
            approval_id="wf-ap-gate", decision=APPROVAL_STATUS_APPROVED
        )
        env.run_store.get_gate = _OnceGate(2)

        results = _run_parallel(
            [
                lambda: asyncio.run(env.runtime.advance(_RUN_ID)),
                lambda: asyncio.run(env.runtime.advance(_RUN_ID)),
            ]
        )

        assert len(env.executor.calls) == 1  # publish 只执行一次
        assert env.node_store.rows[(_RUN_ID, "gate", 1)]["status"] == (
            NODE_STATUS_SUCCEEDED
        )
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_COMPLETED
        assert any(
            result.outcome == OUTCOME_COMPLETED for result in results
        )

    def test_concurrent_cancel_single_winner(self) -> None:
        """并发取消：信号置位 + 收尾 CAS 均单赢；三态不二值化。"""
        env = _Env(_linear_definition())
        env.run_store.seed()
        env.run_store.get_gate = _OnceGate(2)

        results = _run_parallel(
            [
                lambda: asyncio.run(env.runtime.request_cancel(_RUN_ID)),
                lambda: asyncio.run(env.runtime.request_cancel(_RUN_ID)),
            ]
        )

        statuses = sorted(result.outcome for result in results)
        assert set(statuses) <= {OUTCOME_CANCELLED, OUTCOME_CANCEL_REQUESTED}
        assert OUTCOME_CANCELLED in statuses
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_CANCELLED
        assert env.run_store.cancel_calls == 2  # 双请求都到达（一success一already）


# ============================================================
# Test 10：DTO / 边界
# ============================================================


class TestBoundaries:
    _STDLIB_ALLOWED = frozenset(
        {
            "__future__",
            "asyncio",
            "json",
            "logging",
            "time",
            "uuid",
            "dataclasses",
            "datetime",
            "typing",
        }
    )
    _SAME_PACKAGE_ALLOWED = frozenset(
        {
            "backend.app.workflow_runtime.ports",
            "backend.app.workflow_runtime.binding",
            "backend.app.workflow_runtime.execution",
        }
    )

    def _modules(self) -> set[str]:
        tree = ast.parse(_RUNTIME_PATH.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    def test_import_boundary(self) -> None:
        modules = self._modules()
        assert modules, "runtime.py import 收集为空（环境异常）"
        for module in modules:
            if module.split(".")[0] in self._STDLIB_ALLOWED:
                continue
            if module == "backend.app.workflow" or module.startswith(
                "backend.app.workflow."
            ):
                continue
            assert module in self._SAME_PACKAGE_ALLOWED, (
                f"runtime.py 出现边界外 import: {module!r}"
            )

    def test_forbidden_imports_absent(self) -> None:
        modules = self._modules()
        for forbidden in (
            "backend.app.db",
            "backend.app.services",
            "backend.app.tools",
            "backend.app.agent",
            "backend.app.api",
            "backend.app.llm",
            "backend.app.projects",
            "backend.app.workflow_runtime.factory",
            "backend.app.workflow_runtime.adapters",
            "sqlalchemy",
            "fastapi",
            "httpx",
        ):
            assert not any(
                m == forbidden or m.startswith(forbidden + ".")
                for m in modules
            ), f"runtime.py 禁入 import: {forbidden!r}"

    def test_next_action_mirrors_db_model(self) -> None:
        from backend.app.db.models.workflow_run import (
            WORKFLOW_NEXT_ACTION_CANCELLED,
            WORKFLOW_NEXT_ACTION_COMPLETED,
            WORKFLOW_NEXT_ACTION_FAILED,
            WORKFLOW_NEXT_ACTION_NODE_EXECUTION,
            WORKFLOW_NEXT_ACTION_NONE,
            WORKFLOW_NEXT_ACTION_WAITING_APPROVAL,
        )

        assert runtime_module.NEXT_ACTION_NONE == WORKFLOW_NEXT_ACTION_NONE
        assert (
            runtime_module._NEXT_ACTION_NODE_EXECUTION
            == WORKFLOW_NEXT_ACTION_NODE_EXECUTION
        )
        assert (
            runtime_module._NEXT_ACTION_WAITING_APPROVAL
            == WORKFLOW_NEXT_ACTION_WAITING_APPROVAL
        )
        assert (
            runtime_module._NEXT_ACTION_COMPLETED
            == WORKFLOW_NEXT_ACTION_COMPLETED
        )
        assert runtime_module._NEXT_ACTION_FAILED == WORKFLOW_NEXT_ACTION_FAILED
        assert (
            runtime_module._NEXT_ACTION_CANCELLED
            == WORKFLOW_NEXT_ACTION_CANCELLED
        )

    def test_run_outcome_validation(self) -> None:
        with pytest.raises(RuntimeContractError):
            RunOutcome(workflow_run_id="wf-1", outcome="NOT_A_RESULT")
        with pytest.raises(RuntimeContractError):
            RunOutcome(
                workflow_run_id="wf-1",
                outcome=OUTCOME_FAILED,
                error_code="NOPE",
            )
        fine = RunOutcome(workflow_run_id="wf-1", outcome=OUTCOME_RUNNING)
        assert fine.outcome in RUN_OUTCOMES

    def test_sync_ports_are_called_off_main_thread(self) -> None:
        env = _Env(_linear_definition())
        env.start()
        assert env.run_store.get_threads, "run_store.get 未被调用"
        assert all(
            "MainThread" not in name for name in env.run_store.get_threads
        ), "同步端口必须在 asyncio.to_thread worker 线程中调用"

    def test_list_for_run_ordering_used_for_previous(self) -> None:
        env = _Env(_linear_definition())
        env.start()
        records = env.node_store.list_for_run(_RUN_ID)
        assert [record.node_id for record in records] == [
            "collect",
            "summarize",
            "report",
        ]
