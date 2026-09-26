"""Phase 3.9.17 - Full 12-case Semantic Result Evaluation analysis script.

Pure offline (no LLM, no DB):

    python scripts/analyze_text_to_sql_semantic_result_full.py          # write snapshot + report
    python scripts/analyze_text_to_sql_semantic_result_full.py --check  # offline verification only

Reads the saved Phase 3.9.14 actual results (``phase_3_9_14_result_llm_baseline.json``)
and re-evaluates them against the full 12-case semantic ground truth defined
in ``text_to_sql_regression.yaml``. Does NOT re-run DeepSeek. Does NOT
modify the 3.9.14 artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.services.text_to_sql_result_evaluation_service import (
    load_semantic_expectations,
)
from backend.app.services.text_to_sql_semantic_result_evaluation_service import (
    NA_CASE_IDS,
    PHASE_3_9_17,
    REAL_LLM_SNAPSHOT_PATH,
    REPORT_3_9_17_PATH,
    RESULT_EVALUABLE_CASE_IDS,
    SNAPSHOT_3_9_17_PATH,
    SemanticResultCaseOutcome,
    analyze_phase_3_9_14_for_full_semantic,
    validate_semantic_consistency,
)


def _relative(p: Path) -> str:
    try:
        return p.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return p.name


def _sha256(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def generate_snapshot_and_report() -> int:
    consistency = validate_semantic_consistency()
    if consistency:
        print("FAIL: semantic ground truth consistency violations:")
        for problem in consistency:
            print(f"  - {problem}")
        return 1
    summary = analyze_phase_3_9_14_for_full_semantic()
    snapshot = json.loads(
        REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8")
    )
    payload = {
        "phase": PHASE_3_9_17,
        "source_snapshot": summary.source_snapshot,
        "source_snapshot_sha256": _sha256(REAL_LLM_SNAPSHOT_PATH),
        "phase_3_9_14_strict_result_total": summary.phase_3_9_14_result_total,
        "phase_3_9_14_strict_result_correct": summary.phase_3_9_14_result_correct,
        "phase_3_9_14_strict_result_incorrect":
            summary.phase_3_9_14_result_incorrect,
        "phase_3_9_14_strict_result_accuracy":
            summary.phase_3_9_14_result_accuracy,
        "semantic_ground_truth_coverage":
            summary.semantic_ground_truth_coverage,
        "semantic_evaluable_cases": summary.semantic_evaluable_cases,
        "semantic_correct_cases": summary.semantic_correct_cases,
        "semantic_incorrect_cases": summary.semantic_incorrect_cases,
        "semantic_result_correctness": summary.semantic_result_correctness,
        "result_evaluable_case_ids": sorted(RESULT_EVALUABLE_CASE_IDS),
        "na_case_ids": sorted(NA_CASE_IDS),
        "cases": [item.to_dict() for item in summary.cases],
    }
    SNAPSHOT_3_9_17_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_3_9_17_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    REPORT_3_9_17_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_3_9_17_PATH.write_text(
        render_report(payload, consistency=()), encoding="utf-8"
    )
    print(f"Semantic Result Full Analysis - Phase {PHASE_3_9_17}")
    print()
    print(f"Source snapshot           : {summary.source_snapshot}")
    print(f"3.9.14 strict result      : "
          f"{summary.phase_3_9_14_result_correct}/"
          f"{summary.phase_3_9_14_result_total} "
          f"= {summary.phase_3_9_14_result_accuracy * 100:.2f}%")
    print(f"Result-evaluable cases    : {len(RESULT_EVALUABLE_CASE_IDS)}")
    print(f"N/A cases                 : {len(NA_CASE_IDS)}")
    print(f"Semantic ground truth cov : "
          f"{summary.semantic_evaluable_cases}/"
          f"{len(RESULT_EVALUABLE_CASE_IDS)} "
          f"= {summary.semantic_ground_truth_coverage * 100:.2f}%")
    print(f"Semantic correct          : {summary.semantic_correct_cases}")
    print(f"Semantic incorrect        : {summary.semantic_incorrect_cases}")
    print(f"Semantic result correctness: "
          f"{summary.semantic_result_correctness * 100:.2f}%")
    print()
    print(f"Snapshot written: {_relative(SNAPSHOT_3_9_17_PATH)}")
    print(f"Report written:   {_relative(REPORT_3_9_17_PATH)}")
    return 0


def check_existing() -> int:
    problems: list[str] = []
    consistency = validate_semantic_consistency()
    if consistency:
        for problem in consistency:
            problems.append(f"semantic ground truth: {problem}")

    if not SNAPSHOT_3_9_17_PATH.exists():
        print(f"FAIL: snapshot missing: {SNAPSHOT_3_9_17_PATH.name}")
        return 1
    stored = json.loads(SNAPSHOT_3_9_17_PATH.read_text(encoding="utf-8"))

    if stored.get("phase") != PHASE_3_9_17:
        problems.append(
            f"phase: expected {PHASE_3_9_17!r}, got {stored.get('phase')!r}"
        )
    if not REAL_LLM_SNAPSHOT_PATH.exists():
        problems.append(
            f"source snapshot missing: {REAL_LLM_SNAPSHOT_PATH.name}"
        )
    elif stored.get("source_snapshot_sha256") != _sha256(REAL_LLM_SNAPSHOT_PATH):
        problems.append("source snapshot SHA256 mismatch (3.9.14 changed)")

    cases = stored.get("cases", [])
    expected_total = len(RESULT_EVALUABLE_CASE_IDS)
    if stored.get("semantic_evaluable_cases") + sum(
        1 for c in cases if c.get("semantic_passed") is None
    ) != expected_total:
        # Total cases in snapshot should equal all result-evaluable case ids
        # (including ones with missing ground truth).
        problems.append(
            f"snapshot case count != result-evaluable count "
            f"({len(cases)} vs {expected_total})"
        )

    correct = sum(1 for c in cases if c.get("semantic_passed") is True)
    incorrect = sum(1 for c in cases if c.get("semantic_passed") is False)
    evaluable = correct + incorrect
    if stored.get("semantic_evaluable_cases") != evaluable:
        problems.append(
            f"semantic_evaluable_cases: stored="
            f"{stored.get('semantic_evaluable_cases')!r} "
            f"recomputed={evaluable!r}"
        )
    if stored.get("semantic_correct_cases") != correct:
        problems.append(
            f"semantic_correct_cases: stored="
            f"{stored.get('semantic_correct_cases')!r} "
            f"recomputed={correct!r}"
        )
    if stored.get("semantic_incorrect_cases") != incorrect:
        problems.append(
            f"semantic_incorrect_cases: stored="
            f"{stored.get('semantic_incorrect_cases')!r} "
            f"recomputed={incorrect!r}"
        )
    expected_rate = round(correct / evaluable, 4) if evaluable else None
    if stored.get("semantic_result_correctness") != expected_rate:
        problems.append(
            f"semantic_result_correctness: stored="
            f"{stored.get('semantic_result_correctness')!r} "
            f"expected={expected_rate!r}"
        )

    coverage = round(evaluable / expected_total, 4)
    if stored.get("semantic_ground_truth_coverage") != coverage:
        problems.append(
            f"semantic_ground_truth_coverage: stored="
            f"{stored.get('semantic_ground_truth_coverage')!r} "
            f"expected={coverage!r}"
        )

    if stored.get("na_case_ids") != sorted(NA_CASE_IDS):
        problems.append(
            f"na_case_ids mismatch: stored={stored.get('na_case_ids')!r}"
        )
    if stored.get("result_evaluable_case_ids") != sorted(RESULT_EVALUABLE_CASE_IDS):
        problems.append(
            f"result_evaluable_case_ids mismatch"
        )

    blob = json.dumps(stored, ensure_ascii=False).lower()
    for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
        if fragment in blob:
            problems.append(f"forbidden fragment: {fragment}")

    if problems:
        print(f"FAIL: 3.9.17 semantic-full snapshot check failed")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"OK: 3.9.17 semantic-full snapshot consistent "
          f"({evaluable} evaluable / {expected_total} expected)")
    print(f"    source={stored.get('source_snapshot')} "
          f"sha={stored.get('source_snapshot_sha256')[:12]}")
    print(f"    correct={correct} incorrect={incorrect} "
          f"accuracy={stored.get('semantic_result_correctness')}")
    return 0


def render_report(
    payload: dict, *, consistency: tuple[str, ...]
) -> str:
    """Render the 3.9.17 report using the stored payload + 3.9.14 per-case strict results."""
    cases = payload["cases"]
    evaluable = payload["semantic_evaluable_cases"]
    correct = payload["semantic_correct_cases"]
    incorrect = payload["semantic_incorrect_cases"]
    coverage = payload["semantic_ground_truth_coverage"]
    accuracy = payload["semantic_result_correctness"]
    strict_accuracy = payload["phase_3_9_14_strict_result_accuracy"]
    strict_correct = payload["phase_3_9_14_strict_result_correct"]
    strict_total = payload["phase_3_9_14_strict_result_total"]
    strict_incorrect = payload["phase_3_9_14_strict_result_incorrect"]

    accuracy_str = (
        f"{accuracy * 100:.2f}%" if accuracy is not None else "N/A"
    )
    coverage_str = (
        f"{coverage * 100:.2f}%" if coverage is not None else "N/A"
    )
    strict_acc_str = f"{strict_accuracy * 100:.2f}%"

    out.append("# Text-to-SQL Semantic Result Full Evaluation - Phase 3.9.17")
    out.append("")
    out.append("## 1. Objective")
    out.append("")
    out.append("> Extend the Phase 3.9.16 semantic Result-level evaluation from")
    out.append("> 3 cases to the **full 12 result-evaluable cases** and report the")
    out.append("> real Semantic Result Correctness without changing strict")
    out.append("> projection behavior.")
    out.append("")
    out.append("Phase 3.9.14 strict projection: 9 / 12 = 75.00%.")
    out.append("Phase 3.9.17 semantic correctness: see section 8 (real number,")
    out.append("not pre-set).")
    out.append("")
    out.append("## 2. Scope")
    out.append("")
    out.append("12 result-evaluable cases:")
    for cid in payload["result_evaluable_case_ids"]:
        out.append(f"- `{cid}`")
    out.append("")
    out.append(f"2 N/A cases (semantic_expectation intentionally absent):")
    for cid in payload["na_case_ids"]:
        out.append(f"- `{cid}`")
    out.append("")
    out.append("## 3. 12-case semantic Ground Truth design")
    out.append("")
    out.append("| Case | Required | Optional | Forbidden | row_matching | Rationale |")
    out.append("|---|---|---|---|---|---|")
    design_rows = _DESIGN_TABLE
    for row in design_rows:
        out.append("| " + " | ".join(row) + " |")
    out.append("")
    out.append("Design principles applied uniformly:")
    out.append("")
    out.append("- `required_columns` = exactly the columns the question explicitly")
    out.append("  names, OR the natural identifier / business answer when the")
    out.append("  question is generic.")
    out.append("- `optional_columns` = helpful context (e.g. `title`, `file_type`)")
    out.append("  that the question did NOT require.")
    out.append("- `forbidden_columns` = sensitive / business-conflicting columns")
    out.append("  (empty for all 12 cases in 3.9.17).")
    out.append("- `row_matching = ordered` ONLY when the question names an explicit")
    out.append("  ordering (\"最多\" / \"从高到低排序\"). Otherwise unordered.")
    out.append("- `expected_rows` lists ONLY the `required_columns` values.")
    out.append("")
    out.append("## 4. Required / Optional / Forbidden design")
    out.append("")
    out.append("- Every case MUST satisfy: required != empty (parser enforces).")
    out.append("- Cases cannot declare the same column as both required AND")
    out.append("  optional/forbidden (parser enforces).")
    out.append("- `expected_rows` row-width MUST equal `len(required_columns)`")
    out.append("  (parser enforces).")
    out.append("")
    out.append("## 5. Row matching semantics")
    out.append("")
    out.append("| Question phrasing | row_matching |")
    out.append("|---|---|")
    out.append("| \"最多\" / \"从高到低排序\" | ordered |")
    out.append("| \"哪些\" / \"按 ... 过滤\" / \"前 N 条\" | unordered |")
    out.append("| Aggregation (`COUNT(*)`) | unordered (single row) |")
    out.append("")
    out.append("## 6. Strict vs Semantic evaluation")
    out.append("")
    out.append("| Mode | Required cols | Forbidden cols | Extra cols |")
    out.append("|---|---|---|---|")
    out.append("| **strict_exact_projection** (3.9.14) | == expected row tuple | rejected | rejected |")
    out.append("| **semantic_result_correctness** (3.9.16/3.9.17) | subset (extras OK unless forbidden) | rejected | warning only |")
    out.append("")
    out.append("Both metrics measure different layers and are kept side-by-side.")
    out.append("")
    out.append("## 7. Per-case Results")
    out.append("")
    out.append("| Case | Strict (3.9.14) | Semantic (3.9.17) | Required | Optional | Forbidden | Categories |")
    out.append("|---|---|---|---|---|---|---|")
    # Load 3.9.14 per-case strict result_correct so the per-case row can
    # report both Strict (3.9.14) and Semantic (3.9.17) outcomes.
    import json as _json
    _real = _json.loads(REAL_LLM_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    strict_by_id = {
        c["case_id"]: bool(c.get("result_correct"))
        for c in _real.get("cases", [])
    }
    for c in cases:
        strict_pass = strict_by_id.get(c["case_id"], None)
        if strict_pass is True:
            strict = "PASS"
        elif strict_pass is False:
            strict = "FAIL"
        else:
            strict = "N/A"
        # Use semantic_passed
        sem = (
            "PASS" if c["semantic_passed"] is True
            else "FAIL" if c["semantic_passed"] is False
            else "N/A"
        )
        req = ",".join(c["expected_required_columns"]) or "-"
        opt = ",".join(c["expected_optional_columns"]) or "-"
        forb = ",".join(c["expected_forbidden_columns"]) or "-"
        cats = ",".join(c["semantic_categories"]) or "-"
        out.append(
            f"| `{c['case_id']}` | {strict} | {sem} | {req} | {opt} | {forb} | {cats} |"
        )
    out.append("")
    out.append("## 8. Aggregate metrics")
    out.append("")
    out.append(f"| Metric | Value |")
    out.append(f"|---|---|")
    out.append(f"| Source snapshot | {payload['source_snapshot']} (saved DeepSeek run, NOT re-executed) |")
    out.append(f"| Fixture | `t2s_eval` (3.9.13) |")
    out.append(f"| Ground truth | `result_ground_truth.yaml` (3.9.13) + `text_to_sql_regression.yaml` (3.9.17 extended) |")
    out.append(f"| 3.9.14 strict result | {strict_correct} / {strict_total} = {strict_acc_str} |")
    out.append(f"| Result-evaluable cases | {len(payload['result_evaluable_case_ids'])} |")
    out.append(f"| Semantic ground truth coverage | {evaluable} / {len(payload['result_evaluable_case_ids'])} = {coverage_str} |")
    out.append(f"| Semantic correct | {correct} |")
    out.append(f"| Semantic incorrect | {incorrect} |")
    out.append(f"| **Semantic result correctness** | **{accuracy_str}** |")
    out.append("")
    out.append("## 9. Comparison with 3.9.14")
    out.append("")
    out.append("| Metric | 3.9.14 Strict Projection | 3.9.17 Semantic Result |")
    out.append("|---|---:|---:|")
    out.append(f"| Denominator | {strict_total} (evaluable) | {evaluable} |")
    out.append(f"| Correct | {strict_correct} | {correct} |")
    out.append(f"| Incorrect | {strict_incorrect} | {incorrect} |")
    out.append(f"| Accuracy | {strict_acc_str} | {accuracy_str} |")
    out.append("")
    out.append("The two metrics are independent layers and remain separate.")
    out.append("")
    out.append("## 10. Coverage")
    out.append("")
    out.append(f"```text")
    out.append(f"Semantic Ground Truth Coverage = {evaluable}/{len(payload['result_evaluable_case_ids'])} = {coverage_str}")
    out.append(f"```")
    out.append("")
    out.append("Coverage is NOT accuracy. All 12 cases have a semantic ground")
    out.append("truth; the semantic_correctness ratio above is what matters.")
    out.append("")
    out.append("## 11. Limitations")
    out.append("")
    out.append("- `aggregate_document_count` requires the column to be named")
    out.append("  `count` (PostgreSQL default for `COUNT(*)`). Any LLM that")
    out.append("  rewrites it as `total_documents` would fail with")
    out.append("  `MISSING_REQUIRED_COLUMN`. General column-name alias mapping")
    out.append("  is NOT implemented in 3.9.17 — recorded as")
    out.append("  `PHASE_3_9_18_CANDIDATE`.")
    out.append("- No new prompt / SQL / model / executor change.")
    out.append("- Semantic evaluation only checks REQUIRED-column values, row set")
    out.append("  equality, duplicates, and forbidden columns. It does NOT")
    out.append("  verify SQL is optimal, stable, or well-indexed.")
    out.append("")
    out.append("## 12. Phase 3.9.18 recommendation")
    out.append("")
    out.append("- Add column-name alias mapping (`id <-> document_id`) so")
    out.append("  semantic matching is robust against common projection aliases.")
    out.append("- Consider per-category distribution (warning vs hard) in the")
    out.append("  per-case report to surface \"near-miss\" cases.")
    out.append("- If the prompt layer becomes aware of `semantic_expectation`, the")
    out.append("  LLM could be steered toward the required projection more")
    out.append("  reliably — investigate without forcing the issue.")

    return "\n".join(out) + "\n"


# Compact per-case design table for the report's section 3.
# (Case, Required, Optional, Forbidden, row_matching, Rationale)
_DESIGN_TABLE: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "simple_document_list",
        "id",
        "title, file_type, created_at",
        "-",
        "unordered",
        "\"列表\" 的最小回答 = 每个文档一个标识符；title 等是辅助展示。",
    ),
    (
        "top_n_chunks_by_token_count",
        "id, token_count",
        "document_id, content",
        "-",
        "ordered",
        "\"最多\" 隐含 ORDER BY token_count DESC；token_count 是排序与展示核心。",
    ),
    (
        "chunks_ordered_by_token_count",
        "id, token_count",
        "document_id, content",
        "-",
        "ordered",
        "与 Top N 同语义但 question 显式指定 ORDER BY。",
    ),
    (
        "aggregate_document_count",
        "count",
        "-",
        "-",
        "unordered",
        "COUNT(*) scalar；列名依赖 PostgreSQL 默认 (\"count\")。",
    ),
    (
        "group_by_chunk_count_per_document",
        "id, chunk_count",
        "title",
        "-",
        "unordered",
        "文档标识 + 分片数量是 question 明确要求；title 是合理附加。",
    ),
    (
        "having_chunk_count_greater_than",
        "id",
        "title, chunk_count",
        "-",
        "unordered",
        "\"哪些文档满足条件\" → 文档标识符即足够；chunk_count 是 HAVING 谓词。",
    ),
    (
        "join_chunk_with_parent_document",
        "id, title",
        "content, document_id",
        "-",
        "unordered",
        "question 同时点名\"分片\"与\"文档标题\"，两者均 required。",
    ),
    (
        "date_filter_created_after",
        "id",
        "title, file_type, created_at",
        "-",
        "unordered",
        "日期过滤只决定 row set；id 是文档标识符。",
    ),
    (
        "limit_first_10_documents",
        "id",
        "title, file_type, created_at",
        "-",
        "unordered",
        "LIMIT 10 不等于\"特定投影\"；id 是最小标识符；无显式排序。",
    ),
    (
        "semantic_dependent_document_and_chunk",
        "id, chunk_count",
        "title",
        "-",
        "unordered",
        "与 group_by 同形：文档标识 + 分片数量。",
    ),
    (
        "project_a_inventory",
        "item_code, qty",
        "-",
        "-",
        "unordered",
        "\"库存明细\" 的最小回答 = 物料编码 + 数量；两者均为业务核心数据。",
    ),
    (
        "project_b_inventory",
        "item_code, qty",
        "-",
        "-",
        "unordered",
        "与 project_a 同形但 qty 不同（隔离验证）。",
    ),
)


out: list[str] = []


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 3.9.17 full semantic result evaluation."
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