"""Phase 7 Step 7 — 生产 Workflow 定义目录（Catalog）与注册表语义（离线）。

覆盖：

```text
1  目录定义合法且稳定（name / version / 起始节点 / 节点图形态 / 无 retry）
2  定义只读语义（无写操作节点；TOOL 节点只用真实注册的只读 Tool）
3  input_fields 推导（TOOL 参数映射 + AI 模板占位符；纯函数）
4  生产 Composition Root：get_workflow_registry() 惰性注册目录（幂等 / 同一实例）
5  命令 Runtime 装配点使用**同一**注册表（装配连线断言）
6  fail-closed：未注册名称 / 版本不匹配 ⇒ 域异常（不静默回退）
7  目录模块边界（AST：只依赖 workflow 域；零 db/services/tools/llm/network）
```

零 DB / 零 LLM / 零网络（纯域 + Composition Root 连线断言）。
"""
from __future__ import annotations

import ast
from dataclasses import is_dataclass
from pathlib import Path
from typing import Any

import pytest

from backend.app.api.workflow_definitions import collect_input_fields
from backend.app.projects.capabilities import DEFAULT_TOOL_NAMES
from backend.app.workflow.catalog import (
    DEFAULT_WORKFLOW_NAMES,
    WORKFLOW_NAME_ANSWER_REVIEW,
    WORKFLOW_NAME_INVENTORY_CHECK,
    WORKFLOW_VERSION_V1,
    build_default_workflow_definitions,
)
from backend.app.workflow.definition import (
    NODE_TYPE_AI,
    NODE_TYPE_APPROVAL,
    NODE_TYPE_TOOL,
    WorkflowDefinition,
)
from backend.app.workflow.errors import (
    WorkflowDefinitionNotFoundError,
    WorkflowDefinitionVersionMismatchError,
)
from backend.app.workflow_runtime import command_service

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CATALOG_MODULE = "backend/app/workflow/catalog.py"

__all__ = [
    "TestCatalogDefinitions",
    "TestInputFields",
    "TestRegistryComposition",
    "TestCatalogModuleBoundary",
]


def _by_name(name: str) -> WorkflowDefinition:
    return next(
        definition
        for definition in build_default_workflow_definitions()
        if definition.name == name
    )


# ============================================================
# 1 / 2：定义合法性 + 只读语义
# ============================================================


class TestCatalogDefinitions:
    def test_names_and_versions_are_stable(self) -> None:
        definitions = build_default_workflow_definitions()
        assert [d.name for d in definitions] == list(DEFAULT_WORKFLOW_NAMES)
        for definition in definitions:
            assert definition.version == WORKFLOW_VERSION_V1
        # 每次调用返回独立实例（注册表不共享可变状态）
        again = build_default_workflow_definitions()
        assert [d.name for d in again] == [d.name for d in definitions]
        assert all(is_dataclass(d) for d in definitions)

    def test_definitions_are_frozen_and_validated(self) -> None:
        """DAG / 类型-字段一致性由域层构造期强制（构造不抛 = 已通过校验）。"""
        for definition in build_default_workflow_definitions():
            assert isinstance(definition.nodes, tuple)
            node_ids = [node.node_id for node in definition.nodes]
            assert definition.start_node_id in node_ids
            assert len(node_ids) == len(set(node_ids))

    def test_inventory_check_shape(self) -> None:
        definition = _by_name(WORKFLOW_NAME_INVENTORY_CHECK)
        assert definition.start_node_id == "query_stock"
        assert [(n.node_id, n.node_type) for n in definition.nodes] == [
            ("query_stock", NODE_TYPE_TOOL),
            ("confirm_stock", NODE_TYPE_APPROVAL),
        ]
        tool_node = definition.node("query_stock")
        # 只使用**真实注册**的只读 Tool（不新建 Tool / 不伪造业务能力）
        assert tool_node.tool_name in DEFAULT_TOOL_NAMES
        assert dict(tool_node.arguments_map) == {
            "material_code": "input.material_code"
        }
        assert tool_node.next_node_id == "confirm_stock"
        assert tool_node.retry_policy is None  # fail-closed：无自动重试
        approval_node = definition.node("confirm_stock")
        assert approval_node.next_node_id is None  # 终态
        assert approval_node.retry_policy is None  # 审批不可自动重试

    def test_answer_review_shape(self) -> None:
        definition = _by_name(WORKFLOW_NAME_ANSWER_REVIEW)
        assert definition.start_node_id == "answer"
        assert [(n.node_id, n.node_type) for n in definition.nodes] == [
            ("answer", NODE_TYPE_AI),
            ("review_answer", NODE_TYPE_APPROVAL),
        ]
        ai_node = definition.node("answer")
        assert ai_node.question_template == "请回答以下问题：{input.question}"
        assert ai_node.next_node_id == "review_answer"
        assert definition.node("review_answer").next_node_id is None

    def test_no_write_semantics_anywhere(self) -> None:
        """目录只声明只读语义：节点类型 ⊆ {AI, TOOL, APPROVAL}（无 CONDITION 分支）。"""
        for definition in build_default_workflow_definitions():
            types = {node.node_type for node in definition.nodes}
            assert types <= {NODE_TYPE_AI, NODE_TYPE_TOOL, NODE_TYPE_APPROVAL}


# ============================================================
# 3：input_fields 推导
# ============================================================


class TestInputFields:
    def test_inventory_check_requires_material_code(self) -> None:
        assert collect_input_fields(_by_name(WORKFLOW_NAME_INVENTORY_CHECK)) == (
            "material_code",
        )

    def test_answer_review_requires_question(self) -> None:
        assert collect_input_fields(_by_name(WORKFLOW_NAME_ANSWER_REVIEW)) == (
            "question",
        )

    def test_previous_references_are_not_input_fields(self) -> None:
        from backend.app.workflow.definition import WorkflowNode

        definition = WorkflowDefinition(
            name="tmp_previous_only",
            version="v1",
            start_node_id="only",
            nodes=(
                WorkflowNode(
                    node_id="only",
                    node_type=NODE_TYPE_TOOL,
                    tool_name="get_inventory",
                    arguments_map={"material_code": "previous.material_code"},
                ),
            ),
        )
        assert collect_input_fields(definition) == ()

    def test_nested_path_yields_top_level_field(self) -> None:
        from backend.app.workflow.definition import WorkflowNode

        definition = WorkflowDefinition(
            name="tmp_nested",
            version="v1",
            start_node_id="only",
            nodes=(
                WorkflowNode(
                    node_id="only",
                    node_type=NODE_TYPE_TOOL,
                    tool_name="get_inventory",
                    arguments_map={"material_code": "input.payload.material_code"},
                ),
            ),
        )
        assert collect_input_fields(definition) == ("payload",)


# ============================================================
# 4 / 5 / 6：Composition Root 与 fail-closed
# ============================================================


class TestRegistryComposition:
    def test_registry_contains_catalog_and_is_idempotent(self) -> None:
        registry = command_service.get_workflow_registry()
        assert registry is command_service.get_workflow_registry()  # 同一实例
        for name in DEFAULT_WORKFLOW_NAMES:
            assert name in registry
            assert registry.resolve_current(name).version == WORKFLOW_VERSION_V1
        # 重复访问不重复注册（幂等；不会因 name 冲突抛 AlreadyRegistered）
        assert len(registry) >= len(DEFAULT_WORKFLOW_NAMES)
        assert len(command_service.get_workflow_registry()) == len(registry)

    def test_runtime_composition_root_uses_production_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """命令 Runtime 装配点必须把**生产注册表**交给 factory（装配连线）。"""
        captured: dict[str, Any] = {}

        def _spy(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return "runtime-sentinel"

        command_service.get_workflow_command_runtime.cache_clear()
        monkeypatch.setattr(command_service, "create_workflow_runtime", _spy)
        try:
            result = command_service.get_workflow_command_runtime("proj-p7s7")
        finally:
            monkeypatch.undo()
            command_service.get_workflow_command_runtime.cache_clear()
        assert result == "runtime-sentinel"
        assert captured["project_id"] == "proj-p7s7"
        assert captured["registry"] is command_service.get_workflow_registry()
        assert WORKFLOW_NAME_INVENTORY_CHECK in captured["registry"]

    def test_unknown_name_fails_closed(self) -> None:
        registry = command_service.get_workflow_registry()
        with pytest.raises(WorkflowDefinitionNotFoundError):
            registry.resolve_current("does_not_exist_p7s7")

    def test_version_mismatch_fails_closed(self) -> None:
        registry = command_service.get_workflow_registry()
        with pytest.raises(WorkflowDefinitionVersionMismatchError):
            registry.resolve(WORKFLOW_NAME_ANSWER_REVIEW, "v0")


# ============================================================
# 7：目录模块边界（AST）
# ============================================================


class TestCatalogModuleBoundary:
    def test_catalog_module_is_pure_domain(self) -> None:
        """catalog.py 只允许 import workflow 域（零 db / services / tools / llm / api）。"""
        tree = ast.parse(
            (_REPO_ROOT / _CATALOG_MODULE).read_text(encoding="utf-8")
        )
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        forbidden_prefixes = (
            "backend.app.db",
            "backend.app.services",
            "backend.app.tools",
            "backend.app.llm",
            "backend.app.api",
            "backend.app.projects",
        )
        for module in imported:
            assert not module.startswith(forbidden_prefixes), module
        assert "backend.app.workflow.definition" in imported
        assert all(
            module in {"__future__", "typing", "backend.app.workflow.definition"}
            for module in imported
        ), imported
