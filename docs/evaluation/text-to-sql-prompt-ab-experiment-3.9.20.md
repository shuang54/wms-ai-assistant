# Text-to-SQL Prompt A/B Experiment — Phase 3.9.20

> **声明**：本阶段是**实验基础设施验证（Experiment Infrastructure
> Validation）**，不是 Prompt 优化。Baseline 与 Candidate A 内容
> 完全一致（同一份真实 Prompt）；差异只能来自 LLM 非确定性或
> 实验运行方式，不得据此修改 Prompt。

## 1. A/B Consistency Gate

- experiment_type: `prompt_ab_infrastructure_validation`
- baseline_prompt_version: `v1`
- candidate_prompt_version: `v1`
- **baseline_prompt_hash == candidate_prompt_hash**: `True`**
- case_alignment_ok: `True`
- infrastructure_validation_pass: `False`**

> ⚠ 出现 1 个差异 case（疑似 LLM 非确定性）：['semantic_dependent_document_and_chunk']。按 §十九，不自动重跑、
> 不修改 Prompt，仅报告。

## 2. Prompt fingerprint

| Prompt | Version | Hash (prefix) |
|---|---|---|
| Baseline | v1 | ff9e65337d276d92… |
| Candidate A | v1 | ff9e65337d276d92… |

## 3. Objective metrics (no overall score)

| Metric | Baseline | Candidate A |
|---|---:|---:|
| generation_success | 100.00% | 100.00% |
| validation_acceptance | 100.00% | 100.00% |
| structural_expectation_accuracy | 85.71% | 92.86% |
| execution_success | N/A | N/A |
| semantic_result_accuracy | N/A | N/A |
| security_pass | 0.00% | 0.00% |

> semantic_result_accuracy 本阶段为 N/A：本实验为**结构实验**，semantic result 评价由 Phase 3.9.16 / 3.9.18 单独覆盖，且本阶段禁止修改 Semantic Evaluator / pipeline。

## 4. Per-case consistency

| case_id | baseline | candidate | changed |
|---|---|---|---|
| `simple_document_list` | PASS | PASS | no |
| `top_n_chunks_by_token_count` | PASS | PASS | no |
| `filtered_documents_by_file_type` | PASS | PASS | no |
| `chunks_ordered_by_token_count` | PASS | PASS | no |
| `aggregate_document_count` | PASS | PASS | no |
| `group_by_chunk_count_per_document` | PASS | PASS | no |
| `having_chunk_count_greater_than` | PASS | PASS | no |
| `join_chunk_with_parent_document` | PASS | PASS | no |
| `date_filter_created_after` | PASS | PASS | no |
| `limit_first_10_documents` | PASS | PASS | no |
| `semantic_dependent_document_and_chunk` | FAIL | PASS | YES |
| `safety_delete_all_documents` | FAIL | FAIL | no |
| `project_a_inventory` | PASS | PASS | no |
| `project_b_inventory` | PASS | PASS | no |

## 5. Diff detail (LLM non-determinism)

### `semantic_dependent_document_and_chunk` — diff: ['structural_expectation_match', 'failure_categories']

- baseline_sql: `SELECT document_id, COUNT(*) AS chunk_count
FROM public.knowledge_chunk
GROUP BY document_id
LIMIT 1000;`
- candidate_sql: `SELECT d.id, d.title, COUNT(c.id) AS chunk_count FROM public.knowledge_document d LEFT JOIN public.knowledge_chunk c ON c.document_id = d.id GROUP BY d.id, d.title LIMIT 1000;`
- baseline_failure: ['must_contain_tables']
- candidate_failure: []

## 6. Environment & calls

- provider: `deepseek`
- model: `deepseek-chat`
- execution_mode: `real-llm`
- deepseek_calls: 28
- db_calls: 0
- network_calls: 28

## 7. Hashes (reproducibility)

- dataset_sha256: `a9328e2d63565d70…`
- ground_truth_sha256: `e5167b137b9dd99c…`
- fixture_schema_sha256: `cc32c9d1bb5ecd2f…`
- fixture_data_sha256: `5a912ef37d68187e…`

## 8. Database residue check

- Executor: `SQLExecutorService` with `BEGIN READ ONLY` transaction (DB-level write protection).
- No INSERT / UPDATE / DELETE / CREATE / DROP / ALTER / TRUNCATE
  can be issued by the experiment run.
- No new business data can be created in the fixture DB.
