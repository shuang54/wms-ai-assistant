"""Phase 5 P-5 Agent Checkpoint（**离线**；Fake Repository，零 DB / 零真实 LLM）。

覆盖（任务书 §十一–§三十七 / §四十 / §五十二）：

```text
DTO        frozen · 字段集精确 · 非法枚举在仓储层被拒 · version >= 1 · step >= 0
claim      token_urlsafe · 随机（≠ uuid4 / ≠ hash(run_id) / ≠ 时间戳）· 更新不更换（§十八）
refs       "{run_id}:{step_index}" · 跨 run / 跨 step 必须拒绝（§四十）
mapping    COMPLETED/FAILED/LIMIT_REACHED ⇒ TERMINAL（§三十五 / §三十六）
recorder   无 scope ⇒ 不写 · create(v1) → update(v2) · claim 稳定 · best-effort 隔离
审计       Checkpoint 不拥有 AgentRun / Conversation / Evidence / ToolResult / LLMUsage
```
"""
from __future__ import annotations

import ast
import inspect
import uuid
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.app.agent import (
    AGENT_STATUS_CANCELLED,
    AGENT_STATUS_COMPLETED,
    AGENT_STATUS_FAILED,
    AGENT_STATUS_LIMIT_REACHED,
)
from backend.app.agent.checkpoint import (
    CHECKPOINT_NEXT_ACTION_CANCELLED,
    CHECKPOINT_INITIAL_STATE_VERSION,
    CHECKPOINT_NEXT_ACTION_COMPLETED,
    CHECKPOINT_NEXT_ACTION_FAILED,
    CHECKPOINT_NEXT_ACTION_LLM_DECISION,
    CHECKPOINT_NEXT_ACTION_NONE,
    CHECKPOINT_NEXT_ACTION_TOOL_EXECUTION,
    CHECKPOINT_NEXT_ACTION_WAITING_APPROVAL,
    CHECKPOINT_NEXT_ACTIONS,
    CHECKPOINT_STATUS_ACTIVE,
    CHECKPOINT_STATUS_PAUSED,
    CHECKPOINT_STATUS_TERMINAL,
    CHECKPOINT_STATUSES,
    AgentCheckpoint,
    BestEffortAgentCheckpointRecorder,
    assert_tool_result_ref_matches,
    checkpoint_terminal_state_of,
    new_claim_token,
    parse_tool_result_ref,
    tool_result_ref,
)
from backend.app.agent.ids import current_agent_run_id
from backend.app.db.agent_checkpoint_repository import (
    CHECKPOINT_ALREADY_EXISTS,
    CHECKPOINT_CONFLICT,
    CHECKPOINT_VERSION_CONFLICT,
    AgentCheckpointAlreadyExistsError,
    AgentCheckpointConflictError,
    AgentCheckpointRepository,
    AgentCheckpointRepositoryError,
    validate_pending_step_index,
    validate_state_version,
)

__all__ = [
    "TestAgentCheckpointContract",
    "TestAgentCheckpointRefs",
    "TestAgentCheckpointTerminalMapping",
    "TestAgentCheckpointRecorder",
    "TestAgentCheckpointRepositoryValidation",
    "TestAgentCheckpointBoundary",
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENT_DIR = _REPO_ROOT / "backend" / "app" / "agent"

_RUN_ID = "run-p5-offline"
_CLAIM = "claim-fixed-token"


class FakeAgentCheckpointRepository:
    """内存仓储：模拟 UNIQUE(run_id) + 乐观并发（version + claim）。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.rows: dict[str, AgentCheckpoint] = {}
        self.creates: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self._error = error

    def get(self, run_id: str) -> AgentCheckpoint | None:
        if self._error is not None:
            raise self._error
        return self.rows.get(run_id)

    def create(self, **kwargs: Any) -> AgentCheckpoint:
        self.creates.append(kwargs)
        if self._error is not None:
            raise self._error
        if kwargs["run_id"] in self.rows:
            raise AgentCheckpointAlreadyExistsError(CHECKPOINT_ALREADY_EXISTS)
        now = datetime.now(timezone.utc)
        checkpoint = AgentCheckpoint(
            run_id=kwargs["run_id"],
            state_version=kwargs["state_version"],
            claim_token=kwargs["claim_token"],
            status=kwargs["status"],
            next_action=kwargs["next_action"],
            pending_step_index=kwargs["pending_step_index"],
            pending_tool_call_id=kwargs["pending_tool_call_id"],
            pending_decision_ref=kwargs["pending_decision_ref"],
            pending_tool_result_ref=kwargs["pending_tool_result_ref"],
            created_at=now,
            updated_at=now,
        )
        self.rows[checkpoint.run_id] = checkpoint
        return checkpoint

    def update(self, **kwargs: Any) -> AgentCheckpoint:
        self.updates.append(kwargs)
        if self._error is not None:
            raise self._error
        current = self.rows.get(kwargs["run_id"])
        if current is None:
            raise AgentCheckpointRepositoryError("not found")
        if current.state_version != kwargs["expected_state_version"]:
            raise AgentCheckpointConflictError(
                "version", code=CHECKPOINT_VERSION_CONFLICT, reason="version"
            )
        if current.claim_token != kwargs["claim_token"]:
            raise AgentCheckpointConflictError(
                "claim", code=CHECKPOINT_CONFLICT, reason="claim"
            )
        now = datetime.now(timezone.utc)
        checkpoint = AgentCheckpoint(
            run_id=current.run_id,
            state_version=current.state_version + 1,
            claim_token=current.claim_token,  # 更新**不**更换 claim（§十八）
            status=kwargs["status"],
            next_action=kwargs["next_action"],
            pending_step_index=kwargs["pending_step_index"],
            pending_tool_call_id=kwargs["pending_tool_call_id"],
            pending_decision_ref=kwargs["pending_decision_ref"],
            pending_tool_result_ref=kwargs["pending_tool_result_ref"],
            created_at=current.created_at,
            updated_at=now,
        )
        self.rows[checkpoint.run_id] = checkpoint
        return checkpoint

    def delete_for_test(self, run_id: str) -> int:
        return 1 if self.rows.pop(run_id, None) is not None else 0


class _RacingRepository(FakeAgentCheckpointRepository):
    """模拟"读取后、更新前"被其它 worker 推进版本（乐观并发竞态；§四十一）。"""

    def __init__(self) -> None:
        super().__init__()
        self._raced = False

    def get(self, run_id: str) -> AgentCheckpoint | None:
        current = super().get(run_id)
        if current is not None and not self._raced:
            self._raced = True
            self.rows[run_id] = replace(
                current, state_version=current.state_version + 1
            )
        return current


class _ExplodingSessionFactory:
    def __call__(self) -> Any:
        raise AssertionError("校验必须在触达 DB 之前失败")


def _recorder(
    repository: Any = None, *, claim: str = _CLAIM
) -> BestEffortAgentCheckpointRecorder:
    return BestEffortAgentCheckpointRecorder(
        repository if repository is not None else FakeAgentCheckpointRepository(),
        claim_token_factory=lambda: claim,
    )


# ============================================================
# DTO / 枚举（§十一 / §十九 / §二十 / §三十）
# ============================================================


class TestAgentCheckpointContract:
    def test_dto_is_frozen_with_exact_field_set(self) -> None:
        assert [field.name for field in fields(AgentCheckpoint)] == [
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
            # Phase 5 P-7：协作式取消信号（**信号**，不是 status 枚举成员）
            "cancel_requested",
            "cancel_requested_at",
        ]

    def test_dto_rejects_mutation(self) -> None:
        now = datetime.now(timezone.utc)
        checkpoint = AgentCheckpoint(
            run_id=_RUN_ID,
            state_version=1,
            claim_token=_CLAIM,
            status=CHECKPOINT_STATUS_ACTIVE,
            next_action=CHECKPOINT_NEXT_ACTION_LLM_DECISION,
            pending_step_index=1,
            pending_tool_call_id=None,
            pending_decision_ref=None,
            pending_tool_result_ref=f"{_RUN_ID}:0",
            created_at=now,
            updated_at=now,
        )

        with pytest.raises(FrozenInstanceError):
            checkpoint.status = CHECKPOINT_STATUS_TERMINAL  # type: ignore[misc]

    def test_status_and_next_action_whitelists(self) -> None:
        assert CHECKPOINT_STATUSES == {
            CHECKPOINT_STATUS_ACTIVE,
            CHECKPOINT_STATUS_PAUSED,
            CHECKPOINT_STATUS_TERMINAL,
        }
        assert CHECKPOINT_NEXT_ACTIONS == {
            CHECKPOINT_NEXT_ACTION_NONE,
            CHECKPOINT_NEXT_ACTION_LLM_DECISION,
            CHECKPOINT_NEXT_ACTION_TOOL_EXECUTION,
            CHECKPOINT_NEXT_ACTION_WAITING_APPROVAL,
            CHECKPOINT_NEXT_ACTION_COMPLETED,
            CHECKPOINT_NEXT_ACTION_FAILED,
            # Phase 5 P-7 §三十五：取消必须表达为 CANCELLED（**不**用 FAILED 冒充）
            CHECKPOINT_NEXT_ACTION_CANCELLED,
        }
        # §十九 / §三十六：禁止新增其它 action（HITL 裁决不进 Checkpoint：
        # 审批状态在 agent_approval；LIMIT_REACHED 归入 FAILED）
        for forbidden in ("APPROVED", "REJECTED", "LIMIT_REACHED", "RESUME"):
            assert forbidden not in CHECKPOINT_NEXT_ACTIONS

    def test_claim_token_is_cryptographically_random(self) -> None:
        first, second = new_claim_token(), new_claim_token()

        assert first != second
        assert len(first) == 43  # token_urlsafe(32)
        assert first.replace("-", "").replace("_", "").isalnum()
        # 不是 uuid4（§十五：禁止 uuid.uuid4()）
        with pytest.raises(ValueError):
            uuid.UUID(first)
        # 不是 hash(run_id) / 时间戳（长度与形态均不可推导）
        assert first != str(hash(_RUN_ID))

    def test_repo_validators(self) -> None:
        assert validate_state_version(1) == 1
        assert validate_pending_step_index(None) is None
        assert validate_pending_step_index(0) == 0
        for value in (0, -1, True, 1.0, "1"):
            with pytest.raises(ValueError):
                validate_state_version(value)
        for value in (-1, True, 1.0, "1"):
            with pytest.raises(ValueError):
                validate_pending_step_index(value)
        assert CHECKPOINT_INITIAL_STATE_VERSION == 1


# ============================================================
# 引用纯函数（§二十四 / §四十）
# ============================================================


class TestAgentCheckpointRefs:
    def test_ref_round_trip(self) -> None:
        ref = tool_result_ref(_RUN_ID, 3)

        assert ref == f"{_RUN_ID}:3"
        assert parse_tool_result_ref(ref) == (_RUN_ID, 3)
        assert_tool_result_ref_matches(ref, run_id=_RUN_ID, step_index=3)  # 不抛

    @pytest.mark.parametrize(
        "bad",
        ["", "   ", "no-colon", ":3", "run:abc", "run:-1", "run:1.5"],
    )
    def test_invalid_refs_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_tool_result_ref(bad)

    def test_cross_run_reference_rejected(self) -> None:
        with pytest.raises(ValueError, match="其它 run"):
            assert_tool_result_ref_matches(
                f"run-other:0", run_id=_RUN_ID, step_index=0
            )

    def test_cross_step_reference_rejected(self) -> None:
        with pytest.raises(ValueError, match="其它 step"):
            assert_tool_result_ref_matches(
                tool_result_ref(_RUN_ID, 1), run_id=_RUN_ID, step_index=0
            )


# ============================================================
# 终态映射（§三十五 / §三十六 / §三十七）
# ============================================================


class TestAgentCheckpointTerminalMapping:
    def test_completed_maps_to_terminal_completed(self) -> None:
        assert checkpoint_terminal_state_of(AGENT_STATUS_COMPLETED) == (
            CHECKPOINT_STATUS_TERMINAL,
            CHECKPOINT_NEXT_ACTION_COMPLETED,
        )

    def test_failed_maps_to_terminal_failed(self) -> None:
        assert checkpoint_terminal_state_of(AGENT_STATUS_FAILED) == (
            CHECKPOINT_STATUS_TERMINAL,
            CHECKPOINT_NEXT_ACTION_FAILED,
        )

    def test_limit_reached_maps_to_terminal_failed_without_new_action(self) -> None:
        assert checkpoint_terminal_state_of(AGENT_STATUS_LIMIT_REACHED) == (
            CHECKPOINT_STATUS_TERMINAL,
            CHECKPOINT_NEXT_ACTION_FAILED,
        )
        assert "LIMIT_REACHED" not in CHECKPOINT_NEXT_ACTIONS

    def test_cancelled_maps_to_terminal_cancelled(self) -> None:
        """P-7 §三十五：CANCELLED ⇒ TERMINAL / CANCELLED（**不**映射成 FAILED）。"""
        assert checkpoint_terminal_state_of(AGENT_STATUS_CANCELLED) == (
            CHECKPOINT_STATUS_TERMINAL,
            CHECKPOINT_NEXT_ACTION_CANCELLED,
        )

    def test_paused_status_has_no_terminal_mapping(self) -> None:
        # WAITING_APPROVAL 是**暂停**（非终态）：不产生终态 checkpoint；
        # 未知状态同样不猜（fail-closed）。
        assert checkpoint_terminal_state_of("WAITING_APPROVAL") is None
        assert checkpoint_terminal_state_of("UNKNOWN") is None


# ============================================================
# Recorder（§二十九 / §三十一 / §三十二 / §三十三 / §四十四）
# ============================================================


class TestAgentCheckpointRecorder:
    def test_creates_initial_checkpoint_in_scope(self) -> None:
        repository = FakeAgentCheckpointRepository()
        recorder = _recorder(repository)

        with _scope(_RUN_ID):
            checkpoint = recorder.record_tool_result_checkpoint(
                step_index=0, tool_result_persisted=True
            )

        assert checkpoint is not None
        assert checkpoint.state_version == 1  # §十一：创建 = 1
        assert checkpoint.claim_token == _CLAIM
        assert checkpoint.status == CHECKPOINT_STATUS_ACTIVE
        assert checkpoint.next_action == CHECKPOINT_NEXT_ACTION_LLM_DECISION
        assert checkpoint.pending_step_index == 1  # next step（**不是** round）
        assert checkpoint.pending_tool_call_id is None  # §二十二
        assert checkpoint.pending_decision_ref is None  # §二十三（无 Decision Store）
        assert checkpoint.pending_tool_result_ref == f"{_RUN_ID}:0"  # §二十四
        assert len(repository.creates) == 1
        assert repository.updates == []

    def test_result_ref_absent_when_result_not_persisted(self) -> None:
        repository = FakeAgentCheckpointRepository()
        recorder = _recorder(repository)

        with _scope(_RUN_ID):
            checkpoint = recorder.record_tool_result_checkpoint(
                step_index=0, tool_result_persisted=False
            )

        assert checkpoint is not None
        assert checkpoint.pending_tool_result_ref is None  # 不引用未 durable 的结果

    def test_second_write_updates_version_and_keeps_claim(self) -> None:
        repository = FakeAgentCheckpointRepository()
        recorder = _recorder(repository)

        with _scope(_RUN_ID):
            first = recorder.record_tool_result_checkpoint(
                step_index=0, tool_result_persisted=True
            )
            second = recorder.record_tool_result_checkpoint(
                step_index=1, tool_result_persisted=True
            )

        assert first is not None and second is not None
        assert (first.state_version, second.state_version) == (1, 2)  # 只增（§十一）
        assert second.claim_token == first.claim_token  # §十八：更新不换 claim
        assert second.pending_step_index == 2
        assert second.pending_tool_result_ref == f"{_RUN_ID}:1"
        assert len(repository.updates) == 1
        assert repository.updates[0]["expected_state_version"] == 1

    def test_terminal_write_maps_and_clears_pending(self) -> None:
        repository = FakeAgentCheckpointRepository()
        recorder = _recorder(repository)

        with _scope(_RUN_ID):
            recorder.record_tool_result_checkpoint(
                step_index=0, tool_result_persisted=True
            )
            terminal = recorder.record_terminal(run_id=_RUN_ID, agent_status=AGENT_STATUS_COMPLETED)

        assert terminal is not None
        assert terminal.status == CHECKPOINT_STATUS_TERMINAL
        assert terminal.next_action == CHECKPOINT_NEXT_ACTION_COMPLETED
        assert terminal.state_version == 2
        assert terminal.pending_step_index is None
        assert terminal.pending_tool_result_ref is None
        assert terminal.claim_token == _CLAIM

    def test_terminal_without_prior_tool_creates_checkpoint(self) -> None:
        repository = FakeAgentCheckpointRepository()
        recorder = _recorder(repository)

        with _scope(_RUN_ID):
            terminal = recorder.record_terminal(run_id=_RUN_ID, agent_status=AGENT_STATUS_FAILED)

        assert terminal is not None
        assert terminal.state_version == 1
        assert terminal.status == CHECKPOINT_STATUS_TERMINAL
        assert terminal.next_action == CHECKPOINT_NEXT_ACTION_FAILED

    def test_reserved_status_writes_nothing(self) -> None:
        repository = FakeAgentCheckpointRepository()
        recorder = _recorder(repository)

        with _scope(_RUN_ID):
            assert (
            recorder.record_terminal(
                run_id=_RUN_ID, agent_status="WAITING_APPROVAL"
            )
            is None
        )

        assert repository.rows == {}

    def test_tool_result_checkpoint_without_scope_writes_nothing(self) -> None:
        """Runtime 侧写点：无 Agent Run Scope ⇒ 不写（绝不猜 run_id）。"""
        repository = FakeAgentCheckpointRepository()
        recorder = _recorder(repository)

        assert current_agent_run_id() is None
        assert (
            recorder.record_tool_result_checkpoint(
                step_index=0, tool_result_persisted=True
            )
            is None
        )
        assert repository.creates == [] and repository.updates == []

    def test_terminal_checkpoint_takes_run_id_from_result(self) -> None:
        """终态写点在 Orchestrator 触发（**不在** scope 内）⇒ run_id 显式只读传入。"""
        repository = FakeAgentCheckpointRepository()
        recorder = _recorder(repository)

        assert current_agent_run_id() is None
        terminal = recorder.record_terminal(
            run_id=_RUN_ID, agent_status=AGENT_STATUS_COMPLETED
        )

        assert terminal is not None
        assert terminal.run_id == _RUN_ID
        assert terminal.status == CHECKPOINT_STATUS_TERMINAL

    def test_terminal_checkpoint_without_run_id_writes_nothing(self) -> None:
        repository = FakeAgentCheckpointRepository()
        recorder = _recorder(repository)

        for bad in ("", "   ", None):
            assert (
                recorder.record_terminal(
                    run_id=bad,  # type: ignore[arg-type]
                    agent_status=AGENT_STATUS_COMPLETED,
                )
                is None
            )
        assert repository.creates == [] and repository.updates == []

    def test_repository_failure_is_isolated(self) -> None:
        repository = FakeAgentCheckpointRepository(error=RuntimeError("db down"))
        recorder = _recorder(repository)

        with _scope(_RUN_ID):
            assert (
                recorder.record_tool_result_checkpoint(
                    step_index=0, tool_result_persisted=True
                )
                is None
            )
            assert recorder.record_terminal(run_id=_RUN_ID, agent_status=AGENT_STATUS_COMPLETED) is None

    def test_conflict_is_isolated_and_state_unchanged(self) -> None:
        """§四十一：读取后、更新前被其它 worker 推进版本 ⇒ 冲突被隔离、状态不被覆盖。"""
        repository = _RacingRepository()
        recorder = _recorder(repository)
        with _scope(_RUN_ID):
            recorder.record_tool_result_checkpoint(
                step_index=0, tool_result_persisted=True
            )

        with _scope(_RUN_ID):
            assert recorder.record_terminal(run_id=_RUN_ID, agent_status=AGENT_STATUS_COMPLETED) is None

        current = repository.rows[_RUN_ID]
        assert current.state_version == 2  # 竞态写入者的版本（**不是** 3）
        assert current.status == CHECKPOINT_STATUS_ACTIVE  # 未被本次更新覆盖

    async def test_async_entries(self) -> None:
        repository = FakeAgentCheckpointRepository()
        recorder = _recorder(repository)

        with _scope(_RUN_ID):
            checkpoint = await recorder.arecord_tool_result_checkpoint(
                step_index=0, tool_result_persisted=True
            )
            terminal = await recorder.arecord_terminal(
                run_id=_RUN_ID, agent_status=AGENT_STATUS_LIMIT_REACHED
            )

        assert checkpoint is not None and terminal is not None
        assert terminal.status == CHECKPOINT_STATUS_TERMINAL
        assert terminal.next_action == CHECKPOINT_NEXT_ACTION_FAILED

    def test_recorder_requires_repository(self) -> None:
        with pytest.raises(ValueError, match="get\\(\\)"):
            BestEffortAgentCheckpointRecorder(object())

    def test_build_recorder_respects_engine_availability(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import backend.app.db.session as session_module
        from backend.app.agent.recorder import build_agent_checkpoint_recorder
        from backend.app.db.agent_checkpoint_repository import (
            AgentCheckpointRepository,
        )

        monkeypatch.setattr(session_module, "get_engine", lambda: None)
        assert build_agent_checkpoint_recorder() is None

        monkeypatch.setattr(session_module, "get_engine", lambda: object())
        recorder = build_agent_checkpoint_recorder()
        assert isinstance(recorder, BestEffortAgentCheckpointRecorder)
        assert isinstance(recorder.repository, AgentCheckpointRepository)


def _scope(run_id: str) -> Any:
    from backend.app.agent.ids import agent_run_scope

    return agent_run_scope(run_id)


# ============================================================
# Repository 校验（零 DB；§二十七）
# ============================================================


class TestAgentCheckpointRepositoryValidation:
    def _repository(self) -> AgentCheckpointRepository:
        return AgentCheckpointRepository(
            session_factory=_ExplodingSessionFactory()  # type: ignore[arg-type]
        )

    def test_valid_create_payload_reaches_db_layer(self) -> None:
        # 参数全部合法 ⇒ 只在触达 DB 时失败（证明校验通过）
        with pytest.raises(AssertionError):
            self._repository().create(
                run_id=_RUN_ID,
                claim_token=_CLAIM,
                status=CHECKPOINT_STATUS_ACTIVE,
                next_action=CHECKPOINT_NEXT_ACTION_LLM_DECISION,
                pending_step_index=1,
                pending_tool_result_ref=f"{_RUN_ID}:0",
            )

    @pytest.mark.parametrize(
        "overrides",
        [
            {"run_id": ""},
            {"run_id": "r" * 65},
            {"claim_token": ""},
            {"claim_token": "c" * 129},
            {"status": "RUNNING"},
            {"status": "ACTIVE "},
            {"next_action": "LIMIT_REACHED"},
            {"next_action": "APPROVED"},
            {"state_version": 0},
            {"state_version": True},
            {"pending_step_index": -1},
            {"pending_step_index": True},
            # §二十二：pending_tool_call_id 仅在 TOOL_EXECUTION 时允许
            {
                "pending_tool_call_id": "call-1",
                "next_action": CHECKPOINT_NEXT_ACTION_LLM_DECISION,
            },
            # §四十：引用必须指向同一 run
            {"pending_tool_result_ref": "run-other:0"},
            {"pending_tool_result_ref": "not-a-ref"},
            {"pending_decision_ref": "d" * 129},
        ],
    )
    def test_invalid_values_fail_before_db(self, overrides: dict[str, Any]) -> None:
        values: dict[str, Any] = {
            "run_id": _RUN_ID,
            "claim_token": _CLAIM,
            "status": CHECKPOINT_STATUS_ACTIVE,
            "next_action": CHECKPOINT_NEXT_ACTION_LLM_DECISION,
            "pending_step_index": 1,
            "pending_tool_result_ref": f"{_RUN_ID}:0",
        }
        values.update(overrides)

        with pytest.raises(ValueError):
            self._repository().create(**values)

    def test_create_requires_initial_version(self) -> None:
        with pytest.raises(ValueError, match="state_version"):
            self._repository().create(
                run_id=_RUN_ID,
                state_version=2,
                claim_token=_CLAIM,
                status=CHECKPOINT_STATUS_ACTIVE,
                next_action=CHECKPOINT_NEXT_ACTION_LLM_DECISION,
            )

    def test_tool_execution_allows_tool_call_id(self) -> None:
        with pytest.raises(AssertionError):  # 通过校验 ⇒ 触达 DB
            self._repository().create(
                run_id=_RUN_ID,
                claim_token=_CLAIM,
                status=CHECKPOINT_STATUS_ACTIVE,
                next_action=CHECKPOINT_NEXT_ACTION_TOOL_EXECUTION,
                pending_step_index=2,
                pending_tool_call_id="call-provider-1",
            )

    def test_update_validates_expected_version(self) -> None:
        with pytest.raises(ValueError):
            self._repository().update(
                run_id=_RUN_ID,
                expected_state_version=0,
                claim_token=_CLAIM,
                status=CHECKPOINT_STATUS_ACTIVE,
                next_action=CHECKPOINT_NEXT_ACTION_LLM_DECISION,
            )

    def test_repository_api_surface(self) -> None:
        """§二十六：生产 API = create / get / update；delete 仅 delete_for_test。"""
        public = {
            name for name in dir(AgentCheckpointRepository) if not name.startswith("_")
        }

        # Phase 5 P-7：+ request_cancel（取消信号 CAS；fail-closed）
        assert public == {
            "create",
            "get",
            "update",
            "request_cancel",
            "delete_for_test",
        }
        assert not hasattr(AgentCheckpointRepository, "delete")
        # 无 Resume / Replay / Claim / 读模型
        for forbidden in ("resume", "replay", "claim", "list", "timeline", "restore"):
            assert not hasattr(AgentCheckpointRepository, forbidden)


# ============================================================
# 边界审计（§二十五 / §四十六 / §四十八–§五十一）
# ============================================================


class TestAgentCheckpointBoundary:
    def test_checkpoint_does_not_own_other_domains(self) -> None:
        """Checkpoint 不拥有 AgentRun / Conversation / Evidence / Result / LLM 计量。"""
        names = {field.name for field in fields(AgentCheckpoint)}

        for forbidden in (
            "conversation_id",
            "turn_id",
            "idempotency_key",
            "evidence_id",
            "result_payload",
            "result_hash",
            "result_size_bytes",
            "prompt",
            "messages",
            "context",
            "sql",
            "tool_arguments",
            "tool_result_body",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "provider",
            "model",
            "finish_reason",
            "request_id",
            "tool_call_id",
        ):
            assert forbidden not in names, forbidden

    def test_checkpoint_module_has_no_db_or_conversation_import(self) -> None:
        """域层 0 DB（Repository 必须注入；builder 在持久化边界）。"""
        source = (_AGENT_DIR / "checkpoint.py").read_text(encoding="utf-8")
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
            assert not module.startswith("backend.app.services"), module
            assert not module.startswith("sqlalchemy"), module
            assert "conversation" not in module.lower(), module
            assert "evidence" not in module.lower(), module
        # 标识符层面（docstring 中列出"禁止字段"不算耦合）
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "Conversation",
            "ConversationTurn",
            "EvidenceRecord",
            "conversation_id",
            "evidence_id",
            "Idempotency",
        ):
            assert forbidden not in names, forbidden
        # 无 Resume / Replay / HITL 入口（代码层）
        for forbidden in ("def resume", "def replay", "def restore", "def cancel"):
            assert forbidden not in source, forbidden

    def test_runtime_takes_checkpoint_port_only(self) -> None:
        """§四十六 / §四十七：Runtime 仍 0 DB，只持有注入端口。"""
        source = (_AGENT_DIR / "runtime.py").read_text(encoding="utf-8")
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
        for forbidden in (
            "AgentCheckpointRepository",
            "agent_checkpoint_repository",
            "sessionmaker",
        ):
            assert forbidden not in source, forbidden
        assert "checkpoint_sink" in source  # 端口存在（P-5）

    def test_claim_token_is_not_part_of_any_output_contract(self) -> None:
        """§十六 / §三十：claim_token 只属内部 DTO —— 输出契约里不存在该字段。

        说明：**不**新增源码扫描器（项目已有 scanner uniqueness 审计）；
        以**契约 + 运行时**证据为准：
            ① ``AIOrchestrationResult`` 的字段只有 route / content / data / metadata；
            ② Orchestrator 不引用 claim_token（AST 标识符层面）。
        """
        from backend.app.services.ai_orchestrator_service import AIOrchestrationResult

        assert {field.name for field in fields(AIOrchestrationResult)} == {
            "route",
            "content",
            "data",
            "metadata",
        }
        orchestrator_tree = ast.parse(
            (
                _REPO_ROOT
                / "backend"
                / "app"
                / "services"
                / "ai_orchestrator_service.py"
            ).read_text(encoding="utf-8")
        )
        attributes = {
            node.attr
            for node in ast.walk(orchestrator_tree)
            if isinstance(node, ast.Attribute)
        } | {
            node.id
            for node in ast.walk(orchestrator_tree)
            if isinstance(node, ast.Name)
        }

        assert "claim_token" not in attributes

    def test_recorder_run_id_sources_are_frozen(self) -> None:
        """run_id 来源冻结：

        * Runtime 侧（ACTIVE）写点 = ``current_agent_run_id()``（Scope）；
          **不接受** 调用方传入 run_id / request_id；
        * 终态写点（Orchestrator 触发，不在 Scope 内）= 显式 ``run_id``
          （只读 ``AgentRuntimeResult.run_id``）；**不接受** request_id。
        """
        active_params = set(
            inspect.signature(
                BestEffortAgentCheckpointRecorder.record_tool_result_checkpoint
            ).parameters
        )
        terminal_params = set(
            inspect.signature(BestEffortAgentCheckpointRecorder.record_terminal).parameters
        )

        assert active_params == {
            "self",
            "step_index",
            "tool_result_persisted",
        }
        assert terminal_params == {
            "self",
            "run_id",
            "agent_status",
            "agent_error",
        }
        for params in (active_params, terminal_params):
            assert "request_id" not in params
            assert "tool_call_id" not in params
