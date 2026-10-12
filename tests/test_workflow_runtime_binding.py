"""Phase 6 Step 3 — S2 binding 层测试（**全离线**；零 DB / 零网络 / 零 LLM）。

覆盖（用户 S2 任务书 §七 清单）：
```text
Test 1  路径读取（input / previous / 多层 / 空串 vs null / 非法根 / 缺失 /
        类型不匹配 / 列表索引不支持 / 非法 JSON / NaN 载荷绕过防护）
Test 2  模板渲染（单/多占位符 / 文本保留 / 缺失失败 / 空串合法 / null 不可渲染 /
        标量词法 / 非法语法 / 不递归 / 不执行表达式）
Test 3  arguments_map（正常 / 多字段 / 结构值 / null 值 / 缺失 / 非法 schema /
        不修改输入）
Test 4  CONDITION（EQ/NE 类型隔离 / 有序比较 / EXISTS / CONTAINS str·list /
        缺失与 null ⇒ false / 未知 operator ⇒ 报错 / 白名单全覆盖冒烟）
Test 5  binding import 边界（AST：仅 stdlib + 域；同包零耦合；禁入包缺席）
```
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from backend.app.workflow.definition import (
    CONDITION_OPERATORS,
    WorkflowCondition,
)
from backend.app.workflow_runtime.binding import (
    BindingError,
    evaluate_condition,
    render_template,
    resolve_arguments_map,
    resolve_reference,
)

__all__ = [
    "TestResolveReference",
    "TestRenderTemplate",
    "TestResolveArgumentsMap",
    "TestEvaluateCondition",
    "TestBindingImportBoundary",
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BINDING_PATH = (
    _REPO_ROOT / "backend" / "app" / "workflow_runtime" / "binding.py"
)

_INPUT = json.dumps(
    {
        "order_no": "SO-2026-0001",
        "note": "",
        "flag": True,
        "price": 1.5,
        "nothing": None,
        "tpl": "{previous.total_qty}",
        "meta": {"qty": 3, "label": "主仓库", "warehouse": {"code": "WH-1"}},
        "tags": ["a", "b"],
        "cost": {"amount": 10, "currency": "USD"},
    },
    ensure_ascii=False,
)

_PREVIOUS = json.dumps(
    {
        "total_qty": 12,
        "zero": 0,
        "status": "LOW",
        "items": [1, 2, 3],
        "names": ["alpha", "beta"],
        "nested": {"deep": {"value": "x"}},
        "null_value": None,
        "truthy": True,
    },
    ensure_ascii=False,
)


def _cond(source: str, operator: str, expected: object = None) -> WorkflowCondition:
    return WorkflowCondition(source=source, operator=operator, expected=expected)


# ============================================================
# Test 1：路径读取
# ============================================================


class TestResolveReference:
    def test_input_root_string(self) -> None:
        assert (
            resolve_reference("input.order_no", input=_INPUT, previous=None)
            == "SO-2026-0001"
        )

    def test_input_root_number_and_nested(self) -> None:
        assert resolve_reference("input.meta.qty", input=_INPUT, previous=None) == 3
        assert (
            resolve_reference(
                "input.meta.warehouse.code", input=_INPUT, previous=None
            )
            == "WH-1"
        )

    def test_empty_string_is_a_legitimate_value(self) -> None:
        assert resolve_reference("input.note", input=_INPUT, previous=None) == ""

    def test_explicit_null_resolves_to_none(self) -> None:
        assert (
            resolve_reference("input.nothing", input=_INPUT, previous=None) is None
        )

    def test_previous_root(self) -> None:
        assert (
            resolve_reference("previous.total_qty", input=None, previous=_PREVIOUS)
            == 12
        )
        assert (
            resolve_reference(
                "previous.nested.deep.value", input=None, previous=_PREVIOUS
            )
            == "x"
        )

    def test_missing_root_prefix_is_illegal(self) -> None:
        for reference in ("order_no", "input", "session.x", "INPUT.order_no"):
            with pytest.raises(BindingError):
                resolve_reference(reference, input=_INPUT, previous=_PREVIOUS)

    def test_missing_key_fails(self) -> None:
        with pytest.raises(BindingError):
            resolve_reference("input.missing", input=_INPUT, previous=None)
        with pytest.raises(BindingError):
            resolve_reference(
                "input.meta.missing", input=_INPUT, previous=None
            )

    def test_type_mismatch_midpath_fails(self) -> None:
        # qty 是 int，无法继续遍历
        with pytest.raises(BindingError):
            resolve_reference(
                "input.meta.qty.deeper", input=_INPUT, previous=None
            )

    def test_list_index_is_not_supported_by_domain_syntax(self) -> None:
        # domain `_FIELD_PATH_PATTERN` 只允许点分标识符（无列表索引）
        with pytest.raises(BindingError):
            resolve_reference("input.tags.0", input=_INPUT, previous=None)

    def test_none_input_means_no_input(self) -> None:
        with pytest.raises(BindingError):
            resolve_reference("input.order_no", input=None, previous=None)

    def test_invalid_json_payload_fails(self) -> None:
        with pytest.raises(BindingError):
            resolve_reference("input.x", input="{bad", previous=None)
        with pytest.raises(BindingError):
            resolve_reference("previous.x", input=None, previous="not json")

    def test_nan_payload_does_not_bypass_json_safe_boundary(self) -> None:
        # json.loads 默认接受 NaN / Infinity 扩展字面量——必须被域边界拦截
        with pytest.raises(BindingError):
            resolve_reference("input.x", input='{"x": NaN}', previous=None)

    def test_non_str_reference_fails(self) -> None:
        with pytest.raises(BindingError):
            resolve_reference(None, input=_INPUT, previous=None)  # type: ignore[arg-type]


# ============================================================
# Test 2：模板渲染
# ============================================================


class TestRenderTemplate:
    def test_single_placeholder(self) -> None:
        assert (
            render_template("单号 {input.order_no}", input=_INPUT, previous=None)
            == "单号 SO-2026-0001"
        )

    def test_multiple_and_repeated_placeholders_mixed_roots(self) -> None:
        rendered = render_template(
            "{previous.total_qty}/{input.meta.qty}/{previous.total_qty}",
            input=_INPUT,
            previous=_PREVIOUS,
        )
        assert rendered == "12/3/12"

    def test_plain_text_is_preserved(self) -> None:
        assert (
            render_template("请检查库存，无需替换", input=None, previous=None)
            == "请检查库存，无需替换"
        )

    def test_chinese_value_preserved(self) -> None:
        assert (
            render_template("仓库 {input.meta.label}", input=_INPUT, previous=None)
            == "仓库 主仓库"
        )

    def test_empty_string_value_renders_empty(self) -> None:
        assert (
            render_template("[{input.note}]", input=_INPUT, previous=None) == "[]"
        )

    def test_missing_variable_fails(self) -> None:
        with pytest.raises(BindingError):
            render_template("{input.missing}", input=_INPUT, previous=None)
        with pytest.raises(BindingError):
            render_template(
                "{previous.total_qty}", input=_INPUT, previous=None
            )

    def test_null_value_is_not_renderable(self) -> None:
        with pytest.raises(BindingError):
            render_template("{input.nothing}", input=_INPUT, previous=None)

    def test_list_and_dict_values_are_not_renderable(self) -> None:
        with pytest.raises(BindingError):
            render_template("{input.tags}", input=_INPUT, previous=None)
        with pytest.raises(BindingError):
            render_template("{input.cost}", input=_INPUT, previous=None)

    def test_scalar_lexemes(self) -> None:
        assert render_template("{input.flag}", input=_INPUT, previous=None) == "true"
        assert render_template("{input.price}", input=_INPUT, previous=None) == "1.5"
        assert render_template("{input.meta.qty}", input=_INPUT, previous=None) == "3"

    def test_illegal_syntax_fails(self) -> None:
        for template in ("数量 {", "数量 }", "{}", "", "  "):
            with pytest.raises(BindingError):
                render_template(template, input=_INPUT, previous=None)

    def test_expression_content_fails(self) -> None:
        for template in ("{1+1}", "{input.a + 1}", "{__import__('os')}"):
            with pytest.raises(BindingError):
                render_template(template, input=_INPUT, previous=None)

    def test_no_recursive_expansion(self) -> None:
        # 值文本 "{previous.total_qty}" 必须原样保留，不得再次展开
        assert (
            render_template("{input.tpl}", input=_INPUT, previous=_PREVIOUS)
            == "{previous.total_qty}"
        )

    def test_non_str_template_fails(self) -> None:
        with pytest.raises(BindingError):
            render_template(123, input=_INPUT, previous=None)  # type: ignore[arg-type]


# ============================================================
# Test 3：arguments_map
# ============================================================


class TestResolveArgumentsMap:
    def test_single_field(self) -> None:
        assert resolve_arguments_map(
            {"order_no": "input.order_no"}, input=_INPUT, previous=None
        ) == {"order_no": "SO-2026-0001"}

    def test_multiple_fields_mixed_roots(self) -> None:
        resolved = resolve_arguments_map(
            {
                "order_no": "input.order_no",
                "qty": "previous.total_qty",
                "label": "input.meta.label",
            },
            input=_INPUT,
            previous=_PREVIOUS,
        )
        assert resolved == {
            "order_no": "SO-2026-0001",
            "qty": 12,
            "label": "主仓库",
        }

    def test_structural_values_are_json_safe(self) -> None:
        resolved = resolve_arguments_map(
            {"items": "previous.items", "cost": "input.cost"},
            input=_INPUT,
            previous=_PREVIOUS,
        )
        assert resolved == {
            "items": [1, 2, 3],
            "cost": {"amount": 10, "currency": "USD"},
        }

    def test_explicit_null_is_a_legitimate_value(self) -> None:
        assert resolve_arguments_map(
            {"v": "input.nothing"}, input=_INPUT, previous=None
        ) == {"v": None}

    def test_empty_map(self) -> None:
        assert resolve_arguments_map({}, input=_INPUT, previous=_PREVIOUS) == {}

    def test_missing_reference_fails(self) -> None:
        with pytest.raises(BindingError):
            resolve_arguments_map(
                {"order_no": "input.missing"}, input=_INPUT, previous=None
            )

    def test_illegal_schema_fails(self) -> None:
        for bad in (None, [], "x"):
            with pytest.raises(BindingError):
                resolve_arguments_map(bad, input=_INPUT, previous=None)  # type: ignore[arg-type]
        with pytest.raises(BindingError):
            resolve_arguments_map(
                {"bad-key": "input.order_no"}, input=_INPUT, previous=None
            )
        with pytest.raises(BindingError):
            resolve_arguments_map(
                {"k": 1}, input=_INPUT, previous=None  # type: ignore[dict-item]
            )
        with pytest.raises(BindingError):
            resolve_arguments_map(
                {"k": "session.x"}, input=_INPUT, previous=None
            )

    def test_input_mapping_is_not_mutated(self) -> None:
        source = {"order_no": "input.order_no"}
        snapshot = dict(source)
        resolve_arguments_map(source, input=_INPUT, previous=None)
        assert source == snapshot


# ============================================================
# Test 4：CONDITION（OQ-1 冻结）
# ============================================================


class TestEvaluateCondition:
    def test_eq_true_and_false(self) -> None:
        assert evaluate_condition(_cond("total_qty", "EQ", 12), previous=_PREVIOUS)
        assert not evaluate_condition(
            _cond("total_qty", "EQ", 13), previous=_PREVIOUS
        )

    def test_eq_type_mismatch_is_false(self) -> None:
        assert not evaluate_condition(
            _cond("total_qty", "EQ", "12"), previous=_PREVIOUS
        )

    def test_eq_bool_numeric_trap_is_false(self) -> None:
        # truthy=True：True ≢ 1
        assert not evaluate_condition(
            _cond("truthy", "EQ", 1), previous=_PREVIOUS
        )
        assert evaluate_condition(
            _cond("truthy", "EQ", True), previous=_PREVIOUS
        )

    def test_ne(self) -> None:
        assert evaluate_condition(
            _cond("total_qty", "NE", 13), previous=_PREVIOUS
        )
        # 类型不匹配 ⇒ false（OQ-1：不隐式转换）
        assert not evaluate_condition(
            _cond("total_qty", "NE", "12"), previous=_PREVIOUS
        )
        # 显式 null ⇒ false
        assert not evaluate_condition(
            _cond("null_value", "NE", "x"), previous=_PREVIOUS
        )

    def test_ordered_numeric_and_string(self) -> None:
        assert evaluate_condition(
            _cond("total_qty", "LT", 20), previous=_PREVIOUS
        )
        assert not evaluate_condition(
            _cond("total_qty", "GT", 20), previous=_PREVIOUS
        )
        assert evaluate_condition(
            _cond("status", "LT", "LOWX"), previous=_PREVIOUS
        )

    def test_ordered_rejects_bool_and_mixed_types(self) -> None:
        assert not evaluate_condition(
            _cond("truthy", "GT", 0), previous=_PREVIOUS
        )
        assert not evaluate_condition(
            _cond("total_qty", "LT", "20"), previous=_PREVIOUS
        )

    def test_exists(self) -> None:
        assert evaluate_condition(
            _cond("nested.deep.value", "EXISTS"), previous=_PREVIOUS
        )
        # 0 是已存在的值（非 null）
        assert evaluate_condition(
            _cond("zero", "EXISTS"), previous=_PREVIOUS
        )
        assert not evaluate_condition(
            _cond("null_value", "EXISTS"), previous=_PREVIOUS
        )
        assert not evaluate_condition(
            _cond("missing", "EXISTS"), previous=_PREVIOUS
        )

    def test_contains_string(self) -> None:
        assert evaluate_condition(
            _cond("status", "CONTAINS", "LO"), previous=_PREVIOUS
        )
        assert not evaluate_condition(
            _cond("status", "CONTAINS", "X"), previous=_PREVIOUS
        )
        # expected 非 str ⇒ false
        assert not evaluate_condition(
            _cond("status", "CONTAINS", 5), previous=_PREVIOUS
        )
        # 空串子串恒真（合法语义）
        assert evaluate_condition(
            _cond("status", "CONTAINS", ""), previous=_PREVIOUS
        )

    def test_contains_list(self) -> None:
        assert evaluate_condition(
            _cond("items", "CONTAINS", 2), previous=_PREVIOUS
        )
        assert not evaluate_condition(
            _cond("items", "CONTAINS", 4), previous=_PREVIOUS
        )
        assert evaluate_condition(
            _cond("names", "CONTAINS", "alpha"), previous=_PREVIOUS
        )
        # bool 与数值隔离：列表成员 1 ≢ True
        assert not evaluate_condition(
            _cond("items", "CONTAINS", True), previous=_PREVIOUS
        )

    def test_contains_other_types_are_false(self) -> None:
        assert not evaluate_condition(
            _cond("nested", "CONTAINS", "x"), previous=_PREVIOUS
        )

    def test_missing_path_is_false_for_all_operators(self) -> None:
        for operator, expected in (
            ("EQ", 1),
            ("NE", 1),
            ("LT", 1),
            ("CONTAINS", "x"),
            ("EXISTS", None),
        ):
            assert not evaluate_condition(
                _cond("missing", operator, expected), previous=_PREVIOUS
            ), operator

    def test_none_previous_is_false(self) -> None:
        assert not evaluate_condition(
            _cond("total_qty", "EQ", 12), previous=None
        )

    def test_unknown_operator_fails(self) -> None:
        condition = _cond("total_qty", "EQ", 12)
        object.__setattr__(condition, "operator", "REGEX")
        with pytest.raises(BindingError):
            evaluate_condition(condition, previous=_PREVIOUS)

    def test_non_condition_input_fails(self) -> None:
        with pytest.raises(BindingError):
            evaluate_condition("total_qty", previous=_PREVIOUS)  # type: ignore[arg-type]

    @pytest.mark.parametrize("operator", sorted(CONDITION_OPERATORS))
    def test_whitelist_operators_all_evaluate_without_error(
        self, operator: str
    ) -> None:
        expected = None if operator == "EXISTS" else 12
        outcome = evaluate_condition(
            _cond("total_qty", operator, expected), previous=_PREVIOUS
        )
        assert isinstance(outcome, bool)


# ============================================================
# Test 5：binding import 边界（AST）
# ============================================================


class TestBindingImportBoundary:
    _STDLIB_ALLOWED = frozenset(
        {"__future__", "collections", "json", "operator", "re", "typing"}
    )

    def _modules(self) -> set[str]:
        tree = ast.parse(_BINDING_PATH.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    def test_imports_are_stdlib_or_domain_only(self) -> None:
        modules = self._modules()
        assert modules, "binding.py import 收集为空（环境异常）"
        for module in modules:
            if module.split(".")[0] in self._STDLIB_ALLOWED:
                continue
            assert (
                module == "backend.app.workflow"
                or module.startswith("backend.app.workflow.")
            ), f"binding.py 出现边界外 import: {module!r}"

    def test_no_same_package_or_forbidden_imports(self) -> None:
        modules = self._modules()
        for forbidden in (
            "backend.app.workflow_runtime",  # 同包零耦合（binding 不 import ports 等）
            "backend.app.db",
            "backend.app.services",
            "backend.app.tools",
            "backend.app.agent",
            "backend.app.api",
            "backend.app.llm",
            "backend.app.projects",
            "sqlalchemy",
            "fastapi",
            "httpx",
        ):
            assert not any(
                m == forbidden or m.startswith(forbidden + ".")
                for m in modules
            ), f"binding.py 禁入 import: {forbidden!r}"
