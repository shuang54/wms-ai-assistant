"""Workflow 定义域（Phase 6 ADR-01 §6 / §7 / 修正 1 / 修正 4）。

模型（受限 DAG + 状态机 hybrid 的**静态**部分；ADR D1）：

```text
WorkflowDefinition = name + version + start_node_id + nodes（有向无环图）
WorkflowNode       = node_id + 类型（AI / TOOL / APPROVAL / CONDITION）+ 边 + 策略
WorkflowCondition  = 受控判定（source / operator / expected；**不做**任意表达式求值）
```

修正 1（DAG 强制，fail-closed）：
    * 禁止环 / back-edge / loop node / 隐式循环边（拓扑校验）；
    * retry **不是** graph edge —— retry 只通过**同一 node_id 的新 attempt**
      表达（``NodeRetryPolicy``）；禁止把 retry 表示成 ``A → B → B → C``。

修正 4（Registry version API）：
    * ``resolve_current(name)``      —— 新建 WorkflowRun 用（current version）；
    * ``resolve(name, version)``     —— Resume 用（按**持久化** version 解析；
      **禁止**隐式获取 current version —— 不一致 ⇒ fail-closed）。

边界：零 IO / 零 DB；不 import db / agent / services / tools / projects。
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from backend.app.workflow.errors import (
    WorkflowDefinitionAlreadyRegisteredError,
    WorkflowDefinitionError,
    WorkflowDefinitionNotFoundError,
    WorkflowDefinitionVersionMismatchError,
    WorkflowOutputError,
)
from backend.app.workflow.models import validate_json_value
from backend.app.workflow.policy import NodeRetryPolicy

__all__ = [
    # 节点类型
    "NODE_TYPE_AI",
    "NODE_TYPE_TOOL",
    "NODE_TYPE_APPROVAL",
    "NODE_TYPE_CONDITION",
    "NODE_TYPES",
    # condition 算子
    "CONDITION_OPERATORS",
    # 定义模型
    "WorkflowCondition",
    "WorkflowNode",
    "WorkflowDefinition",
    "WorkflowRegistry",
]

# ============================================================
# 常量与语法
# ============================================================

NODE_TYPE_AI: Final[str] = "AI"
NODE_TYPE_TOOL: Final[str] = "TOOL"
NODE_TYPE_APPROVAL: Final[str] = "APPROVAL"
NODE_TYPE_CONDITION: Final[str] = "CONDITION"

#: 全部合法节点类型（**不新增**：无 LOOP / PARALLEL / SUB_WORKFLOW —— 修正 1 / ADR §13）。
NODE_TYPES: Final[frozenset[str]] = frozenset(
    {NODE_TYPE_AI, NODE_TYPE_TOOL, NODE_TYPE_APPROVAL, NODE_TYPE_CONDITION}
)

#: condition 判定算子白名单（受控断言；无任意表达式 / 无 eval）。
CONDITION_OPERATORS: Final[frozenset[str]] = frozenset(
    {"EQ", "NE", "LT", "LTE", "GT", "GTE", "EXISTS", "CONTAINS"}
)

#: 引用根（受限占位符 / 映射值的来源）：
#:   input    → run 启动输入
#:   previous → 直接上游节点 output
_REFERENCE_ROOTS: Final[tuple[str, ...]] = ("input", "previous")

#: workflow name / node_id 语法（对齐项目 snake_case 标识符惯例）。
_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z_][a-z0-9_]*$")

#: Tool 名语法（对齐 ToolDefinition 名称字符集）。
_TOOL_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]+$")

#: 字段路径（点分标识符；condition.source / 引用 path 共用）。
_FIELD_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"
)

#: 占位符提取（``{...}``；内容单独校验）。
_PLACEHOLDER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\{([^{}]*)\}")

#: 长度上限（防御性；防超大定义）。
_IDENTIFIER_MAX_LENGTH: Final[int] = 64
_VERSION_MAX_LENGTH: Final[int] = 64
_TEMPLATE_MAX_LENGTH: Final[int] = 4096
_PROMPT_MAX_LENGTH: Final[int] = 1024
_RETRY_PROMPT_PLACEHOLDER_NOTE: Final[str] = (
    "占位符只允许 {input.<path>} / {previous.<path>}（受限引用；不做任意表达式求值）"
)


# ============================================================
# 内部校验辅助（纯函数）
# ============================================================


def _require_identifier(
    field_name: str, value: object, *, pattern: re.Pattern[str], max_length: int
) -> str:
    """校验标识符：非空 str + 长度 + 正则（fail-closed）。"""
    if not isinstance(value, str) or not value.strip():
        raise WorkflowDefinitionError(f"{field_name} 必须是非空 str")
    if len(value) > max_length:
        raise WorkflowDefinitionError(
            f"{field_name} 长度不能超过 {max_length}（当前: {len(value)}）"
        )
    if not pattern.match(value):
        raise WorkflowDefinitionError(
            f"{field_name} 格式非法: {value!r}"
        )
    return value


def _validate_reference(field_name: str, value: str) -> None:
    """校验受限引用：``input.<path>`` / ``previous.<path>``。"""
    if not isinstance(value, str) or not value.strip():
        raise WorkflowDefinitionError(f"{field_name} 必须是非空 str")
    root, separator, path = value.partition(".")
    if (
        root not in _REFERENCE_ROOTS
        or not separator
        or not path
        or not _FIELD_PATH_PATTERN.match(path)
    ):
        raise WorkflowDefinitionError(
            f"{field_name} 引用非法: {value!r}"
            "（只允许 input.<path> / previous.<path>）"
        )


def _validate_template(field_name: str, template: object) -> str:
    """校验 AI 模板：非空 + 占位符全部为合法受限引用 + 无孤立花括号。"""
    if not isinstance(template, str) or not template.strip():
        raise WorkflowDefinitionError(f"{field_name} 必须是非空 str")
    if len(template) > _TEMPLATE_MAX_LENGTH:
        raise WorkflowDefinitionError(
            f"{field_name} 长度不能超过 {_TEMPLATE_MAX_LENGTH}"
        )
    for placeholder in _PLACEHOLDER_PATTERN.findall(template):
        _validate_reference(f"{field_name} 占位符", placeholder)
    remainder = _PLACEHOLDER_PATTERN.sub("", template)
    if "{" in remainder or "}" in remainder:
        raise WorkflowDefinitionError(
            f"{field_name} 含孤立花括号（{_RETRY_PROMPT_PLACEHOLDER_NOTE}）"
        )
    return template


def _validate_prompt(field_name: str, value: object) -> str:
    """校验审批展示文本：非空 + 长度上限。"""
    if not isinstance(value, str) or not value.strip():
        raise WorkflowDefinitionError(f"{field_name} 必须是非空 str")
    if len(value) > _PROMPT_MAX_LENGTH:
        raise WorkflowDefinitionError(
            f"{field_name} 长度不能超过 {_PROMPT_MAX_LENGTH}"
        )
    return value


def _validate_arguments_map(arguments_map: object) -> Mapping[str, str]:
    """校验 TOOL 节点的参数映射：``{参数名: 受限引用}``。"""
    if not isinstance(arguments_map, Mapping):
        raise WorkflowDefinitionError("arguments_map 必须是 Mapping[str, str]")
    for key, reference in arguments_map.items():
        _require_identifier(
            "arguments_map 键",
            key,
            pattern=_FIELD_PATH_PATTERN,
            max_length=_IDENTIFIER_MAX_LENGTH,
        )
        _validate_reference(f"arguments_map[{key!r}]", reference)
    return arguments_map


def _require_json_scalar(field_name: str, value: object) -> object:
    """校验 JSON 标量（condition.expected）：复用值域 + 顶层标量限制。

    值域错误（``WorkflowOutputError``）在此映射为定义错误（fail-fast 语义统一）。
    """
    try:
        validate_json_value(value)
    except WorkflowOutputError as exc:
        raise WorkflowDefinitionError(
            f"{field_name} 不是合法的 JSON 值: {type(value).__name__}"
            "（必须是 JSON 标量：str / int / float / bool / None）"
        ) from exc
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise WorkflowDefinitionError(
            f"{field_name} 必须是 JSON 标量（str / int / float / bool / None）"
            f"（当前: {type(value).__name__}）"
        )
    return value


# ============================================================
# WorkflowCondition（受控判定）
# ============================================================


@dataclass(frozen=True)
class WorkflowCondition:
    """condition 节点的受控断言（frozen）。

    **不做**任意表达式求值（无 eval / 无布尔组合 —— ADR §6）。

    Attributes:
        source:   判定来源字段路径（点分标识符；对直接上游 output 求值）。
        operator: 判定算子（``CONDITION_OPERATORS`` 白名单）。
        expected: 比较值（JSON 标量；``EXISTS`` ⇒ 必须 None）。
    """

    source: str
    operator: str
    expected: object = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.strip():
            raise WorkflowDefinitionError("condition.source 必须是非空 str")
        if not _FIELD_PATH_PATTERN.match(self.source):
            raise WorkflowDefinitionError(
                f"condition.source 格式非法: {self.source!r}"
                "（点分标识符；不做任意表达式）"
            )
        if not isinstance(self.operator, str) or self.operator not in CONDITION_OPERATORS:
            raise WorkflowDefinitionError(
                f"condition.operator 非法: {self.operator!r}"
                f"（白名单: {sorted(CONDITION_OPERATORS)}）"
            )
        _require_json_scalar("condition.expected", self.expected)
        if self.operator == "EXISTS" and self.expected is not None:
            raise WorkflowDefinitionError(
                "EXISTS 判定不得携带 expected（存在性断言无比较值）"
            )


# ============================================================
# WorkflowNode
# ============================================================


@dataclass(frozen=True)
class WorkflowNode:
    """单个 Workflow 节点的静态声明（frozen；**不执行**）。

    类型-字段一致性（构造即校验；不一致 ⇒ 拒绝）：

    ```text
    AI         question_template 必填；next 可 None（终点）；retry 允许
    TOOL       tool_name 必填；arguments_map 可选；next 可 None；retry 允许
    APPROVAL   approval_prompt 必填；next 可 None；retry **禁止**（审批不可自动重试）
    CONDITION  condition + on_true + on_false 必填；next 必须 None；retry **禁止**
    ```

    Attributes:
        node_id:          节点标识（snake_case；definition 内唯一）。
        node_type:        ``NODE_TYPES`` 之一。
        next_node_id:     顺序边（None = workflow 终点）。
        on_true_node_id:  CONDITION 真分支（否则必须 None）。
        on_false_node_id: CONDITION 假分支（否则必须 None）。
        condition:        CONDITION 判定（否则必须 None）。
        tool_name:        TOOL 目标（否则必须 None）。
        arguments_map:    TOOL 参数映射（参数名 → 受限引用）。
        question_template: AI 问题模板（含 ``{input.<path>}`` /
                          ``{previous.<path>}`` 占位符）。
        approval_prompt:  APPROVAL 展示文本（非敏感）。
        retry_policy:     节点级 retry（immediate only）；**retry 不是边** ——
                          retry 只产生同一 node_id 的新 attempt（修正 1）。
    """

    node_id: str
    node_type: str
    next_node_id: str | None = None
    on_true_node_id: str | None = None
    on_false_node_id: str | None = None
    condition: WorkflowCondition | None = None
    tool_name: str | None = None
    arguments_map: Mapping[str, str] = field(default_factory=dict)
    question_template: str | None = None
    approval_prompt: str | None = None
    retry_policy: NodeRetryPolicy | None = None

    def __post_init__(self) -> None:
        _require_identifier(
            "node_id",
            self.node_id,
            pattern=_IDENTIFIER_PATTERN,
            max_length=_IDENTIFIER_MAX_LENGTH,
        )
        if not isinstance(self.node_type, str) or self.node_type not in NODE_TYPES:
            raise WorkflowDefinitionError(
                f"node_type 非法: {self.node_type!r}"
                f"（白名单: {sorted(NODE_TYPES)}）"
            )
        if self.retry_policy is not None and not isinstance(
            self.retry_policy, NodeRetryPolicy
        ):
            raise WorkflowDefinitionError(
                "retry_policy 必须是 NodeRetryPolicy 或 None"
            )

        if self.node_type == NODE_TYPE_CONDITION:
            self._validate_condition_node()
        elif self.node_type == NODE_TYPE_AI:
            self._validate_ai_node()
        elif self.node_type == NODE_TYPE_TOOL:
            self._validate_tool_node()
        else:  # NODE_TYPE_APPROVAL
            self._validate_approval_node()

    # ---------- 类型专属校验 ----------

    def _validate_condition_node(self) -> None:
        if not isinstance(self.condition, WorkflowCondition):
            raise WorkflowDefinitionError(
                "CONDITION 节点必须提供 condition（WorkflowCondition）"
            )
        for field_name, value in (
            ("on_true_node_id", self.on_true_node_id),
            ("on_false_node_id", self.on_false_node_id),
        ):
            _require_identifier(
                field_name,
                value,
                pattern=_IDENTIFIER_PATTERN,
                max_length=_IDENTIFIER_MAX_LENGTH,
            )
        if self.next_node_id is not None:
            raise WorkflowDefinitionError(
                "CONDITION 节点不得携带 next_node_id（分支由 on_true / on_false 表达）"
            )
        if self.retry_policy is not None:
            raise WorkflowDefinitionError(
                "CONDITION 节点不得声明 retry_policy（纯逻辑；无需重试）"
            )
        self._reject_extra_fields(
            ("tool_name", "question_template", "approval_prompt")
        )
        self._reject_arguments_map()

    def _validate_ai_node(self) -> None:
        _validate_template("question_template", self.question_template)
        if self.condition is not None:
            raise WorkflowDefinitionError("AI 节点不得携带 condition")
        if self.on_true_node_id is not None or self.on_false_node_id is not None:
            raise WorkflowDefinitionError(
                "AI 节点不得携带 on_true_node_id / on_false_node_id"
            )
        self._validate_optional_next_edge()
        self._reject_extra_fields(("tool_name", "approval_prompt"))
        self._reject_arguments_map()

    def _validate_tool_node(self) -> None:
        _require_identifier(
            "tool_name",
            self.tool_name,
            pattern=_TOOL_NAME_PATTERN,
            max_length=_IDENTIFIER_MAX_LENGTH,
        )
        _validate_arguments_map(self.arguments_map)
        if self.condition is not None:
            raise WorkflowDefinitionError("TOOL 节点不得携带 condition")
        if self.on_true_node_id is not None or self.on_false_node_id is not None:
            raise WorkflowDefinitionError(
                "TOOL 节点不得携带 on_true_node_id / on_false_node_id"
            )
        self._validate_optional_next_edge()
        self._reject_extra_fields(("question_template", "approval_prompt"))

    def _validate_approval_node(self) -> None:
        _validate_prompt("approval_prompt", self.approval_prompt)
        if self.retry_policy is not None:
            raise WorkflowDefinitionError(
                "APPROVAL 节点不得声明 retry_policy（人工拒绝不可自动重试）"
            )
        if self.condition is not None:
            raise WorkflowDefinitionError("APPROVAL 节点不得携带 condition")
        if self.on_true_node_id is not None or self.on_false_node_id is not None:
            raise WorkflowDefinitionError(
                "APPROVAL 节点不得携带 on_true_node_id / on_false_node_id"
            )
        self._validate_optional_next_edge()
        self._reject_extra_fields(("tool_name", "question_template"))
        self._reject_arguments_map()

    def _validate_optional_next_edge(self) -> None:
        if self.next_node_id is not None:
            _require_identifier(
                "next_node_id",
                self.next_node_id,
                pattern=_IDENTIFIER_PATTERN,
                max_length=_IDENTIFIER_MAX_LENGTH,
            )

    def _reject_extra_fields(self, field_names: tuple[str, ...]) -> None:
        for field_name in field_names:
            if getattr(self, field_name) is not None:
                raise WorkflowDefinitionError(
                    f"{self.node_type} 节点不得携带 {field_name}"
                )

    def _reject_arguments_map(self) -> None:
        if self.arguments_map:
            raise WorkflowDefinitionError(
                f"{self.node_type} 节点不得携带 arguments_map"
            )

    # ---------- 图辅助（只读） ----------

    def successors(self) -> tuple[str, ...]:
        """本节点的后继 node_id 元组（边；**不含** retry —— retry 不是边）。"""
        if self.node_type == NODE_TYPE_CONDITION:
            # 校验保证两者非 None
            return (self.on_true_node_id, self.on_false_node_id)  # type: ignore[return-value]
        if self.next_node_id is None:
            return ()
        return (self.next_node_id,)

    def is_terminal(self) -> bool:
        """是否为流程终点（无后继边）。"""
        return len(self.successors()) == 0


# ============================================================
# WorkflowDefinition + 图校验（修正 1：DAG 强制）
# ============================================================


@dataclass(frozen=True)
class WorkflowDefinition:
    """一个 Workflow 的完整静态定义（frozen）。

    图约束（构造即校验；fail-closed —— 修正 1）：
        * ``nodes`` 非空且为 tuple；node_id 唯一；
        * ``start_node_id`` 存在；
        * 全部边引用存在；
        * **无环**（禁止 back-edge / loop node / 隐式循环边；Kahn 拓扑校验）；
        * **全部节点从 start 可达**（无 dead node）；
        * retry **不是**边 —— 由 ``NodeRetryPolicy`` + 新 attempt 表达。

    Attributes:
        name:          workflow 名称（snake_case；Registry 唯一键）。
        version:       稳定版本标识（非空、无空白、≤ 64；发布后**冻结**——
                       Run 记录该值；Resume 按该值解析 —— 修正 4）。
        start_node_id: 起始节点。
        nodes:         全部节点（tuple）。
    """

    name: str
    version: str
    start_node_id: str
    nodes: tuple[WorkflowNode, ...]

    def __post_init__(self) -> None:
        _require_identifier(
            "name",
            self.name,
            pattern=_IDENTIFIER_PATTERN,
            max_length=_IDENTIFIER_MAX_LENGTH,
        )
        self._validate_version(self.version)
        if not isinstance(self.nodes, tuple):
            raise WorkflowDefinitionError(
                "nodes 必须是 tuple[WorkflowNode, ...]（frozen 定义不接受可变容器）"
            )
        if not self.nodes:
            raise WorkflowDefinitionError("nodes 不能为空（至少一个节点）")
        for node in self.nodes:
            if not isinstance(node, WorkflowNode):
                raise WorkflowDefinitionError(
                    f"nodes 元素必须是 WorkflowNode（当前: {type(node).__name__}）"
                )
        _require_identifier(
            "start_node_id",
            self.start_node_id,
            pattern=_IDENTIFIER_PATTERN,
            max_length=_IDENTIFIER_MAX_LENGTH,
        )
        _validate_dag(nodes=self.nodes, start_node_id=self.start_node_id)

    @staticmethod
    def _validate_version(version: object) -> None:
        if not isinstance(version, str) or not version.strip():
            raise WorkflowDefinitionError("version 必须是非空 str")
        if len(version) > _VERSION_MAX_LENGTH:
            raise WorkflowDefinitionError(
                f"version 长度不能超过 {_VERSION_MAX_LENGTH}"
            )
        if any(ch.isspace() for ch in version):
            raise WorkflowDefinitionError(
                "version 不得包含空白字符（稳定标识；发布后冻结）"
            )

    # ---------- 只读辅助 ----------

    def node(self, node_id: str) -> WorkflowNode:
        """按 node_id 取节点；未知 ⇒ ``WorkflowDefinitionError``（fail-closed）。"""
        for candidate in self.nodes:
            if candidate.node_id == node_id:
                return candidate
        raise WorkflowDefinitionError(
            f"definition {self.name!r} 中不存在 node_id: {node_id!r}"
        )


def _validate_dag(
    *, nodes: tuple[WorkflowNode, ...], start_node_id: str
) -> None:
    """DAG 校验：唯一 / 引用存在 / 无环 / 全部可达（修正 1）。"""
    by_id: dict[str, WorkflowNode] = {}
    for node in nodes:
        if node.node_id in by_id:
            raise WorkflowDefinitionError(f"node_id 重复: {node.node_id!r}")
        by_id[node.node_id] = node

    if start_node_id not in by_id:
        raise WorkflowDefinitionError(
            f"start_node_id 不存在: {start_node_id!r}"
        )

    # ---- 1) 全部边引用存在 ----
    for node in nodes:
        for successor in node.successors():
            if successor not in by_id:
                raise WorkflowDefinitionError(
                    f"节点 {node.node_id!r} 的边引用不存在: {successor!r}"
                )

    # ---- 2) 无环（Kahn 拓扑排序；自环 / back-edge / loop 全被拒绝） ----
    in_degree: dict[str, int] = {node_id: 0 for node_id in by_id}
    for node in nodes:
        for successor in node.successors():
            in_degree[successor] += 1
    queue: list[str] = [nid for nid, degree in in_degree.items() if degree == 0]
    processed = 0
    while queue:
        current = queue.pop()
        processed += 1
        for successor in by_id[current].successors():
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                queue.append(successor)
    if processed != len(by_id):
        raise WorkflowDefinitionError(
            "定义存在环（back-edge / loop 不被允许 —— Phase 6 只允许 DAG；"
            "retry 必须通过同一 node_id 的新 attempt 表达，而不是图上的循环边）"
        )

    # ---- 3) 全部节点从 start 可达（无 dead node） ----
    reachable: set[str] = set()
    stack: list[str] = [start_node_id]
    while stack:
        current = stack.pop()
        if current in reachable:
            continue
        reachable.add(current)
        stack.extend(by_id[current].successors())
    unreachable = sorted(set(by_id) - reachable)
    if unreachable:
        raise WorkflowDefinitionError(
            f"存在从 start 不可达的节点: {unreachable}"
        )


# ============================================================
# WorkflowRegistry（修正 4：version API）
# ============================================================


class WorkflowRegistry:
    """服务器端静态 Workflow 注册表（模式对齐 ToolRegistry / ProjectRegistry）。

    版本语义（修正 4）：
        * ``resolve_current(name)``  —— 新建 WorkflowRun 的解析入口；
        * ``resolve(name, version)`` —— Resume 的解析入口（**按持久化 version**；
          **禁止**隐式获取 current version：不一致 ⇒ fail-closed 错误）；
        * 每个 name 只能注册一次（重复注册显式失败；不做隐式替换）。

    边界：Registry **不**读 DB / **不**解析 project（project 隔离属 Step 3）。
    """

    def __init__(self) -> None:
        self._definitions: dict[str, WorkflowDefinition] = {}

    # ---------- 注册 ----------

    def register(self, definition: WorkflowDefinition) -> None:
        """注册 definition（显式失败：非法类型 / 重复 name）。

        Raises:
            WorkflowDefinitionError: definition 类型非法。
            WorkflowDefinitionAlreadyRegisteredError: 同 name 已注册。
        """
        if not isinstance(definition, WorkflowDefinition):
            raise WorkflowDefinitionError(
                "register 只接受 WorkflowDefinition"
                f"（当前: {type(definition).__name__}）"
            )
        if definition.name in self._definitions:
            raise WorkflowDefinitionAlreadyRegisteredError(definition.name)
        self._definitions[definition.name] = definition

    # ---------- 解析（修正 4） ----------

    def resolve_current(self, name: str) -> WorkflowDefinition:
        """解析**当前版本**（新建 WorkflowRun 用）。

        Raises:
            WorkflowDefinitionError: name 非法。
            WorkflowDefinitionNotFoundError: 未注册。
        """
        return self._get(name)

    def resolve(self, name: str, version: str) -> WorkflowDefinition:
        """按**持久化 version** 解析（Resume 用；禁止隐式 current）。

        Raises:
            WorkflowDefinitionError: name / version 非法。
            WorkflowDefinitionNotFoundError: name 未注册。
            WorkflowDefinitionVersionMismatchError: 持久化 version 与当前
                注册版本不一致（fail-closed —— 代码演进后不得静默续跑）。
        """
        if not isinstance(version, str) or not version.strip():
            raise WorkflowDefinitionError("version 必须是非空 str")
        definition = self._get(name)
        if definition.version != version:
            raise WorkflowDefinitionVersionMismatchError(name, version)
        return definition

    # ---------- 只读查询 ----------

    def list_definitions(self) -> tuple[WorkflowDefinition, ...]:
        """全部已注册定义（注册顺序）。"""
        return tuple(self._definitions.values())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._definitions

    def __len__(self) -> int:
        return len(self._definitions)

    # ---------- 内部 ----------

    def _get(self, name: str) -> WorkflowDefinition:
        if not isinstance(name, str) or not name.strip():
            raise WorkflowDefinitionError("name 必须是非空 str")
        definition = self._definitions.get(name)
        if definition is None:
            raise WorkflowDefinitionNotFoundError(name)
        return definition
