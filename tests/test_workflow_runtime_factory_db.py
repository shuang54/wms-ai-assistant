"""Phase 6 Step 5 — S5 Factory / Runtime **DB-gated** 集成测试（真 PostgreSQL）。

**默认跳过**（沿用项目既有 ``RUN_DB_TESTS`` 门控；专用测试库 = 本地开发库，
与 Step 2 DB 测试同一机制）：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_workflow_runtime_factory_db.py
```

覆盖（S5 任务书 §九C 六项 + §十）：
```text
1  创建 Run 后可从 Repository 读取（真实 Factory + 真实 Repository）
2  Run CAS 竞争只有一个成功（真实并发）
3  相同 (run,node,attempt) 并发创建只有一个成功（真实唯一约束）
4  父子 project_id 一致性
5  Run / Attempt / Approval 读写与状态版本一致（approval 全流程）
6  运行完成后没有测试数据残留
★ Runtime 并发 advance（真 Repository + 真 NodeExecutor + Fake Tool 服务）
  ⇒ 外部能力恰好调用一次（§十：单赢保证的 DB 级证据）
```

安全边界：**不调用真实 DeepSeek / 真实 Tool Handler**（orchestrator / tool
以离线替身注入）；只写 ``ai_ops.workflow_*`` 三表；定向前缀清理
（``wf-p6s5-``）；不触碰 agent 六表 / 生产数据。
"""
from __future__ import annotations

import asyncio
import os
import threading
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.db.session import get_engine
from backend.app.db.workflow_approval_repository import (
    WorkflowApprovalRepository,
)
from backend.app.db.workflow_node_run_repository import (
    WorkflowNodeRunAlreadyExistsError,
    WorkflowNodeRunRepository,
)
from backend.app.db.workflow_run_repository import (
    WorkflowRunConflictError,
    WorkflowRunRepository,
)
from backend.app.services.ai_orchestrator_service import AIOrchestrationResult
from backend.app.services.ai_router_service import RouteType
from backend.app.tools.base import ToolResult
from backend.app.workflow.definition import (
    NODE_TYPE_APPROVAL,
    NODE_TYPE_TOOL,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRegistry,
)
from backend.app.workflow.states import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    NODE_STATUS_SUCCEEDED,
    WORKFLOW_STATUS_COMPLETED,
    WORKFLOW_STATUS_PENDING,
    WORKFLOW_STATUS_WAITING,
)
from backend.app.workflow_runtime.factory import create_workflow_runtime
from backend.app.workflow_runtime.runtime import (
    OUTCOME_COMPLETED,
    OUTCOME_NOT_CANCELLABLE,
    OUTCOME_WAITING_APPROVAL,
)

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable workflow runtime factory PostgreSQL tests",
)

__all__ = [
    "TestFactoryRuntimeDb",
    "TestRepositoryAtomicityDb",
    "TestResidueDb",
]

#: 本文件的 run_id 前缀（定向清理；绝不触碰其它数据）。
_RUN_PREFIX = "wf-p6s5-"
_PROJECT_ID = "proj-s5"


def _run_id() -> str:
    return f"{_RUN_PREFIX}{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def engine() -> Any:
    eng = get_engine()
    if eng is None:
        pytest.skip("DATABASE_URL 未配置，跳过 Workflow DB 集成测试")
    return eng


def _delete_prefix(engine: Any) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM ai_ops.workflow_run "
                "WHERE workflow_run_id LIKE :prefix"
            ),
            {"prefix": f"{_RUN_PREFIX}%"},
        )


def _counts(engine: Any) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            table: int(
                conn.execute(
                    text(
                        f"SELECT COUNT(*) FROM ai_ops.{table} "
                        "WHERE workflow_run_id LIKE :prefix"
                    ),
                    {"prefix": f"{_RUN_PREFIX}%"},
                ).scalar_one()
            )
            for table in (
                "workflow_run",
                "workflow_node_run",
                "workflow_approval",
            )
        }


@pytest.fixture(scope="module", autouse=True)
def _cleanup(engine: Any):
    _delete_prefix(engine)
    yield
    _delete_prefix(engine)


# ============================================================
# 离线替身（Fake orchestrator / tool；不触真实 LLM / Tool Handler）
# ============================================================


class _CountingOrchestrator:
    def __init__(self) -> None:
        self.execute_calls: list[dict] = []
        self.cancel_calls: list[str] = []

    async def execute(self, question, *, context=None):
        self.execute_calls.append({"question": question})
        return AIOrchestrationResult(
            route=RouteType.RAG, content="答案", data=None, metadata={}
        )

    async def resume_agent_run(self, run_id, *, question, context=None):
        return AIOrchestrationResult(
            route=RouteType.RAG, content="答案", data=None, metadata={}
        )

    async def cancel_agent_run(self, run_id):
        self.cancel_calls.append(run_id)
        return object()


class _CountingToolService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[dict] = []

    async def execute(self, tool_name, *, arguments=None, context=None):
        with self._lock:
            self.calls.append({"tool_name": tool_name, "arguments": arguments})
        return ToolResult(
            tool_name=tool_name, success=True, data={"qty": 3}
        )


# ============================================================
# 定义与 runtime 装配（真实 Factory + 真实 Repository；离线外部服务）
# ============================================================


def _tool_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="s5_tool_only",
        version="v1",
        start_node_id="run_tool",
        nodes=(
            WorkflowNode(
                node_id="run_tool",
                node_type=NODE_TYPE_TOOL,
                tool_name="get_inventory",
                arguments_map={},
            ),
        ),
    )


def _approval_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="s5_approval_only",
        version="v1",
        start_node_id="gate",
        nodes=(
            WorkflowNode(
                node_id="gate",
                node_type=NODE_TYPE_APPROVAL,
                approval_prompt="请审批",
            ),
        ),
    )


def _registry(definition: WorkflowDefinition) -> WorkflowRegistry:
    registry = WorkflowRegistry()
    registry.register(definition)
    return registry


def _make_runtime(
    definition: WorkflowDefinition, *, tool_service=None, approval_id: str | None = None
):
    """真实 Factory（默认 stores = 真实 Repository）+ 离线外部服务。"""
    return create_workflow_runtime(
        registry=_registry(definition),
        project_id=_PROJECT_ID,
        orchestrator=_CountingOrchestrator(),
        tool_execution_service=tool_service or _CountingToolService(),
        approval_id_factory=(lambda: approval_id) if approval_id else None,
        run_id_factory=lambda: _run_id(),
        request_id_factory=lambda: "req-s5",
    )


def _parallel(callables: list) -> list:
    results: list = [None] * len(callables)
    errors: list = []
    barrier = threading.Barrier(len(callables))

    def worker(index: int, fn) -> None:
        try:
            barrier.wait(timeout=30)
            results[index] = fn()
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(i, fn), daemon=True)
        for i, fn in enumerate(callables)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    return results, errors


# ============================================================
# Test 1：Factory + Runtime 全链路（真实 Repository）
# ============================================================


class TestFactoryRuntimeDb:
    def test_start_persists_run_readable_from_repository(self) -> None:
        runtime = _make_runtime(_tool_definition())
        run_id = _run_id()

        outcome = asyncio.run(
            runtime.start(
                workflow_id="s5_tool_only",
                project_id=_PROJECT_ID,
                input=None,
                workflow_run_id=run_id,
            )
        )
        assert outcome.outcome == OUTCOME_COMPLETED

        row = WorkflowRunRepository().get(run_id)
        assert row is not None
        assert row.status == WORKFLOW_STATUS_COMPLETED
        assert row.project_id == _PROJECT_ID
        assert row.state_version == 2  # PENDING→RUNNING + 完成推进

        node_row = WorkflowNodeRunRepository().get(
            workflow_run_id=run_id, node_id="run_tool", attempt=1
        )
        assert node_row is not None
        assert node_row.status == NODE_STATUS_SUCCEEDED
        # 父子 project_id 一致性（F-1：子表从 run 解析复制）
        assert node_row.project_id == row.project_id

    def test_approval_flow_with_real_repository(self) -> None:
        approval_id = f"wf-ap-{uuid.uuid4().hex[:12]}"
        runtime = _make_runtime(
            _approval_definition(), approval_id=approval_id
        )
        run_id = _run_id()

        outcome = asyncio.run(
            runtime.start(
                workflow_id="s5_approval_only",
                project_id=_PROJECT_ID,
                input=None,
                workflow_run_id=run_id,
            )
        )
        assert outcome.outcome == OUTCOME_WAITING_APPROVAL
        assert outcome.approval_id == approval_id

        run_row = WorkflowRunRepository().get(run_id)
        assert run_row is not None
        assert run_row.status == WORKFLOW_STATUS_WAITING

        approval_repo = WorkflowApprovalRepository()
        approval = approval_repo.get(approval_id)
        assert approval is not None
        assert approval.status == APPROVAL_STATUS_PENDING
        assert approval.resolved_at is None

        decided = asyncio.run(
            runtime.submit_approval_decision(
                approval_id=approval_id, decision=APPROVAL_STATUS_APPROVED
            )
        )
        assert decided.approval_status == APPROVAL_STATUS_APPROVED

        resumed = asyncio.run(runtime.advance(run_id))
        assert resumed.outcome == OUTCOME_COMPLETED

        approval_after = approval_repo.get(approval_id)
        assert approval_after is not None
        assert approval_after.status == APPROVAL_STATUS_APPROVED
        assert approval_after.resolved_at is not None

        node_row = WorkflowNodeRunRepository().get(
            workflow_run_id=run_id, node_id="gate", attempt=1
        )
        assert node_row is not None
        assert node_row.status == NODE_STATUS_SUCCEEDED
        assert f'"decision":"{APPROVAL_STATUS_APPROVED}"' in (node_row.output or "")


# ============================================================
# Test 2：真实 DB 原子性（CAS / 唯一约束 / 并发 advance）
# ============================================================


class TestRepositoryAtomicityDb:
    def test_cas_race_single_winner(self) -> None:
        run_repo = WorkflowRunRepository()
        run_id = _run_id()
        run_repo.create(
            workflow_run_id=run_id,
            workflow_id="s5_tool_only",
            workflow_version="v1",
            request_id="req-s5",
            project_id=_PROJECT_ID,
            input=None,
            status=WORKFLOW_STATUS_PENDING,
        )

        def attempt_cas():
            try:
                row = run_repo.update_cas(
                    workflow_run_id=run_id,
                    expected_state_version=0,
                    status="RUNNING",
                    current_node_id="run_tool",
                    next_action="NODE_EXECUTION",
                    attempt_count=0,
                )
                return ("ok", row.state_version)
            except WorkflowRunConflictError as exc:
                return ("conflict", exc.reason)

        results, errors = _parallel([attempt_cas, attempt_cas])
        assert errors == []
        kinds = sorted(kind for kind, _detail in results)
        assert kinds == ["conflict", "ok"]  # 恰一个赢家
        winner = [detail for kind, detail in results if kind == "ok"]
        assert winner == [1]  # state_version 0 → 1

    def test_attempt_create_race_single_winner(self) -> None:
        run_repo = WorkflowRunRepository()
        node_repo = WorkflowNodeRunRepository()
        run_id = _run_id()
        run_repo.create(
            workflow_run_id=run_id,
            workflow_id="s5_tool_only",
            workflow_version="v1",
            request_id="req-s5",
            project_id=_PROJECT_ID,
            input=None,
        )

        def attempt_create():
            try:
                node_repo.create(
                    workflow_run_id=run_id,
                    node_id="run_tool",
                    attempt=1,
                    node_type=NODE_TYPE_TOOL,
                    status="RUNNING",
                )
                return "ok"
            except WorkflowNodeRunAlreadyExistsError:
                return "already_exists"

        results, errors = _parallel([attempt_create, attempt_create])
        assert errors == []
        assert sorted(results) == ["already_exists", "ok"]
        rows = node_repo.list_for_run(run_id)
        assert len(rows) == 1  # 唯一约束：单行

    def test_runtime_concurrent_advance_executes_exactly_once(self) -> None:
        """§十：装配后真实 Repository 下，并发 advance 不重复调用外部能力。"""
        tool_service = _CountingToolService()
        runtime = _make_runtime(_tool_definition(), tool_service=tool_service)
        run_id = _run_id()

        import backend.app.db.workflow_run_repository as run_repo_module

        run_repo_module.WorkflowRunRepository().create(
            workflow_run_id=run_id,
            workflow_id="s5_tool_only",
            workflow_version="v1",
            request_id="req-s5",
            project_id=_PROJECT_ID,
            input=None,
            status=WORKFLOW_STATUS_PENDING,
        )

        results, errors = _parallel(
            [
                lambda: asyncio.run(runtime.advance(run_id)),
                lambda: asyncio.run(runtime.advance(run_id)),
            ]
        )
        assert errors == []
        assert len(tool_service.calls) == 1  # 外部能力恰一次（单赢）
        assert any(
            result.outcome == OUTCOME_COMPLETED for result in results
        )
        row = WorkflowRunRepository().get(run_id)
        assert row is not None and row.status == WORKFLOW_STATUS_COMPLETED
        assert (
            len(WorkflowNodeRunRepository().list_for_run(run_id)) == 1
        )


    def test_cancel_and_terminal_are_mutually_non_overwriting(self) -> None:
        """取消与终态互斥（DB 级）：终态后取消 no-op；取消信号不被终态化抹除。"""
        run_repo = WorkflowRunRepository()

        # a) 终态后取消 ⇒ terminal conflict（不覆盖终态）
        terminal_id = _run_id()
        run_repo.create(
            workflow_run_id=terminal_id,
            workflow_id="s5_tool_only",
            workflow_version="v1",
            request_id="req-s5",
            project_id=_PROJECT_ID,
            input=None,
        )
        run_repo.update_cas(
            workflow_run_id=terminal_id,
            expected_state_version=0,
            status="CANCELLED",
            current_node_id=None,
            next_action="CANCELLED",
            attempt_count=0,
        )
        with pytest.raises(WorkflowRunConflictError):
            run_repo.request_cancel(terminal_id)
        terminal_row = run_repo.get(terminal_id)
        assert terminal_row is not None
        assert terminal_row.status == "CANCELLED"  # 不被覆盖

        # b) 取消后终态化：信号保留（读侧可解释"已取消但在途完成"），
        #    且终态不可再被取消收尾覆盖
        living_id = _run_id()
        run_repo.create(
            workflow_run_id=living_id,
            workflow_id="s5_tool_only",
            workflow_version="v1",
            request_id="req-s5",
            project_id=_PROJECT_ID,
            input=None,
        )
        signalled = run_repo.request_cancel(living_id)
        finished = run_repo.update_cas(
            workflow_run_id=living_id,
            expected_state_version=signalled.state_version,
            status="COMPLETED",
            current_node_id="run_tool",
            next_action="COMPLETED",
            attempt_count=1,
        )
        assert finished.cancel_requested is True  # 信号未被抹除
        assert finished.status == "COMPLETED"  # 完成不被取消覆盖

        # c) Runtime 层（真实组合）：已完成 run 的取消 ⇒ NOT_CANCELLABLE——
        #    终态不被覆盖。注：Repository.update_cas 属 "Persistence only"
        #    （仅 version 条件，无方向守卫）；"终态不可逆"由 Runtime 的
        #    终态前置检查 + 域转移表保证（S4 已离线验证）。
        runtime = _make_runtime(_tool_definition())
        cancel_outcome = asyncio.run(runtime.request_cancel(living_id))
        assert cancel_outcome.outcome == OUTCOME_NOT_CANCELLABLE
        after = run_repo.get(living_id)
        assert after is not None
        assert after.status == "COMPLETED"  # 终态不被取消覆盖
        assert after.cancel_requested is True


# ============================================================
# Test 3：残留
# ============================================================


class TestResidueDb:
    def test_residue_is_zero_after_cleanup(self, engine: Any) -> None:
        _delete_prefix(engine)
        assert _counts(engine) == {
            "workflow_run": 0,
            "workflow_node_run": 0,
            "workflow_approval": 0,
        }
