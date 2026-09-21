"""TXT Parser（Phase 3.3）。

行为：
    - UTF-8 编码（仅 utf-8；不尝试探测/容错编码——保持简单、可测试）
    - 文件不存在 → DocumentNotFoundError
    - 文件为空 → 返回 `""`
    - 编码非 UTF-8 → DocumentParseError（UnicodeDecodeError）
    - 其他 IO 错误 → DocumentParseError
"""
from __future__ import annotations

from pathlib import Path

from backend.app.rag.parsers.base import DocumentParser, read_text_file

__all__ = ["TextParser"]


class TextParser(DocumentParser):
    """纯文本 Parser（仅 UTF-8）。"""

    @property
    def file_type(self) -> str:
        return "txt"

    def parse(self, file_path: str | Path) -> str:
        return read_text_file(file_path, encoding="utf-8")