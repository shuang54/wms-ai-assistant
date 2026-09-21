"""KnowledgeChunk ORM Model（Phase 3.2）。

设计原则：
    - 一个 Document 切分为多个 Chunk
    - (document_id, chunk_index) 唯一约束：保证同一个文档内 chunk 顺序唯一
    - `embedding` 使用 pgvector `Vector(N)`，dimension 由 EmbeddingSettings 提供
    - `content` / `content_hash` 用于 Phase 3.3+ ingestion 去重 / 跳过已嵌入内容
    - **本阶段不创建向量索引**（索引要求 dimension 已稳定；Phase 3.5+ 才加）
    - `meta_data` 与 `KnowledgeDocument.meta_data` 同理（避开 SQLAlchemy `metadata` 保留名）

Chunk 与 Document 的删除策略：
    - `document_id` FK 设置 `ondelete="CASCADE"`
    - 删除 Document → DB 端自动级联删除所有 Chunk
    - ORM 层 `cascade="all, delete-orphan"` 提供 Python 端一致性
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.config import settings
from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.knowledge_document import KnowledgeDocument


class KnowledgeChunk(Base):
    """知识库文档分块表。

    每行存储一个文档切片 + 对应 embedding（Phase 3.3+ 才会真正填入向量）。
    """

    __tablename__ = "knowledge_chunk"

    # ---- 主键 ----
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="Chunk 主键",
    )

    # ---- 外键 ----
    document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_document.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属文档主键（FK → knowledge_document.id, ON DELETE CASCADE）",
    )

    # ---- 业务字段 ----
    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Chunk 在原文中的顺序（从 0 开始）",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Chunk 文本内容",
    )
    content_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Chunk 内容 SHA-256，用于去重",
    )
    token_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Chunk 的 Token 数（Phase 3.3+ 计算；本阶段允许为 NULL）",
    )
    # ---- Embedding（pgvector）----
    # dimension 由 settings.embedding.dimension 控制（环境变量 EMBEDDING_DIMENSION）。
    # 当前阶段**不创建**任何向量索引（HNSW / IVFFlat）——dimension 未稳定。
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.embedding.dimension),
        nullable=True,
        comment=(
            f"Chunk Embedding 向量（pgvector vector({settings.embedding.dimension})）。"
            "Phase 3.3+ 由 Embedding Service 写入；本阶段允许为 NULL。"
        ),
    )

    # ---- JSONB 元数据 ----
    meta_data: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        comment="业务自定义元数据：page / section / department 等",
    )

    # ---- 时间戳 ----
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="创建时间",
    )

    # ---- Relationship ----
    document: Mapped["KnowledgeDocument"] = relationship(
        "KnowledgeDocument",
        back_populates="chunks",
        lazy="joined",  # 访问 chunk.document 通常 1:1，默认 joined 即可
    )

    # ---- Table-level 约束/索引 ----
    __table_args__ = (
        # 核心约束：同一个文档内 chunk_index 唯一
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_knowledge_chunk_document_id_chunk_index",
        ),
        # content_hash 索引：去重 / 跳过已嵌入
        Index("ix_knowledge_chunk_content_hash", "content_hash"),
        # created_at 索引：增量 ingestion
        Index("ix_knowledge_chunk_created_at", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<KnowledgeChunk id={self.id} document_id={self.document_id} "
            f"chunk_index={self.chunk_index}>"
        )


__all__ = ["KnowledgeChunk"]