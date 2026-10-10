"""Phase 6 Step 8A — Agent Read API **认证与项目作用域安全测试**（离线 + DB-gated）。

背景：Project Recovery Audit R1 —— Phase 5 的 Agent Read API 两个只读端点
无任何认证（客户端可任意主张 ``project_id``；缺省回退项目默认）。本文件
验证 S8A 的最小修复（**安全例外解冻**：仅访问控制，其余契约不变），
组织上与 ``tests/test_workflow_read_api_security_audit.py``（S7.2）对称。

覆盖（S8 任务书 §4.4）：

```text
1  缺少凭据 ⇒ 401（两端点）
2  错误凭据 ⇒ 401（两端点）
3  未配置 key / 未配置作用域 / 空白配置 ⇒ 503（fail closed）
4  认证先于数据读取（ExplodingService 证明零 service 调用）
5  正确凭据 + 授权项目 ⇒ 200（两端点；字段集与 P-8 一致）
6  未授权项目（他项目数据）⇒ 404；越权 = 不存在同构（不泄露存在性）
7  客户端 project_id 不能扩权 / 不能切换作用域
8  未配置作用域不回退项目默认（数据属项目默认亦 503）
9  401 与资源存在性无关（同 body）
10 响应 / 日志不出现 API Key（成功 + 401 + 503 三路径）
11 OpenAPI：X-API-Key scheme + 两端点 security 声明 + 与运行时一致

DB-gated（RUN_DB_TESTS=1；真 PostgreSQL + 真 Repository 造数 + 真链路由）：
12 授权项目真实 Run ⇒ 200；他项目真实 Run ⇒ 404（同构）；无凭据 ⇒ 401
13 本文件写入的 6 张 agent 表清理后 residue = 0
```

**不**触碰 Agent Runtime / 决策 / Tool 授权 / Checkpoint 语义（S8 禁止项）。
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from backend.app.api import agent_read as agent_read_module
from backend.app.api import agent_read_auth as agent_auth_module
from backend.app.config import AgentReadSettings, settings
from backend.app.dto.agent_read_api import AGENT_RUN_RESPONSE_FIELDS
from backend.app.main import app
from tests.test_agent_read_service import (
    FakeApprovalRepository,
    FakeCheckpointRepository,
    FakeDecisionRepository,
    FakeRunRepository,
    FakeStepRepository,
    FakeToolResultRepository,
    approval_read,
    build_service,
    checkpoint_dto,
    decision_record,
    run_record,
    step_read,
    tool_result_read,
)

__all__ = [
    "TestAuthenticationRequired",
    "TestAuthorizationScope",
    "TestLeakageAndLogging",
    "TestOpenApiContract",
    "TestAgentReadApiSecurityDb",
]

_RUN_ID = "run-p8-read"
_AUTHORIZED_PROJECT = "proj-agent-s8a"
_OTHER_PROJECT = "proj-attacker"

#: 测试专用凭据（**非**生产凭据；仅存在于测试进程内 monkeypatch 配置）。
_TEST_API_KEY = "agent-security-suite-key-NOT-A-PRODUCTION-CREDENTIAL"
_HEADERS = {"X-API-Key": _TEST_API_KEY}

_URLS = (
    f"/api/agent/runs/{_RUN_ID}",
    f"/api/agent/runs/{_RUN_ID}/timeline",
)

_ENV_FLAG = os.getenv("RUN_DB_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


# ============================================================
# 离线 fixture（fake service + 可控认证配置）
# ============================================================


def _full_read_service(*, project_id: str = _AUTHORIZED_PROJECT) -> Any:
    from dataclasses import replace

    service, _repositories = build_service(
        run=FakeRunRepository({_RUN_ID: run_record(project_id=project_id)}),
        step=FakeStepRepository([step_read(step_index=0)]),
        decision=FakeDecisionRepository([decision_record(step_index=0)]),
        tool_result=FakeToolResultRepository(
            [replace(tool_result_read(step_index=0), project_id=project_id)]
        ),
        checkpoint=FakeCheckpointRepository({_RUN_ID: checkpoint_dto()}),
        approval=FakeApprovalRepository([approval_read(step_index=0)]),
    )
    return service


class _ExplodingService:
    """任何数据读取调用都视为**认证失效**（强证据：未认证不触服务层）。"""

    def get_run(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("认证必须先于数据读取：get_run 被调用")

    def get_timeline(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("认证必须先于数据读取：get_timeline 被调用")


def _client(
    monkeypatch: pytest.MonkeyPatch,
    service: Any,
    *,
    auth_settings: AgentReadSettings | None = None,
    headers: dict[str, str] | None = None,
) -> TestClient:
    monkeypatch.setattr(
        agent_read_module, "get_agent_read_service", lambda: service
    )
    monkeypatch.setattr(
        agent_auth_module,
        "get_agent_read_settings",
        lambda: auth_settings
        if auth_settings is not None
        else AgentReadSettings(
            api_key=_TEST_API_KEY, api_project_id=_AUTHORIZED_PROJECT
        ),
    )
    client = TestClient(app, headers=headers if headers is not None else _HEADERS)
    return client


# ============================================================
# 1 / 2 / 3 / 4：认证必须存在（fail closed + 先于读取）
# ============================================================


class TestAuthenticationRequired:
    def test_missing_api_key_rejected_on_both_endpoints(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch, _full_read_service(), headers={})
        for url in _URLS:
            response = client.get(url)  # 无 X-API-Key
            assert response.status_code == 401, url
            assert response.json()["detail"] == (
                "认证失败：缺少或无效的 API Key"
            )

    def test_wrong_api_key_rejected_on_both_endpoints(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(
            monkeypatch,
            _full_read_service(),
            headers={"X-API-Key": "wrong-key-value"},
        )
        for url in _URLS:
            assert client.get(url).status_code == 401, url

    def test_unconfigured_api_key_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(
            monkeypatch,
            _full_read_service(),
            auth_settings=AgentReadSettings(
                api_key="", api_project_id=_AUTHORIZED_PROJECT
            ),
        )
        for url in _URLS:
            response = client.get(url)  # 即使带 header
            assert response.status_code == 503, url
            assert response.json()["detail"] == (
                "Agent 读取认证未配置（服务不可用）"
            )

    def test_unconfigured_scope_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(
            monkeypatch,
            _full_read_service(),
            auth_settings=AgentReadSettings(
                api_key=_TEST_API_KEY, api_project_id=""
            ),
        )
        for url in _URLS:
            assert client.get(url).status_code == 503, url

    def test_blank_credentials_count_as_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(
            monkeypatch,
            _full_read_service(),
            auth_settings=AgentReadSettings(
                api_key="   ", api_project_id="   "
            ),
        )
        assert client.get(_URLS[0]).status_code == 503

    def test_auth_precedes_any_data_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """缺凭据 ⇒ 401，且 service **零调用**（ExplodingService 为证）。"""
        client = _client(monkeypatch, _ExplodingService(), headers={})
        for url in _URLS:
            response = client.get(url)
            assert response.status_code == 401, url

    def test_wrong_key_precedes_any_data_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(
            monkeypatch,
            _ExplodingService(),
            headers={"X-API-Key": "wrong-key"},
        )
        assert client.get(_URLS[0]).status_code == 401


# ============================================================
# 5 / 6 / 7 / 8 / 9：授权作用域
# ============================================================


class TestAuthorizationScope:
    def test_authorized_key_reads_authorized_project_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch, _full_read_service())
        for url in _URLS:
            response = client.get(url)
            assert response.status_code == 200, url

    def test_cannot_read_other_project_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(
            monkeypatch, _full_read_service(project_id=_OTHER_PROJECT)
        )
        for url in _URLS:
            assert client.get(url).status_code == 404, url

    def test_unauthorized_equals_missing_isomorphic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """越权资源与不存在资源同构（同码同文案——不泄露存在性）。"""
        client = _client(
            monkeypatch, _full_read_service(project_id=_OTHER_PROJECT)
        )
        outside = client.get(_URLS[0])
        missing = client.get("/api/agent/runs/run-unknown")
        assert outside.status_code == missing.status_code == 404
        assert outside.json() == missing.json()

    def test_client_project_id_cannot_widen_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """客户端主张 `?project_id=<victim>` 不能切换 / 扩宽授权。"""
        victim_client = _client(
            monkeypatch, _full_read_service(project_id=_OTHER_PROJECT)
        )
        clamped = victim_client.get(
            _URLS[0], params={"project_id": _OTHER_PROJECT}
        )
        assert clamped.status_code == 404  # 作用域仍是服务端配置

        own_client = _client(monkeypatch, _full_read_service())
        ignored = own_client.get(
            _URLS[0], params={"project_id": "some-other-project"}
        )
        assert ignored.status_code == 200
        assert ignored.json()["project_id"] == _AUTHORIZED_PROJECT

    def test_no_scope_configured_never_falls_back_to_default_project(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未配置作用域 ⇒ 503，即使数据属项目默认（`vietnam-wms`）。"""
        client = _client(
            monkeypatch,
            _full_read_service(project_id=settings.project.project_id),
            auth_settings=AgentReadSettings(
                api_key=_TEST_API_KEY, api_project_id=""
            ),
        )
        assert client.get(_URLS[0]).status_code == 503

    def test_rejected_request_is_resource_agnostic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """401 与目标资源是否存在无关（同 body——不泄露存在性）。"""
        client = _client(monkeypatch, _full_read_service(), headers={})
        existing = client.get(_URLS[0])
        missing = client.get("/api/agent/runs/run-unknown")
        assert existing.status_code == missing.status_code == 401
        assert existing.json() == missing.json()


# ============================================================
# 10：泄漏（响应 / 日志）
# ============================================================


class TestLeakageAndLogging:
    def test_api_key_never_appears_in_response_or_logs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            ok = _client(monkeypatch, _full_read_service()).get(_URLS[0])
            rejected = _client(
                monkeypatch,
                _full_read_service(),
                headers={"X-API-Key": "wrong-key-BUT-SENSITIVE"},
            ).get(_URLS[0])
            unconfigured = _client(
                monkeypatch,
                _full_read_service(),
                auth_settings=AgentReadSettings(
                    api_key="", api_project_id=_AUTHORIZED_PROJECT
                ),
            ).get(_URLS[0])
        assert ok.status_code == 200
        assert rejected.status_code == 401
        assert unconfigured.status_code == 503
        for response in (ok, rejected, unconfigured):
            assert _TEST_API_KEY not in response.text
            assert "wrong-key-BUT-SENSITIVE" not in response.text
        assert _TEST_API_KEY not in caplog.text
        assert "wrong-key-BUT-SENSITIVE" not in caplog.text
        assert "credential rejected" in caplog.text  # 只记录事实类别

    def test_401_has_www_authenticate_without_secrets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _client(monkeypatch, _full_read_service(), headers={})
        response = client.get(_URLS[0])
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == "X-API-Key"
        assert _TEST_API_KEY not in str(response.headers)


# ============================================================
# 11：OpenAPI 契约 + 响应契约零变化
# ============================================================


class TestOpenApiContract:
    def test_security_scheme_and_endpoint_declarations(self) -> None:
        schema = app.openapi()
        schemes = schema["components"]["securitySchemes"]
        api_key_names = [
            name
            for name, definition in schemes.items()
            if definition.get("type") == "apiKey"
            and definition.get("in") == "header"
            and definition.get("name") == "X-API-Key"
        ]
        assert api_key_names, f"OpenAPI 无 X-API-Key scheme: {schemes!r}"
        scheme_name = api_key_names[0]
        for route in (
            "/api/agent/runs/{run_id}",
            "/api/agent/runs/{run_id}/timeline",
        ):
            requirement = schema["paths"][route]["get"].get("security")
            assert requirement, f"{route} 未声明 security"
            assert any(scheme_name in entry for entry in requirement), (
                f"{route} security 未引用 scheme: {requirement!r}"
            )

    def test_runtime_matches_openapi_declaration(self) -> None:
        """真链（不 monkeypatch 任何 composition root）：无凭据 ⇒ 绝非 200。

        认证依赖在数据读取**之前**解析——本测试不触发 DB 查询；
        无论服务端是否已配置凭据，未认证请求只能是 401 或 503。
        """
        client = TestClient(app, raise_server_exceptions=False)
        for url in _URLS:
            response = client.get(url)
            assert response.status_code in (401, 503), url

    def test_authorized_response_contract_is_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S8A 是安全例外解冻：200 响应字段集与 P-8 契约**完全一致**。"""
        response = _client(monkeypatch, _full_read_service()).get(_URLS[0])
        assert response.status_code == 200
        assert set(response.json()) == set(AGENT_RUN_RESPONSE_FIELDS)


# ============================================================
# 12 / 13：DB-gated 真链（真 PostgreSQL；真 Repository 造数）
# ============================================================


@pytest.mark.skipif(
    not _ENV_FLAG,
    reason="set RUN_DB_TESTS=1 to enable agent read API security PostgreSQL tests",
)
class TestAgentReadApiSecurityDb:
    _PREFIX = "run-p6s8a-"

    @staticmethod
    def _run_id() -> str:
        return f"{TestAgentReadApiSecurityDb._PREFIX}{uuid.uuid4().hex[:12]}"

    @pytest.fixture(scope="class")
    def engine(self) -> Any:
        from backend.app.db.session import get_engine

        resolved = get_engine()
        if resolved is None:
            pytest.skip("DATABASE_URL 未配置，跳过 Agent Read API 安全 DB 测试")
        return resolved

    @pytest.fixture(scope="class", autouse=True)
    def _ensure_schema(self, engine: Any) -> Iterator[None]:
        from backend.app.db.base import Base
        from backend.app.db.models import AgentRun as AgentRunModel
        from backend.app.db.models import (
            AgentToolResult as AgentToolResultModel,
        )

        Base.metadata.create_all(
            bind=engine,
            tables=[
                AgentRunModel.__table__,
                AgentToolResultModel.__table__,
            ],
            checkfirst=True,
        )
        yield

    def _client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        """真链：不 monkeypatch service（生产装配 → 真 Repository → DB）。"""
        monkeypatch.setattr(
            agent_auth_module,
            "get_agent_read_settings",
            lambda: AgentReadSettings(
                api_key=_TEST_API_KEY, api_project_id=_AUTHORIZED_PROJECT
            ),
        )
        return TestClient(app, headers=_HEADERS)

    def _seed_run(self, run_id: str, project_id: str) -> None:
        from backend.app.db.agent_run_repository import AgentRunRepository
        from backend.app.db.agent_tool_result_repository import (
            AgentToolResultRepository,
            AgentToolResultRow,
        )
        from tests.test_agent_runtime import _TOOL_NAME

        now = datetime.now(timezone.utc)
        AgentRunRepository().create(
            run_id=run_id,
            request_id=f"req-{run_id}",
            project_id=project_id,
            status="COMPLETED",
            error_code=None,
            limit_reason=None,
            step_count=1,
            tool_call_count=1,
            llm_call_count=1,
            token_usage_unverifiable=False,
            started_at=now,
            finished_at=now,
            duration_ms=1.0,
        )
        AgentToolResultRepository().create(
            AgentToolResultRow(
                run_id=run_id,
                step_index=0,
                tool_name=_TOOL_NAME,
                success=True,
                result_size_bytes=1,
                result_truncated=False,
                result_unavailable=False,
                project_id=project_id,
                tool_call_id="call-seed",
            )
        )

    @pytest.fixture(scope="class", autouse=True)
    def _cleanup(self, engine: Any) -> Iterator[None]:
        yield
        self._delete_prefix(engine)

    @classmethod
    def _delete_prefix(cls, engine: Any) -> None:
        from sqlalchemy import text

        with engine.begin() as connection:
            for table in (
                "agent_run",
                "agent_step",
                "agent_decision",
                "agent_tool_result",
                "agent_checkpoint",
                "agent_approval",
            ):
                connection.execute(
                    text(
                        f"DELETE FROM ai_ops.{table} "
                        "WHERE run_id LIKE :prefix"
                    ),
                    {"prefix": f"{cls._PREFIX}%"},
                )

    def test_authorized_run_readable_via_real_chain(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = self._run_id()
        self._seed_run(run_id, _AUTHORIZED_PROJECT)
        client = self._client(monkeypatch)

        response = client.get(f"/api/agent/runs/{run_id}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["run_id"] == run_id
        assert payload["project_id"] == _AUTHORIZED_PROJECT
        assert set(payload) == set(AGENT_RUN_RESPONSE_FIELDS)
        timeline = client.get(f"/api/agent/runs/{run_id}/timeline")
        assert timeline.status_code == 200

    def test_cross_project_run_is_404_via_real_chain(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = self._run_id()
        self._seed_run(run_id, _OTHER_PROJECT)  # 真实存在但不在授权作用域
        client = self._client(monkeypatch)

        outside = client.get(f"/api/agent/runs/{run_id}")
        missing = client.get("/api/agent/runs/run-p6s8a-missing")

        assert outside.status_code == missing.status_code == 404
        assert outside.json() == missing.json()  # 同构（不泄露存在性）

    def test_missing_credential_rejected_on_real_chain(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_id = self._run_id()
        self._seed_run(run_id, _AUTHORIZED_PROJECT)
        monkeypatch.setattr(
            agent_auth_module,
            "get_agent_read_settings",
            lambda: AgentReadSettings(
                api_key=_TEST_API_KEY, api_project_id=_AUTHORIZED_PROJECT
            ),
        )
        client = TestClient(app)  # 无凭据
        response = client.get(f"/api/agent/runs/{run_id}")
        assert response.status_code == 401
        assert run_id not in response.text  # 不泄露资源身份

    def test_residue_is_zero_after_cleanup(self, engine: Any) -> None:
        from sqlalchemy import text

        self._delete_prefix(engine)
        with engine.connect() as connection:
            counts = {
                table: int(
                    connection.execute(
                        text(
                            f"SELECT COUNT(*) FROM ai_ops.{table} "
                            "WHERE run_id LIKE :prefix"
                        ),
                        {"prefix": f"{self._PREFIX}%"},
                    ).scalar_one()
                )
                for table in (
                    "agent_run",
                    "agent_step",
                    "agent_decision",
                    "agent_tool_result",
                    "agent_checkpoint",
                    "agent_approval",
                )
            }
        assert set(counts.values()) == {0}, counts
