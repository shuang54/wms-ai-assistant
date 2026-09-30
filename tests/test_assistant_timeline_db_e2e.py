"""Assistant Timeline **PostgreSQL E2E**（Phase 3.12 Step 69）。

真实链路（唯一的测试路径）：

    POST /api/ai/chat（Fake LLM：MockTransport；**不调用** DeepSeek）
        ↓ 真实 persistence（LLM usage / Tool / RAG / Outcome）
    真实 PostgreSQL（ai_ops 四张表）
        ↓ 真实 AssistantTimelineQueryService（真实 Repository / 读边界）
    GET /api/observability/assistant-timeline/{assistant_request_id}
        ↓ 真实 API DTO
    JSON 断言

本阶段**不**改任何生产逻辑（Step 69 §四/§五）：

    不改 Timeline DTO / QueryService / API；不改 DB schema / migration / index；
    不改 AI Core / RAG / Tool / T2SQL / Validator / Executor。
    如发现 bug → 先停止并报告（§二十五）。

数据策略：测试专用 provider request_id 前缀 ``step69-``；Assistant request_id 由
Orchestrator 生成（UUID，按 id 精确清理）；**不写生产数据**。
清理：fixture teardown 定向 DELETE（四张表）+ 清空 Runtime Collector +
``tests/conftest.py`` 会话级水位守卫（Outcome 表）⇒ **Step 69 residue = 0**。
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
    _install_t2sql_route,
    _retry_t2sql_client,
)

_TIMELINE = "/api/observability/assistant-timeline"
_TRACE = "/api/observability/assistant-trace"
_PREFIX = "step69-"
_SENTINEL = "STEP69-INTERNAL-SECRET"
_T2SQL_QUESTION = "统计最近7天的入库单数量"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


# ============================================================
# DB helpers（只读查询 / 定向清理 / 残留检查）
# ============================================================

def _llm_rows(request_id: str) -> list[tuple[int, Any]]:
    with get_engine().connect() as conn:
        return [
            (row.id, row.created_at)
            for row in conn.execute(
                text(
                    "SELECT id, created_at FROM ai_ops.llm_usage_record "
                    "WHERE assistant_request_id = :rid ORDER BY created_at, id"
                ),
                {"rid": request_id},
            )
        ]


def _tool_rows(request_id: str) -> list[int]:
    with get_engine().connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT id FROM ai_ops.tool_execution_record "
                    "WHERE request_id = :rid ORDER BY id"
                ),
                {"rid": request_id},
            ).scalars()
        )


def _rag_rows(request_id: str) -> list[int]:
    with get_engine().connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT id FROM ai_ops.rag_execution_record "
                    "WHERE request_id = :rid ORDER BY id"
                ),
                {"rid": request_id},
            ).scalars()
        )


def _outcome_row(request_id: str) -> tuple[int, str] | None:
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
                "OR request_id LIKE :prefix "
                "OR provider = :provider"
            ),
            {
                "ids": ids,
                "prefix": f"{_PREFIX}%",
                "provider": "step69-provider",
            },
        )
        conn.execute(
            text(
                "DELETE FROM ai_ops.tool_execution_record "
                "WHERE request_id = ANY(:ids)"
            ),
            {"ids": ids},
        )
        conn.execute(
            text(
                "DELETE FROM ai_ops.rag_execution_record "
                "WHERE request_id = ANY(:ids)"
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


def _step69_residue() -> dict[str, int]:
    with get_engine().connect() as conn:
        return {
            "llm": conn.execute(
                text(
                    "SELECT COUNT(*) FROM ai_ops.llm_usage_record "
                    "WHERE provider = :provider OR request_id LIKE :prefix"
                ),
                {"provider": "step69-provider", "prefix": f"{_PREFIX}%"},
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
# Fixture：真实 app + Fake LLM + 真实 PostgreSQL
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
                content="STEP69-CHUNK",
                distance=0.1,
                similarity=0.9,
                metadata={"heading": "step69"},
            )
        ]


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
            total_chars=32,
            truncated=False,
            dropped_count=0,
        )


class _RecordingHandler:
    async def __call__(self, arguments: Any):  # noqa: ANN401
        return {"material_code": arguments.get("material_code"), "qty": 5}


def _rag_client(
    provider_request_id: str,
    *,
    content: str = "已收到问题",
) -> OpenAICompatibleClient:
    """真实 LLM Client + **真实 DB sink**（MockTransport；零网络）。"""
    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://step69.fake/v1",
        model="step69-model",
        provider="step69-provider",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=_response_body(provider_request_id, content)
            )
        ),
        accounting_sink=DatabaseLLMAccountingSink(),
    )


@pytest.fixture()
def timeline_db(monkeypatch: pytest.MonkeyPatch):
    """真实 app / 真实 persistence / 真实 PostgreSQL；Fake LLM 传输。"""
    if get_engine() is None or get_session_factory() is None:
        pytest.skip("DATABASE_URL 未配置")

    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()            # 幂等；本阶段**不**新增表结构

    state: dict[str, Any] = {"request_ids": []}

    # ① RAG：真实 observed RagService + 假检索 + 假 LLM（真实 DB sink）
    from backend.app.services.rag_observability_runtime import (
        get_observed_rag_service,
    )

    rag = get_observed_rag_service()
    monkeypatch.setattr(rag, "_llm_client", _rag_client(f"{_PREFIX}rag-llm-1"))
    monkeypatch.setattr(rag, "_vector_search_service", _FakeVectorSearch())
    monkeypatch.setattr(rag, "_context_builder", _EchoContextBuilder())

    # ② TOOL：真实执行边界 + 不调用 LLM 的 handler
    monkeypatch.setitem(
        root._TOOL_REGISTRY._handlers, "get_inventory", _RecordingHandler()
    )

    yield state

    # ---- cleanup ----
    root._TOOL_EXECUTION_COLLECTOR.clear()
    from backend.app.services.rag_observability_runtime import (
        get_rag_execution_collector,
    )

    get_rag_execution_collector().clear()
    _delete_rows(list(state["request_ids"]))
    residue = _step69_residue()
    assert residue == {"llm": 0, "tool": 0, "rag": 0, "outcome": 0}, residue


def _chat(question: str, state: dict[str, Any]) -> tuple[int, Any]:
    """POST /api/ai/chat（真实 app）；成功时把 request_id 记入清理清单。"""
    with TestClient(app) as http:
        response = http.post("/api/ai/chat", json={"question": question})
    if response.status_code == 200:
        state["request_ids"].append(response.json()["metadata"]["request_id"])
    return response.status_code, response


def _timeline(request_id: str) -> Any:
    with TestClient(app) as http:
        return http.get(f"{_TIMELINE}/{request_id}")


# ============================================================
# Case A ~ H
# ============================================================

@requires_db
class TestTimelineDbE2E:
    def test_case_a_rag_complete(self, timeline_db) -> None:
        status, response = _chat(_RAG_QUESTION, timeline_db)
        assert status == 200, response.text
        request_id = response.json()["metadata"]["request_id"]

        payload = _timeline(request_id).json()

        assert payload["assistant_request_id"] == request_id
        assert len(payload["llm_events"]) == 1
        assert len(payload["rag_events"]) == 1
        assert payload["tool_events"] == []
        assert payload["outcome_event"] is not None
        assert payload["outcome_event"]["status"] == "SUCCESS"

        # source_id = 真实 DB 主键
        assert [event["source_id"] for event in payload["llm_events"]] == [
            row[0] for row in _llm_rows(request_id)
        ]
        assert [event["source_id"] for event in payload["rag_events"]] == (
            _rag_rows(request_id)
        )
        assert payload["outcome_event"]["source_id"] == _outcome_row(
            request_id
        )[0]

        # 时间语义：LLM / Outcome 只有 created_at；RAG 只有 App 钟三件套
        llm_event = payload["llm_events"][0]
        assert llm_event["created_at"] is not None
        assert llm_event["started_at"] is None
        assert llm_event["finished_at"] is None
        assert llm_event["duration_ms"] is None

        rag_event = payload["rag_events"][0]
        assert rag_event["created_at"] is None
        assert rag_event["started_at"] is not None
        assert rag_event["finished_at"] is not None
        assert rag_event["duration_ms"] is not None

        assert payload["outcome_event"]["created_at"] is not None

    def test_case_b_tool(self, timeline_db) -> None:
        status, response = _chat(_TOOL_QUESTION, timeline_db)
        assert status == 200, response.text
        request_id = response.json()["metadata"]["request_id"]

        payload = _timeline(request_id).json()

        assert len(payload["tool_events"]) == 1
        assert payload["llm_events"] == []      # Tool 路径不调用 LLM（Step 54）
        assert payload["rag_events"] == []
        assert payload["outcome_event"]["status"] == "SUCCESS"
        assert [event["source_id"] for event in payload["tool_events"]] == (
            _tool_rows(request_id)
        )
        assert payload["tool_events"][0]["status"] == "success"
        assert payload["tool_events"][0]["created_at"] is None

    def test_case_c_t2sql_retry(self, timeline_db, monkeypatch) -> None:
        _install_t2sql_route(
            monkeypatch,
            _retry_t2sql_client(
                provider_ids=(f"{_PREFIX}t2sql-1", f"{_PREFIX}t2sql-2"),
                use_db_sink=True,
            ),
        )

        status, response = _chat(_T2SQL_QUESTION, timeline_db)
        assert status == 200, response.text
        request_id = response.json()["metadata"]["request_id"]

        payload = _timeline(request_id).json()

        assert len(payload["llm_events"]) == 2
        assert payload["tool_events"] == []
        assert payload["rag_events"] == []
        assert payload["outcome_event"]["status"] == "SUCCESS"

        # 组内顺序 = DB 的 (created_at, id) 升序
        assert [event["source_id"] for event in payload["llm_events"]] == [
            row[0] for row in _llm_rows(request_id)
        ]
        # **不**声称 attempt 1 / 2（DB 无 attempt 事实）
        for event in payload["llm_events"]:
            assert "attempt" not in event
            assert event["status"] is None

    def test_case_d_rag_failure(self, timeline_db, monkeypatch) -> None:
        """RAG 运行期失败 → 按**真实**持久化语义校验（不补造记录）。"""
        from backend.app.services.rag_observability_runtime import (
            get_observed_rag_service,
        )

        monkeypatch.setattr(
            get_observed_rag_service(),
            "_vector_search_service",
            _BoomVectorSearch(),
        )
        outcome_before = _outcome_ids()

        status, response = _chat(_RAG_QUESTION, timeline_db)
        assert status == 500
        assert set(response.json()) == {"detail"}        # error body 未变

        new_ids = sorted(_outcome_ids() - outcome_before)
        assert len(new_ids) == 1
        request_id = new_ids[0]
        timeline_db["request_ids"].append(request_id)

        payload = _timeline(request_id).json()

        # 真实事实优先：与 DB 逐段对齐（不臆造 / 不补造 RAG 行）
        assert len(payload["llm_events"]) == len(_llm_rows(request_id))
        assert len(payload["rag_events"]) == len(_rag_rows(request_id))
        assert payload["tool_events"] == []
        assert payload["outcome_event"] is not None
        assert payload["outcome_event"]["status"] == "FAILED"

    def test_case_e_refusal(self, timeline_db, monkeypatch) -> None:
        _install_t2sql_route(
            monkeypatch,
            _rag_client(f"{_PREFIX}refusal-1", content=REFUSAL_MARKER),
        )

        status, response = _chat(_T2SQL_QUESTION, timeline_db)
        assert status == 200, response.text
        request_id = response.json()["metadata"]["request_id"]
        assert response.json()["metadata"]["refused"] is True

        payload = _timeline(request_id).json()

        assert len(payload["llm_events"]) == 1
        assert payload["tool_events"] == []
        assert payload["rag_events"] == []
        assert payload["outcome_event"]["status"] == "REFUSED"
        assert _tool_rows(request_id) == []              # Executor 未执行
        assert _rag_rows(request_id) == []

    def test_case_f_historical_missing_outcome(self, timeline_db) -> None:
        """删除 Outcome 行模拟历史请求 → outcome_event = null（不推断）。"""
        status, response = _chat(_RAG_QUESTION, timeline_db)
        assert status == 200, response.text
        request_id = response.json()["metadata"]["request_id"]

        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM ai_ops.assistant_outcome_record "
                    "WHERE assistant_request_id = :rid"
                ),
                {"rid": request_id},
            )

        payload = _timeline(request_id).json()

        assert payload["outcome_event"] is None
        assert len(payload["llm_events"]) == 1
        assert len(payload["rag_events"]) == 1

    def test_case_g_cross_request_isolation(self, timeline_db) -> None:
        status_a, rag_response = _chat(_RAG_QUESTION, timeline_db)
        status_b, tool_response = _chat(_TOOL_QUESTION, timeline_db)
        assert (status_a, status_b) == (200, 200)
        request_a = rag_response.json()["metadata"]["request_id"]
        request_b = tool_response.json()["metadata"]["request_id"]

        payload_a = _timeline(request_a).json()
        payload_b = _timeline(request_b).json()

        assert payload_a["assistant_request_id"] == request_a
        assert payload_b["assistant_request_id"] == request_b
        for event in payload_a["llm_events"] + payload_a["rag_events"]:
            assert event["assistant_request_id"] == request_a
            assert event["source_id"] != payload_b["tool_events"][0]["source_id"]
        for event in payload_b["tool_events"]:
            assert event["assistant_request_id"] == request_b

        assert [event["source_id"] for event in payload_a["llm_events"]] == [
            row[0] for row in _llm_rows(request_a)
        ]
        assert [event["source_id"] for event in payload_b["tool_events"]] == (
            _tool_rows(request_b)
        )
        # 不得串线：B 无 LLM 段，A 无 Tool 段
        assert payload_b["llm_events"] == []
        assert payload_a["tool_events"] == []
        assert payload_a["outcome_event"]["status"] == "SUCCESS"
        assert payload_b["outcome_event"]["status"] == "SUCCESS"

    def test_case_h_unknown_request(self, timeline_db) -> None:
        response = _timeline(f"{_PREFIX}not-exist")

        assert response.status_code == 200
        assert response.json() == {
            "assistant_request_id": f"{_PREFIX}not-exist",
            "llm_events": [],
            "tool_events": [],
            "rag_events": [],
            "outcome_event": None,
        }


# ============================================================
# 契约 / 安全 / 清理
# ============================================================

@requires_db
class TestTimelineDbContract:
    def test_source_id_are_real_primary_keys(self, timeline_db) -> None:
        status, response = _chat(_RAG_QUESTION, timeline_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]

        payload = _timeline(request_id).json()
        db_ids = {row[0] for row in _llm_rows(request_id)} | set(
            _rag_rows(request_id)
        ) | {_outcome_row(request_id)[0]}

        events = [
            *payload["llm_events"],
            *payload["rag_events"],
            payload["outcome_event"],
        ]
        assert len(events) == 3
        for event in events:
            assert isinstance(event["source_id"], int)      # 非 uuid / 非下标
            assert event["source_id"] in db_ids

    def test_ordering_two_llm_events(self, timeline_db, monkeypatch) -> None:
        _install_t2sql_route(
            monkeypatch,
            _retry_t2sql_client(
                provider_ids=(f"{_PREFIX}order-1", f"{_PREFIX}order-2"),
                use_db_sink=True,
            ),
        )
        status, response = _chat(_T2SQL_QUESTION, timeline_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]

        payload = _timeline(request_id).json()
        db_order = [row[0] for row in _llm_rows(request_id)]

        assert len(db_order) == 2
        assert [event["source_id"] for event in payload["llm_events"]] == (
            db_order
        )
        assert payload["llm_events"][0]["created_at"] <= (
            payload["llm_events"][1]["created_at"]
        )

    def test_security_full_chain(self, timeline_db) -> None:
        status, response = _chat(_RAG_QUESTION, timeline_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]

        blob = _timeline(request_id).text

        for forbidden in (
            "prompt", "messages", "system_prompt", "user_prompt", "sql",
            "query", "content", "embedding", "similarity", "arguments",
            "tool_result", "api_key", "authorization", "password",
            "database_url", "postgresql://", "sk-", "Bearer", "raw_response",
            "traceback", "exception", _SENTINEL, "STEP69-CHUNK",
        ):
            assert forbidden not in blob, forbidden

    def test_api_contract_regression(self) -> None:
        spec = app.openapi()
        timeline_schema = spec["components"]["schemas"][
            "AssistantTimelineResponse"
        ]["properties"]
        event_schema = spec["components"]["schemas"][
            "AssistantTimelineEventResponse"
        ]["properties"]
        trace_schema = spec["components"]["schemas"][
            "AssistantTraceResponse"
        ]["properties"]

        assert list(timeline_schema) == [
            "assistant_request_id",
            "llm_events",
            "tool_events",
            "rag_events",
            "outcome_event",
        ]
        assert len(event_schema) == 9
        assert "events" not in timeline_schema
        assert "sequence" not in timeline_schema
        assert "event_id" not in event_schema
        assert list(trace_schema) == [
            "assistant_request_id",
            "outcome",
            "llm_usage",
            "tool_executions",
            "rag_executions",
        ]

    def test_trace_endpoint_still_works(self, timeline_db) -> None:
        status, response = _chat(_RAG_QUESTION, timeline_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]

        with TestClient(app) as http:
            trace = http.get(f"{_TRACE}/{request_id}")

        assert trace.status_code == 200
        assert set(trace.json()) == {
            "assistant_request_id",
            "outcome",
            "llm_usage",
            "tool_executions",
            "rag_executions",
        }

    def test_cleanup_leaves_zero_residue(self, timeline_db) -> None:
        status, response = _chat(_TOOL_QUESTION, timeline_db)
        assert status == 200
        request_id = response.json()["metadata"]["request_id"]

        assert len(_timeline(request_id).json()["tool_events"]) == 1
        assert _tool_rows(request_id) != []

        _delete_rows([request_id])
        assert _tool_rows(request_id) == []
        assert _llm_rows(request_id) == []
        assert _outcome_row(request_id) is None
        assert _step69_residue() == {"llm": 0, "tool": 0, "rag": 0, "outcome": 0}


__all__ = [
    "TestTimelineDbE2E",
    "TestTimelineDbContract",
]
