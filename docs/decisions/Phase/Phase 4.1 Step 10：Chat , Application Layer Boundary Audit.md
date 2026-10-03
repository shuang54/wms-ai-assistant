继续执行：

# Phase 4.1 Step 10：Chat / Application Layer Boundary Audit

## 一、阶段目标

Phase 4.1 Step 9 已完成：

```text
Conversation API
    ↓
ConversationService
    ↓
ConversationRepository
    ↓
Real PostgreSQL
    ↓
23 passed
```

现在不要实现 Conversation + AI 的集成。

本阶段唯一目标：

> **审计当前 Chat / AI Application Layer，确定 Conversation 应该在哪一层进入 AI 执行链路。**

只做：

```text
阅读现有 Chat / AI API
        ↓
阅读 AIOrchestrator
        ↓
阅读 ConversationService
        ↓
分析 Request / Turn / Context 边界
        ↓
确定未来 Integration Point
        ↓
Backward Compatibility
        ↓
Contract Tests
        ↓
文档
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
ToolChatService
TextToSQLService
LLMProvider
LLMClient

/api/ai/chat
/api/conversations/*
Assistant Trace
Assistant Timeline
Assistant Outcome
LLM Usage
Tool Execution
RAG Execution
```

禁止：

```text
新增 conversation_id 到 /api/ai/chat
新增 Message POST
Conversation → AI 真正执行
Memory
Context Builder 实现
Summary
Regenerate
Auth
Pagination
```

禁止：

```text
DeepSeek
SiliconFlow
真实外部网络
真实 LLM
```

禁止：

```text
新增 DB table
新增 migration
修改 DB schema
```

---

# 三、先阅读真实代码

必须先阅读：

```text
backend/app/api/orchestrator_chat.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/conversation_service.py
backend/app/db/conversation_repository.py
backend/app/dto/conversation_api.py
```

以及：

```text
backend/app/services/rag_service.py
backend/app/services/tool_chat_service.py
backend/app/services/text_to_sql_service.py
```

如果项目存在：

```text
backend/app/services/chat*
backend/app/application/
backend/app/use_cases/
backend/app/dto/
```

也一起检查。

---

# 四、当前 /api/ai/chat Contract

必须确认当前：

```text
POST /api/ai/chat
```

真实：

### Request

```text
question
project_id?
```

### Response

```text
route
content
data
metadata
```

确认：

```text
conversation_id
```

当前不存在。

本阶段：

**不要增加。**

---

# 五、当前 AI Execution Boundary

确认：

```text
/api/ai/chat
        ↓
AIOrchestrator.execute()
        ↓
Route
        ├── RAG
        ├── Tool
        └── Text-to-SQL
```

记录：

```text
谁创建 assistant_request_id
谁创建 request_id
谁写 LLM Usage
谁写 Tool Execution
谁写 RAG Execution
谁写 Assistant Outcome
```

尤其区分：

```text
assistant_request_id
provider request_id
conversation_id
turn_id
```

不能混用。

---

# 六、Conversation 当前 Boundary

确认：

```text
ConversationService
```

当前只负责：

```text
create_conversation
get_conversation
archive_conversation
append_turn
list_turns
```

不要增加：

```text
execute_ai
send_message
chat
generate_answer
```

本阶段不修改。

---

# 七、核心问题：未来 Conversation → AI 应该在哪里连接

分析至少以下三个方案：

## 方案 A

```text
API
 ↓
ConversationService
 ↓
AIOrchestrator
```

问题：

```text
ConversationService
```

是否会开始依赖 AI Core？

---

## 方案 B

```text
API
 ↓
Chat Application Service
 ├── ConversationService
 └── AIOrchestrator
```

重点评估：

```text
Application Service
```

是否应该成为未来：

```text
Conversation + AI
```

的组合边界。

---

## 方案 C

```text
API
 ↓
AI Application Service
 ├── ConversationService
 └── AIOrchestrator
```

评估与 B 的区别。

---

# 八、必须明确依赖方向

目标应该避免：

```text
ConversationService
        ↓
AIOrchestrator
```

以及：

```text
AIOrchestrator
        ↓
ConversationService
```

形成：

```text
业务领域互相依赖
```

优先评估：

```text
API
 ↓
Application / Use Case Layer
 ├── Conversation
 └── AI Core
```

是否更自然。

但：

**不要因为理论上更漂亮就直接重构。**

必须以当前真实代码结构为准。

---

# 九、Turn Boundary

未来一个：

```text
POST /api/conversations/{id}/messages
```

大概率会形成：

```text
User Request
      ↓
append USER turn
      ↓
AIOrchestrator
      ↓
append ASSISTANT turn
      ↓
response
```

本阶段只分析，不实现。

必须明确：

### USER turn

```text
assistant_request_id = NULL
```

### ASSISTANT turn

```text
assistant_request_id = AI request ID
```

注意：

这里的：

```text
assistant_request_id
```

不是：

```text
provider request_id
```

---

# 十、Failure Boundary

未来 Chat Integration 必须考虑：

```text
USER turn
    ↓
AI failure
```

问题：

> USER turn 是否保留？

根据当前 Conversation 数据模型和业务语义分析。

不要在本阶段修改。

重点说明：

```text
Conversation history
```

是否应该记录：

```text
failed assistant turn
```

以及：

```text
AI failure
```

是否产生 ASSISTANT turn。

只给设计结论。

---

# 十一、Transaction Boundary

分析未来：

```text
USER turn
    ↓
AI execution
    ↓
ASSISTANT turn
```

不能把整个流程放在：

```text
一个 DB transaction
```

因为 AI execution 是外部耗时操作。

确认未来应该保持：

```text
TX1:
USER turn
    ↓
COMMIT

AI execution
    ↓
outside transaction

TX2:
ASSISTANT turn
    ↓
COMMIT
```

本阶段只记录结论，不修改代码。

---

# 十二、Context Boundary

必须明确：

```text
Conversation History
```

与：

```text
LLM Context
```

不是同一个概念。

未来可能：

```text
Conversation turns
        ↓
Context Builder
        ↓
LLM context
```

但本阶段：

**不要实现 Context Builder。**

不要：

```text
ConversationService.list_turns()
        ↓
直接拼 prompt
```

不要让 Conversation Service 负责：

```text
prompt construction
```

---

# 十三、Project Binding

当前：

```text
Conversation.project_id
```

创建后 immutable。

未来 Chat Integration 必须考虑：

```text
request.project_id
```

与：

```text
conversation.project_id
```

不一致怎么办。

本阶段只分析并冻结：

推荐：

```text
conversation.project_id
=
AI execution project_id
```

客户端不能通过每次 message request 覆盖 Conversation project。

如果当前没有 Auth：

必须明确：

```text
project_id binding
≠ authorization
```

---

# 十四、assistant_request_id 生命周期

未来一个 Chat Turn：

```text
POST message
      ↓
AI execution
      ↓
assistant_request_id
```

需要明确：

```text
什么时候生成？
```

建议分析当前 AIOrchestrator 是否已经生成：

```text
request_id / assistant_request_id
```

如果已经存在：

**优先复用，不新增第二套 ID generator。**

不要本阶段实现。

---

# 十五、Observability Correlation

未来理想关联：

```text
conversation_id
      │
      └── turn_id
              │
              └── assistant_request_id
                      │
                      ├── LLM Usage
                      ├── Tool Execution
                      ├── RAG Execution
                      └── Assistant Outcome
```

但注意：

```text
provider request_id
```

仍然只是：

```text
LLM Usage
```

自己的 provider-level ID。

不能替代：

```text
assistant_request_id
```

本阶段只审计，不修改 Trace。

---

# 十六、Backward Compatibility

必须确认：

当前：

```text
POST /api/ai/chat
```

保持：

```text
request:
{
  question,
  project_id?
}
```

以及：

```text
response:
{
  route,
  content,
  data,
  metadata
}
```

未来 Conversation Integration：

**不能直接破坏这个 endpoint。**

本阶段不修改。

重点分析：

```text
新 Conversation Chat API
```

是否应该：

```text
新增 endpoint
```

而不是：

```text
修改旧 /api/ai/chat
```

---

# 十七、推荐的未来边界

只进行设计评估。

如果当前代码结构支持，优先考虑：

```text
/api/conversations/{conversation_id}/messages
                ↓
       Chat Application Service
                ├── ConversationService
                └── AIOrchestrator
```

其中：

```text
ConversationService
```

负责：

```text
Conversation / Turn persistence
```

而：

```text
AIOrchestrator
```

负责：

```text
AI execution
```

Application Service 负责：

```text
workflow composition
```

但：

**不要现在创建这个 Service。**

---

# 十八、Contract Tests

新增：

```text
tests/test_chat_application_boundary_contract.py
```

纯离线。

至少验证：

### 1

ConversationService 不 import：

```text
AIOrchestrator
RagService
ToolChatService
TextToSQLService
```

### 2

AIOrchestrator 不 import：

```text
ConversationRepository
Conversation ORM
```

除非当前真实代码已经存在这种依赖；如果存在，只记录问题，不修改。

### 3

/api/ai/chat 不增加：

```text
conversation_id
```

### 4

Conversation API 不增加：

```text
AI execution
```

### 5

没有新的：

```text
global mutable state
```

### 6

没有新的：

```text
DB dependency
```

### 7

没有：

```text
LLM / Network
```

---

# 十九、Architecture Audit

检查依赖方向：

```text
API
 ↓
Application
 ↓
Domain Services
 ↓
Repository
```

以及：

```text
AI API
 ↓
AIOrchestrator
 ↓
AI Core
```

目标：

```text
Conversation
```

与：

```text
AI Core
```

保持相对独立。

不要强制创建完整 DDD。

---

# 二十、文档

新增：

```text
docs/evaluation/Phase 4.1 Step 10 — Chat Application Boundary Audit.md
```

内容：

## 1. Current Architecture

```text
/api/ai/chat
    ↓
AIOrchestrator

/api/conversations
    ↓
ConversationService
```

## 2. Current Responsibilities

分别列出：

```text
ConversationService
AIOrchestrator
API
```

## 3. Future Integration Point

描述：

```text
Conversation Chat
        ↓
Chat Application Service
        ├── ConversationService
        └── AIOrchestrator
```

## 4. Turn Lifecycle

```text
USER turn
 ↓
AI execution
 ↓
ASSISTANT turn
```

## 5. Transaction Boundary

```text
TX1 USER
AI outside TX
TX2 ASSISTANT
```

## 6. ID Correlation

```text
conversation_id
turn_id
assistant_request_id
provider request_id
```

## 7. Failure Semantics

记录：

```text
USER turn retention
ASSISTANT turn behavior
```

只记录设计结论。

## 8. Project Binding

记录：

```text
conversation.project_id
```

作为 AI execution 的 project binding。

但：

```text
not authorization
```

## 9. Backward Compatibility

说明：

```text
/api/ai/chat unchanged
```

## 10. Deferred

```text
Message POST
Chat Application Service implementation
Context Builder
Memory
Summary
Regenerate
Auth
Pagination
```

---

# 二十一、测试命令

只运行：

```powershell
python -m pytest -q tests/test_chat_application_boundary_contract.py
```

然后：

```powershell
python -m compileall -q backend tests
```

必要时运行已有 Conversation API contract：

```powershell
python -m pytest -q tests/test_conversation_api_contract.py
```

不要运行：

```text
RUN_DB_TESTS=1
```

不要调用：

```text
DeepSeek
SiliconFlow
```

---

# 二十二、Git Diff

原则：

```text
Production Code Changes = 0
```

允许：

```text
tests/test_chat_application_boundary_contract.py
docs/evaluation/Phase 4.1 Step 10 — Chat Application Boundary Audit.md
```

不要修改：

```text
AIOrchestrator
ConversationService
API
Repository
ORM
```

---

# 二十三、失败处理

如果发现真实依赖违反边界：

例如：

```text
ConversationService → AIOrchestrator
```

或：

```text
AIOrchestrator → ConversationRepository
```

不要修复。

报告：

```text
问题：
实际依赖：
期望依赖：
影响：
建议：
```

然后停止。

---

# 二十四、最终报告

严格输出：

```text
Phase 4.1 Step 10 COMPLETE

1. Current Chat Boundary
2. Conversation Boundary
3. AI Execution Boundary
4. Future Integration Point
5. Turn Lifecycle
6. Transaction Boundary
7. Context Boundary
8. Project Binding
9. assistant_request_id
10. Observability Correlation
11. Failure Semantics
12. Backward Compatibility
13. Dependency Direction
14. Contract Tests
15. Architecture Audit
16. compileall
17. Git Diff
18. Production Code Changes
19. DB / Network / LLM
20. Current Limitations

Phase 4.1 Step 10 READY
Phase 4.1 Step 10 STOP
```

---

# 二十五、硬停止

完成后立即停止。

不要进入：

```text
Step 11 Chat Integration
Step 12 Message POST
Context Builder
Memory
Summary
Regenerate
Auth
Pagination
```

只完成：

```text
Chat / Application Layer Boundary Audit
```

等待下一步指令。
