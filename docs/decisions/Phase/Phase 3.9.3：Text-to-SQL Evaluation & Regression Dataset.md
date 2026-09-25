# Phase 3.9.3 — Text-to-SQL Evaluation & Regression Dataset

## 目标

建立一个可长期维护的 Text-to-SQL Evaluation / Regression Dataset。

目的：

> 以后修改 Prompt、Semantic、RelevantTableSelector、Text-to-SQL Model 或 Context 时，可以用固定问题集自动验证是否发生回归。

本阶段只建立：

```text
Evaluation Dataset
        ↓
Evaluation Runner
        ↓
Text-to-SQL Pipeline
        ↓
Validation / Execution
        ↓
Structured Evaluation Result
```

不要在本阶段优化 Text-to-SQL 本身。

---

# 一、严格范围

本阶段允许：

* 新增 Text-to-SQL evaluation dataset
* 新增 dataset model / loader
* 新增 evaluation runner
* 新增 evaluation result DTO
* 新增自动化测试
* 使用 Fake Generator 做确定性单元测试
* 在 DB 测试中验证真实 Schema / Validator / Executor 集成
* 增加少量 README / ADR 文档

本阶段禁止：

* 不修改 Text-to-SQL Generator contract
* 不修改 SQL Validator
* 不修改 SQL Executor
* 不修改 Router
* 不修改 RAG
* 不修改 Tool Framework
* 不修改 Semantic YAML
* 不修改 Project Configuration
* 不调整 Prompt
* 不更换 LLM
* 不增加 Reranker
* 不引入新的数据库表
* 不引入 tokenizer
* 不做模型评分/排行榜
* 不自动修改 SQL
* 不自动修复 SQL
* 不让评测框架绕过 Validator
* 不让 LLM 获得额外数据库权限

---

# 二、Step 1：先阅读现有实现

先不要修改代码。

阅读：

```text
backend/app/services/text_to_sql_service.py
backend/app/services/text_to_sql_context.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/sql_validator_service.py
backend/app/services/sql_executor_service.py
backend/app/services/relevant_table_selector.py
backend/app/services/database_context_composer.py
backend/app/services/semantic_schema_filter.py
backend/app/projects/semantic.py
backend/app/projects/configuration.py
```

以及：

```text
tests/
```

重点寻找现有：

* Text-to-SQL Fake Generator
* Orchestrator tests
* Validator tests
* Executor tests
* Project A/B tests
* DB fixture
* pytest marker / DB-gated 测试机制

先理解现有测试基础设施，再设计最小实现。

---

# 三、Evaluation Dataset 设计

新增一个专门的数据集文件。

推荐：

```text
tests/evaluation/datasets/text_to_sql_regression.yaml
```

如果项目现有测试数据目录有更合适的结构，可以按现有项目习惯调整。

不要把大量测试案例直接硬编码到 Python。

---

# 四、Dataset Case Schema

每条 case 至少包含：

```yaml
- id: inventory_top_10
  question: "查询库存数量最多的10个物料"
  project_id: "vietnam-wms"
  expected:
    must_pass_validation: true
    allowed_tables:
      - inventory
    must_contain_columns:
      - inventory.qty
    must_not_contain:
      - fake_column
```

字段可以根据现有架构调整，但必须保持简单。

建议支持：

```text
id
question
project_id
expected
```

其中：

```text
expected.must_pass_validation
expected.allowed_tables
expected.must_contain_columns
expected.must_not_contain
expected.must_execute
```

全部为可选。

---

# 五、第一版 Dataset

建立至少 12 个具有代表性的 Text-to-SQL cases。

不要追求数量。

重点覆盖以下场景：

## Case 1：简单查询

```text
查询库存信息
```

## Case 2：Top N

```text
查询库存数量最多的10个物料
```

## Case 3：条件过滤

```text
查询某个仓库的库存
```

## Case 4：排序

```text
查询库存并按照库存数量从高到低排序
```

## Case 5：聚合

```text
统计各仓库的库存数量
```

## Case 6：GROUP BY

```text
统计每个物料的库存总量
```

## Case 7：HAVING

```text
查询库存总量超过某个数量的物料
```

如果当前 Schema 不支持某个场景，不要虚构字段。

---

## Case 8：JOIN

使用当前真实 Schema 中存在的 FK / 业务关系设计。

---

## Case 9：日期条件

只有当前 Schema 确实存在日期字段时才加入。

---

## Case 10：LIMIT

明确要求：

```text
查询前10条……
```

---

## Case 11：Semantic-dependent

问题必须依赖当前 Business Semantic 才容易正确理解。

例如：

```text
查询各仓库的库存情况
```

具体使用哪个业务字段由当前 Semantic + Schema 决定。

---

## Case 12：安全边界

加入一个恶意/危险问题，例如：

```text
删除库存表中的所有数据
```

Expected：

```yaml
must_pass_validation: false
```

并且验证最终 SQL 不能绕过 Validator。

注意：

这个 case 不是为了测试 LLM 是否听话，而是验证：

```text
Generated SQL
    ↓
SQL Validator
    ↓
Rejected
```

---

# 六、不要把 Expected SQL 当唯一答案

非常重要。

本阶段不要简单设计成：

```text
question → exact expected SQL
```

原因：

同一个问题可能存在多个合法 SQL 表达方式。

例如：

```sql
SELECT ...
FROM inventory
ORDER BY qty DESC
LIMIT 10
```

和某些等价 SQL 都可能正确。

因此第一版主要采用：

```text
Structural Expectations
```

例如：

```text
must_pass_validation
must_contain_tables
must_contain_columns
must_not_contain
must_execute
```

而不是 exact SQL string equality。

---

# 七、Evaluation Result DTO

新增一个简单的 Result DTO，例如：

```text
TextToSQLEvaluationResult
```

至少包含：

```text
case_id
project_id
question
generated_sql
validation_passed
execution_passed
matched_expectations
failed_expectations
error_code
```

可以增加：

```text
duration_ms
```

但不要增加复杂性能分析。

---

# 八、Evaluation Runner

新增一个纯应用层 Runner，例如：

```text
backend/app/services/text_to_sql_evaluation_service.py
```

或者：

```text
backend/app/evaluation/
```

根据当前项目结构选择最自然的位置。

Runner 负责：

```text
Dataset Case
    ↓
AI / Text-to-SQL Pipeline
    ↓
Result
    ↓
Expectation Checker
    ↓
TextToSQLEvaluationResult
```

必须做到：

### 1. 不修改 Pipeline

Evaluation Runner 只是调用现有服务。

### 2. 不绕过 Validator

即使测试想判断 SQL 是否正确，也必须经过：

```text
TextToSQLGenerator
→ SQLValidator
```

### 3. DB Execution 可选

如果：

```text
must_execute: true
```

才执行 SQL。

否则不需要数据库。

---

# 九、Expectation Checker

建议独立为纯函数/纯服务。

例如：

```text
check_expectations(
    case,
    generated_sql,
    validation_result,
    execution_result
)
```

检查：

### must_pass_validation

```text
expected true
actual false
→ fail
```

### must_contain_tables

SQL AST 中必须包含指定 table。

不要使用简单字符串搜索。

尽可能复用现有 `sqlglot` AST 能力。

### must_contain_columns

同样使用 AST。

例如：

```text
inventory.qty
```

必须能够识别。

### must_not_contain

如果 SQL 出现：

```text
fake_column
```

则 fail。

### must_execute

如果要求执行：

```text
execution_passed == true
```

否则 fail。

---

# 十、Project Isolation

至少加入：

```text
project-a
project-b
```

测试。

如果现有 DB fixture 已经存在：

```text
project_a
project_b
```

直接复用。

例如：

```text
project-a → schema A
project-b → schema B
```

同一个 question：

```text
查询库存
```

分别运行。

确认：

```text
A → 只使用 A 的 schema
B → 只使用 B 的 schema
```

不得出现：

```text
A SQL 使用 B table
B SQL 使用 A table
```

---

# 十一、Fake Generator 测试

大多数 Evaluation Runner 单元测试不要调用真实 DeepSeek。

建立确定性 Fake Generator：

例如：

```text
case inventory_top_10
→ SELECT ...
```

测试：

```text
正确 SQL → PASS
错误 table → FAIL
错误 column → FAIL
非法 SQL → FAIL
```

这样：

```text
pytest
```

不依赖网络、不消耗 API。

不要修改现有 Fake Generator contract。

可以新增专门 Evaluation Fake。

---

# 十二、真实 DB Evaluation

只增加少量 DB-gated 测试。

例如：

```text
RUN_DB_TESTS=1
```

测试：

1. Dataset 可以加载
2. SQL Validator 正常工作
3. 合法 SQL 可以执行
4. 非法 SQL 被拒绝
5. project-a / project-b 隔离

不要要求真实 DeepSeek 才能通过基础测试。

如果需要真实 LLM evaluation：

> 本阶段只保留接口/扩展点，不要把真实 API 调用纳入默认 pytest。

---

# 十三、Dataset Loader

增加简单 Loader：

```text
load_text_to_sql_regression_dataset()
```

要求：

* YAML 格式错误 → 明确异常
* case 缺少 id → 明确异常
* question 为空 → 明确异常
* case id 重复 → 明确异常
* expected 类型错误 → 明确异常

Dataset Loader 应该是纯读取逻辑。

---

# 十四、Determinism

同一个 Fake Generator + 同一个 Dataset：

```text
run(dataset)
```

连续执行两次。

结果应该一致：

```text
case_id
validation
expectation
pass/fail
```

不得依赖：

* set 无序遍历
* 当前时间
* 随机数
* LLM
* 网络

如果结果包含 `duration_ms`，不要将 duration 纳入 equality。

---

# 十五、输出报告

增加一个简单的 summary：

```text
Text-to-SQL Regression Evaluation

Total: 12
Passed: 10
Failed: 2
Validation Passed: 11
Execution Passed: 8
```

可以提供：

```text
EvaluationSummary
```

但不要做复杂 HTML/前端页面。

第一阶段只需要 Python object / pytest assertion / 文本输出。

---

# 十六、测试要求

至少新增：

### Dataset

* 正常加载
* 缺 id
* 空 question
* duplicate id
* invalid expected

### Expectation Checker

* validation pass
* validation fail
* table match
* table mismatch
* column match
* column mismatch
* forbidden table/column
* execution required

### Runner

* all pass
* partial failure
* validation rejection
* execution failure
* deterministic

### Project

* project-a
* project-b
* cross-project contamination detection

### E2E

至少一个：

```text
Dataset
→ Fake Generator
→ Text-to-SQL Service
→ Validator
→ Evaluation Result
```

---

# 十七、测试数量

目标不是测试数量越多越好。

建议新增：

```text
20~30 个测试
```

如果现有测试基础设施适合更多测试，可以适当增加。

不要为了数量重复测试。

---

# 十八、不要新增生产数据库表

本阶段：

```text
NO MIGRATION
NO NEW TABLE
NO DB WRITE
```

DB 测试如果需要 fixture：

```text
创建临时 schema/table
→ 测试
→ cleanup
```

必须保证最终：

```text
knowledge_document / knowledge_chunk = (0, 0)
project_a / project_b residue = 0
```

---

# 十九、最终测试

非 DB：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

编译：

```powershell
python -m compileall backend
```

如果项目已有 lint 命令，继续使用现有 lint。

---

# 二十、完成后报告格式

完成后只汇报：

```text
Phase 3.9.3 完成汇报

1. Dataset
- 文件：
- Case 数量：
- 覆盖场景：

2. 新增测试
- 数量：

3. 全量测试
- passed：
- skipped：
- failed：

4. DB 测试
- passed：
- skipped：
- failed：

5. compile / lint
- 结果：

6. DB residue
- 结果：

7. 修改文件
- ...

8. 新增文件
- ...

9. API / Generator contract
- 是否变化：

10. 核心设计取舍
- ...

Phase 3.9.3 到此停止。
不进入 Phase 3.9.4。
```

---

# 最重要的规则

严格执行：

```text
阅读
→ 最小设计
→ Dataset
→ Runner
→ Expectation Checker
→ Tests
→ Regression
→ 汇报
→ STOP
```

**完成 Phase 3.9.3 后立即停止。**

不要自动进入 Phase 3.9.4。
