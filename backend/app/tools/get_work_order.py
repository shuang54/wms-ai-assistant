"""真实 ``get_work_order`` Tool（Phase 3.11 Step 5）。

把 Phase 3.6.1 的 Mock ``get_work_order`` 升级为**真实**只读 Tool，
并验证第二个真实 Tool 与 ``get_inventory`` **共用同一执行边界**：

    Question
        ↓
    AI Router（Tool Selection 唯一来源；Phase 3.11 Step 3）
        ↓ RouteDecision(tool_name="get_work_order")
    AIOrchestrator._run_tool
        ↓
    ToolArgumentExtractor.extract    （work_order_no；Phase 3.11 Step 6：
                                      提取实现已从 Orchestrator 迁出）
        ↓
    ToolExecutionService.execute     （统一执行边界；能力校验 + 委派）
        ↓
    ToolRegistry.execute("get_work_order", arguments={...})
        ↓
    GetWorkOrderHandler.__call__
        ↓
    GetWorkOrderProjectContextProvider.resolve（项目上下文；不硬编码项目）
        ↓
    SQLAlchemy Engine.connect (READ ONLY)
        ↓
    PostgreSQL：SELECT status FROM schema.table
                WHERE work_order_no = :work_order_no LIMIT 1
        ↓
    ToolResult(success=True, data={"work_order_no": ..., "status": ...})

设计纪律（纵深防御，与 get_inventory 完全同构）：

* **只读**：``BEGIN READ ONLY`` 事务 + ``SET LOCAL statement_timeout``
  + 绑定参数；Tool 不提供 UPDATE / INSERT / DELETE 入口；
* **不建表 / 不改数据库**：工单表由部署环境提供（settings.work_order_tool
  可配置 schema/table 名）。Phase 3.11 Step 5 勘察结论：当前真实 DB
  **没有** work_order 业务表 → database-level 集成 = NOT SUPPORTED
  （见 evaluation 文档；本模块不做任何 DDL）；
* **不允许 Tool 输入任意 SQL / table / where_clause**：DTO 只接受
  ``work_order_no`` 一个参数；其它字段被 Tool 参数 Schema 拒绝；
* **不写死 project_id**：project_id 仅通过 ProjectContextProvider 解析
  （API / 工厂注入 override），不硬编码任何具体项目；
* **不创建 Engine / LLMClient / Embedding**：复用 ``get_engine()`` 全局缓存；
* **不调用其它 Tool / Orchestrator / Router / ToolExecutionService**
  （保持 Question → Router → ONE Tool 的边界）；
* **不在 ToolResult.error 中暴露**：DATABASE_URL / password / traceback /
  SQL 原文（含 schema/table 拼接后的最终 SQL）。
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.engine import Engine

from backend.app.config import WorkOrderToolSettings, settings
from backend.app.db.session import get_engine
from backend.app.projects.context import (
    DEFAULT_DATA_SOURCE_NAME,
    DEFAULT_DATA_SOURCE_TYPE,
    DataSource,
    ProjectContext,
)
from backend.app.tools.base import ToolDefinition
from backend.app.tools.errors import (
    ToolError,
    ToolExecutionError,
    ToolValidationError,
)
from backend.app.tools.registry import ToolRegistry


logger = logging.getLogger(__name__)


__all__ = [
    "GET_WORK_ORDER_DEFINITION",
    "GetWorkOrderHandler",
    "GetWorkOrderProjectContextProvider",
    "register_get_work_order_tool",
]


# ============================================================
# ToolDefinition
# ============================================================

GET_WORK_ORDER_DEFINITION: ToolDefinition = ToolDefinition(
    name="get_work_order",
    description=(
        "查询指定工单的状态（只读）。"
        "返回 work_order_no 与 status（工单状态）。"
        "仅支持 work_order_no 单参数；不接受 SQL / table / where_clause 等。"
    ),
    # Router 规则命中所用的业务别名。**必须**是具备区分度的短语：
    # 不要放裸词 "工单"，否则 "统计本月工单数量" 这类聚合问题会被
    # 本 Tool 抢占（与 get_inventory 的"库存"裸词纪律一致）。
    aliases=(
        "工单状态",
        "查工单",
        "工单查询",
        "查询工单",
        "工单号",
    ),
    parameters={
        "type": "object",
        "properties": {
            "work_order_no": {
                "type": "string",
                "description": (
                    "工单号（1-64 字符）；自动 strip；不接受空字符串 / "
                    "纯空白 / SQL 关键字。"
                ),
            },
        },
        "required": ["work_order_no"],
    },
)


# ============================================================
# Project Context Provider（最小本地实现，不依赖 ai_orchestrator_service）
# ============================================================

class GetWorkOrderProjectContextProvider:
    """Tool 层本地最小 ProjectContext Provider（与 inventory 同构）。

    为什么不复用 ``ai_orchestrator_service.DefaultProjectContextProvider``：

        * 循环依赖（orchestrator → tools → orchestrator）；
        * Tool 不需要 DB Schema / Semantic；只需要 project_id 元数据。

    Attributes:
        project_id: 解析出的 project_id；API / 工厂可透传 override。
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

# work_order_no 字符集：字母 / 数字 / dash / dot / underscore
# （覆盖常见工单编码：WO-202609-001 / WO.001 / wo_001 / 10001）
_WORK_ORDER_NO_MAX_LEN_DEFAULT = 64
_WORK_ORDER_NO_PATTERN: re.Pattern[str] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,%d}$"
    % (_WORK_ORDER_NO_MAX_LEN_DEFAULT - 1)
)


def _assert_safe_identifier(value: str, *, field_name: str) -> str:
    """校验 ``value`` 是 SQL 安全标识符（schema / table）。

    Raises:
        ToolExecutionError: 含不安全字符或为空（防御 schema/table 配置错误
                            或环境变量被注入）。
    """
    if not value or not _SAFE_IDENTIFIER_PATTERN.match(value):
        raise ToolExecutionError(
            tool_name="get_work_order",
            reason=(
                f"{field_name} 配置非法: 仅允许 ASCII 字母/数字/下划线，"
                f"且必须以字母或下划线开头"
            ),
        )
    return value


def _validate_work_order_no(raw: Any, *, max_len: int) -> str:
    """校验 work_order_no；strip 后非空非纯空白，且只含合法字符。

    Raises:
        ValueError: 不暴露原始输入（避免注入值回显到 ToolResult.error）。
                    Tool Handler 会包成 ToolValidationError。
    """
    if not isinstance(raw, str):
        raise ValueError(
            f"work_order_no 必须为字符串（got {type(raw).__name__}）"
        )
    value = raw.strip()
    if not value:
        raise ValueError("work_order_no 不能为空或纯空白")
    if len(value) > max_len:
        raise ValueError(
            f"work_order_no 长度不能超过 {max_len}（got {len(value)}）"
        )
    if not _WORK_ORDER_NO_PATTERN.match(value):
        raise ValueError(
            "work_order_no 包含非法字符（仅允许字母/数字/点/下划线/dash）"
        )
    return value


# ============================================================
# 真实 DB 查询（独立函数，便于单元测试 / 单步注入）
# ============================================================

async def _run_work_order_query(
    *,
    engine: Engine,
    schema: str,
    table: str,
    work_order_no: str,
    timeout_seconds: int,
) -> str | None:
    """执行只读查询并返回工单状态。

    安全策略（纵深防御，与 inventory 完全同构）：
        1. ``work_order_no`` 通过绑定参数传递（**不**拼字符串）
        2. schema / table 已通过 ``_assert_safe_identifier`` 校验
        3. 事务级 ``BEGIN READ ONLY`` + ``SET LOCAL statement_timeout``
           防止数据库端写操作 / 长时间占用
        4. 自动 ``ROLLBACK`` 归还连接
        5. SQL 文本不写入 ToolResult.error

    Args:
        engine: SQLAlchemy Engine。
        schema: 已校验的 schema 名。
        table:  已校验的 table 名。
        work_order_no: 已校验的工单号。
        timeout_seconds: PostgreSQL ``statement_timeout``（秒）。

    Returns:
        str | None: 命中的 status；工单不存在 → None（业务语义，
        不伪造状态）。

    Raises:
        ToolExecutionError: DB 异常（错误消息仅含类名，不含 SQL /
                            connection string）。
    """
    # schema / table 已通过 _assert_safe_identifier 校验 → 安全拼接
    qualified_table = f'"{schema}"."{table}"'
    sql = (
        f"SELECT status "
        f"FROM {qualified_table} "
        f"WHERE work_order_no = :work_order_no "
        f"LIMIT 1"
    )

    def _sync_query() -> str | None:
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
                    {"work_order_no": work_order_no},
                ).scalar()
            finally:
                conn.rollback()
        if result is None:
            return None
        return str(result)

    try:
        return await asyncio.to_thread(_sync_query)
    except Exception as exc:  # noqa: BLE001
        # 不向调用方暴露 SQL 原文 / connection string
        raise ToolExecutionError(
            tool_name="get_work_order",
            reason="数据库查询失败",
            cause_type=type(exc).__name__,
        ) from exc


# ============================================================
# Handler
# ============================================================

class GetWorkOrderHandler:
    """``get_work_order`` 的真实 Handler（只读 DB 查询）。

    关键约束（与 ``GetInventoryHandler`` 同构）：
        - Engine 由调用方注入；未注入时懒加载全局 ``get_engine()`` 缓存
        - ProjectContext 由 ``GetWorkOrderProjectContextProvider`` 解析；
          不在 Handler 中硬编码 project_id
        - SQL 使用绑定参数（``work_order_no``）；
          schema/table 名通过白名单校验后安全拼接
        - 事务级 ``BEGIN READ ONLY`` + ``SET LOCAL statement_timeout``
          强制只读
        - 不依赖 / 不调用其它 Tool、ToolRegistry、ToolExecutionService、
          Orchestrator（Question → Router → ONE Tool 边界）
        - 不返回：DATABASE_URL / password / SQL 原文 / traceback

    Args:
        engine: SQLAlchemy Engine；None 时懒加载全局 ``get_engine()`` 缓存。
        wo_settings: 配置（默认全局 ``settings.work_order_tool``）。
        project_context_provider: ProjectContext Provider；
                                  None 时使用
                                  ``GetWorkOrderProjectContextProvider``。
        project_id: 可选 ProjectContext override（与 API 层 / 项目工厂配套）。
    """

    def __init__(
        self,
        *,
        engine: Engine | None = None,
        wo_settings: WorkOrderToolSettings | None = None,
        project_context_provider: GetWorkOrderProjectContextProvider | None = None,
        project_id: str | None = None,
    ) -> None:
        self._engine = engine
        self._settings = wo_settings or settings.work_order_tool
        self._project_provider = (
            project_context_provider
            if project_context_provider is not None
            else GetWorkOrderProjectContextProvider(project_id=project_id)
        )
        self._project_id_override = project_id

    # ----------------------------------------------------------
    # 公开入口
    # ----------------------------------------------------------

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """执行 ``get_work_order``。

        Args:
            arguments: 调用方传入参数；由参数提取层构造
                       ``{"work_order_no": <str>}``
                       （未知字段已被 ToolRegistry Schema 校验拒绝）。

        Returns:
            dict: ``{"work_order_no": <str>, "status": <str|None>,
                   "project_id": <str|None>}``。
                  工单不存在时 ``status is None``（业务语义；不伪造状态）。

        Raises:
            ToolError: 参数校验 / DB 不可用 / 执行错误；归一化为
                       ``ToolResult(success=False, error=...)``。
        """
        # 1. 参数校验
        try:
            work_order_no = _validate_work_order_no(
                arguments.get("work_order_no"),
                max_len=self._settings.work_order_no_max_len,
            )
        except ValueError as exc:
            # 转 ToolValidationError 以保留 Tool 错误语义；原始输入不回显
            raise ToolValidationError(
                tool_name="get_work_order",
                reason=str(exc),
                field_path="work_order_no",
            ) from exc

        # 2. Engine 解析
        engine = self._engine or get_engine()
        if engine is None:
            raise ToolExecutionError(
                tool_name="get_work_order",
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
                    "get_work_order: project_context resolve failed",
                    extra={"error_type": type(exc).__name__},
                )

        # 5. 真实只读查询
        try:
            status_value = await _run_work_order_query(
                engine=engine,
                schema=schema,
                table=table,
                work_order_no=work_order_no,
                timeout_seconds=self._settings.query_timeout_seconds,
            )
        except ToolError:
            raise

        return {
            "work_order_no": work_order_no,
            "status": status_value,
            "project_id": project_id_label,
        }


# ============================================================
# 注册助手
# ============================================================

def register_get_work_order_tool(
    registry: ToolRegistry,
    *,
    handler: GetWorkOrderHandler | None = None,
) -> None:
    """把真实 ``get_work_order`` 注册到给定 Registry（**不**操作全局状态）。

    Args:
        registry: 目标 Registry。
        handler:  可选自定义 Handler（用于测试 / 注入 Engine /
                  ProjectContextProvider）。None 时使用默认 Handler（懒加载）。
    """
    if handler is None:
        handler = GetWorkOrderHandler()
    registry.register(GET_WORK_ORDER_DEFINITION, handler)
