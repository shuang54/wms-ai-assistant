"""Phase 5 P-6 Resume —— **集成** 测试（离线 Orchestrator + 可选真 PostgreSQL）。

覆盖（任务书 §三十 / §三十四 / §四十一 / §五十四 / §五十七 / §五十八）：

```text
离线     resume_agent_run：run_id 不变 / request_id 新建；Replay 不调 LLM / 不重跑 Tool；
        终态 Checkpoint 由 Orchestrator 经 state_version + claim_token 写（§三十四）；
        fail-closed ⇒ AIOrchestratorExecutionError（且 Run 终态 = FAILED）；
        terminal Resume ⇒ 历史内容（LLM = 0 / Tool = 0；§十九 / §五十三）
DB      真 PostgreSQL 全链：agent_decision / agent_checkpoint / agent_tool_result /
        agent_step / agent_run —— 两个"进程"（runtime #1 崩溃 → runtime #2 Resume）
        共用同一 run_id；residue = 0
```
"""
from __future__ import annotations

import os
from typing import Any, Iterator

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
    CHECKPOINT_STATUS_TERMINAL,
    BestEffortAgentCheckpointRecorder,
)
from backend.app.agent.decision_store import AgentDecisionStore
from backend.app.agent.errors import (
    AGENT_ERROR_FINAL_RESULT_UNAVAILABLE,
    AGENT_ERROR_TOOL_RESULT_NOT_FOUND,
)
from backend.app.agent.ids import agent_run_scope
from backend.app.agent.recorder import (
    AgentToolResultWritePoint,
    BestEffortAgentRunRecorder,
    BestEffortAgentStepRecorder,
    BestEffortAgentToolResultRecorder,
)
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorCapabilityError,
    AIOrchestratorExecutionError,
    AIOrchestratorService,
    RouteType,
)
from backend.app.tools.base import ToolResult
from tests.test_agent_checkpoint import FakeAgentCheckpointRepository
from tests.test_agent_checkpoint_integration import (
    _OrderRecorder,
    _OrderCheckpointRecorder,
)
from tests.test_agent_decision_store import FakeAgentDecisionRepository
from tests.test_agent_resume import (
    _env,
    _seed_tool_state,
    _TOOL_NAME,
    _TOOL_CALL,
    _FINAL,
    _RUN_ID,
    RecordingCheckpointRecorder,
)
from tests.test_agent_runtime import FakeLLM, _execution_service
from tests.test_agent_step_persistence import _AgentRouteRouter
from tests.test_agent_tool_result_store import FakeAgentToolResultRepository

__all__ = [
    "TestResumeOrchestration",
    "TestResumeTerminalOrchestration",
    "TestResumeDatabaseIntegration",
]

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_DB_RUN_PREFIX = "run-p6-db-"


def _orchestrator(
    runtime: AgentRuntime,
    *,
    order: list[str] | None = None,
    checkpoint_recorder: Any = None,
    run_recorder: Any = None,
    step_recorder: Any = None,
) -> AIOrchestratorService:
    return AIOrchestratorService(
        router=_AgentRouteRouter(),  # type: ignore[arg-type]
        agent_runtime=runtime,
        agent_run_recorder=run_recorder,
        agent_step_recorder=step_recorder,
        agent_checkpoint_recorder=checkpoint_recorder,
    )


# ============================================================
# §三十 / §三十四 / §四十一 / §五十四：Orchestrator 层 Resume
# ============================================================


class TestResumeOrchestration:
    async def test_resume_keeps_run_id_and_creates_new_request_id(self) -> None:
        order: list[str] = []
        checkpoint_repository = FakeAgentCheckpointRepository()
        checkpoint_recorder = RecordingCheckpointRecorder(checkpoint_repository)
        env = _env(FakeLLM([_FINAL]), checkpoint_recorder=checkpoint_recorder)
        _seed_tool_state(env, repository=checkpoint_repository)
        orchestrator = _orchestrator(
            env.runtime,
            order=order,
            checkpoint_recorder=checkpoint_recorder,
            run_recorder=_OrderRecorder(order, "run"),
            step_recorder=_OrderRecorder(order, "steps"),
        )

        result = await orchestrator.resume_agent_run(
            _RUN_ID, question="查询 MAT001 库存"
        )

        assert result.route == RouteType.AGENT
        assert result.content == "库存 120 件。"
        metadata = result.metadata
        assert metadata["resume_run_id"] == _RUN_ID  # §二十七：run_id 不变
        assert isinstance(metadata["request_id"], str) and metadata["request_id"]
        assert metadata["resume_replayed_step_index"] == 0
        assert metadata["resume_llm_calls"] == 1
        assert metadata["resume_tool_calls"] == 0
        assert metadata["agent_status"] == AGENT_STATUS_COMPLETED
        # Replay 不重跑历史 Tool；只新增 1 次 LLM
        assert env.handler.calls == []
        assert env.llm.calls == 1
        # §三十四：终态 Checkpoint 经 claim（version+1）后由 Orchestrator 写 TERMINAL
        checkpoint = checkpoint_recorder.get(_RUN_ID)
        assert checkpoint.status == CHECKPOINT_STATUS_TERMINAL
        assert checkpoint.next_action == CHECKPOINT_NEXT_ACTION_COMPLETED
        assert checkpoint.state_version >= 3  # create(1) → claim(2) → terminal(3+)
        # 顺序：Step ⇒ Run 终态（Checkpoint 终态由同一 recorder 记录）
        assert order[-2:] == ["steps", "run"]
        assert checkpoint_recorder.terminal_writes == [AGENT_STATUS_COMPLETED]

    async def test_resume_request_id_is_new(self) -> None:
        """§五十四：run_id 不变，request_id 是**新**的请求 correlation。"""
        env = _env(FakeLLM([_TOOL_CALL, _FINAL]))
        orchestrator = _orchestrator(
            env.runtime, checkpoint_recorder=env.checkpoint_recorder
        )

        original = await orchestrator.execute("查询 MAT001 库存")
        run_id = original.data.run_id

        resumed = await orchestrator.resume_agent_run(
            run_id, question="查询 MAT001 库存"
        )

        assert resumed.metadata["resume_run_id"] == run_id  # §二十七
        assert resumed.metadata["request_id"] != original.metadata["request_id"]
        assert resumed.content == original.content  # 终态 Replay（无新 LLM）

    async def test_fail_closed_resume_raises_and_writes_terminal_failed(self) -> None:
        """§五十 / §五十三：缺 Tool Result ⇒ ExecutionError + TERMINAL / FAILED。"""
        repository = FakeAgentCheckpointRepository()
        recorder = RecordingCheckpointRecorder(repository)
        env = _env(FakeLLM([_FINAL]), checkpoint_recorder=recorder)
        _seed_tool_state(env)
        env.tool_results.rows.clear()  # 状态 UNKNOWN
        orchestrator = _orchestrator(env.runtime, checkpoint_recorder=recorder)

        with pytest.raises(AIOrchestratorExecutionError) as excinfo:
            await orchestrator.resume_agent_run(_RUN_ID, question="查询 MAT001 库存")

        assert AGENT_ERROR_TOOL_RESULT_NOT_FOUND in str(excinfo.value)
        checkpoint = repository.rows[_RUN_ID]
        assert checkpoint.status == CHECKPOINT_STATUS_TERMINAL
        assert checkpoint.next_action != CHECKPOINT_NEXT_ACTION_COMPLETED
        assert env.llm.calls == 0 and env.handler.calls == []

    async def test_resume_requires_agent_capability(self) -> None:
        orchestrator = AIOrchestratorService(router=_AgentRouteRouter())  # type: ignore[arg-type]

        with pytest.raises(AIOrchestratorCapabilityError):
            await orchestrator.resume_agent_run(_RUN_ID, question="?")


class TestResumeTerminalOrchestration:
    async def test_terminal_resume_returns_historical_content_without_calls(self) -> None:
        """§十九 / §五十三：终态 ⇒ 历史内容；LLM = 0 / Tool = 0。"""
        env = _env(FakeLLM([_FINAL]))
        # 原始 Run（直接驱动 Runtime）+ 终态 Checkpoint（模拟 Orchestrator 已收尾）
        first = await env.runtime.run(question="库存多少")
        run_id = first.run_id
        with agent_run_scope(run_id):
            env.checkpoint_recorder.record_terminal(
                run_id=run_id, agent_status=AGENT_STATUS_COMPLETED
            )
        llm_calls_before = env.llm.calls
        orchestrator = _orchestrator(
            env.runtime, checkpoint_recorder=env.checkpoint_recorder
        )

        result = await orchestrator.resume_agent_run(run_id, question="库存多少")

        assert result.content == "库存 120 件。"
        assert result.metadata["resume_llm_calls"] == 0
        assert result.metadata["resume_tool_calls"] == 0
        assert result.metadata["resume_replayed_step_index"] is None
        assert env.llm.calls == llm_calls_before  # 无新 LLM
        assert env.handler.calls == []

    async def test_terminal_resume_without_decision_reports_unavailable(self) -> None:
        env = _env(FakeLLM([_FINAL]))
        with agent_run_scope("run-p6-terminal-empty"):
            env.checkpoint_recorder.record_terminal(
                run_id="run-p6-terminal-empty", agent_status=AGENT_STATUS_COMPLETED
            )
        orchestrator = _orchestrator(
            env.runtime, checkpoint_recorder=env.checkpoint_recorder
        )

        result = await orchestrator.resume_agent_run(
            "run-p6-terminal-empty", question="库存多少"
        )

        assert result.metadata["resume_error"] == AGENT_ERROR_FINAL_RESULT_UNAVAILABLE
        assert env.llm.calls == 0


# ============================================================
# §五十七 / §五十八：DB 全链（两个"进程"共用同一 run_id）
# ============================================================


@pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable agent resume PostgreSQL integration tests",
)
class TestResumeDatabaseIntegration:
    @pytest.fixture(autouse=True)
    def _ensure_table(self) -> Iterator[None]:
        """幂等建表（**只** ai_ops.agent_decision；checkfirst=True）。"""
        from backend.app.db.base import Base
        from backend.app.db.models import AgentDecision as AgentDecisionModel
        from backend.app.db.session import get_engine

        engine = get_engine()
        assert engine is not None
        Base.metadata.create_all(
            bind=engine, tables=[AgentDecisionModel.__table__], checkfirst=True
        )
        yield

    @pytest.fixture()
    def cleanup(self) -> Iterator[list[str]]:
        run_ids: list[str] = []
        yield run_ids
        if not run_ids:
            return
        from sqlalchemy import text
        from sqlalchemy.orm import Session

        from backend.app.db.session import get_engine

        engine = get_engine()
        assert engine is not None
        with Session(engine) as session, session.begin():
            for table in (
                "agent_decision",
                "agent_checkpoint",
                "agent_tool_result",
                "agent_step",
                "agent_run",
            ):
                session.execute(
                    text(f"DELETE FROM ai_ops.{table} WHERE run_id = ANY(:ids)"),
                    {"ids": run_ids},
                )

    async def test_full_chain_crash_then_resume(self, cleanup: list[str]) -> None:
        """runtime #1（Tool 执行后 LLM 失败）→ runtime #2 Resume → 全链落库。"""
        from sqlalchemy import select, text
        from sqlalchemy.orm import Session

        from backend.app.db.agent_checkpoint_repository import (
            AgentCheckpointRepository,
        )
        from backend.app.db.agent_decision_repository import (
            AgentDecisionRepository,
        )
        from backend.app.db.agent_tool_result_repository import (
            AgentToolResultRepository,
        )
        from backend.app.db.models import (
            AgentCheckpoint as AgentCheckpointModel,
            AgentDecision as AgentDecisionModel,
            AgentRun as AgentRunModel,
            AgentStep as AgentStepModel,
            AgentToolResult as AgentToolResultModel,
        )
        from backend.app.db.session import get_engine

        engine = get_engine()
        assert engine is not None

        # ---- 进程 #1：Tool 执行完成、第二次 LLM 之前"崩溃" ----
        crash_llm = FakeLLM([_TOOL_CALL], error_on_call=2, error=RuntimeError("llm down"))
        service, _registry, handler = _execution_service()
        checkpoint_repository = AgentCheckpointRepository()
        runtime_one = AgentRuntime(
            llm=crash_llm,
            tool_execution=service,
            policy=AgentRuntimePolicy(
                max_execution_time_ms=30_000, max_cost_tokens=1_000
            ),
            allowed_tools=(_TOOL_NAME,),
            tool_result_sink=AgentToolResultWritePoint(
                recorder=BestEffortAgentToolResultRecorder()
            ),
            checkpoint_sink=BestEffortAgentCheckpointRecorder(checkpoint_repository),
            checkpoint_store=BestEffortAgentCheckpointRecorder(checkpoint_repository),
            decision_store=AgentDecisionStore(AgentDecisionRepository()),
            tool_result_store=AgentToolResultRepository(),
        )
        first = await runtime_one.run(question="查询 MAT001 库存")
        run_id = first.run_id
        cleanup.append(run_id)
        assert first.status == AGENT_STATUS_FAILED
        assert run_id.startswith(_DB_RUN_PREFIX) is False  # 真 run_id（uuid4）
        assert handler.calls == [{"material_code": "MAT001"}]

        checkpoint = checkpoint_repository.get(run_id)
        assert checkpoint is not None
        assert checkpoint.state_version == 2  # decision(1) → tool result(2)
        assert checkpoint.pending_step_index == 1

        # ---- 进程 #2：Resume（同一 run_id；新 LLM 实例 / 新 request_id）----
        resume_llm = FakeLLM([_FINAL])
        resume_service, _registry2, resume_handler = _execution_service()
        resume_checkpoint_recorder = BestEffortAgentCheckpointRecorder(
            checkpoint_repository
        )
        runtime_two = AgentRuntime(
            llm=resume_llm,
            tool_execution=resume_service,
            policy=AgentRuntimePolicy(
                max_execution_time_ms=30_000, max_cost_tokens=1_000
            ),
            allowed_tools=(_TOOL_NAME,),
            tool_result_sink=AgentToolResultWritePoint(
                recorder=BestEffortAgentToolResultRecorder()
            ),
            checkpoint_sink=resume_checkpoint_recorder,
            checkpoint_store=resume_checkpoint_recorder,
            decision_store=AgentDecisionStore(AgentDecisionRepository()),
            tool_result_store=AgentToolResultRepository(),
        )
        orchestrator = AIOrchestratorService(
            router=_AgentRouteRouter(),  # type: ignore[arg-type]
            agent_runtime=runtime_two,
            agent_checkpoint_recorder=resume_checkpoint_recorder,
            agent_run_recorder=BestEffortAgentRunRecorder(),
            agent_step_recorder=BestEffortAgentStepRecorder(),
        )
        result = await orchestrator.resume_agent_run(
            run_id, question="查询 MAT001 库存"
        )

        assert result.route == RouteType.AGENT
        assert result.content == "库存 120 件。"
        assert result.metadata["resume_run_id"] == run_id
        assert result.metadata["resume_llm_calls"] == 1
        assert result.metadata["resume_tool_calls"] == 0
        assert resume_handler.calls == []  # 历史 Tool 未重跑
        assert resume_llm.calls == 1  # 只有续跑的 LLM

        # ---- 全链落库校验（5 张表同一 run_id）----
        with Session(engine) as session:
            decisions = list(
                session.scalars(
                    select(AgentDecisionModel)
                    .where(AgentDecisionModel.run_id == run_id)
                    .order_by(AgentDecisionModel.step_index)
                )
            )
            tool_rows = list(
                session.scalars(
                    select(AgentToolResultModel).where(
                        AgentToolResultModel.run_id == run_id
                    )
                )
            )
            run_rows = list(
                session.scalars(
                    select(AgentRunModel).where(AgentRunModel.run_id == run_id)
                )
            )
            step_rows = list(
                session.scalars(
                    select(AgentStepModel)
                    .where(AgentStepModel.run_id == run_id)
                    .order_by(AgentStepModel.step_index)
                )
            )
            checkpoint_row = session.scalars(
                select(AgentCheckpointModel).where(
                    AgentCheckpointModel.run_id == run_id
                )
            ).one()

        assert [row.step_index for row in decisions] == [0, 1]
        assert decisions[0].decision_type == DECISION_TOOL_CALL
        assert decisions[0].tool_call_id is not None
        assert decisions[1].decision_type == DECISION_FINAL
        assert decisions[1].content == "库存 120 件。"
        assert len(tool_rows) == 1  # Replay **未**新增 Tool Result（§三十九）
        assert len(run_rows) == 1  # 同一 AgentRun（§四十）
        assert run_rows[0].status == AGENT_STATUS_COMPLETED
        assert [row.step_index for row in step_rows] == [1]  # 只投影新发生的 Step
        assert checkpoint_row.status == CHECKPOINT_STATUS_TERMINAL
        assert checkpoint_row.next_action == CHECKPOINT_NEXT_ACTION_COMPLETED
        assert checkpoint_row.state_version >= 3
        # checkpoint 不含 claim_token 之外的任何 payload / 敏感内容
        checkpoint_values = " ".join(
            str(getattr(checkpoint_row, column.name))
            for column in AgentCheckpointModel.__table__.columns
        )
        for token in ("prompt", "sk-live", "password"):
            assert token not in checkpoint_values, token

    async def test_residue_is_zero_after_cleanup(self, cleanup: list[str]) -> None:
        from sqlalchemy import text

        from backend.app.db.session import get_engine

        engine = get_engine()
        assert engine is not None
        with engine.connect() as conn:
            counts = {
                table: int(
                    conn.execute(
                        text(f"SELECT COUNT(*) FROM ai_ops.{table}")
                    ).scalar_one()
                )
                for table in ("agent_decision", "agent_checkpoint")
            }

        assert counts == {"agent_decision": 0, "agent_checkpoint": 0}
