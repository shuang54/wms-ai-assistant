# Phase 3.9.5 — Real LLM Text-to-SQL Baseline

## 一、目标

基于 Phase 3.9.3 的 14-case Text-to-SQL Regression Dataset，以及 Phase 3.9.4 已建立的 Baseline Evaluation Framework，首次使用真实 DeepSeek LLM 运行 Text-to-SQL Evaluation。

本阶段的核心目标只有一个：

> 测量当前真实 LLM Text-to-SQL Pipeline 的实际表现，建立第一份 Real LLM Baseline。

本阶段不是 Prompt 优化阶段，也不是模型优化阶段。

**只测量，不调优。**

---

## 二、必须遵守的边界

### 1. 不修改生产 Text-to-SQL Pipeline

禁止修改：

* `text_to_sql_service.py`
* `text_to_sql_context.py`
* `text_to_sql_evaluation_service.py`
* `sql_validator_service.py`
* `sql_executor_service.py`
* `schema_serializer_service.py`
* `semantic_serializer_service.py`
* `semantic_schema_filter.py`
* `relevant_table_selector.py`
* `database_context_composer.py`
* `ai_orchestrator_service.py`
* Router
* Project Configuration
* Project Context
* RAG
* Tool Framework

除非为了接入现有真实 LLM 配置而发现明确的既有 Bug，否则不要修改生产逻辑。

### 2. 不修改 Dataset

禁止修改：

```text
tests/fixtures/text_to_sql/text_to_sql_regression.yaml
```

保持：

```text
_schema_version: 1.0
14 cases
```

### 3. 不修改 Prompt

本阶段必须使用当前已有 Prompt。

不要为了提高结果而修改：

```text
text_to_sql_system.txt
text_to_sql_user.txt
text_to_sql_retry.txt
```

### 4. 不修改 3.9.4 Baseline

Phase 3.9.4 的 Snapshot 必须保持不变。

不要覆盖：

```text
tests/fixtures/text_to_sql/baselines/phase_3_9_4_baseline.json
```

### 5. 不把真实 LLM 结果写入生产代码

不要：

* hardcode SQL
* hardcode case result
* 修改 Checker 适配结果
* 修改 Dataset 适配结果
* 修改 Prompt 适配结果
* 为失败 case 增加特殊规则

---

# 三、真实模型

优先复用当前项目已经存在的 DeepSeek 配置。

使用已有：

```text
provider = deepseek
model = deepseek-chat
base_url = https://api.deepseek.com
```

API Key：

* 从现有环境变量读取
* 不允许写入 Snapshot
* 不允许写入 Report
* 不允许输出到日志
* 不允许打印完整环境变量
* 不允许提交到代码

不要要求用户提供 API Key。

---

# 四、Evaluation Dataset

继续使用：

```text
tests/fixtures/text_to_sql/text_to_sql_regression.yaml
```

共 14 cases。

必须确认：

```text
dataset version = 1.0
total cases = 14
```

不得修改 Dataset。

---

# 五、真实 LLM Runner

复用 Phase 3.9.3 / 3.9.4 已有：

```text
TextToSQLEvaluationRunner
```

不要重新实现 Evaluation Runner。

需要增加一个非常薄的 Real LLM Generator Adapter / Runner 接入层即可。

原则：

```text
Dataset
   ↓
EvaluationContextResolver
   ↓
真实 Schema / Semantic / Context
   ↓
TextToSQLService
   ↓
DeepSeek
   ↓
SQL Validator
   ↓
Evaluation Checker
```

必须继续使用真实的：

* Relevant Table Selector
* Schema Composer
* SemanticSchemaFilter
* TextToSQLContext
* TextToSQLService
* SQL Validator

不能绕过任何一个。

---

# 六、真实 LLM Evaluation 的关键要求

## 1. 每个 Case 独立执行

14 个 Case 必须分别运行。

不能让前一个 Case 的：

* SQL
* Context
* LLM response
* error
* result

污染下一个 Case。

---

## 2. 记录完整 Case Outcome

Real LLM Snapshot 每个 Case 至少记录：

```text
case_id
passed
validation_passed
matched
error_code
duration_ms
```

可以额外记录：

```text
model
provider
attempt_count
```

但：

**不要默认保存完整 generated SQL。**

如果项目现有安全设计允许，可以保存经过脱敏的 SQL；否则继续不保存 generated SQL。

---

## 3. 必须区分以下状态

### A. LLM 生成失败

例如：

```text
TextToSQLRetryExceededError
```

记录为：

```text
passed = false
error_code = ...
```

不能让整个 Evaluation 因单个 Case 异常而中断。

---

### B. Validator 拒绝

如果 Dataset 期望：

```text
must_pass_validation = false
```

那么：

```text
Validator rejection
```

应该算：

```text
Expectation PASS
```

沿用 3.9.4 的定义。

---

### C. Validator 错误放行

如果 Dataset 要求拒绝，但 Validator 放行：

```text
Expectation FAIL
```

---

### D. SQL Structural Expectation 不匹配

例如：

```text
expected table = knowledge_document
actual table = knowledge_chunk
```

记录：

```text
matched = false
passed = false
```

不要修改 Checker。

---

# 七、增加 Real LLM Baseline Metrics

可以复用：

```text
TextToSQLBaselineMetrics
calculate_baseline_metrics()
```

不要重新发明第二套 Metrics。

Real LLM Baseline 至少报告：

```text
total_cases
passed
failed
expectation_pass_rate
validation_expectation_pass_rate
execution_pass_rate
security_pass_rate
project_isolation_pass_rate
```

另外增加：

```text
llm_generation_success_rate
```

定义：

```text
成功获得可进入 Validator 的 SQL
/
Total Cases
```

注意：

```text
llm_generation_success_rate
```

和：

```text
validation_pass_rate
```

不是同一个指标。

---

# 八、增加真实模型专属指标

在 Report 中增加：

### 1. Generation Success Rate

```text
LLM successfully generated SQL / Total
```

### 2. Validator Acceptance Rate

```text
SQL accepted by Validator / Generated SQL
```

### 3. Expectation Pass Rate

沿用 3.9.4。

### 4. Security Pass Rate

沿用 3.9.4。

### 5. Project Isolation Pass Rate

沿用 3.9.4。

不要创建 overall score。

不要：

```text
score = 0.83
```

也不要：

```text
LLM quality = A
```

只报告客观指标。

---

# 九、Execution Pass Rate

继续沿用 3.9.4 的定义。

当前 Dataset 没有：

```text
must_execute
```

因此：

```text
execution_pass_rate = null
```

Report：

```text
N/A
```

不要为了产生 execution rate 而修改 Dataset。

---

# 十、Snapshot

新增：

```text
tests/fixtures/text_to_sql/baselines/phase_3_9_5_real_llm_baseline.json
```

Snapshot 至少包含：

```json
{
  "phase": "3.9.5",
  "baseline_type": "real_llm",
  "dataset": {
    "path": "...",
    "version": "1.0",
    "total_cases": 14
  },
  "environment": {
    "python": "...",
    "llm_provider": "deepseek",
    "llm_model": "deepseek-chat",
    "postgresql_version": "..."
  },
  "metrics": {},
  "cases": []
}
```

### 严格禁止

Snapshot 不得包含：

```text
api_key
token
authorization
password
DATABASE_URL
完整环境变量
```

建议增加 Secret Safety Check。

---

# 十一、Report

新增：

```text
docs/evaluation/text-to-sql-real-llm-baseline-3.9.5.md
```

报告必须明确说明：

## 1. Dataset

```text
Version: 1.0
Cases: 14
```

## 2. Model

```text
Provider: DeepSeek
Model: deepseek-chat
```

## 3. Metrics

至少：

| Metric                           | Result |
| -------------------------------- | -----: |
| Total Cases                      |     14 |
| Passed                           |      ? |
| Failed                           |      ? |
| Expectation Pass Rate            |      ? |
| Validation Expectation Pass Rate |      ? |
| LLM Generation Success Rate      |      ? |
| Validator Acceptance Rate        |      ? |
| Execution Pass Rate              |    N/A |
| Security Pass Rate               |      ? |
| Project Isolation Pass Rate      |      ? |

不要提前填写任何分数。

必须使用真实运行结果。

---

# 十二、Failed Cases

如果出现失败，Report 必须逐个记录：

```text
case_id
expected behavior
actual outcome
error_code
```

例如：

```text
case_id: aggregation_01
expected: aggregation query
actual: generated SQL did not satisfy expectation
error_code: expectation_mismatch
```

不要进行主观评价，例如：

```text
DeepSeek 很差
模型不聪明
Prompt 不行
```

只能描述事实。

---

# 十三、最重要的对比

Report 中增加：

## Phase 3.9.4 vs Phase 3.9.5

对比：

```text
Deterministic Fake Generator
vs
Real DeepSeek Generator
```

但不要制作：

```text
模型排名
模型评分
模型等级
```

只展示指标变化。

例如：

| Metric                | 3.9.4 Fake | 3.9.5 Real |
| --------------------- | ---------: | ---------: |
| Total                 |         14 |         14 |
| Expectation Pass Rate |       100% |          ? |
| Security Pass Rate    |       100% |          ? |
| Project Isolation     |       100% |          ? |
| Generation Success    |        N/A |          ? |

---

# 十四、不要因为结果不好而修复

这是本阶段最重要的实验纪律。

如果真实 DeepSeek 只有：

```text
10 / 14
```

必须如实记录。

如果：

```text
5 / 14
```

也必须如实记录。

如果：

```text
14 / 14
```

同样如实记录。

**不要为了提高结果修改 Prompt / Dataset / Checker / Validator。**

本阶段的价值就是获得真实 Baseline。

---

# 十五、测试

新增：

```text
tests/test_text_to_sql_real_llm_baseline.py
```

默认 pytest **不要调用真实 DeepSeek API**，避免：

* 测试不稳定
* 消耗 API
* 网络依赖
* CI 不可复现

测试至少覆盖：

1. Real LLM Snapshot schema
2. Secret safety
3. Metrics calculation
4. Dataset version = 1.0
5. Case count = 14
6. Snapshot consistency
7. Real LLM baseline report consistency

如果需要测试真实 API，仅提供显式 opt-in，例如：

```text
RUN_REAL_LLM_EVAL=1
```

默认：

```text
RUN_REAL_LLM_EVAL != 1
```

则跳过。

---

# 十六、真实 Evaluation Script

建议新增：

```text
scripts/generate_text_to_sql_real_llm_baseline.py
```

支持：

```text
python scripts/generate_text_to_sql_real_llm_baseline.py
```

执行真实 DeepSeek Evaluation。

另外支持：

```text
python scripts/generate_text_to_sql_real_llm_baseline.py --check
```

`--check`：

* 不调用 LLM
* 检查 Snapshot / Report 是否一致
* 不修改 Snapshot

---

# 十七、失败容错

单个 Case 失败不能导致整个 Baseline 中断。

例如：

```text
Case 1 PASS
Case 2 PASS
Case 3 ERROR
Case 4 PASS
...
Case 14 PASS
```

最终仍然生成：

```text
Snapshot
Report
```

并记录 Case 3：

```text
passed=false
error_code=...
```

如果整个 DeepSeek 服务不可用，则整个 Evaluation 可以失败，但必须：

* 明确错误
* 不生成伪造 Baseline
* 不覆盖已有 Snapshot

---

# 十八、网络/API异常

需要区分：

```text
LLM generation failure
```

和：

```text
Infrastructure failure
```

例如：

* API timeout
* DNS failure
* authentication failure
* rate limit
* provider unavailable

不要把所有情况都简单归为 SQL generation failure。

可以记录：

```text
error_code
error_category
```

但保持最小实现，不要建立复杂错误体系。

---

# 十九、执行前检查

运行真实 Evaluation 前先确认：

```text
DeepSeek configuration exists
API key exists
Dataset exists
Dataset version = 1.0
14 cases
PostgreSQL available
Project Context available
```

**不要输出 API Key。**

---

# 二十、最终验证

完成后运行：

```text
python -m compileall backend scripts
```

然后：

```text
pytest -q
```

然后：

```text
RUN_DB_TESTS=1
```

按照当前 Windows 项目已有方式执行 DB 测试。

不要因为 Windows PowerShell 环境变量语法问题误判测试失败。

如果项目当前使用：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

继续使用现有方式。

最后运行：

```text
python scripts/generate_text_to_sql_real_llm_baseline.py --check
```

---

# 二十一、DB Residue

完成后检查：

```text
knowledge_document
knowledge_chunk
project_a
project_b
```

确保：

```text
knowledge_document / knowledge_chunk = (0, 0)
project_a / project_b residue = 0
```

Real LLM Evaluation 不允许产生持久化业务数据。

---

# 二十二、最终汇报格式

完成后向我汇报：

```text
Phase 3.9.5 完成汇报

1. Dataset
2. Real LLM
3. Metrics
4. Snapshot
5. Report
6. 新增测试
7. 全量测试
8. DB 测试
9. compile / lint
10. DB residue
11. 修改文件
12. 新增文件
13. API / Generator contract
14. 真实 LLM 失败 Case
15. Phase 3.9.4 Fake vs 3.9.5 Real 对比
16. 结论
```

其中：

```text
Real LLM Baseline
```

必须明确标记为：

> 首次真实 DeepSeek Baseline，不代表最终模型效果，也不代表生产准确率。

---

# 二十三、STOP 条件

完成以下全部内容后：

```text
Real DeepSeek 14-case Evaluation
Snapshot
Report
Tests
Full Test
DB Test
Compile
Lint
DB Residue Check
```

立即停止。

**不要进入 Phase 3.9.6。**

**不要自行优化 Prompt。**

**不要自行修改 Dataset。**

**不要自行增加新的 Text-to-SQL 功能。**
