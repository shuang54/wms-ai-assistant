"""Tool Architecture Contract Tests（Phase 3.11 Step 7）。

本文件只验证 **架构边界（Contract）**，不验证业务结果：

```text
Selection        = Router                       （Tool Selection Authority）
Extraction       = ToolArgumentExtractor        （Candidate Extraction）
Schema           = ToolRegistry                 （Schema Validation Authority）
Execution        = ToolExecutionService         （Capability + Execution Boundary）
Business Logic   = Tool（Handler）               （Business Implementation）
Output           = ToolResult                   （统一输出契约）
Orchestration    = AIOrchestratorService        （编排：decision → extract → execute）
```

Contract 清单：

```text
C1  Router 不执行 Tool（Selection Only；只读 capability 元数据）
C2  ToolArgumentExtractor 不执行 Tool（纯确定性提取；无 Registry / DB / LLM / HTTP）
C3  ToolExecutionService 不解析业务参数（Capability Check + Registry Delegation）
C4  Tool 不调用上层（无 Orchestrator / Router / Extractor / ExecutionService）
C5  Tool 之间不互调（cross-import / Handler 内引用 / 装配层唯一例外）
C6  Orchestrator 只负责编排（不绕过 ToolExecutionService 直接执行 Tool）
C7  ToolRegistry 是唯一 Schema Authority（missing / unknown / type）
C8  ToolExecutionService 原样透传 arguments（不 rename / filter / fill / parse）
C9  Tool Selection 唯一来源 = RouteDecision.tool_name（Orchestrator 不重选）
C10 Unknown Tool 不 fallback（不猜 Tool / 不落到 RAG / Text-to-SQL）
C11 单 Tool 执行（每个 TOOL 请求 1 次执行；无 Tool → Tool / Tool → Router 循环）
C12 禁止 LLM 参数提取（Extractor 无 async / 无 LLM / 无 HTTP / 无 function calling）
C20 AIOrchestrator Tool Observability Integration（Step 18）：TOOL 路径接入
    ToolExecutionContext（一次 execute 一个 request_id / round=1 /
    project_id 仅来自服务器端作用域 / tool_call_id=None）+ 可选
    ToolExecutionObserver 注入；RAG / Text-to-SQL / capability denied
    均 0 Record；observer 失败不改变 ToolResult；不 retry / 不 fallback；
    不创建 Collector / 不接 API / 不持久化 / Metrics 仍为只读下游
```

复用既有测试（**不**重复实现相同断言）：

```text
tests/test_ai_orchestrator.py            §J Tool Selection / §L Argument Extraction
tests/test_tool_execution_service.py     §7 arguments 透传（identity）/ §10 AST 安全
tests/test_tool_argument_extractor.py    §8 迁移锁定 / §9 AST 安全（identifiers/calls）
tests/test_tool_argument_contract.py     §4/6 多参数契约 / §9 Orchestrator E2E
tests/test_get_work_order_tool.py        §8 Tool-to-Tool 隔离（Handler 属性 / 构造参数）
tests/test_ai_router.py                  §Tool Selection Contract（Router 侧）
```

0 DB / 0 Network / 0 LLM（静态 AST + Fake 注入）。
"""
from __future__ import annotations

import ast
import os
from typing import Any

import pytest

from backend.app.services.ai_router_service import (
    AIRouterService,
    RouteType,
    ToolRegistryCapabilityAdapter,
)
from backend.app.services.tool_argument_extractor import ToolArgumentExtractor
from backend.app.services.tool_execution_service import ToolExecutionService
from backend.app.tools.get_inventory import (
    GET_INVENTORY_DEFINITION,
    GetInventoryHandler,
)
from backend.app.tools.get_work_order import (
    GET_WORK_ORDER_DEFINITION,
    GetWorkOrderHandler,
)
from backend.app.tools.registry import ToolRegistry, ToolResult

# 复用既有测试资产（与 test_tool_execution_service 复用 FakeRouter 同模式）
from tests.test_ai_orchestrator import (  # noqa: E402
    FakeContextComposer,
    FakeProjectProvider,
    FakeRAG,
    FakeRouter,
    FakeSQLExecutor,
    FakeTableSelector,
    FakeTextToSQL,
    _make_service,
    _tool_decision,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_MODULE_PATHS = {
    "router": "backend/app/services/ai_router_service.py",
    "extractor": "backend/app/services/tool_argument_extractor.py",
    "execution": "backend/app/services/tool_execution_service.py",
    "orchestrator": "backend/app/services/ai_orchestrator_service.py",
    "registry": "backend/app/tools/registry.py",
    "inventory": "backend/app/tools/get_inventory.py",
    "work_order": "backend/app/tools/get_work_order.py",
    "tool_chat": "backend/app/services/tool_chat_service.py",
}

#: 上层编排模块（Tool 不得依赖）
_UPPER_LAYERS = frozenset({
    "backend.app.services.ai_orchestrator_service",
    "backend.app.services.ai_router_service",
    "backend.app.services.tool_argument_extractor",
    "backend.app.services.tool_execution_service",
})

#: 基础设施（提取 / 执行 / Tool 层均不得依赖）
_INFRA_MODULES = frozenset({
    "sqlalchemy", "psycopg", "psycopg2", "httpx", "requests", "aiohttp",
    "socket", "subprocess", "backend.app.db", "backend.app.api",
})

#: 业务参数字段（不得出现在「非 Tool」组件的源码里）
_BUSINESS_FIELDS = ("material_code", "warehouse_code", "work_order_no")


# ============================================================
# AST 工具（只读，不修改被测代码）
# ============================================================

def _module_source(key: str) -> str:
    path = os.path.join(REPO_ROOT, *(_MODULE_PATHS[key].split("/")))
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _module_ast(key: str) -> ast.Module:
    return ast.parse(_module_source(key))


def _walk_imports(tree: ast.Module) -> set[str]:
    """所有层级的 import（含函数内延迟 import）。"""
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _module_level_imports(tree: ast.Module) -> set[str]:
    """仅模块级 import（不含函数内延迟 import）。"""
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _nested_import_owners(tree: ast.Module, module: str) -> list[str]:
    """返回「函数体内延迟 import ``module``」的最内层函数名。

    模块级 import 不在统计范围内（见 ``_module_level_imports``）。
    """
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    owners: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != module:
            continue
        owner = ""
        current = parents.get(node)
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = current.name
                break
            current = parents.get(current)
        owners.append(owner)
    return owners


def _imported_names_from(tree: ast.Module, module: str) -> set[str]:
    """从 ``module`` 导入的名字集合（含延迟 import）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(alias.name for alias in node.names)
    return names


def _code_layer_haystack(key: str) -> str:
    """代码层文本（imports + AST 标识符 + 非 docstring 字符串，小写）。

    用于「不得出现」类检查：docstring / 注释中的说明文字不算代码依赖。
    """
    tree = _module_ast(key)
    return " ".join(
        list(_walk_imports(tree))
        + list(_identifiers(tree))
        + _non_docstring_strings(tree)
    ).lower()


def _identifiers(node: ast.AST) -> set[str]:
    """Name.id + Attribute.attr（不含 docstring 文本）。"""
    out: set[str] = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Name):
            out.add(item.id.lower())
        elif isinstance(item, ast.Attribute):
            out.add(item.attr.lower())
    return out


def _docstring_ids(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _non_docstring_strings(tree: ast.Module) -> list[str]:
    """源码字符串常量（排除 docstring）：用于「不得出现业务字段 / 写 SQL」检查。"""
    docstrings = _docstring_ids(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _execute_receivers(tree: ast.Module) -> set[str]:
    """收集 ``<receiver>.execute(...)`` 的 receiver 表达式文本。"""
    return {
        ast.unparse(node.func.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
    }


def _class_node(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"未找到 class {name}")


# ============================================================
# 测试替身（记录型；0 DB / 0 Network）
# ============================================================

class _RecordingHandler:
    """记录 arguments 的 Handler。"""

    def __init__(self) -> None:
        self.calls = 0
        self.last_arguments: dict[str, Any] | None = None

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        self.last_arguments = dict(arguments)
        return dict(arguments)


class _RecordingExecution(ToolExecutionService):
    """真实执行边界 + 调用记录。

    Phase 3.11 Step 18：Orchestrator TOOL 路径会传
    ``ToolExecutionContext`` → 本替身接受并原样转发（``calls`` 形状不变）。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def execute(
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        context: Any = None,
    ) -> ToolResult:
        self.calls.append((tool_name, arguments))
        return await super().execute(
            tool_name, arguments=arguments, context=context
        )


class _Env:
    """Orchestrator 环境：Fake 下游 + 可观测的边界调用。"""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        router: Any = None,
        execution: Any = None,
        extractor: Any = None,
    ) -> None:
        self.rag = FakeRAG()
        self.t2s = FakeTextToSQL()
        self.execution = execution or _RecordingExecution(registry=registry)
        self.extractor = extractor or ToolArgumentExtractor()
        self.orchestrator = _make_service(
            router=router or FakeRouter(_tool_decision()),
            rag=self.rag,
            tools=registry,
            t2s=self.t2s,
            sql_executor=FakeSQLExecutor(),
            table_selector=FakeTableSelector(),
            context_composer=FakeContextComposer(),
            project_provider=FakeProjectProvider(),
            tool_execution_service=self.execution,
            tool_argument_extractor=self.extractor,
        )


def _real_router(registry: ToolRegistry) -> AIRouterService:
    return AIRouterService(
        llm_fallback_enabled=False,
        tool_capabilities=ToolRegistryCapabilityAdapter(registry),
    )


class _CountingRouter:
    """包装真实 Router：保持规则选路行为，同时记录 route() 调用次数。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[str] = []

    async def route(self, question: str, *, context: str | None = None):
        self.calls.append(question)
        return await self._inner.route(question, context=context)


def _registry_with_both() -> tuple[ToolRegistry, _RecordingHandler, _RecordingHandler]:
    inv = _RecordingHandler()
    wo = _RecordingHandler()
    registry = ToolRegistry()
    registry.register(GET_INVENTORY_DEFINITION, inv)
    registry.register(GET_WORK_ORDER_DEFINITION, wo)
    return registry, inv, wo


# ============================================================
# C1. Router = Selection Only（不执行 Tool）
# ============================================================

class TestC1RouterIsSelectionOnly:
    """Router 只读取 Tool 元数据（name / description / aliases），
    不持有 handler，也不执行 Tool。"""

    def test_module_level_imports_exclude_execution_and_db(self) -> None:
        imports = _module_level_imports(_module_ast("router"))
        forbidden = {
            "backend.app.tools.registry",
            "backend.app.tools.mock_tools",
            "backend.app.services.tool_execution_service",
            "backend.app.services.ai_orchestrator_service",
        } | set(_INFRA_MODULES)
        assert not (forbidden & imports), imports & forbidden

    def test_no_tool_execution_call_anywhere(self) -> None:
        """任何层级（含延迟 import）都不得出现 ``*.execute(...)``。"""
        tree = _module_ast("router")
        assert _execute_receivers(tree) == set()
        assert "execute" not in _identifiers(tree)

    def test_tool_registry_access_is_metadata_only(self) -> None:
        """唯一允许的 Registry 接触点：
        ``ToolRegistryCapabilityAdapter.list_definitions()``（只读元数据）；
        默认装配的延迟 import 只允许出现在 ``get_default_router``。"""
        tree = _module_ast("router")
        assert _nested_import_owners(
            tree, "backend.app.tools.registry"
        ) == ["get_default_router"]
        source = _module_source("router")
        assert "list_definitions" in source
        assert "list_capabilities" in source

    async def test_router_selects_without_executing_handler(self) -> None:
        handler = _RecordingHandler()
        registry = ToolRegistry()
        registry.register(GET_INVENTORY_DEFINITION, handler)

        decision = await _real_router(registry).route("查询物料 MAT-001 当前库存")

        assert decision.route == RouteType.TOOL
        assert decision.tool_name == "get_inventory"
        assert handler.calls == 0  # Selection Only

    async def test_router_never_calls_execute_on_registry(self) -> None:
        class _SpyRegistry:
            def __init__(self) -> None:
                self.list_definitions_calls = 0

            def list_definitions(self):
                self.list_definitions_calls += 1
                return (GET_INVENTORY_DEFINITION,)

            def execute(self, *args: Any, **kwargs: Any):  # pragma: no cover
                raise AssertionError("Router 不得执行 Tool")

        spy = _SpyRegistry()
        router = AIRouterService(
            llm_fallback_enabled=False,
            tool_capabilities=ToolRegistryCapabilityAdapter(spy),
        )

        decision = await router.route("查询物料 MAT-001 当前库存")

        assert decision.route == RouteType.TOOL
        assert decision.tool_name == "get_inventory"
        assert spy.list_definitions_calls >= 1


# ============================================================
# C2. ToolArgumentExtractor = Pure Extraction
# ============================================================

class TestC2ArgumentExtractorIsPure:
    """Extractor 只做「自然语言 → 参数候选值」，不触碰任何执行能力。

    细节版（identifiers / forbidden calls / SQL 字符串）见
    ``tests/test_tool_argument_extractor.py::TestStaticSecurity``；
    此处只锁 Contract：依赖方向 + 禁止模块。
    """

    def test_imports_are_stdlib_only(self) -> None:
        imports = _walk_imports(_module_ast("extractor"))
        assert imports <= {"__future__", "re", "typing", "collections.abc"}
        assert not any(name.startswith("backend") for name in imports)

    def test_no_execution_dependencies(self) -> None:
        haystack = _code_layer_haystack("extractor")
        for forbidden in (
            "toolregistry", "toolexecutionservice", "aiorchestrator",
            "airouter", "sqlalchemy", "session", "create_engine",
            "httpx", "requests", "openai", "llm_client",
        ):
            assert forbidden not in haystack, forbidden

    def test_has_no_registry_or_execution_calls(self) -> None:
        tree = _module_ast("extractor")
        assert _execute_receivers(tree) == set()
        identifiers = _identifiers(tree)
        for fragment in ("registry", "session", "engine", "llm", "router"):
            assert not any(
                fragment in ident for ident in identifiers
            ), fragment

    def test_extraction_is_side_effect_free(self) -> None:
        extractor = ToolArgumentExtractor()
        assert vars(extractor) == {}
        first = extractor.extract("get_inventory", "查询 A01 仓库 MAT-001 的库存")
        first["material_code"] = "TAMPERED"
        assert extractor.extract(
            "get_inventory", "查询 A01 仓库 MAT-001 的库存"
        ) == {"warehouse_code": "A01", "material_code": "MAT-001"}


# ============================================================
# C3. ToolExecutionService = Capability + Delegation
# ============================================================

class TestC3ExecutionServiceHasNoArgumentLogic:
    """执行边界不解析业务参数：不认识 material_code / warehouse_code /
    work_order_no，也不 import Router / Extractor / RAG / Text-to-SQL。"""

    def test_imports_exclude_router_extractor_and_infra(self) -> None:
        imports = _module_level_imports(_module_ast("execution"))
        forbidden = {
            "backend.app.services.ai_router_service",
            "backend.app.services.tool_argument_extractor",
            "backend.app.services.rag_service",
            "backend.app.services.text_to_sql_service",
            "backend.app.services.ai_orchestrator_service",
        } | set(_INFRA_MODULES)
        assert not (forbidden & imports), imports & forbidden
        # 只允许：Registry（执行权威）+ 纯配置 DTO
        # + Phase 3.11 Step 13：Execution Context（frozen DTO；纯数据，无
        #   Router / Extractor / RAG / T2S / DB / LLM 能力）
        # + Phase 3.11 Step 15：Execution Record / Observer（frozen DTO +
        #   Protocol，单向 Record 出口；无 DB / 消息队列 / tracing backend；
        #   logging / time 仅用于「观测失败 warning + perf_counter 计时」）
        assert imports <= {
            "__future__", "logging", "time", "typing",
            "backend.app.projects.capabilities",
            "backend.app.services.tool_execution_context",
            "backend.app.services.tool_execution_observer",
            "backend.app.services.tool_execution_record",
            "backend.app.tools.registry",
        }

    def test_only_allowed_lazy_import(self) -> None:
        """唯一允许的延迟 import：capability 拒绝异常类型
        （定义在 ``ai_orchestrator_service``；模块级 import 会循环依赖）。
        该 import 只出现在 ``_check_capability`` 内，且只取异常类型。"""
        tree = _module_ast("execution")
        assert _nested_import_owners(
            tree, "backend.app.services.ai_orchestrator_service"
        ) == ["_check_capability"]
        assert _imported_names_from(
            tree, "backend.app.services.ai_orchestrator_service"
        ) == {"AIOrchestratorCapabilityError"}

    def test_no_business_field_names_in_code(self) -> None:
        """docstring 之外的源码不得出现业务字段名（说明文字不算代码）。"""
        strings = _non_docstring_strings(_module_ast("execution"))
        joined = "\n".join(strings).lower()
        for field in _BUSINESS_FIELDS:
            assert field not in joined, field

    def test_no_extraction_patterns(self) -> None:
        source = _module_source("execution")
        tree = _module_ast("execution")
        assert "import re" not in source
        identifiers = _identifiers(tree)
        for fragment in ("pattern", "regex", "finditer", "match"):
            assert not any(
                fragment in ident for ident in identifiers
            ), fragment

    def test_delegates_to_registry_execute(self) -> None:
        """正向约束：执行唯一通道 = ``registry.execute(...)``。"""
        receivers = _execute_receivers(_module_ast("execution"))
        assert receivers, "执行边界必须委派 Registry"
        assert all(
            "registry" in receiver for receiver in receivers
        ), receivers


# ============================================================
# C4. Tool = Business Implementation（不调用上层）
# ============================================================

class TestC4ToolDoesNotDependOnUpperLayers:
    """Tool 只接收 arguments / project context / 既有 DB 抽象；
    不依赖 Orchestrator / Router / Extractor / ExecutionService。"""

    _HANDLERS = {
        "inventory": ("GetInventoryHandler", GetInventoryHandler),
        "work_order": ("GetWorkOrderHandler", GetWorkOrderHandler),
    }

    @pytest.mark.parametrize("key", ["inventory", "work_order"])
    def test_module_imports_exclude_upper_layers(self, key: str) -> None:
        imports = _walk_imports(_module_ast(key))
        assert not (_UPPER_LAYERS & imports), imports & _UPPER_LAYERS

    @pytest.mark.parametrize("key", ["inventory", "work_order"])
    def test_module_imports_limited_to_allowed_dependencies(self, key: str) -> None:
        """允许：既有 DB 抽象（sqlalchemy / backend.app.db.session）、
        Tool 框架（base / errors / registry）、配置与项目上下文。
        禁止：网络客户端 / 上层编排模块。"""
        imports = _walk_imports(_module_ast(key))
        forbidden_network = {
            "httpx", "requests", "aiohttp", "socket", "subprocess",
            "psycopg", "psycopg2", "openai",
        }
        assert not (forbidden_network & imports), imports & forbidden_network
        assert not any(
            name.startswith("backend.app.services") for name in imports
        ), imports

    @pytest.mark.parametrize("key", ["inventory", "work_order"])
    def test_handler_body_has_no_upper_layer_references(self, key: str) -> None:
        class_name, _ = self._HANDLERS[key]
        identifiers = _identifiers(_class_node(_module_ast(key), class_name))
        for forbidden in (
            "toolregistry", "toolexecutionservice", "aiorchestratorservice",
            "toolargumentextractor", "airouterservice", "registry",
            "orchestrator", "router", "extractor",
        ):
            assert not any(
                forbidden in ident for ident in identifiers
            ), (class_name, forbidden)

    @pytest.mark.parametrize("key", ["inventory", "work_order"])
    def test_handler_constructible_without_tool_layer(self, key: str) -> None:
        _, handler_cls = self._HANDLERS[key]
        handler = handler_cls()  # 0 DB / 0 Tool 层依赖
        assert not hasattr(handler, "_registry")
        assert not hasattr(handler, "_tool_execution")


# ============================================================
# C5. Tool-to-Tool Isolation（不互调）
# ============================================================

class TestC5ToolToToolIsolation:
    """Tool A 不得 import / 调用 Tool B。

    Handler 实例属性 / 构造参数隔离已由
    ``tests/test_get_work_order_tool.py::TestToolToToolIsolation`` 覆盖
    （此处不重复）；本类只补 cross-import 与 Handler 内部引用检查。
    """

    _OTHER_TOOL_TOKENS = {
        "inventory": ("get_work_order", "getworkorder", "work_order_no"),
        "work_order": ("get_inventory", "getinventory", "material_code"),
    }

    @pytest.mark.parametrize("key", ["inventory", "work_order"])
    def test_handler_does_not_reference_other_tool(self, key: str) -> None:
        class_name = (
            "GetInventoryHandler" if key == "inventory" else "GetWorkOrderHandler"
        )
        # 只扫 AST 标识符（docstring / 注释中的交叉说明不算调用）
        identifiers = _identifiers(_class_node(_module_ast(key), class_name))
        for token in self._OTHER_TOOL_TOKENS[key]:
            assert not any(
                token in ident for ident in identifiers
            ), (class_name, token)

    def test_no_module_level_cross_import(self) -> None:
        assert "backend.app.tools.get_work_order" not in _module_level_imports(
            _module_ast("inventory")
        )
        assert "backend.app.tools.get_inventory" not in _module_level_imports(
            _module_ast("work_order")
        )

    def test_only_registration_helper_may_cross_import(self) -> None:
        """装配层唯一例外：``build_default_tool_registry`` 延迟 import
        对方的**注册助手**（不是 Handler / 不是调用）。"""
        inventory = _module_ast("inventory")
        owners = _nested_import_owners(
            inventory, "backend.app.tools.get_work_order"
        )
        assert owners == ["build_default_tool_registry"]
        imported_names: set[str] = set()
        for node in ast.walk(inventory):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "backend.app.tools.get_work_order"
            ):
                imported_names.update(alias.name for alias in node.names)
        assert imported_names == {"register_get_work_order_tool"}
        # work_order 侧不存在任何反向 import
        assert "backend.app.tools.get_inventory" not in _walk_imports(
            _module_ast("work_order")
        )


# ============================================================
# C6. Orchestrator = Orchestration Only
# ============================================================

class TestC6OrchestratorIsOrchestrationOnly:
    """Orchestrator 可以持有 ToolRegistry（构造执行边界 + 读取声明字段），
    但不得绕过 ``ToolExecutionService`` 直接执行 Tool，
    也不得 import / 调用任何 Handler。"""

    def test_no_handler_imports(self) -> None:
        imports = _walk_imports(_module_ast("orchestrator"))
        assert "backend.app.tools.get_inventory" not in imports
        assert "backend.app.tools.get_work_order" not in imports
        assert "backend.app.tools.mock_tools" not in imports

    def test_execute_calls_are_limited_to_boundaries(self) -> None:
        receivers = _execute_receivers(_module_ast("orchestrator"))
        assert receivers <= {
            "execution", "self._tool_execution", "self._sql_executor",
        }, receivers
        assert not any(
            "tools" in receiver or "registry" in receiver
            for receiver in receivers
        ), receivers

    def test_registry_used_only_for_definition_lookup(self) -> None:
        source = _module_source("orchestrator")
        assert "self._tools.get_definition(" in source
        assert "self._tools.execute" not in source
        assert "registry.execute" not in source

    def test_depends_on_extractor_and_execution(self) -> None:
        source = _module_source("orchestrator")
        assert (
            "from backend.app.services.tool_argument_extractor import "
            "ToolArgumentExtractor" in source
        )
        assert (
            "from backend.app.services.tool_execution_service import "
            "ToolExecutionService" in source
        )
        assert "_tool_execution" in source
        assert "_argument_extractor" in source

    async def test_tool_path_goes_through_execution_service(self) -> None:
        registry = ToolRegistry()
        handler = _RecordingHandler()
        registry.register(GET_INVENTORY_DEFINITION, handler)
        env = _Env(registry=registry, router=FakeRouter(_tool_decision()))

        result = await env.orchestrator.execute("查询物料 MAT-001 当前库存")

        assert result.metadata["tool_success"] is True
        assert len(env.execution.calls) == 1      # 唯一执行通道
        assert handler.calls == 1


# ============================================================
# C7. ToolRegistry = Schema Validation Authority
# ============================================================

class TestC7RegistryIsSchemaAuthority:
    """Extractor 给候选值；合法性一律由 Registry 裁决
    （missing required / unknown field / invalid type）。"""

    def _service(self, handler: _RecordingHandler):
        registry = ToolRegistry()
        registry.register(GET_INVENTORY_DEFINITION, handler)
        return ToolExecutionService(registry=registry)

    async def test_missing_required_rejected(self) -> None:
        handler = _RecordingHandler()
        service = self._service(handler)
        extractor = ToolArgumentExtractor()

        arguments = extractor.extract(
            "get_inventory",
            "查询 A01 仓库库存",
            parameters=GET_INVENTORY_DEFINITION.parameters,
        )
        assert arguments == {"warehouse_code": "A01"}  # Extractor 不补 material_code

        result = await service.execute("get_inventory", arguments=arguments)

        assert result.success is False
        assert "missing required" in (result.error or "")
        assert handler.calls == 0

    async def test_unknown_field_rejected(self) -> None:
        handler = _RecordingHandler()
        service = self._service(handler)

        result = await service.execute(
            "get_inventory",
            arguments={"material_code": "MAT-001", "batch": "B-001"},
        )

        assert result.success is False
        assert "unknown field" in (result.error or "")
        assert handler.calls == 0

    async def test_invalid_type_rejected(self) -> None:
        handler = _RecordingHandler()
        service = self._service(handler)

        result = await service.execute(
            "get_inventory", arguments={"material_code": 123}
        )

        assert result.success is False
        assert "参数校验失败" in (result.error or "")
        assert handler.calls == 0

    async def test_valid_arguments_pass_to_handler(self) -> None:
        handler = _RecordingHandler()
        service = self._service(handler)

        result = await service.execute(
            "get_inventory", arguments={"material_code": "MAT-001"}
        )

        assert result.success is True
        assert handler.calls == 1

    def test_extractor_module_has_no_schema_validation(self) -> None:
        """Extractor 不得复制 required 校验逻辑（只做候选值识别）。"""
        haystack = _code_layer_haystack("extractor")
        for forbidden in (
            "validate_arguments", "toolresult", "success=false",
            "required", "additionalproperties", "jsonschema",
        ):
            assert forbidden not in haystack, forbidden


# ============================================================
# C8. ToolExecutionService = 原样透传
# ============================================================

class TestC8ArgumentsPassthrough:
    """arguments 经过执行边界必须逐字不变：不 rename / 不 filter /
    不 fill default / 不 parse / 不 normalize。"""

    async def test_dual_arguments_unchanged(self) -> None:
        handler = _RecordingHandler()
        registry = ToolRegistry()
        registry.register(GET_INVENTORY_DEFINITION, handler)
        service = ToolExecutionService(registry=registry)
        arguments = {"material_code": "MAT-001", "warehouse_code": "A01"}

        await service.execute("get_inventory", arguments=arguments)

        assert handler.last_arguments == arguments
        assert set(handler.last_arguments or {}) == set(arguments)

    async def test_missing_optional_not_filled(self) -> None:
        """可选字段缺失 → Handler 收到的 dict 也不多出该 key（无默认填充）。"""
        handler = _RecordingHandler()
        registry = ToolRegistry()
        registry.register(GET_INVENTORY_DEFINITION, handler)
        service = ToolExecutionService(registry=registry)

        await service.execute(
            "get_inventory", arguments={"material_code": "MAT-001"}
        )

        assert handler.last_arguments == {"material_code": "MAT-001"}
        assert "warehouse_code" not in (handler.last_arguments or {})

    async def test_unknown_field_not_filtered_but_rejected(self) -> None:
        """执行边界不"顺手"过滤未知字段：Registry 必须看到它并拒绝。"""
        handler = _RecordingHandler()
        registry = ToolRegistry()
        registry.register(GET_INVENTORY_DEFINITION, handler)
        service = ToolExecutionService(registry=registry)

        result = await service.execute(
            "get_inventory",
            arguments={"material_code": "MAT-001", "batch": "B-001"},
        )

        assert result.success is False
        assert "unknown field" in (result.error or "")
        assert handler.calls == 0

    async def test_orchestrator_path_arguments_unchanged(self) -> None:
        """全链路（Question → Extractor → ExecutionService → Handler）不变形。"""
        handler = _RecordingHandler()
        registry = ToolRegistry()
        registry.register(GET_INVENTORY_DEFINITION, handler)
        env = _Env(registry=registry, router=FakeRouter(_tool_decision()))

        await env.orchestrator.execute("查询仓库 A01 中物料 MAT-001 当前库存")

        assert env.execution.calls == [
            ("get_inventory", {"warehouse_code": "A01", "material_code": "MAT-001"})
        ]
        assert handler.last_arguments == {
            "warehouse_code": "A01", "material_code": "MAT-001",
        }


# ============================================================
# C9. Tool Selection 唯一来源 = RouteDecision.tool_name
# ============================================================

class TestC9SelectionAuthorityIsRouter:
    """Orchestrator 只消费 ``RouteDecision.tool_name``：
    不猜 Tool、不 tokenize 问题、不按关键词重选、不 fallback。"""

    def test_orchestrator_has_no_tool_specific_literals(self) -> None:
        """无 Tool 名称 / 业务字段 / 第二套选择函数（注释中的历史说明不算）。"""
        source = _module_source("orchestrator")
        for literal in (
            "get_inventory", "get_work_order", *_BUSINESS_FIELDS,
        ):
            assert literal not in source, literal

        tree = _module_ast("orchestrator")
        assert "_resolve_tool_name" not in _identifiers(tree)
        assert not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_resolve_tool_name"
            for node in ast.walk(tree)
        )

    @pytest.mark.parametrize("question,expected_tool", [
        ("查询物料 10001 当前库存", "get_inventory"),
        ("查询工单 WO-202609-001", "get_work_order"),
    ])
    async def test_executed_tool_equals_router_decision(
        self, question: str, expected_tool: str,
    ) -> None:
        registry, inv, wo = _registry_with_both()
        router = _real_router(registry)
        decision = await router.route(question)
        assert decision.tool_name == expected_tool

        env = _Env(registry=registry, router=router)
        result = await env.orchestrator.execute(question)

        assert result.metadata["tool_name"] == decision.tool_name
        assert [name for name, _ in env.execution.calls] == [decision.tool_name]
        selected = inv if expected_tool == "get_inventory" else wo
        other = wo if expected_tool == "get_inventory" else inv
        assert selected.calls == 1
        assert other.calls == 0

    async def test_orchestrator_uses_decision_over_question_text(self) -> None:
        """问题文本会命中 get_inventory（"物料 MAT-001"），但 decision 选
        get_work_order → 执行 get_work_order（证明不存在第二套选择算法）。"""
        registry, inv, wo = _registry_with_both()
        env = _Env(
            registry=registry,
            router=FakeRouter(_tool_decision("get_work_order")),
        )

        result = await env.orchestrator.execute(
            "查询物料 MAT-001 的工单 WO-202609-001 状态"
        )

        assert result.metadata["tool_name"] == "get_work_order"
        assert result.metadata["tool_success"] is True
        assert wo.calls == 1
        assert inv.calls == 0
        assert env.execution.calls == [
            ("get_work_order", {"work_order_no": "WO-202609-001"})
        ]


# ============================================================
# C10. Unknown Tool 不 fallback
# ============================================================

class TestC10UnknownToolDoesNotFallback:
    """TOOL 决策缺 tool_name → 既有 RouteError 语义；
    未注册 Tool → ToolResult(success=False)；
    两者都不得落到 RAG / Text-to-SQL / 其它 Tool。"""

    async def test_missing_tool_name_raises_route_error(self) -> None:
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorRouteError,
        )

        registry, inv, wo = _registry_with_both()
        router = FakeRouter(_tool_decision(None))
        env = _Env(registry=registry, router=router)

        with pytest.raises(AIOrchestratorRouteError):
            await env.orchestrator.execute("查询物料 MAT-001 当前库存")

        assert env.execution.calls == []      # 未执行任何 Tool
        assert inv.calls == 0 and wo.calls == 0
        assert env.rag.calls == []            # 不落 RAG
        assert env.t2s.calls == []            # 不落 Text-to-SQL
        assert router.calls == ["查询物料 MAT-001 当前库存"]  # 0 次重选

    async def test_unregistered_tool_fails_without_fallback(self) -> None:
        registry, inv, wo = _registry_with_both()
        env = _Env(registry=registry, router=FakeRouter(_tool_decision("unknown_tool")))

        result = await env.orchestrator.execute("查询物料 MAT-001 当前库存")

        assert result.route == RouteType.TOOL
        assert result.metadata["tool_name"] == "unknown_tool"
        assert result.metadata["tool_success"] is False
        assert "未注册" in (result.data.error or "")
        assert env.execution.calls == [("unknown_tool", {})]
        assert inv.calls == 0 and wo.calls == 0
        assert env.rag.calls == []
        assert env.t2s.calls == []


# ============================================================
# C11. 单 Tool 执行（ONE Tool per request）
# ============================================================

class TestC11SingleToolPerRequest:
    """每个 TOOL 请求：恰好 1 次执行边界调用 + 1 次 Handler 调用；
    无 Tool → Tool、Tool → Router → Tool 循环。"""

    @pytest.mark.parametrize("question,expected_tool", [
        ("查询物料 10001 当前库存", "get_inventory"),
        ("查询工单 WO-202609-001", "get_work_order"),
    ])
    async def test_exactly_one_execution(
        self, question: str, expected_tool: str,
    ) -> None:
        registry, inv, wo = _registry_with_both()
        router = _CountingRouter(_real_router(registry))
        env = _Env(registry=registry, router=router)

        result = await env.orchestrator.execute(question)

        assert result.route == RouteType.TOOL
        assert len(env.execution.calls) == 1
        assert [name for name, _ in env.execution.calls] == [expected_tool]
        assert (inv.calls, wo.calls) in {(1, 0), (0, 1)}
        assert router.calls == [question]        # Router 只调用一次（无循环）

    def test_orchestrator_has_no_tool_loop(self) -> None:
        """静态：Orchestrator 无 loop / 无递归调用自身 execute。"""
        tree = _module_ast("orchestrator")
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.While)):
                raise AssertionError("Orchestrator 不应包含循环（单步执行）")
        assert "asyncio.gather" not in _module_source("orchestrator")


# ============================================================
# C12. 禁止 LLM 参数提取
# ============================================================

class TestC12NoLlmArgumentExtraction:
    """Extractor 必须保持 deterministic：无 LLM / HTTP / async /
    function calling / structured output。"""

    def test_no_async_code(self) -> None:
        tree = _module_ast("extractor")
        async_nodes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.AsyncFunctionDef, ast.Await))
        ]
        assert async_nodes == []

    def test_no_llm_or_http_dependencies(self) -> None:
        """代码层（imports + AST 标识符 + 非 docstring 字符串）不得出现
        LLM / HTTP 依赖；docstring 中"不使用 LLM"的说明文字不算依赖。"""
        tree = _module_ast("extractor")
        haystack = " ".join(
            list(_walk_imports(tree))
            + list(_identifiers(tree))
            + _non_docstring_strings(tree)
        ).lower()
        for forbidden in (
            "openai", "deepseek", "anthropic", "httpx", "requests",
            "aiohttp", "llm", "chat_completion", "function_call",
            "tool_call", "json_mode", "structured_output", "prompt",
            "api_key",
        ):
            assert forbidden not in haystack, forbidden

    def test_no_json_or_schema_prompting(self) -> None:
        imports = _walk_imports(_module_ast("extractor"))
        assert "json" not in imports
        assert "backend.app.llm" not in imports
        assert "backend.app.prompts" not in imports

    def test_extraction_is_deterministic(self) -> None:
        """同一输入 → 恒等输出（纯函数；无随机 / 无外部状态）。"""
        extractor = ToolArgumentExtractor()
        cases = [
            ("get_inventory", "查询 A01 仓库 MAT-001 的库存"),
            ("get_work_order", "查询工单 WO-202609-001"),
            ("unknown_tool", "查询物料 MAT-001 当前库存"),
        ]
        for tool_name, question in cases:
            first = extractor.extract(tool_name, question)
            second = extractor.extract(tool_name, question)
            assert first == second


# ============================================================
# C20. AIOrchestrator Tool Observability Integration（Phase 3.11 Step 18）
# ============================================================

class _C20RecordingHandler:
    """记录 arguments 的 Handler（0 DB）。"""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.calls = 0
        self.last_arguments: dict[str, Any] | None = None
        self._data = data if data is not None else {"qty": 250.0}

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        self.last_arguments = dict(arguments)
        return dict(self._data)


class _C20RecordingExecution(ToolExecutionService):
    """真实执行边界 + Context 记录。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.contexts: list[Any] = []

    async def execute(  # type: ignore[override]
        self,
        tool_name: str,
        *,
        arguments: dict[str, Any] | None = None,
        context: Any = None,
    ) -> ToolResult:
        self.contexts.append(context)
        return await super().execute(
            tool_name, arguments=arguments, context=context
        )


class _C20ExplodingObserver:
    """on_execution 抛异常的 observer（失败必须被隔离）。"""

    def __init__(self) -> None:
        self.calls = 0

    def on_execution(self, record: Any) -> None:
        self.calls += 1
        raise RuntimeError("observer boom")


class _C20CountingRouter:
    def __init__(self, decision: Any) -> None:
        self._decision = decision
        self.calls = 0

    async def route(self, question: str, *, context: str | None = None):
        self.calls += 1
        return self._decision


def _c20_registry() -> tuple[ToolRegistry, _C20RecordingHandler]:
    handler = _C20RecordingHandler()
    registry = ToolRegistry()
    registry.register(GET_INVENTORY_DEFINITION, handler)
    return registry, handler


def _c20_orchestrator(
    *,
    router: Any = None,
    registry: ToolRegistry | None = None,
    observer: Any = None,
    execution: Any = None,
    rag: Any = None,
    t2s: Any = None,
    sql_executor: Any = None,
    table_selector: Any = None,
    context_composer: Any = None,
    project_provider: Any = None,
    capabilities: Any = None,
):
    """构造 Orchestrator（全部下游 Fake / Mock；0 DB / 0 LLM）。"""
    from backend.app.services.ai_orchestrator_service import (
        AIOrchestratorService,
    )

    return AIOrchestratorService(
        router=router or FakeRouter(_tool_decision()),
        rag_service=rag if rag is not None else FakeRAG(),
        tool_registry=registry,
        text_to_sql=t2s if t2s is not None else FakeTextToSQL(),
        sql_executor=(
            sql_executor if sql_executor is not None else FakeSQLExecutor()
        ),
        table_selector=table_selector or FakeTableSelector(),
        context_composer=context_composer or FakeContextComposer(),
        project_context_provider=project_provider or FakeProjectProvider(),
        capabilities=capabilities,
        tool_execution_service=execution,
        tool_execution_observer=observer,
    )


class TestC20OrchestratorToolObservability:
    """C20：AIOrchestrator TOOL 路径接入 Context + Observer（**装配**，
    不改执行能力 / 不改任何 Step 13~17 组件）。

    行为细节见 ``tests/test_ai_orchestrator_tool_observability.py``；
    本类只锁 Contract（结构 / 边界 / 0 事件语义）。
    """

    _QUESTION = "查询物料 MAT-001 当前库存"

    # ---- C20.1 TOOL path passes ToolExecutionContext ----

    async def test_c20_1_tool_path_passes_execution_context(self) -> None:
        from backend.app.services.tool_execution_context import (
            ToolExecutionContext,
        )

        registry, _handler = _c20_registry()
        execution = _C20RecordingExecution(registry=registry)
        orchestrator = _c20_orchestrator(
            registry=registry, execution=execution
        )

        await orchestrator.execute(self._QUESTION)

        assert isinstance(execution.contexts[0], ToolExecutionContext)
        source = _module_source("orchestrator")
        assert "context=tool_context" in source        # 执行调用点带 context
        assert "execution.execute(" in source
        assert "registry.execute(" not in source       # 仍不直接碰 Registry

    # ---- C20.2 one execute() -> one request_id ----

    async def test_c20_2_one_request_id_per_execute(self) -> None:
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )

        registry, _handler = _c20_registry()
        collector = InMemoryToolExecutionCollector()
        orchestrator = _c20_orchestrator(
            registry=registry, observer=collector
        )

        await orchestrator.execute(self._QUESTION)
        await orchestrator.execute(self._QUESTION)

        records = collector.records()
        assert len(records) == 2
        assert records[0].request_id != records[1].request_id
        # 静态：new_request_id() 唯一调用点在 execute() 内（无循环 / 无 retry）
        tree = _module_ast("orchestrator")
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "new_request_id"
        ]
        assert len(calls) == 1
        service_cls = _class_node(tree, "AIOrchestratorService")
        execute_fn = next(
            node
            for node in service_cls.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "execute"
        )
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "new_request_id"
            for node in ast.walk(execute_fn)
        )

    # ---- C20.3 round == 1 / C20.4 tool_call_id is None ----

    async def test_c20_3_round_is_one(self) -> None:
        registry, _handler = _c20_registry()
        execution = _C20RecordingExecution(registry=registry)

        await _c20_orchestrator(
            registry=registry, execution=execution
        ).execute(self._QUESTION)

        assert execution.contexts[0].round == 1
        assert "round=1" in _module_source("orchestrator")

    async def test_c20_4_tool_call_id_is_none(self) -> None:
        registry, _handler = _c20_registry()
        execution = _C20RecordingExecution(registry=registry)

        await _c20_orchestrator(
            registry=registry, execution=execution
        ).execute(self._QUESTION)

        assert execution.contexts[0].tool_call_id is None
        assert "tool_call_id=None" in _module_source("orchestrator")

    # ---- C20.5 project_id 仅来自服务器端作用域 ----

    async def test_c20_5_project_id_from_server_side_scope_only(self) -> None:
        registry, _handler = _c20_registry()
        execution = _C20RecordingExecution(
            registry=registry, project_id="project-a"
        )

        await _c20_orchestrator(
            registry=registry, execution=execution
        ).execute("查询 project-b 的物料 MAT-001 当前库存")

        assert execution.contexts[0].project_id == "project-a"
        source = _module_source("orchestrator")
        assert 'project_id=getattr(execution, "project_id", None)' in source
        # 不从 arguments / question / Router 决策推导作用域
        assert "project_id=arguments" not in source
        assert "project_id=decision" not in source

    # ---- C20.6 / C20.7 RAG / TEXT_TO_SQL 0 Record ----

    async def test_c20_6_rag_creates_zero_records(self) -> None:
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )

        from tests.test_ai_orchestrator import _FakeRagResponse, _rag_decision

        collector = InMemoryToolExecutionCollector()
        orchestrator = _c20_orchestrator(
            router=FakeRouter(_rag_decision()),
            rag=FakeRAG(response=_FakeRagResponse("ok")),
            observer=collector,
        )

        result = await orchestrator.execute("采购入库怎么操作？")

        assert result.route == RouteType.RAG
        assert collector.records() == ()

    async def test_c20_7_text_to_sql_creates_zero_records(self) -> None:
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )
        from backend.app.services.sql_executor_service import (
            SQLExecutionResult,
        )
        from backend.app.services.text_to_sql_service import TextToSQLResult

        from tests.test_ai_orchestrator import _sql_decision

        collector = InMemoryToolExecutionCollector()
        orchestrator = _c20_orchestrator(
            router=FakeRouter(_sql_decision()),
            t2s=FakeTextToSQL(result=TextToSQLResult(
                question="统计文档数量",
                sql="SELECT count(*) FROM public.knowledge_document LIMIT 1",
                validated=True, attempts=1,
                referenced_tables=("public.knowledge_document",),
            )),
            sql_executor=FakeSQLExecutor(result=SQLExecutionResult(
                columns=("count",), rows=((1,),), row_count=1,
                truncated=False, execution_time_ms=1.0,
            )),
            observer=collector,
        )

        result = await orchestrator.execute("统计当前知识库文档数量")

        assert result.route == RouteType.TEXT_TO_SQL
        assert collector.records() == ()

    # ---- C20.8 capability denied 0 Record ----

    async def test_c20_8_capability_denied_creates_zero_records(self) -> None:
        from backend.app.projects.capabilities import ProjectCapabilities
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorCapabilityError,
        )
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )

        registry, handler = _c20_registry()
        collector = InMemoryToolExecutionCollector()
        orchestrator = _c20_orchestrator(
            registry=registry,
            observer=collector,
            capabilities=ProjectCapabilities(tool_names=()),
        )

        with pytest.raises(AIOrchestratorCapabilityError):
            await orchestrator.execute(self._QUESTION)

        assert collector.records() == ()       # 拒绝先于执行 → 0 Record
        assert handler.calls == 0

    # ---- C20.9 / C20.10 成功 / 失败各 1 Record ----

    async def test_c20_9_success_creates_exactly_one_record(self) -> None:
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )

        registry, _handler = _c20_registry()
        collector = InMemoryToolExecutionCollector()

        result = await _c20_orchestrator(
            registry=registry, observer=collector
        ).execute(self._QUESTION)

        records = collector.records()
        assert len(records) == 1
        assert records[0].success is True
        assert records[0].tool_name == "get_inventory"
        assert result.route == RouteType.TOOL

    async def test_c20_10_tool_failure_creates_one_failure_record(self) -> None:
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )

        collector = InMemoryToolExecutionCollector()   # 空 Registry → 未注册

        result = await _c20_orchestrator(
            registry=ToolRegistry(), observer=collector
        ).execute(self._QUESTION)

        records = collector.records()
        assert len(records) == 1
        assert records[0].success is False
        assert result.metadata["tool_success"] is False

    # ---- C20.11 observer 失败不改变执行结果 ----

    async def test_c20_11_observer_failure_cannot_change_result(self) -> None:
        registry, handler = _c20_registry()
        observer = _C20ExplodingObserver()
        router = _C20CountingRouter(_tool_decision())

        result = await _c20_orchestrator(
            router=router, registry=registry, observer=observer
        ).execute(self._QUESTION)

        assert result.route == RouteType.TOOL
        assert result.data.success is True            # ToolResult 未被改写
        assert result.metadata["tool_success"] is True
        assert observer.calls == 1
        assert handler.calls == 1
        assert router.calls == 1                      # Router 只调用一次（无重路由）

    # ---- C20.12 不引入 retry / fallback ----

    async def test_c20_12_no_retry_or_fallback_introduced(self) -> None:
        identifiers = _identifiers(_module_ast("orchestrator"))
        for forbidden in ("retry", "fallback", "replan", "rerun"):
            assert forbidden not in identifiers, forbidden
        source = _module_source("orchestrator")
        assert source.count("execution.execute(") == 1   # 唯一执行调用点

    # ---- C20.13 不创建 Collector ----

    def test_c20_13_orchestrator_does_not_create_collector(self) -> None:
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )

        registry, _handler = _c20_registry()
        collector = InMemoryToolExecutionCollector()
        orchestrator = _c20_orchestrator(
            registry=registry, observer=collector
        )

        assert orchestrator.tool_execution_observer is collector
        source = _module_source("orchestrator")
        assert "InMemoryToolExecutionCollector" not in source
        assert "Collector()" not in source

    # ---- C20.14 不接 API / DB / 持久化 ----

    def test_c20_14_no_api_db_persistence_introduced(self) -> None:
        imports = _walk_imports(_module_ast("orchestrator"))
        for forbidden in (
            "backend.app.api",
            "backend.app.db",
            "sqlalchemy",
            "psycopg",
            "redis",
            "kafka",
            "celery",
            "opentelemetry",
            "backend.app.services.tool_execution_metrics_service",
            "backend.app.services.in_memory_tool_execution_collector",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden

    # ---- C20.15 Metrics 仍为只读下游 ----

    async def test_c20_15_metrics_remain_read_only_downstream(self) -> None:
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )

        registry, _handler = _c20_registry()
        collector = InMemoryToolExecutionCollector()

        await _c20_orchestrator(
            registry=registry, observer=collector
        ).execute(self._QUESTION)

        snapshot = ToolExecutionMetricsService.snapshot(collector.records())
        assert snapshot.total_count == 1
        assert snapshot.success_count == 1
        # Orchestrator 不感知 Metrics（代码层无 import / 无标识符）
        haystack = _code_layer_haystack("orchestrator")
        for forbidden in ("metrics", "snapshot", "aggregate", "percentile"):
            assert forbidden not in haystack, forbidden
