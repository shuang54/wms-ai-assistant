"""Phase 5 P-7 Agent 协作式取消（Cooperative Cancellation）—— **离线**（零 DB）。

覆盖（任务书 §二十四–§三十八 / §五十二–§五十四 / §六十–§六十三 / §六十五）：

```text
1   取消模型        信号 = Checkpoint.cancel_requested（布尔列；不是新状态枚举；§二十八）
2   协调器          终态 no-op / 幂等 / PAUSED 终态化 / 无 Checkpoint ⇒ 不可取消（§三十七 / §三十八）
3   安全边界        LLM 前 / LLM 返回后 / Tool 前 / Tool 返回后（§二十五 / §二十九 / §三十 / §三十一）
4   协作式          Tool 已开始 ⇒ **不强杀** ⇒ 等它返回 ⇒ Tool Result 保留 ⇒ CANCELLED（§二十六 / §三十一）
5   终态            AgentRun/Runtime = CANCELLED（**不**映射成 FAILED；§三十四）
6   Resume          CANCELLED ⇒ AGENT_RUN_NOT_RESUMABLE（LLM = 0 / Tool = 0；§三十三 / §六十五）
7   并发            cancel 与 cancel / cancel 与 claim 竞争 ⇒ 单赢或 fail-closed（§三十六 / §五十四）
8   边界            取消逻辑不 import DB / LLM / Tool；无 force kill（§二十四 / §二十六）
```

说明：测试基建（``P7CheckpointRepository`` / ``RecordingCheckpointRecorder`` / ``_env``）
复用 ``tests/test_agent_hitl.py``。
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.app.agent import (
    AGENT_ERROR_CANCELLATION_CONFLICT,
    AGENT_ERROR_CANCEL_REQUESTED,
    AGENT_ERROR_CANCELLED,
    AGENT_ERROR_RUN_NOT_CANCELLABLE,
    AGENT_ERROR_RUN_NOT_RESUMABLE,
    AGENT_STATUS_CANCELLED,
    AGENT_STATUS_COMPLETED,
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentCancellationCoordinator,
    AgentCancellationError,
    AgentDecision,
)
from backend.app.agent.checkpoint import (
    CHECKPOINT_NEXT_ACTION_CANCELLED,
    CHECKPOINT_NEXT_ACTION_LLM_DECISION,
    CHECKPOINT_STATUS_ACTIVE,
    CHECKPOINT_STATUS_PAUSED,
    CHECKPOINT_STATUS_TERMINAL,
)
from backend.app.agent.ids import agent_run_scope, current_agent_run_id
from tests.test_agent_hitl import (
    P7CheckpointRepository,
    RecordingCheckpointRecorder,
    _env,
)
from tests.test_agent_runtime import FakeLLM, _TOOL_NAME

__all__ = [
    "TestCancellationCoordinator",
    "TestRuntimeCancellation",
    "TestCancellationResume",
    "TestCancellationBoundary",
]

_RUN_ID = "run-p7-cancel"
_TOOL_CALL_1 = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_TOOL_NAME,
    arguments={"material_code": "MAT001"},
    tool_call_id="call_c1",
)
_TOOL_CALL_2 = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_TOOL_NAME,
    arguments={"material_code": "MAT002"},
    tool_call_id="call_c2",
)
_FINAL = AgentDecision(type=DECISION_FINAL, content="库存 120 件。")


# ============================================================
# 测试基建：可注入取消信号的 LLM / Handler
# ============================================================


class _CancellingLLM:
    """在第 ``on_call`` 次 LLM 调用**期间**置位取消信号（模拟"取消到达"）。"""

    def __init__(self, inner: FakeLLM, recorder: Any, *, on_call: int = 1) -> None:
        self._inner = inner
        self._recorder = recorder
        self._on_call = on_call
        self.calls = 0

    async def complete(self, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls == self._on_call:
            self._signal()
        return await self._inner.complete(**kwargs)

    def _signal(self) -> None:
        run_id = current_agent_run_id()
        if run_id is not None:
            self._recorder.request_cancel(run_id=run_id)


class _CancellingHandler:
    """在第 ``on_call`` 次 Tool 调用**期间**置位取消信号（Tool 已开始；§二十六）。"""

    def __init__(self, recorder: Any, *, on_call: int = 2) -> None:
        self._recorder = recorder
        self._on_call = on_call
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, arguments: dict[str, Any]) -> Any:
        self.calls.append(dict(arguments))
        if len(self.calls) == self._on_call:
            run_id = current_agent_run_id()
            if run_id is not None:
                self._recorder.request_cancel(run_id=run_id)
        return {"material_code": arguments.get("material_code"), "quantity": 120}


# ============================================================
# §二十七 / §二十八 / §三十七 / §三十八 / §五十二：协调器
# ============================================================


class _LateBoundRecorder:
    """延迟绑定：handler 构造时 ``_env`` 尚未创建 recorder。"""

    def __init__(self, factory: Any) -> None:
        self._factory = factory

    def request_cancel(self, *, run_id: str) -> Any:
        return self._factory().request_cancel(run_id=run_id)


class TestCancellationCoordinator:
    def _coordinator(
        self, repository: Any, recorder: Any = None
    ) -> AgentCancellationCoordinator:
        return AgentCancellationCoordinator(
            checkpoints=(
                recorder
                if recorder is not None
                else RecordingCheckpointRecorder(repository)
            )
        )

    def _active_checkpoint(self, recorder: RecordingCheckpointRecorder) -> None:
        with agent_run_scope(_RUN_ID):
            recorder.record_decision_checkpoint(
                step_index=0,
                decision_ref=f"{_RUN_ID}:0",
                next_action=CHECKPOINT_NEXT_ACTION_LLM_DECISION,
            )

    def test_cancel_active_run_only_sets_signal(self) -> None:
        repository = P7CheckpointRepository()
        recorder = RecordingCheckpointRecorder(repository)
        self._active_checkpoint(recorder)

        result = self._coordinator(repository).cancel(_RUN_ID)

        assert result.cancel_requested is True
        assert result.finalized is False  # ACTIVE ⇒ 交给运行中的 Agent
        assert result.checkpoint_status == CHECKPOINT_STATUS_ACTIVE
        assert result.code == AGENT_ERROR_CANCEL_REQUESTED
        row = repository.rows[_RUN_ID]
        assert row.cancel_requested is True and row.cancel_requested_at is not None
        assert row.status == CHECKPOINT_STATUS_ACTIVE  # 状态不变（信号才变）
        assert row.state_version == 2  # CAS +1

    def test_cancel_is_idempotent(self) -> None:
        repository = P7CheckpointRepository()
        recorder = RecordingCheckpointRecorder(repository)
        self._active_checkpoint(recorder)
        coordinator = self._coordinator(repository)

        first = coordinator.cancel(_RUN_ID)
        second = coordinator.cancel(_RUN_ID)

        assert first.cancel_requested is True
        assert second.cancel_requested is True
        assert second.code == AGENT_ERROR_CANCEL_REQUESTED
        assert repository.rows[_RUN_ID].state_version == 2  # 第二次不再改版本

    def test_cancel_paused_run_finalizes(self) -> None:
        """PAUSED（无活着的循环）⇒ 直接终态化：TERMINAL / CANCELLED；Tool = 0。"""
        repository = P7CheckpointRepository()
        recorder = RecordingCheckpointRecorder(repository)
        with agent_run_scope(_RUN_ID):
            recorder.record_waiting_approval_checkpoint(
                step_index=0,
                decision_ref=f"{_RUN_ID}:0",
                tool_call_id="call_c1",
            )
        assert repository.rows[_RUN_ID].status == CHECKPOINT_STATUS_PAUSED

        result = self._coordinator(repository, recorder).cancel(_RUN_ID)

        assert result.finalized is True
        assert result.code == AGENT_ERROR_CANCELLED
        row = repository.rows[_RUN_ID]
        assert row.status == CHECKPOINT_STATUS_TERMINAL
        assert row.next_action == CHECKPOINT_NEXT_ACTION_CANCELLED
        assert recorder.terminal_writes == [AGENT_STATUS_CANCELLED]

    def test_cancel_terminal_run_is_noop(self) -> None:
        repository = P7CheckpointRepository()
        recorder = RecordingCheckpointRecorder(repository)
        with agent_run_scope(_RUN_ID):
            recorder.record_decision_checkpoint(
                step_index=0,
                decision_ref=f"{_RUN_ID}:0",
                next_action="COMPLETED",
            )
            recorder.record_terminal(
                run_id=_RUN_ID, agent_status=AGENT_STATUS_COMPLETED
            )
        before = repository.rows[_RUN_ID]

        result = self._coordinator(repository).cancel(_RUN_ID)

        assert result.code == AGENT_ERROR_RUN_NOT_CANCELLABLE
        assert result.finalized is False
        assert repository.rows[_RUN_ID] == before  # no-op（无任何写入）

    def test_cancel_already_cancelled_run_is_idempotent(self) -> None:
        repository = P7CheckpointRepository()
        recorder = RecordingCheckpointRecorder(repository)
        with agent_run_scope(_RUN_ID):
            recorder.record_waiting_approval_checkpoint(
                step_index=0,
                decision_ref=f"{_RUN_ID}:0",
                tool_call_id="call_c1",
            )
        coordinator = self._coordinator(repository, recorder)
        first = coordinator.cancel(_RUN_ID)
        second = coordinator.cancel(_RUN_ID)

        assert first.code == AGENT_ERROR_CANCELLED
        assert second.code == AGENT_ERROR_CANCELLED
        assert second.cancel_requested is True
        assert repository.rows[_RUN_ID].status == CHECKPOINT_STATUS_TERMINAL

    def test_cancel_unknown_run_is_not_cancellable(self) -> None:
        repository = P7CheckpointRepository()
        result = self._coordinator(repository).cancel("run-unknown")

        assert result.cancel_requested is False
        assert result.checkpoint_status is None
        assert result.code == AGENT_ERROR_RUN_NOT_CANCELLABLE

    def test_read_failure_fails_closed(self) -> None:
        repository = P7CheckpointRepository(error=RuntimeError("db down"))
        with pytest.raises(AgentCancellationError):
            self._coordinator(repository).cancel(_RUN_ID)

    def test_invalid_run_id_rejected(self) -> None:
        repository = P7CheckpointRepository()
        with pytest.raises(ValueError):
            self._coordinator(repository).cancel("")

    def test_cas_race_fails_closed_without_overwrite(self) -> None:
        """cancel 与其它控制状态转换竞争 ⇒ 不覆盖、不重试（fail-closed）。"""

        class _RacingRepository(P7CheckpointRepository):
            def request_cancel(self, *, run_id: str) -> Any:
                # 模拟"读取后、CAS 前"被其它 worker 推进版本
                current = self.rows[run_id]
                from dataclasses import replace

                self.rows[run_id] = replace(
                    current,
                    status=CHECKPOINT_STATUS_TERMINAL,
                    next_action="FAILED",
                    state_version=current.state_version + 1,
                )
                raise AgentCancellationError("raced")

        repository = _RacingRepository()
        recorder = RecordingCheckpointRecorder(repository)
        with agent_run_scope(_RUN_ID):
            recorder.record_decision_checkpoint(
                step_index=0,
                decision_ref=f"{_RUN_ID}:0",
                next_action=CHECKPOINT_NEXT_ACTION_LLM_DECISION,
            )

        result = self._coordinator(repository).cancel(_RUN_ID)

        assert result.code == AGENT_ERROR_CANCELLATION_CONFLICT
        assert result.cancel_requested is False
        assert repository.rows[_RUN_ID].cancel_requested is False  # 未置位


# ============================================================
# §二十五 / §二十九 / §三十 / §三十一 / §六十一 / §六十二：Runtime 安全边界
# ============================================================


class TestRuntimeCancellation:
    async def test_cancel_during_llm_stops_before_tool(self) -> None:
        """§六十二：LLM 返回 ⇒ 检查取消 ⇒ CANCELLED（**不**中断网络请求、不执行 Tool）。

        取消在**第二次** LLM 调用期间到达（此时 Checkpoint 已存在）。
        """
        env = _env(FakeLLM([_TOOL_CALL_1, _FINAL]), approval_required=())
        llm = _CancellingLLM(env.llm, env.checkpoint_recorder, on_call=2)
        env.runtime._llm = llm  # noqa: SLF001 —— 测试专用注入

        result = await env.runtime.run(question="查询 MAT001 库存")

        assert result.status == AGENT_STATUS_CANCELLED
        assert result.error == AGENT_ERROR_CANCEL_REQUESTED
        assert llm.calls == 2  # 第二次调用已发生（不强断）
        assert result.llm_calls == 2
        assert result.tool_calls == 1  # 第二次 Tool **未**执行（§三十：Tool = 0）
        assert [name for name, _ in env.registry.execute_calls] == [_TOOL_NAME]
        assert len(env.tool_results.rows) == 1

    async def test_cancel_during_tool_waits_and_preserves_result(self) -> None:
        """§二十六 / §三十一：Tool 已开始 ⇒ 不强杀 ⇒ 等返回 ⇒ 结果保留 ⇒ CANCELLED。"""
        handler = _CancellingHandler(
            _LateBoundRecorder(lambda: env.checkpoint_recorder), on_call=2
        )
        env = _env(
            FakeLLM([_TOOL_CALL_1, _TOOL_CALL_2, _FINAL]),
            approval_required=(),
            handler=handler,
        )

        result = await env.runtime.run(question="查询 MAT001 / MAT002 库存")

        assert result.status == AGENT_STATUS_CANCELLED
        assert result.error == AGENT_ERROR_CANCEL_REQUESTED
        assert len(handler.calls) == 2  # 运行中的 Tool 完整执行完（未强杀）
        assert result.tool_calls == 2
        assert len(env.tool_results.rows) == 2  # Tool Result 全部保留
        assert env.tool_results.rows[1].success is True
        # 取消后**没有**新的 LLM 调用（llm_calls = 决策 2 次）
        assert result.llm_calls == 2
        # Step 投影保留（Tool 步已发生）
        assert [step.type for step in result.step_records] == [
            "TOOL_CALL",
            "TOOL_CALL",
        ]

    async def test_cancel_signal_read_failure_does_not_stop_run(self) -> None:
        """取消是协作信号（非安全边界）：读取失败 ⇒ 不取消（可用性优先）。"""
        env = _env(FakeLLM([_FINAL]), approval_required=())

        def _boom(run_id: str) -> Any:
            raise RuntimeError("db down")

        env.checkpoint_recorder.get = _boom  # type: ignore[method-assign]

        result = await env.runtime.run(question="你好")

        assert result.status == AGENT_STATUS_COMPLETED

    async def test_cancelled_is_not_failed_and_keeps_result(self) -> None:
        """§三十四：CANCELLED **不**映射成 FAILED（状态逐字保留；结果不丢失）。

        第一次 Tool 执行期间取消（此时 Decision Checkpoint 已 durable ⇒ 信号可表达）：
        Tool 完整返回、Tool Result 落盘，随后在"下一轮 LLM 前"的边界停止。
        """
        holder: dict[str, Any] = {}
        handler = _CancellingHandler(
            _LateBoundRecorder(lambda: holder["env"].checkpoint_recorder), on_call=1
        )
        env = _env(
            FakeLLM([_TOOL_CALL_1, _TOOL_CALL_2, _FINAL]),
            approval_required=(),
            handler=handler,
        )
        holder["env"] = env

        result = await env.runtime.run(question="查询 MAT001 / MAT002 库存")

        assert result.status == AGENT_STATUS_CANCELLED
        assert result.error == AGENT_ERROR_CANCEL_REQUESTED
        assert result.status != "FAILED"
        assert result.tool_calls == 1 and result.llm_calls == 1
        assert len(handler.calls) == 1  # 运行中的 Tool 完整执行（未强杀）
        assert len(env.tool_results.rows) == 1  # Tool Result 保留
        assert env.checkpoints.get(result.run_id).cancel_requested is True

    async def test_cancel_before_first_decision_is_not_representable(self) -> None:
        """边界：Run 尚未写任何 Checkpoint 前的取消窗口无法表达（**已知限制**）。

        取消信号存放在 Checkpoint 行上（与 state_version 同一 CAS 域），
        因此"第一个 Decision 之前"的窗口只能由**未来**的 active-run registry /
        Read API 解决；此时``cancel_agent_run`` 返回 ``AGENT_RUN_NOT_CANCELLABLE``
        且 Run **不**受影响。
        """
        env = _env(FakeLLM([_FINAL]), approval_required=())
        repository = env.checkpoints
        coordinator = AgentCancellationCoordinator(
            checkpoints=RecordingCheckpointRecorder(repository)
        )

        result = coordinator.cancel("run-not-started")
        assert result.code == AGENT_ERROR_RUN_NOT_CANCELLABLE

        run_result = await env.runtime.run(question="你好")
        assert run_result.status == AGENT_STATUS_COMPLETED  # 未受影响


# ============================================================
# §三十三 / §六十五：CANCELLED ⇒ 禁止 Resume
# ============================================================


class TestCancellationResume:
    async def test_resume_after_cancel_is_refused(self) -> None:
        """CANCELLED 的 Run ⇒ Resume 拒绝（LLM = 0 / Tool = 0）。"""
        handler = _CancellingHandler(
            _LateBoundRecorder(lambda: env.checkpoint_recorder), on_call=2
        )
        env = _env(
            FakeLLM([_TOOL_CALL_1, _TOOL_CALL_2, _FINAL]),
            approval_required=(),
            handler=handler,
        )
        cancelled = await env.runtime.run(question="查询 MAT001 / MAT002 库存")
        assert cancelled.status == AGENT_STATUS_CANCELLED
        assert cancelled.run_id

        llm_before = env.llm.calls
        tool_before = len(env.registry.execute_calls)

        resumed = await env.runtime.resume(
            run_id=cancelled.run_id, question="查询 MAT001 / MAT002 库存"
        )

        assert resumed.status == AGENT_STATUS_CANCELLED
        assert resumed.error_code == AGENT_ERROR_RUN_NOT_RESUMABLE
        assert resumed.llm_calls == 0 and resumed.tool_calls == 0
        assert env.llm.calls == llm_before
        assert len(env.registry.execute_calls) == tool_before
        # 控制状态已终态化（CANCELLED；**不**允许 CANCELLED → ACTIVE）
        checkpoint = env.checkpoints.get(cancelled.run_id)
        assert checkpoint.status == CHECKPOINT_STATUS_TERMINAL
        assert checkpoint.next_action == CHECKPOINT_NEXT_ACTION_CANCELLED

    async def test_resume_refuses_before_any_llm_when_signal_set(self) -> None:
        """信号已置位但未终态（例如进程崩溃后的 ACTIVE）⇒ 拒绝 + 终态化。"""
        env = _env(FakeLLM([_FINAL]), approval_required=())
        with agent_run_scope(_RUN_ID):
            env.checkpoint_recorder.record_decision_checkpoint(
                step_index=0,
                decision_ref=f"{_RUN_ID}:0",
                next_action=CHECKPOINT_NEXT_ACTION_LLM_DECISION,
            )
        env.checkpoint_recorder.request_cancel(run_id=_RUN_ID)

        resumed = await env.runtime.resume(run_id=_RUN_ID, question="继续")

        assert resumed.status == AGENT_STATUS_CANCELLED
        assert resumed.error_code == AGENT_ERROR_RUN_NOT_RESUMABLE
        assert env.llm.calls == 0
        assert env.checkpoints.get(_RUN_ID).next_action == (
            CHECKPOINT_NEXT_ACTION_CANCELLED
        )

    async def test_resume_continuation_is_cancellable(self) -> None:
        """Resume 的续跑循环同样在 LLM 边界可取消（LLM 前检查）。"""
        env = _env(FakeLLM([_FINAL]), approval_required=())
        # 制造 "Decision + Tool Result + Checkpoint 已 durable" 的 crash 状态
        from tests.test_agent_resume import _seed_tool_state

        _seed_tool_state(env, run_id=_RUN_ID)

        # 续跑：LLM 期间取消 ⇒ CANCELLED（Replay 的历史 Tool **不**重跑）
        llm = _CancellingLLM(env.llm, env.checkpoint_recorder, on_call=1)
        env.runtime._llm = llm  # noqa: SLF001

        resumed = await env.runtime.resume(run_id=_RUN_ID, question="继续")

        assert resumed.status == AGENT_STATUS_CANCELLED
        assert resumed.error_code == AGENT_ERROR_CANCEL_REQUESTED
        assert env.registry.execute_calls == []  # 历史 Tool 未重跑
        assert resumed.tool_calls == 0


# ============================================================
# §二十四 / §二十六 / §二十八：取消边界审计
# ============================================================


class TestCancellationBoundary:
    def test_cancellation_module_is_pure_domain(self) -> None:
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1]
            / "backend"
            / "app"
            / "agent"
            / "cancellation.py"
        ).read_text(encoding="utf-8")
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
            assert not module.startswith("backend.app.tools"), module
        # 无 force kill / 无线程中断（§二十六：协作式）——**代码级**（AST）断言：
        # 不 import asyncio / threading / os / signal，也不调用任何 *cancel / *kill
        assert not any(module == "asyncio" for module in modules), modules
        assert not any(module == "threading" for module in modules), modules
        assert not any(module == "os" for module in modules), modules
        assert not any(module == "signal" for module in modules), modules
        called_attrs = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        for forbidden in ("cancel", "kill", "terminate", "killall"):
            assert forbidden not in called_attrs, called_attrs
        # 取消逻辑不提供 Resume / Replay 入口（§二十三）
        functions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        for forbidden in ("resume", "replay"):
            assert forbidden not in functions, functions
        assert "request_cancel" in called_attrs  # 只经 Checkpoint 的 CAS 信号

    def test_cancelled_terminal_is_not_overwritten(self) -> None:
        """CANCELLED 终态不可逆：后到的 FAILED 写入不得改写（§三十三 / §三十四）。"""
        repository = P7CheckpointRepository()
        recorder = RecordingCheckpointRecorder(repository)
        with agent_run_scope(_RUN_ID):
            recorder.record_waiting_approval_checkpoint(
                step_index=0,
                decision_ref=f"{_RUN_ID}:0",
                tool_call_id="call_c1",
            )
        AgentCancellationCoordinator(checkpoints=recorder).cancel(_RUN_ID)
        assert repository.rows[_RUN_ID].next_action == CHECKPOINT_NEXT_ACTION_CANCELLED

        recorder.record_terminal(run_id=_RUN_ID, agent_status="FAILED")

        row = repository.rows[_RUN_ID]
        assert row.status == CHECKPOINT_STATUS_TERMINAL
        assert row.next_action == CHECKPOINT_NEXT_ACTION_CANCELLED  # **未**被改写

    def test_setup_does_not_add_second_state_object(self) -> None:
        """§二十七：取消**不**新增独立状态对象（信号在 Checkpoint 行上）。"""
        from dataclasses import fields

        from backend.app.agent.checkpoint import AgentCheckpoint

        names = {field.name for field in fields(AgentCheckpoint)}
        assert "cancel_requested" in names
        assert "cancel_requested_at" in names

    def test_cancel_does_not_touch_llm_or_tool_boundaries(self) -> None:
        """Orchestrator 的取消入口不解析 Tool Call / 不执行 Tool（§四十五）。"""
        from backend.app.services.ai_orchestrator_service import AIOrchestratorService

        assert hasattr(AIOrchestratorService, "cancel_agent_run")
        assert hasattr(AIOrchestratorService, "resolve_agent_approval")
        # 取消 / 裁决是**内部 Service API**：不新增 HTTP 路由（§四十三）
        from backend.app.api import orchestrator_chat

        route_paths = {
            getattr(route, "path", "") for route in orchestrator_chat.router.routes
        }
        for forbidden in ("approval", "cancel"):
            assert not any(forbidden in path for path in route_paths), route_paths


def _tool_definition() -> Any:
    from tests.test_agent_runtime import _tool_definition as definition

    return definition()
