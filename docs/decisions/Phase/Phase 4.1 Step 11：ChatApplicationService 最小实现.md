继续执行：

# Phase 4.1 Step 11：ChatApplicationService 最小实现

## 一、阶段目标

Phase 4.1 Step 10 已完成边界审计，已经冻结：

```text
ConversationService
    ↓
只负责 Conversation / Turn persistence

AIOrchestrator
    ↓
只负责 AI execution

ChatApplicationService
    ↓
负责未来 Conversation + AI workflow composition
```

本阶段正式创建：

```text
backend/app/services/chat_application_service.py
```

但只实现：

> **最小 Application Workflow。**

本阶段不创建 Message HTTP API。

不接 `/api/conversations/{conversation_id}/messages`。

---

# 二、本阶段只解决一个问题

建立：

```text
ChatApplicationService
        │
        ├── ConversationService
        │
        └── AIOrchestrator
```

能够表达未来的：

```text
Conversation
    ↓
USER Turn
    ↓
AI Execution
    ↓
ASSISTANT Turn
```

但：

**本阶段只通过 Service-level tests 验证。**

---

# 三、严格范围

## 允许新增

```text
backend/app/services/chat_application_service.py
tests/test_chat_application_service.py
```

如果项目已有最自然的 DTO / Result 类型，可以复用。

必要时可以新增一个极小的 Application Result DTO，但：

**优先复用现有 AIOrchestrationResult。**

---

## 原则上禁止修改

不要修改：

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

api/conversations.py
api/orchestrator_chat.py
main.py

Assistant Trace
Assistant Timeline
Assistant Outcome
LLM Usage
Tool Execution
RAG Execution
```

---

# 四、禁止

本阶段禁止：

```text
/api/conversations/{conversation_id}/messages
```

禁止：

```text
POST Message API
```

禁止：

```text
Context Builder
Memory
Summary
Regenerate
Auth
Pagination
```

禁止：

```text
DeepSeek
SiliconFlow
真实网络
```

禁止：

```text
数据库 Schema 修改
Migration
新增数据库表
```

---

# 五、开始前必须阅读

先阅读：

```text
backend/app/services/conversation_service.py
backend/app/services/ai_orchestrator_service.py
```

以及：

```text
backend/app/dto/conversation_api.py
backend/app/db/conversation_repository.py
```

确认真实：

```text
AIOrchestrationResult
Conversation
ConversationTurn
```

字段。

不要根据 Step 10 报告猜接口。

---

# 六、Application Service 的职责

`ChatApplicationService` 只负责 workflow composition。

即：

```text
1. 获取 Conversation
2. 校验 Conversation 状态
3. 校验 project binding
4. 创建 USER Turn
5. 调用 AIOrchestrator
6. 根据 AI Result 判断是否创建 ASSISTANT Turn
7. 返回 AI Result
```

不要让它负责：

```text
SQL
Prompt
RAG
Tool
LLM
Context construction
Memory
```

---

# 七、建议 API

根据当前项目实际命名风格，实现一个最小方法：

```python
execute_message(
    *,
    conversation_id: str,
    content: str,
)
```

或者：

```python
chat(
    *,
    conversation_id: str,
    content: str,
)
```

具体名称根据现有 Service 命名习惯决定。

不要提前设计：

```text
stream
regenerate
memory
context
```

等参数。

---

# 八、Project Binding

调用：

```text
ConversationService.get_conversation(conversation_id)
```

得到：

```text
conversation.project_id
```

本阶段没有客户端 project_id 参数。

因此：

```text
Conversation.project_id
        ↓
AIOrchestrator project_id
```

必须使用同一个 project_id。

不能让：

```text
ChatApplicationService
```

自行选择：

```text
vietnam-wms
```

也不能硬编码项目名。

---

# 九、Conversation Status

如果：

```text
conversation.status == ARCHIVED
```

必须拒绝执行。

不能：

```text
ARCHIVED
 ↓
AI
```

不能：

```text
ARCHIVED
 ↓
USER turn
```

复用现有：

```text
ConversationArchivedError
```

不要创建新的：

```text
ChatArchivedError
```

---

# 十、USER Turn

AI execution 前：

```text
ConversationService.append_turn(
    conversation_id=conversation_id,
    role=USER,
    content=content,
    assistant_request_id=None,
)
```

必须：

```text
USER.assistant_request_id == None
```

不能提前生成 assistant_request_id。

---

# 十一、AI Execution

USER Turn commit 后：

```text
AIOrchestrator.execute()
```

必须在：

```text
DB transaction
```

之外执行。

即：

```text
TX1
 ↓
USER Turn
 ↓
COMMIT

AIOrchestrator
 ↓
outside TX

TX2
 ↓
ASSISTANT Turn
 ↓
COMMIT
```

不要把 AI execution 包进 ConversationRepository transaction。

---

# 十二、AI Request ID

Step 10 已确认：

```text
AIOrchestrator.execute()
```

已经是：

```text
assistant_request_id
```

的唯一生成点。

因此：

**绝对不要在 ChatApplicationService 再生成一个 request_id。**

AI Result：

```text
metadata["request_id"]
```

是 Application Service 获取 assistant_request_id 的来源。

---

# 十三、ASSISTANT Turn

只有当 AI Result 有：

```text
可展示给用户的最终 content
```

才创建：

```text
ASSISTANT
```

Turn。

使用：

```text
assistant_request_id =
AIResult.metadata["request_id"]
```

不要使用：

```text
LLM provider request_id
```

---

# 十四、ASSISTANT Turn Content

保存：

```text
AIOrchestrationResult.content
```

不要保存：

```text
metadata
SQL
prompt
RAG chunks
Tool raw result
LLM raw response
```

Conversation Turn 是：

```text
conversation history
```

不是：

```text
observability store
```

---

# 十五、REFUSED

如果：

```text
metadata.refused == true
```

或者：

```text
metadata.outcome == REFUSED
```

根据当前真实 Orchestrator contract 判断。

需要明确：

> Refusal 是否属于“可展示给用户的最终内容”。

如果当前 refusal 已经有正常 content：

可以创建 ASSISTANT Turn。

如果当前真实行为：

```text
data=None
content=None
```

则不要人为生成 assistant message。

不要改变现有 refusal 行为。

---

# 十六、FAILED

如果：

```text
AIOrchestrator
```

抛出异常：

必须：

```text
USER Turn 保留
```

不要 rollback USER Turn。

并且：

```text
不要创建空 ASSISTANT Turn
```

即：

```text
USER
 ↓
AI failure
 ↓
no empty assistant turn
```

异常继续向上抛出。

不要在 Application Service 重新包装异常。

---

# 十七、EMPTY

如果 AI Result：

```text
outcome == EMPTY
```

必须根据当前真实 result content 判断。

原则：

> 如果存在可展示的业务内容，可以保存 ASSISTANT Turn；如果没有最终内容，则不创建空 Turn。

不要把：

```text
EMPTY
```

自动等价为：

```text
FAILED
```

也不要创建：

```text
"[EMPTY]"
```

之类的伪造 content。

---

# 十八、T2SQL row_count=0

特别验证：

```text
T2SQL
row_count = 0
outcome = SUCCESS
```

仍然可以创建：

```text
ASSISTANT Turn
```

如果：

```text
content
```

存在。

不要把数据库空结果当成 Conversation EMPTY。

---

# 十九、Transaction Failure

如果：

```text
USER Turn append
```

失败：

```text
AIOrchestrator
```

不能被调用。

验证：

```text
append USER failed
→ AI calls = 0
```

如果：

```text
ASSISTANT Turn append
```

失败：

AI 已经执行完成。

此时：

```text
USER Turn
```

已经存在。

不要重新执行 AI。

不要自动 retry。

异常直接向上传播。

---

# 二十、Application Service 不直接访问 DB

严格禁止：

```python
Session
```

或：

```text
SQLAlchemy
Repository
engine
connection
```

进入：

```text
ChatApplicationService
```

它只能调用：

```text
ConversationService
AIOrchestrator
```

---

# 二十一、依赖方向

必须形成：

```text
ChatApplicationService
        ├── ConversationService
        └── AIOrchestrator
```

禁止：

```text
ConversationService
        ↓
ChatApplicationService
```

禁止：

```text
AIOrchestrator
        ↓
ChatApplicationService
```

避免循环依赖。

---

# 二十二、测试

新增：

```text
tests/test_chat_application_service.py
```

全部使用 Fake / Stub。

不要 DB。

不要 LLM。

不要网络。

---

# 二十三、必须覆盖测试

至少：

### 1. Normal success

```text
Conversation ACTIVE
 ↓
USER Turn
 ↓
AI success
 ↓
ASSISTANT Turn
```

验证：

```text
USER assistant_request_id = None
ASSISTANT assistant_request_id = AI request_id
```

---

### 2. Project binding

验证：

```text
Conversation.project_id
==
AIOrchestrator.project_id
```

---

### 3. Archived

```text
ARCHIVED
 ↓
reject
 ↓
AI calls = 0
```

---

### 4. AI failure

```text
USER Turn committed
 ↓
AI failure
 ↓
no ASSISTANT Turn
```

---

### 5. Empty result

根据当前真实 content 行为验证：

```text
EMPTY
```

不会生成伪造 assistant content。

---

### 6. T2SQL row_count=0

验证：

```text
SUCCESS
```

而不是：

```text
EMPTY
```

---

### 7. Refusal

验证保持当前 refusal semantics。

不要触发：

```text
retry
```

---

### 8. User append failure

验证：

```text
AI calls = 0
```

---

### 9. Assistant append failure

验证：

```text
AI calls = 1
```

并且：

```text
AI 不重新执行
```

---

### 10. Request ID

验证：

```text
assistant_request_id
```

来自：

```text
AI Result metadata.request_id
```

Application Service 不生成第二个 ID。

---

### 11. No DB dependency

AST 检查：

```text
ChatApplicationService
```

不得 import：

```text
sqlalchemy
psycopg
backend.app.db
```

---

### 12. No AI leakage into ConversationService

AST 检查：

```text
ConversationService
```

仍然不能 import：

```text
AIOrchestrator
ChatApplicationService
RagService
ToolChatService
TextToSQLService
```

---

# 二十四、Result Contract

Application Service 最终返回：

```text
AIOrchestrationResult
```

不要重新创建：

```text
ChatResult
AIChatResult
ConversationChatResult
```

除非当前真实代码明确需要。

尽量保持：

```text
AIOrchestrationResult
```

原样。

---

# 二十五、Conversation History

本阶段：

**不要读取历史 turns 并拼到 prompt。**

即不要：

```text
list_turns()
 ↓
prompt
```

当前：

```text
Conversation History
```

只是持久化历史。

Context Builder 留到后续阶段。

---

# 二十六、Security

Application Service 不得把：

```text
SQL
prompt
messages
RAG chunks
tool raw result
API key
DATABASE_URL
```

写入：

```text
ConversationTurn.content
```

只有：

```text
USER content
ASSISTANT final content
```

允许保存。

---

# 二十七、测试数量

建议：

```text
15～25 tests
```

目标：

```text
workflow correctness
```

不要为了数量重复测试。

---

# 二十八、Git Diff

完成后：

```powershell
git status --short
git diff --stat
```

正常情况下只允许新增：

```text
backend/app/services/chat_application_service.py
tests/test_chat_application_service.py
```

不要修改：

```text
ConversationService
AIOrchestrator
API
Repository
ORM
```

---

# 二十九、运行命令

只运行：

```powershell
python -m pytest -q tests/test_chat_application_service.py
```

然后：

```powershell
python -m compileall -q backend tests
```

不要：

```text
RUN_DB_TESTS=1
```

不要：

```text
DeepSeek
```

不要：

```text
全量 pytest
```

---

# 三十、失败处理

如果发现：

```text
AIOrchestrator interface
ConversationService interface
```

与本任务假设不同：

**不要修改现有接口。**

先报告：

```text
问题：
实际接口：
任务预期：
影响：
```

如果发现真实 production bug：

**停止，不修。**

---

# 三十一、最终报告

严格输出：

```text
Phase 4.1 Step 11 COMPLETE

1. 新增文件
2. ChatApplicationService Boundary
3. Workflow
4. Project Binding
5. USER Turn
6. AI Execution
7. ASSISTANT Turn
8. Request ID
9. REFUSED
10. EMPTY
11. FAILED
12. Transaction Boundary
13. Error Handling
14. Conversation History
15. Security
16. Dependency Direction
17. Tests
18. compileall
19. Git Diff
20. Production Code Changes
21. DB / Network / LLM
22. Current Limitations

Phase 4.1 Step 11 READY
Phase 4.1 Step 11 STOP
```

---

# 三十二、硬停止

完成后立即停止。

不要进入：

```text
Step 12 Message POST
Context Builder
Memory
Summary
Regenerate
Auth
Pagination
```

本阶段只完成：

```text
ChatApplicationService
    ↓
ConversationService
    ↓
AIOrchestrator
```

的最小 workflow composition。

等待下一步指令。
