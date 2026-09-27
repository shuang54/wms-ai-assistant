"""Tool Argument Extractor 测试（Phase 3.11 Step 6）。

验证「自然语言 → Tool 参数候选值」提取边界（从 Orchestrator 迁出后的
独立组件）：

    Question + tool_name（+ Tool 声明字段）
        ↓
    ToolArgumentExtractor.extract()          （纯确定性：字符串 / 正则 / 区间）
        ↓
    arguments（仅含命中的字段；缺失字段不补）
        ↓
    ToolExecutionService → ToolRegistry.validate_arguments（唯一 Schema 权威）

覆盖：

    1.  get_inventory 确定性表达（MAT-001 / A01 仓库 / 仓库 A01 /
        warehouse A01 / warehouse_code=A01 / 无空格连写）
    2.  get_work_order 确定性表达（工单 / 工单号 / work_order /
        work_order_no=；以 Phase 3.11 Step 5 实际支持为准）
    3.  声明字段门槛（ToolDefinition.parameters）与 Tool 名称规则表一致
    4.  Regression：仓库 / 工单表达区间内的字面量不得被误当 material_code
    5.  Missing：缺失字段**不**补齐（Schema 校验仍属 ToolRegistry）
    6.  Unknown Tool：不猜 Tool、不 fallback 到 get_inventory / get_work_order
    7.  API 契约：dict（永不为 None）/ 无状态可复用 / 非法输入 TypeError
    8.  Orchestrator 迁移完成：提取实现已 0 处残留在 Orchestrator
    9.  Security：AST 静态断言（无 DB / SQL / HTTP / LLM / 文件系统 / Registry）

0 DB / 0 Network / 0 LLM。
"""
from __future__ import annotations

import ast
import os
from typing import Any

import pytest

from backend.app.services.tool_argument_extractor import (
    _TOOL_FIELD_RULES,
    ToolArgumentExtractor,
    _match_warehouse_code,
    _match_work_order_no,
    _spans_overlap,
)
from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION
from backend.app.tools.get_work_order import GET_WORK_ORDER_DEFINITION

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_EXTRACTOR_MODULE = "backend/app/services/tool_argument_extractor.py"
_ORCHESTRATOR_MODULE = "backend/app/services/ai_orchestrator_service.py"

#: 单一实例（组件无状态；复用同一实例也用于验证可复用性）
_EXTRACTOR = ToolArgumentExtractor()

#: 真实 get_inventory 声明字段（生产路径：Orchestrator 传入 definition.parameters）
_INVENTORY_PARAMETERS = GET_INVENTORY_DEFINITION.parameters
#: 真实 get_work_order 声明字段
_WORK_ORDER_PARAMETERS = GET_WORK_ORDER_DEFINITION.parameters


def _extract(
    tool_name: str,
    question: str,
    *,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _EXTRACTOR.extract(tool_name, question, parameters=parameters)


# ============================================================
# 1. get_inventory（生产路径：parameters=真实 definition）
# ============================================================

_INVENTORY_CASES: list[tuple[str, dict[str, Any]]] = [
    # 单参数（Phase 3.7.12 行为不变）
    ("查询 MAT-001 当前库存", {"material_code": "MAT-001"}),
    ("查询物料 10001 当前库存", {"material_code": "10001"}),
    # 双参数：仓库后置 / 前置 / 中缀 / 无空格连写
    ("查询物料 MAT-001 在 A01 仓库的库存",
     {"warehouse_code": "A01", "material_code": "MAT-001"}),
    ("查询 A01 仓库 MAT-001 的库存",
     {"warehouse_code": "A01", "material_code": "MAT-001"}),
    ("查询仓库 A01 中物料 MAT-001 的库存",
     {"warehouse_code": "A01", "material_code": "MAT-001"}),
    ("查询仓库A01物料MAT-001库存",
     {"warehouse_code": "A01", "material_code": "MAT-001"}),
    ("查询仓库: A01 的物料 MAT-001 库存",
     {"warehouse_code": "A01", "material_code": "MAT-001"}),
    # 英文形式（大小写不敏感）
    ("warehouse A01 物料 10001 库存",
     {"warehouse_code": "A01", "material_code": "10001"}),
    ("warehouse_code=A01 中物料 10001 库存",
     {"warehouse_code": "A01", "material_code": "10001"}),
]


class TestInventoryExtraction:
    @pytest.mark.parametrize("question,expected", _INVENTORY_CASES)
    def test_with_declared_parameters(
        self, question: str, expected: dict[str, Any],
    ) -> None:
        """生产路径：Parameters 来自 ToolDefinition（声明门槛）。"""
        assert _extract(
            "get_inventory", question, parameters=_INVENTORY_PARAMETERS
        ) == expected

    @pytest.mark.parametrize("question,expected", _INVENTORY_CASES)
    def test_with_tool_name_rule_table(
        self, question: str, expected: dict[str, Any],
    ) -> None:
        """独立调用路径：parameters=None → Tool 名称规则表，
        结果必须与生产路径逐字一致（否则两条规则已经漂移）。"""
        assert _extract("get_inventory", question) == expected

    def test_no_warehouse_without_context(self) -> None:
        """无仓库上下文 → 不生成 warehouse_code（保持既有字面量语义）。"""
        assert _extract(
            "get_inventory",
            "查询 MAT-001 当前库存",
            parameters=_INVENTORY_PARAMETERS,
        ) == {"material_code": "MAT-001"}

    def test_material_only_definition_still_extracts(self) -> None:
        """Tool 只声明 material_code（既有测试场景 tool_b 同构）
        → 不生成 warehouse_code（声明门槛生效）。"""
        parameters = {
            "type": "object",
            "properties": {"material_code": {"type": "string"}},
            "required": ["material_code"],
        }
        assert _extract(
            "get_inventory",
            "查询物料 MAT-001 当前库存",
            parameters=parameters,
        ) == {"material_code": "MAT-001"}


# ============================================================
# 2. get_work_order（Phase 3.11 Step 5 实际支持表达）
# ============================================================

_WORK_ORDER_CASES: list[tuple[str, dict[str, Any]]] = [
    ("查询工单 WO-001", {"work_order_no": "WO-001"}),
    ("查询工单 WO-202609-001", {"work_order_no": "WO-202609-001"}),
    ("查询工单号 WO-001", {"work_order_no": "WO-001"}),
    ("工单: WO-001", {"work_order_no": "WO-001"}),
    ("工单号=WO-001", {"work_order_no": "WO-001"}),
    ("work_order WO-001", {"work_order_no": "WO-001"}),
    ("work_order_no=WO-001", {"work_order_no": "WO-001"}),
]


class TestWorkOrderExtraction:
    @pytest.mark.parametrize("question,expected", _WORK_ORDER_CASES)
    def test_with_declared_parameters(
        self, question: str, expected: dict[str, Any],
    ) -> None:
        assert _extract(
            "get_work_order", question, parameters=_WORK_ORDER_PARAMETERS
        ) == expected

    @pytest.mark.parametrize("question,expected", _WORK_ORDER_CASES)
    def test_with_tool_name_rule_table(
        self, question: str, expected: dict[str, Any],
    ) -> None:
        assert _extract("get_work_order", question) == expected

    def test_bare_work_order_no_is_not_extracted(self) -> None:
        """无「工单 / work_order」上下文的裸字面量**不**被提取：
        Step 6 不新增自然语言表达（保持 Step 5 行为）。
        Required 缺失由 ToolRegistry 拒绝，而不是在此处猜值。"""
        assert _extract(
            "get_work_order",
            "查询 WO-001",
            parameters=_WORK_ORDER_PARAMETERS,
        ) == {}

    def test_warehouse_not_extracted_when_not_declared(self) -> None:
        """get_work_order 未声明 warehouse_code → 即使问题含仓库表达
        也不生成该字段（声明门槛生效）。"""
        arguments = _extract(
            "get_work_order",
            "查询 A01 仓库的工单 WO-001",
            parameters=_WORK_ORDER_PARAMETERS,
        )
        assert arguments == {"work_order_no": "WO-001"}
        assert "warehouse_code" not in arguments


# ============================================================
# 3. 声明字段门槛 ↔ Tool 名称规则表 一致性
# ============================================================

class TestFieldRuleTable:
    def test_rule_table_covers_exactly_the_two_real_tools(self) -> None:
        """范围锁定：新增第三个 Tool 必须显式扩展（本测试会失败以提醒）。"""
        assert set(_TOOL_FIELD_RULES) == {"get_inventory", "get_work_order"}

    @pytest.mark.parametrize("definition", [
        GET_INVENTORY_DEFINITION,
        GET_WORK_ORDER_DEFINITION,
    ])
    def test_rule_table_matches_declared_fields(self, definition) -> None:
        """规则表必须与真实 Tool 声明字段一致（防漂移）。"""
        declared = set(definition.parameters.get("properties") or {})
        assert set(_TOOL_FIELD_RULES[definition.name]) == declared

    def test_parameters_without_properties_extracts_nothing(self) -> None:
        """declaration 无 properties（如空 Tool）→ {}（不生成任何字段）。"""
        for parameters in ({}, {"type": "object"}, {"properties": None}):
            assert _extract(
                "get_inventory", "查询物料 MAT-001 当前库存",
                parameters=parameters,
            ) == {}

    def test_unsupported_declared_field_is_ignored(self) -> None:
        """声明了本阶段不支持的字段 → 既不提取也不报错。"""
        parameters = {
            "type": "object",
            "properties": {"batch_no": {"type": "string"}},
        }
        assert _extract(
            "get_inventory", "查询物料 MAT-001 当前库存",
            parameters=parameters,
        ) == {}


# ============================================================
# 4. Regression：锚定区间的字面量排除（Phase 3.11 Step 4 修复）
# ============================================================

class TestAnchoredSpanExclusion:
    def test_warehouse_code_not_taken_as_material(self) -> None:
        """"A01 仓库" 的 A01 不能被误当 material_code。"""
        arguments = _extract(
            "get_inventory",
            "查询 A01 仓库 MAT-001 的库存",
            parameters=_INVENTORY_PARAMETERS,
        )
        assert arguments["material_code"] == "MAT-001"
        assert arguments["warehouse_code"] == "A01"

    def test_warehouse_word_itself_not_taken_as_material(self) -> None:
        """"warehouse A01 ... 10001"：英文模式词 / A01 都在区间内 → 排除。"""
        arguments = _extract(
            "get_inventory",
            "warehouse A01 物料 10001 库存",
            parameters=_INVENTORY_PARAMETERS,
        )
        assert arguments["material_code"] == "10001"

    def test_work_order_span_excluded_for_multi_field_tool(self) -> None:
        """同时声明 work_order_no + material_code 的 Tool：
        WO-001 落在工单区间内 → 不得被当成 material_code。"""
        parameters = {
            "type": "object",
            "properties": {
                "warehouse_code": {"type": "string"},
                "work_order_no": {"type": "string"},
                "material_code": {"type": "string"},
            },
        }
        assert _extract(
            "custom_tool",
            "查询工单 WO-001 的物料 MAT-001 库存",
            parameters=parameters,
        ) == {"work_order_no": "WO-001", "material_code": "MAT-001"}

    def test_match_warehouse_code_returns_span(self) -> None:
        """仓库匹配带字符区间（供 material 排除）。"""
        match = _match_warehouse_code("查询 A01 仓库 MAT-001 的库存")
        assert match is not None
        code, span = match
        assert code == "A01"
        start, end = span
        assert "A01" in "查询 A01 仓库 MAT-001 的库存"[start:end]
        assert "MAT-001" not in "查询 A01 仓库 MAT-001 的库存"[start:end]

    def test_match_work_order_no_returns_span(self) -> None:
        match = _match_work_order_no("查询工单 WO-001 的物料 MAT-001 库存")
        assert match is not None
        code, span = match
        assert code == "WO-001"
        text = "查询工单 WO-001 的物料 MAT-001 库存"
        assert "WO-001" in text[span[0]:span[1]]
        assert "MAT-001" not in text[span[0]:span[1]]

    def test_no_anchor_returns_none(self) -> None:
        assert _match_warehouse_code("查询 MAT-001 当前库存") is None
        assert _match_work_order_no("查询 MAT-001 当前库存") is None

    def test_spans_overlap_helper_semantics(self) -> None:
        assert _spans_overlap((0, 3), (2, 5)) is True
        assert _spans_overlap((0, 3), (3, 6)) is False  # 半开区间
        assert _spans_overlap((5, 8), (0, 3)) is False
        assert _spans_overlap((0, 10), (2, 4)) is True


# ============================================================
# 5. Missing：缺失字段不补齐（Schema 校验属 ToolRegistry）
# ============================================================

class TestMissingArguments:
    def test_missing_material_code_not_fabricated(self) -> None:
        """"查询 A01 仓库库存" → 只有 warehouse_code（**不**补 material_code）。"""
        arguments = _extract(
            "get_inventory",
            "查询 A01 仓库库存",
            parameters=_INVENTORY_PARAMETERS,
        )
        assert arguments == {"warehouse_code": "A01"}
        assert "material_code" not in arguments

    def test_missing_work_order_no_not_fabricated(self) -> None:
        """提到工单但没有单号 → {}（required 由 Registry 裁决）。"""
        assert _extract(
            "get_work_order",
            "查询工单",
            parameters=_WORK_ORDER_PARAMETERS,
        ) == {}

    def test_no_value_at_all(self) -> None:
        assert _extract(
            "get_inventory", "查库存", parameters=_INVENTORY_PARAMETERS
        ) == {}


# ============================================================
# 6. Unknown Tool：不猜 Tool / 不 fallback
# ============================================================

class TestUnknownTool:
    def test_unknown_tool_without_declaration_returns_empty(self) -> None:
        assert _extract(
            "unknown_tool", "查询物料 MAT-001 在 A01 仓库的库存"
        ) == {}

    def test_unknown_tool_does_not_fallback_to_real_tools(self) -> None:
        """未知 Tool → {}；绝不套用 get_inventory / get_work_order 规则。"""
        arguments = _extract("unknown_tool", "查询工单 WO-001")
        assert arguments == {}
        assert "work_order_no" not in arguments
        assert "material_code" not in arguments

    def test_unknown_tool_with_declared_field_uses_declaration_only(self) -> None:
        """未知 Tool 名 + 显式声明字段（自定义 Tool 场景，如同 tool_b）：
        只按声明字段提取，且**不**引入其它 Tool 的规则（无 warehouse_code）。"""
        parameters = {
            "type": "object",
            "properties": {"material_code": {"type": "string"}},
        }
        arguments = _extract(
            "unknown_tool",
            "查询物料 MAT-001 在 A01 仓库的库存",
            parameters=parameters,
        )
        assert arguments == {"material_code": "MAT-001"}
        # get_inventory 的规则未被套用（无 warehouse_code）
        assert "warehouse_code" not in arguments


# ============================================================
# 7. API 契约
# ============================================================

class TestApiContract:
    def test_always_returns_dict(self) -> None:
        for question in ("", "   ", "查库存", "查询物料 MAT-001 当前库存"):
            result = _EXTRACTOR.extract("get_inventory", question)
            assert isinstance(result, dict)

    def test_non_string_question_rejected(self) -> None:
        with pytest.raises(TypeError):
            _EXTRACTOR.extract("get_inventory", 123)  # type: ignore[arg-type]

    def test_invalid_tool_name_rejected(self) -> None:
        with pytest.raises(TypeError):
            _EXTRACTOR.extract("", "查询 MAT-001 当前库存")
        with pytest.raises(TypeError):
            _EXTRACTOR.extract(None, "查询 MAT-001 当前库存")  # type: ignore[arg-type]

    def test_keyword_call_form(self) -> None:
        """支持 spec 中的关键字调用形态（tool_name= / question=）。"""
        assert _EXTRACTOR.extract(
            tool_name="get_work_order", question="查询工单 WO-202609-001"
        ) == {"work_order_no": "WO-202609-001"}

    def test_stateless_and_reusable(self) -> None:
        question = "查询 A01 仓库 MAT-001 的库存"
        first = _EXTRACTOR.extract("get_inventory", question)
        first["material_code"] = "TAMPERED"  # 篡改返回值
        second = _EXTRACTOR.extract("get_inventory", question)
        assert second == {"warehouse_code": "A01", "material_code": "MAT-001"}

    def test_no_private_state_attributes(self) -> None:
        """无状态组件：不持有任何提取结果 / 依赖。"""
        assert vars(_EXTRACTOR) == {}


# ============================================================
# 8. Orchestrator 迁移完成（提取实现 0 处残留）
# ============================================================

class TestExtractionMovedOutOfOrchestrator:
    def _source(self, module: str) -> str:
        path = os.path.join(REPO_ROOT, *module.split("/"))
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_orchestrator_has_no_extraction_implementation(self) -> None:
        src = self._source(_ORCHESTRATOR_MODULE)
        for fragment in (
            "_extract_tool_arguments_from_question",
            "_match_warehouse_code",
            "_match_work_order_no",
            "_spans_overlap",
            "_TOOL_WAREHOUSE_PATTERNS",
            "_TOOL_WORK_ORDER_PATTERNS",
            "_TOOL_ARG_LITERAL_PATTERN",
            "re.compile",
            "import re",
        ):
            assert fragment not in src, fragment

    def test_orchestrator_depends_on_extractor(self) -> None:
        """依赖方向：AIOrchestrator → ToolArgumentExtractor（单向）。"""
        src = self._source(_ORCHESTRATOR_MODULE)
        assert (
            "from backend.app.services.tool_argument_extractor import "
            "ToolArgumentExtractor" in src
        )
        assert "_argument_extractor" in src

    def test_extractor_does_not_import_orchestrator(self) -> None:
        """反向依赖禁止（文本提及不算依赖，此处只检查 import 图）。"""
        with open(
            os.path.join(REPO_ROOT, *(_EXTRACTOR_MODULE.split("/"))),
            encoding="utf-8",
        ) as handle:
            tree = ast.parse(handle.read())

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        assert not any("orchestrator" in name for name in imported)


# ============================================================
# 9. Security：静态边界（AST）
# ============================================================

class TestStaticSecurity:
    """ToolArgumentExtractor 只允许标准库字符串 / 正则能力。"""

    def _module_ast(self) -> ast.Module:
        path = os.path.join(REPO_ROOT, *(_EXTRACTOR_MODULE.split("/")))
        with open(path, encoding="utf-8") as handle:
            return ast.parse(handle.read())

    def test_imports_are_stdlib_only(self) -> None:
        """仅允许 re / typing / collections.abc（+ __future__）；
        禁止任何项目内模块（因此无法触达 Registry / DB / LLM / API）。"""
        tree = self._module_ast()
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        allowed = {"__future__", "re", "typing", "collections.abc"}
        assert imported <= allowed, imported
        assert not any(name.startswith("backend") for name in imported)

    def test_no_db_network_llm_identifiers(self) -> None:
        tree = self._module_ast()
        identifiers: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                identifiers.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr.lower())

        for fragment in (
            "registry", "session", "engine", "connection", "socket",
            "subprocess", "requests", "httpx", "sqlalchemy", "llm",
            "router", "cursor", "database_url",
        ):
            assert not any(
                fragment in ident for ident in identifiers
            ), fragment

    def test_no_forbidden_calls(self) -> None:
        """无 open / eval / exec / compile / __import__ / subprocess 调用。"""
        tree = self._module_ast()
        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)

        forbidden = {"open", "eval", "exec", "compile", "__import__",
                     "input", "system", "popen"}
        assert not (called & forbidden), called & forbidden

    def test_no_sql_string_constants(self) -> None:
        tree = self._module_ast()
        strings: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.append(node.value)

        for fragment in ("select ", "insert ", "update ", "delete ",
                         "drop ", " from ", "where "):
            assert not any(
                fragment in value.lower() for value in strings
            ), fragment
