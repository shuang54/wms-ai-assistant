"""Text-to-SQL Generation Quality Evaluation（Phase 3.9.9）。

在 3.9.3~3.9.8 已有能力之上建立**生成质量基线**。本模块只做评估 / 分析：

- 不修改任何生产逻辑（Generator / Validator / Executor / Runner / Prompt /
  Semantic / Dataset 全部原样）；
- 复用 3.9.6 Taxonomy 做 generation / validation / expectation 分类
  与失败分布（**不创造新的失败类别**）；
- 纯函数 + frozen DTO，可确定性重算。

## 三个概念必须分开（§四）

```text
1) generation success : LLM 是否产出可解析 SQL
2) validation accepted: 该 SQL 是否通过真实 Validator
3) result correct     : SQL 执行结果是否符合 case 的 expected result
```

例如「生成成功 → Validator 通过 → 执行成功 → 结果错误」应统计为
`generation=success / validation=accepted / execution=success / result=incorrect`，
不能简单算成成功。

## 关于 result correctness（§五）

3.9.3 Regression Dataset 的 ``expected`` **只有结构期望**
（must_pass_validation / must_contain_tables / must_contain_columns /
must_not_contain / must_execute），**没有任何 expected result 数据**。

因此本模块**不臆造** expected result：``result_outcomes`` 缺省为 None 时，
全部 case 的 result 一律为 ``n/a``，``result_accuracy`` 为 ``None``。
仅当调用方显式提供结果级判定（``Mapping[case_id, bool]``）时才统计。

## N/A 语义（§三 / §八）

所有比率的**分母只包含有证据的 case**，N/A 不参与比例计算；
分母为 0 时比率为 ``None``（JSON 里是 ``null``），绝不为 0。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Final

from backend.app.services.text_to_sql_evaluation_service import (
    TextToSQLEvaluationCase,
    TextToSQLEvaluationResult,
)
from backend.app.services.text_to_sql_evaluation_taxonomy import (
    GENERATION_FAILURE as TAXONOMY_GENERATION_FAILURE,
)
from backend.app.services.text_to_sql_evaluation_taxonomy import (
    GENERATION_SUCCESS as TAXONOMY_GENERATION_SUCCESS,
)
from backend.app.services.text_to_sql_evaluation_taxonomy import (
    GENERATION_UNKNOWN as TAXONOMY_GENERATION_UNKNOWN,
)
from backend.app.services.text_to_sql_evaluation_taxonomy import (
    VALIDATION_ACCEPTED as TAXONOMY_VALIDATION_ACCEPTED,
)
from backend.app.services.text_to_sql_evaluation_taxonomy import (
    classify_text_to_sql_case,
)

__all__ = [
    "GENERATION_SUCCESS",
    "GENERATION_FAILURE",
    "GENERATION_UNKNOWN",
    "VALIDATION_ACCEPTED",
    "VALIDATION_REJECTED",
    "EXPECTATION_MATCHED",
    "EXPECTATION_MISMATCHED",
    "EXECUTION_SUCCESS",
    "EXECUTION_FAILURE",
    "EXECUTION_NOT_ATTEMPTED",
    "RESULT_CORRECT",
    "RESULT_INCORRECT",
    "RESULT_NOT_EVALUATED",
    "NA",
    "TextToSQLCaseQuality",
    "TextToSQLQualityMetrics",
    "TextToSQLQualitySummary",
    "evaluate_case_quality",
    "calculate_quality_metrics",
    "evaluate_text_to_sql_quality",
]


# ============================================================
# 状态常量
# ============================================================

GENERATION_SUCCESS: Final[str] = "success"
GENERATION_FAILURE: Final[str] = "failure"
GENERATION_UNKNOWN: Final[str] = "unknown"

VALIDATION_ACCEPTED: Final[str] = "accepted"
VALIDATION_REJECTED: Final[str] = "rejected"

EXPECTATION_MATCHED: Final[str] = "matched"
EXPECTATION_MISMATCHED: Final[str] = "mismatched"

EXECUTION_SUCCESS: Final[str] = "success"
EXECUTION_FAILURE: Final[str] = "failure"
EXECUTION_NOT_ATTEMPTED: Final[str] = "n/a"

RESULT_CORRECT: Final[str] = "correct"
RESULT_INCORRECT: Final[str] = "incorrect"
RESULT_NOT_EVALUATED: Final[str] = "n/a"

#: 比率不可用时的展示值
NA: Final[str] = "N/A"

#: 比率精度（0.0 ~ 1.0）
RATE_PRECISION: Final[int] = 4

_GENERATION_TO_QUALITY: Final[dict[str, str]] = {
    TAXONOMY_GENERATION_SUCCESS: GENERATION_SUCCESS,
    TAXONOMY_GENERATION_FAILURE: GENERATION_FAILURE,
    TAXONOMY_GENERATION_UNKNOWN: GENERATION_UNKNOWN,
}

_VALIDATION_TO_QUALITY: Final[dict[str, str]] = {
    TAXONOMY_VALIDATION_ACCEPTED: VALIDATION_ACCEPTED,
}


def _rate(numerator: int, denominator: int) -> float | None:
    """分母为 0 → None（N/A），绝不返回 0。"""
    if denominator <= 0:
        return None
    return round(numerator / denominator, RATE_PRECISION)


# ============================================================
# DTO
# ============================================================

@dataclass(frozen=True)
class TextToSQLCaseQuality:
    """单个 case 的质量状态（§四：四个维度互相独立）。"""

    case_id: str
    generation: str
    validation: str
    expectation: str
    execution: str
    result: str
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "generation": self.generation,
            "validation": self.validation,
            "expectation": self.expectation,
            "execution": self.execution,
            "result": self.result,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class TextToSQLQualityMetrics:
    """质量汇总计数 + 比率（比率由 property 派生，单一真值）。"""

    total_cases: int

    generation_success: int
    generation_failure: int
    generation_unknown: int

    validator_accepted: int
    validator_rejected: int

    expectation_matched: int
    expectation_mismatched: int

    execution_success: int
    execution_failure: int
    execution_not_attempted: int

    result_correct: int
    result_incorrect: int
    result_not_evaluated: int

    # ---------- 比率（分母只含有证据的 case；分母 0 → None） ----------

    @property
    def generation_success_rate(self) -> float | None:
        return _rate(
            self.generation_success,
            self.generation_success + self.generation_failure,
        )

    @property
    def validator_acceptance_rate(self) -> float | None:
        return _rate(
            self.validator_accepted,
            self.validator_accepted + self.validator_rejected,
        )

    @property
    def expectation_match_rate(self) -> float | None:
        return _rate(
            self.expectation_matched,
            self.expectation_matched + self.expectation_mismatched,
        )

    @property
    def execution_success_rate(self) -> float | None:
        return _rate(
            self.execution_success,
            self.execution_success + self.execution_failure,
        )

    @property
    def result_accuracy(self) -> float | None:
        return _rate(
            self.result_correct,
            self.result_correct + self.result_incorrect,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "generation_success": self.generation_success,
            "generation_failure": self.generation_failure,
            "generation_unknown": self.generation_unknown,
            "generation_success_rate": self.generation_success_rate,
            "validator_accepted": self.validator_accepted,
            "validator_rejected": self.validator_rejected,
            "validator_acceptance_rate": self.validator_acceptance_rate,
            "expectation_matched": self.expectation_matched,
            "expectation_mismatched": self.expectation_mismatched,
            "expectation_match_rate": self.expectation_match_rate,
            "execution_success": self.execution_success,
            "execution_failure": self.execution_failure,
            "execution_not_attempted": self.execution_not_attempted,
            "execution_success_rate": self.execution_success_rate,
            "result_correct": self.result_correct,
            "result_incorrect": self.result_incorrect,
            "result_not_evaluated": self.result_not_evaluated,
            "result_accuracy": self.result_accuracy,
        }


@dataclass(frozen=True)
class TextToSQLQualitySummary:
    """一次质量评估的完整产物。"""

    phase: str
    dataset_path: str
    dataset_version: str
    total_cases: int
    metrics: TextToSQLQualityMetrics
    cases: tuple[TextToSQLCaseQuality, ...] = ()
    failure_distribution: dict[str, int] = field(default_factory=dict)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        compare=False,
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "dataset": {
                "path": self.dataset_path,
                "version": self.dataset_version,
                "total_cases": self.total_cases,
            },
            "metrics": self.metrics.as_dict(),
            "failure_distribution": dict(self.failure_distribution),
            "cases": [item.to_dict() for item in self.cases],
            "generated_at": self.generated_at,
        }

    # ---------- Report（§十一） ----------

    def render_report(self) -> str:
        metrics = self.metrics
        lines = [
            f"# Text-to-SQL Generation Quality Baseline — Phase {self.phase}",
            "",
            "> 本阶段只做质量评估：未修改 Prompt / Generator / Validator / "
            "Executor / Dataset。",
            "",
            "## 1. Scope",
            "",
            "- 评估对象：3.9.3 Regression Dataset（14 cases）",
            "- 评估方式：真实 DeepSeek（`deepseek-chat`）+ 真实 Validator / Executor",
            "- 未修改任何生产逻辑",
            "- 未修改 3.9.4 / 3.9.5 / 3.9.6 / 3.9.7 / 3.9.8 产物",
            "",
            "## 2. Dataset",
            "",
            f"- File: `{self.dataset_path}`",
            f"- Version: `{self.dataset_version}`",
            f"- Cases: {self.total_cases}",
            "- Expected 类型：结构期望（must_pass_validation / "
            "must_contain_tables / must_contain_columns / must_not_contain / "
            "must_execute）",
            "- **无 expected result 数据** → result correctness 记为 N/A",
            "",
            "## 3. Metrics",
            "",
            "| Metric | Rate | Cases |",
            "|---|---:|---:|",
            f"| Generation Success | {_pct(metrics.generation_success_rate)} | "
            f"{metrics.generation_success}/"
            f"{metrics.generation_success + metrics.generation_failure} |",
            f"| Validator Acceptance | "
            f"{_pct(metrics.validator_acceptance_rate)} | "
            f"{metrics.validator_accepted}/"
            f"{metrics.validator_accepted + metrics.validator_rejected} |",
            f"| Expectation Match | {_pct(metrics.expectation_match_rate)} | "
            f"{metrics.expectation_matched}/"
            f"{metrics.expectation_matched + metrics.expectation_mismatched} |",
            f"| Execution Success | {_pct(metrics.execution_success_rate)} | "
            f"{metrics.execution_success}/"
            f"{metrics.execution_success + metrics.execution_failure} |",
            f"| Result Accuracy | {_pct(metrics.result_accuracy)} | "
            f"{metrics.result_correct}/"
            f"{metrics.result_correct + metrics.result_incorrect} |",
            "",
            "> N/A 表示**没有证据**（未尝试 / 无 expected result），"
            "**不**参与分母，也**不**等价于 0。",
            "",
            "## 4. Per-case Result",
            "",
            "| case_id | generation | validation | expectation | execution | "
            "result | error_code |",
            "|---|---|---|---|---|---|---|",
        ]
        for item in self.cases:
            lines.append(
                f"| `{item.case_id}` | {item.generation} | {item.validation} | "
                f"{item.expectation} | {item.execution} | {item.result} | "
                f"{item.error_code or '-'} |"
            )
        lines.extend(
            [
                "",
                "## 5. Failure Distribution（复用 3.9.6 Taxonomy）",
                "",
                "| Category | Cases |",
                "|---|---:|",
            ]
        )
        if self.failure_distribution:
            for key, value in self.failure_distribution.items():
                lines.append(f"| `{key}` | {value} |")
        else:
            lines.append("| (none) | 0 |")
        lines.extend(["", "## 6. Findings", ""])
        lines.extend(self._findings())
        lines.extend(["", "## 7. Limitations", ""])
        lines.extend(_limitations())
        lines.append("")
        return "\n".join(lines)

    def _findings(self) -> list[str]:
        """只描述事实，不做主观评价（§十一.6）。"""
        metrics = self.metrics
        attempted = metrics.execution_success + metrics.execution_failure
        evaluated = metrics.result_correct + metrics.result_incorrect
        return [
            f"- {metrics.total_cases}/{metrics.total_cases} cases were run.",
            f"- {metrics.generation_success}/{metrics.total_cases} cases "
            f"produced SQL that reached the validator.",
            f"- {metrics.validator_accepted}/{metrics.total_cases} cases were "
            f"accepted by the real SQL validator.",
            f"- {metrics.expectation_matched}/{metrics.total_cases} cases "
            f"satisfied all configured structural expectations.",
            f"- {metrics.execution_success}/{attempted} cases executed "
            f"successfully where execution was attempted "
            f"({metrics.execution_not_attempted} not attempted)."
            if attempted
            else f"- No case requested SQL execution "
            f"({metrics.execution_not_attempted} not attempted).",
            f"- {evaluated}/{metrics.total_cases} cases had result-level "
            f"correctness evidence."
            if evaluated
            else f"- {metrics.result_not_evaluated}/{metrics.total_cases} "
            f"cases had **no** result-level correctness evidence "
            f"(Dataset provides no expected result).",
        ]


def _pct(rate: float | None) -> str:
    return NA if rate is None else f"{rate * 100:.2f}%"


def _limitations() -> list[str]:
    return [
        "- **Dataset size**: 14 cases，不足以推断生产准确率。",
        "- **LLM nondeterminism**: DeepSeek 输出不稳定，重复运行指标可能变化。",
        "- **Result correctness coverage**: 当前 Dataset 无 expected result，"
        "result_accuracy 为 N/A，不能据此判断 SQL 语义正确性。",
        "- **Execution coverage**: 仅对可执行（目标 schema 存在）的 case "
        "统计执行指标；其余记为 N/A。",
        "- **No production DB**: 仅使用测试 PostgreSQL，且只执行只读 SQL。",
        "- **Structural expectations only**: expectation 命中只表示结构符合，"
        "不代表查询结果语义正确。",
    ]


# ============================================================
# 评估函数（纯函数）
# ============================================================

def evaluate_case_quality(
    result: TextToSQLEvaluationResult,
    case: TextToSQLEvaluationCase | None = None,
    *,
    result_outcomes: Mapping[str, bool] | None = None,
) -> TextToSQLEvaluationResult | TextToSQLCaseQuality:
    """评估单个 case 的四维质量状态（§四）。

    Args:
        result:           3.9.3 Runner 结果。
        case:             可选 Dataset case（提高 generation 判定精度）。
        result_outcomes:  可选 ``case_id → 结果是否正确`` 映射。
                          缺省 None → result 一律 ``n/a``（§五：不臆造）。
    """
    # ---- generation / validation / expectation：复用 3.9.6 Taxonomy ----
    taxonomy = classify_text_to_sql_case(result, case)
    generation = _GENERATION_TO_QUALITY.get(
        taxonomy.generation, GENERATION_UNKNOWN
    )
    validation = (
        VALIDATION_ACCEPTED if result.validation_passed else VALIDATION_REJECTED
    )
    expectation = (
        EXPECTATION_MATCHED if result.passed else EXPECTATION_MISMATCHED
    )

    # ---- execution：只有真正尝试过才有证据 ----
    if result.execution_passed is True:
        execution = EXECUTION_SUCCESS
    elif result.execution_passed is False:
        execution = EXECUTION_FAILURE
    else:
        execution = EXECUTION_NOT_ATTEMPTED

    # ---- result：无 expected result 数据 → N/A ----
    if result_outcomes is None or result.case_id not in result_outcomes:
        result_state = RESULT_NOT_EVALUATED
    else:
        result_state = (
            RESULT_CORRECT
            if result_outcomes[result.case_id]
            else RESULT_INCORRECT
        )

    return TextToSQLCaseQuality(
        case_id=result.case_id,
        generation=generation,
        validation=validation,
        expectation=expectation,
        execution=execution,
        result=result_state,
        error_code=result.error_code,
    )


def calculate_quality_metrics(
    cases: Sequence[TextToSQLCaseQuality],
) -> TextToSQLQualityMetrics:
    """由 case 状态聚合质量指标（纯函数；N/A 不进分母）。"""

    def count(attribute: str, value: str) -> int:
        return sum(1 for item in cases if getattr(item, attribute) == value)

    return TextToSQLQualityMetrics(
        total_cases=len(cases),
        generation_success=count("generation", GENERATION_SUCCESS),
        generation_failure=count("generation", GENERATION_FAILURE),
        generation_unknown=count("generation", GENERATION_UNKNOWN),
        validator_accepted=count("validation", VALIDATION_ACCEPTED),
        validator_rejected=count("validation", VALIDATION_REJECTED),
        expectation_matched=count("expectation", EXPECTATION_MATCHED),
        expectation_mismatched=count("expectation", EXPECTATION_MISMATCHED),
        execution_success=count("execution", EXECUTION_SUCCESS),
        execution_failure=count("execution", EXECUTION_FAILURE),
        execution_not_attempted=count("execution", EXECUTION_NOT_ATTEMPTED),
        result_correct=count("result", RESULT_CORRECT),
        result_incorrect=count("result", RESULT_INCORRECT),
        result_not_evaluated=count("result", RESULT_NOT_EVALUATED),
    )


def evaluate_text_to_sql_quality(
    results: Sequence[TextToSQLEvaluationResult],
    cases: Sequence[TextToSQLEvaluationCase] = (),
    *,
    result_outcomes: Mapping[str, bool] | None = None,
    phase: str = "3.9.9",
    dataset_path: str = "",
    dataset_version: str = "",
) -> TextToSQLQualitySummary:
    """整批质量评估（纯函数，无副作用）。"""
    case_by_id = {case.case_id: case for case in cases}
    qualities = tuple(
        evaluate_case_quality(
            result,
            case_by_id.get(result.case_id),
            result_outcomes=result_outcomes,
        )
        for result in results
    )
    metrics = calculate_quality_metrics(qualities)

    # 失败分布：直接复用 3.9.6 Taxonomy，不创造新类别
    distribution: dict[str, int] = {}
    for result in results:
        taxonomy = classify_text_to_sql_case(result, case_by_id.get(result.case_id))
        if not taxonomy.is_security_case and taxonomy.expectation == "EXPECTATION_MATCH":
            continue
        for label in _taxonomy_labels(taxonomy):
            distribution[label] = distribution.get(label, 0) + 1

    return TextToSQLQualitySummary(
        phase=phase,
        dataset_path=dataset_path,
        dataset_version=dataset_version,
        total_cases=len(qualities),
        metrics=metrics,
        cases=qualities,
        failure_distribution=dict(sorted(distribution.items())),
    )


def _taxonomy_labels(taxonomy: Any) -> tuple[str, ...]:
    """把 Taxonomy 展开为失败分布标签（沿用既有类别，不新增）。"""
    labels: list[str] = []
    if taxonomy.generation == TAXONOMY_GENERATION_FAILURE:
        labels.append("GENERATION_FAILURE")
    if taxonomy.validation == "VALIDATION_REJECTED":
        labels.append("VALIDATION_REJECTED")
    if taxonomy.expectation == "EXPECTATION_MISMATCH":
        labels.append("EXPECTATION_MISMATCH")
    labels.extend(taxonomy.security_categories)
    if taxonomy.project_isolation == "PROJECT_ISOLATION_FAIL":
        labels.append("PROJECT_ISOLATION_FAIL")
    return tuple(labels) or ("OTHER",)
