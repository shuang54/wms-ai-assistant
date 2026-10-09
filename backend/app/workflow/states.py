"""Workflow 状态域（Phase 6 ADR-01 §3 状态机 / §6 control flow）。

三组状态（全部为**稳定字符串常量**；不静默修正、不猜测）：

```text
WorkflowRun   : PENDING → RUNNING ⇄ WAITING → TERMINAL
                TERMINAL = {COMPLETED, FAILED, CANCELLED}
Node attempt  : RUNNING → WAITING_APPROVAL → … → TERMINAL
                TERMINAL = {SUCCEEDED, FAILED}
Approval      : PENDING → {APPROVED, REJECTED}（终态不可逆）
```

纪律（fail-closed）：
    * 未知状态 / 非法转移 ⇒ 拒绝（``WorkflowStateError``；no-op 修正一律不做）；
    * 终态不可逆（CANCELLED ≠ FAILED；WAITING ≠ 终态）；
    * **无 SKIPPED**：未选中的分支节点**不产生记录**（condition 节点自身
      记录选中分支 = 可证明事实）；
    * retry **不是**状态转移，也**不是** graph edge —— retry = 同一
      ``node_id`` 的新 attempt（新行；ADR 批准修正 1）；
    * 本模块零 IO / 零持久化（转移的 CAS 落实在 Step 2/3）。
"""
from __future__ import annotations

from typing import Final

from backend.app.workflow.errors import WorkflowStateError

__all__ = [
    # run 状态
    "WORKFLOW_STATUS_PENDING",
    "WORKFLOW_STATUS_RUNNING",
    "WORKFLOW_STATUS_WAITING",
    "WORKFLOW_STATUS_COMPLETED",
    "WORKFLOW_STATUS_FAILED",
    "WORKFLOW_STATUS_CANCELLED",
    "WORKFLOW_STATUSES",
    "WORKFLOW_TERMINAL_STATUSES",
    # node attempt 状态
    "NODE_STATUS_RUNNING",
    "NODE_STATUS_WAITING_APPROVAL",
    "NODE_STATUS_SUCCEEDED",
    "NODE_STATUS_FAILED",
    "NODE_STATUSES",
    "NODE_TERMINAL_STATUSES",
    # approval 状态
    "APPROVAL_STATUS_PENDING",
    "APPROVAL_STATUS_APPROVED",
    "APPROVAL_STATUS_REJECTED",
    "APPROVAL_STATUSES",
    "APPROVAL_TERMINAL_STATUSES",
    # 校验函数
    "is_workflow_status",
    "is_terminal_workflow_status",
    "is_node_status",
    "is_terminal_node_status",
    "is_approval_status",
    "can_transition_workflow",
    "can_transition_node",
    "can_transition_approval",
    "assert_workflow_transition",
    "assert_node_transition",
    "assert_approval_transition",
]


# ============================================================
# 1) WorkflowRun 状态
# ============================================================

#: 已创建、尚未开始执行（启动窗口；start 内部短暂存在）。
WORKFLOW_STATUS_PENDING: Final[str] = "PENDING"

#: 正在 inline 推进（执行中 / 恢复中）。
WORKFLOW_STATUS_RUNNING: Final[str] = "RUNNING"

#: 到达等待点（approval；ADR §9 wait-point）。
WORKFLOW_STATUS_WAITING: Final[str] = "WAITING"

#: 终态：全部节点成功结束。
WORKFLOW_STATUS_COMPLETED: Final[str] = "COMPLETED"

#: 终态：不可恢复失败（节点终态失败 / 审批拒绝 / 版本不符等）。
WORKFLOW_STATUS_FAILED: Final[str] = "FAILED"

#: 终态：协作式取消生效（**不映射**为 FAILED）。
WORKFLOW_STATUS_CANCELLED: Final[str] = "CANCELLED"

#: 全部合法 run 状态。
WORKFLOW_STATUSES: Final[frozenset[str]] = frozenset(
    {
        WORKFLOW_STATUS_PENDING,
        WORKFLOW_STATUS_RUNNING,
        WORKFLOW_STATUS_WAITING,
        WORKFLOW_STATUS_COMPLETED,
        WORKFLOW_STATUS_FAILED,
        WORKFLOW_STATUS_CANCELLED,
    }
)

#: 终态（不可逆；进入后禁止 resume / 禁止再转移）。
WORKFLOW_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {
        WORKFLOW_STATUS_COMPLETED,
        WORKFLOW_STATUS_FAILED,
        WORKFLOW_STATUS_CANCELLED,
    }
)


# ============================================================
# 2) Node attempt 状态（per-attempt 行；修正 1：retry = 新 attempt）
# ============================================================

#: 节点 attempt 已开始（"先记 RUNNING、后记完成"写入纪律 —— 见 ADR §8/§12.3）。
NODE_STATUS_RUNNING: Final[str] = "RUNNING"

#: approval attempt 等待人工裁决（run = WAITING；不是失败）。
NODE_STATUS_WAITING_APPROVAL: Final[str] = "WAITING_APPROVAL"

#: attempt 终态：成功（节点完成；其 output 为下游/恢复依据）。
NODE_STATUS_SUCCEEDED: Final[str] = "SUCCEEDED"

#: attempt 终态：失败（是否 retry 由 workflow retry policy 决定——不是本层）。
NODE_STATUS_FAILED: Final[str] = "FAILED"

#: 全部合法 node attempt 状态。
NODE_STATUSES: Final[frozenset[str]] = frozenset(
    {
        NODE_STATUS_RUNNING,
        NODE_STATUS_WAITING_APPROVAL,
        NODE_STATUS_SUCCEEDED,
        NODE_STATUS_FAILED,
    }
)

#: node attempt 终态（不可逆；retry 产生**新** attempt 行，不改写历史）。
NODE_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {
        NODE_STATUS_SUCCEEDED,
        NODE_STATUS_FAILED,
    }
)


# ============================================================
# 3) Approval 状态（修正 3：DTO 只定义状态；Runtime 是转换 authority）
# ============================================================

APPROVAL_STATUS_PENDING: Final[str] = "PENDING"
APPROVAL_STATUS_APPROVED: Final[str] = "APPROVED"
APPROVAL_STATUS_REJECTED: Final[str] = "REJECTED"

APPROVAL_STATUSES: Final[frozenset[str]] = frozenset(
    {
        APPROVAL_STATUS_PENDING,
        APPROVAL_STATUS_APPROVED,
        APPROVAL_STATUS_REJECTED,
    }
)

APPROVAL_TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {
        APPROVAL_STATUS_APPROVED,
        APPROVAL_STATUS_REJECTED,
    }
)


# ============================================================
# 4) 转移表（white-list；未知来源不转移）
# ============================================================

_RUN_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    WORKFLOW_STATUS_PENDING: frozenset(
        {WORKFLOW_STATUS_RUNNING, WORKFLOW_STATUS_CANCELLED}
    ),
    WORKFLOW_STATUS_RUNNING: frozenset(
        {
            WORKFLOW_STATUS_WAITING,
            WORKFLOW_STATUS_COMPLETED,
            WORKFLOW_STATUS_FAILED,
            WORKFLOW_STATUS_CANCELLED,
        }
    ),
    # WAITING → RUNNING（resume）/ FAILED（审批拒绝等）/ CANCELLED（协作式取消）
    WORKFLOW_STATUS_WAITING: frozenset(
        {
            WORKFLOW_STATUS_RUNNING,
            WORKFLOW_STATUS_FAILED,
            WORKFLOW_STATUS_CANCELLED,
        }
    ),
    # 终态：无出口
    WORKFLOW_STATUS_COMPLETED: frozenset(),
    WORKFLOW_STATUS_FAILED: frozenset(),
    WORKFLOW_STATUS_CANCELLED: frozenset(),
}

_NODE_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    NODE_STATUS_RUNNING: frozenset(
        {
            NODE_STATUS_WAITING_APPROVAL,
            NODE_STATUS_SUCCEEDED,
            NODE_STATUS_FAILED,
        }
    ),
    # 裁决后 resume：继续（RUNNING）或终止（SUCCEEDED / FAILED）
    NODE_STATUS_WAITING_APPROVAL: frozenset(
        {
            NODE_STATUS_RUNNING,
            NODE_STATUS_SUCCEEDED,
            NODE_STATUS_FAILED,
        }
    ),
    NODE_STATUS_SUCCEEDED: frozenset(),
    NODE_STATUS_FAILED: frozenset(),
}

_APPROVAL_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    APPROVAL_STATUS_PENDING: frozenset(
        {APPROVAL_STATUS_APPROVED, APPROVAL_STATUS_REJECTED}
    ),
    APPROVAL_STATUS_APPROVED: frozenset(),
    APPROVAL_STATUS_REJECTED: frozenset(),
}


# ============================================================
# 5) 校验函数（纯函数；未知 ⇒ fail-closed）
# ============================================================


def is_workflow_status(status: object) -> bool:
    """``status`` 是否属于 ``WORKFLOW_STATUSES``（非 str / 未知 ⇒ False）。"""
    return isinstance(status, str) and status in WORKFLOW_STATUSES


def is_terminal_workflow_status(status: object) -> bool:
    """``status`` 是否为 run 终态（未知 ⇒ False）。"""
    return isinstance(status, str) and status in WORKFLOW_TERMINAL_STATUSES


def is_node_status(status: object) -> bool:
    """``status`` 是否属于 ``NODE_STATUSES``。"""
    return isinstance(status, str) and status in NODE_STATUSES


def is_terminal_node_status(status: object) -> bool:
    """``status`` 是否为 node attempt 终态。"""
    return isinstance(status, str) and status in NODE_TERMINAL_STATUSES


def is_approval_status(status: object) -> bool:
    """``status`` 是否属于 ``APPROVAL_STATUSES``。"""
    return isinstance(status, str) and status in APPROVAL_STATUSES


def can_transition_workflow(from_status: object, to_status: object) -> bool:
    """run 状态转移是否合法（未知来源 / 未知目标 ⇒ False；fail-closed）。"""
    if not isinstance(from_status, str) or not isinstance(to_status, str):
        return False
    allowed = _RUN_TRANSITIONS.get(from_status)
    if allowed is None:
        return False
    return to_status in allowed


def can_transition_node(from_status: object, to_status: object) -> bool:
    """node attempt 状态转移是否合法（未知 ⇒ False）。"""
    if not isinstance(from_status, str) or not isinstance(to_status, str):
        return False
    allowed = _NODE_TRANSITIONS.get(from_status)
    if allowed is None:
        return False
    return to_status in allowed


def can_transition_approval(from_status: object, to_status: object) -> bool:
    """approval 状态转移是否合法（未知 ⇒ False）。"""
    if not isinstance(from_status, str) or not isinstance(to_status, str):
        return False
    allowed = _APPROVAL_TRANSITIONS.get(from_status)
    if allowed is None:
        return False
    return to_status in allowed


def _assert_transition(
    kind: str,
    from_status: object,
    to_status: object,
    allowed: bool,
) -> None:
    """统一 assert 实现（未知状态 / 非法转移 ⇒ ``WorkflowStateError``）。"""
    if not allowed:
        raise WorkflowStateError(
            f"非法 {kind} 状态转移: {from_status!r} → {to_status!r}"
            "（未知状态或不允许的转移；不静默修正）"
        )


def assert_workflow_transition(from_status: object, to_status: object) -> None:
    """run 状态转移断言（非法 ⇒ ``WorkflowStateError``）。"""
    _assert_transition(
        "workflow",
        from_status,
        to_status,
        can_transition_workflow(from_status, to_status),
    )


def assert_node_transition(from_status: object, to_status: object) -> None:
    """node attempt 状态转移断言（非法 ⇒ ``WorkflowStateError``）。"""
    _assert_transition(
        "node",
        from_status,
        to_status,
        can_transition_node(from_status, to_status),
    )


def assert_approval_transition(from_status: object, to_status: object) -> None:
    """approval 状态转移断言（非法 ⇒ ``WorkflowStateError``）。"""
    _assert_transition(
        "approval",
        from_status,
        to_status,
        can_transition_approval(from_status, to_status),
    )
