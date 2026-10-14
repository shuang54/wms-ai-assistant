"""Phase 7 — ``get_inventory`` 报表接入测试（库存现存量报表等价）。

```text
A  离线：SQL 形态（报表关键过滤 / MAX(ID) 语义 / 参数化 / 可选过滤 /
        shelf 过滤 / LIKE 转义 / LIMIT / 非法 schema / 结果映射）
B  真实（需本机 WMS_DATABASE_* 配置；只读）：与**原报表 SQL** 逐行对照、
        空结果、过滤子集、截断、只读会话
```

报表依据（仓库侧第一份 SQL，逐字保留关键过滤）：

* ``barcode.archived = '0'``
* ``bc.archived = '0'``
* ``status.code = 'BARCODE_STATUS_IN'``
* ``HAVING SUM(barcode.qty) <> 0``
* ``latest_record`` = ``MAX(id)``（**不是** MAX(update_time)；语义已核对：
  7705 组中 9 组的 MAX(ID) 行 update_time ≠ MAX(update_time)）

``${if(...)}`` → 绑定参数（material/org/warehouse/location/shelf/batch/date）。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.config import InventoryToolSettings
from backend.app.db.business_session import (
    get_business_engine,
    is_business_datasource_configured,
)
from backend.app.tools.errors import ToolExecutionError
from backend.app.tools.get_inventory import (
    INVENTORY_REPORT_TABLES,
    _build_inventory_query,
    _map_report_rows,
)

__all__ = [
    "TestQueryShapeOffline",
    "TestResultMappingOffline",
    "TestReportConsistencyReal",
]

#: 原报表（第一份《库存现存量报表》）等价 SQL：关键过滤逐字保留；
#: ``${if(物料编码精确)}`` → 绑定参数 ``:material_code``。
REPORT_ON_HAND_SQL = """
WITH latest_record AS (
    SELECT MAX(ID) AS rcid, location_id, material_id
    FROM dyn_wh_bc_location_record
    WHERE archived = '0'
    GROUP BY location_id, material_id
)
SELECT
    org.cnname, w.code AS w_code, w.NAME AS w_name,
    l.location_code, l.NAME AS l_name, bc.code AS bc_code,
    M.material_code, un.unit_desc,
    SUM(barcode.qty) AS qty,
    COUNT(barcode.ID) AS barcode_count,
    r.update_time
FROM dyn_wh_barcode barcode
    JOIN secf_sys_f_dic st ON st.ID = barcode.status_dic_id AND st.code = 'BARCODE_STATUS_IN'
    LEFT JOIN dyn_wh_location l ON l.ID = barcode.location_id
    LEFT JOIN dyn_wh_warehouse w ON w.ID = barcode.warehouse_id
    LEFT JOIN dyn_wh_bc bc ON bc.ID = barcode.wh_bc_id
    LEFT JOIN dyn_erp_basic_material M ON M.ID = barcode.material_id
    LEFT JOIN dyn_erp_basic_material_i18n mi18n ON mi18n.ID = M.ID AND mi18n.locale = 'zh-CN'
    LEFT JOIN dyn_unit un ON un.ID = M.unit_id
    LEFT JOIN latest_record lr ON lr.location_id = barcode.location_id AND lr.material_id = barcode.material_id
    LEFT JOIN dyn_wh_bc_location_record r ON r.ID = lr.rcid
    LEFT JOIN secf_sys_org_company org ON org.ID = barcode.org_id
WHERE barcode.archived = '0'
  AND bc.archived = '0'
  AND M.material_code = :material_code
GROUP BY org.cnname, w.ID, w.code, w.NAME, l.ID, l.location_code, l.NAME,
         bc.code, M.material_code, M.ID, mi18n.material_desc, un.unit_desc, r.update_time
HAVING SUM(barcode.qty) <> 0
ORDER BY r.update_time
"""


# ============================================================
# A. 离线：SQL 形态与结果映射
# ============================================================


class TestQueryShapeOffline:
    def _base_sql(self) -> str:
        sql, _ = _build_inventory_query(
            schema="public", material_code="MAC-001", max_rows=100
        )
        return sql

    def test_report_key_filters_preserved(self) -> None:
        upper = self._base_sql().upper()
        assert "BARCODE.ARCHIVED = '0'" in upper
        assert "BC.ARCHIVED = '0'" in upper
        assert "STATUS.CODE = :STATUS_CODE_IN" in upper
        assert "HAVING SUM(BARCODE.QTY) <> 0" in upper

    def test_latest_record_uses_max_id_not_max_update_time(self) -> None:
        """``latest_record`` 语义 = MAX(id)（未经核对不得改为 MAX(update_time)）。"""
        upper = self._base_sql().upper()
        assert "MAX(ID) AS RECORD_ID" in upper
        assert "MAX(UPDATE_TIME)" not in upper  # 不按最大更新时间挑行
        assert "R.ID = LR.RECORD_ID" in upper   # 展示该行（MAX(ID) 行）的 update_time

    def test_all_fixed_tables_are_schema_qualified(self) -> None:
        """基础查询用到的固定表全部带 schema 前缀（货架表按需接入）。"""
        sql = self._base_sql()
        base_tables = tuple(
            table for table in INVENTORY_REPORT_TABLES if table != "dyn_wh_shelf"
        )
        for table in base_tables:
            assert f'"public".{table}' in sql, table
        assert "dyn_wh_shelf" not in sql  # 货架维度仅在 shelf_code 过滤时接入

    def test_values_are_bound_parameters_never_inlined(self) -> None:
        sql, params = _build_inventory_query(
            schema="public",
            material_code="MAC-001",
            org_code="ORG1",
            warehouse_code="A01",
            location_code="L01",
            shelf_code="S01",
            batch_code="B001",
            max_rows=50,
        )
        for value in ("MAC-001", "ORG1", "A01", "L01", "S01", "B001"):
            assert value not in sql, value
        assert params["material_code"] == "MAC-001"
        assert params["org_code"] == "ORG1"
        assert params["warehouse_code"] == "A01"
        assert params["location_code"] == "L01"
        assert params["shelf_code"] == "S01"
        assert params["batch_code"] == "%B001%"
        assert params["max_rows"] == 50
        assert params["status_code_in"] == "BARCODE_STATUS_IN"

    def test_optional_filters_only_when_provided(self) -> None:
        base = self._base_sql()
        for fragment in (
            "ORG.KD_STOCK_CODE = ",
            "WAREHOUSE.CODE = ",
            "LOCATION.LOCATION_CODE = ",
            "BC.CODE LIKE ",
        ):
            assert fragment not in base.upper(), fragment

        with_org = _build_inventory_query(
            schema="public", material_code="M", org_code="ORG1", max_rows=10
        )[0]
        assert "org.kd_stock_code = :org_code" in with_org

    def test_shelf_join_only_when_shelf_filter_used(self) -> None:
        """货架维度（第二份报表）：仅在 ``shelf_code`` 过滤时接入。"""
        base = self._base_sql()
        assert "dyn_wh_shelf" not in base
        with_shelf = _build_inventory_query(
            schema="public", material_code="M", shelf_code="S01", max_rows=10
        )[0]
        assert '"public".dyn_wh_shelf' in with_shelf
        assert "shelf.shelf_code = :shelf_code" in with_shelf

    def test_batch_like_is_escaped_literal(self) -> None:
        sql, params = _build_inventory_query(
            schema="public", material_code="M", batch_code="A_1", max_rows=10
        )
        assert "bc.code LIKE :batch_code ESCAPE '\\'" in sql
        assert params["batch_code"] == "%A\\_1%"  # 下划线按字面量

    def test_stat_date_is_bound(self) -> None:
        from datetime import date

        sql, params = _build_inventory_query(
            schema="public",
            material_code="M",
            stat_date=date(2026, 3, 27),
            max_rows=10,
        )
        assert "DATE(r.update_time) = :stat_date" in sql
        assert params["stat_date"] == date(2026, 3, 27)

    def test_limit_is_bound(self) -> None:
        sql, params = _build_inventory_query(
            schema="public", material_code="M", max_rows=7
        )
        assert "LIMIT :max_rows" in sql
        assert params["max_rows"] == 7

    @pytest.mark.parametrize(
        "bad_schema", ['public" OR 1=1 --', "public; DROP TABLE x", "", "  "]
    )
    def test_invalid_schema_rejected_without_query(self, bad_schema: str) -> None:
        with pytest.raises(ToolExecutionError):
            _build_inventory_query(
                schema=bad_schema, material_code="M", max_rows=10
            )

    def test_order_is_stable(self) -> None:
        """稳定排序（update_time + 维度 tiebreaker）——分页/截断可复现。"""
        upper = self._base_sql().upper()
        assert "ORDER BY R.UPDATE_TIME, WAREHOUSE.CODE" in upper
        assert "BC.CODE, UNIT.UNIT_DESC" in upper


class TestResultMappingOffline:
    def _row(self, **overrides: Any) -> dict[str, Any]:
        row = {
            "org_name": "ORG",
            "warehouse_code": "W1",
            "warehouse_name": "W1-name",
            "location_code": "L1",
            "location_name": "L1-name",
            "batch_code": "BC1",
            "material_name": "M-name",
            "unit": "PCS",
            "qty": Decimal("12.5"),
            "barcode_count": 2,
            "last_update_time": None,
            "group_count": 3,
            "qty_total": Decimal("30.5"),
        }
        row.update(overrides)
        return row

    def test_mapping_and_totals(self) -> None:
        rows = [
            self._row(),
            self._row(batch_code="BC2"),
            self._row(batch_code="BC3"),
        ]
        outcome = _map_report_rows(rows, max_rows=100)
        assert outcome["group_count"] == 3
        assert outcome["qty_total"] == 30.5
        assert outcome["truncated"] is False
        assert outcome["groups"][0]["qty"] == 12.5
        assert outcome["groups"][0]["barcode_count"] == 2
        assert outcome["groups"][0]["batch_code"] == "BC1"
        assert outcome["groups"][0]["org_name"] == "ORG"
        assert outcome["groups"][0]["last_update_time"] is None

    def test_truncation_flag_and_limited_groups(self) -> None:
        rows = [self._row(batch_code=f"BC{i}") for i in range(3)]
        outcome = _map_report_rows(rows, max_rows=1)
        assert len(outcome["groups"]) == 1
        assert outcome["group_count"] == 3  # 截断前真实总数
        assert outcome["truncated"] is True
        assert outcome["qty_total"] == 30.5  # 截断前真实总量

    def test_empty_rows_is_real_zero(self) -> None:
        outcome = _map_report_rows([], max_rows=100)
        assert outcome == {
            "qty_total": 0.0,
            "group_count": 0,
            "groups": [],
            "truncated": False,
        }

    def test_timestamp_is_isoformatted(self) -> None:
        from datetime import datetime, timezone

        row = self._row(
            last_update_time=datetime(2026, 3, 27, 6, 30, tzinfo=timezone.utc)
        )
        outcome = _map_report_rows([row], max_rows=10)
        assert outcome["groups"][0]["last_update_time"].startswith("2026-03-27")


# ============================================================
# B. 真实（本机业务库配置齐全时执行；只读）
# ============================================================

_configured = is_business_datasource_configured()

requires_business_ds = pytest.mark.skipif(
    not _configured,
    reason=(
        "WMS_DATABASE_* 未配置 ⇒ 跳过真实对照（不回退应用库；"
        "请在本机 .env 填写只读账号后重试）"
    ),
)


def _report_rows(material_code: str) -> list[dict[str, Any]]:
    """原报表行（只读事务）。"""
    engine = get_business_engine()
    assert engine is not None
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        rows = [
            dict(row)
            for row in conn.execute(
                text(REPORT_ON_HAND_SQL), {"material_code": material_code}
            ).mappings().all()
        ]
        conn.rollback()
    return rows


def _tool_rows(material_code: str, **filters: Any) -> tuple[list[dict[str, Any]], dict]:
    """Tool 等价查询（只读事务；返回行 + 映射结果）。"""
    engine = get_business_engine()
    assert engine is not None
    sql, params = _build_inventory_query(
        schema=InventoryToolSettings().schema_name,
        material_code=material_code,
        max_rows=filters.pop("max_rows", 500),
        **filters,
    )
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        rows = [dict(r) for r in conn.execute(text(sql), params).mappings().all()]
        conn.rollback()
    return rows, _map_report_rows(rows, max_rows=500)


def _report_key(row: dict[str, Any]) -> tuple:
    return (
        row["cnname"],
        row["w_code"],
        row["w_name"],
        row["location_code"],
        row["l_name"],
        row["bc_code"],
        row["unit_desc"],
        str(row["qty"]),
        int(row["barcode_count"]),
        str(row["update_time"]),
    )


def _tool_key(row: dict[str, Any]) -> tuple:
    return (
        row["org_name"],
        row["warehouse_code"],
        row["warehouse_name"],
        row["location_code"],
        row["location_name"],
        row["batch_code"],
        row["unit"],
        str(row["qty"]),
        int(row["barcode_count"]),
        str(row["last_update_time"]),
    )


def _materials_with_stock(limit: int = 3) -> list[str]:
    """取若干**真实存在条码库存**的物料（限量；只读）。"""
    engine = get_business_engine()
    assert engine is not None
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        rows = conn.execute(
            text(
                "SELECT m.material_code FROM public.dyn_wh_barcode b "
                "JOIN public.secf_sys_f_dic st ON st.id = b.status_dic_id "
                "  AND st.code = 'BARCODE_STATUS_IN' "
                "LEFT JOIN public.dyn_wh_bc bc ON bc.id = b.wh_bc_id "
                "LEFT JOIN public.dyn_erp_basic_material m ON m.id = b.material_id "
                "WHERE b.archived='0' AND bc.archived='0' "
                "  AND m.material_code IS NOT NULL "
                "GROUP BY m.material_code ORDER BY COUNT(*) DESC LIMIT :n"
            ),
            {"n": limit},
        ).all()
        conn.rollback()
    return [row[0] for row in rows]


@requires_business_ds
class TestReportConsistencyReal:
    def test_tool_matches_original_report_row_by_row(self) -> None:
        materials = _materials_with_stock(limit=3)
        assert materials, "业务库中应存在条码库存物料"
        for material in materials:
            report_rows = _report_rows(material)
            tool_rows, outcome = _tool_rows(material)
            assert len(report_rows) == len(tool_rows), material
            assert {_report_key(r) for r in report_rows} == {
                _tool_key(r) for r in tool_rows
            }, material
            report_total = sum(
                (r["qty"] for r in report_rows), Decimal("0")
            )
            assert outcome["qty_total"] == pytest.approx(float(report_total))
            assert outcome["group_count"] == len(report_rows)
            assert outcome["truncated"] is False

    def test_missing_material_returns_real_zero(self) -> None:
        rows, outcome = _tool_rows("P7-NOT-EXIST-MATERIAL-9999")
        assert rows == []
        assert outcome == {
            "qty_total": 0.0,
            "group_count": 0,
            "groups": [],
            "truncated": False,
        }

    def test_warehouse_filter_matches_report_subset(self) -> None:
        material = _materials_with_stock(limit=1)[0]
        report_rows = _report_rows(material)
        warehouse = report_rows[0]["w_code"]
        expected = [r for r in report_rows if r["w_code"] == warehouse]
        _, outcome = _tool_rows(material, warehouse_code=warehouse)
        assert outcome["group_count"] == len(expected)
        assert outcome["qty_total"] == pytest.approx(
            float(sum((r["qty"] for r in expected), Decimal("0")))
        )

    def test_batch_filter_matches_report_subset(self) -> None:
        material = _materials_with_stock(limit=1)[0]
        report_rows = _report_rows(material)
        batch = str(report_rows[0]["bc_code"])
        if not batch:
            pytest.skip("该物料批次为空 ⇒ 无法验证批次过滤")
        expected = [r for r in report_rows if batch in str(r["bc_code"])]
        _, outcome = _tool_rows(material, batch_code=batch)
        assert outcome["group_count"] == len(expected)

    def test_truncation_keeps_true_totals(self) -> None:
        materials = _materials_with_stock(limit=1)
        material = materials[0]
        full_rows, full = _tool_rows(material)
        if len(full_rows) < 2:
            pytest.skip("该物料分组数不足以验证截断语义")
        limited_sql, params = _build_inventory_query(
            schema=InventoryToolSettings().schema_name,
            material_code=material,
            max_rows=1,
        )
        engine = get_business_engine()
        assert engine is not None
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            rows = [dict(r) for r in conn.execute(text(limited_sql), params).mappings().all()]
            conn.rollback()
        outcome = _map_report_rows(rows, max_rows=1)
        assert len(outcome["groups"]) == 1
        assert outcome["truncated"] is True
        assert outcome["group_count"] == full["group_count"]
        assert outcome["qty_total"] == pytest.approx(full["qty_total"])

    def test_read_only_session_enforced(self) -> None:
        """查询后会话仍为只读（业务库零写入）。"""
        engine = get_business_engine()
        assert engine is not None
        _tool_rows("P7-NOT-EXIST-MATERIAL-9999")
        with engine.connect() as conn:
            state = conn.execute(
                text("SELECT current_setting('transaction_read_only')")
            ).scalar_one()
            assert state == "on"
            with pytest.raises(Exception):  # noqa: B017 —— 只读会话拒绝写入
                conn.execute(text("CREATE TABLE public.__p7_report_probe (id int)"))
            conn.rollback()
