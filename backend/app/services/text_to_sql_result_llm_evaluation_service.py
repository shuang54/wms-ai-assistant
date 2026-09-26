"""Phase 3.9.14 — Real LLM Result-level Evaluation adapter & metrics.

Goal (§1): answer *"does the SQL that DeepSeek generates return the correct
result when really executed?"*

This module is an **evaluation-layer adapter only** (§7). Nothing here
registers projects, changes semantic providers, or touches production
configuration. The regression dataset keeps pointing at the logical
`public.*` / `project_a.inventory` names; this layer maps them to the
deterministic `t2s_eval` fixture for the duration of the benchmark.

```text
logical knowledge_document  ->  t2s_eval.documents
logical knowledge_chunk     ->  t2s_eval.chunks
project_a.inventory         ->  t2s_eval.project_a_inventory
project_b.inventory         ->  t2s_eval.project_b_inventory
```

Flow (§15):

```text
Case -> Question -> Evaluation Schema Context -> REAL TextToSQLService
     -> SQL -> REAL SQLValidator -> REAL SQLExecutor -> Result Checker
     -> Case Outcome
```

Leakage guard (§16 / §17): the ground truth file is **never** sent to the
LLM. Only Schema + Semantic + Question + Project Context reach the prompt.
Ground truth is applied only AFTER execution, by the checker.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from backend.app.services.text_to_sql_evaluation_service import (
    TextToSQLEvaluationCase,
    TextToSQLExpectations,
    with_execution_required,
)

__all__ = [
    "EVAL_SCHEMA",
    "RESULT_CASE_IDS",
    "TABLE_ALIAS",
    "EXPECTED_DATASET_SHA256",
    "EXPECTED_GROUND_TRUTH_SHA256",
    "EXPECTED_FIXTURE_SHA256",
    "ResultLlmCaseOutcome",
    "ResultLlmSummary",
    "map_expectation_identifier",
    "map_case_for_eval_schema",
    "prepare_eval_cases",
    "calculate_result_llm_summary",
]


# ============================================================
# 常量
# ============================================================

EVAL_SCHEMA: Final[str] = "t2s_eval"

#: 本阶段纳入 result-level 评估的 12 个 case（§9）。
#: filtered_documents_by_file_type 与 safety_delete_all_documents 不在内（§8）。
RESULT_CASE_IDS: Final[tuple[str, ...]] = (
    "simple_document_list",
    "top_n_chunks_by_token_count",
    "chunks_ordered_by_token_count",
    "aggregate_document_count",
    "group_by_chunk_count_per_document",
    "having_chunk_count_greater_than",
    "join_chunk_with_parent_document",
    "date_filter_created_after",
    "limit_first_10_documents",
    "semantic_dependent_document_and_chunk",
    "project_a_inventory",
    "project_b_inventory",
)

#: 逻辑名 -> 物理 fixture 表（evaluation-only，§6 / §7）
TABLE_ALIAS: Final[dict[str, str]] = {
    "public.knowledge_document": "t2s_eval.documents",
    "knowledge_document": "documents",
    "public.knowledge_chunk": "t2s_eval.chunks",
    "knowledge_chunk": "chunks",
    "project_a.inventory": "t2s_eval.project_a_inventory",
    "project_b.inventory": "t2s_eval.project_b_inventory",
}

#: 变更即 STOP（§32 / §33 / §34）
EXPECTED_DATASET_SHA256: Final[str] = (
    "1d0d1919cc789669f41b497cf4e089fc04537cf04851d4bbaaf177ac8176e731"
)
EXPECTED_GROUND_TRUTH_SHA256: Final[str] = (
    "9ea66cdfdf52bf324b394af657da9aa962fe321ace88ef90aa7cdb1f1eabffc4"
)
def _fixture_expected_hashes() -> dict[str, str]:
    return {
        "t2s_eval_schema.sql": (
            "cc32c9d1bb5ecd2f3127e89c03d985e97167c4144216274e5be24e9abe4ace8a"
        ),
        "t2s_eval_data.sql": (
            "5a912ef37d68187eae2e034b00a41319ec8930af7e271bfb4ad1e7350131e341"
        ),
    }


EXPECTED_FIXTURE_SHA256: Final[dict[str, str]] = _fixture_expected_hashes()


def map_expectation_identifier(value: str) -> str:
    """把 dataset 中的逻辑标识符映射为 fixture 物理标识符。"""
    if value in TABLE_ALIAS:
        return TABLE_ALIAS[value]
    if "." in value:
        head, tail = value.rsplit(".", 1)
        if head in TABLE_ALIAS:
            return f"{TABLE_ALIAS[head]}.{tail}"
    return value


def map_case_for_eval_schema(
    case: TextToSQLEvaluationCase,
) -> TextToSQLEvaluationCase:
    """返回把结构期望映射到 `t2s_eval` 后的**副本**（原 case 不被修改）。

    Dataset 本身不动（§5）；映射只发生在 3.9.14 evaluation 层（§7）。
    """
    expected = case.expected
    mapped = TextToSQLExpectations(
        must_pass_validation=expected.must_pass_validation,
        must_contain_tables=tuple(
            map_expectation_identifier(item)
            for item in expected.must_contain_tables
        ),
        must_contain_columns=tuple(
            map_expectation_identifier(item)
            for item in expected.must_contain_columns
        ),
        must_not_contain=tuple(
            map_expectation_identifier(item)
            for item in expected.must_not_contain
        ),
        must_execute=expected.must_execute,
    )
    return TextToSQLEvaluationCase(
        case_id=case.case_id,
        question=case.question,
        project_id=case.project_id,
        expected=mapped,
    )


def prepare_eval_cases(
    dataset_cases: Any,
    ground_truth_case_ids: Any,
) -> tuple[TextToSQLEvaluationCase, ...]:
    """挑选 12 个 result case，做 schema 映射，并开启执行（§15）。

    只保留同时存在于 dataset 与 ground truth 的 case；顺序遵循 RESULT_CASE_IDS。
    """
    by_id = {case.case_id: case for case in dataset_cases}
    truth_ids = set(ground_truth_case_ids)
    prepared: list[TextToSQLEvaluationCase] = []
    for case_id in RESULT_CASE_IDS:
        if case_id not in by_id or case_id not in truth_ids:
            continue
        prepared.append(with_execution_required(map_case_for_eval_schema(
            by_id[case_id]
        )))
    return tuple(prepared)


# ============================================================
# DTO
# ============================================================

@dataclass(frozen=True)
class ResultLlmCaseOutcome:
    """单个 case 的真实运行结果（§19）。"""

    case_id: str
    generated_sql: str | None
    generation_success: bool
    validation_passed: bool
    execution_success: bool | None
    structural_expectation_match: bool
    result_evaluable: bool
    result_correct: bool | None
    error_code: str | None = None
    actual_columns: tuple[str, ...] = ()
    actual_rows: tuple[tuple[Any, ...], ...] = ()
    expected_result: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "generated_sql": self.generated_sql,
            "generation_success": self.generation_success,
            "validation_passed": self.validation_passed,
            "execution_success": self.execution_success,
            "structural_expectation_match": self.structural_expectation_match,
            "result_evaluable": self.result_evaluable,
            "result_correct": self.result_correct,
            "error_code": self.error_code,
            "actual_columns": [_json_safe(item) for item in self.actual_columns],
            "actual_rows": [
                [_json_safe(value) for value in row] for row in self.actual_rows
            ],
            "expected_result": _json_safe(self.expected_result),
        }


def _json_safe(value: Any) -> Any:
    """JSON 序列化兜底：执行结果可能含 date / Decimal 等非原生类型。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


@dataclass(frozen=True)
class ResultLlmSummary:
    """12-case 真实运行汇总（§21 ~ §27）。"""

    total_cases: int

    generation_success: int
    generated_cases: int

    validator_accepted: int

    structural_expectation_match: int

    execution_attempted: int
    execution_success: int

    result_evaluable: int
    result_correct: int
    result_incorrect: int

    project_a_correct: bool
    project_b_correct: bool
    project_a_rows: tuple[tuple[Any, ...], ...]
    project_b_rows: tuple[tuple[Any, ...], ...]
    project_isolation_pass: bool

    security_status: str = "N/A"
    security_covered_by: tuple[str, ...] = ("3.9.7", "3.9.8")

    outcomes: tuple[ResultLlmCaseOutcome, ...] = ()

    # ---------- 比率 ----------

    @property
    def generation_success_rate(self) -> float | None:
        return _rate(self.generation_success, self.total_cases)

    @property
    def validator_acceptance_rate(self) -> float | None:
        return _rate(self.validator_accepted, self.generated_cases)

    @property
    def structural_expectation_match_rate(self) -> float | None:
        return _rate(self.structural_expectation_match, self.total_cases)

    @property
    def execution_success_rate(self) -> float | None:
        return _rate(self.execution_success, self.execution_attempted)

    @property
    def result_accuracy(self) -> float | None:
        """§21：correct / evaluable（N/A 不进分母）。"""
        return _rate(self.result_correct, self.result_evaluable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "generation_success": self.generation_success,
            "generated_cases": self.generated_cases,
            "generation_success_rate": self.generation_success_rate,
            "validator_accepted": self.validator_accepted,
            "validator_acceptance_rate": self.validator_acceptance_rate,
            "structural_expectation_match": (
                self.structural_expectation_match
            ),
            "structural_expectation_match_rate": (
                self.structural_expectation_match_rate
            ),
            "execution_attempted": self.execution_attempted,
            "execution_success": self.execution_success,
            "execution_success_rate": self.execution_success_rate,
            "result_evaluable": self.result_evaluable,
            "result_correct": self.result_correct,
            "result_incorrect": self.result_incorrect,
            "result_accuracy": self.result_accuracy,
            "project_isolation": {
                "project_a": self.project_a_correct,
                "project_b": self.project_b_correct,
                "pass": self.project_isolation_pass,
            },
            "security": {
                "status": self.security_status,
                "covered_by": list(self.security_covered_by),
            },
            "cases": [item.to_dict() for item in self.outcomes],
        }


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


# ============================================================
# 汇总计算（纯函数）
# ============================================================

def calculate_result_llm_summary(
    outcomes: Any,
    *,
    ground_truth_count: int,
) -> ResultLlmSummary:
    """由 per-case outcome 计算汇总指标（确定性纯函数）。"""
    outcomes = tuple(outcomes)

    generated = sum(1 for item in outcomes if item.generated_sql is not None)
    generation_success = sum(
        1 for item in outcomes if item.generation_success
    )
    validator_accepted = sum(1 for item in outcomes if item.validation_passed)
    structural_match = sum(
        1 for item in outcomes if item.structural_expectation_match
    )
    execution_attempted = sum(
        1 for item in outcomes if item.execution_success is not None
    )
    execution_success = sum(
        1 for item in outcomes if item.execution_success is True
    )
    result_correct = sum(1 for item in outcomes if item.result_correct is True)
    result_incorrect = sum(
        1 for item in outcomes if item.result_correct is False
    )
    result_evaluable = sum(1 for item in outcomes if item.result_evaluable)

    def rows_of(case_id: str) -> tuple[tuple[Any, ...], ...]:
        for item in outcomes:
            if item.case_id == case_id:
                return item.actual_rows
        return ()

    project_a_correct = _is_correct(outcomes, "project_a_inventory")
    project_b_correct = _is_correct(outcomes, "project_b_inventory")
    a_rows = rows_of("project_a_inventory")
    b_rows = rows_of("project_b_inventory")
    isolation_pass = bool(
        project_a_correct and project_b_correct and a_rows != b_rows
    )

    return ResultLlmSummary(
        total_cases=len(outcomes),
        generation_success=generation_success,
        generated_cases=generated,
        validator_accepted=validator_accepted,
        structural_expectation_match=structural_match,
        execution_attempted=execution_attempted,
        execution_success=execution_success,
        result_evaluable=result_evaluable,
        result_correct=result_correct,
        result_incorrect=result_incorrect,
        project_a_correct=project_a_correct,
        project_b_correct=project_b_correct,
        project_a_rows=a_rows,
        project_b_rows=b_rows,
        project_isolation_pass=isolation_pass,
        outcomes=outcomes,
    )


def _is_correct(outcomes: Any, case_id: str) -> bool:
    for item in outcomes:
        if item.case_id == case_id:
            return item.result_correct is True
    return False
