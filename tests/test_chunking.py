"""Text Chunking 单元测试（Phase 3.4）。

覆盖（**不需要** PostgreSQL / DeepSeek / 任何外部依赖）：
    - TextChunk 数据结构：字段、不可变
    - 基础：正常切分 / 空 / 纯空白 / chunk_index 从 0 / 连续
    - Markdown：H1 / H2 / H3 / 子 Chunk 保留父级 / heading_path 正确
    - Size：短不切 / 超长切 / chunk_size 可配置 / chunk_overlap 可配置
    - 语义边界：段落 → 句子 → 字符 三级 fallback
    - 异常：非法 chunk_size / 非法 chunk_overlap / overlap>=size
    - source_type：markdown / text / 中文
    - Fixtures：short.md / hierarchy.md / long.md
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.rag.chunking import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    InvalidChunkOverlapError,
    InvalidChunkSizeError,
    MarkdownAwareChunker,
    TextChunk,
    default_chunker,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "chunking"


# ============================================================
# TextChunk 数据结构
# ============================================================

class TestTextChunk:
    """TextChunk 字段与不可变性。"""

    def test_basic_construction(self) -> None:
        c = TextChunk(content="abc", chunk_index=0, metadata={"source_type": "text"})
        assert c.content == "abc"
        assert c.chunk_index == 0
        assert c.metadata == {"source_type": "text"}

    def test_default_metadata_is_empty_dict(self) -> None:
        c = TextChunk(content="x", chunk_index=0)
        assert c.metadata == {}

    def test_chunk_is_immutable(self) -> None:
        c = TextChunk(content="abc", chunk_index=0, metadata={})
        with pytest.raises((AttributeError, TypeError)):
            c.content = "xyz"  # type: ignore[misc]

    def test_negative_chunk_index_raises(self) -> None:
        with pytest.raises(ValueError):
            TextChunk(content="x", chunk_index=-1)

    def test_non_str_content_raises(self) -> None:
        with pytest.raises(TypeError):
            TextChunk(content=123, chunk_index=0)  # type: ignore[arg-type]


# ============================================================
# 基础
# ============================================================

class TestChunkingBasics:
    """最基础行为：能切 / 空 / 纯空白 / chunk_index 编号。"""

    def test_normal_text_produces_chunks(self) -> None:
        chunks = MarkdownAwareChunker().chunk("这是第一段。\n\n这是第二段。")
        assert len(chunks) >= 1
        assert all(isinstance(c, TextChunk) for c in chunks)

    def test_empty_text_returns_empty_list(self) -> None:
        assert MarkdownAwareChunker().chunk("") == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        assert MarkdownAwareChunker().chunk("   \n\n  \t\n  ") == []
        assert MarkdownAwareChunker().chunk("\n\n\n") == []

    def test_none_text_returns_empty_list(self) -> None:
        # None 输入不应该崩溃（Chunking 是内部组件，对调用方友好）
        assert MarkdownAwareChunker().chunk(None) == []  # type: ignore[arg-type]

    def test_chunk_index_starts_at_zero(self) -> None:
        chunks = MarkdownAwareChunker(chunk_size=50, chunk_overlap=10).chunk(
            "# A\n\nA1. " * 5 + "\n\n# B\n\nB1."
        )
        assert chunks[0].chunk_index == 0

    def test_chunk_index_is_continuous(self) -> None:
        text = "# A\n\n" + ("长内容。" * 50) + "\n\n# B\n\n" + ("长内容。" * 50)
        chunks = MarkdownAwareChunker(chunk_size=100, chunk_overlap=20).chunk(text)
        indexes = [c.chunk_index for c in chunks]
        assert indexes == list(range(len(chunks)))


# ============================================================
# Markdown
# ============================================================

class TestMarkdownChunking:
    """Markdown 标题识别 + heading_path + 子 Chunk 保留父级。"""

    def test_h1_recognized(self) -> None:
        chunks = MarkdownAwareChunker().chunk("# 一级标题\n\n内容")
        assert "# 一级标题" in chunks[0].content
        assert chunks[0].metadata["heading_path"] == ["一级标题"]
        assert chunks[0].metadata["source_type"] == "markdown"

    def test_h2_recognized(self) -> None:
        chunks = MarkdownAwareChunker().chunk("## 二级标题\n\n内容")
        assert "## 二级标题" in chunks[0].content
        assert chunks[0].metadata["heading_path"] == ["二级标题"]

    def test_h3_recognized(self) -> None:
        chunks = MarkdownAwareChunker().chunk("### 三级标题\n\n内容")
        assert "### 三级标题" in chunks[0].content
        assert chunks[0].metadata["heading_path"] == ["三级标题"]

    def test_h4_through_h6_recognized(self) -> None:
        for n in (4, 5, 6):
            text = ("#" * n) + " 标题\n\n内容"
            chunks = MarkdownAwareChunker().chunk(text)
            assert ("#" * n) + " 标题" in chunks[0].content
            assert chunks[0].metadata["heading_path"] == ["标题"]

    def test_child_chunk_preserves_parent_heading(self) -> None:
        """子 Chunk 显式包含父级标题（prefix）。"""
        text = "# 父标题\n\n父段。\n\n## 子标题\n\n子段。\n"
        chunks = MarkdownAwareChunker(chunk_size=2000).chunk(text)
        assert len(chunks) >= 1
        # 所有 chunks 都应包含父级 # 父标题
        for c in chunks:
            assert "# 父标题" in c.content

    def test_heading_path_correct_for_nested(self) -> None:
        text = "# A\n\nA1.\n\n## B\n\nB1.\n\n### C\n\nC1."
        chunks = MarkdownAwareChunker().chunk(text)
        # 找到包含 C1 的 chunk
        c_chunk = next(c for c in chunks if "C1." in c.content)
        assert c_chunk.metadata["heading_path"] == ["A", "B", "C"]

    def test_heading_level_pruned_correctly(self) -> None:
        """标题级别跳跃时栈正确截断（## 后 ### 不会保留 ## 之前的同级）。"""
        text = "# A\n\n## B\n\nB1.\n\n### C\n\nC1.\n\n## D\n\nD1."
        chunks = MarkdownAwareChunker().chunk(text)
        # D1 的 heading_path 应该是 [A, D]，不是 [A, B, D]
        d_chunk = next(c for c in chunks if "D1." in c.content)
        assert d_chunk.metadata["heading_path"] == ["A", "D"]

    def test_heading_only_section_produces_no_empty_chunk(self) -> None:
        """只含标题、没有正文的 section 不产生空 chunk。"""
        text = "# A\n\n## B\n\n内容B"
        chunks = MarkdownAwareChunker().chunk(text)
        # 只有 1 个 chunk（B 段），A 标题不应单独产生 chunk
        assert len(chunks) == 1
        assert "内容B" in chunks[0].content


# ============================================================
# Size Limit
# ============================================================

class TestSizeLimit:
    """chunk_size / chunk_overlap 配置。"""

    def test_short_text_not_split(self) -> None:
        """短文本（< chunk_size）不应被无意义切分。"""
        chunks = MarkdownAwareChunker(chunk_size=800).chunk("# 短\n\n内容。")
        assert len(chunks) == 1

    def test_long_text_is_split(self) -> None:
        """超长内容应被切分为多个 chunk。"""
        long_para = "非常长的段落用于测试。" * 100  # ~1100 chars
        chunks = MarkdownAwareChunker(chunk_size=200, chunk_overlap=50).chunk(
            "# 长\n\n" + long_para
        )
        assert len(chunks) > 1

    def test_chunk_size_is_configurable(self) -> None:
        """小 chunk_size → 更多 chunks。"""
        text = "# T\n\n" + ("内容。" * 50)
        small = MarkdownAwareChunker(chunk_size=50, chunk_overlap=10).chunk(text)
        large = MarkdownAwareChunker(chunk_size=2000, chunk_overlap=10).chunk(text)
        assert len(small) >= len(large)
        assert len(small) > 1
        assert len(large) == 1

    def test_chunk_overlap_is_configurable(self) -> None:
        """overlap=0 时不重叠；>0 时重叠。"""
        long_para = "这是一个长句子用于测试 chunk_overlap 行为。" * 30
        no_overlap_chunks = MarkdownAwareChunker(
            chunk_size=200, chunk_overlap=0,
        ).chunk("# T\n\n" + long_para)
        with_overlap_chunks = MarkdownAwareChunker(
            chunk_size=200, chunk_overlap=50,
        ).chunk("# T\n\n" + long_para)
        # 有 overlap 时可能产生更多 chunk（因为 overlap 占用空间）
        assert len(with_overlap_chunks) >= len(no_overlap_chunks)
        # overlap 不破坏 heading prefix：所有 chunk 都含 # T
        for c in with_overlap_chunks:
            assert "# T" in c.content

    def test_default_chunk_size_is_800(self) -> None:
        assert MarkdownAwareChunker().chunk_size == DEFAULT_CHUNK_SIZE
        assert DEFAULT_CHUNK_SIZE == 800

    def test_default_chunk_overlap_is_100(self) -> None:
        assert MarkdownAwareChunker().chunk_overlap == DEFAULT_CHUNK_OVERLAP
        assert DEFAULT_CHUNK_OVERLAP == 100


# ============================================================
# 语义边界
# ============================================================

class TestSemanticBoundary:
    """段落 → 句子 → 字符 三级 fallback。"""

    def test_split_by_paragraph_first(self) -> None:
        """多段落：每个段落先独立。"""
        text = "# T\n\n第一段内容。\n\n第二段内容。\n\n第三段内容。"
        chunks = MarkdownAwareChunker(chunk_size=2000).chunk(text)
        assert len(chunks) == 1
        # 全部段落都在同一 chunk 内（chunk_size 够大时）
        assert "第一段内容。" in chunks[0].content
        assert "第二段内容。" in chunks[0].content
        assert "第三段内容。" in chunks[0].content

    def test_long_paragraph_split_by_sentence(self) -> None:
        """单段超长 → 按句子切分，保留 heading prefix。"""
        long_text = "# T\n\n" + ("句子一。" * 10) + ("句子二。" * 10)
        chunks = MarkdownAwareChunker(chunk_size=80, chunk_overlap=10).chunk(long_text)
        assert len(chunks) > 1
        # 所有 chunk 含 # T prefix（句子切分不破坏标题）
        for c in chunks:
            assert "# T" in c.content

    def test_long_no_punctuation_paragraph_split_by_char(self) -> None:
        """单段无标点超长 → 字符切分兜底。"""
        long_text = "# T\n\n" + ("a" * 1000)
        chunks = MarkdownAwareChunker(chunk_size=100, chunk_overlap=10).chunk(long_text)
        assert len(chunks) >= 1
        # 所有 chunk 含 # T prefix
        for c in chunks:
            assert "# T" in c.content

    def test_chinese_sentence_boundary(self) -> None:
        """中文句号（。）作为句子边界。"""
        text = "# T\n\n第一句。第二句。第三句。"
        chunks = MarkdownAwareChunker().chunk(text)
        assert len(chunks) == 1
        assert "第一句。第二句。第三句。" in chunks[0].content


# ============================================================
# 异常
# ============================================================

class TestChunkingErrors:
    """参数验证异常。"""

    def test_zero_chunk_size_raises(self) -> None:
        with pytest.raises(InvalidChunkSizeError):
            MarkdownAwareChunker(chunk_size=0)

    def test_negative_chunk_size_raises(self) -> None:
        with pytest.raises(InvalidChunkSizeError):
            MarkdownAwareChunker(chunk_size=-1)

    def test_non_int_chunk_size_raises(self) -> None:
        with pytest.raises(InvalidChunkSizeError):
            MarkdownAwareChunker(chunk_size="abc")  # type: ignore[arg-type]

    def test_float_chunk_size_raises(self) -> None:
        with pytest.raises(InvalidChunkSizeError):
            MarkdownAwareChunker(chunk_size=100.5)  # type: ignore[arg-type]

    def test_negative_chunk_overlap_raises(self) -> None:
        with pytest.raises(InvalidChunkOverlapError):
            MarkdownAwareChunker(chunk_size=100, chunk_overlap=-1)

    def test_overlap_equals_size_raises(self) -> None:
        with pytest.raises(InvalidChunkOverlapError):
            MarkdownAwareChunker(chunk_size=100, chunk_overlap=100)

    def test_overlap_greater_than_size_raises(self) -> None:
        with pytest.raises(InvalidChunkOverlapError):
            MarkdownAwareChunker(chunk_size=100, chunk_overlap=200)

    def test_non_int_chunk_overlap_raises(self) -> None:
        with pytest.raises(InvalidChunkOverlapError):
            MarkdownAwareChunker(chunk_size=100, chunk_overlap="10")  # type: ignore[arg-type]

    def test_errors_subclass_value_error(self) -> None:
        """Chunking 异常应同时是 ValueError（便于上层统一捕获）。"""
        assert issubclass(InvalidChunkSizeError, ValueError)
        assert issubclass(InvalidChunkOverlapError, ValueError)


# ============================================================
# source_type
# ============================================================

class TestSourceType:
    """Markdown / Text 区分。"""

    def test_markdown_source_type(self) -> None:
        chunks = MarkdownAwareChunker().chunk("# 标题\n\n内容")
        assert chunks[0].metadata["source_type"] == "markdown"

    def test_plain_text_source_type(self) -> None:
        chunks = MarkdownAwareChunker().chunk("纯文本。没有标题符号。")
        assert chunks[0].metadata["source_type"] == "text"

    def test_chinese_text_source_type(self) -> None:
        chunks = MarkdownAwareChunker().chunk("这是中文纯文本，没有标题。")
        assert chunks[0].metadata["source_type"] == "text"
        assert chunks[0].metadata["heading_path"] == []

    def test_hash_without_space_not_a_heading(self) -> None:
        """`#xxx`（无空格）不算 markdown 标题。"""
        chunks = MarkdownAwareChunker().chunk("#xxx 这不是标题\n\n正文")
        assert chunks[0].metadata["source_type"] == "text"


# ============================================================
# 默认 Chunking 策略
# ============================================================

class TestDefaultChunker:
    """便捷函数 default_chunker()。"""

    def test_default_chunker_is_markdown_aware(self) -> None:
        assert isinstance(default_chunker(), MarkdownAwareChunker)

    def test_default_chunk_size_is_800(self) -> None:
        assert default_chunker().chunk_size == 800

    def test_default_chunk_overlap_is_100(self) -> None:
        assert default_chunker().chunk_overlap == 100


# ============================================================
# Fixtures
# ============================================================

class TestWithFixtures:
    """tests/fixtures/chunking/ 下的真实文件。"""

    def test_short_md_yields_single_chunk(self) -> None:
        path = FIXTURES_DIR / "short.md"
        assert path.exists(), f"fixture missing: {path}"
        text = path.read_text(encoding="utf-8")
        chunks = MarkdownAwareChunker().chunk(text)
        # 短文档 < chunk_size，单 chunk
        assert len(chunks) == 1
        assert "短文档" in chunks[0].content
        assert "测试" in chunks[0].content

    def test_hierarchy_md_heading_paths_correct(self) -> None:
        path = FIXTURES_DIR / "hierarchy.md"
        assert path.exists(), f"fixture missing: {path}"
        text = path.read_text(encoding="utf-8")
        # 用小 chunk_size 强制每个段落独立成 chunk（每个 draft size < 50）
        chunks = MarkdownAwareChunker(chunk_size=50, chunk_overlap=10).chunk(text)
        # 至少 4 个 chunk（前置段 + 3 个 ## 子段）
        assert len(chunks) >= 3
        # 验证 metadata
        all_paths = [tuple(c.metadata.get("heading_path", [])) for c in chunks]
        assert ("采购入库",) in all_paths
        assert ("采购入库", "操作步骤", "扫描收货通知") in all_paths
        assert ("采购入库", "操作步骤", "扫描物料") in all_paths
        assert ("采购入库", "异常处理") in all_paths

    def test_hierarchy_md_child_chunks_preserve_parent_heading(self) -> None:
        path = FIXTURES_DIR / "hierarchy.md"
        text = path.read_text(encoding="utf-8")
        chunks = MarkdownAwareChunker().chunk(text)
        # 所有 chunk 都应包含 "# 采购入库"（父级标题保留）
        for c in chunks:
            assert "# 采购入库" in c.content

    def test_long_md_split_into_multiple_chunks(self) -> None:
        path = FIXTURES_DIR / "long.md"
        assert path.exists(), f"fixture missing: {path}"
        text = path.read_text(encoding="utf-8")
        chunks = MarkdownAwareChunker(chunk_size=300, chunk_overlap=50).chunk(text)
        # 默认 800 可能仍然单 chunk；用 300 强制切分
        assert len(chunks) >= 2
        # 所有 chunk 的 chunk_index 都连续
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


# ============================================================
# 集成（Chunking + Parser 协作的轻量验证）
# ============================================================

class TestIntegrationWithParser:
    """Parser → Chunker 串联的最小集成（不依赖 Parser 也能跑）。"""

    def test_chunker_accepts_parser_output(self) -> None:
        """Chunker 应能直接消费 MarkdownParser 的输出（纯文本）。"""
        # MarkdownParser 解析后的纯文本
        parsed_text = (
            "# 采购入库\n\n"
            "采购入库用于处理采购订单到货。\n\n"
            "## 操作步骤\n\n"
            "1. 打开采购入库\n"
            "2. 扫描收货通知\n"
            "3. 扫描物料\n"
        )
        # 用小 chunk_size 强制切分
        chunks = MarkdownAwareChunker(chunk_size=50, chunk_overlap=10).chunk(parsed_text)
        assert len(chunks) >= 2
        # heading_path 全部正确
        for c in chunks:
            assert c.metadata["source_type"] == "markdown"
            assert "采购入库" in c.metadata["heading_path"]