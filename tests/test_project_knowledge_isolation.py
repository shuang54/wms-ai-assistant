"""Project Knowledge Context 隔离集成测试（Phase 3.8.4）。

默认 SKIP；``RUN_DB_TESTS=1``（+ DATABASE_URL）后执行。

覆盖任务书验收（§十四 / §十五 / §十六 / §十七 / §十八）：

    知识矩阵（补充要求 §4，最重要的安全测试）：

        Knowledge A      meta_data={"project_id": "project-a"}
        Knowledge B      meta_data={"project_id": "project-b"}
        Knowledge Global meta_data={"project_id": "__global__"}
        Knowledge Legacy meta_data={} / None（历史知识）

        | 请求项目    |  A |  B | Global | Legacy | VN |
        | project-a  | ✅ | ❌ |     ✅ |     ❌ |  ❌ |
        | project-b  | ❌ | ✅ |     ✅ |     ❌ |  ❌ |
        | vietnam-wms| ❌ | ❌ |     ✅ |     ✅ |  ✅ |

    - VectorSearch：scope 显式过滤，绝不跨项目
    - RagService：A → A context / B → B context（同一问题双项目）
    - Factory：project_id → KnowledgeScope（InMemory Provider）
    - API E2E：project-a → Knowledge A / project-b → Knowledge B /
      unknown → 404 / 注入字段被忽略 / 未注册知识 503 / 能力禁用 403
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.config import settings
from backend.app.db.models import KnowledgeChunk, KnowledgeDocument
from backend.app.embedding.client import EmbeddingClient
from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.projects.knowledge_provider import (
    ProjectKnowledgeScope,
    ProjectKnowledgeScopeError,
)
from backend.app.projects.models import DataSource, ProjectContext
from backend.app.projects.registry import (
    InMemoryProjectRegistry,
    ProjectRegistration,
)
from backend.app.services.rag_service import RagService
from backend.app.services.vector_search_service import VectorSearchService

import backend.app.services.project_orchestrator_factory as factory_module


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not (_env_flag("RUN_DB_TESTS") and settings.database.url.strip()),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable project "
    "knowledge isolation tests",
)


# ============================================================
# 关键词 → 基向量 映射（确定性 Embedding，无网络）
# ============================================================

KEYWORD_INDEX: dict[str, int] = {
    "A-ONLY-KEYWORD": 0,
    "B-ONLY-KEYWORD": 1,
    "GLOBAL-ONLY-KEYWORD": 2,
    "LEGACY-EMPTY-KEYWORD": 3,
    "LEGACY-NULL-KEYWORD": 4,
    "VN-ONLY-KEYWORD": 5,
    "流程": 6,
}


def _kw_vector(*keywords: str) -> list[float]:
    vec = [0.0] * settings.embedding.dimension
    for kw in keywords:
        vec[KEYWORD_INDEX[kw]] = 1.0
    return vec


class KeywordEmbeddingClient(EmbeddingClient):
    """按关键词生成基向量的确定性 Embedding（无网络 / 无 API）。"""

    async def embed(self, text_value: str) -> list[float]:
        return _kw_vector(*[kw for kw in KEYWORD_INDEX if kw in text_value])


class FakeLLMClient:
    """记录 messages 的 Fake LLM（不调真实 DeepSeek，任务书 §十六）。"""

    def __init__(self) -> None:
        self.chats: list[list[dict]] = []

    async def chat(self, messages, **kwargs):
        self.chats.append([dict(m) for m in messages])
        return "FAKE-LLM-ANSWER"


class _RecordingKnowledgeProvider:
    """记录 get_scope 调用次数的 Provider 包装（验证 §十三 不执行）。"""

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


class _RecordingRag:
    """记录 answer 调用（含 knowledge_scope）的 Fake RagService。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def answer(self, query, *, top_k=None, knowledge_scope=None):
        from backend.app.services.rag_service import RagResponse

        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "knowledge_scope": knowledge_scope,
            }
        )
        return RagResponse(
            answer="FAKE-RAG", sources=(), used_chunks_count=0
        )


# ============================================================
# Scope / 知识数据定义
# ============================================================

SCOPE_A = ProjectKnowledgeScope(project_id="project-a", namespace="project-a")
SCOPE_B = ProjectKnowledgeScope(project_id="project-b", namespace="project-b")
SCOPE_VN = ProjectKnowledgeScope(
    project_id="vietnam-wms",
    namespace="vietnam-wms",
    includes_legacy=True,
)

#: 隔离矩阵：scope → 允许命中的文档关键词（补充要求 §4）
EXPECTED_MATRIX: dict[str, set[str]] = {
    "project-a": {"A-ONLY-KEYWORD", "GLOBAL-ONLY-KEYWORD"},
    "project-b": {"B-ONLY-KEYWORD", "GLOBAL-ONLY-KEYWORD"},
    "vietnam-wms": {
        "VN-ONLY-KEYWORD",
        "GLOBAL-ONLY-KEYWORD",
        "LEGACY-EMPTY-KEYWORD",
        "LEGACY-NULL-KEYWORD",
    },
}

#: 全部知识文档：(标题, 关键词, meta_data)
KNOWLEDGE_DOCS: list[tuple[str, str, dict | None]] = [
    ("project-a 库存盘点操作", "A-ONLY-KEYWORD", {"project_id": "project-a"}),
    ("project-b 生产报工操作", "B-ONLY-KEYWORD", {"project_id": "project-b"}),
    ("公共操作规范", "GLOBAL-ONLY-KEYWORD", {"project_id": "__global__"}),
    # legacy 两种形态：空 dict 与 NULL
    ("历史知识-空meta", "LEGACY-EMPTY-KEYWORD", {}),
    ("历史知识-NULL-meta", "LEGACY-NULL-KEYWORD", None),
    ("vietnam-wms 项目知识", "VN-ONLY-KEYWORD", {"project_id": "vietnam-wms"}),
]

QUESTION_GENERIC = "系统的操作流程是什么"

CAPS_RAG = ProjectCapabilities(
    tool_names=(),
    knowledge_enabled=True,
    text_to_sql_enabled=False,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module")
def engine():
    from backend.app.db import reset_engine_cache
    from backend.app.db.base import Base
    from backend.app.db.session import get_engine

    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置"

    from backend.app.db import models  # noqa: F401  # register models

    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture
def _truncate_after_each(engine):
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE knowledge_chunk, knowledge_document "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def db(_truncate_after_each, engine):
    from backend.app.db.session import get_session_factory

    factory = get_session_factory()
    session = factory()
    try:
        yield session
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
    finally:
        session.close()


@pytest.fixture
def seeded(db):
    """写入全部矩阵知识文档（每 chunk 一条，带确定性 embedding）。"""
    for title, keyword, meta_data in KNOWLEDGE_DOCS:
        content = f"{title}操作流程 {keyword}"
        doc = KnowledgeDocument(
            title=title,
            file_name=f"{keyword}.md",
            file_type="md",
            source="test",
            content_hash=hashlib.sha256(keyword.encode("utf-8")).hexdigest(),
            status="ready",
            meta_data=meta_data,
        )
        db.add(doc)
        db.flush()
        db.add(
            KnowledgeChunk(
                document_id=doc.id,
                chunk_index=0,
                content=content,
                content_hash=hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                embedding=_kw_vector(keyword, "流程"),
                meta_data={},
            )
        )
    db.commit()
    yield db


def _search_service() -> VectorSearchService:
    return VectorSearchService(embedding_client=KeywordEmbeddingClient())


def _hit_keywords(results) -> set[str]:
    joined = " ".join(r.content for r in results)
    return {kw for kw in KEYWORD_INDEX if kw != "流程" and kw in joined}


# ============================================================
# §十四 / §4：VectorSearch 隔离矩阵（最重要的安全测试）
# ============================================================

@requires_db
class TestVectorSearchKnowledgeMatrix:
    @pytest.mark.parametrize(
        "scope,expected",
        [
            (SCOPE_A, EXPECTED_MATRIX["project-a"]),
            (SCOPE_B, EXPECTED_MATRIX["project-b"]),
            (SCOPE_VN, EXPECTED_MATRIX["vietnam-wms"]),
        ],
        ids=["project-a", "project-b", "vietnam-wms"],
    )
    async def test_matrix(self, seeded, scope, expected) -> None:
        """scope → 命中且仅命中允许的知识（A/B/Global/Legacy/VN）。"""
        results = await _search_service().search(
            QUESTION_GENERIC, top_k=10, knowledge_scope=scope
        )
        assert results, "scope 内应有知识命中"
        hits = _hit_keywords(results)
        assert hits == expected

    async def test_project_a_cannot_hit_b_keyword(self, seeded) -> None:
        """project-a 查询 B 专属关键词 → 不能命中 B（跨项目阻断）。"""
        results = await _search_service().search(
            "B-ONLY-KEYWORD", top_k=10, knowledge_scope=SCOPE_A
        )
        joined = " ".join(r.content for r in results)
        assert "B-ONLY-KEYWORD" not in joined
        # 返回的全部在 A 允许范围内
        assert _hit_keywords(results) <= EXPECTED_MATRIX["project-a"]

    async def test_project_b_cannot_hit_a_keyword(self, seeded) -> None:
        """project-b 查询 A 专属关键词 → 不能命中 A。"""
        results = await _search_service().search(
            "A-ONLY-KEYWORD", top_k=10, knowledge_scope=SCOPE_B
        )
        joined = " ".join(r.content for r in results)
        assert "A-ONLY-KEYWORD" not in joined
        assert _hit_keywords(results) <= EXPECTED_MATRIX["project-b"]

    async def test_normal_projects_cannot_hit_legacy(self, seeded) -> None:
        """legacy（NULL / 空 meta）仅 vietnam-wms 可见（§2 兼容不泄漏）。"""
        for scope in (SCOPE_A, SCOPE_B):
            results = await _search_service().search(
                "LEGACY-EMPTY-KEYWORD LEGACY-NULL-KEYWORD",
                top_k=10,
                knowledge_scope=scope,
            )
            joined = " ".join(r.content for r in results)
            assert "LEGACY-EMPTY-KEYWORD" not in joined
            assert "LEGACY-NULL-KEYWORD" not in joined

    async def test_vietnam_wms_sees_legacy(self, seeded) -> None:
        """vietnam-wms 继续检索历史 Knowledge（向后兼容）。"""
        results = await _search_service().search(
            "LEGACY-EMPTY-KEYWORD LEGACY-NULL-KEYWORD",
            top_k=10,
            knowledge_scope=SCOPE_VN,
        )
        joined = " ".join(r.content for r in results)
        assert "LEGACY-EMPTY-KEYWORD" in joined
        assert "LEGACY-NULL-KEYWORD" in joined
        # 但 A / B / VN 之外的其它项目知识不可见
        assert "A-ONLY-KEYWORD" not in joined
        assert "B-ONLY-KEYWORD" not in joined

    async def test_no_scope_keeps_legacy_global_behavior(
        self, seeded
    ) -> None:
        """scope=None（历史端点路径）：全库检索，旧行为不变。"""
        results = await _search_service().search(
            QUESTION_GENERIC, top_k=10
        )
        assert _hit_keywords(results) == {
            kw for _, kw, _ in KNOWLEDGE_DOCS
        }


# ============================================================
# §十五 / §十六：RAG Context 隔离（同一问题双项目）
# ============================================================

@requires_db
class TestRagContextIsolation:
    def _rag(self) -> tuple[RagService, FakeLLMClient]:
        llm = FakeLLMClient()
        rag = RagService(
            vector_search_service=_search_service(),
            llm_client=llm,
        )
        return rag, llm

    def _user_prompt(self, llm: FakeLLMClient) -> str:
        assert llm.chats, "RAG 应调用 LLM"
        return llm.chats[0][1]["content"]

    def test_same_question_project_a_context(self, seeded) -> None:
        """同一个问题：project-a → Knowledge A 专属 context。"""
        rag, llm = self._rag()
        response = asyncio.run(
            rag.answer(QUESTION_GENERIC, knowledge_scope=SCOPE_A)
        )
        assert response.answer == "FAKE-LLM-ANSWER"
        prompt = self._user_prompt(llm)
        assert "A-ONLY-KEYWORD" in prompt
        assert "GLOBAL-ONLY-KEYWORD" in prompt
        # 不含 B / legacy / VN
        assert "B-ONLY-KEYWORD" not in prompt
        assert "LEGACY-EMPTY-KEYWORD" not in prompt
        assert "LEGACY-NULL-KEYWORD" not in prompt
        assert "VN-ONLY-KEYWORD" not in prompt

    def test_same_question_project_b_context(self, seeded) -> None:
        """同一个问题：project-b → Knowledge B 专属 context。"""
        rag, llm = self._rag()
        asyncio.run(
            rag.answer(QUESTION_GENERIC, knowledge_scope=SCOPE_B)
        )
        prompt = self._user_prompt(llm)
        assert "B-ONLY-KEYWORD" in prompt
        assert "GLOBAL-ONLY-KEYWORD" in prompt
        assert "A-ONLY-KEYWORD" not in prompt
        assert "LEGACY-EMPTY-KEYWORD" not in prompt
        assert "VN-ONLY-KEYWORD" not in prompt

    def test_same_question_vietnam_wms_context(self, seeded) -> None:
        """同一个问题：vietnam-wms → legacy + VN 知识。"""
        rag, llm = self._rag()
        asyncio.run(
            rag.answer(QUESTION_GENERIC, knowledge_scope=SCOPE_VN)
        )
        prompt = self._user_prompt(llm)
        assert "VN-ONLY-KEYWORD" in prompt
        assert "LEGACY-EMPTY-KEYWORD" in prompt
        assert "GLOBAL-ONLY-KEYWORD" in prompt
        assert "A-ONLY-KEYWORD" not in prompt
        assert "B-ONLY-KEYWORD" not in prompt

    def test_sources_follow_scope(self, seeded) -> None:
        """sources 与 context 一致：A 只含 A + Global 的 chunk。"""
        rag, _ = self._rag()
        response = asyncio.run(
            rag.answer(QUESTION_GENERIC, knowledge_scope=SCOPE_A)
        )
        contents = " ".join(s.content for s in response.sources)
        assert "A-ONLY-KEYWORD" in contents
        assert "B-ONLY-KEYWORD" not in contents


# ============================================================
# §十一 Factory：project_id → KnowledgeScope 接线
# ============================================================

def _registration(project_id: str, capabilities: ProjectCapabilities):
    return ProjectRegistration(
        context=ProjectContext(
            project_id=project_id,
            project_name=f"Project {project_id}",
            description=None,
            data_source=DataSource(name="primary", type="postgresql"),
        ),
        schema_name="public",
        capabilities=capabilities,
    )


@contextmanager
def _test_registry():
    registry = InMemoryProjectRegistry()
    registry.register("project-a", _registration("project-a", CAPS_RAG))
    registry.register("project-b", _registration("project-b", CAPS_RAG))
    registry.register("vietnam-wms", _registration("vietnam-wms", CAPS_RAG))
    registry.register(
        "project-no-rag",
        _registration(
            "project-no-rag",
            ProjectCapabilities(
                tool_names=(),
                knowledge_enabled=False,
                text_to_sql_enabled=False,
            ),
        ),
    )
    yield registry


def _knowledge_provider() -> _RecordingKnowledgeProvider:
    return _RecordingKnowledgeProvider(
        {
            "project-a": SCOPE_A,
            "project-b": SCOPE_B,
            "vietnam-wms": SCOPE_VN,
            # project-no-rag 故意不注册（验证禁用时 Provider 不执行）
        }
    )


def _build(project_id: str, rag, provider):
    from backend.app.api import orchestrator_chat as orch_module
    from backend.app.services.project_orchestrator_factory import (
        build_orchestrator_for_project,
    )

    # Fake RagService 必须在 build **之前**挂到 base 单例上
    # （factory 在构造时拷贝 base._rag 的引用；与 3.8.3 的
    # _text_to_sql 注入模式一致）
    target = orch_module._default_orchestrator
    original = target._rag
    target._rag = rag
    try:
        with _test_registry() as registry:
            orch = build_orchestrator_for_project(
                project_id,
                base=orch_module._default_orchestrator,
                registry=registry,
                knowledge_provider=provider,
            )
    finally:
        target._rag = original
    return orch


@requires_db
class TestFactoryKnowledgeWiring:
    def test_project_a_gets_scope_a(self) -> None:
        rag = _RecordingRag()
        orch = _build("project-a", rag, _knowledge_provider())

        result = asyncio.run(orch.execute(QUESTION_GENERIC))

        assert result.route.value == "rag"
        assert len(rag.calls) == 1
        assert rag.calls[0]["knowledge_scope"] is SCOPE_A
        assert result.metadata["knowledge_scope"] == "project-a"

    def test_project_b_gets_scope_b(self) -> None:
        rag = _RecordingRag()
        orch = _build("project-b", rag, _knowledge_provider())

        result = asyncio.run(orch.execute(QUESTION_GENERIC))

        assert rag.calls[0]["knowledge_scope"] is SCOPE_B
        assert result.metadata["knowledge_scope"] == "project-b"

    def test_same_question_different_scopes(self) -> None:
        """同一个问题：A → Scope A，B → Scope B（§十五）。"""
        rag = _RecordingRag()
        orch_a = _build("project-a", rag, _knowledge_provider())
        asyncio.run(orch_a.execute(QUESTION_GENERIC))

        orch_b = _build("project-b", rag, _knowledge_provider())
        asyncio.run(orch_b.execute(QUESTION_GENERIC))

        assert rag.calls[0]["knowledge_scope"] is SCOPE_A
        assert rag.calls[1]["knowledge_scope"] is SCOPE_B

    def test_unregistered_knowledge_503(self) -> None:
        """显式 Provider 下项目知识未注册 → clear error（不回退他人，
        §十八-2）。"""
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorUnavailableError,
        )
        from backend.app.services.project_orchestrator_factory import (
            build_orchestrator_for_project,
        )
        from backend.app.api import orchestrator_chat as orch_module

        provider = _RecordingKnowledgeProvider({"project-a": SCOPE_A})
        with _test_registry() as registry:
            with pytest.raises(AIOrchestratorUnavailableError, match="知识库"):
                build_orchestrator_for_project(
                    "project-b",
                    base=orch_module._default_orchestrator,
                    registry=registry,
                    knowledge_provider=provider,
                )
        # project-b 的 scope 解析失败，且未触碰其它项目
        assert provider.calls == ["project-b"]

    def test_capability_disabled_provider_not_executed(self) -> None:
        """knowledge_enabled=False：KnowledgeProvider / RagService
        均不执行（§十三），即使 Router 兜底落到 RAG 也被 403 拦截。"""
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorCapabilityError,
        )

        rag = _RecordingRag()
        provider = _knowledge_provider()
        orch = _build("project-no-rag", rag, provider)

        with pytest.raises(AIOrchestratorCapabilityError):
            asyncio.run(orch.execute(QUESTION_GENERIC))

        assert provider.calls == []  # Provider 0 次执行
        assert rag.calls == []      # RagService 0 次调用

    def test_default_provider_vietnam_wms_legacy_scope(self) -> None:
        """默认 Provider：vietnam-wms → legacy 兼容 scope（显式表达）。"""
        rag = _RecordingRag()
        from backend.app.api import orchestrator_chat as orch_module
        from backend.app.services.project_orchestrator_factory import (
            build_orchestrator_for_project,
        )

        target = orch_module._default_orchestrator
        original = target._rag
        target._rag = rag
        try:
            with _test_registry() as registry:
                orch = build_orchestrator_for_project(
                    "vietnam-wms",
                    base=orch_module._default_orchestrator,
                    registry=registry,
                )  # 不传 knowledge_provider → DefaultProjectKnowledgeProvider
        finally:
            target._rag = original

        result = asyncio.run(orch.execute(QUESTION_GENERIC))

        scope = rag.calls[0]["knowledge_scope"]
        assert scope.namespace == "vietnam-wms"
        assert scope.includes_legacy is True
        assert result.metadata["knowledge_scope"] == "vietnam-wms"


# ============================================================
# §十七 / §十八：API E2E + 安全
# ============================================================

@requires_db
class TestApiKnowledgeE2E:
    @contextmanager
    def _api(self, monkeypatch, knowledge_provider):
        from backend.app.api import orchestrator_chat as orch_module
        from backend.app.main import app

        llm = FakeLLMClient()
        rag = RagService(
            vector_search_service=_search_service(),
            llm_client=llm,
        )
        with _test_registry() as registry:
            monkeypatch.setattr(
                factory_module, "get_default_project_registry", lambda: registry
            )
            monkeypatch.setattr(
                factory_module,
                "get_default_project_knowledge_provider",
                lambda: knowledge_provider,
            )
            target = orch_module._default_orchestrator
            original = target._rag
            target._rag = rag
            try:
                with TestClient(app) as client:
                    yield client, llm
            finally:
                target._rag = original

    def _post(self, client, project_id, question=QUESTION_GENERIC, **extra):
        return client.post(
            "/api/ai/chat",
            json={"question": question, "project_id": project_id, **extra},
        )

    def test_api_project_a_knowledge_a(self, monkeypatch, seeded) -> None:
        """project-a → Knowledge A（context 只含 A + Global）。"""
        with self._api(monkeypatch, _knowledge_provider()) as (client, llm):
            resp = self._post(client, "project-a")
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["route"] == "rag"
        assert payload["content"] == "FAKE-LLM-ANSWER"
        assert payload["metadata"]["knowledge_scope"] == "project-a"
        prompt = llm.chats[0][1]["content"]
        assert "A-ONLY-KEYWORD" in prompt
        assert "GLOBAL-ONLY-KEYWORD" in prompt
        assert "B-ONLY-KEYWORD" not in prompt

    def test_api_project_b_knowledge_b(self, monkeypatch, seeded) -> None:
        """project-b → Knowledge B（context 只含 B + Global）。"""
        with self._api(monkeypatch, _knowledge_provider()) as (client, llm):
            resp = self._post(client, "project-b")
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["metadata"]["knowledge_scope"] == "project-b"
        prompt = llm.chats[0][1]["content"]
        assert "B-ONLY-KEYWORD" in prompt
        assert "A-ONLY-KEYWORD" not in prompt
        assert "LEGACY-EMPTY-KEYWORD" not in prompt

    def test_api_same_question_different_projects(
        self, monkeypatch, seeded
    ) -> None:
        """同一问题：A / B 分别拿到各自知识（§十五）。"""
        with self._api(monkeypatch, _knowledge_provider()) as (client, llm):
            resp_a = self._post(client, "project-a")
            prompt_a = llm.chats[-1][1]["content"]
            resp_b = self._post(client, "project-b")
            prompt_b = llm.chats[-1][1]["content"]

        assert resp_a.status_code == resp_b.status_code == 200
        assert "A-ONLY-KEYWORD" in prompt_a and "B-ONLY-KEYWORD" not in prompt_a
        assert "B-ONLY-KEYWORD" in prompt_b and "A-ONLY-KEYWORD" not in prompt_b

    def test_api_http_injection_ignored(self, monkeypatch, seeded) -> None:
        """注入 knowledge_scope / knowledge_id / knowledge_file /
        knowledge_namespace / knowledge_project_id → 全部被忽略，
        仍是 project-a Knowledge（§十八-3）。"""
        with self._api(monkeypatch, _knowledge_provider()) as (client, llm):
            resp = self._post(
                client,
                "project-a",
                knowledge_scope="project-b",
                knowledge_id="project-b",
                knowledge_file="project-b.md",
                knowledge_namespace="project-b",
                knowledge_project_id="project-b",
            )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        # 最终仍由 project_id 决定 Knowledge Scope
        assert payload["metadata"]["knowledge_scope"] == "project-a"
        prompt = llm.chats[0][1]["content"]
        assert "A-ONLY-KEYWORD" in prompt
        assert "B-ONLY-KEYWORD" not in prompt

    def test_api_unknown_project_404(self, monkeypatch, seeded) -> None:
        """未知项目 → 404，绝不 fallback 到 vietnam-wms（§十八-1）。"""
        with self._api(monkeypatch, _knowledge_provider()) as (client, llm):
            resp = self._post(client, "ghost-project")
        assert resp.status_code == 404, resp.text
        assert llm.chats == []  # 未触达 RAG / LLM

    def test_api_unregistered_knowledge_503(
        self, monkeypatch, seeded
    ) -> None:
        """项目已注册但知识 scope 未注册 → 503（不回退其他项目知识）。"""
        provider = _RecordingKnowledgeProvider({"project-a": SCOPE_A})
        with self._api(monkeypatch, provider) as (client, llm):
            resp = self._post(client, "project-b")
        assert resp.status_code == 503, resp.text
        assert "知识库" in resp.json()["detail"]
        assert llm.chats == []

    def test_api_capability_disabled_403(self, monkeypatch, seeded) -> None:
        """knowledge_enabled=False → 403；Provider / RAG / LLM 全部未执行。"""
        provider = _knowledge_provider()  # project-no-rag 未注册 scope
        with self._api(monkeypatch, provider) as (client, llm):
            resp = self._post(client, "project-no-rag")
        assert resp.status_code == 403, resp.text
        assert provider.calls == []  # Provider 0 次执行（Factory 跳过）
        assert llm.chats == []       # LLM 0 次调用


__all__ = [
    "TestVectorSearchKnowledgeMatrix",
    "TestRagContextIsolation",
    "TestFactoryKnowledgeWiring",
    "TestApiKnowledgeE2E",
]
