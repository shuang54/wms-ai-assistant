"""Workflow Runtime 绑定层（Phase 6 Step 3 · S2；ADR-01 §6/§7 + OQ-1 冻结）。

职责（**纯函数、零 IO**）：
    * 受限引用解析（``input`` / ``previous`` 两个根；严格与尝试两种语义）
    * 模板占位符渲染（``{input.<path>}`` / ``{previous.<path>}``）
    * Tool ``arguments_map`` 解析（映射为 JSON-safe 参数字典）
    * CONDITION 节点求值（OQ-1 冻结语义）

复用（**单一事实源；不复制第二套规则**）：
    * 模板 / 引用 / arguments_map 语法 → ``domain.definition`` 既有校验函数
      （``_validate_template`` / ``_validate_reference`` / ``_validate_arguments_map``
      / ``_PLACEHOLDER_PATTERN`` / ``_FIELD_PATH_PATTERN``——**domain 私有 API**：
      binding 直接复用而不复制规则；若 domain 未来公开对应 API，本模块应迁移
      （见 S2 报告 §10））；
    * operator 白名单 → ``CONDITION_OPERATORS``（公开）；
    * JSON-safe 边界 → ``validate_json_value``（公开；载荷解码后**必须**再过，
      防 `json.loads` 的 NaN/Infinity 扩展字面量绕过）。

边界：仅 import stdlib + ``backend.app.workflow``（definition / errors / models）；
      不 import db / services / tools / agent / api / llm，也不 import 同包其它模块
      （零内部耦合）。

读取语义对照（**两场景语义不同，不得混用**）：
```text
模板渲染 / arguments_map：缺失变量 ⇒ BindingError（fail-closed）
CONDITION              ：缺失路径 / 显式 null / 类型不匹配 ⇒ False（OQ-1；不抛错）
两者共同               ：非法语法 / 未知 operator / 载荷损坏 ⇒ BindingError（明确失败）
```

参数名 ``input`` / ``previous`` 与 ADR §8.2 的列名 / 引用根一致（局部 shadow
内建 ``input``——本包范围内无 interactive input 使用，安全性不受影响）。
"""
from __future__ import annotations

import json
import operator
import re
from collections.abc import Callable, Mapping
from typing import Any

from backend.app.workflow.definition import (
    CONDITION_OPERATORS,
    _PLACEHOLDER_PATTERN,
    _validate_arguments_map,
    _validate_reference,
    _validate_template,
    WorkflowCondition,
)
from backend.app.workflow.errors import (
    WorkflowDefinitionError,
    WorkflowOutputError,
)
from backend.app.workflow.models import JsonValue, validate_json_value

__all__ = [
    "BindingError",
    "resolve_reference",
    "render_template",
    "resolve_arguments_map",
    "evaluate_condition",
]

#: 有序比较算子表（仅"数值（非 bool）↔ 数值"或"str ↔ str"到达比较器）。
_ORDERED_COMPARATORS: dict[str, Callable[[Any, Any], bool]] = {
    "LT": operator.lt,
    "LTE": operator.le,
    "GT": operator.gt,
    "GTE": operator.ge,
}


class BindingError(Exception):
    """绑定层错误（引用缺失 / 类型不可渲染 / 载荷损坏 / 语法非法；fail-closed）。

    属 workflow_runtime 命名空间；**不**继承 ports 家族（binding 与端口零耦合）。
    调用方（execution）将其归一为节点失败（WF_ERROR 码由 execution 决定）。
    """


# ============================================================
# 内部：双根惰性解码（缓存）+ 受限路径遍历
# ============================================================


class _ReferenceResolver:
    """``input`` / ``previous`` 双根的惰性解码（缓存）+ 受限路径遍历。

    读取语义（冻结）：
        * 仅 **dict 键**访问（domain ``_FIELD_PATH_PATTERN`` 语法；
          **无**列表索引——``items.0`` 属非法路径 ⇒ BindingError）；
        * 缺失键 / 中途类型不可遍历 ⇒ ``try_resolve`` 返回 ``(False, None)``；
        * 显式 null ⇒ ``(True, None)``（**区分**缺失与显式 None）；
        * 载荷无法解析 / 超出 JSON-safe 边界 ⇒ ``BindingError``（不静默）。
    """

    def __init__(
        self, *, input_json: str | None, previous_json: str | None
    ) -> None:
        self._texts: dict[str, str | None] = {
            "input": input_json,
            "previous": previous_json,
        }
        self._cache: dict[str, JsonValue] = {}

    def root_value(self, root: str) -> JsonValue:
        """取根值（惰性解码 + 缓存；None = 无输入 / 无上游输出）。"""
        if root not in self._texts:
            raise BindingError(
                f"未知引用根: {root!r}（只允许 input / previous）"
            )
        if root not in self._cache:
            self._cache[root] = self._decode(root)
        return self._cache[root]

    def try_resolve(self, reference: str) -> tuple[bool, JsonValue]:
        """尝试解析受限引用（语法非法 ⇒ BindingError；缺失 ⇒ (False, None)）。"""
        self._check_reference(reference)
        root, _, path = reference.partition(".")
        return _walk(self.root_value(root), path)

    def _decode(self, root: str) -> JsonValue:
        text = self._texts[root]
        if text is None:
            return None
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BindingError(
                f"{root} 载荷不是合法 JSON 文本（fail-closed）"
            ) from exc
        try:
            validate_json_value(value)
        except WorkflowOutputError as exc:
            # 不绕过既有 JSON-safe 边界（如 json.loads 的 NaN / Infinity 扩展）
            raise BindingError(
                f"{root} 载荷不满足 JSON-safe 边界（fail-closed）"
            ) from exc
        return value

    @staticmethod
    def _check_reference(reference: str) -> None:
        try:
            _validate_reference("reference", reference)
        except WorkflowDefinitionError as exc:
            raise BindingError(f"引用语法非法: {reference!r}") from exc


def _walk(value: JsonValue, path: str) -> tuple[bool, JsonValue]:
    """沿点分路径做 dict 键遍历（无索引、无属性访问、无函数调用）。

    Returns:
        (True, value)：路径完整命中（value 可为显式 None）；
        (False, None)：任一环节缺失或中途类型不可遍历。
    """
    current: JsonValue = value
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return (False, None)
        current = current[segment]
    return (True, current)


# ============================================================
# 公开：引用解析（严格）
# ============================================================


def resolve_reference(
    reference: str, *, input: str | None, previous: str | None
) -> JsonValue:
    """严格解析受限引用（渲染 / 参数映射共用的底层入口）。

    规则：
        * ``reference`` 语法由 domain 校验（``input.<path>`` / ``previous.<path>``）；
        * ``input`` / ``previous`` 为 JSON 文本（None = 不存在该根）；
        * 缺失路径 / 载荷损坏 / 非法语法 ⇒ ``BindingError``（不静默）。
    """
    resolver = _ReferenceResolver(
        input_json=input, previous_json=previous
    )
    found, value = resolver.try_resolve(reference)
    if not found:
        raise BindingError(f"引用路径不存在: {reference!r}（fail-closed）")
    return value


# ============================================================
# 公开：模板渲染
# ============================================================


def render_template(
    template: str, *, input: str | None, previous: str | None
) -> str:
    """渲染 AI 节点模板（``{input.<path>}`` / ``{previous.<path>}``）。

    规则（冻结）：
        * 语法校验复用 domain（``_validate_template``；非法 ⇒ BindingError）；
        * 缺失变量 / 显式 null / 不可渲染类型（list / dict）⇒ ``BindingError``；
        * 合法**空字符串保留**（与缺失明确区分）；
        * 标量渲染：str 原样 / bool ⇒ ``"true"`` / ``"false"`` /
          int·float ⇒ JSON 数字词法；
        * **单遍**替换：替换文本不再扫描（不递归展开 / 不执行表达式）；
        * 非占位符文本逐字保留。
    """
    if not isinstance(template, str):
        raise BindingError(
            f"template 必须是 str（当前: {type(template).__name__}）"
        )
    try:
        _validate_template("template", template)
    except WorkflowDefinitionError as exc:
        raise BindingError("模板语法非法（复用 domain 校验）") from exc

    resolver = _ReferenceResolver(
        input_json=input, previous_json=previous
    )

    def _replace(match: re.Match[str]) -> str:
        reference = match.group(1)
        found, value = resolver.try_resolve(reference)
        if not found:
            raise BindingError(
                f"模板缺失变量: {reference!r}（fail-closed）"
            )
        return _render_scalar(reference, value)

    return _PLACEHOLDER_PATTERN.sub(_replace, template)


def _render_scalar(reference: str, value: JsonValue) -> str:
    """标量渲染（str 原样 / bool JSON 词法 / 数字 JSON 词法；其余 fail-closed）。"""
    if value is None:
        raise BindingError(
            f"模板变量为 null（不可渲染）: {reference!r}"
        )
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value, allow_nan=False)
    raise BindingError(
        f"模板变量类型不可渲染（{type(value).__name__}）: {reference!r}"
    )


# ============================================================
# 公开：Tool arguments_map
# ============================================================


def resolve_arguments_map(
    arguments_map: Mapping[str, str],
    *,
    input: str | None,
    previous: str | None,
) -> dict[str, JsonValue]:
    """逐项解析 TOOL 参数映射（``{参数名: 受限引用}`` → JSON-safe 参数字典）。

    规则：
        * schema 校验复用 domain（``_validate_arguments_map``）；
        * 全部引用经同一安全路径解析器（``_ReferenceResolver``）；
        * 缺失引用 ⇒ ``BindingError``（显式 null 是**合法**参数值）；
        * 输出为新的 dict（不修改输入 mapping），整体再经
          ``validate_json_value`` 防御性确认；
        * **不**执行 Tool / 不 import Tool Registry / 不调用 LLM；
          路径解析只做 dict 键访问（不触达任意 Python 对象属性）。
    """
    try:
        _validate_arguments_map(arguments_map)
    except WorkflowDefinitionError as exc:
        raise BindingError(
            "arguments_map schema 非法（复用 domain 校验）"
        ) from exc

    resolver = _ReferenceResolver(
        input_json=input, previous_json=previous
    )
    resolved: dict[str, JsonValue] = {}
    for key, reference in arguments_map.items():
        found, value = resolver.try_resolve(reference)
        if not found:
            raise BindingError(
                f"arguments_map 引用缺失: {key!r} -> {reference!r}（fail-closed）"
            )
        resolved[key] = value
    try:
        validate_json_value(resolved)
    except WorkflowOutputError as exc:  # pragma: no cover - 载荷已逐根校验
        raise BindingError("arguments_map 输出不满足 JSON-safe 边界") from exc
    return resolved


# ============================================================
# 公开：CONDITION 求值（OQ-1 冻结）
# ============================================================


def evaluate_condition(
    condition: WorkflowCondition, *, previous: str | None
) -> bool:
    """求值 CONDITION 节点（对**直接上游 output** 求值；OQ-1 冻结语义）。

    规则（冻结，**不得与模板缺失语义混用**）：
        * 缺失路径 / 显式 null / 类型不匹配 ⇒ 比较结果 **False**（不抛错）；
        * ``EXISTS``：路径存在且值非 null ⇒ True；
        * ``CONTAINS``：str ⇒ 子串（expected 必须 str）；
          list ⇒ 严格标量成员（无隐式转换，bool 与数值隔离）；其它 ⇒ False；
        * 有序比较（LT/LTE/GT/GTE）：仅"数值（非 bool）↔ 数值"或"str ↔ str"；
        * **未知 operator ⇒ BindingError**（不静默返回 False）；
        * ``previous`` 载荷损坏 / 非 JSON-safe ⇒ ``BindingError``
          （数据完整性问题不伪装成 False）。
    """
    if not isinstance(condition, WorkflowCondition):
        raise BindingError(
            "condition 必须是 WorkflowCondition"
            f"（当前: {type(condition).__name__}）"
        )
    operator_value = condition.operator
    if (
        not isinstance(operator_value, str)
        or operator_value not in CONDITION_OPERATORS
    ):
        raise BindingError(
            f"未知 condition operator: {operator_value!r}"
            "（fail-closed；不静默返回 False）"
        )

    resolver = _ReferenceResolver(input_json=None, previous_json=previous)
    source_value = resolver.root_value("previous")  # 损坏 ⇒ BindingError
    found, value = _walk(source_value, condition.source)

    if operator_value == "EXISTS":
        return found and value is not None
    if not found or value is None:
        return False  # OQ-1：缺失 / null ⇒ 比较类一律 false

    expected = condition.expected
    if operator_value == "EQ":
        return _strict_equals(value, expected)
    if operator_value == "NE":
        return _comparable(value, expected) and value != expected
    if operator_value in _ORDERED_COMPARATORS:
        return _ordered_compare(operator_value, value, expected)
    if operator_value == "CONTAINS":
        return _contains(value, expected)
    raise BindingError(  # pragma: no cover - 白名单保证不可达
        f"operator 未实现: {operator_value!r}（fail-closed）"
    )


def _comparable(left: JsonValue, right: JsonValue) -> bool:
    """类型兼容判定（无隐式转换；bool 与数值 / 字符串严格隔离）。"""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return True
    return isinstance(left, str) and isinstance(right, str)


def _strict_equals(left: JsonValue, right: JsonValue) -> bool:
    return _comparable(left, right) and left == right


def _ordered_compare(
    operator_value: str, left: JsonValue, right: JsonValue
) -> bool:
    """有序比较：仅"数值（非 bool）↔ 数值"或"str ↔ str"；其它 ⇒ False。"""
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    numeric_pair = isinstance(left, (int, float)) and isinstance(
        right, (int, float)
    )
    string_pair = isinstance(left, str) and isinstance(right, str)
    if not (numeric_pair or string_pair):
        return False
    comparator = _ORDERED_COMPARATORS.get(operator_value)
    if comparator is None:  # pragma: no cover - 白名单已在入口校验
        raise BindingError(f"未知有序 operator: {operator_value!r}")
    return bool(comparator(left, right))


def _contains(value: JsonValue, expected: JsonValue) -> bool:
    """CONTAINS：str ⇒ 子串（expected 必须 str）；list ⇒ 严格标量成员；其它 ⇒ False。"""
    if isinstance(value, str):
        return isinstance(expected, str) and expected in value
    if isinstance(value, list):
        return any(_strict_equals(item, expected) for item in value)
    return False
