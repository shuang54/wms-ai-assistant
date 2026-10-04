你现在开始执行：

# Phase 4.1 Step 3：Conversation 生命周期与 Service Boundary 审计

## 一、阶段目标

Phase 4.1 Step 1 已冻结：

```text
Conversation
    ↓
Assistant Request × N
```

Phase 4.1 Step 2 已冻结：

```text
Conversation
    └── ConversationTurn × N
             │
             └── assistant_request_id
                    ↓
                 Trace
                 Timeline
                 Outcome
```

本 Step 只解决：

> **Conversation Service 应该负责什么，以及 Conversation 生命周期如何与现有 AI Core 解耦。**

最终冻结：

```text
API Layer
    ↓
Conversation Service
    ↓
Conversation Repository
    ↓
Conversation / Turn Persistence
```

与：

```text
Conversation Service
    ↓
AIOrchestrator
```

之间的责任边界。

---

# 二、严格禁止

本 Step 禁止：

```text
新增数据库表
新增 migration
新增 ORM
新增 Repository
新增 Conversation Service production code
新增 API
修改 /api/ai/chat
修改 AIOrchestrator
修改 Router
修改 RAG
修改 Tool
修改 LLM
修改 Trace
修改 Timeline
修改 Outcome
```

禁止：

```text
Memory
Context Builder
Agent
MCP
LangGraph
Streaming
WebSocket
```

禁止：

```text
DeepSeek
真实 LLM
PostgreSQL
Redis
网络请求
```

---

# 三、必须先阅读

阅读真实：

```text
backend/app/api/orchestrator_chat.py
backend/app/services/ai_orchestrator_service.py

backend/app/services/assistant_trace_query_service.py
backend/app/services/assistant_timeline_query_service.py

backend/app/db/
backend/app/db/models/
backend/app/db/repositories/
```

以及 Step 1 / Step 2 产物：

```text
docs/evaluation/Phase 4.1 Step 1 — Conversation Architecture Audit.md
docs/evaluation/Phase 4.1 Step 2 — Conversation Data Model Audit.md

tests/test_conversation_architecture_contract.py
tests/test_conversation_model_contract.py
```

确认项目当前：

```text
Service
Repository
DTO
API
```

的真实分层习惯。

---

# 四、Conversation Service 职责

设计：

```text
ConversationService
```

只负责 Conversation 生命周期。

允许的职责：

```text
create_conversation()
get_conversation()
archive_conversation()
list_turns()
append_turn()
```

但：

**不要直接采用这些 API。**

必须根据当前项目 Service 风格决定最终最小 Contract。

---

# 五、Conversation Service 不负责 AI Execution

必须冻结：

```text
ConversationService
        X
        ↓
AIOrchestrator internal execution
```

ConversationService 不负责：

```text
RAG
Tool
Text-to-SQL
LLM
Router
Planning
Memory
```

推荐未来：

```text
Chat/Application Layer
        │
        ├── ConversationService
        │       ↓
        │   history/context
        │
        └── AIOrchestrator
                ↓
             execution
```

但本 Step 不实现。

---

# 六、Chat API 未来责任

未来：

```text
POST /api/conversations/{conversation_id}/messages
```

应该属于：

```text
Conversation / Chat Application Layer
```

而不是：

```text
ConversationService → 自己调用 HTTP
```

明确禁止 Service 层依赖 FastAPI：

```text
Request
Response
HTTPException
Depends
```

---

# 七、Create Conversation

设计最小输入：

```text
project_id
```

输出：

```text
conversation_id
created_at
updated_at
status
project_id
```

但是：

**只设计，不实现。**

创建规则：

```text
project_id 必须有效
conversation_id 服务端生成
status = ACTIVE
created_at = now
updated_at = created_at
```

不要客户端决定：

```text
conversation_id
status
created_at
updated_at
```

---

# 八、Archive Conversation

设计：

```text
ACTIVE
   ↓
ARCHIVED
```

允许：

```text
读取
查看历史
查看 Trace
查看 Timeline
```

禁止：

```text
追加 Turn
```

但：

**本 Step 不实现数据库状态修改。**

只冻结 Contract。

---

# 九、Archive 幂等性

必须分析：

```text
archive(active)
archive(archived)
```

建议：

```text
ACTIVE → ARCHIVED
ARCHIVED → ARCHIVED
```

第二次归档不应该产生错误。

不要增加：

```text
ARCHIVE_FAILED
```

等复杂状态。

---

# 十、Conversation 不允许 Project Switch

冻结：

```text
Conversation.project_id
```

创建后不可修改。

因此未来：

```text
GET /api/conversations/{conversation_id}
```

必须根据：

```text
conversation_id
```

找到：

```text
project_id
```

不能通过客户端传入另一个：

```text
project_id
```

覆盖。

---

# 十一、Append Turn

未来：

```text
append_turn(
    conversation_id,
    role,
    content,
    assistant_request_id
)
```

但本 Step 不实现。

规则：

### USER

```text
role = USER
assistant_request_id = None
```

### ASSISTANT

```text
role = ASSISTANT
assistant_request_id != None
```

必须再次保持 Step 2 Contract。

---

# 十二、Assistant Turn 的产生时机

这是本 Step 的关键设计。

不要设计成：

```text
先创建 ASSISTANT Turn
        ↓
再调用 AIOrchestrator
```

否则 AI 失败时可能留下：

```text
空 assistant message
```

优先设计为：

```text
USER Turn
    ↓
AIOrchestrator
    ↓
Assistant Result
    ↓
Assistant Turn
```

即：

> Assistant Turn 在 AI Core execution 得到最终业务结果之后创建。

但要分析：

```text
FAILED
REFUSED
EMPTY
```

是否也创建 Assistant Turn。

必须形成明确 Contract。

---

# 十三、Outcome 与 Assistant Turn

当前：

```text
AssistantOutcome
```

已经有：

```text
SUCCESS
EMPTY
REFUSED
FAILED
```

本 Step 必须明确：

### ConversationTurn

保存：

```text
assistant content
```

### AssistantOutcome

保存：

```text
execution outcome
```

两者不合并。

例如：

```text
Assistant Turn
content = "抱歉，我无法执行这个请求。"

Outcome
status = REFUSED
```

这是合法状态。

不要把：

```text
status
```

复制进 Turn。

---

# 十四、AI Failure

如果：

```text
AIOrchestrator
    ↓
FAILED
```

是否创建：

```text
ASSISTANT Turn
```

需要形成结论。

推荐：

```text
有可展示给用户的最终内容
    ↓
创建 ASSISTANT Turn

只有内部异常、无用户可见内容
    ↓
不创建 ASSISTANT Turn
```

但：

**不要修改 AIOrchestrator。**

这里只定义未来 Application Layer 的行为。

---

# 十五、RAG / Tool / T2SQL 不直接写 Conversation

冻结：

```text
RAG Service
Tool Service
TextToSQLService
```

不能：

```text
append_turn()
```

不能：

```text
conversation repository
```

不能：

```text
conversation DB
```

它们只返回自己的业务结果。

---

# 十六、Request / Turn Correlation

未来一次正常 Chat：

```text
USER Turn
    conversation_id = C
    assistant_request_id = NULL

        ↓

AIOrchestrator
    assistant_request_id = A

        ↓

ASSISTANT Turn
    conversation_id = C
    assistant_request_id = A
```

这样：

```text
Conversation
    ↓
Turn
    ↓
Assistant Request
    ↓
Trace / Timeline / Outcome
```

保持单向关联。

---

# 十七、Regenerate

Step 2 已冻结：

```text
User Turn
    ├── Assistant Request A → FAILED
    └── Assistant Request B → SUCCESS
```

因此未来 Regenerate 不修改原 Assistant Turn。

设计为：

```text
same user turn
    ↓
new assistant request
    ↓
new assistant turn
```

但：

**本 Step 不实现 regenerate API。**

---

# 十八、Repository Boundary

未来：

```text
ConversationService
        ↓
ConversationRepository
```

Repository 负责：

```text
CRUD
query
transaction boundary
```

不负责：

```text
LLM
RAG
Tool
Router
AIOrchestrator
```

ConversationService 不允许直接：

```text
SQLAlchemy Session
select()
insert()
update()
```

---

# 十九、Transaction Boundary

分析未来一次：

```text
Create USER Turn
        ↓
AIOrchestrator
        ↓
Create ASSISTANT Turn
        ↓
Update Conversation.updated_at
```

是否应该：

```text
一个数据库 transaction
```

必须形成设计结论。

注意：

**不要把整个 LLM 网络调用包在数据库 transaction 中。**

未来推荐：

```text
DB transaction
    ↓
写 USER Turn
commit

LLM execution
    ↓
network / AI

DB transaction
    ↓
写 ASSISTANT Turn
update conversation
commit
```

本 Step 只冻结边界，不实现。

---

# 二十、Read / Write Separation

未来：

```text
ConversationQueryService
ConversationCommandService
```

是否需要拆分？

结合当前项目规模判断。

**不要为了 CQRS 而 CQRS。**

如果当前项目更适合一个：

```text
ConversationService
```

就保持简单。

本 Step 必须明确选择。

---

# 二十一、Trace Query Separation

ConversationService 不负责：

```text
assistant_trace_query_service
assistant_timeline_query_service
```

未来如果需要：

```text
GET conversation detail
```

由 Application Layer 组合：

```text
ConversationService
TraceQueryService
TimelineQueryService
```

不要把 Trace 查询塞进 Conversation Repository。

---

# 二十二、Security Boundary

Conversation Service 不接触：

```text
api_key
password
database_url
Authorization
raw HTTP headers
LLM prompt
RAG chunks
Tool internal payload
SQL
DB Session
```

Repository 也不返回：

```text
SQLAlchemy Session
```

只返回 DTO / model data。

---

# 二十三、Contract Tests

新增：

```text
tests/test_conversation_service_boundary_contract.py
```

纯离线。

至少覆盖：

### 1

Conversation Service 只属于 Conversation Layer。

### 2

不依赖：

```text
LLM
RAG
Tool
Router
AIOrchestrator
```

### 3

不依赖：

```text
FastAPI Request/Response
```

### 4

不依赖：

```text
DeepSeek
network
```

### 5

Create Contract

```text
project_id required
conversation_id server-generated
status ACTIVE
```

### 6

Archive Contract

```text
ACTIVE → ARCHIVED
ARCHIVED → ARCHIVED
```

### 7

Project Binding

```text
immutable
```

### 8

Turn Contract

```text
USER → no assistant_request_id
ASSISTANT → assistant_request_id
```

### 9

Outcome separation

不复制：

```text
SUCCESS
EMPTY
REFUSED
FAILED
```

### 10

Trace separation

Conversation Service 不查询：

```text
LLM usage
Tool execution
RAG execution
Timeline
```

---

# 二十四、文档

新增：

```text
docs/evaluation/Phase 4.1 Step 3 — Conversation Service Boundary Audit.md
```

至少：

```text
1. Service Responsibility
2. Create Conversation
3. Archive Conversation
4. Project Binding
5. Append Turn
6. Assistant Request Correlation
7. Outcome Boundary
8. Failure Handling
9. Regenerate
10. Repository Boundary
11. Transaction Boundary
12. Read / Write Separation
13. Trace Separation
14. Security
15. Contract Tests
16. Deferred Implementation
```

---

# 二十五、测试命令

只运行：

```powershell
python -m pytest -q tests/test_conversation_service_boundary_contract.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要运行：

```text
RUN_DB_TESTS
pytest -q
DeepSeek
```

要求：

```text
DB writes = 0
DB reads = 0
Network = 0
LLM = 0
```

---

# 二十六、Git Diff

允许：

```text
tests/
docs/
```

禁止：

```text
backend/app/
backend/app/api/
backend/app/db/
.github/
migration/
```

---

# 二十七、完成后立即 STOP

最终报告：

```text
Phase 4.1 Step 3 COMPLETE

1. Conversation Service Responsibility
2. Create Contract
3. Archive Contract
4. Project Binding
5. Turn Append Contract
6. Assistant Request Correlation
7. Outcome Boundary
8. Failure Handling
9. Regenerate
10. Repository Boundary
11. Transaction Boundary
12. Read / Write Separation
13. Trace Separation
14. Security
15. Contract Tests
16. compileall
17. Production Changes
18. DB / Network / LLM
19. Deferred Implementation

Phase 4.1 Step 3 READY
Phase 4.1 Step 3 STOP
```

**不要创建 Conversation Service。**

**不要创建 Repository。**

**不要创建 ORM。**

**不要创建数据库表。**

**不要修改 `/api/ai/chat`。**

**不要进入 Step 4。**
