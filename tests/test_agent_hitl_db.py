"""Phase 5 P-7 HITL 审批 —— **DB** 测试（真 PostgreSQL）。

**默认跳过**（沿用项目既有 ``RUN_DB_TESTS`` 门控）：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_agent_hitl_db.py
```

验证（任务书 §十 / §十一 / §十四 / §十六 / §十八 / §十九 / §五十九 / §六十六 / §六十七）：

```text
schema        ai_ops.agent_approval：columns · types · UNIQUE(approval_id) ·
              UNIQUE(run_id, step_index) · 索引 · foreign keys = 0
repository    create（PENDING）· get · get_for_step · 重复请求幂等（1 行）·
              条件裁决（APPROVED / REJECTED）· 幂等 / 冲突 · 触达 DB 前校验
E2E          真 Runtime + 真 AgentApprovalStore + 真 Repository + 真 Checkpoint：
              WAITING_APPROVAL ⇒ Tool=0 / Tool Result=0 / **AgentRun=0**（§十五 / §十六）；
              APPROVED → Resume ⇒ Tool=1 · COMPLETED · AgentRun=1；
              REJECTED → Resume ⇒ FAILED + AGENT_APPROVAL_REJECTED · Tool=0
residue      本文件写入的 6 张表清理后 = 0（不改既有残留守卫）
```

边界：**不**调用 DeepSeek / 不调用真实业务 Tool（Fake LLM + test-local read-only handler）。
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Iterator

import pytest

from backend.app.agent import (
    AGENT_ERROR_APPROVAL_REJECTED,
    AGENT_STATUS_COMPLETED,
    AGENT_STATUS_FAILED,
    AGENT_STATUS_WAITING_APPROVAL,
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentApprovalStore,
    AgentDecision,
    AgentRuntime,
    AgentRuntimePolicy,
    approval_request_of,
)
from backend.app.agent.checkpoint import BestEffortAgentCheckpointRecorder
from backend.app.agent.decision_store import AgentDecisionStore
from backend.app.agent.recorder import (
    AgentToolResultWritePoint,
    BestEffortAgentRunRecorder,
    BestEffortAgentStepRecorder,
    BestEffortAgentToolResultRecorder,
)
from backend.app.db.agent_approval_repository import (
    AgentApprovalRepository,
    AgentApprovalRepositoryError,
)
from backend.app.db.agent_checkpoint_repository import AgentCheckpointRepository
from backend.app.db.agent_decision_repository import AgentDecisionRepository
from backend.app.db.agent_run_repository import AgentRunRepository
from backend.app.db.agent_step_repository import AgentStepRepository
from backend.app.db.agent_tool_result_repository import AgentToolResultRepository
from backend.app.db.base import Base
from backend.app.db.models import AgentApproval as AgentApprovalModel
from backend.app.db.models.agent_approval import (
    AGENT_APPROVAL_IDENTITY_INDEX,
    AGENT_APPROVAL_ID_INDEX,
    AGENT_APPROVAL_SCHEMA,
    AGENT_APPROVAL_TABLE,
)
from backend.app.db.session import get_engine
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorExecutionError,
    AIOrchestratorService,
)
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
    reason="set RUN_DB_TESTS=1 to enable agent_approval PostgreSQL persistence tests",
)

__all__ = [
    "TestAgentApprovalTable",
    "TestAgentApprovalRepositoryDb",
    "TestAgentHitlDatabase",
    "TestAgentApprovalResidue",
]

_RUN_PREFIX = "run-p7-approval-db-"

#: 本文件写入的表（清理后必须 residue = 0）。
_OWN_TABLES: tuple[str, ...] = (
    "agent_approval",
    "agent_checkpoint",
    "agent_decision",
    "agent_tool_result",
    "agent_step",
    "agent_run",
)

_TOOL_CALL = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_TOOL_NAME,
    arguments={"material_code": "MAT001"},
    tool_call_id="call_p7_db_1",
)
_FINAL = AgentDecision(type=DECISION_FINAL, content="库存 120 件。")


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
def _ensure_table(engine: Any) -> Iterator[None]:
    """幂等建表（本文件写入的 6 张表；checkfirst=True ⇒ 已存在不重复创建）。"""
    from backend.app.db.models import (
        AgentCheckpoint as AgentCheckpointModel,
        AgentDecision as AgentDecisionModel,
        AgentRun as AgentRunModel,
        AgentStep as AgentStepModel,
        AgentToolResult as AgentToolResultModel,
    )

    Base.metadata.create_all(
        bind=engine,
        tables=[
            AgentApprovalModel.__table__,
            AgentCheckpointModel.__table__,
            AgentDecisionModel.__table__,
            AgentToolResultModel.__table__,
            AgentStepModel.__table__,
            AgentRunModel.__table__,
        ],
        checkfirst=True,
    )
    # P-7：本项目无 Alembic ⇒ 已存在的 agent_checkpoint 需幂等补列（非破坏性）
    ensure_p7_checkpoint_columns(engine)
    yield
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(engine) as session, session.begin():
        session.execute(
            text(
                f"DELETE FROM {AGENT_APPROVAL_SCHEMA}.{AGENT_APPROVAL_TABLE}"
                " WHERE run_id LIKE :prefix"
            ),
            {"prefix": f"{_RUN_PREFIX}%"},
        )


@pytest.fixture()
def cleanup(engine: Any) -> Iterator[list[str]]:
    """记录 run_id；结束时按 run_id 精确删除本文件写入的 6 张表。"""
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


def _hitl_env() -> tuple[Any, Any, Any, AIOrchestratorService, Any, Any]:
    """真 AgentRuntime + 真 Repository（Approval / Checkpoint / Decision / Result）。"""
    service, registry, handler = _execution_service()
    checkpoint_repository = AgentCheckpointRepository()
    checkpoint_recorder = BestEffortAgentCheckpointRecorder(checkpoint_repository)
    approval_store = AgentApprovalStore(AgentApprovalRepository())
    decision_store = AgentDecisionStore(AgentDecisionRepository())
    tool_result_repository = AgentToolResultRepository()
    runtime = AgentRuntime(
        llm=FakeLLM([_TOOL_CALL, _FINAL]),
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
        decision_store=decision_store,
        tool_result_store=tool_result_repository,
        approval_port=approval_store,
        approval_required_tools=(_TOOL_NAME,),
    )
    orchestrator = AIOrchestratorService(
        router=_AgentRouteRouter(),  # type: ignore[arg-type]
        agent_runtime=runtime,
        agent_approval_store=approval_store,
        agent_checkpoint_recorder=checkpoint_recorder,
        # 终态执行记录（AgentRun / AgentStep）：与生产装配一致（真实仓储）
        agent_run_recorder=BestEffortAgentRunRecorder(AgentRunRepository()),
        agent_step_recorder=BestEffortAgentStepRecorder(AgentStepRepository()),
    )
    return (
        runtime,
        approval_store,
        checkpoint_repository,
        orchestrator,
        registry,
        handler,
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
# §十：表结构
# ============================================================


class TestAgentApprovalTable:
    def test_table_columns_and_types(self, engine: Any) -> None:
        from sqlalchemy import text

        with engine.connect() as connection:
            columns = {
                name: data_type
                for name, data_type in connection.execute(
                    text(
                        "SELECT column_name, data_type FROM information_schema.columns"
                        " WHERE table_schema = :schema AND table_name = :table"
                    ),
                    {
                        "schema": AGENT_APPROVAL_SCHEMA,
                        "table": AGENT_APPROVAL_TABLE,
                    },
                )
            }

        assert set(columns) == {
            "id",
            "approval_id",
            "run_id",
            "step_index",
            "tool_call_id",
            "tool_name",
            "status",
            "decision",
            "reason_code",
            "created_at",
            "resolved_at",
        }
        assert columns["id"] == "bigint"
        assert columns["approval_id"] == "character varying"
        assert columns["run_id"] == "character varying"
        assert columns["step_index"] == "integer"
        assert columns["tool_call_id"] == "character varying"
        assert columns["tool_name"] == "character varying"
        assert columns["status"] == "character varying"
        assert columns["decision"] == "character varying"
        assert columns["created_at"] == "timestamp with time zone"
        assert columns["resolved_at"] == "timestamp with time zone"

    def test_unique_identity_and_no_foreign_keys(self, engine: Any) -> None:
        from sqlalchemy import text

        with engine.connect() as connection:
            definitions = {
                name: definition
                for name, definition in connection.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes"
                        " WHERE schemaname = :schema AND tablename = :table"
                    ),
                    {
                        "schema": AGENT_APPROVAL_SCHEMA,
                        "table": AGENT_APPROVAL_TABLE,
                    },
                )
            }
            foreign_keys = list(
                connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint c"
                        " JOIN pg_class t ON t.oid = c.conrelid"
                        " JOIN pg_namespace n ON n.oid = t.relnamespace"
                        " WHERE n.nspname = :schema AND t.relname = :table"
                        " AND c.contype = 'f'"
                    ),
                    {
                        "schema": AGENT_APPROVAL_SCHEMA,
                        "table": AGENT_APPROVAL_TABLE,
                    },
                )
            )

        unique_definitions = {
            name: definition
            for name, definition in definitions.items()
            if "UNIQUE" in definition.upper()
        }
        assert set(unique_definitions) == {
            f"{AGENT_APPROVAL_TABLE}_pkey",
            AGENT_APPROVAL_ID_INDEX,
            AGENT_APPROVAL_IDENTITY_INDEX,
        }
        assert "run_id" in unique_definitions[AGENT_APPROVAL_IDENTITY_INDEX]
        assert "step_index" in unique_definitions[AGENT_APPROVAL_IDENTITY_INDEX]
        # tool_call_id **不是**唯一键（protocol correlation；§十二）
        for definition in unique_definitions.values():
            assert "tool_call_id" not in definition
        assert foreign_keys == []  # §七：NO FOREIGN KEY


# ============================================================
# §十 / §十八 / §十九：仓储（幂等 / 裁决 / 冲突）
# ============================================================


class TestAgentApprovalRepositoryDb:
    def test_create_get_and_idempotent_duplicate(
        self, cleanup: list[str]
    ) -> None:
        repository = AgentApprovalRepository()
        run_id = _run_id()
        cleanup.append(run_id)

        first = repository.create(
            approval_id=str(uuid.uuid4()),
            run_id=run_id,
            step_index=0,
            tool_name=_TOOL_NAME,
            tool_call_id="call-db-1",
        )
        second = repository.create(
            approval_id=str(uuid.uuid4()),
            run_id=run_id,
            step_index=0,
            tool_name=_TOOL_NAME,
            tool_call_id="call-db-1",
        )

        assert first.status == APPROVAL_STATUS_PENDING
        assert first.decision is None and first.resolved_at is None
        assert second.approval_id == first.approval_id  # 重复请求 ⇒ 同一行
        assert repository.get(first.approval_id).approval_id == first.approval_id
        fetched = repository.get_for_step(run_id=run_id, step_index=0)
        assert fetched is not None and fetched.approval_id == first.approval_id
        # 无 arguments 列（§十一）
        assert not hasattr(first, "arguments")

    def test_resolve_is_conditional_and_irreversible(
        self, cleanup: list[str]
    ) -> None:
        repository = AgentApprovalRepository()
        run_id = _run_id()
        cleanup.append(run_id)
        row = repository.create(
            approval_id=str(uuid.uuid4()),
            run_id=run_id,
            step_index=0,
            tool_name=_TOOL_NAME,
        )

        approved = repository.resolve(
            approval_id=row.approval_id, decision="APPROVED"
        )
        assert approved is not None
        assert approved.status == APPROVAL_STATUS_APPROVED
        assert approved.decision == APPROVAL_STATUS_APPROVED
        assert approved.resolved_at is not None
        # 幂等（同裁决）与冲突（不同裁决）都**不**产生第二次转换
        assert (
            repository.resolve(approval_id=row.approval_id, decision="APPROVED")
            is None
        )
        assert (
            repository.resolve(approval_id=row.approval_id, decision="REJECTED")
            is None
        )
        assert (
            repository.get(row.approval_id).status == APPROVAL_STATUS_APPROVED
        )

    def test_reject_after_pending(self, cleanup: list[str]) -> None:
        repository = AgentApprovalRepository()
        run_id = _run_id()
        cleanup.append(run_id)
        row = repository.create(
            approval_id=str(uuid.uuid4()),
            run_id=run_id,
            step_index=1,
            tool_name=_TOOL_NAME,
        )

        rejected = repository.resolve(
            approval_id=row.approval_id, decision="REJECTED"
        )

        assert rejected is not None
        assert rejected.status == APPROVAL_STATUS_REJECTED
        assert (
            repository.resolve(approval_id=row.approval_id, decision="APPROVED")
            is None
        )

    def test_missing_approval(self) -> None:
        repository = AgentApprovalRepository()
        assert repository.get(str(uuid.uuid4())) is None
        assert repository.get_for_step(run_id=_run_id(), step_index=0) is None
        assert (
            repository.resolve(
                approval_id=str(uuid.uuid4()), decision="APPROVED"
            )
            is None
        )

    @pytest.mark.parametrize(
        "overrides",
        [
            {"approval_id": ""},
            {"approval_id": "x" * 129},
            {"run_id": "x" * 65},
            {"step_index": -1},
            {"step_index": True},
            {"tool_name": ""},
            {"tool_call_id": "x" * 129},
        ],
    )
    def test_validation_fails_before_db(
        self, overrides: dict[str, Any]
    ) -> None:
        repository = AgentApprovalRepository(
            session_factory=_ExplodingSessionFactory()  # type: ignore[arg-type]
        )
        payload: dict[str, Any] = {
            "approval_id": str(uuid.uuid4()),
            "run_id": _run_id(),
            "step_index": 0,
            "tool_name": _TOOL_NAME,
            "tool_call_id": None,
        }
        payload.update(overrides)

        with pytest.raises(ValueError):
            repository.create(**payload)

    @pytest.mark.parametrize(
        "bad", ["retry", "modify", "edit_tool", "change_arguments", ""]
    )
    def test_illegal_decisions_rejected(self, bad: str) -> None:
        repository = AgentApprovalRepository(
            session_factory=_ExplodingSessionFactory()  # type: ignore[arg-type]
        )
        with pytest.raises(ValueError):
            repository.resolve(approval_id=str(uuid.uuid4()), decision=bad)

    def test_missing_database_is_repository_error(self) -> None:
        repository = AgentApprovalRepository()
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                "backend.app.db.agent_approval_repository.get_session_factory",
                lambda: None,
            )
            with pytest.raises(AgentApprovalRepositoryError):
                repository.get(str(uuid.uuid4()))


class _ExplodingSessionFactory:
    def __call__(self) -> Any:
        raise AssertionError("校验必须在触达 DB 之前失败")


# ============================================================
# §十四 / §十五 / §十六 / §二十 / §二十一：E2E（真 DB 全链）
# ============================================================


class TestAgentHitlDatabase:
    async def test_waiting_approval_writes_no_agent_run(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        runtime, approval_store, _checkpoints, _orchestrator, _registry, _handler = (
            _hitl_env()
        )

        result = await runtime.run(question="查询 MAT001 库存")
        cleanup.append(result.run_id)

        assert result.status == AGENT_STATUS_WAITING_APPROVAL
        counts = _row_counts(engine, result.run_id)
        assert counts["agent_approval"] == 1
        assert counts["agent_checkpoint"] == 1
        assert counts["agent_decision"] == 1
        # §十五 / §十六：Tool 未执行、结果未产生、Run 未终态
        assert counts["agent_tool_result"] == 0
        assert counts["agent_step"] == 0
        assert counts["agent_run"] == 0
        row = approval_store.get_for_step(run_id=result.run_id, step_index=0)
        assert row is not None and row.status == APPROVAL_STATUS_PENDING
        projection = approval_request_of(row)
        assert projection.approval_id == result.approval_id
        assert not hasattr(projection, "arguments")

    async def test_approved_resume_executes_tool_and_records_run(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        runtime, approval_store, checkpoints, orchestrator, registry, handler = (
            _hitl_env()
        )
        paused = await runtime.run(question="查询 MAT001 库存")
        cleanup.append(paused.run_id)

        resolution = await orchestrator.resolve_agent_approval(
            paused.approval_id, decision="APPROVED"
        )
        assert resolution.error_code is None
        resumed = await orchestrator.resume_agent_run(
            paused.run_id, question="查询 MAT001 库存"
        )

        assert resumed.content == "库存 120 件。"
        assert [name for name, _ in registry.execute_calls] == [_TOOL_NAME]
        assert handler.calls == [{"material_code": "MAT001"}]
        counts = _row_counts(engine, paused.run_id)
        assert counts["agent_approval"] == 1
        assert counts["agent_tool_result"] == 1  # Tool Result 落库
        assert counts["agent_run"] == 1  # Resume 后才产生终态执行记录
        assert counts["agent_step"] >= 1
        row = approval_store.get(paused.approval_id)
        assert row.status == APPROVAL_STATUS_APPROVED
        assert row.resolved_at is not None
        # Checkpoint 终态 = TERMINAL / COMPLETED
        checkpoint = checkpoints.get(paused.run_id)
        assert checkpoint.next_action == "COMPLETED"

    async def test_rejected_resume_fails_without_tool(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        runtime, approval_store, checkpoints, orchestrator, registry, _handler = (
            _hitl_env()
        )
        paused = await runtime.run(question="查询 MAT001 库存")
        cleanup.append(paused.run_id)
        resolution = await orchestrator.resolve_agent_approval(
            paused.approval_id, decision="REJECTED"
        )
        assert resolution.status == APPROVAL_STATUS_REJECTED

        with pytest.raises(AIOrchestratorExecutionError) as excinfo:
            await orchestrator.resume_agent_run(
                paused.run_id, question="查询 MAT001 库存"
            )

        assert AGENT_ERROR_APPROVAL_REJECTED in str(excinfo.value)
        assert registry.execute_calls == []
        counts = _row_counts(engine, paused.run_id)
        assert counts["agent_tool_result"] == 0  # §二十二
        assert counts["agent_run"] == 1  # 终态执行记录：FAILED
        row = approval_store.get(paused.approval_id)
        assert row.status == APPROVAL_STATUS_REJECTED
        checkpoint = checkpoints.get(paused.run_id)
        assert checkpoint.next_action == "FAILED"

    async def test_pending_resume_keeps_waiting(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        runtime, _approval_store, _checkpoints, orchestrator, registry, _handler = (
            _hitl_env()
        )
        paused = await runtime.run(question="查询 MAT001 库存")
        cleanup.append(paused.run_id)

        result = await orchestrator.resume_agent_run(
            paused.run_id, question="查询 MAT001 库存"
        )

        assert result.metadata["agent_status"] == AGENT_STATUS_WAITING_APPROVAL
        assert result.metadata["agent_approval_id"] == paused.approval_id
        assert registry.execute_calls == []
        counts = _row_counts(engine, paused.run_id)
        assert counts["agent_run"] == 0  # 仍未终态（§十六）


# ============================================================
# §六十七：残留 = 0
# ============================================================


class TestAgentApprovalResidue:
    def test_residue_is_zero(self, engine: Any) -> None:
        from sqlalchemy import text
        from sqlalchemy.orm import Session

        with Session(engine) as session, session.begin():
            session.execute(
                text(
                    f"DELETE FROM {AGENT_APPROVAL_SCHEMA}.{AGENT_APPROVAL_TABLE}"
                    " WHERE run_id LIKE :prefix"
                ),
                {"prefix": f"{_RUN_PREFIX}%"},
            )
            for table in _OWN_TABLES:
                if table == "agent_approval":
                    continue
                session.execute(
                    text(f"DELETE FROM ai_ops.{table} WHERE run_id LIKE :prefix"),
                    {"prefix": f"{_RUN_PREFIX}%"},
                )

        counts = _counts(engine, _OWN_TABLES)
        assert counts["agent_approval"] == 0
        assert all(value == 0 for value in counts.values()), counts
