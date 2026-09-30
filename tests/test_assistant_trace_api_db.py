"""Assistant Trace Read API：DB 集成测试（Phase 3.12 Step 39）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

真实链路（**无 Fake 服务**）：

    LLM Usage ← 真实 PostgreSQL（ai_ops.llm_usage_record；Step 36 correlation）
    Tool      ← 真实 PostgreSQL（ai_ops.tool_execution_record；Step 41 起）
        ↓
    GET /api/observability/assistant-trace/{assistant_request_id}
        （真实 accessor → 真实 AssistantTraceQueryService → 两个真实读边界）
        ↓
    JSON

数据：synthetic（provider / request_id 前缀 ``step39-``）；未读取真实数据。
清理（不用 TRUNCATE）：按 provider / request_id 前缀定向 ``DELETE``（LLM Usage 与 Tool Execution 两张表）。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import orchestrator_chat as root
from backend.app.db import init_db as init_db_module
from backend.app.db.llm_usage_repository import LLMUsageRepository
from backend.app.db.models.llm_usage_record import LLM_USAGE_SCHEMA
from backend.app.db.session import get_engine, get_session_factory
from backend.app.llm.client import LLMUsage
from backend.app.llm.observability import LLMObservation
from backend.app.main import app
from backend.app.services.assistant_trace import assistant_trace_scope
from backend.app.services.llm_usage_persistence_service import (
    LLMUsagePersistenceService,
)
from backend.app.db.tool_execution_repository import (
    ToolExecutionRepository,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord

_TABLE = f"{LLM_USAGE_SCHEMA}.llm_usage_record"
_PREFIX = "step39-%"
_DELETE_SQL = text(f"DELETE FROM {_TABLE} WHERE provider LIKE :prefix")
_TOOL_TABLE = "ai_ops.tool_execution_record"
_TOOL_DELETE_SQL = text(
    f"DELETE FROM {_TOOL_TABLE} WHERE request_id LIKE :prefix"
)
_TOOL_COUNT_SQL = text(
    f"SELECT COUNT(*) FROM {_TOOL_TABLE} "
    "WHERE request_id LIKE :prefix"
)
_TOOL_TOTAL_SQL = text(f"SELECT COUNT(*) FROM {_TOOL_TABLE}")
_COUNT_SQL = text(f"SELECT COUNT(*) FROM {_TABLE} WHERE provider LIKE :prefix")
_TOTAL_SQL = text(f"SELECT COUNT(*) FROM {_TABLE}")
_ENDPOINT = "/api/observability/assistant-trace"
_BASE = datetime(2026, 9, 28, 19, 0, 0, tzinfo=timezone.utc)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _observation(provider_request_id: str) -> LLMObservation:
    return LLMObservation(
        provider="step39-provider",
        model="step39-model",
        latency_ms=1.0,
        success=True,
        finish_reason="stop",
        usage=LLMUsage(11, 22, 33),
        request_id=provider_request_id,
    )


def _persist_usage(assistant_request_id: str | None, provider_request_id: str) -> None:
    service = LLMUsagePersistenceService(repository=LLMUsageRepository())
    if assistant_request_id is None:
        service.persist(_observation(provider_request_id))
        return
    with assistant_trace_scope(assistant_request_id):
        service.persist(_observation(provider_request_id))


def _tool_record(*, request_id: str, round_: int) -> ToolExecutionRecord:
    started = _BASE + timedelta(seconds=round_)
    return ToolExecutionRecord(
        request_id=request_id,
        round=round_,
        tool_name="get_inventory",
        started_at=started,
        finished_at=started + timedelta(milliseconds=5),
        duration_ms=5.0,
        success=True,
        project_id="project-a",
        tool_call_id=None,
        error_type=None,
    )


@pytest.fixture()
def trace_api_db():
    engine = get_engine()
    if engine is None or get_session_factory() is None:
        pytest.skip("DATABASE_URL 未配置")
    init_db_module.init_db()
    with engine.begin() as conn:
        conn.execute(_DELETE_SQL, {"prefix": _PREFIX})
        baseline = int(conn.execute(_TOTAL_SQL).scalar_one())
        conn.execute(_TOOL_DELETE_SQL, {"prefix": _PREFIX})
        tool_baseline = int(conn.execute(_TOOL_TOTAL_SQL).scalar_one())
    yield engine
    with engine.begin() as conn:
        conn.execute(_DELETE_SQL, {"prefix": _PREFIX})
        assert int(conn.execute(_COUNT_SQL, {"prefix": _PREFIX}).scalar_one()) == 0
        assert int(conn.execute(_TOTAL_SQL).scalar_one()) == baseline
        conn.execute(_TOOL_DELETE_SQL, {"prefix": _PREFIX})
        assert int(conn.execute(_TOOL_COUNT_SQL, {"prefix": _PREFIX}).scalar_one()) == 0
        assert int(conn.execute(_TOOL_TOTAL_SQL).scalar_one()) == tool_baseline


@requires_db
class TestAssistantTraceApiDb:
    def test_llm_and_tool_correlation(self, trace_api_db) -> None:
        _persist_usage("step39-A", "step39-P1")
        _persist_usage("step39-A", "step39-P2")
        ToolExecutionRepository().create(
            _tool_record(request_id="step39-A", round_=1)
        )

        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/step39-A")

        assert response.status_code == 200
        payload = response.json()
        assert payload["assistant_request_id"] == "step39-A"
        assert [v["request_id"] for v in payload["llm_usage"]] == [
            "step39-P1", "step39-P2",
        ]
        assert [v["assistant_request_id"] for v in payload["llm_usage"]] == [
            "step39-A", "step39-A",
        ]
        assert len(payload["tool_executions"]) == 1
        tool = payload["tool_executions"][0]
        assert tool["request_id"] == "step39-A"
        assert tool["round"] == 1 and tool["tool_call_id"] is None
        # 键级安全断言
        for forbidden_key in (
            "prompt", "messages", "arguments", "data", "sql", "secret",
            "api_key", "password", "database_url",
        ):
            assert f'"{forbidden_key}":' not in response.text, forbidden_key

    def test_cross_request_isolation(self, trace_api_db) -> None:
        _persist_usage("step39-A", "step39-P3")
        _persist_usage("step39-B", "step39-P4")
        ToolExecutionRepository().create(
            _tool_record(request_id="step39-A", round_=1)
        )
        ToolExecutionRepository().create(
            _tool_record(request_id="step39-B", round_=2)
        )

        with TestClient(app) as client:
            payload_a = client.get(f"{_ENDPOINT}/step39-A").json()
            payload_b = client.get(f"{_ENDPOINT}/step39-B").json()

        assert [v["request_id"] for v in payload_a["llm_usage"]] == ["step39-P3"]
        assert [t["request_id"] for t in payload_a["tool_executions"]] == [
            "step39-A"
        ]
        assert [v["request_id"] for v in payload_b["llm_usage"]] == ["step39-P4"]
        assert [t["request_id"] for t in payload_b["tool_executions"]] == [
            "step39-B"
        ]

    def test_empty_trace_is_200(self, trace_api_db) -> None:
        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/step39-not-exist")

        assert response.status_code == 200
        assert response.json() == {
            "assistant_request_id": "step39-not-exist",
            "outcome": None,        # Step 64（additive；无终态记录）
            "llm_usage": [],
            "tool_executions": [],
            "rag_executions": [],   # Step 48（additive）
        }

    def test_legacy_null_usage_not_attached(self, trace_api_db) -> None:
        _persist_usage(None, "step39-P5")          # 历史 / 旧链路（NULL）
        _persist_usage("step39-A", "step39-P6")

        with TestClient(app) as client:
            payload = client.get(f"{_ENDPOINT}/step39-A").json()

        assert [v["request_id"] for v in payload["llm_usage"]] == ["step39-P6"]


__all__ = ["TestAssistantTraceApiDb"]
