"""ToolExecutionPersistenceAdapter / CompositeObserver 单元测试（Phase 3.11 Step 28）。

非 DB：Fake Service / Fake Repository（0 网络 / 0 DB / 0 LLM）。

覆盖：

    Adapter        Observer 协议实现 / 委托 Service / 失败**不传播** /
                   warning 日志（白名单字段，无 traceback）/
                   不 retry / 不 sleep / 不改写 Record
    Composite      fan-out（Case A~D）/ 子 observer 失败不阻断后续 /
                   协议校验 / 空 composite
    Integration   真实 Collector + 真实 Adapter（Persistence 失败）→
                   Memory 仍有记录
    Runtime       ToolExecutionService → observer fan-out →
                   ToolResult 不变（observer 失败 ≠ Tool 失败）
"""
from __future__ import annotations

import ast
import logging
import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.in_memory_tool_execution_collector import (
    InMemoryToolExecutionCollector,
)
from backend.app.services.tool_execution_observer import ToolExecutionObserver
from backend.app.services.tool_execution_persistence_adapter import (
    CompositeToolExecutionObserver,
    ToolExecutionPersistenceAdapter,
)
from backend.app.services.tool_execution_persistence_service import (
    ToolExecutionPersistenceService,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION
from backend.app.tools.registry import ToolRegistry

_MODULE = "backend/app/services/tool_execution_persistence_adapter.py"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STARTED = datetime(2026, 9, 26, 10, 20, 30, tzinfo=timezone.utc)

#: 日志中禁止出现的文本（§十一 / §二十六）
_FORBIDDEN_LOG_TEXT = (
    "arguments", "MAT-", "SELECT", "postgresql://", "password", "api_key",
    "Authorization", "Bearer ", "Traceback", "sqlalchemy", "psycopg",
)


def _record(**overrides: object) -> ToolExecutionRecord:
    fields: dict[str, object] = {
        "request_id": "req-adapter-1",
        "round": 1,
        "tool_name": "get_inventory",
        "started_at": _STARTED,
        "finished_at": _STARTED + timedelta(milliseconds=2.0),
        "duration_ms": 2.0,
        "success": True,
        "project_id": "project-a",
        "tool_call_id": None,
    }
    fields.update(overrides)
    return ToolExecutionRecord(**fields)  # type: ignore[arg-type]


class FakeService:
    """记录调用 + 可注入异常（duck-typed；无需 DB）。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[ToolExecutionRecord] = []
        self._error = error

    def persist(self, record: ToolExecutionRecord) -> object:
        self.calls.append(record)
        if self._error is not None:
            raise self._error
        return object()


class _ExplodingObserver:
    def __init__(self) -> None:
        self.calls = 0

    def on_execution(self, record: ToolExecutionRecord) -> None:
        self.calls += 1
        raise RuntimeError("observer boom")


class _RecordingObserver:
    def __init__(self) -> None:
        self.records: list[ToolExecutionRecord] = []

    def on_execution(self, record: ToolExecutionRecord) -> None:
        self.records.append(record)


# ============================================================
# Adapter
# ============================================================

class TestPersistenceAdapter:
    def test_implements_observer_protocol(self) -> None:
        adapter = ToolExecutionPersistenceAdapter(
            persistence_service=FakeService()  # type: ignore[arg-type]
        )

        assert isinstance(adapter, ToolExecutionObserver)
        assert callable(adapter.on_execution)

    def test_delegates_record_to_service(self) -> None:
        service = FakeService()
        adapter = ToolExecutionPersistenceAdapter(
            persistence_service=service  # type: ignore[arg-type]
        )
        record = _record()

        adapter.on_execution(record)

        assert service.calls == [record]
        assert service.calls[0] is record

    def test_failure_is_not_propagated(self) -> None:
        """持久化失败绝不穿透（Observer 契约：失败隔离）。"""
        adapter = ToolExecutionPersistenceAdapter(
            persistence_service=FakeService(  # type: ignore[arg-type]
                error=RuntimeError("db down")
            )
        )

        adapter.on_execution(_record())          # 不抛异常

    def test_failure_logs_warning_with_whitelisted_fields(self, caplog) -> None:
        adapter = ToolExecutionPersistenceAdapter(
            persistence_service=FakeService(  # type: ignore[arg-type]
                error=RuntimeError("boom")
            )
        )

        with caplog.at_level(logging.WARNING):
            adapter.on_execution(_record())

        warning_records = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert len(warning_records) == 1
        record = warning_records[0]
        assert record.tool_name == "get_inventory"
        assert record.request_id == "req-adapter-1"
        assert record.round == 1
        assert record.project_id == "project-a"
        assert record.error_type == "RuntimeError"
        assert record.exc_info is None               # 无 traceback

    def test_failure_log_has_no_sensitive_text(self, caplog) -> None:
        adapter = ToolExecutionPersistenceAdapter(
            persistence_service=FakeService(  # type: ignore[arg-type]
                error=RuntimeError(
                    "SELECT * FROM inventory WHERE password='x' "
                    "postgresql://user:pw@host/db"
                )
            )
        )

        with caplog.at_level(logging.WARNING):
            adapter.on_execution(_record())

        text = caplog.text
        for forbidden in _FORBIDDEN_LOG_TEXT:
            assert forbidden not in text, forbidden

    def test_failure_does_not_mutate_record(self) -> None:
        adapter = ToolExecutionPersistenceAdapter(
            persistence_service=FakeService(  # type: ignore[arg-type]
                error=RuntimeError("boom")
            )
        )
        record = _record()
        before = repr(record)

        adapter.on_execution(record)

        assert repr(record) == before

    def test_no_retry_or_sleep(self) -> None:
        service = FakeService(error=RuntimeError("boom"))
        adapter = ToolExecutionPersistenceAdapter(
            persistence_service=service  # type: ignore[arg-type]
        )

        adapter.on_execution(_record())

        assert len(service.calls) == 1               # 恰好一次（无 retry）

    def test_default_service_is_persistence_service(self) -> None:
        adapter = ToolExecutionPersistenceAdapter()

        assert isinstance(
            adapter.persistence_service, ToolExecutionPersistenceService
        )

    def test_public_api_is_observer_shaped(self) -> None:
        public = {
            name
            for name in dir(ToolExecutionPersistenceAdapter)
            if not name.startswith("_")
        }
        assert public == {"on_execution", "persistence_service"}, public


# ============================================================
# Composite Observer（fan-out）
# ============================================================

class TestCompositeObserver:
    def test_case_a_both_succeed(self) -> None:
        first = _RecordingObserver()
        second = _RecordingObserver()
        composite = CompositeToolExecutionObserver(first, second)
        record = _record()

        composite.on_execution(record)

        assert first.records == [record]
        assert second.records == [record]

    def test_case_b_first_succeeds_second_fails(self, caplog) -> None:
        memory = _RecordingObserver()
        failing = _ExplodingObserver()
        composite = CompositeToolExecutionObserver(memory, failing)
        record = _record()

        with caplog.at_level(logging.WARNING):
            composite.on_execution(record)

        assert memory.records == [record]            # Memory 记录存在
        assert failing.calls == 1
        assert caplog.records                      # 失败被记录

    def test_case_c_first_fails_second_still_receives(self, caplog) -> None:
        failing = _ExplodingObserver()
        memory = _RecordingObserver()
        composite = CompositeToolExecutionObserver(failing, memory)
        record = _record()

        with caplog.at_level(logging.WARNING):
            composite.on_execution(record)

        assert failing.calls == 1
        assert memory.records == [record]            # 后续 observer 仍被调用

    def test_case_d_both_fail_does_not_raise(self, caplog) -> None:
        first = _ExplodingObserver()
        second = _ExplodingObserver()
        composite = CompositeToolExecutionObserver(first, second)

        with caplog.at_level(logging.WARNING):
            composite.on_execution(_record())        # 不抛异常

        assert first.calls == 1
        assert second.calls == 1
        assert len(caplog.records) == 2

    def test_composite_is_observer_and_lists_children(self) -> None:
        memory = _RecordingObserver()
        other = _RecordingObserver()
        composite = CompositeToolExecutionObserver(memory, other)

        assert isinstance(composite, ToolExecutionObserver)
        assert composite.observers == (memory, other)

    def test_empty_composite_is_noop(self) -> None:
        composite = CompositeToolExecutionObserver()

        composite.on_execution(_record())            # 不抛异常

        assert composite.observers == ()

    def test_invalid_child_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            CompositeToolExecutionObserver(object())  # type: ignore[arg-type]

    def test_real_collector_plus_adapter_fan_out(self) -> None:
        """Case A（真实 Collector + 真实 Adapter，Fake Service）。"""
        collector = InMemoryToolExecutionCollector()
        service = FakeService()
        adapter = ToolExecutionPersistenceAdapter(
            persistence_service=service  # type: ignore[arg-type]
        )
        composite = CompositeToolExecutionObserver(collector, adapter)
        record = _record()

        composite.on_execution(record)

        assert collector.records() == (record,)
        assert service.calls == [record]

    def test_memory_success_persistence_failure_keeps_memory_record(self) -> None:
        """Case B：Persistence 失败 → Memory 记录仍在，且不抛异常。"""
        collector = InMemoryToolExecutionCollector()
        adapter = ToolExecutionPersistenceAdapter(
            persistence_service=FakeService(  # type: ignore[arg-type]
                error=RuntimeError("db down")
            )
        )
        composite = CompositeToolExecutionObserver(collector, adapter)
        record = _record()

        composite.on_execution(record)

        assert collector.records() == (record,)

    def test_memory_failure_persistence_still_receives(self) -> None:
        """Case C：Memory 失败 → Persistence 仍收到记录。"""
        service = FakeService()
        composite = CompositeToolExecutionObserver(
            _ExplodingObserver(),
            ToolExecutionPersistenceAdapter(
                persistence_service=service  # type: ignore[arg-type]
            ),
        )
        record = _record()

        composite.on_execution(record)

        assert service.calls == [record]


# ============================================================
# 运行时契约：observer 失败 ≠ Tool 失败（Step 15 隔离保持）
# ============================================================

class TestRuntimeIsolation:
    @staticmethod
    def _registry():
        calls: list[dict[str, object]] = []

        async def handler(arguments: dict[str, object]) -> dict[str, object]:
            calls.append(arguments)
            return {"qty": 250.0}

        registry = ToolRegistry()
        registry.register(GET_INVENTORY_DEFINITION, handler)
        return registry, calls

    async def test_tool_result_unchanged_when_persistence_fails(self) -> None:
        registry, calls = self._registry()
        composite = CompositeToolExecutionObserver(
            InMemoryToolExecutionCollector(),
            ToolExecutionPersistenceAdapter(
                persistence_service=FakeService(  # type: ignore[arg-type]
                    error=RuntimeError("db down")
                )
            ),
        )
        boundary = ToolExecutionService(
            registry=registry, observer=composite
        )
        from backend.app.services.tool_execution_context import (
            ToolExecutionContext,
        )

        result = await boundary.execute(
            "get_inventory",
            arguments={"material_code": "MAT-001"},
            context=ToolExecutionContext(
                request_id="req-runtime", round=1, project_id=None
            ),
        )

        assert result.success is True                 # ToolResult 不变
        assert result.data == {"qty": 250.0}
        assert calls == [{"material_code": "MAT-001"}]  # Tool 恰好执行一次

    async def test_no_retry_no_second_record_insert(self) -> None:
        registry, calls = self._registry()
        service = FakeService(error=RuntimeError("db down"))
        composite = CompositeToolExecutionObserver(
            InMemoryToolExecutionCollector(),
            ToolExecutionPersistenceAdapter(
                persistence_service=service  # type: ignore[arg-type]
            ),
        )
        boundary = ToolExecutionService(registry=registry, observer=composite)
        from backend.app.services.tool_execution_context import (
            ToolExecutionContext,
        )

        await boundary.execute(
            "get_inventory",
            arguments={"material_code": "MAT-001"},
            context=ToolExecutionContext(
                request_id="req-runtime", round=1, project_id=None
            ),
        )

        assert len(service.calls) == 1                # 无 retry / 无重跑
        assert len(calls) == 1


# ============================================================
# 静态边界（C34.1 / C34.2 / C34.7）
# ============================================================

class TestAdapterStaticBoundaries:
    @staticmethod
    def _tree() -> ast.Module:
        with open(
            os.path.join(_REPO_ROOT, *_MODULE.split("/")), encoding="utf-8"
        ) as handle:
            return ast.parse(handle.read())

    def test_adapter_has_no_db_dependency(self) -> None:
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
            "sqlalchemy", "backend.app.db", "backend.app.db.session",
            "backend.app.db.models.tool_execution_record",
            "backend.app.db.tool_execution_repository",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imported
            ), forbidden

    def test_adapter_has_no_session_or_repository_usage(self) -> None:
        tree = self._tree()
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "session", "engine", "commit", "rollback", "create", "insert",
            "dumps", "sleep", "retry", "to_thread", "create_task",
        ):
            assert forbidden not in identifiers, forbidden

    def test_adapter_does_not_use_logger_exception(self) -> None:
        tree = self._tree()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"exception", "critical"}
            ):
                raise AssertionError("禁止 logger.exception（会输出 traceback）")

    def test_adapter_does_not_depend_on_collector(self) -> None:
        tree = self._tree()
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert not any(
            "in_memory_tool_execution_collector" in name for name in imported
        ), imported
