"""Phase 6 Step 3 — S3 execution 层测试（**全离线 Fake Port**；零 DB / 零网络 / 零 LLM）。

覆盖（用户 S3 任务书 §五 清单）：
```text
Test 1  CONDITION：true / false / previous=None / 载荷损坏 / 零外部调用
Test 2  TOOL：成功（参数 + 上下文 + 单次调用）/ 参数映射失败 ⇒ 零调用 /
        工具失败 / 端口异常 / 输出非 JSON-safe ⇒ OUTPUT_UNAVAILABLE / 结构值
Test 3  AI：普通投影 exact / content null / WAITING 恢复信息 / WAITING 无 run_id /
        端口异常 / 模板缺失 ⇒ 零调用 / 不调用 resume·cancel
Test 4  APPROVAL：PENDING 创建 + 身份 / 已裁决行 ⇒ 冲突 / 存储异常 ⇒ 冲突 /
        factory 非法 ⇒ ExecutionError / 不自动裁决（零 resolve）
Test 5  NodeExecutionOutcome：四态校验 / FAILED 码白名单 / 不伪造成功 / frozen
Test 6  契约违反：未知 node_type / 参数非法 / 非 WorkflowNode
Test 7  import boundary（AST：stdlib + 域 + 同包 ports/binding）
```
"""
from __future__ import annotations

import asyncio
import ast
import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.workflow.definition import (
    NODE_TYPE_AI,
    NODE_TYPE_APPROVAL,
    NODE_TYPE_CONDITION,
    NODE_TYPE_TOOL,
    WorkflowCondition,
    WorkflowNode,
)
from backend.app.workflow.errors import (
    WF_ERROR_APPROVAL_CONFLICT,
    WF_ERROR_NODE_EXECUTION_FAILURE,
    WF_ERROR_OUTPUT_UNAVAILABLE,
)
from backend.app.workflow.states import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    NODE_STATUS_FAILED,
    NODE_STATUS_SUCCEEDED,
    NODE_STATUS_WAITING_APPROVAL,
)
from backend.app.workflow_runtime.execution import (
    ExecutionError,
    NodeExecutionOutcome,
    NodeExecutor,
)
from backend.app.workflow_runtime.ports import (
    AGENT_STATUS_WAITING_APPROVAL,
    ApprovalRecord,
    OrchestrationOutcome,
    ToolExecutionOutcome,
    WorkflowPortsError,
)

__all__ = [
    "TestConditionNode",
    "TestToolNode",
    "TestAiNode",
    "TestApprovalNode",
    "TestOutcomeDto",
    "TestDispatchContract",
    "TestExecutionImportBoundary",
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EXECUTION_PATH = (
    _REPO_ROOT / "backend" / "app" / "workflow_runtime" / "execution.py"
)

_INPUT = json.dumps({"q": "剩余库存是多少", "k": "SO-1"}, ensure_ascii=False)
_PREVIOUS = json.dumps({"qty": 10, "labels": ["a", "b"]}, ensure_ascii=False)


# ============================================================
# Fakes（仅测试内部；不 import 任何真实执行面）
# ============================================================


class _FakeOrchestrator:
    def __init__(
        self,
        *,
        outcome: OrchestrationOutcome | None = None,
        error: Exception | None = None,
    ) -> None:
        self.outcome = outcome
        self.error = error
        self.calls: list[str] = []
        self.resume_calls: list[str] = []
        self.cancel_calls: list[str] = []

    async def execute(self, question: str, *, context: str | None = None):
        self.calls.append(question)
        if self.error is not None:
            raise self.error
        return self.outcome

    async def resume_agent_run(
        self, run_id: str, *, question: str, context: str | None = None
    ):
        self.resume_calls.append(run_id)
        raise AssertionError("executor 不得调用 resume_agent_run")

    async def cancel_agent_run(self, run_id: str) -> None:
        self.cancel_calls.append(run_id)
        raise AssertionError("executor 不得调用 cancel_agent_run")


class _FakeToolPort:
    def __init__(
        self,
        *,
        outcome: ToolExecutionOutcome | None = None,
        error: Exception | None = None,
    ) -> None:
        self.outcome = outcome
        self.error = error
        self.calls: list[dict] = []

    async def execute(
        self,
        tool_name: str,
        *,
        arguments: dict | None = None,
        context=None,
    ):
        self.calls.append(
            {"tool_name": tool_name, "arguments": arguments, "context": context}
        )
        if self.error is not None:
            raise self.error
        return self.outcome


class _FakeApprovalStore:
    def __init__(
        self,
        *,
        record: ApprovalRecord | None = None,
        error: Exception | None = None,
    ) -> None:
        self.record = record
        self.error = error
        self.calls: list[dict] = []
        self.resolve_calls: list[dict] = []

    def create(self, *, approval_id, workflow_run_id, node_id) -> ApprovalRecord:
        self.calls.append(
            {
                "approval_id": approval_id,
                "workflow_run_id": workflow_run_id,
                "node_id": node_id,
            }
        )
        if self.error is not None:
            raise self.error
        if self.record is not None:
            return self.record
        return ApprovalRecord(
            approval_id=approval_id,
            workflow_run_id=workflow_run_id,
            node_id=node_id,
            status=APPROVAL_STATUS_PENDING,
        )

    def get(self, approval_id):  # pragma: no cover
        raise AssertionError("executor 不得读取审批")

    def get_for_node(self, *, workflow_run_id, node_id):  # pragma: no cover
        raise AssertionError("executor 不得读取审批")

    def resolve(self, *, approval_id, decision):  # pragma: no cover
        self.resolve_calls.append(
            {"approval_id": approval_id, "decision": decision}
        )
        raise AssertionError("executor 不得裁决审批")


class _Env:
    """最小执行环境（Fake 端口 + provider 调用记录）。"""

    def __init__(
        self,
        *,
        orchestrator: _FakeOrchestrator | None = None,
        tool: _FakeToolPort | None = None,
        store: _FakeApprovalStore | None = None,
        approval_id: str = "wf-ap-1",
    ) -> None:
        self.orchestrator = orchestrator or _FakeOrchestrator()
        self.tool = tool or _FakeToolPort()
        self.store = store or _FakeApprovalStore()
        self.orchestrator_provider_calls: list[str] = []
        self.tool_provider_calls: list[str] = []
        self.executor = NodeExecutor(
            orchestrator_provider=self._orch_provider,
            tool_execution_provider=self._tool_provider,
            approval_store=self.store,
            approval_id_factory=lambda: approval_id,
        )

    def _orch_provider(self, project_id: str) -> _FakeOrchestrator:
        self.orchestrator_provider_calls.append(project_id)
        return self.orchestrator

    def _tool_provider(self, project_id: str) -> _FakeToolPort:
        self.tool_provider_calls.append(project_id)
        return self.tool


def _condition_node() -> WorkflowNode:
    return WorkflowNode(
        node_id="check_stock",
        node_type=NODE_TYPE_CONDITION,
        condition=WorkflowCondition(source="qty", operator="GT", expected=5),
        on_true_node_id="restock",
        on_false_node_id="done",
    )


def _tool_node(arguments_map: dict | None = None) -> WorkflowNode:
    return WorkflowNode(
        node_id="run_tool",
        node_type=NODE_TYPE_TOOL,
        tool_name="get_inventory",
        arguments_map=arguments_map or {"order_no": "input.k"},
    )


def _ai_node(template: str = "{input.q}") -> WorkflowNode:
    return WorkflowNode(
        node_id="ask_ai",
        node_type=NODE_TYPE_AI,
        question_template=template,
    )


def _approval_node() -> WorkflowNode:
    return WorkflowNode(
        node_id="approve_step",
        node_type=NODE_TYPE_APPROVAL,
        approval_prompt="请审批补货",
    )


def _run(
    env: _Env,
    node: WorkflowNode,
    *,
    attempt: int = 1,
    project_id: str = "proj-a",
    input: str | None = _INPUT,
    previous: str | None = _PREVIOUS,
) -> NodeExecutionOutcome:
    return asyncio.run(
        env.executor.execute_node(
            node,
            workflow_run_id="wf-run-1",
            project_id=project_id,
            request_id="req-1",
            input=input,
            previous=previous,
            attempt=attempt,
        )
    )


# ============================================================
# Test 1：CONDITION
# ============================================================


class TestConditionNode:
    def test_true_branch(self) -> None:
        env = _Env()
        outcome = _run(env, _condition_node())

        assert outcome.status == NODE_STATUS_SUCCEEDED
        assert outcome.output == "true"

    def test_false_branch(self) -> None:
        env = _Env()
        outcome = _run(
            env, _condition_node(), previous=json.dumps({"qty": 1})
        )

        assert outcome.status == NODE_STATUS_SUCCEEDED
        assert outcome.output == "false"

    def test_previous_none_is_false(self) -> None:
        env = _Env()
        outcome = _run(env, _condition_node(), previous=None)

        assert outcome.status == NODE_STATUS_SUCCEEDED
        assert outcome.output == "false"

    def test_corrupted_previous_payload_fails(self) -> None:
        env = _Env()
        outcome = _run(env, _condition_node(), previous="{bad")

        assert outcome.status == NODE_STATUS_FAILED
        assert outcome.error_code == WF_ERROR_NODE_EXECUTION_FAILURE

    def test_condition_touches_no_external_port(self) -> None:
        env = _Env()
        _run(env, _condition_node())

        assert env.orchestrator.calls == []
        assert env.tool.calls == []
        assert env.store.calls == []
        assert env.orchestrator_provider_calls == []
        assert env.tool_provider_calls == []


# ============================================================
# Test 2：TOOL
# ============================================================


class TestToolNode:
    def test_success_calls_tool_once_with_arguments_and_context(self) -> None:
        env = _Env(
            tool=_FakeToolPort(
                outcome=ToolExecutionOutcome(success=True, data={"qty": 7})
            )
        )
        outcome = _run(env, _tool_node(), attempt=2)

        assert outcome.status == NODE_STATUS_SUCCEEDED
        assert json.loads(outcome.output) == {"qty": 7}

        assert len(env.tool.calls) == 1
        call = env.tool.calls[0]
        assert call["tool_name"] == "get_inventory"
        assert call["arguments"] == {"order_no": "SO-1"}

        context = call["context"]
        assert context.request_id == "req-1"
        assert context.round == 1
        assert context.project_id == "proj-a"
        assert context.tool_call_id == "wf-run-1:run_tool:2"
        assert env.tool_provider_calls == ["proj-a"]

    def test_structural_tool_output_is_json_safe(self) -> None:
        env = _Env(
            tool=_FakeToolPort(
                outcome=ToolExecutionOutcome(
                    success=True, data={"items": [1, 2], "note": None}
                )
            )
        )
        outcome = _run(env, _tool_node())

        assert json.loads(outcome.output) == {"items": [1, 2], "note": None}

    def test_arguments_mapping_failure_never_calls_tool(self) -> None:
        env = _Env()
        outcome = _run(
            env,
            _tool_node(arguments_map={"order_no": "input.missing"}),
        )

        assert outcome.status == NODE_STATUS_FAILED
        assert outcome.error_code == WF_ERROR_NODE_EXECUTION_FAILURE
        assert env.tool.calls == []
        assert env.tool_provider_calls == []

    def test_tool_reported_failure(self) -> None:
        env = _Env(
            tool=_FakeToolPort(
                outcome=ToolExecutionOutcome(
                    success=False, error="tool not registered"
                )
            )
        )
        outcome = _run(env, _tool_node())

        assert outcome.status == NODE_STATUS_FAILED
        assert outcome.error_code == WF_ERROR_NODE_EXECUTION_FAILURE
        assert len(env.tool.calls) == 1  # 只调用一次（不重试）

    def test_tool_port_exception_is_normalized(self) -> None:
        env = _Env(tool=_FakeToolPort(error=RuntimeError("boom")))
        outcome = _run(env, _tool_node())

        assert outcome.status == NODE_STATUS_FAILED
        assert outcome.error_code == WF_ERROR_NODE_EXECUTION_FAILURE
        assert len(env.tool.calls) == 1

    def test_non_json_safe_tool_output_is_unavailable(self) -> None:
        env = _Env(
            tool=_FakeToolPort(
                outcome=ToolExecutionOutcome(success=True, data=object())
            )
        )
        outcome = _run(env, _tool_node())

        assert outcome.status == NODE_STATUS_FAILED
        assert outcome.error_code == WF_ERROR_OUTPUT_UNAVAILABLE


# ============================================================
# Test 3：AI
# ============================================================


class TestAiNode:
    def test_plain_result_projection_is_exact(self) -> None:
        env = _Env(
            orchestrator=_FakeOrchestrator(
                outcome=OrchestrationOutcome(route="rag", content="答案")
            )
        )
        outcome = _run(env, _ai_node())

        assert outcome.status == NODE_STATUS_SUCCEEDED
        payload = json.loads(outcome.output)
        assert payload == {"route": "rag", "content": "答案"}
        assert set(payload.keys()) == {"route", "content"}  # 不暴露敏感字段

        assert env.orchestrator.calls == ["剩余库存是多少"]
        assert env.orchestrator_provider_calls == ["proj-a"]
        assert env.orchestrator.resume_calls == []
        assert env.orchestrator.cancel_calls == []

    def test_content_none_renders_json_null(self) -> None:
        env = _Env(
            orchestrator=_FakeOrchestrator(
                outcome=OrchestrationOutcome(route="tool", content=None)
            )
        )
        outcome = _run(env, _ai_node())

        assert json.loads(outcome.output) == {"route": "tool", "content": None}

    def test_agent_waiting_keeps_recovery_identities(self) -> None:
        env = _Env(
            orchestrator=_FakeOrchestrator(
                outcome=OrchestrationOutcome(
                    route="agent",
                    content=None,
                    agent_run_id="agent-run-1",
                    agent_status=AGENT_STATUS_WAITING_APPROVAL,
                    agent_approval_id="agent-ap-1",
                )
            )
        )
        outcome = _run(env, _ai_node())

        assert outcome.status == NODE_STATUS_WAITING_APPROVAL
        assert outcome.agent_run_id == "agent-run-1"
        assert outcome.agent_approval_id == "agent-ap-1"
        assert json.loads(outcome.output) == {
            "agent_run_id": "agent-run-1",
            "agent_approval_id": "agent-ap-1",
        }
        assert env.orchestrator.resume_calls == []  # 不自动恢复

    def test_agent_waiting_without_run_id_fails(self) -> None:
        env = _Env(
            orchestrator=_FakeOrchestrator(
                outcome=OrchestrationOutcome(
                    route="agent",
                    agent_status=AGENT_STATUS_WAITING_APPROVAL,
                    agent_run_id=None,
                )
            )
        )
        outcome = _run(env, _ai_node())

        assert outcome.status == NODE_STATUS_FAILED
        assert outcome.error_code == WF_ERROR_NODE_EXECUTION_FAILURE

    def test_orchestrator_exception_is_normalized(self) -> None:
        env = _Env(
            orchestrator=_FakeOrchestrator(error=RuntimeError("llm down"))
        )
        outcome = _run(env, _ai_node())

        assert outcome.status == NODE_STATUS_FAILED
        assert outcome.error_code == WF_ERROR_NODE_EXECUTION_FAILURE
        assert len(env.orchestrator.calls) == 1  # 只调用一次（不重试）

    def test_template_failure_never_calls_orchestrator(self) -> None:
        env = _Env()
        outcome = _run(env, _ai_node(template="{input.missing}"))

        assert outcome.status == NODE_STATUS_FAILED
        assert outcome.error_code == WF_ERROR_NODE_EXECUTION_FAILURE
        assert env.orchestrator.calls == []
        assert env.orchestrator_provider_calls == []


# ============================================================
# Test 4：APPROVAL
# ============================================================


class TestApprovalNode:
    def test_creates_pending_record_and_waits(self) -> None:
        env = _Env(approval_id="wf-ap-9")
        outcome = _run(env, _approval_node())

        assert outcome.status == NODE_STATUS_WAITING_APPROVAL
        assert outcome.approval_id == "wf-ap-9"
        assert json.loads(outcome.output) == {"approval_id": "wf-ap-9"}

        assert env.store.calls == [
            {
                "approval_id": "wf-ap-9",
                "workflow_run_id": "wf-run-1",
                "node_id": "approve_step",
            }
        ]
        assert env.store.resolve_calls == []  # 不自动批准 / 拒绝

    def test_already_resolved_record_is_conflict(self) -> None:
        env = _Env(
            store=_FakeApprovalStore(
                record=ApprovalRecord(
                    approval_id="wf-ap-old",
                    workflow_run_id="wf-run-1",
                    node_id="approve_step",
                    status=APPROVAL_STATUS_APPROVED,
                    resolved_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
                )
            )
        )
        outcome = _run(env, _approval_node())

        assert outcome.status == NODE_STATUS_FAILED
        assert outcome.error_code == WF_ERROR_APPROVAL_CONFLICT

    def test_store_failure_is_conflict_not_silent(self) -> None:
        env = _Env(store=_FakeApprovalStore(error=WorkflowPortsError("db")))
        outcome = _run(env, _approval_node())

        assert outcome.status == NODE_STATUS_FAILED
        assert outcome.error_code == WF_ERROR_APPROVAL_CONFLICT
        assert len(env.store.calls) == 1  # 不重试、不静默创建第二条

    def test_invalid_approval_id_factory_is_contract_violation(self) -> None:
        env = _Env(approval_id="  ")
        with pytest.raises(ExecutionError):
            _run(env, _approval_node())
        assert env.store.calls == []


# ============================================================
# Test 5：NodeExecutionOutcome
# ============================================================


class TestOutcomeDto:
    def test_succeeded_requires_output(self) -> None:
        outcome = NodeExecutionOutcome(
            status=NODE_STATUS_SUCCEEDED, output="null"
        )
        assert outcome.output == "null"
        with pytest.raises(ExecutionError):
            NodeExecutionOutcome(status=NODE_STATUS_SUCCEEDED)

    def test_failed_requires_whitelisted_error_code(self) -> None:
        outcome = NodeExecutionOutcome(
            status=NODE_STATUS_FAILED, error_code=WF_ERROR_NODE_EXECUTION_FAILURE
        )
        assert outcome.error_code == WF_ERROR_NODE_EXECUTION_FAILURE
        with pytest.raises(ExecutionError):
            NodeExecutionOutcome(status=NODE_STATUS_FAILED)
        with pytest.raises(ExecutionError):
            NodeExecutionOutcome(
                status=NODE_STATUS_FAILED, error_code="NOPE_NOT_A_CODE"
            )

    def test_failed_must_not_fake_output(self) -> None:
        with pytest.raises(ExecutionError):
            NodeExecutionOutcome(
                status=NODE_STATUS_FAILED,
                error_code=WF_ERROR_NODE_EXECUTION_FAILURE,
                output="{}",
            )

    def test_non_failed_must_not_carry_error_code(self) -> None:
        with pytest.raises(ExecutionError):
            NodeExecutionOutcome(
                status=NODE_STATUS_SUCCEEDED,
                output="{}",
                error_code=WF_ERROR_NODE_EXECUTION_FAILURE,
            )

    def test_waiting_requires_identity(self) -> None:
        with pytest.raises(ExecutionError):
            NodeExecutionOutcome(
                status=NODE_STATUS_WAITING_APPROVAL, output="{}"
            )
        ok = NodeExecutionOutcome(
            status=NODE_STATUS_WAITING_APPROVAL,
            output="{}",
            approval_id="wf-ap-1",
        )
        assert ok.approval_id == "wf-ap-1"

    def test_unknown_status_rejected_and_frozen(self) -> None:
        with pytest.raises(ExecutionError):
            NodeExecutionOutcome(status="RUNNING", output="{}")
        outcome = NodeExecutionOutcome(
            status=NODE_STATUS_SUCCEEDED, output="true"
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            outcome.status = NODE_STATUS_FAILED  # type: ignore[misc]


# ============================================================
# Test 6：契约违反
# ============================================================


class TestDispatchContract:
    def test_unknown_node_type_raises(self) -> None:
        env = _Env()
        node = _ai_node()
        object.__setattr__(node, "node_type", "LOOP")
        with pytest.raises(ExecutionError):
            _run(env, node)

    def test_non_workflow_node_raises(self) -> None:
        env = _Env()
        with pytest.raises(ExecutionError):
            _run(env, "not a node")  # type: ignore[arg-type]

    def test_invalid_context_arguments_raise(self) -> None:
        env = _Env()
        node = _condition_node()
        with pytest.raises(ExecutionError):
            _run(env, node, attempt=0)
        with pytest.raises(ExecutionError):
            _run(env, node, project_id="  ")


# ============================================================
# Test 7：import boundary（AST）
# ============================================================


class TestExecutionImportBoundary:
    _STDLIB_ALLOWED = frozenset({"__future__", "dataclasses", "typing"})
    _SAME_PACKAGE_ALLOWED = frozenset(
        {
            "backend.app.workflow_runtime.ports",
            "backend.app.workflow_runtime.binding",
        }
    )

    def _modules(self) -> set[str]:
        tree = ast.parse(_EXECUTION_PATH.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    def test_imports_are_stdlib_domain_or_ports_binding_only(self) -> None:
        modules = self._modules()
        assert modules, "execution.py import 收集为空（环境异常）"
        for module in modules:
            if module.split(".")[0] in self._STDLIB_ALLOWED:
                continue
            if module == "backend.app.workflow" or module.startswith(
                "backend.app.workflow."
            ):
                continue
            assert module in self._SAME_PACKAGE_ALLOWED, (
                f"execution.py 出现边界外 import: {module!r}"
            )

    def test_forbidden_packages_absent(self) -> None:
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
            "backend.app.workflow_runtime.runtime",
            "backend.app.workflow_runtime.adapters",
            "sqlalchemy",
            "fastapi",
            "httpx",
        ):
            assert not any(
                m == forbidden or m.startswith(forbidden + ".")
                for m in modules
            ), f"execution.py 禁入 import: {forbidden!r}"
