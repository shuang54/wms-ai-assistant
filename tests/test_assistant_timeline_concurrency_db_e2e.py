"""Assistant Timeline **并发隔离与一致性 Audit**（Phase 3.12 Step 70）。

目标（只验证，不改生产）：

    多个 Assistant Request **并发**产生 LLM / Tool / RAG / Outcome 数据时，
    Timeline 是否保持 request-level 隔离、一致性与完整性。

并发模型（Step 70 §四 —— **真并发**，不是串行循环）：

    httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
        + asyncio.gather(...)            （单进程 · 单事件循环 · 真实 ASGI 调用）
        + 真实 PostgreSQL（ai_ops 四张表）+ 真实 persistence + 真实 API

    Fake LLM 只在传输层（MockTransport）；**不调用** DeepSeek / 任何外部网络。
    每个请求的 provider request_id **唯一**（从 question 中的 ``step70-<TOKEN>``
    派生），避免 LLM usage 幂等键（UNIQUE partial index）相互覆盖。

规模：5 并发 + 10 并发（**不是**压力测试 / 基准测试）。

本阶段禁止修改生产代码与任何 persistence / API / DB contract（§三/§二十五）；
如发现真实 bug → **停止并报告**（不顺手修复）。
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
from typing import Any

import httpx
import pytest
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

_TIMELINE = "/api/observability/assistant-timeline"
_PREFIX = "step70-"
_BAD_SQL = "DROP TABLE x"
_SENTINEL_INTERNAL = "STEP70-INTERNAL-SECRET"
_TOKEN_RE = re.compile(r"step70-([A-Za-z0-9]+)")


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


# ============================================================
# DB helpers（只读 / 定向清理 / 残留）
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


def _ids_of(table: str, column: str, request_id: str) -> list[int]:
    with get_engine().connect() as conn:
        return list(
            conn.execute(
                text(
                    f"SELECT id FROM ai_ops.{table} WHERE {column} = :rid "
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
                "provider": "step70-provider",
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
                {"provider": "step70-provider", "prefix": f"{_PREFIX}%"},
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
# 并发 HTTP（ASGI + asyncio.gather）
# ============================================================

async def _post_chats(questions: list[str]) -> list[tuple[int, Any]]:
    """**并发** POST /api/ai/chat（真实 ASGI app；Fake LLM 传输层）。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://step70.test"
    ) as client:
        responses = await asyncio.gather(
            *[
                client.post("/api/ai/chat", json={"question": question})
                for question in questions
            ]
        )
    return [
        (response.status_code, response.json()) for response in responses
    ]


async def _get_timelines(request_ids: list[str]) -> list[dict[str, Any]]:
    """**并发** GET assistant-timeline/{id}。"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://step70.test"
    ) as client:
        responses = await asyncio.gather(
            *[
                client.get(f"{_TIMELINE}/{request_id}")
                for request_id in request_ids
            ]
        )
    for response in responses:
        assert response.status_code == 200, response.text
    return [response.json() for response in responses]


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _token_of(body: str) -> str:
    match = _TOKEN_RE.search(body)
    return match.group(1) if match else "unknown"


# ============================================================
# Fixture（真实 app / 真实 PG；Fake 传输层）
# ============================================================

class _FakeVectorSearch:
    """检索替身；question 含 ``step70-BAD`` → 抛错（用于失败隔离用例）。"""

    async def search(self, query: str, *, top_k: int):  # noqa: ANN201
        if "step70-BAD" in query:
            raise RuntimeError(_SENTINEL_INTERNAL)
        from backend.app.services.vector_search_service import (
            VectorSearchResult,
        )

        token = _TOKEN_RE.search(query or "")
        return [
            VectorSearchResult(
                chunk_id=901,
                document_id=90,
                chunk_index=0,
                content=f"STEP70-CHUNK-{token.group(1) if token else 'x'}",
                distance=0.1,
                similarity=0.9,
                metadata={"heading": "step70"},
            )
        ]


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


def _rag_llm_client() -> OpenAICompatibleClient:
    """RAG LLM：每次调用一个**全局唯一** provider request_id。

    为什么必须唯一：``llm_usage_record.request_id`` 上有 UNIQUE partial index
    （ON CONFLICT DO NOTHING）——并发请求若共享 provider request_id，
    只有第一条能落库，会造成 count 不一致（这不是生产 bug，是测试替身约束）。
    """
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            200,
            json=_response_body(
                f"{_PREFIX}rag-llm-{counter['n']}", "已收到问题"
            ),
        )

    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://step70.fake/v1",
        model="step70-model",
        provider="step70-provider",
        transport=httpx.MockTransport(handler),
        accounting_sink=DatabaseLLMAccountingSink(),
    )


def _retry_llm_client() -> OpenAICompatibleClient:
    """Text-to-SQL：attempt1 → 非法 SQL；attempt2 → 合法 SQL。

    "是否为重试"由 **prompt 内容**判定（重试 prompt 携带上一次生成的
    ``_BAD_SQL``），**不依赖调用计数** —— 因此并发交错下每个请求仍能
    各自经历 attempt1(bad) → attempt2(good)；provider request_id 全局唯一。
    """
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8", "ignore")
        counter["n"] += 1
        sql = _GOOD_SQL if _BAD_SQL in body else _BAD_SQL
        return httpx.Response(
            200,
            json=_response_body(f"{_PREFIX}sql-{counter['n']}", sql),
        )

    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://step70.fake/v1",
        model="step70-model",
        provider="step70-provider",
        transport=httpx.MockTransport(handler),
        accounting_sink=DatabaseLLMAccountingSink(),
    )


def _refusal_llm_client() -> OpenAICompatibleClient:
    """常返 refusal；provider request_id 全局唯一（避免幂等键冲突）。"""
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            200,
            json=_response_body(
                f"{_PREFIX}refuse-llm-{counter['n']}", REFUSAL_MARKER
            ),
        )

    return OpenAICompatibleClient(
        api_key="test-key",
        base_url="https://step70.fake/v1",
        model="step70-model",
        provider="step70-provider",
        transport=httpx.MockTransport(handler),
        accounting_sink=DatabaseLLMAccountingSink(),
    )


@pytest.fixture()
def conc_db(monkeypatch: pytest.MonkeyPatch):
    if get_engine() is None or get_session_factory() is None:
        pytest.skip("DATABASE_URL 未配置")

    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()                 # 幂等；**不**改 schema

    state: dict[str, Any] = {"request_ids": []}

    from backend.app.services.rag_observability_runtime import (
        get_observed_rag_service,
    )

    rag = get_observed_rag_service()
    monkeypatch.setattr(rag, "_llm_client", _rag_llm_client())
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


def _rag_question(token: str) -> str:
    return f"{_RAG_QUESTION} step70-{token}"


def _tool_question(token: str) -> str:
    return f"{_TOOL_QUESTION} step70-{token}"


def _sql_question(token: str) -> str:
    return f"统计最近7天的入库单数量 step70-{token}"


def _track(state: dict[str, Any], results: list[tuple[int, Any]]) -> list[str]:
    ids = [
        payload["metadata"]["request_id"]
        for status, payload in results
        if status == 200
    ]
    state["request_ids"].extend(ids)
    return ids


def _assert_isolation(payload: dict[str, Any], request_id: str) -> None:
    """**跨请求隔离的唯一判据**：Timeline 内所有 event 的 ``assistant_request_id``。"""
    assert payload["assistant_request_id"] == request_id
    for group in ("llm_events", "tool_events", "rag_events"):
        for event in payload[group]:
            assert event["assistant_request_id"] == request_id, group
    if payload["outcome_event"] is not None:
        assert payload["outcome_event"]["assistant_request_id"] == request_id


def _source_keys(payload: dict[str, Any]) -> set[tuple[str, int]]:
    """Timeline event 的**来源身份** = ``(source, source_id)``。

    Step 73 §八（Source ID Contract）：``source_id`` 只是**对应 source table 的 PK**——
    不是 event_id / 不是全局唯一时间线 ID / 不是排序号 / 不是下标 / 不是 UUID。

    因此：

    ```text
    ("llm_usage", 1) · ("tool_execution", 1) · ("rag_execution", 1)
    ```

    是**不同**的持久化记录；只有 ``(source, source_id)`` 才能定位一条 source record
    （Phase 3.12 Step 99）。
    """
    keys: set[tuple[str, int]] = set()
    for group in ("llm_events", "tool_events", "rag_events"):
        for event in payload[group]:
            keys.add((event["source"], event["source_id"]))
    outcome = payload.get("outcome_event")
    if outcome is not None:
        keys.add((outcome["source"], outcome["source_id"]))
    return keys


def _cross_request_duplicates(
    owners: dict[tuple[str, int], set[str]],
) -> dict[tuple[str, int], set[str]]:
    """同一 ``(source, source_id)`` 出现在 **2 个以上 request** → 跨请求污染。

    反向契约（Step 99）：不同 ``source`` 之间 ``source_id`` 数值相同**不算**污染
    —— 见 :func:`_source_keys` 与 ``test_case_e`` 的反例守卫。
    """
    return {
        key: request_ids
        for key, request_ids in owners.items()
        if len(request_ids) > 1
    }


# ============================================================
# Case A ~ D + 隔离 / 一致性
# ============================================================

@requires_db
class TestTimelineConcurrency:
    def test_case_a_five_concurrent_rag(self, conc_db) -> None:
        tokens = ["A1", "A2", "A3", "A4", "A5"]
        results = _run(_post_chats([_rag_question(t) for t in tokens]))
        request_ids = _track(conc_db, results)

        assert [status for status, _ in results] == [200] * 5
        assert len(set(request_ids)) == 5

        timelines = _run(_get_timelines(request_ids))
        for request_id, payload in zip(request_ids, timelines, strict=True):
            _assert_isolation(payload, request_id)
            assert len(payload["llm_events"]) == 1
            assert len(payload["rag_events"]) == 1
            assert payload["tool_events"] == []
            assert payload["outcome_event"]["status"] == "SUCCESS"

    def test_case_b_mixed_rag_and_tool(self, conc_db) -> None:
        questions = [
            _rag_question("R1"),
            _tool_question("T1"),
            _rag_question("R2"),
            _tool_question("T2"),
            _rag_question("R3"),
        ]
        results = _run(_post_chats(questions))
        request_ids = _track(conc_db, results)
        assert len(set(request_ids)) == 5

        timelines = _run(_get_timelines(request_ids))
        by_id = dict(zip(request_ids, timelines, strict=True))
        rag_ids = [request_ids[0], request_ids[2], request_ids[4]]
        tool_ids = [request_ids[1], request_ids[3]]

        for request_id in rag_ids:
            payload = by_id[request_id]
            _assert_isolation(payload, request_id)
            assert len(payload["llm_events"]) == 1
            assert len(payload["rag_events"]) == 1
            assert payload["tool_events"] == []
            assert payload["outcome_event"]["status"] == "SUCCESS"

        for request_id in tool_ids:
            payload = by_id[request_id]
            _assert_isolation(payload, request_id)
            assert payload["llm_events"] == []
            assert payload["rag_events"] == []
            assert len(payload["tool_events"]) == 1
            assert payload["outcome_event"]["status"] == "SUCCESS"

        # 绝不存在跨类型串线：rag 请求的 tool 组为空，tool 请求的 rag 组为空
        for request_id in rag_ids:
            assert by_id[request_id]["tool_events"] == []
        for request_id in tool_ids:
            assert by_id[request_id]["rag_events"] == []

        # 同一 **source** 内 PK 不跨请求复用（PK 在各自表内唯一）。
        # Step 99：**不**要求 tool_execution.id 与 rag_execution.id 互不相同
        # （不同表的序列各自从 1 开始，数值重叠合法）。
        for group, ids in (("tool_events", tool_ids), ("rag_events", rag_ids)):
            owners: dict[int, str] = {}
            for request_id in ids:
                for event in by_id[request_id][group]:
                    previous = owners.setdefault(event["source_id"], request_id)
                    assert previous == request_id, (group, event["source_id"])

    def test_case_c_concurrent_t2sql_retry(self, conc_db, monkeypatch) -> None:
        _install_t2sql_route(monkeypatch, _retry_llm_client())

        tokens = ["S1", "S2", "S3"]
        results = _run(_post_chats([_sql_question(t) for t in tokens]))
        request_ids = _track(conc_db, results)
        assert len(set(request_ids)) == 3

        timelines = _run(_get_timelines(request_ids))
        for request_id, payload in zip(request_ids, timelines, strict=True):
            _assert_isolation(payload, request_id)
            assert len(payload["llm_events"]) == 2
            assert payload["tool_events"] == []
            assert payload["rag_events"] == []
            assert payload["outcome_event"]["status"] == "SUCCESS"
            # 组内顺序 == DB (created_at, id)
            assert [
                event["source_id"] for event in payload["llm_events"]
            ] == _llm_ids(request_id)
            # **不**声称 attempt 1 / 2
            for event in payload["llm_events"]:
                assert "attempt" not in event

    def test_case_d_concurrent_refusal(self, conc_db, monkeypatch) -> None:
        _install_t2sql_route(monkeypatch, _refusal_llm_client())

        tokens = ["F1", "F2", "F3"]
        results = _run(_post_chats([_sql_question(t) for t in tokens]))
        request_ids = _track(conc_db, results)
        assert len(set(request_ids)) == 3
        for _status, payload in results:
            assert payload["metadata"]["refused"] is True

        timelines = _run(_get_timelines(request_ids))
        for request_id, payload in zip(request_ids, timelines, strict=True):
            _assert_isolation(payload, request_id)
            assert len(payload["llm_events"]) == 1
            assert payload["tool_events"] == []
            assert payload["rag_events"] == []
            assert payload["outcome_event"]["status"] == "REFUSED"
            # Validator / Executor 未执行 → DB 无 Tool 记录
            assert _ids_of("tool_execution_record", "request_id", request_id) == []
            assert _ids_of("rag_execution_record", "request_id", request_id) == []
            assert len(_llm_ids(request_id)) == 1

        # 隔离：其它请求的 assistant_request_id 不得出现在任一 Timeline；
        # provider request_id（内部）也不出现在任何 Timeline 响应中。
        for index, request_id in enumerate(request_ids):
            for other_index, other in enumerate(timelines):
                blob = json.dumps(other, ensure_ascii=False)
                if other_index != index:
                    assert request_id not in blob
                assert f"{_PREFIX}refuse-llm" not in blob

    def test_case_e_ten_concurrent_requests(self, conc_db) -> None:
        questions = [
            _rag_question(f"R{index}") for index in range(1, 6)
        ] + [_tool_question(f"T{index}") for index in range(6, 11)]
        results = _run(_post_chats(questions))
        request_ids = _track(conc_db, results)

        assert len(set(request_ids)) == 10
        timelines = _run(_get_timelines(request_ids))

        # ① 跨请求隔离判据 = assistant_request_id（**不是** source_id 数值）
        # ② source record 身份 = (source, source_id)：同一身份不得归属两个 request
        owners: dict[tuple[str, int], set[str]] = {}
        for request_id, payload in zip(request_ids, timelines, strict=True):
            _assert_isolation(payload, request_id)
            for key in _source_keys(payload):
                owners.setdefault(key, set()).add(request_id)
        assert _cross_request_duplicates(owners) == {}

        # ③ 反例守卫（Step 99）：不同 source table 之间 source_id 数值重叠**合法**，
        #    绝不能据此判定"跨请求污染"（Step 98 的真实失败根因）。
        per_source: dict[str, set[int]] = {}
        for source, source_id in owners:
            per_source.setdefault(source, set()).add(source_id)
        assert len(per_source) >= 2          # 本次覆盖 ≥2 个 source（LLM + RAG/Tool/Outcome）
        #    合成反例：llm id=1 属 A、tool id=1 属 B ⇒ **不得**判为污染
        assert (
            _cross_request_duplicates(
                {("llm_usage", 1): {"A"}, ("tool_execution", 1): {"B"}}
            )
            == {}
        )
        #    合成正例：同一 (source, source_id) 出现在 A 与 B ⇒ 必须判为污染
        assert _cross_request_duplicates({("llm_usage", 1): {"A", "B"}}) == {
            ("llm_usage", 1): {"A", "B"}
        }

    def test_cross_request_isolation(self, conc_db) -> None:
        results = _run(
            _post_chats([_rag_question(f"C{i}") for i in range(1, 6)])
        )
        request_ids = _track(conc_db, results)
        timelines = _run(_get_timelines(request_ids))

        for request_id, payload in zip(request_ids, timelines, strict=True):
            _assert_isolation(payload, request_id)
            own_llm = set(_llm_ids(request_id))
            own_rag = set(_ids_of("rag_execution_record", "request_id", request_id))
            own_outcome = {_outcome(request_id)[0]}
            for event in payload["llm_events"]:
                assert event["source_id"] in own_llm
            for event in payload["rag_events"]:
                assert event["source_id"] in own_rag
            assert payload["outcome_event"]["source_id"] in own_outcome
            # 其它请求的 PK 不得出现在本 Timeline
            for other in request_ids:
                if other == request_id:
                    continue
                assert other not in json.dumps(payload, ensure_ascii=False)

    def test_db_level_isolation(self, conc_db) -> None:
        results = _run(
            _post_chats(
                [_rag_question("D1"), _tool_question("D2"), _rag_question("D3")]
            )
        )
        request_ids = _track(conc_db, results)

        llm_sets: list[set[int]] = []
        rag_sets: list[set[int]] = []
        tool_sets: list[set[int]] = []
        outcome_sets: list[set[int]] = []
        for request_id in request_ids:
            llm_sets.append(set(_llm_ids(request_id)))
            rag_sets.append(
                set(_ids_of("rag_execution_record", "request_id", request_id))
            )
            tool_sets.append(
                set(_ids_of("tool_execution_record", "request_id", request_id))
            )
            outcome = _outcome(request_id)
            outcome_sets.append({outcome[0]} if outcome else set())

        for index_a in range(len(request_ids)):
            for index_b in range(index_a + 1, len(request_ids)):
                assert llm_sets[index_a].isdisjoint(llm_sets[index_b])
                assert rag_sets[index_a].isdisjoint(rag_sets[index_b])
                assert tool_sets[index_a].isdisjoint(tool_sets[index_b])
                assert outcome_sets[index_a].isdisjoint(outcome_sets[index_b])

    def test_count_consistency(self, conc_db) -> None:
        results = _run(
            _post_chats(
                [_rag_question("N1"), _tool_question("N2"), _rag_question("N3")]
            )
        )
        request_ids = _track(conc_db, results)
        timelines = _run(_get_timelines(request_ids))

        for request_id, payload in zip(request_ids, timelines, strict=True):
            assert len(payload["llm_events"]) == len(_llm_ids(request_id))
            assert len(payload["rag_events"]) == len(
                _ids_of("rag_execution_record", "request_id", request_id)
            )
            assert len(payload["tool_events"]) == len(
                _ids_of("tool_execution_record", "request_id", request_id)
            )
            outcome = _outcome(request_id)
            assert (payload["outcome_event"] is None) == (outcome is None)
            if outcome is not None:
                assert payload["outcome_event"]["status"] == outcome[1]

    def test_read_consistency_and_no_mutation(self, conc_db) -> None:
        results = _run(
            _post_chats([_rag_question("P1"), _rag_question("P2")])
        )
        request_ids = _track(conc_db, results)
        first, second = request_ids

        timeline_1 = _run(_get_timelines([first]))[0]
        timeline_2 = _run(_get_timelines([first]))[0]
        assert timeline_1 == timeline_2                  # 重复读取一致

        snapshot = copy.deepcopy(timeline_1)
        other = _run(_get_timelines([second]))[0]
        timeline_1_again = _run(_get_timelines([first]))[0]

        assert timeline_1_again == snapshot              # 读 B 不改 A
        assert other["assistant_request_id"] == second
        assert timeline_1["assistant_request_id"] == first

    def test_source_id_isolation(self, conc_db) -> None:
        results = _run(
            _post_chats([_rag_question("S1"), _tool_question("S2")])
        )
        request_ids = _track(conc_db, results)
        timelines = _run(_get_timelines(request_ids))

        for request_id, payload in zip(request_ids, timelines, strict=True):
            own = set(_llm_ids(request_id))
            own |= set(_ids_of("tool_execution_record", "request_id", request_id))
            own |= set(_ids_of("rag_execution_record", "request_id", request_id))
            outcome = _outcome(request_id)
            if outcome:
                own.add(outcome[0])
            events = [
                *payload["llm_events"],
                *payload["tool_events"],
                *payload["rag_events"],
                payload["outcome_event"],
            ]
            assert events[-1] is not None
            for event in events:
                assert isinstance(event["source_id"], int)   # 非 uuid / 下标 / 序列
                assert event["source_id"] in own

    def test_ordering_within_group_under_concurrency(
        self, conc_db, monkeypatch,
    ) -> None:
        _install_t2sql_route(monkeypatch, _retry_llm_client())
        results = _run(_post_chats([_sql_question("O1"), _sql_question("O2")]))
        request_ids = _track(conc_db, results)
        timelines = _run(_get_timelines(request_ids))

        for request_id, payload in zip(request_ids, timelines, strict=True):
            db_order = _llm_ids(request_id)
            assert len(db_order) == 2
            assert [
                event["source_id"] for event in payload["llm_events"]
            ] == db_order
            assert payload["llm_events"][0]["created_at"] <= (
                payload["llm_events"][1]["created_at"]
            )
        # 不要求跨请求的全局顺序（无 sequence contract）

    def test_security_regression(self, conc_db) -> None:
        results = _run(
            _post_chats([_rag_question("X1"), _tool_question("X2")])
        )
        request_ids = _track(conc_db, results)
        timelines = _run(_get_timelines(request_ids))

        for index, payload in enumerate(timelines):
            blob = json.dumps(payload, ensure_ascii=False)
            for forbidden in (
                "prompt", "messages", "system_prompt", "user_prompt", "sql",
                "query", "content", "embedding", "similarity", "arguments",
                "tool_result", "api_key", "authorization", "password",
                "database_url", "postgresql://", "sk-", "Bearer",
                "raw_response", "traceback", "exception",
                _SENTINEL_INTERNAL, _BAD_SQL, "STEP70-CHUNK",
            ):
                assert forbidden not in blob, forbidden
            # 本请求身份（sentinel = assistant_request_id）不得出现在其它 Timeline
            own_id = payload["assistant_request_id"]
            for other_index, other in enumerate(timelines):
                if other_index == index:
                    continue
                assert own_id not in json.dumps(other, ensure_ascii=False)

    def test_failure_isolation(self, conc_db) -> None:
        """A=RAG 成功 · B=RAG 上游失败 · C=Tool 成功（并发）→ 互不污染。"""
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

        before = _outcome_ids()          # 失败请求没有 HTTP request_id → 用差集定位

        questions = [
            _rag_question("GOOD1"),
            _rag_question("BAD"),
            _tool_question("GOOD2"),
        ]
        results = _run(_post_chats(questions))
        assert [status for status, _ in results] == [200, 500, 200]
        request_a = results[0][1]["metadata"]["request_id"]
        request_c = results[2][1]["metadata"]["request_id"]
        conc_db["request_ids"].extend([request_a, request_c])

        # 差集里去掉两个成功请求 → 剩下的 1 条即失败请求 B 的终态
        new_ids = sorted(_outcome_ids() - before - {request_a, request_c})
        assert len(new_ids) == 1
        request_b = new_ids[0]
        conc_db["request_ids"].append(request_b)

        payloads = _run(_get_timelines([request_a, request_b, request_c]))
        payload_a, payload_b, payload_c = payloads

        # A / C 不受影响
        assert payload_a["outcome_event"]["status"] == "SUCCESS"
        assert payload_c["outcome_event"]["status"] == "SUCCESS"
        assert len(payload_a["rag_events"]) == 1
        assert len(payload_c["tool_events"]) == 1
        # B 为 FAILED 语义，且不污染 A / C
        assert payload_b["outcome_event"]["status"] == "FAILED"
        _assert_isolation(payload_b, request_b)
        assert request_b not in json.dumps(payload_a, ensure_ascii=False)
        assert request_b not in json.dumps(payload_c, ensure_ascii=False)

    def test_cleanup_zero_residue(self, conc_db) -> None:
        results = _run(_post_chats([_rag_question("Z1"), _tool_question("Z2")]))
        request_ids = _track(conc_db, results)
        assert len(request_ids) == 2

        _delete_rows(request_ids)
        for request_id in request_ids:
            assert _llm_ids(request_id) == []
            assert _ids_of("tool_execution_record", "request_id", request_id) == []
            assert _ids_of("rag_execution_record", "request_id", request_id) == []
            assert _outcome(request_id) is None
        assert _residue() == {"llm": 0, "tool": 0, "rag": 0, "outcome": 0}


__all__ = ["TestTimelineConcurrency"]
