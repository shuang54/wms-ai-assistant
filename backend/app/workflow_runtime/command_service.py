"""Workflow 命令服务（Phase 7 Step 1）—— HTTP 命令面与既有 Runtime 之间的最小适配层。

```text
HTTP Request（认证后）
    ↓  WorkflowCommandScope.project_id（服务端唯一作用域）
WorkflowCommandService（本模块）
    ↓  ① 归属校验（经既有 WorkflowReadService —— 同项目过滤；
    ↓     Run / Approval 不存在 **或** 不属于授权项目 ⇒ 同一 NotFound（404 同构））
    ↓  ② 命令执行（**仅**调用既有 WorkflowRuntime 公开方法：
    ↓     start / advance / submit_approval_decision / request_cancel）
    ↓  ③ 结果读回（read_service.get_run_detail —— 复用只读 DTO 字段边界）
HTTP Response（API 层显式映射；不在本模块返回 HTTP 细节）
```

边界（本步只适配、不重造）：

    * **不**修改 Runtime / Domain / Persistence / Read Service 的业务语义；
    * **不**新增数据库表 / **不**改 schema / **不**引入新认证机制；
    * 命令语义 = 既有 Runtime 契约（含 CAS 单赢、终态不可覆盖、UNKNOWN
      不重跑、取消三态、审批单赢）——本模块只做三件事：
        1) 项目归属前置校验（跨项目命令在任何执行前即 404 同构）；
        2) 审批两段式组合（decision → 成功后 advance 恢复执行；
           decision 失败（CONFLICT / NOT_FOUND）**不**推进）；
        3) 结果读回（RunDetail；只在授权作用域内读取）。
    * approve/reject 组合语义：裁决成功后推进 run——reject ⇒ run FAILED、
      approve ⇒ 恢复执行到终态 / 下一等待点；
    * 组合的两次 runtime 调用各自独立事务（既有 Repository 的
      ``with factory() as session, session.begin()`` 边界，本模块不介入）；
      裁决成功而推进失败时：裁决事实已持久化、run 仍 WAITING——
      可重试 advance 恢复，**无**部分状态不一致；
    * Runtime 契约违反（``RuntimeContractError``，如 input JSON 非法）⇒
      :class:`WorkflowCommandInputError`（API 层映射 400，不伪 500）；
    * 本模块**不**吞异常：域异常（定义不存在 / 版本不匹配）透传给 API 层
      显式映射。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from backend.app.workflow.definition import WorkflowDefinition, WorkflowRegistry
from backend.app.workflow_runtime.factory import create_workflow_runtime
from backend.app.workflow_runtime.read_factory import (
    create_workflow_read_service,
)
from backend.app.workflow_runtime.read_service import (
    ApprovalRecord,
    RunDetail,
    WorkflowReadNotFoundError,
    WorkflowReadService,
)
from backend.app.workflow_runtime.runtime import (
    OUTCOME_NOT_FOUND,
    OUTCOME_WAITING_APPROVAL,
    RunOutcome,
    RuntimeContractError,
    WorkflowRuntime,
)

__all__ = [
    "CommandResult",
    "WorkflowCommandInputError",
    "WorkflowCommandService",
    "create_workflow_command_service",
    "get_workflow_command_runtime",
    "get_workflow_registry",
    "register_workflow_definition",
]


class WorkflowCommandInputError(ValueError):
    """命令输入违反 Runtime 契约（如 input JSON 非法）——API 层映射 400。"""


@dataclass(frozen=True)
class CommandResult:
    """命令结果：runtime 事实 + （授权作用域内的）run 只读视图。"""

    outcome: RunOutcome
    run_detail: RunDetail | None


# ============================================================
# Composition Roots（测试以 monkeypatch 替换）
# ============================================================


_REGISTRY = WorkflowRegistry()

#: 生产目录注册状态（惰性 + 幂等 + 线程安全；见 :func:`_ensure_default_definitions`）。
_DEFAULTS_LOCK = threading.Lock()
_DEFAULTS_REGISTERED = False


def _ensure_default_definitions() -> None:
    """把**生产定义目录**注册进进程内注册表（惰性；幂等；线程安全）。

    Phase 7 Step 7：``backend.app.workflow.catalog`` 是服务器端静态定义的
    唯一事实来源；此前生产注册表为空 ⇒ 任何 start 都是 404。本函数只在
    首次访问 Composition Root 时注册（不重复注册 / 不隐式替换：已存在
    同名定义 ⇒ 跳过，注册语义仍由 :meth:`WorkflowRegistry.register` 保持
    fail-closed）。
    """
    global _DEFAULTS_REGISTERED
    if _DEFAULTS_REGISTERED:
        return
    with _DEFAULTS_LOCK:
        if _DEFAULTS_REGISTERED:
            return
        from backend.app.workflow.catalog import (
            build_default_workflow_definitions,
        )

        for definition in build_default_workflow_definitions():
            if definition.name not in _REGISTRY:
                _REGISTRY.register(definition)
        _DEFAULTS_REGISTERED = True


def get_workflow_registry() -> WorkflowRegistry:
    """项目 Workflow 定义注册表（Composition Root）。

    生产装配：本进程内注册表 + **生产定义目录**（Phase 7 Step 7：惰性 +
    幂等注册，见 :func:`_ensure_default_definitions`）；未注册名称
    ⇒ Runtime 侧 ``WorkflowDefinitionNotFoundError`` ⇒ API 404。
    额外定义可经 :func:`register_workflow_definition` 显式注册
    （重复 name 显式失败，不做隐式替换）。
    """
    _ensure_default_definitions()
    return _REGISTRY


def register_workflow_definition(definition: WorkflowDefinition) -> None:
    """向进程内注册表注册定义（显式；重复 name ⇒ 域异常，不隐式替换）。"""
    _REGISTRY.register(definition)


@lru_cache(maxsize=32)
def get_workflow_command_runtime(project_id: str) -> WorkflowRuntime:
    """按授权项目作用域装配 Runtime（**绑定单一 project_id**；惰性 Session）。

    构造是纯装配（不触 DB、不调外部能力）；首次命令执行才会触达
    Repository / Orchestrator / Tool。测试以 monkeypatch 注入替身。
    """
    return create_workflow_runtime(
        registry=get_workflow_registry(),
        project_id=project_id,
    )


def _default_read_service() -> WorkflowReadService:
    return create_workflow_read_service()


# ============================================================
# 命令服务
# ============================================================


class WorkflowCommandService:
    """命令服务（无状态；runtime / read service 按授权作用域获取）。"""

    def __init__(
        self,
        *,
        runtime_provider: Any | None = None,
        read_service_provider: Any | None = None,
    ) -> None:
        self._runtime_provider = (
            runtime_provider if runtime_provider is not None else get_workflow_command_runtime
        )
        self._read_service_provider = (
            read_service_provider if read_service_provider is not None else _default_read_service
        )

    # ---------------- 内部 ----------------

    def _read_service(self) -> WorkflowReadService:
        return self._read_service_provider()

    def _runtime(self, project_id: str) -> WorkflowRuntime:
        return self._runtime_provider(project_id)

    def _require_run_in_scope(self, workflow_run_id: str, project_id: str) -> RunDetail:
        """归属前置校验：不存在 **或** 不属于授权项目 ⇒ 同一 NotFound（404）。"""
        return self._read_service().get_run_detail(
            workflow_run_id=workflow_run_id, project_id=project_id
        )

    def _require_approval_in_scope(
        self, approval_id: str, project_id: str
    ) -> ApprovalRecord:
        """审批归属前置校验（同 run：NotFound ⇒ 404 同构）。"""
        return self._read_service().get_approval_detail(
            approval_id=approval_id, project_id=project_id
        )

    def _read_run_detail(
        self, workflow_run_id: str, project_id: str
    ) -> RunDetail | None:
        """命令后读回 run 视图；不存在（竞态删除）⇒ None（不伪造）。"""
        try:
            return self._read_service().get_run_detail(
                workflow_run_id=workflow_run_id, project_id=project_id
            )
        except WorkflowReadNotFoundError:
            return None

    @staticmethod
    def _guard_start_contract(exc: RuntimeContractError) -> WorkflowCommandInputError:
        return WorkflowCommandInputError(str(exc))

    # ---------------- 命令 ----------------

    async def start_workflow(
        self,
        *,
        project_id: str,
        workflow_id: str,
        input: str | None = None,
        workflow_run_id: str | None = None,
    ) -> CommandResult:
        """启动工作流（新 Run；inline 推进到终态 / 等待点）。"""
        runtime = self._runtime(project_id)
        try:
            outcome = await runtime.start(
                workflow_id=workflow_id,
                project_id=project_id,
                input=input,
                workflow_run_id=workflow_run_id,
            )
        except RuntimeContractError as exc:
            raise self._guard_start_contract(exc) from exc
        detail = (
            self._read_run_detail(outcome.workflow_run_id, project_id)
            if outcome.outcome != OUTCOME_NOT_FOUND and outcome.workflow_run_id
            else None
        )
        return CommandResult(outcome=outcome, run_detail=detail)

    async def advance_workflow(
        self, *, project_id: str, workflow_run_id: str
    ) -> CommandResult:
        """推进 / 恢复工作流（幂等可重入；终态 ⇒ 返回终态）。"""
        self._require_run_in_scope(workflow_run_id, project_id)
        runtime = self._runtime(project_id)
        try:
            outcome = await runtime.advance(workflow_run_id)
        except RuntimeContractError as exc:
            raise self._guard_start_contract(exc) from exc
        detail = self._read_run_detail(workflow_run_id, project_id)
        return CommandResult(outcome=outcome, run_detail=detail)

    async def cancel_workflow(
        self, *, project_id: str, workflow_run_id: str
    ) -> CommandResult:
        """取消工作流（协作式三态：CANCELLED / CANCEL_REQUESTED /
        NOT_CANCELLABLE；终态 no-op）。"""
        self._require_run_in_scope(workflow_run_id, project_id)
        runtime = self._runtime(project_id)
        try:
            outcome = await runtime.request_cancel(workflow_run_id)
        except RuntimeContractError as exc:
            raise self._guard_start_contract(exc) from exc
        detail = self._read_run_detail(workflow_run_id, project_id)
        return CommandResult(outcome=outcome, run_detail=detail)

    async def decide_approval(
        self,
        *,
        project_id: str,
        approval_id: str,
        decision: str,
    ) -> CommandResult:
        """审批裁决 + 恢复：submit_approval_decision → （成功后）advance。

        两段式（既有 Runtime 契约）：
            * decision 失败（NOT_FOUND / CONFLICT）⇒ **不**推进，直接返回；
            * decision 成功 ⇒ advance 恢复执行（approve ⇒ 继续；reject ⇒
              run 走 FAILED 终态）；advance 结果即最终 outcome。
        裁决成功后即使 advance 失败（异常），裁决事实已持久化、run 仍
        WAITING——重试 advance 即可恢复（无部分状态不一致）。
        """
        self._require_approval_in_scope(approval_id, project_id)
        runtime = self._runtime(project_id)
        try:
            decided = await runtime.submit_approval_decision(
                approval_id=approval_id, decision=decision
            )
        except RuntimeContractError as exc:
            raise self._guard_start_contract(exc) from exc

        if decided.outcome != OUTCOME_WAITING_APPROVAL:
            # NOT_FOUND / CONFLICT：裁决未成功 ⇒ **不**推进（不猜测）
            return CommandResult(outcome=decided, run_detail=None)

        run_id = decided.workflow_run_id or (
            decided.run.workflow_run_id if decided.run is not None else ""
        )
        if not run_id:
            return CommandResult(outcome=decided, run_detail=None)

        resumed = await runtime.advance(run_id)
        # 保留裁决事实（approval_id / approval_status）供响应显式暴露。
        merged = RunOutcome(
            workflow_run_id=resumed.workflow_run_id or run_id,
            outcome=resumed.outcome,
            run=resumed.run,
            error_code=resumed.error_code,
            approval_id=decided.approval_id or resumed.approval_id,
            approval_status=decided.approval_status or resumed.approval_status,
            agent_run_id=resumed.agent_run_id,
            agent_approval_id=resumed.agent_approval_id,
        )
        detail = self._read_run_detail(run_id, project_id)
        return CommandResult(outcome=merged, run_detail=detail)


def create_workflow_command_service() -> WorkflowCommandService:
    """Composition Root：装配命令服务（生产实现；测试可 monkeypatch）。"""
    return WorkflowCommandService()
