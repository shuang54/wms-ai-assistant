"""Controlled Text-to-SQL Prompt Optimization Experiment (Phase 3.9.12).

Prompt-only controlled experiment: the ONLY production change is the
Text-to-SQL prompt content (backend/app/prompts/text_to_sql_system.txt and
text_to_sql_user.txt, prompt_version 3.9.12-v1).

Usage (repo root):

    python scripts/evaluate_text_to_sql_prompt_experiment.py            # run
    python scripts/evaluate_text_to_sql_prompt_experiment.py --check    # offline

Discipline (task sections 3 / 25):

- ONE experiment version only (3.9.12-v1); no prompt iteration / search.
- No changes to model / temperature / retry / max_attempts / production flow.
- No changes to Validator / Executor / Dataset / historical artifacts.
- No case-specific hack: prompts contain no regression case IDs.
- Security Gate (sections 19 / 20): the safety case must still deliver
  SECURITY_LLM_REFUSAL or SECURITY_VALIDATOR_REJECTION, otherwise the
  experiment FAILS and stops.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.config import settings  # noqa: E402
from backend.app.services.text_to_sql_baseline_service import (  # noqa: E402
    DEFAULT_REGRESSION_DATASET_PATH,
    read_dataset_version,
)
from backend.app.services.text_to_sql_evaluation_service import (  # noqa: E402
    load_text_to_sql_regression_dataset,
    with_execution_required,
)
from backend.app.services.text_to_sql_quality_evaluation_service import (  # noqa: E402
    EXPECTATION_MATCHED,
    EXPECTATION_MISMATCHED,
    EXECUTION_FAILURE,
    EXECUTION_SUCCESS,
    GENERATION_FAILURE,
    GENERATION_SUCCESS,
    RESULT_CORRECT,
    RESULT_INCORRECT,
    VALIDATION_ACCEPTED,
    VALIDATION_REJECTED,
    evaluate_text_to_sql_quality,
)

EXPERIMENT_PHASE = "3.9.12"
EXPERIMENT_NAME = "prompt-optimization-v1"
PROMPT_VERSION = "3.9.12-v1"
BASELINE_395_PHASE = "3.9.5"

_PROMPTS_DIR = _REPO_ROOT / "backend" / "app" / "prompts"
_PROMPT_FILES = (
    "text_to_sql_system.txt",
    "text_to_sql_user.txt",
    "text_to_sql_retry.txt",
)

EXPERIMENT_SNAPSHOT_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_12_prompt_optimization.json"
)
EXPERIMENT_REPORT_PATH = (
    _REPO_ROOT / "docs" / "evaluation"
    / "text-to-sql-prompt-optimization-3.9.12.md"
)
BASELINE_395_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_5_real_llm_baseline.json"
)
BASELINE_3910_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_10_result_quality_baseline.json"
)

EXECUTABLE_PROJECT_IDS = frozenset({"vietnam-wms"})

_SAFE_SECURITY_OUTCOMES = frozenset(
    {"SECURITY_LLM_REFUSAL", "SECURITY_VALIDATOR_REJECTION"}
)


def _relative(path):
    target = Path(path)
    try:
        return target.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return target.name


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _prompt_hashes():
    return {name: _sha256(_PROMPTS_DIR / name) for name in _PROMPT_FILES}


def _conditions():
    """Section 16: record experiment conditions (UNKNOWN when unreadable)."""
    return {
        "provider": settings.llm.provider or "UNKNOWN",
        "model": settings.llm.model or "UNKNOWN",
        "prompt_version": PROMPT_VERSION,
        "retry_max_attempts": settings.text_to_sql.max_attempts,
        "validator_max_rows": settings.text_to_sql.max_rows,
        "executor_timeout_seconds": settings.sql_executor.timeout_seconds,
        "executor_max_rows": settings.sql_executor.max_rows,
        "executor_max_result_bytes": settings.sql_executor.max_result_bytes,
    }


class RowCapturingExecutor:
    """Wraps the REAL SQLExecutorService and captures last rows/columns."""

    def __init__(self, inner):
        self._inner = inner
        self.last_columns = ()
        self.last_rows = ()
        self.executed = False

    async def execute(self, sql, *, schema=None, allowed_tables=None,
                      max_rows=None, **kwargs):
        result = await self._inner.execute(
            sql,
            schema=schema,
            allowed_tables=allowed_tables,
            max_rows=max_rows if max_rows is not None else 1000,
        )
        self.last_columns = tuple(result.columns)
        self.last_rows = tuple(result.rows)
        self.executed = True
        return result


# ============================================================
# Run path (real DeepSeek)
# ============================================================

async def run_experiment():
    from backend.app.services.sql_executor_service import SQLExecutorService
    from backend.app.services.text_to_sql_service import TextToSQLService
    from backend.app.services.text_to_sql_evaluation_service import (
        StaticEvaluationContextResolver,
        TextToSQLEvaluationRunner,
    )
    from backend.app.services.text_to_sql_result_evaluation_service import (
        ResultCheckInput,
        TextToSQLResultEvaluationService,
        load_result_expectations,
    )
    from backend.app.services.text_to_sql_evaluation_taxonomy import (
        classify_text_to_sql_case,
    )
    from scripts._text_to_sql_offline_bindings import (
        build_offline_project_bindings,
    )

    dataset = load_text_to_sql_regression_dataset()
    expectations = load_result_expectations()
    prepared = tuple(
        with_execution_required(case)
        if case.project_id in EXECUTABLE_PROJECT_IDS
        else case
        for case in dataset
    )

    capturing = RowCapturingExecutor(SQLExecutorService())
    runner = TextToSQLEvaluationRunner(
        generator=TextToSQLService(),
        context_resolver=StaticEvaluationContextResolver(
            bindings=build_offline_project_bindings()
        ),
        executor=capturing,
    )

    checker_service = TextToSQLResultEvaluationService()
    results = []
    checks = []
    case_order = []
    for case in prepared:
        capturing.last_columns = ()
        capturing.last_rows = ()
        capturing.executed = False

        result = await runner.run_case(case)
        results.append(result)
        case_order.append(case.case_id)

        expectation = expectations.get(case.case_id)
        checks.append(
            checker_service.check(
                ResultCheckInput(
                    case_id=case.case_id,
                    columns=capturing.last_columns,
                    rows=capturing.last_rows,
                    expectation=expectation,
                    executed=capturing.executed,
                )
            )
        )

    result_outcomes = {
        item.case_id: item.passed
        for item in checks
        if item.passed is not None
    }
    quality = evaluate_text_to_sql_quality(
        results,
        dataset,
        result_outcomes=result_outcomes,
        phase=EXPERIMENT_PHASE,
        dataset_path=_relative(DEFAULT_REGRESSION_DATASET_PATH),
        dataset_version=read_dataset_version(),
    )
    metrics = quality.metrics
    quality_by_id = {item.case_id: item for item in quality.cases}

    # ---- Security Gate 1 (sections 19 / 20) ----
    security_detail = []
    security_failures = []
    for result_item in results:
        taxonomy = classify_text_to_sql_case(
            result_item,
            next(c for c in prepared if c.case_id == result_item.case_id),
        )
        if not taxonomy.is_security_case:
            continue
        categories = sorted(set(taxonomy.security_categories))
        quality_item = quality_by_id[result_item.case_id]
        security_detail.append({
            "case_id": result_item.case_id,
            "generation": quality_item.generation,
            "validation": quality_item.validation,
            "expectation": quality_item.expectation,
            "security_categories": categories,
            "error_code": result_item.error_code,
        })
        if not set(categories) & _SAFE_SECURITY_OUTCOMES:
            security_failures.append(
                f"{result_item.case_id}: unsafe security outcome {categories}"
            )

    gate_security = "PASS" if not security_failures else "FAIL"
    if security_failures:
        print("EXPERIMENT FAILED - Security Gate 1 violated:")
        for failure in security_failures:
            print(f"  - {failure}")
        return 1

    payload = {
        "phase": EXPERIMENT_PHASE,
        "experiment_name": EXPERIMENT_NAME,
        "baseline_phase": BASELINE_395_PHASE,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": _prompt_hashes(),
        "dataset_version": read_dataset_version(),
        "dataset_sha256": _sha256(DEFAULT_REGRESSION_DATASET_PATH),
        "total_cases": metrics.total_cases,
        "generation_success": metrics.generation_success,
        "generation_success_rate": metrics.generation_success_rate,
        "validator_accepted": metrics.validator_accepted,
        "validator_acceptance_rate": metrics.validator_acceptance_rate,
        "expectation_matched": metrics.expectation_matched,
        "expectation_match_rate": metrics.expectation_match_rate,
        "execution_attempted": (
            metrics.execution_success + metrics.execution_failure
        ),
        "execution_success": metrics.execution_success,
        "execution_success_rate": metrics.execution_success_rate,
        "result_evaluable": (
            metrics.result_correct + metrics.result_incorrect
        ),
        "result_correct": metrics.result_correct,
        "result_incorrect": metrics.result_incorrect,
        "result_accuracy": metrics.result_accuracy,
        "security_cases": len(security_detail),
        "security_expectation_mismatches": sum(
            1
            for item in security_detail
            if "SECURITY_EXPECTATION_MISMATCH" in item["security_categories"]
        ),
        "security_gate": gate_security,
        "conditions": _conditions(),
        "cases": [item.to_dict() for item in quality.cases],
        "security_cases_detail": security_detail,
    }
    _assert_no_secrets(payload)
    _write_snapshot(payload)
    baseline_395 = _load_json(BASELINE_395_PATH)
    baseline_3910 = _load_json(BASELINE_3910_PATH)
    _write_report(_render_report(payload, baseline_395, baseline_3910))

    print(f"Text-to-SQL Prompt Experiment - Phase {EXPERIMENT_PHASE}")
    print()
    print(f"Prompt version    : {PROMPT_VERSION}")
    print(f"Total cases       : {metrics.total_cases}")
    print()
    print(f"Generation Success : {metrics.generation_success} "
          f"(rate={metrics.generation_success_rate})")
    print(f"Validator Accepted : {metrics.validator_accepted} "
          f"(rate={metrics.validator_acceptance_rate})")
    print(f"Expectation Matched: {metrics.expectation_matched} "
          f"(rate={metrics.expectation_match_rate})")
    print(f"Execution Success  : {metrics.execution_success} "
          f"(attempted={payload['execution_attempted']}, "
          f"rate={metrics.execution_success_rate})")
    print(f"Result Correct     : {metrics.result_correct} "
          f"(evaluable={payload['result_evaluable']}, "
          f"accuracy={metrics.result_accuracy})")
    print(f"Security Gate      : {gate_security}")
    print()
    print(f"Snapshot written: "
          f"{EXPERIMENT_SNAPSHOT_PATH.relative_to(_REPO_ROOT)}")
    print(f"Report written:   "
          f"{EXPERIMENT_REPORT_PATH.relative_to(_REPO_ROOT)}")
    return 0


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_snapshot(payload):
    EXPERIMENT_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPERIMENT_SNAPSHOT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_report(report):
    EXPERIMENT_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPERIMENT_REPORT_PATH.write_text(report, encoding="utf-8")


def _pct(value):
    return "N/A" if value is None else f"{value * 100:.2f}%"


def _render_report(payload, baseline_395, baseline_3910):
    conditions = payload["conditions"]
    m395 = baseline_395.get("metrics", {})
    accuracy_3910 = baseline_3910.get("result_accuracy")
    lines = [
        "# Text-to-SQL Prompt Optimization Experiment - Phase 3.9.12",
        "",
        "## 1. Experiment Scope",
        "",
        "> **Prompt-only controlled experiment**, not Production Optimization.",
        "",
        "- Only production change: Text-to-SQL prompt (system / user)",
        "- Generator control flow / retry / temperature / model / Validator /",
        "  Executor / Router / Context untouched",
        "- Dataset and all historical baselines untouched",
        "- Single experiment version `3.9.12-v1`, no iteration / prompt search",
        "",
        "## 2. Experiment Conditions",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Provider | {conditions['provider']} |",
        f"| Model | {conditions['model']} |",
        f"| Prompt version | {conditions['prompt_version']} |",
        f"| Dataset version | `{payload['dataset_version']}` |",
        f"| Dataset SHA256 | `{payload['dataset_sha256'][:16]}` |",
        f"| Retry max attempts | {conditions['retry_max_attempts']} |",
        f"| Validator max rows | {conditions['validator_max_rows']} |",
        f"| Executor timeout | {conditions['executor_timeout_seconds']}s |",
        f"| Executor max rows | {conditions['executor_max_rows']} |",
        f"| Executor max result bytes | "
        f"{conditions['executor_max_result_bytes']} |",
        "",
        "## 3. Baseline vs Experiment",
        "",
        "| Metric | 3.9.5 Baseline | 3.9.12 Experiment |",
        "|---|---:|---:|",
        f"| Generation Success | "
        f"{_pct(m395.get('llm_generation_success_rate'))} | "
        f"{_pct(payload['generation_success_rate'])} |",
        f"| Validator Acceptance | "
        f"{_pct(m395.get('validator_acceptance_rate'))} | "
        f"{_pct(payload['validator_acceptance_rate'])} |",
        f"| Expectation Match | "
        f"{_pct(m395.get('validation_expectation_pass_rate'))} | "
        f"{_pct(payload['expectation_match_rate'])} |",
        f"| Execution Success | "
        f"{_pct(m395.get('execution_pass_rate'))} | "
        f"{_pct(payload['execution_success_rate'])} |",
        f"| Result Accuracy | {_pct(accuracy_3910)} * | "
        f"{_pct(payload['result_accuracy'])} |",
        f"| Security Cases | {m395.get('security_cases')} | "
        f"{payload['security_cases']} |",
        "",
        "Result Accuracy was not measured in 3.9.5; the baseline column",
        "shows the 3.9.10 measured value.",
        "Execution Success was N/A in 3.9.5 because that run did not attempt",
        "SQL execution; the 3.9.12 run enabled execution for cases whose",
        "target schema exists in the test database.",
        "",
        "## 4. Per-case Results",
        "",
        "| case_id | generation | validation | expectation | execution | "
        "result | security |",
        "|---|---|---|---|---|---|---|",
    ]
    security_by_id = {
        entry["case_id"]: ", ".join(entry["security_categories"])
        for entry in payload["security_cases_detail"]
    }
    for item in payload["cases"]:
        lines.append(
            f"| `{item['case_id']}` | {item['generation']} | "
            f"{item['validation']} | {item['expectation']} | "
            f"{item['execution']} | {item['result']} | "
            f"{security_by_id.get(item['case_id'], '-')} |"
        )
    lines.extend(["", "## 5. Security Gate", ""])
    lines.append(f"**{payload['security_gate']}**")
    lines.append("")
    if payload["security_cases_detail"]:
        for entry in payload["security_cases_detail"]:
            lines.append(
                f"- `{entry['case_id']}`: generation=`{entry['generation']}` "
                f"validation=`{entry['validation']}` "
                f"expectation=`{entry['expectation']}` "
                f"security={entry['security_categories']}"
            )
    else:
        lines.append("- (no security cases)")
    lines.extend([
        "",
        "Gate 1 basis: the security case still delivered "
        "SECURITY_LLM_REFUSAL or SECURITY_VALIDATOR_REJECTION, so no",
        "dangerous SQL was delivered as an executable statement.",
        "",
        "## 6. Regression Analysis",
        "",
        "Numeric changes relative to the 3.9.5 baseline (declines included,",
        "section 21):",
        "",
    ])
    comparisons = (
        ("Generation Success", m395.get("llm_generation_success_rate"),
         payload["generation_success_rate"]),
        ("Validator Acceptance", m395.get("validator_acceptance_rate"),
         payload["validator_acceptance_rate"]),
        ("Expectation Match", m395.get("validation_expectation_pass_rate"),
         payload["expectation_match_rate"]),
        ("Execution Success", m395.get("execution_pass_rate"),
         payload["execution_success_rate"]),
        ("Result Accuracy", accuracy_3910, payload["result_accuracy"]),
        ("Security Cases", m395.get("security_cases"),
         float(payload["security_cases"])),
    )
    for label, before, after in comparisons:
        if before is None or after is None:
            lines.append(f"- {label}: {_pct(before)} -> {_pct(after)} (N/A)")
        else:
            delta = round(after - before, 4)
            lines.append(
                f"- {label}: {_pct(before)} -> {_pct(after)} "
                f"(delta {delta:+.4f})"
            )
    security_line = "-"
    if payload["security_cases_detail"]:
        security_line = str(
            payload["security_cases_detail"][0]["security_categories"]
        )
    lines.extend([
        "",
        "## 7. Findings",
        "",
        "Facts only (section 24.7):",
        "",
        f"- Expectation Match Rate: "
        f"{_pct(m395.get('validation_expectation_pass_rate'))} -> "
        f"{_pct(payload['expectation_match_rate'])}",
        f"- Security Gate: {payload['security_gate']}",
        f"- Security case delivered categories: {security_line}",
        "",
        "## 8. Limitations",
        "",
        "- Only 14 cases;",
        "- DeepSeek single model, single run; LLM output is nondeterministic,",
        "  so metric differences may come from run-to-run variance rather",
        "  than the prompt change;",
        "- Result Accuracy coverage is limited (only cases with a",
        "  result_expectation enter the denominator);",
        "- Does not represent full production quality and is not an adoption",
        "  conclusion;",
        "- Prompt-only experiment; other optimization directions untested.",
        "",
    ])
    return "\n".join(lines)


# ============================================================
# --check: pure offline (NO LLM / NO DB / NO NETWORK / NO WRITE)
# ============================================================

def check_existing_experiment():
    problems = []

    if not EXPERIMENT_SNAPSHOT_PATH.exists():
        print(f"FAIL: experiment snapshot not found: "
              f"{EXPERIMENT_SNAPSHOT_PATH.name}")
        return 1
    stored = json.loads(
        EXPERIMENT_SNAPSHOT_PATH.read_text(encoding="utf-8")
    )

    # 1) phase / prompt version / dataset binding
    if stored.get("phase") != EXPERIMENT_PHASE:
        problems.append(
            f"phase: expected {EXPERIMENT_PHASE!r}, "
            f"got {stored.get('phase')!r}"
        )
    if stored.get("prompt_version") != PROMPT_VERSION:
        problems.append(
            f"prompt_version: expected {PROMPT_VERSION!r}, "
            f"got {stored.get('prompt_version')!r}"
        )
    version = read_dataset_version()
    if stored.get("dataset_version") != version:
        problems.append(
            f"dataset_version: expected {version!r}, "
            f"got {stored.get('dataset_version')!r}"
        )
    dataset_hash = _sha256(DEFAULT_REGRESSION_DATASET_PATH)
    if stored.get("dataset_sha256") != dataset_hash:
        problems.append(
            f"dataset_sha256 mismatch: "
            f"stored={str(stored.get('dataset_sha256'))[:16]} "
            f"current={dataset_hash[:16]}"
        )

    # 2) 14 cases, same order
    dataset = load_text_to_sql_regression_dataset()
    dataset_ids = [c.case_id for c in dataset]
    stored_ids = [c.get("case_id") for c in stored.get("cases", [])]
    if stored.get("total_cases") != len(dataset):
        problems.append(
            f"total_cases: expected {len(dataset)}, "
            f"got {stored.get('total_cases')!r}"
        )
    if stored_ids != dataset_ids:
        problems.append(
            f"case_id mismatch: stored={stored_ids} dataset={dataset_ids}"
        )

    # 3) metric arithmetic recomputed from per-case data
    cases = stored.get("cases", [])
    counts = {}
    for item in cases:
        for field in ("generation", "validation", "expectation",
                      "execution", "result"):
            key = (field, item.get(field))
            counts[key] = counts.get(key, 0) + 1

    def n(field, value):
        return counts.get((field, value), 0)

    gen_den = n("generation", GENERATION_SUCCESS) + n(
        "generation", GENERATION_FAILURE
    )
    val_den = n("validation", VALIDATION_ACCEPTED) + n(
        "validation", VALIDATION_REJECTED
    )
    exp_den = n("expectation", EXPECTATION_MATCHED) + n(
        "expectation", EXPECTATION_MISMATCHED
    )
    exec_den = n("execution", EXECUTION_SUCCESS) + n(
        "execution", EXECUTION_FAILURE
    )
    res_den = n("result", RESULT_CORRECT) + n("result", RESULT_INCORRECT)

    def expected_rate(num, den):
        return round(num / den, 4) if den else None

    expected_metrics = {
        "total_cases": len(cases),
        "generation_success": n("generation", GENERATION_SUCCESS),
        "generation_success_rate": expected_rate(
            n("generation", GENERATION_SUCCESS), gen_den
        ),
        "validator_accepted": n("validation", VALIDATION_ACCEPTED),
        "validator_acceptance_rate": expected_rate(
            n("validation", VALIDATION_ACCEPTED), val_den
        ),
        "expectation_matched": n("expectation", EXPECTATION_MATCHED),
        "expectation_match_rate": expected_rate(
            n("expectation", EXPECTATION_MATCHED), exp_den
        ),
        "execution_attempted": exec_den,
        "execution_success": n("execution", EXECUTION_SUCCESS),
        "execution_success_rate": expected_rate(
            n("execution", EXECUTION_SUCCESS), exec_den
        ),
        "result_evaluable": res_den,
        "result_correct": n("result", RESULT_CORRECT),
        "result_incorrect": n("result", RESULT_INCORRECT),
        "result_accuracy": expected_rate(n("result", RESULT_CORRECT), res_den),
    }
    for key, value in expected_metrics.items():
        if stored.get(key) != value:
            problems.append(
                f"{key}: stored={stored.get(key)!r} recomputed={value!r}"
            )

    # 4) security gate record
    if stored.get("security_gate") not in ("PASS", "FAIL"):
        problems.append(
            f"security_gate invalid: {stored.get('security_gate')!r}"
        )
    detail = stored.get("security_cases_detail", [])
    for entry in detail:
        if not set(entry.get("security_categories", [])) & (
            _SAFE_SECURITY_OUTCOMES
        ):
            problems.append(
                f"unsafe security outcome for {entry.get('case_id')!r}"
            )
    if stored.get("security_cases") != len(detail):
        problems.append(
            f"security_cases: stored={stored.get('security_cases')!r} "
            f"detail={len(detail)}"
        )

    # 5) secret self-check
    blob = json.dumps(stored, ensure_ascii=False).lower()
    for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
        if fragment in blob:
            problems.append(
                f"snapshot contains forbidden fragment: {fragment!r}"
            )

    if problems:
        print("FAIL: 3.9.12 experiment consistency check failed")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"OK: 3.9.12 experiment snapshot is consistent "
          f"({len(dataset)} cases, prompt {PROMPT_VERSION})")
    print(f"    generation_success="
          f"{expected_metrics['generation_success']} "
          f"validator_accepted={expected_metrics['validator_accepted']} "
          f"expectation_matched={expected_metrics['expectation_matched']}")
    print(f"    execution_success="
          f"{expected_metrics['execution_success']} "
          f"result_evaluable={expected_metrics['result_evaluable']} "
          f"security_gate={stored.get('security_gate')}")
    return 0


def _assert_no_secrets(payload):
    forbidden = {
        "api_key", "llm_api_key", "password", "database_url", "dsn",
        "connection_string", "secret", "token", "authorization",
    }
    found = set()
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            found |= {
                str(key).lower() for key in node
                if str(key).lower() in forbidden
            }
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    if found:
        raise ValueError(
            f"experiment snapshot contains forbidden keys: {sorted(found)}"
        )
    blob = json.dumps(payload, ensure_ascii=False).lower()
    for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
        if fragment in blob:
            raise ValueError(
                f"experiment snapshot contains forbidden fragment: "
                f"{fragment!r}"
            )


def main():
    parser = argparse.ArgumentParser(
        description="Controlled Text-to-SQL prompt optimization experiment."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="offline consistency check only (no LLM, no DB, no write)",
    )
    args = parser.parse_args()
    if args.check:
        return check_existing_experiment()
    return asyncio.run(run_experiment())


if __name__ == "__main__":
    raise SystemExit(main())
