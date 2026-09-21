"""Chunking Engine（Phase 3.4）。

Chunker 只负责：
    str → list[TextChunk]

不做：
    - Embedding / 向量生成
    - 向量检索 / pgvector
    - LLM / DeepSeek
    - 数据库写入（`knowledge_chunk` 写入属于后续 Ingestion Service）
    - RAG / Tool / Agent / LangGraph

公共接口：
    TextChunk              — 不可变 Chunk 数据结构
    TextChunker (ABC)      — Chunking 策略抽象基类
    MarkdownAwareChunker   — Heading-aware + Size Limit 实现
    default_chunker()      — 便捷函数（chunk_size=800, chunk_overlap=100）
    InvalidChunkSizeError  — chunk_size 非法
    InvalidChunkOverlapError — chunk_overlap 非法
"""
from backend.app.rag.chunking.models import (
    ChunkingError,
    InvalidChunkOverlapError,
    InvalidChunkSizeError,
    TextChunk,
)
from backend.app.rag.chunking.text_chunker import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    MarkdownAwareChunker,
    TextChunker,
    default_chunker,
)

__all__ = [
    # 数据结构
    "TextChunk",
    # 接口
    "TextChunker",
    "MarkdownAwareChunker",
    "default_chunker",
    # 常量
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_CHUNK_OVERLAP",
    # 异常
    "ChunkingError",
    "InvalidChunkSizeError",
    "InvalidChunkOverlapError",
]