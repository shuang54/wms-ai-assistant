"""Reranker Evaluation Service（Phase 3.5.12，离线实验）。

回答唯一问题：

    对当前 WMS 知识库，Reranker 是否能改善 Vector Search 的结果排序？

Pipeline（每 case）：

    Evaluation Case
          ↓
    VectorSearchService.search(query, top_k=retrieval_top_k)   ← 复用，不改
          ↓
    Candidate Top-10（原始序 = similarity 降序）
          ↓
    ┌──────────────────────────┬──────────────────────────┐
    │ Baseline：直接取前 5     │ Reranked：Reranker 打分   │
    │                          │   → 按 score 排序 → 前 5  │
    └──────────────┬───────────┴──────────────┬───────────┘
                   ↓                           ↓
              Hit@1 / Hit@5 / MRR@5       Hit@1 / Hit@5 / MRR@5

匹配语义（与 rag_evaluation_service 完全一致）：
    某 result 为「正确 chunk」⇔ 其 content 或 metadata
    （source / title / heading_path_text）包含 case 的**全部**
    expected_keywords（小写化后子串匹配）。

不调用 DeepSeek；不修改生产 RAG；不写数据库。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from backend.app.reranker.client import RerankerClient
from backend.app.reranker.exceptions import RerankerModelError
from backend.app.services.rag_evaluation_service import (
    RetrievalEvaluationCase,
    _metadata_value,
    _norm,
)
from backend.app.services.vector_search_service import (
    VectorSearchResult,
    VectorSearchService,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RerankedResult",
    "RerankerEvaluationCaseResult",
    "RerankerEvaluationSummary",
    "RerankerEvaluationService",
]


# ============================================================
# DTO（frozen；不含 embedding / API Key / DB connection / SQL / chunk 全文）
# ============================================================

@dataclass(frozen=True)
class RerankedResult:
    """重排后 Top-N 中单条结果（不含 chunk content 全文）。"""

    original_rank: int      # 在 Vector Search 候选中的原始名次（1-based）
    rerank_rank: int        # 重排后名次（1-based）
    chunk_id: int
    document_id: int
    chunk_index: int
    vector_similarity: float
    reranker_score: float


@dataclass(frozen=True)
class RerankerEvaluationCaseResult:
    """单 case 的 Baseline vs Reranked 对比。

    rank 口径（全部 1-based；None = Top-5 内未出现正确 chunk）：
        baseline_rank: 正确 chunk 在 Baseline（原始序取前 5）中的位置
        reranked_rank: 正确 chunk 在 Reranked（重排序取前 5）中的位置
    reciprocal: 1/rank（无 → 0.0）
    change: improved / unchanged / degraded（按首个正确 chunk 位置比较）
    """

    case_id: str
    query: str
    retrieval_top_k: int
    rerank_top_k: int
    baseline_rank: int | None
    reranked_rank: int | None
    baseline_reciprocal: float
    reranked_reciprocal: float
    baseline_hit: bool
    reranked_hit: bool
    change: str
    results: tuple[RerankedResult, ...] = field(default_factory=tuple)
    error: str | None = None


@dataclass(frozen=True)
class RerankerEvaluationSummary:
    """离线实验汇总（Baseline = Vector Search 原始排序取前 rerank_top_k）。"""

    case_count: int
    baseline_hit_rate_at_1: float
    reranked_hit_rate_at_1: float
    baseline_hit_rate_at_5: float
    reranked_hit_rate_at_5: float
    baseline_mrr_at_5: float
    reranked_mrr_at_5: float
    improved_cases: int
    degraded_cases: int
    unchanged_cases: int
    results: tuple[RerankerEvaluationCaseResult, ...] = field(default_factory=tuple)


# ============================================================
# 匹配（复用 rag_evaluation_service 的规范化逻辑）
# ============================================================

def _result_matches(
    result: VectorSearchResult, case: RetrievalEvaluationCase
) -> bool:
    """该 result 是否为「正确 chunk」：包含 case 的全部 expected_keywords。

    与 RagEvaluationService._match_case 的关键词匹配语义一致
    （content 或 metadata(source/title/heading_path_text) 命中即可，
    小写子串匹配）。
    """
    if not case.expected_keywords:
        return True  # 无关键词要求 → 任何结果都算正确
    text = _norm(result.content)
    meta = _norm(_metadata_value(result.metadata, "source", "title", "heading_path_text"))
    return all(_norm(kw) in text or _norm(kw) in meta for kw in case.expected_keywords)


def _first_rank(
    results: list[VectorSearchResult], case: RetrievalEvaluationCase
) -> int | None:
    """首个正确 chunk 的 1-based 位置；无 → None。"""
    for idx, r in enumerate(results, start=1):
        if _result_matches(r, case):
            return idx
    return None


# ============================================================
# Service
# ============================================================

class RerankerEvaluationService:
    """Reranker 离线评估服务（只读；不接生产 RAG）。"""

    def __init__(
        self,
        *,
        vector_search_service: VectorSearchService,
        reranker_client: RerankerClient,
    ) -> None:
        self._vector_search_service = vector_search_service
        self._reranker_client = reranker_client

    async def evaluate(
        self,
        cases: list[RetrievalEvaluationCase],
        *,
        retrieval_top_k: int = 10,
        rerank_top_k: int = 5,
    ) -> RerankerEvaluationSummary:
        """对 cases 执行 Baseline vs Reranked 对比评估。

        每 case 只发起一次 Vector Search（top_k=retrieval_top_k），
        候选文档仅传给 Reranker 内部使用（不进入 DTO）。

        单 case 失败策略：search / rerank 异常 → 该 case 记为 error，
        两组指标均按 miss 处理，不阻断其余 case。
        """
        case_results: list[RerankerEvaluationCaseResult] = []
        n = len(cases)

        for case in cases:
            try:
                candidates = await self._vector_search_service.search(
                    case.query, top_k=retrieval_top_k
                )
                if not candidates:
                    case_results.append(
                        RerankerEvaluationCaseResult(
                            case_id=case.case_id,
                            query=case.query,
                            retrieval_top_k=retrieval_top_k,
                            rerank_top_k=rerank_top_k,
                            baseline_rank=None,
                            reranked_rank=None,
                            baseline_reciprocal=0.0,
                            reranked_reciprocal=0.0,
                            baseline_hit=False,
                            reranked_hit=False,
                            change="unchanged",
                            error="no candidates retrieved",
                        )
                    )
                    continue

                # ---- Reranker 打分（chunk content 只在内部使用）----
                scores = await self._reranker_client.rerank(
                    case.query, [r.content for r in candidates]
                )
                if len(scores) != len(candidates):
                    raise RerankerModelError(
                        f"reranker score 数量与候选不一致"
                        f"（{len(scores)} vs {len(candidates)}）"
                    )

                # ---- Reranked 顺序：score 降序；平分保持原始序（稳定）----
                order = sorted(
                    range(len(candidates)), key=lambda i: (-scores[i], i)
                )
                reranked_candidates = [candidates[i] for i in order]

                # ---- Baseline：原始序取前 rerank_top_k ----
                baseline_top = candidates[:rerank_top_k]
                # ---- Reranked：重排序取前 rerank_top_k ----
                reranked_top = reranked_candidates[:rerank_top_k]

                baseline_rank = _first_rank(baseline_top, case)
                reranked_rank_full = _first_rank(reranked_candidates, case)
                reranked_rank = (
                    reranked_rank_full if reranked_rank_full is not None else None
                )
                # 注意：reranked_rank 统一在完整重序列上计算（语义更准），
                # hit 判断以 rerank_top_k 为界

                base_rr = 1.0 / baseline_rank if baseline_rank else 0.0
                rerank_rr = 1.0 / reranked_rank if reranked_rank else 0.0

                # change：比较首个正确 chunk 的位置（越靠前越好）
                if baseline_rank == reranked_rank or (
                    baseline_rank is None and reranked_rank is None
                ):
                    change = "unchanged"
                elif reranked_rank is None or (
                    baseline_rank is not None and reranked_rank > baseline_rank
                ):
                    change = "degraded"
                else:
                    change = "improved"

                # ---- 重排后 Top-5 详情（RerankedResult，不含全文）----
                details = tuple(
                    RerankedResult(
                        original_rank=i + 1,
                        rerank_rank=pos + 1,
                        chunk_id=candidates[i].chunk_id,
                        document_id=candidates[i].document_id,
                        chunk_index=candidates[i].chunk_index,
                        vector_similarity=candidates[i].similarity,
                        reranker_score=scores[i],
                    )
                    for pos, i in enumerate(order[:rerank_top_k])
                )

                case_results.append(
                    RerankerEvaluationCaseResult(
                        case_id=case.case_id,
                        query=case.query,
                        retrieval_top_k=retrieval_top_k,
                        rerank_top_k=rerank_top_k,
                        baseline_rank=baseline_rank,
                        reranked_rank=reranked_rank,
                        baseline_reciprocal=base_rr,
                        reranked_reciprocal=rerank_rr,
                        baseline_hit=baseline_rank is not None,
                        reranked_hit=(
                            reranked_rank is not None
                            and reranked_rank <= rerank_top_k
                        ),
                        change=change,
                        results=details,
                    )
                )
            except Exception as exc:  # noqa: BLE001 — 评估记录错误而非吞掉
                logger.warning(
                    "reranker evaluation case failed: case_id=%s error=%s",
                    case.case_id,
                    exc,
                    extra={"case_id": case.case_id, "error_type": type(exc).__name__},
                )
                case_results.append(
                    RerankerEvaluationCaseResult(
                        case_id=case.case_id,
                        query=case.query,
                        retrieval_top_k=retrieval_top_k,
                        rerank_top_k=rerank_top_k,
                        baseline_rank=None,
                        reranked_rank=None,
                        baseline_reciprocal=0.0,
                        reranked_reciprocal=0.0,
                        baseline_hit=False,
                        reranked_hit=False,
                        change="unchanged",
                        error=str(exc),
                    )
                )

        def _rate(pred) -> float:
            return (sum(1 for r in case_results if pred(r)) / n) if n else 0.0

        return RerankerEvaluationSummary(
            case_count=n,
            baseline_hit_rate_at_1=_rate(lambda r: r.baseline_rank == 1),
            reranked_hit_rate_at_1=_rate(lambda r: r.reranked_rank == 1),
            baseline_hit_rate_at_5=_rate(lambda r: r.baseline_hit),
            reranked_hit_rate_at_5=_rate(lambda r: r.reranked_hit),
            baseline_mrr_at_5=(
                sum(r.baseline_reciprocal for r in case_results) / n
            )
            if n
            else 0.0,
            reranked_mrr_at_5=(sum(r.reranked_reciprocal for r in case_results) / n)
            if n
            else 0.0,
            improved_cases=sum(1 for r in case_results if r.change == "improved"),
            degraded_cases=sum(1 for r in case_results if r.change == "degraded"),
            unchanged_cases=sum(1 for r in case_results if r.change == "unchanged"),
            results=tuple(case_results),
        )
