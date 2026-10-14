"""真实 ``get_inventory`` Tool（Phase 3.7.12；Phase 7 报表接入：真实条码库存）。

将 Phase 3.6.1 的 Mock ``get_inventory`` 升级为**真实**只读 Tool；
Phase 7 起查询**真实 WMS 条码库存现存量**——业务依据为仓库侧报表
``库存现存量报表``（第一份 SQL；货架维度取自 ``库存盘点单备注查询``）。

链路：

    HTTP /api/ai/chat
        ↓
    AIOrchestratorService.execute
        ↓
    AIRouterService.route            （根据 description 关键词命中 TOOL）
        ↓
    _run_tool                        （tool_name 来自 Router decision；Step 3）
        ↓
    ToolArgumentExtractor.extract    （确定性提取；Phase 3.11 Step 6）
        ↓
    ToolRegistry.execute("get_inventory", arguments={...})
        ↓
    GetInventoryHandler.__call__
        ↓
    ProjectContextProvider.resolve   （解析 Project Context）
        ↓
    SQLAlchemy Engine.connect (READ ONLY；独立 WMS 业务库 Engine)
        ↓
    PostgreSQL：条码库存现存量报表等价查询
                （barcode × 状态字典/仓库/库位/货架/批次/物料/单位/组织
                  + latest_record 最新库位记录；GROUP BY 维度
                  HAVING SUM(barcode.qty) <> 0；LIMIT 上限）
        ↓
    ToolResult(success=True, data={
        "material_code", "qty"（总量）, "groups"（分组明细 ≤ max_rows）,
        "group_count", "truncated", "project_id"})

报表关键过滤条件（**原样保留**）：

* ``barcode.archived = '0'``
* ``bc.archived = '0'``
* ``status.code = 'BARCODE_STATUS_IN'``
* ``HAVING SUM(barcode.qty) <> 0``

``latest_record`` 语义（**已核对，不得擅自修改**）：按
``MAX(id)``（不是 ``MAX(update_time)``）取每个
``(location_id, material_id)`` 的最新一条库位记录，展示**该行**的
``update_time``；实测 7705 组中 9 组的 MAX(ID) 行 update_time 不等于
MAX(update_time) —— 两者语义不同，报表口径为 MAX(ID)。

设计纪律（纵深防御）：

* **不**修改 Orchestrator 核心路由逻辑："从 question 抽取参数"由独立组件
  ``ToolArgumentExtractor`` 负责（Phase 3.11 Step 6 起；Orchestrator 只编排）
* **不**修改 SQLValidator / SQLExecutor / TextToSQLService / AIRouterService
* **不**新增数据库表：Tool 只读**外部** WMS 业务库（表/列映射为服务器端
  固定常量；仅 schema 可配置且经白名单校验）
* **不**写死 ``project_id == "vietnam-wms"``：project_id 仅通过 ProjectContextProvider
  解析；Tool 不关心具体 project_id 值
* **不**允许 Tool 输入任意 SQL / table / where_clause：只接受
  ``material_code``（必填）+ 已确认的筛选参数（``org_code`` /
  ``warehouse_code`` / ``location_code`` / ``shelf_code`` / ``batch_code`` /
  ``stat_date``）；报表平台的 ``${if(...)}`` 全部转为**绑定参数**；
  未知参数被 Tool 参数 Schema 拒绝，非法值显式拒绝（不回显原始输入）
* **只读**：Handler 内使用 ``BEGIN READ ONLY`` 事务 + ``SET LOCAL statement_timeout``
  + 绑定参数 + 行数上限；Tool 不提供 UPDATE/INSERT/DELETE 入口
* **不**在 Tool 中创建 Engine / LLMClient / Embedding：Engine 由装配层注入
  （生产 = 独立 WMS 业务库 Engine，见 ``db/business_session``）
* **不**在 ToolResult.error 中暴露：DATABASE_URL / password / traceback /
  SQL 原文（包含 schema 拼接后的最终 SQL）
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any, Callable

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
        "查询指定物料的**真实条码库存现存量**（只读）。"
        "返回 material_code 与 qty（库存总量），并附按"
        "库存组织/仓库/库位/货架/批次/单位 的分组明细（groups）。"
        "必填 material_code（精确匹配）；可选筛选：org_code（库存组织编码）、"
        "warehouse_code（仓库编码）、location_code（库位编码）、"
        "shelf_code（货架编码）、batch_code（批次编码，模糊匹配）、"
        "stat_date（统计日期 YYYY-MM-DD）。"
        "仅统计条码状态为 BARCODE_STATUS_IN 且未归档的记录；"
        "查询不到（含不存在的物料）返回数量 0。"
        "不接受 SQL / table / where_clause / 任意列名等。"
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
                    "物料编码（1-64 字符，精确匹配）；自动 strip；"
                    "不接受空字符串 / 纯空白 / SQL 关键字。"
                ),
            },
            # Phase 7 报表接入：已确认的筛选参数（与报表 ${if(...)} 一一对应；
            # 全部作为**绑定参数**，绝不拼接进 SQL 文本）。
            "org_code": {
                "type": "string",
                "description": (
                    "库存组织编码（可选；1-64 字符；精确匹配 "
                    "secf_sys_org_company.kd_stock_code）。"
                ),
            },
            "warehouse_code": {
                "type": "string",
                "description": (
                    "仓库编码（可选；1-64 字符；精确匹配 dyn_wh_warehouse.code）。"
                ),
            },
            "location_code": {
                "type": "string",
                "description": (
                    "库位编码（可选；1-64 字符；精确匹配 "
                    "dyn_wh_location.location_code）。"
                ),
            },
            "shelf_code": {
                "type": "string",
                "description": (
                    "货架编码（可选；1-64 字符；精确匹配 dyn_wh_shelf.shelf_code；"
                    "货架维度来自货架报表）。"
                ),
            },
            "batch_code": {
                "type": "string",
                "description": (
                    "批次编码（可选；1-64 字符；模糊匹配 dyn_wh_bc.code，"
                    "字面量匹配 —— % / _ 不作为通配符）。"
                ),
            },
            "stat_date": {
                "type": "string",
                "description": (
                    "统计日期（可选；格式 YYYY-MM-DD；按最新库位记录 "
                    "update_time 的日期过滤，语义与报表一致）。"
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


# ============================================================
# 固定业务表 / 列映射（Phase 7 报表接入；**服务器端常量**）
# ============================================================
#
# 依据（已核对测试库 information_schema，Phase 7 Step 8 报表核对）：
#   * 第一份报表 ``库存现存量报表``（现存量查询基础）
#   * 第二份报表 ``库存盘点单备注查询``（货架维度）
#
# 纪律：表名 / 列名是**固定映射常量**（不使用用户输入；不拼接标识符）；
#       仅 ``schema`` 来自配置且经 ``_assert_safe_identifier`` 白名单校验。
_TABLE_BARCODE: str = "dyn_wh_barcode"
_TABLE_STATUS_DIC: str = "secf_sys_f_dic"
_TABLE_LOCATION: str = "dyn_wh_location"
_TABLE_WAREHOUSE: str = "dyn_wh_warehouse"
_TABLE_SHELF: str = "dyn_wh_shelf"
_TABLE_BC: str = "dyn_wh_bc"
_TABLE_MATERIAL: str = "dyn_erp_basic_material"
_TABLE_MATERIAL_I18N: str = "dyn_erp_basic_material_i18n"
_TABLE_UNIT: str = "dyn_unit"
_TABLE_LOCATION_RECORD: str = "dyn_wh_bc_location_record"
_TABLE_ORG: str = "secf_sys_org_company"

#: 报表口径涉及的**全部**固定表（供部署自检 / 脚本核对；顺序 = SQL 出现顺序）。
INVENTORY_REPORT_TABLES: tuple[str, ...] = (
    _TABLE_BARCODE,
    _TABLE_STATUS_DIC,
    _TABLE_LOCATION,
    _TABLE_WAREHOUSE,
    _TABLE_SHELF,
    _TABLE_BC,
    _TABLE_MATERIAL,
    _TABLE_MATERIAL_I18N,
    _TABLE_UNIT,
    _TABLE_LOCATION_RECORD,
    _TABLE_ORG,
)

#: 条码状态字典固定值（报表关键过滤：仅"入库/在库"条码计入现存量）。
_STATUS_CODE_IN: str = "BARCODE_STATUS_IN"

#: 物料名称 i18n 语言（报表口径：zh-CN）。
_MATERIAL_I18N_LOCALE: str = "zh-CN"

#: LIKE 转义字符（批次编码按**字面量**模糊匹配；% / _ 不作通配符）。
_LIKE_ESCAPE: str = "\\"


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


def _validate_optional_code(
    raw: Any, *, field_name: str, max_len: int
) -> str | None:
    """校验**可选**编码参数（org_code / warehouse_code / location_code /
    shelf_code / batch_code）；None / 缺省 → None（可选语义）。

    字符集 / 长度规则与 material_code 相同（复用同一白名单正则）；全部作为
    绑定参数使用（**不**进入 SQL 标识符位）。

    Raises:
        ValueError: 格式非法（**不**暴露原始输入 —— 避免注入值回显到
                    ToolResult.error）。Tool Handler 会包成 ToolValidationError。
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(f"{field_name} 必须为字符串（got {type(raw).__name__}）")
    value = raw.strip()
    if not value:
        raise ValueError(f"{field_name} 不能为空或纯空白")
    if len(value) > max_len:
        raise ValueError(
            f"{field_name} 长度不能超过 {max_len}（got {len(value)}）"
        )
    if not _MATERIAL_CODE_PATTERN.match(value):
        raise ValueError(
            f"{field_name} 包含非法字符（仅允许字母/数字/点/下划线/dash）"
        )
    return value


def _validate_stat_date(raw: Any) -> "date | None":
    """校验 ``stat_date``（报表"统计日期"）；缺省 → None。

    只接受 ``YYYY-MM-DD``（``datetime.date.fromisoformat``）；非法 ⇒ ValueError
    （不暴露原始输入）。作为绑定参数传给 ``DATE(r.update_time) = :stat_date``。
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(f"stat_date 必须为字符串（got {type(raw).__name__}）")
    value = raw.strip()
    if not value:
        raise ValueError("stat_date 不能为空或纯空白")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("stat_date 格式非法（应为 YYYY-MM-DD）") from exc


def _escape_like(value: str) -> str:
    """转义 LIKE 元字符（``\\`` / ``%`` / ``_``），配合 ``ESCAPE '\\'``。

    批次编码按**字面量**模糊匹配（用户输入的 ``%`` / ``_`` 不作通配符）。
    """
    return (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )


# ============================================================
# 真实 DB 查询（独立函数，便于单元测试 / 单步注入）
# ============================================================

def _build_inventory_query(
    *,
    schema: str,
    material_code: str,
    org_code: str | None = None,
    warehouse_code: str | None = None,
    location_code: str | None = None,
    shelf_code: str | None = None,
    batch_code: str | None = None,
    stat_date: date | None = None,
    max_rows: int,
) -> tuple[str, dict[str, Any]]:
    """构造**库存现存量**查询（报表等价；全部过滤值为绑定参数）。

    报表依据（第一份 ``库存现存量报表``；货架维度取自第二份）：

    * 保留的关键过滤：``barcode.archived = '0'`` · ``bc.archived = '0'`` ·
      ``status.code = 'BARCODE_STATUS_IN'`` · ``HAVING SUM(barcode.qty) <> 0``；
    * ``latest_record`` = 每个 ``(location_id, material_id)`` 的
      ``MAX(id)`` 行（**不是** MAX(update_time)）—— 语义已核对，不得修改；
    * ``${if(...)}`` 全部转为绑定参数（组织 / 仓库 / 库位 / 货架 / 批次 /
      统计日期）；批次为**字面量**模糊匹配（``%`` / ``_`` 已转义，
      ``ESCAPE`` 显式声明）；
    * 表 / 列 = 服务器端**固定映射常量**；仅 schema 来自配置并经白名单校验；
    * 结果附带 ``COUNT(*) OVER ()``（分组总数）与 ``SUM(SUM(qty)) OVER ()``
      （截断前的数量总量）⇒ 单次查询即可如实报告是否被 LIMIT 截断。

    Returns:
        (sql, params)：SQL 文本（标识符已校验；值为绑定参数）+ 绑定参数。

    Raises:
        ToolExecutionError: schema 配置非法（白名单校验失败；零 DB 访问）。
    """
    schema_ref = f'"{_assert_safe_identifier(schema, field_name="schema_name")}"'

    where: list[str] = [
        "barcode.archived = '0'",
        "bc.archived = '0'",
        "status.code = :status_code_in",
        "material.material_code = :material_code",
    ]
    params: dict[str, Any] = {
        "status_code_in": _STATUS_CODE_IN,
        "material_code": material_code,
        "max_rows": int(max_rows),
    }
    if org_code is not None:
        where.append("org.kd_stock_code = :org_code")
        params["org_code"] = org_code
    if warehouse_code is not None:
        where.append("warehouse.code = :warehouse_code")
        params["warehouse_code"] = warehouse_code
    if location_code is not None:
        where.append("location.location_code = :location_code")
        params["location_code"] = location_code
    if batch_code is not None:
        where.append(f"bc.code LIKE :batch_code ESCAPE '{_LIKE_ESCAPE}'")
        params["batch_code"] = f"%{_escape_like(batch_code)}%"
    if stat_date is not None:
        where.append("DATE(r.update_time) = :stat_date")
        params["stat_date"] = stat_date

    where_sql = "\n    AND ".join(where)

    # 货架维度（第二份报表）：**仅在过滤时**接入 —— 基础查询保持第一份报表
    # 的维度集合（现存量口径），避免 HAVING 与更细粒度分组的语义漂移。
    shelf_join = ""
    if shelf_code is not None:
        where.append("shelf.shelf_code = :shelf_code")
        params["shelf_code"] = shelf_code
        where_sql = "\n    AND ".join(where)
        shelf_join = (
            f"    LEFT JOIN {schema_ref}.{_TABLE_SHELF} AS shelf"
            " ON shelf.id = barcode.shelf_id\n"
        )

    sql = (
        "WITH latest_record AS (\n"
        "    SELECT MAX(id) AS record_id, location_id, material_id\n"
        f"    FROM {schema_ref}.{_TABLE_LOCATION_RECORD}\n"
        "    WHERE archived = '0'\n"
        "    GROUP BY location_id, material_id\n"
        ")\n"
        "SELECT\n"
        "    org.cnname                    AS org_name,\n"
        "    warehouse.code                AS warehouse_code,\n"
        "    warehouse.name                AS warehouse_name,\n"
        "    location.location_code        AS location_code,\n"
        "    location.name                 AS location_name,\n"
        "    bc.code                       AS batch_code,\n"
        "    material_i18n.material_desc   AS material_name,\n"
        "    unit.unit_desc                AS unit,\n"
        "    SUM(barcode.qty)              AS qty,\n"
        "    COUNT(barcode.id)             AS barcode_count,\n"
        "    r.update_time                 AS last_update_time,\n"
        "    COUNT(*) OVER ()              AS group_count,\n"
        "    SUM(SUM(barcode.qty)) OVER () AS qty_total\n"
        f"FROM {schema_ref}.{_TABLE_BARCODE} AS barcode\n"
        f"    LEFT JOIN {schema_ref}.{_TABLE_STATUS_DIC} AS status"
        " ON status.id = barcode.status_dic_id\n"
        f"    LEFT JOIN {schema_ref}.{_TABLE_LOCATION} AS location"
        " ON location.id = barcode.location_id\n"
        f"    LEFT JOIN {schema_ref}.{_TABLE_WAREHOUSE} AS warehouse"
        " ON warehouse.id = barcode.warehouse_id\n"
        f"{shelf_join}"
        f"    LEFT JOIN {schema_ref}.{_TABLE_BC} AS bc"
        " ON bc.id = barcode.wh_bc_id\n"
        f"    LEFT JOIN {schema_ref}.{_TABLE_MATERIAL} AS material"
        " ON material.id = barcode.material_id\n"
        f"    LEFT JOIN {schema_ref}.{_TABLE_MATERIAL_I18N} AS material_i18n"
        f" ON material_i18n.id = material.id"
        f" AND material_i18n.locale = '{_MATERIAL_I18N_LOCALE}'\n"
        f"    LEFT JOIN {schema_ref}.{_TABLE_UNIT} AS unit"
        " ON unit.id = material.unit_id\n"
        f"    LEFT JOIN {schema_ref}.{_TABLE_ORG} AS org"
        " ON org.id = barcode.org_id\n"
        "    LEFT JOIN latest_record AS lr"
        " ON lr.location_id = barcode.location_id"
        " AND lr.material_id = barcode.material_id\n"
        f"    LEFT JOIN {schema_ref}.{_TABLE_LOCATION_RECORD} AS r"
        " ON r.id = lr.record_id\n"
        f"WHERE {where_sql}\n"
        "GROUP BY org.cnname, warehouse.code, warehouse.name,\n"
        "         location.location_code, location.name,\n"
        "         bc.code, material_i18n.material_desc, unit.unit_desc,\n"
        "         r.update_time\n"
        "HAVING SUM(barcode.qty) <> 0\n"
        "ORDER BY r.update_time, warehouse.code, location.location_code,\n"
        "         bc.code, unit.unit_desc\n"
        "LIMIT :max_rows"
    )
    return sql, params


def _map_report_rows(
    rows: list[dict[str, Any]], *, max_rows: int
) -> dict[str, Any]:
    """报表行 → Tool 结果（分组明细 + 总量 + 截断标记；纯函数）。"""
    groups: list[dict[str, Any]] = []
    for row in rows[:max_rows]:
        last_update = row.get("last_update_time")
        groups.append(
            {
                "org_name": row.get("org_name"),
                "warehouse_code": row.get("warehouse_code"),
                "warehouse_name": row.get("warehouse_name"),
                "location_code": row.get("location_code"),
                "location_name": row.get("location_name"),
                "batch_code": row.get("batch_code"),
                "material_name": row.get("material_name"),
                "unit": row.get("unit"),
                "qty": float(row.get("qty") or 0.0),
                "barcode_count": int(row.get("barcode_count") or 0),
                "last_update_time": (
                    last_update.isoformat()
                    if hasattr(last_update, "isoformat")
                    else None
                ),
            }
        )
    group_count = int(rows[0].get("group_count") or 0) if rows else 0
    return {
        "qty_total": float(rows[0].get("qty_total") or 0.0) if rows else 0.0,
        "group_count": group_count,
        "groups": groups,
        "truncated": group_count > len(groups),
    }


async def _run_inventory_report_query(
    *,
    engine: Engine,
    schema: str,
    material_code: str,
    org_code: str | None = None,
    warehouse_code: str | None = None,
    location_code: str | None = None,
    shelf_code: str | None = None,
    batch_code: str | None = None,
    stat_date: date | None = None,
    timeout_seconds: int,
    max_rows: int,
) -> dict[str, Any]:
    """执行只读**库存现存量**查询（报表等价；见 :func:`_build_inventory_query`）。

    安全策略（纵深防御）：绑定参数（**不**拼字符串）· 事务级
    ``BEGIN READ ONLY`` + ``SET LOCAL statement_timeout`` · 自动 ``ROLLBACK`` ·
    ``LIMIT`` 行数上限 · SQL 文本不写入 ToolResult.error。

    Returns:
        {"qty_total": float, "group_count": int, "groups": [...], "truncated": bool}

    Raises:
        ToolExecutionError: DB 异常（错误消息仅含类名，不含 SQL / connection string）。
    """
    sql, params = _build_inventory_query(
        schema=schema,
        material_code=material_code,
        org_code=org_code,
        warehouse_code=warehouse_code,
        location_code=location_code,
        shelf_code=shelf_code,
        batch_code=batch_code,
        stat_date=stat_date,
        max_rows=max_rows,
    )

    def _sync_query() -> list[dict[str, Any]]:
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
                fetched = conn.execute(sa_text(sql), params).mappings().all()
            finally:
                conn.rollback()
        return [dict(row) for row in fetched]

    try:
        rows = await asyncio.to_thread(_sync_query)
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001
        # 不向调用方暴露 SQL 原文 / connection string
        raise ToolExecutionError(
            tool_name="get_inventory",
            reason="数据库查询失败",
            cause_type=type(exc).__name__,
        ) from exc
    return _map_report_rows(rows, max_rows=max_rows)


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
        engine_provider: Callable[[], Engine] | None = None,
    ) -> None:
        self._engine = engine
        self._engine_provider = engine_provider
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
            arguments: 调用方传入参数；Orchestrator 传
                       ``{"material_code": <str>}`` 或
                       ``{"material_code": <str>, "warehouse_code": <str>}``
                       （由参数提取层构造；本 Handler 只认这两个字段，
                       未知字段已被 ToolRegistry Schema 校验拒绝）。

        Returns:
            dict: ``{"material_code": <str>, "qty": <float|int>, "project_id": <str>}``。
                  物料不存在时 ``qty == 0``（业务语义）。

        Raises:
            ToolError: 参数校验 / DB 不可用 / 执行错误；归一化为
                       ``ToolResult(success=False, error=...)``。
            ToolExecutionError: 携带 ``warehouse_code`` 时显式拒绝
                       （当前库存表未提供 warehouse 维度；见步骤 1.2）。
        """
        # 1.1 参数校验：material_code（必填）
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

        # 1.1b 参数校验：可选筛选（报表 ${if(...)} 对应的已确认字段）
        #      org_code / warehouse_code / location_code / shelf_code / batch_code
        #      —— 全部作为**绑定参数**（不进入 SQL 标识符位）；非法值显式拒绝。
        optional_codes: dict[str, str | None] = {}
        for field_name in (
            "org_code",
            "warehouse_code",
            "location_code",
            "shelf_code",
            "batch_code",
        ):
            try:
                optional_codes[field_name] = _validate_optional_code(
                    arguments.get(field_name),
                    field_name=field_name,
                    max_len=self._settings.material_code_max_len,
                )
            except ValueError as exc:
                raise ToolValidationError(
                    tool_name="get_inventory",
                    reason=str(exc),
                    field_path=field_name,
                ) from exc

        # 1.1c 参数校验：stat_date（报表"统计日期"；YYYY-MM-DD）
        try:
            stat_date = _validate_stat_date(arguments.get("stat_date"))
        except ValueError as exc:
            raise ToolValidationError(
                tool_name="get_inventory",
                reason=str(exc),
                field_path="stat_date",
            ) from exc

        # 2. Engine 解析（**唯一**顺序：engine_provider → 显式 engine → 全局缓存）
        #
        #   生产装配（project_orchestrator_factory / build_default_tool_registry）
        #   注入 ``engine_provider = db.business_session.require_business_engine``：
        #   库存数据来自**独立** WMS/ERP 业务库（只读）；业务库未配置 ⇒ 显式
        #   失败 —— **绝不**回退应用库（DATABASE_URL）、**绝不**返回假数据。
        if self._engine_provider is not None:
            try:
                engine = self._engine_provider()
            except Exception as exc:  # noqa: BLE001 —— 显式失败；不泄露内部细节
                logger.warning(
                    "get_inventory: business datasource unavailable",
                    extra={"error_type": type(exc).__name__},
                )
                raise ToolExecutionError(
                    tool_name="get_inventory",
                    reason=(
                        "WMS/ERP 业务数据源不可用（未配置或连接失败）："
                        "请检查 WMS_DATABASE_* 配置；不回退应用数据库，"
                        "不返回假数据"
                    ),
                ) from exc
        else:
            engine = self._engine or get_engine()
        if engine is None:
            raise ToolExecutionError(
                tool_name="get_inventory",
                reason="Database engine 未配置（DATABASE_URL 为空）",
            )

        # 3. schema 标识符校验
        #    （表 / 列为服务器端固定映射常量；仅 schema 来自配置 WMS_INVENTORY_SCHEMA，
        #      白名单校验后安全拼接）
        schema = _assert_safe_identifier(
            self._settings.schema_name, field_name="schema_name"
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

        # 5. 真实只读查询（库存现存量报表等价；绑定参数 + 超时 + 行数上限）
        try:
            outcome = await _run_inventory_report_query(
                engine=engine,
                schema=schema,
                material_code=material_code,
                org_code=optional_codes["org_code"],
                warehouse_code=optional_codes["warehouse_code"],
                location_code=optional_codes["location_code"],
                shelf_code=optional_codes["shelf_code"],
                batch_code=optional_codes["batch_code"],
                stat_date=stat_date,
                timeout_seconds=self._settings.query_timeout_seconds,
                max_rows=self._settings.max_rows,
            )
        except ToolError:
            raise

        return {
            "material_code": material_code,
            "qty": outcome["qty_total"],
            "groups": outcome["groups"],
            "group_count": outcome["group_count"],
            "truncated": outcome["truncated"],
            "max_rows": self._settings.max_rows,
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
    work_order_handler: Any | None = None,
) -> ToolRegistry:
    """构造预注册**全部真实 Tool** 的 ToolRegistry。

    Phase 3.11 Step 5：注册两个真实只读 Tool —— 二者共用同一
    ToolExecutionService / ToolRegistry 执行边界：

        * ``get_inventory``   （Phase 3.7.12）
        * ``get_work_order``  （Phase 3.11 Step 5）

    用于在 ``AIOrchestratorService`` 构造时注入；
    Phase 3.7.9 默认 Orchestrator 工厂 ``get_default_orchestrator()``
    不注册任何 Tool（``tool_registry=None``），故调用方需自行注入此 Registry
    以使 TOOL 路由可达。

    Args:
        handler: 可选自定义 get_inventory Handler；None 时使用**生产默认**
                 Handler —— 绑定**独立 WMS/ERP 业务数据源**
                 （``db.business_session.require_business_engine``；只读；
                 业务库未配置 ⇒ 工具显式失败，**不**回退应用库）。
        work_order_handler: 可选自定义 get_work_order Handler；
                            None 时使用默认 Handler（懒加载 engine）。

    Returns:
        ``ToolRegistry``，含两个真实只读 Tool
        （注册顺序：get_inventory → get_work_order）。
    """
    # 延迟 import：避免 tools 包内模块级互相引用
    # （get_work_order 不依赖本模块，此处延迟仅为保持 import 图稳定）
    from backend.app.tools.get_work_order import (
        register_get_work_order_tool,
    )

    registry = ToolRegistry()
    if handler is None:
        # 生产默认：独立业务库（只读）——惰性解析，未配置即显式失败。
        from backend.app.db.business_session import require_business_engine

        handler = GetInventoryHandler(engine_provider=require_business_engine)
    register_get_inventory_tool(registry, handler=handler)
    register_get_work_order_tool(registry, handler=work_order_handler)
    return registry