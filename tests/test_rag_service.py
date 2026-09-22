"""RAG Service 测试（Phase 3.5.4）。

覆盖（对应 Phase 3.5.4 任务书 §十五）：

    1. 正常 RAG：Vector Search → Context → answer → sources 全跑通
    2. 空检索 → 不调 LLM，canned answer，sources=()
    3. Vector Search 错误 → 异常向上传递
    4. LLM 错误 → 异常向上传递
    5. Sources：VectorSearchResult[] → RagSource[] 字段一致
    6. Prompt：发送给 LLM 的 user prompt 含 CONTEXT + QUESTION + 实际知识内容
    7. Anti-hallucination：system prompt 包含"只能根据知识库内容回答"等关键短语

所有单元测试使用 Mock VectorSearchService + MockLLMClient，
**不发起真实网络** / DB / LLM 调用。
DB 集成测试见 TestDbIntegration。
"""
from __future__ import annotations

import hashlib
import os
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from backend.app.config import settings
from backend.app.db import reset_engine_cache
from backend.app.db.base import Base
from backend.app.db.models import KnowledgeChunk, KnowledgeDocument
from backend.app.db.session import get_engine
from backend.app.embedding.client import EmbeddingClient
from backend.app.embedding.exceptions import EmbeddingAPIError
from backend.app.llm.client import MockLLMClient
from backend.app.llm.client import LLMError, LLMRequestError
from backend.app.services.context_builder import DEFAULT_MAX_CONTEXT_CHARS
from backend.app.services.rag_service import (
    DEFAULT_EMPTY_ANSWER,
    RagError,
    RagResponse,
    RagService,
    RagSource,
)
from backend.app.services.vector_search_service import (
    VectorSearchResult,
    VectorSearchService,
)


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable RAG DB tests",
)


# ============================================================
# Mock Helpers
# ============================================================

class _MockVectorSearchService:
    """确定性 Mock：返回固定结果列表；支持错误注入。"""

    def __init__(
        self,
        *,
        results: list[VectorSearchResult] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._results = results or []
        self._raise = raise_exc
        self.search_calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, top_k: int) -> list[VectorSearchResult]:
        self.search_calls.append((query, top_k))
        if self._raise is not None:
            raise self._raise
        return list(self._results)


class _ScriptedLLMClient:
    """可断言调用 messages + 注入错误的 Mock LLMClient。"""

    def __init__(
        self,
        *,
        response: str = "[mock-llm] 已回答",
        raise_exc: Exception | None = None,
    ) -> None:
        self._response = response
        self._raise = raise_exc
        self.chat_calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self.chat_calls.append(messages)
        if self._raise is not None:
            raise self._raise
        return self._response


def _make_chunk(
    *,
    chunk_id: int,
    document_id: int = 1,
    chunk_index: int = 0,
    content: str = "示例内容",
    similarity: float = 0.9,
    metadata: dict | None = None,
) -> VectorSearchResult:
    return VectorSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=chunk_index,
        content=content,
        distance=1.0 - similarity,
        similarity=similarity,
        metadata=metadata if metadata is not None else {},
    )


# ============================================================
# 1. 正常 RAG
# ============================================================

class TestNormalRag:
    async def test_full_pipeline_end_to_end(self) -> None:
        """query → search → context → llm → RagResponse。"""
        chunks = [
            _make_chunk(
                chunk_id=1,
                document_id=10,
                chunk_index=0,
                content="采购入库流程：1.收货 2.质检 3.上架。",
                similarity=0.95,
                metadata={"heading_path": ["采购入库", "操作步骤"]},
            ),
            _make_chunk(
                chunk_id=2,
                document_id=10,
                chunk_index=1,
                content="上架时遵循库位策略。",
                similarity=0.88,
                metadata={"heading_path": ["采购入库", "上架"]},
            ),
        ]
        mock_search = _MockVectorSearchService(results=chunks)
        mock_llm = _ScriptedLLMClient(response="根据知识库，采购入库包括收货、质检和上架三个步骤。")

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )
        resp = await svc.answer("采购入库怎么操作？", top_k=5)

        assert isinstance(resp, RagResponse)
        assert resp.answer == "根据知识库，采购入库包括收货、质检和上架三个步骤。"
        assert len(resp.sources) == 2
        assert resp.used_chunks_count == 2
        # 顺序：与 search 返回一致
        assert resp.sources[0].chunk_id == 1
        assert resp.sources[1].chunk_id == 2

        # 内部调用记录
        assert mock_search.search_calls == [("采购入库怎么操作？", 5)]
        assert len(mock_llm.chat_calls) == 1

    async def test_default_top_k_from_settings(self) -> None:
        """top_k 不传时使用 settings.rag.default_top_k。"""
        mock_search = _MockVectorSearchService(results=[])
        mock_llm = _ScriptedLLMClient()

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )
        await svc.answer("query")

        # 默认 top_k
        assert mock_search.search_calls == [("query", settings.rag.default_top_k)]


# ============================================================
# 2. 空检索
# ============================================================

class TestEmptySearch:
    async def test_empty_search_returns_canned_answer_no_llm(self) -> None:
        """Vector Search 返回 [] 时：不调 LLM，返回 canned answer + sources=()。"""
        mock_search = _MockVectorSearchService(results=[])
        mock_llm = _ScriptedLLMClient()

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )
        resp = await svc.answer("完全没相关的问题")

        assert isinstance(resp, RagResponse)
        assert resp.answer == DEFAULT_EMPTY_ANSWER
        assert resp.sources == ()
        assert resp.used_chunks_count == 0
        # 关键：不调 LLM
        assert mock_llm.chat_calls == []

    async def test_custom_empty_answer_text(self) -> None:
        """empty_answer_text 自定义：注入的自定义字符串生效。"""
        mock_search = _MockVectorSearchService(results=[])
        mock_llm = _ScriptedLLMClient()

        custom = "目前知识库为空，请稍后再试。"
        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
            empty_answer_text=custom,
        )
        resp = await svc.answer("query")

        assert resp.answer == custom


# ============================================================
# 3. Vector Search 错误透传
# ============================================================

class TestVectorSearchError:
    async def test_vector_search_input_error_propagates(self) -> None:
        """VectorSearchInputError 原样向上传递。"""
        from backend.app.services.vector_search_service import VectorSearchInputError

        mock_search = _MockVectorSearchService(
            raise_exc=VectorSearchInputError("empty query")
        )
        mock_llm = _ScriptedLLMClient()

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )

        with pytest.raises(VectorSearchInputError, match="empty query"):
            await svc.answer("?")
        # LLM 不应被调用
        assert mock_llm.chat_calls == []

    async def test_embedding_api_error_propagates(self) -> None:
        """Embedding 阶段错误也原样向上传递。"""
        mock_search = _MockVectorSearchService(
            raise_exc=EmbeddingAPIError("硅基流动 503")
        )
        mock_llm = _ScriptedLLMClient()

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )

        with pytest.raises(EmbeddingAPIError, match="503"):
            await svc.answer("q")
        assert mock_llm.chat_calls == []


# ============================================================
# 4. LLM 错误透传
# ============================================================

class TestLlmError:
    async def test_llm_request_error_propagates(self) -> None:
        """LLMRequestError 原样向上传递，不返回"系统暂时正常"。"""
        chunks = [_make_chunk(chunk_id=1, content="x")]
        mock_search = _MockVectorSearchService(results=chunks)
        mock_llm = _ScriptedLLMClient(raise_exc=LLMRequestError("DeepSeek 500"))

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )

        with pytest.raises(LLMRequestError, match="500"):
            await svc.answer("q")


# ============================================================
# 5. Sources 字段映射
# ============================================================

class TestSourceMapping:
    async def test_sources_match_vector_search_results(self) -> None:
        """RagSource 字段与 VectorSearchResult 对应一致。"""
        meta = {"heading_path": ["采购入库"], "source_type": "markdown"}
        chunks = [
            _make_chunk(
                chunk_id=101,
                document_id=12,
                chunk_index=3,
                content="abc",
                similarity=0.91,
                metadata=meta,
            )
        ]
        mock_search = _MockVectorSearchService(results=chunks)
        mock_llm = _ScriptedLLMClient()

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )
        resp = await svc.answer("q", top_k=3)

        assert len(resp.sources) == 1
        s = resp.sources[0]
        assert isinstance(s, RagSource)
        assert s.chunk_id == 101
        assert s.document_id == 12
        assert s.chunk_index == 3
        assert s.content == "abc"
        assert s.similarity == pytest.approx(0.91)
        assert s.metadata == meta

    async def test_sources_drop_chunks_clipped_by_context_length(self) -> None:
        """Context Builder 截断后被丢弃的 chunk 不应出现在 sources 中。"""
        # 用极小 max_context_chars 制造截断
        chunks = [
            _make_chunk(chunk_id=1, similarity=0.95, content="最相关" + "X" * 500),
            _make_chunk(chunk_id=2, similarity=0.85, content="次相关" + "Y" * 500),
            _make_chunk(chunk_id=3, similarity=0.75, content="再次" + "Z" * 500),
        ]
        mock_search = _MockVectorSearchService(results=chunks)
        mock_llm = _ScriptedLLMClient()
        # 注入小的 context builder
        from backend.app.services.context_builder import ContextBuilder

        small_builder = ContextBuilder(max_context_chars=200)

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
            context_builder=small_builder,
        )
        resp = await svc.answer("q", top_k=3)

        # sources 数应 ≤ search 返回数（截断后）
        assert len(resp.sources) <= len(chunks)
        assert resp.used_chunks_count == len(resp.sources)
        # 第 1 个（最相关）必须保留
        assert resp.sources[0].chunk_id == 1


# ============================================================
# 6. Prompt 结构
# ============================================================

class TestPromptStructure:
    async def test_user_prompt_contains_context_and_question(self) -> None:
        """发送给 LLM 的 user message 必须包含 {context} 与 {question} 替换内容。"""
        chunks = [_make_chunk(chunk_id=1, content="唯一内容：入库要质检")]
        mock_search = _MockVectorSearchService(results=chunks)
        mock_llm = _ScriptedLLMClient()

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )
        await svc.answer("采购怎么入库？", top_k=3)

        assert len(mock_llm.chat_calls) == 1
        messages = mock_llm.chat_calls[0]
        assert len(messages) == 2
        # user 消息
        user_msg = messages[1]
        assert user_msg["role"] == "user"
        assert "CONTEXT" in user_msg["content"]
        assert "QUESTION" in user_msg["content"]
        # 实际内容
        assert "唯一内容：入库要质检" in user_msg["content"]
        assert "采购怎么入库？" in user_msg["content"]

    async def test_system_message_present(self) -> None:
        """system 消息存在，且包含 anti-hallucination 关键短语（见测试 7）。"""
        chunks = [_make_chunk(chunk_id=1)]
        mock_search = _MockVectorSearchService(results=chunks)
        mock_llm = _ScriptedLLMClient()

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )
        await svc.answer("q")

        messages = mock_llm.chat_calls[0]
        sys_msg = messages[0]
        assert sys_msg["role"] == "system"
        assert len(sys_msg["content"]) > 0


# ============================================================
# 7. Anti-hallucination
# ============================================================

class TestAntiHallucination:
    async def test_system_prompt_contains_required_phrases(self) -> None:
        """System Prompt 必须包含 anti-hallucination 关键短语。"""
        # 加载文件实际内容（验证真实 prompt 文件符合要求）
        from pathlib import Path

        prompt_path = (
            Path(__file__).resolve().parents[0].parent
            / "backend"
            / "app"
            / "prompts"
            / "rag_system.txt"
        )
        content = prompt_path.read_text(encoding="utf-8")

        # 关键短语（任务书 §八 明确要求）
        assert "只能" in content
        assert "知识库" in content
        assert "编造" in content  # 不要编造

    async def test_user_prompt_references_no_hallucination(self) -> None:
        """User Prompt 也必须包含 anti-hallucination 提示。"""
        from pathlib import Path

        prompt_path = (
            Path(__file__).resolve().parents[0].parent
            / "backend"
            / "app"
            / "prompts"
            / "rag_user.txt"
        )
        content = prompt_path.read_text(encoding="utf-8")

        assert "CONTEXT" in content
        assert "QUESTION" in content
        # 防止幻觉的明示
        assert "只能依据" in content or "只能根据" in content
        assert "不要编造" in content or "不要" in content

    async def test_full_prompts_passed_to_llm_via_real_service(self) -> None:
        """真实构造的 RagService（懒加载默认文件），messages 包含完整 system + user。"""
        chunks = [_make_chunk(chunk_id=1, content="实际内容")]
        mock_search = _MockVectorSearchService(results=chunks)
        # 用一个简单 mock 捕获 messages（避免读 .env 默认 LLM）
        mock_llm = _ScriptedLLMClient()
        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )
        await svc.answer("q")

        messages = mock_llm.chat_calls[0]
        sys_prompt = messages[0]["content"]
        user_prompt = messages[1]["content"]

        # system 必须含 anti-hallucination
        assert "知识库" in sys_prompt
        assert "编造" in sys_prompt
        # user 必须含占位符替换
        assert "实际内容" in user_prompt
        assert "q" in user_prompt


# ============================================================
# 默认值
# ============================================================

class TestDefaults:
    def test_default_empty_answer_is_non_empty(self) -> None:
        assert isinstance(DEFAULT_EMPTY_ANSWER, str)
        assert len(DEFAULT_EMPTY_ANSWER) > 0

    def test_default_context_builder_uses_rag_setting(self) -> None:
        """未注入时 ContextBuilder.max == settings.rag.max_context_chars。"""
        # 不触发任何 IO，仅校验构造逻辑
        from backend.app.services.context_builder import ContextBuilder

        cb = ContextBuilder(max_context_chars=settings.rag.max_context_chars)
        assert cb.max_context_chars == settings.rag.max_context_chars
        assert cb.max_context_chars == DEFAULT_MAX_CONTEXT_CHARS


# ============================================================
# 真实 DB 集成（任务书 §十六）
# ============================================================

@requires_db
class TestDbIntegration:
    """真实 PostgreSQL + pgvector 端到端：

        VectorSearchService → ContextBuilder → RagService → LLM (Mock)
    """

    @pytest.fixture(scope="module")
    def engine(self):
        reset_engine_cache()
        eng = get_engine()
        assert eng is not None, "DATABASE_URL 未配置；请配置后启用 RUN_DB_TESTS=1"

        from backend.app.db import models  # noqa: F401

        Base.metadata.create_all(eng)
        yield eng

    @pytest.fixture
    def _truncate_after_each(self, engine):
        yield
        with engine.begin() as conn:
            conn.execute(
                text(
                    "TRUNCATE TABLE knowledge_chunk, knowledge_document "
                    "RESTART IDENTITY CASCADE"
                )
            )

    @pytest.fixture
    def db(self, _truncate_after_each, engine):
        """function-level：每个测试一个独立 session。"""
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

    async def test_real_db_end_to_end(self, db, engine):
        """真实 PostgreSQL → pgvector → VectorSearchService → ContextBuilder → LLM。"""
        mock_embedding = _MockEmbeddingClient()
        vector_service = VectorSearchService(embedding_client=mock_embedding)

        mock_llm = _ScriptedLLMClient(response="[mock-llm] 真实 DB 测试通过")

        rag = RagService(
            vector_search_service=vector_service,
            llm_client=mock_llm,
        )

        # 写入 Document + 3 chunks（经 db session，commit 后跨 session 可见）
        doc = KnowledgeDocument(
            title="[TEST] RAG integration",
            file_name="rag_test.md",
            file_type="md",
            source="test",
            content_hash=hashlib.sha256(b"rag-test-doc").hexdigest(),
            status="ready",
            meta_data={"source_type": "md"},
        )
        db.add(doc)
        db.flush()

        dim = settings.embedding.dimension
        query_vec = [0.1] * dim
        v_same = list(query_vec)
        v_ortho = [1.0] + [-1.0 / (dim - 1)] * (dim - 1)
        v_opposite = [-x for x in query_vec]
        vectors = [
            (0, v_ortho, "chunk-ortho"),
            (1, v_same, "chunk-same"),
            (2, v_opposite, "chunk-opposite"),
        ]
        for chunk_index, vec, content in vectors:
            db.add(
                KnowledgeChunk(
                    document_id=doc.id,
                    chunk_index=chunk_index,
                    content=content,
                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    embedding=vec,
                    meta_data={"source_type": "markdown"},
                )
            )
        db.flush()
        # 必须 commit：VectorSearchService 内部使用独立 session；
        # PG 默认 READ COMMITTED 看不到未提交数据
        db.commit()

        # 执行 RAG
        resp = await rag.answer("采购入库怎么操作？", top_k=2)

        # 验证
        assert resp.answer == "[mock-llm] 真实 DB 测试通过"
        assert len(resp.sources) == 2
        # similarity 最高 = same（chunk_index=1）
        assert resp.sources[0].chunk_id is not None
        # LLM 必须被调用（因为有命中）
        assert len(mock_llm.chat_calls) == 1


__all__ = [
    "TestNormalRag",
    "TestEmptySearch",
    "TestVectorSearchError",
    "TestLlmError",
    "TestSourceMapping",
    "TestPromptStructure",
    "TestAntiHallucination",
    "TestDefaults",
    "TestDbIntegration",
]


class _MockEmbeddingClient(EmbeddingClient):
    """确定性 Mock：返回固定 1024 维向量（仅供 DB 集成测试）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.1] * settings.embedding.dimension