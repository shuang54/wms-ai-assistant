你现在只执行 **Phase 3.7.4：Relevant Table Selection（问题 → 相关表选择）**。

**完成本阶段后必须停止，不得自动进入 Phase 3.7.5。**

---

# 一、背景

当前已经完成：

```text
Phase 3.7.1
SchemaExplorerService
        ↓
DatabaseSchema

Phase 3.7.1.1
ProjectContext / DataSource / Provider

Phase 3.7.2
DatabaseSchema
        ↓
SchemaSerializer
        ↓
Prompt-friendly Schema Context

Phase 3.7.3
ProjectSemantic
        ↓
SemanticSchemaValidator
        ↓
BusinessSemanticSerializer
        ↓
DatabaseContextComposer
```

当前已经能够得到完整的：

```text
Database Schema
+
Business Semantics
```

但是企业数据库可能存在：

```text
500+ tables
5000+ columns
```

不能每次 Text-to-SQL 都把全部 Schema 放进 Prompt。

因此本阶段建立：

```text
User Question
      ↓
Relevant Table Selector
      ↓
Selected Tables
      ↓
Schema + Business Semantic Context
```

---

# 二、本阶段核心目标

建立一个**独立、可替换的相关表选择层**。

核心接口应该允许未来替换不同策略：

```text
Rule-based Selector
       ↓
Embedding Selector
       ↓
LLM Selector
       ↓
Hybrid Selector
```

但是：

> **本阶段只实现 Rule-based / Semantic-based 的最小版本。**

不得调用 LLM。

---

# 三、严格禁止

本阶段禁止：

* ❌ DeepSeek
* ❌ LLM 调用
* ❌ Text-to-SQL
* ❌ SQL 生成
* ❌ SQL Validator
* ❌ SQL 执行
* ❌ 数据库查询业务数据
* ❌ Embedding API
* ❌ RAG 修改
* ❌ Chat 修改
* ❌ Tool 修改
* ❌ Agent
* ❌ LangGraph
* ❌ 自动修改 ProjectSemantic
* ❌ 自动修改 DatabaseSchema

本阶段只解决：

> **Question → Relevant Tables**

---

# 四、先阅读

开始编码前必须阅读：

```text
AGENTS.md
docs/requirements.md
docs/architecture.md

docs/decisions/Phase 3.7.1 — ADR.md
docs/decisions/Phase 3.7.1.1 — ADR.md
docs/decisions/Phase 3.7.2 — ADR.md
docs/decisions/Phase 3.7.3 — ADR.md

backend/app/projects/
backend/app/services/schema_serializer_service.py
backend/app/services/business_semantic_serializer.py
backend/app/services/database_context_composer.py
backend/app/services/semantic_schema_validator.py

tests/
```

理解现有：

```text
DatabaseSchema
ProjectSemantic
TableSemantic
ColumnSemantic
SchemaSerializer
BusinessSemanticSerializer
DatabaseContextComposer
```

不要重复设计已有 DTO。

---

# 五、核心抽象

建议新增：

```text
backend/app/services/relevant_table_selector.py
```

或者按照现有项目结构合理拆分。

核心应该存在一个 Protocol / 抽象接口：

```python
class RelevantTableSelector(Protocol):
    def select(
        self,
        question: str,
        schema: DatabaseSchema,
        semantic: ProjectSemantic,
        *,
        top_k: int = 5,
    ) -> TableSelectionResult:
        ...
```

具体命名根据项目现有风格调整。

重点：

> 上层只依赖 Selector 接口，不依赖具体选择算法。

---

# 六、返回结果

建议定义 frozen DTO：

```python
@dataclass(frozen=True)
class TableSelection:
    table: str
    score: float
    matched_terms: tuple[str, ...]
```

以及：

```python
@dataclass(frozen=True)
class TableSelectionResult:
    question: str
    selections: tuple[TableSelection, ...]
```

具体字段可根据现有代码风格调整。

要求：

* frozen
* immutable
* 稳定
* 不返回 ORM
* 不暴露数据库连接信息

---

# 七、选择算法

本阶段不要实现复杂 NLP。

使用一个**可解释的关键词 / 业务语义匹配算法**即可。

候选信息来源：

```text
TableSemantic.business_name
TableSemantic.description
TableSemantic.aliases

ColumnSemantic.business_name
ColumnSemantic.description
ColumnSemantic.aliases

实际 table name
实际 column name
```

例如：

```text
Table:
public.inventory

Business Name:
库存

Aliases:
- 库存
- 库存明细

Column:
material_code

Business Name:
物料编码

Aliases:
- 物料编码
- 料号
- SKU
```

用户：

```text
查询 SKU 10001 当前库存
```

应该能够识别：

```text
public.inventory
```

---

# 八、匹配必须可解释

不要做黑盒分数。

至少能够回答：

```text
为什么选择这个表？
```

例如：

```text
Table: public.inventory
Score: 4.0
Matched Terms:
- 库存
- SKU
```

---

# 九、最小评分规则

建立简单、稳定、可测试的规则。

建议：

### Table Name

用户问题命中 table name：

```text
+3
```

### Business Name

命中：

```text
+5
```

### Alias

命中：

```text
+4
```

### Description

命中：

```text
+2
```

### Column Name

命中：

```text
+2
```

### Column Business Name

命中：

```text
+4
```

### Column Alias

命中：

```text
+3
```

这些权重只是 MVP 默认值。

如果项目现有配置风格更适合，也可以抽成不可变配置：

```python
@dataclass(frozen=True)
class TableSelectionWeights:
    table_name: float = 3.0
    table_business_name: float = 5.0
    table_alias: float = 4.0
    table_description: float = 2.0
    column_name: float = 2.0
    column_business_name: float = 4.0
    column_alias: float = 3.0
```

但不要为了配置化过度设计。

---

# 十、中文匹配

必须考虑中文。

不要只使用：

```python
question.split()
```

因为：

```text
查询当前库存
```

不能正确得到：

```text
查询
当前
库存
```

建议至少支持：

1. 完整 substring 匹配
2. alias substring 匹配
3. 英文不区分大小写
4. table/column 名称不区分大小写

例如：

```text
库存
```

可以匹配：

```text
查询当前库存
```

而：

```text
SKU
```

可以匹配：

```text
查询 SKU 10001
```

不要在本阶段引入 jieba、HanLP 等新的 NLP 依赖。

---

# 十一、数字和业务值

例如：

```text
查询物料 10001 的库存
```

不要把：

```text
10001
```

当成必须匹配的业务语义。

重点识别：

```text
物料
库存
```

但是不要实现复杂实体识别。

可以简单忽略纯数字 token。

---

# 十二、标准化

匹配前做统一标准化：

```text
strip
lower
连续空白压缩
```

英文：

```text
SKU
sku
Sku
```

视为相同。

中文保持原字符。

不要修改原始 Question。

---

# 十三、去重

一个表可能因为：

```text
库存
inventory
库存明细
qty
库存数量
```

命中多次。

最终：

```text
TableSelection
```

每张表只能出现一次。

matched_terms 去重。

---

# 十四、排序

结果必须稳定：

第一排序：

```text
score DESC
```

第二排序：

```text
schema.table ASC
```

例如：

```text
1. public.inventory      9.0
2. public.material        7.0
3. public.warehouse       5.0
```

如果分数相同，不能随机。

---

# 十五、top_k

默认：

```text
top_k=5
```

要求：

```text
top_k >= 1
```

建议设置合理上限，例如：

```text
top_k <= 50
```

具体上限遵循项目现有 API / 配置习惯。

非法输入必须抛出明确异常。

---

# 十六、零匹配

如果：

```text
question = "今天北京天气怎么样"
```

而数据库语义中没有相关表。

应该返回：

```text
TableSelectionResult(
    selections=()
)
```

不要：

* 猜一个表
* 默认返回全部表
* 调 LLM
* 抛异常

因为：

> “没有找到相关表”本身是正常结果。

未来 Router 可以决定是否转 RAG 或普通 Chat。

---

# 十七、没有 Semantic 的情况

必须支持：

```text
ProjectSemantic()
```

也就是说，即使没有业务语义，也能够根据：

```text
table name
column name
```

进行最基本匹配。

这样 Selector 不依赖 YAML 一定存在。

---

# 十八、与 DatabaseSchema 的关系

Selector 的候选表必须来自：

```text
DatabaseSchema.tables
```

不能仅仅根据：

```text
ProjectSemantic.tables
```

生成结果。

原因：

> Semantic 是业务描述，DatabaseSchema 才是当前数据库事实。

如果 Semantic 有：

```text
public.inventory
```

但 DatabaseSchema 没有：

```text
public.inventory
```

不能返回它。

---

# 十九、不要查询数据库

Selector：

```text
question
+
DatabaseSchema
+
ProjectSemantic
```

全部来自内存。

禁止：

```python
SELECT ...
```

禁止：

```text
SchemaExplorerService
```

内部自动调用数据库。

上层如果需要最新 Schema，自己负责先调用 SchemaExplorer。

---

# 二十、不要在这里生成 Prompt

Selector 只返回：

```text
TableSelectionResult
```

不要返回：

```text
prompt
```

不要把：

```text
SchemaSerializer
BusinessSemanticSerializer
DatabaseContextComposer
```

全部塞进 Selector。

职责必须保持：

```text
Selector
    ↓
Which tables?

Serializer
    ↓
How to describe tables?
```

---

# 二十一、可以提供一个组合辅助方法，但要克制

如果项目结构合适，可以增加：

```python
select_context(...)
```

但不是必须。

更推荐未来由上层 Application Service 组合：

```text
Question
   ↓
RelevantTableSelector
   ↓
selected tables
   ↓
SchemaSerializer(tables=...)
   +
BusinessSemanticSerializer(selected)
   ↓
DatabaseContextComposer
```

Selector 本身不要承担 Context Composer 的职责。

---

# 二十二、测试

新增：

```text
tests/test_relevant_table_selector.py
```

至少覆盖：

### 基础

1. 空 schema
2. 单表
3. 多表
4. 无 semantic
5. 有 semantic

### 匹配

6. table name 命中
7. business name 命中
8. alias 命中
9. description 命中
10. column name 命中
11. column business name 命中
12. column alias 命中

### 中文

13. 中文 substring
14. 中英文混合
15. 英文大小写不敏感

### 排序

16. score DESC
17. score 相同按 table name ASC
18. 结果稳定

### 去重

19. 多个字段命中同一表只返回一次
20. matched_terms 去重

### top_k

21. top_k=1
22. top_k=5
23. 超过候选表数量
24. 非法 top_k

### 零匹配

25. 返回空 selections
26. 不默认返回所有表

### Schema 事实约束

27. Semantic 中存在但 Schema 中不存在的表不能被返回

### 不可变

28. 不修改 DatabaseSchema
29. 不修改 ProjectSemantic
30. 相同输入多次执行结果完全一致

---

# 二十三、测试真实数据库

增加少量 DB integration test。

链路：

```text
SchemaExplorerService
        ↓
DatabaseSchema
        ↓
ProjectSemanticLoader
        ↓
ProjectSemantic
        ↓
RelevantTableSelector
```

当前只验证：

```text
public.knowledge_document
public.knowledge_chunk
```

例如问题：

```text
知识文档有哪些？
```

至少应该能够选择：

```text
public.knowledge_document
```

问题：

```text
文档有哪些知识分片？
```

能够命中：

```text
public.knowledge_chunk
```

如果当前最小 YAML 语义不足以支持测试，可以只做必要的最小语义扩充。

**不要灌入整个 WMS 业务语义。**

---

# 二十四、性能

本阶段只需要：

```text
O(number_of_tables × number_of_columns)
```

的简单扫描。

不要实现：

* ANN
* 向量数据库
* BM25
* Elasticsearch
* Redis
* 缓存

未来如果企业数据库达到数千张表，再优化。

---

# 二十五、安全

静态检查：

* 不 import SQLAlchemy
* 不 import database session
* 不读取 `.env`
* 不读取 API key
* 不调用 LLM
* 不调用 Embedding API
* 不执行 SQL

Selector 只能处理传入的：

```text
DatabaseSchema
ProjectSemantic
question
```

---

# 二十六、ADR

新增：

```text
docs/decisions/Phase 3.7.4 — ADR.md
```

记录：

### Context

大型企业数据库不适合把全部 Schema 放入 Prompt。

### Decision

增加独立：

```text
RelevantTableSelector
```

负责：

```text
Question → Candidate Tables
```

### Current Strategy

MVP 使用：

```text
table name
+
business name
+
aliases
+
description
+
column semantics
```

进行可解释匹配。

### Important Boundary

当前版本：

```text
LLM = 0
Embedding = 0
SQL = 0
```

### Future

未来可以替换为：

```text
RuleBasedSelector
EmbeddingSelector
LLMSelector
HybridSelector
```

但上层接口保持不变。

---

# 二十七、回归

执行：

```bash
pytest -q
```

以及：

```bash
RUN_DB_TESTS=1 pytest -q
```

以及：

```bash
python -m compileall backend
```

要求：

```text
新测试全部 PASS
旧测试全部 PASS
```

不得破坏：

```text
RAG
Chat
Tool
Multi-Step Tool
Schema Explorer
Schema Serializer
Business Semantic
Database Context Composer
```

---

# 二十八、数据库要求

本阶段：

```text
DB writes = 0
```

不得：

```text
INSERT
UPDATE
DELETE
ALTER
CREATE
DROP
TRUNCATE
```

测试完成后不能留下任何测试数据。

---

# 二十九、LLM 要求

必须确认：

```text
LLM calls = 0
Embedding calls = 0
Reranker calls = 0
```

---

# 三十、最终报告

完成后只汇报：

1. 新增/修改文件
2. Selector 接口
3. DTO
4. 匹配算法
5. Score 规则
6. 中文匹配策略
7. top_k 行为
8. 零匹配行为
9. 测试数量
10. 全量测试结果
11. DB 是否修改
12. LLM 是否调用
13. 当前阶段结论

**报告完成后立即停止。**

不要进入：

```text
Phase 3.7.5 SQL Validator
Phase 3.7.6 Text-to-SQL
```
