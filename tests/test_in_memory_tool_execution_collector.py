"""In-Memory Tool Execution Collector Contract（Phase 3.11 Step 16）。

```text
ToolExecutionService
        ↓
ToolExecutionRecord（frozen）
        ↓
ToolExecutionObserver
        ↓
InMemoryToolExecutionCollector
        ↓
Read-only Query（tuple 快照）
```

覆盖（§二十二）：

```text
初始化 / 单条 / 多条（顺序）/ Snapshot（tuple 不可变）/ Record Identity（不复制）
Request 查询（match / no match / multiple / 顺序）
Project 查询（project-a / project-b / None 只匹配 None）
Tool 查询（get_inventory / get_work_order / unknown / 严格相等）
Clear（记录 → clear → ()）
Isolation（两个 Collector 互不影响）
Security（Record 中无 arguments / data / SQL / secrets）
Determinism（同插入顺序 → 同查询结果）
统计禁用（无 count / success_rate / p95 …）
无单例（显式实例）/ 输入校验 / 线程安全 smoke
集成：ToolExecutionService（成功 + 失败）/ ToolChatService（round 1,2,3 + request_id）
Retention（Phase 3.11 Step 20）：max_records 校验 / FIFO 淘汰 / 查询窗口 /
    Metrics 只读当前窗口 / clear 语义 / 并发 append + 淘汰 / 不可变快照 /
    无 TTL·后台清理·持久化
```

0 DB / 0 Network / 0 Real LLM。
"""
from __future__ import annotations

import dataclasses
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.app.services.in_memory_tool_execution_collector import (
    InMemoryToolExecutionCollector,
)
from backend.app.services.tool_chat_service import ToolChatService
from backend.app.services.tool_execution_context import ToolExecutionContext
from backend.app.services.tool_execution_observer import ToolExecutionObserver
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.mock_tools import (
    GET_INVENTORY_DEFINITION as MOCK_INVENTORY_DEFINITION,
    register_mock_tools,
)
from backend.app.tools.registry import ToolRegistry

from tests.test_tool_chat_service import (  # noqa: E402
    ScriptedLLMClient,
    _tool_call_llm_response,
)

_SECRETS = (
    "postgresql://", "wms_user", "s3cret-pw", "db.internal",
    "DATABASE_URL", "Authorization", "Bearer ", "sk-", "password",
    "Traceback", "SELECT",
)

_STARTED = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


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
) -> ToolExecutionRecord:
    return ToolExecutionRecord(
        request_id=request_id,
        round=round,
        tool_name=tool_name,
        started_at=_STARTED,
        finished_at=_STARTED + timedelta(milliseconds=5),
        duration_ms=5.0,
        success=success,
        project_id=project_id,
        tool_call_id=tool_call_id,
        error_code=None,
        error_type=None if success else "ToolValidationError",
    )


def _context(
    *, request_id: str = "req-1", round: int = 1,
    project_id: str | None = None, tool_call_id: str | None = "call_001",
) -> ToolExecutionContext:
    return ToolExecutionContext(
        request_id=request_id,
        round=round,
        project_id=project_id,
        tool_call_id=tool_call_id,
    )


def _mock_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_mock_tools(registry)
    return registry


def _blob(record: ToolExecutionRecord) -> str:
    return json.dumps(dataclasses.asdict(record), default=str)


# ============================================================
# 初始化 / 单条 / 多条 / 顺序
# ============================================================

class TestCollectAndQuery:
    def test_empty_collector(self) -> None:
        collector = InMemoryToolExecutionCollector()
        assert collector.records() == ()
        assert isinstance(collector.records(), tuple)

    def test_single_record(self) -> None:
        collector = InMemoryToolExecutionCollector()
        record = _record()

        collector.on_execution(record)

        assert collector.records() == (record,)

    def test_multiple_records_insertion_order(self) -> None:
        collector = InMemoryToolExecutionCollector()
        first, second, third = (
            _record(request_id="req-A"),
            _record(request_id="req-B", round=1),
            _record(request_id="req-A", round=2),
        )

        for record in (first, second, third):
            collector.on_execution(record)

        assert collector.records() == (first, second, third)

    def test_no_sorting_or_deduplication(self) -> None:
        """相同对象重复插入 → 原样保留（不去重 / 不排序）。"""
        collector = InMemoryToolExecutionCollector()
        record = _record(tool_name="get_work_order")

        collector.on_execution(record)
        collector.on_execution(record)

        assert collector.records() == (record, record)

    def test_query_preserves_original_order(self) -> None:
        collector = InMemoryToolExecutionCollector()
        records = [
            _record(request_id="req-A", round=1),
            _record(request_id="req-B"),
            _record(request_id="req-A", round=2),
            _record(request_id="req-A", round=3),
        ]
        for record in records:
            collector.on_execution(record)

        assert collector.records_by_request_id("req-A") == (
            records[0], records[2], records[3],
        )


# ============================================================
# Snapshot / Immutability / Record Identity
# ============================================================

class TestSnapshotSemantics:
    def test_records_returns_tuple(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record())
        assert isinstance(collector.records(), tuple)

    def test_snapshot_cannot_be_mutated(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record())
        snapshot = collector.records()

        with pytest.raises(AttributeError):
            snapshot.append(_record())  # type: ignore[attr-defined]
        assert collector.records() == snapshot

    def test_snapshot_is_new_tuple_each_call(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record())
        assert collector.records() is not collector.records()
        assert collector.records() == collector.records()

    def test_internal_list_not_exposed(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record())
        snapshot = collector.records()
        assert not isinstance(snapshot, list)
        # 调用方对快照的操作不影响内部状态
        assert len(collector.records()) == 1

    def test_record_identity_preserved(self) -> None:
        """Collector 不复制、不重建 Record（同一对象）。"""
        collector = InMemoryToolExecutionCollector()
        record = _record()
        collector.on_execution(record)

        assert collector.records()[0] is record
        assert collector.records_by_request_id("req-1")[0] is record

    def test_record_values_unchanged(self) -> None:
        collector = InMemoryToolExecutionCollector()
        record = _record(project_id="project-a", round=4)
        before = dataclasses.asdict(record)

        collector.on_execution(record)

        assert dataclasses.asdict(collector.records()[0]) == before

    def test_query_results_are_tuples(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record(project_id="project-a"))
        for result in (
            collector.records(),
            collector.records_by_request_id("req-1"),
            collector.records_by_project_id("project-a"),
            collector.records_by_tool_name("get_inventory"),
        ):
            assert isinstance(result, tuple)


# ============================================================
# Request / Project / Tool 查询
# ============================================================

class TestRequestQuery:
    def test_match(self) -> None:
        collector = InMemoryToolExecutionCollector()
        record = _record(request_id="req-A")
        collector.on_execution(record)
        assert collector.records_by_request_id("req-A") == (record,)

    def test_no_match_returns_empty_tuple(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record(request_id="req-A"))
        assert collector.records_by_request_id("req-Z") == ()

    def test_multiple_requests_isolated(self) -> None:
        collector = InMemoryToolExecutionCollector()
        a1, b1, a2 = (
            _record(request_id="req-A", round=1),
            _record(request_id="req-B", round=1),
            _record(request_id="req-A", round=2),
        )
        for record in (a1, b1, a2):
            collector.on_execution(record)

        assert collector.records_by_request_id("req-A") == (a1, a2)
        assert collector.records_by_request_id("req-B") == (b1,)

    @pytest.mark.parametrize("bad", ["", "   ", None, 5])
    def test_invalid_request_id_rejected(self, bad: Any) -> None:
        collector = InMemoryToolExecutionCollector()
        with pytest.raises((TypeError, ValueError)):
            collector.records_by_request_id(bad)


class TestProjectQuery:
    def test_project_a(self) -> None:
        collector = InMemoryToolExecutionCollector()
        a, b = _record(project_id="project-a"), _record(project_id="project-b")
        collector.on_execution(a)
        collector.on_execution(b)
        assert collector.records_by_project_id("project-a") == (a,)

    def test_project_b(self) -> None:
        collector = InMemoryToolExecutionCollector()
        a, b = _record(project_id="project-a"), _record(project_id="project-b")
        collector.on_execution(a)
        collector.on_execution(b)
        assert collector.records_by_project_id("project-b") == (b,)

    def test_none_matches_only_none(self) -> None:
        """None 只匹配 project_id is None（不等于"全部"）。"""
        collector = InMemoryToolExecutionCollector()
        none_record = _record(project_id=None, round=1)
        scoped = _record(project_id="project-a", round=2)
        collector.on_execution(none_record)
        collector.on_execution(scoped)

        assert collector.records_by_project_id(None) == (none_record,)
        assert len(collector.records()) == 2

    def test_no_match_returns_empty_tuple(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record(project_id="project-a"))
        assert collector.records_by_project_id("project-z") == ()

    @pytest.mark.parametrize("bad", ["", "   ", 5])
    def test_invalid_project_id_rejected(self, bad: Any) -> None:
        collector = InMemoryToolExecutionCollector()
        with pytest.raises((TypeError, ValueError)):
            collector.records_by_project_id(bad)


class TestToolQuery:
    def test_get_inventory(self) -> None:
        collector = InMemoryToolExecutionCollector()
        inv = _record(tool_name="get_inventory")
        wo = _record(tool_name="get_work_order")
        collector.on_execution(inv)
        collector.on_execution(wo)
        assert collector.records_by_tool_name("get_inventory") == (inv,)

    def test_get_work_order(self) -> None:
        collector = InMemoryToolExecutionCollector()
        inv = _record(tool_name="get_inventory")
        wo = _record(tool_name="get_work_order")
        collector.on_execution(inv)
        collector.on_execution(wo)
        assert collector.records_by_tool_name("get_work_order") == (wo,)

    def test_unknown_tool_returns_empty_tuple(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record(tool_name="get_inventory"))
        assert collector.records_by_tool_name("ghost_tool") == ()

    @pytest.mark.parametrize("query", ["GET_INVENTORY", "get", "inventory"])
    def test_exact_match_only(self, query: str) -> None:
        """无大小写折叠 / 无前缀 / 无别名 / 无模糊匹配。"""
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record(tool_name="get_inventory"))
        assert collector.records_by_tool_name(query) == ()

    @pytest.mark.parametrize("bad", ["", "   ", None, 7])
    def test_invalid_tool_name_rejected(self, bad: Any) -> None:
        collector = InMemoryToolExecutionCollector()
        with pytest.raises((TypeError, ValueError)):
            collector.records_by_tool_name(bad)


# ============================================================
# Clear / 生命周期
# ============================================================

class TestClear:
    def test_clear_empties(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record())
        collector.on_execution(_record(round=2))

        collector.clear()

        assert collector.records() == ()

    def test_clear_on_empty_is_noop(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.clear()
        assert collector.records() == ()

    def test_collect_again_after_clear(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record(round=1))
        collector.clear()
        second = _record(round=2)

        collector.on_execution(second)

        assert collector.records() == (second,)

    def test_queries_empty_after_clear(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record(project_id="project-a"))
        collector.clear()

        assert collector.records_by_request_id("req-1") == ()
        assert collector.records_by_project_id("project-a") == ()
        assert collector.records_by_tool_name("get_inventory") == ()


# ============================================================
# Isolation（实例独立 / 无单例）
# ============================================================

class TestIsolation:
    def test_two_collectors_are_independent(self) -> None:
        collector_a = InMemoryToolExecutionCollector()
        collector_b = InMemoryToolExecutionCollector()
        record = _record()

        collector_a.on_execution(record)

        assert collector_a.records() == (record,)
        assert collector_b.records() == ()

    def test_clear_does_not_affect_other_collector(self) -> None:
        collector_a = InMemoryToolExecutionCollector()
        collector_b = InMemoryToolExecutionCollector()
        record = _record()
        collector_a.on_execution(record)
        collector_b.on_execution(record)

        collector_a.clear()

        assert collector_a.records() == ()
        assert collector_b.records() == (record,)

    def test_no_module_level_singleton(self) -> None:
        import ast
        import inspect

        from backend.app.services import (
            in_memory_tool_execution_collector as module,
        )

        tree = ast.parse(inspect.getsource(module))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                assert not isinstance(
                    node.value, ast.Call
                ), "模块级不得实例化 Collector（禁止隐藏全局状态）"
            if isinstance(node, ast.FunctionDef):
                assert not node.name.startswith(
                    ("get_default", "default_")
                ), node.name

    def test_instances_are_distinct(self) -> None:
        assert (
            InMemoryToolExecutionCollector()
            is not InMemoryToolExecutionCollector()
        )


# ============================================================
# Security / Determinism / 统计禁用
# ============================================================

class TestSecurityAndDeterminism:
    def test_only_records_are_stored(self) -> None:
        collector = InMemoryToolExecutionCollector()
        with pytest.raises(TypeError):
            collector.on_execution({"request_id": "req-1"})  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            collector.on_execution(object())  # type: ignore[arg-type]
        assert collector.records() == ()

    def test_stored_records_have_no_sensitive_data(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(_record())
        blob = _blob(collector.records()[0])
        for forbidden in (
            "arguments", "material_code", "SELECT", "postgresql://",
            "password", "Api-Key", "traceback",
        ):
            assert forbidden not in blob, forbidden

    def test_query_results_have_no_sensitive_data(self) -> None:
        collector = InMemoryToolExecutionCollector()
        collector.on_execution(
            _record(project_id="project-a", tool_name="get_inventory")
        )
        blob = json.dumps(
            [dataclasses.asdict(r) for r in collector.records_by_project_id("project-a")],
            default=str,
        )
        for secret in _SECRETS:
            assert secret not in blob, secret

    def test_determinism_same_order_same_results(self) -> None:
        def build() -> InMemoryToolExecutionCollector:
            collector = InMemoryToolExecutionCollector()
            for index in range(3):
                collector.on_execution(
                    _record(request_id=f"req-{index}", project_id="project-a")
                )
            return collector

        first, second = build(), build()
        assert first.records() == second.records()
        assert first.records_by_project_id("project-a") == (
            second.records_by_project_id("project-a")
        )

    def test_no_aggregation_api(self) -> None:
        """无 count / success_rate / 分位数等统计（属 Metrics，后续阶段）。

        Phase 3.11 Step 20：新增只读 ``max_records``（retention 窗口参数），
        仍不含任何统计 API。
        """
        public = {
            name
            for name in dir(InMemoryToolExecutionCollector)
            if not name.startswith("_")
        }
        assert public == {
            "clear",
            "max_records",
            "on_execution",
            "records",
            "records_by_project_id",
            "records_by_request_id",
            "records_by_tool_name",
        }, public

    def test_collector_has_no_execution_capability(self) -> None:
        collector = InMemoryToolExecutionCollector()
        for forbidden in ("execute", "run_tool", "registry", "handler"):
            assert not hasattr(collector, forbidden), forbidden

    def test_module_imports_are_pure(self) -> None:
        import ast
        import inspect

        from backend.app.services import (
            in_memory_tool_execution_collector as module,
        )

        tree = ast.parse(inspect.getsource(module))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert imported <= {
            "__future__", "threading", "collections", "collections.abc",
            "backend.app.services.tool_execution_record",
        }, imported
        for forbidden in (
            "sqlalchemy", "backend.app.db", "backend.app.api",
            "backend.app.llm", "backend.app.tools",
            "kafka", "redis", "celery", "opentelemetry", "httpx",
        ):
            assert not any(n.startswith(forbidden) for n in imported), forbidden


# ============================================================
# 线程安全 smoke（单锁；与项目 In-Memory 组件一致）
# ============================================================

class TestThreadSafety:
    def test_concurrent_appends_do_not_lose_records(self) -> None:
        collector = InMemoryToolExecutionCollector()
        total = 200

        def worker(worker_id: int) -> None:
            for index in range(50):
                collector.on_execution(
                    _record(request_id=f"req-{worker_id}", round=index + 1)
                )

        threads = [
            threading.Thread(target=worker, args=(wid,)) for wid in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        records = collector.records()
        assert len(records) == total
        # 每个 worker 的 request_id 都在，且各自 round 完整
        for worker_id in range(4):
            rounds = sorted(
                r.round
                for r in collector.records_by_request_id(f"req-{worker_id}")
            )
            assert rounds == list(range(1, 51))


# ============================================================
# 集成：ToolExecutionService / ToolChatService（既有注入点，无生产改动）
# ============================================================

class TestIntegrationWithBoundary:
    @staticmethod
    def _registry_with(handler: Any) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(MOCK_INVENTORY_DEFINITION, handler)
        return registry

    async def test_concrete_class_satisfies_observer_protocol(self) -> None:
        assert isinstance(
            InMemoryToolExecutionCollector(), ToolExecutionObserver
        )

    async def test_success_execution_is_collected(self) -> None:
        class _Handler:
            async def __call__(self, arguments: dict[str, Any]) -> dict:
                return {"ok": True}

        collector = InMemoryToolExecutionCollector()
        boundary = ToolExecutionService(
            registry=self._registry_with(_Handler()), observer=collector
        )

        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(request_id="req-A"),
        )

        assert result.success is True
        records = collector.records_by_request_id("req-A")
        assert len(records) == 1
        assert records[0].success is True
        assert records[0].tool_name == "get_inventory"

    async def test_failed_execution_is_collected(self) -> None:
        collector = InMemoryToolExecutionCollector()
        # 空 registry → unknown tool（ToolResult(False)）仍产生 Record
        boundary = ToolExecutionService(
            registry=ToolRegistry(), observer=collector
        )

        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(request_id="req-B"),
        )

        assert result.success is False
        records = collector.records_by_request_id("req-B")
        assert len(records) == 1
        assert records[0].success is False

    async def test_no_context_produces_no_collection(self) -> None:
        collector = InMemoryToolExecutionCollector()
        boundary = ToolExecutionService(
            registry=_mock_registry(), observer=collector
        )

        await boundary.execute("get_inventory", arguments={"material_code": "M1"})

        assert collector.records() == ()

    async def test_collector_failure_does_not_affect_result(
        self, monkeypatch
    ) -> None:
        """Collector 内部异常 → 由 Step 15 隔离；ToolResult 不变、Tool 1 次。"""
        class _Handler:
            def __init__(self) -> None:
                self.calls = 0

            async def __call__(self, arguments: dict[str, Any]) -> dict:
                self.calls += 1
                return {"ok": True}

        handler = _Handler()
        collector = InMemoryToolExecutionCollector()
        boundary = ToolExecutionService(
            registry=self._registry_with(handler), observer=collector
        )

        def _boom(record: ToolExecutionRecord) -> None:
            raise RuntimeError("collector failure")

        monkeypatch.setattr(collector, "on_execution", _boom)

        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(),
        )

        assert result.success is True
        assert handler.calls == 1

    async def test_multi_step_records_rounds_and_request_id(self) -> None:
        collector = InMemoryToolExecutionCollector()
        registry = _mock_registry()
        boundary = ToolExecutionService(
            registry=registry, observer=collector, project_id="project-a"
        )
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "M1"}, call_id="call_001"
            ),
            _tool_call_llm_response(
                "get_inventory", {"material_code": "M2"}, call_id="call_002"
            ),
            _tool_call_llm_response(
                "get_inventory", {"material_code": "M3"}, call_id="call_003"
            ),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=boundary)

        await service.chat("三步查询", registry=registry)

        records = collector.records()
        assert [r.round for r in records] == [1, 2, 3]
        assert [r.tool_call_id for r in records] == [
            "call_001", "call_002", "call_003",
        ]
        assert len({r.request_id for r in records}) == 1
        assert collector.records_by_project_id("project-a") == records

    async def test_two_chats_have_different_request_ids(self) -> None:
        collector = InMemoryToolExecutionCollector()
        registry = _mock_registry()
        boundary = ToolExecutionService(registry=registry, observer=collector)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {}, call_id="c1"),
            "answer-1",
            _tool_call_llm_response("get_inventory", {}, call_id="c2"),
            "answer-2",
        ])
        service = ToolChatService(llm_client=llm, execution_service=boundary)

        await service.chat("q1", registry=registry)
        await service.chat("q2", registry=registry)

        first, second = collector.records()
        assert first.request_id != second.request_id
        assert len(collector.records_by_request_id(first.request_id)) == 1
        assert len(collector.records_by_request_id(second.request_id)) == 1

    async def test_boundary_rejects_non_record_observer(self) -> None:
        """Collector 不是 observer 形状 → 边界构造拒绝（Step 15 校验）。"""
        with pytest.raises(TypeError):
            ToolExecutionService(
                registry=_mock_registry(), observer="not-an-observer"  # type: ignore[arg-type]
            )


# ============================================================
# Phase 3.11 Step 20：Retention / Capacity（max_records）
# ============================================================

def _retention_collector(
    max_records: int, count: int
) -> tuple[InMemoryToolExecutionCollector, list[ToolExecutionRecord]]:
    """构造有界 Collector 并写入 ``count`` 条（request_id = req-0…）。"""
    collector = InMemoryToolExecutionCollector(max_records=max_records)
    records = [
        _record(
            request_id=f"req-{index}",
            tool_name=(
                "get_inventory" if index % 2 == 0 else "get_work_order"
            ),
            project_id="project-a" if index % 3 == 0 else None,
            success=index % 2 == 0,
        )
        for index in range(count)
    ]
    for record in records:
        collector.on_execution(record)
    return collector, records


class TestRetentionConfiguration:
    def test_default_max_records(self) -> None:
        from backend.app.services.in_memory_tool_execution_collector import (
            DEFAULT_MAX_RECORDS,
        )

        collector = InMemoryToolExecutionCollector()
        assert collector.max_records == DEFAULT_MAX_RECORDS == 1000

    def test_explicit_max_records(self) -> None:
        assert InMemoryToolExecutionCollector(max_records=7).max_records == 7

    def test_minimal_window_of_one(self) -> None:
        collector = InMemoryToolExecutionCollector(max_records=1)
        first, second = _record(request_id="req-1"), _record(
            request_id="req-2"
        )

        collector.on_execution(first)
        assert collector.records() == (first,)

        collector.on_execution(second)
        assert collector.records() == (second,)      # 只保留最新 1 条

    @pytest.mark.parametrize("bad", [True, False, 1.5, 1.0, "100", None])
    def test_non_int_rejected_with_type_error(self, bad: Any) -> None:
        with pytest.raises(TypeError):
            InMemoryToolExecutionCollector(max_records=bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_non_positive_rejected_with_value_error(self, bad: int) -> None:
        with pytest.raises(ValueError):
            InMemoryToolExecutionCollector(max_records=bad)

    def test_max_records_is_read_only_property(self) -> None:
        collector = InMemoryToolExecutionCollector(max_records=5)
        assert collector.max_records == 5
        with pytest.raises(AttributeError):
            collector.max_records = 10  # type: ignore[misc]


class TestFifoRetention:
    def test_under_capacity_keeps_all(self) -> None:
        collector, records = _retention_collector(max_records=5, count=3)
        assert collector.records() == tuple(records)

    def test_exactly_capacity_keeps_all(self) -> None:
        collector, records = _retention_collector(max_records=3, count=3)
        assert collector.records() == tuple(records)
        assert len(collector.records()) == 3

    def test_one_over_capacity_evicts_oldest(self) -> None:
        collector, records = _retention_collector(max_records=3, count=3)
        fourth = _record(request_id="req-new")

        collector.on_execution(fourth)

        assert collector.records() == (*records[1:], fourth)

    def test_multiple_evictions_keep_newest_n(self) -> None:
        collector, records = _retention_collector(max_records=3, count=6)
        assert collector.records() == tuple(records[-3:])

    def test_oldest_evicted_identity(self) -> None:
        collector, records = _retention_collector(max_records=2, count=3)
        retained = collector.records()
        assert records[0] not in retained            # A 已淘汰
        assert retained == (records[1], records[2])

    def test_retention_is_fifo_not_lru(self) -> None:
        """读取（查询）不刷新顺序：FIFO 只按写入顺序淘汰。"""
        collector, records = _retention_collector(max_records=3, count=3)
        for _ in range(5):                            # 反复读取最旧记录
            collector.records()
            collector.records_by_request_id("req-0")

        fourth = _record(request_id="req-new")
        collector.on_execution(fourth)

        assert collector.records() == (*records[1:], fourth)


class TestRetentionQueryApis:
    def test_records_respects_retention(self) -> None:
        collector, records = _retention_collector(max_records=3, count=5)
        assert collector.records() == tuple(records[-3:])

    def test_request_query_respects_retention(self) -> None:
        collector, _records = _retention_collector(max_records=3, count=5)

        assert collector.records_by_request_id("req-0") == ()   # 已淘汰
        assert collector.records_by_request_id("req-1") == ()   # 已淘汰
        assert len(collector.records_by_request_id("req-4")) == 1

    def test_project_query_respects_retention(self) -> None:
        collector, _records = _retention_collector(max_records=2, count=4)
        # 保留 index 2（project_id=None）/ index 3（project-a）
        assert len(collector.records_by_project_id("project-a")) == 1
        assert len(collector.records_by_project_id(None)) == 1
        # 已淘汰者（index 0 → project-a）不再出现
        assert collector.records_by_request_id("req-0") == ()

    def test_tool_query_respects_retention(self) -> None:
        collector, _records = _retention_collector(max_records=2, count=4)
        # 保留 index 2（get_inventory）/ 3（get_work_order）
        assert len(collector.records_by_tool_name("get_inventory")) == 1
        assert len(collector.records_by_tool_name("get_work_order")) == 1
        assert collector.records_by_tool_name("unknown_tool") == ()

    def test_evicted_record_not_recoverable_from_any_api(self) -> None:
        collector, records = _retention_collector(max_records=2, count=3)
        evicted = records[0]

        assert evicted not in collector.records()
        assert evicted not in collector.records_by_request_id(
            evicted.request_id
        )
        assert evicted not in collector.records_by_project_id(
            evicted.project_id
        )
        assert evicted not in collector.records_by_tool_name(evicted.tool_name)


class TestRetentionMetrics:
    def test_metrics_only_counts_retained_records(self) -> None:
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )

        collector = InMemoryToolExecutionCollector(max_records=3)
        # A success / B success / C failure / D success
        collector.on_execution(_record(request_id="req-A", success=True))
        collector.on_execution(_record(request_id="req-B", success=True))
        collector.on_execution(_record(request_id="req-C", success=False))
        collector.on_execution(_record(request_id="req-D", success=True))

        snapshot = ToolExecutionMetricsService.snapshot(collector.records())

        assert snapshot.total_count == 3
        assert snapshot.success_count == 2
        assert snapshot.failure_count == 1

    def test_evicted_record_absent_from_metrics(self) -> None:
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )

        collector = InMemoryToolExecutionCollector(max_records=1)
        collector.on_execution(_record(request_id="req-old", success=False))
        collector.on_execution(_record(request_id="req-new", success=True))

        snapshot = ToolExecutionMetricsService.snapshot(collector.records())

        assert snapshot.total_count == 1        # evicted 的 failure 不可见
        assert snapshot.success_count == 1
        assert snapshot.failure_count == 0
        assert snapshot.failure_rate == 0.0

    def test_metrics_after_clear_is_empty_semantics(self) -> None:
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )

        collector = InMemoryToolExecutionCollector(max_records=2)
        collector.on_execution(_record())
        collector.clear()

        snapshot = ToolExecutionMetricsService.snapshot(collector.records())

        assert snapshot.total_count == 0
        assert snapshot.success_rate is None
        assert snapshot.average_duration_ms is None
        assert snapshot.max_duration_ms is None


class TestRetentionClear:
    def test_clear_empties_window(self) -> None:
        collector, _records = _retention_collector(max_records=3, count=3)
        collector.clear()
        assert collector.records() == ()

    def test_append_after_clear_respects_capacity(self) -> None:
        collector = InMemoryToolExecutionCollector(max_records=2)
        collector.on_execution(_record(request_id="req-0"))
        collector.clear()

        fresh = [
            _record(request_id=f"req-{index}") for index in range(3)
        ]
        for record in fresh:
            collector.on_execution(record)

        assert collector.records() == tuple(fresh[-2:])
        assert collector.max_records == 2


class TestRetentionThreadSafety:
    def test_concurrent_append_final_count_within_capacity(self) -> None:
        collector = InMemoryToolExecutionCollector(max_records=10)

        def worker(worker_id: int) -> None:
            for index in range(50):
                collector.on_execution(
                    _record(
                        request_id=f"req-{worker_id}",
                        round=index + 1,
                    )
                )

        threads = [
            threading.Thread(target=worker, args=(wid,)) for wid in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        records = collector.records()
        assert len(records) == 10                 # 恰好窗口大小
        assert len(records) <= collector.max_records

    def test_concurrent_append_no_duplicate_or_corrupt_records(self) -> None:
        collector = InMemoryToolExecutionCollector(max_records=25)

        def worker(worker_id: int) -> None:
            for index in range(40):
                collector.on_execution(
                    _record(request_id=f"req-{worker_id}", round=index + 1)
                )

        threads = [
            threading.Thread(target=worker, args=(wid,)) for wid in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        snapshot = collector.records()
        assert len({id(record) for record in snapshot}) == len(snapshot)
        for record in snapshot:
            assert isinstance(record, ToolExecutionRecord)
            record.assert_field_whitelist()
            assert record.round >= 1

    def test_snapshot_never_exceeds_capacity(self) -> None:
        collector = InMemoryToolExecutionCollector(max_records=5)
        stop = threading.Event()
        observed: list[int] = []

        def writer() -> None:
            index = 0
            while not stop.is_set():
                index += 1
                collector.on_execution(
                    _record(request_id=f"req-{index}", round=index)
                )

        def reader() -> None:
            for _ in range(300):
                observed.append(len(collector.records()))

        threads = [threading.Thread(target=writer)] + [
            threading.Thread(target=reader) for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads[1:]:
            thread.join()
        stop.set()
        threads[0].join()

        assert observed and max(observed) <= 5


class TestRetentionImmutability:
    def test_records_returns_tuple_under_retention(self) -> None:
        collector, _records = _retention_collector(max_records=2, count=4)
        snapshot = collector.records()
        assert isinstance(snapshot, tuple)
        with pytest.raises(AttributeError):
            snapshot.append(object())  # type: ignore[attr-defined]

    def test_snapshot_stable_after_eviction(self) -> None:
        collector = InMemoryToolExecutionCollector(max_records=2)
        first, second = _record(request_id="req-1"), _record(
            request_id="req-2"
        )
        collector.on_execution(first)
        collector.on_execution(second)

        snapshot = collector.records()
        collector.on_execution(_record(request_id="req-3"))

        assert snapshot == (first, second)        # 旧快照不受淘汰影响
        assert len(collector.records()) == 2

    def test_record_not_modified_by_retention(self) -> None:
        collector = InMemoryToolExecutionCollector(max_records=1)
        record = _record(request_id="req-keep", project_id="project-a")
        before = dataclasses.asdict(record)

        collector.on_execution(record)
        collector.on_execution(_record(request_id="req-evicts"))  # 淘汰旧者
        collector.on_execution(record)                            # 重新写入

        assert dataclasses.asdict(collector.records()[0]) == before
        assert collector.records()[0] is record


class TestRetentionSecurity:
    def test_no_ttl_or_background_identifiers(self) -> None:
        import ast
        import inspect

        from backend.app.services import (
            in_memory_tool_execution_collector as module,
        )

        tree = ast.parse(inspect.getsource(module))
        identifiers = {
            node.id.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        } | {
            node.attr.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "ttl", "timer", "sleep", "daemon", "schedule", "monotonic",
            "create_task", "thread",
        ):
            assert forbidden not in identifiers, forbidden
        assert "threading.Thread" not in inspect.getsource(module)

    def test_collector_holds_no_external_capability(self) -> None:
        collector = InMemoryToolExecutionCollector(max_records=3)
        assert set(vars(collector)) == {"_max_records", "_records", "_lock"}
        for forbidden in (
            "engine", "session", "connect", "execute", "registry",
            "handler", "llm", "client",
        ):
            assert not hasattr(collector, forbidden), forbidden

    def test_no_persistence_imports(self) -> None:
        import ast
        import inspect

        from backend.app.services import (
            in_memory_tool_execution_collector as module,
        )

        tree = ast.parse(inspect.getsource(module))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for forbidden in (
            "json", "pathlib", "os", "sqlalchemy", "redis", "kafka",
            "celery", "pickle", "sqlite3",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imported
            ), forbidden
