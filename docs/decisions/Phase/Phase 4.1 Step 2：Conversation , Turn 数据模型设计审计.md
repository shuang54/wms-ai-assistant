你现在开始执行：

# Phase 4.1 Step 2：Conversation / Turn 数据模型设计审计

## 一、阶段目标

Phase 4.1 Step 1 已完成 Conversation 架构审计。

已经确认：

```text
Conversation
    ↓
Assistant Request × N
```

以及：

```text
conversation_id
    ≠
assistant_request_id
    ≠
LLM provider request_id
```

本 Step 只完成：

> **Conversation / Conversation Turn 最小数据模型设计与 Contract Freeze。**

本 Step：

```text
阅读现有 DB / DTO / Request
        ↓
设计 Conversation
        ↓
设计 ConversationTurn
        ↓
确认字段职责
        ↓
确认生命周期
        ↓
确认删除/归档边界
        ↓
离线 Contract Tests
        ↓
设计文档
        ↓
STOP
```

---

# 二、严格禁止

本 Step 禁止：

```text
新增数据库表
新增 migration
新增 ORM Model
新增 Repository
新增 Service
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
认证系统
```

禁止：

```text
DeepSeek
真实 LLM
网络请求
PostgreSQL 查询
```

---

# 三、必须先阅读真实实现

阅读：

```text
backend/app/db/models/
backend/app/db/
backend/app/dto/

backend/app/api/orchestrator_chat.py
backend/app/services/ai_orchestrator_service.py

backend/app/services/assistant_trace_query_service.py
backend/app/services/assistant_timeline_query_service.py

backend/app/db/models/assistant_outcome_record.py
backend/app/db/models/llm_usage_record.py
backend/app/db/models/tool_execution_record.py
backend/app/db/models/rag_execution_record.py
```

同时搜索：

```text
created_at
updated_at
UUID
request_id
assistant_request_id
project_id
archived
deleted
status
```

目标：

**复用项目现有字段风格。**

不要重新发明：

```text
ID 类型
timestamp 类型
status 类型
```

---

# 四、Conversation 最小模型

设计一个概念模型：

```text
Conversation
```

第一版只允许讨论这些字段：

```text
conversation_id
project_id
created_at
updated_at
status
```

其中：

### conversation_id

要求：

```text
唯一
不可变
```

不要使用：

```text
assistant_request_id
```

---

### project_id

根据 Step 1 的结论：

```text
Conversation 创建时绑定 project_id
```

之后：

```text
不可切换
```

必须明确：

```text
project_id != null
```

还是允许：

```text
project_id = null
```

以当前 ProjectContext 实现为准。

**不要自行扩大 ProjectContext。**

---

### created_at

表示：

```text
Conversation 创建时间
```

不是：

```text
第一条消息时间
```

不是：

```text
最后一次请求时间
```

---

### updated_at

定义为：

```text
Conversation 最近一次有效变更时间
```

但本阶段必须讨论：

> 未来到底哪些操作会更新 updated_at？

至少分析：

```text
创建
新增 Turn
归档
删除
修改标题（如果未来存在）
```

不要现在实现。

---

### status

只设计，不实现。

如果现有项目没有状态枚举：

不要急着创建复杂状态体系。

优先考虑最小：

```text
ACTIVE
ARCHIVED
```

但必须根据当前项目实际需求判断。

不要增加：

```text
DELETED
CLOSED
EXPIRED
LOCKED
PROCESSING
FAILED
```

等没有明确需求的状态。

---

# 五、ConversationTurn

设计：

```text
ConversationTurn
```

核心关系：

```text
Conversation
    1
    │
    └────── N ConversationTurn
```

最小字段候选：

```text
turn_id
conversation_id
assistant_request_id
role
content
created_at
```

但是：

**不要直接接受这套字段。**

必须逐项分析。

---

# 六、turn_id

明确：

```text
turn_id
```

与：

```text
assistant_request_id
```

不同。

原因：

```text
assistant_request_id
=
一次 AI Core execution

turn_id
=
Conversation 中的一次消息/轮次
```

特别分析：

```text
user message
+
assistant response
```

到底算：

```text
一个 Turn
```

还是：

```text
两个 Message
```

本 Step 必须形成明确结论。

---

# 七、role

至少分析：

```text
user
assistant
```

是否需要：

```text
system
tool
```

注意：

Conversation History 不应该直接等同于：

```text
LLM raw messages
```

因此不要为了兼容 LLM 而提前加入大量 role。

如果第一版只面向用户与 Assistant：

优先：

```text
USER
ASSISTANT
```

---

# 八、content

这是本 Step 最重要的字段之一。

必须明确：

```text
ConversationTurn.content
```

保存的是：

```text
用户输入 / Assistant 最终回答
```

还是：

```text
完整 LLM prompt
```

答案必须明确：

> **不得保存完整 LLM prompt。**

不得把：

```text
RAG chunks
Tool arguments
Tool results
SQL
System Prompt
```

塞入 ConversationTurn。

Conversation History 与 LLM Context 必须继续保持分离。

---

# 九、Assistant Turn 与 Request

未来：

```text
ConversationTurn
        ↓
assistant_request_id
        ↓
Assistant Trace
```

但必须允许：

```text
user turn
```

没有：

```text
assistant_request_id
```

因此设计：

```text
assistant_request_id = nullable
```

还是：

```text
每一个 Turn 都必须绑定 Request
```

必须明确选择。

推荐：

```text
USER
  assistant_request_id = NULL

ASSISTANT
  assistant_request_id = actual request id
```

原因：

用户消息本身不是 AI Core execution。

---

# 十、Request 与 Turn

形成：

```text
Conversation
   │
   ├── Turn 1 USER
   │
   ├── Turn 2 ASSISTANT
   │       └── assistant_request_id = A
   │
   ├── Turn 3 USER
   │
   └── Turn 4 ASSISTANT
           └── assistant_request_id = B
```

但同时分析另一种设计：

```text
Conversation
   │
   ├── Turn 1
   │       ├── user_content
   │       ├── assistant_content
   │       └── assistant_request_id
   │
   └── Turn 2
```

比较：

```text
查询
排序
流式输出
失败恢复
重新生成
工具调用
```

最终选一个。

**本阶段只设计，不实现。**

---

# 十一、重新生成 / Retry

必须考虑：

```text
Assistant Request A
```

失败后：

```text
Assistant Request B
```

是否允许：

```text
同一个 User Turn
    ↓
多个 Assistant Request
```

例如：

```text
USER Turn 1
      │
      ├── Assistant Request A → FAILED
      │
      └── Assistant Request B → SUCCESS
```

这会影响：

```text
assistant_request_id
turn_id
```

的关系。

本 Step 必须记录设计结论。

不要实现 regenerate。

---

# 十二、T2SQL Retry

特别注意当前：

```text
Text-to-SQL semantic retry
```

一个 Assistant Request 内可能产生：

```text
多个 LLM usage records
```

但：

```text
ConversationTurn
```

仍然只对应：

```text
一个 assistant_request_id
```

不要：

```text
一个 LLM attempt = 一个 Conversation Turn
```

---

# 十三、Tool / RAG

ConversationTurn 不保存：

```text
ToolExecutionRecord
RagExecutionRecord
```

只通过：

```text
assistant_request_id
```

间接关联：

```text
ConversationTurn
       ↓
assistant_request_id
       ↓
Trace
       ├── LLM
       ├── Tool
       └── RAG
```

保持现有 Observability 架构。

---

# 十四、Outcome

Assistant Turn 的最终状态不要复制：

```text
SUCCESS
EMPTY
REFUSED
FAILED
```

到 ConversationTurn。

因为已经存在：

```text
AssistantOutcome
```

未来：

```text
ConversationTurn
    ↓
assistant_request_id
    ↓
AssistantOutcome
```

本阶段不复制 Outcome。

---

# 十五、删除 / 归档

必须设计：

### 删除 Conversation

是否：

```text
物理删除
```

还是：

```text
软删除 / ARCHIVED
```

考虑：

```text
Trace
Timeline
LLM Usage
Tool
RAG
Outcome
```

是否应该一起删除。

本阶段推荐：

> **Conversation Layer 不负责删除 Observability 历史记录。**

必须明确记录：

```text
Conversation lifecycle
    ≠
Observability lifecycle
```

不要级联删除：

```text
llm_usage_record
tool_execution_record
rag_execution_record
assistant_outcome_record
```

---

# 十六、Data Retention

只设计，不实现。

需要记录：

```text
Conversation History retention
```

与：

```text
Observability retention
```

是两个独立策略。

不要现在增加：

```text
TTL
Celery cleanup
Cron
partition
```

---

# 十七、Project Isolation

必须锁定：

```text
Conversation.project_id
```

创建之后：

```text
不可修改
```

同一个：

```text
conversation_id
```

不能接受：

```text
project_id = B
```

来查询：

```text
project A
```

本阶段只写 Contract。

不要实现授权系统。

---

# 十八、Security

ConversationTurn：

允许：

```text
普通 user content
assistant content
```

但禁止设计字段保存：

```text
api_key
password
database_url
Authorization
raw headers
SQL
embedding
DB Session
connection
RAG raw chunks
tool raw internal payload
system prompt
```

特别注意：

> Conversation History 未来可能比 Trace 更敏感。

---

# 十九、索引设计

只做设计。

候选：

```text
conversation_id
conversation_id + created_at
assistant_request_id
```

必须判断：

### 查询：

```text
GET conversation
GET conversation turns
```

最主要访问模式是什么。

不要提前创建：

```text
20 个 index
```

建议最小：

```text
conversation_id
(conversation_id, created_at)
```

如果 assistant_request_id 查询未来明确需要，再考虑。

---

# 二十、并发与唯一性

设计：

```text
conversation_id UNIQUE
turn_id UNIQUE
```

如果：

```text
assistant_request_id
```

允许：

```text
多个 assistant turn
```

则：

```text
assistant_request_id
```

不能加全局 UNIQUE。

必须结合：

```text
regenerate
retry
```

形成结论。

---

# 二十一、Contract Tests

新增：

```text
tests/test_conversation_model_contract.py
```

纯离线。

至少测试：

### Conversation

```text
conversation_id
project_id
created_at
updated_at
status
```

### Turn

```text
turn_id
conversation_id
role
content
assistant_request_id
created_at
```

### Relationship

```text
Conversation 1 → N Turn
Turn → optional assistant_request_id
```

### Security

禁止：

```text
prompt
messages
sql
api_key
password
database_url
authorization
```

### Existing boundary

确保：

```text
Trace API
Timeline API
Outcome
```

Contract 不发生变化。

### No production implementation

确认：

```text
backend/app/db
backend/app/api
backend/app/services
```

没有因为本 Step 增加 Conversation production code。

---

# 二十二、文档

新增：

```text
docs/evaluation/Phase 4.1 Step 2 — Conversation Data Model Audit.md
```

至少包含：

```text
1. Conversation Model
2. ConversationTurn Model
3. Turn / Request Relationship
4. User / Assistant Role
5. Project Binding
6. History Boundary
7. Trace Boundary
8. Timeline Boundary
9. Outcome Boundary
10. Retry / Regenerate
11. Delete / Archive
12. Retention
13. Security
14. Index Strategy
15. Concurrency
16. Idempotency
17. Deferred Implementation
```

---

# 二十三、测试命令

只运行：

```powershell
python -m pytest -q tests/test_conversation_model_contract.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

禁止：

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

# 二十四、Git Diff

允许：

```text
tests/
docs/
```

禁止：

```text
backend/app/
.github/
database migration
```

---

# 二十五、完成后立即 STOP

最终报告：

```text
Phase 4.1 Step 2 COMPLETE

1. Conversation Model
2. ConversationTurn Model
3. Turn / Request Relationship
4. Project Binding
5. History Boundary
6. Trace / Timeline / Outcome Boundary
7. Retry / Regenerate
8. Delete / Archive
9. Retention
10. Security
11. Index Strategy
12. Concurrency
13. Idempotency
14. Contract Tests
15. compileall
16. Production Changes
17. DB / Network / LLM
18. Deferred Implementation

Phase 4.1 Step 2 READY
Phase 4.1 Step 2 STOP
```

**不要创建数据库表。**

**不要创建 ORM。**

**不要创建 Conversation API。**

**不要修改 `/api/ai/chat`。**

**不要实现 Memory。**

**不要进入 Step 3。**
