"""Phase 5 S-1 — Agent Tool Execution Observation（**离线**；零 DB / 零真实 LLM）。

修复 D-1：Agent 路径 Tool 执行此前**不产生** ``ToolExecutionRecord``
（`AgentRuntime` 调用 ``execute()`` 未传 ``ToolExecutionContext`` ⇒ 边界
``emit_record = context is not None and observer is not None`` 为假）。

本阶段只做一件事：让 Agent Tool Calling **复用既有 ToolExecutionRecord 观测链路**。

```text
AIOrchestrator(request_id)
    ↓ run(request_id=...)
AgentRuntime（DB-free：只构造 ToolExecutionContext）
    ↓ execute(..., context=...)
ToolExecutionService（授权第二道校验 + 计时）
    ↓ observer.on_execution(record)
ToolExecutionRecord（**复用既有表**，不改 schema）
```

覆盖（任务书 §十三–§十四 / §二十一）：

```text
A Tool Success        → 1 条记录（success=true）
B Tool Failure        → 1 条记录（success=false）+ Agent FAILED/TOOL_EXECUTION_FAILURE
C Observation Failure → Tool 业务结果 / Agent 结果不变；无 retry / fallback
Correlation           → request_id · tool_call_id · project_id · round 全部正确
Authorization         → 未授权 Tool ⇒ Handler 0 次 · 记录 0 条（观察不绕过授权）
Boundary              → AgentRuntime 不 import DB / Repository / Record
```

复用（不重建框架）：``tests.test_agent_runtime`` 的 ``FakeLLM`` /
``RecordingHandler`` / ``_execution_service``。
"""
from __future__ import annotations

import ast
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.agent import (
    AGENT_ERROR_TOOL_EXECUTION_FAILURE,
    AGENT_ERROR_TOOL_NOT_ALLOWED,
    AGENT_STATUS_COMPLETED,
    AGENT_STATUS_FAILED,
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentDecision,
    AgentRuntime,
    AgentRuntimePolicy,
    AgentRuntimeResult,
)
from backend.app.agent.runtime import AgentRuntime as _RuntimeClass
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorExecutionError,
    AIOrchestratorService,
    RouteDecision,
    RouteType,
)
from backend.app.services.tool_execution_context import ToolExecutionContext
from backend.app.services.tool_execution_observer import ToolExecutionObserver
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.tools.errors import ToolError
from tests.test_agent_runtime import FakeLLM, RecordingHandler, _execution_service

__all__ = [
    "TestAgentToolObservationSuccess",
    "TestAgentToolObservationFailure",
    "TestAgentToolObservationIsolation",
    "TestAgentToolObservationCorrelation",
    "TestAgentToolObservationBoundary",
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENT_DIR = _REPO_ROOT / "backend" / "app" / "agent"

_TOOL_NAME = "get_inventory"
_UNAUTHORIZED_TOOL = "delete_inventory"
_PROJECT_ID = "phase5-tool-observation"
_REQUEST_ID = "req-tool-observation-1"

_TOOL_CALL = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_TOOL_NAME,
    arguments={"material_code": "MAT001"},
    tool_call_id="call_obs_1",
)
_TOOL_CALL_2 = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_TOOL_NAME,
    arguments={"material_code": "MAT002"},
    tool_call_id="call_obs_2",
)
_FINAL = AgentDecision(type=DECISION_FINAL, content="库存 120 件。")
_DENIED_CALL = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_UNAUTHORIZED_TOOL,
    arguments={},
    tool_call_id="call_denied",
)


# ============================================================
# Test-local fakes（只记录；不改生产行为）
# ============================================================


@dataclass
class _CapturedRecord:
    request_id: str | None
    round: int
    tool_name: str
    project_id: str | None
    tool_call_id: str | None
    success: bool


class RecordingObserver:
    """``ToolExecutionObserver`` 替身：记录 Record（可选抛异常）。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.records: list[ToolExecutionRecord] = []
        self.captured: list[_CapturedRecord] = []
        self._error = error

    def on_execution(self, record: ToolExecutionRecord) -> None:
        self.records.append(record)
        self.captured.append(
            _CapturedRecord(
                request_id=record.request_id,
                round=record.round,
                tool_name=record.tool_name,
                project_id=record.project_id,
                tool_call_id=record.tool_call_id,
                success=record.success,
            )
        )
        if self._error is not None:
            raise self._error


class _AgentRouteRouter:
    async def route(
        self, question: str, *, context: str | None = None
    ) -> RouteDecision:
        return RouteDecision(
            route=RouteType.AGENT,
            confidence=None,
            reason="smoke: pin AGENT",
            source="llm",
        )


def _runtime(
    llm: Any,
    *,
    observer: ToolExecutionObserver | None,
    handler: RecordingHandler | None = None,
    tool_names: tuple[str, ...] = (_TOOL_NAME,),
    allowed_tools: tuple[str, ...] = (_TOOL_NAME,),
    error: Exception | None = None,
) -> tuple[AgentRuntime, RecordingHandler, Any]:
    service, _registry, resolved_handler = _execution_service(
        handler, tool_names=tool_names
    )
    # 注入 observer（生产由 composition root 装配；此处为测试）
    if observer is not None:
        object.__setattr__(service, "_observer", observer)
    if error is not None:
        resolved_handler._error = error  # noqa: SLF001
    runtime = AgentRuntime(
        llm=llm,
        tool_execution=service,
        policy=AgentRuntimePolicy(
            max_execution_time_ms=30_000, max_cost_tokens=1_000
        ),
        allowed_tools=allowed_tools,
    )
    return runtime, resolved_handler, service


# ============================================================
# Case A：Tool Success
# ============================================================


class TestAgentToolObservationSuccess:
    async def test_success_emits_one_record(self) -> None:
        observer = RecordingObserver()
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        runtime, handler, _service = _runtime(llm, observer=observer)

        result = await runtime.run(question="查询 MAT001", request_id=_REQUEST_ID)

        assert result.status == AGENT_STATUS_COMPLETED
        assert len(handler.calls) == 1
        assert len(observer.records) == 1
        assert observer.records[0].success is True

    async def test_two_tool_calls_emit_two_records(self) -> None:
        observer = RecordingObserver()
        llm = FakeLLM([_TOOL_CALL, _TOOL_CALL_2, _FINAL])
        runtime, handler, _service = _runtime(llm, observer=observer)

        result = await runtime.run(question="两次查询", request_id=_REQUEST_ID)

        assert result.status == AGENT_STATUS_COMPLETED
        assert len(handler.calls) == 2
        assert len(observer.records) == 2
        assert [r.round for r in observer.records] == [1, 2]

    async def test_no_request_id_keeps_zero_event_behaviour(self) -> None:
        """未接线（request_id=None）⇒ 既有 '0 事件' 语义不变。"""
        observer = RecordingObserver()
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        runtime, handler, _service = _runtime(llm, observer=observer)

        result = await runtime.run(question="查询 MAT001")

        assert result.status == AGENT_STATUS_COMPLETED
        assert len(handler.calls) == 1
        assert observer.records == []  # context=None ⇒ 不产生 Record


# ============================================================
# Case B：Tool Failure
# ============================================================


class TestAgentToolObservationFailure:
    async def test_failure_emits_record_with_success_false(self) -> None:
        observer = RecordingObserver()
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        runtime, _handler, _service = _runtime(
            llm, observer=observer, error=ToolError("库存表不可用")
        )

        result = await runtime.run(question="查询 MAT001", request_id=_REQUEST_ID)

        # Agent 语义不变：FAILED / TOOL_EXECUTION_FAILURE
        assert result.status == AGENT_STATUS_FAILED
        assert result.error == AGENT_ERROR_TOOL_EXECUTION_FAILURE
        # 观测：失败**仍然**记录 1 条
        assert len(observer.records) == 1
        assert observer.records[0].success is False

    async def test_unauthorized_tool_emits_no_record_and_no_execution(self) -> None:
        """授权先于观测：未授权 Tool ⇒ Handler 0 次 · Record 0 条。"""
        observer = RecordingObserver()
        llm = FakeLLM([_DENIED_CALL, _FINAL])
        runtime, handler, _service = _runtime(llm, observer=observer)

        result = await runtime.run(question="删除库存", request_id=_REQUEST_ID)

        assert result.status == AGENT_STATUS_FAILED
        assert result.error == AGENT_ERROR_TOOL_NOT_ALLOWED
        assert handler.calls == []
        assert observer.records == []  # 观察不绕过授权


# ============================================================
# Case C：Observation Persistence Failure（best-effort 隔离）
# ============================================================


class TestAgentToolObservationIsolation:
    async def test_observer_failure_does_not_change_agent_result(self) -> None:
        observer = RecordingObserver(error=RuntimeError("DB down"))
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        runtime, handler, _service = _runtime(llm, observer=observer)

        result = await runtime.run(question="查询 MAT001", request_id=_REQUEST_ID)

        # Tool 业务结果与 Agent 结果都不受观测失败影响；无 retry / fallback
        assert result.status == AGENT_STATUS_COMPLETED
        assert result.content == _FINAL.content
        assert len(handler.calls) == 1  # 未被重试
        assert llm.calls == 2 and len(llm.messages) == 2  # 未触发重规划

    async def test_observer_failure_through_orchestrator(self) -> None:
        observer = RecordingObserver(error=RuntimeError("DB down"))
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        runtime, _handler, _service = _runtime(llm, observer=observer)
        orchestrator = AIOrchestratorService(
            router=_AgentRouteRouter(), agent_runtime=runtime
        )

        result = await orchestrator.execute("查询 MAT001")

        assert isinstance(result, AIOrchestrationResult)
        assert result.route is RouteType.AGENT
        assert result.metadata["agent_status"] == AGENT_STATUS_COMPLETED
        assert result.metadata["agent_tool_calls"] == 1

    async def test_failure_and_observer_failure_are_distinct(self) -> None:
        """Tool failure ≠ observation failure：前者 FAILED，后者业务不受影响。"""
        observer = RecordingObserver(error=RuntimeError("DB down"))
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        runtime, _handler, _service = _runtime(
            llm, observer=observer, error=ToolError("boom")
        )
        orchestrator = AIOrchestratorService(
            router=_AgentRouteRouter(), agent_runtime=runtime
        )

        with pytest.raises(AIOrchestratorExecutionError) as excinfo:
            await orchestrator.execute("查询 MAT001")

        assert AGENT_ERROR_TOOL_EXECUTION_FAILURE in str(excinfo.value)
        assert observer.records[0].success is False


# ============================================================
# Correlation：request_id / tool_call_id / project_id / round
# ============================================================


class TestAgentToolObservationCorrelation:
    async def test_record_carries_all_correlation_fields(self) -> None:
        observer = RecordingObserver()
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        runtime, _handler, service = _runtime(llm, observer=observer)

        await runtime.run(question="查询 MAT001", request_id=_REQUEST_ID)

        captured = observer.captured[0]
        assert captured.request_id == _REQUEST_ID  # AgentRun.request_id 同源
        assert captured.tool_call_id == "call_obs_1"  # Provider 原值
        # project_id = 执行边界的授权作用域（不硬编码、不重查 Registry）
        assert captured.project_id == service.project_id
        assert captured.project_id
        assert captured.round == 1
        assert captured.tool_name == _TOOL_NAME

    async def test_agent_run_request_id_equals_record_request_id(self) -> None:
        """§十四：AgentRun.request_id == ToolExecutionRecord.request_id。"""
        observer = RecordingObserver()
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        runtime, _handler, _service = _runtime(llm, observer=observer)
        orchestrator = AIOrchestratorService(
            router=_AgentRouteRouter(), agent_runtime=runtime
        )

        result = await orchestrator.execute("查询 MAT001")

        assert observer.captured[0].request_id == result.metadata["request_id"]

    async def test_context_is_frozen_and_tool_call_id_not_regenerated(self) -> None:
        """tool_call_id 必须来自 LLM；Runtime 不生成 uuid 替代。"""
        observer = RecordingObserver()
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        runtime, _handler, _service = _runtime(llm, observer=observer)

        await runtime.run(question="查询 MAT001", request_id=_REQUEST_ID)

        record = observer.records[0]
        assert record.tool_call_id == _TOOL_CALL.tool_call_id
        assert len(observer.captured) == 1

    def test_context_contract_shape(self) -> None:
        """§五：复用既有 ToolExecutionContext（不新建 AgentToolExecutionContext）。"""
        context = ToolExecutionContext(
            request_id=_REQUEST_ID,
            round=1,
            project_id=_PROJECT_ID,
            tool_call_id="call_obs_1",
        )
        context.assert_field_whitelist()
        assert {field for field in context.__dataclass_fields__} == {
            "request_id",
            "round",
            "project_id",
            "tool_call_id",
        }


# ============================================================
# §二十二：源码边界（Runtime DB-free）
# ============================================================


class TestAgentToolObservationBoundary:
    def test_runtime_does_not_import_db_or_repository(self) -> None:
        forbidden_modules = (
            "sqlalchemy",
            "backend.app.db",
            "backend.app.services.tool_execution_repository",
            "backend.app.services.tool_execution_persistence_service",
        )
        forbidden_names = (
            "ToolExecutionRepository",
            "ToolExecutionRecordModel",
            "Session",
            "sessionmaker",
        )
        for path in sorted(_AGENT_DIR.glob("*.py")):
            if path.name == "recorder.py":
                continue  # P-2 persistence boundary（允许 DB）
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith(forbidden_modules), (
                            path.name,
                            alias.name,
                        )
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    assert not module.startswith(forbidden_modules), (
                        path.name,
                        module,
                    )
                if isinstance(node, ast.Name) and node.id in forbidden_names:
                    raise AssertionError((path.name, node.id))

    def test_runtime_depends_on_service_and_context_only(self) -> None:
        """Runtime 可依赖 ToolExecutionService / ToolExecutionContext，不得依赖 Record 持久化。

        用 **AST 标识符**（不是文本子串）判定：docstring 中提及 Record 语义不算依赖。
        """
        source = (_AGENT_DIR / "runtime.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(alias.name for alias in node.names)
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

        assert "backend.app.services.tool_execution_context" in imported
        assert "backend.app.services.tool_execution_service" in imported
        for forbidden in (
            "ToolExecutionRecord",
            "ToolExecutionRepository",
            "backend.app.db.tool_execution_repository",
        ):
            assert forbidden not in imported, forbidden

    def test_runtime_method_exposes_context_builder(self) -> None:
        runtime = _RuntimeClass.__new__(_RuntimeClass)  # 只验证方法存在与形状
        assert callable(runtime._tool_execution_context)
        assert callable(runtime.run)


# ============================================================
# DB（可选）：复用真实持久化 observer，验证 tool_execution_record
# ============================================================


@pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS", "").strip().lower()
    not in {"1", "true", "yes", "on"},
    reason="set RUN_DB_TESTS=1 to enable agent tool observation DB test",
)
class TestAgentToolObservationDb:
    """``RUN_DB_TESTS=1`` 才运行：insert → verify → cleanup（residue = 0）。"""

    @staticmethod
    def _rows(engine: Any, request_id: str) -> list[Any]:
        from sqlalchemy import text

        with engine.connect() as conn:
            return list(
                conn.execute(
                    text(
                        "SELECT request_id, round, tool_name, project_id,"
                        " tool_call_id, success FROM ai_ops.tool_execution_record"
                        " WHERE request_id = :rid ORDER BY id"
                    ),
                    {"rid": request_id},
                )
            )

    async def test_agent_tool_execution_persists_real_record(self) -> None:
        from backend.app.db.session import get_engine
        from backend.app.services.tool_execution_persistence_adapter import (
            ToolExecutionPersistenceAdapter,
        )

        engine = get_engine()
        if engine is None:
            pytest.skip("DATABASE_URL 未配置（DB 测试跳过）")

        request_id = f"req-agent-obs-{uuid.uuid4().hex[:16]}"
        llm = FakeLLM([_TOOL_CALL, _FINAL])
        runtime, _handler, _service = _runtime(
            llm, observer=ToolExecutionPersistenceAdapter()
        )
        try:
            await runtime.run(question="查询 MAT001", request_id=request_id)
            rows = self._rows(engine, request_id)
        finally:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "DELETE FROM ai_ops.tool_execution_record"
                        " WHERE request_id = :rid"
                    ),
                    {"rid": request_id},
                )

        assert len(rows) == 1
        row = rows[0]
        assert row.request_id == request_id
        assert row.round == 1
        assert row.tool_name == _TOOL_NAME
        assert row.tool_call_id == "call_obs_1"
        assert row.success is True

        assert self._rows(engine, request_id) == []  # residue = 0
