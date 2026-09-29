"""RAG Trace Coverage Audit 测试（Phase 3.12 Step 42）。

本文件**只验证现状**（audit-only）：

    0 生产代码改动 · 0 DB 写入 · 0 real LLM · 0 网络 · 0 新持久化

覆盖：

    1. RAG 是否已在 Assistant Trace Scope 内执行（关联键是否"读得到"）
    2. RAG 是否产生 Tool Record（不应产生）
    3. RAG 经 /api/ai/chat 暴露的字段（仅来源元数据；无 chunk 正文）
    4. RAG 服务层是否存在 request_id / 持久化边界（现状 = 都没有）
    5. RAG 的检索事实当前只进日志（字段清单 + 无 request_id）
    6. 旧 /api/rag/answer 与 /api/ai/chat 的差异（含 chunk content / 无 trace id）
    7. Assistant Trace Read Model 里当前没有任何 RAG 段落
"""
from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import orchestrator_chat as root
from backend.app.main import app
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorService,
)
from backend.app.services.ai_router_service import (
    AIRouterService,
    RouteDecision,
    RouteType,
    ToolRegistryCapabilityAdapter,
)
from backend.app.services.assistant_trace import (
    current_assistant_request_id,
)
from backend.app.services.rag_service import RagResponse, RagSource
from backend.app.tools.registry import ToolRegistry

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RAG_QUESTION = "采购入库的操作步骤是什么"
_TRACE_ENDPOINT = "/api/observability/assistant-trace"
_CHUNK_CONTENT = "CHUNK-CONTENT-SENTINEL-STEP42"

#: RAG 相关模块（本次审计对象）。
#: 注（Phase 3.12 Step 43）：``rag_service.py`` 已建立 **Runtime** 观测边界
#: （读取 contextvar 里的 assistant_request_id 并产生进程内 Observation；
#: 无持久化）；其余三个模块仍然完全无 request_id。
_RAG_MODULES = (
    "backend/app/services/context_builder.py",
    "backend/app/reranker/client.py",
    "backend/app/services/vector_search_service.py",
)

def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.Module:
    return ast.parse(_source(relative))


class _ScopedRag:
    """RagService 替身：记录**执行期间**可见的 Assistant Trace ID。

    真实上生产 RagService 不读取该值（Step 42 审计结论）；本替身用于证明
    "RAG 已经在 Scope 内执行" —— 即关联键**已可读**，缺的只是记录点。
    """

    def __init__(self) -> None:
        self.seen_request_ids: list[str | None] = []
        self.calls = 0

    async def answer(self, query, **kwargs):  # noqa: ANN003, ANN201
        self.calls += 1
        self.seen_request_ids.append(current_assistant_request_id())
        return RagResponse(
            answer="采购入库需要先创建入库通知",
            sources=(
                RagSource(
                    chunk_id=7,
                    document_id=3,
                    chunk_index=1,
                    content=_CHUNK_CONTENT,
                    similarity=0.93,
                    metadata={"source": "sop"},
                ),
            ),
            used_chunks_count=1,
        )


class _TextToSQL:
    async def generate(self, question, **kwargs):  # noqa: ANN003
        raise AssertionError("T2S 不应在本审计路径被调用")


class _SqlExecutor:
    async def execute(self, sql, **kwargs):  # noqa: ANN003
        raise AssertionError("SQL Executor 不应在本审计路径被调用")


class _TableSelector:
    def select(self, question, *, schema, semantic, top_k=5):  # noqa: ANN001
        from backend.app.services.relevant_table_selector import (
            TableSelectionResult,
        )

        return TableSelectionResult(question=question, selections=())


class _ContextComposer:
    def compose(self, *, project, schema, semantic, tables=None, max_chars=4000):
        return "STEP42_CONTEXT"


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
def rag_env(monkeypatch):
    """真实 Orchestrator + Fake RAG（0 DB / 0 LLM）→ /api/ai/chat RAG 路由。"""
    rag = _ScopedRag()
    registry = ToolRegistry()
    orchestrator = AIOrchestratorService(
        router=AIRouterService(
            llm_fallback_enabled=False,
            tool_capabilities=ToolRegistryCapabilityAdapter(registry),
        ),
        rag_service=rag,
        tool_registry=registry,
        text_to_sql=_TextToSQL(),
        sql_executor=_SqlExecutor(),
        table_selector=_TableSelector(),
        context_composer=_ContextComposer(),
        project_context_provider=_ProjectProvider(),
    )
    monkeypatch.setattr(root, "_default_orchestrator", orchestrator)
    root._TOOL_EXECUTION_COLLECTOR.clear()
    yield rag
    root._TOOL_EXECUTION_COLLECTOR.clear()


def _ask_rag() -> tuple[Any, dict[str, Any]]:
    with TestClient(app) as client:
        response = client.post(
            "/api/ai/chat", json={"question": _RAG_QUESTION}
        )
        return response, response.json()


# ============================================================
# 1. RAG 在 Assistant Trace Scope 内执行（关联键已可读）
# ============================================================

class TestRagInsideTraceScope:
    def test_rag_runs_inside_assistant_trace_scope(self, rag_env) -> None:
        response, payload = _ask_rag()

        assert response.status_code == 200
        assert payload["route"] == "rag"
        request_id = payload["metadata"]["request_id"]
        assert isinstance(request_id, str) and request_id

        assert rag_env.calls == 1
        # RAG 执行期间**已经能**读到同一个 Assistant Trace ID
        assert rag_env.seen_request_ids == [request_id]

    def test_rag_path_creates_zero_tool_records(self, rag_env) -> None:
        _response, payload = _ask_rag()

        assert payload["metadata"]["request_id"]
        assert root._TOOL_EXECUTION_COLLECTOR.records() == ()
        with TestClient(app) as client:
            records = client.get("/api/observability/tools").json()
        assert records == {"records": []}

    def test_rag_metadata_keys_current_state(self, rag_env) -> None:
        """RAG metadata 现状：4 个既有键 + request_id（无检索耗时 / 无命中明细）。"""
        _response, payload = _ask_rag()

        assert set(payload["metadata"]) == {
            "decision_source", "route_reason", "knowledge_scope",
            "rag_used_chunks", "request_id",
        }
        # 检索事实不在 metadata（仅 used_chunks_count 一个计数）
        for absent in (
            "top_k", "elapsed_ms", "rerank_elapsed_ms", "chunk_ids",
            "document_ids", "similarities", "retrieval_count", "sources",
        ):
            assert absent not in payload["metadata"], absent


# ============================================================
# 2. RAG 经 /api/ai/chat 暴露的字段（安全边界现状）
# ============================================================

class TestRagHttpExposure:
    def test_sources_expose_metadata_only(self, rag_env) -> None:
        response, payload = _ask_rag()

        sources = payload["data"]["sources"]
        assert len(sources) == 1
        assert tuple(sources[0]) == (
            "chunk_id", "document_id", "chunk_index", "similarity", "metadata",
        )
        assert payload["data"]["used_chunks_count"] == 1
        # chunk 正文（RagSource.content）**不**进入响应
        assert _CHUNK_CONTENT not in response.text
        assert "content" not in sources[0]

    def test_rag_data_has_no_duration_fields(self, rag_env) -> None:
        _response, payload = _ask_rag()

        assert set(payload["data"]) == {"sources", "used_chunks_count"}
        # 无 retrieval / latency / rerank 元数据（审计缺口，非本阶段修复项）
        for absent in (
            "retrieval_count", "top_k", "elapsed_ms", "rerank_elapsed_ms",
            "document_count", "chunk_count",
        ):
            assert absent not in payload["data"], absent


# ============================================================
# 3. RAG 服务层：核心无 request_id / 无持久化 / 无 DTO 扩展
#    （Step 43：RagService 增加 **Runtime** 观测边界 —— 无持久化）
# ============================================================

class TestRagServiceCurrentState:
    @pytest.mark.parametrize("module", _RAG_MODULES)
    def test_no_request_id_in_rag_core_modules(self, module: str) -> None:
        """审计结论：Context / Reranker / Vector Search 仍**完全没有** request_id。

        （Step 43 只在 ``rag_service.py`` 建立 Runtime 观测边界；
          下游核心模块 0 修改 —— 保持审计结论的可验证性。）
        """
        source = _source(module)

        assert "request_id" not in source
        assert "assistant_request_id" not in source

    def test_rag_service_trace_usage_is_runtime_observation_only(self) -> None:
        """``rag_service.py`` 的 request_id 只用于 **Runtime Observation**：
        读取 contextvar + 构造进程内 Observation；**不**新增函数参数、
        **不**写日志 extra、**不**访问 DB / Repository。
        """
        import inspect

        from backend.app.services.rag_service import RagService

        source = _source("backend/app/services/rag_service.py")

        assert "current_assistant_request_id()" in source
        assert "RagExecutionObservation" in source
        # 公开 API 契约不变（request_id 不是参数）
        assert list(inspect.signature(RagService.answer).parameters) == [
            "self", "query", "top_k", "knowledge_scope",
        ]
        assert "request_id" not in inspect.signature(
            RagService.answer
        ).parameters
        # 观测走内存 DTO，不走日志 extra（日志仍只记检索事实）
        for extra in TestRagLoggingCoverage._log_extra_keys(
            "backend/app/services/rag_service.py"
        ):
            assert "request_id" not in extra
            assert "assistant_request_id" not in extra
        # 仍然没有持久化能力
        assert "sqlalchemy" not in source.lower()
        assert "Repository" not in source

    @pytest.mark.parametrize(
        "module",
        (
            "backend/app/services/rag_service.py",
            "backend/app/services/context_builder.py",
            "backend/app/reranker/client.py",
        ),
    )
    def test_no_persistence_boundary_in_rag_modules(self, module: str) -> None:
        """RAG / Context / Reranker 无 Repository / 无 ORM / 无写库。"""
        tree = _tree(module)
        imports = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        for forbidden in ("sqlalchemy", "backend.app.db"):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), (module, forbidden)
        identifiers = {
            node.attr.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        } | {
            node.id.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        }
        for forbidden in (
            "repository", "insert", "commit", "add", "flush", "session",
        ):
            assert forbidden not in identifiers, (module, forbidden)

    def test_rag_dto_shape_current_state(self) -> None:
        """RagResponse / RagSource 字段（无 latency / 无 trace id / 无 top_k）。"""
        assert [f.name for f in fields(RagResponse)] == [
            "answer", "sources", "used_chunks_count",
        ]
        assert [f.name for f in fields(RagSource)] == [
            "chunk_id", "document_id", "chunk_index", "content", "similarity",
            "metadata",
        ]

    def test_only_contract_defined_rag_persistence_exists(self) -> None:
        """Step 46 后的现状：**只有**契约定义的那一套 RAG 持久化。

        存在：``ai_ops.rag_execution_record`` + Repository + Adapter / Service /
        QueryService（均由 Step 45 契约定义）；
        仍不存在：任何 speculative 变体（rag_trace / retrieval_record /
        rag_metrics / RAG 持久化 HTTP API）。
        """
        from backend.app.db.base import Base

        assert sorted(
            name for name in Base.metadata.tables if "rag" in name.lower()
        ) == ["ai_ops.rag_execution_record"]
        offenders: list[str] = []
        backend_dir = _REPO_ROOT / "backend" / "app"
        for path in backend_dir.rglob("*.py"):
            relative = path.relative_to(_REPO_ROOT).as_posix()
            lowered = path.read_text(encoding="utf-8").lower()
            for token in ("rag_trace", "retrieval_record", "rag_metrics"):
                if token in lowered:
                    offenders.append(f"{relative}:{token}")
        assert offenders == [], offenders


# ============================================================
# 4. 检索事实当前只进日志（字段清单 / 无 request_id）
# ============================================================

class TestRagLoggingCoverage:
    @staticmethod
    def _log_extra_keys(module: str) -> list[set[str]]:
        """提取 `logger.info/warning/error(..., extra={...})` 的**键集合**。

        只看键名（值可能是表达式，例如 ``reranker is not None``），
        足以判断"日志记录了什么字段 / 有没有 request_id"。
        """
        extras: list[set[str]] = []
        for node in ast.walk(_tree(module)):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not (
                isinstance(function, ast.Attribute)
                and function.attr in {"info", "warning", "error"}
            ):
                continue
            for keyword in node.keywords:
                if keyword.arg != "extra":
                    continue
                value = keyword.value
                if not isinstance(value, ast.Dict):
                    continue
                extras.append({
                    key.value
                    for key in value.keys
                    if isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                })
        return extras

    def test_rag_service_logs_retrieval_facts_without_request_id(self) -> None:
        extras = self._log_extra_keys("backend/app/services/rag_service.py")

        assert extras, "RAG 服务应有 logger extra（检索事实）"
        keys = set().union(*extras)
        assert {"query_length", "top_k", "result_count", "elapsed_ms"} <= keys
        assert {"used_chunks_count", "rerank_elapsed_ms", "reranker_used"} <= keys
        for extra in extras:
            assert "request_id" not in extra
            assert "assistant_request_id" not in extra
            # 不得记录 query 原文 / answer / context 全文 / vector
            for forbidden in ("query", "answer", "context", "embedding"):
                assert forbidden not in extra, forbidden

    def test_vector_search_logs_without_request_id(self) -> None:
        extras = self._log_extra_keys(
            "backend/app/services/vector_search_service.py"
        )

        assert extras
        keys = set().union(*extras)
        assert {"query_length", "top_k", "result_count", "elapsed_ms"} <= keys
        for extra in extras:
            assert "request_id" not in extra


# ============================================================
# 5. 旧 /api/rag/answer 与 /api/ai/chat 的差异（现状）
# ============================================================

class TestLegacyRagEndpointCurrentState:
    def test_legacy_rag_endpoint_has_no_trace_id(self) -> None:
        from backend.app.api.rag import RagAnswerResponse

        assert list(RagAnswerResponse.model_fields) == [
            "answer", "sources", "used_chunks_count",
        ]
        source = _source("backend/app/api/rag.py")
        assert "request_id" not in source
        assert "assistant_trace" not in source

    def test_legacy_rag_source_includes_chunk_content(self) -> None:
        """旧端点历史上返回 chunk 正文（与 /api/ai/chat 的元数据白名单不同）。"""
        from backend.app.api.rag import RagSourceResponse

        assert "content" in RagSourceResponse.model_fields

    def test_ai_chat_defines_its_own_source_model_without_content(self) -> None:
        from backend.app.api.orchestrator_chat import ChatSourceResponse

        assert list(ChatSourceResponse.model_fields) == [
            "chunk_id", "document_id", "chunk_index", "similarity", "metadata",
        ]

    def test_both_use_the_same_rag_service_class(self) -> None:
        """共享 RagService 实现（但**不**共享 request context / 持久化）。"""
        assert "from backend.app.services.rag_service import" in _source(
            "backend/app/api/rag.py"
        )
        assert "RagService" in _source("backend/app/services/ai_orchestrator_service.py")


# ============================================================
# 6. Assistant Trace Read Model 当前没有 RAG 段落
# ============================================================

class TestAssistantTraceHasNoRagSection:
    def test_trace_schemas_have_no_rag_fields(self) -> None:
        spec = app.openapi()
        components = spec["components"]["schemas"]
        names = (
            "AssistantTraceResponse",
            "LLMUsageTraceResponse",
            "ToolExecutionTraceResponse",
        )
        text = str({name: components[name] for name in names}).lower()

        for absent in ("rag", "retrieval", "chunk", "reranker", "similarity"):
            assert absent not in text, absent

    def test_trace_endpoint_returns_only_two_sections(self) -> None:
        spec = app.openapi()
        path = "/api/observability/assistant-trace/{assistant_request_id}"
        properties = spec["components"]["schemas"][
            "AssistantTraceResponse"
        ]["properties"]

        assert list(properties) == [
            "assistant_request_id", "llm_usage", "tool_executions",
        ]
        assert path in spec["paths"]


__all__ = [
    "TestRagInsideTraceScope",
    "TestRagHttpExposure",
    "TestRagServiceCurrentState",
    "TestRagLoggingCoverage",
    "TestLegacyRagEndpointCurrentState",
    "TestAssistantTraceHasNoRagSection",
]
