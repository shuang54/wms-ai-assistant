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
#: 仓库根（启动脚本位于 `scripts/`）。
_REPO_ROOT = _ROOT
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

    def test_only_allowed_buttons_declared(self) -> None:
        """页面只允许"查询 / 重新加载 / 清除筛选"三个按钮（**无**任何写操作按钮）。

        Step 3 新增"清除筛选"（纯前端）⇒ 允许集合由 2 项扩为 3 项；
        不变式（"不存在执行/裁决类按钮"）保持不变。
        """
        html = _read(_FRONTEND / "index.html")
        button_ids = re.findall(r'<button[^>]*id="([^"]+)"', html)
        assert sorted(button_ids) == [
            "node-filter-clear",
            "run-refresh",
            "run-submit",
        ], button_ids

    def test_no_credential_input_or_authorization_field(self) -> None:
        """页面不得提供任何密钥输入框（凭证不由用户填写）。"""
        html = _read(_FRONTEND / "index.html")
        for forbidden in ("api-key", "api_key", "apikey", "token", "secret"):
            assert forbidden not in html.lower(), forbidden

    def test_client_uses_same_origin_credentials(self) -> None:
        """客户端必须使用同源 Cookie 语义（不跨源、不构造自定义认证头）。"""
        source = _read(_FRONTEND / "js" / "api-client.js")
        assert "credentials: 'same-origin'" in source
        # 不得出现跨源凭证模式
        for forbidden in ("include'", "credentials: 'include'"):
            assert forbidden not in source


class TestApiContractAlignment:
    """前端引用的 API 路径必须与真实 OpenAPI schema 一致（防漂移）。"""

    #: 允许前端引用的**只读**端点模板（真实存在，取自 openapi）。
    #: Step 2 起前端使用**会话保护**的 UI 只读面（不是服务间 API Key 面）。
    EXPECTED_TEMPLATES = (
        "/api/ui/projects/{project_id}/workflow-runs/{run_id}",
        "/api/ui/projects/{project_id}/workflow-runs/{run_id}/nodes",
        "/api/ui/projects/{project_id}/workflow-runs/{run_id}/timeline",
        "/api/ui/projects/{project_id}/workflow-runs/{run_id}/graph",
        "/api/ui/projects/{project_id}/workflow-runs/{run_id}/approvals/{node_id}",
        "/api/ui/projects/{project_id}/workflow-approvals/{approval_id}",
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
            matches = [
                candidate
                for candidate in paths
                if _same_shape(candidate, to_template(raw))
            ]
            assert matches, f"客户端路径无对应真实路由: {raw}"

    def test_client_only_uses_session_protected_ui_surface(self) -> None:
        """前端**只**使用会话保护的 `/api/ui/projects/...` 面。

        明确**不**引用服务间 `X-API-Key` 面（`/api/workflow-runs/...`）——
        那个面要求服务端密钥，浏览器不得接触。
        """
        source = _read(_FRONTEND / "js" / "api-client.js")
        literal_paths = re.findall(r"`(/api/[^`]+)`", source)

        for path in literal_paths:
            assert path.startswith("/api/ui/projects/"), path
        # 服务间面（无 /ui/ 前缀）不得出现
        assert "/api/workflow-runs/" not in source
        assert "/api/workflow-approvals/" not in source

    def test_client_does_not_reference_write_endpoints(self) -> None:
        source = _read(_FRONTEND / "js" / "api-client.js")
        for forbidden in (
            "/api/workflow-definitions",
            "/api/agent/",
            "/api/chat",
            "/api/tools",
        ):
            assert f"POST {forbidden}" not in source
        assert "/api/ui/projects/${" in source

    def test_static_route_shapes_are_known(self) -> None:
        """客户端使用的 5 个路径 → 形状集合必须与 EXPECTED_TEMPLATES 相等。"""
        source = _read(_FRONTEND / "js" / "api-client.js")
        literal_paths = re.findall(r"`(/api/[^`]+)`", source)
        shapes = {
            re.sub(r"\$\{[^}]+\}", "{p}", path) for path in literal_paths
        }
        expected = {
            "/api/ui/projects/{p}/workflow-runs/{p}",
            "/api/ui/projects/{p}/workflow-runs/{p}/nodes",
            "/api/ui/projects/{p}/workflow-runs/{p}/timeline",
            "/api/ui/projects/{p}/workflow-runs/{p}/graph",
            "/api/ui/projects/{p}/workflow-runs/{p}/approvals/{p}",
            "/api/ui/projects/{p}/workflow-approvals/{p}",
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

    def test_no_runtime_dependencies_declared(self) -> None:
        """**运行时**零依赖：页面不引入任何第三方运行时库。

        Step 3B 起新增 **devDependency** `@playwright/test`（真实 Chromium
        E2E 用）。这是申报的例外：E2E 依赖**不得**进入运行时（`dependencies`
        必须为空），且 E2E 只允许使用 Playwright（不引入其它测试框架）。
        """
        manifest = json.loads(_read(_FRONTEND / "package.json"))
        # 运行时不引用任何第三方库（键缺失或空对象均视为零依赖）
        assert not manifest.get("dependencies"), manifest.get("dependencies")
        dev = manifest.get("devDependencies") or {}
        assert set(dev) == {"@playwright/test"}, dev

    def test_e2e_dev_dependency_is_not_bundled(self) -> None:
        """E2E 依赖不得被页面 / 静态服务引用（仅 `playwright.config.js` 可用）。"""
        for path in (
            _FRONTEND / "index.html",
            _FRONTEND / "styles.css",
            _FRONTEND / "scripts" / "serve-static.mjs",
        ):
            assert "@playwright" not in _read(path), path.name
        for js in (_FRONTEND / "js").glob("*.js"):
            assert "@playwright" not in _read(js), js.name
        for test_file in (_FRONTEND / "tests").glob("*.mjs"):
            assert "@playwright" not in _read(test_file), test_file.name

    def test_e2e_config_binds_loopback_and_chromium_only(self) -> None:
        """E2E 配置：只跑 Chromium、只绑回环、不访问外部网络。"""
        config = _read(_FRONTEND / "playwright.config.js")
        assert "browserName: 'chromium'" in config
        assert "127.0.0.1" in config
        # 不使用外部代理（避免流量外泄）
        assert "proxy:" not in config
        # 失败不重试掩盖
        assert "retries: 0" in config

    def test_manifest_declares_esm(self) -> None:
        manifest = json.loads(_read(_FRONTEND / "package.json"))
        assert manifest.get("type") == "module"
        # 既有 `test` 行为不变（Node 内置测试）；新增 `test:e2e` 入口
        scripts = manifest.get("scripts", {})
        assert scripts.get("test") == "node --test tests/"
        assert scripts.get("test:e2e") == "playwright test"


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

    def test_html_declares_access_model(self) -> None:
        """页面必须**如实**标注访问方式（会话 + 项目成员授权；无服务端密钥）。"""
        html = _read(_FRONTEND / "index.html")
        assert "数据访问方式" in html
        assert "会话" in html
        assert "项目成员授权" in html
        # 明确声明浏览器不存放服务端密钥
        assert "不存放任何服务端密钥" in html
        # 三种认证/授权结果都有对应提示
        assert "需要登录" in html
        assert "会话过期" in html
        assert "权限错误" in html

    def test_html_declares_project_input(self) -> None:
        """页面必须提供 Project ID 输入（作为请求目标，由服务端独立授权）。"""
        html = _read(_FRONTEND / "index.html")
        assert 'id="project-id-input"' in html
        assert "maxlength=\"128\"" in html

    def test_no_auto_loaded_placeholder_data(self) -> None:
        """页面初始不得包含任何虚构 Run 数据。"""
        html = _read(_FRONTEND / "index.html")
        assert "workflow_run_id" not in html
        assert "CURRENT_NODE" not in html


class TestWorkbenchInteractions:
    """Step 3：筛选 / 联动 / 复制的前端安全与一致性约束。"""

    def test_no_html_injection_primitives_in_any_module(self) -> None:
        """**任何**前端模块都不得使用 HTML 注入原语（结构上防 XSS）。"""
        for path in _SOURCE_FILES:
            code = _code_only(path)
            for primitive in (
                "innerHTML",
                "outerHTML",
                "insertAdjacentHTML",
                "document.write",
                "createContextualFragment",
            ):
                assert primitive not in code, f"{path.name} 使用了 {primitive}"

    def test_output_rendered_with_text_content(self) -> None:
        """输出必须经 `textContent` 写入 `<pre>`（不被当 HTML 执行）。"""
        source = _read(_FRONTEND / "js" / "render.js")
        assert "pre.textContent = summary.full" in source
        assert "doc.createElement('pre')" in source

    def test_copy_uses_clipboard_api_not_legacy_exec_command(self) -> None:
        source = _read(_FRONTEND / "js" / "state.js")
        assert "navigator.clipboard" in source
        assert "writeText" in source
        assert "execCommand" not in source

    def test_filter_values_match_backend_node_statuses(self) -> None:
        """筛选选项必须基于后端**真实**枚举（外加 unknown），不得臆造状态。"""
        from backend.app.workflow.states import NODE_STATUSES

        state_source = _read(_FRONTEND / "js" / "state.js")
        # 前端已知状态集合 == 后端 NODE_STATUSES
        block = re.search(
            r"KNOWN_NODE_STATUSES = Object\.freeze\(\s*Object\.keys\(\s*NODE_STATUS_LABELS\s*\)\s*\)",
            state_source,
        )
        assert block is not None, "未找到 KNOWN_NODE_STATUSES 定义"

        labels_block = re.search(
            r"NODE_STATUS_LABELS = Object\.freeze\(\{([^}]*)\}\)", state_source
        )
        assert labels_block is not None
        frontend_statuses = set(re.findall(r"^\s*([A-Z_]+):", labels_block.group(1), re.M))
        assert frontend_statuses == set(NODE_STATUSES), (
            frontend_statuses ^ set(NODE_STATUSES)
        )

    def test_filter_select_declares_expected_options(self) -> None:
        html = _read(_FRONTEND / "index.html")
        select = re.search(
            r'<select id="node-filter"[^>]*>(.*?)</select>', html, re.DOTALL
        )
        assert select is not None, "缺少节点状态筛选控件"
        values = re.findall(r'<option value="([^"]+)"', select.group(1))
        assert values == [
            "all",
            "running",
            "succeeded",
            "failed",
            "waiting_approval",
            "unknown",
        ], values

    def test_filter_is_client_side_only(self) -> None:
        """筛选实现不得调用任何网络 / 存储 API（纯前端过滤）。"""
        source = _code_only(_FRONTEND / "js" / "state.js")
        filter_section = source[source.find("export function applyNodeFilter") :]
        filter_section = filter_section[: filter_section.find("\n}\n")]
        for forbidden in (
            "fetch(",
            "readJson",
            "XMLHttpRequest",
            "localStorage",
            "sessionStorage",
        ):
            assert forbidden not in filter_section, forbidden

    def test_controller_exposes_no_write_action(self) -> None:
        source = _read(_FRONTEND / "js" / "controller.js")
        for forbidden in (
            "start(",
            "advance(",
            "request_cancel(",
            "submit_approval_decision(",
            "decide(",
            "approve(",
            "reject(",
        ):
            assert forbidden not in source, forbidden

    def test_reveal_helper_does_not_inject_html(self) -> None:
        source = _code_only(_FRONTEND / "js" / "app.js")
        assert "scrollIntoView" in source
        assert "innerHTML" not in source


class TestDevSameOriginGateway:
    """Step 6：本地同源网关（dev-only）的安全边界。

    该网关**只**解决同源问题；不得成为认证旁路、不得注入凭据、不得开放 CORS、
    不得转发写接口或服务间面、不得用于生产。
    """

    @staticmethod
    def _source() -> str:
        return _read(_FRONTEND / "scripts" / "dev-gateway.mjs")

    def test_gateway_exists_and_is_registered(self) -> None:
        manifest = json.loads(_read(_FRONTEND / "package.json"))
        assert (
            manifest["scripts"]["dev:same-origin"]
            == "node scripts/dev-gateway.mjs"
        )

    def test_binds_loopback_and_rejects_host_override(self) -> None:
        source = self._source()
        assert "127.0.0.1" in source
        # --host 必须被拒绝（不可能绑到外部网卡）
        assert "'--host'" in source
        assert "只允许绑定回环" in source
        # upstream 也必须是回环
        assert "LOOPBACK_HOSTS" in source
        assert "必须是回环地址" in source

    def test_does_not_add_cors_headers(self) -> None:
        code = _code_only(_FRONTEND / "scripts" / "dev-gateway.mjs")
        assert "Access-Control-Allow" not in code
        assert "access-control-allow" not in code.lower()

    def test_does_not_inject_credentials(self) -> None:
        """不得出现凭据头名的赋值（注释中的说明性提及允许）。"""
        code = _code_only(_FRONTEND / "scripts" / "dev-gateway.mjs")
        assert not re.search(r"x-api-key\s*:", code, re.IGNORECASE)
        assert not re.search(r"authorization\s*:", code, re.IGNORECASE)

    def test_forwards_cookie_without_stripping(self) -> None:
        """Cookie / CSRF 必须原样透传（认证由后端执行，网关不做判定）。"""
        source = self._source()
        assert "filterHeaders" in source
        assert "HOP_BY_HOP" in source
        # 逐跳头集合**不得**包含 cookie / x-csrf-token
        hop_block = source[source.index("const HOP_BY_HOP") :][:400]
        assert "cookie" not in hop_block.lower()
        assert "csrf" not in hop_block.lower()

    def test_api_allowlist_is_minimal(self) -> None:
        source = self._source()
        assert "'/api/ui/'" in source
        assert "'/api/auth/'" in source
        # 不得转发服务间 / 写 / Agent / Tool 前缀
        for forbidden in (
            "'/api/workflow-runs/",
            "'/api/workflow-approvals/",
            "'/api/agent/",
            "'/api/tools",
            "'/api/chat",
        ):
            assert forbidden not in source, forbidden

    def test_ui_prefix_only_allows_read_methods(self) -> None:
        source = self._source()
        assert "methods: new Set(['GET', 'HEAD'])" in source

    def test_static_allowlist_excludes_tests_and_deps(self) -> None:
        source = self._source()
        assert "'/index.html'" in source
        assert "'/js/'" in source
        for forbidden in ("'/tests/", "'/e2e/", "'/node_modules/", "'/scripts/"):
            assert forbidden not in source, forbidden

    def test_refuses_to_start_in_production(self) -> None:
        source = self._source()
        assert "NODE_ENV" in source
        assert "不得用于生产" in source
        assert "process.exit(1)" in source

    def test_startup_banner_declares_dev_only_and_https_requirement(self) -> None:
        source = self._source()
        assert "本地开发专用" in source
        assert "HTTPS" in source


class TestLocalLaunchScript:
    """Step 7：本地一键启动脚本（`scripts/start-workflow-ui.ps1`）的安全边界。

    该脚本**只**做启动编排：不设置数据库、不改认证配置、不创建数据、
    不绕过网关的生产门。
    """

    @staticmethod
    def _path() -> Path:
        return _REPO_ROOT / "scripts" / "start-workflow-ui.ps1"

    @classmethod
    def _source(cls) -> str:
        path = cls._path()
        assert path.is_file(), f"缺少启动脚本: {path}"
        return path.read_text(encoding="utf-8-sig")

    @classmethod
    def _code_only(cls) -> str:
        """剥离块注释与**单引号字面量**（提示文案允许"提及"禁用命令）。

        PowerShell 的提示文案普遍写成 `Write-Hint '...'`；只有字面量之外
        出现的赋值 / 调用才是**实际执行**的行为。
        """
        code = re.sub(r"<#[\s\S]*?#>", " ", cls._source())
        return re.sub(r"'[^'\n]*'", "''", code)

    def test_script_exists_with_utf8_bom(self) -> None:
        """PowerShell 5.1 需 UTF-8 BOM 才能正确解析中文（否则乱码 / 语法错误）。"""
        raw = self._path().read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), "缺少 UTF-8 BOM（PS 5.1 兼容性）"

    def test_binds_loopback_only(self) -> None:
        source = self._source()
        assert "'127.0.0.1'" in source
        # 后端必须显式绑回环，不得使用 0.0.0.0
        assert "0.0.0.0" not in source

    def test_refuses_to_start_in_production(self) -> None:
        source = self._source()
        assert "NODE_ENV" in source
        assert "拒绝启动" in source
        # 不得为了让脚本跑通而清空 / 改写 NODE_ENV
        assert not re.search(r"\$env:NODE_ENV\s*=\s*['\"]", source)

    def test_never_sets_auth_cookie_secure(self) -> None:
        """**不得**自动设置 AUTH_COOKIE_SECURE（只允许提示用户显式设置）。"""
        code = self._code_only()
        assert not re.search(r"\$env:AUTH_COOKIE_SECURE\s*=", code), (
            "脚本不得自动设置 AUTH_COOKIE_SECURE"
        )
        # 但必须提示用户该设置（本地 http 需要）
        source = self._source()
        assert "AUTH_COOKIE_SECURE" in source
        assert "不会替你改" in source

    def test_never_prints_raw_database_url(self) -> None:
        """不得把原始连接串（含凭据）打印到控制台。"""
        source = self._source()
        # 任何 Write-* 调用都不得插值 $url
        assert not re.search(r"Write-(?:Host|Step|Ok|Warn|Err|Hint)[^\n]*\$url", source)
        # 解析函数只返回 host / port / database（不含 user / password）
        assert "Username" not in source
        assert "Password" not in source.replace("$Password", "")  # 无口令变量
        assert "[pscustomobject]@{" in source

    def test_never_creates_users_grants_or_runs(self) -> None:
        """只允许启动两个子进程（python uvicorn / node dev-gateway）。"""
        source = self._source()
        starts = re.findall(r"Start-Process -FilePath '([^']+)'", source)
        assert starts == ["python", "node"], starts
        # 不得**执行**创建用户 / 授权 / 启动 Run 的 CLI 或服务
        code = self._code_only()
        for forbidden in (
            "backend.app.cli.create_user",
            "backend.app.cli.grant_role",
            "start_workflow",
            "WorkflowCommandService",
        ):
            assert forbidden not in code, forbidden

    def test_never_runs_migrations_or_init(self) -> None:
        source = self._source()
        assert not re.search(
            r"python[^\n]*backend\.app\.db\.init_db", source
        ), "不得执行 init_db（初始化写操作）"

    def test_checks_database_reachability_without_sql(self) -> None:
        """数据库检查只做 TCP 探测（不连接凭据、不执行 SQL）。"""
        source = self._source()
        assert "Test-TcpPort" in source
        assert "TcpClient" in source
        assert "SELECT 1" not in source
        # 不可达时给出明确提示，且**不**自动切换
        assert "数据库不可达" in source
        assert "不会**自动切换数据库" in source or "自动切换数据库" in source

    def test_checks_port_occupancy(self) -> None:
        source = self._source()
        assert "Get-NetTCPConnection" in source
        assert "已被占用" in source

    def test_waits_for_health_endpoint(self) -> None:
        source = self._source()
        assert "/api/health" in source
        assert "HealthTimeoutSeconds" in source

    def test_stop_is_scoped_to_own_children(self) -> None:
        """安全退出只结束本次启动的子进程（PID + 命令行双重校验）。"""
        source = self._source()
        assert "Test-IsOurProcess" in source
        assert "Get-CimInstance Win32_Process" in source
        assert "避免误杀" in source
        assert "Stop-Process" in source

    def test_launch_state_file_is_gitignored(self) -> None:
        """.dev-launch.json 属运行时状态，不得入库。"""
        ignored = _read(_FRONTEND / ".gitignore")
        assert ".dev-launch.json" in ignored

    def test_readme_documents_one_command_start(self) -> None:
        readme = _read(_FRONTEND / "README.md")
        assert "start-workflow-ui.ps1" in readme
        assert "一键启动" in readme


class TestRealApiE2EGate:
    """Step 5：真实 API 浏览器规格必须**默认跳过**且不硬编码凭证。

    诚实性约束：Mock E2E 与真实 DB 集成分属两层，**不得**混为一谈；
    真实规格在环境未就绪时必须显式跳过（而非伪装通过）。
    """

    @staticmethod
    def _spec() -> str:
        return _read(_FRONTEND / "e2e" / "real-api.spec.js")

    def test_spec_is_skipped_by_default(self) -> None:
        source = self._spec()
        assert "test.skip(!READY" in source, "真实规格必须显式跳过（环境门控）"
        assert "WMS_REAL_API_BASE" in source
        assert "未执行" in source, "跳过原因必须明确写明'未执行'"

    def test_spec_declares_all_required_env_vars(self) -> None:
        source = self._spec()
        for name in (
            "WMS_REAL_API_BASE",
            "WMS_REAL_USERNAME",
            "WMS_REAL_PASSWORD",
            "WMS_REAL_PROJECT",
            "WMS_REAL_RUN_ID",
            "WMS_REAL_OTHER_PROJECT",
            "WMS_REAL_OTHER_RUN_ID",
        ):
            assert name in source, name

    def test_spec_has_no_hardcoded_credentials(self) -> None:
        source = self._spec()
        for forbidden in ("WORKFLOW_READ_API_KEY", "DATABASE_URL", "postgresql://"):
            assert forbidden not in source, forbidden
        # 不得出现形如 password: '...' 的字面量
        assert not re.search(r"password\s*:\s*['\"][^'\"]+['\"]", source, re.I)
        assert not re.search(r"username\s*:\s*['\"][^'\"]+['\"]", source, re.I)

    def test_spec_does_not_mock_api(self) -> None:
        """真实规格**不得**使用 page.route（否则就是 Mock，不是真实集成）。"""
        source = _code_only(_FRONTEND / "e2e" / "real-api.spec.js")
        assert "page.route(" not in source
        assert "route.fulfill(" not in source

    def test_mock_spec_still_exists(self) -> None:
        """Mock E2E 必须保留（两层并存，不互相替代）。"""
        assert (_FRONTEND / "e2e" / "workbench.spec.js").is_file()


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
        # Step 3：页面标题为运维工作台
        assert "Workflow 运维工作台" in body
        # 访问方式（会话 + 项目成员授权；无服务端密钥）必须如实出现在页面上
        assert "数据访问方式" in body
        # 筛选控件必须真实存在（纯前端过滤）
        assert 'id="node-filter"' in body

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
