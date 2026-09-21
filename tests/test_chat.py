"""Chat API 测试（Phase 1 Mock）。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app


def test_chat_returns_mock_answer() -> None:
    """POST /api/chat 必须返回 200，且 answer 中回显用户消息。"""
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": "你好"})

    assert response.status_code == 200
    payload = response.json()
    assert "answer" in payload
    assert "你好" in payload["answer"]


def test_chat_rejects_empty_message() -> None:
    """message 为空字符串时必须返回 422（Pydantic 校验失败）。"""
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": ""})

    assert response.status_code == 422


def test_chat_rejects_missing_field() -> None:
    """缺少 message 字段时必须返回 422。"""
    with TestClient(app) as client:
        response = client.post("/api/chat", json={})

    assert response.status_code == 422
