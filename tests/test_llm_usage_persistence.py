"""LLM Usage Persistence 集成测试（Phase 3.10.14）。

覆盖（任务书 §三十一 ~ §三十七）：

    1.  Table exists              （llm_usage_record 表存在）
    2.  Full usage               （100 / 20 / 120 正确保存）
    3.  Partial usage            （None / 200 / None → NULL / 200 / NULL）
    4.  Usage=None               （sink 不报错，不写入行）
    5.  Provider                 （原样保存；None → NULL，不隐式填充）
    6.  Model                    （来自 LLMObservation.model，非配置 model）
    7.  Request ID               （原样保存；None → NULL，不生成 UUID）
    8.  Secrets / Cost           （无 prompt / SQL / RAG / tool / secret /
                                  price / cost / currency 字段）
    9.  Persistence failure      （仓储抛错 → 业务结果不变、无 retry）
    10. Concurrency              （5 并发 → 5 行，request_id / usage 正确对应）
    11. Tool Calling             （2 轮 → 2 行，不聚合）
    12. T2S Retry                （2 次请求 → 2 行 request-level）
    13. RAG                      （1 行；业务答案不变）
    14. Refusal                  （1 行；attempts=1 语义不变）
    15. DB residue               （测试后无残留行）

环境：
    - RUN_DB_TESTS=1 + DATABASE_URL 指向测试 PostgreSQL（不碰生产库）
    - 未启用时全部 skip（默认 pytest 不连数据库）

隔离策略（与 test_knowledge_models.py 一致）：
    - module fixture: Base.metadata.create_all()
    - autouse fixture: 每个测试后 TRUNCATE llm_usage_record RESTART IDENTITY
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from backend.app.db import reset_engine_cache
from backend.app.db.base import Base
from backend.app.db.llm_usage_repository import (
    LLMUsageRepository,
    LLMUsageRepositoryError,
)
from backend.app.db.models import LLMUsageRecord
from backend.app.db.models.llm_usage_record import LLM_USAGE_SCHEMA
from backend.app.db.session import get_engine, get_session_factory
from backend.app.llm.client import LLMUsage, OpenAICompatibleClient
from backend.app.llm.deepseek_provider import DeepSeekProvider
from backend.app.llm.observability import LLMObservation
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
    LLMUsagePersistenceService,
)


# ============================================================
# RUN_DB_TESTS 门控（复用项目既有约定）
# ============================================================

def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


_RUN_DB = _env_flag("RUN_DB_TESTS")

requires_db = pytest.mark.skipif(
    not _RUN_DB,
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)

_APPROVED_COLUMNS = {
    "id",
    "request_id",
    "provider",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    # Phase 3.12 Step 36：Assistant Trace 关联列（nullable；旧数据 / 旧链路 NULL）
    "assistant_request_id",
    "created_at",
}


# ============================================================
# Helpers
# ============================================================

def _observation(
    *,
    usage: LLMUsage | None = None,
    request_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    success: bool = True,
) -> LLMObservation:
    return LLMObservation(
        provider=provider,
        model=model,
        success=success,
        usage=usage,
        request_id=request_id,
    )


def _content_body(
    content: str | None,
    *,
    request_id: str = "chatcmpl-persist-1",
    finish_reason: str = "stop",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": request_id,
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
    }
    if usage is not None:
        body["usage"] = usage
    return body


def _tool_call_body(
    *, request_id: str, usage: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = _content_body(
        None, request_id=request_id, finish_reason="tool_calls", usage=usage
    )
    body["choices"][0]["message"]["tool_calls"] = [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "get_inventory",
                "arguments": '{"material_code": "MAT001"}',
            },
        }
    ]
    return body


def _provider(handler: Any, sink: Any = None) -> DeepSeekProvider:
    """真实 Client（MockTransport）+ 可选 accounting sink。"""
    client = OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://api.example.com/v1",
        model="configured-model",
        provider="deepseek-test",
        transport=httpx.MockTransport(handler),
        accounting_sink=sink,
    )
    return DeepSeekProvider(client)


def _rows(db: Session) -> list[LLMUsageRecord]:
    return list(db.scalars(select(LLMUsageRecord).order_by(LLMUsageRecord.id)).all())


def _count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(LLMUsageRecord)) or 0)


class FailingRepository:
    """模拟仓储写入失败（测试 persistence failure 隔离）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> int:
        self.calls.append(kwargs)
        raise LLMUsageRepositoryError("simulated persistence failure")


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module")
def engine():
    """module-level：建表（幂等）。"""
    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置；请配置后启用 RUN_DB_TESTS=1"
    from backend.app.db import models  # noqa: F401  确保 Model 已注册

    # llm_usage_record 位于独立 schema（与业务 schema public 隔离）
    with eng.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {LLM_USAGE_SCHEMA}"))
    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture
def db(engine):
    """function-level：独立 Session。"""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _truncate_after_each(engine):
    """每个测试后清理 llm_usage_record，保证隔离 + 零残留。"""
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                f"TRUNCATE TABLE {LLM_USAGE_SCHEMA}.llm_usage_record "
                "RESTART IDENTITY CASCADE"
            )
        )


# ============================================================
# 1. Table exists
# ============================================================

@requires_db
class TestSchema:
    def test_table_exists(self, engine) -> None:
        insp = inspect(engine)
        assert insp.has_table("llm_usage_record", schema=LLM_USAGE_SCHEMA)
        # 关键：不得出现在业务 schema（public）——否则会被 T2S
        # 的 schema 发现当成业务表（既有 DB 测试也会失败）
        assert not insp.has_table("llm_usage_record", schema="public")

    def test_columns_are_approved_only(self, engine) -> None:
        """只允许 §三十九 明确批准的字段（精确列名白名单）；
        不存在 prompt / SQL / RAG / tool / secret / price / cost /
        currency 等字段。"""
        insp = inspect(engine)
        columns = {
            c["name"]
            for c in insp.get_columns(
                "llm_usage_record", schema=LLM_USAGE_SCHEMA
            )
        }

        # 强保证：列名集合恰好等于批准集合（多一个/少一个都失败）
        assert columns == _APPROVED_COLUMNS

        # 显式确认不存在任何违规列（精确列名，避免 prompt_tokens 被误判）
        forbidden_columns = {
            "prompt", "system_prompt", "messages", "sql", "rag_content",
            "chunk_content", "tool_args", "tool_result", "raw_response",
            "headers", "api_key", "password", "authorization", "secret",
            "input_price", "output_price", "currency",
            "input_cost", "output_cost", "total_cost",
        }
        assert not (columns & forbidden_columns)


# ============================================================
# 2 / 5 / 6 / 7. Full usage + provider / model / request_id
# ============================================================

@requires_db
class TestFullUsagePersistence:
    def test_full_usage_persisted(self, db: Session) -> None:
        sink = DatabaseLLMAccountingSink()
        observation = _observation(
            usage=LLMUsage(100, 20, 120),
            request_id="chatcmpl-persist-full",
            provider="deepseek-test",
            model="deepseek-chat",
        )

        sink.record(observation)

        rows = _rows(db)
        assert len(rows) == 1
        row = rows[0]
        assert row.prompt_tokens == 100
        assert row.completion_tokens == 20
        assert row.total_tokens == 120
        assert row.request_id == "chatcmpl-persist-full"
        assert row.provider == "deepseek-test"
        assert row.model == "deepseek-chat"
        # created_at 由 DB server_default 填充
        assert row.created_at is not None

    def test_model_comes_from_observation_not_config(self, db: Session) -> None:
        """model 必须来自实际响应（Observation.model），
        client 配置的 configured-model 不得出现在库里。"""
        handler_calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            handler_calls["n"] += 1
            return httpx.Response(
                200,
                json=_content_body(
                    "answer",
                    request_id="chatcmpl-model",
                    usage={"prompt_tokens": 10, "completion_tokens": 2,
                           "total_tokens": 12},
                ),
            )

        sink = DatabaseLLMAccountingSink()
        provider = _provider(handler, sink)

        async def _run() -> None:
            await provider.chat([{"role": "user", "content": "hi"}])

        asyncio.run(_run())

        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0].model == "deepseek-chat"
        assert rows[0].model != "configured-model"

    def test_request_id_none_stays_null(self, db: Session) -> None:
        """request_id 缺失 → NULL（不生成 UUID 冒充 Provider request ID）。"""
        sink = DatabaseLLMAccountingSink()
        sink.record(
            _observation(
                usage=LLMUsage(1, 2, 3),
                request_id=None,
                provider="deepseek-test",
                model="deepseek-chat",
            )
        )

        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0].request_id is None
        # 确非伪造：库里不存在 UUID 形态的 request_id
        assert rows[0].request_id != "unknown"

    def test_provider_none_stays_null(self, db: Session) -> None:
        """provider 缺失 → NULL（禁止 unknown / deepseek / default 填充）。"""
        sink = DatabaseLLMAccountingSink()
        sink.record(
            _observation(usage=LLMUsage(1, 2, 3), provider=None)
        )

        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0].provider is None
        assert rows[0].provider not in {"unknown", "deepseek", "default"}


# ============================================================
# 3 / 4. Partial usage / None usage
# ============================================================

@requires_db
class TestPartialAndNoneUsage:
    def test_partial_usage_preserves_null(self, db: Session) -> None:
        """partial usage：None / 200 / None → NULL / 200 / NULL（NULL ≠ 0）。"""
        sink = DatabaseLLMAccountingSink()
        sink.record(
            _observation(
                usage=LLMUsage(completion_tokens=200),
                request_id="chatcmpl-partial",
                provider="deepseek-test",
                model="deepseek-chat",
            )
        )

        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0].prompt_tokens is None
        assert rows[0].completion_tokens == 200
        assert rows[0].total_tokens is None
        # 明确不是 0
        assert rows[0].prompt_tokens != 0
        assert rows[0].total_tokens != 0

    def test_usage_none_writes_no_row(self, db: Session) -> None:
        """usage=None → sink 不报错，且不写入行
        （本表是 Usage Storage，不是 request audit log）。"""
        sink = DatabaseLLMAccountingSink()
        sink.record(_observation(usage=None, request_id="chatcmpl-nousage"))

        assert _count(db) == 0

    def test_failure_observation_writes_no_row(self, db: Session) -> None:
        """success=False（无 usage）→ 不写入 Usage Record。"""
        sink = DatabaseLLMAccountingSink()
        sink.record(_observation(usage=None, success=False))

        assert _count(db) == 0


# ============================================================
# 9. Persistence failure 不影响业务结果
# ============================================================

@requires_db
class TestPersistenceFailureIsolation:
    def test_persistence_failure_keeps_business_result(self, db: Session) -> None:
        """仓储写入失败：LLM 业务结果不变、无 retry（只 1 次 HTTP 请求）。"""
        http_calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            http_calls["n"] += 1
            return httpx.Response(
                200,
                json=_content_body(
                    "business answer",
                    request_id="chatcmpl-fail",
                    usage={"prompt_tokens": 10, "completion_tokens": 2,
                           "total_tokens": 12},
                ),
            )

        failing_repo = FailingRepository()
        sink = DatabaseLLMAccountingSink(
            persistence_service=LLMUsagePersistenceService(
                repository=failing_repo  # type: ignore[arg-type]
            )
        )
        provider = _provider(handler, sink)

        async def _run() -> str:
            return await provider.chat([{"role": "user", "content": "hi"}])

        answer = asyncio.run(_run())

        assert answer == "business answer"      # 业务结果不变
        assert http_calls["n"] == 1             # 无 LLM retry
        assert len(failing_repo.calls) == 1     # 只尝试写入一次（无重试队列）
        assert _count(db) == 0                  # 事务已回滚


# ============================================================
# 10. Concurrency
# ============================================================

@requires_db
class TestConcurrency:
    def test_five_concurrent_requests_five_rows(self, db: Session) -> None:
        """5 并发请求 → 5 行，request_id / usage 各自正确对应，无串线。"""
        counter = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            counter["n"] += 1
            n = counter["n"]
            return httpx.Response(
                200,
                json=_content_body(
                    f"answer-{n}",
                    request_id=f"chatcmpl-conc-{n}",
                    usage={"prompt_tokens": 100 * n,
                           "completion_tokens": 10 * n,
                           "total_tokens": 110 * n},
                ),
            )

        sink = DatabaseLLMAccountingSink()
        provider = _provider(handler, sink)

        async def _run() -> list[Any]:
            return list(
                await asyncio.gather(
                    *[
                        provider.chat([{"role": "user", "content": f"q-{i}"}])
                        for i in range(5)
                    ]
                )
            )

        answers = asyncio.run(_run())
        assert len(answers) == 5

        rows = _rows(db)
        assert len(rows) == 5
        by_request_id = {r.request_id: r for r in rows}
        assert set(by_request_id) == {
            f"chatcmpl-conc-{n}" for n in range(1, 6)
        }
        for n in range(1, 6):
            row = by_request_id[f"chatcmpl-conc-{n}"]
            assert row.prompt_tokens == 100 * n
            assert row.total_tokens == 110 * n
            assert row.model == "deepseek-chat"


# ============================================================
# 11. Tool Calling（request-level，不聚合）
# ============================================================

@requires_db
class TestToolCallingPersistence:
    def test_two_rounds_two_rows(self, db: Session) -> None:
        """Tool Calling 两轮 → 2 行 request-level 记录（不合并）。"""
        first = _tool_call_body(
            request_id="chatcmpl-tc-1",
            usage={"prompt_tokens": 100, "completion_tokens": 10,
                   "total_tokens": 110},
        )
        second = _content_body(
            "final answer",
            request_id="chatcmpl-tc-2",
            usage={"prompt_tokens": 200, "completion_tokens": 30,
                   "total_tokens": 230},
        )
        rounds = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            rounds["n"] += 1
            return httpx.Response(
                200, json=first if rounds["n"] == 1 else second
            )

        from backend.app.tools.mock_tools import register_mock_tools
        from backend.app.tools.registry import ToolRegistry

        registry = ToolRegistry()
        register_mock_tools(registry)
        from backend.app.services.tool_chat_service import ToolChatService

        sink = DatabaseLLMAccountingSink()
        provider = _provider(handler, sink)

        async def _run() -> Any:
            return await ToolChatService(provider).chat(
                "帮我查询 MAT001 的库存", registry=registry
            )

        asyncio.run(_run())

        assert rounds["n"] == 2
        rows = _rows(db)
        assert len(rows) == 2                    # 不是 1 条聚合记录
        totals = sorted(r.total_tokens for r in rows)
        assert totals == [110, 230]              # 各自独立，未求和
        assert {r.request_id for r in rows} == {
            "chatcmpl-tc-1", "chatcmpl-tc-2",
        }


# ============================================================
# 12. T2S semantic retry
# ============================================================

@requires_db
class TestT2SRetryPersistence:
    def test_two_requests_two_rows(self, db: Session) -> None:
        """T2S semantic retry：2 次实际请求 → 2 行（无 retry 聚合）。"""
        first = _content_body(
            "SELECT 1",  # 缺 LIMIT → Validator 拒绝（触发语义 retry）
            request_id="chatcmpl-t2s-1",
            usage={"prompt_tokens": 100, "completion_tokens": 20,
                   "total_tokens": 120},
        )
        second = _content_body(
            "```sql\nSELECT 1 LIMIT 1\n```",
            request_id="chatcmpl-t2s-2",
            usage={"prompt_tokens": 150, "completion_tokens": 25,
                   "total_tokens": 175},
        )
        bodies = [first, second]
        sink = DatabaseLLMAccountingSink()
        provider = _provider(
            lambda request: httpx.Response(200, json=bodies.pop(0)), sink
        )
        from backend.app.services.text_to_sql_service import TextToSQLService

        async def _run() -> Any:
            return await TextToSQLService(
                llm_client=provider, max_attempts=3
            ).generate("知识文档有哪些？", database_context="ctx")

        result = asyncio.run(_run())

        assert result.validated is True
        assert result.attempts == 2
        rows = _rows(db)
        assert len(rows) == 2                     # request-level，非聚合
        assert sorted(r.total_tokens for r in rows) == [120, 175]
        # 无 retry 聚合字段
        for row in rows:
            assert not hasattr(row, "retry_total_tokens")


# ============================================================
# 13. RAG
# ============================================================

@requires_db
class TestRagPersistence:
    def test_rag_usage_record_and_answer_unchanged(self, db: Session) -> None:
        """RAG：业务答案不变，新增 1 条 request-level usage 记录。"""
        from backend.app.services.rag_service import RagService
        from backend.app.services.vector_search_service import VectorSearchResult

        class FakeVectorSearch:
            async def search(self, query: str, *, top_k: int):
                return [
                    VectorSearchResult(
                        chunk_id=1, document_id=1, chunk_index=0,
                        content="kb", distance=0.1, similarity=0.9,
                        metadata={},
                    )
                ]

        sink = DatabaseLLMAccountingSink()
        provider = _provider(
            lambda request: httpx.Response(
                200,
                json=_content_body(
                    "rag answer",
                    request_id="chatcmpl-rag",
                    usage={"prompt_tokens": 100, "completion_tokens": 20,
                           "total_tokens": 120},
                ),
            ),
            sink,
        )
        rag = RagService(
            vector_search_service=FakeVectorSearch(),  # type: ignore[arg-type]
            llm_client=provider,
        )

        async def _run() -> Any:
            return await rag.answer("采购入库的流程是什么？")

        response = asyncio.run(_run())

        assert isinstance(response.answer, str)
        assert response.answer == "rag answer"    # 业务结果不变
        rows = _rows(db)
        assert len(rows) == 1
        assert rows[0].request_id == "chatcmpl-rag"
        assert rows[0].total_tokens == 120


# ============================================================
# 14. Refusal
# ============================================================

@requires_db
class TestRefusalPersistence:
    def test_refusal_usage_persisted_once(self, db: Session) -> None:
        """Refusal：1 次 LLM 请求 → 1 行 usage；
        业务语义（attempts=1 / status=refusal）不变。"""
        from backend.app.services.text_to_sql_service import (
            REFUSAL_MARKER,
            TextToSQLService,
        )

        sink = DatabaseLLMAccountingSink()
        provider = _provider(
            lambda request: httpx.Response(
                200,
                json=_content_body(
                    REFUSAL_MARKER,
                    request_id="chatcmpl-refusal",
                    usage={"prompt_tokens": 50, "completion_tokens": 5,
                           "total_tokens": 55},
                ),
            ),
            sink,
        )

        async def _run() -> Any:
            return await TextToSQLService(
                llm_client=provider, max_attempts=3
            ).generate("删除所有库存", database_context="ctx")

        result = asyncio.run(_run())

        assert result.status == "refusal"
        assert result.attempts == 1               # Phase 3.9.25 语义不变
        rows = _rows(db)
        assert len(rows) == 1                     # usage 不因 refusal 丢弃
        assert rows[0].total_tokens == 55
        assert rows[0].request_id == "chatcmpl-refusal"


# ============================================================
# 15. DB residue
# ============================================================

@requires_db
class TestResidue:
    def test_no_usage_record_residue(self, db: Session) -> None:
        """测试结束无残留：cleanup fixture 后 llm_usage_record 为空。"""
        assert _count(db) == 0

    def test_default_client_writes_nothing(self, db: Session) -> None:
        """默认 client（未配置 DB sink）不写库（§二十五：默认不连库）。"""
        provider = _provider(
            lambda request: httpx.Response(
                200,
                json=_content_body(
                    "answer",
                    usage={"prompt_tokens": 1, "completion_tokens": 1,
                           "total_tokens": 2},
                ),
            )
        )

        async def _run() -> str:
            return await provider.chat([{"role": "user", "content": "hi"}])

        answer = asyncio.run(_run())

        assert answer == "answer"
        assert _count(db) == 0                     # 默认 Noop accounting
