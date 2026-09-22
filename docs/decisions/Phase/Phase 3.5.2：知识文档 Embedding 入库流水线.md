# Phase 3.5.2：Knowledge Ingestion Pipeline

你现在进入 **WMS AI Assistant Phase 3.5.2**。

## 一、阶段目标

前面的 Phase 已经完成：

* Phase 3.1：PostgreSQL + pgvector
* Phase 3.2：KnowledgeDocument / KnowledgeChunk 数据模型
* Phase 3.3：TXT / Markdown Parser
* Phase 3.4：Markdown-aware Text Chunking
* Phase 3.5.1.1：Embedding Client
* Phase 3.5.1.5：真实验证 SiliconFlow + BAAI/bge-m3
* Phase 3.5.1.6：Embedding 维度审计
* Phase 3.5.1.7：1536 → 1024 数据库迁移

当前最终 Embedding 配置：

```text
Provider:
siliconflow

Model:
BAAI/bge-m3

Dimension:
1024

Database:
PostgreSQL + pgvector

Column:
knowledge_chunk.embedding = vector(1024)
```

现在把前面已经完成的模块连接起来。

目标：

```text
TXT / Markdown
      ↓
Parser
      ↓
Text Chunker
      ↓
TextChunk[]
      ↓
EmbeddingClient
      ↓
1024-dimensional vectors
      ↓
KnowledgeDocument
      ↓
KnowledgeChunk
      ↓
PostgreSQL
```

---

# 二、本阶段核心原则

本阶段只实现：

> **Document → Parse → Chunk → Embedding → Database**

不要实现：

> Search / RAG / Tool Calling / Agent

---

# 三、严格禁止

本阶段不要实现：

* ❌ Vector Search
* ❌ Cosine Similarity Search
* ❌ RAG
* ❌ `/api/knowledge/search`
* ❌ Chat + RAG
* ❌ Tool Calling
* ❌ Agent
* ❌ LangGraph
* ❌ MCP
* ❌ WMS API
* ❌ ERP API
* ❌ 权限系统
* ❌ 写操作审批
* ❌ OCR
* ❌ PDF
* ❌ DOCX
* ❌ 自动网页抓取
* ❌ 批量后台任务
* ❌ Celery
* ❌ Redis
* ❌ Kafka
* ❌ 微服务拆分

也不要修改：

```text
Parser
Chunking algorithm
LLM Chat
WMS
ERP
```

除非发现明确的兼容性 Bug。

---

# 四、先读取项目文档

开始前必须读取：

```text
AGENTS.md
docs/requirements.md
docs/architecture.md
docs/decisions/embedding-dimension.md
```

然后检查：

```text
backend/app/rag/
backend/app/embedding/
backend/app/db/
backend/app/config.py
tests/
```

理解现有接口后再开发。

---

# 五、设计新的 Ingestion Service

新增：

```text
backend/app/services/knowledge_ingestion_service.py
```

建议职责：

```text
KnowledgeIngestionService
```

负责协调：

```text
Parser
Chunker
EmbeddingClient
KnowledgeDocument
KnowledgeChunk
Database Session
```

不要把业务逻辑塞进 Parser、Chunker 或 EmbeddingClient。

---

# 六、推荐处理流程

输入：

```text
file_path
```

例如：

```text
knowledge/
    wms/
        inbound.md
```

执行：

```text
file_path
    ↓
ParserFactory
    ↓
document_text
    ↓
TextChunker
    ↓
TextChunk[]
    ↓
EmbeddingClient
    ↓
embedding[]
    ↓
KnowledgeDocument
    ↓
KnowledgeChunk[]
    ↓
commit
```

---

# 七、Document 元数据设计

创建 `KnowledgeDocument` 时至少设置：

```text
title
file_name
file_type
source
content_hash
status
metadata
```

建议：

```text
title:
文件名称去掉扩展名

file_name:
原始文件名

file_type:
md / txt

source:
local

status:
completed
```

如果当前项目已有合法的 status 枚举/约定，必须优先遵循现有设计。

---

# 八、Content Hash

使用 SHA-256：

```text
SHA256(document_text)
```

生成：

```text
content_hash
```

目的：

> 防止同一份知识文档重复导入。

---

# 九、重复文档处理

如果：

```text
content_hash
```

已经存在：

不要再次调用 Embedding API。

不要重复创建：

```text
KnowledgeDocument
KnowledgeChunk
```

应该返回：

```text
already_exists
```

或者项目现有的等价结果。

必须避免：

```text
重复文档
    ↓
重复 Embedding API
    ↓
浪费 API 成本
```

---

# 十、Chunk 入库

对于：

```text
TextChunk[]
```

每个 Chunk 创建：

```text
KnowledgeChunk
```

字段映射：

```text
chunk_index
content
content_hash
token_count
embedding
metadata
```

其中：

```text
chunk_index
```

使用 Chunker 返回的 index。

---

# 十一、Chunk Content Hash

每个 Chunk 使用：

```text
SHA256(chunk.content)
```

作为：

```text
content_hash
```

不要使用整个文档 hash 代替。

---

# 十二、Metadata

保留 Chunker 已经产生的：

```text
source_type
heading_path
```

不要破坏现有 metadata。

例如：

```json
{
  "source_type": "markdown",
  "heading_path": [
    "WMS",
    "采购入库"
  ]
}
```

如果当前 Chunker 实际返回格式不同，以实际代码为准。

---

# 十三、Token Count

当前 Chunker 是：

```text
character-based
```

因此：

**不要为了 token_count 引入新的 tokenizer 依赖。**

如果当前没有可靠 tokenizer：

```text
token_count = null
```

即可。

不要把：

```text
len(content)
```

冒充 token 数量。

---

# 十四、Embedding 调用策略

对于每一个 Chunk：

```text
chunk.content
      ↓
EmbeddingClient.embed()
      ↓
1024 dimensions
```

必须使用：

```text
EmbeddingClient
```

不要直接：

```text
httpx.post(...)
```

不要在 ingestion service 中自己实现 SiliconFlow API。

---

# 十五、Embedding Dimension Validation

数据库写入之前必须确认：

```text
len(embedding) == settings.embedding.dimension
```

当前：

```text
1024
```

如果维度不一致：

```text
EmbeddingDimensionError
```

必须阻止数据库写入。

禁止：

```text
截断
补零
自动转换
```

---

# 十六、事务设计

整个单文档导入应该尽量保持事务一致性：

```text
BEGIN
  ↓
创建 KnowledgeDocument
  ↓
创建 KnowledgeChunk
  ↓
写入 Embeddings
  ↓
COMMIT
```

如果中途出现：

```text
Parser Error
Embedding Error
Database Error
Dimension Error
```

应该：

```text
ROLLBACK
```

避免产生：

```text
Document 存在
但是 Chunk 不完整
```

或者：

```text
Chunk 存在
但是 Embedding 缺失
```

---

# 十七、状态设计

使用当前数据库已有：

```text
status
```

不要随意创建新的枚举系统。

如果现有 status 允许：

```text
pending
processing
completed
failed
```

推荐流程：

```text
pending
   ↓
processing
   ↓
completed
```

发生异常：

```text
processing
   ↓
failed
```

但如果事务 rollback 会导致 Document 也回滚，则可以采用项目当前最简单的一致性方案。

**优先简单可靠，不要为了状态系统引入复杂架构。**

---

# 十八、不要一次实现批量异步系统

本阶段只实现：

```text
ingest_one(file_path)
```

也就是：

> 单文件同步/异步导入能力。

暂时不要实现：

```text
ingest_directory()
background worker
Celery
queue
scheduled ingestion
```

后续需要批量导入时再扩展。

---

# 十九、返回结果

建议 Service 返回结构化结果，例如：

```text
IngestionResult
```

至少包含：

```text
document_id
file_name
status
chunk_count
embedded_chunk_count
content_hash
```

例如：

```json
{
  "document_id": 1,
  "file_name": "inbound.md",
  "status": "completed",
  "chunk_count": 8,
  "embedded_chunk_count": 8
}
```

具体数据结构遵循项目现有 Schema 风格。

---

# 二十、暂时不要创建 API

本阶段不要创建：

```text
POST /api/knowledge/ingest
```

先把 Service 层做好。

可以通过：

```text
tests
```

直接测试。

后续 Phase 再增加 API。

---

# 二十一、测试必须覆盖

新增：

```text
tests/test_knowledge_ingestion_service.py
```

至少覆盖：

### Test 1：TXT 导入

```text
TXT
 ↓
Parser
 ↓
Chunk
 ↓
Embedding
 ↓
DB
```

成功。

---

### Test 2：Markdown 导入

验证：

```text
heading
heading_path
content
embedding
```

正确写入。

---

### Test 3：多 Chunk

准备一份足够长的测试文档：

```text
Document
 ↓
多个 Chunk
 ↓
每个 Chunk 都有独立 embedding
```

验证：

```text
chunk_index
0
1
2
...
```

连续。

---

### Test 4：Embedding Dimension

Mock：

```text
1024 dimensions
```

必须成功。

Mock：

```text
1536 dimensions
```

必须失败。

数据库不得产生脏数据。

---

### Test 5：Duplicate Document

第一次：

```text
completed
```

第二次相同内容：

```text
already_exists
```

并验证：

```text
Embedding API
```

没有再次调用。

---

### Test 6：Embedding API Error

Mock：

```text
EmbeddingAPIError
```

要求：

```text
事务 rollback
```

数据库不留下半成品。

---

### Test 7：Parser Error

例如：

```text
unsupported extension
```

要求：

```text
失败
数据库无半成品
```

---

### Test 8：Empty Document

空文件：

```text
Parser
 ↓
empty text
```

应该：

```text
不调用 Embedding
```

并按照项目设计返回明确错误。

不要写入空知识文档。

---

### Test 9：Database Roundtrip

真实 PostgreSQL：

```text
KnowledgeDocument
KnowledgeChunk
embedding vector(1024)
```

写入后读取。

验证：

```text
len(embedding) == 1024
```

---

# 二十二、Embedding API 测试必须使用 Mock

绝大多数单元测试：

```text
MockEmbeddingClient
```

不要产生 API 成本。

只有专门的 Real Smoke Test 才调用 SiliconFlow。

本阶段真实 API：

**最多 1 次小型 Smoke Test。**

不要批量调用真实 API。

---

# 二十三、测试数据

测试知识文档可以使用：

```text
tests/fixtures/knowledge/
```

例如：

```text
tests/fixtures/knowledge/
    sample_wms.md
    sample_wms.txt
    empty.txt
```

内容使用中文 WMS 场景，例如：

```text
采购入库流程：
采购订单审核完成后，采购人员创建入库通知单。
仓库人员根据入库通知单执行收货、扫码和上架。
```

不要使用真实企业敏感数据。

---

# 二十四、数据库测试隔离

DB Test 必须：

```text
RUN_DB_TESTS=1
```

才执行。

测试结束后清理测试数据。

不得污染：

```text
knowledge_document
knowledge_chunk
```

正式数据。

---

# 二十五、错误处理

定义或复用合理的：

```text
KnowledgeIngestionError
```

错误必须能够区分：

```text
Parser Error
Embedding Error
Dimension Error
Database Error
Duplicate Document
```

不要简单：

```python
except Exception:
    raise Exception("导入失败")
```

丢失原始错误信息。

---

# 二十六、日志

Service 可以记录：

```text
document file name
chunk count
embedding count
duration
status
```

但：

**绝对不要记录：**

```text
API Key
Authorization header
完整 embedding vector
```

---

# 二十七、性能原则

当前阶段不要做复杂优化。

但避免：

```text
同一文档重复 Embedding
```

对于单文档：

```text
Parser → Chunker → Embedding → DB
```

即可。

后续可以再考虑：

```text
Embedding Batch API
并发
缓存
队列
```

---

# 二十八、README / Architecture 是否修改

如果新的 Ingestion Service 改变了实际架构，可以更新：

```text
docs/architecture.md
```

但只记录真实已经实现的内容。

不要提前写：

```text
RAG
Vector Search
Agent
```

如果只是增加一个 Service，不需要为了形式修改大量文档。

---

# 二十九、依赖控制

尽量使用当前已有依赖。

本阶段不要为了：

```text
hash
transaction
file parsing
```

引入新的大型依赖。

优先使用：

```text
hashlib
pathlib
现有 Parser
现有 Chunker
现有 EmbeddingClient
现有 SQLAlchemy
```

---

# 三十、执行顺序

严格按照：

```text
1. 读取项目规范
2. 检查现有接口
3. 设计 Ingestion Service
4. 实现 IngestionResult
5. 实现单文件导入
6. 接入 Parser
7. 接入 Chunker
8. 接入 EmbeddingClient
9. 接入 SQLAlchemy
10. 实现事务
11. 实现 duplicate detection
12. 编写 Unit Tests
13. 编写 DB Tests
14. 执行测试
15. 必要时执行一次 Real Smoke
16. 回归全量测试
17. git diff 检查
18. 输出最终报告
19. 停止
```

---

# 三十一、最终报告格式

完成后严格输出：

````text
# Phase 3.5.2 最终报告

## 1. Ingestion Pipeline

Parser:
PASS / FAIL

Chunker:
PASS / FAIL

Embedding:
PASS / FAIL

Database:
PASS / FAIL

## 2. Pipeline

Document
→ Parser
→ Chunker
→ Embedding
→ KnowledgeDocument
→ KnowledgeChunk

Status:
PASS / FAIL

## 3. Embedding

Provider:
siliconflow

Model:
BAAI/bge-m3

Dimension:
1024

Real Smoke:
PASS / FAIL / SKIPPED

## 4. Database

KnowledgeDocument:
PASS / FAIL

KnowledgeChunk:
PASS / FAIL

Vector:
vector(1024)

Roundtrip:
PASS / FAIL

## 5. Duplicate Protection

Duplicate document:
PASS / FAIL

Repeated Embedding prevented:
PASS / FAIL

## 6. Transaction

Embedding failure rollback:
PASS / FAIL

Parser failure rollback:
PASS / FAIL

## 7. Tests

Unit:
XXX passed

DB:
XXX passed

Full:
XXX passed
XXX skipped
XXX failed

## 8. Files Modified

列出实际修改文件。

## 9. Security

API Key:
未输出 / 未进入 Git

Embedding Vector:
未写日志

DeepSeek:
未调用

## 10. Scope Check

RAG:
NOT IMPLEMENTED

Vector Search:
NOT IMPLEMENTED

Tool Calling:
NOT IMPLEMENTED

Agent:
NOT IMPLEMENTED

LangGraph:
NOT IMPLEMENTED

WMS/ERP:
NOT IMPLEMENTED

## 11. Final Status

Phase 3.5.2:
COMPLETE / BLOCKED

下一阶段：
Phase 3.5.3

如果存在任何失败：

不要进入下一阶段。

---

# 三十二、最重要的停止规则

**Phase 3.5.2 完成后立即停止。**

不要自动实现：

```text
Vector Search
RAG
Chat + Knowledge Base
````

下一阶段由我确认后再开始。

核心原则：

> 先让“知识能够可靠进入数据库”，再让“AI 能够搜索知识”。
