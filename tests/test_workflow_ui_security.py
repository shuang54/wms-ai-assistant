"""Workflow UI（Run Detail）安全与契约边界测试 —— **全离线**。

覆盖 Phase 7 Workflow UI Step 1 §八 13/14/17 与 §五 的硬性要求：

    13. 前端 bundle 不包含 API Key 或服务端凭证
    14. 不存在启动 / 推进 / 取消 / 审批写操作
    17. 静态服务**不**代理 API、不持有凭据（因此不可能成为"未受保护的代理"）

另外做**契约交叉校验**：前端引用的每个 API 路径都必须真实存在于 FastAPI
的 OpenAPI schema 中（防止 UI 与后端路由漂移），且不得引用任何写端点。

说明：JS 逻辑本身由 `node --test`（Node 内置，零依赖）覆盖，见
`frontend/tests/*.test.mjs` 与 `docs` 记录；本文件只做**静态结构 / 凭证 /
契约**层面的检查，纯 Python，无需 Node。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

__all__ = [
    "TestNoCredentialsInFrontend",
    "TestNoWriteOrExecutionCapability",
    "TestApiContractAlignment",
    "TestStaticServerBoundary",
    "TestHtmlBoundary",
]

_ROOT = Path(__file__).resolve().parents[1]
_FRONTEND = _ROOT / "frontend"

#: 前端源码文件（页面外壳 + 模块 + 测试 + 本地静态服务）。
_SOURCE_FILES: tuple[Path, ...] = (
    _FRONTEND / "index.html",
    _FRONTEND / "styles.css",
    _FRONTEND / "package.json",
    _FRONTEND / "js" / "api-client.js",
    _FRONTEND / "js" / "state.js",
    _FRONTEND / "js" / "controller.js",
    _FRONTEND / "js" / "render.js",
    _FRONTEND / "js" / "app.js",
    _FRONTEND / "scripts" / "serve-static.mjs",
)

#: 绝不允许出现在前端任何位置的**服务端凭据 / 配置标识**。
#: 这些是**唯一**的取值来源名（环境变量 / 连接串形态）——即便出现在注释里
#: 也说明有人在讨论"往浏览器塞凭据"，一律拒绝。
_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "WORKFLOW_READ_API_KEY",
    "WORKFLOW_COMMAND_API_KEY",
    "AGENT_READ_API_KEY",
    "WORKFLOW_READ_API_PROJECT_ID",
    "AGENT_READ_API_PROJECT_ID",
    "OBSERVABILITY_READ_OPS_USER_IDS",
    "DATABASE_URL",
    "postgresql://",
    "postgresql+psycopg://",
)

#: 赋值 / 构造形态的凭据头（允许文档中作为"禁止项"提到名称，但不允许构造）。
_FORBIDDEN_ASSIGNMENTS: tuple[re.Pattern[str], ...] = (
    re.compile(r"""['"]X-API-Key['"]\s*:""", re.IGNORECASE),
    re.compile(r"""['"]Authorization['"]\s*:""", re.IGNORECASE),
    re.compile(r"""['"]Cookie['"]\s*:""", re.IGNORECASE),
    re.compile(r"\bBearer\s+\S"),
    re.compile(r"""\bapi[_-]?key\s*[:=]\s*['"][^'"]+['"]""", re.IGNORECASE),
    re.compile(r"""\bsecret\s*[:=]\s*['"][^'"]+['"]""", re.IGNORECASE),
)

#: 浏览器持久化 / 环境变量读取（前端不得存放或读取凭据）。
_FORBIDDEN_BROWSER_STORAGE: tuple[str, ...] = (
    "localStorage",
    "sessionStorage",
    "document.cookie",
    "indexedDB",
    "import.meta.env",
    "process.env",
)

#: 写 HTTP 方法（前端不得存在；代码形态，无歧义）。
_FORBIDDEN_WRITE_TOKENS: tuple[str, ...] = (
    "method: 'POST'",
    'method: "POST"',
    "method: 'PUT'",
    'method: "PUT"',
    "method: 'PATCH'",
    'method: "PATCH"',
    "method: 'DELETE'",
    'method: "DELETE"',
)

#: 写端点的**路径形状**（`/api/...` 内出现写动作词）。
#:
#: 为什么用形状而非裸子串：`docs/decisions/` 这类**文档路径**与页面文案会
#: 含 `decision` 子串，但它们不是端点引用；只有出现在 `'/api/...'` /
#: `` `/api/...` `` / `"/api/..."` 引号或模板字面量内的动作词才是写端点。
_WRITE_ENDPOINT_PATTERN: re.Pattern[str] = re.compile(
    r"""['"`]/api/[^'"`\s]*\b(advance|cancel|decision|start|retry|resume)\b""",
    re.IGNORECASE,
)


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件: {path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    """剥离 JS 行注释 / 块注释与 HTML 注释，得到**可执行代码**。

    为什么需要：文档里**允许提及**禁用项（例如注释写"密钥绝不进入
    localStorage"），但代码**不得实施**该行为。凭证标识（环境变量名 /
    连接串）仍用**原文**扫描（连注释里也不允许出现），行为性 token 则只扫
    剥注释后的代码。
    """
    without_block = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    without_html = re.sub(r"<!--.*?-->", " ", without_block, flags=re.DOTALL)
    return re.sub(r"(?m)//[^\n]*$", " ", without_html)


def _code_only(path: Path) -> str:
    return _strip_comments(_read(path))


class TestNoCredentialsInFrontend:
    """§八.13：前端 bundle 不包含 API Key 或任何服务端凭证。"""

    def test_frontend_files_exist(self) -> None:
        for path in _SOURCE_FILES:
            assert path.is_file(), f"缺少前端文件: {path}"

    @pytest.mark.parametrize("token", _FORBIDDEN_TOKENS)
    def test_forbidden_credential_tokens_absent(self, token: str) -> None:
        for path in _SOURCE_FILES:
            assert token not in _read(path), f"{path.name} 出现禁用凭据标识: {token}"

    def test_no_credential_header_construction(self) -> None:
        for path in _SOURCE_FILES:
            source = _read(path)
            for pattern in _FORBIDDEN_ASSIGNMENTS:
                match = pattern.search(source)
                assert match is None, (
                    f"{path.name} 构造了凭据相关头 / 值: {match.group(0)!r}"
                )

    def test_no_browser_storage_or_env_credential_access(self) -> None:
        for path in _SOURCE_FILES:
            source = _code_only(path)   # 注释中的"禁止项说明"不算实施
            for token in _FORBIDDEN_BROWSER_STORAGE:
                assert token not in source, (
                    f"{path.name} 使用了浏览器存储 / 环境变量读取: {token}"
                )

    def test_api_client_declares_only_accept_header(self) -> None:
        """客户端只允许构造 `Accept` 头（结构性证明：无其他头字面量）。"""
        source = _read(_FRONTEND / "js" / "api-client.js")
        assert "Accept: 'application/json'" in source or (
            'Accept: "application/json"' in source
        ), "缺少 Accept 头声明"
        # headers 对象内不得出现第二个键
        headers_block = re.search(
            r"headers:\s*\{([^}]*)\}", source, re.DOTALL
        )
        assert headers_block is not None, "未找到 headers 构造"
        keys = re.findall(r"(\w+)\s*:", headers_block.group(1))
        assert keys == ["Accept"], f"headers 含非预期键: {keys}"

    def test_only_get_method_is_ever_used(self) -> None:
        source = _read(_FRONTEND / "js" / "api-client.js")
        methods = set(re.findall(r"method:\s*['\"](\w+)['\"]", source))
        assert methods == {"GET"}, f"客户端出现非 GET 方法: {methods}"


class TestNoWriteOrExecutionCapability:
    """§八.14 + §七：不存在启动 / 推进 / 取消 / 审批写 / 重试能力。"""

    @pytest.mark.parametrize("token", _FORBIDDEN_WRITE_TOKENS)
    def test_no_write_http_methods(self, token: str) -> None:
        for path in _SOURCE_FILES:
            assert token not in _code_only(path), (
                f"{path.name} 出现写方法: {token}"
            )

    def test_no_write_endpoint_paths_referenced(self) -> None:
        """不得引用任何 `/api/.../{advance|cancel|decision|start|retry|resume}`。"""
        for path in _SOURCE_FILES:
            match = _WRITE_ENDPOINT_PATTERN.search(_code_only(path))
            assert match is None, (
                f"{path.name} 引用了写端点: {match.group(0)!r}"
            )

    def test_no_execution_entrypoint_helpers(self) -> None:
        forbidden = (
            "advance(",
            "request_cancel(",
            "submit_approval_decision(",
            "decide(",
            "retry(",
            "resume(",
            "start_run(",
        )
        for path in _SOURCE_FILES:
            source = _code_only(path)
            for token in forbidden:
                assert token not in source, f"{path.name} 出现执行入口: {token}"

    def test_no_write_controls_in_page(self) -> None:
        """页面不得出现批准 / 拒绝 / 重试执行 / 取消 / 启动按钮文案。"""
        html = _code_only(_FRONTEND / "index.html")
        for label in ("批准", "拒绝", "取消 Run", "启动", "重试执行", "恢复"):
            assert label not in html, f"index.html 出现写操作文案: {label}"

    def test_only_two_buttons_declared(self) -> None:
        html = _read(_FRONTEND / "index.html")
        button_ids = re.findall(r'<button[^>]*id="([^"]+)"', html)
        assert sorted(button_ids) == ["run-refresh", "run-submit"], button_ids


class TestApiContractAlignment:
    """前端引用的 API 路径必须与真实 OpenAPI schema 一致（防漂移）。"""

    #: 允许前端引用的**只读**端点模板（真实存在，取自 openapi）。
    EXPECTED_TEMPLATES = (
        "/api/workflow-runs/{run_id}",
        "/api/workflow-runs/{run_id}/nodes",
        "/api/workflow-runs/{run_id}/timeline",
        "/api/workflow-runs/{run_id}/approvals/{node_id}",
        "/api/workflow-approvals/{approval_id}",
    )

    def test_templates_exist_in_openapi_and_are_get_only(self) -> None:
        from backend.app.main import app

        paths = app.openapi()["paths"]
        for template in self.EXPECTED_TEMPLATES:
            assert template in paths, f"OpenAPI 缺少路由: {template}"
            assert set(paths[template].keys()) == {"get"}, (
                f"{template} 不是纯 GET"
            )

    def test_client_paths_match_openapi_shape(self) -> None:
        """客户端拼出的路径必须能匹配到真实模板（无臆造 / 无旧路径）。"""
        from backend.app.main import app

        paths = set(app.openapi()["paths"])
        source = _read(_FRONTEND / "js" / "api-client.js")
        literal_paths = re.findall(r"`(/api/[^`]+)`", source)
        assert literal_paths, "未找到客户端 API 路径字面量"

        def to_template(value: str) -> str:
            # 把 ${encodeURIComponent(...)} 占位替换为 {param}
            return re.sub(r"\$\{[^}]+\}", "{param}", value)

        for raw in literal_paths:
            templated = to_template(raw).replace("{param}", "{run_id}")
            # 逐段匹配真实模板
            matches = [
                candidate
                for candidate in paths
                if _same_shape(candidate, to_template(raw))
            ]
            assert matches, f"客户端路径无对应真实路由: {raw} -> {templated}"

    def test_client_does_not_reference_write_endpoints(self) -> None:
        source = _read(_FRONTEND / "js" / "api-client.js")
        for forbidden in (
            "/api/workflow-runs`",
            "/api/workflow-definitions",
            "/api/agent/",
            "/api/chat",
            "/api/tools",
        ):
            # 只允许 read 路径；`POST /api/workflow-runs` 不在客户端出现
            assert f"POST {forbidden}" not in source
        assert "/api/workflow-approvals/${" in source

    def test_static_route_shapes_are_known(self) -> None:
        """客户端使用的 5 个路径 → 形状集合必须与 EXPECTED_TEMPLATES 相等。"""
        source = _read(_FRONTEND / "js" / "api-client.js")
        literal_paths = re.findall(r"`(/api/[^`]+)`", source)
        shapes = {
            re.sub(r"\$\{[^}]+\}", "{p}", path).replace("{p}", "{p}")
            for path in literal_paths
        }
        expected = {
            "/api/workflow-runs/{p}",
            "/api/workflow-runs/{p}/nodes",
            "/api/workflow-runs/{p}/timeline",
            "/api/workflow-runs/{p}/approvals/{p}",
            "/api/workflow-approvals/{p}",
        }
        assert shapes == expected, f"路径集合不一致: {shapes ^ expected}"


def _same_shape(template: str, produced: str) -> bool:
    """比较两个路径模板的"段形状"（参数名无关）。"""
    def segments(value: str) -> list[str]:
        return [
            "{}" if segment.startswith("{") else segment
            for segment in value.strip("/").split("/")
        ]

    return segments(template) == segments(produced)


class TestStaticServerBoundary:
    """§八.17 + §五：静态服务不得成为"未受保护的 API 代理"。"""

    def test_server_does_not_proxy_api(self) -> None:
        source = _read(_FRONTEND / "scripts" / "serve-static.mjs")
        # 明确拒绝 /api/ 前缀（不得转发）
        assert "startsWith('/api/')" in source, "静态服务未显式拒绝 /api 路径"
        assert "501" in source, "静态服务未对 /api 返回明确不可用状态"

    def test_server_has_no_credential_handling(self) -> None:
        source = _code_only(_FRONTEND / "scripts" / "serve-static.mjs")
        for token in ("X-API-Key", "apiKey", "api_key", "Authorization"):
            assert token not in source, f"静态服务涉及凭据: {token}"
        # 不得引入反向代理 / 上游转发机制
        for token in ("proxy", "upstream", "http.request", "https.request"):
            assert token not in source.lower(), f"静态服务出现代理机制: {token}"

    def test_server_binds_loopback_only_and_rejects_host_override(self) -> None:
        source = _read(_FRONTEND / "scripts" / "serve-static.mjs")
        assert "127.0.0.1" in source, "未绑定回环地址"
        assert "不使用 --host" in source or "不接受 --host" in source, (
            "未显式拒绝 --host 覆盖"
        )

    def test_server_guards_path_traversal(self) -> None:
        source = _code_only(_FRONTEND / "scripts" / "serve-static.mjs")
        assert "startsWith(ROOT + sep)" in source, "缺少路径穿越防护"

    def test_no_dependencies_declared(self) -> None:
        """前端不得引入任何第三方依赖（零依赖静态页面）。"""
        manifest = json.loads(_read(_FRONTEND / "package.json"))
        assert manifest.get("dependencies") == {}, manifest.get("dependencies")
        assert manifest.get("devDependencies") == {}, manifest.get(
            "devDependencies"
        )

    def test_manifest_declares_esm(self) -> None:
        manifest = json.loads(_read(_FRONTEND / "package.json"))
        assert manifest.get("type") == "module"
        assert manifest.get("scripts", {}).get("test") == "node --test tests/"


class TestHtmlBoundary:
    """页面外壳的安全边界（CSP / 无第三方 / 无内联数据脚本）。"""

    def test_csp_declared_and_self_only(self) -> None:
        html = _read(_FRONTEND / "index.html")
        match = re.search(
            r'http-equiv="Content-Security-Policy"\s+content="([^"]+)"', html
        )
        assert match is not None, "缺少 CSP"
        policy = match.group(1)
        assert "default-src 'self'" in policy
        assert "connect-src 'self'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "form-action 'none'" in policy

    def test_no_third_party_origins(self) -> None:
        for path in (_FRONTEND / "index.html", _FRONTEND / "styles.css"):
            source = _read(path)
            for scheme in ("http://", "https://", "//cdn", "//unpkg", "//fonts"):
                assert scheme not in source, f"{path.name} 引用了外部来源: {scheme}"

    def test_no_inline_script_body(self) -> None:
        html = _read(_FRONTEND / "index.html")
        inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
        assert inline == [], "存在内联脚本（应从模块文件加载）"

    def test_html_declares_not_connected_state(self) -> None:
        """页面必须**如实**标注数据访问尚未接通（不得假装端到端完成）。"""
        html = _read(_FRONTEND / "index.html")
        assert "数据访问尚未接通" in html
        assert "401" in html

    def test_no_auto_loaded_placeholder_data(self) -> None:
        """页面初始不得包含任何虚构 Run 数据。"""
        html = _read(_FRONTEND / "index.html")
        assert "workflow_run_id" not in html
        assert "CURRENT_NODE" not in html


class TestJavaScriptSuite:
    """§八：JS 逻辑由 Node 内置测试运行器覆盖（无 Node 则明确跳过）。"""

    def test_node_builtin_test_suite_passes(self) -> None:
        import shutil
        import subprocess

        node = shutil.which("node")
        if node is None:
            pytest.skip("Node 不可用：跳过前端 JS 测试（未执行，非 PASS）")

        result = subprocess.run(
            [node, "--test", "tests/"],
            cwd=str(_FRONTEND),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        assert result.returncode == 0, (
            f"前端 JS 测试失败（exit {result.returncode}）:\n"
            f"{(result.stdout or '')[-2000:]}\n{(result.stderr or '')[-2000:]}"
        )


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class TestPageIsRunnable:
    """§十：页面**可运行**（启动真实静态服务并抓取）。

    同时验证硬性安全边界：静态服务**不代理** `/api/*`（因此不可能成为
    "未受保护的代理"），并拒绝非回环绑定与路径穿越。
    """

    @pytest.fixture()
    def static_server(self):
        import shutil
        import socket
        import subprocess
        import time

        node = shutil.which("node")
        if node is None:
            pytest.skip("Node 不可用：跳过页面可运行性验证（未执行，非 PASS）")

        port = _free_port()
        process = subprocess.Popen(
            [node, "scripts/serve-static.mjs", "--port", str(port)],
            cwd=str(_FRONTEND),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                process.kill()
                pytest.fail("静态服务未在 20s 内就绪")

            yield port
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()

    def _get(self, port: int, path: str):
        import urllib.error
        import urllib.request

        url = f"http://127.0.0.1:{port}{path}"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return response.status, response.headers.get("Content-Type", ""), (
                    response.read().decode("utf-8", errors="replace")
                )
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Content-Type", ""), (
                error.read().decode("utf-8", errors="replace")
            )

    def test_index_page_served(self, static_server) -> None:
        status, content_type, body = self._get(static_server, "/")

        assert status == 200
        assert "text/html" in content_type
        assert "Workflow Run Detail" in body
        # 未接通状态必须如实出现在页面上
        assert "数据访问尚未接通" in body

    def test_module_assets_served_as_javascript(self, static_server) -> None:
        for path in (
            "/js/app.js",
            "/js/api-client.js",
            "/js/state.js",
            "/js/controller.js",
            "/js/render.js",
            "/styles.css",
        ):
            status, content_type, _body = self._get(static_server, path)
            assert status == 200, path
            assert (
                "javascript" in content_type or "css" in content_type
            ), f"{path} 类型错误: {content_type}"

    def test_api_paths_are_not_proxied(self, static_server) -> None:
        """**关键**：静态服务不得转发 `/api/*`（也不是代理）。"""
        for path in (
            "/api/workflow-runs/wf-1",
            "/api/workflow-runs/wf-1/nodes",
        ):
            status, _content_type, body = self._get(static_server, path)
            assert status == 501, f"{path} 竟被处理: {status}"
            assert "不代理 API" in body

    def test_path_traversal_rejected(self, static_server) -> None:
        for path in (
            "/../backend/app/main.py",
            "/..%2fbackend%2fapp%2fmain.py",
            "/%2e%2e/%2e%2e/.env",
        ):
            status, _content_type, body = self._get(static_server, path)
            assert status in {403, 404}, f"{path} 未被拒绝: {status}"
            assert "FASTAPI" not in body.upper()
            assert "DATABASE_URL" not in body

    def test_directory_listing_disabled(self, static_server) -> None:
        status, _content_type, _body = self._get(static_server, "/js/")
        assert status == 403

    def test_non_loopback_host_is_refused(self) -> None:
        """`--host` 一律拒绝 ⇒ 不可能把页面暴露到外部网卡。"""
        import shutil
        import subprocess

        node = shutil.which("node")
        if node is None:
            pytest.skip("Node 不可用：跳过（未执行，非 PASS）")

        result = subprocess.run(
            [node, "scripts/serve-static.mjs", "--host", "0.0.0.0"],
            cwd=str(_FRONTEND),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        assert result.returncode != 0
        combined = (result.stderr or "") + (result.stdout or "")
        assert "回环" in combined

    def test_only_get_and_head_allowed(self, static_server) -> None:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            f"http://127.0.0.1:{static_server}/",
            data=b"x=1",
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        assert status == 405
