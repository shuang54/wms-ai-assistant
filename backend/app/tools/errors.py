"""Tool Framework 异常体系（Phase 3.6.1）。

层级：

    ToolError（基类）
    ├── ToolAlreadyRegisteredError   重复注册同名 Tool
    ├── ToolNotFoundError            Tool 未注册 / 已注销
    ├── ToolValidationError          参数 Schema 非法、required 缺失、未知字段
    └── ToolExecutionError           Handler 执行抛出的异常（不携带 traceback）

设计原则：
    - 所有 Tool Framework 异常**不**直接暴露底层 traceback。
    - Handler 内部异常应被捕获后转为 ToolExecutionError，并保留
      ``cause_type``（异常类名）便于调用方归因，但不输出原始堆栈。
"""
from __future__ import annotations

__all__ = [
    "ToolError",
    "ToolAlreadyRegisteredError",
    "ToolNotFoundError",
    "ToolValidationError",
    "ToolExecutionError",
]


class ToolError(Exception):
    """Tool Framework 通用异常基类。"""


class ToolAlreadyRegisteredError(ToolError):
    """重复注册同名 Tool。

    Attributes:
        tool_name: 冲突的 Tool 名称。
    """

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"Tool 已注册: {tool_name!r}")
        self.tool_name = tool_name


class ToolNotFoundError(ToolError):
    """Tool 未在 Registry 中找到。

    Attributes:
        tool_name: 未找到的 Tool 名称。
    """

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"Tool 未注册: {tool_name!r}")
        self.tool_name = tool_name


class ToolValidationError(ToolError):
    """参数校验失败（required 缺失、类型不符、未知字段等）。

    Attributes:
        tool_name:   出错的 Tool 名称。
        reason:       简短错误描述（不含敏感信息）。
        field_path:   触发校验失败的字段（顶层或点路径）；None 表示非字段。
    """

    def __init__(
        self,
        tool_name: str,
        reason: str,
        *,
        field_path: str | None = None,
    ) -> None:
        path = f" (field='{field_path}')" if field_path else ""
        super().__init__(f"Tool '{tool_name}' 参数校验失败: {reason}{path}")
        self.tool_name = tool_name
        self.reason = reason
        self.field_path = field_path


class ToolExecutionError(ToolError):
    """Handler 执行阶段抛出的异常（已归一化，不含原始 traceback）。

    Attributes:
        tool_name:  出错的 Tool 名称。
        cause_type: 触发该异常的原始异常类名（不暴露 traceback，
                    仅用于日志归因；不含 traceback 内容）。
    """

    def __init__(
        self,
        tool_name: str,
        reason: str,
        *,
        cause_type: str | None = None,
    ) -> None:
        prefix = f"[{cause_type}] " if cause_type else ""
        super().__init__(f"Tool '{tool_name}' 执行失败: {prefix}{reason}")
        self.tool_name = tool_name
        self.cause_type = cause_type