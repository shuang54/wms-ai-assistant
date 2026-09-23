"""Reranker Evaluation Service 单元测试（Phase 3.5.12）。

覆盖任务书 §十五 Evaluation 部分：

    1. baseline 正确         2. reranker 排序正确   3. Top-K=10 candidate
    4. rerank_top_k=5        5. Hit Rate@1          6. Hit Rate@5
    7. MRR@5                 8. case_003 场景       9. case_011 场景
   10. empty cases           11. reranker error
   12. 不修改原 evaluation 行为

使用 Stub VectorSearchService + Stub RerankerClient（无 DB / 无 torch /
无真实 Embedding / 无 DeepSeek）。
"""
from __future__ import annotations

import dataclasses

import pytest

from backend.app.reranker.exceptions import RerankerModelError
from backend.app.services.rag_evaluation_service import (
    RagEvaluationService,
    RetrievalEvaluationCase,
)
from backend.app.services.reranker_evaluation_service import (
    RerankerEvaluationCaseResult,
    RerankerEvaluationService,
    RerankerEvaluationSummary,
    RerankedResult,
)
from backend.app.services.vector_search_service import (
    VectorSearchResult,
    VectorSearchService,
)


# ============================================================
# Stubs
# ============================================================

def _vsr(
    chunk_id: int,
    similarity: float,
    content: str,
    *,
    document_id: int = 1,
    chunk_index: int = 0,
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
    """按 query 返回固定候选（已按 similarity 降序）。"""

    def __init__(self, results_by_query: dict[str, list[VectorSearchResult]]) -> None:
        super().__init__()
        self._map = results_by_query
        self.top_k_calls: list[int] = []

    async def search(self, query: str, *, top_k: int = 5):  # type: ignore[override]
        self.top_k_calls.append(top_k)
        return self._map.get(query, [])[:top_k]


class StubRerankerClient:
    """按 (query, doc) 返回预设分数的桩。"""

    def __init__(
        self,
        score_fn=None,
        *,
        raise_error: Exception | None = None,
    ) -> None:
        self._score_fn = score_fn
        self._raise = raise_error
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        if self._raise is not None:
            raise self._raise
        self.calls.append((query, list(documents)))
        if self._score_fn is not None:
            return self._score_fn(query, documents)
        return [0.5] * len(documents)


def _case(cid: str, query: str, keywords: tuple[str, ...]) -> RetrievalEvaluationCase:
    return RetrievalEvaluationCase(case_id=cid, query=query, expected_keywords=keywords)


def _svc(
    results_by_query: dict[str, list[VectorSearchResult]],
    score_fn=None,
    *,
    raise_error: Exception | None = None,
) -> tuple[RerankerEvaluationService, StubVectorSearchService, StubRerankerClient]:
    vs = StubVectorSearchService(results_by_query)
    rr = StubRerankerClient(score_fn, raise_error=raise_error)
    return RerankerEvaluationService(vector_search_service=vs, reranker_client=rr), vs, rr


# ============================================================
# 1-4. baseline / 排序 / candidate / rerank_top_k
# ============================================================

class TestBasics:
    async def test_retrieval_uses_top_k_10(self) -> None:
        cands = [_vsr(1, 0.9, "甲"), _vsr(2, 0.8, "乙")]
        svc, vs, _ = _svc({"q": cands})
        await svc.evaluate([_case("c", "q", ("甲",))])
        assert vs.top_k_calls == [10]  # retrieval_top_k=10

    async def test_reranker_receives_candidate_contents(self) -> None:
        cands = [
            _vsr(1, 0.9, "文档甲"),
            _vsr(2, 0.8, "文档乙"),
        ]
        svc, _, rr = _svc({"q": cands})
        await svc.evaluate([_case("c", "q", ("文档",))])
        assert rr.calls == [("q", ["文档甲", "文档乙"])]

    async def test_rerank_orders_by_score_desc(self) -> None:
        # 3 个候选：reranker 认为第 3 个最好（0.95）
        cands = [
            _vsr(1, 0.9, "无关A"),
            _vsr(2, 0.8, "无关B"),
            _vsr(3, 0.7, "目标内容"),
        ]
        svc, _, _ = _svc(
            {"q": cands}, score_fn=lambda q, docs: [0.3, 0.2, 0.95]
        )
        summary = await svc.evaluate([_case("c", "q", ("目标内容",))])

        r = summary.results[0]
        assert r.reranked_rank == 1  # 被推到第 1
        assert r.baseline_rank == 3   # 原始第 3
        assert r.change == "improved"
        # Top-1 命中：reranked hit@1 = 1，baseline hit@1 = 0
        assert summary.reranked_hit_rate_at_1 == 1.0
        assert summary.baseline_hit_rate_at_1 == 0.0

    async def test_rerank_top_k_5_details(self) -> None:
        cands = [_vsr(i, 0.9 - i * 0.05, f"内容{i}") for i in range(1, 9)]
        svc, _, _ = _svc({"q": cands}, score_fn=lambda q, docs: [0.1] * len(docs))
        summary = await svc.evaluate([_case("c", "q", ("内容",))])
        details = summary.results[0].results
        assert len(details) == 5  # rerank_top_k=5
        assert all(isinstance(d, RerankedResult) for d in details)

    async def test_tie_scores_keep_original_order(self) -> None:
        cands = [_vsr(1, 0.9, "甲"), _vsr(2, 0.8, "乙")]
        svc, _, _ = _svc({"q": cands}, score_fn=lambda q, d: [0.5, 0.5])
        summary = await svc.evaluate([_case("c", "q", ("甲",))])
        assert summary.results[0].reranked_rank == 1


# ============================================================
# 5-7. Hit@1 / Hit@5 / MRR@5
# ============================================================

class TestMetrics:
    async def test_hit_at_1(self) -> None:
        # case1：原始 rank1 正确；case2：原始 rank1 错、rank2 对但 rerank 后仍第 2
        q1 = [_vsr(1, 0.9, "目标"), _vsr(2, 0.8, "其他")]
        q2 = [_vsr(3, 0.9, "其他"), _vsr(4, 0.8, "目标")]
        svc, _, _ = _svc(
            {"q1": q1, "q2": q2},
            score_fn=lambda q, docs: [0.9 if "目标" in d else 0.1 for d in docs],
        )
        summary = await svc.evaluate(
            [_case("c1", "q1", ("目标",)), _case("c2", "q2", ("目标",))]
        )
        assert summary.baseline_hit_rate_at_1 == 0.5  # 只有 c1
        assert summary.reranked_hit_rate_at_1 == 1.0  # c2 被推到第 1

    async def test_hit_at_5(self) -> None:
        # 正确 chunk 原始 rank 7（Top-5 外）→ rerank 后 rank 1（Top-5 内）
        cands = [(_vsr(i, 0.9 - i * 0.01, f"无关{i}")) for i in range(1, 7)]
        cands.append(_vsr(7, 0.5, "目标内容"))
        svc, _, _ = _svc(
            {"q": cands}, score_fn=lambda q, d: [0.95 if "目标" in x else 0.1 for x in d]
        )
        summary = await svc.evaluate([_case("c", "q", ("目标",))])
        assert summary.baseline_hit_rate_at_5 == 0.0  # rank 7 > 5
        assert summary.reranked_hit_rate_at_5 == 1.0

    async def test_mrr_at_5(self) -> None:
        # case1：rank1 → rr=1.0；case2：rerank 后 rank2 → rr=0.5；MRR=0.75
        q1 = [_vsr(1, 0.9, "目标"), _vsr(2, 0.8, "其他")]
        q2 = [_vsr(3, 0.9, "其他"), _vsr(4, 0.85, "目标"), _vsr(5, 0.8, "无关")]
        svc, _, _ = _svc(
            {"q1": q1, "q2": q2},
            score_fn=lambda q, docs: [0.9 if "目标" in d else 0.85 for d in docs],
        )
        summary = await svc.evaluate(
            [_case("c1", "q1", ("目标",)), _case("c2", "q2", ("目标",))]
        )
        # baseline：c1 rank1(1.0) + c2 rank2(0.5) → 0.75
        assert summary.baseline_mrr_at_5 == pytest.approx(0.75)
        # reranked：c1 rank1(1.0) + c2 被推到 rank1(1.0) → 1.0
        assert summary.reranked_mrr_at_5 == pytest.approx(1.0)

    async def test_no_hit_contributes_zero(self) -> None:
        cands = [_vsr(1, 0.9, "完全无关")]
        svc, _, _ = _svc({"q": cands})
        summary = await svc.evaluate([_case("c", "q", ("目标",))])
        assert summary.baseline_mrr_at_5 == 0.0
        assert summary.reranked_mrr_at_5 == 0.0
        assert summary.baseline_hit_rate_at_5 == 0.0
        assert summary.reranked_hit_rate_at_5 == 0.0

    async def test_multiple_keywords_all_required(self) -> None:
        # 单个文档只含一个关键词 → 不算正确 chunk（需全部命中）
        cands = [_vsr(1, 0.9, "甲关键词"), _vsr(2, 0.8, "乙关键词")]
        svc, _, _ = _svc({"q": cands})
        summary = await svc.evaluate([_case("c", "q", ("甲关键词", "乙关键词"))])
        assert summary.baseline_hit_rate_at_5 == 0.0

    async def test_keyword_match_via_metadata(self) -> None:
        cands = [_vsr(1, 0.9, "正文不含关键词", metadata={"title": "目标主题"})]
        svc, _, _ = _svc({"q": cands})
        summary = await svc.evaluate([_case("c", "q", ("目标",))])
        assert summary.baseline_hit_rate_at_5 == 1.0  # metadata 命中


# ============================================================
# 8-9. case_003 / case_011 场景（正确 chunk 在原始 rank 2）
# ============================================================

class TestCaseScenarios:
    @staticmethod
    async def _rank2_scenario() -> RerankerEvaluationSummary:
        """模拟 Phase 3.5.11 结论：正确 chunk 原始 rank=2。"""
        cands = [
            _vsr(1, 0.60, "相近但错误的知识"),
            _vsr(2, 0.58, "单据归档的正确知识"),
        ]
        return await _svc(
            {"q": cands},
            score_fn=lambda q, d: [0.2 if "错误" in x else 0.9 for x in d],
        )[0].evaluate([_case("case_x", "q", ("单据归档",))])

    async def test_case_rank2_promoted_to_rank1(self) -> None:
        summary = await self._rank2_scenario()
        r = summary.results[0]
        assert r.baseline_rank == 2
        assert r.reranked_rank == 1
        assert r.change == "improved"
        assert summary.improved_cases == 1
        assert summary.degraded_cases == 0
        assert summary.unchanged_cases == 0

    async def test_degraded_case_detection(self) -> None:
        # reranker 反而把正确 chunk 从 rank1 挤到 rank2
        cands = [
            _vsr(1, 0.9, "目标知识"),
            _vsr(2, 0.8, "干扰知识"),
        ]
        svc, _, _ = _svc(
            {"q": cands}, score_fn=lambda q, d: [0.1 if "目标" in x else 0.9 for x in d]
        )
        summary = await svc.evaluate([_case("c", "q", ("目标",))])
        assert summary.results[0].change == "degraded"
        assert summary.degraded_cases == 1


# ============================================================
# 10-11. empty cases / reranker error
# ============================================================

class TestEdgeCases:
    async def test_empty_cases(self) -> None:
        svc, vs, rr = _svc({})
        summary = await svc.evaluate([])
        assert summary.case_count == 0
        assert summary.baseline_hit_rate_at_5 == 0.0
        assert summary.reranked_mrr_at_5 == 0.0
        assert vs.top_k_calls == []
        assert rr.calls == []

    async def test_no_candidates_case_recorded(self) -> None:
        svc, _, _ = _svc({"q": []})
        summary = await svc.evaluate([_case("c", "q", ("目标",))])
        r = summary.results[0]
        assert r.error == "no candidates retrieved"
        assert r.baseline_hit is False and r.reranked_hit is False

    async def test_reranker_error_counted_not_raised(self) -> None:
        cands = [_vsr(1, 0.9, "目标")]
        svc, _, _ = _svc({"q": cands}, raise_error=RerankerModelError("load failed"))
        summary = await svc.evaluate([_case("c", "q", ("目标",))])
        assert summary.case_count == 1
        assert summary.results[0].error is not None
        assert summary.reranked_hit_rate_at_5 == 0.0

    async def test_search_error_counted_not_raised(self) -> None:
        class FailingSearch(StubVectorSearchService):
            async def search(self, query, *, top_k=5):  # type: ignore[no-untyped-def]
                raise RuntimeError("db down")

        vs = FailingSearch({})
        svc = RerankerEvaluationService(
            vector_search_service=vs,  # type: ignore[arg-type]
            reranker_client=StubRerankerClient(),
        )
        summary = await svc.evaluate([_case("c", "q", ("目标",))])
        assert summary.results[0].error is not None
        assert summary.case_count == 1


# ============================================================
# 12. 不修改原 evaluation 行为
# ============================================================

class TestNoRegressionToExistingEvaluation:
    async def test_existing_rag_evaluation_service_still_works(self) -> None:
        """RagEvaluationService（3.5.7/3.5.11）行为不受影响。"""
        cands = [_vsr(1, 0.9, "采购入库流程")]
        vs = StubVectorSearchService({"q": cands})
        svc = RagEvaluationService(vector_search_service=vs)
        s = await svc.evaluate([_case("c", "q", ("采购入库",))], top_k=5)
        assert s.total_cases == 1 and s.matched_cases == 1
        # evaluate_top_k 也仍在
        t = await svc.evaluate_top_k([_case("c", "q", ("采购入库",))], top_ks=(1, 5))
        assert t.entries[1].hit_rate == 1.0


# ============================================================
# DTO frozen
# ============================================================

class TestDtoFrozen:
    def test_summary_and_case_result_frozen(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            RerankerEvaluationSummary(
                case_count=0,
                baseline_hit_rate_at_1=0.0, reranked_hit_rate_at_1=0.0,
                baseline_hit_rate_at_5=0.0, reranked_hit_rate_at_5=0.0,
                baseline_mrr_at_5=0.0, reranked_mrr_at_5=0.0,
                improved_cases=0, degraded_cases=0, unchanged_cases=0,
            ).case_count = 5  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            RerankerEvaluationCaseResult(
                case_id="c", query="q", retrieval_top_k=10, rerank_top_k=5,
                baseline_rank=None, reranked_rank=None,
                baseline_reciprocal=0.0, reranked_reciprocal=0.0,
                baseline_hit=False, reranked_hit=False, change="unchanged",
            ).case_id = "x"  # type: ignore[misc]

    def test_reranked_result_frozen_and_no_content(self) -> None:
        fields_ = {f.name for f in dataclasses.fields(RerankedResult)}
        assert "content" not in fields_
        assert "embedding" not in fields_
        it = RerankedResult(
            original_rank=1, rerank_rank=1, chunk_id=1, document_id=1,
            chunk_index=0, vector_similarity=0.9, reranker_score=0.8,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            it.rerank_rank = 2  # type: ignore[misc]
