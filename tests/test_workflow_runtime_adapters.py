"""Phase 6 Step 5 — S5 Adapter 契约测试（**全离线**；真实 DTO + Fake 服务）。

覆盖（S5 任务书 §九B）：
```text
Orchestrator：RAG / TOOL / TEXT_TO_SQL 成功映射 · AGENT COMPLETED / WAITING /
              LIMIT_REACHED / FAILED / 状态缺失 · 未知 route · 异常归一 ·
              resume 传参 · cancel
Tool        ：成功 / 失败（不伪装） / 异常归一 · Context 映射（ToolExecutionContext
              字段一致） · project 不一致 fail-closed（外部 0 调用） ·
              ToolResult 结构损坏 fail-closed · arguments 副本隔离
安全        ：metadata / data 结构 / 密钥不进入 ports DTO
```
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.app.agent.models import (
    AGENT_STATUS_COMPLETED,
    AGENT_STATUS_FAILED,
    AGENT_STATUS_LIMIT_REACHED,
    AGENT_STATUS_WAITING_APPROVAL,
    AgentRuntimeResult,
)
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorExecutionError,
)
from backend.app.services.ai_router_service import RouteType
from backend.app.services.tool_execution_context import ToolExecutionContext
from backend.app.tools.base import ToolResult
from backend.app.workflow_runtime.adapters import (
    OrchestratorPortAdapter,
    ToolExecutionPortAdapter,
)
from backend.app.workflow_runtime.ports import (
    ToolCallContext,
    WorkflowPortsError,
)

__all__ = [
    "TestOrchestratorAdapter",
    "TestToolAdapter",
]

_SECRET = "sk-SUPER-SECRET-KEY"


# ============================================================
# Fakes（假服务；返回**真实 DTO**）
# ============================================================


def _agent_result(
    *,
    status: str,
    content: str | None = "agent answer",
    run_id: str = "agent-run-1",
    approval_id: str | None = None,
) -> AgentRuntimeResult:
    return AgentRuntimeResult(
        status=status,
        content=content,
        steps=1,
        tool_calls=0,
        llm_calls=1,
        error=None,
        run_id=run_id,
        approval_id=approval_id,
    )


def _result(
    *,
    route: Any,
    content: str | None,
    data: Any = None,
    agent_status: str | None = None,
) -> AIOrchestrationResult:
    metadata: dict[str, Any] = {
        "decision_source": "llm",
        "route_reason": "test",
        "request_id": "req-x",
        "api_key": _SECRET,  # 故意放"敏感"字段：断言不进入 ports DTO
    }
    if agent_status is not None:
        metadata["agent_status"] = agent_status
    return AIOrchestrationResult(
        route=route, content=content, data=data, metadata=metadata
    )


class _FakeOrchestratorService:
    def __init__(
        self,
        *,
        execute_result: Any = None,
        execute_error: Exception | None = None,
        resume_result: Any = None,
        resume_error: Exception | None = None,
    ) -> None:
        self.execute_calls: list[dict] = []
        self.resume_calls: list[dict] = []
        self.cancel_calls: list[str] = []
        self._execute_result = execute_result
        self._execute_error = execute_error
        self._resume_result = resume_result
        self._resume_error = resume_error

    async def execute(self, question, *, context=None):
        self.execute_calls.append({"question": question, "context": context})
        if self._execute_error is not None:
            raise self._execute_error
        return self._execute_result

    async def resume_agent_run(self, run_id, *, question, context=None):
        self.resume_calls.append(
            {"run_id": run_id, "question": question, "context": context}
        )
        if self._resume_error is not None:
            raise self._resume_error
        return self._resume_result

    async def cancel_agent_run(self, run_id):
        self.cancel_calls.append(run_id)
        return object()  # 真实返回 AgentCancellationResult；adapter 必须忽略


class _FakeToolService:
    def __init__(
        self,
        *,
        result: Any = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[dict] = []
        self._result = result
        self._error = error

    async def execute(self, tool_name, *, arguments=None, context=None):
        self.calls.append(
            {"tool_name": tool_name, "arguments": arguments, "context": context}
        )
        if self._error is not None:
            raise self._error
        return self._result


# ============================================================
# Orchestrator Adapter
# ============================================================


class TestOrchestratorAdapter:
    def _adapter(self, **kwargs) -> tuple[OrchestratorPortAdapter, _FakeOrchestratorService]:
        service = _FakeOrchestratorService(**kwargs)
        return OrchestratorPortAdapter(service), service

    def test_invalid_service_rejected(self) -> None:
        with pytest.raises(TypeError):
            OrchestratorPortAdapter(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            OrchestratorPortAdapter(object())  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "route,content",
        [
            (RouteType.RAG, "rag 答案"),
            (RouteType.TOOL, "tool 摘要"),
            (RouteType.TEXT_TO_SQL, "sql 摘要"),
        ],
    )
    def test_plain_routes_map_route_and_content(
        self, route: RouteType, content: str
    ) -> None:
        import asyncio

        adapter, service = self._adapter(
            execute_result=_result(route=route, content=content, data={"k": 1})
        )
        outcome = asyncio.run(adapter.execute("q1"))

        assert outcome.route == route.value
        assert outcome.content == content
        assert outcome.agent_run_id is None
        assert outcome.agent_status is None
        assert outcome.agent_approval_id is None
        assert service.execute_calls == [{"question": "q1", "context": None}]

    def test_agent_completed_maps_status(self) -> None:
        import asyncio

        adapter, _ = self._adapter(
            execute_result=_result(
                route=RouteType.AGENT,
                content="done",
                data=_agent_result(status=AGENT_STATUS_COMPLETED),
                agent_status=AGENT_STATUS_COMPLETED,
            )
        )
        outcome = asyncio.run(adapter.execute("q"))

        assert outcome.route == "agent"
        assert outcome.content == "done"
        assert outcome.agent_status == AGENT_STATUS_COMPLETED
        assert outcome.agent_run_id is None  # 成功路径不携带恢复键

    def test_agent_waiting_maps_recovery_identities(self) -> None:
        import asyncio

        adapter, _ = self._adapter(
            execute_result=_result(
                route=RouteType.AGENT,
                content=None,
                data=_agent_result(
                    status=AGENT_STATUS_WAITING_APPROVAL,
                    content=None,
                    run_id="agent-run-9",
                    approval_id="agent-ap-9",
                ),
                agent_status=AGENT_STATUS_WAITING_APPROVAL,
            )
        )
        outcome = asyncio.run(adapter.execute("q"))

        assert outcome.agent_status == AGENT_STATUS_WAITING_APPROVAL
        assert outcome.agent_run_id == "agent-run-9"
        assert outcome.agent_approval_id == "agent-ap-9"

    def test_agent_waiting_missing_run_id_fails_closed(self) -> None:
        import asyncio

        adapter, _ = self._adapter(
            execute_result=_result(
                route=RouteType.AGENT,
                content=None,
                data=_agent_result(
                    status=AGENT_STATUS_WAITING_APPROVAL,
                    content=None,
                    run_id="  ",  # 损坏
                ),
                agent_status=AGENT_STATUS_WAITING_APPROVAL,
            )
        )
        with pytest.raises(WorkflowPortsError):
            asyncio.run(adapter.execute("q"))

    @pytest.mark.parametrize(
        "status", [AGENT_STATUS_LIMIT_REACHED, AGENT_STATUS_FAILED, None, "WEIRD"]
    )
    def test_agent_non_success_states_fail_closed(self, status) -> None:
        import asyncio

        adapter, _ = self._adapter(
            execute_result=_result(
                route=RouteType.AGENT,
                content=None,
                data=_agent_result(status=status or AGENT_STATUS_FAILED, content=None),
                agent_status=status,
            )
        )
        with pytest.raises(WorkflowPortsError):
            asyncio.run(adapter.execute("q"))

    def test_unknown_route_fails_closed(self) -> None:
        import asyncio

        adapter, _ = self._adapter(
            execute_result=_result(route="weird_route", content="x")
        )
        with pytest.raises(WorkflowPortsError):
            asyncio.run(adapter.execute("q"))

    def test_execute_error_normalized_with_cause(self) -> None:
        import asyncio

        adapter, _ = self._adapter(
            execute_error=AIOrchestratorExecutionError("downstream failed: boom")
        )
        with pytest.raises(WorkflowPortsError) as info:
            asyncio.run(adapter.execute("q"))
        assert isinstance(info.value.__cause__, AIOrchestratorExecutionError)
        # 消息不含异常正文（只带类型名）
        assert "boom" not in str(info.value)

    def test_resume_signature_and_mapping(self) -> None:
        import asyncio

        adapter, service = self._adapter(
            resume_result=_result(
                route=RouteType.AGENT,
                content="resumed",
                data=_agent_result(status=AGENT_STATUS_COMPLETED),
                agent_status=AGENT_STATUS_COMPLETED,
            )
        )
        outcome = asyncio.run(
            adapter.resume_agent_run("agent-run-1", question="继续", context=None)
        )
        assert outcome.content == "resumed"
        assert service.resume_calls == [
            {"run_id": "agent-run-1", "question": "继续", "context": None}
        ]

    def test_cancel_returns_none_and_calls_service(self) -> None:
        import asyncio

        adapter, service = self._adapter()
        assert asyncio.run(adapter.cancel_agent_run("agent-run-1")) is None
        assert service.cancel_calls == ["agent-run-1"]


# ============================================================
# Tool Adapter
# ============================================================


class TestToolAdapter:
    def _adapter(
        self, *, result: Any = None, error: Exception | None = None,
        project_id: str | None = "proj-a",
    ) -> tuple[ToolExecutionPortAdapter, _FakeToolService]:
        service = _FakeToolService(result=result, error=error)
        return (
            ToolExecutionPortAdapter(service, project_id=project_id),
            service,
        )

    def test_invalid_service_rejected(self) -> None:
        with pytest.raises(TypeError):
            ToolExecutionPortAdapter(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            ToolExecutionPortAdapter(object())  # type: ignore[arg-type]

    def test_success_maps_result(self) -> None:
        import asyncio

        adapter, service = self._adapter(
            result=ToolResult(
                tool_name="get_inventory", success=True, data={"qty": 7}
            )
        )
        outcome = asyncio.run(adapter.execute("get_inventory"))

        assert outcome.success is True
        assert outcome.data == {"qty": 7}
        assert outcome.error is None
        assert service.calls[0]["tool_name"] == "get_inventory"

    def test_failure_is_not_converted_to_success(self) -> None:
        import asyncio

        adapter, _ = self._adapter(
            result=ToolResult(
                tool_name="get_inventory", success=False, error="tool not found"
            )
        )
        outcome = asyncio.run(adapter.execute("get_inventory"))

        assert outcome.success is False
        assert outcome.data is None
        assert outcome.error == "tool not found"

    def test_service_exception_normalized(self) -> None:
        import asyncio

        adapter, service = self._adapter(error=ValueError("context mismatch: secret"))
        with pytest.raises(WorkflowPortsError) as info:
            asyncio.run(adapter.execute("get_inventory"))
        assert isinstance(info.value.__cause__, ValueError)
        assert "secret" not in str(info.value)
        assert len(service.calls) == 1

    def test_context_maps_to_tool_execution_context(self) -> None:
        import asyncio

        adapter, service = self._adapter(
            result=ToolResult(tool_name="t", success=True, data=1)
        )
        context = ToolCallContext(
            request_id="req-1", round=2, project_id="proj-a", tool_call_id="tc-9"
        )
        asyncio.run(adapter.execute("t", arguments={"a": 1}, context=context))

        received = service.calls[0]["context"]
        assert isinstance(received, ToolExecutionContext)
        assert received.request_id == "req-1"
        assert received.round == 2
        assert received.project_id == "proj-a"
        assert received.tool_call_id == "tc-9"
        assert service.calls[0]["arguments"] == {"a": 1}

    def test_project_mismatch_fails_closed_without_service_call(self) -> None:
        import asyncio

        adapter, service = self._adapter(project_id="proj-a")
        foreign = ToolCallContext(
            request_id="req-1", round=1, project_id="proj-B"
        )
        with pytest.raises(WorkflowPortsError):
            asyncio.run(adapter.execute("t", context=foreign))
        assert service.calls == []  # 外部 0 次调用

    def test_missing_project_in_context_is_allowed_when_unbound(self) -> None:
        import asyncio

        adapter, service = self._adapter(
            result=ToolResult(tool_name="t", success=True, data=1),
            project_id=None,
        )
        context = ToolCallContext(request_id="req-1", round=1)
        asyncio.run(adapter.execute("t", context=context))
        assert service.calls[0]["context"].project_id is None

    def test_broken_tool_result_fails_closed(self) -> None:
        import asyncio

        adapter, _ = self._adapter(result=object())
        with pytest.raises(WorkflowPortsError):
            asyncio.run(adapter.execute("t"))

    def test_arguments_are_copied(self) -> None:
        import asyncio

        adapter, service = self._adapter(
            result=ToolResult(tool_name="t", success=True, data=1)
        )
        original = {"a": 1}
        asyncio.run(adapter.execute("t", arguments=original))
        received = service.calls[0]["arguments"]
        assert received == original
        assert received is not original

    def test_invalid_context_type_fails_closed(self) -> None:
        import asyncio

        adapter, service = self._adapter()
        with pytest.raises(WorkflowPortsError):
            asyncio.run(adapter.execute("t", context=object()))  # type: ignore[arg-type]
        assert service.calls == []
