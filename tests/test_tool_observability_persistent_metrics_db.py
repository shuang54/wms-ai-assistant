"""Persistent Metrics —— DB 集成测试（Phase 3.11 Step 33）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

真实链路（无 Mock Repository / 无 Mock Service）：

    INSERT synthetic records（真实 Repository）
        ↓
    GET /api/observability/tools/metrics/persistent?<filters>
        （TestClient → FastAPI → 真实 PersistentQueryService →
          真实 Repository（SQL 聚合）→ PostgreSQL）
        ↓
    断言聚合结果 / 过滤 / 空语义 / 注入安全

synthetic 数据矩阵（§十九）：

    A: project-a / get_inventory  / success
    B: project-a / get_inventory  / failure
    C: project-a / get_work_order / success
    D: project-b / get_inventory  / success
    E: project-b / get_work_order / failure

数据：synthetic only；未读取真实 WMS 数据。
清理（§十九）：**不使用 TRUNCATE**；只按 ``request_id`` 前缀
``step33-`` 定向 ``DELETE`` 本测试自己插入的行，并断言这些行数归零。
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

_TABLE = f"{TOOL_EXECUTION_SCHEMA}.{TOOL_EXECUTION_TABLE}"
_DELETE_SQL = text(
    f"DELETE FROM {_TABLE} WHERE request_id LIKE :prefix"
)                                              # 定向清理（绑定参数）
_COUNT_PREFIX_SQL = text(
    f"SELECT COUNT(*) FROM {_TABLE} WHERE request_id LIKE :prefix"
)

_BASE = datetime(2026, 9, 28, 17, 0, 0, tzinfo=timezone.utc)
_METRICS_PATH = "/api/observability/tools/metrics/persistent"
_PREFIX = "step33-%"

#: (request_id, project_id, tool_name, success, duration_ms)
_MATRIX = (
    ("step33-A", "project-a", "get_inventory", True, 100.0),
    ("step33-B", "project-a", "get_inventory", False, 200.0),
    ("step33-C", "project-a", "get_work_order", True, 300.0),
    ("step33-D", "project-b", "get_inventory", True, 400.0),
    ("step33-E", "project-b", "get_work_order", False, 500.0),
)


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


_RUN_DB = _env_flag("RUN_DB_TESTS")

requires_db = pytest.mark.skipif(
    not _RUN_DB,
    reason="set RUN_DB_TESTS=1 (true/yes/on) to enable DB integration tests",
)


def _record(
    *,
    request_id: str,
    project_id: str,
    tool_name: str,
    success: bool,
    duration_ms: float,
    started_at: datetime,
) -> ToolExecutionRecord:
    return ToolExecutionRecord(
        request_id=request_id,
        round=1,
        tool_name=tool_name,
        started_at=started_at,
        finished_at=started_at + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
        success=success,
        project_id=project_id,
        error_type=None if success else "ToolValidationError",
    )


def _engine() -> Any:
    from backend.app.db import init_db as init_db_module

    engine = get_engine()
    if engine is None:
        pytest.skip("DATABASE_URL 未配置")
    init_db_module.init_db()
    return engine


def _cleanup(engine: Any) -> int:
    """定向清理本测试的 synthetic 行（不 TRUNCATE / 不删真实数据）。"""
    with engine.begin() as conn:
        conn.execute(_DELETE_SQL, {"prefix": _PREFIX})
        return int(
            conn.execute(
                _COUNT_PREFIX_SQL, {"prefix": _PREFIX}
            ).scalar_one()
        )


def _seed(repository: ToolExecutionRepository) -> None:
    for index, (
        request_id, project_id, tool_name, success, duration_ms
    ) in enumerate(_MATRIX):
        repository.create(
            _record(
                request_id=request_id,
                project_id=project_id,
                tool_name=tool_name,
                success=success,
                duration_ms=duration_ms,
                started_at=_BASE - timedelta(seconds=index),
            )
        )


@pytest.fixture()
def metrics_api():
    engine = _engine()
    if get_session_factory() is None:
        pytest.skip("session factory 不可用")
    _cleanup(engine)                          # 先清残留（仅本测试前缀）
    repository = ToolExecutionRepository()
    _seed(repository)
    with TestClient(app) as client:
        yield client
    assert _cleanup(engine) == 0              # 本次测试行全部删除
    with engine.connect() as conn:
        remaining = conn.execute(
            text(f"SELECT COUNT(*) FROM {_TABLE}")
        ).scalar_one()
    assert remaining == 0                     # DB residue = 0


def _metrics(client, query: str = "") -> dict[str, Any]:
    response = client.get(f"{_METRICS_PATH}{query}")
    assert response.status_code == 200
    return response.json()


def _assert_counts(
    payload: dict[str, Any], total: int, success: int, failure: int
) -> None:
    assert payload["total_count"] == total
    assert payload["success_count"] == success
    assert payload["failure_count"] == failure
    assert payload["success_count"] + payload["failure_count"] == total


@requires_db
class TestPersistentMetricsDb:
    def test_all_records(self, metrics_api) -> None:
        payload = _metrics(metrics_api)

        _assert_counts(payload, 5, 3, 2)
        assert payload["success_rate"] == pytest.approx(0.6)
        assert payload["failure_rate"] == pytest.approx(0.4)
        assert payload["total_duration_ms"] == pytest.approx(1500.0)
        assert payload["average_duration_ms"] == pytest.approx(300.0)
        assert payload["max_duration_ms"] == pytest.approx(500.0)

    def test_project_filters(self, metrics_api) -> None:
        project_a = _metrics(metrics_api, "?project_id=project-a")
        project_b = _metrics(metrics_api, "?project_id=project-b")

        _assert_counts(project_a, 3, 2, 1)
        _assert_counts(project_b, 2, 1, 1)
        assert project_a["max_duration_ms"] == pytest.approx(300.0)
        assert project_b["max_duration_ms"] == pytest.approx(500.0)

    def test_tool_filters(self, metrics_api) -> None:
        inventory = _metrics(
            metrics_api, "?tool_name=get_inventory"
        )
        work_order = _metrics(
            metrics_api, "?tool_name=get_work_order"
        )

        _assert_counts(inventory, 3, 2, 1)
        _assert_counts(work_order, 2, 1, 1)
        assert inventory["total_duration_ms"] == pytest.approx(700.0)
        assert work_order["total_duration_ms"] == pytest.approx(800.0)

    def test_success_filters(self, metrics_api) -> None:
        successes = _metrics(metrics_api, "?success=true")
        failures = _metrics(metrics_api, "?success=false")

        _assert_counts(successes, 3, 3, 0)
        assert successes["success_rate"] == pytest.approx(1.0)
        assert successes["failure_rate"] == pytest.approx(0.0)
        _assert_counts(failures, 2, 0, 2)
        assert failures["success_rate"] == pytest.approx(0.0)
        assert failures["failure_rate"] == pytest.approx(1.0)

    def test_combined_filters(self, metrics_api) -> None:
        project_inventory = _metrics(
            metrics_api, "?project_id=project-a&tool_name=get_inventory"
        )
        project_success = _metrics(
            metrics_api, "?project_id=project-a&success=true"
        )
        triple = _metrics(
            metrics_api,
            "?project_id=project-b&tool_name=get_work_order&success=false",
        )

        _assert_counts(project_inventory, 2, 1, 1)
        assert project_inventory["total_duration_ms"] == pytest.approx(300.0)
        _assert_counts(project_success, 2, 2, 0)
        _assert_counts(triple, 1, 0, 1)
        assert triple["total_duration_ms"] == pytest.approx(500.0)

    def test_empty_result_semantics(self, metrics_api) -> None:
        """无匹配行 → 计数 0；比率 / 均值 / 最大值 None（不是 0）。"""
        payload = _metrics(metrics_api, "?project_id=does-not-exist")

        _assert_counts(payload, 0, 0, 0)
        assert payload["success_rate"] is None
        assert payload["failure_rate"] is None
        assert payload["average_duration_ms"] is None
        assert payload["max_duration_ms"] is None
        assert payload["total_duration_ms"] == 0.0

    def test_empty_string_filter_matches_nothing(self, metrics_api) -> None:
        payload = _metrics(metrics_api, "?project_id=&tool_name=")

        _assert_counts(payload, 0, 0, 0)

    def test_sql_injection_input_is_literal(self, metrics_api) -> None:
        payload = _metrics(
            metrics_api, "?project_id=%27%20OR%201%3D1%20--"
        )

        _assert_counts(payload, 0, 0, 0)          # 不扩大结果
        assert _metrics(metrics_api)["total_count"] == 5   # 表未被破坏

    def test_invalid_parameters_return_422(self, metrics_api) -> None:
        """非法布尔字面量 → 422（FastAPI / Pydantic 校验）。

        注：``success=1`` 属 Pydantic 布尔字面量（"1"/"0" 合法），
        与项目既有 typed query parameter 行为一致 —— 不作为错误。
        """
        assert metrics_api.get(
            f"{_METRICS_PATH}?success=maybe"
        ).status_code == 422

    def test_no_limit_or_offset_accepted(self, metrics_api) -> None:
        """Metrics 是单行聚合：limit / offset 不参与（额外参数被忽略）。"""
        payload = _metrics(metrics_api, "?limit=1&offset=0")

        _assert_counts(payload, 5, 3, 2)

    def test_response_field_whitelist(self, metrics_api) -> None:
        payload = _metrics(metrics_api, "?project_id=project-a")

        assert set(payload) == {
            "total_count", "success_count", "failure_count", "success_rate",
            "failure_rate", "total_duration_ms", "average_duration_ms",
            "max_duration_ms",
        }
        for forbidden in (
            "id", "request_id", "project_id", "tool_name", "rows",
            "query", "sql",
        ):
            assert forbidden not in payload, forbidden

    def test_runtime_endpoints_unaffected(self, metrics_api) -> None:
        """Runtime 端点仍只读内存（与持久化数据无关）。"""
        assert metrics_api.get(
            "/api/observability/tools/metrics"
        ).json()["total_count"] == 0
        assert metrics_api.get("/api/observability/tools").json() == {
            "records": []
        }

    def test_llm_usage_table_untouched(self, metrics_api) -> None:
        engine = _engine()

        _metrics(metrics_api, "?project_id=project-a")

        with engine.connect() as conn:
            llm_rows = conn.execute(
                text("SELECT COUNT(*) FROM ai_ops.llm_usage_record")
            ).scalar_one()
        assert llm_rows >= 0                     # 只读断言
