继续 Phase 4.1。

当前状态：

```text
Step 10 COMPLETE
Step 11 COMPLETE
Step 12 COMPLETE
Step 13 COMPLETE
Step 14 COMPLETE
```

当前已经完成：

```text
Conversation Persistence
Conversation API
ChatApplicationService
Conversation Context Builder
Conversation History → Context
AIOrchestrator.execute(context=...)
```

现在只执行：

# Phase 4.1 Step 15：Multi-turn Conversation DB E2E

## 一、唯一目标

使用真实 PostgreSQL，验证当前已经完成的多轮 Conversation 链路：

```text
POST /api/conversations
        ↓
conversation.project_id
        ↓
POST /api/conversations/{id}/messages
        ↓
ChatApplicationService
        ↓
USER Turn 持久化
        ↓
读取历史
        ↓
Context Builder
        ↓
AIOrchestrator
        ↓
ASSISTANT Turn 持久化
        ↓
第二轮
        ↓
历史 Context 正确传入 AI
```

本阶段：

> **只验证真实 DB + FastAPI + Application Service 的多轮闭环。**

不要增加任何新业务能力。

---

# 二、严格禁止

禁止修改：

```text
Conversation ORM
ConversationRepository
ConversationService
ConversationContextBuilder
ChatApplicationService
AIOrchestrator
AIRouter
RAG
Tool Framework
TextToSQL
SQLValidator
SQLExecutor
LLMProvider
LLMClient
Prompt
Conversation API Contract
```

除非真实 E2E 发现明确 bug。

如果发现 bug：

**停止并报告，不要为了通过测试修改生产逻辑。**

禁止：

```text
Memory
Summary
Token Budget
Truncation
Regenerate
Pagination
Auth
Streaming
Embedding
Reranker
DeepSeek
SiliconFlow
```

---

# 三、开始前必须阅读

先阅读：

```text
backend/app/api/conversations.py
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/services/conversation_context_builder.py
backend/app/db/conversation_repository.py
backend/app/db/models/conversation.py
backend/app/db/models/conversation_turn.py
```

以及现有：

```text
tests/test_conversation_api_db_e2e.py
tests/test_conversation_persistence_db.py
tests/test_chat_application_service.py
```

不要重新实现 DB fixture。

优先复用现有：

```text
RUN_DB_TESTS
get_session_factory()
init_db()
TestClient
```

---

# 四、新增测试文件

新增：

```text
tests/test_conversation_multiturn_db_e2e.py
```

只新增测试。

原则上：

```text
Production Code Changes = 0
```

---

# 五、真实 API + Fake AI

必须使用真实：

```text
FastAPI TestClient
```

以及真实：

```text
Conversation API
ChatApplicationService
ConversationService
ConversationRepository
PostgreSQL
Conversation ORM
Context Builder
```

但是：

**AIOrchestrator 必须 Fake/Stub。**

禁止真实：

```text
DeepSeek
SiliconFlow
LLM
```

---

# 六、不要 Mock Conversation Persistence

必须真实执行：

```text
POST /api/conversations
POST /api/conversations/{id}/messages
GET /api/conversations/{id}/messages
```

不能 Mock：

```text
ConversationService
ConversationRepository
Conversation ORM
PostgreSQL
```

只有 AI execution 可以 Fake。

---

# 七、Test 1：Create Conversation

调用：

```http
POST /api/conversations
```

请求：

```json
{
  "project_id": "phase-4-1-e2e"
}
```

验证：

```text
HTTP = 200
conversation_id 非空
project_id = phase-4-1-e2e
status = ACTIVE
```

然后确认 PostgreSQL：

```text
conversation row = 1
conversation_turn row = 0
```

---

# 八、Test 2：First Message

调用：

```http
POST /api/conversations/{id}/messages
```

请求：

```json
{
  "content": "查询当前库存"
}
```

Fake AI 返回：

```text
route = "rag"
content = "当前库存共有 100 件"
metadata.request_id = "assistant-req-001"
metadata.outcome = "SUCCESS"
```

验证：

```text
HTTP = 200
```

并确认数据库：

```text
ConversationTurn:
1. USER
2. ASSISTANT
```

USER：

```text
content = 查询当前库存
assistant_request_id = NULL
```

ASSISTANT：

```text
content = 当前库存共有 100 件
assistant_request_id = assistant-req-001
```

---

# 九、Test 3：First Turn Context

第一轮 Fake Orchestrator 必须记录：

```text
question
context
```

验证：

```text
question = "查询当前库存"
context = None
```

这是：

```text
new conversation
→ no previous history
→ context=None
```

---

# 十、Test 4：Second Message

第二次调用：

```http
POST /api/conversations/{id}/messages
```

请求：

```json
{
  "content": "其中库存最低的 10 个是什么？"
}
```

Fake AI 返回：

```text
route = "rag"
content = "最低库存的10个物料如下..."
metadata.request_id = "assistant-req-002"
metadata.outcome = "SUCCESS"
```

---

# 十一、Second Turn Context

第二次 Fake Orchestrator 必须记录：

```text
question
context
```

验证：

```text
question =
"其中库存最低的 10 个是什么？"
```

context 必须来自真实 Step 13 Builder：

```text
user: 查询当前库存
assistant: 当前库存共有 100 件
```

关键要求：

**当前第二轮 USER 不得进入 context。**

不能出现：

```text
user: 查询当前库存
assistant: 当前库存共有 100 件
user: 其中库存最低的 10 个是什么？
```

---

# 十二、Test 5：数据库最终 Turn 顺序

调用：

```http
GET /api/conversations/{id}/messages
```

验证最终：

```text
1 USER
2 ASSISTANT
3 USER
4 ASSISTANT
```

内容：

```text
1 查询当前库存
2 当前库存共有 100 件
3 其中库存最低的 10 个是什么？
4 最低库存的10个物料如下...
```

验证：

```text
created_at ASC
turn_id ASC
```

---

# 十三、Test 6：Request ID Isolation

验证：

```text
assistant_request_id
```

只来自：

```text
AIOrchestrationResult.metadata["request_id"]
```

即：

```text
assistant-req-001
assistant-req-002
```

并且：

```text
ConversationTurn.assistant_request_id
```

不是：

```text
provider request_id
```

不要混淆：

```text
assistant_request_id
provider request_id
conversation_id
turn_id
```

---

# 十四、Test 7：Conversation Isolation

创建：

```text
conversation-A
conversation-B
```

分别发送：

```text
A:
用户 A 的问题

B:
用户 B 的问题
```

验证：

```text
A context
```

绝对不能出现：

```text
B content
```

以及：

```text
B context
```

绝对不能出现：

```text
A content
```

至少执行：

```text
A1
A2
B1
B2
```

确认历史完全隔离。

---

# 十五、Test 8：Project Binding

创建：

```text
conversation-A
project_id = project-A

conversation-B
project_id = project-B
```

分别发送消息。

验证 Fake Orchestrator Factory 收到：

```text
project-A
project-B
```

严格对应。

用户 message 即使包含：

```text
project-B
```

也不能改变：

```text
conversation-A.project_id
```

---

# 十六、Test 9：Archived Conversation

创建 Conversation。

执行：

```http
POST /api/conversations/{id}/archive
```

然后：

```http
POST /api/conversations/{id}/messages
```

验证：

```text
HTTP = 409
AI calls = 0
DB turn count 不增加
```

---

# 十七、Test 10：AI Failure

创建 Conversation。

Fake Orchestrator：

```text
raise AIOrchestratorExecutionError
```

发送消息。

验证：

```text
HTTP = 500
```

数据库：

```text
USER turn = 1
ASSISTANT turn = 0
```

即：

```text
USER 已提交
AI 失败
ASSISTANT 不创建
```

下一次重新发送正常消息时：

```text
历史 context
```

应该能够看到之前失败请求留下的 USER Turn。

**但本阶段不要修改失败恢复策略。**

---

# 十八、Test 11：History Read Failure

如果现有测试基础设施能够安全注入 Repository failure：

验证：

```text
USER append
↓
history read failure
↓
AI = 0
ASSISTANT = 0
```

并且：

```text
USER turn retained
```

如果当前真实 DB E2E 很难安全注入 Repository failure：

**不要为了这个测试修改 production code。**

可以跳过该 Case，并在报告中说明。

---

# 十九、Test 12：No Cross-request History

创建：

```text
conversation-A
```

完成：

```text
A1
A2
```

创建：

```text
conversation-B
```

发送：

```text
B1
```

验证 B1：

```text
context = None
```

不能因为数据库存在其他 Conversation 的历史而自动获取：

```text
A history
```

---

# 二十、DB Residue

测试开始前记录：

```text
conversation count
conversation_turn count
```

测试完成后清理测试 Conversation。

最终必须：

```text
conversation residue = 0
conversation_turn residue = 0
```

注意：

本测试只允许删除：

```text
本测试创建的 Conversation
```

不得：

```text
TRUNCATE
DELETE 全表
```

不得触碰：

```text
llm_usage_record
tool_execution_record
rag_execution_record
assistant_outcome_record
```

---

# 二十一、Observability Isolation

本阶段 AI 使用 Fake。

确认 Conversation 清理不会影响：

```text
LLM Usage
Tool Execution
RAG Execution
Assistant Outcome
```

如果这些表原本存在数据：

记录：

```text
before
after
```

必须：

```text
unchanged
```

---

# 二十二、Security

API response 不得泄露：

```text
DATABASE_URL
API_KEY
password
Authorization
SQLAlchemy Session
Repository
prompt
raw exception
stacktrace
```

Conversation content 本身可以返回，因为它就是 Message API 的业务数据。

但：

```text
metadata
```

仍然不能出现 secrets。

---

# 二十三、测试数量

目标：

```text
10～15 个高价值 DB E2E tests
```

不要为了数量重复。

---

# 二十四、测试命令

只运行：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_conversation_multiturn_db_e2e.py
```

然后：

```powershell
python -m compileall -q backend tests
```

不要运行：

```text
pytest -q
```

不要真实 LLM。

---

# 二十五、Git Diff

确认：

```powershell
git status --short
git diff --stat
git diff
```

理想情况：

```text
Added:
tests/test_conversation_multiturn_db_e2e.py
```

Production Code Changes：

```text
0
```

如果发现真实生产 bug：

**停止并报告，不要自行修复。**

---

# 二十六、最终报告

严格：

```text
Phase 4.1 Step 15 完成报告

1. Multi-turn E2E
2. First Turn
3. Second Turn
4. Context
5. Current USER Exclusion
6. Turn Ordering
7. Request ID
8. Conversation Isolation
9. Project Binding
10. Archived
11. AI Failure
12. History Read Failure
13. DB Residue
14. Observability Isolation
15. Security
16. Tests
17. compileall
18. DB / Network / LLM
19. Git Diff
20. Production Code Changes
21. Current Limitations

Phase 4.1 Step 15 READY
Phase 4.1 Step 15 STOP
```

必须明确：

```text
Conversation Persistence = E2E verified
Conversation API = E2E verified
ChatApplicationService = E2E verified
Conversation History = E2E verified
Context Builder = E2E verified
AIOrchestrator Context = E2E verified

Memory = 未实现
Summary = 未实现
Token Budget = 未实现
Regenerate = 未实现
Auth = 未实现
Pagination = 未实现
```

完成后立即 STOP。

不要进入 Step 16。

不要实现 Memory / Summary / Token Budget / Regenerate。
