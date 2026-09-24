"""Project-scoped Orchestrator 工厂（Phase 3.8.1）。

职责（任务书 §九 / §十 / §十一 / §十二）：

    project_id
        ↓
    ProjectRegistry.get(project_id)              → ProjectRegistration
        ↓
    DatabaseEngineProvider.get_engine(...)       → 该项目受控 Engine
        ↓
    同一 Engine 贯穿四个消费点：
        ├── SchemaExplorerService（inspect 该项目 schema → DatabaseSchema A/B）
        ├── DefaultProjectContextProvider（Schema → Selector → Composer）
        ├── SQLExecutorService（READ ONLY 执行，Engine A/B）
        └── get_inventory Tool（Handler 查询该项目 schema 的库存表）
        ↓
    AIOrchestratorService（Router / RAG / T2S Generator 复用 base）

纪律（任务书 §二 严格范围）：

- **不修改** Router 路由规则 / Orchestrator 核心执行逻辑 /
  SQL Validator 安全规则 / Tool Framework 业务能力；
- **不修改** RAG：RAG 继续复用 base 的全局 RagService
  （知识库不按项目拆分，任务书 §十三）；
- **不让 Orchestrator 创建数据库连接**：Engine 统一由
  DatabaseEngineProvider（服务器端注册表）解析；
- 本模块位于 Service 层（API 层的静态 import 禁令
  ``test_api_module_does_not_import_forbidden_services`` 不适用于此处；
  API 层只 import 本工厂与 projects 包的异常/注册表）。
"""
from __future__ import annotations

import logging
from typing import Any

from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.projects.engine_provider import (
    DatabaseEngineProvider,
    DatabaseEngineProviderError,
    get_default_engine_provider,
)
from backend.app.projects.registry import (
    ProjectRegistry,
    get_default_project_registry,
)
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorService,
    AIOrchestratorUnavailableError,
    DefaultProjectContextProvider,
)
from backend.app.services.ai_router_service import (
    AIRouterService,
    ToolRegistryCapabilityAdapter,
)
from backend.app.services.schema_explorer_service import SchemaExplorerService
from backend.app.services.sql_executor_service import SQLExecutorService
from backend.app.tools.get_inventory import (
    GetInventoryHandler,
    build_default_tool_registry,
    register_get_inventory_tool,
)
from backend.app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

__all__ = [
    "build_orchestrator_for_project",
]


def build_orchestrator_for_project(
    project_id: str,
    *,
    base: AIOrchestratorService | None = None,
    registry: ProjectRegistry | None = None,
    engine_provider: DatabaseEngineProvider | None = None,
) -> AIOrchestratorService:
    """构造绑定到指定项目数据源的 Orchestrator（Phase 3.8.1）。

    Args:
        project_id:      请求中的 project_id（服务器端注册表解析）。
        base:            共享下游依赖的基准 Orchestrator（Router 之外的
                         RAG / Text-to-SQL Generator / Selector / Composer
                         均复用 base 的实例）；None 时使用全局默认工厂。
        registry:        项目注册表；None 时使用默认注册表。
        engine_provider: Engine Provider；None 时使用默认 Provider。

    Returns:
        新的 ``AIOrchestratorService``：
        - Router：新实例（capability 适配 per-project Tool Registry，
          路由规则本身不变）；
        - RAG / T2S Generator / TableSelector / ContextComposer：复用 base；
        - ProjectContextProvider / SchemaExplorer / SQLExecutor /
          Tool Registry：绑定该项目数据源。

    Raises:
        ProjectNotFoundError:      project_id 未注册（API 层映射 404）。
        AIOrchestratorUnavailableError: 数据源不可用（连接未注册 /
                                   DATABASE_URL 为空等，API 层映射 503）。
    """
    resolved_registry = (
        registry if registry is not None else get_default_project_registry()
    )
    resolved_provider = (
        engine_provider
        if engine_provider is not None
        else get_default_engine_provider()
    )

    # ---- 1) project_id → ProjectRegistration（未注册 → clear error） ----
    registration = resolved_registry.get(project_id)
    context = registration.context
    schema_name = registration.schema_name
    capabilities = registration.capabilities

    # ---- 2) DataSource → 受控 Engine（同一 Engine 贯穿全链路） ----
    try:
        engine = resolved_provider.get_engine(context.data_source)
    except DatabaseEngineProviderError as exc:
        raise AIOrchestratorUnavailableError(
            f"项目 {project_id!r} 的数据源不可用: {exc}"
        ) from exc

    # ---- 3) per-project 依赖（Schema / Executor / Tool 同源） ----
    explorer = SchemaExplorerService(engine=engine)
    project_provider = DefaultProjectContextProvider(
        project_context=context,
        explorer=explorer,
        schema_name=schema_name,
    )
    executor = SQLExecutorService(engine=engine)
    # Phase 3.8.2：Tool Registry 只注册该项目允许的 Tool
    # （Router 的 capability 元数据来自该 Registry → Router 天然
    #  看不到被禁用的 Tool；任务书 §七）
    tool_registry = _build_project_tool_registry(
        capabilities,
        engine=engine,
        schema_name=schema_name,
        project_id=project_id,
    )

    # ---- 4) base 依赖解析（RAG / T2S / Selector / Composer 复用） ----
    if base is None:
        base = _build_default_base()

    logger.info(
        "project orchestrator built",
        extra={
            "project_id": project_id,
            "schema_name": schema_name,
            "data_source_name": context.data_source.name,
            "tools": list(capabilities.tool_names),
            "knowledge_enabled": capabilities.knowledge_enabled,
            "text_to_sql_enabled": capabilities.text_to_sql_enabled,
        },
    )

    return AIOrchestratorService(
        # Router 新实例：capability 元数据来自 per-project Tool Registry
        # （只有该项目允许的 Tool）；路由规则本身不变。
        # Phase 3.8.2：knowledge / text_to_sql 开关同步注入，
        # 被禁用的能力不会被规则选中（任务书 §十）。
        router=AIRouterService(
            tool_capabilities=ToolRegistryCapabilityAdapter(tool_registry),
            knowledge_enabled=capabilities.knowledge_enabled,
            text_to_sql_enabled=capabilities.text_to_sql_enabled,
        ),
        # RAG：知识库全局共享，不按项目拆分（任务书 §十三）；
        # knowledge_enabled=False 时 Orchestrator 硬校验在调用前拦截
        rag_service=base._rag,  # type: ignore[attr-defined]
        tool_registry=tool_registry,
        # Text-to-SQL Generator：纯 LLM + Validator，无 Engine 依赖，复用 base；
        # text_to_sql_enabled=False 时 Orchestrator 在任何 DB 访问前拦截
        text_to_sql=base._text_to_sql,  # type: ignore[attr-defined]
        # Executor：绑定该项目 Engine（任务书 §十一：不能 Schema B + Executor A）
        sql_executor=executor,
        table_selector=base._table_selector,  # type: ignore[attr-defined]
        context_composer=base._context_composer,  # type: ignore[attr-defined]
        project_context_provider=project_provider,
        # Phase 3.8.2：执行前硬校验（Router 是分类器，不是安全边界）
        capabilities=capabilities,
    )


# ============================================================
# Phase 3.8.2：按项目能力过滤 Tool Registry
# ============================================================

def _build_project_tool_registry(
    capabilities: ProjectCapabilities,
    *,
    engine: Any,
    schema_name: str,
    project_id: str,
) -> ToolRegistry:
    """构造只包含该项目允许 Tool 的 ToolRegistry（§六 / §七）。

    复用现有 ToolRegistry（不创建第二套注册中心）：全局 Tool 构建器
    映射 → 按 capabilities.tool_names 白名单注册。

    - 白名单为空 → 空 Registry（该项目无任何 Tool）；
    - 白名单含未知 Tool 名（全局不存在）→ 记 warning 并跳过
      （能力配置是静态服务器端注册，运行期不中断其它能力）。
    """
    import dataclasses as _dc

    from backend.app.config import settings

    registry = ToolRegistry()

    if capabilities.allows_tool("get_inventory"):
        inv_settings = _dc.replace(
            settings.inventory_tool, schema_name=schema_name
        )
        register_get_inventory_tool(
            registry,
            handler=GetInventoryHandler(
                engine=engine,
                inv_settings=inv_settings,
                project_id=project_id,
            ),
        )

    known_tools = {"get_inventory"}
    unknown = [n for n in capabilities.tool_names if n not in known_tools]
    if unknown:
        logger.warning(
            "project capabilities reference unknown tools; skipped",
            extra={"project_id": project_id, "unknown_tools": unknown},
        )
    return registry


def _build_default_base() -> AIOrchestratorService:
    """base=None 时的基准 Orchestrator（默认全局依赖）。

    与 ``api.orchestrator_chat._default_orchestrator`` 同构：
    全局 Tool Registry（primary 数据源）+ 默认 RagService / T2S / Executor。
    API 层调用本工厂时总是显式传入 base（共享模块级单例）。
    """
    return AIOrchestratorService(
        router=AIRouterService(
            tool_capabilities=ToolRegistryCapabilityAdapter(
                build_default_tool_registry()
            ),
        ),
        tool_registry=build_default_tool_registry(),
    )
