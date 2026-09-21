"""KnowledgeDocument ORM Model（Phase 3.2）。

表结构设计原则：
    - 单文件单表，便于早期演进（避免按模块过度拆分）
    - 仅做 Phase 3.2 必须字段，不引入 ingestion pipeline 专用列
    - `metadata` 是 SQLAlchemy `Base` 内置属性 → Python 属性使用 `meta_data`，列名仍为 `metadata`
    - `content_hash` 唯一约束用于后续去重
    - `status` 字符串占位（Phase 3.3+ ingestion pipeline 才会丰富取值）
    - `created_at` / `updated_at` 使用 server_default / server_onupdate（避免 Python 端时区漂移）

暂未实现：
    - ingestion 状态机
    - 多租户 / RBAC
    - 文件存储位置（OSS / S3）
    - Alembic migration（Phase 3.5+ 引入）
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base

if TYPE_CHECKING:
    from backend.app.db.models.knowledge_chunk import KnowledgeChunk


class KnowledgeDocument(Base):
    """知识库文档主表。

    一篇原始文档（如 inbound.md / WMS 操作手册.pdf）对应一行记录。
    文档内容通过切分后存到 `KnowledgeChunk`（1:N 关系）。
    """

    __tablename__ = "knowledge_document"

    # ---- 主键 ----
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="文档主键，自增 BigInteger",
    )

    # ---- 业务字段 ----
    title: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        comment="文档标题（非空）",
    )
    file_name: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="原始文件名",
    )
    file_type: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        index=True,  # 单列索引：按文件类型筛选
        comment="文件类型：md / txt / pdf / docx 等",
    )
    source: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="文档来源：manual / wiki / confluence 等",
    )
    content_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        unique=True,  # 唯一约束：用于后续去重
        comment="文档内容 SHA-256，用于重复文档检测",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,  # 单列索引：按 ingestion 状态筛选
        comment="ingestion 状态：pending / processing / ready / failed",
    )

    # ---- JSONB 元数据 ----
    # SQLAlchemy 的 Base 已经有 `metadata` 属性，因此 Python 属性使用 `meta_data`，
    # 数据库列名保持 `metadata`（符合 docs/architecture.md §11 的命名约定）。
    meta_data: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        comment="业务自定义元数据（JSONB），如 department / warehouse / version",
    )

    # ---- 时间戳 ----
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="创建时间（DB 端时区时间）",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),  # 注意：SQLAlchemy onupdate 是 Python 端，DB 端可用 trigger
        comment="更新时间",
    )

    # ---- Relationship ----
    # 1:N → KnowledgeChunk
    # 删除 Document 时级联删除所有 Chunk（CASCADE 在 FK 处定义）
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        "KnowledgeChunk",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",  # 访问 doc.chunks 时自动加载，避免 N+1
    )

    # ---- Table-level 约束/索引 ----
    __table_args__ = (
        # created_at 单列索引：按时间范围查询
        Index("ix_knowledge_document_created_at", "created_at"),
        # updated_at 单列索引：增量 ingestion 用
        Index("ix_knowledge_document_updated_at", "updated_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<KnowledgeDocument id={self.id} title={self.title!r} status={self.status!r}>"


__all__ = ["KnowledgeDocument"]