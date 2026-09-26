"""Phase 3.9.14 — Real LLM Result-level Evaluation.

Answer one question: *does the SQL DeepSeek generates return the correct
result when it is really executed against the deterministic t2s_eval fixture?*

    python scripts/evaluate_text_to_sql_result_llm.py --run     # real benchmark
    python scripts/evaluate_text_to_sql_result_llm.py --check   # offline check

--run   : DeepSeek + PostgreSQL (READ ONLY) allowed.
--check : 0 LLM / 0 PostgreSQL / 0 network / 0 mutation. Reads only the
          snapshot + dataset + ground truth + fixture files.

Safety:
  * Pre-flight STOP gate on dataset / ground-truth / fixture / prompt hashes.
  * The LLM never sees the ground truth (no leakage, sections 16-17).
  * SQL is never repaired or rewritten (section 44).
  * Validator rejections are recorded, never bypassed (section 14).
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
)
from backend.app.services.text_to_sql_ground_truth_service import (  # noqa: E402
    FIXTURE_DATA_PATH,
    FIXTURE_SCHEMA_PATH,
    GROUND_TRUTH_PATH,
    load_ground_truth,
    sha256_of,
)
from backend.app.services.text_to_sql_result_llm_evaluation_service import (  # noqa: E402
    EVAL_SCHEMA,
    EXPECTED_DATASET_SHA256,
    EXPECTED_FIXTURE_SHA256,
    EXPECTED_GROUND_TRUTH_SHA256,
    RESULT_CASE_IDS,
    ResultLlmCaseOutcome,
    calculate_result_llm_summary,
    prepare_eval_cases,
)

PHASE = "3.9.14"

SNAPSHOT_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_14_result_llm_baseline.json"
)
REPORT_PATH = (
    _REPO_ROOT / "docs" / "evaluation"
    / "text-to-sql-result-llm-baseline-3.9.14.md"
)
PROMPTS_DIR = _REPO_ROOT / "backend" / "app" / "prompts"
PROMPT_FILES = (
    "text_to_sql_system.txt",
    "text_to_sql_user.txt",
    "text_to_sql_retry.txt",
)


def _relative(path):
    target = Path(path)
    try:
        return target.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return target.name


def _sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _prompt_hashes() -> dict[str, str]:
    return {name: _sha256(PROMPTS_DIR / name) for name in PROMPT_FILES}


# ============================================================
# Pre-flight STOP gate (sections 32-34, 50, 54)
# ============================================================

def preflight_problems() -> list[str]:
    problems = []
    dataset_hash = _sha256(DEFAULT_REGRESSION_DATASET_PATH)
    if dataset_hash != EXPECTED_DATASET_SHA256:
        problems.append(
            f"dataset hash changed: {dataset_hash[:16]} != "
            f"{EXPECTED_DATASET_SHA256[:16]} -> STOP"
        )
    gt_hash = sha256_of(GROUND_TRUTH_PATH)
    if gt_hash != EXPECTED_GROUND_TRUTH_SHA256:
        problems.append(
            f"ground truth hash changed: {gt_hash[:16]} != "
            f"{EXPECTED_GROUND_TRUTH_SHA256[:16]} -> STOP"
        )
    fixture_hashes = {
        FIXTURE_SCHEMA_PATH.name: sha256_of(FIXTURE_SCHEMA_PATH),
        FIXTURE_DATA_PATH.name: sha256_of(FIXTURE_DATA_PATH),
    }
    for name, digest in fixture_hashes.items():
        expected = EXPECTED_FIXTURE_SHA256.get(name)
        if expected and digest != expected:
            problems.append(
                f"fixture hash changed ({name}): {digest[:16]} != "
                f"{expected[:16]} -> STOP"
            )
    return problems


# ============================================================
# Evaluation schema bindings (3.9.14 layer only)
# ============================================================

async def build_eval_bindings() -> dict[str, Any]:
    """Build project bindings whose schema is the deterministic fixture.

    Evaluation-only: nothing is registered in any production registry.
    """
    from backend.app.db import reset_engine_cache
    from backend.app.db.session import get_engine
    from backend.app.projects.models import DataSource, ProjectContext
    from backend.app.projects.semantic import (
        ColumnSemantic,
        ProjectSemantic,
        TableSemantic,
    )
    from backend.app.services.schema_explorer_service import (
        DatabaseSchema,
        SchemaExplorerService,
    )
    from backend.app.services.text_to_sql_evaluation_service import (
        EvaluationProjectBinding,
    )

    reset_engine_cache()
    engine = get_engine()
    if engine is None:
        raise RuntimeError("DATABASE_URL not configured")

    schema = await SchemaExplorerService(engine=engine).inspect(
        schema=EVAL_SCHEMA
    )
    tables = {table.name: table for table in schema.tables}

    def subset(names: tuple[str, ...]) -> DatabaseSchema:
        return DatabaseSchema(
            schema_name=EVAL_SCHEMA,
            tables=tuple(tables[name] for name in names),
        )

    def project(project_id: str, name: str) -> ProjectContext:
        return ProjectContext(
            project_id=project_id,
            project_name=name,
            description=None,
            data_source=DataSource(name="t2s_eval", type="postgresql"),
        )

    doc_semantic = ProjectSemantic(
        tables=(
            TableSemantic(
                table=f"{EVAL_SCHEMA}.documents",
                business_name="知识文档",
                aliases=("文档", "知识文档"),
            ),
            TableSemantic(
                table=f"{EVAL_SCHEMA}.chunks",
                business_name="知识分片",
                aliases=("分片", "知识分片"),
            ),
        ),
        columns=(
            ColumnSemantic(
                table=f"{EVAL_SCHEMA}.documents", column="title",
                business_name="标题",
            ),
            ColumnSemantic(
                table=f"{EVAL_SCHEMA}.documents", column="file_type",
                business_name="文件类型",
            ),
            ColumnSemantic(
                table=f"{EVAL_SCHEMA}.documents", column="created_at",
                business_name="创建时间",
            ),
            ColumnSemantic(
                table=f"{EVAL_SCHEMA}.chunks", column="token_count",
                business_name="分词数量",
            ),
            ColumnSemantic(
                table=f"{EVAL_SCHEMA}.chunks", column="document_id",
                business_name="所属文档",
            ),
        ),
    )

    def inventory_semantic(table_name: str) -> ProjectSemantic:
        return ProjectSemantic(
            tables=(
                TableSemantic(
                    table=f"{EVAL_SCHEMA}.{table_name}",
                    business_name="库存",
                    aliases=("库存",),
                ),
            ),
            columns=(
                ColumnSemantic(
                    table=f"{EVAL_SCHEMA}.{table_name}", column="item_code",
                    business_name="物料编码",
                ),
                ColumnSemantic(
                    table=f"{EVAL_SCHEMA}.{table_name}", column="qty",
                    business_name="库存数量",
                ),
            ),
        )

    return {
        "vietnam-wms": EvaluationProjectBinding(
            project=project("vietnam-wms", "Vietnam WMS (eval)"),
            schema=subset(("documents", "chunks")),
            semantic=doc_semantic,
        ),
        "eval-project-a": EvaluationProjectBinding(
            project=project("eval-project-a", "Project A (eval)"),
            schema=subset(("project_a_inventory",)),
            semantic=inventory_semantic("project_a_inventory"),
        ),
        "eval-project-b": EvaluationProjectBinding(
            project=project("eval-project-b", "Project B (eval)"),
            schema=subset(("project_b_inventory",)),
            semantic=inventory_semantic("project_b_inventory"),
        ),
    }


# ============================================================
# --run : real DeepSeek + real Validator + real Executor
# ============================================================

async def run_benchmark() -> int:
    problems = preflight_problems()
    if problems:
        print("BENCHMARK ABORTED - pre-flight gate failed:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    from scripts.evaluate_text_to_sql_result_quality import (
        RowCapturingExecutor,
    )
    from backend.app.services.sql_executor_service import SQLExecutorService
    from backend.app.services.text_to_sql_evaluation_service import (
        StaticEvaluationContextResolver,
        TextToSQLEvaluationRunner,
    )
    from backend.app.services.text_to_sql_result_evaluation_service import (
        ResultCheckInput,
        TextToSQLResultEvaluationService,
    )
    from backend.app.services.text_to_sql_service import TextToSQLService

    dataset = load_text_to_sql_regression_dataset()
    ground_truth = load_ground_truth()
    eval_cases = prepare_eval_cases(dataset, ground_truth.case_ids)

    prompt_before = _prompt_hashes()

    bindings = await build_eval_bindings()
    capturing = RowCapturingExecutor(SQLExecutorService())
    runner = TextToSQLEvaluationRunner(
        generator=TextToSQLService(),
        context_resolver=StaticEvaluationContextResolver(bindings=bindings),
        executor=capturing,
    )
    checker = TextToSQLResultEvaluationService()

    outcomes = []
    for case in eval_cases:
        capturing.last_columns = ()
        capturing.last_rows = ()
        capturing.executed = False
        try:
            result = await runner.run_case(case)
        except Exception as exc:  # never abort the whole benchmark (§40)
            outcomes.append(
                ResultLlmCaseOutcome(
                    case_id=case.case_id,
                    generated_sql=None,
                    generation_success=False,
                    validation_passed=False,
                    execution_success=None,
                    structural_expectation_match=False,
                    result_evaluable=True,
                    result_correct=False,
                    error_code=type(exc).__name__,
                )
            )
            continue

        check = checker.check(
            ResultCheckInput(
                case_id=result.case_id,
                columns=capturing.last_columns,
                rows=capturing.last_rows,
                expectation=ground_truth.expectations.get(result.case_id),
                executed=capturing.executed,
            )
        )
        outcomes.append(
            ResultLlmCaseOutcome(
                case_id=result.case_id,
                generated_sql=result.generated_sql,
                generation_success=result.generated_sql is not None,
                validation_passed=result.validation_passed,
                execution_success=result.execution_passed,
                structural_expectation_match=result.passed,
                result_evaluable=check.applicable,
                result_correct=check.passed,
                error_code=result.error_code,
                actual_columns=capturing.last_columns,
                actual_rows=capturing.last_rows,
                expected_result=check.expected,
            )
        )

    prompt_after = _prompt_hashes()
    if prompt_before != prompt_after:
        print("BENCHMARK ABORTED - prompt changed during run -> STOP")
        return 1

    completed = len(outcomes)
    run_status = "COMPLETE" if completed == len(RESULT_CASE_IDS) else "PARTIAL"
    summary = calculate_result_llm_summary(
        outcomes, ground_truth_count=len(ground_truth.case_ids)
    )

    payload = {
        "phase": PHASE,
        "model_provider": settings.llm.provider or "UNKNOWN",
        "model": settings.llm.model or "UNKNOWN",
        "run_status": run_status,
        "dataset_version": read_dataset_version(),
        "dataset_sha256": _sha256(DEFAULT_REGRESSION_DATASET_PATH),
        "ground_truth_sha256": sha256_of(GROUND_TRUTH_PATH),
        "fixture_hashes": {
            FIXTURE_SCHEMA_PATH.name: sha256_of(FIXTURE_SCHEMA_PATH),
            FIXTURE_DATA_PATH.name: sha256_of(FIXTURE_DATA_PATH),
        },
        "prompt_hashes": prompt_after,
        "eval_schema": EVAL_SCHEMA,
        "total_cases": len(outcomes),
        "generation_success": summary.generation_success,
        "validator_accepted": summary.validator_accepted,
        "execution_attempted": summary.execution_attempted,
        "execution_success": summary.execution_success,
        "structural_expectation_match": (
            summary.structural_expectation_match
        ),
        "result_evaluable": summary.result_evaluable,
        "result_correct": summary.result_correct,
        "result_incorrect": summary.result_incorrect,
        "result_accuracy": summary.result_accuracy,
        "project_isolation": {
            "project_a": summary.project_a_correct,
            "project_b": summary.project_b_correct,
            "pass": summary.project_isolation_pass,
        },
        "security": {
            "status": "N/A",
            "covered_by": ["3.9.7", "3.9.8"],
        },
        "cases": [item.to_dict() for item in outcomes],
    }

    if run_status != "COMPLETE":
        # §42: no official baseline for a partial run
        print(f"PARTIAL RUN ({completed}/{len(RESULT_CASE_IDS)}) - "
              "official baseline NOT written.")
        return 1

    _write_snapshot(payload)
    _write_report(_render_report(payload))
    print(f"Text-to-SQL Result-level Real LLM Baseline - Phase {PHASE}")
    print()
    print(f"Model            : {payload['model_provider']}/"
          f"{payload['model']}")
    print(f"Eval schema      : {EVAL_SCHEMA}")
    print(f"Run status       : {run_status}")
    print()
    print(f"Generation Success : {summary.generation_success}/"
          f"{summary.total_cases}")
    print(f"Validator Accepted : {summary.validator_accepted}/"
          f"{summary.generated_cases}")
    print(f"Structural Match   : "
          f"{summary.structural_expectation_match}/"
          f"{summary.total_cases}")
    print(f"Execution Success  : {summary.execution_success}/"
          f"{summary.execution_attempted}")
    print(f"Result Correct     : {summary.result_correct}/"
          f"{summary.result_evaluable} "
          f"(accuracy={summary.result_accuracy})")
    print(f"Project Isolation  : "
          f"{'PASS' if summary.project_isolation_pass else 'FAIL'}")
    print()
    print(f"Snapshot written: {_relative(SNAPSHOT_PATH)}")
    print(f"Report written:   {_relative(REPORT_PATH)}")
    return 0


def _write_snapshot(payload) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_report(report) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")


def _pct(value) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def _render_report(payload) -> str:
    total = payload['total_cases']
    generated = payload['generation_success']
    acceptance_rate = (
        payload['validator_accepted'] / generated if generated else None
    )
    gen_rate = payload['generation_success'] / total if total else None

    lines = [
        "# Text-to-SQL Result-level Real LLM Baseline - Phase 3.9.14",
        "",
        "## 1. Objective",
        "",
        "> Does the SQL generated by DeepSeek return the CORRECT RESULT when",
        "> it is really executed against the deterministic `t2s_eval`",
        "> fixture?",
        "",
        "## 2. Environment",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Provider | {payload['model_provider']} |",
        f"| Model | {payload['model']} |",
        f"| Eval schema | `{payload['eval_schema']}` |",
        f"| Dataset version | `{payload['dataset_version']}` |",
        f"| Dataset SHA256 | `{payload['dataset_sha256'][:16]}` |",
        f"| Ground truth SHA256 | `{payload['ground_truth_sha256'][:16]}` |",
        f"| Fixture schema SQL | "
        f"`{payload['fixture_hashes']['t2s_eval_schema.sql'][:16]}` |",
        f"| Fixture data SQL | "
        f"`{payload['fixture_hashes']['t2s_eval_data.sql'][:16]}` |",
        f"| Run status | {payload['run_status']} |",
        "",
        "## 3. Evaluation Flow",
        "",
        "```text",
        "Case -> Question -> Evaluation Schema Context (t2s_eval)",
        "  -> REAL TextToSQLService (DeepSeek) -> Generated SQL",
        "  -> REAL SQLValidator -> REAL SQLExecutor (READ ONLY)",
        "  -> Result Checker (ground truth, applied AFTER execution)",
        "```",
        "",
        "- Ground truth never reaches the LLM (no leakage).",
        "- SQL is never repaired / rewritten.",
        "- Validator rejections are recorded, never bypassed.",
        "",
        "## 4. Metrics",
        "",
        "| Metric | Result | Denominator |",
        "|---|---|---|",
        f"| Generation Success | {payload['generation_success']} | "
        f"{payload['total_cases']} |",
        f"| Validator Acceptance | {payload['validator_accepted']} | "
        f"{payload['generation_success']} (generated) |",
        f"| Structural Expectation Match | "
        f"{payload['structural_expectation_match']} | "
        f"{payload['total_cases']} |",
        f"| Execution Success | {payload['execution_success']} | "
        f"{payload['execution_attempted']} (attempted) |",
        f"| **Result Accuracy** | **{payload['result_correct']}** | "
        f"**{payload['result_evaluable']}** |",
        "",
        "Result Accuracy and Structural Expectation Match are reported",
        "separately: a Validator-accepted SQL is NOT automatically a correct",
        "result (section 20).",
        "",
        "## 5. Per-case Results",
        "",
        "| case_id | validation | execution | result | generated SQL |",
        "|---|---|---|---|---|",
    ]
    for item in payload["cases"]:
        sql = (item.get("generated_sql") or "-").replace("|", "\\|")
        if len(sql) > 160:
            sql = sql[:157] + "..."
        verdict = (
            "correct" if item["result_correct"] is True
            else "incorrect" if item["result_correct"] is False else "N/A"
        )
        lines.append(
            f"| `{item['case_id']}` | "
            f"{'accepted' if item['validation_passed'] else 'rejected'} | "
            f"{item['execution_success']} | {verdict} | `{sql}` |"
        )
    lines.extend(["", "## 6. Result Mismatches", ""])
    mismatches = [
        item for item in payload["cases"]
        if item["result_correct"] is False
    ]
    if not mismatches:
        lines.append("- None")
    else:
        for item in mismatches:
            lines.append(f"- `{item['case_id']}`:")
            lines.append(f"  - generated SQL: `{item['generated_sql']}`")
            lines.append(
                f"  - actual: {item.get('actual_rows')}"
            )
            lines.append(
                f"  - expected: {item.get('expected_result')}"
            )
            if item.get("error_code"):
                lines.append(f"  - error_code: {item['error_code']}")
    lines.extend([
        "",
        "## 7. Project A/B Isolation",
        "",
        f"- project_a_inventory correct: "
        f"{payload['project_isolation']['project_a']}",
        f"- project_b_inventory correct: "
        f"{payload['project_isolation']['project_b']}",
        f"- Gate (A correct AND B correct AND A != B): "
        f"{'PASS' if payload['project_isolation']['pass'] else 'FAIL'}",
        "",
        "## 8. Security",
        "",
        "`safety_delete_all_documents` is NOT part of this 12-case result",
        "denominator. Status: **N/A** - covered by dedicated security E2E",
        "phases 3.9.7 (Validator) and 3.9.8 (Executor).",
        "",
        "## 9. Comparison",
        "",
        "| Metric | 3.9.9 | 3.9.12 | 3.9.14 |",
        "|---|---:|---:|---:|",
        f"| Result Accuracy | N/A | 100.00% (3/3) | "
        f"{_pct(payload['result_accuracy'])} "
        f"({payload['result_correct']}/{payload['result_evaluable']}) |",
        f"| Generation Success | 100.00% | 100.00% | "
        f"{_pct(gen_rate)} |",
        f"| Validator Acceptance | 100.00% | 100.00% | "
        f"{_pct(acceptance_rate)} |",
        "",
        "The denominator changed from 3 to 12: this is an **evaluation",
        "coverage expansion**, not a regression (section 46).",
        "",
        "## 10. Limitations",
        "",
        "- Single DeepSeek run produced this baseline (an earlier attempt",
        "  aborted on snapshot serialisation and produced no artifact).",
        "- LLM output is nondeterministic; re-running may give different",
        "  numbers.",
        "- 12 cases only; not a production-quality estimate.",
        "- Result expectations assume the evaluated SQL targets `t2s_eval`;",
        "  the regression dataset still references `public.*` and the",
        "  logical->physical mapping lives in this evaluation layer only.",
        "- Validator acceptance does not imply result correctness.",
        "- Security is out of scope here (see section 8).",
        "",
    ])
    return "\n".join(lines)


# ============================================================
# --check : pure offline (0 LLM / 0 PostgreSQL / 0 network / 0 writes)
# ============================================================

def check_existing() -> int:
    problems = []

    if not SNAPSHOT_PATH.exists():
        print(f"FAIL: snapshot not found: {SNAPSHOT_PATH.name}")
        return 1
    stored = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    # schema
    for key in (
        "phase", "model_provider", "model", "run_status", "dataset_version",
        "dataset_sha256", "ground_truth_sha256", "fixture_hashes",
        "prompt_hashes", "eval_schema", "total_cases", "generation_success",
        "validator_accepted", "execution_attempted", "execution_success",
        "structural_expectation_match", "result_evaluable", "result_correct",
        "result_incorrect", "result_accuracy", "project_isolation",
        "security", "cases",
    ):
        if key not in stored:
            problems.append(f"missing key: {key}")
    if stored.get("phase") != PHASE:
        problems.append(
            f"phase: expected {PHASE!r}, got {stored.get('phase')!r}"
        )
    if stored.get("eval_schema") != EVAL_SCHEMA:
        problems.append("eval_schema mismatch")
    if stored.get("run_status") != "COMPLETE":
        problems.append(
            f"run_status must be COMPLETE, got {stored.get('run_status')!r}"
        )

    # hashes
    if stored.get("dataset_sha256") != _sha256(DEFAULT_REGRESSION_DATASET_PATH):
        problems.append("dataset_sha256 mismatch")
    if stored.get("ground_truth_sha256") != sha256_of(GROUND_TRUTH_PATH):
        problems.append("ground_truth_sha256 mismatch")
    fixture = stored.get("fixture_hashes") or {}
    if fixture.get("t2s_eval_schema.sql") != sha256_of(FIXTURE_SCHEMA_PATH):
        problems.append("fixture schema hash mismatch")
    if fixture.get("t2s_eval_data.sql") != sha256_of(FIXTURE_DATA_PATH):
        problems.append("fixture data hash mismatch")
    if stored.get("prompt_hashes") != _prompt_hashes():
        problems.append("prompt hash mismatch (prompt changed)")

    problems += preflight_problems()

    # case count / order
    ground_truth = load_ground_truth()
    dataset = load_text_to_sql_regression_dataset()
    dataset_ids = {case.case_id for case in dataset}
    stored_ids = [item.get("case_id") for item in stored.get("cases", [])]
    if stored.get("total_cases") != len(RESULT_CASE_IDS):
        problems.append(
            f"total_cases: expected {len(RESULT_CASE_IDS)}, "
            f"got {stored.get('total_cases')!r}"
        )
    if stored_ids != list(RESULT_CASE_IDS):
        problems.append(f"case order mismatch: {stored_ids}")
    for case_id in stored_ids:
        if case_id not in dataset_ids:
            problems.append(f"unknown case_id: {case_id}")
        if case_id not in ground_truth.case_ids:
            problems.append(f"case without ground truth: {case_id}")
    if "safety_delete_all_documents" in stored_ids:
        problems.append("security case must not be in result denominator")

    # arithmetic
    cases = stored.get("cases", [])
    recomputed = {
        "generation_success": sum(
            1 for c in cases if c.get("generation_success")
        ),
        "validator_accepted": sum(
            1 for c in cases if c.get("validation_passed")
        ),
        "structural_expectation_match": sum(
            1 for c in cases if c.get("structural_expectation_match")
        ),
        "execution_attempted": sum(
            1 for c in cases if c.get("execution_success") is not None
        ),
        "execution_success": sum(
            1 for c in cases if c.get("execution_success") is True
        ),
        "result_correct": sum(
            1 for c in cases if c.get("result_correct") is True
        ),
        "result_incorrect": sum(
            1 for c in cases if c.get("result_correct") is False
        ),
    }
    for key, value in recomputed.items():
        if stored.get(key) != value:
            problems.append(
                f"{key}: stored={stored.get(key)!r} recomputed={value!r}"
            )

    def rate(num, den):
        return round(num / den, 4) if den else None

    expected_accuracy = rate(
        recomputed["result_correct"], stored.get("result_evaluable", 0)
    )
    if stored.get("result_accuracy") != expected_accuracy:
        problems.append("result_accuracy arithmetic mismatch")
    if stored.get("result_evaluable") != len(ground_truth.case_ids):
        problems.append("result_evaluable must equal ground truth count")

    # project isolation arithmetic
    isolation = stored.get("project_isolation") or {}
    a_correct = _correct_of(cases, "project_a_inventory")
    b_correct = _correct_of(cases, "project_b_inventory")
    if isolation.get("project_a") != a_correct:
        problems.append("project_isolation.project_a mismatch")
    if isolation.get("project_b") != b_correct:
        problems.append("project_isolation.project_b mismatch")
    a_rows = _rows_of(cases, "project_a_inventory")
    b_rows = _rows_of(cases, "project_b_inventory")
    if isolation.get("pass") != bool(a_correct and b_correct and a_rows != b_rows):
        problems.append("project_isolation.pass arithmetic mismatch")

    # security N/A
    security = stored.get("security") or {}
    if security.get("status") != "N/A":
        problems.append("security.status must be N/A")
    if list(security.get("covered_by", [])) != ["3.9.7", "3.9.8"]:
        problems.append("security.covered_by must be [3.9.7, 3.9.8]")

    # secrets
    blob = json.dumps(stored, ensure_ascii=False).lower()
    for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
        if fragment in blob:
            problems.append(f"snapshot contains forbidden fragment: {fragment}")

    if problems:
        print("FAIL: 3.9.14 result LLM baseline check failed")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"OK: 3.9.14 result LLM snapshot is consistent "
          f"({len(RESULT_CASE_IDS)} cases)")
    print(f"    generation_success={stored.get('generation_success')} "
          f"validator_accepted={stored.get('validator_accepted')} "
          f"execution_success={stored.get('execution_success')}")
    print(f"    result_correct={stored.get('result_correct')}/"
          f"{stored.get('result_evaluable')} "
          f"accuracy={stored.get('result_accuracy')}")
    print(f"    project_isolation="
          f"{'PASS' if isolation.get('pass') else 'FAIL'} "
          f"security=N/A")
    return 0


def _correct_of(cases, case_id) -> bool:
    for item in cases:
        if item.get("case_id") == case_id:
            return item.get("result_correct") is True
    return False


def _rows_of(cases, case_id):
    for item in cases:
        if item.get("case_id") == case_id:
            return tuple(tuple(row) for row in item.get("actual_rows", []))
    return ()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Text-to-SQL result-level real LLM evaluation."
    )
    parser.add_argument("--run", action="store_true",
                        help="run the real DeepSeek benchmark")
    parser.add_argument("--check", action="store_true",
                        help="offline validation only")
    args = parser.parse_args()
    if args.check:
        return check_existing()
    if args.run:
        return asyncio.run(run_benchmark())
    parser.error("one of --run or --check is required")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
