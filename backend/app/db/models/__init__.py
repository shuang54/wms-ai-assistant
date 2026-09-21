"""ORM Models 包（Phase 3.2）。

本包**只**承载 SQLAlchemy ORM Model 的定义与导出，不放业务逻辑。
所有 Model 继承 `backend.app.db.base.Base`，由 `Base.metadata` 统一管理。

新增 Model 流程：
    1. 在本目录新建 `xxx.py`，定义 `class Xxx(Base)`，设置 `__tablename__`
    2. 在本文件 `__init__.py` 的 `_MODELS` 列表中添加该类
    3. `Base.metadata.create_all()` 会自动发现并创建对应表

暂未实现（按 Phase 计划）：
    - 业务表（conversation / user / role 等）— 后续 Phase
    - Embedding / RAG Service — Phase 3.3+
    - Alembic migration — Phase 3.5+
"""
from __future__ import annotations

# 顺序很关键：KnowledgeDocument 必须先于 KnowledgeChunk（虽然 SQLAlchemy 不强制，
# 但 IDE / 类型检查友好）
from backend.app.db.models.knowledge_chunk import KnowledgeChunk
from backend.app.db.models.knowledge_document import KnowledgeDocument

_MODELS: tuple[type, ...] = (KnowledgeDocument, KnowledgeChunk)


__all__ = [
    "KnowledgeDocument",
    "KnowledgeChunk",
]


def get_all_models() -> tuple[type, ...]:
    """返回所有已注册的 ORM Model 类（用于 `Base.metadata.create_all` 自动发现）。"""
    return _MODELS