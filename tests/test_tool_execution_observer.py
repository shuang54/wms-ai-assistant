"""Tool Execution Observer Boundary（Phase 3.11 Step 15）。

锁定「执行 → Record → 可选 Observer」：

```text
ToolExecutionService.execute(tool_name, arguments, context)
        ├── ToolRegistry.execute(...) → ToolResult（原样返回）
        ├── Timing（now_utc / perf_counter）
        ↓
    ToolExecutionRecord（frozen）
        ↓
    observer.on_execution(record)      ← 可选；失败完全隔离
```

覆盖（§十五）：

```text
1  No Observer        execute() → ToolResult（行为不变）
2  Success            ToolResult(True)  → observer 收到 1 个 record
3  Tool Failure       ToolResult(False) → observer 收到 1 个 record
4  Context Mapping    request_id / project_id / tool_call_id / round 来自 Context
5  Timing             started_at / finished_at / duration_ms 有效
6  Tool Name          record.tool_name == execute(tool_name)
7  One Event          一次执行恰好 1 个事件（不 0 / 不 2 / 不 N）
8  Observer Failure   ToolResult 不变 / Tool 恰好执行 1 次 / 无 retry / 无 fallback
9  Record Immutable   observer 修改 record → FrozenInstanceError
10 Sensitive Data     arguments / data / error 中的 secret 不进入 Record
```

附加：observer 注入校验 / capability 拒绝 0 事件 / Registry 异常 0 事件且原样传播 /
无 Context → 0 事件 / Multi-Step（round 1,2,3；同 chat 同 request_id）/
1 个 DB-gated 真实 get_inventory 回归。

0 DB / 0 Network / 0 Real LLM（除 1 个 DB-gated 只读回归）。
"""
from __future__ import annotations

import dataclasses
import json
import logging
import time
from typing import Any

import pytest

from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorCapabilityError,
)
from backend.app.services.tool_chat_service import ToolChatService
from backend.app.services.tool_execution_context import ToolExecutionContext
from backend.app.services.tool_execution_observer import ToolExecutionObserver
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.errors import ToolValidationError
from backend.app.tools.get_inventory import (
    GetInventoryHandler,
    register_get_inventory_tool,
)
from backend.app.tools.mock_tools import (
    GET_INVENTORY_DEFINITION as MOCK_INVENTORY_DEFINITION,
    register_mock_tools,
)
from backend.app.tools.registry import ToolRegistry, ToolResult

from tests.test_get_inventory_tool import requires_db  # noqa: E402
from tests.test_tool_chat_real_get_inventory import (  # noqa: E402
    _PROJECT_ID,
    _real_registry,
    real_engine,
)
from tests.test_tool_chat_service import (  # noqa: E402
    ScriptedLLMClient,
    _tool_call_llm_response,
)

_SECRETS = (
    "postgresql://", "wms_user", "s3cret-pw", "db.internal",
    "DATABASE_URL", "Authorization", "Bearer ", "sk-", "password",
    "Traceback",
)


# ============================================================
# 测试替身（仅测试；生产代码无 Recording 实现）
# ============================================================

class RecordingToolExecutionObserver:
    """内存 Recording Observer（§十四；只追加记录，不做别的）。"""

    def __init__(self) -> None:
        self.records: list[ToolExecutionRecord] = []

    def on_execution(self, record: ToolExecutionRecord) -> None:
        self.records.append(record)


class _ExplodingObserver:
    """每次回调都抛异常的 observer（§十 / §十五 #8）。"""

    def __init__(self) -> None:
        self.calls = 0

    def on_execution(self, record: ToolExecutionRecord) -> None:
        self.calls += 1
        raise RuntimeError("observer failure")


class _ImmutableProbeObserver:
    """尝试改写 Record（必须失败）；记录是否抛 FrozenInstanceError。"""

    def __init__(self) -> None:
        self.frozen_errors = 0
        self.records: list[ToolExecutionRecord] = []

    def on_execution(self, record: ToolExecutionRecord) -> None:
        self.records.append(record)
        try:
            record.success = False  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            self.frozen_errors += 1


class _RecordingRegistry:
    """包装真实 Registry：记录 execute 入参（验证 arguments 原样传递）。"""

    def __init__(self, inner: ToolRegistry) -> None:
        self._inner = inner
        self.calls: list[tuple[str, Any]] = []

    def list_definitions(self):
        return self._inner.list_definitions()

    async def execute(self, tool_name: str, arguments: Any = None) -> ToolResult:
        self.calls.append((tool_name, arguments))
        return await self._inner.execute(tool_name, arguments=arguments)


class _EchoHandler:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {"echo": dict(arguments)}


class _FailingHandler:
    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raise ToolValidationError(
            tool_name="get_inventory",
            reason="material_code 包含非法字符",
            field_path="material_code",
        )


# ============================================================
# 辅助
# ============================================================

def _context(**overrides: Any) -> ToolExecutionContext:
    kwargs: dict[str, Any] = {
        "request_id": "req-0001",
        "round": 1,
        "project_id": None,
        "tool_call_id": "call_001",
    }
    kwargs.update(overrides)
    return ToolExecutionContext(**kwargs)


def _registry_with(handler: Any) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(MOCK_INVENTORY_DEFINITION, handler)
    return registry


def _mock_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_mock_tools(registry)
    return registry


def _boundary(
    registry: ToolRegistry,
    *,
    observer: Any = None,
    capabilities: ProjectCapabilities | None = None,
    project_id: str | None = None,
) -> ToolExecutionService:
    return ToolExecutionService(
        registry=registry,
        observer=observer,
        capabilities=capabilities,
        project_id=project_id,
    )


def _blob(record: ToolExecutionRecord) -> str:
    return json.dumps(dataclasses.asdict(record), default=str)


# ============================================================
# 1. No Observer（向后兼容）
# ============================================================

class TestNoObserver:
    async def test_execute_without_observer_unchanged(self) -> None:
        handler = _EchoHandler()
        boundary = _boundary(_registry_with(handler))

        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(),
        )

        assert result.success is True
        assert result.data == {"echo": {"material_code": "M1"}}
        assert handler.calls == 1
        assert boundary.observer is None

    async def test_constructor_signature_backward_compatible(self) -> None:
        """旧构造形态（registry / capabilities / project_id）保持合法。"""
        boundary = ToolExecutionService(
            registry=_mock_registry(),
            capabilities=ProjectCapabilities(),
            project_id="project-a",
        )
        assert boundary.observer is None

    async def test_result_identical_with_and_without_observer(self) -> None:
        registry = _mock_registry()
        plain = await _boundary(registry).execute(
            "get_inventory", arguments={"material_code": "M1"}
        )
        watched = await _boundary(
            registry, observer=RecordingToolExecutionObserver()
        ).execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(),
        )
        assert plain == watched


# ============================================================
# 2 / 3. Success 与 ToolResult Failure
# ============================================================

class TestObserverReceivesRecord:
    async def test_success_emits_one_record(self) -> None:
        observer = RecordingToolExecutionObserver()
        boundary = _boundary(_registry_with(_EchoHandler()), observer=observer)

        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(),
        )

        assert result.success is True
        assert len(observer.records) == 1
        assert observer.records[0].success is True

    async def test_tool_failure_still_emits_record(self) -> None:
        observer = RecordingToolExecutionObserver()
        boundary = _boundary(
            _registry_with(_FailingHandler()), observer=observer
        )

        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(),
        )

        assert result.success is False
        assert len(observer.records) == 1
        record = observer.records[0]
        assert record.success is False
        assert record.error_type == "ToolValidationError"

    async def test_schema_rejection_emits_record(self) -> None:
        observer = RecordingToolExecutionObserver()
        boundary = _boundary(_registry_with(_EchoHandler()), observer=observer)

        result = await boundary.execute("get_inventory", context=_context())

        assert result.success is False
        assert len(observer.records) == 1
        assert observer.records[0].success is False

    async def test_unknown_tool_emits_record(self) -> None:
        observer = RecordingToolExecutionObserver()
        boundary = _boundary(ToolRegistry(), observer=observer)

        result = await boundary.execute("ghost", context=_context())

        assert result.success is False
        assert len(observer.records) == 1
        assert observer.records[0].tool_name == "ghost"


# ============================================================
# 4 / 5 / 6. Context / Timing / Tool Name
# ============================================================

class TestRecordFields:
    async def test_context_fields_from_execution_context(self) -> None:
        observer = RecordingToolExecutionObserver()
        boundary = _boundary(
            _registry_with(_EchoHandler()),
            observer=observer,
            project_id="project-a",
        )
        context = _context(
            request_id="req-xyz", round=3, project_id="project-a",
            tool_call_id="call_777",
        )

        await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=context,
        )

        record = observer.records[0]
        assert record.request_id == "req-xyz"
        assert record.round == 3
        assert record.project_id == "project-a"
        assert record.tool_call_id == "call_777"

    async def test_tool_name_equals_execute_argument(self) -> None:
        observer = RecordingToolExecutionObserver()
        boundary = _boundary(_mock_registry(), observer=observer)

        await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(),
        )

        assert observer.records[0].tool_name == "get_inventory"

    async def test_timing_is_valid(self) -> None:
        observer = RecordingToolExecutionObserver()
        boundary = _boundary(_registry_with(_EchoHandler()), observer=observer)

        await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(),
        )

        record = observer.records[0]
        assert record.started_at.tzinfo is not None
        assert record.finished_at.tzinfo is not None
        assert record.started_at <= record.finished_at
        assert record.duration_ms >= 0.0
        assert isinstance(record.duration_ms, float)

    async def test_timing_measures_real_work(self) -> None:
        class _SlowHandler:
            async def __call__(self, arguments: dict[str, Any]) -> dict:
                time.sleep(0.02)
                return {"ok": True}

        observer = RecordingToolExecutionObserver()
        boundary = _boundary(_registry_with(_SlowHandler()), observer=observer)

        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(),
        )

        assert result.success is True
        assert observer.records[0].duration_ms >= 15.0

    async def test_record_field_whitelist(self) -> None:
        observer = RecordingToolExecutionObserver()
        boundary = _boundary(_registry_with(_EchoHandler()), observer=observer)

        await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(),
        )

        record = observer.records[0]
        record.assert_field_whitelist()
        assert isinstance(record, ToolExecutionRecord)


# ============================================================
# 7. 事件数量（恰好 1 / 0）
# ============================================================

class TestEventCount:
    async def test_exactly_one_event_per_execution(self) -> None:
        observer = RecordingToolExecutionObserver()
        registry = _registry_with(_EchoHandler())
        boundary = _boundary(registry, observer=observer)

        for _ in range(3):
            await boundary.execute(
                "get_inventory", arguments={"material_code": "M1"},
                context=_context(),
            )

        assert len(observer.records) == 3      # 3 次执行 → 恰好 3 个事件

    async def test_no_context_no_event(self) -> None:
        """context=None（链路 A / 旧调用）→ 0 事件（边界不生成 request_id）。"""
        observer = RecordingToolExecutionObserver()
        boundary = _boundary(_registry_with(_EchoHandler()), observer=observer)

        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"}
        )

        assert result.success is True
        assert observer.records == []

    async def test_capability_denied_no_event(self) -> None:
        observer = RecordingToolExecutionObserver()
        boundary = _boundary(
            _registry_with(_EchoHandler()),
            observer=observer,
            capabilities=ProjectCapabilities(tool_names=()),
            project_id="project-b",
        )

        with pytest.raises(AIOrchestratorCapabilityError):
            await boundary.execute(
                "get_inventory", arguments={"material_code": "M1"},
                context=_context(project_id="project-b"),
            )

        assert observer.records == []          # 未发生执行 → 0 事件

    async def test_registry_exception_no_event_and_propagates(self) -> None:
        error = RuntimeError("registry boom")

        class _ExplodingRegistry:
            async def execute(self, tool_name, arguments=None):  # noqa: ANN001
                raise error

        observer = RecordingToolExecutionObserver()
        boundary = _boundary(
            _ExplodingRegistry(), observer=observer  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError) as excinfo:
            await boundary.execute(
                "get_inventory", arguments={"material_code": "M1"},
                context=_context(),
            )

        assert excinfo.value is error          # 原样传播（同一对象）
        assert observer.records == []          # 无 ToolResult → 无 Record


# ============================================================
# 8. Observer Failure Isolation
# ============================================================

class TestObserverFailureIsolation:
    async def test_success_result_unaffected(self) -> None:
        handler = _EchoHandler()
        observer = _ExplodingObserver()
        boundary = _boundary(_registry_with(handler), observer=observer)

        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(),
        )

        assert result.success is True           # 不是 ToolResult(False)
        assert result.data == {"echo": {"material_code": "M1"}}
        assert handler.calls == 1               # Tool 恰好执行 1 次
        assert observer.calls == 1              # observer 不重试

    async def test_failure_result_unaffected(self) -> None:
        observer = _ExplodingObserver()
        boundary = _boundary(
            _registry_with(_FailingHandler()), observer=observer
        )

        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(),
        )

        assert result.success is False
        assert "非法字符" in (result.error or "")
        assert observer.calls == 1

    async def test_observer_failure_is_logged_without_message(
        self, caplog
    ) -> None:
        observer = _ExplodingObserver()
        boundary = _boundary(_registry_with(_EchoHandler()), observer=observer)

        with caplog.at_level(logging.WARNING):
            await boundary.execute(
                "get_inventory", arguments={"material_code": "M1"},
                context=_context(),
            )

        messages = [r.getMessage() for r in caplog.records]
        assert "tool execution observer failed" in messages
        text = " ".join(
            f"{r.getMessage()} {getattr(r, 'error_type', '')}"
            for r in caplog.records
        )
        assert "RuntimeError" in text           # 只记类型（归因）
        assert "observer failure" not in text   # 不写 exception message

    async def test_observer_validation_rejects_invalid(self) -> None:
        with pytest.raises(TypeError):
            ToolExecutionService(
                registry=_mock_registry(),
                observer=object(),  # type: ignore[arg-type]
            )

    async def test_observer_wired_via_service_and_chat_unchanged(self) -> None:
        """ToolChatService 注入带 observer 的边界 → 链路结果不变。"""
        observer = _ExplodingObserver()
        registry = _mock_registry()
        boundary = _boundary(registry, observer=observer)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {"material_code": "M1"}),
            "最终回答",
        ])
        service = ToolChatService(llm_client=llm, execution_service=boundary)

        response = await service.chat("查询库存", registry=registry)

        assert response.answer == "最终回答"
        assert [c.tool_name for c in response.tool_calls] == ["get_inventory"]
        assert observer.calls == 1


# ============================================================
# 9. Record Immutable
# ============================================================

class TestRecordImmutability:
    async def test_observer_cannot_mutate_record(self) -> None:
        probe = _ImmutableProbeObserver()
        boundary = _boundary(_registry_with(_EchoHandler()), observer=probe)

        await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(),
        )

        assert probe.frozen_errors == 1
        assert probe.records[0].success is True


# ============================================================
# 10. Sensitive Data Boundary
# ============================================================

class TestSensitiveDataBoundary:
    async def test_arguments_and_result_data_not_visible(self) -> None:
        observer = RecordingToolExecutionObserver()
        registry = _registry_with(_EchoHandler())
        boundary = _boundary(registry, observer=observer)

        await boundary.execute(
            "get_inventory",
            arguments={
                "material_code": "postgresql://wms_user:s3cret-pw@db.internal:5432/wms",
                "Authorization": "Bearer sk-leak",
            },
            context=_context(),
        )

        blob = _blob(observer.records[0])
        for secret in _SECRETS:
            assert secret not in blob, secret

    async def test_result_error_message_not_visible(self) -> None:
        class _LeakyHandler:
            async def __call__(self, arguments: dict[str, Any]) -> dict:
                raise ToolValidationError(
                    tool_name="get_inventory",
                    reason=(
                        "connection refused: postgresql://wms_user:s3cret-pw@db.internal"
                        " (password=s3cret-pw)"
                    ),
                    field_path="material_code",
                )

        observer = RecordingToolExecutionObserver()
        boundary = _boundary(_registry_with(_LeakyHandler()), observer=observer)

        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=_context(),
        )

        assert result.success is False
        record = observer.records[0]
        blob = f"{_blob(record)} {record!r}"
        for secret in _SECRETS:
            assert secret not in blob, secret
        assert record.error_type == "ToolValidationError"


# ============================================================
# Multi-Step（ToolChatService 轮次语义，§十七）
# ============================================================

class TestMultiStepRecords:
    async def test_rounds_and_request_id(self) -> None:
        observer = RecordingToolExecutionObserver()
        registry = _mock_registry()
        boundary = _boundary(
            registry, observer=observer, project_id="project-a"
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

        records = observer.records
        assert [r.round for r in records] == [1, 2, 3]
        assert [r.tool_call_id for r in records] == [
            "call_001", "call_002", "call_003",
        ]
        assert len({r.request_id for r in records}) == 1     # 同 chat 同 request_id
        assert {r.project_id for r in records} == {"project-a"}
        assert all(r.success for r in records)

    async def test_different_chats_different_request_id(self) -> None:
        observer = RecordingToolExecutionObserver()
        registry = _mock_registry()
        boundary = _boundary(registry, observer=observer)
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", {}, call_id="c1"),
            "answer-1",
            _tool_call_llm_response("get_inventory", {}, call_id="c2"),
            "answer-2",
        ])
        service = ToolChatService(llm_client=llm, execution_service=boundary)

        await service.chat("q1", registry=registry)
        await service.chat("q2", registry=registry)

        first, second = observer.records
        assert first.request_id != second.request_id
        assert first.round == 1 and second.round == 1


# ============================================================
# 真实 Tool（DB-gated：1 个）
# ============================================================

@requires_db()
class TestRealToolObserverDb:
    async def test_real_get_inventory_emits_record(
        self, real_engine: Any
    ) -> None:
        observer = RecordingToolExecutionObserver()
        registry = _real_registry(real_engine)
        boundary = ToolExecutionService(
            registry=registry,
            project_id=_PROJECT_ID,
            observer=observer,
        )
        llm = ScriptedLLMClient([
            _tool_call_llm_response(
                "get_inventory", {"material_code": "MAT-001"}, call_id="call_001"
            ),
            "MAT-001 当前库存为 250。",
        ])
        service = ToolChatService(llm_client=llm, execution_service=boundary)

        result = await service.chat("查询 MAT-001 库存", registry=registry)

        assert result.answer == "MAT-001 当前库存为 250。"
        assert len(observer.records) == 1
        record = observer.records[0]
        assert record.success is True
        assert record.tool_name == "get_inventory"
        assert record.round == 1
        assert record.project_id == _PROJECT_ID
        assert record.request_id.strip()
        assert record.duration_ms >= 0.0
        assert "MAT-001" not in _blob(record)      # 无业务数据 / 无 SQL
        assert "SELECT" not in _blob(record).upper()


# ============================================================
# Protocol 形状（§四）
# ============================================================

class TestObserverProtocol:
    def test_protocol_shape(self) -> None:
        import inspect
        import typing

        signature = inspect.signature(ToolExecutionObserver.on_execution)
        assert list(signature.parameters) == ["self", "record"]
        hints = typing.get_type_hints(ToolExecutionObserver.on_execution)
        assert hints["record"] is ToolExecutionRecord

    def test_recording_observer_is_a_valid_observer(self) -> None:
        """测试替身满足 Protocol（duck-typing；边界按 on_execution 校验）。"""
        observer = RecordingToolExecutionObserver()
        boundary = ToolExecutionService(
            registry=_mock_registry(), observer=observer
        )
        assert boundary.observer is observer


# ============================================================
# 静态：唯一回调点 + 隔离结构（§十九）
# ============================================================

class TestObserverBoundaryStatic:
    _EXECUTION_MODULE = "backend/app/services/tool_execution_service.py"

    def _source(self) -> str:
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, *self._EXECUTION_MODULE.split("/"))
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_single_call_site_with_record_only(self) -> None:
        """AST：全模块只有 1 处 ``observer.on_execution(record)`` 调用，
        位置参数仅 ``record``，无关键字参数（不传 arguments / result / context）。"""
        import ast

        tree = ast.parse(self._source())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "on_execution"
        ]
        assert len(calls) == 1, [ast.unparse(c) for c in calls]
        call = calls[0]
        assert [ast.unparse(arg) for arg in call.args] == ["record"]
        assert call.keywords == []

    def test_emission_is_wrapped_in_isolation_block(self) -> None:
        import ast

        tree = ast.parse(self._source())
        emitter = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_emit_record"
        )
        assert [
            node for node in ast.walk(emitter) if isinstance(node, ast.Try)
        ], "Record 构建 / observer 回调必须整体隔离（try/except）"
        # 隔离块内不得出现 retry / loop / sleep 之类的补偿动作
        for forbidden in (ast.While, ast.For, ast.AsyncFor):
            assert not [
                n for n in ast.walk(emitter) if isinstance(n, forbidden)
            ], forbidden

    def test_only_emitted_when_context_and_observer_present(self) -> None:
        source = self._source()
        assert (
            "emit_record = context is not None and self._observer is not None"
            in source
        )
