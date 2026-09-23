"""ToolChatService 单元测试（Phase 3.6.2 引入；Phase 3.6.3 扩展 Multi-Step）。

使用 ScriptedLLMClient（按脚本返回 LLMResponse / str / 异常）+ 真实
ToolRegistry + Mock Tools，覆盖 Phase 3.6.3 任务书 §十四 的八个 Case：

    Case 1：无需 Tool（1 轮 LLM / 0 次 Tool）
    Case 2：单个 Tool（2 轮 LLM / 1 次 Tool）
    Case 3：两个顺序 Tool（3 轮 LLM / 2 次 Tool，完整消息历史）
    Case 4：三个顺序 Tool（4 轮 LLM / 3 次 Tool，含测试专用 Tool）
    Case 5：预算耗尽（MAX_TOOL_ROUNDS=2，第 3 个 Tool 绝不执行）
    Case 6：多个 Tool Call 仍然拒绝（多轮 ≠ 并行）
    Case 7：Tool 失败后允许继续下一轮
    Case 8：未知 Tool 不崩溃，可继续

附加覆盖：每轮 tools schema 传递、消息历史不被污染、
LLM 异常透传、tool message 协议结构、安全（不泄露敏感信息）、
配置（TOOL_MAX_ROUNDS 钳制）。
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
    ToolCallingBudgetExceededError,
    ToolChatResponse,
    ToolChatService,
)
from backend.app.tools.base import ToolDefinition
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


# 测试专用 Tool（任务书 §十三：只用于多轮流程测试，不是正式 Tool）

class _TestNoteHandler:
    async def __call__(self, arguments: dict) -> dict:
        return {"note": arguments.get("note", ""), "source": "test"}

_TEST_NOTE_TOOL = ToolDefinition(
    name="get_test_note",
    description="测试专用 Tool（仅用于多轮流程测试，不是正式 Tool）",
    parameters={
        "type": "object",
        "properties": {
            "note": {"type": "string", "description": "备注内容"},
        },
        "required": [],
    },
)


def _register_test_note_tool(r: ToolRegistry) -> None:
    r.register(_TEST_NOTE_TOOL, _TestNoteHandler())


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

    async def test_every_llm_round_carries_tools(self, registry) -> None:
        """多轮链路中每一轮 LLM 调用都携带 tools（LLM 可继续请求 Tool）。"""
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

        assert len(llm.calls) == 2
        for call in llm.calls:
            assert call["tools"] is not None
            assert [t["function"]["name"] for t in call["tools"]] == [
                "get_inventory",
                "get_work_order",
            ]


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
# Case 3：两个顺序 Tool（LLM = 3 / Tool = 2）+ 完整消息历史
# ============================================================

class TestTwoSequentialTools:
    async def test_two_sequential_tools_full_flow(self, registry) -> None:
        """先查库存再查工单：LLM = 3、Tool = 2、tool_calls 按执行顺序。"""
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response(
                    "get_inventory",
                    {"material_code": "MAT001"},
                    call_id="call_001",
                ),
                _tool_call_llm_response(
                    "get_work_order",
                    {"work_order_no": "MO001"},
                    call_id="call_002",
                ),
                "MAT001 库存 1000 PCS；工单 MO001 状态 RELEASED。",
            ]
        )
        service = ToolChatService(llm_client=llm)

        result = await service.chat(
            "先查询 MAT001 库存，然后查询工单 MO001 的状态", registry=registry
        )

        assert result.answer == "MAT001 库存 1000 PCS；工单 MO001 状态 RELEASED。"
        assert [info.tool_name for info in result.tool_calls] == [
            "get_inventory",
            "get_work_order",
        ]
        assert len(llm.calls) == 3  # LLM 恰好 3 次

    async def test_each_llm_round_receives_full_history(
        self, registry
    ) -> None:
        """每一轮 LLM 收到完整累积历史（不是只发最新 ToolResult）。"""
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response(
                    "get_inventory",
                    {"material_code": "MAT001"},
                    call_id="call_001",
                ),
                _tool_call_llm_response(
                    "get_work_order",
                    {"work_order_no": "MO001"},
                    call_id="call_002",
                ),
                "最终回答",
            ]
        )
        service = ToolChatService(llm_client=llm)
        await service.chat("先查库存再查工单", registry=registry)

        # LLM #1：仅 user
        assert len(llm.calls[0]["messages"]) == 1
        assert llm.calls[0]["messages"][0] == {
            "role": "user",
            "content": "先查库存再查工单",
        }

        # LLM #2：user + (assistant tool_call + tool result) × 1 = 3
        m2 = llm.calls[1]["messages"]
        assert [m["role"] for m in m2] == ["user", "assistant", "tool"]
        assert m2[1]["tool_calls"][0]["id"] == "call_001"
        assert m2[2]["tool_call_id"] == "call_001"

        # LLM #3：user + (assistant tool_call + tool result) × 2 = 5
        m3 = llm.calls[2]["messages"]
        assert [m["role"] for m in m3] == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "tool",
        ]
        assert m3[3]["tool_calls"][0]["id"] == "call_002"
        assert m3[4]["tool_call_id"] == "call_002"

    async def test_history_messages_never_mutated(self, registry) -> None:
        """消息历史只增不改：后续轮次的每条历史消息与首轮完全一致。"""
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response(
                    "get_inventory", {"material_code": "MAT001"}
                ),
                _tool_call_llm_response(
                    "get_work_order", {"work_order_no": "MO001"}
                ),
                "最终回答",
            ]
        )
        service = ToolChatService(llm_client=llm)
        await service.chat("先查库存再查工单", registry=registry)

        # 第 3 轮的前 3 条消息 == 第 2 轮的全部消息（严格前缀，未被改写）
        assert llm.calls[2]["messages"][:3] == llm.calls[1]["messages"]
        # user 消息在所有轮次中保持不变
        for call in llm.calls:
            assert call["messages"][0] == {
                "role": "user",
                "content": "先查库存再查工单",
            }


# ============================================================
# Case 4：三个顺序 Tool（LLM = 4 / Tool = 3）
# ============================================================

class TestThreeSequentialTools:
    async def test_three_sequential_tools_all_executed(self) -> None:
        r = ToolRegistry()
        register_mock_tools(r)
        _register_test_note_tool(r)

        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response(
                    "get_inventory",
                    {"material_code": "MAT001"},
                    call_id="call_001",
                ),
                _tool_call_llm_response(
                    "get_work_order",
                    {"work_order_no": "MO001"},
                    call_id="call_002",
                ),
                _tool_call_llm_response(
                    "get_test_note", {"note": "三步验证"}, call_id="call_003"
                ),
                "三步结果汇总完成。",
            ]
        )
        service = ToolChatService(llm_client=llm)

        result = await service.chat(
            "依次执行三步查询", registry=r
        )

        assert result.answer == "三步结果汇总完成。"
        assert [info.tool_name for info in result.tool_calls] == [
            "get_inventory",
            "get_work_order",
            "get_test_note",
        ]
        assert len(llm.calls) == 4  # LLM 恰好 4 次

        # 第 4 轮 LLM 看到 3 组完整的 tool_call + tool 消息对
        m4 = llm.calls[3]["messages"]
        assert [m["role"] for m in m4] == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "tool",
            "assistant",
            "tool",
        ]
        assert m4[6]["tool_call_id"] == "call_003"
        payload = json.loads(m4[6]["content"])
        assert payload == {
            "success": True,
            "data": {"note": "三步验证", "source": "test"},
        }


# ============================================================
# Case 5：预算耗尽（MAX_TOOL_ROUNDS 硬上限）
# ============================================================

class TestBudgetExhausted:
    async def test_budget_two_rounds_third_tool_never_executed(
        self, registry
    ) -> None:
        """max_tool_rounds=2：Tool 1 / Tool 2 执行，Tool 3 绝不执行。"""
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response(
                    "get_inventory",
                    {"material_code": "MAT001"},
                    call_id="call_001",
                ),
                _tool_call_llm_response(
                    "get_work_order",
                    {"work_order_no": "MO001"},
                    call_id="call_002",
                ),
                _tool_call_llm_response(
                    "get_test_note", {"note": "第三个"}, call_id="call_003"
                ),
            ]
        )
        service = ToolChatService(
            llm_client=llm, max_tool_rounds=2
        )

        with pytest.raises(ToolCallingBudgetExceededError) as exc_info:
            await service.chat("q", registry=registry)

        assert exc_info.value.max_rounds == 2
        assert exc_info.value.requested_tool == "get_test_note"

        # Tool 3 未执行：LLM 恰好 3 次（预算 2 → 2 Tool + 1 次拒绝）
        assert len(llm.calls) == 3
        # 第 3 轮只包含前两组消息对（没有 call_003 的 tool result）
        m3 = llm.calls[2]["messages"]
        assert len(m3) == 5
        assert all(
            m.get("tool_call_id") != "call_003" for m in m3
        )

    async def test_default_budget_five_rounds(self, registry) -> None:
        """默认预算（settings.tool.max_rounds=5）：5 Tool 执行 + 第 6 轮拒绝。"""
        llm = ScriptedLLMClient(
            [_tool_call_llm_response("get_inventory", {"material_code": "M"})]
        )
        service = ToolChatService(llm_client=llm)

        assert service.max_tool_rounds == 5

        with pytest.raises(ToolCallingBudgetExceededError):
            await service.chat("q", registry=registry)

        # 最坏情况硬上限：6 次 LLM / 5 次 Tool，绝无第 7 次调用
        assert len(llm.calls) == 6

    async def test_budget_zero_rejected_in_constructor(self) -> None:
        with pytest.raises(ValueError):
            ToolChatService(llm_client=None, max_tool_rounds=0)

    async def test_max_rounds_defaults_to_settings(self) -> None:
        from backend.app.config import settings

        service = ToolChatService(llm_client=ScriptedLLMClient([]))
        assert service.max_tool_rounds == settings.tool.max_rounds


# ============================================================
# Case 7：Tool 失败后允许继续下一轮
# ============================================================

class TestToolFailureContinues:
    async def test_failed_tool_then_next_tool_then_final(
        self, registry
    ) -> None:
        """Tool 失败 ≠ 整个链路失败：LLM 可继续请求下一个 Tool。"""
        llm = ScriptedLLMClient(
            [
                # 第 1 轮：参数类型错误 → ToolResult(success=False)
                _tool_call_llm_response(
                    "get_inventory", {"material_code": 123}, call_id="call_001"
                ),
                # 第 2 轮：LLM 换一个 Tool 继续
                _tool_call_llm_response(
                    "get_work_order",
                    {"work_order_no": "MO001"},
                    call_id="call_002",
                ),
                "虽然库存查询失败，但工单 MO001 状态为 RELEASED。",
            ]
        )
        service = ToolChatService(llm_client=llm)

        result = await service.chat("查一下库存和工单", registry=registry)

        assert (
            result.answer
            == "虽然库存查询失败，但工单 MO001 状态为 RELEASED。"
        )
        # 失败的 Tool 也记录在 tool_calls 中（按执行顺序）
        assert [info.tool_name for info in result.tool_calls] == [
            "get_inventory",
            "get_work_order",
        ]
        assert len(llm.calls) == 3

        # 第 2 轮历史中的 tool message 是失败信息
        failure_payload = json.loads(llm.calls[1]["messages"][2]["content"])
        assert failure_payload["success"] is False
        # 第 3 轮历史中两组消息对：第 1 组失败、第 2 组成功
        m3 = llm.calls[2]["messages"]
        assert json.loads(m3[2]["content"])["success"] is False
        assert json.loads(m3[4]["content"])["success"] is True


# ============================================================
# Case 8：未知 Tool 后可继续（安全失败）
# ============================================================

class TestUnknownToolContinues:
    async def test_unknown_tool_then_known_tool_then_final(
        self, registry
    ) -> None:
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response(
                    "get_unknown_tool", {"x": 1}, call_id="call_unk"
                ),
                _tool_call_llm_response(
                    "get_inventory",
                    {"material_code": "MAT001"},
                    call_id="call_002",
                ),
                "改用库存查询：MAT001 库存 1000 PCS。",
            ]
        )
        service = ToolChatService(llm_client=llm)

        result = await service.chat("查一下", registry=registry)

        assert result.answer == "改用库存查询：MAT001 库存 1000 PCS。"
        assert [info.tool_name for info in result.tool_calls] == [
            "get_unknown_tool",
            "get_inventory",
        ]
        assert len(llm.calls) == 3
        unknown_payload = json.loads(llm.calls[1]["messages"][2]["content"])
        assert unknown_payload["success"] is False
        assert "get_unknown_tool" in unknown_payload["error"]


# ============================================================
# LLM 异常透传
# ============================================================

class TestLLMErrorPropagation:
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

    async def test_llm_round3_error_propagates(self, registry) -> None:
        llm = ScriptedLLMClient(
            [
                _tool_call_llm_response(
                    "get_inventory", {"material_code": "MAT001"}
                ),
                _tool_call_llm_response(
                    "get_work_order", {"work_order_no": "MO001"}
                ),
                LLMRequestError("DeepSeek 500"),
            ]
        )
        service = ToolChatService(llm_client=llm)

        with pytest.raises(LLMRequestError):
            await service.chat("q", registry=registry)
        assert len(llm.calls) == 3


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
# 配置：TOOL_MAX_ROUNDS 钳制（Phase 3.6.3 任务书 §十七）
# ============================================================

class TestToolSettings:
    def test_default_is_five(self, monkeypatch) -> None:
        monkeypatch.delenv("TOOL_MAX_ROUNDS", raising=False)
        from backend.app.config import ToolSettings

        assert ToolSettings().max_rounds == 5

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1", 1),     # 下边界
            ("20", 20),   # 上边界
            ("0", 1),     # 低于下界 → 钳制到 1
            ("-3", 1),
            ("99", 20),   # 高于上界 → 钳制到 20
            ("abc", 5),   # 解析失败 → 默认
            ("", 5),
        ],
    )
    def test_max_rounds_clamped(self, monkeypatch, raw: str, expected: int) -> None:
        monkeypatch.setenv("TOOL_MAX_ROUNDS", raw)
        from backend.app.config import ToolSettings

        assert ToolSettings().max_rounds == expected


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
    "TestTwoSequentialTools",
    "TestThreeSequentialTools",
    "TestBudgetExhausted",
    "TestToolFailureContinues",
    "TestUnknownToolContinues",
    "TestLLMErrorPropagation",
    "TestInputValidation",
    "TestToolSettings",
    "TestSecurity",
]
