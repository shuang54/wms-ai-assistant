你现在只执行 **Phase 3.8.3：Project Semantic Context 正式项目化**。

## 一、目标

Phase 3.8.1 已实现：

```text
project_id
 ↓
ProjectRegistry
 ↓
ProjectContext
 ↓
DataSource
 ↓
DatabaseEngine
```

Phase 3.8.2 已实现：

```text
Project
 ├── DataSource
 └── Capabilities
      ├── Tools
      ├── RAG
      └── Text-to-SQL
```

本阶段继续解决：

> 当前 Business Semantic 虽然已经存在，但还没有成为 Project Runtime Context 的正式组成部分。

目标：

```text
Project
 ├── DataSource
 ├── Capabilities
 └── Semantic
       ↓
Schema + Semantic
       ↓
Text-to-SQL Context
```

最终实现：

```text
project-a
 ↓
Semantic A
 ↓
Text-to-SQL Context A

project-b
 ↓
Semantic B
 ↓
Text-to-SQL Context B
```

同一套 AI Core 不变。

---

# 二、严格范围

本阶段只做：

**Project → Semantic Context 的正式绑定与运行时切换。**

禁止：

1. 不做 Agent
2. 不做 LangGraph
3. 不做 Multi-Agent
4. 不做 Memory
5. 不做 RBAC
6. 不做用户权限
7. 不做 MCP
8. 不做 Streaming
9. 不做前端
10. 不做 Knowledge 项目隔离
11. 不做数据库迁移
12. 不修改 RAG / Reranker
13. 不修改 Tool Framework 核心
14. 不修改 SQL Validator
15. 不修改 SQL Executor 安全逻辑
16. 不重新设计 Semantic 数据模型，除非现有模型确实无法支持项目绑定
17. 不让 LLM 自动生成或修改 Semantic
18. 不引入配置中心
19. 不引入 Redis
20. 不做热加载

如果发现必须扩大范围：

**立即停止并报告原因。**

---

# 三、第一步：先阅读，不要修改

重点阅读：

```text
backend/app/projects/semantic.py
backend/app/projects/semantic_loader.py
backend/app/projects/semantic/
backend/app/services/business_semantic_serializer.py
backend/app/services/database_context_composer.py

backend/app/projects/registry.py
backend/app/projects/models.py
backend/app/services/project_orchestrator_factory.py

backend/app/services/relevant_table_selector.py
backend/app/services/text_to_sql_service.py
backend/app/services/ai_orchestrator_service.py

tests/
docs/decisions/
```

特别确认：

1. `ProjectSemantic`
2. `TableSemantic`
3. `ColumnSemantic`
4. `BusinessRelationship`
5. `semantic_loader`
6. 当前 `vietnam-wms.yaml`
7. `BusinessSemanticSerializer`
8. `DatabaseContextComposer`
9. 当前 Semantic 是如何传给 Text-to-SQL 的
10. 当前 Semantic 是否已经根据 project_id 加载

**第一步完成后先报告现状，不要直接修改。**

---

# 四、核心设计

当前已有 Semantic DTO：

```python
ProjectSemantic
```

优先复用，不要创建：

```text
ProjectSemanticV2
ProjectBusinessSemantic
ProjectSemanticContext
```

等重复模型。

本阶段增加一个项目级 Semantic Provider。

建议：

```python
class ProjectSemanticProvider(Protocol):
    def get(self, project_id: str) -> ProjectSemantic:
        ...
```

然后实现：

```python
InMemoryProjectSemanticProvider
```

如果现有 `semantic_loader.py` 已经具备类似能力，则进行最小改造并复用。

---

# 五、Semantic 必须成为 Project Registry 的一部分

最终概念模型：

```text
ProjectRegistration
 ├── context
 ├── schema_name
 ├── capabilities
 └── semantic
```

但是：

**不要直接把整个 `ProjectSemantic` 强行塞进 `ProjectRegistration`，如果这样会造成职责混乱。**

推荐：

```text
ProjectRegistry
        ↓
ProjectRegistration
        ↓
semantic_key / semantic provider
```

或者：

```text
ProjectContext
        ↓
ProjectSemanticProvider
        ↓
ProjectSemantic
```

选择最符合现有代码结构的方式。

核心要求只有一个：

> `project_id` 必须唯一决定 Semantic。

---

# 六、服务器端控制

HTTP 请求：

```json
{
  "project_id": "project-a",
  "question": "查询库存"
}
```

只能选择：

```text
project-a
```

对应的服务器端 Semantic。

禁止：

```json
{
  "project_id": "project-a",
  "semantic_file": "project-b.yaml"
}
```

也禁止：

```json
{
  "project_id": "project-a",
  "semantic": {...}
}
```

用户不能直接提交 Semantic。

---

# 七、至少准备两个 Semantic

测试必须准备：

```text
project-a
project-b
```

两套明显不同的 Semantic。

例如可以继续使用测试数据库：

```text
project_a.inventory
project_b.inventory
```

但 Semantic 描述故意不同。

例如：

### Project A

```text
inventory
业务名称：库存
material_code
业务名称：物料编码
qty
业务名称：库存数量
```

### Project B

可以使用不同业务名称或别名，例如：

```text
inventory
业务名称：可用库存
material_code
业务名称：产品编号
qty
业务名称：可用数量
```

目的是证明：

> 同一个数据库表结构，也可以因为 Project 不同而产生不同业务语义。

---

# 八、Semantic Serializer

继续复用：

```text
BusinessSemanticSerializer
```

不要重新实现一套 Serializer。

要求：

```text
project-a
 ↓
ProjectSemantic A
 ↓
BusinessSemanticSerializer
 ↓
Semantic Context A
```

以及：

```text
project-b
 ↓
ProjectSemantic B
 ↓
BusinessSemanticSerializer
 ↓
Semantic Context B
```

---

# 九、DatabaseContextComposer

继续复用：

```text
DatabaseContextComposer
```

最终：

```text
Project Context
+
Database Schema
+
Project Semantic
```

组合成：

```text
Text-to-SQL Database Context
```

必须保证：

```text
project-a
Schema A + Semantic A

project-b
Schema B + Semantic B
```

不能出现：

```text
Schema A + Semantic B
```

或者：

```text
Schema B + Semantic A
```

---

# 十、Text-to-SQL 集成

不要修改 TextToSQLGenerator 的核心接口。

现有：

```python
generate(
    question,
    database_context=...,
    allowed_tables=...,
    schema=...,
)
```

继续使用。

只改变：

```text
database_context
```

的来源。

正确：

```text
project_id
 ↓
ProjectSemanticProvider
 ↓
ProjectSemantic
 ↓
DatabaseContextComposer
 ↓
TextToSQLService
```

---

# 十一、Relevant Table Selector

继续使用：

```text
RelevantTableSelector
```

但必须使用当前 Project Semantic。

例如：

```text
project-a
```

有 alias：

```text
库存
```

而：

```text
project-b
```

使用：

```text
可用库存
```

验证：

```text
Project A
"库存"
→ 能匹配 inventory

Project B
"可用库存"
→ 能匹配 inventory
```

同时验证错误语义不会错误跨项目复用。

---

# 十二、Orchestrator

Orchestrator 不应该自己读取 YAML。

也不应该自己：

```text
open(...)
yaml.safe_load(...)
```

应该：

```text
ProjectContext
 ↓
ProjectSemanticProvider
 ↓
ProjectSemantic
```

保持 Orchestrator 只负责 orchestration。

---

# 十三、Factory

重点修改：

```text
backend/app/services/project_orchestrator_factory.py
```

Factory 根据：

```text
project_id
```

组装：

```text
ProjectContext
ProjectCapabilities
ProjectSemantic
DataSource
ToolRegistry
Router
Orchestrator
```

最终：

```text
Project Factory
      ↓
┌───────────────────────┐
│ ProjectContext        │
│ DataSource            │
│ Capabilities          │
│ Semantic              │
└───────────────────────┘
```

不要让 API 层知道 Semantic 文件。

---

# 十四、API

保持：

```http
POST /api/ai/chat
```

现有请求协议不变：

```json
{
  "project_id": "project-a",
  "question": "..."
}
```

不要增加：

```text
semantic_id
semantic_file
semantic_config
```

API 只负责：

```text
project_id
question
```

---

# 十五、安全测试

必须验证：

### 1. Semantic 越权

Project A：

```text
project-a
```

不能请求：

```text
project-b semantic
```

---

### 2. HTTP 注入

请求体增加：

```json
{
  "semantic_file": "project-b.yaml"
}
```

必须被忽略 / 拒绝。

---

### 3. Project Registry 控制

只有：

```text
ProjectRegistry
```

能够决定 Project → Semantic。

---

### 4. SQL 隔离

继续验证：

```text
Project A
Schema A + Semantic A

Project B
Schema B + Semantic B
```

不存在：

```text
Schema A + Semantic B
```

---

# 十六、测试

至少增加：

## 16.1 Semantic Provider

验证：

```text
known project → semantic
unknown project → clear error
```

---

## 16.2 Semantic isolation

验证：

```text
project-a → semantic-a
project-b → semantic-b
```

---

## 16.3 Serializer

验证：

```text
semantic-a
→ only A semantic

semantic-b
→ only B semantic
```

---

## 16.4 Relevant Table Selector

验证不同项目的：

```text
business_name
alias
description
```

能够影响匹配。

---

## 16.5 Context Composer

验证：

```text
Schema A + Semantic A
Schema B + Semantic B
```

并且不能串。

---

## 16.6 Text-to-SQL

使用 Fake Generator。

记录传入的：

```text
database_context
allowed_tables
schema
```

验证：

```text
project-a
→ Semantic A

project-b
→ Semantic B
```

不要为了证明 Semantic 切换而强制使用真实 LLM。

---

# 十七、真实 API E2E

至少验证：

```text
POST /api/ai/chat
```

Project A：

```json
{
  "project_id": "project-a",
  "question": "库存最多的物料有哪些？"
}
```

Project B：

```json
{
  "project_id": "project-b",
  "question": "可用库存最多的产品有哪些？"
}
```

验证：

```text
A → Semantic A
B → Semantic B
```

如果真实 LLM 测试成本较高，可以使用 Fake Generator 完成隔离验证。

不要为了本阶段强制增加真实 LLM 调用次数。

---

# 十八、回归测试

必须保证：

```bash
pytest -q
```

通过。

然后：

```bash
RUN_DB_TESTS=1 pytest -q
```

通过。

最后：

```bash
python -m compileall backend
```

通过。

检查 LSP / lint：

```text
0 errors
```

数据库：

```text
无测试残留
```

---

# 十九、不要改变 RAG

本阶段明确：

```text
RAG Knowledge Store
```

仍然全局共享。

不要增加：

```text
knowledge_project_id
```

不要修改：

```text
knowledge_document
knowledge_chunk
```

不要修改 Reranker。

这部分以后单独做：

```text
Project Knowledge Isolation
```

---

# 二十、文档

新增：

```text
docs/decisions/Phase/Phase 3.8.3：Project Semantic Context 正式项目化.md
```

记录：

1. 当前 Semantic 架构
2. 为什么需要 Project Semantic Provider
3. Project → Semantic 映射
4. Serializer
5. Context Composer
6. Relevant Table Selector
7. Text-to-SQL 集成
8. Semantic 隔离
9. 安全测试
10. API E2E
11. 测试结果
12. 已知限制

---

# 二十一、最终验收

必须证明：

```text
project-a
 ├── DataSource A
 ├── Capability A
 └── Semantic A
```

以及：

```text
project-b
 ├── DataSource B
 ├── Capability B
 └── Semantic B
```

并且：

```text
project-a
    ↓
Schema A + Semantic A

project-b
    ↓
Schema B + Semantic B
```

绝对不能：

```text
Schema A + Semantic B
```

或：

```text
Schema B + Semantic A
```

---

# 二十二、最终汇报格式

完成后只报告：

```text
1. 修改文件
2. 新增文件
3. ProjectSemanticProvider 如何实现
4. Project → Semantic 如何绑定
5. Serializer 如何复用
6. ContextComposer 如何复用
7. RelevantTableSelector 是否使用项目 Semantic
8. Text-to-SQL 是否拿到正确 Semantic
9. project-a / project-b 隔离测试结果
10. API E2E 结果
11. pytest 结果
12. DB pytest 结果
13. 安全测试结果
14. 已知限制
```

**Phase 3.8.3 完成后立即停止。**

不要继续：

* Phase 3.8.4
* Project Knowledge Isolation
* Agent
* LangGraph
* Memory
* RBAC
* Multi-Agent
* MCP
* Streaming

等待下一步指令。
