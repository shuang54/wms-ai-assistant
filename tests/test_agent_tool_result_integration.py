"""Phase 5 P-4 Integration — Tool Result **立即写入**（离线；零 DB / 零真实 LLM）。

覆盖（任务书 §十六 / §二十 / §二十二 / §二十三 / §二十七 / §二十八 / §二十九 /
§三十 / §三十一 / §三十二 / §三十四 / §三十五 / §三十六）：

```text
1  Integration        → Agent → Tool → Result → 写入点（run_id / step_index /
                        tool_name / project_id / tool_call_id / result 全部正确）
2  Write timing       → Tool Result 写入发生在**下一轮 LLM 之前**（立即）
3  Ordering           → Tool Result → Step → Run（生命周期顺序，非同一事务）
4  Crash window       → Tool 已执行、Run 未完成（第二轮 LLM 失败）⇒ 结果已 durable
5  Duplicate          → 同一 (run_id, step_index) 记录两次 ⇒ 1 行（identity 有效）
6  No scope           → 不写 DB（best-effort；**不猜** request_id → run_id）
7  Non-Agent Tool path→ 普通 Tool 调用（ToolChatService 形态）零写入
8  Best-effort 隔离   → Store 失败 / 端口抛异常 ⇒ Tool / Agent 业务结果不变
9  Audit              → ToolExecutionContext / ToolExecutionRecord / schema 未变；
                        Runtime 0 DB import（端口由 composition root 注入）
```
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

from backend.app.agent import (
    AGENT_ERROR_TOOL_EXECUTION_FAILURE,
    AGENT_STATUS_COMPLETED,
    AGENT_STATUS_FAILED,
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentDecision,
    AgentRuntime,
    AgentRuntimePolicy,
)
from backend.app.agent.ids import agent_run_scope, current_agent_run_id
from backend.app.agent.recorder import (
    AgentToolResultWritePoint,
    BestEffortAgentToolResultRecorder,
    build_agent_tool_result_write_point,
)
from backend.app.db.models.tool_execution_record import ToolExecutionRecordModel
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorService,
    RouteDecision,
    RouteType,
)
from backend.app.services.tool_execution_context import ToolExecutionContext
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.tools.base import ToolResult
from tests.test_agent_runtime import FakeLLM, _execution_service
from tests.test_agent_tool_result_store import FakeAgentToolResultRepository
from tests.test_agent_step_persistence import _AgentRouteRouter

__all__ = [
    "TestToolResultImmediateWrite",
    "TestToolResultWriteOrdering",
    "TestToolResultCrashWindow",
    "TestToolResultPersistenceIdentity",
    "TestToolResultNoScopeIsolation",
    "TestToolResultBestEffortIsolation",
    "TestToolResultIntegrationBoundary",
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENT_DIR = _REPO_ROOT / "backend" / "app" / "agent"

_TOOL_NAME = "get_inventory"
#: 与 ``tests.test_agent_runtime._execution_service`` 的授权作用域一致
#: （ToolExecutionContext.project_id 必须等于执行边界 project_id）。
_PROJECT_ID = "phase5-agent-test"

_TOOL_CALL = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_TOOL_NAME,
    arguments={"material_code": "MAT001"},
    tool_call_id="call_p4_1",
)
_FINAL = AgentDecision(type=DECISION_FINAL, content="库存 120 件。")


class _RecordingSink:
    """写入端口替身：记录身份 / 参数 / 写入时刻的 LLM 轮次。"""

    def __init__(self, *, order: list[str] | None = None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._order = order
        self._error = error

    async def arecord_tool_result(self, **kwargs: Any) -> int | None:
        self.calls.append({**kwargs, "scope_run_id": current_agent_run_id()})
        if self._order is not None:
            self._order.append("tool_result")
        if self._error is not None:
            raise self._error
        return 1


class _RecordingStepRecorder:
    def __init__(self, order: list[str]) -> None:
        self._order = order

    def record(self, result: Any) -> list[int]:
        self._order.append("steps")
        return []

    async def arecord(self, result: Any) -> list[int]:
        return self.record(result)


class _RecordingRunRecorder:
    def __init__(self, order: list[str]) -> None:
        self._order = order

    def record(self, result: Any, **kwargs: Any) -> int | None:
        self._order.append("run")
        return 1

    async def arecord(self, result: Any, **kwargs: Any) -> int | None:
        return self.record(result, **kwargs)


def _runtime(
    llm: FakeLLM,
    *,
    sink: Any = None,
    error: Exception | None = None,
) -> AgentRuntime:
    service, _registry, _handler = _execution_service()
    if error is not None:
        from tests.test_agent_runtime import RaisingExecutionService

        service = RaisingExecutionService(error)  # type: ignore[assignment]
    return AgentRuntime(
        llm=llm,
        tool_execution=service,
        policy=AgentRuntimePolicy(
            max_execution_time_ms=30_000, max_cost_tokens=1_000
        ),
        allowed_tools=(_TOOL_NAME,),
        tool_result_sink=sink,
    )


# ============================================================
# 1 / 2：集成（Agent → Tool → Result → 写入点）与写入时机
# ============================================================


class TestToolResultImmediateWrite:
    async def test_write_point_receives_full_identity(self) -> None:
        sink = _RecordingSink()
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        runtime = _runtime(llm, sink=sink)

        result = await runtime.run(question="查询 MAT001 库存", request_id="req-p4")

        assert result.status == AGENT_STATUS_COMPLETED
        assert len(sink.calls) == 1
        call = sink.calls[0]
        assert call["scope_run_id"] == result.run_id  # run_id 来自 Agent Run Scope
        assert call["step_index"] == 0  # **不是** round
        assert call["tool_name"] == _TOOL_NAME
        assert call["project_id"] == _PROJECT_ID  # 复用执行边界作用域
        assert call["tool_call_id"] == "call_p4_1"  # Provider 原值
        assert call["error_code"] is None
        assert isinstance(call["result"], ToolResult)
        assert call["result"].success is True
        assert call["result"].data == {"material_code": "MAT001", "quantity": 120}

    async def test_multi_step_records_distinct_step_indexes(self) -> None:
        second_call = AgentDecision(
            type=DECISION_TOOL_CALL,
            tool_name=_TOOL_NAME,
            arguments={"material_code": "MAT002"},
            tool_call_id="call_p4_2",
        )
        sink = _RecordingSink()
        llm = FakeLLM([_TOOL_CALL, second_call, _FINAL])

        result = await _runtime(llm, sink=sink).run(question="查询两批库存")

        assert result.status == AGENT_STATUS_COMPLETED
        assert [call["step_index"] for call in sink.calls] == [0, 1]
        assert [call["tool_call_id"] for call in sink.calls] == [
            "call_p4_1",
            "call_p4_2",
        ]

    async def test_write_happens_before_next_llm_call(self) -> None:
        """§十六：写入发生在 Tool handler 之后、**下一轮 LLM 之前**。"""
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        rounds_at_write: list[int] = []

        class Sink:
            async def arecord_tool_result(self, **kwargs: Any) -> int | None:
                rounds_at_write.append(llm.calls)
                return 1

        result = await _runtime(llm, sink=Sink()).run(question="查询 MAT001 库存")

        assert result.status == AGENT_STATUS_COMPLETED
        assert rounds_at_write == [1]  # 写入时只发生过第 1 轮 LLM（决策轮）
        assert llm.calls == 2

    async def test_failure_and_unknown_outcomes_are_recorded(self) -> None:
        """handler 返回失败 / execute 抛异常 ⇒ 都以稳定码立即记录。"""
        from tests.test_agent_runtime import RecordingHandler
        from backend.app.tools.errors import ToolExecutionError

        failing_handler = RecordingHandler(
            error=ToolExecutionError(_TOOL_NAME, "inventory tool down")
        )
        service, _registry, _handler = _execution_service(failing_handler)
        runtime = AgentRuntime(
            llm=FakeLLM([_TOOL_CALL, _FINAL]),
            tool_execution=service,
            policy=AgentRuntimePolicy(
                max_execution_time_ms=30_000, max_cost_tokens=1_000
            ),
            allowed_tools=(_TOOL_NAME,),
            tool_result_sink=_RecordingSink(),
        )
        sink = runtime.tool_result_sink
        assert isinstance(sink, _RecordingSink)

        result = await runtime.run(question="查询 MAT001 库存")

        # Registry 归一为 ToolResult(success=False) ⇒ Run FAILED（既有语义：不 retry），
        # 但该次执行**仍然**被立即记录。
        assert result.status == AGENT_STATUS_FAILED
        assert result.error == AGENT_ERROR_TOOL_EXECUTION_FAILURE
        assert len(sink.calls) == 1
        assert sink.calls[0]["result"].success is False
        assert sink.calls[0]["error_code"] is None  # 稳定码映射在写入点内完成

        unknown_sink = _RecordingSink()
        unknown_runtime = _runtime(
            FakeLLM([_TOOL_CALL, _FINAL]),
            sink=unknown_sink,
            error=RuntimeError("tool boundary exploded"),
        )

        unknown_result = await unknown_runtime.run(question="查询 MAT001 库存")

        assert unknown_result.status == AGENT_STATUS_FAILED
        assert unknown_result.error == AGENT_ERROR_TOOL_EXECUTION_FAILURE
        assert len(unknown_sink.calls) == 1
        assert unknown_sink.calls[0]["result"] is None
        assert unknown_sink.calls[0]["error_code"] == AGENT_ERROR_TOOL_EXECUTION_FAILURE
        assert unknown_sink.calls[0]["step_index"] == 0

    async def test_mapped_rows_for_known_and_unknown_failures(self) -> None:
        """经真实写入点 + 仓储：known failure ⇒ 可重放失败；unknown ⇒ fail-closed。"""
        from backend.app.tools.errors import ToolExecutionError
        from tests.test_agent_runtime import RecordingHandler

        # ① known failure（handler 返回失败）
        repository = FakeAgentToolResultRepository()
        write_point = AgentToolResultWritePoint(
            recorder=BestEffortAgentToolResultRecorder(repository=repository)
        )
        service, _registry, _handler = _execution_service(
            RecordingHandler(error=ToolExecutionError(_TOOL_NAME, "down"))
        )
        failed_result = await AgentRuntime(
            llm=FakeLLM([_TOOL_CALL, _FINAL]),
            tool_execution=service,
            policy=AgentRuntimePolicy(
                max_execution_time_ms=30_000, max_cost_tokens=1_000
            ),
            allowed_tools=(_TOOL_NAME,),
            tool_result_sink=write_point,
        ).run(question="查询 MAT001 库存")

        assert failed_result.status == AGENT_STATUS_FAILED
        row = repository.rows[0]
        assert row.success is False
        assert row.error_code == AGENT_ERROR_TOOL_EXECUTION_FAILURE
        assert row.result_unavailable is False  # failure 事实可重放
        assert row.result_payload is None
        assert row.result_size_bytes == 0

        # ② unknown（执行边界抛异常 ⇒ 结果不可判定）
        unknown_repository = FakeAgentToolResultRepository()
        unknown_result = await _runtime(
            FakeLLM([_TOOL_CALL, _FINAL]),
            sink=AgentToolResultWritePoint(
                recorder=BestEffortAgentToolResultRecorder(
                    repository=unknown_repository
                )
            ),
            error=RuntimeError("boundary exploded"),
        ).run(question="查询 MAT001 库存")

        assert unknown_result.status == AGENT_STATUS_FAILED
        unknown_row = unknown_repository.rows[0]
        assert unknown_row.success is False
        assert unknown_row.error_code == AGENT_ERROR_TOOL_EXECUTION_FAILURE
        assert unknown_row.result_unavailable is True  # fail-closed
        assert unknown_row.result_size_bytes == 0


# ============================================================
# 3：生命周期顺序（Tool Result → Step → Run）
# ============================================================


class TestToolResultWriteOrdering:
    async def test_tool_result_precedes_step_and_run(self) -> None:
        """§二十二 / §二十三：Tool Result 先落，其次 Step，最后 Run（非同一事务）。"""
        order: list[str] = []
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        runtime = _runtime(llm, sink=_RecordingSink(order=order))
        orchestrator = AIOrchestratorService(
            router=_AgentRouteRouter(),  # type: ignore[arg-type]
            agent_runtime=runtime,
            agent_run_recorder=_RecordingRunRecorder(order),
            agent_step_recorder=_RecordingStepRecorder(order),
        )

        result = await orchestrator.execute("查询 MAT001 库存")

        assert result.route == RouteType.AGENT
        assert result.metadata["agent_status"] == AGENT_STATUS_COMPLETED
        assert order == ["tool_result", "steps", "run"]

    async def test_run_terminal_write_not_required_for_store(self) -> None:
        """Store 不经 Orchestrator：Runtime 直跑也立即产生 Tool Result。"""
        order: list[str] = []
        llm = FakeLLM([_TOOL_CALL, _FINAL])

        result = await _runtime(llm, sink=_RecordingSink(order=order)).run(
            question="查询 MAT001 库存"
        )

        assert result.status == AGENT_STATUS_COMPLETED
        assert order == ["tool_result"]  # 无 Step / Run 写入（未接 Orchestrator）


# ============================================================
# 4 / 5：Crash window + persistence identity
# ============================================================


class TestToolResultCrashWindow:
    async def test_result_durable_when_run_does_not_complete(self) -> None:
        """§二十九：Tool 已执行 → 结果 durable；Run 未完成（第二轮 LLM 失败）。

        模拟 "Tool Result persisted / Checkpoint not persisted / process crashes"：
        **不** kill 进程、**不**实现 Checkpoint —— 只证明 Store 已落。
        """
        repository = FakeAgentToolResultRepository()
        write_point = AgentToolResultWritePoint(
            recorder=BestEffortAgentToolResultRecorder(repository=repository)
        )
        llm = FakeLLM([_TOOL_CALL], error_on_call=2, error=RuntimeError("llm down"))
        runtime = _runtime(llm, sink=write_point)

        result = await runtime.run(question="查询 MAT001 库存", request_id="req-crash")

        assert result.status == AGENT_STATUS_FAILED
        assert llm.calls == 2  # 第二轮失败（= 崩溃窗口：Tool 已执行）
        assert len(repository.rows) == 1  # Tool Result 已经 durable
        row = repository.rows[0]
        assert (row.run_id, row.step_index) == (result.run_id, 0)
        assert row.tool_name == _TOOL_NAME
        assert row.success is True
        assert row.result_unavailable is False
        assert row.result_payload == {
            "tool_name": _TOOL_NAME,
            "data": {"material_code": "MAT001", "quantity": 120},
        }
        assert row.tool_call_id == "call_p4_1"
        assert row.project_id == _PROJECT_ID


class TestToolResultPersistenceIdentity:
    async def test_duplicate_identity_keeps_single_row(self) -> None:
        """§三十：同一 (run_id, step_index) 记录两次 ⇒ 1 行（**无** runtime dedup）。"""
        repository = FakeAgentToolResultRepository()
        write_point = AgentToolResultWritePoint(
            recorder=BestEffortAgentToolResultRecorder(repository=repository)
        )
        tool_result = ToolResult(
            tool_name=_TOOL_NAME,
            success=True,
            data={"material_code": "MAT001", "quantity": 120},
        )

        with agent_run_scope("run-dup-1"):
            first = write_point.record_tool_result(
                step_index=0, tool_name=_TOOL_NAME, result=tool_result
            )
            second = write_point.record_tool_result(
                step_index=0, tool_name=_TOOL_NAME, result=tool_result
            )
        # 不同 Run 复用同一 step_index ⇒ 各自 1 行
        with agent_run_scope("run-dup-2"):
            third = write_point.record_tool_result(
                step_index=0, tool_name=_TOOL_NAME, result=tool_result
            )

        assert first is not None
        assert second is None  # ON CONFLICT (run_id, step_index) DO NOTHING
        assert third is not None
        assert len(repository.rows) == 2
        assert {row.run_id for row in repository.rows} == {"run-dup-1", "run-dup-2"}


# ============================================================
# 6 / 7：无 scope（不写）与普通 Tool 路径隔离
# ============================================================


class TestToolResultNoScopeIsolation:
    def test_write_point_without_scope_writes_nothing(self) -> None:
        """§三十一：无 Agent Run Scope ⇒ run_id=None ⇒ **不写**（绝不猜 request_id）。"""
        repository = FakeAgentToolResultRepository()
        write_point = AgentToolResultWritePoint(
            recorder=BestEffortAgentToolResultRecorder(repository=repository)
        )

        assert current_agent_run_id() is None
        created = write_point.record_tool_result(
            step_index=0,
            tool_name=_TOOL_NAME,
            result=ToolResult(tool_name=_TOOL_NAME, success=True, data={"qty": 1}),
            project_id=_PROJECT_ID,
            tool_call_id="call_no_scope",
        )

        assert created is None
        assert repository.rows == []
        assert repository.batches == []  # 连仓储都没触达

    def test_write_point_without_scope_is_best_effort(self) -> None:
        repository = FakeAgentToolResultRepository(error=RuntimeError("db down"))
        write_point = AgentToolResultWritePoint(
            recorder=BestEffortAgentToolResultRecorder(repository=repository)
        )

        assert write_point.record_tool_result(
            step_index=0, tool_name=_TOOL_NAME
        ) is None

    def test_write_point_has_no_request_id_fallback(self) -> None:
        """写入点签名里**没有** request_id / run_id 参数（身份只来自 scope）。"""
        params = set(
            inspect.signature(AgentToolResultWritePoint.record_tool_result).parameters
        )

        assert params == {
            "self",
            "step_index",
            "tool_name",
            "result",
            "error_code",
            "project_id",
            "tool_call_id",
        }

    async def test_non_agent_tool_path_writes_nothing(self) -> None:
        """§三十二：普通 Tool 调用（ToolExecutionService + Context）零写入。"""
        repository = FakeAgentToolResultRepository()
        service, registry, _handler = _execution_service()

        # ToolChatService 形态：只有 ToolExecutionContext（无 Agent Run Scope）
        result = await service.execute(
            _TOOL_NAME,
            arguments={"material_code": "MAT001"},
            context=ToolExecutionContext(
                request_id="plain-request",
                round=1,
                project_id=_PROJECT_ID,
                tool_call_id="plain-call",
            ),
        )

        assert result.success is True
        assert [name for name, _ in registry.execute_calls] == [_TOOL_NAME]
        assert current_agent_run_id() is None
        assert repository.rows == []
        # ToolExecutionService 不认识 Tool Result Store（零耦合）
        assert not hasattr(service, "tool_result_sink")
        assert not hasattr(service, "agent_tool_result")


# ============================================================
# 8：Best-effort 隔离（Store 失败不改变 Tool / Agent 结果）
# ============================================================


class TestToolResultBestEffortIsolation:
    async def test_store_failure_does_not_change_agent_result(self) -> None:
        """§二十：DB 不可用 ⇒ Agent 结果不变；不 retry Tool / LLM、不 fallback。"""
        repository = FakeAgentToolResultRepository(error=RuntimeError("db down"))
        write_point = AgentToolResultWritePoint(
            recorder=BestEffortAgentToolResultRecorder(repository=repository)
        )
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        service, _registry, handler = _execution_service()
        runtime = AgentRuntime(
            llm=llm,
            tool_execution=service,
            policy=AgentRuntimePolicy(
                max_execution_time_ms=30_000, max_cost_tokens=1_000
            ),
            allowed_tools=(_TOOL_NAME,),
            tool_result_sink=write_point,
        )

        result = await runtime.run(question="查询 MAT001 库存")

        assert result.status == AGENT_STATUS_COMPLETED
        assert result.content == "库存 120 件。"
        assert result.tool_calls == 1
        assert llm.calls == 2  # 未 retry LLM
        assert handler.calls == [{"material_code": "MAT001"}]  # 未 retry Tool
        assert repository.rows == []

    async def test_sink_exception_does_not_change_agent_result(self) -> None:
        """§二十：端口抛异常 ⇒ Runtime 侧兜底（warning），控制流不变。"""
        sink = _RecordingSink(error=RuntimeError("sink exploded"))
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        service, _registry, handler = _execution_service()
        runtime = AgentRuntime(
            llm=llm,
            tool_execution=service,
            policy=AgentRuntimePolicy(
                max_execution_time_ms=30_000, max_cost_tokens=1_000
            ),
            allowed_tools=(_TOOL_NAME,),
            tool_result_sink=sink,
        )

        result = await runtime.run(question="查询 MAT001 库存")

        assert result.status == AGENT_STATUS_COMPLETED
        assert llm.calls == 2
        assert handler.calls == [{"material_code": "MAT001"}]

    async def test_async_write_point_failure_is_swallowed(self) -> None:
        repository = FakeAgentToolResultRepository(error=RuntimeError("db down"))
        write_point = AgentToolResultWritePoint(
            recorder=BestEffortAgentToolResultRecorder(repository=repository)
        )

        with agent_run_scope("run-async-fail"):
            created = await write_point.arecord_tool_result(
                step_index=0,
                tool_name=_TOOL_NAME,
                result=ToolResult(tool_name=_TOOL_NAME, success=True, data={"qty": 2}),
            )

        assert created is None
        assert repository.rows == []


# ============================================================
# 9：边界审计（ToolExecutionContext / Record / schema / Runtime 依赖）
# ============================================================


class TestToolResultIntegrationBoundary:
    def test_tool_execution_context_is_unchanged(self) -> None:
        """§三十五：ToolExecutionContext 仍是既有 4 字段（**无** run_id）。"""
        assert list(ToolExecutionContext.__dataclass_fields__) == [
            "request_id",
            "round",
            "project_id",
            "tool_call_id",
        ]

    def test_tool_execution_record_is_unchanged_observation(self) -> None:
        """§三十六：ToolExecutionRecord 仍为 observation（无 result / run_id / step_index）。"""
        fields = set(ToolExecutionRecord.__dataclass_fields__)
        columns = {c.name for c in ToolExecutionRecordModel.__table__.columns}

        for forbidden in ("result", "run_id", "step_index", "result_payload"):
            assert forbidden not in fields
            assert forbidden not in columns
        assert columns == {
            "id",
            "request_id",
            "round",
            "tool_name",
            "started_at",
            "finished_at",
            "duration_ms",
            "success",
            "project_id",
            "tool_call_id",
            "error_code",
            "error_type",
        }

    def test_agent_run_and_step_schemas_unchanged(self) -> None:
        from backend.app.db.models import AgentRun, AgentStep

        assert {c.name for c in AgentRun.__table__.columns} == {
            "id",
            "run_id",
            "request_id",
            "project_id",
            "status",
            "error_code",
            "limit_reason",
            "step_count",
            "tool_call_count",
            "llm_call_count",
            "token_usage_unverifiable",
            "started_at",
            "finished_at",
            "duration_ms",
            "created_at",
        }
        assert {c.name for c in AgentStep.__table__.columns} == {
            "id",
            "run_id",
            "step_index",
            "step_type",
            "status",
            "tool_name",
            "error_code",
            "elapsed_ms",
            "created_at",
        }

    def test_runtime_has_no_db_dependency(self) -> None:
        """§三十四：Runtime 仍 0 DB import（端口由 composition root 注入）。"""
        source = (_AGENT_DIR / "runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        for module in modules:
            assert not module.startswith("backend.app.db"), module
            assert not module.startswith("sqlalchemy"), module
        assert "backend.app.agent.recorder" not in modules
        # 既有架构审计（recorder 一词不出现在执行内核）
        assert "recorder" not in source

    def test_runtime_loop_and_tool_boundary_untouched(self) -> None:
        """Tool 边界 / Registry / Handler 零改动（接线只在 Runtime 与端口之间）。"""
        for relative in (
            ("agent", "llm.py"),
            ("agent", "models.py"),
            ("agent", "capability.py"),
            ("services", "tool_execution_service.py"),
            ("tools", "registry.py"),
            ("tools", "get_inventory.py"),
            ("tools", "get_work_order.py"),
        ):
            source = (
                _REPO_ROOT / "backend" / "app" / relative[0] / relative[1]
            ).read_text(encoding="utf-8")

            assert "agent_tool_result" not in source, relative
            assert "tool_result_sink" not in source, relative

    def test_factory_wires_write_point_only_when_agent_enabled(self) -> None:
        source = (
            _REPO_ROOT
            / "backend"
            / "app"
            / "services"
            / "project_orchestrator_factory.py"
        ).read_text(encoding="utf-8")

        assert "tool_result_sink=build_agent_tool_result_write_point()" in source
        # 装配点在 ``if agent_enabled:`` 块内（默认关闭 ⇒ 不接线）
        assert source.index("if agent_enabled:") < source.index(
            "tool_result_sink=build_agent_tool_result_write_point()"
        )

    def test_composition_root_injects_write_point(
        self, monkeypatch: Any
    ) -> None:
        """真装配：agent_enabled=True ⇒ AgentRuntime 持有写入点（真 provider 不建）。"""
        import backend.app.db.session as session_module
        from tests.test_project_configuration import _build, _configuration_provider

        class _FakeProvider:
            async def chat(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
                raise AssertionError("装配测试不应调用 provider")

        provider, *_rest = _configuration_provider()
        monkeypatch.setattr(session_module, "get_engine", lambda: object())
        orchestrator = _build(
            "project-a",
            configuration_provider=provider,
            agent_enabled=True,
            agent_llm_provider=_FakeProvider(),
        )

        runtime = orchestrator._agent_runtime
        assert isinstance(runtime, AgentRuntime)
        assert isinstance(runtime.tool_result_sink, AgentToolResultWritePoint)
        assert runtime.allowed_tools == ("get_inventory",)
        # 生成的 Step / Run recorder 不受影响（各自独立 best-effort）
        assert orchestrator._agent_run_recorder is not None

    def test_composition_root_without_database_injects_no_sink(
        self, monkeypatch: Any
    ) -> None:
        import backend.app.db.session as session_module
        from tests.test_project_configuration import _build, _configuration_provider

        class _FakeProvider:
            async def chat(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
                raise AssertionError("装配测试不应调用 provider")

        provider, *_rest = _configuration_provider()
        monkeypatch.setattr(session_module, "get_engine", lambda: None)
        orchestrator = _build(
            "project-a",
            configuration_provider=provider,
            agent_enabled=True,
            agent_llm_provider=_FakeProvider(),
        )

        runtime = orchestrator._agent_runtime
        assert runtime.tool_result_sink is None  # 无 DATABASE_URL ⇒ 零开销跳过

    def test_agent_disabled_default_has_no_runtime(self) -> None:
        from tests.test_project_configuration import _build, _configuration_provider

        provider, *_rest = _configuration_provider()
        orchestrator = _build("project-a", configuration_provider=provider)

        assert orchestrator._agent_runtime is None  # 默认关闭 ⇒ 无 Agent 接线

    def test_build_write_point_respects_engine_availability(
        self, monkeypatch: Any
    ) -> None:
        import backend.app.db.session as session_module

        monkeypatch.setattr(session_module, "get_engine", lambda: None)
        assert build_agent_tool_result_write_point() is None

        monkeypatch.setattr(session_module, "get_engine", lambda: object())
        write_point = build_agent_tool_result_write_point()
        assert isinstance(write_point, AgentToolResultWritePoint)
        assert write_point.recorder is None  # 延迟解析

    async def test_runtime_rejects_bad_sink_shape(self) -> None:
        from backend.app.agent.errors import AgentPolicyError

        try:
            _runtime(FakeLLM([_FINAL]), sink=object())
        except AgentPolicyError as exc:
            assert "tool_result_sink" in str(exc)
        else:  # pragma: no cover - 防御
            raise AssertionError("非法 sink 必须装配期拒绝")
