"""Phase 5 P-7 协作式取消 —— **DB** 测试（真 PostgreSQL）。

**默认跳过**（沿用项目既有 ``RUN_DB_TESTS`` 门控）：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_agent_cancellation_db.py
```

验证（任务书 §二十七 / §二十八 / §三十一 / §三十四 / §三十五 / §五十四 / §六十六 / §六十七）：

```text
schema        ai_ops.agent_checkpoint 新增两列：cancel_requested（boolean NOT NULL，
              默认 false）· cancel_requested_at（timestamptz NULL）
repository    request_cancel（**单条 SQL CAS**：cancel_requested=false → true；
              state_version + 1；cancel_requested_at 非空）·
              重复取消 / 已终态 ⇒ CHECKPOINT_CANCEL_CONFLICT（不 force）·
              无 Checkpoint ⇒ CHECKPOINT_NOT_FOUND ·
              **update() 不覆盖取消信号**（P-5 语义与 P-7 信号互不干扰）
coordinator   真 recorder + 真 repository：ACTIVE ⇒ 只置信号；PAUSED ⇒ 终态化
E2E           真 Runtime + 真 Checkpoint：Tool 执行期间取消 ⇒ Tool 完整返回、
              Tool Result 落库、**AgentRun = CANCELLED**（不是 FAILED）、
              再 Resume ⇒ AGENT_RUN_NOT_RESUMABLE（LLM = 0 / Tool = 0）
residue       本文件写入的 6 张表清理后 = 0（不改既有残留守卫）
```

边界：**不**调用 DeepSeek / 不调用真实业务 Tool（Fake LLM + test-local read-only handler）。

Note:
    本项目**无 Alembic / migration**（沿用 ``Base.metadata.create_all(checkfirst=True)``）。
    新增列对**已存在**的表不会自动补齐 —— 本文件提供幂等、非破坏性的
    ``ensure_p7_checkpoint_columns``（``ADD COLUMN IF NOT EXISTS``）：
    仅补列、不删列 / 不改历史行（默认值 false / NULL）；P-7 DB 测试共用。
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Iterator

import pytest

from backend.app.agent import (
    AGENT_ERROR_CANCEL_REQUESTED,
    AGENT_ERROR_CANCELLED,
    AGENT_ERROR_RUN_NOT_CANCELLABLE,
    AGENT_ERROR_RUN_NOT_RESUMABLE,
    AGENT_STATUS_CANCELLED,
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentCancellationCoordinator,
    AgentDecision,
    AgentRuntime,
    AgentRuntimePolicy,
)
from backend.app.agent.checkpoint import (
    CHECKPOINT_NEXT_ACTION_CANCELLED,
    CHECKPOINT_NEXT_ACTION_LLM_DECISION,
    CHECKPOINT_STATUS_ACTIVE,
    CHECKPOINT_STATUS_PAUSED,
    CHECKPOINT_STATUS_TERMINAL,
    BestEffortAgentCheckpointRecorder,
)
from backend.app.agent.decision_store import AgentDecisionStore
from backend.app.agent.ids import agent_run_scope
from backend.app.agent.recorder import (
    AgentToolResultWritePoint,
    BestEffortAgentRunRecorder,
    BestEffortAgentStepRecorder,
    BestEffortAgentToolResultRecorder,
)
from backend.app.db.agent_checkpoint_repository import (
    CHECKPOINT_CANCEL_CONFLICT,
    CHECKPOINT_NOT_FOUND,
    AgentCheckpointConflictError,
    AgentCheckpointNotFoundError,
    AgentCheckpointRepository,
)
from backend.app.db.agent_decision_repository import AgentDecisionRepository
from backend.app.db.agent_run_repository import AgentRunRepository
from backend.app.db.agent_step_repository import AgentStepRepository
from backend.app.db.agent_tool_result_repository import AgentToolResultRepository
from backend.app.db.base import Base
from backend.app.db.models import AgentCheckpoint as AgentCheckpointModel
from backend.app.db.models.agent_checkpoint import (
    AGENT_CHECKPOINT_SCHEMA,
    AGENT_CHECKPOINT_TABLE,
)
from backend.app.db.session import get_engine
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorExecutionError,
    AIOrchestratorService,
)
from tests.test_agent_cancellation import _CancellingHandler, _LateBoundRecorder
from tests.test_agent_runtime import FakeLLM, _TOOL_NAME, _execution_service
from tests.test_agent_step_persistence import _AgentRouteRouter

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable agent cancellation PostgreSQL tests",
)

__all__ = [
    "ensure_p7_checkpoint_columns",
    "TestAgentCheckpointCancelColumns",
    "TestAgentCheckpointCancelRepository",
    "TestAgentCancellationCoordinatorDb",
    "TestAgentCancellationDatabase",
    "TestAgentCancellationResidue",
]

_RUN_PREFIX = "run-p7-cancel-db-"

_OWN_TABLES: tuple[str, ...] = (
    "agent_checkpoint",
    "agent_decision",
    "agent_tool_result",
    "agent_step",
    "agent_run",
    "agent_approval",
)

_TOOL_CALL_1 = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_TOOL_NAME,
    arguments={"material_code": "MAT001"},
    tool_call_id="call_p7_db_c1",
)
_TOOL_CALL_2 = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_TOOL_NAME,
    arguments={"material_code": "MAT002"},
    tool_call_id="call_p7_db_c2",
)
_FINAL = AgentDecision(type=DECISION_FINAL, content="库存 120 件。")


def ensure_p7_checkpoint_columns(engine: Any) -> None:
    """幂等补列（P-7：cancel_requested / cancel_requested_at；**非破坏性**）。

    本项目无 Alembic：``create_all(checkfirst=True)`` 不会给**已存在**的表补列，
    因此 P-7 DB 测试显式执行 ``ADD COLUMN IF NOT EXISTS``（只增列；默认值
    false / NULL ⇒ 历史行语义不变；**不**删列 / **不**改历史行）。
    """
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(engine) as session, session.begin():
        session.execute(
            text(
                f"ALTER TABLE {AGENT_CHECKPOINT_SCHEMA}.{AGENT_CHECKPOINT_TABLE}"
                " ADD COLUMN IF NOT EXISTS cancel_requested boolean"
                " NOT NULL DEFAULT false"
            )
        )
        session.execute(
            text(
                f"ALTER TABLE {AGENT_CHECKPOINT_SCHEMA}.{AGENT_CHECKPOINT_TABLE}"
                " ADD COLUMN IF NOT EXISTS cancel_requested_at"
                " timestamp with time zone"
            )
        )


def _run_id() -> str:
    return f"{_RUN_PREFIX}{uuid.uuid4().hex[:12]}"


def _counts(engine: Any, tables: tuple[str, ...]) -> dict[str, int]:
    from sqlalchemy import text

    with engine.connect() as connection:
        return {
            table: int(
                connection.execute(
                    text(f"SELECT COUNT(*) FROM ai_ops.{table}")
                ).scalar_one()
            )
            for table in tables
        }


@pytest.fixture(scope="module")
def engine() -> Any:
    resolved = get_engine()
    if resolved is None:
        pytest.skip("DATABASE_URL 未配置（DB 测试跳过）")
    return resolved


@pytest.fixture(scope="module", autouse=True)
def _ensure_schema(engine: Any) -> Iterator[None]:
    """幂等建表 + 幂等补列（本文件写入的 6 张表）。"""
    from backend.app.db.models import (
        AgentApproval as AgentApprovalModel,
        AgentDecision as AgentDecisionModel,
        AgentRun as AgentRunModel,
        AgentStep as AgentStepModel,
        AgentToolResult as AgentToolResultModel,
    )

    Base.metadata.create_all(
        bind=engine,
        tables=[
            AgentCheckpointModel.__table__,
            AgentDecisionModel.__table__,
            AgentToolResultModel.__table__,
            AgentStepModel.__table__,
            AgentRunModel.__table__,
            AgentApprovalModel.__table__,
        ],
        checkfirst=True,
    )
    ensure_p7_checkpoint_columns(engine)
    yield


@pytest.fixture()
def cleanup(engine: Any) -> Iterator[list[str]]:
    run_ids: list[str] = []
    yield run_ids
    if not run_ids:
        return
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(engine) as session, session.begin():
        for table in _OWN_TABLES:
            session.execute(
                text(f"DELETE FROM ai_ops.{table} WHERE run_id = ANY(:ids)"),
                {"ids": run_ids},
            )


def _row_counts(engine: Any, run_id: str) -> dict[str, int]:
    from sqlalchemy import text

    with engine.connect() as connection:
        return {
            table: int(
                connection.execute(
                    text(f"SELECT COUNT(*) FROM ai_ops.{table} WHERE run_id = :id"),
                    {"id": run_id},
                ).scalar_one()
            )
            for table in _OWN_TABLES
        }


# ============================================================
# §二十七 / §二十八：列
# ============================================================


class TestAgentCheckpointCancelColumns:
    def test_cancel_columns_present(self, engine: Any) -> None:
        from sqlalchemy import text

        with engine.connect() as connection:
            columns = {
                name: (data_type, is_nullable, column_default)
                for name, data_type, is_nullable, column_default in connection.execute(
                    text(
                        "SELECT column_name, data_type, is_nullable, column_default"
                        " FROM information_schema.columns"
                        " WHERE table_schema = :schema AND table_name = :table"
                    ),
                    {
                        "schema": AGENT_CHECKPOINT_SCHEMA,
                        "table": AGENT_CHECKPOINT_TABLE,
                    },
                )
            }

        assert "cancel_requested" in columns
        assert "cancel_requested_at" in columns
        data_type, nullable, default = columns["cancel_requested"]
        assert data_type == "boolean"
        assert nullable == "NO"
        assert default is not None and "false" in default.lower()
        data_type, nullable, _default = columns["cancel_requested_at"]
        assert data_type == "timestamp with time zone"
        assert nullable == "YES"


# ============================================================
# §五十二 / §五十四：request_cancel（CAS）
# ============================================================


class TestAgentCheckpointCancelRepository:
    def _active(self, recorder: BestEffortAgentCheckpointRecorder, run_id: str) -> None:
        with agent_run_scope(run_id):
            recorder.record_decision_checkpoint(
                step_index=0,
                decision_ref=f"{run_id}:0",
                next_action=CHECKPOINT_NEXT_ACTION_LLM_DECISION,
            )

    def test_request_cancel_sets_signal_and_bumps_version(
        self, cleanup: list[str]
    ) -> None:
        repository = AgentCheckpointRepository()
        recorder = BestEffortAgentCheckpointRecorder(repository)
        run_id = _run_id()
        cleanup.append(run_id)
        self._active(recorder, run_id)
        before = repository.get(run_id)

        updated = repository.request_cancel(run_id=run_id)

        assert updated.cancel_requested is True
        assert updated.cancel_requested_at is not None
        assert updated.state_version == before.state_version + 1
        assert updated.status == CHECKPOINT_STATUS_ACTIVE  # 状态不变（信号才变）
        assert updated.claim_token == before.claim_token  # claim 不换

    def test_repeat_cancel_conflicts_without_force(self, cleanup: list[str]) -> None:
        repository = AgentCheckpointRepository()
        recorder = BestEffortAgentCheckpointRecorder(repository)
        run_id = _run_id()
        cleanup.append(run_id)
        self._active(recorder, run_id)
        repository.request_cancel(run_id=run_id)

        with pytest.raises(AgentCheckpointConflictError) as excinfo:
            repository.request_cancel(run_id=run_id)

        assert excinfo.value.code == CHECKPOINT_CANCEL_CONFLICT
        # 幂等重复取消**不**再改版本 / 不覆盖
        assert repository.get(run_id).state_version == 2

    def test_cancel_terminal_conflicts(self, cleanup: list[str]) -> None:
        repository = AgentCheckpointRepository()
        recorder = BestEffortAgentCheckpointRecorder(repository)
        run_id = _run_id()
        cleanup.append(run_id)
        with agent_run_scope(run_id):
            recorder.record_decision_checkpoint(
                step_index=0,
                decision_ref=f"{run_id}:0",
                next_action="COMPLETED",
            )
            recorder.record_terminal(run_id=run_id, agent_status="COMPLETED")

        with pytest.raises(AgentCheckpointConflictError) as excinfo:
            repository.request_cancel(run_id=run_id)

        assert excinfo.value.code == CHECKPOINT_CANCEL_CONFLICT
        assert repository.get(run_id).cancel_requested is False  # 终态不可取消

    def test_cancel_unknown_run(self) -> None:
        repository = AgentCheckpointRepository()
        with pytest.raises(AgentCheckpointNotFoundError) as excinfo:
            repository.request_cancel(run_id=_run_id())
        assert excinfo.value.code == CHECKPOINT_NOT_FOUND

    def test_update_preserves_cancel_signal(self, cleanup: list[str]) -> None:
        """P-5 ``update`` **不**覆盖取消信号（两个语义互不干扰）。"""
        repository = AgentCheckpointRepository()
        recorder = BestEffortAgentCheckpointRecorder(repository)
        run_id = _run_id()
        cleanup.append(run_id)
        self._active(recorder, run_id)
        repository.request_cancel(run_id=run_id)

        with agent_run_scope(run_id):
            recorder.record_decision_checkpoint(
                step_index=1,
                decision_ref=f"{run_id}:1",
                next_action=CHECKPOINT_NEXT_ACTION_LLM_DECISION,
            )

        row = repository.get(run_id)
        assert row.cancel_requested is True  # 信号仍在
        assert row.cancel_requested_at is not None
        assert row.pending_step_index == 1  # 控制状态已推进


# ============================================================
# §三十七 / §三十八：协调器（真仓储）
# ============================================================


class TestAgentCancellationCoordinatorDb:
    def test_active_run_only_signals(self, cleanup: list[str]) -> None:
        repository = AgentCheckpointRepository()
        recorder = BestEffortAgentCheckpointRecorder(repository)
        run_id = _run_id()
        cleanup.append(run_id)
        with agent_run_scope(run_id):
            recorder.record_decision_checkpoint(
                step_index=0,
                decision_ref=f"{run_id}:0",
                next_action=CHECKPOINT_NEXT_ACTION_LLM_DECISION,
            )

        result = AgentCancellationCoordinator(checkpoints=recorder).cancel(run_id)

        assert result.code == AGENT_ERROR_CANCEL_REQUESTED
        assert result.finalized is False
        assert repository.get(run_id).cancel_requested is True
        assert repository.get(run_id).status == CHECKPOINT_STATUS_ACTIVE

    def test_paused_run_is_finalized(self, cleanup: list[str]) -> None:
        repository = AgentCheckpointRepository()
        recorder = BestEffortAgentCheckpointRecorder(repository)
        run_id = _run_id()
        cleanup.append(run_id)
        with agent_run_scope(run_id):
            recorder.record_waiting_approval_checkpoint(
                step_index=0,
                decision_ref=f"{run_id}:0",
                tool_call_id="call-paused",
            )

        result = AgentCancellationCoordinator(checkpoints=recorder).cancel(run_id)

        assert result.code == AGENT_ERROR_CANCELLED
        assert result.finalized is True
        row = repository.get(run_id)
        assert row.status == CHECKPOINT_STATUS_TERMINAL
        assert row.next_action == CHECKPOINT_NEXT_ACTION_CANCELLED

    def test_completed_run_is_noop(self, cleanup: list[str]) -> None:
        repository = AgentCheckpointRepository()
        recorder = BestEffortAgentCheckpointRecorder(repository)
        run_id = _run_id()
        cleanup.append(run_id)
        with agent_run_scope(run_id):
            recorder.record_decision_checkpoint(
                step_index=0,
                decision_ref=f"{run_id}:0",
                next_action="COMPLETED",
            )
            recorder.record_terminal(run_id=run_id, agent_status="COMPLETED")
        before = repository.get(run_id)

        result = AgentCancellationCoordinator(checkpoints=recorder).cancel(run_id)

        assert result.code == AGENT_ERROR_RUN_NOT_CANCELLABLE
        assert repository.get(run_id) == before  # no-op


# ============================================================
# §三十一 / §三十四 / §三十五 / §六十五：E2E（真 DB）
# ============================================================


class TestAgentCancellationDatabase:
    async def test_cancel_during_tool_preserves_result_and_run_is_cancelled(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        holder: dict[str, Any] = {}
        handler = _CancellingHandler(
            _LateBoundRecorder(lambda: holder["runtime_recorder"]), on_call=2
        )
        service, registry, resolved_handler = _execution_service(handler)
        checkpoint_repository = AgentCheckpointRepository()
        recorder = BestEffortAgentCheckpointRecorder(checkpoint_repository)
        tool_result_repository = AgentToolResultRepository()
        runtime = AgentRuntime(
            llm=FakeLLM([_TOOL_CALL_1, _TOOL_CALL_2, _FINAL]),
            tool_execution=service,
            policy=AgentRuntimePolicy(
                max_execution_time_ms=30_000, max_cost_tokens=1_000
            ),
            allowed_tools=(_TOOL_NAME,),
            tool_result_sink=AgentToolResultWritePoint(
                recorder=BestEffortAgentToolResultRecorder(
                    repository=tool_result_repository
                )
            ),
            checkpoint_sink=recorder,
            checkpoint_store=recorder,
            decision_store=AgentDecisionStore(AgentDecisionRepository()),
            tool_result_store=tool_result_repository,
        )
        holder["runtime_recorder"] = recorder
        orchestrator = AIOrchestratorService(
            router=_AgentRouteRouter(),  # type: ignore[arg-type]
            agent_runtime=runtime,
            agent_checkpoint_recorder=recorder,
            agent_run_recorder=BestEffortAgentRunRecorder(AgentRunRepository()),
            agent_step_recorder=BestEffortAgentStepRecorder(AgentStepRepository()),
        )

        result = await orchestrator.execute("查询 MAT001 / MAT002 库存")
        run_id = result.data.run_id
        cleanup.append(run_id)

        # §三十四：CANCELLED（**不**映射成 FAILED）；Tool 完整返回、结果保留
        assert result.metadata["agent_status"] == AGENT_STATUS_CANCELLED
        assert result.metadata["agent_error"] == AGENT_ERROR_CANCEL_REQUESTED
        assert len(registry.execute_calls) == 2
        assert [call["material_code"] for call in resolved_handler.calls] == [
            "MAT001",
            "MAT002",
        ]
        counts = _row_counts(engine, run_id)
        assert counts["agent_tool_result"] == 2  # Tool Result 全部保留
        assert counts["agent_decision"] == 2
        assert counts["agent_checkpoint"] == 1
        assert counts["agent_run"] == 1
        row = checkpoint_repository.get(run_id)
        assert row.cancel_requested is True
        assert row.cancel_requested_at is not None

        # §三十四：AgentRun 终态记录 = **CANCELLED**（不是 FAILED）
        from sqlalchemy import text

        with engine.connect() as connection:
            run_status, run_error = connection.execute(
                text(
                    "SELECT status, error_code FROM ai_ops.agent_run"
                    " WHERE run_id = :id"
                ),
                {"id": run_id},
            ).one()
        assert run_status == AGENT_STATUS_CANCELLED
        assert run_error == AGENT_ERROR_CANCEL_REQUESTED

        # §三十五：终态 Checkpoint = TERMINAL / CANCELLED（由 Orchestrator 写）
        terminal = checkpoint_repository.get(run_id)
        assert terminal.status == CHECKPOINT_STATUS_TERMINAL
        assert terminal.next_action == CHECKPOINT_NEXT_ACTION_CANCELLED

        # §三十三 / §六十五：CANCELLED ⇒ 拒绝 Resume（LLM = 0 / Tool = 0）
        executions_before = len(registry.execute_calls)
        with pytest.raises(AIOrchestratorExecutionError) as excinfo:
            await orchestrator.resume_agent_run(
                run_id, question="查询 MAT001 / MAT002 库存"
            )
        assert AGENT_ERROR_RUN_NOT_RESUMABLE in str(excinfo.value)
        assert len(registry.execute_calls) == executions_before

    async def test_cancel_paused_run_then_resume_is_refused(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        """HITL 暂停 → 取消（终态化）⇒ Resume 拒绝；Tool 一次都没执行。"""
        from backend.app.agent import AgentApprovalStore
        from backend.app.db.agent_approval_repository import AgentApprovalRepository

        service, registry, _handler = _execution_service()
        checkpoint_repository = AgentCheckpointRepository()
        recorder = BestEffortAgentCheckpointRecorder(checkpoint_repository)
        tool_result_repository = AgentToolResultRepository()
        approval_store = AgentApprovalStore(AgentApprovalRepository())
        runtime = AgentRuntime(
            llm=FakeLLM([_TOOL_CALL_1, _FINAL]),
            tool_execution=service,
            policy=AgentRuntimePolicy(
                max_execution_time_ms=30_000, max_cost_tokens=1_000
            ),
            allowed_tools=(_TOOL_NAME,),
            tool_result_sink=AgentToolResultWritePoint(
                recorder=BestEffortAgentToolResultRecorder(
                    repository=tool_result_repository
                )
            ),
            checkpoint_sink=recorder,
            checkpoint_store=recorder,
            decision_store=AgentDecisionStore(AgentDecisionRepository()),
            tool_result_store=tool_result_repository,
            approval_port=approval_store,
            approval_required_tools=(_TOOL_NAME,),
        )
        orchestrator = AIOrchestratorService(
            router=_AgentRouteRouter(),  # type: ignore[arg-type]
            agent_runtime=runtime,
            agent_approval_store=approval_store,
            agent_checkpoint_recorder=recorder,
        )

        paused = await runtime.run(question="查询 MAT001 库存")
        cleanup.append(paused.run_id)
        assert paused.status == "WAITING_APPROVAL"

        cancelled = await orchestrator.cancel_agent_run(paused.run_id)
        assert cancelled.code == AGENT_ERROR_CANCELLED

        with pytest.raises(AIOrchestratorExecutionError) as excinfo:
            await orchestrator.resume_agent_run(
                paused.run_id, question="查询 MAT001 库存"
            )
        assert AGENT_ERROR_RUN_NOT_RESUMABLE in str(excinfo.value)
        assert registry.execute_calls == []  # Tool = 0
        counts = _row_counts(engine, paused.run_id)
        assert counts["agent_tool_result"] == 0
        assert counts["agent_approval"] == 1


# ============================================================
# §六十七：残留 = 0
# ============================================================


class TestAgentCancellationResidue:
    def test_residue_is_zero(self, engine: Any) -> None:
        from sqlalchemy import text
        from sqlalchemy.orm import Session

        with Session(engine) as session, session.begin():
            for table in _OWN_TABLES:
                session.execute(
                    text(f"DELETE FROM ai_ops.{table} WHERE run_id LIKE :prefix"),
                    {"prefix": f"{_RUN_PREFIX}%"},
                )

        counts = _counts(engine, _OWN_TABLES)
        assert all(value == 0 for value in counts.values()), counts
