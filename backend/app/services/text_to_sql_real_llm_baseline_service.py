"""Text-to-SQL Real LLM Baseline（Phase 3.9.5）。

在 3.9.4 的 Baseline Evaluation Framework 之上，增加**真实 DeepSeek**
第一次跑出真实 LLM 表现。本阶段**只测量，不调优**：

```text
Phase 3.9.3 Dataset（只读，不修改 case）
        ↓
真实 TextToSQLService（DeepSeek） + 真实 SQLValidator
        ↓
Phase 3.9.3 TextToSQLEvaluationRunner（不重复实现）
        ↓
calculate_real_llm_baseline_metrics()（纯函数，复用 3.9.4 计算）
        ↓
Snapshot JSON + Markdown Report
```

核心纪律（§十四 / §十七）：

- 不修改 Prompt / Generator / Validator / Selector / Semantic / Dataset；
- 不修改 3.9.4 Snapshot；不覆盖既有产物（若 LLM 不可达则整体失败，
  不写假 Baseline）；
- 不缓存 SQL、不把 case 结果硬编码；
- 单 case 异常不污染其它 case（Runner 已经独立 try/except + 缺省 failed）；
- Snapshot / Report 不含 API Key / DATABASE_URL / 完整环境变量；
- 比率统一 ``0.0 ~ 1.0``（保留 4 位小数），``None`` = N/A。
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from backend.app.config import settings
from backend.app.services.text_to_sql_baseline_service import (
    RATE_PRECISION,
    TextToSQLBaselineEnvironment,
    UNKNOWN_DATASET_VERSION,
    UNAVAILABLE,
    _relative_path,
    _rate,
    calculate_baseline_metrics,
    read_dataset_version,
)
from backend.app.services.text_to_sql_evaluation_service import (
    DEFAULT_REGRESSION_DATASET_PATH,
    EXPECTATION_VALIDATION,
    TextToSQLEvaluationCase,
    TextToSQLEvaluationResult,
    TextToSQLEvaluationRunner,
    load_text_to_sql_regression_dataset,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PHASE_3_9_5",
    "BASELINE_TYPE_REAL_LLM",
    "REAL_LLM_EXECUTION_MODE",
    "REAL_LLM_SNAPSHOT_PATH",
    "REAL_LLM_REPORT_PATH",
    "RATE_PRECISION",
    "TextToSQLRealLLMBaselineMetrics",
    "TextToSQLRealLLMBaseline",
    "calculate_real_llm_baseline_metrics",
    "collect_real_llm_environment_info",
    "run_real_llm_baseline",
]


# ============================================================
# 常量
# ============================================================

PHASE_3_9_5: Final[str] = "3.9.5"
BASELINE_TYPE_REAL_LLM: Final[str] = "real_llm"

#: execution_mode 常量（3.9.4 的 deterministic-fake-generator 对应物）
REAL_LLM_EXECUTION_MODE: Final[str] = "real-llm"

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]

REAL_LLM_SNAPSHOT_PATH: Final[Path] = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "text_to_sql"
    / "baselines"
    / "phase_3_9_5_real_llm_baseline.json"
)

REAL_LLM_REPORT_PATH: Final[Path] = (
    _REPO_ROOT
    / "docs" / "evaluation" / "text-to-sql-real-llm-baseline-3.9.5.md"
)

#: 复用 3.9.4 隔离项目 id 集合
DEFAULT_ISOLATION_PROJECT_IDS: Final[tuple[str, ...]] = (
    "eval-project-a",
    "eval-project-b",
)

#: Snapshot / Report 自检：禁止出现的敏感键
_FORBIDDEN_SNAPSHOT_KEYS: Final[frozenset[str]] = frozenset(
    {"api_key", "password", "database_url", "dsn", "connection_string",
     "secret", "token", "authorization", "llm_api_key"}
)

#: Snapshot / Report 自检：禁止出现的敏感字面量片段
_FORBIDDEN_SNAPSHOT_FRAGMENTS: Final[tuple[str, ...]] = (
    "postgresql://", "postgres://", "sk-", "bearer ", "authorization:",
)


# ============================================================
# Metrics（3.9.4 + LLM 专属 2 个）
# ============================================================

@dataclass(frozen=True)
class TextToSQLRealLLMBaselineMetrics:
    """Real LLM Baseline 统计结果。

    复用 3.9.4 全部计数 + 新增 2 个 LLM 专属计数（llm_generated /
    validator_accepted）。比率以 property 暴露，单一真值。
    """

    # ---- 3.9.4 计数（来自 calculate_baseline_metrics） ----
    total_cases: int
    passed_cases: int
    failed_cases: int
    validation_passed_cases: int
    validation_expectation_cases: int
    validation_expectation_passed_cases: int
    execution_cases: int
    execution_passed_cases: int
    security_cases: int
    security_passed_cases: int
    project_isolation_cases: int
    project_isolation_passed_cases: int
    # ---- LLM 专属 ----
    llm_generated_cases: int
    validator_accepted_cases: int

    # ---------- 3.9.4 比率（property 复用单一真值） ----------

    @property
    def expectation_passed_cases(self) -> int:
        return self.passed_cases

    @property
    def expectation_pass_rate(self) -> float | None:
        return _rate(self.passed_cases, self.total_cases)

    @property
    def overall_pass_rate(self) -> float | None:
        return self.expectation_pass_rate

    @property
    def validation_pass_rate(self) -> float | None:
        return _rate(self.validation_passed_cases, self.total_cases)

    @property
    def validation_expectation_pass_rate(self) -> float | None:
        return _rate(
            self.validation_expectation_passed_cases,
            self.validation_expectation_cases,
        )

    @property
    def execution_pass_rate(self) -> float | None:
        return _rate(self.execution_passed_cases, self.execution_cases)

    @property
    def security_expectation_pass_rate(self) -> float | None:
        return _rate(self.security_passed_cases, self.security_cases)

    @property
    def project_isolation_pass_rate(self) -> float | None:
        return _rate(
            self.project_isolation_passed_cases, self.project_isolation_cases
        )

    # ---------- LLM 专属比率 ----------

    @property
    def llm_generation_success_rate(self) -> float | None:
        """生成阶段成功 = 拿到可进入 Validator 的 SQL / Total。"""
        return _rate(self.llm_generated_cases, self.total_cases)

    @property
    def validator_acceptance_rate(self) -> float | None:
        """Validator 接受率 = Validator 通过 / LLM 实际生成出的 SQL。"""
        return _rate(
            self.validator_accepted_cases, self.llm_generated_cases
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "validation_passed_cases": self.validation_passed_cases,
            "validation_expectation_cases": self.validation_expectation_cases,
            "validation_expectation_passed_cases": (
                self.validation_expectation_passed_cases
            ),
            "execution_cases": self.execution_cases,
            "execution_passed_cases": self.execution_passed_cases,
            "security_cases": self.security_cases,
            "security_passed_cases": self.security_passed_cases,
            "project_isolation_cases": self.project_isolation_cases,
            "project_isolation_passed_cases": (
                self.project_isolation_passed_cases
            ),
            "llm_generated_cases": self.llm_generated_cases,
            "validator_accepted_cases": self.validator_accepted_cases,
            "expectation_pass_rate": self.expectation_pass_rate,
            "overall_pass_rate": self.overall_pass_rate,
            "validation_pass_rate": self.validation_pass_rate,
            "validation_expectation_pass_rate": (
                self.validation_expectation_pass_rate
            ),
            "execution_pass_rate": self.execution_pass_rate,
            "security_expectation_pass_rate": (
                self.security_expectation_pass_rate
            ),
            "project_isolation_pass_rate": self.project_isolation_pass_rate,
            "llm_generation_success_rate": self.llm_generation_success_rate,
            "validator_acceptance_rate": self.validator_acceptance_rate,
        }


def calculate_real_llm_baseline_metrics(
    results: Sequence[TextToSQLEvaluationResult],
    cases: Sequence[TextToSQLEvaluationCase] = (),
    *,
    isolation_project_ids: Sequence[str] = DEFAULT_ISOLATION_PROJECT_IDS,
) -> TextToSQLRealLLMBaselineMetrics:
    """纯函数：3.9.4 计数 + LLM 专属计数（§七 / §二十三：无 DB / 网络 / 文件写入）。

    - ``llm_generated_cases``  =  ``result.generated_sql is not None``
      （Generator 把 SQL 交到 Validator —— 哪怕 Validator 又拒了）；
    - ``validator_accepted_cases``  =  ``result.validation_passed is True``。
    """
    baseline = calculate_baseline_metrics(
        results, cases, isolation_project_ids=isolation_project_ids
    )
    llm_generated = sum(1 for r in results if r.generated_sql is not None)
    validator_accepted = sum(1 for r in results if r.validation_passed)
    return TextToSQLRealLLMBaselineMetrics(
        total_cases=baseline.total_cases,
        passed_cases=baseline.passed_cases,
        failed_cases=baseline.failed_cases,
        validation_passed_cases=baseline.validation_passed_cases,
        validation_expectation_cases=baseline.validation_expectation_cases,
        validation_expectation_passed_cases=(
            baseline.validation_expectation_passed_cases
        ),
        execution_cases=baseline.execution_cases,
        execution_passed_cases=baseline.execution_passed_cases,
        security_cases=baseline.security_cases,
        security_passed_cases=baseline.security_passed_cases,
        project_isolation_cases=baseline.project_isolation_cases,
        project_isolation_passed_cases=(
            baseline.project_isolation_passed_cases
        ),
        llm_generated_cases=llm_generated,
        validator_accepted_cases=validator_accepted,
    )


# ============================================================
# Environment（继承 3.9.4，绝不留机密）
# ============================================================

def collect_real_llm_environment_info(
    *,
    database: str = UNAVAILABLE,
    execution_mode: str = REAL_LLM_EXECUTION_MODE,
) -> TextToSQLBaselineEnvironment:
    """收集 Real LLM 运行环境（不读 API Key / 连接串）。

    与 3.9.4 区别：execution_mode 默认 ``real-llm``；其他字段格式一致。
    """
    from backend.app.services.text_to_sql_baseline_service import (
        collect_environment_info,
    )
    env = collect_environment_info(
        database=database, execution_mode=execution_mode
    )
    # 暴露字段固定：Python / provider / model / database / execution_mode
    return env


# ============================================================
# 真实 LLM Baseline 聚合对象
# ============================================================

def _percent(rate: float | None) -> str:
    if rate is None:
        return "N/A"
    return f"{rate * 100:.2f}%"


def _failure_reasons(
    result: TextToSQLEvaluationResult,
    case_by_id: dict[str, TextToSQLEvaluationCase],
) -> tuple[str, ...]:
    reasons: list[str] = []
    for name in result.failed_expectations:
        if name == EXPECTATION_VALIDATION:
            case = case_by_id.get(result.case_id)
            expected = (
                case.expected.must_pass_validation
                if case is not None else None
            )
            reasons.append(
                f"expected validation={expected}, "
                f"actual validation={result.validation_passed}"
            )
        else:
            reasons.append(f"{name} not satisfied")
    if result.error_code:
        reasons.append(f"error_code={result.error_code}")
    return tuple(reasons)


@dataclass(frozen=True)
class TextToSQLRealLLMBaseline:
    """一次 Real LLM Baseline 的聚合结果（与 3.9.4 TextToSQLBaseline 同形）。"""

    phase: str
    dataset_path: str
    dataset_version: str
    total_cases: int
    results: tuple[TextToSQLEvaluationResult, ...]
    metrics: TextToSQLRealLLMBaselineMetrics
    environment: TextToSQLBaselineEnvironment = field(
        default_factory=collect_real_llm_environment_info
    )
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        compare=False,
    )

    # ---------- 工具 ----------

    def failed_results(self) -> tuple[TextToSQLEvaluationResult, ...]:
        return tuple(r for r in self.results if not r.passed)

    def case_snapshots(self) -> list[dict[str, Any]]:
        """Case 级快照：不保存 generated_sql（§六 / §十二）。

        ``duration_ms`` 是测量噪声，不写入 Snapshot（每次运行都会变），
        仅保留在 Result 对象里供 log / 调试使用。
        """
        return [
            {
                "case_id": result.case_id,
                "project_id": result.project_id,
                "passed": result.passed,
                "validation_passed": result.validation_passed,
                "execution_passed": result.execution_passed,
                "matched_expectations": list(result.matched_expectations),
                "failed_expectations": list(result.failed_expectations),
                "error_code": result.error_code,
            }
            for result in self.results
        ]

    def to_snapshot_dict(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "phase": self.phase,
            "baseline_type": BASELINE_TYPE_REAL_LLM,
            "dataset": {
                "path": self.dataset_path,
                "version": self.dataset_version,
                "total_cases": self.total_cases,
            },
            "execution_mode": self.environment.execution_mode,
            "environment": self.environment.as_dict(),
            "metrics": self.metrics.as_dict(),
            "cases": self.case_snapshots(),
            "generated_at": self.generated_at,
        }
        _assert_no_secrets(snapshot)
        return snapshot

    # ---------- Report（§十一 / §十二 / §十三） ----------

    def render_report(
        self,
        *,
        fake_baseline_metrics: dict[str, Any] | None = None,
    ) -> str:
        """渲染 Markdown Report（§十一 + §十三：3.9.4 vs 3.9.5 对比）。"""
        metrics = self.metrics
        lines = [
            f"# Text-to-SQL Real LLM Baseline — Phase {self.phase}",
            "",
            "> **声明**：本 Baseline 是**首次真实 DeepSeek Baseline**，不代表最终"
            "模型效果，也不代表生产准确率。本阶段只测量、不调优。",
            "",
            "## Dataset",
            "",
            f"- File: `{self.dataset_path}`",
            f"- Version: `{self.dataset_version}`",
            f"- Cases: {self.total_cases}",
            "",
            "## Model",
            "",
            f"- Provider: {self.environment.llm_provider}",
            f"- Model: {self.environment.llm_model}",
            f"- Execution Mode: {self.environment.execution_mode}",
            "",
            "## Metrics",
            "",
            "| Metric | Rate | Cases |",
            "|---|---:|---:|",
            f"| Total Cases | - | {metrics.total_cases} |",
            f"| Passed | - | {metrics.passed_cases} |",
            f"| Failed | - | {metrics.failed_cases} |",
            f"| Expectation Pass Rate | "
            f"{_percent(metrics.expectation_pass_rate)} | "
            f"{metrics.passed_cases}/{metrics.total_cases} |",
            "| Validation Expectation Pass Rate | "
            f"{_percent(metrics.validation_expectation_pass_rate)} | "
            f"{metrics.validation_expectation_passed_cases}/"
            f"{metrics.validation_expectation_cases} |",
            f"| LLM Generation Success Rate | "
            f"{_percent(metrics.llm_generation_success_rate)} | "
            f"{metrics.llm_generated_cases}/{metrics.total_cases} |",
            f"| Validator Acceptance Rate | "
            f"{_percent(metrics.validator_acceptance_rate)} | "
            f"{metrics.validator_accepted_cases}/{metrics.llm_generated_cases} |",
            f"| Execution Pass Rate | "
            f"{_percent(metrics.execution_pass_rate)} | "
            f"{metrics.execution_passed_cases}/{metrics.execution_cases} |",
            f"| Security Pass Rate | "
            f"{_percent(metrics.security_expectation_pass_rate)} | "
            f"{metrics.security_passed_cases}/{metrics.security_cases} |",
            f"| Project Isolation Pass Rate | "
            f"{_percent(metrics.project_isolation_pass_rate)} | "
            f"{metrics.project_isolation_passed_cases}/"
            f"{metrics.project_isolation_cases} |",
            "",
        ]

        if fake_baseline_metrics is not None:
            lines.extend(_render_comparison_table(metrics, fake_baseline_metrics))
            lines.append("")

        lines.extend(
            [
                "## Environment",
                "",
                f"- Python: {self.environment.python_version}",
                f"- PostgreSQL: {self.environment.database}",
                f"- LLM Provider: {self.environment.llm_provider}",
                f"- LLM Model: {self.environment.llm_model}",
                f"- Dataset Version: {self.dataset_version}",
                "",
                f"Generated At: {self.generated_at}",
                "",
                "## Failed Cases",
                "",
            ]
        )
        failed = self.failed_results()
        if not failed:
            lines.append("- None")
        else:
            try:
                case_by_id = {
                    case.case_id: case
                    for case in load_text_to_sql_regression_dataset(
                        self.dataset_path
                    )
                }
            except (OSError, ValueError):
                case_by_id = {}
            for result in failed:
                lines.append(f"- `{result.case_id}`:")
                lines.append(f"  - project_id: `{result.project_id}`")
                lines.append(f"  - validation_passed: "
                             f"`{result.validation_passed}`")
                if result.error_code:
                    lines.append(f"  - error_code: `{result.error_code}`")
                for reason in _failure_reasons(result, case_by_id):
                    lines.append(f"  - reason: {reason}")
        lines.append("")
        return "\n".join(lines)

    def render_summary(self) -> str:
        """CLI 摘要。"""
        metrics = self.metrics
        return "\n".join(
            [
                f"Text-to-SQL Real LLM Baseline — Phase {self.phase}",
                "",
                f"Model: {self.environment.llm_provider}/"
                f"{self.environment.llm_model}",
                f"Dataset: {Path(self.dataset_path).name} "
                f"(version {self.dataset_version})",
                f"Cases: {self.total_cases}",
                "",
                f"Expectation Pass Rate: "
                f"{_percent(metrics.expectation_pass_rate)}",
                "Validation Expectation Pass Rate: "
                f"{_percent(metrics.validation_expectation_pass_rate)}",
                f"LLM Generation Success Rate: "
                f"{_percent(metrics.llm_generation_success_rate)}",
                f"Validator Acceptance Rate: "
                f"{_percent(metrics.validator_acceptance_rate)}",
                f"Execution Pass Rate: "
                f"{_percent(metrics.execution_pass_rate)}",
                f"Security Pass Rate: "
                f"{_percent(metrics.security_expectation_pass_rate)}",
                "Project Isolation Pass Rate: "
                f"{_percent(metrics.project_isolation_pass_rate)}",
                "",
                f"Passed: {metrics.passed_cases}",
                f"Failed: {metrics.failed_cases}",
            ]
        )


def _render_comparison_table(
    real: TextToSQLRealLLMBaselineMetrics,
    fake: dict[str, Any],
) -> list[str]:
    """3.9.4 vs 3.9.5 客观指标对比表（§十三）。"""
    fields = [
        ("Total Cases", real.total_cases, fake.get("total_cases")),
        ("Expectation Pass Rate",
         _percent(real.expectation_pass_rate),
         _percent(fake.get("expectation_pass_rate"))),
        ("Validation Expectation Pass Rate",
         _percent(real.validation_expectation_pass_rate),
         _percent(fake.get("validation_expectation_pass_rate"))),
        ("Security Pass Rate",
         _percent(real.security_expectation_pass_rate),
         _percent(fake.get("security_expectation_pass_rate"))),
        ("Project Isolation Pass Rate",
         _percent(real.project_isolation_pass_rate),
         _percent(fake.get("project_isolation_pass_rate"))),
        ("LLM Generation Success Rate",
         _percent(real.llm_generation_success_rate),
         _render_not_applicable()),
        ("Validator Acceptance Rate",
         _percent(real.validator_acceptance_rate),
         _render_not_applicable()),
        ("Execution Pass Rate",
         _percent(real.execution_pass_rate),
         _render_not_applicable()),
    ]
    lines = [
        "## Phase 3.9.4 vs Phase 3.9.5",
        "",
        "| Metric | 3.9.4 Fake | 3.9.5 Real |",
        "|---|---:|---:|",
    ]
    for label, r_val, f_val in fields:
        lines.append(f"| {label} | {f_val} | {r_val} |")
    return lines


def _render_not_applicable() -> str:
    return "N/A"


# ============================================================
# Secret Safety（§十：绝不允许敏感信息进 Snapshot）
# ============================================================

def _assert_no_secrets(payload: Any) -> None:
    """Snapshot 自检：敏感键 / 敏感字面量片段一律拒绝落盘。"""
    forbidden_keys: set[str] = set()
    stack: list[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            forbidden_keys |= {str(k).lower() for k in node
                                if str(k).lower() in _FORBIDDEN_SNAPSHOT_KEYS}
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    if forbidden_keys:
        raise ValueError(
            f"snapshot contains forbidden keys: {sorted(forbidden_keys)}"
        )
    blob = repr(payload).lower()
    for fragment in _FORBIDDEN_SNAPSHOT_FRAGMENTS:
        if fragment in blob:
            raise ValueError(
                f"snapshot contains forbidden fragment: {fragment!r}"
            )


# ============================================================
# Runner（§十七 / §二十四：复用 3.9.3 + 3.9.4）
# ============================================================

async def run_real_llm_baseline(
    *,
    generator: Any,
    context_resolver: Any,
    validator: Any | None = None,
    executor: Any | None = None,
    cases: Sequence[TextToSQLEvaluationCase] | None = None,
    dataset_path: str | Path | None = None,
    phase: str = PHASE_3_9_5,
    environment: TextToSQLBaselineEnvironment | None = None,
    isolation_project_ids: Sequence[str] = DEFAULT_ISOLATION_PROJECT_IDS,
) -> TextToSQLRealLLMBaseline:
    """跑一次 Real LLM Baseline 并产出结构化结果（复用 3.9.3 Runner + 3.9.4 Metrics）。

    Args:
        generator:        真实 ``TextToSQLService``（或 Fake 测试用）；
        context_resolver: case → 生成上下文；
        validator / executor: 透传给 3.9.3 Runner（默认 Validator = ``SQLValidatorService``）。
        cases / dataset_path: 复用 3.9.4 行为。
        environment: 可选环境信息；``None`` 时自动读取 settings.llm.provider/model。
    """
    resolved_cases = (
        tuple(cases)
        if cases is not None
        else load_text_to_sql_regression_dataset(dataset_path)
    )
    dataset_version = read_dataset_version(dataset_path)

    runner = TextToSQLEvaluationRunner(
        generator=generator,
        context_resolver=context_resolver,
        validator=validator,
        executor=executor,
    )
    summary = await runner.run(resolved_cases)
    metrics = calculate_real_llm_baseline_metrics(
        summary.results, resolved_cases,
        isolation_project_ids=isolation_project_ids,
    )
    resolved_dataset_path = (
        Path(dataset_path)
        if dataset_path is not None
        else DEFAULT_REGRESSION_DATASET_PATH
    )
    baseline = TextToSQLRealLLMBaseline(
        phase=phase,
        dataset_path=_relative_path(resolved_dataset_path),
        dataset_version=dataset_version,
        total_cases=len(resolved_cases),
        results=summary.results,
        metrics=metrics,
        environment=(
            environment
            if environment is not None
            else collect_real_llm_environment_info()
        ),
    )
    logger.info(
        "text-to-sql real llm baseline completed",
        extra={
            "phase": phase,
            "total_cases": metrics.total_cases,
            "passed_cases": metrics.passed_cases,
            "failed_cases": metrics.failed_cases,
            "expectation_pass_rate": metrics.expectation_pass_rate,
            "llm_generation_success_rate": metrics.llm_generation_success_rate,
        },
    )
    return baseline