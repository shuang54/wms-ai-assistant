"""Text-to-SQL Result Ground Truth & Fixture Loader（Phase 3.9.13）。

Evaluation infrastructure only — no production logic, no LLM, no network.

Three concepts stay separated (§22 / §23):

```text
tests/fixtures/text_to_sql/text_to_sql_regression.yaml   structural expectations
tests/fixtures/text_to_sql/result_ground_truth.yaml      RESULT-level ground truth
tests/fixtures/text_to_sql/t2s_eval_*.sql                deterministic fixture data
```

Checker is **reused** from Phase 3.9.10
(``ResultExpectation`` / ``TextToSQLResultEvaluationService``); this module
only adds:

- ``load_ground_truth()``  — parse + validate the ground truth file
- ``validate_ground_truth()`` — cross-check against the regression dataset
- ``read_fixture_sql()`` / ``assert_fixture_targets_eval_schema()`` —
  guarantee the fixture only ever touches the dedicated eval schema (§30)

Ground truth source (§24 / §25): hand-designed fixture rows + hand-computed
expected results. Never produced by DeepSeek, never copied from the SQL under
evaluation.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from backend.app.services.text_to_sql_evaluation_service import (
    TextToSQLEvaluationCase,
)
from backend.app.services.text_to_sql_result_evaluation_service import (
    ResultExpectation,
    parse_result_expectation,
)

__all__ = [
    "EVAL_SCHEMA",
    "GROUND_TRUTH_VERSION",
    "GROUND_TRUTH_PATH",
    "FIXTURE_SCHEMA_PATH",
    "FIXTURE_DATA_PATH",
    "GroundTruthDocument",
    "load_ground_truth",
    "validate_ground_truth",
    "read_fixture_sql",
    "assert_fixture_targets_eval_schema",
    "sha256_of",
]


# ============================================================
# 常量 / 路径
# ============================================================

EVAL_SCHEMA: Final[str] = "t2s_eval"
GROUND_TRUTH_VERSION: Final[str] = "1.0"

_FIXTURES_DIR: Final[Path] = (
    Path(__file__).resolve().parents[3]
    / "tests" / "fixtures" / "text_to_sql"
)

GROUND_TRUTH_PATH: Final[Path] = _FIXTURES_DIR / "result_ground_truth.yaml"
FIXTURE_SCHEMA_PATH: Final[Path] = _FIXTURES_DIR / "t2s_eval_schema.sql"
FIXTURE_DATA_PATH: Final[Path] = _FIXTURES_DIR / "t2s_eval_data.sql"

#: 安全边界 case：永远不应出现在 ground truth 中（§28）
SECURITY_CASE_ID: Final[str] = "safety_delete_all_documents"

_TARGET_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "CREATE TABLE",
        re.compile(r"create\s+table\s+(?:if\s+not\s+exists\s+)?([\w\.]+)", re.I),
    ),
    (
        "INSERT INTO",
        re.compile(r"insert\s+into\s+([\w\.]+)", re.I),
    ),
    (
        "ALTER TABLE",
        re.compile(r"alter\s+table\s+([\w\.]+)", re.I),
    ),
    (
        "DROP TABLE",
        re.compile(r"drop\s+table\s+(?:if\s+exists\s+)?([\w\.]+)", re.I),
    ),
    (
        "CREATE SCHEMA",
        re.compile(r"create\s+schema\s+(?:if\s+not\s+exists\s+)?([\w]+)", re.I),
    ),
    (
        "DROP SCHEMA",
        re.compile(r"drop\s+schema\s+(?:if\s+exists\s+)?([\w]+)", re.I),
    ),
)


def sha256_of(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ============================================================
# DTO
# ============================================================

@dataclass(frozen=True)
class GroundTruthDocument:
    """解析后的 ground truth 文件。"""

    version: str
    schema: str
    expectations: dict[str, ResultExpectation]

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(self.expectations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "schema": self.schema,
            "case_ids": list(self.case_ids),
        }


# ============================================================
# Ground truth 加载 / 校验
# ============================================================

def load_ground_truth(
    path: str | Path | None = None,
) -> GroundTruthDocument:
    """读取并解析 ground truth（不访问 DB / LLM）。

    Raises:
        FileNotFoundError / OSError: 文件缺失或不可读。
        yaml.YAMLError: YAML 语法错误。
        ValueError: 结构错误（缺 version / cases，或 case_id 重复）。
        ResultExpectationError: 某条 expectation 配置非法（由 3.9.10 抛出）。
    """
    target = Path(path) if path is not None else GROUND_TRUTH_PATH
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("ground truth must be a mapping (missing content?)")

    version = str(raw.get("version", ""))
    if not version:
        raise ValueError("ground truth requires a 'version' field")

    entries = raw.get("cases")
    if not isinstance(entries, list):
        raise ValueError("ground truth requires a 'cases' list")

    expectations: dict[str, ResultExpectation] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise ValueError(f"cases[{index}] must be a mapping")
        case_id = str(entry.get("case_id", "")).strip()
        if not case_id:
            raise ValueError(f"cases[{index}] requires a non-empty case_id")
        if case_id in expectations:
            raise ValueError(f"duplicate case_id in ground truth: {case_id!r}")
        expectations[case_id] = parse_result_expectation(
            entry.get("expectation")
        )

    return GroundTruthDocument(
        version=version,
        schema=str(raw.get("schema", EVAL_SCHEMA)),
        expectations=expectations,
    )


def validate_ground_truth(
    document: GroundTruthDocument,
    dataset_cases: Sequence[TextToSQLEvaluationCase],
) -> list[str]:
    """对 ground truth 做结构 / 引用校验；返回问题列表（空 = 合法）。"""
    problems: list[str] = []
    known_ids = [case.case_id for case in dataset_cases]
    known_set = set(known_ids)

    for case_id in document.case_ids:
        if case_id not in known_set:
            problems.append(
                f"ground truth references unknown case_id {case_id!r}"
            )

    if SECURITY_CASE_ID in document.expectations:
        problems.append(
            f"security case {SECURITY_CASE_ID!r} must not have result "
            "ground truth (§28)"
        )

    if document.version != GROUND_TRUTH_VERSION:
        problems.append(
            f"ground_truth_version mismatch: expected "
            f"{GROUND_TRUTH_VERSION!r}, got {document.version!r}"
        )

    if document.schema != EVAL_SCHEMA:
        problems.append(
            f"ground truth schema must be {EVAL_SCHEMA!r}, "
            f"got {document.schema!r}"
        )
    return problems


# ============================================================
# Fixture SQL
# ============================================================

def read_fixture_sql() -> tuple[str, str]:
    """返回 (schema_sql, data_sql)。"""
    return (
        FIXTURE_SCHEMA_PATH.read_text(encoding="utf-8"),
        FIXTURE_DATA_PATH.read_text(encoding="utf-8"),
    )


def assert_fixture_targets_eval_schema(sql_text: str) -> list[str]:
    """§30：fixture 语句只能作用于专用 evaluation schema。

    返回违规列表（空 = 合规）。任何指向 ``public`` 或生产 schema 的
    CREATE / DROP / INSERT / ALTER 都会被视为违规 → 调用方应 STOP。
    """
    problems: list[str] = []

    # 只扫描真正的 SQL 语句：先剥离 `--` 行注释（避免注释里提到 public
    # 造成误判）
    statements_only = "\n".join(
        re.sub(r"--.*$", "", line) for line in sql_text.splitlines()
    )

    if re.search(r"\bpublic\b", statements_only, re.I):
        problems.append("fixture SQL references the 'public' schema")

    for label, pattern in _TARGET_PATTERNS:
        for match in pattern.finditer(statements_only):
            target = match.group(1)
            if label.endswith("SCHEMA"):
                if target.lower() != EVAL_SCHEMA:
                    problems.append(
                        f"{label} targets {target!r}, expected "
                        f"{EVAL_SCHEMA!r}"
                    )
                continue
            if not target.lower().startswith(f"{EVAL_SCHEMA}."):
                problems.append(
                    f"{label} targets {target!r}, expected it to be inside "
                    f"{EVAL_SCHEMA!r}"
                )
    return problems
