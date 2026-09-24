"""Project Knowledge Ingestion Context 测试（Phase 3.8.5）。

覆盖任务书 §十一（Case 1-5）+ §十二（写入→检索 DB 隔离 E2E）+ §十四（安全）：

单元（无 DB）：
    - 文档 hash 命名空间化：跨项目同内容独立、legacy 逐字节兼容
    - _build_document_metadata：合并 / 强制覆盖 / 注入剥离 / 类型校验
    - Provider 注入与未注册 clear error（绝不 fallback）

DB（RUN_DB_TESTS=1 + DATABASE_URL）：
    - Case 1/2: project-a / project-b 正常写入
    - Case 3:   恶意 metadata 注入被覆盖
    - Case 4:   __global__ 显式写入
    - Case 5:   project_id=None → legacy（不伪装成 vietnam-wms/__global__）
    - 跨项目去重：相同内容 → 两份独立文档；项目内重导 → already_exists
    - update_document：归属保持 + unchanged 检测不回归
    - 写入→检索 E2E：ingest(project-a) → VectorSearch(project-a) 只召回 A+Global

Embedding 用确定性 KeywordEmbeddingClient（无网络）；Provider 用
InMemoryProjectKnowledgeProvider（Phase 3.8.4，显式注册、无 fallback）。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.config import settings
from backend.app.db.models import KnowledgeChunk, KnowledgeDocument
from backend.app.embedding.client import EmbeddingClient
from backend.app.projects.knowledge_provider import (
    ProjectKnowledgeScope,
    InMemoryProjectKnowledgeProvider,
)
from backend.app.rag.parsers.base import DocumentNotFoundError
from backend.app.services.knowledge_ingestion_service import (
    KnowledgeIngestionError,
    KnowledgeIngestionProjectScopeError,
    KnowledgeIngestionService,
    _document_content_hash,
    _sha256_hex,
)
from backend.app.services.vector_search_service import VectorSearchService


def _env_flag(name: str) -> bool:
    val = os.getenv(name, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


requires_db = pytest.mark.skipif(
    not (_env_flag("RUN_DB_TESTS") and settings.database.url.strip()),
    reason="set RUN_DB_TESTS=1 (plus DATABASE_URL) to enable project "
    "knowledge ingestion DB tests",
)


# ============================================================
# 确定性 Embedding（关键词 → 基向量，无网络）
# ============================================================

KEYWORD_INDEX: dict[str, int] = {
    "A-ONLY-KEYWORD": 0,
    "B-ONLY-KEYWORD": 1,
    "GLOBAL-ONLY-KEYWORD": 2,
    "LEGACY-ONLY-KEYWORD": 3,
    "VN-ONLY-KEYWORD": 4,
    "操作流程": 5,
}


def _kw_vector(*keywords: str) -> list[float]:
    vec = [0.0] * settings.embedding.dimension
    for kw in keywords:
        vec[KEYWORD_INDEX[kw]] = 1.0
    return vec


class KeywordEmbeddingClient(EmbeddingClient):
    """按关键词生成基向量的确定性 Embedding（ingest / search 共用）。"""

    async def embed(self, text_value: str) -> list[float]:
        return _kw_vector(*[kw for kw in KEYWORD_INDEX if kw in text_value])


# ============================================================
# Scope / Provider helpers
# ============================================================

SCOPE_A = ProjectKnowledgeScope(project_id="project-a", namespace="project-a")
SCOPE_B = ProjectKnowledgeScope(project_id="project-b", namespace="project-b")
SCOPE_VN = ProjectKnowledgeScope(
    project_id="vietnam-wms", namespace="vietnam-wms", includes_legacy=True
)
SCOPE_GLOBAL = ProjectKnowledgeScope(
    project_id="__global__", namespace="__global__"
)


class _RecordingProvider:
    """记录 get_scope 调用的 Provider 包装（验证调用次数 / 不执行）。"""

    def __init__(self, scopes: dict[str, ProjectKnowledgeScope]) -> None:
        self._scopes = scopes
        self.calls: list[str] = []

    def get_scope(self, project_id: str) -> ProjectKnowledgeScope:
        self.calls.append(project_id)
        scope = self._scopes.get(project_id)
        if scope is None:
            from backend.app.projects.knowledge_provider import (
                ProjectKnowledgeScopeError,
            )

            raise ProjectKnowledgeScopeError(
                f"项目 {project_id!r} 的知识 scope 未注册"
            )
        return scope


def _provider() -> _RecordingProvider:
    return _RecordingProvider(
        {
            "project-a": SCOPE_A,
            "project-b": SCOPE_B,
            "vietnam-wms": SCOPE_VN,
            "__global__": SCOPE_GLOBAL,
        }
    )


def _svc(provider, embedding=None) -> KnowledgeIngestionService:
    return KnowledgeIngestionService(
        embedding_client=embedding or KeywordEmbeddingClient(),
        knowledge_provider=provider,
    )


def _write_doc(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# ============================================================
# 单元：文档 hash 命名空间化（§十）
# ============================================================

class TestDocumentContentHash:
    def test_legacy_none_matches_old_hash(self) -> None:
        """project_id=None → 纯内容 SHA-256（与旧行为逐字节一致）。"""
        assert _document_content_hash("内容", None) == _sha256_hex("内容")

    def test_namespace_changes_hash(self) -> None:
        """namespace 参与 hash：同内容不同项目 → 不同 hash。"""
        plain = _sha256_hex("manual")
        a = _document_content_hash("manual", "project-a")
        b = _document_content_hash("manual", "project-b")
        assert a != b
        assert a != plain
        assert b != plain

    def test_same_namespace_deterministic(self) -> None:
        """同项目同内容 → hash 稳定（项目内去重依据）。"""
        assert _document_content_hash("manual", "project-a") == (
            _document_content_hash("manual", "project-a")
        )

    def test_global_namespace_distinct_from_legacy(self) -> None:
        """__global__ 与 None（legacy）不共享 hash。"""
        assert _document_content_hash("x", "__global__") != (
            _document_content_hash("x", None)
        )


# ============================================================
# 单元：metadata 合并规则（§五 / §八）
# ============================================================

class TestBuildDocumentMetadata:
    def test_none_metadata_with_namespace(self) -> None:
        meta = KnowledgeIngestionService._build_document_metadata(
            None, "project-a", "md"
        )
        assert meta == {"project_id": "project-a", "source_type": "md"}

    def test_merge_with_caller_metadata(self) -> None:
        meta = KnowledgeIngestionService._build_document_metadata(
            {"source": "wms-manual"}, "project-a", "md"
        )
        assert meta == {
            "source": "wms-manual",
            "project_id": "project-a",
            "source_type": "md",
        }

    def test_malicious_project_id_overridden(self) -> None:
        """Case 3：调用方 metadata 声称 project-b → 强制覆盖为 project-a。"""
        meta = KnowledgeIngestionService._build_document_metadata(
            {"project_id": "project-b", "source": "test"}, "project-a", "md"
        )
        assert meta["project_id"] == "project-a"
        assert meta["source"] == "test"

    def test_legacy_none_strips_injected_project_id(self) -> None:
        """project_id=None：调用方注入的 project_id 一律剥离（不写归属）。"""
        meta = KnowledgeIngestionService._build_document_metadata(
            {"project_id": "project-b"}, None, "md"
        )
        assert "project_id" not in meta
        assert meta == {"source_type": "md"}

    def test_non_dict_metadata_clear_error(self) -> None:
        for bad in ("x", 123, ["a"], {"a"}):
            with pytest.raises(KnowledgeIngestionError, match="metadata"):
                KnowledgeIngestionService._build_document_metadata(
                    bad, "project-a", "md"  # type: ignore[arg-type]
                )

    def test_only_namespace_written_not_retrieval_flags(self) -> None:
        """§四：只写 namespace；includes_global / includes_legacy 不入库。"""
        meta = KnowledgeIngestionService._build_document_metadata(
            None, "vietnam-wms", "md"
        )
        assert "includes_global" not in meta
        assert "includes_legacy" not in meta


# ============================================================
# 单元：Provider 解析与安全（§三 / §十四）
# ============================================================

class TestScopeResolution:
    async def test_provider_not_called_for_legacy_none(self, tmp_path) -> None:
        """project_id=None（旧调用）→ Provider 0 次执行。"""
        provider = _provider()
        svc = _svc(provider)
        with pytest.raises(DocumentNotFoundError):
            # 文件不存在：证明流程走到了 Parser（跳过了 scope 解析）
            await svc.ingest_one(tmp_path / "missing.md")
        assert provider.calls == []

    async def test_provider_called_for_project_ingest(self, tmp_path) -> None:
        provider = _provider()
        svc = _svc(provider)
        with pytest.raises(DocumentNotFoundError):
            await svc.ingest_one(
                tmp_path / "missing.md", project_id="project-a"
            )
        assert provider.calls == ["project-a"]

    async def test_unregistered_project_clear_error_no_fallback(
        self, tmp_path
    ) -> None:
        """§十四-2：project-x 未注册 → clear error，绝不 fallback。"""
        provider = _provider()  # 未注册 project-x
        svc = _svc(provider)
        with pytest.raises(KnowledgeIngestionProjectScopeError, match="project-x"):
            await svc.ingest_one(
                tmp_path / "any.md", project_id="project-x"
            )
        # 失败发生在 Parser 之前：文件甚至不需要存在
        assert provider.calls == ["project-x"]

    async def test_metadata_type_error_before_everything(self, tmp_path) -> None:
        """metadata 非 dict → 输入校验最先失败（clear error）。"""
        provider = _provider()
        svc = _svc(provider)
        with pytest.raises(KnowledgeIngestionError, match="metadata"):
            await svc.ingest_one(
                tmp_path / "missing.md",
                project_id="project-a",
                metadata="not-a-dict",  # type: ignore[arg-type]
            )
        assert provider.calls == []


# ============================================================
# DB Fixtures
# ============================================================

@pytest.fixture(scope="module")
def engine():
    from backend.app.db import reset_engine_cache
    from backend.app.db.base import Base
    from backend.app.db.session import get_engine

    reset_engine_cache()
    eng = get_engine()
    assert eng is not None, "DATABASE_URL 未配置"

    from backend.app.db import models  # noqa: F401  # register models

    Base.metadata.create_all(eng)
    yield eng


@pytest.fixture
def _truncate_after_each(engine):
    from sqlalchemy import text

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
    from backend.app.db.session import get_session_factory

    session = get_session_factory()()
    try:
        yield session
        session.rollback()
    finally:
        session.close()


def _get_doc(db, document_id: int) -> KnowledgeDocument:
    return db.get(KnowledgeDocument, document_id)


# ============================================================
# DB：Case 1-5（任务书 §十一）
# ============================================================

@requires_db
class TestProjectIngestionCases:
    async def test_case1_project_a(self, tmp_path, db) -> None:
        """Case 1：project-a → meta_data.project_id == "project-a"。"""
        path = _write_doc(tmp_path, "manual_a.md", "A-ONLY-KEYWORD 操作流程")
        result = await _svc(_provider()).ingest_one(
            path, project_id="project-a"
        )
        assert result.status == "ready"
        doc = _get_doc(db, result.document_id)
        assert doc.meta_data["project_id"] == "project-a"
        assert doc.meta_data["source_type"] == "md"

    async def test_case2_project_b(self, tmp_path, db) -> None:
        """Case 2：project-b → meta_data.project_id == "project-b"。"""
        path = _write_doc(tmp_path, "manual_b.md", "B-ONLY-KEYWORD 操作流程")
        result = await _svc(_provider()).ingest_one(
            path, project_id="project-b"
        )
        doc = _get_doc(db, result.document_id)
        assert doc.meta_data["project_id"] == "project-b"

    async def test_case3_malicious_metadata_injection(
        self, tmp_path, db
    ) -> None:
        """Case 3：metadata 声称 project-b，实际 project-a → 写 project-a。"""
        path = _write_doc(tmp_path, "manual.md", "A-ONLY-KEYWORD 操作流程")
        result = await _svc(_provider()).ingest_one(
            path,
            project_id="project-a",
            metadata={"project_id": "project-b", "source": "test"},
        )
        doc = _get_doc(db, result.document_id)
        assert doc.meta_data["project_id"] == "project-a"
        assert doc.meta_data["source"] == "test"

    async def test_case4_global_explicit(self, tmp_path, db) -> None:
        """Case 4：__global__ 必须显式设置（None 绝不代表 global）。"""
        path = _write_doc(
            tmp_path, "common.md", "GLOBAL-ONLY-KEYWORD 操作流程"
        )
        result = await _svc(_provider()).ingest_one(
            path, project_id="__global__"
        )
        doc = _get_doc(db, result.document_id)
        assert doc.meta_data["project_id"] == "__global__"

    async def test_case5_legacy_none(self, tmp_path, db) -> None:
        """Case 5：project_id=None → 无 project_id 键（不伪装）。"""
        path = _write_doc(
            tmp_path, "legacy.md", "LEGACY-ONLY-KEYWORD 操作流程"
        )
        result = await _svc(_provider()).ingest_one(path)
        doc = _get_doc(db, result.document_id)
        assert "project_id" not in (doc.meta_data or {})
        assert doc.meta_data == {"source_type": "md"}  # 旧行为逐字段一致

    async def test_chunk_metadata_not_polluted(self, tmp_path, db) -> None:
        """§九：chunk.meta_data 不写入 project_id（document 级 scope 足够）。"""
        path = _write_doc(tmp_path, "manual.md", "A-ONLY-KEYWORD 操作流程")
        result = await _svc(_provider()).ingest_one(
            path, project_id="project-a"
        )
        chunks = db.scalars(
            select(KnowledgeChunk).where(
                KnowledgeChunk.document_id == result.document_id
            )
        ).all()
        assert chunks
        for chunk in chunks:
            assert "project_id" not in (chunk.meta_data or {})


# ============================================================
# DB：跨项目去重 / 项目内幂等（§十）
# ============================================================

@requires_db
class TestProjectDedup:
    async def test_same_content_two_projects_independent(
        self, tmp_path, db
    ) -> None:
        """project-a / project-b 相同内容 → 两份独立文档（互不覆盖）。"""
        path_a = _write_doc(tmp_path, "same.md", "完全相同的内容 操作流程")
        path_b = _write_doc(tmp_path, "same_copy.md", "完全相同的内容 操作流程")

        svc = _svc(_provider())
        result_a = await svc.ingest_one(path_a, project_id="project-a")
        result_b = await svc.ingest_one(path_b, project_id="project-b")

        assert result_a.status == "ready"
        assert result_b.status == "ready"
        assert result_a.document_id != result_b.document_id

        doc_a = _get_doc(db, result_a.document_id)
        doc_b = _get_doc(db, result_b.document_id)
        assert doc_a.meta_data["project_id"] == "project-a"
        assert doc_b.meta_data["project_id"] == "project-b"

    async def test_same_project_reingest_already_exists(
        self, tmp_path, db
    ) -> None:
        """项目内重导相同内容 → already_exists（指向自己的文档）。"""
        path = _write_doc(tmp_path, "manual.md", "A-ONLY-KEYWORD 操作流程")
        svc = _svc(_provider())
        first = await svc.ingest_one(path, project_id="project-a")
        second = await svc.ingest_one(path, project_id="project-a")

        assert first.status == "ready"
        assert second.status == "already_exists"
        assert second.document_id == first.document_id
        assert second.embedded_chunk_count == 0  # 未重复调 Embedding

    async def test_legacy_and_project_same_content_coexist(
        self, tmp_path, db
    ) -> None:
        """legacy（None）与 project-a 相同内容 → 共存（hash 命名空间不同）。"""
        content = "完全相同的内容 操作流程"
        path_legacy = _write_doc(tmp_path, "l.md", content)
        path_a = _write_doc(tmp_path, "a.md", content)
        svc = _svc(_provider())

        legacy = await svc.ingest_one(path_legacy)
        scoped = await svc.ingest_one(path_a, project_id="project-a")

        assert legacy.status == "ready"
        assert scoped.status == "ready"
        assert legacy.document_id != scoped.document_id


# ============================================================
# DB：update_document 归属保持（§十）
# ============================================================

@requires_db
class TestUpdatePreservesOwnership:
    async def test_update_same_content_unchanged(self, tmp_path, db) -> None:
        """同内容 update → unchanged（namespaced hash 检测不回归）。"""
        path = _write_doc(tmp_path, "v1.md", "A-ONLY-KEYWORD 第一版 操作流程")
        svc = _svc(_provider())
        created = await svc.ingest_one(path, project_id="project-a")

        result = await svc.update_document(created.document_id, path)
        assert result.status == "unchanged"
        assert result.embedded_chunk_count == 0

    async def test_update_new_content_keeps_project_id(
        self, tmp_path, db
    ) -> None:
        """内容更新 → updated，meta_data.project_id 保持 project-a。"""
        path_v1 = _write_doc(tmp_path, "v1.md", "A-ONLY-KEYWORD 第一版")
        path_v2 = _write_doc(tmp_path, "v2.md", "A-ONLY-KEYWORD 第二版")
        svc = _svc(_provider())
        created = await svc.ingest_one(path_v1, project_id="project-a")

        updated = await svc.update_document(created.document_id, path_v2)
        assert updated.status == "updated"

        doc = _get_doc(db, created.document_id)
        assert doc.meta_data["project_id"] == "project-a"
        assert doc.content_hash == _document_content_hash(
            "A-ONLY-KEYWORD 第二版", "project-a"
        )

        # 新内容在 project-a 下再 ingest → already_exists（hash 一致）
        again = await svc.ingest_one(path_v2, project_id="project-a")
        assert again.status == "already_exists"
        assert again.document_id == created.document_id


# ============================================================
# DB：写入 → 检索 隔离 E2E（§十二，最重要）
# ============================================================

@requires_db
class TestIngestToRetrievalE2E:
    """ingest(project) → VectorSearch(scope) 端到端隔离矩阵。"""

    async def _seed(self, tmp_path) -> None:
        svc = _svc(_provider())
        await svc.ingest_one(
            _write_doc(tmp_path, "a.md", "A-ONLY-KEYWORD 操作流程"),
            project_id="project-a",
        )
        await svc.ingest_one(
            _write_doc(tmp_path, "b.md", "B-ONLY-KEYWORD 操作流程"),
            project_id="project-b",
        )
        await svc.ingest_one(
            _write_doc(tmp_path, "g.md", "GLOBAL-ONLY-KEYWORD 操作流程"),
            project_id="__global__",
        )
        # legacy：旧调用形态（无 project_id）
        await svc.ingest_one(
            _write_doc(tmp_path, "legacy.md", "LEGACY-ONLY-KEYWORD 操作流程")
        )
        await svc.ingest_one(
            _write_doc(tmp_path, "vn.md", "VN-ONLY-KEYWORD 操作流程"),
            project_id="vietnam-wms",
        )

    def _search(self) -> VectorSearchService:
        return VectorSearchService(embedding_client=KeywordEmbeddingClient())

    def _hits(self, results) -> set[str]:
        joined = " ".join(r.content for r in results)
        return {
            kw
            for kw in KEYWORD_INDEX
            if kw != "操作流程" and kw in joined
        }

    async def test_project_a_retrieval(self, tmp_path) -> None:
        await self._seed(tmp_path)
        results = await self._search().search(
            "操作流程", top_k=10, knowledge_scope=SCOPE_A
        )
        assert self._hits(results) == {"A-ONLY-KEYWORD", "GLOBAL-ONLY-KEYWORD"}

    async def test_project_b_retrieval(self, tmp_path) -> None:
        await self._seed(tmp_path)
        results = await self._search().search(
            "操作流程", top_k=10, knowledge_scope=SCOPE_B
        )
        assert self._hits(results) == {"B-ONLY-KEYWORD", "GLOBAL-ONLY-KEYWORD"}

    async def test_vietnam_wms_retrieval(self, tmp_path) -> None:
        """vietnam-wms：legacy + global + 自身可见；A / B 不可见。"""
        await self._seed(tmp_path)
        results = await self._search().search(
            "操作流程", top_k=10, knowledge_scope=SCOPE_VN
        )
        assert self._hits(results) == {
            "VN-ONLY-KEYWORD",
            "GLOBAL-ONLY-KEYWORD",
            "LEGACY-ONLY-KEYWORD",
        }

    async def test_project_a_cannot_hit_b_keyword(self, tmp_path) -> None:
        """project-a 查 B 专属关键词 → 不能命中 B。"""
        await self._seed(tmp_path)
        results = await self._search().search(
            "B-ONLY-KEYWORD", top_k=10, knowledge_scope=SCOPE_A
        )
        assert self._hits(results) <= {"A-ONLY-KEYWORD", "GLOBAL-ONLY-KEYWORD"}

    async def test_project_b_cannot_hit_a_keyword(self, tmp_path) -> None:
        await self._seed(tmp_path)
        results = await self._search().search(
            "A-ONLY-KEYWORD", top_k=10, knowledge_scope=SCOPE_B
        )
        assert self._hits(results) <= {"B-ONLY-KEYWORD", "GLOBAL-ONLY-KEYWORD"}

    async def test_normal_projects_cannot_hit_legacy(self, tmp_path) -> None:
        """legacy 仅 vietnam-wms 可见（project-a / project-b 均不可）。"""
        await self._seed(tmp_path)
        for scope in (SCOPE_A, SCOPE_B):
            results = await self._search().search(
                "LEGACY-ONLY-KEYWORD", top_k=10, knowledge_scope=scope
            )
            assert "LEGACY-ONLY-KEYWORD" not in " ".join(
                r.content for r in results
            )


__all__ = [
    "TestDocumentContentHash",
    "TestBuildDocumentMetadata",
    "TestScopeResolution",
    "TestProjectIngestionCases",
    "TestProjectDedup",
    "TestUpdatePreservesOwnership",
    "TestIngestToRetrievalE2E",
]
