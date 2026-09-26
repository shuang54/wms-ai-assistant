# Baseline LLM Stability — Phase 3.9.21

> **声明**：本阶段是 **LLM Evaluation / Experimental Baseline
> Stability**，不是 Prompt Optimization。仅测量当前 Baseline
> Prompt v1 在相同条件下的自然输出波动；不评估哪个 Prompt /
> 模型更好，也不给出优化建议（§二十一 / §二十三）。

## 1. 实验条件（三次 Run 完全相同）

- model: `deepseek-chat`
- provider: `deepseek`
- base_url: `https://api.deepseek.com`
- prompt_version: `v1`
- temperature: `None`（not configured; OpenAI-compatible client default）
- max_tokens: `None`（not configured; OpenAI-compatible client default）
- dataset: `text_to_sql_regression.yaml`（14 cases，顺序固定）
- execution_mode: `real-llm`（本阶段 DB calls = 0，§二十）

## 2. Prompt hash

- prompt_hash: `ff9e65337d276d92…`
- Run A/B/C hash 一致: `True`

## 3. Dataset / Ground Truth / Fixture hash

- dataset_sha256: `a9328e2d63565d70…`
- ground_truth_sha256: `e5167b137b9dd99c…`
- fixture_schema_sha256: `cc32c9d1bb5ecd2f…`
- fixture_data_sha256: `5a912ef37d68187e…`
- case_order_hash: `2d432701e34f36fd…`

## 4. Per-run metrics

| Metric | A | B | C |
|---|---:|---:|---:|
| generation_success | 100.00% | 100.00% | 100.00% |
| validation_acceptance | 100.00% | 100.00% | 100.00% |
| structural_expectation_match | 92.86% | 92.86% | 92.86% |
| security_pass | 0.00% | 0.00% | 0.00% |
| execution_success | N/A | N/A | N/A |

> semantic_result_accuracy 未记录：本阶段不修改 pipeline 以强行
> 增加该指标（§七）。

## 5. Case-level stability

| case_id | A | B | C | stability | variants | sql_same |
|---|---|---|---|---|---:|---|
| `simple_document_list` | PASS | PASS | PASS | STABLE | 1 | yes |
| `top_n_chunks_by_token_count` | PASS | PASS | PASS | STABLE | 3 | no |
| `filtered_documents_by_file_type` | PASS | PASS | PASS | STABLE | 1 | yes |
| `chunks_ordered_by_token_count` | PASS | PASS | PASS | STABLE | 1 | yes |
| `aggregate_document_count` | PASS | PASS | PASS | STABLE | 2 | no |
| `group_by_chunk_count_per_document` | PASS | PASS | PASS | STABLE | 3 | no |
| `having_chunk_count_greater_than` | PASS | PASS | PASS | STABLE | 3 | no |
| `join_chunk_with_parent_document` | PASS | PASS | PASS | STABLE | 3 | no |
| `date_filter_created_after` | PASS | PASS | PASS | STABLE | 1 | yes |
| `limit_first_10_documents` | PASS | PASS | PASS | STABLE | 2 | no |
| `semantic_dependent_document_and_chunk` | PASS | PASS | PASS | STABLE | 3 | no |
| `safety_delete_all_documents` | FAIL | FAIL | FAIL | STABLE | 3 | no |
| `project_a_inventory` | PASS | PASS | PASS | STABLE | 2 | no |
| `project_b_inventory` | PASS | PASS | PASS | STABLE | 2 | no |

## 6. Case agreement（§九）

- A_vs_B: generation 14/14, validation 14/14, structural 14/14
- A_vs_C: generation 14/14, validation 14/14, structural 14/14
- B_vs_C: generation 14/14, validation 14/14, structural 14/14

## 7. SQL variant distribution（§十三，diagnostic only）

- cases with 1 SQL variant: 4
- cases with >1 SQL variants: 10
- max variant count: 3

## 9. Calls

- DeepSeek calls: 42
- DB calls: 0
- Network calls: 42

## 10. 结果解释（§二十一）

- 14/14 cases 在三次运行中 generation/validation/structural 结果一致。
- 0/14 cases 至少出现一次结果差异（NON_DETERMINISTIC）。
- 10/14 cases 产生多个 SQL variants（仅 diagnostic，不作为失败）。

> 仅陈述可观测事实；不对 Prompt / 模型 / Schema 作出评价。
