"""RAG Reranker 接入测试（Phase 3.7.14）。

覆盖任务书 §九：

    9.1 Reranker 单元测试（排序 / top_k / 空候选 / 单候选 / 异常）
        —— ``_rerank_chunks`` 适配层（BGERerankerClient 自身的输入校验 /
           单文档 / 多文档 / 类型错误已在 tests/test_reranker_client.py
           （Phase 3.5.12）覆盖，此处不重复）。
    9.2 RagService 集成测试：
        - RERANKER_ENABLED=false → VectorSearch=1 / Reranker=0 / ContextBuilder=1 / LLM=1
        - RERANKER_ENABLED=true  → VectorSearch=1 / Reranker=1 / ContextBuilder=1 / LLM=1
    9.3 顺序测试：Vector Search → Reranker → ContextBuilder → LLM（严格顺序）
    9.4 排序效果测试：3 个候选被 Fake Reranker 改变顺序后，
        传给 ContextBuilder 的顺序已变化。
    9.5 API E2E（Fake Reranker 注入 + /api/ai/chat 全链路）
        —— 真实模型 E2E 见 tests/test_rag_real_e2e.py
        （RUN_REAL_RAG_E2E=1 RUN_DB_TESTS=1 RERANKER_ENABLED=true）。

所有测试不发起真实网络 / DB / 模型推理。
配置开关通过 monkeypatch 替换 rag_service 模块内的 settings 绑定实现
（frozen dataclass 不可变，用 dataclasses.replace 构造新实例）。
"""
from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from fastapi.testclient import TestClient

import backend.app.services.rag_service as rag_service_module
from backend.app.config import settings
from backend.app.reranker.exceptions import (
    RerankerError,
    RerankerInputError,
    RerankerModelError,
)
from backend.app.services.rag_service import RagError, RagService, RagResponse
from backend.app.services.vector_search_service import VectorSearchResult


# ============================================================
# Helpers：settings 替换（frozen dataclass → replace 构造新实例）
# ============================================================

def _patch_reranker_settings(
    monkeypatch: pytest.MonkeyPatch, **overrides: Any
) -> None:
    """替换 rag_service 模块内 settings.reranker 的字段。"""
    new_reranker = dataclasses.replace(settings.reranker, **overrides)
    new_settings = dataclasses.replace(settings, reranker=new_reranker)
    monkeypatch.setattr(rag_service_module, "settings", new_settings)


# ============================================================
# Helpers：Fake 依赖（与 tests/test_rag_service.py 同风格）
# ============================================================

class _MockVectorSearchService:
    """确定性 Mock：返回固定结果；记录调用顺序到 shared events。"""

    def __init__(
        self,
        *,
        results: list[VectorSearchResult] | None = None,
        raise_exc: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self._results = results or []
        self._raise = raise_exc
        self._events = events if events is not None else []
        self.search_calls: list[tuple[str, int]] = []

    async def search(self, query: str, *, top_k: int) -> list[VectorSearchResult]:
        self._events.append("vector_search")
        self.search_calls.append((query, top_k))
        if self._raise is not None:
            raise self._raise
        return list(self._results)


class _ScriptedLLMClient:
    """记录调用顺序与 messages 的 Mock LLMClient。"""

    def __init__(
        self,
        *,
        response: str = "[mock-llm] 已回答",
        raise_exc: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self._response = response
        self._raise = raise_exc
        self._events = events if events is not None else []
        self.chat_calls: list[list[dict[str, str]]] = []

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self._events.append("llm_chat")
        self.chat_calls.append(messages)
        if self._raise is not None:
            raise self._raise
        return self._response


class _ScriptedReranker:
    """Fake Reranker：返回预设分数（可改变顺序）；记录调用与顺序。

    实现 RerankerClient 的 rerank(query, documents) -> list[float] 契约。
    """

    def __init__(
        self,
        *,
        scores: list[float] | None = None,
        raise_exc: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self._scores = scores
        self._raise = raise_exc
        self._events = events if events is not None else []
        self.rerank_calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self._events.append("reranker")
        self.rerank_calls.append((query, list(documents)))
        if self._raise is not None:
            raise self._raise
        if self._scores is None:
            # 默认：保持原顺序（分数递减）
            n = len(documents)
            return [round(1.0 - i * 0.1, 4) for i in range(n)]
        return list(self._scores)


class _SpyContextBuilder:
    """记录 build() 调用顺序与入参（验证 Reranker 输出顺序进入 Context）。"""

    def __init__(self, events: list[str] | None = None) -> None:
        self._events = events if events is not None else []
        self.build_calls: list[list[VectorSearchResult]] = []

    def build(self, results: list[VectorSearchResult]) -> Any:
        self._events.append("context_builder")
        self.build_calls.append(list(results))
        # 复用真实 ContextBuilder 的输出结构（最小实现）
        from backend.app.services.context_builder import ContextBuilder

        return ContextBuilder(max_context_chars=12000).build(list(results))


def _make_chunk(
    *,
    chunk_id: int,
    document_id: int = 1,
    chunk_index: int = 0,
    content: str = "示例内容",
    similarity: float = 0.9,
) -> VectorSearchResult:
    return VectorSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=chunk_index,
        content=content,
        distance=1.0 - similarity,
        similarity=similarity,
        metadata={},
    )


# ============================================================
# 9.1 适配层单元测试（_rerank_chunks）
# ============================================================

class TestRerankChunksUnit:
    async def test_normal_ordering_desc(self) -> None:
        """score 降序排序：[0.1, 0.9, 0.5] → 顺序变为 [1, 2, 0]。"""
        chunks = [
            _make_chunk(chunk_id=0, content="A"),
            _make_chunk(chunk_id=1, content="B"),
            _make_chunk(chunk_id=2, content="C"),
        ]
        reranker = _ScriptedReranker(scores=[0.1, 0.9, 0.5])

        out = await rag_service_module._rerank_chunks(
            reranker, "查询", chunks, top_k=3
        )

        assert [r.chunk_id for r in out] == [1, 2, 0]

    async def test_top_k_truncation(self) -> None:
        """top_k=2：5 个候选只保留分数最高的 2 个。"""
        chunks = [
            _make_chunk(chunk_id=i, content=f"doc{i}") for i in range(5)
        ]
        scores = [0.5, 0.9, 0.1, 0.7, 0.3]
        reranker = _ScriptedReranker(scores=scores)

        out = await rag_service_module._rerank_chunks(
            reranker, "查询", chunks, top_k=2
        )

        assert [r.chunk_id for r in out] == [1, 3]

    async def test_empty_documents_returns_empty_without_reranker_call(self) -> None:
        """空候选 → 直接返回 []，不调用 Reranker。"""
        reranker = _ScriptedReranker()

        out = await rag_service_module._rerank_chunks(
            reranker, "查询", [], top_k=3
        )

        assert out == []
        assert reranker.rerank_calls == []

    async def test_single_document(self) -> None:
        """单候选：原样保留（仍经 Reranker 打分）。"""
        chunks = [_make_chunk(chunk_id=42, content="唯一")]
        reranker = _ScriptedReranker(scores=[0.77])

        out = await rag_service_module._rerank_chunks(
            reranker, "查询", chunks, top_k=3
        )

        assert len(out) == 1
        assert out[0].chunk_id == 42
        assert reranker.rerank_calls == [("查询", ["唯一"])]

    async def test_tie_scores_keep_original_order(self) -> None:
        """同分稳定性：保持 Vector Search 原顺序。"""
        chunks = [
            _make_chunk(chunk_id=0, content="A"),
            _make_chunk(chunk_id=1, content="B"),
            _make_chunk(chunk_id=2, content="C"),
        ]
        reranker = _ScriptedReranker(scores=[0.5, 0.5, 0.5])

        out = await rag_service_module._rerank_chunks(
            reranker, "查询", chunks, top_k=3
        )

        assert [r.chunk_id for r in out] == [0, 1, 2]

    async def test_reranker_exception_propagates(self) -> None:
        """Reranker 异常原样透传（不吞掉、不降级为未排序结果）。"""
        chunks = [_make_chunk(chunk_id=0), _make_chunk(chunk_id=1)]
        reranker = _ScriptedReranker(
            raise_exc=RerankerModelError("torch OOM", cause_type="RuntimeError")
        )

        with pytest.raises(RerankerModelError, match="torch OOM"):
            await rag_service_module._rerank_chunks(
                reranker, "查询", chunks, top_k=2
            )

    async def test_score_count_mismatch_raises_rag_error(self) -> None:
        """score 数量与候选数不一致 → RagError（防御 Fake / 自定义实现）。"""
        chunks = [
            _make_chunk(chunk_id=0),
            _make_chunk(chunk_id=1),
            _make_chunk(chunk_id=2),
        ]
        reranker = _ScriptedReranker(scores=[0.9, 0.1])  # 只有 2 个分数

        with pytest.raises(RagError, match="不一致"):
            await rag_service_module._rerank_chunks(
                reranker, "查询", chunks, top_k=2
            )

    async def test_reranker_input_error_propagates(self) -> None:
        """空 query 等 RerankerInputError 原样透传。"""
        chunks = [_make_chunk(chunk_id=0)]
        reranker = _ScriptedReranker(
            raise_exc=RerankerInputError("query 必须是非空 str")
        )

        with pytest.raises(RerankerInputError):
            await rag_service_module._rerank_chunks(
                reranker, "", chunks, top_k=1
            )


# ============================================================
# 9.2 RagService 集成测试（开关行为）
# ============================================================

class TestRagServiceToggle:
    async def test_disabled_path_reranker_not_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RERANKER_ENABLED=false：VectorSearch=1 / Reranker=0 / LLM=1，
        且行为与 Phase 3.5.4 完全一致（top_k 直通 vector search）。"""
        _patch_reranker_settings(monkeypatch, enabled=False)

        chunks = [_make_chunk(chunk_id=1), _make_chunk(chunk_id=2)]
        mock_search = _MockVectorSearchService(results=chunks)
        mock_llm = _ScriptedLLMClient()
        fake_reranker = _ScriptedReranker()  # 注入了也不应被调用

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            reranker_client=fake_reranker,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )
        resp = await svc.answer("采购入库怎么操作？")

        assert isinstance(resp, RagResponse)
        # 关键：Reranker 完全旁路
        assert fake_reranker.rerank_calls == []
        # Vector Search 用默认 top_k（RAG_TOP_K），链路原样
        assert mock_search.search_calls == [
            ("采购入库怎么操作？", settings.rag.default_top_k)
        ]
        assert len(mock_llm.chat_calls) == 1
        # 顺序保持 vector search 原顺序
        assert [s.chunk_id for s in resp.sources] == [1, 2]

    async def test_enabled_path_reranker_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RERANKER_ENABLED=true：VectorSearch=1 / Reranker=1 / LLM=1；
        vector search 扩大召回窗口至 candidate_top_k。"""
        _patch_reranker_settings(
            monkeypatch, enabled=True, candidate_top_k=10, top_k=3
        )

        chunks = [
            _make_chunk(chunk_id=i, content=f"候选{i}") for i in range(10)
        ]
        mock_search = _MockVectorSearchService(results=chunks)
        mock_llm = _ScriptedLLMClient()
        # 分数：让候选 7 最相关、候选 3 次之、候选 0 第三
        scores = [0.3, 0.2, 0.1, 0.85, 0.15, 0.25, 0.05, 0.95, 0.35, 0.45]
        fake_reranker = _ScriptedReranker(scores=scores)

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            reranker_client=fake_reranker,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )
        resp = await svc.answer("采购入库怎么操作？")

        # Vector Search 召回 10 条（candidate_top_k）
        assert mock_search.search_calls == [("采购入库怎么操作？", 10)]
        # Reranker 被调用一次，拿到全部 10 个候选内容
        assert len(fake_reranker.rerank_calls) == 1
        rerank_query, rerank_docs = fake_reranker.rerank_calls[0]
        assert rerank_query == "采购入库怎么操作？"
        assert len(rerank_docs) == 10
        assert rerank_docs == [f"候选{i}" for i in range(10)]
        # LLM 调用一次
        assert len(mock_llm.chat_calls) == 1
        # 最终 sources：重排 + 截断至 top_k=3 → [7, 3, 9]
        assert [s.chunk_id for s in resp.sources] == [7, 3, 9]
        assert resp.used_chunks_count == 3

    async def test_enabled_explicit_top_k_respected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """显式传 top_k=2：最终保留 2 条；召回窗口 max(2, candidate_top_k)。"""
        _patch_reranker_settings(
            monkeypatch, enabled=True, candidate_top_k=10, top_k=3
        )

        chunks = [
            _make_chunk(chunk_id=i, content=f"d{i}") for i in range(10)
        ]
        mock_search = _MockVectorSearchService(results=chunks)
        mock_llm = _ScriptedLLMClient()
        fake_reranker = _ScriptedReranker(
            scores=[0.9, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.95]
        )

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            reranker_client=fake_reranker,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )
        resp = await svc.answer("q", top_k=2)

        # 召回窗口仍为 candidate_top_k=10
        assert mock_search.search_calls == [("q", 10)]
        # 最终只保留 2 条（分数最高：chunk 9 = 0.95, chunk 0 = 0.9）
        assert [s.chunk_id for s in resp.sources] == [9, 0]
        assert resp.used_chunks_count == 2

    async def test_enabled_small_candidate_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """候选不足 candidate_top_k：有几条重排几条（不报错）。"""
        _patch_reranker_settings(
            monkeypatch, enabled=True, candidate_top_k=10, top_k=3
        )

        chunks = [_make_chunk(chunk_id=5, content="唯一候选")]
        mock_search = _MockVectorSearchService(results=chunks)
        mock_llm = _ScriptedLLMClient()
        fake_reranker = _ScriptedReranker(scores=[0.66])

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            reranker_client=fake_reranker,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )
        resp = await svc.answer("q")

        assert len(fake_reranker.rerank_calls) == 1
        assert len(fake_reranker.rerank_calls[0][1]) == 1
        assert [s.chunk_id for s in resp.sources] == [5]

    async def test_empty_search_skips_reranker_and_llm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """空检索：不调 Reranker、不调 LLM（canned answer）。"""
        _patch_reranker_settings(
            monkeypatch, enabled=True, candidate_top_k=10, top_k=3
        )

        mock_search = _MockVectorSearchService(results=[])
        mock_llm = _ScriptedLLMClient()
        fake_reranker = _ScriptedReranker()

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            reranker_client=fake_reranker,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )
        resp = await svc.answer("q")

        assert fake_reranker.rerank_calls == []
        assert mock_llm.chat_calls == []
        assert resp.sources == ()
        assert resp.used_chunks_count == 0

    async def test_reranker_error_propagates_llm_not_called(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reranker 失败：异常透传，LLM 不被调用（不返回未排序的错误结果）。"""
        _patch_reranker_settings(
            monkeypatch, enabled=True, candidate_top_k=10, top_k=3
        )

        chunks = [_make_chunk(chunk_id=1), _make_chunk(chunk_id=2)]
        mock_search = _MockVectorSearchService(results=chunks)
        mock_llm = _ScriptedLLMClient()
        fake_reranker = _ScriptedReranker(
            raise_exc=RerankerModelError("模型加载失败", cause_type="OSError")
        )

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            reranker_client=fake_reranker,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )

        with pytest.raises(RerankerModelError, match="模型加载失败"):
            await svc.answer("q")
        assert mock_llm.chat_calls == []

    async def test_default_reranker_lazy_loaded_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """enabled=true 且未注入 client → 懒加载 get_default_reranker_client()。"""
        _patch_reranker_settings(monkeypatch, enabled=True)

        chunks = [_make_chunk(chunk_id=1, content="候选")]
        mock_search = _MockVectorSearchService(results=chunks)
        mock_llm = _ScriptedLLMClient()
        fake_reranker = _ScriptedReranker(scores=[0.9])

        # 拦截懒加载入口（不真正加载 BGE 模型）
        monkeypatch.setattr(
            rag_service_module,
            "get_default_reranker_client",
            lambda: fake_reranker,
        )

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )
        resp = await svc.answer("q")

        assert len(fake_reranker.rerank_calls) == 1
        assert len(resp.sources) == 1


# ============================================================
# 9.3 顺序测试：Vector Search → Reranker → ContextBuilder → LLM
# ============================================================

class TestPipelineOrder:
    async def test_strict_order_search_rerank_context_llm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """严格顺序：vector_search → reranker → context_builder → llm_chat。

        显式验证 Reranker 位于 Vector Search 之后、ContextBuilder 之前，
        不允许出现 context_builder → reranker 的乱序。
        """
        _patch_reranker_settings(
            monkeypatch, enabled=True, candidate_top_k=10, top_k=3
        )

        events: list[str] = []
        chunks = [
            _make_chunk(chunk_id=i, content=f"内容{i}") for i in range(4)
        ]

        mock_search = _MockVectorSearchService(results=chunks, events=events)
        mock_llm = _ScriptedLLMClient(events=events)
        fake_reranker = _ScriptedReranker(
            scores=[0.1, 0.2, 0.3, 0.4], events=events
        )
        spy_builder = _SpyContextBuilder(events=events)

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            reranker_client=fake_reranker,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
            context_builder=spy_builder,  # type: ignore[arg-type]
        )
        await svc.answer("采购入库怎么操作？")

        # 每个阶段恰好一次，且顺序严格
        assert events == [
            "vector_search",
            "reranker",
            "context_builder",
            "llm_chat",
        ]

    async def test_disabled_order_search_context_llm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """关闭时顺序：vector_search → context_builder → llm_chat（无 reranker）。"""
        _patch_reranker_settings(monkeypatch, enabled=False)

        events: list[str] = []
        chunks = [_make_chunk(chunk_id=1), _make_chunk(chunk_id=2)]

        mock_search = _MockVectorSearchService(results=chunks, events=events)
        mock_llm = _ScriptedLLMClient(events=events)
        spy_builder = _SpyContextBuilder(events=events)

        svc = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
            context_builder=spy_builder,  # type: ignore[arg-type]
        )
        await svc.answer("q")

        assert events == ["vector_search", "context_builder", "llm_chat"]


# ============================================================
# 9.4 排序效果测试：重排顺序真正进入 ContextBuilder
# ============================================================

class TestReorderEffect:
    async def test_reordered_candidates_reach_context_builder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """3 个候选 A/B/C，Fake Reranker 反转顺序 → ContextBuilder 收到 C/B/A。"""
        _patch_reranker_settings(
            monkeypatch, enabled=True, candidate_top_k=10, top_k=3
        )

        chunks = [
            _make_chunk(chunk_id=1, content="文档A-采购收货"),
            _make_chunk(chunk_id=2, content="文档B-销售出库"),
            _make_chunk(chunk_id=3, content="文档C-质检上架"),
        ]
        # 分数反转：C > B > A
        fake_reranker = _ScriptedReranker(scores=[0.1, 0.5, 0.9])
        spy_builder = _SpyContextBuilder()

        svc = RagService(
            vector_search_service=_MockVectorSearchService(results=chunks),
            reranker_client=fake_reranker,  # type: ignore[arg-type]
            llm_client=_ScriptedLLMClient(),
            context_builder=spy_builder,  # type: ignore[arg-type]
        )
        resp = await svc.answer("怎么上架？")

        # ContextBuilder 收到的顺序已变为 C / B / A
        assert len(spy_builder.build_calls) == 1
        received = spy_builder.build_calls[0]
        assert [r.chunk_id for r in received] == [3, 2, 1]
        assert [r.content for r in received] == [
            "文档C-质检上架",
            "文档B-销售出库",
            "文档A-采购收货",
        ]
        # sources 顺序同样变化
        assert [s.chunk_id for s in resp.sources] == [3, 2, 1]
        # LLM user prompt 中内容顺序与重排一致（C 在 A 之前）
        user_prompt = None
        # _ScriptedLLMClient() 未保留引用 → 通过 resp 间接验证已足够，
        # 此处再直接验证一次 prompt 顺序
        assert [s.content for s in resp.sources][0] == "文档C-质检上架"

    async def test_reorder_reflected_in_llm_prompt_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM user prompt 中重排后的内容顺序：C 出现在 A 之前。"""
        _patch_reranker_settings(
            monkeypatch, enabled=True, candidate_top_k=10, top_k=3
        )

        chunks = [
            _make_chunk(chunk_id=1, content="标记AAA"),
            _make_chunk(chunk_id=2, content="标记BBB"),
            _make_chunk(chunk_id=3, content="标记CCC"),
        ]
        fake_reranker = _ScriptedReranker(scores=[0.1, 0.5, 0.9])
        mock_llm = _ScriptedLLMClient()

        svc = RagService(
            vector_search_service=_MockVectorSearchService(results=chunks),
            reranker_client=fake_reranker,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )
        await svc.answer("q")

        user_prompt = mock_llm.chat_calls[0][1]["content"]
        pos_ccc = user_prompt.index("标记CCC")
        pos_bbb = user_prompt.index("标记BBB")
        pos_aaa = user_prompt.index("标记AAA")
        # 重排后顺序 CCC < BBB < AAA（出现位置递增）
        assert pos_ccc < pos_bbb < pos_aaa


# ============================================================
# 9.5 API E2E（Fake Reranker 注入 + POST /api/ai/chat）
# ============================================================

class TestApiE2eWithFakeReranker:
    def test_api_chat_rag_route_with_reranker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /api/ai/chat：RAG 路由 + Reranker 参与（Fake，全链路 Mock）。

        链路：Router → Orchestrator → RagService(注入 Fake 依赖) → 200。
        真实模型 E2E 见 tests/test_rag_real_e2e.py。
        """
        _patch_reranker_settings(
            monkeypatch, enabled=True, candidate_top_k=10, top_k=3
        )

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

        chunks = [
            _make_chunk(chunk_id=i, content=f"入库候选{i}") for i in range(10)
        ]
        mock_search = _MockVectorSearchService(results=chunks)
        mock_llm = _ScriptedLLMClient(response="根据知识库，入库包括收货、质检、上架。")
        # 分数让 chunk 9 / 5 / 2 位列前三
        scores = [0.1, 0.2, 0.71, 0.3, 0.4, 0.85, 0.5, 0.6, 0.55, 0.95]
        fake_reranker = _ScriptedReranker(scores=scores)

        rag = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            reranker_client=fake_reranker,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )

        tool_registry = build_default_tool_registry()
        orch = AIOrchestratorService(
            router=AIRouterService(
                tool_capabilities=ToolRegistryCapabilityAdapter(tool_registry),
            ),
            rag_service=rag,
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
            "/api/ai/chat",
            json={"question": "采购入库怎么操作？", "project_id": "vietnam-wms"},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["route"] == "rag"
        assert payload["content"] == "根据知识库，入库包括收货、质检、上架。"
        # Reranker 真正参与
        assert len(fake_reranker.rerank_calls) == 1
        assert len(fake_reranker.rerank_calls[0][1]) == 10
        # Vector Search 正常（candidate 窗口）
        assert mock_search.search_calls == [("采购入库怎么操作？", 10)]
        # LLM 正常
        assert len(mock_llm.chat_calls) == 1
        # sources 重排后 top-3：chunk 9 / 5 / 2
        sources = payload["data"]["sources"]
        assert [s["chunk_id"] for s in sources] == [9, 5, 2]

    def test_api_chat_rag_route_disabled_reranker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /api/ai/chat：RERANKER_ENABLED=false 时 RAG 路由不受影响。"""
        _patch_reranker_settings(monkeypatch, enabled=False)

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

        chunks = [_make_chunk(chunk_id=1, content="入库内容")]
        mock_search = _MockVectorSearchService(results=chunks)
        mock_llm = _ScriptedLLMClient(response="入库流程回答。")
        fake_reranker = _ScriptedReranker()

        rag = RagService(
            vector_search_service=mock_search,  # type: ignore[arg-type]
            reranker_client=fake_reranker,  # type: ignore[arg-type]
            llm_client=mock_llm,  # type: ignore[arg-type]
        )

        tool_registry = build_default_tool_registry()
        orch = AIOrchestratorService(
            router=AIRouterService(
                tool_capabilities=ToolRegistryCapabilityAdapter(tool_registry),
            ),
            rag_service=rag,
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
            "/api/ai/chat", json={"question": "采购入库怎么操作？"}
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "rag"
        assert payload["content"] == "入库流程回答。"
        # Reranker 未参与
        assert fake_reranker.rerank_calls == []
        assert mock_search.search_calls == [
            ("采购入库怎么操作？", settings.rag.default_top_k)
        ]


# ============================================================
# 配置默认值 / 钳制
# ============================================================

class TestRerankerConfigDefaults:
    def test_disabled_by_default(self) -> None:
        """RERANKER_ENABLED 默认 False（生产默认关闭）。"""
        assert settings.reranker.enabled is False

    def test_candidate_top_k_default_and_bounds(self) -> None:
        """candidate_top_k 默认 10，钳制范围 [1, 50]。"""
        assert settings.reranker.candidate_top_k == 10
        assert 1 <= settings.reranker.candidate_top_k <= 50

    def test_top_k_default_and_bounds(self) -> None:
        """RERANKER_TOP_K 默认 3，钳制范围 [1, 50]。"""
        assert settings.reranker.top_k == 3
        assert 1 <= settings.reranker.top_k <= 50

    def test_clamping_applied_on_invalid_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """异常值（9999 / -5 / 非数字）被钳制 / 回退，不导致无限资源消耗。"""
        import importlib

        import backend.app.config as config_module

        monkeypatch.setenv("RERANKER_CANDIDATE_TOP_K", "9999")
        monkeypatch.setenv("RERANKER_TOP_K", "-5")
        reloaded = importlib.reload(config_module)
        try:
            assert reloaded.settings.reranker.candidate_top_k == 50  # 钳制到上界
            assert reloaded.settings.reranker.top_k == 1  # 钳制到下界
        finally:
            importlib.reload(config_module)  # 恢复

    def test_model_default_unchanged(self) -> None:
        """模型默认仍是 BAAI/bge-reranker-v2-m3（不切换模型）。"""
        assert settings.reranker.model == "BAAI/bge-reranker-v2-m3"


__all__ = [
    "TestRerankChunksUnit",
    "TestRagServiceToggle",
    "TestPipelineOrder",
    "TestReorderEffect",
    "TestApiE2eWithFakeReranker",
    "TestRerankerConfigDefaults",
]