"""Phase 6 Step 7.2 — Workflow Read API **安全修复验证测试**（离线）。

历史沿革（**重要**）：
```text
本文件原为 S7.1 审计测试——当时以"缺口证明"形式（绿 = 未认证可读被确认）
暴露四个端点无认证。S7.2 完成最小 API Key 认证 + 服务端项目作用域授权后，
本文件**语义翻转**为修复验证：
  · 保留原威胁场景（未认证 / 跨项目 / 枚举 / 泄漏）的覆盖；
  · 不再保留与新安全契约矛盾的旧期望（"未认证可读"断言已移除）；
  · 新增 §7.1 全部 12 项 + OpenAPI 契约 + 部署边界证据。
```

覆盖（S7.2 §7.1 / §7.3 + S7.1 §六 部署边界保留）：
```text
1  四个端点缺少 API Key 均拒绝（401）
2  错误 API Key 均拒绝（401）
3  服务端未配置 API Key / 未配置作用域 ⇒ fail closed（503；绝不匿名放行）
4  正确 API Key 可访问其授权项目中的资源（200，四端点）
5  正确 API Key 不能访问其他项目的 Run
6  正确 API Key 不能访问其他项目的 Node Run
7  正确 API Key 不能访问其他项目的 Timeline
8  正确 API Key 不能访问其他项目的 Approval
9  客户端修改 project_id 参数不能扩大授权范围
10 缺少项目作用域配置时不回退到全局 / 默认项目（含 vietnam-wms）
11 不存在的资源与越权资源响应同构（不泄露存在性）；401 与资源无关
12 响应体 / 异常 / 日志中不出现 API Key 或内部凭据
★  OpenAPI：securitySchemes（X-API-Key）+ 四端点 security 声明 + 与运行时一致
★  部署边界（保留 S7.1 证据）：无网关 / 反代 / API 容器；APP_HOST 默认 0.0.0.0
★  源码级控制：恒定时间比较 / 无客户端作用域参数 / 依赖精确挂载四处
```
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api import workflow_read as workflow_read_module
from backend.app.api import workflow_read_auth as auth_module
from backend.app.config import WorkflowReadSettings
from backend.app.main import app
from tests.test_workflow_runtime_read_service import (
    _Env,
    _approval_record,
    _node_record,
    _run_record,
)

__all__ = [
    "TestAuthenticationRequired",
    "TestAuthorizedScope",
    "TestEnumerationAndLeakage",
    "TestOpenApiContract",
    "TestSourceLevelControls",
    "TestDeploymentBoundaryEvidence",
]

_ROOT = Path(__file__).resolve().parents[1]
_RUN_ID = "wf-run-1"
_APPROVAL_ID = "wf-ap-1"
_AUTHORIZED_PROJECT = "proj-a"
_VICTIM_PROJECT = "victim-project-scope"
_TEST_API_KEY = "audit-suite-api-key-NOT-A-PRODUCTION-CREDENTIAL"
_HEADERS = {"X-API-Key": _TEST_API_KEY}

_URLS = (
    f"/api/workflow-runs/{_RUN_ID}",
    f"/api/workflow-runs/{_RUN_ID}/nodes",
    f"/api/workflow-runs/{_RUN_ID}/timeline",
    f"/api/workflow-approvals/{_APPROVAL_ID}",
)


def _client(
    monkeypatch: pytest.MonkeyPatch,
    env: _Env,
    *,
    auth_settings: WorkflowReadSettings | None = None,
) -> TestClient:
    """monkeypatch 读取服务（fake 存储）+ 认证配置（可控）。"""
    monkeypatch.setattr(
        workflow_read_module,
        "get_workflow_read_service",
        lambda: env.service,
    )
    monkeypatch.setattr(
        auth_module,
        "get_workflow_read_settings",
        lambda: auth_settings
        or WorkflowReadSettings(
            api_key=_TEST_API_KEY,
            api_project_id=_AUTHORIZED_PROJECT,
        ),
    )
    return TestClient(app)


def _env_full() -> _Env:
    return _Env(
        run=_run_record(project_id=_AUTHORIZED_PROJECT),
        nodes=[_node_record()],
        approvals={_APPROVAL_ID: _approval_record()},
    )


def _auth_only_client(monkeypatch: pytest.MonkeyPatch, env: _Env) -> TestClient:
    return _client(monkeypatch, env)


# ============================================================
# 1 / 2 / 3：认证必须存在（fail closed）
# ============================================================


class TestAuthenticationRequired:
    def test_missing_api_key_rejected_on_all_four_endpoints(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _env_full()
        client = _client(monkeypatch, env)
        for url in _URLS:
            response = client.get(url)  # 无 X-API-Key
            assert response.status_code == 401, url
            assert response.json()["detail"] == (
                "认证失败：缺少或无效的 API Key"
            )
        env.assert_no_writes()

    def test_wrong_api_key_rejected_on_all_four_endpoints(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _env_full()
        client = _client(monkeypatch, env)
        for url in _URLS:
            response = client.get(
                url, headers={"X-API-Key": "wrong-key-value"}
            )
            assert response.status_code == 401, url
        env.assert_no_writes()

    def test_unconfigured_api_key_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """服务端未配置 key ⇒ 503（**绝不**退化为匿名可访问）。"""
        env = _env_full()
        client = _client(
            monkeypatch,
            env,
            auth_settings=WorkflowReadSettings(
                api_key="", api_project_id=_AUTHORIZED_PROJECT
            ),
        )
        for url in _URLS:
            response = client.get(url, headers=_HEADERS)  # 即使带了 header
            assert response.status_code == 503, url
        env.assert_no_writes()

    def test_unconfigured_scope_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """服务端未配置作用域 ⇒ 503（**不**回退任何默认项目）。"""
        env = _env_full()
        client = _client(
            monkeypatch,
            env,
            auth_settings=WorkflowReadSettings(
                api_key=_TEST_API_KEY, api_project_id=""
            ),
        )
        for url in _URLS:
            response = client.get(url, headers=_HEADERS)
            assert response.status_code == 503, url

    def test_blank_credentials_count_as_unconfigured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _env_full()
        client = _client(
            monkeypatch,
            env,
            auth_settings=WorkflowReadSettings(
                api_key="   ", api_project_id="   "
            ),
        )
        response = client.get(_URLS[0], headers=_HEADERS)
        assert response.status_code == 503


# ============================================================
# 4 / 5 / 6 / 7 / 8 / 9 / 10：授权作用域
# ============================================================


class TestAuthorizedScope:
    def test_authorized_key_reads_authorized_project_resources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _env_full()
        client = _client(monkeypatch, env)
        for url in _URLS:
            response = client.get(url, headers=_HEADERS)
            assert response.status_code == 200, url
        env.assert_no_writes()

    def test_cannot_read_other_project_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _Env(run=_run_record(project_id=_VICTIM_PROJECT))
        client = _client(monkeypatch, env)
        response = client.get(_URLS[0], headers=_HEADERS)
        assert response.status_code == 404

    def test_cannot_read_other_project_node_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _Env(
            run=_run_record(project_id=_VICTIM_PROJECT),
            nodes=[_node_record()],
        )
        client = _client(monkeypatch, env)
        response = client.get(_URLS[1], headers=_HEADERS)
        assert response.status_code == 404

    def test_cannot_read_other_project_timeline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _Env(
            run=_run_record(project_id=_VICTIM_PROJECT),
            nodes=[_node_record()],
        )
        client = _client(monkeypatch, env)
        response = client.get(_URLS[2], headers=_HEADERS)
        assert response.status_code == 404

    def test_cannot_read_other_project_approval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _Env(
            run=_run_record(project_id=_VICTIM_PROJECT),
            approvals={_APPROVAL_ID: _approval_record()},
        )
        client = _client(monkeypatch, env)
        response = client.get(_URLS[3], headers=_HEADERS)
        assert response.status_code == 404

    def test_client_project_id_cannot_widen_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§7.1.9：客户端主张 `?project_id=<victim>` 不改变授权范围。"""
        victim_env = _Env(run=_run_record(project_id=_VICTIM_PROJECT))
        victim_client = _client(monkeypatch, victim_env)
        clamped = victim_client.get(
            _URLS[0],
            headers=_HEADERS,
            params={"project_id": _VICTIM_PROJECT},  # 冒充受害项目
        )
        assert clamped.status_code == 404  # 授权范围仍是服务端配置

        own_env = _env_full()
        own_client = _client(monkeypatch, own_env)
        ignored = own_client.get(
            _URLS[0],
            headers=_HEADERS,
            params={"project_id": "some-other-project"},
        )
        assert ignored.status_code == 200  # 服务端作用域判定，与参数无关
        assert ignored.json()["project_id"] == _AUTHORIZED_PROJECT

    def test_no_scope_configured_never_falls_back_to_default_project(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§7.1.10：即使资源属默认项目（vietnam-wms）也不得回退读取。"""
        env = _Env(run=_run_record(project_id="vietnam-wms"))
        client = _client(
            monkeypatch,
            env,
            auth_settings=WorkflowReadSettings(
                api_key=_TEST_API_KEY, api_project_id=""
            ),
        )
        response = client.get(_URLS[0], headers=_HEADERS)
        assert response.status_code == 503  # 不 200、不 404 回退

    def test_unauthorized_project_equals_missing_resource(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§7.1.11：越权资源与不存在资源同构（同码同文案——不泄露存在性）。"""
        env = _Env(run=_run_record(project_id=_VICTIM_PROJECT))
        client = _client(monkeypatch, env)
        outside = client.get(_URLS[0], headers=_HEADERS)
        missing = client.get(
            "/api/workflow-runs/wf-missing", headers=_HEADERS
        )
        assert outside.status_code == missing.status_code == 404
        assert outside.json() == missing.json()


# ============================================================
# 11 / 12：枚举与泄漏
# ============================================================


class TestEnumerationAndLeakage:
    def test_rejected_request_is_resource_agnostic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """401 与目标资源是否存在无关（同 body——不泄露存在性）。"""
        env = _env_full()
        client = _client(monkeypatch, env)
        existing = client.get(_URLS[0])  # 无凭据 + 存在资源
        missing = client.get("/api/workflow-runs/wf-missing")  # 无凭据 + 不存在
        assert existing.status_code == missing.status_code == 401
        assert existing.json() == missing.json()

    def test_api_key_never_appears_in_response_or_logs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """§7.1.12：响应体 / 日志不出现 API Key（含 401 与成功路径）。"""
        env = _env_full()
        client = _client(monkeypatch, env)
        with caplog.at_level(logging.DEBUG):
            ok = client.get(_URLS[0], headers=_HEADERS)
            rejected = client.get(
                _URLS[0], headers={"X-API-Key": "wrong-key-BUT-SENSITIVE"}
            )
            unconfigured = _client(
                monkeypatch,
                env,
                auth_settings=WorkflowReadSettings(
                    api_key="", api_project_id=_AUTHORIZED_PROJECT
                ),
            ).get(_URLS[0], headers=_HEADERS)
        assert ok.status_code == 200
        assert rejected.status_code == 401
        assert unconfigured.status_code == 503
        for response in (ok, rejected, unconfigured):
            assert _TEST_API_KEY not in response.text
            assert "wrong-key-BUT-SENSITIVE" not in response.text
        assert _TEST_API_KEY not in caplog.text
        assert "wrong-key-BUT-SENSITIVE" not in caplog.text
        assert "credential rejected" in caplog.text  # 只记录"拒绝"事实类别

    def test_401_has_www_authenticate_without_secrets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = _env_full()
        client = _client(monkeypatch, env)
        response = client.get(_URLS[0])
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == "X-API-Key"
        assert _TEST_API_KEY not in str(response.headers)


# ============================================================
# ★ OpenAPI 契约（§7.3）
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
        assert api_key_names, f"OpenAPI 无 X-API-Key security scheme: {schemes!r}"
        scheme_name = api_key_names[0]
        for route in (
            "/api/workflow-runs/{run_id}",
            "/api/workflow-runs/{run_id}/nodes",
            "/api/workflow-runs/{run_id}/timeline",
            "/api/workflow-approvals/{approval_id}",
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


# ============================================================
# ★ 源码级控制断言
# ============================================================


class TestSourceLevelControls:
    def test_constant_time_comparison_is_used(self) -> None:
        source = Path(auth_module.__file__).read_text(encoding="utf-8")
        assert "secrets.compare_digest" in source

    def test_no_client_supplied_scope_parameter_in_api(self) -> None:
        """API 层不再接受任何客户端项目作用域参数 / 普通身份头。"""
        source = Path(workflow_read_module.__file__).read_text(
            encoding="utf-8"
        )
        for construct in ("Query(", "X-Project-ID", "X-Forwarded", "Header("):
            assert construct not in source, (
                f"workflow_read.py 出现客户端作用域 / 身份构造: {construct!r}"
            )

    def test_auth_dependency_is_mounted_on_exactly_four_endpoints(self) -> None:
        source = Path(workflow_read_module.__file__).read_text(
            encoding="utf-8"
        )
        assert source.count("Security(require_workflow_read_scope)") == 4

    def test_auth_module_rejects_when_unconfigured_before_comparison(self) -> None:
        """未配置检查必须先于（且独立于）凭据比较（fail closed 顺序）。

        锚点使用代码形态（docstring 中的同名说明不参与判定）。
        """
        source = Path(auth_module.__file__).read_text(encoding="utf-8")
        guard_index = source.index("_reject_if_not_configured(config)")
        compare_index = source.index(
            "if api_key is None or not secrets.compare_digest"
        )
        assert guard_index < compare_index


# ============================================================
# ★ 部署边界证据（保留 S7.1；HTTPS / 网络边界仍 NOT VERIFIED）
# ============================================================


class TestDeploymentBoundaryEvidence:
    def test_no_gateway_or_api_container_configuration_in_repository(self) -> None:
        dockerfile = _ROOT / "Dockerfile"
        assert not dockerfile.exists(), "出现 Dockerfile —— 重新审计部署边界"
        for candidate in (
            "nginx.conf",
            "compose.yaml",
            "docker-compose.prod.yml",
        ):
            assert not (_ROOT / candidate).exists(), candidate
        compose = (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        lowered = compose.lower()
        assert "postgres" in lowered
        assert "nginx" not in lowered
        assert re.search(
            r"^\s{2}(api|app|backend|gateway)\s*:", compose, re.M
        ) is None, "compose 中出现 API / 网关服务 —— 重新审计部署边界"

    def test_app_host_default_allows_binding_all_interfaces(self) -> None:
        """部署边界仍未验证：默认 APP_HOST=0.0.0.0 的启动路径存在。"""
        config_source = (
            _ROOT / "backend" / "app" / "config.py"
        ).read_text(encoding="utf-8")
        assert '"APP_HOST", "0.0.0.0"' in config_source
        main_source = (_ROOT / "backend" / "app" / "main.py").read_text(
            encoding="utf-8"
        )
        assert "host=settings.app_host" in main_source

    def test_no_trusted_proxy_or_header_identity_configuration(self) -> None:
        for relative in (
            "backend/app/main.py",
            "backend/app/config.py",
            "backend/app/api/workflow_read.py",
            "backend/app/api/workflow_read_auth.py",
        ):
            source = (_ROOT / relative).read_text(encoding="utf-8")
            for construct in (
                "TrustedHost",
                "forwarded",
                "X-Forwarded",
                "PROXY_",
            ):
                assert construct not in source, f"{relative}: {construct!r}"
            # "X-Project-ID" 仅允许出现在文档字符串（作为反例说明）；
            # 作为**字符串字面量**（潜在请求头取值）必须不存在
            assert '"X-Project-ID"' not in source, (
                f"{relative}: 出现身份头字面量 'X-Project-ID'"
            )
