你现在开始执行 **Phase 3.9.9 — Text-to-SQL Generation Quality Evaluation**。

## 一、阶段目标

在 Phase 3.9.3～3.9.8 已完成的基础上，对当前 Text-to-SQL 系统建立一份**真实生成质量基线**。

本阶段只做 Evaluation / Analysis。

**禁止修改生产逻辑。**

不要修改：

* TextToSQLService
* TextToSQLContext
* AI Orchestrator
* Router
* Schema Selector
* Schema Composer
* Semantic Layer
* SQL Validator
* SQL Executor
* Project Configuration
* RAG
* Tool Framework
* Prompt
* 3.9.3 regression dataset
* 3.9.4 baseline snapshot
* 3.9.5 real LLM snapshot
* 3.9.6 taxonomy snapshot
* 3.9.7 / 3.9.8 测试

也不要新增新的运行时配置。

---

# 二、先阅读现有实现

先检查：

```text
backend/app/services/text_to_sql_service.py
backend/app/services/text_to_sql_evaluation_service.py
backend/app/services/text_to_sql_evaluation_taxonomy.py

tests/fixtures/text_to_sql/text_to_sql_regression.yaml

tests/fixtures/text_to_sql/baselines/
```

以及：

```text
docs/evaluation/
```

理解当前已有：

* Evaluation Runner
* Regression Dataset
* Baseline
* Real LLM Evaluation
* Failure Taxonomy
* Validator Security E2E
* Executor Security E2E

**不要重复实现已有功能。**

---

# 三、本阶段只增加 Evaluation 能力

新增一个独立的分析服务，例如：

```text
backend/app/services/text_to_sql_quality_evaluation_service.py
```

如果现有架构已有等价能力，则优先复用，不要重复造轮子。

建议提供纯数据 DTO，例如：

```text
TextToSQLQualityMetrics
TextToSQLCaseQuality
TextToSQLQualitySummary
```

至少统计以下指标：

```text
total_cases

generation_success
generation_failure
generation_success_rate

validator_accepted
validator_rejected
validator_acceptance_rate

expectation_matched
expectation_mismatched
expectation_match_rate

execution_success
execution_failure
execution_success_rate

result_correct
result_incorrect
result_accuracy
```

注意：

如果当前 Runner / Dataset 无法可靠判断某项指标，就使用：

```text
N/A
```

不要猜测。

---

# 四、必须区分三个概念

不要把下面三个概念混为一谈：

### 1. SQL 生成成功

LLM 是否返回了可解析的 SQL。

### 2. SQL Validator 接受

生成 SQL 是否通过当前真实 Validator。

### 3. SQL 结果正确

SQL 执行后的结果是否符合 regression case 的 expected result。

例如：

```text
LLM generated SQL
        ↓
Validator PASS
        ↓
SQL executes
        ↓
result wrong
```

这应该统计为：

```text
generation = success
validation = accepted
execution = success
result = incorrect
```

而不是简单算成成功。

---

# 五、结果正确性

优先检查现有 regression dataset 是否已经提供足够的 expected information。

例如：

```yaml
expected:
  ...
```

或者：

```yaml
expectation:
  ...
```

根据**现有数据结构**判断。

不要修改：

```text
tests/fixtures/text_to_sql/text_to_sql_regression.yaml
```

如果某些 case 没有足够的信息判断最终 SQL 结果是否正确：

```text
result_correct = N/A
```

不要人为增加 expected result。

---

# 六、真实 LLM

如果当前项目已有真实 DeepSeek Evaluation Runner：

复用：

```text
DeepSeek
deepseek-chat
```

使用项目现有 `.env`。

**不要要求用户提供 API Key。**

不要修改 API Key。

不要输出 API Key。

---

# 七、重要：不要重复 3.9.5

3.9.5 已经测试过：

```text
14 cases
generation success
validator acceptance
expectation matching
```

本阶段重点不是简单复制 3.9.5。

重点是：

> 在现有 Evaluation 数据基础上，进一步判断“生成 SQL 的执行与结果质量”。

如果现有 Runner 已经能执行 SQL，则直接复用。

如果无法判断结果正确性，则明确报告：

```text
当前 Dataset 不足以进行可靠的 result correctness evaluation。
本阶段只建立 execution-level metrics。
```

不要修改 Dataset 来强行实现。

---

# 八、建议增加测试

新增：

```text
tests/test_text_to_sql_quality_evaluation_service.py
```

测试至少覆盖：

1. generation success
2. generation failure
3. validator accepted
4. validator rejected
5. execution success
6. execution failure
7. result correct
8. result incorrect
9. N/A 不参与比例计算
10. zero-case 不产生除零错误
11. summary arithmetic consistency
12. deterministic output

全部使用 fake/stub。

**不要让单元测试调用 DeepSeek。**

---

# 九、真实 Evaluation Script

如果当前项目已有：

```text
scripts/
```

增加：

```text
scripts/evaluate_text_to_sql_quality.py
```

或者复用已有 3.9.5 evaluation script，避免重复。

要求：

### 默认模式

执行真实 LLM evaluation。

### `--check`

必须：

```text
offline
zero LLM
zero network
zero database
```

只检查已经存在的 snapshot/report。

不能出现：

```python
TextToSQLService(...)
```

真实 LLM client 初始化。

不能调用：

```text
get_engine()
```

不能访问数据库。

---

# 十、Snapshot

如果本阶段需要生成 snapshot：

新增：

```text
tests/fixtures/text_to_sql/baselines/phase_3_9_9_quality_baseline.json
```

不要修改：

```text
phase_3_9_4_baseline.json
phase_3_9_5_real_llm_baseline.json
phase_3_9_6_analysis.json
```

Snapshot 必须包含：

```text
phase
dataset_version
total_cases
generation metrics
validation metrics
execution metrics
result metrics
```

如果某些指标 N/A，明确记录：

```json
null
```

不要填 0。

---

# 十一、Report

新增：

```text
docs/evaluation/text-to-sql-quality-baseline-3.9.9.md
```

报告必须包含：

## 1. Scope

说明本阶段只做质量评估，不修改生产逻辑。

## 2. Dataset

说明：

```text
14 cases
dataset version
case categories
```

## 3. Metrics

表格：

```text
Metric | Result
```

## 4. Per-case result

至少：

```text
case_id
generation
validation
execution
result
```

## 5. Failure distribution

按现有 taxonomy 分类。

不要人为创造新的失败类别。

## 6. Findings

只描述事实。

例如：

```text
X/14 cases executed successfully.
Y/14 cases had result-level correctness evidence.
```

不要写：

```text
DeepSeek 很差
Prompt 很差
Schema 很差
```

除非有证据。

## 7. Limitations

明确：

* dataset size
* LLM nondeterminism
* result correctness coverage
* current expected-result limitations
* no production DB

---

# 十二、禁止事项

本阶段禁止：

```text
❌ 修改 Prompt
❌ 修改 TextToSQLService
❌ 修改 Validator
❌ 修改 Executor
❌ 修改 Dataset
❌ 修改已有 baseline
❌ 修改 3.9.6 taxonomy
❌ 修改生产代码来提高指标
❌ 为了测试而关闭安全检查
❌ 使用生产数据库
❌ 把 API Key 写入代码
❌ 把 API Key 输出到报告
```

---

# 十三、验证

完成后运行：

### 非 DB：

```powershell
python -m pytest -q
```

### DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

Windows PowerShell 不要使用：

```text
RUN_DB_TESTS=1 pytest
```

因为这是 Unix shell 写法。

### compile：

```powershell
python -m compileall backend tests scripts
```

### 历史 baseline：

```text
3.9.4 --check
3.9.5 --check
3.9.6 --check
```

### 新 baseline：

```text
3.9.9 --check
```

要求：

```text
offline
zero LLM
zero DB
zero network
```

---

# 十四、最终汇报格式

完成后不要继续进入 3.9.10。

只汇报：

```text
Phase 3.9.9 完成汇报

1. 新增/修改文件
2. 是否修改生产代码
3. Dataset
4. Metrics
5. Per-case summary
6. Failure distribution
7. Snapshot SHA256
8. Report
9. Tests
10. compile/lint
11. 3.9.4/3.9.5/3.9.6/3.9.9 --check
12. DB residue
13. 本阶段证明了什么
14. 本阶段没有证明什么
15. Phase 3.9.9 到此停止
```

**完成后立即停止，不进入 Phase 3.9.10。**
