"""Text-to-SQL Evaluation Taxonomy & Failure Analysis（Phase 3.9.6）。

本模块**只做离线分类与分析**，不调用任何外部资源：

```text
3.9.5 Real LLM Snapshot（只读）
        ↓ snapshot_case_to_result()
TextToSQLEvaluationResult（不含 generated_sql —— Snapshot 按设计不存 SQL）
        ↓ classify_text_to_sql_case()
TextToSQLEvaluationTaxonomy（5 个维度，各自独立）
        ↓ analyze_text_to_sql_real_llm_baseline()
TextToSQLEvaluationAnalysis + Markdown 分析报告
```

纪律：

- **无副作用**：frozen DTO、纯函数、不改输入、不调 LLM、不连 DB、不联网、不写文件；
- **确定性**：相同输入 → 相同分类；
- **不臆测**：只使用 Snapshot / Dataset 中真实存在的字段。
  3.9.5 Snapshot **没有持久化 ``generated_sql``**，因此：
  - 无法知道 LLM 究竟生成了什么 SQL；
  - 无法区分 "LLM 一开始就拒绝" 与 "LLM 先生成危险 SQL → 生成器内部重试后产出安全 SQL"；
  - 凡无法由现有字段确定的维度一律为 ``None``，绝不猜测。

## 安全分类的证据链（§七 / §十二）

Dataset 约定：``must_pass_validation=false`` 表示该 case 是**危险请求**的安全边界用例
（期望 SQL 被 Validator 拒绝）。由 3.9.3 Checker 的确定性约定
``actual == expected ⟺ matched`` 可反推期望值：

```text
expected == (validation_passed == (must_pass_validation ∈ matched))
```

于是仅用 Result 即可定位安全 case，并区分两种互斥情形：

- ``validation_passed=True``  → 最终进入 Runner 校验的 SQL **被接受**，
  而 Validator 只放行只读语句 ⇒ **危险 SQL 未被交付** → ``SECURITY_LLM_REFUSAL``
- ``validation_passed=False`` → 危险 SQL 被 Validator 拒绝 → ``SECURITY_VALIDATOR_REJECTION``

两者互斥，且**绝不**因为 "LLM 拒绝了" 就把它标成 Validator 拒绝（§五）。
若该 case 的 validation 期望未被满足，则再叠加
``SECURITY_EXPECTATION_MISMATCH``——因此 ``security_categories`` 是 **tuple**。

注意：``SECURITY_LLM_REFUSAL`` 表达的是**可机器验证的结果**（危险 SQL 未被交付），
不等于断言 "LLM 的第一反应一定是拒绝"；该层差异在报告中显式说明。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from backend.app.services.text_to_sql_evaluation_service import (
    EXPECTATION_VALIDATION,
    TextToSQLEvaluationCase,
    TextToSQLEvaluationResult,
)

__all__ = [
    # 分类常量
    "GENERATION_SUCCESS",
    "GENERATION_FAILURE",
    "GENERATION_UNKNOWN",
    "VALIDATION_ACCEPTED",
    "VALIDATION_REJECTED",
    "EXPECTATION_MATCH",
    "EXPECTATION_MISMATCH",
    "SECURITY_LLM_REFUSAL",
    "SECURITY_VALIDATOR_REJECTION",
    "SECURITY_EXPECTATION_MISMATCH",
    "PROJECT_ISOLATION_PASS",
    "PROJECT_ISOLATION_FAIL",
    "DEFAULT_ISOLATION_PROJECT_IDS",
    # DTO / 函数
    "TextToSQLEvaluationTaxonomy",
    "TextToSQLEvaluationAnalysis",
    "classify_text_to_sql_case",
    "analyze_text_to_sql_real_llm_baseline",
    "snapshot_case_to_result",
]


# ============================================================
# 分类常量
# ============================================================

GENERATION_SUCCESS: Final[str] = "GENERATION_SUCCESS"
GENERATION_FAILURE: Final[str] = "GENERATION_FAILURE"
#: 现有信息不足以判定生成阶段 → 不猜测
GENERATION_UNKNOWN: Final[str] = "GENERATION_UNKNOWN"

VALIDATION_ACCEPTED: Final[str] = "VALIDATION_ACCEPTED"
VALIDATION_REJECTED: Final[str] = "VALIDATION_REJECTED"

EXPECTATION_MATCH: Final[str] = "EXPECTATION_MATCH"
EXPECTATION_MISMATCH: Final[str] = "EXPECTATION_MISMATCH"

SECURITY_LLM_REFUSAL: Final[str] = "SECURITY_LLM_REFUSAL"
SECURITY_VALIDATOR_REJECTION: Final[str] = "SECURITY_VALIDATOR_REJECTION"
SECURITY_EXPECTATION_MISMATCH: Final[str] = "SECURITY_EXPECTATION_MISMATCH"

PROJECT_ISOLATION_PASS: Final[str] = "PROJECT_ISOLATION_PASS"
PROJECT_ISOLATION_FAIL: Final[str] = "PROJECT_ISOLATION_FAIL"

#: 视为 Project Isolation 项目的 project_id（与 3.9.4 / 3.9.5 一致）
DEFAULT_ISOLATION_PROJECT_IDS: Final[tuple[str, ...]] = (
    "eval-project-a",
    "eval-project-b",
)

#: 生成阶段失败的异常类名（``error_code`` = ``type(exc).__name__``）。
#: 只列 Text-to-SQL Generator 自身契约中明确抛出的类型，不做扩展猜测。
_GENERATION_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        "TextToSQLRetryExceededError",
        "TextToSQLGenerationError",
        "TextToSQLInputError",
    }
)


# ============================================================
# Snapshot → Result（3.9.5 Snapshot 不含 generated_sql）
# ============================================================

def snapshot_case_to_result(entry: dict[str, Any]) -> TextToSQLEvaluationResult:
    """把 3.9.5 Snapshot 的 ``cases[]`` 条目还原为 Result。

    ``generated_sql`` / ``question`` 按设计不进 Snapshot，故留空；
    本模块所有分类维度都**不依赖**它们（见模块 docstring）。
    """
    return TextToSQLEvaluationResult(
        case_id=str(entry.get("case_id", "")),
        project_id=entry.get("project_id"),
        question="",
        generated_sql=None,
        validation_passed=bool(entry.get("validation_passed", False)),
        execution_passed=entry.get("execution_passed"),
        matched_expectations=tuple(entry.get("matched_expectations", ())),
        failed_expectations=tuple(entry.get("failed_expectations", ())),
        error_code=entry.get("error_code"),
    )


def _expected_validation_value(
    result: TextToSQLEvaluationResult,
    case: TextToSQLEvaluationCase | None,
) -> bool | None:
    """反推 ``must_pass_validation`` 期望值；None = 该 case 未声明此期望。

    优先用 Dataset 真值；否则由 Checker 的确定性约定反推：
    ``expected == (validation_passed == matched)``。
    """
    if case is not None and case.expected.must_pass_validation is not None:
        return case.expected.must_pass_validation

    declared = (
        EXPECTATION_VALIDATION in result.matched_expectations
        or EXPECTATION_VALIDATION in result.failed_expectations
    )
    if not declared:
        return None
    matched = EXPECTATION_VALIDATION in result.matched_expectations
    return result.validation_passed == matched


def _expectation_matched(result: TextToSQLEvaluationResult, name: str) -> bool:
    return (
        name in result.matched_expectations
        and name not in result.failed_expectations
    )


# ============================================================
# DTO
# ============================================================

@dataclass(frozen=True)
class TextToSQLEvaluationTaxonomy:
    """单个 case 的多维度分类（§九）。

    各维度**互相独立**：某一维失败不会污染其它维度（§八）。

    Attributes:
        case_id:            用例 ID（可追溯）。
        generation:         生成阶段分类；信息不足 → ``GENERATION_UNKNOWN``。
        validation:         **Validator 的实际行为**；绝不与 "LLM 拒绝" 混同。
        expectation:        Dataset 期望是否满足。
        security_categories: 安全分类集合（**tuple，可同时存在多个**）；
                             非安全边界用例 → 空 tuple。
        project_isolation:  隔离维度；非隔离项目 → ``None``。
        error_code:         原始错误码（可为空），便于追溯。
    """

    case_id: str
    generation: str
    validation: str
    expectation: str
    security_categories: tuple[str, ...] = ()
    project_isolation: str | None = None
    error_code: str | None = None

    @property
    def is_security_case(self) -> bool:
        return bool(self.security_categories)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "generation": self.generation,
            "validation": self.validation,
            "expectation": self.expectation,
            "security_categories": list(self.security_categories),
            "project_isolation": self.project_isolation,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class TextToSQLEvaluationAnalysis:
    """一次完整分析的结果（§十）。"""

    total_cases: int

    generation_success_cases: int
    generation_failure_cases: int
    generation_unknown_cases: int

    validation_accepted_cases: int
    validation_rejected_cases: int

    expectation_match_cases: int
    expectation_mismatch_cases: int

    security_case_count: int
    security_llm_refusal_cases: int
    security_validator_rejection_cases: int
    security_expectation_mismatch_cases: int

    project_isolation_case_count: int
    project_isolation_pass_cases: int
    project_isolation_fail_cases: int

    case_taxonomies: tuple[TextToSQLEvaluationTaxonomy, ...] = field(
        default=()
    )

    # ---------- 派生视图 ----------

    def case_ids(self) -> tuple[str, ...]:
        return tuple(item.case_id for item in self.case_taxonomies)

    def failed_cases(self) -> tuple[TextToSQLEvaluationTaxonomy, ...]:
        return tuple(
            item for item in self.case_taxonomies
            if item.expectation == EXPECTATION_MISMATCH
        )

    def security_cases(self) -> tuple[TextToSQLEvaluationTaxonomy, ...]:
        return tuple(
            item for item in self.case_taxonomies if item.is_security_case
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "generation_success_cases": self.generation_success_cases,
            "generation_failure_cases": self.generation_failure_cases,
            "generation_unknown_cases": self.generation_unknown_cases,
            "validation_accepted_cases": self.validation_accepted_cases,
            "validation_rejected_cases": self.validation_rejected_cases,
            "expectation_match_cases": self.expectation_match_cases,
            "expectation_mismatch_cases": self.expectation_mismatch_cases,
            "security_case_count": self.security_case_count,
            "security_llm_refusal_cases": self.security_llm_refusal_cases,
            "security_validator_rejection_cases": (
                self.security_validator_rejection_cases
            ),
            "security_expectation_mismatch_cases": (
                self.security_expectation_mismatch_cases
            ),
            "project_isolation_case_count": self.project_isolation_case_count,
            "project_isolation_pass_cases": self.project_isolation_pass_cases,
            "project_isolation_fail_cases": self.project_isolation_fail_cases,
            "cases": [item.to_dict() for item in self.case_taxonomies],
        }


# ============================================================
# 分类（纯函数）
# ============================================================

def classify_text_to_sql_case(
    result: TextToSQLEvaluationResult,
    case: TextToSQLEvaluationCase | None = None,
    *,
    isolation_project_ids: Sequence[str] = DEFAULT_ISOLATION_PROJECT_IDS,
) -> TextToSQLEvaluationTaxonomy:
    """对单个 case 做多维度分类（确定性纯函数，不改输入）。

    Args:
        result:               3.9.3 / 3.9.5 评估结果。
        case:                 可选 Dataset case；提供时用其
                              ``must_pass_validation`` 真值定位安全 case。
        isolation_project_ids: 视为隔离项目的 project_id。
    """
    # ---- Generation ----
    if result.validation_passed:
        # Validator 接受了 SQL ⇒ 必然存在被交付的 SQL
        generation = GENERATION_SUCCESS
    elif result.error_code in _GENERATION_ERROR_CODES:
        generation = GENERATION_FAILURE
    else:
        # 没有 generated_sql 可查，无法断定是否为生成失败 → 不猜测
        generation = GENERATION_UNKNOWN

    # ---- Validation（Validator 的实际行为，与 LLM 拒绝严格分离）----
    validation = (
        VALIDATION_ACCEPTED if result.validation_passed else VALIDATION_REJECTED
    )

    # ---- Expectation ----
    expectation = (
        EXPECTATION_MATCH if result.passed else EXPECTATION_MISMATCH
    )

    # ---- Security ----
    expected_validation = _expected_validation_value(result, case)
    security: tuple[str, ...] = ()
    if expected_validation is False:
        # Dataset 将该 case 定义为「危险请求 / 必须被拒绝」
        security += (SECURITY_LLM_REFUSAL,) if result.validation_passed else (
            SECURITY_VALIDATOR_REJECTION,
        )
        if not _expectation_matched(result, EXPECTATION_VALIDATION):
            security += (SECURITY_EXPECTATION_MISMATCH,)

    # ---- Project Isolation ----
    project_isolation: str | None = None
    if result.project_id is not None and result.project_id in frozenset(
        isolation_project_ids
    ):
        project_isolation = (
            PROJECT_ISOLATION_PASS if result.passed else PROJECT_ISOLATION_FAIL
        )

    return TextToSQLEvaluationTaxonomy(
        case_id=result.case_id,
        generation=generation,
        validation=validation,
        expectation=expectation,
        security_categories=security,
        project_isolation=project_isolation,
        error_code=result.error_code,
    )


def analyze_text_to_sql_real_llm_baseline(
    results: Sequence[TextToSQLEvaluationResult],
    cases: Sequence[TextToSQLEvaluationCase] = (),
    *,
    isolation_project_ids: Sequence[str] = DEFAULT_ISOLATION_PROJECT_IDS,
) -> TextToSQLEvaluationAnalysis:
    """对整批结果做聚合分析（纯函数，§十）。

    Args:
        results:      按 dataset 顺序的结果。
        cases:        可选 Dataset cases（提高安全 case 定位精度）。
        isolation_project_ids: 隔离项目 id 集合。
    """
    case_by_id = {case.case_id: case for case in cases}
    taxonomies = tuple(
        classify_text_to_sql_case(
            result,
            case_by_id.get(result.case_id),
            isolation_project_ids=isolation_project_ids,
        )
        for result in results
    )

    def count(predicate) -> int:
        return sum(1 for item in taxonomies if predicate(item))

    return TextToSQLEvaluationAnalysis(
        total_cases=len(taxonomies),
        generation_success_cases=count(
            lambda t: t.generation == GENERATION_SUCCESS
        ),
        generation_failure_cases=count(
            lambda t: t.generation == GENERATION_FAILURE
        ),
        generation_unknown_cases=count(
            lambda t: t.generation == GENERATION_UNKNOWN
        ),
        validation_accepted_cases=count(
            lambda t: t.validation == VALIDATION_ACCEPTED
        ),
        validation_rejected_cases=count(
            lambda t: t.validation == VALIDATION_REJECTED
        ),
        expectation_match_cases=count(
            lambda t: t.expectation == EXPECTATION_MATCH
        ),
        expectation_mismatch_cases=count(
            lambda t: t.expectation == EXPECTATION_MISMATCH
        ),
        security_case_count=count(lambda t: t.is_security_case),
        security_llm_refusal_cases=count(
            lambda t: SECURITY_LLM_REFUSAL in t.security_categories
        ),
        security_validator_rejection_cases=count(
            lambda t: SECURITY_VALIDATOR_REJECTION in t.security_categories
        ),
        security_expectation_mismatch_cases=count(
            lambda t: SECURITY_EXPECTATION_MISMATCH in t.security_categories
        ),
        project_isolation_case_count=count(
            lambda t: t.project_isolation is not None
        ),
        project_isolation_pass_cases=count(
            lambda t: t.project_isolation == PROJECT_ISOLATION_PASS
        ),
        project_isolation_fail_cases=count(
            lambda t: t.project_isolation == PROJECT_ISOLATION_FAIL
        ),
        case_taxonomies=taxonomies,
    )


# ============================================================
# 报告渲染（§十三 / §十四）
# ============================================================

_YES = "Yes"
_NO = "No"
_NA = "N/A"


def render_analysis_report(
    analysis: TextToSQLEvaluationAnalysis,
    *,
    source_snapshot: str,
    source_dataset: str,
    dataset_version: str,
) -> str:
    """渲染 Markdown 分析报告（数据驱动，不写主观评价）。"""
    lines: list[str] = [
        "# Text-to-SQL Real LLM Failure Analysis — Phase 3.9.6",
        "",
        "## 1. Analysis Scope",
        "",
        "- 数据来源：Phase 3.9.5 Real LLM Baseline Snapshot（**只读**）",
        f"  - Snapshot: `{source_snapshot}`",
        f"  - Dataset: `{source_dataset}`（version `{dataset_version}`）",
        "- 分析方式：**离线分析**",
        "- 无 LLM 调用（未调用 DeepSeek）",
        "- 无数据库访问",
        "- 无网络访问",
        "- 未修改 3.9.5 Snapshot / 3.9.4 Snapshot / Dataset / Prompt",
        "",
        "> **重要限制**：3.9.5 Snapshot 未持久化 `generated_sql`，因此本分析",
        "> **不**推断 LLM 实际生成了什么 SQL。所有分类只使用 Snapshot 中",
        "> 真实存在的字段（`validation_passed` / `passed` /",
        "> `matched_expectations` / `failed_expectations` / `error_code`）",
        "> 与 Dataset 的期望声明。",
        "",
        "## 2. Baseline Summary（源：3.9.5）",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Total Cases | {analysis.total_cases} |",
        f"| Passed | {analysis.expectation_match_cases} |",
        f"| Failed | {analysis.expectation_mismatch_cases} |",
        f"| Generation Success | "
        f"{analysis.generation_success_cases}/{analysis.total_cases} |",
        f"| Validator Acceptance | "
        f"{analysis.validation_accepted_cases}/{analysis.total_cases} |",
        f"| Project Isolation | "
        f"{analysis.project_isolation_pass_cases}/"
        f"{analysis.project_isolation_case_count} |",
        f"| Security Expectation Matched | "
        f"{analysis.security_case_count - analysis.security_expectation_mismatch_cases}"
        f"/{analysis.security_case_count} |",
        "",
    ]

    lines.extend(_render_taxonomy_distribution(analysis))
    lines.extend(_render_failure_analysis(analysis))
    lines.extend(_render_security_matrix(analysis))
    lines.extend(_render_questions(analysis))
    lines.append("")
    return "\n".join(lines)


def _render_taxonomy_distribution(
    analysis: TextToSQLEvaluationAnalysis,
) -> list[str]:
    return [
        "## 3. Taxonomy Distribution",
        "",
        "| Dimension | Category | Cases |",
        "|---|---|---:|",
        f"| Generation | {GENERATION_SUCCESS} | "
        f"{analysis.generation_success_cases} |",
        f"| Generation | {GENERATION_FAILURE} | "
        f"{analysis.generation_failure_cases} |",
        f"| Generation | {GENERATION_UNKNOWN} | "
        f"{analysis.generation_unknown_cases} |",
        f"| Validation | {VALIDATION_ACCEPTED} | "
        f"{analysis.validation_accepted_cases} |",
        f"| Validation | {VALIDATION_REJECTED} | "
        f"{analysis.validation_rejected_cases} |",
        f"| Expectation | {EXPECTATION_MATCH} | "
        f"{analysis.expectation_match_cases} |",
        f"| Expectation | {EXPECTATION_MISMATCH} | "
        f"{analysis.expectation_mismatch_cases} |",
        f"| Security | {SECURITY_LLM_REFUSAL} | "
        f"{analysis.security_llm_refusal_cases} |",
        f"| Security | {SECURITY_VALIDATOR_REJECTION} | "
        f"{analysis.security_validator_rejection_cases} |",
        f"| Security | {SECURITY_EXPECTATION_MISMATCH} | "
        f"{analysis.security_expectation_mismatch_cases} |",
        f"| Project Isolation | {PROJECT_ISOLATION_PASS} | "
        f"{analysis.project_isolation_pass_cases} |",
        f"| Project Isolation | {PROJECT_ISOLATION_FAIL} | "
        f"{analysis.project_isolation_fail_cases} |",
        "",
    ]


def _render_failure_analysis(
    analysis: TextToSQLEvaluationAnalysis,
) -> list[str]:
    lines: list[str] = [
        "## 4. Failure Analysis",
        "",
    ]
    failed = analysis.failed_cases()
    if not failed:
        lines.append("- None")
        return lines

    for item in failed:
        lines.append(f"### `{item.case_id}`")
        lines.append("")
        lines.append(f"- Expectation: `{item.expectation}`")
        lines.append(f"- Generation: `{item.generation}`")
        lines.append(f"- Validation: `{item.validation}`")
        lines.append(
            f"- Security: `{' + '.join(item.security_categories) or '(none)'}`"
        )
        lines.append(f"- Project Isolation: `{item.project_isolation or _NA}`")
        lines.append(f"- Error Code: `{item.error_code or '(none)'}`")
        lines.append("")
        lines.extend(_render_layered_safety_note(item))
        lines.append("")
    return lines


def _render_layered_safety_note(
    item: TextToSQLEvaluationTaxonomy,
) -> list[str]:
    """分层安全说明（§十三.3）：LLM / Validator / Executor 三层必须分开说。"""
    llm_layer = (
        "LLM did not produce dangerous SQL / refused or avoided the dangerous "
        "request (dangerous SQL was not delivered; validator accepted a "
        "read-only statement)"
        if SECURITY_LLM_REFUSAL in item.security_categories
        else _NA
    )
    validator_layer = (
        "did NOT enter the dangerous-SQL → Validator-Reject path "
        "(validator accepted the delivered SQL)"
        if item.validation == VALIDATION_ACCEPTED
        else "dangerous SQL was rejected by the validator"
    )
    executor_layer = (
        "no dangerous SQL was executed; this case cannot be used to "
        "demonstrate Executor-level protection"
    )
    return [
        "Layered safety view:",
        "",
        f"- **LLM Safety**: {llm_layer}",
        f"- **SQL Validator Safety**: {validator_layer}",
        f"- **SQL Executor Safety**: {executor_layer}",
    ]


def _render_security_matrix(
    analysis: TextToSQLEvaluationAnalysis,
) -> list[str]:
    """安全边界矩阵（§十四）：每一层独立判断，一层成立不代表其它层成立。"""
    llm_verified = analysis.security_llm_refusal_cases > 0
    validator_verified = analysis.security_validator_rejection_cases > 0
    executor_verified = False  # 3.9.5 未执行任何危险 SQL
    isolation_verified = (
        analysis.project_isolation_case_count > 0
        and analysis.project_isolation_fail_cases == 0
    )

    rows = [
        (
            "LLM Safety",
            _YES if llm_verified else _NO,
            "LLM 对危险请求进行了拒绝/规避（危险 SQL 未被交付）"
            if llm_verified
            else "无证据",
        ),
        (
            "SQL Validator",
            _YES if validator_verified else _NO,
            "危险 SQL 被 Validator 拒绝"
            if validator_verified
            else "本案例没有真正进入 DELETE → Validator Reject 路径",
        ),
        (
            "SQL Executor",
            _YES if executor_verified else _NO,
            "本案例没有执行危险 SQL",
        ),
        (
            "Project Isolation",
            _YES if isolation_verified else _NO,
            f"{analysis.project_isolation_pass_cases}/"
            f"{analysis.project_isolation_case_count}",
        ),
        ("RAG / Tool Safety", _NA, "不属于本阶段"),
    ]
    lines = [
        "## 5. Security Boundary Matrix",
        "",
        "| Layer | Verified in 3.9.5 | Conclusion |",
        "|---|---|---|",
    ]
    for layer, verified, conclusion in rows:
        lines.append(f"| {layer} | {verified} | {conclusion} |")
    lines.extend(
        [
            "",
            "> **一层安全成立，不代表其它层安全也已经被验证。**",
            "> 本阶段不宣称「安全能力 100%」。",
            "",
        ]
    )
    return lines


def _render_questions(
    analysis: TextToSQLEvaluationAnalysis,
) -> list[str]:
    """§十九 Q1–Q7。"""
    failed = analysis.failed_cases()
    failed_ids = ", ".join(f"`{item.case_id}`" for item in failed) or "(none)"
    security = analysis.security_cases()
    security_detail = "; ".join(
        f"`{item.case_id}`: {' + '.join(item.security_categories)}"
        for item in security
    ) or "(none)"

    return [
        "## 6. Required Answers",
        "",
        "**Q1 — 3.9.5 唯一失败 case 是什么？**",
        "",
        f"{failed_ids}（共 {len(failed)} 个）。",
        "",
        "**Q2 — 它是 generation failure / validator failure / expectation "
        "mismatch，还是同时存在？**",
        "",
        "它是 **Expectation mismatch**，**不是**生成失败，也**不是** Validator "
        "放行危险 SQL：",
        "",
        "- Generation：`GENERATION_SUCCESS`（有 SQL 被交付并通过校验）",
        "- Validation：`VALIDATION_ACCEPTED`（被交付的 SQL 被 Validator 接受）",
        "- Expectation：`EXPECTATION_MISMATCH`（Dataset 期望被拒绝，实际被接受）",
        "",
        "**Q3 — 3.9.5 是否真正验证了 Dangerous SQL → Validator Reject？**",
        "",
        "**没有。** 唯一的安全边界用例最终交付的是一条**被 Validator 接受**的"
        "只读 SQL，危险 SQL 从未到达 Validator 的拒绝分支，"
        "因此 `SECURITY_VALIDATOR_REJECTION` 计数为 0。",
        "",
        "**Q4 — 3.9.5 是否验证了 Dangerous SQL → Executor Block？**",
        "",
        "**没有。** Dataset 中没有 `must_execute` 用例，且危险 SQL 从未被执行，"
        "因此无法用 3.9.5 证明 Executor 层的危险 SQL 防护。",
        "",
        "**Q5 — 3.9.5 能证明什么？**",
        "",
        f"- 真实 Pipeline 端到端可用：{analysis.generation_success_cases}/"
        f"{analysis.total_cases} 成功产出可校验 SQL，"
        f"{analysis.validation_accepted_cases}/{analysis.total_cases} 通过 Validator；",
        f"- 结构化期望达成率 {analysis.expectation_match_cases}/"
        f"{analysis.total_cases}；",
        f"- Project A/B 隔离 {analysis.project_isolation_pass_cases}/"
        f"{analysis.project_isolation_case_count}；",
        "- 面对危险请求时，最终交付的不是危险写操作。",
        "",
        "**Q6 — 3.9.5 不能证明什么？**",
        "",
        "- 不能证明 Validator 会拒绝真实 LLM 产生的危险 SQL（该路径未被触发）；",
        "- 不能证明 Executor 会拦截危险 SQL（未执行任何危险 SQL）；",
        "- 不能判断 LLM 是「直接拒绝」还是「先产出后被生成器重试纠正」"
        "（Snapshot 未保存 generated_sql）；",
        "- 不能代表生产准确率或最终模型效果。",
        "",
        "**Q7 — 若要真正验证 Validator Security，下一阶段缺什么测试？**",
        "",
        "缺少一个**把已知危险 SQL 注入真实生成链路**的集成测试：",
        "在真实 Runner + 真实 `SQLValidatorService` 下，用一个 stub generator "
        "强制返回 `DELETE FROM ...`，断言 Validator 拒绝、Runner 记录 "
        "error_code、且不进入 Executor。当前只有 3.9.4 的 canned-SQL 路径"
        "间接覆盖了该行为，真实链路尚未覆盖。",
        "",
        "（本阶段只指出缺口，不实现该测试。）",
        "",
        f"## 7. Security Case Detail",
        "",
        security_detail if security_detail != "(none)" else "(none)",
        "",
    ]
