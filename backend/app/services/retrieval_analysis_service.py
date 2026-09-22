"""Retrieval Analysis Service（Phase 3.5.11）。

目的：在现有

        query
          ↓
    EmbeddingClient.embed()          （BGE-M3，1024 维）
          ↓
    VectorSearchService.search()     （pgvector cosine）
          ↓
    VectorSearchResult[]
          ↓
    RAG

基础上，提供一个**轻量级、只读**的检索结果分析能力，回答：

    1. 一个 query 实际召回了哪些 chunk？
    2. 每个 chunk 的 similarity / distance 是多少？
    3. Top-K 的平均 / 最小 / 最大 similarity 如何？
    4. 是否存在明显低相关的结果？

为后续是否引入 Reranker / Hybrid Search 提供客观依据。

设计原则：

- **只复用，不重实现**：完全基于 `VectorSearchService.search()`；
  不重新实现 pgvector 查询、不重算 cosine similarity
  （similarity / distance 直接取自 VectorSearchResult）。
- **top_k 语义复用**：合法性校验与边界常量
  （`VectorSearchService.MIN_TOP_K / MAX_TOP_K / DEFAULT_TOP_K`）
  完全由底层 search() 承担；本 Service 不复制任何范围常量，
  非法 top_k → 底层 `VectorSearchParameterError` 原样传播。
- **只读**：不写任何表；不保存 query 到数据库。
- **DTO 纪律**：frozen dataclass；不暴露 ORM 行 / embedding 向量 /
  数据库连接 / API Key / SQL。分析条目**不携带 chunk 全文**
  （仅 content_length），避免日志与异常快照意外泄漏大段知识库内容。
- **日志纪律**：仅记录 query_length / top_k / result_count /
  avg / min / max similarity 与 elapsed_ms；绝不记录完整 embedding 向量。

本阶段明确**不做**（后续独立 Phase 决策）：

    - Reranker / Cross Encoder
    - Hybrid Search / BM25
    - HNSW / IVFFlat 索引
    - 修改 Top-K=5 的生产默认值
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from backend.app.services.vector_search_service import (
    VectorSearchResult,
    VectorSearchService,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RetrievalAnalysisItem",
    "RetrievalAnalysisResult",
    "RetrievalAnalysisService",
]


# ============================================================
# DTO（frozen dataclass；不暴露 ORM / embedding / DB 连接）
# ============================================================

@dataclass(frozen=True)
class RetrievalAnalysisItem:
    """单条召回结果的分析条目。

    字段：
        rank:           在本次 Top-K 中的名次（从 1 开始，1 = 最相似）
        chunk_id:       KnowledgeChunk 主键
        document_id:    所属文档主键
        chunk_index:    Chunk 在原文中的顺序（从 0 开始）
        similarity:     cosine similarity（直接取自 VectorSearchResult）
        distance:       cosine distance（直接取自 VectorSearchResult）
        content_length: chunk 文本长度（字符数；不携带全文）
        metadata:       chunk 的 meta_data 字典（heading / source 等）

    刻意**不**包含：
        - chunk content 全文（只给长度，避免大段内容进入日志 / 报告）
        - embedding 向量
        - ORM 对象 / Session
    """

    rank: int
    chunk_id: int
    document_id: int
    chunk_index: int
    similarity: float
    distance: float
    content_length: int
    metadata: dict


@dataclass(frozen=True)
class RetrievalAnalysisResult:
    """一次 analyze 调用的完整分析结果。

    空结果约定（任务书 §七）：
        result_count     = 0
        items            = ()
        average_similarity = 0.0
        min_similarity   = None
        max_similarity   = None
    """

    query: str
    top_k: int
    result_count: int
    items: tuple[RetrievalAnalysisItem, ...]
    average_similarity: float
    min_similarity: float | None
    max_similarity: float | None


# ============================================================
# Service
# ============================================================

class RetrievalAnalysisService:
    """检索结果分析 Service（只读，零写入）。

    Pipeline（全部复用现有组件）：

        query
          ↓
        VectorSearchService.search(query, top_k)   ← 唯一的检索入口
          ↓
        rank / similarity / distance / content_length / metadata 提取
          ↓
        average / min / max similarity 统计
          ↓
        RetrievalAnalysisResult
    """

    def __init__(
        self,
        *,
        vector_search_service: VectorSearchService | None = None,
        vector_search_factory: Callable[[], VectorSearchService] | None = None,
    ) -> None:
        """Args:
            vector_search_service: 已构造的 VectorSearchService（测试注入用）。
            vector_search_factory: 懒加载工厂（与 service 二选一；
                两者都为 None 时，默认构造 `VectorSearchService()`）。
        """
        self._vector_search_service = vector_search_service
        self._vector_search_factory = vector_search_factory

    def _get_vector_search_service(self) -> VectorSearchService:
        if self._vector_search_service is not None:
            return self._vector_search_service
        if self._vector_search_factory is not None:
            self._vector_search_service = self._vector_search_factory()
        else:
            self._vector_search_service = VectorSearchService()
        return self._vector_search_service

    # ---------- 主流程 ----------

    async def analyze(
        self,
        query: str,
        *,
        top_k: int = VectorSearchService.DEFAULT_TOP_K,
    ) -> RetrievalAnalysisResult:
        """对单个 query 执行检索并生成可解释性分析。

        复用 `VectorSearchService.search()`：
            - query 校验（空 / 纯空白 → VectorSearchInputError）
            - top_k 校验（越界 → VectorSearchParameterError）
            - Embedding 错误家族原样透传
        本方法**不**捕获、不包装上述异常（任务书 §十.11：原样传播）。

        Returns:
            RetrievalAnalysisResult（空结果时按 §七.1 约定返回）。
        """
        started = time.perf_counter()

        # ---- 1. 检索（唯一入口；similarity / distance 直接来自该结果）----
        search_service = self._get_vector_search_service()
        results: list[VectorSearchResult] = await search_service.search(
            query, top_k=top_k
        )

        # ---- 2. 提取 + 统计 ----
        items = tuple(
            RetrievalAnalysisItem(
                rank=rank,
                chunk_id=r.chunk_id,
                document_id=r.document_id,
                chunk_index=r.chunk_index,
                similarity=r.similarity,
                distance=r.distance,
                content_length=len(r.content),
                metadata=dict(r.metadata) if r.metadata else {},
            )
            for rank, r in enumerate(results, start=1)
        )

        similarities = [r.similarity for r in results]
        if similarities:
            average_similarity = sum(similarities) / len(similarities)
            min_similarity: float | None = min(similarities)
            max_similarity: float | None = max(similarities)
        else:
            average_similarity = 0.0
            min_similarity = None
            max_similarity = None

        elapsed_ms = (time.perf_counter() - started) * 1000
        # 日志纪律：只记统计信息，不记 query 全文 / embedding 向量 / chunk 内容
        logger.info(
            "Retrieval analysis completed",
            extra={
                "query_length": len(query) if isinstance(query, str) else -1,
                "top_k": top_k,
                "result_count": len(results),
                "average_similarity": round(average_similarity, 4),
                "min_similarity": round(min_similarity, 4)
                if min_similarity is not None
                else None,
                "max_similarity": round(max_similarity, 4)
                if max_similarity is not None
                else None,
                "elapsed_ms": elapsed_ms,
            },
        )

        return RetrievalAnalysisResult(
            query=query,
            top_k=top_k,
            result_count=len(results),
            items=items,
            average_similarity=average_similarity,
            min_similarity=min_similarity,
            max_similarity=max_similarity,
        )
