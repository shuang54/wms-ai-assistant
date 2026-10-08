"""Phase 5 P-8 Agent Read Service —— **离线**（内存 Fake 仓储；零 DB）。

覆盖（任务书 §六 / §七 – §十二 / §二十一 / §二十二 / §二十四 – §三十）：

```text
1  Query only       只调用仓储的**只读**方法；无 LLM / Tool / Resume / Approve / Cancel
2  AgentRunSummary  13 字段逐字段映射；无终态记录 ⇒ run=None（不推导状态）
3  views            Checkpoint（无 claim_token）· Approval · Decision（无 arguments /
                    content）· ToolResult（无 payload）· Step
4  project 隔离     A 可读 A / A 不可读 B / B 可读 B / B 不可读 A；同码不泄露存在性
5  归属 fail-closed 无 project 可证明 / Tool Result 跨 project ⇒ NotFound
6  分页校验         limit ≤ 0 / > 100 / offset < 0 ⇒ ValueError（触达 DB 之前）
7  安全字段         视图字段集 == 文档清单；禁入字段一个都不存在
8  只读审计         服务源码无写方法；Fake 只收到只读调用
```

本文件同时提供 P-8 测试基建（6 个只读 Fake 仓储 + 行构造器），
被 ``test_agent_timeline.py`` / ``test_agent_read_api.py`` 复用。
"""
from __future__ import annotations

import ast
import inspect
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.app.agent.approval import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
)
from backend.app.agent.checkpoint import (
    CHECKPOINT_NEXT_ACTION_COMPLETED,
    CHECKPOINT_NEXT_ACTION_LLM_DECISION,
    CHECKPOINT_NEXT_ACTION_WAITING_APPROVAL,
    CHECKPOINT_STATUS_ACTIVE,
    CHECKPOINT_STATUS_PAUSED,
    CHECKPOINT_STATUS_TERMINAL,
    AgentCheckpoint,
)
from backend.app.agent.decision_store import AgentDecisionRecord
from backend.app.agent.recorder import AgentRunRecord
from backend.app.db.agent_approval_repository import AgentApprovalRead
from backend.app.db.agent_step_repository import AgentStepRead
from backend.app.db.agent_tool_result_repository import AgentToolResultRead
from backend.app.dto.agent_read import (
    AGENT_APPROVAL_VIEW_FIELDS,
    AGENT_CHECKPOINT_VIEW_FIELDS,
    AGENT_DECISION_VIEW_FIELDS,
    AGENT_READ_DEFAULT_LIMIT,
    AGENT_READ_MAX_LIMIT,
    AGENT_RUN_SUMMARY_FIELDS,
    AGENT_STEP_VIEW_FIELDS,
    AGENT_TOOL_RESULT_VIEW_FIELDS,
    AgentApprovalView,
    AgentCheckpointView,
    AgentDecisionView,
    AgentRunSummary,
    AgentStepView,
    AgentToolResultView,
)
from backend.app.services.agent_read_service import (
    AgentReadService,
    AgentRunNotFoundError,
)

__all__ = [
    "FakeRunRepository",
    "FakeStepRepository",
    "FakeDecisionRepository",
    "FakeToolResultRepository",
    "FakeCheckpointRepository",
    "FakeApprovalRepository",
    "build_service",
    "run_record",
    "step_read",
    "decision_record",
    "tool_result_read",
    "approval_read",
    "checkpoint_dto",
    "TestAgentRunViewMapping",
    "TestAgentSubViewMapping",
    "TestAgentReadProjectIsolation",
    "TestAgentReadPagination",
    "TestAgentReadSecurity",
]

_PROJECT_A = "project-A"
_PROJECT_B = "project-B"
_RUN_ID = "run-p8-read"
_BASE = datetime(2026, 10, 8, 3, 0, 0, tzinfo=timezone.utc)


def _at(seconds: int) -> datetime:
    return _BASE + timedelta(seconds=seconds)


# ============================================================
# 只读 Fake 仓储（**只**实现读方法 ⇒ 写调用会 AttributeError）
# ============================================================


class _ReadOnlyFake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))


class FakeRunRepository(_ReadOnlyFake):
    def __init__(self, rows: dict[str, AgentRunRecord] | None = None) -> None:
        super().__init__()
        self.rows = dict(rows or {})

    def get(self, run_id: str) -> AgentRunRecord | None:
        self._record("get", run_id)
        return self.rows.get(run_id)


class FakeStepRepository(_ReadOnlyFake):
    def __init__(self, rows: list[AgentStepRead] | None = None) -> None:
        super().__init__()
        self.rows = list(rows or [])

    def list_for_run(self, run_id: str) -> list[AgentStepRead]:
        self._record("list_for_run", run_id)
        return sorted(
            (row for row in self.rows if row.run_id == run_id),
            key=lambda row: row.step_index,
        )


class FakeDecisionRepository(_ReadOnlyFake):
    def __init__(self, rows: list[AgentDecisionRecord] | None = None) -> None:
        super().__init__()
        self.rows = list(rows or [])

    def list_for_run(self, run_id: str) -> list[AgentDecisionRecord]:
        self._record("list_for_run", run_id)
        return sorted(
            (row for row in self.rows if row.run_id == run_id),
            key=lambda row: row.step_index,
        )


class FakeToolResultRepository(_ReadOnlyFake):
    def __init__(self, rows: list[AgentToolResultRead] | None = None) -> None:
        super().__init__()
        self.rows = list(rows or [])

    def list_for_run(self, run_id: str) -> list[AgentToolResultRead]:
        self._record("list_for_run", run_id)
        return sorted(
            (row for row in self.rows if row.run_id == run_id),
            key=lambda row: row.step_index,
        )


class FakeCheckpointRepository(_ReadOnlyFake):
    def __init__(self, rows: dict[str, AgentCheckpoint] | None = None) -> None:
        super().__init__()
        self.rows = dict(rows or {})

    def get(self, run_id: str) -> AgentCheckpoint | None:
        self._record("get", run_id)
        return self.rows.get(run_id)


class FakeApprovalRepository(_ReadOnlyFake):
    def __init__(self, rows: list[AgentApprovalRead] | None = None) -> None:
        super().__init__()
        self.rows = list(rows or [])

    def list_for_run(self, run_id: str) -> list[AgentApprovalRead]:
        self._record("list_for_run", run_id)
        return sorted(
            (row for row in self.rows if row.run_id == run_id),
            key=lambda row: row.step_index,
        )


# ============================================================
# 行构造器（默认全部字段合法；测试按需覆盖）
# ============================================================


def run_record(
    *,
    run_id: str = _RUN_ID,
    project_id: str | None = _PROJECT_A,
    status: str = "COMPLETED",
    error_code: str | None = None,
    limit_reason: str | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        request_id="req-p8-1",
        project_id=project_id,
        status=status,
        error_code=error_code,
        limit_reason=limit_reason,
        step_count=2,
        tool_call_count=1,
        llm_call_count=2,
        token_usage_unverifiable=False,
        started_at=started_at or _at(0),
        finished_at=finished_at or _at(9),
        duration_ms=9000.0,
    )


def step_read(*, step_index: int = 0, created_at: datetime | None = None) -> AgentStepRead:
    return AgentStepRead(
        run_id=_RUN_ID,
        step_index=step_index,
        step_type="TOOL_CALL",
        status="OK",
        tool_name="get_inventory",
        error_code=None,
        elapsed_ms=120.5,
        created_at=created_at or _at(4),
    )


def decision_record(
    *, step_index: int = 0, created_at: datetime | None = None
) -> AgentDecisionRecord:
    """注意：``arguments`` / ``content`` **故意**带敏感形状内容，用于断言不外泄。"""
    return AgentDecisionRecord(
        run_id=_RUN_ID,
        step_index=step_index,
        decision_ref=f"{_RUN_ID}:{step_index}",
        decision_type="TOOL_CALL",
        tool_call_id="call-p8-1",
        tool_name="get_inventory",
        arguments={"material_code": "MAT001", "password": "should-not-leak"},
        content="SELECT * FROM secret_users -- should-not-leak",
        finish_reason="tool_calls",
        decision_unavailable=False,
        created_at=created_at or _at(2),
    )


def tool_result_read(
    *, step_index: int = 0, success: bool = True, created_at: datetime | None = None
) -> AgentToolResultRead:
    return AgentToolResultRead(
        run_id=_RUN_ID,
        step_index=step_index,
        tool_name="get_inventory",
        project_id=_PROJECT_A,
        success=success,
        error_code=None if success else "TOOL_EXECUTION_FAILURE",
        result_hash="a" * 64,
        result_size_bytes=128,
        result_truncated=False,
        result_unavailable=False,
        tool_call_id="call-p8-1",
        created_at=created_at or _at(5),
    )


def approval_read(
    *,
    step_index: int = 0,
    status: str = APPROVAL_STATUS_PENDING,
    decision: str | None = None,
    resolved_at: datetime | None = None,
    created_at: datetime | None = None,
) -> AgentApprovalRead:
    return AgentApprovalRead(
        approval_id=f"apr-p8-{step_index}",
        run_id=_RUN_ID,
        step_index=step_index,
        tool_call_id="call-p8-1",
        tool_name="get_inventory",
        status=status,
        decision=decision,
        reason_code=None,
        created_at=created_at or _at(3),
        resolved_at=resolved_at,
    )


def checkpoint_dto(
    *,
    status: str = CHECKPOINT_STATUS_ACTIVE,
    next_action: str = CHECKPOINT_NEXT_ACTION_LLM_DECISION,
    pending_step_index: int | None = 1,
    cancel_requested: bool = False,
) -> AgentCheckpoint:
    return AgentCheckpoint(
        run_id=_RUN_ID,
        state_version=3,
        claim_token="claim-SECRET-should-not-leak",
        status=status,
        next_action=next_action,
        pending_step_index=pending_step_index,
        pending_tool_call_id=None,
        pending_decision_ref=None,
        pending_tool_result_ref=f"{_RUN_ID}:0",
        created_at=_at(1),
        updated_at=_at(6),
        cancel_requested=cancel_requested,
        cancel_requested_at=_at(6) if cancel_requested else None,
    )


def build_service(
    *,
    run: FakeRunRepository | None = None,
    step: FakeStepRepository | None = None,
    decision: FakeDecisionRepository | None = None,
    tool_result: FakeToolResultRepository | None = None,
    checkpoint: FakeCheckpointRepository | None = None,
    approval: FakeApprovalRepository | None = None,
) -> tuple[AgentReadService, dict[str, _ReadOnlyFake]]:
    repository = {
        "run": run if run is not None else FakeRunRepository(),
        "step": step if step is not None else FakeStepRepository(),
        "decision": decision if decision is not None else FakeDecisionRepository(),
        "tool_result": (
            tool_result
            if tool_result is not None
            else FakeToolResultRepository()
        ),
        "checkpoint": (
            checkpoint if checkpoint is not None else FakeCheckpointRepository()
        ),
        "approval": (
            approval if approval is not None else FakeApprovalRepository()
        ),
    }
    service = AgentReadService(
        run_repository=repository["run"],
        step_repository=repository["step"],
        decision_repository=repository["decision"],
        tool_result_repository=repository["tool_result"],
        checkpoint_repository=repository["checkpoint"],
        approval_repository=repository["approval"],
    )
    return service, repository


def _iter_dataclass_instances(value: Any) -> list[Any]:
    """递归展开 dataclass / tuple / list（**只**用于字段名审计）。"""
    import dataclasses

    found: list[Any] = []
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        found.append(value)
        for field in dataclasses.fields(value):
            found.extend(_iter_dataclass_instances(getattr(value, field.name)))
    elif isinstance(value, (tuple, list)):
        for item in value:
            found.extend(_iter_dataclass_instances(item))
    return found


def _full_service() -> tuple[AgentReadService, dict[str, _ReadOnlyFake]]:
    """一套"完整 Run"：Run + Checkpoint + Approval + Step + Decision + ToolResult。"""
    return build_service(
        run=FakeRunRepository({_RUN_ID: run_record()}),
        step=FakeStepRepository([step_read(step_index=0)]),
        decision=FakeDecisionRepository([decision_record(step_index=0)]),
        tool_result=FakeToolResultRepository([tool_result_read(step_index=0)]),
        checkpoint=FakeCheckpointRepository({_RUN_ID: checkpoint_dto()}),
        approval=FakeApprovalRepository([approval_read(step_index=1)]),
    )


# ============================================================
# §七 / §八：AgentRunSummary
# ============================================================


class TestAgentRunViewMapping:
    def test_summary_is_exact_field_mapping(self) -> None:
        service, _repositories = _full_service()

        view = service.get_run(_RUN_ID, _PROJECT_A)

        assert view.run is not None
        assert [field.name for field in fields(view.run)] == list(
            AGENT_RUN_SUMMARY_FIELDS
        )
        assert view.run.run_id == _RUN_ID
        assert view.run.request_id == "req-p8-1"
        assert view.run.project_id == _PROJECT_A
        assert view.run.status == "COMPLETED"
        assert view.run.step_count == 2
        assert view.run.tool_call_count == 1
        assert view.run.llm_call_count == 2
        assert view.run.token_usage_unverifiable is False
        assert view.run.started_at == _at(0)
        assert view.run.finished_at == _at(9)
        assert view.run.duration_ms == 9000.0

    def test_status_is_run_status_not_checkpoint_next_action(self) -> None:
        """§八：``status`` 来自 AgentRun；``next_action`` 只在 checkpoint 视图。"""
        service, _repositories = build_service(
            run=FakeRunRepository({_RUN_ID: run_record(status="CANCELLED")}),
            checkpoint=FakeCheckpointRepository(
                {
                    _RUN_ID: checkpoint_dto(
                        status=CHECKPOINT_STATUS_TERMINAL,
                        next_action="CANCELLED",
                    )
                }
            ),
        )

        view = service.get_run(_RUN_ID, _PROJECT_A)

        assert view.run is not None and view.run.status == "CANCELLED"
        assert view.checkpoint is not None
        assert view.checkpoint.next_action == "CANCELLED"

    def test_run_without_terminal_record_has_no_summary(self) -> None:
        """崩溃窗口：有 Tool Result（可证明归属）但**无** AgentRun 行 ⇒ run=None。"""
        service, _repositories = build_service(
            tool_result=FakeToolResultRepository([tool_result_read()]),
            checkpoint=FakeCheckpointRepository(
                {
                    _RUN_ID: checkpoint_dto(
                        status=CHECKPOINT_STATUS_PAUSED,
                        next_action=CHECKPOINT_NEXT_ACTION_WAITING_APPROVAL,
                        pending_step_index=1,
                    )
                }
            ),
        )

        view = service.get_run(_RUN_ID, _PROJECT_A)

        assert view.run is None  # **不**推导状态
        assert view.checkpoint is not None
        assert view.checkpoint.status == CHECKPOINT_STATUS_PAUSED
        assert len(view.tool_results) == 1

    def test_missing_run_is_not_found(self) -> None:
        service, _repositories = build_service()
        with pytest.raises(AgentRunNotFoundError):
            service.get_run(_RUN_ID, _PROJECT_A)

    def test_invalid_inputs_rejected_before_db(self) -> None:
        service, repositories = build_service()
        for bad in ("", "   ", 1, None):
            with pytest.raises(ValueError):
                service.get_run(bad, _PROJECT_A)  # type: ignore[arg-type]
            with pytest.raises(ValueError):
                service.get_run(_RUN_ID, bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            service.get_run("x" * 65, _PROJECT_A)
        with pytest.raises(ValueError):
            service.get_run(_RUN_ID, "x" * 129)
        assert repositories["run"].calls == []  # 触达 DB 之前失败


# ============================================================
# §九 – §十二：子视图
# ============================================================


class TestAgentSubViewMapping:
    def test_checkpoint_view_drops_claim_token(self) -> None:
        service, _repositories = _full_service()

        view = service.get_checkpoint(_RUN_ID, _PROJECT_A)

        assert view is not None
        assert [field.name for field in fields(view)] == list(
            AGENT_CHECKPOINT_VIEW_FIELDS
        )
        assert not hasattr(view, "claim_token")
        assert "SECRET" not in repr(view)
        assert view.state_version == 3
        assert view.pending_tool_result_ref == f"{_RUN_ID}:0"
        assert view.cancel_requested is False

    def test_checkpoint_cancelled_signal_is_exposed(self) -> None:
        service, _repositories = build_service(
            run=FakeRunRepository({_RUN_ID: run_record(status="CANCELLED")}),
            checkpoint=FakeCheckpointRepository(
                {
                    _RUN_ID: checkpoint_dto(
                        status=CHECKPOINT_STATUS_TERMINAL,
                        next_action="CANCELLED",
                        cancel_requested=True,
                    )
                }
            ),
        )
        view = service.get_checkpoint(_RUN_ID, _PROJECT_A)
        assert view is not None
        assert view.cancel_requested is True
        assert view.cancel_requested_at == _at(6)
        assert view.next_action == "CANCELLED"

    def test_checkpoint_absent_returns_none(self) -> None:
        service, _repositories = build_service(
            run=FakeRunRepository({_RUN_ID: run_record()})
        )
        assert service.get_checkpoint(_RUN_ID, _PROJECT_A) is None

    @pytest.mark.parametrize(
        "status,decision,resolved",
        [
            (APPROVAL_STATUS_PENDING, None, False),
            (APPROVAL_STATUS_APPROVED, APPROVAL_STATUS_APPROVED, True),
            (APPROVAL_STATUS_REJECTED, APPROVAL_STATUS_REJECTED, True),
        ],
    )
    def test_approval_view_states(
        self, status: str, decision: str | None, resolved: bool
    ) -> None:
        service, _repositories = build_service(
            run=FakeRunRepository({_RUN_ID: run_record()}),
            approval=FakeApprovalRepository(
                [
                    approval_read(
                        status=status,
                        decision=decision,
                        resolved_at=_at(7) if resolved else None,
                    )
                ]
            ),
        )

        view = service.get_approval(_RUN_ID, _PROJECT_A)

        assert view is not None
        assert [field.name for field in fields(view)] == list(
            AGENT_APPROVAL_VIEW_FIELDS
        )
        assert view.status == status
        assert view.decision == decision
        assert (view.resolved_at is not None) is resolved
        for forbidden in ("arguments", "prompt", "context", "claim_token"):
            assert not hasattr(view, forbidden)

    def test_approval_returns_last_for_run(self) -> None:
        service, _repositories = build_service(
            run=FakeRunRepository({_RUN_ID: run_record()}),
            approval=FakeApprovalRepository(
                [
                    approval_read(step_index=0),
                    approval_read(
                        step_index=1,
                        status=APPROVAL_STATUS_APPROVED,
                        decision=APPROVAL_STATUS_APPROVED,
                        resolved_at=_at(8),
                    ),
                ]
            ),
        )
        view = service.get_approval(_RUN_ID, _PROJECT_A)
        assert view is not None
        assert view.step_index == 1

    def test_decision_view_never_exposes_arguments_or_content(self) -> None:
        service, _repositories = _full_service()

        view = service.get_run(_RUN_ID, _PROJECT_A)

        assert len(view.decisions) == 1
        decision = view.decisions[0]
        assert [field.name for field in fields(decision)] == list(
            AGENT_DECISION_VIEW_FIELDS
        )
        serialized = repr(decision)
        assert "should-not-leak" not in serialized
        assert "SELECT" not in serialized
        for forbidden in ("arguments", "content", "prompt"):
            assert not hasattr(decision, forbidden)

    def test_tool_result_view_never_exposes_payload(self) -> None:
        service, _repositories = _full_service()

        view = service.get_run(_RUN_ID, _PROJECT_A)

        assert len(view.tool_results) == 1
        item = view.tool_results[0]
        assert [field.name for field in fields(item)] == list(
            AGENT_TOOL_RESULT_VIEW_FIELDS
        )
        for forbidden in ("result_payload", "result_ref", "data", "payload"):
            assert not hasattr(item, forbidden)
        assert item.result_hash == "a" * 64

    def test_step_view_fields(self) -> None:
        service, _repositories = _full_service()
        view = service.get_run(_RUN_ID, _PROJECT_A)
        assert [field.name for field in fields(view.steps[0])] == list(
            AGENT_STEP_VIEW_FIELDS
        )
        assert view.steps[0].duration_ms == 120.5

    def test_view_dataclasses_are_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        service, _repositories = _full_service()
        view = service.get_run(_RUN_ID, _PROJECT_A)
        with pytest.raises(FrozenInstanceError):
            view.run.status = "FAILED"  # type: ignore[union-attr,misc]
        with pytest.raises(FrozenInstanceError):
            view.checkpoint.status = "ACTIVE"  # type: ignore[union-attr,misc]


# ============================================================
# §二十一 / §二十二：project 隔离
# ============================================================


class TestAgentReadProjectIsolation:
    def _matrix(self) -> AgentReadService:
        service, _repositories = build_service(
            run=FakeRunRepository(
                {
                    "run-A": run_record(run_id="run-A", project_id=_PROJECT_A),
                    "run-B": run_record(run_id="run-B", project_id=_PROJECT_B),
                }
            ),
            step=FakeStepRepository(
                [
                    AgentStepRead(
                        run_id="run-A",
                        step_index=0,
                        step_type="FINAL",
                        status="OK",
                        tool_name=None,
                        error_code=None,
                        elapsed_ms=1.0,
                        created_at=_at(3),
                    )
                ]
            ),
        )
        return service

    def test_project_matrix(self) -> None:
        service = self._matrix()

        assert service.get_run("run-A", _PROJECT_A).run is not None
        assert service.get_run("run-B", _PROJECT_B).run is not None
        with pytest.raises(AgentRunNotFoundError):
            service.get_run("run-A", _PROJECT_B)
        with pytest.raises(AgentRunNotFoundError):
            service.get_run("run-B", _PROJECT_A)

    def test_cross_project_looks_identical_to_missing(self) -> None:
        """§二十三：不存在与跨 project **同码**（不泄露存在性）。"""
        service = self._matrix()
        with pytest.raises(AgentRunNotFoundError) as missing:
            service.get_run("run-unknown", _PROJECT_A)
        with pytest.raises(AgentRunNotFoundError) as foreign:
            service.get_run("run-A", _PROJECT_B)
        assert str(missing.value) == str(foreign.value)
        # Timeline 同样只读边界
        with pytest.raises(AgentRunNotFoundError):
            service.get_timeline("run-A", _PROJECT_B)

    def test_cross_project_does_not_read_other_sources(self) -> None:
        """跨 project 探测只读 Run + Tool Result（其余 4 源零查询）。"""
        service, repositories = _full_service()

        with pytest.raises(AgentRunNotFoundError):
            service.get_run(_RUN_ID, _PROJECT_B)

        assert repositories["step"].calls == []
        assert repositories["decision"].calls == []
        assert repositories["checkpoint"].calls == []
        assert repositories["approval"].calls == []

    def test_unprovable_ownership_fails_closed(self) -> None:
        """无 AgentRun 行且无 Tool Result ⇒ 无法证明归属 ⇒ 不可见。"""
        service, _repositories = build_service(
            decision=FakeDecisionRepository([decision_record()]),
            approval=FakeApprovalRepository([approval_read()]),
        )
        with pytest.raises(AgentRunNotFoundError):
            service.get_run(_RUN_ID, _PROJECT_A)

    def test_null_project_run_can_be_owned_by_tool_result(self) -> None:
        service, _repositories = build_service(
            run=FakeRunRepository({_RUN_ID: run_record(project_id=None)}),
            tool_result=FakeToolResultRepository([tool_result_read()]),
        )
        view = service.get_run(_RUN_ID, _PROJECT_A)
        assert view.run is not None and view.run.project_id is None
        with pytest.raises(AgentRunNotFoundError):
            service.get_run(_RUN_ID, _PROJECT_B)

    def test_conflicting_tool_result_projects_fail_closed(self) -> None:
        other = AgentToolResultRead(
            run_id=_RUN_ID,
            step_index=1,
            tool_name="get_inventory",
            project_id=_PROJECT_B,
            success=True,
            error_code=None,
            result_hash=None,
            result_size_bytes=1,
            result_truncated=False,
            result_unavailable=False,
            tool_call_id=None,
            created_at=_at(6),
        )
        service, _repositories = build_service(
            tool_result=FakeToolResultRepository([tool_result_read(), other])
        )
        with pytest.raises(AgentRunNotFoundError):
            service.get_run(_RUN_ID, _PROJECT_A)


# ============================================================
# §二十 / §二十九：分页
# ============================================================


class TestAgentReadPagination:
    def _service_with_events(self) -> AgentReadService:
        service, _repositories = build_service(
            run=FakeRunRepository({_RUN_ID: run_record()}),
            step=FakeStepRepository(
                [step_read(step_index=index) for index in range(3)]
            ),
            tool_result=FakeToolResultRepository(
                [tool_result_read(step_index=index) for index in range(2)]
            ),
        )
        return service

    def test_default_limit_is_applied(self) -> None:
        service = self._service_with_events()
        events = service.get_timeline(_RUN_ID, _PROJECT_A)
        assert len(events) <= AGENT_READ_DEFAULT_LIMIT
        assert [event.sequence for event in events] == list(
            range(len(events))
        )

    #: fixture 事件总数 = RUN_STARTED + RUN_COMPLETED + 3 STEP + 2 TOOL = 7
    _FIXTURE_EVENT_COUNT = 7

    @pytest.mark.parametrize("limit", [1, 2, 10, 100])
    def test_valid_limits(self, limit: int) -> None:
        service = self._service_with_events()
        events = service.get_timeline(_RUN_ID, _PROJECT_A, limit=limit)
        assert len(events) == min(limit, self._FIXTURE_EVENT_COUNT)

    @pytest.mark.parametrize("limit", [0, -1, AGENT_READ_MAX_LIMIT + 1, True])
    def test_invalid_limits_rejected(self, limit: Any) -> None:
        service = self._service_with_events()
        with pytest.raises(ValueError):
            service.get_timeline(_RUN_ID, _PROJECT_A, limit=limit)

    @pytest.mark.parametrize("offset", [-1, True])
    def test_invalid_offsets_rejected(self, offset: Any) -> None:
        service = self._service_with_events()
        with pytest.raises(ValueError):
            service.get_timeline(_RUN_ID, _PROJECT_A, offset=offset)

    def test_pagination_slices_keep_sequence(self) -> None:
        service = self._service_with_events()
        full = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        page_one = service.get_timeline(_RUN_ID, _PROJECT_A, limit=3, offset=0)
        page_two = service.get_timeline(_RUN_ID, _PROJECT_A, limit=3, offset=3)
        page_three = service.get_timeline(_RUN_ID, _PROJECT_A, limit=3, offset=6)

        assert page_one + page_two + page_three == full  # 分页可无损拼接
        assert [event.sequence for event in page_one] == [0, 1, 2]
        assert [event.sequence for event in page_two] == [3, 4, 5]
        assert [event.sequence for event in page_three] == [6]

    def test_offset_beyond_end_is_empty(self) -> None:
        service = self._service_with_events()
        assert service.get_timeline(_RUN_ID, _PROJECT_A, offset=99) == ()


# ============================================================
# §六 / §十八 / §二十五：安全与只读
# ============================================================


class TestAgentReadSecurity:
    def test_forbidden_field_names_absent_everywhere(self) -> None:
        service, _repositories = _full_service()
        view = service.get_run(_RUN_ID, _PROJECT_A)

        forbidden = {
            "claim_token",
            "arguments",
            "content",
            "result_payload",
            "result_ref",
            "payload",
            "prompt",
            "system_prompt",
            "messages",
            "context",
            "sql",
            "password",
            "authorization",
            "api_key",
            "raw_response",
            "exception",
            "stack",
        }
        # 结构检查：递归收集全部 dataclass 字段名（不含子串误判）
        names: set[str] = set()
        for view_object in _iter_dataclass_instances(view):
            names.update(
                field.name for field in fields(view_object)
            )
        assert not (names & forbidden), sorted(names & forbidden)

        # 文本检查：仓储里的**哨兵泄漏串**一个都不能出现
        serialized = repr(view)
        for sentinel in (
            "SECRET",
            "should-not-leak",
            "SELECT * FROM secret_users",
            "claim-SECRET",
        ):
            assert sentinel not in serialized, sentinel
        # pending_tool_result_ref 是**控制引用**（不是 Tool Result 正文）
        assert view.checkpoint is not None
        assert view.checkpoint.pending_tool_result_ref == f"{_RUN_ID}:0"

    def test_service_source_has_no_write_or_command_calls(self) -> None:
        """§六 / §二十七：服务源码不含写方法 / Command 入口调用。"""
        source = inspect.getsource(
            __import__(
                "backend.app.services.agent_read_service",
                fromlist=["AgentReadService"],
            )
        )
        tree = ast.parse(source)
        called_attrs = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        for forbidden in (
            "create",
            "create_many",
            "update",
            "delete",
            "delete_for_test",
            "request_cancel",
            "resolve",
            "record",
            "arecord",
            "execute",
            "resume",
            "claim_for_resume",
        ):
            assert forbidden not in called_attrs, sorted(called_attrs)

    def test_service_module_imports_no_runtime_or_tool_layer(self) -> None:
        source = inspect.getsource(
            __import__(
                "backend.app.services.agent_read_service",
                fromlist=["AgentReadService"],
            )
        )
        tree = ast.parse(source)
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        for forbidden in ("backend.app.tools", "backend.app.llm"):
            assert not any(module.startswith(forbidden) for module in modules)
        assert "AgentRuntime" not in source

    def test_api_module_does_not_import_db_session(self) -> None:
        """§二十四：API 层不接触 Engine / Session（只经 Service）。"""
        path = (
            Path(__file__).resolve().parents[1]
            / "backend"
            / "app"
            / "api"
            / "agent_read.py"
        )
        source = path.read_text(encoding="utf-8")
        for forbidden in (
            "get_session_factory",
            "sessionmaker",
            "Session(",
            "Engine(",
        ):
            assert forbidden not in source, forbidden

    def test_fakes_received_only_read_calls(self) -> None:
        service, repositories = _full_service()
        service.get_run(_RUN_ID, _PROJECT_A)
        service.get_timeline(_RUN_ID, _PROJECT_A, limit=10)
        for name, repository in repositories.items():
            assert repository.calls, name
            for method, _args in repository.calls:
                assert method in {"get", "list_for_run"}, (name, method)
