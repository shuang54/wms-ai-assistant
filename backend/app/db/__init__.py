"""Database Layer（Phase 3.1）。

本包负责：
    - SQLAlchemy 2.x ORM 基类（base.Base）
    - Engine / Session 工厂（session.get_engine / get_db）
    - pgvector extension 启用（init_db.init_db）

业务表（knowledge_document / knowledge_chunk / conversation_* 等）
属于后续 Phase；本包当前**不定义任何业务表**。
"""
from __future__ import annotations

from backend.app.db.base import Base
from backend.app.db.session import (
    get_db,
    get_engine,
    get_session_factory,
    ping_database,
    reset_engine_cache,
)

__all__ = [
    "Base",
    "get_db",
    "get_engine",
    "get_session_factory",
    "ping_database",
    "reset_engine_cache",
]