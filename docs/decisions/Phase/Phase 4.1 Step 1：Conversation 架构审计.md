你现在开始执行：

# Phase 4.1 Step 1：Conversation 架构审计

## 一、阶段目标

Phase 3.12 已完成：

```text
AI Core
├── RAG
├── Tool
├── Text-to-SQL
├── LLM Usage
├── Tool Execution
├── RAG Execution
├── Assistant Outcome
├── Assistant Trace
├── Assistant Timeline
└── CI Governance
```

现在进入：

# Phase 4：Conversation / Chat 会话层

本 Step **只做架构审计与 Contract 设计**。

不要立即实现 Conversation。

目标是回答：

> 当前 AI Core 如何从“一次独立请求”演进为“同一个会话中的多轮请求”，而不破坏现有 request_id / assistant_request_id / Trace / Timeline / Outcome 体系？

---

# 二、严格禁止

本 Step 禁止：

```text
新增 Conversation 数据库表
新增 migration
新增 Conversation ORM
新增 Conversation Repository
新增 Conversation API
修改 /api/ai/chat
修改 AIOrchestrator
修改 AIRouter
修改 RAG
修改 Tool Framework
修改 Text-to-SQL
修改 LLM Provider
修改 Trace API
修改 Timeline API
修改 Outcome
修改现有数据库结构
```

禁止：

```text
Memory
Agent
MCP
LangGraph
Planning
Streaming
WebSocket
Chat UI
```

禁止：

```text
DeepSeek
真实 LLM
真实网络
生产数据库
```

---

# 三、必须先阅读

实际阅读：

```text
backend/app/api/ai.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/ai_router_service.py

backend/app/dto/
backend/app/llm/

backend/app/services/assistant_trace_query_service.py
backend/app/services/assistant_trace_read_model.py
backend/app/services/assistant_timeline_query_service.py

backend/app/db/
backend/app/db/models/

tests/
```

重点搜索：

```text
request_id
assistant_request_id
conversation
session
chat
message
history
context
user_id
project_id
```

同时检查：

```text
docs/architecture.md
docs/requirements.md
docs/decisions/
docs/evaluation/
```

---

# 四、当前 Request Boundary

确认当前：

```text
一次 /api/ai/chat
        ↓
AIOrchestrator
        ↓
assistant_request_id
        ↓
LLM / RAG / Tool / T2SQL
        ↓
Outcome
```

明确回答：

1. `request_id` 从哪里产生？
2. `assistant_request_id` 从哪里产生？
3. 两者是否相同？
4. 谁负责生成？
5. 是否已经存在 request context？
6. 是否存在 session/conversation ID？
7. 当前 Trace 使用哪个 ID？
8. 当前 Timeline 使用哪个 ID？
9. Outcome 使用哪个 ID？

**以真实代码为准。**

---

# 五、Conversation 与 Request 的关系

本阶段必须形成明确设计：

```text
Conversation
    │
    ├── Request 1
    │      ├── LLM
    │      ├── RAG
    │      ├── Tool
    │      └── Outcome
    │
    ├── Request 2
    │      ├── LLM
    │      ├── RAG
    │      ├── Tool
    │      └── Outcome
    │
    └── Request 3
```

核心原则：

```text
conversation_id
    ≠
assistant_request_id
```

其中：

```text
conversation_id
=
多轮会话容器

assistant_request_id
=
单次 Assistant 请求
```

不要用一个 ID 同时承担两个语义。

---

# 六、Correlation Design

设计目标：

```text
conversation_id
        │
        ├── assistant_request_id A
        │       ├── LLM
        │       ├── RAG
        │       ├── Tool
        │       └── Outcome
        │
        ├── assistant_request_id B
        │       ├── LLM
        │       └── RAG
        │
        └── assistant_request_id C
                └── Tool
```

确认：

### 现有 observability 表

```text
llm_usage_record
tool_execution_record
rag_execution_record
assistant_outcome_record
```

目前只关联：

```text
assistant_request_id / request_id
```

本 Step 不修改表。

只记录未来需要：

```text
Conversation
    ↓
Request
```

的关系。

---

# 七、Conversation ID 来源

评估三种方式：

### 方案 A

客户端生成：

```text
conversation_id
```

### 方案 B

服务端创建：

```text
conversation_id
```

### 方案 C

服务端首次请求创建，后续客户端携带：

```text
conversation_id
```

比较：

```text
安全性
可控性
幂等性
API 简洁性
未来持久化
多端使用
```

最终只给出：

```text
推荐方案
```

但不要实现。

---

# 八、Project Context

必须检查：

```text
ProjectContext
```

与 Conversation 的关系。

需要回答：

```text
Conversation 是否绑定 project_id？
```

考虑：

```text
conversation
    ↓
project_id
```

还是：

```text
request
    ↓
project_id
```

尤其考虑：

```text
同一个 conversation
是否允许切换 project？
```

本 Step 不修改 Project Context。

只形成明确设计结论。

---

# 九、User / Authorization

当前系统可能没有：

```text
user_id
```

或者没有完整 authentication。

必须真实检查。

不要自行增加：

```text
user_id
tenant_id
organization_id
```

本阶段只记录：

```text
Conversation ownership
```

当前是否：

```text
未定义
```

如果未定义，明确：

```text
当前 Conversation 不应在本阶段假设存在用户级权限隔离。
```

---

# 十、Conversation History

这是本阶段最重要的部分之一。

先不要实现 Memory。

只定义：

```text
Conversation
    ↓
Messages / Turns
```

评估历史消息未来应该放在哪里：

### A

PostgreSQL

### B

Redis

### C

第三方 Conversation API

### D

现有业务数据库

### E

其他

不要现在实现。

只分析：

```text
持久化
一致性
查询
排序
恢复
多实例
审计
隐私
```

---

# 十一、不要直接把历史塞进 Orchestrator

未来结构应该类似：

```text
Chat API
    ↓
Conversation Service
    ↓
History / Context
    ↓
AIOrchestrator
```

而不是：

```text
Chat API
    ↓
AIOrchestrator
    ↓
AIOrchestrator 自己查询 Conversation DB
```

必须保持：

```text
Conversation Layer
```

与：

```text
AI Core
```

职责分离。

---

# 十二、Context 与 History 区分

明确：

```text
Conversation History
```

不等于：

```text
LLM Context
```

例如：

```text
Conversation
    ↓
100 messages
    ↓
Context Builder
    ↓
relevant history
    ↓
LLM
```

本阶段不要实现 Context Builder。

只记录未来边界。

---

# 十三、Trace 关系

未来应该可以形成：

```text
Conversation
    ↓
Request
    ↓
Trace
```

但当前 Trace API：

```text
/api/observability/assistant-trace/{assistant_request_id}
```

继续保持。

不要改 Trace API。

未来可以：

```text
conversation_id
    ↓
找到 assistant_request_id 列表
    ↓
分别查询 Trace
```

本 Step 只记录这个设计，不实现。

---

# 十四、Timeline 关系

同样：

```text
conversation_id
    ↓
assistant_request_id
    ↓
assistant timeline
```

不要新增：

```text
conversation timeline
```

不要改变现有：

```text
/api/observability/assistant-timeline/{assistant_request_id}
```

---

# 十五、Outcome

当前：

```text
assistant_request_id
    ↓
AssistantOutcome
```

未来：

```text
conversation_id
    ↓
multiple assistant_request_id
    ↓
multiple outcomes
```

不要把：

```text
Conversation Outcome
```

与：

```text
Assistant Request Outcome
```

混合。

本 Step 不修改 Outcome。

---

# 十六、Concurrency

考虑：

```text
同一个 conversation
```

同时收到：

```text
Request A
Request B
```

本阶段只分析风险：

```text
history ordering
race condition
duplicate turn
last-write-wins
```

不要实现锁。

不要实现队列。

不要增加数据库字段。

---

# 十七、Idempotency

未来客户端可能：

```text
POST /chat
```

重复发送。

本阶段分析：

```text
conversation_id
assistant_request_id
client_message_id
```

是否需要区分。

尤其注意：

```text
assistant_request_id
```

是服务端单次请求 ID。

不要把它当：

```text
message_id
```

也不要现在新增 message_id。

---

# 十八、Security Boundary

Conversation 未来可能包含：

```text
用户问题
AI 答案
Tool result
RAG context
SQL result
```

本阶段必须明确：

### 不应进入 Conversation Trace metadata：

```text
API key
password
database URL
connection string
Authorization
raw SQL
embedding
internal DB session
```

Conversation History 与 Observability Metadata 必须分离。

---

# 十九、最小 Contract

本阶段最终提出：

```text
Conversation
```

最小概念字段，例如：

```text
conversation_id
project_id
created_at
updated_at
```

以及：

```text
ConversationTurn
```

最小概念：

```text
conversation_id
assistant_request_id
role
content
created_at
```

注意：

**这只是设计 DTO，不要创建 production DTO。**

---

# 二十、HTTP API

评估未来 API：

```text
POST /api/conversations
GET  /api/conversations/{conversation_id}
POST /api/conversations/{conversation_id}/messages
GET  /api/conversations/{conversation_id}/messages
```

但是：

**本阶段全部只做设计，不实现。**

尤其：

```text
/api/ai/chat
```

保持完全不变。

---

# 二十一、Contract Tests

新增：

```text
tests/test_conversation_architecture_contract.py
```

纯离线。

至少测试：

### 1

Conversation ID 与 Assistant Request ID 是不同概念。

### 2

现有 Trace API 不被修改。

### 3

现有 Timeline API 不被修改。

### 4

现有 Outcome contract 不被修改。

### 5

Conversation 不应直接访问：

```text
SQLAlchemy
PostgreSQL
RAG
Tool
LLM
```

### 6

Conversation 不应依赖：

```text
DeepSeek
network
```

### 7

未来 API 为设计记录，不实际注册路由。

### 8

Security fields：

```text
api_key
password
database_url
authorization
```

不进入设计 metadata。

---

# 二十二、Architecture 文档

新增：

```text
docs/evaluation/Phase 4.1 Step 1 — Conversation Architecture Audit.md
```

必须包含：

## 1. Current State

```text
/api/ai/chat
    ↓
AIOrchestrator
    ↓
single Assistant Request
```

## 2. Proposed State

```text
Conversation
    ↓
Assistant Request × N
```

## 3. ID Boundary

```text
conversation_id
    ≠
assistant_request_id
```

## 4. Project Boundary

## 5. History Boundary

## 6. Context Boundary

## 7. Trace Boundary

## 8. Timeline Boundary

## 9. Outcome Boundary

## 10. Security

## 11. Concurrency

## 12. Idempotency

## 13. Proposed APIs

## 14. Deferred Implementation

明确：

```text
DB = unchanged
API = unchanged
Orchestrator = unchanged
RAG = unchanged
Tool = unchanged
LLM = unchanged
```

---

# 二十三、测试命令

只运行：

```powershell
python -m pytest -q tests/test_conversation_architecture_contract.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要运行：

```text
RUN_DB_TESTS
pytest -q
```

本 Step：

```text
DB writes = 0
DB reads = 0
Network = 0
LLM = 0
```

---

# 二十四、Git Diff

确认：

```text
backend/app/ = unchanged
backend/app/api/ = unchanged
backend/app/db/ = unchanged
.github/ = unchanged
```

允许：

```text
tests/
docs/
```

---

# 二十五、完成后立即 STOP

最终报告：

```text
Phase 4.1 Step 1 COMPLETE

1. Current Request Boundary
2. Conversation Boundary
3. ID Boundary
4. Project Context
5. History
6. Context
7. Trace
8. Timeline
9. Outcome
10. Security
11. Concurrency
12. Idempotency
13. Proposed APIs
14. Contract Tests
15. compileall
16. Production Changes
17. DB / Network / LLM
18. Deferred Implementation

Phase 4.1 Step 1 READY
Phase 4.1 Step 1 STOP
```

**完成后不要创建 Conversation 表。**

**不要修改 `/api/ai/chat`。**

**不要实现 Memory。**

**不要进入 Phase 4.1 Step 2。**
