"""Knowledge Ingestion Service 测试（Phase 3.5.2）。

覆盖（对应 Phase 3.5.2 任务书 §二十一）：

    1. TXT 导入成功（DB）
    2. Markdown 导入成功，heading_path / metadata 正确（DB）
    3. 多 Chunk，chunk_index 连续（DB）
    4. Embedding 维度 1024 成功 / 维度不匹配失败且库无脏数据
    5. 重复文档 already_exists，Embedding 不重复调用（DB）
    6. Embedding API Error → 数据库零写入（DB）
    7. Parser Error（不支持的扩展名 / 文件不存在）
    8. 空文档：不调 Embedding、明确报错
    9. 数据库 Roundtrip：len(embedding) == 1024（DB）

Embedding 一律使用 MockEmbeddingClient（不产生真实 API 成本）；
真实 Smoke 由 tests/test_embedding_real.py 覆盖（本文件不新增真实调用）。

环境变量：
    - RUN_DB_TESTS=true   显式启用 DB 集成用例（默认 skip）
    - DATABASE_URL        必须指向真实 PostgreSQL（vector(1024) 已迁移）
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select, text

from backend.app.config import settings
from backend.app.db import reset_engine_cache
from backend.app.db.base import Base
from backend.app.db.models import KnowledgeChunk, KnowledgeDocument
from backend.app.db.session import get_engine
from backend.app.embedding.client import EmbeddingClient
from backend.app.embedding.exceptions import EmbeddingAPIError, EmbeddingDimensionError
from backend.app.rag.parsers.base import (
    DocumentNotFoundError,
    UnsupportedDocumentTypeError,
)
from backend.app.services.knowledge_ingestion_service import (
    EmptyDocumentError,
    KnowledgeIngestionDatabaseError,
    KnowledgeIngestionError,
    KnowledgeIngestionService,
)

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not _env_flag("RUN_DB_TESTS"),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable ingestion DB tests",
)


# ============================================================
# Fixtures（与 test_knowledge_models.py 相同的隔离策略）
# ============================================================

@pytest.fixture(scope="module")
def engine():
    """module-level: 触发一次 Base.metadata.create_all()（幂等）。"""
    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置；请配置后启用 RUN_DB_TESTS=1"

    from backend.app.db import models  # noqa: F401  # 确保模型已注册

    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture
def _truncate_after_each(engine):
    """使用 db 的测试结束后清理 knowledge_* 表数据（仅对 DB 用例生效，
    纯单元测试不触发 engine 初始化）。"""
    yield
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE knowledge_chunk, knowledge_document "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def db(_truncate_after_each, engine):
    """function-level: 每个测试一个独立 Session，结束后关闭。"""
    from backend.app.db.session import get_session_factory

    factory = get_session_factory()
    session = factory()
    try:
        yield session
        try:
            session.commit()
        except Exception:
            session.rollback()
            raise
    finally:
        session.close()


# ============================================================
# Mock / helper
# ============================================================

class MockEmbeddingClient(EmbeddingClient):
    """确定性 Mock：记录调用；可选在第 N 次调用后抛指定异常。

    向量由 (文本字节和 + 下标) 决定，保证稳定可复现，
    不涉及任何真实网络调用。
    """

    def __init__(
        self,
        *,
        dimension: int | None = None,
        fail_after: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self._dimension = (
            dimension if dimension is not None else settings.embedding.dimension
        )
        self._fail_after = fail_after
        self._error = error if error is not None else EmbeddingAPIError(
            "Mock Embedding API 失败（测试注入）"
        )
        self.calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self._fail_after is not None and len(self.calls) > self._fail_after:
            raise self._error
        seed = sum(text.encode("utf-8"))
        return [((seed + i) % 97 + 1) / 1000.0 for i in range(self._dimension)]


def _fake_session_factory() -> MagicMock:
    """无重复文档的假 Session 工厂（纯单元测试用，不连库）。"""
    session = MagicMock(name="fake_session")
    # with factory() as s: → s 即 session 本身
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    # select(KnowledgeDocument).where(...).first() → None（无重复）
    session.scalars.return_value.first.return_value = None
    return session


def _long_markdown(tmp_path: Path) -> Path:
    """生成足够长的 Markdown（6 节 × ~400 字），保证切出 >= 3 个 Chunk。"""
    sections = "\n\n".join(
        f"## 第{i}节\n\n{'测试内容占位。' * 80}"
        for i in range(1, 7)
    )
    doc = f"# 长文档标题\n\n{sections}\n"
    path = tmp_path / "long_multi_chunk.md"
    path.write_text(doc, encoding="utf-8")
    return path


# ============================================================
# 纯单元（无 DB）
# ============================================================

class TestParserErrors:
    """Parser 阶段失败：不触碰 Embedding、不触碰 DB。"""

    async def test_unsupported_extension_rejected(self, tmp_path: Path) -> None:
        pdf = tmp_path / "spec.pdf"
        pdf.write_text("not really a pdf", encoding="utf-8")

        client = MockEmbeddingClient()
        svc = KnowledgeIngestionService(
            embedding_client=client,
            session_factory=_fake_session_factory(),
        )
        with pytest.raises(UnsupportedDocumentTypeError):
            await svc.ingest_one(pdf)
        assert client.calls == []

    async def test_missing_file_raises_not_found(self, tmp_path: Path) -> None:
        client = MockEmbeddingClient()
        svc = KnowledgeIngestionService(
            embedding_client=client,
            session_factory=_fake_session_factory(),
        )
        with pytest.raises(DocumentNotFoundError):
            await svc.ingest_one(tmp_path / "no_such_file.md")
        assert client.calls == []


class TestEmptyDocument:
    """空文档：不调 Embedding、不写库、明确报错。"""

    async def test_empty_document_rejected(self) -> None:
        empty = FIXTURES / "empty.txt"
        assert empty.exists(), "fixture empty.txt 缺失"

        client = MockEmbeddingClient()
        svc = KnowledgeIngestionService(
            embedding_client=client,
            session_factory=_fake_session_factory(),
        )
        with pytest.raises(EmptyDocumentError):
            await svc.ingest_one(empty)
        assert client.calls == []

    async def test_whitespace_only_document_rejected(self, tmp_path: Path) -> None:
        ws = tmp_path / "whitespace_only.txt"
        ws.write_text("   \n\n\t \n  ", encoding="utf-8")

        client = MockEmbeddingClient()
        svc = KnowledgeIngestionService(
            embedding_client=client,
            session_factory=_fake_session_factory(),
        )
        with pytest.raises(EmptyDocumentError):
            await svc.ingest_one(ws)
        assert client.calls == []


class TestEmbeddingErrors:
    """Embedding 阶段失败：数据库零写入。"""

    async def test_embedding_api_error_leaves_db_untouched(
        self, tmp_path: Path
    ) -> None:
        client = MockEmbeddingClient(fail_after=1)  # 第 2 次调用起抛 API 错误
        fake_session = _fake_session_factory()
        svc = KnowledgeIngestionService(
            embedding_client=client,
            session_factory=lambda: fake_session,
        )

        with pytest.raises(EmbeddingAPIError):
            await svc.ingest_one(_long_markdown(tmp_path))

        assert len(client.calls) == 2
        fake_session.add.assert_not_called()  # 未写库

    async def test_dimension_mismatch_rejected_before_write(
        self, tmp_path: Path
    ) -> None:
        # Mock 返回与配置不符的维度（如 1536）
        client = MockEmbeddingClient(
            dimension=settings.embedding.dimension + 512,
        )
        fake_session = _fake_session_factory()
        svc = KnowledgeIngestionService(
            embedding_client=client,
            session_factory=lambda: fake_session,
        )

        with pytest.raises(EmbeddingDimensionError):
            await svc.ingest_one(FIXTURES / "sample_wms.txt")

        fake_session.add.assert_not_called()  # 不截断、不补零、不写库


class TestErrorHierarchy:
    def test_ingestion_errors_subclass_base(self) -> None:
        assert issubclass(EmptyDocumentError, KnowledgeIngestionError)
        assert issubclass(KnowledgeIngestionDatabaseError, KnowledgeIngestionError)


# ============================================================
# DB 集成（RUN_DB_TESTS=1 + DATABASE_URL）
# ============================================================

@requires_db
class TestTxtIngestion:
    async def test_txt_full_pipeline_success(self, db) -> None:  # type: ignore[name-defined]
        """TXT → Parser → Chunk → Embedding(1024) → DB 全链路成功。"""
        client = MockEmbeddingClient()
        svc = KnowledgeIngestionService(embedding_client=client)

        result = await svc.ingest_one(FIXTURES / "sample_wms.txt")

        assert result.status == "ready"
        assert result.file_name == "sample_wms.txt"
        assert result.chunk_count >= 1
        assert result.embedded_chunk_count == result.chunk_count
        assert len(client.calls) == result.chunk_count
        assert result.content_hash

        doc = db.get(KnowledgeDocument, result.document_id)
        assert doc is not None
        assert doc.title == "sample_wms"
        assert doc.file_type == "txt"
        assert doc.source == "local"
        assert doc.status == "ready"
        assert doc.content_hash == result.content_hash

        chunks = sorted(doc.chunks, key=lambda c: c.chunk_index)
        assert len(chunks) == result.chunk_count
        assert all(c.embedding is not None for c in chunks)


@requires_db
class TestMarkdownIngestion:
    async def test_md_metadata_preserved(self, db) -> None:  # type: ignore[name-defined]
        """Markdown 导入：source_type / heading_path 正确写入 meta_data。"""
        client = MockEmbeddingClient()
        svc = KnowledgeIngestionService(embedding_client=client)

        result = await svc.ingest_one(FIXTURES / "sample_wms.md")
        assert result.status == "ready"
        assert result.chunk_count >= 2  # fixture 足够长，保证多 Chunk

        doc = db.get(KnowledgeDocument, result.document_id)
        assert doc is not None
        assert doc.file_type == "md"
        assert doc.meta_data == {"source_type": "md"}

        chunks = sorted(doc.chunks, key=lambda c: c.chunk_index)
        assert len(chunks) == result.chunk_count
        assert all(
            c.meta_data.get("source_type") == "markdown" for c in chunks
        )
        # heading_path 至少在某个 Chunk 中保留了二级标题
        assert any(
            "操作步骤" in (c.meta_data.get("heading_path") or [])
            for c in chunks
        )
        assert len(client.calls) == result.chunk_count


@requires_db
class TestMultiChunk:
    async def test_multi_chunk_continuous_index(self, db, tmp_path: Path) -> None:  # type: ignore[name-defined]
        """长文档切多 Chunk：chunk_index 0..N-1 连续，每个 Chunk 有独立 embedding。"""
        client = MockEmbeddingClient()
        svc = KnowledgeIngestionService(embedding_client=client)

        path = _long_markdown(tmp_path)
        result = await svc.ingest_one(path)

        assert result.chunk_count >= 3
        assert result.embedded_chunk_count == result.chunk_count

        doc = db.get(KnowledgeDocument, result.document_id)
        assert doc is not None
        chunks = sorted(doc.chunks, key=lambda c: c.chunk_index)
        assert [c.chunk_index for c in chunks] == list(range(result.chunk_count))
        assert all(c.embedding is not None for c in chunks)
        # 每个 Chunk 的 content_hash = SHA256(chunk.content)
        import hashlib

        for c in chunks:
            assert c.content_hash == hashlib.sha256(
                c.content.encode("utf-8")
            ).hexdigest()


@requires_db
class TestDuplicateDocument:
    async def test_duplicate_skips_embedding(self, db) -> None:  # type: ignore[name-defined]
        """相同内容第二次导入：already_exists，Embedding 不重复调用。"""
        client = MockEmbeddingClient()
        svc = KnowledgeIngestionService(embedding_client=client)

        first = await svc.ingest_one(FIXTURES / "sample_wms.md")
        assert first.status == "ready"
        calls_after_first = len(client.calls)

        second = await svc.ingest_one(FIXTURES / "sample_wms.md")

        assert second.status == "already_exists"
        assert second.document_id == first.document_id
        assert second.embedded_chunk_count == 0
        assert second.content_hash == first.content_hash
        # Embedding API 未被再次调用
        assert len(client.calls) == calls_after_first

        # 库中仅一份文档
        doc_count = db.execute(
            select(func.count()).select_from(KnowledgeDocument).where(
                KnowledgeDocument.content_hash == first.content_hash
            )
        ).scalar_one()
        assert doc_count == 1


@requires_db
class TestEmbeddingFailureRollback:
    async def test_embedding_failure_no_partial_data(self, db, tmp_path: Path) -> None:  # type: ignore[name-defined]
        """Embedding 中途失败：Document / Chunk 零残留。"""
        client = MockEmbeddingClient(fail_after=1)
        svc = KnowledgeIngestionService(embedding_client=client)

        path = _long_markdown(tmp_path)  # >= 3 chunks，第 2 次调用抛错
        with pytest.raises(EmbeddingAPIError):
            await svc.ingest_one(path)

        doc_count = db.execute(
            text("SELECT COUNT(*) FROM knowledge_document")
        ).scalar_one()
        chunk_count = db.execute(
            text("SELECT COUNT(*) FROM knowledge_chunk")
        ).scalar_one()
        assert doc_count == 0, "Embedding 失败后不得残留 Document"
        assert chunk_count == 0, "Embedding 失败后不得残留 Chunk"


@requires_db
class TestDbRoundtrip:
    async def test_roundtrip_vector_1024(self, db) -> None:  # type: ignore[name-defined]
        """真实 PostgreSQL：写入 1024 维向量并读回，维度一致。"""
        # 前置：数据库列已迁移为 vector(1024)
        assert settings.embedding.dimension == 1024

        client = MockEmbeddingClient()
        svc = KnowledgeIngestionService(embedding_client=client)

        result = await svc.ingest_one(FIXTURES / "sample_wms.md")
        assert result.status == "ready"

        doc = db.get(KnowledgeDocument, result.document_id)
        assert doc is not None
        for chunk in doc.chunks:
            vector = list(chunk.embedding)
            assert len(vector) == 1024
            assert all(isinstance(x, float) for x in vector)


__all__ = [
    "TestParserErrors",
    "TestEmptyDocument",
    "TestEmbeddingErrors",
    "TestErrorHierarchy",
    "TestTxtIngestion",
    "TestMarkdownIngestion",
    "TestMultiChunk",
    "TestDuplicateDocument",
    "TestEmbeddingFailureRollback",
    "TestDbRoundtrip",
]
