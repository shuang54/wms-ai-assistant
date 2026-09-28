"""Assistant Trace → LLM Usage → Tool Execution 关联测试
（Phase 3.12 Step 36）。

真实链路（**0 real LLM / 0 DB**）：

    POST /api/ai/chat
        ↓
    AIOrchestratorService.execute()      ← request_id = A（唯一来源）
        ↓ assistant_trace_scope(A)
    Router / RAG / Tool / Text-to-SQL
        ↓ LLM 调用（本测试用"会发 Observation 的替身"模拟 LLM 边界）
    DatabaseLLMAccountingSink → LLMUsagePersistenceService → (Fake) Repository
        ↓ assistant_request_id = A / request_id = P（Provider 请求 ID）

断言三方一致（Tool 路径）：

    response.metadata.request_id
        == ToolExecutionRecord.request_id
        == LLMUsageRecord(assistant_request_id)

说明：Orchestrator 的 **Tool 路径当前不调用 LLM**；为验证"同一 Assistant
Scope 内的任何 LLM usage 都关联到 A"，测试用 Tool Handler / T2S Generator
内部触发一次 Accounting Sink 调用（**模拟**该路径的 LLM 边界，非真实 LLM）。
"""
from __future__ import annotations

import ast
import os
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import orchestrator_chat as orch_module
from backend.app.db.llm_usage_repository import LLMUsageRepositoryError
from backend.app.llm.client import LLMUsage
from backend.app.llm.observability import LLMObservation
from backend.app.main import app
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
)
from backend.app.services.rag_service import RagResponse
from backend.app.services.sql_executor_service import SQLExecutionResult
from backend.app.services.text_to_sql_service import TextToSQLResult
from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION
from backend.app.tools.registry import ToolRegistry

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TRACE_MODULE = "backend/app/services/assistant_trace.py"
_API_MODULE = "backend/app/api/orchestrator_chat.py"
_ORCH_MODULE = "backend/app/services/ai_orchestrator_service.py"
_PERSISTENCE_MODULE = (
    "backend/app/services/llm_usage_persistence_service.py"
)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.Module:
    return ast.parse(_source(relative))


def _identifiers(relative: str) -> set[str]:
    return {
        node.id.lower()
        for node in ast.walk(_tree(relative))
        if isinstance(node, ast.Name)
    } | {
        node.attr.lower()
        for node in ast.walk(_tree(relative))
        if isinstance(node, ast.Attribute)
    }


# ============================================================
# Fakes / 替身
# ============================================================

class _CapturingRepository:
    """线程安全地记录 create() kwargs（arecord 会在 worker 线程执行）。"""

    def __init__(self, error: Exception | None = None) -> None:
        self._lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        self._error = error

    def create(self, **kwargs: Any) -> int | None:
        with self._lock:
            self.calls.append(dict(kwargs))
            if self._error is not None:
                raise self._error
            return len(self.calls)


def _observation(provider_request_id: str) -> LLMObservation:
    return LLMObservation(
        provider="deepseek-test",
        model="deepseek-chat",
        latency_ms=1.0,
        success=True,
        finish_reason="stop",
        usage=LLMUsage(11, 22, 33),
        request_id=provider_request_id,
    )


class _AccountingRag:
    """RagService 替身：调用 LLM（用 Sink 调用模拟）后返回答案。"""

    def __init__(
        self,
        sink: DatabaseLLMAccountingSink,
        provider_request_id: str = "chatcmpl-RAG-1",
    ) -> None:
        self._sink = sink
        self._provider_request_id = provider_request_id
        self.calls: list[str] = []

    async def answer(self, query, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(query)
        self._sink.record(_observation(self._provider_request_id))
        return RagResponse(
            answer="采购入库需要先创建入库通知",
            sources=(),
            used_chunks_count=1,
        )


class _LlmUsingToolHandler:
    """Tool Handler 替身：执行期间触发一次 LLM usage（Sink 调用）。"""

    def __init__(self, sink: DatabaseLLMAccountingSink) -> None:
        self._sink = sink
        self.calls = 0

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        self._sink.record(_observation("chatcmpl-TOOL-1"))
        return dict(arguments)


class _AccountingTextToSQL:
    """Text-to-SQL Generator 替身：生成前触发一次 LLM usage。"""

    def __init__(self, sink: DatabaseLLMAccountingSink) -> None:
        self._sink = sink
        self.calls: list[str] = []

    async def generate(self, question, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(question)
        self._sink.record(_observation("chatcmpl-T2S-1"))
        return TextToSQLResult(
            question=question,
            sql="SELECT id FROM public.inventory LIMIT 5",
            validated=True,
            attempts=1,
            referenced_tables=("public.inventory",),
        )


class _SqlExecutor:
    async def execute(self, sql, **kwargs):  # noqa: ANN003, ANN201
        return SQLExecutionResult(
            columns=("id",), rows=((1,),), row_count=1, truncated=False,
            execution_time_ms=1.0,
        )


class _TableSelector:
    def select(self, question, *, schema, semantic, top_k=5):  # noqa: ANN001
        from backend.app.services.relevant_table_selector import (
            TableSelectionResult,
        )

        return TableSelectionResult(question=question, selections=())


class _ContextComposer:
    def compose(self, *, project, schema, semantic, tables=None, max_chars=4000):
        return "TRACE_CONTEXT"


class _ProjectProvider:
    def resolve(self):
        from backend.app.projects.context import DataSource, ProjectContext
        from backend.app.projects.semantic import ProjectSemantic
        from backend.app.services.schema_explorer_service import DatabaseSchema

        return (
            ProjectContext(
                project_id="project-a",
                project_name="Project A",
                description=None,
                data_source=DataSource(name="primary", type="postgresql"),
            ),
            DatabaseSchema(schema_name="public", tables=()),
            ProjectSemantic(),
        )


class _Router:
    def __init__(self, decision) -> None:
        self._decision = decision

    async def route(self, question, *, context=None):  # noqa: ANN001
        return self._decision


def _decision(route):
    from backend.app.services.ai_router_service import RouteDecision

    return RouteDecision(
        route=route,
        confidence=0.9,
        reason=f"step36:{route.value}",
        source="rule",
        tool_name="get_inventory" if route.value == "tool" else None,
    )


def _collector():
    from backend.app.services.in_memory_tool_execution_collector import (
        InMemoryToolExecutionCollector,
    )

    return InMemoryToolExecutionCollector()


def _orchestrator(
    *,
    route,
    sink: DatabaseLLMAccountingSink | None = None,
    repository: _CapturingRepository | None = None,
    observer: Any = None,
    registry: ToolRegistry | None = None,
):
    """真实 AIOrchestratorService + 真实执行边界 + Fake LLM 边界（0 DB）。"""
    from backend.app.services.ai_orchestrator_service import (
        AIOrchestratorService,
    )
    from backend.app.services.ai_router_service import RouteType
    from backend.app.services.tool_execution_service import (
        ToolExecutionService,
    )

    repository = repository if repository is not None else _CapturingRepository()
    sink = (
        sink
        if sink is not None
        else DatabaseLLMAccountingSink(repository=repository)  # type: ignore[arg-type]
    )

    if route == RouteType.TOOL and registry is None:
        registry = ToolRegistry()
        registry.register(
            GET_INVENTORY_DEFINITION, _LlmUsingToolHandler(sink)
        )

    execution = (
        ToolExecutionService(registry=registry, observer=observer)
        if registry is not None
        else None
    )
    orchestrator = AIOrchestratorService(
        router=_Router(_decision(route)),
        rag_service=_AccountingRag(sink),
        tool_registry=registry,
        text_to_sql=_AccountingTextToSQL(sink),
        sql_executor=_SqlExecutor(),
        table_selector=_TableSelector(),
        context_composer=_ContextComposer(),
        project_context_provider=_ProjectProvider(),
        tool_execution_service=execution,
        tool_execution_observer=observer,
    )
    return orchestrator, repository, sink


def _post(monkeypatch, orchestrator, question: str = "查询物料 MAT-001 当前库存"):
    monkeypatch.setattr(orch_module, "_default_orchestrator", orchestrator)
    with TestClient(app) as client:
        return client.post("/api/ai/chat", json={"question": question})


# ============================================================
# 1~3. 三条路由的 correlation（HTTP → Orchestrator → Usage）
# ============================================================

class TestRouteCorrelation:
    def test_rag_route_correlates_usage(self, monkeypatch) -> None:
        from backend.app.services.ai_router_service import RouteType

        orchestrator, repository, _sink = _orchestrator(
            route=RouteType.RAG
        )

        response = _post(monkeypatch, orchestrator, "采购入库流程是什么")

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "rag"
        assert len(repository.calls) == 1
        call = repository.calls[0]
        assert call["assistant_request_id"] == payload["metadata"]["request_id"]
        assert call["request_id"] == "chatcmpl-RAG-1"          # Provider ID
        assert call["assistant_request_id"] != call["request_id"]

    def test_tool_route_three_way_correlation(self, monkeypatch) -> None:
        from backend.app.services.ai_router_service import RouteType

        observer = _collector()
        orchestrator, repository, _sink = _orchestrator(
            route=RouteType.TOOL, observer=observer
        )
        observer.clear()

        response = _post(monkeypatch, orchestrator)

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "tool"
        records = observer.records()
        assert len(records) == 1
        assert len(repository.calls) == 1

        assistant_id = payload["metadata"]["request_id"]
        assert assistant_id == records[0].request_id
        assert assistant_id == repository.calls[0]["assistant_request_id"]
        assert records[0].round == 1
        assert records[0].tool_call_id is None
        assert repository.calls[0]["request_id"] == "chatcmpl-TOOL-1"
        assert assistant_id != repository.calls[0]["request_id"]
        observer.clear()

    def test_text_to_sql_route_correlates_usage(self, monkeypatch) -> None:
        from backend.app.services.ai_router_service import RouteType

        observer = _collector()
        orchestrator, repository, _sink = _orchestrator(
            route=RouteType.TEXT_TO_SQL, observer=observer
        )
        observer.clear()

        response = _post(monkeypatch, orchestrator, "本月入库数量是多少？")

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "text_to_sql"
        assert repository.calls[0]["assistant_request_id"] == (
            payload["metadata"]["request_id"]
        )
        assert repository.calls[0]["request_id"] == "chatcmpl-T2S-1"
        assert observer.records() == ()          # T2S → 0 Tool Record

    def test_two_requests_do_not_mix(self, monkeypatch) -> None:
        """两次独立请求 → 两条 Usage，各自关联自己的 request_id。"""
        from backend.app.services.ai_router_service import RouteType

        orchestrator, repository, _sink = _orchestrator(route=RouteType.RAG)

        first = _post(monkeypatch, orchestrator, "问题一").json()
        second = _post(monkeypatch, orchestrator, "问题二").json()

        ids = [call["assistant_request_id"] for call in repository.calls]
        assert ids == [
            first["metadata"]["request_id"], second["metadata"]["request_id"],
        ]
        assert ids[0] != ids[1]

    def test_usage_persistence_failure_keeps_assistant_execution(
        self, monkeypatch
    ) -> None:
        """Usage 持久化失败 → 只 warning；Assistant 结果与 request_id 不变。"""
        from backend.app.services.ai_router_service import RouteType

        repository = _CapturingRepository(
            error=LLMUsageRepositoryError("db down")
        )
        orchestrator, _repo, _sink = _orchestrator(
            route=RouteType.RAG, repository=repository
        )

        response = _post(monkeypatch, orchestrator, "采购入库流程是什么")

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "rag"
        assert payload["content"]
        assert payload["metadata"]["request_id"]
        assert len(repository.calls) == 1        # 尝试一次，无 retry


# ============================================================
# C41. LLM Usage Trace Correlation
# ============================================================

class TestC41LLMUsageTraceCorrelation:
    """C41：Assistant Trace → LLM Usage → Tool Execution 关联契约。

        C41.1  Assistant request_id → LLM Usage
        C41.2  provider_request_id 保持独立（不覆盖 / 不替代）
        C41.3  API 不生成 request_id
        C41.4  Orchestrator 是 Assistant Trace 唯一来源
        C41.5  Tool Record 与 LLM Usage 可用同一 request_id 关联
        C41.6  RAG / T2S 不产生 Tool Record
        C41.7  历史 Usage（NULL correlation）仍兼容
        C41.8  Usage persistence failure 不影响 Assistant execution
        C41.9  不新增 prompt / tool args / SQL / secrets
    """

    def test_c41_1_and_5_correlation_is_shared_id(self, monkeypatch) -> None:
        from backend.app.services.ai_router_service import RouteType

        observer = _collector()
        orchestrator, repository, _sink = _orchestrator(
            route=RouteType.TOOL, observer=observer
        )
        observer.clear()

        payload = _post(monkeypatch, orchestrator).json()
        record = observer.records()[0]
        usage = repository.calls[0]

        assert (
            payload["metadata"]["request_id"]
            == record.request_id
            == usage["assistant_request_id"]
        )
        observer.clear()

    def test_c41_2_provider_id_stays_independent(self) -> None:
        from backend.app.services.assistant_trace import assistant_trace_scope
        from backend.app.services.llm_usage_persistence_service import (
            LLMUsagePersistenceService,
        )

        repository = _CapturingRepository()
        service = LLMUsagePersistenceService(repository=repository)  # type: ignore[arg-type]

        with assistant_trace_scope("assistant-A"):
            service.persist(_observation("chatcmpl-P"))

        call = repository.calls[0]
        assert call["assistant_request_id"] == "assistant-A"
        assert call["request_id"] == "chatcmpl-P"
        assert call["assistant_request_id"] != call["request_id"]

    def test_c41_3_api_does_not_generate_request_id(self) -> None:
        identifiers = _identifiers(_API_MODULE)
        for forbidden in ("uuid", "uuid4", "new_request_id", "token_hex"):
            assert forbidden not in identifiers, forbidden
        source = _source(_API_MODULE)
        assert "new_request_id(" not in source
        assert "assistant_trace_scope(" not in source   # API 不绑定 Scope

    def test_c41_4_orchestrator_is_the_only_source(self) -> None:
        orch_source = _source(_ORCH_MODULE)
        trace_source = _source(_TRACE_MODULE)
        persistence_source = _source(_PERSISTENCE_MODULE)

        assert orch_source.count("new_request_id()") == 2   # import + 调用
        assert orch_source.count("assistant_trace_scope(") == 1
        # trace 模块只保存 / 读取；不生成 ID
        for forbidden in ("uuid", "uuid4", "time", "random"):
            assert forbidden not in _identifiers(_TRACE_MODULE), forbidden
        assert "uuid" not in _identifiers(_PERSISTENCE_MODULE)
        assert "new_request_id" not in _identifiers(_PERSISTENCE_MODULE)
        # trace 模块不依赖 DB / LLM / 网络
        imports = {
            node.module or ""
            for node in ast.walk(_tree(_TRACE_MODULE))
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(_tree(_TRACE_MODULE))
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        for forbidden in ("sqlalchemy", "backend.app.db", "backend.app.llm"):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden
        assert "assistant_trace_scope" in trace_source

    def test_c41_6_rag_and_t2s_produce_zero_tool_records(
        self, monkeypatch
    ) -> None:
        from backend.app.services.ai_router_service import RouteType

        for route in (RouteType.RAG, RouteType.TEXT_TO_SQL):
            observer = _collector()
            orchestrator, repository, _sink = _orchestrator(
                route=route, observer=observer
            )
            observer.clear()

            payload = _post(monkeypatch, orchestrator, "问题").json()

            assert payload["metadata"]["request_id"]
            assert observer.records() == (), route
            assert repository.calls[0]["assistant_request_id"]
            observer.clear()

    def test_c41_7_legacy_null_correlation_is_compatible(self) -> None:
        from backend.app.db.llm_usage_repository import (
            LLM_USAGE_READ_COLUMNS,
        )
        from backend.app.services.llm_usage_persistence_service import (
            LLMUsagePersistenceService,
        )

        # 读边界未变（历史 NULL 行照常可读；不新增只读字段）
        assert LLM_USAGE_READ_COLUMNS == (
            "id", "request_id", "provider", "model", "prompt_tokens",
            "completion_tokens", "total_tokens", "created_at",
        )
        # 未绑定 Scope（旧链路）→ NULL，而不是生成值
        repository = _CapturingRepository()
        LLMUsagePersistenceService(repository=repository).persist(  # type: ignore[arg-type]
            _observation("chatcmpl-legacy")
        )
        assert repository.calls[0]["assistant_request_id"] is None
        assert repository.calls[0]["request_id"] == "chatcmpl-legacy"

    def test_c41_8_persistence_failure_isolated(self, monkeypatch) -> None:
        from backend.app.services.ai_router_service import RouteType

        repository = _CapturingRepository(
            error=LLMUsageRepositoryError("db down")
        )
        orchestrator, _repo, _sink = _orchestrator(
            route=RouteType.RAG, repository=repository
        )

        response = _post(monkeypatch, orchestrator)

        assert response.status_code == 200
        assert response.json()["metadata"]["request_id"]

    def test_c41_9_no_payload_beyond_whitelist(self) -> None:
        from backend.app.services.assistant_trace import assistant_trace_scope
        from backend.app.services.llm_usage_persistence_service import (
            LLMUsagePersistenceService,
        )

        repository = _CapturingRepository()
        with assistant_trace_scope("assistant-A"):
            LLMUsagePersistenceService(repository=repository).persist(  # type: ignore[arg-type]
                _observation("chatcmpl-P")
            )

        call = repository.calls[0]
        assert set(call) == {
            "request_id", "provider", "model", "prompt_tokens",
            "completion_tokens", "total_tokens", "assistant_request_id",
        }
        for forbidden in (
            "prompt", "messages", "sql", "tool_args", "arguments",
            "tool_result", "rag_content", "chunk_content", "raw_response",
            "headers", "api_key", "authorization", "password", "database_url",
        ):
            assert forbidden not in call, forbidden

    def test_c41_10_no_new_endpoint_or_model_field_leak(self) -> None:
        """不新增 HTTP 端点；analytics 响应结构未变。"""
        paths = set(app.openapi()["paths"])
        assert "/api/usage/analytics" in paths
        assert not any(
            "by-request" in path or "trace" in path for path in paths
        ), sorted(paths)
        from backend.app.api.usage import UsageAnalyticsResponse

        fields = set(UsageAnalyticsResponse.model_fields)
        assert "assistant_request_id" not in fields
        assert "provider_request_id" not in fields


__all__ = [
    "TestRouteCorrelation",
    "TestC41LLMUsageTraceCorrelation",
]
