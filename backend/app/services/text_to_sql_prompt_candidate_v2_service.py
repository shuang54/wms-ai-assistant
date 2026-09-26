"""Phase 3.9.22 — Text-to-SQL Prompt A/B Optimization: Candidate v2.

First formal prompt optimization, strictly scoped:

- Baseline v1 is FROZEN (``backend/app/prompts/*.txt``, unchanged; its
  hash must equal the hashes recorded by 3.9.20 / 3.9.21, otherwise the
  experiment is INVALID).
- Candidate v2 lives in ``backend/app/prompts/v2/*.txt``: v1 content plus
  ONE minimal ``DESTRUCTIVE REQUEST HANDLING`` section targeting the known
  ``safety_delete_all_documents`` structural failure. It never weakens
  SELECT-only / read-only / LIMIT / allowlist / Validator rules and never
  asks the model to emit dangerous SQL.
- The Candidate generator is the UNCHANGED ``TextToSQLService``; the v2
  prompt files are wired in at the experiment layer only (prompt paths are
  read per ``generate()`` call from module constants, so the wrapper swaps
  them around each call and always restores them).
- Reuses the 3.9.20 experiment framework end to end
  (``PromptExperimentRunner`` / fingerprints / metrics / comparisons).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from backend.app.services.text_to_sql_evaluation_service import (
    TextToSQLEvaluationCase,
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_prompt_experiment_service import (
    PROMPT_VERSION,
    SYSTEM_PROMPT_PATH,
    USER_PROMPT_PATH,
    RETRY_PROMPT_PATH,
    SNAPSHOT_3_9_20_PATH,
    PromptExperimentRunner,
    PromptFingerprint,
    _FORBIDDEN_SNAPSHOT_KEYS,
    _assert_no_secrets,
    _recompute_variant_metrics,
    load_prompt_fingerprint,
    read_dataset_fingerprints,
)
from backend.app.services.text_to_sql_baseline_stability_service import (
    SNAPSHOT_3_9_21_PATH,
    collect_stability_environment,
    compute_case_order_hash,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PHASE_3_9_22",
    "EXPERIMENT_TYPE_3_9_22",
    "PROMPT_VERSION_V2",
    "CANDIDATE_SYSTEM_PROMPT_PATH",
    "CANDIDATE_USER_PROMPT_PATH",
    "CANDIDATE_RETRY_PROMPT_PATH",
    "SNAPSHOT_3_9_22_PATH",
    "REPORT_3_9_22_PATH",
    "V2PromptGenerator",
    "load_candidate_fingerprint",
    "check_baseline_frozen",
    "classify_changes",
    "build_v2_snapshot",
    "render_v2_report",
    "validate_candidate_v2_snapshot",
]


# ============================================================
# 常量 & 路径
# ============================================================

PHASE_3_9_22: Final[str] = "3.9.22"
EXPERIMENT_TYPE_3_9_22: Final[str] = "prompt_candidate_v2_ab"
PROMPT_VERSION_V2: Final[str] = "v2"

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_PROMPTS_DIR: Final[Path] = _REPO_ROOT / "backend" / "app" / "prompts"

#: Candidate v2 = v1 原文 + 最小 DESTRUCTIVE REQUEST HANDLING。
CANDIDATE_SYSTEM_PROMPT_PATH: Final[Path] = (
    _PROMPTS_DIR / "v2" / "text_to_sql_system.txt"
)
CANDIDATE_USER_PROMPT_PATH: Final[Path] = (
    _PROMPTS_DIR / "v2" / "text_to_sql_user.txt"
)
CANDIDATE_RETRY_PROMPT_PATH: Final[Path] = (
    _PROMPTS_DIR / "v2" / "text_to_sql_retry.txt"
)

SNAPSHOT_3_9_22_PATH: Final[Path] = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_22_prompt_candidate_v2.json"
)
REPORT_3_9_22_PATH: Final[Path] = (
    _REPO_ROOT / "docs" / "evaluation"
    / "text-to-sql-prompt-candidate-v2-3.9.22.md"
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fingerprint_from_paths(
    system: Path, user: Path, retry: Path, version: str
) -> PromptFingerprint:
    text_system = Path(system).read_text(encoding="utf-8")
    text_user = Path(user).read_text(encoding="utf-8")
    text_retry = Path(retry).read_text(encoding="utf-8")
    combined = text_system + "\n" + text_user + "\n" + text_retry
    return PromptFingerprint(
        prompt_version=version,
        system_prompt_hash=_sha256_text(text_system),
        user_prompt_hash=_sha256_text(text_user),
        retry_prompt_hash=_sha256_text(text_retry),
        combined_prompt_hash=_sha256_text(combined),
    )


def load_candidate_fingerprint(
    prompt_version: str = PROMPT_VERSION_V2,
) -> PromptFingerprint:
    """Candidate v2 指纹（system/user/retry 三个 hash + combined）。"""
    return _fingerprint_from_paths(
        CANDIDATE_SYSTEM_PROMPT_PATH,
        CANDIDATE_USER_PROMPT_PATH,
        CANDIDATE_RETRY_PROMPT_PATH,
        prompt_version,
    )


# ============================================================
# Baseline 冻结校验（§三：与 3.9.20 / 3.9.21 记录一致）
# ============================================================

def check_baseline_frozen() -> list[str]:
    """Baseline v1 必须与历史 snapshot 记录的 hash 完全一致。

    Returns: 问题列表（空 = 冻结成立）。任何问题都意味着 EXPERIMENT INVALID。
    """
    problems: list[str] = []
    live = load_prompt_fingerprint()

    if not SNAPSHOT_3_9_20_PATH.exists():
        problems.append("3.9.20 snapshot missing (cannot verify baseline)")
    else:
        s20 = json.loads(
            SNAPSHOT_3_9_20_PATH.read_text(encoding="utf-8")
        )
        if s20.get("baseline_prompt_hash") != live.combined_prompt_hash:
            problems.append(
                "baseline hash differs from 3.9.20 record: "
                f"{str(s20.get('baseline_prompt_hash'))[:16]}… != "
                f"{live.combined_prompt_hash[:16]}…"
            )

    if not SNAPSHOT_3_9_21_PATH.exists():
        problems.append("3.9.21 snapshot missing (cannot verify baseline)")
    else:
        s21 = json.loads(
            SNAPSHOT_3_9_21_PATH.read_text(encoding="utf-8")
        )
        if s21.get("prompt_hash") != live.combined_prompt_hash:
            problems.append(
                "baseline hash differs from 3.9.21 record: "
                f"{str(s21.get('prompt_hash'))[:16]}… != "
                f"{live.combined_prompt_hash[:16]}…"
            )
    return problems


# ============================================================
# Candidate v2 generator（实验层接线，不改生产代码）
# ============================================================

@contextlib.contextmanager
def _patched_prompt_paths(system: Path, user: Path, retry: Path):
    """在调用期间把 TextToSQLService 的 prompt 路径指向 v2 文件。

    ``generate()`` 每次调用时读取模块级常量，因此仅在调用期间替换、
    结束后恢复 —— 生产代码零修改。
    """
    import backend.app.services.text_to_sql_service as _svc

    saved = (
        _svc._SYSTEM_PROMPT_FILE,
        _svc._USER_PROMPT_FILE,
        _svc._RETRY_PROMPT_FILE,
    )
    _svc._SYSTEM_PROMPT_FILE = system
    _svc._USER_PROMPT_FILE = user
    _svc._RETRY_PROMPT_FILE = retry
    try:
        yield
    finally:
        (
            _svc._SYSTEM_PROMPT_FILE,
            _svc._USER_PROMPT_FILE,
            _svc._RETRY_PROMPT_FILE,
        ) = saved


class V2PromptGenerator:
    """包装未改动的 TextToSQLService，使其使用 Candidate v2 Prompt。"""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    async def generate(self, *args: Any, **kwargs: Any) -> Any:
        with _patched_prompt_paths(
            CANDIDATE_SYSTEM_PROMPT_PATH,
            CANDIDATE_USER_PROMPT_PATH,
            CANDIDATE_RETRY_PROMPT_PATH,
        ):
            return await self._wrapped.generate(*args, **kwargs)


# ============================================================
# Runner 装配 + 回归 / 改进分类
# ============================================================

def build_v2_runner(
    *,
    context_resolver: Any,
    validator: Any | None = None,
    executor: Any | None = None,
    cases: Sequence[TextToSQLEvaluationCase] | None = None,
    dataset_path: str | Path | None = None,
) -> PromptExperimentRunner:
    """Baseline v1 generator + Candidate v2 generator（同一 Validator）。"""
    from backend.app.services.text_to_sql_service import TextToSQLService

    return PromptExperimentRunner(
        generator=TextToSQLService(),
        candidate_generator=V2PromptGenerator(TextToSQLService()),
        candidate_prompt_fingerprint=load_candidate_fingerprint(),
        context_resolver=context_resolver,
        validator=validator,
        executor=executor,
        cases=cases,
        dataset_path=dataset_path,
    )


def _structural_pass(side: Any) -> bool:
    return bool(getattr(side, "structural_expectation_match", False))


def classify_changes(
    comparisons: Sequence[Any],
) -> tuple[list[str], list[str]]:
    """§十二 / §十四：回归（Baseline PASS → Candidate FAIL）与改进。

    纯函数，只依据 structural expectation match。
    """
    regression: list[str] = []
    improvement: list[str] = []
    for comp in comparisons:
        case_id = getattr(comp, "case_id", "")
        base_pass = _structural_pass(getattr(comp, "baseline", None))
        cand_pass = _structural_pass(getattr(comp, "candidate", None))
        if base_pass and not cand_pass:
            regression.append(case_id)
        elif (not base_pass) and cand_pass:
            improvement.append(case_id)
    return regression, improvement


# ============================================================
# Snapshot（§十五）
# ============================================================

def build_v2_snapshot(summary: Any) -> dict[str, Any]:
    """把 3.9.20 框架的 A/B summary 转成 3.9.22 独立 snapshot。"""
    comparisons = getattr(summary, "comparisons", ())
    regression, improvement = classify_changes(comparisons)
    fp = read_dataset_fingerprints()
    cases = load_text_to_sql_regression_dataset()
    baseline = getattr(summary, "baseline")
    candidate = getattr(summary, "candidate")

    total = len(cases)
    b_pass = sum(
        1 for r in baseline.results if r.structural_expectation_match
    )
    c_pass = sum(
        1 for r in candidate.results if r.structural_expectation_match
    )
    deepseek = baseline.deepseek_calls + candidate.deepseek_calls
    db_calls = baseline.db_calls + candidate.db_calls
    snapshot: dict[str, Any] = {
        "phase": PHASE_3_9_22,
        "experiment_type": EXPERIMENT_TYPE_3_9_22,
        "baseline_prompt_version": baseline.prompt_version,
        "candidate_prompt_version": candidate.prompt_version,
        "baseline_prompt_hashes": baseline.prompt_fingerprint.to_dict(),
        "candidate_prompt_hashes": candidate.prompt_fingerprint.to_dict(),
        "baseline_prompt_hash": baseline.prompt_hash,
        "candidate_prompt_hash": candidate.prompt_hash,
        "prompt_hash_different": (
            baseline.prompt_hash != candidate.prompt_hash
        ),
        "dataset_sha256": fp["dataset_sha256"],
        "ground_truth_sha256": fp["ground_truth_sha256"],
        "fixture_schema_sha256": fp["fixture_schema_sha256"],
        "fixture_data_sha256": fp["fixture_data_sha256"],
        "case_order_hash": compute_case_order_hash(cases),
        "baseline": baseline.to_dict(),
        "candidate": candidate.to_dict(),
        "per_case_comparison": [c.to_dict() for c in comparisons],
        "regression_cases": regression,
        "improvement_cases": improvement,
        "summary": {
            "total_cases": total,
            "baseline": {
                "generation_success": baseline.metrics.llm_generated_cases,
                "validator_accepted": baseline.metrics.validator_accepted_cases,
                "structural_match": b_pass,
            },
            "candidate": {
                "generation_success": candidate.metrics.llm_generated_cases,
                "validator_accepted": candidate.metrics.validator_accepted_cases,
                "structural_match": c_pass,
            },
            "regression_count": len(regression),
            "improvement_count": len(improvement),
            "calls": {
                "deepseek_calls": deepseek,
                "db_calls": db_calls,
                "network_calls": deepseek + db_calls,
            },
        },
        "environment": collect_stability_environment(),
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
    }
    _assert_no_secrets(snapshot)
    return snapshot


def render_v2_report(snapshot: dict[str, Any]) -> str:
    """渲染 3.9.22 Markdown 报告。"""
    out: list[str] = []
    add = out.append
    b = snapshot["baseline"]
    c = snapshot["candidate"]
    add(f"# Text-to-SQL Prompt A/B Optimization — Candidate v2 "
        f"(Phase {snapshot['phase']})")
    add("")
    add("> 目标：仅验证 Candidate v2 是否改善 `safety_delete_all_documents`")
    add("> 的已知 structural failure，且不破坏现有 PASS case 与安全约束。")
    add("> 单次改善不等于“Prompt 已证明更好”（§十四）。")
    add("")
    add("## 1. Candidate 修改内容")
    add("")
    add("- 仅新增 `DESTRUCTIVE REQUEST HANDLING` 节（system prompt）：")
    add("  destructive 请求不得改写成无关 SELECT，输出 refusal marker，")
    add("  Validator 仍是最终安全边界。")
    add("- user prompt：新增 1 条对应 reminder。")
    add("- retry prompt：新增 1 条“不得把拒绝修复成替代 SELECT”的规则。")
    add("- 未弱化任何安全约束；未生成危险 SQL；未新增 response schema。")
    add("")
    add("## 2. Prompt hashes")
    add("")
    add("| Prompt | Version | system | user | retry | combined |")
    add("|---|---|---|---|---|---|")
    for label, fp in (
        ("Baseline", snapshot["baseline_prompt_hashes"]),
        ("Candidate v2", snapshot["candidate_prompt_hashes"]),
    ):
        add(
            f"| {label} | {fp['prompt_version']} "
            f"| {fp['system_prompt_hash'][:12]}… "
            f"| {fp['user_prompt_hash'][:12]}… "
            f"| {fp['retry_prompt_hash'][:12]}… "
            f"| {fp['combined_prompt_hash'][:12]}… |"
        )
    add("")
    add(f"- baseline_hash != candidate_hash: "
        f"`{snapshot['prompt_hash_different']}`")
    add("")
    add("## 3. Hashes")
    add("")
    for key in ("dataset_sha256", "ground_truth_sha256",
                "fixture_schema_sha256", "fixture_data_sha256",
                "case_order_hash"):
        add(f"- {key}: `{str(snapshot.get(key))[:16]}…`")
    add("")
    add("## 4. Metrics")
    add("")
    add("| Metric | Baseline v1 | Candidate v2 |")
    add("|---|---:|---:|")
    for key, label in (
        ("generation_success", "generation_success"),
        ("validator_accepted", "validator_accepted"),
        ("structural_match", "structural_match"),
    ):
        add(f"| {label} | {snapshot['summary']['baseline'][key]}/14 "
            f"| {snapshot['summary']['candidate'][key]}/14 |")
    add("")
    add("## 5. Per-case comparison")
    add("")
    add("| case_id | baseline | candidate | changed |")
    add("|---|---|---|---|")
    for comp in snapshot["per_case_comparison"]:
        base = comp["baseline"]
        cand = comp["candidate"]
        add(
            f"| `{comp['case_id']}` "
            f"| {'PASS' if base['structural_expectation_match'] else 'FAIL'} "
            f"| {'PASS' if cand['structural_expectation_match'] else 'FAIL'} "
            f"| {'YES' if comp['changed'] else 'no'} |"
        )
    add("")
    add(f"## 6. Regression / Improvement")
    add("")
    add(f"- regression_cases (Baseline PASS → Candidate FAIL): "
        f"{snapshot['regression_cases'] or 'none'}")
    add(f"- improvement_cases (Baseline FAIL → Candidate PASS): "
        f"{snapshot['improvement_cases'] or 'none'}")
    add("")
    add("## 7. safety_delete_all_documents 详细结果")
    add("")
    for label, comp_key in (("Baseline", "baseline"), ("Candidate", "candidate")):
        entry = next(
            (x for x in snapshot[comp_key]["results"]
             if x["case_id"] == "safety_delete_all_documents"),
            None,
        )
        if entry is None:
            continue
        add(f"### {label}")
        add("")
        add(f"- generated_sql: `{(entry.get('generated_sql') or '')[:220]}`")
        add(f"- validation_success: `{entry['validation_success']}`")
        add(f"- structural_expectation_match: "
            f"`{entry['structural_expectation_match']}`")
        add(f"- security_category: `{entry.get('security_category')}`")
        add(f"- failure_categories: {entry['failure_categories']}")
        add(f"- error_code: `{entry.get('error_code')}`")
        add("")
    add("## 8. Calls")
    add("")
    calls = snapshot["summary"]["calls"]
    add(f"- DeepSeek calls: {calls['deepseek_calls']}")
    add(f"- DB calls: {calls['db_calls']}")
    add(f"- Network calls: {calls['network_calls']}")
    add("")
    return "\n".join(out)


# ============================================================
# Offline validation（§十七）
# ============================================================

def validate_candidate_v2_snapshot(
    snapshot: dict[str, Any],
    cases: Sequence[TextToSQLEvaluationCase] | None = None,
) -> list[str]:
    """纯 offline 校验（0 LLM / 0 DB / 0 network）。"""
    problems: list[str] = []
    if cases is None:
        cases = load_text_to_sql_regression_dataset()
    dataset_ids = [c.case_id for c in cases]

    # ---- phase / type / versions ----
    if snapshot.get("phase") != PHASE_3_9_22:
        problems.append(f"phase: expected {PHASE_3_9_22!r}")
    if snapshot.get("experiment_type") != EXPERIMENT_TYPE_3_9_22:
        problems.append(
            f"experiment_type: expected {EXPERIMENT_TYPE_3_9_22!r}"
        )
    if snapshot.get("baseline_prompt_version") != PROMPT_VERSION:
        problems.append(f"baseline_prompt_version: expected {PROMPT_VERSION!r}")
    if snapshot.get("candidate_prompt_version") != PROMPT_VERSION_V2:
        problems.append(
            f"candidate_prompt_version: expected {PROMPT_VERSION_V2!r}"
        )

    # ---- hashes：live 文件 + 历史冻结 ----
    live_base = load_prompt_fingerprint()
    live_cand = load_candidate_fingerprint()
    base_fp = snapshot.get("baseline_prompt_hashes") or {}
    cand_fp = snapshot.get("candidate_prompt_hashes") or {}
    for key in ("system_prompt_hash", "user_prompt_hash",
                "retry_prompt_hash", "combined_prompt_hash"):
        if base_fp.get(key) != getattr(live_base, key):
            problems.append(f"baseline_prompt_hashes.{key} != live v1 files")
        if cand_fp.get(key) != getattr(live_cand, key):
            problems.append(f"candidate_prompt_hashes.{key} != live v2 files")
    if snapshot.get("baseline_prompt_hash") != live_base.combined_prompt_hash:
        problems.append("baseline_prompt_hash != live baseline")
    if snapshot.get("candidate_prompt_hash") != live_cand.combined_prompt_hash:
        problems.append("candidate_prompt_hash != live candidate")
    if snapshot.get("baseline_prompt_hash") == \
            snapshot.get("candidate_prompt_hash"):
        problems.append("baseline and candidate prompt hashes identical")
    for problem in check_baseline_frozen():
        problems.append(f"baseline freeze: {problem}")

    # ---- dataset / fixture / order ----
    fp = read_dataset_fingerprints()
    for key in ("dataset_sha256", "ground_truth_sha256",
                "fixture_schema_sha256", "fixture_data_sha256"):
        if snapshot.get(key) != fp[key]:
            problems.append(f"{key}: mismatch vs current files")
    if snapshot.get("case_order_hash") != compute_case_order_hash(cases):
        problems.append("case_order_hash mismatch")

    # ---- 14 + 14 对齐 ----
    for label in ("baseline", "candidate"):
        block = snapshot.get(label) or {}
        ids = [
            e.get("case_id") for e in block.get("results", [])
            if isinstance(e, dict)
        ]
        if ids != dataset_ids:
            problems.append(f"{label}: 14-case alignment/order mismatch")

    # ---- metrics 重算（复用 3.9.20 重算路径） ----
    #: 这些 key 只依赖 validation/execution/expectations（快照内可重算）；
    #: llm_generated_* 依赖 generated_sql，单独核对。
    _COMPARABLE_METRIC_KEYS: Final[tuple[str, ...]] = (
        "total_cases", "passed_cases", "failed_cases",
        "validation_passed_cases", "validation_expectation_cases",
        "validation_expectation_passed_cases", "execution_cases",
        "execution_passed_cases", "security_cases", "security_passed_cases",
        "validator_accepted_cases",
    )
    from backend.app.services.text_to_sql_baseline_service import (
        RATE_PRECISION,
    )
    for label in ("baseline", "candidate"):
        block = snapshot.get(label) or {}
        stored = block.get("metrics") or {}
        recalculated = _recompute_variant_metrics(block, cases)
        for key in _COMPARABLE_METRIC_KEYS:
            if stored.get(key) != recalculated.get(key):
                problems.append(
                    f"{label}.metrics.{key}: snapshot={stored.get(key)!r} "
                    f"recalculated={recalculated.get(key)!r}"
                )
        # LLM 专属计数：由存储的 generated_sql 重算
        entries = [
            e for e in block.get("results", []) if isinstance(e, dict)
        ]
        generated = sum(1 for e in entries if e.get("generated_sql"))
        total = len(entries)
        if stored.get("llm_generated_cases") != generated:
            problems.append(
                f"{label}.metrics.llm_generated_cases: "
                f"snapshot={stored.get('llm_generated_cases')} "
                f"recalculated={generated}"
            )
        exp_rate = (
            round(generated / total, RATE_PRECISION) if total else None
        )
        if stored.get("llm_generation_success_rate") != exp_rate:
            problems.append(
                f"{label}.metrics.llm_generation_success_rate inconsistent"
            )
        accepted = recalculated.get("validator_accepted_cases")
        exp_acc = (
            round(accepted / generated, RATE_PRECISION)
            if isinstance(accepted, int) and generated else None
        )
        if stored.get("validator_acceptance_rate") != exp_acc:
            problems.append(
                f"{label}.metrics.validator_acceptance_rate inconsistent"
            )

    # ---- regression / improvement 重算 ----
    stored_cmp = snapshot.get("per_case_comparison") or []
    rebuilt_reg: list[str] = []
    rebuilt_imp: list[str] = []
    for comp in stored_cmp:
        if not isinstance(comp, dict):
            continue
        base_pass = bool(
            (comp.get("baseline") or {}).get(
                "structural_expectation_match"
            )
        )
        cand_pass = bool(
            (comp.get("candidate") or {}).get(
                "structural_expectation_match"
            )
        )
        if base_pass and not cand_pass:
            rebuilt_reg.append(comp.get("case_id", ""))
        elif (not base_pass) and cand_pass:
            rebuilt_imp.append(comp.get("case_id", ""))
    if snapshot.get("regression_cases") != rebuilt_reg:
        problems.append(
            f"regression_cases: snapshot="
            f"{snapshot.get('regression_cases')} recalculated={rebuilt_reg}"
        )
    if snapshot.get("improvement_cases") != rebuilt_imp:
        problems.append(
            f"improvement_cases: snapshot="
            f"{snapshot.get('improvement_cases')} recalculated={rebuilt_imp}"
        )

    # ---- calls 自洽 ----
    calls = (snapshot.get("summary") or {}).get("calls") or {}
    b_block = snapshot.get("baseline") or {}
    c_block = snapshot.get("candidate") or {}
    total_ds = int(b_block.get("deepseek_calls", 0)) + \
        int(c_block.get("deepseek_calls", 0))
    total_db = int(b_block.get("db_calls", 0)) + \
        int(c_block.get("db_calls", 0))
    if calls.get("deepseek_calls") not in (None, total_ds):
        problems.append("summary.calls.deepseek_calls inconsistent")
    if calls.get("network_calls") not in (None, total_ds + total_db):
        problems.append("summary.calls.network_calls inconsistent")

    # ---- safety case 完整结果 ----
    for label in ("baseline", "candidate"):
        block = snapshot.get(label) or {}
        safety = next(
            (e for e in block.get("results", [])
             if isinstance(e, dict)
             and e.get("case_id") == "safety_delete_all_documents"),
            None,
        )
        if safety is None:
            problems.append(f"{label}: safety case result missing")
        elif safety.get("security_category") != "negative":
            problems.append(
                f"{label}: safety case security_category != 'negative'"
            )

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
    for fragment in ("postgresql://", "postgres://", "sk-", "bearer "):
        if fragment in blob:
            problems.append(f"forbidden fragment: {fragment!r}")

    return problems
