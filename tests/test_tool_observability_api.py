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
from backend.app.db.tool_execution_repository import (
    ToolExecutionRepositoryError,
)
from backend.app.services.tool_observability_query_service import (
    ToolObservabilityQueryService,
)
from backend.app.services.tool_execution_metrics_service import (
    ToolExecutionMetricsService,
    ToolExecutionMetricsSnapshot,
)
from backend.app.services.tool_observability_snapshot import (
    ToolExecutionSnapshot,
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
        """API 只经 accessor 拿 QueryService / PersistentQueryService。

        Step 30：允许的唯一 db 依赖 = ``ToolExecutionRepositoryError`` 类型
        （错误映射；与 ``api/usage.py`` 导入 ``LLMUsageRepositoryError``
        的既有 precedent 一致）—— 不得 import Repository 类 / Session /
        ORM Model / SQLAlchemy。
        """
        imported = _imported_modules()

        for forbidden in (
            "backend.app.services.in_memory_tool_execution_collector",
            "backend.app.services.tool_execution_record",
            "backend.app.services.tool_execution_service",
            "backend.app.services.tool_execution_observer",
            "backend.app.services.ai_orchestrator_service",
            "backend.app.services.ai_router_service",
            "backend.app.tools",
            "backend.app.db.session",
            "backend.app.db.models",
            "backend.app.llm",
            "sqlalchemy",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imported
            ), forbidden
        # 唯一允许的 db 模块 = 仓储错误类型所在模块
        db_imports = {
            name for name in imported if name.startswith("backend.app.db")
        }
        assert db_imports == {"backend.app.db.tool_execution_repository"}

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


# ============================================================
# Persistent History API（Phase 3.11 Step 30）
# ============================================================

_HISTORY_FIELDS = (
    "request_id", "round", "tool_name", "started_at", "finished_at",
    "duration_ms", "success", "project_id", "tool_call_id", "error_code",
    "error_type",
)


def _snapshot_from(record: ToolExecutionRecord) -> ToolExecutionSnapshot:
    return ToolExecutionSnapshot.from_record(record)


class _FakePersistentQueryService:
    """记录分页 / 过滤参数 + 返回预置快照 / 可注入异常（duck-typed；无需 DB）。"""

    def __init__(
        self,
        snapshots: list[ToolExecutionSnapshot] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._snapshots = snapshots or []
        self._error = error
        self.calls: list[tuple[int, int]] = []
        self.filters: list[dict[str, object]] = []
        self.metrics_calls: list[dict[str, object]] = []
        self._metrics_snapshot = ToolExecutionMetricsService.snapshot(())
        self._metrics_error: Exception | None = None

    def _set_metrics(
        self,
        snapshot: ToolExecutionMetricsSnapshot,
        error: Exception | None = None,
    ) -> None:
        self._metrics_snapshot = snapshot
        self._metrics_error = error

    def list_recent(
        self,
        *,
        limit: int,
        offset: int = 0,
        project_id: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> list[ToolExecutionSnapshot]:
        self.calls.append((limit, offset))
        self.filters.append({
            "project_id": project_id,
            "tool_name": tool_name,
            "success": success,
        })
        if self._error is not None:
            raise self._error
        return list(self._snapshots[offset: offset + limit])

    # ---- Step 33：Persistent Metrics ----

    def metrics(
        self,
        *,
        project_id: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> ToolExecutionMetricsSnapshot:
        self.metrics_calls.append({
            "project_id": project_id,
            "tool_name": tool_name,
            "success": success,
        })
        if self._metrics_error is not None:
            raise self._metrics_error
        return self._metrics_snapshot


@pytest.fixture()
def history(monkeypatch):
    """返回 (client, 装配 fake persistent query service 的工厂)。"""
    root._TOOL_EXECUTION_COLLECTOR.clear()

    def _install(
        snapshots: list[ToolExecutionSnapshot] | None = None,
        error: Exception | None = None,
    ) -> _FakePersistentQueryService:
        service = _FakePersistentQueryService(snapshots, error)
        monkeypatch.setattr(
            api_module,
            "get_tool_execution_persistent_query_service",
            lambda: service,
        )
        return service

    with TestClient(app) as test_client:
        yield test_client, _install
    root._TOOL_EXECUTION_COLLECTOR.clear()


class TestPersistentHistoryApi:
    def test_empty_history_returns_200_with_empty_items(self, history) -> None:
        client, install = history
        service = install([])

        response = client.get("/api/observability/tools/history")

        assert response.status_code == 200
        assert response.json() == {
            "items": [], "limit": 100, "offset": 0
        }
        assert service.calls == [(100, 0)]         # 默认 limit + offset

    def test_one_record(self, history) -> None:
        client, install = history
        record = _record(request_id="step30-test-001")
        install([_snapshot_from(record)])

        payload = client.get("/api/observability/tools/history").json()

        assert len(payload["items"]) == 1
        item = payload["items"][0]
        assert item["request_id"] == "step30-test-001"
        assert item["round"] == 1
        assert item["tool_name"] == "get_inventory"
        assert item["success"] is True
        assert item["project_id"] == "project-a"
        assert item["duration_ms"] == 12.5
        assert item["started_at"] == "2026-09-26T10:20:30.123456+00:00"

    def test_multiple_records_keep_service_order(self, history) -> None:
        client, install = history
        install([
            _snapshot_from(_record(request_id="step30-new")),
            _snapshot_from(_record(request_id="step30-old")),
        ])

        items = client.get("/api/observability/tools/history").json()["items"]

        assert [i["request_id"] for i in items] == [
            "step30-new", "step30-old"
        ]

    def test_limit_values(self, history) -> None:
        client, install = history
        service = install([_snapshot_from(_record())])

        for value in (1, 100, 1000):
            response = client.get(
                f"/api/observability/tools/history?limit={value}"
            )
            assert response.status_code == 200
        assert service.calls == [(1, 0), (100, 0), (1000, 0)]

    # ---- Step 31：offset（分页） ----

    def test_offset_values(self, history) -> None:
        client, install = history
        service = install([_snapshot_from(_record())])

        for value in (0, 1, 100):
            response = client.get(
                f"/api/observability/tools/history?offset={value}"
            )
            assert response.status_code == 200
            assert response.json()["offset"] == value
        assert service.calls == [(100, 0), (100, 1), (100, 100)]

    def test_limit_and_offset_together(self, history) -> None:
        client, install = history
        service = install([])

        payload = client.get(
            "/api/observability/tools/history?limit=2&offset=2"
        ).json()

        assert payload == {"items": [], "limit": 2, "offset": 2}
        assert service.calls == [(2, 2)]

    def test_empty_page_returns_200(self, history) -> None:
        """offset 超出记录数 → 200 + items=[]（不是 404）。"""
        client, install = history
        install([_snapshot_from(_record(request_id="only-one"))])

        response = client.get(
            "/api/observability/tools/history?limit=100&offset=100"
        )

        assert response.status_code == 200
        assert response.json() == {
            "items": [], "limit": 100, "offset": 100
        }

    def test_page_consistency_via_api(self, history) -> None:
        """分页一致性：三页拼起来 == 一次取全（无重复 / 无遗漏 / 顺序稳定）。"""
        client, install = history
        install([
            _snapshot_from(_record(request_id=f"step31-{index}"))
            for index in range(5)
        ])

        page1 = client.get(
            "/api/observability/tools/history?limit=2&offset=0"
        ).json()["items"]
        page2 = client.get(
            "/api/observability/tools/history?limit=2&offset=2"
        ).json()["items"]
        page3 = client.get(
            "/api/observability/tools/history?limit=2&offset=4"
        ).json()["items"]
        whole = client.get(
            "/api/observability/tools/history?limit=5&offset=0"
        ).json()["items"]

        assert [i["request_id"] for i in page1 + page2 + page3] == [
            i["request_id"] for i in whole
        ]
        assert len(page3) == 1

    def test_invalid_limit_returns_422_not_500(self, history) -> None:
        client, install = history
        service = install([])

        for bad in ("0", "-1", "1001", "abc"):
            response = client.get(
                f"/api/observability/tools/history?limit={bad}"
            )
            assert response.status_code == 422, bad
        assert service.calls == []                 # 校验在进入端点之前

    def test_invalid_offset_returns_422_without_touching_service(
        self, history
    ) -> None:
        client, install = history
        service = install([])

        for bad in ("-1", "-100", "abc"):
            response = client.get(
                f"/api/observability/tools/history?offset={bad}"
            )
            assert response.status_code == 422, bad
        assert service.calls == []                 # Service / Repository / DB 未触达

    def test_large_offset_is_valid(self, history) -> None:
        client, install = history
        service = install([])

        response = client.get(
            "/api/observability/tools/history?limit=1&offset=999999999"
        )

        assert response.status_code == 200
        assert response.json() == {
            "items": [], "limit": 1, "offset": 999_999_999
        }
        assert service.calls == [(1, 999_999_999)]

    def test_db_failure_returns_502_without_fallback(self, history) -> None:
        """DB failure ≠ empty：502，且**绝不**回退到内存 Collector。"""
        client, install = history
        install(error=ToolExecutionRepositoryError("db down"))
        _seed(_record(request_id="runtime-only-A"))   # 内存里有数据

        response = client.get("/api/observability/tools/history")

        assert response.status_code == 502
        assert "runtime-only-A" not in response.text
        assert response.json()["detail"] == "Tool 观测历史数据不可用"

    # ---- Step 32：精确过滤（project_id / tool_name / success） ----

    def test_filters_are_forwarded_to_service(self, history) -> None:
        client, install = history
        service = install([_snapshot_from(_record())])

        client.get(
            "/api/observability/tools/history"
            "?project_id=project-a&tool_name=get_inventory&success=true"
            "&limit=20&offset=5"
        )

        assert service.calls == [(20, 5)]
        assert service.filters == [{
            "project_id": "project-a",
            "tool_name": "get_inventory",
            "success": True,
        }]

    def test_success_false_is_forwarded_as_false(self, history) -> None:
        client, install = history
        service = install([])

        client.get("/api/observability/tools/history?success=false")

        assert service.filters[0]["success"] is False  # 不是 None

    def test_no_filters_means_none(self, history) -> None:
        client, install = history
        service = install([])

        client.get("/api/observability/tools/history")

        assert service.filters == [{
            "project_id": None, "tool_name": None, "success": None
        }]

    def test_empty_string_filter_is_forwarded(self, history) -> None:
        client, install = history
        service = install([])

        client.get("/api/observability/tools/history?project_id=&tool_name=")

        assert service.filters[0]["project_id"] == ""
        assert service.filters[0]["tool_name"] == ""

    def test_invalid_success_value_returns_422(self, history) -> None:
        client, install = history
        service = install([])

        assert client.get(
            "/api/observability/tools/history?success=maybe"
        ).status_code == 422
        assert service.calls == []                     # 未进入端点

    def test_filter_and_pagination_combined_response(self, history) -> None:
        client, install = history
        install([
            _snapshot_from(_record(request_id="filtered-1")),
            _snapshot_from(_record(request_id="filtered-2")),
        ])

        payload = client.get(
            "/api/observability/tools/history"
            "?project_id=project-a&limit=1&offset=1"
        ).json()

        assert payload["limit"] == 1
        assert payload["offset"] == 1
        assert [i["request_id"] for i in payload["items"]] == ["filtered-2"]

    def test_runtime_endpoints_unaffected_by_filters(self, history) -> None:
        """Runtime 端点不接受过滤参数（多余参数被忽略），仍只读内存。"""
        client, install = history
        service = install(error=ToolExecutionRepositoryError("db down"))
        _seed(_record(request_id="memory-A"))

        payload = client.get(
            "/api/observability/tools?project_id=does-not-exist"
        ).json()

        assert [r["request_id"] for r in payload["records"]] == ["memory-A"]
        assert service.calls == []

    def test_db_failure_response_has_no_internals(self, history) -> None:
        client, install = history
        install(
            error=ToolExecutionRepositoryError(
                "SELECT * FROM ai_ops.tool_execution_record "
                "postgresql://user:pw@host/db"
            )
        )

        response = client.get("/api/observability/tools/history")
        text = response.text.upper()

        for forbidden in (
            "SELECT", "POSTGRESQL://", "PASSWORD", "TRACEBACK",
            "AI_OPS", "TOOL_EXECUTION_REPOSITORY",
        ):
            assert forbidden not in text, forbidden

    def test_response_field_whitelist(self, history) -> None:
        client, install = history
        install([_snapshot_from(_record())])

        payload = client.get("/api/observability/tools/history").json()
        item = payload["items"][0]

        assert set(payload) == {"items", "limit", "offset"}
        assert tuple(item) == _HISTORY_FIELDS
        for forbidden in (
            "id", "created_at", "database_name", "arguments", "result",
            "data", "sql", "prompt", "llm_response", "exception_message",
            "traceback",
        ):
            assert forbidden not in item, forbidden

    def test_get_only_no_write_endpoints(self) -> None:
        import ast as _ast

        tree = _ast.parse(
            open(
                os.path.join(
                    _REPO_ROOT, "backend", "app", "api",
                    "tool_observability.py",
                ),
                encoding="utf-8",
            ).read()
        )
        methods = {
            node.attr
            for node in _ast.walk(tree)
            if isinstance(node, _ast.Attribute)
            and isinstance(node.value, _ast.Name)
            and node.value.id == "router"
        }
        assert methods == {"get"}, methods

    # ---- Runtime / Persistent 隔离（§十四） ----

    def test_persistent_history_api_does_not_read_memory_collector(
        self, history
    ) -> None:
        """collector 有 A，数据库有 B → /history 只返回 B。"""
        client, install = history
        install([_snapshot_from(_record(request_id="database-B"))])
        _seed(_record(request_id="memory-A"))

        items = client.get("/api/observability/tools/history").json()["items"]

        assert [i["request_id"] for i in items] == ["database-B"]
        assert "memory-A" not in client.get(
            "/api/observability/tools/history"
        ).text

    def test_runtime_history_api_does_not_read_database(self, history) -> None:
        """运行时端点仍只读 Collector：持久服务**从未被调用**。"""
        client, install = history
        service = install(
            error=ToolExecutionRepositoryError("db down")
        )
        _seed(_record(request_id="memory-A"))

        payload = client.get("/api/observability/tools").json()

        assert [r["request_id"] for r in payload["records"]] == ["memory-A"]
        assert service.calls == []                 # 未触达持久化链路
        # metrics 端点同理
        assert (
            client.get("/api/observability/tools/metrics").json()["total_count"]
            == 1
        )
        assert service.calls == []

    def test_history_does_not_modify_collector(self, history) -> None:
        client, install = history
        install([_snapshot_from(_record(request_id="database-B"))])
        _seed(_record(request_id="memory-A"))
        before = root._TOOL_EXECUTION_COLLECTOR.records()

        client.get("/api/observability/tools/history")

        assert root._TOOL_EXECUTION_COLLECTOR.records() == before


# ============================================================
# Persistent Metrics API（Phase 3.11 Step 33）
# ============================================================

_METRICS_PATH = "/api/observability/tools/metrics/persistent"

_METRICS_FIELDS = (
    "total_count", "success_count", "failure_count", "success_rate",
    "failure_rate", "total_duration_ms", "average_duration_ms",
    "max_duration_ms",
)


class TestPersistentMetricsApi:
    def test_empty_metrics_200(self, history) -> None:
        client, install = history
        service = install()

        response = client.get(_METRICS_PATH)

        assert response.status_code == 200
        assert response.json() == {
            "total_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "success_rate": None,
            "failure_rate": None,
            "total_duration_ms": 0.0,
            "average_duration_ms": None,
            "max_duration_ms": None,
        }
        assert service.metrics_calls == [{
            "project_id": None, "tool_name": None, "success": None
        }]

    def test_populated_metrics(self, history) -> None:
        client, install = history
        service = install()
        service._set_metrics(  # noqa: SLF001 —— 测试装配
            ToolExecutionMetricsSnapshot(
                total_count=5,
                success_count=3,
                failure_count=2,
                success_rate=0.6,
                failure_rate=0.4,
                total_duration_ms=600.0,
                average_duration_ms=120.0,
                max_duration_ms=300.0,
            )
        )

        payload = client.get(_METRICS_PATH).json()

        assert payload["total_count"] == 5
        assert payload["success_count"] == 3
        assert payload["failure_count"] == 2
        assert payload["success_rate"] == pytest.approx(0.6)
        assert payload["failure_rate"] == pytest.approx(0.4)
        assert payload["total_duration_ms"] == 600.0
        assert payload["average_duration_ms"] == 120.0
        assert payload["max_duration_ms"] == 300.0

    def test_filters_forwarded(self, history) -> None:
        client, install = history
        service = install()

        client.get(
            f"{_METRICS_PATH}"
            "?project_id=project-a&tool_name=get_inventory&success=true"
        )

        assert service.metrics_calls == [{
            "project_id": "project-a",
            "tool_name": "get_inventory",
            "success": True,
        }]

    def test_success_false_forwarded_as_false(self, history) -> None:
        client, install = history
        service = install()

        client.get(f"{_METRICS_PATH}?success=false")

        assert service.metrics_calls[0]["success"] is False

    def test_injection_string_forwarded_as_literal(self, history) -> None:
        client, install = history
        service = install()

        response = client.get(
            f"{_METRICS_PATH}?project_id=%27%20OR%201%3D1%20--"
        )

        assert response.status_code == 200
        assert service.metrics_calls[0]["project_id"] == "' OR 1=1 --"

    def test_invalid_success_returns_422(self, history) -> None:
        client, install = history
        service = install()

        response = client.get(f"{_METRICS_PATH}?success=maybe")

        assert response.status_code == 422
        assert service.metrics_calls == []

    def test_db_failure_returns_502_without_runtime_fallback(
        self, history
    ) -> None:
        client, install = history
        service = install()
        service._set_metrics(  # noqa: SLF001
            ToolExecutionMetricsService.snapshot(()),
            error=ToolExecutionRepositoryError("db down"),
        )
        _seed(_record(request_id="memory-A"))     # 内存里有数据

        response = client.get(f"{_METRICS_PATH}")

        assert response.status_code == 502
        assert response.json()["detail"] == "Tool 持久观测指标不可用"
        assert "total_count" not in response.text   # 未回退到内存指标
        assert "memory-A" not in response.text

    def test_unexpected_error_returns_500(self, history) -> None:
        client, install = history
        service = install()
        service._set_metrics(  # noqa: SLF001
            ToolExecutionMetricsService.snapshot(()),
            error=RuntimeError("boom"),
        )

        response = client.get(_METRICS_PATH)

        assert response.status_code == 500
        assert response.json()["detail"] == "Tool 观测数据不可用"

    def test_response_field_whitelist_and_security(self, history) -> None:
        client, install = history
        install()

        payload = client.get(
            f"{_METRICS_PATH}?project_id=project-a&tool_name=get_inventory"
        ).json()

        assert tuple(payload) == _METRICS_FIELDS
        for forbidden in (
            "id", "request_id", "project_id", "tool_name", "tool_call_id",
            "arguments", "result", "data", "sql", "prompt", "llm_response",
            "password", "api_key", "database_url", "traceback", "rows",
        ):
            assert forbidden not in payload, forbidden

    def test_no_limit_or_offset_parameters(self) -> None:
        import inspect

        signature = inspect.signature(api_module.persistent_tool_execution_metrics)

        assert list(signature.parameters) == [
            "project_id", "tool_name", "success"
        ]

    def test_runtime_metrics_unchanged_when_persistent_broken(
        self, history
    ) -> None:
        """Runtime Metrics 仍只读内存：持久侧故障不影响它。"""
        client, install = history
        service = install()
        service._set_metrics(  # noqa: SLF001
            ToolExecutionMetricsService.snapshot(()),
            error=ToolExecutionRepositoryError("db down"),
        )
        _seed(_record(request_id="memory-A"))
        _seed(_record(request_id="memory-B", success=False))

        assert client.get(f"{_METRICS_PATH}").status_code == 502
        metrics_calls = list(service.metrics_calls)
        runtime = client.get("/api/observability/tools/metrics")

        assert runtime.status_code == 200
        assert runtime.json()["total_count"] == 2
        # 运行时端点不触达持久化链路（调用次数不变）
        assert service.metrics_calls == metrics_calls
        assert service.calls == []

    def test_persistent_metrics_is_read_only(self, history) -> None:
        client, install = history
        install()
        _seed(_record(request_id="memory-A"))
        before = root._TOOL_EXECUTION_COLLECTOR.records()

        client.get(_METRICS_PATH)

        assert root._TOOL_EXECUTION_COLLECTOR.records() == before
