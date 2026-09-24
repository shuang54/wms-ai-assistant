"""RAG Project Scope 单元链路测试（Phase 3.8.4）。

无 DB / 无网络 / 无 LLM。覆盖任务书 §二十一 中不需要数据库的部分：

    - VectorSearchService：scope 类型校验 / 显式过滤 SQL 形态 /
      scope=None 保持旧行为（无 JOIN）
    - RagService：knowledge_scope 透传给 VectorSearch
    - AIOrchestratorService：knowledge_scope 注入与传递 /
      capability 拒绝时 RAG 0 次调用

真实 PostgreSQL 隔离矩阵见 test_project_knowledge_isolation.py。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from backend.app.config import settings
from backend.app.projects.capabilities import ProjectCapabilities
from backend.app.projects.knowledge_provider import (
    GLOBAL_NAMESPACE,
    ProjectKnowledgeScope,
)
from backend.app.services.ai_orchestrator_service import (
    AIOrchestratorCapabilityError,
    AIOrchestratorInputError,
    AIOrchestratorService,
)
from backend.app.services.ai_router_service import (
    RouteDecision,
    RouteType,
)
from backend.app.services.rag_service import RagService, RagSource
from backend.app.services.vector_search_service import (
    VectorSearchParameterError,
    VectorSearchResult,
    VectorSearchService,
)

SCOPE_A = ProjectKnowledgeScope(project_id="project-a", namespace="project-a")
SCOPE_B = ProjectKnowledgeScope(project_id="project-b", namespace="project-b")
SCOPE_VN = ProjectKnowledgeScope(
    project_id="vietnam-wms",
    namespace="vietnam-wms",
    includes_legacy=True,
)


# ============================================================
# Fake / helpers
# ============================================================

class MockEmbeddingClient:
    """确定性 Mock（无需继承真实 client；鸭子类型即可）。"""

    async def embed(self, text: str) -> list[float]:
        return [0.1] * settings.embedding.dimension


class _RecordingVectorSearch:
    """记录 search 调用参数的 Fake VectorSearchService。"""

    def __init__(self, results=None):
        self.calls: list[dict] = []
        self._results = results or []

    async def search(self, query, *, top_k=5, knowledge_scope=None):
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "knowledge_scope": knowledge_scope,
            }
        )
        return list(self._results)


class _RecordingRag:
    """记录 answer 调用参数的 Fake RagService。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def answer(self, query, *, top_k=None, knowledge_scope=None):
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "knowledge_scope": knowledge_scope,
            }
        )
        from backend.app.services.rag_service import RagResponse

        return RagResponse(answer="FAKE-RAG", sources=(), used_chunks_count=0)


class _RagRouter:
    """强制 RAG 路由的 Stub Router（任务书 §十三 纵深防御测试）。"""

    async def route(self, question, context=None):
        return RouteDecision(
            route=RouteType.RAG,
            confidence=0.9,
            reason="stub: force RAG",
            source="rule",
        )


def _capturing_session_factory(captured: dict):
    """捕获 session.execute(stmt) 的 stmt，返回空结果。"""
    session = MagicMock(name="fake_session")
    session.__enter__.return_value = session
    session.__exit__.return_value = False

    def _execute(stmt, *args, **kwargs):
        captured["stmt"] = stmt
        result = MagicMock()
        result.all.return_value = []
        return result

    session.execute.side_effect = _execute

    def factory() -> MagicMock:
        return session

    return factory


def _result(content: str) -> VectorSearchResult:
    return VectorSearchResult(
        chunk_id=1,
        document_id=10,
        chunk_index=0,
        content=content,
        distance=0.1,
        similarity=0.9,
        metadata={},
    )


# ============================================================
# VectorSearchService：scope 校验与 SQL 形态
# ============================================================

class TestVectorSearchScope:
    async def test_scope_type_validation(self) -> None:
        """scope 类型非法 → VectorSearchParameterError（Embedding 前拦截）。"""
        svc = VectorSearchService(embedding_client=MockEmbeddingClient())
        with pytest.raises(VectorSearchParameterError):
            await svc.search("q", knowledge_scope={"namespace": "x"})  # type: ignore[arg-type]
        with pytest.raises(VectorSearchParameterError):
            await svc.search("q", knowledge_scope="project-a")  # type: ignore[arg-type]

    def test_scope_filter_normal_project(self) -> None:
        """普通项目：namespace OR __global__，无 IS NULL（legacy 不可见）。"""
        cond = VectorSearchService._scope_filter(SCOPE_A)
        sql = str(
            cond.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "project-a" in sql
        assert GLOBAL_NAMESPACE in sql
        assert "IS NULL" not in sql

    def test_scope_filter_legacy_project(self) -> None:
        """vietnam-wms：namespace OR __global__ OR IS NULL（legacy 兼容）。"""
        cond = VectorSearchService._scope_filter(SCOPE_VN)
        sql = str(
            cond.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "vietnam-wms" in sql
        assert GLOBAL_NAMESPACE in sql
        assert "IS NULL" in sql

    def test_scope_filter_excludes_other_namespaces(self) -> None:
        """project-a 的过滤条件不含 project-b / vietnam-wms。"""
        cond = VectorSearchService._scope_filter(SCOPE_A)
        sql = str(
            cond.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "project-b" not in sql
        assert "vietnam-wms" not in sql

    async def test_scope_adds_document_join(self) -> None:
        """有 scope → JOIN knowledge_document（文档级过滤）。"""
        captured: dict = {}
        svc = VectorSearchService(
            embedding_client=MockEmbeddingClient(),
            session_factory=_capturing_session_factory(captured),
        )
        await svc.search("q", knowledge_scope=SCOPE_A)
        sql = str(captured["stmt"])
        assert "knowledge_document" in sql
        assert "knowledge_chunk" in sql

    async def test_no_scope_keeps_legacy_query(self) -> None:
        """scope=None → 旧行为：无 JOIN、无文档过滤。"""
        captured: dict = {}
        svc = VectorSearchService(
            embedding_client=MockEmbeddingClient(),
            session_factory=_capturing_session_factory(captured),
        )
        await svc.search("q")
        sql = str(captured["stmt"])
        assert "knowledge_document" not in sql
        assert "knowledge_chunk" in sql


# ============================================================
# RagService：scope 透传
# ============================================================

class TestRagServiceScopeForwarding:
    async def test_scope_forwarded_to_vector_search(self) -> None:
        recording = _RecordingVectorSearch()
        rag = RagService(vector_search_service=recording)

        await rag.answer("系统的操作流程是什么", knowledge_scope=SCOPE_A)

        assert len(recording.calls) == 1
        assert recording.calls[0]["knowledge_scope"] is SCOPE_A

    async def test_no_scope_forwarded_as_none(self) -> None:
        recording = _RecordingVectorSearch()
        rag = RagService(vector_search_service=recording)

        await rag.answer("系统的操作流程是什么")

        assert recording.calls[0]["knowledge_scope"] is None

    async def test_context_contains_only_scoped_results(self) -> None:
        """检索结果（已被 scope 过滤）→ Context → LLM prompt。"""
        recording = _RecordingVectorSearch(
            results=[_result("A-ONLY-KEYWORD 库存盘点流程")]
        )
        llm = _FakeLLM()
        rag = RagService(vector_search_service=recording, llm_client=llm)

        response = await rag.answer("库存盘点流程", knowledge_scope=SCOPE_A)

        assert response.answer == "FAKE-LLM"
        messages = llm.messages[0][1]
        user_prompt = messages[1]["content"]
        assert "A-ONLY-KEYWORD" in user_prompt
        assert len(response.sources) == 1
        assert isinstance(response.sources[0], RagSource)


class _FakeLLM:
    def __init__(self) -> None:
        self.messages: list[tuple] = []

    async def chat(self, messages, **kwargs):
        self.messages.append(("chat", messages))
        return "FAKE-LLM"


# ============================================================
# AIOrchestratorService：scope 传递与 capability 拒绝
# ============================================================

class TestOrchestratorScope:
    def test_invalid_scope_type(self) -> None:
        with pytest.raises(AIOrchestratorInputError, match="knowledge_scope"):
            AIOrchestratorService(knowledge_scope="project-a")  # type: ignore[arg-type]

    async def test_scope_forwarded_to_rag(self) -> None:
        """注入 scope → RAG 收到同一 scope；metadata 回显 namespace。"""
        rag = _RecordingRag()
        orch = AIOrchestratorService(
            router=_RagRouter(), rag_service=rag, knowledge_scope=SCOPE_A
        )

        result = await orch.execute("系统的操作流程是什么")

        assert result.route == RouteType.RAG
        assert len(rag.calls) == 1
        assert rag.calls[0]["knowledge_scope"] is SCOPE_A
        assert result.metadata["knowledge_scope"] == "project-a"

    async def test_no_scope_uses_legacy_signature(self) -> None:
        """scope=None → 旧调用形态（兼容 answer(query, *, top_k)）。"""
        rag = _RecordingRag()
        orch = AIOrchestratorService(
            router=_RagRouter(), rag_service=rag, knowledge_scope=None
        )

        result = await orch.execute("系统的操作流程是什么")

        assert rag.calls[0]["knowledge_scope"] is None
        assert result.metadata["knowledge_scope"] is None

    async def test_capability_denied_rag_not_called(self) -> None:
        """knowledge_enabled=False：即使 Router 强制 RAG →
        AIOrchestratorCapabilityError，RagService 0 次调用（§十三）。"""
        rag = _RecordingRag()
        orch = AIOrchestratorService(
            router=_RagRouter(),
            rag_service=rag,
            capabilities=ProjectCapabilities(
                tool_names=(),
                knowledge_enabled=False,
                text_to_sql_enabled=False,
            ),
            knowledge_scope=None,  # Factory 在禁用时不解析 scope
        )

        with pytest.raises(AIOrchestratorCapabilityError) as exc_info:
            await orch.execute("系统的操作流程是什么")

        assert exc_info.value.capability == "knowledge"
        assert rag.calls == []


__all__ = [
    "TestVectorSearchScope",
    "TestRagServiceScopeForwarding",
    "TestOrchestratorScope",
]
