# Phase 3.9.2 — Semantic-Schema Alignment

## 目标

在现有 Phase 3.9.1 基础上，增加一个纯应用层的“业务语义 → 当前 Schema”对齐过滤能力。

目标只有一个：

> Text-to-SQL Prompt 中的 `business_context` 只能包含当前项目、当前 Schema 中真实存在，并且属于本次 Text-to-SQL 已选中表的业务语义。

减少 LLM 因业务语义配置过宽而产生不存在表/字段的风险，同时减少 Prompt 长度。

---

## 严格范围

本阶段只做：

1. Semantic 与 DatabaseSchema 对齐
2. 按 Text-to-SQL 当前 `allowed_tables` 过滤 Semantic
3. 过滤不存在的 table / column
4. 过滤无法成立的 relationship
5. 将过滤后的 Semantic 接入现有 Text-to-SQL Context
6. 补充单元测试和必要的 E2E 测试

不要做：

* 不修改 Semantic YAML
* 不修改 SQL Validator
* 不修改 SQL Executor
* 不修改 Router
* 不修改 Project Configuration
* 不修改 Knowledge/RAG
* 不增加新的 LLM 调用
* 不增加新的 Embedding/Reranker 调用
* 不引入 tokenizer
* 不重构 Text-to-SQL Generator
* 不改变现有 Generator 的函数签名
* 不改变现有 API
* 不把 Semantic 推断交给 LLM

---

# Step 1：先阅读现有实现

先不要修改代码。

重点阅读：

* `backend/app/projects/semantic.py`
* `backend/app/services/schema_serializer_service.py`
* `backend/app/services/database_context_composer.py`
* `backend/app/services/relevant_table_selector.py`
* `backend/app/services/text_to_sql_context.py`
* `backend/app/services/ai_orchestrator_service.py`
* Phase 3.9.1 新增/修改的 Text-to-SQL tests
* 现有 Semantic 相关 tests

同时确认：

1. Semantic 的 table/column/relationship 数据结构
2. DatabaseSchema / SchemaTable / SchemaColumn 数据结构
3. `allowed_tables` 当前是什么格式
4. `DatabaseContextComposer` 当前如何产生 selected schema
5. Phase 3.9.1 中 business_context 当前如何进入 TextToSQLContext

阅读完成后再开始实现。

---

# Step 2：实现 Semantic-Schema Filter

新增一个职责单一的纯服务，例如：

`backend/app/services/business_semantic_context_filter.py`

名称可以根据现有项目命名习惯调整，但不要为了命名进行大规模重构。

建议职责：

```text
ProjectSemantic
        +
DatabaseSchema
        +
allowed_tables
        ↓
SemanticSchemaFilter
        ↓
Filtered ProjectSemantic
```

要求：

### 2.1 Table 过滤

只保留：

* 当前 DatabaseSchema 中真实存在的 table
* 且 table 在 `allowed_tables` 中

例如 Semantic：

```yaml
tables:
  - name: warehouse
  - name: inventory
  - name: nonexistent_table
```

当前：

```text
allowed_tables = ["warehouse", "inventory"]
```

结果：

```text
warehouse
inventory
```

`nonexistent_table` 必须被过滤。

---

### 2.2 Column 过滤

对于保留下来的 table：

只保留 DatabaseSchema 中真实存在的 column。

例如 Semantic：

```yaml
warehouse:
  columns:
    - code
    - name
    - warehouse_name
    - fake_column
```

如果实际 Schema：

```text
warehouse
  code
  name
```

结果只能保留：

```text
code
name
```

不能因为 Semantic 中存在 `warehouse_name` 就认为数据库存在该字段。

---

### 2.3 未选中的合法 Table 也必须过滤

例如数据库真实存在：

```text
warehouse
inventory
sales_order
purchase_order
```

但当前 Text-to-SQL：

```text
allowed_tables = ["warehouse", "inventory"]
```

即使 Semantic 中存在：

```text
sales_order
purchase_order
```

也不能进入 business_context。

这样保证：

> Schema Selector 决定本次 SQL 可以看到哪些表，Semantic 只能解释这些表。

---

### 2.4 Relationship 过滤

只保留满足以下条件的 relationship：

```text
source table 存在
AND
target table 存在
AND
source table 被 selected
AND
target table 被 selected
AND
相关字段存在于当前 DatabaseSchema
```

例如：

```text
inventory.warehouse_id
    →
warehouse.id
```

如果两个 table 都被选中且字段都存在，则保留。

如果：

```text
inventory
```

没有被 selected，则删除该 relationship。

如果：

```text
warehouse.id
```

不存在，则删除该 relationship。

---

# Step 3：不要修改原始 Semantic

过滤必须是不可变/非破坏性的。

不要直接修改：

```text
ProjectSemantic
```

中的原始对象。

应该生成过滤后的对象/结构。

要求：

```text
original semantic
    ↓
filter
    ↓
filtered semantic
```

原始 Semantic 在测试前后必须保持一致。

---

# Step 4：接入 AI Orchestrator

修改：

`backend/app/services/ai_orchestrator_service.py`

当前 Phase 3.9.1 流程大致是：

```text
Question
 ↓
RelevantTableSelector
 ↓
DatabaseContextComposer
 ↓
SemanticSerializer
 ↓
TextToSQLContext
 ↓
TextToSQLGenerator
```

修改为：

```text
Question
 ↓
RelevantTableSelector
 ↓
DatabaseContextComposer
 ↓
Semantic-Schema Filter
 ↓
SemanticSerializer
 ↓
TextToSQLContext
 ↓
TextToSQLGenerator
```

注意：

`Schema` 仍然是事实来源。

Semantic 只是业务解释层。

最终关系必须保持：

```text
DatabaseSchema = Source of Truth
Semantic       = Business Context
SQL Constraints = Security Boundary
```

---

# Step 5：空 Semantic 必须安全

以下情况都不能报错：

### 情况 A

没有配置 Semantic。

结果：

```text
business_context = None / empty
```

### 情况 B

Semantic 全部被过滤。

结果：

```text
business_context = None / empty
```

### 情况 C

当前项目存在 Semantic，但没有任何 selected table 对应的 Semantic。

结果：

```text
business_context = None / empty
```

Text-to-SQL 仍然可以依靠 Schema Context 工作。

---

# Step 6：不要扩大 Schema

特别注意：

绝对不能出现：

```text
Semantic → 推断 Schema
```

只能：

```text
Schema → 限制 Semantic
```

例如：

Semantic：

```text
warehouse_name
```

Schema 没有：

```text
warehouse_name
```

必须删除，而不是：

* 猜测它对应 `name`
* 自动创建映射
* 修改 Schema
* 告诉 LLM “可能是 name”

本阶段不做字段同义词推断。

---

# Step 7：测试

新增专门测试，例如：

`tests/test_business_semantic_context_filter.py`

至少覆盖：

### Test 1：过滤不存在的 table

```text
semantic:
warehouse
fake_table

schema:
warehouse

expected:
warehouse
```

### Test 2：过滤不存在的 column

```text
warehouse:
code
name
fake_column

schema:
code
name

expected:
code
name
```

### Test 3：过滤未 selected 的合法 table

```text
schema:
warehouse
inventory
sales_order

allowed_tables:
warehouse
inventory

semantic:
warehouse
inventory
sales_order

expected:
warehouse
inventory
```

### Test 4：保留合法 relationship

两个 table 和相关字段都存在且 selected。

Expected：

relationship 保留。

### Test 5：过滤非法 relationship

任意一个 table/column 不存在。

Expected：

relationship 删除。

### Test 6：原始 Semantic 不被修改

执行 filter 前后比较 original。

Expected：

完全一致。

### Test 7：空结果

Semantic 全部被过滤。

Expected：

不会异常。

### Test 8：Project A / Project B 隔离

Project A 的 Schema/Semantic：

```text
warehouse_a
inventory_a
```

Project B：

```text
warehouse_b
inventory_b
```

确保：

```text
A → 只能看到 A
B → 只能看到 B
```

不能交叉。

---

# Step 8：Text-to-SQL E2E

增加/修改一个 E2E 测试：

假设 Semantic 配置：

```text
warehouse
inventory
sales_order
fake_table
```

当前 Selector 只选择：

```text
warehouse
inventory
```

最终传给 TextToSQLContext 的：

```text
business_context
```

不得出现：

```text
sales_order
fake_table
```

同时必须包含：

```text
warehouse
inventory
```

确认现有 Fake Generator 不需要修改签名。

---

# Step 9：验证 Prompt 长度

不需要引入 tokenizer。

只验证：

```text
filtered business_context
```

比原始完整 Semantic 更短，或者在 Semantic 被大量过滤时明显减少字符数。

不要新增复杂 token budget 系统。

现有 `DatabaseContextComposer.max_chars` 机制保持不变。

---

# Step 10：回归测试

完成后运行：

```powershell
python -m pytest -q
```

如果需要 DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

然后报告：

* 新增测试数量
* 全量测试结果
* DB 测试结果
* compile/lint 结果
* 是否有 DB residue
* 修改了哪些文件
* 是否改变现有 API / Generator contract

---

# 完成标准

只有满足以下条件才算 Phase 3.9.2 完成：

* [ ] Semantic 只保留当前 selected tables
* [ ] 不存在的 table 被过滤
* [ ] 不存在的 column 被过滤
* [ ] 非 selected 的合法 table 被过滤
* [ ] 非法 relationship 被过滤
* [ ] 原始 Semantic 不被修改
* [ ] 空 Semantic 安全
* [ ] Project A/B 不串数据
* [ ] Text-to-SQL 使用过滤后的 business_context
* [ ] Generator 签名没有变化
* [ ] SQL Validator 没有变化
* [ ] SQL Executor 没有变化
* [ ] 没有新增 LLM/Embedding/Reranker 调用
* [ ] 全量测试通过
* [ ] DB 测试通过（如果环境可用）
* [ ] compile/lint 无错误

---

# 最重要的执行规则

这是一个“小步迭代”阶段。

请严格按照：

```text
阅读现有实现
→ 设计最小改动
→ 实现
→ 测试
→ 回归
→ 汇报
```

执行。

**完成 Phase 3.9.2 后立即停止，不要自动进入 Phase 3.9.3。**
