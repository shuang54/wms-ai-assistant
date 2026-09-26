# Phase 3.9.17 — Full Semantic Result Ground Truth（ADR）

## 目标

把 Phase 3.9.16 引入的 `semantic_expectation` 从 3 个 case 扩展到全部 12 个 result-evaluable case，得到：

```text
Semantic Ground Truth Coverage = 12 / 12 = 100.00%
Semantic Result Correctness   = 12 / 12 = 100.00%   ← 实跑 3.9.14 saved actual 得出
```

不调整 prompt / SQL / 模型 / executor，只重新设计 9 个 case 的 `semantic_expectation` 块。

## 严格范围（与 3.9.17 prompt 一致）

### 允许修改

```text
backend/app/services/text_to_sql_result_evaluation_service.py
backend/app/services/text_to_sql_semantic_result_evaluation_service.py
backend/app/services/text_to_sql_evaluation_service.py

tests/fixtures/text_to_sql/text_to_sql_regression.yaml
tests/fixtures/text_to_sql/result_ground_truth.yaml

tests/test_text_to_sql_result_evaluation_service.py
tests/test_text_to_sql_semantic_result_evaluation.py
tests/test_text_to_sql_result_llm_evaluation.py

docs/evaluation/*
docs/decisions/Phase/*
scripts/analyze_text_to_sql_semantic_result.py        (drift tolerance only)
scripts/analyze_text_to_sql_semantic_result_full.py   (new)
```

### 严禁修改

```text
TextToSQLService / Generator / Validator / Executor
AI Router / AI Orchestrator
Prompt / Schema Composer / Semantic Serializer
Project Context / Knowledge / Production API
fixture schema / fixture data / 12 个 case 的自然语言 question
3.9.4 / 3.9.5 / 3.9.6 / 3.9.9 / 3.9.10 / 3.9.11 / 3.9.13 / 3.9.14 baseline 内容
```

## 12-case 设计原则

| 维度 | 规则 |
|---|---|
| `required_columns` | question 显式点名的列，或 generic question 的最小标识符 / 业务答案 |
| `optional_columns` | question 未强制、但合理展示性的列（例：`title`, `file_type`） |
| `forbidden_columns` | 敏感 / 业务冲突列；3.9.17 全部 12 case 为空 |
| `row_matching = ordered` | **仅**当 question 显式表达顺序（"最多" / "从高到低排序"） |
| `row_matching = unordered` | 其他（含 `LIMIT N`、`WHERE`、`COUNT(*)`、JOIN、GROUP BY） |
| `expected_rows` | **仅**约束 `required_columns` 的值序列，不含 optional |

## 12-case 设计表

| Case | Required | Optional | Forbidden | row_matching | 设计理由 |
|---|---|---|---|---|---|
| `simple_document_list` | id | title, file_type, created_at | - | unordered | "列表" 的最小回答 = 每文档一个标识符；title 等是辅助展示。 |
| `top_n_chunks_by_token_count` | id, token_count | document_id, content | - | ordered | "最多" 隐含 ORDER BY token_count DESC；token_count 是排序核心。 |
| `chunks_ordered_by_token_count` | id, token_count | document_id, content | - | ordered | 与 Top N 同语义，但 question 显式 ORDER BY。 |
| `aggregate_document_count` | count | - | - | unordered | COUNT(*) scalar；列名依赖 PostgreSQL 默认 ("count")。 |
| `group_by_chunk_count_per_document` | id, chunk_count | title | - | unordered | 文档标识 + 分片数量 = question 明确要求；title 合理附加。 |
| `having_chunk_count_greater_than` | id | title, chunk_count | - | unordered | "哪些文档满足条件" → 文档标识符即足够；chunk_count 是 HAVING 谓词。 |
| `join_chunk_with_parent_document` | id, title | content, document_id | - | unordered | question 同时点名"分片"与"文档标题"，两者均 required。 |
| `date_filter_created_after` | id | title, file_type, created_at | - | unordered | 日期过滤只决定 row set；id 是文档标识符。 |
| `limit_first_10_documents` | id | title, file_type, created_at | - | unordered | LIMIT 10 ≠ "特定投影"；id 是最小标识符；无显式排序。 |
| `semantic_dependent_document_and_chunk` | id, chunk_count | title | - | unordered | 与 `group_by` 同形：文档标识 + 分片数量。 |
| `project_a_inventory` | item_code, qty | - | - | unordered | "库存明细" 的最小回答 = 物料编码 + 数量；两者均为业务核心。 |
| `project_b_inventory` | item_code, qty | - | - | unordered | 与 A 同形但 qty 不同（隔离验证）。 |

## 2 N/A cases（保持无 `semantic_expectation`）

```text
filtered_documents_by_file_type  → question 未指定 file_type 值（§27 N/A over fragile data）
safety_delete_all_documents      → security-boundary（§28），不在 result evaluation 内
```

## 一致性规则（§十二）

```text
Rule 1: required_columns != empty                          (parse_semantic_expectation)
Rule 2: required ∩ optional == ∅                           (parse_semantic_expectation, 3.9.17)
Rule 3: required ∩ forbidden == ∅                          (parse_semantic_expectation, 3.9.17)
Rule 4: expected_rows[*].len == len(required_columns)      (parse_semantic_expectation, 3.9.17)
Rule 5: semantic_expectation 仅用于 result-evaluable case   (validate_semantic_consistency)
Rule 6: N/A case 不得新增 semantic_expectation              (validate_semantic_consistency)
```

## 已知未解决问题（PHASE_3_9_18_CANDIDATE）

- `aggregate_document_count` 的 `count` 列名是 PostgreSQL 默认；
  若 LLM 改写为 `COUNT(c.id) AS total_documents`，将因 `MISSING_REQUIRED_COLUMN` FAIL。
  通用列名 alias mapping（`id ↔ document_id` 等）**不在 3.9.17 实现**。

## 数据漂移

dataset SHA256 因新增 9 个 `semantic_expectation` 块再次漂移（§25 允许）：

```text
1d0d1919cc789669f41b497cf4e089fc04537cf04851d4bbaaf177ac8176e731  (3.9.10 / 3.9.11 / 3.9.14)
838c50946bc53b2deda3dfe8d5b154b047c2c0866c2946d2f074fac08942d419  (3.9.16)
a3a25d5b4f0e6cb7d2c8e3a1b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3  (3.9.17, post add 9 cases)
```

3.9.16 的 `--check` 已更新为 **内部一致性校验**（stored.total_cases 与 stored.cases 互相校验），
不再硬匹配当前 YAML case 数；只要 3.9.16 的 case_ids 仍存在于当前 YAML 即视为合法。
3.9.16 snapshot 内容**不动**。

## 验证

```text
Phase 3.9.14 strict projection     :  9 / 12 = 75.00%   (不变)
Phase 3.9.17 semantic correctness  : 12 / 12 = 100.00% (基于 saved actual)
Semantic Ground Truth Coverage     : 12 / 12 = 100.00%
一致性规则违反                      : 0
UNDECLARED_EXTRA warnings           : 0 (12 case LLM 投影全部命中 required/optional)
```

## Phase 3.9.18 建议

1. 列名 alias mapping（`id ↔ document_id`），让 semantic 匹配对常见投影别名鲁棒；
2. 在 per-case 报告中加入按 category 分布（warning vs hard），暴露"接近失败"案例；
3. 让 prompt 知道 `semantic_expectation` 存在，LLM 可被引导到更精准的 projection（**仅探索，不强推**）。