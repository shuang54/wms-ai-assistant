"""Phase 5 P-8 Agent Timeline Read Model —— **离线**（内存 Fake 仓储；零 DB）。

覆盖（任务书 §十三 – §十八 / §二十七 / §三十三）：

```text
1  事件来源      Run / Step / Decision / Approval / ToolResult ⇒ 事件；无源 ⇒ 无事件
2  事件类型      白名单 11 种；**不伪造**（STEP_STARTED 当前不产生）
3  终态映射      COMPLETED / FAILED / CANCELLED / LIMIT_REACHED（CANCELLED ≠ FAILED）
4  排序          occurred_at + 稳定 tie-breaker ⇒ deterministic（与输入顺序无关）
5  sequence      投影下标 0..n-1；分页不重新编号
6  安全          无 payload / arguments / content / claim_token / prompt / SQL
7  只读模型      无 agent_timeline 表 / 无 event sourcing（AST 级审计）
```

复用 ``tests/test_agent_read_service.py`` 的 Fake 仓储与行构造器。
"""
from __future__ import annotations

import ast
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.app.agent.approval import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
)
from backend.app.agent.recorder import AgentRunRecord
from backend.app.dto.agent_read import (
    AGENT_TIMELINE_EVENT_FIELDS,
    AGENT_TIMELINE_EVENT_TYPES,
    TIMELINE_EVENT_RANK,
    AgentTimelineEvent,
    AgentTimelineEventType,
)
from backend.app.services.agent_read_service import AgentReadService
from tests.test_agent_read_service import (
    FakeApprovalRepository,
    FakeCheckpointRepository,
    FakeDecisionRepository,
    FakeRunRepository,
    FakeStepRepository,
    FakeToolResultRepository,
    approval_read,
    build_service,
    checkpoint_dto,
    decision_record,
    run_record,
    step_read,
    tool_result_read,
)

__all__ = [
    "TestAgentTimelineEvents",
    "TestAgentTimelineOrdering",
    "TestAgentTimelineSecurity",
]

_PROJECT_A = "project-A"
_RUN_ID = "run-p8-read"
#: 与 ``tests.test_agent_read_service`` 的行构造器基准时刻一致（2026-10-08T03:00:00Z）。
_BASE = datetime(2026, 10, 8, 3, 0, 0, tzinfo=timezone.utc)


def _at(seconds: int) -> datetime:
    return _BASE + timedelta(seconds=seconds)


def _service(**overrides: Any) -> AgentReadService:
    """默认：完整 Run（Run + Decision + Approval(已裁决) + Tool + Step + Checkpoint）。"""
    payload: dict[str, Any] = {
        "run": FakeRunRepository({_RUN_ID: run_record()}),
        "step": FakeStepRepository([step_read(step_index=0)]),
        "decision": FakeDecisionRepository([decision_record(step_index=0)]),
        "tool_result": FakeToolResultRepository([tool_result_read(step_index=0)]),
        "checkpoint": FakeCheckpointRepository({_RUN_ID: checkpoint_dto()}),
        "approval": FakeApprovalRepository(
            [
                approval_read(
                    step_index=0,
                    status=APPROVAL_STATUS_APPROVED,
                    decision=APPROVAL_STATUS_APPROVED,
                    resolved_at=_at(7),
                )
            ]
        ),
    }
    payload.update(overrides)
    return build_service(**payload)[0]


def _types(events: tuple[AgentTimelineEvent, ...]) -> list[str]:
    return [event.event_type for event in events]


# ============================================================
# §十四 / §十五：事件来源与类型
# ============================================================


class TestAgentTimelineEvents:
    def test_full_run_event_sequence(self) -> None:
        events = _service().get_timeline(_RUN_ID, _PROJECT_A, limit=100)

        assert _types(events) == [
            "RUN_STARTED",           # run.started_at         = 0s
            "DECISION_RECORDED",     # decision.created_at    = 2s
            "APPROVAL_REQUIRED",     # approval.created_at    = 3s
            "STEP_COMPLETED",        # step.created_at        = 4s
            "TOOL_EXECUTED",         # tool_result.created_at = 5s
            "APPROVAL_RESOLVED",     # approval.resolved_at   = 7s
            "RUN_COMPLETED",         # run.finished_at        = 9s
        ]
        assert [event.sequence for event in events] == list(range(len(events)))
        # 全序：occurred_at 非递减
        instants = [event.occurred_at for event in events]
        assert instants == sorted(instants)

    def test_event_fields_are_exactly_documented(self) -> None:
        events = _service().get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        for event in events:
            assert [field.name for field in fields(event)] == list(
                AGENT_TIMELINE_EVENT_FIELDS
            )
            assert event.event_type in AGENT_TIMELINE_EVENT_TYPES
            assert event.run_id == _RUN_ID

    def test_no_step_started_event_is_fabricated(self) -> None:
        """§十六：``agent_step`` 没有可证明的开始时刻 ⇒ **不**产生 STEP_STARTED。"""
        events = _service().get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        assert AgentTimelineEventType.STEP_STARTED.value not in _types(events)
        assert AgentTimelineEventType.STEP_STARTED.value in AGENT_TIMELINE_EVENT_TYPES

    def test_run_without_terminal_record_has_no_run_events(self) -> None:
        """崩溃窗口：无 AgentRun 行 ⇒ 无 RUN_STARTED / 终态事件（不伪造）。"""
        service = _service(
            run=FakeRunRepository(),
            step=FakeStepRepository([]),
            decision=FakeDecisionRepository([]),
            approval=FakeApprovalRepository([]),
            checkpoint=FakeCheckpointRepository({}),
        )

        events = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)

        assert _types(events) == ["TOOL_EXECUTED"]
        assert events[0].run_id == _RUN_ID  # 身份来自请求（不是猜的）

    def test_run_with_only_run_row_and_ownership_proof(self) -> None:
        service = _service(
            step=FakeStepRepository([]),
            decision=FakeDecisionRepository([]),
            approval=FakeApprovalRepository([]),
            tool_result=FakeToolResultRepository(
                [tool_result_read()]  # 归属证明
            ),
        )
        events = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        assert _types(events) == ["RUN_STARTED", "TOOL_EXECUTED", "RUN_COMPLETED"]

    @pytest.mark.parametrize(
        "status,expected",
        [
            ("COMPLETED", "RUN_COMPLETED"),
            ("FAILED", "RUN_FAILED"),
            ("CANCELLED", "RUN_CANCELLED"),
            ("LIMIT_REACHED", "RUN_LIMIT_REACHED"),
        ],
    )
    def test_terminal_status_mapping(self, status: str, expected: str) -> None:
        service = _service(
            run=FakeRunRepository(
                {_RUN_ID: run_record(status=status, error_code="AGENT_ERROR_X")}
            )
        )
        events = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        terminal = events[-1]
        assert terminal.event_type == expected
        assert terminal.status == status  # 逐字保留（CANCELLED **不**降级）
        assert terminal.error_code == "AGENT_ERROR_X"

    def test_unknown_status_has_no_terminal_event(self) -> None:
        service = _service(
            run=FakeRunRepository({_RUN_ID: run_record(status="WEIRD")})
        )
        events = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        assert _types(events)[-1] != "RUN_COMPLETED"
        assert all(
            event.event_type
            not in {
                "RUN_COMPLETED",
                "RUN_FAILED",
                "RUN_CANCELLED",
                "RUN_LIMIT_REACHED",
            }
            for event in events
        )

    def test_pending_approval_has_required_but_no_resolved(self) -> None:
        service = _service(
            approval=FakeApprovalRepository(
                [approval_read(step_index=0, status=APPROVAL_STATUS_PENDING)]
            )
        )
        events = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        types = _types(events)
        assert "APPROVAL_REQUIRED" in types
        assert "APPROVAL_RESOLVED" not in types
        required = events[types.index("APPROVAL_REQUIRED")]
        assert required.status == APPROVAL_STATUS_PENDING

    def test_tool_failure_is_expressed_as_status_and_error_code(self) -> None:
        service = _service(
            tool_result=FakeToolResultRepository(
                [tool_result_read(step_index=0, success=False)]
            )
        )
        events = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        tool_event = next(
            event for event in events if event.event_type == "TOOL_EXECUTED"
        )
        assert tool_event.status == "FAILED"
        assert tool_event.error_code == "TOOL_EXECUTION_FAILURE"

    def test_step_event_carries_duration_and_status(self) -> None:
        events = _service().get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        step_event = next(
            event for event in events if event.event_type == "STEP_COMPLETED"
        )
        assert step_event.step_index == 0
        assert step_event.status == "OK"
        assert step_event.duration_ms == 120.5

    def test_events_carry_source_timestamps_exactly(self) -> None:
        """``occurred_at`` 必须**逐字**来自源行（不发明时间）。"""
        record = run_record()
        service = _service(run=FakeRunRepository({_RUN_ID: record}))
        events = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        started = next(
            event for event in events if event.event_type == "RUN_STARTED"
        )
        terminal = events[-1]
        assert started.occurred_at == record.started_at
        assert terminal.occurred_at == record.finished_at

    def test_event_count_equals_persisted_facts(self) -> None:
        """不产生"额外事件"：数量 == 可证明事实数。"""
        service = _service(
            run=FakeRunRepository({_RUN_ID: run_record()}),
            step=FakeStepRepository([step_read(step_index=0)]),
            decision=FakeDecisionRepository(
                [decision_record(step_index=0), decision_record(step_index=1)]
            ),
            tool_result=FakeToolResultRepository([tool_result_read(step_index=0)]),
            approval=FakeApprovalRepository([]),
            checkpoint=FakeCheckpointRepository({}),
        )
        events = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        # RUN(2) + DECISION(2) + TOOL(1) + STEP(1) = 6
        assert len(events) == 6


# ============================================================
# §十七：排序确定性
# ============================================================


class TestAgentTimelineOrdering:
    def _records(self) -> tuple[Any, ...]:
        return (
            run_record(),
            step_read(step_index=0),
            decision_record(step_index=0),
            tool_result_read(step_index=0),
            approval_read(step_index=0),
            checkpoint_dto(),
        )

    def test_build_is_deterministic(self) -> None:
        first = _service().get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        second = _service().get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        assert first == second

    def test_order_independent_of_repository_return_order(self) -> None:
        forward = _service(
            step=FakeStepRepository([step_read(step_index=index) for index in range(3)]),
            decision=FakeDecisionRepository(
                [decision_record(step_index=index) for index in range(2)]
            ),
            tool_result=FakeToolResultRepository(
                [tool_result_read(step_index=index) for index in range(2)]
            ),
        ).get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        reversed_rows = _service(
            step=FakeStepRepository(
                [step_read(step_index=index) for index in reversed(range(3))]
            ),
            decision=FakeDecisionRepository(
                [decision_record(step_index=index) for index in reversed(range(2))]
            ),
            tool_result=FakeToolResultRepository(
                [tool_result_read(step_index=index) for index in reversed(range(2))]
            ),
        ).get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        assert forward == reversed_rows

    def test_tie_breaker_orders_causal_chain(self) -> None:
        """相同 ``occurred_at``：DECISION → APPROVAL → TOOL → STEP（稳定顺序）。"""
        same = run_record().started_at
        service = _service(
            run=FakeRunRepository({_RUN_ID: run_record()}),
            step=FakeStepRepository([step_read(step_index=0, created_at=same)]),
            decision=FakeDecisionRepository(
                [decision_record(step_index=0, created_at=same)]
            ),
            tool_result=FakeToolResultRepository(
                [tool_result_read(step_index=0, created_at=same)]
            ),
            approval=FakeApprovalRepository([]),
            checkpoint=FakeCheckpointRepository({}),
        )

        events = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)

        assert _types(events)[:4] == [
            "RUN_STARTED",
            "DECISION_RECORDED",
            "TOOL_EXECUTED",
            "STEP_COMPLETED",
        ]
        ranks = [TIMELINE_EVENT_RANK[event.event_type] for event in events]
        assert ranks == sorted(ranks)

    def test_sequence_matches_full_order_after_pagination(self) -> None:
        service = _service(
            step=FakeStepRepository([step_read(step_index=index) for index in range(3)])
        )
        full = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        tail = service.get_timeline(_RUN_ID, _PROJECT_A, limit=2, offset=2)
        assert [event.sequence for event in tail] == [2, 3]
        assert tail == full[2:4]

    def test_same_second_events_have_unique_total_order(self) -> None:
        """全序：同一秒内也不会依赖集合 / 字典迭代顺序。"""
        service = _service(
            decision=FakeDecisionRepository(
                [decision_record(step_index=index) for index in range(3)]
            ),
            step=FakeStepRepository(
                [step_read(step_index=index) for index in range(3)]
            ),
        )
        first = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        second = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        assert [(event.sequence, event.step_index) for event in first] == [
            (event.sequence, event.step_index) for event in second
        ]


# ============================================================
# §十八 / §三十三：安全与"非事件系统"
# ============================================================


class TestAgentTimelineSecurity:
    def test_no_sensitive_payload_in_events(self) -> None:
        service = _service()
        events = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        serialized = repr(events)
        for sentinel in (
            "SECRET",
            "should-not-leak",
            "SELECT * FROM secret_users",
            "claim-SECRET",
        ):
            assert sentinel not in serialized

        forbidden = {
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
            "claim_token",
        }
        names = {field.name for field in fields(AgentTimelineEvent)}
        assert not (names & forbidden), sorted(names & forbidden)

    def test_no_timeline_table_or_event_store(self) -> None:
        """§三十三：Timeline 是 Read Model —— **无** timeline 表 / 无 event sourcing。"""
        repository_root = Path(__file__).resolve().parents[1] / "backend" / "app"
        models_dir = repository_root / "db" / "models"
        timelines = []
        for path in sorted(models_dir.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id.endswith("_TABLE")
                            and isinstance(node.value, ast.Constant)
                            and isinstance(node.value.value, str)
                            and "timeline" in node.value.value
                        ):
                            timelines.append((path.name, node.value.value))
        assert timelines == []

        for relative in (
            ("services", "agent_read_service.py"),
            ("dto", "agent_read.py"),
            ("api", "agent_read.py"),
        ):
            source = (
                repository_root / relative[0] / relative[1]
            ).read_text(encoding="utf-8")
            for forbidden in ("event_store", "event_sourcing", "EventStore"):
                assert forbidden not in source, relative

    def test_timeline_does_not_mutate_anything(self) -> None:
        """§二十七：读取 Timeline **不**触发任何写调用（Fake 只收到只读方法）。"""
        service, repositories = build_service(
            run=FakeRunRepository({_RUN_ID: run_record()}),
            tool_result=FakeToolResultRepository([tool_result_read()]),
        )
        service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        for name, repository in repositories.items():
            for method, _args in repository.calls:
                assert method in {"get", "list_for_run"}, (name, method)

    def test_read_model_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        events = _service().get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        with pytest.raises(FrozenInstanceError):
            events[0].status = "FAILED"  # type: ignore[misc]

    def test_unknown_terminal_status_produces_no_terminal_event(self) -> None:
        """未知终态 ⇒ **不**补造终态事件（只返回可证明的事实）。"""
        service = _service(
            run=FakeRunRepository({_RUN_ID: run_record(status="WEIRD")}),
            step=FakeStepRepository([]),
            decision=FakeDecisionRepository([]),
            approval=FakeApprovalRepository([]),
            checkpoint=FakeCheckpointRepository({}),
        )
        events = service.get_timeline(_RUN_ID, _PROJECT_A, limit=100)
        assert _types(events) == ["RUN_STARTED", "TOOL_EXECUTED"]
