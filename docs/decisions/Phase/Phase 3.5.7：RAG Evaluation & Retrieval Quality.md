你现在开始实施项目的 **Phase 3.5.7：RAG Retrieval Evaluation（检索质量评估）**。

## 一、先严格阅读

开始前必须阅读：

* `AGENTS.md`
* `docs/requirements.md`
* `docs/architecture.md`
* `docs/api.md`
* `README.md`
* `backend/app/services/vector_search_service.py`
* `backend/app/services/rag_service.py`
* `backend/app/services/context_builder.py`
* `backend/app/embedding/`
* `backend/app/models/`
* 当前所有相关测试
* `docs/decisions/`

先理解现有实现，再开始修改。

---

# 二、本阶段唯一目标

建立一个**可重复运行的 RAG 检索质量评估机制**。

目标：

```text
测试问题
  ↓
VectorSearchService
  ↓
Top-K 检索结果
  ↓
与预期 document/chunk 对比
  ↓
输出命中情况
```

本阶段暂时不修改 RAG 主链路。

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
* 实时库存
* 数据库写操作
* 权限系统
* 前端页面
* Streaming
* Redis
* Celery
* 消息队列
* Multi-Agent
* Intent Classifier
* Query Rewrite
* Reranker
* Hybrid Search
* BM25
* HNSW / IVFFlat 索引
* PDF/DOCX 解析
* 新的 Embedding 模型
* 更换 DeepSeek
* 修改现有 RAG Prompt
* 修改 `/api/chat` 协议

不要为了“以后扩展”提前实现上述功能。

---

# 四、核心原则

这次不是优化算法。

先建立**基准线（baseline）**。

当前检索：

```text
query
 ↓
BGE-M3
 ↓
pgvector cosine distance
 ↓
Top-K
```

保持这个实现不变。

我们只是增加测试和评估能力。

---

# 五、建立 Evaluation Dataset

新增：

```text
tests/fixtures/rag/evaluation_cases.json
```

建立第一批 WMS 中文测试问题。

至少包含 10 个 case。

建议覆盖：

1. 采购入库
2. 退货入库
3. 单据归档
4. 发货
5. 调拨
6. 盘点
7. 上架
8. 工单
9. 仓库
10. 条码

每个 case 至少包含：

```json
{
  "id": "case_001",
  "query": "采购入库的操作步骤是什么？",
  "expected_document_id": null,
  "expected_keywords": [
    "采购入库"
  ]
}
```

注意：

不要假设数据库中的 document_id。

因为测试数据可能重新初始化。

优先使用：

* expected_keywords
* expected_source
* expected_title

等稳定信息。

---

# 六、Evaluation DTO

新增一个简单的数据结构，例如：

```text
RetrievalEvaluationCase
RetrievalEvaluationResult
```

要求：

* 使用 frozen dataclass
* 不暴露 ORM
* 不暴露 embedding vector
* 不暴露数据库连接信息
* 不暴露 API Key

建议结果至少包含：

```text
case_id
query
top_k
matched
results_count
matched_result_indexes
```

如果你认为更合理，可以增加：

```text
matched_keywords
missing_keywords
```

但不要过度设计。

---

# 七、Evaluation Service

新增：

```text
backend/app/services/rag_evaluation_service.py
```

提供一个简单的评估入口。

例如：

```python
async def evaluate(
    cases: list[RetrievalEvaluationCase],
    *,
    top_k: int = 5,
) -> RetrievalEvaluationSummary:
    ...
```

要求：

### 1. 必须复用现有 VectorSearchService

不能重新实现：

* embedding
* pgvector search
* similarity calculation

必须：

```text
EvaluationService
    ↓
VectorSearchService
```

### 2. 不允许调用 LLM

本阶段只测试：

```text
Query → Embedding → Vector Search
```

不要调用 DeepSeek。

### 3. 每个 case 独立执行

例如：

```text
case 1 → search
case 2 → search
case 3 → search
...
```

如果某个 case 失败，要明确记录失败原因。

---

# 八、匹配规则

第一版不要做复杂 NLP。

使用简单、可解释的规则。

例如：

```text
expected_keywords:
["采购入库"]
```

如果 Top-K 返回结果中任意 chunk 的：

```text
content
```

或者允许的 metadata/title/source 字段中包含：

```text
采购入库
```

则：

```text
matched = true
```

全部关键词都命中才算该 case 成功。

注意：

不要使用 LLM 判断“是否相关”。

---

# 九、Evaluation Summary

增加：

```text
total_cases
matched_cases
failed_cases
hit_rate
```

例如：

```text
total_cases = 10
matched_cases = 8
failed_cases = 2
hit_rate = 0.8
```

hit_rate：

```text
matched_cases / total_cases
```

不要引入复杂指标。

本阶段只建立最基础的：

> Top-K Keyword Hit Rate

---

# 十、测试要求

新增：

```text
tests/test_rag_evaluation_service.py
```

至少测试：

### 1. 全部命中

例如：

```text
10 / 10
hit_rate = 1.0
```

### 2. 部分命中

例如：

```text
8 / 10
hit_rate = 0.8
```

### 3. 全部未命中

```text
0 / 10
hit_rate = 0
```

### 4. 多关键词

例如：

```text
expected_keywords = [
    "采购入库",
    "采购单"
]
```

必须全部命中。

### 5. 空 cases

返回：

```text
total_cases = 0
matched_cases = 0
failed_cases = 0
hit_rate = 0
```

不要除零。

### 6. VectorSearchService 异常

验证：

* evaluation service 不吞异常
* 或者按照明确的 evaluation failure 结构记录

选择一种清晰、一致的行为即可。

### 7. 不调用 LLM

使用 mock 验证：

```text
LLMClient.chat
```

没有被调用。

---

# 十一、真实 Evaluation Test

增加一个可选测试：

```text
tests/test_rag_evaluation_real.py
```

必须默认 skip。

只有：

```text
RUN_REAL_RAG_EVAL=1
```

时才执行。

这个测试：

```text
evaluation dataset
 ↓
真实 BGE-M3
 ↓
真实 PostgreSQL + pgvector
 ↓
真实 VectorSearchService
 ↓
evaluation summary
```

不要调用 DeepSeek。

不要因为这个测试而产生 LLM 费用。

---

# 十二、测试数据问题

如果现有数据库没有足够的知识数据：

不要偷偷修改生产代码。

可以：

* 使用测试 fixture
* 或在 DB integration test 中创建临时 KnowledgeDocument / KnowledgeChunk

测试结束必须清理。

不要依赖开发者当前数据库中的真实数据才能通过单元测试。

---

# 十三、不要修改核心检索逻辑

除非测试发现明确 bug，否则不要修改：

```text
vector_search_service.py
embedding/
rag_service.py
context_builder.py
```

本阶段主要新增：

```text
evaluation dataset
evaluation DTO
evaluation service
evaluation tests
optional real evaluation test
```

---

# 十四、运行测试

至少执行：

```bash
pytest -q
```

如果需要 DB：

```bash
RUN_DB_TESTS=1 pytest -q
```

真实 Evaluation：

```bash
RUN_REAL_RAG_EVAL=1 pytest -q tests/test_rag_evaluation_real.py
```

Lint 也必须执行。

---

# 十五、最终验收标准

只有满足以下条件才能报告：

```text
Phase 3.5.7 COMPLETE
```

要求：

* evaluation dataset ≥ 10 cases
* evaluation service 完成
* VectorSearchService 被复用
* 不调用 LLM
* Top-K Keyword Hit Rate 可计算
* 单元测试全部通过
* DB 测试通过
* 默认不消耗 Embedding API 额度以外的 LLM 额度
* Real Evaluation 默认 skip
* Lint 通过
* 不修改 Chat/RAG API 协议
* 不引入 Agent / Tool / LangGraph / MCP
* 不修改核心 Vector Search 算法

---

# 十六、最终报告格式

完成后只汇报：

```text
Phase 3.5.7 Retrieval Evaluation

1. Implemented
- ...
- ...

2. Evaluation Dataset
- cases: xx

3. Tests
- Unit: xx passed
- DB: xx passed
- Full regression: xx passed / xx skipped / xx failed
- Real evaluation: PASS / SKIP

4. Baseline
- Top-K: x
- Hit Rate: x%

5. Lint
- PASS / FAIL

6. Modified Files
- ...

7. Scope Check
- Agent: NO
- Tool Calling: NO
- LangGraph: NO
- MCP: NO
- WMS API: NO
- LLM in evaluation: NO

8. Status
Phase 3.5.7 COMPLETE
```

**完成本阶段后必须停止，不得自动开始 Phase 3.5.8。**
