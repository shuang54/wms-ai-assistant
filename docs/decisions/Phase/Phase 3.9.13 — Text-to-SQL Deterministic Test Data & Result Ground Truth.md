# Phase 3.9.13 — Text-to-SQL Deterministic Test Data & Result Ground Truth

## 1. 目标

Phase 3.9.10 已证明 Result-level Correctness 可以确定性评估，但当前只有：

```text
3 / 14
```

个 case 具有稳定的 result expectation。

Phase 3.9.11 进一步确认：

```text
Result Correctness:
3/14 evaluable
11/14 N/A
```

Phase 3.9.12 的 Prompt-only experiment 没有改变这一事实。

因此本阶段只解决一个问题：

> **为 Text-to-SQL regression evaluation 建立隔离、稳定、可重复的 deterministic test data，使更多 SQL 类型具备真实 result-level ground truth。**

---

# 2. 本阶段定位

这是：

> **Evaluation Infrastructure / Test Fixture Phase**

不是：

> Production Feature

不是：

> Prompt Optimization

不是：

> Model Optimization

---

# 3. 严格禁止

本阶段禁止修改：

* TextToSQLService
* Text-to-SQL Prompt
* TextToSQLContext
* AIOrchestratorService
* Router
* Relevant Table Selector
* Schema Composer
* Semantic Layer
* SQL Validator
* SQL Executor
* RAG
* Tool
* Project Configuration
* Project Registry
* API
* Production database schema
* Production database data
* DeepSeek Prompt
* Model
* Retry strategy
* Temperature

禁止修改：

```text
3.9.4 baseline
3.9.5 baseline
3.9.6 analysis
3.9.9 quality baseline
3.9.10 result baseline
3.9.11 diagnosis
3.9.12 experiment snapshot
```

禁止修改：

```text
tests/fixtures/text_to_sql/text_to_sql_regression.yaml
```

除非本阶段明确需要**新增 test-data metadata**；如果必须修改既有 case 字段，先 STOP 并报告，不要自行扩大范围。

---

# 4. 最重要原则

测试数据必须：

```text
deterministic
isolated
repeatable
minimal
read-only during evaluation
```

不能依赖：

```text
当前生产 WMS 数据
当前 Vietnam WMS 数据
用户个人数据
随机数据
当前时间
外部 API
DeepSeek
```

---

# 5. 不要污染现有 public 表

不要继续向：

```text
public.knowledge_document
public.knowledge_chunk
```

插入大量 fixture 数据。

Phase 3.9.10 已经明确：

> 当前 public knowledge tables 为空，因此部分结果只能 N/A。

本阶段应该创建：

```text
专用 evaluation schema
```

例如：

```text
t2s_eval
```

如果项目已经存在合适的专用 schema，则复用它。

不要重复创建。

---

# 6. Schema 设计目标

只建立能够覆盖当前 regression dataset 的最小数据模型。

优先覆盖：

1. simple query
2. Top N
3. filter
4. ORDER BY
5. aggregation
6. GROUP BY
7. HAVING
8. JOIN
9. date filter
10. LIMIT
11. Project A
12. Project B

---

# 7. 不要复制整个生产 WMS Schema

禁止：

> 把完整 WMS 数据库复制到测试 schema。

只需要最小字段。

例如可以设计：

```sql
t2s_eval.documents
```

字段：

```text
id
file_name
file_type
created_at
project_id
```

以及：

```sql
t2s_eval.chunks
```

字段：

```text
id
document_id
content
token_count
project_id
created_at
```

具体字段必须根据现有 regression dataset / semantic / prompt 所需要的字段决定。

不要猜业务字段。

---

# 8. Schema 必须支持真实 JOIN

必须建立真正 FK：

```text
chunks.document_id
    →
documents.id
```

不要使用没有关系约束的两张独立表来模拟 JOIN。

---

# 9. Fixture 数据设计

数据必须：

> 小而有区分度。

例如 documents 可以有：

```text
Document A
Document B
Document C
Document D
```

chunks 可以让不同 document 具有明显不同的：

```text
token_count
file_type
created_at
```

例如：

```text
Document A → 2 chunks
Document B → 3 chunks
Document C → 1 chunk
Document D → 4 chunks
```

这样可以可靠验证：

```text
COUNT
GROUP BY
HAVING
ORDER BY
JOIN
Top N
```

具体数据不要照搬示例数字。

必须根据当前 regression questions 设计。

---

# 10. 数据必须可区分

不要出现：

```text
所有 token_count 都相同
所有 created_at 都相同
所有 file_type 都相同
```

否则：

> SQL 错了，也可能返回一样的结果。

本阶段的核心目标就是提高：

> **Ground Truth Discriminative Power**

---

# 11. 日期数据

日期必须使用固定日期。

例如：

```text
2025-01-01
2025-02-01
2025-03-01
2025-04-01
```

不要使用：

```sql
NOW()
CURRENT_DATE
```

作为 fixture 数据生成依据。

---

# 12. Top N

必须让排序结果具有明显差异。

例如 token_count：

```text
10
20
30
40
```

这样：

```text
ORDER BY token_count DESC
LIMIT 3
```

的正确结果是确定性的。

不要出现并列值导致 Top N 不稳定。

---

# 13. GROUP BY

数据必须保证至少两个 group。

例如：

```text
file_type = md
file_type = txt
```

每组数量不同。

这样可以区分：

```sql
GROUP BY
```

是否正确。

---

# 14. HAVING

必须设计：

```text
至少一个 group 满足 HAVING
至少一个 group 不满足 HAVING
```

例如：

```text
group A → count = 4
group B → count = 2
```

测试：

```sql
HAVING COUNT(*) > 2
```

时只能得到 group A。

不要设计成所有 group 都满足。

---

# 15. JOIN

JOIN 结果必须具有区分度。

例如：

```text
document A → 2 chunks
document B → 3 chunks
document C → 1 chunk
```

这样可以验证：

```text
JOIN key
selected columns
aggregation
```

---

# 16. Filter

至少准备：

```text
2 种 file_type
```

并保证数量不同。

例如：

```text
md
txt
```

这样：

```text
WHERE file_type = 'md'
```

有确定结果。

---

# 17. Project A / Project B

当前：

```text
project_a_inventory
project_b_inventory
```

属于离线 fixture schema。

本阶段可以建立：

```text
t2s_eval.project_a
t2s_eval.project_b
```

或者使用项目已有隔离机制。

但必须保持：

```text
Project A data ≠ Project B data
```

例如同一个逻辑字段：

```text
project_a → 100
project_b → 999
```

这样才能真正验证：

> Project context 是否正确隔离。

---

# 18. Project Isolation

必须设计一个非常明显的差异。

例如：

```text
project_a:
item_x qty = 100

project_b:
item_x qty = 999
```

执行相同语义查询：

```text
What is the quantity of item_x?
```

不同 project 必须得到不同结果。

---

# 19. 数据初始化方式

优先使用现有测试 DB fixture / migration / helper 机制。

如果项目没有合适机制：

新增：

```text
tests/fixtures/text_to_sql/
```

下的 deterministic SQL fixture。

例如：

```text
tests/fixtures/text_to_sql/t2s_eval_schema.sql
```

以及：

```text
tests/fixtures/text_to_sql/t2s_eval_data.sql
```

具体文件名以项目现有结构为准。

---

# 20. 必须支持 Reset

测试数据必须可以：

```text
reset
```

即：

```text
DROP / CREATE / INSERT
```

只允许发生在专用 evaluation schema。

绝对不能：

```text
DROP public.knowledge_document
```

或修改任何生产表。

---

# 21. 不要让 Text-to-SQL Evaluation 自动写数据

非常重要：

> Evaluation runtime 仍然必须 read-only。

数据初始化和 evaluation execution 必须是两个步骤。

结构：

```text
Fixture Setup
    ↓
Deterministic Data
    ↓
Text-to-SQL Evaluation
    ↓
READ ONLY
```

而不是：

```text
Evaluation
    ↓
自动 INSERT fixture
```

---

# 22. Ground Truth

为已有 14 个 case 中适合的案例增加结果预期。

但是：

> 不要直接修改 regression dataset。

优先新增：

```text
tests/fixtures/text_to_sql/result_ground_truth.yaml
```

结构建议：

```yaml
version: "1.0"

cases:
  - case_id: ...
    expectation:
      type: exact_rows
      rows: ...

  - case_id: ...
    expectation:
      type: unordered_rows
      rows: ...

  - case_id: ...
    expectation:
      type: scalar
      value: ...

  - case_id: ...
    expectation:
      type: column_values
      column: ...
      values: ...
```

---

# 23. 为什么使用独立 Ground Truth 文件

原因：

3.9.10 已经修改过：

```text
text_to_sql_regression.yaml
```

本阶段不要继续扩大主 regression dataset 的职责。

保持：

```text
Regression Dataset
+
Result Ground Truth
+
Evaluation Fixture Data
```

三个概念分离。

---

# 24. Ground Truth 必须人工确定

禁止：

```text
LLM generate expected result
```

禁止：

```text
执行模型 SQL → 把结果直接当 expected result
```

否则会形成：

```text
模型答案 = 标准答案
```

导致评估失去意义。

---

# 25. Ground Truth 的来源

正确方式：

```text
人工设计 fixture data
+
人工计算 expected result
```

可以使用 SQL / Python 辅助验证计算。

但最终 expected result 必须独立于被评估 SQL。

---

# 26. Ground Truth Checker

复用 3.9.10：

```text
ResultExpectation
TextToSQLResultEvaluationService
```

不要重新实现 checker。

如果需要新增：

```text
ground_truth_loader
```

单独实现。

---

# 27. Ground Truth 覆盖目标

尽可能覆盖现有 14 cases 中：

```text
Top N
Filter
Sort
Aggregation
GROUP BY
HAVING
JOIN
Date
LIMIT
Project A
Project B
```

但：

> **覆盖率不是硬指标。**

如果某个 case 不能设计出高区分度 deterministic result：

```text
N/A
```

不要为了覆盖率制造脆弱数据。

---

# 28. 安全 Case

```text
safety_delete_all_documents
```

仍然：

```text
无 result expectation
```

不要给它添加正常结果 ground truth。

它仍然由：

```text
3.9.7 Validator Security E2E
3.9.8 Executor Security E2E
```

负责。

---

# 29. New Test

新增：

```text
tests/test_text_to_sql_ground_truth.py
```

至少测试：

### Fixture

* schema creation
* deterministic data
* reset
* isolation

### Ground Truth

* all referenced case IDs exist
* no duplicate case IDs
* all expected columns exist
* expected result format valid
* no security case included

### Determinism

执行 fixture setup 两次：

```text
same data
same row counts
same aggregate results
```

### Project Isolation

确认：

```text
A != B
```

### No Production Mutation

确认 fixture setup 只访问：

```text
t2s_eval
```

---

# 30. Fixture Integrity Test

必须有一个测试：

> 所有 fixture table 都属于专用 evaluation schema。

不能出现：

```text
public
```

或生产 schema。

如果发现：

> STOP。

---

# 31. 新的 Ground Truth Report

新增：

```text
docs/evaluation/text-to-sql-ground-truth-3.9.13.md
```

包含：

## Scope

## Evaluation Schema

## Tables

## Fixture Data Design

## Ground Truth Cases

## Coverage

例如：

```text
14 total
X result-evaluable
14-X N/A
```

## Project Isolation

## Determinism

## Limitations

---

# 32. Snapshot

新增：

```text
tests/fixtures/text_to_sql/baselines/phase_3_9_13_ground_truth.json
```

至少：

```json
{
  "phase": "3.9.13",
  "dataset_version": "1.0",
  "ground_truth_version": "1.0",
  "total_cases": 14,
  "result_evaluable_cases": 0,
  "ground_truth_hash": "...",
  "fixture_schema": "t2s_eval"
}
```

实际数字根据实现填写。

---

# 33. Snapshot 不要覆盖 3.9.10

3.9.10：

```text
result_accuracy = 3/3
```

保持不变。

3.9.13 是：

> 新的 ground-truth infrastructure。

不是重新解释旧 baseline。

---

# 34. 是否需要重新运行 DeepSeek

本阶段：

> **不需要。**

重点是建立数据和 ground truth。

完成后不要自动运行新的 14-case LLM benchmark。

下一阶段再使用新的 fixture。

---

# 35. `--check`

如果新增 setup / validation script：

必须支持：

```bash
python scripts/setup_text_to_sql_ground_truth.py --check
```

或项目实际采用的等价命令。

`--check` 必须：

```text
0 LLM
0 PostgreSQL mutation
0 network
```

如果需要验证 SQL 文件内容：

可以离线解析。

---

# 36. Verification

执行：

```bash
python -m pytest tests/test_text_to_sql_ground_truth.py -q
```

然后：

```bash
python -m compileall backend tests scripts
```

然后执行：

```text
3.9.4 --check
3.9.5 --check
3.9.6 --check
3.9.9 --check
3.9.10 --check
3.9.11 --check
3.9.12 --check
```

所有历史 snapshot 必须保持一致。

---

# 37. Hash

记录：

```text
Regression Dataset SHA256
Ground Truth SHA256
Fixture SQL SHA256
```

以后任何数据变化都必须能检测出来。

---

# 38. DB Residue

完成后：

```text
knowledge_document = 0
knowledge_chunk = 0
```

以及：

```text
t2s_exec_sec = 0
```

如果 project_a / project_b 等已有测试 schema：

保持现有规则。

新的：

```text
t2s_eval
```

允许保留 fixture 数据，因为它本身就是 evaluation data。

但必须：

> 不存在测试过程中额外生成的临时残留表。

---

# 39. 最终报告

完成后只返回：

```text
PHASE 3.9.13 COMPLETE

1. New files
2. Production files modified
3. Evaluation schema
4. Fixture tables
5. Fixture row counts
6. Ground truth cases
7. Result-evaluable coverage
8. Project A/B isolation
9. Determinism verification
10. Tests
11. compileall
12. Historical --check
13. Dataset hash
14. Ground truth hash
15. Fixture hash
16. DB residue
17. What this phase proves
18. What this phase does not prove
```

---

# 40. STOP

完成后：

> **PHASE 3.9.13 COMPLETE**

立即停止。

不要自动运行新的 DeepSeek benchmark。

不要开始 3.9.14。

不要修改 Prompt。

等待下一条指令。
