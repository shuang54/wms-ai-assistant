"""Mock Tools（Phase 3.6.1）。

为验证 Framework 而创建的 **测试用** Tool，**不**接真实 WMS / ERP / DB。
所有数据均为硬编码回显：

    * ``get_inventory`` —— 返回固定 quantity / unit
    * ``get_work_order`` —— 返回固定 status

后续接入真实 WMS API 时，按 docs/architecture.md §13 设计：

    Tool
     ↓
    WMS Client
     ↓
    HTTP API

即 Handler 内部委托 ``integrations/wms`` 模块，不直接 ``requests.get``。
本阶段仅做最小 Framework 验证。
"""
from __future__ import annotations

from typing import Any

from backend.app.tools.base import ToolDefinition, ToolHandler, ToolResult
from backend.app.tools.registry import ToolRegistry

__all__ = [
    "GET_INVENTORY_DEFINITION",
    "GET_WORK_ORDER_DEFINITION",
    "GetInventoryHandler",
    "GetWorkOrderHandler",
    "register_mock_tools",
]


# ============================================================
# get_inventory（Mock）
# ============================================================

GET_INVENTORY_DEFINITION: ToolDefinition = ToolDefinition(
    name="get_inventory",
    description=(
        "查询指定物料在指定仓库的库存数量。"
        "Mock 实现，硬编码 quantity=1000, unit=PPCS；不连 WMS / DB。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "material_code": {
                "type": "string",
                "description": "物料编码",
            },
            "warehouse_code": {
                "type": "string",
                "description": "仓库编码（可选）",
            },
        },
        "required": ["material_code"],
    },
)


class GetInventoryHandler:
    """``get_inventory`` 的 Mock Handler。

    行为：硬编码返回 inventory 字典；不发起任何 I/O。
    """

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "material_code": arguments["material_code"],
            "warehouse_code": arguments.get("warehouse_code"),
            "quantity": 1000,
            "unit": "PCS",
        }


# ============================================================
# get_work_order（Mock）
# ============================================================

GET_WORK_ORDER_DEFINITION: ToolDefinition = ToolDefinition(
    name="get_work_order",
    description=(
        "查询指定工单的状态。"
        "Mock 实现，硬编码 status=RELEASED；不连 WMS / DB。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "work_order_no": {
                "type": "string",
                "description": "工单号",
            },
        },
        "required": ["work_order_no"],
    },
)


class GetWorkOrderHandler:
    """``get_work_order`` 的 Mock Handler。

    行为：硬编码返回 work_order 字典；不发起任何 I/O。
    """

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "work_order_no": arguments["work_order_no"],
            "status": "RELEASED",
        }


# ============================================================
# 注册助手
# ============================================================

def register_mock_tools(registry: ToolRegistry) -> None:
    """把两个 Mock Tool 注册到给定 Registry（**不**操作全局状态）。"""
    registry.register(GET_INVENTORY_DEFINITION, GetInventoryHandler())
    registry.register(GET_WORK_ORDER_DEFINITION, GetWorkOrderHandler())