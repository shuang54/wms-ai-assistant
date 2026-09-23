你现在开始执行：

# Phase 3.5.12 —— Reranker 离线实验

## 一、阶段目标

基于当前已经稳定的：

```text
Query
 ↓
BGE-M3 Embedding
 ↓
pgvector Vector Search
 ↓
Top-K
```

增加一个**独立的 Reranker 离线实验能力**。

本阶段只回答一个问题：

> 对当前 WMS 知识库，Reranker 是否能够改善 Vector Search 的结果排序？

不要直接修改生产 RAG。

最终比较：

```text
Vector Search 原始排序
vs
Vector Search + Reranker 重排序
```

使用当前已有的：

`tests/fixtures/rag/evaluation_cases.json`

以及真实 WMS 知识库：

`docs/knowledge/wms-basic-operations.md`

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
* `backend/app/services/retrieval_analysis_service.py`
* `backend/app/services/rag_evaluation_service.py`
* `backend/app/services/rag_service.py`
* `backend/app/services/context_builder.py`
* `backend/app/services/knowledge_ingestion_service.py`
* `tests/fixtures/rag/evaluation_cases.json`
* `docs/knowledge/wms-basic-operations.md`

先理解现有检索与评估实现，再开始修改。

---

# 三、本阶段严格禁止

本阶段禁止：

* 修改 `/api/chat`
* 修改 `/api/rag/answer`
* 修改 `RagService`
* 修改 VectorSearchService 的生产逻辑
* 修改默认 Top-K=5
* 修改 RAG Prompt
* Agent
* Tool Calling
* Function Calling
* LangGraph
* MCP
* WMS / ERP API
* SQL Tool
* NL2SQL
* Query Rewrite
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
* Knowledge HTTP API
* 知识库生命周期修改
* 新增写入能力
* 修改现有知识库
* 修改 BGE-M3 embedding
* 修改 embedding dimension
* 修改 DeepSeek

特别注意：

**本阶段不能把 Reranker 接入生产 RAG。**

---

# 四、Reranker 技术方案

优先采用一个本地 Cross-Encoder Reranker。

推荐：

```text
BAAI/bge-reranker-v2-m3
```

如果当前 Python / Windows / RTX 3080 16GB 环境无法稳定运行该模型：

不要擅自换成其他模型。

先分析具体原因，并在最终报告说明。

---

# 五、依赖

先检查当前环境是否已经存在：

```text
torch
transformers
sentence-transformers
```

不要重复安装已经存在的依赖。

如果需要新增依赖，只允许增加 Reranker 必需依赖。

不要顺便升级整个 Python 环境。

不要升级 FastAPI / SQLAlchemy / pgvector 等无关依赖。

---

# 六、新增 Reranker Client

新增：

```text
backend/app/reranker/
```

建议结构：

```text
backend/app/reranker/
├── __init__.py
├── client.py
└── exceptions.py
```

设计一个最小抽象：

```python
class RerankerClient:
    async def rerank(
        self,
        query: str,
        documents: list[str],
    ) -> list[float]:
        ...
```

返回：

```text
每个 document 对应一个 relevance score
```

要求：

* 输入 query
* 输入 candidate documents
* 输出与 documents 一一对应的 score
* 不访问数据库
* 不知道 WMS 业务
* 不调用 DeepSeek
* 不调用 VectorSearchService
* 不保存结果
* 不写数据库

---

# 七、Reranker 配置

增加独立配置：

```text
RERANKER_ENABLED
RERANKER_MODEL
RERANKER_DEVICE
RERANKER_MAX_LENGTH
```

默认：

```text
RERANKER_ENABLED=false
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
```

设备：

优先：

```text
cuda
```

如果 CUDA 不可用：

允许显式使用：

```text
cpu
```

但不要自动改变生产配置。

不要把 API Key / token 写入代码。

---

# 八、不要接入生产 RAG

再次强调：

以下代码不能发生变化：

```text
RagService
ChatService
/api/chat
/api/rag/answer
VectorSearchService
ContextBuilder
```

Reranker 只能作为：

```text
offline experiment
```

存在。

---

# 九、新增 Reranker Evaluation Service

新增：

```text
backend/app/services/reranker_evaluation_service.py
```

职责：

```text
Evaluation Case
      ↓
Vector Search
      ↓
Candidate Top-K
      ↓
Reranker
      ↓
Re-ranked results
      ↓
Evaluation
```

建议：

```python
class RerankerEvaluationService:
    async def evaluate(
        self,
        cases,
        *,
        retrieval_top_k: int = 10,
        rerank_top_k: int = 5,
    ) -> ...
```

这里：

```text
retrieval_top_k = 10
```

表示先从向量数据库召回 10 个候选。

然后：

```text
rerank_top_k = 5
```

表示 Reranker 对这 10 个候选排序，再取前 5。

---

# 十、为什么先 Top-K=10

当前 Phase 3.5.11 已经得到：

```text
K=1   83.33%
K=3   100%
K=5   100%
K=10  100%
```

所以这次实验不要改变召回阶段。

固定：

```text
Vector Search Top-K = 10
```

然后观察 Reranker 能否改善前 5 个结果的排序。

---

# 十一、必须同时保留 Baseline

每个 case 必须计算：

## Baseline

```text
Vector Search Top-10
        ↓
直接取前 5
```

## Reranked

```text
Vector Search Top-10
        ↓
Reranker
        ↓
按 reranker score 排序
        ↓
取前 5
```

然后比较。

---

# 十二、评估指标

至少实现：

### 1. Hit Rate@5

判断 expected keywords 是否出现在 Top-5。

---

### 2. MRR@5

如果正确 chunk 第：

```text
1 位 → 1.0
2 位 → 0.5
3 位 → 0.333
4 位 → 0.25
5 位 → 0.2
```

如果 Top-5 没有命中：

```text
0
```

---

### 3. Hit Rate@1

比较：

```text
Baseline Top-1
vs
Reranked Top-1
```

这是本次实验特别重要的指标。

因为当前：

```text
Vector Top-1 = 83.33%
```

需要观察 Reranker 能否把 case_003 / case_011 从 rank 2 推到 rank 1。

---

# 十三、结果 DTO

新增 frozen DTO，例如：

```python
@dataclass(frozen=True)
class RerankedResult:
    original_rank: int
    rerank_rank: int
    chunk_id: int
    document_id: int
    chunk_index: int
    vector_similarity: float
    reranker_score: float
```

不要包含：

* embedding
* API Key
* DB connection
* SQL

---

新增 summary：

```python
@dataclass(frozen=True)
class RerankerEvaluationSummary:
    case_count: int

    baseline_hit_rate_at_1: float
    reranked_hit_rate_at_1: float

    baseline_hit_rate_at_5: float
    reranked_hit_rate_at_5: float

    baseline_mrr_at_5: float
    reranked_mrr_at_5: float
```

可以增加：

```text
improved_cases
degraded_cases
unchanged_cases
```

但不要增加无意义指标。

---

# 十四、重点关注 case_003 / case_011

Phase 3.5.11 已经发现：

```text
case_003
单据归档在哪里处理？

case_011
采购单如何创建？
```

两者：

```text
Vector Search Top-1 → 未命中
Vector Search Top-2 → 正确 chunk
```

因此必须在 Reranker 实验结果中单独展示：

```text
case_id
query

Vector rank
Reranker rank

Vector similarity
Reranker score
```

重点观察：

> Reranker 是否把正确 chunk 从 rank 2 提升到 rank 1。

---

# 十五、测试

新增：

```text
tests/test_reranker_client.py
tests/test_reranker_evaluation_service.py
```

至少覆盖：

### Client

1. 空 query
2. 空 documents
3. 单 document
4. 多 documents
5. 返回 score 数量与 documents 一致
6. 模型异常
7. DTO / exception
8. 不访问 DB
9. 不暴露 embedding

---

### Evaluation

1. baseline 正确
2. reranker 排序正确
3. Top-K=10 candidate
4. rerank_top_k=5
5. Hit Rate@1
6. Hit Rate@5
7. MRR@5
8. case_003
9. case_011
10. empty cases
11. reranker error
12. 不修改原 evaluation 行为

---

# 十六、模型下载与真实实验

新增：

```text
tests/test_reranker_real.py
```

默认：

```text
RUN_REAL_RERANKER=0
```

只有：

```text
RUN_REAL_RERANKER=1
```

才执行真实模型。

使用：

```text
BAAI/bge-reranker-v2-m3
```

和：

```text
RTX 3080 16GB
```

优先 CUDA。

真实实验：

```text
12 evaluation cases
Vector Top-K = 10
Rerank Top-K = 5
```

不要调用 DeepSeek。

---

# 十七、重要性能要求

不要每个 case 重复加载模型。

错误：

```text
case_001 → load model
case_002 → load model
case_003 → load model
```

正确：

```text
程序启动
 ↓
load Reranker
 ↓
12 cases 共用
```

同时：

不要并发加载多个模型实例。

---

# 十八、候选文本

Reranker 的输入应该是：

```text
query
+
candidate chunk.content
```

但是：

**只在 Reranker 内部使用。**

不要把完整 chunk content 放进：

```text
RerankedResult
```

也不要写数据库。

---

# 十九、模型下载问题

如果 HuggingFace 模型下载失败：

不要擅自修改模型。

检查并报告：

* 网络
* CUDA
* transformers
* torch
* model loading error

如果真实模型无法运行：

允许 Unit Test + Mock Reranker 完成。

但是：

最终不能声称真实 Reranker 实验完成。

必须明确：

```text
Real Reranker: BLOCKED
```

---

# 二十、真实实验输出

运行：

```bash
RUN_REAL_RERANKER=1 pytest -q tests/test_reranker_real.py
```

最终报告必须包含：

```text
Baseline:

Hit@1:
Hit@5:
MRR@5:

Reranked:

Hit@1:
Hit@5:
MRR@5:
```

以及：

```text
case_003:
Vector rank:
Reranker rank:

case_011:
Vector rank:
Reranker rank:
```

---

# 二十一、必须回答的问题

最终不要只说“Reranker 成功”。

必须根据真实实验结果回答：

### 1.

Reranker 是否提高 Hit@1？

### 2.

Reranker 是否提高 MRR@5？

### 3.

Reranker 是否改变 Hit@5？

### 4.

case_003 是否改善？

### 5.

case_011 是否改善？

### 6.

有多少 case：

```text
improved
unchanged
degraded
```

### 7.

Reranker 是否值得进入下一阶段生产 RAG？

这里只能**陈述实验数据和影响**，不要凭感觉下结论。

---

# 二十二、生产代码保护

以下文件本阶段不得修改：

```text
backend/app/services/vector_search_service.py
backend/app/services/rag_service.py
backend/app/services/chat_service.py
backend/app/api/chat.py
backend/app/api/rag.py
backend/app/services/context_builder.py
```

如果发现必须修改其中任何一个才能完成本阶段：

**停止并报告，不要自行修改。**

---

# 二十三、数据库保护

本阶段：

```text
只读
```

禁止：

* INSERT knowledge_document
* UPDATE knowledge_document
* DELETE knowledge_document
* INSERT knowledge_chunk
* UPDATE knowledge_chunk
* DELETE knowledge_chunk

现有：

```text
document_id=1
chunk_count=11
dimension=1024
```

必须保持不变。

---

# 二十四、安全

最终检查：

```text
API Key       NOT exposed
.env          NOT committed
DB URL        NOT exposed
Authorization NOT exposed
embedding     NOT exposed
SQL           NOT exposed
```

模型缓存路径如果包含本地用户目录：

不要写进日志。

---

# 二十五、测试命令

至少执行：

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

最后：

```bash
RUN_REAL_RERANKER=1 pytest -q tests/test_reranker_real.py
```

如果真实 Reranker 因环境原因无法运行，必须明确报告 BLOCKED，不允许伪造实验结果。

---

# 二十六、最终报告格式

完成后严格按照：

## Phase 3.5.12 Reranker Offline Evaluation

### 1. Implemented

### 2. Model

* model:
* device:
* torch:
* transformers:

### 3. Tests

* Unit:
* DB:
* Full regression:
* Real RAG evaluation:
* Real Reranker:

### 4. Results

| Metric | Baseline | Reranked |
| ------ | -------: | -------: |
| Hit@1  |          |          |
| Hit@5  |          |          |
| MRR@5  |          |          |

### 5. Case Analysis

| Case     | Vector Rank | Reranker Rank | Vector Similarity | Reranker Score |
| -------- | ----------: | ------------: | ----------------: | -------------: |
| case_003 |             |               |                   |                |
| case_011 |             |               |                   |                |

### 6. Case Changes

* improved:
* unchanged:
* degraded:

### 7. Database Integrity

* document count:
* chunk count:
* embedding dimension:

### 8. RAG Regression

* `/api/rag/answer`
* `/api/chat`
* RAG evaluation

### 9. Security

### 10. Scope

确认没有修改生产 RAG、Chat API、Vector Search 等。

### 11. Modified Files

### 12. Status

最后：

`Phase 3.5.12 COMPLETE`

---

# 二十七、最重要的停止条件

完成 Phase 3.5.12 后：

**立即停止。**

不要自行开始：

* Phase 3.5.13
* Reranker Production Integration
* Hybrid Search
* Agent
* Tool Calling
* LangGraph
* MCP

等后续阶段。
