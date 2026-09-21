"""Document Parser 单元测试（Phase 3.3）。

覆盖（**不需要** PostgreSQL / DeepSeek / 任何外部依赖）：
    - TXT Parser: 中文 / 英文 / 空文件 / 不存在 / UTF-8 强制
    - Markdown Parser: 普通 / 中文 / 标题结构保留 / 空文件
    - Parser Factory: .md / .txt / .pdf / .docx / 无扩展名 / 大写扩展名
    - Fixtures: 通过 factory 读取 tests/fixtures/documents/ 下真实文件
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.rag.parsers import (
    DocumentNotFoundError,
    DocumentParseError,
    MarkdownParser,
    TextParser,
    UnsupportedDocumentTypeError,
    get_parser,
    supported_extensions,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "documents"


# ============================================================
# TXT Parser
# ============================================================

class TestTextParser:
    """TXT Parser 行为验证。"""

    def test_chinese_txt(self, tmp_path: Path) -> None:
        """中文 TXT：UTF-8 正常读取，中文不丢失。"""
        p = tmp_path / "cn.txt"
        p.write_text(
            "WMS 采购入库操作说明\n仓库人员使用 PDA 扫描物料。",
            encoding="utf-8",
        )
        text = TextParser().parse(p)
        assert "WMS" in text
        assert "采购入库" in text
        assert "PDA" in text

    def test_english_txt(self, tmp_path: Path) -> None:
        """英文 TXT：普通 ASCII 内容。"""
        p = tmp_path / "en.txt"
        p.write_text("WMS inbound process.\nScan via PDA.", encoding="utf-8")
        text = TextParser().parse(p)
        assert "inbound" in text
        assert "PDA" in text

    def test_empty_txt_returns_empty_string(self, tmp_path: Path) -> None:
        """空 TXT：返回 `""`，不抛异常。"""
        p = tmp_path / "empty.txt"
        p.write_text("", encoding="utf-8")
        assert TextParser().parse(p) == ""

    def test_missing_file_raises_document_not_found(self, tmp_path: Path) -> None:
        """不存在的路径：抛 `DocumentNotFoundError`。"""
        with pytest.raises(DocumentNotFoundError):
            TextParser().parse(tmp_path / "nope.txt")

    def test_non_utf8_encoding_raises_parse_error(self, tmp_path: Path) -> None:
        """非 UTF-8 编码（GBK）：抛 `DocumentParseError`。"""
        p = tmp_path / "gbk.txt"
        p.write_bytes("WMS 中文".encode("gbk"))
        with pytest.raises(DocumentParseError):
            TextParser().parse(p)

    def test_file_type_is_txt(self) -> None:
        """`file_type` 公开属性 = `'txt'`。"""
        assert TextParser().file_type == "txt"


# ============================================================
# Markdown Parser
# ============================================================

class TestMarkdownParser:
    """Markdown Parser 行为验证。"""

    def test_basic_markdown(self, tmp_path: Path) -> None:
        """普通 Markdown：保留标题符号（`#` / `##`）。"""
        p = tmp_path / "doc.md"
        p.write_text(
            "# Title\n\nBody text.\n\n## Section\n\nMore text.",
            encoding="utf-8",
        )
        text = MarkdownParser().parse(p)
        assert "# Title" in text
        assert "## Section" in text
        assert "Body text" in text

    def test_chinese_markdown(self, tmp_path: Path) -> None:
        """中文 Markdown：UTF-8 + 中文标题保留。"""
        p = tmp_path / "cn.md"
        p.write_text(
            "# WMS 入库操作\n\n## 创建入库通知\n\n采购订单审核完成后创建入库通知。",
            encoding="utf-8",
        )
        text = MarkdownParser().parse(p)
        assert "# WMS 入库操作" in text
        assert "## 创建入库通知" in text
        assert "采购订单" in text

    def test_heading_structure_preserved(self, tmp_path: Path) -> None:
        """多级标题 `#` / `###` / 多次 `#` 全部保留。"""
        md = (
            "# H1 Title\n\nintro\n\n"
            "### H3 Section\n\ntext\n\n"
            "# Another H1\n\nmore\n"
        )
        p = tmp_path / "h.md"
        p.write_text(md, encoding="utf-8")
        text = MarkdownParser().parse(p)
        assert "# H1 Title" in text
        assert "### H3 Section" in text
        assert "# Another H1" in text

    def test_empty_markdown_returns_empty_string(self, tmp_path: Path) -> None:
        """空 Markdown：返回 `""`。"""
        p = tmp_path / "empty.md"
        p.write_text("", encoding="utf-8")
        assert MarkdownParser().parse(p) == ""

    def test_file_type_is_md(self) -> None:
        """`file_type` 公开属性 = `'md'`。"""
        assert MarkdownParser().file_type == "md"


# ============================================================
# Parser Factory
# ============================================================

class TestParserFactory:
    """Factory 路由 + 不支持类型显式拒绝。"""

    def test_md_returns_markdown_parser(self) -> None:
        """`.md` → MarkdownParser。"""
        assert isinstance(get_parser("foo.md"), MarkdownParser)

    def test_txt_returns_text_parser(self) -> None:
        """`.txt` → TextParser。"""
        assert isinstance(get_parser("foo.txt"), TextParser)

    def test_pdf_raises_unsupported(self) -> None:
        """`.pdf` → UnsupportedDocumentTypeError。"""
        with pytest.raises(UnsupportedDocumentTypeError):
            get_parser("foo.pdf")

    def test_docx_raises_unsupported(self) -> None:
        """`.docx` → UnsupportedDocumentTypeError。"""
        with pytest.raises(UnsupportedDocumentTypeError):
            get_parser("foo.docx")

    def test_xlsx_raises_unsupported(self) -> None:
        """`.xlsx` → UnsupportedDocumentTypeError。"""
        with pytest.raises(UnsupportedDocumentTypeError):
            get_parser("foo.xlsx")

    def test_no_extension_raises_unsupported(self) -> None:
        """无扩展名 → UnsupportedDocumentTypeError。"""
        with pytest.raises(UnsupportedDocumentTypeError):
            get_parser("README")

    def test_uppercase_extension_normalized(self) -> None:
        """大写扩展名（.MD / .TXT）应能正确路由。"""
        assert isinstance(get_parser("foo.MD"), MarkdownParser)
        assert isinstance(get_parser("foo.TXT"), TextParser)

    def test_supported_extensions_lists_md_and_txt(self) -> None:
        """`supported_extensions()` 返回当前所有支持扩展名。"""
        exts = supported_extensions()
        assert "md" in exts
        assert "txt" in exts

    def test_factory_returns_new_instance(self) -> None:
        """Factory 每次返回新实例（避免状态污染）。"""
        p1 = get_parser("foo.md")
        p2 = get_parser("bar.md")
        assert p1 is not p2
        assert isinstance(p1, MarkdownParser)
        assert isinstance(p2, MarkdownParser)


# ============================================================
# Fixtures（真实文件 + Factory 集成）
# ============================================================

class TestWithFixtures:
    """通过 Factory 读取 tests/fixtures/documents/ 下真实文件。"""

    def test_sample_txt_via_factory(self) -> None:
        """`sample.txt` 真实文件 + Factory 解析。"""
        path = FIXTURES_DIR / "sample.txt"
        assert path.exists(), f"fixture missing: {path}"
        parser = get_parser(path)
        assert isinstance(parser, TextParser)
        text = parser.parse(path)
        assert "WMS采购入库操作说明" in text
        assert "采购订单" in text

    def test_sample_md_via_factory(self) -> None:
        """`sample.md` 真实文件 + Factory 解析；标题保留。"""
        path = FIXTURES_DIR / "sample.md"
        assert path.exists(), f"fixture missing: {path}"
        parser = get_parser(path)
        assert isinstance(parser, MarkdownParser)
        text = parser.parse(path)
        assert "# WMS采购入库" in text
        assert "## 创建入库通知" in text
        assert "## PDA上架" in text
        assert "## 收货确认" in text

    def test_empty_txt_via_factory(self) -> None:
        """`empty.txt` 通过 Factory 解析返回空字符串。"""
        path = FIXTURES_DIR / "empty.txt"
        assert path.exists(), f"fixture missing: {path}"
        parser = get_parser(path)
        assert isinstance(parser, TextParser)
        assert parser.parse(path) == ""


# ============================================================
# 异常类型继承关系
# ============================================================

class TestExceptionHierarchy:
    """异常类型层级应当合理：调用方可用 DocumentParseError 统一捕获。"""

    def test_document_not_found_is_subclass_of_parse_error(self) -> None:
        assert issubclass(DocumentNotFoundError, DocumentParseError)

    def test_unsupported_is_subclass_of_parse_error(self) -> None:
        assert issubclass(UnsupportedDocumentTypeError, DocumentParseError)

    def test_unified_catch_with_parse_error(self, tmp_path: Path) -> None:
        """调用方可以用 `except DocumentParseError` 统一捕获。"""
        caught: list[Exception] = []
        # 不存在的文件
        try:
            TextParser().parse(tmp_path / "missing.txt")
        except DocumentParseError as e:
            caught.append(e)
        # 不支持的扩展名
        try:
            get_parser("foo.pdf")
        except DocumentParseError as e:
            caught.append(e)
        # 非 UTF-8
        p = tmp_path / "x.txt"
        p.write_bytes(b"\xff\xfe\x00\x00")
        try:
            TextParser().parse(p)
        except DocumentParseError as e:
            caught.append(e)
        assert len(caught) == 3