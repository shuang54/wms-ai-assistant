"""AI Orchestrator（Phase 3.7.9）。

职责：
    把 ``AIRouter`` 的路由决策**组合**到现有三类 AI 能力上，
    形成一条端到端可测试的执行链。本模块**不**实现任何新 AI 能力：

       Question
           ↓
       AIRouter.route（Phase 3.7.8）
           ↓
       ┌───────────────────┼──────────────────────┐
       ↓                   ↓                      ↓
   RagService   ToolArgumentExtractor        TextToSQL
   (Phase 3.5)  （Phase 3.11 Step 6）            ↓
                       ↓                  TableSelector (3.7.4)
                ToolExecutionContext      DatabaseContextComposer (3.7.3)
                （Step 13 DTO；Step 18：     TextToSQLService (3.7.6)
                  round=1 / 不伪造 call id） SQLExecutor (3.7.7)
                       ↓
                ToolExecutionService（Step 2；Step 15：可选 observer）
                       ↓
                ToolRegistry (3.6.1)
                       ↓ ToolResult
                （observer 存在时：Record → 调用方 Collector → Read Model）
           ↓
       AIOrchestrationResult

角色分工（Phase 3.11 Step 18 起）：
   * 本模块只**创建** Tool 执行上下文（一次 execute 一个 request_id /
     round=1 / 授权作用域取自执行边界 / 不伪造 LLM tool_call_id）；
   * 观测出口（observer）是**构造参数**（``None`` = 旧行为）：
     本模块只依赖 ``ToolExecutionObserver`` 协议，不创建 Collector、
     不接收数据库 / Redis / Prometheus / metrics backend，
     **不**读取 Record、**不**聚合。

纪律（纵深防御）：
   ❌ 不重新实现 SQL 生成 / 校验 / 执行（Orchestrator 必须经过现有三层）
   ❌ 不实现 Tool 参数提取（Phase 3.11 Step 6 → ToolArgumentExtractor）
   ❌ 不实现多轮 Agent / Tool Loop / 规划（一次性：Question → ONE route）
   ❌ 不创建 LLM / Engine / Session / RAG / Tool Registry（全部注入）
   ❌ 不创建观测聚合器 / 不持久化观测数据（observer 由调用方装配）
   ❌ 不修改 Chat API（Router / Orchestrator / ChatService 解耦）
   ❌ 不暴露 SQLAlchemy Row / Connection / Session / API Key
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

from backend.app.dto.assistant_outcome import determine_assistant_outcome
from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.projects.context import DataSource, ProjectContext
from backend.app.projects.knowledge_provider import ProjectKnowledgeScope
from backend.app.projects.semantic import ProjectSemantic
from backend.app.services.ai_router_service import (
    AIRouter,
    AIRouterError,
    AIRouterInputError,
    AIRouterService,
    RouteDecision,
    RouteType,
)
from backend.app.services.business_semantic_serializer import (
    BusinessSemanticSerializer,
)
from backend.app.services.database_context_composer import DatabaseContextComposer
from backend.app.services.relevant_table_selector import (
    RelevantTableSelector,
    RuleBasedRelevantTableSelector,
    TableSelectionResult,
)
from backend.app.services.schema_explorer_service import DatabaseSchema
from backend.app.services.sql_executor_service import (
    SQLExecutionResult,
    SQLExecutor,
    SQLExecutorService,
)
from backend.app.services.semantic_schema_filter import SemanticSchemaFilter
from backend.app.services.sql_validator_service import DEFAULT_MAX_ROWS
from backend.app.services.text_to_sql_context import TextToSQLContext
from backend.app.services.text_to_sql_service import (
    RESULT_STATUS_REFUSAL,
    RESULT_STATUS_SQL,
    TextToSQLGenerator,
    TextToSQLService,
)

#: Phase 3.9.25 — refusal 时用户可见的只读拒绝信息（不暴露内部 reason）
TEXT_TO_SQL_REFUSAL_MESSAGE: Final[str] = (
    "当前 AI 数据查询服务仅支持只读查询，不支持删除、修改等操作。"
)
from backend.app.services.assistant_trace import assistant_trace_scope
from backend.app.services.tool_argument_extractor import ToolArgumentExtractor
from backend.app.services.tool_execution_context import (
    ToolExecutionContext,
    new_request_id,
)
from backend.app.services.tool_execution_observer import ToolExecutionObserver
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.errors import ToolError
from backend.app.tools.registry import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

__all__ = [
    "AIOrchestrator",
    "AIOrchestratorService",
    "AIOrchestrationResult",
    "AIOrchestratorError",
    "AIOrchestratorInputError",
    "AIOrchestratorRouteError",
    "AIOrchestratorExecutionError",
    "AIOrchestratorUnavailableError",
    "AIOrchestratorCapabilityError",
    "ProjectContextProvider",
    "DefaultProjectContextProvider",
    "get_default_orchestrator",
]


# ============================================================
# DTO
# ============================================================

@dataclass(frozen=True)
class AIOrchestrationResult:
    """统一 Orchestration 结果（frozen）。

    Attributes:
        route:    最终采用的路由。
        content:  给用户的自然语言 / 自然化摘要（RAG 路径用 RagResponse.answer
                  / Tool 路径序列化 ToolResult.data / SQL 路径为格式化摘要）。
        data:     结构化数据对象（ToolResult / SQLExecutionResult / RagResponse
                  之一；测试用例具体锁定）。
        metadata: 路由 + 执行统计（不含敏感信息）。
    """

    route: RouteType
    content: str | None
    data: Any
    metadata: Mapping[str, Any]


# ============================================================
# 异常体系
# ============================================================

class AIOrchestratorError(Exception):
    """Orchestrator 通用异常。"""


class AIOrchestratorInputError(AIOrchestratorError):
    """输入非法。"""


class AIOrchestratorRouteError(AIOrchestratorError):
    """Router 失败 / 路由解析失败 / 无可执行 Tool。"""


class AIOrchestratorExecutionError(AIOrchestratorError):
    """下游能力（RAG / Tool / Text-to-SQL）执行失败；保留原始异常链。"""


class AIOrchestratorUnavailableError(AIOrchestratorError):
    """Project context 无法获取（DB 不可用 / 语义配置缺失等）。"""


class AIOrchestratorCapabilityError(AIOrchestratorError):
    """项目能力被禁用（Phase 3.8.2）。

    Router 只是分类器，**不能作为安全边界**；Orchestrator 在执行任何
    下游能力（RagService / ToolRegistry / Schema Explorer / TextToSQL /
    SQLExecutor）**之前**做硬校验：

        - knowledge_enabled=False   → RAG 拒绝（RagService 0 次调用）
        - tool_names 不含该 Tool    → TOOL 拒绝（Handler 0 次调用）
        - text_to_sql_enabled=False → T2S 拒绝（**不访问数据库**：
                                      Schema Explorer / Generator /
                                      Executor 均 0 次调用）

    Attributes:
        capability: 被禁用的能力名（"knowledge" / "text_to_sql" / tool 名）。
        project_id: 项目 ID（日志 / 测试断言用）。
    """

    def __init__(self, message: str, *, capability: str, project_id: str | None) -> None:
        super().__init__(message)
        self.capability = capability
        self.project_id = project_id


# ============================================================
# 项目上下文提供器（Orchestrator 不直接创建 Engine）
# ============================================================

class ProjectContextProvider(Protocol):
    """提供 (ProjectContext, DatabaseSchema, ProjectSemantic) 三元组。

    生产实现可包装 ``SchemaExplorerService`` + ``ProjectSemanticLoader``；
    测试实现直接返回预置对象。
    """

    def resolve(
        self,
    ) -> tuple[ProjectContext, DatabaseSchema, ProjectSemantic]: ...


class DefaultProjectContextProvider:
    """生产实现：复用 ``SchemaExplorerService`` + Semantic Provider/Loader。

    Orchestrator 仍**不**创建 Engine / Session，仅调用上层 Service
    暴露的方法。``resolve()`` 是同步桥接：内部用 ``asyncio.run``
    调 async inspector（Orchestrator 在 async 主路径里再 ``to_thread``
    包一层避免阻塞事件循环）。

    Phase 3.8.3：语义解析优先使用注入的 ``semantic_provider``
    （服务器端 project_id → ProjectSemantic 映射）；Provider 异常
    **不吞**（配置错误显式暴露，映射 AIOrchestratorUnavailableError）。
    未注入 provider 时回退旧路径（semantic_loader / 默认 Loader，
    加载失败 → 空语义，保持既有行为兼容）。
    """

    def __init__(
        self,
        *,
        project_context: ProjectContext | None = None,
        explorer: Any = None,
        semantic_loader: Any = None,
        semantic_provider: Any = None,
        schema_name: str | None = None,
    ) -> None:
        self._project_context = project_context
        self._explorer = explorer
        self._semantic_loader = semantic_loader
        self._semantic_provider = semantic_provider
        self._schema_name = schema_name

    def resolve(
        self,
    ) -> tuple[ProjectContext, DatabaseSchema, ProjectSemantic]:
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )
        from backend.app.projects.semantic_loader import ProjectSemanticLoader

        explorer = self._explorer or SchemaExplorerService()
        project = self._project_context or _load_default_project_context()
        try:
            schema = _inspect_sync(explorer, schema_name=self._schema_name)
        except Exception as exc:
            raise AIOrchestratorUnavailableError(
                f"无法解析 DatabaseSchema: {type(exc).__name__}"
            ) from exc

        # Phase 3.8.3：显式 Provider 优先（服务器端 project_id → 语义映射；
        # 异常不吞——未注册 / 配置错误 → 503，绝不静默回退其他项目语义）
        if self._semantic_provider is not None:
            from backend.app.projects.semantic_loader import (
                ProjectSemanticError,
            )

            try:
                semantic = self._semantic_provider.get(project.project_id)
            except AIOrchestratorError:
                raise
            except ProjectSemanticError as exc:
                raise AIOrchestratorUnavailableError(
                    f"项目 {project.project_id!r} 的业务语义不可用: {exc}"
                ) from exc
            except Exception as exc:  # 自定义 Provider 的其它异常
                raise AIOrchestratorUnavailableError(
                    f"Semantic Provider 解析失败: {type(exc).__name__}"
                ) from exc
            return project, schema, semantic

        # 旧路径（兼容）：Loader 文件名约定；失败回退空语义
        loader = self._semantic_loader or ProjectSemanticLoader()
        try:
            semantic = loader.load(project.project_id)
        except Exception as exc:
            logger.info(
                "ProjectSemantic 加载失败，使用空语义: %s", type(exc).__name__
            )
            semantic = ProjectSemantic()
        return project, schema, semantic


# ============================================================
# 协议
# ============================================================

class AIOrchestrator(Protocol):
    """AI Orchestrator 协议（Phase 3.7.9）。"""

    async def execute(
        self,
        question: str,
        *,
        context: str | None = None,
    ) -> AIOrchestrationResult: ...


# ============================================================
# 实现
# ============================================================

class AIOrchestratorService:
    """端到端执行编排器：复用现有能力，不实现任何新 AI 行为。"""

    def __init__(
        self,
        *,
        router: AIRouter | None = None,
        rag_service: Any | None = None,
        tool_registry: ToolRegistry | None = None,
        text_to_sql: TextToSQLGenerator | None = None,
        sql_executor: SQLExecutor | None = None,
        table_selector: RelevantTableSelector | None = None,
        context_composer: DatabaseContextComposer | None = None,
        semantic_serializer: BusinessSemanticSerializer | None = None,
        semantic_filter: SemanticSchemaFilter | None = None,
        project_context_provider: ProjectContextProvider | None = None,
        capabilities: ProjectCapabilities | None = None,
        knowledge_scope: ProjectKnowledgeScope | None = None,
        tool_execution_service: ToolExecutionService | None = None,
        tool_execution_observer: ToolExecutionObserver | None = None,
        tool_argument_extractor: ToolArgumentExtractor | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> None:
        """构造 Orchestrator（全部依赖可注入，**不**创建基础设施）。

        Args:
            capabilities: Phase 3.8.2 —— 该项目的能力配置（执行前硬校验）。
                          ``None`` = 不限制（默认 Orchestrator / 旧行为）；
                          工厂 ``build_orchestrator_for_project`` 总是传入
                          项目注册条目中的 capabilities。
            knowledge_scope: Phase 3.8.4 —— 该项目的知识检索范围
                          （frozen DTO，由 Factory 通过服务器端
                          ProjectKnowledgeProvider 解析后注入）。
                          Orchestrator 只负责把它传递给 RAG，
                          **不访问 Knowledge DB / 不读 Registry**。
                          ``None`` = 旧行为（不带 scope 的全局 RAG，
                          默认 Orchestrator / knowledge_enabled=False）。
            tool_execution_service:
                          Phase 3.11 Step 2 —— Tool 执行边界。
                          ``None`` 且存在 Tool Registry 时自动构造默认
                          实例（capabilities / project_id / observer 同步
                          注入）；``None`` 且无 Registry → 保持 None
                          （_run_tool 先抛 "Tool registry 未配置"）。
            tool_execution_observer:
                          Phase 3.11 Step 18 —— **可选**执行观测出口
                          （``ToolExecutionObserver`` Protocol：只接收
                          ``ToolExecutionRecord``）。``None`` = 旧行为
                          （边界不产生 Record）。
                          只用于**构造默认执行边界**；显式注入
                          ``tool_execution_service`` 时，observer 应由
                          调用方装配在该边界上（两个来源同时提供 →
                          ``AIOrchestratorInputError``，避免静默失效）。
                          本模块不创建 Collector / 不聚合 metrics。
            tool_argument_extractor:
                          Phase 3.11 Step 6 —— Tool 参数提取边界。
                          ``None`` 时构造默认 ``ToolArgumentExtractor``
                          （无状态 / 无外部依赖）；显式注入用于测试 /
                          项目自定义。Orchestrator 只调用其
                          ``extract(tool_name, question, parameters=...)``。
        """
        self._router = router if router is not None else AIRouterService()
        # Phase 3.7.13：rag_service 缺省改为真实 RagService（懒加载默认实例），
        # 修复 Phase 3.7.11 留下的"RAG 路径不可达"接线 bug。
        # 保持 ``None`` 显式注入依然有效（用于测试或项目自定义场景）。
        # 延迟 import：避免 ai_orchestrator_service → rag_service 的循环依赖
        # （RagService 内部不依赖 Orchestrator，但模块级加载顺序仍要保持稳定）。
        if rag_service is None:
            from backend.app.services.rag_service import RagService

            rag_service = RagService()
        self._rag = rag_service
        self._tools = tool_registry
        self._text_to_sql = (
            text_to_sql if text_to_sql is not None else TextToSQLService()
        )
        self._sql_executor = (
            sql_executor if sql_executor is not None else SQLExecutorService()
        )
        self._table_selector = (
            table_selector
            if table_selector is not None
            else RuleBasedRelevantTableSelector()
        )
        self._context_composer = (
            context_composer
            if context_composer is not None
            else DatabaseContextComposer()
        )
        # Phase 3.9.1：业务语义单独序列化，进入 TextToSQLContext.business_context
        self._semantic_serializer = (
            semantic_serializer
            if semantic_serializer is not None
            else BusinessSemanticSerializer()
        )
        # Phase 3.9.2：语义 → 当前 Schema + 本次 selected tables 对齐过滤
        self._semantic_filter = (
            semantic_filter
            if semantic_filter is not None
            else SemanticSchemaFilter()
        )
        self._project_provider = (
            project_context_provider
            if project_context_provider is not None
            else DefaultProjectContextProvider()
        )
        # Phase 3.8.2：None = 不限制（默认 Orchestrator 旧行为）
        if capabilities is not None and not isinstance(
            capabilities, ProjectCapabilities
        ):
            raise AIOrchestratorInputError(
                "capabilities 必须是 ProjectCapabilities 实例或 None"
                f"（当前: {type(capabilities).__name__}）"
            )
        self._capabilities = capabilities
        # Phase 3.8.4：项目知识 scope（frozen DTO；None = 旧行为）
        if knowledge_scope is not None and not isinstance(
            knowledge_scope, ProjectKnowledgeScope
        ):
            raise AIOrchestratorInputError(
                "knowledge_scope 必须是 ProjectKnowledgeScope 实例或 None"
                f"（当前: {type(knowledge_scope).__name__}）"
            )
        self._knowledge_scope = knowledge_scope
        if isinstance(max_rows, bool) or not isinstance(max_rows, int):
            raise AIOrchestratorInputError(
                f"max_rows 必须是整数（当前: {type(max_rows).__name__}）"
            )
        if max_rows < 1:
            raise AIOrchestratorInputError(
                f"max_rows 必须 >= 1（当前: {max_rows}）"
            )
        self._max_rows = max_rows
        # Phase 3.11 Step 18：可选观测出口（``ToolExecutionObserver`` Protocol）。
        # 本模块只**注入**它，不创建 Collector / 不读取 Record / 不聚合 /
        # 不持久化；形状校验与 ToolExecutionService 一致（失败更早）。
        if tool_execution_observer is not None and not callable(
            getattr(tool_execution_observer, "on_execution", None)
        ):
            raise AIOrchestratorInputError(
                "tool_execution_observer 必须提供可调用的 on_execution()"
                f"（当前: {type(tool_execution_observer).__name__}）"
            )
        if (
            tool_execution_service is not None
            and tool_execution_observer is not None
            and getattr(tool_execution_service, "observer", None)
            is not tool_execution_observer
        ):
            raise AIOrchestratorInputError(
                "已显式注入 tool_execution_service 时，observer 应由该边界持有"
                "（不要同时提供两个来源，避免观测静默失效）"
            )
        self._tool_execution_observer = tool_execution_observer
        # Phase 3.11 Step 2：Tool 执行边界（capability 校验 + Registry 执行）。
        # - 显式注入优先（测试 / 项目自定义；其 observer 由调用方装配）；
        # - 未注入且存在 Tool Registry → 构造默认实例（capabilities /
        #   project_id / observer 同步注入，保持既有 403 错误语义）；
        # - tool_registry=None（旧行为）→ 保持 None，_run_tool 仍先抛
        #   "Tool registry 未配置"。
        self._tool_execution: ToolExecutionService | None = (
            tool_execution_service
            if tool_execution_service is not None
            else (
                ToolExecutionService(
                    registry=self._tools,
                    capabilities=self._capabilities,
                    project_id=self._capability_project_id(),
                    observer=self._tool_execution_observer,
                )
                if self._tools is not None
                else None
            )
        )
        # Phase 3.11 Step 6：Tool 参数提取边界（Question + tool_name → arguments）。
        # 无状态纯确定性组件；不持有 ToolRegistry / DB / LLM / Router 能力。
        # 显式注入优先（测试 / 项目自定义），否则构造默认实例。
        if tool_argument_extractor is not None and not callable(
            getattr(tool_argument_extractor, "extract", None)
        ):
            raise AIOrchestratorInputError(
                "tool_argument_extractor 必须提供可调用的 extract()"
                f"（当前: {type(tool_argument_extractor).__name__}）"
            )
        self._argument_extractor: ToolArgumentExtractor = (
            tool_argument_extractor
            if tool_argument_extractor is not None
            else ToolArgumentExtractor()
        )

    # ---------- 只读暴露（便于测试断言注入关系） ----------

    @property
    def tool_execution_observer(self) -> ToolExecutionObserver | None:
        """本 Orchestrator 注入的观测出口（None = 不产生 Record）。"""
        return self._tool_execution_observer

    # ---------- 主入口 ----------

    async def execute(
        self,
        question: str,
        *,
        context: str | None = None,
    ) -> AIOrchestrationResult:
        if not isinstance(question, str):
            raise AIOrchestratorInputError(
                f"question 必须是 str（当前: {type(question).__name__}）"
            )
        normalized = question.strip()
        if not normalized:
            raise AIOrchestratorInputError("question 不能为空或纯空白")

        # Phase 3.11 Step 18：一次 execute() 一个 request_id（观测关联 ID；
        # 复用既有 new_request_id()，不新建第二套 ID 体系；不进 Tool
        # arguments / 不进 LLM messages）。
        # Phase 3.12 Step 35：该 request_id 是**唯一** Assistant Trace ID ——
        # 随三条成功路径的 ``metadata["request_id"]`` 透出（RAG / Tool /
        # Text-to-SQL），使调用方可用它关联 Tool Execution Observability；
        # 由本方法生成（API 层不生成第二个 ID）。
        request_id = new_request_id()

        # ---- 1) 路由决策 ----
        # Phase 3.12 Step 36：整个执行（含 Router 的 LLM fallback、RAG /
        # Tool / Text-to-SQL 内部的 LLM 调用）都在同一个 Assistant Trace
        # Scope 内 —— LLM Usage 持久化因此可以把 usage 事实关联到本
        # request_id（不改 LLM Client / RAG / Text-to-SQL 参数签名）。
        with assistant_trace_scope(request_id):
            try:
                decision: RouteDecision = await self._router.route(
                    question=normalized, context=context
                )
            except AIRouterInputError as exc:
                raise AIOrchestratorInputError(str(exc)) from exc
            except AIRouterError as exc:
                raise AIOrchestratorRouteError(
                    f"Router 决策失败: {type(exc).__name__}"
                ) from exc

            # ---- 2) 单次执行（无 Agent / 无 Loop / 无重规划） ----
            try:
                if decision.route == RouteType.RAG:
                    return await self._run_rag(
                        decision, normalized, request_id=request_id
                    )
                if decision.route == RouteType.TOOL:
                    return await self._run_tool(
                        decision, normalized, request_id=request_id
                    )
                if decision.route == RouteType.TEXT_TO_SQL:
                    return await self._run_text_to_sql(
                        decision, normalized, request_id=request_id
                    )
            except AIOrchestratorError:
                raise
            except Exception as exc:
                raise AIOrchestratorExecutionError(
                    f"能力执行失败: {type(exc).__name__}"
                ) from exc

            raise AIOrchestratorRouteError(
                f"未知 route: {decision.route!r}"
            )

    # ---------- Phase 3.8.2：能力硬校验（Router 是分类器，不是安全边界） ----------

    def _check_capability(self, capability: str) -> None:
        """执行前硬校验；被禁用 → AIOrchestratorCapabilityError。

        Args:
            capability: "knowledge" / "text_to_sql"。

        Phase 3.11 Step 2：**Tool 名称白名单校验已迁移至
        ``ToolExecutionService._check_capability``**（执行边界内校验，
        Handler 0 次调用即拒绝；异常类型 / capability / project_id /
        HTTP 403 语义完全不变）。本方法只保留 Orchestrator 自身
        能力（RAG / Text-to-SQL）的校验。
        """
        if self._capabilities is None:
            return  # 默认 Orchestrator：不限制（旧行为）
        if capability == "knowledge" and not self._capabilities.knowledge_enabled:
            raise AIOrchestratorCapabilityError(
                "该项目未启用知识库（RAG）能力",
                capability=capability,
                project_id=self._capability_project_id(),
            )
        if capability == "text_to_sql" and not (
            self._capabilities.text_to_sql_enabled
        ):
            raise AIOrchestratorCapabilityError(
                "该项目未启用 Text-to-SQL 能力",
                capability=capability,
                project_id=self._capability_project_id(),
            )

    def _capability_project_id(self) -> str | None:
        """能力校验错误中携带的 project_id（尽力而为，不触发解析）。"""
        provider = self._project_provider
        context = getattr(provider, "_project_context", None)
        return getattr(context, "project_id", None)

    # ---------- RAG 路径 ----------

    async def _run_rag(
        self,
        decision: RouteDecision,
        question: str,
        *,
        request_id: str,
    ) -> AIOrchestrationResult:
        """RAG 路径（Phase 3.12 Step 35：metadata 携带本次 trace request_id）。

        ``request_id`` 由 ``execute()`` 生成并透传（本方法不生成 ID ——
        保证 API / ToolExecutionRecord / metadata 三处同一 ID）。
        """
        # Phase 3.8.2：能力硬校验（RagService 0 次调用）
        self._check_capability("knowledge")
        if self._rag is None:
            raise AIOrchestratorExecutionError("RAG service 未配置")
        try:
            # Phase 3.8.4：把项目知识 scope 传给 RAG（Orchestrator 只传递
            # 上下文，不访问 Knowledge DB）。scope=None → 旧调用形态
            # （兼容既有 Fake RagService 的 answer(query, *, top_k) 签名）。
            if self._knowledge_scope is None:
                rag_response = await self._rag.answer(question)
            else:
                rag_response = await self._rag.answer(
                    question, knowledge_scope=self._knowledge_scope
                )
        except Exception as exc:
            raise AIOrchestratorExecutionError(
                f"RAG 执行失败: {type(exc).__name__}"
            ) from exc
        # Phase 3.12 Step 63：Assistant Outcome（Orchestrator 层判定）
        # —— 仅使用白名单信号（route / rag_used_chunks），不做 content 匹配
        rag_used_chunks = getattr(rag_response, "used_chunks_count", None)
        outcome = determine_assistant_outcome(
            route=RouteType.RAG.value,
            rag_used_chunks=rag_used_chunks,
        )
        return AIOrchestrationResult(
            route=RouteType.RAG,
            content=getattr(rag_response, "answer", None),
            data=rag_response,
            metadata={
                "decision_source": decision.source,
                "route_reason": decision.reason,
                # Phase 3.8.4：回显实际使用的知识 scope（非敏感，仅 namespace）
                "knowledge_scope": (
                    self._knowledge_scope.namespace
                    if self._knowledge_scope is not None
                    else None
                ),
                "rag_used_chunks": rag_used_chunks,
                # Phase 3.12 Step 35：Assistant Trace ID（非敏感；仅关联用）
                "request_id": request_id,
                # Phase 3.12 Step 63：Assistant-level 业务结果（4 态固定）
                "outcome": outcome,
            },
        )

    # ---------- Tool 路径 ----------

    async def _run_tool(
        self,
        decision: RouteDecision,
        question: str,
        *,
        request_id: str,
    ) -> AIOrchestrationResult:
        if self._tools is None:
            raise AIOrchestratorExecutionError("Tool registry 未配置")
        # Phase 3.11 Step 3：Tool 选择唯一来源 = Router 的 RouteDecision。
        # Orchestrator **不**做二次选择（原 _resolve_tool_name 已删除）；
        # decision 未携带 Tool 名称时显式拒绝（不猜、不回退其它 Tool、
        # 不重新调用 LLM）。
        tool_name = decision.tool_name
        if not tool_name:
            raise AIOrchestratorRouteError(
                "TOOL 路由未携带 Tool 名称（Router 未选择具体 Tool）"
            )

        # ---- Phase 3.11 Step 6：参数提取边界 ----
        # Orchestrator 只负责把「Router 选中的 tool_name + 用户问题 + Tool
        # 声明的字段」交给 ToolArgumentExtractor；正则 / 字符区间 /
        # material interval exclusion 等提取实现全部属于 Extractor
        # （本模块内已删除，0 处重复）。
        # - Tool 名称来自 Router 的 RouteDecision（唯一选择来源，Step 3）
        # - 提取结果仅含命中的字段（缺失字段不补；Schema 校验仍在 Registry）
        # - Tool 未注册（无 definition）→ 不传 parameters：Extractor 按
        #   Tool 名称规则表处理，未知 Tool 返回 {}，由 Registry 返回
        #   "Tool 未注册"（不猜 Tool、不 fallback 其它 Tool）
        try:
            definition = self._tools.get_definition(tool_name)
        except Exception:  # noqa: BLE001
            definition = None
        arguments: dict[str, Any] = self._argument_extractor.extract(
            tool_name,
            question,
            parameters=(
                definition.parameters if definition is not None else None
            ),
        )

        # ---- Phase 3.11 Step 2：执行边界（capability 校验 + Registry 执行）----
        # Phase 3.8.2 的 Tool 能力硬校验已随执行迁移至
        # ToolExecutionService（Handler 0 次调用即拒绝；异常类型 /
        # capability / project_id / 403 语义完全不变）。
        # 正常情况下 Router 已看不到被禁用的 Tool（工厂过滤了
        # capability 元数据），此处是纵深防御的第二层。
        execution = self._tool_execution
        if execution is None:  # 理论不可达：_tools 非 None 时构造已注入
            raise AIOrchestratorExecutionError("Tool 执行边界未配置")

        # ---- Phase 3.11 Step 18：Execution Context（观测 / 关联用） ----
        # * request_id：一次 execute 一个（由 execute() 创建；本方法不生成）；
        # * round：one execute = one Tool（无 round loop / 无 multi-step /
        #   无 retry）；
        # * project_id：**唯一权威** = 执行边界的授权作用域（服务器端配置
        #   解析结果）—— 不来自 question、不来自 Tool arguments、
        #   不来自 LLM；与边界作用域天然一致（边界亦做一致性校验）；
        # * tool_call_id：本链路不是 Function Calling round（Tool 名称由
        #   Router 决策给出）→ ``None``，**不伪造** call id。
        tool_context = ToolExecutionContext(
            request_id=request_id,
            round=1,
            project_id=getattr(execution, "project_id", None),
            tool_call_id=None,
        )
        try:
            tool_result: ToolResult = await execution.execute(
                tool_name, arguments=arguments, context=tool_context
            )
        except AIOrchestratorCapabilityError:
            # 能力拒绝：保持 403 语义，不被下方 ExecutionError 包装
            raise
        except ToolError as exc:
            raise AIOrchestratorExecutionError(
                f"Tool 执行失败: {type(exc).__name__}"
            ) from exc
        except Exception as exc:
            raise AIOrchestratorExecutionError(
                f"Tool 执行失败: {type(exc).__name__}"
            ) from exc
        content = _tool_result_to_content(tool_result)
        # Phase 3.12 Step 63：Tool 业务失败（HTTP 仍为 200）⇒ outcome=FAILED
        # （HTTP 200 ≠ 业务成功；判定只依赖 tool_success，不读 content 文本）
        tool_outcome = determine_assistant_outcome(
            route=RouteType.TOOL.value,
            tool_success=tool_result.success,
        )
        return AIOrchestrationResult(
            route=RouteType.TOOL,
            content=content,
            data=tool_result,
            metadata={
                "decision_source": decision.source,
                "route_reason": decision.reason,
                "tool_name": tool_name,
                "tool_success": tool_result.success,
                # Phase 3.12 Step 35：Assistant Trace ID —— 与本次
                # ToolExecutionRecord.request_id 完全一致（同一 ID，
                # 不新建第二套）；不含 arguments / ToolResult.data。
                "request_id": request_id,
                # Phase 3.12 Step 63：Assistant-level 业务结果（4 态固定）
                "outcome": tool_outcome,
            },
        )

    # ---------- Text-to-SQL 路径 ----------

    async def _run_text_to_sql(
        self,
        decision: RouteDecision,
        question: str,
        *,
        request_id: str,
    ) -> AIOrchestrationResult:
        """Text-to-SQL 路径（Phase 3.12 Step 35：metadata 携带 trace id）。

        ``request_id`` 由 ``execute()`` 生成并透传（本方法不生成 ID）；
        refused 结果同样携带（成功响应语义，非错误响应）。
        """
        # Phase 3.8.2：能力硬校验 —— 在解析 ProjectContext / inspect
        # Schema / 生成 / 执行 SQL **之前**拦截（0 次数据库访问）。
        self._check_capability("text_to_sql")

        # a) Project context（Phase 3.8.1 修复：to_thread 包裹）
        #    历史 bug：直接在事件循环内同步调用 resolve()，而
        #    DefaultProjectContextProvider.resolve 内部用 asyncio.run
        #    桥接 async SchemaExplorer → "asyncio.run() cannot be called
        #    from a running event loop" RuntimeError。Provider docstring
        #    一直声明"由 to_thread 包裹"，本阶段按声明补上（最小修复，
        #    不改变任何业务逻辑）。
        try:
            ctx = await asyncio.to_thread(self._project_provider.resolve)
        except AIOrchestratorUnavailableError:
            raise
        except Exception as exc:
            raise AIOrchestratorUnavailableError(
                f"项目上下文解析失败: {type(exc).__name__}"
            ) from exc
        project, schema, semantic = ctx

        # b) 相关表选择（同步）
        selection: TableSelectionResult = self._table_selector.select(
            question, schema=schema, semantic=semantic, top_k=10
        )
        allowed_tables = tuple(s.table for s in selection.selections)

        # c) 数据库上下文组装（Phase 3.9.1：三层上下文结构化）
        #    1) database_context = Database Schema 事实（Composer 只出事实）
        #    2) business_context = 业务语义（独立段，Schema 不覆盖语义、
        #       语义也不覆盖 Schema，由 Prompt 明确优先级）
        #    3) SQL Constraints = Prompt 指令（真正拦截仍由 SQLValidator）
        database_context = self._context_composer.compose(
            project=project,
            schema=schema,
            semantic=None,
            tables=allowed_tables or None,
        )
        #    2') Phase 3.9.2：Semantic 先按"当前 Schema + 本次 selected
        #        tables"对齐过滤——Schema 是事实来源，Semantic 只解释可见表；
        #        不存在 / 未选中的表、字段与关系一律删除（绝不回推 Schema）。
        filtered_semantic = self._semantic_filter.filter(
            semantic, schema, allowed_tables=allowed_tables
        )
        business_context = (
            self._semantic_serializer.serialize(filtered_semantic) or None
        )
        generation_context = TextToSQLContext(
            database_context=database_context,
            business_context=business_context,
            allowed_tables=allowed_tables,
            max_rows=self._max_rows,
            project_id=project.project_id,
        )

        # d) 生成 SQL（Generator 内部已调 Validator）
        #    注：generate() 契约保持 Phase 3.7.6 原样（不新增参数），
        #    结构化上下文经 render() 渲染为单段文本传入。
        try:
            sql_result = await self._text_to_sql.generate(
                question,
                database_context=generation_context.render(),
                allowed_tables=generation_context.allowed_tables or None,
                schema=schema,
                max_rows=generation_context.max_rows,
            )
        except Exception as exc:
            raise AIOrchestratorExecutionError(
                f"Text-to-SQL 生成失败: {type(exc).__name__}"
            ) from exc

        # d2) Phase 3.9.25：First-Class Refusal —— LLM 明确拒绝
        #     （destructive request 等）时直接形成 Chat 层拒绝结果，
        #     **绝不进入 Executor**，也不走 Exception → 500 路径。
        #     refusal_reason 属内部语义，不放入 metadata（metadata 会
        #     透出给客户端），仅标记 refused=True。
        if getattr(
            sql_result, "status", RESULT_STATUS_SQL
        ) == RESULT_STATUS_REFUSAL:
            return AIOrchestrationResult(
                route=RouteType.TEXT_TO_SQL,
                content=TEXT_TO_SQL_REFUSAL_MESSAGE,
                data=None,
                metadata={
                    "decision_source": decision.source,
                    "route_reason": decision.reason,
                    "refused": True,
                    # Phase 3.12 Step 35：refusal 亦为成功响应 → 携带 trace id
                    "request_id": request_id,
                    # Phase 3.12 Step 63：拒绝是**预期安全行为** ⇒ REFUSED（非 FAILED）
                    "outcome": determine_assistant_outcome(
                        route=RouteType.TEXT_TO_SQL.value,
                        refused=True,
                    ),
                },
            )

        # e) 安全执行（重新验证 + READ ONLY 事务 + limits）
        try:
            execution: SQLExecutionResult = await self._sql_executor.execute(
                sql_result.sql,
                schema=schema,
                allowed_tables=allowed_tables or None,
                max_rows=self._max_rows,
            )
        except Exception as exc:
            raise AIOrchestratorExecutionError(
                f"SQL 执行失败: {type(exc).__name__}"
            ) from exc

        return AIOrchestrationResult(
            route=RouteType.TEXT_TO_SQL,
            content=_sql_result_to_content(execution, sql_result.sql),
            data=execution,
            metadata={
                "decision_source": decision.source,
                "route_reason": decision.reason,
                "sql": sql_result.sql,
                "row_count": execution.row_count,
                "truncated": execution.truncated,
                "execution_time_ms": execution.execution_time_ms,
                "selected_tables": list(allowed_tables),
                "project_id": project.project_id,
                # Phase 3.12 Step 35：Assistant Trace ID（非敏感；仅关联用）
                "request_id": request_id,
                # Phase 3.12 Step 63：SQL 执行成功 ⇒ SUCCESS
                # （row_count == 0 也是 SUCCESS —— 查询已给出"无匹配数据"这一有效结果；
                #  determine_assistant_outcome 不接受 row_count，从接口层面杜绝误判 EMPTY）
                "outcome": determine_assistant_outcome(
                    route=RouteType.TEXT_TO_SQL.value
                ),
            },
        )


# ============================================================
# 内部工具函数（纯函数，便于测试）
# ============================================================

# Phase 3.11 Step 6：Tool 参数提取（字面量 / 仓库 / 工单正则、字符区间、
# material interval exclusion）**已迁出**至独立组件
# ``backend/app/services/tool_argument_extractor.py``：
#
#     Question + tool_name
#         ↓ ToolArgumentExtractor.extract()
#     arguments
#         ↓ ToolExecutionService.execute()
#     ToolResult
#
# 本模块不再持有任何提取模式或提取实现（单一职责 / 单一实现位置）。
# 迁移记录与边界说明：docs/evaluation/Phase 3.11 Step 6 — Tool Argument
# Extraction Boundary.md；架构：docs/architecture.md §8.24。


def _tool_result_to_content(result: ToolResult) -> str:
    """把 ToolResult 转成给用户看的 content（不泄露内部对象）。"""
    if not result.success:
        return f"[Tool {result.tool_name} 失败] {result.error or '未知错误'}"
    data = result.data
    if isinstance(data, str):
        return data
    if isinstance(data, Mapping):
        return ", ".join(f"{k}={v}" for k, v in data.items())
    return str(data) if data is not None else ""


def _sql_result_to_content(
    execution: SQLExecutionResult, sql: str
) -> str:
    """把 SQLExecutionResult 转成只读摘要（不暴露 Row / Cursor）。"""
    return (
        f"查询返回 {execution.row_count} 行"
        f"{'（已截断）' if execution.truncated else ''}"
    )


def _inspect_sync(explorer: Any, schema_name: str | None) -> DatabaseSchema:
    """同步桥接 SchemaExplorerService.inspect（async）。

    仅在 ``resolve()``（同步上下文）内使用；Orchestrator 的 async 主路径
    通过 ``asyncio.to_thread`` 调用本函数。
    """
    try:
        return asyncio.run(explorer.inspect(schema=schema_name))
    except RuntimeError:
        # 已有 running loop 时（理论不应在 resolve() 中出现）→ 抛清晰错误
        raise AIOrchestratorUnavailableError(
            "SchemaExplorer 需在非事件循环上下文调用"
        )


def _load_default_project_context() -> ProjectContext:
    """加载默认 ProjectContext（无敏感信息）。"""
    from backend.app.config import settings
    return ProjectContext(
        project_id=settings.project.project_id,
        project_name=settings.project.project_name,
        description=settings.project.description,
        data_source=DataSource(name="primary", type="postgresql"),
    )


# ============================================================
# 默认工厂
# ============================================================

def get_default_orchestrator() -> AIOrchestratorService:
    """构造默认 Orchestrator（懒加载所有依赖，**不**创建基础设施）。"""
    return AIOrchestratorService()