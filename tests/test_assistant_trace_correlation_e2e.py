"""Assistant Trace 全链路 Correlation Smoke（Phase 3.12 Step 40）。

真实链路（**0 real LLM / 0 DB**；只 Fake LLM Provider 传输层与 Tool Handler）：

    POST /api/ai/chat
        ↓ 真实 AIOrchestratorService（真实 AIRouterService + 真实 Tool 执行边界）
    assistant_request_id = A          ← new_request_id()（Step 35）
        │  assistant_trace_scope(A)    ← Step 36
        ├── LLM 调用（真实 OpenAICompatibleClient + httpx.MockTransport
        │            + 真实 DatabaseLLMAccountingSink → 真实 PersistenceService
        │            → InMemory Usage Repository）
        │        assistant_request_id = A
        └── Tool 执行（真实 ToolExecutionService → 真实应用级 Collector +
                       内存持久化替身（Step 41：Trace 只读持久化边界））
                 request_id = A
        ↓
    GET /api/observability/assistant-trace/{A}
        （真实 AssistantTraceQueryService + 真实 Trace API；
          Tool 数据源 = 持久化读边界替身 —— Runtime Collector 不参与 Trace）
        ↓ 200 AssistantTraceResponse

断言：assistant_request_id 一条链贯穿（不被 Fake 替换的环节：
        ID 生成 / scope 传播 / LLM usage correlation / Tool request_id /
        组合服务 / Trace API）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.api import assistant_trace as trace_module
from backend.app.api import orchestrator_chat as root
from backend.app.db.llm_usage_repository import LLMUsageTraceRow
from backend.app.llm.client import OpenAICompatibleClient
from backend.app.main import app
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorService,
)
from backend.app.services.ai_router_service import (
    AIRouterService,
    ToolRegistryCapabilityAdapter,
)
from backend.app.services.assistant_trace_query_service import (
    AssistantTraceQueryService,
)
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
    LLMUsagePersistenceService,
)
from backend.app.services.llm_usage_query_service import LLMUsageQueryService
from backend.app.services.rag_service import RagResponse
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.services.tool_execution_observer import ToolExecutionObserver
from backend.app.services.tool_execution_persistence_adapter import (
    CompositeToolExecutionObserver,
)
from backend.app.services.tool_observability_snapshot import (
    ToolExecutionSnapshot,
)
from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION
from backend.app.tools.registry import ToolRegistry

_TRACE_ENDPOINT = "/api/observability/assistant-trace"
_RAG_QUESTION = "采购入库的操作步骤是什么"
_TOOL_QUESTION = "查询物料 MAT-001 当前库存"
_BASE = datetime(2026, 9, 28, 20, 0, 0, tzinfo=timezone.utc)


# ============================================================
# LLM 边界（真实 Client + MockTransport + 真实 Accounting Sink）
# ============================================================

def _response_body(request_id: str, content: str = "已收到问题") -> dict[str, Any]:
    """OpenAI Chat Completions 兼容响应（provider 返回的 request_id = P）。"""
    return {
        "id": request_id,
        "model": "step40-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 22,
            "total_tokens": 33,
        },
    }


class _InMemoryUsageRepository:
    """内存 Usage Repository（duck-typed；**仅 LLM Provider / DB 替身**）。"""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.next_id = 1

    def create(self, **kwargs: Any) -> int:
        row = dict(kwargs)
        row["id"] = self.next_id
        row["created_at"] = _BASE.replace(microsecond=self.next_id)
        self.rows.append(row)
        self.next_id += 1
        return row["id"]

    def list_by_assistant_request_id(
        self, assistant_request_id: str
    ) -> list[LLMUsageTraceRow]:
        return [
            LLMUsageTraceRow(
                id=row["id"],
                assistant_request_id=row["assistant_request_id"],
                request_id=row.get("request_id"),
                provider=row.get("provider"),
                model=row.get("model"),
                prompt_tokens=row.get("prompt_tokens"),
                completion_tokens=row.get("completion_tokens"),
                total_tokens=row.get("total_tokens"),
                created_at=row["created_at"],
            )
            for row in self.rows
            if row.get("assistant_request_id") == assistant_request_id
        ]


def _client(
    repository: _InMemoryUsageRepository,
    *,
    provider_request_id: str,
    content: str = "已收到问题",
) -> OpenAICompatibleClient:
    """真实 LLM Client（provider = DeepSeek-like 传输替身；零网络）。

    accounting_sink = 真实 DatabaseLLMAccountingSink → 真实 PersistenceService
    → 注入的 Repository（本测试为内存实现；DB 测试为真实 PostgreSQL）。
    """
    sink = DatabaseLLMAccountingSink(
        persistence_service=LLMUsagePersistenceService(
            repository=repository  # type: ignore[arg-type]
        )
    )
    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://step40.fake/v1",
        model="step40-model",
        provider="step40-provider",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=_response_body(provider_request_id, content)
            )
        ),
        accounting_sink=sink,
    )


class _LlmBackedRag:
    """RagService 替身：**只**承担"调用 LLM 并返回答案"（检索层不在本测试范围）。"""

    def __init__(self, client: OpenAICompatibleClient) -> None:
        self._client = client
        self.calls: list[str] = []

    async def answer(self, query, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(query)
        response = await self._client.chat(
            messages=[{"role": "user", "content": query}]
        )
        return RagResponse(
            answer=getattr(response, "content", None) or "（无内容）",
            sources=(),
            used_chunks_count=1,
        )


class _RecordingToolHandler:
    """Tool Handler 替身（只读；不访问 DB）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return dict(arguments)


class _LlmCallingToolHandler:
    """Tool Handler 替身：执行期间调用 LLM（模拟"工具内部使用 LLM"）。

    验证：Tool 执行期间产生的 LLM usage 也带上**同一个** Assistant Trace ID。
    """

    def __init__(self, client: OpenAICompatibleClient) -> None:
        self._client = client
        self.calls = 0

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        await self._client.chat(
            messages=[{"role": "user", "content": "总结库存查询结果"}]
        )
        return dict(arguments)


class _TextToSQL:
    async def generate(self, question, **kwargs):  # noqa: ANN003
        raise AssertionError("T2S 不应在本测试路径被调用")


class _SqlExecutor:
    async def execute(self, sql, **kwargs):  # noqa: ANN003
        raise AssertionError("SQL Executor 不应在本测试路径被调用")


class _TableSelector:
    def select(self, question, *, schema, semantic, top_k=5):  # noqa: ANN001
        from backend.app.services.relevant_table_selector import (
            TableSelectionResult,
        )

        return TableSelectionResult(question=question, selections=())


class _ContextComposer:
    def compose(self, *, project, schema, semantic, tables=None, max_chars=4000):
        return "STEP40_CONTEXT"


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


# ============================================================
# 装配（真实 Orchestrator + 真实执行边界 + 真实组合服务）
# ============================================================

class _InMemoryToolTrace:
    """内存 Tool Trace Store（**持久化读边界替身**；无 DB）。

    同时实现 Observer 侧写入（``on_execution``）与 Trace 侧读取
    （``list_by_request_id``），形状与 Phase 3.11 的持久化读边界一致：
    同一 request_id 的全部 Snapshot（写入顺序 = id ASC）。
    """

    def __init__(self) -> None:
        self._snapshots: list[ToolExecutionSnapshot] = []

    def on_execution(self, record: Any) -> None:
        self._snapshots.append(ToolExecutionSnapshot.from_record(record))

    def list_by_request_id(
        self, request_id: str
    ) -> list[ToolExecutionSnapshot]:
        return [
            snapshot
            for snapshot in self._snapshots
            if snapshot.request_id == request_id
        ]

    def clear(self) -> None:
        self._snapshots.clear()


@pytest.fixture()
def e2e(monkeypatch):
    """安装真实 Orchestrator 与真实 Trace 组合服务（只 Fake LLM 传输 / Handler）。"""

    def _install(*, llm_calling_tool: bool = False):
        repository = _InMemoryUsageRepository()
        rag_client = _client(repository, provider_request_id="step40-rag-1")
        tool_client = _client(
            repository, provider_request_id="step40-tool-1", content="工具内 LLM"
        )

        registry = ToolRegistry()
        handler: Any
        handler = (
            _LlmCallingToolHandler(tool_client)
            if llm_calling_tool
            else _RecordingToolHandler()
        )
        registry.register(GET_INVENTORY_DEFINITION, handler)

        collector = root._TOOL_EXECUTION_COLLECTOR
        collector.clear()
        # Step 41：Tool 数据源 = 持久化边界；本测试用内存替身模拟（无 DB）
        persistent_tools = _InMemoryToolTrace()
        observer: ToolExecutionObserver = CompositeToolExecutionObserver(
            collector, persistent_tools
        )
        execution = ToolExecutionService(registry=registry, observer=observer)
        orchestrator = AIOrchestratorService(
            router=AIRouterService(
                llm_fallback_enabled=False,
                tool_capabilities=ToolRegistryCapabilityAdapter(registry),
            ),
            rag_service=_LlmBackedRag(rag_client),
            tool_registry=registry,
            text_to_sql=_TextToSQL(),
            sql_executor=_SqlExecutor(),
            table_selector=_TableSelector(),
            context_composer=_ContextComposer(),
            project_context_provider=_ProjectProvider(),
            tool_execution_service=execution,
            tool_execution_observer=observer,
        )
        monkeypatch.setattr(root, "_default_orchestrator", orchestrator)
        # Trace API：真实组合服务（真实 LLM 读边界 + 持久化 Tool 读边界替身）
        monkeypatch.setattr(
            trace_module,
            "get_assistant_trace_query_service",
            lambda: AssistantTraceQueryService(
                llm_usage_query_service=LLMUsageQueryService(
                    repository=repository  # type: ignore[arg-type]
                ),
                tool_observability_query_service=persistent_tools,
            ),
        )
        return repository, handler, persistent_tools, collector

    yield _install
    root._TOOL_EXECUTION_COLLECTOR.clear()


def _ask(client: TestClient, question: str):
    return client.post("/api/ai/chat", json={"question": question})


def _trace(client: TestClient, request_id: str):
    return client.get(f"{_TRACE_ENDPOINT}/{request_id}")


# ============================================================
# Case A：LLM Only
# ============================================================

def test_llm_only_correlation(e2e) -> None:
    repository, _handler, _persistent_tools, _collector = e2e()

    with TestClient(app) as client:
        chat = _ask(client, _RAG_QUESTION)
        assert chat.status_code == 200
        payload = chat.json()
        assert payload["route"] == "rag"
        request_id = payload["metadata"]["request_id"]
        assert request_id

        trace_response = _trace(client, request_id)

    assert trace_response.status_code == 200
    trace = trace_response.json()
    assert trace["assistant_request_id"] == request_id
    assert len(trace["llm_usage"]) >= 1
    for row in trace["llm_usage"]:
        assert row["assistant_request_id"] == request_id
        assert row["request_id"] == "step40-rag-1"          # Provider ID ≠ A
        assert row["request_id"] != request_id
    assert trace["tool_executions"] == []
    # 内存 Repository 收到的是同一 ID（写入侧证据）
    assert [r["assistant_request_id"] for r in repository.rows] == [request_id]


# ============================================================
# Case B：Tool 路径
# ============================================================

def test_tool_correlation(e2e) -> None:
    _repository, handler, _persistent_tools, _collector = e2e()

    with TestClient(app) as client:
        chat = _ask(client, _TOOL_QUESTION)
        assert chat.status_code == 200
        payload = chat.json()
        assert payload["route"] == "tool"
        request_id = payload["metadata"]["request_id"]

        trace = _trace(client, request_id).json()

    assert handler.calls == 1
    assert trace["assistant_request_id"] == request_id
    assert len(trace["tool_executions"]) >= 1
    for row in trace["tool_executions"]:
        assert row["request_id"] == request_id
        assert row["round"] == 1
        assert row["tool_call_id"] is None
    # 真实链路：Tool 路径不调用 LLM → 不人为要求 >0
    assert trace["llm_usage"] == []


# ============================================================
# Case C：LLM + Tool（同一 Trace）
# ============================================================

def test_llm_and_tool_correlation(e2e) -> None:
    repository, handler, _persistent_tools, _collector = e2e(
        llm_calling_tool=True
    )

    with TestClient(app) as client:
        chat = _ask(client, _TOOL_QUESTION)
        payload = chat.json()
        assert payload["route"] == "tool"
        request_id = payload["metadata"]["request_id"]

        trace = _trace(client, request_id).json()

    assert handler.calls == 1
    assert trace["assistant_request_id"] == request_id
    assert len(trace["llm_usage"]) >= 1          # Tool 执行期间的 LLM usage
    assert len(trace["tool_executions"]) >= 1
    for row in trace["llm_usage"]:
        assert row["assistant_request_id"] == request_id
        assert row["request_id"] == "step40-tool-1"
        assert row["request_id"] != request_id
    for row in trace["tool_executions"]:
        assert row["request_id"] == request_id
    assert [r["assistant_request_id"] for r in repository.rows] == [request_id]


# ============================================================
# Cross Request Isolation
# ============================================================

def test_cross_request_isolation(e2e) -> None:
    e2e(llm_calling_tool=True)

    with TestClient(app) as client:
        first = _ask(client, _TOOL_QUESTION).json()
        second = _ask(client, _RAG_QUESTION).json()

        request_a = first["metadata"]["request_id"]
        request_b = second["metadata"]["request_id"]
        assert request_a != request_b

        trace_a = _trace(client, request_a).json()
        trace_b = _trace(client, request_b).json()

    assert trace_a["assistant_request_id"] == request_a
    assert trace_b["assistant_request_id"] == request_b
    # A 里没有 B 的数据（反之亦然）
    assert {r["assistant_request_id"] for r in trace_a["llm_usage"]} <= {
        request_a
    }
    assert {r["request_id"] for r in trace_a["tool_executions"]} <= {request_a}
    assert {r["assistant_request_id"] for r in trace_b["llm_usage"]} <= {
        request_b
    }
    assert {r["request_id"] for r in trace_b["tool_executions"]} <= {request_b}
    assert request_b not in json.dumps(trace_a)
    assert request_a not in json.dumps(trace_b)


# ============================================================
# Security（真实 application Trace 经 HTTP）
# ============================================================

def test_trace_security(e2e) -> None:
    e2e(llm_calling_tool=True)

    with TestClient(app) as client:
        request_id = _ask(client, _TOOL_QUESTION).json()["metadata"][
            "request_id"
        ]
        response = _trace(client, request_id)

    assert response.status_code == 200
    for forbidden_key in (
        "prompt", "messages", "arguments", "raw_response", "secret",
        "password", "api_key", "authorization", "database_url", "sql",
        "session", "connection", "traceback", "internal_debug", "data",
        "handler", "registry",
    ):
        assert f'"{forbidden_key}":' not in response.text, forbidden_key
    assert "step40.fake" not in response.text               # base_url / 凭据
    assert "test-key" not in response.text
    assert _TOOL_QUESTION not in response.text               # 问题文本外泄
    payload = response.json()
    assert set(payload) == {
        "assistant_request_id", "llm_usage", "tool_executions",
    }


def test_tool_trace_survives_runtime_collector_clear(e2e) -> None:
    """Step 41：Runtime Collector 清空后，Trace 仍能读到 Tool 执行。

    （无 DB 场景用内存持久化替身模拟；DB 场景见
      ``test_assistant_trace_persistent_tool_db.py``。）
    """
    _repository, _handler, persistent_tools, collector = e2e()

    with TestClient(app) as client:
        payload = _ask(client, _TOOL_QUESTION).json()
        assert payload["route"] == "tool"
        request_id = payload["metadata"]["request_id"]

        collector.clear()                       # 模拟进程重启 / retention 淘汰
        trace = _trace(client, request_id).json()
        runtime = client.get("/api/observability/tools").json()

    assert runtime == {"records": []}           # Runtime 视图已空
    assert len(trace["tool_executions"]) >= 1   # Trace 仍可读（持久化边界）
    assert all(
        row["request_id"] == request_id
        for row in trace["tool_executions"]
    )
    assert persistent_tools.list_by_request_id(request_id)


def test_chat_response_envelope_unchanged(e2e) -> None:
    e2e()

    with TestClient(app) as client:
        payload = _ask(client, _RAG_QUESTION).json()

    assert set(payload) == {"route", "content", "data", "metadata"}
    assert set(payload["metadata"]) >= {"request_id"}
    # metadata 只带既有键 + request_id（无 arguments / SQL / prompt）
    for forbidden_key in ("prompt", "messages", "arguments", "sql"):
        assert forbidden_key not in payload["metadata"]


def test_existing_apis_still_work(e2e) -> None:
    e2e()

    with TestClient(app) as client:
        assert client.get("/api/observability/tools").status_code == 200
        assert client.get(
            "/api/observability/tools/metrics"
        ).status_code == 200
        assert client.get("/api/observability/tools/history").status_code in {
            200, 502,
        }
        assert client.get(
            "/api/observability/tools/metrics/persistent"
        ).status_code in {200, 502}


__all__ = [
    "test_llm_only_correlation",
    "test_tool_correlation",
    "test_llm_and_tool_correlation",
    "test_cross_request_isolation",
    "test_trace_security",
    "test_tool_trace_survives_runtime_collector_clear",
    "test_chat_response_envelope_unchanged",
    "test_existing_apis_still_work",
]
