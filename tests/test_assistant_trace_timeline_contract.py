"""Trace / Timeline **Contract Baseline 回归守卫**（Phase 3.12 Step 73）。

固化来源：`docs/evaluation/phase-3.12-trace-timeline-contract-baseline.md`
（Step 64 → 72 已实测行为）。

本文件性质：

    * **纯 offline**：DB = 0 · network = 0（不连接 PostgreSQL / 不创建真实 request）；
    * 只断言 **DTO / 路由 / 文档 已固化的契约**（不含业务实现细节）；
    * 任何"新增身份字段 / 分页字段 / 跨组排序 / 敏感字段"都会使本文件失败
      —— 这是**有意的漂移报警**。

边界（Step 73 §十三）：本文件自身 executable import 不得出现
``sqlalchemy`` / ``psycopg`` / ``redis`` / ``celery`` / ``kafka`` / ``backend.app.db``
（由 ``test_self_import_audit`` 用 AST 校验）。
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.app.api.assistant_timeline import router as timeline_router
from backend.app.api.assistant_trace import (
    AssistantTraceResponse,
    router as trace_router,
)
from backend.app.dto.assistant_outcome import AssistantOutcome
from backend.app.dto.assistant_timeline import (
    ASSISTANT_TIMELINE_EVENT_FIELDS,
    ASSISTANT_TIMELINE_FIELDS,
    AssistantTimeline,
    AssistantTimelineEvent,
    AssistantTimelineEventStatus,
    AssistantTimelineEventType,
    AssistantTimelineSource,
)
from backend.app.dto.assistant_timeline_api import (
    TIMELINE_EVENT_RESPONSE_FIELDS,
    TIMELINE_RESPONSE_FIELDS,
    AssistantTimelineEventResponse,
    AssistantTimelineResponse,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_assistant_trace_timeline_contract.py"
_BASELINE_DOC = "docs/evaluation/phase-3.12-trace-timeline-contract-baseline.md"
_TIMELINE_SERVICE = "backend/app/services/assistant_timeline_query_service.py"
_TIMELINE_PATH = "/observability/assistant-timeline/{assistant_request_id}"
_TRACE_PATH = "/observability/assistant-trace/{assistant_request_id}"

#: 严禁出现的身份 / 顺序字段（Step 65 明确 NOT AVAILABLE）。
FORBIDDEN_IDENTITY_FIELDS: tuple[str, ...] = (
    "event_id",
    "sequence",
    "seq",
    "span_id",
    "parent_event_id",
    "trace_id",
    "order",
    "index",
    "attempt",
    "transaction_id",
)

#: 严禁出现的分页字段（Step 59/67：Pagination = DEFER）。
FORBIDDEN_PAGINATION_FIELDS: tuple[str, ...] = (
    "limit",
    "offset",
    "cursor",
    "page",
    "page_size",
    "total",
    "has_more",
)

#: 严禁出现的敏感字段名（Step 71/72 安全契约）。
FORBIDDEN_SENSITIVE_FIELDS: tuple[str, ...] = (
    "prompt",
    "messages",
    "system_prompt",
    "user_prompt",
    "sql",
    "query",
    "content",
    "answer",
    "embedding",
    "similarity",
    "arguments",
    "tool_result",
    "raw_response",
    "exception",
    "error_message",
    "stacktrace",
    "database_url",
    "api_key",
    "authorization",
    "password",
)


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _schema_properties(model: type) -> dict[str, Any]:
    """模型 JSON Schema 的顶层 properties（offline；不启动 app）。"""
    return model.model_json_schema().get("properties", {})


def _all_property_names(model: type) -> set[str]:
    schema = model.model_json_schema()
    names: set[str] = set()
    for key, value in schema.get("properties", {}).items():
        names.add(key)
    for definition in schema.get("$defs", {}).values():
        names |= set(definition.get("properties", {}))
    return names


def _llm_event(**overrides: Any) -> AssistantTimelineEvent:
    payload: dict[str, Any] = {
        "assistant_request_id": "A",
        "source": AssistantTimelineSource.LLM_USAGE,
        "event_type": AssistantTimelineEventType.LLM,
        "source_id": 1,
        "created_at": datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
    }
    payload.update(overrides)
    return AssistantTimelineEvent(**payload)


def _tool_event(**overrides: Any) -> AssistantTimelineEvent:
    base = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    payload: dict[str, Any] = {
        "assistant_request_id": "A",
        "source": AssistantTimelineSource.TOOL_EXECUTION,
        "event_type": AssistantTimelineEventType.TOOL,
        "source_id": 2,
        "started_at": base,
        "finished_at": base + timedelta(milliseconds=10),
        "duration_ms": 10.0,
        "status": AssistantTimelineEventStatus.TOOL_SUCCESS,
    }
    payload.update(overrides)
    return AssistantTimelineEvent(**payload)


# ============================================================
# 1 ~ 3：顶层 / 事件字段集合
# ============================================================

class TestFieldContract:
    def test_1_timeline_top_level_fields(self) -> None:
        assert TIMELINE_RESPONSE_FIELDS == (
            "assistant_request_id",
            "llm_events",
            "tool_events",
            "rag_events",
            "outcome_event",
        )
        assert tuple(AssistantTimelineResponse.model_fields) == (
            TIMELINE_RESPONSE_FIELDS
        )
        assert tuple(AssistantTimeline.__dataclass_fields__) == (
            ASSISTANT_TIMELINE_FIELDS
        )

    def test_2_timeline_event_fields(self) -> None:
        assert TIMELINE_EVENT_RESPONSE_FIELDS == (
            "assistant_request_id",
            "source",
            "event_type",
            "source_id",
            "started_at",
            "finished_at",
            "duration_ms",
            "created_at",
            "status",
        )
        assert tuple(AssistantTimelineEventResponse.model_fields) == (
            TIMELINE_EVENT_RESPONSE_FIELDS
        )
        assert tuple(AssistantTimelineEvent.__dataclass_fields__) == (
            ASSISTANT_TIMELINE_EVENT_FIELDS
        )
        assert len(TIMELINE_EVENT_RESPONSE_FIELDS) == 9

    def test_3_trace_top_level_fields(self) -> None:
        assert list(AssistantTraceResponse.model_fields) == [
            "assistant_request_id",
            "outcome",
            "llm_usage",
            "tool_executions",
            "rag_executions",
        ]

    def test_3b_api_dto_is_separate_pydantic_layer(self) -> None:
        """API DTO（Pydantic）≠ 内部读模型（frozen dataclass）；字段过滤边界存在。"""
        from pydantic import BaseModel

        assert issubclass(AssistantTimelineResponse, BaseModel)
        assert issubclass(AssistantTimelineEventResponse, BaseModel)
        assert AssistantTimeline.__dataclass_params__.frozen is True
        assert AssistantTimelineEvent.__dataclass_params__.frozen is True
        assert not isinstance(AssistantTimeline(assistant_request_id="A"), BaseModel)


# ============================================================
# 4 ~ 5：Outcome 枚举 / source_id 类型
# ============================================================

class TestOutcomeAndSourceId:
    def test_4_outcome_enum_values(self) -> None:
        assert [member.value for member in AssistantOutcome] == [
            "SUCCESS",
            "EMPTY",
            "REFUSED",
            "FAILED",
        ]
        # 无 error_class（Step 62/64 明确不做）
        for forbidden in (
            "LLM_ERROR",
            "RAG_ERROR",
            "TOOL_ERROR",
            "TEXT_TO_SQL_ERROR",
            "SQL_EXECUTION_ERROR",
            "RETRY",
        ):
            assert forbidden not in AssistantOutcome.__members__
        # Timeline 事件 status 取值 = 四态 + Tool 的 success/failed
        assert {member.value for member in AssistantTimelineEventStatus} == {
            "SUCCESS",
            "EMPTY",
            "REFUSED",
            "FAILED",
            "success",
            "failed",
        }

    def test_5_source_id_is_real_primary_key_int(self) -> None:
        assert _llm_event(source_id=123).source_id == 123
        assert _tool_event(source_id=456).source_id == 456
        for bad in ("123", 1.0, True, None):
            with pytest.raises(ValueError):
                _llm_event(source_id=bad)
        annotation = AssistantTimelineEvent.__dataclass_fields__[
            "source_id"
        ].type
        assert annotation in {int, "int"}

    def test_5b_outcome_is_nullable_and_not_inferred(self) -> None:
        """无终态记录 → outcome_event=None 合法（partial / historical）。"""
        timeline = AssistantTimeline(
            assistant_request_id="A", llm_events=(_llm_event(),)
        )
        assert timeline.outcome_event is None
        assert timeline.is_empty() is False
        assert (
            AssistantTimeline(assistant_request_id="A").is_empty() is True
        )
        assert AssistantTimelineEventResponse.model_fields[
            "status"
        ].default is None


# ============================================================
# 6 ~ 7：禁止字段（身份 / 分页 / 敏感）
# ============================================================

class TestForbiddenFields:
    def test_6_absence_of_identity_fields(self) -> None:
        field_sets = (
            set(AssistantTimelineEvent.__dataclass_fields__),
            set(AssistantTimeline.__dataclass_fields__),
            set(AssistantTimelineEventResponse.model_fields),
            set(AssistantTimelineResponse.model_fields),
            set(AssistantTraceResponse.model_fields),
            _all_property_names(AssistantTimelineResponse),
            _all_property_names(AssistantTraceResponse),
        )
        for fields in field_sets:
            for forbidden in FORBIDDEN_IDENTITY_FIELDS:
                assert forbidden not in fields, forbidden

    def test_7_absence_of_pagination_fields(self) -> None:
        field_sets = (
            _all_property_names(AssistantTimelineResponse),
            _all_property_names(AssistantTraceResponse),
            set(AssistantTimeline.__dataclass_fields__),
        )
        for fields in field_sets:
            for forbidden in FORBIDDEN_PAGINATION_FIELDS:
                assert forbidden not in fields, forbidden
        # 路由不得声明分页查询参数
        route = _find_route(timeline_router, _TIMELINE_PATH)
        assert route.dependant.query_params == []

    def test_8_absence_of_sensitive_field_names(self) -> None:
        names = _all_property_names(AssistantTimelineResponse)
        names |= _all_property_names(AssistantTraceResponse)
        names |= set(AssistantTimelineEventResponse.model_fields)
        for forbidden in FORBIDDEN_SENSITIVE_FIELDS:
            assert forbidden not in names, forbidden

    def test_8b_payload_has_no_sensitive_content(self) -> None:
        payload = AssistantTimelineResponse(
            assistant_request_id="A",
            llm_events=[AssistantTimelineEventResponse(
                assistant_request_id="A",
                source="llm_usage",
                event_type="LLM",
                source_id=1,
            )],
        ).model_dump(mode="json")
        blob = json.dumps(payload, ensure_ascii=False)

        for forbidden in FORBIDDEN_SENSITIVE_FIELDS:
            assert f'"{forbidden}":' not in blob, forbidden
        for sentinel in ("postgresql://", "sk-", "Bearer", "Traceback"):
            assert sentinel not in blob, sentinel


# ============================================================
# 8 ~ 11：partial / 钟域 / 路由 / ordering
# ============================================================

class TestBehaviorContract:
    def test_9_partial_state_is_legal(self) -> None:
        # LLM-only（仅 created_at）
        llm = _llm_event()
        assert llm.created_at is not None
        assert llm.started_at is None
        assert llm.finished_at is None
        assert llm.duration_ms is None
        # Tool-only（仅 App 钟三件套）
        tool = _tool_event()
        assert tool.created_at is None
        assert tool.started_at is not None
        assert tool.finished_at is not None
        assert tool.duration_ms is not None
        # 两个视图允许"数量不同"的 partial 语义在 HTTP 契约层不表达顺序
        timeline = AssistantTimeline(
            assistant_request_id="A", llm_events=(llm,), tool_events=(tool,)
        )
        assert len(timeline.llm_events) == 1 and len(timeline.tool_events) == 1

    def test_9b_clock_domains_are_exclusive(self) -> None:
        from datetime import datetime, timezone

        base = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError):
            _llm_event(started_at=base)          # DB 钟 + App 钟 同时出现
        with pytest.raises(ValueError):
            _tool_event(created_at=base)         # 反向同样禁止
        # created_at 不是 finished_at 的别名（字段独立存在）
        fields = set(AssistantTimelineEvent.__dataclass_fields__)
        assert {"created_at", "started_at", "finished_at"} <= fields

    def test_10_timeline_route_contract(self) -> None:
        route = _find_route(timeline_router, _TIMELINE_PATH)

        assert set(route.methods) == {"GET"}
        declared = set(route.responses)
        assert {200, 400, 422, 502, 500} <= declared
        assert 404 not in declared                  # unknown request ≠ 404
        assert route.dependant.path_params[0].name == "assistant_request_id"
        assert "{" not in _TIMELINE_PATH.replace(
            "{assistant_request_id}", ""
        )

    def test_10b_trace_route_unchanged(self) -> None:
        route = _find_route(trace_router, _TRACE_PATH)

        assert set(route.methods) == {"GET"}
        assert 200 in route.responses
        assert route.dependant.path_params[0].name == "assistant_request_id"

    def test_11_ordering_contract_is_group_local(self) -> None:
        source = _source(_TIMELINE_SERVICE)
        tree = ast.parse(source)

        # 只做**组内**排序（3 次：llm / tool / rag）
        assert source.count("sorted(") == 3
        # 标识符级：不伪造身份 / 不生成全局序 / 不枚举下标
        #（docstring 中说明"不生成 sequence"不算违规，故按 AST 标识符判定）
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "enumerate",
            "uuid4",
            "uuid1",
            "hash",
            "sequence",
            "event_id",
            "span_id",
        ):
            assert forbidden not in identifiers, forbidden
        # 调用级：无 uuid / hash 生成
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "enumerate" not in calls
        assert "uuid4" not in calls

    def test_11b_baseline_document_declares_limits(self) -> None:
        doc = _source(_BASELINE_DOC)

        for marker in (
            "## 1. Contract Matrix",
            "## 3. Partial State Contract",
            "## 5. Source ID Contract",
            "## 6. Ordering Contract",
            "## 7. Read During Write Contract",
            "## 8. Security Contract",
            "NOT PROVIDED",
            "200 + 四段空",
            "HTTP 200 ≠ 完整最终快照",
            "不提供跨 LLM / Tool / RAG / Outcome 的全局时间排序",
            "first-write-wins",
            "event_id",
            "sequence",
            "span_id",
            "pagination",
            "transaction snapshot",
        ):
            assert marker in doc, marker


# ============================================================
# 12：自身 import 的 AST 审计（§十三）
# ============================================================

class TestSelfAudit:
    _FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
        "sqlalchemy",
        "psycopg",
        "psycopg2",
        "alembic",
        "redis",
        "celery",
        "kafka",
        "backend.app.db",
    )

    def _imported_modules(self) -> set[str]:
        tree = ast.parse(_source(_SELF))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    def test_12_no_db_or_infra_imports(self) -> None:
        modules = self._imported_modules()

        assert modules, "AST 未解析到 import（审计失效）"
        for module in modules:
            for prefix in self._FORBIDDEN_IMPORT_PREFIXES:
                assert not module.startswith(prefix), module

    def test_12b_no_db_engine_access_at_runtime(self) -> None:
        """本文件**不**调用 get_engine / Session / create_engine（DB = 0）。"""
        tree = ast.parse(_source(_SELF))
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "get_" + "engine",
            "get_session_" + "factory",
            "create_" + "engine",
            "session" + "maker",
            "Session",
        ):
            assert forbidden not in identifiers, forbidden


def _find_route(router: Any, path: str) -> Any:
    """按路径取 APIRoute（离线；不启动 ASGI app）。"""
    for route in router.routes:
        if getattr(route, "path", None) == path:
            return route
    raise AssertionError(f"route not found: {path}")


__all__ = [
    "TestFieldContract",
    "TestOutcomeAndSourceId",
    "TestForbiddenFields",
    "TestBehaviorContract",
    "TestSelfAudit",
]
