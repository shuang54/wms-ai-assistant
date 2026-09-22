"""Knowledge Lifecycle Service 轻量单测（Phase 3.5.10）。

仅覆盖**不需要真实 DB**的契约：

    1. 异常层级：`KnowledgeDocumentNotFoundError` 是
       `KnowledgeIngestionError` 的子类（与现有异常体系一致）。
    2. DTO：`KnowledgeDocumentInfo` 是 frozen dataclass，字段名/类型符合预期。
    3. Service API：`KnowledgeIngestionService` 暴露
       `update_document` / `delete_document` / `get_document` / `list_documents`。
    4. `IngestionResult.status` 可承载 update 结果："updated" / "unchanged"。

需要真实 DB 的端到端场景（create/update/rollback/get/list/delete/完整性）
由 `tests/test_knowledge_lifecycle_real.py` 覆盖（RUN_DB_TESTS=1）。
需要真实 BGE-M3 的 smoke 由 `tests/test_knowledge_lifecycle_smoke.py` 覆盖。
"""
from __future__ import annotations

import inspect

from backend.app.services.knowledge_ingestion_service import (
    IngestionResult,
    KnowledgeDocumentInfo,
    KnowledgeDocumentNotFoundError,
    KnowledgeIngestionError,
    KnowledgeIngestionService,
)


class TestLifecycleExceptions:
    def test_not_found_subclasses_base(self) -> None:
        assert issubclass(KnowledgeDocumentNotFoundError, KnowledgeIngestionError)


class TestKnowledgeDocumentInfoDto:
    def test_is_frozen_dataclass(self) -> None:
        info = KnowledgeDocumentInfo(
            id=1,
            title="t",
            file_name="f.md",
            file_type="md",
            source="local",
            status="ready",
            created_at=__import__("datetime").datetime(2026, 1, 1),
            updated_at=__import__("datetime").datetime(2026, 1, 2),
            chunk_count=3,
        )
        # frozen: 不可写
        import dataclasses
        with __import__("pytest").raises(dataclasses.FrozenInstanceError):
            info.id = 2  # type: ignore[misc]
        # 字段齐全
        expected_fields = {
            "id", "title", "file_name", "file_type", "source",
            "status", "created_at", "updated_at", "chunk_count",
        }
        assert {f.name for f in dataclasses.fields(info)} == expected_fields


class TestServiceApi:
    def test_service_exposes_lifecycle_methods(self) -> None:
        sync_methods = {"delete_document", "get_document", "list_documents"}
        async_methods = {"update_document"}
        for name in sync_methods | async_methods:
            assert hasattr(KnowledgeIngestionService, name), f"缺少方法: {name}"
            method = getattr(KnowledgeIngestionService, name)
            assert callable(method), f"{name} 不可调用"
        # 同步方法
        for name in sync_methods:
            assert not inspect.iscoroutinefunction(
                getattr(KnowledgeIngestionService, name)
            ), f"{name} 应为同步方法"
        # 异步方法
        for name in async_methods:
            assert inspect.iscoroutinefunction(
                getattr(KnowledgeIngestionService, name)
            ), f"{name} 应为 async 方法"

    async def test_update_document_is_async(self) -> None:
        assert inspect.iscoroutinefunction(KnowledgeIngestionService.update_document)

    def test_update_result_can_carry_updated_or_unchanged_status(self) -> None:
        r1 = IngestionResult(
            document_id=1, file_name="f.md", status="updated",
            chunk_count=2, embedded_chunk_count=2, content_hash="h",
        )
        r2 = IngestionResult(
            document_id=1, file_name="f.md", status="unchanged",
            chunk_count=2, embedded_chunk_count=0, content_hash="h",
        )
        assert r1.status == "updated" and r1.embedded_chunk_count == 2
        assert r2.status == "unchanged" and r2.embedded_chunk_count == 0