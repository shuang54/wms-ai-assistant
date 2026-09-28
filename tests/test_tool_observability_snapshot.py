"""Tool Observability Snapshot Read Model（Phase 3.11 Step 22）。

锁定：``ToolExecutionSnapshot`` 是**独立对外 Read Model** —— 由内部
``ToolExecutionRecord`` 逐字段显式转换而来，不持有 Record / Collector /
Metrics Service；``ToolObservabilityQueryService`` 新增 snapshots*() 查询，
原有 records*() / metrics() 语义不变。

覆盖（§二十）：

    DTO                构造 / 字段相等 / frozen / 精确字段（11 项）
    Conversion         Record → Snapshot（显式映射 / 类型 / 拒绝非 Record / 纯函数）
    Independence       Snapshot 与 Record 解耦（不持有、不随 Record/Collector 变化）
    Query              snapshots / 三个过滤版本 / 空 / 不可变快照
    Retention          max_records=3：A B C D → B C D（不可恢复）
    Metrics isolation  metrics() 仍走 Metrics Service，且不基于 Snapshot 计算
    Security           import 白名单 / 无敏感字段 / 无自动字段泄露

0 DB / 0 Network / 0 Real LLM（全部内存 Fake）。
"""
from __future__ import annotations

import ast
import dataclasses
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.app.services.in_memory_tool_execution_collector import (
    InMemoryToolExecutionCollector,
)
from backend.app.services.tool_execution_metrics_service import (
    ToolExecutionMetricsService,
    ToolExecutionMetricsSnapshot,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.services.tool_observability_query_service import (
    ToolObservabilityQueryService,
)
from backend.app.services.tool_observability_snapshot import (
    ToolExecutionSnapshot,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SNAPSHOT_MODULE = "backend/app/services/tool_observability_snapshot.py"
_QUERY_MODULE = "backend/app/services/tool_observability_query_service.py"

_STARTED = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)

_FAKE_SNAPSHOT = ToolExecutionMetricsSnapshot(
    total_count=99,
    success_count=99,
    failure_count=0,
    success_rate=1.0,
    failure_rate=0.0,
    total_duration_ms=99.0,
    average_duration_ms=1.0,
    max_duration_ms=1.0,
)

_EXPECTED_FIELDS = {
    "request_id", "round", "tool_name", "started_at", "finished_at",
    "duration_ms", "success", "project_id", "tool_call_id",
    "error_code", "error_type",
}


# ============================================================
# 辅助
# ============================================================

def _record(
    *,
    request_id: str = "req-1",
    round: int = 1,
    tool_name: str = "get_inventory",
    project_id: str | None = None,
    tool_call_id: str | None = "call_001",
    success: bool = True,
    duration_ms: float = 5.0,
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
        error_code=None,
        error_type=None if success else "ToolValidationError",
    )


def _collector_with(
    *records: ToolExecutionRecord, max_records: int = 1000
) -> InMemoryToolExecutionCollector:
    collector = InMemoryToolExecutionCollector(max_records=max_records)
    for record in records:
        collector.on_execution(record)
    return collector


def _query(*records: ToolExecutionRecord, max_records: int = 1000,
           metrics_service: Any = None):
    collector = _collector_with(*records, max_records=max_records)
    query = (
        ToolObservabilityQueryService(collector)
        if metrics_service is None
        else ToolObservabilityQueryService(
            collector, metrics_service=metrics_service
        )
    )
    return query, collector


class _FakeMetricsService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def snapshot(self, records: Any) -> ToolExecutionMetricsSnapshot:
        self.calls.append(tuple(records))
        return _FAKE_SNAPSHOT


def _source(path: str) -> str:
    with open(
        os.path.join(REPO_ROOT, *path.split("/")), encoding="utf-8"
    ) as handle:
        return handle.read()


def _tree(path: str) -> ast.Module:
    return ast.parse(_source(path))


def _imports(path: str) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


# ============================================================
# DTO
# ============================================================

class TestSnapshotDto:
    def test_constructor_and_field_equality(self) -> None:
        record = _record(request_id="req-A", project_id="project-a", round=2)
        snapshot = ToolExecutionSnapshot.from_record(record)

        assert snapshot.request_id == "req-A"
        assert snapshot.round == 2
        assert snapshot.tool_name == "get_inventory"
        assert snapshot.project_id == "project-a"
        assert snapshot.tool_call_id == "call_001"
        assert snapshot.duration_ms == 5.0
        assert snapshot.success is True
        assert snapshot.started_at == _STARTED
        assert snapshot.finished_at == _STARTED + timedelta(milliseconds=5.0)
        assert snapshot.error_code is None
        assert snapshot.error_type is None

    def test_optional_fields_default_to_none(self) -> None:
        snapshot = ToolExecutionSnapshot(
            request_id="req-A",
            round=1,
            tool_name="get_inventory",
            started_at=_STARTED,
            finished_at=_STARTED + timedelta(milliseconds=1),
            duration_ms=1.0,
            success=True,
        )

        assert snapshot.project_id is None
        assert snapshot.tool_call_id is None
        assert snapshot.error_code is None
        assert snapshot.error_type is None

    def test_frozen_immutable(self) -> None:
        snapshot = ToolExecutionSnapshot.from_record(_record())

        with pytest.raises(dataclasses.FrozenInstanceError):
            snapshot.request_id = "changed"  # type: ignore[misc]

    def test_exact_fields(self) -> None:
        snapshot = ToolExecutionSnapshot.from_record(_record())

        assert {f.name for f in dataclasses.fields(snapshot)} == (
            _EXPECTED_FIELDS
        )
        snapshot.assert_field_whitelist()

    def test_is_not_and_does_not_wrap_record(self) -> None:
        record = _record()
        snapshot = ToolExecutionSnapshot.from_record(record)

        assert not isinstance(snapshot, ToolExecutionRecord)
        assert snapshot is not record
        # 不持有 Record：内部属性仅为 11 个字段（无 _record / _collector）
        assert set(vars(snapshot)) == _EXPECTED_FIELDS


# ============================================================
# Conversion
# ============================================================

class TestConversion:
    def test_from_record_maps_all_fields(self) -> None:
        record = _record(
            request_id="req-A",
            project_id="project-a",
            success=False,
            duration_ms=7.5,
        )

        snapshot = ToolExecutionSnapshot.from_record(record)

        assert snapshot.request_id == record.request_id
        assert snapshot.round == record.round
        assert snapshot.tool_name == record.tool_name
        assert snapshot.started_at == record.started_at
        assert snapshot.finished_at == record.finished_at
        assert snapshot.duration_ms == record.duration_ms
        assert snapshot.success == record.success
        assert snapshot.project_id == record.project_id
        assert snapshot.tool_call_id == record.tool_call_id
        assert snapshot.error_code == record.error_code
        assert snapshot.error_type == record.error_type

    def test_from_record_rejects_non_record(self) -> None:
        with pytest.raises(TypeError):
            ToolExecutionSnapshot.from_record({"request_id": "req-1"})  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            ToolExecutionSnapshot.from_record(object())  # type: ignore[arg-type]

    def test_conversion_is_deterministic(self) -> None:
        record = _record()

        assert ToolExecutionSnapshot.from_record(record) == (
            ToolExecutionSnapshot.from_record(record)
        )

    def test_conversion_does_not_mutate_record(self) -> None:
        record = _record()
        before = dataclasses.asdict(record)

        ToolExecutionSnapshot.from_record(record)

        assert dataclasses.asdict(record) == before

    def test_conversion_is_explicit_no_automatic_field_leak(self) -> None:
        """禁止 vars / asdict / __dict__ 自动带字段（AST 层锁定；
        docstring 中的"禁止 …"说明不算实现）。"""
        tree = _tree(_SNAPSHOT_MODULE)

        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        assert not (calls & {"vars", "asdict"}), calls
        attributes = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        assert "__dict__" not in attributes, attributes

        # 逐字段显式映射：from_record 内每个字段都是 ``<field>=record.<field>``
        from_record = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "from_record"
        )
        mapping = ast.unparse(from_record)
        for field_name in _EXPECTED_FIELDS:
            assert f"{field_name}=record.{field_name}" in mapping, field_name


# ============================================================
# Snapshot 与 Record / Collector 解耦（§十五）
# ============================================================

class TestSnapshotIndependence:
    def test_snapshot_has_no_external_references(self) -> None:
        snapshot = ToolExecutionSnapshot.from_record(_record())

        for forbidden in (
            "collector", "metrics_service", "record", "engine", "session",
            "handler", "registry", "llm", "client",
        ):
            assert not hasattr(snapshot, forbidden), forbidden
        assert set(vars(snapshot)) == _EXPECTED_FIELDS

    def test_record_cannot_change_snapshot(self) -> None:
        """Record 已 frozen → 无法被修改；Snapshot 复制值而非引用。"""
        record = _record(duration_ms=3.0)
        snapshot = ToolExecutionSnapshot.from_record(record)

        with pytest.raises(dataclasses.FrozenInstanceError):
            record.duration_ms = 999.0  # type: ignore[misc]

        assert snapshot.duration_ms == 3.0

    def test_old_snapshot_stable_after_new_appends(self) -> None:
        query, collector = _query(_record(request_id="req-A"))

        snapshots = query.snapshots()
        collector.on_execution(_record(request_id="req-B"))
        collector.on_execution(_record(request_id="req-C"))

        assert [s.request_id for s in snapshots] == ["req-A"]
        assert len(query.snapshots()) == 3

    def test_old_snapshot_stable_after_eviction(self) -> None:
        collector = _collector_with(
            _record(request_id="req-A"),
            _record(request_id="req-B"),
            max_records=2,
        )
        query = ToolObservabilityQueryService(collector)

        snapshots = query.snapshots()
        collector.on_execution(_record(request_id="req-C"))   # 淘汰 A

        assert [s.request_id for s in snapshots] == ["req-A", "req-B"]
        assert [s.request_id for s in query.snapshots()] == [
            "req-B", "req-C"
        ]


# ============================================================
# Query Snapshot API
# ============================================================

class TestQuerySnapshotApi:
    def test_snapshots_empty(self) -> None:
        query, _collector = _query()

        assert query.snapshots() == ()

    def test_snapshots_match_records(self) -> None:
        records = [
            _record(request_id="req-A"),
            _record(request_id="req-B", success=False),
        ]
        query, _collector = _query(*records)

        snapshots = query.snapshots()

        assert tuple(s.tool_name for s in snapshots) == tuple(
            r.tool_name for r in query.records()
        )
        assert len(snapshots) == 2
        assert all(
            isinstance(s, ToolExecutionSnapshot) for s in snapshots
        )

    def test_snapshots_by_request_id(self) -> None:
        query, _collector = _query(
            _record(request_id="req-A"),
            _record(request_id="req-B"),
            _record(request_id="req-A", round=2),
        )

        snapshots = query.snapshots_by_request_id("req-A")

        assert [(s.request_id, s.round) for s in snapshots] == [
            ("req-A", 1), ("req-A", 2),
        ]
        assert query.snapshots_by_request_id("req-UNKNOWN") == ()

    def test_snapshots_by_project_id(self) -> None:
        query, _collector = _query(
            _record(request_id="req-A", project_id="project-a"),
            _record(request_id="req-B", project_id=None),
        )

        assert [s.request_id for s in query.snapshots_by_project_id(
            "project-a"
        )] == ["req-A"]
        # None **只**匹配 project_id is None
        assert [s.request_id for s in query.snapshots_by_project_id(
            None
        )] == ["req-B"]

    def test_snapshots_by_tool_name(self) -> None:
        query, _collector = _query(
            _record(request_id="req-A", tool_name="get_inventory"),
            _record(request_id="req-B", tool_name="get_work_order"),
        )

        assert [s.request_id for s in query.snapshots_by_tool_name(
            "get_inventory"
        )] == ["req-A"]
        assert query.snapshots_by_tool_name("unknown_tool") == ()

    def test_snapshots_are_tuples_of_immutable_dtos(self) -> None:
        query, _collector = _query(_record())

        snapshots = query.snapshots()

        assert isinstance(snapshots, tuple)
        with pytest.raises(dataclasses.FrozenInstanceError):
            snapshots[0].success = False  # type: ignore[misc]

    def test_snapshots_do_not_mutate_collector(self) -> None:
        query, collector = _query(
            _record(request_id="req-A"),
            _record(request_id="req-B"),
        )
        before = collector.records()

        query.snapshots()
        query.snapshots_by_request_id("req-A")
        query.snapshots_by_project_id(None)
        query.snapshots_by_tool_name("get_inventory")

        assert collector.records() == before

    def test_original_records_api_unchanged(self) -> None:
        """Step 21 的 records*() / metrics() 语义与返回值不变。"""
        query, collector = _query(
            _record(request_id="req-A"),
            _record(request_id="req-B", success=False),
        )

        assert query.records() == collector.records()
        assert all(
            isinstance(r, ToolExecutionRecord) for r in query.records()
        )
        assert query.metrics().total_count == 2
        assert query.metrics().failure_count == 1


# ============================================================
# Retention（§十三）
# ============================================================

class TestRetention:
    def _abcd(self) -> list[ToolExecutionRecord]:
        return [
            _record(request_id="req-A", success=True),
            _record(request_id="req-B", success=True),
            _record(request_id="req-C", success=False),
            _record(request_id="req-D", success=True),
        ]

    def test_snapshots_respect_retention_window(self) -> None:
        records = self._abcd()
        query, collector = _query(*records, max_records=3)

        assert [s.request_id for s in query.snapshots()] == [
            "req-B", "req-C", "req-D"
        ]
        assert [r.request_id for r in collector.records()] == [
            "req-B", "req-C", "req-D"
        ]

    def test_evicted_record_not_recoverable_via_snapshots(self) -> None:
        records = self._abcd()
        query, _collector = _query(*records, max_records=3)

        assert query.snapshots_by_request_id("req-A") == ()

    def test_snapshots_and_records_are_consistent(self) -> None:
        query, _collector = _query(*self._abcd(), max_records=2)

        assert [s.request_id for s in query.snapshots()] == [
            r.request_id for r in query.records()
        ]


# ============================================================
# Metrics isolation（§十六）
# ============================================================

class TestMetricsIsolation:
    def test_metrics_still_uses_metrics_service(self) -> None:
        fake = _FakeMetricsService()
        query, _collector = _query(_record(), metrics_service=fake)

        assert query.metrics() is _FAKE_SNAPSHOT
        assert len(fake.calls) == 1

    def test_metrics_is_not_computed_from_snapshots(self) -> None:
        fake = _FakeMetricsService()
        query, _collector = _query(
            _record(), _record(request_id="req-B"), metrics_service=fake
        )

        query.metrics()

        passed = fake.calls[0]
        assert passed and all(
            isinstance(item, ToolExecutionRecord) for item in passed
        )
        assert not any(
            isinstance(item, ToolExecutionSnapshot) for item in passed
        )

    def test_default_metrics_service_unchanged(self) -> None:
        query, _collector = _query(_record())

        assert query.metrics_service is ToolExecutionMetricsService
        assert query.metrics() == ToolExecutionMetricsService.snapshot(
            query.records()
        )


# ============================================================
# Security
# ============================================================

class TestSecurity:
    def test_snapshot_module_imports_are_pure(self) -> None:
        imported = _imports(_SNAPSHOT_MODULE)
        assert imported <= {
            "__future__", "dataclasses", "datetime", "typing",
            "backend.app.services.tool_execution_record",
        }, imported
        for forbidden in (
            "sqlalchemy", "psycopg", "psycopg2", "redis", "kafka", "celery",
            "requests", "httpx", "os", "subprocess", "pathlib",
            "backend.app.api", "backend.app.llm", "backend.app.db",
            "backend.app.tools", "backend.app.services."
            "in_memory_tool_execution_collector",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imported
            ), forbidden

    def test_query_module_imports_still_pure(self) -> None:
        imported = _imports(_QUERY_MODULE)
        assert imported <= {
            "__future__", "typing",
            "backend.app.services.in_memory_tool_execution_collector",
            "backend.app.services.tool_execution_metrics_service",
            "backend.app.services.tool_execution_record",
            "backend.app.services.tool_observability_snapshot",
        }, imported

    def test_snapshot_has_no_sensitive_fields(self) -> None:
        names = {f.name for f in dataclasses.fields(ToolExecutionSnapshot)}
        for forbidden in (
            "arguments", "sql", "prompt", "llm_response", "engine",
            "session", "api_key", "password", "authorization", "traceback",
        ):
            assert forbidden not in names, forbidden

    def test_snapshot_repr_has_no_sensitive_values(self) -> None:
        query, _collector = _query(
            _record(request_id="req-A", project_id="project-a"),
            _record(request_id="req-B", success=False),
        )

        blob = repr(query.snapshots())
        for forbidden in (
            "arguments", "material_code", "SELECT", "INSERT", "DELETE",
            "postgresql://", "password", "Authorization", "Bearer ",
            "api_key", "Traceback",
        ):
            assert forbidden not in blob, forbidden

    def test_snapshot_module_has_no_io_or_timing(self) -> None:
        source = _source(_SNAPSHOT_MODULE)
        identifiers = {
            node.id.lower()
            for node in ast.walk(_tree(_SNAPSHOT_MODULE))
            if isinstance(node, ast.Name)
        }
        for forbidden in (
            "open", "connect", "execute", "session", "engine", "request",
            "httpx", "now", "monotonic", "perf_counter", "random",
        ):
            assert forbidden not in identifiers, forbidden
        assert not re.search(r"\btime\.", source)
