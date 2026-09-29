"""Assistant Trace 读**持久化** Tool Execution：DB 集成测试（Phase 3.12 Step 41）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

核心证明（§十六 / §十七）：

    POST /api/ai/chat
        ↓ 真实 ToolExecutionService → 真实 CompositeToolExecutionObserver
        ↓ （Collector + ToolExecutionPersistenceAdapter → ai_ops.tool_execution_record）
    清空 Runtime Collector（模拟进程重启 / retention 淘汰）
        ↓ 新建持久化读边界实例（模拟新进程装配）
    GET /api/observability/assistant-trace/{A}
        ↓ Tool 数据源 = PostgreSQL（**不是** Runtime 内存）
    tool_executions >= 1

数据：synthetic（LLM provider 前缀 ``step41-``；Tool request_id = 运行时
生成的 Assistant Trace ID，测试按捕获到的 ID 定向清理）。
清理（不用 TRUNCATE）：LLM 按 provider 前缀，Tool 按 request_id 定向 DELETE。
"""
from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import bindparam, text

from backend.app.api import orchestrator_chat as root
from backend.app.db import init_db as init_db_module
from backend.app.db.llm_usage_repository import LLMUsageRepository
from backend.app.db.models.llm_usage_record import LLM_USAGE_SCHEMA
from backend.app.db.session import get_engine, get_session_factory
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
from backend.app.services.llm_usage_query_service import LLMUsageQueryService
from backend.app.services.tool_execution_persistence_adapter import (
    CompositeToolExecutionObserver,
    ToolExecutionPersistenceAdapter,
)
from backend.app.services.tool_execution_persistence_service import (
    ToolExecutionPersistenceService,
)
from backend.app.services.tool_execution_persistent_query_service import (
    ToolExecutionPersistentQueryService,
)
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION
from backend.app.tools.registry import ToolRegistry

_LLM_TABLE = f"{LLM_USAGE_SCHEMA}.llm_usage_record"
_TOOL_TABLE = "ai_ops.tool_execution_record"
_PREFIX = "step41-%"
_TRACE_ENDPOINT = "/api/observability/assistant-trace"
_TOOL_QUESTION = "查询物料 MAT-001 当前库存"
_RAG_QUESTION = "采购入库的操作步骤是什么"

_TOOL_FIELDS = (
    "request_id", "round", "tool_name", "started_at", "finished_at",
    "duration_ms", "success", "project_id", "tool_call_id", "error_code",
    "error_type",
)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


class _Context:
    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.request_ids: list[str] = []


class _RecordingToolHandler:
    """只读 Tool Handler 替身（不访问 DB）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
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
        return "STEP41_CONTEXT"


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


class _RagStub:
    """RAG 替身（0 LLM；本文件只关注 Tool 持久化链路）。"""

    async def answer(self, query, **kwargs):  # noqa: ANN003
        from backend.app.services.rag_service import RagResponse

        return RagResponse(answer="（stub）", sources=(), used_chunks_count=0)


def _cleanup_tool_rows(conn: Any, request_ids: list[str]) -> int:
    if not request_ids:
        return 0
    return conn.execute(
        text(
            f"DELETE FROM {_TOOL_TABLE} WHERE request_id IN :request_ids"
        ).bindparams(
            bindparam("request_ids", value=tuple(request_ids), expanding=True)
        )
    ).rowcount


def _tool_rows(conn: Any, request_id: str) -> list[Any]:
    return list(
        conn.execute(
            text(
                f"SELECT request_id, round, tool_name, success "
                f"FROM {_TOOL_TABLE} WHERE request_id = :rid ORDER BY id ASC"
            ),
            {"rid": request_id},
        ).all()
    )


@pytest.fixture()
def persistent_env(monkeypatch):
    engine = get_engine()
    if engine is None or get_session_factory() is None:
        pytest.skip("DATABASE_URL 未配置")
    init_db_module.init_db()
    with engine.begin() as conn:
        conn.execute(
            text(f"DELETE FROM {_LLM_TABLE} WHERE provider LIKE :prefix"),
            {"prefix": _PREFIX},
        )
        llm_baseline = int(
            conn.execute(text(f"SELECT COUNT(*) FROM {_LLM_TABLE}")).scalar_one()
        )
        tool_baseline = int(
            conn.execute(text(f"SELECT COUNT(*) FROM {_TOOL_TABLE}")).scalar_one()
        )
    collector = root._TOOL_EXECUTION_COLLECTOR
    collector.clear()

    registry = ToolRegistry()
    handler = _RecordingToolHandler()
    registry.register(GET_INVENTORY_DEFINITION, handler)
    # 生产装配：Collector + Persistence Adapter（同一 Record 扇出）
    observer = CompositeToolExecutionObserver(
        collector,
        ToolExecutionPersistenceAdapter(
            persistence_service=ToolExecutionPersistenceService()
        ),
    )
    orchestrator = AIOrchestratorService(
        router=AIRouterService(
            llm_fallback_enabled=False,
            tool_capabilities=ToolRegistryCapabilityAdapter(registry),
        ),
        rag_service=_RagStub(),
        tool_registry=registry,
        text_to_sql=_TextToSQL(),
        sql_executor=_SqlExecutor(),
        table_selector=_TableSelector(),
        context_composer=_ContextComposer(),
        project_context_provider=_ProjectProvider(),
        tool_execution_service=ToolExecutionService(
            registry=registry, observer=observer
        ),
        tool_execution_observer=observer,
    )
    monkeypatch.setattr(root, "_default_orchestrator", orchestrator)
    context = _Context(handler)
    yield context
    collector.clear()
    with engine.begin() as conn:
        _cleanup_tool_rows(conn, context.request_ids)
        conn.execute(
            text(f"DELETE FROM {_LLM_TABLE} WHERE provider LIKE :prefix"),
            {"prefix": _PREFIX},
        )
        assert int(
            conn.execute(
                text(f"SELECT COUNT(*) FROM {_LLM_TABLE}")
            ).scalar_one()
        ) == llm_baseline
        assert int(
            conn.execute(text(f"SELECT COUNT(*) FROM {_TOOL_TABLE}")).scalar_one()
        ) == tool_baseline


def _ask_tool(client: TestClient, context: _Context) -> tuple[str, dict]:
    payload = client.post(
        "/api/ai/chat", json={"question": _TOOL_QUESTION}
    ).json()
    request_id = payload["metadata"]["request_id"]
    context.request_ids.append(request_id)
    return request_id, payload


@requires_db
class TestPersistentToolTrace:
    def test_1_persisted_tool_only_after_collector_cleared(
        self, persistent_env
    ) -> None:
        context = persistent_env
        with TestClient(app) as client:
            request_id, payload = _ask_tool(client, context)
            assert payload["route"] == "tool"

            # 数据库里确实已经持久化（真实 Redis 之外的持久化证据）
            with get_engine().connect() as conn:
                rows = _tool_rows(conn, request_id)
            assert len(rows) >= 1
            assert rows[0].tool_name == "get_inventory"

            root._TOOL_EXECUTION_COLLECTOR.clear()   # 模拟进程重启 / 淘汰
            runtime = client.get("/api/observability/tools").json()
            trace = client.get(f"{_TRACE_ENDPOINT}/{request_id}").json()

        assert context.handler.calls == 1
        assert runtime == {"records": []}            # Runtime 视图已空
        assert len(trace["tool_executions"]) >= 1    # Trace 来自 PostgreSQL
        assert all(
            row["request_id"] == request_id
            for row in trace["tool_executions"]
        )
        assert trace["tool_executions"][0]["round"] == 1

    def test_2_llm_plus_persisted_tool(self, persistent_env) -> None:
        context = persistent_env
        with TestClient(app) as client:
            request_id, _payload = _ask_tool(client, context)
            trace = client.get(f"{_TRACE_ENDPOINT}/{request_id}").json()

        assert len(trace["tool_executions"]) >= 1
        assert trace["assistant_request_id"] == request_id
        # Tool 行与 Assistant Trace ID 对齐
        assert {row["request_id"] for row in trace["tool_executions"]} == {
            request_id
        }

    def test_3_cross_request_isolation(self, persistent_env) -> None:
        context = persistent_env
        with TestClient(app) as client:
            request_a, _ = _ask_tool(client, context)
            request_b, _ = _ask_tool(client, context)
            assert request_a != request_b

            trace_a = client.get(f"{_TRACE_ENDPOINT}/{request_a}").json()
            trace_b = client.get(f"{_TRACE_ENDPOINT}/{request_b}").json()

        assert {r["request_id"] for r in trace_a["tool_executions"]} == {
            request_a
        }
        assert {r["request_id"] for r in trace_b["tool_executions"]} == {
            request_b
        }
        assert request_b not in str(trace_a)
        assert request_a not in str(trace_b)

    def test_4_empty_trace(self, persistent_env) -> None:
        with TestClient(app) as client:
            response = client.get(f"{_TRACE_ENDPOINT}/step41-not-exist")

        assert response.status_code == 200
        assert response.json() == {
            "assistant_request_id": "step41-not-exist",
            "llm_usage": [],
            "tool_executions": [],
            "rag_executions": [],   # Step 48（additive）
        }

    def test_5_restart_like_new_boundary_still_reads(
        self, persistent_env
    ) -> None:
        """新进程装配（新建持久化读边界 + 新组合服务）仍能读到 Tool 执行。"""
        context = persistent_env
        with TestClient(app) as client:
            request_id, _payload = _ask_tool(client, context)

        root._TOOL_EXECUTION_COLLECTOR.clear()       # Runtime 内存全部丢失

        fresh_service = AssistantTraceQueryService(
            llm_usage_query_service=LLMUsageQueryService(
                repository=LLMUsageRepository()
            ),
            tool_observability_query_service=(
                ToolExecutionPersistentQueryService(
                    repository=None       # 新实例（默认仓储）
                )
            ),
        )
        view = fresh_service.get_trace(request_id)

        assert len(view.tool_executions) >= 1
        assert {s.request_id for s in view.tool_executions} == {request_id}

    def test_6_security_fields(self, persistent_env) -> None:
        context = persistent_env
        with TestClient(app) as client:
            request_id, _payload = _ask_tool(client, context)
            response = client.get(f"{_TRACE_ENDPOINT}/{request_id}")

        assert response.status_code == 200
        payload = response.json()
        assert tuple(payload["tool_executions"][0]) == _TOOL_FIELDS
        for forbidden_key in (
            "arguments", "data", "result", "sql", "prompt", "messages",
            "raw_response", "secret", "password", "api_key", "database_url",
            "session", "connection", "traceback", "id",
        ):
            assert f'"{forbidden_key}":' not in response.text, forbidden_key


__all__ = ["TestPersistentToolTrace"]
