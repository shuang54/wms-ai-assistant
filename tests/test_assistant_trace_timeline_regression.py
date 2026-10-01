"""Trace / Timeline **Contract Regression 入口**（Phase 3.12 Step 74）。

性质：**统一收口（suite collector / gate）**，不新增任何测试语义、不复制既有测试逻辑。

    * 只把 Step 64～73 已经验证过的契约，收口成一个长期可重复运行的入口；
    * 通过「注册表 + 静态一致性校验 + 嵌套 suite 执行」实现，**不**重新实现 E2E；
    * 生产代码 0 改动（本文件不接触 backend 实现，只读文件 + 跑既有测试）。

两种运行模式（显式）：

```text
offline（默认）
    python -m pytest -q tests/test_assistant_trace_timeline_regression.py
        → 静态注册表校验 + 离线 suite（RUN_DB_TESTS 强制清空）

DB-gated
    $env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_assistant_trace_timeline_regression.py
        → 上述全部 + 离线 suite（仍以无 DB 模式）+ DB suite
```

契约边界（Step 73 冻结，本文件**不新增**）：

```text
partial state 合法 · outcome 不推断 · source_id = persistence PK
group ordering（LLM created_at,id / Tool·RAG id / Outcome ≤1）· request isolation · security
不得生成 event_id / sequence / span_id · 不得提供全局顺序 / pagination
```

真实 LLM 调用 = 0（全部沿用既有 Fake / Stub）；不使用生产 / 开发数据库。
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_assistant_trace_timeline_regression.py"

#: Step 73 的离线契约闸门（**必须**始终包含在离线 suite 内）。
_CONTRACT_GATE = "tests/test_assistant_trace_timeline_contract.py"

#: 嵌套 suite 的墙钟上限（避免 CI 挂死）。
_SUITE_TIMEOUT_SECONDS = 1800

#: 单文件 ``--collect-only`` 的超时（Step 81 §十二；超时 = FAIL，不 retry）。
_COLLECT_TIMEOUT_SECONDS = 30

#: 解析 pytest ``--collect-only -q`` 的 "N tests collected" 行。
_COLLECT_COUNT_PATTERN = re.compile(r"(\d+)\s+tests?\s+collected")

#: Step 74 专属残留命名空间（定向清理；禁止 TRUNCATE / DELETE ALL）。
_STEP74_PREFIX = "step74-"

#: 定向清理模板（**单条 SQL 常量**：必须含 WHERE + 前缀 LIKE；表名 / 列名来自硬编码元组）。
_SQL_DELETE_STEP74 = "DELETE FROM {table} WHERE {column} LIKE :p"

#: 残留计数模板（只读）。
_SQL_COUNT_STEP74 = (
    "SELECT COUNT(*) FROM {table} WHERE {column} LIKE :p"
)

#: 必须覆盖的回归类别：Step 74 §五 的 12 类 + Step 79 新增 1 类。
_REQUIRED_CATEGORIES: tuple[str, ...] = (
    "API Contract",
    "Trace Contract",
    "Timeline Contract",
    "Outcome",
    "Partial State",
    "Read During Write",
    "Cross-request Isolation",
    "Concurrency",
    "Trace ↔ Timeline Consistency",
    "Security",
    "Source ID",
    "Ordering",
    # Phase 3.12 Step 79：Observability HTTP Allowlist 契约族
    "OBSERVABILITY_HTTP_ALLOWLIST",
)

#: **Matrix Contract 规模快照**（Phase 3.12 Step 80 冻结；任何增减都需显式授权）。
EXPECTED_MATRIX_SCALE: dict[str, int] = {
    "categories": 13,          # Step 74 的 12 类 + Step 79 的 OBSERVABILITY_HTTP_ALLOWLIST
    "registered_files": 28,
    "offline_files": 18,       # db == "no" | "partial"
    "db_files": 15,            # db == "yes" | "partial"
}

#: FileSpec.db 的合法取值（= db_required 的映射：no→False；yes/partial→True）。
_DB_MODES: tuple[str, ...] = ("no", "partial", "yes")

#: Step 79 新增类别的 Purpose（写进回归矩阵元数据）。
OBSERVABILITY_HTTP_ALLOWLIST_PURPOSE = (
    "确保 Observability HTTP API 的实际 route、Frozen Contract、C25 Allowlist "
    "三者保持一致（route discovery / allowlist completeness / frozen contract / "
    "single source of truth / scanner uniqueness / security）"
)

DbMode = Literal["no", "partial", "yes"]


@dataclass(frozen=True)
class FileSpec:
    """一个既有测试文件在回归入口中的定位（**只登记，不改写**）。

    元数据（Step 79 §九）：``db`` = DB 依赖方式；``network`` / ``llm`` /
    ``production_code`` = 该入口是否需要网络 / 真实 LLM / 生产代码改动。
    """

    path: str
    db: DbMode
    coverage: str
    network: bool = False
    llm: bool = False
    production_code: bool = False


#: 既有测试文件登记表（真实文件名；`db` 表示该文件的 DB 依赖方式）。
FILES: tuple[FileSpec, ...] = (
    # ---- 离线（DB = 0）----
    FileSpec(_CONTRACT_GATE, "no", "DTO / 路由 / 身份字段 / 分页缺失 / 安全字段（Step 73）"),
    FileSpec("tests/test_assistant_timeline_audit.py", "no", "Timeline 可行性审计（Step 65）"),
    FileSpec("tests/test_assistant_timeline_projection.py", "no", "分组投影 / source_id / 无全局顺序（Step 66）"),
    FileSpec("tests/test_assistant_timeline_api_audit.py", "no", "API 可行性 / 无分页（Step 67）"),
    FileSpec("tests/test_assistant_timeline_api.py", "no", "Timeline HTTP Read API（Step 68）"),
    FileSpec("tests/test_assistant_trace_api.py", "no", "Trace HTTP API / OpenAPI（Step 39）"),
    FileSpec("tests/test_assistant_trace_query_service.py", "no", "Trace 读模型（Step 38）"),
    FileSpec("tests/test_assistant_trace_correlation_e2e.py", "no", "Trace 关联 + 安全（Step 40）"),
    FileSpec("tests/test_assistant_outcome.py", "no", "Outcome 契约（Step 62）"),
    # ---- 离线 + DB（同一文件内含 DB 门控部分）----
    FileSpec("tests/test_assistant_trace_multi_path_e2e.py", "partial", "多路径 Trace / partial state（Step 60）"),
    FileSpec("tests/test_assistant_outcome_persistence.py", "partial", "Outcome 持久化 / 幂等 / 冲突 / 并发（Step 64）"),
    FileSpec("tests/test_assistant_trace_pagination_audit.py", "partial", "无分页（Step 59）"),
    FileSpec("tests/test_assistant_trace_outcome_audit.py", "partial", "Outcome × Trace 审计（Step 61）"),
    FileSpec("tests/test_assistant_outcome_contract_audit.py", "partial", "Outcome 契约审计（Step 62）"),
    # ---- Observability HTTP Allowlist 契约族（Step 79；全部离线）----
    FileSpec(
        "tests/test_observability_http_allowlist_audit.py",
        "no",
        "真实 route 发现 + 白名单双向比对 + 安全（Step 76）",
    ),
    FileSpec(
        "tests/test_observability_http_allowlist_regression.py",
        "no",
        "Frozen Contract（六路由精确集合；唯一事实来源）（Step 77）",
    ),
    FileSpec(
        "tests/test_observability_http_allowlist_architecture_audit.py",
        "no",
        "测试架构自审：单一来源 / 单一 scanner / 无循环 import（Step 78）",
    ),
    FileSpec(
        "tests/test_tool_observability_architecture_audit.py",
        "no",
        "C25 Allowlist consumer（只读端点漂移闸门）",
    ),
    # ---- DB 门控 ----
    FileSpec("tests/test_assistant_trace_api_db.py", "yes", "Trace HTTP + 真实读边界（Step 39）"),
    FileSpec("tests/test_assistant_trace_query_service_db.py", "yes", "Trace QueryService 真实读（Step 38）"),
    FileSpec("tests/test_assistant_trace_correlation_e2e_db.py", "yes", "Trace 真实 PG 关联（Step 40）"),
    FileSpec("tests/test_assistant_trace_persistent_tool_db.py", "yes", "Tool 段持久化关联（Step 41）"),
    FileSpec("tests/test_assistant_trace_rag_integration_e2e_db.py", "yes", "RAG 段持久化关联（Step 48）"),
    FileSpec("tests/test_llm_usage_trace_correlation_db.py", "yes", "LLM provider id ≠ assistant id（Step 36/37）"),
    FileSpec("tests/test_assistant_timeline_db_e2e.py", "yes", "Timeline PostgreSQL E2E / ordering（Step 69）"),
    FileSpec("tests/test_assistant_timeline_concurrency_db_e2e.py", "yes", "并发 / 跨请求隔离（Step 70）"),
    FileSpec("tests/test_assistant_trace_timeline_outcome_consistency.py", "yes", "Trace ↔ Timeline 一致性 + 安全（Step 71）"),
    FileSpec("tests/test_assistant_trace_timeline_read_during_write.py", "yes", "写入中读取 / partial 合法（Step 72）"),
)

#: 12 个类别 → 既有测试文件（**每类可追溯**；不新增测试）。
CATEGORIES: dict[str, tuple[str, ...]] = {
    "API Contract": (
        "tests/test_assistant_timeline_api.py",
        "tests/test_assistant_timeline_api_audit.py",
        "tests/test_assistant_trace_api.py",
        "tests/test_assistant_trace_api_db.py",
        "tests/test_assistant_trace_pagination_audit.py",
    ),
    "Trace Contract": (
        "tests/test_assistant_trace_query_service.py",
        "tests/test_assistant_trace_query_service_db.py",
        "tests/test_assistant_trace_correlation_e2e.py",
        "tests/test_assistant_trace_correlation_e2e_db.py",
        "tests/test_assistant_trace_api_db.py",
        "tests/test_llm_usage_trace_correlation_db.py",
    ),
    "Timeline Contract": (
        "tests/test_assistant_timeline_projection.py",
        "tests/test_assistant_timeline_api.py",
        "tests/test_assistant_timeline_api_audit.py",
        "tests/test_assistant_timeline_db_e2e.py",
        "tests/test_assistant_timeline_audit.py",
    ),
    "Outcome": (
        "tests/test_assistant_outcome.py",
        "tests/test_assistant_outcome_contract_audit.py",
        "tests/test_assistant_trace_outcome_audit.py",
        "tests/test_assistant_outcome_persistence.py",
    ),
    "Partial State": (
        "tests/test_assistant_trace_multi_path_e2e.py",
        "tests/test_assistant_trace_timeline_read_during_write.py",
    ),
    "Read During Write": (
        "tests/test_assistant_trace_timeline_read_during_write.py",
    ),
    "Cross-request Isolation": (
        "tests/test_assistant_timeline_concurrency_db_e2e.py",
        "tests/test_assistant_trace_timeline_outcome_consistency.py",
        "tests/test_assistant_trace_persistent_tool_db.py",
    ),
    "Concurrency": (
        "tests/test_assistant_timeline_concurrency_db_e2e.py",
        "tests/test_assistant_outcome_persistence.py",
    ),
    "Trace ↔ Timeline Consistency": (
        "tests/test_assistant_trace_timeline_outcome_consistency.py",
    ),
    "Security": (
        "tests/test_assistant_trace_timeline_outcome_consistency.py",
        "tests/test_assistant_trace_correlation_e2e.py",
        "tests/test_assistant_timeline_api.py",
        _CONTRACT_GATE,
        "tests/test_assistant_trace_rag_integration_e2e_db.py",
    ),
    "Source ID": (
        "tests/test_assistant_timeline_projection.py",
        "tests/test_assistant_timeline_db_e2e.py",
        _CONTRACT_GATE,
    ),
    "Ordering": (
        "tests/test_assistant_timeline_db_e2e.py",
        _CONTRACT_GATE,
        "tests/test_assistant_timeline_projection.py",
    ),
    # Phase 3.12 Step 79：Observability HTTP Allowlist 契约族
    "OBSERVABILITY_HTTP_ALLOWLIST": (
        "tests/test_observability_http_allowlist_audit.py",
        "tests/test_observability_http_allowlist_regression.py",
        "tests/test_observability_http_allowlist_architecture_audit.py",
        "tests/test_tool_observability_architecture_audit.py",
    ),
}

#: 代表性契约 node（Step 79 §八）：**只登记 node id**，不复制断言逻辑。
#: 完整契约仍由各自测试文件覆盖；此处仅提供"矩阵 → 代表性入口"。
REPRESENTATIVE_CONTRACT_NODES: tuple[tuple[str, str], ...] = (
    (
        "OBSERVABILITY_HTTP_ALLOWLIST",
        "tests/test_observability_http_allowlist_audit.py"
        "::TestBaseline::test_10_six_route_baseline_is_exact",
    ),
    (
        "OBSERVABILITY_HTTP_ALLOWLIST",
        "tests/test_observability_http_allowlist_regression.py"
        "::TestFrozenRouteContract::test_exact_observability_route_set",
    ),
    (
        "OBSERVABILITY_HTTP_ALLOWLIST",
        "tests/test_observability_http_allowlist_architecture_audit.py"
        "::TestFrozenContractUniqueness"
        "::test_frozen_route_contract_has_single_declaration",
    ),
    (
        "OBSERVABILITY_HTTP_ALLOWLIST",
        "tests/test_observability_http_allowlist_architecture_audit.py"
        "::TestScannerUniqueness"
        "::test_api_route_scanner_has_single_implementation",
    ),
    (
        "OBSERVABILITY_HTTP_ALLOWLIST",
        "tests/test_tool_observability_architecture_audit.py"
        "::TestC25QuerySnapshotApi"
        "::test_c25_13_only_allowlisted_tool_observability_http_api",
    ),
)


def _source(relative: str) -> str:
    # utf-8-sig：容忍个别历史测试文件的 BOM（不改变内容语义）
    return (_REPO_ROOT / relative).read_text(encoding="utf-8-sig")


def _code_string_constants(relative: str) -> set[str]:
    """文件中的**非 docstring** 字符串常量（供"是否真的门控 DB"判定）。"""
    tree = ast.parse(_source(relative))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))

    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


def _file_specs() -> dict[str, FileSpec]:
    return {spec.path: spec for spec in FILES}


def _offline_suite() -> tuple[str, ...]:
    """离线 suite：纯离线文件 + partial 文件（DB 段自动 skip）。"""
    return tuple(
        spec.path for spec in FILES if spec.db in {"no", "partial"}
    )


def _db_suite() -> tuple[str, ...]:
    """DB suite：DB 门控文件 + partial 文件（其离线段同时得到执行）。"""
    return tuple(
        spec.path for spec in FILES if spec.db in {"yes", "partial"}
    )


def _imported_modules(path: str, *, module_level_only: bool) -> set[str]:
    """AST 取 import 模块（``module_level_only`` = 只看模块顶层）。"""
    tree = ast.parse(_source(path))
    nodes: list[ast.AST] = []
    if module_level_only:
        nodes = list(tree.body)
    else:
        nodes = list(ast.walk(tree))

    modules: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _db_mode() -> bool:
    return os.getenv("RUN_DB_TESTS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _pytest_env(with_db: bool) -> dict[str, str]:
    """子进程环境：``with_db=False`` 时**强制移除** ``RUN_DB_TESTS``（离线语义）。"""
    env = dict(os.environ)
    if with_db:
        env["RUN_DB_TESTS"] = "1"
    else:
        env.pop("RUN_DB_TESTS", None)
    return env


def _run_pytest(
    paths: tuple[str, ...],
    *,
    with_db: bool,
    collect_only: bool = False,
    timeout: int = _SUITE_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """嵌套执行既有 suite（**不复制**任何测试逻辑）。

    ``collect_only`` = True 时只收集 node（``--collect-only``：收集但不执行）。
    """
    env = _pytest_env(with_db)
    extra = ["--collect-only"] if collect_only else []
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:randomly",
            "--tb=line",
            *extra,
            *paths,
        ],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _cleanup_step74_rows() -> int:
    """定向清理 Step 74 命名空间残留（只删 ``step74-``；无 TRUNCATE / 无 DELETE ALL）。

    **函数内延迟 import**：保证本文件在 offline 模式不加载 DB 依赖。
    """
    from sqlalchemy import text

    from backend.app.db.session import get_engine

    engine = get_engine()
    if engine is None:
        return 0

    tables = (
        ("ai_ops.llm_usage_record", "assistant_request_id"),
        ("ai_ops.tool_execution_record", "request_id"),
        ("ai_ops.rag_execution_record", "request_id"),
        ("ai_ops.assistant_outcome_record", "assistant_request_id"),
    )
    deleted = 0
    with engine.begin() as conn:
        for table, column in tables:
            deleted += conn.execute(
                text(
                    _SQL_DELETE_STEP74.format(table=table, column=column)
                ),
                {"p": f"{_STEP74_PREFIX}%"},
            ).rowcount
    return deleted


def _file_categories() -> dict[str, tuple[str, ...]]:
    """file → categories（拓扑映射；只读）。"""
    mapping: dict[str, list[str]] = {}
    for category, paths in CATEGORIES.items():
        for path in paths:
            mapping.setdefault(path, []).append(category)
    return {path: tuple(sorted(categories)) for path, categories in mapping.items()}


def _suite_topology() -> dict[str, tuple[str, ...]]:
    """suite → files + 未被任何 suite 覆盖的注册文件（拓扑审计）。"""
    offline, db = set(_offline_suite()), set(_db_suite())
    registered = {spec.path for spec in FILES}

    return {
        "offline": tuple(sorted(offline)),
        "db": tuple(sorted(db)),
        "uncovered": tuple(sorted(registered - (offline | db))),
    }


def _category_suite_coverage() -> dict[str, dict[str, tuple[str, ...]]]:
    """category → {offline, db, missing}（该 category 的文件在 suite 中的落位）。"""
    offline, db = set(_offline_suite()), set(_db_suite())
    registered = {spec.path for spec in FILES}
    coverage: dict[str, dict[str, tuple[str, ...]]] = {}

    for category, paths in CATEGORIES.items():
        files = set(paths)
        coverage[category] = {
            "offline": tuple(sorted(files & offline)),
            "db": tuple(sorted(files & db)),
            "missing": tuple(sorted((files & registered) - (offline | db))),
        }
    return coverage


@dataclass(frozen=True)
class CollectReport:
    """单文件 ``--collect-only`` 结果（Step 81；**审计结果，不写入 FileSpec**）。"""

    path: str
    returncode: int
    collected: int
    tail: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.collected > 0


def _collect_one(path: str) -> CollectReport:
    """单文件 collection audit（复用 ``_run_pytest``；超时 = FAIL，无 retry）。"""
    try:
        result = _run_pytest(
            (path,),
            with_db=False,
            collect_only=True,
            timeout=_COLLECT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return CollectReport(
            path=path,
            returncode=124,
            collected=0,
            tail=f"TIMEOUT after {_COLLECT_TIMEOUT_SECONDS}s",
        )

    match = _COLLECT_COUNT_PATTERN.search(result.stdout)
    return CollectReport(
        path=path,
        returncode=result.returncode,
        collected=int(match.group(1)) if match else 0,
        tail=(result.stdout + result.stderr)[-600:],
    )


@lru_cache(maxsize=1)
def _collectability_report() -> tuple[CollectReport, ...]:
    """对 FILES 中每个注册文件做一次 collection audit（会话内缓存）。"""
    return tuple(_collect_one(spec.path) for spec in FILES)


def _failures(reports: tuple[CollectReport, ...]) -> list[str]:
    return [
        f"{report.path} (rc={report.returncode}, collected={report.collected})"
        for report in reports
        if not report.ok
    ]


def _step74_residue() -> dict[str, int]:
    """Step 74 命名空间残留计数（只读；函数内延迟 import）。"""
    from sqlalchemy import text

    from backend.app.db.session import get_engine

    engine = get_engine()
    if engine is None:
        return {}

    tables = (
        ("llm_usage_record", "assistant_request_id"),
        ("tool_execution_record", "request_id"),
        ("rag_execution_record", "request_id"),
        ("assistant_outcome_record", "assistant_request_id"),
    )
    with engine.connect() as conn:
        return {
            f"ai_ops.{table}": int(
                conn.execute(
                    text(
                        _SQL_COUNT_STEP74.format(
                            table=f"ai_ops.{table}", column=column
                        )
                    ),
                    {"p": f"{_STEP74_PREFIX}%"},
                ).scalar_one()
            )
            for table, column in tables
        }


# ============================================================
# 注册表一致性（离线 · 静态）
# ============================================================

class TestRegistry:
    def test_all_registered_files_exist(self) -> None:
        for spec in FILES:
            assert (_REPO_ROOT / spec.path).is_file(), spec.path

    def test_required_categories_are_covered(self) -> None:
        assert set(CATEGORIES) == set(_REQUIRED_CATEGORIES)
        for category, paths in CATEGORIES.items():
            assert paths, category
            for path in paths:
                assert (_REPO_ROOT / path).is_file(), (category, path)

    def test_every_registered_file_is_used_by_a_category(self) -> None:
        used = {path for paths in CATEGORIES.values() for path in paths}
        assert {spec.path for spec in FILES} <= used

    def test_offline_gate_always_includes_contract_baseline(self) -> None:
        assert _CONTRACT_GATE in _offline_suite()
        assert _CONTRACT_GATE in CATEGORIES["Security"]
        assert _CONTRACT_GATE in CATEGORIES["Source ID"]
        assert _CONTRACT_GATE in CATEGORIES["Ordering"]

    def test_suites_are_unique_and_exclude_self(self) -> None:
        for suite in (_offline_suite(), _db_suite()):
            assert len(set(suite)) == len(suite)
            assert _SELF not in suite
        # partial 文件（离线 + DB 双跑）是唯一允许的重叠
        overlap = set(_offline_suite()) & set(_db_suite())
        assert overlap == {
            spec.path for spec in FILES if spec.db == "partial"
        }

    def test_db_suite_files_are_really_db_gated(self) -> None:
        for spec in FILES:
            if spec.db == "no":
                continue
            assert "RUN_DB_TESTS" in _source(spec.path), spec.path

    def test_observability_allowlist_category_is_registered(self) -> None:
        """Step 79：新类别已登记，且**只**由 4 个离线契约文件构成。"""
        assert "OBSERVABILITY_HTTP_ALLOWLIST" in _REQUIRED_CATEGORIES
        files = CATEGORIES["OBSERVABILITY_HTTP_ALLOWLIST"]

        assert files == (
            "tests/test_observability_http_allowlist_audit.py",        # Step 76
            "tests/test_observability_http_allowlist_regression.py",   # Step 77
            "tests/test_observability_http_allowlist_architecture_audit.py",  # Step 78
            "tests/test_tool_observability_architecture_audit.py",     # C25 consumer
        )
        assert OBSERVABILITY_HTTP_ALLOWLIST_PURPOSE
        for path in files:
            spec = _file_specs()[path]
            assert spec.db == "no", path
            assert spec.network is False, path
            assert spec.llm is False, path
            assert spec.production_code is False, path

    def test_representative_contract_nodes_are_registered(self) -> None:
        """代表性 node 必须属于已登记类别/文件（第 5 项为 C25-13 漂移闸门）。"""
        known_files = {spec.path for spec in FILES}

        assert len(REPRESENTATIVE_CONTRACT_NODES) == 5
        for category, node_id in REPRESENTATIVE_CONTRACT_NODES:
            file_path = node_id.partition("::")[0]
            assert category in _REQUIRED_CATEGORIES, (category, node_id)
            assert file_path in known_files, node_id
            assert file_path in CATEGORIES[category], node_id

    def test_no_duplicate_regression_framework(self) -> None:
        """collector 结构名只能出现在本文件（禁止第二套回归框架）。"""
        structure_names = (
            "FILES",
            "CATEGORIES",
            "_REQUIRED_CATEGORIES",
            "_offline_suite",
            "_db_suite",
            "REPRESENTATIVE_CONTRACT_NODES",
            "OBSERVABILITY_HTTP_ALLOWLIST_PURPOSE",
            "EXPECTED_MATRIX_SCALE",
        )
        offenders: list[tuple[str, str]] = []
        for path in sorted((_REPO_ROOT / "tests").glob("test_*.py")):
            relative = f"tests/{path.name}"
            if relative == _SELF:
                continue
            tree = ast.parse(_source(relative))
            for node in tree.body:
                assignments = (
                    list(node.targets)
                    if isinstance(node, ast.Assign)
                    else [node.target]
                    if isinstance(node, ast.AnnAssign)
                    else []
                )
                for target in assignments:
                    if (
                        isinstance(target, ast.Name)
                        and target.id in structure_names
                    ):
                        offenders.append((relative, target.id))
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and node.name in structure_names:
                    offenders.append((relative, node.name))

        assert offenders == [], offenders

    def test_pure_offline_files_have_no_db_skip_guard(self) -> None:
        """纯离线文件**不得**因 DB 而 skip（其 DB 无关性由离线 suite 实跑证明）。

        注：静态 DB 无关性的**强**保证只施加于 Step 73 契约闸门
        （见 ``test_contract_gate_stays_db_and_network_free``）；其余离线文件允许
        静态引用 DB 层模块做列白名单 / 依赖方向的**审计断言**。

        判定基于**非 docstring** 字符串常量（docstring 中提到 RUN_DB_TESTS 不算门控）。
        """
        for spec in FILES:
            if spec.db != "no":
                continue
            assert "RUN_DB_TESTS" not in _code_string_constants(spec.path), (
                spec.path
            )


# ============================================================
# Matrix Contract 冻结（Phase 3.12 Step 80 · 离线 · 静态）
#
# 只冻结/验证**矩阵自身**（category / FileSpec / 注册表 / 代表性 node），
# 不新增业务断言、不复制 FILES / CATEGORIES、不建立第二套框架。
# ============================================================

class TestRegressionMatrixContract:
    def test_matrix_scale_is_frozen(self) -> None:
        """规模快照：categories / registered files / offline / DB 四项精确匹配。"""
        actual = {
            "categories": len(CATEGORIES),
            "registered_files": len(FILES),
            "offline_files": len(_offline_suite()),
            "db_files": len(_db_suite()),
        }

        assert actual == EXPECTED_MATRIX_SCALE, actual
        # 分区自洽：offline + DB = registered + partial（partial 双跑）
        partial = sum(1 for spec in FILES if spec.db == "partial")
        assert (
            actual["offline_files"] + actual["db_files"]
            == actual["registered_files"] + partial
        )

    def test_required_categories_match_actual_exactly(self) -> None:
        assert set(_REQUIRED_CATEGORIES) == set(CATEGORIES)
        assert len(_REQUIRED_CATEGORIES) == len(CATEGORIES) == (
            EXPECTED_MATRIX_SCALE["categories"]
        )

    def test_required_categories_have_no_duplicates(self) -> None:
        assert len(set(_REQUIRED_CATEGORIES)) == len(_REQUIRED_CATEGORIES)
        assert all(category.strip() for category in _REQUIRED_CATEGORIES)

    def test_registered_file_paths_are_unique(self) -> None:
        paths = [spec.path for spec in FILES]

        assert len(set(paths)) == len(paths), sorted(
            path for path in set(paths) if paths.count(path) > 1
        )
        assert all(path.startswith("tests/") for path in paths)

    def test_filespec_metadata_is_valid(self) -> None:
        """FileSpec：path/coverage 非空；db ∈ 合法取值；三个开关必须是 bool。

        （``db`` 的取值域是 ``no | partial | yes``；映射：
          db_required = (db != "no")。结构未被本阶段修改。）
        """
        for spec in FILES:
            assert spec.path and spec.path.strip(), spec
            assert spec.coverage and spec.coverage.strip(), spec
            assert spec.db in _DB_MODES, spec
            for flag in (spec.network, spec.llm, spec.production_code):
                assert isinstance(flag, bool), spec

    def test_observability_allowlist_category_metadata_is_all_false(self) -> None:
        """Step 79 语义保持不变：allowlist 契约族必须全离线。"""
        for path in CATEGORIES["OBSERVABILITY_HTTP_ALLOWLIST"]:
            spec = _file_specs()[path]

            assert spec.db == "no", path
            assert spec.network is False, path
            assert spec.llm is False, path
            assert spec.production_code is False, path

    def test_db_and_offline_suite_partition(self) -> None:
        """注册 → suite 的分区语义（只验证登记，不执行 DB）。"""
        offline, db = set(_offline_suite()), set(_db_suite())

        for spec in FILES:
            if spec.db == "no":
                assert spec.path in offline and spec.path not in db, spec.path
            elif spec.db == "yes":
                assert spec.path in db and spec.path not in offline, spec.path
            else:  # partial：两套 suite 都跑（离线段 + DB 段）
                assert spec.path in offline and spec.path in db, spec.path

    def test_category_to_file_completeness(self) -> None:
        """category → file：至少 1 个、全部已登记、无 dangling。"""
        registered = {spec.path for spec in FILES}

        for category, paths in CATEGORIES.items():
            assert paths, category
            assert len(set(paths)) == len(paths), category
            for path in paths:
                assert path in registered, (category, path)
                assert (_REPO_ROOT / path).is_file(), (category, path)

    def test_file_to_category_completeness(self) -> None:
        """file → category：无 orphan（每个注册文件至少属一个已登记类别）。"""
        mapping: dict[str, list[str]] = {}
        for category, paths in CATEGORIES.items():
            assert category in _REQUIRED_CATEGORIES, category
            for path in paths:
                mapping.setdefault(path, []).append(category)

        orphans = sorted({spec.path for spec in FILES} - set(mapping))
        assert orphans == [], orphans
        for path, categories in mapping.items():
            assert categories, path

    def test_no_self_registration(self) -> None:
        """collector 自身不得作为 Matrix entry / category 成员 / suite 成员。"""
        assert _SELF not in {spec.path for spec in FILES}
        assert _SELF not in {path for paths in CATEGORIES.values() for path in paths}
        assert _SELF not in _offline_suite() and _SELF not in _db_suite()
        assert not (_REPO_ROOT / _SELF).name.startswith("test_assistant_regression")

    def test_representative_nodes_are_unique_and_registered(self) -> None:
        registered = {spec.path for spec in FILES}
        node_ids = [node_id for _, node_id in REPRESENTATIVE_CONTRACT_NODES]

        assert len(set(node_ids)) == len(node_ids), sorted(node_ids)
        for category, node_id in REPRESENTATIVE_CONTRACT_NODES:
            file_path = node_id.partition("::")[0]
            assert category in _REQUIRED_CATEGORIES, (category, node_id)
            assert file_path in registered, node_id
            assert file_path in CATEGORIES[category], node_id
            assert node_id.partition("::")[1], node_id   # 必须带 class/function

    def test_representative_nodes_are_collectable(self) -> None:
        """node id 必须真实可被 ``--collect-only`` 收集（**不执行**）。"""
        files = tuple(
            dict.fromkeys(
                node_id.partition("::")[0]
                for _, node_id in REPRESENTATIVE_CONTRACT_NODES
            )
        )
        result = _run_pytest(files, with_db=False, collect_only=True)

        assert result.returncode == 0, result.stdout[-1500:]
        for _, node_id in REPRESENTATIVE_CONTRACT_NODES:
            assert node_id in result.stdout, node_id


# ============================================================
# Matrix 可执行性审计（Phase 3.12 Step 81 · `--collect-only`）
#
# 只收集，**不执行**测试；不开启 RUN_DB_TESTS；结果仅作审计，不写入 FileSpec。
# ============================================================

class TestRegressionMatrixCollectability:
    def test_collectability_report_covers_every_registered_file(self) -> None:
        reports = _collectability_report()

        assert [report.path for report in reports] == [spec.path for spec in FILES]
        assert len(reports) == EXPECTED_MATRIX_SCALE["registered_files"] == 28

    def test_all_registered_files_are_collectable(self) -> None:
        reports = _collectability_report()
        failures = _failures(reports)

        assert failures == [], failures
        assert len(reports) == 28
        assert sum(report.collected for report in reports) > 0

    def test_offline_registered_files_are_collectable(self) -> None:
        offline = set(_offline_suite())
        reports = tuple(
            report
            for report in _collectability_report()
            if report.path in offline
        )

        assert len(reports) == EXPECTED_MATRIX_SCALE["offline_files"] == 18
        assert _failures(reports) == [], _failures(reports)

    def test_db_registered_files_are_collectable(self) -> None:
        db_files = set(_db_suite())
        reports = tuple(
            report
            for report in _collectability_report()
            if report.path in db_files
        )

        assert len(reports) == EXPECTED_MATRIX_SCALE["db_files"] == 15
        assert _failures(reports) == [], _failures(reports)

    def test_collectability_does_not_require_db_gate(self) -> None:
        """未开启 RUN_DB_TESTS 时，DB 门控文件同样可被收集（不启动 PostgreSQL）。"""
        assert "RUN_DB_TESTS" not in _pytest_env(with_db=False)
        assert _pytest_env(with_db=True)["RUN_DB_TESTS"] == "1"

        db_files = set(_db_suite())
        assert db_files, "DB suite 不应为空"
        unreachable = [
            report.path
            for report in _collectability_report()
            if report.path in db_files and report.collected <= 0
        ]
        assert unreachable == [], unreachable


# ============================================================
# 执行注册覆盖闭环（Phase 3.12 Step 82 · 离线 · 纯拓扑）
#
# 只审计 FILES → CATEGORIES → suite 的注册拓扑，**不执行**任何 suite。
# coverage 语义 = registration execution coverage（按**文件**计数），
# **不是** pass rate，也不统计 collected nodes。
# ============================================================

class TestRegressionMatrixExecutionCoverage:
    def test_all_registered_files_are_in_at_least_one_category(self) -> None:
        mapping = _file_categories()
        registered = {spec.path for spec in FILES}

        orphans = sorted(registered - set(mapping))
        assert orphans == [], orphans
        assert len(registered) == len(mapping) == (
            EXPECTED_MATRIX_SCALE["registered_files"]
        )

    def test_all_offline_suite_files_are_registered(self) -> None:
        registered = {spec.path for spec in FILES}
        dangling = sorted(set(_suite_topology()["offline"]) - registered)

        assert dangling == [], dangling

    def test_all_db_suite_files_are_registered(self) -> None:
        registered = {spec.path for spec in FILES}
        dangling = sorted(set(_suite_topology()["db"]) - registered)

        assert dangling == [], dangling

    def test_all_registered_files_are_in_a_suite(self) -> None:
        """注册 → suite 闭环：uncovered = 0 ⇒ suite-covered 28 / 28 = 100%（按文件）。"""
        topology = _suite_topology()
        covered = set(topology["offline"]) | set(topology["db"])

        assert topology["uncovered"] == (), topology["uncovered"]
        assert covered == {spec.path for spec in FILES}
        assert len(covered) == EXPECTED_MATRIX_SCALE["registered_files"] == 28

    def test_category_files_are_suite_covered(self) -> None:
        coverage = _category_suite_coverage()

        assert set(coverage) == set(_REQUIRED_CATEGORIES)
        for category, buckets in coverage.items():
            assert CATEGORIES[category], category
            assert buckets["missing"] == (), (category, buckets["missing"])
            assert buckets["offline"] or buckets["db"], (category, buckets)

    def test_observability_allowlist_category_is_suite_covered(self) -> None:
        """Step 79 语义保持不变：4 files · db=no · 全部落在 offline suite。"""
        files = CATEGORIES["OBSERVABILITY_HTTP_ALLOWLIST"]
        buckets = _category_suite_coverage()["OBSERVABILITY_HTTP_ALLOWLIST"]
        offline = set(_offline_suite())
        db = set(_db_suite())

        assert len(files) == 4
        assert set(buckets["offline"]) == set(files)
        assert buckets["db"] == ()
        assert buckets["missing"] == ()
        for path in files:
            spec = _file_specs()[path]
            assert spec.db == "no", path
            assert path in offline and path not in db, path

    def test_suite_partition_matches_existing_db_semantics(self) -> None:
        """只审计**现有**语义（不重新定义 partial）：no→offline；yes→DB；partial→两者。"""
        offline, db = set(_offline_suite()), set(_db_suite())
        partial = {spec.path for spec in FILES if spec.db == "partial"}

        for spec in FILES:
            if spec.db == "no":
                assert spec.path in offline and spec.path not in db, spec.path
            elif spec.db == "yes":
                assert spec.path in db and spec.path not in offline, spec.path
            else:
                assert spec.path in offline and spec.path in db, spec.path

        # overlap 显式登记：offline + DB ≠ registered（partial 双跑）
        assert (offline & db) == partial
        assert len(offline) + len(db) == (
            EXPECTED_MATRIX_SCALE["registered_files"] + len(partial)
        ) == 28 + 5


# ============================================================
# 入口自身卫生（离线 · 静态）—— 确保"不复制 E2E 逻辑"
# ============================================================

class TestEntryHygiene:
    def test_entry_does_not_reimplement_e2e(self) -> None:
        """入口不得自建 HTTP / 异步执行 / 无条件写库（只做收口）。"""
        tree = ast.parse(_source(_SELF))

        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        for forbidden in ("TestClient", "run_async", "asyncio"):
            assert forbidden not in identifiers, forbidden

        attributes = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        assert "post" not in attributes and "put" not in attributes

        # 所有 SQL 字面量必须是 **定向** 语句（WHERE + LIKE :p）
        sql_literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "FROM " in node.value.upper()
            and any(
                keyword in node.value.upper()
                for keyword in ("INSERT", "DELETE", "UPDATE")
            )
        ]
        assert sql_literals, "AST 未找到清理语句（审计失效）"
        for statement in sql_literals:
            assert "WHERE" in statement.upper(), statement
            assert "LIKE :p" in statement, statement
        # 命名空间前缀必须存在（step74- 定向清理）
        assert _STEP74_PREFIX == "step74-"

    def test_entry_db_imports_are_function_local_only(self) -> None:
        """模块顶层不得 import DB（offline 模式下零 DB 加载）。"""
        module_level = _imported_modules(_SELF, module_level_only=True)

        for module in module_level:
            assert not module.startswith("backend.app.db"), module
            assert not module.startswith("sqlalchemy"), module
        # DB 依赖确实存在于文件内（延迟 import），而不是被遗漏
        assert "backend.app.db.session" in _imported_modules(
            _SELF, module_level_only=False
        )

    def test_entry_imports_no_network_client(self) -> None:
        modules = _imported_modules(_SELF, module_level_only=False)

        for forbidden in ("httpx", "requests", "aiohttp", "openai", "socket"):
            assert forbidden not in modules, forbidden

    def test_contract_gate_stays_db_and_network_free(self) -> None:
        """Step 73 的离线闸门必须保持 DB = 0 / network = 0（Step 74 §七）。"""
        modules = _imported_modules(_CONTRACT_GATE, module_level_only=False)

        for module in modules:
            for prefix in (
                "backend.app.db",
                "sqlalchemy",
                "psycopg",
                "redis",
                "httpx",
                "requests",
                "aiohttp",
            ):
                assert not module.startswith(prefix), (module, prefix)


# ============================================================
# Suite 执行闸门（offline 恒跑；DB 门控按 RUN_DB_TESTS）
# ============================================================

class TestRegressionSuite:
    def test_offline_gate_suite_is_green(self) -> None:
        """离线 suite（RUN_DB_TESTS 已清空）必须全绿。"""
        result = _run_pytest(_offline_suite(), with_db=False)

        assert result.returncode == 0, (
            "离线 Trace / Timeline 回归失败\n"
            f"paths={_offline_suite()}\n"
            f"stdout tail:\n{result.stdout[-2000:]}"
        )

    @pytest.mark.skipif(
        not _db_mode(), reason="需要 RUN_DB_TESTS=1（DB-gated 模式）"
    )
    def test_db_gate_suite_is_green(self) -> None:
        """DB suite（真实测试 PostgreSQL）必须全绿。"""
        result = _run_pytest(_db_suite(), with_db=True)

        assert result.returncode == 0, (
            "DB-gated Trace / Timeline 回归失败\n"
            f"paths={_db_suite()}\n"
            f"stdout tail:\n{result.stdout[-2000:]}"
        )

    @pytest.mark.skipif(
        not _db_mode(), reason="需要 RUN_DB_TESTS=1（DB-gated 模式）"
    )
    def test_step74_residue_namespace_is_empty(self) -> None:
        """Step 74 命名空间（``step74-``）不得有残留；并做定向清理。"""
        counts = _step74_residue()

        if not counts:
            pytest.skip("DATABASE_URL 未配置（DB 功能禁用）")
        assert set(counts.values()) == {0}, counts
        _cleanup_step74_rows()
        assert set(_step74_residue().values()) == {0}


__all__ = [
    "CATEGORIES",
    "EXPECTED_MATRIX_SCALE",
    "FILES",
    "OBSERVABILITY_HTTP_ALLOWLIST_PURPOSE",
    "REPRESENTATIVE_CONTRACT_NODES",
    "CollectReport",
    "FileSpec",
    "TestEntryHygiene",
    "TestRegressionMatrixCollectability",
    "TestRegressionMatrixContract",
    "TestRegressionMatrixExecutionCoverage",
    "TestRegressionSuite",
    "TestRegistry",
]
