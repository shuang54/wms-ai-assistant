# Phase 3.9.10 — Text-to-SQL Result-level Correctness Evaluation

## 1. 目标

在 Phase 3.9.9 已完成的 Generation Quality Evaluation 基础上，新增一层：

> **Result-level Correctness Evaluation（结果级正确性评估）**

目标是回答：

> “SQL 不仅生成成功、通过 Validator、能够执行，而且执行结果是否符合预期？”

本阶段只做**评估体系增强**，不修改生产 Text-to-SQL 逻辑。

---

# 2. 严格范围

本阶段只允许做：

1. 为现有 Text-to-SQL regression dataset 增加少量“结果预期”定义。
2. 新增结果正确性 DTO / checker / runner。
3. 对可以安全、确定性验证的测试案例执行结果级校验。
4. 生成独立的 3.9.10 baseline snapshot。
5. 生成评估报告。
6. 增加单元测试。
7. 保持 `--check` 完全离线。

---

# 3. 明确禁止

本阶段禁止：

* ❌ 修改 `TextToSQLService`
* ❌ 修改 Text-to-SQL Prompt
* ❌ 修改 `TextToSQLContext`
* ❌ 修改 `AIOrchestratorService`
* ❌ 修改 Router
* ❌ 修改 Relevant Table Selector
* ❌ 修改 Schema Composer
* ❌ 修改 Semantic Layer
* ❌ 修改 SQL Validator
* ❌ 修改 SQL Executor
* ❌ 修改 RAG
* ❌ 修改 Tool Framework
* ❌ 修改 Project Configuration
* ❌ 修改 Project Registry
* ❌ 修改生产 API
* ❌ 修改数据库表结构
* ❌ 修改已有 3.9.3 regression dataset 的既有字段含义
* ❌ 修改 3.9.4 baseline
* ❌ 修改 3.9.5 baseline
* ❌ 修改 3.9.6 analysis
* ❌ 修改 3.9.9 quality baseline
* ❌ 修改已有 Prompt
* ❌ 增加新的 LLM provider
* ❌ 增加新的 Python 依赖
* ❌ 引入模糊的 LLM-as-a-Judge
* ❌ 用 LLM 判断 SQL 查询结果是否正确
* ❌ 修改生产数据库数据
* ❌ 在 `--check` 模式访问 DeepSeek
* ❌ 在 `--check` 模式访问 PostgreSQL

如果发现现有数据集无法可靠支持结果正确性，请缩小评估范围，不要为了凑覆盖率修改生产代码。

---

# 4. 核心原则

本阶段的“结果正确性”必须是：

> **确定性的、可重复的、代码可验证的。**

不要使用：

> “看起来结果合理”

也不要使用：

> “让 DeepSeek 判断结果对不对”

必须使用 deterministic checker。

---

# 5. 第一步：分析现有 Dataset

先读取：

```text
tests/fixtures/text_to_sql/text_to_sql_regression.yaml
```

以及：

```text
backend/app/services/text_to_sql_evaluation_service.py
backend/app/services/text_to_sql_quality_evaluation_service.py
```

理解现有：

* Case
* Expectations
* Runner
* ExecutionOutcome
* Quality baseline

不要直接修改。

---

# 6. 结果预期设计

为 regression case 增加一个**可选**字段：

```yaml
result_expectation:
```

必须保持向后兼容：

没有 `result_expectation` 的 case：

```text
result_accuracy = N/A
```

不能把 N/A 算成失败。

---

# 7. ResultExpectation 最小设计

优先支持以下确定性规则。

## 7.1 exact_rows

适用于结果非常稳定的简单查询。

示例：

```yaml
result_expectation:
  type: exact_rows
  rows:
    - [1, "row_1"]
```

要求：

* 行数必须一致
* 行顺序必须一致
* 每个字段值必须一致

---

# 8. unordered_rows

用于 SQL 没有明确 ORDER BY，但结果集合确定的情况：

```yaml
result_expectation:
  type: unordered_rows
  rows:
    - [1, "A"]
    - [2, "B"]
```

比较时：

* 不考虑行顺序
* 考虑重复行
* 使用 multiset / Counter 语义
* 不能简单转换成 set，因为可能丢失重复行

---

# 9. scalar

用于 COUNT / SUM / MAX / MIN 等单值查询：

```yaml
result_expectation:
  type: scalar
  value: 12
```

要求：

```text
1 row
1 column
value == expected
```

---

# 10. column_values

用于只关心某一列：

```yaml
result_expectation:
  type: column_values
  column: "code"
  values:
    - "A"
    - "B"
    - "C"
```

必须定义是否考虑顺序。

默认：

```text
ordered = true
```

如果需要无序：

```yaml
ordered: false
```

---

# 11. 不要一次实现过多规则

第一版只实现：

```text
exact_rows
unordered_rows
scalar
column_values
```

不要实现：

* fuzzy matching
* semantic similarity
* LLM judge
* natural language comparison
* tolerance-based numeric comparison
* 自动推断 expected result
* 自动生成 expected result

如果现有测试案例无法安全使用这些规则，保持 N/A。

---

# 12. 新增 Service

新增：

```text
backend/app/services/text_to_sql_result_evaluation_service.py
```

建议包含：

```text
ResultExpectation
ResultCheckInput
ResultCheckResult
ResultAccuracySummary
TextToSQLResultEvaluationService
```

DTO 尽量保持 frozen / immutable，与已有 evaluation service 风格一致。

---

# 13. ResultCheckResult

至少包含：

```text
case_id
applicable
passed
reason
expected
actual
```

其中：

### applicable

表示该 case 是否有 result_expectation。

没有：

```text
applicable = false
passed = null
```

不能：

```text
passed = false
```

---

# 14. Result Accuracy 指标

计算：

```text
result_accuracy =
correct_result_cases / applicable_result_cases
```

例如：

```text
applicable = 5
correct = 4
accuracy = 80%
```

如果：

```text
applicable = 0
```

则：

```text
result_accuracy = null
```

即：

```text
N/A
```

不要输出：

```text
0%
```

---

# 15. 与 3.9.9 指标保持独立

最终报告必须同时区分：

```text
Generation Success Rate
Validator Acceptance Rate
Expectation Match Rate
Execution Success Rate
Result Accuracy
```

特别注意：

> Result Accuracy ≠ Expectation Match Rate

例如：

```text
SQL 结构符合预期
```

不代表：

```text
SQL 查询结果正确
```

---

# 16. Dataset 修改原则

可以对现有 dataset 增加 `result_expectation`。

但是：

### 不允许修改原有 case 的：

```text
id
question
project_id
must_pass_validation
must_contain_tables
must_contain_columns
must_not_contain
must_execute
```

除非发现明显错误。

如果必须修改，应停止并报告，不要自行扩大范围。

---

# 17. 选择测试案例

优先从现有 14 个案例中选择：

1. simple query
2. Top N
3. filter
4. sort
5. aggregation
6. GROUP BY
7. HAVING
8. JOIN
9. date
10. LIMIT

但只选择**结果可以稳定确定**的案例。

Project A / Project B：

如果当前测试 DB 中没有对应真实数据：

```text
result_accuracy = N/A
```

不要为了执行它们创建新的生产数据。

---

# 18. 关于动态数据

如果案例依赖：

```text
created_at
当前时间
动态数据库数据
```

不要硬编码不稳定结果。

可以选择：

```text
N/A
```

或者只验证确定性结构。

本阶段重点是：

> 少量高可信结果正确性案例

而不是追求 14/14 全覆盖。

---

# 19. Runner 集成

不要修改生产 Runner。

可以在：

```text
TextToSQLResultEvaluationService
```

内部复用现有：

```text
TextToSQLEvaluationRunner
```

或者接受：

```text
ExecutionOutcome
```

作为输入。

优先选择低耦合方案。

---

# 20. Real LLM Evaluation

新增脚本：

```text
scripts/evaluate_text_to_sql_result_quality.py
```

运行真实 DeepSeek pipeline：

```text
Question
→ existing Text-to-SQL pipeline
→ SQL
→ existing Validator
→ existing Executor
→ rows
→ ResultEvaluationService
```

但是：

> 不修改生产代码。

---

# 21. 安全要求

Result evaluation 只能读取测试数据库。

不能：

```text
INSERT
UPDATE
DELETE
CREATE
DROP
ALTER
```

不能写生产数据库。

继续使用现有：

```text
project_a
project_b
t2s_exec_sec
```

等测试环境。

---

# 22. Baseline Snapshot

新增：

```text
tests/fixtures/text_to_sql/baselines/phase_3_9_10_result_quality_baseline.json
```

至少包含：

```json
{
  "phase": "3.9.10",
  "dataset_version": "1.0",
  "total_cases": 14,
  "applicable_result_cases": 0,
  "correct_result_cases": 0,
  "incorrect_result_cases": 0,
  "result_accuracy": null
}
```

实际值根据运行结果填写。

不要伪造结果。

---

# 23. Report

新增：

```text
docs/evaluation/text-to-sql-result-quality-3.9.10.md
```

至少包含：

## Scope

说明：

> 本阶段评估 SQL 执行结果是否与 deterministic expected result 一致。

## Dataset

说明：

* dataset version
* case count
* result expectation case count

## Metrics

至少：

```text
Applicable Result Cases
Correct Result Cases
Incorrect Result Cases
Result Accuracy
```

## Per-case Results

至少：

```text
case_id
result expectation type
applicable
passed
reason
```

## Limitations

必须明确：

* 不是所有 14 个 case 都具备 deterministic expected result
* N/A 不代表失败
* 当前只验证测试数据库中的稳定结果
* 不代表生产数据库上的全面正确性
* 不代表所有 SQL 语义都正确

---

# 24. `--check` 模式

必须提供：

```bash
python scripts/evaluate_text_to_sql_result_quality.py --check
```

要求：

```text
0 LLM calls
0 PostgreSQL connections
0 DB writes
0 network calls
```

只能：

```text
读取 snapshot
读取 dataset
重新计算 snapshot 内可验证指标
检查结构
检查算术一致性
```

如果 snapshot 没有保存完整 actual result：

不要声称可以重新计算所有结果。

---

# 25. Snapshot Integrity

保存 snapshot 前后检查 SHA256。

同时确认以下历史文件没有变化：

```text
tests/fixtures/text_to_sql/text_to_sql_regression.yaml
tests/fixtures/text_to_sql/baselines/phase_3_9_4_baseline.json
tests/fixtures/text_to_sql/baselines/phase_3_9_5_real_llm_baseline.json
tests/fixtures/text_to_sql/baselines/phase_3_9_6_analysis.json
tests/fixtures/text_to_sql/baselines/phase_3_9_9_quality_baseline.json
```

如果 dataset 因本阶段新增 `result_expectation` 而发生变化：

需要在最终报告中明确记录新的 dataset hash。

不要声称 dataset unchanged。

---

# 26. Tests

新增：

```text
tests/test_text_to_sql_result_evaluation_service.py
```

至少测试：

### DTO

* valid exact_rows
* valid unordered_rows
* valid scalar
* valid column_values
* invalid type
* immutable input

### Checker

* exact match
* row count mismatch
* value mismatch
* ordered mismatch
* unordered success
* unordered duplicate handling
* scalar success
* scalar shape mismatch
* column_values success
* column_values mismatch

### N/A

测试：

```text
result_expectation=None
→ applicable=false
→ passed=None
```

### Metrics

测试：

```text
4 applicable
3 correct
1 incorrect
→ 75%
```

以及：

```text
0 applicable
→ result_accuracy=None
```

### Determinism

同一个 input 执行两次：

```text
result identical
```

### Immutability

输入对象不能被修改。

---

# 27. 测试禁止联网

所有单元测试必须：

```text
fake/stub
```

不能访问：

```text
DeepSeek
PostgreSQL
```

---

# 28. Final Verification

完成后执行：

```bash
python -m pytest tests/test_text_to_sql_result_evaluation_service.py -q
```

然后：

```bash
python -m compileall backend tests scripts
```

然后执行：

```bash
python scripts/evaluate_text_to_sql_result_quality.py --check
```

同时执行历史检查：

```bash
python scripts/evaluate_text_to_sql_real_llm.py --check
python scripts/analyze_text_to_sql_real_llm_baseline.py --check
python scripts/evaluate_text_to_sql_quality.py --check
```

如果对应脚本名称不同，使用项目当前实际脚本，不要重命名历史脚本。

---

# 29. 最终报告必须回答

完成后只报告：

### 1. 新增文件

### 2. 是否修改生产代码

### 3. Dataset 是否变化

### 4. Result expectation 覆盖多少 case

### 5. Applicable Result Cases

### 6. Correct Result Cases

### 7. Incorrect Result Cases

### 8. Result Accuracy

### 9. 哪些 case 是 N/A，以及原因

### 10. Tests

### 11. compileall

### 12. 所有历史 baseline --check 是否通过

### 13. DB 是否产生残留

### 14. Snapshot SHA256

### 15. 本阶段证明了什么

### 16. 本阶段没有证明什么

---

# 30. 最重要的停止规则

完成 Phase 3.9.10 后：

> **立即 STOP。**

不要自行开始：

```text
3.9.11
3.9.12
4.0
Prompt Optimization
Model Comparison
Production Optimization
```

不要自行修改生产逻辑。

最终只返回：

```text
PHASE 3.9.10 COMPLETE
```

以及上述验证结果。

等待下一步指令。
