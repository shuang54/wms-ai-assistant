"""Tool Chat API 测试（Phase 3.6.2：POST /api/chat/with-tools）。

通过 monkeypatch 替换 api.tool_chat._tool_chat_service，
验证：

    - 正常路径：普通问题（tool_calls=[]）/ Tool 问题（tool_calls 非空）
    - 协议：response 只含 answer + tool_calls；tool_calls 项只含 tool_name
    - 入参校验：空 / 缺失 / 非法类型
    - 错误映射：LLM 家族 / MultipleToolCallsError / ToolChatError / 未知异常
    - 安全：错误响应不泄露敏感信息
    - OpenAPI 注册
    - 接线：真实 ToolChatService + Scripted LLM + Mock Tools 端到端

全部 Mock，不发起真实 DB / LLM 调用。
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from backend.app.api import tool_chat as tool_chat_module
from backend.app.llm.client import (
    LLMConfigError,
    LLMRequestError,
    LLMResponseError,
    LLMToolCallFormatError,
    ToolCall,
    LLMResponse,
)
from backend.app.main import app
from backend.app.services.tool_chat_service import (
    MultipleToolCallsError,
    ToolChatCallInfo,
    ToolChatError,
    ToolChatResponse,
    ToolChatService,
)
from backend.app.tools.mock_tools import register_mock_tools
from backend.app.tools.registry import ToolRegistry


# ============================================================
# Fake Service
# ============================================================

class FakeToolChatService:
    """记录调用并可注入响应 / 异常的 ToolChatService 替身。"""

    def __init__(
        self,
        *,
        response: ToolChatResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        if response is None:
            response = ToolChatResponse(answer="tool-chat-answer")
        self._response = response
        self._error = error
        self.calls: list[str] = []
        self.registries: list[ToolRegistry] = []

    async def chat(
        self, message: str, *, registry: ToolRegistry
    ) -> ToolChatResponse:
        if not message or not message.strip():
            raise ValueError("message 不能为空")
        self.calls.append(message)
        self.registries.append(registry)
        if self._error is not None:
            raise self._error
        return self._response


@pytest.fixture()
def client(monkeypatch):
    """构造测试客户端；返回 context manager，yield (TestClient, Fake)。"""

    @contextmanager
    def _make(
        *,
        response: ToolChatResponse | None = None,
        error: Exception | None = None,
        raise_server_exceptions: bool = True,
    ):
        fake = FakeToolChatService(response=response, error=error)
        monkeypatch.setattr(tool_chat_module, "_tool_chat_service", fake)
        with TestClient(
            app, raise_server_exceptions=raise_server_exceptions
        ) as c:
            yield c, fake

    return _make


# ============================================================
# 正常路径
# ============================================================

class TestNormalRequest:
    def test_plain_question_returns_200_without_tool_calls(self, client) -> None:
        with client() as (c, _fake):
            response = c.post(
                "/api/chat/with-tools", json={"message": "你好"}
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"] == "tool-chat-answer"
        assert payload["tool_calls"] == []

    def test_tool_question_returns_tool_name(self, client) -> None:
        tool_response = ToolChatResponse(
            answer="MAT001 当前库存为 1000 PCS。",
            tool_calls=(ToolChatCallInfo(tool_name="get_inventory"),),
        )
        with client(response=tool_response) as (c, _fake):
            response = c.post(
                "/api/chat/with-tools",
                json={"message": "查询 MAT001 的库存"},
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"] == "MAT001 当前库存为 1000 PCS。"
        assert payload["tool_calls"] == [{"tool_name": "get_inventory"}]

    def test_response_schema_only_exposes_expected_fields(self, client) -> None:
        with client() as (c, _fake):
            payload = c.post(
                "/api/chat/with-tools", json={"message": "q"}
            ).json()
        assert set(payload.keys()) == {"answer", "tool_calls"}
        if payload["tool_calls"]:
            assert set(payload["tool_calls"][0].keys()) == {"tool_name"}

    def test_message_passed_to_service(self, client) -> None:
        with client() as (c, fake):
            c.post("/api/chat/with-tools", json={"message": "  查库存  "})
        assert fake.calls == ["  查库存  "]

    def test_registry_passed_to_service(self, client) -> None:
        with client() as (c, fake):
            c.post("/api/chat/with-tools", json={"message": "q"})
        assert len(fake.registries) == 1
        # 注入的是模块级 registry（含两个 Mock Tool）
        assert {d.name for d in fake.registries[0].list_definitions()} == {
            "get_inventory",
            "get_work_order",
        }

    def test_openapi_contains_endpoint(self, client) -> None:
        with client() as (c, _fake):
            spec = c.get("/openapi.json").json()
        assert "/api/chat/with-tools" in spec["paths"]
        post = spec["paths"]["/api/chat/with-tools"]["post"]
        assert "200" in post["responses"]


# ============================================================
# 入参校验
# ============================================================

class TestValidation:
    @pytest.mark.parametrize("message", ["", "   ", "\n\t "])
    def test_blank_message_rejected(self, client, message) -> None:
        with client() as (c, fake):
            response = c.post(
                "/api/chat/with-tools", json={"message": message}
            )
        if message == "":
            assert response.status_code == 422
        else:
            assert response.status_code == 400
            assert "不能为空" in response.json()["detail"]
        assert fake.calls == []

    def test_missing_message_rejected_422(self, client) -> None:
        with client() as (c, fake):
            response = c.post("/api/chat/with-tools", json={})
        assert response.status_code == 422
        assert fake.calls == []

    def test_non_string_message_rejected_422(self, client) -> None:
        with client() as (c, fake):
            response = c.post(
                "/api/chat/with-tools", json={"message": 123}
            )
        assert response.status_code == 422
        assert fake.calls == []


# ============================================================
# 错误映射
# ============================================================

class TestErrorMapping:
    @pytest.mark.parametrize(
        ("exc", "expected_status", "expected_fragment"),
        [
            (LLMConfigError("LLM key missing"), 503, "LLM 配置错误"),
            (LLMRequestError("DeepSeek 500"), 502, "LLM 请求失败"),
            (LLMResponseError("bad llm json"), 502, "LLM 响应解析失败"),
            (
                LLMToolCallFormatError("tool_call 缺少 id"),
                502,
                "LLM 响应解析失败",
            ),
            (MultipleToolCallsError(2), 502, "多个 Tool Call"),
        ],
    )
    def test_known_errors_map_to_status(
        self, client, exc, expected_status, expected_fragment
    ) -> None:
        with client(error=exc) as (c, _fake):
            response = c.post("/api/chat/with-tools", json={"message": "q"})
        assert response.status_code == expected_status
        assert expected_fragment in response.json()["detail"]

    def test_tool_chat_error_returns_500(self, client) -> None:
        with client(error=ToolChatError("内部编排错误")) as (c, _fake):
            response = c.post("/api/chat/with-tools", json={"message": "q"})
        assert response.status_code == 500
        assert "Tool Chat 服务内部错误" in response.json()["detail"]

    def test_unknown_error_returns_500_without_leaking_traceback(
        self, client
    ) -> None:
        with client(
            error=RuntimeError("internal boom"),
            raise_server_exceptions=False,
        ) as (c, _fake):
            response = c.post("/api/chat/with-tools", json={"message": "q"})
        assert response.status_code == 500
        body = response.text
        assert "Traceback" not in body
        assert "File \"" not in body
        assert "internal boom" not in body

    @pytest.mark.parametrize(
        "secret_fragment",
        [
            "sk-abc123",
            "Authorization",
            "Bearer ",
            "postgresql://",
            "DATABASE_URL",
            "SELECT ",
            "Traceback",
        ],
    )
    def test_error_response_never_leaks_secrets(
        self, client, secret_fragment
    ) -> None:
        with client(error=LLMRequestError("上游故障")) as (c, _fake):
            response = c.post("/api/chat/with-tools", json={"message": "q"})
        assert response.status_code == 502
        assert secret_fragment not in response.text


# ============================================================
# 接线：真实 ToolChatService + Scripted LLM + Mock Tools
# ============================================================

class _ScriptedLLM:
    """两轮脚本：tool call → 最终回答。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat(self, messages, *, tools=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        if len(self.calls) == 1:
            return LLMResponse(
                content=None,
                tool_calls=(
                    ToolCall(
                        id="call_001",
                        name="get_inventory",
                        arguments={"material_code": "MAT001"},
                    ),
                ),
            )
        return "MAT001 当前库存为 1000 PCS。"


class TestRealServiceWiring:
    def test_end_to_end_through_endpoint(self, monkeypatch) -> None:
        """真实 Service + Scripted LLM：完整两轮链路通过 HTTP 暴露。"""
        llm = _ScriptedLLM()
        monkeypatch.setattr(
            tool_chat_module,
            "_tool_chat_service",
            ToolChatService(llm_client=llm),
        )
        with TestClient(app) as c:
            response = c.post(
                "/api/chat/with-tools",
                json={"message": "查询 MAT001 的库存"},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"] == "MAT001 当前库存为 1000 PCS。"
        assert payload["tool_calls"] == [{"tool_name": "get_inventory"}]
        assert len(llm.calls) == 2

        # 第二轮 LLM 收到 role=tool 消息（Mock Tool 真实执行结果）
        tool_msg = llm.calls[1]["messages"][2]
        assert tool_msg["role"] == "tool"
        import json as _json

        assert _json.loads(tool_msg["content"])["data"]["quantity"] == 1000


__all__ = [
    "FakeToolChatService",
    "TestNormalRequest",
    "TestValidation",
    "TestErrorMapping",
    "TestRealServiceWiring",
]
