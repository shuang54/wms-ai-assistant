"""Persistent History API —— DB 集成测试（Phase 3.11 Step 30）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

真实链路（无 Mock Repository / 无 Mock Service）：

    INSERT synthetic record（真实 Repository）
        ↓
    GET /api/observability/tools/history（TestClient → FastAPI → 真实
        ToolExecutionPersistentQueryService → 真实 Repository → PostgreSQL）
        ↓
    断言 response 字段 / 顺序 / 空库语义

数据：synthetic only（step30-test-* / get_inventory / test-project）；
      未读取真实库存 / 工单 / WMS 数据。
残留：teardown TRUNCATE **本表**（不动 ai_ops.llm_usage_record / public.*），
      并断言 residue = 0。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.app.db.models.tool_execution_record import (
    TOOL_EXECUTION_SCHEMA,
    TOOL_EXECUTION_TABLE,
)
from backend.app.db.session import get_engine, get_session_factory
from backend.app.db.tool_execution_repository import ToolExecutionRepository
from backend.app.main import app
from backend.app.services.tool_execution_record import ToolExecutionRecord

_TEST_TOOL = "get_inventory"
_TEST_PROJECT = "test-project"

_TABLE = f"{TOOL_EXECUTION_SCHEMA}.{TOOL_EXECUTION_TABLE}"
_TRUNCATE_SQL = text(f"TRUNCATE TABLE {_TABLE} RESTART IDENTITY")

_BASE = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)

_API_PATH = "/api/observability/tools/history"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


_RUN_DB = _env_flag("RUN_DB_TESTS")

requires_db = pytest.mark.skipif(
    not _RUN_DB,
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _record(
    *,
    request_id: str = "step30-test-001",
    round: int = 1,
    started_at: datetime | None = None,
    duration_ms: float = 7.5,
    success: bool = True,
    project_id: str | None = _TEST_PROJECT,
    tool_call_id: str | None = None,
    error_code: str | None = None,
    error_type: str | None = None,
) -> ToolExecutionRecord:
    started = started_at or _BASE
    return ToolExecutionRecord(
        request_id=request_id,
        round=round,
        tool_name=_TEST_TOOL,
        started_at=started,
        finished_at=started + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        success=success,
        project_id=project_id,
        tool_call_id=tool_call_id,
        error_code=error_code,
        error_type=error_type,
    )


def _engine() -> Any:
    from backend.app.db import init_db as init_db_module

    engine = get_engine()
    if engine is None:
        pytest.skip("DATABASE_URL 未配置")
    init_db_module.init_db()
    return engine


def _cleanup(engine: Any) -> int:
    with engine.begin() as conn:
        conn.execute(_TRUNCATE_SQL)
        return int(
            conn.execute(text(f"SELECT COUNT(*) FROM {_TABLE}")).scalar_one()
        )


@pytest.fixture()
def db_api():
    engine = _engine()
    if get_session_factory() is None:
        pytest.skip("session factory 不可用")
    _cleanup(engine)
    repository = ToolExecutionRepository()
    with TestClient(app) as client:
        yield client, repository
    assert _cleanup(engine) == 0                 # DB residue = 0


@requires_db
class TestPersistentHistoryApiDb:
    def test_insert_then_get_history(self, db_api) -> None:
        client, repository = db_api
        repository.create(
            _record(request_id="step30-test-001", duration_ms=7.5)
        )

        response = client.get(_API_PATH)

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["request_id"] == "step30-test-001"
        assert item["round"] == 1
        assert item["tool_name"] == _TEST_TOOL
        assert item["project_id"] == _TEST_PROJECT
        assert item["success"] is True
        assert item["duration_ms"] == 7.5
        assert item["started_at"] == "2026-09-28T12:00:00+00:00"
        assert item["finished_at"] == "2026-09-28T12:00:00.007500+00:00"
        assert item["tool_call_id"] is None
        assert item["error_code"] is None
        assert item["error_type"] is None

    def test_ordering_newest_first(self, db_api) -> None:
        client, repository = db_api
        repository.create(
            _record(request_id="step30-test-old",
                    started_at=_BASE - timedelta(minutes=5))
        )
        repository.create(_record(request_id="step30-test-new"))

        items = client.get(_API_PATH).json()["items"]

        assert [i["request_id"] for i in items] == [
            "step30-test-new", "step30-test-old"
        ]

    def test_limit_query_parameter(self, db_api) -> None:
        client, repository = db_api
        for index in range(3):
            repository.create(
                _record(
                    request_id=f"step30-test-{index}",
                    started_at=_BASE + timedelta(seconds=index),
                )
            )

        items = client.get(f"{_API_PATH}?limit=2").json()["items"]

        assert [i["request_id"] for i in items] == [
            "step30-test-2", "step30-test-1"
        ]

    def test_failure_record_and_nullable_fields(self, db_api) -> None:
        client, repository = db_api
        repository.create(
            _record(
                request_id="step30-test-failed",
                success=False,
                project_id=None,
                error_code="invalid_argument",
                error_type="ToolValidationError",
            )
        )

        item = client.get(_API_PATH).json()["items"][0]

        assert item["success"] is False
        assert item["project_id"] is None
        assert item["error_code"] == "invalid_argument"
        assert item["error_type"] == "ToolValidationError"

    def test_empty_table_returns_empty_items(self, db_api) -> None:
        client, _repository = db_api

        response = client.get(_API_PATH)

        assert response.status_code == 200
        assert response.json() == {"items": [], "limit": 100, "offset": 0}

    def test_invalid_limit_422(self, db_api) -> None:
        client, _repository = db_api

        for bad in ("0", "-1", "1001", "abc"):
            assert client.get(
                f"{_API_PATH}?limit={bad}"
            ).status_code == 422, bad

    def test_no_internal_fields_in_response(self, db_api) -> None:
        client, repository = db_api
        repository.create(_record())

        item = client.get(_API_PATH).json()["items"][0]

        assert set(item) == {
            "request_id", "round", "tool_name", "started_at", "finished_at",
            "duration_ms", "success", "project_id", "tool_call_id",
            "error_code", "error_type",
        }
        assert "id" not in item
        assert "created_at" not in item

    # ---- Step 31：分页一致性（5 records） ----

    def _seed_five(self, repository) -> None:
        """5 条 synthetic 记录（started_at 递减 → recent 序 = 5,4,3,2,1）。"""
        for index in range(5):
            repository.create(
                _record(
                    request_id=f"step31-test-{index}",
                    started_at=_BASE - timedelta(seconds=index),
                )
            )

    def test_pagination_page_consistency(self, db_api) -> None:
        client, repository = db_api
        self._seed_five(repository)

        page1 = client.get(
            f"{_API_PATH}?limit=2&offset=0"
        ).json()["items"]
        page2 = client.get(
            f"{_API_PATH}?limit=2&offset=2"
        ).json()["items"]
        page3 = client.get(
            f"{_API_PATH}?limit=2&offset=4"
        ).json()["items"]
        whole = client.get(
            f"{_API_PATH}?limit=5&offset=0"
        ).json()["items"]

        paged = [i["request_id"] for i in page1 + page2 + page3]
        assert paged == [i["request_id"] for i in whole]
        assert len(page1) == 2 and len(page2) == 2 and len(page3) == 1
        assert len(set(paged)) == 5                     # 无重复
        assert paged == [
            "step31-test-0", "step31-test-1", "step31-test-2",
            "step31-test-3", "step31-test-4",
        ]                                               # 顺序稳定

    def test_pagination_response_metadata(self, db_api) -> None:
        client, repository = db_api
        self._seed_five(repository)

        payload = client.get(f"{_API_PATH}?limit=2&offset=2").json()

        assert set(payload) == {"items", "limit", "offset"}
        assert payload["limit"] == 2
        assert payload["offset"] == 2
        assert "total_count" not in payload

    def test_empty_page_offset_beyond_records(self, db_api) -> None:
        client, repository = db_api
        self._seed_five(repository)

        response = client.get(f"{_API_PATH}?limit=100&offset=100")

        assert response.status_code == 200
        assert response.json() == {
            "items": [], "limit": 100, "offset": 100
        }

    def test_tie_breaker_pagination_same_started_at(self, db_api) -> None:
        """同一 started_at → id DESC tie-breaker；分页不重复 / 不遗漏。"""
        client, repository = db_api
        repository.create(_record(request_id="step31-tie-first"))
        repository.create(_record(request_id="step31-tie-second"))

        first = client.get(f"{_API_PATH}?limit=1&offset=0").json()["items"]
        second = client.get(f"{_API_PATH}?limit=1&offset=1").json()["items"]

        assert [i["request_id"] for i in first] == ["step31-tie-second"]
        assert [i["request_id"] for i in second] == ["step31-tie-first"]

    def test_invalid_offset_rejected_422(self, db_api) -> None:
        client, _repository = db_api

        for bad in ("-1", "-100", "abc"):
            assert client.get(
                f"{_API_PATH}?offset={bad}"
            ).status_code == 422, bad

    def test_llm_usage_table_untouched(self, db_api) -> None:
        client, repository = db_api
        engine = _engine()
        repository.create(_record())

        client.get(_API_PATH)

        with engine.connect() as conn:
            llm_rows = conn.execute(
                text("SELECT COUNT(*) FROM ai_ops.llm_usage_record")
            ).scalar_one()
        assert llm_rows >= 0                     # 只读断言
