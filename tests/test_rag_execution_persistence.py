"""RAG Execution Persistence 单元测试（Phase 3.12 Step 46）。

**纯离线**（0 DB / 0 real LLM / 0 网络）：只验证

    Observation → ORM 映射 / Repository SQL 形态 / Service 委派 /
    Adapter 失败隔离 / Composite fan-out / Query Service 语义 / 安全边界

DB 集成（真实 PostgreSQL）在 ``tests/test_rag_execution_persistence_db.py``
（RUN_DB_TESTS=1）与 ``tests/test_rag_runtime_observability_e2e_db.py``。
"""
from __future__ import annotations

import json
import logging
from dataclasses import fields
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.models.rag_execution_record import (
    RAG_EXECUTION_PERSISTED_FIELDS,
    RAG_EXECUTION_SCHEMA,
    RAG_EXECUTION_TABLE,
    RagExecutionRecordModel,
)
from backend.app.db.rag_execution_repository import (
    RAG_EXECUTION_READ_COLUMNS,
    RagExecutionRecordRow,
    RagExecutionRepository,
    RagExecutionRepositoryError,
)
from backend.app.services.composite_rag_execution_observer import (
    CompositeRagExecutionObserver,
)
from backend.app.services.rag_execution_observation import (
    RagExecutionObservation,
)
from backend.app.services.rag_execution_persistence_adapter import (
    RagExecutionPersistenceAdapter,
)
from backend.app.services.rag_execution_persistence_service import (
    RagExecutionPersistenceService,
)
from backend.app.services.rag_execution_persistent_query_service import (
    RagExecutionPersistentQueryService,
)

_CONTRACT_FIELDS: tuple[str, ...] = (
    "request_id", "started_at", "finished_at", "duration_ms", "result_count",
    "used_chunks_count", "top_k", "context_truncated", "context_chars",
    "reranker_used", "rerank_elapsed_ms", "chunk_ids", "document_ids",
)

_FORBIDDEN_COLUMNS: tuple[str, ...] = (
    "query", "answer", "content", "similarity", "embedding", "prompt",
    "messages", "raw_response", "sql", "password", "api_key",
    "authorization", "database_url", "project_id", "tool_call_id",
)

_STARTED = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
_SENTINEL = "SENTINEL-SECRET-STEP46"


def _observation(**overrides) -> RagExecutionObservation:  # noqa: ANN003
    values: dict = {
        "request_id": "req-row-1",
        "started_at": _STARTED,
        "finished_at": _STARTED + timedelta(milliseconds=8.5),
        "duration_ms": 8.5,
        "result_count": 3,
        "used_chunks_count": 2,
        "top_k": 5,
        "context_truncated": False,
        "context_chars": 120,
        "reranker_used": False,
        "rerank_elapsed_ms": None,
        "chunk_ids": (11, 13),
        "document_ids": (7,),
    }
    values.update(overrides)
    return RagExecutionObservation(**values)


def _row(**overrides) -> RagExecutionRecordRow:  # noqa: ANN003
    values: dict = {
        "id": 1,
        "request_id": "req-row-1",
        "started_at": _STARTED,
        "finished_at": _STARTED + timedelta(milliseconds=8.5),
        "duration_ms": 8.5,
        "result_count": 3,
        "used_chunks_count": 2,
        "top_k": 5,
        "context_truncated": False,
        "context_chars": 120,
        "reranker_used": False,
        "rerank_elapsed_ms": None,
        "chunk_ids": (11, 13),
        "document_ids": (7,),
    }
    values.update(overrides)
    return RagExecutionRecordRow(**values)


class _BoomFactory:
    """Session 工厂替身：进入事务即抛 SQLAlchemyError（模拟 DB 故障）。"""

    def __call__(self):  # noqa: ANN204
        raise SQLAlchemyError("db down")


class _FakeRepository:
    """记录调用（服务 / Adapter / QueryService 的委派断言）。"""

    def __init__(self, *, rows: list[RagExecutionRecordRow] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.created: list[RagExecutionObservation] = []
        self.request_ids: list[str] = []

    def create(self, observation: RagExecutionObservation) -> RagExecutionRecordRow:
        self.created.append(observation)
        return _row(request_id=observation.request_id)

    def get_by_request_id(self, request_id: str) -> list[RagExecutionRecordRow]:
        self.request_ids.append(request_id)
        return list(self.rows)


class _FailingRepository:
    def get_by_request_id(self, request_id: str) -> list[RagExecutionRecordRow]:
        raise RagExecutionRepositoryError("query failed")

    def create(self, observation: RagExecutionObservation) -> RagExecutionRecordRow:
        raise RagExecutionRepositoryError("insert failed")


# ============================================================
# 1. ORM Model（列 / 类型 / 可空 / 索引 / schema）
# ============================================================

class TestOrmModel:
    def test_columns_are_exactly_contract_fields_plus_id(self) -> None:
        columns = [
            column.name for column in RagExecutionRecordModel.__table__.columns
        ]

        assert columns == ["id", *_CONTRACT_FIELDS]
        assert RAG_EXECUTION_READ_COLUMNS == ("id", *_CONTRACT_FIELDS)
        assert RAG_EXECUTION_PERSISTED_FIELDS == _CONTRACT_FIELDS

    def test_no_forbidden_columns(self) -> None:
        columns = {
            column.name for column in RagExecutionRecordModel.__table__.columns
        }

        for forbidden in _FORBIDDEN_COLUMNS:
            assert forbidden not in columns, forbidden

    def test_primary_key_is_autoincrement_id(self) -> None:
        column = RagExecutionRecordModel.__table__.columns["id"]

        assert column.primary_key is True
        assert column.autoincrement is True
        assert column.name == "id"
        for forbidden in ("record_id", "observation_id", "uuid"):
            assert forbidden not in RagExecutionRecordModel.__table__.columns

    def test_nullability_contract(self) -> None:
        columns = RagExecutionRecordModel.__table__.columns

        assert columns["rerank_elapsed_ms"].nullable is True   # NULL ≠ 0
        for name in _CONTRACT_FIELDS:
            if name != "rerank_elapsed_ms":
                assert columns[name].nullable is False, name

    def test_table_lives_in_ai_ops_schema(self) -> None:
        assert RAG_EXECUTION_SCHEMA == "ai_ops"
        assert RAG_EXECUTION_TABLE == "rag_execution_record"
        assert RagExecutionRecordModel.__table__.schema == "ai_ops"

    def test_indexes_are_minimal_and_named_per_convention(self) -> None:
        indexes = sorted(
            index.name for index in RagExecutionRecordModel.__table__.indexes
        )

        assert indexes == [
            "ix_rag_execution_record_request_id",
            "ix_rag_execution_record_started_at",
        ]

    def test_json_columns_are_postgresql_jsonb(self) -> None:
        columns = RagExecutionRecordModel.__table__.columns

        for name in ("chunk_ids", "document_ids"):
            assert type(columns[name].type).__name__ == "JSONB", name


# ============================================================
# 2. Repository（映射 / SQL 形态 / 失败语义）
# ============================================================

class TestRepository:
    def test_values_mapping_is_explicit_and_complete(self) -> None:
        values = RagExecutionRepository._values_from(  # noqa: SLF001
            _observation()
        )

        assert tuple(values) == _CONTRACT_FIELDS
        assert values["rerank_elapsed_ms"] is None            # None 不变成 0
        assert values["chunk_ids"] == [11, 13]                # tuple → JSON list
        assert values["document_ids"] == [7]

    def test_insert_sql_is_bound_and_whitelisted(self) -> None:
        statement = RagExecutionRepository().build_insert(_observation())
        sql = str(statement)                     # bind 参数形态（JSONB 不 literal_bind）
        params = statement.compile().params

        assert "INSERT INTO ai_ops.rag_execution_record" in sql
        for name in _CONTRACT_FIELDS:
            assert name in sql, name
        for forbidden in _FORBIDDEN_COLUMNS:
            assert forbidden not in sql, forbidden
        assert params["request_id"] == "req-row-1"
        assert params["rerank_elapsed_ms"] is None
        assert params["chunk_ids"] == [11, 13]
        assert params["document_ids"] == [7]

    def test_request_select_is_explicit_exact_and_ordered(self) -> None:
        statement = RagExecutionRepository().build_request_select("req-A")
        sql = str(statement)
        params = statement.compile().params

        assert "SELECT *" not in sql.upper()
        assert sql.count("FROM") == 1
        assert "request_id" in sql and "ORDER BY" in sql.upper()
        assert "id ASC" in sql
        assert params["request_id_1"] == "req-A"
        for forbidden in _FORBIDDEN_COLUMNS:
            assert forbidden not in sql, forbidden

    def test_injection_input_stays_bound(self) -> None:
        statement = RagExecutionRepository().build_request_select(
            "' OR 1=1 --"
        )
        sql = str(statement)
        params = statement.compile().params

        assert params["request_id_1"] == "' OR 1=1 --"
        assert "OR 1=1" not in sql
        assert ";" not in sql

    def test_db_failure_is_typed_error_not_empty(self) -> None:
        repository = RagExecutionRepository(session_factory=_BoomFactory())  # type: ignore[arg-type]

        with pytest.raises(RagExecutionRepositoryError):
            repository.get_by_request_id("req-A")
        with pytest.raises(RagExecutionRepositoryError):
            repository.create(_observation())

    def test_invalid_request_id_rejected_before_session(self) -> None:
        repository = RagExecutionRepository(session_factory=_BoomFactory())  # type: ignore[arg-type]

        for bad, expected in (
            (None, TypeError),
            (123, TypeError),
            ("", ValueError),
            ("   ", ValueError),
            ("A" * 129, ValueError),
        ):
            with pytest.raises(expected):
                repository.get_by_request_id(bad)  # type: ignore[arg-type]

    def test_create_rejects_non_observation(self) -> None:
        repository = RagExecutionRepository(session_factory=_BoomFactory())  # type: ignore[arg-type]

        with pytest.raises(TypeError):
            repository.create({"request_id": "x"})  # type: ignore[arg-type]

    def test_row_mapping_converts_json_lists_to_tuples(self) -> None:
        class _Raw:
            _mapping = {
                "id": 5,
                "request_id": "req-A",
                "started_at": _STARTED,
                "finished_at": _STARTED,
                "duration_ms": 1.0,
                "result_count": 2,
                "used_chunks_count": 2,
                "top_k": 5,
                "context_truncated": False,
                "context_chars": 10,
                "reranker_used": True,
                "rerank_elapsed_ms": 3.5,
                "chunk_ids": [13, 11],
                "document_ids": [],
            }

        row = RagExecutionRepository._to_row(_Raw())  # noqa: SLF001

        assert row.chunk_ids == (13, 11)                 # 顺序保持
        assert row.document_ids == ()
        assert row.rerank_elapsed_ms == 3.5


# ============================================================
# 3. Persistence Service / Adapter（委派 + 失败隔离）
# ============================================================

class TestServiceAndAdapter:
    def test_service_delegates_to_repository(self) -> None:
        repository = _FakeRepository()
        service = RagExecutionPersistenceService(
            repository=repository  # type: ignore[arg-type]
        )

        row = service.persist(_observation())

        assert isinstance(row, RagExecutionRecordRow)
        assert repository.created == [_observation()] or len(
            repository.created
        ) == 1

    def test_service_does_not_swallow_repository_error(self) -> None:
        service = RagExecutionPersistenceService(
            repository=_FailingRepository()  # type: ignore[arg-type]
        )

        with pytest.raises(RagExecutionRepositoryError):
            service.persist(_observation())

    def test_adapter_isolates_failure_and_logs_safely(self, caplog) -> None:
        adapter = RagExecutionPersistenceAdapter(
            persistence_service=RagExecutionPersistenceService(
                repository=_FailingRepository()  # type: ignore[arg-type]
            )
        )

        with caplog.at_level(logging.WARNING):
            result = adapter.record(_observation())

        assert result is None                             # 失败已隔离
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelno == logging.WARNING
        assert record.__dict__["error_type"] == "RagExecutionRepositoryError"
        assert record.__dict__["request_id"] == "req-row-1"
        # 日志不带 payload / traceback（只有白名单 context）
        assert set(record.__dict__) & {
            "query", "content", "sql", "payload",
        } == set()
        assert record.exc_info is None
        assert _SENTINEL not in record.getMessage()
        assert "Traceback" not in caplog.text

    def test_adapter_returns_row_on_success(self) -> None:
        repository = _FakeRepository()
        adapter = RagExecutionPersistenceAdapter(
            persistence_service=RagExecutionPersistenceService(
                repository=repository  # type: ignore[arg-type]
            )
        )

        row = adapter.record(_observation())

        assert isinstance(row, RagExecutionRecordRow)

    def test_adapter_isolates_unexpected_exception(self, caplog) -> None:
        class _BrokenService:
            def persist(self, observation):  # noqa: ANN001, ANN201
                raise RuntimeError("boom")

        adapter = RagExecutionPersistenceAdapter(
            persistence_service=_BrokenService()  # type: ignore[arg-type]
        )

        with caplog.at_level(logging.WARNING):
            assert adapter.record(_observation()) is None

        assert caplog.records[0].__dict__["error_type"] == "RuntimeError"

    def test_adapter_satisfies_observer_port(self) -> None:
        from backend.app.services.rag_execution_observation import (
            RagExecutionObserver,
        )

        adapter = RagExecutionPersistenceAdapter(
            persistence_service=RagExecutionPersistenceService(
                repository=_FakeRepository()  # type: ignore[arg-type]
            )
        )

        assert isinstance(adapter, RagExecutionObserver)
        assert adapter.persistence_service.repository is not None


# ============================================================
# 4. Composite Observer（fan-out + 互相隔离）
# ============================================================

class _RecordingObserver:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[RagExecutionObservation] = []
        self._error = error

    def record(self, observation: RagExecutionObservation) -> None:
        self.calls.append(observation)
        if self._error is not None:
            raise self._error


class TestCompositeObserver:
    def test_fan_out_calls_every_observer_in_order(self) -> None:
        first = _RecordingObserver()
        second = _RecordingObserver()
        composite = CompositeRagExecutionObserver(first, second)

        composite.record(_observation())

        assert len(first.calls) == 1 and len(second.calls) == 1
        assert composite.observers == (first, second)

    def test_first_failure_does_not_block_second(self, caplog) -> None:
        broken = _RecordingObserver(error=RuntimeError("memory boom"))
        healthy = _RecordingObserver()
        composite = CompositeRagExecutionObserver(broken, healthy)

        with caplog.at_level(logging.WARNING):
            composite.record(_observation())           # 不抛出

        assert len(broken.calls) == 1
        assert len(healthy.calls) == 1                 # 未被跳过
        assert caplog.records[0].__dict__["error_type"] == "RuntimeError"
        assert caplog.records[0].__dict__["observer_type"] == "_RecordingObserver"
        assert caplog.records[0].__dict__["request_id"] == "req-row-1"

    def test_observer_contract_validation(self) -> None:
        with pytest.raises(TypeError):
            CompositeRagExecutionObserver(object())  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            CompositeRagExecutionObserver(_RecordingObserver(), None)  # type: ignore[arg-type]

    def test_composite_satisfies_observer_port(self) -> None:
        from backend.app.services.rag_execution_observation import (
            RagExecutionObserver,
        )

        assert isinstance(
            CompositeRagExecutionObserver(_RecordingObserver()),
            RagExecutionObserver,
        )


# ============================================================
# 5. Persistent Query Service（只读边界语义）
# ============================================================

class TestPersistentQueryService:
    def test_list_by_request_id_delegates_and_returns_rows(self) -> None:
        repository = _FakeRepository(rows=[_row(request_id="req-A")])
        service = RagExecutionPersistentQueryService(repository=repository)  # type: ignore[arg-type]

        rows = service.list_by_request_id("req-A")

        assert [row.request_id for row in rows] == ["req-A"]
        assert repository.request_ids == ["req-A"]

    def test_empty_result_is_empty_list(self) -> None:
        service = RagExecutionPersistentQueryService(
            repository=_FakeRepository()  # type: ignore[arg-type]
        )

        assert service.list_by_request_id("req-unknown") == []

    def test_db_failure_is_propagated_not_empty(self) -> None:
        service = RagExecutionPersistentQueryService(
            repository=_FailingRepository()  # type: ignore[arg-type]
        )

        with pytest.raises(RagExecutionRepositoryError):
            service.list_by_request_id("req-A")

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
        repository = _FakeRepository()
        service = RagExecutionPersistentQueryService(repository=repository)  # type: ignore[arg-type]

        with pytest.raises(expected):
            service.list_by_request_id(bad)

        assert repository.request_ids == []            # Repository 未被访问

    def test_query_service_is_read_only(self) -> None:
        for forbidden in ("create", "delete", "update", "clear", "record"):
            assert forbidden not in dir(RagExecutionPersistentQueryService)

    def test_row_dto_is_immutable_and_whitelisted(self) -> None:
        row = _row()

        assert [f.name for f in fields(RagExecutionRecordRow)] == [
            "id", *_CONTRACT_FIELDS,
        ]
        for forbidden in _FORBIDDEN_COLUMNS:
            assert not hasattr(row, forbidden), forbidden
        with pytest.raises(Exception):
            row.request_id = "mutated"  # type: ignore[misc]


# ============================================================
# 6. Security（持久化路径不出现敏感列 / 值）
# ============================================================

class TestPersistenceSecurity:
    def test_no_sensitive_value_reaches_row_or_sql(self) -> None:
        observation = _observation(request_id="req-sec")
        values = RagExecutionRepository._values_from(observation)  # noqa: SLF001
        serialized = json.dumps(
            {key: str(value) for key, value in values.items()}
        )

        assert _SENTINEL not in serialized
        for forbidden in _FORBIDDEN_COLUMNS:
            assert forbidden not in values, forbidden

    def test_repository_and_adapter_have_no_payload_access(self) -> None:
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        for relative in (
            "backend/app/db/rag_execution_repository.py",
            "backend/app/services/rag_execution_persistence_service.py",
            "backend/app/services/rag_execution_persistence_adapter.py",
            "backend/app/services/composite_rag_execution_observer.py",
        ):
            tree = ast.parse((root / relative).read_text(encoding="utf-8"))
            used = {
                node.attr for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
            } | {
                node.id for node in ast.walk(tree)
                if isinstance(node, ast.Name)
            }
            for forbidden in ("query", "content", "prompt", "similarity"):
                assert forbidden not in used, (relative, forbidden)


__all__ = [
    "TestOrmModel",
    "TestRepository",
    "TestServiceAndAdapter",
    "TestCompositeObserver",
    "TestPersistentQueryService",
    "TestPersistenceSecurity",
]
