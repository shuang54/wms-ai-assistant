# Text-to-SQL Result-level Correctness — Phase 3.9.10

## Scope

> 本阶段评估 SQL **执行结果**是否与 deterministic expected result 一致。

- 评估方式：deterministic checker（exact_rows / unordered_rows / scalar / column_values）
- **不使用 LLM-as-a-Judge**，不让模型判断结果正确性
- 复用既有 Text-to-SQL Pipeline / Validator / Executor，不修改生产逻辑
- 只读取测试数据库，被评估 SQL 全部先过既有 Validator（只读）

## Dataset

- File: `tests/fixtures/text_to_sql/text_to_sql_regression.yaml`
- Version: `1.0`
- Cases: 14
- Result expectation cases: 3

## Metrics

| Metric | Result |
|---|---:|
| Applicable Result Cases | 3 |
| Correct Result Cases | 3 |
| Incorrect Result Cases | 0 |
| Not Evaluable Cases | 0 |
| **Result Accuracy** | **100.00%** |

### 与 3.9.9 其它层级保持独立（§15）

| Metric | Result |
|---|---:|
| Generation Success Rate | 100.00% |
| Validator Acceptance Rate | 100.00% |
| Expectation Match Rate | 92.86% |
| Execution Success Rate | 100.00% |
| Result Accuracy | 100.00% |

> **Result Accuracy ≠ Expectation Match Rate**：SQL 结构符合预期，
> 不代表查询结果正确。

## Per-case Results

| case_id | type | applicable | passed | reason |
|---|---|---|---|---|
| `simple_document_list` | exact_rows | True | True | exact rows matched |
| `top_n_chunks_by_token_count` | - | False | - | no result_expectation defined |
| `filtered_documents_by_file_type` | - | False | - | no result_expectation defined |
| `chunks_ordered_by_token_count` | - | False | - | no result_expectation defined |
| `aggregate_document_count` | scalar | True | True | scalar matched |
| `group_by_chunk_count_per_document` | - | False | - | no result_expectation defined |
| `having_chunk_count_greater_than` | - | False | - | no result_expectation defined |
| `join_chunk_with_parent_document` | - | False | - | no result_expectation defined |
| `date_filter_created_after` | - | False | - | no result_expectation defined |
| `limit_first_10_documents` | exact_rows | True | True | exact rows matched |
| `semantic_dependent_document_and_chunk` | - | False | - | no result_expectation defined |
| `safety_delete_all_documents` | - | False | - | no result_expectation defined |
| `project_a_inventory` | - | False | - | no result_expectation defined |
| `project_b_inventory` | - | False | - | no result_expectation defined |

## Limitations

- **不是所有 14 个 case 都具备 deterministic expected result**；
  仅 3/14 个 case 定义了 result_expectation。
- **N/A 不代表失败**：没有 result_expectation 的 case 记 `applicable=false / passed=null`，不进入准确率分母。
- 当前只验证**测试数据库**中的稳定结果（测试库业务表为空，期望值据此确定）。
- 不代表生产数据库上的全面正确性，也不代表所有 SQL 语义都正确。
- 空表场景下「0 行 / COUNT=0」类期望的区分度有限：能证明执行与结果形状正确，不能区分不同但同样返回空结果的 SQL。
- DeepSeek 输出不稳定，重复运行指标可能变化。
