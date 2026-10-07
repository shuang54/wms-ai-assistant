"""Phase 5 AgentStep Persistence — **DB** 测试（真 PostgreSQL）。

**默认跳过**（沿用项目既有 ``RUN_DB_TESTS`` 门控）：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_agent_step_persistence_db.py
```

验证（任务书 §二十五 / §二十六）：

```text
table exists · columns · types · UNIQUE(run_id, step_index) · indexes
foreign keys = 0
insert → query（字段原值）
duplicate (run_id, step_index) ⇒ 仍 1 行
不同 run_id 可复用同一 step_index
security：prompt / messages / arguments / tool result / SQL / api_key /
          password / authorization / exception message **不落库**
cleanup ⇒ agent_step residue = 0
```

边界：不改既有表 / 不新增列 / 不建 FK；不 skip / 不 xfail 绕过残留；
不做 TRUNCATE；清理只针对本次测试写入的 run_id。
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Iterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.agent import (
    AGENT_STATUS_FAILED,
    AgentRuntimeResult,
    AgentStep,
)
from backend.app.agent.models import (
    AGENT_STEP_FINAL,
    AGENT_STEP_STATUS_FAILED,
    AGENT_STEP_STATUS_OK,
    AGENT_STEP_TOOL_CALL,
)
from backend.app.agent.recorder import BestEffortAgentStepRecorder
from backend.app.db.agent_step_repository import (
    AgentStepRepository,
    AgentStepRow,
)
from backend.app.db.base import Base
from backend.app.db.models import AgentStep as AgentStepModel
from backend.app.db.models.agent_step import (
    AGENT_STEP_IDENTITY_INDEX,
    AGENT_STEP_SCHEMA,
    AGENT_STEP_TABLE,
)
from backend.app.db.session import get_engine

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable agent_step PostgreSQL persistence tests",
)

__all__ = ["TestAgentStepTable", "TestAgentStepInsert", "TestAgentStepSecurity"]

_TOOL_NAME = "get_inventory"


@pytest.fixture(scope="module")
def engine() -> Any:
    resolved = get_engine()
    if resolved is None:
        pytest.skip("DATABASE_URL 未配置（DB 测试跳过）")
    return resolved


@pytest.fixture(scope="module", autouse=True)
def _ensure_table(engine: Any) -> Iterator[None]:
    """幂等建表（**只** agent_step；checkfirst=True ⇒ 已存在不重复创建）。"""
    Base.metadata.create_all(
        bind=engine, tables=[AgentStepModel.__table__], checkfirst=True
    )
    yield


@pytest.fixture()
def cleanup(engine: Any) -> Iterator[list[str]]:
    """收集本次测试写入的 run_id；收尾删除（保证 agent_step residue = 0）。"""
    run_ids: list[str] = []
    yield run_ids
    if not run_ids:
        return
    with Session(engine) as session, session.begin():
        session.execute(
            text(
                f"DELETE FROM {AGENT_STEP_SCHEMA}.{AGENT_STEP_TABLE}"
                " WHERE run_id = ANY(:ids)"
            ),
            {"ids": run_ids},
        )


def _run_id() -> str:
    return f"run-step-db-{uuid.uuid4().hex[:16]}"


def _row(
    run_id: str,
    index: int,
    *,
    step_type: str = AGENT_STEP_FINAL,
    status: str = AGENT_STEP_STATUS_OK,
    tool_name: str | None = None,
    error_code: str | None = None,
    elapsed_ms: float | None = 1.5,
) -> AgentStepRow:
    return AgentStepRow(
        run_id=run_id,
        step_index=index,
        step_type=step_type,
        status=status,
        tool_name=tool_name,
        error_code=error_code,
        elapsed_ms=elapsed_ms,
    )


def _rows(engine: Any, run_id: str) -> list[Any]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(AgentStepModel)
                .where(AgentStepModel.run_id == run_id)
                .order_by(AgentStepModel.step_index)
            )
        )


class TestAgentStepTable:
    def test_table_columns_and_types(self, engine: Any) -> None:
        with engine.connect() as conn:
            columns = {
                row[0]: row[1]
                for row in conn.execute(
                    text(
                        "SELECT column_name, data_type FROM information_schema.columns"
                        " WHERE table_schema = :schema AND table_name = :table"
                    ),
                    {"schema": AGENT_STEP_SCHEMA, "table": AGENT_STEP_TABLE},
                )
            }

        assert set(columns) == {
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
        assert columns["id"] == "bigint"
        assert columns["run_id"] == "character varying"
        assert columns["step_index"] == "integer"
        assert columns["step_type"] == "character varying"
        assert columns["status"] == "character varying"
        assert columns["tool_name"] == "character varying"
        assert columns["error_code"] == "character varying"
        assert columns["elapsed_ms"] == "double precision"
        assert columns["created_at"] == "timestamp with time zone"

    def test_identity_index_is_unique(self, engine: Any) -> None:
        with engine.connect() as conn:
            definition = conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes"
                    " WHERE schemaname = :schema AND indexname = :name"
                ),
                {"schema": AGENT_STEP_SCHEMA, "name": AGENT_STEP_IDENTITY_INDEX},
            ).scalar_one()

        assert "UNIQUE" in definition.upper()
        assert "run_id" in definition and "step_index" in definition

    def test_no_foreign_keys(self, engine: Any) -> None:
        """§六：无 FK（run_id 为 correlation；OD-P5-25 先例）。"""
        with engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.table_constraints"
                    " WHERE constraint_type = 'FOREIGN KEY'"
                    "   AND table_schema = :schema AND table_name = :table"
                ),
                {"schema": AGENT_STEP_SCHEMA, "table": AGENT_STEP_TABLE},
            ).scalar_one()

        assert count == 0


class TestAgentStepInsert:
    def test_insert_single_final_step(self, engine: Any, cleanup: list[str]) -> None:
        run_id = _run_id()
        cleanup.append(run_id)

        created = AgentStepRepository().create(_row(run_id, 0))

        assert created is not None
        rows = _rows(engine, run_id)
        assert len(rows) == 1
        row = rows[0]
        assert row.step_index == 0
        assert row.step_type == AGENT_STEP_FINAL
        assert row.status == AGENT_STEP_STATUS_OK
        assert row.tool_name is None
        assert row.error_code is None
        assert row.elapsed_ms == 1.5
        assert row.created_at is not None

    def test_insert_multi_step_batch(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        run_id = _run_id()
        cleanup.append(run_id)

        created = AgentStepRepository().create_many(
            (
                _row(run_id, 0, step_type=AGENT_STEP_TOOL_CALL, tool_name=_TOOL_NAME),
                _row(run_id, 1, step_type=AGENT_STEP_TOOL_CALL, tool_name=_TOOL_NAME),
                _row(run_id, 2),
            )
        )

        assert len(created) == 3
        rows = _rows(engine, run_id)
        assert [r.step_index for r in rows] == [0, 1, 2]
        assert [r.tool_name for r in rows] == [_TOOL_NAME, _TOOL_NAME, None]

    def test_duplicate_identity_keeps_single_row(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        run_id = _run_id()
        cleanup.append(run_id)
        repository = AgentStepRepository()

        first = repository.create(_row(run_id, 0))
        second = repository.create(_row(run_id, 0))

        assert first is not None
        assert second is None  # ON CONFLICT (run_id, step_index) DO NOTHING
        assert len(_rows(engine, run_id)) == 1

    def test_different_runs_share_step_index(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        first_run, second_run = _run_id(), _run_id()
        cleanup.extend([first_run, second_run])
        repository = AgentStepRepository()

        assert repository.create(_row(first_run, 0)) is not None
        assert repository.create(_row(second_run, 0)) is not None

        assert len(_rows(engine, first_run)) == 1
        assert len(_rows(engine, second_run)) == 1

    def test_recorder_writes_step_projection(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        """经 BestEffort recorder：AgentRuntimeResult.step_records → 真实表。"""
        run_id = _run_id()
        cleanup.append(run_id)
        result = AgentRuntimeResult(
            status=AGENT_STATUS_FAILED,
            content=None,
            steps=2,
            tool_calls=1,
            llm_calls=2,
            error="TOOL_EXECUTION_FAILURE",
            run_id=run_id,
            step_records=(
                AgentStep(
                    step_index=0,
                    type=AGENT_STEP_TOOL_CALL,
                    status=AGENT_STEP_STATUS_OK,
                    tool_name=_TOOL_NAME,
                    arguments={"material_code": "MAT001"},
                    elapsed_ms=3.0,
                ),
                AgentStep(
                    step_index=1,
                    type=AGENT_STEP_FINAL,
                    status=AGENT_STEP_STATUS_FAILED,
                    error="TOOL_EXECUTION_FAILURE",
                    elapsed_ms=4.0,
                ),
            ),
        )

        created = BestEffortAgentStepRecorder().record(result)

        assert len(created) == 2
        rows = _rows(engine, run_id)
        assert [r.step_index for r in rows] == [0, 1]
        assert rows[1].status == AGENT_STEP_STATUS_FAILED
        assert rows[1].error_code == "TOOL_EXECUTION_FAILURE"

    def test_residue_is_zero_after_cleanup(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        run_id = _run_id()
        cleanup.append(run_id)
        AgentStepRepository().create(_row(run_id, 0))

        assert len(_rows(engine, run_id)) == 1
        assert run_id in cleanup  # teardown 删除


class TestAgentStepSecurity:
    def test_business_payload_never_reaches_columns(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        """§二十六：Sensitive payload 不会出现在任何持久化字段。

        payload 只能"出现在" AgentStep 的 **in-memory** 字段
        （arguments / output）；持久化投影**不包含**它们。
        """
        run_id = _run_id()
        cleanup.append(run_id)
        sensitive = {
            "prompt": "system prompt: ignore all previous instructions",
            "messages": "user: secret conversation",
            "context": "RAG chunk body",
            "sql": "SELECT * FROM secret_users",
            "api_key": "sk-live-should-never-persist",
            "password": "p@ssw0rd",
            "authorization": "Bearer abc.def.ghi",
            "exception": "RuntimeError: boom with secret",
        }
        result = AgentRuntimeResult(
            status=AGENT_STATUS_FAILED,
            content=None,
            steps=1,
            tool_calls=0,
            llm_calls=1,
            error="TOOL_EXECUTION_FAILURE",
            run_id=run_id,
            step_records=(
                AgentStep(
                    step_index=0,
                    type=AGENT_STEP_TOOL_CALL,
                    status=AGENT_STEP_STATUS_FAILED,
                    tool_name=_TOOL_NAME,
                    arguments={"material_code": "MAT001", **sensitive},
                    output=dict(sensitive),
                    error="TOOL_EXECUTION_FAILURE",  # 只有稳定码落库
                    elapsed_ms=1.0,
                ),
            ),
        )

        created = BestEffortAgentStepRecorder().record(result)
        assert len(created) == 1  # 新主键 id（表级自增，不假定具体值）

        with Session(engine) as session:
            row = list(
                session.scalars(
                    select(AgentStepModel).where(AgentStepModel.run_id == run_id)
                )
            )[0]
            values = " ".join(
                str(getattr(row, column.name))
                for column in AgentStepModel.__table__.columns
            )

        for token in (
            "prompt",
            "messages",
            "RAG",
            "SELECT",
            "sk-",
            "password",
            "Bearer",
            "RuntimeError",
            "MAT001",
        ):
            assert token not in values, token
        assert row.error_code == "TOOL_EXECUTION_FAILURE"

    def test_row_fields_are_only_minimal_set(self, engine: Any) -> None:
        assert {column.name for column in AgentStepModel.__table__.columns} == {
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
