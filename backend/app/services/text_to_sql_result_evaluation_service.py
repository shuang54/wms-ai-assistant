"""Text-to-SQL Result-level Correctness Evaluation（Phase 3.9.10）。

在 3.9.9 Generation Quality 之上增加**结果级正确性**一层：

```text
Question → 既有 Text-to-SQL Pipeline → SQL → 既有 Validator → 既有 Executor
        → rows → ResultEvaluationService（deterministic checker）→ 正确 / 错误
```

## 核心原则（§4）

结果正确性必须是**确定性的、可重复的、代码可验证的**：

- 不使用「看起来结果合理」这类主观判断；
- **不使用 LLM-as-a-Judge**（禁止让 DeepSeek 判断结果对不对）；
- 只支持 4 种确定性规则：`exact_rows` / `unordered_rows` / `scalar` /
  `column_values`；
- 不做 fuzzy matching / 语义相似度 / 容差数值比较 / 自动推断期望值。

## 规则（§7~§11）

| type | 语义 |
| --- | --- |
| `exact_rows` | 行数、行顺序、每个字段值全部一致 |
| `unordered_rows` | 忽略行顺序，按 **multiset / Counter** 比较（保留重复行，不用 set） |
| `scalar` | 必须 1 行 1 列，且值相等（COUNT / SUM / MAX / MIN） |
| `column_values` | 只比较指定列的值序列；`ordered` 默认 true，false 时按 multiset 比较 |

## N/A 语义（§6 / §13 / §14）

- 没有 `result_expectation` 的 case → `applicable=false`、`passed=None`；
  **不能**记为失败；
- `result_accuracy = correct / (correct + incorrect)`，
  分母为 0 时为 `None`（JSON `null`），**绝不输出 0%**。

## 边界

- 只做评估，不修改 Text-to-SQL 生产逻辑 / Prompt / Validator / Executor；
- 只读取测试数据库，且被评估的 SQL 本身必须是只读（由既有 Validator 保证）；
- Dataset 里 `result_expectation` 是**可选**字段，缺省行为完全不变。
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

from backend.app.services.text_to_sql_evaluation_service import (
    DEFAULT_REGRESSION_DATASET_PATH,
)

__all__ = [
    "RESULT_TYPE_EXACT_ROWS",
    "RESULT_TYPE_UNORDERED_ROWS",
    "RESULT_TYPE_SCALAR",
    "RESULT_TYPE_COLUMN_VALUES",
    "ResultExpectationError",
    "ResultExpectation",
    "ResultCheckInput",
    "ResultCheckResult",
    "ResultAccuracySummary",
    "TextToSQLResultEvaluationService",
    "parse_result_expectation",
    "load_result_expectations",
]


# ============================================================
# 常量
# ============================================================

RESULT_TYPE_EXACT_ROWS: Final[str] = "exact_rows"
RESULT_TYPE_UNORDERED_ROWS: Final[str] = "unordered_rows"
RESULT_TYPE_SCALAR: Final[str] = "scalar"
RESULT_TYPE_COLUMN_VALUES: Final[str] = "column_values"

_SUPPORTED_TYPES: Final[frozenset[str]] = frozenset(
    {
        RESULT_TYPE_EXACT_ROWS,
        RESULT_TYPE_UNORDERED_ROWS,
        RESULT_TYPE_SCALAR,
        RESULT_TYPE_COLUMN_VALUES,
    }
)

#: 比率精度
RATE_PRECISION: Final[int] = 4

_NOT_EVALUABLE_REASON: Final[str] = "no execution result available"
_NO_EXPECTATION_REASON: Final[str] = "no result_expectation defined"


class ResultExpectationError(ValueError):
    """``result_expectation`` 配置非法（类型未知 / 缺字段 / 结构错误）。"""


# ============================================================
# DTO
# ============================================================

@dataclass(frozen=True)
class ResultExpectation:
    """单条结果级预期（frozen，确定性）。"""

    type: str
    rows: tuple[tuple[Any, ...], ...] = ()
    value: Any = None
    column: str | None = None
    values: tuple[Any, ...] = ()
    ordered: bool = True

    def __post_init__(self) -> None:
        if self.type not in _SUPPORTED_TYPES:
            raise ResultExpectationError(
                f"unsupported result_expectation type: {self.type!r} "
                f"(supported: {sorted(_SUPPORTED_TYPES)})"
            )
        if self.type in (
            RESULT_TYPE_EXACT_ROWS, RESULT_TYPE_UNORDERED_ROWS
        ) and not isinstance(self.rows, tuple):
            raise ResultExpectationError(
                f"{self.type} requires rows to be a list of lists"
            )
        if self.type == RESULT_TYPE_SCALAR and self.value is None:
            raise ResultExpectationError("scalar requires a 'value' field")
        if self.type == RESULT_TYPE_COLUMN_VALUES:
            if not self.column:
                raise ResultExpectationError(
                    "column_values requires a 'column' field"
                )
            if not isinstance(self.values, tuple):
                raise ResultExpectationError(
                    "column_values requires values to be a list"
                )


@dataclass(frozen=True)
class ResultCheckInput:
    """一次结果校验的输入（不含任何执行能力）。"""

    case_id: str
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    expectation: ResultExpectation | None = None
    #: SQL 是否真的执行并产出了结果；False 表示无结果可判（→ N/A）
    executed: bool = True


@dataclass(frozen=True)
class ResultCheckResult:
    """单条校验结果（§13）。"""

    case_id: str
    applicable: bool
    passed: bool | None
    reason: str
    expected: Any = None
    actual: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "applicable": self.applicable,
            "passed": self.passed,
            "reason": self.reason,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(frozen=True)
class ResultAccuracySummary:
    """结果正确性汇总（§14）。"""

    total_cases: int
    applicable_result_cases: int
    correct_result_cases: int
    incorrect_result_cases: int
    not_evaluable_cases: int
    checks: tuple[ResultCheckResult, ...] = ()

    @property
    def result_accuracy(self) -> float | None:
        """correct / (correct + incorrect)；分母 0 → None（N/A，不是 0%）。"""
        denominator = self.correct_result_cases + self.incorrect_result_cases
        if denominator <= 0:
            return None
        return round(self.correct_result_cases / denominator, RATE_PRECISION)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "applicable_result_cases": self.applicable_result_cases,
            "correct_result_cases": self.correct_result_cases,
            "incorrect_result_cases": self.incorrect_result_cases,
            "not_evaluable_cases": self.not_evaluable_cases,
            "result_accuracy": self.result_accuracy,
            "cases": [item.to_dict() for item in self.checks],
        }


# ============================================================
# Checker（纯函数）
# ============================================================

def _check_exact_rows(
    payload: ResultCheckInput, expectation: ResultExpectation
) -> tuple[bool, str]:
    expected_rows = expectation.rows
    actual_rows = payload.rows
    if len(actual_rows) != len(expected_rows):
        return False, (
            f"row count mismatch: expected {len(expected_rows)}, "
            f"got {len(actual_rows)}"
        )
    for index, (expected_row, actual_row) in enumerate(
        zip(expected_rows, actual_rows)
    ):
        if tuple(expected_row) != tuple(actual_row):
            return False, (
                f"row {index} mismatch: expected {tuple(expected_row)}, "
                f"got {tuple(actual_row)}"
            )
    return True, "exact rows matched"


def _check_unordered_rows(
    payload: ResultCheckInput, expectation: ResultExpectation
) -> tuple[bool, str]:
    """multiset 比较（Counter）：忽略顺序，保留重复行。"""
    expected_counter = Counter(tuple(r) for r in expectation.rows)
    actual_counter = Counter(tuple(r) for r in payload.rows)
    if expected_counter != actual_counter:
        missing = expected_counter - actual_counter
        extra = actual_counter - expected_counter
        return False, (
            f"unordered rows mismatch (multiset): "
            f"missing={dict(missing)} unexpected={dict(extra)}"
        )
    return True, "unordered rows matched (multiset)"


def _check_scalar(
    payload: ResultCheckInput, expectation: ResultExpectation
) -> tuple[bool, str]:
    if len(payload.rows) != 1 or len(payload.columns) != 1:
        return False, (
            f"scalar shape mismatch: expected 1 row x 1 column, "
            f"got {len(payload.rows)} x {len(payload.columns)}"
        )
    actual = payload.rows[0][0]
    if actual != expectation.value:
        return False, (
            f"scalar mismatch: expected {expectation.value!r}, got {actual!r}"
        )
    return True, "scalar matched"


def _check_column_values(
    payload: ResultCheckInput, expectation: ResultExpectation
) -> tuple[bool, str]:
    lowered = [str(column).lower() for column in payload.columns]
    target = str(expectation.column).lower()
    if target not in lowered:
        return False, (
            f"column {expectation.column!r} not found in result columns "
            f"{list(payload.columns)}"
        )
    index = lowered.index(target)
    actual = tuple(row[index] for row in payload.rows)
    expected = tuple(expectation.values)

    if expectation.ordered:
        if actual != expected:
            return False, (
                f"column_values mismatch (ordered): expected {expected}, "
                f"got {actual}"
            )
        return True, "column_values matched (ordered)"

    actual_counter = Counter(actual)
    expected_counter = Counter(expected)
    if actual_counter != expected_counter:
        return False, (
            f"column_values mismatch (unordered): expected "
            f"{dict(expected_counter)}, got {dict(actual_counter)}"
        )
    return True, "column_values matched (unordered)"


_CHECKERS = {
    RESULT_TYPE_EXACT_ROWS: _check_exact_rows,
    RESULT_TYPE_UNORDERED_ROWS: _check_unordered_rows,
    RESULT_TYPE_SCALAR: _check_scalar,
    RESULT_TYPE_COLUMN_VALUES: _check_column_values,
}


# ============================================================
# Service
# ============================================================

class TextToSQLResultEvaluationService:
    """结果级正确性评估（纯内存，无 LLM / 无 DB 访问）。"""

    def check(self, payload: ResultCheckInput) -> ResultCheckResult:
        """校验单个 case 的执行结果。

        - 无 ``result_expectation`` → ``applicable=False``、``passed=None``；
        - 有预期但没执行出结果 → ``applicable=True``、``passed=None``（N/A）；
        - 其余 → ``passed=True/False`` + 具体 reason。
        """
        expectation = payload.expectation
        if expectation is None:
            return ResultCheckResult(
                case_id=payload.case_id,
                applicable=False,
                passed=None,
                reason=_NO_EXPECTATION_REASON,
            )
        if not payload.executed:
            return ResultCheckResult(
                case_id=payload.case_id,
                applicable=True,
                passed=None,
                reason=_NOT_EVALUABLE_REASON,
                expected=_expected_view(expectation),
                actual=None,
            )
        checker = _CHECKERS[expectation.type]
        passed, reason = checker(payload, expectation)
        return ResultCheckResult(
            case_id=payload.case_id,
            applicable=True,
            passed=passed,
            reason=reason,
            expected=_expected_view(expectation),
            actual=_actual_view(payload),
        )

    def summarize(
        self, checks: Sequence[ResultCheckResult]
    ) -> ResultAccuracySummary:
        return ResultAccuracySummary(
            total_cases=len(checks),
            applicable_result_cases=sum(1 for c in checks if c.applicable),
            correct_result_cases=sum(1 for c in checks if c.passed is True),
            incorrect_result_cases=sum(1 for c in checks if c.passed is False),
            not_evaluable_cases=sum(
                1 for c in checks if c.applicable and c.passed is None
            ),
            checks=tuple(checks),
        )


def _expected_view(expectation: ResultExpectation) -> Any:
    if expectation.type == RESULT_TYPE_SCALAR:
        return expectation.value
    if expectation.type == RESULT_TYPE_COLUMN_VALUES:
        return {
            "column": expectation.column,
            "values": list(expectation.values),
            "ordered": expectation.ordered,
        }
    return [list(row) for row in expectation.rows]


def _actual_view(payload: ResultCheckInput) -> Any:
    return {
        "columns": list(payload.columns),
        "rows": [list(row) for row in payload.rows],
    }


# ============================================================
# Dataset 解析（result_expectation 为**可选**字段）
# ============================================================

def parse_result_expectation(raw: Any) -> ResultExpectation:
    """解析 ``result_expectation`` 块；非法配置抛 ``ResultExpectationError``。"""
    if not isinstance(raw, Mapping):
        raise ResultExpectationError(
            f"result_expectation must be a mapping "
            f"(got {type(raw).__name__})"
        )
    unknown = set(raw) - {
        "type", "rows", "value", "column", "values", "ordered",
    }
    if unknown:
        raise ResultExpectationError(
            f"result_expectation has unknown keys {sorted(unknown)}"
        )
    if "type" not in raw:
        raise ResultExpectationError("result_expectation requires 'type'")

    raw_rows = raw.get("rows")
    rows: tuple[tuple[Any, ...], ...] = ()
    if raw_rows is not None:
        if isinstance(raw_rows, (str, bytes)) or not isinstance(
            raw_rows, (list, tuple)
        ):
            raise ResultExpectationError("rows must be a list of lists")
        rows = tuple(
            tuple(row) if isinstance(row, (list, tuple)) else (row,)
            for row in raw_rows
        )

    raw_values = raw.get("values")
    values: tuple[Any, ...] = ()
    if raw_values is not None:
        if isinstance(raw_values, (str, bytes)) or not isinstance(
            raw_values, (list, tuple)
        ):
            raise ResultExpectationError("values must be a list")
        values = tuple(raw_values)

    return ResultExpectation(
        type=str(raw["type"]),
        rows=rows,
        value=raw.get("value"),
        column=raw.get("column"),
        values=values,
        ordered=bool(raw.get("ordered", True)),
    )


def load_result_expectations(
    path: str | Path | None = None,
) -> dict[str, ResultExpectation]:
    """读取 dataset 中的 ``result_expectation``（可选字段）。

    Returns:
        ``case_id → ResultExpectation``；没有该字段的 case 不会出现在结果里。

    Raises:
        ResultExpectationError: 配置非法。
        OSError / yaml.YAMLError: 文件不可读 / YAML 语法错误。
    """
    target = Path(path) if path is not None else DEFAULT_REGRESSION_DATASET_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        return {}
    entries = data.get("cases")
    if not isinstance(entries, list):
        return {}

    expectations: dict[str, ResultExpectation] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            continue
        raw = entry.get("result_expectation")
        if raw is None:
            continue
        case_id = str(entry.get("id", f"cases[{index}]"))
        expectations[case_id] = parse_result_expectation(raw)
    return expectations
