# Phase 3.5.1.6：Embedding Dimension Migration Audit

你现在进入 **WMS AI Assistant Phase 3.5.1.6**。

## 一、当前背景

Phase 3.5.1.5 已经完成真实 Embedding API 验证：

* Provider：`siliconflow`
* Model：`BAAI/bge-m3`
* Endpoint：`POST https://api.siliconflow.cn/v1/embeddings`
* 真实 API HTTP Status：200
* 真实返回 Vector Dimension：**1024**
* 中文 WMS 文本 Embedding：成功
* 当前数据库设计：`knowledge_chunk.embedding = vector(1536)`
* 当前配置：`EMBEDDING_DIMENSION=1536`

因此：

```text
当前配置：1536
当前数据库：1536
实际模型：1024
```

三者不兼容。

本阶段决定采用：

```text
BAAI/bge-m3
        ↓
1024 dimensions
        ↓
PostgreSQL + pgvector
        ↓
vector(1024)
```

---

# 二、本阶段目标

本阶段**只进行全项目代码和数据库结构审计，并设计迁移方案**。

目标：

1. 找出项目中所有与 Embedding Dimension 相关的代码
2. 找出所有写死的 `1536`
3. 检查 `knowledge_chunk.embedding`
4. 检查 `EMBEDDING_DIMENSION`
5. 检查 Embedding Client 的维度校验
6. 检查测试代码
7. 检查未来 RAG 代码是否存在维度依赖
8. 设计从 `vector(1536)` → `vector(1024)` 的数据库迁移方案
9. 明确哪些文件需要修改
10. 明确哪些测试需要修改
11. 明确迁移过程中旧向量如何处理
12. **本阶段禁止实际执行数据库 ALTER**

---

# 三、严格禁止事项

本阶段不要做以下任何事情：

* ❌ 不执行 `ALTER TABLE`
* ❌ 不修改数据库结构
* ❌ 不修改 `knowledge_chunk.embedding`
* ❌ 不写入任何 Embedding
* ❌ 不调用 SiliconFlow Embedding API
* ❌ 不调用 DeepSeek
* ❌ 不实现 RAG
* ❌ 不实现批量 Embedding
* ❌ 不实现向量搜索
* ❌ 不修改 Chunking
* ❌ 不修改 Parser
* ❌ 不实现 Tool Calling
* ❌ 不实现 Agent
* ❌ 不实现 LangGraph
* ❌ 不修改 WMS/ERP
* ❌ 不新增无关依赖

**本阶段只能审计、分析、设计、写迁移方案。**

---

# 四、第一步：读取项目规范

先读取：

```text
AGENTS.md
docs/requirements.md
docs/architecture.md
```

同时检查：

```text
backend/app/config.py
backend/app/embedding/
backend/app/rag/
backend/app/db/
tests/
```

理解当前项目架构后再开始审计。

---

# 五、第二步：全项目搜索 Embedding Dimension

对整个项目进行搜索，重点搜索：

```text
1536
EMBEDDING_DIMENSION
embedding
vector(
VECTOR
dimensions
```

特别检查：

```text
backend/
tests/
docs/
scripts/
.env.example
```

输出所有发现的位置，例如：

```text
文件                              行号        内容
------------------------------------------------------------
backend/app/config.py             xx          EMBEDDING_DIMENSION
backend/app/db/...                 xx          vector(1536)
backend/app/embedding/client.py    xx          dimension validation
tests/...                           xx          1536
```

不要只搜索 Python 文件。

---

# 六、第三步：检查数据库模型

重点检查：

```text
backend/app/db/
```

以及：

```text
KnowledgeDocument
KnowledgeChunk
```

确认：

```text
knowledge_chunk.embedding
```

当前到底在哪里定义：

```text
vector(1536)
```

并确认这个维度是：

* 写死的
* 配置驱动的
* ORM 类型定义
* SQLAlchemy TypeDecorator
* PostgreSQL 原生类型
* 其他方式

特别判断：

> `EMBEDDING_DIMENSION` 修改成 1024 后，数据库模型是否会自动变成 vector(1024)？

如果不会，请明确指出原因。

---

# 七、第四步：检查 Embedding Client

检查：

```text
backend/app/embedding/client.py
backend/app/embedding/exceptions.py
backend/app/config.py
tests/test_embedding_client.py
tests/test_embedding_real.py
```

重点确认：

```text
EMBEDDING_DIMENSION=1024
```

以后是否能够正确执行：

```text
API 返回 1024
        ↓
Client 校验 1024
        ↓
通过
```

以及：

```text
API 返回 1536
        ↓
Client 校验 1024
        ↓
EmbeddingDimensionError
```

确认当前代码有没有任何：

```text
1536
```

硬编码。

---

# 八、第五步：检查未来 RAG 兼容性

虽然本阶段不实现 RAG，但要检查当前已有代码是否已经为后续 RAG 预留了错误的维度假设。

重点检查：

```text
backend/app/rag/
```

包括：

```text
parsers/
chunking/
```

以及：

```text
knowledge_chunk
embedding
vector search
```

如果发现未来 RAG 会依赖：

```text
1536
```

必须记录下来。

不要实现 RAG。

---

# 九、第六步：检查测试

检查现有测试：

```text
tests/
```

重点寻找：

```text
1536
vector(1536)
EMBEDDING_DIMENSION
EmbeddingDimensionError
```

分类：

### A. 必须修改

例如：

```text
assert dimension == 1536
```

这种以后应该改成：

```text
assert dimension == settings.embedding.dimension
```

或者明确使用：

```text
1024
```

具体根据项目当前测试设计决定。

### B. 不需要修改

例如测试本身只是测试：

```text
EmbeddingDimensionError
```

则保留。

### C. 需要增加

如果当前没有覆盖：

```text
1024 → success
1536 → fail
```

请在迁移方案中提出需要增加的测试。

但是：

**本阶段不要实际修改测试代码。**

---

# 十、第七步：数据库迁移方案设计

设计：

```text
vector(1536)
      ↓
vector(1024)
```

迁移方案。

必须回答以下问题：

### 1. 当前是否存在真实 Embedding 数据？

检查数据库中的：

```text
knowledge_chunk
```

是否已经存在：

```text
embedding IS NOT NULL
```

如果数据库测试环境中存在数据，只做查询统计：

```sql
SELECT COUNT(*) ...
```

**禁止修改数据。**

### 2. 如果存在旧的 1536 维向量怎么办？

明确说明：

```text
1536维旧向量
        ↓
不能直接转换为1024维
        ↓
必须重新 Embedding
```

不要设计：

```text
截断
补零
降维后直接复用
```

作为正式方案。

说明原因。

### 3. 正式迁移流程

设计类似：

```text
停止 RAG 写入
        ↓
确认 Embedding Model
        ↓
确认 EMBEDDING_DIMENSION=1024
        ↓
数据库迁移 vector(1536) → vector(1024)
        ↓
旧 Embedding 数据清理/重新生成
        ↓
重新 Embedding
        ↓
写入 1024 维向量
        ↓
向量搜索测试
        ↓
RAG 验证
```

但根据当前项目实际情况调整。

---

# 十一、重点：判断是否需要 Alembic

当前项目 Phase 3.1 明确：

> 暂时没有引入 Alembic。

请检查当前数据库初始化方式。

判断：

```text
是否应该现在引入 Alembic？
```

不要直接引入。

请给出判断：

### 如果当前项目还没有正式 Migration 机制

建议：

```text
本次 Phase 3.5.1.6
只设计 migration SQL / migration plan

后续正式数据库迁移时
再决定是否引入 Alembic
```

如果你认为现在必须引入 Alembic，必须说明原因。

不要为了一个维度修改就擅自扩大项目范围。

---

# 十二、输出迁移设计文档

本阶段允许新增：

```text
docs/decisions/embedding-dimension.md
```

或者：

```text
docs/embedding-migration.md
```

如果项目已有 ADR 规范，优先放：

```text
docs/decisions/
```

文档至少包含：

## 1. 背景

为什么从 1536 改成 1024。

## 2. 当前状态

```text
Model = BAAI/bge-m3
Actual Dimension = 1024
DB Dimension = 1536
```

## 3. 决策

```text
Embedding Dimension = 1024
```

## 4. 影响范围

列出：

```text
配置
数据库模型
数据库结构
Embedding Client
测试
未来 RAG
```

## 5. 数据迁移策略

说明旧向量是否存在，以及如何处理。

## 6. 数据库迁移 SQL

**只写方案示例，不执行。**

例如：

```sql
-- 示例，仅用于迁移方案
-- 不允许本阶段执行

ALTER TABLE knowledge_chunk
ALTER COLUMN embedding TYPE vector(1024);
```

但是如果 PostgreSQL/pgvector 对这种直接转换存在限制，请不要假设可直接执行。

请根据当前实际数据库结构，判断应该采用：

```text
直接 ALTER
```

还是：

```text
新增 embedding_new
↓
重新生成
↓
切换字段
↓
删除旧字段
```

并说明原因。

## 7. 回滚方案

说明如何从：

```text
1024
```

回退。

特别说明：

> 1024维向量不能无损恢复成原来的1536维向量，因此如果旧数据很重要，需要提前备份/重新生成策略。

## 8. 测试计划

至少包括：

```text
Embedding 1024维验证
数据库 vector(1024) 验证
向量写入
向量读取
维度错误校验
未来 cosine similarity
```

---

# 十三、最终必须给出明确结论

最终报告严格按照下面格式：

```text
# Phase 3.5.1.6 最终报告

## 1. Embedding Dimension
Model:
BAAI/bge-m3

Actual Dimension:
1024

Decision:
vector(1024)

## 2. 1536 硬编码检查

发现 X 处。

逐项列出。

## 3. 数据库检查

knowledge_chunk.embedding:
vector(1536)

当前 embedding 数据：
有/无

数量：
XXX

## 4. Code Compatibility

Embedding Client:
兼容/需要修改

Config:
兼容/需要修改

RAG:
兼容/需要修改

Tests:
兼容/需要修改

## 5. Migration Strategy

详细说明。

## 6. Files That Need Modification

列出：

- xxx
- xxx
- xxx

## 7. Files That Do NOT Need Modification

列出。

## 8. Risks

列出主要风险。

## 9. Recommended Next Step

明确说明：

下一阶段应该执行什么。

## 10. 本阶段执行情况

数据库结构：
未修改

数据库数据：
未修改

Embedding API：
未调用

DeepSeek：
未调用

RAG：
未实现

Tool Calling：
未实现

Agent：
未实现
```

---

# 十四、最重要的执行规则

本阶段完成后：

**立即停止。**

不要自动进入 Phase 3.5.1.7。

不要执行数据库迁移。

不要修改数据库。

不要重新生成 Embedding。

不要实现 RAG。

等待我确认迁移方案后，再进入下一阶段。

最终只报告：

1. 搜索到了哪些 1536
2. 哪些地方需要修改
3. 数据库当前情况
4. 迁移方案
5. 风险
6. 下一阶段建议

然后停止。
