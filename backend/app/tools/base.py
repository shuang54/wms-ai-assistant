"""Tool Framework 核心抽象（Phase 3.6.1）。

本模块定义最小可扩展的 Tool 抽象：
        ToolDefinition      不可变 DTO（name / description / parameters Schema）
        ToolResult         统一执行结果（success / data / error）
        ToolHandler        Callable 协议（async __call__(arguments) -> data）
        validate_arguments 极简 JSON Schema 风格参数校验器

本阶段不实现完整 JSON Schema 引擎——只支持顶层 ``object`` + ``string`` /
``integer`` / ``number`` / ``boolean`` / ``array`` + ``required`` + 拒绝
未知字段。详见 ``validate_arguments`` 文档。

设计原则：
    - ``ToolDefinition`` 与 ``ToolResult`` 均为 ``frozen=True``，符合项目
      既有 DTO 风格（参考 ``RagService`` / ``RerankerEvaluationService``）。
    - 不在 DTO 中保存 handler —— handler 单独保存在 ``ToolRegistry``，
      且 ``list_tools()`` **不**对外暴露 handler。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "ToolDefinition",
    "ToolResult",
    "ToolHandler",
    "validate_arguments",
]


# ============================================================
# 名称格式校验
# ============================================================

_NAME_PATTERN_FRAGMENT: str = "^[A-Za-z0-9_-]+$"


def _validate_tool_name(name: Any) -> str:
    """校验 Tool 名称格式：非空 + 只允许 ASCII 字母 / 数字 / '_' / '-'。

    Raises:
        ValueError: 名称非 str / 空 / 包含非法字符。
    """
    if not isinstance(name, str):
        raise ValueError(
            f"Tool name 必须为 str（got {type(name).__name__}）"
        )
    if not name:
        raise ValueError("Tool name 不能为空")
    # 严格 ASCII 字符范围：避免 CJK / Unicode isalnum 误判
    for ch in name:
        if not (
            ("A" <= ch <= "Z")
            or ("a" <= ch <= "z")
            or ("0" <= ch <= "9")
            or ch in "_-"
        ):
            raise ValueError(
                f"Tool name 仅允许 ASCII 字母 / 数字 / '_' / '-'（got {name!r}）"
            )
    return name


# ============================================================
# ToolDefinition（不可变 DTO）
# ============================================================

@dataclass(frozen=True)
class ToolDefinition:
    """Tool 不可变定义（Phase 3.6.1）。

    Attributes:
        name:        Tool 唯一名称。注册时校验格式，非空、仅允许
                     ``[A-Za-z0-9_-]``；同 Registry 中不允许重复。
        description:  Tool 的自然语言说明，供 LLM 理解用途（非空）。
        parameters:  描述入参的极简 JSON Schema 风格 dict。Schema 形态：
                         {
                             "type": "object",
                             "properties": {
                                 "<field>": {"type": "<json-type>",
                                             "description": "..."},
                                 ...
                             },
                             "required": ["<field>", ...]
                         }
                     支持顶层 ``type==object`` 的子集；
                     嵌套 object / 复杂关键字（anyOf 等）不在本阶段支持范围。

    Raises:
        ValueError: name 非法、description 为空、parameters 非 dict。
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # name —— frozen dataclass 用 object.__setattr__ 已被禁用，
        # 但 __post_init__ 内赋值只是 dataclass 内部 finalize 流程的一部分，
        # 此处仅做合法性校验。
        _validate_tool_name(self.name)
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("Tool description 不能为空")
        if not isinstance(self.parameters, dict):
            raise ValueError(
                f"Tool parameters 必须为 dict（got {type(self.parameters).__name__}）"
            )


# ============================================================
# ToolResult（不可变 DTO）
# ============================================================

@dataclass(frozen=True)
class ToolResult:
    """Tool 统一执行结果（Phase 3.6.1）。

    Attributes:
        tool_name: 来源 Tool 名称。
        success:   是否执行成功；True 时 ``error`` 必须为 None、``data``
                   可为 ``None`` 或任意可序列化对象。
        data:      成功时由 Handler 返回的数据（结构由 Tool 自行决定）；
                   失败时为 None。
        error:     失败时的简短错误说明（**不含** traceback / API Key /
                   数据库连接串 / SQL 等敏感信息）；成功时为 None。

    Raises:
        ValueError: success/data/error 三者关系不符合契约。
    """

    tool_name: str
    success: bool
    data: Any | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.success and self.error is not None:
            raise ValueError("ToolResult.success=True 时 error 必须为 None")
        if not self.success and self.data is not None:
            raise ValueError("ToolResult.success=False 时 data 必须为 None")
        if not self.success and (self.error is None or not self.error.strip()):
            raise ValueError("ToolResult.success=False 时 error 必须非空")


# ============================================================
# ToolHandler（Callable 协议）
# ============================================================

@runtime_checkable
class ToolHandler(Protocol):
    """Tool Handler 协议。

    任何实现 ``async __call__(arguments: dict[str, Any]) -> Any`` 的对象
    均可作为 Handler 注册到 ``ToolRegistry``。

    Handler 内部自由选择：
        - 直接返回 dict / dataclass / str / int 等可序列化值
        - 抛出 ``ToolError`` 任意子类（Registry 会捕获并归一为 ``ToolResult(success=False)``）
        - 抛出其它异常（Registry 捕获后归一为 ``ToolExecutionError``）

    Handler 实现**必须**自行负责：
        - 参数类型校验（若 parameters Schema 校验不充分）
        - 调用 WMS / ERP 业务服务 / 数据库
        - 不返回敏感信息（API Key / Authorization / 数据库连接串 / SQL）
    """

    async def __call__(self, arguments: dict[str, Any]) -> Any:
        ...


# ============================================================
# 参数校验（极简 JSON Schema 子集）
# ============================================================

# 支持的 primitive JSON 类型（数组元素视为 any，递归对象不支持）
_PRIMITIVE_TYPES: frozenset[str] = frozenset(
    {"string", "integer", "number", "boolean", "array"}
)


def _check_primitive_type(field_name: str, value: Any, expected: str) -> None:
    """校验单个值的 primitive type 匹配。"""
    if expected == "string":
        if not isinstance(value, str):
            raise ValueError(f"expected string, got {type(value).__name__}")
    elif expected == "integer":
        # bool 是 int 的子类，必须先排除
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"expected integer, got {type(value).__name__}")
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"expected number, got {type(value).__name__}")
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"expected boolean, got {type(value).__name__}")
    elif expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"expected array, got {type(value).__name__}")
    else:
        # 任务书未要求支持其它类型；遇到未知 type 一律拒绝（防误用）
        raise ValueError(
            f"unsupported type {expected!r} for field '{field_name}'"
        )


def validate_arguments(
    parameters: Mapping[str, Any], arguments: Any
) -> None:
    """极简 JSON Schema 校验（Phase 3.6.1）。

    支持：
        * 顶层 ``type == "object"``
        * ``properties``: ``{name: {"type": "<primitive>", "description": "..."}}``
        * ``required``: ``[<field>, ...]`` —— 全部字段必须出现
        * 未知字段：拒绝（**不**静默忽略，防止 LLM 拼写错误带来隐式 bug）

    不支持（明确）：
        * 嵌套 object / 嵌套校验
        * ``anyOf`` / ``oneOf`` / ``$ref``
        * 数组元素的 item 级类型校验
        * ``additionalProperties`` / ``pattern`` / ``minimum`` 等约束

    Args:
        parameters: Tool 的参数 Schema dict。
        arguments:  调用方传入的参数对象；应为 ``dict[str, Any]``。

    Raises:
        ValueError: Schema 不合法、arguments 类型不符、required 缺失、
                    字段类型不符、出现未知字段。
    """
    if not isinstance(parameters, Mapping):
        raise ValueError(
            f"parameters 必须为 mapping（got {type(parameters).__name__}）"
        )
    if not isinstance(arguments, Mapping):
        raise ValueError(
            f"arguments 必须为 mapping（got {type(arguments).__name__}）"
        )

    schema_type = parameters.get("type", "object")
    if schema_type != "object":
        # 本阶段只接受顶层 object
        raise ValueError(
            f"parameters.type 必须为 'object'（got {schema_type!r}）"
        )

    properties = parameters.get("properties") or {}
    if not isinstance(properties, Mapping):
        raise ValueError("parameters.properties 必须为 mapping")
    required = parameters.get("required") or []
    if not isinstance(required, list):
        raise ValueError("parameters.required 必须为 list")
    if not all(isinstance(f, str) for f in required):
        raise ValueError("parameters.required 元素必须为 str")

    required_set = set(required)

    # ---- 1. required 字段必须存在 ----
    missing = [f for f in required_set if f not in arguments]
    if missing:
        raise ValueError(f"missing required field(s): {missing}")

    # ---- 2. 字段类型 + 未知字段拒绝 ----
    for field_name, field_schema in properties.items():
        if field_name not in arguments:
            continue  # 可选字段缺失合法
        value = arguments[field_name]
        if not isinstance(field_schema, Mapping):
            raise ValueError(
                f"properties['{field_name}'] 必须为 mapping"
            )
        expected_type = field_schema.get("type")
        if expected_type is None:
            raise ValueError(
                f"properties['{field_name}'] 缺少 'type'"
            )
        if expected_type not in _PRIMITIVE_TYPES:
            raise ValueError(
                f"properties['{field_name}'].type={expected_type!r} 不支持"
                f"（仅支持 {sorted(_PRIMITIVE_TYPES)}）"
            )
        _check_primitive_type(field_name, value, expected_type)

    # ---- 3. 未知字段拒绝 ----
    allowed = set(properties.keys())
    unknown = [f for f in arguments.keys() if f not in allowed]
    if unknown:
        raise ValueError(f"unknown field(s): {unknown}")