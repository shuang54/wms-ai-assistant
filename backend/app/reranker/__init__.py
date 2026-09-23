"""Reranker 模块（Phase 3.5.12，离线实验）。

Cross-Encoder Reranker 客户端，用于**离线实验**（Reranker Evaluation），
未接入生产 RAG：RagService / ChatService / VectorSearchService /
/api/chat / /api/rag/answer 均不 import 本模块。

结构：
    __init__.py     导出
    client.py       RerankerClient 抽象 + BGERerankerClient 实现
    exceptions.py   异常体系
"""
from backend.app.reranker.client import (
    BGERerankerClient,
    RerankerClient,
    create_reranker_client,
    get_default_reranker_client,
)
from backend.app.reranker.exceptions import (
    RerankerConfigurationError,
    RerankerError,
    RerankerInputError,
    RerankerModelError,
)

__all__ = [
    "RerankerClient",
    "BGERerankerClient",
    "create_reranker_client",
    "get_default_reranker_client",
    "RerankerError",
    "RerankerInputError",
    "RerankerModelError",
    "RerankerConfigurationError",
]
