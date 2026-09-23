"""Tool Registry（Phase 3.6.1）。

内存中的 Tool 注册中心，负责：

    * 注册 Tool（``register``）
    * 注销 Tool（``unregister``）
    * 查找 Tool（``get`` / ``get_definition`` / ``list_definitions``）
    * 执行 Tool（``execute``），含参数校验 + Handler 异常归一化

约束：
    - ``list_definitions()`` **不**对外暴露 Handler；Handler 仅在
      ``execute`` 内部使用。
    - 重复注册抛 ``ToolAlreadyRegisteredError``，未知 Tool 抛 ``ToolNotFoundError``，
    不会复用 ``KeyError``/``ValueError`` 等无差别类型。
    - ``execute`` 捕获的 Handler 异常归一为 ``ToolResult(success=False)``，
    **不**把原始 traceback 写入 ``error``；仅记录 ``cause_type`` 用于归因。
    - 校验失败归一为 ``ToolResult(success=False, error=...)``；``ToolValidationError``
    不外抛（与 LLM 调用语义一致：失败 = 返回错误结果，而非中断对话）。
    - 系统级未识别异常（``Exception`` 但非 ``ToolError``）：归一为
    ``ToolResult(success=False)``，``cause_type`` 为类名；原始堆栈走 logger.error。
"""
from __future__ import annotations

import logging
from typing import Any

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

logger = logging.getLogger(__name__)

__all__ = ["ToolRegistry"]


class ToolRegistry:
    """内存中的 Tool 注册中心（Phase 3.6.1）。

    单实例行为：
        - 注册顺序保留（Python 3.7+ ``dict`` 保持插入序）。
        - 同一 Registry 内的 ``list_definitions()`` 返回稳定的元组。

    多实例行为：
        - 每个实例独立维护 ``_definitions`` / ``_handlers``；
        - 不存在隐式全局状态（满足 AGENTS.md "不使用全局可变 Registry"）。

    Example:
        >>> registry = ToolRegistry()
        >>> registry.register(get_inventory_definition, GetInventoryHandler())
        >>> registry.execute("get_inventory", {"material_code": "MAT001"})
        ToolResult(tool_name='get_inventory', success=True, data={...}, error=None)
    """

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}

    # --------------------------------------------------------
    # 注册 / 注销
    # --------------------------------------------------------

    def register(
        self, definition: ToolDefinition, handler: ToolHandler
    ) -> None:
        """注册 Tool 与其 Handler。

        Args:
            definition: Tool 不可变定义。
            handler:    实现 ``ToolHandler`` 协议的可调用对象。

        Raises:
            ToolAlreadyRegisteredError: 同名 Tool 已存在。
            ValueError: definition / handler 非法
                        （definition 校验在 ``__post_init__`` 中已抛出）。
        """
        if definition.name in self._definitions:
            raise ToolAlreadyRegisteredError(definition.name)
        self._definitions[definition.name] = definition
        self._handlers[definition.name] = handler

    def unregister(self, tool_name: str) -> None:
        """注销已注册的 Tool。

        Args:
            tool_name: 待注销的 Tool 名称。

        Raises:
            ToolNotFoundError: 该名称未注册。
        """
        if tool_name not in self._definitions:
            raise ToolNotFoundError(tool_name)
        del self._definitions[tool_name]
        del self._handlers[tool_name]

    # --------------------------------------------------------
    # 查找（不暴露 Handler）
    # --------------------------------------------------------

    def get_definition(self, tool_name: str) -> ToolDefinition:
        """按名称获取 Tool 定义。

        Args:
            tool_name: Tool 名称。

        Returns:
            对应的 ``ToolDefinition``。

        Raises:
            ToolNotFoundError: 该名称未注册。
        """
        try:
            return self._definitions[tool_name]
        except KeyError as exc:
            raise ToolNotFoundError(tool_name) from exc

    def list_definitions(self) -> tuple[ToolDefinition, ...]:
        """返回所有 Tool 定义（按注册顺序），**不含** Handler。

        用于：
            - 调试 / 审计
            - 后续 LLM Function Calling 的 schema 暴露
        """
        return tuple(self._definitions.values())

    def __contains__(self, tool_name: object) -> bool:
        return isinstance(tool_name, str) and tool_name in self._definitions

    def __len__(self) -> int:
        return len(self._definitions)

    # --------------------------------------------------------
    # 执行
    # --------------------------------------------------------

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        """执行 Tool：参数校验 + 调用 Handler + 归一化为 ``ToolResult``。

        Args:
            tool_name: Tool 名称。
            arguments: 调用方传入的参数；为 None 时按 ``{}`` 处理。

        Returns:
            ``ToolResult``：
                * 成功 → ``success=True, data=<handler 返回值>``
                * 参数校验失败 → ``success=False, error=<reason>``
                * Handler 抛 ``ToolError`` → ``success=False, error=<reason>``
                * Handler 抛其它异常 → ``success=False, error=<reason>,
                  cause_type=<异常类名>``；原始堆栈走 logger.error，
                  **不**写入 ``error`` 字段。

        异常传播：
            - ``ToolError`` 家族不会向上抛
            - 系统级未知异常（理论不会发生）也归一为 ``ToolResult(success=False)``
            - ``KeyboardInterrupt`` / ``SystemExit`` 等控制流异常原样上抛
        """
        arguments_dict = arguments if arguments is not None else {}

        # ---- 1. Tool 是否存在 ----
        if tool_name not in self._definitions:
            # 按任务书要求："Tool 不存在"也算 execute 错误结果，不上抛
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"Tool 未注册: {tool_name!r}",
            )
        definition = self._definitions[tool_name]
        handler = self._handlers[tool_name]

        # ---- 2. 参数校验 ----
        try:
            validate_arguments(definition.parameters, arguments_dict)
        except ValueError as exc:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"参数校验失败: {exc}",
            )

        # ---- 3. Handler 执行 ----
        try:
            data = await handler(arguments_dict)
        except ToolError as exc:
            # Handler 显式抛 ToolError 家族：归一为 ToolResult(success=False)
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 — execute 必须兜底
            # 不暴露 traceback / 敏感信息；仅 logger 留痕
            logger.error(
                "tool execution unexpected error",
                extra={
                    "tool_name": tool_name,
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )
            # 同样以 ToolExecutionError 形态归一，但作为结果返回（不抛）
            err = ToolExecutionError(
                tool_name=tool_name,
                reason="handler raised unexpected exception",
                cause_type=type(exc).__name__,
            )
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=str(err),
            )
        except BaseException as exc:  # KeyboardInterrupt / SystemExit
            # 控制流异常原样上抛
            logger.warning(
                "tool execution raised BaseException: %s", type(exc).__name__
            )
            raise

        return ToolResult(
            tool_name=tool_name,
            success=True,
            data=data,
        )


__all__ = [
    "ToolRegistry",
]