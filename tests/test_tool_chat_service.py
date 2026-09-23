"""ToolChatService 单元测试（Phase 3.6.2）。

使用 ScriptedLLMClient（按脚本返回 LLMResponse / str / 异常）+ 真实
ToolRegistry + Mock Tools，覆盖任务书 §十四 的五个场景：

    场景 1：无需 Tool（1 轮 LLM / 0 次 Tool）
    场景 2：库存查询（2 轮 LLM / 1 次 Tool）
    场景 3：工单查询（get_work_order）
    场景 4：Tool 参数错误（ToolResult 失败 → tool message → LLM #2）
    场景 5：未知 Tool（安全失败）

附加覆盖：多 tool call 拒绝、LLM 轮数上限、空 registry、
LLM 异常透传、tool message 协议结构、安全（不泄露敏感信息）。
"""
from __future__ import annotations

import json

import pytest

from backend.app.llm.client import (
    LLMRequestError,
    LLMResponse,
    ToolCall,
)
from backend.app.services.tool_chat_service import (
    MultipleToolCallsError,
    ToolChatResponse,
    ToolChatService,
)
from backend.app.tools.mock_tools import register_mock_tools
from backend.app.tools.registry import ToolRegistry


# ============================================================
# Scripted Fake LLM
# ============================================================

class ScriptedLLMClient:
    """按脚本依次返回响应的 Mock LLMClient（记录每次调用）。

    脚本项可以是：
        - LLMResponse / str → 作为返回值
        - Exception          → 抛出
    脚本耗尽后重复最后一项（模拟 LLM 死循环返回 tool call 的场景）。
    """

    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    async def chat(self, messages, *, tools=None):
        idx = min(len(self.calls), len(self._script) - 1)
        self.calls.append(
            {"messages": [dict(m) for m in messages], "tools": tools}
        )
        item = self._script[idx]
        if isinstance(item, Exception):
            raise item
        return item


def _tool_call_llm_response(
    name: str = "get_inventory",
    arguments: dict | None = None,
    call_id: str = "call_001",
) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=(
            ToolCall(
                id=call_id,
                name=name,
                arguments=arguments if arguments is not None else {},
            ),
        ),
    )


@pytest.fixture()
def registry() -> ToolRegistry:
    r = ToolRegistry()
    register_mock_tools(r)
    return r


# ============================================================
# 场景 1：无需 Tool
# ============================================================

class TestNoToolNeeded:
    async def test_plain_answer_single_llm_round(self, registry) -> None:
        llm = ScriptedLLMClient(
            [LLMResponse(content="你好！有什么可以帮你？", tool_calls=())]
        )
        service = ToolChatService(llm_client=llm)

        result = await service.chat("你好", registry=registry)

        assert isinstance(result, ToolChatResponse)
        assert result.answer == "你好！有什么可以帮你？"
        assert result.tool_calls == ()
        assert len(llm.calls) == 1  # LLM 恰好 1 次

    async def test_tools_schema_passed_to_llm(self, registry) -> None:
        """LLM #1 必须收到 registry 中全部 Tool Schema（OpenAI 格式）。"""
        llm = ScriptedLLMClient(
            [LLMResponse(content="ok", tool_calls=())]
        )
        service = ToolChatService(llm_client=llm)
        await service.chat("q", registry=registry)

        tools = llm.calls[0]["tools"]
        assert isinstance(tools, list)
        assert [t["function"]["name"] for t in tools] == [
            "get_inventory",
            "get_work_order",
        ]
        for t in tools:
            assert t["type"] == "function"

    async def test_str_return_from_fake_tolerated(self, registry) -> None:
        """防御：tools 路径实现方返回 str（应包装为 LLMResponse 处理）。"""
        llm = ScriptedLLMClient(["直接回答"])
        service = ToolChatService(llm_client=llm)
        result = await service.chat("q", registry=registry)
        assert result.answer == "直接回答"
        assert result.tool_calls == ()


# ============================================================
# 场景 2：库存查询（get_inventory）
# ============================================================

class TestInventoryQuery:
    async def test_single_tool_call_full_flow(self, registry) -> None:
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response(
                    "get_inventory", {"material_code": "MAT001"}
                ),
                "MAT001 当前库存为 1000 PCS。",
            ]
        )
        service = ToolChatService(llm_client=llm)

        result = await service.chat("查询 MAT001 的库存", registry=registry)

        assert result.answer == "MAT001 当前库存为 1000 PCS。"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "get_inventory"
        assert len(llm.calls) == 2  # LLM 恰好 2 次

    async def test_second_llm_call_receives_tool_messages(
        self, registry
    ) -> None:
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response(
                    "get_inventory", {"material_code": "MAT001"}
                ),
                "最终回答",
            ]
        )
        service = ToolChatService(llm_client=llm)
        await service.chat("查询 MAT001 的库存", registry=registry)

        second_messages = llm.calls[1]["messages"]
        # 1 user + 1 assistant(tool_call) + 1 tool
        assert len(second_messages) == 3
        assert second_messages[0]["role"] == "user"

        assistant_msg = second_messages[1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] is None
        raw_calls = assistant_msg["tool_calls"]
        assert len(raw_calls) == 1
        assert raw_calls[0]["id"] == "call_001"
        assert raw_calls[0]["function"]["name"] == "get_inventory"
        assert json.loads(raw_calls[0]["function"]["arguments"]) == {
            "material_code": "MAT001"
        }

        tool_msg = second_messages[2]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_001"
        payload = json.loads(tool_msg["content"])
        assert payload == {
            "success": True,
            "data": {
                "material_code": "MAT001",
                "warehouse_code": None,
                "quantity": 1000,
                "unit": "PCS",
            },
        }

    async def test_second_llm_call_has_no_tools(self, registry) -> None:
        """LLM #2 不携带 tools（结构上保证最多 2 轮 / 1 次 Tool）。"""
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response(
                    "get_inventory", {"material_code": "MAT001"}
                ),
                "最终回答",
            ]
        )
        service = ToolChatService(llm_client=llm)
        await service.chat("q", registry=registry)

        assert llm.calls[0]["tools"] is not None
        assert llm.calls[1]["tools"] is None


# ============================================================
# 场景 3：工单查询（get_work_order）
# ============================================================

class TestWorkOrderQuery:
    async def test_get_work_order_called(self, registry) -> None:
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response(
                    "get_work_order", {"work_order_no": "MO001"}
                ),
                "工单 MO001 当前状态为 RELEASED。",
            ]
        )
        service = ToolChatService(llm_client=llm)

        result = await service.chat("查询工单 MO001", registry=registry)

        assert result.answer == "工单 MO001 当前状态为 RELEASED。"
        assert result.tool_calls[0].tool_name == "get_work_order"
        tool_msg = llm.calls[1]["messages"][2]
        payload = json.loads(tool_msg["content"])
        assert payload["data"] == {
            "work_order_no": "MO001",
            "status": "RELEASED",
        }


# ============================================================
# 场景 4：Tool 参数错误（LLM 传错参数）
# ============================================================

class TestToolValidationError:
    async def test_invalid_arguments_become_tool_error_message(
        self, registry
    ) -> None:
        """LLM 返回 {"material_code": 123} → Tool 拒绝 → LLM #2 生成说明。"""
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response(
                    "get_inventory", {"material_code": 123}
                ),
                "抱歉，查询失败：material_code 必须是字符串。",
            ]
        )
        service = ToolChatService(llm_client=llm)

        result = await service.chat("查询库存", registry=registry)

        # Service 不崩溃，正常走完 2 轮
        assert result.answer == "抱歉，查询失败：material_code 必须是字符串。"
        assert len(llm.calls) == 2
        tool_msg = llm.calls[1]["messages"][2]
        payload = json.loads(tool_msg["content"])
        assert payload["success"] is False
        assert "参数校验失败" in payload["error"]

    async def test_missing_required_field_becomes_tool_error_message(
        self, registry
    ) -> None:
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response("get_inventory", {}),
                "查询失败：缺少物料编码。",
            ]
        )
        service = ToolChatService(llm_client=llm)
        result = await service.chat("查询库存", registry=registry)

        assert len(llm.calls) == 2
        tool_msg = llm.calls[1]["messages"][2]
        payload = json.loads(tool_msg["content"])
        assert payload["success"] is False
        assert "material_code" in payload["error"]


# ============================================================
# 场景 5：未知 Tool
# ============================================================

class TestUnknownTool:
    async def test_unknown_tool_fails_safely(self, registry) -> None:
        """LLM 幻觉出未注册 Tool → ToolResult 失败 → LLM #2 自然语言说明。"""
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response(
                    "get_unknown_tool", {"x": 1}, call_id="call_unk"
                ),
                "抱歉，当前没有可用的工具支持该查询。",
            ]
        )
        service = ToolChatService(llm_client=llm)

        result = await service.chat("查询某个东西", registry=registry)

        assert result.answer == "抱歉，当前没有可用的工具支持该查询。"
        assert len(llm.calls) == 2
        tool_msg = llm.calls[1]["messages"][2]
        payload = json.loads(tool_msg["content"])
        assert payload["success"] is False
        assert "get_unknown_tool" in payload["error"]


# ============================================================
# 多 tool call 拒绝
# ============================================================

class TestMultipleToolCallsRejected:
    async def test_multiple_tool_calls_raise_before_execution(
        self, registry
    ) -> None:
        llm = ScriptedLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=(
                        ToolCall(
                            id="call_a",
                            name="get_inventory",
                            arguments={"material_code": "M1"},
                        ),
                        ToolCall(
                            id="call_b",
                            name="get_work_order",
                            arguments={"work_order_no": "MO1"},
                        ),
                    ),
                )
            ]
        )
        service = ToolChatService(llm_client=llm)

        with pytest.raises(MultipleToolCallsError) as exc_info:
            await service.chat("q", registry=registry)
        assert exc_info.value.count == 2

        # 不执行第二个 LLM 轮次
        assert len(llm.calls) == 1


# ============================================================
# LLM 轮数上限
# ============================================================

class TestLLMRoundLimit:
    async def test_never_exceeds_two_llm_rounds(self, registry) -> None:
        """即使 LLM #2 仍返回 tool call 形态，也绝不发起第三轮。"""
        # 脚本只有一项且是 tool call：两轮都返回同一响应（脚本耗尽重复）
        llm = ScriptedLLMClient(
            [_tool_call_llm_response("get_inventory", {"material_code": "M"})]
        )
        service = ToolChatService(llm_client=llm)

        result = await service.chat("q", registry=registry)

        assert len(llm.calls) == 2  # 硬上限
        # 第二轮返回 LLMResponse（fake 忽略 tools）→ 取 content（None → ""）
        assert result.answer == ""

    async def test_llm_round1_error_propagates(self, registry) -> None:
        llm = ScriptedLLMClient([LLMRequestError("DeepSeek 500")])
        service = ToolChatService(llm_client=llm)

        with pytest.raises(LLMRequestError):
            await service.chat("q", registry=registry)
        assert len(llm.calls) == 1

    async def test_llm_round2_error_propagates(self, registry) -> None:
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response("get_inventory", {"material_code": "M"}),
                LLMRequestError("DeepSeek 500"),
            ]
        )
        service = ToolChatService(llm_client=llm)

        with pytest.raises(LLMRequestError):
            await service.chat("q", registry=registry)
        assert len(llm.calls) == 2


# ============================================================
# 入参与边界
# ============================================================

class TestInputValidation:
    @pytest.mark.parametrize("message", ["", "   ", "\n\t "])
    async def test_blank_message_rejected(self, registry, message) -> None:
        llm = ScriptedLLMClient([])
        service = ToolChatService(llm_client=llm)
        with pytest.raises(ValueError):
            await service.chat(message, registry=registry)
        assert llm.calls == []

    async def test_empty_registry_still_works(self) -> None:
        """空 registry：不传 tools，直接一轮 LLM 回答。"""
        llm = ScriptedLLMClient(["没有可用工具，但可以聊天。"])
        service = ToolChatService(llm_client=llm)
        empty = ToolRegistry()

        result = await service.chat("你好", registry=empty)

        assert result.answer == "没有可用工具，但可以聊天。"
        assert result.tool_calls == ()
        assert len(llm.calls) == 1
        assert llm.calls[0]["tools"] is None


# ============================================================
# 安全
# ============================================================

class TestSecurity:
    async def test_tool_message_content_no_sensitive_info(
        self, registry
    ) -> None:
        """tool message 只含 success / data / error，不含敏感信息。"""
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response(
                    "get_inventory", {"material_code": "MAT001"}
                ),
                "回答",
            ]
        )
        service = ToolChatService(llm_client=llm)
        await service.chat("q", registry=registry)

        tool_msg = llm.calls[1]["messages"][2]
        body = json.dumps(tool_msg, ensure_ascii=False)
        for secret in (
            "sk-",
            "Authorization",
            "Bearer ",
            "postgresql://",
            "DATABASE_URL",
            "SELECT ",
            "Traceback",
            "handler",
        ):
            assert secret not in body

    async def test_tool_message_error_no_traceback(self, registry) -> None:
        """ToolResult 失败信息不含 traceback（registry 已保证）。"""
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response("get_inventory", {}),
                "回答",
            ]
        )
        service = ToolChatService(llm_client=llm)
        await service.chat("q", registry=registry)

        tool_msg = llm.calls[1]["messages"][2]
        body = json.dumps(tool_msg, ensure_ascii=False)
        assert "Traceback" not in body
        assert 'File "' not in body

    def test_tool_chat_call_info_contains_only_tool_name(self) -> None:
        from backend.app.services.tool_chat_service import ToolChatCallInfo

        info = ToolChatCallInfo(tool_name="get_inventory")
        assert set(info.__dataclass_fields__.keys()) == {"tool_name"}

    def test_service_module_has_no_db_dependency(self) -> None:
        """ToolChatService 不直接持有数据库连接（任务书 §二十）。"""
        import inspect

        from backend.app.services import tool_chat_service as module

        source = inspect.getsource(module)
        for banned in (
            "from backend.app.db",
            "import backend.app.db",
            "create_engine",
            "AsyncSession",
        ):
            assert banned not in source


__all__ = [
    "ScriptedLLMClient",
    "TestNoToolNeeded",
    "TestInventoryQuery",
    "TestWorkOrderQuery",
    "TestToolValidationError",
    "TestUnknownTool",
    "TestMultipleToolCallsRejected",
    "TestLLMRoundLimit",
    "TestInputValidation",
    "TestSecurity",
]
