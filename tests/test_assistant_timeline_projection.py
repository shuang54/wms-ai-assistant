"""Assistant Timeline 分组投影（Phase 3.12 Step 66）——投影 / 契约 / 安全测试。

覆盖（Step 66 §十九）：

    Case A  RAG                 llm=1 · rag=1 · tool=0 · outcome=SUCCESS
    Case B  Tool                tool=1（success）· llm=0 · rag=0 · outcome=SUCCESS
    Case C  T2SQL retry         llm=2（created_at ASC, id ASC；**不**声称 attempt）
    Case D  RAG failure         rag_events 可为 []（不补造）；有行时 status=None
    Case E  Refusal             llm=1 · outcome=REFUSED
    Case F  Historical          outcome_event=None（不推断）
    Case G  Cross-request       A / B 完全隔离

并验证：分组（不 merge）· 组内真实排序键 · source_id = 真实主键 ·
时间字段映射（钟域互斥）· status 映射 · frozen · 安全白名单 ·
无 event_id / sequence · 既有 Assistant Trace API 未变 · Service 无 DB / 无网络。

**离线**：Fake 读边界（真实 Row / View DTO）+ 静态检查 —— 无 DB、无网络。
"""
from __future__ import annotations

import ast
import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.app.api.assistant_trace import AssistantTraceResponse
from backend.app.db.assistant_outcome_repository import AssistantOutcomeRow
from backend.app.db.rag_execution_repository import RagExecutionRecordRow
from backend.app.db.tool_execution_repository import ToolExecutionRecordRow
from backend.app.dto.assistant_timeline import (
    ASSISTANT_TIMELINE_EVENT_FIELDS,
    ASSISTANT_TIMELINE_FIELDS,
    AssistantTimeline,
    AssistantTimelineEvent,
    AssistantTimelineEventStatus,
    AssistantTimelineEventType,
    AssistantTimelineSource,
)
from backend.app.services.assistant_timeline_query_service import (
    AssistantTimelineQueryService,
)
from backend.app.services.llm_usage_query_service import LLMUsageTraceRecordView

_BASE: datetime = datetime(2026, 9, 30, 9, 0, 0, tzinfo=timezone.utc)


# ============================================================
# Fake 读边界（真实 Row / View DTO；无 DB）
# ============================================================

class _FakeLlmQuery:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.calls: list[str] = []

    def list_by_assistant_request_id(self, request_id: str) -> list[Any]:
        self.calls.append(request_id)
        return [
            row for row in self._rows if row.assistant_request_id == request_id
        ]


class _FakeToolQuery:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.calls: list[str] = []

    def list_rows_by_request_id(self, request_id: str) -> list[Any]:
        self.calls.append(request_id)
        return [row for row in self._rows if row.request_id == request_id]


class _FakeRagQuery:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows
        self.calls: list[str] = []

    def list_by_request_id(self, request_id: str) -> list[Any]:
        self.calls.append(request_id)
        return [row for row in self._rows if row.request_id == request_id]


class _FakeOutcomeQuery:
    def __init__(self, rows: dict[str, AssistantOutcomeRow]) -> None:
        self._rows = rows
        self.calls: list[str] = []

    def get_row_by_assistant_request_id(
        self, assistant_request_id: str,
    ) -> AssistantOutcomeRow | None:
        self.calls.append(assistant_request_id)
        return self._rows.get(assistant_request_id)


def _llm(
    *,
    id: int,                                  # noqa: A002 —— 与 DB 主键同名
    assistant_request_id: str,
    created_at: datetime,
    request_id: str | None = "chatcmpl-prov",
) -> LLMUsageTraceRecordView:
    return LLMUsageTraceRecordView(
        id=id,
        assistant_request_id=assistant_request_id,
        request_id=request_id,
        provider="step66-provider",
        model="step66-model",
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
        created_at=created_at,
    )


def _tool(
    *,
    id: int,                                  # noqa: A002
    request_id: str,
    success: bool = True,
    started_at: datetime | None = None,
) -> ToolExecutionRecordRow:
    started = started_at or _BASE
    return ToolExecutionRecordRow(
        id=id,
        request_id=request_id,
        round=1,
        tool_name="get_inventory",
        started_at=started,
        finished_at=started + timedelta(milliseconds=40),
        duration_ms=40.0,
        success=success,
        project_id=None,
        tool_call_id=None,
        error_code=None,
        error_type=None,
    )


def _rag(
    *,
    id: int,                                  # noqa: A002
    request_id: str,
    started_at: datetime | None = None,
) -> RagExecutionRecordRow:
    started = started_at or _BASE
    return RagExecutionRecordRow(
        id=id,
        request_id=request_id,
        started_at=started,
        finished_at=started + timedelta(milliseconds=150),
        duration_ms=150.0,
        result_count=2,
        used_chunks_count=2,
        top_k=5,
        context_truncated=False,
        context_chars=120,
        reranker_used=False,
        rerank_elapsed_ms=None,
        chunk_ids=(1, 2),
        document_ids=(10,),
    )


def _outcome(
    *,
    id: int,                                  # noqa: A002
    assistant_request_id: str,
    outcome: str = "SUCCESS",
) -> AssistantOutcomeRow:
    return AssistantOutcomeRow(
        id=id,
        assistant_request_id=assistant_request_id,
        outcome=outcome,
        created_at=_BASE + timedelta(seconds=3),
    )


def _service(
    *,
    llm: list[Any] | None = None,
    tools: list[Any] | None = None,
    rag: list[Any] | None = None,
    outcomes: dict[str, AssistantOutcomeRow] | None = None,
) -> AssistantTimelineQueryService:
    return AssistantTimelineQueryService(
        llm_usage_query_service=_FakeLlmQuery(llm or []),
        tool_execution_query_service=_FakeToolQuery(tools or []),
        rag_execution_query_service=_FakeRagQuery(rag or []),
        outcome_query_service=_FakeOutcomeQuery(outcomes or {}),
    )


# ============================================================
# Case A ~ G
# ============================================================

class TestProjectionCases:
    def test_case_a_rag(self) -> None:
        service = _service(
            llm=[_llm(id=101, assistant_request_id="A", created_at=_BASE)],
            rag=[_rag(id=201, request_id="A")],
            outcomes={"A": _outcome(id=301, assistant_request_id="A")},
        )

        timeline = service.get_timeline("A")

        assert len(timeline.llm_events) == 1
        assert len(timeline.rag_events) == 1
        assert timeline.tool_events == ()
        assert timeline.outcome_event is not None
        assert timeline.outcome_event.status is AssistantTimelineEventStatus.SUCCESS
        # LLM：只有 DB 钟 created_at；status 不推断
        assert timeline.llm_events[0].created_at == _BASE
        assert timeline.llm_events[0].started_at is None
        assert timeline.llm_events[0].finished_at is None
        assert timeline.llm_events[0].duration_ms is None
        assert timeline.llm_events[0].status is None
        # RAG：App 钟三件套；created_at = None；status = None（RAG 表无 success）
        assert timeline.rag_events[0].started_at == _BASE
        assert timeline.rag_events[0].duration_ms == 150.0
        assert timeline.rag_events[0].created_at is None
        assert timeline.rag_events[0].status is None

    def test_case_b_tool(self) -> None:
        service = _service(
            tools=[_tool(id=211, request_id="B")],
            outcomes={"B": _outcome(id=311, assistant_request_id="B")},
        )

        timeline = service.get_timeline("B")

        assert len(timeline.tool_events) == 1
        assert timeline.llm_events == ()
        assert timeline.rag_events == ()
        assert (
            timeline.tool_events[0].status
            is AssistantTimelineEventStatus.TOOL_SUCCESS
        )
        assert timeline.tool_events[0].created_at is None
        assert (
            timeline.tool_events[0].source
            is AssistantTimelineSource.TOOL_EXECUTION
        )
        assert timeline.tool_events[0].source_id == 211      # 真实主键

    def test_case_b_tool_failure(self) -> None:
        service = _service(
            tools=[_tool(id=212, request_id="B", success=False)],
            outcomes={"B": _outcome(id=312, assistant_request_id="B", outcome="FAILED")},
        )

        timeline = service.get_timeline("B")

        assert (
            timeline.tool_events[0].status
            is AssistantTimelineEventStatus.TOOL_FAILED
        )
        assert timeline.outcome_event is not None
        assert timeline.outcome_event.status is AssistantTimelineEventStatus.FAILED

    def test_case_c_t2sql_retry(self) -> None:
        """两次真实 LLM 调用 → 2 个事件，按 ``created_at ASC, id ASC``
        （**不**声称 attempt=1/2：表里没有 attempt 事实）。"""
        service = _service(
            llm=[
                _llm(id=105, assistant_request_id="C", created_at=_BASE),
                _llm(
                    id=104,
                    assistant_request_id="C",
                    created_at=_BASE,
                ),  # 同刻 → id tie-breaker
                _llm(
                    id=106,
                    assistant_request_id="C",
                    created_at=_BASE + timedelta(seconds=2),
                ),
            ],
            outcomes={"C": _outcome(id=321, assistant_request_id="C")},
        )

        timeline = service.get_timeline("C")

        assert [event.source_id for event in timeline.llm_events] == [
            104, 105, 106,
        ]
        assert "attempt" not in dataclasses.asdict(timeline.llm_events[0])
        for event in timeline.llm_events:
            assert event.status is None                 # 不推断哪次是重试
            assert event.event_type is AssistantTimelineEventType.LLM

    def test_case_d_rag_failure_absence_not_invented(self) -> None:
        """RAG 运行期失败可能**没有** RAG 行 → rag_events = []（不补造）。"""
        service = _service(
            llm=[_llm(id=107, assistant_request_id="D", created_at=_BASE)],
            rag=[],
            outcomes={"D": _outcome(id=331, assistant_request_id="D", outcome="FAILED")},
        )

        timeline = service.get_timeline("D")

        assert timeline.rag_events == ()
        assert timeline.outcome_event is not None
        assert timeline.outcome_event.status is AssistantTimelineEventStatus.FAILED

    def test_case_d_rag_failure_with_row_keeps_status_none(self) -> None:
        """若真实持久化确实写了 RAG 行 → 1 条事件，status 仍为 None（不推断失败）。"""
        service = _service(rag=[_rag(id=221, request_id="D2")])

        timeline = service.get_timeline("D2")

        assert len(timeline.rag_events) == 1
        assert timeline.rag_events[0].status is None

    def test_case_e_refusal(self) -> None:
        service = _service(
            llm=[_llm(id=108, assistant_request_id="E", created_at=_BASE)],
            outcomes={
                "E": _outcome(id=341, assistant_request_id="E", outcome="REFUSED")
            },
        )

        timeline = service.get_timeline("E")

        assert len(timeline.llm_events) == 1
        assert timeline.tool_events == ()
        assert timeline.rag_events == ()
        assert timeline.outcome_event is not None
        assert timeline.outcome_event.status is AssistantTimelineEventStatus.REFUSED

    def test_case_f_historical_without_outcome(self) -> None:
        service = _service(
            llm=[_llm(id=109, assistant_request_id="F", created_at=_BASE)],
            rag=[_rag(id=231, request_id="F")],
            outcomes={},                                # 无终态记录
        )

        timeline = service.get_timeline("F")

        assert timeline.outcome_event is None           # 不推断、不补造
        assert timeline.is_empty() is False             # 其它段仍有事实

    def test_case_g_cross_request_isolation(self) -> None:
        service = _service(
            llm=[
                _llm(id=111, assistant_request_id="A", created_at=_BASE),
                _llm(id=112, assistant_request_id="B", created_at=_BASE),
            ],
            tools=[_tool(id=241, request_id="A"), _tool(id=242, request_id="B")],
            rag=[_rag(id=251, request_id="A"), _rag(id=252, request_id="B")],
            outcomes={
                "A": _outcome(id=351, assistant_request_id="A", outcome="SUCCESS"),
                "B": _outcome(id=352, assistant_request_id="B", outcome="FAILED"),
            },
        )

        timeline_a = service.get_timeline("A")
        timeline_b = service.get_timeline("B")

        assert [event.source_id for event in timeline_a.llm_events] == [111]
        assert [event.source_id for event in timeline_a.tool_events] == [241]
        assert [event.source_id for event in timeline_a.rag_events] == [251]
        assert timeline_a.outcome_event is not None
        assert timeline_a.outcome_event.status is AssistantTimelineEventStatus.SUCCESS

        assert [event.source_id for event in timeline_b.llm_events] == [112]
        assert [event.source_id for event in timeline_b.tool_events] == [242]
        assert [event.source_id for event in timeline_b.rag_events] == [252]
        assert timeline_b.outcome_event is not None
        assert timeline_b.outcome_event.status is AssistantTimelineEventStatus.FAILED

    def test_unknown_request_returns_empty_timeline(self) -> None:
        service = _service(llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)])

        timeline = service.get_timeline("not-exist")

        assert timeline.is_empty() is True
        assert timeline.llm_events == ()
        assert timeline.tool_events == ()
        assert timeline.rag_events == ()
        assert timeline.outcome_event is None
        assert timeline.assistant_request_id == "not-exist"

    def test_foreign_rows_are_filtered_defensively(self) -> None:
        """读边界若误返回其它 request 的行 → 投影仍按 id 隔离（不混入）。"""

        class _LeakyLlmQuery:
            def list_by_assistant_request_id(self, request_id: str) -> list[Any]:
                return [
                    _llm(id=1, assistant_request_id="A", created_at=_BASE),
                    _llm(id=2, assistant_request_id="B", created_at=_BASE),
                ]

        service = AssistantTimelineQueryService(
            llm_usage_query_service=_LeakyLlmQuery(),
            tool_execution_query_service=_FakeToolQuery([]),
            rag_execution_query_service=_FakeRagQuery([]),
            outcome_query_service=_FakeOutcomeQuery({}),
        )

        timeline = service.get_timeline("A")

        assert [event.source_id for event in timeline.llm_events] == [1]
        for event in timeline.llm_events:
            assert event.assistant_request_id == "A"


# ============================================================
# 分组 / 排序 / 身份
# ============================================================

class TestGroupingAndOrdering:
    def test_groups_are_not_merged(self) -> None:
        """没有统一的 ``events`` 字段；四段严格独立。"""
        fields = set(AssistantTimeline.__dataclass_fields__)
        assert fields == set(ASSISTANT_TIMELINE_FIELDS)
        assert "events" not in fields
        assert "sequence" not in fields
        assert "event_id" not in fields

    def test_llm_ordering_is_created_at_then_id(self) -> None:
        service = _service(
            llm=[
                _llm(id=3, assistant_request_id="A", created_at=_BASE + timedelta(seconds=5)),
                _llm(id=1, assistant_request_id="A", created_at=_BASE),
                _llm(id=2, assistant_request_id="A", created_at=_BASE + timedelta(seconds=5)),
            ]
        )

        timeline = service.get_timeline("A")

        assert [event.source_id for event in timeline.llm_events] == [1, 2, 3]

    def test_tool_and_rag_ordering_is_id(self) -> None:
        service = _service(
            tools=[
                _tool(id=9, request_id="A", started_at=_BASE + timedelta(seconds=9)),
                _tool(id=7, request_id="A", started_at=_BASE),
            ],
            rag=[_rag(id=8, request_id="A"), _rag(id=6, request_id="A")],
        )

        timeline = service.get_timeline("A")

        assert [event.source_id for event in timeline.tool_events] == [7, 9]
        assert [event.source_id for event in timeline.rag_events] == [6, 8]

    def test_source_id_is_the_real_primary_key(self) -> None:
        service = _service(
            llm=[_llm(id=1001, assistant_request_id="A", created_at=_BASE)],
            tools=[_tool(id=2001, request_id="A")],
            rag=[_rag(id=3001, request_id="A")],
            outcomes={"A": _outcome(id=4001, assistant_request_id="A")},
        )

        timeline = service.get_timeline("A")

        assert timeline.llm_events[0].source_id == 1001
        assert timeline.tool_events[0].source_id == 2001
        assert timeline.rag_events[0].source_id == 3001
        assert timeline.outcome_event is not None
        assert timeline.outcome_event.source_id == 4001

    def test_source_and_event_type_pairing(self) -> None:
        service = _service(
            llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)],
            tools=[_tool(id=2, request_id="A")],
            rag=[_rag(id=3, request_id="A")],
            outcomes={"A": _outcome(id=4, assistant_request_id="A")},
        )

        timeline = service.get_timeline("A")

        assert (
            timeline.llm_events[0].source is AssistantTimelineSource.LLM_USAGE
            and timeline.llm_events[0].event_type is AssistantTimelineEventType.LLM
        )
        assert (
            timeline.tool_events[0].source
            is AssistantTimelineSource.TOOL_EXECUTION
            and timeline.tool_events[0].event_type is AssistantTimelineEventType.TOOL
        )
        assert (
            timeline.rag_events[0].source is AssistantTimelineSource.RAG_EXECUTION
            and timeline.rag_events[0].event_type is AssistantTimelineEventType.RAG
        )
        assert timeline.outcome_event is not None
        assert (
            timeline.outcome_event.source
            is AssistantTimelineSource.ASSISTANT_OUTCOME
            and timeline.outcome_event.event_type
            is AssistantTimelineEventType.OUTCOME
        )


# ============================================================
# DTO 契约 / 冻结 / 安全
# ============================================================

class TestDtoContract:
    def test_event_fields_are_whitelisted(self) -> None:
        assert tuple(AssistantTimelineEvent.__dataclass_fields__) == (
            ASSISTANT_TIMELINE_EVENT_FIELDS
        )

    def test_no_fake_identity_fields(self) -> None:
        names = set(AssistantTimelineEvent.__dataclass_fields__)
        for forbidden in ("event_id", "sequence", "seq", "span_id",
                          "parent_event_id", "index", "order"):
            assert forbidden not in names, forbidden

    def test_frozen(self) -> None:
        assert AssistantTimelineEvent.__dataclass_params__.frozen is True
        assert AssistantTimeline.__dataclass_params__.frozen is True

        event = AssistantTimelineEvent(
            assistant_request_id="A",
            source=AssistantTimelineSource.LLM_USAGE,
            event_type=AssistantTimelineEventType.LLM,
            source_id=1,
            created_at=_BASE,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.source_id = 2                          # type: ignore[misc]

        timeline = AssistantTimeline(assistant_request_id="A")
        with pytest.raises(dataclasses.FrozenInstanceError):
            timeline.outcome_event = event               # type: ignore[misc]

    def test_clock_domain_exclusivity(self) -> None:
        with pytest.raises(ValueError):
            AssistantTimelineEvent(
                assistant_request_id="A",
                source=AssistantTimelineSource.LLM_USAGE,
                event_type=AssistantTimelineEventType.LLM,
                source_id=1,
                created_at=_BASE,                        # DB 钟
                started_at=_BASE,                        # App 钟（互斥）
            )

    def test_source_id_must_be_real_int_primary_key(self) -> None:
        for bad in ("1", 1.0, True, None):
            with pytest.raises(ValueError):
                AssistantTimelineEvent(
                    assistant_request_id="A",
                    source=AssistantTimelineSource.LLM_USAGE,
                    event_type=AssistantTimelineEventType.LLM,
                    source_id=bad,                       # type: ignore[arg-type]
                    created_at=_BASE,
                )

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValueError):
            AssistantTimelineEvent(
                assistant_request_id="A",
                source=AssistantTimelineSource.TOOL_EXECUTION,
                event_type=AssistantTimelineEventType.TOOL,
                source_id=1,
                started_at=datetime(2026, 9, 30, 9, 0, 0),   # naive
            )

    def test_source_event_type_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError):
            AssistantTimelineEvent(
                assistant_request_id="A",
                source=AssistantTimelineSource.LLM_USAGE,
                event_type=AssistantTimelineEventType.TOOL,
                source_id=1,
            )

    def test_timeline_rejects_foreign_events(self) -> None:
        foreign = AssistantTimelineEvent(
            assistant_request_id="B",
            source=AssistantTimelineSource.LLM_USAGE,
            event_type=AssistantTimelineEventType.LLM,
            source_id=1,
            created_at=_BASE,
        )
        with pytest.raises(ValueError):
            AssistantTimeline(assistant_request_id="A", llm_events=(foreign,))

    def test_invalid_assistant_request_id(self) -> None:
        for bad in ("", "   ", "x" * 129, None, 123):
            with pytest.raises(ValueError):
                _service().get_timeline(bad)             # type: ignore[arg-type]

    def test_event_type_is_limited_to_four(self) -> None:
        assert [member.value for member in AssistantTimelineEventType] == [
            "LLM", "TOOL", "RAG", "OUTCOME",
        ]
        for forbidden in ("ROUTER", "VALIDATOR", "EXECUTOR", "PROMPT", "EMBEDDING"):
            assert forbidden not in AssistantTimelineEventType.__members__


class TestSecurity:
    def test_event_carries_only_metadata_identity_timing_status(self) -> None:
        service = _service(
            llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)],
            tools=[_tool(id=2, request_id="A")],
            rag=[_rag(id=3, request_id="A")],
            outcomes={"A": _outcome(id=4, assistant_request_id="A")},
        )
        timeline = service.get_timeline("A")
        payload = json.dumps(
            dataclasses.asdict(timeline), default=str, ensure_ascii=False
        )

        for forbidden in (
            "prompt", "messages", "answer", "sql", "SELECT ", "content",
            "arguments", "tool_result", "data", "api_key", "authorization",
            "password", "postgresql://", "raw_response", "traceback",
            "exception", "sk-",
        ):
            assert forbidden not in payload, forbidden

    def test_field_names_have_no_sensitive_slots(self) -> None:
        names = set(AssistantTimelineEvent.__dataclass_fields__) | set(
            AssistantTimeline.__dataclass_fields__
        )
        for forbidden in (
            "prompt", "messages", "sql", "query", "answer", "content",
            "chunk_content", "arguments", "result_data", "api_key",
            "authorization", "database_url", "raw_response", "error_message",
        ):
            assert forbidden not in names, forbidden


# ============================================================
# 约束：不新增 API · Service 无 DB / 无网络 · 不生成身份
# ============================================================

def _module_identifiers(relative: str) -> set[str]:
    tree = ast.parse(
        (Path(__file__).resolve().parents[1] / relative).read_text(
            encoding="utf-8"
        )
    )
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


class TestBoundaries:
    def test_trace_api_unchanged(self) -> None:
        """Step 66 §四/§二十三：既有 Trace API **未**加入 timeline 字段 / 端点。"""
        from backend.app.main import app

        assert list(AssistantTraceResponse.model_fields) == [
            "assistant_request_id",
            "outcome",
            "llm_usage",
            "tool_executions",
            "rag_executions",
        ]
        assert not [
            path for path in app.openapi()["paths"] if "timeline" in path.lower()
        ]

    def test_service_has_no_db_access(self) -> None:
        identifiers = _module_identifiers(
            "backend/app/services/assistant_timeline_query_service.py"
        )
        for forbidden in (
            "create_" + "engine",
            "get_" + "engine",
            "Session",
            "session" + "maker",
            "get_session_" + "factory",
            "metadata",
        ):
            assert forbidden not in identifiers, forbidden

    def test_service_has_no_network(self) -> None:
        identifiers = _module_identifiers(
            "backend/app/services/assistant_timeline_query_service.py"
        )
        for forbidden in ("httpx", "requests", "urllib", "socket", "redis",
                          "kafka", "openai", "TestClient"):
            assert forbidden not in identifiers, forbidden

    def test_no_generated_identity(self) -> None:
        for relative in (
            "backend/app/dto/assistant_timeline.py",
            "backend/app/services/assistant_timeline_query_service.py",
        ):
            identifiers = _module_identifiers(relative)
            for forbidden in ("uuid4", "uuid1", "event_id", "sequence",
                              "enumerate", "hash"):
                assert forbidden not in identifiers, f"{relative}: {forbidden}"

    def test_constructor_requires_read_boundaries(self) -> None:
        with pytest.raises(TypeError):
            AssistantTimelineQueryService(llm_usage_query_service=object())


__all__ = [
    "TestProjectionCases",
    "TestGroupingAndOrdering",
    "TestDtoContract",
    "TestSecurity",
    "TestBoundaries",
]
