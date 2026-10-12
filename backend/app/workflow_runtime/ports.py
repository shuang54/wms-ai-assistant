"""Workflow Runtime 端口层（Phase 6 Step 3 · S1；ADR-01 §9/§10/§11/§15 + D1/D2/D3 冻结）。

本模块 = Runtime 的**注入面**（Protocol）与**跨边界 DTO**：零 IO / 零实现。
生产实现（3 个 Repository / AIOrchestratorService / ToolExecutionService）由 factory
适配注入（鸭子类型）；测试以内存 Fake 注入。

设计纪律（冻结，不得在本层外扩）：

```text
D-A  依赖边界：runtime 核心（workflow_runtime 包）**只允许** import stdlib +
     backend.app.workflow（域）；db / services / tools / agent / api / llm
     一律经本模块 Protocol 注入。
D-B  一切 run 状态写入均经 CAS（update_cas / request_cancel）；无"无条件 UPDATE"；
     终态不可逆由域转移表 + 终态守卫 + 条件 UPDATE（cancel 条件含非终态）组合保证。
D-C  取消三态（D3 冻结）：CANCELLED / CANCEL_REQUESTED / NOT_CANCELLABLE ——
     以 CancelOutcome 承载，禁止二值化；CAS 失败按 RunConflictError(reason) 分类，
     禁止盲目覆盖 / 无限重试 / 假设存在活跃执行者。
D-D  执行权原子声明（D3 冻结 · 并发约束 1-3/5）：
       attempt create(RUNNING) **必须先于**任何外部执行器调用（LLM / Agent / Tool）；
       (workflow_run_id, node_id, attempt) 唯一约束 = 执行权的原子声明；
       create 冲突（NodeRunAlreadyExistsError）或恢复判定遇未决 attempt
       ⇒ UNKNOWN fail-closed（WF_ERROR_NODE_UNKNOWN）；
       **严禁**再次调用外部执行器 / **严禁**同 attempt 重试。
D-E  cancel_agent_run 只按既有**协作式**接口调用；不假设其撤销任何已发生副作用；
     不把"数据库状态 RUNNING"当作活跃执行者证明，不把 CAS 失败当作其证明。
D-F  端口为**同步**（生产实现 = 同步 Repository）；Runtime 在事件循环内经
     asyncio.to_thread 调用——端口语义不因异步包装改变。
```

镜像常量契约（值由测试锁定，防漂移）：
    * NEXT_ACTION_NONE ↔ db 模型 `WORKFLOW_NEXT_ACTION_NONE`；
    * AGENT_STATUS_WAITING_APPROVAL ↔ `agent.models.AGENT_STATUS_WAITING_APPROVAL`。

错误映射（factory 适配层职责；本层只定义目标语义）：
```text
workflow_run_repository.WorkflowRunNotFoundError        → RunNotFoundError
workflow_run_repository.WorkflowRunConflictError        → RunConflictError(reason)
workflow_node_run_repository.*AlreadyExistsError        → NodeRunAlreadyExistsError
workflow_node_run_repository.*NotFoundError             → NodeRunNotFoundError
workflow_node_run_repository.*ConflictError             → NodeRunConflictError
workflow_approval_repository.*RepositoryError           → WorkflowPortsError
```
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Final, Protocol, runtime_checkable

from backend.app.workflow.states import (
    NODE_STATUS_RUNNING,
    WORKFLOW_STATUS_PENDING,
)

__all__ = [
    # 取消三态（D3 冻结）
    "CANCEL_RESULT_CANCELLED",
    "CANCEL_RESULT_REQUESTED",
    "CANCEL_RESULT_NOT_CANCELLABLE",
    "CANCEL_RESULTS",
    # 镜像常量
    "AGENT_STATUS_WAITING_APPROVAL",
    "NEXT_ACTION_NONE",
    # CAS 失败分类（reason；与 Repository 诊断 reason 对齐）
    "RUN_CONFLICT_REASON_VERSION",
    "RUN_CONFLICT_REASON_TERMINAL",
    "RUN_CONFLICT_REASON_ALREADY_REQUESTED",
    "RUN_CONFLICT_REASON_UNKNOWN",
    "RUN_CONFLICT_REASONS",
    # DTO
    "RunRecord",
    "NodeRunRecord",
    "ApprovalRecord",
    "OrchestrationOutcome",
    "ToolCallContext",
    "ToolExecutionOutcome",
    "CancelOutcome",
    # 异常
    "WorkflowPortsError",
    "RunNotFoundError",
    "RunConflictError",
    "NodeRunAlreadyExistsError",
    "NodeRunNotFoundError",
    "NodeRunConflictError",
    # 工厂类型
    "Clock",
    "RequestIdFactory",
    "ApprovalIdFactory",
    "RetryableClassifier",
    "OrchestratorProvider",
    "ToolExecutionProvider",
    # Protocol
    "WorkflowRunStore",
    "WorkflowNodeRunStore",
    "WorkflowApprovalStore",
    "OrchestratorPort",
    "ToolExecutionPort",
]


# ============================================================
# 1) 冻结常量
# ============================================================

# ---- 取消三态（D3 冻结：返回值必须区分三者；禁止二值化） ----

#: 已确认终结（cancel 的收尾 CAS 成功；终态不可逆）。
CANCEL_RESULT_CANCELLED: Final[str] = "CANCELLED"

#: 取消**意图**已持久化（信号已置位），尚未确认终结——
#: 需幂等重试或由后续观察者（resume / 执行者边界）消费信号。
CANCEL_RESULT_REQUESTED: Final[str] = "CANCEL_REQUESTED"

#: Run 已终态或不符合取消条件（no-op；不写入）。
CANCEL_RESULT_NOT_CANCELLABLE: Final[str] = "NOT_CANCELLABLE"

#: 全部合法取消结果（白名单；禁止自由文本）。
CANCEL_RESULTS: Final[frozenset[str]] = frozenset(
    {
        CANCEL_RESULT_CANCELLED,
        CANCEL_RESULT_REQUESTED,
        CANCEL_RESULT_NOT_CANCELLABLE,
    }
)

# ---- 镜像常量（测试锁定与来源一致） ----

#: agent 状态镜像（值 = `agent.models.AGENT_STATUS_WAITING_APPROVAL`）——
#: AI 节点 AGENT WAITING 嵌套判定使用（零 agent import；测试锁定一致性）。
AGENT_STATUS_WAITING_APPROVAL: Final[str] = "WAITING_APPROVAL"

#: run 创建时的 next_action 初值（值 = db 模型 `WORKFLOW_NEXT_ACTION_NONE`）。
NEXT_ACTION_NONE: Final[str] = "NONE"

# ---- CAS 失败分类（reason；与 Repository 诊断 reason 语义对齐） ----

#: state_version 不匹配（并发推进竞争）。
RUN_CONFLICT_REASON_VERSION: Final[str] = "version"

#: 行已终态（终态不可逆；cancel 的 no-op 判定）。
RUN_CONFLICT_REASON_TERMINAL: Final[str] = "terminal"

#: 取消信号已置位（幂等重试路径）。
RUN_CONFLICT_REASON_ALREADY_REQUESTED: Final[str] = "already_requested"

#: 无法归因（fail-closed；不猜测）。
RUN_CONFLICT_REASON_UNKNOWN: Final[str] = "unknown"

#: 全部合法冲突分类（白名单）。
RUN_CONFLICT_REASONS: Final[frozenset[str]] = frozenset(
    {
        RUN_CONFLICT_REASON_VERSION,
        RUN_CONFLICT_REASON_TERMINAL,
        RUN_CONFLICT_REASON_ALREADY_REQUESTED,
        RUN_CONFLICT_REASON_UNKNOWN,
    }
)


# ============================================================
# 2) 轻校验辅助（端口层只做结构性校验；域白名单由 Runtime/Execution 使用处校验）
# ============================================================


def _require_non_empty_str(field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowPortsError(
            f"{field} 必须是非空 str（当前: {type(value).__name__}）"
        )
    return value


def _require_optional_non_empty_str(field: str, value: object) -> str | None:
    if value is None:
        return None
    return _require_non_empty_str(field, value)


def _require_int_at_least(field: str, value: object, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkflowPortsError(
            f"{field} 必须是 int（当前: {type(value).__name__}）"
        )
    if value < minimum:
        raise WorkflowPortsError(f"{field} 必须 >= {minimum}（当前: {value}）")
    return value


def _require_bool(field: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise WorkflowPortsError(
            f"{field} 必须是 bool（当前: {type(value).__name__}）"
        )
    return value


def _require_optional_datetime(field: str, value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise WorkflowPortsError(
            f"{field} 必须是 datetime 或 None（当前: {type(value).__name__}）"
        )
    return value


def _require_optional_elapsed_ms(field: str, value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkflowPortsError(
            f"{field} 必须是数值或 None（当前: {type(value).__name__}）"
        )
    number = float(value)
    if math.isnan(number) or math.isinf(number) or number < 0:
        raise WorkflowPortsError(f"{field} 必须是有限非负数（当前: {value!r}）")
    return number


# ============================================================
# 3) 跨边界 DTO（frozen；零行为）
# ============================================================


@dataclass(frozen=True)
class RunRecord:
    """`workflow_run` 行的读取视图（frozen）。

    ``input`` = **D1**：持久化 JSON 文本（nullable；None = 无输入）。
    由 Store 端（生产 = Repository）在写入前经域层 ``serialize_json_value``
    保证 JSON-safe（≤ 32 KiB / 深度 ≤ 8）；本 DTO 只承载。
    时间戳字段允许 None（构造灵活；持久化行实际值由 Store 保证）。
    """

    workflow_run_id: str
    workflow_id: str
    workflow_version: str
    request_id: str
    project_id: str
    status: str
    state_version: int
    current_node_id: str | None
    next_action: str
    attempt_count: int
    cancel_requested: bool
    cancel_requested_at: datetime | None
    input: str | None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty_str("workflow_run_id", self.workflow_run_id)
        _require_non_empty_str("workflow_id", self.workflow_id)
        _require_non_empty_str("workflow_version", self.workflow_version)
        _require_non_empty_str("request_id", self.request_id)
        _require_non_empty_str("project_id", self.project_id)
        _require_non_empty_str("status", self.status)
        _require_int_at_least("state_version", self.state_version, 0)
        _require_optional_non_empty_str("current_node_id", self.current_node_id)
        _require_non_empty_str("next_action", self.next_action)
        _require_int_at_least("attempt_count", self.attempt_count, 0)
        _require_bool("cancel_requested", self.cancel_requested)
        _require_optional_datetime(
            "cancel_requested_at", self.cancel_requested_at
        )
        _require_optional_non_empty_str("input", self.input)
        _require_optional_datetime("created_at", self.created_at)
        _require_optional_datetime("started_at", self.started_at)
        _require_optional_datetime("completed_at", self.completed_at)
        _require_optional_datetime("updated_at", self.updated_at)


@dataclass(frozen=True)
class NodeRunRecord:
    """`workflow_node_run` 行的读取视图（frozen；per-attempt）。

    写入序（列表顺序）由 ``WorkflowNodeRunStore.list_for_run`` 的表达承载；
    本 DTO 不携带 created_at / updated_at（无业务读取需求）。
    """

    workflow_run_id: str
    node_id: str
    attempt: int
    node_type: str
    status: str
    output: str | None
    error_code: str | None
    elapsed_ms: float | None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_non_empty_str("workflow_run_id", self.workflow_run_id)
        _require_non_empty_str("node_id", self.node_id)
        _require_int_at_least("attempt", self.attempt, 1)
        _require_non_empty_str("node_type", self.node_type)
        _require_non_empty_str("status", self.status)
        _require_optional_non_empty_str("output", self.output)
        _require_optional_non_empty_str("error_code", self.error_code)
        _require_optional_elapsed_ms("elapsed_ms", self.elapsed_ms)
        _require_optional_datetime("started_at", self.started_at)
        _require_optional_datetime("completed_at", self.completed_at)


@dataclass(frozen=True)
class ApprovalRecord:
    """`workflow_approval` 行的读取视图（frozen）。

    状态一致性（PENDING ⇒ resolved_at NULL；终态 ⇒ 非 NULL）由 Store 写入路径
    （生产 = Repository 硬编码）+ runtime 使用处保证；本 DTO 只承载。
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
        _require_non_empty_str("status", self.status)
        _require_optional_datetime("resolved_at", self.resolved_at)


@dataclass(frozen=True)
class OrchestrationOutcome:
    """一次 AI 节点调用的结果（frozen；适配自 ``AIOrchestrationResult``）。

    字段为**字符串化 / 稳定**投影（零 services / agent import）；
    ``route`` 透传 Router 结果（"rag" / "tool" / "text_to_sql" / "agent"）；
    ``agent_status`` 使用镜像常量比较（如 ``AGENT_STATUS_WAITING_APPROVAL``）。
    """

    route: str
    content: str | None = None
    agent_run_id: str | None = None
    agent_status: str | None = None
    agent_approval_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_str("route", self.route)
        _require_optional_non_empty_str("content", self.content)
        _require_optional_non_empty_str("agent_run_id", self.agent_run_id)
        _require_optional_non_empty_str("agent_status", self.agent_status)
        _require_optional_non_empty_str(
            "agent_approval_id", self.agent_approval_id
        )


@dataclass(frozen=True)
class ToolCallContext:
    """tool 节点执行上下文（frozen；对齐既有 ToolExecutionContext 字段面）。"""

    request_id: str
    round: int = 1
    project_id: str | None = None
    tool_call_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_str("request_id", self.request_id)
        _require_int_at_least("round", self.round, 1)
        _require_optional_non_empty_str("project_id", self.project_id)
        _require_optional_non_empty_str("tool_call_id", self.tool_call_id)


@dataclass(frozen=True)
class ToolExecutionOutcome:
    """一次 tool 节点调用的结果（frozen；适配自 ``ToolResult``）。

    ``error`` 仅供分类 / 日志；**不得**落库（node_run.error_code 只用
    WF_ERROR_CODES 白名单码）。
    """

    success: bool
    data: Any = None
    error: str | None = None

    def __post_init__(self) -> None:
        _require_bool("success", self.success)
        _require_optional_non_empty_str("error", self.error)


@dataclass(frozen=True)
class CancelOutcome:
    """取消入口的返回值（frozen；D3 冻结三态）。

    Attributes:
        workflow_run_id: Run 身份。
        result:          ``CANCEL_RESULTS`` 之一：
                         CANCELLED（已确认终结）/ CANCEL_REQUESTED（意图已持久化、
                         未确认终结——幂等重试或由观察者消费）/
                         NOT_CANCELLABLE（已终态或不符合条件；no-op）。
        run:             最新 Run 视图（可选；便于调用方直接读取状态）。
    """

    workflow_run_id: str
    result: str
    run: RunRecord | None = None

    def __post_init__(self) -> None:
        _require_non_empty_str("workflow_run_id", self.workflow_run_id)
        if not isinstance(self.result, str) or self.result not in CANCEL_RESULTS:
            raise WorkflowPortsError(
                f"result 必须是 CANCEL_RESULTS 成员（当前: {self.result!r}）"
            )
        if self.run is not None and not isinstance(self.run, RunRecord):
            raise WorkflowPortsError("run 必须是 RunRecord 或 None")


# ============================================================
# 4) 端口异常（runtime 命名空间；不依赖 db / services 异常类型）
# ============================================================


class WorkflowPortsError(Exception):
    """端口层错误基类（factory 适配层把外部异常映射到本家族）。"""


class RunNotFoundError(WorkflowPortsError):
    """``workflow_run_id`` 不存在。"""

    def __init__(self, workflow_run_id: str) -> None:
        super().__init__(f"Workflow Run 不存在: {workflow_run_id!r}")
        self.workflow_run_id = workflow_run_id


class RunConflictError(WorkflowPortsError):
    """run 级 CAS / 条件 UPDATE 未命中（**分类**，禁止盲目覆盖）。

    ``reason`` ∈ ``RUN_CONFLICT_REASONS``（version / terminal /
    already_requested / unknown）——调用方据此决定：幂等返回、no-op 或
    安全退让（不重试、不覆盖、不假设活跃执行者）。
    """

    def __init__(self, message: str, *, reason: str) -> None:
        if not isinstance(reason, str) or reason not in RUN_CONFLICT_REASONS:
            raise ValueError(
                f"reason 必须是 RUN_CONFLICT_REASONS 成员（当前: {reason!r}）"
            )
        super().__init__(message)
        self.reason = reason


class NodeRunAlreadyExistsError(WorkflowPortsError):
    """该 ``(workflow_run_id, node_id, attempt)`` 身份已存在。

    语义（D-D）：**执行权已被声明**——调用方必须按 UNKNOWN fail-closed
    （不得调用外部执行器 / 不得同 attempt 重试）。
    """

    def __init__(
        self, workflow_run_id: str, node_id: str, attempt: int
    ) -> None:
        super().__init__(
            "attempt 已存在（执行权已被声明）:"
            f" run={workflow_run_id!r} node={node_id!r} attempt={attempt}"
        )
        self.workflow_run_id = workflow_run_id
        self.node_id = node_id
        self.attempt = attempt


class NodeRunNotFoundError(WorkflowPortsError):
    """node attempt 执行身份不存在。"""

    def __init__(
        self, workflow_run_id: str, node_id: str, attempt: int
    ) -> None:
        super().__init__(
            "attempt 不存在:"
            f" run={workflow_run_id!r} node={node_id!r} attempt={attempt}"
        )
        self.workflow_run_id = workflow_run_id
        self.node_id = node_id
        self.attempt = attempt


class NodeRunConflictError(WorkflowPortsError):
    """attempt 已终态（终态不可逆；retry 必须创建新 attempt 行）。

    ``reason`` 固定 = ``RUN_CONFLICT_REASON_TERMINAL``。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.reason = RUN_CONFLICT_REASON_TERMINAL


# ============================================================
# 5) 工厂类型别名（注入面）
# ============================================================


class Clock(Protocol):
    """时钟端口（可测：测试注入固定 / 手动推进的时钟）。"""

    def now(self) -> datetime: ...


#: request_id 生成器（生产 = 既有 ``new_request_id``；调用 correlation，非幂等键）。
RequestIdFactory = Callable[[], str]

#: approval_id 生成器（生产 = uuid4；workflow 命名空间，不复用 agent 的）。
ApprovalIdFactory = Callable[[], str]

#: 可重试分类器（生产 = 适配 ``llm.retry.is_retryable_llm_error`` 语义；
#: ADR §12 R4 第 1 条）。仅对**已持久化 FAILED** 的 attempt 生效（fail-before-retry）。
RetryableClassifier = Callable[[BaseException], bool]

#: per-project Orchestrator 解析器（生产 = build_orchestrator_for_project(agent_enabled=True)）。
OrchestratorProvider = Callable[[str], "OrchestratorPort"]

#: per-project Tool 执行边界解析器（生产 = per-project ToolExecutionService，
#: 与 Orchestrator 共享同一实例与 capabilities 白名单）。
ToolExecutionProvider = Callable[[str], "ToolExecutionPort"]


# ============================================================
# 6) 存储端口（同步；生产实现 = 3 个 Repository 的鸭子适配）
# ============================================================


@runtime_checkable
class WorkflowRunStore(Protocol):
    """`workflow_run` 持久化端口（D-B：一切写入均经 CAS / 条件 UPDATE）。

    错误契约（映射见模块 docstring）：
        * ``get`` 无行 ⇒ ``None``；
        * ``update_cas`` 未命中 ⇒ ``RunConflictError``（reason=version/terminal/unknown）
          或 ``RunNotFoundError``；
        * ``request_cancel`` 未命中 ⇒ ``RunConflictError``
          （reason=terminal / already_requested / unknown）——调用方**分类**处理：
          terminal ⇒ NOT_CANCELLABLE（no-op）；already_requested ⇒ 幂等继续收尾尝试。
    """

    def create(
        self,
        *,
        workflow_run_id: str,
        workflow_id: str,
        workflow_version: str,
        request_id: str,
        project_id: str,
        input: str | None,
        status: str = WORKFLOW_STATUS_PENDING,
        current_node_id: str | None = None,
        next_action: str = NEXT_ACTION_NONE,
    ) -> RunRecord:
        """新建 run（``state_version`` = 0；重复身份 ⇒ ``WorkflowPortsError``）。

        ``input``（D1）：**JSON 文本**（None = 无输入）；Store 端负责
        ``serialize_json_value`` 边界（≤ 32 KiB / 深度 ≤ 8），**不**接受任意对象。
        ``status`` 仅允许初始态（PENDING / RUNNING；Store 端校验）。
        """
        ...

    def get(self, workflow_run_id: str) -> RunRecord | None:
        """按身份读取（含 ``input``；无 ⇒ ``None``）。"""
        ...

    def update_cas(
        self,
        *,
        workflow_run_id: str,
        expected_state_version: int,
        status: str,
        current_node_id: str | None,
        next_action: str,
        attempt_count: int,
    ) -> RunRecord:
        """**单条 SQL** CAS 推进（expected = 会话内 fresh read 的版本）。

        失败即**会话终止信号**（E2）：不重试、不覆盖——回读分类后按状态行动。
        """
        ...

    def request_cancel(self, workflow_run_id: str) -> RunRecord:
        """**单条 SQL** 置位取消信号（条件：未置位 AND 非终态）。

        只置信号（协作式意图），**不**代表 run 已终结——收尾由
        ``update_cas``（CANCELLED）承担（D-C）。
        """
        ...


@runtime_checkable
class WorkflowNodeRunStore(Protocol):
    """`workflow_node_run` 持久化端口（per-attempt；D-D 执行权原子声明）。

    **执行权协议（冻结 · 并发约束 1-5 的唯一载体）**：
        1) Runtime **必须**先 ``create(status=RUNNING)`` 成功，然后才允许调用
           外部执行器（LLM / Agent / Tool）——顺序不可交换；
        2) ``UNIQUE(workflow_run_id, node_id, attempt)`` 保证同一尝试只被声明一次；
        3) ``create`` 抛 ``NodeRunAlreadyExistsError`` ⇒ 竞争者或既有未决 attempt
           存在 ⇒ **UNKNOWN fail-closed**：不得调用外部执行器、不得同 attempt 重试；
        4) 恢复判定遇"最新 attempt 非终态"（RUNNING / WAITING_APPROVAL）⇒ 同一
           fail-closed（不重跑）；
        5) 尚未创建 attempt 的并发入口：任何合法路径都必须先经 (1) 的 create
           竞争——唯一约束单赢 ⇒ 无重复执行（无需 claim / lease）。
    """

    def create(
        self,
        *,
        workflow_run_id: str,
        node_id: str,
        attempt: int,
        node_type: str,
        status: str = NODE_STATUS_RUNNING,
        output: str | None = None,
        error_code: str | None = None,
        elapsed_ms: float | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> NodeRunRecord:
        """新建 attempt（**必须发生在外部执行器调用之前**；重复身份 ⇒ AlreadyExists）。"""
        ...

    def get(
        self, *, workflow_run_id: str, node_id: str, attempt: int
    ) -> NodeRunRecord | None:
        """按执行身份读取（无 ⇒ ``None``）。"""
        ...

    def get_latest_attempt(
        self, *, workflow_run_id: str, node_id: str
    ) -> NodeRunRecord | None:
        """该 node 的最新 attempt（恢复 / UNKNOWN 判定入口；无 ⇒ ``None``）。"""
        ...

    def list_for_run(self, workflow_run_id: str) -> tuple[NodeRunRecord, ...]:
        """该 run 的全部 attempt（**写入序 = 列表顺序**；预算恢复 + previous 恢复）。"""
        ...

    def update(
        self,
        *,
        workflow_run_id: str,
        node_id: str,
        attempt: int,
        status: str,
        output: str | None = None,
        error_code: str | None = None,
        elapsed_ms: float | None = None,
        completed_at: datetime | None = None,
    ) -> NodeRunRecord:
        """条件 UPDATE（身份命中 AND 非终态；已终态 ⇒ ``NodeRunConflictError``）。"""
        ...


@runtime_checkable
class WorkflowApprovalStore(Protocol):
    """`workflow_approval` 持久化端口（独立审批状态机；条件 UPDATE 单赢）。"""

    def create(
        self, *, approval_id: str, workflow_run_id: str, node_id: str
    ) -> ApprovalRecord:
        """幂等创建（PENDING；重复 ``(run, node)`` ⇒ 返回**既有**行，不重置裁决）。"""
        ...

    def get(self, approval_id: str) -> ApprovalRecord | None:
        """按审批身份读取（无 ⇒ ``None``）。"""
        ...

    def get_for_node(
        self, *, workflow_run_id: str, node_id: str
    ) -> ApprovalRecord | None:
        """按 ``(run, node)`` 读取（resume 主路径；无 ⇒ ``None``）。"""
        ...

    def resolve(
        self, *, approval_id: str, decision: str
    ) -> ApprovalRecord | None:
        """条件 UPDATE（PENDING → APPROVED / REJECTED；单赢）。

        返回 ``None`` = **无转换**（已裁决 / 不存在）——调用方**回读分类**：
        同裁决 ⇒ 幂等返回既有；异裁决 / run 已终态 ⇒
        ``WF_ERROR_APPROVAL_CONFLICT``（fail-closed）。
        """
        ...


# ============================================================
# 7) 执行端口（异步；生产实现 = AIOrchestratorService / ToolExecutionService）
# ============================================================


@runtime_checkable
class OrchestratorPort(Protocol):
    """AI 节点调用面（**唯一允许路径**：AIOrchestratorService 的公开方法）。

    契约（ADR §10）：
        * 不 import AgentRuntime 内部 / 不直达 agent 六表；
        * ``cancel_agent_run`` 只按既有**协作式**接口调用（D-E）——不假设它
          中断运行中的 Tool、不假设任何副作用被撤销；
        * WAITING 嵌套（AGENT HITL）：``execute`` / ``resume_agent_run`` 在
          子 Agent 暂停时返回 ``agent_status = WAITING_APPROVAL`` 的结果
          （不抛错）；workflow **不自动**裁决子 Agent 的审批。
    """

    async def execute(
        self, question: str, *, context: str | None = None
    ) -> OrchestrationOutcome: ...

    async def resume_agent_run(
        self, run_id: str, *, question: str, context: str | None = None
    ) -> OrchestrationOutcome: ...

    async def cancel_agent_run(self, run_id: str) -> None: ...


@runtime_checkable
class ToolExecutionPort(Protocol):
    """tool 节点执行面（**唯一允许路径**：ToolExecutionService；不建第二套 executor）。"""

    async def execute(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        context: ToolCallContext | None = None,
    ) -> ToolExecutionOutcome: ...
