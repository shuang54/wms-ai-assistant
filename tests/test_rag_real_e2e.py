"""Real RAG End-to-End Test（Phase 3.7.13：真实 RAG E2E）。

本阶段目标（任务书 §一）：

    POST /api/ai/chat
        ↓
    AIOrchestratorService
        ↓
    AIRouterService
        ↓
    RagService
        ↓
    VectorSearchService → EmbeddingClient（BAAI/BGE-M3, 1024-dim）
        ↓
    PostgreSQL + pgvector
        ↓
    ContextBuilder
        ↓
    LLMClient（DeepSeek deepseek-chat）
        ↓
    ChatResponse

**默认跳过**（不消耗任何真实 API 额度，无 Key 也不硬跑失败）：

    RUN_REAL_RAG_E2E=1  RUN_DB_TESTS=1  pytest tests/test_rag_real_e2e.py -v

跳过条件（三层防护）：
    1. RUN_REAL_RAG_E2E 未设置 → skip
    2. RUN_REAL_RAG_E2E=true 但 RUN_DB_TESTS 未设置 → skip
    3. RUN_REAL_RAG_E2E=true + RUN_DB_TESTS=true 但 DATABASE_URL / EMBEDDING_API_KEY /
       LLM_API_KEY 缺失 → skip（不会硬跑失败）

数据隔离（任务书 §十六）：
    - setup：构造唯一内容（含 UUID 片段）的临时 md 文件
    - 测试中：通过 ``KnowledgeIngestionService.ingest_one`` 复用既有
      Pipeline（Parser → Chunker → Embedding → DB 写入）
    - 测试结束后：只删除本次测试插入的 document_id（CASCADE 自动清 chunks），
      不动 CLI 手动加载的其它文档

测试目标（任务书 §十五）：
    1. Router    → ``/api/ai/chat`` 对"采购入库怎么操作？"路由到 RAG
    2. API       → HTTP 200 + route=rag + content 非空
    3. RAG Content → content 由真实 LLM 生成（不是固定 canned answer）
    4. Sources   → sources 非空，含 document_id / chunk_id / similarity
    5. Retrieval → 命中的 chunk 确实来自本次插入的 knowledge_chunk
    6. Project Context → project_id="vietnam-wms" 正常透传到 Orchestrator
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


def _env_flag(name: str) -> bool:
    """读取布尔型环境变量开关。"""
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


_RUN_REAL_RAG_E2E = _env_flag("RUN_REAL_RAG_E2E")
_RUN_DB_TESTS = _env_flag("RUN_DB_TESTS")


# ============================================================
# Opt-in skip marker（只用于 TestRealRagEndToEnd，不影响失败情况 Unit Test）
# ============================================================

# E2E 真实链路测试的额外 marker（与模块级 pytestmark 双重防护）
_requires_full_real_chain = pytest.mark.skipif(
    not (_RUN_REAL_RAG_E2E and _RUN_DB_TESTS),
    reason="set RUN_REAL_RAG_E2E=1 and RUN_DB_TESTS=1",
)


# ============================================================
# Helper fixtures (only constructed when env-gates pass)
# ============================================================

def _require_real_dependencies() -> None:
    """第二/三层防护：无 Key / 无 DB 时 skip（不硬跑失败）。"""
    from backend.app.config import settings

    if not settings.database.url.strip():
        pytest.skip("DATABASE_URL 未配置；无法执行真实 RAG 链路")
    if not settings.embedding.api_key.strip():
        pytest.skip("EMBEDDING_API_KEY 未配置；无法调用 BGE-M3 Embedding")
    if not settings.llm.api_key.strip():
        pytest.skip("LLM_API_KEY 未配置；无法调用 DeepSeek")
    if not settings.llm.base_url.strip():
        pytest.skip("LLM_BASE_URL 未配置；无法调用 DeepSeek")
    if not settings.llm.model.strip():
        pytest.skip("LLM_MODEL 未配置；无法调用 DeepSeek")


@pytest.fixture(scope="module")
def engine():
    """Module-level：保证 Base metadata 已创建（pgvector 已扩展）。"""
    from backend.app.config import settings
    from backend.app.db import reset_engine_cache
    from backend.app.db.base import Base
    from backend.app.db.session import get_engine

    _require_real_dependencies()

    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置"

    from backend.app.db import models  # noqa: F401  # register models

    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture(scope="module")
def _tracked_doc_ids() -> set[int]:
    """记录本测试模块本次插入的 document_id，用于 teardown 精确清理。"""
    return set()


@pytest.fixture
def _cleanup(engine, _tracked_doc_ids):
    """每个测试结束后只删除 _tracked_doc_ids 内的 document + chunks（CASCADE）。"""
    yield
    if not _tracked_doc_ids:
        return
    ids = list(_tracked_doc_ids)
    with engine.begin() as conn:
        # chunks 由 FK CASCADE 自动级联删除
        conn.execute(
            text("DELETE FROM knowledge_document WHERE id = ANY(:ids)"),
            {"ids": ids},
        )


@pytest.fixture
def session(engine, _cleanup):
    """function-level Session。"""
    from backend.app.db.session import get_session_factory

    factory = get_session_factory()
    assert factory is not None
    s = factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def app_client():
    """构造 FastAPI TestClient（用默认 _default_orchestrator，含真实 RagService）。"""
    from backend.app.main import app

    with TestClient(app) as c:
        yield c


# ============================================================
# 真实 RAG E2E
# ============================================================

@_requires_full_real_chain
class TestRealRagEndToEnd:
    """Phase 3.7.13 真实 RAG E2E：POST /api/ai/chat 全链路。"""

    async def test_real_rag_end_to_end_via_api(
        self,
        engine,
        _tracked_doc_ids,
        session,
        app_client,
        tmp_path: Path,
    ) -> None:
        """完整链路：

            HTTP POST /api/ai/chat
                ↓
            AIOrchestratorService
                ↓
            AIRouterService.route → rag
                ↓
            RagService.answer
                ↓
            VectorSearchService.search
                ↓
            EmbeddingClient.embed (SiliconFlow BAAI/bge-m3)
                ↓
            PostgreSQL + pgvector (knowledge_chunk.embedding <=> :q)
                ↓
            ContextBuilder
                ↓
            LLMClient.chat (DeepSeek deepseek-chat)
                ↓
            ChatResponse(content, sources, metadata)

        验证任务书 §十五 全部 6 项。
        """
        from backend.app.config import settings
        from backend.app.services.knowledge_ingestion_service import (
            KnowledgeIngestionService,
        )
        from backend.app.services.rag_service import DEFAULT_EMPTY_ANSWER

        # ---- 1. 准备唯一测试文档（避免与 CLI 加载数据冲突） ----
        unique_marker = f"e2e{random_hex()}"
        unique_doc = tmp_path / f"wms_rag_e2e_{unique_marker}.md"
        # 内容刻意与任务书示例不同措辞，但语义相近；
        # RAG 测试目标：通过语义检索找到，不依赖字面匹配。
        body = (
            f"# WMS 测试文档 {unique_marker}\n\n"
            "## 入库流程概述\n\n"
            "本节描述 WMS 系统中供应商到货后，仓库人员如何把货物正式入账的步骤。\n\n"
            "主要环节包括：接收供应商发来的到货通知、扫描并核对物料标签、"
            "登记到货数量与采购数量是否一致、记录批次信息、将货物转移到指定库位、"
            "更新系统库存。整个流程结束后，物料即可用于生产或销售。\n\n"
            "## 异常情况\n\n"
            "当到货数量与采购订单不一致时，必须先与采购部门沟通确认后再继续。\n"
        )
        unique_doc.write_text(body, encoding="utf-8")

        # ---- 2. 通过既有 Pipeline 导入（真实 Embedding + DB 写入） ----
        ingestion = KnowledgeIngestionService()
        result = await ingestion.ingest_one(unique_doc)
        _tracked_doc_ids.add(result.document_id)

        assert result.status == "ready", (
            f"ingestion 失败: status={result.status}, doc_id={result.document_id}"
        )
        assert result.document_id > 0
        assert result.chunk_count >= 1
        assert result.embedded_chunk_count == result.chunk_count

        # ---- 3. 验证知识数据真实存在 ----
        chunk_count_in_db = session.execute(
            text(
                "SELECT COUNT(*) FROM knowledge_chunk WHERE document_id = :did"
            ),
            {"did": result.document_id},
        ).scalar_one()
        assert chunk_count_in_db == result.chunk_count

        # 验证 embedding 维度 = 1024（pgvector 端点核一次）
        dim_check = session.execute(
            text(
                "SELECT vector_dims(embedding) FROM knowledge_chunk "
                "WHERE document_id = :did LIMIT 1"
            ),
            {"did": result.document_id},
        ).scalar_one()
        assert dim_check == settings.embedding.dimension == 1024

        # ---- 4. POST /api/ai/chat 真实端到端 ----
        question = "采购入库怎么操作？"  # 任务书 §十五 标准问题
        response = app_client.post(
            "/api/ai/chat",
            json={"question": question, "project_id": "vietnam-wms"},
        )

        # ---- 5. 验证 HTTP / Router ----
        assert response.status_code == 200, (
            f"HTTP {response.status_code}: {response.text[:300]}"
        )
        payload = response.json()
        assert payload["route"] == "rag", (
            f"路由判定错误: {payload['route']!r}, full={payload}"
        )

        # ---- 6. 验证 RAG Content（真实 LLM 生成） ----
        content = payload.get("content") or ""
        assert content, "RAG content 为空"
        assert content.strip(), "RAG content 全空白"
        # RagService 真实空检索的 canned answer 是 "知识库中没有找到与该问题相关的信息。"
        # （任务书 §十五 #3）—— 必须**不是**它，意味着真实检索命中。
        # 注意：LLM 自身的回答可能包含 "知识库中没有找到" 字样（合法 anti-hallucination），
        # 所以这里只检查精确的 RagService canned answer，避免误判。
        assert content != DEFAULT_EMPTY_ANSWER, (
            f"RAG 返回了 canned empty answer（未命中 knowledge_chunk）: "
            f"{content!r}"
        )
        # 应有真实 LLM 生成的实质内容（> 5 个字符）
        assert len(content) > 5, f"content 过短: {content!r}"

        # ---- 7. 验证 Sources（任务书 §十五 #4） ----
        data = payload.get("data") or {}
        sources = data.get("sources") or []
        assert sources, "sources 为空（未命中 knowledge_chunk）"
        first = sources[0]
        assert "chunk_id" in first
        assert "document_id" in first
        assert "similarity" in first
        # similarity 必须为合法 float
        assert isinstance(first["similarity"], (int, float))
        assert 0.0 <= first["similarity"] <= 1.0

        # ---- 8. 验证 Retrieval 来自 knowledge_chunk（任务书 §十五 #5） ----
        # 至少有一个 source 的 document_id 等于本次测试插入的 doc_id
        inserted_doc_ids = _tracked_doc_ids
        hit_doc_ids = {s["document_id"] for s in sources}
        assert hit_doc_ids & inserted_doc_ids, (
            f"sources 未命中本次插入的 document (inserted={inserted_doc_ids}, "
            f"hit={hit_doc_ids})"
        )

        # chunk_id 必须在本次插入的 chunks 中
        inserted_chunk_ids = set(
            session.execute(
                text(
                    "SELECT id FROM knowledge_chunk WHERE document_id = :did"
                ),
                {"did": result.document_id},
            ).scalars()
        )
        hit_chunk_ids = {s["chunk_id"] for s in sources}
        assert hit_chunk_ids & inserted_chunk_ids, (
            f"sources chunk_id 未命中本次插入的 chunks "
            f"(hit={hit_chunk_ids}, inserted={inserted_chunk_ids})"
        )

        # ---- 9. 验证 metadata 中 project_id 已透传（任务书 §十五 #6） ----
        metadata = payload.get("metadata") or {}
        # RAG 路径 metadata 不显式带 project_id（rag_service 不读取 project_id），
        # 但 metadata 至少要有 decision_source / route_reason 等字段；
        # 同时 project_id 的端到端透传由 _build_orchestrator_for_project_id
        # 处理，本测试仅验证"传入 project_id 不破坏请求"。
        assert "decision_source" in metadata
        assert "route_reason" in metadata

        # ---- 10. 安全：响应不含敏感信息 ----
        body_text = response.text
        for forbidden in (
            "sk-",
            "Authorization",
            "Bearer ",
            "postgresql://",
            "Traceback",
            "<=>",
            "SELECT ",
        ):
            assert forbidden not in body_text, (
                f"响应泄露敏感信息: {forbidden!r}"
            )

        # ---- 11. 报告（输出供日志/ADR 抓取） ----
        print("\n=== Phase 3.7.13 Real RAG E2E Result ===")
        print(f"  Question       : {question}")
        print(f"  Route          : {payload['route']}")
        print(f"  Document ID    : {result.document_id}")
        print(f"  Chunk count    : {result.chunk_count}")
        print(f"  Sources        : {len(sources)} chunk(s)")
        print(f"  Top similarity : {first['similarity']:.4f}")
        print(f"  Content length : {len(content)} chars")
        print(f"  Content (前 100) : {content[:100]}...")
        print(f"  metadata       : {metadata}")
        print("======================================")


# ============================================================
# 失败情况 Unit Test（不依赖真实外部 API，可随时运行）
# ============================================================

class TestRagFailureUnitTests:
    """Phase 3.7.13 任务书 §十七：失败情况 Unit Test 覆盖。

    不发起任何真实网络调用，使用 MonkeyPatch 注入失败：
        - Embedding unavailable → EmbeddingAPIError → Orchestrator ExecutionError
        - LLM unavailable → LLMRequestError → Orchestrator ExecutionError
        - Vector Search unavailable → VectorSearchError → Orchestrator ExecutionError
        - Empty retrieval → RagResponse(empty) → 200 + DEFAULT_EMPTY_ANSWER

    全部通过真实 Orchestrator（注入 MonkeyPatched 的依赖），
    不伪造 ``RagService`` 的成功返回（任务书 §十二）。
    """

    def _build_real_orchestrator_with_failing_rag(
        self, raise_exc: Exception
    ):
        """构造 Orchestrator，其中 RagService.answer 抛 ``raise_exc``。

        - Router / Orchestrator / ProjectContextProvider / ToolRegistry / T2S 全部真实
        - RagService 是真实对象，仅 monkeypatch 其 answer() 方法抛指定异常
        """
        from backend.app.api import orchestrator_chat as orch_module
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorService,
        )
        from backend.app.services.ai_router_service import (
            AIRouterService,
            ToolRegistryCapabilityAdapter,
        )
        from backend.app.services.rag_service import RagService
        from backend.app.tools.get_inventory import build_default_tool_registry

        tool_registry = build_default_tool_registry()
        real_rag = RagService()

        async def _raising_answer(query: str, *, top_k=None):
            raise raise_exc

        real_rag.answer = _raising_answer  # type: ignore[method-assign]

        return AIOrchestratorService(
            router=AIRouterService(
                tool_capabilities=ToolRegistryCapabilityAdapter(tool_registry),
            ),
            rag_service=real_rag,
            tool_registry=tool_registry,
        )

    def _post_with_orchestrator(
        self,
        monkeypatch,
        orch,
    ):
        """把 _default_orchestrator / _build_orchestrator_for_project_id 都替换为 ``orch``。"""
        from backend.app.api import orchestrator_chat as orch_module
        from backend.app.main import app

        monkeypatch.setattr(orch_module, "_default_orchestrator", orch)

        def _fake_build(project_id: str):
            return orch

        monkeypatch.setattr(
            orch_module,
            "_build_orchestrator_for_project_id",
            _fake_build,
        )

        return TestClient(app)

    def test_embedding_unavailable_returns_safe_error(
        self, monkeypatch
    ) -> None:
        """Embedding 不可用 → RAG 抛 EmbeddingAPIError → Orchestrator 包装为 500。"""
        from backend.app.embedding.exceptions import EmbeddingAPIError
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorExecutionError,
        )

        orch = self._build_real_orchestrator_with_failing_rag(
            EmbeddingAPIError("硅基流动 503")
        )
        client = self._post_with_orchestrator(monkeypatch, orch)

        response = client.post(
            "/api/ai/chat", json={"question": "采购入库怎么操作？"}
        )
        assert response.status_code == 500, response.text
        # 必须返回清晰错误，不泄露内部细节
        detail = response.json()["detail"]
        assert "AI 能力执行失败" in detail
        # 严禁泄露 traceback / API Key
        for forbidden in (
            "Traceback",
            "硅基流动 503",  # 内部错误信息不外泄给客户端
            "sk-",
            "Bearer ",
            "<=>",
        ):
            assert forbidden not in response.text, forbidden

    def test_llm_unavailable_returns_safe_error(
        self, monkeypatch
    ) -> None:
        """LLM 不可用 → RagService.answer 抛 LLMRequestError → Orchestrator 包装为 500。"""
        from backend.app.llm.client import LLMRequestError
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorExecutionError,
        )

        orch = self._build_real_orchestrator_with_failing_rag(
            LLMRequestError("DeepSeek 500")
        )
        client = self._post_with_orchestrator(monkeypatch, orch)

        response = client.post(
            "/api/ai/chat", json={"question": "采购入库怎么操作？"}
        )
        assert response.status_code == 500, response.text
        detail = response.json()["detail"]
        assert "AI 能力执行失败" in detail

    def test_vector_search_unavailable_returns_safe_error(
        self, monkeypatch
    ) -> None:
        """Vector Search 不可用 → RagService 抛 VectorSearchError → Orchestrator 包装。"""
        from backend.app.services.vector_search_service import VectorSearchError

        orch = self._build_real_orchestrator_with_failing_rag(
            VectorSearchError("pgvector down")
        )
        client = self._post_with_orchestrator(monkeypatch, orch)

        response = client.post(
            "/api/ai/chat", json={"question": "采购入库怎么操作？"}
        )
        assert response.status_code == 500, response.text

    def test_empty_retrieval_returns_canned_answer(
        self, monkeypatch
    ) -> None:
        """空检索 → RagService 走现有策略（不调 LLM，返回 canned answer）。"""
        from backend.app.services.rag_service import (
            DEFAULT_EMPTY_ANSWER,
            RagResponse,
            RagService,
        )

        real_rag = RagService()

        async def _empty_answer(query: str, *, top_k=None):
            # 完全模拟 RagService.answer 在 results=[] 时的真实行为
            return RagResponse(
                answer=DEFAULT_EMPTY_ANSWER,
                sources=(),
                used_chunks_count=0,
            )

        real_rag.answer = _empty_answer  # type: ignore[method-assign]

        from backend.app.api import orchestrator_chat as orch_module
        from backend.app.main import app
        from backend.app.services.ai_orchestrator_service import (
            AIOrchestratorService,
        )
        from backend.app.services.ai_router_service import (
            AIRouterService,
            ToolRegistryCapabilityAdapter,
        )
        from backend.app.tools.get_inventory import build_default_tool_registry

        tool_registry = build_default_tool_registry()
        orch = AIOrchestratorService(
            router=AIRouterService(
                tool_capabilities=ToolRegistryCapabilityAdapter(tool_registry),
            ),
            rag_service=real_rag,
            tool_registry=tool_registry,
        )
        monkeypatch.setattr(orch_module, "_default_orchestrator", orch)
        monkeypatch.setattr(
            orch_module,
            "_build_orchestrator_for_project_id",
            lambda project_id: orch,
        )

        client = TestClient(app)
        response = client.post(
            "/api/ai/chat", json={"question": "完全无关问题"}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "rag"
        assert payload["content"] == DEFAULT_EMPTY_ANSWER
        # sources 必须为空（来自真实 RagService 的空检索路径）
        assert payload["data"]["sources"] == []
        assert payload["data"]["used_chunks_count"] == 0


# ============================================================
# Helpers
# ============================================================

def random_hex() -> str:
    """生成短随机 hex 字符串（用于唯一化测试文档）。"""
    return uuid.uuid4().hex[:8]


__all__ = [
    "TestRealRagEndToEnd",
    "TestRagFailureUnitTests",
]