"""Phase 6 Step 1 —— Workflow 定义域测试（**全离线**）。

覆盖（ADR-01 §6 / §7 / 修正 1 / 修正 4）：
```text
Test 1  WorkflowCondition：source / operator / expected 校验
Test 2  WorkflowNode：类型-字段一致性（AI / TOOL / APPROVAL / CONDITION）
Test 3  WorkflowNode：模板占位符 / arguments_map 受限引用
Test 4  WorkflowDefinition：名称 / 版本 / start / nodes 基础校验
Test 5  DAG 强制（修正 1）：自环 / 直接环 / 间接环 / 分支环 全部拒绝
Test 6  可达性：dead node 拒绝
Test 7  Registry（修正 4）：resolve_current / resolve(name, version) /
        重复注册 / 版本不一致 fail-closed
```

边界：零 DB / 零 LLM / 零网络 / 零持久化。
"""
from __future__ import annotations

import pytest

from backend.app.workflow.definition import (
    CONDITION_OPERATORS,
    NODE_TYPE_AI,
    NODE_TYPE_APPROVAL,
    NODE_TYPE_CONDITION,
    NODE_TYPE_TOOL,
    NODE_TYPES,
    WorkflowCondition,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRegistry,
)
from backend.app.workflow.errors import (
    WorkflowDefinitionAlreadyRegisteredError,
    WorkflowDefinitionError,
    WorkflowDefinitionNotFoundError,
    WorkflowDefinitionVersionMismatchError,
)
from backend.app.workflow.policy import NodeRetryPolicy

__all__ = [
    "TestWorkflowCondition",
    "TestWorkflowNode",
    "TestTemplatesAndMappings",
    "TestWorkflowDefinition",
    "TestDagEnforcement",
    "TestReachability",
    "TestWorkflowRegistry",
]


# ============================================================
# 构造辅助
# ============================================================


def _ai(node_id: str, *, next_node_id: str | None = None, **kwargs: object) -> WorkflowNode:
    return WorkflowNode(
        node_id=node_id,
        node_type=NODE_TYPE_AI,
        question_template=kwargs.pop("question_template", f"问题 {node_id}"),
        next_node_id=next_node_id,
        **kwargs,  # type: ignore[arg-type]
    )


def _tool(node_id: str, *, next_node_id: str | None = None, **kwargs: object) -> WorkflowNode:
    return WorkflowNode(
        node_id=node_id,
        node_type=NODE_TYPE_TOOL,
        tool_name=kwargs.pop("tool_name", "get_inventory"),
        next_node_id=next_node_id,
        **kwargs,  # type: ignore[arg-type]
    )


def _approval(node_id: str, *, next_node_id: str | None = None) -> WorkflowNode:
    return WorkflowNode(
        node_id=node_id,
        node_type=NODE_TYPE_APPROVAL,
        approval_prompt="请确认",
        next_node_id=next_node_id,
    )


def _condition(
    node_id: str, *, on_true: str, on_false: str, source: str = "qty", operator: str = "GT",
    expected: object = 0,
) -> WorkflowNode:
    return WorkflowNode(
        node_id=node_id,
        node_type=NODE_TYPE_CONDITION,
        condition=WorkflowCondition(source=source, operator=operator, expected=expected),
        on_true_node_id=on_true,
        on_false_node_id=on_false,
    )


def _linear_definition(**kwargs: object) -> WorkflowDefinition:
    """三节点线性定义 a → b → c（c 终点）。"""
    defaults: dict[str, object] = {
        "name": "linear_flow",
        "version": "v1",
        "start_node_id": "a",
        "nodes": (_ai("a", next_node_id="b"), _tool("b", next_node_id="c"), _ai("c")),
    }
    defaults.update(kwargs)
    return WorkflowDefinition(**defaults)  # type: ignore[arg-type]


# ============================================================
# Test 1：WorkflowCondition
# ============================================================


class TestWorkflowCondition:
    def test_valid_eq(self) -> None:
        condition = WorkflowCondition(source="qty", operator="EQ", expected=1)
        assert condition.source == "qty"
        assert condition.operator == "EQ"

    def test_valid_exists(self) -> None:
        condition = WorkflowCondition(source="result.status", operator="EXISTS")
        assert condition.expected is None

    def test_operator_whitelist(self) -> None:
        assert CONDITION_OPERATORS == {
            "EQ",
            "NE",
            "LT",
            "LTE",
            "GT",
            "GTE",
            "EXISTS",
            "CONTAINS",
        }

    @pytest.mark.parametrize("bad_source", ["", " ", "a..b", ".a", "a.", "a-b", 1, None])
    def test_invalid_source_rejected(self, bad_source: object) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowCondition(source=bad_source, operator="EQ")  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad_operator", ["eq", "IN", "", None, 1])
    def test_invalid_operator_rejected(self, bad_operator: object) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowCondition(source="qty", operator=bad_operator)  # type: ignore[arg-type]

    def test_exists_with_expected_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowCondition(source="qty", operator="EXISTS", expected=1)

    @pytest.mark.parametrize("bad_expected", [[1], {"a": 1}, object()])
    def test_non_scalar_expected_rejected(self, bad_expected: object) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowCondition(source="qty", operator="EQ", expected=bad_expected)


# ============================================================
# Test 2：WorkflowNode 类型-字段一致性
# ============================================================


class TestWorkflowNode:
    def test_node_types_are_frozen_four(self) -> None:
        assert NODE_TYPES == {"AI", "TOOL", "APPROVAL", "CONDITION"}
        # 修正 1：无 LOOP / PARALLEL / SUB_WORKFLOW
        assert not (NODE_TYPES & {"LOOP", "PARALLEL", "SUB_WORKFLOW"})

    def test_ai_node_valid(self) -> None:
        node = _ai("a", next_node_id="b")
        assert node.successors() == ("b",)
        assert not node.is_terminal()

    def test_ai_terminal_node(self) -> None:
        node = _ai("a")
        assert node.successors() == ()
        assert node.is_terminal()

    def test_ai_with_retry_allowed(self) -> None:
        node = _ai("a", retry_policy=NodeRetryPolicy(max_attempts=2))
        assert node.retry_policy is not None
        assert node.retry_policy.max_attempts == 2

    def test_ai_without_template_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowNode(node_id="a", node_type=NODE_TYPE_AI)

    def test_tool_node_valid_with_arguments(self) -> None:
        node = _tool(
            "b",
            arguments_map={"material_code": "input.material_code"},
        )
        assert node.arguments_map == {"material_code": "input.material_code"}

    def test_tool_without_name_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowNode(node_id="b", node_type=NODE_TYPE_TOOL)

    def test_approval_node_valid(self) -> None:
        node = _approval("c")
        assert node.approval_prompt == "请确认"

    def test_approval_with_retry_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowNode(
                node_id="c",
                node_type=NODE_TYPE_APPROVAL,
                approval_prompt="请确认",
                retry_policy=NodeRetryPolicy(max_attempts=2),
            )

    def test_condition_node_valid(self) -> None:
        node = _condition("d", on_true="a", on_false="a")
        assert set(node.successors()) == {"a"}

    def test_condition_without_branches_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowNode(
                node_id="d",
                node_type=NODE_TYPE_CONDITION,
                condition=WorkflowCondition(source="qty", operator="GT", expected=1),
            )

    def test_condition_with_next_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowNode(
                node_id="d",
                node_type=NODE_TYPE_CONDITION,
                condition=WorkflowCondition(source="qty", operator="GT", expected=1),
                on_true_node_id="a",
                on_false_node_id="b",
                next_node_id="c",
            )

    def test_condition_with_retry_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowNode(
                node_id="d",
                node_type=NODE_TYPE_CONDITION,
                condition=WorkflowCondition(source="qty", operator="GT", expected=1),
                on_true_node_id="a",
                on_false_node_id="b",
                retry_policy=NodeRetryPolicy(max_attempts=2),
            )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"tool_name": "get_inventory"},  # AI 不得携带 tool_name
            {"approval_prompt": "x"},  # AI 不得携带 approval_prompt
            {"condition": WorkflowCondition(source="q", operator="EQ")},
            {"on_true_node_id": "b", "on_false_node_id": "c"},
        ],
    )
    def test_ai_extra_fields_rejected(self, kwargs: dict[str, object]) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowNode(
                node_id="a",
                node_type=NODE_TYPE_AI,
                question_template="问题",
                **kwargs,  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize("bad_node_id", ["", "A", "1a", "a-b", "a b"])
    def test_invalid_node_id_rejected(self, bad_node_id: str) -> None:
        with pytest.raises(WorkflowDefinitionError):
            _ai(bad_node_id)


# ============================================================
# Test 3：模板 / 映射受限引用
# ============================================================


class TestTemplatesAndMappings:
    def test_valid_template_placeholders(self) -> None:
        node = _ai(
            "a",
            question_template="查询 {input.material_code}，参考 {previous.qty}",
        )
        assert node.question_template is not None

    def test_plain_template_without_placeholder(self) -> None:
        node = _ai("a", question_template="查询全部库存")
        assert node.question_template is not None

    @pytest.mark.parametrize(
        "bad_template",
        [
            "{bogus.field}",  # 非白名单根
            "{input}",  # 缺 path
            "{input.}",
            "{previous..x}",
            "查询 {input.material_code",  # 孤立花括号（缺闭合）
            "查询 input.material_code}",  # 孤立花括号（缺开启）
        ],
    )
    def test_invalid_template_rejected(self, bad_template: str) -> None:
        with pytest.raises(WorkflowDefinitionError):
            _ai("a", question_template=bad_template)

    def test_valid_arguments_map(self) -> None:
        node = _tool(
            "b",
            arguments_map={
                "material_code": "input.material_code",
                "warehouse": "previous.warehouse",
            },
        )
        assert len(node.arguments_map) == 2

    @pytest.mark.parametrize(
        "bad_ref",
        ["material_code", "user.material", "input", "input.", "input..x", 1],
    )
    def test_invalid_arguments_reference_rejected(self, bad_ref: object) -> None:
        with pytest.raises(WorkflowDefinitionError):
            _tool("b", arguments_map={"material_code": bad_ref})  # type: ignore[dict-item]


# ============================================================
# Test 4：WorkflowDefinition 基础校验
# ============================================================


class TestWorkflowDefinition:
    def test_linear_definition_valid(self) -> None:
        definition = _linear_definition()
        assert definition.name == "linear_flow"
        assert definition.version == "v1"
        assert definition.node("b").node_id == "b"

    def test_branch_definition_valid(self) -> None:
        definition = WorkflowDefinition(
            name="branch_flow",
            version="v1",
            start_node_id="a",
            nodes=(
                _tool("a", next_node_id="c"),
                _condition("c", on_true="t", on_false="f", source="qty", operator="GT"),
                _ai("t"),
                _ai("f"),
            ),
        )
        assert definition.node("c").node_type == NODE_TYPE_CONDITION

    @pytest.mark.parametrize("bad_name", ["", "A", "1a", "a-b", "a b"])
    def test_invalid_name_rejected(self, bad_name: str) -> None:
        with pytest.raises(WorkflowDefinitionError):
            _linear_definition(name=bad_name)

    @pytest.mark.parametrize("bad_version", ["", " ", "v 1", "a\tb", "x" * 65])
    def test_invalid_version_rejected(self, bad_version: str) -> None:
        with pytest.raises(WorkflowDefinitionError):
            _linear_definition(version=bad_version)

    def test_empty_nodes_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowDefinition(
                name="empty", version="v1", start_node_id="a", nodes=()
            )

    def test_nodes_must_be_tuple(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowDefinition(
                name="bad",
                version="v1",
                start_node_id="a",
                nodes=[_ai("a")],  # type: ignore[arg-type]
            )

    def test_duplicate_node_id_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowDefinition(
                name="dup",
                version="v1",
                start_node_id="a",
                nodes=(_ai("a"), _ai("a")),
            )

    def test_missing_start_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowDefinition(
                name="nostart",
                version="v1",
                start_node_id="zzz",
                nodes=(_ai("a"),),
            )

    def test_missing_edge_reference_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowDefinition(
                name="dangling",
                version="v1",
                start_node_id="a",
                nodes=(_ai("a", next_node_id="ghost"),),
            )

    def test_node_lookup_unknown_rejected(self) -> None:
        definition = _linear_definition()
        with pytest.raises(WorkflowDefinitionError):
            definition.node("ghost")


# ============================================================
# Test 5：DAG 强制（修正 1）
# ============================================================


class TestDagEnforcement:
    """修正 1：禁止环 / back-edge / loop node / 隐式循环边。"""

    def test_self_loop_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowDefinition(
                name="self_loop",
                version="v1",
                start_node_id="a",
                nodes=(_ai("a", next_node_id="a"),),
            )

    def test_direct_cycle_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowDefinition(
                name="cycle2",
                version="v1",
                start_node_id="a",
                nodes=(
                    _ai("a", next_node_id="b"),
                    _ai("b", next_node_id="a"),
                ),
            )

    def test_indirect_cycle_rejected(self) -> None:
        """A → B → C → A（back-edge）。"""
        with pytest.raises(WorkflowDefinitionError):
            WorkflowDefinition(
                name="cycle3",
                version="v1",
                start_node_id="a",
                nodes=(
                    _ai("a", next_node_id="b"),
                    _ai("b", next_node_id="c"),
                    _ai("c", next_node_id="a"),
                ),
            )

    def test_condition_branch_cycle_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowDefinition(
                name="cond_cycle",
                version="v1",
                start_node_id="a",
                nodes=(
                    _ai("a", next_node_id="c"),
                    _condition("c", on_true="a", on_false="t"),
                    _ai("t"),
                ),
            )

    def test_retry_is_not_graph_edge(self) -> None:
        """修正 1：retry 通过 NodeRetryPolicy 表达；图上无任何 retry 边。"""
        definition = WorkflowDefinition(
            name="retry_flow",
            version="v1",
            start_node_id="a",
            nodes=(
                _ai("a", retry_policy=NodeRetryPolicy(max_attempts=3)),
            ),
        )
        # 图结构只有单节点；retry 不产生自环
        node = definition.node("a")
        assert node.successors() == ()
        assert node.retry_policy is not None
        assert node.retry_policy.max_attempts == 3


# ============================================================
# Test 6：可达性
# ============================================================


class TestReachability:
    def test_unreachable_node_rejected(self) -> None:
        with pytest.raises(WorkflowDefinitionError):
            WorkflowDefinition(
                name="dead_node",
                version="v1",
                start_node_id="a",
                nodes=(_ai("a"), _ai("island")),
            )

    def test_shared_successor_is_valid_dag(self) -> None:
        """菱形（a → b/c → d）是合法 DAG。"""
        definition = WorkflowDefinition(
            name="diamond",
            version="v1",
            start_node_id="a",
            nodes=(
                _condition("a", on_true="b", on_false="c"),
                _ai("b", next_node_id="d"),
                _ai("c", next_node_id="d"),
                _ai("d"),
            ),
        )
        assert definition.node("d").is_terminal()


# ============================================================
# Test 7：Registry（修正 4：version API）
# ============================================================


class TestWorkflowRegistry:
    def test_register_and_resolve_current(self) -> None:
        registry = WorkflowRegistry()
        definition = _linear_definition()
        registry.register(definition)
        assert registry.resolve_current("linear_flow") is definition
        assert "linear_flow" in registry
        assert len(registry) == 1

    def test_duplicate_register_rejected(self) -> None:
        registry = WorkflowRegistry()
        registry.register(_linear_definition())
        with pytest.raises(WorkflowDefinitionAlreadyRegisteredError):
            registry.register(_linear_definition())

    def test_register_type_checked(self) -> None:
        registry = WorkflowRegistry()
        with pytest.raises(WorkflowDefinitionError):
            registry.register("not-a-definition")  # type: ignore[arg-type]

    def test_resolve_with_persisted_version(self) -> None:
        registry = WorkflowRegistry()
        definition = _linear_definition(version="v1")
        registry.register(definition)
        assert registry.resolve("linear_flow", "v1") is definition

    def test_resolve_version_mismatch_fail_closed(self) -> None:
        """修正 4：禁止 Resume 隐式获取 current version。"""
        # 模拟代码演进：新版部署注册 v2；旧 run 的持久化 version = v1
        registry_v2 = WorkflowRegistry()
        registry_v2.register(_linear_definition(version="v2"))
        with pytest.raises(WorkflowDefinitionVersionMismatchError):
            registry_v2.resolve("linear_flow", "v1")

    def test_resolve_unknown_name(self) -> None:
        registry = WorkflowRegistry()
        with pytest.raises(WorkflowDefinitionNotFoundError):
            registry.resolve_current("ghost")
        with pytest.raises(WorkflowDefinitionNotFoundError):
            registry.resolve("ghost", "v1")

    @pytest.mark.parametrize("bad_version", ["", " ", None])
    def test_resolve_invalid_version_arg(self, bad_version: object) -> None:
        registry = WorkflowRegistry()
        registry.register(_linear_definition())
        with pytest.raises(WorkflowDefinitionError):
            registry.resolve("linear_flow", bad_version)  # type: ignore[arg-type]

    def test_list_definitions_order(self) -> None:
        registry = WorkflowRegistry()
        first = _linear_definition()
        second = _linear_definition(name="second_flow")
        registry.register(first)
        registry.register(second)
        assert registry.list_definitions() == (first, second)
