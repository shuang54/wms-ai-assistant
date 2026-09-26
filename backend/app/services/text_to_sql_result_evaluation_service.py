"""Text-to-SQL Result-level Correctness Evaluation.

Phase 3.9.10 introduces strict 4 checkers; Phase 3.9.16 adds the **semantic**
view (required columns + values correct). Both coexist; the same ground
truth entry may carry both ``expectation`` (strict) and
``semantic_expectation`` (semantic).

Phase 3.9.18 makes the semantic view alias-aware: a required column may be
matched by an entity-scoped semantic alias instead of a literal name.
Alias resolution NEVER applies to the strict projection checkers.

```text
Question -> Pipeline -> SQL -> Validator -> Executor -> rows
                                                     -> ResultEvaluationService
                                                        |- strict  -> passed
                                                        \- semantic -> passed
```

N/A semantics:

- Neither given -> applicable=False, both passed=None.
- Given but no execution result -> applicable=True, both passed=None.
- Only one side given -> the other side stays None (independent scoring).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from backend.app.services.text_to_sql_column_alias_evaluation_service import (
    DIAGNOSTIC_ALIAS_MATCH,
    DIAGNOSTIC_EXACT_MATCH,
    DIAGNOSTIC_UNKNOWN_COLUMN,
    MATCH_ALIAS,
    MATCH_EXACT,
    MATCH_NONE,
    ROLE_FORBIDDEN,
    ROLE_OPTIONAL,
    ROLE_REQUIRED,
    ColumnAliasContext,
    ColumnAliasResolution,
    alias_context_for_case,
    resolve_declared_columns,
)
from backend.app.services.text_to_sql_evaluation_service import (
    DEFAULT_REGRESSION_DATASET_PATH,
)

__all__ = [
    "RESULT_TYPE_EXACT_ROWS",
    "RESULT_TYPE_UNORDERED_ROWS",
    "RESULT_TYPE_SCALAR",
    "RESULT_TYPE_COLUMN_VALUES",
    "SEMANTIC_ROW_MATCHING_UNORDERED",
    "SEMANTIC_ROW_MATCHING_ORDERED",
    "SEMANTIC_CATEGORY_MISSING_REQUIRED",
    "SEMANTIC_CATEGORY_FORBIDDEN",
    "SEMANTIC_CATEGORY_WRONG_VALUE",
    "SEMANTIC_CATEGORY_WRONG_ROW_SET",
    "SEMANTIC_CATEGORY_WRONG_ORDER",
    "SEMANTIC_CATEGORY_UNDECLARED_EXTRA",
    "SEMANTIC_CATEGORY_DUPLICATE_ROW",
    "ResultExpectationError",
    "ResultExpectation",
    "SemanticExpectation",
    "ResultCheckInput",
    "ResultCheckResult",
    "ResultAccuracySummary",
    "TextToSQLResultEvaluationService",
    "parse_result_expectation",
    "parse_semantic_expectation",
    "load_result_expectations",
    "load_semantic_expectations",
    # Phase 3.9.18 — re-exported alias primitives (evaluation layer only).
    "DIAGNOSTIC_ALIAS_MATCH",
    "DIAGNOSTIC_EXACT_MATCH",
    "DIAGNOSTIC_UNKNOWN_COLUMN",
    "MATCH_ALIAS",
    "MATCH_EXACT",
    "MATCH_NONE",
    "ColumnAliasContext",
    "ColumnAliasResolution",
    "alias_context_for_case",
    "resolve_required_columns",
]


# ============================================================
# Constants
# ============================================================

RESULT_TYPE_EXACT_ROWS: Final[str] = "exact_rows"
RESULT_TYPE_UNORDERED_ROWS: Final[str] = "unordered_rows"
RESULT_TYPE_SCALAR: Final[str] = "scalar"
RESULT_TYPE_COLUMN_VALUES: Final[str] = "column_values"

_SUPPORTED_TYPES: Final[frozenset[str]] = frozenset({
    RESULT_TYPE_EXACT_ROWS,
    RESULT_TYPE_UNORDERED_ROWS,
    RESULT_TYPE_SCALAR,
    RESULT_TYPE_COLUMN_VALUES,
})

SEMANTIC_ROW_MATCHING_UNORDERED: Final[str] = "unordered"
SEMANTIC_ROW_MATCHING_ORDERED: Final[str] = "ordered"
SEMANTIC_ROW_MATCHING_VALUES: Final[frozenset[str]] = frozenset({
    SEMANTIC_ROW_MATCHING_UNORDERED, SEMANTIC_ROW_MATCHING_ORDERED,
})

SEMANTIC_CATEGORY_MISSING_REQUIRED: Final[str] = "MISSING_REQUIRED_COLUMN"
SEMANTIC_CATEGORY_FORBIDDEN: Final[str] = "EXTRA_FORBIDDEN_COLUMN"
SEMANTIC_CATEGORY_WRONG_VALUE: Final[str] = "WRONG_COLUMN_VALUE"
SEMANTIC_CATEGORY_WRONG_ROW_SET: Final[str] = "WRONG_ROW_SET"
SEMANTIC_CATEGORY_WRONG_ORDER: Final[str] = "COLUMN_ORDER_MISMATCH"
SEMANTIC_CATEGORY_UNDECLARED_EXTRA: Final[str] = "UNDECLARED_EXTRA_COLUMN"
SEMANTIC_CATEGORY_DUPLICATE_ROW: Final[str] = "DUPLICATE_ROW"

RATE_PRECISION: Final[int] = 4
_NOT_EVALUABLE_REASON: Final[str] = "no execution result available"
_NO_EXPECTATION_REASON: Final[str] = "no result_expectation defined"


class ResultExpectationError(ValueError):
    """``result_expectation`` / ``semantic_expectation`` config invalid."""


@dataclass(frozen=True)
class ResultExpectation:
    """Single result-level expectation (frozen, deterministic)."""
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
class SemanticExpectation:
    """Business-semantic result expectation (Phase 3.9.16).

    Only cares about whether required columns appear + required values are
    correct. Forbidden columns cause failure. Optional columns are
    advisory. Undeclared extra columns: allowed but logged as
    UNDECLARED_EXTRA warning (not a hard failure).
    """
    required_columns: tuple[str, ...]
    optional_columns: tuple[str, ...] = ()
    forbidden_columns: tuple[str, ...] = ()
    expected_rows: tuple[tuple[Any, ...], ...] = ()
    row_matching: str = SEMANTIC_ROW_MATCHING_UNORDERED

    def __post_init__(self) -> None:
        if not self.required_columns:
            raise ResultExpectationError(
                "semantic_expectation requires at least one required_column"
            )
        if self.row_matching not in SEMANTIC_ROW_MATCHING_VALUES:
            raise ResultExpectationError(
                f"row_matching must be one of "
                f"{sorted(SEMANTIC_ROW_MATCHING_VALUES)}, "
                f"got {self.row_matching!r}"
            )
        # Phase 3.9.17 consistency rules (section §十二).
        req_set = {str(c).lower() for c in self.required_columns}
        opt_set = {str(c).lower() for c in self.optional_columns}
        forbidden_set = {str(c).lower() for c in self.forbidden_columns}
        # Rule 2: required ∩ optional == ∅.
        if req_set & opt_set:
            raise ResultExpectationError(
                "semantic_expectation: required_columns and "
                "optional_columns must be disjoint; "
                f"intersection={sorted(req_set & opt_set)}"
            )
        # Rule 3: required ∩ forbidden == ∅.
        if req_set & forbidden_set:
            raise ResultExpectationError(
                "semantic_expectation: required_columns and "
                "forbidden_columns must be disjoint; "
                f"intersection={sorted(req_set & forbidden_set)}"
            )
        # Rule 4: each expected_row must have width == len(required_columns).
        if self.expected_rows:
            expected_width = len(self.required_columns)
            for index, row in enumerate(self.expected_rows):
                if len(row) != expected_width:
                    raise ResultExpectationError(
                        f"semantic_expectation.expected_rows[{index}] has "
                        f"{len(row)} values but required_columns has "
                        f"{expected_width} columns"
                    )


@dataclass(frozen=True)
class ResultCheckInput:
    case_id: str
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    expectation: ResultExpectation | None = None
    semantic_expectation: SemanticExpectation | None = None
    executed: bool = True
    #: Phase 3.9.19 — explicit per-column alias context. When provided it
    #: takes precedence over ``alias_context_for_case(case_id)``; when None
    #: the case-level evaluation metadata is used. Permits projection
    #: variants (synthetic semantic expectations) to carry their own
    #: entity context without mutating the registry.
    alias_context: Mapping[str, ColumnAliasContext] | None = None


@dataclass(frozen=True)
class ResultCheckResult:
    case_id: str
    applicable: bool
    passed: bool | None
    semantic_passed: bool | None
    reason: str
    semantic_reason: str = ""
    semantic_categories: tuple[str, ...] = ()
    #: Phase 3.9.18 — per required-column alias diagnostics
    #: (``matched_by`` = exact / alias / none). Informational only:
    #: ALIAS_MATCH is neither a warning nor an error (section §九).
    semantic_column_matches: tuple[ColumnAliasResolution, ...] = ()
    expected: Any = None
    actual: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "applicable": self.applicable,
            "passed": self.passed,
            "semantic_passed": self.semantic_passed,
            "reason": self.reason,
            "semantic_reason": self.semantic_reason,
            "semantic_categories": list(self.semantic_categories),
            "semantic_column_matches": [
                item.to_dict() for item in self.semantic_column_matches
            ],
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass(frozen=True)
class ResultAccuracySummary:
    total_cases: int
    applicable_result_cases: int
    correct_result_cases: int
    incorrect_result_cases: int
    not_evaluable_cases: int

    semantic_evaluable_cases: int = 0
    semantic_correct_cases: int = 0
    semantic_incorrect_cases: int = 0

    checks: tuple[ResultCheckResult, ...] = ()

    @property
    def result_accuracy(self) -> float | None:
        denom = self.correct_result_cases + self.incorrect_result_cases
        if denom <= 0:
            return None
        return round(self.correct_result_cases / denom, RATE_PRECISION)

    @property
    def semantic_result_correctness(self) -> float | None:
        denom = self.semantic_correct_cases + self.semantic_incorrect_cases
        if denom <= 0:
            return None
        return round(
            self.semantic_correct_cases / denom, RATE_PRECISION
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "applicable_result_cases": self.applicable_result_cases,
            "correct_result_cases": self.correct_result_cases,
            "incorrect_result_cases": self.incorrect_result_cases,
            "not_evaluable_cases": self.not_evaluable_cases,
            "result_accuracy": self.result_accuracy,
            "semantic_evaluable_cases": self.semantic_evaluable_cases,
            "semantic_correct_cases": self.semantic_correct_cases,
            "semantic_incorrect_cases": self.semantic_incorrect_cases,
            "semantic_result_correctness": self.semantic_result_correctness,
            "cases": [item.to_dict() for item in self.checks],
        }


def _check_exact_rows(payload, expectation):
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


def _check_unordered_rows(payload, expectation):
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


def _check_scalar(payload, expectation):
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


def _check_column_values(payload, expectation):
    lowered = [str(c).lower() for c in payload.columns]
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


def _extract_required_values(
    actual_columns, actual_rows, required_columns, resolutions=None
):
    """Extract required-column values from the actual rows.

    Phase 3.9.18: when ``resolutions`` is supplied the column index comes
    from the alias resolution (exact OR entity-scoped alias) instead of a
    plain name lookup. Without ``resolutions`` the 3.9.16 behaviour
    (strict name lookup) is preserved.
    """
    lowered_actual = [str(c).lower() for c in actual_columns]
    if resolutions is None:
        indices = [
            lowered_actual.index(str(req).lower()) for req in required_columns
        ]
    else:
        indices = [
            lowered_actual.index(str(item.actual_column).lower())
            for item in resolutions
        ]
    return tuple(tuple(row[i] for i in indices) for row in actual_rows)


def _check_semantic_result(payload, expectation):
    """Business-semantic checker (3.9.16; alias-aware since 3.9.18).

    Categories (multi-label): MISSING_REQUIRED / FORBIDDEN / WRONG_VALUE /
    WRONG_ROW_SET / WRONG_ORDER / UNDECLARED_EXTRA / DUPLICATE_ROW.
    Hard failure = any category other than UNDECLARED_EXTRA.

    Returns ``(passed, reason, categories, resolutions)`` where
    ``resolutions`` carries every per-column alias diagnostic, tagged by
    ``role`` (required / optional / forbidden). Alias matches are recorded
    as diagnostics only — they never change the hard-failure rule, and they
    are never applied to the strict projection checkers (section §八).

    Phase 3.9.19: ONE resolver and ONE context map are applied to the
    required + optional + forbidden columns so that a column can never be
    both an alias of a declared concept and an undeclared extra column.
    """
    categories = []
    failures = []
    required_lower = [str(c).lower() for c in expectation.required_columns]
    optional_lower = {str(c).lower() for c in expectation.optional_columns}
    forbidden_lower = {str(c).lower() for c in expectation.forbidden_columns}

    # Phase 3.9.19: unified resolution across required/optional/forbidden.
    contexts = payload.alias_context
    if contexts is None:
        contexts = alias_context_for_case(payload.case_id)
    resolutions = resolve_declared_columns(
        expectation.required_columns,
        expectation.optional_columns,
        expectation.forbidden_columns,
        payload.columns,
        contexts,
    )
    required_matches = [r for r in resolutions if r.role == ROLE_REQUIRED]
    optional_matches = [r for r in resolutions if r.role == ROLE_OPTIONAL]
    forbidden_matches = [r for r in resolutions if r.role == ROLE_FORBIDDEN]

    missing_required = [
        item.expected_column for item in required_matches
        if item.match_kind == MATCH_NONE
    ]
    if missing_required:
        categories.append(SEMANTIC_CATEGORY_MISSING_REQUIRED)
        failures.append(
            f"missing required columns: {sorted(missing_required)}"
        )

    # Forbidden is alias-aware (section §八): a forbidden expected column
    # that resolves to an actual column is treated as present. An
    # unresolvable forbidden column stays unmatched -> not flagged (the
    # safe "context cannot disambiguate" policy).
    forbidden_present = sorted(
        f"{item.expected_column}<-{item.actual_column}"
        for item in forbidden_matches if item.actual_column is not None
    )
    if forbidden_present:
        categories.append(SEMANTIC_CATEGORY_FORBIDDEN)
        failures.append(f"forbidden columns present: {forbidden_present}")

    declared = set(required_lower) | optional_lower | forbidden_lower
    # Every alias-matched actual column is "declared" by construction: it
    # is the same business concept, never an undeclared extra (section §七).
    for item in resolutions:
        if item.actual_column is not None:
            declared.add(str(item.actual_column).lower())
    undeclared = sorted(
        str(c).lower() for c in payload.columns
        if str(c).lower() not in declared
    )
    if undeclared:
        categories.append(SEMANTIC_CATEGORY_UNDECLARED_EXTRA)
        failures.append(
            f"undeclared extra columns (warning): {undeclared}"
        )

    if not missing_required and not forbidden_present:
        extracted = _extract_required_values(
            payload.columns,
            payload.rows,
            expectation.required_columns,
            required_matches,
        )
        expected = tuple(tuple(r) for r in expectation.expected_rows)
        if expectation.row_matching == SEMANTIC_ROW_MATCHING_ORDERED:
            if extracted != expected:
                categories.append(SEMANTIC_CATEGORY_WRONG_ORDER)
                failures.append(
                    f"required-column values mismatch (ordered): "
                    f"expected={expected}, got={extracted}"
                )
        else:
            if Counter(extracted) != Counter(expected):
                categories.append(SEMANTIC_CATEGORY_WRONG_ROW_SET)
                missing_values = Counter(expected) - Counter(extracted)
                if missing_values:
                    categories.append(SEMANTIC_CATEGORY_WRONG_VALUE)
                    failures.append(
                        f"required-column values missing: "
                        f"{dict(missing_values)}"
                    )
                else:
                    failures.append(
                        "required-column values mismatch (unordered): "
                        f"expected={expected}, got={extracted}"
                    )

    hard = [c for c in categories if c != SEMANTIC_CATEGORY_UNDECLARED_EXTRA]
    passed = not hard
    reason = "; ".join(failures) if failures else "semantic result matched"
    return passed, reason, tuple(categories), tuple(resolutions)


class TextToSQLResultEvaluationService:
    def check(self, payload):
        expectation = payload.expectation
        semantic = payload.semantic_expectation

        if expectation is None and semantic is None:
            return ResultCheckResult(
                case_id=payload.case_id,
                applicable=False, passed=None, semantic_passed=None,
                reason=_NO_EXPECTATION_REASON,
            )

        if not payload.executed:
            return ResultCheckResult(
                case_id=payload.case_id,
                applicable=True, passed=None, semantic_passed=None,
                reason=_NOT_EVALUABLE_REASON,
                expected=_expected_view(expectation) if expectation else None,
                actual=None,
            )

        passed = None
        reason = _NO_EXPECTATION_REASON
        if expectation is not None:
            checker = _CHECKERS[expectation.type]
            passed, reason = checker(payload, expectation)

        semantic_passed = None
        semantic_reason = ""
        semantic_categories = ()
        semantic_column_matches = ()
        if semantic is not None:
            (
                semantic_passed, semantic_reason, semantic_categories,
                semantic_column_matches,
            ) = _check_semantic_result(payload, semantic)

        return ResultCheckResult(
            case_id=payload.case_id,
            applicable=True,
            passed=passed,
            semantic_passed=semantic_passed,
            reason=reason,
            semantic_reason=semantic_reason,
            semantic_categories=semantic_categories,
            semantic_column_matches=semantic_column_matches,
            expected=_expected_view(expectation) if expectation else None,
            actual=_actual_view(payload),
        )

    def summarize(self, checks):
        return ResultAccuracySummary(
            total_cases=len(checks),
            applicable_result_cases=sum(1 for c in checks if c.applicable),
            correct_result_cases=sum(1 for c in checks if c.passed is True),
            incorrect_result_cases=sum(1 for c in checks if c.passed is False),
            not_evaluable_cases=sum(
                1 for c in checks if c.applicable and c.passed is None
            ),
            semantic_evaluable_cases=sum(
                1 for c in checks if c.semantic_passed is not None
            ),
            semantic_correct_cases=sum(
                1 for c in checks if c.semantic_passed is True
            ),
            semantic_incorrect_cases=sum(
                1 for c in checks if c.semantic_passed is False
            ),
            checks=tuple(checks),
        )


def _expected_view(expectation):
    if expectation.type == RESULT_TYPE_SCALAR:
        return expectation.value
    if expectation.type == RESULT_TYPE_COLUMN_VALUES:
        return {
            "column": expectation.column,
            "values": list(expectation.values),
            "ordered": expectation.ordered,
        }
    return [list(row) for row in expectation.rows]


def _actual_view(payload):
    return {
        "columns": list(payload.columns),
        "rows": [list(row) for row in payload.rows],
    }


def parse_result_expectation(raw):
    if not isinstance(raw, Mapping):
        raise ResultExpectationError(
            "result_expectation must be a mapping"
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
    rows = ()
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
    values = ()
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


def load_result_expectations(path=None):
    """读取 dataset 中的 ``result_expectation``（3.9.10）。"""
    target = Path(path) if path is not None else DEFAULT_REGRESSION_DATASET_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        return {}
    entries = data.get("cases")
    if not isinstance(entries, list):
        return {}
    expectations = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            continue
        raw = entry.get("result_expectation")
        if raw is None:
            continue
        case_id = str(entry.get("id", f"cases[{index}]"))
        expectations[case_id] = parse_result_expectation(raw)
    return expectations


_SEMANTIC_ALLOWED_KEYS = frozenset({
    "required_columns", "optional_columns", "forbidden_columns",
    "expected_rows", "row_matching",
})


def parse_semantic_expectation(raw):
    if not isinstance(raw, Mapping):
        raise ResultExpectationError(
            "semantic_expectation must be a mapping"
        )
    unknown = set(raw) - _SEMANTIC_ALLOWED_KEYS
    if unknown:
        raise ResultExpectationError(
            f"semantic_expectation has unknown keys {sorted(unknown)}"
        )

    def _string_tuple(value, field_name):
        if value is None:
            return ()
        if isinstance(value, (str, bytes)) or not isinstance(value, list):
            raise ResultExpectationError(
                f"{field_name} must be a list of strings"
            )
        out = []
        for entry in value:
            if isinstance(entry, (list, tuple)) and not isinstance(entry, str):
                if len(entry) != 1:
                    raise ResultExpectationError(
                        f"{field_name} entries must be strings, "
                        f"got {entry!r}"
                    )
                out.append(str(entry[0]))
            else:
                out.append(str(entry))
        return tuple(out)

    required = _string_tuple(raw.get("required_columns"), "required_columns")
    optional = _string_tuple(raw.get("optional_columns"), "optional_columns")
    forbidden = _string_tuple(
        raw.get("forbidden_columns"), "forbidden_columns"
    )

    expected_rows = ()
    raw_rows = raw.get("expected_rows")
    if raw_rows is not None:
        if isinstance(raw_rows, (str, bytes)) or not isinstance(raw_rows, list):
            raise ResultExpectationError(
                "expected_rows must be a list of lists"
            )
        expected_rows = tuple(
            tuple(row) if isinstance(row, (list, tuple)) else (row,)
            for row in raw_rows
        )

    row_matching = str(
        raw.get("row_matching", SEMANTIC_ROW_MATCHING_UNORDERED)
    )

    return SemanticExpectation(
        required_columns=required,
        optional_columns=optional,
        forbidden_columns=forbidden,
        expected_rows=expected_rows,
        row_matching=row_matching,
    )


def load_semantic_expectations(path=None):
    """读取 dataset 中的 ``semantic_expectation``（3.9.16）。

    与 ``load_result_expectations`` 共源（``text_to_sql_regression.yaml``），
    保证 3.9.10 dataset hash 不变。
    """
    target = Path(path) if path is not None else DEFAULT_REGRESSION_DATASET_PATH
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        return {}
    entries = data.get("cases")
    if not isinstance(entries, list):
        return {}
    expectations = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            continue
        raw = entry.get("semantic_expectation")
        if raw is None:
            continue
        case_id = str(entry.get("id", f"cases[{index}]"))
        expectations[case_id] = parse_semantic_expectation(raw)
    return expectations
