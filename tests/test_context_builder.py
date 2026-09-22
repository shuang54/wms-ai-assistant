"""Context Builder 测试（Phase 3.5.4）。

覆盖（对应 Phase 3.5.4 任务书 §十五）：

    1. 单 chunk：包含 content + 来源 + heading_path
    2. 多 chunk：顺序与 Vector Search 返回顺序一致（按 similarity 降序）
    3. Context 长度限制：超过 max_context_chars 时截断，从后往前丢弃，
       最后一个允许尾部 content 裁剪，并保留 header
    4. 空输入：返回空 Context（不是异常）

Context Builder 是纯函数，无 I/O、无 LLM 调用、无 DB 调用。
所有测试无需 RUN_DB_TESTS 或 RUN_LLM_TESTS。
"""
from __future__ import annotations

import pytest

from backend.app.services.context_builder import (
    ContextBuildResult,
    ContextBuilder,
    DEFAULT_MAX_CONTEXT_CHARS,
)
from backend.app.services.vector_search_service import VectorSearchResult


# ============================================================
# Fixtures / Helpers
# ============================================================

def make_chunk(
    *,
    chunk_id: int,
    document_id: int = 1,
    chunk_index: int = 0,
    content: str = "示例内容",
    similarity: float = 0.9,
    metadata: dict | None = None,
) -> VectorSearchResult:
    return VectorSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        chunk_index=chunk_index,
        content=content,
        distance=1.0 - similarity,
        similarity=similarity,
        metadata=metadata if metadata is not None else {},
    )


# ============================================================
# 1. 单 chunk
# ============================================================

class TestSingleChunk:
    def test_single_chunk_includes_content_and_source(self) -> None:
        """单 chunk：必须包含 content + 来源 + heading_path（若 metadata 中有）。"""
        chunk = make_chunk(
            chunk_id=101,
            document_id=10,
            chunk_index=3,
            content="采购入库：收货 → 质检 → 上架",
            similarity=0.95,
            metadata={"heading_path": ["采购入库", "操作步骤"]},
        )
        builder = ContextBuilder()
        result = builder.build([chunk])

        assert isinstance(result, ContextBuildResult)
        assert result.total_chars == len(result.text)
        assert "采购入库：收货 → 质检 → 上架" in result.text
        # 来源
        assert "chunk_id=101" in result.text
        assert "document_id=10" in result.text
        assert "chunk_index=3" in result.text
        # 章节（heading_path 用 > 拼接）
        assert "章节：采购入库 > 操作步骤" in result.text
        # 相似度
        assert "相似度：0.9500" in result.text
        # used_chunks / dropped
        assert result.used_chunks == (chunk,)
        assert result.truncated is False
        assert result.dropped_count == 0

    def test_single_chunk_without_heading_path(self) -> None:
        """无 heading_path 时：章节行依然存在但内容为空。"""
        chunk = make_chunk(chunk_id=1, content="abc", metadata={})
        builder = ContextBuilder()
        result = builder.build([chunk])

        assert "章节：" in result.text
        assert "abc" in result.text


# ============================================================
# 2. 多 chunk 顺序
# ============================================================

class TestMultiChunkOrder:
    def test_multi_chunk_preserves_similarity_descending_order(self) -> None:
        """多 chunk：按输入顺序拼接（输入顺序即为 similarity 降序）。"""
        chunks = [
            make_chunk(chunk_id=1, similarity=0.95, content="最相似的内容"),
            make_chunk(chunk_id=2, similarity=0.85, content="次相似的内容"),
            make_chunk(chunk_id=3, similarity=0.75, content="再次相似的内容"),
        ]
        builder = ContextBuilder()
        result = builder.build(chunks)

        # 顺序断言：索引位置
        idx_first = result.text.index("最相似的内容")
        idx_second = result.text.index("次相似的内容")
        idx_third = result.text.index("再次相似的内容")
        assert idx_first < idx_second < idx_third

        # 三个 chunk 全部纳入
        assert len(result.used_chunks) == 3
        assert result.truncated is False
        assert result.dropped_count == 0


# ============================================================
# 3. Context 长度限制
# ============================================================

class TestContextLength:
    def test_truncation_drops_from_end_keeps_highest_relevance(self) -> None:
        """总长度超限时：从后往前丢弃，保留高相关的（输入顺序前部）。"""
        # 每个 chunk 内容 100 字
        chunks = [
            make_chunk(chunk_id=i, similarity=0.95 - i * 0.05, content="X" * 100)
            for i in range(1, 6)
        ]
        # 限长 350（够 3 个，4-5 个会被丢弃）
        builder = ContextBuilder(max_context_chars=350)
        result = builder.build(chunks)

        assert result.truncated is True
        # 至少前几个被保留
        assert len(result.used_chunks) >= 2
        # 第 1 个 chunk（最相似）必须在
        assert result.used_chunks[0].chunk_id == 1
        # 末尾的 chunk 被丢弃（不进 used_chunks）
        assert result.dropped_count >= 1
        # 总长度不超过预算（且最后保留的 chunk 可能有尾截断）
        assert result.total_chars <= 350

    def test_truncation_keeps_header_when_content_tail_clipped(self) -> None:
        """最后一个被保留 chunk 的 content 被尾部裁剪，但 header 完整。"""
        # 制造 1 个大 chunk，需要尾部裁剪
        big_content = "Y" * 5000
        chunk = make_chunk(
            chunk_id=42,
            document_id=7,
            chunk_index=0,
            content=big_content,
            metadata={"heading_path": ["采购入库"]},
        )
        builder = ContextBuilder(max_context_chars=200)  # 极小，强制尾部裁剪
        result = builder.build([chunk])

        assert result.truncated is True
        # header 必须完整
        assert "chunk_id=42" in result.text
        assert "document_id=7" in result.text
        assert "chunk_index=0" in result.text
        assert "章节：采购入库" in result.text
        # 截断标记存在
        assert "已截断" in result.text
        # content 不是完整 5000 Y
        assert result.text.count("Y") < 5000

    def test_truncation_drop_chunks_when_cannot_fit_header(self) -> None:
        """当 budget 连第一个 chunk 的 header + 任意 content 都装不下时：
        该 chunk 及之后所有都被丢弃。"""
        chunk = make_chunk(
            chunk_id=99,
            content="Z" * 10000,
            metadata={"heading_path": ["采购入库"]},
        )
        # budget = 50：远小于任何 chunk 的 header 开销
        builder = ContextBuilder(max_context_chars=50)
        result = builder.build([chunk])

        assert result.truncated is True
        assert result.text == ""
        assert result.used_chunks == ()
        assert result.dropped_count == 1

    def test_max_context_chars_validation(self) -> None:
        """max_context_chars 校验：非 int / 负数 / 0 → ValueError / TypeError。"""
        with pytest.raises(TypeError):
            ContextBuilder(max_context_chars="100")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            ContextBuilder(max_context_chars=True)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            ContextBuilder(max_context_chars=0)
        with pytest.raises(ValueError):
            ContextBuilder(max_context_chars=-1)


# ============================================================
# 4. 空输入
# ============================================================

class TestEmptyInput:
    def test_empty_list_returns_empty_context(self) -> None:
        builder = ContextBuilder()
        result = builder.build([])

        assert isinstance(result, ContextBuildResult)
        assert result.text == ""
        assert result.used_chunks == ()
        assert result.total_chars == 0
        assert result.truncated is False
        assert result.dropped_count == 0


# ============================================================
# 默认值
# ============================================================

class TestDefaults:
    def test_default_max_context_chars_is_12000(self) -> None:
        """默认上限 12000（任务书 §七 建议值）。"""
        assert DEFAULT_MAX_CONTEXT_CHARS == 12000

    def test_builder_property_returns_max(self) -> None:
        builder = ContextBuilder(max_context_chars=9999)
        assert builder.max_context_chars == 9999


__all__ = [
    "TestSingleChunk",
    "TestMultiChunkOrder",
    "TestContextLength",
    "TestEmptyInput",
    "TestDefaults",
]