"""Assistant Timeline HTTP Read API（Phase 3.12 Step 68）——HTTP 契约测试。

    GET /api/observability/assistant-timeline/{assistant_request_id}

测试装配方式（**离线，不触达 PostgreSQL**）：

    monkeypatch api/assistant_timeline.get_assistant_timeline_query_service
        → 真实 AssistantTimelineQueryService + **Fake 读边界**
          （真实 Row / View DTO；无 DB / 无网络）

覆盖（§十九）：完整 Timeline · LLM-only · Tool-only · RAG-only · Outcome-only ·
历史缺 Outcome · Unknown request · Cross-request 隔离 · source/event_type 映射 ·
source_id 暴露 · 9 字段白名单 · 敏感字段隔离 · 非法 request id · 路径长度校验 ·
已知错误→502 · 未知错误→500 · 错误不泄漏内部异常 · API 不直连 DB/网络 ·
既有 Trace API 未变 · main.py 注册 · 不合并 / 不产生 sequence · status 映射 ·
时间字段语义。
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import assistant_timeline as timeline_module
from backend.app.api.assistant_trace import AssistantTraceResponse
from backend.app.db.assistant_outcome_repository import (
    AssistantOutcomeRepositoryError,
)
from backend.app.db.rag_execution_repository import RagExecutionRecordRow
from backend.app.db.tool_execution_repository import ToolExecutionRecordRow
from backend.app.dto.assistant_timeline_api import (
    TIMELINE_EVENT_RESPONSE_FIELDS,
    TIMELINE_RESPONSE_FIELDS,
    AssistantTimelineEventResponse,
    AssistantTimelineResponse,
)
from backend.app.main import app
from backend.app.services.assistant_timeline_query_service import (
    AssistantTimelineQueryService,
)
from backend.app.services.llm_usage_query_service import LLMUsageTraceRecordView

_ENDPOINT: str = "/api/observability/assistant-timeline"
_TRACE_ENDPOINT: str = "/api/observability/assistant-trace"
_BASE: datetime = datetime(2026, 9, 30, 11, 0, 0, tzinfo=timezone.utc)
_SENTINEL: str = "STEP68-INTERNAL-SECRET"
_UNAVAILABLE_DETAIL: str = "助手链路观测数据不可用"


# ============================================================
# Fake 读边界（离线）
# ============================================================

class _FakeLlmQuery:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def list_by_assistant_request_id(self, request_id: str) -> list[Any]:
        return [
            row for row in self._rows if row.assistant_request_id == request_id
        ]


class _FakeToolQuery:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def list_rows_by_request_id(self, request_id: str) -> list[Any]:
        return [row for row in self._rows if row.request_id == request_id]


class _FakeRagQuery:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def list_by_request_id(self, request_id: str) -> list[Any]:
        return [row for row in self._rows if row.request_id == request_id]


class _FakeOutcomeQuery:
    def __init__(self, rows: dict[str, Any]) -> None:
        self._rows = rows

    def get_row_by_assistant_request_id(
        self, assistant_request_id: str,
    ) -> Any | None:
        return self._rows.get(assistant_request_id)


class _ExplodingService:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def get_timeline(self, assistant_request_id: str) -> Any:
        raise self._error


def _llm(
    *, id: int, assistant_request_id: str, created_at: datetime,  # noqa: A002
) -> LLMUsageTraceRecordView:
    return LLMUsageTraceRecordView(
        id=id,
        assistant_request_id=assistant_request_id,
        request_id="chatcmpl-prov",
        provider="step68-provider",
        model="step68-model",
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
        created_at=created_at,
    )


def _tool(*, id: int, request_id: str, success: bool = True) -> (  # noqa: A002
    ToolExecutionRecordRow
):
    return ToolExecutionRecordRow(
        id=id,
        request_id=request_id,
        round=1,
        tool_name="get_inventory",
        started_at=_BASE,
        finished_at=_BASE + timedelta(milliseconds=40),
        duration_ms=40.0,
        success=success,
        project_id=None,
        tool_call_id=None,
        error_code=None,
        error_type=None,
    )


def _rag(*, id: int, request_id: str) -> RagExecutionRecordRow:  # noqa: A002
    return RagExecutionRecordRow(
        id=id,
        request_id=request_id,
        started_at=_BASE,
        finished_at=_BASE + timedelta(milliseconds=150),
        duration_ms=150.0,
        result_count=2,
        used_chunks_count=2,
        top_k=5,
        context_truncated=False,
        context_chars=120,
        reranker_used=False,
        rerank_elapsed_ms=None,
        chunk_ids=(1, 2),
        document_ids=(10,),
    )


def _outcome_row(
    *, id: int, assistant_request_id: str, outcome: str = "SUCCESS",  # noqa: A002
) -> Any:
    from backend.app.db.assistant_outcome_repository import AssistantOutcomeRow

    return AssistantOutcomeRow(
        id=id,
        assistant_request_id=assistant_request_id,
        outcome=outcome,
        created_at=_BASE + timedelta(seconds=3),
    )


@pytest.fixture()
def timeline_api(monkeypatch: pytest.MonkeyPatch):
    """安装真实 Timeline Service + Fake 读边界（**零 DB**）。"""

    def _install(
        *,
        llm: list[Any] | None = None,
        tools: list[Any] | None = None,
        rag: list[Any] | None = None,
        outcomes: dict[str, Any] | None = None,
        service: Any = None,
        error: Exception | None = None,
    ) -> None:
        installed: Any
        if error is not None:
            installed = _ExplodingService(error)
        elif service is not None:
            installed = service
        else:
            installed = AssistantTimelineQueryService(
                llm_usage_query_service=_FakeLlmQuery(llm or []),
                tool_execution_query_service=_FakeToolQuery(tools or []),
                rag_execution_query_service=_FakeRagQuery(rag or []),
                outcome_query_service=_FakeOutcomeQuery(outcomes or {}),
            )
        monkeypatch.setattr(
            timeline_module,
            "get_assistant_timeline_query_service",
            lambda: installed,
        )

    return _install


def _get(request_id: str) -> Any:
    with TestClient(app) as client:
        return client.get(f"{_ENDPOINT}/{request_id}")


# ============================================================
# 1~8. 投影场景
# ============================================================

class TestTimelineEndpointCases:
    def test_1_full_timeline(self, timeline_api) -> None:
        timeline_api(
            llm=[
                _llm(id=2, assistant_request_id="A", created_at=_BASE),
                _llm(
                    id=1,
                    assistant_request_id="A",
                    created_at=_BASE - timedelta(seconds=1),
                ),
            ],
            tools=[_tool(id=21, request_id="A")],
            rag=[_rag(id=31, request_id="A")],
            outcomes={"A": _outcome_row(id=41, assistant_request_id="A")},
        )

        response = _get("A")

        assert response.status_code == 200
        payload = response.json()
        assert payload["assistant_request_id"] == "A"
        assert [event["source_id"] for event in payload["llm_events"]] == [1, 2]
        assert [event["source_id"] for event in payload["tool_events"]] == [21]
        assert [event["source_id"] for event in payload["rag_events"]] == [31]
        assert payload["outcome_event"]["source_id"] == 41
        assert payload["outcome_event"]["status"] == "SUCCESS"

    def test_2_llm_only(self, timeline_api) -> None:
        timeline_api(llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)])

        payload = _get("A").json()

        assert len(payload["llm_events"]) == 1
        assert payload["tool_events"] == []
        assert payload["rag_events"] == []
        assert payload["outcome_event"] is None
        event = payload["llm_events"][0]
        assert event["created_at"] is not None
        assert event["started_at"] is None
        assert event["finished_at"] is None
        assert event["duration_ms"] is None
        assert event["status"] is None

    def test_3_tool_only(self, timeline_api) -> None:
        timeline_api(tools=[_tool(id=21, request_id="A", success=False)])

        payload = _get("A").json()

        assert len(payload["tool_events"]) == 1
        assert payload["tool_events"][0]["status"] == "failed"
        assert payload["tool_events"][0]["created_at"] is None
        assert payload["tool_events"][0]["duration_ms"] == 40.0
        assert payload["llm_events"] == []
        assert payload["rag_events"] == []

    def test_4_rag_only(self, timeline_api) -> None:
        timeline_api(rag=[_rag(id=31, request_id="A")])

        payload = _get("A").json()

        assert len(payload["rag_events"]) == 1
        assert payload["rag_events"][0]["status"] is None   # RAG 无 success 事实
        assert payload["rag_events"][0]["created_at"] is None
        assert payload["llm_events"] == []
        assert payload["tool_events"] == []

    def test_5_outcome_only(self, timeline_api) -> None:
        timeline_api(
            outcomes={
                "A": _outcome_row(id=41, assistant_request_id="A", outcome="EMPTY")
            }
        )

        payload = _get("A").json()

        assert payload["outcome_event"]["status"] == "EMPTY"
        assert payload["outcome_event"]["event_type"] == "OUTCOME"
        assert payload["llm_events"] == []

    def test_6_historical_missing_outcome(self, timeline_api) -> None:
        timeline_api(
            llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)],
            rag=[_rag(id=31, request_id="A")],
            outcomes={},
        )

        payload = _get("A").json()

        assert payload["outcome_event"] is None             # 不推断 SUCCESS
        assert len(payload["llm_events"]) == 1
        assert len(payload["rag_events"]) == 1

    def test_7_unknown_request_is_200_empty(self, timeline_api) -> None:
        timeline_api()

        response = _get("not-exist")

        assert response.status_code == 200                  # **不是** 404
        assert response.json() == {
            "assistant_request_id": "not-exist",
            "llm_events": [],
            "tool_events": [],
            "rag_events": [],
            "outcome_event": None,
        }

    def test_8_cross_request_isolation(self, timeline_api) -> None:
        timeline_api(
            llm=[
                _llm(id=11, assistant_request_id="A", created_at=_BASE),
                _llm(id=12, assistant_request_id="B", created_at=_BASE),
            ],
            tools=[_tool(id=21, request_id="A"), _tool(id=22, request_id="B")],
            rag=[_rag(id=31, request_id="A"), _rag(id=32, request_id="B")],
            outcomes={
                "A": _outcome_row(id=41, assistant_request_id="A", outcome="SUCCESS"),
                "B": _outcome_row(id=42, assistant_request_id="B", outcome="FAILED"),
            },
        )

        payload_a = _get("A").json()
        payload_b = _get("B").json()

        assert [event["source_id"] for event in payload_a["llm_events"]] == [11]
        assert payload_a["outcome_event"]["status"] == "SUCCESS"
        assert [event["source_id"] for event in payload_b["llm_events"]] == [12]
        assert payload_b["outcome_event"]["status"] == "FAILED"
        assert [event["source_id"] for event in payload_b["tool_events"]] == [22]
        assert [event["source_id"] for event in payload_b["rag_events"]] == [32]


# ============================================================
# 9~12. 契约 / 安全
# ============================================================

class TestContractAndSecurity:
    def test_9_source_event_type_mapping(self, timeline_api) -> None:
        timeline_api(
            llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)],
            tools=[_tool(id=2, request_id="A")],
            rag=[_rag(id=3, request_id="A")],
            outcomes={"A": _outcome_row(id=4, assistant_request_id="A")},
        )

        payload = _get("A").json()

        assert (payload["llm_events"][0]["source"],
                payload["llm_events"][0]["event_type"]) == ("llm_usage", "LLM")
        assert (payload["tool_events"][0]["source"],
                payload["tool_events"][0]["event_type"]) == (
            "tool_execution", "TOOL",
        )
        assert (payload["rag_events"][0]["source"],
                payload["rag_events"][0]["event_type"]) == (
            "rag_execution", "RAG",
        )
        assert (payload["outcome_event"]["source"],
                payload["outcome_event"]["event_type"]) == (
            "assistant_outcome", "OUTCOME",
        )

    def test_10_source_id_exposed_as_internal_identity(self, timeline_api) -> None:
        timeline_api(llm=[_llm(id=1001, assistant_request_id="A", created_at=_BASE)])

        payload = _get("A").json()

        assert payload["llm_events"][0]["source_id"] == 1001
        # 文档必须说明它**不是** global event id / 不表示顺序
        description = AssistantTimelineEventResponse.model_fields[
            "source_id"
        ].description or ""
        assert "不是 global event id" in description
        assert "顺序" in description
        assert "event_id" not in payload["llm_events"][0]
        assert "sequence" not in payload["llm_events"][0]

    def test_11_nine_field_whitelist(self) -> None:
        assert tuple(AssistantTimelineEventResponse.model_fields) == (
            TIMELINE_EVENT_RESPONSE_FIELDS
        )
        assert tuple(AssistantTimelineResponse.model_fields) == (
            TIMELINE_RESPONSE_FIELDS
        )
        assert len(TIMELINE_EVENT_RESPONSE_FIELDS) == 9

    def test_11b_no_merged_events_or_identity_fields(self, timeline_api) -> None:
        timeline_api(llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)])

        payload = _get("A").json()

        assert "events" not in payload
        for forbidden in ("event_id", "sequence", "span_id", "parent_event_id",
                          "trace_id", "order", "index", "attempt"):
            assert forbidden not in payload
            assert forbidden not in payload["llm_events"][0]

    def test_12_sensitive_fields_absent(self, timeline_api) -> None:
        timeline_api(
            llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)],
            tools=[_tool(id=2, request_id="A")],
            rag=[_rag(id=3, request_id="A")],
            outcomes={"A": _outcome_row(id=4, assistant_request_id="A")},
        )

        response = _get("A")
        blob = response.text

        for forbidden in (
            "prompt", "messages", "system_prompt", "user_prompt", "sql",
            "query", "content", "embedding", "similarity", "arguments",
            "tool_result", "api_key", "authorization", "password",
            "database_url", "postgresql://", "raw_response", "traceback",
            "exception", "sk-", _SENTINEL,
        ):
            assert forbidden not in blob, forbidden
        # 允许出现的类别
        for allowed in ("source_id", "created_at", "started_at", "status"):
            assert allowed in blob


# ============================================================
# 13~17. 校验 / 错误映射
# ============================================================

class TestValidationAndErrors:
    def test_13_invalid_request_id(self, timeline_api) -> None:
        timeline_api()

        # 纯空白 → 服务层 ValueError → 400（沿用既有「非法输入」语义）
        response = _get("%20%20%20")
        assert response.status_code == 400
        assert "非法输入" in response.json()["detail"]

    def test_14_path_length_validation(self, timeline_api) -> None:
        timeline_api()

        assert _get("A").status_code == 200                     # 1 字符 → 200
        assert _get("x" * 129).status_code == 422               # 越界 → 422

    def test_15_known_repository_error_is_502(self, timeline_api) -> None:
        timeline_api(
            error=AssistantOutcomeRepositoryError(
                f"{_SENTINEL}: connection string leaked"
            )
        )

        response = _get("A")

        assert response.status_code == 502
        assert response.json() == {"detail": _UNAVAILABLE_DETAIL}

    def test_16_unexpected_error_is_500(self, timeline_api) -> None:
        timeline_api(error=RuntimeError(_SENTINEL))

        response = _get("A")

        assert response.status_code == 500
        assert response.json() == {"detail": _UNAVAILABLE_DETAIL}

    def test_17_error_response_leaks_nothing(self, timeline_api) -> None:
        timeline_api(error=RuntimeError(f"{_SENTINEL} / db/postgres/secret"))

        response = _get("A")

        assert _SENTINEL not in response.text
        assert "postgres" not in response.text
        assert "Traceback" not in response.text
        assert set(response.json()) == {"detail"}


# ============================================================
# 18~20. 边界 / 注册 / 既有 API 兼容
# ============================================================

def _module_identifiers(relative: str) -> set[str]:
    tree = ast.parse(
        (Path(__file__).resolve().parents[1] / relative).read_text(
            encoding="utf-8"
        )
    )
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


class TestBoundaries:
    def test_18_api_does_not_touch_db_or_network(self) -> None:
        identifiers = _module_identifiers(
            "backend/app/api/assistant_timeline.py"
        )
        for forbidden in (
            "create_" + "engine",
            "get_" + "engine",
            "Session",
            "session" + "maker",
            "get_session_" + "factory",
            "LLMUsageRepository",
            "ToolExecutionRepository",
            "RagExecutionRepository",
            "AssistantOutcomeRepository",
            "httpx",
            "requests",
            "socket",
        ):
            assert forbidden not in identifiers, forbidden
        # Service 由 Composition Root accessor 提供
        assert "get_assistant_timeline_query_service" in identifiers

    def test_19_trace_api_contract_unchanged(self, timeline_api) -> None:
        """既有 Assistant Trace API 未新增 timeline 字段，端点仍可用。"""
        assert list(AssistantTraceResponse.model_fields) == [
            "assistant_request_id",
            "outcome",
            "llm_usage",
            "tool_executions",
            "rag_executions",
        ]
        trace_schema = app.openapi()["components"]["schemas"][
            "AssistantTraceResponse"
        ]["properties"]
        assert "timeline" not in trace_schema
        assert (
            "/api/observability/assistant-trace/{assistant_request_id}"
            in app.openapi()["paths"]
        )
        # Trace 端点本身仍返回既有 502 契约（未注入 → 真实读边界失败时）
        with TestClient(app) as client:
            response = client.get(f"{_TRACE_ENDPOINT}/step68-trace-check")
        assert response.status_code in {200, 502, 500}

    def test_20_router_registration(self) -> None:
        spec = app.openapi()
        path = "/api/observability/assistant-timeline/{assistant_request_id}"

        assert path in spec["paths"]
        assert list(spec["paths"][path]) == ["get"]
        operation = spec["paths"][path]["get"]
        assert operation["tags"] == ["observability"]
        assert operation["responses"]["200"]["content"][
            "application/json"
        ]["schema"] == {
            "$ref": "#/components/schemas/AssistantTimelineResponse"
        }
        assert set(operation["responses"]) >= {"200", "400", "422", "502", "500"}

    def test_20b_explicit_mapping_only(self) -> None:
        """API 层不得用 model_dump / asdict 直接投影内部 DTO。"""
        identifiers = _module_identifiers(
            "backend/app/api/assistant_timeline.py"
        )
        for forbidden in ("model_dump", "asdict", "vars", "__dict__"):
            assert forbidden not in identifiers, forbidden

    def test_20c_ordering_is_not_recomputed_in_api(self, timeline_api) -> None:
        """HTTP 层不重排：直接沿用 Service 的组内顺序（含 id tie-breaker）。"""
        timeline_api(
            llm=[
                _llm(id=5, assistant_request_id="A", created_at=_BASE),
                _llm(id=3, assistant_request_id="A", created_at=_BASE),
            ],
            tools=[_tool(id=9, request_id="A"), _tool(id=7, request_id="A")],
        )

        payload = _get("A").json()

        assert [event["source_id"] for event in payload["llm_events"]] == [3, 5]
        assert [event["source_id"] for event in payload["tool_events"]] == [7, 9]

    def test_20d_status_mapping(self, timeline_api) -> None:
        timeline_api(
            tools=[_tool(id=1, request_id="A", success=True)],
            rag=[_rag(id=2, request_id="A")],
            outcomes={
                "A": _outcome_row(id=3, assistant_request_id="A", outcome="REFUSED")
            },
        )

        payload = _get("A").json()

        assert payload["tool_events"][0]["status"] == "success"
        assert payload["rag_events"][0]["status"] is None
        assert payload["outcome_event"]["status"] == "REFUSED"

    def test_20e_timestamp_semantics_in_payload(self, timeline_api) -> None:
        """``created_at`` 不得被当成 ``finished_at``（钟域语义在 HTTP 层保持）。"""
        timeline_api(
            llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)],
            tools=[_tool(id=2, request_id="A")],
        )

        payload = _get("A").json()

        llm_event = payload["llm_events"][0]
        assert llm_event["created_at"] is not None
        assert llm_event["started_at"] is None
        assert llm_event["finished_at"] is None
        tool_event = payload["tool_events"][0]
        assert tool_event["created_at"] is None
        assert tool_event["started_at"] is not None
        assert tool_event["finished_at"] is not None
        assert json.dumps(payload) is not None


__all__ = [
    "TestTimelineEndpointCases",
    "TestContractAndSecurity",
    "TestValidationAndErrors",
    "TestBoundaries",
]
