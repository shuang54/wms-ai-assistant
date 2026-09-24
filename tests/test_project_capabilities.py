"""Project Capabilities 单元测试（Phase 3.8.2，任务书 §14.1）。

不依赖数据库 / 网络 / LLM。
覆盖：ProjectCapabilities DTO 校验、ProjectRegistration 集成、
默认注册表能力、Router 能力门控、Orchestrator 执行前硬校验、
工厂 Tool 过滤。
"""
from __future__ import annotations

import asyncio
import dataclasses

import pytest

from backend.app.config import settings
from backend.app.projects.capabilities import (
    DEFAULT_PROJECT_CAPABILITIES,
    DEFAULT_TOOL_NAMES,
    ProjectCapabilities,
    ProjectCapabilityError,
)
from backend.app.projects.models import DataSource, ProjectContext
from backend.app.projects.registry import (
    InMemoryProjectRegistry,
    ProjectNotFoundError,
    ProjectRegistration,
    ProjectRegistryError,
    get_default_project_registry,
)
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorCapabilityError,
    AIOrchestratorService,
)
from backend.app.services.ai_router_service import (
    AIRouterService,
    RouteType,
    ToolRegistryCapabilityAdapter,
)


def _make_context(project_id: str) -> ProjectContext:
    return ProjectContext(
        project_id=project_id,
        project_name=f"Project {project_id}",
        description=None,
        data_source=DataSource(name="primary", type="postgresql"),
    )


# ============================================================
# 14.1 ProjectCapabilities DTO
# ============================================================

class TestProjectCapabilitiesDTO:
    def test_defaults_equal_current_system(self) -> None:
        """默认能力 = Phase 3.8.2 之前的系统行为（§十三）。"""
        caps = ProjectCapabilities()
        assert caps.tool_names == DEFAULT_TOOL_NAMES == ("get_inventory",)
        assert caps.knowledge_enabled is True
        assert caps.text_to_sql_enabled is True

    def test_restricted_capabilities(self) -> None:
        """sales-demo 型项目：无 Tool + T2S 禁用 + Knowledge 开启。"""
        caps = ProjectCapabilities(
            tool_names=(),
            knowledge_enabled=True,
            text_to_sql_enabled=False,
        )
        assert caps.tool_names == ()
        assert caps.allows_tool("get_inventory") is False
        assert caps.text_to_sql_enabled is False

    def test_frozen(self) -> None:
        caps = ProjectCapabilities()
        with pytest.raises(dataclasses.FrozenInstanceError):
            caps.knowledge_enabled = False  # type: ignore[misc]

    def test_tool_names_validation(self) -> None:
        """tool_names：拒绝裸字符串 / 空项 / 非法名 / 重复项。"""
        with pytest.raises(ProjectCapabilityError, match="tuple"):
            ProjectCapabilities(tool_names="get_inventory")  # type: ignore[arg-type]
        with pytest.raises(ProjectCapabilityError, match="非空"):
            ProjectCapabilities(tool_names=("",))
        with pytest.raises(ProjectCapabilityError, match="Tool 名称"):
            ProjectCapabilities(tool_names=("Get-Inventory",))
        with pytest.raises(ProjectCapabilityError, match="重复"):
            ProjectCapabilities(tool_names=("get_inventory", "get_inventory"))

    def test_bool_flags_strict(self) -> None:
        """布尔开关严格 bool（拒绝 1 / 'yes' 等 truthy 值）。"""
        with pytest.raises(ProjectCapabilityError):
            ProjectCapabilities(knowledge_enabled=1)  # type: ignore[arg-type]
        with pytest.raises(ProjectCapabilityError):
            ProjectCapabilities(text_to_sql_enabled="true")  # type: ignore[arg-type]

    def test_no_sensitive_fields(self) -> None:
        """字段白名单（任务书 §五：不含 handler/engine/凭据）。"""
        caps = ProjectCapabilities()
        caps.assert_no_sensitive_fields()  # 不抛
        assert {f.name for f in dataclasses.fields(caps)} == {
            "tool_names", "knowledge_enabled", "text_to_sql_enabled"
        }

    def test_default_capabilities_constant_is_restricted_safe(self) -> None:
        """默认常量与 DTO 默认一致（不可被意外改动后静默扩散）。"""
        assert DEFAULT_PROJECT_CAPABILITIES == ProjectCapabilities()


# ============================================================
# ProjectRegistration 集成（兼容性，任务书 §四）
# ============================================================

class TestRegistrationCapabilities:
    def test_default_capabilities_when_omitted(self) -> None:
        """省略 capabilities → 默认（3.8.1 位置构造仍可用，零改动兼容）。"""
        reg = ProjectRegistration(
            context=_make_context("project-a"), schema_name="project_a"
        )
        assert reg.capabilities == DEFAULT_PROJECT_CAPABILITIES

    def test_explicit_capabilities(self) -> None:
        caps = ProjectCapabilities(tool_names=(), text_to_sql_enabled=False)
        reg = ProjectRegistration(
            context=_make_context("sales-demo"),
            schema_name="public",
            capabilities=caps,
        )
        assert reg.capabilities is caps

    def test_invalid_capabilities_type_rejected(self) -> None:
        with pytest.raises(ProjectRegistryError, match="ProjectCapabilities"):
            ProjectRegistration(
                context=_make_context("x"),
                schema_name="public",
                capabilities={"tools": ["get_inventory"]},  # type: ignore[arg-type]
            )

    def test_registry_returns_capabilities(self) -> None:
        """known project → capabilities（§14.1）。"""
        registry = InMemoryProjectRegistry()
        caps = ProjectCapabilities(tool_names=("get_inventory",))
        registry.register(
            "project-a",
            ProjectRegistration(
                context=_make_context("project-a"),
                schema_name="project_a",
                capabilities=caps,
            ),
        )
        assert registry.get("project-a").capabilities is caps

    def test_unknown_project_still_not_found(self) -> None:
        """unknown project → ProjectNotFoundError（不因能力配置而回退）。"""
        registry = InMemoryProjectRegistry()
        registry.register(
            "project-a",
            ProjectRegistration(
                context=_make_context("project-a"), schema_name="project_a"
            ),
        )
        with pytest.raises(ProjectNotFoundError):
            registry.get("ghost")

    def test_default_registry_default_project_has_default_capabilities(self) -> None:
        """默认注册表：配置默认项目 → 默认能力（旧行为不变，§十三）。"""
        r = get_default_project_registry().get(settings.project.project_id)
        assert r.capabilities == DEFAULT_PROJECT_CAPABILITIES
        assert r.capabilities.allows_tool("get_inventory")


# ============================================================
# Router 能力门控（§十 / §十六）
# ============================================================

class TestRouterCapabilityGating:
    @pytest.mark.asyncio
    async def test_t2s_disabled_analytics_falls_to_rag(self) -> None:
        """text_to_sql_enabled=False → 分析类问题不再选中 TEXT_TO_SQL，
        落到知识规则 / 保守兜底（RAG）。"""
        router = AIRouterService(
            llm_fallback_enabled=False,
            text_to_sql_enabled=False,
        )
        decision = await router.route("统计所有物料的库存汇总明细")
        assert decision.route == RouteType.RAG

    @pytest.mark.asyncio
    async def test_t2s_enabled_analytics_unchanged(self) -> None:
        """开关默认 True → 分析问题仍走 TEXT_TO_SQL（回归）。"""
        router = AIRouterService(llm_fallback_enabled=False)
        decision = await router.route("统计所有物料的库存汇总明细")
        assert decision.route == RouteType.TEXT_TO_SQL

    @pytest.mark.asyncio
    async def test_knowledge_disabled_question_falls_to_fallback(self) -> None:
        """knowledge_enabled=False → 知识类问题跳过知识规则，
        落到保守兜底（RAG 决策仍返回，由 Orchestrator 硬校验拒绝）。"""
        router = AIRouterService(
            llm_fallback_enabled=False,
            knowledge_enabled=False,
        )
        decision = await router.route("WMS 盘点流程是什么？")
        assert decision.route == RouteType.RAG
        assert decision.source == "fallback"  # 不是规则命中的 RAG

    @pytest.mark.asyncio
    async def test_router_does_not_see_disallowed_tools(self) -> None:
        """工具过滤：空 Tool Registry → Router 看不到 get_inventory，
        库存问题不再 TOOL 路由（§七 / §14.2）。"""
        from backend.app.tools.registry import ToolRegistry

        empty_registry = ToolRegistry()
        router = AIRouterService(
            llm_fallback_enabled=False,
            tool_capabilities=ToolRegistryCapabilityAdapter(empty_registry),
        )
        decision = await router.route("查询物料 10001 当前库存")
        assert decision.route != RouteType.TOOL

    @pytest.mark.asyncio
    async def test_router_sees_allowed_tools(self) -> None:
        """Project A（get_inventory 允许）→ Router 正常 TOOL 命中（回归）。"""
        from backend.app.tools.get_inventory import build_default_tool_registry

        router = AIRouterService(
            llm_fallback_enabled=False,
            tool_capabilities=ToolRegistryCapabilityAdapter(
                build_default_tool_registry()
            ),
        )
        decision = await router.route("查询物料 10001 当前库存")
        assert decision.route == RouteType.TOOL

    @pytest.mark.asyncio
    async def test_llm_fallback_disabled_route_treated_as_error(self) -> None:
        """LLM fallback 选中被禁用 route → 分类失败 → 保守兜底 RAG。"""
        class _FakeLLM:
            async def chat(self, messages):
                return '{"route": "text_to_sql", "reason": "analytics"}'

        router = AIRouterService(
            llm_client=_FakeLLM(),
            text_to_sql_enabled=False,
        )
        decision = await router.route("某个模糊问题")
        assert decision.route == RouteType.RAG
        assert decision.source == "fallback"

    def test_router_rejects_non_bool_flags(self) -> None:
        with pytest.raises(Exception):
            AIRouterService(knowledge_enabled=1)  # type: ignore[arg-type]
        with pytest.raises(Exception):
            AIRouterService(text_to_sql_enabled="yes")  # type: ignore[arg-type]


# ============================================================
# Orchestrator 执行前硬校验（§十一 / §14.3-14.5）
# ============================================================

class _CountingRag:
    """Fake RagService：计数 + 返回最小 RagResponse 形状。"""

    def __init__(self) -> None:
        self.calls = 0

    async def answer(self, question: str):
        self.calls += 1
        return type("RagResponse", (), {"answer": "ok", "used_chunks_count": 0})()


class _CountingT2S:
    """Fake TextToSQL Generator：计数 + 固定 SQL。"""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, question, **kwargs):
        self.calls += 1
        return type("SQLResult", (), {"sql": "SELECT 1 LIMIT 1"})()


class _CountingExecutor:
    """Fake SQLExecutor：计数（证明 0 次数据库访问）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, sql, **kwargs):
        self.calls += 1
        raise AssertionError("capability denied 场景不应执行 SQL")


class _StubRouter:
    """固定决策的 Fake Router（绕过规则，直接测 Orchestrator 硬校验）。"""

    def __init__(self, route: RouteType) -> None:
        self._route = route

    async def route(self, question, *, context=None):
        from backend.app.services.ai_router_service import RouteDecision

        return RouteDecision(
            route=self._route, confidence=1.0, reason="stub", source="stub"
        )


class _ExplodingProvider:
    """任何 resolve 调用都视为违规（capability denied 不应触碰 Schema）。"""

    def resolve(self):
        raise AssertionError(
            "capability denied 场景不应解析 ProjectContext / Schema"
        )


class TestOrchestratorHardChecks:
    def _build(
        self,
        capabilities,
        route: RouteType,
        *,
        rag=None,
        t2s=None,
        executor=None,
        tools=None,
    ) -> AIOrchestratorService:
        return AIOrchestratorService(
            router=_StubRouter(route),
            rag_service=rag if rag is not None else _CountingRag(),
            tool_registry=tools,
            text_to_sql=t2s if t2s is not None else _CountingT2S(),
            sql_executor=executor if executor is not None else _CountingExecutor(),
            project_context_provider=_ExplodingProvider(),
            capabilities=capabilities,
        )

    # ---- 14.4 RAG capability ----

    @pytest.mark.asyncio
    async def test_knowledge_disabled_rag_denied_zero_calls(self) -> None:
        """knowledge_enabled=False + RAG route → capability denied；
        RagService 0 次调用。"""
        rag = _CountingRag()
        orch = self._build(
            ProjectCapabilities(knowledge_enabled=False), RouteType.RAG, rag=rag
        )
        with pytest.raises(AIOrchestratorCapabilityError) as ei:
            await orch.execute("WMS 盘点流程是什么？")
        assert ei.value.capability == "knowledge"
        assert rag.calls == 0

    @pytest.mark.asyncio
    async def test_knowledge_enabled_rag_normal(self) -> None:
        """knowledge_enabled=True（默认）→ RAG 正常执行（回归）。"""
        rag = _CountingRag()
        orch = self._build(
            ProjectCapabilities(), RouteType.RAG, rag=rag
        )
        result = await orch.execute("WMS 盘点流程是什么？")
        assert result.route == RouteType.RAG
        assert rag.calls == 1

    # ---- 14.5 Text-to-SQL capability ----

    @pytest.mark.asyncio
    async def test_t2s_disabled_denied_before_any_db_access(self) -> None:
        """text_to_sql_enabled=False + TEXT_TO_SQL route → capability denied；
        Generator 0 次、Executor 0 次、ProjectContextProvider 0 次解析。"""
        t2s = _CountingT2S()
        executor = _CountingExecutor()
        orch = self._build(
            ProjectCapabilities(text_to_sql_enabled=False),
            RouteType.TEXT_TO_SQL,
            t2s=t2s,
            executor=executor,
        )
        with pytest.raises(AIOrchestratorCapabilityError) as ei:
            await orch.execute("统计所有物料的库存汇总明细")
        assert ei.value.capability == "text_to_sql"
        assert t2s.calls == 0
        assert executor.calls == 0

    # ---- 14.3 Tool execution isolation ----

    @pytest.mark.asyncio
    async def test_tool_not_allowed_denied_zero_handler_calls(self) -> None:
        """tool_names 不含目标 Tool + TOOL route → capability denied；
        Handler 0 次调用（工具虽在 Registry，能力白名单拒绝）。"""
        from backend.app.tools.base import ToolDefinition
        from backend.app.tools.registry import ToolRegistry

        class _CountingHandler:
            def __init__(self) -> None:
                self.calls = 0

            async def __call__(self, arguments):
                self.calls += 1
                return {"qty": 1}

        handler = _CountingHandler()
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="get_inventory",
                description="查询物料 当前库存 数量",
                parameters={
                    "type": "object",
                    "properties": {"material_code": {"type": "string"}},
                    "required": ["material_code"],
                },
            ),
            handler,
        )
        orch = self._build(
            ProjectCapabilities(tool_names=()),  # 该项目不允许任何 Tool
            RouteType.TOOL,
            tools=registry,
        )
        with pytest.raises(AIOrchestratorCapabilityError) as ei:
            await orch.execute("查询物料 10001 当前库存")
        assert ei.value.capability == "get_inventory"
        assert handler.calls == 0

    @pytest.mark.asyncio
    async def test_tool_allowed_normal(self) -> None:
        """tool_names 含 get_inventory → 正常执行（回归）。"""
        from backend.app.tools.base import ToolDefinition
        from backend.app.tools.registry import ToolRegistry

        class _OkHandler:
            async def __call__(self, arguments):
                return {"material_code": "10001", "qty": 5.0}

        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="get_inventory",
                description="查询物料 当前库存 数量",
                parameters={
                    "type": "object",
                    "properties": {"material_code": {"type": "string"}},
                    "required": ["material_code"],
                },
            ),
            _OkHandler(),
        )
        orch = self._build(
            ProjectCapabilities(tool_names=("get_inventory",)),
            RouteType.TOOL,
            tools=registry,
        )
        result = await orch.execute("查询物料 10001 当前库存")
        assert result.route == RouteType.TOOL
        assert result.data.success is True
        assert result.data.data["qty"] == 5.0

    # ---- 兼容：capabilities=None 不限制 ----

    @pytest.mark.asyncio
    async def test_none_capabilities_means_unrestricted(self) -> None:
        """capabilities=None（默认 Orchestrator）→ 不做能力校验（旧行为）。"""
        t2s = _CountingT2S()
        orch = AIOrchestratorService(
            router=_StubRouter(RouteType.RAG),
            rag_service=_CountingRag(),
            project_context_provider=_ExplodingProvider(),
        )
        result = await orch.execute("WMS 盘点流程是什么？")
        assert result.route == RouteType.RAG

    def test_invalid_capabilities_type_rejected(self) -> None:
        with pytest.raises(Exception):
            AIOrchestratorService(capabilities="all")  # type: ignore[arg-type]


__all__ = [
    "TestProjectCapabilitiesDTO",
    "TestRegistrationCapabilities",
    "TestRouterCapabilityGating",
    "TestOrchestratorHardChecks",
]
