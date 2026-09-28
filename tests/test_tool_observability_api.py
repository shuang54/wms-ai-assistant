"""Tool Observability Read API 测试（Phase 3.11 Step 25）。

    GET /api/observability/tools
    GET /api/observability/tools/metrics

覆盖（§十五）：

    Records API   200 / empty / 单条成功 / 单条失败 / 多条 / retention /
                  被淘汰不复现 / datetime 为字符串 / None 保持 null /
                  字段白名单 / 不暴露 Collector / 不暴露 Record 内部
    Metrics API   200 / empty / 成功失败统计 / None 语义 / 字段白名单 /
                  不含 identifier
    Architecture  只经 QueryService / 不 import Collector·边界·Registry·
                  Handler / 使用序列化边界 / 不计算指标
    Composition   同一 Application Collector / POST 执行 → GET 可见 /
                  metrics 反映执行 / RAG 不产生 Tool Record
    Security      无 SQL / 无凭据 / 无 traceback / 无 arguments / 无 result data

0 DB / 0 Network / 0 Real LLM（Fake Tool + 内存 Collector）。
"""
from __future__ import annotations

import ast
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import orchestrator_chat as root
from backend.app.api import tool_observability as api_module
from backend.app.main import app
from backend.app.services.in_memory_tool_execution_collector import (
    InMemoryToolExecutionCollector,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.services.tool_observability_query_service import (
    ToolObservabilityQueryService,
)
from backend.app.tools.registry import ToolRegistry

# 复用既有测试资产（Fakes / decisions），不重复创建
from tests.test_ai_orchestrator import (  # noqa: E402
    FakeContextComposer,
    FakeProjectProvider,
    FakeRAG,
    FakeRouter,
    FakeSQLExecutor,
    FakeTableSelector,
    FakeTextToSQL,
    _FakeRagResponse,
    _rag_decision,
    _tool_decision,
)
from backend.app.services.ai_orchestrator_service import (  # noqa: E402
    AIOrchestratorService,
)

_API_MODULE = "backend/app/api/tool_observability.py"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STARTED = datetime(2026, 9, 26, 10, 20, 30, 123456, tzinfo=timezone.utc)

#: Snapshot 的 11 个字段（§五）
_EXPECTED_RECORD_FIELDS = (
    "request_id", "round", "tool_name", "started_at", "finished_at",
    "duration_ms", "success", "project_id", "tool_call_id", "error_code",
    "error_type",
)
#: Metrics 的 8 个字段（§六）
_EXPECTED_METRICS_FIELDS = (
    "total_count", "success_count", "failure_count", "success_rate",
    "failure_rate", "total_duration_ms", "average_duration_ms",
    "max_duration_ms",
)


# ============================================================
# helpers
# ============================================================

def _record(
    *,
    request_id: str = "req-1",
    round: int = 1,
    tool_name: str = "get_inventory",
    project_id: str | None = "project-a",
    tool_call_id: str | None = None,
    success: bool = True,
    duration_ms: float = 12.5,
) -> ToolExecutionRecord:
    return ToolExecutionRecord(
        request_id=request_id,
        round=round,
        tool_name=tool_name,
        started_at=_STARTED,
        finished_at=_STARTED + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        success=success,
        project_id=project_id,
        tool_call_id=tool_call_id,
        error_type=None if success else "ToolValidationError",
    )


def _seed(*records: ToolExecutionRecord) -> None:
    collector = root._TOOL_EXECUTION_COLLECTOR
    for record in records:
        collector.on_execution(record)


def _tree() -> ast.Module:
    with open(
        os.path.join(_REPO_ROOT, *_API_MODULE.split("/")), encoding="utf-8"
    ) as handle:
        return ast.parse(handle.read())


def _identifiers() -> set[str]:
    tree = _tree()
    return {
        node.id.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    } | {
        node.attr.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }


def _imported_modules() -> set[str]:
    tree = _tree()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def _registry_with_handler(result: dict[str, Any] | None = None):
    from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION

    handler_calls: list[dict[str, Any]] = []

    async def handler(arguments: dict[str, Any]) -> dict[str, Any]:
        handler_calls.append(arguments)
        return result if result is not None else {"qty": 250.0}

    registry = ToolRegistry()
    registry.register(GET_INVENTORY_DEFINITION, handler)
    return registry, handler_calls


@pytest.fixture()
def client(monkeypatch):
    """TestClient（Application 级 Collector 清空 + 结束后复原）。"""
    root._TOOL_EXECUTION_COLLECTOR.clear()
    with TestClient(app) as test_client:
        yield test_client
    root._TOOL_EXECUTION_COLLECTOR.clear()


# ============================================================
# Records API
# ============================================================

class TestRecordsApi:
    def test_get_returns_200_and_envelope(self, client) -> None:
        response = client.get("/api/observability/tools")

        assert response.status_code == 200
        assert set(response.json()) == {"records"}
        assert isinstance(response.json()["records"], list)

    def test_empty_collector_returns_empty_list(self, client) -> None:
        response = client.get("/api/observability/tools")

        assert response.json() == {"records": []}

    def test_one_successful_record(self, client) -> None:
        _seed(_record(request_id="req-ok"))

        payload = client.get("/api/observability/tools").json()

        assert len(payload["records"]) == 1
        record = payload["records"][0]
        assert record["request_id"] == "req-ok"
        assert record["round"] == 1
        assert record["tool_name"] == "get_inventory"
        assert record["success"] is True
        assert record["project_id"] == "project-a"
        assert record["tool_call_id"] is None
        assert record["error_type"] is None

    def test_one_failed_record(self, client) -> None:
        _seed(_record(request_id="req-fail", success=False))

        record = client.get("/api/observability/tools").json()["records"][0]

        assert record["success"] is False
        assert record["error_type"] == "ToolValidationError"

    def test_multiple_records_keep_write_order(self, client) -> None:
        _seed(
            _record(request_id="req-A"),
            _record(request_id="req-B"),
            _record(request_id="req-C", success=False),
        )

        records = client.get("/api/observability/tools").json()["records"]

        assert [r["request_id"] for r in records] == ["req-A", "req-B", "req-C"]

    def test_retention_respected(self, client, monkeypatch) -> None:
        """retention window 之外的记录不返回（max_records=2）。"""
        collector = InMemoryToolExecutionCollector(max_records=2)
        for request_id in ("req-1", "req-2", "req-3"):
            collector.on_execution(_record(request_id=request_id))
        monkeypatch.setattr(
            api_module,
            "get_tool_observability_query_service",
            lambda: ToolObservabilityQueryService(collector),
        )

        records = client.get("/api/observability/tools").json()["records"]

        assert [r["request_id"] for r in records] == ["req-2", "req-3"]

    def test_evicted_record_not_returned(self, client, monkeypatch) -> None:
        collector = InMemoryToolExecutionCollector(max_records=1)
        collector.on_execution(_record(request_id="req-old"))
        collector.on_execution(_record(request_id="req-new"))
        monkeypatch.setattr(
            api_module,
            "get_tool_observability_query_service",
            lambda: ToolObservabilityQueryService(collector),
        )

        payload = client.get("/api/observability/tools").json()

        assert [r["request_id"] for r in payload["records"]] == ["req-new"]
        assert "req-old" not in payload.__str__()

    def test_datetime_is_iso_string(self, client) -> None:
        _seed(_record())

        record = client.get(
            "/api/observability/tools"
        ).json()["records"][0]

        assert isinstance(record["started_at"], str)
        assert record["started_at"] == "2026-09-26T10:20:30.123456+00:00"
        assert record["finished_at"].endswith("+00:00")

    def test_none_fields_remain_null(self, client) -> None:
        _seed(_record(project_id=None, tool_call_id=None))

        record = client.get(
            "/api/observability/tools"
        ).json()["records"][0]
        raw = client.get("/api/observability/tools").text

        assert record["project_id"] is None
        assert record["tool_call_id"] is None
        assert record["error_code"] is None
        assert "null" in raw

    def test_response_field_whitelist(self, client) -> None:
        _seed(_record())

        record = client.get(
            "/api/observability/tools"
        ).json()["records"][0]

        assert tuple(record) == _EXPECTED_RECORD_FIELDS
        assert set(record) == set(_EXPECTED_RECORD_FIELDS)

    def test_api_does_not_expose_collector(self, client) -> None:
        payload = client.get("/api/observability/tools").json()

        assert "collector" not in payload
        assert "max_records" not in payload.__str__().lower()

    def test_api_does_not_expose_record_internals(self, client) -> None:
        _seed(_record())

        record = client.get(
            "/api/observability/tools"
        ).json()["records"][0]

        for forbidden in (
            "arguments", "result", "data", "handler", "registry", "sql",
            "engine", "session", "traceback",
        ):
            assert forbidden not in record, forbidden


# ============================================================
# Metrics API
# ============================================================

class TestMetricsApi:
    def test_get_returns_200(self, client) -> None:
        response = client.get("/api/observability/tools/metrics")

        assert response.status_code == 200

    def test_empty_metrics_are_null_not_zero(self, client) -> None:
        payload = client.get("/api/observability/tools/metrics").json()

        assert payload == {
            "total_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "success_rate": None,
            "failure_rate": None,
            "total_duration_ms": 0.0,
            "average_duration_ms": None,
            "max_duration_ms": None,
        }
        assert "null" in client.get("/api/observability/tools/metrics").text

    def test_success_and_failure_metrics(self, client) -> None:
        _seed(
            _record(duration_ms=10.0),
            _record(duration_ms=20.0),
            _record(duration_ms=30.0, success=False),
        )

        payload = client.get("/api/observability/tools/metrics").json()

        assert payload["total_count"] == 3
        assert payload["success_count"] == 2
        assert payload["failure_count"] == 1
        assert payload["success_rate"] == pytest.approx(2 / 3)
        assert payload["failure_rate"] == pytest.approx(1 / 3)
        assert payload["total_duration_ms"] == 60.0
        assert payload["average_duration_ms"] == 20.0
        assert payload["max_duration_ms"] == 30.0

    def test_none_semantics_not_converted(self, client) -> None:
        _seed(_record(success=False))

        payload = client.get("/api/observability/tools/metrics").json()

        # 全部失败：success_rate = 0.0（有数据），不是 None
        assert payload["success_rate"] == 0.0
        assert payload["failure_rate"] == 1.0
        # 空数据：必须 None（对比）
        root._TOOL_EXECUTION_COLLECTOR.clear()
        empty = client.get("/api/observability/tools/metrics").json()
        assert empty["success_rate"] is None
        assert empty["success_rate"] != 0

    def test_response_field_whitelist(self, client) -> None:
        _seed(_record())

        payload = client.get("/api/observability/tools/metrics").json()

        assert tuple(payload) == _EXPECTED_METRICS_FIELDS
        assert set(payload) == set(_EXPECTED_METRICS_FIELDS)

    def test_metrics_excludes_identifiers(self, client) -> None:
        _seed(
            _record(request_id="req-secret", project_id="project-secret"),
            _record(tool_name="get_work_order"),
        )

        payload = client.get("/api/observability/tools/metrics").json()
        text = client.get("/api/observability/tools/metrics").text

        for forbidden in ("req-secret", "project-secret", "get_work_order"):
            assert forbidden not in text, forbidden
        for key in ("request_id", "project_id", "tool_name"):
            assert key not in payload, key


# ============================================================
# Architecture Boundary（静态）
# ============================================================

class TestApiArchitectureBoundary:
    def test_api_uses_query_service_accessor(self) -> None:
        identifiers = _identifiers()

        assert "get_tool_observability_query_service" in identifiers
        assert "snapshots" in identifiers
        assert "metrics" in identifiers

    def test_api_does_not_import_collector_or_record(self) -> None:
        imported = _imported_modules()

        for forbidden in (
            "backend.app.services.in_memory_tool_execution_collector",
            "backend.app.services.tool_execution_record",
            "backend.app.services.tool_execution_service",
            "backend.app.services.tool_execution_observer",
            "backend.app.services.ai_orchestrator_service",
            "backend.app.services.ai_router_service",
            "backend.app.tools",
            "backend.app.db",
            "backend.app.llm",
            "sqlalchemy",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imported
            ), forbidden

    def test_api_does_not_import_registry_or_handler(self) -> None:
        imported = _imported_modules()

        assert not any(
            name.startswith("backend.app.tools") for name in imported
        ), imported
        identifiers = _identifiers()
        for forbidden in ("registry", "handler", "execute_tool"):
            assert forbidden not in identifiers, forbidden

    def test_api_uses_serialization_boundary(self) -> None:
        identifiers = _identifiers()

        assert "snapshot_to_dict" in identifiers
        assert "metrics_to_dict" in identifiers
        # 不自己实现 isoformat / 字段映射
        assert "isoformat" not in identifiers
        assert "asdict" not in identifiers
        assert "__dict__" not in identifiers

    def test_api_does_not_calculate_metrics(self) -> None:
        tree = _tree()
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp):
                assert not isinstance(
                    node.op, (ast.Div, ast.FloorDiv)
                ), "API 不得自行计算比率 / 均值"
        identifiers = _identifiers()
        for forbidden in ("sum", "max", "min", "mean", "statistics"):
            assert forbidden not in identifiers, forbidden


# ============================================================
# Composition / Integration
# ============================================================

class TestCompositionAndIntegration:
    def test_api_uses_same_application_collector(self) -> None:
        query = api_module.get_tool_observability_query_service()

        assert query.collector is root._TOOL_EXECUTION_COLLECTOR

    def test_seeded_records_are_visible(self, client) -> None:
        _seed(_record(request_id="req-shared"))

        records = client.get("/api/observability/tools").json()["records"]

        assert [r["request_id"] for r in records] == ["req-shared"]

    def test_post_chat_then_get_records(
        self, client, monkeypatch
    ) -> None:
        """真实 TestClient：POST /api/ai/chat → Tool 执行 → GET 可见。"""
        registry, handler_calls = _registry_with_handler()
        orchestrator = AIOrchestratorService(
            router=FakeRouter(_tool_decision()),
            rag_service=FakeRAG(),
            text_to_sql=FakeTextToSQL(),
            sql_executor=FakeSQLExecutor(),
            table_selector=FakeTableSelector(),
            context_composer=FakeContextComposer(),
            project_context_provider=FakeProjectProvider(),
            tool_registry=registry,
            tool_execution_observer=root._TOOL_EXECUTION_COLLECTOR,
        )
        monkeypatch.setattr(root, "_default_orchestrator", orchestrator)

        post = client.post(
            "/api/ai/chat", json={"question": "查询物料 MAT-001 当前库存"}
        )
        assert post.status_code == 200
        assert handler_calls, "Tool Handler 应被调用一次"

        records = client.get("/api/observability/tools").json()["records"]

        assert len(records) == 1
        assert records[0]["tool_name"] == "get_inventory"
        assert records[0]["success"] is True
        assert records[0]["round"] == 1
        assert records[0]["tool_call_id"] is None

    def test_metrics_reflect_tool_execution(self, client, monkeypatch) -> None:
        registry, _calls = _registry_with_handler()
        orchestrator = AIOrchestratorService(
            router=FakeRouter(_tool_decision()),
            rag_service=FakeRAG(),
            text_to_sql=FakeTextToSQL(),
            sql_executor=FakeSQLExecutor(),
            table_selector=FakeTableSelector(),
            context_composer=FakeContextComposer(),
            project_context_provider=FakeProjectProvider(),
            tool_registry=registry,
            tool_execution_observer=root._TOOL_EXECUTION_COLLECTOR,
        )
        monkeypatch.setattr(root, "_default_orchestrator", orchestrator)

        client.post(
            "/api/ai/chat", json={"question": "查询物料 MAT-001 当前库存"}
        )
        payload = client.get("/api/observability/tools/metrics").json()

        assert payload["total_count"] == 1
        assert payload["success_count"] == 1
        assert payload["failure_count"] == 0
        assert payload["success_rate"] == 1.0
        assert payload["max_duration_ms"] is not None

    def test_rag_execution_creates_no_tool_record(
        self, client, monkeypatch
    ) -> None:
        registry, handler_calls = _registry_with_handler()
        orchestrator = AIOrchestratorService(
            router=FakeRouter(_rag_decision()),
            rag_service=FakeRAG(response=_FakeRagResponse("流程说明")),
            text_to_sql=FakeTextToSQL(),
            sql_executor=FakeSQLExecutor(),
            table_selector=FakeTableSelector(),
            context_composer=FakeContextComposer(),
            project_context_provider=FakeProjectProvider(),
            tool_registry=registry,
            tool_execution_observer=root._TOOL_EXECUTION_COLLECTOR,
        )
        monkeypatch.setattr(root, "_default_orchestrator", orchestrator)

        post = client.post("/api/ai/chat", json={"question": "采购入库怎么操作？"})

        assert post.status_code == 200
        assert handler_calls == []
        assert client.get("/api/observability/tools").json() == {"records": []}
        assert (
            client.get("/api/observability/tools/metrics").json()["total_count"]
            == 0
        )


# ============================================================
# Security
# ============================================================

class TestApiSecurity:
    def test_no_sql_or_credentials(self, client) -> None:
        _seed(
            _record(request_id="req-1"),
            _record(success=False, request_id="req-2"),
        )
        text = (
            client.get("/api/observability/tools").text
            + client.get("/api/observability/tools/metrics").text
        ).upper()

        for forbidden in (
            "SELECT ", "POSTGRESQL://", "PASSWORD", "API_KEY",
            "AUTHORIZATION", "BEARER ", "DSN", "DATABASE_URL",
        ):
            assert forbidden not in text, forbidden

    def test_no_traceback_or_internal_paths(self, client) -> None:
        text = client.get("/api/observability/tools").text

        for forbidden in (
            "Traceback", "backend/app", "sqlalchemy", "psycopg", ".py",
        ):
            assert forbidden not in text, forbidden

    def test_no_tool_arguments_or_result_data(self, client, monkeypatch) -> None:
        registry, _calls = _registry_with_handler(
            {"qty": 250.0, "marker": "DATA-LEAK-MARKER"}
        )
        orchestrator = AIOrchestratorService(
            router=FakeRouter(_tool_decision()),
            rag_service=FakeRAG(),
            text_to_sql=FakeTextToSQL(),
            sql_executor=FakeSQLExecutor(),
            table_selector=FakeTableSelector(),
            context_composer=FakeContextComposer(),
            project_context_provider=FakeProjectProvider(),
            tool_registry=registry,
            tool_execution_observer=root._TOOL_EXECUTION_COLLECTOR,
        )
        monkeypatch.setattr(root, "_default_orchestrator", orchestrator)
        client.post(
            "/api/ai/chat", json={"question": "查询物料 MAT-001 当前库存"}
        )

        text = client.get("/api/observability/tools").text

        for forbidden in (
            "MAT-001", "DATA-LEAK-MARKER", "material_code", "qty",
        ):
            assert forbidden not in text, forbidden
