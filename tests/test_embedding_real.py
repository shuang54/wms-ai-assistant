"""Real Embedding API Smoke Test（Phase 3.5.1）。

默认 **skip**；仅在设置：

    RUN_REAL_EMBEDDING_TESTS=1

且 Embedding 配置完整（EMBEDDING_API_KEY 或 LLM_API_KEY、
EMBEDDING_MODEL、EMBEDDING_BASE_URL）时才执行真实调用。

只验证：一句中文 → 向量 → 维度 == EMBEDDING_DIMENSION。
禁止：写数据库 / 批量 / 长文本 / 大量请求。
"""
from __future__ import annotations

import os

import pytest

from backend.app.config import settings
from backend.app.embedding import (
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
    EmbeddingError,
    create_embedding_client,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_EMBEDDING_TESTS", "").strip().lower() not in {"1", "true", "yes", "on"},
    reason="set RUN_REAL_EMBEDDING_TESTS=1 (plus full EMBEDDING_* config) to enable real embedding smoke test",
)


def _config_ready() -> bool:
    s = settings.embedding
    return bool(s.api_key and s.base_url and s.model and s.dimension > 0)


async def test_real_embed_single_chinese_text() -> None:
    """中文 → Embedding API → 向量，校验维度（不写数据库）。"""
    if not _config_ready():
        pytest.skip("Embedding 配置不完整（需要 EMBEDDING_MODEL / BASE_URL / API_KEY）")

    client = create_embedding_client(settings.embedding)
    text = "采购入库需要先确认采购订单。"
    try:
        vector = await client.embed(text)
    except EmbeddingError as exc:
        pytest.fail(f"真实 Embedding 调用失败（真实 API 出错，非测试问题）: {exc}")

    assert isinstance(vector, list)
    assert len(vector) == settings.embedding.dimension, (
        f"维度不匹配：模型返回 {len(vector)} 维，"
        f"EMBEDDING_DIMENSION={settings.embedding.dimension}"
    )
    assert all(isinstance(x, float) for x in vector)
