"""Assistant Trace 全链路 Correlation Smoke：DB 集成（Phase 3.12 Step 40）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

真实链路（**0 real LLM**；无 Fake Trace API / 无 Fake Query Service / 无 Fake Repository）：

    POST /api/ai/chat
        ↓ 真实 AIOrchestratorService（真实 AIRouterService + 真实 Tool 执行边界）
    assistant_request_id = A
        ├── LLM（真实 OpenAICompatibleClient + httpx.MockTransport +
        │        真实 DatabaseLLMAccountingSink → 真实 PersistenceService
        │        → 真实 LLMUsageRepository → PostgreSQL）
        └── Tool（真实 ToolExecutionService → 真实应用级 Collector +
                 真实 ToolExecutionPersistenceAdapter → PostgreSQL）
        ↓
    GET /api/observability/assistant-trace/{A}
        （**真实 accessor** → 真实组合服务 → 真实读边界 → PostgreSQL + Memory）

数据：synthetic（provider / request_id 前缀 ``step40-``）；未读取真实数据。
清理（不用 TRUNCATE）：按 provider 前缀（LLM）与
捕获到的 request_id（Tool）定向 ``DELETE`` + 清空 Collector。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import bindparam, text

from backend.app.api import orchestrator_chat as root
from backend.app.db import init_db as init_db_module
from backend.app.db.llm_usage_repository import LLMUsageRepository
from backend.app.db.models.llm_usage_record import LLM_USAGE_SCHEMA
from backend.app.db.session import get_engine, get_session_factory
from backend.app.llm.client import OpenAICompatibleClient
from backend.app.main import app
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorService,
)
from backend.app.services.ai_router_service import (
    AIRouterService,
    ToolRegistryCapabilityAdapter,
)
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
    LLMUsagePersistenceService,
)
from backend.app.services.rag_service import RagResponse
from backend.app.services.tool_execution_persistence_adapter import (
    CompositeToolExecutionObserver,
    ToolExecutionPersistenceAdapter,
)
from backend.app.services.tool_execution_persistence_service import (
    ToolExecutionPersistenceService,
)
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION
from backend.app.tools.registry import ToolRegistry

_TABLE = f"{LLM_USAGE_SCHEMA}.llm_usage_record"
_PREFIX = "step40-%"
_DELETE_SQL = text(f"DELETE FROM {_TABLE} WHERE provider LIKE :prefix")
_COUNT_SQL = text(f"SELECT COUNT(*) FROM {_TABLE} WHERE provider LIKE :prefix")
_TOTAL_SQL = text(f"SELECT COUNT(*) FROM {_TABLE}")

#: Phase 3.12 Step 41：Tool Execution 表（生产装配会写入该表）
_TOOL_TABLE = "ai_ops.tool_execution_record"
_TOOL_DELETE_SQL = text(
    f"DELETE FROM {_TOOL_TABLE} WHERE request_id IN :request_ids"
).bindparams()
_TOOL_TOTAL_SQL = text(f"SELECT COUNT(*) FROM {_TOOL_TABLE}")
_TRACE_ENDPOINT = "/api/observability/assistant-trace"
_RAG_QUESTION = "采购入库的操作步骤是什么"
_TOOL_QUESTION = "查询物料 MAT-001 当前库存"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


class _E2eContext:
    """测试上下文：Tool Handler + 本次产生的 Assistant request_id 列表。"""

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.request_ids: list[str] = []


def _response_body(request_id: str, content: str = "已收到问题") -> dict[str, Any]:
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


def _client(*, provider_request_id: str, content: str = "已收到问题"):
    """真实 LLM Client（MockTransport + 真实 Accounting Sink → 真实 DB 仓储）。"""
    sink = DatabaseLLMAccountingSink(
        persistence_service=LLMUsagePersistenceService(
            repository=LLMUsageRepository()
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
    def __init__(self, client: OpenAICompatibleClient) -> None:
        self._client = client

    async def answer(self, query, **kwargs):  # noqa: ANN003, ANN201
        response = await self._client.chat(
            messages=[{"role": "user", "content": query}]
        )
        return RagResponse(
            answer=getattr(response, "content", None) or "（无内容）",
            sources=(),
            used_chunks_count=1,
        )


class _LlmCallingToolHandler:
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


@pytest.fixture()
def e2e_db(monkeypatch):
    engine = get_engine()
    if engine is None or get_session_factory() is None:
        pytest.skip("DATABASE_URL 未配置")
    init_db_module.init_db()
    with engine.begin() as conn:
        conn.execute(_DELETE_SQL, {"prefix": _PREFIX})
        baseline = int(conn.execute(_TOTAL_SQL).scalar_one())
        tool_baseline = int(conn.execute(_TOOL_TOTAL_SQL).scalar_one())
    collector = root._TOOL_EXECUTION_COLLECTOR
    collector.clear()

    rag_client = _client(provider_request_id="step40-db-rag-1")
    tool_client = _client(
        provider_request_id="step40-db-tool-1", content="工具内 LLM"
    )
    registry = ToolRegistry()
    handler = _LlmCallingToolHandler(tool_client)
    registry.register(GET_INVENTORY_DEFINITION, handler)
    # Step 41：与生产 Composition Root 一致（Collector + Persistence Adapter）
    observer = CompositeToolExecutionObserver(
        collector,
        ToolExecutionPersistenceAdapter(
            persistence_service=ToolExecutionPersistenceService()
        ),
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
    # 注意：**不** patch trace accessor —— Trace API 走真实生产装配（含 DB 读边界）
    context = _E2eContext(handler)
    yield context
    collector.clear()
    with engine.begin() as conn:
        conn.execute(_DELETE_SQL, {"prefix": _PREFIX})
        assert int(conn.execute(_COUNT_SQL, {"prefix": _PREFIX}).scalar_one()) == 0
        assert int(conn.execute(_TOTAL_SQL).scalar_one()) == baseline
        # Step 41：Tool 行按捕获到的 request_id 定向清理（不用 TRUNCATE）
        if context.request_ids:
            conn.execute(
                text(
                    f"DELETE FROM {_TOOL_TABLE} "
                    "WHERE request_id IN :request_ids"
                ).bindparams(
                    bindparam(
                        "request_ids",
                        value=tuple(context.request_ids),
                        expanding=True,
                    )
                )
            )
        assert int(conn.execute(_TOOL_TOTAL_SQL).scalar_one()) == tool_baseline


@requires_db
class TestAssistantTraceCorrelationE2eDb:
    def test_llm_only_correlation_persists_to_postgres(self, e2e_db) -> None:
        with TestClient(app) as client:
            chat = client.post(
                "/api/ai/chat", json={"question": _RAG_QUESTION}
            )
            assert chat.status_code == 200
            request_id = chat.json()["metadata"]["request_id"]
            e2e_db.request_ids.append(request_id)

            trace = client.get(f"{_TRACE_ENDPOINT}/{request_id}")

        assert trace.status_code == 200
        payload = trace.json()
        assert payload["assistant_request_id"] == request_id
        assert len(payload["llm_usage"]) >= 1
        for row in payload["llm_usage"]:
            assert row["assistant_request_id"] == request_id
            assert row["request_id"] == "step40-db-rag-1"
        assert payload["tool_executions"] == []

    def test_llm_and_tool_correlation_e2e(self, e2e_db) -> None:
        assert e2e_db.handler.calls == 0

        with TestClient(app) as client:
            chat = client.post(
                "/api/ai/chat", json={"question": _TOOL_QUESTION}
            )
            payload = chat.json()
            assert payload["route"] == "tool"
            request_id = payload["metadata"]["request_id"]
            e2e_db.request_ids.append(request_id)

            trace = client.get(f"{_TRACE_ENDPOINT}/{request_id}").json()

        assert e2e_db.handler.calls == 1
        assert trace["assistant_request_id"] == request_id
        assert len(trace["llm_usage"]) >= 1
        assert len(trace["tool_executions"]) >= 1
        for row in trace["llm_usage"]:
            assert row["assistant_request_id"] == request_id
            assert row["request_id"] == "step40-db-tool-1"
        for row in trace["tool_executions"]:
            assert row["request_id"] == request_id

    def test_cross_request_isolation_e2e(self, e2e_db) -> None:
        with TestClient(app) as client:
            first = client.post(
                "/api/ai/chat", json={"question": _TOOL_QUESTION}
            ).json()
            second = client.post(
                "/api/ai/chat", json={"question": _RAG_QUESTION}
            ).json()
            request_a = first["metadata"]["request_id"]
            request_b = second["metadata"]["request_id"]
            e2e_db.request_ids.extend([request_a, request_b])

            trace_a = client.get(f"{_TRACE_ENDPOINT}/{request_a}").json()
            trace_b = client.get(f"{_TRACE_ENDPOINT}/{request_b}").json()

        assert request_a != request_b
        assert {r["assistant_request_id"] for r in trace_a["llm_usage"]} <= {
            request_a
        }
        assert {r["request_id"] for r in trace_a["tool_executions"]} <= {
            request_a
        }
        assert {r["assistant_request_id"] for r in trace_b["llm_usage"]} <= {
            request_b
        }
        assert request_b not in str(trace_a)
        assert request_a not in str(trace_b)

    def test_empty_trace_from_real_wiring(self, e2e_db) -> None:
        with TestClient(app) as client:
            response = client.get(f"{_TRACE_ENDPOINT}/step40-not-exist")

        assert response.status_code == 200
        assert response.json() == {
            "assistant_request_id": "step40-not-exist",
            "llm_usage": [],
            "tool_executions": [],
            "rag_executions": [],   # Step 48（additive）
        }


__all__ = ["TestAssistantTraceCorrelationE2eDb"]
