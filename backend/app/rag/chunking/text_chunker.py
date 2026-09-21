"""Text Chunking Engine（Phase 3.4）。

策略：**Heading-aware + Size Limit**

    1. Markdown 标题（# / ## / ...）作为逻辑边界
    2. 每个段落（空行分隔）独立成 unit
    3. 每个 unit 携带 `heading_levels: list[tuple[int, str]]`（级别, 标题）
    4. 合并到 chunk 时 prefix 按**原始级别**还原为 # 形式
    5. 超长段落 → 按句子（中英文标点）切分
    6. 句子仍超长 → 按字符切分（兜底）
    7. `chunk_overlap` 仅用于超长段落的内部细分，**不**破坏标题结构

未实现（后续 Phase）：
    - Recursive splitter
    - Token-aware chunking
    - Semantic / embedding-aware chunking
    - 向量数据库 / LLM / DB 写入
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from backend.app.rag.chunking.models import (
    InvalidChunkOverlapError,
    InvalidChunkSizeError,
    TextChunk,
)

__all__ = [
    "TextChunker",
    "MarkdownAwareChunker",
    "default_chunker",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_CHUNK_OVERLAP",
]


# ============================================================
# 常量
# ============================================================

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100

# 标题识别：`# ` `## ` ... `###### `（标题后必须有非空白字符）
_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*)$")

# 段落分隔：连续空行（允许中间有空白字符）
_PARA_SPLIT_RE = re.compile(r"\n\s*\n")

# 句子边界：中英文句末标点；保留标点本身
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s*")


# ============================================================
# 类型别名
# ============================================================

# 内部用：保留 (级别, 标题文字) 配对，避免降级丢级
HeadingLevel = tuple[int, str]


# ============================================================
# 抽象接口
# ============================================================

class TextChunker(ABC):
    """所有 Chunking 策略的抽象基类。

    接口语义：
        输入：纯文本（Parser 阶段的输出）
        输出：TextChunk 列表（chunk_index 从 0 开始连续递增）
        **不**触碰：DB / LLM / Embedding / Vector Search
    """

    @abstractmethod
    def chunk(self, text: str) -> list[TextChunk]:
        """将纯文本切分为多个 Chunk。

        Args:
            text: 纯文本字符串（允许为空 / 纯空白 → 返回 `[]`）。

        Returns:
            `TextChunk` 列表，按出现顺序排列；
            首个元素 `chunk_index == 0`，相邻元素 `chunk_index` 相差 1。
        """


# ============================================================
# 内部草稿（不暴露给外部）
# ============================================================

@dataclass
class _ChunkDraft:
    """中间态：完整内容 + 元数据。"""

    content: str
    heading_levels: list[HeadingLevel] = field(default_factory=list)
    source_type: str = "text"

    @property
    def heading_path(self) -> list[str]:
        """对外的 heading_path（仅标题文字，去掉级别）。"""
        return [title for _level, title in self.heading_levels]


# ============================================================
# Markdown-aware + Size Limit 实现
# ============================================================

class MarkdownAwareChunker(TextChunker):
    """Heading-aware + Size Limit Chunking 策略。

    行为示例（chunk_size=800, chunk_overlap=100）：

    ```markdown
    # 采购入库

    采购入库用于处理采购订单到货。

    ## 操作步骤

    1. 打开采购入库
    2. 扫描收货通知
    3. 扫描物料
    ```

    将产生 2 个 Chunk：

        Chunk 0:
            # 采购入库

            采购入库用于处理采购订单到货。

        Chunk 1:
            # 采购入库
            ## 操作步骤

            1. 打开采购入库
            2. 扫描收货通知
            3. 扫描物料

    Chunk 1 的内容**显式包含父级 `# 采购入库`**（按原始级别还原），
    metadata.heading_path = ["采购入库", "操作步骤"]。
    """

    def __init__(
        self,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        self._validate(chunk_size, chunk_overlap)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    # ------------------------------------------------------------
    # 参数验证
    # ------------------------------------------------------------

    @staticmethod
    def _validate(chunk_size: Any, chunk_overlap: Any) -> None:
        # 排除 bool（bool 是 int 的子类）
        if not isinstance(chunk_size, int) or isinstance(chunk_size, bool):
            raise InvalidChunkSizeError(
                f"chunk_size 必须是 int（当前: {type(chunk_size).__name__}）"
            )
        if chunk_size <= 0:
            raise InvalidChunkSizeError(
                f"chunk_size 必须 > 0（当前: {chunk_size}）"
            )
        if not isinstance(chunk_overlap, int) or isinstance(chunk_overlap, bool):
            raise InvalidChunkOverlapError(
                f"chunk_overlap 必须是 int（当前: {type(chunk_overlap).__name__}）"
            )
        if chunk_overlap < 0:
            raise InvalidChunkOverlapError(
                f"chunk_overlap 必须 >= 0（当前: {chunk_overlap}）"
            )
        if chunk_overlap >= chunk_size:
            raise InvalidChunkOverlapError(
                f"chunk_overlap ({chunk_overlap}) 必须 < chunk_size ({chunk_size})"
            )

    # ------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------

    def chunk(self, text: str) -> list[TextChunk]:
        """切分纯文本 → TextChunk 列表。

        流程：
            1. 空白 / 空 → `[]`
            2. 检测 source_type（markdown / text）
            3. 解析 Markdown → [(heading_levels, paragraph), ...]
            4. 阶段 1：materialize drafts（处理超长段）
            5. 阶段 2：合并 drafts 为最终 chunk（chunk_size 控制）
            6. 包装为 TextChunk，重新编号 chunk_index
        """
        text = (text or "").strip()
        if not text:
            return []

        source_type = self._detect_source_type(text)
        items = self._parse_items(text)
        drafts = self._materialize_drafts(items, source_type)
        merged = self._combine_drafts(drafts)

        return [
            TextChunk(
                content=d.content,
                chunk_index=i,
                metadata={
                    "source_type": d.source_type,
                    "heading_path": d.heading_path,
                },
            )
            for i, d in enumerate(merged)
        ]

    # ------------------------------------------------------------
    # source_type 检测
    # ------------------------------------------------------------

    @staticmethod
    def _detect_source_type(text: str) -> str:
        """检测文本是否含合法的 Markdown 标题。

        只要发现一行 `# ` / `## ` / ... 开头即判定为 markdown；
        否则视为纯文本。
        """
        for line in text.split("\n"):
            if _HEADING_RE.match(line.strip()):
                return "markdown"
        return "text"

    # ------------------------------------------------------------
    # 阶段 0：解析为 items（保留原始级别）
    # ------------------------------------------------------------

    @staticmethod
    def _parse_items(text: str) -> list[tuple[list[HeadingLevel], str]]:
        """解析文本为 `[(heading_levels, paragraph_text), ...]`。

        - `heading_levels: list[tuple[int, str]]` — 保留 (level, title) 配对
        - 段落分隔：连续空行（`\\n\\s*\\n`）
        - 标题嵌套：按级别截断栈（保留 level < 新标题级别的栈项）
        - 空段（只有标题没有内容）跳过
        """
        items: list[tuple[list[HeadingLevel], str]] = []
        heading_stack: list[HeadingLevel] = []
        current_lines: list[str] = []

        def flush() -> None:
            nonlocal current_lines
            para_text = "\n".join(current_lines).strip()
            current_lines = []
            if not para_text:
                return
            for sub in _PARA_SPLIT_RE.split(para_text):
                sub = sub.strip()
                if sub:
                    items.append((list(heading_stack), sub))

        for line in text.split("\n"):
            stripped = line.strip()
            m = _HEADING_RE.match(stripped)
            if m:
                flush()
                level = len(m.group(1))
                title = m.group(2).strip()
                # 截断栈到 level-1：保留所有 lv < level 的项
                heading_stack = [(lv, t) for lv, t in heading_stack if lv < level]
                heading_stack.append((level, title))
            else:
                current_lines.append(line)

        flush()
        return items

    # ------------------------------------------------------------
    # Heading prefix 构造（按原始级别）
    # ------------------------------------------------------------

    @staticmethod
    def _build_prefix(heading_levels: list[HeadingLevel]) -> str:
        """从 heading_levels 构造 markdown 风格前缀（按原始级别）。

        - `[]` → `""`
        - `[(1, "A"), (2, "B")]` → `"# A\\n## B\\n\\n"`
        - `[(2, "B")]` → `"## B\\n\\n"`（不降级为 H1）
        """
        if not heading_levels:
            return ""
        lines = [("#" * level) + " " + title for level, title in heading_levels]
        return "\n".join(lines) + "\n\n"

    # ------------------------------------------------------------
    # 阶段 1：把 items 转 drafts（处理超长段）
    # ------------------------------------------------------------

    def _materialize_drafts(
        self,
        items: list[tuple[list[HeadingLevel], str]],
        source_type: str,
    ) -> list[_ChunkDraft]:
        """把 `(heading_levels, paragraph)` 转 `_ChunkDraft` 列表。

        对超长段落（`len(para) > target_size`）调用 `_split_long_paragraph`。
        """
        drafts: list[_ChunkDraft] = []
        for heading_levels, para in items:
            prefix = self._build_prefix(heading_levels)
            target = self.chunk_size - len(prefix)
            # 极端保护：prefix 长度 ≥ chunk_size（极少见）
            if target <= 0:
                drafts.append(_ChunkDraft(
                    content=prefix + para,
                    heading_levels=list(heading_levels),
                    source_type=source_type,
                ))
                continue

            if len(para) > target:
                drafts.extend(
                    self._split_long_paragraph(
                        heading_levels, para, target, source_type,
                    )
                )
            else:
                drafts.append(_ChunkDraft(
                    content=prefix + para,
                    heading_levels=list(heading_levels),
                    source_type=source_type,
                ))
        return drafts

    def _split_long_paragraph(
        self,
        heading_levels: list[HeadingLevel],
        para: str,
        target_size: int,
        source_type: str,
    ) -> list[_ChunkDraft]:
        """单段超长细分：先按句子，再字符兜底；保留 heading prefix（按级别）。"""
        prefix = self._build_prefix(heading_levels)

        # 极端：prefix 已经超过 chunk_size → 直接字符切 para
        if len(prefix) >= self.chunk_size:
            return self._char_split(
                heading_levels, para, max(self.chunk_size - 1, 1), source_type,
            )

        # 句子级累积（带 overlap）
        bodies: list[str] = []
        sentences = self._split_sentences(para)
        current = ""
        for sent in sentences:
            if not current:
                current = sent
                continue
            # +1 防止"a."+"b"被合并时多计
            if len(current) + len(sent) + 1 > target_size:
                bodies.append(current)
                # overlap：保留尾部 chunk_overlap 字符
                if self.chunk_overlap > 0 and len(current) > self.chunk_overlap:
                    current = current[-self.chunk_overlap :] + sent
                else:
                    current = sent
            else:
                current = current + sent

        if current:
            bodies.append(current)

        # 兜底：单句超过 target_size 时按字符切
        result: list[_ChunkDraft] = []
        for body in bodies:
            if len(body) > target_size:
                result.extend(
                    self._char_split(heading_levels, body, target_size, source_type)
                )
            else:
                result.append(_ChunkDraft(
                    content=prefix + body,
                    heading_levels=list(heading_levels),
                    source_type=source_type,
                ))
        return result

    def _char_split(
        self,
        heading_levels: list[HeadingLevel],
        body: str,
        chunk_size: int,
        source_type: str,
    ) -> list[_ChunkDraft]:
        """纯字符切分（兜底）。每段都带 heading prefix（按级别）。"""
        prefix = self._build_prefix(heading_levels)
        step = max(chunk_size, 1)
        return [
            _ChunkDraft(
                content=prefix + body[i : i + step],
                heading_levels=list(heading_levels),
                source_type=source_type,
            )
            for i in range(0, len(body), step)
        ]

    # ------------------------------------------------------------
    # 句子切分
    # ------------------------------------------------------------

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """按中英文句末标点切分（保留标点本身）。"""
        parts = _SENT_SPLIT_RE.split(text)
        return [p for p in parts if p and p.strip()]

    # ------------------------------------------------------------
    # 阶段 2：合并 drafts 为最终 chunks（chunk_size 控制）
    # ------------------------------------------------------------

    def _combine_drafts(self, drafts: list[_ChunkDraft]) -> list[_ChunkDraft]:
        """按 `chunk_size` 累积 drafts 为最终 chunk。

        段落间用 `\\n\\n` 分隔；
        不会**为了合并**而引入 overlap（overlap 只用在超长段内部细分）。
        """
        if not drafts:
            return []
        result: list[_ChunkDraft] = []
        current: list[_ChunkDraft] = []
        current_size = 0
        for d in drafts:
            d_size = len(d.content)
            added = d_size + (2 if current else 0)  # 段间 "\n\n"
            if current and current_size + added > self.chunk_size:
                result.append(self._merge_drafts(current))
                current = [d]
                current_size = d_size
            else:
                current.append(d)
                current_size += added
        if current:
            result.append(self._merge_drafts(current))
        return result

    @staticmethod
    def _merge_drafts(drafts: list[_ChunkDraft]) -> _ChunkDraft:
        """合并多个 drafts 为一个。

        heading_levels 取**最后一个** draft 的（嵌套最深 / 最具体）。
        """
        content = "\n\n".join(d.content for d in drafts)
        return _ChunkDraft(
            content=content,
            heading_levels=list(drafts[-1].heading_levels),
            source_type=drafts[-1].source_type,
        )


# ============================================================
# 便捷函数
# ============================================================

def default_chunker() -> MarkdownAwareChunker:
    """返回默认 Chunking 策略实例（chunk_size=800, chunk_overlap=100）。"""
    return MarkdownAwareChunker()