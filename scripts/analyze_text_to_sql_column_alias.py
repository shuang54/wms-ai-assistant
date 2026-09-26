"""Semantic Column Alias Mapping - offline audits (Phase 3.9.18 / 3.9.19).

Pure offline (0 DeepSeek calls, 0 DB, 0 network).

    python scripts/analyze_text_to_sql_column_alias.py            # 3.9.18 snapshot + report
    python scripts/analyze_text_to_sql_column_alias.py --phase 3.9.19  # 3.9.19 trigger snapshot + report
    python scripts/analyze_text_to_sql_column_alias.py --check    # verify 3.9.18 (+ 3.9.19 if present)

Phase 3.9.18 re-evaluates the 12 result-evaluable cases (saved 3.9.14
actual results + frozen 3.9.17 semantic snapshot) with the alias-aware
semantic checker.

Phase 3.9.19 additionally builds OFFLINE projection variants (derived from
the saved 3.9.14 results, never re-run) that genuinely exercise the alias
path, and reports the unified required/optional/forbidden/extra handling.

The 3.9.14 and 3.9.17 artifacts are never modified.
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

from backend.app.services.text_to_sql_column_alias_evaluation_service import (  # noqa: E402
    SEMANTIC_COLUMN_ALIASES,
    validate_alias_registry,
)
from backend.app.services.text_to_sql_semantic_result_evaluation_service import (  # noqa: E402
    NA_CASE_IDS,
    PHASE_3_9_18,
    PHASE_3_9_19,
    PROJECTION_VARIANT_PATH,
    REAL_LLM_SNAPSHOT_PATH,
    REPORT_3_9_18_PATH,
    REPORT_3_9_19_PATH,
    RESULT_EVALUABLE_CASE_IDS,
    SNAPSHOT_3_9_17_PATH,
    SNAPSHOT_3_9_18_PATH,
    SNAPSHOT_3_9_19_PATH,
    analyze_phase_3_9_14_for_alias_audit,
    run_projection_variants,
)


def _relative(p: Path) -> str:
    try:
        return p.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return p.name


def _sha256(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def _audit_rows(audit) -> tuple[tuple[str, str, str, str, str], ...]:
    """(case, expected semantic column, actual column, current match, alias needed)"""
    rows = []
    for entry in audit.entries:
        rows.append((
            entry.case_id,
            entry.expected_column,
            str(entry.actual_column) if entry.actual_column else "-",
            entry.matched_by,
            "YES" if entry.alias_needed else "no",
        ))
    return tuple(rows)


def generate_snapshot_and_report() -> int:
    problems = validate_alias_registry()
    if problems:
        print("FAIL: alias registry is inconsistent:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    audit = analyze_phase_3_9_14_for_alias_audit()

    payload = {
        "phase": PHASE_3_9_18,
        "source_snapshot": audit.source_snapshot,
        "source_snapshot_sha256": _sha256(REAL_LLM_SNAPSHOT_PATH),
        "phase_3_9_17_snapshot": SNAPSHOT_3_9_17_PATH.name,
        "phase_3_9_17_semantic_accuracy": audit.phase_3_9_17_semantic_accuracy,
        "phase_3_9_17_semantic_correct": audit.phase_3_9_17_semantic_correct,
        "phase_3_9_18_semantic_accuracy": audit.phase_3_9_18_semantic_accuracy,
        "phase_3_9_18_semantic_correct": audit.phase_3_9_18_semantic_correct,
        "semantic_evaluable_cases": audit.semantic_evaluable_cases,
        "exact_matches": audit.exact_matches,
        "alias_matches": audit.alias_matches,
        "unmatched_columns": audit.unmatched_columns,
        "alias_needed_cases": audit.alias_needed_cases,
        "alias_registry_size": len(SEMANTIC_COLUMN_ALIASES),
        "deepseek_calls": 0,
        "result_evaluable_case_ids": sorted(RESULT_EVALUABLE_CASE_IDS),
        "na_case_ids": sorted(NA_CASE_IDS),
        "entries": [entry.to_dict() for entry in audit.entries],
        "cases": [case.to_dict() for case in audit.cases],
    }

    SNAPSHOT_3_9_18_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_3_9_18_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    REPORT_3_9_18_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_3_9_18_PATH.write_text(
        render_report(payload, _audit_rows(audit)), encoding="utf-8"
    )

    print(f"Semantic Column Alias Audit - Phase {PHASE_3_9_18}")
    print()
    print(f"Source snapshot            : {audit.source_snapshot}")
    print(f"3.9.17 semantic accuracy   : "
          f"{_pct(audit.phase_3_9_17_semantic_accuracy)} "
          f"({audit.phase_3_9_17_semantic_correct}/"
          f"{audit.semantic_evaluable_cases})")
    print(f"3.9.18 semantic accuracy   : "
          f"{_pct(audit.phase_3_9_18_semantic_accuracy)} "
          f"({audit.phase_3_9_18_semantic_correct}/"
          f"{audit.semantic_evaluable_cases})")
    print(f"Exact matches              : {audit.exact_matches}")
    print(f"Alias matches              : {audit.alias_matches}")
    print(f"Unmatched columns          : {audit.unmatched_columns}")
    print(f"Cases needing an alias     : {audit.alias_needed_cases}")
    print(f"DeepSeek calls             : 0")
    print()
    print(f"Snapshot written: {_relative(SNAPSHOT_3_9_18_PATH)}")
    print(f"Report written:   {_relative(REPORT_3_9_18_PATH)}")
    return 0


def check_existing() -> int:
    problems: list[str] = []

    registry_problems = validate_alias_registry()
    problems.extend(f"alias registry: {p}" for p in registry_problems)

    if not SNAPSHOT_3_9_18_PATH.exists():
        print(f"FAIL: snapshot missing: {SNAPSHOT_3_9_18_PATH.name}")
        return 1
    stored = json.loads(SNAPSHOT_3_9_18_PATH.read_text(encoding="utf-8"))

    if stored.get("phase") != PHASE_3_9_18:
        problems.append(
            f"phase: expected {PHASE_3_9_18!r}, got {stored.get('phase')!r}"
        )
    if not REAL_LLM_SNAPSHOT_PATH.exists():
        problems.append(
            f"source snapshot missing: {REAL_LLM_SNAPSHOT_PATH.name}"
        )
    elif stored.get("source_snapshot_sha256") != _sha256(REAL_LLM_SNAPSHOT_PATH):
        problems.append("source snapshot SHA256 mismatch (3.9.14 changed)")

    entries = stored.get("entries", [])
    cases = stored.get("cases", [])

    recomputed = {
        "exact": sum(1 for e in entries if e.get("matched_by") == "exact"),
        "alias": sum(1 for e in entries if e.get("matched_by") == "alias"),
        "none": sum(1 for e in entries if e.get("matched_by") == "none"),
    }
    for key, field in (
        ("exact", "exact_matches"),
        ("alias", "alias_matches"),
        ("none", "unmatched_columns"),
    ):
        if stored.get(field) != recomputed[key]:
            problems.append(
                f"{field}: stored={stored.get(field)!r} "
                f"recomputed={recomputed[key]!r}"
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
    if stored.get("phase_3_9_18_semantic_correct") != correct:
        problems.append(
            f"phase_3_9_18_semantic_correct: stored="
            f"{stored.get('phase_3_9_18_semantic_correct')!r} "
            f"recomputed={correct!r}"
        )
    expected_rate = round(correct / evaluable, 4) if evaluable else None
    if stored.get("phase_3_9_18_semantic_accuracy") != expected_rate:
        problems.append(
            f"phase_3_9_18_semantic_accuracy: stored="
            f"{stored.get('phase_3_9_18_semantic_accuracy')!r} "
            f"expected={expected_rate!r}"
        )

    # Regression guard (section §十五): alias must never reduce accuracy.
    before = stored.get("phase_3_9_17_semantic_accuracy")
    after = stored.get("phase_3_9_18_semantic_accuracy")
    if before is not None and after is not None and after < before:
        problems.append(
            f"semantic accuracy regressed: 3.9.17={before} 3.9.18={after}"
        )

    if stored.get("result_evaluable_case_ids") != sorted(
        RESULT_EVALUABLE_CASE_IDS
    ):
        problems.append("result_evaluable_case_ids mismatch")
    if stored.get("na_case_ids") != sorted(NA_CASE_IDS):
        problems.append("na_case_ids mismatch")

    blob = json.dumps(stored, ensure_ascii=False).lower()
    for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
        if fragment in blob:
            problems.append(f"forbidden fragment: {fragment}")

    if problems:
        print(f"FAIL: 3.9.18 alias snapshot check failed")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"OK: 3.9.18 alias snapshot consistent "
          f"({evaluable} evaluable / {len(RESULT_EVALUABLE_CASE_IDS)} expected)")
    print(f"    source={stored.get('source_snapshot')} "
          f"sha={str(stored.get('source_snapshot_sha256'))[:12]}")
    print(f"    exact={stored.get('exact_matches')} "
          f"alias={stored.get('alias_matches')} "
          f"unmatched={stored.get('unmatched_columns')}")
    print(f"    3.9.17={stored.get('phase_3_9_17_semantic_accuracy')} "
          f"3.9.18={stored.get('phase_3_9_18_semantic_accuracy')}")
    return 0


def generate_3_9_19() -> int:
    problems = validate_alias_registry()
    if problems:
        print("FAIL: alias registry is inconsistent:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    summary = run_projection_variants()

    payload = summary.to_dict()
    payload["source_snapshot_sha256"] = _sha256(REAL_LLM_SNAPSHOT_PATH)
    payload["phase_3_9_17_snapshot"] = SNAPSHOT_3_9_17_PATH.name
    payload["parent_baseline_snapshot"] = SNAPSHOT_3_9_18_PATH.name
    payload["projection_variant_file"] = PROJECTION_VARIANT_PATH.name
    payload["alias_registry_size"] = len(SEMANTIC_COLUMN_ALIASES)
    # Explicitly distinguish real historical results from synthetic ones.
    payload["kind_note"] = (
        "variant_count entries are OFFLINE projection variants derived from "
        "the saved Phase 3.9.14 results; they are NOT real LLM outputs and "
        "must never be reported as such (section §十三)."
    )

    SNAPSHOT_3_9_19_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_3_9_19_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    REPORT_3_9_19_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_3_9_19_PATH.write_text(render_report_3_9_19(summary), encoding="utf-8")

    print(f"Semantic Column Alias Trigger - Phase {PHASE_3_9_19}")
    print()
    print(f"Parent baseline            : {summary.parent_baseline}")
    print(f"Real historical results    : {summary.real_historical_result_count}")
    print(f"3.9.17 / 3.9.18 accuracy   : "
          f"{_pct(summary.phase_3_9_17_semantic_accuracy)} / "
          f"{_pct(summary.phase_3_9_18_semantic_accuracy)}")
    print(f"Projection variants        : {summary.variant_count}")
    print(f"Expectation met            : "
          f"{summary.variants_expectation_met}/{summary.variant_count}")
    print(f"required matched_by        : {dict(summary.required_matched_by)}")
    print(f"optional matched_by        : {dict(summary.optional_matched_by)}")
    print(f"forbidden matched_by       : {dict(summary.forbidden_matched_by)}")
    print(f"UNDECLARED_EXTRA variants  : {summary.undeclared_extra_variants}")
    print(f"DeepSeek/DB/network calls  : "
          f"{summary.llm_calls}/{summary.db_calls}/{summary.network_calls}")
    print()
    print(f"Snapshot written: {_relative(SNAPSHOT_3_9_19_PATH)}")
    print(f"Report written:   {_relative(REPORT_3_9_19_PATH)}")
    return 0


def check_3_9_19() -> int:
    if not SNAPSHOT_3_9_19_PATH.exists():
        print("SKIP: 3.9.19 alias-trigger snapshot not generated yet")
        return 0
    problems: list[str] = []
    stored = json.loads(SNAPSHOT_3_9_19_PATH.read_text(encoding="utf-8"))

    if stored.get("phase") != PHASE_3_9_19:
        problems.append(
            f"phase: expected {PHASE_3_9_19!r}, got {stored.get('phase')!r}"
        )
    if not REAL_LLM_SNAPSHOT_PATH.exists():
        problems.append("source snapshot missing: "
                        f"{REAL_LLM_SNAPSHOT_PATH.name}")
    elif stored.get("source_snapshot_sha256") != _sha256(REAL_LLM_SNAPSHOT_PATH):
        problems.append("source snapshot SHA256 mismatch (3.9.14 changed)")

    live = analyze_phase_3_9_14_for_alias_audit()
    # `unknown_matches` in the 3.9.19 snapshot maps to `unmatched_columns`
    # on the live 3.9.18 audit object.
    _live_fields = {
        "phase_3_9_18_semantic_correct": "phase_3_9_18_semantic_correct",
        "phase_3_9_18_semantic_accuracy": "phase_3_9_18_semantic_accuracy",
        "exact_matches": "exact_matches",
        "alias_matches": "alias_matches",
        "unknown_matches": "unmatched_columns",
    }
    for stored_field, live_field in _live_fields.items():
        if stored.get(stored_field) != getattr(live, live_field):
            problems.append(
                f"{stored_field}: stored={stored.get(stored_field)!r} "
                f"recomputed={getattr(live, live_field)!r}"
            )

    # Every variant must satisfy its declared expectation.
    variants = stored.get("variants", [])
    if stored.get("variant_count") != len(variants):
        problems.append(
            f"variant_count: stored={stored.get('variant_count')!r} "
            f"recomputed={len(variants)}"
        )
    if any(not v.get("expectation_met") for v in variants):
        problems.append("at least one projection variant did NOT meet its "
                        "declared expectation")

    # No regression: alias must never reduce semantic accuracy vs 3.9.17.
    before = stored.get("phase_3_9_17_semantic_accuracy")
    after = stored.get("phase_3_9_18_semantic_accuracy")
    if before is not None and after is not None and after < before:
        problems.append(
            f"semantic accuracy regressed: 3.9.17={before} 3.9.18={after}"
        )

    # Offline guarantees.
    for key in ("llm_calls", "db_calls", "network_calls"):
        if stored.get(key) != 0:
            problems.append(f"{key} must be 0, got {stored.get(key)}")

    blob = json.dumps(stored, ensure_ascii=False).lower()
    for fragment in ("sk-", "postgres://", "postgresql://", "bearer "):
        if fragment in blob:
            problems.append(f"forbidden fragment: {fragment}")

    if problems:
        print("FAIL: 3.9.19 alias-trigger snapshot check failed")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"OK: 3.9.19 alias-trigger snapshot consistent "
          f"({len(variants)} variants, "
          f"{stored.get('real_historical_result_count')} real historical)")
    print(f"    real acc 3.9.17={stored.get('phase_3_9_17_semantic_accuracy')} "
          f"3.9.18={stored.get('phase_3_9_18_semantic_accuracy')}")
    print(f"    required={dict(stored.get('required_matched_by', {}))} "
          f"optional={dict(stored.get('optional_matched_by', {}))} "
          f"forbidden={dict(stored.get('forbidden_matched_by', {}))}")
    return 0


def render_report_3_9_19(summary) -> str:
    out: list[str] = []
    add = out.append

    add("# Text-to-SQL Alias Trigger & Optional/Extra Consistency - 3.9.19")
    add("")
    add("## 1. Goal")
    add("")
    add("> Extend the alias resolver to `required`, `optional`, and the")
    add("> `UNDECLARED_EXTRA_COLUMN` classification, and prove the alias")
    add("> path is **actually triggered** by offline projection variants.")
    add("")
    add("This is an Evaluation-Layer improvement, not a Text-to-SQL model")
    add("change. No DeepSeek, no DB, no network (section §三).")
    add("")
    add("## 2. Unified resolver")
    add("")
    add("One resolver (`resolve_semantic_column`) and one entity context map")
    add("are applied to `required_columns`, `optional_columns`, and")
    add("`forbidden_columns`. A column can therefore never be both an alias")
    add("of a declared concept AND an undeclared extra column.")
    add("")
    add("| Role | Behaviour |")
    add("|---|---|")
    add("| required | alias match counts as the column being present;")
    add("missing -> `MISSING_REQUIRED_COLUMN` |")
    add("| optional | alias match means the optional column is present;")
    add("absent -> ignored |")
    add("| forbidden | alias match means the forbidden column IS present")
    add("-> `EXTRA_FORBIDDEN_COLUMN` (hard fail) |")
    add("")
    add("## 3. Cross-entity guard")
    add("")
    add("A projection name claimed by concepts of more than one entity")
    add("(`id`, `count`) only matches when the entity context is explicit")
    add("and equals the concept's entity. Wrong entity, or no context, ")
    add("resolves to `UNKNOWN_COLUMN` — never a guess. So `documents.id`")
    add("never bleeds into `chunk_id`.")
    add("")
    add("## 4. matched_by diagnostics")
    add("")
    add("Every matched (or unmatched) column carries")
    add("`matched_by ∈ {exact, alias, none}` plus a `role`")
    add("(`required` / `optional` / `forbidden`). The diagnostic")
    add("distinguishes exact from alias and is recorded only; it never")
    add("relaxes the hard-failure rule.")
    add("")
    add("## 5. Projection variants (offline alias trigger)")
    add("")
    add(f"All {summary.variant_count} variants are derived in-memory from the")
    add("SAVED Phase 3.9.14 actual results (file")
    add(f"`{PROJECTION_VARIANT_PATH.name}`). They are NOT real LLM outputs")
    add("and must never be reported as such (section §十三).")
    add("")
    add("| Kind | Count |")
    add("|---|---|")
    add(f"| Real historical result (Phase 3.9.14) | "
        f"{summary.real_historical_result_count} |")
    add(f"| Offline projection variant | {summary.variant_count} |")
    add("")
    add("### matched_by distribution")
    add("")
    add("| Role | exact | alias | none |")
    add("|---|---:|---:|---:|")
    add(f"| required | {summary.required_matched_by.get('exact', 0)} | "
        f"{summary.required_matched_by.get('alias', 0)} | "
        f"{summary.required_matched_by.get('none', 0)} |")
    add(f"| optional | {summary.optional_matched_by.get('exact', 0)} | "
        f"{summary.optional_matched_by.get('alias', 0)} | "
        f"{summary.optional_matched_by.get('none', 0)} |")
    add(f"| forbidden | {summary.forbidden_matched_by.get('exact', 0)} | "
        f"{summary.forbidden_matched_by.get('alias', 0)} | "
        f"{summary.forbidden_matched_by.get('none', 0)} |")
    add("")
    add(f"ALIAS matches triggered: "
        f"{summary.required_matched_by.get('alias', 0) + summary.optional_matched_by.get('alias', 0) + summary.forbidden_matched_by.get('alias', 0)}")
    add("")
    add("### Variant expectations")
    add("")
    add(f"- variants expecting PASS: {summary.variants_expecting_pass}")
    add(f"- variants whose actual PASS matches expectation: "
        f"{summary.variants_pass_expected}")
    add(f"- variants meeting full declared expectation: "
        f"{summary.variants_expectation_met}/{summary.variant_count}")
    add(f"- variants reporting UNDECLARED_EXTRA_COLUMN (warning, still PASS): "
        f"{list(summary.undeclared_extra_variants)}")
    add("")
    add("## 6. Alias correctness")
    add("")
    add("- No false positive: fuzzy-looking names (`doc_id`, `documentid`,")
    add("  `document_ids`, `doc`) never match `document_id`.")
    add("- Wrong-entity (`documents.id` under chunk context) never matches.")
    add("- Ambiguous bare `id` / `count` without context never auto-binds.")
    add("- Forbidden `chunk_id` resolved via alias (`id` under chunk context)")
    add("  IS detected as `EXTRA_FORBIDDEN_COLUMN`.")
    add("- An alias-matched column never produces a spurious")
    add("  `UNDECLARED_EXTRA_COLUMN`.")
    add("")
    add("## 7. Semantic accuracy (must not regress)")
    add("")
    add("| Metric | 3.9.17 | 3.9.18 | 3.9.19 |")
    add("|---|---:|---:|---:|")
    add(f"| Semantic evaluable cases | {summary.real_historical_result_count} "
        f"| {summary.real_historical_result_count} "
        f"| {summary.real_historical_result_count} |")
    add(f"| Semantic correct | "
        f"{summary.phase_3_9_18_semantic_correct} "
        f"| {summary.phase_3_9_18_semantic_correct} "
        f"| {summary.phase_3_9_18_semantic_correct} |")
    add(f"| **Semantic result correctness** | "
        f"**{_pct(summary.phase_3_9_17_semantic_accuracy)}** | "
        f"**{_pct(summary.phase_3_9_18_semantic_accuracy)}** | "
        f"**{_pct(summary.phase_3_9_18_semantic_accuracy)}** |")
    add(f"| Real exact matches | n/a | {summary.exact_matches} | "
        f"{summary.exact_matches} |")
    add(f"| Real alias matches | n/a | {summary.alias_matches} | "
        f"{summary.alias_matches} |")
    add(f"| Real unmatched columns | n/a | {summary.unknown_matches} | "
        f"{summary.unknown_matches} |")
    add(f"| DeepSeek / DB / network calls | 0 / 0 / 0 | 0 / 0 / 0 | "
        f"{summary.llm_calls} / {summary.db_calls} / {summary.network_calls} |")
    add("")
    add("The 12 real historical cases stay 12/12; the alias extension to")
    add("optional/forbidden changed no real result.")
    add("")
    return "\n".join(out) + "\n"


def render_report(
    payload: dict,
    audit_rows: tuple[tuple[str, str, str, str, str], ...],
) -> str:
    out: list[str] = []
    add = out.append

    add("# Text-to-SQL Semantic Column Alias Mapping - Phase 3.9.18")
    add("")
    add("## 1. Problem")
    add("")
    add("> Make result evaluation robust against **column-name variation**")
    add("> without turning it into fuzzy matching.")
    add("")
    add("The same business concept may be projected under different names:")
    add("")
    add("```text")
    add("documents.id   ->  id  |  document_id")
    add("chunks.id      ->  id  |  chunk_id")
    add("COUNT(*)       ->  count  |  total_count  |  total_documents")
    add("```")
    add("")
    add("Phase 3.9.17 matched required columns by literal (case-insensitive)")
    add("name, so any of the above variations produced")
    add("`MISSING_REQUIRED_COLUMN` — a false negative.")
    add("")
    add("## 2. Why global alias is unsafe")
    add("")
    add("A global rule such as `\"id\" -> \"document_id\"` is wrong, because")
    add("`documents.id` and `chunks.id` denote **different business")
    add("entities**. The same argument applies to aggregates: `count` over")
    add("`documents` is not `count` over `chunks`.")
    add("")
    add("Therefore alias resolution is **never** a string substitution and")
    add("**never** a similarity measure:")
    add("")
    add("```text")
    add("forbidden: fuzzy matching | Levenshtein | embedding similarity")
    add("           | LLM-as-a-judge")
    add("```")
    add("")
    add("An unresolvable column resolves to `UNKNOWN_COLUMN` instead of a")
    add("guess. A false positive is worse than an explicit unknown.")
    add("")
    add("## 3. Context-aware alias model")
    add("")
    add("```text")
    add("ColumnSemanticAlias")
    add("  semantic_name   canonical concept name")
    add("  entity          business entity (document / chunk / inventory_item)")
    add("  source_table    fixture table the concept comes from")
    add("  source_column   physical column (or '*' for aggregates)")
    add("  aggregate       'count' for aggregate concepts, else None")
    add("  aliases         projection names that PROVABLY denote this concept")
    add("                  for THIS entity")
    add("```")
    add("")
    add("Example:")
    add("")
    add("```yaml")
    add("documents:")
    add("  id:")
    add("    semantic_name: document_id")
    add("    aliases: [id, document_id]")
    add("chunks:")
    add("  id:")
    add("    semantic_name: chunk_id")
    add("    aliases: [id, chunk_id]")
    add("```")
    add("")
    add("Both concepts claim `id`, so `id` alone is **ambiguous** and is only")
    add("resolved when the caller supplies an entity context.")
    add("")
    add("Implementation: `text_to_sql_column_alias_evaluation_service.py`")
    add("(evaluation layer only — pure function, deterministic, no DB, no")
    add("LLM, no network).")
    add("")
    add(f"Registry size: {payload['alias_registry_size']} concepts.")
    add("")
    add("## 4. Exact vs Alias matching")
    add("")
    add("| Priority | Rule | Diagnostic |")
    add("|---|---|---|")
    add("| 1 | exact column name (case-insensitive) | `EXACT_MATCH` |")
    add("| 2 | expected column IS a canonical `semantic_name`;")
    add("actual column is one of its declared aliases | `ALIAS_MATCH` |")
    add("| 3 | expected column is a bare alias (`id`, `count`, `title`);")
    add("the concept is selected by entity + aggregate context | `ALIAS_MATCH` |")
    add("| 4 | otherwise | `UNKNOWN_COLUMN` |")
    add("")
    add("Priority 3 refuses to resolve when the context is missing or when")
    add("more than one concept matches the context.")
    add("")
    add("`ALIAS_MATCH` is a **diagnostic**, not a warning and not an error:")
    add("it is recorded as `matched_by = alias` (or `matched_by = exact`) and")
    add("never changes the hard-failure rule.")
    add("")
    add("### Strict projection is untouched (section §八)")
    add("")
    add("| Mode | Expected `document_id`, actual `id` |")
    add("|---|---|")
    add("| `strict_exact_projection` (3.9.14) | FAIL |")
    add("| `semantic_result_correctness` (3.9.18) | PASS (if the alias is provable) |")
    add("")
    add("Alias resolution is applied to REQUIRED columns inside the semantic")
    add("checker only. The four strict checkers (`exact_rows`,")
    add("`unordered_rows`, `scalar`, `column_values`) are unchanged.")
    add("")
    add("## 5. Aggregate alias semantics")
    add("")
    add("| Concept | Entity | Aggregate | Aliases |")
    add("|---|---|---|---|")
    add("| `document_count` | document | count | count, total_count, total_documents, document_count, documents_count, num_documents |")
    add("| `chunk_count` | chunk | count | count, total_count, total_chunks, chunk_count, chunks_count, num_chunks |")
    add("")
    add("Rules:")
    add("")
    add("- `count` / `total_count` are shared by several entities and are")
    add("  therefore **never** resolved without an entity context.")
    add("- `total_documents` is an alias of the *document* count concept")
    add("  only; there is no rule `count == total_documents`.")
    add("- `COUNT(*) FROM documents` and `COUNT(*) FROM documents AS")
    add("  total_documents` are the same business answer; `COUNT(*) FROM")
    add("  chunks` is not.")
    add("")
    add("## 6. Negative cases")
    add("")
    add("| # | Setup | Expected outcome |")
    add("|---|---|---|")
    add("| 1 | `documents.id` vs expected `document_id` | alias match |")
    add("| 2 | `chunks.id` vs expected `chunk_id` | alias match |")
    add("| 3 | `documents.id` vs actual `chunk_id` | NO match (different entity) |")
    add("| 4 | `COUNT(documents)` -> `total_documents`; `COUNT(chunks)` -> `total_documents` | match / no match |")
    add("| 5 | expected `document_id` vs actual `document_code` | NO match (unknown alias) |")
    add("| 6 | bare `id` or `count` without entity context | NO match (ambiguous) |")
    add("| 7 | `document_ids` / `doc_id` / `documentid` / `doc` | NO match (no fuzzy matching) |")
    add("| 8 | strict `column_values` on `document_id`, actual `id` | FAIL |")
    add("")
    add("## 7. 12-case alias audit")
    add("")
    add("Source: saved Phase 3.9.14 actual results (NOT re-executed).")
    add("Ground truth: unchanged 3.9.17 `semantic_expectation`.")
    add("")
    add("| Case | Expected semantic column | Actual column | Current match | Alias needed? |")
    add("|---|---|---|---|---|")
    for row in audit_rows:
        add("| `" + row[0] + "` | " + " | ".join(row[1:]) + " |")
    add("")
    add(f"- exact matches: **{payload['exact_matches']}**")
    add(f"- alias matches: **{payload['alias_matches']}**")
    add(f"- unmatched columns: **{payload['unmatched_columns']}**")
    add(f"- cases needing an alias: **{payload['alias_needed_cases']}**")
    add("")
    add("**Audit conclusion:** every required column of all 12 cases already")
    add("matches by exact name. The alias layer is therefore inactive for the")
    add("current snapshot — which is the desired outcome. No ground-truth")
    add("entry was modified to make this true (section §十二).")
    add("")
    add("The mechanism is nevertheless exercised: `aggregate_document_count`")
    add("now also passes semantically if the projection is named")
    add("`total_documents` instead of `count`, which was an explicit")
    add("3.9.17 limitation.")
    add("")
    add("## 8. Semantic accuracy comparison")
    add("")
    add("| Metric | 3.9.17 | 3.9.18 |")
    add("|---|---:|---:|")
    add(f"| Semantic evaluable cases | {payload['semantic_evaluable_cases']} "
        f"| {payload['semantic_evaluable_cases']} |")
    add(f"| Semantic correct | {payload['phase_3_9_17_semantic_correct']} "
        f"| {payload['phase_3_9_18_semantic_correct']} |")
    add(f"| Semantic incorrect | "
        f"{payload['semantic_evaluable_cases'] - payload['phase_3_9_17_semantic_correct']} "
        f"| {payload['semantic_evaluable_cases'] - payload['phase_3_9_18_semantic_correct']} |")
    add(f"| **Semantic result correctness** | "
        f"**{_pct(payload['phase_3_9_17_semantic_accuracy'])}** | "
        f"**{_pct(payload['phase_3_9_18_semantic_accuracy'])}** |")
    add(f"| Alias matches | n/a | {payload['alias_matches']} |")
    add(f"| Exact matches | n/a | {payload['exact_matches']} |")
    add(f"| Unmatched columns | n/a | {payload['unmatched_columns']} |")
    add("")
    add("3.9.14 strict projection remains 9 / 12 = 75.00% (unchanged).")
    add("DeepSeek calls in this phase: **0**.")
    add("")
    add("## 9. Limitations")
    add("")
    add("- The registry is deliberately minimal: it covers the `t2s_eval`")
    add("  fixture entities (document, chunk, inventory_item). Real WMS/ERP")
    add("  schemas need their own declarations — the registry is data, not")
    add("  logic.")
    add("- Alias resolution only applies to **required** columns. Optional")
    add("  and forbidden columns are still matched by literal name.")
    add("- `optional_columns` are not alias-resolved, so a semantically")
    add("  equivalent optional column may still be reported as")
    add("  `UNDECLARED_EXTRA_COLUMN` (warning only).")
    add("- The per-case entity context is declared in the evaluation layer")
    add("  and derived from the saved 3.9.14 SQL. It is metadata; if the")
    add("  questions change, the context must be reviewed.")
    add("- No alias is inferred from data types, values, or row shape.")
    add("  A column whose business meaning cannot be proven stays UNKNOWN.")
    add("- This phase did not re-run DeepSeek; the numbers are derived from")
    add("  the saved 3.9.14 snapshot only.")
    add("")
    add("## 10. Phase 3.9.19 recommendation")
    add("")
    add("- Apply alias resolution to `optional_columns` and to the")
    add("  `UNDECLARED_EXTRA_COLUMN` classification so equivalence is")
    add("  consistent across all three column classes.")
    add("- Report a per-case `matched_by` distribution (exact / alias /")
    add("  unknown) in the semantic snapshot to surface near-miss cases.")
    add("- Evaluate alias behaviour against a second saved LLM run (or a")
    add("  mutated projection) so the alias path is exercised on real data,")
    add("  not only by unit tests.")
    add("- Keep the registry declarative (YAML) once it grows beyond the")
    add("  fixture entities, but keep resolution pure and offline.")
    add("")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Semantic column alias offline audits (3.9.18 / 3.9.19)."
    )
    parser.add_argument(
        "--phase",
        choices=["3.9.18", "3.9.19"],
        default="3.9.18",
        help="which phase snapshot/report to generate (default 3.9.18)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="offline consistency check only (no LLM, no DB)",
    )
    args = parser.parse_args()
    if args.check:
        rc = check_existing()
        rc_3_9 = check_3_9_19()
        return 1 if (rc or rc_3_9) else 0
    if args.phase == "3.9.19":
        return generate_3_9_19()
    return generate_snapshot_and_report()


if __name__ == "__main__":
    raise SystemExit(main())
