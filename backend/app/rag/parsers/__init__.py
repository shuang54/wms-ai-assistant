"""Document Parsers 子包（Phase 3.3）。

公共接口：
    DocumentParser         — 抽象基类
    DocumentParseError     — 通用异常
    DocumentNotFoundError  — 文件不存在
    UnsupportedDocumentTypeError — 不支持的扩展名
    TextParser / MarkdownParser — 具体实现
    get_parser(file_path)  — Factory 入口

未实现：
    PDF / DOCX / Excel / Word — 后续 Phase；扩展时仅需修改 factory._REGISTRY
"""
from backend.app.rag.parsers.base import (
    DocumentNotFoundError,
    DocumentParseError,
    DocumentParser,
    UnsupportedDocumentTypeError,
)
from backend.app.rag.parsers.factory import get_parser, supported_extensions
from backend.app.rag.parsers.markdown_parser import MarkdownParser
from backend.app.rag.parsers.text_parser import TextParser

__all__ = [
    # 接口
    "DocumentParser",
    "DocumentParseError",
    "DocumentNotFoundError",
    "UnsupportedDocumentTypeError",
    # 实现
    "TextParser",
    "MarkdownParser",
    # Factory
    "get_parser",
    "supported_extensions",
]