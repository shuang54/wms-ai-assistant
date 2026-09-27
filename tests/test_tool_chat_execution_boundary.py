"""ToolChatService Execution Boundary 迁移测试（Phase 3.11 Step 9）。

验证迁移：

```text
Before：ToolChatService → ToolRegistry.execute                （直接执行）
After ：ToolChatService → ToolExecutionService → ToolRegistry.execute
```

覆盖（§十二 Test 1–8）：

```text
Test 1  ToolChatService 使用 ExecutionService（且不直接调用 registry.execute）
Test 2  arguments 原样转发（不 rename / drop / add / normalize）
Test 3  ToolResult 原样进入 role=tool 消息
Test 4  Tool failure continuation（success=False → LLM 继续）
Test 5  Multi-Step（2 次执行；执行边界本身无 loop）
Test 6  Budget（ToolCallingBudgetExceededError 语义不变）
Test 7  Multiple Tool Calls（MultipleToolCallsError；0 次执行）
Test 8  Malformed ToolCall（LLMToolCallFormatError；0 次执行）
Plus    API 装配：module-level 单例注入同一执行边界（HTTP 级验证）
```

复用既有测试资产（不重复造 Mock Framework）：
`tests/test_tool_chat_service.py` 的 ScriptedLLMClient / _tool_call_llm_response、
`tests/test_tool_chat_service_characterization.py` 的 _CountingRegistry。

0 DB / 0 Network / 0 真实 LLM。
"""
from __future__ import annotations

import ast
import inspect
import json
import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.llm.client import LLMResponse, LLMToolCallFormatError, ToolCall
from backend.app.services.tool_chat_service import (
    MultipleToolCallsError,
    ToolCallingBudgetExceededError,
    _serialize_tool_result,
)
from backend.app.services.tool_chat_service import ToolChatService
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.base import ToolResult
from backend.app.tools.mock_tools import register_mock_tools
from backend.app.tools.registry import ToolRegistry

from tests.test_tool_chat_service import (  # noqa: E402
    ScriptedLLMClient,
    _tool_call_llm_response,
)
from tests.test_tool_chat_service_characterization import (  # noqa: E402
    _CountingRegistry,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVICE_MODULE = "backend/app/services/tool_chat_service.py"
_EXECUTION_MODULE = "backend/app/services/tool_execution_service.py"


# ============================================================
# 测试替身
# ============================================================

class _RecordingExecution(ToolExecutionService):
    """真实执行边界 + 调用记录（行为完全委托，仅记录）。

    Phase 3.11 Step 13：额外记录 Execution Context（``contexts``），
    ``calls`` 仍保持 ``(tool_name, arguments)`` 形态（既有断言不变）。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.contexts: list[Any] = []

    async def execute(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        context: Any = None,
    ) -> ToolResult:
        self.calls.append((tool_name, arguments))
        self.contexts.append(context)
        return await super().execute(
            tool_name, arguments=arguments, context=context
        )


class _StubExecution:
    """Fake 执行边界：不接触 Registry，返回预设 ToolResult。

    用于证明「ToolChatService 只依赖执行边界」：
    即使 Registry 完全没有被调用，链路也照常完成。
    （无 ``registry`` 属性 → 注入校验按 duck-typing 放行）
    """

    def __init__(self, result: ToolResult) -> None:
        self._result = result
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.contexts: list[Any] = []

    async def execute(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        context: Any = None,
    ) -> ToolResult:
        self.calls.append((tool_name, arguments))
        self.contexts.append(context)
        return self._result


def _registry_with_mock_tools() -> ToolRegistry:
    registry = ToolRegistry()
    register_mock_tools(registry)
    return registry


# ============================================================
# Test 1：ToolChatService 使用 ExecutionService
# ============================================================

class Test1ServiceUsesExecutionBoundary:
    async def test_execution_service_receives_tool_call(self) -> None:
        registry = _registry_with_mock_tools()
        execution = _RecordingExecution(registry=registry)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"}),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        result = await service.chat("查询库存", registry=registry)

        assert execution.calls == [("get_inventory", {"material_code": "MAT001"})]
        assert result.tool_calls[0].tool_name == "get_inventory"
        assert result.answer == "最终回答"

    async def test_service_never_calls_registry_execute_directly(self) -> None:
        """注入的 Fake 边界不接触 Registry：链路仍完成，且
        ``registry.execute`` 0 次调用（证明 Service 只依赖执行边界）。"""
        counting = _CountingRegistry(_registry_with_mock_tools())
        stub = _StubExecution(
            ToolResult(
                tool_name="get_inventory",
                success=True,
                data={"material_code": "MAT001", "qty": 1000},
            )
        )
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"}),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=stub)

        result = await service.chat("查询库存", registry=counting)

        assert counting.calls == []                      # Registry.execute 未被直接调用
        assert stub.calls == [("get_inventory", {"material_code": "MAT001"})]
        assert result.answer == "最终回答"

    def test_source_has_no_registry_execute_call(self) -> None:
        """静态：Service 源码的执行调用点只有 execution.execute(...)。"""
        with open(
            os.path.join(REPO_ROOT, *(_SERVICE_MODULE.split("/"))),
            encoding="utf-8",
        ) as fh:
            source = fh.read()
        assert "registry.execute(" not in source
        assert "execution.execute(" in source

    async def test_injected_service_is_reused_across_calls(self) -> None:
        """两次 chat 都经**同一个**注入实例（不是每次调用新建边界）。"""
        registry = _registry_with_mock_tools()
        execution = _RecordingExecution(registry=registry)
        service = ToolChatService(
            llm_client=ScriptedLLMClient([
                # chat#1：tool call → answer
                _tool_call_llm_response("get_inventory", {"material_code": "A1"}),
                "answer-1",
                # chat#2：tool call → answer
                _tool_call_llm_response("get_inventory", {"material_code": "A2"}),
                "answer-2",
            ]),
            execution_service=execution,
        )
        assert service.execution_service is execution

        first = await service.chat("q1", registry=registry)
        second = await service.chat("q2", registry=registry)

        assert first.answer == "answer-1"
        assert second.answer == "answer-2"
        assert execution.calls == [
            ("get_inventory", {"material_code": "A1"}),
            ("get_inventory", {"material_code": "A2"}),
        ]

    async def test_without_injection_boundary_is_built_per_chat(self) -> None:
        """未注入 → 按本次 registry 构造边界（旧调用形态保持可用）。"""
        registry = _registry_with_mock_tools()
        service = ToolChatService(
            llm_client=ScriptedLLMClient([
                _tool_call_llm_response("get_work_order", {"work_order_no": "WO1"}),
                "answer",
            ])
        )
        assert service.execution_service is None

        result = await service.chat("查询工单", registry=registry)

        assert result.tool_calls[0].tool_name == "get_work_order"

    async def test_registry_mismatch_rejected_before_llm_call(self) -> None:
        """注入边界的 registry 与传入 registry 不一致 → ValueError（fail fast）。"""
        llm = ScriptedLLMClient(["answer"])
        service = ToolChatService(
            llm_client=llm,
            execution_service=_RecordingExecution(
                registry=_registry_with_mock_tools()
            ),
        )

        with pytest.raises(ValueError):
            await service.chat("q", registry=_registry_with_mock_tools())

        assert llm.calls == []  # 未发起 LLM 调用

    def test_invalid_execution_service_rejected(self) -> None:
        with pytest.raises(TypeError):
            ToolChatService(
                llm_client=ScriptedLLMClient([]),
                execution_service=object(),  # type: ignore[arg-type]
            )


# ============================================================
# Test 2：arguments 原样转发
# ============================================================

class Test2ArgumentsForwardedVerbatim:
    async def test_dual_arguments_unchanged(self) -> None:
        registry = _registry_with_mock_tools()
        execution = _RecordingExecution(registry=registry)
        arguments = {"material_code": "MAT-001", "warehouse_code": "A01"}
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", arguments),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        await service.chat("查询库存", registry=registry)

        tool_name, received = execution.calls[0]
        assert tool_name == "get_inventory"
        assert received == arguments
        assert received is arguments          # 同一对象：不复制 / 不重建
        assert set(received or {}) == {"material_code", "warehouse_code"}

    async def test_empty_arguments_not_filled(self) -> None:
        registry = _registry_with_mock_tools()
        execution = _RecordingExecution(registry=registry)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {}),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        # 空 arguments → Registry 校验失败（不补默认值）→ 链路继续
        await service.chat("查询库存", registry=registry)

        assert execution.calls == [("get_inventory", {})]


# ============================================================
# Test 3：ToolResult 原样进入 role=tool 消息
# ============================================================

class Test3ToolResultReturnedVerbatim:
    async def test_success_result_serialized_as_before(self) -> None:
        result = ToolResult(
            tool_name="get_inventory",
            success=True,
            data={"material_code": "MAT001", "qty": 1000},
        )
        stub = _StubExecution(result)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"}),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=stub)

        response = await service.chat(
            "查询库存", registry=_registry_with_mock_tools()
        )

        tool_message = llm.calls[1]["messages"][-1]
        assert tool_message["role"] == "tool"
        assert tool_message["tool_call_id"] == "call_001"
        assert tool_message["content"] == _serialize_tool_result(result)
        assert json.loads(tool_message["content"]) == {
            "success": True,
            "data": {"material_code": "MAT001", "qty": 1000},
        }
        assert response.tool_calls[0].tool_name == "get_inventory"


# ============================================================
# Test 4：Tool failure continuation
# ============================================================

class Test4ToolFailureContinuation:
    async def test_failure_result_does_not_break_loop(self) -> None:
        failure = ToolResult(
            tool_name="get_inventory",
            success=False,
            error="参数校验失败: missing required field(s): ['material_code']",
        )
        stub = _StubExecution(failure)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {}),
            "工具失败，已改用其它方式回答。",
        ])
        service = ToolChatService(llm_client=llm, execution_service=stub)

        response = await service.chat(
            "查询库存", registry=_registry_with_mock_tools()
        )

        assert len(llm.calls) == 2                       # LLM 继续（未中断）
        assert response.answer == "工具失败，已改用其它方式回答。"
        assert response.tool_calls[0].tool_name == "get_inventory"
        payload = json.loads(llm.calls[1]["messages"][-1]["content"])
        assert payload["success"] is False
        assert "missing required" in payload["error"]


# ============================================================
# Test 5：Multi-Step
# ============================================================

class Test5MultiStep:
    async def test_two_tools_executed_in_order(self) -> None:
        registry = _registry_with_mock_tools()
        execution = _RecordingExecution(registry=registry)
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "MAT001"}, call_id="c1"
            ),
            _tool_call_llm_response(
                "get_work_order", {"work_order_no": "WO1"}, call_id="c2"
            ),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        response = await service.chat("两步查询", registry=registry)

        assert [name for name, _ in execution.calls] == [
            "get_inventory", "get_work_order",
        ]
        assert len(execution.calls) == 2
        assert [c.tool_name for c in response.tool_calls] == [
            "get_inventory", "get_work_order",
        ]
        assert len(llm.calls) == 3

    def test_execution_boundary_has_no_loop(self) -> None:
        """执行边界保持 ONE Tool：无 while / for（loop 只属于 ToolChatService）。"""
        with open(
            os.path.join(REPO_ROOT, *(_EXECUTION_MODULE.split("/"))),
            encoding="utf-8",
        ) as fh:
            tree = ast.parse(fh.read())
        loops = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.While, ast.For, ast.AsyncFor))
        ]
        assert loops == []
        # 也不依赖 LLM / 编排层
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        assert "backend.app.llm.client" not in imports
        assert "backend.app.services.tool_chat_service" not in imports

    def test_orchestration_stays_in_tool_chat_service(self) -> None:
        source = inspect.getsource(ToolChatService)
        assert "while True" in source
        assert "ToolCallingBudgetExceededError" in source


# ============================================================
# Test 6：Budget
# ============================================================

class Test6Budget:
    async def test_budget_exhausted_semantics_unchanged(self) -> None:
        registry = _registry_with_mock_tools()
        execution = _RecordingExecution(registry=registry)
        # 脚本耗尽后重复最后一项 → 永远请求 Tool
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"})
        ])
        service = ToolChatService(
            llm_client=llm, max_tool_rounds=2, execution_service=execution
        )

        with pytest.raises(ToolCallingBudgetExceededError) as excinfo:
            await service.chat("查询库存", registry=registry)

        assert excinfo.value.max_rounds == 2
        assert excinfo.value.requested_tool == "get_inventory"
        assert len(execution.calls) == 2   # 2 次执行后拒绝（先于执行）
        assert len(llm.calls) == 3         # 3 次 LLM，无第 4 次


# ============================================================
# Test 7：Multiple Tool Calls
# ============================================================

class Test7MultipleToolCalls:
    async def test_multiple_calls_rejected_without_execution(self) -> None:
        registry = _registry_with_mock_tools()
        execution = _RecordingExecution(registry=registry)
        llm = ScriptedLLMClient([
            LLMResponse(
                content=None,
                tool_calls=(
                    ToolCall(
                        id="c1", name="get_inventory",
                        arguments={"material_code": "MAT001"},
                    ),
                    ToolCall(
                        id="c2", name="get_work_order",
                        arguments={"work_order_no": "WO1"},
                    ),
                ),
            ),
        ])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        with pytest.raises(MultipleToolCallsError) as excinfo:
            await service.chat("同时查两个", registry=registry)

        assert excinfo.value.count == 2
        assert execution.calls == []   # 0 次执行
        assert len(llm.calls) == 1


# ============================================================
# Test 8：Malformed ToolCall
# ============================================================

class Test8MalformedToolCall:
    async def test_format_error_propagates_without_execution(self) -> None:
        registry = _registry_with_mock_tools()
        execution = _RecordingExecution(registry=registry)
        llm = ScriptedLLMClient([
            LLMToolCallFormatError("tool_call 'c1' arguments 不是合法 JSON"),
        ])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        with pytest.raises(LLMToolCallFormatError):
            await service.chat("查询库存", registry=registry)

        assert execution.calls == []   # 0 次执行
        assert len(llm.calls) == 1     # 0 次重试


# ============================================================
# API 装配：module-level 单例注入同一执行边界
# ============================================================

class TestApiWiringInjectsBoundary:
    def test_api_module_singletons_share_one_boundary(self) -> None:
        from backend.app.api import tool_chat as api_module

        assert isinstance(
            api_module._tool_execution_service, ToolExecutionService
        )
        assert api_module._tool_execution_service.registry is (
            api_module._tool_registry
        )
        assert api_module._tool_chat_service.execution_service is (
            api_module._tool_execution_service
        )

    def test_http_path_goes_through_injected_boundary(self, monkeypatch) -> None:
        """HTTP 级：注入记录型边界 + Scripted LLM → 端点 200，
        执行边界收到调用（且 registry 与边界一致，不触发不一致校验）。

        Phase 3.11 Step 11：生产 Registry 为真实只读 get_inventory（会访问
        PostgreSQL）→ 本用例注入 Mock Registry（只验证执行边界接线）。
        """
        from backend.app.api import tool_chat as api_module
        from backend.app.main import app

        monkeypatch.setattr(
            api_module, "_tool_registry", _registry_with_mock_tools()
        )
        execution = _RecordingExecution(registry=api_module._tool_registry)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"}),
            "MAT001 当前库存为 1000 PCS。",
        ])
        monkeypatch.setattr(
            api_module,
            "_tool_chat_service",
            ToolChatService(llm_client=llm, execution_service=execution),
        )

        with TestClient(app) as client:
            response = client.post(
                "/api/chat/with-tools", json={"message": "查询 MAT001 的库存"}
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"] == "MAT001 当前库存为 1000 PCS。"
        assert payload["tool_calls"] == [{"tool_name": "get_inventory"}]
        assert execution.calls == [("get_inventory", {"material_code": "MAT001"})]
