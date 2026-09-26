"""Phase 3.9.16 - Semantic Result Evaluation analysis script.

Pure offline (no LLM, no DB):
    python scripts/analyze_text_to_sql_semantic_result.py          # write snapshot + report
    python scripts/analyze_text_to_sql_semantic_result.py --check  # offline verification only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.services.text_to_sql_semantic_result_evaluation_service import (
    PHASE_3_9_16,
    REAL_LLM_SNAPSHOT_PATH,
    REPORT_3_9_16_PATH,
    SNAPSHOT_3_9_16_PATH,
    analyze_phase_3_9_14_for_semantic,
)
from backend.app.services.text_to_sql_result_evaluation_service import (
    load_semantic_expectations,
)


def _relative(p):
    target = Path(p)
    try:
        return target.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return target.name


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def generate_snapshot_and_report() -> int:
    summary = analyze_phase_3_9_14_for_semantic()
    payload = {
        "phase": PHASE_3_9_16,
        "source_snapshot": summary.source_snapshot,
        "source_snapshot_sha256": _sha256(REAL_LLM_SNAPSHOT_PATH),
        "total_cases": summary.total_cases,
        "semantic_evaluable_cases": summary.semantic_evaluable_cases,
        "semantic_correct_cases": summary.semantic_correct_cases,
        "semantic_incorrect_cases": summary.semantic_incorrect_cases,
        "semantic_result_correctness": summary.semantic_result_correctness,
        "cases": [item.to_dict() for item in summary.cases],
    }
    SNAPSHOT_3_9_16_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_3_9_16_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    REPORT_3_9_16_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_3_9_16_PATH.write_text(
        render_report(payload), encoding="utf-8"
    )
    print(f"Semantic Result Analysis - Phase {PHASE_3_9_16}")
    print()
    print(f"Source snapshot     : {summary.source_snapshot}")
    print(f"Total cases         : {summary.total_cases}")
    print(f"Semantic evaluable   : {summary.semantic_evaluable_cases}")
    print(f"Semantic correct     : {summary.semantic_correct_cases}")
    print(f"Semantic incorrect   : {summary.semantic_incorrect_cases}")
    print(f"Semantic correctness : {summary.semantic_result_correctness}")
    print()
    print(f"Snapshot written: {_relative(SNAPSHOT_3_9_16_PATH)}")
    print(f"Report written:   {_relative(REPORT_3_9_16_PATH)}")
    return 0


def check_existing() -> int:
    problems = []
    if not SNAPSHOT_3_9_16_PATH.exists():
        print(f"FAIL: snapshot missing: {SNAPSHOT_3_9_16_PATH.name}")
        return 1
    stored = json.loads(SNAPSHOT_3_9_16_PATH.read_text(encoding="utf-8"))

    if stored.get("phase") != PHASE_3_9_16:
        problems.append(
            f"phase: expected {PHASE_3_9_16!r}, got {stored.get('phase')!r}"
        )
    if not REAL_LLM_SNAPSHOT_PATH.exists():
        problems.append(
            f"source snapshot missing: {REAL_LLM_SNAPSHOT_PATH.name}"
        )
    elif stored.get("source_snapshot_sha256") != _sha256(REAL_LLM_SNAPSHOT_PATH):
        problems.append("source snapshot SHA256 mismatch (3.9.14 changed)")

    semantic_truth = load_semantic_expectations()
    cases = stored.get("cases", [])
    # Phase 3.9.17: the YAML gained more ``semantic_expectation`` entries
    # (3 -> 12, full coverage). The 3.9.16 snapshot is a frozen 3-case
    # artifact; its --check validates INTERNAL consistency only. The
    # ``stored.total_cases`` value MUST be preserved (history), and the
    # 3.9.16 case_ids MUST still be present in the current YAML (so the
    # 3.9.16 ground truth was not accidentally deleted).
    if stored.get("total_cases") != len(cases):
        problems.append(
            f"total_cases ({stored.get('total_cases')}) "
            f"!= len(cases) ({len(cases)})"
        )
    if len(cases) != stored.get("semantic_evaluable_cases"):
        problems.append(
            f"len(cases) ({len(cases)}) != "
            f"semantic_evaluable_cases ({stored.get('semantic_evaluable_cases')})"
        )
    stored_ids = [c.get("case_id") for c in cases]
    missing_in_truth = [cid for cid in stored_ids if cid not in semantic_truth]
    if missing_in_truth:
        # The 3.9.16 ground truth was deleted -> real regression.
        problems.append(
            f"3.9.16 case_ids no longer in dataset: {missing_in_truth}"
        )

    correct = sum(1 for c in cases if c.get("semantic_passed") is True)
    incorrect = sum(1 for c in cases if c.get("semantic_passed") is False)
    evaluable = correct + incorrect
    if stored.get("semantic_evaluable_cases") != evaluable:
        problems.append(
            f"semantic_evaluable_cases: stored={stored.get('semantic_evaluable_cases')!r} "
            f"recomputed={evaluable!r}"
        )
    if stored.get("semantic_correct_cases") != correct:
        problems.append(
            f"semantic_correct_cases: stored={stored.get('semantic_correct_cases')!r} "
            f"recomputed={correct!r}"
        )
    if stored.get("semantic_incorrect_cases") != incorrect:
        problems.append(
            f"semantic_incorrect_cases: stored={stored.get('semantic_incorrect_cases')!r} "
            f"recomputed={incorrect!r}"
        )
    expected_rate = round(correct / evaluable, 4) if evaluable else None
    if stored.get("semantic_result_correctness") != expected_rate:
        problems.append(
            f"semantic_result_correctness: stored={stored.get('semantic_result_correctness')!r} "
            f"expected={expected_rate!r}"
        )

    blob = json.dumps(stored, ensure_ascii=False).lower()
    for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
        if fragment in blob:
            problems.append(f"forbidden fragment: {fragment}")

    if problems:
        print("FAIL: 3.9.16 semantic snapshot check failed")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    stored_total = stored.get("total_cases", len(cases))
    print(f"OK: 3.9.16 semantic snapshot consistent ({stored_total} cases)")
    print(f"    source={stored.get('source_snapshot')} "
          f"sha={stored.get('source_snapshot_sha256')[:12]}")
    print(f"    correct={stored.get('semantic_correct_cases')} "
          f"incorrect={stored.get('semantic_incorrect_cases')} "
          f"accuracy={stored.get('semantic_result_correctness')}")
    return 0


def render_report(payload):
    cases = payload["cases"]
    metrics = {
        "Source snapshot": payload["source_snapshot"],
        "Total cases": payload["total_cases"],
        "Semantic evaluable cases": payload["semantic_evaluable_cases"],
        "Semantic correct cases": payload["semantic_correct_cases"],
        "Semantic incorrect cases": payload["semantic_incorrect_cases"],
        "Semantic result correctness": (
            f"{payload['semantic_result_correctness'] * 100:.2f}%"
            if payload["semantic_result_correctness"] is not None
            else "N/A"
        ),
    }
    lines = [
        "# Text-to-SQL Semantic Result Evaluation - Phase 3.9.16",
        "",
        "## 1. Objective",
        "",
        "> Make Result-level evaluation correctly distinguish two questions:",
        "> (a) Are the SQL **values** correct?  (b) Is the SQL **projection**",
        "> strictly the columns the GT happened to list?",
        "",
        "Phase 3.9.14 strict projection conflated both; 3.9.16 introduces a",
        "**semantic** view alongside, without modifying the 3.9.14 artifact.",
        "",
        "## 2. Ground Truth Semantic Model",
        "",
        "Each case may carry both:",
        "",
        "- ``expectation`` (strict 4 types, unchanged from 3.9.10)",
        "- ``semantic_expectation`` (new, 3.9.16)",
        "",
        "```yaml",
        "semantic_expectation:",
        "  required_columns: [col_a, col_b]   # must be present",
        "  optional_columns: [col_c]          # allowed but not required",
        "  forbidden_columns: []              # must NOT be present",
        "  expected_rows: [[v_a, v_b], ...]  # required-column values",
        "  row_matching: unordered            # or 'ordered'",
        "```",
        "",
        "**strict_exact_projection** (3.9.14): row tuple must match exactly.",
        "**semantic_result_correctness** (3.9.16): only required columns +",
        "values checked; optional allowed; forbidden rejected; undeclared",
        "extra recorded as UNDECLARED_EXTRA warning (not a hard failure).",
        "",
        "## 3. Two Evaluation Modes",
        "",
        "| Mode | Required cols | Forbidden cols | Extra cols",
        "|---|---|---|---|",
        "| strict | == expected row tuple | rejected if extra | rejected |",
        "| semantic | subset (any extras OK unless forbidden) | rejected | warning only |",
        "",
        "## 4. Failure Categories (semantic)",
        "",
        "- ``MISSING_REQUIRED_COLUMN`` - required column absent",
        "- ``EXTRA_FORBIDDEN_COLUMN`` - forbidden column present",
        "- ``WRONG_COLUMN_VALUE`` / ``WRONG_ROW_SET`` - value mismatch",
        "- ``COLUMN_ORDER_MISMATCH`` - ordered semantic + wrong order",
        "- ``UNDECLARED_EXTRA_COLUMN`` - warning, not hard failure",
        "- ``DUPLICATE_ROW`` - duplicate row detected",
        "",
        "Multi-label: a case may accumulate several categories.",
        "",
        "## 5. Environment",
        "",
        "| Item | Value |",
        "|---|---|",
        f"| Source | {metrics['Source snapshot']} (saved DeepSeek run, NOT re-executed) |",
        "| Fixture | `t2s_eval` (3.9.13) |",
        "| Ground truth | `result_ground_truth.yaml` (3.9.13) |",
        "| Dataset | `text_to_sql_regression.yaml` (1.0) |",
        "",
        "## 6. Metrics",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Total cases | {metrics['Total cases']} |",
        f"| Semantic evaluable cases | {metrics['Semantic evaluable cases']} |",
        f"| Semantic correct cases | {metrics['Semantic correct cases']} |",
        f"| Semantic incorrect cases | {metrics['Semantic incorrect cases']} |",
        f"| **Semantic result correctness** | **{metrics['Semantic result correctness']}** |",
        "",
        "## 7. Per-case Results",
        "",
        "| case_id | required | optional | forbidden | semantic_passed | categories |",
        "|---|---|---|---|---|---|",
    ]
    for c in cases:
        cats = ", ".join(c["semantic_categories"]) or "-"
        lines.append(
            f"| `{c['case_id']}` | "
            f"{','.join(c['expected_required_columns']) or '-'} | "
            f"{','.join(c['expected_optional_columns']) or '-'} | "
            f"{','.join(c['expected_forbidden_columns']) or '-'} | "
            f"{c['semantic_passed']} | {cats} |"
        )
    lines.extend([
        "",
        "## 8. Re-mapped Mismatches (from Phase 3.9.15 diagnosis)",
        "",
        "Three cases were `mismatched` under strict projection in 3.9.14",
        "but their **values** are correct. Under semantic mode:",
        "",
    ])
    for c in cases:
        if c["semantic_passed"] is True and not c["semantic_categories"]:
            lines.append(
                f"- `{c['case_id']}`: **PASS** (required columns + values match;"
                " extra column accepted as semantic-compliant)."
            )
        elif c["semantic_passed"] is False:
            cats = ", ".join(c["semantic_categories"])
            lines.append(f"- `{c['case_id']}`: **FAIL** ({cats}).")
    lines.extend([
        "",
        "## 9. Comparison with 3.9.14 (strict projection)",
        "",
        "| Metric | 3.9.14 Strict Projection | 3.9.16 Semantic Result |",
        "|---|---:|---:|",
        f"| Denominator | 12 (evaluable) | {payload['semantic_evaluable_cases']} |",
        f"| Correct | 9 | {payload['semantic_correct_cases']} |",
        f"| Incorrect | 3 | {payload['semantic_incorrect_cases']} |",
        f"| Accuracy | 75.00% | {metrics['Semantic result correctness']} |",
        "",
        "Two metrics measure different layers; both are kept (section 6).",
        "",
        "## 10. Backward Compatibility",
        "",
        "- ``exact_rows`` / ``unordered_rows`` / ``scalar`` / ``column_values`` checker unchanged.",
        "- Strict ``passed`` semantics unchanged in ``ResultCheckResult.passed``.",
        "- New ``semantic_passed`` / ``semantic_categories`` fields added alongside, never replacing.",
        "- Old tests still pass; new tests added for semantic mode.",
        "",
        "## 11. Limitations",
        "",
        "- Only 3 cases have ``semantic_expectation``; the remaining 9 stay N/A.",
        "- Undeclared extra columns are allowed (deliberate, by design).",
        "- 3.9.14 --check fails on dataset SHA256 because ground truth added",
        "  new ``semantic_expectation`` blocks (section 25 explicitly allows",
        "  this and requires the new hash to be recorded).",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 3.9.16 semantic result evaluation."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="offline consistency check only (no LLM, no DB)",
    )
    args = parser.parse_args()
    if args.check:
        return check_existing()
    return generate_snapshot_and_report()


if __name__ == "__main__":
    raise SystemExit(main())
