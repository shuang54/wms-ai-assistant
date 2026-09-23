"""Reranker 离线性能基准（Phase 3.5.13，离线实验）。

**只测量，不做业务。**

回答任务书核心问题：

    > Reranker 在当前环境下到底有多慢，以及 Batch / Candidate 数量
    > 变化是否能够降低平均推理成本。

约束：
    - **不调用** VectorSearchService / DeepSeek / 业务 Service / DB
    - **不修改** rerank 外部 API 语义；仅切换 batch_size 并计时
    - 不打印 query 全文 / doc 全文 / 模型本地路径（§ 二十三）
    - 不引入第三方性能库（标准库 statistics 即可）
    - 仅用于离线 benchmark；不进入生产 RAG

输出：
    RerankerBenchmarkResult（frozen，只含统计指标）
"""
from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass

from backend.app.reranker.client import BGERerankerClient

logger = logging.getLogger(__name__)

__all__ = [
    "RerankerBenchmarkResult",
    "RerankerBenchmarkService",
]


# ============================================================
# DTO
# ============================================================

@dataclass(frozen=True)
class RerankerBenchmarkResult:
    """单次 benchmark 配置的统计结果（纯统计，无文本泄漏）。

    字段：
        document_count:       本次候选文档数（≠ batch_size）
        batch_size:          forward batch size
        warmup_runs:         预热次数（不计入 total_seconds）
        measured_runs:       正式测量次数
        model_load_seconds:  首次模型加载耗时（精确到秒），归一化为
                             单次；warmup / measured 复用同一加载好的
                             模型故不影响平均推理时间
        total_seconds:       measured_runs 总耗时（秒）
        average_seconds:     平均单次推理耗时（秒）
        average_ms_per_document:  average_seconds / document_count * 1000
        p50_ms:              median 单次耗时（毫秒）
        p95_ms:              95 percentile 单次耗时（毫秒）
    """

    document_count: int
    batch_size: int
    warmup_runs: int
    measured_runs: int
    model_load_seconds: float
    total_seconds: float
    average_seconds: float
    average_ms_per_document: float
    p50_ms: float
    p95_ms: float


# ============================================================
# 工具
# ============================================================

def _percentile(values: list[float], p: float) -> float:
    """线性插值百分位（p ∈ [0, 100]）；空列表返回 0.0。"""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    k = (n - 1) * (p / 100.0)
    f_lo = int(k)
    f_hi = min(f_lo + 1, n - 1)
    return s[f_lo] + (s[f_hi] - s[f_lo]) * (k - f_lo)


# ============================================================
# Service
# ============================================================

class RerankerBenchmarkService:
    """Reranker 性能基准服务（离线测量，不进入生产）。

    用法：
        svc = RerankerBenchmarkService(get_default_reranker_client())
        result = await svc.ameasure(
            query="采购入库如何操作？",
            documents=[chunk1, chunk2, ...],  # length = document_count
            batch_size=8,
            warmup_runs=1,
            measured_runs=3,
        )

    行为：
        - 切换 client.batch_size（save / restore in try / finally）
        - warmup_runs 次预热（不计入）
        - measured_runs 次正式测量（独立计时）
        - model_load_seconds：首次 _ensure_model 耗时（独立计时，
          不混入 measured_runs 平均；warmup 之后即为已加载状态）
    """

    def __init__(self, reranker_client: BGERerankerClient) -> None:
        self._client = reranker_client

    async def ameasure(
        self,
        *,
        query: str,
        documents: list[str],
        batch_size: int,
        warmup_runs: int = 1,
        measured_runs: int = 3,
    ) -> RerankerBenchmarkResult:
        """异步入口；测量给定 (query, documents) 在 batch_size 下的耗时。

        Args:
            query:         真实 query 文本（**日志**中不打印，仅用于打分）
            documents:     候选文档列表；数量决定 document_count
            batch_size:    forward batch size（仅本次生效）
            warmup_runs:   预热次数（默认 1）
            measured_runs: 正式测量次数（默认 3）

        Raises:
            ValueError:   batch_size < 1 / measured_runs < 1 / documents 空
            异常透传:    client.rerank 的异常（如 RerankerModelError）
        """
        if not documents:
            raise ValueError("documents 不能为空")
        if batch_size < 1:
            raise ValueError(f"batch_size 必须 >= 1（got {batch_size}）")
        if measured_runs < 1:
            raise ValueError(f"measured_runs 必须 >= 1（got {measured_runs}）")
        if warmup_runs < 0:
            raise ValueError(f"warmup_runs 必须 >= 0（got {warmup_runs}）")

        # ---- 保存原 batch_size 并切换 ----
        prev_bs = self._client.batch_size
        self._client.set_batch_size(batch_size)
        try:
            # ---- 1. 模型加载（若尚未加载）耗时独立测量 ----
            model_load_seconds = 0.0
            if self._client._model is None:  # type: ignore[attr-defined]
                t0 = time.perf_counter()
                # 触发懒加载（不会跑 forward）
                self._client._ensure_model()  # type: ignore[attr-defined]
                model_load_seconds = time.perf_counter() - t0

            # ---- 2. warmup（不计入正式）----
            for _ in range(warmup_runs):
                await self._client.rerank(query, documents)

            # ---- 3. measured_runs 独立计时 ----
            runs_ms: list[float] = []
            for _ in range(measured_runs):
                t0 = time.perf_counter()
                await self._client.rerank(query, documents)
                runs_ms.append((time.perf_counter() - t0) * 1000.0)
        finally:
            # ---- 4. 恢复 batch_size（不影响后续 benchmark / 真实实验）----
            try:
                self._client.set_batch_size(prev_bs)
            except Exception:  # noqa: BLE001 — 恢复失败不影响测量结果
                logger.warning("failed to restore reranker batch_size: %s", prev_bs)

        total_seconds = sum(runs_ms) / 1000.0
        average_seconds = total_seconds / len(runs_ms)
        avg_ms_per_doc = (
            (average_seconds * 1000.0) / len(documents) if documents else 0.0
        )
        # statistics.median 在 even [2] 下返回 mean，与 _percentile 一致；
        # 此处直接用 median + 自定义 percentile
        p50_ms = float(statistics.median(runs_ms))
        p95_ms = float(_percentile(runs_ms, 95.0))

        return RerankerBenchmarkResult(
            document_count=len(documents),
            batch_size=batch_size,
            warmup_runs=warmup_runs,
            measured_runs=measured_runs,
            model_load_seconds=model_load_seconds,
            total_seconds=total_seconds,
            average_seconds=average_seconds,
            average_ms_per_document=avg_ms_per_doc,
            p50_ms=p50_ms,
            p95_ms=p95_ms,
        )