# Phase 3.9.11 — Text-to-SQL Error Diagnosis & Bottleneck Analysis

## 1. 目标

基于已经完成的：

* Phase 3.9.5 Real LLM Baseline
* Phase 3.9.6 Failure Taxonomy
* Phase 3.9.7 Validator Security E2E
* Phase 3.9.8 Executor Security E2E
* Phase 3.9.9 Generation Quality
* Phase 3.9.10 Result-level Correctness

新增一个**统一的错误诊断 / 瓶颈分析层**。

核心问题：

> 当前 Text-to-SQL Pipeline 的失败，到底发生在 Generation、Validation、Expectation、Execution、Result Correctness 还是 Security Boundary？

本阶段只分析，不优化。

---

# 2. 严格范围

允许：

1. 读取已有 baseline / analysis / dataset。
2. 统一不同阶段的 case-level 信息。
3. 建立 deterministic diagnosis model。
4. 统计各阶段通过率和失败分布。
5. 识别 pipeline bottleneck。
6. 生成 diagnosis snapshot。
7. 生成 diagnosis report。
8. 增加单元测试。
9. `--check` 必须完全离线。

---

# 3. 禁止

本阶段禁止修改：

* TextToSQLService
* Prompt
* TextToSQLContext
* AIOrchestratorService
* Router
* Selector
* Composer
* Semantic Layer
* SQLValidatorService
* SQLExecutorService
* RAG
* Tool
* Project Configuration
* Project Registry
* API
* 数据库 schema
* LLM provider
* Embedding
* Reranker

禁止：

* ❌ Prompt Optimization
* ❌ Model Switching
* ❌ 自动修复 SQL
* ❌ Retry 策略调整
* ❌ LLM-as-a-Judge
* ❌ 新增 failure category
* ❌ 修改 3.9.3 dataset
* ❌ 修改历史 baseline
* ❌ 重新生成历史 baseline
* ❌ 生产数据库写入

---

# 4. 核心原则

本阶段不是重新跑 DeepSeek。

优先使用：

```text
3.9.5 snapshot
3.9.6 taxonomy snapshot
3.9.9 quality snapshot
3.9.10 result snapshot
3.9.3 dataset
```

建立统一诊断。

如果某个历史 snapshot 没有保存足够信息：

> 标记 UNKNOWN / N/A。

不要猜。

---

# 5. 新增 Service

新增：

```text
backend/app/services/text_to_sql_diagnosis_service.py
```

建议 DTO：

```text
DiagnosisCase
DiagnosisSummary
DiagnosisInput
DiagnosisDimension
DiagnosisService
```

全部尽量 immutable。

---

# 6. Diagnosis Dimension

每个 case 分成以下独立维度：

## Generation

```text
SUCCESS
FAILURE
UNKNOWN
```

来源：

3.9.5 / 3.9.9。

---

## Validation

```text
ACCEPTED
REJECTED
UNKNOWN
```

来源：

3.9.5 / 3.9.9。

---

## Structural Expectation

```text
MATCH
MISMATCH
N/A
UNKNOWN
```

注意：

这里使用现有 dataset expectation，不重新解释。

---

## Execution

```text
SUCCESS
FAILURE
N/A
UNKNOWN
```

来源：

3.9.9。

---

## Result Correctness

```text
CORRECT
INCORRECT
N/A
UNKNOWN
```

来源：

3.9.10。

---

## Security

复用 3.9.6：

```text
security_categories
```

不要新增 security category。

---

# 7. Bottleneck Stage

新增一个**确定性**的 bottleneck 判断。

优先级：

```text
GENERATION
VALIDATION
STRUCTURAL_EXPECTATION
EXECUTION
RESULT_CORRECTNESS
```

但只有在对应阶段明确失败时才判定。

例如：

```text
generation failure
```

则：

```text
bottleneck = GENERATION
```

如果：

```text
generation success
validation rejected
```

则：

```text
bottleneck = VALIDATION
```

如果：

```text
generation success
validation accepted
structural expectation mismatch
```

则：

```text
bottleneck = STRUCTURAL_EXPECTATION
```

如果：

```text
generation success
validation accepted
execution failure
```

则：

```text
bottleneck = EXECUTION
```

如果：

```text
execution success
result incorrect
```

则：

```text
bottleneck = RESULT_CORRECTNESS
```

---

# 8. 安全 Case 特殊处理

对于：

```text
safety_delete_all_documents
```

不能简单说：

```text
bottleneck = STRUCTURAL_EXPECTATION
```

因为它属于安全边界测试。

必须同时保留：

```text
security_categories
```

以及：

```text
structural_expectation = MISMATCH
```

不要把：

```text
SECURITY_LLM_REFUSAL
```

解释成模型错误。

3.9.6 已明确：

> dangerous SQL 没有被交给 Validator 执行，而是得到安全 SQL，因此产生 expectation mismatch。

本阶段只能事实性描述。

---

# 9. DiagnosisCase

建议至少包含：

```text
case_id
project_id
generation_status
validation_status
expectation_status
execution_status
result_status
security_categories
bottleneck
```

以及：

```text
evaluable
```

用于表示该 case 是否具有完整的结果级证据。

---

# 10. Evaluable 定义

不要把所有 case 都强行纳入 result correctness。

建议：

```text
evaluable_result =
result_status in {CORRECT, INCORRECT}
```

而：

```text
N/A
UNKNOWN
```

均不进入 result accuracy 分母。

---

# 11. Bottleneck Summary

统计：

```text
generation_failure_count
validation_failure_count
expectation_mismatch_count
execution_failure_count
result_incorrect_count
security_case_count
```

注意：

这些是不同维度。

不要简单把它们相加后声称：

```text
total failures = ...
```

因为同一个 case 可能同时具有多个标签。

---

# 12. Multi-label 原则

例如：

```text
safety_delete_all_documents
```

可能同时：

```text
EXPECTATION_MISMATCH
SECURITY_LLM_REFUSAL
SECURITY_EXPECTATION_MISMATCH
```

所以报告中必须明确：

> Failure distribution is multi-label.

不能把各类别数量直接相加作为 case 总失败数。

---

# 13. Bottleneck Case Count

另外单独计算：

```text
bottleneck_distribution
```

每个 case 只能有一个 primary bottleneck：

```text
GENERATION
VALIDATION
STRUCTURAL_EXPECTATION
EXECUTION
RESULT_CORRECTNESS
NONE
UNKNOWN
```

这样才能得到：

> 在当前可观测证据中，哪个阶段出现了 primary failure。

---

# 14. 3.9.5～3.9.10 数据一致性

必须检查：

### 3.9.5

14 cases。

### 3.9.9

14 cases。

### 3.9.10

14 cases。

如果 case 数量不一致：

> STOP 并报告。

不要自行补齐。

---

# 15. 不允许重新调用 LLM

分析脚本：

```text
scripts/analyze_text_to_sql_diagnosis.py
```

默认：

```bash
python scripts/analyze_text_to_sql_diagnosis.py
```

如果需要真实历史数据，只能读取已经 sealed 的 snapshot。

不得：

```text
DeepSeek
OpenAI-compatible client
TextToSQLService.generate()
```

---

# 16. Snapshot

新增：

```text
tests/fixtures/text_to_sql/baselines/phase_3_9_11_diagnosis.json
```

至少包含：

```json
{
  "phase": "3.9.11",
  "dataset_version": "1.0",
  "total_cases": 14,
  "bottleneck_distribution": {},
  "failure_distribution": {},
  "security_distribution": {},
  "result_evaluable_cases": 0
}
```

实际内容根据历史 snapshot 计算。

---

# 17. Report

新增：

```text
docs/evaluation/text-to-sql-diagnosis-3.9.11.md
```

必须包含：

## 1. Scope

说明本阶段是：

> offline diagnostic analysis

---

## 2. Evidence Sources

明确：

```text
3.9.5
3.9.6
3.9.9
3.9.10
3.9.3
```

---

## 3. Pipeline Metrics

至少列：

```text
Generation Success
Validator Acceptance
Expectation Match
Execution Success
Result Accuracy
```

但不要重新解释历史指标。

---

## 4. Primary Bottleneck Distribution

例如：

```text
GENERATION: x
VALIDATION: x
STRUCTURAL_EXPECTATION: x
EXECUTION: x
RESULT_CORRECTNESS: x
NONE: x
UNKNOWN: x
```

如果为 0，也保留。

---

## 5. Failure Distribution

使用 3.9.6 已有 taxonomy。

禁止新增类别。

---

## 6. Security Distribution

使用已有 security categories。

---

## 7. Per-case Diagnosis

14 个 case 全部列出：

```text
case_id
generation
validation
expectation
execution
result
security
bottleneck
```

---

## 8. Evidence Gaps

明确：

哪些阶段无法判断。

例如：

```text
Result Correctness:
3/14 evaluable
11/14 N/A
```

---

# 18. 不要做“模型好坏”评价

本阶段禁止出现：

```text
DeepSeek 很差
DeepSeek 很好
模型不行
模型准确率很高
模型准确率很低
```

只报告：

```text
observed metrics
observed failures
observed evidence gaps
```

---

# 19. 不做优化建议

本阶段报告不要提出：

```text
应该修改 Prompt
应该换模型
应该增加 RAG
应该增加 Tool
应该调整 temperature
```

这些属于后续阶段。

3.9.11 只回答：

> **问题发生在哪里。**

不回答：

> **应该怎么解决。**

---

# 20. Tests

新增：

```text
tests/test_text_to_sql_diagnosis_service.py
```

至少覆盖：

### Dimension Mapping

* generation success
* generation failure
* validation accepted
* validation rejected
* expectation match
* expectation mismatch
* execution success
* execution failure
* result correct
* result incorrect
* N/A
* UNKNOWN

### Bottleneck

测试：

```text
generation failure → GENERATION
validation failure → VALIDATION
expectation mismatch → STRUCTURAL_EXPECTATION
execution failure → EXECUTION
result incorrect → RESULT_CORRECTNESS
all success → NONE
insufficient evidence → UNKNOWN
```

### Security

确保：

```text
SECURITY_LLM_REFUSAL
SECURITY_EXPECTATION_MISMATCH
```

被原样保留。

不要生成新的 category。

### Multi-label

验证一个 case 可以同时拥有多个 security labels。

### Determinism

同样输入：

```text
result1 == result2
```

### Immutability

输入不能被修改。

---

# 21. `--check`

必须支持：

```bash
python scripts/analyze_text_to_sql_diagnosis.py --check
```

要求：

```text
0 LLM
0 PostgreSQL
0 network
0 DB writes
```

只读取：

```text
snapshot
dataset
```

并验证：

* snapshot schema
* case count
* metric consistency
* distribution consistency
* security categories
* bottleneck distribution
* arithmetic consistency

---

# 22. Historical Integrity

完成后检查：

```text
3.9.4 snapshot SHA256 unchanged
3.9.5 snapshot SHA256 unchanged
3.9.6 snapshot SHA256 unchanged
3.9.9 snapshot SHA256 unchanged
3.9.10 snapshot SHA256 unchanged
```

如果任何历史文件发生变化：

> STOP。

---

# 23. Dataset

本阶段：

> 不允许修改 `text_to_sql_regression.yaml`。

3.9.10 已经记录了 dataset hash：

```text
1D0D1919CC789669F41B497CF4E089FC04537CF04851D4BBAAF177AC8176E731
```

本阶段必须保持不变。

---

# 24. Verification

执行：

```bash
python -m pytest tests/test_text_to_sql_diagnosis_service.py -q
```

然后：

```bash
python -m compileall backend tests scripts
```

然后：

```bash
python scripts/analyze_text_to_sql_diagnosis.py --check
```

然后重新执行已有：

```bash
python scripts/evaluate_text_to_sql_quality.py --check
python scripts/evaluate_text_to_sql_result_quality.py --check
```

如果 3.9.5 / 3.9.6 历史脚本仍可执行，也继续执行其 `--check`。

---

# 25. Final Report

完成后只报告：

1. 新增文件
2. 是否修改生产代码
3. Dataset 是否变化
4. 14 case 是否全部进入 diagnosis
5. Primary bottleneck distribution
6. Failure taxonomy distribution
7. Security distribution
8. Result-level evaluable cases
9. Result-level correct / incorrect
10. Tests
11. compileall
12. `--check`
13. 历史 snapshot hash
14. DB residue
15. 本阶段证明什么
16. 本阶段没有证明什么

---

# 26. STOP

完成后：

> **PHASE 3.9.11 COMPLETE**

立即停止。

不要自行进入：

* 3.9.12
* Prompt Optimization
* Model Comparison
* Model Replacement
* Production Optimization
* 4.0

等待下一条指令。
