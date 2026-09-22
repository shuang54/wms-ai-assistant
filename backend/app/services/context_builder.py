"""RAG Context Builder（Phase 3.5.4）。

职责：
    VectorSearchResult[] → 结构化的、来源可追溯的纯文本 Context
                          ↓
                          喂给 LLM 的 user message 内容

设计要点：

- 单一职责：仅做"序列化 + 截断"，不调任何外部依赖（DB / Embedding / LLM）。
- 可测试：纯函数 / 字符串拼接逻辑，无 I/O；测试只需构造 VectorSearchResult 列表。
- 来源优先：每个片段的来源 / 章节 / 相似度 必须在 Chunk 内容上方，
  即使内容被截断也不允许丢失来源行（保证前端 / 审计可追溯）。
- 截断策略：
    - 当拼接所有片段会超过 max_context_chars 时，**从后往前**丢弃片段，
      直到总长度 ≤ 预算（保留高相关的）。
    - 最后一个被保留的片段允许做"末尾字符截断"，但仅裁剪 content 字段，
      不会裁掉该片段的来源 / 章节 / 相似度 头部行。
    - 永不从 chunk 中间裁剪（避免破坏语义）；若超限空间确实无法再塞入任何
      一个完整片段的 header，则删除该片段。
- 输出格式：

    [知识片段 1]
    来源：chunk_id=123, document_id=10, chunk_index=0
    章节：采购入库 > 操作步骤
    相似度：0.9100
    内容：
    实际内容...

    [知识片段 2]
    ...
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from backend.app.services.vector_search_service import VectorSearchResult

logger = logging.getLogger(__name__)

__all__ = [
    "ContextBuilder",
    "ContextBuildResult",
]


# ============================================================
# 默认常量（任务书 §七 建议 12000；常量集中便于测试断言）
# ============================================================

DEFAULT_MAX_CONTEXT_CHARS: Final[int] = 12000


# ============================================================
# Context 格式化常量（便于测试与替换）
# ============================================================

SECTION_HEADER_FMT: Final[str] = "[知识片段 {index}]"

# 头部字段模板（用于 \n 拼接）
FIELD_SOURCE_FMT: Final[str] = "来源：chunk_id={chunk_id}, document_id={document_id}, chunk_index={chunk_index}"
FIELD_SECTION_FMT: Final[str] = "章节：{section}"
FIELD_SIMILARITY_FMT: Final[str] = "相似度：{similarity:.4f}"
FIELD_CONTENT_HEADER: Final[str] = "内容："

# 片段之间的分隔
SECTION_SEPARATOR: Final[str] = "\n\n"

# 内容被截断时的省略标记（仅在 content 字段被尾部裁剪时出现）
TRUNCATION_MARK: Final[str] = " …(已截断)"


# ============================================================
# 结果结构（DTO）
# ============================================================

@dataclass(frozen=True)
class ContextBuildResult:
    """Context Builder 的输出。

    Attributes:
        text:         最终拼好的 Context 字符串（喂给 LLM）。
        used_chunks:  实际纳入 Context 的片段（按原始顺序，与 text 中出现的顺序一致）。
        total_chars:  text 的字符长度（UTF-8/Unicode 字符数）。
        truncated:    是否发生截断（片段级丢弃 / content 末尾裁剪 / 两者皆有 均算 True）。
        dropped_count: 因长度限制被丢弃的片段数量（仅"片段级丢弃"，不含 content 裁剪）。
    """

    text: str
    used_chunks: tuple[VectorSearchResult, ...]
    total_chars: int
    truncated: bool
    dropped_count: int


# ============================================================
# Builder
# ============================================================

class ContextBuilder:
    """RAG Context Builder（Phase 3.5.4）。

    构造期注入 max_context_chars；不可变；线程安全。
    """

    def __init__(self, *, max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS) -> None:
        if not isinstance(max_context_chars, int) or isinstance(max_context_chars, bool):
            raise TypeError(
                f"max_context_chars 必须是 int（当前: {type(max_context_chars).__name__}）"
            )
        if max_context_chars <= 0:
            raise ValueError(
                f"max_context_chars 必须为正整数（当前: {max_context_chars}）"
            )
        self._max_chars = max_context_chars

    @property
    def max_context_chars(self) -> int:
        return self._max_chars

    # ---------- public API ----------

    def build(
        self, results: list[VectorSearchResult]
    ) -> ContextBuildResult:
        """将 VectorSearchResult 序列化为结构化 Context 文本。

        Pipeline:
            1. 逐个片段构造带 header 的完整段落（按 similarity 降序，与输入顺序一致）
            2. 若总长度 ≤ max_context_chars → 整体返回
            3. 若超限 → 从后往前丢弃完整片段；最后一个保留片段允许 content 尾部裁剪

        Args:
            results: Vector Search 结果列表（已按 cosine distance 升序 = similarity 降序）。

        Returns:
            ContextBuildResult；
            空输入 → text="" / used_chunks=() / truncated=False / dropped_count=0。
        """
        if not results:
            return ContextBuildResult(
                text="",
                used_chunks=(),
                total_chars=0,
                truncated=False,
                dropped_count=0,
            )

        budget = self._max_chars
        kept_blocks: list[str] = []
        kept_results: list[VectorSearchResult] = []
        current_len = 0
        truncated = False
        dropped_count = 0

        for idx, r in enumerate(results, start=1):
            full_block = self._render_block(idx, r)
            sep_len = len(SECTION_SEPARATOR) if kept_blocks else 0
            prospective_len = current_len + sep_len + len(full_block)

            if prospective_len <= budget:
                kept_blocks.append(full_block)
                kept_results.append(r)
                current_len = prospective_len
                continue

            # ---- 超限：尝试保留当前片段但裁剪其 content ----
            # 估计仅保留当前片段 header（含 SECTION_SEPARATOR 与 SECTION_HEADER）
            # 之后还有多少预算给 content
            header_overhead = (
                sep_len
                + len(SECTION_HEADER_FMT.format(index=idx))
                + 1  # 标题后换行
                + len(FIELD_SOURCE_FMT.format(
                    chunk_id=r.chunk_id,
                    document_id=r.document_id,
                    chunk_index=r.chunk_index,
                ))
                + 1
                + len(FIELD_SECTION_FMT.format(section=self._extract_section(r)))
                + 1
                + len(FIELD_SIMILARITY_FMT.format(similarity=r.similarity))
                + 1
                + len(FIELD_CONTENT_HEADER)
                + 1
                + len(TRUNCATION_MARK)
            )
            content_budget = budget - current_len - header_overhead
            if content_budget <= 0:
                # 当前片段连 header + 标记都装不下 → 丢弃当前及之后所有
                truncated = True
                dropped_count += len(results) - idx + 1
                break

            truncated_content = r.content[:content_budget] + TRUNCATION_MARK
            truncated_block = self._render_block_with_content(idx, r, truncated_content)
            kept_blocks.append(truncated_block)
            kept_results.append(r)
            current_len += sep_len + len(truncated_block)
            truncated = True

            # 当前片段之后的所有片段都丢弃
            remaining = len(results) - idx
            if remaining > 0:
                dropped_count += remaining
            break

        text = SECTION_SEPARATOR.join(kept_blocks) if kept_blocks else ""
        return ContextBuildResult(
            text=text,
            used_chunks=tuple(kept_results),
            total_chars=len(text),
            truncated=truncated,
            dropped_count=dropped_count,
        )

    # ---------- internals ----------

    @staticmethod
    def _extract_section(result: VectorSearchResult) -> str:
        """从 result.metadata 中提取 heading_path；缺失时返回空。"""
        meta = result.metadata or {}
        heading_path = meta.get("heading_path") or meta.get("section_path")
        if isinstance(heading_path, list) and heading_path:
            return " > ".join(str(h) for h in heading_path)
        if isinstance(heading_path, str) and heading_path:
            return heading_path
        return ""

    def _render_block(self, index: int, result: VectorSearchResult) -> str:
        """渲染一个完整片段（含所有 header 行 + content）。"""
        return self._render_block_with_content(index, result, result.content)

    def _render_block_with_content(
        self,
        index: int,
        result: VectorSearchResult,
        content: str,
    ) -> str:
        """渲染一个片段（header 完整 + 给定 content）。"""
        section = self._extract_section(result)
        return "\n".join(
            [
                SECTION_HEADER_FMT.format(index=index),
                FIELD_SOURCE_FMT.format(
                    chunk_id=result.chunk_id,
                    document_id=result.document_id,
                    chunk_index=result.chunk_index,
                ),
                FIELD_SECTION_FMT.format(section=section),
                FIELD_SIMILARITY_FMT.format(similarity=result.similarity),
                FIELD_CONTENT_HEADER,
                content,
            ]
        )