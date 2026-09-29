"""RAG Persistent Observation Contract 测试（Phase 3.12 Step 45）。

本文件**只锁定契约**，不实现持久化：

    0 DB（无表 / 无 Repository / 无 SQLAlchemy Session）
    0 生产运行时代码改动（只读既有实现 + 断言契约一致性）
    0 real LLM / 0 网络

契约（Step 46 实现时必须满足）：

    A. 字段：RagExecutionObservation 的 **13 字段 1:1** 进入持久化 Read Model
       （列名不变；主键由数据库生成，读侧沿用既有 ``id`` 命名）
    B. 禁止字段：query / content / answer / similarity / embedding / prompt /
       messages / raw_response / sql / password / api_key / authorization /
       database_url 永不进入持久化
    C. 关联：``request_id`` == Assistant Trace ID（唯一来源 =
       ``current_assistant_request_id()``；不新增第二套 correlation id）
    D. 序列化：chunk_ids / document_ids 去重 + **首次出现顺序**，JSON 数组形态
    E. 错误语义：empty result ≠ persistence failure
       （空 → 空列表；DB 失败 → typed repository error，**绝不**返回空）
    F. 索引：第一优先级 ``request_id``；其次 ``started_at``（不做无关索引）
    G. Deferred：无表 / 无 migration / 无 Repository / 无 Assistant Trace
       集成 / 无 HTTP API / 无 retention 实现
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.db.llm_usage_repository import (
    LLMUsageRecordRow,
    LLMUsageRepositoryError,
)
from backend.app.db.tool_execution_repository import (
    ToolExecutionRecordRow,
    ToolExecutionRepositoryError,
)
from backend.app.main import app
from backend.app.services.assistant_trace import (
    ASSISTANT_REQUEST_ID_MAX_LENGTH,
    assistant_trace_scope,
)
from backend.app.services.context_builder import ContextBuildResult
from backend.app.services.rag_execution_observation import (
    RagExecutionObservation,
)
from backend.app.services.rag_service import RagService
from backend.app.services.tool_execution_persistent_query_service import (
    ToolExecutionPersistentQueryService,
)
from backend.app.services.vector_search_service import VectorSearchResult

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OBSERVATION_MODULE = "backend/app/services/rag_execution_observation.py"
_RAG_SERVICE_MODULE = "backend/app/services/rag_service.py"

#: 持久化契约字段（顺序 = RagExecutionObservation 字段顺序 = 列顺序）
_CONTRACT_FIELDS: tuple[str, ...] = (
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

#: 永不进入持久化的字段名（Step 42/43 安全白名单之外的一切）
_FORBIDDEN_FIELDS: tuple[str, ...] = (
    "query", "content", "answer", "similarity", "embedding", "prompt",
    "messages", "raw_response", "sql", "password", "api_key",
    "authorization", "database_url", "traceback", "session", "connection",
    "project_id", "tool_call_id",
)

_SENTINEL_QUERY = "SENTINEL-USER-QUERY-STEP45"
_SENTINEL_CHUNK = "SENTINEL-CHUNK-CONTENT-STEP45"


@dataclass(frozen=True)
class _PersistentBoundarySpec:
    """Step 46 的目标边界（**设计契约**；本阶段不实现）。"""

    record_dto: str = "RagExecutionPersistentRecord"
    query_service: str = "RagExecutionPersistentQueryService"
    read_method: str = "list_by_request_id"
    #: Repository 侧方法名沿用既有只读方法（Tool 同构）
    repository_method: str = "get_by_request_id"
    write_port: str = "RagExecutionObserver"          # 既有接口，不新建
    write_binding: str = "CompositeRagExecutionObserver"  # 与 Tool 同构（未来）
    repository_error: str = "RagExecutionRepositoryError"
    primary_key: str = "id"                           # DB 自增；读侧沿用既有命名
    indexes: tuple[str, ...] = ("request_id", "started_at")
    unique_constraints: tuple[str, ...] = ()          # 第一版不做幂等约束
    retention: str = "DEFERRED"                       # Runtime capacity ≠ retention
    deferred: tuple[str, ...] = (
        "database_table", "migration", "repository", "persistence_adapter",
        "assistant_trace_integration", "http_api", "retention_policy",
    )


_SPEC = _PersistentBoundarySpec()


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


class _FakeVectorSearch:
    def __init__(self, results: list[VectorSearchResult]) -> None:
        self._results = results

    async def search(self, query: str, *, top_k: int) -> list[VectorSearchResult]:
        return list(self._results)


class _FakeLLM:
    async def chat(self, messages: list[dict[str, str]]) -> str:  # noqa: ARG002
        return "contract-answer"


class _EchoContextBuilder:
    def build(self, results):  # noqa: ANN001, ANN201
        used = tuple(results)
        return ContextBuildResult(
            text="\n".join(r.content for r in used),
            used_chunks=used,
            total_chars=42,
            truncated=False,
            dropped_count=0,
        )


def _chunk(chunk_id: int, *, document_id: int = 10) -> VectorSearchResult:
    return VectorSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=0,
        content=_SENTINEL_CHUNK,
        distance=0.1,
        similarity=0.93,
        metadata={"heading": "契约测试"},
    )


def _observation(
    *, chunk_ids: tuple[int, ...] = (1,), document_ids: tuple[int, ...] = (10,)
) -> RagExecutionObservation:
    now = datetime.now(timezone.utc)
    return RagExecutionObservation(
        request_id="req-contract-1",
        started_at=now,
        finished_at=now,
        duration_ms=1.5,
        result_count=len(chunk_ids),
        used_chunks_count=len(chunk_ids),
        top_k=5,
        context_truncated=False,
        context_chars=42,
        reranker_used=False,
        rerank_elapsed_ms=None,
        chunk_ids=chunk_ids,
        document_ids=document_ids,
    )


# ============================================================
# A. Observation → Persistent Record（13 字段 1:1）
# ============================================================

class TestFieldContract:
    def test_observation_has_exactly_contract_fields_in_order(self) -> None:
        assert tuple(f.name for f in fields(RagExecutionObservation)) == (
            _CONTRACT_FIELDS
        )

    def test_to_dict_keys_match_contract(self) -> None:
        assert tuple(_observation().to_dict()) == _CONTRACT_FIELDS

    def test_column_set_is_fields_plus_database_id(self) -> None:
        """持久化列 = 13 契约字段 + 数据库自增 ``id``（**不含** record_id）。"""
        columns = (*_CONTRACT_FIELDS, _SPEC.primary_key)

        assert len(columns) == 14
        assert len(set(columns)) == 14

    def test_primary_key_follows_existing_autoincrement_pattern(self) -> None:
        """既有 Row DTO 以数据库 ``id`` 作为首字段（沿用，不新造 record_id）。"""
        assert [f.name for f in fields(ToolExecutionRecordRow)][:1] == ["id"]
        assert [f.name for f in fields(LLMUsageRecordRow)][:1] == ["id"]
        assert _SPEC.primary_key == "id"
        assert "record_id" not in _CONTRACT_FIELDS
        assert not hasattr(_observation(), "record_id")

    def test_observation_has_no_id_field(self) -> None:
        """观测（Runtime）不持有主键 —— 主键只在数据库生成。"""
        assert "id" not in _CONTRACT_FIELDS
        assert not hasattr(_observation(), "id")


# ============================================================
# B. Forbidden fields（永不持久化）
# ============================================================

class TestForbiddenFields:
    def test_no_forbidden_field_in_observation(self) -> None:
        names = set(_CONTRACT_FIELDS)

        for forbidden in _FORBIDDEN_FIELDS:
            assert forbidden not in names, forbidden

    def test_no_forbidden_key_in_serialization(self) -> None:
        payload = _observation().to_dict()

        for forbidden in _FORBIDDEN_FIELDS:
            assert forbidden not in payload, forbidden

    def test_observation_module_declares_forbidden_boundary(self) -> None:
        """模块文档必须显式声明禁止字段（安全边界可审计）。"""
        source = _source(_OBSERVATION_MODULE)

        for forbidden in ("query", "content", "embedding", "prompt", "SQL"):
            assert forbidden in source, forbidden
        for marker in ("禁止", "Step 42"):
            assert marker in source, marker

    def test_no_sensitive_value_reaches_observation(self) -> None:
        """真实 pipeline（Fake 边界）：query / chunk 正文都不进入观测。"""
        import asyncio

        service = RagService(
            vector_search_service=_FakeVectorSearch(  # type: ignore[arg-type]
                [_chunk(5, document_id=50)]
            ),
            llm_client=_FakeLLM(),  # type: ignore[arg-type]
            context_builder=_EchoContextBuilder(),  # type: ignore[arg-type]
        )
        captured: list[RagExecutionObservation] = []

        class _Recorder:
            def record(self, observation: RagExecutionObservation) -> None:
                captured.append(observation)

        service._observer = _Recorder()  # noqa: SLF001 —— 契约测试专用注入

        with assistant_trace_scope("req-contract-leak"):
            asyncio.run(service.answer(_SENTINEL_QUERY))

        assert len(captured) == 1
        serialized = str(captured[0].to_dict())
        assert _SENTINEL_QUERY not in serialized
        assert _SENTINEL_CHUNK not in serialized
        assert "similarity" not in captured[0].to_dict()


# ============================================================
# C. request_id == Assistant Trace ID（唯一来源）
# ============================================================

class TestRequestIdCorrelation:
    def test_request_id_equals_assistant_trace_id(self) -> None:
        import asyncio

        service = RagService(
            vector_search_service=_FakeVectorSearch(  # type: ignore[arg-type]
                [_chunk(7)]
            ),
            llm_client=_FakeLLM(),  # type: ignore[arg-type]
            context_builder=_EchoContextBuilder(),  # type: ignore[arg-type]
        )
        captured: list[RagExecutionObservation] = []

        class _Recorder:
            def record(self, observation: RagExecutionObservation) -> None:
                captured.append(observation)

        service._observer = _Recorder()  # noqa: SLF001

        with assistant_trace_scope("req-A"):
            asyncio.run(service.answer("q"))
        with assistant_trace_scope("req-B"):
            asyncio.run(service.answer("q"))

        assert [o.request_id for o in captured] == ["req-A", "req-B"]
        assert captured[0].request_id != captured[1].request_id

    def test_request_id_is_the_only_source_of_correlation(self) -> None:
        """rag_service 只从 contextvar 取 id；不生成 / 不推断第二套 id。"""
        tree = ast.parse(_source(_RAG_SERVICE_MODULE))
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        assert "current_assistant_request_id" in calls
        for forbidden in ("uuid4", "uuid1", "new_request_id", "token_hex"):
            assert forbidden not in calls, forbidden
        assert "uuid" not in _source(_RAG_SERVICE_MODULE).lower().split(
            "不生成"
        )[0]

    def test_observation_module_generates_no_id(self) -> None:
        source = _source(_OBSERVATION_MODULE)

        for forbidden in ("uuid", "time.time", "os.urandom", "token_hex"):
            assert forbidden not in source, forbidden

    def test_max_length_matches_assistant_request_id_limit(self) -> None:
        assert ASSISTANT_REQUEST_ID_MAX_LENGTH == 128
        with pytest.raises(ValueError):
            _observation().__class__(
                **{
                    **_observation().__dict__,
                    "request_id": "A" * 129,
                }
            )
        with pytest.raises(ValueError):
            _observation().__class__(
                **{**_observation().__dict__, "request_id": "   "}
            )

    def test_field_name_matches_tool_convention(self) -> None:
        """RAG 无 provider id → 沿用 Tool 的 ``request_id`` 命名。"""
        assert "request_id" in [
            f.name for f in fields(ToolExecutionRecordRow)
        ]
        # LLM 侧因 provider id 占用 request_id，才使用 assistant_request_id
        assert "request_id" in [f.name for f in fields(LLMUsageRecordRow)]
        assert _CONTRACT_FIELDS[0] == "request_id"


# ============================================================
# D. Serialization 确定性
# ============================================================

class TestSerializationDeterminism:
    def test_ids_deduplicated_with_first_occurrence_order(self) -> None:
        observation = _observation(
            chunk_ids=(5, 5, 7, 5), document_ids=(10, 10, 30)
        )

        assert observation.chunk_ids == (5, 7)
        assert observation.document_ids == (10, 30)
        # 内存形态 = tuple（不可变）；线形态 = list（JSON 数组）
        assert isinstance(observation.chunk_ids, tuple)
        payload = observation.to_dict()
        assert payload["chunk_ids"] == [5, 7]
        assert payload["document_ids"] == [10, 30]

    def test_repeated_serialization_is_stable(self) -> None:
        observation = _observation(chunk_ids=(9, 8, 9))

        first = observation.to_dict()
        second = observation.to_dict()

        assert first == second
        assert first["chunk_ids"] == [9, 8]

    def test_json_round_trip_preserves_order(self) -> None:
        payload = _observation(
            chunk_ids=(3, 1, 3, 2), document_ids=(7, 7, 5)
        ).to_dict()

        restored = json.loads(json.dumps(payload))

        assert restored["chunk_ids"] == [3, 1, 2]
        assert restored["document_ids"] == [7, 5]
        assert restored["request_id"] == "req-contract-1"

    def test_ids_are_non_negative_ints(self) -> None:
        with pytest.raises((TypeError, ValueError)):
            _observation(chunk_ids=("x",))  # type: ignore[arg-type]
        with pytest.raises((TypeError, ValueError)):
            _observation(document_ids=(-1,))  # type: ignore[arg-type]


# ============================================================
# E. Error semantics：empty ≠ failure
# ============================================================

class _FailingRepository:
    """既有读模式：Repository 抛 typed error（服务层必须原样透传）。"""

    def get_by_request_id(self, request_id):  # noqa: ANN001, ANN201
        raise ToolExecutionRepositoryError("db down")


class _EmptyRepository:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_by_request_id(self, request_id):  # noqa: ANN001, ANN201
        self.calls.append(request_id)
        return []


class _SpyRepository:
    """非法输入时**不得**被访问（校验先于查询）。"""

    def __init__(self) -> None:
        self.calls = 0

    def get_by_request_id(self, request_id):  # noqa: ANN001, ANN201
        self.calls += 1
        return []


class TestErrorSemantics:
    def test_existing_pattern_empty_result_is_empty_list(self) -> None:
        repository = _EmptyRepository()
        service = ToolExecutionPersistentQueryService(
            repository=repository  # type: ignore[arg-type]
        )

        assert service.list_by_request_id("req-A") == []
        assert repository.calls == ["req-A"]

    def test_existing_pattern_db_failure_is_typed_error_not_empty(self) -> None:
        service = ToolExecutionPersistentQueryService(
            repository=_FailingRepository()  # type: ignore[arg-type]
        )

        with pytest.raises(ToolExecutionRepositoryError):
            service.list_by_request_id("req-A")

    def test_existing_pattern_invalid_input_not_touching_repository(self) -> None:
        repository = _SpyRepository()
        service = ToolExecutionPersistentQueryService(
            repository=repository  # type: ignore[arg-type]
        )

        with pytest.raises((TypeError, ValueError)):
            service.list_by_request_id("   ")

        assert repository.calls == 0

    def test_rag_boundary_must_mirror_the_same_semantics(self) -> None:
        """设计契约：未来 RAG 读边界 = 同一语义（空 ≠ 失败）。"""
        assert _SPEC.read_method == "list_by_request_id"          # 服务层
        assert _SPEC.repository_method == "get_by_request_id"     # 仓储层
        assert _SPEC.repository_error.endswith("RepositoryError")
        for error in (ToolExecutionRepositoryError, LLMUsageRepositoryError):
            assert error.__name__ == "ToolExecutionRepositoryError" or (
                error.__name__ == "LLMUsageRepositoryError"
            )
            assert issubclass(error, Exception)
        # 既有错误族命名与本设计一致（<Domain>RepositoryError）
        assert _SPEC.repository_error == "RagExecutionRepositoryError"


# ============================================================
# F. 索引设计（只设计，不 migration）
# ============================================================

class TestIndexDesign:
    def test_index_priority_is_request_id_then_started_at(self) -> None:
        assert _SPEC.indexes == ("request_id", "started_at")
        assert _SPEC.indexes[0] == "request_id"        # Assistant Trace 第一优先级

    def test_no_speculative_indexes(self) -> None:
        for forbidden in (
            "query", "document_id", "chunk_id", "project_id", "tool_name",
        ):
            assert forbidden not in _SPEC.indexes, forbidden

    def test_no_unique_constraint_in_first_version(self) -> None:
        assert _SPEC.unique_constraints == ()
        # 参照：LLM usage 因 provider 幂等才使用 partial unique index
        source = _source("backend/app/db/models/llm_usage_record.py")
        assert "uq_llm_usage_record_request_id" in source

    def test_index_naming_follows_existing_convention(self) -> None:
        """既有命名：``ix_<table>_<column>``；RAG 未来沿用同一风格。"""
        source = _source("backend/app/db/models/llm_usage_record.py")

        assert 'ix_llm_usage_record_created_at' in source
        assert _SPEC.indexes[1] == "started_at"


# ============================================================
# G. Deferred / 无实现（本阶段边界）
# ============================================================

class TestDeferredBoundaries:
    """Step 46 落地后的**实现态一致性**（契约本身不变）。"""

    def test_only_the_contract_table_is_registered(self) -> None:
        from backend.app.db.base import Base

        assert sorted(
            name for name in Base.metadata.tables if "rag" in name.lower()
        ) == ["ai_ops.rag_execution_record"]

    def test_implemented_modules_match_contract_names(self) -> None:
        expected_modules = (
            "backend/app/db/models/rag_execution_record.py",
            "backend/app/db/rag_execution_repository.py",
            "backend/app/services/rag_execution_persistence_service.py",
            "backend/app/services/rag_execution_persistence_adapter.py",
            "backend/app/services/composite_rag_execution_observer.py",
            "backend/app/services/rag_execution_persistent_query_service.py",
        )
        for relative in expected_modules:
            assert (_REPO_ROOT / relative).exists(), relative
        # 不引入第二套 speculative 持久化（无 metrics / no-trace 变体）
        offenders: list[str] = []
        for path in (_REPO_ROOT / "backend" / "app").rglob("*.py"):
            relative = path.relative_to(_REPO_ROOT).as_posix()
            lowered = path.read_text(encoding="utf-8").lower()
            for token in ("rag_trace", "retrieval_record", "rag_metrics"):
                if token in lowered:
                    offenders.append(f"{relative}:{token}")
        assert offenders == [], offenders

    def test_still_deferred_items_are_recorded(self) -> None:
        assert set(_SPEC.deferred) == {
            "database_table", "migration", "repository", "persistence_adapter",
            "assistant_trace_integration", "http_api", "retention_policy",
        }
        # Step 46 实现表 / Repository / Adapter；其余仍 deferred：
        assert _SPEC.retention == "DEFERRED"          # 无 TTL / 无 cleanup
        paths = sorted(app.openapi()["paths"])
        assert not [
            p for p in paths if "rag" in p.lower() and "answer" not in p
        ], paths                                          # 无 RAG Query/Metrics API
        trace = app.openapi()["components"]["schemas"][
            "AssistantTraceResponse"
        ]["properties"]
        assert "rag" not in str(list(trace)).lower()       # 未接 Assistant Trace

    def test_write_port_is_the_existing_observer_interface(self) -> None:
        """写侧复用既有 ``record()`` 端口；不新造生命周期。"""
        from backend.app.services.rag_execution_observation import (
            RagExecutionObserver,
        )

        assert _SPEC.write_port == "RagExecutionObserver"
        assert hasattr(RagExecutionObserver, "record")
        # 参照：Tool 侧同构（observer 端口 + 未来 composite fan-out）
        collector = _source(
            "backend/app/services/in_memory_rag_execution_collector.py"
        )
        assert "def record(" in collector

    def test_assistant_trace_and_http_api_unchanged(self) -> None:
        schema = app.openapi()["components"]["schemas"]["AssistantTraceResponse"]

        assert list(schema["properties"]) == [
            "assistant_request_id", "llm_usage", "tool_executions",
        ]
        paths = sorted(app.openapi()["paths"])
        assert [p for p in paths if "rag" in p.lower()] == ["/api/rag/answer"]

    def test_record_dto_follows_project_naming_convention(self) -> None:
        """Step 46 落地：持久化 Read DTO = ``RagExecutionRecordRow``
        （沿用 ``ToolExecutionRecordRow`` / ``LLMUsageRecordRow`` 命名），
        字段 = 数据库 ``id`` + 13 契约字段（**无** record_id / 无敏感字段）。"""
        from backend.app.db.rag_execution_repository import (
            RAG_EXECUTION_READ_COLUMNS,
            RagExecutionRecordRow,
        )

        assert _SPEC.record_dto in (
            "RagExecutionPersistentRecord",
            "RagExecutionRecordRow",
        )
        assert [f.name for f in fields(RagExecutionRecordRow)] == [
            "id", *_CONTRACT_FIELDS,
        ]
        assert RAG_EXECUTION_READ_COLUMNS == ("id", *_CONTRACT_FIELDS)


__all__ = [
    "TestFieldContract",
    "TestForbiddenFields",
    "TestRequestIdCorrelation",
    "TestSerializationDeterminism",
    "TestErrorSemantics",
    "TestIndexDesign",
    "TestDeferredBoundaries",
]
