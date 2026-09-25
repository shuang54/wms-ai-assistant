现在开始实施 **Phase 3.5.3：Vector Search（向量检索）**。

项目：
`D:\coding\ai\wms-ai-assistant`

本阶段目标：

实现最小可用的向量检索能力：

`用户查询文本 → EmbeddingClient → 1024维 query vector → PostgreSQL + pgvector → 相似度搜索 → 返回 KnowledgeChunk`

## 一、先阅读项目

开始前必须阅读：

* `AGENTS.md`
* `docs/requirements.md`
* `docs/architecture.md`
* `docs/decisions/embedding-dimension.md`

同时检查当前已有实现：

* `backend/app/embedding/`
* `backend/app/models/`
* `backend/app/db/`
* `backend/app/services/knowledge_ingestion_service.py`
* `tests/`

不要重复实现已有的 EmbeddingClient、Parser、Chunker、KnowledgeIngestionService。

---

# 二、本阶段严格范围

只实现：

1. Vector Search Service
2. pgvector 相似度查询
3. 查询结果 DTO / Result
4. 单元测试
5. DB 集成测试
6. 必要的 pgvector 索引

不实现：

* RAG
* Chat + RAG
* LLM Prompt
* Tool Calling
* Agent
* LangGraph
* MCP
* WMS/ERP API
* 权限
* 写操作
* 前端页面
* API Endpoint
* Redis
* Elasticsearch
* Milvus
* Qdrant
* 新的向量数据库
* 批量搜索
* 混合检索 BM25
* reranker
* 多路召回

保持：

**Simple First, Evolve Later**

---

# 三、确认当前向量模型

当前项目已经确定：

Embedding：

`BAAI/bge-m3`

Provider：

`SiliconFlow`

Dimension：

`1024`

数据库：

PostgreSQL + pgvector

`knowledge_chunk.embedding`：

`vector(1024)`

不要重新改 embedding dimension。

不要截断、补零或转换成其他维度。

---

# 四、设计 Vector Search Service

新增：

`backend/app/services/vector_search_service.py`

建议提供类似：

```python
class VectorSearchService:
    async def search(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[VectorSearchResult]:
        ...
```

具体接口可以根据项目现有代码风格调整，但必须保持职责清晰。

Service 负责：

1. 校验 query
2. 调用现有 EmbeddingClient 获取 query embedding
3. 校验 embedding dimension == 1024
4. 查询 `knowledge_chunk`
5. 使用 pgvector cosine distance
6. 按相似度从高到低排序
7. 返回 top_k

---

# 五、相似度算法

使用：

**Cosine Distance**

pgvector 推荐操作符：

```sql
<=> 
```

注意：

pgvector 的 `<=>` 返回的是 cosine distance。

因此：

```text
distance 越小
→ 越相似
```

如果需要向用户返回 similarity，可以：

```text
similarity = 1 - distance
```

不要把 distance 和 similarity 混淆。

---

# 六、数据库查询

核心查询逻辑类似：

```sql
SELECT
    id,
    document_id,
    chunk_index,
    content,
    content_hash,
    token_count,
    metadata,
    embedding <=> :query_embedding AS distance
FROM knowledge_chunk
WHERE embedding IS NOT NULL
ORDER BY embedding <=> :query_embedding
LIMIT :top_k;
```

实际实现必须结合当前 ORM / DB Session 的写法，不要机械复制上面的 SQL。

要求：

* 只搜索 `embedding IS NOT NULL`
* 默认 `top_k=5`
* `top_k` 必须限制合理范围
* 不允许 `top_k <= 0`
* 不允许无限制查询

建议至少限制：

```text
1 <= top_k <= 50
```

如果项目已有统一参数校验方式，优先使用已有方式。

---

# 七、VectorSearchResult

建议新增明确的 Result DTO，例如：

```python
@dataclass(frozen=True)
class VectorSearchResult:
    chunk_id: int
    document_id: int
    chunk_index: int
    content: str
    distance: float
    similarity: float
    metadata: dict
```

具体字段可根据现有项目模型调整。

至少必须能够拿到：

* chunk_id
* document_id
* chunk_index
* content
* distance
* similarity
* metadata

不要直接把 ORM Model 暴露给上层。

---

# 八、空查询

以下情况应该拒绝：

```text
""
"   "
"\n"
```

应该抛出明确的输入异常。

不要调用 Embedding API。

---

# 九、Embedding 异常

如果 EmbeddingClient 失败：

```text
VectorSearchService
        ↓
EmbeddingClient
        ↓
Embedding API Error
```

应该保留项目已有异常体系，不要吞掉异常。

不要返回：

```python
[]
```

来掩盖 API 错误。

---

# 十、Embedding Dimension

必须验证：

```text
len(query_embedding) == settings.embedding.dimension
```

当前应该是：

```text
1024
```

如果维度错误：

* 不允许执行 DB 查询
* 抛出明确异常

禁止：

* truncate
* padding
* reshape
* 自动转换维度

---

# 十一、pgvector 索引

检查当前 `knowledge_chunk.embedding` 是否已经存在向量索引。

如果没有，评估增加一个最小的 cosine index。

优先考虑：

```sql
CREATE INDEX ...
ON knowledge_chunk
USING hnsw (embedding vector_cosine_ops);
```

但注意：

**不要为了“看起来专业”而强制添加索引。**

如果当前数据量非常小，且项目没有 migration framework：

* 可以暂时不创建 index
* 在代码/文档中说明当前阶段数据量较小时无需索引
* 或者提供明确、可重复执行的初始化方式

不要引入 Alembic。

不要创建复杂 migration 系统。

如果增加 index，必须确保不会破坏当前测试和初始化流程。

---

# 十二、测试

新增：

`tests/test_vector_search_service.py`

至少覆盖：

### 1. 基本搜索

输入：

```text
采购入库怎么操作？
```

Mock EmbeddingClient 返回 1024 维向量。

Mock/测试 DB 返回多个 chunk。

验证：

* 返回结果
* top_k 生效
* distance 正确
* similarity = 1 - distance

---

### 2. 相似度排序

准备：

```text
distance = 0.1
distance = 0.3
distance = 0.2
```

最终必须：

```text
0.1
0.2
0.3
```

也就是：

最相似的排在最前面。

---

### 3. top_k

测试：

```text
top_k=1
top_k=3
top_k=5
```

确认不会超过 top_k。

---

### 4. 非法 top_k

测试：

```text
top_k=0
top_k=-1
top_k=51
```

应该明确拒绝。

---

### 5. 空 query

测试：

```text
""
"   "
```

验证：

* 抛出输入异常
* EmbeddingClient 不被调用

---

### 6. Embedding API Error

Mock EmbeddingClient 抛出：

```text
EmbeddingAPIError
```

验证异常向上传递。

---

### 7. Dimension Error

Mock 返回：

```text
1536 dimensions
```

验证：

* 抛出 dimension error
* 不执行 DB 查询

---

### 8. Empty Result

数据库没有匹配 chunk：

应该返回：

```python
[]
```

这属于正常结果，不是异常。

---

### 9. DB Integration Test

使用现有：

```text
RUN_DB_TESTS=1
```

测试真实 PostgreSQL + pgvector。

流程：

1. 插入临时 knowledge_document
2. 插入多个 knowledge_chunk
3. 写入 1024 维 embedding
4. 调用 VectorSearchService
5. 查询
6. 验证排序
7. 验证 top_k
8. 清理测试数据

不要污染现有知识库数据。

---

# 十三、不要调用真实 Embedding API 作为主要测试

单元测试使用 MockEmbeddingClient。

不要因为测试方便而大量调用 SiliconFlow。

最多保留一个可选 smoke test。

真实 API 测试必须继续受到现有环境配置控制。

---

# 十四、测试 fixture

如果需要增加测试数据，可以使用：

`tests/fixtures/knowledge/`

继续使用中文 WMS 场景，例如：

```text
采购入库
采购订单
收货
质检
上架
异常处理
```

但不要为了 Vector Search 修改现有 chunker 或 ingestion fixture 的行为。

---

# 十五、禁止修改已有核心逻辑

本阶段不要修改：

* MarkdownAwareChunker
* Parser
* EmbeddingClient 核心行为
* KnowledgeIngestionService
* 已通过的 Phase 3.5.2 测试

如果发现已有代码确实阻碍 Vector Search：

先停止并报告问题，不要擅自扩大修改范围。

---

# 十六、日志与安全

禁止日志输出：

* LLM API Key
* Embedding API Key
* Authorization Header
* 完整 embedding vector

可以记录：

* query 长度
* top_k
* result count
* 耗时

但当前阶段如果没有统一日志体系，也不要为了日志引入复杂框架。

---

# 十七、回归测试

完成后执行：

PowerShell：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest tests/test_vector_search_service.py -v
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest
```

确认：

```text
0 failed
```

并检查 lint。

---

# 十八、Git Diff

最后检查：

```powershell
git status --short
git diff --stat
git diff
```

确认：

* 没有 `.env` 泄露
* 没有 API Key
* 没有无关修改
* 没有修改前面阶段核心逻辑

---

# 十九、完成标准

只有以下全部满足，才认为 Phase 3.5.3 完成：

* VectorSearchService 完成
* Query → Embedding → pgvector Search 打通
* cosine distance 正确
* similarity 正确
* top_k 正确
* 空 query 正确处理
* embedding dimension 正确校验
* embedding error 正确传播
* DB integration test 通过
* 单元测试通过
* 全量测试通过
* lint 通过
* Git diff 无异常

---

# 二十、最终报告

完成后只汇报：

```text
Phase 3.5.3 Vector Search

1. 实现：
   - VectorSearchService
   - VectorSearchResult
   - pgvector cosine search

2. 测试：
   - Vector Search tests: X passed
   - Full regression: X passed / X skipped / X failed
   - DB tests: PASS/FAIL
   - Lint: PASS/FAIL

3. 数据库：
   - vector dimension: 1024
   - similarity algorithm: cosine
   - index: 有/无

4. 修改文件：
   - ...

5. Git diff：
   - ...

如果全部通过，明确写：
`Phase 3.5.3 COMPLETE`

完成后立即停止。

不要开始 Phase 3.5.4。
```
