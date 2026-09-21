"""Parser Factory（Phase 3.3）。

根据文件路径 / 扩展名选择 Parser。

支持的格式：
    .md   → MarkdownParser
    .txt  → TextParser

不支持的格式（.pdf / .docx / .xlsx 等）抛出 `UnsupportedDocumentTypeError`。

扩展方式（未来 Phase）：
    1. 新建模块，实现 `DocumentParser`
    2. 在本文件 `_REGISTRY` 中注册 `{扩展名: Parser类}`
"""
from __future__ import annotations

from pathlib import Path
from typing import Type

from backend.app.rag.parsers.base import (
    DocumentParser,
    UnsupportedDocumentTypeError,
)
from backend.app.rag.parsers.markdown_parser import MarkdownParser
from backend.app.rag.parsers.text_parser import TextParser

__all__ = ["get_parser", "supported_extensions"]


# 显式注册表：扩展名（小写、不含点号）→ Parser 类
# 新格式只需在此处追加一行。
_REGISTRY: dict[str, Type[DocumentParser]] = {
    "md": MarkdownParser,
    "txt": TextParser,
}


def get_parser(file_path: str | Path) -> DocumentParser:
    """根据文件扩展名返回对应 Parser 实例（每次调用返回新实例）。

    Args:
        file_path: 文件路径（仅用于判断扩展名，**不读取**文件）。

    Returns:
        `DocumentParser` 子类实例。

    Raises:
        UnsupportedDocumentTypeError: 文件无扩展名 / 扩展名不在 `_REGISTRY`。
    """
    path = Path(file_path)
    suffix = path.suffix.lstrip(".").lower()
    if not suffix:
        raise UnsupportedDocumentTypeError(
            f"无法识别文件类型（无扩展名）: {path}"
        )
    cls = _REGISTRY.get(suffix)
    if cls is None:
        raise UnsupportedDocumentTypeError(
            f"不支持的文档类型: '.{suffix}'（当前支持: {sorted(_REGISTRY)}）"
        )
    return cls()


def supported_extensions() -> list[str]:
    """返回当前所有支持的扩展名（小写、不含点号），按字母序。"""
    return sorted(_REGISTRY.keys())