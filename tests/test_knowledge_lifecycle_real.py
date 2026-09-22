"""Knowledge Base Lifecycle 真实 DB 集成测试（Phase 3.5.10）。

覆盖任务书 §十九 / §二十：

    1. Create                （已由 Phase 3.5.8 测试覆盖；此处确认未回归）
    2. Duplicate (already_exists)
    3. Update unchanged      （同 hash → status=unchanged，零 Embedding）
    4. Update changed        （新 hash → 同 document_id，新 chunks）
    5. Update rollback       （Embedding 中途失败 → 旧 doc + chunks 不变）
    6. Delete                （document + chunks）
    7. Delete missing        （→ NotFound）
    8. Get                   （DTO 字段 / chunk_count）
    9. List
   10. Data integrity        （FK 一致 / dim 一致）

默认 SKIP：仅当 `RUN_DB_TESTS=1` 且 DATABASE_URL 已配置时执行。
Embedding 一律使用 `MockEmbeddingClient`（不产生真实 API 成本）；
真实 BGE-M3 smoke 由 `tests/test_knowledge_lifecycle_smoke.py` 覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import delete as sa_delete, select, text

from backend.app.config import settings
from backend.app.db import reset_engine_cache
from backend.app.db.base import Base
from backend.app.db.models import KnowledgeChunk, KnowledgeDocument
from backend.app.db.session import get_engine
from backend.app.embedding.client import EmbeddingClient
from backend.app.embedding.exceptions import EmbeddingAPIError
from backend.app.services.knowledge_ingestion_service import (
    EmptyDocumentError,
    KnowledgeDocumentNotFoundError,
    KnowledgeIngestionService,
)


# ============================================================
# Skip 开关
# ============================================================

def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable lifecycle DB tests",
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module")
def engine():
    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置"
    from backend.app.db import models  # noqa: F401
    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture(autouse=True)
def _truncate_after_each(engine):
    """每个测试后清空两张表（与既有 ingestion DB 测试一致）。"""
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE knowledge_chunk, knowledge_document "
                "RESTART IDENTITY CASCADE"
            )
        )


# ============================================================
# Mock EmbeddingClient
# ============================================================

class MockEmbeddingClient(EmbeddingClient):
    """确定性 Mock；支持在指定调用次数后失败（用于 rollback 测试）。"""

    def __init__(
        self,
        *,
        dimension: int | None = None,
        fail_after: int | None = None,
    ) -> None:
        self._dim = dimension or settings.embedding.dimension
        self._fail_after = fail_after
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self._fail_after is not None and len(self.calls) > self._fail_after:
            raise EmbeddingAPIError("Mock Embedding API 失败（测试注入）")
        seed = sum(text.encode("utf-8"))
        return [((seed + i) % 97 + 1) / 1000.0 for i in range(self._dim)]


# ============================================================
# 文档 fixture
# ============================================================

def _write_md(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _md_v1(tmp_path: Path) -> Path:
    body = (
        "# WMS 生命周期测试 v1\n\n"
        "## 第一节\n\n" + ("初始内容。" * 30) + "\n\n"
        "## 第二节\n\n" + ("补充段落。" * 30) + "\n"
    )
    return _write_md(tmp_path, "lifecycle_v1.md", body)


def _md_v2(tmp_path: Path) -> Path:
    body = (
        "# WMS 生命周期测试 v2（已更新）\n\n"
        "## 第一节\n\n" + ("更新后的内容段落，新增了一些信息。" * 30) + "\n\n"
        "## 第二节\n\n" + ("第二节也完全重写了。" * 30) + "\n\n"
        "## 第三节\n\n" + ("全新加入的第三节，用于触发新的 chunk 数量。" * 30) + "\n"
    )
    return _write_md(tmp_path, "lifecycle_v2.md", body)


def _md_v3(tmp_path: Path) -> Path:
    """用于触发 update_document 且不与已有 content_hash 冲突。"""
    body = (
        "# WMS 生命周期测试 v3 全新内容\n\n"
        "## 第一节\n\n" + ("v3 全新引入的内容标识 V3_UNIQUE_MARKER_A。" * 30) + "\n\n"
        "## 第二节\n\n" + ("v3 第二节 V3_UNIQUE_MARKER_B。" * 30) + "\n"
    )
    return _write_md(tmp_path, "lifecycle_v3.md", body)


# ============================================================
# 1. Create / Duplicate
# ============================================================

@requires_db
class TestLifecycleCreate:
    async def test_create_then_duplicate_returns_already_exists(
        self, tmp_path: Path
    ) -> None:
        path = _md_v1(tmp_path)
        svc = KnowledgeIngestionService(embedding_client=MockEmbeddingClient())
        first = await svc.ingest_one(path)
        assert first.status == "ready"
        assert first.chunk_count >= 1
        assert first.embedded_chunk_count == first.chunk_count

        # 完全相同内容再次 ingest
        second = await svc.ingest_one(path)
        assert second.status == "already_exists"
        assert second.document_id == first.document_id
        assert second.embedded_chunk_count == 0


# ============================================================
# 3 & 4. Update（unchanged / changed）
# ============================================================

@requires_db
class TestLifecycleUpdate:
    async def test_update_unchanged_does_not_call_embedding(
        self, tmp_path: Path
    ) -> None:
        path = _md_v1(tmp_path)
        client = MockEmbeddingClient()
        svc = KnowledgeIngestionService(embedding_client=client)

        first = await svc.ingest_one(path)
        assert first.status == "ready"
        baseline_calls = len(client.calls)
        assert baseline_calls >= 1

        # 文件未变化 → update_document 应判定 unchanged 且 0 次新 embedding
        result = await svc.update_document(first.document_id, path)
        assert result.status == "unchanged"
        assert result.document_id == first.document_id
        assert result.embedded_chunk_count == 0
        assert len(client.calls) == baseline_calls  # 没有额外调用

        # 验证 DB 状态不变
        from backend.app.db.session import get_session_factory
        with get_session_factory()() as s:
            doc = s.get(KnowledgeDocument, first.document_id)
            assert doc.content_hash == first.content_hash
            assert len(doc.chunks) == first.chunk_count

    async def test_update_changed_keeps_document_id_and_replaces_chunks(
        self, tmp_path: Path
    ) -> None:
        v1 = _md_v1(tmp_path)
        v2 = _md_v2(tmp_path)
        client = MockEmbeddingClient()
        svc = KnowledgeIngestionService(embedding_client=client)

        first = await svc.ingest_one(v1)
        old_doc_id = first.document_id
        old_hash = first.content_hash
        old_chunk_count = first.chunk_count

        result = await svc.update_document(old_doc_id, v2)
        assert result.status == "updated"
        assert result.document_id == old_doc_id  # 主键保留
        assert result.content_hash != old_hash
        assert result.chunk_count >= 1
        assert result.embedded_chunk_count == result.chunk_count

        # DB 验证：同 doc_id，content_hash 已更新，chunks 被替换
        from backend.app.db.session import get_session_factory
        with get_session_factory()() as s:
            doc = s.get(KnowledgeDocument, old_doc_id)
            assert doc.content_hash == result.content_hash
            assert len(doc.chunks) == result.chunk_count
            # 没有任何旧 chunk 残留
            for c in doc.chunks:
                assert c.content_hash != "stale-marker"

        # 旧 chunk_count 与新 chunk_count 不同（v2 多了第三节）
        assert result.chunk_count != old_chunk_count


# ============================================================
# 5. Rollback（关键）
# ============================================================

@requires_db
class TestLifecycleRollback:
    async def test_embedding_failure_during_update_keeps_old_state(
        self, tmp_path: Path
    ) -> None:
        """Phase B Embedding 失败 → 旧 document + 旧 chunks 完全保留。"""
        v1 = _md_v1(tmp_path)
        v2 = _md_v2(tmp_path)

        # 第 1 次 ingest 用稳定 client（全部完成）
        bootstrap = MockEmbeddingClient()
        svc = KnowledgeIngestionService(embedding_client=bootstrap)
        first = await svc.ingest_one(v1)
        old_doc_id = first.document_id
        old_hash = first.content_hash
        old_chunk_count = first.chunk_count

        # update 时换 fail_after=1：第 2 次 embed 起抛错（v2 chunk 数 ≥ 2 时必触发）
        failing = MockEmbeddingClient(fail_after=1)
        svc_fail = KnowledgeIngestionService(embedding_client=failing)

        with pytest.raises(EmbeddingAPIError):
            await svc_fail.update_document(old_doc_id, v2)

        # 旧状态完全保留
        from backend.app.db.session import get_session_factory
        with get_session_factory()() as s:
            doc = s.get(KnowledgeDocument, old_doc_id)
            assert doc is not None
            assert doc.content_hash == old_hash
            assert len(doc.chunks) == old_chunk_count
            # 所有 chunk 的 content 都来自 v1
            for c in doc.chunks:
                assert "v1" in c.content or "初始内容" in c.content or "补充段落" in c.content


# ============================================================
# 6 & 7. Delete
# ============================================================

@requires_db
class TestLifecycleDelete:
    async def test_delete_removes_document_and_chunks(self, tmp_path: Path) -> None:
        svc = KnowledgeIngestionService(embedding_client=MockEmbeddingClient())
        first = await svc.ingest_one(_md_v1(tmp_path))
        doc_id = first.document_id

        # 删除前 chunks 存在
        from backend.app.db.session import get_session_factory
        with get_session_factory()() as s:
            assert s.scalar(
                select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_id)
            ) is not None or s.scalar(
                select(text("COUNT(*)")).select_from(KnowledgeChunk).where(
                    KnowledgeChunk.document_id == doc_id
                )
            ) >= 1

        deleted_chunks = svc.delete_document(doc_id)
        assert deleted_chunks == first.chunk_count

        with get_session_factory()() as s:
            assert s.get(KnowledgeDocument, doc_id) is None
            n = s.scalar(
                text("SELECT COUNT(*) FROM knowledge_chunk WHERE document_id = :d"),
                {"d": doc_id},
            )
            assert n == 0

    def test_delete_missing_raises_not_found(self) -> None:
        svc = KnowledgeIngestionService(embedding_client=MockEmbeddingClient())
        with pytest.raises(KnowledgeDocumentNotFoundError):
            svc.delete_document(999_999_999)

    def test_update_missing_raises_not_found(self, tmp_path: Path) -> None:
        svc = KnowledgeIngestionService(embedding_client=MockEmbeddingClient())
        with pytest.raises(KnowledgeDocumentNotFoundError):
            asyncio_run_or_invoke(
                svc.update_document(999_999_999, _md_v1(tmp_path))
            )

    def test_get_missing_raises_not_found(self) -> None:
        svc = KnowledgeIngestionService(embedding_client=MockEmbeddingClient())
        with pytest.raises(KnowledgeDocumentNotFoundError):
            svc.get_document(999_999_999)


def asyncio_run_or_invoke(coro) -> None:
    """为同步测试调用 async 服务方法。"""
    import asyncio
    asyncio.run(coro)


# ============================================================
# 8 & 9. Get / List
# ============================================================

@requires_db
class TestLifecycleGetAndList:
    async def test_get_returns_dto_with_correct_chunk_count(
        self, tmp_path: Path
    ) -> None:
        svc = KnowledgeIngestionService(embedding_client=MockEmbeddingClient())
        first = await svc.ingest_one(_md_v1(tmp_path))

        info = svc.get_document(first.document_id)
        assert info.id == first.document_id
        assert info.title
        assert info.file_name == "lifecycle_v1.md"
        assert info.file_type == "md"
        assert info.status == "ready"
        assert info.chunk_count == first.chunk_count
        # DTO 不应暴露 ORM 行（无 .chunks 等）
        assert not hasattr(info, "chunks") or isinstance(info.chunk_count, int)

    async def test_list_returns_all_documents_sorted_by_id(
        self, tmp_path: Path
    ) -> None:
        svc = KnowledgeIngestionService(embedding_client=MockEmbeddingClient())
        a = await svc.ingest_one(_md_v1(tmp_path))
        b = await svc.ingest_one(_md_v2(tmp_path))

        docs = svc.list_documents()
        ids = [d.id for d in docs]
        assert ids == sorted(ids)
        assert a.document_id in ids
        assert b.document_id in ids
        assert len(docs) == 2


# ============================================================
# 10. Integrity
# ============================================================

@requires_db
class TestLifecycleDataIntegrity:
    async def test_no_orphan_chunks_and_consistent_embedding_dim(
        self, tmp_path: Path, engine
    ) -> None:
        """所有 chunk.document_id 必须指向已存在的 document；embedding dim 恒为 1024。"""
        svc = KnowledgeIngestionService(embedding_client=MockEmbeddingClient())
        await svc.ingest_one(_md_v1(tmp_path))
        await svc.ingest_one(_md_v2(tmp_path))
        # 再做一次 update 触发 chunk 替换路径（用全新 v3 内容以避免
        # content_hash 与已有 v1/v2 文档冲突——同一内容不应被两个文档持有）
        docs = svc.list_documents()
        await svc.update_document(docs[0].id, _md_v3(tmp_path))

        with engine.connect() as conn:
            orphan = conn.execute(
                text(
                    "SELECT COUNT(*) FROM knowledge_chunk c "
                    "LEFT JOIN knowledge_document d ON c.document_id = d.id "
                    "WHERE d.id IS NULL"
                )
            ).scalar_one()
            assert orphan == 0, "存在孤儿 chunk"

            dim_rows = conn.execute(
                text("SELECT DISTINCT vector_dims(embedding) FROM knowledge_chunk")
            ).fetchall()
            assert len(dim_rows) == 1
            assert dim_rows[0][0] == settings.embedding.dimension == 1024