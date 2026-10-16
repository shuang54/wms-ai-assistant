"""M2.4 授权强制（HTTP 端点级）测试 —— **零 DB / 零残留**。

验证 ``AuthorizationService`` 通过 M2.3 A2 的两个依赖真正生效：

    系统级（5 个端点，``require_observability_ops``）
        过渡期并集：``SYSTEM_OPS`` 授权行  OR  旧白名单
        旧白名单不可用 ⇒ 503（A2 语义逐字保留）
        授权存储不可用 ⇒ 503（M2.4 新增 fail closed）
        两者皆无       ⇒ 403

    项目级（2 个端点，``require_observability_project_scope``）
        会话 + 服务端 ``PROJECT_ID`` + 该项目 ``MEMBER`` / ``PROJECT_OPS``
        非成员         ⇒ 403
        授权存储不可用 ⇒ 503

使用真实依赖链（``require_session`` / ``require_observability_ops`` /
``require_observability_project_scope`` 全部真实执行），**不**使用
``dependency_overrides``。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.api import observability_read_auth as auth_module
from backend.app.config import ObservabilityReadSettings
from backend.app.db.models.authorization_grant import (
    PROJECT_ROLE_MEMBER,
    PROJECT_ROLE_PROJECT_OPS,
    SYSTEM_ROLE_SYSTEM_OPS,
)
from backend.app.main import app
from tests.api_session_auth import (
    SessionTestClient,
    install_authorization_grants,
    install_fake_session_service,
    install_observability_ops_allowlist,
    install_observability_project_scope,
)
from tests.conversation_auth_support import USER_IDS, USER_PASSWORDS

_USAGE_PATH = "/api/usage/analytics"
_TOOLS_PATH = "/api/observability/tools"
_TRACE_PATH = "/api/observability/assistant-trace/A"
_TIMELINE_PATH = "/api/observability/assistant-timeline/A"
_HISTORY_PATH = "/api/observability/tools/history"
_PERSISTENT_METRICS_PATH = "/api/observability/tools/metrics/persistent"

#: 5 个系统级端点。
_SYSTEM_ENDPOINTS = (
    _USAGE_PATH,
    _TOOLS_PATH,
    "/api/observability/tools/metrics",
    _TRACE_PATH,
    _TIMELINE_PATH,
)

#: 2 个项目级端点。
_PROJECT_ENDPOINTS = (_HISTORY_PATH, _PERSISTENT_METRICS_PATH)

_PROJECT = "vietnam-wms"


def _alice() -> str:
    return USER_IDS["alice"]


def _valid_whitelist_without_alice() -> set[str]:
    """**有效但不含 alice** 的旧白名单。

    用于隔离出"授权表分支"：白名单本身可解析（不触发 503），但 alice 不在其中，
    因此她能否通过完全取决于 ``SYSTEM_OPS`` 授权行。

    Note:
        空 / 空白 / 含空条目的白名单会触发 503（M2.3 A2 语义），
        因此这类用例不能用空集合来表达"白名单不授予此人"。
    """
    return {USER_IDS["bob"]}


# ============================================================
# 系统级：SYSTEM_OPS 授权行分支
# ============================================================

class TestSystemOpsGrantBranch:
    @pytest.mark.parametrize("path", _SYSTEM_ENDPOINTS)
    def test_system_ops_grant_allows_ops_endpoints(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """仅持 ``SYSTEM_OPS`` 授权行、**不在**旧白名单 ⇒ 通过授权门。

        端点体可能因 Fake 未装配而返回 502/500，但**绝不**是 401/403/503。
        """
        install_fake_session_service(monkeypatch)
        install_observability_ops_allowlist(
            monkeypatch, _valid_whitelist_without_alice()
        )
        install_authorization_grants(
            monkeypatch, system_ops={_alice()}
        )
        client = SessionTestClient(app)

        response = client.get(path)

        assert response.status_code not in (401, 403, 503), path

    @pytest.mark.parametrize("path", _SYSTEM_ENDPOINTS)
    def test_whitelist_remains_sufficient_under_union(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """**不在**授权表但**在**旧白名单 ⇒ 仍通过（并集语义，A2 行为保留）。"""
        install_fake_session_service(monkeypatch)
        install_observability_ops_allowlist(monkeypatch, {_alice()})
        install_authorization_grants(monkeypatch)
        client = SessionTestClient(app)

        response = client.get(path)

        assert response.status_code not in (401, 403, 503), path

    def test_projects_ops_does_not_open_system_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``PROJECT_OPS``（即使是该项目）**不得**获得系统级访问。

        M2.4 最重要的越权断言：项目级运维 ≠ 系统级运维。
        """
        install_fake_session_service(monkeypatch)
        install_observability_ops_allowlist(
            monkeypatch, _valid_whitelist_without_alice()
        )
        install_authorization_grants(
            monkeypatch,
            project_roles={
                (_alice(), _PROJECT): {
                    PROJECT_ROLE_MEMBER, PROJECT_ROLE_PROJECT_OPS
                }
            },
        )
        client = SessionTestClient(app)

        for path in _SYSTEM_ENDPOINTS:
            assert client.get(path).status_code == 403, path

    def test_system_ops_does_not_open_project_endpoints(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``SYSTEM_OPS`` **不得**自动成为项目成员（反向也不推导）。"""
        install_fake_session_service(monkeypatch)
        install_observability_project_scope(monkeypatch, _PROJECT)
        install_authorization_grants(
            monkeypatch, system_ops={_alice()}
        )
        client = SessionTestClient(app)

        for path in _PROJECT_ENDPOINTS:
            assert client.get(path).status_code == 403, path

    def test_non_ops_user_is_forbidden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        install_observability_ops_allowlist(
            monkeypatch, _valid_whitelist_without_alice()
        )
        install_authorization_grants(monkeypatch)
        client = SessionTestClient(app)

        response = client.get(_USAGE_PATH)

        assert response.status_code == 403
        assert response.json()["detail"] == "权限不足：需要运维读取授权"


# ============================================================
# 系统级：fail closed + A2 503 语义回归
# ============================================================

class TestSystemOpsFailClosed:
    @pytest.mark.parametrize(
        "raw", ("", "   ", "a,,b", "trailing,", ",")
    )
    def test_legacy_whitelist_unusable_is_still_503(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        """A2 语义逐字保留：白名单不可用 ⇒ 503（**即使**授权表可用）。"""
        install_fake_session_service(monkeypatch)
        monkeypatch.setattr(
            auth_module,
            "get_observability_read_settings",
            lambda: ObservabilityReadSettings(ops_user_ids=raw),
        )
        install_authorization_grants(
            monkeypatch, system_ops={_alice()}
        )
        client = SessionTestClient(app)

        response = client.get(_USAGE_PATH)

        assert response.status_code == 503
        assert response.json()["detail"] == "运维读取授权未配置（服务不可用）"

    @pytest.mark.parametrize("path", _SYSTEM_ENDPOINTS)
    def test_authorization_store_unavailable_is_503(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """授权存储不可用 ⇒ 503 fail closed（**不**降级为放行，也**不**返回 403）。"""
        install_fake_session_service(monkeypatch)
        install_observability_ops_allowlist(monkeypatch, {_alice()})
        install_authorization_grants(monkeypatch, failure="unavailable")
        client = SessionTestClient(app)

        response = client.get(path)

        assert response.status_code == 503, path
        assert response.json()["detail"] == "授权服务不可用"

    @pytest.mark.parametrize("path", _SYSTEM_ENDPOINTS)
    def test_authorization_store_not_configured_falls_back_to_whitelist(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """存储**未配置**（无 DB 部署）⇒ 不报 503，由旧白名单决断。

        这是无 DB 部署的既有形态（M2.3 A2 时代没有授权表）。
        """
        install_fake_session_service(monkeypatch)
        install_observability_ops_allowlist(monkeypatch, {_alice()})
        install_authorization_grants(monkeypatch, failure="not_configured")
        client = SessionTestClient(app)

        response = client.get(path)

        assert response.status_code not in (401, 403, 503), path

    def test_anonymous_still_401_before_authorization(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """认证先于授权：匿名仍是 401（**不**泄漏是否持有授权）。"""
        install_observability_ops_allowlist(monkeypatch, {_alice()})
        install_authorization_grants(
            monkeypatch, system_ops={_alice()}
        )

        with TestClient(app) as client:
            for path in (*_SYSTEM_ENDPOINTS, *_PROJECT_ENDPOINTS):
                assert client.get(path).status_code == 401, path

    def test_invalid_session_is_401_even_with_system_ops_grant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        install_observability_ops_allowlist(
            monkeypatch, _valid_whitelist_without_alice()
        )
        install_authorization_grants(
            monkeypatch, system_ops={_alice()}
        )
        client = SessionTestClient(app)
        client.cookies.set("wms_ai_session", "token-does-not-exist")

        assert client.get(_TOOLS_PATH).status_code == 401


# ============================================================
# 项目级：MEMBER / PROJECT_OPS / 非成员
# ============================================================

class TestProjectMemberEnforcement:
    @pytest.mark.parametrize("role", (PROJECT_ROLE_MEMBER, PROJECT_ROLE_PROJECT_OPS))
    @pytest.mark.parametrize("path", _PROJECT_ENDPOINTS)
    def test_project_role_grants_access(
        self, monkeypatch: pytest.MonkeyPatch, role: str, path: str
    ) -> None:
        install_fake_session_service(monkeypatch)
        install_observability_project_scope(monkeypatch, _PROJECT)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {role}},
        )
        client = SessionTestClient(app)

        response = client.get(path)

        assert response.status_code not in (401, 403, 503), path

    @pytest.mark.parametrize("path", _PROJECT_ENDPOINTS)
    def test_no_membership_is_forbidden(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        install_fake_session_service(monkeypatch)
        install_observability_project_scope(monkeypatch, _PROJECT)
        install_authorization_grants(monkeypatch)
        client = SessionTestClient(app)

        response = client.get(path)

        assert response.status_code == 403, path
        assert response.json()["detail"] == "权限不足：需要项目访问授权"

    @pytest.mark.parametrize("path", _PROJECT_ENDPOINTS)
    def test_membership_of_other_project_is_forbidden(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """另一项目的成员身份**不**构成本项目成员。"""
        install_fake_session_service(monkeypatch)
        install_observability_project_scope(monkeypatch, _PROJECT)
        install_authorization_grants(
            monkeypatch,
            project_roles={
                (_alice(), "other-project"): {PROJECT_ROLE_PROJECT_OPS}
            },
        )
        client = SessionTestClient(app)

        assert client.get(path).status_code == 403, path

    @pytest.mark.parametrize("path", _PROJECT_ENDPOINTS)
    def test_legacy_whitelist_never_grants_project_access(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """**旧白名单只适用于 5 个系统级端点**（审查要点 §2）。

        白名单成员身份**不**构成项目成员资格 —— 两个项目级端点必须拒绝。
        这是并集语义最容易出错的方向：把"部署级运维配置"误当成"项目授权"。
        """
        install_fake_session_service(monkeypatch)
        install_observability_project_scope(monkeypatch, _PROJECT)
        install_observability_ops_allowlist(monkeypatch, {_alice()})
        install_authorization_grants(monkeypatch)      # 无任何成员授权
        client = SessionTestClient(app)

        response = client.get(path)

        assert response.status_code == 403, path
        assert response.json()["detail"] == "权限不足：需要项目访问授权"

    @pytest.mark.parametrize("path", _PROJECT_ENDPOINTS)
    def test_client_project_id_cannot_substitute_for_membership(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """客户端 ``project_id``（哪怕是**不存在**的项目）不得替代成员授权。"""
        install_fake_session_service(monkeypatch)
        install_observability_project_scope(monkeypatch, _PROJECT)
        install_authorization_grants(monkeypatch)   # alice 无任何成员授权
        client = SessionTestClient(app)

        for claimed in (_PROJECT, "other-project", "", "' OR 1=1 --"):
            response = client.get(f"{path}?project_id={claimed}")
            assert response.status_code == 403, (path, claimed)

    @pytest.mark.parametrize("path", _PROJECT_ENDPOINTS)
    def test_authorization_store_unavailable_is_503(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        install_fake_session_service(monkeypatch)
        install_observability_project_scope(monkeypatch, _PROJECT)
        install_authorization_grants(monkeypatch, failure="unavailable")
        client = SessionTestClient(app)

        response = client.get(path)

        assert response.status_code == 503, path
        assert response.json()["detail"] == "授权服务不可用"

    @pytest.mark.parametrize("path", _PROJECT_ENDPOINTS)
    def test_missing_project_scope_is_503_before_membership(
        self, monkeypatch: pytest.MonkeyPatch, path: str
    ) -> None:
        """作用域未配置 ⇒ 503（先于成员判定；**不**泄漏是否有成员记录）。"""
        install_fake_session_service(monkeypatch)
        install_observability_project_scope(monkeypatch, "   ")
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {PROJECT_ROLE_MEMBER}},
        )
        client = SessionTestClient(app)

        response = client.get(path)

        assert response.status_code == 503, path
        assert response.json()["detail"] == "部署项目作用域未配置（服务不可用）"


# ============================================================
# 零副作用 / 不泄漏
# ============================================================

class TestNoLeakageAndNoSideEffects:
    def test_forbidden_body_does_not_leak_identity_or_grants(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        install_observability_ops_allowlist(
            monkeypatch, _valid_whitelist_without_alice()
        )
        install_authorization_grants(monkeypatch)
        client = SessionTestClient(app)

        body = client.get(_USAGE_PATH).text

        assert _alice() not in body
        assert _PROJECT not in body
        assert "authorization_grant" not in body
        assert "SYSTEM_OPS" not in body
        assert "OBSERVABILITY_READ_OPS_USER_IDS" not in body

    def test_503_body_does_not_leak_store_details(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        install_fake_session_service(monkeypatch)
        install_observability_ops_allowlist(monkeypatch, {_alice()})
        install_authorization_grants(monkeypatch, failure="unavailable")
        client = SessionTestClient(app)

        body = client.get(_USAGE_PATH).text.upper()

        for forbidden in ("POSTGRES", "SQLALCHEMY", "TRACEBACK", "AI_OPS"):
            assert forbidden not in body, forbidden

    def test_denied_request_does_not_call_read_boundaries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """授权拒绝后**零**业务读取（无部分结果）。"""
        import backend.app.api.tool_observability as tool_api

        calls: list[str] = []

        class _DenyTracking:
            def list_recent(self, **kwargs: object) -> list[object]:
                calls.append("list_recent")
                return []

            def metrics(self, **kwargs: object) -> object:
                calls.append("metrics")
                raise AssertionError("metrics must not be called")

        install_fake_session_service(monkeypatch)
        install_observability_project_scope(monkeypatch, _PROJECT)
        install_authorization_grants(monkeypatch)      # 非成员
        monkeypatch.setattr(
            tool_api,
            "get_tool_execution_persistent_query_service",
            lambda: _DenyTracking(),
        )
        client = SessionTestClient(app)

        assert client.get(_HISTORY_PATH).status_code == 403
        assert client.get(_PERSISTENT_METRICS_PATH).status_code == 403
        assert calls == []

    def test_openapi_declares_membership_failure_mode(self) -> None:
        """OpenAPI 仍声明 401/403/503（M2.4 未改变状态码集合）。"""
        spec = app.openapi()

        for path in _PROJECT_ENDPOINTS:
            responses = spec["paths"][path]["get"]["responses"]
            assert {"401", "403", "503"} <= set(responses), path


# ============================================================
# 结构性：依赖无客户端可控的授权入参
# ============================================================

class TestSharedDecisionHelpers:
    """M2.4 Pre-Commit Review 修复：授权决策单一事实来源的直接测试。

    重构前，系统级 / 项目级判定在 ``observability_read_auth`` 与
    ``authorization_dependency`` 各写一遍（分歧风险），且
    ``require_project_ops`` / ``require_system_ops`` **无任何测试覆盖**。
    本类直接测试共享决策助手与那两个依赖。
    """

    def _request(self, token: str) -> object:
        """构造携带有效会话 Cookie 的 Starlette Request（不经过 HTTP 栈）。"""
        from starlette.requests import Request

        from backend.app.config import settings

        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "query_string": b"",
                "headers": [
                    (
                        b"cookie",
                        f"{settings.auth.session_cookie_name}={token}".encode(),
                    )
                ],
            }
        )

    # ---- enforce_project_access（ops_only 分支此前未覆盖）----

    def test_enforce_project_access_member_level(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.app.api import authorization_dependency as authz

        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {PROJECT_ROLE_MEMBER}},
        )

        # MEMBER 满足"至少为成员"，**不**满足 ops_only
        assert authz.enforce_project_access(
            _alice(), _PROJECT, ops_only=False
        ) is None
        with pytest.raises(Exception) as excinfo:
            authz.enforce_project_access(_alice(), _PROJECT, ops_only=True)
        assert getattr(excinfo.value, "status_code", None) == 403

    def test_enforce_project_access_ops_grants_both_levels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.app.api import authorization_dependency as authz

        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {PROJECT_ROLE_PROJECT_OPS}},
        )

        assert authz.enforce_project_access(
            _alice(), _PROJECT, ops_only=False
        ) is None
        assert authz.enforce_project_access(
            _alice(), _PROJECT, ops_only=True
        ) is None

    def test_enforce_project_access_other_project_is_403(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.app.api import authorization_dependency as authz

        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {PROJECT_ROLE_PROJECT_OPS}},
        )

        with pytest.raises(Exception) as excinfo:
            authz.enforce_project_access(
                _alice(), "other-project", ops_only=False
            )
        assert getattr(excinfo.value, "status_code", None) == 403

    def test_enforce_project_access_store_failure_is_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.app.api import authorization_dependency as authz

        install_authorization_grants(monkeypatch, failure="unavailable")

        with pytest.raises(Exception) as excinfo:
            authz.enforce_project_access(_alice(), _PROJECT, ops_only=False)
        assert getattr(excinfo.value, "status_code", None) == 503

    # ---- enforce_system_ops_grant ----

    def test_enforce_system_ops_grant_true_only_with_system_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.app.api import authorization_dependency as authz

        install_authorization_grants(
            monkeypatch,
            project_roles={
                (_alice(), _PROJECT): {PROJECT_ROLE_PROJECT_OPS}
            },
        )

        # 项目运维 ≠ 系统运维（共享助手也必须遵守该铁律）
        assert authz.enforce_system_ops_grant(_alice()) is False

        install_authorization_grants(
            monkeypatch, system_ops={_alice()}
        )
        assert authz.enforce_system_ops_grant(_alice()) is True

    def test_enforce_system_ops_grant_store_failure_is_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.app.api import authorization_dependency as authz

        install_authorization_grants(monkeypatch, failure="unavailable")

        with pytest.raises(Exception) as excinfo:
            authz.enforce_system_ops_grant(_alice())
        assert getattr(excinfo.value, "status_code", None) == 503

    # ---- 两个此前无覆盖的 FastAPI 依赖 ----

    def test_require_system_ops_dependency_rejects_without_grant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``require_system_ops`` **只**看授权表（**不**含旧白名单并集）。

        安全含义：一个仅在旧白名单中的用户**不**应通过这个更严格的依赖
        —— 它供未来不受旧配置约束的系统级端点使用。
        """
        import asyncio

        from backend.app.api import authorization_dependency as authz

        service = install_fake_session_service(monkeypatch)
        install_observability_ops_allowlist(monkeypatch, {_alice()})
        install_authorization_grants(monkeypatch)      # 无 SYSTEM_OPS 行
        result = service.login(
            username="alice", password=USER_PASSWORDS["alice"]
        )

        with pytest.raises(Exception) as excinfo:
            asyncio.run(authz.require_system_ops(self._request(result.token)))
        assert getattr(excinfo.value, "status_code", None) == 403

    def test_require_system_ops_dependency_allows_with_grant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        from backend.app.api import authorization_dependency as authz

        service = install_fake_session_service(monkeypatch)
        install_authorization_grants(monkeypatch, system_ops={_alice()})
        result = service.login(
            username="alice", password=USER_PASSWORDS["alice"]
        )

        assert asyncio.run(
            authz.require_system_ops(self._request(result.token))
        ) is None

    def test_require_project_ops_dependency_requires_ops_role(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``require_project_ops``：``MEMBER`` **不足**，``PROJECT_OPS`` 才通过。"""
        import asyncio

        from backend.app.api import authorization_dependency as authz

        service = install_fake_session_service(monkeypatch)
        install_observability_project_scope(monkeypatch, _PROJECT)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {PROJECT_ROLE_MEMBER}},
        )
        result = service.login(
            username="alice", password=USER_PASSWORDS["alice"]
        )
        request = self._request(result.token)

        with pytest.raises(Exception) as excinfo:
            asyncio.run(authz.require_project_ops(request))
        assert getattr(excinfo.value, "status_code", None) == 403

        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {PROJECT_ROLE_PROJECT_OPS}},
        )
        assert asyncio.run(
            authz.require_project_ops(self._request(result.token))
        ) is None

    def test_require_project_member_dependency_allows_member(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        from backend.app.api import authorization_dependency as authz

        service = install_fake_session_service(monkeypatch)
        install_observability_project_scope(monkeypatch, _PROJECT)
        install_authorization_grants(
            monkeypatch,
            project_roles={(_alice(), _PROJECT): {PROJECT_ROLE_MEMBER}},
        )
        result = service.login(
            username="alice", password=USER_PASSWORDS["alice"]
        )

        assert asyncio.run(
            authz.require_project_member(self._request(result.token))
        ) is None

    def test_project_scope_is_set_only_after_authorization(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**顺序保证**：未授权时 ``request.state`` 不得写入作用域。

        若作用域先写入、授权后失败，后续中间件 / 端点可能误用已写入的
        作用域（潜在越权读）。此处断言拒绝路径下 ``state`` 无该键。
        """
        import asyncio

        from backend.app.api import observability_read_auth as module

        service = install_fake_session_service(monkeypatch)
        install_observability_project_scope(monkeypatch, _PROJECT)
        install_authorization_grants(monkeypatch)      # 非成员
        result = service.login(
            username="alice", password=USER_PASSWORDS["alice"]
        )
        request = self._request(result.token)

        with pytest.raises(Exception) as excinfo:
            asyncio.run(module.require_observability_project_scope(request))
        assert getattr(excinfo.value, "status_code", None) == 403
        assert not hasattr(request.state, "observability_project_scope")


class TestDependencyContract:
    def test_dependencies_take_only_request(self) -> None:
        """两个授权依赖只接受 ``request`` ⇒ 客户端无法传入 role / user_id。"""
        import inspect

        for func in (
            auth_module.require_observability_ops,
            auth_module.require_observability_project_scope,
        ):
            parameters = inspect.signature(func).parameters
            assert list(parameters) == ["request"], func.__name__

    def test_authorization_module_declares_no_routes(self) -> None:
        """授权依赖模块**不得**声明任何 HTTP 路由（C25 闸门纪律）。"""
        import inspect

        from backend.app.api import authorization_dependency as module

        source = inspect.getsource(module)
        assert "@router." not in source
        assert "APIRouter(" not in source
