"""Assistant Trace × RAG Integration Contract 测试（Phase 3.12 Step 47）。

**纯设计/审计**：不修改 AssistantTraceResponse / AssistantTraceQueryService /
任何生产代码；0 DB / 0 网络 / 0 real LLM。

锁定两件事：

1. **现状**（不得被未来实现破坏）：Trace 响应字段 / 组合层语义 /
   错误映射 / Runtime≠Persistent / 无 project 授权。
2. **设计契约**（Step 48 实现时必须满足）：RAG 数据源 = Persistent
   QueryService；DTO 字段裁剪；关联键仍是 ``request_id``；
   empty ≠ failure；ordering = id ASC；additive 且向后兼容。
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

import pytest

from backend.app.api import assistant_trace as trace_api
from backend.app.api.assistant_trace import (
    AssistantTraceResponse,
    LLMUsageTraceResponse,
    ToolExecutionTraceResponse,
)
from backend.app.db.rag_execution_repository import (
    RagExecutionRecordRow,
    RagExecutionRepositoryError,
)
from backend.app.main import app
from backend.app.services.assistant_trace_query_service import (
    AssistantTraceQueryService,
    AssistantTraceView,
)
from backend.app.services.rag_execution_persistent_query_service import (
    RagExecutionPersistentQueryService,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TRACE_API_MODULE = "backend/app/api/assistant_trace.py"
_TRACE_QUERY_MODULE = "backend/app/services/assistant_trace_query_service.py"

#: Persistent Record → Trace DTO 的**设计映射**（Step 47 契约）
#: 说明：DB 主键 ``id`` 被**刻意裁剪**（与 ToolExecutionTraceResponse 一致；
#: 排序由仓储 ``id ASC`` 保证，不需要把存储主键暴露给 HTTP 客户端）。
_RAG_TRACE_FIELDS: Final[tuple[str, ...]] = (
    "request_id",
    "started_at",
    "finished_at",
    "duration_ms",
    "result_count",
    "used_chunks_count",
    "top_k",
    "context_truncated",
    "context_chars",
    "reranker_used",
    "rerank_elapsed_ms",
    "chunk_ids",
    "document_ids",
)

#: Trace 中**允许**出现的 RAG 字段（安全白名单 = 设计字段 + 无敏感项）
_FORBIDDEN_FIELDS: Final[tuple[str, ...]] = (
    "query", "answer", "content", "similarity", "embedding", "prompt",
    "messages", "raw_response", "sql", "password", "api_key",
    "authorization", "database_url", "session", "connection", "traceback",
    "project_id", "id",
)

#: 当前 HTTP 响应字段（不得改名 / 不得删除 / 不得改语义）
#: Step 48/64 之前的字段顺序（三个 legacy 字段）。
_CURRENT_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "assistant_request_id",
    "llm_usage",
    "tool_executions",
)

#: Step 64：additive 的终态字段（位于 assistant_request_id 之后）。
_RESPONSE_OUTCOME_FIELD: Final[str] = "outcome"

_STARTED = datetime(2026, 9, 29, 10, 0, 0, tzinfo=timezone.utc)


def _row(**overrides: Any) -> RagExecutionRecordRow:
    values: dict[str, Any] = {
        "id": 42,
        "request_id": "A",
        "started_at": _STARTED,
        "finished_at": _STARTED + timedelta(milliseconds=7.5),
        "duration_ms": 7.5,
        "result_count": 3,
        "used_chunks_count": 2,
        "top_k": 5,
        "context_truncated": False,
        "context_chars": 210,
        "reranker_used": True,
        "rerank_elapsed_ms": 1.25,
        "chunk_ids": (11, 13),
        "document_ids": (7,),
    }
    values.update(overrides)
    return RagExecutionRecordRow(**values)


@dataclass(frozen=True)
class _ProposedRagExecutionTrace:
    """**设计态** RAG Trace DTO（本测试专用；生产尚未实现）。"""

    request_id: str
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    result_count: int
    used_chunks_count: int
    top_k: int
    context_truncated: bool
    context_chars: int
    reranker_used: bool
    rerank_elapsed_ms: float | None
    chunk_ids: tuple[int, ...]
    document_ids: tuple[int, ...]

    @classmethod
    def from_row(cls, row: RagExecutionRecordRow) -> "_ProposedRagExecutionTrace":
        """**显式逐字段映射**（不使用 vars / __dict__ / asdict / model_dump）。"""
        return cls(
            request_id=row.request_id,
            started_at=row.started_at,
            finished_at=row.finished_at,
            duration_ms=row.duration_ms,
            result_count=row.result_count,
            used_chunks_count=row.used_chunks_count,
            top_k=row.top_k,
            context_truncated=row.context_truncated,
            context_chars=row.context_chars,
            reranker_used=row.reranker_used,
            rerank_elapsed_ms=row.rerank_elapsed_ms,
            chunk_ids=row.chunk_ids,
            document_ids=row.document_ids,
        )


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


class _BoomRepository:
    def get_by_request_id(self, request_id: str) -> list[RagExecutionRecordRow]:
        raise RagExecutionRepositoryError("db down")


class _SpyRepository:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_by_request_id(self, request_id: str) -> list[RagExecutionRecordRow]:
        self.calls.append(request_id)
        return []


# ============================================================
# 1. 现状：Trace Contract（未来实现不得破坏）
# ============================================================

class TestCurrentTraceContract:
    def test_response_model_fields(self) -> None:
        """Step 48/64 实现后：三个 legacy 字段 + additive 的 outcome / rag_executions。"""
        fields = list(AssistantTraceResponse.model_fields)

        assert fields == [
            "assistant_request_id",
            _RESPONSE_OUTCOME_FIELD,                 # Step 64（additive）
            *_CURRENT_RESPONSE_FIELDS[1:],
            "rag_executions",                        # Step 48（additive）
        ]
        for name in _CURRENT_RESPONSE_FIELDS:
            assert name in fields                    # legacy 字段未删除 / 未改名

    def test_read_model_is_frozen_and_three_sources(self) -> None:
        assert [f.name for f in fields(AssistantTraceView)] == [
            "assistant_request_id", "llm_usage", "tool_executions",
            "rag_executions",
            _RESPONSE_OUTCOME_FIELD,                 # Step 64（末位带默认值）
        ]
        with pytest.raises(ValueError):
            AssistantTraceView(
                assistant_request_id="A", llm_usage=[], tool_executions=[],
                rag_executions=[],
            )

    def test_nested_dto_field_counts(self) -> None:
        assert len(LLMUsageTraceResponse.model_fields) == 9
        assert len(ToolExecutionTraceResponse.model_fields) == 11

    def test_error_mapping_current_state(self) -> None:
        tree = ast.parse(_source(_TRACE_API_MODULE))
        handlers = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
        ]
        names: list[str] = []
        for handler in handlers:
            if handler.type is None:
                continue
            if isinstance(handler.type, ast.Tuple):
                names.extend(
                    element.id
                    for element in handler.type.elts
                    if isinstance(element, ast.Name)
                )
            elif isinstance(handler.type, ast.Name):
                names.append(handler.type.id)

        assert "LLMUsageRepositoryError" in names
        assert "ToolExecutionRepositoryError" in names
        # Step 48 已修复：RAG 仓储错误同样映射 502（不再落到 catch-all → 500）
        assert "RagExecutionRepositoryError" in names

    def test_http_contract_exposes_rag_executions(self) -> None:
        schema = app.openapi()["components"]["schemas"][
            "AssistantTraceResponse"
        ]["properties"]

        assert "rag_executions" in schema
        assert list(schema) == [
            "assistant_request_id",
            _RESPONSE_OUTCOME_FIELD,                 # Step 64（additive）
            *_CURRENT_RESPONSE_FIELDS[1:],
            "rag_executions",                        # Step 48（additive）
        ]

    def test_trace_service_has_no_write_capability(self) -> None:
        for forbidden in ("execute", "record", "clear", "metrics", "persist"):
            assert forbidden not in dir(AssistantTraceQueryService), forbidden


# ============================================================
# 2. RAG Persistent Read Boundary（唯一允许的数据源）
# ============================================================

class TestRagReadBoundaryCapabilities:
    def test_list_by_request_id_exists_and_is_read_only(self) -> None:
        assert callable(
            getattr(
                RagExecutionPersistentQueryService, "list_by_request_id", None
            )
        )
        for forbidden in ("create", "delete", "update", "clear", "record"):
            assert forbidden not in dir(RagExecutionPersistentQueryService)

    def test_empty_result_is_empty_list(self) -> None:
        service = RagExecutionPersistentQueryService(
            repository=_SpyRepository()  # type: ignore[arg-type]
        )

        assert service.list_by_request_id("A") == []

    def test_failure_is_typed_error_not_empty(self) -> None:
        service = RagExecutionPersistentQueryService(
            repository=_BoomRepository()  # type: ignore[arg-type]
        )

        with pytest.raises(RagExecutionRepositoryError):
            service.list_by_request_id("A")

    @pytest.mark.parametrize(
        ("bad", "expected"),
        [
            (None, TypeError),
            (123, TypeError),
            ("", ValueError),
            ("   ", ValueError),
            ("A" * 129, ValueError),
        ],
    )
    def test_validation_precedes_repository(self, bad, expected) -> None:  # noqa: ANN001
        repository = _SpyRepository()
        service = RagExecutionPersistentQueryService(
            repository=repository  # type: ignore[arg-type]
        )

        with pytest.raises(expected):
            service.list_by_request_id(bad)

        assert repository.calls == []

    def test_ordering_is_id_asc(self) -> None:
        from backend.app.db.rag_execution_repository import (
            RagExecutionRepository,
        )

        sql = str(RagExecutionRepository().build_request_select("A"))

        assert "ORDER BY" in sql.upper()
        assert "id ASC" in sql

    def test_explicit_fields_only(self) -> None:
        from backend.app.db.rag_execution_repository import (
            RAG_EXECUTION_READ_COLUMNS,
        )

        sql = str(
            __import__(
                "backend.app.db.rag_execution_repository", fromlist=["x"]
            ).RagExecutionRepository().build_request_select("A")
        )
        assert "SELECT *" not in sql.upper()
        assert RAG_EXECUTION_READ_COLUMNS == ("id", *_RAG_TRACE_FIELDS)


# ============================================================
# 3. 设计态映射：Persistent Record → Trace DTO
# ============================================================

class TestProposedTraceDtoMapping:
    def test_all_trace_fields_present(self) -> None:
        view = _ProposedRagExecutionTrace.from_row(_row())

        assert tuple(f.name for f in fields(_ProposedRagExecutionTrace)) == (
            _RAG_TRACE_FIELDS
        )
        assert view.request_id == "A"
        assert view.result_count == 3
        assert view.used_chunks_count == 2
        assert view.rerank_elapsed_ms == 1.25      # None → None，不是 0
        assert view.chunk_ids == (11, 13)           # 顺序保持
        assert view.document_ids == (7,)

    def test_database_primary_key_is_dropped(self) -> None:
        """DB 主键不进 Trace（与 ToolExecutionTraceResponse 一致）。"""
        view = _ProposedRagExecutionTrace.from_row(_row(id=99))

        assert "id" not in _RAG_TRACE_FIELDS
        assert not hasattr(view, "record_id")

    def test_null_rerank_stays_null(self) -> None:
        view = _ProposedRagExecutionTrace.from_row(_row(rerank_elapsed_ms=None))

        assert view.rerank_elapsed_ms is None

    def test_ordering_preserved_across_rows(self) -> None:
        rows = [
            _row(request_id="A", chunk_ids=(1, 2)),
            _row(request_id="A", chunk_ids=(3,)),
        ]
        views = [_ProposedRagExecutionTrace.from_row(row) for row in rows]

        assert [view.chunk_ids for view in views] == [(1, 2), (3,)]


# ============================================================
# 4. Security（Trace 白名单 / 禁止字段）
# ============================================================

class TestTraceSecurity:
    def test_no_forbidden_field_in_proposed_dto(self) -> None:
        names = tuple(
            f.name for f in fields(_ProposedRagExecutionTrace)
        )

        for forbidden in _FORBIDDEN_FIELDS:
            assert forbidden not in names, forbidden

    def test_no_forbidden_key_in_serialized_trace(self) -> None:
        payload = json.dumps(
            {
                "request_id": "A",
                "started_at": _STARTED.isoformat(),
                "chunk_ids": [11, 13],
            }
        )

        for forbidden in _FORBIDDEN_FIELDS:
            assert f'"{forbidden}"' not in payload, forbidden

    def test_chunk_and_document_exposure_matches_existing_api(self) -> None:
        """chunk_id / document_id 的暴露级别与既有 /api/ai/chat RAG 响应一致
        （同一请求所有者可见）；若 Trace 受众变化须重新评估。"""
        from backend.app.api.orchestrator_chat import ChatSourceResponse

        assert {"chunk_id", "document_id"} <= set(
            ChatSourceResponse.model_fields
        )
        assert "content" not in ChatSourceResponse.model_fields

    def test_no_new_correlation_id_in_backend(self) -> None:
        evidence: list[str] = []
        for path in (_REPO_ROOT / "backend" / "app").rglob("*.py"):
            relative = path.relative_to(_REPO_ROOT).as_posix()
            lowered = path.read_text(encoding="utf-8").lower()
            for token in (
                "rag_request_id", "retrieval_request_id", "trace_request_id",
            ):
                if token in lowered:
                    evidence.append(f"{relative}:{token}")
        assert evidence == [], evidence


# ============================================================
# 5. Correlation（同一 assistant request_id）
# ============================================================

class TestCorrelation:
    def test_rag_request_id_equals_assistant_trace_id(self) -> None:
        row = _row(request_id="A")
        view = _ProposedRagExecutionTrace.from_row(row)

        assert view.request_id == row.request_id == "A"

    def test_three_sources_share_one_key_naming(self) -> None:
        """LLM=assistant_request_id / Tool=request_id / RAG=request_id
        —— 三者值相同（Step 45 §五 结论），不新增 correlation id。"""
        assert "assistant_request_id" in LLMUsageTraceResponse.model_fields
        assert "request_id" in ToolExecutionTraceResponse.model_fields
        assert _RAG_TRACE_FIELDS[0] == "request_id"


# ============================================================
# 6. Ordering（方案 A：三个独立列表）
# ============================================================

class TestOrderingDesign:
    def test_proposed_shape_is_three_independent_lists(self) -> None:
        proposed = (*_CURRENT_RESPONSE_FIELDS, "rag_executions")

        assert proposed == (
            "assistant_request_id", "llm_usage", "tool_executions",
            "rag_executions",
        )
        # 不引入统一 events（方案 B 未采用）
        assert "events" not in proposed

    def test_current_lists_keep_their_own_order(self) -> None:
        source = _source(_TRACE_QUERY_MODULE)

        assert "本层不重排" in source or "不重排" in source


# ============================================================
# 7. Runtime vs Persistent / Backward Compatibility
# ============================================================

class TestRuntimeVsPersistentAndCompatibility:
    def test_trace_uses_persistent_tool_boundary(self) -> None:
        trace_api.get_assistant_trace_query_service  # 装配 accessor 存在
        from backend.app.api.orchestrator_chat import (
            get_assistant_trace_query_service,
        )

        service = get_assistant_trace_query_service()

        assert type(
            service.tool_observability_query_service
        ).__name__ == "ToolExecutionPersistentQueryService"

    def test_trace_sources_must_not_use_runtime_collector(self) -> None:
        """Trace 组合层只依赖 **Query Service**（持久化读边界）；
        不得引用任何 Runtime Collector（Collector 由 Composition Root 创建）。"""
        for relative in (_TRACE_QUERY_MODULE, _TRACE_API_MODULE):
            tree = ast.parse(_source(relative))
            imported = {
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
                "in_memory_rag_execution_collector",
                "rag_observability_query_service",
            ):
                assert forbidden not in imported, (relative, forbidden)

    def test_proposed_change_is_additive(self) -> None:
        """Step 48/64 实现：新增字段是 additive（旧字段不改名 / 不删除 / 语义不变）。"""
        current = list(_CURRENT_RESPONSE_FIELDS)
        implemented = list(AssistantTraceResponse.model_fields)

        assert set(current) <= set(implemented)       # 旧字段全部保留
        assert "rag_executions" in implemented        # Step 48（additive）
        assert _RESPONSE_OUTCOME_FIELD in implemented  # Step 64（additive）
        assert implemented[0] == current[0]           # 首字段未变
        # 旧客户端解析（忽略未知字段）仍可用
        payload = json.dumps(
            {"assistant_request_id": "A", "llm_usage": [], "tool_executions": [],
             "rag_executions": []}
        )
        parsed = json.loads(payload)
        assert {"assistant_request_id", "llm_usage", "tool_executions"} <= set(
            parsed
        )


# ============================================================
# 8. Project Authorization 现状（记录，不新增）
# ============================================================

class TestProjectAuthorizationLimitation:
    def test_rag_record_has_no_project_id(self) -> None:
        assert "project_id" not in [
            f.name for f in fields(RagExecutionRecordRow)
        ]

    def test_trace_endpoint_has_no_project_parameter(self) -> None:
        """端点入参只有 assistant_request_id（路径参数）；无 project 维度。"""
        parameters = app.openapi()["paths"][
            "/api/observability/assistant-trace/{assistant_request_id}"
        ]["get"]["parameters"]

        assert [parameter["name"] for parameter in parameters] == [
            "assistant_request_id",
        ]
        assert trace_api.MIN_ASSISTANT_REQUEST_ID_LENGTH == 1
        assert trace_api.MAX_ASSISTANT_REQUEST_ID_LENGTH == 128

    def test_trace_query_service_keyed_by_request_id_only(self) -> None:
        import inspect

        assert list(
            inspect.signature(AssistantTraceQueryService.get_trace).parameters
        ) == ["self", "assistant_request_id"]


__all__ = [
    "TestCurrentTraceContract",
    "TestRagReadBoundaryCapabilities",
    "TestProposedTraceDtoMapping",
    "TestTraceSecurity",
    "TestCorrelation",
    "TestOrderingDesign",
    "TestRuntimeVsPersistentAndCompatibility",
    "TestProjectAuthorizationLimitation",
]
