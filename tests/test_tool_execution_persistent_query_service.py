"""ToolExecutionPersistentQueryService 单元测试（Phase 3.11 Step 29）。

非 DB：Fake Repository（0 网络 / 0 DB / 0 LLM）。

覆盖（§十九）：

    1  repository 返回记录 → Snapshot 列表
    2  Row → Snapshot 显式映射
    3  11 字段全部保留
    4  None 保留
    5  empty result → []（不是 None）
    6  limit 默认值
    7  limit 边界（min / max）
    8  invalid limit → ValueError（不触达 repository）
    9  RepositoryError 原样透传（不转空列表 / 不新造异常体系）
    10 不返回 ORM Model
    11 无 JSON serialization
    12 deterministic mapping
    +  静态边界（不 import SQLAlchemy / Collector / 执行链；无写入）
"""
from __future__ import annotations

import ast
import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.db.tool_execution_repository import (
    MAX_RECENT_LIMIT,
    ToolExecutionMetricsRow,
    ToolExecutionRecordRow,
    ToolExecutionRepositoryError,
)
from backend.app.services.tool_execution_metrics_service import (
    ToolExecutionMetricsSnapshot,
)
from backend.app.services.tool_execution_persistent_query_service import (
    DEFAULT_RECENT_LIMIT,
    DEFAULT_RECENT_OFFSET,
    MIN_RECENT_LIMIT,
    ToolExecutionPersistentQueryService,
)
from backend.app.services.tool_observability_snapshot import (
    ToolExecutionSnapshot,
)

_MODULE = "backend/app/services/tool_execution_persistent_query_service.py"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STARTED = datetime(2026, 9, 28, 11, 0, 0, 123456, tzinfo=timezone.utc)

_EXPECTED_FIELDS = (
    "request_id", "round", "tool_name", "started_at", "finished_at",
    "duration_ms", "success", "project_id", "tool_call_id", "error_code",
    "error_type",
)


def _row(
    *,
    id: int = 1,
    request_id: str = "step29-test-001",
    round: int = 1,
    tool_name: str = "get_inventory",
    started_at: datetime | None = None,
    duration_ms: float = 4.5,
    success: bool = True,
    project_id: str | None = "test-project",
    tool_call_id: str | None = None,
    error_code: str | None = None,
    error_type: str | None = None,
) -> ToolExecutionRecordRow:
    started = started_at or _STARTED
    return ToolExecutionRecordRow(
        id=id,
        request_id=request_id,
        round=round,
        tool_name=tool_name,
        started_at=started,
        finished_at=started + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        success=success,
        project_id=project_id,
        tool_call_id=tool_call_id,
        error_code=error_code,
        error_type=error_type,
    )


class FakeRepository:
    """记录查询参数 + 过滤条件 + 可注入异常（duck-typed；无需 DB）。"""

    def __init__(
        self,
        rows: list[ToolExecutionRecordRow] | None = None,
        error: Exception | None = None,
        metrics_row: ToolExecutionMetricsRow | None = None,
        metrics_error: Exception | None = None,
    ) -> None:
        self.rows = rows if rows is not None else []
        self.calls: list[tuple[int, int]] = []
        self.filters: list[dict[str, object]] = []
        self.metrics_calls: list[dict[str, object]] = []
        self.request_id_calls: list[str] = []       # Step 41：Trace 读路径
        self._error = error
        self.metrics_row = metrics_row or ToolExecutionMetricsRow(
            total_count=0,
            success_count=0,
            failure_count=0,
            total_duration_ms=None,
            average_duration_ms=None,
            max_duration_ms=None,
        )
        self._metrics_error = metrics_error

    def list_recent(
        self,
        *,
        limit: int,
        offset: int = 0,
        project_id: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> list[ToolExecutionRecordRow]:
        self.calls.append((limit, offset))
        self.filters.append({
            "project_id": project_id,
            "tool_name": tool_name,
            "success": success,
        })
        if self._error is not None:
            raise self._error
        return list(self.rows[offset: offset + limit])

    def get_by_request_id(
        self, request_id: str
    ) -> list[ToolExecutionRecordRow]:
        """Step 41：按 request_id 取全部行（同一 request 内的执行顺序）。"""
        self.request_id_calls.append(request_id)
        if self._error is not None:
            raise self._error
        return [row for row in self.rows if row.request_id == request_id]

    def get_metrics(
        self,
        *,
        project_id: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> ToolExecutionMetricsRow:
        self.metrics_calls.append({
            "project_id": project_id,
            "tool_name": tool_name,
            "success": success,
        })
        if self._metrics_error is not None:
            raise self._metrics_error
        return self.metrics_row


def _service(
    rows: list[ToolExecutionRecordRow] | None = None,
    error: Exception | None = None,
) -> tuple[ToolExecutionPersistentQueryService, FakeRepository]:
    repository = FakeRepository(rows=rows, error=error)
    return (
        ToolExecutionPersistentQueryService(repository=repository),  # type: ignore[arg-type]
        repository,
    )


# ============================================================
# 1 ~ 5：查询 / 映射 / None / 空结果
# ============================================================

class TestListRecentMapping:
    def test_1_repository_rows_become_snapshots(self) -> None:
        service, repository = _service([_row(id=3), _row(id=2)])

        snapshots = service.list_recent(limit=2)

        assert repository.calls == [(2, 0)]
        assert [s.request_id for s in snapshots] == [
            "step29-test-001", "step29-test-001"
        ]
        assert all(
            isinstance(s, ToolExecutionSnapshot) for s in snapshots
        )

    def test_2_row_to_snapshot_explicit_mapping(self) -> None:
        row = _row(tool_call_id="call_1", error_type="ToolValidationError")
        service, _repository = _service([row])

        snapshot = service.list_recent(limit=1)[0]

        assert snapshot is not row
        assert snapshot.request_id == row.request_id
        assert snapshot.round == row.round
        assert snapshot.tool_name == row.tool_name
        assert snapshot.started_at == row.started_at
        assert snapshot.finished_at == row.finished_at
        assert snapshot.duration_ms == row.duration_ms
        assert snapshot.success == row.success
        assert snapshot.project_id == row.project_id
        assert snapshot.tool_call_id == row.tool_call_id
        assert snapshot.error_code == row.error_code
        assert snapshot.error_type == row.error_type

    def test_3_all_eleven_fields_preserved(self) -> None:
        service, _repository = _service([_row()])

        snapshot = service.list_recent(limit=1)[0]

        assert tuple(vars(snapshot)) == _EXPECTED_FIELDS
        assert len(vars(snapshot)) == 11

    def test_3b_primary_key_not_exposed(self) -> None:
        """主键 id 不泄漏到 Read Model（§十二）。"""
        service, _repository = _service([_row(id=987_654)])

        snapshot = service.list_recent(limit=1)[0]

        assert not hasattr(snapshot, "id")
        assert "987654" not in repr(snapshot)

    def test_4_none_preserved(self) -> None:
        service, _repository = _service([
            _row(
                project_id=None,
                tool_call_id=None,
                error_code=None,
                error_type=None,
            )
        ])

        snapshot = service.list_recent(limit=1)[0]

        assert snapshot.project_id is None
        assert snapshot.tool_call_id is None
        assert snapshot.error_code is None
        assert snapshot.error_type is None

    def test_5_empty_result_is_empty_list(self) -> None:
        service, _repository = _service([])

        result = service.list_recent()

        assert result == []
        assert result is not None
        assert isinstance(result, list)

    def test_5b_failure_record_mapping(self) -> None:
        service, _repository = _service([
            _row(success=False, error_code="invalid_argument",
                 error_type="ToolValidationError")
        ])

        snapshot = service.list_recent(limit=1)[0]

        assert snapshot.success is False
        assert snapshot.error_code == "invalid_argument"
        assert snapshot.error_type == "ToolValidationError"


# ============================================================
# 6 ~ 9：limit / 异常语义
# ============================================================

class TestLimitAndErrors:
    def test_6_default_limit_is_100(self) -> None:
        service, repository = _service([])

        service.list_recent()

        assert DEFAULT_RECENT_LIMIT == 100
        assert repository.calls == [(DEFAULT_RECENT_LIMIT, DEFAULT_RECENT_OFFSET)]

    def test_7_limit_boundaries(self) -> None:
        service, repository = _service([])

        service.list_recent(limit=MIN_RECENT_LIMIT)
        service.list_recent(limit=MAX_RECENT_LIMIT)

        assert repository.calls == [
            (MIN_RECENT_LIMIT, 0), (MAX_RECENT_LIMIT, 0)
        ]

    def test_8_invalid_limit_rejected_before_repository(self) -> None:
        service, repository = _service([])

        for bad in (0, -5, MAX_RECENT_LIMIT + 1, 999_999_999, "10", 2.5, True):
            with pytest.raises(ValueError):
                service.list_recent(limit=bad)  # type: ignore[arg-type]
        assert repository.calls == []            # 未触达 repository

    # ---- Step 31：offset（分页） ----

    def test_offset_default_zero_and_passed_through(self) -> None:
        service, repository = _service([])

        service.list_recent(limit=10)

        assert DEFAULT_RECENT_OFFSET == 0
        assert repository.calls == [(10, 0)]

    def test_offset_values_passed_through(self) -> None:
        service, repository = _service([])

        service.list_recent(limit=2, offset=0)
        service.list_recent(limit=2, offset=2)
        service.list_recent(limit=2, offset=100)
        service.list_recent(limit=2, offset=999_999_999)   # 无 MAX_OFFSET

        assert repository.calls == [
            (2, 0), (2, 2), (2, 100), (2, 999_999_999)
        ]

    def test_invalid_offset_rejected_before_repository(self) -> None:
        service, repository = _service([])

        for bad in (-1, -100, "0", 1.5, True):
            with pytest.raises(ValueError):
                service.list_recent(offset=bad)  # type: ignore[arg-type]
        assert repository.calls == []            # 未触达 repository

    def test_page_consistency_is_delegated_to_repository(self) -> None:
        """分页一致性：同一排序窗口切片（Service 不重排 / 不去重）。"""
        rows = [_row(id=value) for value in (5, 4, 3, 2, 1)]
        service, _repository = _service(rows)

        page1 = service.list_recent(limit=2, offset=0)
        page2 = service.list_recent(limit=2, offset=2)
        page3 = service.list_recent(limit=2, offset=4)
        whole = service.list_recent(limit=5, offset=0)

        assert [s.request_id for s in page1 + page2 + page3] == [
            s.request_id for s in whole
        ]
        assert len(page3) == 1

    def test_offset_beyond_records_is_empty_list(self) -> None:
        service, repository = _service([_row()])

        result = service.list_recent(limit=100, offset=100)

        assert result == []
        assert repository.calls == [(100, 100)]

    def test_9_repository_error_propagated_unchanged(self) -> None:
        """DB failure ≠ empty database（不转 []，不新造异常体系）。"""
        service, _repository = _service(
            error=ToolExecutionRepositoryError("query failed")
        )

        with pytest.raises(ToolExecutionRepositoryError):
            service.list_recent()

    def test_9b_unexpected_error_propagated(self) -> None:
        service, _repository = _service(error=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            service.list_recent()

    def test_9c_single_query_no_retry(self) -> None:
        service, repository = _service(error=ToolExecutionRepositoryError("x"))

        with pytest.raises(ToolExecutionRepositoryError):
            service.list_recent()

        assert len(repository.calls) == 1

    def test_10_no_orm_model_returned(self) -> None:
        service, _repository = _service([_row()])

        snapshot = service.list_recent(limit=1)[0]

        assert isinstance(snapshot, ToolExecutionSnapshot)
        assert not hasattr(snapshot, "__tablename__")
        assert type(snapshot).__name__ == "ToolExecutionSnapshot"

    def test_12_deterministic_mapping(self) -> None:
        rows = [_row(id=2), _row(id=1)]

        first, _ = _service(rows)
        second, _ = _service(rows)

        assert first.list_recent(limit=2) == second.list_recent(limit=2)

    def test_12b_order_preserved_from_repository(self) -> None:
        """Service 不重排：顺序完全来自 Repository（started_at DESC, id DESC）。"""
        rows = [
            _row(id=11, started_at=_STARTED),
            _row(id=10, started_at=_STARTED),
            _row(id=9, started_at=_STARTED - timedelta(seconds=1)),
        ]
        service, _repository = _service(rows)

        snapshots = service.list_recent(limit=3)

        assert [s.round for s in snapshots] == [1, 1, 1]
        assert len(snapshots) == 3


# ============================================================
# Step 33：Persistent Metrics（SQL 聚合 + 应用层比率）
# ============================================================

class TestListByRequestId:
    """Phase 3.12 Step 41：Assistant Trace 的 Tool 数据源（持久化读路径）。"""

    @staticmethod
    def _service(
        rows: list[ToolExecutionRecordRow] | None = None,
        error: Exception | None = None,
    ) -> tuple[ToolExecutionPersistentQueryService, "FakeRepository"]:
        repository = FakeRepository(rows=rows, error=error)
        return (
            ToolExecutionPersistentQueryService(repository=repository),  # type: ignore[arg-type]
            repository,
        )

    def test_returns_snapshots_in_repository_order(self) -> None:
        rows = [
            _row(id=1, request_id="A", round=1),
            _row(id=2, request_id="A", round=2),
        ]
        service, repository = self._service(rows)

        snapshots = service.list_by_request_id("A")

        assert repository.request_id_calls == ["A"]      # 精确透传
        assert [type(s).__name__ for s in snapshots] == [
            "ToolExecutionSnapshot", "ToolExecutionSnapshot",
        ]
        assert [s.round for s in snapshots] == [1, 2]     # 不重排序
        assert all(not hasattr(s, "id") for s in snapshots)   # 主键不外泄

    def test_empty_result_is_not_an_error(self) -> None:
        service, _repository = self._service()

        assert service.list_by_request_id("not-exist") == []

    @pytest.mark.parametrize("bad", [None, "", "   ", 123, "x" * 129])
    def test_invalid_request_id_rejected_before_db(self, bad) -> None:
        service, repository = self._service()

        with pytest.raises(ValueError):
            service.list_by_request_id(bad)  # type: ignore[arg-type]

        assert repository.request_id_calls == []          # 未触达 Repository

    def test_repository_error_propagates(self) -> None:
        service, _repository = self._service(
            error=ToolExecutionRepositoryError("db down")
        )

        with pytest.raises(ToolExecutionRepositoryError):
            service.list_by_request_id("A")

    def test_does_not_use_runtime_collector(self) -> None:
        """数据源 = 持久化仓储；代码层不依赖 Runtime Collector。

        （模块文档字符串会用 Collector 做边界对照说明，因此按 import / AST
        断言而不是原始文本断言。）
        """
        with open(
            os.path.join(_REPO_ROOT, *_MODULE.split("/")), encoding="utf-8"
        ) as handle:
            tree = ast.parse(handle.read())

        imports = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not any(
            "in_memory_tool_execution_collector" in name for name in imports
        ), imports
        attributes = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        assert "snapshots_by_request_id" not in attributes, attributes


class TestPersistentMetrics:
    @staticmethod
    def _service(
        *,
        metrics_row: ToolExecutionMetricsRow | None = None,
        metrics_error: Exception | None = None,
    ) -> tuple[ToolExecutionPersistentQueryService, FakeRepository]:
        repository = FakeRepository(
            metrics_row=metrics_row, metrics_error=metrics_error
        )
        return (
            ToolExecutionPersistentQueryService(repository=repository),  # type: ignore[arg-type]
            repository,
        )

    def test_empty_metrics_semantics(self) -> None:
        service, repository = self._service()

        metrics = service.metrics()

        assert isinstance(metrics, ToolExecutionMetricsSnapshot)
        assert metrics.total_count == 0
        assert metrics.success_count == 0
        assert metrics.failure_count == 0
        assert metrics.success_rate is None          # 不是 0
        assert metrics.failure_rate is None
        assert metrics.average_duration_ms is None
        assert metrics.max_duration_ms is None
        assert metrics.total_duration_ms == 0.0      # 与 Runtime Metrics 一致
        assert repository.metrics_calls == [{
            "project_id": None, "tool_name": None, "success": None
        }]

    def test_all_success_metrics(self) -> None:
        service, _repository = self._service(
            metrics_row=ToolExecutionMetricsRow(
                total_count=3,
                success_count=3,
                failure_count=0,
                total_duration_ms=600.0,
                average_duration_ms=200.0,
                max_duration_ms=300.0,
            )
        )

        metrics = service.metrics()

        assert metrics.total_count == 3
        assert metrics.success_rate == 1.0
        assert metrics.failure_rate == 0.0
        assert metrics.average_duration_ms == 200.0
        assert metrics.max_duration_ms == 300.0
        assert metrics.total_duration_ms == 600.0

    def test_mixed_metrics_rates(self) -> None:
        service, _repository = self._service(
            metrics_row=ToolExecutionMetricsRow(
                total_count=5,
                success_count=3,
                failure_count=2,
                total_duration_ms=1000.0,
                average_duration_ms=200.0,
                max_duration_ms=400.0,
            )
        )

        metrics = service.metrics()

        assert pytest.approx(metrics.success_rate, rel=1e-12) == 0.6
        assert pytest.approx(metrics.failure_rate, rel=1e-12) == 0.4
        assert pytest.approx(
            metrics.success_rate + metrics.failure_rate, rel=1e-12
        ) == 1.0

    def test_filters_passed_through_unchanged(self) -> None:
        service, repository = self._service()

        service.metrics(
            project_id="project-a",
            tool_name="get_inventory",
            success=True,
        )

        assert repository.metrics_calls == [{
            "project_id": "project-a",
            "tool_name": "get_inventory",
            "success": True,
        }]

    def test_success_false_not_treated_as_missing(self) -> None:
        service, repository = self._service()

        service.metrics(success=False)

        assert repository.metrics_calls[0]["success"] is False

    def test_empty_string_filter_is_literal(self) -> None:
        service, repository = self._service()

        service.metrics(project_id="", tool_name="")

        assert repository.metrics_calls[0]["project_id"] == ""
        assert repository.metrics_calls[0]["tool_name"] == ""

    def test_invalid_filters_rejected_before_repository(self) -> None:
        service, repository = self._service()

        for kwargs in (
            {"project_id": 123},
            {"tool_name": 123},
            {"success": 1},
        ):
            with pytest.raises(ValueError):
                service.metrics(**kwargs)  # type: ignore[arg-type]
        assert repository.metrics_calls == []

    def test_repository_error_propagated(self) -> None:
        service, _repository = self._service(
            metrics_error=ToolExecutionRepositoryError("db down")
        )

        with pytest.raises(ToolExecutionRepositoryError):
            service.metrics()

    def test_single_query_no_retry(self) -> None:
        service, repository = self._service(
            metrics_error=ToolExecutionRepositoryError("db down")
        )

        with pytest.raises(ToolExecutionRepositoryError):
            service.metrics()

        assert len(repository.metrics_calls) == 1

    @staticmethod
    def _tree() -> ast.Module:
        with open(
            os.path.join(_REPO_ROOT, *_MODULE.split("/")), encoding="utf-8"
        ) as handle:
            return ast.parse(handle.read())

    def test_no_python_aggregation_in_metrics(self) -> None:
        """比率在应用层计算；聚合（count / sum / avg / max）不在 Python 侧。"""
        tree = self._tree()
        metrics_fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "metrics"
        )
        called = {
            node.func.id
            for node in ast.walk(metrics_fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for forbidden in ("sum", "min", "max", "len", "sorted"):
            assert forbidden not in called, forbidden
        assert not [
            node
            for node in ast.walk(metrics_fn)
            if isinstance(node, (ast.ListComp, ast.GeneratorExp))
        ]


# ============================================================
# 静态边界（C35 相关）
# ============================================================

class TestPersistentQueryStaticBoundaries:
    @staticmethod
    def _tree() -> ast.Module:
        with open(
            os.path.join(_REPO_ROOT, *_MODULE.split("/")), encoding="utf-8"
        ) as handle:
            return ast.parse(handle.read())

    def test_11_no_json_serialization(self) -> None:
        tree = self._tree()
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in ("dumps", "loads", "asdict", "model_dump",
                          "isoformat"):
            assert forbidden not in identifiers, forbidden

    # ---- Step 32：精确过滤（project_id / tool_name / success） ----

    def test_filters_default_to_none(self) -> None:
        service, repository = _service([])

        service.list_recent()

        assert repository.filters == [{
            "project_id": None, "tool_name": None, "success": None
        }]

    def test_filters_passed_through_unchanged(self) -> None:
        service, repository = _service([])

        service.list_recent(
            limit=20,
            offset=5,
            project_id="project-a",
            tool_name="get_inventory",
            success=True,
        )

        assert repository.calls == [(20, 5)]
        assert repository.filters == [{
            "project_id": "project-a",
            "tool_name": "get_inventory",
            "success": True,
        }]

    def test_success_false_is_not_treated_as_missing(self) -> None:
        service, repository = _service([])

        service.list_recent(success=False)

        assert repository.filters[0]["success"] is False

    def test_empty_string_filter_is_literal_value(self) -> None:
        service, repository = _service([])

        service.list_recent(project_id="", tool_name="")

        assert repository.filters[0]["project_id"] == ""
        assert repository.filters[0]["tool_name"] == ""

    def test_invalid_filter_types_rejected_before_repository(self) -> None:
        service, repository = _service([])

        with pytest.raises(ValueError):
            service.list_recent(project_id=123)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            service.list_recent(tool_name=123)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            service.list_recent(success=1)  # type: ignore[arg-type]
        assert repository.calls == []                 # 未触达 repository

    def test_no_python_filtering_in_service(self) -> None:
        """Service 不做 Python 过滤（AST：无 filter / 推导式过滤）。"""
        tree = self._tree()
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        for forbidden in ("filter", "sorted", "order_by"):
            assert forbidden not in called, forbidden

    def test_no_sqlalchemy_or_orm_import(self) -> None:
        tree = self._tree()
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        for forbidden in (
            "sqlalchemy",
            "backend.app.db.session",
            "backend.app.db.models.tool_execution_record",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imported
            ), forbidden
        # Repository 是本层唯一依赖（application read boundary）
        assert any(
            name.endswith("db.tool_execution_repository")
            for name in imported
        ), imported

    def test_no_collector_or_execution_chain_import(self) -> None:
        tree = self._tree()
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        for forbidden in (
            "backend.app.services.in_memory_tool_execution_collector",
            "backend.app.services.tool_execution_service",
            "backend.app.services.ai_orchestrator_service",
            "backend.app.tools",
            "backend.app.api",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imported
            ), forbidden

    def test_read_only_no_write_capability(self) -> None:
        """只读边界（Step 33：+ ``metrics()`` 只读聚合，仍无写能力）。"""
        public = {
            name
            for name in dir(ToolExecutionPersistentQueryService)
            if not name.startswith("_")
        }
        # Step 41：+ list_by_request_id（Assistant Trace 的只读 Tool 数据源）
        assert public == {
            "list_recent", "list_by_request_id", "metrics", "repository",
        }, public
        identifiers = {
            node.id.lower()
            for node in ast.walk(self._tree())
            if isinstance(node, ast.Name)
        } | {
            node.attr.lower()
            for node in ast.walk(self._tree())
            if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "create", "insert", "update", "delete", "truncate", "commit",
            "rollback", "session", "engine", "clear", "evict",
        ):
            assert forbidden not in identifiers, forbidden
