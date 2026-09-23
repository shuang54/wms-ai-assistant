你现在只执行 **Phase 3.7.2：Schema → Prompt Serializer**。

这是一个严格限定范围的开发阶段。**完成本阶段后必须停止，不得自动进入下一阶段。**

## 一、先阅读现有项目

开始编码前，先阅读并理解：

1. `AGENTS.md`
2. `docs/requirements.md`
3. `docs/architecture.md`
4. `docs/decisions/Phase 3.7.1 — ADR.md`
5. `docs/decisions/Phase 3.7.1.1 — ADR.md`
6. `backend/app/services/schema_explorer_service.py`
7. `backend/app/projects/models.py`
8. `backend/app/projects/context.py`
9. 现有测试目录和测试风格

重点理解现有：

* `DatabaseSchema`
* `SchemaTable`
* `SchemaColumn`
* `SchemaForeignKey`
* `ProjectContext`
* `DataSource`
* `DatabaseMetadataProvider`

不要重新设计这些已有模型。

---

# 二、本阶段目标

实现：

```text
DatabaseSchema
       +
ProjectContext（可选）
       ↓
SchemaSerializer
       ↓
AI-readable Schema Context
```

最终得到一段可以直接作为未来 Text-to-SQL Prompt 上下文的稳定文本。

例如：

```text
Project: Vietnam WMS
Data Source: primary
Database Type: postgresql

## Table: public.knowledge_document
Description: ...

Columns:
- id: bigint [PK, NOT NULL]
- title: character varying(512) [NOT NULL] — ...
- file_name: character varying(512)
- status: character varying(32) [NOT NULL] — ...

Foreign Keys:
- ...

## Table: public.knowledge_chunk
...
```

---

# 三、严格禁止

本阶段禁止实现：

* ❌ LLM 调用
* ❌ Text-to-SQL
* ❌ SQL 生成
* ❌ SQL Validator
* ❌ SQL 执行
* ❌ Tool 修改
* ❌ RAG 修改
* ❌ Chat 修改
* ❌ Agent
* ❌ LangGraph
* ❌ 表语义自动推断
* ❌ LLM 自动选择相关表
* ❌ Embedding / 向量检索
* ❌ 缓存
* ❌ 权限系统
* ❌ 数据库写操作

本阶段只是：

> **Schema DTO → Prompt-friendly text**

---

# 四、实现 Schema Serializer

建议新增：

```text
backend/app/services/schema_serializer_service.py
```

如项目现有风格需要，也可以拆出：

```text
backend/app/services/schema_serializer_models.py
```

但不要为了拆文件而过度设计。

建议核心接口：

```python
class SchemaSerializer:
    def serialize(
        self,
        schema: DatabaseSchema,
        *,
        project: ProjectContext | None = None,
        tables: Sequence[str] | None = None,
        max_chars: int = 12000,
    ) -> str:
        ...
```

具体命名可以根据项目现有命名风格调整。

---

# 五、输出格式要求

输出必须：

## 1. 稳定

相同输入必须产生完全相同的输出。

不要依赖：

* dict 随机顺序
* 数据库返回顺序
* 对象内存地址
* 当前时间
* UUID
* 随机数

排序规则：

```text
schema
→ table
→ column ordinal_position
→ foreign key
```

---

## 2. 包含项目上下文（如果传入）

例如：

```text
Project: Vietnam WMS
Data Source: primary
Database Type: postgresql
```

只允许输出：

* project_id / project_name / description 中适合展示的信息
* datasource name/type

绝对不能输出：

* password
* API key
* token
* connection string
* `.env` 内容
* 数据库用户名密码等敏感配置

不要直接把 `ProjectContext` 或 `DataSource` repr 输出到 Prompt。

---

# 六、表结构格式

每张表使用：

```text
## Table: public.knowledge_document
Description: Knowledge document metadata

Columns:
- id: bigint [PK, NOT NULL]
- title: character varying(512) [NOT NULL]
- file_name: character varying(512)
- status: character varying(32) [NOT NULL]

Foreign Keys:
- ...
```

要求：

### Table

必须保留：

```text
schema.table
```

不能只输出 table name。

### Description

如果存在 table comment：

```text
Description: xxx
```

如果没有 comment：

```text
Description: —
```

**绝对不要自行猜测业务含义。**

---

# 七、Column 格式

每个字段至少保留：

* column name
* data type
* nullable
* PK
* comment（如果存在）

例如：

```text
- id: bigint [PK, NOT NULL]
- title: character varying(512) [NOT NULL] — 文档标题
- embedding: vector(1024)
```

Nullable 规则：

```text
nullable=False → NOT NULL
nullable=True  → NULLABLE
```

如果当前 DTO 对 nullable 的表达方式不同，以现有模型为准。

Default 如果存在，可以保留，例如：

```text
- status: character varying(32) [NOT NULL, DEFAULT 'pending']
```

但不要为了展示 default 而改变现有 DTO。

---

# 八、Foreign Key 格式

保留完整关系：

```text
Foreign Keys:
- document_id -> public.knowledge_document.id
```

如果现有 DTO 包含：

* source schema
* source table
* source column
* target schema
* target table
* target column

必须完整输出。

不要推断：

```text
document_id 是文档ID
```

这种业务语义。

---

# 九、Table Selection

支持：

```python
tables=None
```

表示序列化全部表。

也支持：

```python
tables=["public.knowledge_document"]
```

只输出指定表。

可以支持：

```python
tables=["knowledge_document", "knowledge_chunk"]
```

但必须有明确、稳定的匹配规则。

推荐：

* `schema.table`：精确匹配
* `table`：在当前 schema 中匹配

不要通过 SQL 处理。

这是对已经获取到的 `DatabaseSchema` DTO 做内存过滤。

---

# 十、max_chars

未来真实企业数据库可能存在：

```text
500+ tables
5000+ columns
```

不能无上限把整个 Schema 塞给 LLM。

因此必须支持：

```python
max_chars
```

默认：

```text
12000
```

这是**字符预算，不是精确 Token 数**。

在代码注释和 ADR 中明确：

> 当前 MVP 使用字符数近似控制 Prompt Budget，不引入 Tokenizer 依赖。

---

# 十一、截断策略

不要简单：

```python
text[:max_chars]
```

因为可能把一行字段截断成：

```text
- customer_address: character varying(2
```

要求尽可能按完整结构截断。

优先级：

```text
Project Header
↓
Table Header
↓
Table Description
↓
Columns
↓
Foreign Keys
↓
后续 Table
```

当预算不足时，可以：

1. 完整保留已经开始输出的表
2. 后续表不再输出
3. 最后增加：

```text
[Schema truncated: max_chars=12000]
```

如果单张表本身就超过预算：

* 至少保留 Table Header
* 尽可能保留完整字段行
* 不允许输出半截字段行

不要实现复杂的智能压缩。

---

# 十二、错误处理

明确处理：

### schema=None

抛出合适的项目异常。

### max_chars <= 0

拒绝。

### tables 中指定不存在的表

不要静默忽略。

建议抛出：

```python
SchemaSerializationError
```

并清楚说明缺少哪些表。

具体异常命名遵循现有项目风格。

---

# 十三、DTO / Immutability

如果需要新增返回 DTO：

优先使用：

```python
@dataclass(frozen=True)
```

或者项目已有的不可变模型方式。

不要创建 ORM 对象。

Serializer 不应该依赖 SQLAlchemy Session。

---

# 十四、测试

新增：

```text
tests/test_schema_serializer_service.py
```

至少覆盖：

### 基础

1. 空 Schema
2. 单表
3. 多表
4. table comment
5. column comment
6. nullable
7. PK
8. default
9. FK

### 稳定性

10. 相同输入 → 相同输出
11. 输入顺序变化 → 输出仍然稳定排序

### Selection

12. 全部 tables
13. 指定 `schema.table`
14. 指定 table name
15. 指定不存在 table → 明确异常

### Budget

16. max_chars 正常
17. max_chars <= 0 → 异常
18. 多表超过预算
19. 不产生半截字段
20. 包含 truncation marker

### Security

21. ProjectContext 中不能因为 serializer 而输出密码/API Key/连接串
22. 不读取 `.env`
23. 不输出数据库连接信息

### Immutability

24. serialize 不修改原始 `DatabaseSchema`

---

# 十五、集成测试

如果方便，在现有 DB 测试体系下增加少量真实 DB 验证：

```text
SchemaExplorerService
        ↓
DatabaseSchema
        ↓
SchemaSerializer
        ↓
string
```

验证当前数据库中的：

```text
public.knowledge_document
public.knowledge_chunk
```

能够正确序列化。

不要修改数据库。

---

# 十六、ADR

新增：

```text
docs/decisions/Phase 3.7.2 — ADR.md
```

至少说明：

### Context

Schema Explorer 已经能够得到结构化 DatabaseSchema，但 Text-to-SQL 后续需要一种稳定、Token-aware、LLM-readable 的表示方式。

### Decision

增加独立 Schema Serializer。

职责：

```text
DatabaseSchema → Prompt Context
```

不负责：

```text
Schema → Business Meaning
Schema → Relevant Tables
Schema → SQL
```

### Budget

MVP 使用字符预算，不引入 Tokenizer。

### Selection

当前只支持显式表选择。

未来如果需要，可以增加：

```text
Question
↓
Relevant Table Selection
↓
Schema Serializer
```

但不在本阶段实现。

### Security

Serializer 不读取数据库连接凭据，也不输出 secrets。

---

# 十七、代码质量要求

保持当前项目风格：

* 类型注解
* 清晰 docstring
* 小函数
* 无重复逻辑
* 无 ORM 依赖
* 无全局可变状态
* 不修改现有 Schema Explorer 行为

尤其注意：

> Schema Serializer 是 Presentation / Prompt Context 层，不是 Business Semantic Layer。

不要在这个阶段加入任何业务规则。

---

# 十八、验证

完成后执行：

```text
pytest -q
```

如果项目已有 DB 测试方式，再执行完整 DB 回归，例如：

```text
RUN_DB_TESTS=1 pytest -q
```

同时执行：

```text
python -m compileall backend
```

检查：

* 新测试全部通过
* 原有测试不能回归失败
* 无 SQL 修改
* 无 DB 数据修改
* 无 LLM 调用
* 无 Tool/RAG/Chat 行为变化

---

# 十九、最终报告

完成后只汇报：

1. 新增/修改了哪些文件
2. Serializer 的核心接口
3. 输出格式
4. Selection 规则
5. max_chars 截断策略
6. 测试数量
7. 全量测试结果
8. 是否修改数据库
9. 是否调用 LLM
10. 当前阶段结论

**然后停止。不要自动开始 Phase 3.7.3。**
