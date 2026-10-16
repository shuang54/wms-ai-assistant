"""Workflow 执行域 DTO + JSON 值边界（Phase 6 ADR-01 §8 / §12 / 修正 2 / 修正 3）。

三层内容：

    1) **JSON 值边界**（修正 2）：``JsonValue`` 类型 + ``validate_json_value`` /
       ``serialize_json_value`` —— 节点输出（``NodeOutput``）与启动 input 共用。
       保证：
         * 可序列化 / 可持久化（严格 JSON 兼容子集）；
         * **不携带** DB Session / Connection / ORM Entity / 任意 Python 对象
           （非 JSON 类型一律拒绝，对象无从进入）；
         * **不允许无限制 object graph**（深度 + 字节双边界；
           32 KiB 内联对齐 Agent Tool Result 先例 —— ADR §8.2 / §12.3）。

    2) **执行身份**：``NodeExecutionIdentity``（workflow_run_id / node_id / attempt）。
       retry = 同一 node_id 的新 attempt（修正 1；不是 graph edge、不是状态回退）。

    3) **内存 DTO**（frozen；**零行为**）：``WorkflowRunResult`` /
       ``NodeRunOutcome`` / ``WorkflowApprovalRecord``。
       修正 3：``WorkflowApprovalRecord`` 只是 approval state representation；
       ``approve()`` / ``reject()`` / ``cancel()`` **不属于** DTO ——
       Runtime 才是状态转换 authority（CAS / 单赢 / 持久化转换在 Step 2/3）。

边界：零 IO / 零 DB；本模块不 import db / agent / services / tools / projects。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from backend.app.workflow.errors import (
    WF_ERROR_CODES,
    WorkflowInputError,
    WorkflowOutputError,
    WorkflowStateError,
)
from backend.app.workflow.states import (
    APPROVAL_STATUSES,
    APPROVAL_STATUS_PENDING,
    NODE_STATUSES,
    WORKFLOW_STATUSES,
    is_node_status,
    is_workflow_status,
)

__all__ = [
    # JSON 值边界
    "JSON_VALUE_MAX_DEPTH",
    "JSON_VALUE_MAX_BYTES",
    "JsonScalar",
    "JsonValue",
    "NodeOutput",
    "validate_json_value",
    "serialize_json_value",
    "validate_json_text",
    # 执行身份
    "NodeExecutionIdentity",
    # DTO
    "WorkflowRunResult",
    "NodeRunOutcome",
    "WorkflowApprovalRecord",
]


# ============================================================
# 1) JSON 值边界（修正 2：轻量 NodeOutput / JsonValue 边界）
# ============================================================

#: 值嵌套深度上限（防无限制 object graph；根 = 0，最深子元素 = MAX）。
JSON_VALUE_MAX_DEPTH: Final[int] = 8

#: 值序列化字节上限（UTF-8；32 KiB —— 对齐 Agent Tool Result 内联先例）。
JSON_VALUE_MAX_BYTES: Final[int] = 32 * 1024

#: JSON 标量（比较值 / 叶子）。
JsonScalar = str | int | float | bool | None

#: JSON 兼容值域（严格子集：**仅** None / str / int / float / bool / list / dict[str, ...]；
#: tuple / set / bytes / datetime / 任意对象 / ORM 实体一律拒绝）。
JsonValue = (
    str
    | int
    | float
    | bool
    | None
    | list["JsonValue"]
    | dict[str, "JsonValue"]
)

#: 语义别名：节点输出必须满足 ``JsonValue`` 边界（见 ``validate_json_value``）。
NodeOutput = JsonValue


def _reject_non_json(value: object, depth: int) -> None:
    """拒绝非 JSON 类型（消息只含类名与深度——不含值内容）。"""
    raise WorkflowOutputError(
        f"值不是 JSON 兼容类型: {type(value).__name__}"
        f"（depth={depth}；仅允许 None / str / int / float / bool / list / dict）"
    )


def validate_json_value(value: object, *, _depth: int = 0) -> None:
    """校验 ``value`` 满足 JSON 值边界（fail-closed；不归一化、不改写）。

    Args:
        value: 待校验值（可为 ``None``）。
        _depth: 内部递归深度（调用方不传）。

    Raises:
        WorkflowOutputError:
            * 非 JSON 兼容类型（tuple / set / bytes / 任意对象 / ORM 实体 …）；
            * ``float`` 为 NaN / Infinity（不可序列化 / 不可持久化）；
            * ``dict`` 键不是 ``str``；
            * 嵌套深度超过 :data:`JSON_VALUE_MAX_DEPTH`（递归结构亦在此被拦截）。
    """
    if _depth > JSON_VALUE_MAX_DEPTH:
        raise WorkflowOutputError(
            f"JSON 值嵌套深度超过上限 {JSON_VALUE_MAX_DEPTH}"
            "（拒绝无限制 object graph）"
        )
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise WorkflowOutputError(
                "JSON 值不允许 NaN / Infinity（不可序列化 / 不可持久化）"
            )
        return
    if isinstance(value, list):
        for item in value:
            validate_json_value(item, _depth=_depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise WorkflowOutputError(
                    f"JSON 对象的键必须是 str（当前: {type(key).__name__}）"
                )
            validate_json_value(item, _depth=_depth + 1)
        return
    _reject_non_json(value, _depth)


def serialize_json_value(value: object) -> str:
    """校验并序列化为 JSON 文本（紧凑；fail-closed；不含敏感值内容于错误）。

    Raises:
        WorkflowOutputError: 值非法（同 ``validate_json_value``）或序列化
            字节数超过 :data:`JSON_VALUE_MAX_BYTES`（超限 ⇒ 拒绝，**不截断**）。
    """
    validate_json_value(value)
    text = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    size = len(text.encode("utf-8"))
    if size > JSON_VALUE_MAX_BYTES:
        raise WorkflowOutputError(
            f"JSON 值序列化大小 {size} bytes 超过内联上限 "
            f"{JSON_VALUE_MAX_BYTES} bytes（fail-closed：不截断 / 不静默丢弃）"
        )
    return text


def validate_json_text(value: object) -> str:
    """校验**已序列化的 JSON 文本**（读侧内联边界；单一事实来源）。

    与 :func:`serialize_json_value` 构成同一内联契约的两端：
    写侧保证 ``output`` 是本函数的合法输入；读侧可用本函数在不信任持久化
    内容时**独立复验**（例如行被绕过仓储直接改写 / 被截断）。

    契约（与 ``ai_ops.workflow_node_run.output`` 列一致）：
        * ``value`` 必须是 str（``None`` 由调用方自行处理——本函数不接受）；
        * 去空白后非空；
        * UTF-8 字节数 ≤ :data:`JSON_VALUE_MAX_BYTES`（32 KiB）；
        * 能被 :func:`json.loads` 解析为合法 JSON。

    失败语义：任一条不满足 ⇒ :class:`WorkflowOutputError`
    （**拒绝**；不截断、不清空、不自动修复、不返回部分结果）。

    Args:
        value: 待校验的 JSON 文本。

    Returns:
        **原样**返回 ``value``（不重新序列化——避免改变持久化事实）。

    Raises:
        WorkflowOutputError: 非法 / 超限 / 无法 UTF-8 编码 / 非合法 JSON。
    """
    if not isinstance(value, str) or not value.strip():
        raise WorkflowOutputError("JSON 文本必须是非空 str")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise WorkflowOutputError(
            "JSON 文本无法按 UTF-8 编码（fail-closed：不修复）"
        ) from exc
    if size > JSON_VALUE_MAX_BYTES:
        raise WorkflowOutputError(
            f"JSON 文本大小 {size} bytes 超过内联上限 "
            f"{JSON_VALUE_MAX_BYTES} bytes（fail-closed：不截断 / 不静默丢弃）"
        )
    try:
        json.loads(value)
    except ValueError as exc:  # json.JSONDecodeError ⊂ ValueError
        raise WorkflowOutputError("JSON 文本无法解析（fail-closed：不修复）") from exc
    return value


# ============================================================
# 2) 执行身份
# ============================================================


def _require_non_empty_str(name: str, value: object) -> str:
    """字段级非空 str 校验（fail-closed；消息只含字段名）。"""
    if not isinstance(value, str) or not value.strip():
        raise WorkflowInputError(
            f"{name} 必须是非空 str（当前: {type(value).__name__}）"
        )
    return value


def _require_non_negative_int(name: str, value: object) -> int:
    """字段级非负 int 校验（拒绝 bool / float —— 不静默转换）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkflowInputError(
            f"{name} 必须是 int（当前: {type(value).__name__}）"
        )
    if value < 0:
        raise WorkflowInputError(f"{name} 必须 >= 0（当前: {value}）")
    return value


def _require_optional_elapsed_ms(value: object) -> float | None:
    """``elapsed_ms``：None 或有限非负数值（int/float；拒绝 bool / NaN / inf）。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkflowInputError(
            f"elapsed_ms 必须是数值或 None（当前: {type(value).__name__}）"
        )
    number = float(value)
    if math.isnan(number) or math.isinf(number) or number < 0:
        raise WorkflowInputError(
            f"elapsed_ms 必须是有限非负数（当前: {value!r}）"
        )
    return number


@dataclass(frozen=True)
class NodeExecutionIdentity:
    """一次节点 attempt 的执行身份（frozen）。

    Attributes:
        workflow_run_id: Workflow Run 身份（由 Runtime 签发；**不**复用 agent run_id）。
        node_id:         定义内节点标识（来自 ``WorkflowDefinition``）。
        attempt:         本节点第几次尝试（1 起；retry = 新 attempt 新行 —— 修正 1）。
    """

    workflow_run_id: str
    node_id: str
    attempt: int

    def __post_init__(self) -> None:
        _require_non_empty_str("workflow_run_id", self.workflow_run_id)
        _require_non_empty_str("node_id", self.node_id)
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int):
            raise WorkflowInputError(
                f"attempt 必须是 int（当前: {type(self.attempt).__name__}）"
            )
        if self.attempt < 1:
            raise WorkflowInputError(f"attempt 必须 >= 1（当前: {self.attempt}）")


# ============================================================
# 3) 内存 DTO（frozen；零行为 —— 状态转换不属于 DTO）
# ============================================================


@dataclass(frozen=True)
class NodeRunOutcome:
    """一次 node attempt 的内存执行结果（frozen）。

    Attributes:
        identity:   执行身份（run / node / attempt）。
        status:     ``NODE_STATUSES`` 之一（未知 ⇒ 拒绝）。
        output:     已满足 JSON 值边界的节点输出（``None`` = 无输出）。
        error_code: 稳定错误码（``WF_ERROR_CODES`` 白名单或 None）。
        elapsed_ms: 耗时（None 或有限非负）。

    Note:
        **零行为**：本 DTO 不持久化、不裁决、不重试（Step 2/3 负责）。
    """

    identity: NodeExecutionIdentity
    status: str
    output: NodeOutput | None = None
    error_code: str | None = None
    elapsed_ms: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identity, NodeExecutionIdentity):
            raise WorkflowInputError(
                "identity 必须是 NodeExecutionIdentity"
                f"（当前: {type(self.identity).__name__}）"
            )
        if not is_node_status(self.status):
            raise WorkflowStateError(
                f"未知 node attempt 状态: {self.status!r}"
            )
        if self.output is not None:
            validate_json_value(self.output)
        if self.error_code is not None and self.error_code not in WF_ERROR_CODES:
            raise WorkflowInputError(
                f"error_code 必须是 WF_ERROR_CODES 白名单成员（当前: {self.error_code!r}）"
            )
        _require_optional_elapsed_ms(self.elapsed_ms)


@dataclass(frozen=True)
class WorkflowRunResult:
    """一次 ``start`` / ``resume`` 调用的内存结果（frozen；对齐 AgentRuntimeResult 风格）。

    Attributes:
        workflow_run_id: Run 身份。
        status:          ``WORKFLOW_STATUSES`` 之一（未知 ⇒ 拒绝）。
        current_node_id: 当前/下一个节点（None = 无）。
        nodes_executed:  **本次调用**真实执行的节点数（>= 0；非 Replay）。
        attempts_used:   **本次调用**真实产生 attempt 数（>= 0）。
        error_code:      稳定错误码（白名单或 None）。
        approval_id:     等待审批时的审批身份（否则 None）。

    Note:
        **不含** input / output 全文（安全边界 —— ADR §8.2）。
    """

    workflow_run_id: str
    status: str
    current_node_id: str | None = None
    nodes_executed: int = 0
    attempts_used: int = 0
    error_code: str | None = None
    approval_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_str("workflow_run_id", self.workflow_run_id)
        if not is_workflow_status(self.status):
            raise WorkflowStateError(
                f"未知 workflow 状态: {self.status!r}"
            )
        if self.current_node_id is not None:
            _require_non_empty_str("current_node_id", self.current_node_id)
        _require_non_negative_int("nodes_executed", self.nodes_executed)
        _require_non_negative_int("attempts_used", self.attempts_used)
        if self.error_code is not None and self.error_code not in WF_ERROR_CODES:
            raise WorkflowInputError(
                f"error_code 必须是 WF_ERROR_CODES 白名单成员（当前: {self.error_code!r}）"
            )
        if self.approval_id is not None:
            _require_non_empty_str("approval_id", self.approval_id)


@dataclass(frozen=True)
class WorkflowApprovalRecord:
    """审批**事实**表示（frozen DTO；修正 3）。

    定义（域层）：``PENDING`` → ``APPROVED`` | ``REJECTED``（终态不可逆）。

    **明确不属于本 DTO**：
        * ``approve()`` / ``reject()`` / ``cancel()`` 等方法；
        * CAS / 单赢 / 持久化状态转换（Step 2/3；Runtime 是转换 authority）。

    Attributes:
        approval_id:    审批身份。
        workflow_run_id: 所属 Run。
        node_id:        审批节点标识。
        status:         ``APPROVAL_STATUSES`` 之一。
        resolved_at:    裁决时间（PENDING ⇒ 必须 None；终态可携带）。
    """

    approval_id: str
    workflow_run_id: str
    node_id: str
    status: str
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty_str("approval_id", self.approval_id)
        _require_non_empty_str("workflow_run_id", self.workflow_run_id)
        _require_non_empty_str("node_id", self.node_id)
        if not isinstance(self.status, str) or self.status not in APPROVAL_STATUSES:
            raise WorkflowStateError(
                f"未知 approval 状态: {self.status!r}"
            )
        if self.status == APPROVAL_STATUS_PENDING and self.resolved_at is not None:
            raise WorkflowInputError(
                "PENDING 审批不得携带 resolved_at（状态-字段一致性）"
            )
        if self.resolved_at is not None and not isinstance(
            self.resolved_at, datetime
        ):
            raise WorkflowInputError(
                "resolved_at 必须是 datetime 或 None"
                f"（当前: {type(self.resolved_at).__name__}）"
            )
