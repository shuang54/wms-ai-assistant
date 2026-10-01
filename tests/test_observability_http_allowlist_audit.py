"""Observability HTTP API **Allowlist 完整性审计**（Phase 3.12 Step 76）。

性质：**静态架构审计**（offline · AST only）。

```text
DB        = 0（不连接 PostgreSQL；不打开 RUN_DB_TESTS 门控）
Network   = 0（无 HTTP 客户端）
DeepSeek  = 0（不调用 LLM）
backend   = 只读（本文件与审计对象均不修改生产代码）
```

审计链路（§四～§十一）：

```text
backend/app/api/*.py  --AST-->  route (method, path, module)
        ↓ 过滤 path 含 observability / metrics / snapshot
discovered observability routes
        ↓ 双向比对
frozen allowlist（tests/test_tool_observability_architecture_audit.py :: allowed_paths）
        ↓
ALLOWLIST_MISSING / STALE_ALLOWLIST_ENTRY / DUPLICATE_OBSERVABILITY_ROUTE /
UNEXPECTED_OBSERVABILITY_ROUTE / write-method 违规
```

**本阶段只报告，不修复**（发现新 drift → 报告，不由本文件自动改写白名单）。
"""
from __future__ import annotations

import ast
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_API_DIR = "backend/app/api"
_MAIN = "backend/app/main.py"
_SELF = "tests/test_observability_http_allowlist_audit.py"

#: 冻结白名单的**唯一**声明位置（Step 75 修复后的状态）。
_ALLOWLIST_SOURCE = "tests/test_tool_observability_architecture_audit.py"
_ALLOWLIST_VARIABLE = "allowed_paths"

#: Observability 分类关键字（§五）。
_OBSERVABILITY_TOKENS: tuple[str, ...] = ("observability", "metrics", "snapshot")

#: 只读 API 原则（§六）：白名单内出现这些方法必须单独报告。
_WRITE_METHODS: frozenset[str] = frozenset({"post", "put", "patch", "delete"})

_SUPPORTED_METHODS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)

#: §三 声明的六路由基线（**不信任列表本身**：与真实发现结果双向比对）。
_EXPECTED_BASELINE: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/observability/tools"),
        ("GET", "/observability/tools/metrics"),
        ("GET", "/observability/tools/history"),
        ("GET", "/observability/tools/metrics/persistent"),
        ("GET", "/observability/assistant-trace/{assistant_request_id}"),
        ("GET", "/observability/assistant-timeline/{assistant_request_id}"),
    }
)

#: §十一 安全扫描禁止标识符（只扫可执行标识符 / 形参；**不扫 docstring**）。
_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "api_key",
    "password",
    "authorization",
    "database_url",
)


@dataclass(frozen=True)
class Route:
    """一条 HTTP route（AST 原始声明；path 为 decorator 字面量）。"""

    method: str
    path: str
    module: str

    @property
    def identity(self) -> tuple[str, str]:
        return (self.method, self.path)

    @property
    def full_path(self) -> str:
        """对外路径（router prefix 由 ``main.py`` 的 ``include_router`` 提供）。"""
        return f"/api{self.path}"


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _api_modules() -> tuple[str, ...]:
    return tuple(
        sorted(
            f"{_API_DIR}/{path.name}"
            for path in (_REPO_ROOT / _API_DIR).glob("*.py")
            if path.name != "__init__.py"
        )
    )


def _normalize(path: str) -> str:
    """轻量规范化（§十）：仅去尾部斜杠；**不做**前缀/正则匹配。"""
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path


def discover_routes() -> tuple[Route, ...]:
    """AST 扫描 ``backend/app/api/*.py`` 的 ``@<router>.<method>("path")``。"""
    found: list[Route] = []
    for relative in _api_modules():
        tree = ast.parse(_source(relative))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr not in _SUPPORTED_METHODS:
                    continue
                if not decorator.args:
                    continue
                literal = decorator.args[0]
                if not (
                    isinstance(literal, ast.Constant)
                    and isinstance(literal.value, str)
                ):
                    continue          # 动态 path：本阶段不纳入白名单比对
                found.append(
                    Route(
                        method=func.attr.upper(),
                        path=literal.value,
                        module=relative,
                    )
                )
    return tuple(found)


def observability_routes() -> tuple[Route, ...]:
    """过滤 path 含 observability / metrics / snapshot 的 route（§五）。"""
    return tuple(
        route
        for route in discover_routes()
        if any(token in route.path for token in _OBSERVABILITY_TOKENS)
    )


def frozen_allowlist() -> frozenset[str]:
    """从审计测试的 ``allowed_paths`` 集合字面量提取冻结白名单。"""
    tree = ast.parse(_source(_ALLOWLIST_SOURCE))
    literals: list[ast.Set] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [
            target.id for target in node.targets if isinstance(target, ast.Name)
        ]
        if _ALLOWLIST_VARIABLE in targets and isinstance(node.value, ast.Set):
            literals.append(node.value)

    assert len(literals) == 1, (
        f"{_ALLOWLIST_SOURCE} 中 {_ALLOWLIST_VARIABLE} 集合字面量应唯一"
        f"（当前: {len(literals)}）"
    )
    entries = {
        element.value
        for element in literals[0].elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    }
    assert len(entries) == len(literals[0].elts), "白名单含非字符串条目"
    return frozenset(entries)


def _include_router_prefixes() -> dict[str, str]:
    """``main.py`` 的 ``include_router(<name>.router, prefix="/api", ...)`` 证据。"""
    tree = ast.parse(_source(_MAIN))
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "include_router"
            and node.args
        ):
            continue
        target = node.args[0]
        if not (
            isinstance(target, ast.Attribute)
            and target.attr == "router"
            and isinstance(target.value, ast.Name)
        ):
            continue
        prefix = ""
        for keyword in node.keywords:
            if (
                keyword.arg == "prefix"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                prefix = keyword.value.value
        mapping[target.value.id] = prefix
    return mapping


def _identifiers_and_args(relative: str) -> set[str]:
    """AST 取可执行标识符 / 属性名 / 形参名（**不含** docstring 文本）。"""
    tree = ast.parse(_source(relative))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


# ============================================================
# 1 ~ 2：Route Discovery / Observability 过滤
# ============================================================

class TestRouteDiscovery:
    def test_1_ast_discovery_finds_api_routes(self) -> None:
        routes = discover_routes()

        assert len(routes) >= 12, [r.identity for r in routes]
        assert len({r.identity for r in routes}) == len(routes), "发现重复 route"
        for route in routes:
            assert route.path.startswith("/"), route
            assert route.module.startswith(f"{_API_DIR}/"), route
            assert route.method in {m.upper() for m in _SUPPORTED_METHODS}

    def test_1b_discovery_covers_every_api_module_with_routes(self) -> None:
        modules = {route.module for route in discover_routes()}

        for expected in (
            f"{_API_DIR}/tool_observability.py",
            f"{_API_DIR}/assistant_trace.py",
            f"{_API_DIR}/assistant_timeline.py",
        ):
            assert expected in modules, expected

    def test_2_observability_filter_matches_token_rule(self) -> None:
        routes = observability_routes()
        manual = tuple(
            route
            for route in discover_routes()
            if any(token in route.path for token in _OBSERVABILITY_TOKENS)
        )

        assert len(routes) == len(manual) == 6
        assert all(route.path.startswith("/observability/") for route in routes)
        # 过滤是"路径包含"语义：非 observability 前缀但命中 token 的路径也应被纳入
        # （当前仓库不存在此类 route；此处锁定语义，防止未来静默漏审计）
        for route in discover_routes():
            hit = any(token in route.path for token in _OBSERVABILITY_TOKENS)
            assert hit is (route in routes)


# ============================================================
# 3 ~ 5：Method / Path Identity、白名单完整性
# ============================================================

class TestAllowlistCompleteness:
    def test_3_method_path_identity_and_public_path(self) -> None:
        routes = observability_routes()

        # (method, path) 唯一身份；method 大写；prefix 由 main.py 证据提供
        for route in routes:
            assert route.method.isupper(), route
            assert route.full_path == f"/api{route.path}"
        prefixes = _include_router_prefixes()
        for module in {route.module for route in routes}:
            name = Path(module).stem
            assert name in prefixes, (module, sorted(prefixes))
            assert prefixes[name] == "/api", (module, prefixes[name])

    def test_4_allowlist_missing_is_empty(self) -> None:
        allowed = frozen_allowlist()
        missing = sorted(
            {
                route.path
                for route in observability_routes()
                if route.path not in allowed
            }
        )

        assert missing == [], f"ALLOWLIST_MISSING: {missing}"

    def test_5_stale_allowlist_entry_is_empty(self) -> None:
        allowed = frozen_allowlist()
        discovered = {_normalize(route.path) for route in observability_routes()}
        stale = sorted(
            entry for entry in allowed if _normalize(entry) not in discovered
        )

        assert stale == [], f"STALE_ALLOWLIST_ENTRY: {stale}"

    def test_5b_meta_guard_allowlist_parser_is_live(self) -> None:
        """元断言：解析器必须真的读到 Step 75 的 6 条白名单（防止解析失效→假绿）。"""
        allowed = frozen_allowlist()

        assert len(allowed) == 6, sorted(allowed)
        assert "/observability/assistant-timeline/{assistant_request_id}" in allowed


# ============================================================
# 6 ~ 7：重复 route / 动态 path 处理
# ============================================================

class TestRouteHygiene:
    def test_6_no_duplicate_observability_route(self) -> None:
        seen: dict[tuple[str, str], list[str]] = {}
        for route in observability_routes():
            seen.setdefault(
                (route.method, _normalize(route.path)), []
            ).append(route.module)

        duplicates = {
            identity: sorted(modules)
            for identity, modules in seen.items()
            if len(modules) > 1
        }
        assert duplicates == {}, f"DUPLICATE_OBSERVABILITY_ROUTE: {duplicates}"

    def test_6b_same_path_different_method_would_not_collide(self) -> None:
        """同 path 不同 method 必须视为不同 route（身份 = method + path）。"""
        synthetic = (
            Route("GET", "/observability/x", f"{_API_DIR}/a.py"),
            Route("POST", "/observability/x", f"{_API_DIR}/b.py"),
        )

        identities = {route.identity for route in synthetic}
        assert len(identities) == 2
        assert len({route.path for route in synthetic}) == 1

    def test_7_dynamic_paths_are_compared_exactly(self) -> None:
        allowed = frozen_allowlist()
        routes = observability_routes()

        # 参数化 path 逐字比较（禁止前缀 / 子串匹配造成误判）
        parameterized = {route.path for route in routes if "{" in route.path}
        assert parameterized == {
            "/observability/assistant-trace/{assistant_request_id}",
            "/observability/assistant-timeline/{assistant_request_id}",
        }
        for path in parameterized:
            assert path in allowed
            # 去掉参数化后缀的"前缀形式"**不得**被当作同一 route
            assert path.rsplit("/", 1)[0] not in allowed
        for path in allowed:
            assert path in {_normalize(route.path) for route in routes}

    def test_7b_trailing_slash_normalization_is_safe(self) -> None:
        assert _normalize("/observability/tools") == "/observability/tools"
        assert _normalize("/observability/tools/") == "/observability/tools"
        assert _normalize("/") == "/"
        # 规范化不得吞掉路径段
        assert _normalize("/observability/tools/metrics") != _normalize(
            "/observability/tools"
        )


# ============================================================
# 8 ~ 9：Method 审计 / 安全扫描
# ============================================================

class TestSecurityAudit:
    def test_8_observability_routes_are_read_only(self) -> None:
        routes = observability_routes()
        write_routes = sorted(
            (route.identity for route in routes if route.method.lower() in _WRITE_METHODS)
        )

        assert write_routes == [], f"unexpected write route: {write_routes}"
        assert {route.method for route in routes} == {"GET"}

    def test_8b_whole_api_surface_write_routes_are_known(self) -> None:
        """全 API 的写方法仅限既有业务入口（report-only：任何新增写方法都会暴露）。"""
        write_routes = sorted(
            {
                route.full_path
                for route in discover_routes()
                if route.method.lower() in _WRITE_METHODS
            }
        )

        assert set(write_routes) <= {
            "/api/chat",
            "/api/chat/with-tools",
            "/api/ai/chat",
            "/api/rag/answer",
        }, write_routes
        for route in discover_routes():
            if route.method.lower() in _WRITE_METHODS:
                assert not any(
                    token in route.path for token in _OBSERVABILITY_TOKENS
                ), route

    def test_9_observability_modules_expose_no_credentials_identifiers(self) -> None:
        report: dict[str, list[str]] = {}
        for module in sorted({route.module for route in observability_routes()}):
            names = _identifiers_and_args(module)
            hits = sorted(name for name in _FORBIDDEN_IDENTIFIERS if name in names)
            if hits:
                report[module] = hits

        assert report == {}, f"credential-like identifiers: {report}"

    def test_9b_security_scan_ignores_docstring_text(self) -> None:
        """扫描器只取标识符/形参；docstring 中的同名词不得被判为违规。"""
        probe = f"{_API_DIR}/_rag_error_mapping.py"
        source = _source(probe)

        assert source  # 该模块存在（保证探针有效）
        names = _identifiers_and_args(probe)
        assert "docstring" not in names
        # 扫描集合中不存在任何"整句话"形态的条目（证明不是全文匹配）
        assert not [name for name in names if " " in name or len(name) > 60]


# ============================================================
# 10 ~ 12：六路由基线 / backend 完整性 / 本文件离线
# ============================================================

class TestBaseline:
    def test_10_six_route_baseline_is_exact(self) -> None:
        discovered = frozenset(route.identity for route in observability_routes())

        assert discovered == _EXPECTED_BASELINE
        assert len(discovered) == 6

    def test_10b_baseline_classification_is_complete(self) -> None:
        """A/B/C 三类归属完整（D. Other = UNEXPECTED_OBSERVABILITY_ROUTE 为空）。"""
        classification: dict[str, list[str]] = {
            "A_tool_observability": [],
            "B_assistant_trace": [],
            "C_assistant_timeline": [],
            "D_unexpected": [],
        }
        for route in observability_routes():
            module = Path(route.module).stem
            if module == "tool_observability":
                classification["A_tool_observability"].append(route.path)
            elif module == "assistant_trace":
                classification["B_assistant_trace"].append(route.path)
            elif module == "assistant_timeline":
                classification["C_assistant_timeline"].append(route.path)
            else:
                classification["D_unexpected"].append(route.path)

        assert classification["D_unexpected"] == []
        assert len(classification["A_tool_observability"]) == 4
        assert classification["B_assistant_trace"] == [
            "/observability/assistant-trace/{assistant_request_id}"
        ]
        assert classification["C_assistant_timeline"] == [
            "/observability/assistant-timeline/{assistant_request_id}"
        ]

    def test_11_backend_working_tree_is_unmodified(self) -> None:
        if shutil.which("git") is None:
            pytest.skip("git 不可用")

        result = subprocess.run(
            ["git", "diff", "--name-only", "--", "backend"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "", (
            f"backend 存在未提交修改: {result.stdout.strip()}"
        )

    def test_12_audit_file_is_offline(self) -> None:
        module_level: set[str] = set()
        tree = ast.parse(_source(_SELF))
        for node in tree.body:
            if isinstance(node, ast.Import):
                module_level |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_level.add(node.module)

        for module in module_level:
            for prefix in (
                "backend.app.db",
                "sqlalchemy",
                "psycopg",
                "redis",
                "httpx",
                "requests",
                "aiohttp",
                "openai",
            ):
                assert not module.startswith(prefix), module


__all__ = [
    "Route",
    "discover_routes",
    "frozen_allowlist",
    "observability_routes",
    "TestAllowlistCompleteness",
    "TestBaseline",
    "TestRouteDiscovery",
    "TestRouteHygiene",
    "TestSecurityAudit",
]
