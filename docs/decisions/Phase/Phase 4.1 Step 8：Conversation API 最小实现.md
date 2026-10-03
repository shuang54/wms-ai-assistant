你现在开始执行：

# Phase 4.1 Step 8：Conversation API 最小实现

## 一、当前状态

Phase 4.1 Step 7 已完成：

```text
Conversation Persistence = READY
Conversation API Contract = FROZEN
```

本阶段正式实现以下 4 个 API：

```text
POST /api/conversations
GET  /api/conversations/{conversation_id}
GET  /api/conversations/{conversation_id}/messages
POST /api/conversations/{conversation_id}/archive
```

只实现 Conversation Management API。

**不要进入 Chat/Application Layer。**

---

# 二、严格范围

允许新增/修改：

```text
backend/app/dto/conversation_api.py
backend/app/api/conversations.py
backend/app/main.py

tests/test_conversation_api.py
tests/test_conversation_api_contract.py
tests/test_conversation_api_architecture_audit.py

docs/evaluation/Phase 4.1 Step 8 — Conversation API Implementation.md
```

禁止修改：

```text
ConversationService
ConversationRepository
Conversation ORM
ConversationTurn ORM

AIOrchestrator
AIRouter
RagService
TextToSQLService
ToolChatService
LLM
Trace
Timeline
Outcome
```

禁止：

```text
POST /api/conversations/{conversation_id}/messages
Context Builder
Memory
Summary
Regenerate
Authentication
Authorization
Pagination
Delete
Switch Project
```

不要修改：

```text
POST /api/ai/chat
```

---

# 三、先阅读

真实阅读：

```text
backend/app/main.py
backend/app/api/orchestrator_chat.py
backend/app/api/assistant_trace.py
backend/app/api/assistant_timeline.py

backend/app/services/conversation_service.py
backend/app/db/conversation_repository.py

tests/test_conversation_api_contract.py
tests/test_conversation_api_architecture_audit.py
```

不要假设接口。

---

# 四、DTO

新增：

```text
backend/app/dto/conversation_api.py
```

定义：

### CreateConversationRequest

仅：

```text
project_id
```

规则：

```text
required
str
1 <= length <= 128
strip whitespace
```

禁止：

```text
conversation_id
status
created_at
updated_at
user_id
tenant_id
title
name
metadata
context
system_prompt
```

---

### ConversationResponse

仅：

```text
conversation_id
project_id
status
created_at
updated_at
```

---

### ConversationTurnResponse

仅：

```text
turn_id
role
content
assistant_request_id
created_at
```

---

### ConversationMessagesResponse

仅：

```text
conversation_id
messages
```

其中：

```text
messages: list[ConversationTurnResponse]
```

空消息必须：

```json
{
  "conversation_id": "...",
  "messages": []
}
```

---

# 五、API Router

新增：

```text
backend/app/api/conversations.py
```

只包含 4 个 route。

不要增加任何额外 endpoint。

---

# 六、POST /api/conversations

调用：

```text
ConversationService.create_conversation(
    project_id=request.project_id
)
```

成功：

```text
HTTP 200
```

返回：

```text
ConversationResponse
```

Conversation ID 必须继续由 Service 生成。

API 不接受客户端 conversation_id。

---

# 七、GET /api/conversations/{conversation_id}

调用：

```text
ConversationService.get_conversation(conversation_id)
```

成功：

```text
200
```

不存在：

```text
ConversationNotFoundError
→ 404
```

只返回 Conversation metadata。

**不要自动查询 turns。**

---

# 八、GET /api/conversations/{conversation_id}/messages

调用：

```text
ConversationService.list_turns(conversation_id)
```

成功：

```text
200
```

不存在：

```text
404
```

存在但没有消息：

```json
{
  "conversation_id": "...",
  "messages": []
}
```

必须保持：

```text
created_at ASC
turn_id ASC
```

API 层不得重新排序。

---

# 九、POST /api/conversations/{conversation_id}/archive

调用：

```text
ConversationService.archive_conversation(conversation_id)
```

成功：

```text
200
```

ACTIVE：

```text
ACTIVE → ARCHIVED
```

ARCHIVED：

```text
ARCHIVED → ARCHIVED
```

保持幂等。

不存在：

```text
404
```

返回：

```text
ConversationResponse
```

---

# 十、Error Mapping

保持 Step 7 Contract：

```text
ConversationNotFoundError
→ 404
```

```text
ConversationArchivedError
→ 409
```

```text
ValueError
→ 422
```

```text
ConversationRepositoryError
→ 500
```

未知异常：

```text
→ 500
```

500 response 不得包含：

```text
traceback
exception message
SQL
database_url
credentials
```

优先复用项目现有 HTTP error handling 风格。

---

# 十一、Path 参数

所有：

```text
conversation_id
```

使用：

```text
min_length=1
max_length=128
```

与现有 Trace API 保持一致。

---

# 十二、Response Model

每个 endpoint 必须明确 response model。

不要直接返回：

```text
ORM
SQLAlchemy Row
Session
Repository
Engine
```

必须经过：

```text
ConversationResponse
ConversationMessagesResponse
ConversationTurnResponse
```

FastAPI 的 `response_model` 会负责响应验证、OpenAPI schema、序列化和字段过滤，因此这里也是安全边界。

---

# 十三、Router 注册

在：

```text
backend/app/main.py
```

按照当前项目既有方式注册：

```text
conversations.router
```

prefix：

```text
/api
```

不要修改其他 Router。

---

# 十四、Service Boundary

API：

```text
API
 ↓
ConversationService
 ↓
ConversationRepository
```

禁止：

```text
API
 ↓
SQLAlchemy
```

API 不直接操作数据库。

ConversationService 不允许 import：

```text
FastAPI
APIRouter
Request
Response
HTTPException
Depends
```

---

# 十五、安全

Response 禁止：

```text
api_key
authorization
password
database_url
SQL
prompt
system_prompt
raw LLM response
RAG chunk
embedding
headers
traceback
SQLAlchemy Session
Engine
```

允许：

```text
content
assistant_request_id
```

不要暴露 LLM Provider request_id。

---

# 十六、Backward Compatibility

确认：

```text
POST /api/ai/chat
```

完全不变：

Request：

```json
{
  "question": "...",
  "project_id": "..."
}
```

Response：

```json
{
  "route": "...",
  "content": "...",
  "data": "...",
  "metadata": "..."
}
```

不要加入：

```text
conversation_id
```

不要修改：

```text
AIOrchestrationResult
```

Trace / Timeline 也不修改。

---

# 十七、Tests

新增：

```text
tests/test_conversation_api.py
```

至少覆盖：

### Create

```text
valid project_id
empty project_id
too long project_id
server-generated conversation_id
exact response fields
```

### Get

```text
existing → 200
missing → 404
```

### Messages

```text
empty → 200 + []
USER
ASSISTANT
ordering
assistant_request_id
forbidden fields absent
```

### Archive

```text
ACTIVE → ARCHIVED
ARCHIVED → ARCHIVED
missing → 404
```

### Errors

```text
RepositoryError → 500
unknown exception → 500
no traceback leakage
no secret leakage
```

### Compatibility

```text
/api/ai/chat unchanged
Trace unchanged
Timeline unchanged
```

### Routes

严格确认：

```text
POST /api/conversations
GET /api/conversations/{conversation_id}
GET /api/conversations/{conversation_id}/messages
POST /api/conversations/{conversation_id}/archive
```

且：

```text
DELETE = 0
PATCH = 0
PUT = 0
POST messages = 0
```

---

# 十八、测试方式

使用当前项目已有：

```text
FastAPI TestClient
```

或者现有 HTTP test fixture。

必须：

```text
DB = 0
Network = 0
LLM = 0
```

可以使用 Fake ConversationService。

不要修改 Production Service。

---

# 十九、更新 Step 7 Contract Tests

更新：

```text
tests/test_conversation_api_contract.py
```

不要删除原有 Contract。

把：

```text
Conversation API = NOT IMPLEMENTED
route count = 0
```

更新为：

```text
Conversation API = IMPLEMENTED
route count = 4
```

其余 Contract 保持：

```text
DTO
status
error mapping
empty semantics
ordering
project binding
UUID
assistant_request_id
security
no pagination
no auth claim
/api/ai/chat unchanged
```

---

# 二十、更新 Architecture Audit

更新：

```text
tests/test_conversation_api_architecture_audit.py
```

Conversation route count：

```text
4
```

必须精确存在：

```text
POST /api/conversations
GET /api/conversations/{conversation_id}
GET /api/conversations/{conversation_id}/messages
POST /api/conversations/{conversation_id}/archive
```

并确认：

```text
DELETE = 0
PATCH = 0
PUT = 0
POST messages = 0
```

同时确认：

```text
ConversationService
ConversationRepository
Conversation ORM
ConversationTurn ORM
```

没有引入 FastAPI / AI Core 依赖。

---

# 二十一、文档

新增：

```text
docs/evaluation/Phase 4.1 Step 8 — Conversation API Implementation.md
```

记录：

```text
API Implementation
Endpoints
DTO
Error Mapping
Security
Backward Compatibility
Tests
Deferred
```

明确：

```text
Message POST = Deferred
Chat Integration = Deferred
Context Builder = Deferred
Memory = Deferred
Auth = Deferred
Pagination = Deferred
```

---

# 二十二、测试命令

先：

```powershell
python -m pytest -q tests/test_conversation_api.py
```

然后：

```powershell
python -m pytest -q tests/test_conversation_api_contract.py
python -m pytest -q tests/test_conversation_api_architecture_audit.py
```

然后：

```powershell
python -m compileall -q backend tests
```

本阶段：

```text
DB = 0
Network = 0
DeepSeek = 0
DB Writes = 0
```

不要运行：

```text
RUN_DB_TESTS=1
```

不要运行：

```text
pytest -q
```

Step 9 再做真实 PostgreSQL API E2E。

---

# 二十三、Git Diff

允许：

```text
backend/app/main.py
backend/app/api/conversations.py
backend/app/dto/conversation_api.py
tests/test_conversation_api.py
tests/test_conversation_api_contract.py
tests/test_conversation_api_architecture_audit.py
docs/evaluation/Phase 4.1 Step 8 — Conversation API Implementation.md
```

禁止修改：

```text
ConversationService
ConversationRepository
Conversation ORM
ConversationTurn ORM
AIOrchestrator
AIRouter
RAG
Tool
T2SQL
LLM
Trace
Timeline
Outcome
```

---

# 二十四、最终报告

严格输出：

```text
Phase 4.1 Step 8 COMPLETE

1. API Implementation
2. Endpoints
3. DTO
4. Create
5. Get
6. Messages
7. Archive
8. Error Mapping
9. Security
10. Backward Compatibility
11. Tests
12. Architecture Audit
13. Contract Tests
14. compileall
15. DB / Network / LLM
16. Production Changes
17. Git Diff
18. Problems
19. Deferred

Phase 4.1 Step 8 READY
Phase 4.1 Step 8 STOP
```

如果发现 Persistence 层 bug：

**立即 STOP 并报告。**

不要为了 API 测试通过修改 Persistence。

---

# 二十五、硬停止

完成后：

**立即 STOP。**

不要进入：

```text
Step 9 DB API E2E
Chat Integration
Context Builder
Memory
Regenerate
Auth
Pagination
```

等待下一步指令。
