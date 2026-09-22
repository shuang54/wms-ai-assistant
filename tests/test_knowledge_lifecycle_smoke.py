"""Knowledge Lifecycle 真实 BGE-M3 Smoke（Phase 3.5.10）。

执行：`RUN_REAL_KB_LIFECYCLE=1 pytest -q tests/test_knowledge_lifecycle_smoke.py`

前置：
    RUN_REAL_KB_LIFECYCLE=1 / RUN_DB_TESTS=1
    EMBEDDING_API_KEY / EMBEDDING_BASE_URL / EMBEDDING_MODEL
    DATABASE_URL

覆盖：create → update → query（VectorSearchService 真实 BGE-M3）→ delete，
全程不调用 DeepSeek。

为避免污染当前真实知识库，本测试使用临时唯一文档并自清理。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app.config import settings
from backend.app.db import reset_engine_cache
from backend.app.db.base import Base
from backend.app.db.session import get_engine
from backend.app.services.knowledge_ingestion_service import (
    KnowledgeDocumentNotFoundError,
    KnowledgeIngestionService,
)
from backend.app.services.vector_search_service import VectorSearchService


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_real = pytest.mark.skipif(
    not (_env_flag("RUN_REAL_KB_LIFECYCLE") and _env_flag("RUN_DB_TESTS")
         and settings.database.url.strip()
         and settings.embedding.api_key.strip()),
    reason=(
        "Real KB lifecycle smoke requires RUN_REAL_KB_LIFECYCLE=1 + RUN_DB_TESTS=1 "
        "+ EMBEDDING_API_KEY + DATABASE_URL"
    ),
)


@pytest.fixture(scope="module")
def engine():
    reset_engine_cache()
    eng = get_engine()
    assert eng is not None
    from backend.app.db import models  # noqa: F401
    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture
def tmp_doc(tmp_path: Path) -> Path:
    """生成仅用于本测试的临时文档（唯一内容，避免与真实 KB 冲突）。"""
    p = tmp_path / "lifecycle_smoke.md"
    body = (
        "# Lifecycle Smoke Unique Doc\n\n"
        "## 第一节\n\n" + ("这是生命周期 smoke 测试的初始版本内容。" * 20) + "\n\n"
        "## 第二节\n\n" + ("补充段落用于增加 chunk 数量。" * 20) + "\n"
    )
    p.write_text(body, encoding="utf-8")
    return p


@pytest.fixture
def tmp_doc_v2(tmp_path: Path) -> Path:
    p = tmp_path / "lifecycle_smoke_v2.md"
    body = (
        "# Lifecycle Smoke Unique Doc v2（已更新）\n\n"
        "## 第一节\n\n" + ("v2 完全改写了首段，引入新的关键术语 LC_V2_TOKEN。" * 20) + "\n\n"
        "## 第二节\n\n" + ("v2 第二节补充内容。" * 20) + "\n\n"
        "## 第三节\n\n" + ("v2 全新加入的第三节 LC_V2_SECTION_3_MARKER。" * 20) + "\n"
    )
    p.write_text(body, encoding="utf-8")
    return p


@requires_real
class TestLifecycleRealSmoke:
    async def test_create_update_query_delete(
        self, tmp_doc: Path, tmp_doc_v2: Path, engine
    ) -> None:
        # ---- 1. CREATE ----
        svc = KnowledgeIngestionService()
        created = await svc.ingest_one(tmp_doc)
        assert created.status == "ready"
        assert created.chunk_count >= 1
        doc_id = created.document_id

        try:
            # ---- 2. UPDATE ----
            updated = await svc.update_document(doc_id, tmp_doc_v2)
            assert updated.status == "updated"
            assert updated.document_id == doc_id
            assert updated.content_hash != created.content_hash

            # ---- 3. QUERY：真实 BGE-M3 + pgvector 应能召回含 LC_V2_TOKEN 的 chunk ----
            vs = VectorSearchService()
            rows = await vs.search("LC_V2_TOKEN 关键词检索", top_k=5)
            assert any(r.document_id == doc_id for r in rows), \
                "VectorSearchService 未召回刚更新的文档"

            # ---- 4. GET ----
            info = svc.get_document(doc_id)
            assert info.chunk_count == updated.chunk_count
            assert info.status == "ready"

        finally:
            # ---- 5. DELETE（不论中途是否断言失败，都清理）----
            svc.delete_document(doc_id)
            with pytest.raises(KnowledgeDocumentNotFoundError):
                svc.get_document(doc_id)
            # DB 行确实被清空
            from sqlalchemy import text
            with engine.connect() as conn:
                n = conn.execute(
                    text("SELECT COUNT(*) FROM knowledge_chunk WHERE document_id = :d"),
                    {"d": doc_id},
                ).scalar_one()
                assert n == 0