"""Trace / Timeline **写入中读取**一致性 Audit（Phase 3.12 Step 72）。

只回答：

    当一个 Assistant Request 的 LLM / Tool / RAG / Outcome 数据正在陆续写入
    PostgreSQL 时，同时读取 Trace / Timeline，是否出现
        ① 跨 request 污染  ② 不存在的 source_id  ③ 重复 source_id
        ④ 违反当前 Contract 的状态
    并且最终稳定状态是否与 Step 71 一致。

**不**证明"读取到的是完整最终快照"（当前系统没有 event_id / sequence /
transaction_id / 全局排序 / 统一 started_at）—— partial state 本身是**合法**的。

构造方式（§十九：**不改生产代码**、不加 test seam）：

    直接用**真实 Repository** 按阶段写入真实持久化记录
    （LLM usage → RAG → Tool → Outcome），每个阶段读取两个真实 Read API；
    另设一个"后台写入 + 前台轮询"用例模拟真实写入中读取。

禁止：直接读 Session 内部事务 / 改 isolation level / SELECT FOR UPDATE /
TRUNCATE / DELETE all / DROP。
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.db.assistant_outcome_repository import (
    AssistantOutcomeRepository,
)
from backend.app.db.llm_usage_repository import LLMUsageRepository
from backend.app.db.rag_execution_repository import RagExecutionRepository
from backend.app.db.session import get_engine, get_session_factory
from backend.app.db.tool_execution_repository import ToolExecutionRepository
from backend.app.main import app
from backend.app.services.rag_execution_observation import (
    RagExecutionObservation,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord

_TRACE = "/api/observability/assistant-trace"
_TIMELINE = "/api/observability/assistant-timeline"
_PREFIX = "step72-"
_SENTINEL = "STEP72-INTERNAL-SECRET"
_BASE = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


# ============================================================
# 真实 Repository 写入（按阶段构造 partial state）
# ============================================================

def _insert_llm(request_id: str, provider_request_id: str) -> int:
    created = LLMUsageRepository().create(
        request_id=provider_request_id,
        provider="step72-provider",
        model="step72-model",
        prompt_tokens=11,
        completion_tokens=22,
        total_tokens=33,
        assistant_request_id=request_id,
    )
    assert created is not None
    return created


def _insert_tool(request_id: str, *, success: bool = True) -> int:
    row = ToolExecutionRepository().create(
        ToolExecutionRecord(
            request_id=request_id,
            round=1,
            tool_name="get_inventory",
            started_at=_BASE,
            finished_at=_BASE + timedelta(milliseconds=40),
            duration_ms=40.0,
            success=success,
            project_id=None,
            tool_call_id=None,
            error_code=None,
            error_type=None,
        )
    )
    return row.id


def _insert_rag(
    request_id: str,
    *,
    result_count: int = 1,
    used_chunks_count: int = 1,
) -> int:
    row = RagExecutionRepository().create(
        RagExecutionObservation(
            request_id=request_id,
            started_at=_BASE,
            finished_at=_BASE + timedelta(milliseconds=120),
            duration_ms=120.0,
            result_count=result_count,
            used_chunks_count=used_chunks_count,
            top_k=5,
            context_truncated=False,
            context_chars=64,
            reranker_used=False,
            rerank_elapsed_ms=None,
            chunk_ids=tuple(range(1, used_chunks_count + 1)),
            document_ids=(90,),
        )
    )
    return row.id


def _insert_outcome(request_id: str, outcome: str) -> int:
    created = AssistantOutcomeRepository().create(
        assistant_request_id=request_id, outcome=outcome
    )
    assert created is not None
    return created


# ============================================================
# DB 读取（校验用）
# ============================================================

def _db_ids(request_id: str) -> dict[str, set[int]]:
    with get_engine().connect() as conn:
        llm = set(
            conn.execute(
                text(
                    "SELECT id FROM ai_ops.llm_usage_record "
                    "WHERE assistant_request_id = :rid"
                ),
                {"rid": request_id},
            ).scalars()
        )
        tool = set(
            conn.execute(
                text(
                    "SELECT id FROM ai_ops.tool_execution_record "
                    "WHERE request_id = :rid"
                ),
                {"rid": request_id},
            ).scalars()
        )
        rag = set(
            conn.execute(
                text(
                    "SELECT id FROM ai_ops.rag_execution_record "
                    "WHERE request_id = :rid"
                ),
                {"rid": request_id},
            ).scalars()
        )
        outcome = set(
            conn.execute(
                text(
                    "SELECT id FROM ai_ops.assistant_outcome_record "
                    "WHERE assistant_request_id = :rid"
                ),
                {"rid": request_id},
            ).scalars()
        )
    return {"llm": llm, "tool": tool, "rag": rag, "outcome": outcome}


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
                "provider": "step72-provider",
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
                {"provider": "step72-provider", "prefix": f"{_PREFIX}%"},
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
# 读取 + Contract 校验
# ============================================================

def _read_both(request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with TestClient(app) as http:
        trace = http.get(f"{_TRACE}/{request_id}")
        timeline = http.get(f"{_TIMELINE}/{request_id}")
    assert trace.status_code == 200, trace.text
    assert timeline.status_code == 200, timeline.text
    return trace.json(), timeline.json()


def _validate_views(
    request_id: str,
    *,
    allowed_outcomes: set[str | None],
    other_request_ids: set[str] | None = None,
    require_view_match: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """一次读取的**完整 Contract 校验**（partial state 合法）。

    Args:
        require_view_match: ``True``（默认）要求 Trace 与 Timeline 在同一稳定
            阶段读出相同数量；``False`` 用于**写入进行中**的用例 —— 两个视图
            是两次独立读取，中间可能又发生了写入，因此**不**要求相等
            （Step 72 §十五：中间发生新写入时**不**要求 equality）。
    """
    trace, timeline = _read_both(request_id)
    db = _db_ids(request_id)          # 读取之后再查库 → 允许读到的是子集

    # ① 不跨 request
    #    LLM 段的 ``request_id`` 是 **Provider** 请求 ID（Step 37 语义），
    #    其 Assistant 关联键是 ``assistant_request_id``；
    #    Tool / RAG 段的 ``request_id`` 就是 Assistant Trace ID。
    assert trace["assistant_request_id"] == request_id
    assert timeline["assistant_request_id"] == request_id
    for row in trace["llm_usage"]:
        assert row["assistant_request_id"] == request_id
    for segment in ("tool_executions", "rag_executions"):
        for row in trace[segment]:
            assert row["request_id"] == request_id, segment
    groups = {
        "llm_events": "LLM",
        "tool_events": "TOOL",
        "rag_events": "RAG",
    }
    for group, expected_type in groups.items():
        for event in timeline[group]:
            assert event["assistant_request_id"] == request_id
            assert event["event_type"] == expected_type
    blob = str(trace) + str(timeline)
    for other in other_request_ids or set():
        assert other not in blob

    # ② 数量（partial 允许：两侧都不得超过 DB 实际行数）
    for trace_segment, timeline_group, db_key in (
        ("llm_usage", "llm_events", "llm"),
        ("tool_executions", "tool_events", "tool"),
        ("rag_executions", "rag_events", "rag"),
    ):
        assert len(trace[trace_segment]) <= len(db[db_key])
        assert len(timeline[timeline_group]) <= len(db[db_key])
        if require_view_match:
            assert len(trace[trace_segment]) == len(timeline[timeline_group])

    # ③ source_id 校验：int · 存在于 DB · 组内不重复 · 非 uuid/下标
    for group, key in (
        ("llm_events", "llm"),
        ("tool_events", "tool"),
        ("rag_events", "rag"),
    ):
        source_ids = [event["source_id"] for event in timeline[group]]
        for value in source_ids:
            assert isinstance(value, int) and not isinstance(value, bool)
            assert value in db[key], (group, value, db[key])
        assert len(source_ids) == len(set(source_ids))       # 组内无重复
    if timeline["outcome_event"] is not None:
        outcome_id = timeline["outcome_event"]["source_id"]
        assert isinstance(outcome_id, int)
        assert outcome_id in db["outcome"]

    # ④ Outcome：只允许"已写入的值"或 null，**不推断**
    assert trace["outcome"] in allowed_outcomes, trace["outcome"]
    timeline_status = (
        None
        if timeline["outcome_event"] is None
        else timeline["outcome_event"]["status"]
    )
    assert timeline_status in allowed_outcomes, timeline_status
    if require_view_match:
        assert (trace["outcome"] is None) == (timeline["outcome_event"] is None)
    else:
        # 写入进行中：两次读取之间可能刚好落库 → 允许一侧先看到
        assert {trace["outcome"], timeline_status} <= allowed_outcomes

    # ⑤ 不出现 merged events / sequence / event_id
    assert "events" not in timeline
    assert "sequence" not in timeline
    for event in timeline["llm_events"] + timeline["tool_events"] + timeline["rag_events"]:
        assert "event_id" not in event and "sequence" not in event
    return trace, timeline


@pytest.fixture()
def rw_db():
    if get_engine() is None or get_session_factory() is None:
        pytest.skip("DATABASE_URL 未配置")

    from backend.app.db import init_db as init_db_module

    init_db_module.init_db()
    state: dict[str, Any] = {"request_ids": []}
    yield state
    _delete_rows(list(state["request_ids"]))
    assert _residue() == {"llm": 0, "tool": 0, "rag": 0, "outcome": 0}


# ============================================================
# Case A ~ G（分阶段 partial state）
# ============================================================

@requires_db
class TestPartialStateStages:
    def test_case_a_llm_only(self, rw_db) -> None:
        request_id = f"{_PREFIX}a"
        rw_db["request_ids"].append(request_id)
        llm_id = _insert_llm(request_id, f"{_PREFIX}a-llm")
        _validate_views(request_id, allowed_outcomes={None})

        trace, timeline = _read_both(request_id)
        assert [row["id"] for row in trace["llm_usage"]] == [llm_id]
        assert [event["source_id"] for event in timeline["llm_events"]] == [llm_id]
        assert trace["tool_executions"] == [] and timeline["tool_events"] == []
        assert trace["rag_executions"] == [] and timeline["rag_events"] == []
        # Outcome 未写入 → **不**推断 SUCCESS / FAILED / REFUSED
        assert trace["outcome"] is None and timeline["outcome_event"] is None

    def test_case_b_llm_plus_rag(self, rw_db) -> None:
        request_id = f"{_PREFIX}b"
        rw_db["request_ids"].append(request_id)
        _insert_llm(request_id, f"{_PREFIX}b-llm")
        rag_id = _insert_rag(request_id)

        trace, timeline = _validate_views(request_id, allowed_outcomes={None})
        assert len(trace["llm_usage"]) == len(timeline["llm_events"]) == 1
        assert len(trace["rag_executions"]) == len(timeline["rag_events"]) == 1
        assert [event["source_id"] for event in timeline["rag_events"]] == [rag_id]
        assert trace["rag_executions"][0]["started_at"] == (
            timeline["rag_events"][0]["started_at"]
        )
        assert trace["outcome"] is None and timeline["outcome_event"] is None

    def test_case_c_tool_only(self, rw_db) -> None:
        request_id = f"{_PREFIX}c"
        rw_db["request_ids"].append(request_id)
        tool_id = _insert_tool(request_id)

        trace, timeline = _validate_views(request_id, allowed_outcomes={None})
        assert len(trace["tool_executions"]) == len(timeline["tool_events"]) == 1
        assert [event["source_id"] for event in timeline["tool_events"]] == [tool_id]
        assert trace["tool_executions"][0]["success"] is True
        assert timeline["tool_events"][0]["status"] == "success"
        # 不因 Tool 成功推断 outcome=SUCCESS
        assert trace["outcome"] is None and timeline["outcome_event"] is None

    def test_case_d_outcome_success(self, rw_db) -> None:
        request_id = f"{_PREFIX}d"
        rw_db["request_ids"].append(request_id)
        _insert_llm(request_id, f"{_PREFIX}d-llm")
        _insert_rag(request_id)
        _insert_tool(request_id)
        outcome_id = _insert_outcome(request_id, "SUCCESS")

        trace, timeline = _validate_views(request_id, allowed_outcomes={"SUCCESS"})
        assert trace["outcome"] == "SUCCESS"
        assert timeline["outcome_event"]["status"] == "SUCCESS"
        assert timeline["outcome_event"]["source_id"] == outcome_id
        assert len(trace["llm_usage"]) == len(timeline["llm_events"]) == 1
        assert len(trace["tool_executions"]) == len(timeline["tool_events"]) == 1
        assert len(trace["rag_executions"]) == len(timeline["rag_events"]) == 1

    def test_case_e_outcome_failed(self, rw_db) -> None:
        request_id = f"{_PREFIX}e"
        rw_db["request_ids"].append(request_id)
        _insert_llm(request_id, f"{_PREFIX}e-llm")
        _insert_tool(request_id, success=False)
        outcome_id = _insert_outcome(request_id, "FAILED")

        trace, timeline = _validate_views(request_id, allowed_outcomes={"FAILED"})
        assert trace["outcome"] == "FAILED"
        assert timeline["outcome_event"]["status"] == "FAILED"
        assert timeline["outcome_event"]["source_id"] == outcome_id
        # Tool failure 与 Outcome FAILED 各自独立表达
        assert trace["tool_executions"][0]["success"] is False
        assert timeline["tool_events"][0]["status"] == "failed"

    def test_case_f_outcome_refused(self, rw_db) -> None:
        request_id = f"{_PREFIX}f"
        rw_db["request_ids"].append(request_id)
        _insert_llm(request_id, f"{_PREFIX}f-llm")
        _insert_outcome(request_id, "REFUSED")

        trace, timeline = _validate_views(request_id, allowed_outcomes={"REFUSED"})
        assert trace["outcome"] == "REFUSED"
        assert timeline["outcome_event"]["status"] == "REFUSED"
        # 不推断 SUCCESS / EMPTY
        assert trace["tool_executions"] == [] and trace["rag_executions"] == []
        assert len(trace["llm_usage"]) == len(timeline["llm_events"]) == 1

    def test_case_g_outcome_empty(self, rw_db) -> None:
        request_id = f"{_PREFIX}g"
        rw_db["request_ids"].append(request_id)
        _insert_rag(request_id, result_count=0, used_chunks_count=0)
        _insert_outcome(request_id, "EMPTY")

        trace, timeline = _validate_views(request_id, allowed_outcomes={"EMPTY"})
        assert trace["outcome"] == "EMPTY"
        assert timeline["outcome_event"]["status"] == "EMPTY"
        # 当前真实 EMPTY 语义：无 LLM 调用
        assert trace["llm_usage"] == [] and timeline["llm_events"] == []
        assert len(trace["rag_executions"]) == len(timeline["rag_events"]) == 1


# ============================================================
# 交叉 / 实时 / 一致性 / 安全 / 清理
# ============================================================

@requires_db
class TestReadDuringWriteContract:
    def test_case_h_cross_request_read_during_write(self, rw_db) -> None:
        """A/B/C 处于**不同持久化阶段**，同时读取 → 互不污染。"""
        request_a = f"{_PREFIX}h-a"      # LLM only
        request_b = f"{_PREFIX}h-b"      # LLM + RAG
        request_c = f"{_PREFIX}h-c"      # Tool + Outcome
        rw_db["request_ids"].extend([request_a, request_b, request_c])

        _insert_llm(request_a, f"{_PREFIX}h-a-llm")
        _insert_llm(request_b, f"{_PREFIX}h-b-llm")
        _insert_rag(request_b)
        _insert_tool(request_c)
        _insert_outcome(request_c, "SUCCESS")

        all_ids = {request_a, request_b, request_c}
        trace_a, timeline_a = _validate_views(
            request_a, allowed_outcomes={None}, other_request_ids=all_ids - {request_a}
        )
        trace_b, timeline_b = _validate_views(
            request_b, allowed_outcomes={None}, other_request_ids=all_ids - {request_b}
        )
        trace_c, timeline_c = _validate_views(
            request_c,
            allowed_outcomes={"SUCCESS"},
            other_request_ids=all_ids - {request_c},
        )

        # 阶段各不相同且符合预期
        assert len(trace_a["llm_usage"]) == 1 and trace_a["rag_executions"] == []
        assert len(trace_b["rag_executions"]) == 1
        assert len(trace_c["tool_executions"]) == 1
        # source_id 集合互不相交（不同 request 的 PK 不复用）
        ids_a = {event["source_id"] for event in timeline_a["llm_events"]}
        ids_b = {event["source_id"] for event in timeline_b["llm_events"]}
        ids_c = {event["source_id"] for event in timeline_c["tool_events"]}
        assert ids_a.isdisjoint(ids_b) and ids_a.isdisjoint(ids_c)

    def test_read_consistency_stable_phase(self, rw_db) -> None:
        request_id = f"{_PREFIX}stable"
        rw_db["request_ids"].append(request_id)
        _insert_llm(request_id, f"{_PREFIX}stable-llm")
        _insert_rag(request_id)
        _insert_outcome(request_id, "SUCCESS")

        trace_1, timeline_1 = _read_both(request_id)
        trace_2, timeline_2 = _read_both(request_id)
        assert trace_1 == trace_2
        assert timeline_1 == timeline_2

    def test_no_fake_completeness(self, rw_db) -> None:
        """partial state 合法：HTTP 200 但 outcome 可以为 null（不要求完整）。"""
        request_id = f"{_PREFIX}partial"
        rw_db["request_ids"].append(request_id)
        _insert_llm(request_id, f"{_PREFIX}partial-llm")

        with TestClient(app) as http:
            trace = http.get(f"{_TRACE}/{request_id}")
            timeline = http.get(f"{_TIMELINE}/{request_id}")

        assert trace.status_code == 200
        assert timeline.status_code == 200
        assert trace.json()["outcome"] is None
        assert timeline.json()["outcome_event"] is None
        assert len(trace.json()["llm_usage"]) == 1

    def test_live_read_during_write(self, rw_db) -> None:
        """后台线程陆续写入 + 前台轮询读取 → 每次读取都必须 Contract 合法。"""
        request_id = f"{_PREFIX}live"
        rw_db["request_ids"].append(request_id)

        stop = threading.Event()

        def writer() -> None:
            _insert_llm(request_id, f"{_PREFIX}live-llm")
            time.sleep(0.05)
            _insert_rag(request_id)
            time.sleep(0.05)
            _insert_tool(request_id)
            time.sleep(0.05)
            _insert_outcome(request_id, "SUCCESS")
            stop.set()

        thread = threading.Thread(target=writer)
        thread.start()

        observations: list[int] = []
        allowed: set[str | None] = {None, "SUCCESS"}
        try:
            for _ in range(40):
                _trace, timeline = _validate_views(
                    request_id,
                    allowed_outcomes=allowed,
                    # 写入进行中：两次读取之间可能刚好落库 → 不要求两视图相等
                    require_view_match=False,
                )
                observations.append(
                    len(timeline["llm_events"])
                    + len(timeline["tool_events"])
                    + len(timeline["rag_events"])
                    + (1 if timeline["outcome_event"] else 0)
                )
                if stop.is_set():
                    break
                time.sleep(0.02)
        finally:
            # 必须 join：否则后台线程会在 fixture teardown 之后继续写入
            thread.join(timeout=10)
        assert not thread.is_alive()
        # 最终稳定状态 = 完整（与 Step 71 一致）
        trace, timeline = _validate_views(request_id, allowed_outcomes={"SUCCESS"})
        assert len(timeline["llm_events"]) == 1
        assert len(timeline["rag_events"]) == 1
        assert len(timeline["tool_events"]) == 1
        assert timeline["outcome_event"]["status"] == "SUCCESS"
        # 观察单调不减（只增不减：无重复 / 无回退）
        assert observations == sorted(observations)

    def test_source_id_validation(self, rw_db) -> None:
        request_id = f"{_PREFIX}ids"
        rw_db["request_ids"].append(request_id)
        _insert_llm(request_id, f"{_PREFIX}ids-llm")
        _insert_rag(request_id)
        _insert_tool(request_id)
        _insert_outcome(request_id, "SUCCESS")

        _trace, timeline = _read_both(request_id)
        db = _db_ids(request_id)
        for group, key in (
            ("llm_events", "llm"),
            ("tool_events", "tool"),
            ("rag_events", "rag"),
        ):
            for event in timeline[group]:
                value = event["source_id"]
                assert isinstance(value, int)
                assert not isinstance(value, bool)
                assert not isinstance(value, str)          # 非 uuid
                assert value in db[key]
        assert timeline["outcome_event"]["source_id"] in db["outcome"]
        # Trace 侧 LLM 主键 == Timeline source_id
        assert [row["id"] for row in _trace["llm_usage"]] == [
            event["source_id"] for event in timeline["llm_events"]
        ]

    def test_security_read_during_write(self, rw_db) -> None:
        request_id = f"{_PREFIX}sec"
        rw_db["request_ids"].append(request_id)
        _insert_llm(request_id, f"{_PREFIX}sec-llm")
        _insert_rag(request_id)
        _insert_tool(request_id)
        _insert_outcome(request_id, "SUCCESS")

        with TestClient(app) as http:
            trace_text = http.get(f"{_TRACE}/{request_id}").text
            timeline_text = http.get(f"{_TIMELINE}/{request_id}").text

        for blob in (trace_text, timeline_text):
            for name in (
                "prompt", "messages", "system_prompt", "user_prompt", "sql",
                "query", "content", "embedding", "similarity", "arguments",
                "tool_result", "raw_response", "exception", "database_url",
                "result", "answer",
            ):
                assert f'"{name}":' not in blob, name
            for forbidden in (
                "api_key", "authorization", "password", "postgresql://",
                "sk-", "Bearer", "traceback", _SENTINEL,
            ):
                assert forbidden not in blob, forbidden

    def test_unknown_request_contract(self) -> None:
        with TestClient(app) as http:
            trace = http.get(f"{_TRACE}/{_PREFIX}not-exist")
            timeline = http.get(f"{_TIMELINE}/{_PREFIX}not-exist")

        assert trace.status_code == 200
        assert timeline.status_code == 200
        assert trace.json()["outcome"] is None
        assert timeline.json() == {
            "assistant_request_id": f"{_PREFIX}not-exist",
            "llm_events": [],
            "tool_events": [],
            "rag_events": [],
            "outcome_event": None,
        }

    def test_cleanup_zero_residue(self, rw_db) -> None:
        request_id = f"{_PREFIX}cleanup"
        rw_db["request_ids"].append(request_id)
        _insert_llm(request_id, f"{_PREFIX}cleanup-llm")
        _insert_rag(request_id)
        _insert_outcome(request_id, "SUCCESS")

        assert _db_ids(request_id)["llm"]
        _delete_rows([request_id])
        assert _db_ids(request_id) == {
            "llm": set(), "tool": set(), "rag": set(), "outcome": set(),
        }
        assert _residue() == {"llm": 0, "tool": 0, "rag": 0, "outcome": 0}


__all__ = [
    "TestPartialStateStages",
    "TestReadDuringWriteContract",
]
