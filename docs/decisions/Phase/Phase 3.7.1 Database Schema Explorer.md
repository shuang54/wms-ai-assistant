你现在开始执行项目 **Phase 3.7.1 — Database Schema Explorer（数据库结构探索器）**。

这是一个严格受控的单阶段任务。

## 一、开始前必须做

先阅读并理解：

1. `AGENTS.md`
2. `docs/requirements.md`
3. `docs/architecture.md`
4. 当前 `backend/app/db/`、`backend/app/models/`、`backend/app/config.py`
5. 当前测试结构和数据库连接方式

先检查现有代码，**不要重复实现已经存在的数据库连接、Session、配置能力**。

---

# 二、本阶段唯一目标

让 AI 项目能够从当前 PostgreSQL 数据库中：

* 获取 Schema
* 获取表
* 获取字段
* 获取字段类型
* 获取主键
* 获取外键关系
* 获取数据库/表/字段注释（如果数据库中存在）
* 将这些信息转换成稳定的结构化 DTO

最终形成：

```text
PostgreSQL
    ↓
Schema Explorer
    ↓
DatabaseSchema
    ├── tables
    │   ├── table_name
    │   ├── description
    │   └── columns
    │       ├── name
    │       ├── data_type
    │       ├── nullable
    │       ├── default
    │       ├── is_primary_key
    │       └── description
    │
    └── foreign_keys
```

本阶段的产物是一个**数据库结构读取能力**。

---

# 三、建议实现位置

根据当前项目结构自行判断最终位置，但优先考虑：

```text
backend/app/services/schema_explorer_service.py
```

以及必要的 DTO，例如：

```text
backend/app/services/schema_models.py
```

或者合理的独立模块。

测试：

```text
tests/test_schema_explorer_service.py
```

如果当前项目已有合适的数据库 metadata / inspection 模块，应优先复用，而不是重复创建。

---

# 四、数据库读取范围

默认只读取当前项目配置的 PostgreSQL 数据库。

重点读取 PostgreSQL Catalog：

```text
information_schema
pg_catalog
```

至少支持：

### 1. 表

获取：

```text
schema_name
table_name
table_description
```

默认只关注业务表。

不要把 PostgreSQL 系统表全部暴露给 AI。

至少排除：

```text
pg_catalog
information_schema
```

如果当前项目存在明确的业务 schema，则优先使用业务 schema。

不要擅自假设业务 schema 名称。

先检查当前数据库实际情况。

---

### 2. 字段

每个表获取：

```text
column_name
data_type
nullable
default
description
ordinal_position
```

其中：

```text
description
```

来自 PostgreSQL 字段 comment。

如果没有 comment：

```text
description = None
```

不要让 AI 自己猜字段含义。

---

### 3. 主键

获取每个表的主键字段。

例如：

```text
id
```

最终 DTO 能明确表示：

```text
is_primary_key = true
```

---

### 4. 外键

读取表之间的外键关系。

至少包含：

```text
source_schema
source_table
source_column

target_schema
target_table
target_column
```

例如：

```text
knowledge_chunk.document_id
        ↓
knowledge_document.id
```

---

# 五、DTO 设计

DTO 使用 frozen dataclass 或当前项目已经采用的等价不可变 DTO 风格。

建议：

```python
SchemaColumn
SchemaForeignKey
SchemaTable
DatabaseSchema
```

大致语义：

```text
SchemaColumn
├── name
├── data_type
├── nullable
├── default
├── ordinal_position
├── is_primary_key
└── description

SchemaForeignKey
├── source_schema
├── source_table
├── source_column
├── target_schema
├── target_table
└── target_column

SchemaTable
├── schema_name
├── name
├── description
├── columns
└── foreign_keys

DatabaseSchema
└── tables
```

具体字段类型和命名根据当前项目风格调整。

---

# 六、Service API

至少提供：

```python
async def inspect(
    self,
    *,
    schema: str | None = None,
) -> DatabaseSchema
```

并且允许指定 schema。

例如：

```python
schema = await service.inspect(schema="public")
```

如果：

```python
schema=None
```

则使用项目默认业务 schema 策略。

不要把 schema 名称硬编码成某个具体业务名称。

---

# 七、重要：不要引入 AI

本阶段：

**禁止调用 DeepSeek。**

不要：

```text
Database → LLM → Schema
```

而是：

```text
Database
    ↓
Schema Explorer
    ↓
结构化 DTO
```

AI 理解 Schema 是下一阶段的工作。

---

# 八、重要：不要实现 Text-to-SQL

本阶段禁止实现：

```text
用户问题
 ↓
LLM
 ↓
SQL
```

也禁止：

```text
execute_sql()
```

不要创建：

```text
SQLGenerator
SQLExecutor
NL2SQL
TextToSQL
```

这些全部留到后续 Phase。

---

# 九、重要：不要修改现有 Tool

不要修改：

```text
ToolRegistry
ToolDefinition
ToolChatService
get_inventory
get_work_order
```

也不要修改：

```text
/api/chat
/api/chat/with-tools
/api/rag/answer
```

本阶段只是新增数据库 Schema 探索能力。

---

# 十、安全要求

Schema Explorer：

### 允许

```text
SELECT
information_schema
pg_catalog
```

### 禁止

```text
INSERT
UPDATE
DELETE
DROP
ALTER
TRUNCATE
CREATE
```

本阶段所有数据库操作必须是只读 metadata 查询。

不要执行任何业务数据修改。

---

# 十一、测试要求

新增完整单元测试。

至少覆盖：

### 1. 空数据库

返回：

```text
tables = ()
```

或者当前项目约定的等价空集合。

---

### 2. 单表

验证：

```text
table name
columns
types
nullable
```

---

### 3. 主键

建立测试表：

```text
id BIGINT PRIMARY KEY
```

验证：

```text
is_primary_key == true
```

---

### 4. 外键

例如：

```text
parent(id)
child(parent_id REFERENCES parent(id))
```

验证：

```text
child.parent_id
        ↓
parent.id
```

---

### 5. Comment

验证 PostgreSQL：

```sql
COMMENT ON TABLE ...
COMMENT ON COLUMN ...
```

可以正确读取。

---

### 6. schema 参数

至少测试：

```text
schema="public"
```

以及不存在 schema 的处理。

---

### 7. SQL Injection 防护

schema 参数不能直接字符串拼接到危险 SQL 中。

例如测试：

```text
public"; DROP TABLE ...
```

不能导致任何 DDL/DML 执行。

---

### 8. DTO

验证 DTO：

* 不暴露 SQLAlchemy ORM 对象
* 不可变
* 类型稳定
* 嵌套结构稳定

---

# 十二、数据库测试策略

优先复用项目现有：

```text
RUN_DB_TESTS
```

机制。

如果当前项目已有测试数据库 fixture，就复用。

不要重新创建一套数据库测试框架。

测试创建的表、comment 等资源必须：

```text
测试前创建
    ↓
测试执行
    ↓
测试后清理
```

不能污染当前真实 WMS 知识库数据。

尤其不要：

```text
TRUNCATE knowledge_document
TRUNCATE knowledge_chunk
```

也不要删除当前已有知识库数据。

---

# 十三、输出格式

Schema Explorer 最终返回的数据必须是稳定、适合后续 AI 使用的结构。

例如：

```text
DatabaseSchema
 ├── tables
 │
 ├── knowledge_document
 │   ├── id bigint PK
 │   ├── title varchar
 │   ├── file_name varchar
 │   └── ...
 │
 └── knowledge_chunk
     ├── id bigint PK
     ├── document_id bigint
     ├── content text
     ├── embedding vector
     └── ...
```

但不要在代码中把上述表名写死。

必须从数据库动态读取。

---

# 十四、日志

日志可以记录：

```text
schema
table_count
column_count
foreign_key_count
elapsed_ms
```

禁止记录：

* API Key
* Authorization
* 数据库密码
* DATABASE_URL 完整值
* 业务数据内容

---

# 十五、文档

完成后增加一个简短文档：

```text
docs/decisions/Phase 3.7.1 — ADR.md
```

说明：

1. 为什么需要 Schema Explorer
2. 为什么暂时不做 Text-to-SQL
3. Schema Explorer 的职责
4. 后续 Text-to-SQL 如何使用它
5. 为什么 AI 不应该直接拥有数据库连接

不要写过度设计。

---

# 十六、严格禁止范围

本阶段不要做：

* Text-to-SQL
* SQL Generator
* SQL Executor
* SQL Validator
* RAG 修改
* Tool Calling 修改
* Agent
* LangGraph
* MCP
* 前端
* HTTP API
* WMS API
* 数据修改
* 自动 Schema 总结
* LLM Schema 理解
* Embedding Schema
* Reranker
* Cache
* Redis

---

# 十七、验收标准

完成后执行：

```text
1. 单元测试
2. 数据库测试（如果 RUN_DB_TESTS=1）
3. 全量回归测试
4. py_compile / 当前项目已有静态检查
```

必须确认：

```text
现有 RAG 不受影响
现有 Chat 不受影响
现有 Tool Calling 不受影响
现有 Multi-Step Tool Calling 不受影响
知识库数据不受影响
```

---

# 十八、最重要的执行规则

**只完成 Phase 3.7.1。**

不要自动进入 Phase 3.7.2。

完成后立即停止，并向我汇报：

```text
【Phase 3.7.1 完成】

1. 新增文件：
2. 修改文件：
3. Schema Explorer 能力：
4. DTO：
5. 测试结果：
6. 全量回归结果：
7. 数据库是否被污染：
8. 是否修改现有 Tool：
9. 是否调用 LLM：
10. 当前数据库实际发现了多少业务表：
11. 当前阶段遗留问题：
12. 下一阶段建议：
```

**不要自行开始下一阶段。**
