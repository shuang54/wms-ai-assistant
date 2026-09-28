你现在开始执行：

# Phase 3.12 Step 42 — RAG Trace Coverage Audit

## 一、阶段目标

本阶段只做：

**RAG Observability / Assistant Trace Coverage Audit。**

目标不是立刻开发 RAG Trace。

而是回答一个非常具体的问题：

> 当前 RAG 执行链路已经有哪些可观测数据？这些数据是否足以与 `assistant_request_id` 建立可靠关联？如果不足，缺口在哪里？

Phase 3.12 Step 41 已经完成：

```text
Assistant Trace
├── LLM → PostgreSQL
└── Tool → PostgreSQL
```

现在检查：

```text
Assistant Trace
├── LLM → PostgreSQL
├── Tool → PostgreSQL
└── RAG → ?
```

本阶段只调查 RAG，不进入实现阶段。

---

# 二、严格范围

## 允许

只允许：

* 阅读现有 RAG 实现
* 阅读现有 RAG tests
* 阅读 RAG Service
* 阅读 Knowledge Retrieval / Search / Vector Search 相关代码
* 阅读 RAG API
* 阅读 Orchestrator RAG 路径
* 阅读现有日志 / metrics / observability
* 检查是否已经存在 request_id / assistant_request_id
* 新增极少量 audit tests，如果完全不修改生产逻辑
* 新增一份 evaluation / architecture audit 文档

## 禁止

本阶段禁止修改：

```text
RAGService 核心逻辑
Knowledge Retrieval 核心逻辑
Embedding Service
Vector Search
Reranker
AI Router
AI Orchestrator
LLM Usage
Tool Execution
Assistant Trace Query
Assistant Trace API
SQL Validator
SQL Executor
Database Schema
```

禁止新增：

```text
RAG Trace Table
RAG Repository
RAG ORM Model
OpenTelemetry
Span
TraceId
Conversation
Memory
Agent
MCP
Streaming
Dashboard
```

禁止：

```text
DeepSeek
真实 LLM 调用
生产数据库写入
修改现有 baseline
```

---

# 三、Step 1：阅读真实代码

不要假设接口。

先搜索并阅读：

```text
backend/app/services/rag_service.py
backend/app/services/
backend/app/api/rag.py
backend/app/api/orchestrator_chat.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/ai_router_service.py
```

然后搜索：

```text
request_id
assistant_request_id
correlation
contextvar
rag
retrieval
search
similarity
chunk
embedding
vector
metadata
latency
duration
```

重点确认：

### RAG Service

回答：

```text
RAGService.execute / answer / query
```

实际入口是什么？

---

### Retrieval

确认：

```text
Vector Search
Knowledge Retrieval
Top K
Similarity
Reranker
Chunk Selection
```

分别在哪里发生。

---

### RAG API

确认：

```text
/api/rag/answer
```

是否与：

```text
/api/ai/chat
```

使用同一套 RAG Service。

---

### Orchestrator

确认：

```text
/api/ai/chat
    ↓
AIOrchestrator
    ↓
RAG
```

RAG 执行期间是否已经能够读取：

```text
assistant_request_id
```

---

# 四、Step 2：建立当前 RAG 数据流

不要修改代码。

只画出当前真实数据流。

例如：

```text
POST /api/ai/chat
        ↓
AIOrchestrator
        ↓
assistant_request_id = A
        ↓
Router
        ↓
RAG
        ↓
RagService
        ↓
Knowledge Retrieval
        ↓
Vector Search
        ↓
Chunks
        ↓
LLM
        ↓
Answer
```

标记每一层：

```text
[已有 request_id]
[没有 request_id]
[已有日志]
[没有日志]
[已有持久化]
[只有 runtime]
```

必须基于真实代码。

不要猜。

---

# 五、Step 3：检查现有 RAG 可观测数据

重点检查是否已经存在：

```text
retrieval_count
top_k
similarity
chunk_ids
document_ids
retrieval_duration
reranker_duration
embedding_duration
query
```

以及：

```text
request_id
assistant_request_id
```

如果存在：

记录：

```text
来源
字段
生命周期
是否持久化
```

如果不存在：

明确写：

```text
NOT AVAILABLE
```

不要为了补指标修改生产代码。

---

# 六、Step 4：检查 Security Boundary

特别检查当前 RAG 如果未来进入 Assistant Trace，哪些信息可以暴露。

分类：

## 可以考虑作为诊断元数据

例如：

```text
retrieval_count
top_k
duration_ms
document_count
chunk_count
```

## 高风险

例如：

```text
用户原始 query
chunk content
document content
embedding vector
prompt
LLM messages
raw response
database connection
credentials
```

本阶段不要实现任何字段，只完成分类。

尤其注意：

**不要把 Knowledge Chunk 原文默认视为 Trace-safe。**

---

# 七、Step 5：检查是否存在天然持久化边界

确认当前 RAG 是否已经有：

```text
Repository
PersistenceService
ExecutionRecord
History
Audit
```

如果没有：

明确记录：

```text
Current RAG has no persistent execution record.
```

不要新增。

如果已经存在：

记录：

```text
table
repository
service
safe fields
request correlation
```

---

# 八、Step 6：检查三条 API 路径

分别检查：

### A

```text
POST /api/rag/answer
```

### B

```text
POST /api/ai/chat
```

RAG route。

### C

其他可能直接调用 RAG Service 的 API / internal path。

回答：

```text
是否共享 RAG Service？
是否共享 request context？
是否共享错误语义？
是否共享 observability？
```

特别注意：

不能因为 `/api/ai/chat` 有：

```text
assistant_request_id
```

就假设：

```text
/api/rag/answer
```

也有。

---

# 九、Step 7：不要设计实现

本阶段不要提出：

```text
新增 RAGExecutionRecord
新增 rag_execution_record 表
新增 repository
新增 trace DTO
```

只允许形成：

```text
Current State
Gap
Potential Boundary
```

例如：

```text
Current State:
RAG retrieval has no persistent execution record.

Gap:
Assistant Trace cannot reconstruct retrieval history.

Potential Boundary:
Future RAG observability could expose retrieval metadata only.

Decision:
Defer implementation to a later step.
```

---

# 十、Step 8：新增 Audit 文档

新增：

```text
docs/evaluation/Phase 3.12 Step 42 — RAG Trace Coverage Audit.md
```

文档必须包含：

## 1. Current Architecture

```text
Assistant Request
        ↓
Router
        ↓
RAG
        ↓
Retrieval
        ↓
LLM
```

## 2. Current Observability

表格：

```text
Component | request_id | persistence | observable fields
```

至少：

```text
AIOrchestrator
RAG Service
Retriever
Vector Search
LLM
Tool
```

## 3. Current RAG Fields

明确：

```text
AVAILABLE
NOT AVAILABLE
```

不要猜测。

## 4. Security Classification

```text
Trace-safe
Potentially sensitive
Must never expose
```

## 5. API Comparison

```text
/api/rag/answer
/api/ai/chat
```

## 6. Gap

明确当前 Assistant Trace 为什么：

```text
可以 / 不可以
```

重建 RAG 执行历史。

## 7. Recommendation

只写：

```text
Defer implementation
```

或者：

```text
Existing boundary is sufficient
```

不要进入实现设计。

---

# 十一、Audit Test

如果现有测试基础设施允许，可以新增：

```text
tests/test_rag_trace_coverage_audit.py
```

但只有在能够验证**现有行为**时才新增。

例如验证：

```text
/api/ai/chat
RAG request
metadata.request_id exists
```

以及：

```text
RAG execution does not expose chunk content through Assistant Trace
```

不要为了测试而修改生产代码。

如果现有测试已经覆盖：

**直接复用，不新增重复测试。**

---

# 十二、禁止真实 LLM / DB

本阶段要求：

```text
DeepSeek calls = 0
Network calls = 0
DB writes = 0
Schema changes = 0
```

优先使用：

```text
Fake LLM
MockTransport
Existing fixtures
Existing fake retrieval
```

不要运行真实 DeepSeek。

如果测试需要 PostgreSQL：

只允许只读验证，并且不要产生新数据。

---

# 十三、Regression

执行：

```powershell
python -m pytest -q tests/test_rag_trace_coverage_audit.py
```

如果没有新增测试，则运行相关现有 RAG / Assistant Trace 测试。

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests
```

LSP：

```text
0 error
0 warning
```

lint：

如果环境没有：

```text
ruff
flake8
```

保持：

```text
lint unavailable
```

不要为了本阶段安装新的工具。

---

# 十四、Git Diff

检查：

```powershell
git diff --stat
git diff --name-only
```

确认：

生产逻辑没有被意外修改。

如果新增了 audit test / document，只允许这些。

---

# 十五、最终报告

完成后严格按照：

```text
Phase 3.12 Step 42 COMPLETE

1. RAG 当前链路
2. request_id / assistant_request_id
3. Retrieval Observability
4. Persistent Boundary
5. API 对比
6. Security Boundary
7. Current Gap
8. Audit Tests
9. Full Tests
10. DB Tests
11. compile / LSP / lint
12. DB residue
13. Git Diff
14. Step 42 结论
15. 当前限制
```

最后明确：

```text
本阶段只完成 Audit。
没有新增 RAG Trace Persistence。
没有新增 RAG Table。
没有修改 RAG Core。
没有修改 Assistant Trace API。
```

然后：

# STOP

**不要进入 Step 43。**

不要开发：

```text
RAG Trace Persistence
Conversation
Memory
Agent
MCP
OpenTelemetry
Streaming
Dashboard
```

等待下一步指令。
