"""Embedding Client 抽象层与实现（Phase 3.5.1）。

抽象边界：

    （未来的 Ingestion / RAG Service）
        ↓
    EmbeddingClient（ABC）
        ↓
    OpenAICompatibleEmbeddingClient
        ↓
    httpx (HTTP)
        ↓
    OpenAI-compatible Embeddings API
      （OpenAI / 硅基流动 SiliconFlow / Qwen (compatible-mode)
        / Ollama / 其它自部署 —— 均遵循 POST {base_url}/embeddings 协议）

⚠ 关于 DeepSeek（重要事实，Phase 3.5.1 调研结论）：

    DeepSeek 官方 API（api.deepseek.com）**不提供 Embedding 端点**，
    仅提供 chat/completions（deepseek-chat / deepseek-reasoner）。
    因此本模块**不实现** DeepSeekEmbeddingClient，
    也不猜测任何 DeepSeek Embedding 模型名。

    Embedding Provider / Model / Base URL / API Key / Dimension
    全部通过环境变量配置（见 backend/app/config.py EmbeddingSettings），
    任何 OpenAI 兼容的 Embedding 服务都可以接入。

维度契约：

    - `EMBEDDING_DIMENSION`（默认 1536）必须与 Phase 3.2 的
      `knowledge_chunk.embedding vector(1536)` 一致；
    - Client 校验 `len(vector) == dimension`，不一致抛
      `EmbeddingDimensionError`——**不补 0、不截断、不改数据库**。

本阶段**不做**（后续 Phase）：
    - embed_many 批量接口（Phase 3.5.2）
    - 向量写入 PostgreSQL / pgvector 查询
    - RAG Retrieval / Vector Search
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from backend.app.config import EmbeddingSettings, settings
from backend.app.embedding.exceptions import (
    EmbeddingAPIError,
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingInputError,
    EmbeddingResponseError,
)

logger = logging.getLogger(__name__)


# ============================================================
# 抽象接口
# ============================================================

class EmbeddingClient(ABC):
    """Embedding Client 抽象接口。

    只负责：文本 → 向量。
    不负责：批量、缓存、持久化、检索。

    注意：与 Phase 2 LLMClient 一致，采用 async 接口
    （AGENTS.md §7 代码风格：使用 async / await）。
    """

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """将单段文本转为 embedding 向量。

        Args:
            text: 非空、非纯空白的文本（Chunking 阶段已保证长度合理）。

        Returns:
            长度等于 EMBEDDING_DIMENSION 的 float 列表。

        Raises:
            EmbeddingInputError:         text 为空 / 纯空白 / 非 str
            EmbeddingConfigurationError: 配置缺失或非法（构造期即可抛出）
            EmbeddingAPIError:           网络 / 超时 / HTTP 非 2xx
            EmbeddingResponseError:     响应结构异常
            EmbeddingDimensionError:     向量维度与配置不一致
        """
        raise NotImplementedError


# ============================================================
# OpenAI 兼容实现
# ============================================================

class OpenAICompatibleEmbeddingClient(EmbeddingClient):
    """OpenAI Embeddings API 兼容协议的 Embedding Client。

    通过环境变量 EMBEDDING_BASE_URL + EMBEDDING_MODEL + EMBEDDING_API_KEY，
    可对接以下任一服务（都遵循相同的 /embeddings 协议）：

        - OpenAI:        https://api.openai.com/v1        (text-embedding-3-small: 1536)
        - 硅基流动:       https://api.siliconflow.cn/v1     (BAAI/bge-* 系列)
        - Qwen:          https://dashscope.aliyuncs.com/compatible-mode/v1
        - Ollama:        http://localhost:11434/v1
        - 其它自部署

    请求协议：

        POST {base_url}/embeddings
        Authorization: Bearer {api_key}
        Content-Type: application/json
        {
            "model": "...",
            "input": "..."
        }

    响应结构（兼容）：

        {
            "data": [
                {"embedding": [0.1, 0.2, ...], "index": 0}
            ],
            "model": "...",
            "usage": {"prompt_tokens": 5, "total_tokens": 5}
        }

    安全：
        - API Key 只出现在请求 Header，绝不写入日志 / 异常消息；
        - 非 2xx 响应体预览截断至 200 字符。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimension: int,
        timeout: float = 60.0,
        provider: str = "openai_compatible",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # ---- 配置校验：缺失 / 非法即抛 EmbeddingConfigurationError ----
        if not api_key:
            raise EmbeddingConfigurationError(
                "EMBEDDING_API_KEY 未配置（且未回退到 LLM_API_KEY）"
            )
        if not base_url:
            raise EmbeddingConfigurationError("EMBEDDING_BASE_URL 未配置")
        if not model:
            raise EmbeddingConfigurationError("EMBEDDING_MODEL 未配置")
        if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension <= 0:
            raise EmbeddingConfigurationError(
                f"EMBEDDING_DIMENSION 必须是正整数（当前: {dimension!r}）"
            )
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise EmbeddingConfigurationError(
                f"EMBEDDING_TIMEOUT 必须是正数（当前: {timeout!r}）"
            )

        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._dimension = dimension
        self._timeout = httpx.Timeout(float(timeout))
        self._provider = provider or "openai_compatible"
        # 仅测试使用：传入 httpx.MockTransport 以避免真实网络
        self._transport = transport

    # ---------- internal helpers ----------

    def _endpoint_url(self) -> str:
        return f"{self._base_url.rstrip('/')}/embeddings"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _validate_input(self, text: str) -> str:
        """输入校验：空 / 纯空白 / 非 str 直接拒绝，不发送给真实 API。"""
        if not isinstance(text, str):
            raise EmbeddingInputError(
                f"输入必须是 str（当前: {type(text).__name__}）"
            )
        stripped = text.strip()
        if not stripped:
            raise EmbeddingInputError("输入文本为空或纯空白，已拒绝（不调用 Embedding API）")
        # 不做任何截断：Chunking 已负责文本切分，长度超限由 API 报错
        return text

    def _extract_vector(self, data: Any) -> list[float]:
        """从响应 JSON 中提取并校验向量。"""
        if not isinstance(data, dict) or "data" not in data:
            raise EmbeddingResponseError(
                f"Embedding 响应结构不符合预期（缺少 data 字段）: {str(data)[:200]}"
            )
        items = data["data"]
        if not isinstance(items, list) or len(items) == 0:
            raise EmbeddingResponseError("Embedding 响应 data 为空")
        first = items[0]
        if not isinstance(first, dict) or "embedding" not in first:
            raise EmbeddingResponseError("Embedding 响应缺少 data[0].embedding 字段")

        vector = first["embedding"]
        if not isinstance(vector, list):
            raise EmbeddingResponseError(
                f"embedding 类型不是 list：{type(vector).__name__}"
            )
        if len(vector) == 0:
            raise EmbeddingResponseError("embedding 向量为空列表")
        for i, item in enumerate(vector):
            if not isinstance(item, (int, float)) or isinstance(item, bool):
                raise EmbeddingResponseError(
                    f"embedding[{i}] 不是数值：{type(item).__name__}"
                )

        # ---- 维度校验：不一致直接失败，不补 0 / 不截断 / 不改数据库 ----
        if len(vector) != self._dimension:
            raise EmbeddingDimensionError(
                f"Embedding 向量维度不匹配：模型返回 {len(vector)} 维，"
                f"EMBEDDING_DIMENSION 配置为 {self._dimension} 维。"
                "请修正 EMBEDDING_DIMENSION 或更换模型；"
                "如需修改数据库维度，请先执行 Phase 3.2 的迁移方案。"
            )
        return [float(x) for x in vector]

    def _log_common(self) -> dict[str, Any]:
        # 注意：绝不包含 api_key
        return {
            "embedding_provider": self._provider,
            "embedding_model": self._model,
            "embedding_dimension": self._dimension,
        }

    # ---------- public API ----------

    async def embed(self, text: str) -> list[float]:
        text = self._validate_input(text)

        url = self._endpoint_url()
        headers = self._headers()
        payload = {"model": self._model, "input": text}
        client_kwargs: dict[str, Any] = {"timeout": self._timeout}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        extra_base = self._log_common()
        logger.info(
            "Embedding request start: text_len=%d",
            len(text),
            extra={**extra_base, "text_length": len(text)},
        )
        start_time = time.perf_counter()

        # ---- HTTP 调用 ----
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.ConnectError as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "Embedding connection failed",
                extra={**extra_base, "elapsed_ms": elapsed_ms, "error_type": "ConnectError"},
            )
            raise EmbeddingAPIError(
                f"无法连接 Embedding 服务 {self._base_url}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "Embedding request timeout",
                extra={**extra_base, "elapsed_ms": elapsed_ms, "error_type": "Timeout"},
            )
            raise EmbeddingAPIError(f"Embedding 请求超时: {exc}") from exc
        except httpx.HTTPError as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "Embedding http error",
                extra={
                    **extra_base,
                    "elapsed_ms": elapsed_ms,
                    "error_type": type(exc).__name__,
                },
            )
            raise EmbeddingAPIError(f"Embedding 请求异常: {exc}") from exc

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # ---- 非 2xx ----
        if response.status_code != 200:
            # 截断响应体以避免日志/异常信息过长
            body_preview = response.text[:200]
            logger.error(
                "Embedding non-2xx response: status=%d elapsed=%.1fms",
                response.status_code,
                elapsed_ms,
                extra={
                    **extra_base,
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "response_body_preview": body_preview,
                },
            )
            raise EmbeddingAPIError(
                f"Embedding API 返回 HTTP {response.status_code}: {body_preview[:200]}"
            )

        # ---- 响应解析 ----
        try:
            data = response.json()
        except ValueError as exc:
            logger.error(
                "Embedding response not JSON",
                extra={**extra_base, "elapsed_ms": elapsed_ms},
            )
            raise EmbeddingResponseError(f"Embedding 响应不是合法 JSON: {exc}") from exc

        vector = self._extract_vector(data)

        logger.info(
            "Embedding request success: elapsed=%.1fms vector_len=%d",
            elapsed_ms,
            len(vector),
            extra={
                **extra_base,
                "elapsed_ms": elapsed_ms,
                "vector_length": len(vector),
            },
        )
        return vector


# ============================================================
# 工厂 + 单例
# ============================================================

def create_embedding_client(
    embedding_settings: EmbeddingSettings | None = None,
) -> EmbeddingClient:
    """根据配置创建 Embedding Client。

    规则：
        - 配置完整（Key / Base URL / Model / Dimension）→
            OpenAICompatibleEmbeddingClient
        - 任何配置缺失 → 抛 EmbeddingConfigurationError

    与 LLM 工厂不同，这里**没有** Mock 回退：
    Embedding 没有"演示模式"的意义，缺配置必须显式失败，
    由调用方（未来的 Ingestion Service）决定如何降级。

    API Key 回退链：EMBEDDING_API_KEY → LLM_API_KEY
    （在 EmbeddingSettings.api_key 中实现）。
    """
    s = embedding_settings if embedding_settings is not None else settings.embedding
    # 构造器内部会做完整校验并抛 EmbeddingConfigurationError
    return OpenAICompatibleEmbeddingClient(
        api_key=s.api_key,
        base_url=s.base_url,
        model=s.model,
        dimension=s.dimension,
        timeout=s.timeout,
        provider=s.provider,
    )


_default_client: EmbeddingClient | None = None


def get_default_embedding_client() -> EmbeddingClient:
    """获取默认 Embedding Client（模块级单例）。

    配置不完整时抛 EmbeddingConfigurationError。
    测试时可调用 reset_default_embedding_client() 重新构造。
    """
    global _default_client
    if _default_client is None:
        _default_client = create_embedding_client(settings.embedding)
    return _default_client


def reset_default_embedding_client() -> None:
    """测试辅助：重置默认客户端缓存。"""
    global _default_client
    _default_client = None


__all__ = [
    "EmbeddingClient",
    "OpenAICompatibleEmbeddingClient",
    "create_embedding_client",
    "get_default_embedding_client",
    "reset_default_embedding_client",
    # 异常（便于从包顶层统一 import）
    "EmbeddingError",
    "EmbeddingConfigurationError",
    "EmbeddingInputError",
    "EmbeddingAPIError",
    "EmbeddingResponseError",
    "EmbeddingDimensionError",
]
