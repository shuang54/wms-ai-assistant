"""Phase 8 Step 1 —— 生产部署模板的离线校验（DB-free / 无网络）。

设计原则：**解析配置结构**而不是做字符串包含判断。

* Nginx 模板由本文件内的最小解析器（块 / 指令 / 引号）构建语法树，
  因此"某个 location 是否 proxy_pass""limit_except 允许哪些方法"
  都是对**结构**的断言，而不是对文本片段的猜测。
* 环境变量模板按 KEY=VALUE 解析为映射后断言取值。
* 转发头片段单独解析，验证其为 nginx 变量覆盖而非客户端透传。
* 开发网关的生产禁用是**真实执行**（启动子进程）验证，不是读源码。

`nginx -t` 无法执行（本机未安装 nginx）⇒ 明确 skip 并标注 NOT VERIFIED，
不把模板检查冒充为真实部署验证。
"""
from __future__ import annotations

import json
import re
import re as _re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEPLOY = _REPO_ROOT / "deploy"
_NGINX_TEMPLATE = _DEPLOY / "nginx" / "wms-workflow-ui.conf.template"
_PROXY_SNIPPET = _DEPLOY / "nginx" / "proxy-headers.conf"
_ENV_TEMPLATE = _DEPLOY / "production.env.example"
_DEPLOY_README = _DEPLOY / "README.md"

__all__ = [
    "TestNginxTemplateStructure",
    "TestPathAndMethodWhitelist",
    "TestTrustedProxyHeaders",
    "TestNoSecretsOrTestAssets",
    "TestEnvTemplate",
    "TestDevelopmentGatewayIsNotProduction",
    "TestTemplateIsNotVerifiedDeployment",
]


# ============================================================
# 最小 Nginx 配置解析器（块 / 指令 / 引号 / 注释）
# ============================================================


@dataclass
class Directive:
    """一条 nginx 指令或块。"""

    name: str
    args: list[str]
    line: int
    children: list["Directive"] | None = None

    @property
    def is_block(self) -> bool:
        return self.children is not None


@dataclass
class NginxConfig:
    root: Directive = field(default_factory=lambda: Directive("__root__", [], 0, []))

    def walk(self) -> list[Directive]:
        out: list[Directive] = []

        def visit(node: Directive) -> None:
            for child in node.children or []:
                out.append(child)
                visit(child)

        visit(self.root)
        return out

    def top_level(self, name: str) -> list[Directive]:
        return [
            child
            for child in (self.root.children or [])
            if child.name == name
        ]


def _split_args(text: str) -> list[str]:
    """按空白切分参数，保留双引号内的整体（含空串 ``""``）。"""
    return [
        (m.group(1) if m.group(1) is not None else m.group(2))
        for m in re.finditer(r'"([^"]*)"|(\S+)', text)
    ]


def _strip_comment(line: str) -> str:
    """去掉行内注释（本模板中不存在引号内 ``#``）。"""
    idx = line.find("#")
    return line if idx < 0 else line[:idx]


def parse_nginx(text: str) -> NginxConfig:
    """解析 nginx 配置为语法树；大括号不平衡 ⇒ 抛 ``SyntaxError``。"""
    config = NginxConfig()
    stack: list[Directive] = [config.root]
    buffer = ""
    start_line = 0

    for number, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if not buffer:
            start_line = number
        buffer = f"{buffer} {line}".strip()

        while buffer:
            if buffer.startswith("}"):
                buffer = buffer[1:].strip()
                if len(stack) == 1:
                    raise SyntaxError(f"第 {number} 行：多余的 '}}'")
                stack.pop()
                continue

            match = re.search(r"[;{}]", buffer)
            if match is None:
                break
            body = buffer[: match.start()].strip()
            terminator = match.group(0)
            buffer = buffer[match.end() :].strip()
            if not body:
                raise SyntaxError(f"第 {number} 行：空指令")
            parts = _split_args(body)
            if terminator == ";":
                stack[-1].children.append(
                    Directive(parts[0], parts[1:], start_line, None)
                )
            elif terminator == "{":
                block = Directive(parts[0], parts[1:], start_line, [])
                stack[-1].children.append(block)
                stack.append(block)
            else:  # 单独的 '}' 已在上方处理
                raise SyntaxError(f"第 {number} 行：无法解析 {body!r}")

    if buffer:
        raise SyntaxError(f"文件末尾存在未结束的指令: {buffer!r}")
    if len(stack) != 1:
        raise SyntaxError("大括号不平衡（存在未闭合的块）")
    return config


# ============================================================
# 夹具：解析后的模板 / 片段
# ============================================================


@pytest.fixture(scope="module")
def nginx_config() -> NginxConfig:
    if not _NGINX_TEMPLATE.is_file():
        pytest.fail(f"缺少生产网关模板: {_NGINX_TEMPLATE}")
    return parse_nginx(_NGINX_TEMPLATE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def https_server(nginx_config: NginxConfig) -> Directive:
    for block in nginx_config.top_level("server"):
        listens = [
            child for child in block.children or [] if child.name == "listen"
        ]
        if any("443" in child.args for child in listens):
            return block
    pytest.fail("模板中缺少监听 443 的 server 块")


def _locations(server: Directive) -> dict[str, Directive]:
    """``{'<modifier> <path>': 节点}``。"""
    out: dict[str, Directive] = {}
    for child in server.children or []:
        if child.name != "location":
            continue
        out[" ".join(child.args)] = child
    return out


def _directives(node: Directive, name: str) -> list[Directive]:
    return [child for child in (node.children or []) if child.name == name]


# ============================================================
# 1. 结构：upstream / server / TLS
# ============================================================


class TestNginxTemplateStructure:
    def test_template_parses_into_balanced_blocks(
        self, nginx_config: NginxConfig
    ) -> None:
        """解析成功即证明大括号 / 指令终止符结构合法。"""
        assert nginx_config.top_level("server"), "缺少 server 块"

    def test_upstream_is_loopback_only(self, nginx_config: NginxConfig) -> None:
        """后端上游必须是回环地址（不得指向公网 / 其它主机）。"""
        upstreams = nginx_config.top_level("upstream")
        assert len(upstreams) == 1, upstreams
        upstream = upstreams[0]
        assert upstream.args == ["wms_workflow_ui_backend"]

        servers = _directives(upstream, "server")
        assert len(servers) == 1
        assert servers[0].args == ["127.0.0.1:8000"]

    def test_https_server_uses_modern_tls_only(
        self, https_server: Directive
    ) -> None:
        protocols = _directives(https_server, "ssl_protocols")
        assert len(protocols) == 1
        assert set(protocols[0].args) == {"TLSv1.2", "TLSv1.3"}
        assert not {"SSLv3", "TLSv1", "TLSv1.1"} & set(protocols[0].args)

    def test_certificate_paths_are_placeholders(
        self, https_server: Directive
    ) -> None:
        for name in ("ssl_certificate", "ssl_certificate_key"):
            found = _directives(https_server, name)
            assert len(found) == 1, name
            value = found[0].args[0]
            assert value.startswith("<") and value.endswith(">"), value

    def test_server_tokens_disabled_and_hsts_present(
        self, https_server: Directive
    ) -> None:
        tokens = _directives(https_server, "server_tokens")
        assert [d.args for d in tokens] == [["off"]]

        headers = {
            d.args[0]: d.args for d in _directives(https_server, "add_header")
        }
        assert "Strict-Transport-Security" in headers
        assert headers["Strict-Transport-Security"][1].startswith("max-age=")

    def test_http_server_only_redirects_to_https(
        self, nginx_config: NginxConfig
    ) -> None:
        plain = [
            block
            for block in nginx_config.top_level("server")
            if any(
                "80" in child.args and "443" not in child.args
                for child in block.children or []
                if child.name == "listen"
            )
        ]
        assert len(plain) == 1
        block = plain[0]

        returns = _directives(block, "return")
        assert returns and returns[0].args[0] == "301"
        assert returns[0].args[1].startswith("https://")

        # 明文端口不得承载业务：不得代理、不得提供静态内容
        assert not _directives(block, "proxy_pass")
        assert not _directives(block, "root")

    def test_backend_port_is_not_exposed_by_listen(self) -> None:
        """模板不得直接 ``listen 8000``（后端端口不由网关暴露）。"""
        text = _NGINX_TEMPLATE.read_text(encoding="utf-8")
        config = parse_nginx(text)
        listens = [d for d in config.walk() if d.name == "listen"]
        assert listens, "缺少 listen 指令"
        for directive in listens:
            assert directive.args[0] in {"80", "443"}, directive.args


# ============================================================
# 2. 路径与方法白名单
# ============================================================


class TestPathAndMethodWhitelist:
    def test_location_set_is_exactly_the_allowlist(
        self, https_server: Directive
    ) -> None:
        expected = {
            "= /",
            "= /index.html",
            "= /styles.css",
            "^~ /js/",
            "^~ /api/ui/projects/",
            "= /api/auth/login",
            "= /api/auth/logout",
            "^~ /api/",
            "/",
        }
        assert set(_locations(https_server)) == expected

    def test_proxied_paths_are_only_ui_and_auth(
        self, https_server: Directive
    ) -> None:
        proxied = {
            key
            for key, node in _locations(https_server).items()
            if _directives(node, "proxy_pass")
        }
        assert proxied == {
            "^~ /api/ui/projects/",
            "= /api/auth/login",
            "= /api/auth/logout",
        }

    def test_no_blanket_api_proxy(self, https_server: Directive) -> None:
        """``/api/`` 兜底必须是 404，**不得** ``proxy_pass``（禁止全路径转发）。"""
        catch_all = _locations(https_server)["^~ /api/"]
        assert not _directives(catch_all, "proxy_pass")
        returns = _directives(catch_all, "return")
        assert [d.args for d in returns] == [["404"]]

    def test_root_catch_all_is_rejected(self, https_server: Directive) -> None:
        catch_all = _locations(https_server)["/"]
        assert not _directives(catch_all, "proxy_pass")
        assert [d.args for d in _directives(catch_all, "return")] == [["404"]]

    def test_ui_surface_is_read_only(self, https_server: Directive) -> None:
        """``/api/ui/`` 只允许 GET（HEAD 隐含）——部署不得把只读面变写面。"""
        node = _locations(https_server)["^~ /api/ui/projects/"]
        limits = _directives(node, "limit_except")
        assert len(limits) == 1
        assert limits[0].args == ["GET"]
        assert [d.args for d in _directives(limits[0], "deny")] == [["all"]]

    @pytest.mark.parametrize(
        "location", ["= /api/auth/login", "= /api/auth/logout"]
    )
    def test_auth_routes_are_post_only(
        self, https_server: Directive, location: str
    ) -> None:
        node = _locations(https_server)[location]
        limits = _directives(node, "limit_except")
        assert len(limits) == 1
        assert limits[0].args == ["POST"]
        assert [d.args for d in _directives(limits[0], "deny")] == [["all"]]

    def test_unused_auth_route_is_not_exposed(
        self, https_server: Directive
    ) -> None:
        """``/api/auth/me`` 页面未使用 ⇒ 模板不暴露它。"""
        assert "= /api/auth/me" not in _locations(https_server)

    def test_no_cors_headers_anywhere(self, nginx_config: NginxConfig) -> None:
        """同源部署不需要 CORS；不得添加任何 ``Access-Control-*``。"""
        for directive in nginx_config.walk():
            if directive.name != "add_header":
                continue
            header = directive.args[0].lower()
            assert not header.startswith("access-control"), directive.args


# ============================================================
# 3. 可信代理与转发头
# ============================================================


class TestTrustedProxyHeaders:
    @pytest.fixture(scope="class")
    def snippet(self) -> NginxConfig:
        if not _PROXY_SNIPPET.is_file():
            pytest.fail(f"缺少转发头片段: {_PROXY_SNIPPET}")
        return parse_nginx(_PROXY_SNIPPET.read_text(encoding="utf-8"))

    def test_every_proxied_location_includes_the_snippet(
        self, https_server: Directive
    ) -> None:
        for key, node in _locations(https_server).items():
            if not _directives(node, "proxy_pass"):
                continue
            includes = _directives(node, "include")
            assert [d.args for d in includes] == [["proxy-headers.conf"]], key

    def test_forwarded_headers_are_overridden_not_passed_through(
        self, snippet: NginxConfig
    ) -> None:
        """客户端自带 XFF 必须被**丢弃**（用 ``$remote_addr``）。"""
        headers = {
            directive.args[0]: directive.args[1]
            for directive in snippet.root.children or []
            if directive.name == "proxy_set_header"
        }
        assert headers["X-Forwarded-For"] == "$remote_addr"
        assert headers["X-Forwarded-For"] != "$proxy_add_x_forwarded_for"
        assert headers["X-Forwarded-Proto"] == "$scheme"
        #: ``$host`` 取自**客户端** Host 头，可被伪造（Step 2 已用真实请求证实）；
        #: 必须用 ``$server_name``（部署时的 <PRODUCTION_HOST>）覆盖。
        assert headers["Host"] == "$server_name"
        assert headers["X-Forwarded-Host"] == "$server_name"
        assert headers["Host"] != "$host"
        assert headers["X-Forwarded-Host"] != "$host"

    def test_host_header_uses_server_name_not_client_host(
        self, snippet: NginxConfig
    ) -> None:
        """Host 头注入回归：``$host`` 会透传客户端 Host，``$server_name`` 不会。"""
        pairs = {
            directive.args[0]: directive.args[1]
            for directive in snippet.root.children or []
            if directive.name == "proxy_set_header"
        }
        for header in ("Host", "X-Forwarded-Host"):
            assert pairs[header] == "$server_name", (header, pairs[header])

    def test_no_client_header_passthrough(self, snippet: NginxConfig) -> None:
        """不得出现 ``$http_*``（那等于把客户端头原样传给后端）。"""
        text = _PROXY_SNIPPET.read_text(encoding="utf-8")
        assert "$http_" not in text

    def test_backend_does_not_trust_forwarded_headers_by_default(self) -> None:
        """后端代码不读取 X-Forwarded-*（认证/授权不依赖代理头）。"""
        backend = _REPO_ROOT / "backend"
        hits: list[str] = []
        for path in backend.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"X-Forwarded|x_forwarded", text, re.IGNORECASE):
                hits.append(str(path.relative_to(_REPO_ROOT)))
        assert hits == [], f"后端开始读取转发头，需重新审阅信任边界: {hits}"

    def test_gateway_never_rewrites_or_hides_credentials(
        self, nginx_config: NginxConfig
    ) -> None:
        """部署不得弱化认证：会话 Cookie / Authorization 必须原样透传。

        网关**不**认证，因此它既不能剥离 Cookie，也不能隐藏 ``Set-Cookie``，
        更不能伪造或清空 ``Authorization``。
        """
        forbidden_headers = {"cookie", "authorization", "set-cookie"}
        for directive in nginx_config.walk():
            if directive.name == "proxy_set_header":
                assert (
                    directive.args[0].lower() not in forbidden_headers
                ), f"网关改写了认证头: {directive.args}"
            if directive.name == "proxy_hide_header":
                assert (
                    directive.args[0].lower() not in forbidden_headers
                ), f"网关隐藏了认证头: {directive.args}"
            if directive.name == "proxy_set_header":
                # 不得把客户端头直接当作上游头（$http_ 透传）
                assert "$http_" not in directive.args[1], directive.args

    def test_gateway_does_not_disable_request_headers(
        self, nginx_config: NginxConfig
    ) -> None:
        for directive in nginx_config.walk():
            if directive.name == "proxy_pass_request_headers":
                assert directive.args == ["on"], directive.args


# ============================================================
# 4. 无密钥 / 无测试资产 / 无开发组件
# ============================================================


class TestNoSecretsOrTestAssets:
    """部署模板不得含密钥 / 测试资产 / 开发组件。

    ⚠️ 断言对象是**解析后的配置值**（nginx 指令参数、env 的值），
    而不是原始文本 —— 注释里说明"不得使用 dev-gateway"是**允许**的，
    只有真的把开发组件写进指令才是违规。
    """

    @staticmethod
    def _deploy_files() -> dict[str, Path]:
        assert _DEPLOY.is_dir(), "缺少 deploy/ 目录"
        return {
            str(path.relative_to(_REPO_ROOT)): path
            for path in sorted(_DEPLOY.rglob("*"))
            if path.is_file()
        }

    @staticmethod
    def _config_tokens(path: Path) -> list[str]:
        """提取**配置值**：nginx 指令名+参数 / env 的取值；其它文件返回空。"""
        if path.suffix in {".template", ".conf"}:
            config = parse_nginx(path.read_text(encoding="utf-8"))
            tokens: list[str] = []
            for directive in config.walk():
                tokens.append(directive.name)
                tokens.extend(directive.args)
            return tokens
        if path.name.endswith(".env.example"):
            parsed: dict[str, str] = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, _, value = stripped.partition("=")
                parsed[key.strip()] = value.strip()
            return list(parsed.values())
        return []

    def test_no_private_key_or_certificate_material(self) -> None:
        for name, path in self._deploy_files().items():
            text = path.read_text(encoding="utf-8", errors="replace")
            assert "PRIVATE KEY-----" not in text, name
            assert "-----BEGIN CERTIFICATE" not in text, name

    def test_no_test_database_or_test_credentials(self) -> None:
        """不得把测试库地址 / 测试凭据复制成生产配置。"""
        for name, path in self._deploy_files().items():
            for token in self._config_tokens(path):
                for banned in ("wms_ai_test", "wms_test", "5433", "wms_test_pw"):
                    assert banned not in token, f"{name} 含测试资产: {banned}"

    def test_no_real_ipv4_outside_loopback(self) -> None:
        pattern = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
        for name, path in self._deploy_files().items():
            for token in self._config_tokens(path):
                if not pattern.match(token):
                    continue
                assert token == "127.0.0.1", f"{name} 出现非回环地址: {token}"

    def test_loopback_upstream_is_present(self) -> None:
        """反向校验：确实配置了回环上游（否则上面的断言恒真）。"""
        tokens: list[str] = []
        for path in self._deploy_files().values():
            tokens.extend(self._config_tokens(path))
        assert "127.0.0.1:8000" in tokens, tokens[:20]

    def test_no_development_gateway_or_launcher(self) -> None:
        """生产模板不得依赖 dev-gateway / 本地启动脚本 / 静态外壳服务。"""
        for name, path in self._deploy_files().items():
            for token in self._config_tokens(path):
                for banned in ("dev-gateway", "serve-static", "start-workflow-ui"):
                    assert banned not in token, f"{name} 引用了开发组件: {banned}"

    def test_gitignore_protects_tls_material(self) -> None:
        git = shutil.which("git")
        if git is None:
            pytest.skip("git 不可用：跳过忽略规则验证（未执行，非 PASS）")

        samples = [
            "deploy/nginx/server.key",
            "deploy/nginx/fullchain.pem",
            "deploy/nginx/site.crt",
            "deploy/production.env",
            "backend/app/certs/client.p12",
        ]
        for sample in samples:
            result = subprocess.run(
                [git, "check-ignore", "-q", sample],
                cwd=str(_REPO_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert result.returncode == 0, f"未被忽略: {sample}"

    def test_env_template_is_not_ignored(self) -> None:
        """模板本身必须能入库（否则部署说明缺失）。"""
        git = shutil.which("git")
        if git is None:
            pytest.skip("git 不可用：跳过（未执行，非 PASS）")

        result = subprocess.run(
            [git, "check-ignore", "-q", "deploy/production.env.example"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert result.returncode != 0, "模板被 .gitignore 误排除"


class TestEnvTemplate:
    @staticmethod
    def _parse(path: Path) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            out[key.strip()] = value.strip()
        return out

    @pytest.fixture(scope="class")
    def env(self) -> dict[str, str]:
        if not _ENV_TEMPLATE.is_file():
            pytest.fail(f"缺少生产环境变量模板: {_ENV_TEMPLATE}")
        return self._parse(_ENV_TEMPLATE)

    def test_cookie_must_be_secure_in_production(self, env: dict[str, str]) -> None:
        assert env["AUTH_COOKIE_SECURE"] == "true"

    def test_samesite_is_not_none_without_secure(self, env: dict[str, str]) -> None:
        assert env["AUTH_COOKIE_SAMESITE"] in {"lax", "strict"}

    def test_backend_binds_loopback(self, env: dict[str, str]) -> None:
        assert env["APP_HOST"] == "127.0.0.1"
        assert env["APP_PORT"] == "8000"

    def test_app_env_is_production(self, env: dict[str, str]) -> None:
        assert env["APP_ENV"] == "production"

    @pytest.mark.parametrize(
        "key",
        [
            "DATABASE_URL",
            "WORKFLOW_READ_API_KEY",
            "WORKFLOW_COMMAND_API_KEY",
            "LLM_API_KEY",
            "WMS_DATABASE_PASSWORD",
        ],
    )
    def test_sensitive_values_are_placeholders(
        self, env: dict[str, str], key: str
    ) -> None:
        value = env[key]
        assert value.startswith("<") and value.endswith(">"), (key, value)

    def test_no_hardcoded_credentials(self, env: dict[str, str]) -> None:
        for key, value in env.items():
            lowered = value.lower()
            for token in ("wms_test", "test_pw", "postgresql+psycopg://"):
                assert token not in lowered, f"{key} 含疑似凭据/连接串: {value}"


# ============================================================
# 6. 开发网关的生产禁用（真实执行）
# ============================================================


class TestDevelopmentGatewayIsNotProduction:
    def test_gateway_refuses_to_start_in_production(self) -> None:
        node = shutil.which("node")
        if node is None:
            pytest.skip("Node 不可用：跳过（未执行，非 PASS）")

        gateway = _REPO_ROOT / "frontend" / "scripts" / "dev-gateway.mjs"
        assert gateway.is_file()

        env = {
            "NODE_ENV": "production",
            "PATH": __import__("os").environ.get("PATH", ""),
        }
        result = subprocess.run(
            [node, str(gateway), "--port", "0", "--api-port", "0"],
            cwd=str(_REPO_ROOT / "frontend"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=env,
        )
        assert result.returncode != 0, "开发网关在生产环境竟可启动"
        combined = (result.stdout or "") + (result.stderr or "")
        assert "不得用于生产" in combined or "拒绝启动" in combined


# ============================================================
# 7. 不冒充已验证部署
# ============================================================


class TestTemplateIsNotVerifiedDeployment:
    def test_deploy_readme_keeps_status_blocked(self) -> None:
        text = _DEPLOY_README.read_text(encoding="utf-8")
        assert "BLOCKED" in text
        assert "NOT VERIFIED" in text
        # 不得声称已完成真实部署验证
        for claim in ("Production Deployment = READY", "HTTPS = VERIFIED"):
            assert claim not in text

    def test_nginx_syntax_check_is_explicitly_not_verified(self) -> None:
        """宿主无 nginx ⇒ 本文件不声称 ``nginx -t`` 通过。

        真实的 ``nginx -t`` 由门控装置执行：
        `tests/test_gateway_validation.py`（`RUN_GATEWAY_VALIDATION=1`，
        在 `nginx:1.27-alpine` 容器内、与网关同一 Docker 网络执行）。
        """
        if shutil.which("nginx") is None:
            pytest.skip(
                "宿主未安装 nginx：本文件不执行 `nginx -t`；"
                "真实语法验证见 RUN_GATEWAY_VALIDATION=1 的 "
                "tests/test_gateway_validation.py（此处未执行，不计入 PASS）"
            )
        result = subprocess.run(
            ["nginx", "-t", "-c", str(_NGINX_TEMPLATE)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        # 模板含占位符，预期失败；此处仅记录真实输出的存在性
        assert result.returncode != 0
