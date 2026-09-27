"""ToolChatService Characterization Tests（Phase 3.11 Step 8 — 只补缺口）。

本文件**只**验证 Step 8 勘察所需、而现有测试**未覆盖**的事实；
不新增行为要求，也不修改任何生产代码。

已有覆盖（**不重复**，见 ``tests/test_tool_chat_service.py``）：

```text
no tool needed / single tool call / two & three sequential tools
multiple tool calls rejected / budget(max_rounds) exhausted
invalid arguments → Registry 拒绝 / missing required → Registry 拒绝
unknown tool → 安全失败后继续 / tool 失败后继续
LLM error（round1/2/3）传播 / 输入校验（空消息）/ TOOL_MAX_ROUNDS 设置
tool message 不含敏感信息 / tool_call 元信息只含名称
API 层：/api/chat/with-tools 正常链路、错误映射（400/500/502/503）、OpenAPI
```

本文件补齐的 5 个缺口（全部为 characterization）：

```text
1. LLM 提供的 arguments 原样到达 Handler（不 rename / 不补默认 / 不过滤）
2. unknown field 经 Function Calling 链路 → Registry 仍拒绝（Schema Authority）
3. Capability 校验在该链路**不存在**（任何已注册 Tool 都可执行）—— C5 缺口事实
4. Handler 只收到 arguments（不下发 project context / 无调用方元数据）
5. malformed tool_call（LLMToolCallFormatError）原样传播：0 次执行、0 次重试
```

0 DB / 0 Network / 0 LLM（Scripted Fake）。
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.app.llm.client import LLMToolCallFormatError
from backend.app.services.tool_chat_service import ToolChatService
from backend.app.tools.base import ToolDefinition
from backend.app.tools.mock_tools import register_mock_tools
from backend.app.tools.registry import ToolRegistry

# 复用既有测试资产（与 test_tool_execution_service 复用 FakeRouter 同模式）
from tests.test_tool_chat_service import (  # noqa: E402
    ScriptedLLMClient,
    _tool_call_llm_response,
)


# ============================================================
# 测试替身（记录型；不改变生产行为）
# ============================================================

class _RecordingHandler:
    """记录到达 Handler 的入参（含原始对象身份）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(arguments)
        return {"ok": True}


class _SignatureRecordingHandler:
    """记录 Handler 的调用签名（位置参数 / 关键字参数）。"""

    def __init__(self) -> None:
        self.signatures: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.signatures.append((args, kwargs))
        return {"ok": True}


class _CountingRegistry:
    """包装真实 ToolRegistry：记录 execute 调用（行为完全委托）。"""

    def __init__(self, inner: ToolRegistry) -> None:
        self._inner = inner
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_definitions(self):
        return self._inner.list_definitions()

    async def execute(self, tool_name, arguments=None):
        self.calls.append((tool_name, dict(arguments or {})))
        return await self._inner.execute(tool_name, arguments)


def _registry_with(definition: ToolDefinition, handler: Any) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(definition, handler)
    return registry


def _mock_inventory_definition() -> ToolDefinition:
    registry = ToolRegistry()
    register_mock_tools(registry)
    return registry.get_definition("get_inventory")


# ============================================================
# 缺口 1：arguments 原样到达 Handler
# ============================================================

class TestArgumentsReachHandlerVerbatim:
    """LLM（function calling）给出的 arguments 逐字进入 Handler：
    Service / Registry 都不 rename / 不补默认 / 不过滤。"""

    async def test_dual_arguments_unchanged(self) -> None:
        handler = _RecordingHandler()
        registry = _registry_with(_mock_inventory_definition(), handler)
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory",
                {"material_code": "MAT001", "warehouse_code": "A01"},
            ),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm)

        result = await service.chat("查询库存", registry=registry)

        assert result.tool_calls[0].tool_name == "get_inventory"
        assert len(handler.calls) == 1
        assert handler.calls[0] == {
            "material_code": "MAT001", "warehouse_code": "A01",
        }
        assert set(handler.calls[0]) == {"material_code", "warehouse_code"}

    async def test_missing_optional_not_filled(self) -> None:
        """LLM 未给可选参数 → Handler 收到的 dict 不额外补 key。"""
        handler = _RecordingHandler()
        registry = _registry_with(_mock_inventory_definition(), handler)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"}),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm)

        await service.chat("查询库存", registry=registry)

        assert handler.calls[0] == {"material_code": "MAT001"}
        assert "warehouse_code" not in handler.calls[0]


# ============================================================
# 缺口 2：unknown field → Registry 仍拒绝（Schema Authority 不变）
# ============================================================

class TestUnknownFieldRejectedByRegistry:
    """LLM 幻觉出的额外字段不会到达 Handler：Registry 是唯一 Schema 校验入口，
    失败归一为 ToolResult(success=False) → role=tool message → 链路继续。"""

    async def test_unknown_field_rejected_and_loop_continues(self) -> None:
        handler = _RecordingHandler()
        registry = _registry_with(_mock_inventory_definition(), handler)
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory",
                {"material_code": "MAT001", "batch": "B-001"},
            ),
            "参数不被支持，已改用其他方式回答。",
        ])
        service = ToolChatService(llm_client=llm)

        result = await service.chat("查询库存", registry=registry)

        assert handler.calls == []                     # Schema 校验先于 Handler
        assert result.tool_calls[0].tool_name == "get_inventory"
        assert result.answer == "参数不被支持，已改用其他方式回答。"
        tool_message = llm.calls[1]["messages"][-1]
        assert tool_message["role"] == "tool"
        assert '"success": false' in tool_message["content"]
        assert "unknown field" in tool_message["content"]


# ============================================================
# 缺口 3：Capability 校验不存在（C5 缺口事实；本 Step 不修复）
# ============================================================

class TestCapabilityGateAbsent:
    """链路 B 无 ProjectCapabilities 概念：只要 Tool 注册进传入的 Registry，
    LLM 就可以请求执行（与主链路的 capability 硬校验不同）。

    CURRENT GAP（Step 8 勘察结论）：接入真实 Tool 前必须先决定
    project scope / capability 策略（见 evaluation 文档 §7）。
    """

    async def test_any_registered_tool_executes_without_capability_config(
        self,
    ) -> None:
        probe = ToolDefinition(
            name="custom_probe",
            description="勘察专用 Tool（Step 8 记录 capability 缺口，不是正式 Tool）",
            parameters={
                "type": "object",
                "properties": {"note": {"type": "string"}},
                "required": [],
            },
        )
        handler = _RecordingHandler()
        registry = _registry_with(probe, handler)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("custom_probe", {"note": "n"}),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm)

        result = await service.chat("任意问题", registry=registry)

        assert result.tool_calls[0].tool_name == "custom_probe"
        assert handler.calls == [{"note": "n"}]  # 无 capability 拦截

    async def test_all_registered_tools_are_exposed_to_llm(self) -> None:
        """tools schema = 传入 Registry 的全部定义（无项目白名单过滤）。"""
        registry = ToolRegistry()
        register_mock_tools(registry)
        llm = ScriptedLLMClient(["直接回答"])
        service = ToolChatService(llm_client=llm)

        await service.chat("任意问题", registry=registry)

        exposed = [t["function"]["name"] for t in llm.calls[0]["tools"]]
        assert exposed == ["get_inventory", "get_work_order"]


# ============================================================
# 缺口 4：Handler 只收到 arguments（无 project context）
# ============================================================

class TestHandlerReceivesOnlyArguments:
    """ToolChatService / Registry 不给 Handler 传 project context、
    project_id、调用方元数据或 tool_call id —— 只有 arguments。"""

    async def test_handler_call_signature(self) -> None:
        handler = _SignatureRecordingHandler()
        registry = _registry_with(_mock_inventory_definition(), handler)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "MAT001"}),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm)

        await service.chat("查询库存", registry=registry)

        assert len(handler.signatures) == 1
        args, kwargs = handler.signatures[0]
        assert kwargs == {}
        assert len(args) == 1                      # 唯一入参 = arguments
        assert args[0] == {"material_code": "MAT001"}

    async def test_tool_call_id_not_forwarded_to_handler(self) -> None:
        """tool_call id 仅用于回传 LLM 的协议，不下发给 Handler。"""
        handler = _SignatureRecordingHandler()
        registry = _registry_with(_mock_inventory_definition(), handler)
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "MAT001"}, call_id="call_xyz"
            ),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm)

        await service.chat("查询库存", registry=registry)

        args, _ = handler.signatures[0]
        assert "call_xyz" not in str(args)
        assert "tool_call_id" not in args[0]


# ============================================================
# 缺口 5：malformed tool_call 原样传播（0 执行 / 0 重试）
# ============================================================

class TestMalformedToolCallPropagates:
    """LLM Client 解析 tool_calls 失败（非法 JSON / 缺字段）→
    ``LLMToolCallFormatError`` 原样上抛：不吞、不重试、不执行 Tool
    （API 层映射 502，见 tests/test_tool_chat_api.py::TestErrorMapping）。"""

    async def test_format_error_propagates_without_execution(self) -> None:
        registry = ToolRegistry()
        register_mock_tools(registry)
        counting = _CountingRegistry(registry)
        llm = ScriptedLLMClient([
            LLMToolCallFormatError("tool_call 'call_001' arguments 不是合法 JSON"),
        ])
        service = ToolChatService(llm_client=llm)

        with pytest.raises(LLMToolCallFormatError):
            await service.chat("查询库存", registry=counting)

        assert counting.calls == []      # 0 次 Tool 执行
        assert len(llm.calls) == 1       # 0 次重试 / 0 次续轮
