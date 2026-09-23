"""LLM Tool Calling 单元测试（Phase 3.6.2）。

覆盖：

    * 无 tools 调用保持 Phase 2 行为（str + payload 无 tools）
    * tools 正确传给 OpenAI-compatible 请求体
    * 普通 response / tool_calls response 解析
    * arguments JSON 解析（字符串 / 空串 / dict 透传）
    * malformed tool call（缺 id / 缺 name / 非法 JSON / 多个 tool call）
    * MockLLMClient 的 tools 路径降级行为
    * ToolDefinition → OpenAI Tool Schema 转换层

全部通过 httpx.MockTransport，不发起真实网络。
"""
from __future__ import annotations

import json

import httpx

from backend.app.llm.client import (
    LLMResponse,
    LLMResponseError,
    LLMToolCallFormatError,
    MockLLMClient,
    OpenAICompatibleClient,
    ToolCall,
)
from backend.app.llm.tool_schema import (
    definition_to_openai_tool,
    definitions_to_openai_tools,
)
from backend.app.tools.mock_tools import (
    GET_INVENTORY_DEFINITION,
    GET_WORK_ORDER_DEFINITION,
)


# ============================================================
# Helpers
# ============================================================

def _tool_call_response(
    call_id: str = "call_001",
    name: str = "get_inventory",
    arguments: str = '{"material_code": "MAT001"}',
) -> httpx.Response:
    """构造包含单个 tool_call 的 OpenAI 兼容响应。"""
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ],
                    }
                }
            ]
        },
    )


def _make_client(handler) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="test-model",
        provider="test",
        transport=httpx.MockTransport(handler),
    )


# ============================================================
# 无 tools：Phase 2 行为保持
# ============================================================

async def test_chat_without_tools_returns_str_and_payload_has_no_tools() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]},
        )

    client = _make_client(handler)
    result = await client.chat([{"role": "user", "content": "hello"}])

    assert isinstance(result, str)
    assert result == "hi"
    assert "tools" not in captured["body"]


# ============================================================
# tools 请求体
# ============================================================

async def test_chat_with_tools_sends_tools_in_payload() -> None:
    captured: dict = {}
    tools = [definition_to_openai_tool(GET_INVENTORY_DEFINITION)]

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _tool_call_response()

    client = _make_client(handler)
    await client.chat([{"role": "user", "content": "q"}], tools=tools)

    assert captured["body"]["tools"] == tools


async def test_chat_with_empty_tools_list_behaves_like_no_tools() -> None:
    """空 tools 列表视同未传（不发送空数组，返回 str）。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    client = _make_client(handler)
    result = await client.chat(
        [{"role": "user", "content": "q"}], tools=[]
    )

    assert isinstance(result, str)
    assert result == "ok"
    assert "tools" not in captured["body"]


# ============================================================
# 响应解析：普通 / tool_calls
# ============================================================

async def test_chat_with_tools_plain_content_returns_llm_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "直接回答"}}]},
        )

    client = _make_client(handler)
    result = await client.chat(
        [{"role": "user", "content": "q"}],
        tools=[definition_to_openai_tool(GET_INVENTORY_DEFINITION)],
    )

    assert isinstance(result, LLMResponse)
    assert result.content == "直接回答"
    assert result.tool_calls == ()


async def test_chat_with_tools_parses_tool_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _tool_call_response()

    client = _make_client(handler)
    result = await client.chat(
        [{"role": "user", "content": "查询 MAT001 库存"}],
        tools=[definition_to_openai_tool(GET_INVENTORY_DEFINITION)],
    )

    assert isinstance(result, LLMResponse)
    assert result.content is None
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.id == "call_001"
    assert call.name == "get_inventory"
    assert call.arguments == {"material_code": "MAT001"}


async def test_tool_call_arguments_empty_string_becomes_empty_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _tool_call_response(arguments="")

    client = _make_client(handler)
    result = await client.chat(
        [{"role": "user", "content": "q"}],
        tools=[definition_to_openai_tool(GET_INVENTORY_DEFINITION)],
    )
    assert isinstance(result, LLMResponse)
    assert result.tool_calls[0].arguments == {}


async def test_tool_call_arguments_dict_passthrough() -> None:
    """部分兼容实现直接返回 dict（而非 JSON 字符串），应能透传。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_002",
                                    "type": "function",
                                    "function": {
                                        "name": "get_work_order",
                                        "arguments": {
                                            "work_order_no": "MO001"
                                        },
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = _make_client(handler)
    result = await client.chat(
        [{"role": "user", "content": "q"}],
        tools=[definition_to_openai_tool(GET_WORK_ORDER_DEFINITION)],
    )
    assert isinstance(result, LLMResponse)
    assert result.tool_calls[0].arguments == {"work_order_no": "MO001"}


# ============================================================
# malformed tool call
# ============================================================

async def test_tool_call_invalid_json_arguments_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _tool_call_response(arguments="{not json")

    client = _make_client(handler)
    try:
        await client.chat(
            [{"role": "user", "content": "q"}],
            tools=[definition_to_openai_tool(GET_INVENTORY_DEFINITION)],
        )
    except LLMToolCallFormatError as exc:
        assert "JSON" in str(exc) or "json" in str(exc).lower()
    else:
        raise AssertionError("expected LLMToolCallFormatError")


async def test_tool_call_non_object_arguments_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _tool_call_response(arguments='["array", "not", "object"]')

    client = _make_client(handler)
    try:
        await client.chat(
            [{"role": "user", "content": "q"}],
            tools=[definition_to_openai_tool(GET_INVENTORY_DEFINITION)],
        )
    except LLMToolCallFormatError:
        pass
    else:
        raise AssertionError("expected LLMToolCallFormatError")


async def test_tool_call_missing_id_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "get_inventory",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = _make_client(handler)
    try:
        await client.chat(
            [{"role": "user", "content": "q"}],
            tools=[definition_to_openai_tool(GET_INVENTORY_DEFINITION)],
        )
    except LLMToolCallFormatError as exc:
        assert "id" in str(exc)
    else:
        raise AssertionError("expected LLMToolCallFormatError")


async def test_tool_call_missing_name_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_003",
                                    "type": "function",
                                    "function": {"arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = _make_client(handler)
    try:
        await client.chat(
            [{"role": "user", "content": "q"}],
            tools=[definition_to_openai_tool(GET_INVENTORY_DEFINITION)],
        )
    except LLMToolCallFormatError as exc:
        assert "name" in str(exc)
    else:
        raise AssertionError("expected LLMToolCallFormatError")


async def test_multiple_tool_calls_rejected() -> None:
    """Phase 3.6.2 协议约束：多个 tool call 必须被明确拒绝（不静默截断）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_a",
                                    "type": "function",
                                    "function": {
                                        "name": "get_inventory",
                                        "arguments": '{"material_code": "M1"}',
                                    },
                                },
                                {
                                    "id": "call_b",
                                    "type": "function",
                                    "function": {
                                        "name": "get_work_order",
                                        "arguments": '{"work_order_no": "MO1"}',
                                    },
                                },
                            ],
                        }
                    }
                ]
            },
        )

    client = _make_client(handler)
    try:
        await client.chat(
            [{"role": "user", "content": "q"}],
            tools=[
                definition_to_openai_tool(GET_INVENTORY_DEFINITION),
                definition_to_openai_tool(GET_WORK_ORDER_DEFINITION),
            ],
        )
    except LLMToolCallFormatError as exc:
        assert "2" in str(exc)
    else:
        raise AssertionError("expected LLMToolCallFormatError")


async def test_non_string_content_with_tools_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": 123}}]},
        )

    client = _make_client(handler)
    try:
        await client.chat(
            [{"role": "user", "content": "q"}],
            tools=[definition_to_openai_tool(GET_INVENTORY_DEFINITION)],
        )
    except LLMResponseError:
        pass
    else:
        raise AssertionError("expected LLMResponseError")


# ============================================================
# ToolCall DTO 契约
# ============================================================

def test_tool_call_dto_validation() -> None:
    try:
        ToolCall(id="", name="get_inventory")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty id")

    try:
        ToolCall(id="call_x", name="")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty name")

    try:
        ToolCall(id="call_x", name="t", arguments="not-a-dict")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for non-dict arguments")

    call = ToolCall(id="call_x", name="t", arguments={"a": 1})
    try:
        call.id = "changed"  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("ToolCall must be frozen")


# ============================================================
# MockLLMClient（无 Key 降级）
# ============================================================

async def test_mock_llm_client_with_tools_returns_response_without_calls() -> None:
    """无 Key 场景：Mock 不伪造 tool call，返回普通 LLMResponse。"""
    client = MockLLMClient()
    result = await client.chat(
        [{"role": "user", "content": "你好"}],
        tools=[definition_to_openai_tool(GET_INVENTORY_DEFINITION)],
    )
    assert isinstance(result, LLMResponse)
    assert result.content is not None and "你好" in result.content
    assert result.tool_calls == ()


async def test_mock_llm_client_without_tools_returns_str() -> None:
    client = MockLLMClient()
    result = await client.chat([{"role": "user", "content": "你好"}])
    assert isinstance(result, str)
    assert "你好" in result


# ============================================================
# 转换层（ToolDefinition → OpenAI Tool Schema）
# ============================================================

class TestToolSchemaConversion:
    def test_definition_to_openai_tool_structure(self) -> None:
        tool = definition_to_openai_tool(GET_INVENTORY_DEFINITION)
        assert tool["type"] == "function"
        function = tool["function"]
        assert function["name"] == "get_inventory"
        assert function["description"] == GET_INVENTORY_DEFINITION.description
        assert function["parameters"] == GET_INVENTORY_DEFINITION.parameters
        assert function["parameters"]["required"] == ["material_code"]
        props = function["parameters"]["properties"]
        assert props["material_code"] == {
            "type": "string",
            "description": "物料编码",
        }

    def test_definitions_to_openai_tools_preserves_order(self) -> None:
        tools = definitions_to_openai_tools(
            [GET_INVENTORY_DEFINITION, GET_WORK_ORDER_DEFINITION]
        )
        assert [t["function"]["name"] for t in tools] == [
            "get_inventory",
            "get_work_order",
        ]

    def test_converted_schema_is_json_serializable(self) -> None:
        tools = definitions_to_openai_tools(
            [GET_INVENTORY_DEFINITION, GET_WORK_ORDER_DEFINITION]
        )
        json.dumps(tools)  # 不抛异常即可

    def test_converted_schema_leaks_no_internal_info(self) -> None:
        """转换结果只含 name / description / parameters，无 handler / module 等。"""
        tool = definition_to_openai_tool(GET_INVENTORY_DEFINITION)
        body = json.dumps(tool, ensure_ascii=False)
        for fragment in (
            "GetInventoryHandler",
            "handler",
            "module",
            "__class__",
            "ToolRegistry",
        ):
            assert fragment not in body

    def test_converted_schema_is_deep_copied(self) -> None:
        """修改转换结果不影响原 ToolDefinition。"""
        tool = definition_to_openai_tool(GET_INVENTORY_DEFINITION)
        tool["function"]["parameters"]["properties"]["material_code"][
            "type"
        ] = "integer"
        assert GET_INVENTORY_DEFINITION.parameters["properties"][
            "material_code"
        ]["type"] == "string"


__all__ = ["TestToolSchemaConversion"]
