# Phase 3.8.4：Project Knowledge Context 正式项目化

## 一、目标

在 Phase 3.8.1～3.8.3 已完成的基础上，把当前全局共享的 Knowledge Store 正式纳入 Project Runtime Context。

最终形成：

```text
Project
├── DataSource
├── Capabilities
├── Semantic
├── Tools
└── Knowledge
```

目标运行链：

```text
HTTP project_id
      ↓
ProjectRegistry
      ↓
ProjectContext
      ├── DataSource
      ├── Capabilities
      ├── Semantic
      └── Knowledge
              ↓
          RAG Service
              ↓
      Project-scoped Retrieval
              ↓
          DeepSeek
```

核心要求：

> project_id 必须决定 Knowledge Context。
>
> HTTP 请求不能自行指定 knowledge_file、knowledge_id、knowledge_namespace 等方式越权切换项目知识库。

---

# 二、严格范围

## 本阶段允许修改

只允许围绕以下内容修改：

* Project Knowledge Provider
* Project → Knowledge 绑定
* RAG Retrieval 的 Project Scope
* Project Orchestrator Factory
* Project Context Provider
* AI Orchestrator 中 RAG 的项目知识上下文传递
* `/api/ai/chat` 必要的 project_id 接线
* 对应测试
* ADR

## 本阶段禁止修改

不要做：

* Agent
* LangGraph
* Memory
* RBAC
* MCP
* Streaming
* 前端
* Tool Core
* Text-to-SQL 核心
* SQL Validator
* SQL Executor
* Schema Explorer
* Business Semantic
* Reranker 算法
* Embedding 模型
* KnowledgeDocument / KnowledgeChunk 数据库结构迁移
* 删除现有 RAG 能力
* LLM 自动生成 Knowledge
* 配置中心
* 微服务拆分

尤其注意：

> 不要为了 Project Knowledge Isolation 重构整个 RAG。

优先做最小 DI / Provider 接线。

---

# 三、第一步：先阅读现有实现，暂时不要修改

首先完整阅读现有：

```text
backend/app/services/rag_service.py
backend/app/services/vector_search_service.py
backend/app/services/context_builder.py
backend/app/projects/
backend/app/services/ai_orchestrator_service.py
backend/app/services/project_orchestrator_factory.py
backend/app/api/orchestrator_chat.py
```

以及现有 RAG 测试：

```text
tests/test_rag_*.py
tests/test_rag_real_e2e.py
```

重点确认：

1. KnowledgeDocument / KnowledgeChunk 当前如何关联。
2. VectorSearchService 当前如何筛选数据。
3. RagService 当前如何调用 VectorSearch。
4. Orchestrator 当前如何创建 RagService。
5. 当前 Knowledge Store 是否完全没有 project_id。
6. 当前测试如何构造 Knowledge 数据。
7. 是否已经存在可以复用的 project/knowledge scope 抽象。

### 第一阶段要求

阅读完后先向我汇报：

```text
1. 当前 Knowledge 数据模型
2. 当前 VectorSearch 查询方式
3. 当前 RAG 调用链
4. 当前最小改造点
5. 推荐的 Project Knowledge 隔离方案
6. 将修改哪些文件
7. 不修改哪些文件
```

**汇报完成后必须停止，等待下一步指令。**

不要自行继续修改。

---

# 四、设计要求

如果现有代码没有等价抽象，则新增：

```text
backend/app/projects/knowledge_provider.py
```

提供最小 Provider 抽象。

建议接口：

```python
class ProjectKnowledgeProvider(Protocol):
    def get_scope(self, project_id: str) -> KnowledgeScope:
        ...
```

可以根据现有代码实际情况调整 DTO 名称，但必须满足：

* frozen DTO
* 无数据库连接
* 无密码
* 无 LLM
* 无 HTTP 请求
* 不接受 HTTP 传入的任意路径
* server-side project_id 决定 scope

例如：

```python
@dataclass(frozen=True)
class KnowledgeScope:
    project_id: str
    namespace: str
```

或者如果现有 Knowledge 数据模型更适合使用 document IDs / collection 名称，可以采用对应最小 DTO。

**不要提前设计复杂的 Knowledge Registry。**

---

# 五、Knowledge Provider 实现

如果没有已有等价实现，至少提供：

```text
InMemoryProjectKnowledgeProvider
```

要求：

* 显式服务器端注册
* 线程安全
* 重复注册 clear error
* 类型错误 clear error
* 未注册项目 clear error
* 不允许静默使用其他项目 Knowledge

例如：

```text
project-a → knowledge-a
project-b → knowledge-b
```

不能出现：

```text
project-a 未配置
    ↓
自动使用 project-b
```

绝对禁止。

---

# 六、兼容当前 vietnam-wms

当前系统已经存在：

```text
vietnam-wms
```

必须保持现有行为。

如果当前 Knowledge Store 是默认全局 Knowledge：

可以设计：

```text
vietnam-wms
    ↓
default/global knowledge scope
```

但这个兼容行为必须通过 Provider 显式表达。

不要在 RagService 内写：

```python
if project_id == "vietnam-wms":
    ...
```

项目特殊逻辑必须放在 Project Knowledge Provider / Factory。

---

# 七、最重要：RAG 必须真正使用 Project Scope

当前类似：

```text
RAG
 ↓
VectorSearch
 ↓
knowledge_chunk
```

需要变成：

```text
Project
 ↓
KnowledgeScope
 ↓
VectorSearch
 ↓
仅搜索该 Project Knowledge
```

例如：

```text
project-a
Knowledge:
  inventory.md
  outbound.md

project-b
Knowledge:
  production.md
  quality.md
```

那么：

```text
project-a:
  "如何做库存盘点？"
```

只能检索：

```text
inventory.md
outbound.md
```

不能命中：

```text
production.md
quality.md
```

---

# 八、数据库结构优先复用，不要轻易迁移

首先检查当前：

```text
knowledge_document
knowledge_chunk
```

是否已经存在可以表达 Project Scope 的字段。

例如：

```text
project_id
namespace
collection
source
metadata
```

如果已有字段可以复用：

> 优先直接复用。

如果完全不存在 Project Scope：

**本阶段不要立即做数据库 Migration。**

先设计最小兼容方案，并向我汇报。

原因：

当前目标是先把：

```text
Project → Knowledge Scope
```

接入运行时架构，而不是在同一阶段同时完成数据库模型重构。

如果数据库字段确实必须增加才能完成真实隔离：

必须先停止并报告：

```text
当前模型无法安全表达 Project Knowledge Scope
需要增加字段 XXX
预计影响：
...
```

等待确认后再修改。

---

# 九、VectorSearchService 改造要求

如果当前 VectorSearchService 没有 scope 参数，则进行最小扩展。

例如：

```python
search(
    query,
    *,
    top_k=...,
    knowledge_scope=...
)
```

或者：

```python
search(
    query,
    *,
    project_id=...
)
```

优先使用已有领域对象，不要把 ProjectContext 整个塞进 VectorSearch。

推荐：

```text
ProjectKnowledgeScope
```

而不是：

```text
VectorSearchService.search(project_context=...)
```

VectorSearch 只关心：

> “我要在哪个 Knowledge Scope 中搜索？”

不应该知道：

* DataSource
* Tool
* Capability
* Semantic
* LLM

---

# 十、RagService 改造要求

RagService 可以接收：

```text
knowledge_scope
```

或者：

```text
project_knowledge_provider
```

但不要让 RagService 自己读取：

```text
ProjectRegistry
```

也不要：

```python
ProjectRegistry.get(...)
```

RAG 核心应该保持可复用。

推荐依赖方向：

```text
Project Factory
      ↓
Knowledge Provider
      ↓
Knowledge Scope
      ↓
RagService
      ↓
VectorSearch
```

而不是：

```text
RagService
      ↓
ProjectRegistry
      ↓
KnowledgeProvider
```

保持依赖倒置。

---

# 十一、Project Factory

修改：

```text
backend/app/services/project_orchestrator_factory.py
```

由 Factory 完成：

```text
project_id
    ↓
ProjectRegistry
    ↓
ProjectContext
    ↓
DataSource
Capabilities
Semantic
Knowledge
Tools
    ↓
AIOrchestrator
```

最终 Factory 应该是整个 Project Runtime Context 的组装中心。

概念上：

```python
build_orchestrator_for_project(
    project_id=...,
    ...
)
```

内部组装：

```text
ProjectContext
SemanticProvider
KnowledgeProvider
ToolRegistry
Router
Orchestrator
```

不要让 API 层自己组装 Knowledge。

---

# 十二、AI Orchestrator

当前：

```text
Question
 ↓
Router
 ↓
RAG
```

改成：

```text
Question
 ↓
Router
 ↓
RAG
 ↓
Project Knowledge Scope
 ↓
VectorSearch
```

但 Orchestrator 不应该直接访问 Knowledge DB。

只负责把项目 Knowledge Context 传给 RAG。

---

# 十三、Capability 关系

Phase 3.8.2 已经有：

```text
knowledge_enabled
```

本阶段必须继续兼容。

因此：

```text
knowledge_enabled = false
```

时：

```text
Router
 ↓
不会选择 RAG
```

即使 Router 被测试 Stub 强制返回：

```text
RAG
```

Orchestrator 仍然必须：

```text
403 / capability denied
```

而且：

```text
KnowledgeProvider
VectorSearch
RagService
```

都不能真正执行。

---

# 十四、项目隔离测试

建立至少两个项目：

```text
project-a
project-b
```

使用相同或近似的 Knowledge 数据结构，但内容明显不同。

例如：

### project-a

```text
标题：库存盘点操作

内容：
project-a 专属库存盘点流程
A-ONLY-KEYWORD
```

### project-b

```text
标题：生产报工操作

内容：
project-b 专属生产报工流程
B-ONLY-KEYWORD
```

要求：

```text
project-a 查询 A-ONLY-KEYWORD
→ 能命中 A

project-a 查询 B-ONLY-KEYWORD
→ 不能命中 B

project-b 查询 B-ONLY-KEYWORD
→ 能命中 B

project-b 查询 A-ONLY-KEYWORD
→ 不能命中 A
```

---

# 十五、同一个问题双项目隔离

必须增加一个更重要的测试：

同一个问题：

```text
"系统的操作流程是什么？"
```

project-a：

```text
project-a
 ↓
Knowledge A
 ↓
A 专属答案
```

project-b：

```text
project-b
 ↓
Knowledge B
 ↓
B 专属答案
```

不能因为问题完全一样而共享检索结果。

---

# 十六、RAG Context 隔离

验证：

```text
project-a
```

最终送给 LLM 的 context：

```text
包含 A
不包含 B
```

project-b：

```text
包含 B
不包含 A
```

建议 Fake LLM / Fake RAG 捕获：

```text
retrieved chunks
context
project_id / scope
```

不要为了这个测试调用真实 DeepSeek。

---

# 十七、API E2E

保持 API：

```http
POST /api/ai/chat
```

请求结构：

```json
{
  "project_id": "project-a",
  "question": "系统的操作流程是什么？"
}
```

必须验证：

```text
project-a → Knowledge A
project-b → Knowledge B
```

同时：

```json
{
  "project_id": "project-a",
  "question": "...",
  "knowledge_scope": "project-b"
}
```

这类 HTTP 注入字段：

```text
knowledge_scope
knowledge_id
knowledge_file
knowledge_namespace
knowledge_project_id
```

都必须被忽略。

最终仍由：

```text
project_id
```

决定 Knowledge Scope。

---

# 十八、安全测试

至少覆盖：

### 1. Unknown project

```text
project-x
```

结果：

```text
404
```

不能：

```text
fallback → vietnam-wms
```

### 2. 未注册 Knowledge

如果使用显式 InMemory Provider：

```text
project-a 已注册
project-a knowledge 未注册
```

必须：

```text
503
```

而不是使用其他项目 Knowledge。

### 3. HTTP 注入

请求：

```json
{
  "project_id": "project-a",
  "question": "...",
  "knowledge_project_id": "project-b",
  "knowledge_namespace": "project-b",
  "knowledge_file": "project-b.md"
}
```

最终必须仍然：

```text
project-a Knowledge
```

### 4. Cross-project retrieval

Project A：

```text
只能看到 A
```

Project B：

```text
只能看到 B
```

---

# 十九、不要改变 Reranker

如果 Reranker 当前链路：

```text
VectorSearch
 ↓
Candidate Chunks
 ↓
Reranker
 ↓
Top K
```

保持不变。

只需要保证：

```text
VectorSearch
```

在进入 Reranker 之前已经完成 Project Scope 隔离。

正确：

```text
Project Scope
 ↓
Vector Search
 ↓
Candidate A
 ↓
Reranker
 ↓
Top A
```

错误：

```text
Vector Search 全库
 ↓
Reranker
 ↓
再尝试判断 Project
```

---

# 二十、不要修改 Knowledge 内容模型，除非确实必要

本阶段重点：

```text
Project → Knowledge Scope
```

而不是：

```text
Knowledge Model 大重构
```

如果发现数据库模型确实无法实现安全隔离：

立即停止并汇报，不要自行做 Migration。

---

# 二十一、测试要求

新增：

```text
tests/test_project_knowledge_provider.py
tests/test_project_knowledge_isolation.py
```

根据现有测试结构，可以增加：

```text
tests/test_rag_project_scope.py
```

至少覆盖：

### Provider

```text
注册
获取
重复注册
未注册
类型错误
线程安全
```

### Vector Search

```text
scope A → only A
scope B → only B
```

### RAG

```text
A → A context
B → B context
```

### Capability

```text
knowledge_enabled=false
→ no RAG execution
→ no VectorSearch
→ no KnowledgeProvider execution
```

### API

```text
project-a → A
project-b → B
unknown → 404
injection → ignored
```

---

# 二十二、回归测试

完成后执行：

```powershell
pytest -q
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

Windows PowerShell 不要使用：

```bash
RUN_DB_TESTS=1 pytest -q
```

还要执行：

```powershell
python -m compileall backend
```

如果项目已有 lint 命令，继续执行现有 lint。

---

# 二十三、ADR

新增：

```text
docs/decisions/Phase/Phase 3.8.4：Project Knowledge Context 实施 ADR.md
```

至少记录：

1. 为什么要 Project Knowledge Scope
2. 当前旧架构的问题
3. Provider 设计
4. Project → Knowledge 的绑定方式
5. VectorSearch 如何实现 Scope
6. 为什么不让 RagService 读取 ProjectRegistry
7. Capability 与 Knowledge 的关系
8. 安全隔离
9. 向后兼容策略
10. 当前限制
11. 后续是否需要 Knowledge DB Migration

---

# 二十四、完成后最终汇报格式

完成后只汇报以下内容：

## 1. 修改文件

```text
文件：
修改内容：
```

## 2. 新增文件

```text
文件：
用途：
```

## 3. Project Knowledge Provider

说明：

```text
Project ID
    ↓
Knowledge Provider
    ↓
Knowledge Scope
```

## 4. RAG 链路

说明最终：

```text
Project
 ↓
Knowledge Scope
 ↓
VectorSearch
 ↓
Reranker
 ↓
Context
 ↓
LLM
```

## 5. 项目隔离测试

说明：

```text
project-a → A
project-b → B
```

以及测试数量。

## 6. API E2E

说明：

```text
project-a → Knowledge A
project-b → Knowledge B
unknown → 404
injection → ignored
```

## 7. pytest

报告：

```text
pytest -q
```

结果。

## 8. DB pytest

报告：

```text
RUN_DB_TESTS=1 python -m pytest -q
```

结果。

## 9. 安全测试

说明：

* Cross-project Knowledge 是否被阻断
* HTTP Knowledge 注入是否被阻断
* unknown project 是否 404
* knowledge 未配置是否不会 fallback 到其他项目

## 10. 已知限制

只写真实存在的限制。

---

# 二十五、最重要的执行纪律

这是一个小阶段。

**不要顺手开始 Phase 3.8.5。**

完成本阶段后：

> 停止。

不要自行继续做：

* Knowledge Migration
* Agent
* Memory
* MCP
* RBAC
* Streaming
* Multi-Agent
* Config Center

等待下一条指令。
继续执行 Phase 3.8.4，按你刚才的现状分析进入实现阶段。

在原 Phase 3.8.4 任务书基础上，增加并严格遵守以下两个要求：

## 1. Global Scope 必须显式表达，不能用“不加过滤”表示

不要设计成：

```text
global scope
    ↓
VectorSearch 不加 WHERE
    ↓
搜索整个 Knowledge Store
```

这样未来 project-a / project-b 的知识会发生跨项目泄漏。

统一采用显式 scope：

```text
project-a → project_id = "project-a"
project-b → project_id = "project-b"
global     → project_id = "__global__"
```

Knowledge Scope 建议：

```python
@dataclass(frozen=True)
class ProjectKnowledgeScope:
    project_id: str
    namespace: str
```

其中：

```text
project-a → namespace="project-a"
project-b → namespace="project-b"
global     → namespace="__global__"
```

VectorSearch 在有明确 scope 时必须进行明确过滤，不能因为 scope 是 global 就退化成全库查询。

---

## 2. 必须兼容现有 vietnam-wms 历史 Knowledge

当前已有 Knowledge 数据可能没有：

```text
meta_data.project_id
```

因此不能简单把：

```text
vietnam-wms → "__global__"
```

然后只查询：

```sql
meta_data->>'project_id' = '__global__'
```

否则历史 Knowledge 会全部无法检索。

请采用明确的 legacy 兼容策略。

建议：

```text
历史 Knowledge：
meta_data.project_id IS NULL
        ↓
视为 legacy/global knowledge

明确标记的公共 Knowledge：
meta_data.project_id = "__global__"

project-a：
meta_data.project_id = "project-a"

project-b：
meta_data.project_id = "project-b"
```

对于当前默认 `vietnam-wms`，必须继续能够检索历史 Knowledge。

同时必须保证：

```text
project-a
```

不能因为 legacy 兼容逻辑而看到：

```text
project-b
```

以及：

```text
project-b
```

不能看到：

```text
project-a
```

---

## 3. 推荐的最终过滤语义

请以安全隔离优先，明确实现并测试以下规则：

### vietnam-wms legacy 兼容

```text
vietnam-wms
    ↓
允许：
    meta_data.project_id IS NULL
    OR
    meta_data.project_id = "__global__"
    OR
    meta_data.project_id = "vietnam-wms"
```

### 普通项目

例如 project-a：

```text
project-a
    ↓
只允许：
    meta_data.project_id = "project-a"
    OR
    meta_data.project_id = "__global__"
```

project-b 同理。

也就是说：

```text
global knowledge
    → 所有项目可以共享

legacy knowledge
    → 仅 vietnam-wms 兼容

project-a knowledge
    → 仅 project-a

project-b knowledge
    → 仅 project-b
```

这样既兼容旧数据，又不会产生 project-a ↔ project-b 泄漏。

如果你发现当前产品语义并不适合“global knowledge 对所有项目共享”，请不要自行改变语义，先停止并汇报。

---

## 4. 测试必须覆盖这组核心隔离矩阵

至少建立：

```text
Knowledge A
meta_data={"project_id": "project-a"}

Knowledge B
meta_data={"project_id": "project-b"}

Global Knowledge
meta_data={"project_id": "__global__"}

Legacy Knowledge
meta_data={}
```

验证：

| 请求项目        |  A |  B | Global | Legacy |
| ----------- | -: | -: | -----: | -----: |
| project-a   |  ✅ |  ❌ |      ✅ |      ❌ |
| project-b   |  ❌ |  ✅ |      ✅ |      ❌ |
| vietnam-wms |  ❌ |  ❌ |      ✅ |      ✅ |

其中：

```text
project-a 查询 B 专属关键词 → 不能命中
project-b 查询 A 专属关键词 → 不能命中
```

这是本阶段最重要的安全测试。

---

## 5. Provider 要保持简单

继续使用：

```text
ProjectKnowledgeProvider
InMemoryProjectKnowledgeProvider
DefaultProjectKnowledgeProvider
```

但不要增加复杂 Registry / Config Center。

Provider 只负责：

```text
project_id
    ↓
ProjectKnowledgeScope
```

不要让 Provider 直接操作数据库。

---

## 6. 继续遵守原阶段边界

不要修改：

```text
KnowledgeDocument ORM
KnowledgeChunk ORM
Embedding
Reranker
ContextBuilder
SQL Validator
SQL Executor
Semantic
Tool Framework
Agent
MCP
Memory
RBAC
Streaming
```

除非为了 Project Scope 隔离发现绝对必要的结构性问题。

如果确实需要数据库 Migration：

> 立即停止并汇报，不要自行 Migration。

---

确认以上要求后，**现在开始正式实现 Phase 3.8.4**。

完成后严格按照原任务书第二十四节格式汇报，并停止，不要进入 Phase 3.8.5。
