"""Observability HTTP API **Allowlist Regression Freeze**（Phase 3.12 Step 77）。

性质：**静态回归契约**（offline · AST only）。

```text
DB = 0 · Network = 0 · DeepSeek = 0 · backend = 只读
```

三层一致性（§七）：

```text
Actual Routes（真实 decorator 发现，Step 76 扫描器）
        ==
Frozen Contract（本文件的 EXPECTED_OBSERVABILITY_ROUTES —— **唯一**测试事实来源）
        ==
C25 Allowlist（tests/test_tool_observability_architecture_audit.py :: allowed_paths）
```

边界：

```text
* **不**新建第二套 Route Discovery Engine —— 复用
  tests/test_observability_http_allowlist_audit.py 的 AST 发现与 allowlist 解析；
* 比较对象是 **router decorator 原始 path**（`/api` 来自 main.py 的 include_router prefix，
  不在本契约内）；
* 冻结后：新增 / 删除 / 改 method / 改 path 均需未来单独的 Phase / Step 显式授权。
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.test_observability_http_allowlist_audit import (  # noqa: PLC2701
    discover_routes,
    frozen_allowlist,
    observability_routes,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_observability_http_allowlist_regression.py"

#: **冻结契约（Frozen Contract）—— 唯一测试事实来源。**
#:
#: 6 条只读 Observability HTTP 路由（decorator 原始 path，逐条列出，**不使用通配符**）。
#: 任何新增 / 删除 / method 变更 / path 变更都必须先修改本常量（= 显式授权变更契约）。
EXPECTED_OBSERVABILITY_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/observability/tools"),
        ("GET", "/observability/tools/metrics"),
        ("GET", "/observability/tools/history"),
        ("GET", "/observability/tools/metrics/persistent"),
        ("GET", "/observability/assistant-trace/{assistant_request_id}"),
        ("GET", "/observability/assistant-timeline/{assistant_request_id}"),
    }
)

#: 冻结路由数（Contract Count）。
EXPECTED_OBSERVABILITY_ROUTE_COUNT: int = 6

_TRACE_PATH = "/observability/assistant-trace/{assistant_request_id}"
_TIMELINE_PATH = "/observability/assistant-timeline/{assistant_request_id}"

#: 只读原则：冻结集合内不允许出现写方法。
_WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _actual_routes() -> frozenset[tuple[str, str]]:
    """真实路由集合（复用 Step 76 的 AST 发现；不重建扫描器）。"""
    return frozenset(route.identity for route in observability_routes())


def _frozen_paths() -> frozenset[str]:
    return frozenset(path for _, path in EXPECTED_OBSERVABILITY_ROUTES)


def _diff(actual: frozenset[str], expected: frozenset[str]) -> dict[str, list[str]]:
    return {
        "missing": sorted(expected - actual),    # 期望存在但未发现
        "unexpected": sorted(actual - expected),  # 发现但不在契约内
    }


# ============================================================
# 1 ~ 3：Count / Exact Equality / Method
# ============================================================

class TestFrozenRouteContract:
    def test_exact_observability_route_count(self) -> None:
        actual = _actual_routes()

        assert len(EXPECTED_OBSERVABILITY_ROUTES) == (
            EXPECTED_OBSERVABILITY_ROUTE_COUNT
        )
        assert len(actual) == EXPECTED_OBSERVABILITY_ROUTE_COUNT, sorted(actual)

    def test_exact_observability_route_set(self) -> None:
        """必须**精确集合相等**（不是 subset，也不是 expected <= actual）。"""
        actual = _actual_routes()

        assert actual == EXPECTED_OBSERVABILITY_ROUTES, _diff(
            actual, EXPECTED_OBSERVABILITY_ROUTES
        )
        assert not (actual < EXPECTED_OBSERVABILITY_ROUTES)
        assert not (EXPECTED_OBSERVABILITY_ROUTES < actual)

    def test_all_observability_routes_are_get(self) -> None:
        methods = {method for method, _ in _actual_routes()}

        assert methods == {"GET"}, sorted(methods)
        for method, path in EXPECTED_OBSERVABILITY_ROUTES:
            assert method == "GET", (method, path)
            assert method not in _WRITE_METHODS


# ============================================================
# 4 ~ 6：Trace / Timeline 冻结 · Unexpected
# ============================================================

class TestFrozenEntries:
    def test_trace_route_is_frozen(self) -> None:
        actual = _actual_routes()

        assert ("GET", _TRACE_PATH) in EXPECTED_OBSERVABILITY_ROUTES
        assert ("GET", _TRACE_PATH) in actual
        assert _TRACE_PATH in frozen_allowlist()

    def test_timeline_route_is_frozen(self) -> None:
        actual = _actual_routes()

        assert ("GET", _TIMELINE_PATH) in EXPECTED_OBSERVABILITY_ROUTES
        assert ("GET", _TIMELINE_PATH) in actual
        assert _TIMELINE_PATH in frozen_allowlist()

    def test_no_unexpected_observability_route(self) -> None:
        actual_paths = frozenset(path for _, path in _actual_routes())
        frozen_paths = _frozen_paths()

        # 逐条列出（不使用 /observability/* 通配符断言）
        assert frozen_paths - actual_paths == frozenset(), sorted(
            frozen_paths - actual_paths
        )
        assert actual_paths - frozen_paths == frozenset(), sorted(
            actual_paths - frozen_paths
        )
        # 契约内不得出现通配符
        assert all("*" not in path for path in frozen_paths)
        assert all(path.startswith("/observability/") for path in frozen_paths)

    def test_route_identity_is_method_plus_path(self) -> None:
        """身份 = (METHOD, PATH)：同 path 不同 method 不视为同一 route。"""
        assert len({identity for identity in EXPECTED_OBSERVABILITY_ROUTES}) == (
            EXPECTED_OBSERVABILITY_ROUTE_COUNT
        )
        synthetic = {("GET", "/observability/x"), ("POST", "/observability/x")}

        assert len(synthetic) == 2
        # 冻结契约内同 path 只出现一次（method 维度未重复）
        paths = [path for _, path in EXPECTED_OBSERVABILITY_ROUTES]
        assert len(set(paths)) == len(paths)


# ============================================================
# 7 ~ 9：三层一致 / 单一事实来源 / 离线自审
# ============================================================

class TestThreeWayConsistency:
    def test_allowlist_matches_frozen_contract(self) -> None:
        """Actual == Frozen == C25 Allowlist（allowlist 为 path-only 投影）。"""
        actual = _actual_routes()
        allowlist = frozen_allowlist()
        frozen_paths = _frozen_paths()

        assert actual == EXPECTED_OBSERVABILITY_ROUTES, _diff(
            actual, EXPECTED_OBSERVABILITY_ROUTES
        )
        assert {path for _, path in actual} == frozen_paths
        assert allowlist == frozen_paths, _diff(allowlist, frozen_paths)
        assert len(allowlist) == EXPECTED_OBSERVABILITY_ROUTE_COUNT

    def test_frozen_contract_is_single_source_of_truth(self) -> None:
        """防漂移：Step 76 审计侧必须**引用**本契约，而不是再写一份字面量。"""
        audit_source = _source("tests/test_observability_http_allowlist_audit.py")

        assert "test_observability_http_allowlist_regression" in audit_source, (
            "Step 76 审计未引用冻结契约（可能出现两份可漂移的基线）"
        )
        assert "EXPECTED_OBSERVABILITY_ROUTES" in audit_source
        # 本文件是唯一持有冻结字面量的测试模块
        assert "EXPECTED_OBSERVABILITY_ROUTES" in _source(_SELF)
        for method, path in EXPECTED_OBSERVABILITY_ROUTES:
            assert f'"{path}"' in _source(_SELF), (method, path)

    def test_regression_file_is_offline(self) -> None:
        tree = ast.parse(_source(_SELF))
        module_level: set[str] = set()
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
        # 只允许复用 Step 76 的测试侧发现器
        assert "tests.test_observability_http_allowlist_audit" in module_level
        # 本文件**不新建**第二套扫描器：不得出现 api 目录扫描 / 自有 AST 发现实现
        # （needle 动态拼接：避免断言自身文本被误判为违规）
        source = _source(_SELF)
        for needle in (
            "/".join(("backend", "app", "api")),
            "def " + "discover_routes",
            "def " + "observability_routes",
            "def " + "frozen_allowlist",
        ):
            assert needle not in source, needle
        # 复用解析器确实“活着”（防止 import 失效导致空集假绿）
        assert len(frozen_allowlist()) == EXPECTED_OBSERVABILITY_ROUTE_COUNT
        assert len(discover_routes()) > EXPECTED_OBSERVABILITY_ROUTE_COUNT


__all__ = [
    "EXPECTED_OBSERVABILITY_ROUTE_COUNT",
    "EXPECTED_OBSERVABILITY_ROUTES",
    "TestFrozenEntries",
    "TestFrozenRouteContract",
    "TestThreeWayConsistency",
]
