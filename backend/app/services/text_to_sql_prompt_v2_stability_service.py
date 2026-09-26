"""Phase 3.9.23 — Prompt v2 repeated stability validation.

Validation ONLY (no prompt optimization):

```text
Baseline v1 : Run A / Run B / Run C   (3 x 14 cases)
Candidate v2: Run A / Run B / Run C   (3 x 14 cases)
                                        = 84 generate() calls total
```

Fixed order (§六): baseline_a → baseline_b → baseline_c → candidate_a →
candidate_b → candidate_c; every run walks case 1 → case 14.

Reuses the existing experiment framework end to end:
- ``PromptExperimentRunner`` (3.9.20) for baseline/candidate runs
- ``StabilityCaseRecord`` / ``StabilityRun`` (3.9.21) for per-run records
- ``check_baseline_frozen`` / ``load_candidate_fingerprint`` (3.9.22)

Definitions (§十二 / §十六 / §十七):
- STABLE / NON_DETERMINISTIC is judged by **structural result only**.
- SQL text differences are diagnostics (``sql_variant_count``), never
  failures.
- Refusal (``generated_sql is None``) on a destructive request is the
  EXPECTED safe behavior for Candidate v2 — ``generation_success=False``
  there must not be read as a quality drop (§十三).
"""
from __future__ import annotations

import itertools
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from backend.app.services.text_to_sql_evaluation_service import (
    TextToSQLEvaluationCase,
    TextToSQLEvaluationResult,
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_baseline_stability_service import (
    STABILITY_NON_DETERMINISTIC,
    STABILITY_STABLE,
    StabilityCaseRecord,
    StabilityRun,
    _stability_case_record,
    collect_stability_environment,
    compute_case_order_hash,
)
from backend.app.services.text_to_sql_baseline_service import RATE_PRECISION
from backend.app.services.text_to_sql_prompt_experiment_service import (
    PromptExperimentRunner,
    _FORBIDDEN_SNAPSHOT_KEYS,
    _assert_no_secrets,
    _recompute_variant_metrics,
    load_prompt_fingerprint,
    read_dataset_fingerprints,
)
from backend.app.services.text_to_sql_prompt_candidate_v2_service import (
    SNAPSHOT_3_9_22_PATH,
    check_baseline_frozen,
    load_candidate_fingerprint,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PHASE_3_9_23",
    "EXPERIMENT_TYPE_3_9_23",
    "SNAPSHOT_3_9_23_PATH",
    "REPORT_3_9_23_PATH",
    "SAFETY_CASE_ID",
    "VariantCaseStability",
    "VariantStability",
    "CrossVariantCaseComparison",
    "V2StabilitySummary",
    "V2StabilityRunner",
    "check_candidate_frozen",
    "classify_cross_variant_changes",
    "build_safety_case_analysis",
    "validate_v2_stability_snapshot",
]


# ============================================================
# 常量 & 路径
# ============================================================

PHASE_3_9_23: Final[str] = "3.9.23"
EXPERIMENT_TYPE_3_9_23: Final[str] = "prompt_v2_repeated_stability"
RUNS_PER_VARIANT: Final[int] = 3
SAFETY_CASE_ID: Final[str] = "safety_delete_all_documents"

#: 固定运行顺序（§六）：Baseline A/B/C → Candidate A/B/C
RUN_IDS: Final[tuple[str, ...]] = (
    "baseline_a", "baseline_b", "baseline_c",
    "candidate_a", "candidate_b", "candidate_c",
)
BASELINE_RUN_IDS: Final[tuple[str, ...]] = RUN_IDS[:3]
CANDIDATE_RUN_IDS: Final[tuple[str, ...]] = RUN_IDS[3:]

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]

SNAPSHOT_3_9_23_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_23_prompt_v2_stability.json"
)
REPORT_3_9_23_PATH: Final[Path] = (
    _REPO_ROOT / "docs" / "evaluation"
    / "text-to-sql-prompt-v2-stability-3.9.23.md"
)


# ============================================================
# 冻结校验（§四 / §五）
# ============================================================

def check_candidate_frozen() -> list[str]:
    """Candidate v2 hash 必须与 Phase 3.9.22 记录一致。"""
    problems: list[str] = []
    live = load_candidate_fingerprint()
    if not SNAPSHOT_3_9_22_PATH.exists():
        problems.append("3.9.22 snapshot missing (cannot verify candidate)")
        return problems
    s22 = json.loads(SNAPSHOT_3_9_22_PATH.read_text(encoding="utf-8"))
    recorded = (s22.get("candidate_prompt_hashes") or {}).get(
        "combined_prompt_hash"
    )
    if recorded != live.combined_prompt_hash:
        problems.append(
            "candidate hash differs from 3.9.22 record: "
            f"{str(recorded)[:16]}… != {live.combined_prompt_hash[:16]}…"
        )
    return problems


def check_experiment_frozen() -> list[str]:
    """Baseline / Candidate / 数据全部冻结才允许运行（§四 / §五）。"""
    problems: list[str] = []
    problems.extend(check_baseline_frozen())
    problems.extend(check_candidate_frozen())

    # dataset / ground truth / fixture 必须与 3.9.22 记录一致
    if SNAPSHOT_3_9_22_PATH.exists():
        s22 = json.loads(SNAPSHOT_3_9_22_PATH.read_text(encoding="utf-8"))
        fp = read_dataset_fingerprints()
        for key in ("dataset_sha256", "ground_truth_sha256",
                    "fixture_schema_sha256", "fixture_data_sha256"):
            if s22.get(key) != fp[key]:
                problems.append(
                    f"{key}: differs from 3.9.22 record"
                )
    return problems


# ============================================================
# Per-case stability（单 variant 内 3 次运行；§十二 structural-only）
# ============================================================

@dataclass(frozen=True)
class VariantCaseStability:
    case_id: str
    question: str
    structural_results: dict[str, bool]
    stability_status: str
    sql_variant_count: int
    sql_text_same: bool
    run_sqls: dict[str, str | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "question": self.question,
            "structural_results": dict(self.structural_results),
            "stability_status": self.stability_status,
            "sql_variant_count": self.sql_variant_count,
            "sql_text_same": self.sql_text_same,
            "run_sqls": dict(self.run_sqls),
        }


def compare_variant_case(
    case_id: str,
    per_run: dict[str, StabilityCaseRecord],
    run_ids: Sequence[str],
) -> VariantCaseStability:
    records = [per_run[rid] for rid in run_ids]
    structural = {rid: per_run[rid].structural_expectation_match
                  for rid in run_ids}
    structural_same = len(set(structural.values())) == 1
    sqls = [r.generated_sql for r in records]
    generated = [s for s in sqls if s]
    variant_count = len(set(generated))
    return VariantCaseStability(
        case_id=case_id,
        question=records[0].question,
        structural_results=structural,
        stability_status=(
            STABILITY_STABLE if structural_same
            else STABILITY_NON_DETERMINISTIC
        ),
        sql_variant_count=variant_count,
        sql_text_same=(
            len(generated) == len(sqls) and variant_count <= 1
        ),
        run_sqls={rid: per_run[rid].generated_sql for rid in run_ids},
    )


@dataclass(frozen=True)
class VariantStability:
    variant: str
    run_ids: tuple[str, ...]
    case_stabilities: tuple[VariantCaseStability, ...]
    stable_cases: int
    non_deterministic_cases: int
    pairwise: dict[str, dict[str, int]]
    sql_variant_distribution: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "run_ids": list(self.run_ids),
            "stable_cases": self.stable_cases,
            "non_deterministic_cases": self.non_deterministic_cases,
            "pairwise": self.pairwise,
            "sql_variant_distribution": self.sql_variant_distribution,
            "cases": [c.to_dict() for c in self.case_stabilities],
        }


def build_variant_stability(
    variant: str,
    runs: Sequence[StabilityRun],
) -> VariantStability:
    run_ids = [r.run_id for r in runs]
    per_case: dict[str, dict[str, StabilityCaseRecord]] = {}
    for run in runs:
        for rec in run.results:
            per_case.setdefault(rec.case_id, {})[run.run_id] = rec
    comparisons = tuple(
        compare_variant_case(cid, per_run, run_ids)
        for cid, per_run in per_case.items()
    )
    stable = sum(
        1 for c in comparisons
        if c.stability_status == STABILITY_STABLE
    )
    pairwise: dict[str, dict[str, int]] = {}
    for a, b in itertools.combinations(run_ids, 2):
        key = f"{a}_vs_{b}"
        pairwise[key] = {
            "structural_agreement": sum(
                1 for c in comparisons
                if c.structural_results[a] == c.structural_results[b]
            ),
        }
    distribution: dict[str, int] = {}
    for c in comparisons:
        label = str(c.sql_variant_count)
        distribution[label] = distribution.get(label, 0) + 1
    return VariantStability(
        variant=variant,
        run_ids=tuple(run_ids),
        case_stabilities=comparisons,
        stable_cases=stable,
        non_deterministic_cases=len(comparisons) - stable,
        pairwise=pairwise,
        sql_variant_distribution=distribution,
    )


# ============================================================
# 跨 variant 比较（regression / improvement；§十 / §十四）
# ============================================================

@dataclass(frozen=True)
class CrossVariantCaseComparison:
    case_id: str
    question: str
    baseline_structural: dict[str, bool]
    candidate_structural: dict[str, bool]
    baseline_all_pass: bool
    baseline_all_fail: bool
    candidate_all_pass: bool
    candidate_all_fail: bool
    regression_runs: tuple[str, ...]
    improvement_runs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "question": self.question,
            "baseline_structural": dict(self.baseline_structural),
            "candidate_structural": dict(self.candidate_structural),
            "baseline_all_pass": self.baseline_all_pass,
            "baseline_all_fail": self.baseline_all_fail,
            "candidate_all_pass": self.candidate_all_pass,
            "candidate_all_fail": self.candidate_all_fail,
            "regression_runs": list(self.regression_runs),
            "improvement_runs": list(self.improvement_runs),
        }


def classify_cross_variant_changes(
    baseline_stab: VariantStability,
    candidate_stab: VariantStability,
) -> tuple[CrossVariantCaseComparison, ...]:
    """regression：baseline 全 PASS 的 case 在某 candidate run FAIL。

    improvement：baseline 全 FAIL 的 case 在某 candidate run PASS。
    （以 3 次基线运行的整体一致行为为准，避免单次波动误判。）
    """
    by_id_b = {c.case_id: c for c in baseline_stab.case_stabilities}
    by_id_c = {c.case_id: c for c in candidate_stab.case_stabilities}
    out: list[CrossVariantCaseComparison] = []
    for case_id, cb in by_id_b.items():
        cc = by_id_c.get(case_id)
        if cc is None:
            continue
        b_vals = list(cb.structural_results.values())
        c_vals = list(cc.structural_results.values())
        b_all_pass = all(b_vals)
        b_all_fail = not any(b_vals)
        regression_runs = tuple(
            rid for rid, passed in cc.structural_results.items()
            if b_all_pass and not passed
        )
        improvement_runs = tuple(
            rid for rid, passed in cc.structural_results.items()
            if b_all_fail and passed
        )
        out.append(
            CrossVariantCaseComparison(
                case_id=case_id,
                question=cb.question,
                baseline_structural=dict(cb.structural_results),
                candidate_structural=dict(cc.structural_results),
                baseline_all_pass=b_all_pass,
                baseline_all_fail=b_all_fail,
                candidate_all_pass=all(c_vals),
                candidate_all_fail=not any(c_vals),
                regression_runs=regression_runs,
                improvement_runs=improvement_runs,
            )
        )
    order = {c.case_id: i for i, c in enumerate(by_id_b.values())}
    out.sort(key=lambda x: order.get(x.case_id, 10 ** 9))
    return tuple(out)


# ============================================================
# Safety case analysis（§九 / §十三）
# ============================================================

def build_safety_case_analysis(
    runs: Sequence[StabilityRun],
) -> dict[str, Any]:
    per_run: dict[str, Any] = {}
    for run in runs:
        rec = next(
            (r for r in run.results if r.case_id == SAFETY_CASE_ID), None
        )
        if rec is None:
            per_run[run.run_id] = {"missing": True}
            continue
        per_run[run.run_id] = {
            "generated_sql": rec.generated_sql,
            "generation_success": rec.generation_success,
            "validation_success": rec.validation_success,
            "structural_expectation_match":
                rec.structural_expectation_match,
            "security_category": rec.security_category,
            "failure_categories": list(rec.failure_categories),
            "error_code": rec.error_code,
        }

    def _variant_stats(run_ids: Sequence[str]) -> dict[str, Any]:
        refusal = sum(
            1 for rid in run_ids
            if not per_run[rid].get("generated_sql")
        )
        success = sum(
            1 for rid in run_ids
            if per_run[rid].get("structural_expectation_match")
        )
        return {
            "refusal_count": refusal,
            "executable_sql_count": len(run_ids) - refusal,
            "safety_case_success_count": success,
            "safety_case_success_rate": _rate(success, len(run_ids)),
        }

    return {
        "case_id": SAFETY_CASE_ID,
        "runs": per_run,
        "baseline": _variant_stats(BASELINE_RUN_IDS),
        "candidate": _variant_stats(CANDIDATE_RUN_IDS),
        "note": (
            "Candidate refusal (generation_success=False) is the EXPECTED "
            "safe behavior for destructive requests — it must not be read "
            "as a model quality drop. Judge safety behavior by "
            "refusal/executable counts and structural expectation, never "
            "by generation rate alone."
        ),
    }


def _rate(count: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(count / total, RATE_PRECISION)


# ============================================================
# Summary（§十八 snapshot 结构）
# ============================================================

@dataclass(frozen=True)
class V2StabilitySummary:
    phase: str
    experiment_type: str
    baseline_prompt_hashes: dict[str, Any]
    candidate_prompt_hashes: dict[str, Any]
    baseline_prompt_hash: str
    candidate_prompt_hash: str
    dataset_sha256: str
    ground_truth_sha256: str
    fixture_schema_sha256: str
    fixture_data_sha256: str
    case_order_hash: str
    runs: dict[str, dict[str, Any]]
    per_case_comparison: tuple[CrossVariantCaseComparison, ...]
    baseline_stability: VariantStability
    candidate_stability: VariantStability
    regression_cases: tuple[dict[str, str], ...]
    improvement_cases: tuple[dict[str, str], ...]
    safety_case_analysis: dict[str, Any]
    summary: dict[str, Any]
    environment: dict[str, Any]
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        compare=False,
    )

    def to_snapshot_dict(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "phase": self.phase,
            "experiment_type": self.experiment_type,
            "baseline_prompt_hashes": self.baseline_prompt_hashes,
            "candidate_prompt_hashes": self.candidate_prompt_hashes,
            "baseline_prompt_hash": self.baseline_prompt_hash,
            "candidate_prompt_hash": self.candidate_prompt_hash,
            "prompt_hash_different": (
                self.baseline_prompt_hash != self.candidate_prompt_hash
            ),
            "dataset_sha256": self.dataset_sha256,
            "ground_truth_sha256": self.ground_truth_sha256,
            "fixture_schema_sha256": self.fixture_schema_sha256,
            "fixture_data_sha256": self.fixture_data_sha256,
            "case_order_hash": self.case_order_hash,
            "runs": self.runs,
            "per_case_comparison": [
                c.to_dict() for c in self.per_case_comparison
            ],
            "baseline_stability": self.baseline_stability.to_dict(),
            "candidate_stability": self.candidate_stability.to_dict(),
            "regression_cases": list(self.regression_cases),
            "improvement_cases": list(self.improvement_cases),
            "safety_case_analysis": self.safety_case_analysis,
            "summary": self.summary,
            "environment": self.environment,
            "generated_at": self.generated_at,
        }
        _assert_no_secrets(snapshot)
        return snapshot

    def render_summary(self) -> str:
        s = self.summary
        lines = [
            f"Prompt v2 Repeated Stability — Phase {self.phase}",
            "",
            f"Runs: {len(self.runs)} x 14 cases "
            "(baseline x3, candidate x3)",
            f"baseline structural_pass: "
            f"{s['baseline']['structural_pass_count']}/42",
            f"candidate structural_pass: "
            f"{s['candidate']['structural_pass_count']}/42",
            f"baseline stable/non-det: "
            f"{self.baseline_stability.stable_cases}/"
            f"{self.baseline_stability.non_deterministic_cases}",
            f"candidate stable/non-det: "
            f"{self.candidate_stability.stable_cases}/"
            f"{self.candidate_stability.non_deterministic_cases}",
            f"safety success: baseline "
            f"{self.safety_case_analysis['baseline']['safety_case_success_count']}/3"
            f", candidate "
            f"{self.safety_case_analysis['candidate']['safety_case_success_count']}/3",
            f"regression_cases: {len(self.regression_cases)}"
            f" | improvement_cases: {len(self.improvement_cases)}",
            f"calls: deepseek={s['calls']['deepseek_calls']} "
            f"db={s['calls']['db_calls']} "
            f"network={s['calls']['network_calls']}",
        ]
        return "\n".join(lines)

    def render_report(self) -> str:
        out: list[str] = []
        add = out.append
        add(f"# Prompt v2 Repeated Stability Validation — Phase {self.phase}")
        add("")
        add("> **Validation only**：验证 3.9.22 Candidate v2 的改善是否稳定")
        add("> 复现，并与 Baseline 自然波动比较。不优化 Prompt，不下"
            "“模型更强/Prompt 更好”的结论。")
        add("")
        add("## 1. 实验设计")
        add("")
        add("- Baseline v1 × 3 runs + Candidate v2 × 3 runs，每 run 14 cases，")
        add("  顺序固定 case 1 → 14；运行顺序 Baseline A/B/C → Candidate A/B/C。")
        add("- temperature / max_tokens：client default（未修改）。")
        add("")
        add("## 2. Prompt hashes")
        add("")
        add("| Variant | Version | combined |")
        add("|---|---|---|")
        add(f"| Baseline | v1 | {self.baseline_prompt_hash[:16]}… |")
        add(f"| Candidate | v2 | {self.candidate_prompt_hash[:16]}… |")
        add("")
        add(f"- Baseline A=B=C: `{len({r['prompt_hash'] for k, r in self.runs.items() if k.startswith('baseline')}) == 1}`")
        add(f"- Candidate A=B=C: `{len({r['prompt_hash'] for k, r in self.runs.items() if k.startswith('candidate')}) == 1}`")
        add(f"- Baseline != Candidate: `{self.baseline_prompt_hash != self.candidate_prompt_hash}`")
        add("")
        add("## 3. Hashes")
        add("")
        for key in ("dataset_sha256", "ground_truth_sha256",
                    "fixture_schema_sha256", "fixture_data_sha256",
                    "case_order_hash"):
            add(f"- {key}: `{str(getattr(self, key))[:16]}…`")
        add("")
        add("## 4. Per-run metrics")
        add("")
        add("| Run | generation | validation | structural | security |")
        add("|---|---:|---:|---:|---:|")
        for run_id in RUN_IDS:
            m = self.runs[run_id]["metrics"]
            add(
                f"| {run_id} "
                f"| {_fmt(m.get('llm_generation_success_rate'))} "
                f"| {_fmt(m.get('validator_acceptance_rate'))} "
                f"| {_fmt(m.get('expectation_pass_rate'))} "
                f"| {_fmt(m.get('security_expectation_pass_rate'))} |"
            )
        add("")
        add("## 5. Stability（§十二，structural-only）")
        add("")
        add(f"- Baseline: stable={self.baseline_stability.stable_cases}, "
            f"non_deterministic={self.baseline_stability.non_deterministic_cases}")
        add(f"- Candidate: stable={self.candidate_stability.stable_cases}, "
            f"non_deterministic={self.candidate_stability.non_deterministic_cases}")
        add("")
        add("## 6. Per-case structural matrix")
        add("")
        add("| case_id | " + " | ".join(RUN_IDS) + " |")
        add("|---|" + "---|" * len(RUN_IDS))
        cand_by = {c.case_id: c for c in self.candidate_stability.case_stabilities}
        for cb in self.baseline_stability.case_stabilities:
            cc = cand_by.get(cb.case_id)
            cells = []
            for rid in RUN_IDS:
                src = cb if rid.startswith("baseline") else cc
                val = (src.structural_results[rid]
                       if src is not None and rid in src.structural_results
                       else None)
                cells.append("PASS" if val else "FAIL")
            add(f"| `{cb.case_id}` | " + " | ".join(cells) + " |")
        add("")
        add("## 7. safety_delete_all_documents（6 次独立结果）")
        add("")
        sca = self.safety_case_analysis
        add("| Run | generated_sql | gen_ok | validation | structural |")
        add("|---|---|---|---|---|")
        for rid in RUN_IDS:
            e = sca["runs"][rid]
            sql = (e.get("generated_sql") or "(refused / no SQL)")
            add(
                f"| {rid} | `{str(sql)[:80]}…` "
                f"| {e.get('generation_success')} "
                f"| {e.get('validation_success')} "
                f"| {e.get('structural_expectation_match')} |"
            )
        add("")
        add(f"- Baseline: refusal {sca['baseline']['refusal_count']}/3, "
            f"executable {sca['baseline']['executable_sql_count']}/3, "
            f"safety success {sca['baseline']['safety_case_success_count']}/3")
        add(f"- Candidate: refusal {sca['candidate']['refusal_count']}/3, "
            f"executable {sca['candidate']['executable_sql_count']}/3, "
            f"safety success {sca['candidate']['safety_case_success_count']}/3")
        add("")
        add(f"> {sca['note']}")
        add("")
        add("## 8. Regression / Improvement")
        add("")
        reg_labels = [
            f"{r['case_id']}@{r['candidate_run']}"
            for r in self.regression_cases
        ]
        imp_labels = [
            f"{r['case_id']}@{r['candidate_run']}"
            for r in self.improvement_cases
        ]
        add(f"- regression_cases: {reg_labels or 'none'}")
        add(f"- improvement_cases: {imp_labels or 'none'}")
        add("")
        add("## 9. semantic_dependent_document_and_chunk 稳定性矩阵")
        add("")
        target_b = next(
            (c for c in self.baseline_stability.case_stabilities
             if c.case_id == "semantic_dependent_document_and_chunk"),
            None,
        )
        target_c = next(
            (c for c in self.candidate_stability.case_stabilities
             if c.case_id == "semantic_dependent_document_and_chunk"),
            None,
        )
        if target_b is not None:
            add("| Run | Structural | SQL |")
            add("|---|---|---|")
            for rid in RUN_IDS:
                src = target_b if rid.startswith("baseline") else target_c
                val = src.structural_results.get(rid)
                sql = (src.run_sqls.get(rid) or "(none)").replace("|", "\\|")
                add(f"| {rid} | {'PASS' if val else 'FAIL'} | `{str(sql)[:90]}` |")
            add("")
            add("> 只按现有 evaluation pipeline 判定 PASS/FAIL，"
                "不因 SQL 写法更合理而改判。")
            add("")
        add("## 10. SQL variant distribution（diagnostic，非质量分）")
        add("")
        add(f"- Baseline: {self.baseline_stability.sql_variant_distribution}")
        add(f"- Candidate: {self.candidate_stability.sql_variant_distribution}")
        add("")
        add("## 11. Calls")
        add("")
        s = self.summary
        add(f"- DeepSeek generate() calls: {s['calls']['deepseek_calls']}")
        add(f"- DB calls: {s['calls']['db_calls']}")
        add(f"- Network calls: {s['calls']['network_calls']}")
        add("")
        return "\n".join(out)


def _fmt(rate: float | None) -> str:
    return "N/A" if rate is None else f"{rate * 100:.2f}%"


# ============================================================
# Runner（§二 / §六：6 runs，固定顺序，不补跑）
# ============================================================

class V2StabilityRunner:
    """Baseline×3 + Candidate×3（复用 3.9.20/3.9.22 框架）。"""

    def __init__(
        self,
        *,
        runner: PromptExperimentRunner,
        runs_per_variant: int = RUNS_PER_VARIANT,
        cases: Sequence[TextToSQLEvaluationCase] | None = None,
    ) -> None:
        self._runner = runner
        self._runs_per_variant = runs_per_variant
        self._cases = tuple(
            cases
            if cases is not None
            else load_text_to_sql_regression_dataset()
        )
        self._baseline_fingerprint = load_prompt_fingerprint()
        self._candidate_fingerprint = load_candidate_fingerprint()

    async def run(self) -> V2StabilitySummary:
        runs: dict[str, StabilityRun] = {}

        async def _do(run_id: str, *, candidate: bool) -> None:
            variant = (
                await self._runner.run_candidate() if candidate
                else await self._runner.run_baseline()
            )
            fp = (
                self._candidate_fingerprint if candidate
                else self._baseline_fingerprint
            )
            case_by_id = {c.case_id: c for c in self._cases}
            records = tuple(
                _stability_case_record(r, case_by_id[r.case_id])
                for r in variant.results
            )
            runs[run_id] = StabilityRun(
                run_id=run_id,
                prompt_version=fp.prompt_version,
                prompt_hash=fp.combined_prompt_hash,
                results=records,
                metrics=variant.metrics.as_dict(),
                headline_metrics=variant.headline().to_dict(),
                total_cases=variant.total_cases,
                deepseek_calls=variant.deepseek_calls,
                db_calls=variant.db_calls,
                network_calls=variant.network_calls,
            )

        for rid in BASELINE_RUN_IDS:
            await _do(rid, candidate=False)
        for rid in CANDIDATE_RUN_IDS:
            await _do(rid, candidate=True)

        baseline_runs = [runs[rid] for rid in BASELINE_RUN_IDS]
        candidate_runs = [runs[rid] for rid in CANDIDATE_RUN_IDS]
        baseline_stab = build_variant_stability("baseline", baseline_runs)
        candidate_stab = build_variant_stability("candidate", candidate_runs)
        comparisons = classify_cross_variant_changes(
            baseline_stab, candidate_stab
        )
        regression_cases = tuple(
            {"case_id": c.case_id, "candidate_run": rid}
            for c in comparisons for rid in c.regression_runs
        )
        improvement_cases = tuple(
            {"case_id": c.case_id, "candidate_run": rid}
            for c in comparisons for rid in c.improvement_runs
        )
        all_runs = [runs[rid] for rid in RUN_IDS]
        safety = build_safety_case_analysis(all_runs)

        def _variant_totals(run_ids: Sequence[str]) -> dict[str, Any]:
            pass_count = sum(
                1 for rid in run_ids
                for r in runs[rid].results
                if r.structural_expectation_match
            )
            variant = (
                "baseline" if run_ids[0].startswith("baseline")
                else "candidate"
            )
            return {
                "structural_pass_count": pass_count,
                "structural_pass_rate": _rate(pass_count, 14 * len(run_ids)),
                "safety_case_success_count": (
                    safety[variant]["safety_case_success_count"]
                ),
                "safety_case_success_rate": (
                    safety[variant]["safety_case_success_rate"]
                ),
            }

        fp = read_dataset_fingerprints()
        summary = {
            "baseline": _variant_totals(BASELINE_RUN_IDS),
            "candidate": _variant_totals(CANDIDATE_RUN_IDS),
            "per_run_metrics": {
                rid: {
                    "generation_success": runs[rid].metrics.get(
                        "llm_generated_cases"
                    ),
                    "validator_accepted": runs[rid].metrics.get(
                        "validator_accepted_cases"
                    ),
                    "structural_match": runs[rid].metrics.get(
                        "passed_cases"
                    ),
                    "security_passed": runs[rid].metrics.get(
                        "security_passed_cases"
                    ),
                }
                for rid in RUN_IDS
            },
            "calls": {
                "deepseek_calls": sum(
                    r.deepseek_calls for r in all_runs
                ),
                "db_calls": sum(r.db_calls for r in all_runs),
                "network_calls": sum(r.network_calls for r in all_runs),
            },
        }
        return V2StabilitySummary(
            phase=PHASE_3_9_23,
            experiment_type=EXPERIMENT_TYPE_3_9_23,
            baseline_prompt_hashes=(
                self._baseline_fingerprint.to_dict()
            ),
            candidate_prompt_hashes=(
                self._candidate_fingerprint.to_dict()
            ),
            baseline_prompt_hash=self._baseline_fingerprint.combined_prompt_hash,
            candidate_prompt_hash=(
                self._candidate_fingerprint.combined_prompt_hash
            ),
            dataset_sha256=fp["dataset_sha256"],
            ground_truth_sha256=fp["ground_truth_sha256"],
            fixture_schema_sha256=fp["fixture_schema_sha256"],
            fixture_data_sha256=fp["fixture_data_sha256"],
            case_order_hash=compute_case_order_hash(self._cases),
            runs={rid: runs[rid].to_dict() for rid in RUN_IDS},
            per_case_comparison=comparisons,
            baseline_stability=baseline_stab,
            candidate_stability=candidate_stab,
            regression_cases=regression_cases,
            improvement_cases=improvement_cases,
            safety_case_analysis=safety,
            summary=summary,
            environment=collect_stability_environment(),
        )


# ============================================================
# Offline validation（§二十：0 LLM / 0 DB / 0 network）
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


def _rebuild_run(run_dict: dict[str, Any]) -> StabilityRun:
    records = tuple(
        StabilityCaseRecord(
            case_id=str(e.get("case_id", "")),
            project_id=e.get("project_id"),
            question=str(e.get("question", "")),
            generated_sql=e.get("generated_sql"),
            generation_success=bool(e.get("generation_success", False)),
            validation_success=bool(e.get("validation_success", False)),
            structural_expectation_match=bool(
                e.get("structural_expectation_match", False)
            ),
            security_category=e.get("security_category"),
            execution_success=e.get("execution_success"),
            semantic_result_correct=e.get("semantic_result_correct"),
            failure_categories=tuple(e.get("failure_categories", ())),
            matched_expectations=tuple(e.get("matched_expectations", ())),
            error_code=e.get("error_code"),
        )
        for e in run_dict.get("results", [])
        if isinstance(e, dict)
    )
    return StabilityRun(
        run_id=str(run_dict.get("run_id", "")),
        prompt_version=str(run_dict.get("prompt_version", "")),
        prompt_hash=str(run_dict.get("prompt_hash", "")),
        results=records,
        metrics=run_dict.get("metrics") or {},
        headline_metrics=run_dict.get("headline_metrics") or {},
        total_cases=len(records),
        deepseek_calls=int(run_dict.get("deepseek_calls", 0)),
        db_calls=int(run_dict.get("db_calls", 0)),
        network_calls=int(run_dict.get("network_calls", 0)),
    )


def validate_v2_stability_snapshot(
    snapshot: dict[str, Any],
    cases: Sequence[TextToSQLEvaluationCase] | None = None,
) -> list[str]:
    """纯 offline 校验（§二十）。"""
    problems: list[str] = []
    if cases is None:
        cases = load_text_to_sql_regression_dataset()
    dataset_ids = [c.case_id for c in cases]

    # ---- phase / type ----
    if snapshot.get("phase") != PHASE_3_9_23:
        problems.append(f"phase: expected {PHASE_3_9_23!r}")
    if snapshot.get("experiment_type") != EXPERIMENT_TYPE_3_9_23:
        problems.append(
            f"experiment_type: expected {EXPERIMENT_TYPE_3_9_23!r}"
        )

    # ---- 6 runs ----
    runs = snapshot.get("runs") or {}
    if list(runs.keys()) != list(RUN_IDS):
        problems.append(
            f"runs: expected {list(RUN_IDS)}, got {list(runs.keys())}"
        )
    for rid, run in runs.items():
        results = [e for e in run.get("results", []) if isinstance(e, dict)]
        ids = [e.get("case_id") for e in results]
        if len(results) != 14:
            problems.append(f"run {rid}: expected 14 cases, got {len(results)}")
        if ids != dataset_ids:
            problems.append(f"run {rid}: case order mismatch")

    # ---- prompt hashes（§四） ----
    live_base = load_prompt_fingerprint()
    live_cand = load_candidate_fingerprint()
    base_hashes = {
        runs[rid].get("prompt_hash") for rid in BASELINE_RUN_IDS
        if rid in runs
    }
    cand_hashes = {
        runs[rid].get("prompt_hash") for rid in CANDIDATE_RUN_IDS
        if rid in runs
    }
    if base_hashes != {live_base.combined_prompt_hash}:
        problems.append("baseline prompt hashes not identical/live")
    if cand_hashes != {live_cand.combined_prompt_hash}:
        problems.append("candidate prompt hashes not identical/live")
    if snapshot.get("baseline_prompt_hash") != live_base.combined_prompt_hash:
        problems.append("baseline_prompt_hash != live v1")
    if snapshot.get("candidate_prompt_hash") != live_cand.combined_prompt_hash:
        problems.append("candidate_prompt_hash != live v2")
    if snapshot.get("baseline_prompt_hash") == \
            snapshot.get("candidate_prompt_hash"):
        problems.append("baseline and candidate hashes identical")
    problems.extend(
        f"freeze: {p}" for p in check_baseline_frozen()
    )
    problems.extend(
        f"freeze: {p}" for p in check_candidate_frozen()
    )

    # ---- dataset / fixture / order ----
    fp = read_dataset_fingerprints()
    for key in ("dataset_sha256", "ground_truth_sha256",
                "fixture_schema_sha256", "fixture_data_sha256"):
        if snapshot.get(key) != fp[key]:
            problems.append(f"{key}: mismatch vs current files")
    if snapshot.get("case_order_hash") != compute_case_order_hash(cases):
        problems.append("case_order_hash mismatch")

    # ---- metrics 重算（每 run） ----
    comparable = (
        "total_cases", "passed_cases", "failed_cases",
        "validation_passed_cases", "validation_expectation_cases",
        "validation_expectation_passed_cases", "execution_cases",
        "execution_passed_cases", "security_cases", "security_passed_cases",
        "validator_accepted_cases",
    )
    for rid, run in runs.items():
        stored = run.get("metrics") or {}
        results = tuple(
            _record_from_snapshot(e)
            for e in run.get("results", []) if isinstance(e, dict)
        )
        recalculated = _recompute_variant_metrics(run, cases)
        for key in comparable:
            if stored.get(key) != recalculated.get(key):
                problems.append(
                    f"run {rid}.metrics.{key}: snapshot={stored.get(key)!r} "
                    f"recalculated={recalculated.get(key)!r}"
                )
        entries = [e for e in run.get("results", []) if isinstance(e, dict)]
        generated = sum(1 for e in entries if e.get("generated_sql"))
        if stored.get("llm_generated_cases") != generated:
            problems.append(
                f"run {rid}.metrics.llm_generated_cases: "
                f"snapshot={stored.get('llm_generated_cases')} "
                f"recalculated={generated}"
            )

    # ---- stability 重算（§十二 structural-only） ----
    rebuilt_runs = {rid: _rebuild_run(run) for rid, run in runs.items()}
    baseline_stab = build_variant_stability(
        "baseline", [rebuilt_runs[rid] for rid in BASELINE_RUN_IDS
                     if rid in rebuilt_runs]
    )
    candidate_stab = build_variant_stability(
        "candidate", [rebuilt_runs[rid] for rid in CANDIDATE_RUN_IDS
                      if rid in rebuilt_runs]
    )
    for label, stored_block, rebuilt in (
        ("baseline_stability", snapshot.get("baseline_stability") or {},
         baseline_stab),
        ("candidate_stability", snapshot.get("candidate_stability") or {},
         candidate_stab),
    ):
        if stored_block.get("stable_cases") != rebuilt.stable_cases:
            problems.append(
                f"{label}.stable_cases: snapshot="
                f"{stored_block.get('stable_cases')} "
                f"recalculated={rebuilt.stable_cases}"
            )
        if stored_block.get("non_deterministic_cases") != \
                rebuilt.non_deterministic_cases:
            problems.append(
                f"{label}.non_deterministic_cases: snapshot="
                f"{stored_block.get('non_deterministic_cases')} "
                f"recalculated={rebuilt.non_deterministic_cases}"
            )
        if stored_block.get("sql_variant_distribution") != \
                rebuilt.sql_variant_distribution:
            problems.append(
                f"{label}.sql_variant_distribution: snapshot="
                f"{stored_block.get('sql_variant_distribution')} "
                f"recalculated={rebuilt.sql_variant_distribution}"
            )

    # ---- regression / improvement 重算 ----
    comparisons = classify_cross_variant_changes(
        baseline_stab, candidate_stab
    )
    rebuilt_reg = [
        {"case_id": c.case_id, "candidate_run": rid}
        for c in comparisons for rid in c.regression_runs
    ]
    rebuilt_imp = [
        {"case_id": c.case_id, "candidate_run": rid}
        for c in comparisons for rid in c.improvement_runs
    ]
    if snapshot.get("regression_cases") != rebuilt_reg:
        problems.append(
            f"regression_cases: snapshot={snapshot.get('regression_cases')} "
            f"recalculated={rebuilt_reg}"
        )
    if snapshot.get("improvement_cases") != rebuilt_imp:
        problems.append(
            f"improvement_cases: snapshot={snapshot.get('improvement_cases')} "
            f"recalculated={rebuilt_imp}"
        )

    # ---- safety case 重算 ----
    sca = snapshot.get("safety_case_analysis") or {}
    rebuilt_sca = build_safety_case_analysis(
        [rebuilt_runs[rid] for rid in RUN_IDS if rid in rebuilt_runs]
    )
    for variant in ("baseline", "candidate"):
        stored_v = sca.get(variant) or {}
        rebuilt_v = rebuilt_sca[variant]
        for key in ("refusal_count", "executable_sql_count",
                    "safety_case_success_count"):
            if stored_v.get(key) != rebuilt_v.get(key):
                problems.append(
                    f"safety_case_analysis.{variant}.{key}: "
                    f"snapshot={stored_v.get(key)} "
                    f"recalculated={rebuilt_v.get(key)}"
                )

    # ---- calls ----
    total_ds = sum(int(r.get("deepseek_calls", 0)) for r in runs.values())
    total_db = sum(int(r.get("db_calls", 0)) for r in runs.values())
    stored_calls = (snapshot.get("summary") or {}).get("calls") or {}
    if stored_calls.get("deepseek_calls") not in (None, total_ds):
        problems.append("summary.calls.deepseek_calls inconsistent")
    if stored_calls.get("network_calls") not in (None, total_ds + total_db):
        problems.append("summary.calls.network_calls inconsistent")

    # ---- secrets ----
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
    for fragment in ("postgresql://", "postgres://", "sk-", "bearer "):
        if fragment in blob:
            problems.append(f"forbidden fragment: {fragment!r}")

    return problems
