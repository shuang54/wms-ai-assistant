# Phase 3.9.20：Text-to-SQL Prompt A/B 实验框架

## 一、阶段目标

建立 Text-to-SQL 的可重复 A/B 实验框架：

```text
Baseline Prompt
      ↓
同一 14-case Dataset
      ↓
同一 DeepSeek Model
      ↓
同一 Schema / Semantic Context
      ↓
同一 Validator
      ↓
同一 Executor
      ↓
同一 Result / Semantic Evaluator

Candidate Prompt
      ↓
完全相同的实验条件
      ↓
同一套 Evaluation
      ↓
A/B 对比
```

本阶段的重点：

**建立实验框架，不优化 Prompt 内容。**

---

# 二、严格原则

## 允许

允许新增：

```text
backend/app/services/text_to_sql_prompt_experiment_service.py

scripts/
    run_text_to_sql_prompt_experiment.py

tests/
    test_text_to_sql_prompt_experiment.py

tests/fixtures/text_to_sql/
    prompt_experiment_cases.yaml

tests/fixtures/text_to_sql/baselines/
    phase_3_9_20_prompt_experiment.json

docs/evaluation/
    text-to-sql-prompt-ab-experiment-3.9.20.md
```

具体文件名可以根据现有项目结构调整。

允许对现有 Prompt 做**只读加载 / 版本化封装**。

---

# 三、禁止事项

本阶段禁止：

```text
修改现有 Baseline Prompt 内容
修改 Candidate Prompt 内容
修改 TextToSQLService
修改 TextToSQLGenerator 核心逻辑
修改 SQL Validator
修改 SQL Executor
修改 AI Router
修改 AI Orchestrator
修改 Semantic Context
修改 Schema Composer
修改 regression dataset
修改 result ground truth
修改 semantic ground truth
修改 alias evaluator
```

禁止新增业务规则。

禁止优化 SQL。

禁止根据实验结果自动修改 Prompt。

---

# 四、Baseline Prompt

必须把当前正式 Prompt 当作：

```text
Baseline
```

Baseline 必须来自当前项目实际使用的：

```text
system prompt
user prompt
retry prompt
```

不要复制一份后手工改写。

建议形成不可变的 Prompt Version：

```text
prompt_version = "v1"
```

如果项目已有 hash/version 机制，直接复用。

记录：

```text
system_prompt_hash
user_prompt_hash
retry_prompt_hash
```

确保实验能够证明使用的是哪个 Prompt。

---

# 五、Candidate Prompt

本阶段 Candidate 不进行优化。

建立：

```text
Candidate A
```

但 Candidate A 必须与 Baseline **内容完全一致**。

也就是说：

```text
Baseline v1
        VS
Candidate A v1
```

两者内容相同。

目的不是获得提升，而是验证：

> A/B 实验框架本身不会因为实验运行方式不同而产生结果差异。

因此本阶段属于：

**Experiment Infrastructure Validation**

而不是 Prompt Optimization。

---

# 六、实验 Case

使用现有：

```text
tests/fixtures/text_to_sql/text_to_sql_regression.yaml
```

保持 14 cases 不变。

不得复制后修改问题。

不得修改：

```text
questions
expectations
security cases
project isolation cases
semantic_expectation
```

实验必须记录：

```text
dataset_sha256
```

---

# 七、实验环境固定

每次 Experiment Run 必须记录：

```text
model
base_url
prompt_version
prompt_hash
dataset_hash
ground_truth_hash
fixture_schema_hash
fixture_data_hash
```

以及：

```text
temperature
max_tokens
```

如果当前 OpenAI-compatible client 没有设置这些参数，则记录实际默认行为，不要擅自修改。

---

# 八、实验 Runner

新增一个纯实验层 Runner。

建议接口：

```python
PromptExperimentRunner
```

职责：

```text
run_baseline()
run_candidate()
compare()
```

但不要让 Runner 自己实现：

```text
SQL generation
SQL validation
SQL execution
result evaluation
```

必须复用项目现有 Service。

结构：

```text
Experiment Runner
       │
       ├── Prompt Provider
       │
       ├── existing TextToSQL Generator
       │
       ├── existing Validator
       │
       ├── existing Executor
       │
       └── existing Evaluation
```

不要复制生产逻辑。

---

# 九、实验结果 DTO

至少记录：

```text
ExperimentCaseResult
```

包含：

```text
case_id
prompt_variant
generated_sql
generation_success
validation_success
structural_expectation_match
execution_success
semantic_result_correct
security_category
failure_categories
```

如果现有 Evaluation DTO 已经提供这些字段，直接复用，不要重复定义。

---

# 十、A/B 对比指标

最终 Summary 至少包含：

### Generation

```text
generation_success
```

### Validation

```text
validation_acceptance
```

### Structural

```text
structural_expectation_accuracy
```

### Execution

```text
execution_success
```

### Semantic Result

```text
semantic_result_accuracy
```

### Security

```text
security_pass
```

### Overall

不要创建：

```text
overall_score
```

不要给 Baseline / Candidate 打分。

本阶段只报告各项客观指标。

---

# 十一、必须保留逐 Case 差异

不要只输出：

```text
Baseline: 100%
Candidate: 100%
```

必须能够看到：

```text
case_id
baseline_result
candidate_result
changed
```

例如：

```text
aggregate_document_count
Baseline: PASS
Candidate: PASS
changed: false
```

如果以后出现：

```text
Baseline: FAIL
Candidate: PASS
```

必须能够继续查看：

```text
baseline_sql
candidate_sql
baseline_failure_category
candidate_failure_category
```

---

# 十二、重要：不要比较 SQL 字符串

禁止：

```text
baseline_sql == candidate_sql
```

作为正确性判断。

例如：

```sql
SELECT COUNT(*) FROM documents;
```

和：

```sql
SELECT COUNT(id) FROM documents;
```

可能得到相同结果。

SQL 文本只用于：

```text
diagnostic
```

真正判断必须继续使用：

```text
Validator
Structural Evaluation
Execution
Semantic Result Evaluation
```

---

# 十三、实验确定性

因为使用真实 LLM：

**不要声称模型输出完全 deterministic。**

实验记录：

```text
run_id
started_at
completed_at
model
prompt_hash
dataset_hash
```

如果 API 返回 usage，也记录：

```text
prompt_tokens
completion_tokens
total_tokens
```

如果没有，就不要伪造。

---

# 十四、Baseline / Candidate 顺序

不要依赖调用完成顺序。

固定：

```text
Baseline first
Candidate second
```

并且 Candidate 必须使用完全相同的：

```text
case order
context
schema
semantic context
project
```

不要随机打乱。

---

# 十五、不要重复污染数据库

Executor 继续使用：

```text
READ ONLY
```

实验过程中不得：

```text
INSERT
UPDATE
DELETE
CREATE
DROP
ALTER
TRUNCATE
```

实验完成后检查：

```text
public knowledge tables
t2s_eval
temporary tables
```

不得出现新的业务数据残留。

---

# 十六、CLI

增加：

```text
python scripts/run_text_to_sql_prompt_experiment.py
```

建议支持：

```text
--variant baseline
--variant candidate
--compare
--check
```

其中：

```text
--check
```

必须：

```text
0 DeepSeek calls
0 DB calls
0 network calls
```

只验证：

```text
snapshot integrity
hash
schema
dataset
prompt version
```

---

# 十七、首次真实实验

首次真实实验只允许：

```text
Baseline v1
Candidate A v1
```

两者 Prompt 内容必须相同。

使用：

```text
DeepSeek deepseek-chat
```

和项目当前真实配置。

不得修改：

```text
temperature
model
schema
dataset
ground truth
```

---

# 十八、Snapshot

新增：

```text
phase_3_9_20_prompt_experiment.json
```

必须记录：

```json
{
  "phase": "3.9.20",
  "experiment_type": "prompt_ab_infrastructure_validation",
  "baseline_prompt_version": "v1",
  "candidate_prompt_version": "v1",
  "baseline_prompt_hash": "...",
  "candidate_prompt_hash": "...",
  "dataset_sha256": "...",
  "ground_truth_sha256": "...",
  "fixture_schema_sha256": "...",
  "fixture_data_sha256": "...",
  "deepseek_calls": "...",
  "db_calls": "...",
  "network_calls": "..."
}
```

特别要求：

```text
baseline_prompt_hash == candidate_prompt_hash
```

如果不相等，实验必须 FAIL，而不是继续比较。

---

# 十九、A/B 一致性 Gate

因为本阶段两个 Prompt 完全相同，所以预期：

```text
Baseline result == Candidate result
```

必须逐 case 比较。

允许存在极少数 LLM 非确定性导致差异，但如果出现差异：

**不要修改 Prompt，也不要自动重跑直到一致。**

必须报告：

```text
case
baseline SQL
candidate SQL
validation
execution
semantic result
```

并明确：

```text
experiment infrastructure validation = PASS / FAIL
```

---

# 二十、测试

至少新增：

```text
Prompt hash mismatch
Dataset hash mismatch
Baseline/Candidate case alignment
Same Prompt produces same hash
Case order fixed
No production service mutation
Result comparison
Snapshot validation
--check does not call LLM
```

运行：

```text
pytest tests/test_text_to_sql_prompt_experiment.py -q

pytest -q

python -m compileall backend tests scripts
```

---

# 二十一、历史检查

必须确认：

```text
3.9.17 semantic = 12/12
3.9.18 alias = exact 19 / alias 0 / unmatched 0
3.9.19 projection variants = 16
```

历史 baseline 不得修改。

如果已有历史 `--check`，继续执行。

---

# 二十二、最终报告

完成后只报告：

1. 新增/修改文件
2. Baseline Prompt version/hash
3. Candidate Prompt version/hash
4. 两者 hash 是否一致
5. Dataset hash
6. Ground Truth hash
7. Fixture hash
8. 14 cases 是否完全对齐
9. Baseline 各项指标
10. Candidate 各项指标
11. 每个 case 是否一致
12. 是否出现 LLM 非确定性差异
13. DeepSeek calls
14. DB calls
15. Network calls
16. 数据库 residue 检查
17. 测试结果
18. compileall
19. Git diff
20. 是否生成新的 snapshot
21. 是否发现实验框架问题

---

# 二十三、最重要的 STOP 条件

本阶段完成后：

**立即 STOP。**

不要：

```text
修改 Candidate Prompt
优化 Prompt
增加 Few-shot
增加 SQL examples
修改 Schema Context
修改 Semantic Context
重新设计 Router
修改 Generator
进入 Phase 3.9.21
```

3.9.20 的唯一目标：

> **证明我们已经拥有一个可以公平比较两个 Text-to-SQL Prompt 版本的实验框架。**

如果 Baseline 与 Candidate 完全一致且所有结果一致，则说明实验基础设施通过，可以在下一阶段正式进行 Prompt Optimization。
