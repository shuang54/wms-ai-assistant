# Phase 3.9.23：Prompt v2 Repeated Stability Validation

## 一、阶段目标

验证 Phase 3.9.22 Candidate v2 的改善是否能够稳定复现。

Phase 3.9.22 单次 A/B：

```text
Baseline v1    12/14 structural
Candidate v2   14/14 structural
```

其中：

```text
safety_delete_all_documents
```

是明确的目标改善。

而：

```text
semantic_dependent_document_and_chunk
```

在 Phase 3.9.21 已表现出自然波动，因此不能直接把 3.9.22 的改善归因于 v2。

本阶段唯一目标：

> 在相同条件下分别重复 Baseline v1 和 Candidate v2，观察 v2 的改善是否稳定，并与 Baseline 的自然波动进行比较。

---

# 二、实验设计

执行：

```text
Baseline v1
  Run A
  Run B
  Run C

Candidate v2
  Run A
  Run B
  Run C
```

每个 Run：

```text
14 cases
```

总计：

```text
14 × 6 = 84 DeepSeek calls
```

不得补跑。

不得 cherry-pick。

不得删除失败结果。

如果真实 API 调用失败，原样记录，并在最终报告中区分：

```text
LLM generation failure
API/network failure
evaluation failure
```

---

# 三、绝对禁止

本阶段禁止修改：

```text
backend/app/prompts/v1/*
backend/app/prompts/v2/*
```

也禁止修改：

```text
TextToSQLService
TextToSQLGenerator
SQLValidator
SQLExecutor
AI Router
AI Orchestrator
Schema Composer
Semantic Serializer
Semantic Schema Filter
Alias Evaluator
RelevantTableSelector
Dataset
Ground Truth
Fixture Schema
Fixture Data
```

禁止：

```text
Prompt optimization
Few-shot
Model switching
temperature adjustment
max_tokens adjustment
retry strategy modification
SQL rewriting
LLM Judge
```

本阶段是：

**Validation only。**

---

# 四、Prompt 必须冻结

Baseline：

```text
Prompt v1
```

Candidate：

```text
Prompt v2
```

不得修改任何 Prompt 内容。

记录六组运行对应的 Prompt hash。

必须满足：

```text
Baseline Run A = Baseline Run B = Baseline Run C
```

以及：

```text
Candidate Run A = Candidate Run B = Candidate Run C
```

并且：

```text
Baseline hash != Candidate hash
```

Baseline hash 必须与：

```text
Phase 3.9.20
Phase 3.9.21
Phase 3.9.22
```

保持一致。

Candidate hash 必须与 Phase 3.9.22 一致。

如果发现不一致：

```text
EXPERIMENT INVALID
```

立即停止。

---

# 五、实验数据必须冻结

六次 Run 使用完全相同：

```text
dataset
ground truth
fixture schema
fixture data
case order
model
base_url
schema context
semantic context
```

记录：

```text
dataset_sha256
ground_truth_sha256
fixture_schema_sha256
fixture_data_sha256
case_order_hash
```

必须与 Phase 3.9.22 当前实验记录一致。

temperature / max_tokens：

```text
client default
```

不得修改。

---

# 六、实验顺序

固定：

```text
Baseline A
Baseline B
Baseline C

Candidate A
Candidate B
Candidate C
```

每个 Run 都使用：

```text
case 1 → case 14
```

禁止随机化。

---

# 七、每个 Case 保存完整结果

每次运行至少保存：

```text
case_id
question
generated_sql
generation_success
validation_success
structural_expectation_match
security_category
```

如果现有实验框架已经支持：

```text
execution_success
semantic_result_correct
```

可以记录。

但是：

**不要为了增加指标而修改 production pipeline。**

---

# 八、核心指标

分别统计：

## Baseline

```text
Baseline A
Baseline B
Baseline C
```

每次：

```text
generation
validation
structural
security
```

## Candidate

```text
Candidate A
Candidate B
Candidate C
```

每次：

```text
generation
validation
structural
security
```

---

# 九、核心比较一：Safety Case

重点：

```text
safety_delete_all_documents
```

必须做 6 次独立结果比较：

```text
Baseline A
Baseline B
Baseline C

Candidate A
Candidate B
Candidate C
```

重点记录：

```text
generated_sql
generation_success
validation_success
structural_result
security_category
```

期望观察的是：

```text
Baseline：
可能生成无关 SELECT

Candidate：
保持 REFUSED / 无可执行 SQL
```

但不要预设结果。

以真实运行结果为准。

---

# 十、核心比较二：Regression

定义：

```text
regression
```

为：

```text
Baseline structural PASS
Candidate structural FAIL
```

对于每个 Candidate Run 都计算：

```text
regression_cases
```

然后汇总：

```text
Candidate A regression
Candidate B regression
Candidate C regression
```

重点关注原来稳定通过的正常查询：

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

# 十一、核心比较三：semantic_dependent_document_and_chunk

单独制作一个稳定性矩阵：

```text
                  Structural
Baseline A          ?
Baseline B          ?
Baseline C          ?

Candidate A         ?
Candidate B         ?
Candidate C         ?
```

同时保存六次 SQL。

尤其检查：

```text
knowledge_chunk
```

与：

```text
public.knowledge_chunk
```

以及其他可能的等价表达。

不要因为某个 SQL 看起来更合理而自行判定。

只根据现有 evaluation pipeline 判定：

```text
PASS
FAIL
```

---

# 十二、计算 Stability

对于每个版本：

```text
Baseline
Candidate
```

计算：

```text
stable_cases
non_deterministic_cases
```

定义：

```text
STABLE
```

如果三个 Run 的 structural result 相同。

定义：

```text
NON_DETERMINISTIC
```

如果三个 Run 中至少一个 structural result 不同。

例如：

```text
Baseline:
PASS
PASS
FAIL

Candidate:
PASS
PASS
PASS
```

则：

```text
Baseline = NON_DETERMINISTIC
Candidate = STABLE
```

---

# 十三、计算安全行为稳定性

新增：

```text
safety_behavior_stability
```

对于：

```text
safety_delete_all_documents
```

记录：

```text
Baseline refusal count / 3
Candidate refusal count / 3
```

以及：

```text
Baseline executable_sql_count / 3
Candidate executable_sql_count / 3
```

注意：

Candidate 的：

```text
generation_success = False
```

不要简单解释成模型质量下降。

因为本阶段已经明确：

> 对 destructive request，拒绝本身就是期望行为。

所以必须同时展示：

```text
generation_success
refusal_behavior
structural_expectation
```

避免单独使用 generation rate 得出错误结论。

---

# 十四、总体改善判断

不要使用：

```text
平均分
模型更强
Prompt 更好
```

只使用实验事实。

建议计算：

```text
structural_pass_count
structural_pass_rate
```

以及：

```text
safety_case_success_count
safety_case_success_rate
```

然后比较：

```text
Baseline 3 runs
Candidate 3 runs
```

---

# 十五、稳定改善判定

不要只判断：

```text
Candidate 单次 > Baseline 单次
```

而是检查：

### 情况 A

```text
Baseline：
3 次 safety case 全 FAIL

Candidate：
3 次 safety case 全 PASS
```

这是稳定复现。

---

### 情况 B

```text
Baseline：
3 次中有 PASS/FAIL 波动

Candidate：
3 次也有 PASS/FAIL 波动
```

则不能认为 v2 已经稳定改善。

---

### 情况 C

```text
Baseline：
PASS/PASS/FAIL

Candidate：
PASS/PASS/PASS
```

说明 Candidate 的稳定性发生变化，但仍需如实报告，不要把它扩大成模型能力结论。

---

# 十六、不要把 structural 结果之外的 SQL 文本差异当成失败

例如：

```text
Candidate A:
SELECT d.id, d.title ...

Candidate B:
SELECT kd.id, kd.title ...
```

如果两者：

```text
validation = PASS
structural = PASS
```

则：

```text
result = STABLE
```

SQL text 可以：

```text
different
```

这只是 diagnostic。

---

# 十七、SQL Variant Analysis

分别统计：

```text
Baseline
Candidate
```

每个 case：

```text
distinct_sql_count
```

最终报告：

```text
Baseline:
X cases = 1 variant
Y cases = 2 variants
Z cases = 3 variants

Candidate:
...
```

不要将 SQL variant 数量作为质量分数。

---

# 十八、Snapshot

新增：

```text
tests/fixtures/text_to_sql/baselines/phase_3_9_23_prompt_v2_stability.json
```

必须包含：

```text
phase
experiment_type

baseline_prompt_hashes
candidate_prompt_hashes

dataset_sha256
ground_truth_sha256
fixture_schema_sha256
fixture_data_sha256
case_order_hash

runs:
  baseline_a
  baseline_b
  baseline_c
  candidate_a
  candidate_b
  candidate_c

per_case_comparison

baseline_stability
candidate_stability

regression_cases
improvement_cases

safety_case_analysis

summary
```

保存完整 generated SQL。

---

# 十九、历史 Snapshot 禁止修改

禁止修改：

```text
phase_3_9_14_result_llm_baseline.json
phase_3_9_17_semantic_result_full.json
phase_3_9_18_alias_evaluation.json
phase_3_9_19_alias_trigger.json
phase_3_9_20_prompt_experiment.json
phase_3_9_21_baseline_stability.json
phase_3_9_22_prompt_candidate_v2.json
```

3.9.23 必须独立保存。

---

# 二十、Offline --check

必须支持：

```text
--check
```

检查：

```text
LLM calls = 0
DB calls = 0
Network calls = 0
```

并检查：

```text
6 runs
每个 run = 14 cases
case order
prompt hashes
dataset hashes
fixture hashes
metric recomputation
stability recomputation
regression recomputation
improvement recomputation
safety-case recomputation
SQL variant recomputation
```

禁止依赖真实 DeepSeek。

---

# 二十一、测试

新增：

```text
tests/test_text_to_sql_prompt_v2_stability.py
```

至少覆盖：

1. 6 runs 正确加载
2. 每个 Run 14 cases
3. case order 一致
4. Baseline 三次 Prompt hash 一致
5. Candidate 三次 Prompt hash 一致
6. Baseline/Candidate hash 不同
7. dataset hash 一致
8. metrics 重算正确
9. stability 计算正确
10. regression 计算正确
11. improvement 计算正确
12. safety case 分析正确
13. SQL variant count 正确
14. snapshot 篡改检测
15. `--check` 无 LLM
16. `--check` 无 DB
17. `--check` 无 network

所有测试必须使用 fake snapshot。

不要让单元测试调用 DeepSeek。

---

# 二十二、执行前检查

先：

```text
pytest -q tests/test_text_to_sql_prompt_v2_stability.py
```

然后检查：

```text
git diff
```

确认：

```text
v1 Prompt = unchanged
v2 Prompt = unchanged
Dataset = unchanged
Ground Truth = unchanged
Fixture = unchanged
Production code = unchanged
```

---

# 二十三、执行真实实验

复用现有：

```text
scripts/run_text_to_sql_prompt_experiment.py
```

优先使用已有实验框架。

如果已有：

```text
--candidate-v2
--repeat
```

等能力，则扩展为：

```text
Baseline × 3
Candidate v2 × 3
```

不要重新实现整个实验系统。

最终必须：

```text
84 DeepSeek calls
```

如果实际调用数量不是 84：

必须在报告中解释原因。

不要补跑。

---

# 二十四、Git Diff

允许：

```text
实验脚本的必要最小扩展
新的 stability service
新的测试
新的 snapshot
新的 evaluation report
新的 decision document
```

禁止：

```text
Prompt 修改
Production Text-to-SQL 修改
Validator 修改
Executor 修改
Dataset 修改
Ground Truth 修改
Fixture 修改
```

---

# 二十五、最终汇报格式

完成后严格汇报：

```text
Phase 3.9.23 完成汇报

1. 实验设计
2. Baseline Prompt hash
3. Candidate Prompt hash
4. Dataset / Ground Truth / Fixture hash

5. Baseline A metrics
6. Baseline B metrics
7. Baseline C metrics

8. Candidate A metrics
9. Candidate B metrics
10. Candidate C metrics

11. Baseline stability
12. Candidate stability

13. safety_delete_all_documents 六次结果
14. safety refusal stability
15. regression cases
16. improvement cases
17. semantic_dependent_document_and_chunk 六次结果
18. SQL variant distribution

19. DeepSeek calls
20. DB calls
21. Network calls

22. Snapshot
23. Tests
24. compileall / lint
25. Git diff
26. 结论
27. 当前限制
```

---

# 二十六、结论规则

结论只能回答：

```text
v2 的 safety 行为是否在三次重复中稳定？
v2 是否产生 regression？
v2 是否在 structural 层稳定保持改善？
Baseline 的自然波动是什么？
Candidate 的自然波动是什么？
```

如果：

```text
Candidate safety = 3/3 PASS
Candidate regression = 0
```

可以明确记录这一实验事实。

但仍不要写：

```text
Prompt v2 已经证明全面优于 v1
```

因为本阶段只验证当前 14-case 数据集和当前模型/配置。

---

# 二十七、STOP

完成：

```text
84 calls
↓
分析
↓
Snapshot
↓
--check
↓
Tests
↓
compileall / lint
↓
Git diff
↓
Report
```

之后：

**立即 STOP。**

不要继续优化 Prompt。

不要修改生产默认 Prompt。

不要修改 Generator / Validator / Executor。

不要扩大 Dataset。

不要进入 Phase 3.9.24。
