"""Tool Observability Serialization Boundary 测试（Phase 3.11 Step 24）。

覆盖（§十四）：

    Snapshot     normal / None 字段 / datetime→ISO / tz 保留 / 11 字段齐全 /
                 无多余字段 / json.dumps / deterministic / 源对象不变
    Metrics      normal / empty / None 语义保持 / 8 字段齐全 / 无多余字段 /
                 json.dumps / deterministic / 源对象不变
    Security     禁用键缺失 / 无嵌套对象引用 / 无 SQL·凭据
    Integration  QueryService snapshot → serialize /
                 QueryService metrics → serialize /
                 retention 后只含保留记录 / 被淘汰记录不会因序列化复现
    Contract     显式映射（无 vars/asdict/__dict__）/ 非法输入 TypeError /
                 无 DB · LLM · Tool 执行 · HTTP

0 DB / 0 Network / 0 Real LLM。
"""
from __future__ import annotations

import ast
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.app.services.in_memory_tool_execution_collector import (
    InMemoryToolExecutionCollector,
)
from backend.app.services.tool_execution_metrics_service import (
    ToolExecutionMetricsService,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.services.tool_observability_query_service import (
    ToolObservabilityQueryService,
)
from backend.app.services.tool_observability_serialization import (
    SERIALIZED_METRICS_FIELDS,
    SERIALIZED_SNAPSHOT_FIELDS,
    metrics_to_dict,
    snapshot_to_dict,
)
from backend.app.services.tool_observability_snapshot import (
    ToolExecutionSnapshot,
)

_MODULE = "backend/app/services/tool_observability_serialization.py"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STARTED = datetime(2026, 9, 26, 10, 20, 30, 123456, tzinfo=timezone.utc)

#: 禁止出现在序列化结果中的键 / 值（§九）
_FORBIDDEN_KEYS = frozenset({
    "arguments", "sql", "result", "data", "traceback", "exception",
    "prompt", "response", "messages", "password", "api_key", "apikey",
    "authorization", "secret", "token", "dsn", "database_url",
    "connection", "session", "engine", "registry", "handler",
    "record", "records", "collector", "metrics_service", "llm",
})
_FORBIDDEN_VALUES = (
    "SELECT", "postgresql://", "Bearer ", "sk-", "Traceback",
)


def _path(relative: str) -> str:
    return os.path.join(_REPO_ROOT, *relative.split("/"))


def _tree() -> ast.Module:
    with open(_path(_MODULE), encoding="utf-8") as handle:
        return ast.parse(handle.read())


def _identifiers() -> set[str]:
    tree = _tree()
    return {
        node.id.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    } | {
        node.attr.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }


def _record(
    *,
    request_id: str = "req-1",
    round: int = 1,
    tool_name: str = "get_inventory",
    project_id: str | None = "project-a",
    tool_call_id: str | None = "call_1",
    success: bool = True,
    duration_ms: float = 12.5,
) -> ToolExecutionRecord:
    return ToolExecutionRecord(
        request_id=request_id,
        round=round,
        tool_name=tool_name,
        started_at=_STARTED,
        finished_at=_STARTED + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        success=success,
        project_id=project_id,
        tool_call_id=tool_call_id,
        error_type=None if success else "ToolValidationError",
    )


def _snapshot(**kwargs: Any) -> ToolExecutionSnapshot:
    return ToolExecutionSnapshot.from_record(_record(**kwargs))


def _metrics_of(*records: ToolExecutionRecord):
    return ToolExecutionMetricsService.snapshot(records)


# ============================================================
# Snapshot Serialization
# ============================================================

class TestSnapshotSerialization:
    def test_normal_snapshot_serializes_to_primitives(self) -> None:
        result = snapshot_to_dict(_snapshot())

        assert result["request_id"] == "req-1"
        assert result["round"] == 1
        assert result["tool_name"] == "get_inventory"
        assert result["duration_ms"] == 12.5
        assert result["success"] is True
        assert result["project_id"] == "project-a"
        assert result["tool_call_id"] == "call_1"

    def test_optional_fields_keep_none(self) -> None:
        result = snapshot_to_dict(
            _snapshot(project_id=None, tool_call_id=None, success=False)
        )

        assert result["project_id"] is None
        assert result["tool_call_id"] is None
        assert result["error_code"] is None
        assert result["error_type"] == "ToolValidationError"

    def test_datetime_becomes_iso8601(self) -> None:
        result = snapshot_to_dict(_snapshot())

        assert isinstance(result["started_at"], str)
        assert result["started_at"] == _STARTED.isoformat()
        assert result["finished_at"] == (
            _STARTED + timedelta(milliseconds=12.5)
        ).isoformat()

    def test_timezone_is_preserved(self) -> None:
        result = snapshot_to_dict(_snapshot())

        assert result["started_at"].endswith("+00:00")
        assert result["started_at"] == "2026-09-26T10:20:30.123456+00:00"
        # 非本地时间 / 非 epoch number
        assert "T" in result["started_at"]
        assert not result["started_at"].isdigit()

    def test_non_utc_offset_is_preserved(self) -> None:
        offset = timezone(timedelta(hours=8))
        record = ToolExecutionRecord(
            request_id="req-tz",
            round=1,
            tool_name="get_inventory",
            started_at=datetime(2026, 9, 26, 18, 0, 0, tzinfo=offset),
            finished_at=datetime(2026, 9, 26, 18, 0, 1, tzinfo=offset),
            duration_ms=1000.0,
            success=True,
        )

        result = snapshot_to_dict(ToolExecutionSnapshot.from_record(record))

        assert result["started_at"].endswith("+08:00")
        assert result["started_at"] == "2026-09-26T18:00:00+08:00"

    def test_all_eleven_fields_present_and_no_extra(self) -> None:
        result = snapshot_to_dict(_snapshot())

        assert tuple(result) == SERIALIZED_SNAPSHOT_FIELDS
        assert set(result) == set(SERIALIZED_SNAPSHOT_FIELDS)
        assert len(result) == 11

    def test_json_dumps_succeeds(self) -> None:
        serialized = snapshot_to_dict(_snapshot())

        text = json.dumps(serialized)

        assert json.loads(text) == serialized

    def test_deterministic(self) -> None:
        snapshot = _snapshot()

        assert snapshot_to_dict(snapshot) == snapshot_to_dict(snapshot)
        assert json.dumps(snapshot_to_dict(snapshot)) == json.dumps(
            snapshot_to_dict(snapshot)
        )

    def test_source_snapshot_not_mutated(self) -> None:
        snapshot = _snapshot()
        before = repr(snapshot)

        snapshot_to_dict(snapshot)

        assert repr(snapshot) == before

    def test_invalid_input_raises_type_error(self) -> None:
        for bad in (None, "snapshot", {}, _record()):
            with pytest.raises(TypeError):
                snapshot_to_dict(bad)  # type: ignore[arg-type]

    def test_naive_datetime_is_rejected(self) -> None:
        naive = datetime(2026, 9, 26, 10, 20, 30)  # noqa: DTZ001
        snapshot = ToolExecutionSnapshot(
            request_id="req-naive",
            round=1,
            tool_name="get_inventory",
            started_at=naive,
            finished_at=naive,
            duration_ms=0.0,
            success=True,
        )

        with pytest.raises(ValueError, match="timezone-aware"):
            snapshot_to_dict(snapshot)


# ============================================================
# Metrics Serialization
# ============================================================

class TestMetricsSerialization:
    def test_normal_metrics_serialize(self) -> None:
        result = metrics_to_dict(
            _metrics_of(
                _record(duration_ms=10.0),
                _record(duration_ms=20.0, success=False),
            )
        )

        assert result["total_count"] == 2
        assert result["success_count"] == 1
        assert result["failure_count"] == 1
        assert result["success_rate"] == 0.5
        assert result["failure_rate"] == 0.5
        assert result["total_duration_ms"] == 30.0
        assert result["average_duration_ms"] == 15.0
        assert result["max_duration_ms"] == 20.0

    def test_empty_metrics_keep_none_semantics(self) -> None:
        result = metrics_to_dict(_metrics_of())

        assert result["total_count"] == 0
        assert result["success_rate"] is None      # None ≠ 0（§七）
        assert result["failure_rate"] is None
        assert result["average_duration_ms"] is None
        assert result["max_duration_ms"] is None
        assert result["total_duration_ms"] == 0.0

    def test_none_is_not_converted_to_zero(self) -> None:
        result = metrics_to_dict(_metrics_of())

        for key in (
            "success_rate", "failure_rate", "average_duration_ms",
            "max_duration_ms",
        ):
            assert result[key] is None, key
            assert result[key] != 0, key

    def test_all_eight_fields_present_and_no_extra(self) -> None:
        result = metrics_to_dict(_metrics_of(_record()))

        assert tuple(result) == SERIALIZED_METRICS_FIELDS
        assert set(result) == set(SERIALIZED_METRICS_FIELDS)
        assert len(result) == 8

    def test_json_dumps_succeeds_including_none(self) -> None:
        for serialized in (
            metrics_to_dict(_metrics_of()),
            metrics_to_dict(_metrics_of(_record())),
        ):
            text = json.dumps(serialized)
            assert json.loads(text) == serialized
            assert "null" in text or serialized["total_count"] > 0

    def test_deterministic(self) -> None:
        metrics = _metrics_of(_record(), _record(success=False))

        assert metrics_to_dict(metrics) == metrics_to_dict(metrics)

    def test_source_metrics_not_mutated(self) -> None:
        metrics = _metrics_of(_record())
        before = repr(metrics)

        metrics_to_dict(metrics)

        assert repr(metrics) == before

    def test_invalid_input_raises_type_error(self) -> None:
        for bad in (None, {}, _snapshot()):
            with pytest.raises(TypeError):
                metrics_to_dict(bad)  # type: ignore[arg-type]


# ============================================================
# Security Boundary
# ============================================================

class TestSerializationSecurity:
    def test_forbidden_keys_absent(self) -> None:
        for serialized in (
            snapshot_to_dict(_snapshot()),
            metrics_to_dict(_metrics_of(_record())),
        ):
            assert not (set(serialized) & _FORBIDDEN_KEYS), set(serialized)

    def test_no_nested_object_references(self) -> None:
        serialized = snapshot_to_dict(_snapshot())

        for value in serialized.values():
            assert isinstance(value, (str, int, float, bool, type(None)))
            assert value is None or not hasattr(value, "__dict__") or isinstance(
                value, (str, int, float)
            )

    def test_no_sql_or_credentials_in_serialized_text(self) -> None:
        serialized = snapshot_to_dict(
            _snapshot(project_id="project-a", tool_call_id="call_1")
        )
        text = json.dumps(serialized, ensure_ascii=False).upper()

        for forbidden in _FORBIDDEN_VALUES:
            assert forbidden.upper() not in text, forbidden

    def test_metrics_do_not_carry_identifiers(self) -> None:
        serialized = metrics_to_dict(
            _metrics_of(_record(request_id="req-secret", project_id="project-a"))
        )

        assert "req-secret" not in json.dumps(serialized)
        assert "project-a" not in json.dumps(serialized)
        assert "get_inventory" not in json.dumps(serialized)


# ============================================================
# Integration：Collector → Query Service → Serialization
# ============================================================

class TestSerializationIntegration:
    def test_query_service_snapshots_serialize(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record(request_id="req-A"))
        collector.on_execution(_record(request_id="req-B", success=False))
        query = ToolObservabilityQueryService(collector)

        serialized = [snapshot_to_dict(s) for s in query.snapshots()]

        assert len(serialized) == 2
        assert [s["request_id"] for s in serialized] == ["req-A", "req-B"]
        assert serialized[0]["success"] is True
        assert serialized[1]["success"] is False
        json.dumps(serialized)

    def test_query_service_metrics_serialize(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record(duration_ms=10.0))
        collector.on_execution(_record(duration_ms=30.0, success=False))

        serialized = metrics_to_dict(
            ToolObservabilityQueryService(collector).metrics()
        )

        assert serialized["total_count"] == 2
        assert serialized["average_duration_ms"] == 20.0
        assert serialized["max_duration_ms"] == 30.0

    def test_retention_window_limits_serialized_output(self) -> None:
        collector = InMemoryToolExecutionCollector(max_records=2)
        for request_id in ("req-1", "req-2", "req-3"):
            collector.on_execution(_record(request_id=request_id))

        serialized = [
            snapshot_to_dict(s)
            for s in ToolObservabilityQueryService(collector).snapshots()
        ]

        assert [s["request_id"] for s in serialized] == ["req-2", "req-3"]
        assert metrics_to_dict(
            ToolObservabilityQueryService(collector).metrics()
        )["total_count"] == 2

    def test_evicted_record_cannot_reappear(self) -> None:
        collector = InMemoryToolExecutionCollector(max_records=1)
        collector.on_execution(_record(request_id="req-old"))
        collector.on_execution(_record(request_id="req-new"))

        query = ToolObservabilityQueryService(collector)
        serialized = [snapshot_to_dict(s) for s in query.snapshots()]
        metrics = metrics_to_dict(query.metrics())

        assert [s["request_id"] for s in serialized] == ["req-new"]
        assert "req-old" not in json.dumps(serialized)
        assert metrics["total_count"] == 1

    def test_serialization_does_not_mutate_collector_or_records(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record())
        before = collector.records()

        for s in ToolObservabilityQueryService(collector).snapshots():
            snapshot_to_dict(s)
        metrics_to_dict(ToolObservabilityQueryService(collector).metrics())

        assert collector.records() == before


# ============================================================
# Contract：显式映射 / 无自动序列化 / 无 IO
# ============================================================

class TestSerializationContract:
    def test_no_automatic_serialization_helpers(self) -> None:
        tree = _tree()
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not (calls & {"vars", "asdict", "eval", "exec"})
        assert "__dict__" not in _identifiers()

    def test_explicit_mapping_uses_attribute_access(self) -> None:
        with open(_path(_MODULE), encoding="utf-8") as handle:
            source = handle.read()
        for field in SERIALIZED_SNAPSHOT_FIELDS:
            assert f"snapshot.{field}" in source, field
        for field in SERIALIZED_METRICS_FIELDS:
            assert f"metrics.{field}" in source, field

    def test_module_has_no_io_or_execution_capability(self) -> None:
        tree = _tree()
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        for forbidden in (
            "sqlalchemy", "psycopg", "redis", "kafka", "celery", "httpx",
            "requests", "json", "pathlib", "os", "subprocess",
            "backend.app.api", "backend.app.db", "backend.app.llm",
            "backend.app.tools",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imported
            ), forbidden
        identifiers = _identifiers()
        for forbidden in (
            "open", "dump", "load", "now", "perf_counter", "random", "uuid",
            "execute", "session", "engine", "commit",
        ):
            assert forbidden not in identifiers, forbidden

    def test_public_api_is_two_functions(self) -> None:
        from backend.app.services import (
            tool_observability_serialization as module,
        )

        public = {
            name for name in dir(module) if not name.startswith("_")
        }
        assert {"snapshot_to_dict", "metrics_to_dict"} <= public
        # 无反序列化（§十三）
        for forbidden in ("dict_to_snapshot", "dict_to_metrics", "deserialize"):
            assert forbidden not in public, forbidden
