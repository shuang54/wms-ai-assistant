"""Embedding 向量数据库 Roundtrip 测试（Phase 3.5.1.7）。

验证：1024 维向量写入 PostgreSQL pgvector `knowledge_chunk.embedding`
（Phase 3.5.1.7 迁移后为 vector(1024)）并成功读回、长度一致。

测试自建临时 Document / Chunk 数据，结束后级联清理，
**不污染正式知识库数据**。

默认 skip；仅当 RUN_DB_TESTS=1 且 DATABASE_URL 可达时执行。

前置条件：数据库已完成 vector(1536) → vector(1024) 迁移
（见 docs/decisions/embedding-dimension.md §6）。
"""
from __future__ import annotations

import os

import pytest

requires_db = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS", "").strip().lower() not in {"1", "true", "yes", "on"},
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable DB roundtrip tests",
)


def _db_ready() -> bool:
    from backend.app.config import settings

    return bool(settings.database.url.strip())


@requires_db
class TestEmbeddingVector1024Roundtrip:
    def teardown_method(self) -> None:
        from backend.app.db import reset_engine_cache

        reset_engine_cache()

    def test_roundtrip_1024_vector(self) -> None:
        if not _db_ready():
            pytest.skip("DATABASE_URL 未配置")

        from sqlalchemy import select, text
        from sqlalchemy.orm import Session

        from backend.app.config import settings
        from backend.app.db import reset_engine_cache
        from backend.app.db.base import Base
        from backend.app.db.models import knowledge_chunk  # noqa: F401
        from backend.app.db.models import knowledge_document  # noqa: F401
        from backend.app.db.models.knowledge_chunk import KnowledgeChunk
        from backend.app.db.models.knowledge_document import KnowledgeDocument
        from backend.app.db.session import get_engine

        # 本测试假设迁移已完成：ORM dimension 应解析为 1024
        assert settings.embedding.dimension == 1024, (
            "EMBEDDING_DIMENSION 应为 1024（迁移未完成或 .env 未更新）"
        )

        reset_engine_cache()
        engine = get_engine()
        assert engine is not None, "DATABASE_URL 未配置"

        # 幂等：只补缺失的表（已有表由外部迁移 SQL 处理）
        Base.metadata.create_all(bind=engine)

        vector = [0.001 * (i + 1) for i in range(1024)]

        # Session 上下文管理器退出时仅 close 不 commit，需显式开启事务块
        with Session(engine) as session, session.begin():
            before = session.execute(
                text("SELECT COUNT(*) FROM knowledge_chunk")
            ).scalar_one()

            doc = KnowledgeDocument(title="[TEST] embedding-dimension roundtrip 1024")
            session.add(doc)
            session.flush()
            session.add(
                KnowledgeChunk(
                    document_id=doc.id,
                    chunk_index=0,
                    content="测试 1024 维向量 roundtrip（Phase 3.5.1.7，测试数据）。",
                    embedding=vector,
                )
            )
            session.flush()
            doc_id = doc.id

        # 通过 ORM 读回：pgvector.sqlalchemy 的 result processor 会把底层
        # 文本表示 '[0.001,0.002,...]' 转成 list[float]；
        # 裸 SQL scalar 拿到的是字符串，list(row) 会错拆成单个字符。
        with Session(engine) as session:
            chunk = session.scalars(
                select(KnowledgeChunk).where(
                    KnowledgeChunk.document_id == doc_id,
                    KnowledgeChunk.chunk_index == 0,
                )
            ).one()
            assert chunk.embedding is not None
            stored = list(chunk.embedding)
            assert len(stored) == 1024
            assert all(abs(a - b) < 1e-6 for a, b in zip(stored, vector))

        # 清理：删 Document 级联删 Chunk，row count 恢复
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM knowledge_document WHERE id = :d"), {"d": doc_id}
            )
            after = conn.execute(
                text("SELECT COUNT(*) FROM knowledge_chunk")
            ).scalar_one()

        assert after == before, "roundtrip 测试未清理干净（row count 不一致）"


__all__ = ["TestEmbeddingVector1024Roundtrip"]
