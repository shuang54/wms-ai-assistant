你现在开始实施项目的 **Phase 3.5.10：Knowledge Base Lifecycle Management**。

# 一、阶段目标

当前项目已经完成：

```text
3.5.1 Embedding
3.5.2 Knowledge Ingestion
3.5.3 Vector Search
3.5.4 RAG
3.5.5 RAG API
3.5.6 Chat + RAG
3.5.7 Retrieval Evaluation
3.5.8 First Real WMS Knowledge Base
3.5.9 Retrieval Analysis & Optimization
```

当前知识库已经能够：

```text
Markdown
 ↓
Parser
 ↓
Chunker
 ↓
BGE-M3
 ↓
pgvector
 ↓
Vector Search
 ↓
RAG
 ↓
Chat
```

但目前存在一个工程问题：

> 当同一个知识文档内容发生变化时，目前需要人工清理旧 document 后重新 ingestion。

本阶段唯一目标：

> **建立最小可用的知识文档生命周期管理能力，使文档可以安全地新增、更新、删除，并正确维护对应 chunks 和 embeddings。**

---

# 二、开始前必须阅读

必须先阅读：

```text
AGENTS.md
docs/requirements.md
docs/architecture.md
docs/api.md
README.md

backend/app/services/knowledge_ingestion_service.py
backend/app/services/vector_search_service.py
backend/app/services/rag_service.py
backend/app/services/rag_evaluation_service.py

backend/app/models/
backend/app/db/
backend/app/parsers/
backend/app/chunking/
backend/app/embedding/

tests/test_knowledge_ingestion_service.py
tests/test_real_wms_kb_ingestion.py
```

同时阅读：

```text
docs/decisions/
docs/evaluation/
docs/knowledge/
```

---

# 三、严格禁止

本阶段不要实现：

* Agent
* Tool Calling
* Function Calling
* LangGraph
* MCP
* WMS API
* ERP API
* SQL Tool
* 自然语言数据库查询
* Intent Classifier
* Query Rewrite
* Reranker
* Hybrid Search
* BM25
* HNSW
* IVFFlat
* Streaming
* Redis
* Celery
* 消息队列
* 权限系统
* 前端
* PDF/DOCX
* 新 Embedding 模型
* 更换 DeepSeek
* 修改 Vector Search 算法
* 修改 RAG Prompt
* 修改 `/api/chat`
* 修改 `/api/rag/answer`

也不要进行大规模 ORM / DB 架构重构。

---

# 四、先分析当前数据模型

重点确认：

```text
KnowledgeDocument
KnowledgeChunk
```

当前字段。

特别确认：

```text
content_hash
document_id
chunk_id
document.status
document.created_at
document.updated_at
chunk.document_id
chunk.embedding
```

确认：

```text
KnowledgeChunk.document_id
```

是否已经存在：

```text
ON DELETE CASCADE
```

如果已经存在：

> 直接复用。

不要重复设计。

---

# 五、设计生命周期

本阶段只需要支持：

```text
CREATE
UPDATE
DELETE
GET
```

不需要：

```text
PUBLISH
VERSION
ROLLBACK
APPROVAL
```

这些以后再做。

---

# 六、Create / Ingest

复用现有：

```text
KnowledgeIngestionService
```

行为保持兼容：

```text
第一次导入新内容
→ 创建 document
→ 创建 chunks
→ 创建 embeddings
→ status = ready
```

同一内容再次导入：

```text
same content_hash
→ already_exists
→ 不重复创建
```

这个现有行为不能破坏。

---

# 七、Update：本阶段核心

新增一个明确的：

```text
update_document(...)
```

或者等价 Service 方法。

要求：

```text
旧 document
 ↓
读取新文件内容
 ↓
计算新 content_hash
 ↓
判断内容是否发生变化
```

如果：

```text
new_hash == old_hash
```

则：

```text
already_exists / unchanged
```

不要重新调用 Embedding API。

---

# 八、内容发生变化时

如果：

```text
new_hash != old_hash
```

必须安全地更新。

推荐最小策略：

```text
BEGIN TRANSACTION

旧 document
    ↓
删除旧 chunks
    ↓
重新 Parser
    ↓
重新 Chunk
    ↓
重新 Embedding
    ↓
写入新 chunks
    ↓
更新 document metadata/content_hash/status

COMMIT
```

关键要求：

> **必须保证整个更新过程具有事务一致性。**

不能出现：

```text
旧 chunks 已删除
↓
Embedding 失败
↓
数据库只剩空 document
```

如果中途失败：

```text
ROLLBACK
```

必须保持原来的 document + chunks 不变。

---

# 九、Document ID 策略

优先：

> **更新同一个 document_id。**

例如：

```text
旧：
document_id = 1
chunks = 10

更新：

document_id = 1
chunks = 11
```

不要采用：

```text
旧 document_id = 1
删除

新 document_id = 2
```

除非当前数据模型明确不支持原地更新。

原因：

* 外部引用更稳定
* source metadata 更稳定
* 不需要重新生成 document identity
* 更符合 update 语义

---

# 十、Chunk 更新

更新时必须：

```text
旧 chunks 全部删除
↓
根据新文档重新 chunk
↓
重新生成 embedding
↓
重新写入 chunks
```

不要尝试本阶段做：

```text
chunk diff
partial embedding update
```

因为这会增加复杂度。

---

# 十一、失败回滚测试

这是本阶段最重要的测试之一。

构造：

```text
旧文档
10 chunks
```

然后更新一个新版本。

人为让 Embedding 在中途失败。

最终必须验证：

```text
document.content_hash
```

仍然是旧 hash。

并且：

```text
旧 chunks
```

仍然全部存在。

不能出现：

```text
0 chunks
```

也不能留下：

```text
半套新 chunks
```

---

# 十二、Delete

增加：

```text
delete_document(document_id)
```

要求：

```text
document
 ↓
chunks
```

正确删除。

如果 FK cascade 已经存在：

> 直接依赖数据库 cascade。

删除后：

```text
knowledge_document = 0
knowledge_chunk = 0
```

对应数据必须不存在。

---

# 十三、Delete 不存在的 document

明确行为：

```text
document_id 不存在
```

应该返回：

```text
NotFound
```

不要静默成功。

---

# 十四、Get Document

增加：

```text
get_document(document_id)
```

返回一个简单 DTO。

建议包含：

```text
id
title
file_name
file_type
source
status
created_at
updated_at
chunk_count
```

不要返回：

```text
embedding
chunk content 全文
数据库连接
API Key
```

chunk_count 可以通过查询得到。

---

# 十五、DTO

建议使用 frozen dataclass：

```text
KnowledgeDocumentInfo
KnowledgeDocumentOperationResult
```

不要让 Service 对外暴露 ORM。

如果项目已有类似 DTO：

> 复用已有结构。

---

# 十六、CLI

检查当前是否已经存在：

```text
backend/app/cli/ingest_knowledge.py
```

如果存在：

扩展它。

如果不存在：

可以建立最小 CLI。

支持：

```bash
python -m backend.app.cli.knowledge list
python -m backend.app.cli.knowledge get 1
python -m backend.app.cli.knowledge ingest docs/knowledge/wms-basic-operations.md
python -m backend.app.cli.knowledge delete 1
```

但是：

> 如果实现 CLI 会明显扩大范围，可以只实现 Service + 测试。

CLI 不是本阶段核心目标。

---

# 十七、不要做 HTTP API

本阶段：

> **不要新增 `/api/knowledge/*` API。**

原因：

目前重点是先把领域 Service 的生命周期行为做正确。

HTTP API 可以后续单独作为一个阶段。

---

# 十八、现有 ingestion 行为必须保持

必须保证：

```text
新文件
→ CREATE

完全相同文件
→ ALREADY_EXISTS

修改后的文件
→ UPDATE SAME DOCUMENT ID

更新失败
→ ROLLBACK

删除
→ DELETE DOCUMENT + CHUNKS
```

---

# 十九、测试

新增或扩展：

```text
tests/test_knowledge_lifecycle_service.py
```

至少覆盖：

## 1. Create

```text
new document
→ created
→ chunks > 0
→ embedding exists
```

## 2. Duplicate

```text
same content
→ already_exists
→ document count 不增加
→ chunk count 不增加
→ Embedding 不重新调用
```

## 3. Update unchanged

```text
same hash
→ unchanged
→ embedding 不重新调用
```

## 4. Update changed

```text
new content
→ same document_id
→ old chunks 被替换
→ new chunks 创建
→ new hash
```

## 5. Update rollback

Embedding 中途失败：

```text
old document 保留
old hash 保留
old chunks 保留
new chunks 不残留
```

## 6. Delete

```text
delete
→ document 不存在
→ chunks 不存在
```

## 7. Delete missing

```text
missing document
→ NotFound
```

## 8. Get

验证：

```text
document info
chunk_count
```

正确。

---

# 二十、真实 DB 测试

增加：

```text
tests/test_knowledge_lifecycle_real.py
```

默认：

```text
SKIP
```

只有：

```text
RUN_DB_TESTS=1
```

执行。

注意：

> 所有真实 DB 测试必须使用测试自建 document。

不能直接删除当前真实：

```text
document_id = 1
```

除非测试明确创建并管理该数据。

---

# 二十一、真实 Embedding

Lifecycle 测试默认不要大量消耗 API。

可以：

```text
mock EmbeddingClient
```

测试：

```text
create
update
rollback
```

逻辑。

真实 BGE-M3 测试最多保留一个 smoke test。

例如：

```text
RUN_REAL_KB_LIFECYCLE=1
```

执行：

```text
create
update
query
delete
```

不要调用 DeepSeek。

---

# 二十二、重新验证 RAG

生命周期测试完成后：

当前真实知识库可以继续保持可用。

必须至少验证：

```text
Vector Search
RAG Evaluation
```

没有被破坏。

如果为了测试创建临时文档：

> 测试结束必须清理。

---

# 二十三、数据一致性检查

最终必须验证：

```text
每个 KnowledgeChunk.document_id
```

都对应一个存在的：

```text
KnowledgeDocument.id
```

并且：

```text
embedding dimension = 1024
```

---

# 二十四、安全

继续确保：

```text
API Key: NOT FOUND
Authorization: NOT FOUND
Database URL: NOT FOUND
真实服务器 IP: NOT FOUND
个人信息: NOT FOUND
```

`.env`：

```text
NOT COMMITTED
```

---

# 二十五、不要修改这些核心模块

除非发现明确 bug：

```text
vector_search_service.py
rag_service.py
context_builder.py
embedding/
llm/
rag_evaluation_service.py
```

也不要修改：

```text
evaluation_cases.json
```

---

# 二十六、Regression

执行：

```bash
pytest -q
```

然后：

```bash
RUN_DB_TESTS=1 pytest -q
```

如果存在真实 lifecycle smoke：

```bash
RUN_REAL_KB_LIFECYCLE=1 pytest -q tests/test_knowledge_lifecycle_real.py
```

然后重新运行：

```bash
RUN_REAL_RAG_EVAL=1 pytest -q tests/test_rag_evaluation_real.py
```

要求：

```text
0 failed
```

---

# 二十七、最终报告

严格按照下面格式：

```text
Phase 3.5.10 Knowledge Base Lifecycle Management

1. Implemented
- Create:
- Duplicate:
- Update:
- Rollback:
- Delete:
- Get:

2. Update Semantics
- unchanged content:
- changed content:
- document_id preserved:
- old chunks replaced:
- transaction rollback:

3. Tests
- Unit:
- DB:
- Full regression:
- Real lifecycle:
- Real RAG evaluation:

4. Database
- document:
- chunks:
- embedding:
- dimension:

5. RAG Regression
- Vector Search:
- RAG Evaluation:
- Hit Rate:

6. API
- /api/rag/answer:
- /api/chat:

7. Security
- API Key:
- .env committed:
- Sensitive data:

8. Scope
- Agent: NO
- Tool Calling: NO
- LangGraph: NO
- MCP: NO
- WMS API: NO
- HTTP Knowledge API: NO
- Query Rewrite: NO
- Reranker: NO
- Hybrid Search: NO

9. Modified Files
- ...

10. Status
Phase 3.5.10 COMPLETE
```

**完成 Phase 3.5.10 后必须停止，不得自动进入 Phase 3.5.11。**
