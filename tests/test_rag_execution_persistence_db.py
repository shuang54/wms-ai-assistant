"""RAG Execution Persistence —— DB 集成测试（Phase 3.12 Step 46）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）：

    Case 1  insert（Observation → 行存在）
    Case 2  read（get_by_request_id；13 字段 + NULL 语义 + 顺序）
    Case 3  同一 request_id 多条（A/A/B → A=2、B=1；id ASC）
    Case 4  empty（unknown request → []）
    Case 5  invalid request_id（校验先于查询；0 DB 访问）
    Case 6  failure semantics（DB 故障 → RagExecutionRepositoryError，**不是 []**）
    Case 7  Adapter 失败隔离（真实 DB 故障 → 返回 None + warning；不落行）
    Case 8  Persistent Query Service（真实 DB 读边界）
    Case 9  restart-like（新建 QueryService 仍可读到已落库的观测）

数据：**只使用 synthetic observation**（request_id 前缀 ``rag-step46-``），
不读取真实 WMS / 库存 / 工单数据，不调用真实 LLM。
残留：定向 DELETE 本模块自己的 request_id（**不使用 TRUNCATE**），
并在结束时断言 residue = 0。
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.models.rag_execution_record import (
    RAG_EXECUTION_SCHEMA,
    RAG_EXECUTION_TABLE,
)
from backend.app.db.rag_execution_repository import (
    RagExecutionRecordRow,
    RagExecutionRepository,
    RagExecutionRepositoryError,
)
from backend.app.db.session import get_engine
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

_TABLE = f"{RAG_EXECUTION_SCHEMA}.{RAG_EXECUTION_TABLE}"
_PREFIX = "rag-step46-"
_STARTED = datetime(2026, 9, 28, 15, 0, 0, tzinfo=timezone.utc)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _engine():  # noqa: ANN202
    engine = get_engine()
    if engine is None:
        pytest.skip("DATABASE_URL 未配置")
    return engine


def _ensure_schema() -> None:
    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()


def _request_id(suffix: str) -> str:
    return f"{_PREFIX}{suffix}-{uuid.uuid4().hex[:8]}"


def _observation(**overrides):  # noqa: ANN003, ANN201
    values: dict = {
        "request_id": _request_id("default"),
        "started_at": _STARTED,
        "finished_at": _STARTED + timedelta(milliseconds=12.5),
        "duration_ms": 12.5,
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


def _count_rows(request_id: str) -> int:
    with _engine().connect() as conn:
        return int(
            conn.execute(
                text(
                    f"SELECT COUNT(*) FROM {_TABLE} WHERE request_id = :rid"
                ),
                {"rid": request_id},
            ).scalar_one()
        )


def _delete_own_rows() -> int:
    """定向 DELETE（只删本模块前缀的测试行；不使用 TRUNCATE）。"""
    with _engine().begin() as conn:
        conn.execute(
            text(f"DELETE FROM {_TABLE} WHERE request_id LIKE :prefix"),
            {"prefix": f"{_PREFIX}%"},
        )
    return 0


@pytest.fixture(scope="module", autouse=True)
def _schema():
    if not _env_flag("RUN_DB_TESTS"):
        yield
        return
    _ensure_schema()
    yield


@pytest.fixture(autouse=True)
def _cleanup():
    if not _env_flag("RUN_DB_TESTS"):
        yield
        return
    _delete_own_rows()
    yield
    _delete_own_rows()


class _BoomFactory:
    """Session 工厂替身：进入事务即抛 SQLAlchemyError。"""

    def __call__(self):  # noqa: ANN204
        raise SQLAlchemyError("simulated db failure")


# ============================================================
# Case 1 / 2. insert + read
# ============================================================

@requires_db
class TestInsertAndRead:
    def test_insert_persists_row(self) -> None:
        observation = _observation()
        repository = RagExecutionRepository()

        row = repository.create(observation)

        assert isinstance(row, RagExecutionRecordRow)
        assert row.id >= 1
        assert row.request_id == observation.request_id
        assert _count_rows(observation.request_id) == 1

    def test_read_round_trips_all_contract_fields(self) -> None:
        observation = _observation(
            rerank_elapsed_ms=3.25, reranker_used=True,
            chunk_ids=(5, 3), document_ids=(9, 9, 8),
        )
        repository = RagExecutionRepository()
        repository.create(observation)

        rows = repository.get_by_request_id(observation.request_id)

        assert len(rows) == 1
        row = rows[0]
        assert row.request_id == observation.request_id
        assert row.started_at == observation.started_at
        assert row.finished_at == observation.finished_at
        assert row.duration_ms == pytest.approx(12.5)
        assert row.result_count == 3
        assert row.used_chunks_count == 2
        assert row.top_k == 5
        assert row.context_truncated is False
        assert row.context_chars == 120
        assert row.reranker_used is True
        assert row.rerank_elapsed_ms == pytest.approx(3.25)
        assert row.chunk_ids == (5, 3)                 # 顺序保持
        assert row.document_ids == (9, 8)              # 去重在 DTO 层保证

    def test_null_rerank_elapsed_stays_null(self) -> None:
        observation = _observation(rerank_elapsed_ms=None)   # 不是 0
        repository = RagExecutionRepository()
        repository.create(observation)

        rows = repository.get_by_request_id(observation.request_id)

        assert rows[0].rerank_elapsed_ms is None
        with _engine().connect() as conn:
            raw = conn.execute(
                text(
                    f"SELECT rerank_elapsed_ms FROM {_TABLE} "
                    "WHERE request_id = :rid"
                ),
                {"rid": observation.request_id},
            ).scalar_one()
        assert raw is None

    def test_empty_arrays_are_supported(self) -> None:
        observation = _observation(chunk_ids=(), document_ids=())
        repository = RagExecutionRepository()
        repository.create(observation)

        rows = repository.get_by_request_id(observation.request_id)

        assert rows[0].chunk_ids == ()
        assert rows[0].document_ids == ()


# ============================================================
# Case 3 / 4. multiple records + empty
# ============================================================

@requires_db
class TestMultipleAndEmpty:
    def test_multiple_records_isolated_by_request_id(self) -> None:
        request_a = _request_id("multi-a")
        request_b = _request_id("multi-b")
        repository = RagExecutionRepository()
        first = repository.create(_observation(request_id=request_a))
        second = repository.create(_observation(request_id=request_a))
        third = repository.create(_observation(request_id=request_b))

        rows_a = repository.get_by_request_id(request_a)
        rows_b = repository.get_by_request_id(request_b)

        assert [row.id for row in rows_a] == [first.id, second.id]   # id ASC
        assert [row.id for row in rows_b] == [third.id]
        assert {row.request_id for row in rows_a} == {request_a}
        assert {row.request_id for row in rows_b} == {request_b}

    def test_unknown_request_id_returns_empty_list(self) -> None:
        repository = RagExecutionRepository()

        assert repository.get_by_request_id(_request_id("unknown")) == []


# ============================================================
# Case 5 / 6. invalid input + failure semantics
# ============================================================

@requires_db
class TestValidationAndFailure:
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
    def test_invalid_request_id_rejected_before_db(self, bad, expected) -> None:  # noqa: ANN001
        repository = RagExecutionRepository(session_factory=_BoomFactory())  # type: ignore[arg-type]

        with pytest.raises(expected):
            repository.get_by_request_id(bad)          # DB 未被访问

    def test_db_failure_is_typed_error_not_empty(self) -> None:
        repository = RagExecutionRepository(session_factory=_BoomFactory())  # type: ignore[arg-type]

        with pytest.raises(RagExecutionRepositoryError):
            repository.get_by_request_id(_request_id("boom"))

    def test_query_service_propagates_failure_not_empty(self) -> None:
        service = RagExecutionPersistentQueryService(
            repository=RagExecutionRepository(  # type: ignore[arg-type]
                session_factory=_BoomFactory()
            )
        )

        with pytest.raises(RagExecutionRepositoryError):
            service.list_by_request_id(_request_id("boom"))


# ============================================================
# Case 7. Adapter 失败隔离（真实链路）
# ============================================================

@requires_db
class TestAdapterIsolation:
    def test_adapter_persists_through_real_chain(self) -> None:
        adapter = RagExecutionPersistenceAdapter()
        observation = _observation()

        row = adapter.record(observation)

        assert row is not None
        assert _count_rows(observation.request_id) == 1

    def test_adapter_isolates_real_db_failure(self, caplog) -> None:
        adapter = RagExecutionPersistenceAdapter(
            persistence_service=RagExecutionPersistenceService(
                repository=RagExecutionRepository(  # type: ignore[arg-type]
                    session_factory=_BoomFactory()
                )
            )
        )
        observation = _observation()

        with caplog.at_level(logging.WARNING):
            result = adapter.record(observation)

        assert result is None                                  # 失败已隔离
        assert _count_rows(observation.request_id) == 0        # 无半条记录
        assert caplog.records[-1].__dict__["error_type"] == (
            "RagExecutionRepositoryError"
        )
        assert caplog.records[-1].__dict__["request_id"] == (
            observation.request_id
        )


# ============================================================
# Case 8 / 9. Persistent Query Service + restart-like
# ============================================================

@requires_db
class TestPersistentReadBoundary:
    def test_query_service_reads_persisted_rows(self) -> None:
        request_id = _request_id("qsvc")
        RagExecutionPersistenceAdapter().record(_observation(request_id=request_id))
        service = RagExecutionPersistentQueryService()

        rows = service.list_by_request_id(request_id)

        assert len(rows) == 1
        assert rows[0].request_id == request_id

    def test_new_query_service_sees_persisted_observation(self) -> None:
        """restart-like：**新建** QueryService 仍能读到已落库的观测。"""
        request_id = _request_id("restart")
        RagExecutionPersistenceAdapter().record(_observation(request_id=request_id))

        fresh = RagExecutionPersistentQueryService()           # 新实例 / 新仓储
        rows = fresh.list_by_request_id(request_id)

        assert [row.request_id for row in rows] == [request_id]

    def test_residue_is_zero_after_cleanup(self) -> None:
        _delete_own_rows()
        with _engine().connect() as conn:
            remaining = int(
                conn.execute(
                    text(
                        f"SELECT COUNT(*) FROM {_TABLE} "
                        "WHERE request_id LIKE :prefix"
                    ),
                    {"prefix": f"{_PREFIX}%"},
                ).scalar_one()
            )
        assert remaining == 0
