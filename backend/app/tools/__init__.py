"""Tool Framework（Phase 3.6.1：Tool 框架骨架）。

本阶段只解决：

    * Tool 定义 / 注册 / 查找 / 执行 / 结果标准化
    * 最小参数校验（极简 JSON Schema 子集）

**未**做：

    * LLM 自动 Tool 选择
    * Agent / LangGraph / MCP
    * 真实 WMS / ERP Tool（仅提供 ``mock_tools`` 验证 Framework）
    * 权限 / 审计 / 持久化

后续阶段（Phase 3.6.2+）将：

    * LLM Function Calling
    * 真实业务 Tool 注册

详见 docs/architecture.md §13–§15、AGENTS.md §8。
"""
from __future__ import annotations

from backend.app.tools.base import (
    ToolDefinition,
    ToolHandler,
    ToolResult,
    validate_arguments,
)
from backend.app.tools.errors import (
    ToolAlreadyRegisteredError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolValidationError,
)
from backend.app.tools.mock_tools import (
    GET_INVENTORY_DEFINITION,
    GET_WORK_ORDER_DEFINITION,
    GetInventoryHandler,
    GetWorkOrderHandler,
    register_mock_tools,
)
from backend.app.tools.registry import ToolRegistry

__all__ = [
    # 核心抽象
    "ToolDefinition",
    "ToolHandler",
    "ToolResult",
    "ToolRegistry",
    "validate_arguments",
    # 异常
    "ToolError",
    "ToolAlreadyRegisteredError",
    "ToolNotFoundError",
    "ToolValidationError",
    "ToolExecutionError",
    # Mock Tools
    "GET_INVENTORY_DEFINITION",
    "GET_WORK_ORDER_DEFINITION",
    "GetInventoryHandler",
    "GetWorkOrderHandler",
    "register_mock_tools",
]