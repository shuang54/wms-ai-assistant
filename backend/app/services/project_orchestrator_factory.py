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

- Phase 3.8.6 —— 本工厂不再分别调用 Registry / SemanticProvider /
  KnowledgeProvider，统一通过 ``ProjectConfigurationProvider.get(project_id)``
  获取 ``ProjectConfiguration``（context / schema_name / capabilities /
  semantic / knowledge_scope），消除多源配置漂移；Core Orchestrator
  职责边界不变（§十一：不把整个 ProjectConfiguration 注入 Orchestrator）；
- **不修改** Router 路由规则 / Orchestrator 核心执行逻辑 /
  SQL Validator 安全规则 / Tool Framework 业务能力；
- **不修改** RAG 核心：RagService / Reranker / ContextBuilder /
  Embedding 均复用 base 实例；Phase 3.8.4 仅通过 per-project
  ``knowledge_scope`` 在 VectorSearch 调用点显式过滤（不重构 RAG）；
- **不让 Orchestrator 创建数据库连接**：Engine 统一由
  DatabaseEngineProvider（服务器端注册表）解析；
- 本模块位于 Service 层（API 层的静态 import 禁令
  ``test_api_module_does_not_import_forbidden_services`` 不适用于此处；
  API 层只 import 本工厂与 projects 包的异常/注册表）。
"""
from __future__ import annotations

import logging
from typing import Any

from backend.app.agent import (
    AGENT_DEFAULT_MAX_COST_TOKENS,
    AGENT_DEFAULT_MAX_EXECUTION_TIME_MS,
    AgentRuntime,
    AgentRuntimePolicy,
    ProviderAgentLLM,
)
from backend.app.agent.recorder import build_agent_tool_result_write_point
from backend.app.agent.recorder import (
    build_agent_run_recorder,
    build_agent_step_recorder,
)
from backend.app.llm.client import get_default_llm_client
from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.projects.configuration import (
    DefaultProjectConfigurationProvider,
    ProjectConfiguration,
    ProjectConfigurationProvider,
)
from backend.app.projects.engine_provider import (
    DatabaseEngineProvider,
    DatabaseEngineProviderError,
    get_default_engine_provider,
)
from backend.app.projects.knowledge_provider import (
    ProjectKnowledgeProvider,
    ProjectKnowledgeProviderError,
    get_default_project_knowledge_provider,
)
from backend.app.projects.registry import (
    ProjectRegistry,
    get_default_project_registry,
)
from backend.app.projects.semantic_loader import (
    ProjectSemanticError,
    ProjectSemanticNotFoundError,
)
from backend.app.projects.semantic_provider import (
    ProjectSemanticProvider,
    get_default_project_semantic_provider,
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
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.get_inventory import (
    GetInventoryHandler,
    build_default_tool_registry,
    register_get_inventory_tool,
)
from backend.app.tools.get_work_order import (
    GetWorkOrderHandler,
    register_get_work_order_tool,
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
    semantic_provider: ProjectSemanticProvider | None = None,
    knowledge_provider: ProjectKnowledgeProvider | None = None,
    configuration_provider: ProjectConfigurationProvider | None = None,
    agent_enabled: bool = False,
    agent_llm_provider: Any | None = None,
    agent_policy: AgentRuntimePolicy | None = None,
) -> AIOrchestratorService:
    """构造绑定到指定项目数据源的 Orchestrator（Phase 3.8.1）。

    Args:
        project_id:         请求中的 project_id（服务器端注册表解析）。
        base:               共享下游依赖的基准 Orchestrator（Router 之外的
                            RAG / Text-to-SQL Generator / Selector / Composer
                            均复用 base 的实例）；None 时使用全局默认工厂。
        registry:           项目注册表；None 时使用默认注册表。
        engine_provider:    Engine Provider；None 时使用默认 Provider。
        semantic_provider:  Phase 3.8.3 —— project_id → ProjectSemantic
                            的服务器端解析器；None 时使用默认 Provider
                            （LoaderBacked，文件名 == project_id，
                            旧行为完全等价）。
        knowledge_provider: Phase 3.8.4 —— project_id → KnowledgeScope
                            的服务器端解析器；None 时使用默认 Provider
                            （vietnam-wms legacy 兼容）。仅当该项目
                            ``knowledge_enabled=True`` 时解析（禁用 RAG 的
                            项目不触碰 KnowledgeProvider，任务书 §十三）；
                            解析失败 → AIOrchestratorUnavailableError
                            （API 层映射 503，绝不回退其他项目知识）。
        configuration_provider:
                            Phase 3.8.6 —— project_id → ProjectConfiguration
                            的统一装配器；None 时由上面的 registry /
                            semantic_provider / knowledge_provider 组合
                            构造 ``DefaultProjectConfigurationProvider``
                            （Factory 不再分别调用各 Provider）。
        agent_enabled:      Phase 5 Agent Runtime Integration（§29.1 / §29.11）——
                            AGENT 能力开关；**默认 False**。
                            False ⇒ 本函数**不**创建 ProviderAgentLLM /
                            AgentRuntime，Router 的 AGENT route 也被拒绝
                            （fail-closed；行为与 Phase 4.2 完全一致）。
                            True ⇒ 在本装配点构造 AgentRuntime（唯一允许的
                            composition root；Runtime **不**自行读 .env /
                            不创建 DeepSeek client）并共享**同一**
                            ToolExecutionService 执行边界。
        agent_llm_provider: 可选 LLM Provider（Agent 的 AgentLLM 来源）；
                            None ⇒ ``get_default_llm_client()``
                            （生产默认单例 —— 与 RAG / T2SQL 同源）。
                            仅在 ``agent_enabled=True`` 时被使用。
        agent_policy:       可选 AgentRuntimePolicy；None ⇒ 使用
                            ``AGENT_DEFAULT_MAX_EXECUTION_TIME_MS`` /
                            ``AGENT_DEFAULT_MAX_COST_TOKENS``
                            （steps / tool_calls / llm_calls 保持 8/8/8 默认）。
                            仅在 ``agent_enabled=True`` 时被使用。

    Observability（Phase 3.11 Step 19，**签名不变**）：

        project-scoped Orchestrator 的观测出口**继承** ``base``：
        ``base.tool_execution_observer`` → 同一 Application lifetime 内
        per-project 与默认 Orchestrator 共享同一 Collector（由 Composition
        Root / 调用方创建并持有；Factory **不**创建 Collector）。
        base 无 observer（含 ``base=None`` 时构造的默认 base）→ ``None``
        （0 Record，旧行为逐字不变）。

    Returns:
        新的 ``AIOrchestratorService``：
        - Router：新实例（capability 适配 per-project Tool Registry，
          路由规则本身不变）；
        - RAG / T2S Generator / TableSelector / ContextComposer：复用 base；
        - ProjectContextProvider（含 **per-project Semantic**，
          Phase 3.8.3）/ SchemaExplorer / SQLExecutor / Tool Registry /
          **KnowledgeScope**（Phase 3.8.4）：绑定该项目。

    Raises:
        ProjectNotFoundError:      project_id 未注册（API 层映射 404）。
        AIOrchestratorUnavailableError: 数据源 / 知识 scope 不可用
                                   （连接未注册 / DATABASE_URL 为空 /
                                   知识 scope 未注册等，API 层映射 503）。
    """
    resolved_provider = (
        engine_provider
        if engine_provider is not None
        else get_default_engine_provider()
    )
    # Phase 3.8.6：统一配置入口（Factory 不再分别调用 registry /
    # semantic_provider / knowledge_provider；未显式注入时按既有 DI
    # 语义组合默认装配器，保持旧调用与 monkeypatch 行为不变）。
    resolved_configuration_provider = (
        configuration_provider
        if configuration_provider is not None
        else DefaultProjectConfigurationProvider(
            registry=(
                registry if registry is not None
                else get_default_project_registry()
            ),
            semantic_provider=(
                semantic_provider
                if semantic_provider is not None
                else get_default_project_semantic_provider()
            ),
            knowledge_provider=(
                knowledge_provider
                if knowledge_provider is not None
                else get_default_project_knowledge_provider()
            ),
        )
    )

    # ---- 1) project_id → ProjectConfiguration（§七：Registry 先行） ----
    # ProjectNotFoundError 原样透传（→ 404）；Semantic / Knowledge 缺失
    # → clear error → 503（§二十：绝不 fallback 到 vietnam-wms / global）。
    try:
        configuration = resolved_configuration_provider.get(project_id)
    except ProjectSemanticError as exc:
        raise AIOrchestratorUnavailableError(
            f"项目 {project_id!r} 的业务语义不可用: {exc}"
        ) from exc
    except ProjectKnowledgeProviderError as exc:
        raise AIOrchestratorUnavailableError(
            f"项目 {project_id!r} 的知识库上下文不可用: {exc}"
        ) from exc
    context = configuration.context
    schema_name = configuration.schema_name
    capabilities = configuration.capabilities

    # ---- 2) DataSource → 受控 Engine（同一 Engine 贯穿全链路） ----
    try:
        engine = resolved_provider.get_engine(context.data_source)
    except DatabaseEngineProviderError as exc:
        raise AIOrchestratorUnavailableError(
            f"项目 {project_id!r} 的数据源不可用: {exc}"
        ) from exc

    # ---- 3) per-project 依赖（Schema / Executor / Tool / Semantic 同源） ----
    explorer = SchemaExplorerService(engine=engine)
    project_provider = DefaultProjectContextProvider(
        project_context=context,
        explorer=explorer,
        schema_name=schema_name,
        # Phase 3.8.3 + 3.8.6：Semantic 来自统一聚合配置（不再各自解析），
        # 仍然遵循"由服务器端 project_id 唯一决定"（HTTP 不可注入）。
        semantic_provider=_ResolvedSemanticProvider(configuration),
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

    # ---- 3.5) Phase 3.8.4 / 3.8.6：Project Knowledge Scope ----
    # scope 已在 ProjectConfiguration 装配阶段解析（仅 knowledge_enabled=True
    # 的项目；禁用 RAG 的项目 KnowledgeProvider 0 次解析，任务书 §十三）。
    knowledge_scope = configuration.knowledge_scope

    # ---- 4) base 依赖解析（RAG / T2S / Selector / Composer 复用） ----
    if base is None:
        base = _build_default_base()

    # ---- 4.5) Phase 3.11 Step 19：Observability 出口（不创建 Collector） ----
    # 继承 base 的观测出口：同一 Application lifetime 内 per-project 与默认
    # Orchestrator 共享同一 Collector（Collector 由 Composition Root 创建）；
    # base 无 observer（含 base=None 的默认 base）→ None（0 Record，旧行为）。
    # 保持 Factory 签名不变（HTTP 不能注入任何配置 / 观测依赖）。
    resolved_observer = getattr(base, "tool_execution_observer", None)

    # ---- 4.6) Phase 5 Agent Runtime Integration（§29.1 / §29.11 / OD-P5-23） ----
    # 默认（agent_enabled=False）⇒ **不**创建 Provider / Runtime / Router LLM；
    # 本函数行为与 Phase 4.2 逐字节等价（AGENT route 在 Router 层即被拒绝）。
    agent_runtime: AgentRuntime | None = None
    router_llm_client: Any | None = None
    shared_tool_execution: ToolExecutionService | None = None
    # Phase 5 P-2：Agent Run 持久化边界 —— 与 observer 同方式继承 base
    # （同一 Application lifetime 内共享一个 recorder；无 DB ⇒ None）。
    resolved_agent_run_recorder: Any | None = getattr(
        base, "agent_run_recorder", None
    )
    # Phase 5 AgentStep Persistence：Step 持久化边界（同方式继承 base）。
    resolved_agent_step_recorder: Any | None = getattr(
        base, "agent_step_recorder", None
    )
    if agent_enabled:
        resolved_agent_provider = (
            agent_llm_provider
            if agent_llm_provider is not None
            else get_default_llm_client()
        )
        # 与 Orchestrator 自建默认实例**同源同参**（registry / capabilities /
        # project_id / observer）⇒ 复用**同一**执行边界（capability 第二道
        # 校验 + 观测出口不丢失），不新建第二套授权中心。
        shared_tool_execution = ToolExecutionService(
            registry=tool_registry,
            capabilities=capabilities,
            project_id=project_id,
            observer=resolved_observer,
        )
        agent_runtime = AgentRuntime(
            llm=ProviderAgentLLM(resolved_agent_provider),
            tool_execution=shared_tool_execution,
            policy=(
                agent_policy
                if agent_policy is not None
                else AgentRuntimePolicy(
                    max_execution_time_ms=(
                        AGENT_DEFAULT_MAX_EXECUTION_TIME_MS
                    ),
                    max_cost_tokens=AGENT_DEFAULT_MAX_COST_TOKENS,
                )
            ),
            # Agent 只能请求项目允许的 Tool（⊆ ProjectCapabilities；
            # Runtime 装配期二次校验，ToolExecutionService 执行期第三次校验）。
            allowed_tools=capabilities.tool_names,
            # Phase 5 P-4 Integration：Tool Result 写入端口（Tool handler 返回后
            # **立即**写 agent_tool_result；run_id 取自 Agent Run Scope）。
            # 无 DATABASE_URL ⇒ None（Runtime 零开销跳过；行为与 P-4 之前一致）。
            tool_result_sink=build_agent_tool_result_write_point(),
        )
        # AGENT 只能由 LLM fallback 选择（§29.1 / OQ-P5-11）⇒ 启用 Agent 的
        # 项目必须让 Router 能真正发起 fallback，否则 AGENT 恒不可达。
        router_llm_client = resolved_agent_provider
        # Agent Run 持久化（P-2）：base 未提供时在本装配点创建（唯一允许位置）。
        if resolved_agent_run_recorder is None:
            resolved_agent_run_recorder = build_agent_run_recorder()
        # Agent Step 持久化：同上（独立边界，独立 best-effort）。
        if resolved_agent_step_recorder is None:
            resolved_agent_step_recorder = build_agent_step_recorder()

    logger.info(
        "project orchestrator built",
        extra={
            "project_id": project_id,
            "schema_name": schema_name,
            "data_source_name": context.data_source.name,
            "tools": list(capabilities.tool_names),
            "knowledge_enabled": capabilities.knowledge_enabled,
            "text_to_sql_enabled": capabilities.text_to_sql_enabled,
            "agent_enabled": bool(agent_enabled),
            "knowledge_namespace": (
                knowledge_scope.namespace
                if knowledge_scope is not None
                else None
            ),
        },
    )

    return AIOrchestratorService(
        # Router 新实例：capability 元数据来自 per-project Tool Registry
        # （只有该项目允许的 Tool）；路由规则本身不变。
        # Phase 3.8.2：knowledge / text_to_sql 开关同步注入，
        # 被禁用的能力不会被规则选中（任务书 §十）。
        # Phase 5 §29.1：agent_enabled / llm_client 仅在显式启用 Agent 时注入
        # （AGENT 只能经 LLM fallback 进入；默认两者都不改变）。
        router=AIRouterService(
            llm_client=router_llm_client,
            tool_capabilities=ToolRegistryCapabilityAdapter(tool_registry),
            knowledge_enabled=capabilities.knowledge_enabled,
            text_to_sql_enabled=capabilities.text_to_sql_enabled,
            agent_enabled=agent_enabled,
        ),
        # RAG：RagService 实例复用 base（无状态，可共享），
        # 但检索范围由 Phase 3.8.4 的 per-project knowledge_scope
        # 在调用点限定（VectorSearch 显式过滤，绝不跨项目检索）；
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
        # Phase 3.8.4：项目知识检索范围（服务器端 Provider 解析；
        # knowledge_enabled=False 时为 None，且 RAG 不可达）
        knowledge_scope=knowledge_scope,
        # Phase 3.11 Step 19：Observability 出口（Application lifetime 共享；
        # Collector 由 Composition Root / 调用方创建 —— Factory 不创建）
        tool_execution_observer=resolved_observer,
        # Phase 5 §29.11：Agent 接线（默认两者均 None ⇒ 旧行为 / fail-closed）。
        # 显式注入 ToolExecutionService 时 observer 由该边界持有（同一实例）。
        tool_execution_service=shared_tool_execution,
        agent_runtime=agent_runtime,
        # Phase 5 P-2：Agent Run 持久化（None ⇒ 0 行；仅在 AGENT 执行时写入）。
        agent_run_recorder=resolved_agent_run_recorder,
        # Phase 5 AgentStep Persistence：Step 投影持久化（独立 best-effort）。
        agent_step_recorder=resolved_agent_step_recorder,
    )


# ============================================================
# Phase 3.8.6：已聚合 Semantic → 既有 ProjectContextProvider
# ============================================================

class _ResolvedSemanticProvider:
    """把 ProjectConfiguration.semantic 交给既有 DefaultProjectContextProvider。

    目的：Core 层（``ai_orchestrator_service.DefaultProjectContextProvider``）
    **零改动**（任务书 §十一）——它仍然只看到一个 "SemanticProvider"，
    但语义结果来自 Phase 3.8.6 的统一聚合配置。

    - project_id 一致 → 返回已装配的 semantic；
    - project_id 不一致 → ``ProjectSemanticNotFoundError``（clear error，
      Core 层已将其映射为 ``AIOrchestratorUnavailableError`` → 503），
      绝不返回其它项目的语义。
    """

    def __init__(self, configuration: ProjectConfiguration) -> None:
        self._configuration = configuration

    def get(self, project_id: str) -> ProjectSemantic:  # noqa: ANN401
        from backend.app.projects.semantic import ProjectSemantic

        if project_id != self._configuration.project_id:
            raise ProjectSemanticNotFoundError(project_id)
        semantic = self._configuration.semantic
        assert isinstance(semantic, ProjectSemantic)  # noqa: S101
        return semantic


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

    Phase 3.11 Step 5：支持两个真实只读 Tool（均绑定同一项目 Engine /
    schema / project_id，共用 ToolExecutionService 执行边界）：

        * ``get_inventory``   （Phase 3.7.12）
        * ``get_work_order``  （Phase 3.11 Step 5）

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

    if capabilities.allows_tool("get_work_order"):
        wo_settings = _dc.replace(
            settings.work_order_tool, schema_name=schema_name
        )
        register_get_work_order_tool(
            registry,
            handler=GetWorkOrderHandler(
                engine=engine,
                wo_settings=wo_settings,
                project_id=project_id,
            ),
        )

    known_tools = {"get_inventory", "get_work_order"}
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
