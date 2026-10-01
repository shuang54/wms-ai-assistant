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
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SELF = "tests/test_assistant_trace_timeline_regression.py"

#: Step 73 的离线契约闸门（**必须**始终包含在离线 suite 内）。
_CONTRACT_GATE = "tests/test_assistant_trace_timeline_contract.py"

#: 嵌套 suite 的墙钟上限（避免 CI 挂死）。
_SUITE_TIMEOUT_SECONDS = 1800

#: Step 74 专属残留命名空间（定向清理；禁止 TRUNCATE / DELETE ALL）。
_STEP74_PREFIX = "step74-"

#: 定向清理模板（**单条 SQL 常量**：必须含 WHERE + 前缀 LIKE；表名 / 列名来自硬编码元组）。
_SQL_DELETE_STEP74 = "DELETE FROM {table} WHERE {column} LIKE :p"

#: 残留计数模板（只读）。
_SQL_COUNT_STEP74 = (
    "SELECT COUNT(*) FROM {table} WHERE {column} LIKE :p"
)

#: 必须覆盖的 12 个回归类别（Step 74 §五）。
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
)

DbMode = Literal["no", "partial", "yes"]


@dataclass(frozen=True)
class FileSpec:
    """一个既有测试文件在回归入口中的定位（**只登记，不改写**）。"""

    path: str
    db: DbMode
    coverage: str


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
}


def _source(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


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


def _run_pytest(
    paths: tuple[str, ...], *, with_db: bool
) -> subprocess.CompletedProcess[str]:
    """嵌套执行既有 suite（**不复制**任何测试逻辑）。"""
    env = dict(os.environ)
    if with_db:
        env["RUN_DB_TESTS"] = "1"
    else:
        env.pop("RUN_DB_TESTS", None)   # 离线模式：强制无 DB 门控

    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:randomly",
            "--tb=line",
            *paths,
        ],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=_SUITE_TIMEOUT_SECONDS,
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

    def test_twelve_required_categories_are_covered(self) -> None:
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

    def test_pure_offline_files_have_no_db_skip_guard(self) -> None:
        """纯离线文件**不得**因 DB 而 skip（其 DB 无关性由离线 suite 实跑证明）。

        注：静态 DB 无关性的**强**保证只施加于 Step 73 契约闸门
        （见 ``test_contract_gate_stays_db_and_network_free``）；其余离线文件允许
        静态引用 DB 层模块做列白名单 / 依赖方向的**审计断言**。
        """
        for spec in FILES:
            if spec.db != "no":
                continue
            assert "RUN_DB_TESTS" not in _source(spec.path), spec.path


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
    "FILES",
    "FileSpec",
    "TestEntryHygiene",
    "TestRegressionSuite",
    "TestRegistry",
]
