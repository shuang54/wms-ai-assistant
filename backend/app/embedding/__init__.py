"""Embedding Client（Phase 3.5.1）。

只负责：
    文本 → 向量（list[float]）

不做：
    - embed_many 批量接口（Phase 3.5.2）
    - 向量写入 PostgreSQL / knowledge_chunk.embedding
    - pgvector 查询 / Vector Search / RAG Retrieval
    - 任何数据库 INSERT / UPDATE

Provider 说明：
    DeepSeek 官方 API 不提供 Embedding 端点（调研结论，见 client.py 模块注释），
    因此本模块实现的是**通用 OpenAI 兼容**客户端（POST {base_url}/embeddings），
    模型名全部通过环境变量 EMBEDDING_MODEL 配置，不做任何猜测。

公共接口：
    EmbeddingClient (ABC)           — async embed(text) -> list[float]
    OpenAICompatibleEmbeddingClient  — OpenAI / 硅基流动 / Qwen / Ollama 等兼容实现
    create_embedding_client()        — 工厂（缺配置抛 EmbeddingConfigurationError）
    get_default_embedding_client()   — 模块级单例

异常体系（见 exceptions.py）：
    EmbeddingError
    ├── EmbeddingConfigurationError
    ├── EmbeddingInputError
    ├── EmbeddingAPIError
    ├── EmbeddingResponseError
    └── EmbeddingDimensionError
"""
from backend.app.embedding.client import (
    EmbeddingClient,
    OpenAICompatibleEmbeddingClient,
    create_embedding_client,
    get_default_embedding_client,
    reset_default_embedding_client,
)
from backend.app.embedding.exceptions import (
    EmbeddingAPIError,
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingInputError,
    EmbeddingResponseError,
)

__all__ = [
    # 接口
    "EmbeddingClient",
    "OpenAICompatibleEmbeddingClient",
    "create_embedding_client",
    "get_default_embedding_client",
    "reset_default_embedding_client",
    # 异常
    "EmbeddingError",
    "EmbeddingConfigurationError",
    "EmbeddingInputError",
    "EmbeddingAPIError",
    "EmbeddingResponseError",
    "EmbeddingDimensionError",
]
