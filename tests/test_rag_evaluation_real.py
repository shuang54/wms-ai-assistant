"""RAG Retrieval Evaluation Real Test（Phase 3.5.7，可选，默认 SKIP）。

仅当环境变量 RUN_REAL_RAG_EVAL=1 时执行：

    evaluation dataset
      ↓
    真实 BGE-M3 Embedding
      ↓
    真实 PostgreSQL + pgvector
      ↓
    真实 VectorSearchService
      ↓
    RetrievalEvaluationSummary

绝对不调用 LLM（不会产生 DeepSeek 费用）。

设计：
    - 默认 skip（不依赖 RUN_REAL_LLM_TEST / RUN_DB_TESTS，独立可区分）
    - 严格依赖：BGE-M3 / DB 配置必须可用
    - 失败 case 不会阻断报告；汇总以 RetrievalEvaluationSummary 形式返回
    - 测试结果打印 hit_rate 以便人工 baseline 对比

使用：
    $env:RUN_REAL_RAG_EVAL="1"
    python -m pytest tests/test_rag_evaluation_real.py -q -s
"""
from __future__ import annotations

import os

import pytest

from backend.app.config import settings
from backend.app.embedding.client import create_embedding_client
from backend.app.services.rag_evaluation_service import (
    RagEvaluationService,
    RetrievalEvaluationCase,
    load_cases_from_json,
)
from backend.app.services.vector_search_service import VectorSearchService


FIXTURE_PATH = "tests/fixtures/rag/evaluation_cases.json"


def _eval_enabled() -> bool:
    return os.getenv("RUN_REAL_RAG_EVAL") == "1"


pytestmark = pytest.mark.skipif(
    not _eval_enabled(),
    reason=(
        "Real RAG evaluation is disabled. Set RUN_REAL_RAG_EVAL=1 to enable. "
        "Real test requires: EMBEDDING_API_KEY / DATABASE_URL / "
        "and a populated knowledge base."
    ),
)


# ============================================================
# Skip with explicit env diagnostic (runs BEFORE skipif to print reasons)
# ============================================================

def _require_real_env() -> None:
    """在 skipif 之外再做依赖校验，给出明确失败原因。"""
    missing: list[str] = []
    if not settings.database.url:
        missing.append("DATABASE_URL 未配置")
    if not settings.embedding.api_key:
        missing.append("EMBEDDING_API_KEY 未配置")
    if not os.path.exists(FIXTURE_PATH):
        missing.append(f"评测数据集不存在: {FIXTURE_PATH}")
    if missing:
        pytest.skip("Real eval 缺少前置条件: " + "; ".join(missing))


@pytest.fixture(scope="module")
def vector_search_service() -> VectorSearchService:
    """构造真实 VectorSearchService（默认单例模式）。"""
    _require_real_env()
    return VectorSearchService(embedding_client=create_embedding_client())


@pytest.fixture(scope="module")
def evaluation_service(vector_search_service: VectorSearchService) -> RagEvaluationService:
    return RagEvaluationService(vector_search_service=vector_search_service)


@pytest.fixture(scope="module")
def cases() -> list[RetrievalEvaluationCase]:
    """从 fixture 加载 evaluation cases。"""
    _require_real_env()
    return load_cases_from_json(FIXTURE_PATH)


# ============================================================
# Tests
# ============================================================

def test_evaluation_dataset_has_minimum_cases(cases: list[RetrievalEvaluationCase]) -> None:
    """评估数据集 ≥ 10 case（任务规范 § 十五）。"""
    assert len(cases) >= 10
    # 每个 case 应有非空 query
    for c in cases:
        assert c.case_id, "case_id required"
        assert c.query.strip(), "query required"


def test_real_retrieval_evaluation_smoke(
    evaluation_service: RagEvaluationService,
    cases: list[RetrievalEvaluationCase],
    capsys,
) -> None:
    """对全部 cases 执行真实检索评估，打印 Baseline。"""
    import asyncio

    summary = asyncio.run(evaluation_service.evaluate(cases, top_k=5))

    with capsys.disabled():
        print("\n=== RAG Retrieval Evaluation Baseline ===")
        print(f"total_cases   : {summary.total_cases}")
        print(f"matched_cases : {summary.matched_cases}")
        print(f"failed_cases  : {summary.failed_cases}")
        print(f"hit_rate      : {summary.hit_rate:.2%}")
        print(f"top_k         : {summary.top_k}")
        print("per-case results:")
        for r in summary.results:
            print(
                f"  - {r.case_id}: matched={r.matched} "
                f"results={r.results_count} "
                f"missing={list(r.missing_keywords)} "
                f"err={r.error}"
            )

    # 类型契约
    assert summary.total_cases == len(cases)
    assert summary.top_k == 5
    assert 0.0 <= summary.hit_rate <= 1.0
    # matched_cases ≤ total_cases
    assert summary.matched_cases <= summary.total_cases
    # failed_cases = error 的 case 数
    assert summary.failed_cases == sum(1 for r in summary.results if r.error)


def test_no_llm_call_during_real_evaluation(
    evaluation_service: RagEvaluationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行评估时 LLMClient.chat / .generate 必须从不被调用（不产生 DeepSeek 费用）。"""
    import asyncio

    from backend.app.llm.client import LLMClient

    spy = pytest.MonkeyPatch()
    try:
        # 用一份最小 case 触发 search 路径
        sample = [
            RetrievalEvaluationCase(
                case_id="real_probe",
                query="probe",
                expected_keywords=("probe",),
            ),
        ]
        # monkeypatch LLMClient.chat 和 generate 为 sentinel（绝不应被调用）
        def _fail(*_args, **_kwargs):  # pragma: no cover
            raise AssertionError("LLMClient.chat must NOT be called during evaluation")

        def _fail_gen(*_args, **_kwargs):  # pragma: no cover
            raise AssertionError(
                "LLMClient.generate must NOT be called during evaluation"
            )

        monkeypatch.setattr(LLMClient, "chat", _fail, raising=False)
        monkeypatch.setattr(LLMClient, "generate", _fail, raising=False)

        asyncio.run(evaluation_service.evaluate(sample, top_k=3))
        # 顺利结束 → LLM 未被调用（任何触发都会抛 AssertionError）
    finally:
        spy.undo()


__all__ = ["test_evaluation_dataset_has_minimum_cases", "test_real_retrieval_evaluation_smoke", "test_no_llm_call_during_real_evaluation"]