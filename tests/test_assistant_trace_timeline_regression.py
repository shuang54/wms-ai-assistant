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
import inspect
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping

import pytest

# Step 95：生产 CI Adapter（**唯一**实现）+ 唯一退出码映射；测试侧复用，不复制。
from backend.app.services.matrix_ci_adapter import (  # noqa: E402
    CI_ADAPTER_EXIT_CODES as _PROD_CI_ADAPTER_EXIT_CODES,
    adapt_gate_result_to_exit_code,
)

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

#: Step 83：pytest terminal summary 的 outcome 计数（值 → 需要的计数桶）。
#: 只映射 4 个必需桶；``warnings`` 不计入（非 outcome），xfail/xpass/deselected
#: 由 ``_unexpected_outcome_tokens`` 单独暴露，**不塞进 total**。
_OUTCOME_BUCKETS: dict[str, str] = {
    "passed": "passed",
    "failed": "failed",
    "error": "errors",
    "errors": "errors",
    "skipped": "skipped",
}

#: 任何 "N <outcome>" 计数 token（用于发现未建模的 outcome 类别）。
_OUTCOME_TOKEN_PATTERN = re.compile(
    r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?)"
)

#: terminal summary 的耗时（"in 9.17s"）；仅用于信息字段，不参与 equality。
_DURATION_PATTERN = re.compile(r"\bin\s+(\d+(?:\.\d+)?)s\b")

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

#: **Offline Execution Summary Contract**（Phase 3.12 Step 84 冻结）。
#: 只描述**语义**（PASS / FAIL / SKIP / arithmetic / exit_code / immutability），
#: **不绑定任何具体用例数量** —— 数量属于 snapshot（见下），不属 Contract。
OFFLINE_EXECUTION_SUMMARY_CONTRACT: dict[str, str] = {
    "pass": "exit_code == 0 AND failed == 0 AND errors == 0",
    "fail": "exit_code != 0 OR failed > 0 OR errors > 0",
    "skip": "skipped >= 0（仅 outcome count；不单独导致 FAIL）",
    "arithmetic": "total == passed + skipped + failed + errors",
    "exit_code": "0 = 成功；非 0（含信号导致的负值）= FAIL",
    "immutability": "frozen=True（禁止 summary.passed = ...）",
    "duration": "informational only（compare=False；不进入 Contract / baseline / equality）",
    "extra_outcomes": "xfail / xpass / deselected / warnings 不并入 total",
}

#: **Step 83 离线执行快照**（snapshot ≠ contract：用例数增长不构成 Contract 回归；
#: offline suite 规模变化时应**显式更新本快照**，而不是修改 Contract）。
OFFLINE_EXECUTION_SUMMARY_SNAPSHOT: dict[str, int | str] = {
    "total": 375,
    "passed": 356,
    "skipped": 19,
    "failed": 0,
    "errors": 0,
    "exit_code": 0,
    "status": "PASS",
}

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

#: **Node-hosted Contract Category**（Phase 3.12 Step 86）。
#:
#: 该契约（Offline Execution Summary + Snapshot Drift）由 collector **自身**的测试类承载，
#: 因此**不**放进 ``CATEGORIES``（file-mapped）：
#: ``CATEGORIES`` 的每个条目必须指向 **FILES 已注册**的文件（Step 80 §七 / Step 82），
#: 而把 collector 自身登记为 category file 会同时违反 "no self registration"（Step 80 §九）。
#: 故以**并行注册表**表达：保留 13 个 file-hosted category 不变，新增 1 个 node-hosted category。
NODE_HOSTED_CONTRACT_CATEGORIES: dict[str, dict[str, object]] = {
    "OFFLINE_EXECUTION_CONTRACT": {
        "host": _SELF,
        "classes": (
            "TestOfflineRegressionExecutionSummary",
            "TestOfflineRegressionExecutionSummaryContract",
            "TestOfflineRegressionSnapshotDrift",
        ),
        "scope": (
            "OFFLINE_EXECUTION_SUMMARY_CONTRACT / "
            "OFFLINE_EXECUTION_SUMMARY_SNAPSHOT / classify_snapshot_drift()"
        ),
        "db_required": False,
        "network_required": False,
        "llm_required": False,
        "production_code_required": False,
    },
}

#: Node-hosted category 的代表性 node（Step 86 §八；真实存在、可 collect、属当前 Matrix）。
NODE_HOSTED_REPRESENTATIVE_NODES: tuple[tuple[str, str], ...] = (
    (
        "OFFLINE_EXECUTION_CONTRACT",
        f"{_SELF}::TestOfflineRegressionSnapshotDrift"
        "::test_snapshot_is_immutable_during_drift_audit",
    ),
    (
        "OFFLINE_EXECUTION_CONTRACT",
        f"{_SELF}::TestOfflineRegressionExecutionSummaryContract"
        "::test_current_snapshot_matches_recorded_baseline",
    ),
)

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


def _module_level_assignments(name: str) -> tuple[str, ...]:
    """模块级 ``name = ...`` 的**声明位置**（本 collector 内；声明唯一性校验用）。"""
    tree = ast.parse(_source(_SELF))
    declared = False
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(
            isinstance(target, ast.Name) and target.id == name
            for target in targets
        ):
            declared = True
    return (_SELF,) if declared else ()


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
class MatrixExecutionSummary:
    """整张 Regression Matrix 的**实际执行**汇总（Phase 3.12 Step 89）。

    * ``offline`` = offline suite 实际执行结果（复用 ``RegressionExecutionSummary``）；
    * ``db`` = DB suite 实际执行结果；环境不满足 DB 条件时为 ``None``（并记录原因，
      **不伪造 PASS**）；
    * ``db_skip_reason`` = 仅在 ``db is None`` 时非空。

    不含 Node-hosted Contract（它只是 Contract Audit，不参与 execution count）。
    """

    offline: RegressionExecutionSummary
    db: RegressionExecutionSummary | None = None
    db_skip_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.offline, RegressionExecutionSummary):
            raise ValueError("offline 必须是 RegressionExecutionSummary")
        if self.db is not None:
            if not isinstance(self.db, RegressionExecutionSummary):
                raise ValueError("db 必须是 RegressionExecutionSummary | None")
            if self.db_skip_reason is not None:
                raise ValueError("db 已执行时不得再记录 db_skip_reason")
        elif not self.db_skip_reason:
            raise ValueError("db 未执行时必须记录 db_skip_reason（不得静默）")

    @property
    def executed_parts(self) -> tuple[RegressionExecutionSummary, ...]:
        return tuple(
            part for part in (self.offline, self.db) if part is not None
        )

    @property
    def total(self) -> int:
        """已执行部分的用例总数（Node-hosted Contract 不计入）。"""
        return sum(part.total for part in self.executed_parts)

    @property
    def status(self) -> str:
        return (
            "PASS"
            if all(part.is_pass for part in self.executed_parts)
            else "FAIL"
        )

    @property
    def is_pass(self) -> bool:
        return self.status == "PASS"


#: Matrix Execution Baseline 的 Drift 分类（Step 90；**不修改** Step 85 的 4 类 Drift）。
_MATRIX_BASELINE_DRIFT_TYPES: tuple[str, ...] = (
    "NO_BASELINE_DRIFT",
    "OFFLINE_EXECUTION_DRIFT",
    "DB_EXECUTION_DRIFT",
    "MATRIX_TOTAL_DRIFT",
    "MATRIX_STATUS_DRIFT",
    "DB_RESIDUE_DRIFT",
)


@dataclass(frozen=True)
class MatrixExecutionBaseline:
    """Phase 3.12 **Matrix Execution Baseline**（Step 90 冻结）。

    第三层基线（与 Step 84 Offline Snapshot / Step 80 Matrix Scale **分离**）：

        Registration Matrix（Scale：注册结构）
                ↓
        Actual Execution（Step 89 MatrixExecutionSummary）
                ↓
        Frozen Matrix Execution Baseline（本 DTO）

    边界：

        * ``matrix_total`` **必须** = ``offline.total + db.total``（不是 passed 之和）；
        * ``matrix_status`` 复用 ``RegressionExecutionSummary.status``（**不**新建第三套 classifier）；
        * ``db_residue`` 只是**整数**（不含连接 / Session / Query 等运行时对象）；
        * **不含** ``duration``（informational，compare=False）；
        * 不含 Node-hosted Contract（它只是 Contract Audit，不进入执行计数）。
    """

    offline: RegressionExecutionSummary
    db: RegressionExecutionSummary
    matrix_total: int
    matrix_status: str
    db_residue: int

    def __post_init__(self) -> None:
        for name in ("offline", "db"):
            if not isinstance(getattr(self, name), RegressionExecutionSummary):
                raise ValueError(f"{name} 必须是 RegressionExecutionSummary")
        if not isinstance(self.matrix_total, int) or isinstance(
            self.matrix_total, bool
        ):
            raise ValueError("matrix_total 必须是 int")
        if self.matrix_total != self.offline.total + self.db.total:
            raise ValueError(
                "matrix_total 必须等于 offline.total + db.total"
                f"（{self.matrix_total} != {self.offline.total}"
                f" + {self.db.total}）"
            )
        expected_status = (
            "PASS"
            if self.offline.is_pass and self.db.is_pass
            else "FAIL"
        )
        if self.matrix_status != expected_status:
            raise ValueError(
                f"matrix_status 必须由 offline/db status 推导（{expected_status}）"
            )
        if not isinstance(self.db_residue, int) or isinstance(
            self.db_residue, bool
        ):
            raise ValueError("db_residue 必须是 int")
        if self.db_residue < 0:
            raise ValueError("db_residue 不能为负")

    @property
    def parts(self) -> tuple[RegressionExecutionSummary, ...]:
        return (self.offline, self.db)


@dataclass(frozen=True)
class MatrixBaselineGateResult:
    """Matrix Baseline Gate 结果（Phase 3.12 Step 91；immutable）。

    只承载：执行摘要（current / baseline）· drift 类型 · status。
    **不含**任何凭据 / prompt / SQL / RAG chunk / tool args / raw response。
    """

    status: str
    drifts: tuple[str, ...]
    current: MatrixExecutionBaseline
    baseline: MatrixExecutionBaseline

    def __post_init__(self) -> None:
        if self.status not in {"PASS", "DRIFT"}:
            raise ValueError(f"status 必须是 PASS | DRIFT（当前: {self.status!r}）")
        for name in ("current", "baseline"):
            if not isinstance(getattr(self, name), MatrixExecutionBaseline):
                raise ValueError(f"{name} 必须是 MatrixExecutionBaseline")
        if not isinstance(self.drifts, tuple) or not self.drifts:
            raise ValueError("drifts 必须是非空 tuple")
        for drift in self.drifts:
            if drift not in _MATRIX_BASELINE_DRIFT_TYPES:
                raise ValueError(f"未知 drift 类型: {drift!r}")
        expected = "PASS" if self.drifts == ("NO_BASELINE_DRIFT",) else "DRIFT"
        if self.status != expected:
            raise ValueError(
                f"status 必须由 drifts 推导（期望 {expected}，当前 {self.status}）"
            )

    @property
    def is_pass(self) -> bool:
        return self.status == "PASS"


def evaluate_matrix_baseline_gate(
    current: MatrixExecutionBaseline,
    baseline: MatrixExecutionBaseline,
) -> MatrixBaselineGateResult:
    """Frozen Regression Gate（Step 91）：current vs baseline → PASS / DRIFT。

    规则（无"基本一致" / 无容差 / 无自动更新）：

        PASS ⇔ offline == baseline.offline ∧ db == baseline.db
                ∧ matrix_total 相同 ∧ matrix_status 相同
                ∧ db_residue 相同 ∧ **current.db_residue == 0**

        duration_seconds **不参与**（RegressionExecutionSummary compare=False）。

    复用 Step 90 的 ``compare_matrix_execution_baseline``（不重复 drift 逻辑）；
    额外保证 ``current.db_residue != 0`` 一定导致 DRIFT。
    """
    drifts = [
        drift
        for drift in compare_matrix_execution_baseline(baseline, current)
        if drift != "NO_BASELINE_DRIFT"
    ]
    if current.db_residue != 0 and "DB_RESIDUE_DRIFT" not in drifts:
        # 安全边界：即使 current == baseline，非零 residue 也必须 DRIFT
        # （此处不再保留 NO_BASELINE_DRIFT —— drifts 只表达"为何不是 PASS"；且不重复登记）
        drifts.append("DB_RESIDUE_DRIFT")

    resolved = tuple(drifts) if drifts else ("NO_BASELINE_DRIFT",)
    return MatrixBaselineGateResult(
        status="PASS" if resolved == ("NO_BASELINE_DRIFT",) else "DRIFT",
        drifts=resolved,
        current=current,
        baseline=baseline,
    )


def compare_matrix_execution_baseline(
    baseline: MatrixExecutionBaseline,
    current: MatrixExecutionBaseline,
) -> tuple[str, ...]:
    """比较 Baseline 与当前执行结果（纯函数；分类属于 ``_MATRIX_BASELINE_DRIFT_TYPES``）。

    只比较：offline · db · matrix_total · matrix_status · db_residue。
    **不**扩展 Step 85 的 snapshot classifier。
    """
    drift: list[str] = []

    if current.offline != baseline.offline:
        drift.append("OFFLINE_EXECUTION_DRIFT")
    if current.db != baseline.db:
        drift.append("DB_EXECUTION_DRIFT")
    if current.matrix_total != baseline.matrix_total:
        drift.append("MATRIX_TOTAL_DRIFT")
    if current.matrix_status != baseline.matrix_status:
        drift.append("MATRIX_STATUS_DRIFT")
    if current.db_residue != baseline.db_residue:
        drift.append("DB_RESIDUE_DRIFT")

    return tuple(drift) if drift else ("NO_BASELINE_DRIFT",)


@dataclass(frozen=True)
class RegressionExecutionSummary:
    """Offline Regression Matrix 执行结果摘要（Phase 3.12 Step 83）。

    语义（§六）：

        PASS : ``exit_code == 0`` ∧ ``failed == 0`` ∧ ``errors == 0``
        FAIL : ``failed > 0`` ∨ ``errors > 0`` ∨ ``exit_code != 0``
        SKIP : ``skipped`` 只是统计量，**不等于**失败

    只表达 Matrix 执行结果；**不含** DB 行数 / token / 网络调用等运行时指标。
    ``duration_seconds`` 仅信息字段（``compare=False``，不参与 equality）。
    """

    total: int
    passed: int
    skipped: int
    failed: int
    errors: int
    exit_code: int
    duration_seconds: float | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        for name in ("total", "passed", "skipped", "failed", "errors", "exit_code"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} 必须是 int（当前: {value!r}）")
        for name in ("total", "passed", "skipped", "failed", "errors"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 不能为负")
        if self.total != self.passed + self.skipped + self.failed + self.errors:
            raise ValueError(
                "total 必须等于 passed + skipped + failed + errors"
                f"（{self.total} != {self.passed}+{self.skipped}"
                f"+{self.failed}+{self.errors}）"
            )
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("duration_seconds 不能为负")

    @property
    def status(self) -> str:
        if self.exit_code != 0 or self.failed > 0 or self.errors > 0:
            return "FAIL"
        return "PASS"

    @property
    def is_pass(self) -> bool:
        return self.status == "PASS"


#: **冻结的 Matrix Execution Baseline**（来源于 Step 89 **实际执行**结果；
#: duration 有意不记录 —— informational only）。
MATRIX_EXECUTION_BASELINE: MatrixExecutionBaseline = MatrixExecutionBaseline(
    offline=RegressionExecutionSummary(
        total=375, passed=356, skipped=19, failed=0, errors=0, exit_code=0
    ),
    db=RegressionExecutionSummary(
        total=180, passed=180, skipped=0, failed=0, errors=0, exit_code=0
    ),
    matrix_total=555,
    matrix_status="PASS",
    db_residue=0,
)


def _parse_pytest_summary(
    stdout: str,
    *,
    exit_code: int,
    duration_seconds: float | None = None,
) -> RegressionExecutionSummary:
    """解析 pytest terminal summary（"N passed, M skipped in Xs"）。

    只取**已建模**的 4 个 outcome 桶；``warnings`` 不计入；
    xfail/xpass/deselected 由 ``_unexpected_outcome_tokens`` 单独暴露。
    """
    counts = {"passed": 0, "skipped": 0, "failed": 0, "errors": 0}
    for number, outcome in _OUTCOME_TOKEN_PATTERN.findall(stdout):
        bucket = _OUTCOME_BUCKETS.get(outcome)
        if bucket is not None:
            counts[bucket] += int(number)

    total = counts["passed"] + counts["skipped"] + counts["failed"] + counts["errors"]
    return RegressionExecutionSummary(
        total=total,
        passed=counts["passed"],
        skipped=counts["skipped"],
        failed=counts["failed"],
        errors=counts["errors"],
        exit_code=exit_code,
        duration_seconds=duration_seconds,
    )


def _unexpected_outcome_tokens(stdout: str) -> tuple[str, ...]:
    """未建模的 outcome token（xfailed / xpassed / deselected …）；warnings 不算。"""
    unexpected: list[str] = []
    for _, outcome in _OUTCOME_TOKEN_PATTERN.findall(stdout):
        if outcome in _OUTCOME_BUCKETS or outcome in {"warning", "warnings"}:
            continue
        if outcome not in unexpected:
            unexpected.append(outcome)
    return tuple(unexpected)


def _parse_duration(stdout: str) -> float | None:
    match = _DURATION_PATTERN.search(stdout)
    return float(match.group(1)) if match else None


@lru_cache(maxsize=1)
def _offline_suite_execution() -> subprocess.CompletedProcess[str]:
    """执行一次 offline suite（会话内缓存；供 gate 测试与 summary 共用）。"""
    return _run_pytest(_offline_suite(), with_db=False)


@lru_cache(maxsize=1)
def _offline_execution_summary() -> RegressionExecutionSummary:
    """真实 offline suite 执行 → 结构化 summary（in-memory；无持久化）。"""
    result = _offline_suite_execution()
    return _parse_pytest_summary(
        result.stdout,
        exit_code=result.returncode,
        duration_seconds=_parse_duration(result.stdout),
    )


#: 四张观测表（与各表 request 关联列）—— 只读残留计数使用。
_RESIDUE_TABLES: tuple[tuple[str, str], ...] = (
    ("llm_usage_record", "assistant_request_id"),
    ("tool_execution_record", "request_id"),
    ("rag_execution_record", "request_id"),
    ("assistant_outcome_record", "assistant_request_id"),
)


@lru_cache(maxsize=1)
def _db_residue_total() -> int | None:
    """四张观测表总行数（只读）；DB 不可用 → ``None``（不猜测）。

    函数内延迟 import（模块顶层保持无 DB 依赖）；不保存任何连接 / Session。
    """
    available, _ = _db_execution_available()
    if not available:
        return None

    from sqlalchemy import text

    from backend.app.db.session import get_engine

    engine = get_engine()
    if engine is None:                      # pragma: no cover - 与 available 一致
        return None
    with engine.connect() as conn:
        return sum(
            int(
                conn.execute(
                    text(f"SELECT COUNT(*) FROM ai_ops.{table}")
                ).scalar_one()
            )
            for table, _ in _RESIDUE_TABLES
        )


def _current_matrix_baseline() -> MatrixExecutionBaseline | None:
    """当前实际执行对应的 Baseline 视图（复用 Step 89 summary；DB 不可用 → None）。"""
    matrix = _matrix_execution_summary()
    residue = _db_residue_total()
    if matrix.db is None or residue is None:
        return None
    return MatrixExecutionBaseline(
        offline=matrix.offline,
        db=matrix.db,
        matrix_total=matrix.total,
        matrix_status=matrix.status,
        db_residue=residue,
    )


@lru_cache(maxsize=1)
def _db_execution_available() -> tuple[bool, str]:
    """DB suite 是否可安全执行（只读探测；函数内延迟 import，避免模块级 DB 依赖）。"""
    try:
        from backend.app.db.session import get_engine
    except Exception as exc:  # noqa: BLE001 —— 导入失败同样视为不可用
        return False, f"DB 会话模块不可导入：{type(exc).__name__}"

    try:
        engine = get_engine()
    except Exception as exc:  # noqa: BLE001
        return False, f"get_engine() 失败：{type(exc).__name__}"
    if engine is None:
        return False, "DATABASE_URL 未配置（DB 功能禁用）"
    return True, ""


@lru_cache(maxsize=1)
def _db_suite_execution() -> subprocess.CompletedProcess[str]:
    """执行一次 DB suite（``RUN_DB_TESTS=1``；会话内缓存，只跑一次）。"""
    return _run_pytest(_db_suite(), with_db=True)


@lru_cache(maxsize=1)
def _db_execution_summary() -> RegressionExecutionSummary | None:
    """DB suite 实际执行结果；环境不满足 → ``None``（不伪造 PASS）。"""
    available, _ = _db_execution_available()
    if not available:
        return None
    result = _db_suite_execution()
    return _parse_pytest_summary(
        result.stdout,
        exit_code=result.returncode,
        duration_seconds=_parse_duration(result.stdout),
    )


@lru_cache(maxsize=1)
def _matrix_execution_summary() -> MatrixExecutionSummary:
    """整张 Matrix 的实际执行汇总（offline + DB；Node-hosted 不计入）。"""
    available, reason = _db_execution_available()
    db_summary = _db_execution_summary() if available else None
    if db_summary is None:
        return MatrixExecutionSummary(
            offline=_offline_execution_summary(),
            db_skip_reason=reason or "DB suite 未执行（环境不满足）",
        )
    return MatrixExecutionSummary(
        offline=_offline_execution_summary(), db=db_summary
    )


@lru_cache(maxsize=1)
def _self_collection_result() -> subprocess.CompletedProcess[str]:
    """本 collector 自身的 ``--collect-only`` 结果（只收集、不执行；会话内缓存）。"""
    return _run_pytest((_SELF,), with_db=False, collect_only=True, timeout=60)


@lru_cache(maxsize=1)
def _self_collected_nodes() -> tuple[str, ...]:
    """本 collector 收集到的 node id 集合（Contract Audit 覆盖面证据）。"""
    return tuple(
        sorted(
            {
                line.strip()
                for line in _self_collection_result().stdout.splitlines()
                if "::" in line
            }
        )
    )


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
            "OFFLINE_EXECUTION_SUMMARY_CONTRACT",
            "OFFLINE_EXECUTION_SUMMARY_SNAPSHOT",
            "NODE_HOSTED_CONTRACT_CATEGORIES",
            "NODE_HOSTED_REPRESENTATIVE_NODES",
            "MATRIX_EXECUTION_BASELINE",
            "CI_ADAPTER_EXIT_CODES",
            "CI_ADAPTER_IMPLEMENTATION",
            "CI_GATE_CLI",
        )
    # 备注：Step 91 的 Gate 复用既有 DTO，未引入新的 collector 结构名。
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

# ============================================================
# Node-hosted Contract Registration 冻结（Phase 3.12 Step 87 · 离线 · 静态）
#
# Node-hosted（NODE_HOSTED_CONTRACT_CATEGORIES → NODE_HOSTED_REPRESENTATIVE_NODES
# → collect-only 校验）与 File-hosted（FILES → CATEGORIES → suites）**严格分离**。
# ============================================================

class TestNodeHostedContractRegistration:
    def test_node_hosted_category_is_unique(self) -> None:
        assert set(NODE_HOSTED_CONTRACT_CATEGORIES) == {
            "OFFLINE_EXECUTION_CONTRACT"
        }
        # 声明唯一（模块级仅一处）
        assert _module_level_assignments("NODE_HOSTED_CONTRACT_CATEGORIES") == (
            _SELF,
        )

    def test_node_hosted_category_has_exactly_one_host(self) -> None:
        hosts = {entry["host"] for entry in NODE_HOSTED_CONTRACT_CATEGORIES.values()}

        assert hosts == {_SELF}
        assert (_REPO_ROOT / _SELF).is_file()
        for entry in NODE_HOSTED_CONTRACT_CATEGORIES.values():
            assert isinstance(entry["host"], str) and entry["host"]
            assert len(entry["classes"]) == 3

    def test_node_hosted_representative_nodes_are_unique_and_well_formed(
        self,
    ) -> None:
        node_ids = [node_id for _, node_id in NODE_HOSTED_REPRESENTATIVE_NODES]

        assert node_ids and len(set(node_ids)) == len(node_ids)
        for category, node_id in NODE_HOSTED_REPRESENTATIVE_NODES:
            entry = NODE_HOSTED_CONTRACT_CATEGORIES[category]
            file_part, _, rest = node_id.partition("::")
            class_part, _, function_part = rest.partition("::")
            assert file_part == entry["host"], node_id
            assert class_part in entry["classes"], node_id
            assert function_part, node_id

    def test_node_hosted_category_stays_out_of_file_hosted_matrix(self) -> None:
        """Node-hosted 不得进入 FILES / CATEGORIES / suites（否则违反 Step 80 no self-registration）。"""
        assert "OFFLINE_EXECUTION_CONTRACT" not in CATEGORIES
        assert _SELF not in {spec.path for spec in FILES}
        assert _SELF not in {
            path for paths in CATEGORIES.values() for path in paths
        }
        assert _SELF not in _offline_suite()
        assert _SELF not in _db_suite()

    def test_node_hosted_category_metadata_matches_convention(self) -> None:
        """元数据沿用既有约定（``*_required`` 布尔键；**不**新建第二套 FileSpec）。"""
        required_keys = {
            "host",
            "classes",
            "scope",
            "db_required",
            "network_required",
            "llm_required",
            "production_code_required",
        }
        for category, entry in NODE_HOSTED_CONTRACT_CATEGORIES.items():
            assert set(entry) == required_keys, category
            assert isinstance(entry, dict) and not isinstance(entry, FileSpec)
            for flag in (
                "db_required",
                "network_required",
                "llm_required",
                "production_code_required",
            ):
                assert entry[flag] is False, (category, flag)

    def test_node_hosted_scope_is_unique_and_artifacts_exist(self) -> None:
        scopes = [str(entry["scope"]) for entry in NODE_HOSTED_CONTRACT_CATEGORIES.values()]

        assert len(set(scopes)) == len(scopes)
        scope = NODE_HOSTED_CONTRACT_CATEGORIES["OFFLINE_EXECUTION_CONTRACT"][
            "scope"
        ]
        for artifact in (
            "OFFLINE_EXECUTION_SUMMARY_CONTRACT",
            "OFFLINE_EXECUTION_SUMMARY_SNAPSHOT",
            "classify_snapshot_drift()",
        ):
            assert artifact in str(scope), artifact
        # 契约三要素在模块内真实存在
        assert isinstance(OFFLINE_EXECUTION_SUMMARY_CONTRACT, dict)
        assert isinstance(OFFLINE_EXECUTION_SUMMARY_SNAPSHOT, dict)
        assert callable(classify_snapshot_drift)
        assert _DRIFT_TYPES == (
            "NO_DRIFT",
            "COUNT_DRIFT",
            "EXIT_CODE_DRIFT",
            "STATUS_DRIFT",
        )

    def test_node_hosted_representative_nodes_are_collectable(self) -> None:
        """`--collect-only`（只收集、不执行）验证 node 真实存在。"""
        result = _self_collection_result()

        assert result.returncode == 0, result.stdout[-800:]
        for _, node_id in NODE_HOSTED_REPRESENTATIVE_NODES:
            assert node_id in result.stdout, node_id


# ============================================================
# Node-hosted Contract **Regression Integration**（Phase 3.12 Step 88 · 离线 · 静态）
#
# 只证明"Node-hosted Contract 被 Contract Audit 覆盖，且**不**进入执行 Matrix"。
# 不重复 Step 87 的注册自检（唯一性 / metadata / scope / self-registration）。
# ============================================================

class TestNodeHostedContractRegressionIntegration:
    def test_node_hosted_contract_is_covered_by_contract_audit(self) -> None:
        """每个 node-hosted 契约都由本 collector 的**可收集测试类**承载（审计真实存在）。"""
        collected = _self_collected_nodes()

        assert NODE_HOSTED_CONTRACT_CATEGORIES, "node-hosted 注册表不得为空"
        assert collected, "本 collector 未收集到任何 node（审计失效）"
        for category, entry in NODE_HOSTED_CONTRACT_CATEGORIES.items():
            assert entry["host"] == _SELF, category
            for class_name in entry["classes"]:
                prefix = f"{entry['host']}::{class_name}::"
                assert any(node.startswith(prefix) for node in collected), (
                    category,
                    class_name,
                )

    def test_node_hosted_contract_is_not_a_registered_file(self) -> None:
        """Node-hosted 契约不进入 FILES（也不得因 host 而被登记）。"""
        registered = {spec.path for spec in FILES}

        assert "OFFLINE_EXECUTION_CONTRACT" not in registered
        assert _SELF not in registered
        for entry in NODE_HOSTED_CONTRACT_CATEGORIES.values():
            assert entry["host"] not in registered, entry["host"]
        assert len(registered) == EXPECTED_MATRIX_SCALE["registered_files"]

    def test_node_hosted_contract_adds_no_execution_suite_entries(self) -> None:
        """Node-hosted 不改变执行套件规模；suite 成员只能来自 FILES。"""
        registered = {spec.path for spec in FILES}
        offline, db = set(_offline_suite()), set(_db_suite())

        assert len(offline) == EXPECTED_MATRIX_SCALE["offline_files"] == 18
        assert len(db) == EXPECTED_MATRIX_SCALE["db_files"] == 15
        assert offline <= registered and db <= registered
        for entry in NODE_HOSTED_CONTRACT_CATEGORIES.values():
            assert entry["host"] not in offline, entry["host"]
            assert entry["host"] not in db, entry["host"]

    def test_representative_nodes_align_with_node_hosted_contract(self) -> None:
        """代表 node = 审计入口：可 collect ∧ 归属正确 host/class ∧ node id 唯一。"""
        collected = set(_self_collected_nodes())
        node_ids = [node_id for _, node_id in NODE_HOSTED_REPRESENTATIVE_NODES]

        assert len(set(node_ids)) == len(node_ids)
        for category, node_id in NODE_HOSTED_REPRESENTATIVE_NODES:
            entry = NODE_HOSTED_CONTRACT_CATEGORIES[category]
            file_part, _, rest = node_id.partition("::")
            class_part, _, function_part = rest.partition("::")
            assert file_part == entry["host"], node_id
            assert class_part in entry["classes"], node_id
            assert function_part, node_id
            assert node_id in collected, node_id

    def test_node_hosted_contract_does_not_affect_matrix_scale(self) -> None:
        """Matrix Scale 保持 13/28/18/15；node-hosted 计数**有意排除**在外。"""
        assert EXPECTED_MATRIX_SCALE == {
            "categories": 13,
            "registered_files": 28,
            "offline_files": 18,
            "db_files": 15,
        }
        assert len(CATEGORIES) == EXPECTED_MATRIX_SCALE["categories"]
        assert len(FILES) == EXPECTED_MATRIX_SCALE["registered_files"]
        # Node-hosted 概念不得进入 Scale 键集；Snapshot 数量也不得参与
        assert not any("node_hosted" in key for key in EXPECTED_MATRIX_SCALE)
        for value in EXPECTED_MATRIX_SCALE.values():
            assert value not in {375, 356, 19}, value
        assert len(NODE_HOSTED_CONTRACT_CATEGORIES) == 1  # 有意不并入 categories 计数

    def test_file_hosted_and_node_hosted_boundaries_are_disjoint(self) -> None:
        """双向边界：FileSpec 不得混入 node-hosted 字段；node-hosted 不得引用 FileSpec 字段。"""
        file_spec_fields = [item.name for item in fields(FileSpec)]

        assert file_spec_fields == [
            "path",
            "db",
            "coverage",
            "network",
            "llm",
            "production_code",
        ]
        assert not any("node_hosted" in name for name in file_spec_fields)
        for category, entry in NODE_HOSTED_CONTRACT_CATEGORIES.items():
            assert not (set(entry) & set(file_spec_fields)), (category, set(entry))
            assert not isinstance(entry, FileSpec), category
        # 反向：FileSpec 也不携带任何 node-hosted 注册信息
        for spec in FILES:
            assert not hasattr(spec, "classes") and not hasattr(spec, "scope")
            assert spec.path != _SELF


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

    def test_snapshot_is_not_part_of_matrix_scale(self) -> None:
        """Matrix Scale ≠ Execution Snapshot：数量快照不得写入 EXPECTED_MATRIX_SCALE。"""
        assert set(EXPECTED_MATRIX_SCALE) == {
            "categories",
            "registered_files",
            "offline_files",
            "db_files",
        }
        assert EXPECTED_MATRIX_SCALE == {
            "categories": 13,          # file-hosted（node-hosted 另计 1）
            "registered_files": 28,
            "offline_files": 18,
            "db_files": 15,
        }
        for value in EXPECTED_MATRIX_SCALE.values():
            assert value not in {375, 356, 19}, value
        assert OFFLINE_EXECUTION_SUMMARY_SNAPSHOT is not EXPECTED_MATRIX_SCALE
        assert "375" not in str(EXPECTED_MATRIX_SCALE)

    def test_future_count_independence_remains_pass(self) -> None:
        """Matrix 注册后语义不变：380/361/19 ⇒ COUNT_DRIFT 且 Contract = PASS。"""
        owner = NODE_HOSTED_CONTRACT_CATEGORIES["OFFLINE_EXECUTION_CONTRACT"]
        assert "TestOfflineRegressionSnapshotDrift" in owner["classes"]

        current = _summary(passed=361, skipped=19)
        assert classify_snapshot_drift(_frozen_snapshot(), current) == (
            "COUNT_DRIFT",
        )
        assert current.status == "PASS"

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
# Offline Execution Summary Contract（Phase 3.12 Step 83 · in-memory）
#
# Offline Matrix → 执行既有 offline suite → 结构化 summary（单一缓存执行）。
# 只解析 pytest terminal summary；不写 HTML / JSON / DB；不引入 pytest plugin。
# ============================================================

class TestOfflineRegressionExecutionSummary:
    def test_summary_dto_is_immutable(self) -> None:
        summary = _parse_pytest_summary("10 passed in 0.5s\n", exit_code=0)

        with pytest.raises(Exception) as excinfo:
            summary.passed = 99  # type: ignore[misc]
        assert "frozen" in type(excinfo.value).__name__.lower()
        # duration 不参与 equality（信息字段）
        same = _parse_pytest_summary(
            "10 passed in 9.9s\n", exit_code=0, duration_seconds=9.9
        )
        assert same == summary
        assert same.duration_seconds != summary.duration_seconds

    def test_all_pass_summary_is_pass(self) -> None:
        summary = _parse_pytest_summary("10 passed in 0.5s\n", exit_code=0)

        assert (summary.total, summary.passed, summary.skipped) == (10, 10, 0)
        assert (summary.failed, summary.errors, summary.exit_code) == (0, 0, 0)
        assert summary.status == "PASS" and summary.is_pass is True

    def test_pass_with_skips_is_pass(self) -> None:
        """skipped 只是统计（DB-gated skip 属 SKIPPED，不是 FAIL）。"""
        summary = _parse_pytest_summary(
            "356 passed, 19 skipped in 9.17s", exit_code=0
        )

        assert (summary.total, summary.passed, summary.skipped) == (375, 356, 19)
        assert summary.status == "PASS"

    def test_failed_summary_is_fail(self) -> None:
        summary = _parse_pytest_summary(
            "9 passed, 1 failed in 1.0s", exit_code=1
        )

        assert summary.failed == 1
        assert summary.status == "FAIL" and summary.is_pass is False

    def test_error_summary_is_fail(self) -> None:
        summary = _parse_pytest_summary("1 error in 0.5s", exit_code=1)

        assert summary.errors == 1
        assert summary.status == "FAIL"

    def test_non_zero_exit_code_is_fail(self) -> None:
        """即使计数全绿，exit_code != 0 也是 FAIL（如 collection error / no tests）。"""
        summary = _parse_pytest_summary("10 passed in 0.5s", exit_code=5)

        assert summary.status == "FAIL"

    def test_summary_arithmetic_consistency(self) -> None:
        summary = _parse_pytest_summary(
            "3 passed, 2 skipped, 1 failed in 1.0s", exit_code=1
        )

        assert summary.total == (
            summary.passed + summary.skipped + summary.failed + summary.errors
        )
        # 算术不一致 → 拒绝构造（total 必须等于四项之和）
        with pytest.raises(ValueError):
            RegressionExecutionSummary(
                total=8, passed=3, skipped=2, failed=1, errors=0, exit_code=1
            )
        # 非 int（bool）→ 拒绝
        with pytest.raises(ValueError):
            RegressionExecutionSummary(
                total=6, passed=6, skipped=0, failed=0, errors=0,
                exit_code=True,  # type: ignore[arg-type]
            )
        # 负数 exit_code（被信号终止）是**合法表达**，但必须是 FAIL
        killed = _parse_pytest_summary("6 passed in 0.5s", exit_code=-9)

        assert killed.status == "FAIL" and killed.is_pass is False

    def test_real_offline_suite_execution_summary(self) -> None:
        """真实执行 `_offline_suite`（18 文件）并断言 summary 契约。"""
        summary = _offline_execution_summary()
        result = _offline_suite_execution()

        assert len(_offline_suite()) == EXPECTED_MATRIX_SCALE["offline_files"] == 18
        assert summary.total > 0
        assert summary.status == "PASS", result.stdout[-1500:]
        assert summary.exit_code == 0
        assert summary.failed == 0 and summary.errors == 0
        assert summary.total == (
            summary.passed + summary.skipped + summary.failed + summary.errors
        )
        # 未建模的 outcome 类别（xfail / xpass / deselected…）必须为空
        assert _unexpected_outcome_tokens(result.stdout) == ()


# ============================================================
# Offline Execution Summary Contract 冻结（Phase 3.12 Step 84）
#
# 只冻结 Step 83 的语义（synthsummary + 已知 snapshot）；**不**重跑 18 个文件。
# Contract（语义，长期稳定）与 Snapshot（当前数量）严格区分。
# ============================================================

#: Snapshot Drift 的最小分类（Step 85 §五；不新增更复杂分类）。
_DRIFT_TYPES: tuple[str, ...] = (
    "NO_DRIFT",
    "COUNT_DRIFT",
    "EXIT_CODE_DRIFT",
    "STATUS_DRIFT",
)

#: 计数维度（Drift 比较的字段）。
_DRIFT_COUNT_FIELDS: tuple[str, ...] = (
    "total",
    "passed",
    "skipped",
    "failed",
    "errors",
)


def _frozen_snapshot() -> MappingProxyType[str, int | str]:
    """Snapshot 的**只读视图**（Drift 审计物理上无法改写 Snapshot；§九 无自动更新）。"""
    return MappingProxyType(dict(OFFLINE_EXECUTION_SUMMARY_SNAPSHOT))


def classify_snapshot_drift(
    snapshot: Mapping[str, int | str],
    current: RegressionExecutionSummary,
) -> tuple[str, ...]:
    """比较当前执行结果与 Snapshot，返回 Drift 分类（可组合；纯函数、无副作用）。

    语义：

        NO_DRIFT        当前计数 / exit_code / status 全部等于 Snapshot
        COUNT_DRIFT     total / passed / skipped / failed / errors 任一不同
        EXIT_CODE_DRIFT exit_code 不同
        STATUS_DRIFT    status 不同

    **Drift ≠ 失败**：最终 PASS / FAIL 由 Step 84 Summary Contract 决定
    （``current.status``）；本函数只指出"与 Snapshot 是否一致"。
    """
    drift: list[str] = []

    if any(
        getattr(current, name) != int(snapshot[name])
        for name in _DRIFT_COUNT_FIELDS
    ):
        drift.append("COUNT_DRIFT")
    if current.exit_code != int(snapshot["exit_code"]):
        drift.append("EXIT_CODE_DRIFT")
    if current.status != str(snapshot["status"]):
        drift.append("STATUS_DRIFT")

    return tuple(drift) if drift else ("NO_DRIFT",)


def _summary_from_snapshot(snapshot: dict[str, int | str]) -> RegressionExecutionSummary:
    return RegressionExecutionSummary(
        total=int(snapshot["total"]),
        passed=int(snapshot["passed"]),
        skipped=int(snapshot["skipped"]),
        failed=int(snapshot["failed"]),
        errors=int(snapshot["errors"]),
        exit_code=int(snapshot["exit_code"]),
    )


def _summary(
    passed: int, skipped: int = 0, failed: int = 0, errors: int = 0, exit_code: int = 0
) -> RegressionExecutionSummary:
    return RegressionExecutionSummary(
        total=passed + skipped + failed + errors,
        passed=passed,
        skipped=skipped,
        failed=failed,
        errors=errors,
        exit_code=exit_code,
    )


class TestOfflineRegressionExecutionSummaryContract:
    def test_contract_declares_semantics_not_counts(self) -> None:
        """Contract 只声明语义；数量只出现在 snapshot（两者不得混用）。"""
        assert set(OFFLINE_EXECUTION_SUMMARY_CONTRACT) == {
            "pass",
            "fail",
            "skip",
            "arithmetic",
            "exit_code",
            "immutability",
            "duration",
            "extra_outcomes",
        }
        assert set(OFFLINE_EXECUTION_SUMMARY_SNAPSHOT) == {
            "total",
            "passed",
            "skipped",
            "failed",
            "errors",
            "exit_code",
            "status",
        }
        # Contract 文本中不得出现 snapshot 的具体数量（防把快照写进契约）
        assert "375" not in " ".join(OFFLINE_EXECUTION_SUMMARY_CONTRACT.values())
        assert "356" not in " ".join(OFFLINE_EXECUTION_SUMMARY_CONTRACT.values())

    def test_current_snapshot_matches_recorded_baseline(self) -> None:
        summary = _summary_from_snapshot(OFFLINE_EXECUTION_SUMMARY_SNAPSHOT)

        assert summary.total == 375
        assert summary.passed == 356
        assert summary.skipped == 19
        assert (summary.failed, summary.errors) == (0, 0)
        assert summary.exit_code == 0
        assert summary.status == OFFLINE_EXECUTION_SUMMARY_SNAPSHOT["status"] == "PASS"

    def test_pass_semantics(self) -> None:
        for summary in (_summary(10), _summary(10, skipped=2), _summary(0, skipped=5)):
            assert summary.status == "PASS", summary

    def test_fail_semantics(self) -> None:
        assert _summary(9, failed=1, exit_code=1).status == "FAIL"
        assert _summary(1, errors=1, exit_code=1).status == "FAIL"
        assert _summary(10, exit_code=1).status == "FAIL"
        assert _summary(10, exit_code=5).status == "FAIL"     # no tests ran / collection error
        assert _summary(10, exit_code=-9).status == "FAIL"    # 信号终止

    def test_skip_does_not_cause_failure(self) -> None:
        """skipped 只是 outcome count：DB-gated skip 属 SKIPPED，不是 FAILED。"""
        summary = _summary(10, skipped=1000)

        assert summary.skipped == 1000
        assert summary.status == "PASS"
        assert "skipped" not in OFFLINE_EXECUTION_SUMMARY_CONTRACT["fail"]

    def test_arithmetic_contract(self) -> None:
        summary = _summary(3, skipped=2, failed=1, exit_code=1)

        assert summary.total == (
            summary.passed + summary.skipped + summary.failed + summary.errors
        )
        assert "total == passed + skipped + failed + errors" == (
            OFFLINE_EXECUTION_SUMMARY_CONTRACT["arithmetic"]
        )
        for invalid in (
            dict(total=8, passed=3, skipped=2, failed=1, errors=0, exit_code=1),
            dict(total=7, passed=3, skipped=2, failed=1, errors=0, exit_code=1),
        ):
            with pytest.raises(ValueError):
                RegressionExecutionSummary(**invalid)

    def test_immutability_contract(self) -> None:
        summary = _summary(4)

        with pytest.raises(Exception) as excinfo:
            summary.total = 0  # type: ignore[misc]
        assert "frozen" in type(excinfo.value).__name__.lower()
        # duration 为信息字段：不参与 equality / baseline
        assert _summary(4) == RegressionExecutionSummary(
            total=4, passed=4, skipped=0, failed=0, errors=0,
            exit_code=0, duration_seconds=42.0,
        )

    def test_future_count_independence(self) -> None:
        """Contract 不绑定 375：数量变化但语义相同 → 仍 PASS。"""
        grown = _summary(passed=361, skipped=19)

        assert grown.total == 380
        assert grown.status == "PASS"
        # 语义判定只依赖字段，不读取 snapshot（AST 校验 status 属性体）
        tree = ast.parse(_source(_SELF))
        status_source = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == (
                "RegressionExecutionSummary"
            ):
                for child in node.body:
                    if isinstance(child, ast.FunctionDef) and child.name == "status":
                        status_source = ast.unparse(child)
        assert status_source, "未找到 status 属性实现"
        assert "SNAPSHOT" not in status_source
        assert "CONTRACT" not in status_source
        assert status_source.count("self.exit_code") >= 1
        assert status_source.count("self.failed") >= 1
        assert status_source.count("self.errors") >= 1


# ============================================================
# Offline Snapshot Drift Audit（Phase 3.12 Step 85 · synthetic only）
#
# Contract（PASS/FAIL 语义） ≠ Snapshot（数量记录） ≠ Drift（与 Snapshot 的差异）。
# Drift ≠ 失败；**不**自动更新 Snapshot；**不**重跑 18 个文件。
# ============================================================

class TestOfflineRegressionSnapshotDrift:
    def test_no_drift_when_current_matches_snapshot(self) -> None:
        snapshot = _frozen_snapshot()
        current = _summary_from_snapshot(dict(snapshot))

        assert classify_snapshot_drift(snapshot, current) == ("NO_DRIFT",)
        assert current.status == "PASS"

    def test_count_drift_on_passed_increase(self) -> None:
        snapshot = _frozen_snapshot()
        current = _summary(passed=361, skipped=19)

        assert classify_snapshot_drift(snapshot, current) == ("COUNT_DRIFT",)
        assert current.status == "PASS"        # Drift ≠ 失败

    def test_count_drift_on_skipped_increase(self) -> None:
        snapshot = _frozen_snapshot()
        current = _summary(passed=356, skipped=20)

        assert classify_snapshot_drift(snapshot, current) == ("COUNT_DRIFT",)
        assert current.status == "PASS"

    def test_count_drift_with_failed_is_contract_fail(self) -> None:
        snapshot = _frozen_snapshot()
        current = _summary(passed=355, failed=1, exit_code=1)

        drift = classify_snapshot_drift(snapshot, current)
        assert "COUNT_DRIFT" in drift
        assert current.status == "FAIL"        # 最终判定仍由 Step 84 Contract 决定

    def test_count_drift_with_errors_is_contract_fail(self) -> None:
        snapshot = _frozen_snapshot()
        current = _summary(passed=356, skipped=19, errors=1, exit_code=1)

        drift = classify_snapshot_drift(snapshot, current)
        assert "COUNT_DRIFT" in drift
        assert current.status == "FAIL"

    def test_exit_code_drift(self) -> None:
        """计数不变但 exit_code 0 → 1：EXIT_CODE_DRIFT（并伴随 STATUS_DRIFT）+ FAIL。"""
        snapshot = _frozen_snapshot()
        current = _summary(passed=356, skipped=19, exit_code=1)

        assert set(classify_snapshot_drift(snapshot, current)) == {
            "EXIT_CODE_DRIFT",
            "STATUS_DRIFT",
        }
        assert current.status == "FAIL"

    def test_status_drift(self) -> None:
        """status 变化与计数 / exit_code 变化共现（分类是组合式，非互斥）。"""
        snapshot = _frozen_snapshot()
        current = _summary(passed=355, skipped=19, failed=1, exit_code=1)

        drift = classify_snapshot_drift(snapshot, current)
        assert set(drift) == {"COUNT_DRIFT", "EXIT_CODE_DRIFT", "STATUS_DRIFT"}
        assert current.status == "FAIL"

    def test_future_count_independence(self) -> None:
        """380 / 361 / 19 / 0 / 0 → COUNT_DRIFT 且 Contract = PASS。"""
        snapshot = _frozen_snapshot()
        current = _summary(passed=361, skipped=19)

        assert (current.total, current.passed, current.skipped) == (380, 361, 19)
        assert classify_snapshot_drift(snapshot, current) == ("COUNT_DRIFT",)
        assert current.status == "PASS"

    def test_snapshot_is_immutable_during_drift_audit(self) -> None:
        """审计后 Snapshot 不变；只读视图拒绝写入（无 self-healing baseline）。"""
        snapshot = _frozen_snapshot()
        before = dict(OFFLINE_EXECUTION_SUMMARY_SNAPSHOT)

        classify_snapshot_drift(snapshot, _summary(passed=361, skipped=19))
        with pytest.raises(TypeError):
            snapshot["total"] = 999  # type: ignore[index]

        assert dict(OFFLINE_EXECUTION_SUMMARY_SNAPSHOT) == before
        assert OFFLINE_EXECUTION_SUMMARY_SNAPSHOT["total"] == 375

    def test_drift_audit_does_not_execute_suite(self) -> None:
        """Drift 分类是纯函数：不得引用 suite 执行器 / subprocess（不重跑 18 文件）。"""
        tree = ast.parse(_source(_SELF))
        body = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == (
                "classify_snapshot_drift"
            ):
                body = ast.unparse(node)

        assert body, "未找到 classify_snapshot_drift 实现"
        for forbidden in (
            "_offline_suite_execution",
            "_offline_execution_summary",
            "_run_pytest",
            "subprocess",
            "_collect_one",
        ):
            assert forbidden not in body, forbidden


# ============================================================
# Matrix Execution Summary（Phase 3.12 Step 89 · 实际执行）
#
# 只汇总 offline suite + DB suite 的**实际**执行结果；不复用/不重复 Step 83～85
# 的纯单元测试；不修改 Snapshot（只报告 drift）。
# ============================================================

class TestMatrixExecutionSummary:
    def test_matrix_execution_summary_is_immutable(self) -> None:
        summary = _matrix_execution_summary()

        with pytest.raises(Exception) as excinfo:
            summary.offline = None  # type: ignore[assignment]
        assert "frozen" in type(excinfo.value).__name__.lower()
        assert not hasattr(summary, "node_hosted")
        # db 未执行时必须给原因（不得静默）；已执行时不得再给原因
        with pytest.raises(ValueError):
            MatrixExecutionSummary(offline=_summary(1))
        with pytest.raises(ValueError):
            MatrixExecutionSummary(
                offline=_summary(1), db=_summary(1), db_skip_reason="x"
            )
        skipped = MatrixExecutionSummary(offline=_summary(1), db_skip_reason="env")

        assert skipped.db is None and skipped.status == "PASS"

    def test_offline_summary_uses_real_execution_result(self) -> None:
        summary = _matrix_execution_summary().offline
        result = _offline_suite_execution()

        assert len(_offline_suite()) == EXPECTED_MATRIX_SCALE["offline_files"] == 18
        assert summary.total > 0
        assert summary.exit_code == result.returncode
        assert summary.duration_seconds == _parse_duration(result.stdout)
        assert summary == _parse_pytest_summary(
            result.stdout,
            exit_code=result.returncode,
            duration_seconds=_parse_duration(result.stdout),
        )

    def test_db_summary_uses_real_execution_result(self) -> None:
        matrix = _matrix_execution_summary()
        available, reason = _db_execution_available()

        if not available:
            assert matrix.db is None
            assert matrix.db_skip_reason == reason and reason
            pytest.skip(f"DB execution = SKIPPED（{reason}）")
        assert matrix.db is not None
        assert matrix.db_skip_reason is None
        assert len(_db_suite()) == EXPECTED_MATRIX_SCALE["db_files"] == 15
        assert matrix.db.total > 0
        assert matrix.db.exit_code == _db_suite_execution().returncode

    def test_node_hosted_contract_is_not_counted_in_execution(self) -> None:
        """Node-hosted 只做 Contract Audit，不参与 execution count。"""
        matrix = _matrix_execution_summary()
        suite_files = set(_offline_suite()) | set(_db_suite())

        assert NODE_HOSTED_CONTRACT_CATEGORIES  # 存在，但...
        assert "OFFLINE_EXECUTION_CONTRACT" not in CATEGORIES
        assert _SELF not in suite_files
        # execution 总量只来自已执行的两套 suite
        assert matrix.total == sum(part.total for part in matrix.executed_parts)
        assert matrix.total >= matrix.offline.total

    def test_scale_is_not_pass_count(self) -> None:
        """Scale 表达注册结构，不等于 passed 数量（两维度不可混用）。"""
        matrix = _matrix_execution_summary()

        assert EXPECTED_MATRIX_SCALE == {
            "categories": 13,
            "registered_files": 28,
            "offline_files": 18,
            "db_files": 15,
        }
        assert matrix.offline.passed != EXPECTED_MATRIX_SCALE["offline_files"]
        assert matrix.offline.passed > 18
        if matrix.db is not None:
            assert matrix.db.passed != EXPECTED_MATRIX_SCALE["db_files"]
        for value in EXPECTED_MATRIX_SCALE.values():
            assert value not in {375, 356, 19}, value

    def test_execution_failure_propagates(self) -> None:
        """任一 suite FAIL → MatrixExecutionSummary FAIL（合成传播验证）。"""
        passing = _summary(3)
        failing = _summary(2, failed=1, exit_code=1)

        assert MatrixExecutionSummary(
            offline=passing, db_skip_reason="env"
        ).status == "PASS"
        assert MatrixExecutionSummary(
            offline=failing, db_skip_reason="env"
        ).status == "FAIL"
        assert MatrixExecutionSummary(
            offline=passing, db=failing
        ).status == "FAIL"
        assert MatrixExecutionSummary(
            offline=passing, db_skip_reason="env"
        ).status == "PASS"
        assert _matrix_execution_summary().status in {"PASS", "FAIL"}

    def test_snapshot_drift_reuses_existing_classifier(self) -> None:
        """复用 Step 85 classifier；Snapshot **不被更新**（只报告差异）。"""
        matrix = _matrix_execution_summary()
        frozen_before = dict(OFFLINE_EXECUTION_SUMMARY_SNAPSHOT)

        drift = classify_snapshot_drift(_frozen_snapshot(), matrix.offline)

        assert drift and set(drift) <= set(_DRIFT_TYPES)
        assert dict(OFFLINE_EXECUTION_SUMMARY_SNAPSHOT) == frozen_before
        assert OFFLINE_EXECUTION_SUMMARY_SNAPSHOT["total"] == 375

    def test_summary_arithmetic_consistency(self) -> None:
        matrix = _matrix_execution_summary()

        for part in matrix.executed_parts:
            assert part.total == (
                part.passed + part.skipped + part.failed + part.errors
            )
        assert matrix.total == sum(part.total for part in matrix.executed_parts)
        assert matrix.status == (
            "PASS" if all(p.is_pass for p in matrix.executed_parts) else "FAIL"
        )


# ============================================================
# Matrix Execution Baseline（Phase 3.12 Step 90 · 冻结 Step 89 实际执行结果）
#
# Baseline（执行基线） ≠ Snapshot（离线快照） ≠ Scale（注册拓扑）。
# 不重跑 Matrix（复用 Step 89 execution helper）；duration 不入 Contract。
# ============================================================

class TestMatrixExecutionBaseline:
    def test_baseline_dto_is_immutable(self) -> None:
        baseline = MATRIX_EXECUTION_BASELINE

        for field_name in ("offline", "db", "matrix_total", "matrix_status"):
            with pytest.raises(Exception) as excinfo:
                setattr(baseline, field_name, None)
            assert "frozen" in type(excinfo.value).__name__.lower()
        assert not hasattr(baseline, "duration")

    def test_baseline_matches_step89_actual_execution(self) -> None:
        """Baseline 必须等于 Step 89 **实际执行**结果（不得手工构造 PASS）。"""
        current = _current_matrix_baseline()

        if current is None:
            pytest.skip(f"DB 不可用（{_db_execution_available()[1]}）")
        assert MATRIX_EXECUTION_BASELINE == current
        assert compare_matrix_execution_baseline(
            MATRIX_EXECUTION_BASELINE, current
        ) == ("NO_BASELINE_DRIFT",)
        # 逐字段等价（offline / db / total / status / residue）
        assert MATRIX_EXECUTION_BASELINE.offline == current.offline
        assert MATRIX_EXECUTION_BASELINE.db == current.db
        assert MATRIX_EXECUTION_BASELINE.matrix_total == current.matrix_total == (
            current.offline.total + current.db.total
        )

    def test_matrix_total_equals_offline_plus_db(self) -> None:
        baseline = MATRIX_EXECUTION_BASELINE

        assert baseline.matrix_total == (
            baseline.offline.total + baseline.db.total
        ) == 375 + 180 == 555
        # 明确**不是** passed 之和
        assert baseline.offline.passed + baseline.db.passed == 536 != (
            baseline.matrix_total
        )
        with pytest.raises(ValueError):
            MatrixExecutionBaseline(
                offline=baseline.offline,
                db=baseline.db,
                matrix_total=536,
                matrix_status="PASS",
                db_residue=0,
            )

    def test_matrix_status_derives_from_part_statuses(self) -> None:
        """status 由 offline/db 的 Step 84 status 推导（**不**新建 classifier）。"""
        baseline = MATRIX_EXECUTION_BASELINE

        assert baseline.matrix_status == "PASS"
        assert baseline.offline.status == "PASS" and baseline.db.status == "PASS"
        failing = RegressionExecutionSummary(
            total=1, passed=0, skipped=0, failed=1, errors=0, exit_code=1
        )
        with pytest.raises(ValueError):          # status 与部分状态不一致 → 拒绝构造
            MatrixExecutionBaseline(
                offline=failing,
                db=baseline.db,
                matrix_total=1 + baseline.db.total,
                matrix_status="PASS",
                db_residue=0,
            )
        assert MatrixExecutionBaseline(
            offline=failing,
            db=baseline.db,
            matrix_total=1 + baseline.db.total,
            matrix_status="FAIL",
            db_residue=0,
        ).matrix_status == "FAIL"

    def test_db_residue_is_zero(self) -> None:
        residue = _db_residue_total()

        if residue is None:
            pytest.skip(f"DB 不可用（{_db_execution_available()[1]}）")
        assert residue == 0
        assert MATRIX_EXECUTION_BASELINE.db_residue == residue
        assert isinstance(MATRIX_EXECUTION_BASELINE.db_residue, int)

    def test_baseline_does_not_modify_offline_snapshot(self) -> None:
        """Step 84 的 Offline Snapshot 必须保持原值（375 / 356 / 19）。"""
        before = dict(OFFLINE_EXECUTION_SUMMARY_SNAPSHOT)

        _ = compare_matrix_execution_baseline(
            MATRIX_EXECUTION_BASELINE, MATRIX_EXECUTION_BASELINE
        )

        assert dict(OFFLINE_EXECUTION_SUMMARY_SNAPSHOT) == before
        assert OFFLINE_EXECUTION_SUMMARY_SNAPSHOT["total"] == 375
        assert OFFLINE_EXECUTION_SUMMARY_SNAPSHOT["passed"] == 356
        assert OFFLINE_EXECUTION_SUMMARY_SNAPSHOT["skipped"] == 19
        assert OFFLINE_EXECUTION_SUMMARY_SNAPSHOT is not MATRIX_EXECUTION_BASELINE

    def test_baseline_does_not_modify_matrix_scale(self) -> None:
        """Scale = 注册拓扑（13/28/18/15）；不得写入执行结果（375 / 180 / 555）。"""
        assert EXPECTED_MATRIX_SCALE == {
            "categories": 13,
            "registered_files": 28,
            "offline_files": 18,
            "db_files": 15,
        }
        for value in EXPECTED_MATRIX_SCALE.values():
            assert value not in {375, 180, 555}, value
        assert len(CATEGORIES) == 13  # Node-hosted 不并入 categories

    def test_baseline_excludes_node_hosted_execution(self) -> None:
        """Node-hosted（1 个契约 / 2 条 node）不进入执行计数。"""
        baseline = MATRIX_EXECUTION_BASELINE
        baseline_fields = {item.name for item in fields(MatrixExecutionBaseline)}

        assert not any("node_hosted" in name for name in baseline_fields)
        assert baseline.matrix_total == (
            baseline.offline.total + baseline.db.total
        )
        assert baseline.matrix_total not in {
            555 + 1,
            555 + 2,
            555 + len(NODE_HOSTED_CONTRACT_CATEGORIES),
            555 + len(NODE_HOSTED_REPRESENTATIVE_NODES),
        }
        assert "OFFLINE_EXECUTION_CONTRACT" not in CATEGORIES

    def test_baseline_excludes_duration(self) -> None:
        baseline = MATRIX_EXECUTION_BASELINE

        assert "duration" not in {item.name for item in fields(baseline)}
        assert baseline.offline.duration_seconds is None
        assert baseline.db.duration_seconds is None
        # duration 变化不影响 Baseline equality（compare=False）
        dated = RegressionExecutionSummary(
            total=375, passed=356, skipped=19, failed=0, errors=0,
            exit_code=0, duration_seconds=99.0,
        )
        assert dated == baseline.offline

    def test_synthetic_execution_drift_is_detected(self) -> None:
        baseline = MATRIX_EXECUTION_BASELINE
        grown_offline = RegressionExecutionSummary(
            total=380, passed=361, skipped=19, failed=0, errors=0, exit_code=0
        )
        grown = MatrixExecutionBaseline(
            offline=grown_offline,
            db=baseline.db,
            matrix_total=grown_offline.total + baseline.db.total,
            matrix_status="PASS",
            db_residue=0,
        )
        failed_db = RegressionExecutionSummary(
            total=180, passed=179, skipped=0, failed=1, errors=0, exit_code=1
        )
        failing = MatrixExecutionBaseline(
            offline=baseline.offline,
            db=failed_db,
            matrix_total=baseline.offline.total + failed_db.total,
            matrix_status="FAIL",
            db_residue=1,
        )

        assert compare_matrix_execution_baseline(baseline, baseline) == (
            "NO_BASELINE_DRIFT",
        )
        assert set(compare_matrix_execution_baseline(baseline, grown)) == {
            "OFFLINE_EXECUTION_DRIFT",
            "MATRIX_TOTAL_DRIFT",
        }
        assert set(compare_matrix_execution_baseline(baseline, failing)) == {
            "DB_EXECUTION_DRIFT",
            "MATRIX_STATUS_DRIFT",
            "DB_RESIDUE_DRIFT",
        }
        for drift in (
            ("NO_BASELINE_DRIFT",),
            *(
                compare_matrix_execution_baseline(baseline, grown),
                compare_matrix_execution_baseline(baseline, failing),
            ),
        ):
            assert set(drift) <= set(_MATRIX_BASELINE_DRIFT_TYPES)
        # Step 85 的 4 类 Drift 未被扩展
        assert _DRIFT_TYPES == (
            "NO_DRIFT",
            "COUNT_DRIFT",
            "EXIT_CODE_DRIFT",
            "STATUS_DRIFT",
        )

    def test_baseline_comparison_is_deterministic(self) -> None:
        baseline = MATRIX_EXECUTION_BASELINE

        first = compare_matrix_execution_baseline(baseline, baseline)
        second = compare_matrix_execution_baseline(baseline, baseline)

        assert first == second == ("NO_BASELINE_DRIFT",)
        assert MATRIX_EXECUTION_BASELINE == MatrixExecutionBaseline(
            offline=MATRIX_EXECUTION_BASELINE.offline,
            db=MATRIX_EXECUTION_BASELINE.db,
            matrix_total=555,
            matrix_status="PASS",
            db_residue=0,
        )

    def test_baseline_logic_has_no_db_or_network_dependency(self) -> None:
        """Baseline 比较逻辑为纯函数；DB 读只发生在 residue helper（延迟 import）。"""
        tree = ast.parse(_source(_SELF))
        body = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == (
                "compare_matrix_execution_baseline"
            ):
                body = ast.unparse(node)

        assert body, "未找到 compare_matrix_execution_baseline 实现"
        for forbidden in (
            "subprocess",
            "get_engine",
            "sqlalchemy",
            "_run_pytest",
            "_offline_suite_execution",
            "_matrix_execution_summary",
        ):
            assert forbidden not in body, forbidden
        module_level = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        for module in module_level:
            for prefix in ("backend.app.db", "sqlalchemy", "httpx", "requests"):
                assert not module.startswith(prefix), module


# ============================================================
# Matrix Baseline Gate（Phase 3.12 Step 91 · 纯函数 · 无执行）
#
# current vs frozen baseline → PASS / DRIFT；无容差、无自动更新 baseline。
# ============================================================

class TestMatrixBaselineGate:
    @staticmethod
    def _baseline_with(
        *,
        offline: RegressionExecutionSummary | None = None,
        db: RegressionExecutionSummary | None = None,
        residue: int = 0,
    ) -> MatrixExecutionBaseline:
        base = MATRIX_EXECUTION_BASELINE
        offline_part = offline or base.offline
        db_part = db or base.db
        return MatrixExecutionBaseline(
            offline=offline_part,
            db=db_part,
            matrix_total=offline_part.total + db_part.total,
            matrix_status=(
                "PASS" if offline_part.is_pass and db_part.is_pass else "FAIL"
            ),
            db_residue=residue,
        )

    def test_gate_passes_when_current_matches_baseline(self) -> None:
        """真实数据（复用 Step 89 helper，**不**重跑）：current == baseline ⇒ PASS。"""
        current = _current_matrix_baseline()

        if current is None:
            pytest.skip(f"DB 不可用（{_db_execution_available()[1]}）")
        result = evaluate_matrix_baseline_gate(
            current, MATRIX_EXECUTION_BASELINE
        )

        assert result.status == "PASS" and result.is_pass is True
        assert result.drifts == ("NO_BASELINE_DRIFT",)

    def test_gate_detects_offline_execution_drift(self) -> None:
        """Case B：offline 结果变化（passed 356→355 / skipped 19→20，total 不变）。"""
        current = self._baseline_with(
            offline=RegressionExecutionSummary(
                total=375, passed=355, skipped=20, failed=0, errors=0, exit_code=0
            )
        )
        result = evaluate_matrix_baseline_gate(
            current, MATRIX_EXECUTION_BASELINE
        )

        assert result.status == "DRIFT"
        assert result.drifts == ("OFFLINE_EXECUTION_DRIFT",)

    def test_gate_detects_db_execution_drift(self) -> None:
        """Case C：DB 结果变化（skipped 0→1 / passed 180→179，total 与 status 不变）。"""
        current = self._baseline_with(
            db=RegressionExecutionSummary(
                total=180, passed=179, skipped=1, failed=0, errors=0, exit_code=0
            )
        )
        result = evaluate_matrix_baseline_gate(
            current, MATRIX_EXECUTION_BASELINE
        )

        assert result.status == "DRIFT"
        assert result.drifts == ("DB_EXECUTION_DRIFT",)

    def test_gate_detects_matrix_total_drift(self) -> None:
        """Case D：total 变化必然伴随某个 part 变化（组合式，TOTAL 必在其中）。"""
        current = self._baseline_with(
            db=RegressionExecutionSummary(
                total=181, passed=181, skipped=0, failed=0, errors=0, exit_code=0
            )
        )
        result = evaluate_matrix_baseline_gate(
            current, MATRIX_EXECUTION_BASELINE
        )

        assert result.status == "DRIFT"
        assert "MATRIX_TOTAL_DRIFT" in result.drifts
        assert set(result.drifts) == {
            "DB_EXECUTION_DRIFT",
            "MATRIX_TOTAL_DRIFT",
        }

    def test_gate_detects_matrix_status_drift(self) -> None:
        """Case E：status PASS→FAIL（必然伴随对应 part 变化）。"""
        current = self._baseline_with(
            db=RegressionExecutionSummary(
                total=180, passed=179, skipped=0, failed=1, errors=0, exit_code=1
            )
        )
        result = evaluate_matrix_baseline_gate(
            current, MATRIX_EXECUTION_BASELINE
        )

        assert result.status == "DRIFT"
        assert "MATRIX_STATUS_DRIFT" in result.drifts
        assert set(result.drifts) == {
            "DB_EXECUTION_DRIFT",
            "MATRIX_STATUS_DRIFT",
        }

    def test_gate_detects_db_residue_drift(self) -> None:
        """Case F：residue 0→1 ⇒ DB_RESIDUE_DRIFT + DRIFT（其余字段完全一致）。"""
        current = self._baseline_with(residue=1)
        result = evaluate_matrix_baseline_gate(
            current, MATRIX_EXECUTION_BASELINE
        )

        assert result.status == "DRIFT"
        assert result.drifts == ("DB_RESIDUE_DRIFT",)

    def test_gate_ignores_duration_changes(self) -> None:
        """Case G：仅 duration 变化（9.15→20.0）不得产生 DRIFT。"""
        base = MATRIX_EXECUTION_BASELINE
        current = MatrixExecutionBaseline(
            offline=RegressionExecutionSummary(
                total=375, passed=356, skipped=19, failed=0, errors=0,
                exit_code=0, duration_seconds=20.0,
            ),
            db=RegressionExecutionSummary(
                total=180, passed=180, skipped=0, failed=0, errors=0,
                exit_code=0, duration_seconds=20.0,
            ),
            matrix_total=base.matrix_total,
            matrix_status=base.matrix_status,
            db_residue=base.db_residue,
        )
        result = evaluate_matrix_baseline_gate(current, base)

        assert result.status == "PASS"
        assert result.drifts == ("NO_BASELINE_DRIFT",)

    def test_gate_detects_multiple_drifts_compositionally(self) -> None:
        """Case H：offline / db / residue 同时变化 ⇒ 全部 drift 类型均出现。"""
        current = self._baseline_with(
            offline=RegressionExecutionSummary(
                total=380, passed=361, skipped=19, failed=0, errors=0, exit_code=0
            ),
            db=RegressionExecutionSummary(
                total=180, passed=179, skipped=1, failed=0, errors=0, exit_code=0
            ),
            residue=2,
        )
        result = evaluate_matrix_baseline_gate(
            current, MATRIX_EXECUTION_BASELINE
        )

        assert result.status == "DRIFT"
        assert set(result.drifts) == {
            "OFFLINE_EXECUTION_DRIFT",
            "DB_EXECUTION_DRIFT",
            "MATRIX_TOTAL_DRIFT",
            "DB_RESIDUE_DRIFT",
        }

    def test_gate_result_is_immutable_and_inputs_untouched(self) -> None:
        base_before = MATRIX_EXECUTION_BASELINE
        current = self._baseline_with(residue=1)
        current_before = current

        result = evaluate_matrix_baseline_gate(current, base_before)

        with pytest.raises(Exception) as excinfo:
            result.status = "PASS"  # type: ignore[misc]
        assert "frozen" in type(excinfo.value).__name__.lower()
        assert MATRIX_EXECUTION_BASELINE == base_before
        assert current == current_before
        assert isinstance(result.drifts, tuple)
        assert OFFLINE_EXECUTION_SUMMARY_SNAPSHOT["total"] == 375

    def test_gate_is_deterministic(self) -> None:
        current = self._baseline_with(residue=1)

        first = evaluate_matrix_baseline_gate(current, MATRIX_EXECUTION_BASELINE)
        second = evaluate_matrix_baseline_gate(current, MATRIX_EXECUTION_BASELINE)

        assert first == second
        assert first.drifts == second.drifts == ("DB_RESIDUE_DRIFT",)

    def test_gate_result_has_no_sensitive_data(self) -> None:
        """Gate Result 只含执行摘要 / baseline / drift / status（无凭据类字段）。"""
        result = evaluate_matrix_baseline_gate(
            MATRIX_EXECUTION_BASELINE, MATRIX_EXECUTION_BASELINE
        )
        field_names = {item.name for item in fields(MatrixBaselineGateResult)}
        blob = repr(result)

        assert field_names == {"status", "drifts", "current", "baseline"}
        for forbidden in (
            "api_key",
            "password",
            "database_url",
            "authorization",
            "prompt",
            "sql",
            "rag_chunk",
            "tool_args",
            "raw_response",
        ):
            assert forbidden not in field_names
            assert forbidden not in blob
        for sentinel in ("postgresql://", "sk-", "Bearer"):
            assert sentinel not in blob

    def test_gate_has_no_execution_or_db_dependency(self) -> None:
        """Gate 是纯函数：不执行 pytest / 不读 DB / 不访问网络。"""
        tree = ast.parse(_source(_SELF))
        gate_body = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == (
                "evaluate_matrix_baseline_gate"
            ):
                gate_body = ast.unparse(node)

        assert gate_body, "未找到 evaluate_matrix_baseline_gate 实现"
        for forbidden in (
            "subprocess",
            "_run_pytest",
            "get_engine",
            "sqlalchemy",
            "_matrix_execution_summary",
            "_current_matrix_baseline",
            "_db_residue_total",
        ):
            assert forbidden not in gate_body, forbidden


# ============================================================
# Matrix Baseline Gate Contract 冻结（Phase 3.12 Step 92 · 全合成 · 零执行）
#
# 只冻结 Step 91 Gate 的**语义契约**；不调用 Step 89 execution helper、
# 不读 PostgreSQL、不触发执行缓存、不修改 Step 90 Baseline。
# ============================================================

def _synthetic_baseline(
    *,
    offline: RegressionExecutionSummary,
    db: RegressionExecutionSummary,
    residue: int,
) -> MatrixExecutionBaseline:
    """合成 MatrixExecutionBaseline（供 Step 92 契约测试；无任何真实执行）。"""
    return MatrixExecutionBaseline(
        offline=offline,
        db=db,
        matrix_total=offline.total + db.total,
        matrix_status="PASS" if offline.is_pass and db.is_pass else "FAIL",
        db_residue=residue,
    )


def _synthetic_offline(
    *, total: int = 375, passed: int = 356, skipped: int = 19
) -> RegressionExecutionSummary:
    return RegressionExecutionSummary(
        total=total, passed=passed, skipped=skipped, failed=0, errors=0,
        exit_code=0,
    )


def _synthetic_db(
    *, total: int = 180, passed: int = 180, skipped: int = 0
) -> RegressionExecutionSummary:
    return RegressionExecutionSummary(
        total=total, passed=passed, skipped=skipped, failed=0, errors=0,
        exit_code=0,
    )


class TestMatrixBaselineGateContract:
    @staticmethod
    def _frozen_pair() -> tuple[MatrixExecutionBaseline, MatrixExecutionBaseline]:
        baseline = _synthetic_baseline(
            offline=_synthetic_offline(), db=_synthetic_db(), residue=0
        )
        return baseline, baseline

    def test_gate_status_contract_is_frozen(self) -> None:
        """status 只有 PASS / DRIFT；其它状态一律拒绝（无 WARNING/UNKNOWN/…）。"""
        baseline, current = self._frozen_pair()
        result = evaluate_matrix_baseline_gate(current, baseline)

        assert result.status in {"PASS", "DRIFT"}
        for forbidden in ("WARNING", "UNKNOWN", "PARTIAL", "SKIPPED", "STALE"):
            with pytest.raises(ValueError):
                MatrixBaselineGateResult(
                    status=forbidden,
                    drifts=("NO_BASELINE_DRIFT",),
                    current=current,
                    baseline=baseline,
                )

    def test_drift_type_set_is_frozen(self) -> None:
        assert _MATRIX_BASELINE_DRIFT_TYPES == (
            "NO_BASELINE_DRIFT",
            "OFFLINE_EXECUTION_DRIFT",
            "DB_EXECUTION_DRIFT",
            "MATRIX_TOTAL_DRIFT",
            "MATRIX_STATUS_DRIFT",
            "DB_RESIDUE_DRIFT",
        )
        for forbidden in (
            "PERFORMANCE_DRIFT",
            "DURATION_DRIFT",
            "COLLECTABILITY_DRIFT",
            "NETWORK_DRIFT",
            "LLM_DRIFT",
        ):
            assert forbidden not in _MATRIX_BASELINE_DRIFT_TYPES
        # 未知 drift 类型不得进入 Gate Result
        baseline, current = self._frozen_pair()
        with pytest.raises(ValueError):
            MatrixBaselineGateResult(
                status="DRIFT",
                drifts=("DURATION_DRIFT",),
                current=current,
                baseline=baseline,
            )

    def test_pass_contract_is_exact_equality(self) -> None:
        """PASS ⇔ 五项精确相等 ∧ residue == 0；任一字段微差即 DRIFT（无容差）。"""
        baseline, _ = self._frozen_pair()
        cases = {
            "identical": (baseline, "PASS"),
            "offline-skip+1": (
                _synthetic_baseline(
                    offline=_synthetic_offline(skipped=20, passed=355),
                    db=_synthetic_db(),
                    residue=0,
                ),
                "DRIFT",
            ),
            "db-passed-1": (
                _synthetic_baseline(
                    offline=_synthetic_offline(),
                    db=_synthetic_db(passed=179, skipped=1),
                    residue=0,
                ),
                "DRIFT",
            ),
            "db-total+1": (
                _synthetic_baseline(
                    offline=_synthetic_offline(),
                    db=_synthetic_db(total=181, passed=181),
                    residue=0,
                ),
                "DRIFT",
            ),
            "residue-1": (
                _synthetic_baseline(
                    offline=_synthetic_offline(), db=_synthetic_db(), residue=1
                ),
                "DRIFT",
            ),
        }
        for name, (current, expected) in cases.items():
            assert evaluate_matrix_baseline_gate(
                current, baseline
            ).status == expected, name

    def test_gate_uses_only_equality_no_tolerance_semantics(self) -> None:
        """Gate 不得出现 >= / <= / > / < 或 tolerance / threshold 语义。"""
        tree = ast.parse(_source(_SELF))
        body = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == (
                "evaluate_matrix_baseline_gate"
            ):
                body = ast.unparse(node)

        assert body, "未找到 evaluate_matrix_baseline_gate 实现"
        for forbidden in (">=", "<=", ">", "<"):
            assert f" {forbidden} " not in body, forbidden
        for forbidden in ("tolerance", "threshold", "percent", "rate"):
            assert forbidden not in body, forbidden

    def test_duration_contract_is_excluded(self) -> None:
        """duration 不进入 Baseline 字段 / equality / drift / gate status。"""
        baseline, _ = self._frozen_pair()
        current = _synthetic_baseline(
            offline=RegressionExecutionSummary(
                total=375, passed=356, skipped=19, failed=0, errors=0,
                exit_code=0, duration_seconds=20.0,
            ),
            db=RegressionExecutionSummary(
                total=180, passed=180, skipped=0, failed=0, errors=0,
                exit_code=0, duration_seconds=30.0,
            ),
            residue=0,
        )

        assert "duration" not in {
            item.name for item in fields(MatrixExecutionBaseline)
        }
        assert "duration" not in _MATRIX_BASELINE_DRIFT_TYPES
        assert evaluate_matrix_baseline_gate(current, baseline).status == "PASS"
        assert MATRIX_EXECUTION_BASELINE.offline.duration_seconds is None

    def test_node_hosted_boundary_is_frozen_for_gate(self) -> None:
        """Node-hosted 仅是 Contract Audit：不进入 execution / summary / baseline / gate。"""
        summary_fields = {item.name for item in fields(MatrixExecutionSummary)}
        baseline_fields = {item.name for item in fields(MatrixExecutionBaseline)}

        assert NODE_HOSTED_CONTRACT_CATEGORIES  # 契约审计仍在
        assert "OFFLINE_EXECUTION_CONTRACT" not in CATEGORIES
        for names in (summary_fields, baseline_fields):
            assert not any("node_hosted" in name for name in names)
        assert _SELF not in _offline_suite() and _SELF not in _db_suite()

    def test_baseline_is_read_only_across_gate_evaluation(self) -> None:
        baseline, _ = self._frozen_pair()
        before = (baseline, repr(baseline), baseline.matrix_total)

        for _ in range(3):
            evaluate_matrix_baseline_gate(baseline, baseline)

        assert (baseline, repr(baseline), baseline.matrix_total) == before
        with pytest.raises(Exception) as excinfo:
            baseline.matrix_total = 0  # type: ignore[misc]
        assert "frozen" in type(excinfo.value).__name__.lower()
        assert MATRIX_EXECUTION_BASELINE.matrix_total == 555   # Step 90 未被触碰

    def test_current_is_read_only_across_gate_evaluation(self) -> None:
        baseline, _ = self._frozen_pair()
        current = _synthetic_baseline(
            offline=_synthetic_offline(), db=_synthetic_db(), residue=1
        )
        before = (current, current.offline, current.db, current.db_residue)

        evaluate_matrix_baseline_gate(current, baseline)

        assert (current, current.offline, current.db, current.db_residue) == before
        with pytest.raises(Exception) as excinfo:
            current.offline.total = 0  # type: ignore[misc]
        assert "frozen" in type(excinfo.value).__name__.lower()

    def test_drift_is_deterministic(self) -> None:
        baseline, _ = self._frozen_pair()
        current = _synthetic_baseline(
            offline=_synthetic_offline(total=380, passed=361),
            db=_synthetic_db(),
            residue=0,
        )

        results = [evaluate_matrix_baseline_gate(current, baseline) for _ in range(3)]

        assert results[0] == results[1] == results[2]
        assert all(
            item.drifts == results[0].drifts
            and item.status == results[0].status
            and item.current == current
            and item.baseline == baseline
            for item in results
        )

    def test_composite_drift_returns_all_types(self) -> None:
        """复合 drift 必须返回**全部**类型（不得只返回第一个错误）。"""
        baseline, _ = self._frozen_pair()
        current = _synthetic_baseline(
            offline=_synthetic_offline(total=380, passed=361),
            db=_synthetic_db(passed=179, skipped=1),
            residue=2,
        )
        result = evaluate_matrix_baseline_gate(current, baseline)

        assert result.status == "DRIFT"
        assert set(result.drifts) == {
            "OFFLINE_EXECUTION_DRIFT",
            "DB_EXECUTION_DRIFT",
            "MATRIX_TOTAL_DRIFT",
            "DB_RESIDUE_DRIFT",
        }
        assert len(result.drifts) == 4

    def test_no_drift_contract(self) -> None:
        baseline, current = self._frozen_pair()
        result = evaluate_matrix_baseline_gate(current, baseline)

        assert result.status == "PASS"
        assert result.drifts == ("NO_BASELINE_DRIFT",)
        assert result.is_pass is True

    def test_residue_safety_when_baseline_residue_is_nonzero(self) -> None:
        """安全边界：即使 current == baseline，residue != 0 也必须 DRIFT。"""
        baseline = _synthetic_baseline(
            offline=_synthetic_offline(), db=_synthetic_db(), residue=1
        )
        current = _synthetic_baseline(
            offline=_synthetic_offline(), db=_synthetic_db(), residue=1
        )

        result = evaluate_matrix_baseline_gate(current, baseline)

        assert current == baseline
        assert result.status == "DRIFT"
        assert result.drifts == ("DB_RESIDUE_DRIFT",)

    def test_gate_result_security_contract(self) -> None:
        """复用 Step 91 的 security 断言风格（不重新设计 scanner）；drift 结果同样干净。"""
        baseline, _ = self._frozen_pair()
        current = _synthetic_baseline(
            offline=_synthetic_offline(), db=_synthetic_db(), residue=1
        )
        result = evaluate_matrix_baseline_gate(current, baseline)
        field_names = {item.name for item in fields(MatrixBaselineGateResult)}
        blob = repr(result)

        assert field_names == {"status", "drifts", "current", "baseline"}
        for forbidden in (
            "api_key",
            "password",
            "database_url",
            "authorization",
            "prompt",
            "messages",
            "sql",
            "rag_chunk",
            "tool_args",
            "raw_response",
        ):
            assert forbidden not in field_names
            assert forbidden not in blob
        for sentinel in ("postgresql://", "sk-", "Bearer"):
            assert sentinel not in blob

    def test_contract_tests_use_no_execution_or_db_helper(self) -> None:
        """§十七 自审：本契约类**不**调用 Step 89 execution helper / DB 读取。"""
        tree = ast.parse(_source(_SELF))
        body = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == (
                "TestMatrixBaselineGateContract"
            ):
                body = ast.unparse(node)

        assert body, "未找到 TestMatrixBaselineGateContract"
        # needle 动态拼接：避免断言自身文本被误判为违规
        for forbidden in (
            "_matrix_" + "execution_summary",
            "_current_matrix_" + "baseline",
            "_db_" + "residue_total",
            "_offline_suite_" + "execution",
            "_db_suite_" + "execution",
            "_run_" + "pytest",
        ):
            assert forbidden not in body, forbidden


# ============================================================
# Matrix Baseline Gate CI-Ready Audit（Phase 3.12 Step 93 · 全合成 · 零执行）
#
# CI-ready audit ≠ CI integration：本类只回答"Gate 是否具备被 CI 调用的清晰边界"。
# 不建 CI / 不建 workflow / 不建 CLI / 不改 Gate API / 不刷新 Baseline。
# ============================================================

#: 未来 CI 需要的稳定映射 —— **复用生产 Adapter 的唯一映射**（不创建第二份）。
_CI_EXIT_MAPPING: Mapping[str, int] = _PROD_CI_ADAPTER_EXIT_CODES


class TestMatrixBaselineGateCiReadiness:
    @staticmethod
    def _pair(
        *,
        offline: RegressionExecutionSummary | None = None,
        db: RegressionExecutionSummary | None = None,
        residue: int = 0,
    ) -> tuple[MatrixExecutionBaseline, MatrixExecutionBaseline]:
        baseline = _synthetic_baseline(
            offline=_synthetic_offline(), db=_synthetic_db(), residue=0
        )
        current = _synthetic_baseline(
            offline=offline or baseline.offline,
            db=db or baseline.db,
            residue=residue,
        )
        return current, baseline

    def test_input_contract_shape_is_explicit(self) -> None:
        """输入签名显式、无默认值、无 *args/**kwargs；未知输入被拒绝。"""
        signature = inspect.signature(evaluate_matrix_baseline_gate)
        parameters = list(signature.parameters.values())

        assert [item.name for item in parameters] == ["current", "baseline"]
        for item in parameters:
            assert item.default is inspect.Parameter.empty
            assert item.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
            assert "MatrixExecutionBaseline" in str(item.annotation)

        current, baseline = self._pair()
        for bad in (None, {"offline": 1}, "PASS", (1, 2), object()):
            with pytest.raises(Exception):
                evaluate_matrix_baseline_gate(bad, baseline)  # type: ignore[arg-type]
            with pytest.raises(Exception):
                evaluate_matrix_baseline_gate(current, bad)  # type: ignore[arg-type]

    def test_input_contract_rejects_bare_execution_summary(self) -> None:
        """裸 MatrixExecutionSummary（缺 residue / total / status 视图）不得被静默接受。"""
        current, baseline = self._pair()
        summary = MatrixExecutionSummary(
            offline=current.offline, db=current.db
        )

        with pytest.raises(Exception):
            evaluate_matrix_baseline_gate(summary, baseline)  # type: ignore[arg-type]

    def test_output_contract_status_is_sufficient_for_ci(self) -> None:
        current, baseline = self._pair()
        result = evaluate_matrix_baseline_gate(current, baseline)

        assert set(fields(MatrixBaselineGateResult)[i].name for i in range(4)) == {
            "status",
            "drifts",
            "current",
            "baseline",
        }
        assert result.status in _CI_EXIT_MAPPING
        assert result.is_pass is (result.status == "PASS")

    def test_exit_semantics_are_stable(self) -> None:
        """PASS → 成功 / DRIFT → 失败（语义稳定；**不**在 Gate API 内 sys.exit）。"""
        passing, baseline = self._pair()
        drifting, _ = self._pair(residue=1)
        mapping: dict[str, int] = {}

        for current in (passing, drifting):
            status = evaluate_matrix_baseline_gate(current, baseline).status
            mapping[status] = _CI_EXIT_MAPPING[status]

        assert mapping == {"PASS": 0, "DRIFT": 1}
        # Gate API 本身不产生 exit code（无 sys.exit / 无 exit_code 字段或方法）
        gate_source = inspect.getsource(evaluate_matrix_baseline_gate)
        assert "sys.exit" not in gate_source
        assert "exit_code" not in gate_source
        assert not hasattr(MatrixBaselineGateResult, "exit_code")
        assert not hasattr(MatrixBaselineGateResult, "to_exit_code")

    def test_determinism_over_ten_consecutive_calls(self) -> None:
        current, baseline = self._pair(
            offline=_synthetic_offline(total=380, passed=361)
        )
        results = [
            evaluate_matrix_baseline_gate(current, baseline) for _ in range(10)
        ]

        assert all(item == results[0] for item in results)
        assert results[0].status == "DRIFT"

    def test_input_immutability_before_and_after_gate(self) -> None:
        current, baseline = self._pair(residue=1)
        before = (
            current.offline, current.db, current.matrix_total,
            current.matrix_status, current.db_residue,
            baseline.offline, baseline.db, baseline.matrix_total,
            baseline.matrix_status, baseline.db_residue,
        )

        evaluate_matrix_baseline_gate(current, baseline)

        after = (
            current.offline, current.db, current.matrix_total,
            current.matrix_status, current.db_residue,
            baseline.offline, baseline.db, baseline.matrix_total,
            baseline.matrix_status, baseline.db_residue,
        )
        assert before == after

    def test_output_immutability_fields_are_frozen(self) -> None:
        current, baseline = self._pair()
        result = evaluate_matrix_baseline_gate(current, baseline)

        for field_name in ("status", "drifts", "current", "baseline"):
            with pytest.raises(Exception) as excinfo:
                setattr(result, field_name, None)
            assert "frozen" in type(excinfo.value).__name__.lower()

    def test_drift_completeness_covers_every_axis(self) -> None:
        """每个轴都能进入 drifts（无静默丢弃）；组合时返回全部类型。"""
        _, baseline = self._pair()
        axes = {
            "OFFLINE_EXECUTION_DRIFT": self._pair(
                offline=_synthetic_offline(skipped=20, passed=355)
            )[0],
            "DB_EXECUTION_DRIFT": self._pair(
                db=_synthetic_db(passed=179, skipped=1)
            )[0],
            "DB_RESIDUE_DRIFT": self._pair(residue=1)[0],
        }
        for expected, current in axes.items():
            drifts = evaluate_matrix_baseline_gate(current, baseline).drifts
            assert expected in drifts, (expected, drifts)
            assert "NO_BASELINE_DRIFT" not in drifts

        composite, _ = self._pair(
            offline=_synthetic_offline(total=380, passed=361),
            db=_synthetic_db(passed=179, skipped=1),
            residue=2,
        )
        composite_drifts = set(
            evaluate_matrix_baseline_gate(composite, baseline).drifts
        )
        assert composite_drifts == {
            "OFFLINE_EXECUTION_DRIFT",
            "DB_EXECUTION_DRIFT",
            "MATRIX_TOTAL_DRIFT",
            "DB_RESIDUE_DRIFT",
        }

    def test_no_false_no_baseline_drift(self) -> None:
        """有真实 drift 时不得附带 NO_BASELINE_DRIFT；完全一致才返回它。"""
        identical, baseline = self._pair()
        residue_only, _ = self._pair(residue=1)
        drifted, _ = self._pair(offline=_synthetic_offline(passed=355, skipped=20))

        assert evaluate_matrix_baseline_gate(
            identical, baseline
        ).drifts == ("NO_BASELINE_DRIFT",)
        assert evaluate_matrix_baseline_gate(
            residue_only, baseline
        ).drifts == ("DB_RESIDUE_DRIFT",)
        assert "NO_BASELINE_DRIFT" not in evaluate_matrix_baseline_gate(
            drifted, baseline
        ).drifts

    def test_duration_isolation_for_ci(self) -> None:
        _, baseline = self._pair()
        results = []
        for duration in (1.0, 100.0, 9999.0):
            current = _synthetic_baseline(
                offline=RegressionExecutionSummary(
                    total=375, passed=356, skipped=19, failed=0, errors=0,
                    exit_code=0, duration_seconds=duration,
                ),
                db=RegressionExecutionSummary(
                    total=180, passed=180, skipped=0, failed=0, errors=0,
                    exit_code=0, duration_seconds=duration,
                ),
                residue=0,
            )
            results.append(evaluate_matrix_baseline_gate(current, baseline))

        assert all(item.status == "PASS" for item in results)
        assert all(item.drifts == ("NO_BASELINE_DRIFT",) for item in results)
        assert all(item == results[0] for item in results)

    def test_node_hosted_isolation_for_ci(self) -> None:
        """CI-Ready Gate 不含 node_hosted 输入 / 输出 / 依赖。"""
        gate_source = inspect.getsource(evaluate_matrix_baseline_gate)

        assert "node_hosted" not in gate_source
        assert "OFFLINE_EXECUTION_CONTRACT" not in gate_source
        for dto in (MatrixBaselineGateResult, MatrixExecutionBaseline):
            assert not any(
                "node_hosted" in item.name for item in fields(dto)
            )
        assert NODE_HOSTED_CONTRACT_CATEGORIES  # 契约审计仍在（但不参与 Gate）

    def test_ci_output_security(self) -> None:
        """复用既有 security 断言风格；drift 结果 repr 同样干净。"""
        current, baseline = self._pair(residue=1)
        result = evaluate_matrix_baseline_gate(current, baseline)
        blob = repr(result)
        field_names = {item.name for item in fields(MatrixBaselineGateResult)}

        for forbidden in (
            "api_key",
            "password",
            "database_url",
            "authorization",
            "prompt",
            "messages",
            "sql",
            "rag_chunk",
            "tool_args",
            "raw_response",
        ):
            assert forbidden not in field_names
            assert forbidden not in blob
        for sentinel in ("postgresql://", "sk-", "Bearer"):
            assert sentinel not in blob

    def test_dependency_boundary_of_ci_path(self) -> None:
        """CI 相关路径（summary → gate → result）不依赖 DB / HTTP / LLM / Redis / Kafka。"""
        gate_source = inspect.getsource(evaluate_matrix_baseline_gate)

        for forbidden in (
            "sqlalchemy",
            "psycopg",
            "httpx",
            "requests",
            "openai",
            "redis",
            "kafka",
            "subprocess",
            "get_engine",
        ):
            assert forbidden not in gate_source, forbidden
        module_level = {
            node.module
            for node in ast.parse(_source(_SELF)).body
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.parse(_source(_SELF)).body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        for module in module_level:
            for prefix in (
                "sqlalchemy",
                "psycopg",
                "httpx",
                "requests",
                "openai",
                "redis",
                "kafka",
                "backend.app.db",
            ):
                assert not module.startswith(prefix), module

    def test_no_hidden_execution_in_gate_call_graph(self) -> None:
        """Gate 的模块内调用图 = {compare_matrix_execution_baseline}（无执行 / 无 DB）。"""
        tree = ast.parse(_source(_SELF))
        body: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == (
                "evaluate_matrix_baseline_gate"
            ):
                body = node
        assert body is not None
        called = {
            call.func.id
            for call in ast.walk(body)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }

        # 冻结的模块内调用图（构造器 + 复用比较 + tuple() 归一化；**无**执行器 / DB）
        assert called == {
            "compare_matrix_execution_baseline",
            "MatrixBaselineGateResult",
            "tuple",
        }, sorted(called)
        # needle 动态拼接：避免断言自身文本被误判为违规
        for forbidden in (
            "_run_" + "pytest",
            "_offline_suite_" + "execution",
            "_db_suite_" + "execution",
            "_db_" + "residue_total",
            "get_" + "engine",
            "open",
            "eval",
            "exec",
        ):
            assert forbidden not in called, forbidden
        # 本契约类同样不调用执行 / DB helper
        class_body = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and node.name == "TestMatrixBaselineGateCiReadiness"
        )
        class_source = ast.unparse(class_body)
        for forbidden in (
            "_matrix_" + "execution_summary",
            "_current_matrix_" + "baseline",
            "_db_" + "residue_total",
            "_run_" + "pytest",
        ):
            assert forbidden not in class_source, forbidden

    def test_ci_adapter_boundary_is_not_implemented(self) -> None:
        """CI Adapter / workflow / CLI 均未实现（本阶段只冻结前三层）。"""
        # Step 97 授权：`.github/` 下**仅**一个 workflow（observability-matrix-gate）
        github_dir = _REPO_ROOT / ".github"

        assert github_dir.is_dir()
        workflows = sorted(
            path.name
            for path in (github_dir / "workflows").iterdir()
            if path.is_file()
        )
        assert workflows == ["observability-matrix-gate.yml"], workflows
        assert not (_REPO_ROOT / "scripts/check_baseline.py").exists()
        # Step 96 授权的本地 CLI（orchestration only：无参数 / 无 baseline 写入）
        assert (_REPO_ROOT / "scripts/run_matrix_gate.py").is_file()
        module_level = {
            node.module
            for node in ast.parse(_source(_SELF)).body
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.parse(_source(_SELF)).body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        for cli in ("argparse", "click", "typer"):
            assert cli not in module_level, cli


# ============================================================
# CI Adapter Contract Freeze（Phase 3.12 Step 94 · **不实现** Adapter）
#
# 冻结最后两层之间：`MatrixBaselineGateResult → CI Adapter → exit code`。
# Adapter 唯一职责：读取 `status` 并映射为 process exit code（0 / 1）。
# 不得：执行 Matrix · 读 DB · 重算 drift · 读 baseline · 刷新 baseline · 任何输出持久化。
# ============================================================

#: **冻结的 CI Adapter 退出码契约**（只读映射；**同一个对象**复用生产 Adapter，
#: 不创建第二份定义）。
CI_ADAPTER_EXIT_CODES: Mapping[str, int] = _PROD_CI_ADAPTER_EXIT_CODES

#: Step 95：Adapter **唯一实现**（作为现有 Matrix Contract 的一个节点登记；
#: 不建立第二套 Contract Registry）。
CI_ADAPTER_IMPLEMENTATION = adapt_gate_result_to_exit_code

#: Step 96：本地 Matrix Gate CLI（orchestration layer；只编排，不重新判断）。
CI_GATE_CLI = "scripts/run_matrix_gate.py"

#: Adapter 唯一允许的输入 / 输出（仅 Contract 文本；**不**实现函数）。
CI_ADAPTER_INPUT_TYPE: str = "MatrixBaselineGateResult"
CI_ADAPTER_OUTPUT: str = "process exit code"

#: Adapter **不得**出现的职责（Contract 黑名单；仅作为断言依据）。
CI_ADAPTER_FORBIDDEN_RESPONSIBILITIES: tuple[str, ...] = (
    "execute regression matrix",
    "read postgresql",
    "recompute drift",
    "read baseline",
    "refresh baseline",
    "call llm",
    "network request",
    "persist output",
)


class TestCiAdapterContract:
    @staticmethod
    def _result(status: str, drifts: tuple[str, ...]) -> MatrixBaselineGateResult:
        baseline = _synthetic_baseline(
            offline=_synthetic_offline(), db=_synthetic_db(), residue=0
        )
        return MatrixBaselineGateResult(
            status=status, drifts=drifts, current=baseline, baseline=baseline
        )

    def test_exit_code_contract_is_exactly_zero_and_one(self) -> None:
        assert dict(CI_ADAPTER_EXIT_CODES) == {"PASS": 0, "DRIFT": 1}
        assert set(CI_ADAPTER_EXIT_CODES.values()) == {0, 1}
        for extra in (2, 3, 4, 10, 99):
            assert extra not in CI_ADAPTER_EXIT_CODES.values()
        # 只读：映射不得被改写（无自动 refresh / 无新增业务退出码）
        with pytest.raises(TypeError):
            CI_ADAPTER_EXIT_CODES["DRIFT"] = 0  # type: ignore[index]

    def test_pass_maps_to_exit_code_zero(self) -> None:
        result = self._result("PASS", ("NO_BASELINE_DRIFT",))

        assert result.status == "PASS"
        assert CI_ADAPTER_EXIT_CODES[result.status] == 0

    def test_drift_maps_to_exit_code_one(self) -> None:
        result = self._result("DRIFT", ("DB_RESIDUE_DRIFT",))

        assert CI_ADAPTER_EXIT_CODES[result.status] == 1

    def test_all_drift_types_map_to_exit_code_one(self) -> None:
        """Adapter 不得按 drift 类型选择不同退出码 —— 一律 1。"""
        for drift in (
            "OFFLINE_EXECUTION_DRIFT",
            "DB_EXECUTION_DRIFT",
            "MATRIX_TOTAL_DRIFT",
            "MATRIX_STATUS_DRIFT",
            "DB_RESIDUE_DRIFT",
        ):
            assert drift not in CI_ADAPTER_EXIT_CODES
            result = self._result("DRIFT", (drift,))
            assert CI_ADAPTER_EXIT_CODES[result.status] == 1

    def test_illegal_statuses_are_rejected_by_gate_result_contract(self) -> None:
        """非法 status 由 Gate Result Contract 拒绝；Adapter 不需要处理它们。"""
        for illegal in ("WARNING", "UNKNOWN", "PARTIAL", "SKIPPED", "STALE"):
            with pytest.raises(ValueError):
                self._result(illegal, ("NO_BASELINE_DRIFT",))
            assert illegal not in CI_ADAPTER_EXIT_CODES

    def test_adapter_status_domain_is_exhaustive(self) -> None:
        """Gate 只能产出 PASS / DRIFT；Adapter 映射域必须恰好覆盖两者。"""
        baseline = _synthetic_baseline(
            offline=_synthetic_offline(), db=_synthetic_db(), residue=0
        )
        drifted = _synthetic_baseline(
            offline=_synthetic_offline(), db=_synthetic_db(), residue=1
        )
        observed = {
            evaluate_matrix_baseline_gate(baseline, baseline).status,
            evaluate_matrix_baseline_gate(drifted, baseline).status,
        }

        assert observed == {"PASS", "DRIFT"} == set(CI_ADAPTER_EXIT_CODES)
        assert CI_ADAPTER_INPUT_TYPE == "MatrixBaselineGateResult"
        assert CI_ADAPTER_OUTPUT == "process exit code"

    def test_adapter_contract_does_not_read_baseline_or_current(self) -> None:
        """Adapter 只接受 Gate Result：不得再读取 baseline / current 字段。"""
        tree = ast.parse(_source(_SELF))
        scope = ""
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if any(
                    isinstance(target, ast.Name)
                    and target.id.startswith("CI_ADAPTER_")
                    for target in targets
                ):
                    scope += ast.unparse(node)

        assert scope, "未找到 CI_ADAPTER_* 契约常量"
        # needle 动态拼接：避免断言自身文本被误判为违规
        for forbidden in (
            "MATRIX_" + "EXECUTION_BASELINE",
            ".offline",
            ".db",
            "matrix_" + "total",
            "db_" + "residue",
        ):
            assert forbidden not in scope, forbidden
        for key in CI_ADAPTER_EXIT_CODES:
            assert key in {"PASS", "DRIFT"}

    def test_adapter_contract_does_not_recalculate_drift(self) -> None:
        """映射只由 status 决定；契约里不得存在 drift 计算或比较逻辑。"""
        assert set(CI_ADAPTER_EXIT_CODES) == {"PASS", "DRIFT"}
        assert not any(
            drift in CI_ADAPTER_EXIT_CODES
            for drift in _MATRIX_BASELINE_DRIFT_TYPES
            if drift != "NO_BASELINE_DRIFT"
        )
        assert (
            "compare_" + "matrix_execution_baseline"
            not in str(CI_ADAPTER_EXIT_CODES)
        )
        assert "evaluate_" + "matrix_baseline_gate" not in str(
            CI_ADAPTER_EXIT_CODES
        )

    def test_baseline_ownership_stays_with_gate(self) -> None:
        """Baseline 所有权只在 Gate；Adapter 侧不持有、不刷新。"""
        before = MATRIX_EXECUTION_BASELINE

        for _ in range(3):
            evaluate_matrix_baseline_gate(before, before)

        assert MATRIX_EXECUTION_BASELINE == before
        assert MATRIX_EXECUTION_BASELINE.matrix_total == 555
        assert "baseline" not in CI_ADAPTER_EXIT_CODES
        for responsibility in CI_ADAPTER_FORBIDDEN_RESPONSIBILITIES:
            assert responsibility in {
                "execute regression matrix",
                "read postgresql",
                "recompute drift",
                "read baseline",
                "refresh baseline",
                "call llm",
                "network request",
                "persist output",
            }, responsibility

    def test_adapter_treats_gate_result_as_read_only(self) -> None:
        result = self._result("DRIFT", ("DB_RESIDUE_DRIFT",))

        for field_name in ("status", "drifts", "current", "baseline"):
            with pytest.raises(Exception) as excinfo:
                setattr(result, field_name, None)
            assert "frozen" in type(excinfo.value).__name__.lower()
        for mutator in ("set_status", "update", "refresh", "recompute"):
            assert not hasattr(MatrixBaselineGateResult, mutator)

    def test_ci_adapter_implementation_is_registered(self) -> None:
        """Step 95：Adapter 唯一实现登记在 Matrix Contract，并复用同一映射对象。"""
        assert CI_ADAPTER_IMPLEMENTATION is adapt_gate_result_to_exit_code
        assert CI_ADAPTER_EXIT_CODES is _PROD_CI_ADAPTER_EXIT_CODES
        assert CI_ADAPTER_IMPLEMENTATION(
            self._result("PASS", ("NO_BASELINE_DRIFT",))
        ) == 0
        assert CI_ADAPTER_IMPLEMENTATION(
            self._result("DRIFT", ("DB_RESIDUE_DRIFT",))
        ) == 1

    def test_adapter_contract_has_no_db_network_or_llm_dependency(self) -> None:
        """静态审计（仅 Step 94 契约范围）：无 sys.exit / subprocess / DB / LLM / HTTP。"""
        tree = ast.parse(_source(_SELF))
        contract_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "TestCiAdapterContract"
        )
        # 只扫描方法体（**排除 docstring**），避免契约说明文本被误判为违规
        scope = "".join(
            ast.unparse(statement)
            for member in contract_class.body
            if isinstance(member, ast.FunctionDef)
            for statement in member.body[1:]
        )
        for forbidden in (
            "sys" + ".exit",
            "sub" + "process",
            "sql" + "alchemy",
            "psyc" + "opg",
            "get_" + "engine",
            "Open" + "AI",
            "Deep" + "Seek",
            "htt" + "px",
        ):
            assert forbidden not in scope, forbidden

    def test_adapter_implementation_still_absent(self) -> None:
        """本阶段**未**实现 Adapter：无 .github / 无 CLI / 无注入式入口函数。"""
        tree = ast.parse(_source(_SELF))
        names = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ] + [
            node.name for node in tree.body if isinstance(node, ast.ClassDef)
        ]

        for forbidden in (
            "adapt_gate_to_exit_code",
            "main",
            "cli",
            "run_gate",
            "MatrixCiAdapter",
        ):
            assert forbidden not in names, forbidden
        # Step 97 授权：`.github/workflows/` 下**仅**一个 workflow
        github_dir = _REPO_ROOT / ".github"

        assert github_dir.is_dir()
        workflows = sorted(
            path.name
            for path in (github_dir / "workflows").iterdir()
            if path.is_file()
        )
        assert workflows == ["observability-matrix-gate.yml"], workflows
        assert not (_REPO_ROOT / "scripts/check_baseline.py").exists()
        # Step 96 **授权**的本地 CLI（仅编排：无参数 / 无 argparse / 不刷新 baseline）
        assert (_REPO_ROOT / CI_GATE_CLI).is_file()
        cli_source = (_REPO_ROOT / CI_GATE_CLI).read_text(encoding="utf-8")
        for forbidden in ("argparse", "click", "typer", "sys.argv"):
            assert forbidden not in cli_source, forbidden


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
        result = _offline_suite_execution()

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
    "NODE_HOSTED_CONTRACT_CATEGORIES",
    "NODE_HOSTED_REPRESENTATIVE_NODES",
    "OFFLINE_EXECUTION_SUMMARY_CONTRACT",
    "OFFLINE_EXECUTION_SUMMARY_SNAPSHOT",
    "REPRESENTATIVE_CONTRACT_NODES",
    "CollectReport",
    "FileSpec",
    "RegressionExecutionSummary",
    "CI_ADAPTER_EXIT_CODES",
    "CI_ADAPTER_IMPLEMENTATION",
    "MATRIX_EXECUTION_BASELINE",
    "MatrixBaselineGateResult",
    "TestCiAdapterContract",
    "MatrixExecutionBaseline",
    "MatrixExecutionSummary",
    "TestEntryHygiene",
    "TestMatrixBaselineGate",
    "TestMatrixBaselineGateCiReadiness",
    "TestMatrixBaselineGateContract",
    "TestMatrixExecutionBaseline",
    "TestMatrixExecutionSummary",
    "compare_matrix_execution_baseline",
    "TestNodeHostedContractRegistration",
    "TestNodeHostedContractRegressionIntegration",
    "TestOfflineRegressionExecutionSummary",
    "TestOfflineRegressionExecutionSummaryContract",
    "TestOfflineRegressionSnapshotDrift",
    "classify_snapshot_drift",
    "TestRegressionMatrixCollectability",
    "TestRegressionMatrixContract",
    "TestRegressionMatrixExecutionCoverage",
    "TestRegressionSuite",
    "TestRegistry",
]
