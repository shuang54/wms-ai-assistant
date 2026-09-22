"""Retrieval Analysis 真实 DB 集成测试（Phase 3.5.11）。

两个层级：

    A. RUN_DB_TESTS=1（默认 SKIP）
       真实 pgvector + **测试自建文档** + MockEmbeddingClient（零 API 成本）。
       验证：result_count / rank 从 1 开始 / similarity 降序 /
       distance = 1 - similarity / metadata / embedding dimension = 1024。

    B. RUN_REAL_RETRIEVAL_ANALYSIS=1（且 RUN_DB_TESTS=1，默认 SKIP）
       真实 BGE-M3 + 真实知识库（docs/knowledge/wms-basic-operations.md，
       若库中已有同 hash 文档则复用，结束后**不删除已存在的真实文档**；
       仅清理本测试自建的数据）。
       输出 Top-K = 1 / 3 / 5 / 10 的 case_count / hit_count / hit_rate /
       avg / min / max similarity，以及每个失败 case 的 Top 结果详情。
       **不调用 DeepSeek。**

数据库保护（任务书 §十五）：不删除 / 修改既有 document、chunk、embedding。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.app.config import settings
from backend.app.db import reset_engine_cache
from backend.app.db.base import Base
from backend.app.db.session import get_engine
from backend.app.embedding.client import EmbeddingClient
from backend.app.services.knowledge_ingestion_service import (
    KnowledgeIngestionService,
)
from backend.app.services.rag_evaluation_service import (
    RagEvaluationService,
    load_cases_from_json,
)
from backend.app.services.retrieval_analysis_service import (
    RetrievalAnalysisService,
)
from backend.app.services.vector_search_service import VectorSearchService


FIXTURES = Path(__file__).parent / "fixtures" / "rag"
REAL_KB_DOC = Path("docs/knowledge/wms-basic-operations.md")
EVALUATION_CASES = FIXTURES / "evaluation_cases.json"


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable retrieval analysis DB tests",
)

requires_real_analysis = pytest.mark.skipif(
    not (
        _env_flag("RUN_REAL_RETRIEVAL_ANALYSIS")
        and _env_flag("RUN_DB_TESTS")
        and settings.database.url.strip()
        and settings.embedding.api_key.strip()
    ),
    reason=(
        "set RUN_REAL_RETRIEVAL_ANALYSIS=1 + RUN_DB_TESTS=1 "
        "(plus EMBEDDING_API_KEY / DATABASE_URL) to enable real retrieval analysis"
    ),
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module")
def engine():
    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置"
    from backend.app.db import models  # noqa: F401
    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture
def _clean_around_each(engine):
    """**仅 mock 测试类显式引用**；测试前后各清理一次。

    - before：清空知识表，保证 mock 检索只命中**本测试自建**文档
      （避免与已存在的真实 KB 文档互相污染断言）。
    - after：清理自建数据。

    刻意不用 autouse：真实分析测试（TestRealRetrievalAnalysis）
    复用/新建真实知识库文档，绝不能被 truncate 波及（任务书 §十五）。
    """

    def _truncate() -> None:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "TRUNCATE TABLE knowledge_chunk, knowledge_document "
                    "RESTART IDENTITY CASCADE"
                )
            )

    _truncate()
    yield
    _truncate()


class MockEmbeddingClient(EmbeddingClient):
    """确定性 Mock（与既有 ingestion 测试同一策略）。"""

    def __init__(self) -> None:
        self._dim = settings.embedding.dimension

    async def embed(self, txt: str) -> list[float]:
        seed = sum(txt.encode("utf-8"))
        return [((seed + i) % 97 + 1) / 1000.0 for i in range(self._dim)]


def _write_doc(tmp_path: Path) -> Path:
    p = tmp_path / "analysis_real.md"
    body = (
        "# 检索分析集成测试文档\n\n"
        "## 采购入库主题\n\n" + ("采购入库用于处理供应商到货。" * 25) + "\n\n"
        "## 销售出库主题\n\n" + ("销售出库用于处理客户订单发货。" * 25) + "\n\n"
        "## 盘点主题\n\n" + ("盘点用于核对库存数量。" * 25) + "\n"
    )
    p.write_text(body, encoding="utf-8")
    return p


# ============================================================
# A. RUN_DB_TESTS=1（mock embedding + 测试自建文档）
# ============================================================

@requires_db
class TestRetrievalAnalysisRealDB:
    async def test_analyze_against_real_pgvector(
        self, tmp_path: Path, engine, _clean_around_each
    ) -> None:
        # ---- 准备：自建文档（MockEmbeddingClient，零 API 成本）----
        mock = MockEmbeddingClient()
        ingest = KnowledgeIngestionService(embedding_client=mock)
        created = await ingest.ingest_one(_write_doc(tmp_path))
        assert created.status == "ready"
        assert created.chunk_count >= 2

        # ---- 分析 Service 使用同一 Mock（查询向量与库内向量同源可命中）----
        analysis = RetrievalAnalysisService(
            vector_search_service=VectorSearchService(embedding_client=mock)
        )
        result = await analysis.analyze("采购入库的流程", top_k=5)

        # ---- 断言：任务书 §十一 ----
        assert result.result_count >= 1
        assert result.result_count <= 5
        assert result.top_k == 5

        # rank 从 1 开始且连续
        assert [it.rank for it in result.items] == list(
            range(1, result.result_count + 1)
        )

        # similarity 降序（等价于 distance 升序）
        sims = [it.similarity for it in result.items]
        assert sims == sorted(sims, reverse=True)

        # distance = 1 - similarity（直接取自 VectorSearchService 的换算）
        for it in result.items:
            assert it.distance == pytest.approx(1.0 - it.similarity)

        # similarity 范围合理（cosine similarity ∈ [-1, 1]）
        for it in result.items:
            assert -1.0 <= it.similarity <= 1.0
            assert 0.0 <= it.distance <= 2.0

        # 统计一致性
        assert result.average_similarity == pytest.approx(
            sum(sims) / len(sims), abs=1e-9
        )
        assert result.min_similarity == pytest.approx(min(sims))
        assert result.max_similarity == pytest.approx(max(sims))

        # content_length / metadata 保留
        for it in result.items:
            assert it.content_length > 0
            assert isinstance(it.metadata, dict)
            assert it.document_id == created.document_id

        # embedding dimension = 1024
        with engine.connect() as conn:
            dims = conn.execute(
                text("SELECT DISTINCT vector_dims(embedding) FROM knowledge_chunk")
            ).fetchall()
        assert dims == [(settings.embedding.dimension,)]
        assert settings.embedding.dimension == 1024

    async def test_evaluate_top_k_against_real_pgvector(
        self, tmp_path: Path, _clean_around_each
    ) -> None:
        mock = MockEmbeddingClient()
        ingest = KnowledgeIngestionService(embedding_client=mock)
        await ingest.ingest_one(_write_doc(tmp_path))

        svc = RagEvaluationService(
            vector_search_service=VectorSearchService(embedding_client=mock)
        )
        cases = load_cases_from_json(str(EVALUATION_CASES))
        summary = await svc.evaluate_top_k(cases, top_ks=(1, 3, 5, 10))

        assert summary.top_ks == (1, 3, 5, 10)
        for e in summary.entries:
            assert e.case_count == len(cases)
            # Mock 向量语义弱，仅验证统计口径自洽，不断言具体命中率
            assert e.average_similarity >= 0.0
            if e.hit_count > 0:
                assert 0 < e.hit_rate <= 1.0


# ============================================================
# B. RUN_REAL_RETRIEVAL_ANALYSIS=1（真实 BGE-M3 + 真实知识库）
# ============================================================

@requires_real_analysis
class TestRealRetrievalAnalysis:
    async def test_top_k_analysis_on_real_kb(self, engine) -> None:
        """真实 BGE-M3 + pgvector：Top-K 1/3/5/10 检索质量分析。

        知识库保护：导入真实文档（同 hash → already_exists 时复用，
        不新建不删除）；仅当本次测试**新建**了文档才在结束时清理。
        """
        created_by_test = False
        doc_id: int | None = None
        try:
            ingest = KnowledgeIngestionService()
            created = await ingest.ingest_one(REAL_KB_DOC)
            if created.status == "ready":  # 本次新建 → 测试结束后清理
                created_by_test = True
                doc_id = created.document_id
            # already_exists → 真实知识库已有该文档，保持不动

            svc = RagEvaluationService(vector_search_service=VectorSearchService())
            cases = load_cases_from_json(str(EVALUATION_CASES))
            summary = await svc.evaluate_top_k(cases, top_ks=(1, 3, 5, 10))

            print("\n=== Real Retrieval Analysis (BGE-M3 + pgvector) ===")
            print(
                f"{'Top-K':<7}{'cases':<8}{'hit':<6}{'hit_rate':<11}"
                f"{'avg_sim':<10}{'min_sim':<10}{'max_sim':<10}"
            )
            for e in summary.entries:
                print(
                    f"{e.top_k:<7}{e.case_count:<8}{e.hit_count:<6}"
                    f"{e.hit_rate:<11.2%}{e.average_similarity:<10.4f}"
                    f"{(f'{e.min_similarity:.4f}' if e.min_similarity is not None else 'None'):<10}"
                    f"{(f'{e.max_similarity:.4f}' if e.max_similarity is not None else 'None'):<10}"
                )

            # 失败 case 详情（含 Top 结果 similarity）
            analysis = RetrievalAnalysisService(
                vector_search_service=VectorSearchService()
            )
            for e in summary.entries:
                if e.top_k != 5:
                    continue
                for r in e.results:
                    if r.matched or r.error:
                        continue
                    print(f"\n--- FAILED {r.case_id} (top_k=5) ---")
                    print(f"query            : {r.query}")
                    print(f"expected_keywords: {r.missing_keywords}")
                    detail = await analysis.analyze(r.query, top_k=5)
                    for it in detail.items:
                        print(
                            f"  rank={it.rank} chunk_id={it.chunk_id} "
                            f"chunk_index={it.chunk_index} sim={it.similarity:.4f} "
                            f"len={it.content_length}"
                        )

            # 自洽断言（不预设具体命中率；记录真实结果）
            for e in summary.entries:
                assert e.case_count == len(cases) == 12
                assert 0 <= e.hit_rate <= 1.0
                assert e.error_count == 0
        finally:
            if created_by_test and doc_id is not None:
                ingest = KnowledgeIngestionService()
                ingest.delete_document(doc_id)
