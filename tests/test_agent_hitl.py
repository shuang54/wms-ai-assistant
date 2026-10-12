"""Phase 5 P-7 Agent HITL（人工审批门）—— **离线**（Fake LLM / Fake Tool / 内存仓储；零 DB）。

覆盖（任务书 §七–§二十三 / §四十–§四十二 / §五十八 / §五十九）：

```text
1  审批门位置      Tool Authorization 之后、Tool 执行**之前**（§七 / §四十一）
2  WAITING_APPROVAL Tool Handler = 0 / ToolExecutionRecord = 0 / Tool Result = 0（§十五 / §五十九）
3  Checkpoint      status=PAUSED · next_action=WAITING_APPROVAL · pending_*（§十四）
4  AgentRun        暂停**不**写 Run（既不是 WAITING_APPROVAL 也不是 FAILED；§十六）
5  Approval 身份   approval_id ≠ run_id / request_id / tool_call_id（§十二）
6  状态机          PENDING → APPROVED | REJECTED；幂等 / 冲突 / 终态不可逆（§十八 / §十九 / §五十一）
7  APPROVED        Resume → **重新 Authorization** → Tool 执行 → 续跑（§二十 / §五十五）
8  REJECTED        FAILED + AGENT_APPROVAL_REJECTED（Tool = 0；不重问 LLM；§二十一 / §二十二）
9  重新授权失败    AGENT_TOOL_AUTHORIZATION_FAILED（Approval ≠ Authorization；§二十）
10 fail-closed     审批缺失 / 身份不一致 / Store 写失败 / Decision 未 durable（§十一 / §二十三）
11 装配期策略      approval_required_tools ⊆ allowed_tools + 必须接线端口（§四十）
12 安全            Approval 不存 arguments / prompt / context / Tool Result（§十一 / §四十二）
```

说明：本文件同时提供 P-7 测试基建（``FakeAgentApprovalRepository`` /
``P7CheckpointRepository`` / ``RecordingCheckpointRecorder`` / ``_env``），
被 ``test_agent_cancellation.py`` 与 ``test_agent_hitl_cancellation_race.py`` 复用。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.agent import (
    AGENT_ERROR_APPROVAL_CONFLICT,
    AGENT_ERROR_APPROVAL_NOT_FOUND,
    AGENT_ERROR_APPROVAL_REJECTED,
    AGENT_ERROR_APPROVAL_REQUIRED,
    AGENT_ERROR_APPROVAL_STORE_FAILURE,
    AGENT_ERROR_DECISION_PERSISTENCE_FAILURE,
    AGENT_ERROR_TOOL_AUTHORIZATION_FAILED,
    AGENT_STATUS_COMPLETED,
    AGENT_STATUS_FAILED,
    AGENT_STATUS_WAITING_APPROVAL,
    APPROVAL_DECISIONS,
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
    DECISION_FINAL,
    DECISION_TOOL_CALL,
    AgentApprovalRequest,
    AgentApprovalStore,
    AgentDecision,
    AgentPolicyError,
    AgentRuntime,
    AgentRuntimePolicy,
    approval_request_of,
    new_approval_id,
)
from backend.app.agent.approval import (
    APPROVAL_STATUSES,
    approval_conflict_code,
    is_valid_approval_decision,
)
from backend.app.agent.checkpoint import (
    CHECKPOINT_NEXT_ACTION_WAITING_APPROVAL,
    CHECKPOINT_STATUS_PAUSED,
    CHECKPOINT_STATUS_TERMINAL,
    BestEffortAgentCheckpointRecorder,
)
from backend.app.agent.decision_store import AgentDecisionStore
from backend.app.agent.ids import agent_run_scope
from backend.app.agent.recorder import (
    AgentToolResultWritePoint,
    BestEffortAgentToolResultRecorder,
)
from backend.app.db.agent_checkpoint_repository import (
    CHECKPOINT_CANCEL_CONFLICT,
    CHECKPOINT_NOT_FOUND,
    AgentCheckpointConflictError,
    AgentCheckpointNotFoundError,
)
from tests.test_agent_checkpoint import FakeAgentCheckpointRepository
from tests.test_agent_decision_store import FakeAgentDecisionRepository
from tests.test_agent_runtime import FakeLLM, _TOOL_NAME, _execution_service
from tests.test_agent_tool_result_store import FakeAgentToolResultRepository

__all__ = [
    "FakeAgentApprovalRepository",
    "P7CheckpointRepository",
    "RecordingCheckpointRecorder",
    "_env",
    "_Env",
    "TestApprovalDomain",
    "TestApprovalStore",
    "TestApprovalGate",
    "TestApprovalResume",
    "TestApprovalFailClosed",
    "TestApprovalSecurity",
]

_RUN_ID = "run-p7-hitl"
_TOOL_CALL = AgentDecision(
    type=DECISION_TOOL_CALL,
    tool_name=_TOOL_NAME,
    arguments={"material_code": "MAT001"},
    tool_call_id="call_p7_1",
)
_FINAL = AgentDecision(type=DECISION_FINAL, content="库存 120 件。")


# ============================================================
# 测试基建（内存 Fake；生产实现分别被离线 / DB 测试覆盖）
# ============================================================


@dataclass(frozen=True)
class _ApprovalRow:
    """审批行（镜像 ORM 字段；**无** arguments —— §十一）。"""

    approval_id: str
    run_id: str
    step_index: int
    tool_name: str
    tool_call_id: str | None
    status: str
    decision: str | None
    created_at: datetime
    resolved_at: datetime | None = None


class FakeAgentApprovalRepository:
    """内存审批仓储：UNIQUE(approval_id) + UNIQUE(run_id, step_index) + 条件裁决。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.rows: dict[str, _ApprovalRow] = {}
        self.creates: list[dict[str, Any]] = []
        self.resolves: list[dict[str, Any]] = []
        self._error = error

    def _check(self) -> None:
        if self._error is not None:
            raise self._error

    def create(
        self,
        *,
        approval_id: str,
        run_id: str,
        step_index: int,
        tool_name: str,
        tool_call_id: str | None = None,
    ) -> _ApprovalRow:
        self.creates.append(
            {
                "approval_id": approval_id,
                "run_id": run_id,
                "step_index": step_index,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
            }
        )
        self._check()
        existing = self.get_for_step(run_id=run_id, step_index=step_index)
        if existing is not None:
            return existing  # 重复请求 ⇒ 幂等（不产生第二行、不重置状态）
        row = _ApprovalRow(
            approval_id=approval_id,
            run_id=run_id,
            step_index=step_index,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            status=APPROVAL_STATUS_PENDING,
            decision=None,
            created_at=datetime.now(timezone.utc),
        )
        self.rows[approval_id] = row
        return row

    def get(self, approval_id: str) -> _ApprovalRow | None:
        self._check()
        return self.rows.get(approval_id)

    def get_for_step(self, *, run_id: str, step_index: int) -> _ApprovalRow | None:
        self._check()
        for row in self.rows.values():
            if row.run_id == run_id and row.step_index == step_index:
                return row
        return None

    def resolve(self, *, approval_id: str, decision: str) -> _ApprovalRow | None:
        self.resolves.append({"approval_id": approval_id, "decision": decision})
        self._check()
        row = self.rows.get(approval_id)
        if row is None or row.status != APPROVAL_STATUS_PENDING:
            return None  # 条件 UPDATE 未命中（调用方判幂等 / 冲突）
        updated = replace(
            row,
            status=decision,
            decision=decision,
            resolved_at=datetime.now(timezone.utc),
        )
        self.rows[approval_id] = updated
        return updated

    def delete_for_test(self, approval_id: str) -> int:
        return 1 if self.rows.pop(approval_id, None) is not None else 0


class P7CheckpointRepository(FakeAgentCheckpointRepository):
    """P-5 内存仓储 + P-7 取消信号语义（update 保留信号；request_cancel CAS）。"""

    def create(self, **kwargs: Any) -> Any:
        row = super().create(**kwargs)
        updated = replace(
            row,
            cancel_requested=bool(kwargs.get("cancel_requested", False)),
            cancel_requested_at=kwargs.get("cancel_requested_at"),
        )
        self.rows[updated.run_id] = updated
        return updated

    def update(self, **kwargs: Any) -> Any:
        current = self.rows.get(kwargs["run_id"])
        row = super().update(**kwargs)
        merged = replace(
            row,
            cancel_requested=current.cancel_requested if current else False,
            cancel_requested_at=current.cancel_requested_at if current else None,
        )
        self.rows[merged.run_id] = merged
        return merged

    def request_cancel(self, *, run_id: str) -> Any:
        current = self.rows.get(run_id)
        if current is None:
            raise AgentCheckpointNotFoundError(CHECKPOINT_NOT_FOUND)
        if current.status == CHECKPOINT_STATUS_TERMINAL or current.cancel_requested:
            raise AgentCheckpointConflictError(
                "cancel not applied",
                code=CHECKPOINT_CANCEL_CONFLICT,
                reason="already",
            )
        updated = replace(
            current,
            cancel_requested=True,
            cancel_requested_at=datetime.now(timezone.utc),
            state_version=current.state_version + 1,
        )
        self.rows[run_id] = updated
        return updated

    def force_terminal_cancelled(self, run_id: str) -> None:
        """测试辅助：模拟"取消信号已置位且已终态化"。"""
        current = self.rows[run_id]
        self.rows[run_id] = replace(
            current,
            status=CHECKPOINT_STATUS_TERMINAL,
            next_action="CANCELLED",
        )


class RecordingCheckpointRecorder:
    """记录器包装（委托真实 best-effort 记录器；额外记录暂停/终态写点）。"""

    def __init__(self, repository: Any) -> None:
        self._inner = BestEffortAgentCheckpointRecorder(
            repository, claim_token_factory=lambda: "claim-p7"
        )
        self.repository = repository
        self.waiting_writes: list[tuple[int, str, str | None]] = []
        self.terminal_writes: list[str] = []
        self.decision_writes: list[tuple[int, str, str]] = []

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
        return self._inner.record_tool_result_checkpoint(
            step_index=step_index, tool_result_persisted=tool_result_persisted
        )

    async def arecord_tool_result_checkpoint(self, **kwargs: Any) -> Any:
        return self.record_tool_result_checkpoint(**kwargs)

    def record_waiting_approval_checkpoint(
        self,
        *,
        step_index: int,
        decision_ref: str,
        tool_call_id: str | None = None,
    ) -> Any:
        self.waiting_writes.append((step_index, decision_ref, tool_call_id))
        return self._inner.record_waiting_approval_checkpoint(
            step_index=step_index,
            decision_ref=decision_ref,
            tool_call_id=tool_call_id,
        )

    async def arecord_waiting_approval_checkpoint(self, **kwargs: Any) -> Any:
        return self.record_waiting_approval_checkpoint(**kwargs)

    def record_terminal(self, **kwargs: Any) -> Any:
        self.terminal_writes.append(str(kwargs.get("agent_status")))
        return self._inner.record_terminal(**kwargs)

    async def arecord_terminal(self, **kwargs: Any) -> Any:
        return self.record_terminal(**kwargs)

    # ---- 读 / 声明 / 取消（Resume / Cancel 端口） ----
    def get(self, run_id: str) -> Any:
        return self._inner.get(run_id)

    def claim_for_resume(self, **kwargs: Any) -> Any:
        return self._inner.claim_for_resume(**kwargs)

    def request_cancel(self, *, run_id: str) -> Any:
        return self._inner.request_cancel(run_id=run_id)


@dataclass
class _Env:
    """测试环境：真实 Runtime + 真实端口实现（仓储为内存 Fake）。"""

    runtime: AgentRuntime
    llm: FakeLLM
    handler: Any
    registry: Any
    checkpoints: P7CheckpointRepository
    checkpoint_recorder: RecordingCheckpointRecorder
    approvals: FakeAgentApprovalRepository
    approval_store: AgentApprovalStore
    decisions: FakeAgentDecisionRepository
    decision_store: AgentDecisionStore
    tool_results: FakeAgentToolResultRepository
    tool_result_writer: AgentToolResultWritePoint


def _env(
    llm: FakeLLM,
    *,
    approval_required: tuple[str, ...] = (_TOOL_NAME,),
    approval_repository: Any = None,
    approval_port: Any = None,
    checkpoint_recorder: Any = None,
    decision_store: Any = None,
    handler: Any = None,
) -> _Env:
    service, registry, resolved_handler = _execution_service(handler)
    checkpoints = P7CheckpointRepository()
    recorder = checkpoint_recorder or RecordingCheckpointRecorder(checkpoints)
    approvals = (
        approval_repository
        if approval_repository is not None
        else FakeAgentApprovalRepository()
    )
    store = (
        approval_port
        if approval_port is not None
        else AgentApprovalStore(approvals)
    )
    decisions = FakeAgentDecisionRepository()
    decision_store = (
        decision_store if decision_store is not None else AgentDecisionStore(decisions)
    )
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
        decision_store=decision_store,
        tool_result_store=tool_results,
        approval_port=store,
        approval_required_tools=approval_required,
    )
    return _Env(
        runtime=runtime,
        llm=llm,
        handler=resolved_handler,
        registry=registry,
        checkpoints=checkpoints,
        checkpoint_recorder=recorder,
        approvals=approvals,
        approval_store=store,
        decisions=decisions,
        decision_store=decision_store,
        tool_results=tool_results,
        tool_result_writer=runtime.tool_result_sink,
    )


def _open_approval(
    env: _Env,
    *,
    run_id: str = _RUN_ID,
    step_index: int = 0,
    tool_name: str = _TOOL_NAME,
    tool_call_id: str | None = "call_p7_1",
) -> Any:
    """测试辅助：直接构造"审批已存在"的持久化事实。"""
    return env.approval_store.request_approval(
        run_id=run_id,
        step_index=step_index,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
    )


# ============================================================
# §九 / §十二 / §十三 / §五十一：审批域（DTO / 状态机 / 身份）
# ============================================================


class TestApprovalDomain:
    def test_status_whitelist_and_decisions(self) -> None:
        assert APPROVAL_STATUSES == {
            APPROVAL_STATUS_PENDING,
            APPROVAL_STATUS_APPROVED,
            APPROVAL_STATUS_REJECTED,
        }
        assert APPROVAL_DECISIONS == {
            APPROVAL_STATUS_APPROVED,
            APPROVAL_STATUS_REJECTED,
        }
        assert APPROVAL_STATUS_PENDING not in APPROVAL_DECISIONS
        # §十三：不接受 retry / modify / edit_tool / change_arguments / change_tool
        for forbidden in (
            "retry",
            "modify",
            "edit_tool",
            "change_arguments",
            "change_tool",
        ):
            assert forbidden not in APPROVAL_DECISIONS
            assert not is_valid_approval_decision(forbidden)

    @pytest.mark.parametrize("bad", [None, "", "PENDING", "approved", 1, True])
    def test_invalid_decisions_rejected(self, bad: Any) -> None:
        assert not is_valid_approval_decision(bad)

    def test_state_machine_is_irreversible(self) -> None:
        # 幂等：同裁决重复 ⇒ None（不产生第二次转换）
        assert approval_conflict_code(APPROVAL_STATUS_APPROVED, "APPROVED") is None
        assert approval_conflict_code(APPROVAL_STATUS_REJECTED, "REJECTED") is None
        # 冲突：终态不可逆（APPROVED ↔ REJECTED 互斥；PENDING 不是终态裁决）
        assert (
            approval_conflict_code(APPROVAL_STATUS_APPROVED, "REJECTED")
            == AGENT_ERROR_APPROVAL_CONFLICT
        )
        assert (
            approval_conflict_code(APPROVAL_STATUS_REJECTED, "APPROVED")
            == AGENT_ERROR_APPROVAL_CONFLICT
        )
        assert (
            approval_conflict_code(APPROVAL_STATUS_PENDING, "APPROVED")
            == AGENT_ERROR_APPROVAL_CONFLICT
        )

    def test_approval_id_is_own_identity(self) -> None:
        first = new_approval_id()
        second = new_approval_id()
        assert first != second
        assert isinstance(first, str) and first.strip()

    def test_request_projection_exposes_only_frozen_fields(self) -> None:
        """§四十二：对外只有 approval_id / run_id / step_index / tool_name /
        tool_call_id / status / created_at —— **无** arguments / prompt / context。"""
        assert [field.name for field in fields(AgentApprovalRequest)] == [
            "approval_id",
            "run_id",
            "step_index",
            "tool_name",
            "tool_call_id",
            "status",
            "created_at",
        ]
        now = datetime.now(timezone.utc)
        row = _ApprovalRow(
            approval_id="apr-1",
            run_id=_RUN_ID,
            step_index=0,
            tool_name=_TOOL_NAME,
            tool_call_id="call-1",
            status=APPROVAL_STATUS_PENDING,
            decision=None,
            created_at=now,
        )
        request = approval_request_of(row)
        assert request.approval_id == "apr-1"
        assert request.status == APPROVAL_STATUS_PENDING
        for forbidden in ("arguments", "prompt", "context", "claim_token", "sql"):
            assert not hasattr(request, forbidden)


# ============================================================
# §十 / §十一 / §十八 / §十九：审批存储（幂等 / 裁决 / 冲突）
# ============================================================


class TestApprovalStore:
    def test_request_is_idempotent_per_execution_identity(self) -> None:
        repository = FakeAgentApprovalRepository()
        store = AgentApprovalStore(repository)

        first = store.request_approval(
            run_id=_RUN_ID, step_index=0, tool_name=_TOOL_NAME, tool_call_id="c1"
        )
        second = store.request_approval(
            run_id=_RUN_ID, step_index=0, tool_name=_TOOL_NAME, tool_call_id="c1"
        )

        assert first is not None and second is not None
        assert first.approval_id == second.approval_id  # 同一行（不产生第二个审批）
        assert len(repository.rows) == 1

    def test_approve_then_resolve_is_idempotent_then_conflict(self) -> None:
        repository = FakeAgentApprovalRepository()
        store = AgentApprovalStore(repository)
        row = store.request_approval(
            run_id=_RUN_ID, step_index=0, tool_name=_TOOL_NAME
        )

        approved = store.resolve(approval_id=row.approval_id, decision="APPROVED")
        assert approved is not None and approved.status == APPROVAL_STATUS_APPROVED
        # 幂等：再次 APPROVED ⇒ 无转换（返回 None；行状态不变）
        assert store.resolve(approval_id=row.approval_id, decision="APPROVED") is None
        assert store.get(row.approval_id).status == APPROVAL_STATUS_APPROVED
        # 冲突：APPROVED → REJECTED 不可能（条件 UPDATE 未命中 ⇒ None）
        assert store.resolve(approval_id=row.approval_id, decision="REJECTED") is None
        assert store.get(row.approval_id).status == APPROVAL_STATUS_APPROVED

    def test_reject_first_wins(self) -> None:
        repository = FakeAgentApprovalRepository()
        store = AgentApprovalStore(repository)
        row = store.request_approval(
            run_id=_RUN_ID, step_index=0, tool_name=_TOOL_NAME
        )

        rejected = store.resolve(approval_id=row.approval_id, decision="REJECTED")
        assert rejected is not None and rejected.status == APPROVAL_STATUS_REJECTED
        assert store.resolve(approval_id=row.approval_id, decision="APPROVED") is None
        assert store.get(row.approval_id).decision == APPROVAL_STATUS_REJECTED
        assert store.resolve(approval_id=row.approval_id, decision="REJECTED") is None

    def test_resolve_rejects_illegal_decision(self) -> None:
        repository = FakeAgentApprovalRepository()
        store = AgentApprovalStore(repository)
        row = store.request_approval(
            run_id=_RUN_ID, step_index=0, tool_name=_TOOL_NAME
        )
        for bad in ("retry", "modify", "PENDING", ""):
            with pytest.raises(ValueError):
                store.resolve(approval_id=row.approval_id, decision=bad)

    def test_missing_approval_is_not_found(self) -> None:
        repository = FakeAgentApprovalRepository()
        store = AgentApprovalStore(repository)
        assert store.get("nope") is None
        assert store.get_for_step(run_id=_RUN_ID, step_index=3) is None
        assert store.resolve(approval_id="nope", decision="APPROVED") is None

    def test_write_failure_returns_none(self) -> None:
        """§十一：写失败 ⇒ ``None``（**不**吞成"已创建"）⇒ 调用方 fail-closed。"""
        store = AgentApprovalStore(
            FakeAgentApprovalRepository(error=RuntimeError("db down"))
        )
        assert (
            store.request_approval(
                run_id=_RUN_ID, step_index=0, tool_name=_TOOL_NAME
            )
            is None
        )

    def test_read_failure_raises(self) -> None:
        store = AgentApprovalStore(
            FakeAgentApprovalRepository(error=RuntimeError("db down"))
        )
        with pytest.raises(RuntimeError):
            store.get_for_step(run_id=_RUN_ID, step_index=0)

    def test_store_requires_repository_capabilities(self) -> None:
        class _Broken:
            def create(self, **kwargs: Any) -> None: ...

        with pytest.raises(ValueError):
            AgentApprovalStore(_Broken())

    async def test_async_entries_delegate(self) -> None:
        repository = FakeAgentApprovalRepository()
        store = AgentApprovalStore(repository)
        row = await store.arequest_approval(
            run_id=_RUN_ID, step_index=0, tool_name=_TOOL_NAME, tool_call_id="c9"
        )
        assert row is not None
        fetched = await store.aget_for_step(run_id=_RUN_ID, step_index=0)
        assert fetched.approval_id == row.approval_id
        resolved = await store.aresolve(
            approval_id=row.approval_id, decision="APPROVED"
        )
        assert resolved.status == APPROVAL_STATUS_APPROVED


# ============================================================
# §七 / §十四 / §十五 / §十六 / §四十九 / §五十九：审批门（暂停）
# ============================================================


class TestApprovalGate:
    async def test_required_tool_pauses_before_execution(self) -> None:
        env = _env(FakeLLM([_TOOL_CALL, _FINAL]))

        result = await env.runtime.run(question="查询 MAT001 库存")

        # §十五：Tool Handler / 执行 / 结果全部 = 0
        assert result.status == AGENT_STATUS_WAITING_APPROVAL
        assert result.error == AGENT_ERROR_APPROVAL_REQUIRED
        assert env.registry.execute_calls == []
        assert env.handler.calls == []
        assert env.tool_results.rows == []
        # §十四：Checkpoint = PAUSED / WAITING_APPROVAL + pending_*
        checkpoint = env.checkpoints.get(result.run_id)
        assert checkpoint.status == CHECKPOINT_STATUS_PAUSED
        assert checkpoint.next_action == CHECKPOINT_NEXT_ACTION_WAITING_APPROVAL
        assert checkpoint.pending_step_index == 0
        assert checkpoint.pending_decision_ref == f"{result.run_id}:0"
        assert checkpoint.pending_tool_call_id == "call_p7_1"
        assert checkpoint.pending_tool_result_ref is None  # Tool 未执行 ⇒ 无结果引用
        # §四十九：不伪造 ToolExecution 步（step projection 为空）
        assert result.step_records == ()
        assert env.checkpoint_recorder.waiting_writes == [
            (0, f"{result.run_id}:0", "call_p7_1")
        ]
        # Approval（PENDING）+ 身份透出
        assert result.approval_id is not None
        row = env.approvals.get(result.approval_id)
        assert row.status == APPROVAL_STATUS_PENDING
        assert row.run_id == result.run_id and row.step_index == 0
        assert row.tool_name == _TOOL_NAME and row.tool_call_id == "call_p7_1"
        # LLM 只调用一次（决策步已发生；未继续下一次）
        assert result.llm_calls == 1

    async def test_not_required_tool_executes_directly(self) -> None:
        """默认（无 approval_required_tools）行为与 P-6 一致（回归）。"""
        env = _env(FakeLLM([_TOOL_CALL, _FINAL]), approval_required=())

        result = await env.runtime.run(question="查询 MAT001 库存")

        assert result.status == AGENT_STATUS_COMPLETED
        assert [name for name, _ in env.registry.execute_calls] == [_TOOL_NAME]
        assert env.approvals.rows == {}
        assert env.checkpoint_recorder.waiting_writes == []
        assert env.checkpoints.get(result.run_id).next_action == "COMPLETED"

    async def test_approval_row_has_no_arguments(self) -> None:
        """§十一：审批**不**保存 tool arguments（连 hash 都不需要）。"""
        env = _env(FakeLLM([_TOOL_CALL, _FINAL]))

        result = await env.runtime.run(question="查询 MAT001 库存")

        row = env.approvals.get(result.approval_id)
        assert not hasattr(row, "arguments")
        assert "MAT001" not in repr(row)

    async def test_store_failure_fails_closed(self) -> None:
        """§十一：审批无法持久化 ⇒ 不暂停、不执行 Tool（fail-closed）。"""
        env = _env(
            FakeLLM([_TOOL_CALL, _FINAL]),
            approval_repository=FakeAgentApprovalRepository(
                error=RuntimeError("db down")
            ),
        )

        result = await env.runtime.run(question="查询 MAT001 库存")

        assert result.status == AGENT_STATUS_FAILED
        assert result.error == AGENT_ERROR_APPROVAL_STORE_FAILURE
        assert env.registry.execute_calls == []
        assert env.tool_results.rows == []
        # 失败步被记录（授权/执行门失败与既有语义一致）
        assert [step.error for step in result.step_records] == [
            AGENT_ERROR_APPROVAL_STORE_FAILURE
        ]

    async def test_pauses_only_after_decision_is_durable(self) -> None:
        """无 Decision Store ⇒ 暂停不可恢复 ⇒ fail-closed（不创建审批、不执行）。"""
        env = _env(FakeLLM([_TOOL_CALL, _FINAL]), decision_store=None)
        # 未接线 Decision Store ⇒ Runtime 不持久化 Decision ⇒ 不允许暂停
        env.runtime._decision_store = None  # noqa: SLF001 —— 测试专用装配

        result = await env.runtime.run(question="查询 MAT001 库存")

        assert result.status == AGENT_STATUS_FAILED
        assert result.error == AGENT_ERROR_DECISION_PERSISTENCE_FAILURE
        assert env.approvals.rows == {}  # 不创建审批（不可恢复的暂停）
        assert env.registry.execute_calls == []

    async def test_pause_does_not_write_agent_run(self) -> None:
        """§十六：暂停**不**产生 AgentRun 终态记录（Runtime 侧无 Run 写点）。"""
        env = _env(FakeLLM([_TOOL_CALL, _FINAL]))

        result = await env.runtime.run(question="查询 MAT001 库存")

        assert result.status == AGENT_STATUS_WAITING_APPROVAL
        # Runtime 从不写 AgentRun（写点属 Orchestrator）；暂停路径亦不写终态 Checkpoint
        assert env.checkpoint_recorder.terminal_writes == []
        assert env.checkpoints.get(result.run_id).status == CHECKPOINT_STATUS_PAUSED


# ============================================================
# §十七 / §十八 / §二十 / §二十一 / §二十二 / §五十五：裁决 + Resume
# ============================================================


class TestApprovalResume:
    async def _paused_env(self) -> tuple[_Env, Any]:
        """构造"已暂停待审批"的真实状态（Approval = PENDING）。"""
        env = _env(FakeLLM([_TOOL_CALL, _FINAL]))
        paused = await env.runtime.run(question="查询 MAT001 库存")
        assert paused.status == AGENT_STATUS_WAITING_APPROVAL
        return env, paused

    async def test_pending_resume_stays_waiting(self) -> None:
        env, paused = await self._paused_env()

        resumed = await env.runtime.resume(
            run_id=paused.run_id, question="查询 MAT001 库存"
        )

        assert resumed.status == AGENT_STATUS_WAITING_APPROVAL
        assert resumed.error_code == AGENT_ERROR_APPROVAL_REQUIRED
        assert resumed.approval_id == paused.approval_id
        assert resumed.llm_calls == 0 and resumed.tool_calls == 0
        assert env.registry.execute_calls == []
        assert env.llm.calls == 1  # 只有暂停前的决策调用

    async def test_approved_resume_executes_tool_then_continues(self) -> None:
        env, paused = await self._paused_env()
        await env.approval_store.aresolve(
            approval_id=paused.approval_id, decision="APPROVED"
        )

        resumed = await env.runtime.resume(
            run_id=paused.run_id, question="查询 MAT001 库存"
        )

        # Tool 执行一次（重新授权通过）→ Tool Result 持久化 → 续跑下一次 LLM
        assert [name for name, _ in env.registry.execute_calls] == [_TOOL_NAME]
        assert env.handler.calls == [{"material_code": "MAT001"}]
        assert len(env.tool_results.rows) == 1
        row = env.tool_results.rows[0]
        assert (row.run_id, row.step_index, row.success) == (paused.run_id, 0, True)
        assert row.tool_call_id == "call_p7_1"
        assert resumed.status == AGENT_STATUS_COMPLETED
        assert resumed.content == "库存 120 件。"
        assert resumed.replayed_step_index == 0
        assert resumed.tool_calls == 1 and resumed.llm_calls == 1
        assert resumed.approval_id == paused.approval_id
        # Checkpoint 已推进（ACTIVE / LLM_DECISION；Tool Result 已 durable）
        checkpoint = env.checkpoints.get(paused.run_id)
        assert checkpoint.pending_step_index == 1
        assert checkpoint.next_action == "COMPLETED"  # 续跑后的终态 Decision 写点

    async def test_rejected_resume_fails_without_tool(self) -> None:
        env, paused = await self._paused_env()
        await env.approval_store.aresolve(
            approval_id=paused.approval_id, decision="REJECTED"
        )

        resumed = await env.runtime.resume(
            run_id=paused.run_id, question="查询 MAT001 库存"
        )

        assert resumed.status == AGENT_STATUS_FAILED
        assert resumed.error_code == AGENT_ERROR_APPROVAL_REJECTED
        assert env.registry.execute_calls == []
        assert env.tool_results.rows == []  # §二十二：REJECTED ⇒ AgentToolResult = 0
        assert resumed.llm_calls == 0 and resumed.tool_calls == 0
        assert env.llm.calls == 1  # 不重问 LLM

    async def test_approved_resume_requires_authorization_again(self) -> None:
        """§二十 / §四十一：Approval ≠ Authorization；批准后仍必须重新授权。"""
        env, paused = await self._paused_env()
        await env.approval_store.aresolve(
            approval_id=paused.approval_id, decision="APPROVED"
        )
        # capability / policy 收紧（例如项目停用该 Tool）⇒ 重新授权失败
        env.runtime._allowed_tools = ()  # noqa: SLF001 —— 测试专用收紧

        resumed = await env.runtime.resume(
            run_id=paused.run_id, question="查询 MAT001 库存"
        )

        assert resumed.status == AGENT_STATUS_FAILED
        assert resumed.error_code == AGENT_ERROR_TOOL_AUTHORIZATION_FAILED
        assert env.registry.execute_calls == []
        assert env.tool_results.rows == []

    async def test_approved_tool_failure_replays_as_failed(self) -> None:
        from backend.app.tools.errors import ToolExecutionError

        env = _env(
            FakeLLM([_TOOL_CALL, _FINAL]),
            handler=_failing_handler(ToolExecutionError(_TOOL_NAME, "down")),
        )
        paused = await env.runtime.run(question="查询 MAT001 库存")
        await env.approval_store.aresolve(
            approval_id=paused.approval_id, decision="APPROVED"
        )

        resumed = await env.runtime.resume(
            run_id=paused.run_id, question="查询 MAT001 库存"
        )

        assert resumed.status == AGENT_STATUS_FAILED
        assert resumed.error_code == "TOOL_EXECUTION_FAILURE"
        assert len(env.tool_results.rows) == 1  # 失败事实仍持久化（不丢失）
        assert env.tool_results.rows[0].success is False
        assert env.llm.calls == 1  # 不继续 LLM

    async def test_duplicate_resume_executes_tool_once(self) -> None:
        """§十八：重复裁决/重复 Resume 幂等 ⇒ Tool **不**执行两次。"""
        env, paused = await self._paused_env()
        await env.approval_store.aresolve(
            approval_id=paused.approval_id, decision="APPROVED"
        )

        first = await env.runtime.resume(
            run_id=paused.run_id, question="查询 MAT001 库存"
        )
        second = await env.runtime.resume(
            run_id=paused.run_id, question="查询 MAT001 库存"
        )

        assert first.status == AGENT_STATUS_COMPLETED
        assert second.status in (AGENT_STATUS_COMPLETED, AGENT_STATUS_FAILED)
        assert len(env.registry.execute_calls) == 1  # Tool 只执行一次
        assert len(env.tool_results.rows) == 1

    async def test_run_id_unchanged_and_request_scoped(self) -> None:
        env, paused = await self._paused_env()
        await env.approval_store.aresolve(
            approval_id=paused.approval_id, decision="APPROVED"
        )

        resumed = await env.runtime.resume(
            run_id=paused.run_id, question="查询 MAT001 库存", request_id="req-p7"
        )

        assert resumed.run_id == paused.run_id
        assert env.tool_results.rows[0].run_id == paused.run_id


def _failing_handler(error: Exception) -> Any:
    from tests.test_agent_runtime import RecordingHandler

    return RecordingHandler(error=error)


# ============================================================
# §十一 / §二十三 / §五十九：fail-closed 与安全边界
# ============================================================


class TestApprovalFailClosed:
    async def test_missing_approval_row_fails_closed(self) -> None:
        env, paused = await self._paused_env_isolated(delete_approval=True)

        resumed = await env.runtime.resume(
            run_id=paused.run_id, question="查询 MAT001 库存"
        )

        assert resumed.status == AGENT_STATUS_FAILED
        assert resumed.error_code == AGENT_ERROR_APPROVAL_NOT_FOUND
        assert env.registry.execute_calls == []

    async def test_approval_identity_mismatch_fails_closed(self) -> None:
        env, paused = await self._paused_env_isolated()
        # 篡改：审批指向另一个 Tool（身份不一致 ⇒ 不能执行）
        row = env.approvals.get(paused.approval_id)
        env.approvals.rows[paused.approval_id] = replace(row, tool_name="get_work_order")

        resumed = await env.runtime.resume(
            run_id=paused.run_id, question="查询 MAT001 库存"
        )

        assert resumed.status == AGENT_STATUS_FAILED
        assert resumed.error_code == AGENT_ERROR_APPROVAL_CONFLICT
        assert env.registry.execute_calls == []

    async def test_approval_tool_call_id_mismatch_fails_closed(self) -> None:
        env, paused = await self._paused_env_isolated()
        row = env.approvals.get(paused.approval_id)
        env.approvals.rows[paused.approval_id] = replace(
            row, tool_call_id="call-other"
        )

        resumed = await env.runtime.resume(
            run_id=paused.run_id, question="查询 MAT001 库存"
        )

        assert resumed.status == AGENT_STATUS_FAILED
        assert resumed.error_code == AGENT_ERROR_APPROVAL_CONFLICT
        assert env.registry.execute_calls == []

    async def test_approval_read_failure_fails_closed(self) -> None:
        env, paused = await self._paused_env_isolated()
        env.approval_store._repository._error = RuntimeError("db down")  # noqa: SLF001

        resumed = await env.runtime.resume(
            run_id=paused.run_id, question="查询 MAT001 库存"
        )

        assert resumed.status == AGENT_STATUS_FAILED
        assert resumed.error_code == AGENT_ERROR_APPROVAL_NOT_FOUND
        assert env.registry.execute_calls == []

    async def test_unknown_approval_status_fails_closed(self) -> None:
        env, paused = await self._paused_env_isolated()
        row = env.approvals.get(paused.approval_id)
        env.approvals.rows[paused.approval_id] = replace(row, status="EXPIRED")

        resumed = await env.runtime.resume(
            run_id=paused.run_id, question="查询 MAT001 库存"
        )

        assert resumed.status == AGENT_STATUS_FAILED
        assert resumed.error_code == AGENT_ERROR_APPROVAL_CONFLICT
        assert env.registry.execute_calls == []

    async def test_paused_without_decision_ref_fails_closed(self) -> None:
        env, paused = await self._paused_env_isolated()
        checkpoint = env.checkpoints.rows[paused.run_id]
        env.checkpoints.rows[paused.run_id] = replace(
            checkpoint, pending_decision_ref=None
        )

        resumed = await env.runtime.resume(
            run_id=paused.run_id, question="查询 MAT001 库存"
        )

        assert resumed.status == AGENT_STATUS_FAILED
        assert resumed.error_code == "AGENT_CHECKPOINT_DECISION_MISMATCH"

    async def _paused_env_isolated(
        self, *, delete_approval: bool = False
    ) -> tuple[_Env, Any]:
        env = _env(FakeLLM([_TOOL_CALL, _FINAL]))
        paused = await env.runtime.run(question="查询 MAT001 库存")
        assert paused.status == AGENT_STATUS_WAITING_APPROVAL
        if delete_approval:
            env.approvals.delete_for_test(paused.approval_id)
        return env, paused


class TestApprovalSecurity:
    def test_assembly_requires_port_when_policy_non_empty(self) -> None:
        service, _registry, _handler = _execution_service()
        with pytest.raises(AgentPolicyError):
            AgentRuntime(
                llm=FakeLLM([_FINAL]),
                tool_execution=service,
                policy=AgentRuntimePolicy(
                    max_execution_time_ms=1_000, max_cost_tokens=10
                ),
                allowed_tools=(_TOOL_NAME,),
                approval_required_tools=(_TOOL_NAME,),
            )

    def test_approval_tool_must_be_allowed(self) -> None:
        """审批不得给未授权 Tool 挂门（不扩权；§四十）。"""
        service, _registry, _handler = _execution_service()
        with pytest.raises(AgentPolicyError):
            AgentRuntime(
                llm=FakeLLM([_FINAL]),
                tool_execution=service,
                policy=AgentRuntimePolicy(
                    max_execution_time_ms=1_000, max_cost_tokens=10
                ),
                allowed_tools=(_TOOL_NAME,),
                approval_port=AgentApprovalStore(FakeAgentApprovalRepository()),
                approval_required_tools=("delete_inventory",),
            )

    @pytest.mark.parametrize("bad", [None, 1, "get_inventory", ("a", "a")])
    def test_invalid_policy_shapes_rejected(self, bad: Any) -> None:
        service, _registry, _handler = _execution_service()
        with pytest.raises(AgentPolicyError):
            AgentRuntime(
                llm=FakeLLM([_FINAL]),
                tool_execution=service,
                policy=AgentRuntimePolicy(
                    max_execution_time_ms=1_000, max_cost_tokens=10
                ),
                allowed_tools=(_TOOL_NAME,),
                approval_port=AgentApprovalStore(FakeAgentApprovalRepository()),
                approval_required_tools=bad,
            )

    def test_runtime_holds_only_injected_port(self) -> None:
        env = _env(FakeLLM([_FINAL]))
        assert env.runtime.approval_required_tools == (_TOOL_NAME,)
        assert env.runtime.approval_port is env.approval_store

    def test_pause_writes_nothing_sensitive(self) -> None:
        """审批 / Checkpoint / Step 均不含 arguments 之外的敏感内容（离线断言）。"""
        env = _env(FakeLLM([_TOOL_CALL, _FINAL]))
        env.runtime  # noqa: B018

        async def _run() -> Any:
            return await env.runtime.run(question="查询 MAT001 库存")

        result = asyncio.run(_run())
        row = env.approvals.get(result.approval_id)
        for forbidden in ("prompt", "messages", "api_key", "password", "Bearer"):
            assert not hasattr(row, forbidden)
        checkpoint = env.checkpoints.get(result.run_id)
        assert not hasattr(checkpoint, "tool_arguments")
        assert not hasattr(checkpoint, "permissions")

    def test_approval_module_has_no_db_or_llm_import(self) -> None:
        """域层 approval.py 0 DB / 0 LLM（仓储必须注入）。"""
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1]
            / "backend"
            / "app"
            / "agent"
            / "approval.py"
        ).read_text(encoding="utf-8")
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
        # 只写/读/裁决：不出现 Resume / Tool 执行入口（§二十三）
        for forbidden in (
            "def resume",
            "def replay",
            "ToolExecutionService",
            "AgentRuntime",
        ):
            assert forbidden not in source, forbidden
        assert "def resolve" in source and "def request_approval" in source
