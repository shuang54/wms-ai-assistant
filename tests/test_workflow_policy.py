"""Phase 6 Step 1 —— Workflow 策略测试（**全离线**）。

覆盖（ADR-01 §12 / P6 Explicit Budget）：
```text
Test 1  NodeRetryPolicy：默认 / 合法边界（1..硬上限）/ 非法（0 / 负 / bool / float / None / 超上限）
Test 2  WorkflowRuntimePolicy：默认有限 / 合法覆盖 / 非法（0 / 负 / bool / float / None）
Test 3  frozen 不可变（装配后不可修改）
Test 4  异常类型（WorkflowPolicyError ∈ WorkflowError + ValueError）
```

边界：零 DB / 零 LLM / 零网络。
"""
from __future__ import annotations

import dataclasses

import pytest

from backend.app.workflow.errors import WorkflowError, WorkflowPolicyError
from backend.app.workflow.policy import (
    NODE_RETRY_MAX_ATTEMPTS_HARD_LIMIT,
    WORKFLOW_DEFAULT_MAX_NODES_EXECUTED,
    WORKFLOW_DEFAULT_MAX_TOTAL_ATTEMPTS,
    NodeRetryPolicy,
    WorkflowRuntimePolicy,
)

__all__ = [
    "TestNodeRetryPolicy",
    "TestWorkflowRuntimePolicy",
    "TestFrozenAndErrors",
]


class TestNodeRetryPolicy:
    """Test 1：节点重试策略（immediate only；硬上限强制）。"""

    def test_default_is_no_retry(self) -> None:
        policy = NodeRetryPolicy()
        assert policy.max_attempts == 1  # 1 = 不重试

    @pytest.mark.parametrize("attempts", [1, 2, 3, 4, 5])
    def test_valid_attempts(self, attempts: int) -> None:
        policy = NodeRetryPolicy(max_attempts=attempts)
        assert policy.max_attempts == attempts

    def test_hard_limit_value_is_frozen_constant(self) -> None:
        assert NODE_RETRY_MAX_ATTEMPTS_HARD_LIMIT == 5

    @pytest.mark.parametrize(
        "bad",
        [0, -1, True, False, 1.0, "1", None, 6, 100],
    )
    def test_invalid_attempts_rejected(self, bad: object) -> None:
        with pytest.raises(WorkflowPolicyError):
            NodeRetryPolicy(max_attempts=bad)  # type: ignore[arg-type]

    def test_beyond_hard_limit_rejected(self) -> None:
        with pytest.raises(WorkflowPolicyError):
            NodeRetryPolicy(
                max_attempts=NODE_RETRY_MAX_ATTEMPTS_HARD_LIMIT + 1
            )


class TestWorkflowRuntimePolicy:
    """Test 2：run 级预算（显式有限）。"""

    def test_defaults_are_finite(self) -> None:
        policy = WorkflowRuntimePolicy()
        assert policy.max_nodes_executed == WORKFLOW_DEFAULT_MAX_NODES_EXECUTED
        assert policy.max_total_attempts == WORKFLOW_DEFAULT_MAX_TOTAL_ATTEMPTS
        assert policy.max_nodes_executed > 0
        assert policy.max_total_attempts > 0

    def test_explicit_override(self) -> None:
        policy = WorkflowRuntimePolicy(
            max_nodes_executed=10, max_total_attempts=12
        )
        assert policy.max_nodes_executed == 10
        assert policy.max_total_attempts == 12

    @pytest.mark.parametrize("bad", [0, -5, True, 8.0, "8", None])
    def test_invalid_nodes_executed_rejected(self, bad: object) -> None:
        with pytest.raises(WorkflowPolicyError):
            WorkflowRuntimePolicy(max_nodes_executed=bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", [0, -5, True, 8.0, "8", None])
    def test_invalid_total_attempts_rejected(self, bad: object) -> None:
        with pytest.raises(WorkflowPolicyError):
            WorkflowRuntimePolicy(max_total_attempts=bad)  # type: ignore[arg-type]


class TestFrozenAndErrors:
    """Test 3 / 4：不可变 + 异常类型。"""

    def test_node_retry_policy_is_frozen(self) -> None:
        policy = NodeRetryPolicy(max_attempts=2)
        with pytest.raises(dataclasses.FrozenInstanceError):
            policy.max_attempts = 3  # type: ignore[misc]

    def test_runtime_policy_is_frozen(self) -> None:
        policy = WorkflowRuntimePolicy()
        with pytest.raises(dataclasses.FrozenInstanceError):
            policy.max_nodes_executed = 1  # type: ignore[misc]

    def test_policy_error_type(self) -> None:
        assert issubclass(WorkflowPolicyError, WorkflowError)
        assert issubclass(WorkflowPolicyError, ValueError)
