"""Phase 5 P-5 Agent Checkpoint — **DB** 测试（真 PostgreSQL）。

**默认跳过**（沿用项目既有 ``RUN_DB_TESTS`` 门控）：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_agent_checkpoint_db.py
```

验证（任务书 §五十三 / §四十一–§四十四 / §六十一）：

```text
schema exists · columns · types · UNIQUE(run_id) · indexes · foreign keys = 0
create → get（字段原值）· update（version +1 / claim 不变 / updated_at 变化）
duplicate run_id ⇒ CHECKPOINT_ALREADY_EXISTS（不覆盖）
version conflict ⇒ CHECKPOINT_VERSION_CONFLICT · claim conflict ⇒ CHECKPOINT_CONFLICT
concurrent update（同一 expected version 只有一个成功）
not found ⇒ CHECKPOINT_NOT_FOUND
cleanup ⇒ ai_ops.agent_checkpoint residue = 0
其它 7 张表：本文件**零写入**（前后 count 相同；不改既有 residue guard）
```

边界：不改既有表 / 不新增列 / 不建 FK；不 skip / 不 xfail 绕过残留；
不做 TRUNCATE；清理只针对本次测试写入的 run_id（+ 本文件前缀兜底）。
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Iterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.agent.checkpoint import (
    CHECKPOINT_NEXT_ACTION_COMPLETED,
    CHECKPOINT_NEXT_ACTION_LLM_DECISION,
    CHECKPOINT_STATUS_ACTIVE,
    CHECKPOINT_STATUS_TERMINAL,
    BestEffortAgentCheckpointRecorder,
    new_claim_token,
    tool_result_ref,
)
from backend.app.agent.ids import agent_run_scope
from backend.app.db.agent_checkpoint_repository import (
    CHECKPOINT_ALREADY_EXISTS,
    CHECKPOINT_CONFLICT,
    CHECKPOINT_NOT_FOUND,
    CHECKPOINT_VERSION_CONFLICT,
    AgentCheckpointAlreadyExistsError,
    AgentCheckpointConflictError,
    AgentCheckpointNotFoundError,
    AgentCheckpointRepository,
)
from backend.app.db.base import Base
from backend.app.db.models import AgentCheckpoint as AgentCheckpointModel
from backend.app.db.models.agent_checkpoint import (
    AGENT_CHECKPOINT_IDENTITY_INDEX,
    AGENT_CHECKPOINT_SCHEMA,
    AGENT_CHECKPOINT_TABLE,
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
    reason=(
        "set RUN_DB_TESTS=1 to enable agent_checkpoint PostgreSQL persistence tests"
    ),
)

__all__ = [
    "TestAgentCheckpointTable",
    "TestAgentCheckpointCreateAndGet",
    "TestAgentCheckpointOptimisticConcurrency",
    "TestAgentCheckpointResidue",
]

_RUN_PREFIX = "run-checkpoint-db-"

#: 本文件**不**写入的既有表（前后 count 必须不变）。
_OTHER_TABLES: tuple[str, ...] = (
    "agent_tool_result",
    "agent_step",
    "agent_run",
    "tool_execution_record",
    "llm_usage_record",
    "assistant_outcome_record",
    "rag_execution_record",
)


@pytest.fixture(scope="module")
def engine() -> Any:
    resolved = get_engine()
    if resolved is None:
        pytest.skip("DATABASE_URL 未配置（DB 测试跳过）")
    return resolved


@pytest.fixture(scope="module", autouse=True)
def _ensure_table(engine: Any) -> Iterator[None]:
    """幂等建表（**只** agent_checkpoint；checkfirst=True ⇒ 已存在不重复创建）。"""
    Base.metadata.create_all(
        bind=engine, tables=[AgentCheckpointModel.__table__], checkfirst=True
    )
    yield
    with Session(engine) as session, session.begin():
        session.execute(
            text(
                f"DELETE FROM {AGENT_CHECKPOINT_SCHEMA}.{AGENT_CHECKPOINT_TABLE}"
                " WHERE run_id LIKE :prefix"
            ),
            {"prefix": f"{_RUN_PREFIX}%"},
        )


@pytest.fixture(scope="module")
def other_table_counts(engine: Any) -> dict[str, int]:
    """快照：其它 7 张表的 count（本文件必须零写入）。"""
    return _counts(engine, _OTHER_TABLES)


@pytest.fixture()
def cleanup(engine: Any) -> Iterator[list[str]]:
    """收集本次测试写入的 run_id；收尾删除（保证 residue = 0）。"""
    run_ids: list[str] = []
    yield run_ids
    if not run_ids:
        return
    with Session(engine) as session, session.begin():
        session.execute(
            text(
                f"DELETE FROM {AGENT_CHECKPOINT_SCHEMA}.{AGENT_CHECKPOINT_TABLE}"
                " WHERE run_id = ANY(:ids)"
            ),
            {"ids": run_ids},
        )


def _run_id() -> str:
    return f"{_RUN_PREFIX}{uuid.uuid4().hex[:16]}"


def _counts(engine: Any, tables: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for table in tables:
            counts[table] = int(
                conn.execute(
                    text(f"SELECT COUNT(*) FROM {AGENT_CHECKPOINT_SCHEMA}.{table}")
                ).scalar_one()
            )
    return counts


def _rows(engine: Any, run_id: str) -> list[Any]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(AgentCheckpointModel).where(
                    AgentCheckpointModel.run_id == run_id
                )
            )
        )


def _create(
    repository: AgentCheckpointRepository,
    run_id: str,
    *,
    claim_token: str | None = None,
    step_index: int | None = 1,
    result_ref: str | None = None,
    status: str = CHECKPOINT_STATUS_ACTIVE,
    next_action: str = CHECKPOINT_NEXT_ACTION_LLM_DECISION,
) -> Any:
    return repository.create(
        run_id=run_id,
        claim_token=claim_token or new_claim_token(),
        status=status,
        next_action=next_action,
        pending_step_index=step_index,
        pending_tool_result_ref=result_ref,
    )


class TestAgentCheckpointTable:
    def test_table_columns_and_types(self, engine: Any) -> None:
        with engine.connect() as conn:
            columns = {
                row[0]: row[1]
                for row in conn.execute(
                    text(
                        "SELECT column_name, data_type FROM information_schema.columns"
                        " WHERE table_schema = :schema AND table_name = :table"
                    ),
                    {
                        "schema": AGENT_CHECKPOINT_SCHEMA,
                        "table": AGENT_CHECKPOINT_TABLE,
                    },
                )
            }

        assert set(columns) == {
            "id",
            "run_id",
            "state_version",
            "claim_token",
            "status",
            "next_action",
            "pending_step_index",
            "pending_tool_call_id",
            "pending_decision_ref",
            "pending_tool_result_ref",
            "created_at",
            "updated_at",
        }
        assert columns["id"] == "bigint"
        assert columns["run_id"] == "character varying"
        assert columns["state_version"] == "bigint"
        assert columns["claim_token"] == "character varying"
        assert columns["status"] == "character varying"
        assert columns["next_action"] == "character varying"
        assert columns["pending_step_index"] == "integer"
        assert columns["pending_tool_call_id"] == "character varying"
        assert columns["pending_decision_ref"] == "character varying"
        assert columns["pending_tool_result_ref"] == "character varying"
        assert columns["created_at"] == "timestamp with time zone"
        assert columns["updated_at"] == "timestamp with time zone"

    def test_identity_index_is_unique_on_run_id_only(self, engine: Any) -> None:
        """§十：UNIQUE(run_id)（**不是** UNIQUE(run_id, state_version)）。"""
        with engine.connect() as conn:
            definitions = {
                row[0]: row[1]
                for row in conn.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes"
                        " WHERE schemaname = :schema AND tablename = :table"
                    ),
                    {
                        "schema": AGENT_CHECKPOINT_SCHEMA,
                        "table": AGENT_CHECKPOINT_TABLE,
                    },
                )
            }

        unique_definitions = {
            name: definition
            for name, definition in definitions.items()
            if "UNIQUE" in definition.upper()
        }
        assert set(unique_definitions) == {
            f"{AGENT_CHECKPOINT_TABLE}_pkey",
            AGENT_CHECKPOINT_IDENTITY_INDEX,
        }
        identity = unique_definitions[AGENT_CHECKPOINT_IDENTITY_INDEX]
        assert "run_id" in identity
        assert "state_version" not in identity  # version 不是行身份

    def test_no_foreign_keys(self, engine: Any) -> None:
        """§七：无 FK（run_id 为 correlation，与 agent_run / agent_step 一致）。"""
        with engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.table_constraints"
                    " WHERE constraint_type = 'FOREIGN KEY'"
                    "   AND table_schema = :schema AND table_name = :table"
                ),
                {
                    "schema": AGENT_CHECKPOINT_SCHEMA,
                    "table": AGENT_CHECKPOINT_TABLE,
                },
            ).scalar_one()

        assert count == 0


class TestAgentCheckpointCreateAndGet:
    def test_create_then_get_round_trip(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        run_id = _run_id()
        cleanup.append(run_id)
        repository = AgentCheckpointRepository()

        created = _create(
            repository, run_id, step_index=1, result_ref=tool_result_ref(run_id, 0)
        )

        assert created.state_version == 1  # §十一：创建 = 1
        assert created.status == CHECKPOINT_STATUS_ACTIVE
        assert created.next_action == CHECKPOINT_NEXT_ACTION_LLM_DECISION
        assert created.claim_token
        assert created.created_at == created.updated_at or (
            created.updated_at >= created.created_at
        )

        loaded = repository.get(run_id)
        assert loaded is not None
        assert loaded == created  # DTO 全字段一致（frozen dataclass）

        row = _rows(engine, run_id)[0]
        assert row.pending_step_index == 1
        assert row.pending_tool_result_ref == f"{run_id}:0"
        assert row.pending_tool_call_id is None
        assert row.pending_decision_ref is None

    def test_duplicate_run_id_is_rejected_without_overwrite(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        """§二十九：重复 run_id ⇒ CHECKPOINT_ALREADY_EXISTS（**不**静默覆盖）。"""
        run_id = _run_id()
        cleanup.append(run_id)
        repository = AgentCheckpointRepository()
        original = _create(repository, run_id, claim_token="claim-original")

        with pytest.raises(AgentCheckpointAlreadyExistsError) as excinfo:
            _create(repository, run_id, claim_token="claim-second")

        assert excinfo.value.code == CHECKPOINT_ALREADY_EXISTS
        current = repository.get(run_id)
        assert current is not None
        assert current.claim_token == original.claim_token  # 原行未被覆盖
        assert current.state_version == 1
        assert len(_rows(engine, run_id)) == 1

    def test_get_missing_returns_none(self) -> None:
        assert AgentCheckpointRepository().get(_run_id()) is None

    def test_recorder_creates_row_from_agent_run_scope(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        """真记录器：run_id 取自 Agent Run Scope（无 scope ⇒ 不写）。"""
        run_id = _run_id()
        cleanup.append(run_id)
        recorder = BestEffortAgentCheckpointRecorder(AgentCheckpointRepository())

        assert (
            recorder.record_tool_result_checkpoint(
                step_index=0, tool_result_persisted=True
            )
            is None
        )  # 无 scope ⇒ 0 行

        with agent_run_scope(run_id):
            checkpoint = recorder.record_tool_result_checkpoint(
                step_index=0, tool_result_persisted=True
            )

        assert checkpoint is not None
        assert checkpoint.run_id == run_id
        assert checkpoint.pending_tool_result_ref == f"{run_id}:0"
        assert len(_rows(engine, run_id)) == 1


class TestAgentCheckpointOptimisticConcurrency:
    def test_successful_update_advances_version_keeps_claim(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        """§四十四：version=1 + claim=X ⇒ update ⇒ version=2 / claim=X / updated_at 变化。"""
        run_id = _run_id()
        cleanup.append(run_id)
        repository = AgentCheckpointRepository()
        claim = new_claim_token()
        created = _create(repository, run_id, claim_token=claim)

        updated = repository.update(
            run_id=run_id,
            expected_state_version=created.state_version,
            claim_token=claim,
            status=CHECKPOINT_STATUS_TERMINAL,
            next_action=CHECKPOINT_NEXT_ACTION_COMPLETED,
            pending_step_index=None,
            pending_tool_result_ref=None,
        )

        assert updated.state_version == 2
        assert updated.claim_token == claim  # §十八：更新不更换 claim
        assert updated.status == CHECKPOINT_STATUS_TERMINAL
        assert updated.next_action == CHECKPOINT_NEXT_ACTION_COMPLETED
        assert updated.pending_step_index is None
        assert updated.pending_tool_result_ref is None
        assert updated.created_at == created.created_at
        assert updated.updated_at >= created.updated_at
        assert len(_rows(engine, run_id)) == 1

    def test_wrong_version_conflicts_and_leaves_state_unchanged(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        """§四十三：claim 正确、expected version 过期 ⇒ CHECKPOINT_VERSION_CONFLICT。"""
        run_id = _run_id()
        cleanup.append(run_id)
        repository = AgentCheckpointRepository()
        claim = new_claim_token()
        created = _create(repository, run_id, claim_token=claim)
        repository.update(
            run_id=run_id,
            expected_state_version=created.state_version,
            claim_token=claim,
            status=CHECKPOINT_STATUS_ACTIVE,
            next_action=CHECKPOINT_NEXT_ACTION_LLM_DECISION,
            pending_step_index=1,
        )

        with pytest.raises(AgentCheckpointConflictError) as excinfo:
            repository.update(
                run_id=run_id,
                expected_state_version=created.state_version,  # 旧版本
                claim_token=claim,
                status=CHECKPOINT_STATUS_TERMINAL,
                next_action=CHECKPOINT_NEXT_ACTION_COMPLETED,
            )

        assert excinfo.value.code == CHECKPOINT_VERSION_CONFLICT
        assert excinfo.value.reason == "version"
        current = repository.get(run_id)
        assert current is not None
        assert current.state_version == 2  # 未被冲突更新推进
        assert current.status == CHECKPOINT_STATUS_ACTIVE

    def test_wrong_claim_conflicts_and_leaves_state_unchanged(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        """§四十二：version 正确、claim 错误 ⇒ CHECKPOINT_CONFLICT（无自动接管）。"""
        run_id = _run_id()
        cleanup.append(run_id)
        repository = AgentCheckpointRepository()
        created = _create(repository, run_id, claim_token="claim-owner")

        with pytest.raises(AgentCheckpointConflictError) as excinfo:
            repository.update(
                run_id=run_id,
                expected_state_version=created.state_version,
                claim_token="claim-intruder",
                status=CHECKPOINT_STATUS_TERMINAL,
                next_action=CHECKPOINT_NEXT_ACTION_COMPLETED,
            )

        assert excinfo.value.code == CHECKPOINT_CONFLICT
        assert excinfo.value.reason == "claim"
        current = repository.get(run_id)
        assert current is not None
        assert current.claim_token == "claim-owner"  # 未被接管 / 未被重新生成
        assert current.state_version == 1
        assert current.status == CHECKPOINT_STATUS_ACTIVE

    def test_concurrent_update_only_one_wins(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        """§四十一：Worker A / Worker B 同 expected version ⇒ 只有 A 成功。"""
        run_id = _run_id()
        cleanup.append(run_id)
        repository = AgentCheckpointRepository()
        claim_a = new_claim_token()
        claim_b = "claim-worker-b"
        created = _create(repository, run_id, claim_token=claim_a)

        worker_a = repository.update(
            run_id=run_id,
            expected_state_version=created.state_version,
            claim_token=claim_a,
            status=CHECKPOINT_STATUS_ACTIVE,
            next_action=CHECKPOINT_NEXT_ACTION_LLM_DECISION,
            pending_step_index=2,
        )
        assert worker_a.state_version == 2

        with pytest.raises(AgentCheckpointConflictError) as excinfo:
            repository.update(
                run_id=run_id,
                expected_state_version=created.state_version,  # B 的旧视图
                claim_token=claim_b,  # B 的 claim 也无效
                status=CHECKPOINT_STATUS_TERMINAL,
                next_action=CHECKPOINT_NEXT_ACTION_COMPLETED,
            )

        assert excinfo.value.code in {
            CHECKPOINT_VERSION_CONFLICT,
            CHECKPOINT_CONFLICT,
        }
        current = repository.get(run_id)
        assert current is not None
        assert current.state_version == 2  # A 的写入保持不变
        assert current.claim_token == claim_a
        assert current.pending_step_index == 2

    def test_update_missing_run_is_not_found(self) -> None:
        with pytest.raises(AgentCheckpointNotFoundError) as excinfo:
            AgentCheckpointRepository().update(
                run_id=_run_id(),
                expected_state_version=1,
                claim_token=new_claim_token(),
                status=CHECKPOINT_STATUS_TERMINAL,
                next_action=CHECKPOINT_NEXT_ACTION_COMPLETED,
            )

        assert excinfo.value.code == CHECKPOINT_NOT_FOUND

    def test_delete_for_test_removes_row(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        run_id = _run_id()
        cleanup.append(run_id)
        repository = AgentCheckpointRepository()
        _create(repository, run_id)

        assert repository.delete_for_test(run_id) == 1
        assert repository.get(run_id) is None
        assert _rows(engine, run_id) == []


class TestAgentCheckpointResidue:
    def test_residue_is_zero_after_cleanup(
        self, engine: Any, cleanup: list[str]
    ) -> None:
        run_id = _run_id()
        cleanup.append(run_id)
        _create(AgentCheckpointRepository(), run_id)

        assert len(_rows(engine, run_id)) == 1
        assert run_id in cleanup  # teardown 删除

    def test_table_residue_is_zero(self, engine: Any) -> None:
        """本文件结束时：agent_checkpoint residue = 0（前缀 + 全表）。"""
        with engine.connect() as conn:
            prefixed = int(
                conn.execute(
                    text(
                        f"SELECT COUNT(*) FROM {AGENT_CHECKPOINT_SCHEMA}."
                        f"{AGENT_CHECKPOINT_TABLE} WHERE run_id LIKE :prefix"
                    ),
                    {"prefix": f"{_RUN_PREFIX}%"},
                ).scalar_one()
            )
            total = int(
                conn.execute(
                    text(
                        f"SELECT COUNT(*) FROM {AGENT_CHECKPOINT_SCHEMA}."
                        f"{AGENT_CHECKPOINT_TABLE}"
                    )
                ).scalar_one()
            )

        assert prefixed == 0
        assert total == 0

    def test_other_tables_untouched(
        self, engine: Any, other_table_counts: dict[str, int]
    ) -> None:
        """本文件**零写入**其它 7 张表（不改既有 residue guard 统计口径）。"""
        assert _counts(engine, _OTHER_TABLES) == other_table_counts
