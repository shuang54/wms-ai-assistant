"""Phase 7 Step 4 — Agent **命令**服务（start / resume / cancel / approval decision）。

定位（与 Workflow Command API 对称的 Agent 写边界；**只适配、不重造**）：

```text
API 层（agent_command.py；X-API-Key + 服务端项目作用域）
      ↓  授权 project_id（**仅**来自服务端配置）
AgentCommandService（本模块：归属前置校验 + 命令执行 + best-effort 持久化）
      ↓
既有 AgentRuntime（run / resume——**不修改**状态机）
      ↓
既有 AgentCancellationCoordinator（cancel）· AgentApprovalStore（decision）
      ↓
既有 Repository（每操作独立事务；best-effort 语义不变）
```

纪律：

* **不修改** Agent Runtime / Domain / Persistence 业务语义——本服务只做
  HTTP 边界适配（归属校验、结果映射、错误码归一）；
* **归属前置校验**（§四）：run / approval 命令执行前经 ``AgentRunRepository``
  按授权项目过滤；不存在**或**跨项目 ⇒ 同一 ``AGENT_RUN_NOT_FOUND``
  （不泄露存在性）；
* **start 无客户端 project_id**：作用域来自服务端认证配置 ⇒ 经
  composition root **per-project 装配**的 Runtime（allowed_tools /
  ToolExecutionService 均绑定该项目）；run 落库的 ``project_id`` 即
  授权作用域（与 Orchestrator 的 Agent 路径同一 recorder 时序）；
* **持久化 best-effort 不变**：run / step 写入失败只记 warning，
  不改变 Agent 业务结果（与 ``AIOrchestratorService._run_agent`` 同序：
  先 Steps、后 Run；WAITING_APPROVAL ⇒ 不写终态 Run）；
* **fail-closed**：终态不可覆盖（decision 冲突 ⇒ ``AGENT_APPROVAL_CONFLICT``）；
  取消三态不二值化；取消竞争无法定输赢 ⇒ ``AGENT_CANCELLATION_CONFLICT``；
* 响应只含非敏感事实（无 prompt / arguments / content 明细中的敏感数据——
  ``content`` 是 Agent 对授权项目的最终回答，属调用方自有数据）。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from backend.app.agent.approval import (
    AGENT_ERROR_APPROVAL_CONFLICT,
    AGENT_ERROR_APPROVAL_NOT_FOUND,
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_REJECTED,
    approval_conflict_code,
    is_valid_approval_decision,
)
from backend.app.agent.cancellation import (
    AgentCancellationCoordinator,
    AgentCancellationError,
    AgentCancellationResult,
)
from backend.app.agent.errors import (
    AGENT_ERROR_CANCELLATION_CONFLICT,
    AGENT_ERROR_CANCEL_REQUESTED,
    AGENT_ERROR_RUN_NOT_CANCELLABLE,
)
from backend.app.agent.resume import AgentResumeResult
from backend.app.agent.runtime import AgentRuntime
from backend.app.db.agent_run_repository import (
    AgentRunRecord,
    AgentRunRepository,
    AgentRunRepositoryError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AGENT_OUTCOME_COMPLETED",
    "AGENT_OUTCOME_FAILED",
    "AGENT_OUTCOME_LIMIT_REACHED",
    "AGENT_OUTCOME_WAITING_APPROVAL",
    "AGENT_OUTCOME_CANCELLED",
    "AGENT_OUTCOME_CANCEL_REQUESTED",
    "AGENT_OUTCOME_NOT_FOUND",
    "AGENT_OUTCOME_CONFLICT",
    "AGENT_OUTCOME_NOT_CANCELLABLE",
    "AGENT_ERROR_RUN_NOT_FOUND",
    "AgentCommandOutcome",
    "AgentCommandError",
    "AgentCommandService",
]

#: 命令结果 kind（HTTP 语义由 API 层映射；此处只做稳定分类）。
AGENT_OUTCOME_COMPLETED: Final[str] = "COMPLETED"
AGENT_OUTCOME_FAILED: Final[str] = "FAILED"
AGENT_OUTCOME_LIMIT_REACHED: Final[str] = "LIMIT_REACHED"
AGENT_OUTCOME_WAITING_APPROVAL: Final[str] = "WAITING_APPROVAL"
AGENT_OUTCOME_CANCELLED: Final[str] = "CANCELLED"
AGENT_OUTCOME_CANCEL_REQUESTED: Final[str] = "CANCEL_REQUESTED"
AGENT_OUTCOME_NOT_FOUND: Final[str] = "NOT_FOUND"
AGENT_OUTCOME_CONFLICT: Final[str] = "CONFLICT"
AGENT_OUTCOME_NOT_CANCELLABLE: Final[str] = "NOT_CANCELLABLE"

#: 稳定错误码（复用既有域码；不新增码空间）。
AGENT_ERROR_RUN_NOT_FOUND: Final[str] = "AGENT_RUN_NOT_FOUND"

#: 取消结果码 ⇒ 结果 kind（三态不二值化）。
_CANCEL_CODE_TO_KIND: Final[dict[str, str]] = {
    AGENT_ERROR_CANCEL_REQUESTED: AGENT_OUTCOME_CANCEL_REQUESTED,
    "AGENT_CANCELLED": AGENT_OUTCOME_CANCELLED,
    AGENT_ERROR_RUN_NOT_CANCELLABLE: AGENT_OUTCOME_NOT_CANCELLABLE,
    AGENT_ERROR_CANCELLATION_CONFLICT: AGENT_OUTCOME_CONFLICT,
}


@dataclass(frozen=True)
class AgentCommandOutcome:
    """一次命令的结果（非敏感事实；API 层显式映射为 HTTP）。

    Attributes:
        kind:            结果分类（见 ``AGENT_OUTCOME_*``）。
        run_id:          相关 Agent Run 身份（NOT_FOUND 时可为 None）。
        status:          Run / 审批的当前状态（域词汇原值；不映射）。
        error_code:      稳定错误码（成功 ⇒ None）。
        approval_id:     相关审批身份（decision 命令）。
        approval_status: 审批当前状态（decision 命令）。
        decision:        本次裁决（decision 命令；幂等回显）。
        content:         Agent 最终回答（start / resume 成功时；无 ⇒ None）。
        run_view:        轻量 Run 读回（``AgentRunRecord`` 投影；无记录 ⇒ None）。
    """

    kind: str
    run_id: str | None = None
    status: str | None = None
    error_code: str | None = None
    approval_id: str | None = None
    approval_status: str | None = None
    decision: str | None = None
    content: str | None = None
    run_view: AgentRunRecord | None = None


class AgentCommandError(Exception):
    """命令基础设施故障（仓储 / 存储不可用；API 层映射 502）。"""


class AgentCommandService:
    """Agent 命令服务（归属校验 + 命令分发；**无** HTTP / 认证知识）。"""

    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        run_repository: AgentRunRepository,
        checkpoints: Any,
        approval_store: Any,
        run_recorder: Any = None,
        step_recorder: Any = None,
    ) -> None:
        """构造命令服务（全部依赖注入；**不**创建基础设施）。

        Args:
            runtime:        per-project 装配的 AgentRuntime（命令执行面）。
            run_repository: Agent Run 只读仓储（归属前置校验 + 结果读回）。
            checkpoints:    Checkpoint 持久化边界（取消协调点注入）。
            approval_store: 审批存储（decision 命令；None ⇒ 命令 404/冲突
                            由 store 调用期表现——装配默认注入生产实现）。
            run_recorder:   Run 持久化边界（start；best-effort）。
            step_recorder:  Step 持久化边界（start；best-effort）。
        """
        self._runtime = runtime
        self._run_repository = run_repository
        self._checkpoints = checkpoints
        self._approval_store = approval_store
        self._run_recorder = run_recorder
        self._step_recorder = step_recorder

    # ---------- 归属前置校验（§四：不存在 / 跨项目同码） ----------

    def _require_run_in_scope(
        self, run_id: str, project_id: str
    ) -> AgentCommandOutcome | None:
        """Run 必须存在**且**属于授权项目。

        Returns:
            None ⇒ 通过；否则 NOT_FOUND 结果（不存在 / 跨项目**同码**——
            不泄露存在性）。
        """
        try:
            record = self._run_repository.get(run_id)
        except (ValueError, AgentRunRepositoryError) as exc:
            raise AgentCommandError(
                f"Agent Run 归属校验失败: {type(exc).__name__}"
            ) from exc
        if record is None or record.project_id != project_id:
            return AgentCommandOutcome(
                kind=AGENT_OUTCOME_NOT_FOUND,
                error_code=AGENT_ERROR_RUN_NOT_FOUND,
            )
        return None

    # ---------- start ----------

    async def start_agent(
        self,
        *,
        project_id: str,
        question: str,
        user_id: str | None = None,
    ) -> AgentCommandOutcome:
        """启动一次新的 Agent Run（有界循环到终态 / 等待点）。

        持久化时序与 Orchestrator Agent 路径一致（先 Steps、后 Run；
        WAITING_APPROVAL 不写终态 Run；best-effort）。

        Phase 7 Recovery M1：``user_id`` 为**可选**的已认证用户（来自服务端
        会话；由调用方解析后传入——本方法**不**接受任何客户端身份字段）。
        提供时在 Run 落库后登记资源归属（best-effort：失败仅记录错误，
        不阻断 Run 本身；未登记的 Run 在用户上下文读取时 fail-closed 拒绝）。
        """
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question 必须是非空 str")
        request_id = _new_request_id()
        started_at = datetime.now(timezone.utc)
        try:
            result = await self._runtime.run(
                question=question, request_id=request_id
            )
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 归一为命令失败（fail-closed）
            logger.error(
                "agent command start failed", extra={"error_type": type(exc).__name__}
            )
            return AgentCommandOutcome(
                kind=AGENT_OUTCOME_FAILED,
                error_code="AGENT_EXECUTION_FAILED",
            )
        finished_at = datetime.now(timezone.utc)
        await self._persist_run(
            result,
            request_id=request_id,
            project_id=project_id,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=max(
                0.0, (finished_at - started_at).total_seconds() * 1000.0
            ),
        )
        if user_id:
            self._bind_run_ownership(
                run_id=getattr(result, "run_id", None),
                project_id=project_id,
                user_id=user_id,
            )
        return self._from_runtime_result(result)

    def _bind_run_ownership(
        self, *, run_id: str | None, project_id: str, user_id: str
    ) -> None:
        """登记 Agent Run 归属（Phase 7 Recovery M1；best-effort）。

        失败（DB 不可用 / 归属冲突）只记录错误——Run 本身已成功执行，
        且失败后果是 fail-closed（该 Run 在用户上下文不可读），不阻断命令。
        """
        if not run_id:
            return
        from backend.app.services.resource_ownership_service import (
            RESOURCE_TYPE_AGENT_RUN,
            ResourceOwnershipError,
            ResourceOwnershipService,
        )

        try:
            ResourceOwnershipService().bind_owner(
                resource_type=RESOURCE_TYPE_AGENT_RUN,
                resource_id=run_id,
                user_id=user_id,
                project_id=project_id,
            )
        except ResourceOwnershipError:
            logger.error(
                "agent run ownership bind failed",
                extra={"error_type": "ResourceOwnershipError"},
            )

    # ---------- resume ----------

    async def resume_agent(
        self, *, project_id: str, run_id: str, question: str | None
    ) -> AgentCommandOutcome:
        """恢复一个已存在的 Agent Run（TERMINAL ⇒ 历史终态；Replay 不重跑）。"""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空 str")
        rejected = self._require_run_in_scope(run_id, project_id)
        if rejected is not None:
            return rejected
        try:
            result: AgentResumeResult = await self._runtime.resume(
                run_id=run_id, question=question
            )
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "agent command resume failed",
                extra={"error_type": type(exc).__name__},
            )
            return AgentCommandOutcome(
                kind=AGENT_OUTCOME_FAILED,
                run_id=run_id,
                error_code="AGENT_RESUME_FAILED",
            )
        return self._from_resume_result(result, run_id=run_id)

    # ---------- cancel ----------

    async def cancel_agent(self, *, project_id: str, run_id: str) -> AgentCommandOutcome:
        """协作式取消（三态：CANCEL_REQUESTED / CANCELLED / NOT_CANCELLABLE）。"""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 必须是非空 str")
        rejected = self._require_run_in_scope(run_id, project_id)
        if rejected is not None:
            return rejected
        coordinator = AgentCancellationCoordinator(checkpoints=self._checkpoints)
        try:
            result: AgentCancellationResult = await asyncio.to_thread(
                coordinator.cancel, run_id
            )
        except AgentCancellationError as exc:
            raise AgentCommandError(
                f"Agent Run 取消失败: {type(exc).__name__}"
            ) from exc
        kind = _CANCEL_CODE_TO_KIND.get(
            result.code or "", AGENT_OUTCOME_CANCEL_REQUESTED
        )
        return AgentCommandOutcome(
            kind=kind,
            run_id=result.run_id,
            status=result.checkpoint_status,
            error_code=(
                None
                if kind in (AGENT_OUTCOME_CANCELLED, AGENT_OUTCOME_CANCEL_REQUESTED)
                else result.code
            ),
            run_view=self._read_run_view(run_id),
        )

    # ---------- approval decision ----------

    async def decide_approval(
        self, *, project_id: str, approval_id: str, decision: str
    ) -> AgentCommandOutcome:
        """裁决 HITL 审批（APPROVED / REJECTED；幂等；冲突 fail-closed）。

        只改控制状态（**不**调用 LLM / Tool / Resume——执行由 resume 命令承担，
        与 ``AIOrchestratorService.resolve_agent_approval`` 语义一致）。
        """
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValueError("approval_id 必须是非空 str")
        if not is_valid_approval_decision(decision):
            raise ValueError(
                "decision 只接受 APPROVED / REJECTED"
                f"（当前: {decision!r}）"
            )
        record = await self._call_approval(("aget", "get"), approval_id=approval_id)
        if record is None:
            return AgentCommandOutcome(
                kind=AGENT_OUTCOME_NOT_FOUND,
                approval_id=approval_id,
                error_code=AGENT_ERROR_APPROVAL_NOT_FOUND,
            )
        run_id = getattr(record, "run_id", None)
        if not isinstance(run_id, str) or not run_id.strip():
            return AgentCommandOutcome(
                kind=AGENT_OUTCOME_NOT_FOUND,
                approval_id=approval_id,
                error_code=AGENT_ERROR_APPROVAL_NOT_FOUND,
            )
        # 归属：经父 Run 的项目过滤（不存在 / 跨项目同码）
        rejected = self._require_run_in_scope(run_id, project_id)
        if rejected is not None:
            return rejected
        resolved = await self._call_approval(
            ("aresolve", "resolve"), approval_id=approval_id, decision=decision
        )
        if resolved is not None:
            return AgentCommandOutcome(
                kind=AGENT_OUTCOME_COMPLETED,
                run_id=getattr(resolved, "run_id", run_id),
                approval_id=approval_id,
                approval_status=getattr(resolved, "status", None),
                decision=getattr(resolved, "decision", decision),
                run_view=self._read_run_view(run_id),
            )
        # 无转换（条件 UPDATE 未命中）：读回判定"幂等"或"冲突"（绝不 force）
        current = await self._call_approval(("aget", "get"), approval_id=approval_id)
        if current is None:
            return AgentCommandOutcome(
                kind=AGENT_OUTCOME_NOT_FOUND,
                run_id=run_id,
                approval_id=approval_id,
                error_code=AGENT_ERROR_APPROVAL_NOT_FOUND,
            )
        existing = getattr(current, "status", None)
        conflict = approval_conflict_code(existing, decision)
        if conflict is not None:
            return AgentCommandOutcome(
                kind=AGENT_OUTCOME_CONFLICT,
                run_id=run_id,
                approval_id=approval_id,
                approval_status=existing,
                error_code=AGENT_ERROR_APPROVAL_CONFLICT,
                run_view=self._read_run_view(run_id),
            )
        # 幂等（已有状态 == 本次 decision）：返回既有终态，不二次转换
        return AgentCommandOutcome(
            kind=AGENT_OUTCOME_COMPLETED,
            run_id=run_id,
            approval_id=approval_id,
            approval_status=existing,
            decision=decision,
            run_view=self._read_run_view(run_id),
        )

    # ---------- 内部 ----------

    async def _call_approval(
        self, names: tuple[str, str], **kwargs: Any
    ) -> Any | None:
        """调用审批存储的异步入口（``aget``/``aresolve``，否则同步入口进线程）。"""
        async_name, sync_name = names
        call = getattr(self._approval_store, async_name, None)
        if callable(call):
            return await call(**kwargs)
        sync_call = getattr(self._approval_store, sync_name, None)
        if callable(sync_call):
            return await asyncio.to_thread(sync_call, **kwargs)
        return None

    def _read_run_view(self, run_id: str) -> AgentRunRecord | None:
        """轻量 Run 读回（best-effort；失败 ⇒ None，不改变命令结果）。"""
        try:
            return self._run_repository.get(run_id)
        except (ValueError, AgentRunRepositoryError):
            return None

    async def _persist_run(
        self,
        result: Any,
        *,
        request_id: str,
        project_id: str,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: float,
    ) -> None:
        """Run / Step 持久化（best-effort；与 Orchestrator Agent 路径同序）。

        WAITING_APPROVAL ⇒ Run **尚未终态** ⇒ 不写 AgentRun（终态记录留给
        审批后的 resume 路径）——不伪造、不映射成 FAILED。

        Phase 7 Step 5：真实 recorder 提供**同步** ``record`` 与**异步**
        ``arecord``（内部 ``asyncio.to_thread``）两个入口——此处必须
        走 awaitable 语义（优先 ``arecord`` 并 await；仅有同步入口时
        放 worker 线程），否则 coroutine 未 await ⇒ 静默丢失持久化
        （step/run 均不落库；离线 Fake 的同步 ``arecord`` 曾掩盖此缺口）。
        """
        status = getattr(result, "status", None)
        if status == AGENT_OUTCOME_WAITING_APPROVAL:
            return
        if self._step_recorder is not None:
            await self._invoke_recorder(
                self._step_recorder, result, kwargs={}, operation="step"
            )
        if self._run_recorder is not None:
            await self._invoke_recorder(
                self._run_recorder,
                result,
                kwargs={
                    "request_id": request_id,
                    "project_id": project_id,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "duration_ms": duration_ms,
                },
                operation="run",
            )

    @staticmethod
    async def _invoke_recorder(
        recorder: Any,
        result: Any,
        *,
        kwargs: dict[str, Any],
        operation: str,
    ) -> None:
        """调用持久化 recorder（异步入口 await；同步入口放 worker 线程）。

        best-effort：任何异常只 warning——绝不改变命令业务结果。
        """
        try:
            async_call = getattr(recorder, "arecord", None)
            if callable(async_call):
                outcome = async_call(result, **kwargs)
                if inspect.isawaitable(outcome):
                    await outcome
                return
            sync_call = getattr(recorder, "record", None)
            if callable(sync_call):
                await asyncio.to_thread(sync_call, result, **kwargs)
        except Exception as exc:  # noqa: BLE001 —— best-effort
            logger.warning(
                "agent command %s persistence failed" % operation,
                extra={"error_type": type(exc).__name__},
            )

    def _from_runtime_result(self, result: Any) -> AgentCommandOutcome:
        """AgentRuntimeResult ⇒ 命令结果（状态 / 稳定码原值）。"""
        status = getattr(result, "status", None)
        error_code = getattr(result, "error", None)
        kind = {
            "COMPLETED": AGENT_OUTCOME_COMPLETED,
            "FAILED": AGENT_OUTCOME_FAILED,
            "LIMIT_REACHED": AGENT_OUTCOME_LIMIT_REACHED,
            "WAITING_APPROVAL": AGENT_OUTCOME_WAITING_APPROVAL,
        }.get(status or "", AGENT_OUTCOME_FAILED)
        return AgentCommandOutcome(
            kind=kind,
            run_id=getattr(result, "run_id", None),
            status=status,
            error_code=error_code,
            content=getattr(result, "content", None),
        )

    def _from_resume_result(
        self, result: AgentResumeResult, *, run_id: str
    ) -> AgentCommandOutcome:
        """AgentResumeResult ⇒ 命令结果（状态 / 稳定码原值）。"""
        status = getattr(result, "status", None)
        error_code = getattr(result, "error_code", None)
        kind = {
            "COMPLETED": AGENT_OUTCOME_COMPLETED,
            "FAILED": AGENT_OUTCOME_FAILED,
            "LIMIT_REACHED": AGENT_OUTCOME_LIMIT_REACHED,
        }.get(status or "", AGENT_OUTCOME_FAILED)
        return AgentCommandOutcome(
            kind=kind,
            run_id=run_id,
            status=status,
            error_code=error_code,
            content=getattr(result, "content", None),
            run_view=self._read_run_view(run_id),
        )


def _new_request_id() -> str:
    """命令请求 correlation（一次命令一个 ID；不进 LLM / Tool arguments）。"""
    import uuid

    return f"agent-cmd-{uuid.uuid4().hex[:16]}"
