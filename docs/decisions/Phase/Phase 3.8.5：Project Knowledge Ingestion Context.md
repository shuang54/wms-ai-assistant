# Phase 3.8.5：Project Knowledge Ingestion Context

## 一、目标

在 Phase 3.8.4 已完成 Project Knowledge 检索隔离的基础上，本阶段只解决：

> **Knowledge Ingestion 在写入知识库时自动携带项目上下文 `project_id`。**

最终形成：

```text
project_id
    ↓
ProjectKnowledgeProvider / ProjectKnowledgeScope
    ↓
Knowledge Ingestion
    ↓
knowledge_document.meta_data.project_id
    ↓
knowledge_chunk
    ↓
Project-scoped VectorSearch
```

要求：

* 不改变现有 Knowledge ORM 模型；
* 不增加数据库 Migration；
* 继续复用 `meta_data JSONB`；
* 不修改 Embedding / Reranker / ContextBuilder；
* 不修改 Text-to-SQL / Tool / Semantic；
* 不进入 Agent / LangGraph；
* 保持现有旧调用兼容；
* 本阶段完成后立即停止，不进入 Phase 3.8.6。

---

# 二、开始前必须先检查现有实现

先不要修改代码。

阅读并总结以下现有实现：

```text
backend/app/services/knowledge_ingestion_service.py
backend/app/services/vector_search_service.py
backend/app/projects/knowledge_provider.py
backend/app/projects/registry.py
backend/app/projects/context.py
backend/app/models/
tests/
```

重点回答：

1. 当前 KnowledgeIngestionService 的入口方法是什么；
2. document 是在哪里创建的；
3. document.meta_data 是在哪里赋值的；
4. chunk 是在哪里创建的；
5. ingestion 是否存在文件/Markdown/TXT 批量导入入口；
6. 当前 ingestion 测试覆盖哪些路径；
7. 是否存在不经过 KnowledgeIngestionService 直接创建 knowledge_document 的测试或代码；
8. 是否已有 project_id 参数；
9. 是否已有类似 semantic/provider 的项目上下文注入方式。

检查完成后先输出：

```text
Implementation reconnaissance complete.

Current ingestion entry:
Document creation:
Metadata assignment:
Chunk creation:
Existing project context:
Existing tests:
Potential compatibility risks:
Recommended minimal change:
```

然后再开始实施。

---

# 三、本阶段核心设计

## 3.1 project_id 必须由服务端调用上下文决定

不要允许 HTTP 请求直接传：

```text
meta_data.project_id
knowledge_namespace
knowledge_scope
```

客户端不能决定最终 Knowledge scope。

推荐：

```text
API / Application
      ↓
project_id
      ↓
KnowledgeIngestionService
      ↓
ProjectKnowledgeProvider
      ↓
ProjectKnowledgeScope
      ↓
meta_data.project_id
```

---

# 四、ProjectKnowledgeScope 复用 Phase 3.8.4

不要重新定义第二套 Scope。

复用：

```python
ProjectKnowledgeScope
```

以及：

```python
ProjectKnowledgeProvider
```

例如：

```python
scope = provider.get_scope(project_id)
```

得到：

```text
project-a
    namespace = "project-a"

project-b
    namespace = "project-b"

vietnam-wms
    namespace = "vietnam-wms"
    includes_global = True
    includes_legacy = True
```

注意：

**Ingestion 写入时只写 `scope.namespace`。**

不要把：

```text
includes_global
includes_legacy
```

写入 Knowledge metadata。

它们是检索策略，不是文档归属。

---

# 五、写入规则

Knowledge Document 的：

```python
meta_data
```

必须最终包含：

```json
{
  "project_id": "project-a"
}
```

例如：

```json
{
  "project_id": "project-a",
  "source": "wms-manual",
  "category": "warehouse"
}
```

如果原来已经有 metadata：

```json
{
  "source": "wms-manual"
}
```

则应该合并：

```json
{
  "source": "wms-manual",
  "project_id": "project-a"
}
```

但：

> `project_id` 必须由 ingestion service 强制覆盖，而不是信任调用方 metadata 中已有的值。

例如调用方恶意传：

```json
{
  "project_id": "project-b"
}
```

实际项目是：

```text
project-a
```

最终必须写：

```json
{
  "project_id": "project-a"
}
```

不能出现：

```text
project-b
```

---

# 六、是否允许 project_id=None

需要根据现有 ingestion API 兼容性做最小设计。

推荐：

### 新的项目化入口

如果明确使用：

```python
project_id="project-a"
```

则必须写入：

```text
meta_data.project_id = "project-a"
```

### 历史直接调用

如果现有 KnowledgeIngestionService 已经存在大量：

```python
ingest(...)
```

且没有 project_id：

不要贸然破坏旧测试/旧调用。

可以保留：

```python
project_id: str | None = None
```

兼容旧入口。

但：

```text
project_id=None
```

不能伪装成：

```text
__global__
```

也不能自动变成：

```text
vietnam-wms
```

否则会产生错误归属。

旧入口如果继续写入无 project_id：

```json
{}
```

则按照 Phase 3.8.4 的规则，它属于 legacy，只能被 vietnam-wms 检索。

这是安全优先的行为。

---

# 七、Provider 注入方式

优先采用依赖注入，不要在 KnowledgeIngestionService 内部硬编码：

```python
get_default_project_knowledge_provider()
```

推荐类似：

```python
class KnowledgeIngestionService:
    def __init__(
        self,
        ...,
        knowledge_provider: ProjectKnowledgeProvider | None = None,
    ):
        ...
```

如果现有项目已经有成熟的 Provider 注入模式，则保持现有风格。

默认情况下可以使用：

```python
get_default_project_knowledge_provider()
```

但要保证：

* 测试可以注入 Fake/InMemory Provider；
* 不需要修改全局 singleton；
* 不从 HTTP 参数构造 Provider；
* Provider 不连接数据库；
* Provider 不处理 Knowledge ORM。

---

# 八、Metadata 合并规则

实现一个清晰的内部方法，例如：

```python
_build_document_metadata(
    metadata,
    project_id,
)
```

要求：

### project_id 有值

最终：

```python
metadata["project_id"] = scope.namespace
```

### metadata 原本没有 project_id

正常写入。

### metadata 原本有错误 project_id

覆盖。

### metadata 不是 dict

按照现有项目的数据契约处理。

不要为了本阶段重新设计 metadata 类型体系。

---

# 九、Document 与 Chunk 的归属

本阶段只要求：

```text
knowledge_document.meta_data.project_id
```

作为唯一 Knowledge scope 来源。

不要给：

```text
knowledge_chunk.meta_data.project_id
```

增加新的强制字段，除非现有架构已经要求 chunk metadata 同步。

原因：

Phase 3.8.4 已经确定：

```text
chunk
  ↓
document
  ↓
document.meta_data.project_id
```

Document-level scope 足够。

VectorSearch 继续通过：

```sql
knowledge_chunk
JOIN knowledge_document
```

进行项目过滤。

不要为了 ingestion 冗余数据。

---

# 十、幂等 / 更新行为

仔细检查现有：

```text
content_hash
document update
duplicate document
re-ingestion
```

行为。

项目 scope 必须参与文档身份判断的风险要先分析。

例如：

```text
project-a + manual.md
project-b + manual.md
```

允许成为两个独立项目知识。

不能因为：

```text
file_name
content_hash
```

相同就导致：

```text
project-a
```

覆盖：

```text
project-b
```

但是：

**不要本阶段重新设计完整 Knowledge 唯一键。**

如果现有 ingestion 的幂等逻辑会导致跨项目冲突：

先测试并报告具体冲突。

只有确实需要才能做最小修改。

---

# 十一、必须增加的测试

新增：

```text
tests/test_project_knowledge_ingestion.py
```

以及必要的 DB integration 测试。

至少覆盖以下场景。

## Case 1：project-a 正常写入

输入：

```text
project_id = "project-a"
```

检查：

```text
knowledge_document.meta_data.project_id == "project-a"
```

---

## Case 2：project-b 正常写入

检查：

```text
project_id == "project-b"
```

---

## Case 3：恶意 metadata 注入

输入：

```python
metadata = {
    "project_id": "project-b",
    "source": "test",
}
```

实际：

```text
project_id = "project-a"
```

最终必须：

```json
{
  "project_id": "project-a",
  "source": "test"
}
```

---

## Case 4：global

如果系统允许显式 global ingestion：

```text
project_id / namespace = "__global__"
```

最终：

```json
{
  "project_id": "__global__"
}
```

必须显式设置。

绝对不能通过：

```text
None
```

表示 global。

---

## Case 5：legacy compatibility

历史：

```python
project_id=None
```

保持旧行为：

```text
meta_data.project_id 不存在
```

不能自动写：

```text
vietnam-wms
```

不能自动写：

```text
__global__
```

---

# 十二、最重要的 DB 隔离 E2E

建立最小测试数据：

```text
A document:
project_id = "project-a"
content = "A-ONLY-KEYWORD"

B document:
project_id = "project-b"
content = "B-ONLY-KEYWORD"

Global document:
project_id = "__global__"
content = "GLOBAL-KEYWORD"

Legacy document:
project_id absent
content = "LEGACY-KEYWORD"
```

然后验证：

```text
project-a retrieval
    A = yes
    B = no
    Global = yes
    Legacy = no

project-b retrieval
    A = no
    B = yes
    Global = yes
    Legacy = no

vietnam-wms retrieval
    A = no
    B = no
    Global = yes
    Legacy = yes
```

重点：

本阶段新增的是 **写入链路**。

因此至少要验证：

```text
ingest(project-a)
      ↓
document metadata project-a
      ↓
VectorSearch(project-a)
      ↓
只能召回 A + Global
```

而不是只检查数据库字段。

---

# 十三、API E2E

如果当前已经存在项目化 Knowledge ingestion API：

则增加：

```text
POST ingestion(project_id="project-a")
```

之后：

```text
POST /api/ai/chat
project_id="project-a"
```

必须能够检索刚刚写入的知识。

如果当前没有 ingestion API：

**不要为了本阶段新增一个新的 HTTP API。**

直接测试 Application/Service 层。

---

# 十四、安全要求

必须验证：

### 1. HTTP 不能决定 Knowledge project

如果 API request model 收到：

```json
{
  "project_id": "project-a",
  "knowledge_project_id": "project-b",
  "knowledge_scope": "project-b",
  "knowledge_namespace": "project-b"
}
```

最终仍然只能使用服务器端：

```text
ProjectKnowledgeProvider(project-a)
```

得到：

```text
project-a
```

---

### 2. Provider 未配置

如果：

```text
project-x
```

没有 Knowledge Provider 配置：

不能：

```text
fallback → project-a
fallback → vietnam-wms
fallback → global
```

应该明确失败。

---

### 3. knowledge_enabled=False

保持 Phase 3.8.2 行为：

```text
403
Provider = 0
RAG = 0
LLM = 0
```

不要因为 ingestion 改造破坏 capability 防线。

---

# 十五、不要修改

本阶段明确禁止修改：

```text
Knowledge ORM model
database migration
Embedding
Reranker
ContextBuilder
VectorSearch filtering semantics
SQL Validator
SQL Executor
Text-to-SQL
Tool
Semantic
AI Router
AI capability
ProjectRegistry
ProjectContext
```

其中：

**VectorSearch 可以只用于验证现有隔离，不修改 Phase 3.8.4 已完成的过滤逻辑。**

---

# 十六、兼容性要求

Phase 3.8.4 已经验证：

```text
1267 passed / 220 skipped
DB 1449 passed / 38 skipped
```

本阶段完成后：

```text
default pytest
DB pytest
compileall
lint
```

全部必须通过。

不能为了新增 project ingestion 而破坏：

* 旧 RAG
* 旧 ingestion
* 旧 Mock
* 旧 API
* 旧测试

---

# 十七、实现原则

严格遵循：

> 最小修改。

不要：

* 重构 KnowledgeIngestionService；
* 重写 metadata 模型；
* 新增 repository 层；
* 新增事件系统；
* 新增消息队列；
* 新增缓存；
* 新增配置中心；
* 新增 migration；
* 新增微服务；
* 引入 LangGraph；
* 引入 Agent；
* 顺手优化无关代码。

---

# 十八、完成后的最终汇报格式

严格按照 Phase 3.8.4 的格式汇报：

1. 修改文件
2. 新增文件
3. Project Knowledge Ingestion 设计
4. Project → Provider → Scope → Metadata 写入链路
5. Metadata 覆盖 / 注入安全
6. Project Knowledge 隔离测试
7. DB / API E2E
8. pytest
9. 安全测试
10. 已知限制
11. 本阶段停止

特别说明：

```text
Phase 3.8.5 到此停止。
不进入 Phase 3.8.6。
```

不要自行继续下一阶段。

---

# 附录：实施结果（Phase 3.8.5 完成）

> 以下为本阶段实际实施记录（侦察结论、修改清单与回归结果）。

## A.1 侦察结论

- 唯一写入口：`KnowledgeIngestionService.ingest_one(file_path)`（此前无
  `project_id` / `metadata` 参数）；lifecycle 另有 `update_document /
  delete_document / get_document / list_documents`。无批量目录导入、
  无 HTTP ingestion API。
- `meta_data` 此前硬编码 `{"source_type": parser.file_type}`，调用方
  无法传入任何 metadata。
- `content_hash` 有 **DB UNIQUE 约束** + 全局查重：project-a 与
  project-b 导入相同内容必然冲突（任务书 §十要求允许共存）——
  属必须最小修复的真实冲突，见 A.3。

## A.2 修改文件

```text
backend/app/services/knowledge_ingestion_service.py
    - ingest_one(file_path, *, project_id=None, metadata=None)
    - 构造器新增 knowledge_provider 注入（复用 Phase 3.8.4 协议，
      懒加载默认 Provider；project_id=None 不触碰 Provider）
    - _resolve_knowledge_namespace：project_id → scope.namespace
      （未注册 → KnowledgeIngestionProjectScopeError，绝不 fallback）
    - _build_document_metadata：合并 metadata、剥离并强制覆盖 project_id、
      source_type 保持 parser 权威；非 dict → clear error
    - _document_content_hash：namespace 参与 hash（见 A.3）
    - update_document：以文档自身 meta_data.project_id 计算 hash
      （归属保持，unchanged 检测不回归）
```

## A.3 content_hash 命名空间化（§十 冲突的最小修复）

```text
project_id=None   → sha256(content)                （旧行为逐字节兼容）
project_id 有值    → sha256(namespace + b"\0" + content)
```

- 满足 `content_hash` UNIQUE 约束的同时实现 per-project 去重：
  project-a 与 project-b 的相同内容是两份独立文档；
- 项目内重导相同内容 → `already_exists`（指向自己的文档，不重复调
  Embedding）；
- chunk 级 hash 不变（chunk 身份与项目无关）；
- 零数据库 Migration。

## A.4 新增文件

```text
tests/test_project_knowledge_ingestion.py
    单元 14 项：hash 命名空间化 / metadata 合并与注入剥离 /
    Provider 解析（未注册 clear error、None 时 0 次调用）
    DB   17 项：Case 1-5 / chunk meta 不污染 / 跨项目去重共存 /
    项目内幂等 / update 归属保持 / ingest→VectorSearch 隔离矩阵
```

## A.5 回归结果

```text
pytest -q                          → 1281 passed / 237 skipped / 0 failed
$env:RUN_DB_TESTS="1"; pytest -q   → 1480 passed /  38 skipped / 0 failed
python -m compileall backend       → OK
lint（修改文件）                   → 0 errors
数据库残留                          → knowledge_document/chunk = (0, 0)
```

## A.6 已知限制

1. 无 HTTP ingestion API（按任务书 §十三：不为本阶段新增）；
   `project_id` 注入安全在 Service 层验证（metadata 剥离 + Provider
   服务器端解析）；
2. `IngestionResult.content_hash` 对项目化导入返回命名空间化 hash
   （文档身份含项目维度，语义与列注释一致但与旧值不可比）；
3. `ingest_directory` 批量入口仍未实现（原有边界，未扩大）；
4. `meta_data->>'project_id'` 仍无索引（Phase 3.8.4 已知限制，未变）。

