# Phase 3.9.19：Alias 真触发与 Optional/Extra 一致性验证

## 一、阶段目标

在 Phase 3.9.18 已有上下文感知 Alias Registry 基础上，只完成两个目标：

1. 将 alias 解析统一扩展到：

   * `required_columns`
   * `optional_columns`
   * `UNDECLARED_EXTRA_COLUMN` 判定

2. 使用**离线构造的 projection variant**，真正触发 alias evaluation 路径。

本阶段是 Evaluation Layer 改进，不是 Text-to-SQL 模型优化。

---

# 二、严格范围

## 允许修改

允许修改：

```text
backend/app/services/text_to_sql_column_alias_evaluation_service.py
backend/app/services/text_to_sql_result_evaluation_service.py
backend/app/services/text_to_sql_semantic_result_evaluation_service.py
tests/test_text_to_sql_column_alias_evaluation.py
tests/test_text_to_sql_semantic_result_evaluation.py
scripts/analyze_text_to_sql_column_alias.py
```

允许新增：

```text
tests/fixtures/text_to_sql/
    projection_variants_3_9_19.yaml

docs/evaluation/
    text-to-sql-alias-trigger-3.9.19.md

tests/fixtures/text_to_sql/baselines/
    phase_3_9_19_alias_trigger.json
```

如果实际实现不需要新增文件，不要为了凑文件而新增。

---

# 三、明确禁止

本阶段禁止修改：

```text
Prompt
TextToSQLService
TextToSQL Generator
TextToSQL Validator
TextToSQL Executor
AI Router
AI Orchestrator
Project Context
Project Registry
Semantic Serializer
Schema Composer
text_to_sql_regression.yaml
result_ground_truth.yaml
t2s_eval_schema.sql
t2s_eval_data.sql
```

禁止：

```text
DeepSeek API
LLM
数据库查询
网络请求
embedding
reranker
```

禁止修改：

```text
phase_3_9_14_result_llm_baseline.json
phase_3_9_17_semantic_result_full.json
phase_3_9_18_alias_evaluation.json
```

历史 baseline 必须保持原样。

---

# 四、Alias 解析统一规则

继续使用 Phase 3.9.18 的：

```text
ColumnSemanticAlias(
    semantic_name,
    entity,
    source_table,
    source_column,
    aliases,
    aggregate
)
```

不要重新设计 Registry。

匹配优先级保持：

```text
1. exact
2. semantic_name 对应 alias
3. 裸 alias + entity/aggregate context
4. unknown
```

禁止：

```text
fuzzy matching
Levenshtein
embedding
LLM judge
全局 id → document_id
全局 count → document_count
```

---

# 五、Required Columns

保持 Phase 3.9.18 行为：

```text
required expected column
        ↓
alias resolver
        ↓
exact / alias / unknown
```

例如：

```text
expected = document_id
actual   = id
entity   = document
```

结果：

```text
ALIAS_MATCH
```

但是：

```text
expected = document_id
actual   = id
entity   = chunk
```

不能错误匹配成 document_id。

---

# 六、Optional Columns

将同样的 alias 解析应用于：

```yaml
optional_columns:
```

例如：

```yaml
required_columns:
  - document_id

optional_columns:
  - title
```

实际结果：

```text
id
title
```

应当得到：

```text
document_id → id      ALIAS_MATCH
title       → title   EXACT_MATCH
```

而不是把 `id` 当成 undeclared extra column。

---

# 七、UNDECLARED_EXTRA_COLUMN

统一使用同一个 alias resolver 判断实际列是否已经被：

```text
required_columns
OR
optional_columns
```

语义覆盖。

例如：

```yaml
required_columns:
  - document_id

optional_columns:
  - title
```

实际：

```text
id
title
```

其中：

```text
id → document_id
```

是合法 alias。

因此：

```text
UNDECLARED_EXTRA_COLUMN
```

不能出现。

---

# 八、Forbidden Columns

Forbidden columns 不得因为 alias 而被错误放行。

例如：

```yaml
forbidden_columns:
  - chunk_id
```

实际：

```text
id
```

如果当前 entity/context 能够明确把：

```text
id → chunk_id
```

则应该识别为 forbidden，而不是普通 alias。

如果 context 无法消解，则：

```text
UNKNOWN_COLUMN
```

按照现有安全策略处理。

不要为了通过测试而放宽 forbidden 规则。

---

# 九、matched_by

在 semantic evaluation 输出中增加统一诊断信息。

每个成功匹配的 expected/actual column 至少能够表达：

```text
EXACT_MATCH
ALIAS_MATCH
```

无法匹配：

```text
UNKNOWN_COLUMN
```

建议结构：

```json
{
  "expected": "document_id",
  "actual": "id",
  "matched_by": "alias"
}
```

或者使用项目已有 DTO 风格，不强制 JSON。

关键要求：

**诊断信息必须能够区分 exact 与 alias。**

---

# 十、Projection Variant

不要重新调用 DeepSeek。

读取 Phase 3.9.14 已保存的实际结果，在内存中构造 projection variants。

至少构造以下场景：

### Case A：document id alias

```text
expected: document_id
actual:   id
entity:   document
```

预期：

```text
ALIAS_MATCH
PASS
```

---

### Case B：chunk id alias

```text
expected: chunk_id
actual:   id
entity:   chunk
```

预期：

```text
ALIAS_MATCH
PASS
```

---

### Case C：aggregate alias

```text
expected: document_count
actual:   total_documents
entity:   document
aggregate: true
```

预期：

```text
ALIAS_MATCH
PASS
```

---

### Case D：wrong entity

```text
expected: document_id
actual:   id
entity:   chunk
```

不能通过。

---

### Case E：ambiguous bare id

没有 entity context：

```text
expected: document_id
actual:   id
entity: null
```

不能自动选择 document_id。

---

### Case F：optional alias

例如：

```yaml
required_columns:
  - document_id

optional_columns:
  - title
```

实际：

```text
id
title
```

预期：

```text
document_id → id       ALIAS_MATCH
title       → title    EXACT_MATCH
```

整体 PASS。

---

### Case G：alias 不应该产生 extra

实际：

```text
id
title
```

expected：

```text
document_id
```

optional：

```text
title
```

不得出现：

```text
UNDECLARED_EXTRA_COLUMN
```

---

### Case H：unknown extra

expected：

```text
document_id
```

optional：

```text
title
```

actual：

```text
id
title
foo
```

其中：

```text
id → document_id
title → title
foo → UNKNOWN
```

应产生：

```text
UNDECLARED_EXTRA_COLUMN
```

---

# 十一、测试要求

新增或修改测试，至少覆盖：

### Positive

```text
document.id → document_id
chunk.id → chunk_id
document.total_documents → document_count
optional alias
alias does not become extra
exact match remains exact
```

### Negative

```text
document.id → chunk_id
chunk.id → document_id
bare id without context
bare count without context
wrong aggregate entity
unknown column
forbidden alias
fuzzy-looking aliases
```

继续明确禁止：

```text
document_ids
doc_id
documentid
doc
```

自动变成合法 alias。

---

# 十二、3.9.19 Offline Audit

运行：

```text
python scripts/analyze_text_to_sql_column_alias.py
```

如果脚本已有参数，优先复用现有 CLI。

增加或扩展 audit，使其能够报告：

```text
exact_matches
alias_matches
unknown_matches
alias_needed_cases
```

并进一步报告：

```text
required matched_by
optional matched_by
extra-column alias handling
```

不得调用：

```text
DeepSeek
DB
network
```

---

# 十三、Snapshot

如果本阶段确实产生新的 evaluation behavior，新增：

```text
phase_3_9_19_alias_trigger.json
```

必须明确记录：

```text
phase: 3.9.19
parent_baseline: 3.9.18
llm_calls: 0
db_calls: 0
network_calls: 0
```

并区分：

```text
real historical result
projection variant
```

不能把 projection variant 冒充成真实 LLM 结果。

如果 snapshot 没有实际价值，可以不新增。

---

# 十四、Regression 要求

执行至少：

```text
pytest tests/test_text_to_sql_result_evaluation_service.py -q
pytest tests/test_text_to_sql_semantic_result_evaluation.py -q
pytest tests/test_text_to_sql_column_alias_evaluation.py -q
pytest -q
python -m compileall backend tests scripts
```

检查：

```text
3.9.17 semantic result = 12/12
3.9.18 semantic result = 12/12
```

不得因为 alias 扩展导致历史语义结果下降。

---

# 十五、最终报告必须回答

完成后只报告，不继续下一阶段。

报告：

1. 修改/新增哪些文件
2. alias resolver 是否复用 3.9.18
3. required / optional / extra 是否统一
4. forbidden alias 如何处理
5. projection variant 一共多少个
6. 实际触发多少次 ALIAS_MATCH
7. exact / alias / unknown 分布
8. 是否出现错误放行
9. 3.9.17 semantic accuracy 是否仍为 12/12
10. 3.9.18 baseline 是否保持不变
11. DeepSeek calls
12. DB calls
13. network calls
14. 测试结果
15. compileall
16. Git diff
17. 是否产生新的 baseline
18. 是否发现新的设计问题

---

# 十六、STOP 条件

完成上述内容后：

**立即 STOP。**

不要：

* 优化 Prompt
* 重新跑 DeepSeek
* 修改 Text-to-SQL Generator
* 修改 SQL Validator
* 修改 SQL Executor
* 扩大 Alias Registry
* 自动进入 Phase 3.9.20

只返回 Phase 3.9.19 完成报告。
