"""Reranker Client（Phase 3.5.12，离线实验）。

最小抽象：

        query + candidate documents
                ↓
        RerankerClient.rerank()
                ↓
        list[float]（与 documents 一一对应的 relevance score）

实现：BGERerankerClient
    - 模型：BAAI/bge-reranker-v2-m3（Cross-Encoder）
    - 依赖：torch + transformers（**懒导入**——未安装时模块仍可 import，
      仅在真正 rerank 时抛 RerankerModelError，便于单元测试与降级）
    - 设备：RERANKER_DEVICE（空 = 自动：CUDA 可用 → cuda，否则 cpu）
    - 模型**进程内只加载一次**（首次 rerank 时懒加载；线程锁防止并发加载
      多个实例——任务书 § 十七）
    - logits → sigmoid 归一化为 [0, 1] relevance score

纪律（任务书 § 六）：
    - 不访问数据库 / 不知道 WMS 业务 / 不调 DeepSeek /
      不调 VectorSearchService / 不保存结果 / 不写数据库
    - 不把 API Key / token 写入代码
    - 日志只记统计（query_length / doc_count / device / elapsed_ms），
      不记 query 全文 / 文档全文 / 模型本地缓存路径
"""
from __future__ import annotations

import logging
import threading
import time

from backend.app.config import settings
from backend.app.reranker.exceptions import (
    RerankerConfigurationError,
    RerankerInputError,
    RerankerModelError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RerankerClient",
    "BGERerankerClient",
    "create_reranker_client",
    "get_default_reranker_client",
]


# ============================================================
# 抽象
# ============================================================

class RerankerClient:
    """Reranker 客户端抽象（仅 query → per-doc relevance score）。"""

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """对 (query, documents) 打分。

        Args:
            query:     查询文本（非空 str）
            documents: 候选文本列表（非空；元素为非空 str）

        Returns:
            与 documents 一一对应的 relevance score 列表。

        Raises:
            RerankerInputError: query / documents 输入非法
            RerankerModelError: 模型加载 / 推理失败（依赖缺失等）
        """
        raise NotImplementedError


# ============================================================
# BGE Cross-Encoder 实现
# ============================================================

class BGERerankerClient(RerankerClient):
    """BAAI/bge-reranker-v2-m3 Cross-Encoder Reranker。

    模型加载策略（§ 十七 性能要求）：
        - 首次 rerank 时懒加载（_ensure_model）
        - threading.Lock 保证并发下只加载一次
        - 进程生命周期内复用同一实例（get_default_reranker_client 单例）
    """

    _load_lock = threading.Lock()

    def __init__(
        self,
        *,
        model_name: str | None = None,
        device: str | None = None,
        max_length: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        self._model_name = model_name or settings.reranker.model
        self._device_override = device if device is not None else settings.reranker.device
        self._max_length = (
            max_length if max_length is not None else settings.reranker.max_length
        )
        self._batch_size = (
            batch_size if batch_size is not None else settings.reranker.batch_size
        )
        # 已加载的模型与 tokenizer（懒加载；torch / transformers 缺失时保持 None）
        self._model = None
        self._tokenizer = None
        self._resolved_device: str | None = None

    # ---------- batch_size 控制（Phase 3.5.13 benchmark 用）----------

    @property
    def batch_size(self) -> int:
        """当前生效的 forward batch size（仅对后续 rerank 生效，不重加载模型）。"""
        return self._batch_size

    def set_batch_size(self, batch_size: int) -> None:
        """设置后续 rerank 的 forward batch size（无需 reload）。

        Raises:
            RerankerConfigurationError: batch_size < 1
        """
        try:
            bs = int(batch_size)
        except (TypeError, ValueError) as exc:
            raise RerankerConfigurationError(
                f"batch_size 必须为整数（got {batch_size!r}）"
            ) from exc
        if bs < 1:
            raise RerankerConfigurationError(
                f"batch_size 必须 >= 1（got {bs}）"
            )
        self._batch_size = bs

    # ---------- 校验 ----------

    @staticmethod
    def _validate_inputs(query: str, documents: list[str]) -> None:
        if not isinstance(query, str) or not query.strip():
            raise RerankerInputError("query 必须是非空 str")
        if not isinstance(documents, list) or not documents:
            raise RerankerInputError("documents 必须是非空 list")
        for i, doc in enumerate(documents):
            if not isinstance(doc, str) or not doc.strip():
                raise RerankerInputError(
                    f"documents[{i}] 必须是非空 str", index=i
                )

    # ---------- 模型生命周期 ----------

    def _resolve_device(self) -> str:
        """设备解析：显式 RERANKER_DEVICE 优先；空 → 自动（cuda→cpu）。"""
        if self._resolved_device is not None:
            return self._resolved_device
        explicit = (self._device_override or "").strip().lower()
        if explicit:
            if explicit not in {"cuda", "cpu"}:
                raise RerankerConfigurationError(
                    f"RERANKER_DEVICE 非法：{self._device_override!r}（允许 cuda / cpu）"
                )
            self._resolved_device = explicit
            return explicit
        # 自动：CUDA 可用 → cuda，否则 cpu
        try:
            import torch  # 懒导入

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:  # torch 缺失时由 _ensure_model 报错，这里先给 cpu
            device = "cpu"
        self._resolved_device = device
        return device

    def _ensure_model(self) -> None:
        """加载模型（进程内仅一次；线程安全）。

        Raises:
            RerankerModelError: torch / transformers 缺失、下载 / 加载失败
        """
        if self._model is not None:
            return
        with BGERerankerClient._load_lock:
            if self._model is not None:  # double-checked
                return
            if not (self._model_name or "").strip():
                raise RerankerConfigurationError("RERANKER_MODEL 为空，无法加载 Reranker")
            # 设备解析先行：非法 RERANKER_DEVICE 应报配置错，
            # 而非被后续 torch 缺失错误掩盖
            device = self._resolve_device()
            try:
                # 懒导入：未安装时抛 RerankerModelError 而非 import error
                import torch
                from transformers import AutoModelForSequenceClassification, AutoTokenizer
            except Exception as exc:  # noqa: BLE001 — 依赖缺失归一化
                raise RerankerModelError(
                    "加载 Reranker 失败：torch / transformers 未安装或导入失败"
                    f"（cause={type(exc).__name__}）",
                    cause_type=type(exc).__name__,
                ) from exc

            try:
                tokenizer = AutoTokenizer.from_pretrained(self._model_name)
                model = AutoModelForSequenceClassification.from_pretrained(
                    self._model_name
                )
                model.eval()
                model.to(device)
            except Exception as exc:  # noqa: BLE001 — 下载 / 加载失败归一化
                raise RerankerModelError(
                    f"Reranker 模型加载失败（model={self._model_name}, "
                    f"cause={type(exc).__name__}）",
                    cause_type=type(exc).__name__,
                ) from exc

            self._tokenizer = tokenizer
            self._model = model
            self._resolved_device = device
            logger.info(
                "Reranker model loaded",
                extra={
                    "model_name": self._model_name,
                    "device": device,
                    "max_length": self._max_length,
                },
            )

    # ---------- 打分 ----------

    def _score_pairs(self, query: str, documents: list[str]) -> list[float]:
        """对 (query, doc) pairs 执行 Cross-Encoder 推理（按 batch_size 分块）。

        Returns:
            sigmoid(logit) ∈ [0, 1]，与 documents 一一对应。

        性能（Phase 3.5.13）：
            - 单次 forward 处理 self._batch_size 个 (q, d) 对，
              累计到全量 documents 后拼接
            - 每批 padding 在该批内动态计算 → 与「全量一次 forward」相比
              单条分数可能有非常小的浮点差异（任务书 § 二十允许）；
              rank 顺序一致
        """
        import torch

        assert self._model is not None and self._tokenizer is not None
        device = self._resolved_device or "cpu"
        bs = max(1, int(self._batch_size))
        scores: list[float] = []
        with torch.inference_mode():
            for start in range(0, len(documents), bs):
                batch_docs = documents[start : start + bs]
                inputs = self._tokenizer(
                    [(query, d) for d in batch_docs],
                    padding=True,
                    truncation=True,
                    max_length=self._max_length,
                    return_tensors="pt",
                ).to(device)
                logits = self._model(**inputs).logits.squeeze(-1)
                probs = torch.sigmoid(logits)
                scores.extend(float(x) for x in probs.tolist())
        return scores

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        started = time.perf_counter()

        # ---- 1. 校验（不加载模型即可拒绝非法输入）----
        self._validate_inputs(query, documents)

        # ---- 2. 模型（懒加载，仅一次）----
        self._ensure_model()

        # ---- 3. 推理 ----
        try:
            scores = self._score_pairs(query, documents)
        except RerankerModelError:
            raise
        except Exception as exc:  # noqa: BLE001 — 推理异常归一化
            raise RerankerModelError(
                f"Reranker 推理失败（cause={type(exc).__name__}）",
                cause_type=type(exc).__name__,
            ) from exc

        # 防御：长度必须一致
        if len(scores) != len(documents):
            raise RerankerModelError(
                f"Reranker 返回 score 数量与 documents 不一致"
                f"（{len(scores)} vs {len(documents)}）"
            )

        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "Reranker rerank completed",
            extra={
                "query_length": len(query),
                "doc_count": len(documents),
                "device": self._resolved_device,
                "elapsed_ms": elapsed_ms,
            },
        )
        return scores


# ============================================================
# 工厂 / 默认实例
# ============================================================

def create_reranker_client() -> RerankerClient:
    """按配置构造 Reranker 客户端（enabled=False 时仍可构造——
    本模块只用于离线实验，开关由实验入口检查）。"""
    if not (settings.reranker.model or "").strip():
        raise RerankerConfigurationError(
            "RERANKER_MODEL 未配置，无法创建 Reranker 客户端"
        )
    return BGERerankerClient()


_default_client: BGERerankerClient | None = None
_default_client_lock = threading.Lock()


def get_default_reranker_client() -> BGERerankerClient:
    """进程级单例（并发安全；确保 12 cases 共用同一模型实例）。"""
    global _default_client
    if _default_client is None:
        with _default_client_lock:
            if _default_client is None:
                _default_client = BGERerankerClient()
    return _default_client


def reset_default_reranker_client() -> None:
    """仅测试用：重置单例。"""
    global _default_client
    with _default_client_lock:
        _default_client = None
