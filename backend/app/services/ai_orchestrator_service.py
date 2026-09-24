"""AI Orchestrator（Phase 3.7.9）。

职责：
    把 ``AIRouter`` 的路由决策**组合**到现有三类 AI 能力上，
    形成一条端到端可测试的执行链。本模块**不**实现任何新 AI 能力：

        Question
            ↓
        AIRouter.route（Phase 3.7.8）
            ↓
        ┌───────────┼────────────────┐
        ↓           ↓                ↓
    RagService   ToolRegistry   TextToSQL
    (Phase 3.5)  (Phase 3.6.1)  ↓
                                TableSelector (3.7.4)
                                    ↓
                                DatabaseContextComposer (3.7.3)
                                    ↓
                                TextToSQLService (3.7.6)
                                    ↓
                                SQLExecutor (3.7.7)
            ↓
        AIOrchestrationResult

纪律（纵深防御）：
    ❌ 不重新实现 SQL 生成 / 校验 / 执行（Orchestrator 必须经过现有三层）
    ❌ 不实现多轮 Agent / Tool Loop / 规划（一次性：Question → ONE route）
    ❌ 不创建 LLM / Engine / Session / RAG / Tool Registry（全部注入）
    ❌ 不修改 Chat API（Router / Orchestrator / ChatService 解耦）
    ❌ 不暴露 SQLAlchemy Row / Connection / Session / API Key
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.projects.context import DataSource, ProjectContext
from backend.app.projects.semantic import ProjectSemantic
from backend.app.services.ai_router_service import (
    AIRouter,
    AIRouterError,
    AIRouterInputError,
    AIRouterService,
    RouteDecision,
    RouteType,
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
from backend.app.services.sql_validator_service import DEFAULT_MAX_ROWS
from backend.app.services.text_to_sql_service import (
    TextToSQLGenerator,
    TextToSQLService,
)
from backend.app.tools.base import ToolDefinition
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
    """生产实现：复用 ``SchemaExplorerService`` + ``ProjectSemanticLoader``。

    Orchestrator 仍**不**创建 Engine / Session，仅调用上层 Service
    暴露的方法。``resolve()`` 是同步桥接：内部用 ``asyncio.run``
    调 async inspector（Orchestrator 在 async 主路径里再 ``to_thread``
    包一层避免阻塞事件循环）。
    """

    def __init__(
        self,
        *,
        project_context: ProjectContext | None = None,
        explorer: Any = None,
        semantic_loader: Any = None,
        schema_name: str | None = None,
    ) -> None:
        self._project_context = project_context
        self._explorer = explorer
        self._semantic_loader = semantic_loader
        self._schema_name = schema_name

    def resolve(
        self,
    ) -> tuple[ProjectContext, DatabaseSchema, ProjectSemantic]:
        from backend.app.services.schema_explorer_service import (
            SchemaExplorerService,
        )
        from backend.app.projects.semantic_loader import ProjectSemanticLoader

        explorer = self._explorer or SchemaExplorerService()
        loader = self._semantic_loader or ProjectSemanticLoader()
        project = self._project_context or _load_default_project_context()
        try:
            schema = _inspect_sync(explorer, schema_name=self._schema_name)
        except Exception as exc:
            raise AIOrchestratorUnavailableError(
                f"无法解析 DatabaseSchema: {type(exc).__name__}"
            ) from exc
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
        project_context_provider: ProjectContextProvider | None = None,
        capabilities: ProjectCapabilities | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> None:
        """构造 Orchestrator（全部依赖可注入，**不**创建基础设施）。

        Args:
            capabilities: Phase 3.8.2 —— 该项目的能力配置（执行前硬校验）。
                          ``None`` = 不限制（默认 Orchestrator / 旧行为）；
                          工厂 ``build_orchestrator_for_project`` 总是传入
                          项目注册条目中的 capabilities。
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
        if isinstance(max_rows, bool) or not isinstance(max_rows, int):
            raise AIOrchestratorInputError(
                f"max_rows 必须是整数（当前: {type(max_rows).__name__}）"
            )
        if max_rows < 1:
            raise AIOrchestratorInputError(
                f"max_rows 必须 >= 1（当前: {max_rows}）"
            )
        self._max_rows = max_rows

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

        # ---- 1) 路由决策 ----
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
                return await self._run_rag(decision, normalized)
            if decision.route == RouteType.TOOL:
                return await self._run_tool(decision, normalized)
            if decision.route == RouteType.TEXT_TO_SQL:
                return await self._run_text_to_sql(decision, normalized)
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
            capability: "knowledge" / "text_to_sql" / Tool 名称。
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
        if (
            capability not in ("knowledge", "text_to_sql")
            and not self._capabilities.allows_tool(capability)
        ):
            raise AIOrchestratorCapabilityError(
                f"Tool {capability!r} 未在该项目启用",
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
        self, decision: RouteDecision, question: str
    ) -> AIOrchestrationResult:
        # Phase 3.8.2：能力硬校验（RagService 0 次调用）
        self._check_capability("knowledge")
        if self._rag is None:
            raise AIOrchestratorExecutionError("RAG service 未配置")
        try:
            rag_response = await self._rag.answer(question)
        except Exception as exc:
            raise AIOrchestratorExecutionError(
                f"RAG 执行失败: {type(exc).__name__}"
            ) from exc
        return AIOrchestrationResult(
            route=RouteType.RAG,
            content=getattr(rag_response, "answer", None),
            data=rag_response,
            metadata={
                "decision_source": decision.source,
                "route_reason": decision.reason,
                "rag_used_chunks": getattr(
                    rag_response, "used_chunks_count", None
                ),
            },
        )

    # ---------- Tool 路径 ----------

    async def _run_tool(
        self, decision: RouteDecision, question: str
    ) -> AIOrchestrationResult:
        if self._tools is None:
            raise AIOrchestratorExecutionError("Tool registry 未配置")
        tool_name = _resolve_tool_name(question, self._tools)
        if tool_name is None:
            raise AIOrchestratorRouteError(
                "TOOL 路由未命中任何已注册 Tool"
            )
        # Phase 3.8.2：能力硬校验（Handler 0 次调用）。
        # 正常情况下 Router 已看不到被禁用的 Tool（工厂过滤了
        # capability 元数据），此处是纵深防御的第二层。
        self._check_capability(tool_name)

        # ---- Phase 3.7.12 最小兼容性 layer ----
        # 历史行为：_run_tool 向 registry.execute() 传入 arguments=None，
        # 导致 Handler 收到 {}；真实 Tool 无法从空 arguments 提取 material_code。
        # 本阶段：从 ToolDefinition.parameters 中按字段名做"关键字命中 + 兜底提取"，
        # 将构造好的 arguments 传给 Handler。
        # - 保留 routes 路由语义（_resolve_tool_name 仍然走原有流程）
        # - 保留 ToolRegistry.execute() 现有签名
        # - 仅在字段无值时不传递该字段（Handler 仍可校验失败 → ToolResult(success=False)）
        try:
            definition = self._tools.get_definition(tool_name)
        except Exception:  # noqa: BLE001
            definition = None
        arguments: dict[str, Any] | None = (
            _extract_tool_arguments_from_question(question, definition)
            if definition is not None
            else None
        )

        try:
            tool_result: ToolResult = await self._tools.execute(
                tool_name, arguments=arguments
            )
        except ToolError as exc:
            raise AIOrchestratorExecutionError(
                f"Tool 执行失败: {type(exc).__name__}"
            ) from exc
        except Exception as exc:
            raise AIOrchestratorExecutionError(
                f"Tool 执行失败: {type(exc).__name__}"
            ) from exc
        content = _tool_result_to_content(tool_result)
        return AIOrchestrationResult(
            route=RouteType.TOOL,
            content=content,
            data=tool_result,
            metadata={
                "decision_source": decision.source,
                "route_reason": decision.reason,
                "tool_name": tool_name,
                "tool_success": tool_result.success,
            },
        )

    # ---------- Text-to-SQL 路径 ----------

    async def _run_text_to_sql(
        self, decision: RouteDecision, question: str
    ) -> AIOrchestrationResult:
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

        # c) 数据库上下文组装
        database_context = self._context_composer.compose(
            project=project,
            schema=schema,
            semantic=semantic,
            tables=allowed_tables or None,
        )

        # d) 生成 SQL（Generator 内部已调 Validator）
        try:
            sql_result = await self._text_to_sql.generate(
                question,
                database_context=database_context,
                allowed_tables=allowed_tables or None,
                schema=schema,
                max_rows=self._max_rows,
            )
        except Exception as exc:
            raise AIOrchestratorExecutionError(
                f"Text-to-SQL 生成失败: {type(exc).__name__}"
            ) from exc

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
            },
        )


# ============================================================
# 内部工具函数（纯函数，便于测试）
# ============================================================

def _tokenize(text_lower: str) -> list[str]:
    """极简分词：英文 / 数字按连续字符；中文按 2-gram。

    兼容：
        "查询物料 库存 数量 ABC" → ["查询", "物料", "库存", "数量", "abc"]
    """
    out: list[str] = []
    out.extend(re.findall(r"[a-z0-9]+", text_lower))
    for block in re.findall(r"[\u4e00-\u9fff]+", text_lower):
        for i in range(len(block) - 1):
            out.append(block[i:i + 2])
    return out

def _resolve_tool_name(
    question: str, registry: ToolRegistry
) -> str | None:
    """根据 question 文本从 ToolCapability 选 1 个 Tool。

    复用 AIRouter 的 capability 匹配思路：先比 aliases（精确包含），
    再比 description 关键词。无注册 Tool 或无命中 → None。
    """
    try:
        definitions: tuple[ToolDefinition, ...] = registry.list_definitions()
    except Exception:
        return None
    if not definitions:
        return None

    # 1) aliases / 参数 properties 字段命中
    for d in definitions:
        params = getattr(d, "parameters", {}) or {}
        aliases = list((params.get("properties") or {}).keys())
        for alias in aliases:
            if isinstance(alias, str) and alias and alias in question:
                return d.name
    # 2) description 关键词命中（英文按空格，中文 2-gram）
    q_lower = question.lower()
    for d in definitions:
        desc = (getattr(d, "description", "") or "").lower()
        for token in _tokenize(desc):
            if token in q_lower:
                return d.name
    return None


# ============================================================
# Phase 3.7.12 — Tool 参数最小提取器
# ============================================================
#
# 历史：_run_tool 调用 ``registry.execute(tool_name, arguments=None)``，
#       Handler 收到 ``{}``；对真实业务 Tool（如 get_inventory）来说，
#       无法从空 arguments 取得用户输入。
#
# 本阶段最小修改：从 ToolDefinition.parameters["properties"] 按字段名
#       + 一个保守的字面量提取器抽取（最常见是 ``material_code``）。
#
# 设计纪律：
# - **不**修改 routes 路由语义：仍然走 _resolve_tool_name 旧路径
# - **不**修改 ToolRegistry.execute() 现有签名
# - 仅当字段名是 "material_code"（与 Phase 3.7.12 一致）才提取；
#   其它字段（如未来 warehouse_code）扩展时再增加，不提前实现
# - 提取失败（无匹配字面量）→ 字段不出现在 arguments 中；
#   Tool 参数 Schema 校验失败 → ToolResult(success=False)
# - 提取的值**不**进入 SQL 拼接，仅作为 Tool 入参（Tool 内部会再次校验）

_TOOL_ARG_LITERAL_PATTERN: re.Pattern[str] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._\-]{0,63}"
)


def _extract_tool_arguments_from_question(
    question: str,
    definition: ToolDefinition | None,
) -> dict[str, Any] | None:
    """从 question 中按 Tool 参数 properties 提取（Phase 3.7.12）。

    当前支持：
        * ``material_code``：从 question 中匹配第一个合法字面量
                              （字母 / 数字 / dash / dot / underscore，长度 ≤ 64）。

    Args:
        question: 用户问题（已 strip 过）。
        definition: 已选中的 ToolDefinition；None → 返回 None。

    Returns:
        dict 或 None。
            - dict：提取到的字段（仅含确实匹配到的字段）
            - None：definition 为空（不传递 arguments，让 Handler 默认空）
    """
    if definition is None:
        return None
    properties = (getattr(definition, "parameters", {}) or {}).get("properties") or {}
    if not properties:
        return None

    out: dict[str, Any] = {}

    # material_code：取 question 中第一个合法字面量
    if "material_code" in properties:
        match = _TOOL_ARG_LITERAL_PATTERN.search(question)
        if match is not None:
            candidate = match.group(0)
            if candidate.strip():
                out["material_code"] = candidate

    # 其它字段（如 warehouse_code / work_order_no）暂不提取；
    # 扩展新 Tool 时按需增加。

    return out or None


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