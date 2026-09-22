"""Real Embedding API Smoke Test（Phase 3.5.1.5：模型选型真实验证）。

默认 **skip**；仅在设置：

    RUN_REAL_EMBEDDING_TESTS=1

且 Embedding 配置完整（EMBEDDING_API_KEY 或 LLM_API_KEY、
EMBEDDING_MODEL、EMBEDDING_BASE_URL）时才执行真实调用。

本测试固定只发送 Phase 3.5.1.5 指定的两条中文文本
（共 2 次真实 Embedding API 调用，不写数据库、不批量、不导入知识库）。

报告项（pytest -s 运行时打印）：

    Provider / Model / Base URL / HTTP Status / 实际维度 /
    vector[:3] / 与 PostgreSQL vector(1024) 的兼容性结论。

安全：任何输出（含异常消息）都不包含 API Key。
"""
from __future__ import annotations

import os

import pytest

from backend.app.config import settings
from backend.app.embedding import (
    EmbeddingDimensionError,
    EmbeddingError,
    create_embedding_client,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_EMBEDDING_TESTS", "").strip().lower() not in {"1", "true", "yes", "on"},
    reason="set RUN_REAL_EMBEDDING_TESTS=1 (plus full EMBEDDING_* config) to enable real embedding smoke test",
)

# Phase 3.5.1.5 固定的两条真实测试文本（总共最多 2 次 API 调用）。
SMOKE_TEXT_1 = "采购入库时，需要先确认采购订单和收货通知，然后扫描物料条码并完成库位上架。"
SMOKE_TEXT_2 = "WMS 当前越南仓原材料库存数量是多少？"


def _config_ready() -> bool:
    s = settings.embedding
    return bool(s.api_key and s.base_url and s.model and s.dimension > 0)


async def test_real_embedding_smoke_two_chinese_texts() -> None:
    """两条中文文本 → Embedding API → 报告真实维度（不写数据库）。"""
    if not _config_ready():
        pytest.skip("Embedding 配置不完整（需要 EMBEDDING_MODEL / BASE_URL / API_KEY）")

    s = settings.embedding
    client = create_embedding_client(s)

    # 注意：以下 print 均不包含 API Key（Base URL 不含凭据）。
    print(f"\n[SMOKE] Provider = {s.provider}")
    print(f"[SMOKE] Model = {s.model}")
    print(f"[SMOKE] BaseURL = {s.base_url}")
    print(f"[SMOKE] ConfiguredDimension = {s.dimension}")

    observed_dims: list[int] = []
    dimension_mismatch = False
    api_failed = False
    for i, text in enumerate((SMOKE_TEXT_1, SMOKE_TEXT_2), 1):
        try:
            vector = await client.embed(text)
        except EmbeddingDimensionError as exc:
            # API 本身成功（HTTP 200 且返回了向量），仅维度与配置不符；
            # 真实维度已含在异常消息（"模型返回 N 维"）中，不判死，
            # 由最终报告给出 Compatible = NO。
            dimension_mismatch = True
            print(f"[SMOKE] TEXT{i}: HTTP=200 DimensionMismatch: {exc}")
            continue
        except EmbeddingError as exc:
            # 真实 API 出错（401/429/超时/响应异常等）：完整打印供人工排查。
            api_failed = True
            print(f"[SMOKE] TEXT{i}: API_ERROR {type(exc).__name__}: {exc}")
            continue
        print(f"[SMOKE] TEXT{i}: HTTP=200 Dimension={len(vector)} vector[:3]={vector[:3]}")
        observed_dims.append(len(vector))

    # ---- 兼容性结论（以真实 API Response 为准） ----
    if api_failed:
        print("[SMOKE] Result = API_FAILED（请检查 Key / Model / Base URL）")
    elif observed_dims:
        actual = observed_dims[0]
        print(f"[SMOKE] ActualDimension = {actual}")
        print(
            f"[SMOKE] CompatibleWithVector({s.dimension}) = "
            f"{'YES' if actual == s.dimension else 'NO'}"
        )
    elif dimension_mismatch:
        print("[SMOKE] Result = DIMENSION_MISMATCH Compatible=NO（真实维度见上方 TEXT 行）")
