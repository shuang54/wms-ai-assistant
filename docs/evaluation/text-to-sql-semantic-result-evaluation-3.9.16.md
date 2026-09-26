# Text-to-SQL Semantic Result Evaluation - Phase 3.9.16

## 1. Objective

> Make Result-level evaluation correctly distinguish two questions:
> (a) Are the SQL **values** correct?  (b) Is the SQL **projection**
> strictly the columns the GT happened to list?

Phase 3.9.14 strict projection conflated both; 3.9.16 introduces a
**semantic** view alongside, without modifying the 3.9.14 artifact.

## 2. Ground Truth Semantic Model

Each case may carry both:

- ``expectation`` (strict 4 types, unchanged from 3.9.10)
- ``semantic_expectation`` (new, 3.9.16)

```yaml
semantic_expectation:
  required_columns: [col_a, col_b]   # must be present
  optional_columns: [col_c]          # allowed but not required
  forbidden_columns: []              # must NOT be present
  expected_rows: [[v_a, v_b], ...]  # required-column values
  row_matching: unordered            # or 'ordered'
```

**strict_exact_projection** (3.9.14): row tuple must match exactly.
**semantic_result_correctness** (3.9.16): only required columns +
values checked; optional allowed; forbidden rejected; undeclared
extra recorded as UNDECLARED_EXTRA warning (not a hard failure).

## 3. Two Evaluation Modes

| Mode | Required cols | Forbidden cols | Extra cols
|---|---|---|---|
| strict | == expected row tuple | rejected if extra | rejected |
| semantic | subset (any extras OK unless forbidden) | rejected | warning only |

## 4. Failure Categories (semantic)

- ``MISSING_REQUIRED_COLUMN`` - required column absent
- ``EXTRA_FORBIDDEN_COLUMN`` - forbidden column present
- ``WRONG_COLUMN_VALUE`` / ``WRONG_ROW_SET`` - value mismatch
- ``COLUMN_ORDER_MISMATCH`` - ordered semantic + wrong order
- ``UNDECLARED_EXTRA_COLUMN`` - warning, not hard failure
- ``DUPLICATE_ROW`` - duplicate row detected

Multi-label: a case may accumulate several categories.

## 5. Environment

| Item | Value |
|---|---|
| Source | phase_3_9_14_result_llm_baseline.json (saved DeepSeek run, NOT re-executed) |
| Fixture | `t2s_eval` (3.9.13) |
| Ground truth | `result_ground_truth.yaml` (3.9.13) |
| Dataset | `text_to_sql_regression.yaml` (1.0) |

## 6. Metrics

| Metric | Result |
|---|---|
| Total cases | 3 |
| Semantic evaluable cases | 3 |
| Semantic correct cases | 3 |
| Semantic incorrect cases | 0 |
| **Semantic result correctness** | **100.00%** |

## 7. Per-case Results

| case_id | required | optional | forbidden | semantic_passed | categories |
|---|---|---|---|---|---|
| `group_by_chunk_count_per_document` | id,chunk_count | title | - | True | - |
| `having_chunk_count_greater_than` | id | title,chunk_count | - | True | - |
| `semantic_dependent_document_and_chunk` | id,chunk_count | title | - | True | - |

## 8. Re-mapped Mismatches (from Phase 3.9.15 diagnosis)

Three cases were `mismatched` under strict projection in 3.9.14
but their **values** are correct. Under semantic mode:

- `group_by_chunk_count_per_document`: **PASS** (required columns + values match; extra column accepted as semantic-compliant).
- `having_chunk_count_greater_than`: **PASS** (required columns + values match; extra column accepted as semantic-compliant).
- `semantic_dependent_document_and_chunk`: **PASS** (required columns + values match; extra column accepted as semantic-compliant).

## 9. Comparison with 3.9.14 (strict projection)

| Metric | 3.9.14 Strict Projection | 3.9.16 Semantic Result |
|---|---:|---:|
| Denominator | 12 (evaluable) | 3 |
| Correct | 9 | 3 |
| Incorrect | 3 | 0 |
| Accuracy | 75.00% | 100.00% |

Two metrics measure different layers; both are kept (section 6).

## 10. Backward Compatibility

- ``exact_rows`` / ``unordered_rows`` / ``scalar`` / ``column_values`` checker unchanged.
- Strict ``passed`` semantics unchanged in ``ResultCheckResult.passed``.
- New ``semantic_passed`` / ``semantic_categories`` fields added alongside, never replacing.
- Old tests still pass; new tests added for semantic mode.

## 11. Limitations

- Only 3 cases have ``semantic_expectation``; the remaining 9 stay N/A.
- Undeclared extra columns are allowed (deliberate, by design).
- 3.9.14 --check fails on dataset SHA256 because ground truth added
  new ``semantic_expectation`` blocks (section 25 explicitly allows
  this and requires the new hash to be recorded).
