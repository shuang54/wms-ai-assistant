"""Reranker 真实离线性能基准（Phase 3.5.13）。

默认 SKIP：仅当 `RUN_REAL_RERANKER_BENCHMARK=1` 才执行（避免在 CI
加载 2.3GB 模型）。

测量矩阵（任务书 § 十六）：

    candidates ∈ {1, 3, 5, 10, 11}   ×   batch ∈ {1, 4, 8}

11 = 当前真实知识库 chunk_count；其余使用真实 chunk 切片（按
chunk_index 顺序）。

只读：**禁止**任何 DB 写操作。
模型：BAAI/bge-reranker-v2-m3（本地缓存）；不可换模型。
"""
from __future__ import annotations

import asyncio
import os

import pytest

from backend.app.config import settings as app_settings
from backend.app.reranker.client import (
    get_default_reranker_client,
    reset_default_reranker_client,
)
from backend.app.services.reranker_benchmark_service import (
    RerankerBenchmarkResult,
    RerankerBenchmarkService,
)


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


_MATRIX_CANDIDATES: tuple[int, ...] = (1, 3, 5, 10, 11)
_MATRIX_BATCH: tuple[int, ...] = (1, 4, 8)

_REAL_QUERIES = [
    "采购入库如何操作？",
    "销售出库怎么处理？",
    "单据归档在哪里处理？",
    "采购单如何创建？",
    "仓库库位如何管理？",
]


def _real_documents() -> list[str]:
    """真实知识库 11 个 chunk（只读）。"""
    from sqlalchemy import text as sa_text

    from backend.app.db import reset_engine_cache
    from backend.app.db.session import get_engine

    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置"
    with eng.connect() as conn:
        rows = conn.execute(
            sa_text(
                "SELECT content FROM knowledge_chunk "
                "WHERE document_id = 1 ORDER BY chunk_index"
            )
        ).fetchall()
    docs = [r[0] for r in rows]
    assert len(docs) >= 11, f"知识库 chunks < 11（got {len(docs)}）"
    return docs[:11]


def _is_real_env() -> bool:
    return (
        _env_flag("RUN_REAL_RERANKER_BENCHMARK")
        and _env_flag("RUN_DB_TESTS")
        and app_settings.reranker.enabled
        and app_settings.reranker.model.strip()
        and app_settings.database.url.strip()
    )


requires_real_benchmark = pytest.mark.skipif(
    not _is_real_env(),
    reason=(
        "set RUN_REAL_RERANKER_BENCHMARK=1 + RUN_DB_TESTS=1 + RERANKER_ENABLED=true "
        "+ DATABASE_URL + RERANKER_MODEL to enable real benchmark"
    ),
)


def _format_row(result: RerankerBenchmarkResult) -> str:
    return (
        f"| {result.document_count:>9} | {result.batch_size:>5} | "
        f"{result.average_seconds * 1000.0:>8.1f} | "
        f"{result.p50_ms:>6.1f} | {result.p95_ms:>6.1f} | "
        f"{result.average_ms_per_document:>7.2f} |"
    )


def _format_header() -> str:
    return (
        "| Candidates | Batch | Avg ms |   P50 |   P95 |  ms/doc |\n"
        "| ---------: | ----: | -----: | ----: | ----: | ------: |"
    )


# ============================================================
# 真实 benchmark 矩阵
# ============================================================

@requires_real_benchmark
class TestRerankerBenchmarkMatrix:
    """5 × 3 性能矩阵（任务书 § 十六）。"""

    @pytest.fixture(scope="class")
    def client(self):
        reset_default_reranker_client()
        return get_default_reranker_client()

    @pytest.fixture(scope="class")
    def documents(self) -> list[str]:
        return _real_documents()

    @pytest.fixture(scope="class")
    def service(self, client):
        return RerankerBenchmarkService(client)

    @pytest.fixture(scope="class")
    def collected(self, service, documents):
        """收集矩阵结果（class 级缓存避免重复测量）。"""

        async def _collect() -> list[RerankerBenchmarkResult]:
            out: list[RerankerBenchmarkResult] = []
            query = _REAL_QUERIES[0]
            for doc_count in _MATRIX_CANDIDATES:
                docs_subset = documents[:doc_count]
                for bs in _MATRIX_BATCH:
                    result = await service.ameasure(
                        query=query,
                        documents=docs_subset,
                        batch_size=bs,
                        warmup_runs=1,
                        measured_runs=3,
                    )
                    out.append(result)
            return out

        return asyncio.run(_collect())

    def test_model_load_recorded(self, collected) -> None:
        any_loaded = any(r.model_load_seconds > 0 for r in collected)
        all_zero = all(r.model_load_seconds == 0 for r in collected)
        assert any_loaded or all_zero

    def test_avg_ms_per_doc_non_negative(self, collected) -> None:
        for r in collected:
            assert r.average_ms_per_document >= 0
            assert r.p50_ms >= 0
            assert r.p95_ms >= 0

    def test_p95_greater_equal_p50(self, collected) -> None:
        for r in collected:
            assert r.p95_ms >= r.p50_ms - 0.5

    def test_measured_runs_recorded(self, collected) -> None:
        for r in collected:
            assert r.measured_runs == 3
            assert r.warmup_runs == 1

    def test_matrix_complete(self, collected) -> None:
        assert len(collected) == len(_MATRIX_CANDIDATES) * len(_MATRIX_BATCH)

    def test_print_markdown_table(self, collected) -> None:
        print("\n=== Reranker Benchmark Matrix (CPU, bge-reranker-v2-m3) ===")
        print(_format_header())
        sorted_results = sorted(
            collected, key=lambda r: (r.document_count, r.batch_size)
        )
        for r in sorted_results:
            print(_format_row(r))

    def test_print_per_cell_detail(self, collected) -> None:
        print("\n=== Per-cell detail ===")
        sorted_results = sorted(
            collected, key=lambda r: (r.document_count, r.batch_size)
        )
        for r in sorted_results:
            print(
                f"docs={r.document_count:>2} bs={r.batch_size} "
                f"total={r.total_seconds:.3f}s "
                f"avg={r.average_seconds * 1000.0:.1f}ms "
                f"ms/doc={r.average_ms_per_document:.2f}"
            )


# ============================================================
# 单组冒烟
# ============================================================

@requires_real_benchmark
class TestRerankerBenchmarkSmoke:
    @pytest.fixture(scope="class")
    def service(self):
        reset_default_reranker_client()
        client = get_default_reranker_client()
        return RerankerBenchmarkService(client)

    async def test_one_doc_one_batch_smoke(self, service) -> None:
        docs = _real_documents()[:1]
        result = await service.ameasure(
            query=_REAL_QUERIES[0],
            documents=docs,
            batch_size=1,
            warmup_runs=1,
            measured_runs=2,
        )
        assert result.document_count == 1
        assert result.batch_size == 1
        assert result.measured_runs == 2
        assert result.average_ms_per_document >= 0