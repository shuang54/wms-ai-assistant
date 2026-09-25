"""Project Configuration Aggregation 测试（Phase 3.8.6）。

覆盖任务书 §十五（Case 1-6）、§十六（调用顺序 / 调用次数）、
§十七（Factory E2E）、§十八（跨项目隔离）与 §十九（HTTP 注入安全）。

层次：

1. DTO 单元：frozen / 字段白名单（无 secret / engine / service / provider）
   / 类型与一致性校验；
2. ConfigurationProvider 单元：Fake Registry / Semantic / Knowledge
   记录调用次数，覆盖 Case 1-6 与 knowledge_enabled=False 的 0 次解析；
3. Factory 层：Registry 权威性（404 透传）、Semantic / Knowledge 缺失 →
   AIOrchestratorUnavailableError（503）、旧 DI 入参完全兼容；
4. DB E2E：project-a / project-b 的 DataSource + Capability + Semantic +
   Knowledge 四者同源且彼此隔离；
5. 安全：HTTP 无法注入 capabilities / semantic / knowledge_scope /
   datasource（Pydantic 忽略未知字段 +  Provider 调用记录证明）。
"""
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields, replace

import pytest

from backend.app.config import settings
from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.projects.configuration import (
    DefaultProjectConfigurationProvider,
    ProjectConfiguration,
    ProjectConfigurationError,
)
from backend.app.projects.context import get_default_project_context
from backend.app.projects.knowledge_provider import (
    ProjectKnowledgeScope,
    ProjectKnowledgeScopeError,
)
from backend.app.projects.models import DataSource, ProjectContext
from backend.app.projects.registry import (
    ProjectNotFoundError,
    ProjectRegistration,
)
from backend.app.projects.semantic import (
    ColumnSemantic,
    ProjectSemantic,
    TableSemantic,
)
from backend.app.projects.semantic_loader import ProjectSemanticNotFoundError


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not (_env_flag("RUN_DB_TESTS") and settings.database.url.strip()),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable project "
    "configuration DB E2E tests",
)


# ============================================================
# 测试夹具：A / B / vietnam-wms 三项目的最小配置源
# ============================================================

SEMANTIC_A = ProjectSemantic(
    tables=(TableSemantic(table="inventory", business_name="库存"),),
    columns=(ColumnSemantic(
        table="inventory", column="qty", business_name="库存数量"
    ),),
)
SEMANTIC_B = ProjectSemantic(
    tables=(TableSemantic(table="inventory", business_name="可用库存"),),
    columns=(ColumnSemantic(
        table="inventory", column="qty", business_name="可用数量"
    ),),
)
SEMANTIC_VN = ProjectSemantic(
    tables=(TableSemantic(table="knowledge_document", business_name="知识文档"),),
)

SCOPE_A = ProjectKnowledgeScope(project_id="project-a", namespace="project-a")
SCOPE_B = ProjectKnowledgeScope(project_id="project-b", namespace="project-b")
SCOPE_VN = ProjectKnowledgeScope(
    project_id="vietnam-wms",
    namespace="vietnam-wms",
    includes_global=True,
    includes_legacy=True,
)

CAPS_FULL = ProjectCapabilities(
    tool_names=("get_inventory",), knowledge_enabled=True, text_to_sql_enabled=True
)
CAPS_NO_KNOWLEDGE = ProjectCapabilities(
    tool_names=("get_inventory",), knowledge_enabled=False, text_to_sql_enabled=True
)


def _context(project_id: str) -> ProjectContext:
    return ProjectContext(
        project_id=project_id,
        project_name=f"Project {project_id}",
        description=None,
        data_source=DataSource(name="primary", type="postgresql"),
    )


def _registration(
    project_id: str,
    schema_name: str,
    capabilities: ProjectCapabilities = CAPS_FULL,
) -> ProjectRegistration:
    return ProjectRegistration(
        context=_context(project_id),
        schema_name=schema_name,
        capabilities=capabilities,
    )


# ---- Fake Providers（记录调用次数，任务书 §十六） ----

class FakeRegistry:
    def __init__(self, projects: dict[str, ProjectRegistration]) -> None:
        self._projects = projects
        self.calls: list[str] = []

    def get(self, project_id: str) -> ProjectRegistration:
        self.calls.append(project_id)
        registration = self._projects.get(project_id)
        if registration is None:
            raise ProjectNotFoundError(project_id)
        return registration


class FakeSemanticProvider:
    def __init__(self, semantics: dict[str, ProjectSemantic]) -> None:
        self._semantics = semantics
        self.calls: list[str] = []

    def get(self, project_id: str) -> ProjectSemantic:
        self.calls.append(project_id)
        semantic = self._semantics.get(project_id)
        if semantic is None:
            raise ProjectSemanticNotFoundError(project_id)
        return semantic


class FakeKnowledgeProvider:
    def __init__(self, scopes: dict[str, ProjectKnowledgeScope]) -> None:
        self._scopes = scopes
        self.calls: list[str] = []

    def get_scope(self, project_id: str) -> ProjectKnowledgeScope:
        self.calls.append(project_id)
        scope = self._scopes.get(project_id)
        if scope is None:
            raise ProjectKnowledgeScopeError(
                f"项目 {project_id!r} 的知识 scope 未注册"
            )
        return scope


def _providers(with_b: bool = True):
    """A / B（+ vietnam-wms）齐全的最小配置源。"""
    projects = {"project-a": _registration("project-a", "project_a")}
    semantics = {"project-a": SEMANTIC_A}
    scopes = {"project-a": SCOPE_A}
    if with_b:
        projects["project-b"] = _registration("project-b", "project_b")
        semantics["project-b"] = SEMANTIC_B
        scopes["project-b"] = SCOPE_B
    projects["vietnam-wms"] = _registration("vietnam-wms", "public")
    semantics["vietnam-wms"] = SEMANTIC_VN
    scopes["vietnam-wms"] = SCOPE_VN
    return (
        FakeRegistry(projects),
        FakeSemanticProvider(semantics),
        FakeKnowledgeProvider(scopes),
    )


def _configuration_provider(with_b: bool = True):
    registry, semantic, knowledge = _providers(with_b=with_b)
    provider = DefaultProjectConfigurationProvider(
        registry=registry,
        semantic_provider=semantic,
        knowledge_provider=knowledge,
    )
    return provider, registry, semantic, knowledge


# ============================================================
# 1. DTO 单元（任务书 §四）
# ============================================================

class TestProjectConfigurationDTO:
    def _cfg(self, **overrides) -> ProjectConfiguration:
        kwargs = {
            "context": _context("project-a"),
            "schema_name": "project_a",
            "capabilities": CAPS_FULL,
            "semantic": SEMANTIC_A,
            "knowledge_scope": SCOPE_A,
        }
        kwargs.update(overrides)
        return ProjectConfiguration(**kwargs)  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        cfg = self._cfg()
        with pytest.raises(FrozenInstanceError):
            cfg.schema_name = "other"  # type: ignore[misc]

    def test_project_id_property(self) -> None:
        assert self._cfg().project_id == "project-a"

    def test_field_whitelist_no_secrets(self) -> None:
        """§四：DTO 只保存配置结果，不含 secret / engine / service。"""
        allowed = {
            "context", "schema_name", "capabilities", "semantic",
            "knowledge_scope",
        }
        assert {f.name for f in fields(ProjectConfiguration)} == allowed
        forbidden = (
            "password", "db_url", "database_url", "api_key", "token",
            "engine", "session", "service", "registry",
            "semantic_provider", "knowledge_provider", "llm",
        )
        actual = {f.name for f in fields(ProjectConfiguration)}
        assert actual.isdisjoint(forbidden)

    def test_wrong_types_rejected(self) -> None:
        with pytest.raises(ProjectConfigurationError):
            self._cfg(context="project-a")  # type: ignore[arg-type]
        with pytest.raises(ProjectConfigurationError):
            self._cfg(schema_name="")  # type: ignore[arg-type]
        with pytest.raises(ProjectConfigurationError):
            self._cfg(capabilities={})  # type: ignore[arg-type]
        with pytest.raises(ProjectConfigurationError):
            self._cfg(semantic="semantic")  # type: ignore[arg-type]
        with pytest.raises(ProjectConfigurationError):
            self._cfg(knowledge_scope="project-a")  # type: ignore[arg-type]

    def test_knowledge_scope_must_belong_to_project(self) -> None:
        """§六：Knowledge Scope 归属必须与本项目一致（跨源一致性）。"""
        with pytest.raises(ProjectConfigurationError, match="不一致"):
            self._cfg(knowledge_scope=SCOPE_B)

    def test_knowledge_scope_none_allowed(self) -> None:
        """knowledge_enabled=False 的项目：scope 为空（Phase 3.8.4 §十三）。"""
        cfg = self._cfg(knowledge_scope=None)
        assert cfg.knowledge_scope is None


# ============================================================
# 2. ConfigurationProvider：Case 1-6 + 调用次数（§十五 / §十六）
# ============================================================

class TestAggregationCases:
    def test_case1_project_a_consistent(self) -> None:
        """Case 1：project-a 四者一致。"""
        provider, registry, semantic, knowledge = _configuration_provider()
        cfg = provider.get("project-a")

        assert cfg.context.project_id == "project-a"
        assert cfg.capabilities is CAPS_FULL
        assert cfg.semantic is SEMANTIC_A
        assert cfg.knowledge_scope is SCOPE_A
        assert cfg.knowledge_scope is not None
        assert cfg.knowledge_scope.namespace == "project-a"
        # §十六：每个 Provider 恰好 1 次
        assert registry.calls == ["project-a"]
        assert semantic.calls == ["project-a"]
        assert knowledge.calls == ["project-a"]

    def test_case2_project_b_consistent(self) -> None:
        """Case 2：project-b 四者一致，且不与 A 串配置。"""
        provider, *_ = _configuration_provider()
        cfg = provider.get("project-b")

        assert cfg.context.project_id == "project-b"
        assert cfg.schema_name == "project_b"
        assert cfg.semantic is SEMANTIC_B
        assert cfg.knowledge_scope is SCOPE_B

    def test_case3_vietnam_wms_legacy(self) -> None:
        """Case 3：vietnam-wms 的 legacy 语义完全保留。"""
        provider, *_ = _configuration_provider()
        cfg = provider.get("vietnam-wms")

        assert cfg.context.project_id == "vietnam-wms"
        assert cfg.schema_name == "public"
        assert cfg.semantic is SEMANTIC_VN
        assert cfg.knowledge_scope is not None
        assert cfg.knowledge_scope.namespace == "vietnam-wms"
        assert cfg.knowledge_scope.includes_legacy is True
        assert cfg.knowledge_scope.includes_global is True

    def test_case4_unknown_project_stops_at_registry(self) -> None:
        """Case 4：Registry 未注册 → ProjectNotFoundError，
        Semantic / Knowledge 0 次调用（§七 / §八）。"""
        provider, registry, semantic, knowledge = _configuration_provider()
        with pytest.raises(ProjectNotFoundError, match="project-x"):
            provider.get("project-x")
        assert registry.calls == ["project-x"]
        assert semantic.calls == []
        assert knowledge.calls == []

    def test_case5_missing_semantic_clear_error(self) -> None:
        """Case 5：Registry 有 project-a 但 Semantic 缺失 → clear error，
        绝不 fallback 到 vietnam-wms 语义。"""
        registry, semantic, knowledge = _providers()
        semantic_missing = FakeSemanticProvider({})  # 空映射
        provider = DefaultProjectConfigurationProvider(
            registry=registry,
            semantic_provider=semantic_missing,
            knowledge_provider=knowledge,
        )
        with pytest.raises(ProjectSemanticNotFoundError) as exc:
            provider.get("project-a")
        assert exc.value.project_id == "project-a"
        # Registry 先行已通过 → Semantic 失败后不再查 Knowledge（§二十）
        assert knowledge.calls == []

    def test_case6_missing_knowledge_clear_error(self) -> None:
        """Case 6：Knowledge 缺失 → clear error，绝不 fallback（global 也不）。"""
        registry, semantic, knowledge = _providers()
        knowledge_missing = FakeKnowledgeProvider({})
        provider = DefaultProjectConfigurationProvider(
            registry=registry,
            semantic_provider=semantic,
            knowledge_provider=knowledge_missing,
        )
        with pytest.raises(ProjectKnowledgeScopeError):
            provider.get("project-a")
        assert semantic.calls == ["project-a"]
        assert knowledge_missing.calls == ["project-a"]

    def test_knowledge_disabled_provider_not_executed(self) -> None:
        """knowledge_enabled=False → knowledge_scope=None 且 Provider 0 次。"""
        registry = FakeRegistry(
            {"project-no-rag": _registration(
                "project-no-rag", "project_b", CAPS_NO_KNOWLEDGE)}
        )
        semantic = FakeSemanticProvider({"project-no-rag": SEMANTIC_B})
        knowledge = FakeKnowledgeProvider({"project-no-rag": SCOPE_B})
        provider = DefaultProjectConfigurationProvider(
            registry=registry,
            semantic_provider=semantic,
            knowledge_provider=knowledge,
        )
        cfg = provider.get("project-no-rag")
        assert cfg.knowledge_scope is None
        assert knowledge.calls == []

    def test_no_duplicate_provider_calls(self) -> None:
        """§十六：不得出现 Semantic=2 / Knowledge=2。"""
        provider, registry, semantic, knowledge = _configuration_provider()
        provider.get("project-a")
        assert registry.calls == ["project-a"]
        assert semantic.calls == ["project-a"]
        assert knowledge.calls == ["project-a"]


# ============================================================
# 3. Factory 层（§十 / §十一）
# ============================================================

class _RecordingRAG:
    """Fake RagService：记录传入的 knowledge_scope（不涉及 DB / LLM）。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def answer(self, question, *, knowledge_scope=None, top_k=None):
        self.calls.append(
            {"question": question, "knowledge_scope": knowledge_scope}
        )
        from types import SimpleNamespace

        return SimpleNamespace(
            answer="FAKE-RAG-ANSWER", sources=(), used_chunks_count=0
        )


class _FakeEngineProvider:
    """Fake Engine Provider（不创建真实 Engine / 不连库）。"""

    def __init__(self) -> None:
        self.engine = object()
        self.calls: list[str] = []

    def get_engine(self, data_source) -> object:
        self.calls.append(data_source.name)
        return self.engine


def _semantic_of(orch) -> ProjectSemantic:
    """读取 Orchestrator 实际携带的 Semantic（不经 DB schema inspect）。"""
    return orch._project_provider._semantic_provider.get(
        orch._project_provider._project_context.project_id
    )


def _build(project_id: str, *, configuration_provider=None, rag=None, **kwargs):
    """构造 Orchestrator（默认 base = 真实模块级单例，engine 为 fake）。"""
    from backend.app.api import orchestrator_chat as orch_module
    from backend.app.services.project_orchestrator_factory import (
        build_orchestrator_for_project,
    )

    base = orch_module._default_orchestrator
    original_rag = base._rag
    if rag is not None:
        base._rag = rag
    try:
        return build_orchestrator_for_project(
            project_id,
            base=base,
            engine_provider=_FakeEngineProvider(),
            configuration_provider=configuration_provider,
            **kwargs,
        )
    finally:
        base._rag = original_rag


class TestFactoryAssembly:
    def test_factory_uses_single_configuration_entry(self) -> None:
        """§十：Factory 通过一次 configuration_provider.get 完成装配。"""
        provider, registry, semantic, knowledge = _configuration_provider()
        rag = _RecordingRAG()
        orch = _build("project-a", configuration_provider=provider, rag=rag)

        assert orch._knowledge_scope is SCOPE_A
        assert orch._capabilities is CAPS_FULL
        assert registry.calls == ["project-a"]
        assert semantic.calls == ["project-a"]
        assert knowledge.calls == ["project-a"]

    def test_factory_project_context_carries_semantic(self) -> None:
        """Semantic 来自聚合配置（Core Orchestrator 零改动）。"""
        provider, *_ = _configuration_provider()
        orch = _build("project-b", configuration_provider=provider)

        assert orch._project_provider._project_context.project_id == "project-b"
        assert _semantic_of(orch) is SEMANTIC_B

    def test_factory_unknown_project_404_semantics(self) -> None:
        """§八：Registry 权威性 —— 未注册透传 ProjectNotFoundError（→404）。"""
        provider, registry, semantic, knowledge = _configuration_provider()
        with pytest.raises(ProjectNotFoundError):
            _build("project-x", configuration_provider=provider)
        assert semantic.calls == []
        assert knowledge.calls == []
        assert registry.calls == ["project-x"]

    def test_factory_missing_semantic_unavailable(self) -> None:
        """Case 5 经 Factory：clear error → AIOrchestratorUnavailableError
        （API 映射 503），不 fallback vietnam-wms。"""
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorUnavailableError,
        )

        registry, semantic, knowledge = _providers()
        provider = DefaultProjectConfigurationProvider(
            registry=registry,
            semantic_provider=FakeSemanticProvider({}),
            knowledge_provider=knowledge,
        )
        with pytest.raises(AIOrchestratorUnavailableError, match="业务语义"):
            _build("project-a", configuration_provider=provider)

    def test_factory_missing_knowledge_unavailable(self) -> None:
        """Case 6 经 Factory：clear error → 503（保持既有文案含"知识库"）。"""
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorUnavailableError,
        )

        registry, semantic, knowledge = _providers()
        provider = DefaultProjectConfigurationProvider(
            registry=registry,
            semantic_provider=semantic,
            knowledge_provider=FakeKnowledgeProvider({}),
        )
        with pytest.raises(AIOrchestratorUnavailableError, match="知识库"):
            _build("project-a", configuration_provider=provider)

    def test_legacy_di_parameters_still_supported(self) -> None:
        """§二十一：旧调用（registry / semantic_provider /
        knowledge_provider 分别注入）行为完全不变。"""
        registry, semantic, knowledge = _providers()
        rag = _RecordingRAG()
        orch = _build(
            "project-a",
            rag=rag,
            registry=registry,
            semantic_provider=semantic,
            knowledge_provider=knowledge,
        )
        assert orch._knowledge_scope is SCOPE_A
        assert _semantic_of(orch) is SEMANTIC_A

    def test_tool_registry_follows_capabilities(self) -> None:
        """§十七：Tool 只加载 project capabilities.tool_names（空 → 空）。"""
        registry = FakeRegistry(
            {
                "project-a": _registration("project-a", "project_a", CAPS_FULL),
                "project-no-tool": _registration(
                    "project-no-tool",
                    "project_a",
                    ProjectCapabilities(tool_names=()),
                ),
            }
        )
        semantic = FakeSemanticProvider(
            {"project-a": SEMANTIC_A, "project-no-tool": SEMANTIC_A}
        )
        knowledge = FakeKnowledgeProvider(
            {
                "project-a": SCOPE_A,
                "project-no-tool": replace(
                    SCOPE_A,
                    project_id="project-no-tool",
                    namespace="project-no-tool",
                ),
            }
        )
        provider = DefaultProjectConfigurationProvider(
            registry=registry,
            semantic_provider=semantic,
            knowledge_provider=knowledge,
        )
        assert [
            d.name for d in _build(
                "project-a", configuration_provider=provider
            )._tools.list_definitions()
        ] == ["get_inventory"]
        assert _build(
            "project-no-tool", configuration_provider=provider
        )._tools.list_definitions() == ()


# ============================================================
# 4. 跨项目 E2E（§十八）
# ============================================================

class TestCrossProjectConsistency:
    def test_a_and_b_configuration_fully_isolated(self) -> None:
        """project-a / project-b：DataSource schema + Semantic + Knowledge
        全部各自取到自己的配置。"""
        provider, *_ = _configuration_provider()
        cfg_a = provider.get("project-a")
        cfg_b = provider.get("project-b")

        assert cfg_a.context.project_id != cfg_b.context.project_id
        assert cfg_a.schema_name == "project_a"
        assert cfg_b.schema_name == "project_b"
        assert cfg_a.semantic is SEMANTIC_A
        assert cfg_b.semantic is SEMANTIC_B
        assert cfg_a.knowledge_scope is SCOPE_A
        assert cfg_b.knowledge_scope is SCOPE_B

    def test_same_question_different_knowledge_scope(self) -> None:
        """同一问题进入不同项目 → RAG 收到的 scope 分别是 A / B。"""
        provider, *_ = _configuration_provider()
        question = "系统的操作流程是什么"
        rag_a, rag_b = _RecordingRAG(), _RecordingRAG()

        asyncio.run(
            _build("project-a", configuration_provider=provider, rag=rag_a)
            .execute(question)
        )
        asyncio.run(
            _build("project-b", configuration_provider=provider, rag=rag_b)
            .execute(question)
        )
        assert rag_a.calls[0]["knowledge_scope"] is SCOPE_A
        assert rag_b.calls[0]["knowledge_scope"] is SCOPE_B

    @requires_db
    def test_default_vietnam_wms_behavior_unchanged(self) -> None:
        """§九：默认 vietnam-wms 行为完全不变（语义 / knowledge / schema）。"""
        from backend.app.projects.configuration import (
            get_default_project_configuration_provider,
        )

        cfg = get_default_project_configuration_provider().get(
            settings.project.project_id
        )
        assert cfg.project_id == settings.project.project_id
        assert cfg.schema_name == "public"
        assert cfg.capabilities.tool_names == ("get_inventory",)
        assert cfg.capabilities.knowledge_enabled is True
        assert cfg.capabilities.text_to_sql_enabled is True
        # 默认 LoaderBacked semantic（vietnam-wms.yaml 配置了表语义）
        assert cfg.semantic.tables or cfg.semantic == ProjectSemantic()
        assert cfg.knowledge_scope is not None
        assert cfg.knowledge_scope.namespace == settings.project.project_id
        assert cfg.knowledge_scope.includes_legacy is True

    def test_default_context_still_primary_datasource(self) -> None:
        """默认项目 DataSource 仍是 primary / postgresql（不引入第二套取值）。"""
        cfg_context = get_default_project_context()
        assert cfg_context.data_source.name == "primary"
        assert cfg_context.data_source.type == "postgresql"


# ============================================================
# 5. 安全（§十九）：HTTP 不能注入项目配置
# ============================================================

class TestHttpRequestCannotInjectConfiguration:
    def test_chat_request_ignores_unknown_config_fields(self) -> None:
        """API request model 收到 project_configuration / capabilities /
        semantic / knowledge_scope / datasource → 一律不接受。"""
        from backend.app.api.orchestrator_chat import ChatRequest

        req = ChatRequest(
            question="系统的操作流程是什么",
            project_id="project-a",
            knowledge_scope="project-b",  # type: ignore[call-arg]
            semantic="project-b",  # type: ignore[call-arg]
            datasource="project-b",  # type: ignore[call-arg]
            capabilities="project-b",  # type: ignore[call-arg]
            project_configuration="project-b",  # type: ignore[call-arg]
        )
        assert req.project_id == "project-a"
        for field in (
            "knowledge_scope", "semantic", "datasource", "capabilities",
            "project_configuration",
        ):
            assert not hasattr(req, field)

    def test_factory_signature_has_no_config_injection_params(self) -> None:
        """配置只能来自服务器端 DI（qv_kwargs 之外的 project_id 之外
        没有任何 HTTP 可直达的配置参数）。"""
        import inspect

        from backend.app.services.project_orchestrator_factory import (
            build_orchestrator_for_project,
        )

        params = set(inspect.signature(build_orchestrator_for_project).parameters)
        # 唯一非 DI 的位置参数是 project_id；其余均为 keyword-only 的
        # Provider / base 注入（测试 / 组合用），不含 request 字段
        for forbidden in ("knowledge_scope", "semantic", "capabilities"):
            assert forbidden not in params
        assert set(inspect.signature(
            build_orchestrator_for_project
        ).parameters) == {
            "project_id", "base", "registry", "engine_provider",
            "semantic_provider", "knowledge_provider",
            "configuration_provider",
        }

    @contextmanager
    def _api(self, monkeypatch, provider, registry_semantic):
        """HTTP 层：标准 app + 真实 factory + Fake RAG（不触达 DB / LLM）。"""
        from fastapi.testclient import TestClient

        from backend.app.api import orchestrator_chat as orch_module
        from backend.app.main import app
        from backend.app.services import project_orchestrator_factory as fm

        rag = _RecordingRAG()
        monkeypatch.setattr(fm, "get_default_engine_provider",
                            lambda: _FakeEngineProvider())
        target = orch_module._default_orchestrator
        original = target._rag
        target._rag = rag
        try:
            with TestClient(app) as client:
                yield client, rag
        finally:
            target._rag = original

    def test_http_injection_still_uses_project_a_configuration(
        self, monkeypatch
    ) -> None:
        """恶意 body 携带 knowledge_scope / semantic / datasource =
        project-b → 最终仍是 project-a 的服务器端配置。"""
        from backend.app.services import project_orchestrator_factory as fm

        registry, semantic, knowledge = _providers()
        provider = DefaultProjectConfigurationProvider(
            registry=registry,
            semantic_provider=semantic,
            knowledge_provider=knowledge,
        )
        for name, getter in (
            ("get_default_project_registry", lambda: registry),
            ("get_default_project_semantic_provider", lambda: semantic),
            ("get_default_project_knowledge_provider", lambda: knowledge),
        ):
            monkeypatch.setattr(fm, name, getter)

        with self._api(monkeypatch, provider, None) as (client, rag):
            resp = client.post(
                "/api/ai/chat",
                json={
                    "question": "系统的操作流程是什么",
                    "project_id": "project-a",
                    "knowledge_scope": "project-b",
                    "semantic": "project-b",
                    "datasource": "project-b",
                    "capabilities": "project-b",
                    "project_configuration": "project-b",
                },
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["route"] == "rag"
        assert rag.calls[0]["knowledge_scope"] is SCOPE_A
        # 只解析了 project-a：HTTP 注入没有触发任何其它项目读取
        assert set(semantic.calls) == {"project-a"}
        assert set(knowledge.calls) == {"project-a"}

    def test_http_unknown_project_404_no_fallback(self, monkeypatch) -> None:
        """project-x 未注册 → 404，且不向 Semantic / Knowledge 查询。"""
        from backend.app.services import project_orchestrator_factory as fm

        registry, semantic, knowledge = _providers()
        for name, getter in (
            ("get_default_project_registry", lambda: registry),
            ("get_default_project_semantic_provider", lambda: semantic),
            ("get_default_project_knowledge_provider", lambda: knowledge),
        ):
            monkeypatch.setattr(fm, name, getter)

        with self._api(monkeypatch, None, None) as (client, rag):
            resp = client.post(
                "/api/ai/chat",
                json={"question": "系统的操作流程是什么",
                      "project_id": "project-x"},
            )
        assert resp.status_code == 404, resp.text
        assert semantic.calls == []
        assert knowledge.calls == []
        assert rag.calls == []


__all__ = [
    "TestProjectConfigurationDTO",
    "TestAggregationCases",
    "TestFactoryAssembly",
    "TestCrossProjectConsistency",
    "TestHttpRequestCannotInjectConfiguration",
]
