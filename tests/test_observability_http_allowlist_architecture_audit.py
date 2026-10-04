"""Observability Allowlist **Test Architecture Self-Audit**（Phase 3.12 Step 78）。

性质：**静态架构审计**（offline · AST only）。

```text
DB = 0 · Network = 0 · DeepSeek = 0 · backend = 只读
```

它保护的关系（Step 76 / 77 的测试架构）：

```text
Frozen Contract（tests/test_observability_http_allowlist_regression.py
                 ::EXPECTED_OBSERVABILITY_ROUTES）        ← 唯一事实来源
        ▲ 引用
Route Discovery Scanner（tests/test_observability_http_allowlist_audit.py）← 唯一可复用实现
        ▲ 引用
本文件（Architecture Audit）—— 只读源码，不 import 任何被测测试模块
        └── 保护：不出现第二份完整 allowlist / 第二个 scanner / 循环 import / 绕过契约
```

被审计的 4 个模块（**family registry**）：

```text
tests/test_observability_http_allowlist_regression.py       Frozen Contract（declaration）
tests/test_observability_http_allowlist_audit.py            Route Scanner（single impl）
tests/test_tool_observability_architecture_audit.py          C25 Allowlist（consumer / drift gate）
tests/test_observability_http_allowlist_architecture_audit.py 本文件（self-audit）
```

检测器全部基于 AST（**不使用全文搜索**）：Assign / AnnAssign / FunctionDef / ClassDef /
Call / Set / Tuple / List / Constant / Name，并做轻量常量折叠（Name → 模块/局部字符串常量、
`os.path.join`、`/` 拼接）以定位文件系统扫描目标。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TESTS_DIR = "tests"
_SELF = "tests/test_observability_http_allowlist_architecture_audit.py"

_FROZEN_MODULE = "tests/test_observability_http_allowlist_regression.py"
_SCANNER_MODULE = "tests/test_observability_http_allowlist_audit.py"
_ALLOWLIST_MODULE = "tests/test_tool_observability_architecture_audit.py"

#: 冻结常量名（**唯一声明**；其它模块只能引用）。
_FROZEN_NAME = "EXPECTED_OBSERVABILITY_ROUTES"
#: C25 白名单变量名（consumer）。
_ALLOWLIST_NAME = "allowed_paths"
#: **必须**由唯一 scanner 提供的三个可复用发现函数。
_REQUIRED_DISCOVERY_FUNCTIONS: tuple[str, ...] = (
    "discover_routes",
    "observability_routes",
    "frozen_allowlist",
)
#: 可复用 route discovery 的函数名（检测超集；只允许出现在 scanner 模块）。
_DISCOVERY_FUNCTION_NAMES: tuple[str, ...] = (
    *_REQUIRED_DISCOVERY_FUNCTIONS,
    "route_discovery",
    "discover_observability_routes",
)
#: observability 路径前缀（用于识别"路径字面量集合"）。
_OBSERVABILITY_PREFIX = "/observability/"
#: 一个集合中含 ≥ N 条 observability 路径 → 视为"路径集合声明"（≥2 即纳入登记）。
_PATH_SET_THRESHOLD = 2

#: **冻结期望：路径集合字面量登记表**（module → 单字面量内最大 observability 路径数）。
#: 任何新模块 / 数量变化 = 可能的新 allowlist（先失败，再人工授权）。
_EXPECTED_PATH_SET_REGISTRY: dict[str, int] = {
    # Frozen Contract（唯一事实来源）
    _FROZEN_MODULE: 6,
    # C25 allowlist（consumer / drift gate；逐条列出 6 条）
    _ALLOWLIST_MODULE: 6,
    # 两条参数化 route 的**定点断言**（非完整集合；§八 允许）
    _SCANNER_MODULE: 2,
}

#: **冻结期望：文件系统 traversal 登记表**（module → 遍历次数；目标命中 backend/app/api）。
#: 每条登记 = module + scanner operation + reason（新模块 / 计数变化 → 先失败，再人工授权）。
_EXPECTED_FS_TRAVERSAL_REGISTRY: dict[str, int] = {
    _SCANNER_MODULE: 1,                                        # Route Discovery（唯一 scanner）
    _ALLOWLIST_MODULE: 2,                                      # 路由白名单扫描 + 依赖方向扫描
    "tests/test_tool_observability_persistence_architecture.py": 2,   # 静态依赖审计（非路由发现）
    "tests/test_tool_chat_architecture_contract.py": 1,        # 静态契约审计（非路由发现）
    # ---- Conversation 契约族（Phase 4.1 Step 2~9；静态契约审计，非路由发现）----
    # operation 均为 backend/app/api 目录遍历；只做文件清单 / AST 边界检查，不产 route 集合。
    "tests/test_conversation_api_architecture_audit.py": 1,     # api_root.rglob('conversation*.py') —— API 模块边界审计
    "tests/test_conversation_api_contract.py": 1,               # api_root.rglob('conversation*.py') —— 路由集合契约冻结
    "tests/test_conversation_architecture_contract.py": 1,      # api_root.glob('*.py') —— 设计阶段架构审计
    "tests/test_conversation_model_contract.py": 1,             # api_root.glob('*.py') —— 模型契约 scope 审计
    "tests/test_conversation_persistence_contract.py": 1,       # (_REPO_ROOT / _API_DIR).glob('conversation*.py') —— 持久化契约 AST guard
    "tests/test_conversation_service_boundary_contract.py": 1,  # api_root.glob('*.py') —— Service 边界契约审计
}

#: 只允许 scanner 模块实现的"路由发现"函数持有者集合。
_EXPECTED_DISCOVERY_MODULES: frozenset[str] = frozenset({_SCANNER_MODULE})

#: 模块级 import 图中允许的边（consumer → provider）。
_EXPECTED_MODULE_LEVEL_EDGES: frozenset[tuple[str, str]] = frozenset(
    {
        # Regression（consumer）→ Scanner（provider）
        (_FROZEN_MODULE, _SCANNER_MODULE),
    }
)

#: 只允许出现"函数内延迟引用"的边（避免循环 import）。
_EXPECTED_LAZY_EDGES: frozenset[tuple[str, str]] = frozenset(
    {
        # Scanner → Frozen Contract（函数内 import）
        (_SCANNER_MODULE, _FROZEN_MODULE),
    }
)

#: §十七 安全：禁止标识符（本文件为架构审计，不得引入任何凭据相关标识符）。
_FORBIDDEN_IDENTIFIERS: tuple[str, ...] = (
    "api_key",
    "password",
    "authorization",
    "database_url",
)

#: §十七 禁止依赖（离线静态审计）。
_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "backend.app.db",
    "sqlalchemy",
    "psycopg",
    "psycopg2",
    "redis",
    "celery",
    "kafka",
    "httpx",
    "requests",
    "aiohttp",
    "openai",
    "socket",
)


@dataclass(frozen=True)
class PathSetFinding:
    """一个"路径集合字面量"发现结果。"""

    module: str
    size: int
    paths: tuple[str, ...]


@dataclass(frozen=True)
class TraversalFinding:
    """一次文件系统遍历调用（已做常量折叠）。"""

    module: str
    expression: str
    resolved_dir: str
    resolved_args: str = ""

    @property
    def targets_api_dir(self) -> bool:
        normalized = self.resolved_dir.replace("\\", "/").rstrip("/")
        return normalized.endswith("app/api")


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def _test_modules() -> tuple[str, ...]:
    return tuple(
        f"{_TESTS_DIR}/{path.name}"
        for path in sorted((_REPO_ROOT / _TESTS_DIR).glob("test_*.py"))
    )


@lru_cache(maxsize=1)
def _trees() -> tuple[tuple[str, ast.Module], ...]:
    return tuple(
        (module, ast.parse(_source(module))) for module in _test_modules()
    )


@lru_cache(maxsize=None)
def _consts_for(module: str) -> dict[str, str]:
    """按模块缓存的字符串常量表（只读使用）。"""
    return _string_constants(ast.parse(_source(module)))


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """收集（任意作用域的）字符串常量（含 ``os.path.join`` / ``a / b`` 折叠）。

    迭代 3 轮以解析依赖链（例如 ``api_dir = os.path.join(ROOT, "backend",
    "app", "api")`` 或 ``API_DIR = "backend/app/api"``）。
    """
    consts: dict[str, str] = {}
    for _ in range(3):
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            resolved = _resolve(node.value, consts)
            if not resolved:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    consts[target.id] = resolved
    return consts


def _resolve(node: ast.AST | None, consts: dict[str, str]) -> str:
    """轻量常量折叠：Constant / Name / ``a / b`` / ``os.path.join(...)``。"""
    if node is None:
        return ""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id, "")
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = _resolve(node.left, consts), _resolve(node.right, consts)
        return f"{left}/{right}" if left or right else ""
    if isinstance(node, ast.Call):
        name = ast.unparse(node.func)
        if name in {"os.path.join", "path.join", "join"}:
            return "/".join(
                filter(None, (_resolve(arg, consts) for arg in node.args))
            )
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return ""


def _collection_literals(tree: ast.Module):
    """set / list / tuple 字面量，以及 set(...) / frozenset(...) 的字面量实参。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            yield node
        elif isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            if (
                name in {"set", "frozenset", "list", "tuple"}
                and node.args
                and isinstance(node.args[0], (ast.Set, ast.List, ast.Tuple))
            ):
                yield node.args[0]


def _elements(literal: ast.AST) -> list[ast.AST]:
    """展平一层：集合元素；元组元素内部再展开（``("GET", "/path")``）。"""
    elts = getattr(literal, "elts", [])
    flattened: list[ast.AST] = []
    for element in elts:
        if isinstance(element, ast.Tuple):
            flattened.extend(element.elts)
        else:
            flattened.append(element)
    return flattened


@lru_cache(maxsize=None)
def find_observability_path_sets() -> tuple[PathSetFinding, ...]:
    """所有"单字面量内含 ≥2 条 observability 路径"的声明（AST 级）。"""
    findings: list[PathSetFinding] = []
    for module, tree in _trees():
        best: tuple[str, ...] = ()
        for literal in _collection_literals(tree):
            paths = sorted(
                {
                    element.value
                    for element in _elements(literal)
                    if isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                    and element.value.startswith(_OBSERVABILITY_PREFIX)
                }
            )
            if len(paths) >= _PATH_SET_THRESHOLD and len(paths) > len(best):
                best = tuple(paths)
        if best:
            findings.append(
                PathSetFinding(module=module, size=len(best), paths=best)
            )
    return tuple(findings)


@lru_cache(maxsize=None)
def find_fs_traversals() -> tuple[TraversalFinding, ...]:
    """所有 glob / rglob / os.walk / os.listdir 调用（含目标常量折叠）。"""
    findings: list[TraversalFinding] = []
    for module, tree in _trees():
        consts = _consts_for(module)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            if not (
                name.endswith((".glob", ".rglob"))
                or name in {"glob.glob", "os.walk", "os.listdir"}
            ):
                continue
            if name.endswith((".glob", ".rglob")):
                target = node.func.value
            else:
                target = node.args[0] if node.args else None
            findings.append(
                TraversalFinding(
                    module=module,
                    expression=ast.unparse(node)[:120],
                    resolved_dir=_resolve(target, consts),
                    resolved_args=" ".join(
                        part
                        for part in (_resolve(arg, consts) for arg in node.args)
                        if part
                    ),
                )
            )
    return tuple(findings)


@lru_cache(maxsize=None)
def find_function_definitions(name: str) -> tuple[str, ...]:
    """任意作用域内定义 ``name`` 的模块集合。"""
    modules: list[str] = []
    for module, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == name:
                    modules.append(module)
                    break
    return tuple(sorted(set(modules)))


@lru_cache(maxsize=None)
def find_module_level_assignments(name: str) -> tuple[str, ...]:
    """模块级 Assign / AnnAssign 目标为 ``name`` 的模块集合（= 声明计数）。"""
    modules: list[str] = []
    for module, tree in _trees():
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in targets
            ):
                modules.append(module)
                break
    return tuple(sorted(set(modules)))


def _import_edges(module: str, *, module_level: bool) -> set[tuple[str, str]]:
    tree = ast.parse(_source(module))
    nodes = tree.body if module_level else list(ast.walk(tree))
    edges: set[tuple[str, str]] = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("tests."):
                edges.add((module, f"{node.module.replace('.', '/')}.py"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("tests."):
                    edges.add((module, f"{alias.name.replace('.', '/')}.py"))
    return edges


def _module_level_imports(module: str) -> set[str]:
    tree = ast.parse(_source(module))
    imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


# ============================================================
# 1 ~ 4：Frozen Contract 唯一性 / 无重复 allowlist
# ============================================================

class TestFrozenContractUniqueness:
    def test_frozen_route_contract_has_single_declaration(self) -> None:
        declaring = find_module_level_assignments(_FROZEN_NAME)

        assert len(declaring) == 1, (
            f"{_FROZEN_NAME} 声明次数应为 1（当前: {list(declaring)}）"
        )
        assert declaring[0] == _FROZEN_MODULE, declaring[0]

    def test_declaring_module_is_the_frozen_contract_file(self) -> None:
        assert (_REPO_ROOT / _FROZEN_MODULE).is_file()
        source = _source(_FROZEN_MODULE)

        assert _FROZEN_NAME in source
        # 冻结声明必须是集合字面量（可被其它测试引用为唯一事实来源）
        tree = ast.parse(source)
        declarations = [
            node
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id == _FROZEN_NAME
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
            )
        ]
        assert len(declarations) == 1
        assert isinstance(declarations[0].value, ast.Call)
        assert ast.unparse(declarations[0].value.func) == "frozenset"

    def test_no_duplicate_observability_allowlist(self) -> None:
        findings = {
            finding.module: finding.size
            for finding in find_observability_path_sets()
        }

        assert findings == _EXPECTED_PATH_SET_REGISTRY, {
            "unexpected": {
                module: size
                for module, size in findings.items()
                if module not in _EXPECTED_PATH_SET_REGISTRY
            },
            "changed": {
                module: (expected, findings.get(module))
                for module, expected in _EXPECTED_PATH_SET_REGISTRY.items()
                if findings.get(module) != expected
            },
        }

    def test_consumer_allowlist_matches_frozen_contract(self) -> None:
        """C25 allowlist（consumer）必须与 Frozen Contract 逐条一致。"""
        findings = {finding.module: finding for finding in find_observability_path_sets()}
        frozen = findings[_FROZEN_MODULE].paths
        consumer = findings[_ALLOWLIST_MODULE].paths

        assert set(consumer) == set(frozen)
        assert len(frozen) == 6
        # consumer 字面量是 string-only（无 method 维度），与 C25 审计语义一致
        assert all(path.startswith(_OBSERVABILITY_PREFIX) for path in consumer)


# ============================================================
# 5 ~ 8：单一 Scanner / 无重复扫描 / 引用方向
# ============================================================

class TestScannerUniqueness:
    def test_api_route_scanner_has_single_implementation(self) -> None:
        holders: dict[str, list[str]] = {}
        for name in _DISCOVERY_FUNCTION_NAMES:
            for module in find_function_definitions(name):
                holders.setdefault(module, []).append(name)

        assert set(holders) == _EXPECTED_DISCOVERY_MODULES, {
            "duplicate scanner locations": sorted(
                module
                for module in holders
                if module not in _EXPECTED_DISCOVERY_MODULES
            ),
            "holders": holders,
        }
        assert sorted(holders[_SCANNER_MODULE]) == sorted(
            _REQUIRED_DISCOVERY_FUNCTIONS
        )

    def test_no_second_backend_api_scanner(self) -> None:
        registry: dict[str, int] = {}
        details: list[str] = []
        for finding in find_fs_traversals():
            if not finding.targets_api_dir:
                continue
            registry[finding.module] = registry.get(finding.module, 0) + 1
            details.append(f"{finding.module} :: {finding.expression}")

        assert registry == _EXPECTED_FS_TRAVERSAL_REGISTRY, {
            "registry": registry,
            "expected": _EXPECTED_FS_TRAVERSAL_REGISTRY,
            "new_scanners": sorted(
                module
                for module in registry
                if module not in _EXPECTED_FS_TRAVERSAL_REGISTRY
            ),
            "details": details,
        }
        # 只有 scanner 模块可复用"路由发现"；其余仅静态依赖审计（不产 route 集合）
        route_holders = {
            finding.module for finding in find_observability_path_sets()
        }
        assert route_holders <= set(_EXPECTED_PATH_SET_REGISTRY)

    def test_no_duplicate_observability_route_source(self) -> None:
        """除 scanner 模块外，不得再定义"路由发现"函数或 ``*routes`` 集合。"""
        extra = {
            module
            for name in _DISCOVERY_FUNCTION_NAMES
            for module in find_function_definitions(name)
        } - _EXPECTED_DISCOVERY_MODULES

        assert extra == set(), sorted(extra)
        # 其它模块也不得用模块级别名"重新出口"route 来源
        for name in _DISCOVERY_FUNCTION_NAMES:
            holders = find_module_level_assignments(name)
            assert set(holders) <= _EXPECTED_DISCOVERY_MODULES, (name, holders)

    def test_frozen_contract_import_direction(self) -> None:
        module_level = _import_edges(_FROZEN_MODULE, module_level=True)
        lazy = _import_edges(_SCANNER_MODULE, module_level=False)
        scanner_level = _import_edges(_SCANNER_MODULE, module_level=True)

        assert module_level == _EXPECTED_MODULE_LEVEL_EDGES, sorted(module_level)
        assert lazy >= _EXPECTED_LAZY_EDGES, sorted(lazy)
        # 反向边必须是**延迟**（函数内）引用，否则形成循环 import
        assert (_SCANNER_MODULE, _FROZEN_MODULE) not in scanner_level

    def test_no_circular_import_in_contract_family(self) -> None:
        family = (
            _FROZEN_MODULE,
            _SCANNER_MODULE,
            _ALLOWLIST_MODULE,
            _SELF,
        )
        edges: set[tuple[str, str]] = set()
        for module in family:
            edges |= _import_edges(module, module_level=True)

        # 模块级依赖图必须无环（拓扑：Frozen ← Scanner ← 本文件；consumer 独立）
        order = {module: index for index, module in enumerate(reversed(family))}
        for consumer, provider in edges:
            if consumer in order and provider in order:
                assert order[provider] < order[consumer], (consumer, provider)
            assert consumer != provider, consumer

    def test_architecture_audit_reads_sources_instead_of_importing(self) -> None:
        imports = _module_level_imports(_SELF)

        for module in (
            "tests.test_observability_http_allowlist_regression",
            "tests.test_observability_http_allowlist_audit",
            "tests.test_tool_observability_architecture_audit",
        ):
            assert module not in imports, module


# ============================================================
# 9 ~ 11：文件系统扫描完整性 / 安全 / 离线
# ============================================================

class TestScanAndSecurity:
    def test_api_dir_traversals_do_not_build_route_sets(self) -> None:
        """遍历 api 目录 ≠ 允许建立 route 集合：非 scanner 模块必须 0 路径集合。"""
        traversing = {
            finding.module
            for finding in find_fs_traversals()
            if finding.targets_api_dir
        }
        path_set_modules = {
            finding.module for finding in find_observability_path_sets()
        }

        assert traversing & path_set_modules <= {
            _SCANNER_MODULE,
            _ALLOWLIST_MODULE,
            _FROZEN_MODULE,
        }, sorted(traversing & path_set_modules)

    def test_no_secrets_identifiers_in_contract_family(self) -> None:
        report: dict[str, list[str]] = {}
        for module in (
            _FROZEN_MODULE,
            _SCANNER_MODULE,
            _ALLOWLIST_MODULE,
            _SELF,
        ):
            names = {
                node.id
                for node in ast.walk(ast.parse(_source(module)))
                if isinstance(node, ast.Name)
            } | {
                node.attr
                for node in ast.walk(ast.parse(_source(module)))
                if isinstance(node, ast.Attribute)
            }
            hits = sorted(name for name in _FORBIDDEN_IDENTIFIERS if name in names)
            if hits:
                report[module] = hits

        assert report == {}, report

    def test_contract_family_has_no_db_or_network_dependency(self) -> None:
        report: dict[str, list[str]] = {}
        for module in (
            _FROZEN_MODULE,
            _SCANNER_MODULE,
            _ALLOWLIST_MODULE,
            _SELF,
        ):
            hits = sorted(
                imported
                for imported in _module_level_imports(module)
                for prefix in _FORBIDDEN_IMPORT_PREFIXES
                if imported.startswith(prefix)
            )
            if hits:
                report[module] = hits

        assert report == {}, report

    def test_architecture_audit_is_offline(self) -> None:
        imports = _module_level_imports(_SELF)

        assert imports <= {
            "__future__",
            "ast",
            "dataclasses",
            "functools",
            "pathlib",
            "pytest",
        }, sorted(imports)
        # 不启动子进程 / 不读环境变量（纯静态）
        assert "subprocess" not in imports and "os" not in imports


# ============================================================
# 12 ~ 13：元守卫（detector 活性） / family registry
# ============================================================

class TestMetaGuards:
    def test_detectors_are_live(self) -> None:
        path_sets = find_observability_path_sets()
        traversals = find_fs_traversals()
        frozen = find_module_level_assignments(_FROZEN_NAME)

        assert len(path_sets) == len(_EXPECTED_PATH_SET_REGISTRY)
        assert sum(finding.size for finding in path_sets) >= 14
        assert any(finding.targets_api_dir for finding in traversals)
        assert any(
            finding.module == _SCANNER_MODULE for finding in traversals
        )
        assert frozen == (_FROZEN_MODULE,)
        assert find_function_definitions("discover_routes") == (_SCANNER_MODULE,)

    def test_contract_family_modules_exist(self) -> None:
        for module in (
            _FROZEN_MODULE,
            _SCANNER_MODULE,
            _ALLOWLIST_MODULE,
            _SELF,
        ):
            assert (_REPO_ROOT / module).is_file(), module
            assert module in set(_test_modules()), module

    def test_no_wildcard_contract_entries(self) -> None:
        for finding in find_observability_path_sets():
            for path in finding.paths:
                assert "*" not in path, (finding.module, path)

    def test_expected_registries_are_declared_once_in_this_file(self) -> None:
        for name in (
            "_EXPECTED_PATH_SET_REGISTRY",
            "_EXPECTED_FS_TRAVERSAL_REGISTRY",
            "_EXPECTED_DISCOVERY_MODULES",
            "_EXPECTED_MODULE_LEVEL_EDGES",
        ):
            assert find_module_level_assignments(name) == (_SELF,), name

        assert len(_EXPECTED_PATH_SET_REGISTRY) == 3
        # 4 既有条目 + 6 Conversation 契约族（Phase 4.1 Step 2~9）—— 逐条显式登记，非通配放行
        assert len(_EXPECTED_FS_TRAVERSAL_REGISTRY) == 10
        assert _EXPECTED_DISCOVERY_MODULES == frozenset({_SCANNER_MODULE})
        assert _EXPECTED_MODULE_LEVEL_EDGES == frozenset(
            {(_FROZEN_MODULE, _SCANNER_MODULE)}
        )
        assert pytest is not None  # 保持 pytest import（fixture / skip 语义预留）


__all__ = [
    "PathSetFinding",
    "TraversalFinding",
    "find_fs_traversals",
    "find_function_definitions",
    "find_module_level_assignments",
    "find_observability_path_sets",
    "TestFrozenContractUniqueness",
    "TestMetaGuards",
    "TestScanAndSecurity",
    "TestScannerUniqueness",
]
