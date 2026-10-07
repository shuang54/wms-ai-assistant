"""Phase 5 P-2 — AgentRun Persistence **DB** 测试（真 PostgreSQL）。

**默认跳过**（沿用项目既有 ``RUN_DB_TESTS`` 门控）：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_agent_run_persistence_db.py
```

验证（任务书 §二十六）：

```text
create_all（仅 agent_run 表；幂等）
    ↓ insert（Repository 幂等写入）
    ↓ query（字段 / 状态 / 计数 / token flag / 时间）
    ↓ UNIQUE(run_id) · INDEX(request_id) · INDEX(started_at) 存在
    ↓ 重复 run_id ⇒ 仍 1 行（first-write-wins）
    ↓ 收尾：删除本次测试写入的行 ⇒ agent_run residue = 0
```

边界：不修改既有表 / 索引 / baseline；不 skip / 不 xfail 绕过残留；
不新增 Read API / 不新增 delete API（清理只发生在测试自身的收尾）。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.agent import (
    AGENT_ERROR_LIMIT_REACHED,
    AGENT_STATUS_COMPLETED,
    AGENT_STATUS_LIMIT_REACHED,
    AgentRuntimeResult,
)
from backend.app.agent.recorder import (
    BestEffortAgentRunRecorder,
    agent_run_record_of,
)
from backend.app.db.agent_run_repository import AgentRunRepository
from backend.app.db.base import Base
from backend.app.db.models import AgentRun
from backend.app.db.models.agent_run import (
    AGENT_RUN_REQUEST_ID_INDEX,
    AGENT_RUN_RUN_ID_INDEX,
    AGENT_RUN_SCHEMA,
    AGENT_RUN_STARTED_AT_INDEX,
    AGENT_RUN_TABLE,
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
    reason="set RUN_DB_TESTS=1 to enable agent_run PostgreSQL persistence tests",
)

__all__ = ["TestAgentRunTable", "TestAgentRunInsert"]

_PROJECT_ID = "phase5-persistence-db"
_STARTED = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def engine() -> Any:
    """真实 Engine（DATABASE_URL 未配置 ⇒ skip）。"""
    resolved = get_engine()
    if resolved is None:
        pytest.skip("DATABASE_URL 未配置（DB 测试跳过）")
    return resolved


@pytest.fixture(scope="module", autouse=True)
def _ensure_table(engine: Any) -> Iterator[None]:
    """幂等建表（**只** agent_run；checkfirst=True ⇒ 已存在不重复创建）。"""
    Base.metadata.create_all(
        bind=engine, tables=[AgentRun.__table__], checkfirst=True
    )
    yield


@pytest.fixture()
def cleanup(engine: Any) -> Iterator[list[str]]:
    """收集本次测试写入的 run_id；收尾删除（保证 agent_run residue = 0）。"""
    run_ids: list[str] = []
    yield run_ids
    if not run_ids:
        return
    with Session(engine) as session, session.begin():
        session.execute(
            text(f"DELETE FROM {AGENT_RUN_SCHEMA}.{AGENT_RUN_TABLE} WHERE run_id = ANY(:ids)"),
            {"ids": run_ids},
        )


def _result(*, status: str = AGENT_STATUS_COMPLETED, **kwargs: Any) -> AgentRuntimeResult:
    payload: dict[str, Any] = {
        "status": status,
        "content": "回答" if status == AGENT_STATUS_COMPLETED else None,
        "steps": 2,
        "tool_calls": 1,
        "llm_calls": 2,
        "error": None,
        "run_id": f"run-db-{uuid.uuid4().hex[:16]}",
        "tokens_used": 10,
        "token_budget_unverifiable": False,
    }
    payload.update(kwargs)
    return AgentRuntimeResult(**payload)


def _record(
    result: AgentRuntimeResult, *, request_id: str | None = None
) -> Any:
    return agent_run_record_of(
        result,
        request_id=request_id or f"req-db-{uuid.uuid4().hex[:16]}",
        project_id=_PROJECT_ID,
        started_at=_STARTED,
        finished_at=_STARTED + timedelta(milliseconds=250),
        duration_ms=250.0,
    )


def _rows(engine: Any, run_id: str) -> list[AgentRun]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(AgentRun).where(AgentRun.run_id == run_id)
            )
        )


def _index_names(engine: Any) -> set[str]:
    with engine.connect() as conn:
        rows = list(
            conn.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes"
                    " WHERE schemaname = :schema AND tablename = :table"
                ),
                {"schema": AGENT_RUN_SCHEMA, "table": AGENT_RUN_TABLE},
            )
        )
    return {row[0] for row in rows}


class TestAgentRunTable:
    def test_table_and_indexes_exist(self, engine: Any) -> None:
        names = _index_names(engine)

        assert AGENT_RUN_RUN_ID_INDEX in names
        assert AGENT_RUN_REQUEST_ID_INDEX in names
        assert AGENT_RUN_STARTED_AT_INDEX in names

    def test_run_id_index_is_unique(self, engine: Any) -> None:
        with engine.connect() as conn:
            definition = conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes"
                    " WHERE schemaname = :schema AND indexname = :name"
                ),
                {"schema": AGENT_RUN_SCHEMA, "name": AGENT_RUN_RUN_ID_INDEX},
            ).scalar_one()

        assert "UNIQUE" in definition.upper()

    def test_table_has_no_foreign_keys(self, engine: Any) -> None:
        """OD-P5-25：无 FK（request_id / project_id 仅为 correlation 字符串）。"""
        with engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.table_constraints"
                    " WHERE constraint_type = 'FOREIGN KEY'"
                    "   AND table_schema = :schema AND table_name = :table"
                ),
                {"schema": AGENT_RUN_SCHEMA, "table": AGENT_RUN_TABLE},
            ).scalar_one()

        assert count == 0


class TestAgentRunInsert:
    def test_insert_and_query_completed(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        result = _result()
        cleanup.append(result.run_id)
        recorder = BestEffortAgentRunRecorder()
        record = _record(result)

        created_id = recorder.record(
            result,
            request_id=record.request_id,
            project_id=record.project_id,
            started_at=record.started_at,
            finished_at=record.finished_at,
            duration_ms=record.duration_ms,
        )

        assert created_id is not None
        rows = _rows(engine, result.run_id)
        assert len(rows) == 1
        row = rows[0]
        assert row.status == AGENT_STATUS_COMPLETED
        assert row.request_id == record.request_id
        assert row.project_id == _PROJECT_ID
        assert (row.step_count, row.tool_call_count, row.llm_call_count) == (2, 1, 2)
        assert row.token_usage_unverifiable is False
        assert row.error_code is None and row.limit_reason is None
        assert row.duration_ms == 250.0
        assert row.created_at is not None

    def test_limit_reached_persists_true_status(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        result = _result(
            status=AGENT_STATUS_LIMIT_REACHED,
            error=AGENT_ERROR_LIMIT_REACHED,
            steps=8,
            tool_calls=8,
            llm_calls=8,
        )
        cleanup.append(result.run_id)
        record = _record(result)

        AgentRunRepository().create(**record.__dict__)  # type: ignore[arg-type]

        row = _rows(engine, result.run_id)[0]
        assert row.status == AGENT_STATUS_LIMIT_REACHED  # 不被改写成 FAILED
        assert row.limit_reason == AGENT_ERROR_LIMIT_REACHED

    def test_duplicate_run_id_keeps_single_row(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        result = _result()
        cleanup.append(result.run_id)
        record = _record(result)
        repository = AgentRunRepository()

        first = repository.create(**record.__dict__)  # type: ignore[arg-type]
        second = repository.create(**record.__dict__)  # type: ignore[arg-type]

        assert first is not None
        assert second is None  # ON CONFLICT (run_id) DO NOTHING
        assert len(_rows(engine, result.run_id)) == 1

    def test_row_contains_no_sensitive_payload(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        result = _result()
        cleanup.append(result.run_id)
        record = _record(result)
        AgentRunRepository().create(**record.__dict__)  # type: ignore[arg-type]

        with Session(engine) as session:
            columns = {column.name for column in AgentRun.__table__.columns}
            row = _rows(engine, result.run_id)[0]
            values = " ".join(
                str(getattr(row, name)) for name in columns
            )

        for forbidden in ("prompt", "context", "SELECT", "sk-", "Authorization"):
            assert forbidden not in values, forbidden
        assert columns == {
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

    def test_residue_is_zero_after_cleanup(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        result = _result()
        cleanup.append(result.run_id)
        record = _record(result)
        AgentRunRepository().create(**record.__dict__)  # type: ignore[arg-type]

        assert len(_rows(engine, result.run_id)) == 1
        # cleanup fixture 在 teardown 删除 ⇒ 这里只断言行确实属于本次测试
        assert result.run_id in cleanup
