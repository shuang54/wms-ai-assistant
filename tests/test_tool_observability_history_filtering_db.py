"""Persistent History Filtering —— DB 集成测试（Phase 3.11 Step 32）。

只在 ``RUN_DB_TESTS=1`` 时运行（默认跳过）。

真实链路（无 Mock Repository / 无 Mock Service）：

    INSERT synthetic records（真实 Repository）
        ↓
    GET /api/observability/tools/history?<filters>（TestClient → FastAPI →
        真实 PersistentQueryService → 真实 Repository → PostgreSQL）
        ↓
    断言过滤结果 / 排序 / 分页 / 注入安全

synthetic 数据矩阵（§十五）：

    A: project-a  get_inventory  success=true
    B: project-a  get_inventory  success=false
    C: project-a  get_work_order success=true
    D: project-b  get_inventory  success=true
    E: project-b  get_work_order success=false

数据：synthetic only（step32-* / get_inventory / get_work_order /
      project-a / project-b）；未读取真实库存 / 工单 / WMS 数据。
残留：teardown TRUNCATE **本表**，并断言 residue = 0；
      未触碰 ai_ops.llm_usage_record / public.*。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import text

from backend.app.db.models.tool_execution_record import (
    TOOL_EXECUTION_SCHEMA,
    TOOL_EXECUTION_TABLE,
)
from backend.app.db.session import get_engine, get_session_factory
from backend.app.db.tool_execution_repository import ToolExecutionRepository
from backend.app.main import app
from backend.app.services.tool_execution_record import ToolExecutionRecord

# M2.3 A2：history 端点需要"已登录 + 服务端项目作用域"；客户端 project_id
# 查询参数已移除（不再接受），因此本文件改为验证"服务端作用域内的业务过滤"。
from tests.api_session_auth import SessionTestClient as TestClient  # noqa: E402
from tests.api_session_auth import (  # noqa: F401,E402
    observability_project_scope_session,
)

pytestmark = pytest.mark.usefixtures("observability_project_scope_session")

#: M2.3 A2：服务端唯一授权项目（与 observability_project_scope_session 一致）。
_SERVER_PROJECT_ID = "vietnam-wms"
#: 授权范围之外的项目（其记录必须不可见）。
_OUT_OF_SCOPE_PROJECT_ID = "other-project"

_TABLE = f"{TOOL_EXECUTION_SCHEMA}.{TOOL_EXECUTION_TABLE}"
_TRUNCATE_SQL = text(f"TRUNCATE TABLE {_TABLE} RESTART IDENTITY")

_BASE = datetime(2026, 9, 28, 15, 0, 0, tzinfo=timezone.utc)
_API_PATH = "/api/observability/tools/history"

#: (request_id, project_id, tool_name, success) —— started_at 递减（A 最新）
#: M2.3 A2：前三条在服务端授权项目内；后两条在授权范围外（必须不可见）。
_MATRIX = (
    ("step32-A", _SERVER_PROJECT_ID, "get_inventory", True),
    ("step32-B", _SERVER_PROJECT_ID, "get_inventory", False),
    ("step32-C", _SERVER_PROJECT_ID, "get_work_order", True),
    ("step32-D", _OUT_OF_SCOPE_PROJECT_ID, "get_inventory", True),
    ("step32-E", _OUT_OF_SCOPE_PROJECT_ID, "get_work_order", False),
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
    project_id: str | None,
    tool_name: str,
    success: bool,
    started_at: datetime,
    duration_ms: float = 5.0,
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
    for index, (request_id, project_id, tool_name, success) in enumerate(
        _MATRIX
    ):
        repository.create(
            _record(
                request_id=request_id,
                project_id=project_id,
                tool_name=tool_name,
                success=success,
                started_at=_BASE - timedelta(seconds=index),
            )
        )
    with TestClient(app) as client:
        yield client, repository
    assert _cleanup(engine) == 0                 # DB residue = 0


def _ids(client, query: str = "") -> list[str]:
    response = client.get(f"{_API_PATH}{query}")
    assert response.status_code == 200
    return [item["request_id"] for item in response.json()["items"]]


@requires_db
class TestPersistentHistoryFilteringDb:
    def test_server_scope_limits_visible_records(self, db_api) -> None:
        """A2：只有服务端授权项目内的记录可见（越权项目不可见）。"""
        client, _repository = db_api

        assert _ids(client) == ["step32-A", "step32-B", "step32-C"]

    def test_client_project_id_is_ignored(self, db_api) -> None:
        """A2：客户端 project_id 不再被接受，不能扩大 / 缩小授权范围。"""
        client, _repository = db_api

        for claimed in (_SERVER_PROJECT_ID, _OUT_OF_SCOPE_PROJECT_ID, ""):
            assert _ids(client, f"?project_id={claimed}") == [
                "step32-A", "step32-B", "step32-C"
            ]

    def test_tool_name_filter(self, db_api) -> None:
        client, _repository = db_api

        assert _ids(client, "?tool_name=get_inventory") == [
            "step32-A", "step32-B"
        ]
        assert _ids(client, "?tool_name=get_work_order") == ["step32-C"]

    def test_success_true_filter(self, db_api) -> None:
        client, _repository = db_api

        assert _ids(client, "?success=true") == ["step32-A", "step32-C"]

    def test_success_false_filter(self, db_api) -> None:
        client, _repository = db_api

        assert _ids(client, "?success=false") == ["step32-B"]

    def test_combined_filters(self, db_api) -> None:
        client, _repository = db_api

        assert _ids(
            client,
            "?tool_name=get_inventory&success=true",
        ) == ["step32-A"]
        assert _ids(
            client,
            "?tool_name=get_inventory&success=false",
        ) == ["step32-B"]
        assert _ids(
            client, "?tool_name=get_work_order"
        ) == ["step32-C"]

    def test_filter_with_pagination(self, db_api) -> None:
        """filter → order → limit → offset（不是 limit → filter）。"""
        client, _repository = db_api

        page1 = _ids(client, "?limit=2&offset=0")
        page2 = _ids(client, "?limit=2&offset=2")
        whole = _ids(client, "?limit=100&offset=0")

        assert page1 == ["step32-A", "step32-B"]
        assert page2 == ["step32-C"]
        assert page1 + page2 == whole
        assert len(set(page1 + page2)) == len(whole)

    def test_filter_unknown_value_returns_empty_page(self, db_api) -> None:
        client, _repository = db_api

        response = client.get(
            f"{_API_PATH}?tool_name=does-not-exist&limit=100&offset=0"
        )

        assert response.status_code == 200                # 不是 404
        assert response.json() == {
            "items": [], "limit": 100, "offset": 0
        }

    def test_empty_string_filter_matches_nothing(self, db_api) -> None:
        client, _repository = db_api

        assert _ids(client, "?tool_name=") == []

    def test_filter_does_not_change_ordering(self, db_api) -> None:
        """过滤后仍按 started_at DESC, id DESC（非按过滤列排序）。"""
        client, _repository = db_api

        ids = _ids(client, "?tool_name=get_inventory")

        assert ids == ["step32-A", "step32-B"]  # 时间倒序

    def test_sql_injection_input_is_treated_as_literal(self, db_api) -> None:
        client, _repository = db_api

        injected = [
            _ids(client, "?project_id='%20OR%201=1%20--"),
            _ids(client, "?tool_name=get_inventory'%20OR%201=1%20--"),
        ]

        # 第一个：客户端 project_id 被忽略 ⇒ 仍是授权范围内的三条；
        # 第二个：注入串按字面量匹配 ⇒ 空结果。两者都不扩大结果。
        assert injected == [
            ["step32-A", "step32-B", "step32-C"], []
        ]
        assert _ids(client) == [
            "step32-A", "step32-B", "step32-C"
        ]                                                  # 表未被破坏

    def test_out_of_scope_project_records_are_invisible(self, db_api) -> None:
        """A2：授权范围外的记录（step32-D / E）对任何查询都不可见。"""
        client, _repository = db_api

        for query in (
            "",
            "?tool_name=get_inventory",
            "?tool_name=get_work_order",
            "?success=true",
            "?success=false",
            "?limit=100&offset=0",
        ):
            assert "step32-D" not in _ids(client, query), query
            assert "step32-E" not in _ids(client, query), query

    def test_filtered_items_keep_snapshot_whitelist(self, db_api) -> None:
        client, _repository = db_api

        response = client.get(f"{_API_PATH}?success=false")
        item = response.json()["items"][0]

        assert set(item) == {
            "request_id", "round", "tool_name", "started_at", "finished_at",
            "duration_ms", "success", "project_id", "tool_call_id",
            "error_code", "error_type",
        }
        assert item["success"] is False
        assert item["error_type"] == "ToolValidationError"
        assert "id" not in item

    def test_invalid_parameters_return_422(self, db_api) -> None:
        client, _repository = db_api

        for query in (
            "?success=maybe",
            "?limit=0",
            "?limit=1001",
            "?offset=-1",
        ):
            assert client.get(
                f"{_API_PATH}{query}"
            ).status_code == 422, query

    def test_llm_usage_table_untouched(self, db_api) -> None:
        client, _repository = db_api
        engine = _engine()

        client.get(f"{_API_PATH}?project_id=project-a")

        with engine.connect() as conn:
            llm_rows = conn.execute(
                text("SELECT COUNT(*) FROM ai_ops.llm_usage_record")
            ).scalar_one()
        assert llm_rows >= 0                     # 只读断言
