"""真实 ``get_work_order`` Tool 测试（Phase 3.11 Step 5）。

验证第二个真实只读 Tool 接入**统一执行边界**：

    Question
        ↓ AI Router（Tool Selection 唯一来源）
    RouteDecision(tool_name="get_work_order")
        ↓ AIOrchestrator（参数提取）
    ToolExecutionService.execute（统一执行边界；与 get_inventory 共用）
        ↓
    ToolRegistry.execute（Schema 校验 + Handler + ToolResult）
        ↓
    GetWorkOrderHandler（只读查询 / 项目上下文）

覆盖：

    1.  Definition（name / description / aliases / schema / 无注入面）
    2.  参数校验（正常 / 缺失 / unknown field / invalid type /
        SQL injection）—— 校验权威在 ToolRegistry
    3.  Handler（Fake Engine：0 DB）
        - SQL 必须使用绑定参数（:work_order_no），不拼接
        - READ ONLY 事务 + statement_timeout + rollback
        - status 返回 / 不存在 → None（不伪造）
        - project_id 回显（project A / B 轻量隔离）
        - engine 未配置 → ToolExecutionError
    4.  Registry（schema validation / handler invocation / ToolResult）
    5.  ToolExecutionService：get_inventory 与 get_work_order 共用同一
        执行边界实例（不改 Service，只通过测试证明复用）
    6.  Orchestrator E2E：真实 Router → TOOL → 对应 Handler；
        RAG = 0 / Text-to-SQL = 0 / 不误调另一个 Tool
    7.  Multi-Tool Selection Regression（库存 ↔ 工单互不误选）
    8.  Tool-to-Tool 隔离（Handler 无 Registry / ExecutionService /
        Orchestrator 依赖）
    9.  API 默认装配 Router 能发现 get_work_order

0 DB / 0 Network（database-level 集成 = NOT SUPPORTED：Step 5 勘察
确认真实 DB 无 work_order 业务表；按约束不修改数据库结构）。
"""
from __future__ import annotations

import ast
import contextlib
import inspect
import os
from typing import Any

import pytest

from backend.app.services.ai_orchestrator_service import AIOrchestratorService
from backend.app.services.ai_router_service import (
    AIRouterService,
    RouteType,
    ToolRegistryCapabilityAdapter,
)
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.errors import ToolValidationError
from backend.app.tools.get_inventory import (
    GET_INVENTORY_DEFINITION,
    GetInventoryHandler,
    build_default_tool_registry,
)
from backend.app.tools.get_work_order import (
    GET_WORK_ORDER_DEFINITION,
    GetWorkOrderHandler,
    GetWorkOrderProjectContextProvider,
    _assert_safe_identifier,
    _validate_work_order_no,
    register_get_work_order_tool,
)
from backend.app.tools.registry import ToolRegistry

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_WORK_ORDER_MODULE = "backend/app/tools/get_work_order.py"

#: 复用既有测试资产（test_ai_orchestrator 的 Fakes），不重复创建。
from tests.test_ai_orchestrator import (  # noqa: E402
    FakeRAG,
    FakeTextToSQL,
    _make_service,
)


# ============================================================
# Fake Engine（0 DB；验证真实 SQL 安全形态）
# ============================================================

class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _FakeConn:
    """记录 execute 调用（SQL 文本 + 绑定参数）与 rollback。"""

    def __init__(self, scalar_value: Any) -> None:
        self.executed: list[tuple[str, dict[str, Any] | None]] = []
        self.rolled_back = False
        self._scalar_value = scalar_value

    def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        self.executed.append((str(stmt), params))
        return _FakeResult(self._scalar_value)

    def rollback(self) -> None:
        self.rolled_back = True


class _FakeEngine:
    """SQLAlchemy Engine 最小替身（connect() 支持 with 协议）。"""

    def __init__(self, scalar_value: Any = None) -> None:
        self.conn = _FakeConn(scalar_value)

    def connect(self) -> Any:
        return contextlib.nullcontext(self.conn)


class _RecordingHandler:
    """记录 arguments 的 Handler（供 Registry / E2E 路由断言）。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.calls = 0
        self.last_arguments: dict[str, Any] | None = None
        self._payload = payload

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        self.last_arguments = dict(arguments)
        return dict(self._payload)


def _dual_registry(
    inventory_handler: Any, work_order_handler: Any
) -> ToolRegistry:
    """真实双 Tool definition + 自定义 handler（0 DB）。"""
    registry = ToolRegistry()
    registry.register(GET_INVENTORY_DEFINITION, inventory_handler)
    registry.register(GET_WORK_ORDER_DEFINITION, work_order_handler)
    return registry


# ============================================================
# 1. Definition
# ============================================================

class TestDefinition:
    def test_definition_name(self) -> None:
        assert GET_WORK_ORDER_DEFINITION.name == "get_work_order"

    def test_definition_description(self) -> None:
        desc = GET_WORK_ORDER_DEFINITION.description
        assert desc.strip()
        assert "工单" in desc
        assert "只读" in desc

    def test_definition_aliases_avoid_bare_subject_word(self) -> None:
        """别名不能用裸词 "工单"（避免抢占"统计本月工单数量"类聚合问题）。"""
        aliases = GET_WORK_ORDER_DEFINITION.aliases
        assert aliases
        assert "工单" not in aliases

    def test_definition_requires_work_order_no(self) -> None:
        params = GET_WORK_ORDER_DEFINITION.parameters
        assert params["required"] == ["work_order_no"]
        assert params["properties"]["work_order_no"]["type"] == "string"

    def test_definition_does_not_expose_unsafe_params(self) -> None:
        props = GET_WORK_ORDER_DEFINITION.parameters["properties"]
        forbidden = {
            "sql", "query", "raw_sql", "table", "where_clause", "order_by",
        }
        assert not (forbidden & set(props.keys()))


# ============================================================
# 2. 参数校验（含 SQL injection / unknown / invalid type）
# ============================================================

class TestArgumentValidation:
    def test_validate_work_order_no_strips(self) -> None:
        assert _validate_work_order_no("  WO-001  ", max_len=64) == "WO-001"

    @pytest.mark.parametrize("value", [
        "", "   ", 123, 1.5, None,
        "WO-001' OR '1'='1", "WO; DELETE FROM x", "a" * 65, "-WO001",
        "<script>",
    ])
    def test_validate_work_order_no_rejects(self, value: Any) -> None:
        with pytest.raises(ValueError):
            _validate_work_order_no(value, max_len=64)

    async def test_registry_rejects_missing_required(self) -> None:
        handler = _RecordingHandler({"status": "RELEASED"})
        registry = _dual_registry(_RecordingHandler({}), handler)

        result = await registry.execute("get_work_order", arguments={})

        assert result.success is False
        assert "参数校验失败" in (result.error or "")
        assert handler.calls == 0

    async def test_registry_rejects_unknown_field(self) -> None:
        """{"work_order_no": ..., "password": ...} → 未知字段拒绝。"""
        handler = _RecordingHandler({"status": "RELEASED"})
        registry = _dual_registry(_RecordingHandler({}), handler)

        result = await registry.execute(
            "get_work_order",
            arguments={"work_order_no": "WO-001", "password": "xxx"},
        )

        assert result.success is False
        assert handler.calls == 0

    async def test_registry_rejects_invalid_type(self) -> None:
        """{"work_order_no": 123} → 类型拒绝（Schema 权威在 Registry）。"""
        handler = _RecordingHandler({"status": "RELEASED"})
        registry = _dual_registry(_RecordingHandler({}), handler)

        result = await registry.execute(
            "get_work_order", arguments={"work_order_no": 123}
        )

        assert result.success is False
        assert handler.calls == 0

    async def test_registry_rejects_sql_injection_string(self) -> None:
        """注入串（类型合法、字符集非法）→ 真实 Handler 拒绝
        （Schema 只做类型；字符集属 Handler 第二层校验）→
        ToolResult(success=False)，且**未触达 SQL**。"""
        engine = _FakeEngine(scalar_value="RELEASED")
        registry = _dual_registry(
            _RecordingHandler({}),
            GetWorkOrderHandler(engine=engine),
        )

        result = await registry.execute(
            "get_work_order",
            arguments={"work_order_no": "WO-001' OR '1'='1"},
        )

        assert result.success is False
        assert result.error is not None
        assert engine.conn.executed == []  # 校验失败先于任何 SQL

    async def test_handler_rejects_injection_before_sql(self) -> None:
        """真实 Handler：注入串 → ToolValidationError（不触 DB）。"""
        handler = GetWorkOrderHandler()  # 无 engine；校验先于查询
        with pytest.raises(ToolValidationError) as ei:
            await handler({"work_order_no": "WO-001' OR '1'='1"})
        assert ei.value.field_path == "work_order_no"


# ============================================================
# 3. Handler（Fake Engine；0 DB）
# ============================================================

class TestHandlerWithFakeEngine:
    async def test_query_uses_bound_parameters_and_readonly(self) -> None:
        """SQL 必须使用 :work_order_no 绑定参数 + READ ONLY +
        statement_timeout + rollback；不拼接输入值。"""
        engine = _FakeEngine(scalar_value="RELEASED")
        handler = GetWorkOrderHandler(
            engine=engine,
            project_id="project-a",
        )

        data = await handler({"work_order_no": "WO-001"})

        assert data["work_order_no"] == "WO-001"
        assert data["status"] == "RELEASED"
        assert data["project_id"] == "project-a"

        executed = engine.conn.executed
        statements = [sql for sql, _ in executed]
        # 1) READ ONLY 事务 + 超时（顺序在前）
        assert any("SET TRANSACTION READ ONLY" in s for s in statements)
        assert any("SET LOCAL statement_timeout" in s for s in statements)
        # 2) 查询使用绑定参数，SQL 文本内**不**出现输入值
        select_calls = [
            (sql, params) for sql, params in executed
            if "SELECT" in sql.upper()
        ]
        assert len(select_calls) == 1
        sql, params = select_calls[0]
        assert ":work_order_no" in sql
        assert "WO-001" not in sql  # 值不进入 SQL 文本
        assert params == {"work_order_no": "WO-001"}
        # 3) rollback 归还连接
        assert engine.conn.rolled_back is True

    async def test_missing_work_order_returns_none_status(self) -> None:
        """工单不存在 → status=None（业务语义；不伪造状态）。"""
        engine = _FakeEngine(scalar_value=None)
        handler = GetWorkOrderHandler(engine=engine)

        data = await handler({"work_order_no": "WO-NOT-EXIST"})

        assert data["work_order_no"] == "WO-NOT-EXIST"
        assert data["status"] is None

    async def test_project_isolation_via_injected_override(self) -> None:
        """project A / B 轻量隔离：override 原样回显（不硬编码项目）。"""
        engine = _FakeEngine(scalar_value="RELEASED")
        handler_a = GetWorkOrderHandler(engine=engine, project_id="project-a")
        handler_b = GetWorkOrderHandler(engine=engine, project_id="project-b")

        data_a = await handler_a({"work_order_no": "WO-1"})
        data_b = await handler_b({"work_order_no": "WO-1"})

        assert data_a["project_id"] == "project-a"
        assert data_b["project_id"] == "project-b"

    async def test_project_isolation_via_provider(self) -> None:
        """无 override 时由 ProjectContextProvider 解析（Fake provider）。"""
        from backend.app.projects.context import (
            DEFAULT_DATA_SOURCE_NAME,
            DEFAULT_DATA_SOURCE_TYPE,
            DataSource,
            ProjectContext,
        )

        class _FakeProvider:
            def resolve(self) -> ProjectContext:
                return ProjectContext(
                    project_id="project-b",
                    project_name="project-b",
                    description=None,
                    data_source=DataSource(
                        name=DEFAULT_DATA_SOURCE_NAME,
                        type=DEFAULT_DATA_SOURCE_TYPE,
                    ),
                )

        engine = _FakeEngine(scalar_value="RELEASED")
        handler = GetWorkOrderHandler(
            engine=engine,
            project_context_provider=_FakeProvider(),  # type: ignore[arg-type]
        )

        data = await handler({"work_order_no": "WO-1"})

        assert data["project_id"] == "project-b"

    async def test_local_provider_resolves_default_project(self) -> None:
        provider = GetWorkOrderProjectContextProvider()
        ctx = provider.resolve()
        assert ctx.project_id  # 来自 settings.project.project_id

    async def test_engine_unconfigured_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """get_engine() 为 None（DATABASE_URL 未配置）→ ToolExecutionError。"""
        from backend.app.tools import get_work_order as wo_module

        monkeypatch.setattr(wo_module, "get_engine", lambda: None)
        handler = GetWorkOrderHandler()

        from backend.app.tools.errors import ToolExecutionError

        with pytest.raises(ToolExecutionError):
            await handler({"work_order_no": "WO-001"})

    def test_unsafe_identifier_rejected(self) -> None:
        from backend.app.tools.errors import ToolError

        with pytest.raises(ToolError):
            _assert_safe_identifier('evil"; DROP TABLE x;--', field_name="x")
        with pytest.raises(ToolError):
            _assert_safe_identifier("", field_name="x")

    async def test_run_query_rejects_unsafe_identifier_via_config(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """配置注入 schema → ToolExecutionError（标识符白名单防注入）。"""
        from backend.app.config import WorkOrderToolSettings
        from backend.app.tools.errors import ToolError

        engine = _FakeEngine(scalar_value="X")
        handler = GetWorkOrderHandler(
            engine=engine,
            wo_settings=WorkOrderToolSettings(
                schema_name="public; DROP TABLE x;--",
                table_name="work_order",
            ),
        )
        with pytest.raises(ToolError):
            await handler({"work_order_no": "WO-001"})


# ============================================================
# 4. Registry（注册 / ToolResult 契约）
# ============================================================

class TestRegistry:
    def test_register_and_count(self) -> None:
        registry = ToolRegistry()
        assert len(registry) == 0
        register_get_work_order_tool(registry)
        assert len(registry) == 1
        assert "get_work_order" in registry

    def test_register_twice_raises(self) -> None:
        from backend.app.tools.errors import ToolAlreadyRegisteredError

        registry = ToolRegistry()
        register_get_work_order_tool(registry)
        with pytest.raises(ToolAlreadyRegisteredError):
            register_get_work_order_tool(registry)

    def test_list_definitions_has_no_handler(self) -> None:
        registry = ToolRegistry()
        register_get_work_order_tool(registry)
        for d in registry.list_definitions():
            assert not hasattr(d, "_handler")

    async def test_execute_returns_unified_tool_result(self) -> None:
        """统一 ToolResult 契约（不新建 WorkOrderResult）。"""
        engine = _FakeEngine(scalar_value="RELEASED")
        registry = _dual_registry(
            _RecordingHandler({}),
            GetWorkOrderHandler(engine=engine, project_id="project-a"),
        )

        result = await registry.execute(
            "get_work_order", arguments={"work_order_no": "WO-001"}
        )

        assert result.tool_name == "get_work_order"
        assert result.success is True
        assert result.error is None
        assert result.data["work_order_no"] == "WO-001"
        assert result.data["status"] == "RELEASED"

    async def test_build_default_registry_contains_two_real_tools(self) -> None:
        registry = build_default_tool_registry()
        names = [d.name for d in registry.list_definitions()]
        assert names == ["get_inventory", "get_work_order"]


# ============================================================
# 5. ToolExecutionService：两个 Tool 共用同一执行边界
# ============================================================

class TestToolExecutionServiceShared:
    async def test_both_tools_share_same_execution_boundary(self) -> None:
        """同一个 ToolExecutionService 实例分别执行两个 Tool ——
        完全相同的执行路径（Service 零修改）。"""
        inv_handler = _RecordingHandler({"qty": 5.0})
        wo_handler = _RecordingHandler({"status": "RELEASED"})
        registry = _dual_registry(inv_handler, wo_handler)
        service = ToolExecutionService(registry=registry)

        inv_result = await service.execute(
            "get_inventory", arguments={"material_code": "MAT-001"}
        )
        wo_result = await service.execute(
            "get_work_order", arguments={"work_order_no": "WO-001"}
        )

        assert inv_result.success is True
        assert wo_result.success is True
        assert inv_handler.calls == 1
        assert wo_handler.calls == 1
        assert inv_handler.last_arguments == {"material_code": "MAT-001"}
        assert wo_handler.last_arguments == {"work_order_no": "WO-001"}
        assert service.registry is registry

    async def test_capability_denied_applies_to_second_tool(self) -> None:
        """能力白名单对第二个 Tool 同样生效（Handler 0 次调用）。"""
        from backend.app.projects.capabilities import ProjectCapabilities
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorCapabilityError,
        )

        wo_handler = _RecordingHandler({"status": "RELEASED"})
        registry = _dual_registry(_RecordingHandler({}), wo_handler)
        service = ToolExecutionService(
            registry=registry,
            capabilities=ProjectCapabilities(tool_names=("get_inventory",)),
            project_id="proj-a",
        )

        with pytest.raises(AIOrchestratorCapabilityError) as ei:
            await service.execute(
                "get_work_order", arguments={"work_order_no": "WO-001"}
            )

        assert ei.value.capability == "get_work_order"
        assert wo_handler.calls == 0


# ============================================================
# 6. Orchestrator E2E（真实 Router + 双 Tool；0 DB）
# ============================================================

class TestOrchestratorE2E:
    async def test_work_order_question_routes_to_work_order_only(self) -> None:
        inv_handler = _RecordingHandler({"qty": 5.0})
        wo_handler = _RecordingHandler({"status": "RELEASED"})
        registry = _dual_registry(inv_handler, wo_handler)
        rag = FakeRAG()
        t2s = FakeTextToSQL()
        router = AIRouterService(
            llm_fallback_enabled=False,
            tool_capabilities=ToolRegistryCapabilityAdapter(registry),
        )
        orch = _make_service(router=router, tools=registry, rag=rag, t2s=t2s)

        result = await orch.execute("查询工单 WO-202609-001")

        assert result.route == RouteType.TOOL
        assert result.metadata["tool_name"] == "get_work_order"
        assert result.metadata["tool_success"] is True
        assert wo_handler.calls == 1
        assert wo_handler.last_arguments == {
            "work_order_no": "WO-202609-001"
        }
        assert inv_handler.calls == 0
        assert rag.calls == []
        assert t2s.calls == []

    async def test_inventory_question_routes_to_inventory_only(self) -> None:
        inv_handler = _RecordingHandler({"qty": 5.0})
        wo_handler = _RecordingHandler({"status": "RELEASED"})
        registry = _dual_registry(inv_handler, wo_handler)
        rag = FakeRAG()
        t2s = FakeTextToSQL()
        router = AIRouterService(
            llm_fallback_enabled=False,
            tool_capabilities=ToolRegistryCapabilityAdapter(registry),
        )
        orch = _make_service(router=router, tools=registry, rag=rag, t2s=t2s)

        result = await orch.execute("查询物料 10001 当前库存")

        assert result.route == RouteType.TOOL
        assert result.metadata["tool_name"] == "get_inventory"
        assert inv_handler.calls == 1
        assert wo_handler.calls == 0
        assert rag.calls == []
        assert t2s.calls == []

    async def test_two_question_two_tools_no_cross_call(self) -> None:
        """同一 Orchestrator 上连续两个问题：各走各的 Tool，互不误调。"""
        inv_handler = _RecordingHandler({"qty": 5.0})
        wo_handler = _RecordingHandler({"status": "RELEASED"})
        registry = _dual_registry(inv_handler, wo_handler)
        router = AIRouterService(
            llm_fallback_enabled=False,
            tool_capabilities=ToolRegistryCapabilityAdapter(registry),
        )
        orch = AIOrchestratorService(router=router, tool_registry=registry)

        r1 = await orch.execute("查询物料 10001 当前库存")
        r2 = await orch.execute("查询工单 WO-001")

        assert r1.metadata["tool_name"] == "get_inventory"
        assert r2.metadata["tool_name"] == "get_work_order"
        assert inv_handler.calls == 1
        assert wo_handler.calls == 1


# ============================================================
# 7. Multi-Tool Selection Regression（用户 §十八）
# ============================================================

class TestMultiToolSelectionRegression:
    @staticmethod
    def _router(registry: ToolRegistry) -> AIRouterService:
        return AIRouterService(
            llm_fallback_enabled=False,
            tool_capabilities=ToolRegistryCapabilityAdapter(registry),
        )

    async def test_multiple_tools_are_selected_by_router(self) -> None:
        """库存问题 → get_inventory；工单问题 → get_work_order。"""
        router = self._router(build_default_tool_registry())

        inv = await router.route("查询物料 10001 当前库存")
        wo = await router.route("查询工单 WO-202609-001")

        assert inv.route == RouteType.TOOL
        assert inv.tool_name == "get_inventory"
        assert wo.route == RouteType.TOOL
        assert wo.tool_name == "get_work_order"

    async def test_no_cross_selection(self) -> None:
        """get_inventory 不会因工单问题被选中，反之亦然。"""
        router = self._router(build_default_tool_registry())

        for question in (
            "查询工单 WO-001",
            "工单号 WO-202609-001",
            "work_order_no=WO-001",
        ):
            decision = await router.route(question)
            assert decision.route == RouteType.TOOL
            assert decision.tool_name == "get_work_order", question

        for question in (
            "查询物料 10001 当前库存",
            "查询物料 MAT-001 当前库存",
        ):
            decision = await router.route(question)
            assert decision.route == RouteType.TOOL
            assert decision.tool_name == "get_inventory", question

    async def test_aggregation_questions_not_hijacked_by_new_tool(self) -> None:
        """"统计本月工单数量" 类聚合问题仍由 Text-to-SQL 承担。"""
        router = self._router(build_default_tool_registry())

        for question in (
            "统计本月工单数量",
            "库存最多的 10 个物料是什么？",
            "哪个仓库库存最高？",
        ):
            decision = await router.route(question)
            assert decision.route != RouteType.TOOL, question

    async def test_default_api_router_discovers_work_order(self) -> None:
        """API 默认装配的 Router 能发现 get_work_order（§二十二验收）。"""
        from backend.app.api import orchestrator_chat

        decision = await orchestrator_chat._default_orchestrator._router.route(
            "查询工单 WO-202609-001"
        )
        assert decision.route == RouteType.TOOL
        assert decision.tool_name == "get_work_order"


# ============================================================
# 8. Tool-to-Tool 隔离（用户 §十九）
# ============================================================

class TestToolToToolIsolation:
    """Tool Handler 不拥有 Registry / ExecutionService / Orchestrator；
    Tool 之间不互相调用（Question → Router → ONE Tool）。"""

    _FORBIDDEN_ATTRS = (
        "_registry",
        "_tool_registry",
        "_tools",
        "_tool_execution",
        "_tool_execution_service",
        "_orchestrator",
        "_router",
    )
    _FORBIDDEN_PARAMS = frozenset({
        "registry", "tool_registry", "tool_execution_service", "orchestrator",
    })

    def test_work_order_handler_has_no_tool_dependencies(self) -> None:
        handler = GetWorkOrderHandler()
        for attr in self._FORBIDDEN_ATTRS:
            assert not hasattr(handler, attr), attr
        params = set(
            inspect.signature(GetWorkOrderHandler.__init__).parameters
        )
        assert not (params & self._FORBIDDEN_PARAMS)

    def test_inventory_handler_has_no_tool_dependencies(self) -> None:
        handler = GetInventoryHandler()
        for attr in self._FORBIDDEN_ATTRS:
            assert not hasattr(handler, attr), attr
        params = set(
            inspect.signature(GetInventoryHandler.__init__).parameters
        )
        assert not (params & self._FORBIDDEN_PARAMS)

    def test_work_order_module_does_not_import_execution_or_orchestrator(
        self,
    ) -> None:
        """模块级 AST import 检查：无 ToolExecutionService /
        Orchestrator / Router 依赖（ToolRegistry 仅注册助手使用）。"""
        path = os.path.join(REPO_ROOT, *(_WORK_ORDER_MODULE.split("/")))
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        forbidden = {
            "backend.app.services.ai_orchestrator_service",
            "backend.app.services.ai_router_service",
            "backend.app.services.tool_execution_service",
            "backend.app.api",
        }
        assert not (forbidden & imported), imported

    def test_work_order_module_has_no_write_sql(self) -> None:
        """源码（docstring 之外的字符串常量）不含写操作 SQL（只读 Tool）。"""
        path = os.path.join(REPO_ROOT, *(_WORK_ORDER_MODULE.split("/")))
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())

        # 收集 docstring 节点 id（说明文字不算 SQL 常量）
        docstring_ids: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(
                node,
                (ast.Module, ast.ClassDef, ast.FunctionDef,
                 ast.AsyncFunctionDef),
            ):
                body = getattr(node, "body", [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstring_ids.add(id(body[0].value))

        strings: list[str] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstring_ids
            ):
                strings.append(node.value)

        for fragment in (
            "insert ", "update ", "delete ", "drop ", "alter ", "truncate ",
        ):
            assert not any(
                fragment in value.lower() for value in strings
            ), fragment
