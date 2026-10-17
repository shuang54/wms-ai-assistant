"""Phase 8 Step 2 —— **本机隔离**的 Nginx 网关验证装置（非生产部署）。

用途
----
把 `deploy/nginx/wms-workflow-ui.conf.template` 渲染成一份**临时配置**，
在本机 Docker 中启动真实的 `nginx:1.27-alpine` 与一个**桩上游**，
用真实 HTTP/HTTPS 请求验证：

* `nginx -t` 语法
* HTTP → HTTPS 跳转
* 路由 / 方法矩阵（含 `limit_except` 的真实语义）
* `proxy_pass` 的 URI 行为（桩捕获完整路径）
* 代理头覆盖（`X-Forwarded-*` 不被客户端伪造）
* 未授权路由 fail closed，且**未到达上游**

隔离规则（硬约束）
------------------
* 仅绑定 `127.0.0.1`；不绑定 `0.0.0.0`，不暴露公网。
* 全部使用 `%TEMP%` 下的临时目录：配置、自签名证书、静态文件、桩脚本。
* 不修改 `deploy/` 正式模板；不写入任何真实证书 / 私钥。
* 不连接 PostgreSQL / LLM / 业务 API：桩上游只回固定 JSON。
* 不新增 compose 服务、不新增表或 migration。

**本装置不能证明生产 HTTPS 或生产网络隔离**（见 `docs/security.md` §7）。

桩上游的记录范围
----------------
只记录 **method / path / 白名单 header**；**不记录** Cookie、Authorization、
请求体或任何凭据。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "deploy" / "nginx" / "wms-workflow-ui.conf.template"
PROXY_SNIPPET = REPO_ROOT / "deploy" / "nginx" / "proxy-headers.conf"
STATIC_SRC = REPO_ROOT / "frontend"

NGINX_IMAGE = "nginx:1.27-alpine"

#: 临时配置里替换后的主机名（仅本地；不得用于生产）。
TEST_HOST = "gateway.validation.localhost"

#: 桩上游在容器网络内的别名与端口。
STUB_ALIAS = "stub"
STUB_PORT = 9100

_STUB_SOURCE = '''\
"""最小桩上游：记录 method / path / 白名单 header，返回固定 JSON。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: 只记录这些（**不含** Cookie / Authorization / 请求体）。
ALLOWED = {
    "host",
    "x-real-ip",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-host",
    "x-forwarded-port",
    "content-type",
    "content-length",
    "user-agent",
}

RECORDS = []
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _handle(self):
        if self.path == "/__records":
            with LOCK:
                self._json(200, list(RECORDS))
            return
        if self.path == "/__reset":
            with LOCK:
                RECORDS.clear()
            self._json(204, {})
            return

        headers = {
            key.lower(): value
            for key, value in self.headers.items()
            if key.lower() in ALLOWED
        }
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)          # 丢弃请求体（不记录）
        with LOCK:
            RECORDS.append(
                {"method": self.command, "path": self.path, "headers": headers}
            )
        self._json(200, {"ok": True})

    do_GET = _handle
    do_HEAD = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle

    def log_message(self, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", %(port)d), Handler).serve_forever()
'''


class HarnessUnavailable(RuntimeError):
    """环境不支持本地网关验证（Docker / 镜像 / 端口不可用）。"""


def _run(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return _run(["docker", "info"], timeout=60).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def image_present(tag: str) -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = _run(["docker", "image", "inspect", tag], timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_tcp(host: str, port: int, *, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.25)
    return False


# ============================================================
# HTTP 客户端（本机自签名证书 ⇒ 关闭校验，仅用于 loopback 测试）
# ============================================================


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def _ssl_context() -> Any:
    import ssl

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """**禁止**跟随跳转：本装置必须观察到 301/302 本身。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 15.0,
) -> Response:
    req = urllib.request.Request(url, data=body, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)

    handlers: list[Any] = [_NoRedirect]
    if url.startswith("https://"):
        handlers.append(urllib.request.HTTPSHandler(context=_ssl_context()))
    opener = urllib.request.build_opener(*handlers)

    try:
        with opener.open(req, timeout=timeout) as response:
            return Response(
                status=response.status,
                headers={k.lower(): v for k, v in response.headers.items()},
                body=response.read(),
            )
    except urllib.error.HTTPError as error:
        return Response(
            status=error.code,
            headers={k.lower(): v for k, v in error.headers.items()},
            body=error.read(),
        )


# ============================================================
# 环境编排
# ============================================================


@dataclass
class GatewayEnv:
    """一次性的验证环境（容器 + 临时目录）。"""

    workdir: Path
    static_dir: Path
    tls_dir: Path
    http_port: int
    https_port: int
    stub_port: int
    network: str
    nginx_name: str
    stub_name: str
    config_text: str = ""
    _started: list[str] = field(default_factory=list)

    # ---- URL 便捷属性 ----
    @property
    def http_base(self) -> str:
        return f"http://127.0.0.1:{self.http_port}"

    @property
    def https_base(self) -> str:
        return f"https://127.0.0.1:{self.https_port}"

    @property
    def stub_base(self) -> str:
        return f"http://127.0.0.1:{self.stub_port}"

    # ---- 桩控制 ----
    def records(self) -> list[dict[str, Any]]:
        return request(f"{self.stub_base}/__records").json()

    def reset_records(self) -> None:
        request(f"{self.stub_base}/__reset")

    # ---- 容器操作 ----
    def _mounts(self) -> list[str]:
        def mount(path: Path, target: str) -> list[str]:
            return ["-v", f"{path.as_posix()}:{target}:ro"]

        return (
            mount(self.workdir / "default.conf", "/etc/nginx/conf.d/default.conf")
            + mount(PROXY_SNIPPET, "/etc/nginx/proxy-headers.conf")
            + mount(self.tls_dir, "/etc/nginx/tls")
            + mount(self.static_dir, "/srv/static")
        )

    def nginx_config_test(self) -> subprocess.CompletedProcess:
        """在**运行网络**中执行真实的 ``nginx -t``。

        必须挂到与网关相同的网络上：配置里的 ``upstream`` 使用容器别名，
        离线容器无法解析该主机名（会误报 ``host not found in upstream``）。
        """
        return _run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                self.network,
                *self._mounts(),
                NGINX_IMAGE,
                "nginx",
                "-t",
            ]
        )

    def start(self) -> None:
        # 私有 bridge 网络（host 上仅发布 127.0.0.1 端口）。
        # 说明：`--internal` 会阻止 `-p` 端口发布，故不使用；两个容器都不需要外网。
        network = _run(["docker", "network", "create", self.network])
        if network.returncode != 0:
            raise HarnessUnavailable(f"network create 失败: {network.stderr}")

        stub = _run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                self.stub_name,
                "--network",
                self.network,
                "--network-alias",
                STUB_ALIAS,
                "-p",
                f"127.0.0.1:{self.stub_port}:{STUB_PORT}",
                "-v",
                f"{(self.workdir / 'stub_upstream.py').as_posix()}:"
                "/app/stub_upstream.py:ro",
                "python:3.12-alpine",
                "python",
                "/app/stub_upstream.py",
            ]
        )
        if stub.returncode != 0:
            raise HarnessUnavailable(f"stub 创建失败: {stub.stderr}")
        self._started.append(self.stub_name)
        self._ensure_running(self.stub_name)

        if not wait_tcp("127.0.0.1", self.stub_port, timeout=45):
            raise HarnessUnavailable(
                f"stub 未在 45s 内就绪；日志: {self._logs(self.stub_name)[-800:]}"
            )

        nginx = _run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                self.nginx_name,
                "--network",
                self.network,
                "-p",
                f"127.0.0.1:{self.http_port}:80",
                "-p",
                f"127.0.0.1:{self.https_port}:443",
                *self._mounts(),
                NGINX_IMAGE,
                "nginx",
                "-g",
                "daemon off;",
            ]
        )
        if nginx.returncode != 0:
            raise HarnessUnavailable(f"nginx 创建失败: {nginx.stderr}")
        self._started.append(self.nginx_name)
        self._ensure_running(self.nginx_name)

        if not wait_tcp("127.0.0.1", self.https_port, timeout=60):
            raise HarnessUnavailable(
                f"nginx 未在 60s 内就绪；日志: {self._logs(self.nginx_name)[-800:]}"
            )

    @staticmethod
    def _logs(name: str) -> str:
        return _run(["docker", "logs", name]).stdout or ""

    def _ensure_running(self, name: str) -> None:
        """显式启动容器。

        某些 Docker Desktop 环境里 ``docker run -d`` 只创建不启动（状态停在
        ``created``，端口也未发布）；此处显式 ``docker start`` 作为幂等兜底。
        """
        inspect = _run(
            ["docker", "inspect", name, "--format", "{{.State.Status}}"]
        )
        status = (inspect.stdout or "").strip()
        if status == "running":
            return
        started = _run(["docker", "start", name])
        if started.returncode != 0:
            raise HarnessUnavailable(
                f"容器 {name} 启动失败: {started.stderr or started.stdout}"
            )


    def nginx_logs(self) -> str:
        if self.nginx_name not in self._started:
            return ""
        return _run(["docker", "logs", self.nginx_name]).stdout or ""

    def stop(self) -> None:
        for name in reversed(self._started):
            _run(["docker", "rm", "-f", name])
        self._started.clear()
        _run(["docker", "network", "rm", self.network])

    # ---- 容器内绑定的端口是否只在本机 ----
    def published_bindings(self) -> str:
        result = _run(["docker", "port", self.nginx_name])
        return result.stdout


def render_config(*, stub_alias: str = STUB_ALIAS) -> str:
    """把正式模板渲染为临时配置。

    只替换**占位符**与上游地址（后者指向桩容器），其余内容**逐字节保留**，
    因此被测的路径 / 方法 / 头规则与正式模板完全一致。
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "<PRODUCTION_HOST>": TEST_HOST,
        "<TLS_CERT_PATH>": "/etc/nginx/tls/test.crt",
        "<TLS_KEY_PATH>": "/etc/nginx/tls/test.key",
        "<STATIC_ROOT>": "/srv/static",
        # 唯一的结构性替换：上游从 loopback 换成桩容器别名（仅本地验证）。
        "server 127.0.0.1:8000;": f"server {stub_alias}:{STUB_PORT};",
    }
    for source, target in replacements.items():
        if source not in text:
            raise HarnessUnavailable(f"模板缺少占位符: {source}")
        text = text.replace(source, target)
    return text


def _copy_static(target: Path) -> None:
    """按模板白名单复制真实前端资源（不含 tests / e2e / scripts）。"""
    target.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "styles.css"):
        shutil.copy2(STATIC_SRC / name, target / name)
    (target / "js").mkdir(exist_ok=True)
    for source in (STATIC_SRC / "js").glob("*.js"):
        shutil.copy2(source, target / "js" / source.name)


def _generate_cert(tls_dir: Path) -> None:
    """用固定版本 alpine + openssl 生成 1 天期自签名**测试**证书。

    私钥只落在临时目录，不进入仓库、不写入 `deploy/`。
    """
    script = (
        "apk add --no-cache openssl >/dev/null 2>&1 && "
        "openssl req -x509 -nodes -newkey rsa:2048 -days 1 "
        f"-subj '/CN={TEST_HOST}' "
        "-keyout /out/test.key -out /out/test.crt >/dev/null 2>&1 && "
        "echo CERT_OK"
    )
    result = _run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{tls_dir.as_posix()}:/out",
            "alpine:3.20",
            "sh",
            "-c",
            script,
        ],
        timeout=180,
    )
    if result.returncode != 0 or "CERT_OK" not in (result.stdout or ""):
        raise HarnessUnavailable(
            f"测试证书生成失败（可用镜像 alpine:3.20）: {result.stderr[-400:]}"
        )


def create_env(*, reuse_cert_from: Path | None = None) -> GatewayEnv:
    """创建一次性环境（不启动容器）。"""
    suffix = uuid.uuid4().hex[:8]
    workdir = Path(
        os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp"
    ) / f"wms-gw-{suffix}"
    workdir.mkdir(parents=True, exist_ok=True)

    tls_dir = workdir / "tls"
    tls_dir.mkdir()
    static_dir = workdir / "static"

    env = GatewayEnv(
        workdir=workdir,
        static_dir=static_dir,
        tls_dir=tls_dir,
        http_port=free_loopback_port(),
        https_port=free_loopback_port(),
        stub_port=free_loopback_port(),
        network=f"wms-gw-net-{suffix}",
        nginx_name=f"wms-gw-nginx-{suffix}",
        stub_name=f"wms-gw-stub-{suffix}",
    )

    _copy_static(static_dir)
    (workdir / "stub_upstream.py").write_text(
        _STUB_SOURCE % {"port": STUB_PORT}, encoding="utf-8"
    )
    env.config_text = render_config()
    (workdir / "default.conf").write_text(env.config_text, encoding="utf-8")

    if reuse_cert_from is not None and (reuse_cert_from / "test.crt").is_file():
        for name in ("test.crt", "test.key"):
            shutil.copy2(reuse_cert_from / name, tls_dir / name)
    else:
        _generate_cert(tls_dir)
    return env


def cleanup(env: GatewayEnv) -> None:
    env.stop()
    shutil.rmtree(env.workdir, ignore_errors=True)
