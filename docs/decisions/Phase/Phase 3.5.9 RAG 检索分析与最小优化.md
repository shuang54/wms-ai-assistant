你现在开始实施项目的 **Phase 3.5.9：RAG Retrieval Analysis & Optimization**。

# 一、阶段背景

当前项目已经完成：

```text
Phase 3.5.1  Embedding
Phase 3.5.2  Knowledge Ingestion
Phase 3.5.3  Vector Search
Phase 3.5.4  RAG
Phase 3.5.5  RAG API
Phase 3.5.6  Chat + RAG
Phase 3.5.7  Retrieval Evaluation
Phase 3.5.8  First Real WMS Knowledge Base
```

当前真实知识库：

```text
document: 1
chunks: 10
embedding: 10
dimension: 1024
```

当前 Retrieval Baseline：

```text
Top-K = 5
Evaluation Cases = 12
Matched = 10
Failed = 2
Hit Rate = 83.33%
```

失败案例：

```text
case_003
case_011
```

本阶段唯一目标：

> **分析为什么 case_003 和 case_011 没有命中，并在确认存在明确问题后进行最小化优化。**

---

# 二、开始前必须阅读

必须先阅读：

```text
AGENTS.md
docs/requirements.md
docs/architecture.md
docs/api.md
README.md

docs/knowledge/wms-basic-operations.md
tests/fixtures/rag/evaluation_cases.json

backend/app/services/vector_search_service.py
backend/app/services/rag_evaluation_service.py
backend/app/services/rag_service.py
backend/app/services/context_builder.py
backend/app/services/knowledge_ingestion_service.py

backend/app/chunking/
backend/app/embedding/
backend/app/models/

tests/test_rag_evaluation_service.py
tests/test_rag_evaluation_real.py
```

同时阅读：

```text
docs/decisions/
```

理解之前已经做出的技术决策。

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
* 数据库自然语言查询
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

也不要：

* 修改 `/api/chat`
* 修改 `/api/rag/answer`
* 修改 LLM Prompt
* 为了提高分数而修改 evaluation cases
* 删除失败 case
* 降低关键词匹配标准
* 人为提高 hit rate

---

# 四、先做 Failure Analysis

在修改任何代码之前，必须先分析：

```text
case_003
case_011
```

对于每个 case，记录：

```text
query
expected_keywords
Top-5 results
distance
similarity
chunk_index
chunk content
document metadata
```

重点回答：

> 为什么人类认为这个问题应该命中，但向量检索没有命中？

---

# 五、分类失败原因

分别判断 case_003 / case_011 属于哪一种：

## A. 知识文档缺失

例如：

```text
用户问：
某业务怎么操作？

文档根本没有相关内容。
```

这种情况下：

> 不应该修改检索算法。

应该记录：

```text
failure_type = DOCUMENT_GAP
```

---

## B. Chunk 切分问题

例如：

```text
相关知识存在于 Markdown，
但因为 chunk 边界导致关键语义被拆散。
```

记录：

```text
failure_type = CHUNKING
```

---

## C. Query 与知识表达差异

例如：

```text
用户：
“退货怎么入库？”

文档：
“退货入库流程”
```

如果 Embedding 已经能够找到相关 chunk，则不要为了这种情况引入 Query Rewrite。

---

## D. Evaluation Case 设计问题

如果发现：

```text
向量结果实际上已经返回正确知识，
但是当前关键词匹配器因为 metadata/content 判断方式导致 false negative
```

则必须修正评测逻辑。

但是：

> 不得降低测试标准。

必须说明为什么原来的判断属于 false negative。

---

## E. Embedding / Vector Search 问题

只有在确认：

```text
知识存在
chunk 合理
query 合理
evaluation 合理
```

之后，才能认为可能是当前向量检索能力问题。

本阶段不要因此更换 Embedding 模型。

---

# 六、建立 Failure Analysis 输出

建议增加：

```text
docs/evaluation/rag-retrieval-analysis.md
```

记录：

```markdown
# RAG Retrieval Analysis

## Baseline

Top-K: 5
Cases: 12
Hit: 10
Failed: 2
Hit Rate: 83.33%

## case_003

### Query
...

### Expected Keywords
...

### Top-5 Results
...

### Root Cause
...

### Decision
...

## case_011

...

## Optimization Decision

...
```

必须记录**证据**，不要只写结论。

---

# 七、优化原则

只允许：

> **最小修改、可解释、可回滚。**

优先级：

```text
1. 文档内容问题
2. Chunk 配置/结构问题
3. Evaluation 判断问题
4. Vector Search 参数问题
5. 最后才考虑算法优化
```

不要一看到召回失败就改算法。

---

# 八、如果是文档问题

如果确认：

```text
DOCUMENT_GAP
```

允许修改：

```text
docs/knowledge/wms-basic-operations.md
```

补充缺失的 WMS 通用知识。

要求：

* 不加入企业敏感信息
* 不编造真实企业规则
* 保持通用 WMS 内容
* 保持 Markdown 结构
* 不修改 evaluation case 来适应文档

修改后必须重新 ingestion。

---

# 九、如果修改知识文档

必须注意 content_hash。

不能简单认为：

```text
原 document
+
新 chunks
```

就结束。

必须验证现有 KnowledgeIngestionService 对内容变化的处理方式。

如果当前 Service 的设计是：

```text
same content_hash → already_exists
```

那么修改文件后：

```text
content_hash 改变
```

应该创建/更新正确的知识文档。

必须避免：

```text
旧 document + 新 document
```

造成不必要的重复。

如果现有 ingestion service 对“同一个文件内容发生变化”的行为不明确：

> 先分析，不要自行大改 ingestion architecture。

---

# 十、如果是 Chunking 问题

只有有明确证据时才修改 Chunking。

例如：

```text
当前：
chunk_size = 800
overlap = 100
```

如果确实发现 chunk 边界导致语义断裂：

可以做最小配置实验。

例如比较：

```text
800 / 100
```

和：

```text
1000 / 150
```

但是必须：

* 使用真实评测数据
* 记录实验结果
* 不直接覆盖原配置
* 找到合理结果后再决定是否修改默认值

不要进行大量参数搜索。

最多测试 2～3 组有依据的配置。

---

# 十一、如果是 Vector Search 问题

当前：

```text
cosine distance
Top-K = 5
BGE-M3
1024 dimensions
```

保持不变。

允许进行：

```text
Top-K = 3
Top-K = 5
Top-K = 10
```

的**离线评估比较**。

目的只是观察：

```text
Top-K 对 Recall 的影响
```

不要因为 Top-K=10 分数更高就直接修改系统默认值。

必须考虑 RAG Context 和 LLM 成本。

---

# 十二、必须保留原始 Baseline

无论如何优化：

```text
Baseline:
Top-K = 5
Hit Rate = 83.33%
```

必须记录。

优化后记录：

```text
Optimized:
Top-K = ?
Hit Rate = ?
```

如果优化没有提升：

> 也接受。

不要为了得到更高数字继续修改。

---

# 十三、重新执行完整 Evaluation

优化完成后执行：

```bash
RUN_REAL_RAG_EVAL=1 pytest -q tests/test_rag_evaluation_real.py
```

然后得到：

```text
total_cases
matched_cases
failed_cases
hit_rate
```

必须保留：

```text
case_003
case_011
```

最终状态。

如果失败 case 改变，记录原因。

---

# 十四、重新验证 RAG API

确认：

```text
POST /api/rag/answer
POST /api/chat
```

都没有被破坏。

至少验证：

```text
采购入库的基本流程是什么？
退货入库怎么处理？
调拨流程是什么？
盘点怎么操作？
```

不要修改 API。

---

# 十五、Regression

执行：

```bash
pytest -q
```

然后：

```bash
RUN_DB_TESTS=1 pytest -q
```

要求：

```text
0 failed
```

如果涉及真实 Embedding：

```bash
RUN_REAL_RAG_EVAL=1 pytest -q tests/test_rag_evaluation_real.py
```

不要调用 DeepSeek。

本阶段 Retrieval Evaluation 不需要 LLM。

---

# 十六、测试要求

如果修改了代码：

必须新增/修改对应测试。

至少保证：

* failure analysis 逻辑有测试（如果代码化）
* Evaluation 仍然正确
* Vector Search 没有回归
* RAG 没有回归
* Chat 没有回归
* duplicate ingestion 没有回归

不要为了测试覆盖率而增加无意义测试。

---

# 十七、安全

继续确保：

```text
API Key: NOT FOUND
Authorization: NOT FOUND
Database URL: NOT FOUND
真实服务器 IP: NOT FOUND
个人信息: NOT FOUND
```

`.env` 不得进入 Git。

---

# 十八、最终报告

完成后严格按照下面格式报告：

```text
Phase 3.5.9 RAG Retrieval Analysis & Optimization

1. Baseline
- Top-K:
- Cases:
- Hit:
- Failed:
- Hit Rate:

2. Failure Analysis

case_003
- Query:
- Expected:
- Root Cause:
- Evidence:
- Action:

case_011
- Query:
- Expected:
- Root Cause:
- Evidence:
- Action:

3. Optimization
- Changed:
- Why:
- Alternative considered:
- Result:

4. Final Evaluation
- Top-K:
- Cases:
- Hit:
- Failed:
- Hit Rate:
- Improvement:

5. Tests
- Unit:
- DB:
- Full regression:
- Real evaluation:

6. API Regression
- /api/rag/answer:
- /api/chat:

7. Lint
- PASS/FAIL

8. Security
- API Key:
- .env committed:
- Sensitive data:

9. Scope
- Agent: NO
- Tool Calling: NO
- LangGraph: NO
- MCP: NO
- WMS API: NO
- Query Rewrite: NO
- Reranker: NO
- Hybrid Search: NO
- Embedding model changed: NO

10. Modified Files
- ...

11. Status
Phase 3.5.9 COMPLETE
```

**特别要求：**

如果分析发现当前 83.33% 已经是合理结果，没有足够证据支持优化：

> 可以选择“不修改代码，只完成 Failure Analysis”。

不要为了“必须优化”而强行修改系统。

**完成 Phase 3.5.9 后立即停止，不得自动进入 Phase 3.5.10。**
