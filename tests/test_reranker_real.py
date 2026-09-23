"""Reranker 真实离线实验（Phase 3.5.12）。

默认 SKIP；仅当：

    RUN_REAL_RERANKER=1（+ RUN_DB_TESTS=1 / DATABASE_URL / EMBEDDING_API_KEY）
    且 RERANKER_ENABLED=true（显式确认使用本地 Reranker）

才执行。使用：

    - 真实 BGE-M3 Embedding（SiliconFlow API，检索候选）
    - 真实 pgvector（Vector Search Top-10）
    - 真实 BAAI/bge-reranker-v2-m3（本地 Cross-Encoder，优先 CUDA）
    - 真实知识库 docs/knowledge/wms-basic-operations.md（只读！）

实验（任务书 § 十六）：

    12 evaluation cases × Vector Top-K=10 × Rerank Top-K=5

**不调用 DeepSeek。** 不写数据库（只读）。

模型单例：整个实验进程只加载一次 Reranker（§ 十七）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app.config import settings
from backend.app.reranker.client import get_default_reranker_client
from backend.app.services.rag_evaluation_service import (
    load_cases_from_json,
)
from backend.app.services.reranker_evaluation_service import (
    RerankerEvaluationService,
)
from backend.app.services.vector_search_service import VectorSearchService

FIXTURES = Path(__file__).parent / "fixtures" / "rag"
EVALUATION_CASES = FIXTURES / "evaluation_cases.json"
REAL_KB_DOC = Path("docs/knowledge/wms-basic-operations.md")


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


# ============================================================
# DB 集成测试（RUN_DB_TESTS=1）：真实 pgvector 候选 + Mock Reranker
#
# 说明：这组测试用**确定性 Mock Reranker** 验证 RerankerEvaluationService
# 在真实 pgvector 检索候选上的端到端正确性（不加载真实模型，零 GPU/CPU 成本）。
# 真实模型实验见下方 TestRealRerankerExperiment（RUN_REAL_RERANKER=1）。
# ============================================================

requires_db = pytest.mark.skipif(
    not (_env_flag("RUN_DB_TESTS") and settings.database.url.strip()),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable reranker DB tests",
)


class MockEmbeddingClient:
    """确定性 Mock Embedding（与既有 DB 测试同策略，零 API 成本）。"""

    def __init__(self) -> None:
        self._dim = settings.embedding.dimension

    async def embed(self, txt: str) -> list[float]:
        seed = sum(txt.encode("utf-8"))
        return [((seed + i) % 97 + 1) / 1000.0 for i in range(self._dim)]


class MockKeywordRerankerClient:
    """确定性 Mock Reranker：包含 query 关键词的文档得高分。

    仅用于验证 RerankerEvaluationService 管线（打分 → 排序 → 指标），
    不代表真实模型质量。
    """

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        scores = []
        for doc in documents:
            overlap = sum(1 for ch in query if ch in doc)
            scores.append(min(0.99, 0.1 + overlap / max(len(query), 1)))
        return scores


@pytest.fixture(scope="module")
def _engine():
    from backend.app.db import reset_engine_cache
    from backend.app.db.base import Base
    from backend.app.db.session import get_engine

    reset_engine_cache()
    eng = get_engine()
    assert eng is not None
    from backend.app.db import models  # noqa: F401
    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture
def _clean_around_each(_engine):
    """测试前后各清一次（只清理本组自建数据；不触碰真实知识库场景）。

    本测试自建文档固定使用 file_name='reranker_db_test.md'；
    knowledge_chunk 的 document_id FK 带 ondelete=CASCADE，
    删除 document 行即可级联删除其 chunk。
    """

    def _truncate() -> None:
        from sqlalchemy import text

        with _engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM knowledge_document "
                    "WHERE file_name = 'reranker_db_test.md'"
                )
            )

    _truncate()
    yield
    _truncate()


@requires_db
class TestRerankerEvaluationWithRealPgVector:
    async def test_service_pipeline_on_real_candidates(
        self, tmp_path: Path, _engine, _clean_around_each
    ) -> None:
        from sqlalchemy import text

        from backend.app.services.knowledge_ingestion_service import (
            KnowledgeIngestionService,
        )
        from backend.app.services.vector_search_service import (
            VectorSearchService,
        )

        mock_embed = MockEmbeddingClient()
        ingest = KnowledgeIngestionService(embedding_client=mock_embed)
        doc = tmp_path / "reranker_db_test.md"
        doc.write_text(
            "# 测试\n\n## 主题甲\n\n" + ("主题甲详细内容。" * 40) + "\n\n"
            "## 主题乙\n\n" + ("主题乙详细内容。" * 40) + "\n",
            encoding="utf-8",
        )
        created = await ingest.ingest_one(doc)
        assert created.status == "ready"

        with _engine.connect() as conn:
            chunk_before = conn.execute(
                text("SELECT COUNT(*) FROM knowledge_chunk")
            ).scalar_one()

        svc = RerankerEvaluationService(
            vector_search_service=VectorSearchService(embedding_client=mock_embed),
            reranker_client=MockKeywordRerankerClient(),
        )
        from backend.app.services.rag_evaluation_service import (
            RetrievalEvaluationCase,
        )

        case = RetrievalEvaluationCase(
            case_id="db_c1", query="主题甲", expected_keywords=("主题甲",)
        )
        summary = await svc.evaluate([case], retrieval_top_k=10, rerank_top_k=5)

        assert summary.case_count == 1
        r = summary.results[0]
        assert r.error is None
        # 指标自洽
        assert 0.0 <= summary.baseline_hit_rate_at_5 <= 1.0
        assert 0.0 <= summary.reranked_hit_rate_at_5 <= 1.0
        assert 0.0 <= summary.reranked_mrr_at_5 <= 1.0
        # reranked top-5 详情完整（候选可能混入真实知识库 chunk，
        # 因此只校验字段自洽 + 自建文档进入 top-5，不限定全部来源）
        assert len(r.results) <= 5
        for d in r.results:
            assert d.rerank_rank >= 1
            assert 0.0 <= d.reranker_score <= 1.0
        own_results = [
            d for d in r.results if d.document_id == created.document_id
        ]
        assert own_results, "自建文档应进入 reranked top-5"
        # 确定性 mock：查询关键词与自建文档重合度最高，应排第一
        assert own_results[0].rerank_rank == 1
        # 排序：reranker_score 降序
        scores = [d.reranker_score for d in r.results]
        assert scores == sorted(scores, reverse=True)
        # 只读：chunk 数量不变
        with _engine.connect() as conn:
            chunk_after = conn.execute(
                text("SELECT COUNT(*) FROM knowledge_chunk")
            ).scalar_one()
        assert chunk_after == chunk_before


requires_real_reranker = pytest.mark.skipif(
    not (
        _env_flag("RUN_REAL_RERANKER")
        and _env_flag("RUN_DB_TESTS")
        and settings.database.url.strip()
        and settings.embedding.api_key.strip()
        and settings.reranker.enabled
    ),
    reason=(
        "set RUN_REAL_RERANKER=1 + RUN_DB_TESTS=1 + RERANKER_ENABLED=true "
        "(plus DATABASE_URL / EMBEDDING_API_KEY) to enable real reranker experiment"
    ),
)


@requires_real_reranker
class TestRealRerankerExperiment:
    async def test_baseline_vs_reranked_on_real_kb(self) -> None:
        """12 cases：Vector Top-10 → Reranker → Top-5，对比 Baseline。

        前置：真实知识库已导入（document_id=1 / 11 chunks）。
        本测试**只读**：不 ingest、不 delete、不写任何表。
        """
        # ---- 知识库只读自检（不满足则失败，绝不自行写入）----
        from sqlalchemy import text

        from backend.app.db.session import get_engine

        engine = get_engine()
        assert engine is not None, "DATABASE_URL 未配置"
        with engine.connect() as conn:
            doc_count = conn.execute(
                text("SELECT COUNT(*) FROM knowledge_document")
            ).scalar_one()
            chunk_count = conn.execute(
                text("SELECT COUNT(*) FROM knowledge_chunk")
            ).scalar_one()
        assert doc_count >= 1, "知识库为空：请先导入 docs/knowledge/wms-basic-operations.md"
        assert chunk_count >= 10

        # ---- Reranker 单例（整个实验只加载一次模型）----
        reranker = get_default_reranker_client()

        svc = RerankerEvaluationService(
            vector_search_service=VectorSearchService(),
            reranker_client=reranker,
        )
        cases = load_cases_from_json(str(EVALUATION_CASES))
        assert len(cases) == 12

        summary = await svc.evaluate(
            cases, retrieval_top_k=10, rerank_top_k=5
        )

        # ---- 报告输出（§ 二十）----
        print("\n=== Reranker Offline Experiment (real BGE-M3 + bge-reranker-v2-m3) ===")
        print(f"retrieval_top_k=10, rerank_top_k=5, cases={summary.case_count}")
        print(
            f"{'':14}{'Baseline':>12}{'Reranked':>12}"
        )
        print(
            f"{'Hit@1':<14}"
            f"{summary.baseline_hit_rate_at_1:>11.2%}{summary.reranked_hit_rate_at_1:>12.2%}"
        )
        print(
            f"{'Hit@5':<14}"
            f"{summary.baseline_hit_rate_at_5:>11.2%}{summary.reranked_hit_rate_at_5:>12.2%}"
        )
        print(
            f"{'MRR@5':<14}"
            f"{summary.baseline_mrr_at_5:>11.4f}{summary.reranked_mrr_at_5:>12.4f}"
        )
        print(
            f"improved={summary.improved_cases} "
            f"unchanged={summary.unchanged_cases} "
            f"degraded={summary.degraded_cases}"
        )

        # ---- case_003 / case_011 单独展示（§ 十四）----
        for case_id in ("case_003", "case_011"):
            r = next((x for x in summary.results if x.case_id == case_id), None)
            if r is None:
                continue
            print(f"\n--- {case_id} ---")
            print(f"query          : {r.query}")
            print(
                f"vector rank    : {r.baseline_rank}   "
                f"reranker rank: {r.reranked_rank}   change: {r.change}"
            )
            for d in r.results[:3]:
                print(
                    f"  top rerank: chunk_id={d.chunk_id} "
                    f"rerank_rank={d.rerank_rank} (orig {d.original_rank}) "
                    f"vec_sim={d.vector_similarity:.4f} "
                    f"score={d.reranker_score:.4f}"
                )

        # ---- 自洽断言（记录真实数据，不预设结果）----
        assert summary.case_count == 12
        for r in summary.results:
            assert r.error is None, f"{r.case_id} error: {r.error}"
        assert 0 <= summary.baseline_hit_rate_at_1 <= 1
        assert 0 <= summary.reranked_hit_rate_at_1 <= 1
        # 数据库未被写入（只读校验）
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT COUNT(*) FROM knowledge_chunk")
            ).scalar_one() == chunk_count

    async def test_reranker_client_smoke(self) -> None:
        """真实模型打分冒烟：单例复用，score 数量一致。"""
        reranker = get_default_reranker_client()
        scores = await reranker.rerank(
            "采购入库的流程是什么？",
            ["采购入库用于处理供应商到货。", "盘点用于核对库存数量。"],
        )
        assert len(scores) == 2
        assert all(isinstance(s, float) for s in scores)
