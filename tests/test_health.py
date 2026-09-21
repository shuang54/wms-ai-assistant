"""Health Check API 测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app


def test_health_returns_ok() -> None:
    """GET /api/health 必须返回 200 与 status=ok。"""
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "wms-ai-assistant"
