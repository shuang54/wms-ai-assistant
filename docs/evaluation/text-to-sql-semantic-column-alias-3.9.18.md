# Text-to-SQL Semantic Column Alias Mapping - Phase 3.9.18

## 1. Problem

> Make result evaluation robust against **column-name variation**
> without turning it into fuzzy matching.

The same business concept may be projected under different names:

```text
documents.id   ->  id  |  document_id
chunks.id      ->  id  |  chunk_id
COUNT(*)       ->  count  |  total_count  |  total_documents
```

Phase 3.9.17 matched required columns by literal (case-insensitive)
name, so any of the above variations produced
`MISSING_REQUIRED_COLUMN` — a false negative.

## 2. Why global alias is unsafe

A global rule such as `"id" -> "document_id"` is wrong, because
`documents.id` and `chunks.id` denote **different business
entities**. The same argument applies to aggregates: `count` over
`documents` is not `count` over `chunks`.

Therefore alias resolution is **never** a string substitution and
**never** a similarity measure:

```text
forbidden: fuzzy matching | Levenshtein | embedding similarity
           | LLM-as-a-judge
```

An unresolvable column resolves to `UNKNOWN_COLUMN` instead of a
guess. A false positive is worse than an explicit unknown.

## 3. Context-aware alias model

```text
ColumnSemanticAlias
  semantic_name   canonical concept name
  entity          business entity (document / chunk / inventory_item)
  source_table    fixture table the concept comes from
  source_column   physical column (or '*' for aggregates)
  aggregate       'count' for aggregate concepts, else None
  aliases         projection names that PROVABLY denote this concept
                  for THIS entity
```

Example:

```yaml
documents:
  id:
    semantic_name: document_id
    aliases: [id, document_id]
chunks:
  id:
    semantic_name: chunk_id
    aliases: [id, chunk_id]
```

Both concepts claim `id`, so `id` alone is **ambiguous** and is only
resolved when the caller supplies an entity context.

Implementation: `text_to_sql_column_alias_evaluation_service.py`
(evaluation layer only — pure function, deterministic, no DB, no
LLM, no network).

Registry size: 11 concepts.

## 4. Exact vs Alias matching

| Priority | Rule | Diagnostic |
|---|---|---|
| 1 | exact column name (case-insensitive) | `EXACT_MATCH` |
| 2 | expected column IS a canonical `semantic_name`;
actual column is one of its declared aliases | `ALIAS_MATCH` |
| 3 | expected column is a bare alias (`id`, `count`, `title`);
the concept is selected by entity + aggregate context | `ALIAS_MATCH` |
| 4 | otherwise | `UNKNOWN_COLUMN` |

Priority 3 refuses to resolve when the context is missing or when
more than one concept matches the context.

`ALIAS_MATCH` is a **diagnostic**, not a warning and not an error:
it is recorded as `matched_by = alias` (or `matched_by = exact`) and
never changes the hard-failure rule.

### Strict projection is untouched (section §八)

| Mode | Expected `document_id`, actual `id` |
|---|---|
| `strict_exact_projection` (3.9.14) | FAIL |
| `semantic_result_correctness` (3.9.18) | PASS (if the alias is provable) |

Alias resolution is applied to REQUIRED columns inside the semantic
checker only. The four strict checkers (`exact_rows`,
`unordered_rows`, `scalar`, `column_values`) are unchanged.

## 5. Aggregate alias semantics

| Concept | Entity | Aggregate | Aliases |
|---|---|---|---|
| `document_count` | document | count | count, total_count, total_documents, document_count, documents_count, num_documents |
| `chunk_count` | chunk | count | count, total_count, total_chunks, chunk_count, chunks_count, num_chunks |

Rules:

- `count` / `total_count` are shared by several entities and are
  therefore **never** resolved without an entity context.
- `total_documents` is an alias of the *document* count concept
  only; there is no rule `count == total_documents`.
- `COUNT(*) FROM documents` and `COUNT(*) FROM documents AS
  total_documents` are the same business answer; `COUNT(*) FROM
  chunks` is not.

## 6. Negative cases

| # | Setup | Expected outcome |
|---|---|---|
| 1 | `documents.id` vs expected `document_id` | alias match |
| 2 | `chunks.id` vs expected `chunk_id` | alias match |
| 3 | `documents.id` vs actual `chunk_id` | NO match (different entity) |
| 4 | `COUNT(documents)` -> `total_documents`; `COUNT(chunks)` -> `total_documents` | match / no match |
| 5 | expected `document_id` vs actual `document_code` | NO match (unknown alias) |
| 6 | bare `id` or `count` without entity context | NO match (ambiguous) |
| 7 | `document_ids` / `doc_id` / `documentid` / `doc` | NO match (no fuzzy matching) |
| 8 | strict `column_values` on `document_id`, actual `id` | FAIL |

## 7. 12-case alias audit

Source: saved Phase 3.9.14 actual results (NOT re-executed).
Ground truth: unchanged 3.9.17 `semantic_expectation`.

| Case | Expected semantic column | Actual column | Current match | Alias needed? |
|---|---|---|---|---|
| `aggregate_document_count` | count | count | exact | no |
| `chunks_ordered_by_token_count` | id | id | exact | no |
| `chunks_ordered_by_token_count` | token_count | token_count | exact | no |
| `date_filter_created_after` | id | id | exact | no |
| `group_by_chunk_count_per_document` | id | id | exact | no |
| `group_by_chunk_count_per_document` | chunk_count | chunk_count | exact | no |
| `having_chunk_count_greater_than` | id | id | exact | no |
| `join_chunk_with_parent_document` | id | id | exact | no |
| `join_chunk_with_parent_document` | title | title | exact | no |
| `limit_first_10_documents` | id | id | exact | no |
| `project_a_inventory` | item_code | item_code | exact | no |
| `project_a_inventory` | qty | qty | exact | no |
| `project_b_inventory` | item_code | item_code | exact | no |
| `project_b_inventory` | qty | qty | exact | no |
| `semantic_dependent_document_and_chunk` | id | id | exact | no |
| `semantic_dependent_document_and_chunk` | chunk_count | chunk_count | exact | no |
| `simple_document_list` | id | id | exact | no |
| `top_n_chunks_by_token_count` | id | id | exact | no |
| `top_n_chunks_by_token_count` | token_count | token_count | exact | no |

- exact matches: **19**
- alias matches: **0**
- unmatched columns: **0**
- cases needing an alias: **0**

**Audit conclusion:** every required column of all 12 cases already
matches by exact name. The alias layer is therefore inactive for the
current snapshot — which is the desired outcome. No ground-truth
entry was modified to make this true (section §十二).

The mechanism is nevertheless exercised: `aggregate_document_count`
now also passes semantically if the projection is named
`total_documents` instead of `count`, which was an explicit
3.9.17 limitation.

## 8. Semantic accuracy comparison

| Metric | 3.9.17 | 3.9.18 |
|---|---:|---:|
| Semantic evaluable cases | 12 | 12 |
| Semantic correct | 12 | 12 |
| Semantic incorrect | 0 | 0 |
| **Semantic result correctness** | **100.00%** | **100.00%** |
| Alias matches | n/a | 0 |
| Exact matches | n/a | 19 |
| Unmatched columns | n/a | 0 |

3.9.14 strict projection remains 9 / 12 = 75.00% (unchanged).
DeepSeek calls in this phase: **0**.

## 9. Limitations

- The registry is deliberately minimal: it covers the `t2s_eval`
  fixture entities (document, chunk, inventory_item). Real WMS/ERP
  schemas need their own declarations — the registry is data, not
  logic.
- Alias resolution only applies to **required** columns. Optional
  and forbidden columns are still matched by literal name.
- `optional_columns` are not alias-resolved, so a semantically
  equivalent optional column may still be reported as
  `UNDECLARED_EXTRA_COLUMN` (warning only).
- The per-case entity context is declared in the evaluation layer
  and derived from the saved 3.9.14 SQL. It is metadata; if the
  questions change, the context must be reviewed.
- No alias is inferred from data types, values, or row shape.
  A column whose business meaning cannot be proven stays UNKNOWN.
- This phase did not re-run DeepSeek; the numbers are derived from
  the saved 3.9.14 snapshot only.

## 10. Phase 3.9.19 recommendation

- Apply alias resolution to `optional_columns` and to the
  `UNDECLARED_EXTRA_COLUMN` classification so equivalence is
  consistent across all three column classes.
- Report a per-case `matched_by` distribution (exact / alias /
  unknown) in the semantic snapshot to surface near-miss cases.
- Evaluate alias behaviour against a second saved LLM run (or a
  mutated projection) so the alias path is exercised on real data,
  not only by unit tests.
- Keep the registry declarative (YAML) once it grows beyond the
  fixture entities, but keep resolution pure and offline.

