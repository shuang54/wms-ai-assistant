"""Reranker Batch 推理一致性测试（Phase 3.5.13）。

默认 SKIP：仅当 `RUN_REAL_RERANKER_BATCH=1` 才执行（避免在 CI /
单元测试环境加载 2.3GB 模型）。

覆盖任务书 § 十八：

    1. batch size=1     2. batch size=4     3. batch size=8
    4. score 数量一致    5. score 顺序一致   6. ranking 一致
    7. 单 document      8. 大于 batch 的 documents
    9. 模型异常        10. 不重复加载模型

（空 documents 用例由 tests/test_reranker_client.py 已覆盖——此处
复测意义不大且需要 stub。）

模型与 Phase 3.5.12 一致：`BAAI/bge-reranker-v2-m3`（本地缓存）。
仅只读；不写数据库。
"""
from __future__ import annotations

import os

import pytest

from backend.app.config import settings as app_settings
from backend.app.reranker.client import (
    BGERerankerClient,
    get_default_reranker_client,
    reset_default_reranker_client,
)
from backend.app.reranker.exceptions import (
    RerankerConfigurationError,
    RerankerInputError,
    RerankerModelError,
)


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_real = pytest.mark.skipif(
    not (
        _env_flag("RUN_REAL_RERANKER_BATCH")
        and app_settings.reranker.enabled
        and app_settings.reranker.model.strip()
    ),
    reason=(
        "set RUN_REAL_RERANKER_BATCH=1 + RERANKER_ENABLED=true "
        "(plus RERANKER_MODEL) to enable batch correctness test"
    ),
)


# 与任务书 § 十五 一致的真实 WMS queries
_REAL_QUERIES = [
    "采购入库如何操作？",
    "销售出库怎么处理？",
    "单据归档在哪里处理？",
    "采购单如何创建？",
    "仓库库位如何管理？",
]


def _real_documents() -> list[str]:
    """取真实知识库的 11 个 chunk 内容（只读）；任务书 § 十三要求
    优先 1/3/5/10/11，且 11 = 真实知识库上限。"""
    if not _env_flag("RUN_DB_TESTS"):
        # 退化：用一组简短合成文本，但 doc_count 仍可覆盖 1/3/5/10/11
        return [
            f"采购入库用于处理供应商到货，文档编号 {i}。"
            for i in range(1, 12)
        ]
    from sqlalchemy import text

    from backend.app.db import reset_engine_cache
    from backend.app.db.session import get_engine

    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置"
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT content FROM knowledge_chunk "
                "WHERE document_id = 1 ORDER BY chunk_index"
            )
        ).fetchall()
    docs = [r[0] for r in rows]
    assert len(docs) >= 11, f"真实知识库 chunks < 11（got {len(docs)}）"
    return docs[:11]


# ============================================================
# 模型异常 / 配置异常（不依赖真实模型，直接构造）
# ============================================================

class TestClientApiContract:
    """客户端 API 契约：batch_size 行为 + 配置 / 输入校验。"""

    def test_batch_size_property_default(self) -> None:
        client = BGERerankerClient(model_name="stub/r")
        assert client.batch_size >= 1

    def test_set_batch_size_too_small_rejected(self) -> None:
        client = BGERerankerClient(model_name="stub/r")
        with pytest.raises(RerankerConfigurationError):
            client.set_batch_size(0)
        with pytest.raises(RerankerConfigurationError):
            client.set_batch_size(-3)

    def test_set_batch_size_non_int_rejected(self) -> None:
        client = BGERerankerClient(model_name="stub/r")
        with pytest.raises(RerankerConfigurationError):
            client.set_batch_size("abc")  # type: ignore[arg-type]

    def test_set_batch_size_does_not_reload_model(self) -> None:
        """切换 batch_size 不触发模型重加载（仅切 _batch_size 字段）。"""
        client = BGERerankerClient(model_name="stub/r")
        client._model = object()  # type: ignore[attr-defined]
        before = client._model  # type: ignore[attr-defined]
        client.set_batch_size(4)
        client.set_batch_size(1)
        assert client._model is before  # type: ignore[attr-defined]

    def test_invalid_inputs_still_rejected(self) -> None:
        """空 query / 空 documents 仍由 _validate_inputs 在 _ensure_model
        之前拒绝；不依赖真实模型。"""
        import asyncio

        client = BGERerankerClient(model_name="stub/r")
        with pytest.raises(RerankerInputError):
            asyncio.run(client.rerank("", ["d"]))
        with pytest.raises(RerankerInputError):
            asyncio.run(client.rerank("q", []))


# ============================================================
# 真实模型 batch 一致性
# ============================================================

@requires_real
class TestBatchConsistency:
    """同一 query + documents 在不同 batch_size 下：score 数量 / 顺序 /
    ranking 应一致；分数允许非常小的浮点误差（任务书 § 二十）。"""

    @pytest.fixture(scope="module")
    def client(self):
        reset_default_reranker_client()
        return get_default_reranker_client()

    @pytest.fixture(scope="module")
    def documents(self):
        return _real_documents()

    @pytest.mark.parametrize(
        "bs",
        [1, 2, 4, 8],
        ids=["bs=1", "bs=2", "bs=4", "bs=8"],
    )
    async def test_score_count_matches_documents(
        self, client, documents, bs: int
    ) -> None:
        client.set_batch_size(bs)
        scores = await client.rerank(_REAL_QUERIES[0], documents)
        assert len(scores) == len(documents)

    @pytest.mark.parametrize("bs", [1, 4, 8], ids=["bs=1", "bs=4", "bs=8"])
    async def test_single_document(self, client, documents, bs: int) -> None:
        client.set_batch_size(bs)
        scores = await client.rerank(_REAL_QUERIES[0], [documents[0]])
        assert len(scores) == 1
        assert 0.0 <= scores[0] <= 1.0

    async def test_docs_larger_than_batch(self, client, documents) -> None:
        """11 docs / batch=4 → 内部 3 个 mini-batch（4/4/3）。"""
        client.set_batch_size(4)
        scores = await client.rerank(_REAL_QUERIES[0], documents)
        assert len(scores) == len(documents)

    async def test_batch_size_does_not_change_ranking(
        self, client, documents
    ) -> None:
        """同一 (query, documents) 在 batch_size ∈ {1, 4, 8} 下排名一致。"""
        client.set_batch_size(8)
        base = await client.rerank(_REAL_QUERIES[1], documents)
        base_rank = sorted(range(len(base)), key=lambda i: (-base[i], i))

        for bs in (1, 4, 8):
            client.set_batch_size(bs)
            cur = await client.rerank(_REAL_QUERIES[1], documents)
            cur_rank = sorted(range(len(cur)), key=lambda i: (-cur[i], i))
            assert cur_rank == base_rank, (
                f"ranking changed at bs={bs}: base={base_rank} got={cur_rank}"
            )

    async def test_batch_size_score_similarity(self, client, documents) -> None:
        """batch_size ∈ {1, 4} 与基线 {8} 分数误差 ≤ 0.01（sigmoid 输出
        范围内非常小差异）。"""
        client.set_batch_size(8)
        base = await client.rerank(_REAL_QUERIES[2], documents)
        for bs in (1, 4):
            client.set_batch_size(bs)
            cur = await client.rerank(_REAL_QUERIES[2], documents)
            assert len(cur) == len(base)
            for a, b in zip(cur, base):
                assert abs(a - b) <= 0.01, f"bs={bs} score drift {a} vs {b}"

    async def test_model_loaded_only_once(self, client) -> None:
        """无论怎么切换 batch_size + rerank 多少次，模型实例不变。"""
        before = client._model  # type: ignore[attr-defined]
        for bs in (1, 2, 4, 8, 16, 1):
            client.set_batch_size(bs)
            await client.rerank(_REAL_QUERIES[0], [_REAL_QUERIES[1]] * 3)
        assert client._model is before  # type: ignore[attr-defined]


@requires_real
class TestRerankerErrorFromRealClient:
    """模型层异常归一化为 RerankerModelError（真实 rerank 异常路径）。"""

    @pytest.fixture(scope="module")
    def client(self):
        reset_default_reranker_client()
        return get_default_reranker_client()

    async def test_score_length_mismatch_normalized(self, client) -> None:
        """若 _score_pairs 返回长度异常（理论不会发生），仍归一化为 ModelError。"""
        original = client._score_pairs  # type: ignore[attr-defined]

        def _bad(query, documents):  # type: ignore[no-untyped-def]
            return [0.5]  # 故意长度不对

        client._score_pairs = _bad  # type: ignore[method-assign]
        try:
            with pytest.raises(RerankerModelError):
                await client.rerank("q", ["d1", "d2"])
        finally:
            client._score_pairs = original  # type: ignore[method-assign]