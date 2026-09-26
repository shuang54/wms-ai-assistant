# Text-to-SQL Alias Trigger & Optional/Extra Consistency - 3.9.19

## 1. Goal

> Extend the alias resolver to `required`, `optional`, and the
> `UNDECLARED_EXTRA_COLUMN` classification, and prove the alias
> path is **actually triggered** by offline projection variants.

This is an Evaluation-Layer improvement, not a Text-to-SQL model
change. No DeepSeek, no DB, no network (section §三).

## 2. Unified resolver

One resolver (`resolve_semantic_column`) and one entity context map
are applied to `required_columns`, `optional_columns`, and
`forbidden_columns`. A column can therefore never be both an alias
of a declared concept AND an undeclared extra column.

| Role | Behaviour |
|---|---|
| required | alias match counts as the column being present;
missing -> `MISSING_REQUIRED_COLUMN` |
| optional | alias match means the optional column is present;
absent -> ignored |
| forbidden | alias match means the forbidden column IS present
-> `EXTRA_FORBIDDEN_COLUMN` (hard fail) |

## 3. Cross-entity guard

A projection name claimed by concepts of more than one entity
(`id`, `count`) only matches when the entity context is explicit
and equals the concept's entity. Wrong entity, or no context, 
resolves to `UNKNOWN_COLUMN` — never a guess. So `documents.id`
never bleeds into `chunk_id`.

## 4. matched_by diagnostics

Every matched (or unmatched) column carries
`matched_by ∈ {exact, alias, none}` plus a `role`
(`required` / `optional` / `forbidden`). The diagnostic
distinguishes exact from alias and is recorded only; it never
relaxes the hard-failure rule.

## 5. Projection variants (offline alias trigger)

All 16 variants are derived in-memory from the
SAVED Phase 3.9.14 actual results (file
`projection_variants_3_9_19.yaml`). They are NOT real LLM outputs
and must never be reported as such (section §十三).

| Kind | Count |
|---|---|
| Real historical result (Phase 3.9.14) | 12 |
| Offline projection variant | 16 |

### matched_by distribution

| Role | exact | alias | none |
|---|---:|---:|---:|
| required | 1 | 6 | 10 |
| optional | 2 | 1 | 0 |
| forbidden | 0 | 1 | 0 |

ALIAS matches triggered: 8

### Variant expectations

- variants expecting PASS: 6
- variants whose actual PASS matches expectation: 16
- variants meeting full declared expectation: 16/16
- variants reporting UNDECLARED_EXTRA_COLUMN (warning, still PASS): ['case_h_unknown_extra']

## 6. Alias correctness

- No false positive: fuzzy-looking names (`doc_id`, `documentid`,
  `document_ids`, `doc`) never match `document_id`.
- Wrong-entity (`documents.id` under chunk context) never matches.
- Ambiguous bare `id` / `count` without context never auto-binds.
- Forbidden `chunk_id` resolved via alias (`id` under chunk context)
  IS detected as `EXTRA_FORBIDDEN_COLUMN`.
- An alias-matched column never produces a spurious
  `UNDECLARED_EXTRA_COLUMN`.

## 7. Semantic accuracy (must not regress)

| Metric | 3.9.17 | 3.9.18 | 3.9.19 |
|---|---:|---:|---:|
| Semantic evaluable cases | 12 | 12 | 12 |
| Semantic correct | 12 | 12 | 12 |
| **Semantic result correctness** | **100.00%** | **100.00%** | **100.00%** |
| Real exact matches | n/a | 19 | 19 |
| Real alias matches | n/a | 0 | 0 |
| Real unmatched columns | n/a | 0 | 0 |
| DeepSeek / DB / network calls | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |

The 12 real historical cases stay 12/12; the alias extension to
optional/forbidden changed no real result.

