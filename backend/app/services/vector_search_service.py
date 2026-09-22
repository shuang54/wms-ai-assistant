"""Vector Search Service（Phase 3.5.3）。

最小可用的向量检索能力：

        user query text
                ↓
        EmbeddingClient.embed()         （Phase 3.5.1，BAAI/bge-m3，1024 维）
        1024-dim query vector
                ↓
        VectorSearchService.search()
                ↓
        PostgreSQL + pgvector `embedding <=> :query_vec`
                ↓
        VectorSearchResult[]（按 distance 升序 = 相似度降序）

设计要点：

- 职责单一：
    - 校验输入（query 非空 / top_k 在合法范围）
    - 调用 EmbeddingClient（业务不重复实现 Embedding）
    - 校验 query vector 维度（防御 Mock / 自定义实现绕过 EmbeddingClient 内部校验）
    - 执行 pgvector cosine distance 查询
    - 映射 ORM 行 → VectorSearchResult DTO（**不把 ORM Model 暴露给上层**）

- 相似度算法：
    - pgvector `<=>` 操作符 = cosine **distance**
        * distance ∈ [0, 2]
        * distance 越小 → 越相似
        * distance = 0  ⇔  cosine_similarity = 1
        * distance = 1  ⇔  cosine_similarity = 0
        * distance = 2  ⇔  cosine_similarity = -1
    - DTO 同时返回 `distance` 与 `similarity`（`similarity = 1 - distance`），
      由调用方按需取用，绝不混用。

- 不做的事情（保持 Simple First）：
    - 不做混合检索（BM25 / 多路召回 / reranker）
    - 不做 RAG Prompt 拼接 / LLM 调用
    - 不做批量搜索接口
    - 不实现任何权限 / 业务校验（属于后续 Phase）
    - 不暴露 ORM 行 / Session 给上层

- pgvector 索引策略（**本阶段不创建**）：
    - 当前数据量极小（Phase 3.5.2 起才有向量写入），全表 `<=>` 排序的代价可接受。
    - 显式决策：暂时不创建 HNSW / IVFFlat 索引。
    - 待 `knowledge_chunk` 行数 / 单库体量明确后，再在迁移 SQL 中按数据特征
      选择 HNSW（recall 优先）或 IVFFlat（速度优先）——本模块不引入 Alembic
      或 Migration 框架（与 Phase 3.1 一致，详见 `backend/app/db/init_db.py`）。
    - 该决策在 Phase 3.5.1.6 迁移文档中已说明
      （docs/decisions/embedding-dimension.md §3 / Phase 3.2）。

- 安全：
    - 日志绝不记录 query embedding 完整内容 / API Key；
      仅记录 query_length / top_k / result_count / elapsed_ms。
    - Embedding 错误原样透传（绝不返回 `[]` 掩盖失败）。
    - 不修改、不截断、不补零 query vector（维度不一致直接抛
      EmbeddingDimensionError，不发起 DB 查询）。

异常体系：

    VectorSearchError (Exception)
        ├── VectorSearchInputError       空 / 纯空白 query
        └── VectorSearchParameterError  top_k 非法

    EmbeddingError 家族（EmbeddingAPIError / EmbeddingDimensionError / ...）
    原样透传，调用方可在更上层统一捕获。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Float, cast, select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.db.models import KnowledgeChunk
from backend.app.db.session import get_session_factory
from backend.app.embedding.client import EmbeddingClient, get_default_embedding_client

logger = logging.getLogger(__name__)

__all__ = [
    "VectorSearchResult",
    "VectorSearchError",
    "VectorSearchInputError",
    "VectorSearchParameterError",
    "VectorSearchService",
]


# ============================================================
# 异常体系
# ============================================================

class VectorSearchError(Exception):
    """Vector Search Service 通用异常基类。

    Embedding 阶段错误（EmbeddingError 家族）由本 Service 原样透传，
    调用方按原类型区分；本类承载 Search 自身的错误（输入非法 / 参数非法）。
    """


class VectorSearchInputError(VectorSearchError):
    """查询文本非法：空字符串 / 纯空白 / 非 str。

    在调用 EmbeddingClient 之前拒绝，避免无效请求与 API 费用。
    """


class VectorSearchParameterError(VectorSearchError):
    """搜索参数非法：top_k 越界 / 非整数 / 非正整数。"""


# ============================================================
# 结果结构（DTO）
# ============================================================

@dataclass(frozen=True)
class VectorSearchResult:
    """向量检索的单条命中结果（DTO，不暴露 ORM 行）。

    字段：
        chunk_id:     KnowledgeChunk 主键
        document_id:  所属文档主键
        chunk_index:  Chunk 在原文中的顺序（从 0 开始）
        content:      Chunk 文本内容
        distance:     cosine distance，pgvector `<=>` 算子结果（越小越相似）
        similarity:   cosine similarity = 1 - distance（越大越相似）
        metadata:     KnowledgeChunk.meta_data 字典（None 时为空 dict）
    """

    chunk_id: int
    document_id: int
    chunk_index: int
    content: str
    distance: float
    similarity: float
    metadata: dict


# ============================================================
# Service
# ============================================================

class VectorSearchService:
    """向量检索 Service（Phase 3.5.3）。

    仅负责：
        query → Embedding → pgvector cosine search → DTO

    业务规则、权限、Prompt 拼接、LLM 调用均不属于本 Service。
    """

    # top_k 上下界（与任务书 §六 保持一致；常量集中便于测试断言）
    MIN_TOP_K: int = 1
    MAX_TOP_K: int = 50
    DEFAULT_TOP_K: int = 5

    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient | None = None,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        """
        Args:
            embedding_client: Embedding 客户端；None 时懒加载默认单例
                （首次 search 时构造；配置缺失抛 EmbeddingConfigurationError）。
            session_factory: Session 工厂；None 时用全局 get_session_factory()
                （DATABASE_URL 为空则 search 时抛 RuntimeError；
                 由调用方决定如何处理 / 降级）。
        """
        self._embedding_client = embedding_client
        self._session_factory = session_factory

    # ---------- 依赖解析（懒加载） ----------

    def _get_embedding_client(self) -> EmbeddingClient:
        if self._embedding_client is None:
            self._embedding_client = get_default_embedding_client()
        return self._embedding_client

    def _get_session_factory(self) -> Callable[[], Session]:
        if self._session_factory is not None:
            return self._session_factory
        factory = get_session_factory()
        if factory is None:
            raise RuntimeError(
                "DATABASE_URL 未配置，无法执行向量检索。"
            )
        return factory

    # ---------- 校验 ----------

    @staticmethod
    def _validate_query(query: str) -> str:
        """校验 query：非 str / 空 / 纯空白 → 拒绝，不调用 Embedding API。"""
        if not isinstance(query, str):
            raise VectorSearchInputError(
                f"查询文本必须是 str（当前: {type(query).__name__}）"
            )
        stripped = query.strip()
        if not stripped:
            raise VectorSearchInputError(
                "查询文本为空或纯空白，已拒绝（不调用 Embedding API）"
            )
        return query

    def _validate_top_k(self, top_k: int) -> int:
        """校验 top_k：必须为 int 且在 [MIN_TOP_K, MAX_TOP_K] 区间。"""
        # bool 是 int 子类，必须先排除
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise VectorSearchParameterError(
                f"top_k 必须是整数（当前: {type(top_k).__name__}）"
            )
        if top_k < self.MIN_TOP_K or top_k > self.MAX_TOP_K:
            raise VectorSearchParameterError(
                f"top_k 超出合法范围 [{self.MIN_TOP_K}, {self.MAX_TOP_K}]"
                f"（当前: {top_k}）"
            )
        return top_k

    # ---------- 主流程 ----------

    async def search(
        self,
        query: str,
        *,
        top_k: int = DEFAULT_TOP_K,
    ) -> list[VectorSearchResult]:
        """对 query 执行向量检索，返回 top_k 个最相似的 KnowledgeChunk。

        Pipeline:
            1. 校验 query（空 / 纯空白 → VectorSearchInputError）
            2. 校验 top_k（非法 → VectorSearchParameterError）
            3. 调用 EmbeddingClient.embed(query) → query_embedding
               （EmbeddingError 家族原样透传）
            4. 校验 query_embedding 维度 == settings.embedding.dimension
               （维度错 → EmbeddingDimensionError，不发起 DB 查询）
            5. SQL：SELECT ... FROM knowledge_chunk
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> :query_embedding
                LIMIT :top_k
            6. ORM 行 → VectorSearchResult DTO
            7. 返回 DTO 列表（按 distance 升序）

        Returns:
            命中结果列表（无命中 → []）。列表已按 cosine distance 升序，
            即 cosine similarity 降序。

        Raises:
            VectorSearchInputError:      query 为空 / 纯空白 / 非 str
            VectorSearchParameterError:  top_k 非法
            EmbeddingConfigurationError / EmbeddingInputError /
            EmbeddingAPIError / EmbeddingResponseError /
            EmbeddingDimensionError:     Embedding 阶段错误（**原样透传**）
            RuntimeError:                DATABASE_URL 未配置（无法执行检索）
        """
        started = time.perf_counter()

        # ---- 1. 校验 ----
        query = self._validate_query(query)
        top_k = self._validate_top_k(top_k)

        # ---- 2. Embedding（异常原样透传）----
        embedding_client = self._get_embedding_client()
        query_embedding = await embedding_client.embed(query)

        # ---- 3. 维度校验（防御）----
        # EmbeddingClient 内部已校验；此处防御 Mock / 自定义实现绕过
        expected_dim = settings.embedding.dimension
        # 延迟导入避免循环依赖（exceptions 模块独立）
        from backend.app.embedding.exceptions import EmbeddingDimensionError

        if len(query_embedding) != expected_dim:
            raise EmbeddingDimensionError(
                f"query embedding 维度与配置不匹配：得到 {len(query_embedding)} 维，"
                f"EMBEDDING_DIMENSION 配置为 {expected_dim} 维；"
                "已阻止向量检索（不截断、不补零、不发起 DB 查询）。"
            )

        # ---- 4. pgvector cosine search ----
        # pgvector `<=>` 算子 = cosine distance（float）；ORDER BY 用同一表达式
        # 以避免重复计算。
        # 注意：必须显式 cast 至 Float——pgvector.sqlalchemy 为 Vector 列注册的
        # comparator 会让 `<=>` 表达式的输出类型被推断为 Vector，导致结果处理
        # 阶段用 `_from_text` 解析 float 值。
        distance_expr = cast(
            KnowledgeChunk.embedding.op("<=>")(query_embedding),
            Float,
        )

        stmt = (
            select(
                KnowledgeChunk.id,
                KnowledgeChunk.document_id,
                KnowledgeChunk.chunk_index,
                KnowledgeChunk.content,
                KnowledgeChunk.meta_data,
                distance_expr.label("distance"),
            )
            .where(KnowledgeChunk.embedding.is_not(None))
            .order_by(distance_expr)
            .limit(top_k)
        )

        factory = self._get_session_factory()
        with factory() as session:
            rows = session.execute(stmt).all()

        # ---- 5. ORM 行 → DTO ----
        results: list[VectorSearchResult] = []
        for row in rows:
            chunk_id, document_id, chunk_index, content, meta_data, distance = row
            # pgvector distance 为 float；防御性 cast 兼容 Decimal / numpy 类型
            distance_f = float(distance)
            results.append(
                VectorSearchResult(
                    chunk_id=int(chunk_id),
                    document_id=int(document_id),
                    chunk_index=int(chunk_index),
                    content=content,
                    distance=distance_f,
                    similarity=1.0 - distance_f,
                    metadata=dict(meta_data) if meta_data is not None else {},
                )
            )

        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "Vector search completed",
            extra={
                "query_length": len(query),
                "top_k": top_k,
                "result_count": len(results),
                "embedding_dimension": expected_dim,
                "elapsed_ms": elapsed_ms,
            },
        )

        return results