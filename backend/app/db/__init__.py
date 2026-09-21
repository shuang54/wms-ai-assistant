"""Database Layer（Phase 3.2）。

本包负责：
    - SQLAlchemy 2.x ORM 基类（base.Base）
    - Engine / Session 工厂（session.get_engine / get_db）
    - pgvector extension 启用 + ORM 表创建（init_db.init_db）
    - ORM Models 导出（models.KnowledgeDocument / KnowledgeChunk）

业务表（knowledge_document / knowledge_chunk）在 models/ 中定义。
"""
from __future__ import annotations

from backend.app.db.base import Base
from backend.app.db.models import get_all_models
from backend.app.db.session import (
    get_db,
    get_engine,
    get_session_factory,
    ping_database,
    reset_engine_cache,
)

# 确保导入 db 包时所有 ORM Model 已注册到 Base.metadata。
# 这一行**不要删除**——它是 Base.metadata.create_all() 自动建表的前提。
_ = get_all_models()

__all__ = [
    "Base",
    "get_db",
    "get_engine",
    "get_session_factory",
    "ping_database",
    "reset_engine_cache",
    "get_all_models",
]