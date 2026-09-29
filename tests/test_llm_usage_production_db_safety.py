"""LLM Usage Production DB Safety（Phase 3.12 Step 57）——连接 / 事务 / 并发安全契约。

审计对象（Step 56 已接线）：
    LLMClient → DatabaseLLMAccountingSink → LLMUsagePersistenceService
        → RuntimeBridge(asyncio.to_thread) → LLMUsageRepository → Session → INSERT

覆盖（**只补既有测试未覆盖的部分**；Step 56 / Step 3.10.x 已覆盖的不重复）：
    1. Pool 配置事实（pool_size / max_overflow / pre_ping / default timeout / recycle）
       —— 且**未新增** pool_timeout / pool_recycle 配置
    2. Session 生命周期契约（每次记录 **独立 Session**；成功 commit；失败 rollback；
       均关闭 → 无 Session 泄漏 / 无长事务 / 无全局 Session）
    3. Pool timeout 隔离（sqlalchemy.exc.TimeoutError ⊂ SQLAlchemyError →
       Repository 归因 → sink 收敛 → LLM 业务结果不变 + 不重试）
    4. DB 不可达（unreachable DSN）隔离：LLM 成功 · 仅 1 次 provider 调用 ·
       失败收敛为 warning（caplog 证据）
    5. [DB] 同 provider request_id 两次 → 幂等 1 行（ON CONFLICT DO NOTHING）
    6. [DB] 连接正确归还 Pool（checkedout 回落）+ 无 idle-in-transaction
    7. [DB] 行/索引形态：字段白名单 + request_id 唯一 partial 索引 +
       assistant_request_id **非**唯一（不是幂等键）

本文件不做压力测试 / benchmark / load test；只做静态契约 + 最小 DB 验证。
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError

from backend.app.config import settings
from backend.app.db import session as db_session
from backend.app.db.llm_usage_repository import (
    LLMUsageRepository,
    LLMUsageRepositoryError,
)
from backend.app.db.models.llm_usage_record import (
    LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX,
    LLM_USAGE_REQUEST_ID_INDEX,
)
from backend.app.llm import client as client_module
from backend.app.llm.client import (
    OpenAICompatibleClient,
    get_default_accounting_sink,
    get_default_llm_client,
    reset_default_llm_client,
)
from backend.app.llm.deepseek_provider import DeepSeekProvider
from backend.app.llm.observability import LLMObservation
from backend.app.services.context_builder import ContextBuildResult
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
    LLMUsagePersistenceService,
)
from backend.app.services.rag_observability_runtime import get_observed_rag_service
from backend.app.services.vector_search_service import VectorSearchResult

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PREFIX = "step57-"
_PROVIDER_ID = f"{_PREFIX}provider-fixed"
_RAG_QUESTION = "采购入库的操作步骤是什么"

_EXPECTED_COLUMNS = (
    "id",
    "request_id",
    "provider",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "created_at",
    "assistant_request_id",
)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _engine():  # noqa: ANN202
    engine = db_session.get_engine()
    if engine is None:
        pytest.skip("DATABASE_URL 未配置")
    return engine


def _run(coro_factory: Any) -> Any:
    return asyncio.run(coro_factory())


# ============================================================
# Fake Session / Factory（离线契约验证；不触达 DB）
# ============================================================

class _FakeResult:
    def __init__(self, value: int | None = 7) -> None:
        self._value = value

    def scalar_one_or_none(self) -> int | None:
        return self._value


class _FakeTransaction:
    def __init__(self, session: "_FakeSession") -> None:
        self._session = session

    def __enter__(self) -> "_FakeTransaction":
        self._session.began += 1
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is None:
            self._session.committed += 1
        else:
            self._session.rolled_back += 1
        return False


class _FakeSession:
    """最小 Session 替身：只记录生命周期（不执行真实 SQL）。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.began = 0
        self.committed = 0
        self.rolled_back = 0
        self.closed = 0
        self.executed = 0

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.closed += 1
        return False

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction(self)

    def execute(self, statement: Any) -> _FakeResult:  # noqa: ANN401
        self.executed += 1
        if self.fail:
            raise OperationalError("INSERT", {}, Exception("step57-boom"))
        return _FakeResult()


class _FakeFactory:
    """记录每次创建的 Session（验证 per-record 独立 Session）。"""

    def __init__(self, *, fail: bool = False, raise_on_call: Any = None) -> None:
        self.sessions: list[_FakeSession] = []
        self._fail = fail
        self._raise_on_call = raise_on_call

    def __call__(self) -> _FakeSession:
        if self._raise_on_call is not None:
            raise self._raise_on_call
        session = _FakeSession(fail=self._fail)
        self.sessions.append(session)
        return session


def _observation(provider_request_id: str = _PROVIDER_ID) -> LLMObservation:
    from backend.app.llm.client import LLMUsage

    return LLMObservation(
        request_id=provider_request_id,
        provider="step57-provider",
        model="step57-model",
        usage=LLMUsage(1, 2, 3),
        success=True,
    )


# ============================================================
# 1. Pool 配置事实
# ============================================================

class TestPoolConfiguration:
    def test_engine_pool_matches_configured_settings(self) -> None:
        engine = db_session.get_engine()
        if engine is None:
            pytest.skip("DATABASE_URL 未配置")
        pool = engine.pool

        assert type(pool).__name__ == "QueuePool"
        assert pool._max_overflow == settings.database.max_overflow == 10
        assert pool._timeout == 30.0            # SQLAlchemy 默认（未显式配置）
        assert pool._recycle == -1              # 未配置 pool_recycle（默认 -1）
        assert pool._pre_ping is True
        # pool_size 由 settings 传入 create_engine（见下方源码断言）
        assert isinstance(settings.database.pool_size, int)
        assert settings.database.pool_size == 5

    def test_session_module_does_not_add_pool_timeout_or_recycle(self) -> None:
        """静态：不得新增 pool_timeout / pool_recycle（本阶段禁止扩配置）。"""
        source = (_REPO_ROOT / "backend/app/db/session.py").read_text(
            encoding="utf-8"
        )

        assert "pool_size=db_settings.pool_size" in source
        assert "max_overflow=db_settings.max_overflow" in source
        assert "pool_pre_ping=True" in source
        assert "pool_timeout" not in source
        assert "pool_recycle" not in source


# ============================================================
# 2. Session 生命周期契约（per-record · commit / rollback / close）
# ============================================================

class TestSessionLifecycleContract:
    def test_success_path_commits_and_closes_one_session(self) -> None:
        factory = _FakeFactory()
        repository = LLMUsageRepository(session_factory=factory)

        created_id = repository.create(
            request_id=_PROVIDER_ID,
            provider="p",
            model="m",
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=3,
            assistant_request_id=None,
        )

        assert created_id == 7
        assert len(factory.sessions) == 1
        session = factory.sessions[0]
        assert (session.began, session.committed) == (1, 1)
        assert session.rolled_back == 0
        assert session.closed == 1              # 已关闭 → 无 Session 泄漏

    def test_failure_path_rolls_back_and_closes(self) -> None:
        factory = _FakeFactory(fail=True)
        repository = LLMUsageRepository(session_factory=factory)

        with pytest.raises(LLMUsageRepositoryError):
            repository.create(
                request_id=_PROVIDER_ID,
                provider="p",
                model="m",
                prompt_tokens=1,
                completion_tokens=2,
                total_tokens=3,
                assistant_request_id=None,
            )

        session = factory.sessions[0]
        assert session.rolled_back == 1          # 失败 → ROLLBACK
        assert session.committed == 0
        assert session.closed == 1               # 仍关闭

    def test_each_record_uses_its_own_session(self) -> None:
        factory = _FakeFactory()
        repository = LLMUsageRepository(session_factory=factory)

        for _ in range(3):
            repository.create(
                request_id=_PROVIDER_ID,
                provider="p",
                model="m",
                prompt_tokens=1,
                completion_tokens=2,
                total_tokens=3,
                assistant_request_id=None,
            )

        assert len(factory.sessions) == 3        # per-record 独立 Session
        assert len({id(s) for s in factory.sessions}) == 3
        assert all(s.closed == 1 for s in factory.sessions)
        assert all(s.committed == 1 for s in factory.sessions)

    def test_no_shared_session_or_engine_in_persistence_path(self) -> None:
        """静态：持久化路径不持有任何模块级 Session / Engine / Connection。"""
        for relative in (
            "backend/app/services/llm_usage_persistence_service.py",
            "backend/app/services/llm_usage_persistence_runtime.py",
            "backend/app/db/llm_usage_repository.py",
            "backend/app/llm/client.py",
        ):
            source = (_REPO_ROOT / relative).read_text(encoding="utf-8")
            for forbidden in (
                "sessionmaker(",
                "create_engine(",
                "SessionLocal =",
                "global_session",
            ):
                assert forbidden not in source, f"{relative}: {forbidden}"

        sink = get_default_accounting_sink()
        for attribute in ("_session", "_engine", "_connection", "_transaction"):
            assert not hasattr(sink, attribute), attribute

    def test_persistence_service_skips_none_usage_without_session(self) -> None:
        """usage=None（LLM 失败路径）→ 不写入，且**不创建 Session**。"""
        factory = _FakeFactory()
        service = LLMUsagePersistenceService(
            repository=LLMUsageRepository(session_factory=factory)
        )

        assert service.persist(None) is None
        assert factory.sessions == []


# ============================================================
# 3. Pool timeout / DB 错误隔离（离线）
# ============================================================

class TestPoolTimeoutIsolation:
    def test_pool_timeout_is_typed_and_swallowed(self) -> None:
        # sqlalchemy.exc.TimeoutError 是 SQLAlchemyError 子类 → Repository 归因正确
        assert issubclass(SATimeoutError, Exception)

        factory = _FakeFactory(
            raise_on_call=SATimeoutError("QueuePool limit of size 5 overflow 10 reached")
        )
        repository = LLMUsageRepository(session_factory=factory)

        with pytest.raises(LLMUsageRepositoryError):   # 归因为仓储错误（非裸异常）
            repository.create(
                request_id=_PROVIDER_ID,
                provider="p",
                model="m",
                prompt_tokens=1,
                completion_tokens=2,
                total_tokens=3,
                assistant_request_id=None,
            )
        assert factory.sessions == []                  # 未创建 Session

    def test_sink_and_llm_survive_pool_timeout_without_retry(self) -> None:
        factory = _FakeFactory(
            raise_on_call=SATimeoutError("step57-pool-timeout")
        )
        sink = DatabaseLLMAccountingSink(
            repository=LLMUsageRepository(session_factory=factory)
        )
        requests: list[httpx.Request] = []
        client = DeepSeekProvider(
            OpenAICompatibleClient(
                api_key="step57-key",
                base_url="https://step57.fake/v1",
                model="step57-model",
                provider="step57-provider",
                transport=httpx.MockTransport(
                    lambda request: (
                        requests.append(request)
                        or httpx.Response(
                            200,
                            json={
                                "id": _PROVIDER_ID,
                                "model": "step57-model",
                                "choices": [
                                    {
                                        "index": 0,
                                        "finish_reason": "stop",
                                        "message": {
                                            "role": "assistant",
                                            "content": "答案",
                                        },
                                    }
                                ],
                                "usage": {
                                    "prompt_tokens": 1,
                                    "completion_tokens": 1,
                                    "total_tokens": 2,
                                },
                            },
                        )
                    )
                ),
                accounting_sink=sink,
            )
        )

        answer = _run(lambda: client.chat([{"role": "user", "content": "hi"}]))

        assert answer == "答案"                  # LLM 业务结果不变
        assert len(requests) == 1                # 无 retry / 无重放
        sink.record(_observation())              # 同步入口同样不抛出
        _run(lambda: sink.arecord(_observation()))


# ============================================================
# 4. DB 不可达（unreachable DSN）隔离
# ============================================================

class TestDbUnreachableIsolation:
    def test_unreachable_db_keeps_llm_success(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        bad_settings = dataclasses.replace(
            settings,
            database=dataclasses.replace(
                settings.database,
                url=(
                    "postgresql+psycopg://step57:step57@127.0.0.1:1/step57"
                    "?connect_timeout=2"
                ),
            ),
        )
        monkeypatch.setattr(client_module, "settings", bad_settings)
        # get_engine() 读取 db/session.py 的 settings → 必须一并替换（否则会连真实库）
        monkeypatch.setattr(db_session, "settings", bad_settings)
        db_session.reset_engine_cache()
        reset_default_llm_client()
        try:
            # DB 已配置（不可达）→ 仍应选择 DatabaseLLMAccountingSink
            assert isinstance(
                get_default_accounting_sink(), DatabaseLLMAccountingSink
            )
            requests: list[httpx.Request] = []
            client = DeepSeekProvider(
                OpenAICompatibleClient(
                    api_key="step57-key",
                    base_url="https://step57.fake/v1",
                    model="step57-model",
                    provider="step57-provider",
                    transport=httpx.MockTransport(
                        lambda request: (
                            requests.append(request)
                            or httpx.Response(
                                200,
                                json={
                                    "id": _PROVIDER_ID,
                                    "model": "step57-model",
                                    "choices": [
                                        {
                                            "index": 0,
                                            "finish_reason": "stop",
                                            "message": {
                                                "role": "assistant",
                                                "content": "答案",
                                            },
                                        }
                                    ],
                                    "usage": {
                                        "prompt_tokens": 1,
                                        "completion_tokens": 1,
                                        "total_tokens": 2,
                                    },
                                },
                            )
                        )
                    ),
                    accounting_sink=get_default_accounting_sink(),
                )
            )

            with caplog.at_level(logging.WARNING):
                answer = _run(
                    lambda: client.chat([{"role": "user", "content": "hi"}])
                )

            assert answer == "答案"              # LLM 成功
            assert len(requests) == 1            # 无 retry / 无二次调用
            assert any(
                "LLM usage 持久化失败" in record.getMessage()
                for record in caplog.records
            ), "DB 不可达应只产生 persistence warning"
        finally:
            db_session.reset_engine_cache()
            reset_default_llm_client()


# ============================================================
# 5~7. [DB] 幂等 / 连接归还 / 行与索引形态
# ============================================================

def _delete_own_rows() -> None:
    with _engine().begin() as conn:
        conn.execute(
            text(
                "DELETE FROM ai_ops.llm_usage_record "
                "WHERE request_id LIKE :prefix OR assistant_request_id LIKE :prefix"
            ),
            {"prefix": f"{_PREFIX}%"},
        )


def _own_row_count() -> int:
    with _engine().connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.llm_usage_record "
                    "WHERE request_id LIKE :prefix OR assistant_request_id LIKE :prefix"
                ),
                {"prefix": f"{_PREFIX}%"},
            ).scalar_one()
        )


def _max_rag_id() -> int:
    with _engine().connect() as conn:
        return int(
            conn.execute(
                text("SELECT COALESCE(MAX(id), 0) FROM ai_ops.rag_execution_record")
            ).scalar_one()
        )


@pytest.fixture()
def _cleanup():
    """定向清理 + residue 断言。

    RAG 记录按 **id 水位线** 清理（RAG 行的 request_id 是 Assistant Trace ID，
    不是 ``step57-`` 前缀，见 Step 43：异常路径同样产生 RAG 记录）。
    """
    if not _env_flag("RUN_DB_TESTS"):
        yield
        return
    _delete_own_rows()
    rag_watermark = _max_rag_id()
    yield
    _delete_own_rows()
    with _engine().begin() as conn:
        conn.execute(
            text("DELETE FROM ai_ops.rag_execution_record WHERE id > :watermark"),
            {"watermark": rag_watermark},
        )
    assert _own_row_count() == 0, "Step 57 测试行未清理"
    with _engine().connect() as conn:
        assert int(
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.rag_execution_record "
                    "WHERE id > :watermark"
                ),
                {"watermark": rag_watermark},
            ).scalar_one()
        ) == 0, "RAG 表残留"


@pytest.fixture()
def _default_client_db(monkeypatch: pytest.MonkeyPatch):
    """生产默认 Client（真实 DB sink）+ MockTransport（0 网络）。"""
    monkeypatch.setattr(
        client_module,
        "settings",
        dataclasses.replace(
            settings,
            llm=dataclasses.replace(
                settings.llm,
                api_key="step57-key",
                base_url="https://step57.fake/v1",
                model="step57-model",
                provider="step57-provider",
            ),
        ),
    )
    real_class = client_module.OpenAICompatibleClient

    class _FixedIdClient(real_class):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            kwargs.setdefault(
                "transport",
                httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={
                            "id": _PROVIDER_ID,      # **固定** provider 请求 ID
                            "model": "step57-model",
                            "choices": [
                                {
                                    "index": 0,
                                    "finish_reason": "stop",
                                    "message": {
                                        "role": "assistant",
                                        "content": "答案",
                                    },
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 1,
                                "completion_tokens": 1,
                                "total_tokens": 2,
                            },
                        },
                    )
                ),
            )
            super().__init__(**kwargs)

    monkeypatch.setattr(
        client_module, "OpenAICompatibleClient", _FixedIdClient
    )
    reset_default_llm_client()
    yield get_default_llm_client()
    reset_default_llm_client()


@requires_db
class TestDuplicateRequestIdIdempotencyDb:
    def test_same_provider_request_id_twice_writes_one_row(
        self,
        _cleanup: Any,
        _default_client_db: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from fastapi.testclient import TestClient

        from backend.app.main import app

        rag = get_observed_rag_service()
        monkeypatch.setattr(
            rag, "_vector_search_service", _FakeVectorSearch()
        )
        monkeypatch.setattr(rag, "_context_builder", _EchoContextBuilder())
        monkeypatch.setattr(rag, "_llm_client", None)   # → 生产默认 Client

        client = TestClient(app)
        ids: list[str] = []
        for _ in range(2):
            response = client.post("/api/ai/chat", json={"question": _RAG_QUESTION})
            assert response.status_code == 200, response.text
            ids.append(response.json()["metadata"]["request_id"])

        with _engine().connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT assistant_request_id FROM ai_ops.llm_usage_record "
                    "WHERE request_id = :provider"
                ),
                {"provider": _PROVIDER_ID},
            ).all()
        assert len(rows) == 1                       # 幂等：仍只有 1 行
        assert rows[0][0] == ids[0]                 # first-write-wins

        # 第二次请求仍成功；其 Trace 的 llm_usage 为空（冲突被忽略，非错误）
        trace = client.get(f"/api/observability/assistant-trace/{ids[1]}")
        assert trace.status_code == 200
        assert trace.json()["llm_usage"] == []


@requires_db
class TestConnectionReturnedToPoolDb:
    def test_connection_returned_and_no_idle_in_transaction(
        self, _cleanup: Any, _default_client_db: Any
    ) -> None:
        engine = _engine()
        baseline_checkedout = engine.pool.checkedout()

        sink = get_default_accounting_sink()
        assert isinstance(sink, DatabaseLLMAccountingSink)
        for index in range(3):
            _run(
                lambda index=index: sink.arecord(
                    _observation(f"{_PREFIX}provider-pool-{index}")
                )
            )

        assert _own_row_count() == 3                # 3 次记录 → 3 行
        assert engine.pool.checkedout() == baseline_checkedout   # 已归还 Pool

        with engine.connect() as conn:
            assert int(
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM pg_stat_activity "
                        "WHERE state = 'idle in transaction'"
                    )
                ).scalar_one()
            ) == 0, "存在 idle-in-transaction（Session 未关闭）"

@requires_db
class TestRowShapeAndIndexDb:
    def test_columns_are_whitelisted_and_indexes_as_designed(
        self, _cleanup: Any
    ) -> None:
        with _engine().connect() as conn:
            columns = tuple(
                conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'ai_ops' "
                        "AND table_name = 'llm_usage_record' "
                        "ORDER BY ordinal_position"
                    )
                ).scalars()
            )
            definitions = {
                name: ddl
                for name, ddl in conn.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE schemaname = 'ai_ops' "
                        "AND tablename = 'llm_usage_record'"
                    )
                ).all()
            }

        assert columns == _EXPECTED_COLUMNS                 # 白名单列（未扩大）
        assert LLM_USAGE_REQUEST_ID_INDEX in definitions
        assert "UNIQUE" in definitions[LLM_USAGE_REQUEST_ID_INDEX].upper()
        assert "request_id IS NOT NULL" in definitions[
            LLM_USAGE_REQUEST_ID_INDEX
        ]                                                    # 幂等键 = request_id
        assert LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX in definitions
        assert "UNIQUE" not in definitions[
            LLM_USAGE_ASSISTANT_REQUEST_ID_INDEX
        ].upper()                                            # assistant_request_id 非唯一

        for name, ddl in definitions.items():
            if "UNIQUE" in ddl.upper():
                assert "assistant_request_id" not in ddl, f"{name} 不应以关联键唯一"


# ============================================================
# 共享 Fake（RAG 外部边界）
# ============================================================

class _FakeVectorSearch:
    async def search(self, query: str, *, top_k: int) -> list[VectorSearchResult]:
        return [
            VectorSearchResult(
                chunk_id=201,
                document_id=30,
                chunk_index=0,
                content="STEP57-CHUNK",
                distance=0.1,
                similarity=0.9,
                metadata={"heading": "采购入库"},
            )
        ]


class _EchoContextBuilder:
    def build(self, results: Any) -> ContextBuildResult:  # noqa: ANN401
        used = tuple(results)
        return ContextBuildResult(
            text="\n".join(r.content for r in used),
            used_chunks=used,
            total_chars=32,
            truncated=False,
            dropped_count=0,
        )


__all__ = [
    "TestPoolConfiguration",
    "TestSessionLifecycleContract",
    "TestPoolTimeoutIsolation",
    "TestDbUnreachableIsolation",
    "TestDuplicateRequestIdIdempotencyDb",
    "TestConnectionReturnedToPoolDb",
    "TestRowShapeAndIndexDb",
]
