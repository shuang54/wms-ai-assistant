# Phase 3.5.1.7：Embedding Dimension Migration Execution & Verification

你现在进入 **WMS AI Assistant Phase 3.5.1.7**。

## 一、阶段目标

Phase 3.5.1.5 已通过真实 SiliconFlow API 验证：

```text
Provider: SiliconFlow
Model: BAAI/bge-m3
Actual Dimension: 1024
Endpoint: https://api.siliconflow.cn/v1/embeddings
```

Phase 3.5.1.6 已完成全项目审计，确认：

```text
当前配置：1536
当前数据库：vector(1536)
实际模型：1024
当前 knowledge_chunk.embedding 数据：0 条
```

现在正式执行：

```text
1536
  ↓
1024
```

最终目标：

```text
BAAI/bge-m3
      ↓
1024 dimensions
      ↓
EmbeddingClient
      ↓
knowledge_chunk.embedding
      ↓
PostgreSQL pgvector vector(1024)
```

---

# 二、必须先读取的文档

开始前必须读取：

```text
AGENTS.md
docs/requirements.md
docs/architecture.md
docs/decisions/embedding-dimension.md
```

同时检查：

```text
backend/app/config.py
backend/app/embedding/
backend/app/db/
tests/
.env.example
```

严格按照现有项目架构执行。

---

# 三、本阶段允许修改

允许修改：

```text
.env
.env.example

backend/app/config.py
backend/app/embedding/client.py

tests/test_embedding_client.py
tests/test_embedding_real.py

必要时：
docs/decisions/embedding-dimension.md
```

允许执行：

```text
PostgreSQL DDL
```

但只能修改：

```text
knowledge_chunk.embedding
```

的 Vector Dimension。

---

# 四、本阶段禁止事项

本阶段不要做：

* ❌ RAG
* ❌ 批量文档 Embedding
* ❌ 文档入库
* ❌ Vector Search
* ❌ Similarity Search API
* ❌ Tool Calling
* ❌ Agent
* ❌ LangGraph
* ❌ MCP
* ❌ WMS API
* ❌ ERP API
* ❌ 修改 Chunking 算法
* ❌ 修改 Parser
* ❌ 修改 Chat
* ❌ 修改 DeepSeek LLM
* ❌ 引入 Alembic
* ❌ 引入新依赖
* ❌ 修改无关数据库表

本阶段只解决：

> **Embedding 1024 维 → PostgreSQL vector(1024) → Client/ORM/Test 全部一致。**

---

# 五、第一步：修改配置

修改：

```text
.env
```

将：

```text
EMBEDDING_DIMENSION=1536
```

修改为：

```text
EMBEDDING_DIMENSION=1024
```

保持当前 SiliconFlow 配置不变。

例如：

```text
EMBEDDING_PROVIDER=siliconflow
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
EMBEDDING_API_KEY=<existing local key>
EMBEDDING_DIMENSION=1024
EMBEDDING_TIMEOUT=60
```

**不要输出 API Key。**

不要修改：

```text
LLM_API_KEY
```

不要调用 DeepSeek。

---

# 六、第二步：修改 .env.example

修改：

```text
.env.example
```

将 Embedding 默认维度改成：

```text
EMBEDDING_DIMENSION=1024
```

注释应与当前方案一致：

```text
BAAI/bge-m3
1024 dimensions
```

不要继续保留误导性的：

```text
Default 1536
OpenAI embedding
```

---

# 七、第三步：修改 Config 默认值

检查：

```text
backend/app/config.py
```

将默认：

```text
1536
```

修改为：

```text
1024
```

确保：

```text
EMBEDDING_DIMENSION
```

在没有 `.env` 时默认也是：

```text
1024
```

目标：

```text
.env = 1024
config default = 1024
```

避免不同环境出现默认值漂移。

---

# 八、第四步：检查 Embedding Client

检查：

```text
backend/app/embedding/client.py
```

确认不存在任何业务意义上的：

```text
1536
```

硬编码。

最终逻辑必须是：

```text
settings.embedding.dimension
```

作为唯一配置来源。

必须保证：

```text
API 返回 1024
        ↓
Client dimension validation
        ↓
通过
```

以及：

```text
API 返回 1536
        ↓
Client dimension validation
        ↓
EmbeddingDimensionError
```

不要做：

```text
截断
补零
自动降维
```

---

# 九、第五步：修改测试

检查：

```text
tests/test_embedding_client.py
tests/test_embedding_real.py
```

把依赖旧 1536 默认值的测试调整为 1024。

重点覆盖：

### Case 1：默认配置

```text
EmbeddingSettings().dimension == 1024
```

### Case 2：1024 维成功

Mock 一个：

```text
[0.1, 0.2, ...]
```

长度：

```text
1024
```

要求：

```text
embed()
```

成功。

### Case 3：1536 维失败

Mock 一个长度：

```text
1536
```

要求：

```text
EmbeddingDimensionError
```

### Case 4：自定义 Dimension

确保仍然支持：

```text
dimension=512
```

等测试场景。

这里不要把所有测试硬编码成 1024。

测试应该继续验证：

> Client 能否按照配置进行维度校验。

---

# 十、第六步：修改数据库 Schema

这是本阶段最重要的一步。

首先连接当前 PostgreSQL。

**不要直接 ALTER。**

先执行只读检查：

```sql
SELECT COUNT(*)
FROM knowledge_chunk
WHERE embedding IS NOT NULL;
```

再执行：

```sql
SELECT
    column_name,
    data_type,
    udt_name
FROM information_schema.columns
WHERE table_name = 'knowledge_chunk'
  AND column_name = 'embedding';
```

如果项目已有更可靠的 pgvector schema 查询方式，也可以使用。

---

# 十一、数据库安全检查

如果：

```text
embedding 非空数量 > 0
```

立即停止数据库迁移。

不要删除旧向量。

不要执行：

```text
USING NULL
```

不要执行任何破坏性操作。

报告：

```text
发现已有 Embedding 数据
数量：XXX
```

然后停止，等待人工确认。

---

只有当：

```text
embedding IS NOT NULL = 0
```

才允许继续迁移。

---

# 十二、执行 Vector Dimension Migration

当前：

```text
knowledge_chunk.embedding
vector(1536)
```

目标：

```text
knowledge_chunk.embedding
vector(1024)
```

根据 Phase 3.5.1.6 的审计结果：

```text
当前没有 Embedding 数据
```

因此可以执行维度迁移。

优先采用当前决策文档中的安全方案：

```sql
ALTER TABLE knowledge_chunk
ALTER COLUMN embedding
TYPE vector(1024)
USING NULL;
```

但是：

**不要盲目假设 SQL 一定可执行。**

如果 PostgreSQL/pgvector 实际执行时报错：

1. 不要反复尝试破坏性 SQL
2. 读取错误信息
3. 判断 pgvector 当前版本和实际字段类型
4. 选择当前环境可行的等价迁移方式
5. 记录实际执行方式

当前 pgvector 版本之前已经验证为：

```text
0.8.6
```

---

# 十三、迁移后 Schema 验证

迁移完成后必须验证：

```text
knowledge_chunk.embedding
```

确实为：

```text
vector(1024)
```

同时检查：

```text
knowledge_chunk
```

的数据数量没有异常变化。

要求：

```text
迁移前 row count
=
迁移后 row count
```

并确认：

```text
embedding 非空数量 = 0
```

因为当前阶段还没有正式生成 Embedding。

---

# 十四、ORM 验证

检查：

```text
backend/app/db/models/knowledge_chunk.py
```

确认：

```text
Vector(settings.embedding.dimension)
```

最终解析为：

```text
Vector(1024)
```

不要为了 1024 修改成：

```text
Vector(1024)
```

如果当前 ORM 已经是配置驱动：

```text
Vector(settings.embedding.dimension)
```

则保持代码不变。

这是更好的设计。

---

# 十五、数据库 Roundtrip Test

增加/执行一个数据库集成测试。

要求：

```text
创建 KnowledgeChunk
        ↓
embedding = 1024维向量
        ↓
INSERT
        ↓
SELECT
        ↓
读取成功
        ↓
长度 = 1024
```

测试完成后清理测试数据。

注意：

**不要污染正式知识库数据。**

如果当前 DB 测试机制要求：

```text
RUN_DB_TESTS=1
```

按照项目现有测试方式执行。

---

# 十六、Embedding Client Mock 验证

执行：

```text
1024维 Mock
```

必须：

```text
PASS
```

执行：

```text
1536维 Mock
```

必须：

```text
EmbeddingDimensionError
```

确保：

```text
Client dimension
=
Database dimension
=
1024
```

---

# 十七、Real Embedding Smoke Test

在本地 `.env` 已经配置 SiliconFlow Key 的情况下，执行：

```text
BAAI/bge-m3
```

真实 API Smoke Test。

最多使用：

```text
1~2 条中文 WMS 文本
```

例如：

```text
越南仓库当前库存查询需要根据物料编码和仓库编码获取库存数量。
```

以及：

```text
采购订单入库后，仓库人员根据入库通知单执行收货、扫码和上架。
```

验证：

```text
HTTP 200
Vector Dimension = 1024
```

然后再使用 Client：

```text
EmbeddingClient.embed()
```

确认：

```text
1024
→
通过 Client 校验
```

不要调用 DeepSeek。

不要调用 Chat API。

---

# 十八、不要在本阶段写入正式 KnowledgeChunk Embedding

特别注意：

虽然已经可以成功生成：

```text
1024维 Embedding
```

但本阶段：

**不要把真实 Embedding 写入 knowledge_chunk。**

不要实现：

```text
document
→ chunk
→ embedding
→ database
```

这属于下一阶段。

本阶段只验证：

```text
Embedding API
        ↓
EmbeddingClient
        ↓
1024 dimensions
```

以及：

```text
Mock 1024 vector
        ↓
PostgreSQL vector(1024)
```

---

# 十九、测试顺序

按以下顺序执行：

### 1. 单元测试

```bash
pytest tests/test_embedding_client.py -v
```

### 2. Parser / Chunking 回归测试

```bash
pytest tests/test_rag_parsers.py tests/test_chunking.py -v
```

如果实际项目测试文件名不同，使用项目现有测试文件。

### 3. DB 测试

```bash
RUN_DB_TESTS=1 pytest tests/test_db.py -v
```

### 4. Embedding DB 相关测试

如果项目已有：

```text
tests/test_embedding_db.py
```

执行它。

### 5. Real Smoke

仅在本地存在合法 Embedding API Key 时执行。

### 6. 完整测试

```bash
RUN_DB_TESTS=1 pytest -v
```

记录：

```text
passed
failed
skipped
warnings
```

---

# 二十、检查 API 服务

启动：

```bash
uvicorn backend.app.main:app --reload
```

验证：

```text
/api/health
```

要求：

```json
{
  "status": "ok",
  "service": "wms-ai-assistant",
  "database": "ok"
}
```

再验证：

```text
/api/chat
```

确认之前 Phase 2 的 Chat 功能没有受到影响。

如果 chat 测试需要真实 DeepSeek：

**不要为了本阶段验证而调用 DeepSeek。**

优先使用 Mock 测试。

---

# 二十一、Git Diff 检查

最后执行：

```bash
git status
git diff
```

检查是否出现无关修改。

特别确认没有：

```text
API Key
Secret
Token
Password
```

进入代码或 Git diff。

`.env` 必须继续被 `.gitignore` 忽略。

---

# 二十二、最终报告格式

完成后必须按照以下格式报告：

```text
# Phase 3.5.1.7 最终报告

## 1. Dimension Migration

Model:
BAAI/bge-m3

Old Dimension:
1536

New Dimension:
1024

Migration:
SUCCESS / BLOCKED

## 2. Configuration

.env:
1024

.env.example:
1024

config default:
1024

## 3. Code

EmbeddingClient:
PASS / FAIL

Hardcoded 1536:
0 / XXX

ORM:
Vector(1024)

## 4. Database

Before:
vector(1536)

After:
vector(1024)

knowledge_chunk row count:
XXX

Embedding non-null count:
XXX

## 5. Database Roundtrip

1024 vector INSERT:
PASS / FAIL

1024 vector SELECT:
PASS / FAIL

Dimension:
1024

## 6. Embedding API

Provider:
siliconflow

Model:
BAAI/bge-m3

HTTP:
200

Actual dimension:
1024

Client validation:
PASS

API calls:
XXX

## 7. Tests

Embedding Unit:
XXX passed

DB:
XXX passed

Full:
XXX passed / XXX skipped / XXX failed

## 8. Regression

Chat:
PASS / FAIL

Health:
PASS / FAIL

Parser:
PASS / FAIL

Chunking:
PASS / FAIL

## 9. Database Data

Old embeddings:
0 / XXX

New embeddings:
0

注意：
本阶段不得写入正式 Embedding。

## 10. Files Modified

列出实际修改的文件。

## 11. Security

API Key:
未进入 Git / 未输出

DeepSeek:
未调用

## 12. Final Status

明确：

Phase 3.5.1.7:
COMPLETE / BLOCKED

下一阶段：
Phase 3.5.2

但如果存在任何失败、数据库数据不为 0、维度不一致或测试失败：

**不要进入 Phase 3.5.2，必须明确 BLOCKED。**
```

---

# 二十三、最重要的停止条件

完成以上工作后：

**立即停止。**

不要自动进入 Phase 3.5.2。

不要开始批量 Embedding。

不要开始知识库导入。

不要开始 RAG。

不要开始 Vector Search。

等待我确认 Phase 3.5.1.7 的最终报告后，再决定下一步。
