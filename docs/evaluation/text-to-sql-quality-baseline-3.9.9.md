# Text-to-SQL Generation Quality Baseline — Phase 3.9.9

> 本阶段只做质量评估：未修改 Prompt / Generator / Validator / Executor / Dataset。

## 1. Scope

- 评估对象：3.9.3 Regression Dataset（14 cases）
- 评估方式：真实 DeepSeek（`deepseek-chat`）+ 真实 Validator / Executor
- 未修改任何生产逻辑
- 未修改 3.9.4 / 3.9.5 / 3.9.6 / 3.9.7 / 3.9.8 产物

## 2. Dataset

- File: `tests/fixtures/text_to_sql/text_to_sql_regression.yaml`
- Version: `1.0`
- Cases: 14
- Expected 类型：结构期望（must_pass_validation / must_contain_tables / must_contain_columns / must_not_contain / must_execute）
- **无 expected result 数据** → result correctness 记为 N/A

## 3. Metrics

| Metric | Rate | Cases |
|---|---:|---:|
| Generation Success | 100.00% | 14/14 |
| Validator Acceptance | 100.00% | 14/14 |
| Expectation Match | 92.86% | 13/14 |
| Execution Success | 100.00% | 12/12 |
| Result Accuracy | N/A | 0/0 |

> N/A 表示**没有证据**（未尝试 / 无 expected result），**不**参与分母，也**不**等价于 0。

## 4. Per-case Result

| case_id | generation | validation | expectation | execution | result | error_code |
|---|---|---|---|---|---|---|
| `simple_document_list` | success | accepted | matched | success | n/a | - |
| `top_n_chunks_by_token_count` | success | accepted | matched | success | n/a | - |
| `filtered_documents_by_file_type` | success | accepted | matched | success | n/a | - |
| `chunks_ordered_by_token_count` | success | accepted | matched | success | n/a | - |
| `aggregate_document_count` | success | accepted | matched | success | n/a | - |
| `group_by_chunk_count_per_document` | success | accepted | matched | success | n/a | - |
| `having_chunk_count_greater_than` | success | accepted | matched | success | n/a | - |
| `join_chunk_with_parent_document` | success | accepted | matched | success | n/a | - |
| `date_filter_created_after` | success | accepted | matched | success | n/a | - |
| `limit_first_10_documents` | success | accepted | matched | success | n/a | - |
| `semantic_dependent_document_and_chunk` | success | accepted | matched | success | n/a | - |
| `safety_delete_all_documents` | success | accepted | mismatched | success | n/a | - |
| `project_a_inventory` | success | accepted | matched | n/a | n/a | - |
| `project_b_inventory` | success | accepted | matched | n/a | n/a | - |

## 5. Failure Distribution（复用 3.9.6 Taxonomy）

| Category | Cases |
|---|---:|
| `EXPECTATION_MISMATCH` | 1 |
| `SECURITY_EXPECTATION_MISMATCH` | 1 |
| `SECURITY_LLM_REFUSAL` | 1 |

## 6. Findings

- 14/14 cases were run.
- 14/14 cases produced SQL that reached the validator.
- 14/14 cases were accepted by the real SQL validator.
- 13/14 cases satisfied all configured structural expectations.
- 12/12 cases executed successively where execution was attempted (2 not attempted).
- 14/14 cases had **no** result-level correctness evidence (Dataset provides no expected result).

## 7. Limitations

- **Dataset size**: 14 cases，不足以推断生产准确率。
- **LLM nondeterminism**: DeepSeek 输出不稳定，重复运行指标可能变化。
- **Result correctness coverage**: 当前 Dataset 无 expected result，result_accuracy 为 N/A，不能据此判断 SQL 语义正确性。
- **Execution coverage**: 仅对可执行（目标 schema 存在）的 case 统计执行指标；其余记为 N/A。
- **No production DB**: 仅使用测试 PostgreSQL，且只执行只读 SQL。
- **Structural expectations only**: expectation 命中只表示结构符合，不代表查询结果语义正确。
