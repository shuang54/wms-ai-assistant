"""Workflow Runtime（Phase 6 Step 4 · S4；ADR-01 §8/§9/§10/§11/§12/§14/§15/§16 + S1 冻结协议）。

职责（**Run 生命周期 + 节点调度 + 持久化状态推进 + 等待/恢复 + 取消 + 失败边界**）：
    * 启动 Run（definition 解析 + 输入校验 + 创建 PENDING 行）；
    * inline 顺序调度节点（经 :class:`NodeExecutor`——S3；不自行执行任何节点逻辑）；
    * 每次状态变更均经 CAS / 条件更新（无无条件 UPDATE）；
    * CONDITION 分支（读 S3 输出 "true"/"false"；不静默跳过无效目标）；
    * Workflow Approval 等待与恢复（裁决与恢复是**不同操作**）；
    * 嵌套 Agent 等待与恢复（不递归启动 Workflow、不自动裁决子 Agent）；
    * 失败 / 终态 / 取消（协作式；三态不二值化）。

执行驱动（ADR §9.2 · A' = Inline until Wait-Point + Durable State + External-Driven
Resume）：
```text
start   : 创建 run（PENDING）→ inline 推进（PENDING→RUNNING→…）直到 TERMINAL
          （COMPLETED / FAILED / CANCELLED）或 WAITING（approval / 嵌套 Agent）
advance : 读持久化状态 → 从断点继续 inline（与 resume 同一入口；幂等可重入）
crash   : run 停在最后持久化状态；RUNNING attempt 无终态记录 ⇒ UNKNOWN
          fail-closed（**不自动重跑**；ADR §8.3 恢复判定）
```

并发安全（**最高优先级**；全部为「单赢点」组合——无 lease / 无 claim 表 / 无后台
执行者）：
```text
1) 执行权原子声明（S1 · D-D，顺序不可交换）：
   attempt create(RUNNING) **必须先于**任何外部执行器调用（LLM / Agent / Tool）；
   UNIQUE(workflow_run_id, node_id, attempt) 是执行权竞争的**唯一载体** ⇒ 单赢；
   create 冲突（NodeRunAlreadyExistsError）⇒ UNKNOWN fail-closed ——
   失败的竞争者**绝不**调用外部 Tool / AI。
2) attempt 编号：n = 该 node 已存在 attempt 的最大编号 + 1 —— 两个并发执行者
   会算出同一 n，于是双方同时落在 (1) 的唯一约束上（**不需要**编号协调锁）。
3) WAITING 恢复权：run 状态 CAS（WAITING → RUNNING，expected=state_version）单赢；
   失败者重读分类（RUNNING ⇒ 让位；终态 ⇒ 返回事实）。
4) run 推进：一切写入均经 update_cas（expected = 会话内 fresh read 的版本）；
   CAS 失败 ⇒ **重读 + 分类**（version / terminal / …），**零自动重试**
   （不存在无限 CAS 循环——失败者让位，由调用方决定是否再次 advance）。
5) 取消：request_cancel（条件 UPDATE：未置位 AND 非终态）+ 收尾 CAS（CANCELLED）
   单赢；三态（CANCELLED / CANCEL_REQUESTED / NOT_CANCELLABLE）不二值化。
```
以上「失败竞争者零外部调用 / 相同节点不重复执行」已由离线 Fake 端口并发测试证明；
其中单赢语义的**原子性**最终依赖 PostgreSQL 的唯一约束与 CAS ⇒ 必须叠加
DB-gated 集成测试（见测试模块 docstring——Fake 不替代 DB 原子性验证）。

边界（AST 测试锁定）：仅 import stdlib + ``backend.app.workflow``（域）+
同包 ``ports`` / ``binding`` / ``execution``；不 import db / services / tools /
agent / api / llm / projects；不 import 同包 factory / adapters（防未来循环）。
端口为同步（S1 · D-F）⇒ 全部经 ``asyncio.to_thread`` 调用（不阻塞事件循环）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time as _time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Final

from backend.app.workflow.definition import (
    NODE_TYPE_AI,
    NODE_TYPE_APPROVAL,
    NODE_TYPE_CONDITION,
    NODE_TYPE_TOOL,
    WorkflowDefinition,
    WorkflowDefinitionError,
    WorkflowNode,
    WorkflowRegistry,
)
from backend.app.workflow.errors import (
    WF_ERROR_APPROVAL_CONFLICT,
    WF_ERROR_APPROVAL_NOT_FOUND,
    WF_ERROR_APPROVAL_REJECTED,
    WF_ERROR_CODES,
    WF_ERROR_DEFINITION_VERSION_MISMATCH,
    WF_ERROR_LIMIT_REACHED,
    WF_ERROR_NODE_EXECUTION_FAILURE,
    WF_ERROR_NODE_UNKNOWN,
    WF_ERROR_PERSISTENCE_FAILURE,
    WF_ERROR_RUN_NOT_FOUND,
    WF_ERROR_STATE_CONFLICT,
    WorkflowOutputError,
)
from backend.app.workflow.models import serialize_json_value, validate_json_value
from backend.app.workflow.policy import WorkflowRuntimePolicy
from backend.app.workflow.states import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
    NODE_STATUS_FAILED,
    NODE_STATUS_RUNNING,
    NODE_STATUS_SUCCEEDED,
    NODE_STATUS_WAITING_APPROVAL,
    WORKFLOW_STATUS_CANCELLED,
    WORKFLOW_STATUS_COMPLETED,
    WORKFLOW_STATUS_FAILED,
    WORKFLOW_STATUS_PENDING,
    WORKFLOW_STATUS_RUNNING,
    WORKFLOW_STATUS_WAITING,
    WORKFLOW_TERMINAL_STATUSES,
)
from backend.app.workflow_runtime.execution import (
    NodeExecutionOutcome,
    NodeExecutor,
)
from backend.app.workflow_runtime.binding import render_template
from backend.app.workflow_runtime.ports import (
    AGENT_STATUS_WAITING_APPROVAL,
    NEXT_ACTION_NONE,
    ApprovalRecord,
    NodeRunAlreadyExistsError,
    NodeRunRecord,
    OrchestratorProvider,
    RunConflictError,
    RunNotFoundError,
    RunRecord,
    WorkflowApprovalStore,
    WorkflowNodeRunStore,
    WorkflowPortsError,
    WorkflowRunStore,
)

__all__ = [
    "RuntimeContractError",
    "RunOutcome",
    "RUN_OUTCOMES",
    "WorkflowRuntime",
]

logger = logging.getLogger(__name__)

# ============================================================
# 镜像常量（值由测试锁定与 db 模型一致；runtime 不得 import db —— D-A）
# ============================================================

#: next_action 白名单（镜像 `db.models.workflow_run`；NONE 复用 ports 导出）。
_NEXT_ACTION_NODE_EXECUTION: Final[str] = "NODE_EXECUTION"
_NEXT_ACTION_WAITING_APPROVAL: Final[str] = "WAITING_APPROVAL"
_NEXT_ACTION_COMPLETED: Final[str] = "COMPLETED"
_NEXT_ACTION_FAILED: Final[str] = "FAILED"
_NEXT_ACTION_CANCELLED: Final[str] = "CANCELLED"

# ============================================================
# RunOutcome（runtime 方法的统一结果；白名单强校验）
# ============================================================

#: 终态（run.status 一一对应）。
OUTCOME_COMPLETED: Final[str] = "COMPLETED"
OUTCOME_FAILED: Final[str] = "FAILED"
OUTCOME_CANCELLED: Final[str] = "CANCELLED"

#: 等待点（approval gate / 嵌套 Agent HITL）。
OUTCOME_WAITING_APPROVAL: Final[str] = "WAITING_APPROVAL"

#: run 处于 RUNNING（推进中；竞争失败让位时亦返回此分类）。
OUTCOME_RUNNING: Final[str] = "RUNNING"

#: 取消三态（D3 冻结；不二值化）。
OUTCOME_CANCEL_REQUESTED: Final[str] = "CANCEL_REQUESTED"
OUTCOME_NOT_CANCELLABLE: Final[str] = "NOT_CANCELLABLE"

#: fail-closed：不可安全判定（未决 RUNNING attempt / 持久化缺失等）。
OUTCOME_UNKNOWN: Final[str] = "UNKNOWN"

#: 竞争 / 冲突（CAS 版本竞争、状态冲突、冲突裁决等）。
OUTCOME_CONFLICT: Final[str] = "CONFLICT"

#: run 级预算耗尽（LIMIT_REACHED；重试将突破预算语义）。
OUTCOME_LIMIT_REACHED: Final[str] = "LIMIT_REACHED"

#: run 不存在 / 定义解析失败。
OUTCOME_NOT_FOUND: Final[str] = "NOT_FOUND"
OUTCOME_DEFINITION_MISMATCH: Final[str] = "DEFINITION_MISMATCH"

#: 全部合法 RunOutcome.outcome（白名单；禁止自由文本）。
RUN_OUTCOMES: Final[frozenset[str]] = frozenset(
    {
        OUTCOME_COMPLETED,
        OUTCOME_FAILED,
        OUTCOME_CANCELLED,
        OUTCOME_WAITING_APPROVAL,
        OUTCOME_RUNNING,
        OUTCOME_CANCEL_REQUESTED,
        OUTCOME_NOT_CANCELLABLE,
        OUTCOME_UNKNOWN,
        OUTCOME_CONFLICT,
        OUTCOME_LIMIT_REACHED,
        OUTCOME_NOT_FOUND,
        OUTCOME_DEFINITION_MISMATCH,
    }
)

#: 可重试错误码白名单（S4 冻结 · ADR §12.2 R4「具体码表实现阶段冻结」）：
#: 仅「执行未能产出结果」类别（execution 层归一码）；business refusal /
#: OUTPUT_UNAVAILABLE / APPROVAL_REJECTED / LIMIT_REACHED / UNKNOWN /
#: cancel 类一律不可重试（fail-closed——宁可不重试）。
_RETRYABLE_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {WF_ERROR_NODE_EXECUTION_FAILURE}
)

#: 上传错误码属性名（审批 RESOLVED 后写入 attempt.output 的决策键）。
_DECISION_KEY: Final[str] = "decision"


class RuntimeContractError(ValueError):
    """Runtime 调用契约违反（**编程 / 调用错误**——fail loud，不创建/推进 run）。

    例：workflow_id / project_id 空、input 非法 JSON 或超界、decision 非法。
    业务失败不抛本异常——一律归一为 :class:`RunOutcome`。
    """


@dataclass(frozen=True)
class RunOutcome:
    """Runtime 方法的统一结果（frozen；调用方与 Read 层的消费面）。

    Attributes:
        workflow_run_id: Run 身份。
        outcome:         ``RUN_OUTCOMES`` 之一（白名单强校验）。
        run:             最新 run 视图（NOT_FOUND 时为 None）。
        error_code:      ``WF_ERROR_CODES`` 稳定码（仅失败/未知/冲突/预算类给出）。
        approval_id / approval_status: 等待审批点 / 裁决操作的审批事实。
        agent_run_id / agent_approval_id: 嵌套 Agent 等待点的恢复身份。
    """

    workflow_run_id: str
    outcome: str
    run: RunRecord | None = None
    error_code: str | None = None
    approval_id: str | None = None
    approval_status: str | None = None
    agent_run_id: str | None = None
    agent_approval_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, str) or self.outcome not in RUN_OUTCOMES:
            raise RuntimeContractError(
                f"outcome 非法: {self.outcome!r}（白名单: {sorted(RUN_OUTCOMES)}）"
            )
        if self.error_code is not None and self.error_code not in WF_ERROR_CODES:
            raise RuntimeContractError(
                f"error_code 必须是 WF_ERROR_* 稳定码（当前: {self.error_code!r}）"
            )


def _outcome(
    run: RunRecord,
    outcome: str,
    *,
    error_code: str | None = None,
    approval_id: str | None = None,
    approval_status: str | None = None,
    agent_run_id: str | None = None,
    agent_approval_id: str | None = None,
) -> RunOutcome:
    return RunOutcome(
        workflow_run_id=run.workflow_run_id,
        outcome=outcome,
        run=run,
        error_code=error_code,
        approval_id=approval_id,
        approval_status=approval_status,
        agent_run_id=agent_run_id,
        agent_approval_id=agent_approval_id,
    )


def _not_found(workflow_run_id: str, error_code: str) -> RunOutcome:
    return RunOutcome(
        workflow_run_id=workflow_run_id, outcome=OUTCOME_NOT_FOUND,
        error_code=error_code,
    )


#: 单步结果：``(progressed, outcome)``——progressed=True ⇒ outcome 为 None
#: 且调用方必须重读 run 继续 inline；progressed=False ⇒ 返回该 outcome。
_StepResult = tuple[bool, "RunOutcome | None"]


class WorkflowRuntime:
    """Workflow Run 生命周期运行时（无状态；端口全部构造注入）。"""

    def __init__(
        self,
        *,
        run_store: WorkflowRunStore,
        node_run_store: WorkflowNodeRunStore,
        approval_store: WorkflowApprovalStore,
        registry: WorkflowRegistry,
        executor: NodeExecutor,
        policy: WorkflowRuntimePolicy,
        clock: Any,
        orchestrator_provider: OrchestratorProvider,
        request_id_factory: Callable[[], str] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._run_store = run_store
        self._node_run_store = node_run_store
        self._approval_store = approval_store
        self._registry = registry
        self._executor = executor
        self._policy = policy
        self._clock = clock
        self._orchestrator_provider = orchestrator_provider
        self._request_id_factory = request_id_factory or _default_request_id
        self._run_id_factory = run_id_factory or _default_run_id

    # ========================================================
    # 公开入口
    # ========================================================

    async def start(
        self,
        *,
        workflow_id: str,
        project_id: str,
        input: str | None = None,
        request_id: str | None = None,
        workflow_run_id: str | None = None,
    ) -> RunOutcome:
        """启动新 Run 并 inline 推进到终态 / 等待点（ADR §9.2）。

        Args:
            workflow_id:    已注册 workflow name（Registry 解析；服务器端）。
            project_id:     服务器端给定的项目作用域（**不**来自 input）。
            input:          JSON 文本（None = 无输入）；非法 / 超界 ⇒
                            ``RuntimeContractError``（**不创建 run**）。
            request_id:     correlation id（None ⇒ 工厂生成；非幂等键）。
            workflow_run_id:显式指定身份（None ⇒ 工厂生成）。

        Raises:
            RuntimeContractError:     调用契约违反（含 input 校验失败）。
            WorkflowDefinitionError:  定义不存在（启动前置失败——透传域异常）。
        """
        _require_non_empty("workflow_id", workflow_id, RuntimeContractError)
        _require_non_empty("project_id", project_id, RuntimeContractError)
        if request_id is not None:
            _require_non_empty("request_id", request_id, RuntimeContractError)
        if workflow_run_id is not None:
            _require_non_empty(
                "workflow_run_id", workflow_run_id, RuntimeContractError
            )
        input_text = _validated_input_text(input)

        definition = self._registry.resolve_current(workflow_id)
        run_id = workflow_run_id or self._run_id_factory()

        try:
            created = await asyncio.to_thread(
                self._run_store.create,
                workflow_run_id=run_id,
                workflow_id=definition.name,
                workflow_version=definition.version,
                request_id=request_id or self._request_id_factory(),
                project_id=project_id,
                input=input_text,
                status=WORKFLOW_STATUS_PENDING,
                current_node_id=None,
                next_action=NEXT_ACTION_NONE,
            )
        except WorkflowPortsError:
            existing = await self._get_run(run_id)
            if existing is not None:
                # 身份冲突（重复 start 同一 run_id）⇒ 明确冲突 + 现有视图
                return _outcome(
                    existing,
                    OUTCOME_CONFLICT,
                    error_code=WF_ERROR_STATE_CONFLICT,
                )
            raise
        return await self._drive(created.workflow_run_id)

    async def advance(self, workflow_run_id: str) -> RunOutcome:
        """从持久化状态继续 inline 推进（resume 统一入口；幂等可重入）。

        分类（不需要调用方区分“首次 / 恢复”）：
            终态 ⇒ 返回终态；cancel 信号 ⇒ 收尾取消；PENDING ⇒ 启动；
            RUNNING ⇒ 执行 / 接续 / UNKNOWN 判定；WAITING ⇒ 审批 / 嵌套 Agent 恢复。
        """
        _require_non_empty(
            "workflow_run_id", workflow_run_id, RuntimeContractError
        )
        return await self._drive(workflow_run_id)

    async def submit_approval_decision(
        self, *, approval_id: str, decision: str
    ) -> RunOutcome:
        """提交 Workflow Approval 裁决（**与 Run 恢复是不同操作**）。

        语义（ADR §14 H2 / §15 C2）：
            * 单赢（条件 UPDATE）；重复同裁决 ⇒ 幂等返回既有终态；
            * 相反裁决 / 终态后异裁决 ⇒ ``CONFLICT``（APPROVAL_CONFLICT）；
            * run 已终态（含 CANCELLED）⇒ 拒绝裁决（``CONFLICT``/STATE_CONFLICT
              ——cancel 使 approval 失效）；
            * **不**推进 run（恢复由 ``advance`` 承担）。

        Raises:
            RuntimeContractError: decision 非 APPROVED / REJECTED。
        """
        _require_non_empty("approval_id", approval_id, RuntimeContractError)
        if decision not in (APPROVAL_STATUS_APPROVED, APPROVAL_STATUS_REJECTED):
            raise RuntimeContractError(
                "decision 必须是 APPROVED 或 REJECTED"
                f"（当前: {decision!r}）"
            )

        approval = await self._get_approval(approval_id)
        if approval is None:
            return RunOutcome(
                workflow_run_id="",
                outcome=OUTCOME_NOT_FOUND,
                error_code=WF_ERROR_APPROVAL_NOT_FOUND,
            )
        run = await self._get_run(approval.workflow_run_id)
        if run is None:
            return _not_found(approval.workflow_run_id, WF_ERROR_RUN_NOT_FOUND)
        if run.status in WORKFLOW_TERMINAL_STATUSES:
            # cancel 使 approval 失效（C2）；终态 run 不接受新裁决
            return _outcome(
                run, OUTCOME_CONFLICT, error_code=WF_ERROR_STATE_CONFLICT
            )

        try:
            resolved = await asyncio.to_thread(
                self._approval_store.resolve,
                approval_id=approval_id,
                decision=decision,
            )
        except WorkflowPortsError:
            return _outcome(
                run, OUTCOME_CONFLICT, error_code=WF_ERROR_PERSISTENCE_FAILURE
            )

        if resolved is not None:
            return _outcome(
                run,
                OUTCOME_WAITING_APPROVAL,
                approval_id=resolved.approval_id,
                approval_status=resolved.status,
            )

        # 无转换 ⇒ 回读分类（同裁决 ⇒ 幂等；异裁决 / 缺失 ⇒ 冲突 / 未找到）
        fresh = await self._get_approval(approval_id)
        if fresh is None:
            return _outcome(
                run, OUTCOME_NOT_FOUND, error_code=WF_ERROR_APPROVAL_NOT_FOUND
            )
        if fresh.status == decision:
            return _outcome(
                run,
                OUTCOME_WAITING_APPROVAL,
                approval_id=fresh.approval_id,
                approval_status=fresh.status,
            )
        return _outcome(
            run, OUTCOME_CONFLICT, error_code=WF_ERROR_APPROVAL_CONFLICT
        )

    async def request_cancel(self, workflow_run_id: str) -> RunOutcome:
        """协作式取消（置信号 + 传播 + 收尾尝试；三态不二值化——ADR §15）。

        Returns:
            ``CANCELLED``（已确认终结）/ ``CANCEL_REQUESTED``（意图已持久化，
            尚未确认终结）/ ``NOT_CANCELLABLE``（已终态或不符合条件，no-op）。
        """
        _require_non_empty(
            "workflow_run_id", workflow_run_id, RuntimeContractError
        )
        run = await self._get_run(workflow_run_id)
        if run is None:
            return _not_found(workflow_run_id, WF_ERROR_RUN_NOT_FOUND)
        if run.status in WORKFLOW_TERMINAL_STATUSES:
            return _outcome(run, OUTCOME_NOT_CANCELLABLE)

        if not run.cancel_requested:
            try:
                run = await asyncio.to_thread(
                    self._run_store.request_cancel, workflow_run_id
                )
            except RunConflictError as exc:
                fresh = await self._get_run(workflow_run_id)
                if fresh is None:
                    return _not_found(
                        workflow_run_id, WF_ERROR_RUN_NOT_FOUND
                    )
                if fresh.status in WORKFLOW_TERMINAL_STATUSES:
                    return _outcome(fresh, OUTCOME_NOT_CANCELLABLE)
                if not fresh.cancel_requested:
                    # 无法归因（reason=unknown 类）⇒ 明确冲突，不猜测
                    return _outcome(
                        fresh,
                        OUTCOME_CONFLICT,
                        error_code=WF_ERROR_STATE_CONFLICT,
                    )
                run = fresh
            except WorkflowPortsError:
                return _outcome(
                    run,
                    OUTCOME_CONFLICT,
                    error_code=WF_ERROR_PERSISTENCE_FAILURE,
                )

        attempts = await self._list_attempts(workflow_run_id)
        return await self._finalize_cancellation(run, attempts)

    # ========================================================
    # inline 驱动（start / advance 共用）
    # ========================================================

    def _iteration_limit(self) -> int:
        return (
            2 * self._policy.max_nodes_executed
            + self._policy.max_total_attempts
            + 4
        )

    async def _drive(self, workflow_run_id: str) -> RunOutcome:
        for _ in range(self._iteration_limit()):
            run = await self._get_run(workflow_run_id)
            if run is None:
                return _not_found(workflow_run_id, WF_ERROR_RUN_NOT_FOUND)

            if run.status in WORKFLOW_TERMINAL_STATUSES:
                return await self._terminal_outcome(run)
            if run.cancel_requested:
                attempts = await self._list_attempts(workflow_run_id)
                return await self._finalize_cancellation(run, attempts)

            if run.status == WORKFLOW_STATUS_PENDING:
                progressed, outcome = await self._step_start(run)
            elif run.status == WORKFLOW_STATUS_WAITING:
                progressed, outcome = await self._step_waiting(run)
            elif run.status == WORKFLOW_STATUS_RUNNING:
                progressed, outcome = await self._step_running(run)
            else:
                # 未知 run 状态（域白名单外）⇒ fail-closed
                return _outcome(
                    run, OUTCOME_UNKNOWN, error_code=WF_ERROR_STATE_CONFLICT
                )

            if not progressed:
                assert outcome is not None  # 契约：未推进必有结果
                return outcome
            # progressed ⇒ 重读 run 继续 inline

        # 防御护栏（不应到达：预算在 step 内强制）
        return await self._fail_run_fresh(
            workflow_run_id, WF_ERROR_LIMIT_REACHED
        )

    async def _fail_run_fresh(
        self, workflow_run_id: str, error_code: str
    ) -> RunOutcome:
        run = await self._get_run(workflow_run_id)
        if run is None:
            return _not_found(workflow_run_id, WF_ERROR_RUN_NOT_FOUND)
        if run.status in WORKFLOW_TERMINAL_STATUSES:
            return await self._terminal_outcome(run)
        attempts = await self._list_attempts(workflow_run_id)
        _, outcome = await self._fail_run(
            run, error_code=error_code, attempt_count=len(attempts)
        )
        assert outcome is not None
        return outcome

    # ========================================================
    # 单步：PENDING → RUNNING（启动竞争 CAS 单赢）
    # ========================================================

    async def _step_start(self, run: RunRecord) -> _StepResult:
        definition = await self._resolve_definition(run)
        if isinstance(definition, RunOutcome):
            return False, definition
        try:
            await self._cas(
                run,
                status=WORKFLOW_STATUS_RUNNING,
                current_node_id=definition.start_node_id,
                next_action=_NEXT_ACTION_NODE_EXECUTION,
                attempt_count=run.attempt_count,
            )
        except _CasAbort as abort:
            return False, abort.outcome
        return True, None

    # ========================================================
    # 单步：RUNNING（执行 / 接续 / UNKNOWN 判定）
    # ========================================================

    async def _step_running(self, run: RunRecord) -> _StepResult:
        context = await self._node_context(run)
        if isinstance(context, RunOutcome):
            return False, context
        _definition, node, latest, attempts = context

        if latest is None:
            return await self._claim_and_execute(
                run, node, attempt=1, attempts=attempts
            )
        if latest.status == NODE_STATUS_RUNNING:
            # 未决 attempt：执行者可能仍在途或已崩溃 —— 不可区分 ⇒ UNKNOWN
            # fail-closed（ADR §8.3；不重跑、不假设执行者已死）
            return False, _outcome(
                run, OUTCOME_UNKNOWN, error_code=WF_ERROR_NODE_UNKNOWN
            )
        if latest.status == NODE_STATUS_WAITING_APPROVAL:
            # attempt 已入等待、run 却仍 RUNNING（写好 attempt 后 CAS 前崩溃）
            # ⇒ 幂等补写 CAS 到 WAITING（不重复执行）
            return await self._cas_to_waiting(run, node, latest, attempts)
        if latest.status == NODE_STATUS_SUCCEEDED:
            # 结果已持久化、run 未推进（崩溃残留）⇒ 用持久化结果接续推进
            return await self._advance_after_success(run, node, latest, attempts)
        # FAILED：retry 判定 或 run 失败（fail-before-retry 已满足）
        return await self._settle_failed(
            run, node, error_code=latest.error_code, attempts=attempts
        )

    # ========================================================
    # 单步：WAITING（审批 / 嵌套 Agent 恢复——CAS 抢恢复权）
    # ========================================================

    async def _step_waiting(self, run: RunRecord) -> _StepResult:
        context = await self._node_context(run)
        if isinstance(context, RunOutcome):
            return False, context
        _definition, node, latest, attempts = context

        if latest is None or latest.status == NODE_STATUS_RUNNING:
            return False, _outcome(
                run, OUTCOME_UNKNOWN, error_code=WF_ERROR_NODE_UNKNOWN
            )
        if latest.status == NODE_STATUS_FAILED:
            # REJECTED 恢复的残留（attempt 已 FAILED、run CAS 前崩溃）⇒ 接续
            return await self._settle_failed(
                run, node, error_code=latest.error_code, attempts=attempts
            )
        if latest.status == NODE_STATUS_SUCCEEDED:
            return await self._advance_after_success(run, node, latest, attempts)

        # WAITING_APPROVAL
        if node.node_type == NODE_TYPE_APPROVAL:
            return await self._resume_approval(run, node, latest, attempts)
        if node.node_type == NODE_TYPE_AI:
            return await self._resume_agent(run, node, latest, attempts)
        return False, _outcome(
            run, OUTCOME_UNKNOWN, error_code=WF_ERROR_NODE_UNKNOWN
        )

    async def _resume_approval(
        self,
        run: RunRecord,
        node: WorkflowNode,
        latest: NodeRunRecord,
        attempts: tuple[NodeRunRecord, ...],
    ) -> _StepResult:
        approval = await self._get_approval_for_node(
            workflow_run_id=run.workflow_run_id, node_id=node.node_id
        )
        if approval is None:
            return False, _outcome(
                run, OUTCOME_UNKNOWN, error_code=WF_ERROR_APPROVAL_NOT_FOUND
            )
        if approval.status == APPROVAL_STATUS_PENDING:
            # 仍等待（幂等返回；不自动裁决、不推进）
            return False, _outcome(
                run,
                OUTCOME_WAITING_APPROVAL,
                approval_id=approval.approval_id,
                approval_status=approval.status,
            )

        # 已裁决 ⇒ 抢恢复权（WAITING → RUNNING CAS 单赢）
        try:
            updated = await self._cas(
                run,
                status=WORKFLOW_STATUS_RUNNING,
                current_node_id=node.node_id,
                next_action=_NEXT_ACTION_NODE_EXECUTION,
                attempt_count=len(attempts),
            )
        except _CasAbort as abort:
            return False, abort.outcome

        if approval.status == APPROVAL_STATUS_APPROVED:
            output = serialize_json_value(
                {
                    "approval_id": approval.approval_id,
                    _DECISION_KEY: APPROVAL_STATUS_APPROVED,
                }
            )
            try:
                finished = await asyncio.to_thread(
                    self._node_run_store.update,
                    workflow_run_id=run.workflow_run_id,
                    node_id=node.node_id,
                    attempt=latest.attempt,
                    status=NODE_STATUS_SUCCEEDED,
                    output=output,
                    completed_at=self._clock.now(),
                )
            except WorkflowPortsError:
                return False, _outcome(
                    updated,
                    OUTCOME_UNKNOWN,
                    error_code=WF_ERROR_PERSISTENCE_FAILURE,
                )
            return await self._advance_after_success(
                updated, node, finished, attempts
            )

        # REJECTED ⇒ node FAILED → run FAILED（第一阶段 on_reject = fail run）
        try:
            await asyncio.to_thread(
                self._node_run_store.update,
                workflow_run_id=run.workflow_run_id,
                node_id=node.node_id,
                attempt=latest.attempt,
                status=NODE_STATUS_FAILED,
                error_code=WF_ERROR_APPROVAL_REJECTED,
                completed_at=self._clock.now(),
            )
        except WorkflowPortsError:
            return False, _outcome(
                updated,
                OUTCOME_UNKNOWN,
                error_code=WF_ERROR_PERSISTENCE_FAILURE,
            )
        return await self._settle_failed(
            updated,
            node,
            error_code=WF_ERROR_APPROVAL_REJECTED,
            attempts=attempts,
            allow_retry=False,
        )

    async def _resume_agent(
        self,
        run: RunRecord,
        node: WorkflowNode,
        latest: NodeRunRecord,
        attempts: tuple[NodeRunRecord, ...],
    ) -> _StepResult:
        agent_run_id = _agent_run_id_from_output(latest.output)
        if agent_run_id is None:
            # WAITING 但恢复键缺失（数据损坏）⇒ fail-closed
            return False, _outcome(
                run, OUTCOME_UNKNOWN, error_code=WF_ERROR_NODE_UNKNOWN
            )

        # 抢恢复权（WAITING → RUNNING CAS 单赢）
        try:
            updated = await self._cas(
                run,
                status=WORKFLOW_STATUS_RUNNING,
                current_node_id=node.node_id,
                next_action=_NEXT_ACTION_NODE_EXECUTION,
                attempt_count=len(attempts),
            )
        except _CasAbort as abort:
            return False, abort.outcome

        try:
            question = render_template(
                node.question_template,
                input=run.input,
                previous=_previous_output(attempts),
            )
        except Exception:
            return await self._fail_waiting_attempt(
                updated, node, latest, attempts, WF_ERROR_NODE_EXECUTION_FAILURE
            )

        port = self._orchestrator_provider(updated.project_id)
        try:
            outcome = await port.resume_agent_run(agent_run_id, question=question)
        except Exception:
            return await self._fail_waiting_attempt(
                updated, node, latest, attempts, WF_ERROR_NODE_EXECUTION_FAILURE
            )

        if outcome.agent_status == AGENT_STATUS_WAITING_APPROVAL:
            if not outcome.agent_run_id:
                return False, _outcome(
                    updated, OUTCOME_UNKNOWN, error_code=WF_ERROR_NODE_UNKNOWN
                )
            try:
                output = serialize_json_value(
                    {
                        "agent_run_id": outcome.agent_run_id,
                        "agent_approval_id": outcome.agent_approval_id,
                    }
                )
            except WorkflowOutputError:
                return False, _outcome(
                    updated,
                    OUTCOME_UNKNOWN,
                    error_code=WF_ERROR_PERSISTENCE_FAILURE,
                )
            try:
                await asyncio.to_thread(
                    self._node_run_store.update,
                    workflow_run_id=run.workflow_run_id,
                    node_id=node.node_id,
                    attempt=latest.attempt,
                    status=NODE_STATUS_WAITING_APPROVAL,
                    output=output,
                )
            except WorkflowPortsError:
                return False, _outcome(
                    updated,
                    OUTCOME_UNKNOWN,
                    error_code=WF_ERROR_PERSISTENCE_FAILURE,
                )
            # 回到 WAITING（幂等；CAS 失败 ⇒ 重读分类）
            try:
                back = await self._cas(
                    updated,
                    status=WORKFLOW_STATUS_WAITING,
                    current_node_id=node.node_id,
                    next_action=_NEXT_ACTION_WAITING_APPROVAL,
                    attempt_count=len(attempts),
                )
            except _CasAbort as abort:
                return False, abort.outcome
            return False, _outcome(
                back,
                OUTCOME_WAITING_APPROVAL,
                agent_run_id=outcome.agent_run_id,
                agent_approval_id=outcome.agent_approval_id,
            )

        # 子 Agent 完成 ⇒ AI node SUCCEEDED（OQ-4 投影）⇒ 接续推进
        try:
            output = serialize_json_value(
                {"route": outcome.route, "content": outcome.content}
            )
        except WorkflowOutputError:
            return False, _outcome(
                updated,
                OUTCOME_UNKNOWN,
                error_code=WF_ERROR_PERSISTENCE_FAILURE,
            )
        try:
            finished = await asyncio.to_thread(
                self._node_run_store.update,
                workflow_run_id=run.workflow_run_id,
                node_id=node.node_id,
                attempt=latest.attempt,
                status=NODE_STATUS_SUCCEEDED,
                output=output,
                completed_at=self._clock.now(),
            )
        except WorkflowPortsError:
            return False, _outcome(
                updated,
                OUTCOME_UNKNOWN,
                error_code=WF_ERROR_PERSISTENCE_FAILURE,
            )
        return await self._advance_after_success(updated, node, finished, attempts)

    async def _fail_waiting_attempt(
        self,
        run: RunRecord,
        node: WorkflowNode,
        latest: NodeRunRecord,
        attempts: tuple[NodeRunRecord, ...],
        error_code: str,
    ) -> _StepResult:
        """恢复调用失败 ⇒ WAITING attempt → FAILED ⇒ retry 判定 / run FAILED。"""
        try:
            await asyncio.to_thread(
                self._node_run_store.update,
                workflow_run_id=run.workflow_run_id,
                node_id=node.node_id,
                attempt=latest.attempt,
                status=NODE_STATUS_FAILED,
                error_code=error_code,
                completed_at=self._clock.now(),
            )
        except WorkflowPortsError:
            return False, _outcome(
                run, OUTCOME_UNKNOWN, error_code=WF_ERROR_PERSISTENCE_FAILURE
            )
        return await self._settle_failed(
            run, node, error_code=error_code, attempts=attempts
        )

    # ========================================================
    # 执行权声明 + 外部调用（唯一外部调用点）
    # ========================================================

    async def _claim_and_execute(
        self,
        run: RunRecord,
        node: WorkflowNode,
        *,
        attempt: int,
        attempts: tuple[NodeRunRecord, ...],
    ) -> _StepResult:
        # ---- 预算（P6 explicit budget）----
        if len(attempts) >= self._policy.max_total_attempts:
            return await self._fail_run(
                run,
                error_code=WF_ERROR_LIMIT_REACHED,
                attempt_count=len(attempts),
            )
        executed_nodes = {record.node_id for record in attempts}
        if (
            node.node_id not in executed_nodes
            and len(executed_nodes) >= self._policy.max_nodes_executed
        ):
            return await self._fail_run(
                run,
                error_code=WF_ERROR_LIMIT_REACHED,
                attempt_count=len(attempts),
            )

        # ---- 执行权原子声明（D-D：必须先于任何外部调用）----
        try:
            record = await asyncio.to_thread(
                self._node_run_store.create,
                workflow_run_id=run.workflow_run_id,
                node_id=node.node_id,
                attempt=attempt,
                node_type=node.node_type,
                status=NODE_STATUS_RUNNING,
                started_at=self._clock.now(),
            )
        except NodeRunAlreadyExistsError:
            # 竞争者已声明（或历史未决）⇒ UNKNOWN fail-closed：
            # **绝不**调用外部执行器、**绝不**同 attempt 重试
            return False, _outcome(
                run, OUTCOME_UNKNOWN, error_code=WF_ERROR_NODE_UNKNOWN
            )
        except WorkflowPortsError:
            return False, _outcome(
                run, OUTCOME_CONFLICT, error_code=WF_ERROR_PERSISTENCE_FAILURE
            )

        # ---- 唯一外部调用（执行权已到手；失败竞争者到不了这里）----
        attempts_now = attempts + (record,)
        started = _time.perf_counter()
        execution = await self._executor.execute_node(
            node,
            workflow_run_id=run.workflow_run_id,
            project_id=run.project_id,
            request_id=run.request_id,
            input=run.input,
            previous=_previous_output(attempts),
            attempt=attempt,
        )
        elapsed_ms = (_time.perf_counter() - started) * 1000.0

        return await self._persist_outcome(
            run, node, record, execution, elapsed_ms, attempts_now
        )

    async def _persist_outcome(
        self,
        run: RunRecord,
        node: WorkflowNode,
        record: NodeRunRecord,
        execution: NodeExecutionOutcome,
        elapsed_ms: float,
        attempts: tuple[NodeRunRecord, ...],
    ) -> _StepResult:
        if execution.status == NODE_STATUS_SUCCEEDED:
            try:
                finished = await asyncio.to_thread(
                    self._node_run_store.update,
                    workflow_run_id=run.workflow_run_id,
                    node_id=node.node_id,
                    attempt=record.attempt,
                    status=NODE_STATUS_SUCCEEDED,
                    output=execution.output,
                    elapsed_ms=elapsed_ms,
                    completed_at=self._clock.now(),
                )
            except WorkflowPortsError:
                # 外部执行成功但结果写入失败：**不重跑外部**（UNKNOWN / 人工边界）
                return False, _outcome(
                    run,
                    OUTCOME_UNKNOWN,
                    error_code=WF_ERROR_PERSISTENCE_FAILURE,
                )
            return await self._advance_after_success(
                run, node, finished, attempts
            )

        if execution.status == NODE_STATUS_WAITING_APPROVAL:
            try:
                waited = await asyncio.to_thread(
                    self._node_run_store.update,
                    workflow_run_id=run.workflow_run_id,
                    node_id=node.node_id,
                    attempt=record.attempt,
                    status=NODE_STATUS_WAITING_APPROVAL,
                    output=execution.output,
                    elapsed_ms=elapsed_ms,
                )
            except WorkflowPortsError:
                return False, _outcome(
                    run,
                    OUTCOME_UNKNOWN,
                    error_code=WF_ERROR_PERSISTENCE_FAILURE,
                )
            return await self._cas_to_waiting(
                run,
                node,
                waited,
                attempts,
                approval_id=execution.approval_id,
                approval_status=(
                    APPROVAL_STATUS_PENDING
                    if execution.approval_id is not None
                    else None
                ),
                agent_run_id=execution.agent_run_id,
                agent_approval_id=execution.agent_approval_id,
            )

        # FAILED（fail-before-retry：失败事实先持久化，再判定重试）
        try:
            await asyncio.to_thread(
                self._node_run_store.update,
                workflow_run_id=run.workflow_run_id,
                node_id=node.node_id,
                attempt=record.attempt,
                status=NODE_STATUS_FAILED,
                error_code=execution.error_code,
                elapsed_ms=elapsed_ms,
                completed_at=self._clock.now(),
            )
        except WorkflowPortsError:
            return False, _outcome(
                run, OUTCOME_UNKNOWN, error_code=WF_ERROR_PERSISTENCE_FAILURE
            )
        return await self._settle_failed(
            run, node, error_code=execution.error_code, attempts=attempts
        )

    # ========================================================
    # 结果推进（SUCCEEDED ⇒ 下一节点 / COMPLETED）
    # ========================================================

    async def _advance_after_success(
        self,
        run: RunRecord,
        node: WorkflowNode,
        finished: NodeRunRecord,
        attempts: tuple[NodeRunRecord, ...],
    ) -> _StepResult:
        target = await self._resolve_successor(run, node, finished)
        if isinstance(target, RunOutcome):
            return False, target

        if target is None:
            try:
                updated = await self._cas(
                    run,
                    status=WORKFLOW_STATUS_COMPLETED,
                    current_node_id=node.node_id,
                    next_action=_NEXT_ACTION_COMPLETED,
                    attempt_count=len(attempts),
                )
            except _CasAbort as abort:
                return False, abort.outcome
            return False, _outcome(updated, OUTCOME_COMPLETED)

        try:
            await self._cas(
                run,
                status=WORKFLOW_STATUS_RUNNING,
                current_node_id=target,
                next_action=_NEXT_ACTION_NODE_EXECUTION,
                attempt_count=len(attempts),
            )
        except _CasAbort as abort:
            return False, abort.outcome
        return True, None  # inline：继续推进 target

    async def _resolve_successor(
        self, run: RunRecord, node: WorkflowNode, finished: NodeRunRecord
    ) -> "str | None | RunOutcome":
        """计算下一节点（None = 完成）；返回 RunOutcome 表示明确失败。"""
        if node.node_type == NODE_TYPE_CONDITION:
            branch = _parse_condition_output(finished.output)
            if branch is None:
                _, outcome = await self._fail_run(
                    run,
                    error_code=WF_ERROR_NODE_EXECUTION_FAILURE,
                    attempt_count=len(
                        await self._list_attempts(run.workflow_run_id)
                    ),
                )
                assert outcome is not None
                return outcome
            target = node.on_true_node_id if branch else node.on_false_node_id
        else:
            target = node.next_node_id

        if target is not None:
            definition = await self._resolve_definition(run)
            if isinstance(definition, RunOutcome):
                return definition
            try:
                definition.node(target)
            except WorkflowDefinitionError:
                # 无效目标：不静默跳过（定义与持久化状态不一致）⇒ run FAILED
                _, outcome = await self._fail_run(
                    run,
                    error_code=WF_ERROR_NODE_UNKNOWN,
                    attempt_count=len(
                        await self._list_attempts(run.workflow_run_id)
                    ),
                )
                assert outcome is not None
                return outcome
        return target

    # ========================================================
    # 失败结算（retry 判定 · fail-before-retry）
    # ========================================================

    async def _settle_failed(
        self,
        run: RunRecord,
        node: WorkflowNode,
        *,
        error_code: str | None,
        attempts: tuple[NodeRunRecord, ...],
        allow_retry: bool = True,
    ) -> _StepResult:
        if error_code is None:
            # FAILED attempt 必须携带稳定码（S1/DB 约束）；缺失 ⇒ fail-closed
            return await self._fail_run(
                run,
                error_code=WF_ERROR_NODE_EXECUTION_FAILURE,
                attempt_count=len(attempts),
            )

        used_attempts = sum(
            1 for record in attempts if record.node_id == node.node_id
        )
        retryable = (
            allow_retry
            and node.node_type in (NODE_TYPE_AI, NODE_TYPE_TOOL)
            and node.retry_policy is not None
            and used_attempts < node.retry_policy.max_attempts
            and error_code in _RETRYABLE_ERROR_CODES
            and run.status not in WORKFLOW_TERMINAL_STATUSES
            and not run.cancel_requested
        )
        if retryable:
            # R1：retry = 新 attempt（历史保留）；编号 = 同 node 已用 + 1
            # （并发竞争者算出同一编号 ⇒ 唯一约束单赢——见模块 docstring）
            return await self._claim_and_execute(
                run, node, attempt=used_attempts + 1, attempts=attempts
            )
        return await self._fail_run(
            run, error_code=error_code, attempt_count=len(attempts)
        )

    # ========================================================
    # run 级写入（CAS 唯一通道）
    # ========================================================

    async def _cas(
        self,
        run: RunRecord,
        *,
        status: str,
        current_node_id: str | None,
        next_action: str,
        attempt_count: int,
    ) -> RunRecord:
        """单条 CAS；未命中 ⇒ ``_CasAbort``（已重读分类——零自动重试）。"""
        try:
            return await asyncio.to_thread(
                self._run_store.update_cas,
                workflow_run_id=run.workflow_run_id,
                expected_state_version=run.state_version,
                status=status,
                current_node_id=current_node_id,
                next_action=next_action,
                attempt_count=attempt_count,
            )
        except RunConflictError:
            raise _CasAbort(await self._classify_after_conflict(run))
        except RunNotFoundError:
            raise _CasAbort(
                _not_found(run.workflow_run_id, WF_ERROR_RUN_NOT_FOUND)
            )
        except WorkflowPortsError:
            raise _CasAbort(
                _outcome(
                    run,
                    OUTCOME_UNKNOWN,
                    error_code=WF_ERROR_PERSISTENCE_FAILURE,
                )
            )

    async def _fail_run(
        self, run: RunRecord, *, error_code: str, attempt_count: int
    ) -> _StepResult:
        try:
            updated = await self._cas(
                run,
                status=WORKFLOW_STATUS_FAILED,
                current_node_id=run.current_node_id,
                next_action=_NEXT_ACTION_FAILED,
                attempt_count=attempt_count,
            )
        except _CasAbort as abort:
            return False, abort.outcome
        return False, _outcome(updated, OUTCOME_FAILED, error_code=error_code)

    async def _cas_to_waiting(
        self,
        run: RunRecord,
        node: WorkflowNode,
        waited: NodeRunRecord,
        attempts: tuple[NodeRunRecord, ...],
        *,
        approval_id: str | None = None,
        approval_status: str | None = None,
        agent_run_id: str | None = None,
        agent_approval_id: str | None = None,
    ) -> _StepResult:
        try:
            updated = await self._cas(
                run,
                status=WORKFLOW_STATUS_WAITING,
                current_node_id=node.node_id,
                next_action=_NEXT_ACTION_WAITING_APPROVAL,
                attempt_count=len(attempts),
            )
        except _CasAbort as abort:
            return False, abort.outcome
        return False, _outcome(
            updated,
            OUTCOME_WAITING_APPROVAL,
            approval_id=approval_id,
            approval_status=approval_status,
            agent_run_id=agent_run_id,
            agent_approval_id=agent_approval_id,
        )

    # ========================================================
    # 取消收尾（信号已置位后）
    # ========================================================

    async def _finalize_cancellation(
        self, run: RunRecord, attempts: tuple[NodeRunRecord, ...]
    ) -> RunOutcome:
        # C2 传播（best-effort；不假设任何副作用被撤销——D-E）：
        # 最新 AI 等待 attempt（若存在）⇒ 协作式 cancel_agent_run
        agent_run_id = _waiting_agent_run_id(attempts)
        if agent_run_id is not None:
            try:
                port = self._orchestrator_provider(run.project_id)
                await port.cancel_agent_run(agent_run_id)
            except Exception:
                logger.warning(
                    "cancel_agent_run 传播失败（不阻断 run 收尾）: %s",
                    run.workflow_run_id,
                )

        try:
            updated = await self._cas(
                run,
                status=WORKFLOW_STATUS_CANCELLED,
                current_node_id=run.current_node_id,
                next_action=_NEXT_ACTION_CANCELLED,
                attempt_count=len(attempts),
            )
        except _CasAbort as abort:
            fresh = abort.outcome
            if fresh.outcome == OUTCOME_CANCELLED:
                return fresh
            if fresh.outcome in (
                OUTCOME_COMPLETED,
                OUTCOME_FAILED,
                OUTCOME_NOT_CANCELLABLE,
            ):
                # 先完成即完成（C3）：终态不可逆 ⇒ 取消未生效
                if fresh.run is not None:
                    return _outcome(fresh.run, OUTCOME_NOT_CANCELLABLE)
                return fresh
            # 非终态竞争（他人推进中）⇒ 意图已持久化、未确认终结
            if fresh.run is not None:
                return _outcome(fresh.run, OUTCOME_CANCEL_REQUESTED)
            return fresh
        return _outcome(updated, OUTCOME_CANCELLED)

    # ========================================================
    # 读取 / 分类辅助
    # ========================================================

    async def _get_run(self, workflow_run_id: str) -> RunRecord | None:
        return await asyncio.to_thread(self._run_store.get, workflow_run_id)

    async def _list_attempts(
        self, workflow_run_id: str
    ) -> tuple[NodeRunRecord, ...]:
        return await asyncio.to_thread(
            self._node_run_store.list_for_run, workflow_run_id
        )

    async def _get_approval(self, approval_id: str) -> ApprovalRecord | None:
        return await asyncio.to_thread(self._approval_store.get, approval_id)

    async def _get_approval_for_node(
        self, *, workflow_run_id: str, node_id: str
    ) -> ApprovalRecord | None:
        return await asyncio.to_thread(
            self._approval_store.get_for_node,
            workflow_run_id=workflow_run_id,
            node_id=node_id,
        )

    async def _resolve_definition(
        self, run: RunRecord
    ) -> "WorkflowDefinition | RunOutcome":
        try:
            return self._registry.resolve(
                run.workflow_id, run.workflow_version
            )
        except WorkflowDefinitionError:
            # 修正 4：Resume 按持久化 version 解析；不一致 ⇒ 明确报告
            return _outcome(
                run,
                OUTCOME_DEFINITION_MISMATCH,
                error_code=WF_ERROR_DEFINITION_VERSION_MISMATCH,
            )

    async def _node_context(
        self, run: RunRecord
    ) -> "tuple[WorkflowDefinition, WorkflowNode, NodeRunRecord | None, tuple[NodeRunRecord, ...]] | RunOutcome":
        definition = await self._resolve_definition(run)
        if isinstance(definition, RunOutcome):
            return definition
        if run.current_node_id is None:
            _, outcome = await self._fail_run(
                run,
                error_code=WF_ERROR_NODE_UNKNOWN,
                attempt_count=len(
                    await self._list_attempts(run.workflow_run_id)
                ),
            )
            assert outcome is not None
            return outcome
        try:
            node = definition.node(run.current_node_id)
        except WorkflowDefinitionError:
            _, outcome = await self._fail_run(
                run,
                error_code=WF_ERROR_NODE_UNKNOWN,
                attempt_count=len(
                    await self._list_attempts(run.workflow_run_id)
                ),
            )
            assert outcome is not None
            return outcome
        attempts = await self._list_attempts(run.workflow_run_id)
        return definition, node, _latest_attempt(attempts, node.node_id), attempts

    async def _classify_after_conflict(self, run: RunRecord) -> RunOutcome:
        """CAS 失败 ⇒ **重读 + 分类**（零自动重试——ADR §9.2 / 用户 S4 §3）。"""
        fresh = await self._get_run(run.workflow_run_id)
        if fresh is None:
            return _not_found(run.workflow_run_id, WF_ERROR_RUN_NOT_FOUND)
        if fresh.status in WORKFLOW_TERMINAL_STATUSES:
            return await self._terminal_outcome(fresh)
        if fresh.cancel_requested:
            return _outcome(fresh, OUTCOME_CANCEL_REQUESTED)
        if fresh.status == WORKFLOW_STATUS_WAITING:
            approval_id, approval_status = await self._waiting_identity(fresh)
            return _outcome(
                fresh,
                OUTCOME_WAITING_APPROVAL,
                approval_id=approval_id,
                approval_status=approval_status,
            )
        if fresh.status == WORKFLOW_STATUS_RUNNING:
            # 他人正在推进（或已推进到新节点）⇒ 让位
            return _outcome(fresh, OUTCOME_RUNNING)
        return _outcome(
            fresh, OUTCOME_CONFLICT, error_code=WF_ERROR_STATE_CONFLICT
        )

    async def _waiting_identity(
        self, run: RunRecord
    ) -> tuple[str | None, str | None]:
        """尽力补齐 WAITING 视图的审批身份（失败不影响分类结果）。"""
        if run.current_node_id is None:
            return None, None
        try:
            approval = await self._get_approval_for_node(
                workflow_run_id=run.workflow_run_id,
                node_id=run.current_node_id,
            )
        except WorkflowPortsError:
            return None, None
        if approval is None:
            return None, None
        return approval.approval_id, approval.status

    async def _terminal_outcome(self, run: RunRecord) -> RunOutcome:
        if run.status == WORKFLOW_STATUS_CANCELLED:
            return _outcome(run, OUTCOME_CANCELLED)
        if run.status == WORKFLOW_STATUS_COMPLETED:
            return _outcome(run, OUTCOME_COMPLETED)
        # FAILED：best-effort 从持久化 attempt 取失败码（诊断增强；不阻断）
        error_code: str | None = None
        try:
            for record in reversed(await self._list_attempts(run.workflow_run_id)):
                if record.status == NODE_STATUS_FAILED and record.error_code:
                    error_code = record.error_code
                    break
        except WorkflowPortsError:
            error_code = None
        return _outcome(run, OUTCOME_FAILED, error_code=error_code)


class _CasAbort(Exception):
    """CAS 未命中（内部信号；携带重读分类后的结果——不对外抛出）。"""

    def __init__(self, outcome: RunOutcome) -> None:
        super().__init__(outcome.outcome)
        self.outcome = outcome


# ============================================================
# 纯函数辅助
# ============================================================


def _default_request_id() -> str:
    return f"wf-req-{uuid.uuid4().hex}"


def _default_run_id() -> str:
    return f"wf-{uuid.uuid4().hex}"


def _require_non_empty(
    field: str, value: object, error: type[Exception]
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error(f"{field} 必须是非空 str（当前: {type(value).__name__}）")
    return value


def _validated_input_text(input: str | None) -> str | None:
    """D1：input = JSON 文本（None = 无输入）；非法 / 超界 ⇒ 拒绝（不创建 run）。"""
    if input is None:
        return None
    if not isinstance(input, str) or not input.strip():
        raise RuntimeContractError(
            f"input 必须是 JSON 文本或 None（当前: {type(input).__name__}）"
        )
    try:
        parsed = json.loads(input)
    except (json.JSONDecodeError, ValueError):
        raise RuntimeContractError("input 不是合法 JSON 文本") from None
    try:
        validate_json_value(parsed)
        return serialize_json_value(parsed)
    except WorkflowOutputError:
        raise RuntimeContractError(
            "input 超过 JSON 安全边界（大小 / 深度；fail-closed 不截断）"
        ) from None


def _latest_attempt(
    attempts: tuple[NodeRunRecord, ...], node_id: str
) -> NodeRunRecord | None:
    """该 node 的最新 attempt（列表序 = 写入序 ⇒ 最后一个匹配即最新）。"""
    latest: NodeRunRecord | None = None
    for record in attempts:
        if record.node_id == node_id:
            latest = record
    return latest


def _previous_output(attempts: tuple[NodeRunRecord, ...]) -> str | None:
    """previous = 写入序中最后一个 SUCCEEDED attempt 的 output（实际执行路径上游）。"""
    for record in reversed(attempts):
        if record.status == NODE_STATUS_SUCCEEDED and record.output is not None:
            return record.output
    return None


def _parse_condition_output(output: str | None) -> bool | None:
    """解析 CONDITION 结果（S3 输出 "true"/"false"）；非法 ⇒ None。"""
    if output is None:
        return None
    try:
        parsed = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(parsed, bool):
        return parsed
    return None


def _agent_run_id_from_output(output: str | None) -> str | None:
    """从 AI WAITING attempt 的 output 提取恢复键（缺失 / 损坏 ⇒ None）。"""
    if output is None:
        return None
    try:
        parsed = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    value = parsed.get("agent_run_id")
    if isinstance(value, str) and value.strip():
        return value
    return None


def _waiting_agent_run_id(attempts: tuple[NodeRunRecord, ...]) -> str | None:
    """取消传播用：最新 AI 等待 attempt 的 agent_run_id（无 ⇒ None）。"""
    for record in reversed(attempts):
        if record.status != NODE_STATUS_WAITING_APPROVAL:
            continue
        if record.node_type != NODE_TYPE_AI:
            continue
        return _agent_run_id_from_output(record.output)
    return None
