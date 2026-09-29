"""Assistant Trace × RAG 集成——单元/契约测试（Phase 3.12 Step 48）。

**纯离线**（0 DB / 0 real LLM / 0 网络）：RAG 持久化读边界用 Fake，
其余全部真实（Trace API / AssistantTraceQueryService / 映射 / response_model）。

覆盖：
    mapping（Row → View → HTTP DTO；13 字段，无主键 id）· empty ·
    ordering（id ASC 保持）· correlation · failure（502，不是 []）·
    security（无敏感字段 / 无正文）· composition（Persistent 读边界、
    不引入 Runtime Collector）· backward compatibility（additive）。
"""
from __future__ import annotations

import ast
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import assistant_trace as trace_module
from backend.app.api.assistant_trace import (
    AssistantTraceResponse,
    RagExecutionTraceResponse,
)
from backend.app.api.orchestrator_chat import (
    get_assistant_trace_query_service,
    get_rag_execution_persistent_query_service,
)
from backend.app.db.rag_execution_repository import (
    RagExecutionRecordRow,
    RagExecutionRepositoryError,
)
from backend.app.main import app
from backend.app.services.assistant_trace_query_service import (
    AssistantTraceQueryService,
    AssistantTraceView,
    RagExecutionTraceView,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TRACE_API_MODULE = "backend/app/api/assistant_trace.py"
_TRACE_QUERY_MODULE = "backend/app/services/assistant_trace_query_service.py"

_ENDPOINT = "/api/observability/assistant-trace"
_STARTED = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)
_SENTINEL_CHUNK = "STEP48-CHUNK-CONTENT-LEAK"

_RAG_FIELDS = (
    "request_id", "started_at", "finished_at", "duration_ms", "result_count",
    "used_chunks_count", "top_k", "context_truncated", "context_chars",
    "reranker_used", "rerank_elapsed_ms", "chunk_ids", "document_ids",
)
_FORBIDDEN_KEYS = (
    "query", "answer", "content", "similarity", "embedding", "prompt",
    "messages", "raw_response", "sql", "password", "api_key", "authorization",
    "database_url", "session", "connection", "traceback", "id", "project_id",
)


def _row(**overrides: Any) -> RagExecutionRecordRow:
    values: dict[str, Any] = {
        "id": 7,
        "request_id": "A",
        "started_at": _STARTED,
        "finished_at": _STARTED + timedelta(milliseconds=9.0),
        "duration_ms": 9.0,
        "result_count": 3,
        "used_chunks_count": 2,
        "top_k": 5,
        "context_truncated": True,
        "context_chars": 321,
        "reranker_used": True,
        "rerank_elapsed_ms": 2.5,
        "chunk_ids": (11, 13),
        "document_ids": (4,),
    }
    values.update(overrides)
    return RagExecutionRecordRow(**values)


class _FakeLlmService:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []
        self.calls: list[str] = []

    def list_by_assistant_request_id(self, assistant_request_id: str) -> list[Any]:
        self.calls.append(assistant_request_id)
        return list(self._rows)


class _FakeToolService:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []
        self.calls: list[str] = []

    def list_by_request_id(self, request_id: str) -> list[Any]:
        self.calls.append(request_id)
        return list(self._rows)


class _FakeRagService:
    """RAG **持久化**读边界替身（离线零 DB；可注入行 / 错误）。"""

    def __init__(
        self,
        rows: list[RagExecutionRecordRow] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._rows = rows if rows is not None else []
        self._error = error
        self.calls: list[str] = []

    def list_by_request_id(self, request_id: str) -> list[RagExecutionRecordRow]:
        self.calls.append(request_id)
        if self._error is not None:
            raise self._error
        return list(self._rows)


def _service(**kwargs: Any) -> AssistantTraceQueryService:
    return AssistantTraceQueryService(
        llm_usage_query_service=_FakeLlmService(),
        tool_observability_query_service=_FakeToolService(),
        rag_execution_query_service=(
            kwargs["rag"] if "rag" in kwargs else _FakeRagService()
        ),
    )


@pytest.fixture()
def install(monkeypatch):
    def _install(rag: _FakeRagService) -> _FakeRagService:
        monkeypatch.setattr(
            trace_module,
            "get_assistant_trace_query_service",
            lambda: _service(rag=rag),
        )
        return rag

    return _install


# ============================================================
# 1. Mapping（Row → View → HTTP DTO）
# ============================================================

class TestMapping:
    def test_view_fields_are_thirteen_without_primary_key(self) -> None:
        assert [f.name for f in fields(RagExecutionTraceView)] == list(
            _RAG_FIELDS
        )
        assert "id" not in _RAG_FIELDS

    def test_row_to_view_explicit_mapping(self) -> None:
        view = RagExecutionTraceView.from_row(_row())

        assert view.request_id == "A"
        assert view.duration_ms == 9.0
        assert view.result_count == 3
        assert view.used_chunks_count == 2
        assert view.top_k == 5
        assert view.context_truncated is True
        assert view.context_chars == 321
        assert view.reranker_used is True
        assert view.rerank_elapsed_ms == 2.5
        assert view.chunk_ids == (11, 13)
        assert view.document_ids == (4,)
        assert not hasattr(view, "id")

    def test_http_dto_fields(self) -> None:
        assert list(RagExecutionTraceResponse.model_fields) == list(
            _RAG_FIELDS
        )

    def test_end_to_end_mapping_via_http(self, install) -> None:
        install(_FakeRagService([_row()]))

        with TestClient(app) as client:
            payload = client.get(f"{_ENDPOINT}/A").json()

        item = payload["rag_executions"][0]
        assert tuple(item) == _RAG_FIELDS
        assert item["request_id"] == "A"
        assert item["chunk_ids"] == [11, 13]
        assert item["document_ids"] == [4]
        assert item["rerank_elapsed_ms"] == 2.5
        assert "id" not in item

    def test_null_rerank_stays_null_through_trace(self, install) -> None:
        install(_FakeRagService([_row(rerank_elapsed_ms=None)]))

        with TestClient(app) as client:
            item = client.get(f"{_ENDPOINT}/A").json()["rag_executions"][0]

        assert item["rerank_elapsed_ms"] is None          # 不是 0


# ============================================================
# 2. Empty / Ordering
# ============================================================

class TestEmptyAndOrdering:
    def test_no_rag_record_returns_empty_list_200(self, install) -> None:
        install(_FakeRagService([]))

        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/A")

        assert response.status_code == 200                 # 不是 404 / 502
        assert response.json()["rag_executions"] == []

    def test_ordering_preserved_id_asc(self, install) -> None:
        install(
            _FakeRagService(
                [
                    _row(id=1, chunk_ids=(1, 2)),
                    _row(id=2, chunk_ids=(3,)),
                    _row(id=3, chunk_ids=(4,)),
                ]
            )
        )

        with TestClient(app) as client:
            items = client.get(f"{_ENDPOINT}/A").json()["rag_executions"]

        assert [item["chunk_ids"] for item in items] == [[1, 2], [3], [4]]

    def test_view_does_not_reorder(self) -> None:
        service = _service(
            rag=_FakeRagService([_row(id=2), _row(id=1)])
        )

        trace = service.get_trace("A")

        # 仓储负责 id ASC；组合层原样保留下游顺序（不重排）
        assert len(trace.rag_executions) == 2
        assert isinstance(trace.rag_executions, tuple)


# ============================================================
# 3. Correlation
# ============================================================

class TestCorrelation:
    def test_rag_request_id_equals_assistant_request_id(self, install) -> None:
        rag = install(_FakeRagService([_row(request_id="A")]))

        with TestClient(app) as client:
            payload = client.get(f"{_ENDPOINT}/A").json()

        assert payload["assistant_request_id"] == "A"
        assert {item["request_id"] for item in payload["rag_executions"]} == {"A"}
        assert rag.calls == ["A"]

    def test_three_sources_share_one_id(self) -> None:
        service = _service(rag=_FakeRagService([_row(request_id="A")]))
        trace = service.get_trace("A")

        assert trace.assistant_request_id == "A"
        assert trace.rag_executions[0].request_id == "A"


# ============================================================
# 4. Failure Semantics（502，不是 []）
# ============================================================

class TestFailureSemantics:
    def test_repository_error_propagates_from_service(self) -> None:
        service = _service(
            rag=_FakeRagService(error=RagExecutionRepositoryError("db down"))
        )

        with pytest.raises(RagExecutionRepositoryError):
            service.get_trace("A")

    def test_repository_error_is_http_502(self, install) -> None:
        install(_FakeRagService(error=RagExecutionRepositoryError("db down")))

        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/A")

        assert response.status_code == 502
        assert set(response.json()) == {"detail"}
        for leaked in ("Traceback", "SELECT", "postgresql://", "ai_ops"):
            assert leaked not in response.text, leaked

    def test_value_error_still_400_and_unexpected_still_500(
        self, monkeypatch
    ) -> None:
        for error, status in (
            (ValueError("非法输入: x"), 400),
            (RuntimeError("boom"), 500),
        ):
            class _Broken:
                def get_trace(self, assistant_request_id: str) -> Any:
                    raise error

            monkeypatch.setattr(
                trace_module,
                "get_assistant_trace_query_service",
                lambda: _Broken(),
            )
            with TestClient(app) as client:
                assert client.get(f"{_ENDPOINT}/A").status_code == status


# ============================================================
# 5. Security
# ============================================================

class TestSecurity:
    def test_no_forbidden_key_in_response(self, install) -> None:
        install(_FakeRagService([_row()]))

        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/A")

        for forbidden in _FORBIDDEN_KEYS:
            assert f'"{forbidden}":' not in response.text, forbidden

    def test_chunk_content_never_in_trace(self, install) -> None:
        install(_FakeRagService([_row()]))

        with TestClient(app) as client:
            response = client.get(f"{_ENDPOINT}/A")

        assert _SENTINEL_CHUNK not in response.text

    def test_rag_dto_has_no_payload_fields(self) -> None:
        dto = RagExecutionTraceResponse(
            request_id="A",
            started_at=_STARTED,
            finished_at=_STARTED,
            duration_ms=1.0,
            result_count=1,
            used_chunks_count=1,
            top_k=5,
            context_truncated=False,
            context_chars=1,
            reranker_used=False,
        )

        assert dto.chunk_ids == [] and dto.document_ids == []
        assert dto.rerank_elapsed_ms is None


# ============================================================
# 6. Composition / Runtime vs Persistent
# ============================================================

class TestCompositionAndRuntimeIsolation:
    def test_production_accessor_injects_persistent_rag_boundary(self) -> None:
        service = get_assistant_trace_query_service()

        assert service.rag_execution_query_service is (
            get_rag_execution_persistent_query_service()
        )

    def test_trace_modules_do_not_import_runtime_collectors(self) -> None:
        for relative in (_TRACE_QUERY_MODULE, _TRACE_API_MODULE):
            tree = ast.parse((_REPO_ROOT / relative).read_text(encoding="utf-8"))
            names = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                for alias in node.names
            } | {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            } | {
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            }
            for forbidden in (
                "InMemoryRagExecutionCollector",
                "InMemoryToolExecutionCollector",
                "rag_observability_query_service",
                "rag_observability_runtime",
            ):
                assert forbidden not in names, (relative, forbidden)

    def test_api_layer_creates_no_repository_or_session(self) -> None:
        source = (_REPO_ROOT / _TRACE_API_MODULE).read_text(encoding="utf-8")
        tree = ast.parse(source)
        used = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "RagExecutionRepository", "RagExecutionPersistenceService",
            "get_session_factory", "sessionmaker", "Session",
        ):
            assert forbidden not in used, forbidden


# ============================================================
# 7. Backward Compatibility / 无新增 API
# ============================================================

class TestBackwardCompatibility:
    def test_legacy_fields_unchanged(self) -> None:
        assert list(AssistantTraceResponse.model_fields)[:3] == [
            "assistant_request_id", "llm_usage", "tool_executions",
        ]
        assert list(AssistantTraceResponse.model_fields)[3:] == [
            "rag_executions",
        ]

    def test_view_accepts_three_sources(self) -> None:
        trace = AssistantTraceView(
            assistant_request_id="A",
            llm_usage=(),
            tool_executions=(),
            rag_executions=(RagExecutionTraceView.from_row(_row()),),
        )

        assert len(trace.rag_executions) == 1

    def test_no_new_rag_http_api(self) -> None:
        paths = sorted(app.openapi()["paths"])

        assert [p for p in paths if "rag" in p.lower()] == ["/api/rag/answer"]
        assert not [
            p for p in paths if "rag" in p.lower() and "observability" in p
        ]

    def test_legacy_rag_answer_unchanged(self) -> None:
        from backend.app.api.rag import RagAnswerResponse

        assert list(RagAnswerResponse.model_fields) == [
            "answer", "sources", "used_chunks_count",
        ]


__all__ = [
    "TestMapping",
    "TestEmptyAndOrdering",
    "TestCorrelation",
    "TestFailureSemantics",
    "TestSecurity",
    "TestCompositionAndRuntimeIsolation",
    "TestBackwardCompatibility",
]
