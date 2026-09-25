你现在只执行 **Phase 3.7.3：Business Semantic Layer（业务语义层）**。

这是一个严格限定范围的开发阶段。

**完成本阶段后必须停止，不得自动进入 Phase 3.7.4。**

---

# 一、背景

当前已经完成：

```text
Phase 3.7.1
SchemaExplorerService
        ↓
DatabaseSchema

Phase 3.7.1.1
ProjectContext
DataSource
DatabaseMetadataProvider

Phase 3.7.2
DatabaseSchema
        ↓
SchemaSerializer
        ↓
Prompt-friendly Schema Context
```

当前链路已经能够告诉 LLM：

```text
public.xxx
    ├── id
    ├── material_code
    ├── qty
    └── warehouse_id
```

但还存在一个问题：

> 数据库结构 ≠ 业务语义。

例如：

```text
material_code
qty
warehouse_id
```

LLM 不应该自己猜：

```text
material_code = 物料编码
qty = 库存数量
warehouse_id = 仓库
```

因此本阶段建立：

```text
Project
   ↓
Business Semantic
   ↓
Database Schema
   ↓
AI-readable Semantic Context
```

---

# 二、本阶段目标

建立一个**项目可配置、AI Core 无业务耦合**的 Business Semantic Layer。

核心原则：

> AI Core 不知道“WMS 是什么”，Project Semantic 才知道。

最终结构：

```text
AI Core
   │
   ├── RAG
   ├── Tools
   ├── Text-to-SQL
   │
   └── ProjectContext
          │
          ├── DataSource
          ├── DatabaseSchema
          └── BusinessSemantic
```

未来切换项目时：

```text
Vietnam WMS
    ↓
VietnamWmsSemantic

其他 ERP
    ↓
ErpSemantic

MES
    ↓
MesSemantic
```

AI Core 不修改。

---

# 三、严格禁止

本阶段禁止实现：

* ❌ LLM 调用
* ❌ Text-to-SQL
* ❌ SQL 生成
* ❌ SQL Validator
* ❌ SQL 执行
* ❌ 自动表选择
* ❌ Embedding
* ❌ RAG 修改
* ❌ Chat 修改
* ❌ Tool 修改
* ❌ Agent
* ❌ LangGraph
* ❌ 自动从数据库推断业务语义
* ❌ 使用 LLM 自动生成业务语义
* ❌ 修改数据库业务数据
* ❌ 修改现有 Schema Explorer 行为

本阶段只做：

```text
Business Semantic
        ↓
结构化定义
        ↓
加载
        ↓
与 DatabaseSchema 对齐
        ↓
生成 AI-readable Semantic Context
```

---

# 四、先阅读现有代码

开始编码前必须阅读：

```text
AGENTS.md
docs/requirements.md
docs/architecture.md

docs/decisions/Phase 3.7.1 — ADR.md
docs/decisions/Phase 3.7.1.1 — ADR.md
docs/decisions/Phase 3.7.2 — ADR.md

backend/app/projects/
backend/app/services/schema_explorer_service.py
backend/app/services/schema_serializer_service.py
backend/app/config.py

tests/test_project_context.py
tests/test_schema_explorer_service.py
tests/test_schema_serializer_service.py
```

同时搜索：

```text
ProjectContext
DataSource
DatabaseSchema
SchemaSerializer
```

理解现有模型后再设计。

**不要重复创建已有 DTO。**

---

# 五、设计原则

Business Semantic 必须满足：

## 1. 项目隔离

语义属于 Project，而不是 AI Core。

例如：

```text
ProjectContext
    └── semantic
```

或者通过独立的：

```text
ProjectSemantic
```

关联。

具体设计根据当前项目结构决定。

---

# 六、第一版 Semantic 模型

建议至少支持以下概念：

## 1. Table Semantic

描述业务表。

例如：

```text
Table: public.inventory

Business Name: 库存

Description:
当前库存明细。

Aliases:
- 库存
- 库存明细
- 库存数据
```

注意：

> Business Name / Description / Aliases 都是人工配置，不允许自动推断。

---

## 2. Column Semantic

描述字段。

例如：

```text
Table: public.inventory

Column: material_code

Business Name: 物料编码

Description:
企业内部物料编码。

Aliases:
- 物料编码
- 料号
- SKU
```

---

## 3. Business Relationship

支持业务关系。

例如：

```text
inventory.material_code
    →
material.code
```

或者：

```text
sales_order
    └── sales_order_detail
```

但本阶段只建立**描述模型**。

不要实现复杂关系推理。

---

# 七、建议模型

可以新增：

```text
backend/app/projects/semantic.py
```

或者按照当前项目结构拆分。

建议使用 frozen dataclass：

```python
@dataclass(frozen=True)
class TableSemantic:
    table: str
    business_name: str | None = None
    description: str | None = None
    aliases: tuple[str, ...] = ()
```

```python
@dataclass(frozen=True)
class ColumnSemantic:
    table: str
    column: str
    business_name: str | None = None
    description: str | None = None
    aliases: tuple[str, ...] = ()
```

```python
@dataclass(frozen=True)
class BusinessRelationship:
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    description: str | None = None
```

最终可以有：

```python
@dataclass(frozen=True)
class ProjectSemantic:
    tables: tuple[TableSemantic, ...] = ()
    columns: tuple[ColumnSemantic, ...] = ()
    relationships: tuple[BusinessRelationship, ...] = ()
```

具体字段和命名可以根据项目现有风格调整。

---

# 八、不要把 Semantic 写死在代码中

非常重要。

不要：

```python
if table == "inventory":
    ...
```

不要：

```python
SEMANTICS = {
    "inventory": ...
}
```

这种方式会让 AI 项目重新绑定 WMS。

第一版建议使用**项目配置文件**。

例如：

```text
backend/app/projects/
    semantic/
        vietnam-wms.yaml
```

或者：

```text
config/projects/
    vietnam-wms/
        semantic.yaml
```

具体目录根据当前项目结构选择。

目标结构类似：

```yaml
tables:
  - table: public.inventory
    business_name: 库存
    description: 当前库存数据
    aliases:
      - 库存
      - 库存明细

columns:
  - table: public.inventory
    column: material_code
    business_name: 物料编码
    description: 企业内部物料编码
    aliases:
      - 物料编码
      - 料号
      - SKU
```

---

# 九、不要现在填充大量真实 WMS 业务语义

这是一个非常重要的边界。

本阶段不是：

> 把整个越南 WMS 的业务知识全部录进去。

只需要创建：

**最小示例 Semantic**

用于验证机制。

例如只定义当前知识库表：

```text
public.knowledge_document
public.knowledge_chunk
```

可以定义：

```text
knowledge_document
    → 知识文档

knowledge_chunk
    → 知识文档分片
```

以及几个字段。

不要大量扩展业务表。

---

# 十、Semantic Loader

需要一个独立的加载器。

例如：

```text
backend/app/projects/semantic_loader.py
```

负责：

```text
project_id
    ↓
加载 semantic 配置
    ↓
ProjectSemantic
```

接口可以类似：

```python
class ProjectSemanticLoader:
    def load(self, project_id: str) -> ProjectSemantic:
        ...
```

要求：

* 不访问数据库
* 不调用 LLM
* 不依赖 FastAPI
* 不依赖 SQLAlchemy
* 配置不存在时行为明确
* YAML/JSON 解析错误要有清晰异常
* 不允许静默吞掉配置错误

---

# 十一、Schema 对齐

这是本阶段最重要的部分之一。

Semantic 可能配置：

```text
public.inventory
```

但实际数据库可能没有这个表。

因此需要一个校验层。

建议：

```text
ProjectSemantic
        +
DatabaseSchema
        ↓
SemanticSchemaValidator
```

检查：

### Table

Semantic 中指定的：

```text
public.inventory
```

必须存在于 DatabaseSchema。

### Column

Semantic 中指定：

```text
public.inventory.material_code
```

必须存在。

### Relationship

source / target table 和 column 都必须存在。

---

# 十二、不要自动修复

如果 Semantic 配置：

```text
public.inventory.material_code
```

但数据库只有：

```text
public.inventory.mat_code
```

不要自动修改。

应该报明确错误：

```text
Semantic references unknown column:
public.inventory.material_code
```

这样以后数据库结构发生变化时能够尽早发现。

---

# 十三、Semantic Context Serializer

在现有：

```text
SchemaSerializer
```

之外新增一个专门的：

```text
BusinessSemanticSerializer
```

例如：

```text
backend/app/services/business_semantic_serializer.py
```

职责：

```text
ProjectSemantic
        ↓
AI-readable Business Semantic Context
```

输出类似：

```text
Business Semantics:

## Table: public.knowledge_document
Business Name: 知识文档
Description: 存储知识库文档信息
Aliases:
- 知识文档
- 文档

Columns:
- title
  Business Name: 文档标题
  Description: 文档标题
  Aliases: 标题

## Table: public.knowledge_chunk
Business Name: 知识分片
Description: 文档切分后的知识片段
```

要求：

* 稳定排序
* 不输出 secrets
* 不猜业务含义
* 不依赖 ORM
* 不调用 LLM

---

# 十四、SchemaSerializer 与 SemanticSerializer 分工

必须保持清晰：

```text
SchemaSerializer
    ↓
数据库事实
```

例如：

```text
qty: numeric
nullable
PK
FK
comment
```

而：

```text
BusinessSemanticSerializer
    ↓
业务人工定义
```

例如：

```text
qty
Business Name: 库存数量
Description: 当前可用库存数量
Aliases:
- 库存
- 库存数量
```

两者不要混为一个类。

最终未来 Text-to-SQL Context 可以组合：

```text
Schema Context
+
Business Semantic Context
```

而不是修改 SchemaSerializer 的职责。

---

# 十五、Context Composer

如果实现简单，可以增加一个非常轻量的组合层，例如：

```text
Schema Context
        +
Business Semantic Context
        ↓
AI Database Context
```

但不要把它发展成 Prompt Engine。

建议只负责字符串组合：

```text
Project Context

Database Schema

Business Semantics
```

不要：

* 调 LLM
* 管 Prompt Template
* 管 Chat
* 管 Tool

---

# 十六、测试

新增测试，例如：

```text
tests/test_project_semantic.py
tests/test_project_semantic_loader.py
tests/test_business_semantic_serializer.py
tests/test_semantic_schema_validator.py
```

具体可以合并，但不要为了数量拆文件。

至少测试：

### Model

1. frozen / immutable
2. 默认值
3. aliases
4. relationship

### Loader

5. 正常加载
6. 不存在 project
7. 文件格式错误
8. 必填字段错误
9. 空 semantic

### Serializer

10. table semantic
11. column semantic
12. aliases
13. relationship
14. 稳定排序
15. 空 semantic

### Validator

16. 存在的 table
17. 不存在 table
18. 存在 column
19. 不存在 column
20. relationship source 不存在
21. relationship target 不存在
22. 多个错误一次返回，而不是发现第一个就停止

### Security

23. semantic 中出现 password / api_key / token 等字段时，不应被特殊展开或读取外部 secrets
24. 不读取 `.env`
25. 不访问数据库

### Integration

26. 当前 `DatabaseSchema`
    +
    当前最小 `ProjectSemantic`
    →
    Validator PASS

---

# 十七、当前项目真实 DB 集成

使用现有真实数据库。

不要修改任何数据库数据。

验证：

```text
SchemaExplorerService
        ↓
DatabaseSchema
        ↓
ProjectSemantic
        ↓
SemanticSchemaValidator
        ↓
PASS
```

当前至少验证：

```text
public.knowledge_document
public.knowledge_chunk
```

---

# 十八、ADR

新增：

```text
docs/decisions/Phase 3.7.3 — ADR.md
```

至少记录：

### Context

Schema 描述数据库事实，但不足以表达业务语义。

### Decision

增加独立 Project Business Semantic Layer。

### Important Principle

```text
Schema = database facts

Semantic = human-defined business meaning
```

### Project Isolation

Semantic 属于 Project，而不是 AI Core。

### Validation

Semantic 必须与实际 DatabaseSchema 对齐。

### No Automatic Inference

当前版本禁止：

```text
Database Schema
↓
AI 猜业务语义
```

### Future

未来可以支持：

```text
Question
↓
Relevant Table Selection
↓
Schema + Semantic Context
↓
Text-to-SQL
```

但本阶段不实现。

---

# 十九、回归要求

执行：

```bash
pytest -q
```

以及：

```bash
RUN_DB_TESTS=1 pytest -q
```

并执行：

```bash
python -m compileall backend
```

要求：

```text
新测试全部 PASS
旧测试全部 PASS
```

不能因为本阶段修改导致：

* RAG 回归
* Chat 回归
* Tool 回归
* Multi-Step Tool 回归
* Schema Explorer 回归
* Schema Serializer 回归

---

# 二十、数据库要求

本阶段：

```text
只读
```

禁止：

```text
INSERT
UPDATE
DELETE
ALTER
CREATE
DROP
TRUNCATE
```

不允许留下测试表、测试数据或 schema。

---

# 二十一、LLM 要求

本阶段：

```text
LLM calls = 0
```

不得调用：

* DeepSeek
* Embedding API
* Reranker
* RAG

---

# 二十二、最终报告

完成后只汇报：

1. 新增/修改文件
2. Semantic 模型
3. Semantic 配置文件格式
4. Loader
5. Schema 对齐 Validator
6. BusinessSemanticSerializer
7. 是否实现 Context Composer
8. 测试数量
9. 全量测试结果
10. 是否修改数据库
11. 是否调用 LLM
12. 当前阶段结论

**完成报告后立即停止。**

不要自动进入 Phase 3.7.4。

尤其不要自行开始：

```text
Text-to-SQL
SQL Validator
自动选表
LLM Router
```
