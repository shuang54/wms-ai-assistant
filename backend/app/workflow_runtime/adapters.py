"""Workflow Runtime 端口适配器（Phase 6 Step 5 · S5；装配层）。

把既有基础设施（services）映射到 S1 冻结端口（鸭子类型；**不修改**被适配者）：

```text
OrchestratorPortAdapter  : AIOrchestratorService → OrchestratorPort
ToolExecutionPortAdapter : ToolExecutionService  → ToolExecutionPort
```

纪律（S5 任务书 §六 / §七）：
    * 本模块是**装配层**：允许 import services / agent 只读状态常量；
      Domain / ports / binding / execution / runtime **不** import 本模块；
    * 不改被适配者 / 不旁路权限 / 不重试 / 不吞失败（失败必传播或归一）；
    * 外部异常 ⇒ :class:`WorkflowPortsError`（**只带异常类型名**，不带正文 /
      密钥 / URL / SQL —— 安全边界）；
    * 结果映射白名单：route（``RouteType.value``）/ content / agent 身份与状态；
      ``metadata`` / ``data`` 结构 / Session / 密钥 **不进入** ports DTO；
    * 未知 / 非法状态 ⇒ **fail-closed**（抛错），不猜测、不把失败映射成成功。

映射依据（S5 编码前逐字核对真实代码，非猜测）：
    * ``AIOrchestrationResult(route: RouteType, content: str | None,
      data: Any, metadata: Mapping[str, Any])``（services/ai_orchestrator_service.py）；
    * AGENT 路径：``data = AgentRuntimeResult``（``run_id`` 为该 Agent Run 身份、
      ``approval_id`` 为 WAITING 审批身份）、``metadata["agent_status"]`` ∈
      agent 状态白名单（``AGENT_STATUS_*``）；
    * ``resume_agent_run(run_id, *, question, context=None)`` 签名与 ports 一致；
    * ``ToolExecutionService.execute(tool_name, *, arguments,
      context: ToolExecutionContext | None)``（context.project_id 一致性由
      被适配者 fail-closed 校验；本适配器前置同义校验）；
    * ``ToolResult(tool_name, success, data, error)``（error 文本由 Registry
      归一、非敏感；仅用于分类 / 日志——S3 不落库）。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from backend.app.agent.models import (
    AGENT_STATUS_COMPLETED,
    AGENT_STATUS_WAITING_APPROVAL,
)
from backend.app.services.ai_router_service import RouteType
from backend.app.services.tool_execution_context import ToolExecutionContext
from backend.app.workflow_runtime.ports import (
    OrchestrationOutcome,
    ToolCallContext,
    ToolExecutionOutcome,
    WorkflowPortsError,
)

__all__ = [
    "OrchestratorPortAdapter",
    "ToolExecutionPortAdapter",
]

#: 合法 route 值集（与 Router 白名单同源；未知 ⇒ fail-closed）。
_KNOWN_ROUTES: Final[frozenset[str]] = frozenset(
    item.value for item in RouteType
)


class OrchestratorPortAdapter:
    """``AIOrchestratorService`` → :class:`OrchestratorPort`（鸭子类型适配）。

    被适配者是 **project-scoped** 实例（由 composition root 保证与
    run.project_id 匹配）；本适配器不重新绑定 project、不读 .env、
    不创建任何基础设施。
    """

    def __init__(self, service: Any) -> None:
        if service is None:
            raise TypeError("service 不能为 None")
        for name in ("execute", "resume_agent_run", "cancel_agent_run"):
            if not callable(getattr(service, name, None)):
                raise TypeError(
                    f"service 必须提供可调用的 {name}()"
                    f"（got {type(service).__name__}）"
                )
        self._service = service

    async def execute(
        self, question: str, *, context: str | None = None
    ) -> OrchestrationOutcome:
        result = await self._call(
            "execute", question, context=context
        )
        return _map_orchestration_result(result)

    async def resume_agent_run(
        self, run_id: str, *, question: str, context: str | None = None
    ) -> OrchestrationOutcome:
        result = await self._call(
            "resume_agent_run", run_id, question=question, context=context
        )
        return _map_orchestration_result(result)

    async def cancel_agent_run(self, run_id: str) -> None:
        """协作式取消（被适配者返回 ``AgentCancellationResult``；ports 契约返回 None）。

        **不**假设该调用中断任何进行中的工作 / **不**假设副作用被撤销
        （D-E：仍以被适配者既有语义为准；返回对象不进入 ports 结果面）。
        """
        await self._call("cancel_agent_run", run_id)

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        fn = getattr(self._service, method)
        try:
            return await fn(*args, **kwargs)
        except WorkflowPortsError:
            raise
        except Exception as exc:
            # 外部执行边界：归一为 ports 家族；消息只含类型名（不带异常正文）
            raise WorkflowPortsError(
                f"Orchestrator {method} 失败: {type(exc).__name__}"
            ) from exc


class ToolExecutionPortAdapter:
    """``ToolExecutionService`` → :class:`ToolExecutionPort`。

    唯一 Tool 执行边界（不直呼 Handler / 不绕过 capability 校验）：
    ``ports.ToolCallContext`` 映射为真实 ``ToolExecutionContext``（字段面一致，
    S1 冻结时已对齐）；project 不一致 ⇒ fail-closed（外部 0 次调用）。
    """

    def __init__(self, service: Any, *, project_id: str | None = None) -> None:
        if service is None:
            raise TypeError("service 不能为 None")
        if not callable(getattr(service, "execute", None)):
            raise TypeError(
                "service 必须提供可调用的 execute()"
                f"（got {type(service).__name__}）"
            )
        if project_id is not None and (
            not isinstance(project_id, str) or not project_id.strip()
        ):
            raise TypeError("project_id 必须是非空 str 或 None")
        self._service = service
        self._project_id = project_id

    async def execute(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        context: ToolCallContext | None = None,
    ) -> ToolExecutionOutcome:
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise WorkflowPortsError(
                f"tool_name 必须是非空 str（当前: {type(tool_name).__name__}）"
            )
        execution_context: ToolExecutionContext | None = None
        if context is not None:
            if not isinstance(context, ToolCallContext):
                raise WorkflowPortsError(
                    "context 必须是 ToolCallContext 或 None"
                    f"（当前: {type(context).__name__}）"
                )
            if (
                self._project_id is not None
                and context.project_id is not None
                and context.project_id != self._project_id
            ):
                raise WorkflowPortsError(
                    "Tool 执行上下文 project_id 与绑定作用域不一致（fail-closed）"
                )
            execution_context = ToolExecutionContext(
                request_id=context.request_id,
                round=context.round,
                project_id=context.project_id,
                tool_call_id=context.tool_call_id,
            )

        # 副本：arguments 只含业务参数（Context 不写入 arguments —— 被适配者契约）
        call_arguments = dict(arguments) if arguments is not None else None
        try:
            result = await self._service.execute(
                tool_name,
                arguments=call_arguments,
                context=execution_context,
            )
        except WorkflowPortsError:
            raise
        except Exception as exc:
            raise WorkflowPortsError(
                f"Tool 执行失败: {type(exc).__name__}"
            ) from exc

        success = getattr(result, "success", None)
        if not isinstance(success, bool):
            raise WorkflowPortsError(
                "ToolResult.success 缺失或类型非法（契约违反；fail-closed）"
            )
        data: Any = None
        error: str | None = None
        if success:
            data = getattr(result, "data", None)
        else:
            candidate = getattr(result, "error", None)
            if isinstance(candidate, str) and candidate.strip():
                error = candidate
        # Tool 失败**绝不**被映射成成功；error 仅供分类 / 日志（S3 不落库）
        return ToolExecutionOutcome(success=success, data=data, error=error)


# ============================================================
# 结果映射（纯函数；白名单）
# ============================================================


def _map_orchestration_result(result: Any) -> OrchestrationOutcome:
    """``AIOrchestrationResult`` → :class:`OrchestrationOutcome`（fail-closed）。"""
    route = getattr(result, "route", None)
    route_value = getattr(route, "value", None)
    if not isinstance(route_value, str):
        route_value = route if isinstance(route, str) else None
    if route_value not in _KNOWN_ROUTES:
        raise WorkflowPortsError(
            f"Orchestrator route 非法 / 未知: {route_value!r}（fail-closed）"
        )

    content = getattr(result, "content", None)
    if content is not None and not isinstance(content, str):
        raise WorkflowPortsError(
            "Orchestrator content 类型非法（fail-closed；不猜结构）"
        )

    if route_value != RouteType.AGENT.value:
        # RAG / TOOL / TEXT_TO_SQL：服务正常返回 ⇒ 有效结果（业务失败语义
        # 在 content / data 内，由上层解释——不在此重新判定）
        return OrchestrationOutcome(route=route_value, content=content)

    # ---- AGENT：状态白名单判定（不伪装成功）----
    metadata = getattr(result, "metadata", None)
    agent_status = (
        metadata.get("agent_status")
        if isinstance(metadata, Mapping)
        else None
    )
    data = getattr(result, "data", None)

    if agent_status == AGENT_STATUS_WAITING_APPROVAL:
        agent_run_id = getattr(data, "run_id", None)
        if not isinstance(agent_run_id, str) or not agent_run_id.strip():
            raise WorkflowPortsError(
                "Agent WAITING_APPROVAL 但缺 agent run_id（fail-closed）"
            )
        approval_id = getattr(data, "approval_id", None)
        return OrchestrationOutcome(
            route=route_value,
            content=content,
            agent_run_id=agent_run_id,
            agent_status=agent_status,
            agent_approval_id=(
                approval_id
                if isinstance(approval_id, str) and approval_id.strip()
                else None
            ),
        )

    if agent_status == AGENT_STATUS_COMPLETED:
        return OrchestrationOutcome(
            route=route_value, content=content, agent_status=agent_status
        )

    # FAILED 通常经异常抛出；LIMIT_REACHED / CANCELLED / 缺失 / 未知 ⇒
    # fail-closed（**绝不**把非成功状态映射为成功结果）
    raise WorkflowPortsError(
        f"Agent 非成功终态（{agent_status!r}）——不伪装成功（fail-closed）"
    )
