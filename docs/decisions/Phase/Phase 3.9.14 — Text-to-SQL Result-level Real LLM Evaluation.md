# Phase 3.9.14 — Text-to-SQL Result-level Real LLM Evaluation

## 1. 目标

基于已经完成的：

```text
Phase 3.9.13
```

使用：

```text
t2s_eval
12 个 deterministic result ground truth cases
DeepSeek
真实 TextToSQLService
真实 SQLValidator
真实 SQLExecutor
真实 Result-level Checker
```

完成一次：

> **Real LLM Result-level Evaluation**

核心目标：

```text
3.9.13
Deterministic Fixture
        ↓
12 Result Ground Truth Cases
        ↓
DeepSeek
        ↓
TextToSQLService
        ↓
SQLValidator
        ↓
SQLExecutor
        ↓
Result Evaluation
```

最终回答：

> DeepSeek 生成的 SQL，在真实执行后，最终查询结果是否正确？

---

# 2. 严格范围

本阶段只允许：

### 新增

```text
Evaluation Runner / Adapter
Evaluation Script
Tests
Snapshot
Report
```

如果现有 Evaluation Service 可以通过 DI / adapter 实现，则优先复用。

---

# 3. 严格禁止

禁止修改：

```text
TextToSQLService
Text-to-SQL Prompt
Text-to-SQL Context
AIOrchestratorService
Router
RelevantTableSelector
SchemaComposer
Semantic Layer
SQLValidatorService
SQLExecutorService
RAG
Tool
ProjectConfiguration
ProjectRegistry
API
```

禁止修改：

```text
tests/fixtures/text_to_sql/text_to_sql_regression.yaml
```

禁止修改：

```text
result_ground_truth.yaml
t2s_eval_schema.sql
t2s_eval_data.sql
```

禁止修改历史 baseline：

```text
3.9.4
3.9.5
3.9.6
3.9.9
3.9.10
3.9.11
3.9.12
3.9.13
```

禁止修改 Prompt。

禁止改变 DeepSeek model / provider / temperature / retry configuration。

---

# 4. 重要：不要修改 Production Schema

3.9.13 已经建立：

```text
t2s_eval
```

本阶段必须让 Evaluation Runtime 使用：

```text
t2s_eval
```

而不是：

```text
public
```

不要修改 production database schema。

不要把 fixture 数据复制到 public。

---

# 5. Regression Dataset 不修改

当前 regression dataset 仍然包含：

```text
public.knowledge_document
public.knowledge_chunk
```

本阶段不要直接修改 dataset。

必须通过 evaluation adapter / schema context / fixture mapping 解决：

```text
logical case
      ↓
evaluation schema
      ↓
t2s_eval.documents
t2s_eval.chunks
```

---

# 6. Schema Mapping

需要建立一个非常明确的 evaluation-only mapping。

例如：

```text
logical knowledge_document
        ↓
t2s_eval.documents

logical knowledge_chunk
        ↓
t2s_eval.chunks
```

Project cases：

```text
project_a_inventory
        ↓
t2s_eval.project_a_inventory

project_b_inventory
        ↓
t2s_eval.project_b_inventory
```

具体实现必须基于当前项目已有 Schema / Semantic / Evaluation 架构。

不要修改 production schema metadata。

---

# 7. Mapping 的边界

Mapping 只能存在于：

```text
3.9.14 evaluation layer
```

不要加入：

```text
Production ProjectRegistry
Production ProjectConfiguration
Production SemanticProvider
```

因为本阶段是：

> Evaluation Adapter

不是：

> Production Project configuration change.

---

# 8. Ground Truth

读取：

```text
tests/fixtures/text_to_sql/result_ground_truth.yaml
```

只评估其中：

```text
12 cases
```

不评估：

```text
filtered_documents_by_file_type
safety_delete_all_documents
```

原因：

```text
filtered_documents_by_file_type
→ 当前问句缺少确定 file_type

safety_delete_all_documents
→ Security E2E 已覆盖
```

---

# 9. 12 个 Result Cases

必须包含：

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

# 10. 必须使用真实 DeepSeek

使用当前项目既有：

```text
provider = deepseek
model = deepseek-chat
base_url = https://api.deepseek.com
```

不要创建新的 LLM client。

不要要求用户提供 API Key。

继续读取现有 `.env`。

---

# 11. 必须使用真实 TextToSQLService

不能：

```text
FakeGenerator
StubGenerator
Hardcoded SQL
```

本阶段必须：

```text
real DeepSeek
    ↓
real TextToSQLService
```

这样结果才具有实际意义。

---

# 12. 必须使用真实 Validator

必须调用现有：

```text
SQLValidatorService
```

不能自己实现 Validator。

---

# 13. 必须使用真实 Executor

必须调用：

```text
SQLExecutorService
```

并执行：

```text
t2s_eval
```

Schema。

Executor 必须保持：

```text
READ ONLY
```

---

# 14. Execution Safety

所有 12 cases 都必须：

```text
read-only
```

如果某个生成 SQL 被 Validator 拒绝：

```text
不要绕过 Validator
不要手工修改 SQL
不要执行
```

直接记录：

```text
validation failure
```

---

# 15. Evaluation Flow

严格按照：

```text
Case
 ↓
Question
 ↓
Evaluation Schema Context
 ↓
Real TextToSQLService
 ↓
Generated SQL
 ↓
Real SQLValidator
 ↓
Real SQLExecutor
 ↓
Result Checker
 ↓
Case Outcome
```

---

# 16. 不允许 Ground Truth 反向影响 SQL Generation

特别注意：

Ground Truth：

```text
expected result
```

不能传给：

```text
DeepSeek
```

Ground Truth 只能在：

```text
SQL execution 完成之后
```

用于 checker。

否则会发生 evaluation leakage。

---

# 17. 不允许把 Expected SQL 提供给 LLM

Ground Truth 文件不能进入 Prompt。

只能提供：

```text
Schema
Semantic
Question
Project Context
```

不能提供：

```text
expected SQL
expected rows
expected scalar
```

---

# 18. Result Checker

复用 Phase 3.9.10：

```text
TextToSQLResultEvaluationService
```

不要重新实现：

```text
exact_rows
unordered_rows
scalar
column_values
```

如果现有 service 不支持 evaluation schema，只新增最小 adapter。

---

# 19. Result Evaluation

每一个 case 至少记录：

```text
case_id
generated_sql
generation_success
validation_passed
execution_success
result_evaluable
result_correct
error_code
```

不要记录：

```text
API key
database password
DATABASE_URL
```

---

# 20. SQL 是否正确

必须区分：

### Structural correctness

例如：

```text
SQL Validator accepted
```

### Result correctness

例如：

```text
actual rows == expected rows
```

本阶段核心指标是：

> Result correctness

不能把：

```text
Validator accepted
```

当作：

```text
Result correct
```

---

# 21. Result Accuracy

计算：

```text
correct_result_cases / evaluable_result_cases
```

当前：

```text
evaluable_result_cases = 12
```

所以 Result Accuracy 应明确：

```text
X / 12
```

不要把 N/A case 算进 denominator。

---

# 22. Execution Success

单独统计：

```text
execution_success / execution_attempted
```

如果 Validator reject：

```text
not executed
```

不要把它算成 execution failure。

---

# 23. Generation Success

统计：

```text
generation_success / 12
```

---

# 24. Validator Acceptance

统计：

```text
validator_accepted / generated_cases
```

---

# 25. Structural Expectation

仍然复用 3.9.9 / 3.9.12 的 structural checker。

统计：

```text
expectation_match / 12
```

但是必须与：

```text
result_accuracy
```

分开。

---

# 26. Project A / B

必须单独记录：

```text
project_a_inventory
project_b_inventory
```

Expected：

```text
project_a item_x = 100
project_b item_x = 999
```

至少验证：

```text
A result == A ground truth
B result == B ground truth
A result != B result
```

---

# 27. Project Isolation Gate

增加：

```text
project_isolation_pass
```

只有：

```text
project_a_correct
AND
project_b_correct
AND
A != B
```

才算：

```text
PASS
```

---

# 28. Security Case

不要把：

```text
safety_delete_all_documents
```

加入本阶段的 12-case result denominator。

但报告中必须明确：

```text
Security case evaluated separately in Phase 3.9.7 / 3.9.8.
```

不要因为本阶段没有运行 security case 而把 Security 标成：

```text
PASS
```

应该：

```text
N/A — covered by dedicated security E2E phases
```

---

# 29. 新 Snapshot

新增：

```text
tests/fixtures/text_to_sql/baselines/phase_3_9_14_result_llm_baseline.json
```

建议结构：

```json
{
  "phase": "3.9.14",
  "model_provider": "deepseek",
  "model": "deepseek-chat",
  "total_cases": 12,
  "generation_success": 0,
  "validator_accepted": 0,
  "execution_attempted": 0,
  "execution_success": 0,
  "result_evaluable": 12,
  "result_correct": 0,
  "result_accuracy": 0.0,
  "project_isolation": {
    "project_a": false,
    "project_b": false,
    "pass": false
  },
  "security": {
    "status": "N/A",
    "covered_by": [
      "3.9.7",
      "3.9.8"
    ]
  }
}
```

实际数字由真实运行结果填写。

---

# 30. Snapshot 不允许手工填写结果

运行 DeepSeek 后：

```text
actual result
    ↓
evaluation service
    ↓
metrics
    ↓
snapshot
```

不能：

```text
看输出
    ↓
人工修改 JSON
```

---

# 31. Generated SQL 保存策略

为了后续诊断，本阶段应保存每个 case 的：

```text
generated_sql
```

但是：

> 不保存任何 secret。

Snapshot 可以只保存：

```text
case_id
generated_sql
generation_success
validation_passed
execution_success
result_correct
```

如果项目已有统一脱敏机制则复用。

---

# 32. Ground Truth Hash

Snapshot 必须记录：

```text
ground_truth_sha256
```

当前预期：

```text
9EA66CDFDF52BF324B394AF657DA9AA962FE321ACE88EF90AA7CDB1F1EABFFC4
```

如果不一致：

> STOP。

不要继续 benchmark。

---

# 33. Fixture Hash

必须记录：

```text
t2s_eval_schema.sql
t2s_eval_data.sql
```

当前：

```text
schema:
CC32C9D1BB5ECD2F3127E89C03D985E97167C4144216274E5BE24E9ABE4ACE8A

data:
5A912EF37D68187EAE2E034B00A41319EC8930AF7E271BFB4AD1E7350131E341
```

如果不一致：

> STOP。

---

# 34. Dataset Hash

必须记录：

```text
1D0D1919CC789669F41B497CF4E089FC04537CF04851D4BBAAF177AC8176E731
```

如果不一致：

> STOP。

---

# 35. Real Run 与 Check 分离

必须有两个路径：

```text
--run
```

和：

```text
--check
```

其中：

### `--run`

允许：

```text
DeepSeek
PostgreSQL read-only
```

### `--check`

必须：

```text
0 LLM
0 PostgreSQL
0 network
0 mutation
```

---

# 36. `--check` 必须验证

至少：

```text
snapshot schema
dataset hash
ground truth hash
fixture hashes
case count
result denominator
metric arithmetic
project isolation arithmetic
security N/A status
```

---

# 37. `--check` 不允许重新调用 DeepSeek

这是硬要求。

不能出现：

```python
TextToSQLService(...)
```

或任何：

```text
LLM client
```

在 check path 中。

---

# 38. Evaluation DB

运行前：

```text
setup t2s_eval
```

确保 fixture 已存在。

如果已有：

```text
t2s_eval
```

不要重复创建 production objects。

---

# 39. 不允许 Evaluation Runtime 修改 Fixture

真实 benchmark 期间：

```text
INSERT = forbidden
UPDATE = forbidden
DELETE = forbidden
DDL = forbidden
```

只有 fixture setup/reset script 可以写：

```text
t2s_eval
```

---

# 40. Failure Handling

单个 case 失败：

> 不要中断整个 benchmark。

继续执行剩余 cases。

记录：

```text
generation failure
validation failure
execution failure
result mismatch
```

最终统一汇总。

---

# 41. DeepSeek Failure

如果 API 调用失败：

不要伪造结果。

Snapshot 必须记录：

```text
run_status = FAILED
```

并停止生成正式 baseline。

---

# 42. Partial Run

如果 12 个只跑了 8 个：

不能生成正式：

```text
phase_3_9_14_result_llm_baseline.json
```

可以保存：

```text
temporary run artifact
```

但不能标记：

```text
COMPLETE
```

---

# 43. Result Mismatch

如果：

```text
actual != expected
```

必须保留：

```text
case_id
generated_sql
actual_result
expected_result
```

用于诊断。

---

# 44. 不允许自动修正 SQL

例如：

```text
DeepSeek:
SELECT wrong_column ...

程序：
自动替换 column
```

绝对禁止。

评估必须反映：

> Model 原始输出。

---

# 45. Report

新增：

```text
docs/evaluation/text-to-sql-result-llm-baseline-3.9.14.md
```

至少包含：

## 1. Objective

## 2. Environment

```text
provider
model
dataset hash
ground truth hash
fixture hashes
```

## 3. Evaluation Flow

## 4. Metrics

表格：

```text
Metric
Result
Denominator
```

包括：

```text
Generation Success
Validator Acceptance
Structural Expectation Match
Execution Success
Result Accuracy
```

## 5. Per-case Results

14 case 中：

```text
12 evaluated
2 N/A
```

每个 case：

```text
case
generated SQL
validation
execution
result correctness
```

## 6. Result Mismatches

## 7. Project A/B Isolation

## 8. Security

引用：

```text
3.9.7
3.9.8
```

## 9. Comparison

与：

```text
3.9.9
3.9.12
```

进行指标对比。

## 10. Limitations

---

# 46. 最重要的比较

报告必须区分：

### 3.9.12

```text
Result Accuracy = 100%
3/3 evaluable
```

与：

### 3.9.14

```text
Result Accuracy = X/12
```

不能简单说：

```text
100% → X%
```

因为 denominator 从：

```text
3
```

变成：

```text
12
```

这是：

> Evaluation coverage expansion

不是 regression。

---

# 47. 需要回答的问题

最终报告必须明确：

### Q1

DeepSeek 是否生成有效 SQL？

### Q2

SQL 是否通过 Validator？

### Q3

SQL 是否成功执行？

### Q4

SQL 执行结果是否正确？

### Q5

哪些 case 出现：

```text
SQL valid
but result incorrect
```

### Q6

哪些 case：

```text
SQL structurally correct
but result incorrect
```

### Q7

Project A/B 是否保持隔离？

---

# 48. Tests

新增测试必须覆盖：

```text
12 cases loaded
ground truth loaded
fixture mapping
schema mapping
real result checker integration
metric calculation
project isolation calculation
snapshot validation
--check purity
failure handling
```

Real DeepSeek 测试不要作为普通 unit test。

必须显式：

```text
RUN_REAL_LLM=1
```

才运行。

默认：

```text
SKIPPED
```

---

# 49. Real Test

真实 benchmark 只运行一次。

不要：

```text
连续跑 5 次
```

不要：

```text
挑最好的一次
```

不要：

```text
失败后自动重新 benchmark
```

原因：

> 本阶段建立 baseline，而不是寻找最佳成绩。

---

# 50. Prompt 不变证明

运行前记录：

```text
3.9.12 Prompt v1 hash
```

运行后确认：

```text
Prompt hash unchanged
```

如果发生变化：

> STOP。

---

# 51. Historical Integrity

完成后必须运行：

```text
3.9.4 --check
3.9.5 --check
3.9.6 --check
3.9.9 --check
3.9.10 --check
3.9.11 --check
3.9.12 --check
3.9.13 --check
3.9.14 --check
```

全部 PASS。

---

# 52. DB Residue

Benchmark 完成后：

```text
public.knowledge_document = 0
public.knowledge_chunk = 0
```

并确认：

```text
t2s_eval
```

只保留 fixture。

没有：

```text
temporary table
temporary schema
```

---

# 53. 不做优化

本阶段出现任何错误：

不要：

```text
修改 Prompt
修改 TextToSQLService
修改 Semantic
修改 Validator
修改 Executor
```

只记录。

例如：

```text
Case X:
Generation SUCCESS
Validation ACCEPTED
Execution SUCCESS
Result INCORRECT
```

这正是本阶段需要发现的问题。

---

# 54. STOP 条件

遇到以下任何情况：

```text
dataset hash changed
ground truth hash changed
fixture hash changed
prompt hash changed
production file modified
fixture mapping requires production modification
```

立即：

```text
STOP
```

并报告原因。

---

# 55. 最终报告格式

完成后只返回：

```text
PHASE 3.9.14 COMPLETE

1. New files
2. Production files modified
3. DeepSeek model/provider
4. Dataset hash
5. Ground truth hash
6. Fixture hashes
7. Generation Success
8. Validator Acceptance
9. Structural Expectation Match
10. Execution Success
11. Result Accuracy
12. 12-case per-case results
13. Result mismatches
14. Project A/B isolation
15. Security status
16. Comparison with 3.9.9 / 3.9.12
17. Tests
18. compileall/lint
19. Historical --check
20. DB residue
21. What this phase proves
22. What this phase does not prove
```

最后：

```text
PHASE 3.9.14 COMPLETE
```

然后立即停止。

**不要开始 3.9.15。**
**不要修改 Prompt。**
**不要进行第二次 DeepSeek benchmark。**
