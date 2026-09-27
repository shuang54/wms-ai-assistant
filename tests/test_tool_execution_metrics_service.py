"""Tool Execution Metrics Read Model（Phase 3.11 Step 17）测试。

覆盖范围（§二十四）：

    Empty            空数据集 → counts 0 / rates None / average=None / max=None
    Success          all success
    Failure          all failure
    Mixed            success + failure
    Arithmetic       success_count + failure_count == total_count
    Rates            success_rate / failure_rate（math.isclose 断言）
    Duration         total / average / max（来自 record.duration_ms）
    Zero             无 ZeroDivisionError / NaN / Infinity
    Immutability     Snapshot frozen；输入不被修改
    Invalid Input    非 Record → TypeError（不静默跳过）
    Determinism      同一输入 → 同一 Snapshot
    Collector        Collector → records() → Metrics（§二十一）
    Security         Snapshot 不含 identifier / secret / result data
    Static           无 IO / DB / LLM / Tool 执行 / 计时重测 / 单例 / 持久化

全部纯内存：0 DB / 0 Network / 0 LLM / 0 Tool 执行。
"""
from __future__ import annotations

import ast
import dataclasses
import math
import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.in_memory_tool_execution_collector import (
    InMemoryToolExecutionCollector,
)
from backend.app.services.tool_execution_metrics_service import (
    ToolExecutionMetricsService,
    ToolExecutionMetricsSnapshot,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_METRICS_MODULE = "backend/app/services/tool_execution_metrics_service.py"

_STARTED = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)

#: Snapshot 字段白名单（§十七：只有 count / rate / duration）
_ALLOWED_FIELDS = frozenset({
    "total_count",
    "success_count",
    "failure_count",
    "success_rate",
    "failure_rate",
    "total_duration_ms",
    "average_duration_ms",
    "max_duration_ms",
})


def _record(
    *,
    duration_ms: float = 10.0,
    success: bool = True,
    round: int = 1,
    tool_name: str = "get_inventory",
    request_id: str = "req-1",
    project_id: str | None = None,
    error_type: str | None = None,
) -> ToolExecutionRecord:
    """构造一条真实 ToolExecutionRecord（Step 14 frozen DTO）。"""
    return ToolExecutionRecord(
        request_id=request_id,
        round=round,
        tool_name=tool_name,
        started_at=_STARTED,
        finished_at=_STARTED + timedelta(milliseconds=float(duration_ms)),
        duration_ms=duration_ms,
        success=success,
        project_id=project_id,
        error_type=error_type,
    )


def _snapshot(records) -> ToolExecutionMetricsSnapshot:
    return ToolExecutionMetricsService.snapshot(records)


def _generator(*records: ToolExecutionRecord):
    yield from records


# ============================================================
# Empty Dataset（§十五）
# ============================================================

class TestEmptyDataset:
    def test_counts_and_total_duration_are_zero(self) -> None:
        snapshot = _snapshot(())
        assert snapshot.total_count == 0
        assert snapshot.success_count == 0
        assert snapshot.failure_count == 0
        assert snapshot.total_duration_ms == 0.0
        assert isinstance(snapshot.total_duration_ms, float)

    def test_rates_average_and_max_are_none(self) -> None:
        """0 条记录 ≠ 0% 成功率；且无 NaN / Infinity（§六 / §七 / §十五）。"""
        snapshot = _snapshot(())
        assert snapshot.success_rate is None
        assert snapshot.failure_rate is None
        assert snapshot.average_duration_ms is None
        assert snapshot.max_duration_ms is None
        for value in (
            snapshot.total_duration_ms,
            snapshot.success_rate,
            snapshot.failure_rate,
            snapshot.average_duration_ms,
            snapshot.max_duration_ms,
        ):
            assert value is None or math.isfinite(value)

    def test_list_tuple_generator_agree(self) -> None:
        assert _snapshot([]) == _snapshot(()) == _snapshot(_generator())


# ============================================================
# All Success / All Failure / Mixed
# ============================================================

class TestAllSuccess:
    def test_counts_and_rates(self) -> None:
        snapshot = _snapshot([_record(duration_ms=10.0)] * 3)
        assert (snapshot.total_count, snapshot.success_count) == (3, 3)
        assert snapshot.failure_count == 0
        assert snapshot.success_rate == 1.0
        assert snapshot.failure_rate == 0.0

    def test_durations(self) -> None:
        snapshot = _snapshot([
            _record(duration_ms=10.0),
            _record(duration_ms=20.0),
            _record(duration_ms=30.0),
        ])
        assert snapshot.total_duration_ms == 60.0
        assert snapshot.average_duration_ms == 20.0
        assert snapshot.max_duration_ms == 30.0


class TestAllFailure:
    def test_counts_and_rates(self) -> None:
        snapshot = _snapshot([
            _record(success=False, duration_ms=5.0, error_type="ToolValidationError"),
            _record(success=False, duration_ms=15.0, error_type="ToolExecutionError"),
        ])
        assert (snapshot.total_count, snapshot.success_count) == (2, 0)
        assert snapshot.failure_count == 2
        assert snapshot.success_rate == 0.0
        assert snapshot.failure_rate == 1.0

    def test_durations_still_counted(self) -> None:
        """失败也有 duration（Metrics 不按 success 过滤耗时）。"""
        snapshot = _snapshot([
            _record(success=False, duration_ms=5.0),
            _record(success=False, duration_ms=15.0),
        ])
        assert snapshot.total_duration_ms == 20.0
        assert snapshot.average_duration_ms == 10.0
        assert snapshot.max_duration_ms == 15.0


class TestMixedDataset:
    """§二十一 示例：10 success / 20 success / 30 failure。"""

    def _records(self) -> list[ToolExecutionRecord]:
        return [
            _record(duration_ms=10.0),
            _record(duration_ms=20.0),
            _record(duration_ms=30.0, success=False),
        ]

    def test_counts_and_arithmetic(self) -> None:
        snapshot = _snapshot(self._records())
        assert snapshot.total_count == 3
        assert snapshot.success_count == 2
        assert snapshot.failure_count == 1
        assert (
            snapshot.success_count + snapshot.failure_count
            == snapshot.total_count
        )

    def test_rates(self) -> None:
        snapshot = _snapshot(self._records())
        assert snapshot.success_rate == pytest.approx(2 / 3)
        assert snapshot.failure_rate == pytest.approx(1 / 3)
        assert math.isclose(
            snapshot.success_rate + snapshot.failure_rate,
            1.0,
            rel_tol=1e-9,
            abs_tol=1e-12,
        )

    def test_durations(self) -> None:
        snapshot = _snapshot(self._records())
        assert snapshot.total_duration_ms == 60.0
        assert snapshot.average_duration_ms == 20.0
        assert snapshot.max_duration_ms == 30.0


# ============================================================
# Rates（§六 / §七 / §二十）
# ============================================================

class TestRatesSemantics:
    @pytest.mark.parametrize("size", [1, 7])
    def test_zero_success_with_records_is_zero_not_none(self, size: int) -> None:
        snapshot = _snapshot([
            _record(success=False, duration_ms=1.0) for _ in range(size)
        ])
        assert snapshot.success_rate == 0.0
        assert snapshot.failure_rate == 1.0

    def test_rate_is_not_rounded(self) -> None:
        """§十六：保持原始精度（1/3 不做 round）。"""
        snapshot = _snapshot([
            _record(duration_ms=1.0),
            _record(duration_ms=1.0, success=False),
            _record(duration_ms=1.0, success=False),
        ])
        assert snapshot.success_rate == 1 / 3
        assert snapshot.failure_rate == 2 / 3
        assert 0.0 <= snapshot.success_rate <= 1.0
        assert 0.0 <= snapshot.failure_rate <= 1.0


# ============================================================
# Duration Metrics（§八 / §九 / §十六）
# ============================================================

class TestDurationMetrics:
    def test_total_average_max_from_records(self) -> None:
        snapshot = _snapshot([
            _record(duration_ms=12.5),
            _record(duration_ms=7.5),
            _record(duration_ms=0.5),
        ])
        assert snapshot.total_duration_ms == 20.5
        assert snapshot.average_duration_ms == pytest.approx(20.5 / 3)
        assert snapshot.max_duration_ms == 12.5

    def test_int_duration_normalized_to_float(self) -> None:
        """Record 允许 int duration_ms；Metrics 统一输出 float。"""
        snapshot = _snapshot([_record(duration_ms=30)])
        assert isinstance(snapshot.total_duration_ms, float)
        assert isinstance(snapshot.average_duration_ms, float)
        assert isinstance(snapshot.max_duration_ms, float)
        assert snapshot.total_duration_ms == 30.0

    def test_all_zero_durations(self) -> None:
        """有记录但耗时全 0 → 0.0（不是 None：None 只属于空数据集）。"""
        snapshot = _snapshot([_record(duration_ms=0.0), _record(duration_ms=0.0)])
        assert snapshot.total_duration_ms == 0.0
        assert snapshot.average_duration_ms == 0.0
        assert snapshot.max_duration_ms == 0.0

    def test_precision_preserved(self) -> None:
        """不做 round：max 原样来自 record.duration_ms。"""
        snapshot = _snapshot([
            _record(duration_ms=0.123456789),
            _record(duration_ms=0.000000001),
        ])
        assert snapshot.max_duration_ms == 0.123456789
        assert snapshot.total_duration_ms == pytest.approx(0.12345679)
        assert snapshot.average_duration_ms == pytest.approx(0.061728395)


# ============================================================
# Immutability（§十三）
# ============================================================

class TestImmutability:
    def test_snapshot_is_frozen(self) -> None:
        assert dataclasses.is_dataclass(ToolExecutionMetricsSnapshot)
        assert ToolExecutionMetricsSnapshot.__dataclass_params__.frozen is True
        snapshot = _snapshot([_record(duration_ms=1.0)])
        with pytest.raises(dataclasses.FrozenInstanceError):
            snapshot.total_count = 10  # type: ignore[misc]

    def test_input_records_not_mutated(self) -> None:
        records = (
            _record(duration_ms=1.0),
            _record(duration_ms=2.0, success=False),
        )
        before = tuple(records)
        _snapshot(records)
        assert records == before
        assert [id(r) for r in records] == [id(r) for r in before]

    def test_each_call_returns_independent_snapshot(self) -> None:
        records = [_record(duration_ms=1.0)]
        first = _snapshot(records)
        second = _snapshot(records)
        assert first is not second
        assert first == second


# ============================================================
# Invalid Input（§十四）
# ============================================================

class TestInvalidInput:
    @pytest.mark.parametrize("bad", ["bad", 42, None, {"material_code": "M"}])
    def test_non_record_element_raises_type_error(self, bad: object) -> None:
        with pytest.raises(TypeError):
            _snapshot([_record(duration_ms=1.0), bad])

    def test_bad_tail_is_not_silently_skipped(self) -> None:
        """不允许 skip bad item（头 / 尾 / 中间一律失败）。"""
        with pytest.raises(TypeError):
            _snapshot([_record(duration_ms=1.0), object()])
        with pytest.raises(TypeError):
            _snapshot(["bad", _record(duration_ms=1.0)])
        with pytest.raises(TypeError):
            _snapshot([
                _record(duration_ms=1.0),
                "bad",
                _record(duration_ms=2.0),
            ])

    @pytest.mark.parametrize("bad", [None, "req-1", b"req-1", 42])
    def test_invalid_records_container_raises(self, bad: object) -> None:
        with pytest.raises(TypeError):
            _snapshot(bad)

    def test_error_message_names_expected_type(self) -> None:
        with pytest.raises(TypeError) as excinfo:
            _snapshot([1, 2, 3])
        assert "ToolExecutionRecord" in str(excinfo.value)

    def test_error_message_does_not_echo_element_content(self) -> None:
        with pytest.raises(TypeError) as excinfo:
            _snapshot([_record(duration_ms=1.0), "MAT-SECRET-001"])
        assert "MAT-SECRET-001" not in str(excinfo.value)


# ============================================================
# Determinism（§十九）
# ============================================================

class TestDeterminism:
    def _records(self) -> list[ToolExecutionRecord]:
        return [
            _record(duration_ms=10.0),
            _record(duration_ms=20.0, success=False),
            _record(duration_ms=30.0),
        ]

    def test_same_input_same_snapshot(self) -> None:
        records = self._records()
        assert _snapshot(records) == _snapshot(records)
        assert _snapshot(list(records)) == _snapshot(tuple(records))

    def test_order_independent(self) -> None:
        records = self._records()
        assert _snapshot(records) == _snapshot(list(reversed(records)))

    def test_generator_consumed_once(self) -> None:
        records = self._records()
        assert _snapshot(_generator(*records)) == _snapshot(records)


# ============================================================
# Collector Integration（§十二 / §二十一）
# ============================================================

class TestCollectorIntegration:
    def _collector(self) -> InMemoryToolExecutionCollector:
        collector = InMemoryToolExecutionCollector()
        for record in (
            _record(duration_ms=10.0),
            _record(duration_ms=20.0),
            _record(duration_ms=30.0, success=False),
        ):
            collector.on_execution(record)
        return collector

    def test_collector_records_to_snapshot(self) -> None:
        collector = self._collector()
        snapshot = ToolExecutionMetricsService.snapshot(collector.records())
        assert snapshot.total_count == 3
        assert snapshot.success_count == 2
        assert snapshot.failure_count == 1
        assert snapshot.success_rate == pytest.approx(2 / 3)
        assert snapshot.failure_rate == pytest.approx(1 / 3)
        assert snapshot.total_duration_ms == 60.0
        assert snapshot.average_duration_ms == 20.0
        assert snapshot.max_duration_ms == 30.0

    def test_metrics_does_not_mutate_collector(self) -> None:
        collector = self._collector()
        before = collector.records()
        _snapshot(collector.records())
        assert collector.records() == before

    def test_snapshot_stable_after_collector_clear(self) -> None:
        collector = self._collector()
        snapshot = _snapshot(collector.records())
        collector.clear()
        assert snapshot.total_count == 3          # 快照已物化，不受 clear 影响
        assert collector.records() == ()

    def test_filtered_records_can_be_summarised(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record(duration_ms=10.0))
        collector.on_execution(
            _record(duration_ms=90.0, tool_name="other_tool", success=False)
        )
        snapshot = _snapshot(
            collector.records_by_tool_name("get_inventory")
        )
        assert snapshot.total_count == 1
        assert snapshot.success_count == 1
        assert snapshot.max_duration_ms == 10.0


# ============================================================
# Security & Scope（§十七 / §十八）
# ============================================================

class TestSecurityAndScope:
    def test_field_whitelist_only(self) -> None:
        snapshot = _snapshot([_record(duration_ms=1.0)])
        snapshot.assert_field_whitelist()
        names = {f.name for f in dataclasses.fields(snapshot)}
        assert names == _ALLOWED_FIELDS
        for forbidden in (
            "arguments",
            "data",
            "sql",
            "error",
            "error_message",
            "request_id",
            "project_id",
            "tool_name",
        ):
            assert forbidden not in names

    def test_no_identifier_or_secret_in_repr(self) -> None:
        snapshot = _snapshot([
            _record(
                duration_ms=1.0,
                request_id="req-secret-123",
                project_id="project-secret",
                tool_name="secret_tool",
                success=False,
                error_type="ToolValidationError",
            )
        ])
        text = repr(snapshot)
        for secret in (
            "req-secret-123",
            "project-secret",
            "secret_tool",
            "get_inventory",
            "ToolValidationError",
            "MAT-001",
        ):
            assert secret not in text, secret

    def test_values_are_scalars_and_no_dimension_api(self) -> None:
        snapshot = _snapshot([
            _record(duration_ms=1.0),
            _record(duration_ms=2.0, success=False),
        ])
        for field in dataclasses.fields(snapshot):
            value = getattr(snapshot, field.name)
            assert isinstance(value, (int, float)) or value is None, field.name
        for forbidden in (
            "by_tool",
            "by_project",
            "by_request",
            "top_slowest_tools",
            "failure_by_tool",
            "success_rate_by_project",
            "metrics_by_tool",
        ):
            assert not hasattr(ToolExecutionMetricsService, forbidden)
            assert not hasattr(ToolExecutionMetricsSnapshot, forbidden)


# ============================================================
# Static Boundaries（§十 / §二十二 / §二十五）
# ============================================================

def _tree(path: str) -> ast.Module:
    with open(os.path.join(REPO_ROOT, *path.split("/")), encoding="utf-8") as fh:
        return ast.parse(fh.read())


def _imports(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def _identifiers(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            out.add(node.attr.lower())
    return out


class TestStaticBoundaries:
    def test_imports_are_minimal(self) -> None:
        assert _imports(_tree(_METRICS_MODULE)) == {
            "__future__",
            "math",
            "collections.abc",
            "dataclasses",
            "typing",
            "backend.app.services.tool_execution_record",
        }

    @pytest.mark.parametrize(
        "forbidden",
        [
            "sqlalchemy",
            "backend.app.db",
            "backend.app.api",
            "backend.app.llm",
            "backend.app.tools",
            "redis",
            "celery",
            "opentelemetry",
            "httpx",
        ],
    )
    def test_no_io_dependencies(self, forbidden: str) -> None:
        imports = _imports(_tree(_METRICS_MODULE))
        assert not any(name.startswith(forbidden) for name in imports)

    def test_no_timing_re_measurement(self) -> None:
        """§九：Metrics 不重新测量时间（无 perf_counter / now / random）。"""
        tree = _tree(_METRICS_MODULE)
        identifiers = _identifiers(tree)
        for forbidden in (
            "perf_counter",
            "monotonic",
            "now",
            "utcnow",
            "today",
            "sleep",
            "random",
            "randint",
        ):
            assert forbidden not in identifiers, forbidden
        assert "time" not in _imports(tree)
        assert "datetime" not in _imports(tree)

    def test_no_execution_or_storage_capability(self) -> None:
        identifiers = _identifiers(_tree(_METRICS_MODULE))
        for forbidden in (
            "execute",
            "registry",
            "handler",
            "engine",
            "session",
            "connect",
            "cursor",
            "commit",
            "rollback",
            "open",
            "write",
            "read_text",
            "dump",
            "save",
        ):
            assert forbidden not in identifiers, forbidden

    def test_no_module_level_singleton_or_factory(self) -> None:
        """§二十二 / C19.12：无模块级实例 / 无 get_default_* 工厂。"""
        tree = _tree(_METRICS_MODULE)
        expected_dto = "ToolExecutionMetricsSnapshot"
        expected_service = "ToolExecutionMetricsService"
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # 只允许私有 helper；不允许模块级工厂（get_default_* 等）
                assert node.name.startswith("_"), node.name
                assert "get_default" not in node.name
                continue
            if isinstance(node, ast.Assign):
                # 模块级只允许 __all__（字符串清单），不允许实例 / 可变全局状态
                assert isinstance(node.targets[0], ast.Name)
                assert node.targets[0].id == "__all__"
                assert isinstance(node.value, (ast.List, ast.Tuple))
            if isinstance(node, ast.AnnAssign):
                value = node.value
                if isinstance(value, ast.Call) and isinstance(
                    value.func, ast.Name
                ):
                    assert value.func.id not in {
                        expected_dto,
                        expected_service,
                    }, ast.dump(node)[:80]
        assert {
            node.name for node in tree.body if isinstance(node, ast.ClassDef)
        } == {expected_dto, expected_service}
        assert "get_default" not in _identifiers(tree)
        assert "singleton" not in _identifiers(tree)

    def test_service_is_stateless_and_pure(self) -> None:
        tree = _tree(_METRICS_MODULE)
        service_cls = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ToolExecutionMetricsService"
        )
        methods = [
            node.name
            for node in service_cls.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        assert methods == ["snapshot"]              # 无 __init__ / 无缓存字段
        assert "collector" not in _identifiers(tree)


# ============================================================
# Snapshot Arithmetic Contract（§二十）
# ============================================================

class TestSnapshotArithmeticContract:
    def test_dto_rejects_inconsistent_counts(self) -> None:
        with pytest.raises(ValueError):
            ToolExecutionMetricsSnapshot(
                total_count=3,
                success_count=1,
                failure_count=1,          # 1 + 1 != 3
                success_rate=1 / 3,
                failure_rate=1 / 3,
                total_duration_ms=3.0,
                average_duration_ms=1.0,
                max_duration_ms=1.0,
            )

    def test_dto_rejects_empty_dataset_with_rates(self) -> None:
        with pytest.raises(ValueError):
            ToolExecutionMetricsSnapshot(
                total_count=0,
                success_count=0,
                failure_count=0,
                success_rate=0.0,         # 空数据集必须 None
                failure_rate=0.0,
                total_duration_ms=0.0,
                average_duration_ms=None,
                max_duration_ms=None,
            )

    @pytest.mark.parametrize("bad", [-1.0, math.nan, math.inf])
    def test_dto_rejects_invalid_duration(self, bad: float) -> None:
        with pytest.raises(ValueError):
            ToolExecutionMetricsSnapshot(
                total_count=1,
                success_count=1,
                failure_count=0,
                success_rate=1.0,
                failure_rate=0.0,
                total_duration_ms=bad,
                average_duration_ms=bad,
                max_duration_ms=bad,
            )
