"""Assistant Trace Read API 测试（Phase 3.12 Step 39）。

    GET /api/observability/assistant-trace/{assistant_request_id}
        ↓ AssistantTraceQueryService.get_trace()（真实组合服务）
        ├── LLMUsageQueryService（测试用 Fake / 可注入异常）
        └── ToolObservabilityQueryService（真实类 + 真实 InMemory Collector）
        ↓ AssistantTraceResponse（显式映射）
        ↓ JSON

0 DB（真实 PostgreSQL 路径见 test_assistant_trace_api_db.py）；
0 real LLM；0 真实 Tool 执行。
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import assistant_trace as trace_module
from backend.app.api import orchestrator_chat as root
from backend.app.db.llm_usage_repository import LLMUsageRepositoryError
from backend.app.main import app
from backend.app.services.assistant_trace_query_service import (
    AssistantTraceQueryService,
)
from backend.app.services.in_memory_tool_execution_collector import (
    InMemoryToolExecutionCollector,
)
from backend.app.services.llm_usage_query_service import (
    LLMUsageTraceRecordView,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.services.tool_observability_query_service import (
    ToolObservabilityQueryService,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_API_MODULE = "backend/app/api/assistant_trace.py"
_ENDPOINT = "/api/observability/assistant-trace"
_BASE = datetime(2026, 9, 28, 18, 0, 0, tzinfo=timezone.utc)

_LLM_FIELDS = (
    "id", "assistant_request_id", "request_id", "provider", "model",
    "prompt_tokens", "completion_tokens", "total_tokens", "created_at",
)
_TOOL_FIELDS = (
    "request_id", "round", "tool_name", "started_at", "finished_at",
    "duration_ms", "success", "project_id", "tool_call_id", "error_code",
    "error_type",
)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.Module:
    return ast.parse(_source(relative))


def _identifiers(relative: str) -> set[str]:
    tree = _tree(relative)
    return {
        node.id.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    } | {
        node.attr.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }


def _llm_view(**overrides: Any) -> LLMUsageTraceRecordView:
    values: dict[str, Any] = {
        "id": 1,
        "assistant_request_id": "A",
        "request_id": "chatcmpl-P1",
        "provider": "deepseek-test",
        "model": "deepseek-chat",
        "prompt_tokens": 11,
        "completion_tokens": 22,
        "total_tokens": 33,
        "created_at": _BASE,
    }
    values.update(overrides)
    return LLMUsageTraceRecordView(**values)


def _tool_record(*, request_id: str, round_: int = 1) -> ToolExecutionRecord:
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


class _FakeLlmService:
    def __init__(
        self,
        views: list[LLMUsageTraceRecordView] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.views = views if views is not None else []
        self.calls: list[str] = []
        self._error = error

    def list_by_assistant_request_id(
        self, assistant_request_id: str
    ) -> list[LLMUsageTraceRecordView]:
        self.calls.append(assistant_request_id)
        if self._error is not None:
            raise self._error
        return list(self.views)


class _DuckLlmRow:
    """带**额外字段**的 LLM 行替身（验证显式映射不透传额外字段）。"""

    def __init__(self) -> None:
        self.id = 7
        self.assistant_request_id = "A"
        self.request_id = "chatcmpl-duck"
        self.provider = "deepseek-test"
        self.model = "deepseek-chat"
        self.prompt_tokens = 1
        self.completion_tokens = 2
        self.total_tokens = 3
        self.created_at = _BASE
        # 以下字段**绝不允许**出现在响应里
        self.prompt = "SYSTEM-PROMPT-LEAK"
        self.messages = [{"role": "user", "content": "LEAK"}]
        self.arguments = {"material_code": "LEAK"}
        self.raw_response = "RAW-LEAK"
        self.api_key = "sk-LEAK"


class _DuckToolRow:
    def __init__(self) -> None:
        self.request_id = "A"
        self.round = 1
        self.tool_name = "get_inventory"
        self.started_at = _BASE
        self.finished_at = _BASE + timedelta(milliseconds=1)
        self.duration_ms = 1.0
        self.success = True
        self.project_id = "project-a"
        self.tool_call_id = None
        self.error_code = None
        self.error_type = None
        # 额外字段（禁止外泄）
        self.data = {"result": "LEAK"}
        self.sql = "SELECT 1"
        self.secret = "top-secret"


class _DuckTraceView:
    """duck-typed View（模拟未来 DTO 增加内部字段的情形）。"""

    def __init__(self) -> None:
        self.assistant_request_id = "A"
        self.llm_usage = (_DuckLlmRow(),)
        self.tool_executions = (_DuckToolRow(),)
        self.internal_debug = "LEAK"


class _FakeTraceService:
    def __init__(
        self, view: Any = None, error: Exception | None = None
    ) -> None:
        self.calls: list[str] = []
        self._view = view
        self._error = error

    def get_trace(self, assistant_request_id: str) -> Any:
        self.calls.append(assistant_request_id)
        if self._error is not None:
            raise self._error
        return self._view


@pytest.fixture()
def trace_api(monkeypatch):
    """装配测试用组合服务（真实 AssistantTraceQueryService + Fake LLM 边界）。"""

    def _install(
        *,
        views: list[LLMUsageTraceRecordView] | None = None,
        llm_error: Exception | None = None,
        tool_records: list[ToolExecutionRecord] | None = None,
        service: Any = None,
    ):
        collector = InMemoryToolExecutionCollector()
        for record in tool_records or []:
            collector.on_execution(record)
        llm = _FakeLlmService(views or [], error=llm_error)
        installed = service or AssistantTraceQueryService(
            llm_usage_query_service=llm,
            tool_observability_query_service=(
                ToolObservabilityQueryService(collector)
            ),
        )
        monkeypatch.setattr(
            trace_module,
            "get_assistant_trace_query_service",
            lambda: installed,
        )
        return llm, collector

    return _install


# ============================================================
# Test 1~4：组合语义（HTTP）
# ============================================================

class TestTraceEndpoint:
    def test_1_llm_only(self, trace_api) -> None:
        trace_api(views=[
            _llm_view(id=1, request_id="chatcmpl-P1"),
            _llm_view(
                id=2, request_id="chatcmpl-P2",
                created_at=_BASE + timedelta(seconds=1),
            ),
        ])

        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/A")

        assert response.status_code == 200
        payload = response.json()
        assert payload["assistant_request_id"] == "A"
        assert [v["request_id"] for v in payload["llm_usage"]] == [
            "chatcmpl-P1", "chatcmpl-P2",
        ]
        assert payload["tool_executions"] == []
        assert tuple(payload["llm_usage"][0]) == _LLM_FIELDS
        assert payload["llm_usage"][0]["created_at"].startswith("2026-09-28T18:00:00")

    def test_2_tool_only(self, trace_api) -> None:
        trace_api(tool_records=[_tool_record(request_id="A")])

        with TestClient(app) as client:
            payload = client.get(f"{_ENDPOINT}/A").json()

        assert payload["llm_usage"] == []
        assert len(payload["tool_executions"]) == 1
        assert tuple(payload["tool_executions"][0]) == _TOOL_FIELDS
        assert payload["tool_executions"][0]["request_id"] == "A"

    def test_3_llm_and_tool(self, trace_api) -> None:
        trace_api(
            views=[_llm_view(id=1), _llm_view(id=2, request_id="chatcmpl-P2")],
            tool_records=[_tool_record(request_id="A")],
        )

        with TestClient(app) as client:
            payload = client.get(f"{_ENDPOINT}/A").json()

        assert len(payload["llm_usage"]) == 2
        assert len(payload["tool_executions"]) == 1
        assert all(
            v["assistant_request_id"] == "A" for v in payload["llm_usage"]
        )
        assert all(
            t["request_id"] == "A" for t in payload["tool_executions"]
        )

    def test_4_empty_trace_is_200(self, trace_api) -> None:
        trace_api()

        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/not-exist")

        assert response.status_code == 200                 # 不是 404
        assert response.json() == {
            "assistant_request_id": "not-exist",
            "llm_usage": [],
            "tool_executions": [],
        }

    def test_ordering_preserved(self, trace_api) -> None:
        """HTTP 层只做 tuple → list（不重排）。"""
        trace_api(
            views=[
                _llm_view(id=11, request_id="chatcmpl-P1", created_at=_BASE),
                _llm_view(
                    id=12, request_id="chatcmpl-P2",
                    created_at=_BASE + timedelta(seconds=1),
                ),
                _llm_view(
                    id=13, request_id="chatcmpl-P3",
                    created_at=_BASE + timedelta(seconds=2),
                ),
            ],
            tool_records=[
                _tool_record(request_id="A", round_=1),
                _tool_record(request_id="A", round_=2),
            ],
        )

        with TestClient(app) as client:
            payload = client.get(f"{_ENDPOINT}/A").json()

        assert [v["request_id"] for v in payload["llm_usage"]] == [
            "chatcmpl-P1", "chatcmpl-P2", "chatcmpl-P3",
        ]
        assert [t["round"] for t in payload["tool_executions"]] == [1, 2]

    def test_traces_do_not_mix(self, trace_api) -> None:
        trace_api(
            views=[_llm_view(id=1, assistant_request_id="A")],
            tool_records=[
                _tool_record(request_id="A"),
                _tool_record(request_id="B", round_=2),
            ],
        )

        with TestClient(app) as client:
            payload = client.get(f"{_ENDPOINT}/A").json()

        assert payload["tool_executions"][0]["request_id"] == "A"
        assert "B" not in {t["request_id"] for t in payload["tool_executions"]}


# ============================================================
# Test 5~6：校验 / 错误
# ============================================================

class TestTraceErrors:
    @pytest.mark.parametrize("bad", ["%20%20", "%09", "%20"])
    def test_5_blank_id_is_400_without_downstream_call(
        self, trace_api, bad
    ) -> None:
        """纯空白 → 400（服务层 ValueError）；LLM 下游零调用。"""
        llm, _collector = trace_api()

        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/{bad}")

        assert response.status_code in {400, 422}      # 项目既有输入校验语义
        assert response.json()["detail"]
        assert llm.calls == []                         # 下游未被调用

    def test_5_too_long_id_is_422(self, trace_api) -> None:
        llm, _collector = trace_api()

        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/{'x' * 129}")

        assert response.status_code == 422
        assert llm.calls == []

    def test_5_missing_id_is_404(self, trace_api) -> None:
        trace_api()

        with TestClient(app) as client:
            assert client.get(f"{_ENDPOINT}/").status_code == 404

    def test_6_repository_error_is_502_not_empty(self, trace_api) -> None:
        from backend.app.services.llm_usage_query_service import (
            LLMUsageQueryInputError,
        )

        trace_api(service=_FakeTraceService(
            error=LLMUsageRepositoryError("db down")
        ))

        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/A")

        assert response.status_code == 502
        assert set(response.json()) == {"detail"}
        assert "llm_usage" not in response.text            # 未降级为空 Trace
        assert LLMUsageQueryInputError is not None

    def test_6_unexpected_error_is_500(self, trace_api) -> None:
        trace_api(service=_FakeTraceService(error=RuntimeError("boom")))

        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/A")

        assert response.status_code == 500
        assert set(response.json()) == {"detail"}
        assert "Traceback" not in response.text
        assert "boom" not in response.text                 # 不暴露异常消息

    def test_6_value_error_is_400(self, trace_api) -> None:
        trace_api(service=_FakeTraceService(error=ValueError("非法输入: x")))

        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/A")

        assert response.status_code == 400


# ============================================================
# Test 7~8：安全 / 显式映射
# ============================================================

class TestTraceSecurity:
    def test_7_no_sensitive_fields(self, trace_api) -> None:
        trace_api(
            views=[_llm_view()],
            tool_records=[_tool_record(request_id="A")],
        )

        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/A")

        assert response.status_code == 200
        # 键级断言（避免 prompt_tokens 等合法字段误判）
        for forbidden_key in (
            "prompt", "messages", "arguments", "raw_response", "secret",
            "password", "api_key", "authorization", "database_url", "sql",
            "session", "connection", "traceback", "handler", "registry",
        ):
            assert f'"{forbidden_key}":' not in response.text, forbidden_key

    def test_8_extra_dto_fields_not_exposed(self, trace_api) -> None:
        """显式映射：duck-typed DTO 的额外字段不会进入响应。"""
        trace_api(service=_FakeTraceService(view=_DuckTraceView()))

        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/A")

        assert response.status_code == 200
        payload = response.json()
        assert tuple(payload["llm_usage"][0]) == _LLM_FIELDS
        assert tuple(payload["tool_executions"][0]) == _TOOL_FIELDS
        for forbidden_key in (
            "prompt", "messages", "arguments", "raw_response", "api_key",
            "data", "sql", "secret", "internal_debug",
        ):
            assert f'"{forbidden_key}":' not in response.text, forbidden_key
        # 额外字段的**值**同样不得泄漏
        for leaked_value in (
            "SYSTEM-PROMPT-LEAK", "RAW-LEAK", "sk-LEAK", "SELECT 1",
            "top-secret", "LEAK",
        ):
            assert leaked_value not in response.text, leaked_value

    def test_api_module_has_no_implicit_projection(self) -> None:
        identifiers = _identifiers(_API_MODULE)
        for forbidden in (
            "vars", "asdict", "model_dump", "session", "engine", "select",
            "execute", "collector", "repository",
        ):
            assert forbidden not in identifiers, forbidden
        assert "__dict__" not in identifiers
        source = _source(_API_MODULE)
        assert "InMemoryToolExecutionCollector" not in source

    def test_wiring_uses_application_collector(self, monkeypatch) -> None:
        """HTTP accessor 复用**同一个**应用级 Collector（不创建第二个）。"""
        monkeypatch.setattr(root, "_TOOL_EXECUTION_COLLECTOR", root._TOOL_EXECUTION_COLLECTOR)
        service = root.get_assistant_trace_query_service()

        assert (
            service.tool_observability_query_service.collector
            is root._TOOL_EXECUTION_COLLECTOR
        )


# ============================================================
# Test 9：既有 API 回归 + OpenAPI
# ============================================================

class TestRegressionAndOpenAPI:
    def test_9_existing_endpoints_unchanged(self) -> None:
        with TestClient(app) as client:
            records = client.get("/api/observability/tools")
            metrics = client.get("/api/observability/tools/metrics")
            history = client.get("/api/observability/tools/history")
            persistent = client.get(
                "/api/observability/tools/metrics/persistent"
            )
            chat_missing_body = client.post("/api/ai/chat", json={})
            analytics = client.get("/api/usage/analytics")

        assert records.status_code == 200
        assert records.json() == {"records": []}
        assert metrics.status_code == 200
        assert len(metrics.json()) == 8
        assert history.status_code in {200, 502}
        assert persistent.status_code in {200, 502}
        assert chat_missing_body.status_code == 422
        assert analytics.status_code in {200, 422, 502}

    def test_openapi_contains_endpoint_with_safe_schema(self) -> None:
        spec = app.openapi()
        path = "/api/observability/assistant-trace/{assistant_request_id}"

        assert path in spec["paths"]
        operation = spec["paths"][path]["get"]
        assert operation["responses"]["200"]["content"][
            "application/json"
        ]["schema"] == {"$ref": "#/components/schemas/AssistantTraceResponse"}
        components = spec["components"]["schemas"]
        assert list(components["AssistantTraceResponse"]["properties"]) == [
            "assistant_request_id", "llm_usage", "tool_executions",
        ]
        assert list(components["LLMUsageTraceResponse"]["properties"]) == list(
            _LLM_FIELDS
        )
        assert list(
            components["ToolExecutionTraceResponse"]["properties"]
        ) == list(_TOOL_FIELDS)
        # 只检查本端点 + 其 schema（全 App spec 的其它端点文案不属本阶段范围）
        subset_text = str({
            "operation": operation,
            "schemas": {
                name: components[name]
                for name in (
                    "AssistantTraceResponse",
                    "LLMUsageTraceResponse",
                    "ToolExecutionTraceResponse",
                )
            },
        })
        for forbidden in (
            "password", "api_key", "arguments", "raw_response", "secret",
        ):
            assert f'"{forbidden}"' not in subset_text, forbidden

    def test_params_are_path_only(self) -> None:
        import inspect

        parameters = set(
            inspect.signature(
                trace_module.get_assistant_trace
            ).parameters
        )
        assert parameters == {"assistant_request_id"}
        for forbidden in (
            "route", "provider", "model", "tool_name", "limit", "offset",
            "page", "page_size", "cursor", "sort", "from_", "to",
        ):
            assert forbidden not in parameters, forbidden


__all__ = [
    "TestTraceEndpoint",
    "TestTraceErrors",
    "TestTraceSecurity",
    "TestRegressionAndOpenAPI",
]
