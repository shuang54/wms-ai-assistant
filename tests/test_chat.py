"""Chat API 测试（Phase 2）。

通过 monkeypatch 替换 api.chat._chat_service 为带 FakeLLMClient 的实例，
验证：
    - 正常路径：200 + answer
    - 入参校验：422
    - LLMConfigError → 503
    - LLMRequestError → 502
    - LLMResponseError → 502
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.api import chat as chat_module
from backend.app.llm.client import (
    LLMConfigError,
    LLMRequestError,
    LLMResponseError,
)
from backend.app.main import app
from backend.app.services.chat_service import ChatService


class FakeLLMClient:
    """与 ChatService 配合的测试用 LLM Client。"""

    def __init__(
        self,
        response: str = "hello from fake",
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._error = error
        self.calls: list[list[dict[str, str]]] = []

    async def generate(self, prompt: str) -> str:
        return self._response

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(list(messages))
        if self._error is not None:
            raise self._error
        return self._response


@pytest.fixture()
def client(monkeypatch):
    """构造测试客户端，并注入 FakeLLMClient。"""

    def _make_client(response: str = "hello from fake", error: Exception | None = None):
        fake = FakeLLMClient(response=response, error=error)
        chat_module._chat_service = ChatService(llm_client=fake, system_prompt="")
        return TestClient(app)

    yield _make_client

    # 恢复默认（防止影响其它测试文件）
    from backend.app.llm.client import reset_default_llm_client
    reset_default_llm_client()


# ============================================================
# 正常路径
# ============================================================

def test_chat_returns_llm_answer(client) -> None:
    """POST /api/chat 必须返回 200 + answer，且 answer 来自注入的 FakeLLMClient。"""
    with client() as c:
        response = c.post("/api/chat", json={"message": "你好"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "hello from fake"


def test_chat_passes_user_message_to_llm(client) -> None:
    """ChatService 必须把 message 传给 LLM。"""
    fake_holder: dict = {}

    def _make(response="ok", error=None):
        fake = FakeLLMClient(response=response, error=error)
        fake_holder["fake"] = fake
        chat_module._chat_service = ChatService(llm_client=fake, system_prompt="")
        return TestClient(app)

    with _make() as c:
        c.post("/api/chat", json={"message": "测试一下"})
    assert len(fake_holder["fake"].calls) == 1
    assert fake_holder["fake"].calls[0][-1] == {"role": "user", "content": "测试一下"}


# ============================================================
# 入参校验（Pydantic）
# ============================================================

def test_chat_rejects_empty_message(client) -> None:
    with client() as c:
        response = c.post("/api/chat", json={"message": ""})
    assert response.status_code == 422


def test_chat_rejects_missing_field(client) -> None:
    with client() as c:
        response = c.post("/api/chat", json={})
    assert response.status_code == 422


def test_chat_rejects_non_string_message(client) -> None:
    with client() as c:
        response = c.post("/api/chat", json={"message": 123})
    assert response.status_code == 422


# ============================================================
# LLM 异常 → HTTP 状态码映射
# ============================================================

def test_chat_maps_llm_config_error_to_503(client) -> None:
    """LLMConfigError（如 API Key 缺失）→ 503 Service Unavailable。"""
    with client(error=LLMConfigError("api key missing")) as c:
        response = c.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "配置错误" in detail
    # 异常信息对外应清晰，但不暴露完整堆栈
    assert "Traceback" not in detail


def test_chat_maps_llm_request_error_to_502(client) -> None:
    """LLMRequestError（连接失败 / 超时 / 非 2xx）→ 502 Bad Gateway。"""
    with client(error=LLMRequestError("connection refused")) as c:
        response = c.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "请求失败" in detail


def test_chat_maps_llm_response_error_to_502(client) -> None:
    """LLMResponseError（响应解析失败）→ 502 Bad Gateway。"""
    with client(error=LLMResponseError("bad json")) as c:
        response = c.post("/api/chat", json={"message": "hi"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "响应解析" in detail


def test_chat_does_not_leak_traceback(client) -> None:
    """任何 LLM 异常响应中都不能包含 Python traceback。"""
    with client(error=RuntimeError("internal boom")) as c:
        response = c.post("/api/chat", json={"message": "hi"})
    # 兜底映射到 500
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "Traceback" not in detail
    assert "File \"" not in detail