"""Phase 5 P-8 Agent Read API / Timeline —— **DB** 集成测试（真 PostgreSQL）。

**默认跳过**（沿用项目既有 ``RUN_DB_TESTS`` 门控）：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_agent_read_db.py
```

验证（任务书 §二十一 / §三十 / §三十一 / §三十二 / §三十八）：

```text
1  真实 Run 全链     真 Runtime + 真 Repository ⇒ Read Service / Timeline 可读；
                     AgentRun / AgentStep / AgentDecision / AgentToolResult /
                     AgentCheckpoint 六表 **行数前后不变**（§三十一：只读）
2  project 隔离矩阵  真行（project-A / project-B）：A↔A 可读、A↔B 404、
                     B↔B 可读、B↔A 404（§三十）
3  HITL 暂停          暂停中的 Run **无 project 归属来源** ⇒ 404（fail-closed；
                     P-8 Known Limitation）；批准 + Resume 后（Tool Result 落盘）
                     ⇒ 立即可读（同一 run_id）
4  CANCELLED         真协作式取消 ⇒ status=CANCELLED 可读；Timeline 有
                     RUN_CANCELLED（**无** RUN_FAILED）
5  分页              真行上分页可无损拼接（sequence 保持）
6  残留              本文件写入的 6 张表清理后 residue = 0
```

边界：**不**调用 DeepSeek / 不调用真实业务 Tool（Fake LLM + test-local read-only
handler）；不改任何既有表 schema。
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Iterator

import pytest

from backend.app.agent import (
    AGENT_STATUS_CANCELLED,
    AGENT_STATUS_COMPLETED,
    AGENT_STATUS_WAITING_APPROVAL,
    APPROVAL_STATUS_APPROVED,
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentApprovalStore,
    AgentDecision,
    AgentRuntime,
    AgentRuntimePolicy,
)
from backend.app.agent.checkpoint import BestEffortAgentCheckpointRecorder
from backend.app.agent.decision_store import AgentDecisionStore
from backend.app.agent.recorder import (
    AgentToolResultWritePoint,
    BestEffortAgentRunRecorder,
    BestEffortAgentStepRecorder,
    BestEffortAgentToolResultRecorder,
)
from backend.app.config import settings
from backend.app.db.agent_approval_repository import AgentApprovalRepository
from backend.app.db.agent_checkpoint_repository import AgentCheckpointRepository
from backend.app.db.agent_decision_repository import AgentDecisionRepository
from backend.app.db.agent_run_repository import AgentRunRepository
from backend.app.db.agent_step_repository import AgentStepRepository
from backend.app.db.agent_tool_result_repository import (
    AgentToolResultRepository,
    AgentToolResultRow,
)
from backend.app.db.base import Base
from backend.app.db.models import AgentRun as AgentRunModel
from backend.app.db.session import get_engine
from backend.app.services.agent_read_service import (
    AgentReadService,
    AgentRunNotFoundError,
)
from backend.app.services.ai_orchestrator_service import AIOrchestratorService
from tests.test_agent_cancellation import _CancellingHandler, _LateBoundRecorder
from tests.test_agent_cancellation_db import ensure_p7_checkpoint_columns
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
    reason="set RUN_DB_TESTS=1 to enable agent read model PostgreSQL tests",
)

__all__ = [
    "TestAgentReadDatabaseIntegration",
    "TestAgentReadProjectIsolationDb",
    "TestAgentReadCancelAndPauseDb",
    "TestAgentReadResidue",
]

_RUN_PREFIX = "run-p8-read-db-"

#: 本文件写入的表（清理后 residue = 0）。
_OWN_TABLES: tuple[str, ...] = (
    "agent_run",
    "agent_step",
    "agent_decision",
    "agent_tool_result",
    "agent_checkpoint",
    "agent_approval",
)

#: 真实 Runtime 的 Tool 执行作用域（= Tool Result 的 project_id；归属来源）。
_TOOL_PROJECT = "phase5-agent-test"
_OTHER_PROJECT = "phase5-other-project"

_TOOL_CALL_1 = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_TOOL_NAME,
    arguments={"material_code": "MAT001"},
    tool_call_id="call_p8_db_1",
)
_TOOL_CALL_2 = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_TOOL_NAME,
    arguments={"material_code": "MAT002"},
    tool_call_id="call_p8_db_2",
)
_FINAL = AgentDecision(type=DECISION_FINAL, content="库存 120 件。")


def _run_id() -> str:
    return f"{_RUN_PREFIX}{uuid.uuid4().hex[:12]}"


def _read_service() -> AgentReadService:
    return AgentReadService(
        run_repository=AgentRunRepository(),
        step_repository=AgentStepRepository(),
        decision_repository=AgentDecisionRepository(),
        tool_result_repository=AgentToolResultRepository(),
        checkpoint_repository=AgentCheckpointRepository(),
        approval_repository=AgentApprovalRepository(),
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
        AgentCheckpoint as AgentCheckpointModel,
        AgentDecision as AgentDecisionModel,
        AgentStep as AgentStepModel,
        AgentToolResult as AgentToolResultModel,
    )

    Base.metadata.create_all(
        bind=engine,
        tables=[
            AgentRunModel.__table__,
            AgentStepModel.__table__,
            AgentDecisionModel.__table__,
            AgentToolResultModel.__table__,
            AgentCheckpointModel.__table__,
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


async def _execute_run(
    script: list[AgentDecision],
    *,
    handler: Any = None,
    approval_required: tuple[str, ...] = (),
) -> tuple[Any, Any, Any]:
    """真 Runtime + 真 Repository 执行一次 Run（返回 result / registry / handler）。"""
    service, registry, resolved_handler = _execution_service(handler)
    checkpoint_recorder = BestEffortAgentCheckpointRecorder(
        AgentCheckpointRepository()
    )
    tool_result_repository = AgentToolResultRepository()
    approval_store = AgentApprovalStore(AgentApprovalRepository())
    runtime = AgentRuntime(
        llm=FakeLLM(script),
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
        checkpoint_sink=checkpoint_recorder,
        checkpoint_store=checkpoint_recorder,
        decision_store=AgentDecisionStore(AgentDecisionRepository()),
        tool_result_store=tool_result_repository,
        approval_port=approval_store,
        approval_required_tools=approval_required,
    )
    orchestrator = AIOrchestratorService(
        router=_AgentRouteRouter(),  # type: ignore[arg-type]
        agent_runtime=runtime,
        agent_approval_store=approval_store,
        agent_checkpoint_recorder=checkpoint_recorder,
        agent_run_recorder=BestEffortAgentRunRecorder(AgentRunRepository()),
        agent_step_recorder=BestEffortAgentStepRecorder(AgentStepRepository()),
    )
    if approval_required:
        result = await runtime.run(question="查询 MAT001 库存")
        return result, registry, resolved_handler, runtime, orchestrator
    orchestration = await orchestrator.execute("查询 MAT001 / MAT002 库存")
    return orchestration, registry, resolved_handler, runtime, orchestrator


# ============================================================
# §三十一 / §三十二：真实 Run 只读 + 不修改数据库
# ============================================================


class TestAgentReadDatabaseIntegration:
    async def test_terminal_run_is_readable_and_reads_write_nothing(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        orchestration, registry, handler, _runtime, _orchestrator = (
            await _execute_run([_TOOL_CALL_1, _FINAL])
        )
        run_id = orchestration.data.run_id
        cleanup.append(run_id)
        assert orchestration.metadata["agent_status"] == AGENT_STATUS_COMPLETED
        assert len(registry.execute_calls) == 1

        before = _row_counts(engine, run_id)
        service = _read_service()

        view = service.get_run(run_id, _TOOL_PROJECT)
        timeline = service.get_timeline(run_id, _TOOL_PROJECT, limit=100)
        checkpoint = service.get_checkpoint(run_id, _TOOL_PROJECT)
        approval = service.get_approval(run_id, _TOOL_PROJECT)
        paged = service.get_timeline(run_id, _TOOL_PROJECT, limit=1, offset=1)

        after = _row_counts(engine, run_id)

        # §三十一：只读 —— 六表行数不变
        assert before == after
        # Run 摘要（status 来自 agent_run）
        assert view.run is not None
        assert view.run.status == AGENT_STATUS_COMPLETED
        assert view.run.tool_call_count == 1
        assert view.run.llm_call_count == 2
        assert view.run.project_id is None  # 测试装配未提供 project context
        assert len(view.steps) == 2
        assert len(view.decisions) == 2
        assert len(view.tool_results) == 1
        assert view.tool_results[0].success is True
        assert view.tool_results[0].result_hash is not None
        assert checkpoint is not None and checkpoint.status == "TERMINAL"
        assert approval is None  # 无 HITL
        # Timeline（deterministic；终态事件）
        types = [event.event_type for event in timeline]
        assert types[0] == "RUN_STARTED"
        assert "RUN_COMPLETED" in types
        # 终态事件**不保证**排在最后：agent_run.finished_at 是应用时钟，
        # agent_step.created_at 是 DB 时钟（写入时刻），二者可能交叉 ——
        # Timeline 只按 occurred_at 排序（不重排 / 不修正）。
        assert "TOOL_EXECUTED" in types
        assert "DECISION_RECORDED" in types
        assert types.count("STEP_COMPLETED") == 2
        assert [event.sequence for event in timeline] == list(range(len(timeline)))
        assert paged == timeline[1:2]
        # 唯一 handler 调用（读模型**不**触发 Tool）
        assert handler.calls == [{"material_code": "MAT001"}]

    async def test_repeated_reads_are_stable(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        orchestration, _registry, _handler, _runtime, _orchestrator = (
            await _execute_run([_TOOL_CALL_1, _FINAL])
        )
        run_id = orchestration.data.run_id
        cleanup.append(run_id)
        service = _read_service()

        first = service.get_timeline(run_id, _TOOL_PROJECT, limit=100)
        second = service.get_timeline(run_id, _TOOL_PROJECT, limit=100)

        assert first == second  # deterministic（与 DB 自然顺序无关）

    async def test_pagination_on_real_rows_is_lossless(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        orchestration, _registry, _handler, _runtime, _orchestrator = (
            await _execute_run([_TOOL_CALL_1, _FINAL])
        )
        run_id = orchestration.data.run_id
        cleanup.append(run_id)
        service = _read_service()
        full = service.get_timeline(run_id, _TOOL_PROJECT, limit=100)

        pages = [
            service.get_timeline(run_id, _TOOL_PROJECT, limit=2, offset=offset)
            for offset in range(0, len(full), 2)
        ]

        assert tuple(event for page in pages for event in page) == full
        assert [event.sequence for event in full] == list(range(len(full)))

    def test_limit_over_cap_is_rejected(self, engine: Any) -> None:
        service = _read_service()
        with pytest.raises(ValueError):
            service.get_timeline("any-run", settings.project.project_id, limit=101)


# ============================================================
# §三十：project 隔离（真实行）
# ============================================================


class TestAgentReadProjectIsolationDb:
    def _seed_run(self, run_id: str, project_id: str) -> None:
        from datetime import datetime, timezone

        from backend.app.db.agent_tool_result_repository import (
            AgentToolResultRow as Row,
        )

        now = datetime.now(timezone.utc)
        AgentRunRepository().create(
            run_id=run_id,
            request_id=f"req-{run_id}",
            project_id=project_id,
            status="COMPLETED",
            error_code=None,
            limit_reason=None,
            step_count=1,
            tool_call_count=1,
            llm_call_count=1,
            token_usage_unverifiable=False,
            started_at=now,
            finished_at=now,
            duration_ms=1.0,
        )
        AgentToolResultRepository().create(
            Row(
                run_id=run_id,
                step_index=0,
                tool_name=_TOOL_NAME,
                success=True,
                result_size_bytes=1,
                result_truncated=False,
                result_unavailable=False,
                project_id=project_id,
                tool_call_id="call-seed",
            )
        )

    def test_project_matrix_on_real_rows(self, engine: Any, cleanup: list[str]) -> None:
        run_a, run_b = _run_id(), _run_id()
        cleanup.extend([run_a, run_b])
        self._seed_run(run_a, "project-A")
        self._seed_run(run_b, "project-B")
        service = _read_service()

        assert service.get_run(run_a, "project-A").run is not None
        assert service.get_run(run_b, "project-B").run is not None
        with pytest.raises(AgentRunNotFoundError):
            service.get_run(run_a, "project-B")
        with pytest.raises(AgentRunNotFoundError):
            service.get_run(run_b, "project-A")
        # Timeline 同样隔离
        with pytest.raises(AgentRunNotFoundError):
            service.get_timeline(run_a, "project-B")
        assert service.get_timeline(run_a, "project-A", limit=10)

    def test_project_scoped_reads_do_not_touch_other_run(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        run_a, run_b = _run_id(), _run_id()
        cleanup.extend([run_a, run_b])
        self._seed_run(run_a, "project-A")
        self._seed_run(run_b, "project-B")
        service = _read_service()

        before_a = _row_counts(engine, run_a)
        before_b = _row_counts(engine, run_b)
        service.get_run(run_a, "project-A")
        with pytest.raises(AgentRunNotFoundError):
            service.get_run(run_b, "project-A")

        assert _row_counts(engine, run_a) == before_a
        assert _row_counts(engine, run_b) == before_b


# ============================================================
# §三十二：HITL 暂停 / CANCELLED 的可读性
# ============================================================


class TestAgentReadCancelAndPauseDb:
    async def test_paused_run_is_not_readable_until_tool_result_exists(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        """HITL 暂停：**无** project 归属来源 ⇒ 404（fail-closed；已知限制）。"""
        paused, _registry, _handler, _runtime, orchestrator = (
            await _execute_run([_TOOL_CALL_1, _FINAL], approval_required=(_TOOL_NAME,))
        )
        run_id = paused.run_id
        cleanup.append(run_id)
        assert paused.status == AGENT_STATUS_WAITING_APPROVAL
        counts = _row_counts(engine, run_id)
        assert counts["agent_approval"] == 1
        assert counts["agent_checkpoint"] == 1
        assert counts["agent_run"] == 0  # 尚未终态

        service = _read_service()
        with pytest.raises(AgentRunNotFoundError):
            service.get_run(run_id, _TOOL_PROJECT)
        with pytest.raises(AgentRunNotFoundError):
            service.get_timeline(run_id, _TOOL_PROJECT)
        assert _row_counts(engine, run_id) == counts  # 只读失败也**不**写

        # 批准 + Resume ⇒ Tool 执行 ⇒ Tool Result 带 project ⇒ 立即可读
        await orchestrator.resolve_agent_approval(
            paused.approval_id, decision="APPROVED"
        )
        resumed = await orchestrator.resume_agent_run(
            run_id, question="查询 MAT001 库存"
        )

        assert resumed.content == "库存 120 件。"
        view = service.get_run(run_id, _TOOL_PROJECT)
        assert view.run is not None and view.run.status == AGENT_STATUS_COMPLETED
        assert len(view.tool_results) == 1
        assert view.tool_results[0].tool_name == _TOOL_NAME
        # 归属证据在仓储行上（视图按 §十二 **不**暴露 project_id）
        stored = AgentToolResultRepository().list_for_run(run_id)
        assert stored[0].project_id == _TOOL_PROJECT
        assert view.steps  # Step 投影可读
        with pytest.raises(AgentRunNotFoundError):
            service.get_run(run_id, _OTHER_PROJECT)

    async def test_cancelled_run_is_readable_with_cancelled_status(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        holder: dict[str, Any] = {}
        handler = _CancellingHandler(
            _LateBoundRecorder(lambda: holder["recorder"]), on_call=2
        )
        service_, registry, resolved_handler = _execution_service(handler)
        checkpoint_recorder = BestEffortAgentCheckpointRecorder(
            AgentCheckpointRepository()
        )
        tool_result_repository = AgentToolResultRepository()
        runtime = AgentRuntime(
            llm=FakeLLM([_TOOL_CALL_1, _TOOL_CALL_2, _FINAL]),
            tool_execution=service_,
            policy=AgentRuntimePolicy(
                max_execution_time_ms=30_000, max_cost_tokens=1_000
            ),
            allowed_tools=(_TOOL_NAME,),
            tool_result_sink=AgentToolResultWritePoint(
                recorder=BestEffortAgentToolResultRecorder(
                    repository=tool_result_repository
                )
            ),
            checkpoint_sink=checkpoint_recorder,
            checkpoint_store=checkpoint_recorder,
            decision_store=AgentDecisionStore(AgentDecisionRepository()),
            tool_result_store=tool_result_repository,
        )
        holder["recorder"] = checkpoint_recorder
        orchestrator = AIOrchestratorService(
            router=_AgentRouteRouter(),  # type: ignore[arg-type]
            agent_runtime=runtime,
            agent_checkpoint_recorder=checkpoint_recorder,
            agent_run_recorder=BestEffortAgentRunRecorder(AgentRunRepository()),
            agent_step_recorder=BestEffortAgentStepRecorder(AgentStepRepository()),
        )

        orchestration = await orchestrator.execute("查询 MAT001 / MAT002 库存")
        run_id = orchestration.data.run_id
        cleanup.append(run_id)

        assert orchestration.metadata["agent_status"] == AGENT_STATUS_CANCELLED
        view = _read_service().get_run(run_id, _TOOL_PROJECT)
        timeline = _read_service().get_timeline(run_id, _TOOL_PROJECT, limit=100)

        assert view.run is not None
        assert view.run.status == AGENT_STATUS_CANCELLED  # **不**映射成 FAILED
        assert view.checkpoint is not None
        assert view.checkpoint.cancel_requested is True
        assert view.checkpoint.next_action == "CANCELLED"
        types = [event.event_type for event in timeline]
        assert "RUN_CANCELLED" in types
        assert "RUN_FAILED" not in types
        assert types.count("TOOL_EXECUTED") == 2  # Tool Result 保留
        assert len(resolved_handler.calls) == 2

    async def test_cancelled_run_state_unchanged_by_reads(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        orchestration, _registry, _handler, _runtime, _orchestrator = (
            await _execute_run([_TOOL_CALL_1, _FINAL])
        )
        run_id = orchestration.data.run_id
        cleanup.append(run_id)
        before = _row_counts(engine, run_id)
        before_run = AgentRunRepository().get(run_id)
        before_checkpoint = AgentCheckpointRepository().get(run_id)

        service = _read_service()
        service.get_run(run_id, _TOOL_PROJECT)
        service.get_timeline(run_id, _TOOL_PROJECT, limit=100)

        assert _row_counts(engine, run_id) == before
        after_run = AgentRunRepository().get(run_id)
        after_checkpoint = AgentCheckpointRepository().get(run_id)
        assert after_run == before_run
        assert after_checkpoint == before_checkpoint  # state_version 未变


# ============================================================
# §三十八：残留 = 0
# ============================================================


class TestAgentReadResidue:
    def test_residue_is_zero(self, engine: Any) -> None:
        from sqlalchemy import text
        from sqlalchemy.orm import Session

        with Session(engine) as session, session.begin():
            for table in _OWN_TABLES:
                session.execute(
                    text(f"DELETE FROM ai_ops.{table} WHERE run_id LIKE :prefix"),
                    {"prefix": f"{_RUN_PREFIX}%"},
                )

        from sqlalchemy import text as text_

        with engine.connect() as connection:
            counts = {
                table: int(
                    connection.execute(
                        text_(f"SELECT COUNT(*) FROM ai_ops.{table}")
                    ).scalar_one()
                )
                for table in _OWN_TABLES
            }
        assert all(value == 0 for value in counts.values()), counts
