你现在开始执行：

# Phase 3.5.13 —— Reranker 性能基准与 Batch 优化

## 一、阶段目标

基于 Phase 3.5.12 已完成的：

`BAAI/bge-reranker-v2-m3`

进行**离线性能基准与最小 Batch 优化**。

本阶段只研究：

> Reranker 在当前环境下到底有多慢，以及 Batch / Candidate 数量变化是否能够降低平均推理成本。

不要把 Reranker 接入生产 RAG。

---

# 二、开始前必须阅读

阅读：

* `AGENTS.md`
* `docs/requirements.md`
* `docs/architecture.md`
* `README.md`

重点阅读：

* `backend/app/reranker/client.py`
* `backend/app/reranker/exceptions.py`
* `backend/app/config.py`
* `backend/app/services/reranker_evaluation_service.py`
* `tests/test_reranker_client.py`
* `tests/test_reranker_evaluation_service.py`
* `tests/test_reranker_real.py`
* `tests/fixtures/rag/evaluation_cases.json`

理解 Phase 3.5.12 当前实现后再修改。

---

# 三、严格禁止

本阶段禁止：

* 修改 `/api/chat`
* 修改 `/api/rag/answer`
* 修改 `RagService`
* 修改 `ChatService`
* 修改 `VectorSearchService`
* 修改 `ContextBuilder`
* 修改 RAG Prompt
* 修改默认生产 Top-K
* Reranker 生产集成
* Agent
* Tool Calling
* Function Calling
* LangGraph
* MCP
* Hybrid Search
* BM25
* Query Rewrite
* WMS / ERP API
* SQL Tool
* NL2SQL
* Redis
* Celery
* Streaming
* 前端
* HTTP Knowledge API
* 修改知识库内容
* 修改知识库生命周期
* 修改 Embedding Model
* 修改 Embedding Dimension
* 修改 DeepSeek

本阶段只做：

```text
Reranker
性能基准
+
Batch 推理优化
+
离线实验
```

---

# 四、先做环境基线

真实实验开始前检查：

```text
Python
PyTorch
Transformers
CUDA availability
CUDA device count
CUDA device name
```

特别检查：

```python
torch.cuda.is_available()
```

如果：

```text
False
```

必须记录：

```text
CUDA unavailable
device = cpu
```

不要修改驱动。

不要安装 CUDA。

不要为了本阶段升级 PyTorch。

---

# 五、检查当前 Reranker Client

检查：

```text
backend/app/reranker/client.py
```

重点确认：

1. 模型是否单例
2. 是否懒加载
3. 是否重复加载模型
4. 是否每次 rerank 都重新创建 tokenizer
5. 是否使用 `torch.no_grad()` 或 inference_mode
6. 是否支持 batch
7. 是否在 CPU 上产生不必要的数据复制
8. max_length 是否配置化

---

# 六、增加 Batch 能力

如果当前接口类似：

```python
rerank(
    query,
    documents
)
```

保持兼容。

允许内部实现优化为：

```text
query
+
documents[]
        ↓
batch pairs
        ↓
model
        ↓
scores[]
```

不要改变现有外部 API 的语义。

如果当前已经支持 batch：

不要重复重构，只补测试和 benchmark。

---

# 七、Batch 设计

Reranker 输入：

```text
(query, document)
```

例如：

```text
query = "采购单如何创建？"

documents = [
    chunk1,
    chunk2,
    chunk3,
    ...
]
```

应该构造：

```text
[
    [query, chunk1],
    [query, chunk2],
    [query, chunk3],
    ...
]
```

然后一次 batch 推理。

---

# 八、Batch Size

新增配置：

```text
RERANKER_BATCH_SIZE
```

默认：

```text
RERANKER_BATCH_SIZE=8
```

允许：

```text
1
2
4
8
16
```

如果 CPU 内存或模型限制导致 16 不稳定：

允许降低。

不要自动修改配置。

---

# 九、必须保证结果一致性

Batch 优化不能改变排序结果。

对于同一：

```text
query
+
documents
```

比较：

```text
batch_size=1
vs
batch_size=8
```

要求：

* documents 数量一致
* scores 数量一致
* score 顺序一致
* score 数值误差在合理范围内
* 排名一致

允许非常小的浮点误差。

---

# 十、增加性能 Benchmark Service

新增：

```text
backend/app/services/reranker_benchmark_service.py
```

只负责：

```text
测量
```

不负责业务。

建议 DTO：

```python
@dataclass(frozen=True)
class RerankerBenchmarkResult:
    document_count: int
    batch_size: int
    warmup_runs: int
    measured_runs: int
    model_load_seconds: float
    total_seconds: float
    average_seconds: float
    average_ms_per_document: float
```

可以增加：

```text
p50_ms
p95_ms
```

如果实现简单就加。

不要为了 benchmark 引入复杂性能库。

---

# 十一、Benchmark 必须区分模型加载

第一次：

```text
load model
```

与后续：

```text
inference
```

完全不同。

因此至少记录：

```text
model_load_seconds
```

以及：

```text
inference_seconds
```

不能把模型加载时间混入每个 case 的平均推理时间。

---

# 十二、Warmup

Benchmark：

```text
warmup_runs = 1
```

Warmup 不计入正式平均。

正式：

```text
measured_runs = 3
```

如果 CPU 太慢，可以：

```text
measured_runs = 2
```

但必须在报告说明。

---

# 十三、Candidate 数量实验

使用真实模型执行：

```text
document_count =
1
3
5
10
20
```

如果当前知识库只有 11 chunks：

不要人为修改知识库。

对于：

```text
20
```

可以使用：

```text
重复已有候选文本
```

但必须明确标记为：

```text
synthetic benchmark
```

不要把 synthetic benchmark 当作真实检索实验。

优先真实测试：

```text
1 / 3 / 5 / 10 / 11
```

---

# 十四、Batch Size 实验

至少测试：

```text
batch_size=1
batch_size=4
batch_size=8
```

如果机器资源允许：

```text
batch_size=16
```

否则跳过并说明原因。

---

# 十五、真实实验数据

使用当前真实 WMS 知识库：

```text
document_id=1
chunk_count=11
embedding dimension=1024
```

只读。

不要修改任何数据。

选择几个真实 query：

```text
采购入库如何操作？
销售出库怎么处理？
单据归档在哪里处理？
采购单如何创建？
仓库库位如何管理？
```

不需要调用 DeepSeek。

---

# 十六、完整 Benchmark Matrix

最终至少得到：

| Candidates | Batch | Avg ms | P50 | P95 |
| ---------: | ----: | -----: | --: | --: |
|          1 |     1 |        |     |     |
|          3 |     1 |        |     |     |
|          5 |     1 |        |     |     |
|         10 |     1 |        |     |     |
|         11 |     1 |        |     |     |
|          1 |     4 |        |     |     |
|          3 |     4 |        |     |     |
|          5 |     4 |        |     |     |
|         10 |     4 |        |     |     |
|         11 |     4 |        |     |     |
|          1 |     8 |        |     |     |
|          3 |     8 |        |     |     |
|          5 |     8 |        |     |     |
|         10 |     8 |        |     |     |
|         11 |     8 |        |     |     |

如果某些组合不适合执行，可以减少，但必须说明原因。

---

# 十七、重点测量两个指标

### 1. 单文档成本

```text
average_ms_per_document
```

### 2. 一次 Query 的总耗时

例如：

```text
Top-K=10
Reranker batch=8

total = xxx ms
```

未来真正进入 RAG 时，我们关心的是：

```text
用户 Query
 ↓
Embedding
 ↓
Vector Search
 ↓
Reranker
 ↓
LLM
```

所以必须能够估算：

> Reranker 本身会增加多少延迟。

---

# 十八、Batch Correctness Test

新增：

```text
tests/test_reranker_batch.py
```

至少测试：

1. batch size=1
2. batch size=4
3. batch size=8
4. score 数量一致
5. score 顺序一致
6. ranking 一致
7. 单 document
8. 空 documents
9. 大于 batch size 的 documents
10. 模型异常
11. 不重复加载模型

---

# 十九、真实 Benchmark

新增：

```text
tests/test_reranker_benchmark_real.py
```

默认：

```text
RUN_REAL_RERANKER_BENCHMARK=0
```

只有：

```text
RUN_REAL_RERANKER_BENCHMARK=1
```

才执行。

真实实验必须使用：

```text
BAAI/bge-reranker-v2-m3
```

不要换模型。

---

# 二十、生产知识库保护

当前真实知识库：

```text
document_id=1
11 chunks
1024 dimensions
```

必须保持：

```text
document count = 1
chunk count = 11
```

Benchmark：

**只读。**

禁止：

* INSERT
* UPDATE
* DELETE
* TRUNCATE

尤其不要使用：

```sql
TRUNCATE knowledge_chunk
```

---

# 二十一、测试

完成后执行：

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
RUN_REAL_RERANKER_BENCHMARK=1 pytest -q tests/test_reranker_benchmark_real.py
```

如果真实 benchmark 很慢：

不要伪造结果。

可以减少 measured_runs，但必须报告实际次数。

---

# 二十二、回归要求

必须确认：

```text
Unit tests       PASS
DB tests         PASS
RAG evaluation   12/12
/api/chat        PASS
/api/rag/answer  PASS
```

Reranker Benchmark 不得改变生产 RAG 行为。

---

# 二十三、安全检查

确认：

```text
API Key       NOT exposed
.env          NOT committed
DB URL        NOT exposed
Authorization NOT exposed
Embedding     NOT logged
SQL           NOT logged
```

日志不要打印：

* query 全文
* chunk 全文
* embedding
* model path 中的敏感路径信息

Benchmark 输出只保留统计信息。

---

# 二十四、性能优化边界

只允许进行：

```text
Batch inference
torch.inference_mode()
model.eval()
避免重复加载 tokenizer/model
```

如果发现其他明显问题：

可以修复。

但不要做：

* 模型量化
* ONNX
* TensorRT
* OpenVINO
* 模型蒸馏
* 换模型
* GPU 驱动修改
* CUDA 环境重装

这些全部留到后续专门阶段。

---

# 二十五、最终必须回答

根据真实 benchmark：

### 1.

Batch=1 / 4 / 8 的性能差异是多少？

### 2.

Candidate 从 1 → 11，耗时如何增长？

### 3.

Batch 是否明显降低：

```text
ms/document
```

### 4.

当前 CPU 环境下：

```text
Top-K=5
Top-K=10
```

分别增加多少毫秒？

### 5.

模型加载耗时是多少？

### 6.

当前 Reranker 是否存在明显性能瓶颈？

这里只报告实验数据。

不要自行决定生产集成。

---

# 二十六、最终报告格式

完成后严格：

## Phase 3.5.13 Reranker Performance Benchmark

### 1. Environment

* Python:
* PyTorch:
* Transformers:
* CUDA:
* Device:
* Model:

### 2. Implementation

* Batch inference:
* inference_mode:
* model singleton:
* lazy loading:
* batch size config:

### 3. Correctness

* batch=1 vs batch=4:
* batch=1 vs batch=8:
* ranking consistency:
* score consistency:

### 4. Benchmark

| Candidates | Batch | Avg ms | P50 | P95 | ms/doc |
| ---------: | ----: | -----: | --: | --: | -----: |
|          1 |     1 |        |     |     |        |
|          3 |     1 |        |     |     |        |
|          5 |     1 |        |     |     |        |
|         10 |     1 |        |     |     |        |
|         11 |     1 |        |     |     |        |
|          1 |     4 |        |     |     |        |
|          3 |     4 |        |     |     |        |
|          5 |     4 |        |     |     |        |
|         10 |     4 |        |     |     |        |
|         11 |     4 |        |     |     |        |
|          1 |     8 |        |     |     |        |
|          3 |     8 |        |     |     |        |
|          5 |     8 |        |     |     |        |
|         10 |     8 |        |     |     |        |
|         11 |     8 |        |     |     |        |

### 5. Model Load

* model_load_seconds:

### 6. Tests

* Unit:
* DB:
* Full regression:
* RAG evaluation:
* Real benchmark:

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

确认没有：

* Reranker production integration
* Vector Search modification
* RAG modification
* Chat modification
* API modification
* Agent
* Tool Calling
* LangGraph
* MCP
* Hybrid Search

### 11. Modified Files

### 12. Status

最后：

`Phase 3.5.13 COMPLETE`

---

# 二十七、停止条件

完成 Phase 3.5.13 后：

**立即停止。**

不要自行进入：

* Reranker Production Integration
* Hybrid Search
* Agent
* Tool Calling
* LangGraph
* MCP

等后续阶段。
