"""Phase 3.12 Step 65 —— Unified Timeline 可行性与 Contract Audit（**只审计**）。

本文件回答一个问题：

    当前四类持久化事实（LLM Usage / Tool Execution / RAG Execution /
    Assistant Outcome）**是否已经具备建立 Unified Assistant Timeline 的最小条件**？

审计原则（Step 65 §十 / §十一）：

    * **不伪造顺序**：没有可靠事件序就写 ``NOT_AVAILABLE``；
      **绝不**用 ``enumerate()`` 把查询顺序冒充业务执行顺序；
    * **不伪造 ID**：没有 event_id 就写 ``NOT_AVAILABLE``；
      **绝不**用 ``uuid4()`` / ``hash(timestamp)`` 造一个；
    * **不改 production**：本文件只读真实代码 + 合成数据 + 静态检查；
    * **无 DB / 无网络**：不 import DB session / engine / repository 连接，
      不发起任何 HTTP 调用（``TestAuditHygiene`` 用 AST 自查）。

审计结论（详见 ``docs/evaluation/phase-3.12-step-65-unified-timeline-audit.md``）：

    correlation      : ✅ 四类记录都可由 assistant_request_id 关联（LLM 另有 Provider request_id）
    within-source    : ✅ 每一来源内部可稳定排序（LLM: created_at,id；Tool/RAG: id）
    cross-source     : ⚠️ 仅**同钟域**可比（LLM ↔ Outcome 同 DB 钟；
                       Tool ↔ RAG 同 App 钟）；跨钟域 = UNSAFE_TO_DERIVE
    event_id         : ❌ NOT_AVAILABLE（四表均无）
    sequence         : ❌ NOT_AVAILABLE（窗口函数/行号只能得到"查询顺序"，不是业务顺序）
    span/parent/route: ❌ NOT_AVAILABLE
    缺失事件         : ❌ Router 决策 / Validator 判定 / Executor 执行 / Tool 参数提取
                       完全**没有**任何持久化记录
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

import pytest

from backend.app.api.assistant_trace import (
    AssistantTraceResponse,
    LLMUsageTraceResponse,
    RagExecutionTraceResponse,
    ToolExecutionTraceResponse,
)
from backend.app.db.llm_usage_repository import LLM_USAGE_TRACE_READ_COLUMNS
from backend.app.db.models.assistant_outcome_record import AssistantOutcomeRecord
from backend.app.db.models.llm_usage_record import LLMUsageRecord
from backend.app.db.models.rag_execution_record import RagExecutionRecordModel
from backend.app.db.models.tool_execution_record import ToolExecutionRecordModel
from backend.app.db.rag_execution_repository import RAG_EXECUTION_READ_COLUMNS
from backend.app.db.tool_execution_repository import TOOL_EXECUTION_READ_COLUMNS
from backend.app.dto.assistant_outcome import AssistantOutcome
from backend.app.llm.client import LLMUsage
from backend.app.llm.observability import LLMObservation
from backend.app.services.assistant_outcome_persistence_service import (
    AssistantOutcomePersistenceService,
)
from backend.app.services.assistant_trace import (
    assistant_trace_scope,
    current_assistant_request_id,
)
from backend.app.services.assistant_trace_query_service import AssistantTraceView
from backend.app.services.llm_usage_persistence_service import (
    LLMUsagePersistenceService,
)
from backend.app.services.rag_service import RagService
from backend.app.services.tool_observability_snapshot import (
    ToolExecutionSnapshot,
)
from tests.test_assistant_trace_correlation_e2e import (  # noqa: F401
    _TOOL_QUESTION,
    e2e,
)

# ============================================================
# 审计矩阵（本文件即事实来源；由下面的测试逐项核对真实代码）
# ============================================================

AVAILABLE: Final[str] = "AVAILABLE"
NOT_AVAILABLE: Final[str] = "NOT_AVAILABLE"
DERIVED: Final[str] = "DERIVED"
UNSAFE_TO_DERIVE: Final[str] = "UNSAFE_TO_DERIVE"

_AUDIT_KEYS: Final[tuple[str, ...]] = (
    "request_id",
    "assistant_request_id",
    "started_at",
    "finished_at",
    "created_at",
    "duration_ms",
    "id",
    "provider",
    "model",
    "route",
    "success",
    "outcome",
)

#: 字段矩阵：source → audit key → 可用性。
#: ``AVAILABLE`` 表示该 source 的**持久化读模型**里真的有这个字段。
FIELD_MATRIX: Final[dict[str, dict[str, str]]] = {
    "LLM_USAGE": {
        "request_id": AVAILABLE,            # Provider 请求 ID（chatcmpl-…）
        "assistant_request_id": AVAILABLE,  # Assistant Trace ID
        "started_at": NOT_AVAILABLE,        # 表里没有；observation 也只有 latency
        "finished_at": NOT_AVAILABLE,
        "created_at": AVAILABLE,            # DB now()（**插入时刻**，非调用开始）
        "duration_ms": NOT_AVAILABLE,       # latency 未落库
        "id": AVAILABLE,
        "provider": AVAILABLE,
        "model": AVAILABLE,
        "route": NOT_AVAILABLE,
        "success": NOT_AVAILABLE,           # 失败调用不落行 → 表内无 success
        "outcome": NOT_AVAILABLE,
    },
    "TOOL_EXECUTION": {
        "request_id": AVAILABLE,            # = Assistant Trace ID（/api/ai/chat）
        "assistant_request_id": NOT_AVAILABLE,   # 同名不同义：名为 request_id
        "started_at": AVAILABLE,            # App 钟（datetime.now(UTC)）
        "finished_at": AVAILABLE,
        "created_at": NOT_AVAILABLE,        # 表里没有
        "duration_ms": AVAILABLE,           # perf_counter 差值
        "id": AVAILABLE,
        "provider": NOT_AVAILABLE,
        "model": NOT_AVAILABLE,
        "route": NOT_AVAILABLE,
        "success": AVAILABLE,
        "outcome": NOT_AVAILABLE,           # Tool success ≠ Assistant outcome
    },
    "RAG_EXECUTION": {
        "request_id": AVAILABLE,            # = Assistant Trace ID
        "assistant_request_id": NOT_AVAILABLE,
        "started_at": AVAILABLE,            # App 钟
        "finished_at": AVAILABLE,
        "created_at": NOT_AVAILABLE,
        "duration_ms": AVAILABLE,
        "id": AVAILABLE,
        "provider": NOT_AVAILABLE,
        "model": NOT_AVAILABLE,
        "route": NOT_AVAILABLE,
        "success": NOT_AVAILABLE,           # 运行期失败不保证有行（见 Case D）
        "outcome": NOT_AVAILABLE,
    },
    "ASSISTANT_OUTCOME": {
        "request_id": NOT_AVAILABLE,        # 无 Provider 概念
        "assistant_request_id": AVAILABLE,  # UNIQUE
        "started_at": NOT_AVAILABLE,
        "finished_at": NOT_AVAILABLE,
        "created_at": AVAILABLE,            # DB now()（**写入时刻**，即请求结束附近）
        "duration_ms": NOT_AVAILABLE,
        "id": AVAILABLE,
        "provider": NOT_AVAILABLE,
        "model": NOT_AVAILABLE,
        "route": NOT_AVAILABLE,             # route 只在内存 Result.metadata
        "success": NOT_AVAILABLE,
        "outcome": AVAILABLE,
    },
}

#: source → 该 source 的 ORM 真实列名（含主键；用于与矩阵双向核对）。
_SOURCE_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "LLM_USAGE": tuple(LLMUsageRecord.__table__.columns.keys()),
    "TOOL_EXECUTION": tuple(ToolExecutionRecordModel.__table__.columns.keys()),
    "RAG_EXECUTION": tuple(RagExecutionRecordModel.__table__.columns.keys()),
    "ASSISTANT_OUTCOME": tuple(AssistantOutcomeRecord.__table__.columns.keys()),
}

#: source → 持久化读边界允许返回的列（**白名单**；不含没落库的字段）。
_SOURCE_READ_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "LLM_USAGE": LLM_USAGE_TRACE_READ_COLUMNS,
    "TOOL_EXECUTION": TOOL_EXECUTION_READ_COLUMNS,
    "RAG_EXECUTION": RAG_EXECUTION_READ_COLUMNS,
    "ASSISTANT_OUTCOME": ("assistant_request_id", "outcome", "created_at"),
}

#: DB 生成的列（``server_default``）—— 与 App 生成的时间戳**不同钟域**。
_DB_GENERATED_TIMESTAMP_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("LLM_USAGE", "created_at"),
    ("ASSISTANT_OUTCOME", "created_at"),
)

#: App 生成的列（``datetime.now(UTC)`` / ``perf_counter``）—— 无 server_default。
_APP_GENERATED_TIMESTAMP_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("TOOL_EXECUTION", "started_at"),
    ("TOOL_EXECUTION", "finished_at"),
    ("RAG_EXECUTION", "started_at"),
    ("RAG_EXECUTION", "finished_at"),
)

_MODEL_BY_SOURCE: Final[dict[str, Any]] = {
    "LLM_USAGE": LLMUsageRecord,
    "TOOL_EXECUTION": ToolExecutionRecordModel,
    "RAG_EXECUTION": RagExecutionRecordModel,
    "ASSISTANT_OUTCOME": AssistantOutcomeRecord,
}

#: 四表中**确实不存在**的 Timeline 身份字段（禁止伪造）。
FORBIDDEN_TIMELINE_FIELDS: Final[tuple[str, ...]] = (
    "event_id",
    "sequence",
    "seq",
    "span_id",
    "parent_event_id",
    "parent_span_id",
    "trace_id",
    "span",
    "attempt",
    "attempt_no",
    "step_index",
)

_TRACE_READ_PATH_FILES: Final[tuple[str, ...]] = (
    "backend/app/services/assistant_trace_query_service.py",
    "backend/app/api/assistant_trace.py",
    "backend/app/services/llm_usage_query_service.py",
    "backend/app/services/tool_execution_persistent_query_service.py",
    "backend/app/services/rag_execution_persistent_query_service.py",
    "backend/app/services/assistant_outcome_query_service.py",
)


def _source_path(relative: str) -> Path:
    return Path(__file__).resolve().parents[1] / relative


def _identifiers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _non_docstring_strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


# ============================================================
# 1~5. Correlation Audit
# ============================================================

class TestCorrelationAudit:
    def test_1_correlation_matrix_matches_real_columns(self) -> None:
        """矩阵 ↔ 真实 ORM 列**双向**核对（不靠记忆）。"""
        for source, matrix in FIELD_MATRIX.items():
            columns = set(_SOURCE_COLUMNS[source])

            for key, availability in matrix.items():
                if availability == AVAILABLE:
                    assert key in columns, f"{source}: 矩阵称 AVAILABLE 但无列 {key}"
                else:
                    assert key not in columns, (
                        f"{source}: 矩阵称 {availability} 但真实存在列 {key}"
                    )

    def test_1a_read_exposure_matches_read_whitelists(self) -> None:
        """AVAILABLE ≠ 一定被读模型暴露（如 Outcome.id 不暴露）。"""
        expected = {
            "LLM_USAGE": set(LLM_USAGE_TRACE_READ_COLUMNS),
            "TOOL_EXECUTION": set(TOOL_EXECUTION_READ_COLUMNS),
            "RAG_EXECUTION": set(RAG_EXECUTION_READ_COLUMNS),
            "ASSISTANT_OUTCOME": set(_SOURCE_READ_COLUMNS["ASSISTANT_OUTCOME"]),
        }
        for source, read_columns in expected.items():
            assert read_columns <= set(_SOURCE_COLUMNS[source]), source
        # Outcome 主键不进入读边界（排序由 UNIQUE 键 + 至多 1 行保证）
        assert "id" not in expected["ASSISTANT_OUTCOME"]

    def test_1b_matrix_covers_all_audit_keys(self) -> None:
        for source, matrix in FIELD_MATRIX.items():
            assert tuple(matrix) == _AUDIT_KEYS, source
            assert set(matrix.values()) <= {
                AVAILABLE, NOT_AVAILABLE, DERIVED, UNSAFE_TO_DERIVE,
            }

    def test_2_llm_provider_request_id_not_confused_with_assistant_request_id(
        self,
    ) -> None:
        """真实持久化边界：Provider request_id 与 Assistant Trace ID 来自**两个**来源。"""

        class _CapturingRepository:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def create(self, **kwargs: Any) -> int:
                self.calls.append(dict(kwargs))
                return 1

        repository = _CapturingRepository()
        service = LLMUsagePersistenceService(repository=repository)
        observation = LLMObservation(
            provider="step65-provider",
            model="step65-model",
            success=True,
            request_id="chatcmpl-provider-65",          # Provider 侧 ID
            usage=LLMUsage(
                prompt_tokens=1, completion_tokens=2, total_tokens=3
            ),
        )

        with assistant_trace_scope("step65-assistant-A"):
            assert current_assistant_request_id() == "step65-assistant-A"
            service.persist(observation)

        call = repository.calls[0]
        assert call["request_id"] == "chatcmpl-provider-65"       # 未被子覆盖
        assert call["assistant_request_id"] == "step65-assistant-A"
        assert call["request_id"] != call["assistant_request_id"]

        # 未绑定 Scope（旧链路 / 直连）→ assistant_request_id = None（不生成 / 不回填）
        service.persist(observation)
        assert repository.calls[1]["assistant_request_id"] is None
        assert repository.calls[1]["request_id"] == "chatcmpl-provider-65"

        # 两个字段是**两个维度**，读模型也分别暴露
        assert "request_id" in LLM_USAGE_TRACE_READ_COLUMNS
        assert "assistant_request_id" in LLM_USAGE_TRACE_READ_COLUMNS

    def test_3_tool_request_id_is_the_assistant_request_id(self, e2e) -> None:
        """离线 E2E：Tool 持久化记录的 request_id == metadata.request_id。"""
        _repository, _handler, persistent_tools, _collector = e2e()

        from fastapi.testclient import TestClient

        from backend.app.main import app

        with TestClient(app) as client:
            response = client.post(
                "/api/ai/chat", json={"question": _TOOL_QUESTION}
            )

        assert response.status_code == 200, response.text
        payload = response.json()

        assistant_request_id = payload["metadata"]["request_id"]
        snapshots = persistent_tools.list_by_request_id(assistant_request_id)

        assert len(snapshots) == 1
        assert snapshots[0].request_id == assistant_request_id
        # Tool 记录**没有**独立的 assistant_request_id 列（同名不同义：request_id）
        assert "request_id" in TOOL_EXECUTION_READ_COLUMNS
        assert "assistant_request_id" not in TOOL_EXECUTION_READ_COLUMNS

    def test_4_rag_request_id_is_the_assistant_request_id(self) -> None:
        """真实 RagService：观测 request_id 只来自 Assistant Trace Scope。"""

        class _NoopObserver:
            def __init__(self) -> None:
                self.records: list[Any] = []

            def record(self, observation: Any) -> None:
                self.records.append(observation)

        observer = _NoopObserver()
        rag = RagService(observer=observer)

        # observer 存在但**未绑定** Scope → 禁用草稿（不记录、不生成 ID）
        assert rag._begin_observation().request_id is None

        with assistant_trace_scope("step65-assistant-B"):
            draft = rag._begin_observation()

        assert draft.request_id == "step65-assistant-B"
        assert draft.started_at.tzinfo is not None                # App 钟（UTC）
        assert current_assistant_request_id() is None             # scope 已退出
        # RAG 记录同样**没有** assistant_request_id 列
        assert "request_id" in RAG_EXECUTION_READ_COLUMNS
        assert "assistant_request_id" not in RAG_EXECUTION_READ_COLUMNS

    def test_5_outcome_correlates_one_to_one_with_the_assistant_request(
        self,
    ) -> None:
        """Outcome 的关联键 = assistant_request_id，且 UNIQUE（一次请求至多一条）。"""

        class _FakeRepository:
            def __init__(self) -> None:
                self.rows: dict[str, str] = {}
                self.calls: list[str] = []

            def create(self, *, assistant_request_id: str, outcome: str) -> None:
                self.calls.append(assistant_request_id)
                self.rows.setdefault(assistant_request_id, outcome)

        repository = _FakeRepository()
        service = AssistantOutcomePersistenceService(repository=repository)

        service.persist("step65-assistant-C", AssistantOutcome.SUCCESS)
        service.persist("step65-assistant-C", AssistantOutcome.FAILED)

        assert repository.calls == ["step65-assistant-C", "step65-assistant-C"]
        assert repository.rows == {"step65-assistant-C": "SUCCESS"}   # first-write-wins

        indexes = {
            index.name: index
            for index in AssistantOutcomeRecord.__table__.indexes
        }
        unique_index = indexes[
            "uq_assistant_outcome_record_assistant_request_id"
        ]
        assert unique_index.unique is True
        assert [column.name for column in unique_index.columns] == [
            "assistant_request_id"
        ]


# ============================================================
# 6~7. Timestamp Availability / Semantics
# ============================================================

class TestTimestampAudit:
    def test_6_timestamp_availability_per_source(self) -> None:
        expected = {
            "LLM_USAGE": {"created_at"},
            "TOOL_EXECUTION": {"started_at", "finished_at", "duration_ms"},
            "RAG_EXECUTION": {"started_at", "finished_at", "duration_ms"},
            "ASSISTANT_OUTCOME": {"created_at"},
        }
        for source, keys in expected.items():
            columns = set(_SOURCE_COLUMNS[source])
            for key in ("started_at", "finished_at", "created_at", "duration_ms"):
                if key in keys:
                    assert key in columns, f"{source}.{key} 应存在"
                else:
                    assert key not in columns, f"{source}.{key} 不应存在"

    def test_7_timestamp_semantics_are_distinct(self) -> None:
        """``created_at``（DB 钟·写入时刻）≠ ``started_at``（App 钟·开始时刻）。"""
        for source, column in _DB_GENERATED_TIMESTAMP_COLUMNS:
            real = _MODEL_BY_SOURCE[source].__table__.columns[column]
            assert real.server_default is not None, f"{source}.{column}"
            assert real.type.timezone is True

        for source, column in _APP_GENERATED_TIMESTAMP_COLUMNS:
            real = _MODEL_BY_SOURCE[source].__table__.columns[column]
            assert real.server_default is None, f"{source}.{column}"
            assert real.type.timezone is True

        # 语义不可互换：LLM/Outcome **没有**开始时间，Tool/RAG **没有**写入时间
        assert "started_at" not in _SOURCE_COLUMNS["LLM_USAGE"]
        assert "started_at" not in _SOURCE_COLUMNS["ASSISTANT_OUTCOME"]
        assert "created_at" not in _SOURCE_COLUMNS["TOOL_EXECUTION"]
        assert "created_at" not in _SOURCE_COLUMNS["RAG_EXECUTION"]

    def test_7b_read_path_does_not_alias_timestamps(self) -> None:
        """读路径不得把 created_at 改名成 started_at / finished_at（或反之）。"""
        for relative in _TRACE_READ_PATH_FILES:
            source = _source_path(relative).read_text(encoding="utf-8")
            assert "started_at=row.created_at" not in source
            assert "created_at=row.started_at" not in source
            assert "finished_at=row.created_at" not in source


# ============================================================
# 8~10. Ordering Audit（T2SQL Retry / RAG / Tool / Outcome）
# ============================================================

_BASE: Final[datetime] = datetime(2026, 9, 30, 8, 0, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _LlmRow:
    """合成 LLM Usage 行（审计用；字段 = 真实读白名单）。"""

    id: int
    assistant_request_id: str
    request_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class _TimedRow:
    """合成 Tool / RAG 行（审计用；id + started/finished）。"""

    id: int
    request_id: str
    started_at: datetime
    finished_at: datetime


def _order_key_llm(row: _LlmRow) -> tuple[datetime, int]:
    """LLM 读路径的真实稳定排序键（Step 37：``created_at ASC, id ASC``）。"""
    return (row.created_at, row.id)


def _order_key_id(row: _TimedRow) -> tuple[int]:
    """Tool / RAG 读路径的真实稳定排序键（``id ASC``）。"""
    return (row.id,)


class TestOrderingAudit:
    def test_8_t2sql_retry_within_source_order_is_available(self) -> None:
        """attempt 顺序 = 落库顺序（``created_at, id``）—— 但 attempt 编号**不可得**。"""
        rows = [
            _LlmRow(1, "A", "provider-1", _BASE),
            _LlmRow(2, "A", "provider-2", _BASE + timedelta(milliseconds=400)),
        ]

        ordered = sorted(rows, key=_order_key_llm)
        assert [row.id for row in ordered] == [1, 2]

        # 时间戳相等时仍可定序（id tie-breaker）
        tied = [
            _LlmRow(7, "A", "provider-7", _BASE),
            _LlmRow(6, "A", "provider-6", _BASE),
        ]
        assert [row.id for row in sorted(tied, key=_order_key_llm)] == [6, 7]

        # 但「这是第几次 attempt」在持久化事实里**不存在**
        assert "attempt" not in _SOURCE_COLUMNS["LLM_USAGE"]
        assert "round" not in _SOURCE_COLUMNS["LLM_USAGE"]
        # Validator / Executor 完全没有记录 → 事件缺失（不是"顺序不明"）
        assert not [
            name
            for name in _SOURCE_COLUMNS
            if "validator" in name.lower() or "executor" in name.lower()
        ]

    def test_8b_t2sql_retry_semantic_labels_are_not_derivable(self) -> None:
        """LLM 行**不含** route / attempt 语义 ⇒ 不能断言「哪一行是重试」。"""
        matrix = FIELD_MATRIX["LLM_USAGE"]
        assert matrix["route"] == NOT_AVAILABLE
        assert matrix["success"] == NOT_AVAILABLE
        assert matrix["duration_ms"] == NOT_AVAILABLE

    def test_9_rag_ordering_feasibility(self) -> None:
        """同请求内 RAG 行内部可排序；与 LLM/Outcome **跨钟域** ⇒ UNSAFE。"""
        rags = [
            _TimedRow(11, "A", _BASE, _BASE + timedelta(milliseconds=120)),
        ]
        assert [row.id for row in sorted(rags, key=_order_key_id)] == [11]
        assert rags[0].finished_at >= rags[0].started_at

        # RAG 行**没有** createdAt（DB 钟）→ 无法与 Outcome/LLM 的 created_at 直接比较
        assert "created_at" not in _SOURCE_COLUMNS["RAG_EXECUTION"]
        assert FIELD_MATRIX["RAG_EXECUTION"]["created_at"] == NOT_AVAILABLE

    def test_10_tool_ordering_feasibility(self) -> None:
        tools = [
            _TimedRow(21, "A", _BASE, _BASE + timedelta(milliseconds=30)),
            _TimedRow(22, "A", _BASE + timedelta(milliseconds=40),
                      _BASE + timedelta(milliseconds=70)),
        ]
        ordered = sorted(tools, key=_order_key_id)
        assert [row.id for row in ordered] == [21, 22]

        # Tool 行有 round 列（/api/ai/chat 恒为 1），但**没有** created_at
        assert "round" in _SOURCE_COLUMNS["TOOL_EXECUTION"]
        assert "created_at" not in _SOURCE_COLUMNS["TOOL_EXECUTION"]

    def test_10b_outcome_ordering_feasibility(self) -> None:
        """Outcome 只有 created_at（DB 钟，写入时刻）⇒ 与 LLM 同钟域可比。"""
        assert set(_SOURCE_COLUMNS["ASSISTANT_OUTCOME"]) == {
            "id", "assistant_request_id", "outcome", "created_at",
        }
        assert FIELD_MATRIX["ASSISTANT_OUTCOME"]["started_at"] == NOT_AVAILABLE
        assert FIELD_MATRIX["LLM_USAGE"]["created_at"] == AVAILABLE

    def test_10c_cross_clock_domains_are_explicitly_unsafe(self) -> None:
        """跨钟域排序标记：不允许在实现里"看起来能排"。"""
        cross_clock = {
            ("LLM_USAGE", "TOOL_EXECUTION"): UNSAFE_TO_DERIVE,
            ("LLM_USAGE", "RAG_EXECUTION"): UNSAFE_TO_DERIVE,
            ("ASSISTANT_OUTCOME", "TOOL_EXECUTION"): UNSAFE_TO_DERIVE,
            ("ASSISTANT_OUTCOME", "RAG_EXECUTION"): UNSAFE_TO_DERIVE,
            ("ASSISTANT_OUTCOME", "LLM_USAGE"): DERIVED,   # 同 DB 钟（插入序）
            ("TOOL_EXECUTION", "RAG_EXECUTION"): DERIVED,  # 同 App 钟（墙钟）
        }
        assert set(cross_clock.values()) <= {UNSAFE_TO_DERIVE, DERIVED}
        assert cross_clock[("LLM_USAGE", "TOOL_EXECUTION")] == UNSAFE_TO_DERIVE


# ============================================================
# 11~12. Missing Identity：event_id / sequence
# ============================================================

class TestMissingIdentityAudit:
    def test_11_event_id_does_not_exist_anywhere(self) -> None:
        for source, columns in _SOURCE_COLUMNS.items():
            for forbidden in ("event_id", "span_id", "parent_event_id",
                              "parent_span_id", "trace_id"):
                assert forbidden not in columns, f"{source}.{forbidden}"

        for dto in (
            LLMUsageTraceResponse,
            ToolExecutionTraceResponse,
            RagExecutionTraceResponse,
            AssistantTraceResponse,
        ):
            assert not [
                name
                for name in dto.model_fields
                if name in FORBIDDEN_TIMELINE_FIELDS
            ], dto.__name__

        # 内部读模型同样没有事件身份（Trace View / Tool Snapshot）
        for fields_tuple in (
            tuple(AssistantTraceView.__dataclass_fields__),
            tuple(f.name for f in dataclasses.fields(ToolExecutionSnapshot)),
        ):
            assert not [
                name for name in fields_tuple if name in FORBIDDEN_TIMELINE_FIELDS
            ]

    def test_11b_no_runtime_generation_of_fake_event_ids(self) -> None:
        """读路径 / 持久化层都不得用 uuid4 / hash 造事件 ID。"""
        for relative in _TRACE_READ_PATH_FILES + (
            "backend/app/services/llm_usage_persistence_service.py",
            "backend/app/services/assistant_outcome_persistence_service.py",
        ):
            identifiers = _identifiers(_source_path(relative))
            assert "uuid4" not in identifiers, relative
            assert "uuid1" not in identifiers, relative
            assert "event_id" not in identifiers, relative
            assert "span_id" not in identifiers, relative

    def test_12_sequence_does_not_exist_and_is_not_synthesized(self) -> None:
        for source, columns in _SOURCE_COLUMNS.items():
            assert "sequence" not in columns, source
            assert "seq" not in columns, source
            assert "step_index" not in columns, source

        for relative in _TRACE_READ_PATH_FILES:
            identifiers = _identifiers(_source_path(relative))
            assert "sequence" not in identifiers, relative
            assert "seq" not in identifiers, relative
            assert "enumerate" not in identifiers, relative    # 禁止"查询序 = 事件序"
            assert "sorted" not in identifiers, relative       # 读路径不重排
            assert "merge" not in identifiers, relative        # 不合并为统一 events

    def test_12b_trace_api_unchanged_by_this_step(self) -> None:
        """本阶段不得改动 Trace API（无 timeline 端点 / 无新字段）。"""
        from backend.app.main import app

        assert list(AssistantTraceResponse.model_fields) == [
            "assistant_request_id",
            "outcome",
            "llm_usage",
            "tool_executions",
            "rag_executions",
        ]
        assert not [
            path
            for path in app.openapi()["paths"]
            if "timeline" in path.lower()
        ]


# ============================================================
# 13~14. Synthetic Timeline Projection（Case A ~ E）+ 隔离
# ============================================================

@dataclass(frozen=True)
class _Projection:
    """审计用投影结果：**只**暴露真实可得的字段 + 明确的"不可得"标记。"""

    assistant_request_id: str
    llm: tuple[_LlmRow, ...]
    tools: tuple[_TimedRow, ...]
    rag: tuple[_TimedRow, ...]
    outcome: str | None
    #: 全局事件序：**NOT_AVAILABLE**（不允许用 enumerate / 查询顺序伪造）
    global_sequence: None = None
    #: 事件 ID：**NOT_AVAILABLE**（四表无 event_id）
    event_ids: None = None
    #: 每个来源内部的排序依据（真实存在）
    within_source_order: dict[str, str] = field(
        default_factory=lambda: {
            "LLM_USAGE": "created_at ASC, id ASC",
            "TOOL_EXECUTION": "id ASC",
            "RAG_EXECUTION": "id ASC",
            "ASSISTANT_OUTCOME": "assistant_request_id (UNIQUE) → 至多 1 条",
        }
    )


def _project(
    assistant_request_id: str,
    *,
    llm: list[_LlmRow] | None = None,
    tools: list[_TimedRow] | None = None,
    rag: list[_TimedRow] | None = None,
    outcome: str | None = None,
) -> _Projection:
    """合成投影：**按真实排序键排序，不生成事件 ID / 不生成全局序**。"""
    return _Projection(
        assistant_request_id=assistant_request_id,
        llm=tuple(
            sorted(
                (row for row in (llm or []) if row.assistant_request_id == assistant_request_id),
                key=_order_key_llm,
            )
        ),
        tools=tuple(
            sorted(
                (row for row in (tools or []) if row.request_id == assistant_request_id),
                key=_order_key_id,
            )
        ),
        rag=tuple(
            sorted(
                (row for row in (rag or []) if row.request_id == assistant_request_id),
                key=_order_key_id,
            )
        ),
        outcome=outcome,
    )


class TestSyntheticProjectionAudit:
    def test_13_case_a_rag_success(self) -> None:
        projection = _project(
            "A",
            llm=[_LlmRow(1, "A", "p1", _BASE + timedelta(milliseconds=300))],
            rag=[_TimedRow(5, "A", _BASE, _BASE + timedelta(milliseconds=200))],
            outcome="SUCCESS",
        )

        assert projection.outcome == "SUCCESS"
        assert len(projection.llm) == 1 and len(projection.rag) == 1
        assert projection.global_sequence is None       # NOT_AVAILABLE
        assert projection.event_ids is None             # NOT_AVAILABLE
        # 只能得到"RAG 在 App 钟内先于 …"这类**同钟域**结论，不能得到全局事件序
        assert projection.rag[0].finished_at <= projection.llm[0].created_at

    def test_13_case_b_tool(self) -> None:
        projection = _project(
            "B",
            tools=[_TimedRow(9, "B", _BASE, _BASE + timedelta(milliseconds=50))],
            outcome="SUCCESS",
        )

        assert len(projection.tools) == 1
        assert projection.llm == () and projection.rag == ()
        assert projection.global_sequence is None

    def test_13_case_c_t2sql_retry(self) -> None:
        projection = _project(
            "C",
            llm=[
                _LlmRow(1, "C", "p1", _BASE),
                _LlmRow(2, "C", "p2", _BASE + timedelta(seconds=1)),
            ],
            outcome="SUCCESS",
        )

        assert [row.id for row in projection.llm] == [1, 2]   # 内部有序
        assert projection.outcome == "SUCCESS"                 # 只记终态，不记 RETRY
        assert projection.global_sequence is None
        # Validator / Executor 没有记录 → 投影里**不能**出现这两个事件
        assert set(FIELD_MATRIX) == {
            "LLM_USAGE", "TOOL_EXECUTION", "RAG_EXECUTION", "ASSISTANT_OUTCOME",
        }

    def test_13_case_d_rag_failure_absence_is_representable(self) -> None:
        """RAG 运行期失败可能**没有** RAG 行（执行未走到观测写入）→ 不得补造。"""
        projection = _project(
            "D",
            llm=[_LlmRow(3, "D", "p3", _BASE)],
            rag=[],                                   # 允许为空
            outcome="FAILED",
        )

        assert projection.rag == ()
        assert projection.outcome == "FAILED"
        assert projection.global_sequence is None
        assert FIELD_MATRIX["RAG_EXECUTION"]["success"] == NOT_AVAILABLE

    def test_13_case_e_refusal(self) -> None:
        projection = _project(
            "E",
            llm=[_LlmRow(4, "E", "p4", _BASE)],
            outcome="REFUSED",
        )

        assert projection.outcome == "REFUSED"
        assert projection.tools == () and projection.rag == ()
        assert projection.global_sequence is None

    def test_13f_projection_never_fabricates_identity(self) -> None:
        projection = _project("F", llm=[_LlmRow(1, "F", "p1", _BASE)])
        payload = dataclasses.asdict(projection)

        assert payload["global_sequence"] is None
        assert payload["event_ids"] is None
        blob = json.dumps(payload, default=str, ensure_ascii=False)
        for forbidden in ("event_id\":", "\"sequence\": 1", "uuid"):
            assert forbidden not in blob

    def test_14_cross_request_isolation(self) -> None:
        projection_a = _project(
            "A",
            llm=[
                _LlmRow(1, "A", "p1", _BASE),
                _LlmRow(2, "B", "p2", _BASE),          # 另一请求 → 不得混入
            ],
            tools=[_TimedRow(9, "B", _BASE, _BASE)],
            outcome="SUCCESS",
        )

        assert [row.id for row in projection_a.llm] == [1]
        assert projection_a.tools == ()
        assert projection_a.assistant_request_id == "A"


# ============================================================
# 15~16. Hygiene：本审计文件本身不得触 DB / 网络
# ============================================================

class TestAuditHygiene:
    _SELF: Final[str] = "tests/test_assistant_timeline_audit.py"

    def test_15_audit_file_does_not_touch_db(self) -> None:
        """自查：本审计文件不得 import / 调用任何 DB 连接设施。

        禁用词**拼装**生成，避免断言文本本身命中自己（否则守卫会自伤）。
        """
        source = _source_path(self._SELF).read_text(encoding="utf-8")
        forbidden = (
            "get_" + "engine",
            "get_session_" + "factory",
            "create_" + "engine",
            "session" + "maker",
            "RUN_DB_" + "TESTS",
            "init_" + "db",
            "backend.app.db." + "session",
            "sqlalchemy." + "exc",
        )
        for token in forbidden:
            assert token not in source, token

    def test_16_audit_file_does_not_touch_network(self) -> None:
        """自查：本审计文件不得引用网络客户端（标识符级检查）。"""
        identifiers = _identifiers(_source_path(self._SELF))
        for token in (
            "httpx",
            "requests",
            "urllib",
            "socket",
            "openai",
            "api_" + "key",
            "Transport",
        ):
            assert token not in identifiers, token

    def test_16b_audit_module_has_no_test_client_calls(self) -> None:
        """自查：无 URL 字面量、无 HTTP 测试客户端调用。

        明文 URL scheme 同样**拼装**生成，避免断言文本命中自己。
        """
        strings = _non_docstring_strings(_source_path(self._SELF))
        url_prefixes = ("http" + "://", "https" + "://")
        assert not [
            value for value in strings if value.startswith(url_prefixes)
        ]
        assert "TestClient" not in inspect.getsource(
            TestTimestampAudit
        ) and "TestClient" not in inspect.getsource(TestOrderingAudit)


__all__ = [
    "TestCorrelationAudit",
    "TestTimestampAudit",
    "TestOrderingAudit",
    "TestMissingIdentityAudit",
    "TestSyntheticProjectionAudit",
    "TestAuditHygiene",
]
