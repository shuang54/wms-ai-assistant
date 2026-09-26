"""Phase 3.9.21 — Baseline LLM natural fluctuation & repeatability.

LLM Evaluation / Experimental Baseline Stability (NOT prompt optimization).

```text
Baseline Prompt v1 (unchanged, hash-frozen)
      ↓  same 14 cases / same DeepSeek / same schema+semantic context
Run A: 14 cases
Run B: 14 cases
Run C: 14 cases
      ↓
three-way comparison (42 DeepSeek calls total, no reruns)
```

This module REUSES the Phase 3.9.20 experiment framework
(``PromptExperimentRunner`` / ``ExperimentCaseResult`` / metrics) — it does
not re-implement generation, validation, execution or evaluation.

Discipline:
- Prompt / dataset / ground truth / fixtures are read-only; hashes are
  recorded and must be identical across all three runs (§五), otherwise the
  experiment is INVALID.
- Case order follows ``text_to_sql_regression.yaml`` exactly (§六).
- No cherry-picking: failed calls are recorded, never retried (§四).
- SQL text is a diagnostic only (§八 / §十一): ``sql_text_same`` and
  ``sql_variant_count`` never feed the stability verdict. ``STABLE`` is
  defined purely by generation / validation / structural agreement.
- Failure categories reuse the existing taxonomy (§十四) — no invented
  ``MODEL_BAD`` / ``PROMPT_BAD`` labels.
- Interpretation is restricted to observable facts (§二十一).
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from backend.app.services.text_to_sql_baseline_service import RATE_PRECISION
from backend.app.services.text_to_sql_evaluation_service import (
    TextToSQLEvaluationCase,
    TextToSQLEvaluationResult,
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_prompt_experiment_service import (
    PROMPT_VERSION,
    SYSTEM_PROMPT_PATH,
    USER_PROMPT_PATH,
    RETRY_PROMPT_PATH,
    ExperimentCaseResult,
    PromptExperimentRunner,
    _FORBIDDEN_SNAPSHOT_FRAGMENTS,
    _FORBIDDEN_SNAPSHOT_KEYS,
    load_prompt_fingerprint,
    read_dataset_fingerprints,
)
from backend.app.services.text_to_sql_real_llm_baseline_service import (
    TextToSQLRealLLMBaselineMetrics,
    calculate_real_llm_baseline_metrics,
    collect_real_llm_environment_info,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PHASE_3_9_21",
    "EXPERIMENT_TYPE_3_9_21",
    "STABILITY_STABLE",
    "STABILITY_NON_DETERMINISTIC",
    "SNAPSHOT_3_9_21_PATH",
    "REPORT_3_9_21_PATH",
    "StabilityCaseRecord",
    "StabilityRun",
    "CaseStabilityComparison",
    "BaselineStabilitySummary",
    "BaselineStabilityRunner",
    "validate_stability_snapshot",
]


# ============================================================
# 常量 & 路径
# ============================================================

PHASE_3_9_21: Final[str] = "3.9.21"
EXPERIMENT_TYPE_3_9_21: Final[str] = "baseline_llm_stability"
STABILITY_STABLE: Final[str] = "STABLE"
STABILITY_NON_DETERMINISTIC: Final[str] = "NON_DETERMINISTIC"
RUN_COUNT_REQUIRED: Final[int] = 3

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]

SNAPSHOT_3_9_21_PATH: Final = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_21_baseline_stability.json"
)
REPORT_3_9_21_PATH: Final = (
    _REPO_ROOT / "docs" / "evaluation"
    / "text-to-sql-baseline-stability-3.9.21.md"
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_case_order_hash(cases: Sequence[TextToSQLEvaluationCase]) -> str:
    """§六：case 顺序指纹（dataset hash 之外再固化一份顺序证明）。"""
    return _sha256_text("\n".join(c.case_id for c in cases))


# ============================================================
# Per-case record（§七）
# ============================================================

@dataclass(frozen=True)
class StabilityCaseRecord:
    """单 Run 内单 case 的结果（含 ``question``，§七）。"""

    case_id: str
    project_id: str | None
    question: str
    generated_sql: str | None
    generation_success: bool
    validation_success: bool
    structural_expectation_match: bool
    security_category: str | None
    execution_success: bool | None
    semantic_result_correct: bool | None
    failure_categories: tuple[str, ...]
    matched_expectations: tuple[str, ...]
    error_code: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "project_id": self.project_id,
            "question": self.question,
            "generated_sql": self.generated_sql,
            "generation_success": self.generation_success,
            "validation_success": self.validation_success,
            "structural_expectation_match": self.structural_expectation_match,
            "security_category": self.security_category,
            "execution_success": self.execution_success,
            "semantic_result_correct": self.semantic_result_correct,
            "failure_categories": list(self.failure_categories),
            "matched_expectations": list(self.matched_expectations),
            "error_code": self.error_code,
        }


def _stability_case_record(
    result: ExperimentCaseResult, case: TextToSQLEvaluationCase
) -> StabilityCaseRecord:
    return StabilityCaseRecord(
        case_id=result.case_id,
        project_id=result.project_id,
        question=case.question,
        generated_sql=result.generated_sql,
        generation_success=result.generation_success,
        validation_success=result.validation_success,
        structural_expectation_match=result.structural_expectation_match,
        security_category=result.security_category,
        execution_success=result.execution_success,
        semantic_result_correct=result.semantic_result_correct,
        failure_categories=result.failure_categories,
        matched_expectations=result.matched_expectations,
        error_code=result.error_code,
    )


# ============================================================
# Run（一次完整 14-case 运行）
# ============================================================

@dataclass(frozen=True)
class StabilityRun:
    run_id: str
    prompt_version: str
    prompt_hash: str
    results: tuple[StabilityCaseRecord, ...]
    metrics: dict[str, Any]
    headline_metrics: dict[str, Any]
    total_cases: int
    deepseek_calls: int
    db_calls: int
    network_calls: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "total_cases": self.total_cases,
            "metrics": self.metrics,
            "headline_metrics": self.headline_metrics,
            "deepseek_calls": self.deepseek_calls,
            "db_calls": self.db_calls,
            "network_calls": self.network_calls,
            "results": [r.to_dict() for r in self.results],
        }


# ============================================================
# Case-level stability（§九 / §十 / §十一 / §十三）
# ============================================================

def _pair_key(a: str, b: str) -> str:
    return f"{a}_vs_{b}"


@dataclass(frozen=True)
class CaseStabilityComparison:
    case_id: str
    question: str
    stability_status: str
    sql_text_same: bool
    sql_variant_count: int
    generation_same: bool
    validation_same: bool
    structural_same: bool
    pairwise: dict[str, dict[str, bool]]
    run_results: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "question": self.question,
            "stability_status": self.stability_status,
            "sql_text_same": self.sql_text_same,
            "sql_variant_count": self.sql_variant_count,
            "generation_same": self.generation_same,
            "validation_same": self.validation_same,
            "structural_same": self.structural_same,
            "pairwise": self.pairwise,
            "run_results": self.run_results,
        }


def compare_case_across_runs(
    case_id: str,
    per_run: dict[str, StabilityCaseRecord],
    run_ids: Sequence[str],
) -> CaseStabilityComparison:
    """对单 case 做三向比较（不比较 SQL 文本作为成败，§八 / §十一）。"""
    records = [per_run[rid] for rid in run_ids]
    generation_same = len({r.generation_success for r in records}) == 1
    validation_same = len({r.validation_success for r in records}) == 1
    structural_same = len({r.structural_expectation_match for r in records}) == 1
    stable = generation_same and validation_same and structural_same

    sqls = [r.generated_sql for r in records]
    generated = [s for s in sqls if s]
    sql_variant_count = len(set(generated))
    sql_text_same = len(generated) == len(sqls) and sql_variant_count <= 1

    pairwise: dict[str, dict[str, bool]] = {}
    for a, b in itertools.combinations(run_ids, 2):
        ra, rb = per_run[a], per_run[b]
        pairwise[_pair_key(a, b)] = {
            "generation_agreement": (
                ra.generation_success == rb.generation_success
            ),
            "validation_agreement": (
                ra.validation_success == rb.validation_success
            ),
            "structural_agreement": (
                ra.structural_expectation_match
                == rb.structural_expectation_match
            ),
        }

    run_results = {
        rid: {
            "generation_success": rec.generation_success,
            "validation_success": rec.validation_success,
            "structural_expectation_match": rec.structural_expectation_match,
            "generated_sql": rec.generated_sql,
            "failure_categories": list(rec.failure_categories),
        }
        for rid, rec in per_run.items()
    }
    first = records[0]
    return CaseStabilityComparison(
        case_id=case_id,
        question=first.question,
        stability_status=(
            STABILITY_STABLE if stable else STABILITY_NON_DETERMINISTIC
        ),
        sql_text_same=sql_text_same,
        sql_variant_count=sql_variant_count,
        generation_same=generation_same,
        validation_same=validation_same,
        structural_same=structural_same,
        pairwise=pairwise,
        run_results=run_results,
    )


# ============================================================
# Environment（§三：固定条件全部记录；temperature/max_tokens 为 client default）
# ============================================================

def collect_stability_environment() -> dict[str, Any]:
    env = collect_real_llm_environment_info().as_dict()
    from backend.app.config import settings  # lazy: --check stays offline

    llm = getattr(settings, "llm", None)
    environment: dict[str, Any] = {
        "llm_provider": env.get("llm_provider"),
        "llm_model": env.get("llm_model"),
        "llm_base_url": getattr(llm, "base_url", None),
        # 当前项目未配置 temperature / max_tokens：如实记录 client default，
        # 不擅自修改（§三）。
        "temperature": getattr(llm, "temperature", None),
        "max_tokens": getattr(llm, "max_tokens", None),
        "temperature_note": "not configured; OpenAI-compatible client default",
        "max_tokens_note": "not configured; OpenAI-compatible client default",
        "timeout_connect": getattr(llm, "timeout_connect", None),
        "timeout_read": getattr(llm, "timeout_read", None),
        "execution_mode": env.get("execution_mode"),
        "python_version": env.get("python_version"),
    }
    return environment


# ============================================================
# Summary（§十五 snapshot 结构）
# ============================================================

def _rate(count: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(count / total, RATE_PRECISION)


@dataclass(frozen=True)
class BaselineStabilitySummary:
    phase: str
    experiment_type: str
    run_count: int
    prompt_version: str
    prompt_hash: str
    dataset_sha256: str
    ground_truth_sha256: str
    fixture_schema_sha256: str
    fixture_data_sha256: str
    case_order_hash: str
    runs: tuple[StabilityRun, ...]
    case_comparisons: tuple[CaseStabilityComparison, ...]
    summary: dict[str, Any]
    environment: dict[str, Any]
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        compare=False,
    )

    # ---------- snapshot ----------

    def to_snapshot_dict(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "phase": self.phase,
            "experiment_type": self.experiment_type,
            "run_count": self.run_count,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "dataset_sha256": self.dataset_sha256,
            "ground_truth_sha256": self.ground_truth_sha256,
            "fixture_schema_sha256": self.fixture_schema_sha256,
            "fixture_data_sha256": self.fixture_data_sha256,
            "case_order_hash": self.case_order_hash,
            "runs": [r.to_dict() for r in self.runs],
            "case_comparisons": [c.to_dict() for c in self.case_comparisons],
            "summary": self.summary,
            "environment": self.environment,
            "generated_at": self.generated_at,
        }
        _assert_no_secrets(snapshot)
        return snapshot

    # ---------- rendering ----------

    def render_summary(self) -> str:
        lines = [
            f"Baseline LLM Stability — Phase {self.phase}",
            "",
            f"Experiment type : {self.experiment_type}",
            f"Runs            : {self.run_count} x 14 cases",
            f"Prompt version  : {self.prompt_version}",
            f"Prompt hash     : {self.prompt_hash[:16]}…",
            f"Prompt hash equal across runs: "
            f"{len({r.prompt_hash for r in self.runs}) == 1}",
            "",
            "Per-run structural match: "
            + ", ".join(
                f"{r.run_id}={_fmt_rate(r.metrics.get('expectation_pass_rate'))}"
                for r in self.runs
            ),
            f"STABLE cases           : "
            f"{self.summary['stability']['stable_cases']}",
            f"NON_DETERMINISTIC cases: "
            f"{self.summary['stability']['non_deterministic_cases']}",
            f"Cases with >1 SQL variant: "
            f"{self.summary['sql_variants']['cases_with_multiple_variants']}",
            "",
            f"DeepSeek calls: {sum(r.deepseek_calls for r in self.runs)}",
            f"DB calls      : {sum(r.db_calls for r in self.runs)}",
            f"Network calls : {sum(r.network_calls for r in self.runs)}",
        ]
        return "\n".join(lines)

    def render_report(self) -> str:
        out: list[str] = []
        add = out.append
        add(f"# Baseline LLM Stability — Phase {self.phase}")
        add("")
        add("> **声明**：本阶段是 **LLM Evaluation / Experimental Baseline")
        add("> Stability**，不是 Prompt Optimization。仅测量当前 Baseline")
        add("> Prompt v1 在相同条件下的自然输出波动；不评估哪个 Prompt /")
        add("> 模型更好，也不给出优化建议（§二十一 / §二十三）。")
        add("")
        add("## 1. 实验条件（三次 Run 完全相同）")
        add("")
        add(f"- model: `{self.environment.get('llm_model')}`")
        add(f"- provider: `{self.environment.get('llm_provider')}`")
        add(f"- base_url: `{self.environment.get('llm_base_url')}`")
        add(f"- prompt_version: `{self.prompt_version}`")
        add(f"- temperature: `{self.environment.get('temperature')}`"
            f"（{self.environment.get('temperature_note')}）")
        add(f"- max_tokens: `{self.environment.get('max_tokens')}`"
            f"（{self.environment.get('max_tokens_note')}）")
        add(f"- dataset: `text_to_sql_regression.yaml`（14 cases，顺序固定）")
        add(f"- execution_mode: `{self.environment.get('execution_mode')}`"
            "（本阶段 DB calls = 0，§二十）")
        add("")
        add("## 2. Prompt hash")
        add("")
        add(f"- prompt_hash: `{self.prompt_hash[:16]}…`")
        add(f"- Run A/B/C hash 一致: "
            f"`{len({r.prompt_hash for r in self.runs}) == 1}`")
        add("")
        add("## 3. Dataset / Ground Truth / Fixture hash")
        add("")
        add(f"- dataset_sha256: `{self.dataset_sha256[:16]}…`")
        add(f"- ground_truth_sha256: `{self.ground_truth_sha256[:16]}…`")
        add(f"- fixture_schema_sha256: `{self.fixture_schema_sha256[:16]}…`")
        add(f"- fixture_data_sha256: `{self.fixture_data_sha256[:16]}…`")
        add(f"- case_order_hash: `{self.case_order_hash[:16]}…`")
        add("")
        add("## 4. Per-run metrics")
        add("")
        add("| Metric | " + " | ".join(r.run_id for r in self.runs) + " |")
        add("|---|" + "---:|" * len(self.runs))
        for key, label in (
            ("llm_generation_success_rate", "generation_success"),
            ("validator_acceptance_rate", "validation_acceptance"),
            ("expectation_pass_rate", "structural_expectation_match"),
            ("security_expectation_pass_rate", "security_pass"),
            ("execution_pass_rate", "execution_success"),
        ):
            add(f"| {label} | " + " | ".join(
                _fmt_rate(r.metrics.get(key)) for r in self.runs
            ) + " |")
        add("")
        add("> semantic_result_accuracy 未记录：本阶段不修改 pipeline 以强行")
        add("> 增加该指标（§七）。")
        add("")
        add("## 5. Case-level stability")
        add("")
        add("| case_id | " + " | ".join(
            f"{r.run_id}" for r in self.runs
        ) + " | stability | variants | sql_same |")
        add("|---|" + "---|" * len(self.runs) + "---|---:|---|")
        by_case = {c.case_id: c for c in self.case_comparisons}
        for case_id, comp in by_case.items():
            cells = []
            for r in self.runs:
                rr = comp.run_results[r.run_id]
                cells.append(
                    "PASS" if rr["structural_expectation_match"] else "FAIL"
                )
            add(
                f"| `{comp.case_id}` | " + " | ".join(cells)
                + f" | {comp.stability_status} "
                f"| {comp.sql_variant_count} "
                f"| {'yes' if comp.sql_text_same else 'no'} |"
            )
        add("")
        add("## 6. Case agreement（§九）")
        add("")
        for pair, values in self.summary["pairwise_agreement"].items():
            add(f"- {pair}: generation {values['generation_agreement']}/14, "
                f"validation {values['validation_agreement']}/14, "
                f"structural {values['structural_agreement']}/14")
        add("")
        add("## 7. SQL variant distribution（§十三，diagnostic only）")
        add("")
        add(f"- cases with 1 SQL variant: "
            f"{self.summary['sql_variants']['cases_with_single_variant']}")
        add(f"- cases with >1 SQL variants: "
            f"{self.summary['sql_variants']['cases_with_multiple_variants']}")
        add(f"- max variant count: "
            f"{self.summary['sql_variants']['max_variant_count']}")
        add("")
        non_det = [
            c for c in self.case_comparisons
            if c.stability_status == STABILITY_NON_DETERMINISTIC
        ]
        if non_det:
            add("## 8. NON_DETERMINISTIC cases")
            add("")
            for comp in non_det:
                add(f"### `{comp.case_id}`")
                add("")
                for r in self.runs:
                    rr = comp.run_results[r.run_id]
                    add(f"- Run {r.run_id}: "
                        f"{'PASS' if rr['structural_expectation_match'] else 'FAIL'}"
                        f" | failure={rr['failure_categories']}")
                    add(f"  - sql: `{_short(rr['generated_sql'])}`")
                add("")
        add("## 9. Calls")
        add("")
        add(f"- DeepSeek calls: {sum(r.deepseek_calls for r in self.runs)}")
        add(f"- DB calls: {sum(r.db_calls for r in self.runs)}")
        add(f"- Network calls: {sum(r.network_calls for r in self.runs)}")
        add("")
        add("## 10. 结果解释（§二十一）")
        add("")
        s = self.summary
        add(f"- {s['stability']['stable_cases']}/14 cases 在三次运行中 "
            "generation/validation/structural 结果一致。")
        add(f"- {s['stability']['non_deterministic_cases']}/14 cases "
            "至少出现一次结果差异（NON_DETERMINISTIC）。")
        add(f"- {s['sql_variants']['cases_with_multiple_variants']}/14 cases "
            "产生多个 SQL variants（仅 diagnostic，不作为失败）。")
        add("")
        add("> 仅陈述可观测事实；不对 Prompt / 模型 / Schema 作出评价。")
        add("")
        return "\n".join(out)


def _fmt_rate(rate: float | None) -> str:
    return "N/A" if rate is None else f"{rate * 100:.2f}%"


def _short(sql: str | None) -> str:
    if not sql:
        return ""
    return sql.replace("`", "'")[:160]


def _assert_no_secrets(payload: Any) -> None:
    from backend.app.services.text_to_sql_prompt_experiment_service import (
        _assert_no_secrets as _check,
    )
    _check(payload)


# ============================================================
# Runner（§四：3 runs, 42 calls, 不补跑）
# ============================================================

class BaselineStabilityRunner:
    """三次独立运行同一 Baseline（复用 3.9.20 框架，不重复实现）。"""

    def __init__(
        self,
        *,
        runner: PromptExperimentRunner,
        run_count: int = RUN_COUNT_REQUIRED,
        run_labels: Sequence[str] = ("A", "B", "C"),
        cases: Sequence[TextToSQLEvaluationCase] | None = None,
    ) -> None:
        if run_count > len(run_labels):
            raise ValueError(
                f"run_count={run_count} exceeds labels {list(run_labels)}"
            )
        self._runner = runner
        self._run_count = run_count
        self._labels = tuple(run_labels[:run_count])
        self._cases = tuple(
            cases
            if cases is not None
            else load_text_to_sql_regression_dataset()
        )

    async def run(self) -> BaselineStabilitySummary:
        case_by_id = {c.case_id: c for c in self._cases}
        runs: list[StabilityRun] = []
        for label in self._labels:
            variant = await self._runner.run_baseline()
            records = tuple(
                _stability_case_record(r, case_by_id[r.case_id])
                for r in variant.results
            )
            runs.append(
                StabilityRun(
                    run_id=label,
                    prompt_version=variant.prompt_version,
                    prompt_hash=variant.prompt_hash,
                    results=records,
                    metrics=variant.metrics.as_dict(),
                    headline_metrics=variant.headline().to_dict(),
                    total_cases=variant.total_cases,
                    deepseek_calls=variant.deepseek_calls,
                    db_calls=variant.db_calls,
                    network_calls=variant.network_calls,
                )
            )

        run_ids = [r.run_id for r in runs]
        per_case: dict[str, dict[str, StabilityCaseRecord]] = {}
        for run in runs:
            for rec in run.results:
                per_case.setdefault(rec.case_id, {})[run.run_id] = rec
        comparisons = tuple(
            compare_case_across_runs(case_id, per_run, run_ids)
            for case_id, per_run in per_case.items()
        )
        # 保持 dataset 顺序
        order = {c.case_id: i for i, c in enumerate(self._cases)}
        comparisons = tuple(
            sorted(comparisons, key=lambda c: order.get(c.case_id, 10**9))
        )

        fp = read_dataset_fingerprints()
        return BaselineStabilitySummary(
            phase=PHASE_3_9_21,
            experiment_type=EXPERIMENT_TYPE_3_9_21,
            run_count=len(runs),
            prompt_version=runs[0].prompt_version,
            prompt_hash=runs[0].prompt_hash,
            dataset_sha256=fp["dataset_sha256"],
            ground_truth_sha256=fp["ground_truth_sha256"],
            fixture_schema_sha256=fp["fixture_schema_sha256"],
            fixture_data_sha256=fp["fixture_data_sha256"],
            case_order_hash=compute_case_order_hash(self._cases),
            runs=tuple(runs),
            case_comparisons=comparisons,
            summary=_build_summary(runs, comparisons),
            environment=collect_stability_environment(),
        )


def _build_summary(
    runs: Sequence[StabilityRun],
    comparisons: Sequence[CaseStabilityComparison],
) -> dict[str, Any]:
    run_ids = [r.run_id for r in runs]
    pairwise: dict[str, dict[str, int]] = {}
    for a, b in itertools.combinations(run_ids, 2):
        key = _pair_key(a, b)
        pairwise[key] = {
            "generation_agreement": sum(
                1 for c in comparisons
                if c.pairwise[key]["generation_agreement"]
            ),
            "validation_agreement": sum(
                1 for c in comparisons
                if c.pairwise[key]["validation_agreement"]
            ),
            "structural_agreement": sum(
                1 for c in comparisons
                if c.pairwise[key]["structural_agreement"]
            ),
        }
    stable = sum(
        1 for c in comparisons if c.stability_status == STABILITY_STABLE
    )
    non_det = len(comparisons) - stable
    multi = sum(1 for c in comparisons if c.sql_variant_count > 1)
    single = sum(
        1 for c in comparisons if 0 < c.sql_variant_count <= 1
    )
    max_variants = max(
        (c.sql_variant_count for c in comparisons), default=0
    )
    return {
        "per_run": {
            r.run_id: {
                "total_cases": r.total_cases,
                "generation_success": r.metrics.get("llm_generated_cases"),
                "validator_accepted": r.metrics.get("validator_accepted_cases"),
                "structural_matched": r.metrics.get("passed_cases"),
                "deepseek_calls": r.deepseek_calls,
                "db_calls": r.db_calls,
            }
            for r in runs
        },
        "pairwise_agreement": pairwise,
        "all_three_agree": {
            "generation": sum(1 for c in comparisons if c.generation_same),
            "validation": sum(1 for c in comparisons if c.validation_same),
            "structural": sum(1 for c in comparisons if c.structural_same),
        },
        "stability": {
            "stable_cases": stable,
            "non_deterministic_cases": non_det,
        },
        "sql_variants": {
            "cases_with_single_variant": single,
            "cases_with_multiple_variants": multi,
            "max_variant_count": max_variants,
        },
        "calls": {
            "deepseek_calls": sum(r.deepseek_calls for r in runs),
            "db_calls": sum(r.db_calls for r in runs),
            "network_calls": sum(r.network_calls for r in runs),
        },
    }


# ============================================================
# Offline validation（§十七：0 LLM / 0 DB / 0 network）
# ============================================================

def _record_from_snapshot(entry: dict[str, Any]) -> TextToSQLEvaluationResult:
    return TextToSQLEvaluationResult(
        case_id=str(entry.get("case_id", "")),
        project_id=entry.get("project_id"),
        question=str(entry.get("question", "")),
        generated_sql=entry.get("generated_sql"),
        validation_passed=bool(entry.get("validation_success", False)),
        execution_passed=entry.get("execution_success"),
        matched_expectations=tuple(entry.get("matched_expectations", ())),
        failed_expectations=tuple(entry.get("failure_categories", ())),
        error_code=entry.get("error_code"),
    )


def _recompute_run_metrics(
    run_dict: dict[str, Any],
    cases: Sequence[TextToSQLEvaluationCase],
) -> dict[str, Any]:
    results = tuple(
        _record_from_snapshot(e)
        for e in run_dict.get("results", [])
        if isinstance(e, dict)
    )
    return calculate_real_llm_baseline_metrics(results, cases).as_dict()


def _recompute_case_comparisons(
    runs: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    run_ids = [r.get("run_id") for r in runs]
    per_case: dict[str, dict[str, StabilityCaseRecord]] = {}
    questions: dict[str, str] = {}
    for run in runs:
        rid = run.get("run_id")
        for entry in run.get("results", []):
            if not isinstance(entry, dict):
                continue
            cid = str(entry.get("case_id", ""))
            questions.setdefault(cid, str(entry.get("question", "")))
            per_case.setdefault(cid, {})[rid] = StabilityCaseRecord(
                case_id=cid,
                project_id=entry.get("project_id"),
                question=str(entry.get("question", "")),
                generated_sql=entry.get("generated_sql"),
                generation_success=bool(entry.get("generation_success", False)),
                validation_success=bool(entry.get("validation_success", False)),
                structural_expectation_match=bool(
                    entry.get("structural_expectation_match", False)
                ),
                security_category=entry.get("security_category"),
                execution_success=entry.get("execution_success"),
                semantic_result_correct=entry.get("semantic_result_correct"),
                failure_categories=tuple(entry.get("failure_categories", ())),
                matched_expectations=tuple(
                    entry.get("matched_expectations", ())
                ),
                error_code=entry.get("error_code"),
            )
    rebuilt = [
        compare_case_across_runs(cid, per_run, run_ids)
        for cid, per_run in per_case.items()
    ]
    return [c.to_dict() for c in rebuilt]


def validate_stability_snapshot(
    snapshot: dict[str, Any],
    cases: Sequence[TextToSQLEvaluationCase] | None = None,
) -> list[str]:
    """纯 offline 校验 3.9.21 snapshot（§十七）。"""
    problems: list[str] = []
    if cases is None:
        cases = load_text_to_sql_regression_dataset()
    dataset_ids = [c.case_id for c in cases]

    # ---- phase / type / run_count ----
    if snapshot.get("phase") != PHASE_3_9_21:
        problems.append(f"phase: expected {PHASE_3_9_21!r}, "
                        f"got {snapshot.get('phase')!r}")
    if snapshot.get("experiment_type") != EXPERIMENT_TYPE_3_9_21:
        problems.append(f"experiment_type: expected "
                        f"{EXPERIMENT_TYPE_3_9_21!r}")
    runs = snapshot.get("runs") or []
    if snapshot.get("run_count") != RUN_COUNT_REQUIRED or \
            len(runs) != RUN_COUNT_REQUIRED:
        problems.append(
            f"run_count: expected {RUN_COUNT_REQUIRED}, "
            f"got {snapshot.get('run_count')} / {len(runs)} runs"
        )

    # ---- prompt hash 一致（§五） ----
    if snapshot.get("prompt_version") != PROMPT_VERSION:
        problems.append(f"prompt_version: expected {PROMPT_VERSION!r}")
    live_hash = load_prompt_fingerprint().combined_prompt_hash
    run_hashes = {r.get("prompt_hash") for r in runs if isinstance(r, dict)}
    if snapshot.get("prompt_hash") != live_hash:
        problems.append("prompt_hash not matching live prompt files")
    if len(run_hashes) != 1 or run_hashes != {snapshot.get("prompt_hash")}:
        problems.append(
            f"prompt_hash not identical across runs: {sorted(map(str, run_hashes))}"
        )

    # ---- dataset / ground truth / fixture hashes ----
    fp = read_dataset_fingerprints()
    for key in ("dataset_sha256", "ground_truth_sha256",
                "fixture_schema_sha256", "fixture_data_sha256"):
        if snapshot.get(key) != fp[key]:
            problems.append(
                f"{key}: snapshot={str(snapshot.get(key))[:16]}… "
                f"current={fp[key][:16]}…"
            )

    # ---- case order（§六） ----
    if snapshot.get("case_order_hash") != compute_case_order_hash(cases):
        problems.append("case_order_hash mismatch (dataset order changed?)")

    # ---- 每 run 14 cases，顺序一致 ----
    for run in runs:
        if not isinstance(run, dict):
            problems.append("run entry is not an object")
            continue
        rid = run.get("run_id")
        results = [
            e for e in run.get("results", []) if isinstance(e, dict)
        ]
        ids = [e.get("case_id") for e in results]
        if len(results) != len(dataset_ids):
            problems.append(
                f"run {rid}: expected {len(dataset_ids)} cases, "
                f"got {len(results)}"
            )
        if ids != dataset_ids:
            problems.append(f"run {rid}: case order mismatch")

    # ---- metrics 重算 ----
    for run in runs:
        if not isinstance(run, dict):
            continue
        rid = run.get("run_id")
        stored = run.get("metrics") or {}
        recalculated = _recompute_run_metrics(run, cases)
        for key, value in recalculated.items():
            if stored.get(key) != value:
                problems.append(
                    f"run {rid}.metrics.{key}: "
                    f"snapshot={stored.get(key)!r} "
                    f"recalculated={value!r}"
                )

    # ---- case comparison 重算 ----
    stored_cmp = snapshot.get("case_comparisons") or []
    rebuilt = _recompute_case_comparisons(runs)
    if len(stored_cmp) != len(rebuilt):
        problems.append(
            f"case_comparisons count: snapshot={len(stored_cmp)} "
            f"recalculated={len(rebuilt)}"
        )
    else:
        by_id = {c.get("case_id"): c for c in stored_cmp
                 if isinstance(c, dict)}
        for rec in rebuilt:
            old = by_id.get(rec["case_id"])
            if old is None:
                problems.append(
                    f"case_comparisons missing {rec['case_id']!r}"
                )
                continue
            for key in ("stability_status", "sql_text_same",
                        "sql_variant_count", "generation_same",
                        "validation_same", "structural_same"):
                if old.get(key) != rec[key]:
                    problems.append(
                        f"case {rec['case_id']}.{key}: "
                        f"snapshot={old.get(key)!r} "
                        f"recalculated={rec[key]!r}"
                    )

    # ---- summary 一致性 ----
    stored_summary = snapshot.get("summary") or {}
    if runs:
        rebuilt_summary = _build_summary(
            [
                StabilityRun(
                    run_id=r.get("run_id"),
                    prompt_version=r.get("prompt_version", ""),
                    prompt_hash=r.get("prompt_hash", ""),
                    results=(),
                    metrics=r.get("metrics") or {},
                    headline_metrics={},
                    total_cases=len(r.get("results", [])),
                    deepseek_calls=int(r.get("deepseek_calls", 0)),
                    db_calls=int(r.get("db_calls", 0)),
                    network_calls=int(r.get("network_calls", 0)),
                )
                for r in runs if isinstance(r, dict)
            ],
            [
                CaseStabilityComparison(
                    case_id=c.get("case_id", ""),
                    question="",
                    stability_status=c.get("stability_status", ""),
                    sql_text_same=bool(c.get("sql_text_same")),
                    sql_variant_count=int(c.get("sql_variant_count", 0)),
                    generation_same=bool(c.get("generation_same")),
                    validation_same=bool(c.get("validation_same")),
                    structural_same=bool(c.get("structural_same")),
                    pairwise=c.get("pairwise") or {},
                    run_results=c.get("run_results") or {},
                )
                for c in stored_cmp if isinstance(c, dict)
            ],
        )
        for section in ("stability", "sql_variants", "pairwise_agreement",
                        "all_three_agree", "calls"):
            if stored_summary.get(section) != rebuilt_summary.get(section):
                problems.append(
                    f"summary.{section} inconsistent with case data"
                )

    # ---- calls 自洽 ----
    total_ds = sum(int(r.get("deepseek_calls", 0)) for r in runs
                   if isinstance(r, dict))
    total_db = sum(int(r.get("db_calls", 0)) for r in runs
                   if isinstance(r, dict))
    if snapshot.get("summary", {}).get("calls", {}).get("deepseek_calls") \
            not in (None, total_ds):
        problems.append("summary.calls.deepseek_calls inconsistent")
    if snapshot.get("summary", {}).get("calls", {}).get("network_calls") \
            not in (None, total_ds + total_db):
        problems.append("summary.calls.network_calls inconsistent")

    # ---- secret safety ----
    forbidden: set[str] = set()
    stack: list[Any] = [snapshot]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            forbidden |= {
                str(k).lower() for k in node
                if str(k).lower() in _FORBIDDEN_SNAPSHOT_KEYS
            }
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    if forbidden:
        problems.append(f"forbidden keys: {sorted(forbidden)}")
    blob = json.dumps(snapshot, ensure_ascii=False).lower()
    for fragment in _FORBIDDEN_SNAPSHOT_FRAGMENTS:
        if fragment in blob:
            problems.append(f"forbidden fragment: {fragment!r}")

    return problems
