"""Knowledge ORM Model 集成测试（Phase 3.2）。

覆盖（**真实 PostgreSQL + pgvector**）：
    1. KnowledgeDocument 创建 / 字段持久化 / 必填校验
    2. KnowledgeChunk 创建 / 字段持久化
    3. Document → Chunk Relationship（document.chunks / chunk.document）
    4. UniqueConstraint(document_id, chunk_index)
    5. JSONB metadata 读写
    6. embedding 字段识别为 pgvector `vector(N)` 类型 + round-trip

环境变量：
    - RUN_DB_TESTS=true   显式启用（默认 skip）
    - DATABASE_URL        必须指向真实 PostgreSQL

隔离策略：
    - module fixture:    Base.metadata.create_all()（不 drop，保留结构供用户查看）
    - autouse fixture:   每个测试后 TRUNCATE 两表 + RESTART IDENTITY（保证幂等 / 不污染）
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from backend.app.db import reset_engine_cache
from backend.app.db.base import Base
from backend.app.db.models import KnowledgeChunk, KnowledgeDocument
from backend.app.db.session import get_engine, get_session_factory


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


_RUN_DB = _env_flag("RUN_DB_TESTS")

requires_db = pytest.mark.skipif(
    not _RUN_DB,
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module")
def engine():
    """module-level: 触发一次 Base.metadata.create_all()。

    Base.metadata.create_all 是幂等的——重复运行不会重建已存在的表。
    """
    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置；请配置后启用 RUN_DB_TESTS=1"

    # 确保 models 已注册（防御性 import）
    from backend.app.db import models  # noqa: F401

    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture
def db(engine):
    """function-level: 每个测试一个独立 Session，结束后关闭。"""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        # 默认提交（测试大多显式 commit；这里是兜底）
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _truncate_after_each(engine):
    """每个测试后清理 knowledge_* 表数据，保证测试隔离。

    TRUNCATE ... CASCADE 自动处理 FK；RESTART IDENTITY 重置主键序列。
    """
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE knowledge_chunk, knowledge_document "
                "RESTART IDENTITY CASCADE"
            )
        )


# ============================================================
# 1. KnowledgeDocument
# ============================================================

@requires_db
class TestKnowledgeDocument:
    """KnowledgeDocument 创建 / 字段 / 约束。"""

    def test_create_document_persists_fields(self, db: "sqlalchemy.orm.Session") -> None:  # type: ignore[name-defined]
        """插入 Document 并验证所有字段正确持久化。"""
        title = f"WMS 入库操作说明 {uuid.uuid4()}"
        doc = KnowledgeDocument(
            title=title,
            file_name="inbound.md",
            file_type="md",
            source="manual",
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        assert doc.id is not None and doc.id > 0
        assert doc.title == title
        assert doc.file_name == "inbound.md"
        assert doc.file_type == "md"
        assert doc.source == "manual"
        assert doc.status == "pending"  # 默认值
        assert doc.content_hash is None
        assert doc.meta_data is None
        assert doc.created_at is not None
        assert doc.updated_at is not None

    def test_title_required(self, db) -> None:
        """title=None 必须被 DB 拒绝（IntegrityError）。"""
        doc = KnowledgeDocument(title=None, file_name="x.md")  # type: ignore[arg-type]
        db.add(doc)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_content_hash_unique(self, db) -> None:
        """相同 content_hash 的两个 Document → 第二个被拒绝。"""
        h = uuid.uuid4().hex
        db.add(KnowledgeDocument(title=f"d1 {uuid.uuid4()}", content_hash=h))
        db.commit()

        db.add(KnowledgeDocument(title=f"d2 {uuid.uuid4()}", content_hash=h))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_status_filter_indexed(self, db) -> None:
        """file_type / status 列有索引（column 上声明的 index 会自动建索引）。"""
        insp = inspect(db.get_bind())
        indexes = insp.get_indexes("knowledge_document")
        index_cols = {tuple(sorted(idx["column_names"])) for idx in indexes}
        # file_type 列有 index=True
        assert ("file_type",) in index_cols
        assert ("status",) in index_cols


# ============================================================
# 2. KnowledgeChunk
# ============================================================

@requires_db
class TestKnowledgeChunk:
    """KnowledgeChunk 创建 / 字段。"""

    def _make_doc(self, db) -> KnowledgeDocument:
        doc = KnowledgeDocument(title=f"chunk fixture {uuid.uuid4()}")
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc

    def test_create_two_chunks_persists(self, db) -> None:
        """同一个 Document 下创建 chunk_index=0 和 1 均成功。"""
        doc = self._make_doc(db)
        c1 = KnowledgeChunk(
            document_id=doc.id,
            chunk_index=0,
            content="采购入库首先需要创建采购入库通知...",
        )
        c2 = KnowledgeChunk(
            document_id=doc.id,
            chunk_index=1,
            content="仓库人员使用 PDA 扫描物料...",
        )
        db.add_all([c1, c2])
        db.commit()
        db.refresh(c1)
        db.refresh(c2)

        assert c1.id is not None and c1.id > 0
        assert c2.id is not None and c2.id > 0
        assert c1.document_id == doc.id
        assert c2.document_id == doc.id
        assert c1.chunk_index == 0
        assert c2.chunk_index == 1
        assert "采购入库" in c1.content
        assert "PDA" in c2.content

    def test_chunk_token_count_nullable(self, db) -> None:
        """token_count 允许 NULL（Phase 3.3+ 才计算）。"""
        doc = self._make_doc(db)
        c = KnowledgeChunk(
            document_id=doc.id, chunk_index=0, content="x"
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        assert c.token_count is None


# ============================================================
# 3. Relationship (1:N)
# ============================================================

@requires_db
class TestRelationship:
    """Document ↔ Chunk 双向 relationship。"""

    def test_document_chunks_relationship(self, db) -> None:
        """document.chunks 返回该 doc 下所有 chunk，并按 chunk_index 排序可读。"""
        doc = KnowledgeDocument(title=f"rel {uuid.uuid4()}")
        db.add(doc)
        db.commit()

        db.add_all([
            KnowledgeChunk(document_id=doc.id, chunk_index=0, content="A"),
            KnowledgeChunk(document_id=doc.id, chunk_index=1, content="B"),
        ])
        db.commit()
        db.refresh(doc)

        assert len(doc.chunks) == 2
        indices = sorted(c.chunk_index for c in doc.chunks)
        assert indices == [0, 1]
        # lazy="selectin" 应已自动加载；不应触发额外 lazy load 警告

    def test_chunk_back_reference(self, db) -> None:
        """chunk.document 反向引用 doc。"""
        doc = KnowledgeDocument(title=f"backref {uuid.uuid4()}")
        db.add(doc)
        db.commit()

        c = KnowledgeChunk(document_id=doc.id, chunk_index=0, content="x")
        db.add(c)
        db.commit()

        assert c.document is not None
        assert c.document.id == doc.id
        assert c.document.title == doc.title


# ============================================================
# 4. UniqueConstraint(document_id, chunk_index)
# ============================================================

@requires_db
class TestUniqueConstraint:
    """(document_id, chunk_index) 唯一性。"""

    def test_duplicate_chunk_index_rejected(self, db) -> None:
        """同一 Document 下相同 chunk_index 第二次插入必须被拒绝。"""
        doc = KnowledgeDocument(title=f"uniq {uuid.uuid4()}")
        db.add(doc)
        db.commit()

        db.add(KnowledgeChunk(document_id=doc.id, chunk_index=0, content="first"))
        db.commit()

        # 同一个 (doc.id, chunk_index=0) 再次插入
        db.add(KnowledgeChunk(document_id=doc.id, chunk_index=0, content="second"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    def test_same_chunk_index_different_documents_allowed(self, db) -> None:
        """不同 Document 的 chunk_index=0 应允许并存。"""
        doc1 = KnowledgeDocument(title=f"d1 {uuid.uuid4()}")
        doc2 = KnowledgeDocument(title=f"d2 {uuid.uuid4()}")
        db.add_all([doc1, doc2])
        db.commit()

        db.add_all([
            KnowledgeChunk(document_id=doc1.id, chunk_index=0, content="A"),
            KnowledgeChunk(document_id=doc2.id, chunk_index=0, content="B"),
        ])
        db.commit()
        # 两条都成功


# ============================================================
# 5. JSONB metadata
# ============================================================

@requires_db
class TestJSONBMetadata:
    """JSONB metadata 读写。"""

    def test_document_metadata_roundtrip(self, db) -> None:
        """Document.meta_data 写入 dict 后能完整读回。"""
        meta = {"department": "warehouse", "language": "zh-CN"}
        doc = KnowledgeDocument(
            title=f"jsonb doc {uuid.uuid4()}",
            meta_data=meta,
        )
        db.add(doc)
        db.commit()
        doc_id = doc.id
        db.expire_all()  # 强制重新加载

        loaded = db.get(KnowledgeDocument, doc_id)
        assert loaded is not None
        assert loaded.meta_data == meta
        assert loaded.meta_data["department"] == "warehouse"
        assert loaded.meta_data["language"] == "zh-CN"

    def test_chunk_metadata_roundtrip(self, db) -> None:
        """Chunk.meta_data 写入 dict 后能完整读回。"""
        doc = KnowledgeDocument(title=f"jsonb chunk doc {uuid.uuid4()}")
        db.add(doc)
        db.commit()

        meta = {"page": 3, "section": "3.1 收货流程", "tags": ["inbound", "wms"]}
        c = KnowledgeChunk(
            document_id=doc.id,
            chunk_index=0,
            content="x",
            meta_data=meta,
        )
        db.add(c)
        db.commit()
        c_id = c.id
        db.expire_all()

        loaded = db.get(KnowledgeChunk, c_id)
        assert loaded is not None
        assert loaded.meta_data == meta
        assert loaded.meta_data["page"] == 3
        assert loaded.meta_data["tags"] == ["inbound", "wms"]


# ============================================================
# 6. pgvector embedding 字段
# ============================================================

@requires_db
class TestVectorColumn:
    """knowledge_chunk.embedding 字段类型与 round-trip。"""

    def test_embedding_column_type_is_vector(self, engine) -> None:
        """embedding 列在 PostgreSQL 中为 vector 类型，且 dimension 与 settings 一致。"""
        from backend.app.config import settings

        expected_dim = settings.embedding.dimension

        insp = inspect(engine)
        cols = {c["name"]: c for c in insp.get_columns("knowledge_chunk")}
        assert "embedding" in cols
        # SQLAlchemy 将 pgvector 类型表示为 Vector，inspect 返回 generic object
        col_type = cols["embedding"]["type"]
        # 类型对象的 Python 表示通常是 VECTOR(dim)；含 "VECTOR" 即可
        assert "VECTOR" in str(col_type).upper(), f"unexpected type: {col_type}"

        # 通过 pg_attribute / pg_type 双重确认（atttypmod = dimension）
        with engine.connect() as conn:
            dim_row = conn.execute(
                text(
                    """
                    SELECT a.atttypmod
                    FROM pg_attribute a
                    JOIN pg_class c ON a.attrelid = c.oid
                    WHERE c.relname = 'knowledge_chunk'
                      AND a.attname = 'embedding'
                      AND a.attnum > 0
                      AND NOT a.attisdropped
                    """
                )
            ).first()
        assert dim_row is not None
        # atttypmod = dimension（pgvector convention）
        assert dim_row[0] == expected_dim, (
            f"expected dim={expected_dim}, got atttypmod={dim_row[0]}"
        )

    def test_embedding_insert_and_reload(self, db) -> None:
        """插入一个维度匹配的向量，能 round-trip 回 Python list[float]。"""
        from backend.app.config import settings

        doc = KnowledgeDocument(title=f"vec {uuid.uuid4()}")
        db.add(doc)
        db.commit()

        dim = settings.embedding.dimension
        vec = [0.1 * i for i in range(dim)]
        chunk = KnowledgeChunk(
            document_id=doc.id,
            chunk_index=0,
            content="embedding test",
            embedding=vec,
        )
        db.add(chunk)
        db.commit()
        chunk_id = chunk.id
        db.expire_all()

        loaded = db.get(KnowledgeChunk, chunk_id)
        assert loaded is not None
        assert loaded.embedding is not None
        assert isinstance(loaded.embedding, list)
        assert len(loaded.embedding) == dim
        # 浮点精度允许微小误差
        assert abs(loaded.embedding[0] - 0.0) < 1e-6
        assert abs(loaded.embedding[dim - 1] - (0.1 * (dim - 1))) < 1e-3

    def test_embedding_null_allowed(self, db) -> None:
        """embedding 允许 NULL（本阶段不强制要求已嵌入）。"""
        doc = KnowledgeDocument(title=f"null vec {uuid.uuid4()}")
        db.add(doc)
        db.commit()

        chunk = KnowledgeChunk(
            document_id=doc.id, chunk_index=0, content="no embedding yet"
        )
        db.add(chunk)
        db.commit()
        assert chunk.embedding is None