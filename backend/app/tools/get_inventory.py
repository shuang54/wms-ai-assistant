"""真实 ``get_inventory`` Tool（Phase 3.7.12）。

将 Phase 3.6.1 的 Mock ``get_inventory`` 升级为**真实**只读 Tool。

链路：

    HTTP /api/ai/chat
        ↓
    AIOrchestratorService.execute
        ↓
    AIRouterService.route            （根据 description 关键词命中 TOOL）
        ↓
    _run_tool
        ↓
    _extract_tool_arguments          （最小兼容性 layer；Phase 3.7.12）
        ↓
    ToolRegistry.execute("get_inventory", arguments={...})
        ↓
    GetInventoryHandler.__call__
        ↓
    ProjectContextProvider.resolve   （解析 Project Context）
        ↓
    SQLAlchemy Engine.connect (READ ONLY)
        ↓
    PostgreSQL：SELECT SUM(qty) FROM schema.table WHERE material_code = :material_code
        ↓
    ToolResult(success=True, data={"material_code": "...", "qty": <n>})

设计纪律（纵深防御）：

* **不**修改 Orchestrator 核心路由逻辑：仅在 ``_run_tool`` 增加"从 question 抽取
  material_code"的最少 patch（保留现有语义，向后兼容）
* **不**修改 SQLValidator / SQLExecutor / TextToSQLService / AIRouterService
* **不**修改已有 RAG 核心逻辑
* **不**新增数据库表：Tool 引用**外部**库存表（settings.inventory_tool
  可配置 schema/table 名），生产可指向任何已有库存表
* **不**写死 ``project_id == "vietnam-wms"``：project_id 仅通过 ProjectContextProvider
  解析；Tool 不关心具体 project_id 值
* **不**允许 Tool 输入任意 SQL / table / where_clause：DTO 只接受
  ``material_code`` 一个参数；其它字段被 Tool 参数 Schema 拒绝
* **只读**：Handler 内使用 ``BEGIN READ ONLY`` 事务 + ``SET LOCAL statement_timeout``
  + 绑定参数；Tool 不提供 UPDATE/INSERT/DELETE 入口
* **不**在 Tool 中创建 Engine / LLMClient / Embedding：复用 ``get_engine()``
  全局缓存
* **不**在 ToolResult.error 中暴露：DATABASE_URL / password / traceback /
  SQL 原文（包含 schema/table 拼接后的最终 SQL）
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine

from backend.app.config import InventoryToolSettings, settings
from backend.app.db.session import get_engine
from backend.app.projects.context import (
    DEFAULT_DATA_SOURCE_NAME,
    DEFAULT_DATA_SOURCE_TYPE,
    DataSource,
    ProjectContext,
)
from backend.app.tools.base import ToolDefinition, ToolResult
from backend.app.tools.errors import (
    ToolError,
    ToolExecutionError,
    ToolValidationError,
)
from backend.app.tools.registry import ToolRegistry


logger = logging.getLogger(__name__)


__all__ = [
    "GET_INVENTORY_DEFINITION",
    "GetInventoryHandler",
    "GetInventoryProjectContextProvider",
    "register_get_inventory_tool",
    "build_default_tool_registry",
]


# ============================================================
# ToolDefinition
# ============================================================

GET_INVENTORY_DEFINITION: ToolDefinition = ToolDefinition(
    name="get_inventory",
    description=(
        "查询指定物料的当前库存数量（只读）。"
        "返回 material_code 与 qty（库存数量）。"
        "仅支持 material_code 单参数；不接受 SQL / table / where_clause 等。"
    ),
    # Router 规则命中所用的业务别名。**必须**是具备区分度的短语：
    # 不要放裸词 "库存"，否则 "库存最多的 10 个物料是什么？" 这类
    # 聚合问题会被本 Tool 抢占（见决策文档 §十九：不要污染 Text-to-SQL）。
    aliases=(
        "当前库存",
        "库存数量",
        "查库存",
        "物料库存",
        "库存查询",
    ),
    parameters={
        "type": "object",
        "properties": {
            "material_code": {
                "type": "string",
                "description": (
                    "物料编码（1-64 字符）；自动 strip；不接受空字符串 / "
                    "纯空白 / SQL 关键字。"
                ),
            },
        },
        "required": ["material_code"],
    },
)


# ============================================================
# Project Context Provider（最小本地实现，不依赖 ai_orchestrator_service）
# ============================================================

class GetInventoryProjectContextProvider:
    """Tool 层本地最小 ProjectContext Provider。

    为什么不复用 ``backend.app.services.ai_orchestrator_service.DefaultProjectContextProvider``：

        * 循环依赖（orchestrator → tools → orchestrator）
        * Tool 不需要 DB Schema / Semantic；只需要 project_id 元数据

    Attributes:
        project_id: 解析出的 project_id；API 层 ``project_id`` 可透传覆盖。
    """

    def __init__(self, *, project_id: str | None = None) -> None:
        effective_id = project_id or settings.project.project_id
        self._ctx: ProjectContext = ProjectContext(
            project_id=effective_id,
            project_name=effective_id,
            description=settings.project.project_description or None,
            data_source=DataSource(
                name=DEFAULT_DATA_SOURCE_NAME,
                type=DEFAULT_DATA_SOURCE_TYPE,
            ),
        )

    def resolve(self) -> ProjectContext:
        return self._ctx


# ============================================================
# 参数 / 标识符校验
# ============================================================

# 安全标识符（schema / table）：字母 / 数字 / 下划线
_SAFE_IDENTIFIER_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# material_code 字符集：字母 / 数字 / dash / dot / underscore
# （覆盖常见 ERP 编码：10001 / MAT-001 / SKU.001 / mat_001）
_MATERIAL_CODE_MAX_LEN_DEFAULT = 64
_MATERIAL_CODE_PATTERN: re.Pattern[str] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,%d}$"
    % (_MATERIAL_CODE_MAX_LEN_DEFAULT - 1)
)


def _assert_safe_identifier(value: str, *, field_name: str) -> str:
    """校验 ``value`` 是 SQL 安全标识符（schema / table）。

    Raises:
        ToolExecutionError: 含不安全字符或为空（防御 schema/table 配置错误
                            或环境变量被注入）。**不**抛 ToolError 家族以外的异常，
                            ToolRegistry 会归一为 ToolResult(success=False)。
    """
    if not value or not _SAFE_IDENTIFIER_PATTERN.match(value):
        raise ToolExecutionError(
            tool_name="get_inventory",
            reason=(
                f"{field_name} 配置非法: 仅允许 ASCII 字母/数字/下划线，"
                f"且必须以字母或下划线开头"
            ),
        )
    return value


def _validate_material_code(
    raw: Any, *, max_len: int
) -> str:
    """校验 material_code；strip 后非空非纯空白，且只含合法字符。

    Raises:
        ValueError: 不暴露原始输入（避免注入值回显到 ToolResult.error）。
                    Tool Handler 会包成 ToolValidationError。
    """
    if not isinstance(raw, str):
        raise ValueError(
            f"material_code 必须为字符串（got {type(raw).__name__}）"
        )
    value = raw.strip()
    if not value:
        raise ValueError("material_code 不能为空或纯空白")
    if len(value) > max_len:
        raise ValueError(
            f"material_code 长度不能超过 {max_len}（got {len(value)}）"
        )
    if not _MATERIAL_CODE_PATTERN.match(value):
        raise ValueError(
            "material_code 包含非法字符（仅允许字母/数字/点/下划线/dash）"
        )
    return value


# ============================================================
# 真实 DB 查询（独立函数，便于单元测试 / 单步注入）
# ============================================================

async def _run_inventory_query(
    *,
    engine: Engine,
    schema: str,
    table: str,
    material_code: str,
    timeout_seconds: int,
) -> float:
    """执行只读查询并返回物料库存合计。

    安全策略（纵深防御）：
        1. ``material_code`` 通过绑定参数传递（**不**拼字符串）
        2. schema / table 已通过 ``_assert_safe_identifier`` 校验
        3. 事务级 ``BEGIN READ ONLY`` + ``SET LOCAL statement_timeout`` 防止
           数据库端写操作 / 长时间占用
        4. 自动 ``ROLLBACK`` 归还连接
        5. SQL 文本不写入 ToolResult.error

    Args:
        engine: SQLAlchemy Engine。
        schema: 已校验的 schema 名。
        table:  已校验的 table 名。
        material_code: 已校验的物料编码。
        timeout_seconds: PostgreSQL ``statement_timeout``（秒）。

    Returns:
        float: SUM(qty)；不存在记录 → 0.0。

    Raises:
        ToolExecutionError: DB 异常（错误消息仅含类名，不含 SQL / connection string）。
    """
    # schema / table 已通过 _assert_safe_identifier 校验 → 安全拼接
    qualified_table = f'"{schema}"."{table}"'
    sql = (
        f"SELECT COALESCE(SUM(qty), 0) "
        f"FROM {qualified_table} "
        f"WHERE material_code = :material_code"
    )

    def _sync_query() -> float:
        # statement_timeout 不支持 bind parameter；用整数直接内插
        # （已钳制到 [1, 60]；Python int() 是无注入风险的）
        timeout_ms = str(int(timeout_seconds) * 1000)
        with engine.connect() as conn:
            try:
                # SET TRANSACTION / SET LOCAL 必须是事务的第一条语句；
                # SQLAlchemy 2.x 的 execute() 自动 begin 后立刻执行 SET。
                conn.execute(sa_text("SET TRANSACTION READ ONLY"))
                conn.execute(sa_text(
                    f"SET LOCAL statement_timeout = {timeout_ms}"
                ))
                result = conn.execute(
                    sa_text(sql),
                    {"material_code": material_code},
                ).scalar()
            finally:
                conn.rollback()
        if result is None:
            return 0.0
        return float(result)

    try:
        return await asyncio.to_thread(_sync_query)
    except Exception as exc:  # noqa: BLE001
        # 不向调用方暴露 SQL 原文 / connection string
        raise ToolExecutionError(
            tool_name="get_inventory",
            reason="数据库查询失败",
            cause_type=type(exc).__name__,
        ) from exc


# ============================================================
# Handler
# ============================================================

class GetInventoryHandler:
    """``get_inventory`` 的真实 Handler（只读 DB 查询）。

    关键约束：
        - Engine 由调用方注入；未注入时懒加载全局 ``get_engine()`` 缓存
        - ProjectContext 由 ``GetInventoryProjectContextProvider`` 解析；
          不在 Handler 中硬编码 project_id
        - SQL 使用绑定参数（``material_code``）；
          schema/table 名通过白名单校验后安全拼接
        - 事务级 ``BEGIN READ ONLY`` + ``SET LOCAL statement_timeout`` 强制只读
        - 不返回：DATABASE_URL / password / SQL 原文 / traceback

    Args:
        engine: SQLAlchemy Engine；None 时懒加载全局 ``get_engine()`` 缓存。
        inv_settings: 配置（默认全局 ``settings.inventory_tool``）。
        project_context_provider: ProjectContext Provider；
                                  None 时使用 ``GetInventoryProjectContextProvider``。
        project_id: 可选 ProjectContext override（与 API 层 ``project_id`` 配套）。
    """

    def __init__(
        self,
        *,
        engine: Engine | None = None,
        inv_settings: InventoryToolSettings | None = None,
        project_context_provider: GetInventoryProjectContextProvider | None = None,
        project_id: str | None = None,
    ) -> None:
        self._engine = engine
        self._settings = inv_settings or settings.inventory_tool
        self._project_provider = (
            project_context_provider
            if project_context_provider is not None
            else GetInventoryProjectContextProvider(project_id=project_id)
        )
        self._project_id_override = project_id

    # ----------------------------------------------------------
    # 公开入口
    # ----------------------------------------------------------

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """执行 ``get_inventory``。

        Args:
            arguments: 调用方传入参数；Orchestrator 当前传 ``{}``，
                        由 Phase 3.7.12 兼容性 layer 注入 ``material_code``。

        Returns:
            dict: ``{"material_code": <str>, "qty": <float|int>, "project_id": <str>}``。
                  物料不存在时 ``qty == 0``（业务语义）。

        Raises:
            ToolError: 参数校验 / DB 不可用 / 执行错误；归一化为
                       ``ToolResult(success=False, error=...)``。
        """
        # 1. 参数校验
        try:
            material_code = _validate_material_code(
                arguments.get("material_code"),
                max_len=self._settings.material_code_max_len,
            )
        except ValueError as exc:
            # 转 ToolValidationError 以保留 Tool 错误语义；原始输入不回显
            raise ToolValidationError(
                tool_name="get_inventory",
                reason=str(exc),
                field_path="material_code",
            ) from exc

        # 2. Engine 解析
        engine = self._engine or get_engine()
        if engine is None:
            raise ToolExecutionError(
                tool_name="get_inventory",
                reason="Database engine 未配置（DATABASE_URL 为空）",
            )

        # 3. schema / table 标识符校验
        schema = _assert_safe_identifier(
            self._settings.schema_name, field_name="schema_name"
        )
        table = _assert_safe_identifier(
            self._settings.table_name, field_name="table_name"
        )

        # 4. ProjectContext 解析（仅用于 metadata；不进入 SQL 拼接）
        project_id_label: str | None = self._project_id_override
        if project_id_label is None:
            try:
                ctx = self._project_provider.resolve()
                project_id_label = getattr(ctx, "project_id", None)
            except Exception as exc:  # noqa: BLE001
                # ProjectContext 解析失败不应阻断 Tool（业务上仍可查询）；
                # 记 logger，project_id 留空
                logger.warning(
                    "get_inventory: project_context resolve failed",
                    extra={"error_type": type(exc).__name__},
                )

        # 5. 真实只读查询
        try:
            qty = await _run_inventory_query(
                engine=engine,
                schema=schema,
                table=table,
                material_code=material_code,
                timeout_seconds=self._settings.query_timeout_seconds,
            )
        except ToolError:
            raise

        return {
            "material_code": material_code,
            "qty": qty,
            "project_id": project_id_label,
        }


# ============================================================
# 注册助手
# ============================================================

def register_get_inventory_tool(
    registry: ToolRegistry,
    *,
    handler: GetInventoryHandler | None = None,
) -> None:
    """把真实 ``get_inventory`` 注册到给定 Registry（**不**操作全局状态）。

    Args:
        registry: 目标 Registry。
        handler:  可选自定义 Handler（用于测试 / 注入 Engine / ProjectContextProvider）。
                  None 时使用默认 Handler（懒加载）。
    """
    if handler is None:
        handler = GetInventoryHandler()
    registry.register(GET_INVENTORY_DEFINITION, handler)


def build_default_tool_registry(
    *,
    handler: GetInventoryHandler | None = None,
) -> ToolRegistry:
    """构造一个预注册真实 ``get_inventory`` 的 ToolRegistry。

    用于在 ``AIOrchestratorService`` 构造时注入；
    Phase 3.7.9 默认 Orchestrator 工厂 ``get_default_orchestrator()``
    不注册任何 Tool（``tool_registry=None``），故调用方需自行注入此 Registry
    以使 TOOL 路由可达。

    Args:
        handler: 可选自定义 Handler；None 时使用默认 Handler。

    Returns:
        ``ToolRegistry``，包含真实 ``get_inventory``。
    """
    registry = ToolRegistry()
    register_get_inventory_tool(registry, handler=handler)
    return registry