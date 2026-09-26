"""Phase 3.9.20 — Text-to-SQL Prompt A/B Experiment runner (CLI).

Pure experiment orchestration on top of the existing production pipeline.
Baseline and Candidate A use the **same** real Prompt this phase
(§五), so ``baseline_prompt_hash == candidate_prompt_hash``.

Usage
-----
    # First real experiment (Baseline v1 vs Candidate A v1):
    python scripts/run_text_to_sql_prompt_experiment.py --compare

    # Run a single variant and inspect:
    python scripts/run_text_to_sql_prompt_experiment.py --variant baseline
    python scripts/run_text_to_sql_prompt_experiment.py --variant candidate

    # Offline integrity check (0 DeepSeek / 0 DB / 0 network):
    python scripts/run_text_to_sql_prompt_experiment.py --check

Execution (DB) is opt-in, gated by ``RUN_DB_TESTS`` (the same gate used by
the existing DB tests). Without it, the run is structural-only (0 DB),
matching ``generate_text_to_sql_real_llm_baseline.py --compare``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.services.text_to_sql_prompt_experiment_service import (  # noqa: E402
    SNAPSHOT_3_9_20_PATH,
    REPORT_3_9_20_PATH,
    PromptExperimentRunner,
)
from backend.app.config import settings  # noqa: E402

_VARIANT_RESULT_PATHS = {
    "baseline": SNAPSHOT_3_9_20_PATH.with_name(
        "phase_3_9_20_baseline_results.json"
    ),
    "candidate": SNAPSHOT_3_9_20_PATH.with_name(
        "phase_3_9_20_candidate_results.json"
    ),
}


def _build_runner(variant: str | None = None) -> PromptExperimentRunner:
    """Compose the experiment runner from EXISTING services (§八)."""
    from backend.app.services.text_to_sql_service import TextToSQLService
    from backend.app.services.sql_validator_service import SQLValidatorService
    from backend.app.services.sql_executor_service import SQLExecutorService
    from scripts._text_to_sql_offline_bindings import (
        build_offline_project_bindings,
    )
    from backend.app.services.text_to_sql_evaluation_service import (
        StaticEvaluationContextResolver,
    )

    generator = TextToSQLService()
    bindings = build_offline_project_bindings()
    resolver = StaticEvaluationContextResolver(bindings=bindings)
    validator = SQLValidatorService()

    executor = None
    force_execution = False
    if os.environ.get("RUN_DB_TESTS"):
        # DB-mode: exercise the read-only executor (§十五).
        try:  # pragma: no cover - depends on DB availability
            executor = SQLExecutorService()
            force_execution = True
        except Exception as exc:  # pragma: no cover
            print(f"[warn] RUN_DB_TESTS set but executor unavailable: {exc}")
            executor = None

    return PromptExperimentRunner(
        generator=generator,
        context_resolver=resolver,
        validator=validator,
        executor=executor,
        force_execution=force_execution,
    )


def _verify_environment() -> None:
    """Pre-flight: ensure a real DeepSeek experiment can run (no secrets

    printed). Mirrors generate_text_to_sql_real_llm_baseline.py."""
    missing: list[str] = []
    if not getattr(settings.llm, "api_key", None):
        missing.append("LLM API key")
    if not getattr(settings.llm, "provider", None):
        missing.append("LLM provider")
    if not getattr(settings.llm, "model", None):
        missing.append("LLM model")
    if missing:
        print("Missing environment for a real experiment run:")
        for item in missing:
            print(f"  - {item}")
        print("Set the relevant variables, or run --check (offline).")
        raise SystemExit(2)


def _run_variant(variant: str) -> int:
    _verify_environment()
    runner = _build_runner(variant)
    result = asyncio.run(runner.run_variant(variant))
    path = _VARIANT_RESULT_PATHS[variant]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    m = result.metrics
    gen = f"{m.generation_success * 100:.1f}%" if m.generation_success is not None else "N/A"
    val = f"{m.validation_acceptance * 100:.1f}%" if m.validation_acceptance is not None else "N/A"
    struct = f"{m.structural_expectation_accuracy * 100:.1f}%" if m.structural_expectation_accuracy is not None else "N/A"
    sec = f"{m.security_pass * 100:.1f}%" if m.security_pass is not None else "N/A"
    print(f"Variant: {variant} (prompt {result.prompt_version}, "
          f"hash {result.prompt_hash[:16]}…)")
    print(f"  total_cases={result.total_cases} "
          f"deepseek_calls={result.deepseek_calls} db_calls={result.db_calls}")
    print(f"  metrics: generation_success={gen} validation_acceptance={val} "
          f"structural_expectation_accuracy={struct} security_pass={sec}")
    print(f"{variant} result written: {path}")
    return 0


def _run_compare() -> int:
    _verify_environment()
    runner = _build_runner()
    summary = asyncio.run(runner.compare())

    snapshot = summary.to_snapshot_dict()
    SNAPSHOT_3_9_20_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_3_9_20_PATH.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    REPORT_3_9_20_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_3_9_20_PATH.write_text(summary.render_report(), encoding="utf-8")

    print(summary.render_summary())
    print()
    if not summary.prompt_hash_equal:
        print("[FAIL] prompt hash mismatch -- Baseline and Candidate differ.")
    if summary.changed_case_ids:
        print(f"[WARN] {len(summary.changed_case_ids)} case(s) differ "
              f"(LLM non-determinism): {list(summary.changed_case_ids)}")
    print(f"Snapshot written: {SNAPSHOT_3_9_20_PATH}")
    print(f"Report written:   {REPORT_3_9_20_PATH}")
    return 0


def _run_check() -> int:
    """Offline snapshot integrity (0 LLM / 0 DB / 0 network)."""
    if not SNAPSHOT_3_9_20_PATH.exists():
        print("SKIP: 3.9.20 experiment snapshot not generated yet")
        return 0
    from backend.app.services.text_to_sql_prompt_experiment_service import (
        validate_experiment_snapshot,
    )
    snapshot = json.loads(SNAPSHOT_3_9_20_PATH.read_text(encoding="utf-8"))
    problems = validate_experiment_snapshot(snapshot)
    if problems:
        print("FAIL: 3.9.20 prompt experiment snapshot check failed")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("OK: 3.9.20 prompt experiment snapshot consistent "
          "(Baseline v1 == Candidate A v1, hashes aligned)")
    print(f"    infra_validation_pass = {snapshot.get('infrastructure_validation_pass')}")
    print(f"    changed_case_ids = {snapshot.get('changed_case_ids') or 'none'}")
    print(f"    deepseek/db/network = {snapshot.get('deepseek_calls')}/"
          f"{snapshot.get('db_calls')}/{snapshot.get('network_calls')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 3.9.20 Text-to-SQL Prompt A/B experiment."
    )
    parser.add_argument(
        "--variant",
        choices=["baseline", "candidate"],
        help="run only one variant and write its result file",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="run Baseline then Candidate, compare, write snapshot+report",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="offline snapshot integrity (no LLM/DB/network)",
    )
    args = parser.parse_args()

    if args.check:
        return _run_check()
    if args.variant:
        return _run_variant(args.variant)
    # default == --compare (first real experiment)
    return _run_compare()


if __name__ == "__main__":
    raise SystemExit(main())
