"""Retrieval Analysis Service 单元测试（Phase 3.5.11）。

覆盖任务书 §十 的 13 项：

    1. 空结果            2. 单结果            3. 多结果（avg/min/max）
    4. rank 正确         5. average similarity 6. min similarity
    7. max similarity    8. content_length    9. metadata 保留
   10. top_k 传递        11. VectorSearchError 原样传播
   12. DTO frozen        13. 不暴露 ORM

同时覆盖 `RagEvaluationService.evaluate_top_k`（前缀截断 / 各档统计 /
空 cases / 错误传播 / 与 evaluate 一致的匹配语义）。

全部使用 Stub VectorSearchService（无 DB、无真实 Embedding、无 LLM）。
"""
from __future__ import annotations

import dataclasses

import pytest

from backend.app.services.rag_evaluation_service import (
    RagEvaluationService,
    RetrievalEvaluationCase,
)
from backend.app.services.retrieval_analysis_service import (
    RetrievalAnalysisItem,
    RetrievalAnalysisResult,
    RetrievalAnalysisService,
)
from backend.app.services.vector_search_service import (
    VectorSearchInputError,
    VectorSearchParameterError,
    VectorSearchResult,
    VectorSearchService,
)


# ============================================================
# Stub：VectorSearchService
# ============================================================

def _vsr(
    chunk_id: int,
    similarity: float,
    *,
    document_id: int = 1,
    chunk_index: int = 0,
    content: str = "示例内容",
    metadata: dict | None = None,
) -> VectorSearchResult:
    return VectorSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=chunk_index,
        content=content,
        distance=1.0 - similarity,
        similarity=similarity,
        metadata=metadata or {},
    )


class StubVectorSearchService(VectorSearchService):
    """按 query 返回预设结果的 stub；记录 top_k 供断言。

    异常注入：`raise_error` 非空时 search 原样抛出。
    返回时按 top_k 截断（模拟真实 LIMIT 语义）。
    """

    def __init__(
        self,
        results_by_query: dict[str, list[VectorSearchResult]] | None = None,
        *,
        raise_error: Exception | None = None,
    ) -> None:
        super().__init__()
        self._map = results_by_query or {}
        self._raise = raise_error
        self.top_k_calls: list[int] = []

    async def search(self, query: str, *, top_k: int = 5):  # type: ignore[override]
        self.top_k_calls.append(top_k)
        if self._raise is not None:
            raise self._raise
        return self._map.get(query, [])[:top_k]


def _svc(stub: StubVectorSearchService) -> RetrievalAnalysisService:
    return RetrievalAnalysisService(vector_search_service=stub)


# ============================================================
# 1-3. 空结果 / 单结果 / 多结果
# ============================================================

class TestEmptyResult:
    async def test_empty_results_zero_stats(self) -> None:
        stub = StubVectorSearchService({"任何查询": []})
        result = await _svc(stub).analyze("任何查询", top_k=5)

        assert isinstance(result, RetrievalAnalysisResult)
        assert result.result_count == 0
        assert result.items == ()
        assert result.average_similarity == 0.0
        assert result.min_similarity is None
        assert result.max_similarity is None
        assert result.query == "任何查询"
        assert result.top_k == 5


class TestSingleResult:
    async def test_single_result_avg_min_max_equal(self) -> None:
        stub = StubVectorSearchService({"q": [_vsr(10, 0.91)]})
        result = await _svc(stub).analyze("q")

        assert result.result_count == 1
        assert result.average_similarity == pytest.approx(0.91)
        assert result.min_similarity == pytest.approx(0.91)
        assert result.max_similarity == pytest.approx(0.91)
        assert len(result.items) == 1


class TestMultipleResults:
    async def test_multi_result_stats(self) -> None:
        # 0.91 / 0.87 / 0.72 / 0.61 → avg=0.7775, min=0.61, max=0.91
        stub = StubVectorSearchService(
            {"q": [_vsr(1, 0.91), _vsr(2, 0.87), _vsr(3, 0.72), _vsr(4, 0.61)]}
        )
        result = await _svc(stub).analyze("q", top_k=4)

        assert result.result_count == 4
        assert result.average_similarity == pytest.approx(0.7775)
        assert result.min_similarity == pytest.approx(0.61)
        assert result.max_similarity == pytest.approx(0.91)


# ============================================================
# 4. rank / 8. content_length / 9. metadata / 2. distance
# ============================================================

class TestItemExtraction:
    async def test_rank_starts_at_one_and_increments(self) -> None:
        stub = StubVectorSearchService(
            {"q": [_vsr(1, 0.9), _vsr(2, 0.8), _vsr(3, 0.7)]}
        )
        result = await _svc(stub).analyze("q", top_k=3)

        assert [it.rank for it in result.items] == [1, 2, 3]

    async def test_content_length_and_metadata_preserved(self) -> None:
        content = "采购入库的操作流程说明"
        meta = {"heading": "采购入库", "source": "wms-basic-operations.md"}
        stub = StubVectorSearchService(
            {"q": [_vsr(7, 0.8, chunk_index=2, content=content, metadata=meta)]}
        )
        result = await _svc(stub).analyze("q")

        it = result.items[0]
        assert it.chunk_id == 7
        assert it.chunk_index == 2
        assert it.content_length == len(content)
        assert it.metadata == meta

    async def test_similarity_and_distance_from_source(self) -> None:
        stub = StubVectorSearchService({"q": [_vsr(5, 0.62)]})
        result = await _svc(stub).analyze("q")

        it = result.items[0]
        # similarity / distance 直接取自 VectorSearchResult，不重算
        assert it.similarity == pytest.approx(0.62)
        assert it.distance == pytest.approx(1.0 - 0.62)

    async def test_items_have_no_orm_or_content_fields(self) -> None:
        """DTO 不暴露 ORM / chunk 全文 / embedding。"""
        stub = StubVectorSearchService({"q": [_vsr(5, 0.8, content="全文不应出现")]})
        result = await _svc(stub).analyze("q")

        field_names = {f.name for f in dataclasses.fields(result.items[0])}
        assert "content" not in field_names
        assert "embedding" not in field_names
        assert "session" not in field_names
        # Item 也不应携带全文
        assert "全文不应出现" not in repr(result.items[0])


# ============================================================
# 10. top_k 传递
# ============================================================

class TestTopKPassThrough:
    async def test_top_k_passed_to_search(self) -> None:
        stub = StubVectorSearchService({"q": [_vsr(1, 0.9)]})
        await _svc(stub).analyze("q", top_k=7)
        assert stub.top_k_calls == [7]

    async def test_default_top_k_is_vector_search_default(self) -> None:
        stub = StubVectorSearchService({"q": [_vsr(1, 0.9)]})
        await _svc(stub).analyze("q")
        assert stub.top_k_calls == [VectorSearchService.DEFAULT_TOP_K]

    async def test_no_local_range_constants_defined(self) -> None:
        """不得复制一套新的范围常量（复用 VectorSearchService 的）。"""
        for name in ("MIN_TOP_K", "MAX_TOP_K", "DEFAULT_TOP_K"):
            assert not hasattr(RetrievalAnalysisService, name), \
                f"RetrievalAnalysisService 不应自定义 {name}"


# ============================================================
# 11. VectorSearchError 原样传播
# ============================================================

class TestErrorPropagation:
    async def test_vector_search_error_propagates_unchanged(self) -> None:
        err = VectorSearchParameterError("top_k 超出合法范围 [1, 50]（当前: 999）")
        stub = StubVectorSearchService(raise_error=err)
        with pytest.raises(VectorSearchParameterError) as ei:
            await _svc(stub).analyze("q", top_k=999)
        assert ei.value is err  # 原样传播，未包装

    async def test_input_error_propagates_unchanged(self) -> None:
        err = VectorSearchInputError("查询文本为空或纯空白")
        stub = StubVectorSearchService(raise_error=err)
        with pytest.raises(VectorSearchInputError) as ei:
            await _svc(stub).analyze("  ")
        assert ei.value is err


# ============================================================
# 12. DTO frozen
# ============================================================

class TestDtoFrozen:
    def test_result_frozen(self) -> None:
        r = RetrievalAnalysisResult(
            query="q", top_k=5, result_count=0, items=(),
            average_similarity=0.0, min_similarity=None, max_similarity=None,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.top_k = 10  # type: ignore[misc]

    def test_item_frozen(self) -> None:
        it = RetrievalAnalysisItem(
            rank=1, chunk_id=1, document_id=1, chunk_index=0,
            similarity=0.9, distance=0.1, content_length=5, metadata={},
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            it.rank = 2  # type: ignore[misc]


# ============================================================
# evaluate_top_k（RagEvaluationService 扩展）
# ============================================================

def _case(cid: str, query: str, keywords: tuple[str, ...]) -> RetrievalEvaluationCase:
    return RetrievalEvaluationCase(
        case_id=cid, query=query, expected_keywords=keywords
    )


class TestEvaluateTopK:
    async def test_prefix_truncation_and_hit_rates(self) -> None:
        """K=1 命中差、K=3 命中好：验证前缀截断 + 各档统计。"""
        # case_001：rank1 内容不含关键词，rank2 才含 → K=1 miss, K>=2 hit
        r1 = _vsr(1, 0.9, content="无关内容AAA")
        r2 = _vsr(2, 0.8, content="采购入库相关内容")
        # case_002：rank1 即命中
        r3 = _vsr(3, 0.95, content="销售出库流程")
        stub = StubVectorSearchService(
            {
                "查一": [r1, r2],
                "查二": [r3],
            }
        )
        svc = RagEvaluationService(vector_search_service=stub)
        summary = await svc.evaluate_top_k(
            [_case("c1", "查一", ("采购入库",)), _case("c2", "查二", ("销售出库",))],
            top_ks=(1, 3),
        )

        by_k = {e.top_k: e for e in summary.entries}
        # K=1：c1 miss（rank1 无关键词），c2 hit → 1/2
        assert by_k[1].hit_count == 1
        assert by_k[1].hit_rate == pytest.approx(0.5)
        # K=3：c1 的 rank2 进入前缀 → hit → 2/2
        assert by_k[3].hit_count == 2
        assert by_k[3].hit_rate == pytest.approx(1.0)

    async def test_similarity_stats_per_k(self) -> None:
        stub = StubVectorSearchService(
            {"q": [_vsr(1, 0.9), _vsr(2, 0.8), _vsr(3, 0.7)]}
        )
        svc = RagEvaluationService(vector_search_service=stub)
        summary = await svc.evaluate_top_k(
            [_case("c", "q", ("无关",))], top_ks=(1, 2, 3)
        )
        by_k = {e.top_k: e for e in summary.entries}

        # K=1：只有 0.9
        assert by_k[1].average_similarity == pytest.approx(0.9)
        assert by_k[1].min_similarity == pytest.approx(0.9)
        assert by_k[1].max_similarity == pytest.approx(0.9)
        # K=2：(0.9+0.8)/2
        assert by_k[2].average_similarity == pytest.approx(0.85)
        assert by_k[2].min_similarity == pytest.approx(0.8)
        # K=3：(0.9+0.8+0.7)/3
        assert by_k[3].average_similarity == pytest.approx(0.8)
        assert by_k[3].min_similarity == pytest.approx(0.7)
        assert by_k[3].max_similarity == pytest.approx(0.9)

    async def test_one_embedding_call_per_case_not_per_k(self) -> None:
        """每个 case 只发起一次 search（去重 max top_k），而非每档一次。"""
        stub = StubVectorSearchService({"q": [_vsr(1, 0.9)]})
        svc = RagEvaluationService(vector_search_service=stub)
        await svc.evaluate_top_k(
            [_case("c1", "q", ("x",)), _case("c2", "q", ("x",))],
            top_ks=(1, 3, 5, 10),
        )
        assert len(stub.top_k_calls) == 2  # 2 cases × 1 次
        assert stub.top_k_calls == [10, 10]  # 均取 max(top_ks)

    async def test_search_error_counted_not_raised(self) -> None:
        stub = StubVectorSearchService(raise_error=RuntimeError("embedding down"))
        svc = RagEvaluationService(vector_search_service=stub)
        summary = await svc.evaluate_top_k(
            [_case("c", "q", ("x",))], top_ks=(1, 5)
        )
        for e in summary.entries:
            assert e.hit_count == 0
            assert e.error_count == 1
            assert e.average_similarity == 0.0
            assert e.min_similarity is None
        assert all(r.error for r in summary.entries[0].results)

    async def test_empty_cases_returns_zero_entries(self) -> None:
        stub = StubVectorSearchService()
        svc = RagEvaluationService(vector_search_service=stub)
        summary = await svc.evaluate_top_k([], top_ks=(1, 5, 10))
        assert summary.top_ks == (1, 5, 10)
        for e in summary.entries:
            assert e.case_count == 0
            assert e.hit_rate == 0.0
        assert stub.top_k_calls == []  # 未发起任何检索

    async def test_duplicate_top_ks_deduped(self) -> None:
        stub = StubVectorSearchService({"q": [_vsr(1, 0.9)]})
        svc = RagEvaluationService(vector_search_service=stub)
        summary = await svc.evaluate_top_k(
            [_case("c", "q", ("x",))], top_ks=(5, 5, 3)
        )
        assert summary.top_ks == (5, 3)

    async def test_existing_evaluate_api_unchanged(self) -> None:
        """原 evaluate() 行为不受影响（同一 stub 上对比）。"""
        r1 = _vsr(1, 0.9, content="采购入库")
        stub = StubVectorSearchService({"q": [r1]})
        svc = RagEvaluationService(vector_search_service=stub)
        s = await svc.evaluate([_case("c", "q", ("采购入库",))], top_k=5)
        assert s.total_cases == 1
        assert s.matched_cases == 1
        assert s.hit_rate == pytest.approx(1.0)
        assert s.results[0].matched is True