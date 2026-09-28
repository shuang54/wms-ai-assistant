你现在开始执行：

# Phase 3.12 Step 44 — RAG Runtime Observation Production Wiring

## 一、阶段目标

Phase 3.12 Step 43 已完成：

```text
RAG Runtime Observation
        ↓
InMemoryRagExecutionCollector
        ↓
RagObservabilityQueryService
```

但是 Step 43 明确留下：

```text
生产装配未接线
observer 默认 None
```

因此本阶段只解决：

> **将 RAG Runtime Observation 接入 `/api/ai/chat` 的真实生产装配。**

目标：

```text
POST /api/ai/chat
        ↓
AIOrchestrator
        ↓
assistant_request_id = A
        ↓
RAG
        ↓
RagExecutionObserver
        ↓
InMemoryRagExecutionCollector
        ↓
RagExecutionObservation.request_id = A
```

同时：

```text
/api/rag/answer
/api/chat
```

保持现有行为。

---

# 二、严格范围

## 允许修改

优先允许：

```text
backend/app/api/orchestrator_chat.py
backend/app/services/rag_service.py
backend/app/services/in_memory_rag_execution_collector.py
backend/app/services/rag_observability_query_service.py
```

以及：

```text
tests/
docs/architecture.md
docs/evaluation/
```

实际如果只需要修改 Composition Root，则不要扩大修改范围。

---

## 禁止

本阶段禁止修改：

```text
AI Router 核心路由
AI Orchestrator 核心执行逻辑
VectorSearchService
EmbeddingService
Reranker
ContextBuilder
LLM Usage
Tool Execution
Assistant Trace Query
Assistant Trace API
Text-to-SQL
SQL Validator
SQL Executor
```

禁止：

```text
PostgreSQL
RAG Persistence
RAG Repository
RAG ORM Model
RAG Table
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

禁止新增 HTTP API。

禁止修改：

```text
/api/rag/answer
/api/chat
/api/ai/chat
```

的 response schema。

---

# 三、Step 1：先阅读真实装配

先不要修改。

重点阅读：

```text
backend/app/api/orchestrator_chat.py
backend/app/api/rag.py
backend/app/api/chat.py
backend/app/main.py
backend/app/services/rag_service.py
```

确认：

```text
/api/ai/chat
```

当前到底在哪里创建：

```text
RagService
AIRouterService
AIOrchestratorService
```

特别寻找：

```text
RagService(...)
```

以及：

```text
RagService.answer(...)
```

不要假设。

---

# 四、Step 2：确定唯一 Runtime Collector

非常重要：

本阶段必须保证：

```text
一个应用进程
        ↓
一个 RAG Runtime Collector
```

不要：

```text
每次请求 new Collector
```

否则：

```text
POST request A
POST request B
```

之间无法形成稳定的 Runtime Observation 查询视图。

优先使用现有 Tool Runtime Observability 的单例/Composition Root 模式。

但：

**不要复制 Tool 的全部框架。**

只复用其“应用级 Collector 生命周期”原则。

---

# 五、Step 3：生产装配目标

最终希望形成：

```text
Composition Root
        │
        ├── RAG Runtime Collector
        │
        ├── RAG Observability Query Service
        │
        └── RagService(observer=...)
```

然后：

```text
AIOrchestrator
      ↓
Router
      ↓
RagService
      ↓
RagExecutionObserver
      ↓
Collector
```

其中：

```text
assistant_request_id
```

仍然来自现有：

```python
current_assistant_request_id()
```

不要新增第二套 request ID。

---

# 六、Step 4：不要让旧 API 自动产生 Trace

这是非常重要的边界。

### `/api/ai/chat`

应该：

```text
observer = enabled
```

### `/api/rag/answer`

保持：

```text
observer = disabled
```

因为当前旧 API 没有：

```text
assistant_request_id
```

Step 43 已经明确：

```text
未绑定 Trace → 不记录
```

不要为了“统一”而给旧 API 强行创建 request ID。

---

# 七、Step 5：真实 `/api/ai/chat` E2E

新增一个真正的应用装配测试。

建议：

```text
tests/test_rag_runtime_observability_e2e.py
```

如果项目已有合适文件，则复用。

测试：

```text
POST /api/ai/chat
        ↓
真实 create_app()
        ↓
真实 Composition Root
        ↓
真实 AIOrchestrator
        ↓
真实 Router
        ↓
真实 RagService
        ↓
Fake Retrieval / Fake LLM boundary
        ↓
RAG Runtime Observation
```

只允许 Fake：

```text
LLM transport
retrieval / embedding boundary
```

不要 Fake：

```text
RagService
AIOrchestrator
AIRouter
Collector
QueryService
```

---

# 八、Step 6：核心 E2E 断言

请求：

```text
一个确定走 RAG 的问题
```

然后：

### HTTP

```text
status == 200
```

并且：

```text
response.metadata.request_id == A
```

### Runtime Observation

通过：

```text
RagObservabilityQueryService
```

查询：

```text
A
```

必须得到：

```text
>= 1
```

并且：

```text
observation.request_id == A
```

---

# 九、Step 7：验证真实装配，而不是手工注入

不要写：

```python
rag_service = RagService(observer=collector)
```

然后直接调用。

这不够。

必须验证：

```text
create_app()
    ↓
Composition Root
    ↓
/api/ai/chat
```

真实链路已经自动产生 Observation。

也就是说：

**测试必须证明生产装配真的接线成功。**

---

# 十、Step 8：API 行为不变

同一个 RAG 请求必须比较：

### 之前已有 contract

```text
route
content
data
metadata
```

现在仍然：

```text
route
content
data
metadata
```

只允许：

```text
metadata.request_id
```

保持 Step 35 已有行为。

不得新增：

```text
rag
retrieval
observations
chunks
latency
```

到 `/api/ai/chat` response。

---

# 十一、Step 9：Runtime Query

测试：

```text
A
```

能够：

```text
query_service.observations_by_request_id(A)
```

得到 observation。

然后：

```text
clear()
```

验证：

```text
query A → []
```

说明：

```text
Runtime Observation
```

仍然是：

```text
Memory only
```

---

# 十二、Step 10：跨请求隔离

执行：

```text
POST RAG → A
POST RAG → B
```

然后：

```text
query(A)
query(B)
```

必须：

```text
A only contains A
B only contains B
```

不得：

```text
A contains B
B contains A
```

---

# 十三、Step 11：非 RAG 路径

必须验证：

### TOOL

```text
POST /api/ai/chat
Tool request
```

RAG Observation：

```text
[]
```

同时 Tool Execution：

```text
正常
```

不能因为 RAG Observer 已经装配而导致 Tool 请求产生假的 RAG Observation。

---

### 其他非 RAG

如果现有 Router 有明确非 RAG 路径：

同样验证：

```text
RAG Observation = 0
```

---

# 十四、Step 12：旧 API Regression

至少验证：

```text
/api/rag/answer
/api/chat
```

仍然：

```text
正常
```

并且：

```text
没有 RAG Runtime Observation
```

原因：

```text
没有 assistant_request_id
```

不要修改旧 API。

---

# 十五、Step 13：Observer Failure Isolation

必须保留 Step 43 的安全原则。

构造：

```text
Collector.record()
```

抛异常。

然后：

```text
/api/ai/chat
```

仍然应该：

```text
RAG success
HTTP 200
```

不能因为：

```text
Observability failure
```

导致：

```text
Business failure
```

---

# 十六、Step 14：Collector 生命周期

验证：

```text
create_app()
```

不会每个请求重新创建：

```text
RagExecutionCollector
```

例如：

```text
request A
request B
```

使用：

```text
same collector instance
```

如果现有架构有明确 Composition Root accessor：

优先复用。

不要新增复杂 Service Locator。

---

# 十七、Step 15：安全边界

HTTP response 不得出现：

```text
chunk content
query
answer duplicate
embedding
prompt
messages
raw_response
sql
password
api_key
authorization
database_url
session
connection
traceback
```

Runtime Observation 仍然只有 Step 43 的：

```text
13 fields
```

不要增加：

```text
similarity
query
content
```

---

# 十八、Step 16：性能原则

本阶段不做性能优化。

只验证：

```text
observer disabled
```

时：

```text
RagService
```

没有明显行为变化。

不要增加：

```text
async queue
background worker
thread pool
Redis
```

不要引入异步日志系统。

---

# 十九、Step 17：测试

新增/修改：

```text
tests/test_rag_runtime_observability_e2e.py
```

至少覆盖：

1. `/api/ai/chat` RAG → observation exists
2. request_id correlation
3. A/B isolation
4. Tool → no RAG observation
5. old `/api/rag/answer` → no observation
6. old `/api/chat` → no observation
7. collector clear
8. observer failure does not break RAG
9. HTTP response contract unchanged
10. security fields unchanged
11. same collector reused across requests

不要重复 Step 43 已有的纯 Collector 单元测试。

---

# 二十、测试策略

默认：

```text
DeepSeek = 0
Network = 0
Database = 0
```

使用：

```text
Fake LLM
MockTransport
Fake retrieval boundary
```

但是：

**AIOrchestrator / Router / RagService / Composition Root 必须是真实代码。**

FastAPI 应用测试继续使用项目现有 `TestClient + pytest` 模式即可。FastAPI 官方也推荐使用 `TestClient` 配合 pytest 测试真实应用路径。

---

# 二十一、Regression

先：

```powershell
python -m pytest -q tests/test_rag_runtime_observability.py
```

再：

```powershell
python -m pytest -q tests/test_rag_runtime_observability_e2e.py
```

然后：

```powershell
python -m pytest -q
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

如果不存在：

```text
ruff
flake8
```

保持：

```text
lint unavailable
```

不要安装工具。

---

# 二十二、Git Diff

执行：

```powershell
git diff --stat
git diff --name-only
```

允许：

```text
Composition Root / RAG Runtime Observation / Tests / Docs
```

禁止出现：

```text
Assistant Trace API
LLM Usage
Tool Persistence
Database Schema
VectorSearch Core
Reranker Core
ContextBuilder Core
Router Core
Text-to-SQL
```

---

# 二十三、文档

新增：

```text
docs/evaluation/Phase 3.12 Step 44 — RAG Runtime Wiring.md
```

必须记录：

## 1. Before

```text
RAG Runtime Observation
    observer=None
    production not wired
```

## 2. After

```text
/api/ai/chat
    ↓
RagService
    ↓
RAG Runtime Observation
```

## 3. Old API

```text
/api/rag/answer → no observation
/api/chat       → no observation
```

## 4. Correlation

```text
assistant_request_id = observation.request_id
```

## 5. Security

13-field whitelist unchanged。

## 6. Persistence

```text
NO
```

## 7. Assistant Trace

```text
UNCHANGED
```

---

# 二十四、最终报告

完成后严格按照：

```text
Phase 3.12 Step 44 COMPLETE

1. Production Wiring
2. Composition Root
3. /api/ai/chat RAG E2E
4. request_id Correlation
5. Cross Request Isolation
6. Non-RAG Isolation
7. Legacy API Regression
8. Observer Failure Isolation
9. Security
10. Tests
11. Full Tests
12. compile / LSP / lint
13. DB
14. Git Diff
15. 当前限制
```

明确：

```text
RAG Runtime Observation = production wired
RAG Persistence = NO
Assistant Trace integration = NO
RAG HTTP API changes = NO
Database schema = unchanged
DeepSeek calls = 0
Network calls = 0
DB writes = 0
```

最后：

# STOP

完成 Step 44 后立即停止。

不要进入 Step 45。

不要做：

```text
RAG Persistence
Assistant Trace RAG integration
Conversation
Memory
Agent
MCP
OpenTelemetry
Streaming
Dashboard
```
