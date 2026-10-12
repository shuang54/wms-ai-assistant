"""Workflow Runtime Composition Root（Phase 6 Step 5 · S5）。

``create_workflow_runtime(...)`` 是**唯一**把 Domain / Persistence / Ports /
Binding / Execution / Runtime 组装为可直接调用（start / advance / 决策 / 取消）
的 :class:`WorkflowRuntime` 的生产装配点（ADR-01 §10.2 决策 B；沿用项目
composition-root 惯例：显式工厂函数 + 全部 keyword-only + 依赖可注入）。

**只装配**：不启动 Run / 不推进 Run / 不修改状态机 / 不自动恢复 UNKNOWN /
不创建全局共享请求级 Session / 不隐式新建 Engine / 不调用外部 LLM / Tool。

依赖注入（全部可覆盖；默认值 = 生产实现）：
```text
stores                  None ⇒ 3 个真实 Repository **经 Store Adapter**
                             （store_adapters.py：ORM→DTO 显式映射 + 异常映射；
                              惰性 Session；每操作独立事务——S5 §七审查）
orchestrator            None ⇒ build_orchestrator_for_project(
                             project_id, agent_enabled=True)
                             （Phase 5 唯一允许的 Agent 装配点；README：
                             装配层显式启用 Agent——ADR §10.2 决策 B；
                             HTTP 入口默认不受影响）
tool_execution_service  None ⇒ ToolExecutionService(build_default_tool_registry(),
                             capabilities=capabilities, project_id=project_id)
policy / clock / *_factory ⇒ 生产默认（WorkflowRuntimePolicy / SystemClock /
                             UUID 生成器；approval_id 使用独立 wf-ap- 命名空间）
```

Project 一致性（ADR §16 I2 · 本 factory 的 fail-closed 防线）：
    本函数绑定**单一** ``project_id``；Runtime 在执行前经 provider 校验
    ``run.project_id == 绑定值``——不一致 ⇒ ``WorkflowPortsError``
    （fail-closed；外部 Tool / AI **0 次调用**）。

边界（AST 测试锁定）：
    * 本模块（装配层）允许 import db / services / tools —— 这是它的职责；
    * Domain / ports / binding / execution / runtime **不**反向 import 本模块；
    * 全部重型 import 放在函数体内（延迟导入；对齐项目惯例，防循环依赖）。
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from backend.app.workflow.definition import WorkflowRegistry
from backend.app.workflow.policy import WorkflowRuntimePolicy
from backend.app.workflow_runtime.adapters import (
    OrchestratorPortAdapter,
    ToolExecutionPortAdapter,
)
from backend.app.workflow_runtime.execution import NodeExecutor
from backend.app.workflow_runtime.ports import (
    Clock,
    OrchestratorPort,
    ToolExecutionPort,
    WorkflowApprovalStore,
    WorkflowNodeRunStore,
    WorkflowPortsError,
    WorkflowRunStore,
)
from backend.app.workflow_runtime.runtime import WorkflowRuntime

__all__ = [
    "SystemClock",
    "create_workflow_runtime",
]


class SystemClock:
    """生产时钟（UTC；仅 ``now()``——ADR/S1 冻结的 Clock 端口）。"""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def create_workflow_runtime(
    *,
    registry: WorkflowRegistry,
    project_id: str,
    orchestrator: Any | None = None,
    tool_execution_service: Any | None = None,
    capabilities: Any | None = None,
    run_store: WorkflowRunStore | None = None,
    node_run_store: WorkflowNodeRunStore | None = None,
    approval_store: WorkflowApprovalStore | None = None,
    policy: WorkflowRuntimePolicy | None = None,
    clock: Clock | None = None,
    run_id_factory: Callable[[], str] | None = None,
    request_id_factory: Callable[[], str] | None = None,
    approval_id_factory: Callable[[], str] | None = None,
) -> WorkflowRuntime:
    """组装一个绑定 ``project_id`` 的 WorkflowRuntime（**纯装配**）。

    Args:
        registry:               项目 Workflow 定义注册表（**调用方提供**；
                                本函数不新建第二套 Registry、不硬编码任何
                                workflow 名；未注册 ⇒ Runtime 侧
                                ``resolve_current`` 显式失败）。
        project_id:             服务器端解析结果（非空 str）；绑定为执行作用域。
        orchestrator:           ``OrchestratorPort`` 兼容服务（真实 =
                                ``AIOrchestratorService``）；None ⇒ 生产装配
                                （``build_orchestrator_for_project(agent_enabled=True)``）。
        tool_execution_service: ``ToolExecutionPort`` 兼容服务（真实 =
                                ``ToolExecutionService``）；None ⇒ 生产装配
                                （默认只读 Tool Registry）。
        capabilities:           可选 ``ProjectCapabilities``——注入 Tool 执行
                                边界的 capability 白名单（None = 与既有
                                ToolExecutionService 契约一致：不限制；
                                生产调用方应从 ProjectConfiguration 解析后传入）。
        run_store / node_run_store / approval_store:
                                None ⇒ 真实 Repository（惰性 Session；
                                **不**触碰 DB 直到 Runtime 首次调用）。
        policy / clock / run_id_factory / request_id_factory / approval_id_factory:
                                生产默认 / 覆盖点（测试可注入确定性实现）。

    Returns:
        WorkflowRuntime（可直接 ``start`` / ``advance`` / ``submit_approval_decision``
        / ``request_cancel``）。

    Raises:
        ValueError: project_id 非法。
        TypeError:  registry 类型非法。
    """
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError(
            "project_id 必须是非空 str（服务器端解析结果——ADR §16 I1："
            "禁止 runtime memory 猜测 / 用户输入直用）"
        )
    if not isinstance(registry, WorkflowRegistry):
        raise TypeError(
            "registry 必须是 WorkflowRegistry（项目唯一注册表；不新建第二套）"
        )

    # ---- 1) Stores（默认 = 真实 Repository 经 Store Adapter 接入；
    #         惰性 Session，构造不触 DB）----
    if run_store is None or node_run_store is None or approval_store is None:
        from backend.app.db.workflow_approval_repository import (
            WorkflowApprovalRepository,
        )
        from backend.app.db.workflow_node_run_repository import (
            WorkflowNodeRunRepository,
        )
        from backend.app.db.workflow_run_repository import (
            WorkflowRunRepository,
        )
        from backend.app.workflow_runtime.store_adapters import (
            WorkflowApprovalStoreAdapter,
            WorkflowNodeRunStoreAdapter,
            WorkflowRunStoreAdapter,
        )

        if run_store is None:
            run_store = WorkflowRunStoreAdapter(WorkflowRunRepository())
        if node_run_store is None:
            node_run_store = WorkflowNodeRunStoreAdapter(
                WorkflowNodeRunRepository()
            )
        if approval_store is None:
            approval_store = WorkflowApprovalStoreAdapter(
                WorkflowApprovalRepository()
            )

    # ---- 2) Orchestrator（默认 = ADR §10.2 决策 B 的唯一装配点）----
    if orchestrator is None:
        from backend.app.services.project_orchestrator_factory import (
            build_orchestrator_for_project,
        )

        orchestrator = build_orchestrator_for_project(
            project_id, agent_enabled=True
        )

    # ---- 3) Tool 执行边界（默认 = 既有只读 Tool Registry）----
    if tool_execution_service is None:
        from backend.app.services.tool_execution_service import (
            ToolExecutionService,
        )
        from backend.app.tools.get_inventory import build_default_tool_registry

        tool_execution_service = ToolExecutionService(
            build_default_tool_registry(),
            capabilities=capabilities,
            project_id=project_id,
        )

    # ---- 4) 端口适配器（装配层）----
    orchestrator_adapter = OrchestratorPortAdapter(orchestrator)
    tool_adapter = ToolExecutionPortAdapter(
        tool_execution_service, project_id=project_id
    )

    # ---- 5) project 一致性防线（provider 级；不一致 ⇒ 外部 0 次调用）----
    def _orchestrator_provider(requested_project_id: str) -> OrchestratorPort:
        _require_same_project(requested_project_id, project_id)
        return orchestrator_adapter

    def _tool_provider(requested_project_id: str) -> ToolExecutionPort:
        _require_same_project(requested_project_id, project_id)
        return tool_adapter

    # ---- 6) NodeExecutor（S3）----
    executor = NodeExecutor(
        orchestrator_provider=_orchestrator_provider,
        tool_execution_provider=_tool_provider,
        approval_store=approval_store,
        approval_id_factory=approval_id_factory or _default_approval_id,
    )

    # ---- 7) Runtime（S4）----
    return WorkflowRuntime(
        run_store=run_store,
        node_run_store=node_run_store,
        approval_store=approval_store,
        registry=registry,
        executor=executor,
        policy=policy or WorkflowRuntimePolicy(),
        clock=clock or SystemClock(),
        orchestrator_provider=_orchestrator_provider,
        request_id_factory=request_id_factory,
        run_id_factory=run_id_factory,
    )


# ============================================================
# 内部辅助
# ============================================================


def _require_same_project(requested_project_id: object, bound: str) -> None:
    """Runtime 传入的 project 必须与 factory 绑定值一致（ADR §16 I2）。"""
    if requested_project_id != bound:
        raise WorkflowPortsError(
            "run.project_id 与 Runtime 绑定作用域不一致（fail-closed）:"
            f" requested={requested_project_id!r} bound={bound!r}"
        )


def _default_approval_id() -> str:
    """审批身份（workflow 独立命名空间；不复用 agent 的 ID 体系）。"""
    return f"wf-ap-{uuid.uuid4().hex}"
