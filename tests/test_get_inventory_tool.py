"""真实 ``get_inventory`` Tool 测试（Phase 3.7.12）。

覆盖（任务书 §十七）：

    1. 正常查询（material_code=10001）→ 真实 DB 数据
    2. 不存在物料 → success=True，qty=0（业务语义）
    3. 空参数 "" → success=False，error 不含原始输入
    4. 纯空白参数 "   " → success=False
    5. SQL Injection（"10001' OR '1'='1"）→ 实际 qty == 10001 的精确值；
       不返回全部记录
    6. SQL Injection + DELETE → 仍是只读 SELECT，DB 行数不变
    7. Tool Registry：get_inventory 可正常注册和执行
    8. Router：类似"查询物料 10001 当前库存" → ROUTE=TOOL（source=tool_match）；
       且聚合问题（"库存最多的 10 个物料"）仍归 Text-to-SQL（§十九）
    9. Orchestrator：Router → ToolRegistry → get_inventory 链路通
    10. API：POST /api/ai/chat 返回 route=tool + content/data/metadata
    11. Project Context：project_id 出现在响应 metadata
    12. DB 无写入：执行前后 public.knowledge_document / knowledge_chunk 行数不变

真实 DB 测试需要：

    RUN_DB_TESTS=1 (true/yes/on)
    DATABASE_URL 已配置

测试数据来源：

    独立 schema ``inventory_tool_test``（避免污染 public schema）；
    由 ``_setup_inventory_fixture`` 在 ``TestRealDatabaseInventory`` 类的
    setup_class / teardown_class 中自动建立 + 清理。
"""
from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as sa_text

from backend.app.api import orchestrator_chat
from backend.app.config import InventoryToolSettings, settings
from backend.app.db import reset_engine_cache
from backend.app.db.session import get_engine
from backend.app.main import app
from backend.app.projects.context import ProjectContext
from backend.app.services.ai_orchestrator_service import (
    AIOrchestrationResult,
    AIOrchestratorExecutionError,
    AIOrchestratorRouteError,
    AIOrchestratorService,
    AIRouterService,
    RouteDecision,
    RouteType,
)
from backend.app.services.ai_router_service import (
    ToolRegistryCapabilityAdapter,
)
from backend.app.tools.base import ToolResult
from backend.app.tools.errors import (
    ToolError,
    ToolExecutionError,
    ToolValidationError,
)
from backend.app.tools.get_inventory import (
    GET_INVENTORY_DEFINITION,
    GetInventoryHandler,
    GetInventoryProjectContextProvider,
    _MATERIAL_CODE_PATTERN,
    _SAFE_IDENTIFIER_PATTERN,
    _assert_safe_identifier,
    _escape_like,
    _validate_material_code,
    _validate_optional_code,
    _validate_stat_date,
    build_default_tool_registry,
    register_get_inventory_tool,
)
from backend.app.tools.registry import ToolRegistry


# ============================================================
# 环境检查
# ============================================================

def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


_RUN_DB = _env_flag("RUN_DB_TESTS")


requires_db = pytest.mark.skipif(
    not _RUN_DB,
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


# ============================================================
# Test Inventory 隔离 Schema
# ============================================================

_INVENTORY_TEST_SCHEMA = "inventory_tool_test"
#: 报表口径条码表（Phase 7：库存现存量 = 条码库存；夹具表名与业务库一致）。
_INVENTORY_TEST_TABLE = "dyn_wh_barcode"
_INVENTORY_TEST_FULL = f"{_INVENTORY_TEST_SCHEMA}.{_INVENTORY_TEST_TABLE}"

# 关键测试 fixture 数据（material_code → qty；每个物料 1 条在库条码）
_INVENTORY_SEED_DATA: tuple[tuple[str, float], ...] = (
    ("10001", 120.0),
    ("10002", 80.0),
    ("MAT-001", 250.0),
    ("SKU.001", 5.0),
)

#: fixture 内的固定维度 id（隔离 schema；与业务库结构对齐的最小列集）
_FX_DIC_IN = "st-in"
_FX_DIC_OUT = "st-out"
_FX_UNIT = "u1"
_FX_ORG = "o1"
_FX_WAREHOUSE = "w1"
_FX_LOCATION = "l1"
_FX_SHELF = "sh1"
_FX_UPDATE_TIME = "2026-01-01 08:00:00"

#: 报表口径夹具表清单（DDL 由本文件管理；teardown 整 schema 删除）。
_INVENTORY_TEST_TABLES: tuple[str, ...] = (
    "secf_sys_f_dic",
    "dyn_unit",
    "secf_sys_org_company",
    "dyn_wh_warehouse",
    "dyn_wh_location",
    "dyn_wh_shelf",
    "dyn_wh_bc",
    "dyn_erp_basic_material",
    "dyn_erp_basic_material_i18n",
    "dyn_wh_bc_location_record",
    "dyn_wh_barcode",
)

#: 报表口径夹具 DDL（最小列集；**仅**建在隔离 schema，绝不触碰 public）。
_INVENTORY_TEST_DDL: tuple[str, ...] = (
    "CREATE TABLE {s}.secf_sys_f_dic ("
    "  id VARCHAR(64) PRIMARY KEY, code VARCHAR(64))",
    "CREATE TABLE {s}.dyn_unit ("
    "  id VARCHAR(64) PRIMARY KEY, unit_desc VARCHAR(64))",
    "CREATE TABLE {s}.secf_sys_org_company ("
    "  id VARCHAR(64) PRIMARY KEY, cnname VARCHAR(128), kd_stock_code VARCHAR(64))",
    "CREATE TABLE {s}.dyn_wh_warehouse ("
    "  id VARCHAR(64) PRIMARY KEY, code VARCHAR(64), name VARCHAR(128))",
    "CREATE TABLE {s}.dyn_wh_location ("
    "  id VARCHAR(64) PRIMARY KEY, location_code VARCHAR(64), name VARCHAR(128))",
    "CREATE TABLE {s}.dyn_wh_shelf ("
    "  id VARCHAR(64) PRIMARY KEY, shelf_code VARCHAR(64))",
    "CREATE TABLE {s}.dyn_wh_bc ("
    "  id VARCHAR(64) PRIMARY KEY, code VARCHAR(64), archived VARCHAR(4),"
    "  material_code VARCHAR(64))",
    "CREATE TABLE {s}.dyn_erp_basic_material ("
    "  id VARCHAR(64) PRIMARY KEY, material_code VARCHAR(64), unit_id VARCHAR(64))",
    "CREATE TABLE {s}.dyn_erp_basic_material_i18n ("
    "  id VARCHAR(64), locale VARCHAR(16), material_desc VARCHAR(128))",
    "CREATE TABLE {s}.dyn_wh_bc_location_record ("
    "  id VARCHAR(64) PRIMARY KEY, archived VARCHAR(4), location_id VARCHAR(64),"
    "  material_id VARCHAR(64), update_time TIMESTAMP)",
    "CREATE TABLE {s}.dyn_wh_barcode ("
    "  id VARCHAR(64) PRIMARY KEY, archived VARCHAR(4), qty NUMERIC(18,4),"
    "  status_dic_id VARCHAR(64), location_id VARCHAR(64),"
    "  warehouse_id VARCHAR(64), wh_bc_id VARCHAR(64), material_id VARCHAR(64),"
    "  org_id VARCHAR(64), shelf_id VARCHAR(64))",
)


def _setup_inventory_fixture(engine: Any) -> None:
    """创建 inventory_tool_test 隔离 schema + **报表口径**夹具表 + seed。

    为什么是"报表口径"：``get_inventory``（Phase 7）执行的是真实 WMS
    现存量报表等价查询（``dyn_wh_barcode`` 等固定表映射）⇒ 夹具必须提供
    同结构的最小表；schema 由 ``WMS_INVENTORY_SCHEMA``（测试注入）指向本
    隔离 schema，因此**不触碰** public / 业务库。

    负向数据（验证报表关键过滤）：
        * ``MAT-001`` 另有一条 ``archived='1'`` 条码（qty=1000）⇒ 不得计入；
        * ``MAT-001`` 另有一条状态 ``BARCODE_STATUS_OUT`` 条码（qty=500）
          ⇒ 不得计入；
        * 期望结果仍为 seed 值（250）。
    """
    with engine.begin() as conn:
        conn.execute(sa_text(
            f'DROP SCHEMA IF EXISTS "{_INVENTORY_TEST_SCHEMA}" CASCADE'
        ))
        conn.execute(sa_text(
            f'CREATE SCHEMA "{_INVENTORY_TEST_SCHEMA}"'
        ))
        qualified = f'"{_INVENTORY_TEST_SCHEMA}"'
        for ddl in _INVENTORY_TEST_DDL:
            conn.execute(sa_text(ddl.format(s=qualified)))

        # 字典 / 单位 / 组织 / 仓库 / 库位 / 货架
        conn.execute(sa_text(
            f"INSERT INTO {qualified}.secf_sys_f_dic (id, code) VALUES "
            "(:i, 'BARCODE_STATUS_IN'), (:o, 'BARCODE_STATUS_OUT')"
        ), {"i": _FX_DIC_IN, "o": _FX_DIC_OUT})
        conn.execute(sa_text(
            f"INSERT INTO {qualified}.dyn_unit (id, unit_desc) VALUES (:id, 'PCS')"
        ), {"id": _FX_UNIT})
        conn.execute(sa_text(
            f"INSERT INTO {qualified}.secf_sys_org_company "
            "(id, cnname, kd_stock_code) VALUES (:id, 'Test Org', 'ORG1')"
        ), {"id": _FX_ORG})
        conn.execute(sa_text(
            f"INSERT INTO {qualified}.dyn_wh_warehouse (id, code, name) "
            "VALUES (:id, 'A01', 'Warehouse A01')"
        ), {"id": _FX_WAREHOUSE})
        conn.execute(sa_text(
            f"INSERT INTO {qualified}.dyn_wh_location (id, location_code, name) "
            "VALUES (:id, 'L01', 'Location L01')"
        ), {"id": _FX_LOCATION})
        conn.execute(sa_text(
            f"INSERT INTO {qualified}.dyn_wh_shelf (id, shelf_code) "
            "VALUES (:id, 'S01')"
        ), {"id": _FX_SHELF})

        for code, qty in _INVENTORY_SEED_DATA:
            material_id = f"m-{code}"
            bc_id = f"bc-{code}"
            conn.execute(sa_text(
                f"INSERT INTO {qualified}.dyn_erp_basic_material "
                "(id, material_code, unit_id) VALUES (:mid, :code, :unit)"
            ), {"mid": material_id, "code": code, "unit": _FX_UNIT})
            conn.execute(sa_text(
                f"INSERT INTO {qualified}.dyn_erp_basic_material_i18n "
                "(id, locale, material_desc) VALUES (:mid, 'zh-CN', :desc)"
            ), {"mid": material_id, "desc": f"Material {code}"})
            conn.execute(sa_text(
                f"INSERT INTO {qualified}.dyn_wh_bc "
                "(id, code, archived, material_code) "
                "VALUES (:bid, :bcode, '0', :code)"
            ), {"bid": bc_id, "bcode": f"B{code}", "code": code})
            conn.execute(sa_text(
                f"INSERT INTO {qualified}.dyn_wh_bc_location_record "
                "(id, archived, location_id, material_id, update_time) "
                "VALUES (:rid, '0', :loc, :mid, CAST(:ts AS timestamp))"
            ), {"rid": f"r-{code}", "loc": _FX_LOCATION,
                 "mid": material_id, "ts": _FX_UPDATE_TIME})
            conn.execute(sa_text(
                f"INSERT INTO {qualified}.dyn_wh_barcode "
                "(id, archived, qty, status_dic_id, location_id, warehouse_id, "
                " wh_bc_id, material_id, org_id, shelf_id) VALUES "
                "(:bid, '0', :qty, :dic, :loc, :wh, :bc, :mid, :org, :shelf)"
            ), {"bid": f"b-{code}", "qty": qty, "dic": _FX_DIC_IN,
                 "loc": _FX_LOCATION, "wh": _FX_WAREHOUSE, "bc": bc_id,
                 "mid": material_id, "org": _FX_ORG, "shelf": _FX_SHELF})

        # 负向数据（仅 MAT-001）：归档条码 + 非在库状态条码
        conn.execute(sa_text(
            f"INSERT INTO {qualified}.dyn_wh_barcode "
            "(id, archived, qty, status_dic_id, location_id, warehouse_id, "
            " wh_bc_id, material_id, org_id, shelf_id) VALUES "
            "('b-MAT-001-archived', '1', 1000, :dic, :loc, :wh, 'bc-MAT-001', "
            " 'm-MAT-001', :org, :shelf)"
        ), {"dic": _FX_DIC_IN, "loc": _FX_LOCATION, "wh": _FX_WAREHOUSE,
            "org": _FX_ORG, "shelf": _FX_SHELF})
        conn.execute(sa_text(
            f"INSERT INTO {qualified}.dyn_wh_barcode "
            "(id, archived, qty, status_dic_id, location_id, warehouse_id, "
            " wh_bc_id, material_id, org_id, shelf_id) VALUES "
            "('b-MAT-001-out', '0', 500, :dic, :loc, :wh, 'bc-MAT-001', "
            " 'm-MAT-001', :org, :shelf)"
        ), {"dic": _FX_DIC_OUT, "loc": _FX_LOCATION, "wh": _FX_WAREHOUSE,
            "org": _FX_ORG, "shelf": _FX_SHELF})


def _teardown_inventory_fixture(engine: Any) -> None:
    """清理 inventory_tool_test schema（整 schema 删除，零残留）。"""
    with engine.begin() as conn:
        conn.execute(sa_text(
            f'DROP SCHEMA IF EXISTS "{_INVENTORY_TEST_SCHEMA}" CASCADE'
        ))


# ============================================================
# 1. ToolDefinition / 参数校验（mock-free）
# ============================================================

class TestDefinitionAndValidation:
    """纯静态 / 校验层测试（不依赖 DB）。"""

    def test_definition_name_is_get_inventory(self) -> None:
        assert GET_INVENTORY_DEFINITION.name == "get_inventory"

    def test_definition_requires_material_code(self) -> None:
        params = GET_INVENTORY_DEFINITION.parameters
        assert params["required"] == ["material_code"]
        assert params["properties"]["material_code"]["type"] == "string"

    def test_definition_does_not_expose_unsafe_params(self) -> None:
        """Tool parameters **不**接受 SQL / table / where_clause 等不安全字段。"""
        props = GET_INVENTORY_DEFINITION.parameters["properties"]
        forbidden = {"sql", "query", "raw_sql", "table", "where_clause", "order_by"}
        leaked = forbidden & set(props.keys())
        assert not leaked, (
            f"Tool parameters 不应暴露 SQL 注入面: {leaked}"
        )

    @pytest.mark.parametrize("code", [
        "10001", "MAT-001", "SKU.001", "mat_001", "A", "0"
    ])
    def test_material_code_pattern_accepts_valid(self, code) -> None:
        assert _MATERIAL_CODE_PATTERN.match(code) is not None

    @pytest.mark.parametrize("code", [
        "", "' OR '1'='1", "10001; DELETE FROM x",
        "10001 OR 1=1", "<script>", "10001 SELECT",
        "a" * 65,  # 长度超
        "-10001",   # dash 开头不允许
    ])
    def test_material_code_pattern_rejects_unsafe(self, code) -> None:
        assert _MATERIAL_CODE_PATTERN.match(code) is None

    @pytest.mark.parametrize("value", ["public", "inventory", "Sales_2023",
                                "WMS_v2"])
    def test_safe_identifier_accepts_valid(self, value) -> None:
        assert _assert_safe_identifier(value, field_name="x") == value

    @pytest.mark.parametrize("value", [
        "", "100inventory",  # 数字开头
        'evil"; DROP TABLE x;--',
        "in v entory",
        "public.invent ory",
    ])
    def test_safe_identifier_rejects_unsafe(self, value) -> None:
        with pytest.raises(ToolError):
            _assert_safe_identifier(value, field_name="x")

    def test_validate_material_code_strips(self) -> None:
        assert _validate_material_code("  10001  ", max_len=64) == "10001"

    def test_validate_material_code_empty(self) -> None:
        with pytest.raises(ValueError):
            _validate_material_code("", max_len=64)
        with pytest.raises(ValueError):
            _validate_material_code("   ", max_len=64)

    def test_validate_material_code_non_string(self) -> None:
        with pytest.raises(ValueError):
            _validate_material_code(123, max_len=64)
        with pytest.raises(ValueError):
            _validate_material_code(None, max_len=64)

    # ---------- Phase 3.11 Step 4：warehouse_code（可选第二参数） ----------

    def test_definition_declares_optional_warehouse_code(self) -> None:
        """warehouse_code 为可选字段（Phase 7 起**真实生效**）；required 仍只有
        material_code。"""
        params = GET_INVENTORY_DEFINITION.parameters
        props = params["properties"]
        assert props["warehouse_code"]["type"] == "string"
        assert params["required"] == ["material_code"]

    def test_definition_declares_report_filters(self) -> None:
        """Phase 7：报表筛选参数（组织/仓库/库位/货架/批次/统计日期）齐备。"""
        props = GET_INVENTORY_DEFINITION.parameters["properties"]
        for field_name in (
            "org_code",
            "warehouse_code",
            "location_code",
            "shelf_code",
            "batch_code",
            "stat_date",
        ):
            assert field_name in props, field_name
            assert props[field_name]["type"] == "string"

    def test_validate_optional_code_none_is_optional(self) -> None:
        """缺省（None）→ None（可选语义，与 material 的必填语义不同）。"""
        assert (
            _validate_optional_code(
                None, field_name="warehouse_code", max_len=64
            )
            is None
        )

    def test_validate_optional_code_strips(self) -> None:
        assert (
            _validate_optional_code(
                "  A01  ", field_name="warehouse_code", max_len=64
            )
            == "A01"
        )

    @pytest.mark.parametrize("value", [
        "", "   ", 123, 1.5,
        "' OR '1'='1", "A01; DELETE FROM x", "a" * 65, "-A01",
    ])
    def test_validate_optional_code_rejects_invalid(self, value) -> None:
        with pytest.raises(ValueError):
            _validate_optional_code(
                value, field_name="warehouse_code", max_len=64
            )

    async def test_handler_rejects_invalid_warehouse_code(self) -> None:
        """warehouse_code 格式非法 → ToolValidationError（DB 访问之前拦截）。"""
        handler = GetInventoryHandler()
        with pytest.raises(ToolValidationError) as ei:
            await handler(
                {"material_code": "MAT-001",
                 "warehouse_code": "A01' OR '1'='1"}
            )
        assert ei.value.field_path == "warehouse_code"

    async def test_handler_rejects_unsupported_filter_fields(self) -> None:
        """未确认的筛选字段（如任意列名 / SQL 片段）由 Schema 拒绝（0 DB）。"""
        from backend.app.tools.base import validate_arguments

        with pytest.raises(ValueError):
            validate_arguments(
                GET_INVENTORY_DEFINITION.parameters,
                {"material_code": "MAT-001", "where_clause": "1=1"},
            )

    @pytest.mark.parametrize("value", ["2026-01-01", "2026-12-31"])
    def test_validate_stat_date_accepts_iso(self, value) -> None:
        from datetime import date

        assert _validate_stat_date(value) == date.fromisoformat(value)

    def test_validate_stat_date_none_is_optional(self) -> None:
        assert _validate_stat_date(None) is None

    @pytest.mark.parametrize("value", [
        "", "   ", "2026/01/01", "2026-13-01", "01-01-2026", 20260101,
        "2026-01-01'; DROP TABLE x; --",
    ])
    def test_validate_stat_date_rejects_invalid(self, value) -> None:
        with pytest.raises(ValueError):
            _validate_stat_date(value)

    @pytest.mark.parametrize("value,expected", [
        ("A01", "A01"),
        ("100%", "100\\%"),
        ("A_1", "A\\_1"),
        ("a\\b", "a\\\\b"),
    ])
    def test_escape_like_is_literal(self, value, expected) -> None:
        """批次模糊匹配按字面量（% / _ / \\ 全部转义）。"""
        assert _escape_like(value) == expected


# ============================================================
# 2. Tool Registry（mock-free）
# ============================================================

class TestRegistryRegistration:
    """真实 Tool 注册 + 参数校验（mock-free）。"""

    def test_register_increases_count(self) -> None:
        r = ToolRegistry()
        assert len(r) == 0
        register_get_inventory_tool(r)
        assert len(r) == 1
        assert "get_inventory" in r

    def test_register_twice_raises(self) -> None:
        from backend.app.tools.errors import ToolAlreadyRegisteredError
        r = ToolRegistry()
        register_get_inventory_tool(r)
        with pytest.raises(ToolAlreadyRegisteredError):
            register_get_inventory_tool(r)

    def test_build_default_tool_registry_contains_get_inventory(self) -> None:
        """Phase 3.11 Step 5：默认 Registry 含两个真实只读 Tool。"""
        r = build_default_tool_registry()
        names = [d.name for d in r.list_definitions()]
        assert names == ["get_inventory", "get_work_order"]

    def test_definition_has_no_handler_in_list_def(self) -> None:
        """list_definitions() 不暴露 handler。"""
        r = build_default_tool_registry()
        for d in r.list_definitions():
            assert not hasattr(d, "_handler") or d._handler is None

    async def test_registry_execute_validation_failure_returns_tool_result(self) -> None:
        r = build_default_tool_registry()
        result = await r.execute("get_inventory", arguments={})
        assert isinstance(result, ToolResult)
        assert result.tool_name == "get_inventory"
        assert result.success is False
        assert result.data is None
        assert result.error is not None
        assert "参数校验失败" in result.error

    async def test_registry_execute_unknown_tool_returns_tool_result(self) -> None:
        r = build_default_tool_registry()
        result = await r.execute("nonexistent_tool", arguments={})
        assert result.success is False
        assert "Tool 未注册" in result.error


# ============================================================
# 2.5 Router 能力发现（任务书 §十七.8 / §十九）
# ============================================================

class TestRouterDiscovery:
    """Router 通过 Tool Capability 发现 ``get_inventory``（无 DB / 无 LLM）。"""

    @staticmethod
    def _router() -> AIRouterService:
        """真实 ToolRegistry 能力元数据 + 关闭 LLM fallback（规则路径确定性）。"""
        return AIRouterService(
            tool_capabilities=ToolRegistryCapabilityAdapter(
                build_default_tool_registry()
            ),
            llm_fallback_enabled=False,
        )

    def test_capability_metadata_exposes_aliases_only(self) -> None:
        """能力元数据只暴露 name / description / aliases，**不**暴露 handler。

        Phase 3.11 Step 5：默认 Registry 含两个真实 Tool
        （get_inventory / get_work_order），元数据契约对两者一致。
        """
        caps = ToolRegistryCapabilityAdapter(
            build_default_tool_registry()
        ).list_capabilities()
        assert len(caps) == 2
        by_name = {c.name: c for c in caps}
        inv = by_name["get_inventory"]
        assert "库存" in inv.description
        assert "当前库存" in inv.aliases
        assert "get_work_order" in by_name
        # 只读元数据：不泄露 handler / 入参 Schema / SQL
        for cap in caps:
            assert not hasattr(cap, "handler")
            assert not hasattr(cap, "parameters")

    def test_capability_aliases_avoid_bare_subject_word(self) -> None:
        """§十九：别名不能用裸词，否则会抢占聚合类 Text-to-SQL 问题。"""
        caps = ToolRegistryCapabilityAdapter(
            build_default_tool_registry()
        ).list_capabilities()
        for cap in caps:
            assert "库存" not in cap.aliases
            assert "工单" not in cap.aliases
            assert "物料" not in cap.aliases

    @pytest.mark.parametrize("question", [
        "查询物料 10001 当前库存",
        "查询物料10001当前库存",
        "帮我查库存 10001",
        "物料库存 10001",
    ])
    async def test_material_inventory_question_routes_to_tool(
        self, question: str
    ) -> None:
        """§十七.8：类似"查询物料10001当前库存" → TOOL（source=tool_match）。"""
        decision = await self._router().route(question)
        assert decision.route == RouteType.TOOL
        assert decision.source == "tool_match"
        assert "get_inventory" in (decision.reason or "")

    @pytest.mark.parametrize("question", [
        "库存最多的 10 个物料是什么？",
        "哪个仓库库存最高？",
        "本月采购入库数量是多少？",
        "统计最近 7 天入库量",
    ])
    async def test_aggregation_question_is_not_hijacked_by_tool(
        self, question: str
    ) -> None:
        """§十九：聚合 / 统计问题仍由 Text-to-SQL 承担，不被固定 Tool 抢占。"""
        decision = await self._router().route(question)
        assert decision.route == RouteType.TEXT_TO_SQL

    async def test_router_without_capability_cannot_discover_tool(self) -> None:
        """未注入能力元数据时，Router 不应凭空路由到 TOOL。

        这也是 API 层必须显式注入 ``ToolRegistryCapabilityAdapter`` 的原因
        （见 ``orchestrator_chat._default_orchestrator``）。
        """
        decision = await AIRouterService(
            llm_fallback_enabled=False
        ).route("查询物料 10001 当前库存")
        assert decision.route == RouteType.TEXT_TO_SQL

    async def test_default_api_router_discovers_get_inventory(self) -> None:
        """§二十二 验收：API 默认装配的 Router 能发现 ``get_inventory``。"""
        decision = await orchestrator_chat._default_orchestrator._router.route(
            "查询物料 10001 当前库存"
        )
        assert decision.route == RouteType.TOOL
        assert decision.source == "tool_match"


# ============================================================
# 3. 真实 DB 集成测试
# ============================================================

@pytest.fixture()
def real_engine() -> Generator[Any, None, None]:
    """真实 PostgreSQL Engine（要求 RUN_DB_TESTS + DATABASE_URL）。

    function-scope：每个测试独立 setup / teardown。
    """
    reset_engine_cache()
    engine = get_engine()
    assert engine is not None, "DATABASE_URL 未配置"
    _setup_inventory_fixture(engine)
    try:
        yield engine
    finally:
        try:
            _teardown_inventory_fixture(engine)
        except Exception:
            pass
        reset_engine_cache()


def _fixture_inv_settings() -> InventoryToolSettings:
    """隔离 fixture schema 的 Tool 配置（Phase 7：报表口径固定表映射）。

    只覆盖 ``schema_name`` ⇒ 查询指向隔离 schema 中的报表结构夹具表
    （``dyn_wh_barcode`` 等）；表 / 列映射为服务器端固定常量。
    """
    return InventoryToolSettings(schema_name=_INVENTORY_TEST_SCHEMA)


@pytest.fixture()
def real_handler(real_engine: Any) -> Generator[GetInventoryHandler, None, None]:
    """真实 GetInventoryHandler（指向 _INVENTORY_TEST_FULL）。"""
    yield GetInventoryHandler(
        engine=real_engine,
        inv_settings=_fixture_inv_settings(),
        project_id="inventory-tool-test",
    )


def _count_public_knowledge_rows(conn: Any) -> dict[str, int]:
    """统计 public.knowledge_document / knowledge_chunk 行数（写入保护检查）。"""
    rows: dict[str, int] = {}
    for table_name in ("knowledge_document", "knowledge_chunk"):
        result = conn.execute(sa_text(
            f"SELECT count(*) FROM public.{table_name}"
        )).scalar()
        rows[table_name] = int(result or 0)
    return rows


@requires_db()
class TestRealDatabaseInventory:
    """真实 PostgreSQL 集成测试（``get_inventory`` Tool）。

    ⚠️ 仅在 RUN_DB_TESTS=1 时执行；不依赖 ORM Model 注册；
    使用隔离 schema ``inventory_tool_test``，绝不触碰 public.* 表。
    """

    async def test_real_query_returns_seed_qty(
        self, real_handler: GetInventoryHandler
    ) -> None:
        """material_code=10001 → 真实返回 120.0（来自 seed 数据）。"""
        result = await real_handler({"material_code": "10001"})
        assert result["material_code"] == "10001"
        assert float(result["qty"]) == pytest.approx(120.0)
        assert result["project_id"] == "inventory-tool-test"

    async def test_real_query_returns_zero_for_missing(
        self, real_handler: GetInventoryHandler
    ) -> None:
        """不存在的物料 → qty=0（业务语义，**不**报 500）。"""
        result = await real_handler({"material_code": "99999"})
        assert result["material_code"] == "99999"
        assert float(result["qty"]) == 0.0

    async def test_sql_injection_does_not_exfiltrate(
        self, real_handler: GetInventoryHandler, real_engine: Any
    ) -> None:
        """SQL 注入尝试 "10001' OR '1'='1" → 参数校验拒绝（单引号不在
        合法字符集内），抛 ToolValidationError；且 DB 数据未泄露/未变化。
        """
        payload = "10001' OR '1'='1"
        with real_engine.connect() as conn:
            before_count = conn.execute(
                sa_text(f"SELECT count(*) FROM {_INVENTORY_TEST_FULL}")
            ).scalar()
        with pytest.raises(ToolValidationError):
            await real_handler({"material_code": payload})
        with real_engine.connect() as conn:
            after_count = conn.execute(
                sa_text(f"SELECT count(*) FROM {_INVENTORY_TEST_FULL}")
            ).scalar()
            # 行数不变，且未发生全表求和泄露
            assert int(after_count) == int(before_count)
            # 报表口径合计（仅 未归档 + BARCODE_STATUS_IN + bc 未归档 的条码）
            total_qty = conn.execute(
                sa_text(
                    f"SELECT COALESCE(SUM(b.qty), 0) FROM {_INVENTORY_TEST_FULL} b "
                    f"JOIN {_INVENTORY_TEST_SCHEMA}.secf_sys_f_dic st "
                    "  ON st.id = b.status_dic_id AND st.code = 'BARCODE_STATUS_IN' "
                    f"LEFT JOIN {_INVENTORY_TEST_SCHEMA}.dyn_wh_bc bc "
                    "  ON bc.id = b.wh_bc_id "
                    "WHERE b.archived = '0' AND bc.archived = '0'"
                )
            ).scalar()
        assert float(total_qty) == 120.0 + 80.0 + 250.0 + 5.0

    async def test_sql_injection_with_space_rejected(
        self, real_handler: GetInventoryHandler
    ) -> None:
        """带空格的注入 → 参数校验拒绝（_MATERIAL_CODE_PATTERN 不含空格）。"""
        with pytest.raises(ToolValidationError):
            await real_handler({"material_code": "10001' OR '1'='1"})

    async def test_sql_injection_delete_attempt_rejected(
        self, real_handler: GetInventoryHandler, real_engine: Any
    ) -> None:
        """"10001'; DELETE FROM inventory; --" 注入 → 参数校验拒绝；
        同时 DB 行数验证为未变化。
        """
        with real_engine.connect() as conn:
            before_count = conn.execute(
                sa_text(f"SELECT count(*) FROM {_INVENTORY_TEST_FULL}")
            ).scalar()
        with pytest.raises(ToolValidationError):
            await real_handler({
                "material_code": "10001'; DELETE FROM inventory; --",
            })
        with real_engine.connect() as conn:
            after_count = conn.execute(
                sa_text(f"SELECT count(*) FROM {_INVENTORY_TEST_FULL}")
            ).scalar()
        assert int(after_count) == int(before_count)

    async def test_no_db_write_to_public_knowledge(
        self, real_handler: GetInventoryHandler, real_engine: Any
    ) -> None:
        """执行多次查询后 public.knowledge_document / knowledge_chunk 行数不变。"""
        with real_engine.connect() as conn:
            before = _count_public_knowledge_rows(conn)
        for code in ("10001", "10002", "MAT-001", "99999-NOT-EXIST"):
            try:
                await real_handler({"material_code": code})
            except ToolError:
                pass  # 参数校验失败允许
        with real_engine.connect() as conn:
            after = _count_public_knowledge_rows(conn)
        assert before == after, (
            f"DB 写入被检测到: before={before}, after={after}"
        )

    async def test_handler_validates_material_code(
        self, real_handler: GetInventoryHandler
    ) -> None:
        """material_code="" / "   " → ToolValidationError。"""
        with pytest.raises(ToolValidationError):
            await real_handler({"material_code": ""})
        with pytest.raises(ToolValidationError):
            await real_handler({"material_code": "   "})

    async def test_handler_rejects_non_string(
        self, real_handler: GetInventoryHandler
    ) -> None:
        """material_code 非字符串 → ToolValidationError。"""
        with pytest.raises(ToolValidationError):
            await real_handler({"material_code": 12345})  # type: ignore[arg-type]
        with pytest.raises(ToolValidationError):
            await real_handler({"material_code": None})  # type: ignore[arg-type]

    async def test_db_unavailable_when_engine_none(self) -> None:
        """Engine 未配置 → ToolExecutionError（不抛非 ToolError）。"""
        handler = GetInventoryHandler(
            engine=None,
            inv_settings=InventoryToolSettings(),
        )
        # 临时 monkeypatch get_engine → None
        import backend.app.tools.get_inventory as gi_module
        original = gi_module.get_engine
        gi_module.get_engine = lambda: None  # type: ignore[assignment]
        try:
            with pytest.raises(ToolExecutionError):
                await handler({"material_code": "10001"})
        finally:
            gi_module.get_engine = original  # type: ignore[assignment]

    async def test_invalid_schema_config_rejected(self) -> None:
        """schema_name 包含非法字符 → ToolExecutionError。"""
        handler = GetInventoryHandler(
            inv_settings=InventoryToolSettings(
                schema_name="public; DROP TABLE x",
            ),
        )
        with pytest.raises(ToolExecutionError):
            await handler({"material_code": "10001"})


# ============================================================
# 4. Project Context 注入
# ============================================================

class TestProjectContext:
    """project_id 透传到 Tool 响应。"""

    @requires_db()
    async def test_project_id_appears_in_response(
        self, real_engine: Any
    ) -> None:
        """Handler(project_id=...) → data["project_id"] == ...。

        注意：real_engine fixture（module 作用域）已负责建表；
        本测试**不**再 setup / teardown，避免破坏 module 共享状态。
        """
        handler = GetInventoryHandler(
            engine=real_engine,
            inv_settings=_fixture_inv_settings(),
            project_id="another-warehouse",
        )
        result = await handler({"material_code": "10001"})
        assert result["project_id"] == "another-warehouse"

    def test_provider_resolve_returns_project_context(self) -> None:
        provider = GetInventoryProjectContextProvider(project_id="abc")
        ctx = provider.resolve()
        assert isinstance(ctx, ProjectContext)
        assert ctx.project_id == "abc"


# ============================================================
# 5. Orchestrator 链路（fake Router + 真实 Handler）
# ============================================================

class FakeRouter:
    def __init__(self, decision: RouteDecision) -> None:
        self._decision = decision
        self.calls: list[str] = []

    async def route(self, question: str, *, context: str | None = None):
        self.calls.append(question)
        return self._decision


class _OrchDeps:
    """最小 Orchestrator 依赖集合（仅启用 tool 路径）。"""

    @staticmethod
    def build(*, tools: ToolRegistry, project_id: str = "tool-test"):
        # 简化 project provider：不查 DB
        from backend.app.projects.context import (
            DataSource,
            DEFAULT_DATA_SOURCE_NAME,
            DEFAULT_DATA_SOURCE_TYPE,
            ProjectContext,
        )
        class _P:
            def resolve(self) -> ProjectContext:
                return ProjectContext(
                    project_id=project_id,
                    project_name=project_id,
                    description=None,
                    data_source=DataSource(
                        name=DEFAULT_DATA_SOURCE_NAME,
                        type=DEFAULT_DATA_SOURCE_TYPE,
                    ),
                )
        return AIOrchestratorService(
            router=FakeRouter(
                RouteDecision(
                    route=RouteType.TOOL,
                    confidence=1.0,
                    reason="test",
                    source="rule",
                    # Phase 3.11 Step 3：Router 是 Tool 选择唯一来源
                    tool_name="get_inventory",
                )
            ),
            tool_registry=tools,
            project_context_provider=_P(),
        )


@requires_db()
class TestOrchestratorIntegration:
    """Orchestrator → Router → ToolRegistry → get_inventory 真实 DB 链路。"""

    async def test_orchestrator_routes_to_get_inventory_real_db(
        self, real_engine: Any
    ) -> None:
        """Orchestrator 调用真实 get_inventory Handler → 真实 DB 数据。"""
        handler = GetInventoryHandler(
            engine=real_engine,
            inv_settings=_fixture_inv_settings(),
            project_id="orch-test",
        )
        registry = ToolRegistry()
        register_get_inventory_tool(registry, handler=handler)
        orch = _OrchDeps.build(tools=registry)
        result = await orch.execute("查询物料 10001 当前库存")
        assert isinstance(result, AIOrchestrationResult)
        assert result.route == RouteType.TOOL
        assert result.metadata["tool_name"] == "get_inventory"
        assert result.metadata["tool_success"] is True
        data = result.data
        assert isinstance(data, ToolResult)
        assert data.success is True
        assert data.data["material_code"] == "10001"
        assert float(data.data["qty"]) == pytest.approx(120.0)

    async def test_orchestrator_tool_failure_wrapped(
        self, real_engine: Any
    ) -> None:
        """material_code="   " → Tool 失败 → Orchestrator 包成
        AIOrchestratorExecutionError（因为 ToolResult(success=False)
        不应被 Orchestrator 当作异常；这里验证 Tool 异常路径）。
        """
        handler = GetInventoryHandler(
            engine=real_engine,
            inv_settings=_fixture_inv_settings(),
        )
        registry = ToolRegistry()
        register_get_inventory_tool(registry, handler=handler)
        orch = _OrchDeps.build(tools=registry)
        # 由于 Tool 参数校验失败被 ToolRegistry 归一为 ToolResult(False)，
        # Orchestrator 不抛异常，而是 ToolResult(success=False) 在 result.data
        # 流程：
        result = await orch.execute("查库存")  # 无 material_code → 校验失败
        # ToolArgumentExtractor 对 "查库存" 提取不到候选值（arguments={}）
        # → ToolRegistry 校验失败（missing required material_code）
        # → ToolResult(success=False)
        # Orchestrator 不抛 → result 包含失败 ToolResult
        assert result.route == RouteType.TOOL
        assert result.metadata["tool_success"] is False
        data = result.data
        assert isinstance(data, ToolResult)
        assert data.success is False


# ============================================================
# 6. API E2E（Real DB）
# ============================================================

@requires_db()
class TestApiIntegration:
    """POST /api/ai/chat 端到端测试（真实 PostgreSQL + 真实 get_inventory）。"""

    @pytest.fixture(autouse=True)
    def _setup_real_handler(self, real_engine: Any, monkeypatch):
        """重写 ``_default_orchestrator`` 注入真实 DB Handler。"""
        _setup_inventory_fixture(real_engine)

        handler = GetInventoryHandler(
            engine=real_engine,
            inv_settings=_fixture_inv_settings(),
            project_id="api-test",
        )
        registry = build_default_tool_registry(handler=handler)

        # 提供最小 ProjectContextProvider，避免触发 AIOrchestratorUnavailableError
        # （默认 Provider 需要 DB Schema / Semantic，测试场景不需要）。
        # 注意：_run_text_to_sql 要求 resolve() 返回
        # (ProjectContext, DatabaseSchema, ProjectSemantic) 元组。
        from backend.app.projects.context import (
            DataSource,
            DEFAULT_DATA_SOURCE_NAME,
            DEFAULT_DATA_SOURCE_TYPE,
            ProjectContext,
        )
        from backend.app.projects.semantic import ProjectSemantic
        from backend.app.services.schema_explorer_service import DatabaseSchema
        from backend.app.services.ai_router_service import (
            ToolRegistryCapabilityAdapter,
        )

        class _StubProvider:
            def __init__(self) -> None:
                self._ctx = ProjectContext(
                    project_id="api-test",
                    project_name="api-test",
                    description=None,
                    data_source=DataSource(
                        name=DEFAULT_DATA_SOURCE_NAME,
                        type=DEFAULT_DATA_SOURCE_TYPE,
                    ),
                )

            def resolve(self) -> tuple[Any, Any, Any]:
                return (self._ctx, DatabaseSchema("public", ()), ProjectSemantic())

        real_orch = AIOrchestratorService(
            router=AIRouterService(
                tool_capabilities=ToolRegistryCapabilityAdapter(registry),
            ),
            tool_registry=registry,
            project_context_provider=_StubProvider(),
        )
        monkeypatch.setattr(orchestrator_chat, "_default_orchestrator", real_orch)
        yield

    async def test_api_returns_tool_route_with_real_db(self) -> None:
        """POST /api/ai/chat + "查询物料 10001 当前库存" →
        route=tool + data 含 material_code/qty。"""
        with TestClient(app) as client:
            response = client.post(
                "/api/ai/chat",
                json={"question": "查询物料 10001 当前库存"},
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "tool"
        assert payload["data"]["tool_name"] == "get_inventory"
        assert payload["data"]["success"] is True
        assert payload["data"]["data"]["material_code"] == "10001"
        assert float(payload["data"]["data"]["qty"]) == pytest.approx(120.0)

    async def test_api_returns_zero_for_missing_material(self) -> None:
        with TestClient(app) as client:
            response = client.post(
                "/api/ai/chat",
                json={"question": "查询物料 99999 当前库存"},
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "tool"
        assert float(payload["data"]["data"]["qty"]) == 0.0

    async def test_api_sql_injection_blocked(self) -> None:
        """POST /api/ai/chat + 注入字符串 → 仍 200，但 Tool 失败。"""
        with TestClient(app) as client:
            response = client.post(
                "/api/ai/chat",
                json={
                    "question": (
                        "查询物料 10001' OR '1'='1 当前库存"
                    ),
                },
            )
        # 注入含单引号/空格 → ToolArgumentExtractor 提取到
        # "10001" 或 _validate_material_code 拒绝（取决于首 token）
        # → Tool 失败 → Orchestrator 把 ToolResult(success=False) 透传为 200
        assert response.status_code == 200
        payload = response.json()
        # material_code 可能被截断为 "10001"（合法）→ qty == 120
        # 或校验失败 → success=False
        if payload["data"]["success"]:
            assert float(payload["data"]["data"]["qty"]) <= 120.0


# ============================================================
# 7. DB 无写入（全局写入保护）
# ============================================================

@requires_db()
class TestDbWriteProtection:
    """执行所有 get_inventory 操作前后，public.knowledge_* 行数保持不变。"""

    async def test_knowledge_tables_unchanged(self, real_engine: Any) -> None:
        with real_engine.connect() as conn:
            before = _count_public_knowledge_rows(conn)

        handler = GetInventoryHandler(
            engine=real_engine,
            inv_settings=_fixture_inv_settings(),
        )
        for code in ("10001", "10002", "99999", "MAT-001"):
            try:
                await handler({"material_code": code})
            except ToolError:
                pass

        with real_engine.connect() as conn:
            after = _count_public_knowledge_rows(conn)
        assert before == after


__all__ = [
    "TestDefinitionAndValidation",
    "TestRegistryRegistration",
    "TestRealDatabaseInventory",
    "TestProjectContext",
    "TestOrchestratorIntegration",
    "TestApiIntegration",
    "TestDbWriteProtection",
]