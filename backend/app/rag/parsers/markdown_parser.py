"""Markdown Parser（Phase 3.3，第一阶段）。

设计：
    - 第一阶段**不**做 AST 解析；保留原始 Markdown（含标题符号）
    - 原因：Phase 3.4 Chunking 阶段可能直接按 Markdown 标题切分；
            太早做 AST 解析会限制 Chunking 策略
    - 替换实现时**不**影响外部接口（仍 `parse(file_path) -> str`）
"""
from __future__ import annotations

from pathlib import Path

from backend.app.rag.parsers.base import DocumentParser, read_text_file

__all__ = ["MarkdownParser"]


class MarkdownParser(DocumentParser):
    """Markdown Parser（保留原始文本与结构）。"""

    @property
    def file_type(self) -> str:
        return "md"

    def parse(self, file_path: str | Path) -> str:
        return read_text_file(file_path, encoding="utf-8")