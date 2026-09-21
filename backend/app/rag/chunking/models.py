"""Chunking 数据结构 + 异常体系（Phase 3.4）。

设计原则：
    - `TextChunk` 为不可变 dataclass（frozen=True）
    - 异常继承自 `ChunkingError`，与 `ValueError` 多继承以便上层统一捕获
    - 不依赖 DB / LLM / Parser

未来扩展：
    - Token-aware chunking 仍可继续用本数据结构
    - Phase 3.5/3.6 在此基础上增加 embedding 字段
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "TextChunk",
    "ChunkingError",
    "InvalidChunkSizeError",
    "InvalidChunkOverlapError",
]


# ============================================================
# 异常体系
# ============================================================

class ChunkingError(Exception):
    """Chunking 通用异常基类。

    所有 Chunking 阶段抛出的异常均继承自本类；
    调用方可以 `except ChunkingError` 统一捕获。
    """


class InvalidChunkSizeError(ChunkingError, ValueError):
    """`chunk_size` 非法：

    - 不是 int 类型
    - <= 0
    """


class InvalidChunkOverlapError(ChunkingError, ValueError):
    """`chunk_overlap` 非法：

    - 不是 int 类型
    - < 0
    - >= `chunk_size`
    """


# ============================================================
# Chunk 数据结构
# ============================================================

@dataclass(frozen=True)
class TextChunk:
    """一个 Chunk 的不可变视图。

    字段：
        content:     Chunk 纯文本（含 Markdown 标题前缀）
        chunk_index: 从 0 开始连续递增（由 Chunking 层保证）
        metadata:    dict，至少包含：
                       - `source_type: str`     "markdown" / "text"
                       - `heading_path: list[str]` 当前段落对应的标题路径
    """

    content: str
    chunk_index: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError(f"content 必须是 str（当前: {type(self.content).__name__}）")
        if not isinstance(self.chunk_index, int) or isinstance(self.chunk_index, bool):
            raise TypeError(
                f"chunk_index 必须是 int（当前: {type(self.chunk_index).__name__}）"
            )
        if self.chunk_index < 0:
            raise ValueError(f"chunk_index 必须 >= 0（当前: {self.chunk_index}）")
        if not isinstance(self.metadata, dict):
            raise TypeError(f"metadata 必须是 dict（当前: {type(self.metadata).__name__}）")