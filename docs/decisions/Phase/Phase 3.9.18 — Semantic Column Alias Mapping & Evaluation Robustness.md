# Phase 3.9.18 — Semantic Column Alias Mapping & Evaluation Robustness

## 目标

基于 Phase 3.9.17 的完整 12-case Semantic Result Evaluation，增强 evaluation 层对“列名表达差异”的识别能力。

本阶段不是模型优化。

目标是解决：

```text
document.id
document_id
id

chunk.id
chunk_id
id

COUNT(*)
count
total_count
total_documents
```

这类可能具有相同业务语义、但 SQL 输出列名不同的情况。

---

# 一、严格限制

## 允许修改

只允许 evaluation / test / documentation：

```text
backend/app/services/*evaluation*
tests/*
tests/fixtures/text_to_sql/*
docs/evaluation/*
docs/decisions/Phase/*
scripts/analyze_text_to_sql*
```

如果实际结构不同，以现有项目结构为准。

---

## 严禁修改

绝对不要修改：

```text
TextToSQLService
TextToSQLGenerator
TextToSQLValidator
TextToSQLExecutor
AI Router
AI Orchestrator
Prompt
Schema Composer
Semantic Serializer
Project Context
Knowledge
Production API
```

不要：

* 修改 Prompt
* 重新调用 DeepSeek
* 修改 DeepSeek 参数
* 修改 SQL Generator
* 修改 Validator
* 修改 Executor
* 修改 fixture schema
* 修改 fixture data
* 修改 12 个 question
* 修改 Phase 3.9.14 baseline
* 修改 Phase 3.9.17 baseline
* 引入 LLM-as-a-Judge

---

# 二、先阅读现有实现

先阅读：

```text
docs/evaluation/text-to-sql-semantic-result-full-3.9.17.md
docs/evaluation/text-to-sql-semantic-result-evaluation-3.9.16.md

backend/app/services/text_to_sql_result_evaluation_service.py
backend/app/services/text_to_sql_semantic_result_evaluation_service.py

tests/fixtures/text_to_sql/text_to_sql_regression.yaml
tests/fixtures/text_to_sql/result_ground_truth.yaml

tests/fixtures/text_to_sql/baselines/phase_3_9_17_semantic_result_full.json
```

重点确认：

1. 当前 semantic checker 如何匹配列
2. required_columns 如何定位 actual columns
3. expected_rows 如何映射到 actual rows
4. 是否已经存在部分 column normalization
5. aggregate case 当前如何处理
6. strict 与 semantic 的边界

先分析，不要立即改代码。

---

# 三、核心设计原则

## 原则 1：Alias 不是字符串全局替换

禁止设计成：

```python
"id" -> "document_id"
```

这种全局规则。

因为：

```text
documents.id
chunks.id
inventory.item_code
```

中的 `id` 语义不同。

---

# 四、Alias 必须有上下文

推荐模型：

```text
ColumnSemanticAlias
```

至少考虑：

```text
source_table
source_column
semantic_name
aliases
```

例如：

```yaml
documents:
  id:
    semantic_name: document_id
    aliases:
      - id
      - document_id

chunks:
  id:
    semantic_name: chunk_id
    aliases:
      - id
      - chunk_id
```

如果当前 evaluator 不需要这么复杂，不要过度设计。

核心要求：

> alias 必须能够区分不同业务实体。

---

# 五、Aggregate Alias

单独处理聚合结果。

例如：

```text
COUNT(*)
count
total_count
total_documents
```

不能简单认为全部等价。

必须结合语义：

```text
COUNT(documents)
→ total_documents

COUNT(chunks)
→ total_chunks

COUNT(*)
→ count
```

因此：

```text
total_documents
```

只能在明确的 document count context 下作为 alias。

不要建立：

```text
count == total_documents
```

这种全局规则。

---

# 六、设计最小 Alias Registry

可以增加一个纯 evaluation 层 registry，例如：

```text
backend/app/services/text_to_sql_column_alias_service.py
```

但只有在当前代码结构确实需要时才新增。

推荐提供：

```text
resolve_semantic_column(...)
```

或等价能力。

必须是：

```text
pure function
deterministic
no DB
no LLM
no network
```

---

# 七、Alias Matching 优先级

定义明确优先级：

```text
1. exact column name
2. explicit semantic alias
3. context-aware alias
4. no match
```

禁止：

```text
fuzzy string matching
```

禁止：

```text
Levenshtein similarity
```

禁止：

```text
embedding similarity
```

禁止：

```text
LLM judge
```

原因：

Evaluation 必须 deterministic。

---

# 八、不要改变 Strict Projection

非常重要：

Alias mapping **只作用于 semantic_result_correctness**。

Strict mode：

```text
strict_exact_projection
```

必须继续要求：

```text
exact column names
exact projection shape
```

因此：

```text
Expected:
document_id

Actual:
id
```

Strict：

```text
FAIL
```

Semantic：

```text
PASS
```

如果 alias 明确成立。

---

# 九、增加 Alias-specific Diagnostic

semantic comparison 中增加：

```text
ALIAS_MATCH
```

例如：

```text
expected semantic column:
document_id

actual:
id

diagnostic:
ALIAS_MATCH
```

这不是 warning，也不是 error。

只是记录：

```text
matched_by = exact
```

或者：

```text
matched_by = alias
```

---

# 十、必须防止错误 alias

至少增加这些负面测试：

## Test 1

```text
documents.id
```

可以匹配：

```text
document_id
```

---

## Test 2

```text
chunks.id
```

可以匹配：

```text
chunk_id
```

---

## Test 3

```text
documents.id
```

不能因为字符串都是 `id` 就匹配：

```text
chunk_id
```

---

## Test 4

```text
COUNT(documents)
```

可以匹配：

```text
total_documents
```

但：

```text
COUNT(chunks)
```

不能匹配：

```text
total_documents
```

---

## Test 5

未知 alias：

```text
document_code
```

不能自动匹配：

```text
document_id
```

---

# 十一、针对当前 12 cases 做 alias audit

不要重新运行 DeepSeek。

只分析：

```text
Phase 3.9.14 saved actual results
```

以及：

```text
Phase 3.9.17 semantic expectations
```

建立：

| Case | Expected semantic column | Actual column | Current match | Alias needed? |
| ---- | ------------------------ | ------------- | ------------- | ------------- |

重点确认：

```text
simple_document_list
top_n_chunks_by_token_count
chunks_ordered_by_token_count
aggregate_document_count
group_by_chunk_count_per_document
having_chunk_count_greater_than
join_chunk_with_parent_document
date_filter_created_after
limit_first_10_documents
semantic_dependent_document_and_chunk
project_a_inventory
project_b_inventory
```

---

# 十二、不要为了 alias 修改 Ground Truth

如果发现：

```text
expected = document_id
actual = id
```

首先应该判断：

> 这是 Ground Truth 语义名称，还是实际 SQL 输出名称？

不要直接修改 Ground Truth 来适配 actual。

如果 alias 可以明确证明语义相同：

保留 Ground Truth semantic name。

由 evaluator 解决。

---

# 十三、Aggregate 特别谨慎

`aggregate_document_count` 是重点。

先确认实际 saved SQL。

例如：

```sql
SELECT COUNT(*) AS count
FROM documents
```

和：

```sql
SELECT COUNT(*) AS total_documents
FROM documents
```

可以是相同业务语义。

但：

```sql
SELECT COUNT(*) AS count
FROM chunks
```

显然不是。

因此 aggregate alias 必须包含 aggregation context。

---

# 十四、测试要求

至少新增：

### Exact match

```text
expected document_id
actual document_id
→ exact
```

### Alias match

```text
expected document_id
actual id
→ alias
```

### Wrong entity

```text
expected document_id
actual chunk_id
→ FAIL
```

### Aggregate alias

```text
expected total_documents
actual count
context = documents
→ PASS
```

### Aggregate wrong context

```text
expected total_documents
actual count
context = chunks
→ FAIL
```

### Unknown alias

```text
expected document_id
actual document_code
→ FAIL
```

### Strict mode

```text
expected document_id
actual id
→ FAIL
```

### Semantic mode

```text
expected document_id
actual id
→ PASS
```

---

# 十五、回归要求

必须证明：

```text
Phase 3.9.17 semantic result:
12/12
```

不会因为 alias 加入而降低。

如果变化：

立即停止并分析。

不要为了保持 12/12 放宽规则。

---

# 十六、禁止重新跑 LLM

本阶段：

```text
DeepSeek calls = 0
```

必须保持：

```text
offline deterministic evaluation
```

---

# 十七、Snapshot

如果实际 evaluator 行为有变化，可以创建：

```text
tests/fixtures/text_to_sql/baselines/phase_3_9_18_alias_evaluation.json
```

但不要覆盖：

```text
phase_3_9_17_semantic_result_full.json
```

记录：

```text
phase_3_9_17_semantic_accuracy
phase_3_9_18_semantic_accuracy
alias_matches
exact_matches
unmatched_columns
```

如果 alias audit 发现当前 12 cases 根本没有需要 alias 的情况：

不要为了产生 snapshot 而制造变化。

可以只产生 audit report。

---

# 十八、文档

新增：

```text
docs/evaluation/text-to-sql-semantic-column-alias-3.9.18.md
```

内容：

1. Problem
2. Why global alias is unsafe
3. Context-aware alias model
4. Exact vs Alias matching
5. Aggregate alias semantics
6. Negative cases
7. 12-case audit
8. Semantic accuracy comparison
9. Limitations
10. Phase 3.9.19 recommendation

---

# 十九、测试

至少：

```text
pytest tests/test_text_to_sql_result_evaluation_service.py -q

pytest tests/test_text_to_sql_semantic_result_evaluation.py -q

pytest <new alias tests> -q
```

然后：

```text
pytest -q
```

以及：

```text
python -m compileall backend tests scripts
```

执行现有 lint。

---

# 二十、历史检查

确认：

```text
3.9.4 --check
3.9.5 --check
3.9.6 --check
3.9.9 --check
3.9.10 --check
3.9.11 --check
3.9.13 --check
3.9.14 --check
3.9.16 --check
3.9.17 --check
```

全部保持通过。

如果某个历史 check 因 dataset hash 漂移出现问题：

不要修改历史 baseline。

---

# 二十一、最终 Git 审查

执行：

```text
git status
git diff --stat
git diff
```

重点确认：

```text
Production Text-to-SQL = unchanged
Prompt = unchanged
Validator = unchanged
Executor = unchanged
Orchestrator = unchanged
Database fixture = unchanged
12 questions = unchanged
3.9.14 baseline = unchanged
3.9.17 baseline = unchanged
```

---

# 二十二、最终汇报

完成后 STOP。

格式：

```text
Phase 3.9.18 完成

1. 新增文件
2. 修改文件
3. Alias model
4. Exact / Alias matching 规则
5. Aggregate alias 规则
6. 负面测试
7. 12-case alias audit
8. Semantic Accuracy
9. 3.9.17 → 3.9.18 对比
10. DeepSeek calls = 0
11. 测试结果
12. 历史 --check
13. baseline 状态
14. Git diff
15. Phase 3.9.19 建议

STOP。
```

# 最重要原则

不要把 alias mapping 做成：

```text
“看起来像，就认为相同”
```

而必须做到：

```text
业务实体明确
+
语义明确
+
规则确定
+
结果可复现
```

Evaluation 系统宁可暂时判定：

```text
UNKNOWN
```

也不要通过模糊匹配制造 false positive。
