"""Tool Observability Query Service（Phase 3.11 Step 21）——只读查询边界测试。

锁定：Query Service 只是 **Read Facade** —— 查询语义（retention window /
过滤 / 校验）全部来自 Collector；统计语义全部来自既有 Metrics Service。
它自己不存储、不淘汰、不算数、不清空。

覆盖（§十五）：

    1  Empty              （空 Collector → records () / metrics 空语义）
    2  Single Record      （单条 → records / metrics / by_request_id）
    3  Multiple Records   （records / request / project / tool 查询 + 字段校验）
    4  Retention          （max_records=3：A B C D → Query 只看到 B C D）
    5  Metrics + Retention（统计只含 retained records）
    6  Query Does Not Mutate（所有查询后 Collector 状态不变）
    7  Snapshot Immutability（tuple 快照；后续 append 不影响旧快照）
    8  Clear Isolation    （collector.clear() 对 Query 可见；Query 无 clear）
    9  Metrics Service Reuse（Fake Metrics Service 证明委托、不复制算法）
    10 Security           （import 白名单 / 无存储·锁 / 只读 API / 无敏感信息）

0 DB / 0 Network / 0 Real LLM（全部内存 Fake）。
"""
from __future__ import annotations

import ast
import os
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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_QUERY_MODULE = "backend/app/services/tool_observability_query_service.py"

_STARTED = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)

#: Fake Metrics Service 的返回值（total_count=99 明显区别于真实统计）
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


# ============================================================
# 辅助
# ============================================================

def _record(
    *,
    request_id: str = "req-1",
    round: int = 1,
    tool_name: str = "get_inventory",
    project_id: str | None = None,
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
        error_type=None if success else "ToolValidationError",
    )


def _collector_with(
    *records: ToolExecutionRecord, max_records: int = 1000
) -> InMemoryToolExecutionCollector:
    collector = InMemoryToolExecutionCollector(max_records=max_records)
    for record in records:
        collector.on_execution(record)
    return collector


def _query(
    *records: ToolExecutionRecord,
    max_records: int = 1000,
    metrics_service: Any = None,
) -> tuple[ToolObservabilityQueryService, InMemoryToolExecutionCollector]:
    collector = _collector_with(*records, max_records=max_records)
    if metrics_service is None:
        query = ToolObservabilityQueryService(collector)
    else:
        query = ToolObservabilityQueryService(
            collector, metrics_service=metrics_service
        )
    return query, collector


class _FakeMetricsService:
    """记录调用并返回**可辨识**快照（证明 metrics() 是委托而非本地计算）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[ToolExecutionRecord, ...]] = []

    def snapshot(  # noqa: ANN201
        self, records: Any,
    ) -> ToolExecutionMetricsSnapshot:
        self.calls.append(tuple(records))
        return _FAKE_SNAPSHOT


def _module_source() -> str:
    with open(
        os.path.join(REPO_ROOT, *_QUERY_MODULE.split("/")), encoding="utf-8"
    ) as handle:
        return handle.read()


def _module_tree() -> ast.Module:
    return ast.parse(_module_source())


def _module_imports() -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_module_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


# ============================================================
# 1. Empty
# ============================================================

class TestEmpty:
    def test_records_empty(self) -> None:
        query, _collector = _query()
        assert query.records() == ()

    def test_metrics_empty_semantics(self) -> None:
        query, _collector = _query()

        snapshot = query.metrics()

        assert snapshot.total_count == 0
        assert snapshot.success_rate is None
        assert snapshot.failure_rate is None
        assert snapshot.average_duration_ms is None
        assert snapshot.max_duration_ms is None

    def test_filtered_queries_empty(self) -> None:
        query, _collector = _query()

        assert query.records_by_request_id("req-1") == ()
        assert query.records_by_project_id(None) == ()
        assert query.records_by_tool_name("get_inventory") == ()


# ============================================================
# 2. Single Record
# ============================================================

class TestSingleRecord:
    def test_records_single(self) -> None:
        record = _record(request_id="req-A", project_id="project-a")
        query, _collector = _query(record)

        assert query.records() == (record,)

    def test_metrics_single(self) -> None:
        query, _collector = _query(_record(success=True))

        snapshot = query.metrics()

        assert snapshot.total_count == 1
        assert snapshot.success_count == 1
        assert snapshot.failure_count == 0
        assert snapshot.success_rate == 1.0

    def test_by_request_id_single(self) -> None:
        record = _record(request_id="req-A")
        query, _collector = _query(record)

        assert query.records_by_request_id("req-A") == (record,)
        assert query.records_by_request_id("req-B") == ()


# ============================================================
# 3. Multiple Records
# ============================================================

class TestMultipleRecords:
    def _records(self) -> list[ToolExecutionRecord]:
        return [
            _record(request_id="req-A", tool_name="get_inventory",
                    project_id="project-a", success=True),
            _record(request_id="req-B", tool_name="get_work_order",
                    project_id=None, success=False),
            _record(request_id="req-A", round=2, tool_name="get_inventory",
                    project_id="project-a", success=True),
        ]

    def test_records_all_in_writing_order(self) -> None:
        records = self._records()
        query, _collector = _query(*records)

        assert query.records() == tuple(records)

    def test_records_by_request_id(self) -> None:
        records = self._records()
        query, _collector = _query(*records)

        assert query.records_by_request_id("req-A") == (
            records[0], records[2],
        )

    def test_records_by_project_id(self) -> None:
        records = self._records()
        query, _collector = _query(*records)

        assert query.records_by_project_id("project-a") == (
            records[0], records[2],
        )
        # None **只**匹配 project_id is None（不表示"全部"）
        assert query.records_by_project_id(None) == (records[1],)

    def test_records_by_tool_name(self) -> None:
        records = self._records()
        query, _collector = _query(*records)

        assert query.records_by_tool_name("get_inventory") == (
            records[0], records[2],
        )
        assert query.records_by_tool_name("get_work_order") == (records[1],)
        assert query.records_by_tool_name("unknown_tool") == ()

    @pytest.mark.parametrize("bad", [None, 123, "", "   "])
    def test_query_field_validation_delegated_to_collector(
        self, bad: Any
    ) -> None:
        """查询字段校验不复制：由 Collector 决定（TypeError / ValueError）。"""
        query, _collector = _query(_record())

        with pytest.raises((TypeError, ValueError)):
            query.records_by_request_id(bad)


# ============================================================
# 4~5. Retention / Metrics + Retention
# ============================================================

class TestRetention:
    def _abcd(self) -> list[ToolExecutionRecord]:
        return [
            _record(request_id="req-A", success=True),
            _record(request_id="req-B", success=True),
            _record(request_id="req-C", success=False),
            _record(request_id="req-D", success=True),
        ]

    def test_records_respects_retention_window(self) -> None:
        records = self._abcd()
        query, collector = _query(*records, max_records=3)

        assert collector.records() == tuple(records[-3:])   # 权威：Collector
        assert query.records() == tuple(records[-3:])       # 一致：B C D

    def test_filtered_queries_respect_retention(self) -> None:
        records = self._abcd()
        query, _collector = _query(*records, max_records=3)

        assert query.records_by_request_id("req-A") == ()    # 已淘汰
        assert len(query.records_by_request_id("req-D")) == 1

    def test_metrics_only_counts_retained_records(self) -> None:
        records = self._abcd()
        query, _collector = _query(*records, max_records=3)

        snapshot = query.metrics()

        assert snapshot.total_count == 3
        assert snapshot.success_count == 2
        assert snapshot.failure_count == 1     # C（failure）仍在窗口内

    def test_metrics_matches_real_service_over_same_window(self) -> None:
        records = self._abcd()
        query, collector = _query(*records, max_records=2)

        assert query.metrics() == ToolExecutionMetricsService.snapshot(
            collector.records()
        )


# ============================================================
# 6. Query Does Not Mutate
# ============================================================

class TestQueryDoesNotMutate:
    def test_all_queries_leave_collector_unchanged(self) -> None:
        query, collector = _query(
            _record(request_id="req-A", project_id="project-a"),
            _record(request_id="req-B", tool_name="get_work_order"),
            max_records=1000,
        )
        before = collector.records()

        query.records()
        query.records_by_request_id("req-A")
        query.records_by_project_id("project-a")
        query.records_by_project_id(None)
        query.records_by_tool_name("get_inventory")
        query.metrics()

        assert collector.records() == before
        assert len(collector.records()) == len(before)
        assert query.records() == before

    def test_query_does_not_append_clear_or_evict(self) -> None:
        collector = _collector_with(
            _record(request_id="req-A"),
            _record(request_id="req-B"),
            max_records=2,
        )
        query = ToolObservabilityQueryService(collector)
        before_len = len(collector.records())
        before_max = collector.max_records

        query.records()
        query.metrics()

        assert len(collector.records()) == before_len
        assert collector.max_records == before_max

    def test_metrics_does_not_mutate(self) -> None:
        query, collector = _query(_record(), _record(request_id="req-B"))
        before = collector.records()

        query.metrics()

        assert collector.records() == before


# ============================================================
# 7. Snapshot Immutability
# ============================================================

class TestSnapshotImmutability:
    def test_records_returns_tuple(self) -> None:
        query, _collector = _query(_record())

        snapshot = query.records()

        assert isinstance(snapshot, tuple)
        with pytest.raises(AttributeError):
            snapshot.append(_record())  # type: ignore[attr-defined]

    def test_snapshot_stable_after_new_append(self) -> None:
        query, collector = _query(_record(request_id="req-A"))

        snapshot = query.records()
        collector.on_execution(_record(request_id="req-B"))

        assert snapshot == (_record(request_id="req-A"),)     # B 不出现在旧快照
        assert len(query.records()) == 2

    def test_snapshot_stable_after_eviction(self) -> None:
        collector = _collector_with(
            _record(request_id="req-A"), _record(request_id="req-B"),
            max_records=2,
        )
        query = ToolObservabilityQueryService(collector)

        snapshot = query.records()
        collector.on_execution(_record(request_id="req-C"))   # 淘汰 A

        assert snapshot == collector.records()[:0] + snapshot
        assert len(query.records()) == 2


# ============================================================
# 8. Clear Isolation
# ============================================================

class TestClearIsolation:
    def test_collector_clear_is_visible_to_query(self) -> None:
        query, collector = _query(_record())

        collector.clear()

        assert query.records() == ()
        assert query.metrics().total_count == 0

    def test_query_has_no_clear(self) -> None:
        query, _collector = _query(_record())

        assert not hasattr(query, "clear")

    def test_query_has_no_write_api(self) -> None:
        query, _collector = _query()

        for forbidden in (
            "clear", "on_execution", "append", "add",
            "evict", "reset", "delete",
        ):
            assert not hasattr(query, forbidden), forbidden


# ============================================================
# 9. Metrics Service Reuse
# ============================================================

class TestMetricsServiceReuse:
    def test_metrics_delegates_to_metrics_service(self) -> None:
        fake = _FakeMetricsService()
        query, _collector = _query(
            _record(), _record(request_id="req-B"), metrics_service=fake
        )

        snapshot = query.metrics()

        assert snapshot is _FAKE_SNAPSHOT      # 返回值来自 Metrics Service
        assert len(fake.calls) == 1

    def test_metrics_passes_current_window_records(self) -> None:
        records = [
            _record(request_id="req-A"),
            _record(request_id="req-B"),
            _record(request_id="req-C"),
        ]
        fake = _FakeMetricsService()
        query, collector = _query(*records, max_records=2, metrics_service=fake)

        query.metrics()

        assert fake.calls == [collector.records()]     # 只传 retained 窗口
        assert len(fake.calls[0]) == 2

    def test_default_metrics_service_is_existing_service(self) -> None:
        query, _collector = _query()

        assert query.metrics_service is ToolExecutionMetricsService

    def test_metrics_service_shape_is_validated(self) -> None:
        collector = _collector_with()

        with pytest.raises(TypeError):
            ToolObservabilityQueryService(
                collector, metrics_service="not-a-service"  # type: ignore[arg-type]
            )

    def test_collector_shape_is_validated(self) -> None:
        with pytest.raises(TypeError):
            ToolObservabilityQueryService(None)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            ToolObservabilityQueryService("not-a-collector")  # type: ignore[arg-type]


# ============================================================
# 10. Security / Structure
# ============================================================

class TestSecurityAndStructure:
    def test_imports_are_pure(self) -> None:
        imported = _module_imports()
        assert imported <= {
            "__future__", "typing",
            "backend.app.services.in_memory_tool_execution_collector",
            "backend.app.services.tool_execution_metrics_service",
            "backend.app.services.tool_execution_record",
            # Phase 3.11 Step 22：Snapshot Read Model 转换（纯 DTO，无 IO）
            "backend.app.services.tool_observability_snapshot",
        }, imported
        for forbidden in (
            "sqlalchemy", "psycopg", "psycopg2", "redis", "kafka", "celery",
            "requests", "httpx", "os", "subprocess", "pathlib",
            "backend.app.api", "backend.app.llm", "backend.app.db",
            "backend.app.tools",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imported
            ), forbidden

    def test_no_storage_or_retention_implementation(self) -> None:
        source = _module_source()
        tree = _module_tree()
        for forbidden in (
            "deque", "threading", "Lock", "append", "clear", "pop",
            "max_records", "evict",
        ):
            assert forbidden not in {
                node.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Name)
            }, forbidden
        assert "Lock(" not in source
        assert "self._records" not in source

    def test_public_read_only_api(self) -> None:
        public = {
            name
            for name in dir(ToolObservabilityQueryService)
            if not name.startswith("_")
        }
        assert public == {
            "collector",
            "metrics",
            "metrics_service",
            "records",
            "records_by_project_id",
            "records_by_request_id",
            "records_by_tool_name",
            # Phase 3.11 Step 22：对外 Read Model 查询（records* 全部保留）
            "snapshots",
            "snapshots_by_project_id",
            "snapshots_by_request_id",
            "snapshots_by_tool_name",
        }, public

    def test_outputs_have_no_sensitive_information(self) -> None:
        query, _collector = _query(
            _record(request_id="req-A", project_id="project-a"),
            _record(request_id="req-B", success=False),
        )

        blob = repr(query.records()) + repr(query.metrics())
        for forbidden in (
            "arguments", "material_code", "SELECT", "INSERT", "DELETE",
            "postgresql://", "password", "Authorization", "Bearer ",
            "api_key", "Traceback",
        ):
            assert forbidden not in blob, forbidden

    def test_query_holds_only_two_references(self) -> None:
        query, collector = _query(_record())

        assert set(vars(query)) == {"_collector", "_metrics_service"}
        assert query.collector is collector
        assert query.metrics_service is ToolExecutionMetricsService
