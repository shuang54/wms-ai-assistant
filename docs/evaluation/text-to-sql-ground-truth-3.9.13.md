# Text-to-SQL Deterministic Test Data & Ground Truth - Phase 3.9.13

## Scope

> Evaluation infrastructure phase: deterministic test data plus
> result-level ground truth. No production logic, no Prompt
> change, no model run.

## Evaluation Schema

- Dedicated schema: `t2s_eval`
- Nothing is written to `public` or any production schema
- Reset is allowed only inside this schema

## Tables

| Table | Columns | Rows |
|---|---|---:|
| `t2s_eval.documents` | id, title, file_type, created_at | 4 |
| `t2s_eval.chunks` | id, document_id, content, token_count | 10 |
| `t2s_eval.project_a_inventory` | item_code, qty | 2 |
| `t2s_eval.project_b_inventory` | item_code, qty | 2 |

Real FK verified: `chunks.document_id -> documents.id` = True

## Fixture Data Design

- 4 documents; chunk counts 2 / 3 / 1 / 4 (all different)
- token_count 10..100, no ties (Top-N and ORDER BY deterministic)
- file_type: 3 x `md`, 1 x `txt` (GROUP BY groups differ in size;
  HAVING COUNT(*) > 2 keeps exactly one group)
- created_at: 2 before 2026-01-01, 2 after (date filter yields 2)
- No NOW() / CURRENT_DATE / random values

## Ground Truth Cases

| case_id | in ground truth |
|---|---|
| `simple_document_list` | yes |
| `top_n_chunks_by_token_count` | yes |
| `filtered_documents_by_file_type` | N/A |
| `chunks_ordered_by_token_count` | yes |
| `aggregate_document_count` | yes |
| `group_by_chunk_count_per_document` | yes |
| `having_chunk_count_greater_than` | yes |
| `join_chunk_with_parent_document` | yes |
| `date_filter_created_after` | yes |
| `limit_first_10_documents` | yes |
| `semantic_dependent_document_and_chunk` | yes |
| `safety_delete_all_documents` | N/A |
| `project_a_inventory` | yes |
| `project_b_inventory` | yes |

## Coverage

- Total cases: 14
- Result-evaluable: 12
- N/A: 2

Not evaluable and why:

- `filtered_documents_by_file_type`: question does not name the file_type value, so no single deterministic result set exists
- `safety_delete_all_documents`: security-boundary case, no result ground truth (covered by Phase 3.9.7 / 3.9.8)

## Project Isolation

- `t2s_eval.project_a_inventory.item_x` = 100
- `t2s_eval.project_b_inventory.item_x` = 999
- Same item_code, different qty: the same logical question must
  return different results per project.

## Determinism

- Fixed constants only; setup is idempotent (DROP/CREATE/INSERT)
- Re-running setup yields identical row counts and aggregates
- No dependence on current time, random data, or external APIs

## Limitations

- Ground truth assumes the evaluated SQL runs against `t2s_eval`. The current regression dataset
  still points at `public.*`, so a future evaluation phase must
  bind the project schema to this fixture.
- Ground truth was computed by hand from the fixture rows (SQL/Python only used to re-check the arithmetic).
- Top-N / ORDER BY expectations assume a LIMIT large enough to
  return the full ordered sequence.
- Coverage is not a hard target: cases that would require fragile
  data stay N/A.
- No DeepSeek benchmark was run in this phase.
