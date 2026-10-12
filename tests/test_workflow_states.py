"""Phase 6 Step 1 —— Workflow 状态域测试（**全离线**）。

覆盖（ADR-01 §3 状态机 / §6 control flow / 修正 1）：
```text
Test 1  run 状态白名单 / 终态集合
Test 2  node attempt 状态白名单 / 终态集合
Test 3  approval 状态白名单 / 终态集合
Test 4  run 转移全矩阵（6×6 —— 合法 / 非法双向断言）
Test 5  node 转移全矩阵（4×4）
Test 6  approval 转移全矩阵（3×3）
Test 7  终态不可逆（合法转移集合为空）
Test 8  未知状态 / 非 str ⇒ fail-closed（can_* = False；assert_* 抛错）
Test 9  CANCELLED ≠ FAILED；WAITING ≠ 终态（语义隔离）
Test 10 retry 不是状态转移（无 RETRY 状态；无 SKIPPED 状态 —— 修正 1）
```

边界：零 DB / 零 LLM / 零网络 / 零持久化。
"""
from __future__ import annotations

import pytest

from backend.app.workflow.errors import WorkflowError, WorkflowStateError
from backend.app.workflow.states import (
    APPROVAL_STATUSES,
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
    APPROVAL_TERMINAL_STATUSES,
    NODE_STATUSES,
    NODE_STATUS_FAILED,
    NODE_STATUS_RUNNING,
    NODE_STATUS_SUCCEEDED,
    NODE_STATUS_WAITING_APPROVAL,
    NODE_TERMINAL_STATUSES,
    WORKFLOW_STATUSES,
    WORKFLOW_STATUS_CANCELLED,
    WORKFLOW_STATUS_COMPLETED,
    WORKFLOW_STATUS_FAILED,
    WORKFLOW_STATUS_PENDING,
    WORKFLOW_STATUS_RUNNING,
    WORKFLOW_STATUS_WAITING,
    WORKFLOW_TERMINAL_STATUSES,
    assert_approval_transition,
    assert_node_transition,
    assert_workflow_transition,
    can_transition_approval,
    can_transition_node,
    can_transition_workflow,
    is_approval_status,
    is_node_status,
    is_terminal_node_status,
    is_terminal_workflow_status,
    is_workflow_status,
)

__all__ = [
    "TestStateConstants",
    "TestWorkflowTransitionMatrix",
    "TestNodeTransitionMatrix",
    "TestApprovalTransitionMatrix",
    "TestFailClosed",
    "TestSemanticIsolation",
]

#: 期望的 run 转移矩阵（与 states.py 白名单逐一对齐；测试独立重述 = 双源校验）。
_RUN_EXPECTED: dict[str, set[str]] = {
    WORKFLOW_STATUS_PENDING: {WORKFLOW_STATUS_RUNNING, WORKFLOW_STATUS_CANCELLED},
    WORKFLOW_STATUS_RUNNING: {
        WORKFLOW_STATUS_WAITING,
        WORKFLOW_STATUS_COMPLETED,
        WORKFLOW_STATUS_FAILED,
        WORKFLOW_STATUS_CANCELLED,
    },
    WORKFLOW_STATUS_WAITING: {
        WORKFLOW_STATUS_RUNNING,
        WORKFLOW_STATUS_FAILED,
        WORKFLOW_STATUS_CANCELLED,
    },
    WORKFLOW_STATUS_COMPLETED: set(),
    WORKFLOW_STATUS_FAILED: set(),
    WORKFLOW_STATUS_CANCELLED: set(),
}

_NODE_EXPECTED: dict[str, set[str]] = {
    NODE_STATUS_RUNNING: {
        NODE_STATUS_WAITING_APPROVAL,
        NODE_STATUS_SUCCEEDED,
        NODE_STATUS_FAILED,
    },
    NODE_STATUS_WAITING_APPROVAL: {
        NODE_STATUS_RUNNING,
        NODE_STATUS_SUCCEEDED,
        NODE_STATUS_FAILED,
    },
    NODE_STATUS_SUCCEEDED: set(),
    NODE_STATUS_FAILED: set(),
}

_APPROVAL_EXPECTED: dict[str, set[str]] = {
    APPROVAL_STATUS_PENDING: {APPROVAL_STATUS_APPROVED, APPROVAL_STATUS_REJECTED},
    APPROVAL_STATUS_APPROVED: set(),
    APPROVAL_STATUS_REJECTED: set(),
}


class TestStateConstants:
    """状态常量白名单 / 终态集合（Test 1–3）。"""

    def test_run_status_whitelist(self) -> None:
        assert WORKFLOW_STATUSES == {
            "PENDING",
            "RUNNING",
            "WAITING",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        }
        assert WORKFLOW_TERMINAL_STATUSES == {
            "COMPLETED",
            "FAILED",
            "CANCELLED",
        }
        # WAITING 不是终态（HITL 等待点）
        assert WORKFLOW_STATUS_WAITING not in WORKFLOW_TERMINAL_STATUSES

    def test_node_status_whitelist(self) -> None:
        assert NODE_STATUSES == {
            "RUNNING",
            "WAITING_APPROVAL",
            "SUCCEEDED",
            "FAILED",
        }
        assert NODE_TERMINAL_STATUSES == {"SUCCEEDED", "FAILED"}

    def test_approval_status_whitelist(self) -> None:
        assert APPROVAL_STATUSES == {"PENDING", "APPROVED", "REJECTED"}
        assert APPROVAL_TERMINAL_STATUSES == {"APPROVED", "REJECTED"}
        assert APPROVAL_STATUS_PENDING not in APPROVAL_TERMINAL_STATUSES

    def test_no_retry_or_skipped_state(self) -> None:
        """修正 1：无 RETRY / SKIPPED / RETRYING 状态（retry = 新 attempt）。"""
        forbidden = {"RETRY", "RETRYING", "SKIPPED", "BACKOFF", "LOOP"}
        assert not (WORKFLOW_STATUSES & forbidden)
        assert not (NODE_STATUSES & forbidden)


class TestWorkflowTransitionMatrix:
    """run 转移全矩阵（Test 4 / 7）。"""

    def test_full_matrix(self) -> None:
        for from_status in WORKFLOW_STATUSES:
            for to_status in WORKFLOW_STATUSES:
                expected = to_status in _RUN_EXPECTED[from_status]
                assert (
                    can_transition_workflow(from_status, to_status) is expected
                ), f"{from_status} → {to_status} 期望 {expected}"

    def test_terminal_statuses_have_no_outgoing(self) -> None:
        for terminal in WORKFLOW_TERMINAL_STATUSES:
            for to_status in WORKFLOW_STATUSES:
                assert not can_transition_workflow(terminal, to_status)

    def test_assert_transition_ok(self) -> None:
        assert_workflow_transition(
            WORKFLOW_STATUS_PENDING, WORKFLOW_STATUS_RUNNING
        )
        assert_workflow_transition(
            WORKFLOW_STATUS_RUNNING, WORKFLOW_STATUS_WAITING
        )
        assert_workflow_transition(
            WORKFLOW_STATUS_WAITING, WORKFLOW_STATUS_RUNNING
        )

    def test_assert_transition_invalid_raises(self) -> None:
        with pytest.raises(WorkflowStateError):
            assert_workflow_transition(
                WORKFLOW_STATUS_COMPLETED, WORKFLOW_STATUS_RUNNING
            )
        with pytest.raises(WorkflowStateError):
            assert_workflow_transition(
                WORKFLOW_STATUS_PENDING, WORKFLOW_STATUS_WAITING
            )


class TestNodeTransitionMatrix:
    """node attempt 转移全矩阵（Test 5 / 7）。"""

    def test_full_matrix(self) -> None:
        for from_status in NODE_STATUSES:
            for to_status in NODE_STATUSES:
                expected = to_status in _NODE_EXPECTED[from_status]
                assert (
                    can_transition_node(from_status, to_status) is expected
                ), f"{from_status} → {to_status} 期望 {expected}"

    def test_terminal_attempts_have_no_outgoing(self) -> None:
        for terminal in NODE_TERMINAL_STATUSES:
            for to_status in NODE_STATUSES:
                assert not can_transition_node(terminal, to_status)

    def test_retry_is_new_attempt_not_transition(self) -> None:
        """修正 1：FAILED attempt 不可回到 RUNNING（retry = 新 attempt 行）。"""
        assert not can_transition_node(NODE_STATUS_FAILED, NODE_STATUS_RUNNING)


class TestApprovalTransitionMatrix:
    """approval 转移全矩阵（Test 6 / 7）。"""

    def test_full_matrix(self) -> None:
        for from_status in APPROVAL_STATUSES:
            for to_status in APPROVAL_STATUSES:
                expected = to_status in _APPROVAL_EXPECTED[from_status]
                assert (
                    can_transition_approval(from_status, to_status) is expected
                ), f"{from_status} → {to_status} 期望 {expected}"

    def test_assert_invalid_raises(self) -> None:
        with pytest.raises(WorkflowStateError):
            assert_approval_transition(
                APPROVAL_STATUS_APPROVED, APPROVAL_STATUS_REJECTED
            )


class TestFailClosed:
    """未知状态 / 非 str ⇒ fail-closed（Test 8）。"""

    @pytest.mark.parametrize("bogus", ["BOGUS", "", "running", None, 1, True])
    def test_unknown_run_status(self, bogus: object) -> None:
        assert is_workflow_status(bogus) is False
        assert is_terminal_workflow_status(bogus) is False
        assert can_transition_workflow(bogus, WORKFLOW_STATUS_RUNNING) is False
        assert can_transition_workflow(WORKFLOW_STATUS_PENDING, bogus) is False

    @pytest.mark.parametrize("bogus", ["BOGUS", "", "OK", None, 0])
    def test_unknown_node_status(self, bogus: object) -> None:
        assert is_node_status(bogus) is False
        assert is_terminal_node_status(bogus) is False
        assert can_transition_node(bogus, NODE_STATUS_RUNNING) is False
        assert can_transition_node(NODE_STATUS_RUNNING, bogus) is False

    def test_unknown_approval_status(self) -> None:
        assert is_approval_status("BOGUS") is False
        assert is_approval_status(None) is False
        assert can_transition_approval("BOGUS", APPROVAL_STATUS_APPROVED) is False

    def test_assert_unknown_raises(self) -> None:
        with pytest.raises(WorkflowStateError):
            assert_workflow_transition("BOGUS", WORKFLOW_STATUS_RUNNING)
        with pytest.raises(WorkflowStateError):
            assert_node_transition(NODE_STATUS_RUNNING, "BOGUS")


class TestSemanticIsolation:
    """语义隔离（Test 9 / 10）。"""

    def test_cancelled_is_not_failed(self) -> None:
        assert WORKFLOW_STATUS_CANCELLED != WORKFLOW_STATUS_FAILED
        # CANCELLED / FAILED 互不转移（都是终态）
        assert not can_transition_workflow(
            WORKFLOW_STATUS_CANCELLED, WORKFLOW_STATUS_FAILED
        )
        assert not can_transition_workflow(
            WORKFLOW_STATUS_FAILED, WORKFLOW_STATUS_CANCELLED
        )

    def test_waiting_is_not_terminal(self) -> None:
        assert WORKFLOW_STATUS_WAITING not in WORKFLOW_TERMINAL_STATUSES
        assert can_transition_workflow(
            WORKFLOW_STATUS_WAITING, WORKFLOW_STATUS_RUNNING
        )

    def test_error_types(self) -> None:
        assert issubclass(WorkflowStateError, WorkflowError)
        assert issubclass(WorkflowStateError, ValueError)
