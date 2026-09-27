"""Tool Execution Service 测试（Phase 3.11 Step 2）。

验证最小 Tool 执行边界：

    tool_name + arguments
        ↓
    ToolExecutionService.execute()
        ├── capability 校验（工具白名单；被拒 → Handler 0 次调用）
        ↓
    ToolRegistry.execute()（唯一执行权威；本层不绕过）
        ↓
    ToolResult（原样返回，不重新包装）

覆盖：

    1.  构造校验（registry None / 缺 execute / capabilities 类型 /
        project_id 类型 → TypeError）
    2.  成功执行（capabilities=None 不限制；Handler 正常返回）
    3.  capability allowed → 正常执行
    4.  capability rejected → AIOrchestratorCapabilityError（capability /
        project_id 语义不变）+ Registry / Handler 0 次调用
    5.  Registry ToolError 归一化 → ToolResult(success=False) 原样返回
    6.  unexpected exception：Registry 层异常原样传播（不吞 / 不包装）
    7.  arguments 原样传递（含 None 语义；不做任何变形）
    8.  Tool 未注册 → ToolResult(success=False)（不抛，Registry 语义）
    9.  Orchestrator 集成（默认构造 / 显式注入 / capability 403 回归）
    10. 静态安全检查（AST：无 DB / HTTP / Shell / LLM 标识符）

复用项目既有模式：真实 ToolRegistry + 私有 handler（test_tool_framework
风格）、FakeRouter / _tool_decision（test_ai_orchestrator 复用）。

0 DB / 0 Network。
"""
from __future__ import annotations

import ast
import os
from typing import Any

import pytest

from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorCapabilityError,
    AIOrchestratorService,
)
from backend.app.services.tool_execution_service import (
    ToolExecutionService,
)
from backend.app.tools.base import ToolDefinition, ToolResult
from backend.app.tools.errors import ToolExecutionError
from backend.app.tools.mock_tools import register_mock_tools
from backend.app.tools.registry import ToolRegistry

# 复用既有测试资产（与 test_llm_usage_analytics_facade 复用
# test_llm_usage_analytics helpers 同模式），不重复创建 FakeRouter。
from tests.test_ai_orchestrator import (  # noqa: E402
    FakeRouter,
    _tool_decision,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SERVICE_MODULE = "backend/app/services/tool_execution_service.py"


# ============================================================
# 测试资产（与 test_tool_framework 的私有 handler 模式一致）
# ============================================================

def _echo_definition() -> ToolDefinition:
    return ToolDefinition(
        name="get_inventory",
        description="查询物料 当前库存 数量",
        parameters={
            "type": "object",
            "properties": {"material_code": {"type": "string"}},
            "required": ["material_code"],
        },
    )


class _EchoHandler:
    """记录调用并回显 arguments 的 Handler。"""

    def __init__(self) -> None:
        self.calls = 0
        self.last_arguments: dict[str, Any] | None = None

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        self.last_arguments = dict(arguments)
        return {"material_code": arguments.get("material_code"), "qty": 5.0}


class _ToolErrorHandler:
    """抛 ToolError 家族异常的 Handler（Registry 应归一化）。"""

    async def __call__(self, arguments: dict[str, Any]) -> dict[str, Any]:
        raise ToolExecutionError(tool_name="get_inventory", reason="boom")


class _RecordingRegistry:
    """记录 execute 调用（tool_name + arguments 原样）的 Registry 替身。"""

    def __init__(self, result: ToolResult | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self._result = result or ToolResult(
            tool_name="get_inventory", success=True, data={"qty": 1.0}
        )

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        self.calls.append((tool_name, arguments))
        return self._result


class _ExplodingRegistry:
    """execute 抛未归一化异常的 Registry 替身（验证不吞 / 不包装）。"""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> ToolResult:
        raise self.error


def _registry_with(handler: Any) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_echo_definition(), handler)
    return registry


# ============================================================
# 1. 构造校验
# ============================================================

class TestConstruction:
    def test_registry_none_rejected(self) -> None:
        with pytest.raises(TypeError):
            ToolExecutionService(registry=None)  # type: ignore[arg-type]

    def test_registry_without_execute_rejected(self) -> None:
        class _NoExecute:
            pass

        with pytest.raises(TypeError):
            ToolExecutionService(registry=_NoExecute())  # type: ignore[arg-type]

    def test_capabilities_type_rejected(self) -> None:
        with pytest.raises(TypeError):
            ToolExecutionService(
                registry=ToolRegistry(),
                capabilities={"not": "capabilities"},  # type: ignore[arg-type]
            )

    def test_project_id_type_rejected(self) -> None:
        with pytest.raises(TypeError):
            ToolExecutionService(
                registry=ToolRegistry(),
                project_id=123,  # type: ignore[arg-type]
            )

    def test_readonly_exposure(self) -> None:
        registry = ToolRegistry()
        caps = ProjectCapabilities()
        service = ToolExecutionService(
            registry=registry, capabilities=caps, project_id="proj-x"
        )
        assert service.registry is registry
        assert service.capabilities is caps
        assert service.project_id == "proj-x"


# ============================================================
# 2 / 3. 成功执行 + capability allowed
# ============================================================

class TestSuccessfulExecution:
    async def test_executes_via_registry(self) -> None:
        """capabilities=None → 不限制；Handler 正常返回。"""
        handler = _EchoHandler()
        service = ToolExecutionService(registry=_registry_with(handler))

        result = await service.execute(
            "get_inventory", arguments={"material_code": "MAT-001"}
        )

        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.data == {"material_code": "MAT-001", "qty": 5.0}
        assert handler.calls == 1

    async def test_capability_allowed_executes(self) -> None:
        """capability 白名单包含该 Tool → 正常执行。"""
        handler = _EchoHandler()
        service = ToolExecutionService(
            registry=_registry_with(handler),
            capabilities=ProjectCapabilities(
                tool_names=("get_inventory",)
            ),
            project_id="proj-x",
        )

        result = await service.execute(
            "get_inventory", arguments={"material_code": "MAT-001"}
        )

        assert result.success is True
        assert handler.calls == 1

    async def test_default_capabilities_allow_inventory(self) -> None:
        """默认能力（DEFAULT_PROJECT_CAPABILITIES）允许 get_inventory。"""
        handler = _EchoHandler()
        service = ToolExecutionService(
            registry=_registry_with(handler),
            capabilities=ProjectCapabilities(),
        )
        result = await service.execute(
            "get_inventory", arguments={"material_code": "X"}
        )
        assert result.success is True


# ============================================================
# 4. capability rejected
# ============================================================

class TestCapabilityRejected:
    async def test_rejected_raises_and_skips_registry(self) -> None:
        """白名单不含该 Tool → AIOrchestratorCapabilityError；
        Registry + Handler 0 次调用。"""
        handler = _EchoHandler()
        registry = _registry_with(handler)
        service = ToolExecutionService(
            registry=registry,
            capabilities=ProjectCapabilities(tool_names=()),
            project_id="proj-a",
        )

        with pytest.raises(AIOrchestratorCapabilityError) as ei:
            await service.execute(
                "get_inventory", arguments={"material_code": "MAT-001"}
            )

        assert ei.value.capability == "get_inventory"
        assert ei.value.project_id == "proj-a"
        assert handler.calls == 0

    async def test_rejected_zero_registry_calls(self) -> None:
        """被拒时 Registry.execute 本身 0 次调用（边界前置校验）。"""
        registry = _RecordingRegistry()
        service = ToolExecutionService(
            registry=registry,
            capabilities=ProjectCapabilities(tool_names=("other_tool",)),
        )

        with pytest.raises(AIOrchestratorCapabilityError):
            await service.execute("get_inventory")

        assert registry.calls == []

    async def test_none_capabilities_means_unrestricted(self) -> None:
        """capabilities=None → 不做能力校验（旧行为）。"""
        registry = _RecordingRegistry()
        service = ToolExecutionService(registry=registry)
        result = await service.execute("anything")
        assert result.success is True
        assert len(registry.calls) == 1


# ============================================================
# 5. Registry 归一化（ToolError → ToolResult(False)）原样返回
# ============================================================

class TestRegistryNormalization:
    async def test_tool_error_becomes_failed_result(self) -> None:
        """Handler 抛 ToolExecutionError → Registry 归一 →
        service 原样返回 success=False（不抛 / 不重新包装）。"""
        service = ToolExecutionService(
            registry=_registry_with(_ToolErrorHandler())
        )

        result = await service.execute(
            "get_inventory", arguments={"material_code": "MAT-001"}
        )

        assert result.success is False
        assert result.data is None
        assert result.error is not None
        assert "boom" in result.error

    async def test_unknown_tool_returns_failed_result(self) -> None:
        """Tool 未注册 → ToolResult(success=False)（Registry 语义，不抛）。"""
        service = ToolExecutionService(registry=ToolRegistry())
        result = await service.execute("nonexistent_tool")
        assert result.success is False
        assert result.error is not None

    async def test_validation_failure_returns_failed_result(self) -> None:
        """参数 Schema 校验失败 → ToolResult(success=False)。"""
        handler = _EchoHandler()
        service = ToolExecutionService(registry=_registry_with(handler))
        result = await service.execute(
            "get_inventory", arguments={"wrong_field": "x"}
        )
        assert result.success is False
        assert handler.calls == 0  # Schema 校验先于 Handler


# ============================================================
# 6. unexpected exception（不吞 / 不包装）
# ============================================================

class TestUnexpectedException:
    async def test_registry_exception_propagates_as_is(self) -> None:
        """Registry 抛未归一化异常 → 原样传播（同一对象，
        不包装为 ToolResult / 不重抛新异常）。"""
        error = RuntimeError("registry boom")
        service = ToolExecutionService(
            registry=_ExplodingRegistry(error)
        )

        with pytest.raises(RuntimeError) as excinfo:
            await service.execute("get_inventory")

        assert excinfo.value is error


# ============================================================
# 7. arguments 原样传递
# ============================================================

class TestArgumentsPassthrough:
    async def test_arguments_passed_verbatim(self) -> None:
        """arguments 原样传给 Registry.execute（同一对象身份；
        不新增 / 不删除 / 不 strip 字段）。"""
        registry = _RecordingRegistry()
        service = ToolExecutionService(registry=registry)
        arguments = {"material_code": "MAT-001"}

        await service.execute("get_inventory", arguments=arguments)

        assert len(registry.calls) == 1
        tool_name, received = registry.calls[0]
        assert tool_name == "get_inventory"
        assert received is arguments

    async def test_none_arguments_passed_verbatim(self) -> None:
        """未传 arguments → Registry 收到 None（不预先转 {}）。"""
        registry = _RecordingRegistry()
        service = ToolExecutionService(registry=registry)

        await service.execute("get_inventory")

        assert registry.calls == [("get_inventory", None)]


# ============================================================
# 9. Orchestrator 集成
# ============================================================

class TestOrchestratorIntegration:
    def test_orchestrator_builds_default_execution_service(self) -> None:
        """Orchestrator 未显式注入 → 按 tools/capabilities 自动构造，
        且 registry 与 _tools 是同一对象。"""
        registry = ToolRegistry()
        register_mock_tools(registry)
        caps = ProjectCapabilities()
        orch = AIOrchestratorService(
            tool_registry=registry, capabilities=caps
        )

        execution = orch._tool_execution
        assert isinstance(execution, ToolExecutionService)
        assert execution.registry is registry
        assert execution.capabilities is caps

    def test_orchestrator_without_tools_has_no_execution(self) -> None:
        """tool_registry=None（旧行为）→ 不构造执行边界。"""
        orch = AIOrchestratorService()
        assert orch._tool_execution is None

    async def test_orchestrator_uses_injected_execution_service(self) -> None:
        """显式注入的执行边界被 _run_tool 使用（收到正确
        tool_name / arguments）。"""
        calls: list[tuple[str, dict[str, Any] | None]] = []

        class _RecordingExecution:
            """duck-typed 边界替身（Phase 3.11 Step 18：接受 context 入参）。"""

            async def execute(
                self,
                tool_name: str,
                *,
                arguments: dict[str, Any] | None = None,
                context: Any = None,
            ) -> ToolResult:
                calls.append((tool_name, arguments))
                return ToolResult(
                    tool_name=tool_name, success=True, data={"qty": 7.0}
                )

        registry = ToolRegistry()
        register_mock_tools(registry)
        orch = AIOrchestratorService(
            router=FakeRouter(_tool_decision()),
            tool_registry=registry,
            tool_execution_service=_RecordingExecution(),  # type: ignore[arg-type]
        )

        result = await orch.execute("查询物料 MAT-001 当前库存")

        assert result.route.value == "tool"
        assert result.data.success is True
        assert len(calls) == 1
        tool_name, arguments = calls[0]
        assert tool_name == "get_inventory"
        # Phase 3.7.12 正则提取行为不变：字面量进入 arguments
        assert isinstance(arguments, dict)
        assert arguments.get("material_code") == "MAT-001"

    async def test_orchestrator_capability_denied_semantics_unchanged(
        self,
    ) -> None:
        """capability 拒绝经新链路仍抛 AIOrchestratorCapabilityError
        （403 语义），且 Handler 0 次调用。"""
        handler = _EchoHandler()
        registry = _registry_with(handler)
        orch = AIOrchestratorService(
            router=FakeRouter(_tool_decision()),
            tool_registry=registry,
            capabilities=ProjectCapabilities(tool_names=()),  # 不允许任何 Tool
        )

        with pytest.raises(AIOrchestratorCapabilityError) as ei:
            await orch.execute("查询物料 MAT-001 当前库存")

        assert ei.value.capability == "get_inventory"
        assert handler.calls == 0

    async def test_orchestrator_tool_failure_surfaces_as_result(self) -> None:
        """Tool 失败（Registry 归一）→ Orchestrator 不抛，
        metadata.tool_success=False（既有行为回归）。"""
        registry = ToolRegistry()
        registry.register(_echo_definition(), _ToolErrorHandler())
        orch = AIOrchestratorService(
            router=FakeRouter(_tool_decision()),
            tool_registry=registry,
        )

        result = await orch.execute("查询物料 MAT-001 当前库存")

        assert result.route.value == "tool"
        assert result.metadata["tool_success"] is False


# ============================================================
# 10. 静态安全检查（AST，非全文扫描）
# ============================================================

class TestStaticSecurity:
    """ToolExecutionService 只允许依赖 ToolRegistry + 纯配置 DTO。"""

    def _service_module_ast(self) -> ast.Module:
        path = os.path.join(REPO_ROOT, *(_SERVICE_MODULE.split("/")))
        with open(path, encoding="utf-8") as handle:
            return ast.parse(handle.read())

    def test_no_db_or_infra_imports(self) -> None:
        tree = self._service_module_ast()
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        forbidden = {
            "sqlalchemy", "psycopg", "httpx", "requests", "socket",
            "subprocess", "os", "redis", "celery", "kafka",
            "backend.app.db", "backend.app.llm",
        }
        assert not any(name in forbidden for name in imported), imported

    def test_no_forbidden_db_or_network_identifiers(self) -> None:
        tree = self._service_module_ast()
        identifiers: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                identifiers.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr.lower())

        for fragment in (
            "session", "engine", "connection", "socket",
            "subprocess", "requests", "httpx", "sqlalchemy",
        ):
            assert not any(
                fragment in ident for ident in identifiers
            ), fragment

    def test_no_sql_string_constants(self) -> None:
        tree = self._service_module_ast()
        strings: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(
                node.value, str
            ):
                strings.append(node.value)

        for fragment in ("select ", "insert ", "update ", "delete "):
            assert not any(
                fragment in value.lower() for value in strings
            ), fragment
