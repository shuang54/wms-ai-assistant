# Phase 3.9.21：Baseline LLM 自然波动与重复性实验

## 一、阶段目标

在 Phase 3.9.20 发现：

```text
同一 Prompt
14 cases
Baseline vs Candidate
```

仍出现 1 个 case 的 LLM 非确定性差异后，本阶段不进行 Prompt 优化。

唯一目标：

> 测量当前 Baseline Prompt 在相同条件下的自然输出波动。

实验：

```text
Baseline Prompt v1
      ↓
Run A：14 cases
      ↓
Run B：14 cases
      ↓
Run C：14 cases
      ↓
三次结果比较
```

本阶段属于：

**LLM Evaluation / Experimental Baseline Stability**

不是 Prompt Optimization。

---

# 二、严格禁止

本阶段禁止修改：

```text
Prompt
TextToSQLService
TextToSQLGenerator
SQLValidator
SQLExecutor
AI Router
AI Orchestrator
Schema Composer
Semantic Context
RelevantTableSelector
Alias Evaluator
Dataset
Ground Truth
Fixture Schema
Fixture Data
```

禁止：

```text
Candidate Prompt
Few-shot
Prompt optimization
Temperature optimization
Model switching
Retry optimization
SQL rewriting
LLM judge
```

不要因为某个 case 失败而修改任何逻辑。

---

# 三、实验条件必须固定

三个 Run 必须使用完全相同：

```text
model
base_url
prompt_version
system_prompt
user_prompt
retry_prompt
prompt hashes
dataset
ground truth
fixture schema
fixture data
project
schema context
semantic context
temperature
max_tokens
```

记录：

```text
model
prompt_hash
dataset_sha256
ground_truth_sha256
fixture_schema_sha256
fixture_data_sha256
```

如果现有项目还有其他影响 LLM 输出的配置，也一并记录。

---

# 四、Run 数量

严格执行：

```text
Run A = 14 cases
Run B = 14 cases
Run C = 14 cases
```

总计：

```text
42 DeepSeek calls
```

不要为了得到“好看的结果”额外重跑。

如果某次 API 调用真正失败：

* 记录失败
* 不自动补跑
* 不删除失败结果
* 不 cherry-pick 成功结果

最终报告必须区分：

```text
LLM generation failure
API/network failure
evaluation failure
```

---

# 五、Baseline Prompt

只允许使用当前正式 Baseline：

```text
Prompt v1
```

确认：

```text
Run A hash
Run B hash
Run C hash
```

必须完全一致。

如果 hash 不一致：

```text
EXPERIMENT INVALID
```

停止分析，不要自动修复。

---

# 六、Case 顺序

14 cases 必须按照现有：

```text
text_to_sql_regression.yaml
```

中的固定顺序运行。

三个 Run：

```text
Run A: case 1 → case 14
Run B: case 1 → case 14
Run C: case 1 → case 14
```

不要随机排序。

记录：

```text
case_order_hash
```

如果项目已有 dataset hash，可以直接证明顺序没有变化。

---

# 七、每个 Case 必须记录

每次 Run 至少保存：

```text
case_id
question
generated_sql
generation_success
validation_success
structural_expectation_match
security_category
```

如果本实验 pipeline 当前能够获得 execution / semantic result，则可以记录：

```text
execution_success
semantic_result_correct
```

但不要修改 pipeline 以强行增加这些指标。

---

# 八、SQL 不能只比较字符串

同一问题的：

```sql
SELECT COUNT(*) FROM documents;
```

和：

```sql
SELECT COUNT(id) FROM documents;
```

可能语义一致。

因此需要同时保存：

```text
raw SQL
```

用于诊断。

但是稳定性分析至少应该区分：

### SQL Text Difference

```text
SQL string A != SQL string B
```

### Validation Difference

```text
A PASS
B FAIL
```

### Structural Difference

```text
A PASS
B FAIL
```

### Semantic Difference

如果当前有语义结果评价：

```text
A PASS
B FAIL
```

---

# 九、核心指标

## 1. Generation Success Rate

分别计算：

```text
Run A
Run B
Run C
```

---

## 2. Validation Acceptance Rate

分别计算：

```text
Run A
Run B
Run C
```

---

## 3. Structural Expectation Match

分别计算：

```text
Run A
Run B
Run C
```

---

## 4. Case-level Agreement

对于每个 case，比较：

```text
A vs B
A vs C
B vs C
```

至少统计：

```text
generation_agreement
validation_agreement
structural_agreement
```

---

# 十、Case Stability

新增一个诊断字段：

```text
stability_status
```

推荐：

```text
STABLE
```

表示三次结果一致。

```text
NON_DETERMINISTIC
```

表示至少一次运行出现结果差异。

例如：

```text
semantic_dependent_document_and_chunk

Run A → FAIL
Run B → PASS
Run C → PASS
```

则：

```text
NON_DETERMINISTIC
```

---

# 十一、不要把 SQL 文本变化直接算作失败

例如：

```text
Run A:
SELECT id, title FROM documents LIMIT 10

Run B:
SELECT title, id FROM documents LIMIT 10
```

如果现有 evaluation 判定：

```text
A = PASS
B = PASS
```

则：

```text
structural_agreement = true
```

但：

```text
sql_text_same = false
```

这是允许的。

因此建议分别记录：

```text
sql_text_same
validation_same
structural_same
semantic_same
```

---

# 十二、重点分析 Case

特别关注：

```text
semantic_dependent_document_and_chunk
```

Phase 3.9.20 已观察：

```text
Baseline → FAIL
Candidate → PASS
```

本阶段必须查看：

```text
Run A
Run B
Run C
```

分别生成什么 SQL。

尤其检查：

```text
knowledge_chunk
```

与：

```text
public.knowledge_chunk
```

之间的差异。

但不要提前假设哪个是正确答案。

以现有 Validator / Structural / Semantic Evaluation 的结果为准。

---

# 十三、SQL Variant Count

统计每个 case：

```text
distinct SQL strings
```

例如：

```text
case_x
Run A → SQL-1
Run B → SQL-1
Run C → SQL-2

variant_count = 2
```

注意：

这只是：

```text
diagnostic
```

不是质量评分。

---

# 十四、Failure Category

复用已有 taxonomy。

不要创建：

```text
MODEL_BAD
PROMPT_BAD
```

这类主观类别。

可以记录现有：

```text
GENERATION_*
VALIDATION_*
STRUCTURAL_*
SECURITY_*
PROJECT_ISOLATION_*
```

如果当前 taxonomy 已经有对应类别，直接复用。

---

# 十五、Snapshot

新增：

```text
tests/fixtures/text_to_sql/baselines/phase_3_9_21_baseline_stability.json
```

必须包含：

```text
phase
experiment_type
run_count = 3

prompt_version
prompt_hash

dataset_sha256
ground_truth_sha256
fixture_schema_sha256
fixture_data_sha256

runs
case_comparisons
summary
```

每个 Run 保存：

```text
run_id
14 case results
metrics
```

---

# 十六、禁止修改历史 Snapshot

绝对不要修改：

```text
phase_3_9_14_result_llm_baseline.json
phase_3_9_17_semantic_result_full.json
phase_3_9_18_alias_evaluation.json
phase_3_9_19_alias_trigger.json
phase_3_9_20_prompt_experiment.json
```

3.9.21 必须是独立 snapshot。

---

# 十七、Offline --check

必须支持：

```text
--check
```

要求：

```text
DeepSeek calls = 0
DB calls = 0
Network calls = 0
```

检查：

```text
snapshot JSON integrity
prompt hash consistency
dataset hash
ground truth hash
fixture hashes
14 case alignment
3 runs present
metrics recomputation
case comparison recomputation
```

---

# 十八、测试

新增或扩展：

```text
tests/test_text_to_sql_prompt_experiment.py
```

或者单独：

```text
tests/test_text_to_sql_baseline_stability.py
```

至少测试：

1. 三个 Run 能正确加载
2. 每个 Run 必须包含 14 cases
3. case 顺序一致
4. prompt hash 一致
5. dataset hash 一致
6. metrics 可以从 case results 重新计算
7. case agreement 计算正确
8. stable / non-deterministic 分类正确
9. SQL variant count 正确
10. `--check` 不调用 LLM
11. `--check` 不调用 DB
12. `--check` 不访问网络

使用 fake snapshot。

不要让单元测试调用 DeepSeek。

---

# 十九、真实实验

运行：

```text
python scripts/run_text_to_sql_prompt_experiment.py
```

如果现有脚本已经支持 repeat/stability 参数，优先扩展现有 CLI。

推荐：

```text
--variant baseline
--repeat 3
```

如果现有 CLI 不适合，可以新增：

```text
python scripts/run_text_to_sql_baseline_stability.py
```

但不要重复实现已有实验框架。

---

# 二十、数据库

本阶段默认：

```text
DB calls = 0
```

如果当前 Text-to-SQL evaluation 需要真实 DB：

必须：

```text
BEGIN READ ONLY
```

并且不得产生：

```text
INSERT
UPDATE
DELETE
CREATE
DROP
ALTER
TRUNCATE
```

但是不要为了 3.9.21 强行打开 DB。

如果结构级实验已经足够完成稳定性分析，保持 DB = 0。

---

# 二十一、结果解释规则

禁止出现：

```text
Baseline 平均分 X，所以模型不稳定
```

这种未经定义的结论。

只能陈述：

```text
X/14 cases 在三次运行中 structural result 一致。
Y/14 cases 至少出现一次 structural result 差异。
Z/14 cases 产生多个 SQL variants。
```

如果某个 case：

```text
A FAIL
B PASS
C PASS
```

只能说：

```text
该 case 在本次三次实验中表现为 NON_DETERMINISTIC。
```

不要进一步断言：

```text
Prompt 有问题
模型有问题
Schema 有问题
```

这些需要后续实验验证。

---

# 二十二、最终报告

严格按照：

```text
Phase 3.9.21 完成汇报

1. 实验条件
2. Prompt hash
3. Dataset / Ground Truth / Fixture hash
4. Run A 指标
5. Run B 指标
6. Run C 指标
7. 三次总体对比
8. Case-level stability
9. SQL variant distribution
10. semantic_dependent_document_and_chunk 详细结果
11. Non-deterministic cases
12. DeepSeek calls
13. DB calls
14. Network calls
15. Snapshot
16. Tests
17. compileall / lint
18. Git diff
19. 结论
20. 当前限制
```

结论只能回答：

```text
Baseline 是否存在可观测自然波动？
哪些 case 发生波动？
波动发生在哪一层？
```

不要回答：

```text
哪个 Prompt 更好
哪个模型更好
如何优化 Prompt
```

---

# 二十三、STOP

完成：

```text
3 Runs
↓
Analysis
↓
Snapshot
↓
--check
↓
Tests
↓
Report
```

之后：

**立即 STOP。**

不要进入 Prompt Optimization。

不要修改 Prompt。

不要运行 Candidate。

不要增加 Few-shot。

不要修改 Generator。

不要修改 Validator。

不要修改 Dataset。

不要进入 Phase 3.9.22。
