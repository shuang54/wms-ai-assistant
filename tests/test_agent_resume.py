"""Phase 5 P-6 Agent Resume / Replay（**离线**；Fake LLM / Fake Tool，零 DB）。

覆盖（任务书 §十四–§二十七 / §三十三–§五十六）：

```text
Decision 写点     Decision 先落 Store（fail-closed），再执行 Tool（§十三 / §十四）
Checkpoint ref    Decision → Checkpoint.pending_decision_ref 一致（§十五 / §四十五）
Resume FINAL      replay ⇒ 返回历史内容；LLM = 0 / Tool = 0（§四十六）
Resume TOOL_CALL  replay Decision + Tool Result ⇒ 新 LLM（历史 handler = 0；§四十七）
Replay failure    Tool Result success=false ⇒ FAILED（不重试；§四十八 / §二十二）
Fail closed       Decision 缺失 / 不一致 / Tool Result 缺失 / 不可用（§十七 / §十八 /
                  §二十三 / §五十六）——**绝不**盲目重跑 Tool（READ_ONLY 也不行）
并发              state_version + claim_token ⇒ 单赢（§三十五 / §五十二）
终态              TERMINAL ⇒ 历史结果（LLM = 0 / Tool = 0；§十九 / §三十六 / §三十七）
身份              run_id 不变（§二十七）；Resume 内 LLM/Tool 计数只算真实调用（§四十一）
安全              Replay 不把敏感 Tool Result 写进 Decision / Checkpoint / Step（§五十五）
```
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

import pytest

from backend.app.agent import (
    AGENT_STATUS_COMPLETED,
    AGENT_STATUS_FAILED,
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentDecision,
    AgentRuntime,
    AgentRuntimePolicy,
)
from backend.app.agent.checkpoint import (
    CHECKPOINT_NEXT_ACTION_COMPLETED,
    CHECKPOINT_NEXT_ACTION_LLM_DECISION,
    CHECKPOINT_NEXT_ACTION_TOOL_EXECUTION,
    CHECKPOINT_STATUS_ACTIVE,
    CHECKPOINT_STATUS_TERMINAL,
    BestEffortAgentCheckpointRecorder,
)
from backend.app.agent.decision_store import AgentDecisionStore
from backend.app.agent.errors import (
    AGENT_ERROR_DECISION_PERSISTENCE_FAILURE,
    AGENT_ERROR_RESUME_CONFLICT,
    AGENT_ERROR_CHECKPOINT_DECISION_MISMATCH,
    AGENT_ERROR_CHECKPOINT_NOT_FOUND,
    AGENT_ERROR_DECISION_NOT_FOUND,
    AGENT_ERROR_DECISION_UNAVAILABLE,
    AGENT_ERROR_FINAL_RESULT_UNAVAILABLE,
    AGENT_ERROR_TOOL_RESULT_NOT_FOUND,
    AGENT_ERROR_TOOL_RESULT_UNAVAILABLE,
)
from backend.app.agent.ids import agent_run_scope
from backend.app.agent.recorder import (
    AgentToolResultWritePoint,
    BestEffortAgentToolResultRecorder,
)
from backend.app.tools.base import ToolResult
from tests.test_agent_checkpoint import FakeAgentCheckpointRepository
from tests.test_agent_decision_store import FakeAgentDecisionRepository
from tests.test_agent_runtime import FakeLLM, _execution_service
from tests.test_agent_tool_result_store import FakeAgentToolResultRepository

__all__ = [
    "TestDecisionWritePoint",
    "TestResumeFinal",
    "TestResumeToolCall",
    "TestResumeFailClosed",
    "TestResumeConcurrency",
    "TestResumeTerminal",
    "TestResumeSecurity",
]

_TOOL_NAME = "get_inventory"
_RUN_ID = "run-p6-resume"
_TOOL_CALL = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_TOOL_NAME,
    arguments={"material_code": "MAT001"},
    tool_call_id="call_resume_1",
)
_FINAL = AgentDecision(type=DECISION_FINAL, content="库存 120 件。")


class RecordingCheckpointRecorder:
    """记录 Checkpoint 写点序列（委托真实 best-effort 记录器）。"""

    def __init__(self, repository: Any) -> None:
        self._inner = BestEffortAgentCheckpointRecorder(
            repository, claim_token_factory=lambda: "claim-p6"
        )
        self.decision_writes: list[tuple[int, str, str]] = []
        self.tool_result_writes: list[tuple[int, bool]] = []
        self.terminal_writes: list[str] = []

    # ---- 写（Runtime 端口） ----
    def record_decision_checkpoint(
        self,
        *,
        step_index: int,
        decision_ref: str,
        next_action: str,
        tool_call_id: str | None = None,
    ) -> Any:
        self.decision_writes.append((step_index, decision_ref, next_action))
        return self._inner.record_decision_checkpoint(
            step_index=step_index,
            decision_ref=decision_ref,
            next_action=next_action,
            tool_call_id=tool_call_id,
        )

    async def arecord_decision_checkpoint(self, **kwargs: Any) -> Any:
        return self.record_decision_checkpoint(**kwargs)

    def record_tool_result_checkpoint(
        self, *, step_index: int, tool_result_persisted: bool
    ) -> Any:
        self.tool_result_writes.append((step_index, tool_result_persisted))
        return self._inner.record_tool_result_checkpoint(
            step_index=step_index, tool_result_persisted=tool_result_persisted
        )

    async def arecord_tool_result_checkpoint(self, **kwargs: Any) -> Any:
        return self.record_tool_result_checkpoint(**kwargs)

    # ---- 读 / 声明（Resume 端口） ----
    def get(self, run_id: str) -> Any:
        return self._inner.get(run_id)

    def claim_for_resume(
        self, *, run_id: str, expected_state_version: int, claim_token: str
    ) -> Any:
        return self._inner.claim_for_resume(
            run_id=run_id,
            expected_state_version=expected_state_version,
            claim_token=claim_token,
        )

    def record_terminal(self, **kwargs: Any) -> Any:
        self.terminal_writes.append(str(kwargs.get("agent_status")))
        return self._inner.record_terminal(**kwargs)

    async def arecord_terminal(self, **kwargs: Any) -> Any:
        return self.record_terminal(**kwargs)


@dataclass
class _Env:
    """测试环境：真实 Runtime + 真实端口实现（仓储为内存 Fake）。"""

    runtime: AgentRuntime
    llm: FakeLLM
    handler: Any
    registry: Any
    checkpoints: FakeAgentCheckpointRepository
    checkpoint_recorder: RecordingCheckpointRecorder
    decisions: FakeAgentDecisionRepository
    decision_store: AgentDecisionStore
    tool_results: FakeAgentToolResultRepository
    tool_result_writer: AgentToolResultWritePoint


def _env(
    llm: FakeLLM,
    *,
    decision_store: Any = None,
    checkpoint_recorder: Any = None,
) -> _Env:
    service, registry, handler = _execution_service()
    checkpoints = FakeAgentCheckpointRepository()
    recorder = checkpoint_recorder or RecordingCheckpointRecorder(checkpoints)
    decisions = FakeAgentDecisionRepository()
    store = decision_store if decision_store is not None else AgentDecisionStore(decisions)
    tool_results = FakeAgentToolResultRepository()
    runtime = AgentRuntime(
        llm=llm,
        tool_execution=service,
        policy=AgentRuntimePolicy(
            max_execution_time_ms=30_000, max_cost_tokens=1_000
        ),
        allowed_tools=(_TOOL_NAME,),
        tool_result_sink=AgentToolResultWritePoint(
            recorder=BestEffortAgentToolResultRecorder(repository=tool_results)
        ),
        checkpoint_sink=recorder,
        checkpoint_store=recorder,
        decision_store=store,
        tool_result_store=tool_results,
    )
    return _Env(
        runtime=runtime,
        llm=llm,
        handler=handler,
        registry=registry,
        checkpoints=checkpoints,
        checkpoint_recorder=recorder,
        decisions=decisions,
        decision_store=store,
        tool_results=tool_results,
        tool_result_writer=runtime.tool_result_sink,
    )


def _seed_tool_state(
    env: _Env,
    *,
    run_id: str = _RUN_ID,
    step_index: int = 0,
    success: bool = True,
    data: Any = None,
    repository: Any = None,
) -> None:
    """构造 "Decision 已落 + Tool Result 已 durable + Checkpoint ACTIVE" 的 crash 状态。"""
    decision = AgentDecision(
        type=DECISION_TOOL_CALL,
        tool_name=_TOOL_NAME,
        arguments={"material_code": "MAT001"},
        tool_call_id="call_seed_1",
    )
    env.decision_store.save(run_id=run_id, step_index=step_index, decision=decision)
    with agent_run_scope(run_id):
        env.tool_result_writer.record_tool_result(
            step_index=step_index,
            tool_name=_TOOL_NAME,
            result=ToolResult(
                tool_name=_TOOL_NAME,
                success=success,
                data=(
                    (data if data is not None else {"material_code": "MAT001", "quantity": 120})
                    if success
                    else None
                ),
                error=None if success else "boom",
            ),
        )
    # 生产同款控制状态：ACTIVE / TOOL_EXECUTION + pending_decision_ref（§十三）
    recorder = env.checkpoint_recorder
    if repository is not None:
        recorder = BestEffortAgentCheckpointRecorder(
            repository, claim_token_factory=lambda: "claim-p6"
        )
    with agent_run_scope(run_id):
        recorder.record_decision_checkpoint(
            step_index=step_index,
            decision_ref=f"{run_id}:{step_index}",
            next_action=CHECKPOINT_NEXT_ACTION_TOOL_EXECUTION,
            tool_call_id="call_seed_1",
        )


# ============================================================
# §十三 / §十四 / §十五 / §四十五：Decision 写点与 fail-closed
# ============================================================


class TestDecisionWritePoint:
    async def test_decisions_persist_before_tool_execution(self) -> None:
        """§十三：Decision 先落 Store（再执行 Tool）；两次决策 ⇒ 两行 Decision。"""
        env = _env(FakeLLM([_TOOL_CALL, _FINAL]))

        result = await env.runtime.run(question="查询 MAT001 库存")

        assert result.status == AGENT_STATUS_COMPLETED
        first = env.decisions.get(result.run_id, 0)
        second = env.decisions.get(result.run_id, 1)
        assert first is not None and first.decision_type == DECISION_TOOL_CALL
        assert second is not None and second.decision_type == DECISION_FINAL
        # 写点顺序：Decision(step0) → Tool Result(step0) → Decision(step1)
        assert env.checkpoint_recorder.decision_writes == [
            (0, f"{result.run_id}:0", CHECKPOINT_NEXT_ACTION_TOOL_EXECUTION),
            (1, f"{result.run_id}:1", CHECKPOINT_NEXT_ACTION_COMPLETED),
        ]
        assert env.checkpoint_recorder.tool_result_writes == [(0, True)]
        # §十五：Checkpoint 的 pending_decision_ref 真被使用（终态决策的 ref）
        checkpoint = env.checkpoints.rows[result.run_id]
        assert checkpoint.pending_decision_ref == f"{result.run_id}:1"

    async def test_decision_store_failure_is_fail_closed(self) -> None:
        """§十四：Decision Store 失败 ⇒ FAILED，**不执行 Tool**、不再调 LLM。"""
        env = _env(
            FakeLLM([_TOOL_CALL, _FINAL]),
            decision_store=AgentDecisionStore(
                FakeAgentDecisionRepository(error=RuntimeError("db down"))
            ),
        )

        result = await env.runtime.run(question="查询 MAT001 库存")

        assert result.status == AGENT_STATUS_FAILED
        assert result.error == AGENT_ERROR_DECISION_PERSISTENCE_FAILURE
        assert env.handler.calls == []  # Tool 0 次执行
        assert env.registry.execute_calls == []
        assert env.llm.calls == 1  # 不再调 LLM
        assert env.tool_results.rows == []

    async def test_unwired_decision_store_keeps_previous_behaviour(self) -> None:
        """未接线 Decision Store ⇒ 不做 Decision 持久化（P-6 之前行为不变）。"""
        service, _registry, _handler = _execution_service()
        runtime = AgentRuntime(
            llm=FakeLLM([_TOOL_CALL, _FINAL]),
            tool_execution=service,
            policy=AgentRuntimePolicy(
                max_execution_time_ms=30_000, max_cost_tokens=1_000
            ),
            allowed_tools=(_TOOL_NAME,),
        )

        result = await runtime.run(question="查询 MAT001 库存")

        assert result.status == AGENT_STATUS_COMPLETED
        assert runtime.decision_store is None
        # 端口未接线 ⇒ resume 拒绝（AgentPolicyError 继承 ValueError）
        with pytest.raises(ValueError):
            await runtime.resume(run_id=_RUN_ID, question="查询库存")


# ============================================================
# §四十六：Resume FINAL
# ============================================================


class TestResumeFinal:
    async def test_final_decision_is_replayed_without_llm_or_tool(self) -> None:
        env = _env(FakeLLM([_FINAL]))

        result = await env.runtime.run(question="库存多少")
        run_id = result.run_id
        assert env.llm.calls == 1  # 原始 Run 的 LLM 调用

        resume = await env.runtime.resume(run_id=run_id, question="库存多少")

        assert resume.run_id == run_id  # §二十七：run_id 不变
        assert resume.status == AGENT_STATUS_COMPLETED
        assert resume.content == "库存 120 件。"
        assert resume.replayed_step_index == 0
        assert resume.llm_calls == 0 and resume.tool_calls == 0
        assert env.llm.calls == 1  # **没有**新的 LLM 调用（§十六 / §四十一）
        assert env.handler.calls == []  # 无 Tool
        assert env.checkpoints.rows[run_id].state_version == 2  # Resume claim

    async def test_missing_checkpoint_is_fail_closed(self) -> None:
        env = _env(FakeLLM([_FINAL]))

        resume = await env.runtime.resume(run_id="run-unknown", question="?")

        assert resume.status == AGENT_STATUS_FAILED
        assert resume.error_code == AGENT_ERROR_CHECKPOINT_NOT_FOUND
        assert env.llm.calls == 0 and env.handler.calls == []


# ============================================================
# §四十七 / §四十八：Resume TOOL_CALL
# ============================================================


class TestResumeToolCall:
    async def test_tool_result_is_replayed_then_llm_continues(self) -> None:
        """Decision + Tool Result 都 Replay；历史 handler 0 次；新增 LLM 1 次。"""
        env = _env(FakeLLM([_FINAL]))
        _seed_tool_state(env)

        resume = await env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存")

        assert resume.status == AGENT_STATUS_COMPLETED
        assert resume.content == "库存 120 件。"
        assert resume.replayed_step_index == 0
        assert resume.llm_calls == 1  # 只有续跑的**新** LLM 调用
        assert resume.tool_calls == 0
        assert env.llm.calls == 1
        assert env.handler.calls == []  # §二十一：handler **0** 次（不重跑）
        assert env.registry.execute_calls == []

    async def test_replayed_tool_result_keeps_untrusted_marker(self) -> None:
        env = _env(FakeLLM([_FINAL]))
        _seed_tool_state(env)

        await env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存")

        messages = env.llm.messages[0]
        tool_messages = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert "【TOOL RESULT — UNTRUSTED DATA】" in tool_messages[0]["content"]
        assistant = [m for m in messages if m.get("tool_calls")]
        assert assistant[0]["tool_calls"][0]["id"] == "call_seed_1"
        assert assistant[0]["tool_calls"][0]["function"]["name"] == _TOOL_NAME

    async def test_failure_result_is_replayed_as_failed(self) -> None:
        """§四十八：success=false ⇒ Replay failure ⇒ FAILED；不重试、不调 LLM。"""
        env = _env(FakeLLM([_FINAL]))
        _seed_tool_state(env, success=False)

        resume = await env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存")

        assert resume.status == AGENT_STATUS_FAILED
        assert resume.error_code == "TOOL_EXECUTION_FAILURE"
        assert resume.llm_calls == 0 and resume.tool_calls == 0
        assert env.llm.calls == 0  # 无新 LLM
        assert env.handler.calls == []  # 无 Tool 重跑

    async def test_no_pending_decision_continues_with_new_llm(self) -> None:
        """Checkpoint ACTIVE / LLM_DECISION（无 ref）⇒ 下一步 = 新的 LLM 调用。"""
        env = _env(FakeLLM([_FINAL]))
        # 构造 "Tool 已完成、控制状态 = LLM_DECISION、第二步尚未发生" 的 crash 态
        env.decision_store.save(run_id=_RUN_ID, step_index=0, decision=_TOOL_CALL)
        with agent_run_scope(_RUN_ID):
            env.tool_result_writer.record_tool_result(
                step_index=0,
                tool_name=_TOOL_NAME,
                result=ToolResult(
                    tool_name=_TOOL_NAME,
                    success=True,
                    data={"material_code": "MAT001", "quantity": 120},
                ),
            )
            env.checkpoint_recorder.record_tool_result_checkpoint(
                step_index=0, tool_result_persisted=True
            )

        checkpoint = env.checkpoints.rows[_RUN_ID]
        assert checkpoint.next_action == CHECKPOINT_NEXT_ACTION_LLM_DECISION
        assert checkpoint.pending_decision_ref is None
        assert checkpoint.pending_step_index == 1

        resume = await env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存")

        assert resume.status == AGENT_STATUS_COMPLETED
        assert resume.replayed_step_index is None  # 没有 Decision 被 Replay
        assert resume.llm_calls == 1  # 只有新的 LLM 调用
        assert resume.tool_calls == 0
        assert env.handler.calls == []  # Tool **未**重跑
        assert env.decisions.get(_RUN_ID, 1) is not None  # 新 Decision 属于新 step
        assert env.decisions.get(_RUN_ID, 0) is not None  # 历史 Decision 未被覆盖


# ============================================================
# §十七 / §十八 / §二十三 / §五十六：Fail Closed
# ============================================================


class TestResumeFailClosed:
    async def test_missing_decision_is_fail_closed(self) -> None:
        """§四十九：pending_decision_ref 指向的 Decision 不存在 ⇒ FAILED。"""
        env = _env(FakeLLM([_FINAL]))
        _seed_tool_state(env)
        env.decisions.rows.clear()  # 模拟 Decision Store 丢失

        resume = await env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存")

        assert resume.status == AGENT_STATUS_FAILED
        assert resume.error_code == AGENT_ERROR_DECISION_NOT_FOUND
        assert resume.llm_calls == 0 and resume.tool_calls == 0
        assert env.llm.calls == 0 and env.handler.calls == []

    async def test_unavailable_decision_is_fail_closed(self) -> None:
        env = _env(FakeLLM([_FINAL]))
        oversized = AgentDecision(
            type=DECISION_TOOL_CALL,
            tool_name=_TOOL_NAME,
            arguments={"blob": "x" * (32 * 1024 + 64)},
            tool_call_id="call-big",
        )
        env.decision_store.save(run_id=_RUN_ID, step_index=0, decision=oversized)
        with agent_run_scope(_RUN_ID):
            env.tool_result_writer.record_tool_result(
                step_index=0,
                tool_name=_TOOL_NAME,
                result=ToolResult(
                    tool_name=_TOOL_NAME, success=True, data={"qty": 1}
                ),
            )
        with agent_run_scope(_RUN_ID):
            env.checkpoint_recorder.record_decision_checkpoint(
                step_index=0,
                decision_ref=f"{_RUN_ID}:0",
                next_action=CHECKPOINT_NEXT_ACTION_TOOL_EXECUTION,
                tool_call_id="call-big",
            )

        resume = await env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存")

        assert resume.error_code == AGENT_ERROR_DECISION_UNAVAILABLE
        assert env.llm.calls == 0 and env.handler.calls == []

    async def test_missing_tool_result_is_not_rerun(self) -> None:
        """§五十 / §五十六：Tool Result 缺失 ⇒ FAILED；**绝不**重跑（READ_ONLY 也不行）。"""
        env = _env(FakeLLM([_FINAL]))
        _seed_tool_state(env)
        env.tool_results.rows.clear()  # 模拟 Result 未持久化（状态 UNKNOWN）

        resume = await env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存")

        assert resume.status == AGENT_STATUS_FAILED
        assert resume.error_code == AGENT_ERROR_TOOL_RESULT_NOT_FOUND
        assert env.handler.calls == []  # 关键：没有盲目重跑 Tool
        assert env.registry.execute_calls == []
        assert env.llm.calls == 0

    async def test_tool_result_step_mismatch_is_fail_closed(self) -> None:
        """结果存在但属于其它 step ⇒ 视为"没有可用结果"（fail-closed）。"""
        env = _env(FakeLLM([_FINAL]))
        _seed_tool_state(env)
        env.tool_results.rows[0].step_index = 5  # 指向其它 step

        resume = await env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存")

        assert resume.error_code == AGENT_ERROR_TOOL_RESULT_NOT_FOUND
        assert env.handler.calls == []

    async def test_decision_run_mismatch_is_fail_closed(self) -> None:
        """§五十一：decision.run_id != checkpoint.run_id ⇒ MISMATCH。"""
        env = _env(FakeLLM([_FINAL]))
        _seed_tool_state(env)
        env.checkpoints.rows[_RUN_ID] = replace(
            env.checkpoints.rows[_RUN_ID], pending_decision_ref="other-run:0"
        )
        with agent_run_scope("other-run"):
            env.decision_store.save(
                run_id="other-run", step_index=0, decision=_TOOL_CALL
            )

        resume = await env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存")

        assert resume.error_code == AGENT_ERROR_CHECKPOINT_DECISION_MISMATCH
        assert env.llm.calls == 0

    async def test_decision_step_mismatch_is_fail_closed(self) -> None:
        """§五十一：decision.step_index != checkpoint.pending_step_index ⇒ MISMATCH。"""
        env = _env(FakeLLM([_FINAL]))
        _seed_tool_state(env)
        env.checkpoints.rows[_RUN_ID] = replace(
            env.checkpoints.rows[_RUN_ID], pending_step_index=7
        )

        resume = await env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存")

        assert resume.error_code == AGENT_ERROR_CHECKPOINT_DECISION_MISMATCH
        assert env.llm.calls == 0 and env.handler.calls == []

    async def test_active_without_ref_requires_llm_next_action(self) -> None:
        """ACTIVE 但 next_action 不是 LLM_DECISION 且没有 ref ⇒ 不一致 ⇒ fail closed。"""
        env = _env(FakeLLM([_FINAL]))
        env.checkpoints.create(
            run_id=_RUN_ID,
            state_version=1,
            claim_token="claim-p6",
            status=CHECKPOINT_STATUS_ACTIVE,
            next_action=CHECKPOINT_NEXT_ACTION_TOOL_EXECUTION,
            pending_step_index=0,
            pending_tool_call_id="call-x",
            pending_decision_ref=None,
            pending_tool_result_ref=None,
        )

        resume = await env.runtime.resume(run_id=_RUN_ID, question="?")

        assert resume.error_code == AGENT_ERROR_CHECKPOINT_DECISION_MISMATCH
        assert env.llm.calls == 0 and env.handler.calls == []


# ============================================================
# §三十五 / §五十二：并发 Resume
# ============================================================


class TestResumeConcurrency:
    async def test_concurrent_resume_only_one_wins(self) -> None:
        env = _env(FakeLLM([_FINAL]))
        _seed_tool_state(env)

        first, second = await asyncio.gather(
            env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存"),
            env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存"),
        )

        results = [first, second]
        winners = [r for r in results if r.error_code is None]
        losers = [r for r in results if r.error_code == AGENT_ERROR_RESUME_CONFLICT]
        assert len(winners) == 1
        assert len(losers) == 1  # §三十五：失败者 fail closed（无 retry / 无 takeover）
        assert env.llm.calls == 1  # 只有一个 Resume 真正继续（§五十二）
        assert env.handler.calls == []

    async def test_sequential_resume_is_idempotent_replay(self) -> None:
        """顺序（非并发）Resume：第二次只是幂等 Replay ⇒ 无新 LLM / Tool（§三十六）。"""
        env = _env(FakeLLM([_FINAL]))
        _seed_tool_state(env)

        first = await env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存")
        assert first.status == AGENT_STATUS_COMPLETED
        assert env.llm.calls == 1

        second = await env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存")

        assert second.status == AGENT_STATUS_COMPLETED
        assert second.content == first.content
        assert second.llm_calls == 0 and second.tool_calls == 0
        assert env.llm.calls == 1  # 仍未新增 LLM 调用
        assert env.handler.calls == []


# ============================================================
# §十九 / §三十六 / §三十七：Terminal Resume
# ============================================================


class TestResumeTerminal:
    def _terminal(
        self, env: _Env, *, status: str, with_final: bool = True, run_id: str = _RUN_ID
    ) -> None:
        if with_final:
            env.decision_store.save(run_id=run_id, step_index=0, decision=_FINAL)
        with agent_run_scope(run_id):
            env.checkpoint_recorder.record_terminal(
                run_id=run_id, agent_status=status
            )

    async def test_terminal_completed_returns_historical_content(self) -> None:
        env = _env(FakeLLM([_FINAL]))
        self._terminal(env, status=AGENT_STATUS_COMPLETED)

        resume = await env.runtime.resume(run_id=_RUN_ID, question="库存多少")

        assert resume.status == AGENT_STATUS_COMPLETED
        assert resume.content == "库存 120 件。"  # 从 Decision Store 安全恢复
        assert resume.llm_calls == 0 and resume.tool_calls == 0
        assert env.llm.calls == 0 and env.handler.calls == []

    async def test_terminal_completed_without_decision_returns_unavailable(self) -> None:
        """§三十七：内容不可恢复 ⇒ 明确返回 AGENT_FINAL_RESULT_UNAVAILABLE（不重问 LLM）。"""
        env = _env(FakeLLM([_FINAL]))
        self._terminal(env, status=AGENT_STATUS_COMPLETED, with_final=False)

        resume = await env.runtime.resume(run_id=_RUN_ID, question="库存多少")

        assert resume.status == AGENT_STATUS_COMPLETED
        assert resume.content is None
        assert resume.error_code == AGENT_ERROR_FINAL_RESULT_UNAVAILABLE
        assert env.llm.calls == 0

    async def test_terminal_failed_returns_failed_without_llm(self) -> None:
        env = _env(FakeLLM([_FINAL]))
        self._terminal(env, status=AGENT_STATUS_FAILED)

        resume = await env.runtime.resume(run_id=_RUN_ID, question="库存多少")

        assert resume.status == AGENT_STATUS_FAILED
        assert resume.error_code is None
        assert env.llm.calls == 0 and env.handler.calls == []
        # 终态 Resume 不写 Checkpoint（无 claim；§三十六）
        assert env.checkpoints.rows[_RUN_ID].status == CHECKPOINT_STATUS_TERMINAL
        assert env.checkpoints.rows[_RUN_ID].state_version == 1


# ============================================================
# §五十五：Replay Security（不把敏感结果写进其它 Store）
# ============================================================


class TestResumeSecurity:
    async def test_sensitive_result_is_not_replayed_into_other_stores(self) -> None:
        """敏感 Tool Result（P-4 ⇒ unavailable）⇒ fail closed，且**不**复制到别处。"""
        env = _env(FakeLLM([_FINAL]))
        _seed_tool_state(
            env,
            data={
                "material_code": "MAT001",
                "password": "p@ssw0rd",
                "authorization": "Bearer abc.def",
                "database_url": "postgresql://u:p@h/db",
            },
        )

        resume = await env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存")

        assert resume.status == AGENT_STATUS_FAILED
        assert resume.error_code == AGENT_ERROR_TOOL_RESULT_UNAVAILABLE
        assert env.llm.calls == 0 and env.handler.calls == []
        # 敏感内容不进入 Decision / Checkpoint（Step/Run 由上层投影，此处无写入）
        blobs = [
            " ".join(str(v) for v in vars(row).values())
            for row in env.decisions.rows.values()
        ] + [
            " ".join(str(v) for v in vars(row).values())
            for row in env.checkpoints.rows.values()
        ]
        for blob in blobs:
            for token in ("password", "p@ssw0rd", "Bearer", "postgresql://"):
                assert token not in blob, token

    async def test_replay_does_not_create_new_rows(self) -> None:
        """§三十九 / §四十：Replay 只读 —— 不新增 Tool Result / Decision / AgentRun。"""
        env = _env(FakeLLM([_FINAL]))
        _seed_tool_state(env)
        original = env.decisions.get(_RUN_ID, 0)
        tool_rows_before = len(env.tool_results.rows)

        resume = await env.runtime.resume(run_id=_RUN_ID, question="查询 MAT001 库存")

        assert resume.status == AGENT_STATUS_COMPLETED
        assert len(env.tool_results.rows) == tool_rows_before  # 未新增 Tool Result
        assert env.decisions.get(_RUN_ID, 0) == original  # 历史 Decision 未被改写
        # 续跑产生的新 Step 只写"新的" Decision（step 1）；不覆盖历史
        assert env.decisions.get(_RUN_ID, 1) is not None
        # 合成结果为纯 Replay 路径（checkpoint 仍为 ACTIVE；终态由上层写）
