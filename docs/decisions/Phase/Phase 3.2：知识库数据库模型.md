现在开始 **Phase 3.2：知识库数据库模型**。

Phase 3.1 已经完成并真实验证：

* PostgreSQL 16
* pgvector
* SQLAlchemy 2.x
* psycopg 3
* 数据库连接
* pgvector extension
* `/api/health`
* 数据库集成测试

现在开始构建 RAG 的数据库基础。

## 一、本阶段目标

本阶段只完成：

```text
知识库
  ↓
knowledge_document
  ↓
knowledge_chunk
  ↓
embedding vector 字段
```

本阶段暂时**不实现**：

* Embedding API 调用
* DeepSeek Embedding
* 文档上传
* PDF 解析
* DOCX 解析
* Markdown 解析
* TXT 解析
* Chunk 自动切分
* 向量生成
* 向量搜索 API
* RAG
* Tool Calling
* Agent
* LangGraph
* MCP
* WMS / ERP API

不要超范围开发。

---

# 二、先阅读项目规范

必须先阅读：

* `AGENTS.md`
* `docs/requirements.md`
* `docs/architecture.md`

然后检查：

* `backend/app/db/base.py`
* `backend/app/db/session.py`
* `backend/app/db/init_db.py`
* `backend/app/config.py`
* `requirements.txt`
* `tests/test_db.py`

必须复用 Phase 3.1 已有 Database Layer。

不要重新创建数据库连接。

---

# 三、设计 knowledge_document

创建 ORM Model：

```text
backend/app/db/models/knowledge_document.py
```

建议字段：

```text
id
title
file_name
file_type
source
content_hash
status
metadata
created_at
updated_at
```

要求：

* `id` 使用项目统一 ID 类型
* `title` 非空
* `file_name` 可用于记录原始文件名称
* `file_type` 记录 md/txt/pdf/docx 等类型
* `source` 记录文档来源
* `content_hash` 用于后续判断文档是否重复
* `status` 用于后续 ingestion 状态管理
* `metadata` 使用 PostgreSQL JSONB
* `created_at`
* `updated_at`

不要过度设计。

如果项目已有统一时间字段、ID 类型或 ORM 基类规范，优先遵循现有规范。

---

# 四、设计 knowledge_chunk

创建：

```text
backend/app/db/models/knowledge_chunk.py
```

建议字段：

```text
id
document_id
chunk_index
content
content_hash
token_count
embedding
metadata
created_at
```

其中：

```text
document_id
```

必须建立外键：

```text
knowledge_chunk.document_id
        ↓
knowledge_document.id
```

要求：

* 一个 Document 可以拥有多个 Chunk
* 删除 Document 时需要明确处理 Chunk
* `chunk_index` 表示 Chunk 在原文中的顺序
* `content` 保存 Chunk 文本
* `content_hash` 用于后续去重
* `token_count` 可以为空，因为本阶段还没有真正计算 Token
* `metadata` 使用 JSONB
* `embedding` 使用 pgvector

---

# 五、Embedding 维度暂时不要写死

这是本阶段一个重要要求。

**不要现在直接写：**

```python
Vector(1536)
```

或者：

```python
Vector(1024)
```

因为我们还没有最终确定 Embedding 模型。

设计必须允许未来配置：

```text
Embedding Model
        ↓
dimension
        ↓
knowledge_chunk.embedding
```

如果 pgvector ORM 在当前阶段要求 dimension 才能建立向量索引，那么：

**本阶段不要创建 embedding vector index。**

可以先保留 vector 类型字段，具体 dimension 在后续确定 Embedding 模型之后再处理。

---

# 六、建立 Model 导出

整理：

```text
backend/app/db/models/__init__.py
```

让后续可以统一 import：

```python
from backend.app.db.models import KnowledgeDocument, KnowledgeChunk
```

同时确保 SQLAlchemy metadata 能够发现这些 Model。

---

# 七、数据库初始化

修改：

```text
backend/app/db/init_db.py
```

让数据库初始化能够创建本阶段 ORM 表。

要求：

```text
PostgreSQL
    ↓
vector extension
    ↓
knowledge_document
    ↓
knowledge_chunk
```

初始化必须幂等。

重复执行：

```bash
python -m backend.app.db.init_db
```

不能报错。

---

# 八、暂时不要引入 Alembic

本阶段不要为了数据库迁移引入复杂系统。

继续使用项目当前：

```text
Base.metadata.create_all()
```

即可。

后续项目进入稳定开发阶段，再单独设计 Alembic migration。

---

# 九、数据库约束

至少考虑：

### knowledge_document

```text
content_hash
```

建立合理索引。

### knowledge_chunk

```text
document_id
chunk_index
```

建立索引。

建议保证：

```text
同一个 document
不能存在重复 chunk_index
```

可以通过数据库 Unique Constraint 实现：

```text
(document_id, chunk_index)
```

---

# 十、Document / Chunk ORM Relationship

建立：

```text
KnowledgeDocument
      │
      │ 1:N
      ↓
KnowledgeChunk
```

要求 Python ORM 可以：

```python
document.chunks
```

以及：

```python
chunk.document
```

---

# 十一、测试

新增：

```text
tests/test_knowledge_models.py
```

必须使用真实 PostgreSQL + pgvector 进行数据库集成测试。

测试至少包括：

### 1. 创建 Document

插入：

```text
title = "WMS 入库操作说明"
file_name = "inbound.md"
file_type = "md"
source = "manual"
```

确认能够成功保存。

### 2. 创建 Chunk

创建至少两个 Chunk：

```text
document_id
chunk_index = 0
content = "采购入库首先需要创建采购入库通知..."
```

以及：

```text
chunk_index = 1
content = "仓库人员使用 PDA 扫描物料..."
```

确认能够保存。

### 3. Relationship

确认：

```python
document.chunks
```

能够获取两个 Chunk。

### 4. Unique Constraint

测试同一个 Document：

```text
chunk_index = 0
```

不能重复。

### 5. JSONB

确认 metadata 可以保存：

```json
{
  "department": "warehouse",
  "language": "zh-CN"
}
```

并能够读取。

### 6. Vector 类型

确认 `embedding` 字段可以被 PostgreSQL 识别为 pgvector 类型。

本阶段不要求真实 Embedding 数据。

不要调用 DeepSeek API。

---

# 十二、回归测试

执行：

```bash
RUN_DB_TESTS=1 pytest -v
```

确保：

```text
Phase 3.1 tests
+
Phase 3.2 tests
+
Phase 1 tests
+
Phase 2 tests
```

全部通过。

本阶段不要执行真实 LLM Smoke Test。

不要消耗 DeepSeek API 额度。

---

# 十三、检查数据库实际结构

完成后实际连接 PostgreSQL，检查：

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;
```

确认存在：

```text
knowledge_document
knowledge_chunk
```

检查 pgvector：

```sql
SELECT extname
FROM pg_extension
WHERE extname = 'vector';
```

确认仍然存在。

检查字段：

```text
knowledge_document
knowledge_chunk
```

确认 ORM 和数据库结构一致。

---

# 十四、不要修改 Phase 2

禁止修改：

```text
backend/app/llm/
backend/app/services/chat_service.py
backend/app/api/chat.py
backend/app/prompts/
```

DeepSeek 已经真实验证成功。

本阶段不要碰它。

---

# 十五、完成后停止

完成后只给我报告，不要自动进入 Phase 3.3。

报告：

## Phase 3.2 Result

| 项目                            | 状态       |
| ----------------------------- | -------- |
| KnowledgeDocument             | ✅/❌      |
| KnowledgeChunk                | ✅/❌      |
| PostgreSQL JSONB              | ✅/❌      |
| pgvector 字段                   | ✅/❌      |
| Document → Chunk Relationship | ✅/❌      |
| Unique Constraint             | ✅/❌      |
| Database Integration Tests    | X passed |
| Full Tests                    | X passed |
| DeepSeek API                  | 未调用      |

## 数据库验证

列出：

* knowledge_document
* knowledge_chunk
* 关键字段
* 索引 / 约束
* pgvector 状态

不要输出任何：

* API Key
* 数据库密码
* `.env` 敏感信息

## 当前状态

明确回答：

> Phase 3.2 是否完成？

如果完成：

> 可以进入 Phase 3.3。

否则：

> 停留在 Phase 3.2，并说明问题。

**不要自行进入 Phase 3.3。**
