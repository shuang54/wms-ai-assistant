"""Phase 3.9.13 — setup / validate the Text-to-SQL deterministic fixture.

Two strictly separated modes:

    python scripts/setup_text_to_sql_ground_truth.py
        Load t2s_eval_schema.sql + t2s_eval_data.sql into the TEST database,
        verify row counts / FK, then write the ground-truth snapshot.

    python scripts/setup_text_to_sql_ground_truth.py --check
        Pure offline validation: read the snapshot + fixture SQL + ground
        truth + dataset, verify hashes, structure and arithmetic.
        0 LLM / 0 PostgreSQL / 0 network / 0 writes.

Discipline:
    * Fixture writes happen ONLY inside the dedicated `t2s_eval` schema.
    * Evaluation itself stays READ ONLY — data setup and evaluation are
      two separate steps (§21).
    * No DeepSeek call in either mode (§34).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.services.text_to_sql_baseline_service import (  # noqa: E402
    DEFAULT_REGRESSION_DATASET_PATH,
    read_dataset_version,
)
from backend.app.services.text_to_sql_evaluation_service import (  # noqa: E402
    load_text_to_sql_regression_dataset,
)
from backend.app.services.text_to_sql_ground_truth_service import (  # noqa: E402
    EVAL_SCHEMA,
    FIXTURE_DATA_PATH,
    FIXTURE_SCHEMA_PATH,
    GROUND_TRUTH_PATH,
    GROUND_TRUTH_VERSION,
    assert_fixture_targets_eval_schema,
    load_ground_truth,
    read_fixture_sql,
    sha256_of,
    validate_ground_truth,
)

PHASE = "3.9.13"

SNAPSHOT_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "text_to_sql" / "baselines"
    / "phase_3_9_13_ground_truth.json"
)
REPORT_PATH = (
    _REPO_ROOT / "docs" / "evaluation"
    / "text-to-sql-ground-truth-3.9.13.md"
)

#: expected row counts after setup (documents, chunks, project_a, project_b)
EXPECTED_ROWS = {
    "documents": 4,
    "chunks": 10,
    "project_a_inventory": 2,
    "project_b_inventory": 2,
}


def _relative(path):
    target = Path(path)
    try:
        return target.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return target.name


def _split_statements(sql_text):
    """Split SQL into statements, ignoring `--` line comments.

    Comments may contain semicolons / SQL keywords, so they must be
    removed before splitting.
    """
    cleaned = "\n".join(
        re.sub(r"--.*$", "", line) for line in sql_text.splitlines()
    )
    return [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]


def _fixture_problems() -> list[str]:
    """§30: every fixture statement must target the dedicated eval schema."""
    schema_sql, data_sql = read_fixture_sql()
    problems = assert_fixture_targets_eval_schema(schema_sql)
    problems += assert_fixture_targets_eval_schema(data_sql)
    return problems


# ============================================================
# Setup path (writes ONLY inside t2s_eval, on the TEST database)
# ============================================================

def run_setup() -> int:
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text as sa_text

    from backend.app.db import reset_engine_cache
    from backend.app.db.session import get_engine

    problems = _fixture_problems()
    if problems:
        print("FIXTURE REJECTED — statements outside the eval schema:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    reset_engine_cache()
    engine = get_engine()
    if engine is None:
        print("FAIL: DATABASE_URL not configured; cannot set up fixture.")
        return 1

    schema_sql, data_sql = read_fixture_sql()
    with engine.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        for statement in _split_statements(schema_sql):
            conn.execute(sa_text(statement))
        for statement in _split_statements(data_sql):
            conn.execute(sa_text(statement))

        row_counts: dict[str, int] = {}
        for table in EXPECTED_ROWS:
            value = conn.execute(
                sa_text(f"SELECT count(*) FROM {EVAL_SCHEMA}.{table}")
            ).scalar()
            row_counts[table] = int(value or 0)

        # verify the real FK exists (no fake JOIN)
        foreign_keys = sa_inspect(engine).get_foreign_keys(
            "chunks", schema=EVAL_SCHEMA
        )
        fk_ok = any(
            fk.get("referred_schema") == EVAL_SCHEMA
            and fk.get("referred_table") == "documents"
            and "document_id" in (fk.get("constrained_columns") or ())
            for fk in foreign_keys
        )

        # public business tables must stay untouched
        public_docs = conn.execute(
            sa_text("SELECT count(*) FROM knowledge_document")
        ).scalar()
        public_chunks = conn.execute(
            sa_text("SELECT count(*) FROM knowledge_chunk")
        ).scalar()

        # project isolation must be observable in the data
        qty_a = conn.execute(
            sa_text(
                f"SELECT qty FROM {EVAL_SCHEMA}.project_a_inventory "
                "WHERE item_code = 'item_x'"
            )
        ).scalar()
        qty_b = conn.execute(
            sa_text(
                f"SELECT qty FROM {EVAL_SCHEMA}.project_b_inventory "
                "WHERE item_code = 'item_x'"
            )
        ).scalar()

    mismatches = [
        f"{table}: expected {expected}, got {row_counts[table]}"
        for table, expected in EXPECTED_ROWS.items()
        if row_counts[table] != expected
    ]
    if not fk_ok:
        mismatches.append("FK chunks.document_id -> documents.id missing")
    if int(public_docs or 0) != 0 or int(public_chunks or 0) != 0:
        mismatches.append(
            f"public tables mutated: knowledge_document={public_docs}, "
            f"knowledge_chunk={public_chunks}"
        )
    if qty_a == qty_b:
        mismatches.append(
            f"project isolation not observable: item_x qty "
            f"project_a={qty_a} == project_b={qty_b}"
        )
    if mismatches:
        print("FIXTURE VERIFICATION FAILED:")
        for item in mismatches:
            print(f"  - {item}")
        return 1

    document = load_ground_truth()
    dataset = load_text_to_sql_regression_dataset()
    gt_problems = validate_ground_truth(document, dataset)
    if gt_problems:
        print("GROUND TRUTH INVALID:")
        for problem in gt_problems:
            print(f"  - {problem}")
        return 1

    payload = _build_payload(document, dataset, row_counts, bool(fk_ok))
    _write_snapshot(payload)
    _write_report(_render_report(payload))

    print(f"Text-to-SQL Ground Truth Fixture — Phase {PHASE}")
    print()
    print(f"Eval schema      : {EVAL_SCHEMA}")
    print(f"Ground truth ver : {document.version}")
    print(f"Row counts       : {row_counts}")
    print(f"FK verified      : {bool(fk_ok)}")
    print(f"Public untouched : knowledge_document={public_docs}, "
          f"knowledge_chunk={public_chunks}")
    print(f"Project isolation: item_x project_a={qty_a} vs "
          f"project_b={qty_b}")
    print()
    print(f"Total cases           : {payload['total_cases']}")
    print(f"Result-evaluable      : "
          f"{payload['result_evaluable_cases']}")
    print(f"Not evaluable (N/A)   : {payload['result_na_cases']}")
    print()
    print(f"Snapshot written: {_relative(SNAPSHOT_PATH)}")
    print(f"Report written:   {_relative(REPORT_PATH)}")
    return 0


def _build_payload(document, dataset, row_counts, fk_ok) -> dict[str, Any]:
    ground_truth_hash = sha256_of(GROUND_TRUTH_PATH)
    return {
        "phase": PHASE,
        "dataset_version": read_dataset_version(),
        "dataset_sha256": sha256_of(DEFAULT_REGRESSION_DATASET_PATH),
        "ground_truth_version": document.version,
        "ground_truth_hash": ground_truth_hash,
        "fixture_schema": EVAL_SCHEMA,
        "fixture_hashes": {
            "schema_sql": sha256_of(FIXTURE_SCHEMA_PATH),
            "data_sql": sha256_of(FIXTURE_DATA_PATH),
        },
        "total_cases": len(dataset),
        "result_evaluable_cases": len(document.case_ids),
        "result_na_cases": len(dataset) - len(document.case_ids),
        "ground_truth_case_ids": list(document.case_ids),
        "not_evaluable_case_ids": [
            case.case_id for case in dataset
            if case.case_id not in set(document.case_ids)
        ],
        "row_counts": dict(row_counts),
        "foreign_key_verified": bool(fk_ok),
    }


def _write_snapshot(payload) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_report(report) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")


def _render_report(payload) -> str:
    from backend.app.services.text_to_sql_evaluation_service import (
        load_text_to_sql_regression_dataset,
    )

    lines = [
        "# Text-to-SQL Deterministic Test Data & Ground Truth - Phase 3.9.13",
        "",
        "## Scope",
        "",
        "> Evaluation infrastructure phase: deterministic test data plus",
        "> result-level ground truth. No production logic, no Prompt",
        "> change, no model run.",
        "",
        "## Evaluation Schema",
        "",
        f"- Dedicated schema: `{payload['fixture_schema']}`",
        "- Nothing is written to `public` or any production schema",
        "- Reset is allowed only inside this schema",
        "",
        "## Tables",
        "",
        "| Table | Columns | Rows |",
        "|---|---|---:|",
        "| `t2s_eval.documents` | id, title, file_type, created_at | "
        f"{payload['row_counts'].get('documents')} |",
        "| `t2s_eval.chunks` | id, document_id, content, token_count | "
        f"{payload['row_counts'].get('chunks')} |",
        "| `t2s_eval.project_a_inventory` | item_code, qty | "
        f"{payload['row_counts'].get('project_a_inventory')} |",
        "| `t2s_eval.project_b_inventory` | item_code, qty | "
        f"{payload['row_counts'].get('project_b_inventory')} |",
        "",
        "Real FK verified: `chunks.document_id -> documents.id` = "
        f"{payload['foreign_key_verified']}",
        "",
        "## Fixture Data Design",
        "",
        "- 4 documents; chunk counts 2 / 3 / 1 / 4 (all different)",
        "- token_count 10..100, no ties (Top-N and ORDER BY deterministic)",
        "- file_type: 3 x `md`, 1 x `txt` (GROUP BY groups differ in size;",
        "  HAVING COUNT(*) > 2 keeps exactly one group)",
        "- created_at: 2 before 2026-01-01, 2 after (date filter yields 2)",
        "- No NOW() / CURRENT_DATE / random values",
        "",
        "## Ground Truth Cases",
        "",
        "| case_id | in ground truth |",
        "|---|---|",
    ]
    in_truth = set(payload["ground_truth_case_ids"])
    for case in load_text_to_sql_regression_dataset():
        lines.append(
            f"| `{case.case_id}` | "
            f"{'yes' if case.case_id in in_truth else 'N/A'} |"
        )
    lines.extend([
        "",
        "## Coverage",
        "",
        f"- Total cases: {payload['total_cases']}",
        f"- Result-evaluable: {payload['result_evaluable_cases']}",
        f"- N/A: {payload['result_na_cases']}",
        "",
        "Not evaluable and why:",
        "",
    ])
    reasons = {
        "filtered_documents_by_file_type": (
            "question does not name the file_type value, so no single "
            "deterministic result set exists"
        ),
        "safety_delete_all_documents": (
            "security-boundary case, no result ground truth (covered by "
            "Phase 3.9.7 / 3.9.8)"
        ),
    }
    for case_id in payload["not_evaluable_case_ids"]:
        lines.append(
            f"- `{case_id}`: "
            f"{reasons.get(case_id, 'no deterministic expectation')}"
        )
    lines.extend([
        "",
        "## Project Isolation",
        "",
        "- `t2s_eval.project_a_inventory.item_x` = 100",
        "- `t2s_eval.project_b_inventory.item_x` = 999",
        "- Same item_code, different qty: the same logical question must",
        "  return different results per project.",
        "",
        "## Determinism",
        "",
        "- Fixed constants only; setup is idempotent (DROP/CREATE/INSERT)",
        "- Re-running setup yields identical row counts and aggregates",
        "- No dependence on current time, random data, or external APIs",
        "",
        "## Limitations",
        "",
        f"- Ground truth assumes the evaluated SQL runs against "
        f"`{payload['fixture_schema']}`. The current regression dataset",
        "  still points at `public.*`, so a future evaluation phase must",
        "  bind the project schema to this fixture.",
        "- Ground truth was computed by hand from the fixture rows "
        "(SQL/Python only used to re-check the arithmetic).",
        "- Top-N / ORDER BY expectations assume a LIMIT large enough to",
        "  return the full ordered sequence.",
        "- Coverage is not a hard target: cases that would require fragile",
        "  data stay N/A.",
        "- No DeepSeek benchmark was run in this phase.",
        "",
    ])
    return "\n".join(lines)


# ============================================================
# --check: pure offline (0 LLM / 0 DB / 0 network / 0 writes)
# ============================================================

def check_existing() -> int:
    problems = []

    if not SNAPSHOT_PATH.exists():
        print(f"FAIL: ground truth snapshot not found: {SNAPSHOT_PATH.name}")
        return 1
    stored = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    if stored.get("phase") != PHASE:
        problems.append(
            f"phase: expected {PHASE!r}, got {stored.get('phase')!r}"
        )
    version = read_dataset_version()
    if stored.get("dataset_version") != version:
        problems.append(
            f"dataset_version: expected {version!r}, "
            f"got {stored.get('dataset_version')!r}"
        )
    if stored.get("ground_truth_version") != GROUND_TRUTH_VERSION:
        problems.append(
            f"ground_truth_version: expected {GROUND_TRUTH_VERSION!r}, "
            f"got {stored.get('ground_truth_version')!r}"
        )
    if stored.get("fixture_schema") != EVAL_SCHEMA:
        problems.append(
            f"fixture_schema: expected {EVAL_SCHEMA!r}, "
            f"got {stored.get('fixture_schema')!r}"
        )

    dataset_hash = sha256_of(DEFAULT_REGRESSION_DATASET_PATH)
    if stored.get("dataset_sha256") != dataset_hash:
        problems.append("dataset_sha256 mismatch (dataset changed)")
    gt_hash = sha256_of(GROUND_TRUTH_PATH)
    if stored.get("ground_truth_hash") != gt_hash:
        problems.append("ground_truth_hash mismatch (ground truth changed)")
    fixture_hashes = stored.get("fixture_hashes") or {}
    if fixture_hashes.get("schema_sql") != sha256_of(FIXTURE_SCHEMA_PATH):
        problems.append("fixture schema SQL hash mismatch")
    if fixture_hashes.get("data_sql") != sha256_of(FIXTURE_DATA_PATH):
        problems.append("fixture data SQL hash mismatch")

    document = load_ground_truth()
    dataset = load_text_to_sql_regression_dataset()
    problems += validate_ground_truth(document, dataset)
    problems += _fixture_problems()

    if stored.get("total_cases") != len(dataset):
        problems.append(
            f"total_cases: expected {len(dataset)}, "
            f"got {stored.get('total_cases')!r}"
        )
    if stored.get("result_evaluable_cases") != len(document.case_ids):
        problems.append("result_evaluable_cases != ground truth case count")
    if stored.get("result_na_cases") != (
        len(dataset) - len(document.case_ids)
    ):
        problems.append("result_na_cases arithmetic mismatch")
    if "safety_delete_all_documents" in document.case_ids:
        problems.append("security case must not be in ground truth")

    if problems:
        print("FAIL: 3.9.13 ground truth check failed")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"OK: 3.9.13 ground truth snapshot is consistent "
          f"({len(dataset)} cases)")
    print(f"    fixture_schema={stored.get('fixture_schema')} "
          f"ground_truth_version={stored.get('ground_truth_version')}")
    print(f"    result_evaluable="
          f"{stored.get('result_evaluable_cases')} "
          f"na={stored.get('result_na_cases')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Setup / validate Text-to-SQL deterministic fixture."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="offline validation only (no LLM, no DB, no write)",
    )
    args = parser.parse_args()
    if args.check:
        return check_existing()
    return run_setup()


if __name__ == "__main__":
    raise SystemExit(main())
