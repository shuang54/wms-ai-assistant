# Phase 3.9.16 — Semantic Result Evaluation Model（ADR）

## 目标

基于 Phase 3.9.15 诊断结果，把 Result-level Evaluation 拆为：

| 维度 | 含义 |
|---|---|
| strict_exact_projection | 行 tuple 与 GT 完全一致（沿用 3.9.10 / 3.9.14） |
| semantic_result_correctness | 仅业务必需的列 + 值正确；允许 optional / 未声明 extra；拒绝 forbidden |

不再让 projection 形状差异拖累“值正确”的判定。

## 背景

3.9.14 暴露 3 个 mismatch：

```
group_by_chunk_count_per_document
having_chunk_count_greater_than
semantic_dependent_document_and_chunk
```

它们的 **值** 都正确，但投影比 GT 多/少一个 title / id，被 strict 模式一律记为 FAIL，导致：

```
Strict Projection Accuracy = 9/12 = 75%
```

3.9.15 诊断已确认这些是「表达方式不同，业务答案相同」。3.9.16 通过引入 semantic 视角，让这两个问题独立计分。

## 设计

### Ground Truth 语义模型（3.9.16 新增）

```yaml
semantic_expectation:
  required_columns: [col_a, col_b]   # 必须出现
  optional_columns: [col_c]          # 允许但非必须
  forbidden_columns: []              # 必须不出现
  expected_rows: [[v_a, v_b], ...]  # required 列的值序列
  row_matching: unordered            # 或 ordered
```

与 `result_expectation`（strict）并列存在，**互不污染**。

### Checker 行为

```text
semantic_result_correctness (3.9.16):
  required_columns 必须出现
  forbidden_columns 出现即失败
  optional_columns 出现不加分也不扣分
  未声明的额外列：默认允许，但记录 UNDECLARED_EXTRA warning（不算硬失败）
  expected_rows 仅约束 required_columns 的值序列

strict_exact_projection (3.9.10/3.9.14):
  行 tuple 必须 == expected rows
  任意额外列 → FAIL
  任意缺列 → FAIL
```

### 失败类别（ResultComparison / semantic_categories）

| 类别 | 严重性 | 触发条件 |
|---|---|---|
| MISSING_REQUIRED_COLUMN | hard | required_columns 缺一个 |
| EXTRA_FORBIDDEN_COLUMN | hard | forbidden_columns 出现 |
| WRONG_COLUMN_VALUE | hard | expected multiset 不含该值 |
| WRONG_ROW_SET | hard | unordered multiset 不一致 |
| COLUMN_ORDER_MISMATCH | hard | ordered 模式 + 顺序错 |
| UNDECLARED_EXTRA_COLUMN | warning | 有列既非 required 也非 optional 也非 forbidden |
| DUPLICATE_ROW | hard | unordered 中出现重复行 |

多标签共存：一次 semantic 校验可累加多个类别。

### 向后兼容

| API | 状态 |
|---|---|
| `exact_rows` / `unordered_rows` / `scalar` / `column_values` | 不变 |
| `ResultCheckResult.passed` | 不变（strict 语义） |
| `ResultCheckResult.expected` / `actual` | 不变 |
| 新增 `ResultCheckResult.semantic_passed` | 默认 `None`（N/A） |
| 新增 `ResultCheckResult.semantic_reason` | 默认 `""` |
| 新增 `ResultCheckResult.semantic_categories` | 默认 `()` |

## 严格范围（与 3.9.16 prompt 一致）

允许修改：

- `backend/app/services/text_to_sql_result_evaluation_service.py`（DTO + checker + 解析）
- `backend/app/services/text_to_sql_semantic_result_evaluation_service.py`（新增）
- `tests/fixtures/text_to_sql/text_to_sql_regression.yaml`（3 个 case 加 `semantic_expectation`）
- `scripts/analyze_text_to_sql_semantic_result.py`（新增，离线分析）
- `tests/test_text_to_sql_semantic_result_evaluation.py`（新增）

不允许修改：

- `TextToSQLService` / `Generator` / `Validator` / `Executor`
- AI Router / Orchestrator / Prompt / Schema Composer / Semantic Serializer
- Project Context / Knowledge / Production API
- fixture database schema / fixture data / regression dataset questions
- Phase 3.9.14 baseline / 任何历史 baseline snapshot 内容

## 三个 ground truth 的语义重映射

| case_id | question | required | optional | forbidden |
|---|---|---|---|---|
| `group_by_chunk_count_per_document` | 统计每个文档的知识分片数量 | `id, chunk_count` | `title` | - |
| `having_chunk_count_greater_than` | 分片数 > 2 的文档 | `id` | `title, chunk_count` | - |
| `semantic_dependent_document_and_chunk` | 每个文档包含多少个分片 | `id, chunk_count` | `title` | - |

注：实际 LLM 投影列为 `id`（非 `document_id`），以 saved actual 列名为准。

## 验证

```text
Phase 3.9.14 strict projection (3.9.14 报告) : 9 / 12 = 75.00%
Phase 3.9.16 semantic result correctness       : 3 / 3  = 100.00%
```

两个指标并存，互不替代。

## 数据漂移

dataset_sha256 因新增 `semantic_expectation` 字段发生漂移（§25 允许）：

```text
1d0d1919cc789669f41b497cf4e089fc04537cf04851d4bbaaf177ac8176e731  (3.9.10/3.9.11/3.9.14 baseline)
838c50946bc53b2deda3dfe8d5b154b047c2c0866c2946d2f074fac08942d419  (post-3.9.16)
```

## Phase 3.9.17 建议

- 把 `semantic_expectation` 普及到剩余 9 个 case；
- 引入 column-name 别名表（`id ↔ document_id`），使语义匹配对投影别名更鲁棒；
- 评估 LLM 是否能利用该模型改进 prompt（明确声明 optional 而非多塞）。