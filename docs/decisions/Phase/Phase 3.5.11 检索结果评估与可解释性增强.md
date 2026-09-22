你现在开始执行：

# Phase 3.5.11 —— 检索结果评估与可解释性增强

## 一、阶段目标

在现有：

`Embedding → pgvector Cosine Search → VectorSearchResult → RAG`

基础上，增加一个**轻量级的检索结果分析能力**。

本阶段不是提升算法，而是回答：

1. 一个 query 实际召回了哪些 chunk？
2. 每个 chunk 的 similarity / distance 是多少？
3. 命中了哪个知识主题？
4. Top-K 中是否存在明显的低相关结果？
5. 能否通过离线测试观察不同 Top-K 的检索质量？

目标是为后续是否引入 Reranker / Hybrid Search 提供客观依据。

---

# 二、开始前必须阅读

先阅读：

* `AGENTS.md`
* `docs/requirements.md`
* `docs/architecture.md`
* `README.md`
* `docs/api.md`
* `docs/evaluation/rag-retrieval-analysis.md`

重点阅读：

* `backend/app/services/vector_search_service.py`
* `backend/app/services/rag_service.py`
* `backend/app/services/rag_evaluation_service.py`
* `backend/app/services/context_builder.py`
* `backend/app/services/knowledge_ingestion_service.py`
* `tests/fixtures/rag/evaluation_cases.json`
* `docs/knowledge/wms-basic-operations.md`

不要修改代码之前先理解现有实现。

---

# 三、本阶段严格禁止

本阶段禁止引入：

* Agent
* Tool Calling
* Function Calling
* LangGraph
* MCP
* WMS / ERP API
* SQL Tool
* NL2SQL
* Query Rewrite
* Reranker
* Cross Encoder
* Hybrid Search
* BM25
* HNSW
* IVFFlat
* Redis
* Celery
* 消息队列
* Streaming
* 权限系统
* 前端页面
* HTTP Knowledge API
* PDF / DOCX
* 新 Embedding Model
* 修改 BGE-M3
* 修改 embedding dimension
* 修改 DeepSeek
* 修改 RAG Prompt
* 修改 Chat API
* 修改 `/api/rag/answer`
* 修改知识库生命周期逻辑

本阶段只做：

**Vector Search 的离线评估、结果分析、可解释性。**

---

# 四、核心任务

## 4.1 新增 Retrieval Analysis Service

新增：

`backend/app/services/retrieval_analysis_service.py`

设计一个轻量 Service，例如：

```python
class RetrievalAnalysisService:
    async def analyze(
        self,
        query: str,
        *,
        top_k: int = 5,
    ) -> RetrievalAnalysisResult:
        ...
```

必须复用：

```python
VectorSearchService.search()
```

不要重新实现 pgvector 查询。

---

# 五、DTO

新增 frozen dataclass：

```python
@dataclass(frozen=True)
class RetrievalAnalysisItem:
    rank: int
    chunk_id: int
    document_id: int
    chunk_index: int
    similarity: float
    distance: float
    content_length: int
    metadata: dict
```

以及：

```python
@dataclass(frozen=True)
class RetrievalAnalysisResult:
    query: str
    top_k: int
    result_count: int
    items: tuple[RetrievalAnalysisItem, ...]
    average_similarity: float
    min_similarity: float | None
    max_similarity: float | None
```

要求：

* frozen=True
* 不暴露 ORM
* 不暴露 embedding vector
* 不暴露数据库连接
* 不暴露 API Key
* 不暴露 SQL
* 不保存 query 到数据库

---

# 六、分析逻辑

复用：

```python
VectorSearchService.search(query, top_k=top_k)
```

然后：

```text
VectorSearchResult[]
        ↓
计算 rank
        ↓
提取 similarity
        ↓
计算 content_length
        ↓
统计平均 similarity
        ↓
统计 min / max
        ↓
RetrievalAnalysisResult
```

注意：

`similarity` 和 `distance` 必须直接使用现有 VectorSearchService 的结果。

不要重新计算 cosine similarity。

---

# 七、边界情况

必须处理：

### 1. 空结果

返回：

```text
result_count = 0
items = ()
average_similarity = 0
min_similarity = None
max_similarity = None
```

### 2. 单结果

正确计算：

```text
average = similarity
min = similarity
max = similarity
```

### 3. 多结果

例如：

```text
0.91
0.87
0.72
0.61
```

正确计算：

```text
average = 0.7775
min = 0.61
max = 0.91
```

### 4. top_k

继续复用：

```python
VectorSearchService.MIN_TOP_K
VectorSearchService.MAX_TOP_K
```

不要复制一套新的范围常量。

非法 top_k 应该复用现有 VectorSearchService 的错误语义。

---

# 八、增加离线评估能力

在现有：

`RagEvaluationService`

基础上增加一个非常轻量的能力：

对：

`tests/fixtures/rag/evaluation_cases.json`

执行：

```text
Top-K = 1
Top-K = 3
Top-K = 5
Top-K = 10
```

分别统计：

```text
case_count
hit_count
hit_rate
average_similarity
min_similarity
max_similarity
```

注意：

这里是**离线检索评估**。

不要调用 DeepSeek。

不要生成 RAG answer。

只执行：

```text
query
 ↓
Embedding
 ↓
Vector Search
 ↓
Evaluation
```

---

# 九、不要改变现有 3.5.7 Evaluation

非常重要：

不要破坏：

```python
RagEvaluationService
```

现有 API。

如果需要扩展，必须保持：

```text
现有测试全部通过
```

优先新增一个独立方法，例如：

```python
evaluate_top_k(...)
```

而不是重写原来的 evaluation。

---

# 十、增加测试

新增：

`tests/test_retrieval_analysis_service.py`

至少覆盖：

### Unit Tests

1. 空结果
2. 单结果
3. 多结果
4. rank 正确
5. average similarity
6. min similarity
7. max similarity
8. content_length
9. metadata 保留
10. top_k 传递
11. VectorSearchError 原样传播
12. DTO frozen
13. 不暴露 ORM

---

# 十一、增加 DB Integration Test

新增：

`tests/test_retrieval_analysis_real.py`

默认：

```text
RUN_DB_TESTS=1
```

才能执行。

测试使用当前真实 pgvector。

至少验证：

```text
query
 ↓
BGE-M3 1024
 ↓
pgvector
 ↓
RetrievalAnalysisService
```

检查：

* result_count 正确
* rank 从 1 开始
* similarity 降序
* distance 正确
* similarity 范围合理
* embedding dimension = 1024

不要修改现有真实知识库内容。

---

# 十二、真实评估

增加一个可选测试：

```text
RUN_REAL_RETRIEVAL_ANALYSIS=1
```

使用当前真实：

`docs/knowledge/wms-basic-operations.md`

执行：

```text
Top-K 1
Top-K 3
Top-K 5
Top-K 10
```

输出：

```text
Top-K 1:
  cases: 12
  hit_rate: xx.xx%
  avg_similarity: x.xxx

Top-K 3:
  cases: 12
  hit_rate: xx.xx%
  avg_similarity: x.xxx

Top-K 5:
  cases: 12
  hit_rate: xx.xx%
  avg_similarity: x.xxx

Top-K 10:
  cases: 12
  hit_rate: xx.xx%
  avg_similarity: x.xxx
```

同时输出每个失败 case 的：

```text
case_id
query
expected_keywords
top results
similarity
```

但是：

**不要调用 DeepSeek。**

只使用 BGE-M3 + pgvector。

---

# 十三、重点分析一个问题

最终报告必须回答：

> 当前 WMS 知识库在 Top-K=1、3、5、10 下，检索质量有什么变化？

特别观察：

```text
Top-K 增加
是否提高 hit_rate？
是否引入大量低 similarity chunk？
Top-K=5 是否已经足够？
Top-K=10 是否明显增加噪声？
```

注意：

这里只报告实际实验结果。

不要提前判断“应该使用 Reranker”。

不要修改 Top-K=5 的当前生产默认值。

---

# 十四、RAG 回归测试

完成后必须运行：

```bash
pytest -q
```

然后：

```bash
RUN_DB_TESTS=1 pytest -q
```

然后：

```bash
RUN_REAL_RAG_EVAL=1 pytest -q tests/test_rag_evaluation_real.py
```

如果支持：

```bash
RUN_REAL_RETRIEVAL_ANALYSIS=1 pytest -q tests/test_retrieval_analysis_real.py
```

必须确认：

```text
Unit tests       PASS
DB tests         PASS
Full regression  PASS
RAG evaluation   12/12
```

---

# 十五、数据库保护

绝对不要：

* 删除 document
* 删除 chunk
* 修改现有知识内容
* 修改 embedding
* 修改 vector dimension
* 修改 pgvector extension
* 修改 document_id
* 清空知识库

当前真实知识库必须保持可用。

---

# 十六、安全检查

确认：

```text
API Key         NOT exposed
.env            NOT committed
DATABASE_URL    NOT exposed
Authorization   NOT exposed
embedding       NOT exposed
SQL             NOT exposed
```

不要把完整 embedding vector 打到日志。

日志只允许必要统计信息。

---

# 十七、文件范围

预计新增：

```text
backend/app/services/retrieval_analysis_service.py
tests/test_retrieval_analysis_service.py
tests/test_retrieval_analysis_real.py
```

如确有必要，可以少量修改：

```text
backend/app/services/rag_evaluation_service.py
README.md
docs/api.md
```

但不要为了“完善文档”扩大范围。

---

# 十八、完成后的最终报告

完成后严格按照以下格式汇报：

## Phase 3.5.11 Retrieval Analysis

### 1. Implemented

* RetrievalAnalysisService
* RetrievalAnalysisResult
* RetrievalAnalysisItem
* Top-K evaluation

### 2. Tests

* Unit:
* DB:
* Full regression:
* Real retrieval analysis:

### 3. Retrieval Results

| Top-K | Cases | Hit | Hit Rate | Avg Similarity | Min | Max |
| ----- | ----: | --: | -------: | -------------: | --: | --: |
| 1     |       |     |          |                |     |     |
| 3     |       |     |          |                |     |     |
| 5     |       |     |          |                |     |     |
| 10    |       |     |          |                |     |     |

### 4. Failure Cases

列出实际失败 case。

### 5. RAG Regression

* `/api/rag/answer`
* `/api/chat`
* RAG evaluation

### 6. Database

* document count
* chunk count
* embedding dimension

### 7. Security

* API Key
* .env
* DB URL
* sensitive data

### 8. Scope

确认没有引入：
Agent / Tool / LangGraph / MCP / Reranker / Hybrid Search 等。

### 9. Modified Files

### 10. Status

最后明确：

`Phase 3.5.11 COMPLETE`

---

## 十九、重要

这是一个严格的小阶段。

**完成 Phase 3.5.11 后立即停止。**

不要自行开始：

* Phase 3.5.12
* Reranker
* Hybrid Search
* Agent
* Tool Calling
* LangGraph

等下一步任务。
