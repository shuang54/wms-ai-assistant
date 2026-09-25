# Phase 3.9.4 — Text-to-SQL Baseline Evaluation

## 一、阶段目标

基于 Phase 3.9.3 已经完成的：

```text
text_to_sql_regression.yaml
TextToSQLEvaluationRunner
Expectation Checker
EvaluationResult
EvaluationSummary
```

建立第一份正式的：

**Text-to-SQL Baseline Evaluation**

目标：

> 把当前 Text-to-SQL 系统在固定 14 个 Regression Cases 上的表现量化，并保存为可比较的 Baseline。

以后修改：

```text
Prompt
Semantic
Semantic-Schema Filter
RelevantTableSelector
DatabaseContextComposer
Text-to-SQL Model
```

都可以重新运行相同 Dataset，与 Baseline 比较。

---

# 二、严格范围

本阶段只允许：

* 读取 Phase 3.9.3 Dataset
* 使用现有 Evaluation Runner
* 增加 Baseline Summary
* 增加结构化 Evaluation Report
* 增加少量统计函数
* 增加 Baseline 测试
* 固化当前 Baseline 结果
* 增加 ADR / evaluation 文档

本阶段禁止：

* 不修改 Text-to-SQL Prompt
* 不修改 TextToSQLGenerator
* 不修改 SQL Validator
* 不修改 SQL Executor
* 不修改 RelevantTableSelector
* 不修改 SemanticSchemaFilter
* 不修改 DatabaseContextComposer
* 不修改 Router
* 不修改 Orchestrator
* 不修改 Semantic YAML
* 不增加新的 LLM 调用
* 不更换模型
* 不优化 SQL
* 不自动修复 SQL
* 不调整 Dataset Case 以“提高分数”
* 不增加新的 WMS 表
* 不新增数据库表
* 不引入 tokenizer
* 不做 Benchmark Web UI
* 不做模型排行榜

**本阶段是测量，不是优化。**

---

# 三、Step 1：先阅读 Phase 3.9.3

开始编码前先阅读：

```text
backend/app/services/text_to_sql_evaluation_service.py

tests/fixtures/text_to_sql/text_to_sql_regression.yaml

tests/test_text_to_sql_evaluation_service.py

docs/decisions/Phase/Phase 3.9.3：Text-to-SQL Evaluation & Regression Dataset 实施 ADR.md
```

同时确认：

```text
TextToSQLEvaluationResult
EvaluationSummary
TextToSQLEvaluationRunner
check_expectations()
load_text_to_sql_regression_dataset()
```

当前真实 Dataset 是：

```text
14 Cases
```

不要修改这 14 个 Case。

---

# 四、Baseline 的核心原则

Baseline 必须回答：

> 当前版本的 Text-to-SQL 到底表现如何？

因此：

**不要重新定义一套新的评分体系。**

优先复用 Phase 3.9.3 已经存在的：

```text
validation_passed
execution_passed
matched_expectations
failed_expectations
```

---

# 五、Baseline Metrics

新增一个简单的 Baseline Metrics DTO，例如：

```text
TextToSQLBaselineMetrics
```

至少包含：

```text
total_cases
passed_cases
failed_cases

validation_passed_cases
execution_passed_cases

expectation_passed_cases

validation_pass_rate
execution_pass_rate
expectation_pass_rate
overall_pass_rate
```

百分比统一：

```text
0.0 ~ 1.0
```

不要直接存：

```text
85%
```

例如：

```text
0.8571
```

---

# 六、Metrics 定义必须明确

不要出现“Pass”的定义不清楚。

固定定义：

## 1. Total

```text
total_cases = Dataset Case 数量
```

当前应该是：

```text
14
```

---

## 2. Validation Pass Rate

```text
validation_passed_cases / total_cases
```

但必须注意安全 Case：

例如：

```text
DELETE
```

Expected：

```text
must_pass_validation = false
```

如果 Validator 正确拒绝：

```text
validation_passed = false
```

不能简单把它算成失败。

因此 Baseline 需要区分：

```text
actual validation result
```

和：

```text
validation expectation matched
```

建议最终指标：

```text
validation_expectation_pass_rate
```

而不是仅仅：

```text
validation_pass_rate
```

---

# 七、Expectation Pass Rate

这是本阶段最重要的指标之一。

定义：

```text
case_passed
```

当且仅当：

```text
所有 configured expectations 都满足
```

例如：

```text
must_pass_validation
must_contain_tables
must_contain_columns
must_not_contain
must_execute
```

全部满足：

```text
PASS
```

否则：

```text
FAIL
```

直接复用 Phase 3.9.3 Checker 的结果。

不要重新实现另一套 Checker。

---

# 八、Execution Pass Rate

只统计真正要求执行的 Case。

例如：

```text
must_execute = true
```

才进入 execution denominator。

如果当前 Dataset：

```text
must_execute = 0
```

则：

```text
execution_pass_rate = None
```

而不是：

```text
0%
```

这样可以避免：

```text
没有执行测试
```

被错误解释成：

```text
执行成功率 0%
```

---

# 九、Security Metrics

增加一个简单的安全指标：

```text
security_expectation_pass_rate
```

识别 Dataset 中：

```text
must_pass_validation = false
```

的 Case。

当前至少应该包含：

```text
DELETE
```

以及 Dataset 中已有的安全边界 Case。

计算：

```text
security_cases_passed
/
security_cases_total
```

要求：

**安全 Case 的 Validator 拒绝必须被视为 PASS。**

---

# 十、Project Isolation Metrics

不要重新实现 Project Isolation。

直接利用现有 Dataset / Evaluation Result。

统计：

```text
project-a
project-b
```

对应 Case 是否满足 expectations。

输出：

```text
project_isolation_cases
project_isolation_passed
project_isolation_pass_rate
```

如果当前 Dataset 中 Project A/B Case 是独立测试，则保持现有定义。

---

# 十一、Baseline Report

新增一个纯文本/Markdown Report。

推荐：

```text
docs/evaluation/text-to-sql-baseline-3.9.4.md
```

内容类似：

```markdown
# Text-to-SQL Baseline — Phase 3.9.4

Dataset:
- File: tests/fixtures/text_to_sql/text_to_sql_regression.yaml
- Version: ...
- Cases: 14

Results:

| Metric | Result |
|---|---:|
| Total Cases | 14 |
| Expectation Pass Rate | ... |
| Validation Expectation Pass Rate | ... |
| Execution Pass Rate | N/A |
| Security Pass Rate | ... |
| Project Isolation Pass Rate | ... |

Environment:
- Python: ...
- PostgreSQL: ...
- Model: ...
- Dataset Version: ...

Generated At:
...

Notes:
...
```

---

# 十二、不要把环境信息写死

如果可以可靠获取：

```text
Python version
```

可以记录。

如果当前配置可以安全读取：

```text
LLM provider
LLM model
```

可以记录。

但是：

**绝对不能记录：**

```text
API Key
DATABASE_URL
password
authorization
connection string
secret
```

如果无法安全获取模型名称：

```text
model = unavailable
```

也可以。

不要为了记录环境修改生产配置。

---

# 十三、Dataset Version

利用 Dataset 中已经存在的：

```text
_schema_version
```

将它记录到 Baseline。

例如：

```text
Dataset Version: 1
```

如果当前值不是 1，就使用真实值。

不要修改 Dataset 版本。

Baseline 必须绑定：

```text
Dataset Version
```

否则以后 Dataset 增加 Case 后无法知道旧 Baseline 对应什么数据。

---

# 十四、Baseline Snapshot

增加一个机器可读的 Snapshot。

推荐：

```text
tests/fixtures/text_to_sql/baselines/phase_3_9_4_baseline.json
```

内容至少包含：

```json
{
  "phase": "3.9.4",
  "dataset_version": "...",
  "total_cases": 14,
  "passed_cases": 0,
  "failed_cases": 0,
  "metrics": {
    "expectation_pass_rate": 0.0,
    "validation_expectation_pass_rate": 0.0,
    "execution_pass_rate": null,
    "security_expectation_pass_rate": 0.0,
    "project_isolation_pass_rate": 0.0
  }
}
```

上面的数值只是结构示例。

**必须使用实际运行结果，不允许手工猜测。**

---

# 十五、Baseline 不等于“所有 Case 必须通过”

非常重要。

当前系统如果某些 Case 失败：

不要：

```text
修改 Prompt
修改 Dataset
修改 Semantic
修改 Checker
```

让它变成 PASS。

应该记录真实结果：

```text
14 cases
12 passed
2 failed
```

然后把它作为：

```text
Baseline
```

这正是 Baseline 的意义。

---

# 十六、Case-Level Snapshot

除了总体 Metrics，JSON 最好记录每个 Case：

```text
case_id
project_id
validation_passed
execution_passed
expectations_passed
failed_expectations
error_code
```

不要必须保存完整 generated SQL。

原因：

* SQL 可能随着模型变化
* Snapshot 应主要描述结果
* 避免敏感数据
* 减少文件噪音

但是可以在 Markdown Report 中记录当前失败 Case 的：

```text
case_id
failure reason
```

如果 SQL 本身是测试 SQL 且安全，可以记录；不是必须。

---

# 十七、Deterministic Baseline

Baseline 统计必须是确定性的。

相同：

```text
Dataset
Fake Generator
Validator
Executor
```

连续运行：

```text
run #1
run #2
```

Metrics 必须一致。

不要把：

```text
duration_ms
```

纳入 Baseline equality。

---

# 十八、DB Baseline

默认 Baseline 不需要真实 LLM。

优先使用：

```text
Phase 3.9.3 Fake Generator
```

如果 Dataset 中已有 DB-gated execution Case：

按照现有：

```text
RUN_DB_TESTS=1
```

执行。

但：

**不要为了 Baseline 增加新的 DB 写入。**

继续保持：

```text
NO MIGRATION
NO NEW TABLE
NO PERSISTENT WRITE
```

---

# 十九、真实 LLM Baseline

本阶段：

**不要默认运行 DeepSeek。**

不要新增：

```text
real_llm pytest
```

如果现有项目已经有独立 real LLM smoke：

可以记录：

```text
Real LLM Baseline: not executed
```

即可。

本阶段正式 Baseline：

```text
Deterministic Evaluation Baseline
```

---

# 二十、Baseline Regression Test

新增：

```text
tests/test_text_to_sql_baseline.py
```

至少测试：

### Test 1

Dataset Version 正确读取。

### Test 2

Total Case 数量：

```text
14
```

如果未来 Dataset 改变，不要静默修改测试。

---

### Test 3

Metrics 计算正确。

使用固定 Fake Results：

```text
PASS
FAIL
SECURITY PASS
```

验证统计。

---

### Test 4

`must_execute = false` 时：

```text
execution_pass_rate is None
```

如果 Dataset 当前没有执行 Case。

---

### Test 5

Security rejection 正确计为 PASS。

---

### Test 6

Project A/B Metrics 正确。

---

### Test 7

Baseline Snapshot Schema 正确。

---

# 二十一、不要锁死当前具体分数

不要写：

```python
assert expectation_pass_rate == 0.8571
```

作为生产逻辑。

原因：

Baseline 的目的是记录当前结果，不应该让未来任何合法变化都导致整个测试体系无法更新。

可以测试：

```text
snapshot schema
metric calculation
dataset version
case count
```

Baseline 文件本身作为历史记录。

如果未来系统发生有意变化：

```text
生成新的 baseline
```

而不是修改统计代码来迁就旧分数。

---

# 二十二、Baseline Report 中记录失败原因

如果存在失败 Case，报告：

```text
Failed Cases

- case_id:
  reason:
- case_id:
  reason:
```

不要给出主观评价：

不要写：

```text
模型太差
Prompt 很差
Selector 很垃圾
```

只记录事实：

```text
Expected table: inventory
Actual table: knowledge_document
```

或者：

```text
Expected validation rejection
Actual validation passed
```

---

# 二十三、统计函数保持纯函数

建议：

```text
calculate_baseline_metrics(results)
```

满足：

```text
input
→ pure calculation
→ metrics
```

不要在 Metrics 计算过程中：

* 查询 DB
* 调 LLM
* 调 API
* 修改文件

---

# 二十四、Baseline Runner

如果需要增加：

```text
run_baseline()
```

它应该只是：

```text
load dataset
↓
run existing EvaluationRunner
↓
calculate metrics
↓
produce snapshot
↓
produce report
```

不要复制 Phase 3.9.3 Runner。

---

# 二十五、输出格式

CLI 可以增加类似：

```text
Text-to-SQL Baseline — Phase 3.9.4

Dataset: text_to_sql_regression.yaml
Version: 1
Cases: 14

Expectation Pass Rate:  XX.XX%
Validation Expectation Pass Rate: XX.XX%
Execution Pass Rate: N/A
Security Pass Rate: 100.00%
Project Isolation Pass Rate: 100.00%

Passed: X
Failed: X
```

实际数字必须来自真实运行。

---

# 二十六、测试执行

先：

```powershell
python -m pytest -q tests/test_text_to_sql_baseline.py
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

如果项目现有 DB 测试命令不同，遵循项目实际机制。

编译：

```powershell
python -m compileall backend
```

继续运行现有 lint。

---

# 二十七、最终检查

必须确认：

```text
[ ] Dataset 14 Cases 未修改
[ ] Dataset version 未修改
[ ] Evaluation Runner 核心行为未改变
[ ] Generator contract 未改变
[ ] Validator 未改变
[ ] Executor 未改变
[ ] Prompt 未改变
[ ] Semantic 未改变
[ ] Selector 未改变
[ ] Composer 未改变
[ ] 无新增 LLM 调用
[ ] 无新增 Embedding 调用
[ ] 无新增 DB 持久化写入
[ ] Baseline Metrics 来自真实运行
[ ] Snapshot 与 Report 数值一致
[ ] Security rejection 正确统计为 PASS
[ ] execution N/A 正确处理
[ ] Project A/B 隔离结果正确
[ ] 全量测试通过
```

---

# 二十八、完成汇报格式

完成后严格按照：

```text
Phase 3.9.4 完成汇报

1. Baseline Dataset
- 文件：
- Version：
- Case 数量：

2. Baseline Metrics
- Total：
- Passed：
- Failed：
- Expectation Pass Rate：
- Validation Expectation Pass Rate：
- Execution Pass Rate：
- Security Pass Rate：
- Project Isolation Pass Rate：

3. Snapshot
- 文件：

4. Report
- 文件：

5. 新增测试
- 数量：

6. 全量测试
- passed：
- skipped：
- failed：

7. DB 测试
- passed：
- skipped：
- failed：

8. compile / lint
- 结果：

9. DB residue
- 结果：

10. 修改文件
- ...

11. 新增文件
- ...

12. API / Generator contract
- 是否变化：

13. 核心设计取舍
- ...

14. 当前 Baseline 中的失败 Case
- ...

Phase 3.9.4 到此停止。
不进入 Phase 3.9.5。
```

---

# 最重要的执行规则

严格执行：

```text
阅读 3.9.3
→ 计算 Metrics
→ 运行 14 Cases
→ 生成 Baseline Snapshot
→ 生成 Markdown Report
→ 测试
→ 回归
→ 汇报
→ STOP
```

**不要为了提高 Baseline 数字而修改 Prompt、Dataset 或生产逻辑。**

**完成 Phase 3.9.4 后立即停止，不进入 Phase 3.9.5。**
