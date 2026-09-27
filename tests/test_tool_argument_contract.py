"""Tool Argument Contract 测试（Phase 3.11 Step 4）。

多参数契约：``material_code``（必填）+ ``warehouse_code``（可选）。

    Question
        ↓
    ToolArgumentExtractor.extract（确定性提取；非 NLP / 非 LLM）
        （Phase 3.11 Step 6：原 _extract_tool_arguments_from_question）
        ↓
    arguments
        ↓
    ToolRegistry.validate_arguments（唯一 Schema 权威）
        ↓
    Handler

覆盖：

    1.  单参数兼容（Phase 3.7.12 行为不变）
    2.  双参数提取（多种仓库表达：仓库 A01 / A01 仓库 /
        warehouse A01 / warehouse_code=A01）
    3.  参数顺序变化
    4.  缺失 material_code → ToolRegistry Schema 拒绝（不假装成功，
        Handler 0 次调用）
    5.  缺失 warehouse_code → 可选 → Schema 通过
    6.  Unknown field（batch）→ 提取器不生成；Registry 拒绝未知字段
    7.  Schema 权威：definition 声明两参数，required 仅 material_code
    8.  ToolExecutionService：多参数 arguments 原样透传
    9.  Orchestrator E2E（真实 Router → decision.tool_name →
        提取 → 执行边界 → Registry → Handler，0 DB）
    10. 误提取防护（A01 不被当 material；无仓库上下文不提取 warehouse）
    11. 真实 Handler 的 warehouse guard（经 Registry 归一为
        ToolResult(success=False)，显式拒绝而非静默忽略）

0 DB / 0 Network（真实 PostgreSQL 双参数查询按 Step 4 约束不做：
当前库存表无 warehouse 维度）。
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorService,
)
from backend.app.services.ai_router_service import (
    AIRouterService,
    ToolRegistryCapabilityAdapter,
)
from backend.app.services.tool_argument_extractor import (
    ToolArgumentExtractor,
    _match_warehouse_code,
)
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.get_inventory import (
    GET_INVENTORY_DEFINITION,
    GetInventoryHandler,
)
from backend.app.tools.registry import ToolRegistry

_EXTRACTOR = ToolArgumentExtractor()


def _extract(question: str) -> dict[str, Any]:
    """按真实 get_inventory definition 提取参数。

    Phase 3.11 Step 6：提取实现已从 Orchestrator 迁至
    ``ToolArgumentExtractor``（本测试文件仅换 import，断言全部不变）。
    """
    return _EXTRACTOR.extract(
        "get_inventory",
        question,
        parameters=GET_INVENTORY_DEFINITION.parameters,
    )


class _RecordingEchoHandler:
    """记录 arguments 并回显（0 DB；验证到达 Handler 的精确入参）。"""

    def __init__(self) -> None:
        self.calls = 0
        self.last_arguments: dict[str, Any] | None = None

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        self.last_arguments = dict(arguments)
        return {
            "material_code": arguments.get("material_code"),
            "warehouse_code": arguments.get("warehouse_code"),
            "qty": 5.0,
        }


def _registry_with(handler: Any) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(GET_INVENTORY_DEFINITION, handler)
    return registry


# ============================================================
# 1. 单参数兼容
# ============================================================

class TestSingleParameterCompatibility:
    @pytest.mark.parametrize("question,expected", [
        ("查询 MAT-001 当前库存", {"material_code": "MAT-001"}),
        ("查询物料 10001 当前库存", {"material_code": "10001"}),
        ("查库存 10001", {"material_code": "10001"}),
    ])
    def test_material_only(self, question: str, expected: dict) -> None:
        """无仓库上下文 → 只提取 material_code（原行为不变）。"""
        assert _extract(question) == expected

    def test_no_warehouse_without_context(self) -> None:
        assert "warehouse_code" not in (_extract("查询 MAT-001 当前库存") or {})


# ============================================================
# 2 / 3. 双参数提取（含顺序变化 / 表达形式）
# ============================================================

class TestDualParameterExtraction:
    @pytest.mark.parametrize("question,expected", [
        # 仓库 A01 前置
        ("查询 A01 仓库 MAT-001 的库存",
         {"warehouse_code": "A01", "material_code": "MAT-001"}),
        # 仓库后置
        ("查询 MAT-001 在 A01 仓库的库存",
         {"warehouse_code": "A01", "material_code": "MAT-001"}),
        # 仓库短语 + 中缀
        ("查询仓库 A01 中物料 MAT-001 的库存",
         {"warehouse_code": "A01", "material_code": "MAT-001"}),
        # 无空格连写
        ("查询仓库A01物料MAT-001库存",
         {"warehouse_code": "A01", "material_code": "MAT-001"}),
        # 英文形式
        ("warehouse_code=A01 中物料 10001 库存",
         {"warehouse_code": "A01", "material_code": "10001"}),
        ("warehouse A01 物料 10001 库存",
         {"warehouse_code": "A01", "material_code": "10001"}),
        # 仓库: 形式
        ("查询仓库: A01 的物料 MAT-001 库存",
         {"warehouse_code": "A01", "material_code": "MAT-001"}),
    ])
    def test_extracts_both_parameters(
        self, question: str, expected: dict,
    ) -> None:
        assert _extract(question) == expected

    def test_match_warehouse_code_returns_span(self) -> None:
        """仓库匹配带字符区间（供 material 排除）。"""
        match = _match_warehouse_code("查询 A01 仓库 MAT-001 的库存")
        assert match is not None
        code, span = match
        assert code == "A01"
        start, end = span
        assert "A01" in "查询 A01 仓库 MAT-001 的库存"[start:end]
        assert "MAT-001" not in "查询 A01 仓库 MAT-001 的库存"[start:end]


# ============================================================
# 4. 缺失 material_code → Registry 拒绝（不假装成功）
# ============================================================

class TestMissingMaterialCode:
    async def test_missing_material_rejected_by_registry(self) -> None:
        """提取结果只有 warehouse_code → Schema Validation 拒绝；
        Handler 0 次调用（校验权威仍在 ToolRegistry）。"""
        handler = _RecordingEchoHandler()
        registry = _registry_with(handler)

        arguments = _extract("查询仓库 A01 的库存")
        assert arguments == {"warehouse_code": "A01"}
        assert "material_code" not in arguments

        result = await registry.execute(
            "get_inventory", arguments=arguments
        )

        assert result.success is False
        assert result.data is None
        assert result.error is not None
        assert "参数校验失败" in result.error
        assert handler.calls == 0  # Schema 校验先于 Handler


# ============================================================
# 5. 缺失 warehouse_code → 可选 → Schema 通过
# ============================================================

class TestMissingWarehouseCode:
    async def test_warehouse_optional_schema_passes(self) -> None:
        handler = _RecordingEchoHandler()
        registry = _registry_with(handler)
        arguments = _extract("查询 MAT-001 当前库存")

        result = await registry.execute(
            "get_inventory", arguments=arguments
        )

        assert result.success is True
        assert handler.calls == 1
        assert handler.last_arguments == {"material_code": "MAT-001"}


# ============================================================
# 6. Unknown field（batch）不入参 / Registry 拒绝
# ============================================================

class TestUnknownField:
    def test_batch_not_generated_by_extractor(self) -> None:
        """提取器只生成 Schema 声明字段，不偷偷扩展 Tool Schema。"""
        arguments = _extract(
            "查询 MAT-001 在 A01 仓库的库存，并按批次查询"
        )
        assert arguments == {
            "warehouse_code": "A01", "material_code": "MAT-001",
        }
        assert "batch" not in arguments

    async def test_registry_rejects_unknown_field_if_passed(self) -> None:
        """即使上游硬塞未知字段，Registry 仍是拒绝者（权威不变）。"""
        handler = _RecordingEchoHandler()
        registry = _registry_with(handler)

        result = await registry.execute(
            "get_inventory",
            arguments={
                "material_code": "MAT-001",
                "warehouse_code": "A01",
                "batch": "B-001",
            },
        )

        assert result.success is False
        assert "unknown field" in (result.error or "") or (
            "参数校验失败" in (result.error or "")
        )
        assert handler.calls == 0


# ============================================================
# 7. Schema 权威（definition 声明）
# ============================================================

class TestSchemaAuthority:
    def test_definition_declares_two_parameters(self) -> None:
        params = GET_INVENTORY_DEFINITION.parameters
        props = params["properties"]
        assert set(props) == {"material_code", "warehouse_code"}
        assert props["material_code"]["type"] == "string"
        assert props["warehouse_code"]["type"] == "string"
        # warehouse_code 可选：required 仍只有 material_code
        assert params["required"] == ["material_code"]

    def test_definition_has_no_sql_injection_surface(self) -> None:
        props = GET_INVENTORY_DEFINITION.parameters["properties"]
        forbidden = {"sql", "query", "table", "where_clause", "order_by"}
        assert not (forbidden & set(props.keys()))


# ============================================================
# 8. ToolExecutionService：多参数原样透传
# ============================================================

class TestExecutionServicePassthrough:
    async def test_dual_arguments_passed_verbatim(self) -> None:
        handler = _RecordingEchoHandler()
        registry = _registry_with(handler)
        service = ToolExecutionService(registry=registry)

        arguments = _extract("查询 A01 仓库 MAT-001 的库存")
        result = await service.execute("get_inventory", arguments=arguments)

        assert result.success is True
        assert handler.last_arguments == {
            "warehouse_code": "A01", "material_code": "MAT-001",
        }


# ============================================================
# 9. Orchestrator E2E（真实 Router，0 DB）
# ============================================================

class TestOrchestratorE2E:
    async def test_dual_parameter_question_end_to_end(self) -> None:
        """Question → Router（tool_match）→ decision.tool_name →
        提取（双参数）→ 执行边界 → Registry → Handler。"""
        handler = _RecordingEchoHandler()
        registry = _registry_with(handler)
        router = AIRouterService(
            llm_fallback_enabled=False,
            tool_capabilities=ToolRegistryCapabilityAdapter(registry),
        )
        orch = AIOrchestratorService(router=router, tool_registry=registry)

        result = await orch.execute(
            "查询仓库 A01 中物料 MAT-001 当前库存"
        )

        assert result.route.value == "tool"
        assert result.metadata["tool_name"] == "get_inventory"
        assert result.metadata["tool_success"] is True
        assert handler.last_arguments == {
            "warehouse_code": "A01", "material_code": "MAT-001",
        }

    async def test_single_parameter_question_end_to_end(self) -> None:
        """单参数回归：无仓库上下文 → 原行为。"""
        handler = _RecordingEchoHandler()
        registry = _registry_with(handler)
        router = AIRouterService(
            llm_fallback_enabled=False,
            tool_capabilities=ToolRegistryCapabilityAdapter(registry),
        )
        orch = AIOrchestratorService(router=router, tool_registry=registry)

        result = await orch.execute("查询物料 10001 当前库存")

        assert result.route.value == "tool"
        assert result.metadata["tool_success"] is True
        assert handler.last_arguments == {"material_code": "10001"}

    async def test_real_handler_rejects_warehouse_via_registry(self) -> None:
        """真实 Handler + warehouse_code → 经 Registry 归一为
        ToolResult(success=False)（显式拒绝；guard 在 DB 访问之前，0 DB）。"""
        registry = _registry_with(GetInventoryHandler())

        result = await registry.execute(
            "get_inventory",
            arguments={"material_code": "MAT-001", "warehouse_code": "A01"},
        )

        assert result.success is False
        assert result.data is None
        assert result.error is not None
        assert "warehouse" in result.error


# ============================================================
# 10. 误提取防护
# ============================================================

class TestFalseExtractionProtection:
    def test_warehouse_value_not_taken_as_material(self) -> None:
        """"A01 仓库" 的 A01 不能被误当 material_code。"""
        arguments = _extract("查询 A01 仓库 MAT-001 的库存")
        assert arguments is not None
        assert arguments["material_code"] == "MAT-001"

    def test_material_value_not_taken_as_warehouse_without_context(
        self,
    ) -> None:
        """无仓库上下文时 A01 是物料编码（保持既有的字面量语义）。"""
        arguments = _extract("查询 A01 当前库存")
        assert arguments == {"material_code": "A01"}

    def test_ku_cun_word_is_not_warehouse_context(self) -> None:
        """"库存 A01" 不构成仓库上下文（"库存" ≠ "仓库"）。"""
        arguments = _extract("查询物料 10001 当前库存 A01")
        assert arguments is not None
        assert "warehouse_code" not in arguments
        assert arguments["material_code"] == "10001"

    def test_warehouse_expression_before_code_wins_over_after(self) -> None:
        """"A01 仓库 MAT-001"：MAT-001 不得被当作仓库编码
        （模式顺序：[CODE] 仓库 优先于 仓库 [CODE]）。"""
        match = _match_warehouse_code("查询 A01 仓库 MAT-001 的库存")
        assert match is not None
        assert match[0] == "A01"
