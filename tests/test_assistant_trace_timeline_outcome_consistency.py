"""Assistant Trace ↔ Assistant Timeline **Outcome 一致性 Audit**（Phase 3.12 Step 71）。

只回答一个问题：

    同一个 assistant_request_id 上，
        GET /api/observability/assistant-trace/{id}      → outcome
        GET /api/observability/assistant-timeline/{id}   → outcome_event.status
    **是否表达一致的业务终态**？并且两个视图的事件集合（数量 / 身份 / 顺序）
    是否一致？

本阶段不改任何生产代码 / DTO / QueryService / persistence / DB schema / API
contract（§四/§二十六）；发现真实 bug → **停止并报告**。

真实链路：Fake LLM（MockTransport）+ 真实 persistence + 真实 PostgreSQL +
真实两个 Read API；数据前缀 ``step71-``，按精确 request_id 定向清理。

已知（且**不得**改变）的 Contract 事实：

    * Trace 的 ``llm_usage[]`` 暴露 DB 主键 ``id``；
      Trace 的 ``tool_executions[]`` / ``rag_executions[]`` **不暴露**主键
      （Step 48 决定）→ Tool/RAG 只能按"数量 + 内容 + 顺序"比对，
      source_id 只在 Timeline 侧存在（= 真实主键）。
    * Trace 的 ``outcome`` 是枚举字符串；Timeline 的 ``outcome_event`` 是事件对象
      （含 source_id / created_at / status）。
"""
from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.api import orchestrator_chat as root
from backend.app.db.session import get_engine, get_session_factory
from backend.app.llm.client import OpenAICompatibleClient
from backend.app.main import app
from backend.app.services.llm_usage_persistence_service import (
    DatabaseLLMAccountingSink,
)
from backend.app.services.text_to_sql_service import REFUSAL_MARKER

from tests.test_assistant_trace_correlation_e2e import (  # noqa: PLC2701
    _RAG_QUESTION,
    _TOOL_QUESTION,
    _response_body,
)
from tests.test_assistant_trace_multi_path_e2e import (  # noqa: PLC2701
    _GOOD_SQL,
    _install_t2sql_route,
)

_TRACE = "/api/observability/assistant-trace"
_TIMELINE = "/api/observability/assistant-timeline"
_PREFIX = "step71-"
_BAD_SQL = "DROP TABLE x"
_SENTINEL = "STEP71-INTERNAL-SECRET"
_T2SQL_QUESTION = "统计最近7天的入库单数量"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


# ============================================================
# DB helpers
# ============================================================

def _llm_ids(request_id: str) -> list[int]:
    with get_engine().connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT id FROM ai_ops.llm_usage_record "
                    "WHERE assistant_request_id = :rid ORDER BY created_at, id"
                ),
                {"rid": request_id},
            ).scalars()
        )


def _ids_of(table: str, request_id: str) -> list[int]:
    with get_engine().connect() as conn:
        return list(
            conn.execute(
                text(
                    f"SELECT id FROM ai_ops.{table} WHERE request_id = :rid "
                    "ORDER BY id"
                ),
                {"rid": request_id},
            ).scalars()
        )


def _outcome(request_id: str) -> tuple[int, str] | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, outcome FROM ai_ops.assistant_outcome_record "
                "WHERE assistant_request_id = :rid"
            ),
            {"rid": request_id},
        ).one_or_none()
    return None if row is None else (row.id, row.outcome)


def _outcome_ids() -> set[str]:
    with get_engine().connect() as conn:
        return set(
            conn.execute(
                text(
                    "SELECT assistant_request_id "
                    "FROM ai_ops.assistant_outcome_record"
                )
            ).scalars()
        )


def _delete_rows(request_ids: list[str]) -> None:
    ids = request_ids or [""]
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "DELETE FROM ai_ops.llm_usage_record "
                "WHERE assistant_request_id = ANY(:ids) "
                "OR request_id LIKE :prefix OR provider = :provider"
            ),
            {
                "ids": ids,
                "prefix": f"{_PREFIX}%",
                "provider": "step71-provider",
            },
        )
        for table in ("tool_execution_record", "rag_execution_record"):
            conn.execute(
                text(
                    f"DELETE FROM ai_ops.{table} WHERE request_id = ANY(:ids)"
                ),
                {"ids": ids},
            )
        conn.execute(
            text(
                "DELETE FROM ai_ops.assistant_outcome_record "
                "WHERE assistant_request_id = ANY(:ids)"
            ),
            {"ids": ids},
        )


def _residue() -> dict[str, int]:
    with get_engine().connect() as conn:
        return {
            "llm": conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.llm_usage_record "
                    "WHERE provider = :provider OR request_id LIKE :prefix"
                ),
                {"provider": "step71-provider", "prefix": f"{_PREFIX}%"},
            ).scalar_one(),
            "tool": conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.tool_execution_record "
                    "WHERE request_id LIKE :prefix"
                ),
                {"prefix": f"{_PREFIX}%"},
            ).scalar_one(),
            "rag": conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.rag_execution_record "
                    "WHERE request_id LIKE :prefix"
                ),
                {"prefix": f"{_PREFIX}%"},
            ).scalar_one(),
            "outcome": conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.assistant_outcome_record "
                    "WHERE assistant_request_id LIKE :prefix"
                ),
                {"prefix": f"{_PREFIX}%"},
            ).scalar_one(),
        }


# ============================================================
# Fake 读边界 / 传输层（真实 persistence）
# ============================================================

class _FakeVectorSearch:
    async def search(self, query: str, *, top_k: int):  # noqa: ANN201
        from backend.app.services.vector_search_service import (
            VectorSearchResult,
        )

        return [
            VectorSearchResult(
                chunk_id=901,
                document_id=90,
                chunk_index=0,
                content="STEP71-CHUNK",
                distance=0.1,
                similarity=0.9,
                metadata={"heading": "step71"},
            )
        ]


class _EmptyVectorSearch:
    async def search(self, query: str, *, top_k: int):  # noqa: ANN201
        return []


class _BoomVectorSearch:
    async def search(self, query: str, *, top_k: int):  # noqa: ANN201
        raise RuntimeError(_SENTINEL)


class _EchoContextBuilder:
    def build(self, results: Any):  # noqa: ANN401
        from backend.app.services.context_builder import ContextBuildResult

        used = tuple(results)
        return ContextBuildResult(
            text="\n".join(item.content for item in used),
            used_chunks=used,
            total_chars=len(used) * 16,
            truncated=False,
            dropped_count=0,
        )


class _RecordingHandler:
    async def __call__(self, arguments: Any):  # noqa: ANN401
        return {"material_code": arguments.get("material_code"), "qty": 5}


class _BoomHandler:
    async def __call__(self, arguments: Any):  # noqa: ANN401
        raise RuntimeError(_SENTINEL)


class _BoomExecutor:
    async def execute(self, sql: str, **kwargs: Any) -> Any:  # noqa: ANN401
        raise RuntimeError(_SENTINEL)


def _client_with(
    handler: Any,
) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://step71.fake/v1",
        model="step71-model",
        provider="step71-provider",
        transport=httpx.MockTransport(handler),
        accounting_sink=DatabaseLLMAccountingSink(),
    )


def _rag_client() -> OpenAICompatibleClient:
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            200,
            json=_response_body(f"{_PREFIX}rag-{counter['n']}", "已收到问题"),
        )

    return _client_with(handler)


def _retry_client() -> OpenAICompatibleClient:
    """attempt1 → 非法 SQL；attempt2（prompt 带上一次 SQL）→ 合法 SQL。"""
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        body = request.content.decode("utf-8", "ignore")
        sql = _GOOD_SQL if _BAD_SQL in body else _BAD_SQL
        return httpx.Response(
            200, json=_response_body(f"{_PREFIX}sql-{counter['n']}", sql)
        )

    return _client_with(handler)


def _refusal_client() -> OpenAICompatibleClient:
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            200,
            json=_response_body(f"{_PREFIX}refuse-{counter['n']}", REFUSAL_MARKER),
        )

    return _client_with(handler)


@pytest.fixture()
def consistency_db(monkeypatch: pytest.MonkeyPatch):
    if get_engine() is None or get_session_factory() is None:
        pytest.skip("DATABASE_URL 未配置")

    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()

    state: dict[str, Any] = {"request_ids": []}

    from backend.app.services.rag_observability_runtime import (
        get_observed_rag_service,
    )

    rag = get_observed_rag_service()
    monkeypatch.setattr(rag, "_llm_client", _rag_client())
    monkeypatch.setattr(rag, "_vector_search_service", _FakeVectorSearch())
    monkeypatch.setattr(rag, "_context_builder", _EchoContextBuilder())
    monkeypatch.setitem(
        root._TOOL_REGISTRY._handlers, "get_inventory", _RecordingHandler()
    )

    yield state

    root._TOOL_EXECUTION_COLLECTOR.clear()
    from backend.app.services.rag_observability_runtime import (
        get_rag_execution_collector,
    )

    get_rag_execution_collector().clear()
    _delete_rows(list(state["request_ids"]))
    assert _residue() == {"llm": 0, "tool": 0, "rag": 0, "outcome": 0}


def _chat(question: str, state: dict[str, Any]) -> tuple[int, Any]:
    with TestClient(app) as http:
        response = http.post("/api/ai/chat", json={"question": question})
    if response.status_code == 200:
        state["request_ids"].append(response.json()["metadata"]["request_id"])
    return response.status_code, response


def _read_both(request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with TestClient(app) as http:
        trace = http.get(f"{_TRACE}/{request_id}")
        timeline = http.get(f"{_TIMELINE}/{request_id}")
    assert trace.status_code == 200, trace.text
    assert timeline.status_code == 200, timeline.text
    return trace.json(), timeline.json()


def _assert_outcome_matches(
    trace: dict[str, Any],
    timeline: dict[str, Any],
    *,
    expected: str | None,
    request_id: str,
) -> None:
    """Trace.outcome ↔ Timeline.outcome_event.status 一致性（不靠 HTTP status）。"""
    assert trace["outcome"] == expected, trace["outcome"]
    if expected is None:
        assert timeline["outcome_event"] is None
        return
    assert timeline["outcome_event"] is not None
    assert timeline["outcome_event"]["status"] == expected
    # source_id = 真实 assistant_outcome_record.id
    outcome = _outcome(request_id)
    assert outcome is not None
    assert timeline["outcome_event"]["source_id"] == outcome[0]
    assert outcome[1] == expected


def _failed_request_id(before: set[str], exclude: set[str]) -> str:
    new_ids = sorted(_outcome_ids() - before - exclude)
    assert len(new_ids) == 1, new_ids
    return new_ids[0]


# ============================================================
# 场景矩阵（§六 ~ §十四）
# ============================================================

@requires_db
class TestOutcomeConsistencyMatrix:
    def test_rag_success(self, consistency_db) -> None:
        status, response = _chat(_RAG_QUESTION, consistency_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]

        trace, timeline = _read_both(request_id)
        _assert_outcome_matches(
            trace, timeline, expected="SUCCESS", request_id=request_id
        )
        assert len(trace["llm_usage"]) == len(timeline["llm_events"]) == 1
        assert len(trace["rag_executions"]) == len(timeline["rag_events"]) == 1

    def test_rag_empty(self, consistency_db, monkeypatch) -> None:
        """空检索 → EMPTY（由 outcome persistence 决定，不是从 HTTP 200 推导）。"""
        from backend.app.services.rag_observability_runtime import (
            get_observed_rag_service,
        )

        monkeypatch.setattr(
            get_observed_rag_service(),
            "_vector_search_service",
            _EmptyVectorSearch(),
        )
        status, response = _chat(_RAG_QUESTION, consistency_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]
        assert response.json()["metadata"]["outcome"] == "EMPTY"

        trace, timeline = _read_both(request_id)
        _assert_outcome_matches(
            trace, timeline, expected="EMPTY", request_id=request_id
        )
        assert trace["llm_usage"] == []                 # 空检索不调用 LLM
        assert timeline["llm_events"] == []
        assert len(trace["rag_executions"]) == 1        # 观测仍持久化
        assert len(timeline["rag_events"]) == 1

    def test_tool_success(self, consistency_db) -> None:
        status, response = _chat(_TOOL_QUESTION, consistency_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]

        trace, timeline = _read_both(request_id)
        _assert_outcome_matches(
            trace, timeline, expected="SUCCESS", request_id=request_id
        )
        assert len(trace["tool_executions"]) == len(timeline["tool_events"]) == 1
        assert trace["tool_executions"][0]["success"] is True
        assert timeline["tool_events"][0]["status"] == "success"

    def test_tool_failure(self, consistency_db, monkeypatch) -> None:
        """HTTP 200 + tool_success=false → FAILED（**不**因 200 判 SUCCESS）。"""
        monkeypatch.setitem(
            root._TOOL_REGISTRY._handlers, "get_inventory", _BoomHandler()
        )
        status, response = _chat(_TOOL_QUESTION, consistency_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]
        assert response.json()["metadata"]["tool_success"] is False

        trace, timeline = _read_both(request_id)
        _assert_outcome_matches(
            trace, timeline, expected="FAILED", request_id=request_id
        )
        assert trace["tool_executions"][0]["success"] is False
        assert timeline["tool_events"][0]["status"] == "failed"

    def test_t2sql_retry_success(self, consistency_db, monkeypatch) -> None:
        _install_t2sql_route(monkeypatch, _retry_client())

        status, response = _chat(_T2SQL_QUESTION, consistency_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]

        trace, timeline = _read_both(request_id)
        _assert_outcome_matches(
            trace, timeline, expected="SUCCESS", request_id=request_id
        )
        assert len(trace["llm_usage"]) == len(timeline["llm_events"]) == 2
        # **不**新增 attempt 字段
        for event in timeline["llm_events"]:
            assert "attempt" not in event
        assert "attempt" not in trace["llm_usage"][0]

    def test_t2sql_execution_failure(self, consistency_db, monkeypatch) -> None:
        _install_t2sql_route(monkeypatch, _retry_client())
        monkeypatch.setattr(
            root._default_orchestrator, "_sql_executor", _BoomExecutor()
        )
        before = _outcome_ids()

        status, _ = _chat(_T2SQL_QUESTION, consistency_db)
        assert status == 500
        request_id = _failed_request_id(before, set())
        consistency_db["request_ids"].append(request_id)

        trace, timeline = _read_both(request_id)
        _assert_outcome_matches(
            trace, timeline, expected="FAILED", request_id=request_id
        )

    def test_refusal(self, consistency_db, monkeypatch) -> None:
        _install_t2sql_route(monkeypatch, _refusal_client())

        status, response = _chat(_T2SQL_QUESTION, consistency_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]

        trace, timeline = _read_both(request_id)
        _assert_outcome_matches(
            trace, timeline, expected="REFUSED", request_id=request_id
        )
        assert len(trace["llm_usage"]) == len(timeline["llm_events"]) == 1
        assert trace["tool_executions"] == [] and timeline["tool_events"] == []
        assert trace["rag_executions"] == [] and timeline["rag_events"] == []
        # Validator / Executor 未执行
        assert _ids_of("tool_execution_record", request_id) == []

    def test_rag_upstream_failure(self, consistency_db, monkeypatch) -> None:
        from backend.app.services.rag_observability_runtime import (
            get_observed_rag_service,
        )

        monkeypatch.setattr(
            get_observed_rag_service(),
            "_vector_search_service",
            _BoomVectorSearch(),
        )
        before = _outcome_ids()

        status, _ = _chat(_RAG_QUESTION, consistency_db)
        assert status == 500
        request_id = _failed_request_id(before, set())
        consistency_db["request_ids"].append(request_id)

        trace, timeline = _read_both(request_id)
        _assert_outcome_matches(
            trace, timeline, expected="FAILED", request_id=request_id
        )
        # 保留当前真实 RAG 观测语义（不因本阶段改动）
        assert len(trace["rag_executions"]) == len(_ids_of(
            "rag_execution_record", request_id
        ))
        assert len(timeline["rag_events"]) == len(_ids_of(
            "rag_execution_record", request_id
        ))

    def test_historical_missing_outcome(self, consistency_db) -> None:
        status, response = _chat(_RAG_QUESTION, consistency_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]

        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM ai_ops.assistant_outcome_record "
                    "WHERE assistant_request_id = :rid"
                ),
                {"rid": request_id},
            )

        trace, timeline = _read_both(request_id)
        assert trace["outcome"] is None
        assert timeline["outcome_event"] is None
        # 其它段仍存在（不因缺 outcome 而消失 / 不推断 SUCCESS）
        assert len(trace["llm_usage"]) == len(timeline["llm_events"]) == 1


# ============================================================
# 一致性 / 身份 / 顺序 / 安全 / 契约
# ============================================================

@requires_db
class TestConsistencyContract:
    def test_cross_request_consistency(self, consistency_db, monkeypatch) -> None:
        """A=RAG · B=Tool · C=Refusal · D=T2SQL → 各自 Trace==Timeline，互不污染。"""
        status_a, rag_response = _chat(_RAG_QUESTION, consistency_db)
        status_b, tool_response = _chat(_TOOL_QUESTION, consistency_db)
        assert (status_a, status_b) == (200, 200)
        request_a = rag_response.json()["metadata"]["request_id"]
        request_b = tool_response.json()["metadata"]["request_id"]

        _install_t2sql_route(monkeypatch, _refusal_client())
        status_c, refuse_response = _chat(_T2SQL_QUESTION, consistency_db)
        assert status_c == 200
        request_c = refuse_response.json()["metadata"]["request_id"]

        _install_t2sql_route(monkeypatch, _retry_client())
        status_d, sql_response = _chat(_T2SQL_QUESTION, consistency_db)
        assert status_d == 200
        request_d = sql_response.json()["metadata"]["request_id"]

        expectations = {
            request_a: "SUCCESS",
            request_b: "SUCCESS",
            request_c: "REFUSED",
            request_d: "SUCCESS",
        }
        assert len(set(expectations)) == 4                 # 4 个互异 request
        for request_id, expected in expectations.items():
            trace, timeline = _read_both(request_id)
            _assert_outcome_matches(
                trace, timeline, expected=expected, request_id=request_id
            )
            assert trace["assistant_request_id"] == request_id
            assert timeline["assistant_request_id"] == request_id
            # 不得串线：其它 request_id 不出现在任一响应
            blob = str(trace) + str(timeline)
            for other in expectations:
                if other != request_id:
                    assert other not in blob

    def test_count_consistency(self, consistency_db) -> None:
        status, rag_response = _chat(_RAG_QUESTION, consistency_db)
        assert status == 200
        _chat(_TOOL_QUESTION, consistency_db)
        request_id = rag_response.json()["metadata"]["request_id"]

        trace, timeline = _read_both(request_id)
        assert len(trace["llm_usage"]) == len(timeline["llm_events"])
        assert len(trace["tool_executions"]) == len(timeline["tool_events"])
        assert len(trace["rag_executions"]) == len(timeline["rag_events"])
        assert (trace["outcome"] is not None) == (
            timeline["outcome_event"] is not None
        )

    def test_source_id_consistency(self, consistency_db) -> None:
        status, response = _chat(_RAG_QUESTION, consistency_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]

        trace, timeline = _read_both(request_id)

        # LLM：Trace 暴露主键 → 与 Timeline source_id **逐一相等**
        assert [row["id"] for row in trace["llm_usage"]] == [
            event["source_id"] for event in timeline["llm_events"]
        ]
        assert [row["id"] for row in trace["llm_usage"]] == _llm_ids(request_id)

        # Tool / RAG：Trace **不**暴露主键（Step 48 决定）→ 只校验 Timeline 侧
        assert "id" not in trace["rag_executions"][0]
        assert [
            event["source_id"] for event in timeline["rag_events"]
        ] == _ids_of("rag_execution_record", request_id)

        # Outcome：source_id == assistant_outcome_record.id
        outcome = _outcome(request_id)
        assert outcome is not None
        assert timeline["outcome_event"]["source_id"] == outcome[0]
        # Trace 侧**不**暴露 source_id（§十五）
        assert "source_id" not in trace

    def test_ordering_consistency(self, consistency_db, monkeypatch) -> None:
        _install_t2sql_route(monkeypatch, _retry_client())
        status, response = _chat(_T2SQL_QUESTION, consistency_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]

        trace, timeline = _read_both(request_id)

        # LLM：两个视图顺序一致（created_at, id）
        assert [row["id"] for row in trace["llm_usage"]] == [
            event["source_id"] for event in timeline["llm_events"]
        ] == _llm_ids(request_id)

        # Tool / RAG：顺序一致（Trace 无主键 → 按内容逐项比对）
        status_tool, tool_response = _chat(_TOOL_QUESTION, consistency_db)
        assert status_tool == 200
        tool_id = tool_response.json()["metadata"]["request_id"]
        trace_tool, timeline_tool = _read_both(tool_id)

        assert len(trace_tool["tool_executions"]) == len(
            timeline_tool["tool_events"]
        )
        assert [
            row["started_at"] for row in trace_tool["tool_executions"]
        ] == [event["started_at"] for event in timeline_tool["tool_events"]]
        assert [row["success"] for row in trace_tool["tool_executions"]] == [
            event["status"] == "success"
            for event in timeline_tool["tool_events"]
        ]

        # RAG 段（同一 request 的 RAG 视图对比：本例 LLM-only 请求无 RAG）
        assert len(trace["rag_executions"]) == len(timeline["rag_events"])
        assert [
            row["started_at"] for row in trace["rag_executions"]
        ] == [event["started_at"] for event in timeline["rag_events"]]

        # 不要求跨组全局排序
        assert "sequence" not in timeline
        assert "events" not in timeline

    def test_security_both_views(self, consistency_db) -> None:
        status, response = _chat(_RAG_QUESTION, consistency_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]

        with TestClient(app) as http:
            trace_text = http.get(f"{_TRACE}/{request_id}").text
            timeline_text = http.get(f"{_TIMELINE}/{request_id}").text

        for blob in (trace_text, timeline_text):
            # ① 字段**名**级检查（用 JSON key 形式，避免 prompt_tokens /
            #    result_count 这类**合法**观测字段被子串误判）
            for name in (
                "prompt", "messages", "system_prompt", "user_prompt", "sql",
                "query", "content", "embedding", "similarity", "arguments",
                "tool_result", "raw_response", "exception", "database_url",
                "result", "answer",
            ):
                assert f'"{name}":' not in blob, name
            # ② 敏感值 / 哨兵（子串）
            for forbidden in (
                "api_key", "authorization", "password", "postgresql://",
                "sk-", "Bearer", "traceback", _SENTINEL, "STEP71-CHUNK",
                _GOOD_SQL, "DROP TABLE",
            ):
                assert forbidden not in blob, forbidden

    def test_error_responses_leak_nothing(self, consistency_db, monkeypatch) -> None:
        """失败请求的错误响应不含内部异常（Trace / Timeline 均不暴露）。"""
        from backend.app.services.rag_observability_runtime import (
            get_observed_rag_service,
        )

        monkeypatch.setattr(
            get_observed_rag_service(),
            "_vector_search_service",
            _BoomVectorSearch(),
        )
        before = _outcome_ids()
        status, response = _chat(_RAG_QUESTION, consistency_db)
        assert status == 500
        assert set(response.json()) == {"detail"}
        assert _SENTINEL not in response.text
        request_id = _failed_request_id(before, set())
        consistency_db["request_ids"].append(request_id)

    def test_api_contract_lock(self) -> None:
        spec = app.openapi()
        trace_schema = spec["components"]["schemas"][
            "AssistantTraceResponse"
        ]["properties"]
        timeline_schema = spec["components"]["schemas"][
            "AssistantTimelineResponse"
        ]["properties"]
        event_schema = spec["components"]["schemas"][
            "AssistantTimelineEventResponse"
        ]["properties"]

        assert list(trace_schema) == [
            "assistant_request_id",
            "outcome",
            "llm_usage",
            "tool_executions",
            "rag_executions",
        ]
        assert list(timeline_schema) == [
            "assistant_request_id",
            "llm_events",
            "tool_events",
            "rag_events",
            "outcome_event",
        ]
        assert len(event_schema) == 9
        for forbidden in ("timeline", "events", "sequence", "event_id",
                          "trace_id", "span_id"):
            assert forbidden not in trace_schema
            assert forbidden not in timeline_schema
        assert "event_id" not in event_schema

    def test_cleanup_zero_residue(self, consistency_db) -> None:
        status, response = _chat(_TOOL_QUESTION, consistency_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]

        _delete_rows([request_id])
        assert _ids_of("tool_execution_record", request_id) == []
        assert _llm_ids(request_id) == []
        assert _outcome(request_id) is None
        assert _residue() == {"llm": 0, "tool": 0, "rag": 0, "outcome": 0}


__all__ = [
    "TestOutcomeConsistencyMatrix",
    "TestConsistencyContract",
]
