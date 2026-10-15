"""M2.3 A2 — Usage / Observability Read API 认证与授权回归测试。

背景：A2 之前，7 个只读端点**匿名可达**：

    GET /api/usage/analytics
    GET /api/observability/tools
    GET /api/observability/tools/metrics
    GET /api/observability/tools/history
    GET /api/observability/tools/metrics/persistent
    GET /api/observability/assistant-trace/{assistant_request_id}
    GET /api/observability/assistant-timeline/{assistant_request_id}

A2 收口为两类访问模型（**不新增 API-Key 旁路**）：

    运维专用全局视图（数据模型无可信项目维度）
        → M1 会话 + 服务端运维白名单（OBSERVABILITY_READ_OPS_USER_IDS）
        → usage/analytics · observability/tools · tools/metrics
          · assistant-trace/{id} · assistant-timeline/{id}

    服务端项目作用域（tool_execution_record.project_id 为可信维度）
        → M1 会话 + 服务端 PROJECT_ID
        → observability/tools/history · tools/metrics/persistent
        → 客户端 project_id 查询参数**已移除**；NULL 项目记录不可见

本文件用**真实** M1 会话装配（``tests/api_session_auth``；Fake 只替换
**会话存储**与**授权配置来源**，``require_session`` / ``require_csrf`` /
``require_observability_ops`` / ``require_observability_project_scope``
本身真实执行，**不**使用 ``dependency_overrides``）。

覆盖（对应实施任务书 §八）：
    1. 七个接口匿名请求均拒绝（401）
    2. 无效 / 过期 / 已撤销 Session 均拒绝（401）
    3. 运维白名单缺失 / 空白 / 无法解析 ⇒ 503（fail closed）
    4. 非白名单用户访问运维接口 ⇒ 403
    5. 有效运维用户可读取原有全局视图（200 + DTO 不变）
    6. A2-04/05 不接受客户端 project_id 扩大访问范围
    7. NULL 项目记录不可见（离线层：作用域恒被应用；DB 层由 *_db 测试覆盖）
    8. Trace / Timeline 受运维授权保护
    9. GET 不因缺少 CSRF 头产生人为回归
   10. 错误响应不泄漏敏感信息
   11. 只读接口不触发 Runtime / Tool / LLM / 写操作
   12. 原有分页、响应 DTO 与字段白名单保持有效

0 DB / 0 Network / 0 Real LLM / 0 DB writes（DB 级断言由
``test_tool_observability_*_db.py`` 覆盖，本文件不触达 PostgreSQL）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.api import assistant_timeline as timeline_module
from backend.app.api import assistant_trace as trace_module
from backend.app.api import tool_observability as tool_api
from backend.app.api import usage as usage_module
from backend.app.api import orchestrator_chat as root
from backend.app.main import app
from backend.app.db.tool_execution_repository import (
    ToolExecutionRepositoryError,
)
from backend.app.db.llm_usage_repository import LLMUsageRepositoryError
from backend.app.services.assistant_timeline_query_service import (
    AssistantTimelineQueryService,
)
from backend.app.services.assistant_trace_query_service import (
    AssistantTraceQueryService,
)
from backend.app.services.llm_usage_query_service import (
    LLMUsageTraceRecordView,
)
from backend.app.services.tool_execution_persistent_query_service import (
    ToolExecutionPersistentQueryService,
)
from backend.app.services.tool_execution_record import ToolExecutionRecord
from backend.app.services.tool_execution_metrics_service import (
    ToolExecutionMetricsService,
    ToolExecutionMetricsSnapshot,
)
from backend.app.services.tool_observability_snapshot import (
    ToolExecutionSnapshot,
)
from tests.api_session_auth import (
    FakeSessionService,
    SessionTestClient,
    install_fake_session_service,
    install_observability_ops_allowlist,
    install_observability_project_scope,
)
from tests.conversation_auth_support import USER_IDS

# ---- 七个端点（与实施任务书 §三 一致）----
_USAGE_PATH = "/api/usage/analytics"
_TOOLS_PATH = "/api/observability/tools"
_TOOLS_METRICS_PATH = "/api/observability/tools/metrics"
_HISTORY_PATH = "/api/observability/tools/history"
_PERSISTENT_METRICS_PATH = "/api/observability/tools/metrics/persistent"
_TRACE_PATH = "/api/observability/assistant-trace"
_TIMELINE_PATH = "/api/observability/assistant-timeline"

#: A2 运维专用端点（Session + 运维白名单）——HTTP 请求路径。
_OPS_ENDPOINTS = (
    _USAGE_PATH,
    _TOOLS_PATH,
    _TOOLS_METRICS_PATH,
    f"{_TRACE_PATH}/A",
    f"{_TIMELINE_PATH}/A",
)

#: A2 运维专用端点的 OpenAPI 模板路径（含路径参数）。
_OPS_OPENAPI_PATHS = (
    _USAGE_PATH,
    _TOOLS_PATH,
    _TOOLS_METRICS_PATH,
    f"{_TRACE_PATH}/{{assistant_request_id}}",
    f"{_TIMELINE_PATH}/{{assistant_request_id}}",
)

#: A2 服务端项目作用域端点（Session + PROJECT_ID）。
_PROJECT_SCOPED_ENDPOINTS = (
    _HISTORY_PATH,
    _PERSISTENT_METRICS_PATH,
)

#: 服务端部署项目（与 observability_project_scope_session 注入值一致）。
_SERVER_PROJECT_ID = "vietnam-wms"

_STARTED = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)


# ============================================================
# 客户端装配
# ============================================================

@pytest.fixture()
def anonymous_client() -> TestClient:
    """匿名客户端：无会话 Cookie、无 CSRF 头。"""
    return TestClient(app)


@pytest.fixture()
def session_client(monkeypatch: pytest.MonkeyPatch) -> SessionTestClient:
    """已登录但**不在**运维白名单（运维白名单配置为不含 alice）。"""
    install_fake_session_service(monkeypatch)
    install_observability_ops_allowlist(monkeypatch, {USER_IDS["bob"]})
    return SessionTestClient(app)


@pytest.fixture()
def ops_client(monkeypatch: pytest.MonkeyPatch) -> SessionTestClient:
    """已登录且**在**运维白名单（alice）。"""
    install_fake_session_service(monkeypatch)
    install_observability_ops_allowlist(monkeypatch, {USER_IDS["alice"]})
    return SessionTestClient(app)


@pytest.fixture()
def project_scope_client(monkeypatch: pytest.MonkeyPatch) -> SessionTestClient:
    """已登录 + 服务端项目作用域（history / persistent metrics）。"""
    install_fake_session_service(monkeypatch)
    install_observability_project_scope(monkeypatch, _SERVER_PROJECT_ID)
    return SessionTestClient(app)


@pytest.fixture()
def fake_session(monkeypatch: pytest.MonkeyPatch) -> FakeSessionService:
    """仅安装 Fake 会话存储（供 401/503 用例自建客户端）。"""
    return install_fake_session_service(monkeypatch)


# ============================================================
# Fake 读边界（零 DB）
# ============================================================

class _FakeUsageFacade:
    """Usage Analytics 读边界替身（记录调用；不触达 DB）。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.snapshot_calls: list[Any] = []
        self._error = error

    async def query_snapshot(self, query_filter: Any) -> Any:
        self.snapshot_calls.append(query_filter)
        if self._error is not None:
            raise self._error
        from backend.app.services.llm_usage_analytics_service import (
            LLMUsageAnalyticsSnapshot,
        )
        from backend.app.services.llm_usage_aggregation_service import (
            LLMUsageAggregate,
        )

        return LLMUsageAnalyticsSnapshot(
            total=LLMUsageAggregate(
                total_requests=1,
                prompt_tokens=11,
                completion_tokens=22,
                total_tokens=33,
                prompt_tokens_known=True,
                completion_tokens_known=True,
                total_tokens_known=True,
            ),
            by_provider=(),
            by_model=(),
            by_provider_model=(),
        )


class _FakePersistentQueryService:
    """Tool 持久读边界替身：记录收到的 project_id / 过滤条件。"""

    def __init__(self) -> None:
        self.history_calls: list[dict[str, Any]] = []
        self.metrics_calls: list[dict[str, Any]] = []

    def list_recent(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        project_id: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> list[ToolExecutionSnapshot]:
        self.history_calls.append({
            "limit": limit,
            "offset": offset,
            "project_id": project_id,
            "tool_name": tool_name,
            "success": success,
        })
        return []

    def metrics(
        self,
        *,
        project_id: str | None = None,
        tool_name: str | None = None,
        success: bool | None = None,
    ) -> ToolExecutionMetricsSnapshot:
        self.metrics_calls.append({
            "project_id": project_id,
            "tool_name": tool_name,
            "success": success,
        })
        return ToolExecutionMetricsService.snapshot(())


class _FakeLlmQuery:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []

    def list_by_assistant_request_id(
        self, assistant_request_id: str
    ) -> list[LLMUsageTraceRecordView]:
        return list(self._rows)


class _FakeToolQuery:
    """Tool 读边界替身（Trace 段：作为 PersistentQueryService 的 repository）。"""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []

    def get_by_request_id(self, request_id: str) -> list[Any]:
        return list(self._rows)

    def list_by_request_id(self, request_id: str) -> list[Any]:
        return list(self._rows)


class _FakeToolRowQuery:
    """Tool 读边界替身（Timeline 段用 ``list_rows_by_request_id``）。"""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self._rows = rows or []

    def list_rows_by_request_id(self, request_id: str) -> list[Any]:
        return list(self._rows)


class _FakeRagQuery:
    def list_by_request_id(self, request_id: str) -> list[Any]:
        return []


class _FakeOutcomeQuery:
    """Trace 段 outcome 读边界替身（``get_by_assistant_request_id``）。"""

    def get_by_assistant_request_id(self, assistant_request_id: str) -> Any:
        return None


class _FakeOutcomeRowQuery:
    """Timeline 段 outcome 读边界替身（``get_row_by_assistant_request_id``）。"""

    def get_row_by_assistant_request_id(
        self, assistant_request_id: str
    ) -> Any | None:
        return None


def _llm_view(**overrides: Any) -> LLMUsageTraceRecordView:
    values: dict[str, Any] = {
        "id": 1,
        "assistant_request_id": "A",
        "request_id": "chatcmpl-A2",
        "provider": "deepseek-test",
        "model": "deepseek-chat",
        "prompt_tokens": 11,
        "completion_tokens": 22,
        "total_tokens": 33,
        "created_at": _STARTED,
    }
    values.update(overrides)
    return LLMUsageTraceRecordView(**values)


def _tool_record(**overrides: Any) -> ToolExecutionRecord:
    values: dict[str, Any] = {
        "request_id": "req-a2",
        "round": 1,
        "tool_name": "get_inventory",
        "started_at": _STARTED,
        "finished_at": _STARTED + timedelta(milliseconds=5),
        "duration_ms": 5.0,
        "success": True,
        "project_id": _SERVER_PROJECT_ID,
        "tool_call_id": None,
        "error_code": None,
        "error_type": None,
    }
    values.update(overrides)
    return ToolExecutionRecord(**values)


def _install_usage_facade(
    monkeypatch: pytest.MonkeyPatch, *, error: Exception | None = None
) -> _FakeUsageFacade:
    facade = _FakeUsageFacade(error=error)
    monkeypatch.setattr(usage_module, "_usage_facade", facade)
    return facade


def _install_trace_service(
    monkeypatch: pytest.MonkeyPatch, *, error: Exception | None = None
) -> None:
    """装配真实 AssistantTraceQueryService + 全 Fake 读边界（零 DB）。"""
    if error is not None:
        class _Exploding:
            def get_trace(self, assistant_request_id: str) -> Any:
                raise error

        monkeypatch.setattr(
            trace_module, "get_assistant_trace_query_service", _Exploding
        )
        return

    service = AssistantTraceQueryService(
        llm_usage_query_service=_FakeLlmQuery([_llm_view()]),
        tool_observability_query_service=ToolExecutionPersistentQueryService(
            repository=_FakeToolQuery()
        ),
        rag_execution_query_service=_FakeRagQuery(),
        outcome_query_service=_FakeOutcomeQuery(),
    )
    monkeypatch.setattr(
        trace_module, "get_assistant_trace_query_service", lambda: service
    )


def _install_timeline_service(
    monkeypatch: pytest.MonkeyPatch, *, error: Exception | None = None
) -> None:
    if error is not None:
        class _Exploding:
            def get_timeline(self, assistant_request_id: str) -> Any:
                raise error

        monkeypatch.setattr(
            timeline_module,
            "get_assistant_timeline_query_service",
            _Exploding,
        )
        return

    service = AssistantTimelineQueryService(
        llm_usage_query_service=_FakeLlmQuery([_llm_view()]),
        tool_execution_query_service=_FakeToolRowQuery(),
        rag_execution_query_service=_FakeRagQuery(),
        outcome_query_service=_FakeOutcomeRowQuery(),
    )
    monkeypatch.setattr(
        timeline_module,
        "get_assistant_timeline_query_service",
        lambda: service,
    )


def _install_persistent_service(
    monkeypatch: pytest.MonkeyPatch, *, error: Exception | None = None
) -> _FakePersistentQueryService:
    service = _FakePersistentQueryService()
    if error is not None:

        def _raise() -> Any:
            raise error

        monkeypatch.setattr(
            tool_api, "get_tool_execution_persistent_query_service", _raise
        )
        return service
    monkeypatch.setattr(
        tool_api,
        "get_tool_execution_persistent_query_service",
        lambda: service,
    )
    return service


# ============================================================
# 1. 七个接口匿名请求均拒绝
# ============================================================

class TestAnonymousDenied:
    @pytest.mark.parametrize("path", _OPS_ENDPOINTS)
    def test_anonymous_denied_on_ops_endpoints(
        self, anonymous_client: TestClient, path: str
    ) -> None:
        response = anonymous_client.get(path)

        assert response.status_code == 401
        assert "detail" in response.json()

    @pytest.mark.parametrize("path", _PROJECT_SCOPED_ENDPOINTS)
    def test_anonymous_denied_on_project_scoped_endpoints(
        self, anonymous_client: TestClient, path: str
    ) -> None:
        response = anonymous_client.get(path)

        assert response.status_code == 401

    def test_anonymous_denied_even_with_project_query(
        self, anonymous_client: TestClient
    ) -> None:
        """匿名 + 任意 project_id 也不能读到任何数据。"""
        response = anonymous_client.get(
            f"{_HISTORY_PATH}?project_id={_SERVER_PROJECT_ID}"
        )

        assert response.status_code == 401

    def test_no_read_happens_before_auth(
        self, anonymous_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """认证前零业务执行：Facade / QueryService 一次都不被调用。"""
        facade = _install_usage_facade(monkeypatch)
        persistent = _install_persistent_service(monkeypatch)

        anonymous_client.get(_USAGE_PATH)
        anonymous_client.get(_HISTORY_PATH)
        anonymous_client.get(_PERSISTENT_METRICS_PATH)

        assert facade.snapshot_calls == []
        assert persistent.history_calls == []
        assert persistent.metrics_calls == []


# ============================================================
# 2. 无效 / 过期 / 已撤销 Session
# ============================================================

class TestInvalidSessionDenied:
    def _ops_client(self, monkeypatch: pytest.MonkeyPatch) -> SessionTestClient:
        install_fake_session_service(monkeypatch)
        install_observability_ops_allowlist(monkeypatch, {USER_IDS["alice"]})
        return SessionTestClient(app)

    def test_bogus_cookie_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._ops_client(monkeypatch)
        client.cookies.set("wms_ai_session", "token-does-not-exist")

        response = client.get(_USAGE_PATH)

        assert response.status_code == 401

    def test_expired_session_is_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self._ops_client(monkeypatch)
        monkeypatch  # noqa: B018 —— 装配已由 _ops_client 完成
        from backend.app.api import auth_dependency as auth_dep

        auth_dep.get_session_service().expire(client.session_token)

        assert client.get(_TOOLS_PATH).status_code == 401

    def test_revoked_session_is_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self._ops_client(monkeypatch)
        from backend.app.api import auth_dependency as auth_dep

        auth_dep.get_session_service().logout(client.session_token)

        assert client.get(f"{_TRACE_PATH}/A").status_code == 401

    def test_blank_cookie_is_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = self._ops_client(monkeypatch)
        client.cookies.set("wms_ai_session", "   ")

        assert client.get(_TOOLS_METRICS_PATH).status_code == 401

    def test_invalid_session_denied_on_project_scoped_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        install_observability_project_scope(monkeypatch, _SERVER_PROJECT_ID)
        client = SessionTestClient(app)
        client.cookies.set("wms_ai_session", "token-does-not-exist")

        assert client.get(_HISTORY_PATH).status_code == 401


# ============================================================
# 3. 运维白名单 fail closed（503）
# ============================================================

class TestOpsAllowlistFailClosed:
    @pytest.mark.parametrize(
        "raw",
        (
            "",
            "   ",
            "a,,b",          # 含空条目
            "trailing,",      # 尾逗号 ⇒ 空条目
            ",",
        ),
    )
    def test_unusable_allowlist_is_503(
        self,
        monkeypatch: pytest.MonkeyPatch,
        raw: str,
    ) -> None:
        from backend.app.api import observability_read_auth as auth_module
        from backend.app.config import ObservabilityReadSettings

        install_fake_session_service(monkeypatch)
        monkeypatch.setattr(
            auth_module,
            "get_observability_read_settings",
            lambda: ObservabilityReadSettings(ops_user_ids=raw),
        )
        client = SessionTestClient(app)

        response = client.get(_USAGE_PATH)

        assert response.status_code == 503
        # 固定文案：不回显白名单内容 / 配置细节
        assert response.json()["detail"] == "运维读取授权未配置（服务不可用）"

    def test_parse_rejects_blank_and_oversized_entries(self) -> None:
        from backend.app.api.observability_read_auth import (
            OPS_USER_ID_MAX_LENGTH,
            OPS_USER_IDS_MAX_ENTRIES,
            parse_ops_user_ids,
        )

        assert parse_ops_user_ids("a,b,a") == frozenset({"a", "b"})
        for bad in ("", "   ", "a,,b", "a,", ",", " , "):
            with pytest.raises(ValueError):
                parse_ops_user_ids(bad)
        with pytest.raises(ValueError):
            parse_ops_user_ids("x" * (OPS_USER_ID_MAX_LENGTH + 1))
        with pytest.raises(ValueError):
            parse_ops_user_ids(
                ",".join(f"u{i}" for i in range(OPS_USER_IDS_MAX_ENTRIES + 1))
            )

    def test_missing_project_scope_is_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.app.api import observability_read_auth as auth_module

        install_fake_session_service(monkeypatch)
        monkeypatch.setattr(
            auth_module, "get_observability_project_id", lambda: "   "
        )
        client = SessionTestClient(app)

        assert client.get(_HISTORY_PATH).status_code == 503
        assert client.get(_PERSISTENT_METRICS_PATH).status_code == 503

    def test_scope_missing_in_request_state_is_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """端点体在依赖未注入作用域时 fail closed（不静默放行）。"""
        from fastapi import Request

        from backend.app.api.observability_read_auth import (
            get_observability_project_scope,
        )

        request = Request({"type": "http", "headers": []})
        with pytest.raises(Exception) as excinfo:  # noqa: B017
            get_observability_project_scope(request)
        assert "503" in str(excinfo.value) or "部署项目作用域" in str(
            excinfo.value
        )


# ============================================================
# 4. 非白名单用户 ⇒ 403
# ============================================================

class TestNonOpsUserForbidden:
    @pytest.mark.parametrize("path", _OPS_ENDPOINTS)
    def test_authenticated_non_ops_is_403(
        self, session_client: SessionTestClient, path: str
    ) -> None:
        response = session_client.get(path)

        assert response.status_code == 403
        assert response.json()["detail"] == "权限不足：需要运维读取授权"

    def test_403_does_not_leak_allowlist(
        self, session_client: SessionTestClient
    ) -> None:
        response = session_client.get(_USAGE_PATH)
        body = response.text

        # 白名单内容（bob 的 user_id）不得出现在响应中
        assert USER_IDS["bob"] not in body
        assert "ops_user_ids" not in body
        assert "OBSERVABILITY_READ_OPS_USER_IDS" not in body

    def test_non_ops_user_still_can_use_project_scoped_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """项目作用域端点只要求会话（不要求运维白名单）。"""
        install_fake_session_service(monkeypatch)
        install_observability_project_scope(monkeypatch, _SERVER_PROJECT_ID)
        persistent = _install_persistent_service(monkeypatch)
        client = SessionTestClient(app)

        response = client.get(_HISTORY_PATH)

        assert response.status_code == 200
        assert persistent.history_calls[0]["project_id"] == _SERVER_PROJECT_ID


# ============================================================
# 5. 有效运维用户可读取原有全局视图（DTO 不变）
# ============================================================

class TestOpsPositivePath:
    def test_usage_analytics_returns_original_contract(
        self, ops_client: SessionTestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_usage_facade(monkeypatch)

        response = ops_client.get(_USAGE_PATH, params={"limit": 50})

        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"total", "by_provider", "by_model",
                                "by_provider_model"}
        assert payload["total"]["total_requests"] == 1
        assert payload["total"]["total_tokens"] == 33

    def test_tools_runtime_returns_original_contract(
        self, ops_client: SessionTestClient
    ) -> None:
        root._TOOL_EXECUTION_COLLECTOR.clear()
        try:
            root._TOOL_EXECUTION_COLLECTOR.on_execution(_tool_record())
            response = ops_client.get(_TOOLS_PATH)
        finally:
            root._TOOL_EXECUTION_COLLECTOR.clear()

        assert response.status_code == 200
        records = response.json()["records"]
        assert len(records) == 1
        assert set(records[0]) == {
            "request_id", "round", "tool_name", "started_at", "finished_at",
            "duration_ms", "success", "project_id", "tool_call_id",
            "error_code", "error_type",
        }

    def test_tools_metrics_returns_original_contract(
        self, ops_client: SessionTestClient
    ) -> None:
        root._TOOL_EXECUTION_COLLECTOR.clear()
        try:
            root._TOOL_EXECUTION_COLLECTOR.on_execution(_tool_record())
            response = ops_client.get(_TOOLS_METRICS_PATH)
        finally:
            root._TOOL_EXECUTION_COLLECTOR.clear()

        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {
            "total_count", "success_count", "failure_count", "success_rate",
            "failure_rate", "total_duration_ms", "average_duration_ms",
            "max_duration_ms",
        }
        assert payload["total_count"] == 1

    def test_assistant_trace_returns_original_contract(
        self, ops_client: SessionTestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_trace_service(monkeypatch)

        response = ops_client.get(f"{_TRACE_PATH}/A")

        assert response.status_code == 200
        payload = response.json()
        assert payload["assistant_request_id"] == "A"
        assert len(payload["llm_usage"]) == 1
        assert payload["llm_usage"][0]["model"] == "deepseek-chat"
        assert payload["tool_executions"] == []
        assert payload["rag_executions"] == []
        assert payload["outcome"] is None

    def test_assistant_timeline_returns_original_contract(
        self, ops_client: SessionTestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_timeline_service(monkeypatch)

        response = ops_client.get(f"{_TIMELINE_PATH}/A")

        assert response.status_code == 200
        payload = response.json()
        assert payload["assistant_request_id"] == "A"
        assert set(payload) == {
            "assistant_request_id", "llm_events", "tool_events",
            "rag_events", "outcome_event",
        }
        assert payload["outcome_event"] is None

    def test_history_pagination_contract_preserved(
        self,
        project_scope_client: SessionTestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        persistent = _install_persistent_service(monkeypatch)

        response = project_scope_client.get(
            f"{_HISTORY_PATH}?limit=20&offset=5&tool_name=get_inventory"
            "&success=true"
        )

        assert response.status_code == 200
        assert response.json() == {"items": [], "limit": 20, "offset": 5}
        assert persistent.history_calls == [{
            "limit": 20,
            "offset": 5,
            "project_id": _SERVER_PROJECT_ID,
            "tool_name": "get_inventory",
            "success": True,
        }]

    def test_persistent_metrics_contract_preserved(
        self,
        project_scope_client: SessionTestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        persistent = _install_persistent_service(monkeypatch)

        response = project_scope_client.get(
            f"{_PERSISTENT_METRICS_PATH}?tool_name=get_inventory&success=true"
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["total_count"] == 0
        assert payload["success_rate"] is None      # None ≠ 0
        assert persistent.metrics_calls == [{
            "project_id": _SERVER_PROJECT_ID,
            "tool_name": "get_inventory",
            "success": True,
        }]


# ============================================================
# 6. 客户端 project_id 不能扩大访问范围
# ============================================================

class TestClientProjectIdCannotWidenScope:
    @pytest.mark.parametrize(
        "claimed",
        (_SERVER_PROJECT_ID, "other-project", "", "' OR 1=1 --"),
    )
    def test_history_ignores_client_project_id(
        self,
        project_scope_client: SessionTestClient,
        monkeypatch: pytest.MonkeyPatch,
        claimed: str,
    ) -> None:
        persistent = _install_persistent_service(monkeypatch)

        response = project_scope_client.get(
            f"{_HISTORY_PATH}?project_id={claimed}"
        )

        assert response.status_code == 200
        assert persistent.history_calls[0]["project_id"] == _SERVER_PROJECT_ID

    @pytest.mark.parametrize(
        "claimed",
        (_SERVER_PROJECT_ID, "other-project", "", "' OR 1=1 --"),
    )
    def test_persistent_metrics_ignores_client_project_id(
        self,
        project_scope_client: SessionTestClient,
        monkeypatch: pytest.MonkeyPatch,
        claimed: str,
    ) -> None:
        persistent = _install_persistent_service(monkeypatch)

        response = project_scope_client.get(
            f"{_PERSISTENT_METRICS_PATH}?project_id={claimed}"
        )

        assert response.status_code == 200
        assert persistent.metrics_calls[0]["project_id"] == _SERVER_PROJECT_ID

    def test_handlers_do_not_expose_project_id_parameter(self) -> None:
        """A2：project_id 不再是客户端 query 参数（契约级断言）。"""
        import inspect

        history_params = list(
            inspect.signature(tool_api.list_tool_execution_history).parameters
        )
        metrics_params = list(
            inspect.signature(
                tool_api.persistent_tool_execution_metrics
            ).parameters
        )

        assert "project_id" not in history_params
        assert "project_id" not in metrics_params

    def test_openapi_has_no_project_id_query_parameter(self) -> None:
        spec = app.openapi()

        for path in (_HISTORY_PATH, _PERSISTENT_METRICS_PATH):
            parameters = spec["paths"][path]["get"].get("parameters", [])
            names = {p["name"] for p in parameters}
            assert "project_id" not in names, path


# ============================================================
# 7. NULL 项目记录不可见（离线层：作用域恒被应用）
# ============================================================

class TestNullProjectScope:
    def test_scope_is_always_a_non_null_project(
        self,
        project_scope_client: SessionTestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """作用域恒为非空项目 ⇒ ``project_id IS NULL`` 记录永不匹配。"""
        persistent = _install_persistent_service(monkeypatch)

        project_scope_client.get(_HISTORY_PATH)
        project_scope_client.get(_PERSISTENT_METRICS_PATH)

        for call in (*persistent.history_calls, *persistent.metrics_calls):
            assert call["project_id"] == _SERVER_PROJECT_ID
            assert call["project_id"] is not None

    def test_db_level_null_project_visibility_is_covered_elsewhere(self) -> None:
        """DB 级断言见 tests/test_tool_observability_history_api_db.py::
        TestPersistentHistoryApiDb::test_null_project_record_is_invisible。"""
        from pathlib import Path

        source = Path(
            "tests/test_tool_observability_history_api_db.py"
        ).read_text(encoding="utf-8")
        assert "test_null_project_record_is_invisible" in source


# ============================================================
# 8. Trace / Timeline 运维授权保护
# ============================================================

class TestTraceTimelineOpsProtection:
    @pytest.mark.parametrize("path", (f"{_TRACE_PATH}/A", f"{_TIMELINE_PATH}/A"))
    def test_trace_and_timeline_require_ops(
        self, session_client: SessionTestClient, path: str
    ) -> None:
        assert session_client.get(path).status_code == 403

    def test_knowing_request_id_is_not_enough(
        self, anonymous_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """“知道 assistant_request_id 即可读”不再成立。"""
        _install_trace_service(monkeypatch)
        _install_timeline_service(monkeypatch)

        assert anonymous_client.get(f"{_TRACE_PATH}/A").status_code == 401
        assert anonymous_client.get(f"{_TIMELINE_PATH}/A").status_code == 401

    def test_trace_service_not_called_when_forbidden(
        self, session_client: SessionTestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """403 时零数据读取（不返回部分结果）。"""
        calls: list[str] = []

        class _Counting:
            def get_trace(self, assistant_request_id: str) -> Any:
                calls.append(assistant_request_id)
                return None

        monkeypatch.setattr(
            trace_module, "get_assistant_trace_query_service", _Counting
        )

        assert session_client.get(f"{_TRACE_PATH}/A").status_code == 403
        assert calls == []


# ============================================================
# 9. GET 不因缺少 CSRF 头产生人为回归
# ============================================================

class TestGetNotBlockedByCsrf:
    def test_get_without_csrf_header_is_200(
        self, ops_client: SessionTestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_usage_facade(monkeypatch)

        response = ops_client.get(_USAGE_PATH)

        assert response.status_code == 200
        assert "x-csrf-token" not in {
            key.lower() for key in response.request.headers
        }

    def test_get_with_wrong_csrf_header_is_still_200(
        self, ops_client: SessionTestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET 是安全方法：CSRF 校验不适用（不制造人为 403）。"""
        _install_usage_facade(monkeypatch)

        response = ops_client.get(
            _USAGE_PATH, headers={"X-CSRF-Token": "totally-wrong"}
        )

        assert response.status_code == 200

    def test_project_scoped_get_without_csrf_is_200(
        self,
        project_scope_client: SessionTestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_persistent_service(monkeypatch)

        response = project_scope_client.get(_HISTORY_PATH)

        assert response.status_code == 200


# ============================================================
# 10. 错误响应不泄漏敏感信息
# ============================================================

class TestErrorResponseLeakage:
    def test_usage_repository_error_hides_internals(
        self, ops_client: SessionTestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_usage_facade(
            monkeypatch,
            error=LLMUsageRepositoryError(
                "SELECT * FROM ai_ops.llm_usage_record "
                "postgresql://user:secret@host:5432/db"
            ),
        )

        response = ops_client.get(_USAGE_PATH)
        text = response.text.upper()

        assert response.status_code == 502
        for forbidden in (
            "SELECT", "POSTGRESQL://", "SECRET", "TRACEBACK",
            "AI_OPS", "LLM_USAGE_RECORD",
        ):
            assert forbidden not in text, forbidden

    def test_trace_repository_error_hides_internals(
        self, ops_client: SessionTestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_trace_service(
            monkeypatch,
            error=RuntimeError("internal boom /ai_ops/secret-path"),
        )

        response = ops_client.get(f"{_TRACE_PATH}/A")

        assert response.status_code == 500
        assert response.json()["detail"] == "助手链路观测数据不可用"

    def test_history_repository_error_hides_internals(
        self,
        project_scope_client: SessionTestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install_persistent_service(
            monkeypatch,
            error=ToolExecutionRepositoryError(
                "Tool Execution 查询失败: OperationalError "
                "postgresql://user:secret@host/db"
            ),
        )

        response = project_scope_client.get(_HISTORY_PATH)

        assert response.status_code == 502
        assert response.json()["detail"] == "Tool 观测历史数据不可用"


# ============================================================
# 11. 只读接口不触发执行 / 写操作
# ============================================================

class TestReadOnlyNoSideEffects:
    def test_reads_do_not_mutate_collector(
        self, ops_client: SessionTestClient
    ) -> None:
        root._TOOL_EXECUTION_COLLECTOR.clear()
        try:
            root._TOOL_EXECUTION_COLLECTOR.on_execution(_tool_record())
            before = root._TOOL_EXECUTION_COLLECTOR.records()

            ops_client.get(_TOOLS_PATH)
            ops_client.get(_TOOLS_METRICS_PATH)

            assert root._TOOL_EXECUTION_COLLECTOR.records() == before
        finally:
            root._TOOL_EXECUTION_COLLECTOR.clear()

    def test_usage_facade_called_exactly_once(
        self, ops_client: SessionTestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        facade = _install_usage_facade(monkeypatch)

        ops_client.get(_USAGE_PATH)

        assert len(facade.snapshot_calls) == 1

    def test_no_write_endpoints_registered(self) -> None:
        """七个端点全部是 GET（无 POST / PUT / PATCH / DELETE）。"""
        spec = app.openapi()

        for path in (*_OPS_OPENAPI_PATHS, _HISTORY_PATH,
                     _PERSISTENT_METRICS_PATH):
            methods = set(spec["paths"][path])
            assert methods == {"get"}, path

    def test_openapi_declares_auth_responses(self) -> None:
        """OpenAPI 显式声明 401/403/503（运维面）与 401/503（项目面）。"""
        spec = app.openapi()

        for path in _OPS_OPENAPI_PATHS:
            responses = spec["paths"][path]["get"]["responses"]
            assert {"401", "403", "503"} <= set(responses), path
        for path in _PROJECT_SCOPED_ENDPOINTS:
            responses = spec["paths"][path]["get"]["responses"]
            assert {"401", "503"} <= set(responses), path


# ============================================================
# 12. 路由集合未被改变（冻结 6 路由契约旁证）
# ============================================================

class TestRouteSetUnchanged:
    def test_seven_paths_still_registered(self) -> None:
        spec = app.openapi()

        for path in (
            _USAGE_PATH, _TOOLS_PATH, _TOOLS_METRICS_PATH, _HISTORY_PATH,
            _PERSISTENT_METRICS_PATH, *_OPS_OPENAPI_PATHS[3:],
        ):
            assert path in spec["paths"], path

    def test_handler_signatures_of_ops_endpoints_unchanged(self) -> None:
        """运维端点用装饰器级依赖 ⇒ Handler 签名未新增参数。"""
        import inspect

        assert list(
            inspect.signature(usage_module.usage_analytics).parameters
        ) == ["params"]
        assert list(
            inspect.signature(tool_api.list_tool_executions).parameters
        ) == []
        assert list(
            inspect.signature(tool_api.tool_execution_metrics).parameters
        ) == []
        assert list(
            inspect.signature(trace_module.get_assistant_trace).parameters
        ) == ["assistant_request_id"]
        assert list(
            inspect.signature(timeline_module.get_assistant_timeline).parameters
        ) == ["assistant_request_id"]
