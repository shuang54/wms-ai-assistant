# Text-to-SQL Semantic Result Full Evaluation - Phase 3.9.17

## 1. Objective

> Extend the Phase 3.9.16 semantic Result-level evaluation from
> 3 cases to the **full 12 result-evaluable cases** and report the
> real Semantic Result Correctness without changing strict
> projection behavior.

Phase 3.9.14 strict projection: 9 / 12 = 75.00%.
Phase 3.9.17 semantic correctness: see section 8 (real number,
not pre-set).

## 2. Scope

12 result-evaluable cases:
- `aggregate_document_count`
- `chunks_ordered_by_token_count`
- `date_filter_created_after`
- `group_by_chunk_count_per_document`
- `having_chunk_count_greater_than`
- `join_chunk_with_parent_document`
- `limit_first_10_documents`
- `project_a_inventory`
- `project_b_inventory`
- `semantic_dependent_document_and_chunk`
- `simple_document_list`
- `top_n_chunks_by_token_count`

2 N/A cases (semantic_expectation intentionally absent):
- `filtered_documents_by_file_type`
- `safety_delete_all_documents`

## 3. 12-case semantic Ground Truth design

| Case | Required | Optional | Forbidden | row_matching | Rationale |
|---|---|---|---|---|---|
| simple_document_list | id | title, file_type, created_at | - | unordered | "列表" 的最小回答 = 每个文档一个标识符；title 等是辅助展示。 |
| top_n_chunks_by_token_count | id, token_count | document_id, content | - | ordered | "最多" 隐含 ORDER BY token_count DESC；token_count 是排序与展示核心。 |
| chunks_ordered_by_token_count | id, token_count | document_id, content | - | ordered | 与 Top N 同语义但 question 显式指定 ORDER BY。 |
| aggregate_document_count | count | - | - | unordered | COUNT(*) scalar；列名依赖 PostgreSQL 默认 ("count")。 |
| group_by_chunk_count_per_document | id, chunk_count | title | - | unordered | 文档标识 + 分片数量是 question 明确要求；title 是合理附加。 |
| having_chunk_count_greater_than | id | title, chunk_count | - | unordered | "哪些文档满足条件" → 文档标识符即足够；chunk_count 是 HAVING 谓词。 |
| join_chunk_with_parent_document | id, title | content, document_id | - | unordered | question 同时点名"分片"与"文档标题"，两者均 required。 |
| date_filter_created_after | id | title, file_type, created_at | - | unordered | 日期过滤只决定 row set；id 是文档标识符。 |
| limit_first_10_documents | id | title, file_type, created_at | - | unordered | LIMIT 10 不等于"特定投影"；id 是最小标识符；无显式排序。 |
| semantic_dependent_document_and_chunk | id, chunk_count | title | - | unordered | 与 group_by 同形：文档标识 + 分片数量。 |
| project_a_inventory | item_code, qty | - | - | unordered | "库存明细" 的最小回答 = 物料编码 + 数量；两者均为业务核心数据。 |
| project_b_inventory | item_code, qty | - | - | unordered | 与 project_a 同形但 qty 不同（隔离验证）。 |

Design principles applied uniformly:

- `required_columns` = exactly the columns the question explicitly
  names, OR the natural identifier / business answer when the
  question is generic.
- `optional_columns` = helpful context (e.g. `title`, `file_type`)
  that the question did NOT require.
- `forbidden_columns` = sensitive / business-conflicting columns
  (empty for all 12 cases in 3.9.17).
- `row_matching = ordered` ONLY when the question names an explicit
  ordering ("最多" / "从高到低排序"). Otherwise unordered.
- `expected_rows` lists ONLY the `required_columns` values.

## 4. Required / Optional / Forbidden design

- Every case MUST satisfy: required != empty (parser enforces).
- Cases cannot declare the same column as both required AND
  optional/forbidden (parser enforces).
- `expected_rows` row-width MUST equal `len(required_columns)`
  (parser enforces).

## 5. Row matching semantics

| Question phrasing | row_matching |
|---|---|
| "最多" / "从高到低排序" | ordered |
| "哪些" / "按 ... 过滤" / "前 N 条" | unordered |
| Aggregation (`COUNT(*)`) | unordered (single row) |

## 6. Strict vs Semantic evaluation

| Mode | Required cols | Forbidden cols | Extra cols |
|---|---|---|---|
| **strict_exact_projection** (3.9.14) | == expected row tuple | rejected | rejected |
| **semantic_result_correctness** (3.9.16/3.9.17) | subset (extras OK unless forbidden) | rejected | warning only |

Both metrics measure different layers and are kept side-by-side.

## 7. Per-case Results

| Case | Strict (3.9.14) | Semantic (3.9.17) | Required | Optional | Forbidden | Categories |
|---|---|---|---|---|---|---|
| `aggregate_document_count` | PASS | PASS | count | - | - | - |
| `chunks_ordered_by_token_count` | PASS | PASS | id,token_count | document_id,content | - | - |
| `date_filter_created_after` | PASS | PASS | id | title,file_type,created_at | - | - |
| `group_by_chunk_count_per_document` | FAIL | PASS | id,chunk_count | title | - | - |
| `having_chunk_count_greater_than` | FAIL | PASS | id | title,chunk_count | - | - |
| `join_chunk_with_parent_document` | PASS | PASS | id,title | content,document_id | - | - |
| `limit_first_10_documents` | PASS | PASS | id | title,file_type,created_at | - | - |
| `project_a_inventory` | PASS | PASS | item_code,qty | - | - | - |
| `project_b_inventory` | PASS | PASS | item_code,qty | - | - | - |
| `semantic_dependent_document_and_chunk` | FAIL | PASS | id,chunk_count | title | - | - |
| `simple_document_list` | PASS | PASS | id | title,file_type,created_at | - | - |
| `top_n_chunks_by_token_count` | PASS | PASS | id,token_count | document_id,content | - | - |

## 8. Aggregate metrics

| Metric | Value |
|---|---|
| Source snapshot | phase_3_9_14_result_llm_baseline.json (saved DeepSeek run, NOT re-executed) |
| Fixture | `t2s_eval` (3.9.13) |
| Ground truth | `result_ground_truth.yaml` (3.9.13) + `text_to_sql_regression.yaml` (3.9.17 extended) |
| 3.9.14 strict result | 9 / 12 = 75.00% |
| Result-evaluable cases | 12 |
| Semantic ground truth coverage | 12 / 12 = 100.00% |
| Semantic correct | 12 |
| Semantic incorrect | 0 |
| **Semantic result correctness** | **100.00%** |

## 9. Comparison with 3.9.14

| Metric | 3.9.14 Strict Projection | 3.9.17 Semantic Result |
|---|---:|---:|
| Denominator | 12 (evaluable) | 12 |
| Correct | 9 | 12 |
| Incorrect | 3 | 0 |
| Accuracy | 75.00% | 100.00% |

The two metrics are independent layers and remain separate.

## 10. Coverage

```text
Semantic Ground Truth Coverage = 12/12 = 100.00%
```

Coverage is NOT accuracy. All 12 cases have a semantic ground
truth; the semantic_correctness ratio above is what matters.

## 11. Limitations

- `aggregate_document_count` requires the column to be named
  `count` (PostgreSQL default for `COUNT(*)`). Any LLM that
  rewrites it as `total_documents` would fail with
  `MISSING_REQUIRED_COLUMN`. General column-name alias mapping
  is NOT implemented in 3.9.17 — recorded as
  `PHASE_3_9_18_CANDIDATE`.
- No new prompt / SQL / model / executor change.
- Semantic evaluation only checks REQUIRED-column values, row set
  equality, duplicates, and forbidden columns. It does NOT
  verify SQL is optimal, stable, or well-indexed.

## 12. Phase 3.9.18 recommendation

- Add column-name alias mapping (`id <-> document_id`) so
  semantic matching is robust against common projection aliases.
- Consider per-category distribution (warning vs hard) in the
  per-case report to surface "near-miss" cases.
- If the prompt layer becomes aware of `semantic_expectation`, the
  LLM could be steered toward the required projection more
  reliably — investigate without forcing the issue.
