# Phase 3.9.1：Text-to-SQL Context Enhancement

## 一、阶段目标

在现有 Phase 3.7 Text-to-SQL 链路以及 Phase 3.8 Project Configuration 完成的基础上，本阶段只解决：

> **让 Text-to-SQL Generator 获得更加明确、结构化、可约束的业务查询上下文。**

当前链路：

```text
Question
  ↓
AI Router
  ↓
RelevantTableSelector
  ↓
DatabaseContextComposer
  ↓
TextToSQLService
  ↓
SQLValidator
  ↓
SQLExecutor
```

本阶段目标：

```text
Project Configuration
       ↓
Question
       ↓
Relevant Tables
       ↓
Database Schema
       +
Business Semantic
       +
SQL Generation Constraints
       ↓
TextToSQLService
       ↓
SQLValidator
       ↓
SQLExecutor
```

注意：

**本阶段不修改 SQL Validator 和 SQL Executor。**

---

# 二、开始前先侦察

先不要修改代码。

阅读：

```text
backend/app/services/text_to_sql_service.py
backend/app/services/relevant_table_selector.py
backend/app/services/schema_serializer_service.py
backend/app/services/database_context_composer.py
backend/app/projects/semantic.py
backend/app/projects/semantic_provider.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/project_orchestrator_factory.py
backend/app/prompts/text_to_sql_system.txt
backend/app/prompts/text_to_sql_user.txt
backend/app/prompts/text_to_sql_retry.txt
```

以及：

```text
tests/test_text_to_sql_service.py
tests/test_relevant_table_selector.py
tests/test_*
```

重点确认：

1. 当前 TextToSQLResult 的字段；
2. 当前 TextToSQLGenerator Protocol；
3. TextToSQLService.generate() 的完整参数；
4. database_context 当前具体格式；
5. DatabaseContextComposer 当前输出什么；
6. Semantic 是否已经包含业务名称、别名、关系；
7. RelevantTableSelector 当前返回哪些信息；
8. Orchestrator 如何把 context 传给 TextToSQL；
9. 当前 prompt 是否已经要求：

   * SELECT only；
   * LIMIT；
   * 不允许修改数据；
   * 只能使用 allowed tables；
10. retry 时 validator 错误如何回传给 LLM。

侦察结束后先输出：

```text
Text-to-SQL reconnaissance complete.

Current generator contract:
Current context format:
Current semantic information:
Current selected-table information:
Current prompt constraints:
Current retry behavior:
Current orchestrator integration:
Recommended minimal change:
Potential compatibility risks:
```

然后再开始实施。

---

# 三、核心原则

本阶段必须保持：

```text
SQLValidator = 最终安全边界
SQLExecutor  = 最终执行边界
TextToSQL    = 只负责生成候选 SQL
```

不要把安全职责转移到 Prompt。

即使 Prompt 写：

```text
只允许 SELECT
```

也不能取消：

```text
SQLValidator
```

---

# 四、新增 SQL Generation Context DTO

建议新增：

```text
backend/app/services/text_to_sql_context.py
```

定义一个 frozen DTO，例如：

```python
TextToSQLContext
```

字段根据现有代码最小设计。

推荐至少包含：

```text
database_context
allowed_tables
max_rows
project_id
```

如果现有架构中已经能稳定获得 semantic/project information，可以增加：

```text
business_context
```

但不要重复保存完整：

```text
ProjectConfiguration
ProjectContext
DatabaseSchema
ProjectSemantic
```

避免 TextToSQLService 与 Project 层强耦合。

---

# 五、最重要：不要让 TextToSQLService 直接依赖 Project

TextToSQLService 不应该：

```python
ProjectRegistry
ProjectConfigurationProvider
ProjectSemanticProvider
ProjectKnowledgeProvider
```

这些全部不要 import。

正确：

```text
Project Layer
      ↓
Orchestrator / Application
      ↓
TextToSQLContext
      ↓
TextToSQLService
```

这样以后这个 TextToSQLService 可以脱离 WMS 项目复用。

---

# 六、Context 必须明确区分三类信息

## 6.1 Database Schema

例如：

```text
TABLE inventory
  material_code TEXT
  qty NUMERIC
  warehouse_code TEXT
```

---

## 6.2 Business Semantic

例如：

```text
inventory:
  business_name: 库存
  description: 当前仓库库存余额

qty:
  business_name: 库存数量
  description: 当前可用库存数量
```

---

## 6.3 SQL Constraints

例如：

```text
SQL constraints:
- SELECT only
- Only use allowed tables
- LIMIT is mandatory
- LIMIT <= max_rows
- Do not modify database
- Do not access system/catalog tables
```

注意：

这些 constraints 是 Prompt 指令。

真正执行前仍然必须通过 SQLValidator。

---

# 七、Prompt 结构

把 Text-to-SQL System Prompt 调整成明确的结构。

推荐：

```text
ROLE

You generate read-only PostgreSQL SQL for an enterprise data analysis system.

DATABASE CONTEXT

{database_context}

BUSINESS SEMANTICS

{business_context}

ALLOWED TABLES

{allowed_tables}

QUERY CONSTRAINTS

1. Generate exactly one SQL statement.
2. The statement must be SELECT or a read-only CTE.
3. Only use allowed tables.
4. Do not modify data.
5. Do not use dangerous functions.
6. LIMIT is mandatory.
7. LIMIT must not exceed {max_rows}.
8. Never invent tables or columns.
9. Return SQL only.
```

但必须根据当前项目已有 Prompt 风格修改。

**不要机械重写现有 Prompt。**

---

# 八、明确“不要猜字段”

当前企业 Text-to-SQL 最容易出现：

```text
用户：
查询本月库存

LLM：
SELECT stock_qty
FROM inventory
```

但实际字段可能是：

```text
qty
```

所以 Prompt 必须明确：

> 如果 Schema 中没有明确字段，不允许自行创造字段。

同样：

```text
warehouse_name
```

如果只有：

```text
warehouse_code
```

不能自行猜测。

---

# 九、业务语义优先级

Prompt 中明确：

```text
Business Semantic
        >
raw database name
```

例如：

```text
库存数量
```

对应：

```text
inventory.qty
```

那么 LLM 应优先根据：

```text
qty.business_name = 库存数量
```

进行映射。

但：

> Semantic 不能覆盖真实 Schema。

如果 semantic 写了：

```text
inventory.stock_qty
```

但真实 Schema 没有：

```text
stock_qty
```

仍然不能生成：

```sql
SELECT stock_qty ...
```

---

# 十、Allowed Tables

Text-to-SQL 必须继续支持：

```python
allowed_tables
```

例如：

```text
inventory
warehouse
material
```

如果 RelevantTableSelector 只选择：

```text
inventory
material
```

那么 SQL 不允许：

```sql
JOIN supplier
```

即使数据库中真实存在 supplier。

最终仍由：

```text
SQLValidator
```

强制拦截。

---

# 十一、Project ID

如果新增：

```python
TextToSQLContext.project_id
```

仅用于：

* tracing
* metadata
* debug
* evaluation

不要让 TextToSQLService 根据：

```text
project_id
```

自己查询：

```text
ProjectRegistry
DataSource
SemanticProvider
KnowledgeProvider
```

项目上下文必须已经在上游解析完成。

---

# 十二、Result Contract

检查当前：

```python
TextToSQLResult
```

如果已经足够：

```text
sql
attempts
validation
```

不要为了“完整”重新设计。

如果确实缺少有用信息，可以最小增加：

```text
selected_tables
```

或者：

```text
generation_metadata
```

但必须证明现有功能需要。

不要为了未来可能使用而堆字段。

---

# 十三、Retry Context

当前已经支持：

```text
SQL
+
Validator errors
+
context
```

本阶段只优化结构。

Retry Prompt 应明确：

```text
Previous SQL:
...

Validation errors:
...

Allowed tables:
...

Schema:
...

Business semantics:
...
```

要求：

> 只修改导致验证失败的部分，不要无理由改变已经正确的 SQL 结构。

例如：

```text
TABLE_NOT_ALLOWED
```

不要换成另一个不存在的表。

应该重新根据：

```text
allowed_tables
```

生成。

---

# 十四、必须增加的测试

新增：

```text
tests/test_text_to_sql_context.py
```

至少覆盖：

### Case 1

Context frozen：

```text
cannot mutate
```

---

### Case 2

正确构造：

```text
project-a
schema
semantic
allowed_tables
max_rows
```

---

### Case 3

Prompt 包含：

```text
schema
business semantic
allowed tables
max_rows
```

---

### Case 4

Prompt 不包含：

```text
API key
DB password
DB URL
credentials
```

---

### Case 5

不存在字段不会被自动加入 Context。

---

### Case 6

allowed_tables 严格保留。

---

### Case 7

max_rows 正确传递。

---

### Case 8

Retry Prompt 包含：

```text
previous SQL
validation error
allowed tables
schema
semantic
```

---

# 十五、现有 Text-to-SQL 测试必须全部保持

尤其：

```text
56 tests
```

以及：

```text
SQL Validator
SQL Executor
AI Router
AI Orchestrator
Project Configuration
```

相关测试全部保持。

---

# 十六、项目隔离 E2E

增加一个最小测试：

```text
project-a
    schema = A
    semantic = A

project-b
    schema = B
    semantic = B
```

同一个问题：

```text
查询库存
```

验证 Fake TextToSQLGenerator 收到：

```text
project-a → schema A + semantic A + allowed A
project-b → schema B + semantic B + allowed B
```

不能出现：

```text
project-a → semantic B
project-b → schema A
```

---

# 十七、安全边界测试

必须保持：

```text
LLM generated SQL
       ↓
SQLValidator
       ↓
SQLExecutor
```

即使 Prompt 被恶意问题影响：

```text
删除库存
DROP TABLE ...
DELETE ...
```

仍然必须由 Validator 拦截。

不要因为 Prompt 增强而删除或弱化 Validator 测试。

---

# 十八、不要修改

本阶段禁止修改：

```text
SQL Validator
SQL Executor
ProjectRegistry
ProjectConfiguration
KnowledgeProvider
KnowledgeIngestion
RAG
Embedding
Reranker
Tool
```

除非侦察发现为了传递 context 必须做极小的 Orchestrator 改动。

如果需要修改：

```text
AIOrchestratorService
```

必须保持现有公开接口兼容。

---

# 十九、性能要求

不要新增：

* LLM 调用
* Embedding
* Reranker
* DB 查询

Context 构造必须是纯内存操作。

Text-to-SQL 仍然：

```text
一次正常生成
最多现有 max_attempts retry
```

不能增加额外模型调用。

---

# 二十、完成后运行

```powershell
python -m pytest -q
```

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

```powershell
python -m compileall backend
```

以及现有 lint。

必须报告：

```text
passed
skipped
failed
```

并与 Phase 3.8.6：

```text
1309 passed / 238 skipped
1509 passed / 38 skipped
```

进行比较。

---

# 二十一、最终汇报格式

完成后严格按照以下格式：

```text
Phase 3.9.1 完成汇报

1. 修改文件

2. 新增文件

3. Text-to-SQL Context 设计

4. Schema / Semantic / Constraint 三层上下文

5. Prompt 改造

6. Retry Context

7. Project A/B 隔离测试

8. SQLValidator 安全边界

9. pytest

10. DB pytest

11. 性能影响

12. 已知限制

13. 本阶段停止
```

最后：

```text
Phase 3.9.1 到此停止。
不进入 Phase 3.9.2。
等待下一条指令。
```

严格执行阶段边界，不要自行扩展功能。
