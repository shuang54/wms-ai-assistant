"""ToolDefinition → OpenAI-compatible Tool Schema 转换层（Phase 3.6.2）。

职责边界：

    ToolRegistry.list_definitions()          （内部 DTO，含 Handler 分离设计）
        ↓ 本模块（唯一转换点）
    OpenAI Chat Completions ``tools`` 参数

设计原则（任务书 §五）：

    - ``ToolDefinition`` 保持 LLM Provider 无关（不变成 OpenAI 专用 DTO）；
    - 转换只使用 name / description / parameters 三个公开字段，
      **不**携带 ToolHandler / Python module / class / 异常对象等内部信息；
    - parameters 深拷贝后传入，避免调用方修改 LLM 请求影响 ToolDefinition。

依赖方向：

    backend.app.llm.tool_schema → backend.app.tools.base（单向，允许）

    反向依赖（ToolRegistry → LLMClient）在任务书 §二十 中被明确禁止。
"""
from __future__ import annotations

import copy
from typing import Any, Iterable

from backend.app.tools.base import ToolDefinition

__all__ = [
    "definition_to_openai_tool",
    "definitions_to_openai_tools",
]


def definition_to_openai_tool(definition: ToolDefinition) -> dict[str, Any]:
    """把单个 ``ToolDefinition`` 转换为 OpenAI-compatible tool schema。

    输出结构：

        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": {...}
            }
        }

    Args:
        definition: Tool 不可变定义。

    Returns:
        可直接放入 Chat Completions 请求 ``tools`` 字段的 dict。
    """
    return {
        "type": "function",
        "function": {
            "name": definition.name,
            "description": definition.description,
            # 深拷贝：LLM 请求体与 ToolDefinition 内部状态互不影响
            "parameters": copy.deepcopy(definition.parameters),
        },
    }


def definitions_to_openai_tools(
    definitions: Iterable[ToolDefinition],
) -> list[dict[str, Any]]:
    """批量转换（保持注册顺序）。"""
    return [
        definition_to_openai_tool(definition) for definition in definitions
    ]
