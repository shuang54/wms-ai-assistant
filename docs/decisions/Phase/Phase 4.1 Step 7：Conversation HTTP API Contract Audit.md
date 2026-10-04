你现在开始执行：

# Phase 4.1 Step 7：Conversation HTTP API Contract Audit

## 一、阶段目标

Phase 4.1 Step 6 已完成：

```text
Conversation ORM
ConversationTurn ORM
ConversationRepository
ConversationService
        ↓
真实 PostgreSQL 验证
```

当前状态：

```text
Conversation Persistence = READY
Conversation HTTP API = NOT IMPLEMENTED
```

本阶段**不要实现 API**。

唯一目标：

> 审计并冻结 Conversation HTTP API Contract，为下一阶段 API 实现提供明确边界。

本阶段只做：

```text
阅读现有 API 架构
        ↓
设计 Conversation API Contract
        ↓
请求/响应 DTO
        ↓
HTTP Status
        ↓
Error Mapping
        ↓
Security Boundary
        ↓
Backward Compatibility
        ↓
Offline Contract Tests
        ↓
Documentation
        ↓
STOP
```

---

# 二、严格禁止

本阶段禁止修改：

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
实现 Conversation API
修改 main.py router
新增 FastAPI route
修改 /api/ai/chat
修改 AssistantTraceResponse
修改 Timeline API
```

禁止：

```text
DeepSeek
SiliconFlow
真实网络
真实 PostgreSQL
DB migration
DB write
```

禁止进入：

```text
Context Builder
Memory
Regenerate
Authentication
Authorization
Conversation summarization
Agent
MCP
```

---

# 三、先阅读真实 API 实现

必须阅读：

```text
backend/app/main.py
backend/app/api/orchestrator_chat.py
backend/app/api/assistant_trace.py
backend/app/api/assistant_timeline.py
```

以及：

```text
backend/app/services/conversation_service.py
backend/app/dto/
tests/test_*api*
tests/test_*http*
```

重点确认：

1. Router 注册方式
2. API DTO 风格
3. Pydantic 版本及配置
4. HTTPException 使用方式
5. Service → API error mapping
6. 现有 API response 风格
7. `/api/ai/chat` 当前 Contract
8. observability API 当前 Contract

**不要根据之前阶段报告猜接口。**

---

# 四、Conversation API 范围

本阶段只评估以下候选 API：

## 1. 创建 Conversation

```http
POST /api/conversations
```

Request：

```json
{
  "project_id": "vietnam-wms"
}
```

Response：

```json
{
  "conversation_id": "...",
  "project_id": "vietnam-wms",
  "status": "ACTIVE",
  "created_at": "...",
  "updated_at": "..."
}
```

---

## 2. 获取 Conversation

```http
GET /api/conversations/{conversation_id}
```

返回：

```text
Conversation metadata
```

本阶段必须明确：

是否包含：

```text
turns
```

建议先保持：

```text
Conversation metadata only
```

不要在一个 API 中混合完整历史。

---

## 3. 获取 Conversation Turns

候选：

```http
GET /api/conversations/{conversation_id}/messages
```

返回：

```json
{
  "conversation_id": "...",
  "messages": [
    {
      "turn_id": 1,
      "role": "USER",
      "content": "...",
      "assistant_request_id": null,
      "created_at": "..."
    }
  ]
}
```

本阶段只冻结 Contract。

---

## 4. Archive Conversation

候选：

```http
POST /api/conversations/{conversation_id}/archive
```

需要明确：

```text
ACTIVE → ARCHIVED
ARCHIVED → ARCHIVED
不存在 → 404
```

不建议本阶段使用：

```http
DELETE
```

因为：

```text
Archive != Delete
```

物理删除暂时不是业务 API。

---

# 五、不要在 Step 7 增加 Message POST

暂时不要实现：

```http
POST /api/conversations/{conversation_id}/messages
```

原因：

它会立即涉及：

```text
Conversation
    ↓
AIOrchestrator
    ↓
LLM / RAG / Tool / T2SQL
    ↓
Assistant Turn
```

这已经进入：

```text
Chat/Application Layer
```

留到后续阶段。

本阶段只冻结：

```text
Conversation Management API
```

---

# 六、Request DTO

设计最小：

```text
CreateConversationRequest
```

字段：

```text
project_id
```

规则：

```text
required
non-empty
String
```

不要增加：

```text
user_id
tenant_id
title
name
metadata
context
system_prompt
```

这些全部 Deferred。

---

# 七、Response DTO

设计：

```text
ConversationResponse
```

字段只允许：

```text
conversation_id
project_id
status
created_at
updated_at
```

不要暴露 ORM。

不要暴露：

```text
SQLAlchemy Session
engine
database URL
internal repository
```

---

# 八、Message DTO

设计：

```text
ConversationTurnResponse
```

允许：

```text
turn_id
role
content
assistant_request_id
created_at
```

但必须重点评估：

### 是否允许 HTTP 客户端看到：

```text
assistant_request_id
```

建议：

**允许。**

原因：

它是现有：

```text
Conversation Turn
        ↓
assistant_request_id
        ↓
Assistant Trace
        ↓
Timeline
```

的正式 correlation boundary。

但不要暴露：

```text
LLM provider request_id
```

两者完全不同。

---

# 九、Security Boundary

HTTP API 禁止返回：

```text
API Key
Authorization
password
database_url
SQL
prompt
system_prompt
messages raw
RAG chunk
embedding
raw LLM response
LLM headers
internal traceback
SQLAlchemy objects
```

Conversation content 本身：

```text
允许
```

因为它是用户明确保存的 Conversation Turn。

但：

```text
content
```

不允许被写入：

```text
日志
error message
exception
```

---

# 十、Error Mapping

冻结：

### Conversation 不存在

Service：

```text
ConversationNotFoundError
```

API：

```http
404
```

---

### Conversation 已归档

Service：

```text
ConversationArchivedError
```

API：

```http
409
```

如果当前项目已有统一 Conflict mapping，则复用。

---

### 非法 project_id / content

API：

```http
422
```

优先使用 FastAPI/Pydantic validation。

FastAPI 的 path operation 可以直接通过响应状态码和 response model 形成明确的 OpenAPI Contract。

---

### Repository failure

不要：

```text
→ 404
```

不要：

```text
→ []
```

应该映射成当前项目统一的：

```http
500
```

或者当前已有 internal service error mapping。

**先检查现有项目实际规则。**

---

# 十一、Empty Semantics

必须冻结：

### Conversation 不存在

```text
404
```

### Conversation 存在但没有 turns

```json
{
  "messages": []
}
```

不是：

```text
404
```

不是：

```text
null
```

这与 Step 6 Persistence Contract 保持一致。

---

# 十二、Archive Semantics

HTTP：

```http
POST /api/conversations/{conversation_id}/archive
```

必须：

### ACTIVE

```text
200
status=ARCHIVED
```

### 已 ARCHIVED

仍然：

```text
200
status=ARCHIVED
```

保持幂等。

### 不存在

```text
404
```

---

# 十三、Project Isolation

当前 Conversation：

```text
project_id
```

创建后不可修改。

API 不允许：

```text
PATCH project_id
```

也不要增加：

```text
POST /api/conversations/{id}/switch-project
```

同一个 Conversation 永远绑定：

```text
project_id
```

---

# 十四、Conversation ID

必须确认：

```text
server generated
UUID4
```

客户端：

```text
不能指定 conversation_id
```

创建 API：

```json
{
  "project_id": "vietnam-wms"
}
```

不能：

```json
{
  "conversation_id": "client-generated"
}
```

---

# 十五、Ordering

Messages：

```text
created_at ASC
turn_id ASC
```

HTTP response 必须保持 Persistence Layer 的顺序。

不要：

```text
sort by client
```

不要：

```text
reverse
```

不要：

```text
LLM order
```

---

# 十六、Pagination

本阶段：

**不要实现 Pagination。**

但必须记录：

```text
当前没有 pagination
```

原因：

先保持：

```text
Conversation API
```

简单。

未来如果历史变大，再单独设计：

```text
limit
cursor
before
after
```

不要提前加。

---

# 十七、Authentication / Authorization

当前项目没有完整 auth。

因此本阶段必须明确记录：

```text
Authentication = NOT IMPLEMENTED
Authorization = NOT IMPLEMENTED
```

不要假装：

```text
project_id
```

就是安全边界。

尤其：

```text
GET /api/conversations/{conversation_id}
```

当前只依赖：

```text
conversation_id
```

进行定位。

这是一个明确的安全限制。

本阶段不解决。

---

# 十八、Backward Compatibility

必须确认：

```text
POST /api/ai/chat
```

完全不变。

不要增加：

```text
conversation_id
```

到现有 `/api/ai/chat` response。

也不要修改：

```text
AIOrchestrationResult
```

本阶段只是：

```text
新增 Conversation Management API
```

未来 Chat integration 单独处理。

---

# 十九、OpenAPI Contract

检查未来 API 应该具备：

```text
POST /api/conversations
GET /api/conversations/{conversation_id}
GET /api/conversations/{conversation_id}/messages
POST /api/conversations/{conversation_id}/archive
```

每个 API 必须明确：

```text
request model
response model
status code
error mapping
```

本阶段：

**只设计，不注册 route。**

---

# 二十、Contract Tests

新增：

```text
tests/test_conversation_api_contract.py
```

纯离线。

至少测试：

1. Create request fields
2. Create response fields
3. Get response fields
4. Turn response fields
5. Archive response
6. 404 mapping
7. 409 mapping
8. 422 validation
9. empty messages
10. ordering
11. project binding
12. UUID server generation contract
13. assistant_request_id correlation
14. forbidden fields
15. no SQLAlchemy leakage
16. no secrets
17. no pagination
18. no auth claim
19. `/api/ai/chat` unchanged
20. no route implementation yet

---

# 二十一、Architecture Audit

增加：

```text
tests/test_conversation_api_architecture_audit.py
```

检查：

```text
backend/app/api/
```

中：

```text
conversations
```

当前：

```text
route count = 0
```

确认本阶段没有偷偷实现 API。

同时检查：

```text
ConversationService
```

仍然：

```text
不 import FastAPI
不 import APIRouter
不 import Request
```

---

# 二十二、文档

新增：

```text
docs/evaluation/Phase 4.1 Step 7 — Conversation API Contract Audit.md
```

内容：

## 1. Current State

```text
Conversation Persistence = READY
Conversation API = NOT IMPLEMENTED
```

## 2. Proposed Endpoints

列出：

```text
POST /api/conversations
GET /api/conversations/{conversation_id}
GET /api/conversations/{conversation_id}/messages
POST /api/conversations/{conversation_id}/archive
```

## 3. DTO

列出：

```text
CreateConversationRequest
ConversationResponse
ConversationTurnResponse
```

## 4. HTTP Status

```text
200
404
409
422
500
```

## 5. Error Mapping

## 6. Security

## 7. Project Binding

## 8. Ordering

## 9. Empty Semantics

## 10. Authentication Limitation

## 11. Backward Compatibility

## 12. Pagination Deferred

## 13. Message POST Deferred

## 14. Implementation Deferred

---

# 二十三、测试命令

只执行：

```powershell
python -m pytest -q tests/test_conversation_api_contract.py
python -m pytest -q tests/test_conversation_api_architecture_audit.py
python -m compileall -q backend tests
```

不要运行：

```text
pytest -q
```

不要：

```text
RUN_DB_TESTS=1
```

本阶段：

```text
DB = 0
Network = 0
DeepSeek = 0
DB Writes = 0
```

---

# 二十四、Git Diff

确认：

```text
ConversationService = unchanged
ConversationRepository = unchanged
Conversation ORM = unchanged
Conversation API routes = 0
AI Chat API = unchanged
Trace API = unchanged
Timeline API = unchanged
```

允许：

```text
tests/
docs/
```

---

# 二十五、最终报告

严格输出：

```text
Phase 4.1 Step 7 COMPLETE

1. Current Conversation API State
2. Proposed Endpoints
3. Request / Response DTO
4. HTTP Status
5. Error Mapping
6. Empty Semantics
7. Archive Semantics
8. Ordering
9. Project Binding
10. Security
11. Authentication / Authorization
12. Backward Compatibility
13. Pagination
14. Message POST
15. Contract Tests
16. Architecture Audit
17. compileall
18. DB / Network / LLM
19. Git Diff
20. Deferred

Phase 4.1 Step 7 READY
Phase 4.1 Step 7 STOP
```

**完成后立即 STOP。**

不要：

```text
实现 Conversation API
接入 Chat
Context Builder
Memory
Regenerate
Auth
```

等待下一步指令。
