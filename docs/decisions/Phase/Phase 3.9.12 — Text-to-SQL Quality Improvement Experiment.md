# Phase 3.9.12 — Controlled Text-to-SQL Prompt Optimization Experiment

## 1. 目标

基于 Phase 3.9.5～3.9.11 已建立的 Text-to-SQL baseline 和 diagnosis，进行第一次**受控 Prompt 优化实验**。

核心问题：

> 在不修改 Text-to-SQL 生成器、Validator、Executor、Router、Schema/Semantic Context 的情况下，仅调整 Prompt，是否能够改善当前 14-case regression dataset 的结构性表现，同时不降低安全性？

本阶段是：

> **Experiment，不是 Production Optimization。**

---

# 2. 本阶段唯一允许的生产修改

只允许修改：

```text
Text-to-SQL Prompt
```

即：

```text
backend/app/services/text_to_sql_service.py
```

中实际用于 Text-to-SQL generation 的 system/user prompt 构造内容。

如果项目实际 Prompt 位于其他明确的 Text-to-SQL Prompt 文件，则使用实际位置。

除此之外：

> **禁止修改任何生产逻辑。**

---

# 3. 禁止修改

禁止修改：

* TextToSQLService 的控制流程
* retry 次数
* temperature
* model
* provider
* Context DTO
* Schema Serializer
* Semantic Serializer
* Relevant Table Selector
* SQL Validator
* SQL Executor
* AI Router
* AI Orchestrator
* Project Configuration
* RAG
* Tool
* Embedding
* Reranker
* API
* database schema
* regression dataset
* baseline snapshot
* evaluation taxonomy

尤其禁止通过代码逻辑“修正”模型生成结果。

---

# 4. 为什么只改 Prompt

Phase 3.9.11 已显示：

```text
GENERATION       0 primary failures
VALIDATION       0 primary failures
EXECUTION        0 primary failures
RESULT           0 primary failures
STRUCTURAL       1 primary failure
```

唯一 mismatch：

```text
safety_delete_all_documents
```

属于安全边界设计。

因此本阶段不能为了提高：

```text
13/14 → 14/14
```

而削弱安全边界。

---

# 5. Baseline 必须冻结

继续使用：

```text
tests/fixtures/text_to_sql/text_to_sql_regression.yaml
```

当前 dataset hash：

```text
1D0D1919CC789669F41B497CF4E089FC04537CF04851D4BBAAF177AC8176E731
```

本阶段：

> **禁止修改 dataset。**

Baseline 使用 Phase 3.9.5：

```text
14 cases
```

以及 Phase 3.9.9 / 3.9.10 已有指标作为辅助参考。

---

# 6. 优化原则

不要“大改 Prompt”。

只允许进行**最小、明确、可解释的 Prompt 修改**。

重点增强三个方面：

## A. Schema 使用纪律

明确要求：

> 只能使用 Context 中提供的真实 table / column。

不要猜：

```text
warehouse_name
stock_qty
```

如果 Schema 中实际没有这些字段。

---

## B. Semantic 使用纪律

明确要求：

> Semantic Context 是业务解释，不是数据库结构来源。

数据库字段必须以 Schema Context 为准。

---

## C. SQL 目标纪律

明确要求：

> 先理解问题，再选择必要的表和字段，生成最小化 SQL。

避免：

* 无关 JOIN
* 无关字段
* 猜测不存在字段
* 无意义复杂 SQL

---

# 7. 安全 Prompt 不得弱化

必须保留并强化：

> Only generate read-only SELECT queries.

并明确：

```text
DELETE
UPDATE
INSERT
DROP
ALTER
TRUNCATE
CREATE
GRANT
REVOKE
```

均不得生成。

但是：

> 不允许通过 Prompt 特判 `safety_delete_all_documents` case。

Prompt 必须是通用规则。

---

# 8. 重要：不要加入 Case-specific Hack

禁止出现：

```text
if question contains "delete all"
```

或者：

```text
if case_id == ...
```

Prompt 中也禁止出现 regression dataset 的具体 case ID。

不能为了提高 benchmark 分数而针对测试案例作弊。

---

# 9. 建议 Prompt 结构

保留当前 Prompt 的：

```text
ROLE
HARD SQL CONSTRAINTS
HOW TO USE CONTEXT
SECURITY
```

在此基础上最小调整。

推荐逻辑：

```text
ROLE
↓
TASK
↓
SCHEMA IS SOURCE OF TRUTH
↓
SEMANTIC IS BUSINESS GUIDANCE
↓
QUERY PLANNING RULES
↓
SQL CONSTRUCTION RULES
↓
SECURITY
↓
OUTPUT FORMAT
```

不要引入 Chain-of-Thought 输出要求。

不要要求模型输出内部推理过程。

---

# 10. 不改变 Output Contract

当前 TextToSQLService 的输出格式必须保持完全兼容。

例如当前如果要求：

```text
SQL only
```

仍然：

```text
SQL only
```

不要改成：

```text
Explanation:
SQL:
```

除非当前代码本来就支持这种格式。

---

# 11. 新增 Experiment Snapshot

不要覆盖 3.9.5。

新增：

```text
tests/fixtures/text_to_sql/baselines/phase_3_9_12_prompt_optimization.json
```

必须记录：

```text
phase
dataset_version
dataset_sha256
baseline_phase
experiment_name
prompt_version
total_cases

generation_success
generation_success_rate

validator_accepted
validator_acceptance_rate

expectation_matched
expectation_match_rate

execution_attempted
execution_success
execution_success_rate

result_evaluable
result_correct
result_incorrect
result_accuracy

security_cases
security_expectation_mismatches
```

---

# 12. Prompt Version

给本次 Prompt 一个固定版本，例如：

```text
prompt_version: 3.9.12-v1
```

不要使用：

```text
latest
optimized
final
best
```

---

# 13. 新增实验脚本

新增：

```text
scripts/evaluate_text_to_sql_prompt_experiment.py
```

职责：

```text
Load dataset
↓
Run current Prompt
↓
Existing TextToSQLService
↓
Existing Validator
↓
Existing Executor
↓
Existing evaluation checkers
↓
Generate experiment metrics
```

尽可能复用现有 evaluation service。

不要复制已有评估逻辑。

---

# 14. 不修改历史 Snapshot

以下文件必须保持不变：

```text
phase_3_9_4_baseline.json
phase_3_9_5_real_llm_baseline.json
phase_3_9_6_analysis.json
phase_3_9_9_quality_baseline.json
phase_3_9_10_result_quality_baseline.json
phase_3_9_11_diagnosis.json
```

本实验只新增自己的 snapshot。

---

# 15. Real DeepSeek Run

使用当前已经配置好的：

```text
provider = deepseek
model = deepseek-chat
base_url = https://api.deepseek.com
```

不要修改：

* model
* temperature
* retry
* max attempts

否则实验不再是 Prompt-only experiment。

---

# 16. 必须记录实验条件

报告中记录：

```text
Provider
Model
Prompt version
Dataset version
Dataset SHA256
Retry configuration
Validator configuration
Executor configuration
```

如果某个配置无法从已有配置读取：

> 标记 UNKNOWN。

不要猜。

---

# 17. 关键对照

必须把：

```text
3.9.5 Baseline
```

与：

```text
3.9.12 Prompt Experiment
```

放在同一张表。

例如：

| Metric               | 3.9.5 Baseline | 3.9.12 |
| -------------------- | -------------: | -----: |
| Generation Success   |                |        |
| Validator Acceptance |                |        |
| Expectation Match    |                |        |
| Execution Success    |                |        |
| Result Accuracy      |                |        |
| Security Cases       |                |        |

注意：

> 不允许使用“更好 / 更差 / 胜出 / 最佳”等主观词。

只报告数值变化。

---

# 18. Result Accuracy

继续遵循 3.9.10 定义。

只有：

```text
result_expectation
```

存在并且实际执行结果可验证的 case 才进入分母。

当前 baseline：

```text
3 / 14
```

如果实验也是：

```text
3 / 14
```

才比较：

```text
3/3 vs 3/3
```

不能把 N/A 当成失败。

---

# 19. Security Analysis

重点检查：

```text
safety_delete_all_documents
```

但是：

> 不针对这个 case 做 Prompt 特判。

记录：

```text
generation status
validation status
expectation status
security categories
```

如果 Prompt 优化后：

```text
SECURITY_LLM_REFUSAL
```

消失，但生成了危险 SQL：

这是严重问题。

必须立即：

> STOP。

不能继续实验。

---

# 20. Security Gate

本阶段存在硬性 Gate：

### Gate 1

所有原本 Validator 应拒绝的危险 SQL：

> 不得因为 Prompt 优化而变成可执行危险 SQL。

### Gate 2

所有 Executor 安全测试：

> 必须继续通过。

### Gate 3

3.9.7 / 3.9.8 历史测试必须通过。

### Gate 4

不得出现生产数据库写入。

如果任何 Gate 失败：

```text
EXPERIMENT FAILED
```

立即停止。

---

# 21. Regression Gate

实验不能只看 Expectation Match。

必须检查：

```text
Generation
Validation
Expectation
Execution
Result
Security
```

如果某一个指标下降，需要如实记录。

特别是：

> 不允许为了提高一个指标而忽略另一个指标。

---

# 22. Tests

新增：

```text
tests/test_text_to_sql_prompt_experiment.py
```

至少覆盖：

### Prompt

* prompt version
* required sections exist
* schema source-of-truth instruction
* semantic guidance instruction
* security instructions
* no case-specific IDs
* no regression-case hardcoding
* no chain-of-thought requirement

### Dataset

* dataset hash unchanged
* 14 cases
* case order unchanged

### Snapshot

* schema valid
* metric arithmetic
* dataset hash
* prompt version

### Security

测试实验代码不能绕过：

```text
Validator
Executor
```

### Offline Check

`--check`：

```text
0 LLM
0 PostgreSQL
0 network
0 DB write
```

---

# 23. `--check`

新增：

```bash
python scripts/evaluate_text_to_sql_prompt_experiment.py --check
```

要求：

只能读取：

```text
snapshot
dataset
```

检查：

```text
dataset hash
snapshot structure
metric arithmetic
14 cases
prompt version
```

不能调用：

```text
DeepSeek
PostgreSQL
TextToSQLService.generate()
```

---

# 24. Report

新增：

```text
docs/evaluation/text-to-sql-prompt-optimization-3.9.12.md
```

必须包含：

## 1. Experiment Scope

说明：

> Prompt-only controlled experiment.

## 2. Experiment Conditions

Provider / Model / Prompt version / Dataset / Config。

## 3. Baseline vs Experiment

表格对比。

## 4. Per-case Results

14 个 case。

至少：

```text
case_id
generation
validation
expectation
execution
result
security
```

## 5. Security Gate

明确：

```text
PASS / FAIL
```

以及依据。

## 6. Regression Analysis

列出所有发生变化的 case。

不要只列成功案例。

## 7. Findings

只描述事实：

例如：

> Expectation Match Rate changed from X to Y.

不要写：

> 新 Prompt 更好。

## 8. Limitations

必须明确：

* 仅 14 cases
* DeepSeek 单模型
* 单次实验可能存在随机性
* Result Accuracy 当前覆盖有限
* 不代表生产环境全面质量

---

# 25. 不允许自动迭代

这是本阶段非常重要的一条。

只做：

```text
Prompt v1
→ Run
→ Evaluate
→ Report
```

不要：

```text
v1
→ 看结果
→ 自动改 Prompt
→ v2
→ 再跑
→ v3
```

也不要让 CodeBuddy 自动搜索最佳 Prompt。

本阶段只允许：

> **一个实验版本：3.9.12-v1**

---

# 26. 最终验证

执行：

```bash
python -m pytest tests/test_text_to_sql_prompt_experiment.py -q
```

然后：

```bash
python -m compileall backend tests scripts
```

然后：

```bash
python scripts/evaluate_text_to_sql_prompt_experiment.py --check
```

再执行：

```bash
python scripts/evaluate_text_to_sql_quality.py --check
python scripts/evaluate_text_to_sql_result_quality.py --check
python scripts/analyze_text_to_sql_diagnosis.py --check
```

以及：

```text
3.9.7 security E2E
3.9.8 executor security E2E
```

如果项目已有对应测试文件，直接执行，不要重新实现。

---

# 27. 最终报告格式

完成后只返回：

```text
PHASE 3.9.12 COMPLETE

1. Prompt version
2. Modified production files
3. Dataset hash
4. Baseline metrics
5. Experiment metrics
6. Changed cases
7. Security Gate
8. Regression Gate
9. Tests
10. compileall
11. --check
12. Historical snapshot hashes
13. DB residue
14. What this experiment demonstrates
15. What it does not demonstrate
```

特别注意：

不要写：

```text
Prompt 优化成功
Prompt 更好
模型提升了 X%
```

除非只是客观描述指标变化，例如：

```text
Expectation Match Rate:
92.86% → 100%
```

---

# 28. STOP

完成：

```text
PHASE 3.9.12 COMPLETE
```

后立即停止。

不要自动进入：

* 3.9.13
* Model Comparison
* Prompt v2
* Prompt Search
* Production Prompt Adoption
* 4.0

等待下一条指令。
