"""Phase 6 Step 5 — S5 Factory 单元测试（**全离线**；Fake 依赖 + 真实 Adapter）。

覆盖（S5 任务书 §九A + §十一）：
```text
· 全部必需 Port 正确装配（端到端：tool 链 / agent WAITING 链 / approval 链）
· Runtime 获得同一组预期依赖（同一性 + 类型）
· 不创建真实 Engine / Session（惰性断言） / 不调用真实 LLM · Tool
· project 绑定 fail-closed（外部 0 次调用）
· 不引入新 DI 框架 · import boundary（AST；无循环导入）
```
不跑真实 DB / LLM：stores / services 全部以离线实现注入；默认分支仅做
"构造 + 惰性"断言（真实 Repository 实例化不触 DB；默认 orchestrator 分支
需要真实 LLM 配置 ⇒ 不在此测试——由 DB-gated/生产装配覆盖）。
"""
from __future__ import annotations

import ast
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.agent.models import (
    AGENT_STATUS_WAITING_APPROVAL,
    AgentRuntimeResult,
)
from backend.app.services.ai_orchestrator_service import AIOrchestrationResult
from backend.app.services.ai_router_service import RouteType
from backend.app.services.tool_execution_context import ToolExecutionContext
from backend.app.tools.base import ToolResult
from backend.app.workflow.definition import (
    NODE_TYPE_AI,
    NODE_TYPE_APPROVAL,
    NODE_TYPE_TOOL,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRegistry,
)
from backend.app.workflow.states import (
    APPROVAL_STATUS_PENDING,
    NODE_STATUS_FAILED,
    NODE_STATUS_RUNNING,
    NODE_STATUS_SUCCEEDED,
    NODE_STATUS_WAITING_APPROVAL,
    WORKFLOW_STATUS_COMPLETED,
    WORKFLOW_STATUS_PENDING,
    WORKFLOW_STATUS_WAITING,
    WORKFLOW_TERMINAL_STATUSES,
)
from backend.app.workflow.policy import WorkflowRuntimePolicy
from backend.app.workflow_runtime.execution import NodeExecutor
from backend.app.workflow_runtime.factory import (
    SystemClock,
    create_workflow_runtime,
)
from backend.app.workflow_runtime.ports import (
    NEXT_ACTION_NONE,
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
from backend.app.workflow_runtime.runtime import (
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_WAITING_APPROVAL,
    WorkflowRuntime,
)

__all__ = [
    "TestFactoryAssembly",
    "TestFactoryBoundaries",
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FACTORY_PATH = (
    _REPO_ROOT / "backend" / "app" / "workflow_runtime" / "factory.py"
)
_ADAPTERS_PATH = (
    _REPO_ROOT / "backend" / "app" / "workflow_runtime" / "adapters.py"
)
_RUN_ID = "wf-s5-run-1"
_REQUEST_ID = "req-s5-1"
_APPROVAL_ID = "wf-ap-s5-1"
_INPUT = json.dumps({"code": "MAT-1"})


class _FixedClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now


# ============================================================
# 极简内存 stores（单线程工厂测试；语义与真实 Repository 对齐）
# ============================================================


class _RunStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def create(
        self, *, workflow_run_id, workflow_id, workflow_version, request_id,
        project_id, input, status=WORKFLOW_STATUS_PENDING,
        current_node_id=None, next_action=NEXT_ACTION_NONE,
    ) -> RunRecord:
        if workflow_run_id in self.rows:
            raise WorkflowPortsError(f"run 已存在: {workflow_run_id!r}")
        self.rows[workflow_run_id] = {
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
        }
        return RunRecord(**self.rows[workflow_run_id])

    def get(self, workflow_run_id):
        row = self.rows.get(workflow_run_id)
        return RunRecord(**dict(row)) if row else None

    def update_cas(
        self, *, workflow_run_id, expected_state_version, status,
        current_node_id, next_action, attempt_count,
    ) -> RunRecord:
        row = self.rows.get(workflow_run_id)
        if row is None:
            raise RunNotFoundError(workflow_run_id)
        if row["status"] in WORKFLOW_TERMINAL_STATUSES:
            raise RunConflictError("terminal", reason="terminal")
        if row["state_version"] != expected_state_version:
            raise RunConflictError("version", reason="version")
        row.update(
            status=status, current_node_id=current_node_id,
            next_action=next_action, attempt_count=attempt_count,
            state_version=row["state_version"] + 1,
        )
        return RunRecord(**dict(row))

    def request_cancel(self, workflow_run_id):
        row = self.rows.get(workflow_run_id)
        if row is None:
            raise RunNotFoundError(workflow_run_id)
        if row["status"] in WORKFLOW_TERMINAL_STATUSES:
            raise RunConflictError("terminal", reason="terminal")
        if row["cancel_requested"]:
            raise RunConflictError("already", reason="already_requested")
        row["cancel_requested"] = True
        row["state_version"] += 1
        return RunRecord(**dict(row))


class _NodeStore:
    def __init__(self) -> None:
        self.rows: dict[tuple, dict] = {}

    def create(
        self, *, workflow_run_id, node_id, attempt, node_type,
        status=NODE_STATUS_RUNNING, output=None, error_code=None,
        elapsed_ms=None, started_at=None, completed_at=None,
    ) -> NodeRunRecord:
        key = (workflow_run_id, node_id, attempt)
        if key in self.rows:
            raise NodeRunAlreadyExistsError(workflow_run_id, node_id, attempt)
        self.rows[key] = {
            "workflow_run_id": workflow_run_id, "node_id": node_id,
            "attempt": attempt, "node_type": node_type, "status": status,
            "output": output, "error_code": error_code,
            "elapsed_ms": elapsed_ms, "started_at": started_at,
            "completed_at": completed_at,
        }
        return NodeRunRecord(**self.rows[key])

    def get(self, *, workflow_run_id, node_id, attempt):
        row = self.rows.get((workflow_run_id, node_id, attempt))
        return NodeRunRecord(**dict(row)) if row else None

    def get_latest_attempt(self, *, workflow_run_id, node_id):
        matches = [
            r for (rid, nid, _a), r in self.rows.items()
            if rid == workflow_run_id and nid == node_id
        ]
        return NodeRunRecord(**dict(matches[-1])) if matches else None

    def list_for_run(self, workflow_run_id):
        return tuple(
            NodeRunRecord(**dict(r))
            for (rid, _n, _a), r in self.rows.items()
            if rid == workflow_run_id
        )

    def update(
        self, *, workflow_run_id, node_id, attempt, status, output=None,
        error_code=None, elapsed_ms=None, completed_at=None,
    ) -> NodeRunRecord:
        key = (workflow_run_id, node_id, attempt)
        row = self.rows.get(key)
        if row is None:
            raise NodeRunNotFoundError(workflow_run_id, node_id, attempt)
        if row["status"] in (NODE_STATUS_SUCCEEDED, NODE_STATUS_FAILED):
            raise NodeRunConflictError("attempt 已终态")
        row.update(
            status=status,
            output=output if output is not None else row["output"],
            error_code=error_code if error_code is not None else row["error_code"],
            completed_at=completed_at,
        )
        return NodeRunRecord(**dict(row))


class _ApprovalStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self._by_node: dict[tuple, str] = {}

    def create(self, *, approval_id, workflow_run_id, node_id):
        existing = self._by_node.get((workflow_run_id, node_id))
        if existing is not None:
            return ApprovalRecord(**self.rows[existing])
        self.rows[approval_id] = {
            "approval_id": approval_id, "workflow_run_id": workflow_run_id,
            "node_id": node_id, "status": APPROVAL_STATUS_PENDING,
            "resolved_at": None,
        }
        self._by_node[(workflow_run_id, node_id)] = approval_id
        return ApprovalRecord(**self.rows[approval_id])

    def get(self, approval_id):
        row = self.rows.get(approval_id)
        return ApprovalRecord(**row) if row else None

    def get_for_node(self, *, workflow_run_id, node_id):
        approval_id = self._by_node.get((workflow_run_id, node_id))
        return ApprovalRecord(**self.rows[approval_id]) if approval_id else None

    def resolve(self, *, approval_id, decision):
        row = self.rows.get(approval_id)
        if row is None or row["status"] != APPROVAL_STATUS_PENDING:
            return None
        row["status"] = decision
        row["resolved_at"] = datetime(2026, 10, 9, tzinfo=timezone.utc)
        return ApprovalRecord(**row)


class _OrchestratorService:
    """Fake AIOrchestratorService（返回真实 DTO；记录调用）。"""

    def __init__(self, *, agent_waiting: bool = False) -> None:
        self.agent_waiting = agent_waiting
        self.execute_calls: list[dict] = []
        self.resume_calls: list[dict] = []
        self.cancel_calls: list[str] = []

    async def execute(self, question, *, context=None):
        self.execute_calls.append({"question": question, "context": context})
        if not self.agent_waiting:
            return AIOrchestrationResult(
                route=RouteType.RAG, content="答案", data=None, metadata={}
            )
        return AIOrchestrationResult(
            route=RouteType.AGENT,
            content=None,
            data=AgentRuntimeResult(
                status=AGENT_STATUS_WAITING_APPROVAL, content=None, steps=1,
                tool_calls=0, llm_calls=1, error=None, run_id="agent-run-7",
                approval_id="agent-ap-7",
            ),
            metadata={"agent_status": AGENT_STATUS_WAITING_APPROVAL},
        )

    async def resume_agent_run(self, run_id, *, question, context=None):
        self.resume_calls.append({"run_id": run_id, "question": question})
        return AIOrchestrationResult(
            route=RouteType.AGENT, content="done", data=None,
            metadata={"agent_status": "COMPLETED"},
        )

    async def cancel_agent_run(self, run_id):
        self.cancel_calls.append(run_id)
        return object()


class _ToolService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, tool_name, *, arguments=None, context=None):
        self.calls.append(
            {"tool_name": tool_name, "arguments": arguments, "context": context}
        )
        return ToolResult(
            tool_name=tool_name, success=True, data={"qty": 3}
        )


# ============================================================
# 定义与装配 helper
# ============================================================


def _tool_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="tool_only", version="v1", start_node_id="run_tool",
        nodes=(
            WorkflowNode(
                node_id="run_tool", node_type=NODE_TYPE_TOOL,
                tool_name="get_inventory",
                arguments_map={"material_code": "input.code"},
            ),
        ),
    )


def _agent_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="agent_only", version="v1", start_node_id="consult",
        nodes=(
            WorkflowNode(
                node_id="consult", node_type=NODE_TYPE_AI,
                question_template="{input.code}",
            ),
        ),
    )


def _approval_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="approval_only", version="v1", start_node_id="gate",
        nodes=(
            WorkflowNode(
                node_id="gate", node_type=NODE_TYPE_APPROVAL,
                approval_prompt="请审批",
            ),
        ),
    )


def _registry(definition: WorkflowDefinition) -> WorkflowRegistry:
    registry = WorkflowRegistry()
    registry.register(definition)
    return registry


class _Env:
    def __init__(self, definition: WorkflowDefinition, *, agent_waiting: bool = False):
        self.run_store = _RunStore()
        self.node_store = _NodeStore()
        self.approval_store = _ApprovalStore()
        self.orchestrator = _OrchestratorService(agent_waiting=agent_waiting)
        self.tool_service = _ToolService()
        self.definition = definition
        self.runtime = create_workflow_runtime(
            registry=_registry(definition),
            project_id="proj-a",
            orchestrator=self.orchestrator,
            tool_execution_service=self.tool_service,
            run_store=self.run_store,
            node_run_store=self.node_store,
            approval_store=self.approval_store,
            clock=_FixedClock(),
            run_id_factory=lambda: _RUN_ID,
            request_id_factory=lambda: _REQUEST_ID,
            approval_id_factory=lambda: _APPROVAL_ID,
        )

    def start(self, **overrides):
        kwargs = {
            "workflow_id": self.definition.name,
            "project_id": "proj-a",
            "input": _INPUT,
        }
        kwargs.update(overrides)
        return asyncio.run(self.runtime.start(**kwargs))


# ============================================================
# Test 1：装配（端到端）
# ============================================================


class TestFactoryAssembly:
    def test_tool_chain_end_to_end(self) -> None:
        env = _Env(_tool_definition())
        outcome = env.start()

        assert outcome.outcome == OUTCOME_COMPLETED
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_COMPLETED

        # Tool Adapter 契约：参数（binding 产物）+ 真实 ToolExecutionContext
        assert len(env.tool_service.calls) == 1
        call = env.tool_service.calls[0]
        assert call["tool_name"] == "get_inventory"
        assert call["arguments"] == {"material_code": "MAT-1"}
        context = call["context"]
        assert isinstance(context, ToolExecutionContext)
        assert context.request_id == _REQUEST_ID
        assert context.round == 1
        assert context.project_id == "proj-a"
        assert context.tool_call_id == f"{_RUN_ID}:run_tool:1"

        # 持久化事实（真实 NodeExecutor 写入注入 store）
        node_row = env.node_store.rows[(_RUN_ID, "run_tool", 1)]
        assert node_row["status"] == NODE_STATUS_SUCCEEDED
        assert json.loads(node_row["output"]) == {"qty": 3}

    def test_agent_waiting_chain_end_to_end(self) -> None:
        env = _Env(_agent_definition(), agent_waiting=True)
        outcome = env.start()

        assert outcome.outcome == OUTCOME_WAITING_APPROVAL
        assert outcome.agent_run_id == "agent-run-7"
        assert outcome.agent_approval_id == "agent-ap-7"
        run_row = env.run_store.rows[_RUN_ID]
        assert run_row["status"] == WORKFLOW_STATUS_WAITING
        assert (
            env.node_store.rows[(_RUN_ID, "consult", 1)]["status"]
            == NODE_STATUS_WAITING_APPROVAL
        )

    def test_approval_chain_end_to_end(self) -> None:
        env = _Env(_approval_definition())
        outcome = env.start()

        assert outcome.outcome == OUTCOME_WAITING_APPROVAL
        assert outcome.approval_id == _APPROVAL_ID
        assert outcome.approval_status == APPROVAL_STATUS_PENDING
        assert env.approval_store.rows[_APPROVAL_ID]["status"] == APPROVAL_STATUS_PENDING
        assert env.run_store.rows[_RUN_ID]["status"] == WORKFLOW_STATUS_WAITING

    def test_runtime_gets_same_injected_dependencies(self) -> None:
        env = _Env(_tool_definition())
        runtime = env.runtime

        assert isinstance(runtime, WorkflowRuntime)
        assert runtime._run_store is env.run_store
        assert runtime._node_run_store is env.node_store
        assert runtime._approval_store is env.approval_store
        assert isinstance(runtime._executor, NodeExecutor)

    def test_clock_and_policy_injection(self) -> None:
        policy = WorkflowRuntimePolicy(
            max_nodes_executed=3, max_total_attempts=3
        )
        clock = _FixedClock()
        runtime = create_workflow_runtime(
            registry=_registry(_tool_definition()),
            project_id="proj-a",
            orchestrator=_OrchestratorService(),
            tool_execution_service=_ToolService(),
            run_store=_RunStore(),
            node_run_store=_NodeStore(),
            approval_store=_ApprovalStore(),
            policy=policy,
            clock=clock,
        )
        assert runtime._policy is policy
        assert runtime._clock is clock

    def test_project_binding_fails_closed_without_external_calls(self) -> None:
        env = _Env(_tool_definition())
        outcome = env.start(project_id="proj-B")  # 与绑定作用域不一致

        assert outcome.outcome == OUTCOME_FAILED
        assert env.tool_service.calls == []  # 外部 0 次调用（fail-closed）
        assert env.orchestrator.execute_calls == []

    def test_default_stores_are_real_repositories_and_lazy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """默认 stores = 真实 Repository **经 Store Adapter**；构造**不**触 DB。"""

        def _boom():  # pragma: no cover - 触发即失败
            raise AssertionError("构造期不得解析 Session 工厂")

        for module in (
            "backend.app.db.workflow_run_repository",
            "backend.app.db.workflow_node_run_repository",
            "backend.app.db.workflow_approval_repository",
        ):
            monkeypatch.setattr(f"{module}.get_session_factory", _boom)

        from backend.app.db.workflow_approval_repository import (
            WorkflowApprovalRepository,
        )
        from backend.app.db.workflow_node_run_repository import (
            WorkflowNodeRunRepository,
        )
        from backend.app.db.workflow_run_repository import WorkflowRunRepository
        from backend.app.workflow_runtime.adapters import (
            OrchestratorPortAdapter,
            ToolExecutionPortAdapter,
        )
        from backend.app.workflow_runtime.store_adapters import (
            WorkflowApprovalStoreAdapter,
            WorkflowNodeRunStoreAdapter,
            WorkflowRunStoreAdapter,
        )

        runtime = create_workflow_runtime(
            registry=_registry(_tool_definition()),
            project_id="proj-a",
            orchestrator=_OrchestratorService(),
            tool_execution_service=_ToolService(),
        )
        # Store 面 = Adapter（Runtime 只见 ports DTO，不见 ORM）
        assert isinstance(runtime._run_store, WorkflowRunStoreAdapter)
        assert isinstance(runtime._node_run_store, WorkflowNodeRunStoreAdapter)
        assert isinstance(runtime._approval_store, WorkflowApprovalStoreAdapter)
        # Adapter 内部包裹真实 Repository（构造惰性——get_session_factory 未被调用）
        assert isinstance(
            runtime._run_store._repository, WorkflowRunRepository
        )
        assert isinstance(
            runtime._node_run_store._repository, WorkflowNodeRunRepository
        )
        assert isinstance(
            runtime._approval_store._repository, WorkflowApprovalRepository
        )
        assert isinstance(runtime._executor, NodeExecutor)
        assert isinstance(runtime._clock, SystemClock)
        # 装配链内部持有的是 Adapter（而不是裸 services）
        assert isinstance(
            runtime._executor._orchestrator_provider("proj-a"),
            OrchestratorPortAdapter,
        )
        assert isinstance(
            runtime._executor._tool_execution_provider("proj-a"),
            ToolExecutionPortAdapter,
        )

    def test_factory_rejects_invalid_inputs(self) -> None:
        with pytest.raises(ValueError):
            create_workflow_runtime(
                registry=_registry(_tool_definition()),
                project_id="  ",
                orchestrator=_OrchestratorService(),
                tool_execution_service=_ToolService(),
                run_store=_RunStore(),
                node_run_store=_NodeStore(),
                approval_store=_ApprovalStore(),
            )
        with pytest.raises(TypeError):
            create_workflow_runtime(
                registry=object(),  # type: ignore[arg-type]
                project_id="proj-a",
                orchestrator=_OrchestratorService(),
                tool_execution_service=_ToolService(),
                run_store=_RunStore(),
                node_run_store=_NodeStore(),
                approval_store=_ApprovalStore(),
            )


# ============================================================
# Test 2：边界（AST；无 DI 框架；无循环导入）
# ============================================================


class TestFactoryBoundaries:
    _STDLIB = frozenset(
        {"__future__", "uuid", "collections", "datetime", "typing", "asyncio", "json", "logging", "time"}
    )
    _FACTORY_SAME_PACKAGE = frozenset(
        {
            "backend.app.workflow_runtime.adapters",
            "backend.app.workflow_runtime.execution",
            "backend.app.workflow_runtime.ports",
            "backend.app.workflow_runtime.runtime",
            "backend.app.workflow_runtime.store_adapters",
        }
    )
    _ADAPTERS_ALLOWED_EXTRA = frozenset(
        {"backend.app.agent.models", "backend.app.services"}
    )

    def _modules(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    def test_factory_imports_within_whitelist(self) -> None:
        modules = self._modules(_FACTORY_PATH)
        assert modules, "factory.py import 收集为空（环境异常）"
        for module in modules:
            if module.split(".")[0] in self._STDLIB:
                continue
            if module == "backend.app.workflow" or module.startswith(
                "backend.app.workflow."
            ):
                continue
            if module in self._FACTORY_SAME_PACKAGE:
                continue
            # 装配层允许：db / services / tools（composition root 职责）
            if any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in (
                    "backend.app.db",
                    "backend.app.services",
                    "backend.app.tools",
                )
            ):
                continue
            raise AssertionError(f"factory.py 出现白名单外 import: {module!r}")

    def test_adapters_imports_within_whitelist(self) -> None:
        modules = self._modules(_ADAPTERS_PATH)
        assert modules, "adapters.py import 收集为空（环境异常）"
        for module in modules:
            if module.split(".")[0] in self._STDLIB:
                continue
            if any(
                module == allowed or module.startswith(allowed + ".")
                for allowed in self._ADAPTERS_ALLOWED_EXTRA
            ):
                continue
            if module == "backend.app.workflow_runtime.ports":
                continue
            raise AssertionError(f"adapters.py 出现白名单外 import: {module!r}")

    def test_no_agent_runtime_or_orm_in_adapters(self) -> None:
        modules = self._modules(_ADAPTERS_PATH)
        for forbidden in (
            "backend.app.agent.runtime",
            "backend.app.agent.repository",
            "backend.app.db",
            "backend.app.api",
            "sqlalchemy",
            "fastapi",
        ):
            assert not any(
                m == forbidden or m.startswith(forbidden + ".")
                for m in modules
            ), f"adapters.py 禁入 import: {forbidden!r}"

    def test_no_di_framework_introduced(self) -> None:
        forbidden_roots = {
            "injector",
            "dependency_injector",
            "punq",
            "lagom",
            "kink",
            "wireup",
        }
        for path in (_FACTORY_PATH, _ADAPTERS_PATH):
            for module in self._modules(path):
                assert module.split(".")[0] not in forbidden_roots, (
                    f"{path.name} 引入 DI 框架: {module!r}"
                )

    def test_no_circular_import_via_import(self) -> None:
        """直接 import 全链（域 → ports/binding/execution/runtime → factory/adapters）。"""
        import importlib

        for name in (
            "backend.app.workflow_runtime.ports",
            "backend.app.workflow_runtime.binding",
            "backend.app.workflow_runtime.execution",
            "backend.app.workflow_runtime.runtime",
            "backend.app.workflow_runtime.adapters",
            "backend.app.workflow_runtime.factory",
        ):
            importlib.import_module(name)
