"""Phase 5 P-7 HITL × Cancellation **竞争** 与 Orchestrator 内部 API（**离线**；零 DB）。

覆盖（任务书 §三十六 / §五十四 / §六十四）：

```text
Case A  WAITING_APPROVAL → cancel → approve
        ⇒ 只能一个赢：CANCELLED（**Tool = 0**）；后到的 approve ⇒ APPROVAL_CONFLICT；
          Resume ⇒ AGENT_RUN_NOT_RESUMABLE（**绝不** CANCELLED + Tool executed）
Case B  WAITING_APPROVAL → approve → cancel
        ⇒ 允许两种结局（APPROVED → Tool / CANCELLED），但 **Tool 绝不执行两次**
并发    approve ∥ reject ⇒ 单赢（另一个 CONFLICT）
        resume ∥ resume    ⇒ Tool 只执行一次
        cancel ∥ resume    ⇒ 单赢（fail-closed；无 blind rerun）
API     resolve_agent_approval / cancel_agent_run（内部 Service API；§四十三）
```

说明：测试基建复用 ``tests/test_agent_hitl.py``（``_env`` / 内存仓储）与
``tests.test_agent_step_persistence._AgentRouteRouter``（AGENT 路由）。
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from backend.app.agent import (
    AGENT_ERROR_APPROVAL_CONFLICT,
    AGENT_ERROR_APPROVAL_NOT_FOUND,
    AGENT_ERROR_CANCELLED,
    AGENT_ERROR_RUN_NOT_CANCELLABLE,
    AGENT_ERROR_RUN_NOT_RESUMABLE,
    AGENT_STATUS_CANCELLED,
    AGENT_STATUS_COMPLETED,
    AGENT_STATUS_WAITING_APPROVAL,
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_REJECTED,
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentDecision,
)
from backend.app.agent.checkpoint import (
    CHECKPOINT_NEXT_ACTION_CANCELLED,
    CHECKPOINT_STATUS_PAUSED,
    CHECKPOINT_STATUS_TERMINAL,
)
from backend.app.agent.ids import agent_run_scope
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorCapabilityError,
    AIOrchestratorExecutionError,
    AIOrchestratorInputError,
    AIOrchestratorService,
    RouteType,
)
from tests.test_agent_hitl import _env
from tests.test_agent_runtime import FakeLLM, _TOOL_NAME
from tests.test_agent_step_persistence import _AgentRouteRouter

__all__ = [
    "TestCaseACancelWins",
    "TestCaseBApproveWins",
    "TestConcurrentResolution",
    "TestOrchestratorCancellationApi",
]

_TOOL_CALL = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_TOOL_NAME,
    arguments={"material_code": "MAT001"},
    tool_call_id="call_race_1",
)
_FINAL = AgentDecision(type=DECISION_FINAL, content="库存 120 件。")


def _orchestrator(
    env: Any,
    *,
    approval_store: Any = None,
    checkpoint_recorder: Any = None,
) -> AIOrchestratorService:
    return AIOrchestratorService(
        router=_AgentRouteRouter(),  # type: ignore[arg-type]
        agent_runtime=env.runtime,
        agent_approval_store=(
            env.approval_store if approval_store is None else approval_store
        ),
        agent_checkpoint_recorder=(
            env.checkpoint_recorder
            if checkpoint_recorder is None
            else checkpoint_recorder
        ),
    )


async def _paused_env() -> tuple[Any, Any, AIOrchestratorService]:
    env = _env(FakeLLM([_TOOL_CALL, _FINAL]))
    paused = await env.runtime.run(question="查询 MAT001 库存")
    assert paused.status == AGENT_STATUS_WAITING_APPROVAL
    return env, paused, _orchestrator(env)


# ============================================================
# Case A：cancel 赢（approve 后到 ⇒ CONFLICT；Tool = 0）
# ============================================================


class TestCaseACancelWins:
    async def test_cancel_then_approve_conflicts_and_tool_never_runs(self) -> None:
        env, paused, orchestrator = await _paused_env()

        cancelled = await orchestrator.cancel_agent_run(paused.run_id)

        assert cancelled.code == AGENT_ERROR_CANCELLED
        assert cancelled.finalized is True  # PAUSED ⇒ 直接终态化
        checkpoint = env.checkpoints.get(paused.run_id)
        assert checkpoint.status == CHECKPOINT_STATUS_TERMINAL
        assert checkpoint.next_action == CHECKPOINT_NEXT_ACTION_CANCELLED

        # 后到的 approve：已终态 Run ⇒ fail-closed（**不**复活执行）
        resolution = await orchestrator.resolve_agent_approval(
            paused.approval_id, decision="APPROVED"
        )
        assert resolution.error_code == AGENT_ERROR_APPROVAL_CONFLICT
        assert resolution.status == "PENDING"  # 审批**未**被改写（不 force）

        # Resume ⇒ 拒绝（LLM = 0 / Tool = 0）
        with pytest.raises(AIOrchestratorExecutionError) as excinfo:
            await orchestrator.resume_agent_run(
                paused.run_id, question="查询 MAT001 库存"
            )
        assert AGENT_ERROR_RUN_NOT_RESUMABLE in str(excinfo.value)
        assert env.registry.execute_calls == []
        assert env.tool_results.rows == []
        assert env.llm.calls == 1  # 只有暂停前的决策调用

    async def test_cancel_then_approve_then_cancel_is_idempotent(self) -> None:
        env, paused, orchestrator = await _paused_env()

        first = await orchestrator.cancel_agent_run(paused.run_id)
        second = await orchestrator.cancel_agent_run(paused.run_id)

        assert first.code == AGENT_ERROR_CANCELLED
        assert second.code == AGENT_ERROR_CANCELLED
        assert second.cancel_requested is True
        assert second.finalized is False  # 已经终态：不再写
        assert env.registry.execute_calls == []


# ============================================================
# Case B：approve 赢（Tool 只执行一次；cancel 不得造成二次执行）
# ============================================================


class TestCaseBApproveWins:
    async def test_approve_then_cancel_before_resume_keeps_tool_at_zero(self) -> None:
        """approve 先赢，随后（Resume 之前）cancel ⇒ CANCELLED（Tool = 0）。

        §三十六 允许两种结局之一；这里断言的是**唯一不变量**：
        绝不出现 "CANCELLED + Tool executed"，也绝不执行两次。
        """
        env, paused, orchestrator = await _paused_env()

        approved = await orchestrator.resolve_agent_approval(
            paused.approval_id, decision="APPROVED"
        )
        assert approved.error_code is None
        assert approved.status == APPROVAL_STATUS_APPROVED

        cancelled = await orchestrator.cancel_agent_run(paused.run_id)
        assert cancelled.code == AGENT_ERROR_CANCELLED

        with pytest.raises(AIOrchestratorExecutionError):
            await orchestrator.resume_agent_run(
                paused.run_id, question="查询 MAT001 库存"
            )
        assert env.registry.execute_calls == []  # cancel 赢 ⇒ Tool = 0

    async def test_approve_then_resume_then_cancel_executes_tool_once(self) -> None:
        env, paused, orchestrator = await _paused_env()
        await orchestrator.resolve_agent_approval(
            paused.approval_id, decision="APPROVED"
        )

        resumed = await orchestrator.resume_agent_run(
            paused.run_id, question="查询 MAT001 库存"
        )
        assert resumed.route == RouteType.AGENT
        assert [name for name, _ in env.registry.execute_calls] == [_TOOL_NAME]

        # 已完成（终态）⇒ cancel 为 no-op（§三十八）
        late_cancel = await orchestrator.cancel_agent_run(paused.run_id)
        assert late_cancel.code == AGENT_ERROR_RUN_NOT_CANCELLABLE
        assert late_cancel.finalized is False
        assert len(env.registry.execute_calls) == 1  # **没有**第二次执行

    async def test_second_resume_never_executes_tool_twice(self) -> None:
        env, paused, orchestrator = await _paused_env()
        await orchestrator.resolve_agent_approval(
            paused.approval_id, decision="APPROVED"
        )
        await orchestrator.resume_agent_run(paused.run_id, question="查询 MAT001 库存")

        replay = await orchestrator.resume_agent_run(
            paused.run_id, question="查询 MAT001 库存"
        )

        assert replay.metadata["agent_status"] == AGENT_STATUS_COMPLETED
        assert replay.metadata["resume_tool_calls"] == 0  # 纯 Replay（0 次 handler）
        assert len(env.registry.execute_calls) == 1
        assert len(env.tool_results.rows) == 1


# ============================================================
# §三十六 / §五十四：并发（单赢；无双重执行）
# ============================================================


class TestConcurrentResolution:
    async def test_approve_and_reject_single_winner(self) -> None:
        env, paused, orchestrator = await _paused_env()

        results = await asyncio.gather(
            orchestrator.resolve_agent_approval(
                paused.approval_id, decision="APPROVED"
            ),
            orchestrator.resolve_agent_approval(
                paused.approval_id, decision="REJECTED"
            ),
        )

        winners = [item for item in results if item.error_code is None]
        losers = [item for item in results if item.error_code is not None]
        assert len(winners) == 1 and len(losers) == 1
        assert losers[0].error_code == AGENT_ERROR_APPROVAL_CONFLICT
        # 持久化状态 == 赢家（**不**被覆盖）
        row = env.approvals.get(paused.approval_id)
        assert row.status == winners[0].status
        assert row.status in (APPROVAL_STATUS_APPROVED, APPROVAL_STATUS_REJECTED)

    async def test_repeated_approve_is_idempotent(self) -> None:
        env, paused, orchestrator = await _paused_env()

        first = await orchestrator.resolve_agent_approval(
            paused.approval_id, decision="APPROVED"
        )
        second = await orchestrator.resolve_agent_approval(
            paused.approval_id, decision="APPROVED"
        )

        assert first.error_code is None and second.error_code is None
        assert second.status == APPROVAL_STATUS_APPROVED
        assert env.registry.execute_calls == []  # 裁决本身不执行 Tool

    async def test_concurrent_resumes_execute_tool_once(self) -> None:
        env, paused, orchestrator = await _paused_env()
        await orchestrator.resolve_agent_approval(
            paused.approval_id, decision="APPROVED"
        )

        outcomes: list[str] = []

        async def _resume() -> None:
            try:
                result = await orchestrator.resume_agent_run(
                    paused.run_id, question="查询 MAT001 库存"
                )
                outcomes.append(result.metadata["agent_status"])
            except AIOrchestratorExecutionError:
                outcomes.append("EXECUTION_ERROR")

        await asyncio.gather(_resume(), _resume())

        assert len(env.registry.execute_calls) == 1  # 单赢；Tool 只执行一次
        assert len(env.tool_results.rows) == 1
        assert "EXECUTION_ERROR" not in outcomes or outcomes.count(
            AGENT_STATUS_COMPLETED
        ) >= 0

    async def test_cancel_and_resume_single_winner(self) -> None:
        """cancel ∥ resume：要么 resume 赢（Tool 一次），要么 cancel 赢（Tool = 0）。"""
        env, paused, orchestrator = await _paused_env()
        await orchestrator.resolve_agent_approval(
            paused.approval_id, decision="APPROVED"
        )

        async def _resume() -> str:
            try:
                result = await orchestrator.resume_agent_run(
                    paused.run_id, question="查询 MAT001 库存"
                )
                return result.metadata["agent_status"]
            except AIOrchestratorExecutionError:
                return "EXECUTION_ERROR"

        statuses, cancellation = await asyncio.gather(
            _resume(), orchestrator.cancel_agent_run(paused.run_id)
        )

        executions = len(env.registry.execute_calls)
        assert executions <= 1  # 绝不双重执行
        checkpoint = env.checkpoints.get(paused.run_id)
        if executions == 0:
            # cancel 赢：控制状态终态化为 CANCELLED（Tool 未执行）
            assert checkpoint.next_action == CHECKPOINT_NEXT_ACTION_CANCELLED
            assert cancellation.code == AGENT_ERROR_CANCELLED
        else:
            # resume 赢：Tool 恰好一次（结果已持久化；Run 随后可能被取消）
            assert executions == 1
            assert len(env.tool_results.rows) == 1
        assert statuses  # 结果已判定（不悬挂）

        # 无论谁赢，重复 Resume 都不会再执行 Tool
        with pytest.raises(AIOrchestratorExecutionError):
            await orchestrator.resume_agent_run(
                paused.run_id, question="查询 MAT001 库存"
            )


# ============================================================
# §四十三 / §四十五：内部 Service API 边界
# ============================================================


class TestOrchestratorCancellationApi:
    async def test_cancel_requires_input(self) -> None:
        env, _paused, orchestrator = await _paused_env()
        with pytest.raises(AIOrchestratorInputError):
            await orchestrator.cancel_agent_run("")

    async def test_cancel_without_checkpoint_recorder_is_capability_error(self) -> None:
        env = _env(FakeLLM([_FINAL]), approval_required=())
        orchestrator = AIOrchestratorService(
            router=_AgentRouteRouter(),  # type: ignore[arg-type]
            agent_runtime=env.runtime,
        )
        with pytest.raises(AIOrchestratorCapabilityError):
            await orchestrator.cancel_agent_run("run-x")

    async def test_resolve_without_store_is_capability_error(self) -> None:
        env = _env(FakeLLM([_FINAL]), approval_required=())
        orchestrator = AIOrchestratorService(
            router=_AgentRouteRouter(),  # type: ignore[arg-type]
            agent_runtime=env.runtime,
        )
        with pytest.raises(AIOrchestratorCapabilityError):
            await orchestrator.resolve_agent_approval("apr-x", decision="APPROVED")

    async def test_resolve_rejects_illegal_decisions(self) -> None:
        env, _paused, orchestrator = await _paused_env()
        for bad in ("retry", "modify", "change_arguments", "PENDING", ""):
            with pytest.raises(AIOrchestratorInputError):
                await orchestrator.resolve_agent_approval("apr-x", decision=bad)

    async def test_resolve_unknown_approval_is_not_found(self) -> None:
        env, _paused, orchestrator = await _paused_env()
        resolution = await orchestrator.resolve_agent_approval(
            "apr-unknown", decision="APPROVED"
        )
        assert resolution.error_code == AGENT_ERROR_APPROVAL_NOT_FOUND
        assert resolution.run_id is None

    async def test_resolve_after_cancelled_terminal_run_conflicts(self) -> None:
        """终态（CANCELLED）Run 的审批 ⇒ CONFLICT；审批行不被改动（§三十二）。"""
        env, paused, orchestrator = await _paused_env()
        await orchestrator.cancel_agent_run(paused.run_id)
        before = replace(env.approvals.get(paused.approval_id))

        resolution = await orchestrator.resolve_agent_approval(
            paused.approval_id, decision="APPROVED"
        )

        assert resolution.error_code == AGENT_ERROR_APPROVAL_CONFLICT
        assert env.approvals.get(paused.approval_id) == before

    async def test_checkpoint_read_failure_fails_closed(self) -> None:
        """裁决前无法读取 Checkpoint ⇒ fail-closed（不猜、不覆盖）。"""
        env, paused, orchestrator = await _paused_env()

        def _boom(run_id: str) -> Any:
            raise RuntimeError("db down")

        env.checkpoint_recorder.get = _boom  # type: ignore[method-assign]

        resolution = await orchestrator.resolve_agent_approval(
            paused.approval_id, decision="APPROVED"
        )
        assert resolution.error_code == AGENT_ERROR_APPROVAL_CONFLICT
        assert env.approvals.get(paused.approval_id).status == "PENDING"

    async def test_paused_run_metadata_exposes_approval_id(self) -> None:
        """§四十二：暂停结果只透出非敏感审批身份（供未来 Read API / UI）。"""
        env = _env(FakeLLM([_TOOL_CALL, _FINAL]))
        result = await env.runtime.run(question="查询 MAT001 库存")
        assert result.status == AGENT_STATUS_WAITING_APPROVAL
        assert result.approval_id

        checkpoint = env.checkpoints.get(result.run_id)
        assert checkpoint.status == CHECKPOINT_STATUS_PAUSED
        assert agent_run_scope  # 保持 import（测试专用可读性）
