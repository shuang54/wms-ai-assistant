"""SQLAlchemy 2.x Declarative Base（Phase 3.1）。

本文件只提供 ORM 基类，**不在此定义任何业务表**。
业务表（如 knowledge_document / knowledge_chunk）将在后续 Phase 定义。
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM Model 的基类。

    使用 SQLAlchemy 2.x 的 DeclarativeBase 风格，
    推荐搭配 `Mapped[...]` + `mapped_column(...)` 注解使用。

    Example（Phase 3.2+）:
        class KnowledgeDocument(Base):
            __tablename__ = "knowledge_document"
            id: Mapped[int] = mapped_column(primary_key=True)
            ...
    """


__all__ = ["Base"]