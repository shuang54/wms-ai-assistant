"""ToolChatService Capability 测试（Phase 3.11 Step 10）。

验证链路 B 的项目级 Tool 授权：

    LLM → ToolChatService → ToolExecutionService → Capability Check → ToolRegistry → Tool

覆盖（§十六 Test 1–4）：

```text
Test 1  允许 Tool（get_inventory）→ 正常执行（Handler 1 次）
Test 2  禁止 Tool（get_work_order）→ AIOrchestratorCapabilityError
        + Handler 0 次 + Registry 0 次 + LLM 不继续
Test 3  未知 Tool → 无 fallback / 无 retry / 无 guess
        （未列入白名单 → capability 拒绝；已列入白名单 → Registry 未注册）
Test 4  ToolChatService 不含任何 Capability 判断（AST / 代码层静态断言）
```

关键语义（§十二 / §十三）：capability 拒绝是 **authorization failure**，
不是 tool failure —— 异常原样上抛，**不**降级为
``ToolResult(success=False)``，也**不**让 LLM 继续下一轮。

复用既有测试资产：``tests/test_tool_chat_service.py`` 的 ScriptedLLMClient /
_tool_call_llm_response、``tests/test_tool_chat_service_characterization.py``
的 _CountingRegistry / _RecordingHandler。

0 DB / 0 Network / 0 真实 LLM。
"""
from __future__ import annotations

import ast
import json
import os

import pytest

from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorCapabilityError,
)
from backend.app.services.tool_chat_service import (
    ToolCallingBudgetExceededError,
    ToolChatService,
)
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.mock_tools import (
    GET_INVENTORY_DEFINITION,
    GET_WORK_ORDER_DEFINITION,
)
from backend.app.tools.registry import ToolRegistry

from tests.test_tool_chat_service import (  # noqa: E402
    ScriptedLLMClient,
    _tool_call_llm_response,
)
from tests.test_tool_chat_service_characterization import (  # noqa: E402
    _CountingRegistry,
    _RecordingHandler,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVICE_MODULE = "backend/app/services/tool_chat_service.py"
_EXECUTION_MODULE = "backend/app/services/tool_execution_service.py"

PROJECT_ID = "project-a"

#: 只允许 get_inventory（get_work_order 被禁用）
CAPS_INVENTORY_ONLY = ProjectCapabilities(
    tool_names=("get_inventory",),
    knowledge_enabled=True,
    text_to_sql_enabled=True,
)

#: 项目允许任何注册 Tool 之外 —— 用于「白名单含未知 Tool 名」场景
CAPS_UNKNOWN_TOOL = ProjectCapabilities(
    tool_names=("unknown_tool",),
)


# ============================================================
# 测试替身
# ============================================================

def _registry_with_recording_handlers() -> tuple[
    ToolRegistry, _RecordingHandler, _RecordingHandler
]:
    """注册两个 Mock Tool 定义 + 记录型 Handler（0 DB）。"""
    inventory = _RecordingHandler()
    work_order = _RecordingHandler()
    registry = ToolRegistry()
    registry.register(GET_INVENTORY_DEFINITION, inventory)
    registry.register(GET_WORK_ORDER_DEFINITION, work_order)
    return registry, inventory, work_order


def _scoped_service(
    registry: ToolRegistry,
    llm: ScriptedLLMClient,
    *,
    capabilities: ProjectCapabilities,
    project_id: str | None = PROJECT_ID,
) -> ToolChatService:
    """构造项目级执行边界 + 注入 ToolChatService（等价 API 装配）。"""
    execution = ToolExecutionService(
        registry=registry,
        capabilities=capabilities,
        project_id=project_id,
    )
    return ToolChatService(llm_client=llm, execution_service=execution)


# ============================================================
# Test 1：允许 Tool → 正常执行
# ============================================================

class Test1AllowedToolExecutes:
    async def test_allowed_tool_reaches_handler(self) -> None:
        registry, inventory, work_order = _registry_with_recording_handlers()
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"}),
            "MAT001 库存已查询。",
        ])
        service = _scoped_service(
            registry, llm, capabilities=CAPS_INVENTORY_ONLY
        )

        result = await service.chat("查询库存", registry=registry)

        assert result.answer == "MAT001 库存已查询。"
        assert [c.tool_name for c in result.tool_calls] == ["get_inventory"]
        assert len(inventory.calls) == 1
        assert work_order.calls == []

    async def test_allowed_tool_result_flows_back_to_llm(self) -> None:
        registry, inventory, _wo = _registry_with_recording_handlers()
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"}),
            "最终回答",
        ])
        service = _scoped_service(
            registry, llm, capabilities=CAPS_INVENTORY_ONLY
        )

        await service.chat("查询库存", registry=registry)

        tool_message = llm.calls[1]["messages"][-1]
        assert tool_message["role"] == "tool"
        assert json.loads(tool_message["content"]) == {
            "success": True, "data": {"ok": True},
        }


# ============================================================
# Test 2：禁止 Tool → capability 拒绝（Handler 0 次）
# ============================================================

class Test2DeniedToolRejected:
    async def test_denied_tool_raises_capability_error(self) -> None:
        registry, inventory, work_order = _registry_with_recording_handlers()
        counting = _CountingRegistry(registry)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_work_order", {"work_order_no": "WO1"}),
            "（不应到达这里）",
        ])
        service = _scoped_service(
            counting, llm, capabilities=CAPS_INVENTORY_ONLY
        )

        with pytest.raises(AIOrchestratorCapabilityError) as excinfo:
            await service.chat("查询工单", registry=counting)

        # 既有统一 capability 异常（未新建第二种）；携带 Tool 名与 project_id
        assert excinfo.value.capability == "get_work_order"
        assert excinfo.value.project_id == PROJECT_ID
        # Handler 0 次 / Registry.execute 0 次（边界前置校验）
        assert work_order.calls == []
        assert inventory.calls == []
        assert counting.calls == []
        # 授权失败不继续下一轮 LLM（不降级为 ToolResult → 不给 LLM 机会继续）
        assert len(llm.calls) == 1

    async def test_denied_tool_does_not_call_registry(self) -> None:
        """拒绝发生在 Registry 之前（Agent / Handler 都拿不到请求）。"""
        registry, _inv, _wo = _registry_with_recording_handlers()
        counting = _CountingRegistry(registry)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_work_order", {"work_order_no": "WO1"}),
        ])
        service = _scoped_service(
            counting, llm, capabilities=CAPS_INVENTORY_ONLY
        )

        with pytest.raises(AIOrchestratorCapabilityError):
            await service.chat("查询工单", registry=counting)

        assert counting.calls == []

    async def test_capability_rejection_is_not_a_tool_result(self) -> None:
        """授权失败不是 tool failure：不产生 ToolResult(success=False)。"""
        registry, _inv, _wo = _registry_with_recording_handlers()
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_work_order", {"work_order_no": "WO1"}),
            "（不应到达这里）",
        ])
        service = _scoped_service(
            registry, llm, capabilities=CAPS_INVENTORY_ONLY
        )

        with pytest.raises(AIOrchestratorCapabilityError):
            await service.chat("查询工单", registry=registry)

        assert len(llm.calls) == 1   # 没有第二轮（未把拒绝当普通 Tool 失败）


# ============================================================
# Test 3：未知 Tool → 无 fallback / retry / guess
# ============================================================

class Test3UnknownToolNoFallback:
    async def test_unknown_tool_not_whitelisted_denied_by_capability(
        self,
    ) -> None:
        registry, inventory, work_order = _registry_with_recording_handlers()
        llm = ScriptedLLMClient([
            _tool_call_llm_response("unknown_tool", {}),
            "（不应到达这里）",
        ])
        service = _scoped_service(
            registry, llm, capabilities=CAPS_INVENTORY_ONLY
        )

        with pytest.raises(AIOrchestratorCapabilityError) as excinfo:
            await service.chat("随便问问", registry=registry)

        assert excinfo.value.capability == "unknown_tool"
        assert inventory.calls == [] and work_order.calls == []
        assert len(llm.calls) == 1        # 无重试 / 无重选 / 无 fallback

    async def test_whitelisted_but_unregistered_tool_fails_safely(
        self,
    ) -> None:
        """白名单含该名（能力通过）但 Registry 未注册 → ToolResult(False)，
        LLM 继续（既有 Registry 语义；不猜、不 fallback 到其它 Tool）。"""
        registry, inventory, work_order = _registry_with_recording_handlers()
        llm = ScriptedLLMClient([
            _tool_call_llm_response("unknown_tool", {}),
            "该工具不可用，已用其它方式回答。",
        ])
        service = _scoped_service(
            registry, llm, capabilities=CAPS_UNKNOWN_TOOL
        )

        result = await service.chat("随便问问", registry=registry)

        assert [c.tool_name for c in result.tool_calls] == ["unknown_tool"]
        assert result.answer == "该工具不可用，已用其它方式回答。"
        assert inventory.calls == [] and work_order.calls == []
        payload = json.loads(llm.calls[1]["messages"][-1]["content"])
        assert payload["success"] is False
        assert "未注册" in payload["error"]


# ============================================================
# Test 4：ToolChatService 不含 Capability 判断（静态）
# ============================================================

class Test4ServiceHasNoCapabilityLogic:
    def _service_tree(self) -> ast.Module:
        path = os.path.join(REPO_ROOT, *(_SERVICE_MODULE.split("/")))
        with open(path, encoding="utf-8") as fh:
            return ast.parse(fh.read())

    def _code_layer(self, tree: ast.Module) -> str:
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(
                node,
                (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                body = getattr(node, "body", [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstrings.add(id(body[0].value))

        parts: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                parts.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                parts.append(node.module)
                parts.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.Name):
                parts.append(node.id)
            elif isinstance(node, ast.Attribute):
                parts.append(node.attr)
            elif isinstance(node, ast.keyword) and node.arg:
                parts.append(node.arg)
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                parts.append(node.value)
        return " ".join(parts).lower()

    def test_no_capability_identifiers_or_keywords(self) -> None:
        haystack = self._code_layer(self._service_tree())
        for forbidden in (
            "capabilit", "allowstool", "projectcapabilities",
            "check_capability", "whitelist",
        ):
            assert forbidden not in haystack, forbidden

    def test_no_tool_name_branches(self) -> None:
        """无 `if tool_name == ...` / `if tool_name in ...` 形态的分支。"""
        tree = self._service_tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                condition = ast.unparse(node.test)
                assert "tool_name" not in condition, condition
                assert "call.name" not in condition, condition

    def test_no_capability_parameter(self) -> None:
        import inspect

        for sig in (
            inspect.signature(ToolChatService.__init__),
            inspect.signature(ToolChatService.chat),
            inspect.signature(ToolChatService._resolve_execution_service),
        ):
            assert not any(
                "capabilit" in name for name in sig.parameters
            ), sig

    def test_enforcement_lives_in_execution_service(self) -> None:
        """对照：唯一 Enforcement Point = ToolExecutionService。"""
        path = os.path.join(REPO_ROOT, *(_EXECUTION_MODULE.split("/")))
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        assert "_check_capability" in source
        assert "allows_tool" in source
        assert "AIOrchestratorCapabilityError" in source


# ============================================================
# Test 5：Multi-Step + 预算在 capability 之下保持不变（§二十 / §二十一）
# ============================================================

CAPS_BOTH = ProjectCapabilities(
    tool_names=("get_inventory", "get_work_order"),
)


class Test5MultiStepAndBudgetUnderCapability:
    async def test_two_allowed_tools_executed_sequentially(self) -> None:
        """Tool A → Tool B → Final：每次都是 ONE Tool execution
        （loop 仍在 ToolChatService，执行边界不被改造成 Multi-Step Engine）。"""
        registry, inventory, work_order = _registry_with_recording_handlers()
        execution = ToolExecutionService(
            registry=registry,
            capabilities=CAPS_BOTH,
            project_id=PROJECT_ID,
        )
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "MAT001"}, call_id="c1"
            ),
            _tool_call_llm_response(
                "get_work_order", {"work_order_no": "WO1"}, call_id="c2"
            ),
            "两步查询完成。",
        ])
        service = ToolChatService(llm_client=llm, execution_service=execution)

        result = await service.chat("先查库存再查工单", registry=registry)

        assert [c.tool_name for c in result.tool_calls] == [
            "get_inventory", "get_work_order",
        ]
        assert len(inventory.calls) == 1     # 每次调一次 Handler（无内部 loop）
        assert len(work_order.calls) == 1
        assert len(llm.calls) == 3

    async def test_budget_exceeded_still_precedes_execution(self) -> None:
        registry, inventory, _wo = _registry_with_recording_handlers()
        execution = ToolExecutionService(
            registry=registry, capabilities=CAPS_BOTH, project_id=PROJECT_ID
        )
        # 脚本耗尽后重复最后一项 → 永远请求 Tool
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"})
        ])
        service = ToolChatService(
            llm_client=llm, max_tool_rounds=1, execution_service=execution
        )

        with pytest.raises(ToolCallingBudgetExceededError) as excinfo:
            await service.chat("查询库存", registry=registry)

        assert excinfo.value.max_rounds == 1
        assert len(inventory.calls) == 1     # 预算内执行 1 次
        assert len(llm.calls) == 2           # 第 2 轮拒绝（先于执行）

    def test_budget_range_unchanged(self) -> None:
        """TOOL_MAX_ROUNDS 仍钳制在 [1, 20]（配置未改动）。"""
        from backend.app.config import (
            TOOL_MAX_ROUNDS_MAX,
            TOOL_MAX_ROUNDS_MIN,
        )

        assert (TOOL_MAX_ROUNDS_MIN, TOOL_MAX_ROUNDS_MAX) == (1, 20)
