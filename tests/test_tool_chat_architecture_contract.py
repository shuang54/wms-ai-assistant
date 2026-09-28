"""ToolChatService Architecture Contract（Phase 3.11 Step 8 勘察 → Step 9 迁移）。

本文件锁定链路 B 的架构边界（静态 AST + 极小行为断言）。

被锁定链路（Function Calling 链路，链路 B；Step 9 迁移后的真实状态）：

```text
POST /api/chat/with-tools                      backend/app/api/tool_chat.py
    ↓ 模块级单例 _tool_chat_service / _tool_execution_service / _tool_registry
ToolChatService.chat(message, registry)        backend/app/services/tool_chat_service.py
    ↓ while True（预算内多步）
LLM（tools=registry.list_definitions() 转换的 OpenAI schema）
    ↓ ToolCall(name, arguments)（LLM 决定）
ToolExecutionService.execute(name, arguments)  ← Phase 3.11 Step 9：统一执行边界
    ↓
ToolRegistry.execute（Schema 校验 → Handler → 异常归一化）
    ↓
Tool Handler（Step 11 起：真实只读 get_inventory；Mock 仅测试注入）
    ↓ ToolResult → role=tool message → 下一轮 LLM
最终回答
```

Contract 清单（C1–C14）：

```text
C1  入口与接线：唯一入口 = /api/chat/with-tools；模块级单例；
    Step 11 起生产 Registry = 真实只读 get_inventory
C2  执行路径：ToolChatService → ToolExecutionService → ToolRegistry
    → PASS（Phase 3.11 Step 9 迁移完成；不再直接 registry.execute）
    → 反向确认：ToolExecutionService 仍是 ONE Tool execution（无 while / 无 LLM）
C3  ToolChatService 不直接触碰 Handler
C4  多步循环：YES（while + max_rounds 预算；非 Agent / 非无限循环）
C5  Capability 校验：NO（本 Service 不做授权；Step 10 起由执行边界承载）
C6  Project Context：NO（Handler 只收到 arguments）
C7  Schema Authority：ToolRegistry 仍是唯一校验入口（未绕过）
C8  Selection / Argument：100% 来自 LLM（service 源码无 Tool 名字面量、无解析逻辑）
C9  与主链路隔离：不 import Orchestrator / Router / Extractor
    （ToolExecutionService 是共享执行边界，Step 9 起允许依赖）
C10 共享契约：ToolExecutionService + ToolRegistry + ToolResult
C13 Real Tool Execution（Step 11）：生产 Registry 用真实 Definition；
    真实 get_work_order = DEFERRED；Mock 无法覆盖真实 Handler
C14 Tool Failure Boundary（Step 12）：失败不绕过执行边界，也不降级 / fallback；
    capability / schema / multiple / malformed / unknown 五类失败均 0 执行
C15 Tool Execution Context Boundary（Step 13）：Context 由 ToolChatService
    创建并交给执行边界；Registry / Handler / LLM / Tool arguments 均不持有
    或控制 Execution Context（project_id 唯一权威 = 服务器端作用域）
C16 Tool Execution Record Contract（Step 14）：ToolExecutionRecord =
    Context + ToolResult + Timing 的**不可变执行元数据**；不含 arguments /
    result data / SQL / secrets；不持久化；ToolResult / execute() 返回类型 /
    Registry / Handler / API 均不变
C17 Tool Execution Observer Boundary（Step 15）：执行边界把 Record 交给
    **可选** observer（单向 Record 出口）；observer 失败不影响 ToolResult /
    不 retry / 不执行 Tool；observer 无 arguments / result.data / DB / LLM /
    Engine / Session 能力；零持久化
C18 In-Memory Tool Execution Collector（Step 16）：Collector 只保存
    ToolExecutionRecord；records()/查询返回不可变 tuple 快照；保持插入顺序、
    不去重 / 不聚合 / 无统计；无持久化 / 无 DB / 无执行能力 / 无单例
C19 Tool Execution Metrics Read Model（Step 17）：Metrics 是**只读**分析层
    （Iterable[ToolExecutionRecord] → frozen Snapshot）；不重新测量时间 /
    不接受 Collector internals / 不带 request·project·tool 维度 / 无敏感字段 /
    无持久化 / 无单例；生产执行链不依赖它（Metrics 失败不影响 ToolResult）
C22 Tool Execution Collector Retention（Step 20）：有限内存窗口
    （max_records 默认 1000；FIFO 淘汰最新 N 条）；淘汰者对所有查询与 Metrics
    不可见；无 TTL / 后台线程 / 自动 clear / 持久化
C23 Tool Observability Read Query Boundary（Step 21）：Query Service 是只读
    Read Facade（不存储 / 不算数 / 不清空）；retention 与 Collector 一致；
    不依赖 DB / LLM / Registry / API
C24 Tool Observability Snapshot Read Model（Step 22）：ToolExecutionSnapshot
    是**独立对外** Read Model（frozen DTO + 显式逐字段映射）；不持有 Record /
    Collector / Metrics Service；不含 arguments / SQL / secrets；Metrics 仍
    由既有 Metrics Service 计算（不经 Snapshot）
```

已有行为覆盖（不重复）：`tests/test_tool_chat_service.py` /
`tests/test_tool_chat_api.py` / `tests/test_tool_chat_service_characterization.py` /
`tests/test_tool_chat_execution_boundary.py`（Step 9 迁移测试）/
`tests/test_tool_runtime_failure_contract.py`（Step 12 失败矩阵）。

0 DB / 0 Network / 0 LLM。
"""
from __future__ import annotations

import ast
import inspect
import os
from typing import Any

import pytest

from backend.app.services.tool_execution_service import ToolExecutionService

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SERVICE_MODULE = "backend/app/services/tool_chat_service.py"
_API_MODULE = "backend/app/api/tool_chat.py"
_EXECUTION_MODULE = "backend/app/services/tool_execution_service.py"
_CONTEXT_MODULE = "backend/app/services/tool_execution_context.py"

#: 链路 A 的 **Orchestration** 组件：链路 B 不得依赖
#: （ToolExecutionService 不在其中：Phase 3.11 Step 9 起它是两条链路
#:   共享的单 Tool 执行边界）
_CHAIN_A_ORCHESTRATION_MODULES = frozenset({
    "backend.app.services.ai_orchestrator_service",
    "backend.app.services.ai_router_service",
    "backend.app.services.tool_argument_extractor",
})

#: Step 9 起两条链路共享的模块
_SHARED_MODULES = frozenset({
    "backend.app.services.tool_execution_service",
    "backend.app.tools.registry",
    "backend.app.tools.base",
})


# ============================================================
# AST / 源码工具
# ============================================================

def _source(path: str) -> str:
    with open(os.path.join(REPO_ROOT, *path.split("/")), encoding="utf-8") as fh:
        return fh.read()


def _tree(path: str) -> ast.Module:
    return ast.parse(_source(path))


def _walk_imports(tree: ast.Module) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _identifiers(tree: ast.Module) -> set[str]:
    """Name.id + Attribute.attr（不含 docstring 文本）。"""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            out.add(node.attr.lower())
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
    docstrings = _docstring_ids(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _imported_names_from(tree: ast.Module, module: str) -> set[str]:
    """从 ``module`` 导入的名字集合（含延迟 import）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(alias.name for alias in node.names)
    return names


def _execute_receivers(tree: ast.Module) -> set[str]:
    return {
        ast.unparse(node.func.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
    }


# ============================================================
# C1. 入口与接线
# ============================================================

class TestC1EntryPointAndWiring:
    """唯一入口 = ``POST /api/chat/with-tools``；模块级单例；仅 Mock Tools。"""

    def test_api_module_wires_module_level_singletons(self) -> None:
        from backend.app.api import tool_chat as api_module
        from backend.app.services.tool_chat_service import ToolChatService
        from backend.app.tools.registry import ToolRegistry

        assert isinstance(api_module._tool_registry, ToolRegistry)
        assert isinstance(api_module._tool_chat_service, ToolChatService)

    def test_api_registry_contains_real_get_inventory_only(self) -> None:
        """Phase 3.11 Step 11：生产 Registry = 真实只读 get_inventory
        （唯一 Definition；get_work_order 真实接入 = DEFERRED）。"""
        from backend.app.api import tool_chat as api_module
        from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION

        names = [
            d.name for d in api_module._tool_registry.list_definitions()
        ]
        assert names == ["get_inventory"]
        assert (
            api_module._tool_registry.get_definition("get_inventory")
            is GET_INVENTORY_DEFINITION
        )

    def test_api_module_registers_real_tool_not_mock(self) -> None:
        """代码层（imports + AST 标识符 + 非 docstring 字符串）：
        注册真实 get_inventory；不注册 Mock Tools；不接入 get_work_order。
        （docstring 中的 "DEFERRED" 说明文字不算实现。）"""
        tree = _tree(_API_MODULE)
        haystack = " ".join(
            list(_walk_imports(tree))
            + list(_identifiers(tree))
            + _non_docstring_strings(tree)
        )
        assert "register_get_inventory_tool" in haystack
        assert "register_mock_tools" not in haystack
        assert "mock_tools" not in haystack
        assert "get_work_order" not in haystack
        # 真实 Tool 定义来自 backend.app.tools.get_inventory（非 mock 模块）
        assert (
            "backend.app.tools.get_inventory" in _walk_imports(tree)
        )

    def test_http_route_path(self) -> None:
        source = _source(_API_MODULE)
        assert "@router.post(" in source
        assert '"/chat/with-tools"' in source

    def test_service_takes_registry_per_call(self) -> None:
        """registry 由调用方逐次传入（非构造期注入）→ 链路 B 无项目级 Registry。"""
        from backend.app.services.tool_chat_service import ToolChatService

        params = inspect.signature(ToolChatService.chat).parameters
        assert "registry" in params


# ============================================================
# C2. 执行路径（Phase 3.11 Step 9 迁移后：PASS）
# ============================================================

class TestC2ExecutionGoesThroughBoundary:
    """ToolChatService → ToolExecutionService → ToolRegistry.execute。

    结论：``PASS``（Step 9 迁移完成；Service 不再直接 ``registry.execute``）。
    """

    def test_service_calls_execution_service_only(self) -> None:
        """唯一执行调用点 = ``execution.execute(...)``。"""
        receivers = _execute_receivers(_tree(_SERVICE_MODULE))
        assert receivers == {"execution"}, receivers
        assert "registry.execute(" not in _source(_SERVICE_MODULE)

    def test_service_depends_on_execution_service(self) -> None:
        imports = _walk_imports(_tree(_SERVICE_MODULE))
        assert "backend.app.services.tool_execution_service" in imports
        identifiers = _identifiers(_tree(_SERVICE_MODULE))
        assert any("toolexecutionservice" in i for i in identifiers)

    def test_execution_service_still_single_execution_boundary(self) -> None:
        """反向确认：新边界仍是「单次执行」，未被本链路改造为
        loop / orchestration / LLM（loop 只属于 ToolChatService）。"""
        from backend.app.services.tool_execution_service import (
            ToolExecutionService,
        )

        source = inspect.getsource(ToolExecutionService)
        assert "while" not in source
        params = inspect.signature(ToolExecutionService.execute).parameters
        # Phase 3.11 Step 13：新增可选 context（Execution Context，见 C15）；
        # 仍是 ONE Tool execution（无 loop / 无 retry）。
        assert set(params) == {"self", "tool_name", "arguments", "context"}

        tree = _tree("backend/app/services/tool_execution_service.py")
        assert [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.While, ast.For, ast.AsyncFor))
        ] == []
        imports = _walk_imports(tree)
        assert "backend.app.llm.client" not in imports
        assert "backend.app.services.tool_chat_service" not in imports


# ============================================================
# C3. 不直接触碰 Handler
# ============================================================

class TestC3NoDirectHandlerAccess:
    def test_service_module_does_not_import_tool_implementations(self) -> None:
        imports = _walk_imports(_tree(_SERVICE_MODULE))
        for forbidden in (
            "backend.app.tools.get_inventory",
            "backend.app.tools.get_work_order",
            "backend.app.tools.mock_tools",
        ):
            assert forbidden not in imports, forbidden

    def test_service_never_touches_handler_registry(self) -> None:
        """Service 只经执行边界；不访问 Registry 私有 handler 表。"""
        identifiers = _identifiers(_tree(_SERVICE_MODULE))
        assert "_handlers" not in identifiers
        assert "handlers" not in identifiers

    def test_registry_surface_is_metadata_only(self) -> None:
        """Service 对 Registry 只使用 ``list_definitions``（生成 Tool Schema）；
        执行一律经 ToolExecutionService。"""
        source = _source(_SERVICE_MODULE)
        assert "registry.list_definitions()" in source
        assert "registry.execute(" not in source
        assert "registry.register" not in source
        assert "registry.unregister" not in source
        assert "execution.execute(" in source
        assert "ToolExecutionService(registry=registry)" in source


# ============================================================
# C4. 多步循环
# ============================================================

class TestC4MultiStepLoop:
    """链路 B 是**多步**（LLM → Tool → LLM → ...），与主链路
    「ONE route → ONE Tool」不同：YES。"""

    def test_service_has_while_loop(self) -> None:
        tree = _tree(_SERVICE_MODULE)
        while_nodes = [
            node for node in ast.walk(tree) if isinstance(node, ast.While)
        ]
        assert len(while_nodes) == 1

    def test_loop_has_hard_budget(self) -> None:
        source = _source(_SERVICE_MODULE)
        assert "_max_tool_rounds" in source
        assert "tool_round >= self._max_tool_rounds" in source
        assert "ToolCallingBudgetExceededError" in source

    def test_at_most_one_tool_call_per_llm_response(self) -> None:
        source = _source(_SERVICE_MODULE)
        assert "MultipleToolCallsError" in source
        assert "len(response.tool_calls) > 1" in source

    def test_is_not_agent_runtime(self) -> None:
        """无规划 / 无 replanning / 无并行 / 无 retry / 无记忆
        （docstring 中「不做」的说明文字不算实现 → 只扫代码层）。"""
        tree = _tree(_SERVICE_MODULE)
        haystack = " ".join(
            list(_walk_imports(tree))
            + list(_identifiers(tree))
            + _non_docstring_strings(tree)
        ).lower()
        for forbidden in (
            "agent", "plan", "replan", "parallel", "gather",
            "retry", "cache", "memory", "langgraph", "mcp",
        ):
            assert forbidden not in haystack, forbidden


# ============================================================
# C5. Capability 校验（NO）
# ============================================================

class TestC5CapabilityBoundaryAbsent:
    """链路 B **没有** capability 校验（无 ProjectCapabilities 概念）：
    任何注册进该 Registry 的 Tool，LLM 都可请求执行。

    对比：主链路由 ``ToolExecutionService._check_capability`` 硬校验
    （非白名单 → Handler 0 次调用 + HTTP 403）。
    """

    def test_service_does_not_inject_capabilities_into_boundary(self) -> None:
        """构造 ``ToolExecutionService`` 时**不**传 capabilities / project_id
        （→ capabilities=None = 不限制；Step 9 明确 Deferred）。"""
        calls = [
            node
            for node in ast.walk(_tree(_SERVICE_MODULE))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ToolExecutionService"
        ]
        assert len(calls) == 1
        keyword_names = {kw.arg for kw in calls[0].keywords}
        assert keyword_names == {"registry"}

    def test_service_has_no_capability_logic(self) -> None:
        tree = _tree(_SERVICE_MODULE)
        haystack = " ".join(
            list(_walk_imports(tree))
            + list(_identifiers(tree))
            + _non_docstring_strings(tree)
        ).lower()
        assert "capabilit" not in haystack
        assert "allows_tool" not in haystack

    def test_service_signature_has_no_capability_parameter(self) -> None:
        from backend.app.services.tool_chat_service import ToolChatService

        for sig in (
            inspect.signature(ToolChatService.__init__),
            inspect.signature(ToolChatService.chat),
        ):
            assert not any(
                "capabilit" in name for name in sig.parameters
            ), sig

    def test_main_pipeline_keeps_capability_check(self) -> None:
        """对照事实：主链路的 capability 校验仍在（Step 9 未触碰）。"""
        from backend.app.services.tool_execution_service import (
            ToolExecutionService,
        )

        assert "_check_capability" in inspect.getsource(ToolExecutionService)


# ============================================================
# C6. Project Context（NO）
# ============================================================

class TestC6ProjectContextAbsent:
    """链路 B 仍**不下发** ProjectContext（Step 9 明确 Deferred，未实现）。

    Phase 3.11 Step 13 精确化：Service 唯一读取的作用域信息是**执行边界的
    ``project_id`` 标签**（复制进 Execution Context，见 C15）；它不接收 /
    不构造 ProjectContext，也不把 scope 下发给 Handler / LLM。
    """

    def test_service_has_no_project_context_object(self) -> None:
        tree = _tree(_SERVICE_MODULE)
        identifiers = _identifiers(tree)
        haystack = " ".join(
            list(_walk_imports(tree))
            + list(identifiers)
            + _non_docstring_strings(tree)
        ).lower()
        # 没有 ProjectContext / Provider（只有 Execution Context 的 scope 标签）
        assert "projectcontext" not in haystack
        assert "projectcontextprovider" not in identifiers
        assert "project_context_provider" not in identifiers

    def test_service_does_not_import_project_modules(self) -> None:
        imports = _walk_imports(_tree(_SERVICE_MODULE))
        assert not any(
            name.startswith("backend.app.projects") for name in imports
        )


# ============================================================
# C7. Schema Authority（仍然成立）
# ============================================================

class TestC7RegistryIsStillSchemaAuthority:
    """链路 B 未绕过 Schema 校验：arguments 仍全部交给
    ``ToolRegistry.validate_arguments``（行为覆盖见
    ``tests/test_tool_chat_service.py::TestToolValidationError`` 与
    ``tests/test_tool_chat_service_characterization.py``）。"""

    def test_service_does_not_validate_arguments_itself(self) -> None:
        haystack = " ".join(
            list(_walk_imports(_tree(_SERVICE_MODULE)))
            + list(_identifiers(_tree(_SERVICE_MODULE)))
            + _non_docstring_strings(_tree(_SERVICE_MODULE))
        ).lower()
        for forbidden in (
            "validate_arguments", "required", "additionalproperties", "jsonschema",
        ):
            assert forbidden not in haystack, forbidden

    def test_service_delegates_execution_to_boundary(self) -> None:
        """校验仍发生在 Registry（经执行边界进入），Service 不自己校验。"""
        source = _source(_SERVICE_MODULE)
        assert "execution.execute(" in source
        assert "registry.execute(" not in source


# ============================================================
# C8. Selection / Argument 来自 LLM
# ============================================================

class TestC8SelectionComesFromLlm:
    """Tool 名称与 arguments 均由 LLM（function calling）给出：
    Service 源码不含任何 Tool 名字面量，也不做参数解析/映射。"""

    def test_service_has_no_tool_name_literals(self) -> None:
        source = _source(_SERVICE_MODULE)
        for literal in ("get_inventory", "get_work_order"):
            assert literal not in source, literal

    def test_service_does_not_parse_arguments(self) -> None:
        """arguments 已由 LLM Client 解析为 dict（ToolCall.arguments）；
        Service 不再做 JSON 解析 / 映射 / 二次选择。"""
        imports = _walk_imports(_tree(_SERVICE_MODULE))
        assert "json" in imports  # 仅用于「回传 LLM 的序列化」
        source = _source(_SERVICE_MODULE)
        assert "json.loads" not in source
        assert "json.dumps" in source

    def test_tool_schema_comes_from_registry_definitions(self) -> None:
        source = _source(_SERVICE_MODULE)
        assert "definitions_to_openai_tools" in source
        assert "registry.list_definitions()" in source

    def test_llm_response_dto_carries_name_and_arguments(self) -> None:
        from backend.app.llm.client import ToolCall

        fields = set(ToolCall.__dataclass_fields__)
        assert fields == {"id", "name", "arguments"}


# ============================================================
# C9. 与主链路隔离
# ============================================================

class TestC9IsolatedFromMainPipeline:
    """链路 B 不得依赖链路 A 的 **Orchestration**（Orchestrator / Router /
    Extractor）；执行边界是 Step 9 起两者**共享**的组件（允许依赖）。"""

    def test_service_does_not_import_chain_a_orchestration(self) -> None:
        imports = _walk_imports(_tree(_SERVICE_MODULE))
        assert not (_CHAIN_A_ORCHESTRATION_MODULES & imports), (
            imports & _CHAIN_A_ORCHESTRATION_MODULES
        )

    def test_api_module_only_borrows_capability_error(self) -> None:
        """API 层唯一允许的链路 A import：**统一 capability 异常类型**
        （Phase 3.11 Step 10 复用既有 403 语义，不新建第二种 CapabilityError）；
        不 import Orchestrator / Router / Extractor 的任何其它名字。"""
        tree = _tree(_API_MODULE)
        imports = _walk_imports(tree)
        assert (
            imports & _CHAIN_A_ORCHESTRATION_MODULES
        ) == {"backend.app.services.ai_orchestrator_service"}, imports
        assert _imported_names_from(
            tree, "backend.app.services.ai_orchestrator_service"
        ) == {"AIOrchestratorCapabilityError"}

    def test_service_shares_only_execution_boundary_with_chain_a(self) -> None:
        imports = _walk_imports(_tree(_SERVICE_MODULE))
        assert _SHARED_MODULES & imports == {
            "backend.app.services.tool_execution_service",
            "backend.app.tools.base",
            "backend.app.tools.registry",
        }, imports

    def test_service_import_surface_is_minimal(self) -> None:
        imports = _walk_imports(_tree(_SERVICE_MODULE))
        assert imports <= {
            "__future__", "json", "logging", "time", "dataclasses", "typing",
            "uuid",
            "backend.app.config",
            "backend.app.llm.client",
            "backend.app.llm.provider",
            "backend.app.llm.tool_schema",
            # Phase 3.11 Step 13：Execution Context（frozen DTO + request_id）
            "backend.app.services.tool_execution_context",
            "backend.app.services.tool_execution_service",
            "backend.app.tools.base",
            "backend.app.tools.registry",
        }, imports

    def test_main_pipeline_does_not_import_tool_chat(self) -> None:
        """反向：主链路（Orchestrator / ExecutionBoundary）不依赖链路 B。"""
        from backend.app.services.ai_orchestrator_service import (
            __name__ as orchestrator_module,
        )
        from backend.app.services.tool_execution_service import (
            __name__ as execution_module,
        )

        for module_name in (orchestrator_module, execution_module):
            path = module_name.replace(".", "/") + ".py"
            imports = _walk_imports(_tree(path))
            assert "backend.app.services.tool_chat_service" not in imports, path
            assert "backend.app.api.tool_chat" not in imports, path


# ============================================================
# C10. 共享契约（ToolExecutionService + ToolRegistry + ToolResult）
# ============================================================

class TestC10SharedContracts:
    """两条链路共享的组件：**单 Tool 执行边界** + Registry + Result
    （Phase 3.11 Step 9 起执行边界已统一；Orchestration 仍各自独立）。"""

    def test_both_paths_use_same_registry_class(self) -> None:
        import backend.app.services.tool_chat_service as chain_b
        import backend.app.services.tool_execution_service as chain_a
        from backend.app.tools.registry import ToolRegistry

        assert chain_b.ToolRegistry is ToolRegistry
        assert chain_a.ToolRegistry is ToolRegistry

    def test_both_paths_use_same_tool_result(self) -> None:
        import backend.app.services.tool_chat_service as chain_b
        from backend.app.tools.base import ToolResult

        assert chain_b.ToolResult is ToolResult

    def test_execution_boundary_is_shared_now(self) -> None:
        """Step 9 事实：两条链路都经 ``ToolExecutionService``
        （链路 A：Orchestrator 调用；链路 B：ToolChatService 调用）。"""
        import backend.app.services.tool_chat_service as chain_b
        import backend.app.services.ai_orchestrator_service as chain_a
        from backend.app.services.tool_execution_service import (
            ToolExecutionService,
        )

        assert chain_b.ToolExecutionService is ToolExecutionService
        assert chain_a.ToolExecutionService is ToolExecutionService
        assert "ToolExecutionService" in _source(_SERVICE_MODULE)
        assert "registry.execute(" not in _source(_SERVICE_MODULE)

    def test_orchestration_is_not_merged(self) -> None:
        """两个 Orchestrator 仍独立：链路 B 不 import Orchestrator，
        链路 A 不 import ToolChatService。"""
        assert not (
            _walk_imports(_tree(_SERVICE_MODULE))
            & {"backend.app.services.ai_orchestrator_service"}
        )
        orchestrator_imports = _walk_imports(
            _tree("backend/app/services/ai_orchestrator_service.py")
        )
        assert (
            "backend.app.services.tool_chat_service" not in orchestrator_imports
        )


# ============================================================
# C11. Capability Boundary（Phase 3.11 Step 10）
# ============================================================

class TestC11CapabilityBoundary:
    """项目级 Tool 授权（capability）**只**存在于执行边界：

        ToolChatService → ToolExecutionService（唯一 Enforcement Point）→ ToolRegistry

    链路 B 的 ToolChatService 自身不含任何 capability 判断；
    授权来源 = 服务器端 ProjectRegistry.capabilities（HTTP 不可注入）。
    """

    def test_enforcement_point_is_execution_service(self) -> None:
        execution_source = _source(
            "backend/app/services/tool_execution_service.py"
        )
        assert "_check_capability" in execution_source
        assert "allows_tool" in execution_source
        assert "AIOrchestratorCapabilityError" in execution_source
        # 链路 B 的 Service 只转发（不含白名单判断 / 不 import ProjectCapabilities）
        service_haystack = " ".join(
            list(_walk_imports(_tree(_SERVICE_MODULE)))
            + list(_identifiers(_tree(_SERVICE_MODULE)))
            + _non_docstring_strings(_tree(_SERVICE_MODULE))
        ).lower()
        assert "allows_tool" not in service_haystack
        assert "projectcapabilities" not in service_haystack

    def test_api_resolves_capabilities_from_project_registry(self) -> None:
        """API 层：ProjectRegistry.get(project_id) → registration.capabilities
        → ToolExecutionService(capabilities=..., project_id=...)。"""
        tree = _tree(_API_MODULE)
        builder_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ToolExecutionService"
        ]
        # 两个构造点：模块级默认边界（capabilities=None）+ 项目级边界
        project_calls = [
            node
            for node in builder_calls
            if {kw.arg for kw in node.keywords}
            == {"registry", "capabilities", "project_id"}
        ]
        assert len(project_calls) == 1, "唯一项目级边界构造点"
        default_calls = [
            node
            for node in builder_calls
            if {kw.arg for kw in node.keywords} == {"registry"}
        ]
        assert len(default_calls) == 1, "旧行为默认边界（不限制）唯一"

        source = _source(_API_MODULE)
        assert ".get(project_id)" in source
        assert "registration.capabilities" in source

    def test_request_cannot_inject_capabilities(self) -> None:
        """HTTP DTO 只有 message / project_id：能力字段无法由请求注入。"""
        from backend.app.api.tool_chat import ToolChatRequest

        assert set(ToolChatRequest.model_fields) == {"message", "project_id"}
        for field_name in ("message", "project_id"):
            annotation = str(ToolChatRequest.model_fields[field_name].annotation)
            assert "Capabilities" not in annotation

    def test_capability_error_is_not_downgraded_to_tool_result(self) -> None:
        """授权失败未被降级为 ToolResult：执行调用点不在 try/except 内。"""
        tree = _tree(_SERVICE_MODULE)
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        call = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
        )
        current: ast.AST | None = call
        while current is not None:
            assert not isinstance(current, ast.Try), (
                "capability 拒绝不得被 try/except 捕获后转成 ToolResult"
            )
            current = parents.get(current)

    def test_api_maps_capability_denied_to_403(self) -> None:
        source = _source(_API_MODULE)
        assert "AIOrchestratorCapabilityError" in source
        assert "HTTP_403_FORBIDDEN" in source
        assert "HTTP_404_NOT_FOUND" in source


# ============================================================
# C12. Project Context Boundary（Phase 3.11 Step 10）
# ============================================================

class TestC12ProjectContextBoundary:
    """project scope 只作为**执行上下文**进入执行边界：

        project_id → ToolExecutionService(capabilities, project_id) → ToolRegistry

    它**不**进入 LLM 输入（messages / tools schema），
    也**不**进入 Tool Handler 的 arguments。
    """

    def test_service_signatures_have_no_project_parameter(self) -> None:
        from backend.app.services.tool_chat_service import ToolChatService

        for sig in (
            inspect.signature(ToolChatService.__init__),
            inspect.signature(ToolChatService.chat),
        ):
            assert not any(
                "project" in name for name in sig.parameters
            ), sig

    def test_endpoint_passes_project_scope_only_to_boundary(self) -> None:
        """`chat(...)` 的关键字参数只有 registry / execution_service；
        project_id 不会被塞进消息或 Tool 参数。"""
        tree = _tree(_API_MODULE)
        chat_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "chat"
        ]
        assert chat_calls, "未找到 chat() 调用"
        for call in chat_calls:
            keyword_names = {kw.arg for kw in call.keywords}
            assert keyword_names <= {
                "registry", "execution_service",
            }, keyword_names
            # 第一条位置参数永远是 request.message（不拼接 project_id）
            assert ast.unparse(call.args[0]) == "request.message"

    def test_service_only_copies_scope_into_execution_context(self) -> None:
        """Phase 3.11 Step 13：scope 只被**复制**进 Execution Context
        （来源 = 执行边界的 ``project_id``），不进入 messages / arguments /
        Handler。
        """
        source = _source(_SERVICE_MODULE)
        assert 'getattr(execution, "project_id", None)' in source
        assert "project_id=context_project_id" in source

        # AST：Service 不构造任何含 "project_id" 键的 payload
        # （messages / tool arguments / tool message 均不得携带 scope）
        tree = _tree(_SERVICE_MODULE)
        dict_keys = {
            key.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Dict)
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        assert "project_id" not in dict_keys, dict_keys
        assert "request_id" not in dict_keys, dict_keys
        assert "round" not in dict_keys, dict_keys

    def test_handler_arguments_contract_unchanged(self) -> None:
        """Tool Handler 仍只接收 arguments（ToolHandler 协议不变）。"""
        from backend.app.tools.base import ToolHandler
        import backend.app.tools.base as base_module

        assert hasattr(base_module, "ToolHandler")
        signature = inspect.signature(ToolHandler.__call__)
        assert list(signature.parameters) == ["self", "arguments"]


# ============================================================
# C13. Real Tool Execution（Phase 3.11 Step 11）
# ============================================================

class TestC13RealToolExecution:
    """生产链路执行**真实**只读 Tool（get_inventory）：

        ToolChatService → ToolExecutionService → ToolRegistry
            → Real Handler → PostgreSQL（READ ONLY）

    禁止（静态锁定）：

        ToolChatService → Real Handler
        ToolChatService → SQL
        ToolChatService → DB（sqlalchemy / engine / session / cursor）

    真实 DB 行为验证见 ``tests/test_tool_chat_real_get_inventory.py``
    （RUN_DB_TESTS=1 门控）。"""

    def test_production_registry_uses_real_definition(self) -> None:
        from backend.app.api import tool_chat as api_module
        from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION
        from backend.app.tools.mock_tools import (
            GET_INVENTORY_DEFINITION as MOCK_INVENTORY_DEFINITION,
        )

        definition = api_module._tool_registry.get_definition("get_inventory")
        assert definition is GET_INVENTORY_DEFINITION
        assert definition is not MOCK_INVENTORY_DEFINITION
        assert definition.parameters["required"] == ["material_code"]

    def test_real_get_work_order_not_registered(self) -> None:
        """真实 get_work_order 接入 = DEFERRED（生产 Registry 只有 1 个 Tool）。"""
        from backend.app.api import tool_chat as api_module

        names = tuple(
            d.name for d in api_module._tool_registry.list_definitions()
        )
        assert names == ("get_inventory",)

    def test_mock_tool_cannot_overwrite_real_handler(self) -> None:
        """同名 Mock Definition 无法静默覆盖真实 Tool（Registry 拒绝重复注册）。"""
        from backend.app.api import tool_chat as api_module
        from backend.app.tools.mock_tools import (
            GET_INVENTORY_DEFINITION as MOCK_INVENTORY_DEFINITION,
            GetInventoryHandler as MockGetInventoryHandler,
        )
        from backend.app.tools.errors import ToolAlreadyRegisteredError

        with pytest.raises(ToolAlreadyRegisteredError):
            api_module._tool_registry.register(
                MOCK_INVENTORY_DEFINITION, MockGetInventoryHandler()
            )

    def test_service_has_no_db_or_sql_access(self) -> None:
        haystack = " ".join(
            list(_walk_imports(_tree(_SERVICE_MODULE)))
            + list(_identifiers(_tree(_SERVICE_MODULE)))
            + _non_docstring_strings(_tree(_SERVICE_MODULE))
        ).lower()
        for forbidden in (
            "sqlalchemy", "session", "engine", "cursor", "connect",
            "select", "insert", "update", "delete", "where_clause",
            "get_engine", "material_code", "get_inventory",
        ):
            assert forbidden not in haystack, forbidden

    def test_api_module_does_not_build_sql_or_engine(self) -> None:
        haystack = " ".join(
            list(_walk_imports(_tree(_API_MODULE)))
            + list(_identifiers(_tree(_API_MODULE)))
            + _non_docstring_strings(_tree(_API_MODULE))
        ).lower()
        for forbidden in (
            "sqlalchemy", "text(", "get_engine", "select ", "execute(",
        ):
            assert forbidden not in haystack, forbidden

    def test_real_tool_reachable_only_via_execution_boundary(self) -> None:
        """执行调用点唯一：``execution.execute(...)``（无 registry.execute）。"""
        receivers = _execute_receivers(_tree(_SERVICE_MODULE))
        assert receivers == {"execution"}, receivers
        assert "registry.execute(" not in _source(_SERVICE_MODULE)


# ============================================================
# C14. Tool Failure Boundary（Phase 3.11 Step 12）
# ============================================================

class TestC14ToolFailureBoundary:
    """失败契约的边界锁定：

        capability denied  → no Handler
        schema rejected    → no Handler
        multiple ToolCall  → no Tool execution
        malformed ToolCall → no Tool execution
        unknown Tool       → no fallback Tool

    行为矩阵细节见 ``tests/test_tool_runtime_failure_contract.py``；
    本类只锁定「失败不绕过 / 不降级 / 不 fallback」的边界事实。
    """

    def _real_registry_with_recorder(self) -> tuple[object, object]:
        from backend.app.tools.get_inventory import (
            GET_INVENTORY_DEFINITION,
            register_get_inventory_tool,
        )
        from backend.app.tools.registry import ToolRegistry

        from tests.test_tool_runtime_failure_contract import _RecordingHandler

        handler = _RecordingHandler()
        registry = ToolRegistry()
        register_get_inventory_tool(registry, handler=handler)
        assert (
            registry.get_definition("get_inventory")
            is GET_INVENTORY_DEFINITION
        )
        return registry, handler

    async def test_capability_denied_never_reaches_handler(self) -> None:
        from backend.app.projects.capabilities import ProjectCapabilities
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorCapabilityError,
        )
        from backend.app.services.tool_chat_service import ToolChatService
        from backend.app.services.tool_execution_service import (
            ToolExecutionService,
        )

        from tests.test_tool_chat_service import (
            ScriptedLLMClient,
            _tool_call_llm_response,
        )

        registry, handler = self._real_registry_with_recorder()
        execution = ToolExecutionService(
            registry=registry,
            capabilities=ProjectCapabilities(tool_names=()),
            project_id="project-b",
        )
        service = ToolChatService(
            llm_client=ScriptedLLMClient([
                _tool_call_llm_response(
                    "get_inventory", {"material_code": "MAT-001"}
                ),
                "回答",
            ]),
            execution_service=execution,
        )

        with pytest.raises(AIOrchestratorCapabilityError):
            await service.chat("q", registry=registry)

        assert handler.calls == 0

    @pytest.mark.parametrize(
        "arguments",
        [
            {},                                              # missing required
            {"material_code": "MAT-001", "x": 1},            # unknown field
            {"material_code": 123},                          # invalid type
        ],
    )
    async def test_schema_rejected_never_reaches_handler(
        self, arguments: dict
    ) -> None:
        from backend.app.services.tool_chat_service import ToolChatService

        from tests.test_tool_chat_service import (
            ScriptedLLMClient,
            _tool_call_llm_response,
        )

        registry, handler = self._real_registry_with_recorder()
        llm = ScriptedLLMClient([
            _tool_call_llm_response("get_inventory", arguments),
            "回答",
        ])
        service = ToolChatService(llm_client=llm)

        result = await service.chat("q", registry=registry)

        assert handler.calls == 0
        # 失败仍以 ToolResult 形式回传 LLM（可继续，但不执行 Handler）
        assert len(result.tool_calls) == 1

    async def test_multiple_tool_calls_never_execute(self) -> None:
        from backend.app.llm.client import LLMResponse, ToolCall
        from backend.app.services.tool_chat_service import (
            MultipleToolCallsError,
            ToolChatService,
        )
        from backend.app.services.tool_execution_service import (
            ToolExecutionService,
        )

        from tests.test_tool_chat_service import ScriptedLLMClient

        registry, handler = self._real_registry_with_recorder()
        execution = _CountingExecution(registry=registry)
        service = ToolChatService(
            llm_client=ScriptedLLMClient([
                LLMResponse(
                    content=None,
                    tool_calls=(
                        ToolCall(
                            id="c1",
                            name="get_inventory",
                            arguments={"material_code": "M1"},
                        ),
                        ToolCall(
                            id="c2",
                            name="get_inventory",
                            arguments={"material_code": "M2"},
                        ),
                    ),
                ),
                "回答",
            ]),
            execution_service=execution,
        )

        with pytest.raises(MultipleToolCallsError):
            await service.chat("q", registry=registry)

        assert execution.calls == []
        assert handler.calls == 0
        assert isinstance(execution, ToolExecutionService)

    async def test_malformed_tool_call_never_executes(self) -> None:
        from backend.app.llm.client import LLMToolCallFormatError
        from backend.app.services.tool_chat_service import ToolChatService

        from tests.test_tool_chat_service import ScriptedLLMClient

        registry, handler = self._real_registry_with_recorder()
        execution = _CountingExecution(registry=registry)
        service = ToolChatService(
            llm_client=ScriptedLLMClient([
                LLMToolCallFormatError("arguments 不是合法 JSON"),
            ]),
            execution_service=execution,
        )

        with pytest.raises(LLMToolCallFormatError):
            await service.chat("q", registry=registry)

        assert execution.calls == []
        assert handler.calls == 0

    async def test_unknown_tool_no_fallback_execution(self) -> None:
        from backend.app.services.tool_chat_service import ToolChatService

        from tests.test_tool_chat_service import (
            ScriptedLLMClient,
            _tool_call_llm_response,
        )

        registry, handler = self._real_registry_with_recorder()
        llm = ScriptedLLMClient([
            _tool_call_llm_response("not_registered_tool", {"material_code": "M1"}),
            "回答",
        ])
        service = ToolChatService(llm_client=llm)

        result = await service.chat("q", registry=registry)

        # 不 fallback 到已注册的 get_inventory
        assert handler.calls == 0
        assert [c.tool_name for c in result.tool_calls] == ["not_registered_tool"]

    def test_service_swallows_no_failure(self) -> None:
        """本 Service 无任何 try/except：失败一律穿透或经 ToolResult 契约。"""
        assert not [
            node
            for node in ast.walk(_tree(_SERVICE_MODULE))
            if isinstance(node, (ast.Try,))
        ], "ToolChatService 不应吞任何异常（失败契约不得被降级）"
        assert "allows_tool" not in _identifiers(_tree(_SERVICE_MODULE))
        assert "fallback" not in " ".join(
            list(_identifiers(_tree(_SERVICE_MODULE)))
            + _non_docstring_strings(_tree(_SERVICE_MODULE))
        ).lower()


# ============================================================
# C15. Tool Execution Context Boundary（Phase 3.11 Step 13）
# ============================================================

class TestC15ToolExecutionContextBoundary:
    """Execution Context 的拥有者 / 传递路径 / 隔离边界：

        ToolChatService
            ↓ 创建 ToolExecutionContext（request_id / project_id /
              tool_call_id / round）
        ToolExecutionService.execute(..., context=…)
            ↓（Registry 只收到 tool_name + arguments）
        ToolRegistry → Tool Handler

    锁定：
        * Context 由 ToolChatService 创建（边界不生成 request_id）；
        * Registry **不**持有 Context（签名 / 源码均无 context）；
        * Tool Handler **不**持有 Context（只接收 arguments）；
        * LLM **不能**控制 project_id / round / request_id；
        * Tool arguments **不含** Runtime Context。
    """

    def test_service_creates_execution_context(self) -> None:
        imports = _walk_imports(_tree(_SERVICE_MODULE))
        assert (
            "backend.app.services.tool_execution_context" in imports
        )
        source = _source(_SERVICE_MODULE)
        assert "ToolExecutionContext(" in source
        assert "new_request_id()" in source
        # request_id 只生成一次（每轮复用同一 request_id）
        assert source.count("new_request_id()") == 1

    def test_service_passes_context_to_boundary(self) -> None:
        """AST：唯一执行调用点携带 ``context=`` 关键字。"""
        tree = _tree(_SERVICE_MODULE)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
        ]
        assert len(calls) == 1, "执行调用点应唯一"
        keyword_names = {kw.arg for kw in calls[0].keywords}
        assert keyword_names == {"arguments", "context"}, keyword_names

    def test_scope_comes_from_boundary_not_llm(self) -> None:
        """project scope 只从执行边界读取（不读 ToolCall.arguments）。"""
        source = _source(_SERVICE_MODULE)
        assert 'getattr(execution, "project_id", None)' in source
        assert "arguments[" not in source
        assert "arguments.get(" not in source

    def test_boundary_signature_accepts_context(self) -> None:
        params = inspect.signature(ToolExecutionService.execute).parameters
        assert set(params) == {"self", "tool_name", "arguments", "context"}

    def test_registry_does_not_own_context(self) -> None:
        from backend.app.tools.registry import ToolRegistry

        params = inspect.signature(ToolRegistry.execute).parameters
        assert set(params) == {"self", "tool_name", "arguments"}
        registry_source = _source("backend/app/tools/registry.py")
        assert "ToolExecutionContext" not in registry_source
        assert "context" not in params

    def test_handler_does_not_own_context(self) -> None:
        from backend.app.tools.base import ToolHandler

        assert list(
            inspect.signature(ToolHandler.__call__).parameters
        ) == ["self", "arguments"]
        inventory_source = _source("backend/app/tools/get_inventory.py")
        assert "ToolExecutionContext" not in inventory_source
        assert "request_id" not in inventory_source

    def test_boundary_forwards_arguments_unchanged(self) -> None:
        """执行边界不把 Context 塞进 Registry 入参（原样透传）。

        Phase 3.11 Step 15：Context / Record 只进入 `_emit_record(...)`
        （观测出口），Registry 调用仍只有 ``arguments=``。
        """
        source = _source(_EXECUTION_MODULE)
        assert "self._registry.execute(tool_name, arguments=arguments)" in source
        assert "arguments=context" not in source


# ============================================================
# C16. Tool Execution Record Contract（Phase 3.11 Step 14）
# ============================================================

class TestC16ToolExecutionRecordContract:
    """`ToolExecutionRecord` = Context + ToolResult + Timing 的不可变元数据：

        C16.1 frozen / immutable
        C16.2 字段只有执行元数据（白名单）
        C16.3 不含 arguments / result data / SQL / credentials / connection
        C16.4 request_id / project_id / tool_call_id / round 来自 Execution Context
        C16.5 tool_name 来自实际 execute 参数（不推断）
        C16.6 success 来自 ToolResult（不推断）
        C16.7 错误信息不泄露 secret / password / connection string / traceback
        C16.8 Record 模块本身不执行 DB / LLM / Tool / IO / logging
    """

    _RECORD_MODULE = "backend/app/services/tool_execution_record.py"

    def _record_kwargs(self, **overrides):
        from datetime import datetime, timedelta, timezone

        from backend.app.services.tool_execution_context import (
            ToolExecutionContext,
        )
        from backend.app.tools.base import ToolResult

        started = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
        kwargs = {
            "context": ToolExecutionContext(
                request_id="req-1", round=2, project_id="project-a",
                tool_call_id="call_002",
            ),
            "tool_name": "get_inventory",
            "result": ToolResult(
                tool_name="get_inventory", success=True,
                data={"material_code": "MAT-001", "qty": 250.0},
            ),
            "started_at": started,
            "finished_at": started + timedelta(milliseconds=5),
            "duration_ms": 5.0,
        }
        kwargs.update(overrides)
        return kwargs

    def _build(self, **overrides):
        from backend.app.services.tool_execution_record import (
            ToolExecutionRecord,
        )

        return ToolExecutionRecord.from_execution(
            **self._record_kwargs(**overrides)
        )

    # ---- C16.1 ----
    def test_record_is_frozen(self) -> None:
        import dataclasses

        record = self._build()
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.round = 3  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.success = False  # type: ignore[misc]

    # ---- C16.2 ----
    def test_fields_are_execution_metadata_only(self) -> None:
        import dataclasses

        record = self._build()
        record.assert_field_whitelist()
        assert {f.name for f in dataclasses.fields(record)} == {
            "request_id", "project_id", "tool_call_id", "round",
            "tool_name", "started_at", "finished_at", "duration_ms",
            "success", "error_code", "error_type",
        }

    # ---- C16.3 ----
    def test_no_arguments_result_data_sql_or_credentials(self) -> None:
        import dataclasses
        import json

        from backend.app.tools.base import ToolResult

        leaky = ToolResult(
            tool_name="get_inventory", success=False,
            error=(
                "ToolExecutionError: Tool 'get_inventory' 执行失败: "
                "[RuntimeError] SELECT material_code FROM public.inventory "
                "postgresql://wms_user:s3cret-pw@db.internal:5432/wms "
                "Authorization: Bearer sk-test"
            ),
        )
        record = self._build(result=leaky)
        blob = json.dumps(dataclasses.asdict(record), default=str)
        for secret in (
            "SELECT", "postgresql://", "s3cret-pw", "db.internal",
            "Authorization", "Bearer", "sk-test", "material_code",
            "执行失败",
        ):
            assert secret not in blob, secret
        assert record.error_type == "ToolExecutionError"   # 仅类名（安全）

    # ---- C16.4 ----
    def test_context_fields_mapped_from_execution_context(self) -> None:
        record = self._build()
        assert record.request_id == "req-1"
        assert record.project_id == "project-a"
        assert record.tool_call_id == "call_002"
        assert record.round == 2

    # ---- C16.5 ----
    def test_tool_name_comes_from_execute_parameter(self) -> None:
        import inspect

        from backend.app.services.tool_execution_record import (
            ToolExecutionRecord,
        )

        params = inspect.signature(
            ToolExecutionRecord.from_execution
        ).parameters
        assert "tool_name" in params
        assert "arguments" not in params
        assert "question" not in params

    # ---- C16.6 ----
    def test_success_comes_from_tool_result(self) -> None:
        from backend.app.tools.base import ToolResult

        failed = ToolResult(
            tool_name="get_inventory", success=False,
            error="参数校验失败: missing required field(s): ['material_code']",
        )
        ok = self._build()
        assert ok.success is True
        assert self._build(result=failed).success is False

    # ---- C16.7 ----
    def test_error_classification_is_allow_listed(self) -> None:
        from backend.app.tools.base import ToolResult

        forged = ToolResult(
            tool_name="get_inventory", success=False,
            error="postgresql://user:pw@host/db: Traceback (most recent call last)",
        )
        record = self._build(result=forged)
        assert record.error_type is None      # 非白名单 → 不推断
        assert record.error_code is None      # 不发明错误码体系

    # ---- C16.8 ----
    def test_record_module_is_pure(self) -> None:
        tree = _tree(self._RECORD_MODULE)
        imports = _walk_imports(tree)
        assert imports <= {
            "__future__", "math", "re", "time", "dataclasses", "datetime",
            "typing",
            "backend.app.services.tool_execution_context",
            "backend.app.tools.base",
        }, imports
        for forbidden in (
            "sqlalchemy", "logging", "httpx", "requests", "openai",
            "backend.app.llm", "backend.app.db", "backend.app.tools.registry",
        ):
            assert not any(n.startswith(forbidden) for n in imports), forbidden
        receivers = _execute_receivers(tree)
        assert receivers == set(), receivers

    def test_contracts_unchanged(self) -> None:
        """ToolResult / execute() / ToolChatService / Registry / API 契约不变。

        Phase 3.11 Step 15：Record 的生产位置 = 执行边界（`_emit_record`，
        仅当注入 observer 时；见 C17）→ 此处只锁定「其余契约未变」。
        """
        import dataclasses

        from backend.app.services.tool_execution_service import (
            ToolExecutionService,
        )
        from backend.app.tools.base import ToolResult

        # ToolResult 结构不变（无 Record 字段）
        assert {f.name for f in dataclasses.fields(ToolResult)} == {
            "tool_name", "success", "data", "error",
        }
        # execute() 仍返回 ToolResult（不是 Record / tuple）
        annotation = inspect.signature(
            ToolExecutionService.execute
        ).return_annotation
        assert annotation in {"ToolResult", ToolResult}, annotation
        # ToolChatService / Registry / API 源码均不引用 Record
        for path in (
            _SERVICE_MODULE, _API_MODULE, "backend/app/tools/registry.py",
        ):
            assert "ToolExecutionRecord" not in _source(path), path


class TestC17ToolExecutionObserverBoundary:
    """C17：Observer = **可选**、**单向**的 Record 出口（Phase 3.11 Step 15）。

    行为细节见 ``tests/test_tool_execution_observer.py``；此处只锁 Contract。
    """

    _OBSERVER_MODULE = "backend/app/services/tool_execution_observer.py"

    def _context(self):
        from backend.app.services.tool_execution_context import (
            ToolExecutionContext,
        )

        return ToolExecutionContext(
            request_id="req-c17", round=1, project_id=None, tool_call_id="c1"
        )

    def _registry(self):
        from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION
        from backend.app.tools.registry import ToolRegistry

        class _Handler:
            def __init__(self) -> None:
                self.calls = 0

            async def __call__(self, arguments: dict) -> dict:
                self.calls += 1
                return {"ok": True}

        handler = _Handler()
        registry = ToolRegistry()
        registry.register(GET_INVENTORY_DEFINITION, handler)
        return registry, handler

    # ---- C17.1 observer is optional ----
    def test_observer_is_optional(self) -> None:
        from backend.app.tools.registry import ToolRegistry

        params = inspect.signature(ToolExecutionService.__init__).parameters
        assert params["observer"].default is None
        assert ToolExecutionService(registry=ToolRegistry()).observer is None

    # ---- C17.2 observer receives ToolExecutionRecord only ----
    def test_observer_receives_record_only(self) -> None:
        import typing

        from backend.app.services.tool_execution_observer import (
            ToolExecutionObserver,
        )
        from backend.app.services.tool_execution_record import (
            ToolExecutionRecord,
        )

        signature = inspect.signature(ToolExecutionObserver.on_execution)
        assert list(signature.parameters) == ["self", "record"]
        hints = typing.get_type_hints(ToolExecutionObserver.on_execution)
        assert hints["record"] is ToolExecutionRecord
        assert hints["return"] is type(None)

    # ---- C17.3 one execution produces at most one record event ----
    async def test_at_most_one_event_per_execution(self) -> None:
        registry, _ = self._registry()
        observer = _RecordingObserver()
        boundary = ToolExecutionService(registry=registry, observer=observer)

        await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=self._context(),
        )

        assert len(observer.records) == 1

    # ---- C17.4 ToolResult contract unchanged ----
    def test_toolresult_contract_unchanged(self) -> None:
        import dataclasses

        from backend.app.tools.base import ToolResult

        assert {f.name for f in dataclasses.fields(ToolResult)} == {
            "tool_name", "success", "data", "error",
        }
        annotation = inspect.signature(
            ToolExecutionService.execute
        ).return_annotation
        assert annotation in {"ToolResult", ToolResult}, annotation

    # ---- C17.5 / C17.6 observer failure cannot alter result / cannot retry ----
    async def test_observer_failure_isolated_no_retry(self) -> None:
        registry, handler = self._registry()

        class _ExplodingObserver:
            def __init__(self) -> None:
                self.calls = 0

            def on_execution(self, record: Any) -> None:
                self.calls += 1
                raise RuntimeError("observer failure")

        observer = _ExplodingObserver()
        boundary = ToolExecutionService(registry=registry, observer=observer)

        result = await boundary.execute(
            "get_inventory", arguments={"material_code": "M1"},
            context=self._context(),
        )

        assert result.success is True          # ToolResult 不受 observer 影响
        assert handler.calls == 1              # Tool 恰好执行 1 次（无 retry）
        assert observer.calls == 1             # observer 恰好 1 次（不重试）

    # ---- C17.7 observer cannot execute Tool ----
    def test_observer_cannot_execute_tool(self) -> None:
        tree = _tree(self._OBSERVER_MODULE)
        assert _execute_receivers(tree) == set()
        builders = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        assert builders == set(), builders
        identifiers = _identifiers(tree)
        for forbidden in ("registry", "handler", "session", "engine"):
            assert forbidden not in identifiers, forbidden

    # ---- C17.8 observer cannot access arguments / result.data ----
    async def test_observer_cannot_access_arguments_or_result_data(self) -> None:
        import dataclasses
        import json

        registry, _ = self._registry()
        observer = _RecordingObserver()
        boundary = ToolExecutionService(registry=registry, observer=observer)

        await boundary.execute(
            "get_inventory",
            arguments={"material_code": "SECRET-ARG-42"},
            context=self._context(),
        )

        blob = json.dumps(
            dataclasses.asdict(observer.records[0]), default=str
        )
        assert "SECRET-ARG-42" not in blob
        assert "ok" not in blob

    # ---- C17.9 observer cannot access DB / LLM / Engine / Session ----
    def test_observer_module_has_no_db_or_llm_capability(self) -> None:
        imports = _walk_imports(_tree(self._OBSERVER_MODULE))
        assert imports <= {
            "__future__", "typing",
            "backend.app.services.tool_execution_record",
        }, imports
        for forbidden in (
            "sqlalchemy", "backend.app.db", "backend.app.llm",
            "backend.app.api", "backend.app.tools.registry", "httpx",
        ):
            assert not any(n.startswith(forbidden) for n in imports), forbidden

    # ---- C17.10 no persistence ----
    def test_no_persistence(self) -> None:
        import dataclasses

        from backend.app.services.tool_execution_observer import (
            ToolExecutionObserver,
        )

        # Protocol 只有 on_execution（无 record/store/persist/emit 等）
        methods = [
            name for name in vars(ToolExecutionObserver)
            if not name.startswith("_")
        ]
        assert methods == ["on_execution"], methods
        # 边界 / Record / Observer 三个模块均无持久化依赖
        for path in (
            self._OBSERVER_MODULE,
            "backend/app/services/tool_execution_record.py",
            _EXECUTION_MODULE,
        ):
            imports = _walk_imports(_tree(path))
            for forbidden in (
                "sqlalchemy", "backend.app.db", "backend.app.api",
                "kafka", "redis", "celery", "opentelemetry", "logging.handlers",
            ):
                assert not any(
                    n.startswith(forbidden) for n in imports
                ), (path, forbidden)


class TestC18InMemoryToolExecutionCollector:
    """C18：In-Memory Collector（Phase 3.11 Step 16）。

    行为细节见 ``tests/test_in_memory_tool_execution_collector.py``；
    此处只锁 Contract（结构与依赖方向）。
    """

    _COLLECTOR_MODULE = (
        "backend/app/services/in_memory_tool_execution_collector.py"
    )

    # ---- C18.1 stores ToolExecutionRecord only ----
    def test_c18_1_stores_record_only(self) -> None:
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )

        source = _source(self._COLLECTOR_MODULE)
        assert "ToolExecutionRecord" in source
        assert "isinstance(record, ToolExecutionRecord)" in source
        collector = InMemoryToolExecutionCollector()
        with pytest.raises(TypeError):
            collector.on_execution(object())  # type: ignore[arg-type]

    # ---- C18.2 records() 不可变快照 ----
    def test_c18_2_records_snapshot_immutable(self) -> None:
        collector = _collector()
        snapshot = collector.records()
        assert isinstance(snapshot, tuple)
        with pytest.raises(AttributeError):
            snapshot.append(object())  # type: ignore[attr-defined]

    # ---- C18.3 查询结果同样不可变 ----
    def test_c18_3_query_results_immutable(self) -> None:
        collector = _collector()
        for result in (
            collector.records_by_request_id("req-1"),
            collector.records_by_project_id(None),
            collector.records_by_tool_name("get_inventory"),
        ):
            assert isinstance(result, tuple)

    # ---- C18.4 插入顺序 ----
    def test_c18_4_insertion_order(self) -> None:
        collector = _collector()
        first, second = _record_for_c18(round=1), _record_for_c18(round=2)
        collector.on_execution(first)
        collector.on_execution(second)
        assert collector.records() == (first, second)

    # ---- C18.5 不去重 ----
    def test_c18_5_no_deduplication(self) -> None:
        collector = _collector()
        record = _record_for_c18()
        collector.on_execution(record)
        collector.on_execution(record)
        assert collector.records() == (record, record)

    # ---- C18.6 不做聚合 / 统计 ----
    def test_c18_6_no_aggregation_api(self) -> None:
        """无 count / success_rate / 分位数等统计。

        Phase 3.11 Step 20：新增只读 ``max_records``（retention 窗口参数；
        见 C22），仍不含任何统计 API。
        """
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )

        public = {
            name for name in dir(InMemoryToolExecutionCollector)
            if not name.startswith("_")
        }
        assert public == {
            "clear", "max_records", "on_execution", "records",
            "records_by_project_id", "records_by_request_id",
            "records_by_tool_name",
        }, public

    # ---- C18.7 无持久化 ----
    def test_c18_7_no_persistence(self) -> None:
        tree = _tree(self._COLLECTOR_MODULE)
        imports = _walk_imports(tree)
        for forbidden in (
            "sqlalchemy", "backend.app.db", "backend.app.api",
            "kafka", "redis", "celery", "opentelemetry", "httpx",
            "pathlib", "os",
        ):
            assert not any(n.startswith(forbidden) for n in imports), forbidden
        identifiers = _identifiers(tree)
        for forbidden in ("open", "write", "commit", "session", "migrate"):
            assert forbidden not in identifiers, forbidden

    # ---- C18.8 无 DB / LLM / Registry / Handler 依赖 ----
    def test_c18_8_no_execution_dependencies(self) -> None:
        imports = _walk_imports(_tree(self._COLLECTOR_MODULE))
        assert imports <= {
            "__future__", "threading", "collections", "collections.abc",
            "backend.app.services.tool_execution_record",
        }, imports
        identifiers = _identifiers(_tree(self._COLLECTOR_MODULE))
        for forbidden in (
            "registry", "handler", "engine", "llm", "toolchat",
            "orchestrator", "router",
        ):
            assert forbidden not in identifiers, forbidden

    # ---- C18.9 无 Tool 执行能力 ----
    def test_c18_9_no_tool_execution_capability(self) -> None:
        tree = _tree(self._COLLECTOR_MODULE)
        assert _execute_receivers(tree) == set()
        # 代码层（标识符 + 非 docstring 字符串）不得出现第二条执行路径
        haystack = " ".join(
            list(_identifiers(tree)) + _non_docstring_strings(tree)
        )
        assert "execute_and_collect" not in haystack
        assert "registry" not in haystack
        assert "handler" not in haystack

    # ---- C18.10 无全局单例 ----
    def test_c18_10_no_global_singleton(self) -> None:
        tree = _tree(self._COLLECTOR_MODULE)
        for node in tree.body:
            assert not (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
            ), ast.unparse(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert not node.name.startswith(("get_default", "default_"))

    # ---- C18.11 clear() 显式；无 TTL / 后台清理 ----
    def test_c18_11_clear_is_explicit(self) -> None:
        collector = _collector()
        collector.clear()
        assert collector.records() == ()
        source = _source(self._COLLECTOR_MODULE)
        # 只允许 threading.Lock（无 Thread / Timer / schedule / 后台任务）
        assert "threading.Lock()" in source
        assert "threading.Thread" not in source
        identifiers = _identifiers(_tree(self._COLLECTOR_MODULE))
        for forbidden in ("thread", "timer", "sleep", "daemon", "ttl", "schedule"):
            assert forbidden not in identifiers, forbidden

    # ---- C18.12 AIOrchestrator 不创建 Collector ----
    def test_c18_12_orchestrator_does_not_create_collector(self) -> None:
        """Step 16 时点：Orchestrator 未接入观测（C18.12 原断言）。
        Step 18 起：Orchestrator 可以**注入** observer（observation 出口），
        但仍**不创建 / 不持有** Collector（Collector 属调用方 / composition
        root，见 C20.13）；也仍不接触 Metrics。"""
        source = _source(
            "backend/app/services/ai_orchestrator_service.py"
        )
        assert "InMemoryToolExecutionCollector" not in source
        assert "Collector()" not in source          # 不实例化任何 Collector
        assert "tool_execution_metrics_service" not in source
        assert "tool_execution_observer" in source  # Step 18：仅构造参数注入


# ============================================================
# C22. Tool Execution Collector Retention（Phase 3.11 Step 20）
# ============================================================

def _c22_record(
    *,
    request_id: str = "req-c22",
    round: int = 1,
    tool_name: str = "get_inventory",
    project_id: str | None = None,
    success: bool = True,
):
    """C22 用：字段可区分的 Record（验证 FIFO 淘汰语义）。"""
    from datetime import datetime, timedelta, timezone

    from backend.app.services.tool_execution_record import (
        ToolExecutionRecord,
    )

    started = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
    return ToolExecutionRecord(
        request_id=request_id,
        round=round,
        tool_name=tool_name,
        started_at=started,
        finished_at=started + timedelta(milliseconds=1),
        duration_ms=1.0,
        success=success,
        project_id=project_id,
        error_type=None if success else "ToolValidationError",
    )


def _c22_collector(max_records: int):
    from backend.app.services.in_memory_tool_execution_collector import (
        InMemoryToolExecutionCollector,
    )

    return InMemoryToolExecutionCollector(max_records=max_records)


#: Collector 模块路径（与 TestC18 的类属性同值；此处模块级以便 C22 复用）
_COLLECTOR_MODULE = "backend/app/services/in_memory_tool_execution_collector.py"


def _c23_code_layer() -> str:
    """Query Service 代码层文本（imports + 标识符 + 非 docstring 字符串）。

    docstring / 注释里的"禁止 …"说明不算实现（避免文本误判）。
    """
    tree = _tree("backend/app/services/tool_observability_query_service.py")
    return " ".join(
        list(_walk_imports(tree))
        + list(_identifiers(tree))
        + _non_docstring_strings(tree)
    ).lower()


class TestC22CollectorRetention:
    """C22：Collector 的**有限内存窗口**（FIFO）契约。

    行为细节见 ``tests/test_in_memory_tool_execution_collector.py``
    （Retention 测试类）；本类只锁 Contract（结构 / 边界 / 语义）。
    """

    def _bounded(self, max_records: int):
        return _c22_collector(max_records)

    # ---- C22.1 有限 max_records ----

    def test_c22_1_collector_has_finite_max_records(self) -> None:
        from backend.app.services.in_memory_tool_execution_collector import (
            DEFAULT_MAX_RECORDS,
            InMemoryToolExecutionCollector,
        )

        assert isinstance(DEFAULT_MAX_RECORDS, int) and DEFAULT_MAX_RECORDS == 1000
        assert _collector().max_records == DEFAULT_MAX_RECORDS
        assert self._bounded(7).max_records == 7

    # ---- C22.2 参数校验（bool 明确拒绝） ----

    def test_c22_2_max_records_validation(self) -> None:
        for bad in (True, False, 1.5, "100", None):
            with pytest.raises(TypeError):
                self._bounded(bad)  # type: ignore[arg-type]
        for bad in (0, -1):
            with pytest.raises(ValueError):
                self._bounded(bad)

    # ---- C22.3 FIFO 策略 ----

    def test_c22_3_retention_policy_is_fifo(self) -> None:
        collector = self._bounded(3)
        first = _c22_record(request_id="req-A")
        second = _c22_record(request_id="req-B")
        third = _c22_record(request_id="req-C")
        for record in (first, second, third):
            collector.on_execution(record)

        for _ in range(3):                       # 读取不刷新顺序（非 LRU）
            collector.records()

        fourth = _c22_record(request_id="req-D")
        collector.on_execution(fourth)

        assert collector.records() == (second, third, fourth)

    # ---- C22.4 保留最新 N ----

    def test_c22_4_keeps_newest_records(self) -> None:
        collector = self._bounded(2)
        records = [
            _c22_record(request_id=f"req-{round_}")
            for round_ in range(1, 5)
        ]
        for record in records:
            collector.on_execution(record)

        assert collector.records() == tuple(records[-2:])
        assert len(collector.records()) == 2

    # ---- C22.5 淘汰者对所有查询不可见 ----

    def test_c22_5_evicted_unavailable_through_all_queries(self) -> None:
        collector = _c22_collector(1)
        evicted = _c22_record(
            request_id="req-evicted",
            tool_name="get_work_order",
            project_id="project-a",
            success=False,
        )
        kept = _c22_record(request_id="req-kept")
        collector.on_execution(evicted)
        collector.on_execution(kept)

        assert collector.records() == (kept,)
        assert collector.records_by_request_id("req-evicted") == ()
        assert collector.records_by_project_id("project-a") == ()
        assert collector.records_by_tool_name("get_work_order") == ()

    # ---- C22.6 records() 仍为不可变 tuple ----

    def test_c22_6_records_snapshot_is_immutable_tuple(self) -> None:
        collector = self._bounded(1)
        collector.on_execution(_record_for_c18(1))
        snapshot = collector.records()

        assert isinstance(snapshot, tuple)
        with pytest.raises(AttributeError):
            snapshot.append(object())  # type: ignore[attr-defined]

    # ---- C22.7 线程安全（append + 淘汰原子） ----

    def test_c22_7_retention_is_thread_safe(self) -> None:
        import threading

        collector = self._bounded(10)

        def worker() -> None:
            for _ in range(30):
                collector.on_execution(_record_for_c18(1))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        snapshot = collector.records()
        assert len(snapshot) == 10
        assert len({id(record) for record in snapshot}) == 10

    # ---- C22.8 clear() 仍是显式清空 ----

    def test_c22_8_clear_explicitly_clears_retained_records(self) -> None:
        collector = self._bounded(3)
        collector.on_execution(_record_for_c18(1))
        collector.clear()
        assert collector.records() == ()

    # ---- C22.9 无自动 clear ----

    def test_c22_9_no_automatic_clear(self) -> None:
        """无任何执行路径自动 clear（Collector 内部只有 self._records.clear）。"""
        for path in (
            "backend/app/services/ai_orchestrator_service.py",
            "backend/app/services/tool_execution_service.py",
            "backend/app/api/orchestrator_chat.py",
        ):
            tree = _tree(path)
            assert not any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "clear"
                for node in ast.walk(tree)
            ), path

        collector_clears = {
            ast.unparse(node.func)
            for node in ast.walk(_tree(_COLLECTOR_MODULE))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "clear"
        }
        assert collector_clears == {"self._records.clear"}, collector_clears

    # ---- C22.10 无 TTL ----

    def test_c22_10_no_ttl(self) -> None:
        source = _source(_COLLECTOR_MODULE)
        identifiers = _identifiers(_tree(_COLLECTOR_MODULE))
        for forbidden in ("ttl", "timer", "monotonic", "expires", "deadline"):
            assert forbidden not in identifiers, forbidden
        assert "time." not in source

    # ---- C22.11 无后台线程 ----

    def test_c22_11_no_background_thread(self) -> None:
        source = _source(_COLLECTOR_MODULE)
        assert "threading.Lock()" in source
        assert "threading.Thread" not in source
        identifiers = _identifiers(_tree(_COLLECTOR_MODULE))
        for forbidden in ("daemon", "schedule", "create_task", "sleep"):
            assert forbidden not in identifiers, forbidden

    # ---- C22.12 Metrics 只看到保留窗口 ----

    def test_c22_12_metrics_only_sees_retained_records(self) -> None:
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )

        collector = self._bounded(2)
        collector.on_execution(_record_for_c18(1))       # success=True
        collector.on_execution(_record_for_c18(2))
        collector.on_execution(_record_for_c18(3))

        snapshot = ToolExecutionMetricsService.snapshot(collector.records())

        assert snapshot.total_count == 2                 # evicted 不可见
        assert snapshot.success_count == 2

    # ---- C22.13 Record 不可变且从不被修改 ----

    def test_c22_13_record_never_modified_by_retention(self) -> None:
        import dataclasses

        from backend.app.services.tool_execution_record import (
            ToolExecutionRecord,
        )

        collector = self._bounded(1)
        record = _c22_record(request_id="req-immutable")
        before = dataclasses.asdict(record)

        collector.on_execution(record)
        collector.on_execution(_record_for_c18(2))       # 淘汰旧者

        assert dataclasses.asdict(record) == before
        assert isinstance(record, ToolExecutionRecord)
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.success = False  # type: ignore[misc]

    # ---- C22.14 无持久化 ----

    def test_c22_14_no_persistence(self) -> None:
        imports = _walk_imports(_tree(_COLLECTOR_MODULE))
        for forbidden in (
            "json", "pathlib", "os", "redis", "kafka", "celery", "pickle",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden

    # ---- C22.15 无 DB / LLM / Tool 依赖 ----

    def test_c22_15_no_db_llm_or_tool_dependency(self) -> None:
        imports = _walk_imports(_tree(_COLLECTOR_MODULE))
        for forbidden in (
            "sqlalchemy", "backend.app.db", "backend.app.llm",
            "backend.app.tools", "backend.app.api", "httpx",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden

    # ---- C22.16 无 API 变化 ----

    def test_c22_16_no_api_change(self) -> None:
        from backend.app.api.orchestrator_chat import ChatRequest, ChatResponse

        assert set(ChatResponse.model_fields) == {
            "route", "content", "data", "metadata",
        }
        assert "max_records" not in ChatRequest.model_fields
        # 组合根不注入 retention 配置（使用 Collector 默认值；无新配置层）：
        # 构造点无 max_records 关键字参数（AST；docstring 说明不算）
        calls = [
            node
            for node in ast.walk(_tree("backend/app/api/orchestrator_chat.py"))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "InMemoryToolExecutionCollector"
        ]
        assert len(calls) == 1, calls
        assert calls[0].keywords == [], ast.dump(calls[0])[:80]

    # ---- C22.17 ToolResult 不变 ----

    def test_c22_17_tool_result_unchanged(self) -> None:
        import dataclasses

        from backend.app.tools.base import ToolResult

        assert {f.name for f in dataclasses.fields(ToolResult)} == {
            "tool_name", "success", "data", "error",
        }

    # ---- C22.18 AIOrchestrator 执行语义不变 ----

    async def test_c22_18_orchestrator_semantics_unchanged(self) -> None:
        """执行边界语义不变（retention 只是旁路窗口）；Orchestrator / Router
        代码层**无** retention 概念（不感知 max_records / eviction）。"""
        from backend.app.services.tool_execution_context import (
            ToolExecutionContext,
        )
        from backend.app.tools.get_inventory import GET_INVENTORY_DEFINITION
        from backend.app.tools.registry import ToolRegistry, ToolResult

        class _Handler:
            def __init__(self) -> None:
                self.calls = 0

            async def __call__(self, arguments: dict) -> dict:
                self.calls += 1
                return {"qty": 1.0}

        handler = _Handler()
        registry = ToolRegistry()
        registry.register(GET_INVENTORY_DEFINITION, handler)
        collector = _c22_collector(1)
        boundary = ToolExecutionService(registry=registry, observer=collector)

        result = await boundary.execute(
            "get_inventory",
            arguments={"material_code": "MAT-001"},
            context=ToolExecutionContext(request_id="req-c22", round=1),
        )

        assert isinstance(result, ToolResult)
        assert result.success is True
        assert handler.calls == 1
        assert len(collector.records()) == 1

        for path in (
            "backend/app/services/ai_orchestrator_service.py",
            "backend/app/services/ai_router_service.py",
        ):
            tree = _tree(path)
            identifiers = _identifiers(tree)
            strings = " ".join(_non_docstring_strings(tree)).lower()
            for forbidden in ("max_records", "retention", "evict"):
                assert forbidden not in identifiers, (path, forbidden)
                assert forbidden not in strings, (path, forbidden)


# ============================================================
# C23. Tool Observability Read Query Boundary（Phase 3.11 Step 21）
# ============================================================

_C23_QUERY_MODULE = "backend/app/services/tool_observability_query_service.py"


def _c23_query(
    *records, max_records: int = 1000, metrics_service: Any = None,
):
    """C23 用：Collector + Query Service（默认 Metrics Service 或 Fake）。"""
    from backend.app.services.in_memory_tool_execution_collector import (
        InMemoryToolExecutionCollector,
    )
    from backend.app.services.tool_observability_query_service import (
        ToolObservabilityQueryService,
    )

    collector = InMemoryToolExecutionCollector(max_records=max_records)
    for record in records:
        collector.on_execution(record)
    if metrics_service is None:
        query = ToolObservabilityQueryService(collector)
    else:
        query = ToolObservabilityQueryService(
            collector, metrics_service=metrics_service
        )
    return query, collector


class TestC23ReadQueryBoundary:
    """C23：Query Service = **只读 Read Facade**（不存储 / 不算数 / 不清空）。

    行为细节见 ``tests/test_tool_observability_query_service.py``；
    本类只锁 Contract（结构 / 依赖方向 / 语义一致性）。
    """

    # ---- C23.1 只读 ----

    def test_c23_1_query_service_is_read_only(self) -> None:
        from backend.app.services.tool_observability_query_service import (
            ToolObservabilityQueryService,
        )

        public = {
            name
            for name in dir(ToolObservabilityQueryService)
            if not name.startswith("_")
        }
        assert not (public & {
            "clear", "on_execution", "append", "add", "evict", "reset",
            "delete", "pop",
        }), public

    # ---- C23.2 不持有自己的 Record storage ----

    def test_c23_2_no_own_record_storage(self) -> None:
        query, _collector = _c23_query(_c22_record())
        assert set(vars(query)) == {"_collector", "_metrics_service"}

        source = _source(_C23_QUERY_MODULE)
        assert "self._records" not in source          # 无内部 Record 集合
        assert "deque" not in _c23_code_layer()       # 不自建容器（代码层）

    # ---- C23.3 / C23.4 不实现 Retention / FIFO ----

    def test_c23_3_and_4_no_retention_or_fifo(self) -> None:
        identifiers = _identifiers(_tree(_C23_QUERY_MODULE))
        for forbidden in (
            "deque", "max_records", "popleft", "evict", "append", "clear",
            "threading", "lock",
        ):
            assert forbidden not in identifiers, forbidden

    # ---- C23.5 不实现 Metrics arithmetic ----

    def test_c23_5_no_metrics_arithmetic(self) -> None:
        identifiers = _identifiers(_tree(_C23_QUERY_MODULE))
        for forbidden in (
            "sum", "max", "min", "len", "success_count", "failure_count",
            "total_duration_ms", "average_duration_ms", "success_rate",
            "failure_rate",
        ):
            assert forbidden not in identifiers, forbidden

    # ---- C23.6 records 返回不可变 snapshot ----

    def test_c23_6_records_is_immutable_tuple(self) -> None:
        query, _collector = _c23_query(_c22_record())

        snapshot = query.records()

        assert isinstance(snapshot, tuple)
        with pytest.raises(AttributeError):
            snapshot.append(object())  # type: ignore[attr-defined]

    # ---- C23.7 retention window 一致 ----

    def test_c23_7_retention_window_consistent(self) -> None:
        records = [
            _c22_record(request_id="req-A"),
            _c22_record(request_id="req-B"),
            _c22_record(request_id="req-C"),
            _c22_record(request_id="req-D"),
        ]
        query, collector = _c23_query(*records, max_records=3)

        assert collector.records() == tuple(records[-3:])   # Collector 权威
        assert query.records() == tuple(records[-3:])       # Query 完全一致
        assert query.records_by_request_id("req-A") == ()   # 已淘汰：不可恢复

    # ---- C23.8 query 不修改 Collector ----

    def test_c23_8_query_does_not_mutate_collector(self) -> None:
        query, collector = _c23_query(
            _c22_record(request_id="req-A"),
            _c22_record(request_id="req-B"),
        )
        before = collector.records()

        query.records()
        query.records_by_request_id("req-A")
        query.records_by_project_id(None)
        query.records_by_tool_name("get_inventory")
        query.metrics()

        assert collector.records() == before
        assert len(collector.records()) == 2

    # ---- C23.9 不暴露 clear ----

    def test_c23_9_no_clear_exposure(self) -> None:
        query, _collector = _c23_query(_c22_record())
        assert not hasattr(query, "clear")
        tree = _tree(_C23_QUERY_MODULE)
        assert not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "clear"
            for node in ast.walk(tree)
        )
        assert "clear" not in _c23_code_layer()        # 无 clear 方法 / 调用

    # ---- C23.10 ~ C23.13 不依赖 DB / LLM / Registry / API ----

    def test_c23_10_to_13_no_forbidden_dependencies(self) -> None:
        imports = _walk_imports(_tree(_C23_QUERY_MODULE))
        for forbidden in (
            "sqlalchemy", "psycopg", "backend.app.db", "backend.app.llm",
            "backend.app.tools", "backend.app.api", "redis", "kafka",
            "celery", "httpx", "requests", "os", "pathlib", "subprocess",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden
        assert "backend.app.api" not in imports

    # ---- C23.14 不暴露 secrets / SQL / arguments ----

    def test_c23_14_no_sensitive_output(self) -> None:
        query, _collector = _c23_query(
            _c22_record(request_id="req-A"),
            _c22_record(request_id="req-B", success=False),
        )

        blob = repr(query.records()) + repr(query.metrics())
        for forbidden in (
            "arguments", "material_code", "SELECT", "postgresql://",
            "password", "Authorization", "Bearer ", "api_key", "Traceback",
        ):
            assert forbidden not in blob, forbidden

    # ---- C23.15 Metrics 复用现有 Metrics Service ----

    def test_c23_15_metrics_reuses_existing_service(self) -> None:
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )
        from backend.app.services.tool_observability_query_service import (
            ToolObservabilityQueryService,
        )

        class _FakeMetrics:
            def __init__(self) -> None:
                self.calls = 0

            def snapshot(self, records: Any) -> str:  # type: ignore[valid-type]
                self.calls += 1
                return "FAKE-SNAPSHOT"

        fake = _FakeMetrics()
        query, collector = _c23_query(
            _c22_record(), _c22_record(request_id="req-B"),
            metrics_service=fake,
        )

        assert query.metrics() == "FAKE-SNAPSHOT"     # 返回值来自 Metrics Service
        assert fake.calls == 1
        assert _c23_query()[0].metrics_service is ToolExecutionMetricsService
        assert issubclass(type(query), ToolObservabilityQueryService)
        assert query.metrics_service is fake
        assert collector.records() is not None


# ============================================================
# C24. Tool Observability Snapshot Read Model（Phase 3.11 Step 22）
# ============================================================

_C24_SNAPSHOT_MODULE = "backend/app/services/tool_observability_snapshot.py"


class TestC24SnapshotReadModel:
    """C24：``ToolExecutionSnapshot`` = 对外 Read Model（与内部 Record 解耦）。

    行为细节见 ``tests/test_tool_observability_snapshot.py``；
    本类只锁 Contract（结构 / 解耦 / 安全 / 语义一致性）。
    """

    def _snapshot(self):
        from backend.app.services.tool_observability_snapshot import (
            ToolExecutionSnapshot,
        )

        return ToolExecutionSnapshot.from_record(_c22_record())

    # ---- C24.1 独立 DTO ----

    def test_c24_1_snapshot_is_independent_dto(self) -> None:
        from backend.app.services.tool_execution_record import (
            ToolExecutionRecord,
        )
        from backend.app.services.tool_observability_snapshot import (
            ToolExecutionSnapshot,
        )

        snapshot = self._snapshot()

        assert isinstance(snapshot, ToolExecutionSnapshot)
        assert not isinstance(snapshot, ToolExecutionRecord)
        assert ToolExecutionSnapshot is not ToolExecutionRecord

    # ---- C24.2 immutable ----

    def test_c24_2_snapshot_is_frozen(self) -> None:
        import dataclasses

        snapshot = self._snapshot()

        with pytest.raises(dataclasses.FrozenInstanceError):
            snapshot.tool_name = "other"  # type: ignore[misc]

    # ---- C24.3 ~ C24.5 不持有 Record / Collector / Metrics Service ----

    def test_c24_3_to_5_holds_no_external_objects(self) -> None:
        snapshot = self._snapshot()

        assert set(vars(snapshot)) == {
            "request_id", "round", "tool_name", "started_at", "finished_at",
            "duration_ms", "success", "project_id", "tool_call_id",
            "error_code", "error_type",
        }
        for forbidden in ("record", "collector", "metrics_service"):
            assert not hasattr(snapshot, forbidden), forbidden

    # ---- C24.6 显式字段映射 ----

    def test_c24_6_explicit_field_mapping(self) -> None:
        from_record = next(
            node
            for node in ast.walk(_tree(_C24_SNAPSHOT_MODULE))
            if isinstance(node, ast.FunctionDef)
            and node.name == "from_record"
        )
        mapping = ast.unparse(from_record)

        for field_name in (
            "request_id", "round", "tool_name", "started_at", "finished_at",
            "duration_ms", "success", "project_id", "tool_call_id",
            "error_code", "error_type",
        ):
            assert f"{field_name}=record.{field_name}" in mapping, field_name

    # ---- C24.7 不自动泄露字段 ----

    def test_c24_7_no_automatic_field_leak(self) -> None:
        tree = _tree(_C24_SNAPSHOT_MODULE)
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
        }
        assert not (calls & {"vars", "asdict"}), calls
        attributes = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        assert "__dict__" not in attributes

    # ---- C24.8 ~ C24.10 无 arguments / SQL / secrets ----

    def test_c24_8_to_10_no_sensitive_content(self) -> None:
        import dataclasses

        query, _collector = _c23_query(
            _c22_record(request_id="req-A"),
            _c22_record(request_id="req-B", success=False),
        )
        names = {
            field.name
            for field in dataclasses.fields(type(self._snapshot()))
        }
        assert not (names & {
            "arguments", "sql", "prompt", "api_key", "password",
            "authorization", "traceback",
        })
        blob = repr(query.snapshots())
        for forbidden in (
            "arguments", "SELECT", "postgresql://", "password",
            "Authorization", "Bearer ", "api_key", "Traceback",
        ):
            assert forbidden not in blob, forbidden

    # ---- C24.11 retention 一致 ----

    def test_c24_11_retention_window_consistent(self) -> None:
        records = [
            _c22_record(request_id="req-A"),
            _c22_record(request_id="req-B"),
            _c22_record(request_id="req-C"),
            _c22_record(request_id="req-D"),
        ]
        query, collector = _c23_query(*records, max_records=3)

        assert [s.request_id for s in query.snapshots()] == [
            r.request_id for r in collector.records()
        ] == ["req-B", "req-C", "req-D"]
        assert query.snapshots_by_request_id("req-A") == ()

    # ---- C24.12 旧快照不随 Collector 变化 ----

    def test_c24_12_old_snapshot_is_stable(self) -> None:
        query, collector = _c23_query(_c22_record(request_id="req-A"))

        snapshots = query.snapshots()
        collector.on_execution(_c22_record(request_id="req-B"))

        assert [s.request_id for s in snapshots] == ["req-A"]
        assert len(query.snapshots()) == 2

    # ---- C24.13 Metrics Service 不变 ----

    def test_c24_13_metrics_service_unchanged(self) -> None:
        import inspect

        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )

        methods = {
            name
            for name, _ in inspect.getmembers(
                ToolExecutionMetricsService, inspect.isfunction
            )
            if not name.startswith("_")
        }
        assert methods == {"snapshot"}, methods

    # ---- C24.14 Query Service 原有 API 不变 ----

    def test_c24_14_query_service_original_api_unchanged(self) -> None:
        from backend.app.services.tool_observability_query_service import (
            ToolObservabilityQueryService,
        )

        public = {
            name
            for name in dir(ToolObservabilityQueryService)
            if not name.startswith("_")
        }
        assert public == {
            "collector", "metrics", "metrics_service",
            "records", "records_by_project_id", "records_by_request_id",
            "records_by_tool_name",
            "snapshots", "snapshots_by_project_id",
            "snapshots_by_request_id", "snapshots_by_tool_name",
        }, public

    # ---- C24.15 Snapshot 查询只读 ----

    def test_c24_15_snapshot_query_is_read_only(self) -> None:
        query, collector = _c23_query(
            _c22_record(request_id="req-A"),
            _c22_record(request_id="req-B"),
        )
        before = collector.records()

        query.snapshots()
        query.snapshots_by_request_id("req-A")
        query.snapshots_by_project_id(None)
        query.snapshots_by_tool_name("get_inventory")

        assert collector.records() == before
        assert not hasattr(query, "clear")


# ============================================================
# C19. Tool Execution Metrics Read Model（Phase 3.11 Step 17）
# ============================================================

class TestC19ToolExecutionMetricsReadModel:
    """C19：Metrics 是**只读** Read Model（不改执行链 / 不带维度 / 无状态）。

        C19.1  接收 Records（不是 Collector internals）
        C19.2  Snapshot 是 frozen dataclass
        C19.3  不重新测量时间（只读 record.duration_ms）
        C19.4  total_count = success_count + failure_count
        C19.5  空数据集 → rates / average / max 为 None（不是 0）
        C19.6  duration 来自 Record.duration_ms
        C19.7  无 request / project / tool 维度
        C19.8  无敏感字段（arguments / result data / error / SQL）
        C19.9  无 DB / LLM / Tool execution
        C19.10 Deterministic
        C19.11 无持久化
        C19.12 无全局单例
        （附加）生产执行链未接线 —— Metrics 失败不可能影响 ToolResult

    行为细节见 ``tests/test_tool_execution_metrics_service.py``；
    本类只锁 Contract（结构 / 依赖方向 / 边界）。
    """

    _METRICS_MODULE = "backend/app/services/tool_execution_metrics_service.py"

    _ALLOWED_FIELDS = frozenset({
        "total_count",
        "success_count",
        "failure_count",
        "success_rate",
        "failure_rate",
        "total_duration_ms",
        "average_duration_ms",
        "max_duration_ms",
    })

    # ---- helpers ----

    @staticmethod
    def _record(duration_ms: float = 1.0, success: bool = True):
        from datetime import datetime, timedelta, timezone

        from backend.app.services.tool_execution_record import (
            ToolExecutionRecord,
        )

        started = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
        return ToolExecutionRecord(
            request_id="req-c19",
            round=1,
            tool_name="get_inventory",
            started_at=started,
            finished_at=started + timedelta(milliseconds=duration_ms),
            duration_ms=duration_ms,
            success=success,
        )

    # ---- C19.1 accepts Records, not Collector internals ----

    def test_c19_1_accepts_records_not_collector_internals(self) -> None:
        from backend.app.services.in_memory_tool_execution_collector import (
            InMemoryToolExecutionCollector,
        )
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )

        signature = inspect.signature(ToolExecutionMetricsService.snapshot)
        assert list(signature.parameters) == ["records"]
        with pytest.raises(TypeError):
            ToolExecutionMetricsService.snapshot(
                InMemoryToolExecutionCollector()
            )
        identifiers = _identifiers(_tree(self._METRICS_MODULE))
        assert "collector" not in identifiers
        assert "_records" not in identifiers
        assert "session" not in identifiers

    # ---- C19.2 snapshot is frozen ----

    def test_c19_2_snapshot_is_frozen(self) -> None:
        import dataclasses

        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
            ToolExecutionMetricsSnapshot,
        )

        assert ToolExecutionMetricsSnapshot.__dataclass_params__.frozen is True
        snapshot = ToolExecutionMetricsService.snapshot((self._record(),))
        with pytest.raises(dataclasses.FrozenInstanceError):
            snapshot.total_count = 999  # type: ignore[misc]

    # ---- C19.3 no timing re-measurement ----

    def test_c19_3_no_timing_re_measurement(self) -> None:
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )

        tree = _tree(self._METRICS_MODULE)
        identifiers = _identifiers(tree)
        for forbidden in ("perf_counter", "monotonic", "now", "random", "sleep"):
            assert forbidden not in identifiers, forbidden
        imports = _walk_imports(tree)
        assert "time" not in imports
        assert "datetime" not in imports
        # 时长可由 record.duration_ms 精确复现（无二次测量）
        snapshot = ToolExecutionMetricsService.snapshot((
            self._record(duration_ms=7.5),
            self._record(duration_ms=2.25, success=False),
        ))
        assert snapshot.total_duration_ms == 9.75
        assert snapshot.max_duration_ms == 7.5

    # ---- C19.4 total = success + failure ----

    def test_c19_4_total_equals_success_plus_failure(self) -> None:
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )

        snapshot = ToolExecutionMetricsService.snapshot((
            self._record(),
            self._record(success=False),
            self._record(),
        ))
        assert snapshot.total_count == 3
        assert (
            snapshot.success_count + snapshot.failure_count
            == snapshot.total_count
        )

    # ---- C19.5 empty dataset uses None rates ----

    def test_c19_5_empty_dataset_uses_none_rates(self) -> None:
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )

        snapshot = ToolExecutionMetricsService.snapshot(())
        assert snapshot.total_count == 0
        assert snapshot.success_rate is None
        assert snapshot.failure_rate is None
        assert snapshot.average_duration_ms is None
        assert snapshot.max_duration_ms is None
        assert snapshot.total_duration_ms == 0.0

    # ---- C19.6 duration uses Record.duration_ms ----

    def test_c19_6_duration_comes_from_record(self) -> None:
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )

        snapshot = ToolExecutionMetricsService.snapshot((
            self._record(duration_ms=4.0),
            self._record(duration_ms=6.0),
        ))
        assert snapshot.total_duration_ms == 10.0
        assert snapshot.average_duration_ms == 5.0
        assert snapshot.max_duration_ms == 6.0

    # ---- C19.7 no dimensions ----

    def test_c19_7_no_request_project_tool_dimensions(self) -> None:
        import dataclasses

        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
            ToolExecutionMetricsSnapshot,
        )

        assert {
            f.name for f in dataclasses.fields(ToolExecutionMetricsSnapshot)
        } == self._ALLOWED_FIELDS
        for forbidden in (
            "by_tool", "by_project", "by_request", "top_slowest_tools",
            "failure_by_tool", "success_rate_by_project", "group_by",
        ):
            assert not hasattr(ToolExecutionMetricsService, forbidden)
            assert not hasattr(ToolExecutionMetricsSnapshot, forbidden)
        identifiers = _identifiers(_tree(self._METRICS_MODULE))
        for forbidden in ("request_id", "project_id", "tool_name", "round"):
            assert forbidden not in identifiers, forbidden

    # ---- C19.8 no sensitive fields ----

    def test_c19_8_no_sensitive_fields(self) -> None:
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )

        snapshot = ToolExecutionMetricsService.snapshot((
            self._record(),
            self._record(success=False),
        ))
        text = repr(snapshot)
        for forbidden in (
            "request_id", "project_id", "tool_name", "arguments",
            "result", "sql", "error", "MAT-001", "password",
            "DATABASE_URL", "Authorization",
        ):
            assert forbidden not in text, forbidden
        snapshot.assert_field_whitelist()

    # ---- C19.9 no DB / LLM / Tool execution ----

    def test_c19_9_no_execution_capability(self) -> None:
        tree = _tree(self._METRICS_MODULE)
        imports = _walk_imports(tree)
        for forbidden in (
            "sqlalchemy",
            "backend.app.db",
            "backend.app.api",
            "backend.app.llm",
            "backend.app.tools",
            "backend.app.projects",
            "backend.app.services.tool_execution_service",
            "backend.app.services.tool_chat_service",
            "httpx",
            "redis",
        ):
            assert not any(
                name.startswith(forbidden) for name in imports
            ), forbidden
        identifiers = _identifiers(tree)
        for forbidden in (
            "execute", "registry", "handler", "engine", "session", "connect",
        ):
            assert forbidden not in identifiers, forbidden

    # ---- C19.10 deterministic ----

    def test_c19_10_deterministic(self) -> None:
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )

        records = (
            self._record(duration_ms=3.5),
            self._record(duration_ms=1.5, success=False),
        )
        assert ToolExecutionMetricsService.snapshot(
            records
        ) == ToolExecutionMetricsService.snapshot(records)
        assert ToolExecutionMetricsService.snapshot(
            list(records)
        ) == ToolExecutionMetricsService.snapshot(tuple(records))

    # ---- C19.11 no persistence ----

    def test_c19_11_no_persistence(self) -> None:
        tree = _tree(self._METRICS_MODULE)
        imports = _walk_imports(tree)
        for forbidden in (
            "json", "pathlib", "os", "redis", "kafka", "celery", "pickle",
            "shelve", "sqlite3",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden
        identifiers = _identifiers(tree)
        for forbidden in ("open", "dump", "save", "write", "flush", "commit"):
            assert forbidden not in identifiers, forbidden

    # ---- C19.12 no global singleton ----

    def test_c19_12_no_global_singleton(self) -> None:
        tree = _tree(self._METRICS_MODULE)
        assert {
            node.name for node in tree.body if isinstance(node, ast.ClassDef)
        } == {
            "ToolExecutionMetricsSnapshot",
            "ToolExecutionMetricsService",
        }
        for node in tree.body:
            if isinstance(node, ast.AnnAssign):
                value = node.value
                if isinstance(value, ast.Call) and isinstance(
                    value.func, ast.Name
                ):
                    assert value.func.id not in {
                        "ToolExecutionMetricsSnapshot",
                        "ToolExecutionMetricsService",
                    }, ast.dump(node)[:80]
        identifiers = _identifiers(tree)
        assert "get_default" not in identifiers
        assert "singleton" not in identifiers
        service_cls = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ToolExecutionMetricsService"
        )
        methods = [
            node.name
            for node in service_cls.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        assert methods == ["snapshot"]      # 无 __init__（无实例状态）

    # ---- 附加：生产执行链未接线 ----

    def test_production_chain_not_wired_to_metrics(self) -> None:
        """Metrics 是 Read Model：执行链 / API / Orchestrator 均不依赖它
        （Metrics 计算失败不可能变成 ToolResult(success=False) / retry）。"""
        for path in (
            _EXECUTION_MODULE,
            _SERVICE_MODULE,
            _API_MODULE,
            "backend/app/services/tool_execution_record.py",
            "backend/app/services/tool_execution_observer.py",
            "backend/app/services/in_memory_tool_execution_collector.py",
            "backend/app/services/ai_orchestrator_service.py",
        ):
            source = _source(path)
            assert "ToolExecutionMetrics" not in source, path
            assert "metrics_service" not in source, path
            assert "tool_execution_metrics_service" not in source, path


def _collector():
    from backend.app.services.in_memory_tool_execution_collector import (
        InMemoryToolExecutionCollector,
    )

    return InMemoryToolExecutionCollector()


def _record_for_c18(round: int = 1):
    from datetime import datetime, timedelta, timezone

    from backend.app.services.tool_execution_record import (
        ToolExecutionRecord,
    )

    started = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)
    return ToolExecutionRecord(
        request_id="req-1",
        round=round,
        tool_name="get_inventory",
        started_at=started,
        finished_at=started + timedelta(milliseconds=1),
        duration_ms=1.0,
        success=True,
    )


class _RecordingObserver:
    """C17 用：内存 Recording Observer（仅测试；生产代码无此实现）。"""

    def __init__(self) -> None:
        self.records: list[Any] = []

    def on_execution(self, record: Any) -> None:
        self.records.append(record)


class _CountingExecution(ToolExecutionService):
    """C14 用：真实执行边界 + 调用记录（Phase 3.11 Step 12 同款）。

    Phase 3.11 Step 13：接受并记录 ``context``（C15 复用同一替身）。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls: list[tuple[str, dict | None]] = []
        self.contexts: list[Any] = []

    async def execute(  # type: ignore[override]
        self,
        tool_name: str,
        *,
        arguments: dict | None = None,
        context: Any = None,
    ) -> Any:
        self.calls.append((tool_name, arguments))
        self.contexts.append(context)
        return await super().execute(
            tool_name, arguments=arguments, context=context
        )


# ============================================================
# C26. Tool Observability Serialization Boundary（Phase 3.11 Step 24）
# ============================================================

_C26_SERIALIZATION_MODULE = (
    "backend/app/services/tool_observability_serialization.py"
)


class TestC26SerializationBoundary:
    """C26：Read Model → JSON-safe dict 的转换边界（只读 / 纯函数 / 不改语义）。

        C26.1  Snapshot → JSON-safe dict
        C26.2  Metrics → JSON-safe dict
        C26.3  Explicit field mapping
        C26.4  不使用 vars / asdict / __dict__
        C26.5  datetime → ISO 8601
        C26.6  timezone preserved
        C26.7  None 语义保留（不写成 0）
        C26.8  no extra fields
        C26.9  no forbidden fields
        C26.10 no mutation
        C26.11 deterministic
        C26.12 no DB
        C26.13 no LLM
        C26.14 no Tool execution
        C26.15 no HTTP API

    行为细节见 ``tests/test_tool_observability_serialization.py``；
    本类只锁 Contract（结构 / 依赖方向 / 边界）。
    """

    _EXPECTED_SNAPSHOT_FIELDS = (
        "request_id", "round", "tool_name", "started_at", "finished_at",
        "duration_ms", "success", "project_id", "tool_call_id",
        "error_code", "error_type",
    )
    _EXPECTED_METRICS_FIELDS = (
        "total_count", "success_count", "failure_count", "success_rate",
        "failure_rate", "total_duration_ms", "average_duration_ms",
        "max_duration_ms",
    )

    def _snapshot(self):
        from backend.app.services.tool_observability_snapshot import (
            ToolExecutionSnapshot,
        )

        return ToolExecutionSnapshot.from_record(_c22_record())

    def _metrics(self):
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )

        return ToolExecutionMetricsService.snapshot(
            (_c22_record(), _c22_record(success=False))
        )

    # ---- C26.1 / C26.2 JSON-safe dict ----

    def test_c26_1_and_2_json_safe_dicts(self) -> None:
        import json

        from backend.app.services.tool_observability_serialization import (
            metrics_to_dict,
            snapshot_to_dict,
        )

        snapshot_dict = snapshot_to_dict(self._snapshot())
        metrics_dict = metrics_to_dict(self._metrics())

        for serialized in (snapshot_dict, metrics_dict):
            assert isinstance(serialized, dict)
            for value in serialized.values():
                assert isinstance(
                    value, (str, int, float, bool, type(None))
                ), value
            json.dumps(serialized)

    # ---- C26.3 explicit field mapping ----

    def test_c26_3_explicit_field_mapping(self) -> None:
        source = _source(_C26_SERIALIZATION_MODULE)

        for field_name in self._EXPECTED_SNAPSHOT_FIELDS:
            assert f"snapshot.{field_name}" in source, field_name
        for field_name in self._EXPECTED_METRICS_FIELDS:
            assert f"metrics.{field_name}" in source, field_name

    # ---- C26.4 no automatic serialization ----

    def test_c26_4_no_automatic_serialization(self) -> None:
        tree = _tree(_C26_SERIALIZATION_MODULE)
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not (calls & {"vars", "asdict"}), calls
        identifiers = _identifiers(tree)
        assert "__dict__" not in identifiers
        assert "model_dump" not in identifiers

    # ---- C26.5 / C26.6 datetime → ISO 8601 + timezone ----

    def test_c26_5_and_6_datetime_iso8601_with_timezone(self) -> None:
        from backend.app.services.tool_observability_serialization import (
            snapshot_to_dict,
        )

        result = snapshot_to_dict(self._snapshot())

        assert isinstance(result["started_at"], str)
        assert result["started_at"].endswith("+00:00")
        assert result["started_at"] == (
            self._snapshot().started_at.isoformat()
        )
        assert "T" in result["finished_at"]

    # ---- C26.7 None semantics ----

    def test_c26_7_none_semantics_preserved(self) -> None:
        from backend.app.services.tool_execution_metrics_service import (
            ToolExecutionMetricsService,
        )
        from backend.app.services.tool_observability_serialization import (
            metrics_to_dict,
        )

        empty = metrics_to_dict(ToolExecutionMetricsService.snapshot(()))

        assert empty["success_rate"] is None
        assert empty["failure_rate"] is None
        assert empty["average_duration_ms"] is None
        assert empty["max_duration_ms"] is None
        assert empty["total_duration_ms"] == 0.0

    # ---- C26.8 no extra fields ----

    def test_c26_8_no_extra_fields(self) -> None:
        from backend.app.services.tool_observability_serialization import (
            metrics_to_dict,
            snapshot_to_dict,
        )

        assert set(snapshot_to_dict(self._snapshot())) == set(
            self._EXPECTED_SNAPSHOT_FIELDS
        )
        assert set(metrics_to_dict(self._metrics())) == set(
            self._EXPECTED_METRICS_FIELDS
        )

    # ---- C26.9 no forbidden fields ----

    def test_c26_9_no_forbidden_fields(self) -> None:
        import json

        from backend.app.services.tool_observability_serialization import (
            metrics_to_dict,
            snapshot_to_dict,
        )

        blob = json.dumps(
            [snapshot_to_dict(self._snapshot()), metrics_to_dict(self._metrics())],
            ensure_ascii=False,
        )
        for forbidden in (
            "arguments", "sql", "result", "prompt", "password", "api_key",
            "Authorization", "Traceback", "postgresql://", "SELECT",
        ):
            assert forbidden not in blob, forbidden

    # ---- C26.10 no mutation ----

    def test_c26_10_no_mutation(self) -> None:
        from backend.app.services.tool_observability_serialization import (
            metrics_to_dict,
            snapshot_to_dict,
        )

        snapshot = self._snapshot()
        metrics = self._metrics()
        snapshot_before, metrics_before = repr(snapshot), repr(metrics)

        snapshot_to_dict(snapshot)
        metrics_to_dict(metrics)

        assert repr(snapshot) == snapshot_before
        assert repr(metrics) == metrics_before

    # ---- C26.11 deterministic ----

    def test_c26_11_deterministic(self) -> None:
        from backend.app.services.tool_observability_serialization import (
            metrics_to_dict,
            snapshot_to_dict,
        )

        snapshot = self._snapshot()
        metrics = self._metrics()

        assert snapshot_to_dict(snapshot) == snapshot_to_dict(snapshot)
        assert metrics_to_dict(metrics) == metrics_to_dict(metrics)

    # ---- C26.12 ~ C26.14 无 DB / LLM / Tool 执行 ----

    def test_c26_12_to_14_no_db_llm_or_tool_execution(self) -> None:
        tree = _tree(_C26_SERIALIZATION_MODULE)
        imports = _walk_imports(tree)
        for forbidden in (
            "sqlalchemy", "psycopg", "redis", "kafka", "celery", "httpx",
            "requests", "json", "pathlib", "os", "subprocess",
            "backend.app.db", "backend.app.llm", "backend.app.tools",
            "backend.app.api",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden
        identifiers = _identifiers(tree)
        for forbidden in (
            "execute", "registry", "handler", "session", "engine", "commit",
            "now", "perf_counter", "random", "uuid", "open",
        ):
            assert forbidden not in identifiers, forbidden

    # ---- C26.15 无 HTTP API ----

    def test_c26_15_no_http_api(self) -> None:
        tree = _tree(_C26_SERIALIZATION_MODULE)
        imports = _walk_imports(tree)
        assert not any(
            name.startswith("fastapi") for name in imports
        ), imports
        identifiers = _identifiers(tree)
        for forbidden in (
            "apireuter", "fastapi", "response", "request", "depends",
            "status_code", "endpoint",
        ):
            assert forbidden not in identifiers, forbidden

        api_dir = os.path.join(REPO_ROOT, "backend", "app", "api")
        for filename in sorted(os.listdir(api_dir)):
            if not filename.endswith(".py"):
                continue
            if filename == "tool_observability.py":
                # Step 25：唯一被允许的序列化边界消费方（Read API）
                continue
            with open(
                os.path.join(api_dir, filename), encoding="utf-8"
            ) as handle:
                api_source = handle.read()
            assert (
                "tool_observability_serialization" not in api_source
            ), filename
            assert "snapshot_to_dict" not in api_source, filename


# ============================================================
# C27. Tool Observability HTTP Read Boundary（Phase 3.11 Step 25）
# ============================================================

_C27_API_MODULE = "backend/app/api/tool_observability.py"


class TestC27HttpReadBoundary:
    """C27：Tool Observability **只读** HTTP 边界。

        C27.1  GET records 只读                C27.2  GET metrics 只读
        C27.3  API → QueryService only         C27.4  API ↛ Collector
        C27.5  API ↛ ToolExecutionService      C27.6  API ↛ ToolRegistry
        C27.7  API ↛ Tool Handler              C27.8  使用 Snapshot Read Model
        C27.9  使用 Serialization Boundary     C27.10 API 不计算 Metrics
        C27.11 API 不创建 Collector            C27.12 使用 Application Collector
        C27.13 GET 不触发 Tool 执行            C27.14~16 无 DB / LLM / 持久化
        C27.17 响应无凭据                      C27.18 响应无 Tool args/result
        C27.19 Metrics 无 identifier           C27.20 无 Query DSL

    行为细节见 ``tests/test_tool_observability_api.py``；
    本类只锁 Contract（结构 / 依赖方向 / 边界）。
    """

    @staticmethod
    def _record(
        *,
        request_id: str = "req-c27",
        success: bool = True,
        duration_ms: float = 5.0,
        project_id: str | None = "project-a",
    ):
        from datetime import datetime, timedelta, timezone

        from backend.app.services.tool_execution_record import (
            ToolExecutionRecord,
        )

        started = datetime(2026, 9, 26, 10, 20, 30, tzinfo=timezone.utc)
        return ToolExecutionRecord(
            request_id=request_id,
            round=1,
            tool_name="get_inventory",
            started_at=started,
            finished_at=started + timedelta(milliseconds=duration_ms),
            duration_ms=duration_ms,
            success=success,
            project_id=project_id,
            error_type=None if success else "ToolValidationError",
        )

    @classmethod
    def _client(cls):
        from fastapi.testclient import TestClient

        from backend.app.api import orchestrator_chat as root
        from backend.app.main import app

        root._TOOL_EXECUTION_COLLECTOR.clear()
        return TestClient(app), root

    # ---- C27.1 / C27.2 read-only ----

    def test_c27_1_and_2_only_get_endpoints(self) -> None:
        tree = _tree(_C27_API_MODULE)
        methods = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "router"
        }
        assert methods == {"get"}, methods
        for forbidden in ("post", "put", "delete", "patch"):
            assert forbidden not in methods, forbidden

    def test_c27_1_and_2_get_does_not_change_collector_state(self) -> None:
        client, root = self._client()
        try:
            root._TOOL_EXECUTION_COLLECTOR.on_execution(self._record())
            before = root._TOOL_EXECUTION_COLLECTOR.records()

            client.get("/api/observability/tools")
            client.get("/api/observability/tools/metrics")

            assert root._TOOL_EXECUTION_COLLECTOR.records() == before
        finally:
            root._TOOL_EXECUTION_COLLECTOR.clear()

    # ---- C27.3 ~ C27.7 依赖方向 ----

    def test_c27_3_to_7_dependency_direction(self) -> None:
        imports = _walk_imports(_tree(_C27_API_MODULE))
        for forbidden in (
            "backend.app.services.in_memory_tool_execution_collector",
            "backend.app.services.tool_execution_record",
            "backend.app.services.tool_execution_service",
            "backend.app.services.tool_execution_observer",
            "backend.app.services.ai_orchestrator_service",
            "backend.app.tools",
            "backend.app.db",
            "backend.app.llm",
            "sqlalchemy",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden
        # 唯一数据来源 = Composition Root 的 QueryService accessor
        assert (
            "backend.app.api.orchestrator_chat" in imports
            or "get_tool_observability_query_service"
            in _identifiers(_tree(_C27_API_MODULE))
        )

    # ---- C27.8 / C27.9 Read Model + Serialization ----

    def test_c27_8_and_9_read_model_and_serialization(self) -> None:
        tree = _tree(_C27_API_MODULE)
        identifiers = _identifiers(tree)
        for required in ("snapshots", "snapshot_to_dict", "metrics_to_dict"):
            assert required in identifiers, required
        for forbidden in ("isoformat", "asdict", "vars"):
            assert forbidden not in identifiers, forbidden
        # 不绕过 Read Model 直取 Record：无 ``query.records()`` 调用
        assert not [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "records"
            and isinstance(node.value, ast.Name)
        ]

    # ---- C27.10 / C27.20 不计算 / 无 DSL ----

    def test_c27_10_api_does_not_calculate_metrics(self) -> None:
        tree = _tree(_C27_API_MODULE)
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp):
                assert not isinstance(node.op, (ast.Div, ast.FloorDiv))
        identifiers = _identifiers(tree)
        for forbidden in ("sum", "max", "min", "mean", "statistics", "round"):
            assert forbidden not in identifiers, forbidden

    def test_c27_20_no_query_dsl(self) -> None:
        tree = _tree(_C27_API_MODULE)
        params = [
            arg.arg
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for arg in node.args.args
        ]
        assert params == [], params
        assert not [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "fastapi"
            and any(alias.name == "Query" for alias in node.names)
        ]
        identifiers = _identifiers(tree)
        for forbidden in ("params", "limit", "offset", "sort", "filter"):
            assert forbidden not in identifiers, forbidden

    # ---- C27.11 / C27.12 Collector ----

    def test_c27_11_api_does_not_create_collector(self) -> None:
        tree = _tree(_C27_API_MODULE)
        identifiers = _identifiers(tree)          # 不含 docstring 文本
        assert "inmemorytoolexecutioncollector" not in identifiers
        assert not [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.endswith("Collector")
        ]

    def test_c27_12_uses_application_collector(self) -> None:
        from backend.app.api import orchestrator_chat as root
        from backend.app.api import tool_observability as api

        query = api.get_tool_observability_query_service()

        assert query.collector is root._TOOL_EXECUTION_COLLECTOR

    # ---- C27.13 GET 不触发 Tool 执行 ----

    def test_c27_13_get_does_not_execute_tool(self) -> None:
        client, root = self._client()
        try:
            payload = client.get("/api/observability/tools").json()
            assert payload == {"records": []}
            assert root._TOOL_EXECUTION_COLLECTOR.records() == ()
        finally:
            root._TOOL_EXECUTION_COLLECTOR.clear()

    # ---- C27.14 ~ C27.16 无 DB / LLM / 持久化 ----

    def test_c27_14_to_16_no_db_llm_or_persistence(self) -> None:
        tree = _tree(_C27_API_MODULE)
        imports = _walk_imports(tree)
        for forbidden in (
            "sqlalchemy", "psycopg", "redis", "kafka", "celery",
            "backend.app.db", "backend.app.llm", "pathlib", "pickle",
        ):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imports
            ), forbidden
        identifiers = _identifiers(tree)
        for forbidden in (
            "session", "engine", "commit", "open", "dump", "save", "persist",
        ):
            assert forbidden not in identifiers, forbidden

    # ---- C27.17 ~ C27.19 响应安全 ----

    def test_c27_17_to_19_response_security(self) -> None:
        client, root = self._client()
        try:
            root._TOOL_EXECUTION_COLLECTOR.on_execution(
                self._record(request_id="req-secret-1", project_id="project-a")
            )
            root._TOOL_EXECUTION_COLLECTOR.on_execution(
                self._record(request_id="req-secret-2", success=False)
            )
            records_text = client.get("/api/observability/tools").text
            metrics_text = client.get("/api/observability/tools/metrics").text
            metrics_payload = client.get(
                "/api/observability/tools/metrics"
            ).json()

            for forbidden in (
                "SELECT", "postgresql://", "password", "api_key",
                "Authorization", "Bearer ", "Traceback", "MAT-001",
                "arguments", "result",
            ):
                assert forbidden not in records_text, forbidden
                assert forbidden not in metrics_text, forbidden
            for key in ("request_id", "project_id", "tool_name"):
                assert key not in metrics_payload, key
            assert "req-secret-1" not in metrics_text
        finally:
            root._TOOL_EXECUTION_COLLECTOR.clear()
