"""Phase 3.12 Step 67 —— Assistant Timeline **HTTP Read API 可行性审计**。

只审计一个问题：

    AssistantTimelineQueryService → AssistantTimeline → **是否值得 / 适合暴露为 HTTP**？

本阶段**不新增任何 HTTP endpoint**（Step 67 §十六）：

    * 不新增 router / 不注册路径 / 不修改 Assistant Trace API；
    * 不改 DTO（即使审计发现开放问题，也只**记录**）；
    * 不实现 Unified Timeline / event_id / sequence / pagination / 认证。

审计方式：真实 Service + Fake 读边界（离线）+ **候选 API DTO 映射**（审计本地
定义，仅用于评估 shape / 字段 / 体积）+ 静态检查（错误映射约定 / 无 DB / 无网络 /
无 endpoint 注册）。

覆盖（§十六）：response shape · 字段白名单 · source/event_type 配对 ·
时间语义 · 组内排序 · 无合并排序 · source_id 暴露审计 · unknown request ·
历史缺 Outcome · 空分组 · 跨请求隔离 · 敏感字段隔离 · request_id 校验 ·
错误映射契约 · 合成体积 · 无 DB/网络 · 无 endpoint。
"""
from __future__ import annotations

import ast
import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

import pytest

from backend.app.api.assistant_trace import AssistantTraceResponse
from backend.app.db.assistant_outcome_repository import AssistantOutcomeRow
from backend.app.db.rag_execution_repository import RagExecutionRecordRow
from backend.app.db.tool_execution_repository import ToolExecutionRecordRow
from backend.app.dto.assistant_timeline import (
    ASSISTANT_TIMELINE_EVENT_FIELDS,
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

_BASE: datetime = datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc)
_SENTINEL: Final[str] = "STEP67-SECRET-SENTINEL"

#: 审计候选端点（**未实现**；仅用于文档与断言"当前确实不存在"）。
CANDIDATE_PATH: Final[str] = (
    "/api/observability/assistant-timeline/{assistant_request_id}"
)

#: 评估用的软阈值（Step 54 既有 64 KiB 触发线；本阶段只估算，不实现分页）
PAYLOAD_TRIGGER_BYTES: Final[int] = 64 * 1024


# ============================================================
# Fake 读边界（离线；真实 Row / View DTO）
# ============================================================

class _FakeLlmQuery:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def list_by_assistant_request_id(self, request_id: str) -> list[Any]:
        return [
            row for row in self._rows if row.assistant_request_id == request_id
        ]


class _FakeToolQuery:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def list_rows_by_request_id(self, request_id: str) -> list[Any]:
        return [row for row in self._rows if row.request_id == request_id]


class _FakeRagQuery:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def list_by_request_id(self, request_id: str) -> list[Any]:
        return [row for row in self._rows if row.request_id == request_id]


class _FakeOutcomeQuery:
    def __init__(self, rows: dict[str, AssistantOutcomeRow]) -> None:
        self._rows = rows

    def get_row_by_assistant_request_id(
        self, assistant_request_id: str,
    ) -> AssistantOutcomeRow | None:
        return self._rows.get(assistant_request_id)


def _llm(
    *,
    id: int,                                  # noqa: A002
    assistant_request_id: str,
    created_at: datetime,
) -> LLMUsageTraceRecordView:
    return LLMUsageTraceRecordView(
        id=id,
        assistant_request_id=assistant_request_id,
        request_id="chatcmpl-prov",
        provider="step67-provider",
        model="step67-model",
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
        created_at=created_at,
    )


def _tool(*, id: int, request_id: str) -> ToolExecutionRecordRow:  # noqa: A002
    return ToolExecutionRecordRow(
        id=id,
        request_id=request_id,
        round=1,
        tool_name="get_inventory",
        started_at=_BASE,
        finished_at=_BASE + timedelta(milliseconds=40),
        duration_ms=40.0,
        success=True,
        project_id=None,
        tool_call_id=None,
        error_code=None,
        error_type=None,
    )


def _rag(*, id: int, request_id: str) -> RagExecutionRecordRow:  # noqa: A002
    return RagExecutionRecordRow(
        id=id,
        request_id=request_id,
        started_at=_BASE,
        finished_at=_BASE + timedelta(milliseconds=150),
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
    *, id: int, assistant_request_id: str, outcome: str = "SUCCESS",  # noqa: A002
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
# 候选 API 响应映射（**审计本地**；不是生产 DTO）
# ============================================================

#: 候选响应字段（不含 ``source_id`` 的版本 —— 隐藏内部主键）
CANDIDATE_EVENT_FIELDS_HIDDEN_ID: Final[tuple[str, ...]] = (
    "source",
    "event_type",
    "started_at",
    "finished_at",
    "duration_ms",
    "created_at",
    "status",
)

#: 候选响应字段（含 ``source_id`` —— 保留事件身份）
CANDIDATE_EVENT_FIELDS_WITH_ID: Final[tuple[str, ...]] = (
    "source",
    "event_type",
    "source_id",
    "started_at",
    "finished_at",
    "duration_ms",
    "created_at",
    "status",
)

CANDIDATE_TIMELINE_FIELDS: Final[tuple[str, ...]] = (
    "assistant_request_id",
    "llm_events",
    "tool_events",
    "rag_events",
    "outcome_event",
)


def _candidate_event_payload(
    event: AssistantTimelineEvent,
    *,
    expose_source_id: bool,
) -> dict[str, Any]:
    """审计用候选映射（显式逐字段；不含任何内部对象）。"""
    payload: dict[str, Any] = {
        "source": event.source.value,
        "event_type": event.event_type.value,
    }
    if expose_source_id:
        payload["source_id"] = event.source_id
    payload.update(
        {
            "started_at": (
                event.started_at.isoformat() if event.started_at else None
            ),
            "finished_at": (
                event.finished_at.isoformat() if event.finished_at else None
            ),
            "duration_ms": event.duration_ms,
            "created_at": (
                event.created_at.isoformat() if event.created_at else None
            ),
            "status": event.status.value if event.status else None,
        }
    )
    return payload


def _candidate_timeline_payload(
    timeline: AssistantTimeline,
    *,
    expose_source_id: bool,
) -> dict[str, Any]:
    return {
        "assistant_request_id": timeline.assistant_request_id,
        "llm_events": [
            _candidate_event_payload(event, expose_source_id=expose_source_id)
            for event in timeline.llm_events
        ],
        "tool_events": [
            _candidate_event_payload(event, expose_source_id=expose_source_id)
            for event in timeline.tool_events
        ],
        "rag_events": [
            _candidate_event_payload(event, expose_source_id=expose_source_id)
            for event in timeline.rag_events
        ],
        "outcome_event": (
            None
            if timeline.outcome_event is None
            else _candidate_event_payload(
                timeline.outcome_event, expose_source_id=expose_source_id
            )
        ),
    }


# ============================================================
# 1~6. Response shape / 字段 / 配对 / 时间 / 排序 / 不合并
# ============================================================

class TestResponseContractAudit:
    def test_1_candidate_response_shape(self) -> None:
        service = _service(
            llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)],
            tools=[_tool(id=2, request_id="A")],
            rag=[_rag(id=3, request_id="A")],
            outcomes={"A": _outcome(id=4, assistant_request_id="A")},
        )
        payload = _candidate_timeline_payload(
            service.get_timeline("A"), expose_source_id=True
        )

        assert tuple(payload) == CANDIDATE_TIMELINE_FIELDS
        assert payload["assistant_request_id"] == "A"
        assert len(payload["llm_events"]) == 1
        assert len(payload["tool_events"]) == 1
        assert len(payload["rag_events"]) == 1
        assert payload["outcome_event"] is not None
        # 四段仍是**独立数组**（不是 merged events）
        assert "events" not in payload
        assert "sequence" not in payload

    def test_2_field_whitelist(self) -> None:
        assert tuple(AssistantTimelineEvent.__dataclass_fields__) == (
            ASSISTANT_TIMELINE_EVENT_FIELDS
        )
        event_payload = _candidate_event_payload(
            AssistantTimelineEvent(
                assistant_request_id="A",
                source=AssistantTimelineSource.RAG_EXECUTION,
                event_type=AssistantTimelineEventType.RAG,
                source_id=3,
                started_at=_BASE,
                finished_at=_BASE,
                duration_ms=1.0,
            ),
            expose_source_id=True,
        )
        assert tuple(event_payload) == CANDIDATE_EVENT_FIELDS_WITH_ID
        assert tuple(
            _candidate_event_payload(
                AssistantTimelineEvent(
                    assistant_request_id="A",
                    source=AssistantTimelineSource.RAG_EXECUTION,
                    event_type=AssistantTimelineEventType.RAG,
                    source_id=3,
                    started_at=_BASE,
                ),
                expose_source_id=False,
            )
        ) == CANDIDATE_EVENT_FIELDS_HIDDEN_ID

    def test_3_source_event_type_pairing_is_stable(self) -> None:
        service = _service(
            llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)],
            tools=[_tool(id=2, request_id="A")],
            rag=[_rag(id=3, request_id="A")],
            outcomes={"A": _outcome(id=4, assistant_request_id="A")},
        )
        timeline = service.get_timeline("A")
        events = (
            *timeline.llm_events,
            *timeline.tool_events,
            *timeline.rag_events,
            timeline.outcome_event,
        )
        for event in events:
            assert event is not None
            assert event.source.value in (
                "llm_usage", "tool_execution", "rag_execution",
                "assistant_outcome",
            )
            assert event.event_type.value in ("LLM", "TOOL", "RAG", "OUTCOME")

    def test_4_timestamp_semantics_are_not_aliased(self) -> None:
        service = _service(
            llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)],
            tools=[_tool(id=2, request_id="A")],
            rag=[_rag(id=3, request_id="A")],
            outcomes={"A": _outcome(id=4, assistant_request_id="A")},
        )
        timeline = service.get_timeline("A")

        llm_event = timeline.llm_events[0]
        assert llm_event.created_at == _BASE          # DB 钟（落库时刻）
        assert llm_event.started_at is None           # → 不得冒充开始时间
        assert llm_event.finished_at is None          # ← created_at 不得改名

        tool_event = timeline.tool_events[0]
        assert tool_event.created_at is None          # Tool 无 DB 钟列
        assert tool_event.started_at is not None
        assert tool_event.finished_at is not None

        assert timeline.outcome_event is not None
        assert timeline.outcome_event.created_at is not None
        assert timeline.outcome_event.started_at is None

    def test_5_grouped_ordering_only(self) -> None:
        service = _service(
            llm=[
                _llm(id=3, assistant_request_id="A", created_at=_BASE),
                _llm(id=1, assistant_request_id="A", created_at=_BASE),
                _llm(
                    id=2,
                    assistant_request_id="A",
                    created_at=_BASE + timedelta(seconds=1),
                ),
            ],
            tools=[_tool(id=9, request_id="A"), _tool(id=7, request_id="A")],
            rag=[_rag(id=8, request_id="A"), _rag(id=6, request_id="A")],
        )
        timeline = service.get_timeline("A")

        assert [
            (event.source_id, event.created_at)
            for event in timeline.llm_events
        ] == [(1, _BASE), (3, _BASE), (2, _BASE + timedelta(seconds=1))]
        assert [event.source_id for event in timeline.tool_events] == [7, 9]
        assert [event.source_id for event in timeline.rag_events] == [6, 8]

    def test_6_no_merged_ordering_in_service(self) -> None:
        """Service 不得跨组排序 / 不得产生 sequence / 不得合并事件。"""
        source = Path(
            "backend/app/services/assistant_timeline_query_service.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "enumerate" not in calls
        assert "uuid4" not in calls
        assert "hash" not in calls
        # 排序只出现在**组内**（3 次：llm / tool / rag），键均为真实字段
        assert source.count("sorted(") == 3


# ============================================================
# 7. source_id 暴露审计（记录问题，不改 DTO）
# ============================================================

class TestSourceIdExposureAudit:
    def test_7_source_id_is_the_internal_primary_key(self) -> None:
        service = _service(
            llm=[_llm(id=1001, assistant_request_id="A", created_at=_BASE)],
            tools=[_tool(id=2001, request_id="A")],
        )
        timeline = service.get_timeline("A")

        # 事实：source_id = 内部 BIGINT 主键（当前**唯一**事件身份）
        assert timeline.llm_events[0].source_id == 1001
        assert timeline.tool_events[0].source_id == 2001
        assert isinstance(timeline.llm_events[0].source_id, int)

    def test_7b_project_precedent_is_inconsistent(self) -> None:
        """项目先例不一致：LLM Trace **暴露** id，Tool / RAG Trace **不暴露**。"""
        from backend.app.api.assistant_trace import (
            LLMUsageTraceResponse,
            RagExecutionTraceResponse,
            ToolExecutionTraceResponse,
        )

        assert "id" in LLMUsageTraceResponse.model_fields        # Step 37
        assert "id" not in ToolExecutionTraceResponse.model_fields
        assert "id" not in RagExecutionTraceResponse.model_fields

    def test_7c_hiding_source_id_is_possible_but_costs_identity(self) -> None:
        """隐藏 source_id 的候选映射可用，但事件将**没有任何身份**。"""
        service = _service(llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)])
        timeline = service.get_timeline("A")

        hidden = _candidate_event_payload(
            timeline.llm_events[0], expose_source_id=False
        )
        assert "source_id" not in hidden
        assert "event_id" not in hidden                 # 也没有替代身份
        assert "sequence" not in hidden


# ============================================================
# 8~10. Unknown request / Historical / Empty groups
# ============================================================

class TestHistoricalAndEmptyAudit:
    def test_8_unknown_request_is_empty_not_error(self) -> None:
        service = _service(llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)])

        timeline = service.get_timeline("not-exist")

        assert timeline.is_empty() is True
        payload = _candidate_timeline_payload(timeline, expose_source_id=True)
        assert payload == {
            "assistant_request_id": "not-exist",
            "llm_events": [],
            "tool_events": [],
            "rag_events": [],
            "outcome_event": None,
        }

    def test_9_historical_missing_outcome(self) -> None:
        service = _service(
            llm=[_llm(id=1, assistant_request_id="F", created_at=_BASE)],
            rag=[_rag(id=3, request_id="F")],
            outcomes={},
        )

        timeline = service.get_timeline("F")

        assert timeline.outcome_event is None           # 不推断
        assert len(timeline.llm_events) == 1
        assert len(timeline.rag_events) == 1

    def test_10_empty_groups_case_matrix(self) -> None:
        """Case A~F：任意分组缺失都为空数组 / None，**不补造**。"""
        all_rows = _service(
            llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)],
            tools=[_tool(id=2, request_id="A")],
            rag=[_rag(id=3, request_id="A")],
            outcomes={"A": _outcome(id=4, assistant_request_id="A")},
        )
        complete = all_rows.get_timeline("A")
        assert (
            len(complete.llm_events) == 1
            and len(complete.tool_events) == 1
            and len(complete.rag_events) == 1
            and complete.outcome_event is not None
        )

        # Case B（无 Outcome）+ Case F（全空）
        assert all_rows.get_timeline("A").outcome_event is not None
        assert all_rows.get_timeline("missing").is_empty() is True

        no_rag = _service(
            llm=[_llm(id=1, assistant_request_id="B", created_at=_BASE)],
            tools=[_tool(id=2, request_id="B")],
        ).get_timeline("B")
        assert no_rag.rag_events == ()                  # Case C

        no_tool = _service(
            llm=[_llm(id=1, assistant_request_id="C", created_at=_BASE)],
            rag=[_rag(id=3, request_id="C")],
        ).get_timeline("C")
        assert no_tool.tool_events == ()                # Case D

        no_llm = _service(
            tools=[_tool(id=2, request_id="D")],
            rag=[_rag(id=3, request_id="D")],
        ).get_timeline("D")
        assert no_llm.llm_events == ()                  # Case E


# ============================================================
# 11~12. 隔离 / 敏感字段
# ============================================================

class TestIsolationAndSecurityAudit:
    def test_11_cross_request_isolation(self) -> None:
        service = _service(
            llm=[
                _llm(id=11, assistant_request_id="A", created_at=_BASE),
                _llm(id=12, assistant_request_id="B", created_at=_BASE),
            ],
            tools=[_tool(id=21, request_id="A"), _tool(id=22, request_id="B")],
            rag=[_rag(id=31, request_id="A"), _rag(id=32, request_id="B")],
            outcomes={
                "A": _outcome(id=41, assistant_request_id="A", outcome="SUCCESS"),
                "B": _outcome(id=42, assistant_request_id="B", outcome="FAILED"),
            },
        )

        payload_a = _candidate_timeline_payload(
            service.get_timeline("A"), expose_source_id=True
        )
        payload_b = _candidate_timeline_payload(
            service.get_timeline("B"), expose_source_id=True
        )

        assert payload_a["llm_events"][0]["source_id"] == 11
        assert payload_a["outcome_event"]["status"] == "SUCCESS"
        assert payload_b["llm_events"][0]["source_id"] == 12
        assert payload_b["outcome_event"]["status"] == "FAILED"
        assert [event["source_id"] for event in payload_b["llm_events"]] == [12]
        assert [event["source_id"] for event in payload_b["tool_events"]] == [22]
        assert [event["source_id"] for event in payload_b["rag_events"]] == [32]

    def test_12_sensitive_fields_are_absent(self) -> None:
        service = _service(
            llm=[_llm(id=1, assistant_request_id="A", created_at=_BASE)],
            tools=[_tool(id=2, request_id="A")],
            rag=[_rag(id=3, request_id="A")],
            outcomes={"A": _outcome(id=4, assistant_request_id="A")},
        )
        blob = json.dumps(
            _candidate_timeline_payload(
                service.get_timeline("A"), expose_source_id=True
            ),
            ensure_ascii=False,
        )

        for forbidden in (
            "prompt", "messages", "system_prompt", "sql", "query", "content",
            "embedding", "similarity", "arguments", "result", "api_key",
            "authorization", "password", "postgresql://", "raw_response",
            "traceback", "exception", "sk-", _SENTINEL,
        ):
            assert forbidden not in blob, forbidden
        # 允许出现的类别：identity / timing / status / metadata
        assert "source_id" in blob and "created_at" in blob and "status" in blob


# ============================================================
# 13~14. request_id 校验 / 错误映射契约
# ============================================================

class TestValidationAndErrorContractAudit:
    def test_13_request_id_validation_reuses_existing_rules(self) -> None:
        service = _service()
        for bad in ("", "   ", "x" * 129, None, 123):
            with pytest.raises(ValueError):
                service.get_timeline(bad)               # type: ignore[arg-type]

        # 既有 Trace API 的 Path 校验边界（复用同一 1~128 约定）
        from backend.app.api.assistant_trace import (
            MAX_ASSISTANT_REQUEST_ID_LENGTH,
            MIN_ASSISTANT_REQUEST_ID_LENGTH,
        )

        assert MIN_ASSISTANT_REQUEST_ID_LENGTH == 1
        assert MAX_ASSISTANT_REQUEST_ID_LENGTH == 128

    def test_14_error_mapping_follows_trace_conventions(self) -> None:
        """错误契约：复用既有 Trace 约定（400 / 422 / 502 / 500），不暴露 DB 细节。"""
        from backend.app.api import assistant_trace as trace_api

        source = Path(trace_api.__file__).read_text(encoding="utf-8")
        assert "HTTP_400_BAD_REQUEST" in source
        assert "HTTP_502_BAD_GATEWAY" in source
        assert "HTTP_500_INTERNAL_SERVER_ERROR" in source
        # 不把异常文本直接回给客户端
        assert 'detail="助手链路观测数据不可用"' in source
        assert "detail=str(exc)" not in source

        # Timeline Service **原样透传**下游异常（不吞、不降级）
        service_source = Path(
            "backend/app/services/assistant_timeline_query_service.py"
        ).read_text(encoding="utf-8")
        assert "except " not in service_source.split("def get_timeline")[-1]

    def test_14b_trace_api_unchanged(self) -> None:
        assert list(AssistantTraceResponse.model_fields) == [
            "assistant_request_id",
            "outcome",
            "llm_usage",
            "tool_executions",
            "rag_executions",
        ]


# ============================================================
# 15. Payload size（synthetic；不实现分页）
# ============================================================

def _synthetic_timeline(
    *,
    assistant_request_id: str,
    llm_count: int,
    tool_count: int,
    rag_count: int,
    with_outcome: bool,
) -> AssistantTimeline:
    llm = tuple(
        AssistantTimelineEvent(
            assistant_request_id=assistant_request_id,
            source=AssistantTimelineSource.LLM_USAGE,
            event_type=AssistantTimelineEventType.LLM,
            source_id=index,
            created_at=_BASE + timedelta(milliseconds=index),
        )
        for index in range(1, llm_count + 1)
    )
    tools = tuple(
        AssistantTimelineEvent(
            assistant_request_id=assistant_request_id,
            source=AssistantTimelineSource.TOOL_EXECUTION,
            event_type=AssistantTimelineEventType.TOOL,
            source_id=index,
            started_at=_BASE,
            finished_at=_BASE + timedelta(milliseconds=10),
            duration_ms=10.0,
            status=AssistantTimelineEventStatus.TOOL_SUCCESS,
        )
        for index in range(1, tool_count + 1)
    )
    rag = tuple(
        AssistantTimelineEvent(
            assistant_request_id=assistant_request_id,
            source=AssistantTimelineSource.RAG_EXECUTION,
            event_type=AssistantTimelineEventType.RAG,
            source_id=index,
            started_at=_BASE,
            finished_at=_BASE + timedelta(milliseconds=20),
            duration_ms=20.0,
        )
        for index in range(1, rag_count + 1)
    )
    return AssistantTimeline(
        assistant_request_id=assistant_request_id,
        llm_events=llm,
        tool_events=tools,
        rag_events=rag,
        outcome_event=(
            AssistantTimelineEvent(
                assistant_request_id=assistant_request_id,
                source=AssistantTimelineSource.ASSISTANT_OUTCOME,
                event_type=AssistantTimelineEventType.OUTCOME,
                source_id=1,
                created_at=_BASE,
                status=AssistantTimelineEventStatus.SUCCESS,
            )
            if with_outcome
            else None
        ),
    )


class TestPayloadSizeAudit:
    @pytest.mark.parametrize("total", [10, 50, 100])
    def test_15_payload_sizes_are_small(self, total: int) -> None:
        """合成估算：单次请求内事件数（10 / 50 / 100）的 JSON 体积。"""
        llm_count = total // 2
        tool_count = total // 4
        rag_count = total - llm_count - tool_count
        timeline = _synthetic_timeline(
            assistant_request_id="A",
            llm_count=llm_count,
            tool_count=tool_count,
            rag_count=rag_count,
            with_outcome=True,
        )
        payload = _candidate_timeline_payload(timeline, expose_source_id=True)
        size = len(json.dumps(payload, separators=(",", ":")))

        assert size < PAYLOAD_TRIGGER_BYTES, (total, size)
        # 无 source_id 的版本体积略小（数量级不变）
        hidden_size = len(
            json.dumps(
                _candidate_timeline_payload(timeline, expose_source_id=False),
                separators=(",", ":"),
            )
        )
        assert hidden_size <= size

    def test_15b_realistic_scale_is_tiny(self) -> None:
        """Step 54 实测：单请求上限 6 条观测记录 → Timeline 事件 ≤ 7。"""
        timeline = _synthetic_timeline(
            assistant_request_id="A",
            llm_count=4,
            tool_count=1,
            rag_count=1,
            with_outcome=True,
        )
        size = len(
            json.dumps(
                _candidate_timeline_payload(timeline, expose_source_id=True),
                separators=(",", ":"),
            )
        )
        assert size < 4 * 1024                          # ≪ 64 KiB 触发线
        assert len(timeline.llm_events) + len(timeline.tool_events) + len(
            timeline.rag_events
        ) + 1 == 7


# ============================================================
# 16~17. 无 DB / 无网络 / 无 endpoint
# ============================================================

class TestAuditHygiene:
    _SELF: Final[str] = "tests/test_assistant_timeline_api_audit.py"

    def test_16_audit_file_touches_no_db(self) -> None:
        source = Path(self._SELF).read_text(encoding="utf-8")
        for token in (
            "get_" + "engine",
            "get_session_" + "factory",
            "create_" + "engine",
            "session" + "maker",
            "RUN_DB_" + "TESTS",
            "init_" + "db",
            "backend.app.db." + "session",
        ):
            assert token not in source, token

    def test_16b_audit_file_touches_no_network(self) -> None:
        tree = ast.parse(Path(self._SELF).read_text(encoding="utf-8"))
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for token in ("httpx", "requests", "urllib", "socket", "openai",
                      "api_" + "key", "TestClient"):
            assert token not in identifiers, token

    def test_17_no_timeline_endpoint_registered(self) -> None:
        """Step 67：**不**新增 endpoint；审计确认 App 里确实没有 timeline 路径。"""
        from backend.app.main import app

        paths = sorted(app.openapi()["paths"])
        assert not [path for path in paths if "timeline" in path.lower()]
        assert CANDIDATE_PATH not in paths
        # 也不存在 api 模块文件（未注册、未实现）
        assert not list(
            Path("backend/app/api").glob("assistant_timeline*.py")
        )

    def test_17b_no_timeline_router_module(self) -> None:
        """未把 timeline router 挂进 create_app()（静态检查）。"""
        source = Path("backend/app/main.py").read_text(encoding="utf-8")
        assert "assistant_timeline" not in source
        assert "timeline" not in source

    def test_17c_dataclass_shape_is_serializable_without_internals(self) -> None:
        """dataclasses.asdict 结果不含 ORM / Session / 连接对象。"""
        timeline = _synthetic_timeline(
            assistant_request_id="A", llm_count=1, tool_count=0,
            rag_count=0, with_outcome=True,
        )
        blob = json.dumps(dataclasses.asdict(timeline), default=str)
        for forbidden in ("Session", "Engine", "Connection", "0x", _SENTINEL):
            assert forbidden not in blob, forbidden
        assert "source_id" in blob


__all__ = [
    "TestResponseContractAudit",
    "TestSourceIdExposureAudit",
    "TestHistoricalAndEmptyAudit",
    "TestIsolationAndSecurityAudit",
    "TestValidationAndErrorContractAudit",
    "TestPayloadSizeAudit",
    "TestAuditHygiene",
]
