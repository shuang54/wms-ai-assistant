"""Phase 3.9.20 — Text-to-SQL Prompt A/B Experiment Framework.

Experiment Infrastructure Validation (NOT prompt optimization).

```text
Baseline Prompt (current real prompts, read-only, versioned)
      ↓  same 14-case dataset / same DeepSeek / same Schema+Semantic
      ↓  same Validator / Executor / Result+Semantic Evaluator
Candidate Prompt A (identical content to Baseline this phase)
      ↓  identical experiment conditions
      ↓  identical evaluation
A/B comparison (per-case diff, no SQL-string equality)
```

This module is a thin **experiment layer** on top of the existing
production pipeline. It does NOT re-implement SQL generation / validation /
execution / evaluation — it composes:

    TextToSQLService (generator, unchanged)
        → TextToSQLEvaluationRunner (3.9.3, unchanged)
        → SQLValidator / SQLExecutor (unchanged)
        → calculate_real_llm_baseline_metrics (3.9.5, unchanged)

Discipline (consistent with §三 / §二十三 of the task):
- Baseline = the project's **actual** prompts (system / user / retry),
  loaded read-only and versioned as ``v1``. Nothing is copied-and-edited.
- Candidate A is **byte-identical** to Baseline this phase, so
  ``baseline_prompt_hash == candidate_prompt_hash``. The experiment proves
  the framework does not introduce run-order / infra differences.
- No production service is modified; run/compare are pure orchestration.
- ``--check`` performs ZERO DeepSeek / DB / network calls.
- Snapshot never contains API keys / DATABASE_URL / tokens.
- SQL text is kept only as a **diagnostic**; correctness is judged by
  Validator / Structural Evaluation / Execution / Semantic Result — never
  by ``baseline_sql == candidate_sql`` (§十二).
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from backend.app.services.text_to_sql_baseline_service import (
    RATE_PRECISION,
    read_dataset_version,
)
from backend.app.services.text_to_sql_evaluation_service import (
    DEFAULT_REGRESSION_DATASET_PATH,
    TextToSQLEvaluationCase,
    TextToSQLEvaluationResult,
    TextToSQLExpectations,
    TextToSQLGenerator,
    EvaluationContextResolver,
    with_execution_required,
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_real_llm_baseline_service import (
    TextToSQLBaselineEnvironment,
    TextToSQLRealLLMBaselineMetrics,
    calculate_real_llm_baseline_metrics,
    collect_real_llm_environment_info,
    run_real_llm_baseline,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PHASE_3_9_20",
    "EXPERIMENT_TYPE",
    "PROMPT_VERSION",
    "SNAPSHOT_3_9_20_PATH",
    "REPORT_3_9_20_PATH",
    "PromptFingerprint",
    "ExperimentCaseResult",
    "PromptExperimentVariantMetrics",
    "PromptExperimentVariantResult",
    "ExperimentCaseComparison",
    "PromptExperimentSummary",
    "PromptExperimentRunner",
    "load_prompt_fingerprint",
    "read_dataset_version",
    "validate_experiment_snapshot",
]


# ============================================================
# 常量 & 路径
# ============================================================

PHASE_3_9_20: Final[str] = "3.9.20"
EXPERIMENT_TYPE: Final[str] = "prompt_ab_infrastructure_validation"
PROMPT_VERSION: Final[str] = "v1"

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_PROMPTS_DIR: Final[Path] = _REPO_ROOT / "backend" / "app" / "prompts"

#: Baseline / Candidate 来源 = 项目实际使用的真实 Prompt（只读加载）。
SYSTEM_PROMPT_PATH: Final[Path] = _PROMPTS_DIR / "text_to_sql_system.txt"
USER_PROMPT_PATH: Final[Path] = _PROMPTS_DIR / "text_to_sql_user.txt"
RETRY_PROMPT_PATH: Final[Path] = _PROMPTS_DIR / "text_to_sql_retry.txt"

#: Ground truth = 结构期望（regression dataset） + 语义结果真值（3.9.17 frozen）。
RESULT_GROUND_TRUTH_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "result_ground_truth.yaml"
)
SEMANTIC_GROUND_TRUTH_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_17_semantic_result_full.json"
)
FIXTURE_SCHEMA_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "t2s_eval_schema.sql"
)
FIXTURE_DATA_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "t2s_eval_data.sql"
)

SNAPSHOT_3_9_20_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_20_prompt_experiment.json"
)
REPORT_3_9_20_PATH: Final[Path] = (
    _REPO_ROOT / "docs" / "evaluation"
    / "text-to-sql-prompt-ab-experiment-3.9.20.md"
)

#: Snapshot 自检：禁止出现的敏感键
_FORBIDDEN_SNAPSHOT_KEYS: Final[frozenset[str]] = frozenset(
    {"api_key", "llm_api_key", "password", "database_url", "dsn",
     "connection_string", "secret", "token", "authorization"}
)
_FORBIDDEN_SNAPSHOT_FRAGMENTS: Final[tuple[str, ...]] = (
    "postgresql://", "postgres://", "sk-", "bearer ",
)


# ============================================================
# Hash helpers
# ============================================================

def _sha256_file(path: Path) -> str:
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ============================================================
# Prompt fingerprint（§四：Baseline 来自真实 Prompt，只读 + 版本化）
# ============================================================

@dataclass(frozen=True)
class PromptFingerprint:
    """不可变 Prompt 指纹：证明实验使用的是哪份 Prompt。"""

    prompt_version: str
    system_prompt_hash: str
    user_prompt_hash: str
    retry_prompt_hash: str
    combined_prompt_hash: str

    def to_dict(self) -> dict[str, str]:
        return {
            "prompt_version": self.prompt_version,
            "system_prompt_hash": self.system_prompt_hash,
            "user_prompt_hash": self.user_prompt_hash,
            "retry_prompt_hash": self.retry_prompt_hash,
            "combined_prompt_hash": self.combined_prompt_hash,
        }


def load_prompt_fingerprint(
    prompt_version: str = PROMPT_VERSION,
) -> PromptFingerprint:
    """读取真实 Prompt 三元组并生成指纹（read-only，不复制）。

    ``combined_prompt_hash`` = sha256(system + user + retry)，作为
    Baseline / Candidate 统一的 ``prompt_hash``。
    """
    system = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    user = USER_PROMPT_PATH.read_text(encoding="utf-8")
    retry = RETRY_PROMPT_PATH.read_text(encoding="utf-8")
    combined = system + "\n" + user + "\n" + retry
    return PromptFingerprint(
        prompt_version=prompt_version,
        system_prompt_hash=_sha256_text(system),
        user_prompt_hash=_sha256_text(user),
        retry_prompt_hash=_sha256_text(retry),
        combined_prompt_hash=_sha256_text(combined),
    )


def read_dataset_fingerprints() -> dict[str, str]:
    """计算 dataset / ground-truth / fixture 的 sha256（§六 / §七）。"""
    ground_truth = ""
    if RESULT_GROUND_TRUTH_PATH.exists():
        ground_truth += RESULT_GROUND_TRUTH_PATH.read_text(encoding="utf-8")
    if SEMANTIC_GROUND_TRUTH_PATH.exists():
        ground_truth += SEMANTIC_GROUND_TRUTH_PATH.read_text(encoding="utf-8")
    return {
        "dataset_sha256": _sha256_file(DEFAULT_REGRESSION_DATASET_PATH),
        "ground_truth_sha256": _sha256_text(ground_truth),
        "fixture_schema_sha256": _sha256_file(FIXTURE_SCHEMA_PATH),
        "fixture_data_sha256": _sha256_file(FIXTURE_DATA_PATH),
    }


# ============================================================
# Per-case result（§九：复用现有 DTO 字段，不重复定义）
# ============================================================

@dataclass(frozen=True)
class ExperimentCaseResult:
    """单 case 在某一 Prompt variant 下的实验结果。

    字段直接由 ``TextToSQLEvaluationResult`` 派生：
    ``generated_sql`` / ``validation_passed`` / ``execution_passed`` /
    ``failed_expectations`` / ``passed`` 等。

    ``semantic_result_correct`` 本阶段不计算（结构实验），恒为 ``None``；
    ``security_category`` 由 case 的 ``must_pass_validation`` 期望推导。
    """

    case_id: str
    prompt_variant: str
    project_id: str | None
    generated_sql: str | None
    generation_success: bool
    validation_success: bool
    structural_expectation_match: bool
    execution_success: bool | None
    semantic_result_correct: bool | None
    security_category: str | None
    failure_categories: tuple[str, ...]
    matched_expectations: tuple[str, ...]
    error_code: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "prompt_variant": self.prompt_variant,
            "project_id": self.project_id,
            "generated_sql": self.generated_sql,
            "generation_success": self.generation_success,
            "validation_success": self.validation_success,
            "structural_expectation_match": self.structural_expectation_match,
            "execution_success": self.execution_success,
            "semantic_result_correct": self.semantic_result_correct,
            "security_category": self.security_category,
            "failure_categories": list(self.failure_categories),
            "matched_expectations": list(self.matched_expectations),
            "error_code": self.error_code,
        }


# ============================================================
# Variant metrics（§十：客观指标，无 overall_score）
# ============================================================

@dataclass(frozen=True)
class PromptExperimentVariantMetrics:
    """单 variant 的客观指标（全部复用 3.9.5 metrics，单一真值）。"""

    generation_success: float | None
    validation_acceptance: float | None
    structural_expectation_accuracy: float | None
    execution_success: float | None
    semantic_result_accuracy: float | None
    security_pass: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation_success": self.generation_success,
            "validation_acceptance": self.validation_acceptance,
            "structural_expectation_accuracy": self.structural_expectation_accuracy,
            "execution_success": self.execution_success,
            "semantic_result_accuracy": self.semantic_result_accuracy,
            "security_pass": self.security_pass,
        }


def _variant_metrics(
    metrics: TextToSQLRealLLMBaselineMetrics,
) -> PromptExperimentVariantMetrics:
    """把 3.9.5 metrics 映射到 3.9.20 指标名（§十）。

    ``semantic_result_accuracy`` 本阶段为 ``None``：本实验是**结构实验**，
    semantic result 评价由 Phase 3.9.16 / 3.9.18 单独覆盖，且本阶段禁止
    修改 Semantic Evaluator / pipeline（§三）。
    """
    return PromptExperimentVariantMetrics(
        generation_success=metrics.llm_generation_success_rate,
        validation_acceptance=metrics.validator_acceptance_rate,
        structural_expectation_accuracy=metrics.expectation_pass_rate,
        execution_success=metrics.execution_pass_rate,
        semantic_result_accuracy=None,
        security_pass=metrics.security_expectation_pass_rate,
    )


# ============================================================
# Variant result + comparison + summary
# ============================================================

@dataclass(frozen=True)
class PromptExperimentVariantResult:
    """一次 variant 运行（Baseline 或 Candidate A）的聚合。"""

    variant: str
    prompt_version: str
    prompt_hash: str
    prompt_fingerprint: PromptFingerprint
    results: tuple[ExperimentCaseResult, ...]
    metrics: TextToSQLRealLLMBaselineMetrics
    total_cases: int
    deepseek_calls: int = 0
    db_calls: int = 0
    network_calls: int = 0

    def headline(self) -> PromptExperimentVariantMetrics:
        """6 个对外指标（§十），由完整 3.9.5 metrics 映射。"""
        return _variant_metrics(self.metrics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "prompt_fingerprint": self.prompt_fingerprint.to_dict(),
            # 完整 3.9.5 metrics（供 --check 重算校验）
            "metrics": self.metrics.as_dict(),
            # 6 个对外 headline 指标（供报告）
            "headline_metrics": self.headline().to_dict(),
            "total_cases": self.total_cases,
            "deepseek_calls": self.deepseek_calls,
            "db_calls": self.db_calls,
            "network_calls": self.network_calls,
            "results": [r.to_dict() for r in self.results],
        }


@dataclass(frozen=True)
class ExperimentCaseComparison:
    """逐 case 的 Baseline vs Candidate 差异（§十一：必须可见）。"""

    case_id: str
    changed: bool
    diff_fields: tuple[str, ...]
    baseline: ExperimentCaseResult
    candidate: ExperimentCaseResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "changed": self.changed,
            "diff_fields": list(self.diff_fields),
            "baseline": self.baseline.to_dict(),
            "candidate": self.candidate.to_dict(),
        }


@dataclass(frozen=True)
class PromptExperimentSummary:
    """一次完整 A/B 实验的聚合（Baseline v1 vs Candidate A v1）。"""

    phase: str
    experiment_type: str
    baseline: PromptExperimentVariantResult
    candidate: PromptExperimentVariantResult
    prompt_hash_equal: bool
    case_alignment_ok: bool
    infrastructure_validation_pass: bool
    changed_case_ids: tuple[str, ...]
    comparisons: tuple[ExperimentCaseComparison, ...]
    dataset_sha256: str
    ground_truth_sha256: str
    fixture_schema_sha256: str
    fixture_data_sha256: str
    environment: dict[str, Any]
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        compare=False,
    )

    # ---------- 工具 ----------

    def to_snapshot_dict(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "phase": self.phase,
            "experiment_type": self.experiment_type,
            "baseline_prompt_version": self.baseline.prompt_version,
            "candidate_prompt_version": self.candidate.prompt_version,
            "baseline_prompt_hash": self.baseline.prompt_hash,
            "candidate_prompt_hash": self.candidate.prompt_hash,
            "prompt_hash_equal": self.prompt_hash_equal,
            "baseline_prompt_fingerprint": (
                self.baseline.prompt_fingerprint.to_dict()
            ),
            "candidate_prompt_fingerprint": (
                self.candidate.prompt_fingerprint.to_dict()
            ),
            "case_alignment_ok": self.case_alignment_ok,
            "infrastructure_validation_pass": (
                self.infrastructure_validation_pass
            ),
            "changed_case_ids": list(self.changed_case_ids),
            "dataset_sha256": self.dataset_sha256,
            "ground_truth_sha256": self.ground_truth_sha256,
            "fixture_schema_sha256": self.fixture_schema_sha256,
            "fixture_data_sha256": self.fixture_data_sha256,
            "deepseek_calls": (
                self.baseline.deepseek_calls + self.candidate.deepseek_calls
            ),
            "db_calls": (
                self.baseline.db_calls + self.candidate.db_calls
            ),
            "network_calls": (
                self.baseline.network_calls + self.candidate.network_calls
            ),
            "environment": self.environment,
            "baseline": self.baseline.to_dict(),
            "candidate": self.candidate.to_dict(),
            "comparisons": [c.to_dict() for c in self.comparisons],
            "generated_at": self.generated_at,
        }
        _assert_no_secrets(snapshot)
        return snapshot

    def render_summary(self) -> str:
        lines = [
            f"Text-to-SQL Prompt A/B Experiment — Phase {self.phase}",
            "",
            f"Experiment type: {self.experiment_type}",
            f"Baseline prompt version : {self.baseline.prompt_version}",
            f"Baseline prompt hash    : {self.baseline.prompt_hash[:16]}…",
            f"Candidate prompt version: {self.candidate.prompt_version}",
            f"Candidate prompt hash   : {self.candidate.prompt_hash[:16]}…",
            f"Prompt hash equal       : {self.prompt_hash_equal}",
            f"Case alignment ok       : {self.case_alignment_ok}",
            f"Infra validation PASS   : {self.infrastructure_validation_pass}",
            f"Changed case ids        : {list(self.changed_case_ids) or 'none'}",
            "",
            "Baseline metrics  : "
            + _metrics_line(self.baseline.headline()),
            "Candidate metrics : "
            + _metrics_line(self.candidate.headline()),
            "",
            f"DeepSeek calls: {self.baseline.deepseek_calls + self.candidate.deepseek_calls}",
            f"DB calls     : {self.baseline.db_calls + self.candidate.db_calls}",
            f"Network calls: {self.baseline.network_calls + self.candidate.network_calls}",
        ]
        return "\n".join(lines)

    def render_report(self) -> str:
        out: list[str] = []
        add = out.append
        add(f"# Text-to-SQL Prompt A/B Experiment — Phase {self.phase}")
        add("")
        add("> **声明**：本阶段是**实验基础设施验证（Experiment Infrastructure")
        add("> Validation）**，不是 Prompt 优化。Baseline 与 Candidate A 内容")
        add("> 完全一致（同一份真实 Prompt）；差异只能来自 LLM 非确定性或")
        add("> 实验运行方式，不得据此修改 Prompt。")
        add("")
        add("## 1. A/B Consistency Gate")
        add("")
        add(f"- experiment_type: `{self.experiment_type}`")
        add(f"- baseline_prompt_version: `{self.baseline.prompt_version}`")
        add(f"- candidate_prompt_version: `{self.candidate.prompt_version}`")
        add(f"- **baseline_prompt_hash == candidate_prompt_hash**: "
            f"`{self.prompt_hash_equal}`**")
        if not self.prompt_hash_equal:
            add("")
            add("> ❌ FAIL：两份 Prompt 内容不一致，实验必须先保证 hash 相等。")
        add(f"- case_alignment_ok: `{self.case_alignment_ok}`")
        add(f"- infrastructure_validation_pass: "
            f"`{self.infrastructure_validation_pass}`**")
        if self.changed_case_ids:
            add("")
            add(f"> ⚠ 出现 {len(self.changed_case_ids)} 个差异 case"
                f"（疑似 LLM 非确定性）："
                f"{list(self.changed_case_ids)}。按 §十九，不自动重跑、")
            add("> 不修改 Prompt，仅报告。")
        add("")
        add("## 2. Prompt fingerprint")
        add("")
        add("| Prompt | Version | Hash (prefix) |")
        add("|---|---|---|")
        add(f"| Baseline | {self.baseline.prompt_version} | "
            f"{self.baseline.prompt_hash[:16]}… |")
        add(f"| Candidate A | {self.candidate.prompt_version} | "
            f"{self.candidate.prompt_hash[:16]}… |")
        add("")
        add("## 3. Objective metrics (no overall score)")
        add("")
        add("| Metric | Baseline | Candidate A |")
        add("|---|---:|---:|")
        add(f"| generation_success | "
            f"{_pct(self.baseline.headline().generation_success)} | "
            f"{_pct(self.candidate.headline().generation_success)} |")
        add(f"| validation_acceptance | "
            f"{_pct(self.baseline.headline().validation_acceptance)} | "
            f"{_pct(self.candidate.headline().validation_acceptance)} |")
        add(f"| structural_expectation_accuracy | "
            f"{_pct(self.baseline.headline().structural_expectation_accuracy)} | "
            f"{_pct(self.candidate.headline().structural_expectation_accuracy)} |")
        add(f"| execution_success | "
            f"{_pct(self.baseline.headline().execution_success)} | "
            f"{_pct(self.candidate.headline().execution_success)} |")
        add(f"| semantic_result_accuracy | "
            f"{_pct(self.baseline.headline().semantic_result_accuracy)} | "
            f"{_pct(self.candidate.headline().semantic_result_accuracy)} |")
        add(f"| security_pass | "
            f"{_pct(self.baseline.headline().security_pass)} | "
            f"{_pct(self.candidate.headline().security_pass)} |")
        add("")
        add("> semantic_result_accuracy 本阶段为 N/A：本实验为**结构实验**，"
            "semantic result 评价由 Phase 3.9.16 / 3.9.18 单独覆盖，"
            "且本阶段禁止修改 Semantic Evaluator / pipeline。")
        add("")
        add("## 4. Per-case consistency")
        add("")
        add("| case_id | baseline | candidate | changed |")
        add("|---|---|---|---|")
        for comp in self.comparisons:
            b = "PASS" if comp.baseline.structural_expectation_match else "FAIL"
            c = "PASS" if comp.candidate.structural_expectation_match else "FAIL"
            changed = "YES" if comp.changed else "no"
            add(f"| `{comp.case_id}` | {b} | {c} | {changed} |")
        add("")
        changed = [c for c in self.comparisons if c.changed]
        if changed:
            add("## 5. Diff detail (LLM non-determinism)")
            add("")
            for comp in changed:
                add(f"### `{comp.case_id}` — diff: "
                    f"{list(comp.diff_fields)}")
                add("")
                add(f"- baseline_sql: "
                    f"`{_safe_sql(comp.baseline.generated_sql)}`")
                add(f"- candidate_sql: "
                    f"`{_safe_sql(comp.candidate.generated_sql)}`")
                add(f"- baseline_failure: "
                    f"{list(comp.baseline.failure_categories)}")
                add(f"- candidate_failure: "
                    f"{list(comp.candidate.failure_categories)}")
                add("")
        add("## 6. Environment & calls")
        add("")
        env = self.environment
        add(f"- provider: `{env.get('llm_provider')}`")
        add(f"- model: `{env.get('llm_model')}`")
        add(f"- execution_mode: `{env.get('execution_mode')}`")
        add(f"- deepseek_calls: "
            f"{self.baseline.deepseek_calls + self.candidate.deepseek_calls}")
        add(f"- db_calls: {self.baseline.db_calls + self.candidate.db_calls}")
        add(f"- network_calls: "
            f"{self.baseline.network_calls + self.candidate.network_calls}")
        add("")
        add("## 7. Hashes (reproducibility)")
        add("")
        add(f"- dataset_sha256: `{self.dataset_sha256[:16]}…`")
        add(f"- ground_truth_sha256: `{self.ground_truth_sha256[:16]}…`")
        add(f"- fixture_schema_sha256: `{self.fixture_schema_sha256[:16]}…`")
        add(f"- fixture_data_sha256: `{self.fixture_data_sha256[:16]}…`")
        add("")
        add("## 8. Database residue check")
        add("")
        add("- Executor: `SQLExecutorService` with `BEGIN READ ONLY` "
            "transaction (DB-level write protection).")
        add("- No INSERT / UPDATE / DELETE / CREATE / DROP / ALTER / TRUNCATE")
        add("  can be issued by the experiment run.")
        add("- No new business data can be created in the fixture DB.")
        add("")
        return "\n".join(out)


# ============================================================
# Secret safety（§十八 / 历史纪律）
# ============================================================

def _assert_no_secrets(payload: Any) -> None:
    forbidden_keys: set[str] = set()
    stack: list[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            forbidden_keys |= {
                str(k).lower() for k in node
                if str(k).lower() in _FORBIDDEN_SNAPSHOT_KEYS
            }
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


def _pct(rate: float | None) -> str:
    if rate is None:
        return "N/A"
    return f"{rate * 100:.2f}%"


def _metrics_line(m: PromptExperimentVariantMetrics) -> str:
    return (
        f"gen={_pct(m.generation_success)} "
        f"val={_pct(m.validation_acceptance)} "
        f"struct={_pct(m.structural_expectation_accuracy)} "
        f"exec={_pct(m.execution_success)} "
        f"sec={_pct(m.security_pass)}"
    )


def _safe_sql(sql: str | None) -> str:
    if not sql:
        return ""
    return sql.replace("`", "'")[:200]


# ============================================================
# Call-counting wrappers（实验层埋点，不修改生产 service）
# ============================================================

class _CallCountingGenerator:
    """包装真实 generator，统计 DeepSeek 调用次数（不改动 generator 本身）。"""

    def __init__(self, wrapped: TextToSQLGenerator) -> None:
        self._wrapped = wrapped
        self.calls = 0

    async def generate(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return await self._wrapped.generate(*args, **kwargs)


class _CallCountingExecutor:
    """包装真实 executor，统计 DB 调用次数（不改动 executor 本身）。"""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped
        self.calls = 0

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return await self._wrapped.execute(*args, **kwargs)


# ============================================================
# Runner（§八：复用现有 Pipeline，只做编排）
# ============================================================

def _security_category(case: TextToSQLEvaluationCase) -> str | None:
    expected: TextToSQLExpectations = case.expected
    if expected.must_pass_validation is False:
        return "negative"
    if expected.must_pass_validation is True:
        return "positive"
    return None


def _to_case_result(
    result: TextToSQLEvaluationResult,
    case: TextToSQLEvaluationCase,
    variant: str,
) -> ExperimentCaseResult:
    return ExperimentCaseResult(
        case_id=result.case_id,
        prompt_variant=variant,
        project_id=result.project_id,
        generated_sql=result.generated_sql,
        generation_success=result.generated_sql is not None,
        validation_success=result.validation_passed,
        structural_expectation_match=result.passed,
        execution_success=result.execution_passed,
        semantic_result_correct=None,
        security_category=_security_category(case),
        failure_categories=result.failed_expectations,
        matched_expectations=result.matched_expectations,
        error_code=result.error_code,
    )


def _diff_fields(
    base: ExperimentCaseResult, cand: ExperimentCaseResult
) -> tuple[str, ...]:
    diffs: list[str] = []
    if base.generation_success != cand.generation_success:
        diffs.append("generation_success")
    if base.validation_success != cand.validation_success:
        diffs.append("validation_success")
    if base.structural_expectation_match != cand.structural_expectation_match:
        diffs.append("structural_expectation_match")
    if base.execution_success != cand.execution_success:
        diffs.append("execution_success")
    if base.failure_categories != cand.failure_categories:
        diffs.append("failure_categories")
    return tuple(diffs)


class PromptExperimentRunner:
    """Phase 3.9.20 A/B 实验编排器。

    只编排，不重造：generator / validator / executor / evaluator 全部来自
    现有生产服务（§八）。Baseline 与 Candidate 本阶段使用**同一份**
    真实 Prompt（``load_prompt_fingerprint`` 读取），因此两者 ``prompt_hash``
    必然相等 —— 这是基础设施验证的核心断言。
    """

    def __init__(
        self,
        *,
        generator: TextToSQLGenerator,
        context_resolver: EvaluationContextResolver,
        validator: Any | None = None,
        executor: Any | None = None,
        cases: Sequence[TextToSQLEvaluationCase] | None = None,
        dataset_path: str | Path | None = None,
        prompt_version: str = PROMPT_VERSION,
        isolation_project_ids: Sequence[str] = ("eval-project-a",
                                                  "eval-project-b"),
        force_execution: bool = False,
        candidate_generator: TextToSQLGenerator | None = None,
        candidate_prompt_fingerprint: "PromptFingerprint | None" = None,
    ) -> None:
        self._generator = generator
        # 本阶段 Candidate == Baseline 内容（同一份真实 Prompt），因此默认
        # 复用同一个 generator。未来真正做 Prompt Optimization 时，可传入
        # 一个不同的 generator 而无需修改本文件以外的任何生产代码。
        self._candidate_generator = candidate_generator or generator
        # Phase 3.9.22：Candidate 使用不同 Prompt 文件时，其指纹由调用方
        # 提供（默认仍为 Baseline 指纹，保持 3.9.20/3.9.21 行为不变）。
        self._candidate_fingerprint = (
            candidate_prompt_fingerprint
            if candidate_prompt_fingerprint is not None
            else load_prompt_fingerprint(prompt_version)
        )
        self._resolver = context_resolver
        self._validator = validator
        self._executor = executor
        self._cases = (
            tuple(cases)
            if cases is not None
            else load_text_to_sql_regression_dataset(dataset_path)
        )
        self._prompt_version = prompt_version
        self._isolation = tuple(isolation_project_ids)
        self._force_execution = force_execution
        self._prompt_fingerprint = load_prompt_fingerprint(prompt_version)

    async def run_variant(
        self,
        variant: str,
        fingerprint: "PromptFingerprint | None" = None,
    ) -> PromptExperimentVariantResult:
        """运行单个 variant（Baseline 或 Candidate A）。"""
        fp = fingerprint or self._prompt_fingerprint
        gen = _CallCountingGenerator(self._generator)
        exec_wrapper = (
            _CallCountingExecutor(self._executor) if self._executor else None
        )
        cases = self._cases
        if self._force_execution:
            cases = tuple(with_execution_required(c) for c in cases)

        baseline = await run_real_llm_baseline(
            generator=gen,
            context_resolver=self._resolver,
            validator=self._validator,
            executor=exec_wrapper,
            cases=cases,
            phase=PHASE_3_9_20,
            environment=collect_real_llm_environment_info(),
            isolation_project_ids=self._isolation,
        )

        case_by_id = {c.case_id: c for c in cases}
        results = tuple(
            _to_case_result(r, case_by_id[r.case_id], variant)
            for r in baseline.results
        )
        db_calls = exec_wrapper.calls if exec_wrapper is not None else 0
        return PromptExperimentVariantResult(
            variant=variant,
            prompt_version=fp.prompt_version,
            prompt_hash=fp.combined_prompt_hash,
            prompt_fingerprint=fp,
            results=results,
            metrics=baseline.metrics,
            total_cases=len(cases),
            deepseek_calls=gen.calls,
            db_calls=db_calls,
            network_calls=gen.calls + db_calls,
        )

    async def run_baseline(self) -> PromptExperimentVariantResult:
        return await self.run_variant("baseline")

    async def run_candidate(self) -> PromptExperimentVariantResult:
        # 3.9.20/3.9.21：Candidate == Baseline 内容（同一份真实 Prompt）。
        # 3.9.22 起：candidate_generator / candidate_prompt_fingerprint 可
        # 指向不同 Prompt 版本（实验层装配，不改生产代码）。
        saved = self._generator
        self._generator = self._candidate_generator
        try:
            return await self.run_variant(
                "candidate", fingerprint=self._candidate_fingerprint
            )
        finally:
            self._generator = saved

    async def compare(self) -> PromptExperimentSummary:
        """跑 Baseline 再跑 Candidate，逐 case 比较（§十四 顺序固定）。"""
        baseline = await self.run_baseline()
        candidate = await self.run_candidate()

        base_by = {r.case_id: r for r in baseline.results}
        cand_by = {r.case_id: r for r in candidate.results}
        base_ids = [r.case_id for r in baseline.results]
        cand_ids = [r.case_id for r in candidate.results]
        alignment_ok = base_ids == cand_ids

        comparisons: list[ExperimentCaseComparison] = []
        changed: list[str] = []
        for cid in base_ids:
            b = base_by[cid]
            c = cand_by.get(cid)
            if c is None:
                continue
            diffs = _diff_fields(b, c)
            if diffs:
                changed.append(cid)
            comparisons.append(
                ExperimentCaseComparison(
                    case_id=cid,
                    changed=bool(diffs),
                    diff_fields=diffs,
                    baseline=b,
                    candidate=c,
                )
            )

        hash_equal = baseline.prompt_hash == candidate.prompt_hash
        infra_pass = hash_equal and alignment_ok and not changed

        fp = read_dataset_fingerprints()
        environment = self._environment_dict()
        return PromptExperimentSummary(
            phase=PHASE_3_9_20,
            experiment_type=EXPERIMENT_TYPE,
            baseline=baseline,
            candidate=candidate,
            prompt_hash_equal=hash_equal,
            case_alignment_ok=alignment_ok,
            infrastructure_validation_pass=infra_pass,
            changed_case_ids=tuple(changed),
            comparisons=tuple(comparisons),
            dataset_sha256=fp["dataset_sha256"],
            ground_truth_sha256=fp["ground_truth_sha256"],
            fixture_schema_sha256=fp["fixture_schema_sha256"],
            fixture_data_sha256=fp["fixture_data_sha256"],
            environment=environment,
        )

    def _environment_dict(self) -> dict[str, Any]:
        env: TextToSQLBaselineEnvironment = collect_real_llm_environment_info()
        data = env.as_dict()
        return {
            "llm_provider": data.get("llm_provider"),
            "llm_model": data.get("llm_model"),
            "execution_mode": data.get("execution_mode"),
            "python_version": data.get("python_version"),
            "database": data.get("database"),
        }


# ============================================================
# Offline snapshot validation（§十六：--check，0 LLM/DB/network）
# ============================================================

def _result_from_snapshot_case(entry: dict[str, Any]) -> TextToSQLEvaluationResult:
    """把 Snapshot 的 case 条目还原为 Result，仅用于 metrics 重算。

    读取 ``ExperimentCaseResult.to_dict`` 的键名（validation_success /
    execution_success / failure_categories / matched_expectations）。
    """
    return TextToSQLEvaluationResult(
        case_id=str(entry.get("case_id", "")),
        project_id=entry.get("project_id"),
        question="",
        generated_sql=None,
        validation_passed=bool(entry.get("validation_success", False)),
        execution_passed=entry.get("execution_success"),
        matched_expectations=tuple(entry.get("matched_expectations", ())),
        failed_expectations=tuple(entry.get("failure_categories", ())),
        error_code=entry.get("error_code"),
    )


def _recompute_variant_metrics(
    variant_dict: dict[str, Any], cases: Sequence[TextToSQLEvaluationCase]
) -> dict[str, Any]:
    """由 Snapshot variant 的 results[] 重算 3.9.5 metrics。"""
    results = tuple(
        _result_from_snapshot_case(e)
        for e in variant_dict.get("results", [])
        if isinstance(e, dict)
    )
    return calculate_real_llm_baseline_metrics(results, cases).as_dict()


def validate_experiment_snapshot(
    snapshot: dict[str, Any],
    cases: Sequence[TextToSQLEvaluationCase] | None = None,
) -> list[str]:
    """纯 offline 校验 Snapshot 完整性（§十六 / §十八）。

    Returns: 问题列表（空 = 通过）。不调用 LLM / DB / 网络、不写文件。
    """
    problems: list[str] = []
    if cases is None:
        cases = load_text_to_sql_regression_dataset()

    # ---- phase / experiment_type ----
    if snapshot.get("phase") != PHASE_3_9_20:
        problems.append(f"phase: expected {PHASE_3_9_20!r}, "
                        f"got {snapshot.get('phase')!r}")
    if snapshot.get("experiment_type") != EXPERIMENT_TYPE:
        problems.append(f"experiment_type: expected {EXPERIMENT_TYPE!r}, "
                        f"got {snapshot.get('experiment_type')!r}")

    # ---- Prompt hash 硬 Gate（§十八） ----
    base_hash = snapshot.get("baseline_prompt_hash")
    cand_hash = snapshot.get("candidate_prompt_hash")
    if base_hash != cand_hash:
        problems.append(
            f"prompt hash mismatch: baseline={base_hash!r} "
            f"candidate={cand_hash!r}"
        )
    if snapshot.get("baseline_prompt_version") != PROMPT_VERSION:
        problems.append(
            f"baseline_prompt_version: expected {PROMPT_VERSION!r}"
        )
    if snapshot.get("candidate_prompt_version") != PROMPT_VERSION:
        problems.append(
            f"candidate_prompt_version: expected {PROMPT_VERSION!r}"
        )

    # ---- stored prompt hashes 必须匹配当前真实 Prompt 文件 ----
    live_hash = load_prompt_fingerprint().combined_prompt_hash
    if snapshot.get("baseline_prompt_hash") != live_hash:
        problems.append(
            f"baseline_prompt_hash not matching live prompt: "
            f"stored={str(snapshot.get('baseline_prompt_hash'))[:16]}… "
            f"live={live_hash[:16]}…"
        )
    if snapshot.get("candidate_prompt_hash") != live_hash:
        problems.append(
            f"candidate_prompt_hash not matching live prompt: "
            f"stored={str(snapshot.get('candidate_prompt_hash'))[:16]}… "
            f"live={live_hash[:16]}…"
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

    # ---- 14 cases 对齐（顺序固定） ----
    dataset_ids = [c.case_id for c in cases]
    base_var = snapshot.get("baseline") or {}
    cand_var = snapshot.get("candidate") or {}
    for label, var in (("baseline", base_var), ("candidate", cand_var)):
        var_ids = [
            e.get("case_id") for e in var.get("results", [])
            if isinstance(e, dict)
        ]
        if set(var_ids) != set(dataset_ids):
            missing = sorted(set(dataset_ids) - set(var_ids))
            extra = sorted(set(var_ids) - set(dataset_ids))
            if missing:
                problems.append(f"{label}: missing case_id {missing}")
            if extra:
                problems.append(f"{label}: unexpected case_id {extra}")
        elif var_ids != dataset_ids:
            # 顺序固定（§十四）：与 dataset 顺序必须一致
            problems.append(
                f"{label}: case order mismatch (must follow dataset order)"
            )

    # ---- metrics 重算（不信任存量值） ----
    for label, var in (("baseline", base_var), ("candidate", cand_var)):
        if not var:
            problems.append(f"{label}: variant block missing")
            continue
        recalculated = _recompute_variant_metrics(var, cases)
        stored = var.get("metrics") or {}
        for key in (
            "total_cases", "passed_cases", "failed_cases",
            "validation_passed_cases", "validation_expectation_cases",
            "validation_expectation_passed_cases", "execution_cases",
            "execution_passed_cases", "security_cases",
            "security_passed_cases", "validator_accepted_cases",
        ):
            if stored.get(key) != recalculated.get(key):
                problems.append(
                    f"{label}.metrics.{key}: snapshot={stored.get(key)!r} "
                    f"recalculated={recalculated.get(key)!r}"
                )
        # LLM 专属比率自洽（存量计数 → 存量比率）
        total = stored.get("total_cases")
        generated = stored.get("llm_generated_cases")
        accepted = recalculated.get("validator_accepted_cases")
        if isinstance(total, int) and isinstance(generated, int) and \
                isinstance(accepted, int) and total > 0:
            if not 0 <= generated <= total:
                problems.append(f"{label}.metrics.llm_generated_cases "
                                f"out of range: {generated}")
            if generated < accepted:
                problems.append(f"{label}.metrics.llm_generated_cases < "
                                f"validator_accepted_cases")
            if stored.get("llm_generation_success_rate") != \
                    round(generated / total, RATE_PRECISION):
                problems.append(f"{label}.metrics.llm_generation_success_rate "
                                f"inconsistent")
            exp_acc = (round(accepted / generated, RATE_PRECISION)
                       if generated else None)
            if stored.get("validator_acceptance_rate") != exp_acc:
                problems.append(f"{label}.metrics.validator_acceptance_rate "
                                f"inconsistent")

    # ---- calls 自洽 ----
    for key in ("deepseek_calls", "db_calls", "network_calls"):
        if not isinstance(snapshot.get(key), int) or snapshot.get(key) < 0:
            problems.append(f"{key}: invalid {snapshot.get(key)!r}")
    if snapshot.get("network_calls") != (
        snapshot.get("deepseek_calls", 0) + snapshot.get("db_calls", 0)
    ):
        problems.append("network_calls != deepseek_calls + db_calls")

    # ---- secret safety ----
    forbidden_keys: set[str] = set()
    stack: list[Any] = [snapshot]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            forbidden_keys |= {
                str(k).lower() for k in node
                if str(k).lower() in _FORBIDDEN_SNAPSHOT_KEYS
            }
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    if forbidden_keys:
        problems.append(f"forbidden keys: {sorted(forbidden_keys)}")
    blob = _json_dumps_safe(snapshot)
    for fragment in _FORBIDDEN_SNAPSHOT_FRAGMENTS:
        if fragment in blob:
            problems.append(f"forbidden fragment: {fragment!r}")

    return problems


def _json_dumps_safe(payload: Any) -> str:  # noqa: A001
    """Snapshot 自检用的小写序列化（不抛异常）。"""
    try:
        return json.dumps(payload, ensure_ascii=False).lower()
    except (TypeError, ValueError):
        return repr(payload).lower()
