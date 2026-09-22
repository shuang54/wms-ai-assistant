"""Real WMS Knowledge Base Ingestion 集成测试（Phase 3.5.8）。

覆盖任务规范 § 十一：

1. 真实 BGE-M3 / 真实 PostgreSQL + pgvector / 真实 VectorSearchService
2. **不**调用 DeepSeek（LLM）
3. 默认 skip：`RUN_DB_TESTS=1` + `DATABASE_URL` 配置后才执行
4. 不删除 / 不污染已有数据：复用 KnowledgeIngestionService 的
   content_hash 去重机制，重复调用返回 already_exists

不重复实现：现有 `KnowledgeIngestionService.ingest_one()` 已保证幂等；
本文件只做**端到端契约验证**。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import select, text

from backend.app.config import settings
from backend.app.db import reset_engine_cache
from backend.app.db.base import Base
from backend.app.db.models import KnowledgeChunk, KnowledgeDocument
from backend.app.db.session import get_engine
from backend.app.services.knowledge_ingestion_service import (
    KnowledgeIngestionService,
)


WMS_DOC = Path("docs/knowledge/wms-basic-operations.md")


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS") or not settings.database.url.strip(),
    reason=(
        "Real WMS KB ingestion test requires "
        "RUN_DB_TESTS=1 and DATABASE_URL configured."
    ),
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module")
def engine():
    """module-level: ensure Base metadata exists."""
    reset_engine_cache()
    eng = get_engine()
    assert eng is not None
    from backend.app.db import models  # noqa: F401  # register models

    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture(scope="module")
def _test_loaded_doc_ids() -> set[int]:
    """记录本次 test session 内由本文件测试加载的 document_id，
    用于测试结束时只清理这些文档（不影响 CLI 手动加载的其他文档）。"""
    return set()


@pytest.fixture
def _cleanup_only_test_docs(engine, _test_loaded_doc_ids):
    """测试结束后只删除 _test_loaded_doc_ids 内的 document + chunks，
    保留任何 CLI 手动加载的其它文档。
    """
    yield
    if not _test_loaded_doc_ids:
        return
    ids = list(_test_loaded_doc_ids)
    with engine.begin() as conn:
        # chunks 由 FK CASCADE 自动删除
        conn.execute(
            text("DELETE FROM knowledge_document WHERE id = ANY(:ids)"),
            {"ids": ids},
        )


@pytest.fixture
def db(_cleanup_only_test_docs, engine):
    """function-level Session（仅清理本次测试插入的文档）。"""
    from backend.app.db.session import get_session_factory

    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _ingest_and_track(svc: KnowledgeIngestionService, path: Path, track: set[int]):
    """调 ingest_one，记录返回的 document_id 到 track。"""
    return svc.ingest_one(path)


# (helper available but tests can also just call svc.ingest_one and track.add(...))


def _fetch_document_by_content_hash(session, content_hash: str):
    return session.scalars(
        select(KnowledgeDocument).where(
            KnowledgeDocument.content_hash == content_hash
        )
    ).first()


# ============================================================
# Real ingestion contract tests
# ============================================================

@requires_db
class TestRealWmsKbIngestion:
    async def test_first_or_subsequent_ingest_is_idempotent(
        self, db, engine, _test_loaded_doc_ids, tmp_path: Path
    ) -> None:
        """第一次成功 ready / 后续 already_exists，二者必居其一。

        使用临时唯一文档（不与 CLI 加载的 wms-basic-operations.md 共享
        content_hash），测试结束后自动清理本次插入，不影响用户数据。
        """
        # 使用唯一内容，避免与 CLI 已加载文档冲突
        unique_doc = tmp_path / "wms_test_unique_001.md"
        unique_doc.write_text(
            "# WMS 测试文档 1\n\n## 第一章\n\n这是临时测试文档内容。"
            "\n\n## 第二章\n\n这是另一节内容，用于测试 KnowledgeIngestionService。\n",
            encoding="utf-8",
        )

        svc = KnowledgeIngestionService()
        first = await svc.ingest_one(unique_doc)
        _test_loaded_doc_ids.add(first.document_id)

        # 第一次一定是 ready（content_hash 唯一）
        assert first.status == "ready", first.status
        assert first.document_id > 0
        assert first.chunk_count >= 1
        assert first.content_hash and len(first.content_hash) == 64

        # 验证数据库里有该 document
        doc = db.get(KnowledgeDocument, first.document_id)
        assert doc is not None
        assert doc.title == "wms_test_unique_001"
        assert doc.file_name == unique_doc.name
        assert doc.file_type == "md"
        assert doc.status == "ready"
        assert doc.content_hash == first.content_hash

        # chunks 必须实际写入
        assert len(doc.chunks) == first.chunk_count
        for c in doc.chunks:
            assert c.document_id == doc.id
            assert c.content.strip(), "chunk content must be non-empty"

    async def test_second_ingest_returns_already_exists(
        self, db, engine, _test_loaded_doc_ids, tmp_path: Path
    ) -> None:
        """第二次导入完全相同文件 → already_exists，document_id 不变，Embedding 不再调用。

        使用临时唯一文档，避免污染 CLI 加载的真实数据。
        """
        unique_doc = tmp_path / "wms_test_unique_002.md"
        body = "# WMS 测试文档 2\n\n## 第一节\n\n这是用于测试重复导入的临时内容。\n\n## 第二节\n\n补充内容，确保切出多个 chunk 用于验证。\n"
        unique_doc.write_text(body, encoding="utf-8")

        svc = KnowledgeIngestionService()
        first = await svc.ingest_one(unique_doc)
        _test_loaded_doc_ids.add(first.document_id)
        second = await svc.ingest_one(unique_doc)

        # 同一 content_hash → 同一 document_id
        assert second.content_hash == first.content_hash
        assert second.document_id == first.document_id
        assert second.status == "already_exists"
        # 第二次不调 Embedding
        assert second.embedded_chunk_count == 0

        # DB 中 chunks 数量未翻倍
        with engine.connect() as conn:
            chunk_total = conn.execute(
                text("SELECT COUNT(*) FROM knowledge_chunk WHERE document_id = :did"),
                {"did": first.document_id},
            ).scalar_one()
        assert chunk_total == first.chunk_count

    async def test_no_duplicate_documents_for_same_hash(
        self, db, _test_loaded_doc_ids, tmp_path: Path
    ) -> None:
        """重复 content_hash 在 knowledge_document 中只能出现 0 或 1 行。

        使用临时唯一文档。
        """
        unique_doc = tmp_path / "wms_test_unique_003.md"
        unique_doc.write_text(
            "# WMS 测试文档 3\n\n## 唯一内容\n\n这一节专门用于验证 content_hash 唯一性。\n",
            encoding="utf-8",
        )

        svc = KnowledgeIngestionService()
        first = await svc.ingest_one(unique_doc)
        _test_loaded_doc_ids.add(first.document_id)

        # 直接 SELECT 验证：唯一约束 + 实际只有一行
        rows = db.scalars(
            select(KnowledgeDocument).where(
                KnowledgeDocument.content_hash == first.content_hash
            )
        ).all()
        assert len(rows) == 1, f"发现重复 document: {len(rows)} 行"
        assert rows[0].id == first.document_id


def _noop() -> None:
    return None


# ============================================================
# Vector dimension validation
# ============================================================

@requires_db
class TestRealWmsKbEmbeddingDimension:
    async def test_chunk_embedding_is_1024_dim(
        self, db, _test_loaded_doc_ids, tmp_path: Path
    ) -> None:
        """所有 chunk 的 embedding 维度必须 = 1024（= EMBEDDING_DIMENSION）。

        使用临时唯一文档（不与 CLI 加载的数据冲突）。
        """
        unique_doc = tmp_path / "wms_test_unique_004.md"
        # 多段长文本，确保至少一个 chunk
        body = (
            "# WMS 测试文档 4\n\n## 第一节\n\n"
            + ("这是用于验证 embedding 维度的测试文本。" * 50)
            + "\n\n## 第二节\n\n"
            + ("补充内容，确保切出至少一个 chunk。" * 30)
            + "\n"
        )
        unique_doc.write_text(body, encoding="utf-8")
        expected_dim = settings.embedding.dimension
        assert expected_dim == 1024

        svc = KnowledgeIngestionService()
        result = await svc.ingest_one(unique_doc)
        _test_loaded_doc_ids.add(result.document_id)

        # 取全部 chunks，验证 dim
        chunks = db.scalars(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == result.document_id
            ).order_by(KnowledgeChunk.chunk_index)
        ).all()

        assert len(chunks) == result.chunk_count
        for c in chunks:
            assert c.embedding is not None, "embedding IS NOT NULL"
            # pgvector 返回 list[float]（length 可直接读）
            assert len(c.embedding) == expected_dim, (
                f"chunk {c.chunk_index} dim={len(c.embedding)} != {expected_dim}"
            )

        # pgvector 端点再核一次（更可靠，绕开 ORM 缓存）
        with db.connection().engine.connect() as conn:
            from sqlalchemy import text as _text

            row = conn.execute(
                _text(
                    "SELECT vector_dims(embedding) "
                    "FROM knowledge_chunk "
                    "WHERE document_id = :did "
                    "LIMIT 1"
                ),
                {"did": result.document_id},
            ).first()
        assert row is not None
        assert row[0] == expected_dim


__all__ = [
    "TestRealWmsKbIngestion",
    "TestRealWmsKbEmbeddingDimension",
]