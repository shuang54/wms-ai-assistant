"""ToolExecutionPersistenceService 单元测试（Phase 3.11 Step 28）。

非 DB：Fake Repository（0 网络 / 0 DB / 0 LLM）。

覆盖：

    * persist(record) → repository.create(record)（**同一个** Record 对象）
    * 返回值原样透传（ToolExecutionRecordRow）
    * 异常原样上抛（不吞、不转换、不 retry）
    * 不做转换：不转 dict / 不转 JSON / 不经 Snapshot
    * 不做校验 / 聚合 / 序列化 / metrics
    * 默认构造：注入 Repository（配置缺失时构造期不连接数据库）
    * public API 最小：persist（+ repository 只读属性）
"""
from __future__ import annotations

import ast
import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.db.tool_execution_repository import (
    ToolExecutionRecordRow,
    ToolExecutionRepository,
    ToolExecutionRepositoryError,
)
from backend.app.services.tool_execution_persistence_service import (
    ToolExecutionPersistenceService,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord

_MODULE = "backend/app/services/tool_execution_persistence_service.py"
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STARTED = datetime(2026, 9, 26, 10, 20, 30, tzinfo=timezone.utc)

_SENSITIVE_TOKENS = (
    "arguments", "result", "sql", "prompt", "api_key", "password",
    "database_url", "traceback",
)


def _record(**overrides: object) -> ToolExecutionRecord:
    fields: dict[str, object] = {
        "request_id": "req-1",
        "round": 1,
        "tool_name": "get_inventory",
        "started_at": _STARTED,
        "finished_at": _STARTED + timedelta(milliseconds=1.5),
        "duration_ms": 1.5,
        "success": True,
        "project_id": "project-a",
    }
    fields.update(overrides)
    return ToolExecutionRecord(**fields)  # type: ignore[arg-type]


def _row(record: ToolExecutionRecord) -> ToolExecutionRecordRow:
    return ToolExecutionRecordRow(
        id=7,
        request_id=record.request_id,
        round=record.round,
        tool_name=record.tool_name,
        started_at=record.started_at,
        finished_at=record.finished_at,
        duration_ms=record.duration_ms,
        success=record.success,
        project_id=record.project_id,
        tool_call_id=record.tool_call_id,
        error_code=record.error_code,
        error_type=record.error_type,
    )


class FakeRepository:
    """记录调用 + 可注入异常（duck-typed；无需 DB）。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[ToolExecutionRecord] = []
        self._error = error

    def create(self, record: ToolExecutionRecord) -> ToolExecutionRecordRow:
        self.calls.append(record)
        if self._error is not None:
            raise self._error
        return _row(record)


def _service(**kwargs: object) -> tuple[ToolExecutionPersistenceService, FakeRepository]:
    repository = FakeRepository(**kwargs)  # type: ignore[arg-type]
    return ToolExecutionPersistenceService(repository=repository), repository  # type: ignore[arg-type]


# ============================================================
# persist 语义
# ============================================================

class TestPersist:
    def test_persist_delegates_same_record_object(self) -> None:
        """输入必须保持 ToolExecutionRecord（不转 dict / JSON / Snapshot）。"""
        service, repository = _service()
        record = _record()

        service.persist(record)

        assert repository.calls == [record]
        assert repository.calls[0] is record

    def test_persist_returns_repository_row(self) -> None:
        service, _repository = _service()
        record = _record()

        row = service.persist(record)

        assert isinstance(row, ToolExecutionRecordRow)
        assert row.id == 7
        assert row.request_id == record.request_id

    def test_persist_does_not_swallow_repository_error(self) -> None:
        """Service **不吞异常**（失败隔离由 Adapter 负责）。"""
        service, _repository = _service(
            error=ToolExecutionRepositoryError("insert failed")
        )

        with pytest.raises(ToolExecutionRepositoryError):
            service.persist(_record())

    def test_persist_does_not_retry(self) -> None:
        service, repository = _service(
            error=ToolExecutionRepositoryError("boom")
        )

        with pytest.raises(ToolExecutionRepositoryError):
            service.persist(_record())

        assert len(repository.calls) == 1        # 恰好一次调用（无 retry）

    def test_persist_with_failure_record(self) -> None:
        service, repository = _service()
        record = _record(success=False, error_code=None)

        service.persist(record)

        assert repository.calls[0].success is False


# ============================================================
# 构造 / 默认依赖
# ============================================================

class TestConstruction:
    def test_default_repository_is_tool_execution_repository(self) -> None:
        service = ToolExecutionPersistenceService()

        assert isinstance(service.repository, ToolExecutionRepository)

    def test_construction_does_not_connect_database(self) -> None:
        """构造期不连接 DB（DATABASE_URL 未配置也不报错）。"""
        service = ToolExecutionPersistenceService()

        assert service.repository is not None

    def test_injected_repository_is_used(self) -> None:
        repository = FakeRepository()

        service = ToolExecutionPersistenceService(repository=repository)  # type: ignore[arg-type]

        assert service.repository is repository

    def test_public_api_is_minimal(self) -> None:
        public = {
            name
            for name in dir(ToolExecutionPersistenceService)
            if not name.startswith("_")
        }
        assert public == {"persist", "repository"}, public


# ============================================================
# 静态边界（不做转换 / 无 IO / 无重试 / 无敏感文本）
# ============================================================

class TestServiceBoundaries:
    @staticmethod
    def _tree() -> ast.Module:
        with open(
            os.path.join(_REPO_ROOT, *_MODULE.split("/")), encoding="utf-8"
        ) as handle:
            return ast.parse(handle.read())

    def test_no_serialization_or_snapshot_conversion(self) -> None:
        tree = self._tree()
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "dumps", "loads", "asdict", "isoformat", "ToolExecutionSnapshot",
            "snapshot_to_dict",
        ):
            assert forbidden not in identifiers, forbidden
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        for forbidden in ("json", "backend.app.llm", "backend.app.api",
                          "backend.app.tools", "backend.app.db.session"):
            assert not any(
                name == forbidden or name.startswith(forbidden + ".")
                for name in imported
            ), forbidden

    def test_no_retry_queue_or_async(self) -> None:
        tree = self._tree()
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in (
            "sleep", "retry", "queue", "to_thread", "create_task", "batch",
            "celery", "kafka", "redis", "session", "engine", "commit",
        ):
            assert forbidden not in identifiers, forbidden

    def test_no_aggregation_or_metrics(self) -> None:
        public = {
            name
            for name in dir(ToolExecutionPersistenceService)
            if not name.startswith("_")
        }
        for forbidden in (
            "metrics", "aggregate", "sum", "count", "list_all", "delete",
        ):
            assert forbidden not in public, forbidden

    def test_no_sensitive_field_names(self) -> None:
        """AST 层面（代码标识符；docstring 中的"缺 DATABASE_URL"说明不算字段）。"""
        tree = self._tree()
        identifiers = {
            node.id.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        } | {
            node.attr.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for token in _SENSITIVE_TOKENS:
            assert token not in identifiers, token
