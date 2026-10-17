"""Phase 8 Step 2 —— 生产网关模板的**真实 Nginx** 验证（本机隔离）。

与 `test_deployment_config.py` 的分工：

* `test_deployment_config.py` —— **静态结构**解析（离线、秒级）。
* 本文件 —— **真实行为**：启动 `nginx:1.27-alpine` 容器 + 桩上游，
  用真实 HTTP/HTTPS 请求验证跳转、路由/方法矩阵、`proxy_pass` URI 行为、
  代理头覆盖、以及"被拒绝的请求确实没有到达上游"。

运行门控（沿用项目 `RUN_DB_TESTS` 习惯）：

```powershell
$env:RUN_GATEWAY_VALIDATION="1"
python -m pytest -q tests/test_gateway_validation.py
```

未设置、或 Docker / 镜像不可用时 **skip**（明确不是 PASS）。

隔离：只发布 `127.0.0.1` 端口；临时目录存放配置 / 自签名证书 / 静态文件；
不连接数据库、不调用真实 LLM / Tool；测试结束删除容器、网络与临时目录。

⚠️ 本文件**不**验证生产 HTTPS、生产证书或生产网络隔离 —— 那些必须是
真实主机证据（见报告中的 NOT VERIFIED 列表）。
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from tests.gateway_validation import (
    NGINX_IMAGE,
    TEST_HOST,
    GatewayEnv,
    HarnessUnavailable,
    cleanup,
    create_env,
    docker_available,
    image_present,
    request,
)

_ENABLED = os.getenv("RUN_GATEWAY_VALIDATION", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

pytestmark = pytest.mark.skipif(
    not _ENABLED,
    reason=(
        "设置 RUN_GATEWAY_VALIDATION=1 才运行真实 Nginx 网关验证"
        "（需要本机 Docker；未执行不等于 PASS）"
    ),
)

__all__ = [
    "TestNginxSyntax",
    "TestHttpToHttps",
    "TestStaticWhitelist",
    "TestRouteMethodMatrix",
    "TestProxyPassPath",
    "TestProxyHeaders",
    "TestLoopbackIsolation",
    "TestTempArtifacts",
]


# ============================================================
# 环境夹具
# ============================================================


@pytest.fixture(scope="module")
def env() -> GatewayEnv:
    if not docker_available():
        pytest.skip("Docker 不可用：跳过真实网关验证（未执行，非 PASS）")
    if not image_present(NGINX_IMAGE):
        pytest.skip(
            f"缺少镜像 {NGINX_IMAGE}：`docker pull {NGINX_IMAGE}` 后重试"
            "（未执行，非 PASS）"
        )

    try:
        created = create_env()
    except HarnessUnavailable as error:
        pytest.skip(f"验证环境准备失败：{error}（未执行，非 PASS）")

    try:
        yield created
    finally:
        cleanup(created)


@pytest.fixture(scope="module")
def running(env: GatewayEnv) -> GatewayEnv:
    try:
        env.start()
    except HarnessUnavailable as error:
        pytest.fail(f"网关容器启动失败：{error}\n{env.nginx_logs()[-1500:]}")
    return env


def _assert_not_proxied(env: GatewayEnv, response, *, where: str) -> None:
    """状态码被拒**且**上游确实没收到请求（仅看状态码不够）。"""
    time.sleep(0.15)   # 给可能的异步转发留出观测窗口
    records = env.records()
    assert not records, f"{where} 被拒绝但仍转发到上游: {records}"
    assert response.status in {400, 403, 404, 405}, (
        f"{where} 期望被拒绝，实际 {response.status}"
    )


# ============================================================
# 1. nginx -t
# ============================================================


class TestNginxSyntax:
    def test_nginx_dash_t_passes(self, running: GatewayEnv) -> None:
        """真实 `nginx -t` 退出码必须为 0 才可报告 PASS。

        在网关所在的 Docker 网络上执行：配置中的 ``upstream`` 使用容器别名，
        离线容器无法解析（会误报 ``host not found in upstream``）。
        """
        result = running.nginx_config_test()
        combined = (result.stdout or "") + (result.stderr or "")
        assert result.returncode == 0, combined[-2000:]
        assert "syntax is ok" in combined
        assert "test is successful" in combined

    def test_config_came_from_the_official_template(
        self, running: GatewayEnv
    ) -> None:
        """临时配置只替换占位符与上游地址，其余规则保持不变。"""
        text = running.config_text
        code = "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("#")
        )
        # 注释里允许出现 ``<UPPERCASE_PLACEHOLDER>`` 说明；**指令行**不得残留占位符
        assert "<" not in code, "指令行仍有未替换的占位符"
        assert "location ^~ /api/ui/projects/" in code
        assert "location = /api/auth/login" in code
        # 上游被替换为桩容器（唯一结构性替换）
        assert "server stub:9100;" in code
        assert "server 127.0.0.1:8000;" not in code


# ============================================================
# 2. HTTP → HTTPS
# ============================================================


class TestHttpToHttps:
    def test_http_root_redirects_to_https(self, running: GatewayEnv) -> None:
        response = request(f"{running.http_base}/")
        assert response.status == 301
        location = response.headers.get("location", "")
        assert location.startswith("https://"), location
        assert location.endswith("/")

    def test_http_redirect_uses_request_host(self, running: GatewayEnv) -> None:
        response = request(
            f"{running.http_base}/api/ui/projects/p/workflow-runs/r",
            headers={"Host": TEST_HOST},
        )
        assert response.status == 301
        assert response.headers["location"] == (
            f"https://{TEST_HOST}/api/ui/projects/p/workflow-runs/r"
        )

    def test_http_does_not_serve_content_or_proxy(
        self, running: GatewayEnv
    ) -> None:
        """明文端口只做跳转：不得返回 200，也不得触达上游。"""
        running.reset_records()
        for path in ("/index.html", "/api/ui/projects/p/workflow-runs/r"):
            response = request(f"{running.http_base}{path}")
            assert response.status == 301, path
        assert running.records() == []


# ============================================================
# 3. 静态白名单
# ============================================================


class TestStaticWhitelist:
    @pytest.mark.parametrize(
        "path", ["/index.html", "/styles.css", "/js/app.js"]
    )
    def test_allowed_static_files_are_served(
        self, running: GatewayEnv, path: str
    ) -> None:
        response = request(f"{running.https_base}{path}")
        assert response.status == 200, path

    def test_root_redirects_to_index(self, running: GatewayEnv) -> None:
        response = request(f"{running.https_base}/")
        assert response.status == 302
        assert response.headers["location"].endswith("/index.html")

    @pytest.mark.parametrize(
        "path",
        [
            "/package.json",
            "/README.md",
            "/tests/state.test.mjs",
            "/e2e/workbench.spec.js",
            "/scripts/dev-gateway.mjs",
            "/js/",
        ],
    )
    def test_paths_outside_whitelist_are_rejected(
        self, running: GatewayEnv, path: str
    ) -> None:
        running.reset_records()
        response = request(f"{running.https_base}{path}")
        assert response.status in {403, 404}, path
        assert running.records() == [], f"{path} 落入了上游代理"

    @pytest.mark.parametrize(
        "path", ["/js/%2e%2e/package.json", "/js/../../package.json"]
    )
    def test_traversal_never_reaches_upstream(
        self, running: GatewayEnv, path: str
    ) -> None:
        running.reset_records()
        response = request(f"{running.https_base}{path}")
        assert response.status in {400, 403, 404}, path
        assert running.records() == []


# ============================================================
# 4. 路由 / 方法矩阵
# ============================================================


class TestRouteMethodMatrix:
    def test_ui_get_is_proxied(self, running: GatewayEnv) -> None:
        running.reset_records()
        response = request(
            f"{running.https_base}/api/ui/projects/p1/workflow-runs/r1"
        )
        assert response.status == 200
        records = running.records()
        assert len(records) == 1
        assert records[0]["method"] == "GET"
        assert records[0]["path"] == "/api/ui/projects/p1/workflow-runs/r1"

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_ui_write_methods_are_rejected(
        self, running: GatewayEnv, method: str
    ) -> None:
        """只读面：非 GET 必须被网关拒绝，且**不得到达上游**。"""
        running.reset_records()
        response = request(
            f"{running.https_base}/api/ui/projects/p1/workflow-runs/r1",
            method=method,
            body=b"{}" if method in {"POST", "PUT", "PATCH"} else None,
            headers={"Content-Type": "application/json"},
        )
        _assert_not_proxied(running, response, where=f"{method} /api/ui/...")

    def test_ui_head_is_allowed_by_nginx_semantics(
        self, running: GatewayEnv
    ) -> None:
        """``limit_except GET`` 隐含允许 HEAD（Nginx 真实语义，已记录）。"""
        running.reset_records()
        response = request(
            f"{running.https_base}/api/ui/projects/p1/workflow-runs/r1",
            method="HEAD",
        )
        assert response.status == 200
        records = running.records()
        assert len(records) == 1 and records[0]["method"] == "HEAD"

    @pytest.mark.parametrize("path", ["/api/auth/login", "/api/auth/logout"])
    def test_auth_post_is_proxied(
        self, running: GatewayEnv, path: str
    ) -> None:
        running.reset_records()
        response = request(
            f"{running.https_base}{path}",
            method="POST",
            body=b'{"username":"x","password":"y"}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status == 200
        records = running.records()
        assert len(records) == 1
        assert records[0]["method"] == "POST"
        assert records[0]["path"] == path

    @pytest.mark.parametrize("path", ["/api/auth/login", "/api/auth/logout"])
    def test_auth_get_is_rejected(
        self, running: GatewayEnv, path: str
    ) -> None:
        running.reset_records()
        response = request(f"{running.https_base}{path}")
        _assert_not_proxied(running, response, where=f"GET {path}")

    @pytest.mark.parametrize(
        "path",
        [
            "/api/auth/me",              # 未暴露的认证路由
            "/api/unknown",              # 未定义的 API
            "/api/workflow-runs/r1",     # 服务间读面（不得对外）
            "/api/ui/",                  # 前缀边界（无 project 段）
            "/api/ui/projectsX/r",       # 相近前缀
        ],
    )
    def test_unlisted_api_paths_fail_closed(
        self, running: GatewayEnv, path: str
    ) -> None:
        running.reset_records()
        response = request(f"{running.https_base}{path}")
        _assert_not_proxied(running, response, where=path)

    def test_bare_prefix_is_normalized_by_nginx(
        self, running: GatewayEnv
    ) -> None:
        """``/api/ui/projects``（缺尾斜杠）被 Nginx **301 补斜杠**。

        这是 Nginx 对 ``location .../`` 的真实语义，**不是**绕过：
        跳转目标仍在白名单前缀内，且本次请求未到达上游（仅在跟随跳转后、
        且路径本身落在白名单前缀内时才会转发）。
        """
        running.reset_records()
        response = request(f"{running.https_base}/api/ui/projects")
        assert response.status == 301
        # Nginx 生成的是绝对 URL（含 scheme/host）
        assert response.headers["location"].endswith("/api/ui/projects/")
        assert running.records() == [], "301 阶段不得到达上游"

        # 跟随到白名单前缀本身：属于允许前缀，后端会对该路径返回 404
        followed = request(f"{running.https_base}/api/ui/projects/")
        records = running.records()
        assert len(records) == 1
        assert records[0]["path"] == "/api/ui/projects/"

    @pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
    def test_unlisted_api_write_methods_fail_closed(
        self, running: GatewayEnv, method: str
    ) -> None:
        running.reset_records()
        response = request(
            f"{running.https_base}/api/workflow-runs/r1",
            method=method,
            body=b"{}" if method != "DELETE" else None,
            headers={"Content-Type": "application/json"},
        )
        _assert_not_proxied(running, response, where=f"{method} /api/workflow-runs")


# ============================================================
# 5. proxy_pass 的 URI 行为
# ============================================================


class TestProxyPassPath:
    def test_full_path_is_preserved(self, running: GatewayEnv) -> None:
        """``proxy_pass`` 不带 URI ⇒ location 前缀**不**被替换。"""
        running.reset_records()
        path = "/api/ui/projects/p1/workflow-runs/r1/nodes"
        assert request(f"{running.https_base}{path}").status == 200
        assert running.records()[0]["path"] == path

    def test_query_string_is_preserved(self, running: GatewayEnv) -> None:
        running.reset_records()
        request(
            f"{running.https_base}/api/ui/projects/p1/workflow-runs/r1"
            "/timeline?since=2026-01-01&limit=5"
        )
        assert running.records()[0]["path"] == (
            "/api/ui/projects/p1/workflow-runs/r1/timeline"
            "?since=2026-01-01&limit=5"
        )

    def test_nested_project_segments_are_preserved(
        self, running: GatewayEnv
    ) -> None:
        running.reset_records()
        path = "/api/ui/projects/proj-a/workflow-runs/wf-1/approvals/gate"
        assert request(f"{running.https_base}{path}").status == 200
        assert running.records()[0]["path"] == path


# ============================================================
# 6. 代理头
# ============================================================


class TestProxyHeaders:
    def test_client_forwarded_for_is_discarded(
        self, running: GatewayEnv
    ) -> None:
        """客户端伪造的 XFF **不得**被透传（网关用 $remote_addr 覆盖）。"""
        running.reset_records()
        request(
            f"{running.https_base}/api/ui/projects/p1/workflow-runs/r1",
            headers={"X-Forwarded-For": "203.0.113.9, 198.51.100.7"},
        )
        headers = running.records()[0]["headers"]
        xff = headers.get("x-forwarded-for", "")
        assert "203.0.113.9" not in xff, xff
        assert "198.51.100.7" not in xff, xff
        assert xff, "网关应设置 X-Forwarded-For"

    def test_forwarded_proto_reflects_real_scheme(
        self, running: GatewayEnv
    ) -> None:
        """客户端伪称 http，网关必须改写为 https（真实连接协议）。"""
        running.reset_records()
        request(
            f"{running.https_base}/api/ui/projects/p1/workflow-runs/r1",
            headers={"X-Forwarded-Proto": "http"},
        )
        assert running.records()[0]["headers"]["x-forwarded-proto"] == "https"

    def test_client_host_header_cannot_be_injected(
        self, running: GatewayEnv
    ) -> None:
        """客户端伪造 ``Host`` **不得**抵达上游（Step 2 实测缺陷的回归）。

        实测证据：改前 ``Host: evil.example`` 会原样到达上游（``$host`` 取自
        客户端）；改为 ``$server_name`` 后上游恒收到部署主机名。
        """
        running.reset_records()
        request(
            f"{running.https_base}/api/ui/projects/p1/workflow-runs/r1",
            headers={"X-Forwarded-Host": "evil.example", "Host": "evil.example"},
        )
        headers = running.records()[0]["headers"]
        assert headers["host"] == TEST_HOST, headers["host"]
        assert headers["x-forwarded-host"] == TEST_HOST, headers["x-forwarded-host"]
        assert "evil.example" not in str(headers)

    def test_no_credential_headers_are_recorded(
        self, running: GatewayEnv
    ) -> None:
        """桩上游只记录白名单头：Cookie / Authorization **不**入库。"""
        running.reset_records()
        request(
            f"{running.https_base}/api/ui/projects/p1/workflow-runs/r1",
            headers={
                "Cookie": "wms_ai_session=TEST-FAKE-SESSION",
                "Authorization": "Bearer TEST-FAKE-TOKEN",
            },
        )
        headers = running.records()[0]["headers"]
        assert "cookie" not in headers
        assert "authorization" not in headers
        blob = str(running.records())
        assert "TEST-FAKE-SESSION" not in blob
        assert "TEST-FAKE-TOKEN" not in blob


# ============================================================
# 7. 仅回环
# ============================================================


class TestLoopbackIsolation:
    def test_published_ports_are_loopback_only(self, running: GatewayEnv) -> None:
        """`docker port` 形如 ``443/tcp -> 127.0.0.1:53312``。"""
        bindings = running.published_bindings()
        lines = [line for line in bindings.splitlines() if line.strip()]
        assert len(lines) >= 2, bindings      # 80 与 443 都应有映射
        for line in lines:
            assert "-> 127.0.0.1:" in line, line
            assert "0.0.0.0" not in line, line
            assert ":::" not in line, line

    def test_no_production_services_are_touched(
        self, running: GatewayEnv
    ) -> None:
        """验证环境只连接桩上游（9010 端口别名），不连真实后端。"""
        assert "server stub:9100;" in running.config_text
        for forbidden in ("postgres", "5432", "wms_ai", "llm"):
            assert forbidden not in running.config_text.lower()


# ============================================================
# 8. 临时产物不进入 Git
# ============================================================


class TestTempArtifacts:
    def test_temp_dir_is_outside_repository(self, env: GatewayEnv) -> None:
        repo = Path(__file__).resolve().parents[1]
        assert not env.workdir.is_relative_to(repo), env.workdir

    def test_certificates_are_not_tracked(self) -> None:
        import shutil as _shutil

        git = _shutil.which("git")
        if git is None:
            pytest.skip("git 不可用：跳过（未执行，非 PASS）")

        repo = Path(__file__).resolve().parents[1]
        for sample in (
            "deploy/nginx/test.key",
            "deploy/nginx/test.crt",
            "tests/test.key",
        ):
            result = subprocess.run(
                [git, "check-ignore", "-q", sample],
                cwd=str(repo),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert result.returncode == 0, f"未被忽略: {sample}"

    def test_official_template_is_unmodified_by_validation(self) -> None:
        """验证过程不得污染正式模板（占位符必须仍在）。"""
        from tests.gateway_validation import TEMPLATE

        text = TEMPLATE.read_text(encoding="utf-8")
        for placeholder in (
            "<PRODUCTION_HOST>",
            "<TLS_CERT_PATH>",
            "<TLS_KEY_PATH>",
            "<STATIC_ROOT>",
        ):
            assert placeholder in text, placeholder
        assert "server 127.0.0.1:8000;" in text
        assert "server stub:9100;" not in text
