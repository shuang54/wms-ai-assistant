你现在开始实施项目的 **Phase 3.5.8：First Real WMS Knowledge Base Loading**。

## 一、阶段目标

当前项目已经具备：

```text
Parser
↓
Chunker
↓
Embedding
↓
PostgreSQL + pgvector
↓
Vector Search
↓
RAG
↓
Chat
↓
Retrieval Evaluation
```

但当前真实知识库为空。

本阶段唯一目标：

> **创建并导入第一份真实的 WMS 知识文档，使知识库从空状态变成可用于 RAG 的真实数据。**

完成后必须能够验证：

```text
WMS Markdown
 ↓
Parser
 ↓
Chunker
 ↓
BGE-M3 1024
 ↓
knowledge_document
 ↓
knowledge_chunk
 ↓
pgvector
 ↓
Vector Search
 ↓
RAG Evaluation
```

---

# 二、严格限制

本阶段只做知识库首次加载。

禁止实现：

* Agent
* Tool Calling
* Function Calling
* LangGraph
* MCP
* WMS API
* ERP API
* 实时库存
* SQL Tool
* 数据库自然语言查询
* Intent Classifier
* Query Rewrite
* Reranker
* Hybrid Search
* HNSW / IVFFlat
* Streaming
* 权限系统
* 前端
* Redis
* Celery
* 消息队列
* PDF/DOCX
* 新 Embedding 模型
* 修改 DeepSeek
* 修改 RAG Prompt
* 修改 Vector Search 算法
* 修改 `/api/chat`
* 修改 `/api/rag/answer`

不要提前实现后续阶段功能。

---

# 三、先检查现有实现

开始前必须阅读：

```text
AGENTS.md
docs/requirements.md
docs/architecture.md
docs/api.md
README.md

backend/app/services/knowledge_ingestion_service.py
backend/app/services/vector_search_service.py
backend/app/services/rag_service.py
backend/app/services/rag_evaluation_service.py

backend/app/parsers/
backend/app/chunking/
backend/app/embedding/
backend/app/models/
backend/app/db/

tests/fixtures/knowledge/
tests/fixtures/rag/evaluation_cases.json
```

重点理解已经存在的：

```text
KnowledgeIngestionService
ParserFactory
MarkdownAwareChunker
EmbeddingClient
KnowledgeDocument
KnowledgeChunk
```

**不要重新实现已有能力。**

---

# 四、创建第一份真实 WMS 知识文档

新增：

```text
docs/knowledge/wms-basic-operations.md
```

这是一份**真实可用于 RAG 的 WMS 操作知识文档**。

不要写成测试代码。

不要使用假的 document/chunk 数据。

内容至少覆盖：

## 1. 采购入库

说明：

* 什么是采购入库
* 采购订单如何进入入库流程
* 采购入库单如何创建
* 收货
* 数量确认
* 批次
* 条码
* 库位
* 上架
* 入库完成

## 2. 退货入库

说明：

* 退货入库的业务目的
* 来源单据
* 数量
* 条码
* 库位
* 完成条件

## 3. 销售出库

说明：

* 销售订单
* 发货通知
* 拣货
* PDA
* 库位
* 条码
* 出库确认

## 4. 调拨

说明：

* 调拨通知
* 来源仓库
* 目标仓库
* 拣货
* 调拨出库
* 调拨入库
* 条码管控

## 5. 盘点

说明：

* 盘点任务
* 库位
* 物料
* 批次
* 条码
* 初盘
* 复盘
* 差异

## 6. 上架

说明：

* 上架任务
* PDA
* 库位
* 条码
* 数量
* 上架完成

## 7. 工单领料

说明：

* 工单
* 领料
* 转移
* 线边仓
* PDA
* 条码

## 8. 条码

说明：

* 条码与物料关系
* 条码扫描
* 条码管控
* 无条码物料的处理原则
* 条码在入库、出库、调拨中的作用

## 9. 仓库与库位

说明：

```text
组织
 ↓
仓库
 ↓
库区/库位
 ↓
货架
```

解释仓库、库位、货架之间的关系。

---

# 五、文档写作要求

这是知识库源文档，因此必须：

### 1. 使用明确标题

例如：

```markdown
# WMS 基础业务操作

## 一、采购入库

### 1.1 业务说明

### 1.2 操作流程

### 1.3 注意事项
```

### 2. 内容必须适合向量检索

不要写：

```text
采购入库就是采购入库。
```

应该写成有上下文的信息：

```text
采购入库用于记录采购物料到货后的收货、数量确认和入库过程。
典型流程为采购订单→采购收货通知→采购入库→库位上架。
```

### 3. 不要虚构企业内部不存在的具体规则

不要自行编造：

* ERP 单号规则
* API 地址
* 数据库表名
* 用户权限
* 企业真实审批规则
* 企业真实接口字段

本阶段文档写成**通用 WMS 操作知识**即可。

### 4. 可以使用你已经掌握的实际 WMS 业务概念

例如：

* PDA
* 库位
* 条码
* 批次
* 采购入库
* 销售出库
* 调拨
* 盘点
* 工单
* 线边仓
* 上架

但不要把公司敏感信息写进知识库。

---

# 六、导入方式

优先复用现有：

```text
KnowledgeIngestionService
```

不要重新写第二套 ingestion pipeline。

如果当前 `KnowledgeIngestionService` 已经有：

```python
ingest(...)
```

或者等价入口：

直接调用现有入口。

不要复制：

```text
Parser
Chunker
Embedding
DB insert
```

---

# 七、增加一个明确的开发入口

如果当前项目还没有简单的 CLI 导入入口，可以增加：

```text
backend/app/cli/
```

例如：

```bash
python -m backend.app.cli.ingest_knowledge docs/knowledge/wms-basic-operations.md
```

要求：

* 调用现有 KnowledgeIngestionService
* 不直接操作 ORM
* 不绕过 Parser
* 不绕过 Chunker
* 不绕过 EmbeddingClient

CLI 只负责：

```text
参数
 ↓
调用 Service
 ↓
打印结果
```

如果当前项目已经存在等价 CLI，则直接复用，不要重复创建。

---

# 八、导入结果

导入完成后必须能够看到至少：

```text
document_id
title
file_name
chunk_count
status
```

例如：

```text
document_id: 1
title: WMS 基础业务操作
file_name: wms-basic-operations.md
chunk_count: 20
status: completed
```

实际数量以程序结果为准。

不要硬编码示例数字。

---

# 九、重复导入必须验证

这是本阶段非常重要的一项。

由于 KnowledgeDocument 已经有：

```text
content_hash
```

重复导入同一个文件时：

> 不应该产生第二个 document。

验证：

```text
第一次导入
→ 创建 document + chunks

第二次导入完全相同文件
→ 不产生重复 document
→ 不产生重复 chunks
```

必须增加测试。

---

# 十、修改文件必须最小化

预期新增：

```text
docs/knowledge/wms-basic-operations.md
```

如果没有 CLI：

```text
backend/app/cli/ingest_knowledge.py
```

测试：

```text
tests/test_knowledge_ingestion_integration.py
```

或者在现有 ingestion 测试体系中增加测试。

如果必须修改已有文件，只允许为：

* CLI 支持
* 真实文档导入
* 重复导入验证

不要为了本阶段重构已有核心服务。

---

# 十一、数据库验证

使用：

```bash
RUN_DB_TESTS=1 pytest -q
```

同时必须检查数据库中真实数据。

至少验证：

```text
knowledge_document
```

存在：

```text
1 个真实 document
```

以及：

```text
knowledge_chunk
```

存在：

```text
> 0 个 chunk
```

并验证：

```text
embedding IS NOT NULL
```

以及：

```text
vector dimension = 1024
```

不能只依赖单元测试。

---

# 十二、重新运行 Retrieval Evaluation

这是本阶段最重要的验收。

执行：

```bash
RUN_REAL_RAG_EVAL=1 pytest -q tests/test_rag_evaluation_real.py
```

要求：

```text
12 个 evaluation cases
 ↓
真实 BGE-M3
 ↓
真实 pgvector
 ↓
VectorSearchService
 ↓
EvaluationService
```

输出真实：

```text
total_cases
matched_cases
failed_cases
hit_rate
```

---

# 十三、不要人为保证命中率

非常重要：

不要修改：

```text
evaluation_cases.json
```

来迎合结果。

也不要：

* 修改 query
* 修改 expected_keywords
* 修改 VectorSearch
* 修改 similarity
* 修改 Top-K

来“提高分数”。

如果命中率只有 40%，也如实报告。

如果是 90%，也如实报告。

这一阶段的目的就是建立真实 baseline。

---

# 十四、RAG API 验证

知识导入后，至少手工验证一个：

```http
POST /api/rag/answer
```

例如：

```json
{
  "query": "采购入库的基本流程是什么？"
}
```

确认：

```text
HTTP 200
answer 非空
used_chunks_count > 0
sources 非空
```

然后验证：

```http
POST /api/chat
```

例如：

```json
{
  "message": "WMS 的采购入库流程是什么？"
}
```

确认：

```text
HTTP 200
answer 非空
```

注意：

这只是验证已有 API，不要修改 API。

---

# 十五、安全检查

知识文档中不得出现：

* API Key
* Token
* Password
* Database URL
* Authorization Header
* 公司内部账号密码
* 真实个人信息
* 真实服务器 IP
* 真实接口密钥

最终检查：

```text
git diff
git status
```

确保：

```text
.env
```

没有进入 Git。

---

# 十六、测试要求

至少：

```bash
pytest -q
```

以及：

```bash
RUN_DB_TESTS=1 pytest -q
```

以及：

```bash
RUN_REAL_RAG_EVAL=1 pytest -q tests/test_rag_evaluation_real.py
```

还要执行 Lint。

---

# 十七、最终报告

完成后只报告：

```text
Phase 3.5.8 First Real WMS Knowledge Base

1. Knowledge Document
- file:
- title:
- document_id:
- chunks:
- status:

2. Database
- knowledge_document: PASS/FAIL
- knowledge_chunk: PASS/FAIL
- embedding: PASS/FAIL
- dimension: 1024 PASS/FAIL

3. Duplicate Ingestion
- first ingestion: ...
- second ingestion: ...
- duplicate document: YES/NO
- duplicate chunks: YES/NO

4. Tests
- Unit: ...
- DB: ...
- Full regression: ...
- Real evaluation: ...

5. Retrieval Baseline
- Top-K:
- total cases:
- matched:
- failed:
- hit rate:

6. API Smoke
- /api/rag/answer: PASS/FAIL
- /api/chat: PASS/FAIL

7. Lint
- PASS/FAIL

8. Security
- API Key: NOT FOUND
- .env committed: NO
- sensitive data: NOT FOUND

9. Scope
- Agent: NO
- Tool Calling: NO
- LangGraph: NO
- MCP: NO
- WMS API: NO
- Vector Search modification: NO

10. Status
Phase 3.5.8 COMPLETE
```

**完成 Phase 3.5.8 后必须停止，不得自动开始 Phase 3.5.9。**
