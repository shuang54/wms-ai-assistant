你现在开始执行：

# Phase 4.1 Step 6：Conversation Persistence 真实 DB 验证

## 一、阶段目标

Phase 4.1 Step 5 已完成：

```text
Conversation ORM
ConversationTurn ORM
ConversationRepository
ConversationService
```

但 Step 5 的 DB-gated 测试由于：

```text
PostgreSQL localhost:5432 不可达
```

尚未真正执行。

因此本阶段唯一目标：

> **在数据库可用时，对 Phase 4.1 Step 5 的 Conversation Persistence 做一次完整真实 PostgreSQL 验证。**

本阶段：

```text
不新增功能
不新增 API
不修改 AI Core
不修改 Repository Contract
不修改 ORM Contract
```

---

# 二、严格禁止

禁止修改：

```text
backend/app/api/
backend/app/llm/
backend/app/services/ai_orchestrator_service.py
backend/app/services/rag_service.py
backend/app/services/tool_chat_service.py
backend/app/services/assistant_trace*
backend/app/services/assistant_timeline*
backend/app/services/assistant_outcome*
```

禁止：

```text
Conversation API
Chat API
Context Builder
Memory
Regenerate
Authentication
Authorization
client_message_id
Alembic
新的 migration
新的数据库表
新的字段
```

禁止：

```text
DeepSeek
SiliconFlow
真实外部 API
```

不要修改：

```text
Conversation ORM fields
ConversationTurn ORM fields
Repository 5-method contract
ConversationService 5-method contract
```

---

# 三、Step 1：先检查 PostgreSQL

确认当前项目数据库配置。

只检查：

```text
DATABASE_URL
```

不要输出：

```text
password
credentials
完整 connection string
```

只允许报告：

```text
DB configured = yes/no
host = masked
port = masked/5432
database = masked
```

然后检查：

```text
localhost:5432
127.0.0.1:5432
```

是否可连接。

---

# 四、如果 PostgreSQL 不可用

如果数据库仍然不可达：

**不要修改代码。**

不要：

```text
重构 fixture
修改 Repository
修改 ORM
绕过 DB tests
```

只报告：

```text
DB Verification = BLOCKED

Reason:
PostgreSQL unavailable

Offline Contract = PASS
DB-gated = NOT EXECUTED
```

然后 STOP。

---

# 五、如果 PostgreSQL 可用

先使用当前项目已有：

```text
RUN_DB_TESTS=1
```

机制。

不要建立新的 DB test framework。

运行：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_conversation_persistence_db.py
```

---

# 六、Conversation CRUD

必须真实验证：

### Create

```text
create
 ↓
PostgreSQL
 ↓
ConversationRow
```

确认：

```text
conversation_id != null
project_id 正确
status = ACTIVE
created_at != null
updated_at != null
```

并确认：

```text
conversation_id
```

不是：

```text
assistant_request_id
```

---

# 七、Get

验证：

```text
existing conversation
→ ConversationRow
```

以及：

```text
unknown conversation
→ None
```

注意：

不要把：

```text
None
```

和：

```text
empty turns
```

混淆。

---

# 八、Archive

真实验证：

```text
ACTIVE
 ↓
ARCHIVED
```

确认：

```text
status = ARCHIVED
updated_at changed
```

第二次：

```text
ARCHIVED
 ↓
ARCHIVED
```

必须：

```text
no error
```

并验证：

```text
updated_at
```

是否按照 Step 3/4 冻结语义处理。

---

# 九、Append USER

真实验证：

```text
ACTIVE conversation
 ↓
append USER
```

确认：

```text
role = USER
assistant_request_id IS NULL
content 正确保存
```

---

# 十、Append ASSISTANT

真实验证：

```text
ACTIVE conversation
 ↓
append ASSISTANT
```

确认：

```text
role = ASSISTANT
assistant_request_id IS NOT NULL
content 正确保存
```

---

# 十一、Ordering

至少插入：

```text
USER
ASSISTANT
USER
ASSISTANT
```

验证：

```text
list_turns()
```

返回严格：

```text
created_at ASC
turn_id ASC
```

如果可以制造相同 timestamp：

必须确认：

```text
turn_id
```

能够稳定解决 tie。

---

# 十二、Empty Turns

创建一个没有 Turn 的 Conversation：

```text
list_turns()
```

必须：

```text
()
```

不是：

```text
None
```

---

# 十三、Archived Append

真实验证：

```text
archive conversation
        ↓
append_turn
```

必须：

```text
ConversationArchivedError
```

并确认：

```text
DB Turn count unchanged
```

特别确认：

```text
write before reject = 0
```

---

# 十四、Role Validation

真实验证：

```text
USER + assistant_request_id
```

必须拒绝。

以及：

```text
ASSISTANT + assistant_request_id=None
```

必须拒绝。

以及：

```text
invalid role
```

必须拒绝。

以及：

```text
empty content
```

必须拒绝。

---

# 十五、Atomicity

验证：

```text
append_turn
```

内部：

```text
INSERT turn
+
UPDATE conversation.updated_at
```

在一个 transaction 中。

测试必须确认：

如果第二步失败：

```text
Turn 不存在
```

不能留下：

```text
Turn exists
updated_at unchanged
```

如果当前项目已有 DB fault-injection fixture：

**复用。**

不要新建复杂 transaction framework。

---

# 十六、Conversation Delete Semantics

Step 5 已设计：

```text
Conversation
    ↓
Turn
```

使用：

```text
ON DELETE CASCADE
```

同时：

```text
assistant_request_id
```

不是 ForeignKey。

真实 DB 验证：

```text
delete Conversation
```

如果当前已有测试用例允许：

确认：

```text
Conversation deleted
Turns deleted
```

同时确认：

```text
LLMUsageRecord
ToolExecutionRecord
RagExecutionRecord
AssistantOutcomeRecord
```

不会因为 Conversation 删除而级联删除。

---

# 十七、Residue

测试结束必须确认：

```text
conversation = 0
conversation_turn = 0
```

并确认：

```text
llm_usage_record
tool_execution_record
rag_execution_record
assistant_outcome_record
```

没有被错误清理。

如果已有 fixture：

**复用现有清理机制。**

不要：

```text
TRUNCATE
```

不要清空整个：

```text
ai_ops
```

---

# 十八、Project Binding

真实验证：

```text
Conversation A
project_id = project-a
```

写入 Turn 后：

```text
project_id
```

保持：

```text
project-a
```

不能因为客户端传入：

```text
project-b
```

而改变。

注意：

本阶段：

```text
Authorization = NOT IMPLEMENTED
```

只验证：

```text
project_id immutable
```

不要实现权限系统。

---

# 十九、Persistence Security

真实验证数据库中只存在：

```text
Conversation fields
Turn fields
```

不得出现额外字段：

```text
api_key
password
Authorization
database_url
prompt
messages
SQL
RAG chunk
embedding
raw response
```

不需要做复杂 DLP。

---

# 二十、Backward Regression

真实 DB 验证完成后，至少运行：

```powershell
python -m pytest -q tests/test_conversation_persistence_contract.py
python -m pytest -q tests/test_conversation_service_boundary_contract.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

本阶段不要运行：

```text
全量 pytest
```

除非 DB 测试结束后仍然需要确认已有会话测试没有受到影响。

---

# 二十一、测试结果要求

最终报告必须区分：

```text
Offline Contract:
PASS / FAIL

DB-gated:
PASS / FAIL / BLOCKED

Database availability:
AVAILABLE / UNAVAILABLE
```

不能把：

```text
19 skipped
```

写成：

```text
19 passed
```

---

# 二十二、Git Diff

确认：

```text
Conversation ORM = unchanged
Repository = unchanged
ConversationService = unchanged
```

本阶段原则上：

```text
Production Code Changes = 0
```

如果测试发现真实 bug：

**先停止并报告，不要修改生产代码。**

---

# 二十三、完成报告

严格输出：

```text
Phase 4.1 Step 6 COMPLETE

1. Database Availability
2. Conversation Create/Get
3. Archive
4. USER Turn
5. ASSISTANT Turn
6. Ordering
7. Empty Turns
8. Archived Append
9. Role / Content Validation
10. Atomicity
11. Delete Semantics
12. Project Binding
13. Security
14. Residue
15. Offline Contract
16. DB-gated Tests
17. compileall
18. Production Changes
19. Problems Found
20. Deferred
```

如果 DB 不可用：

```text
Phase 4.1 Step 6 BLOCKED

Reason:
...

Offline Contract = PASS
DB Verification = NOT EXECUTED
```

---

# 二十四、STOP

完成后：

**立即停止。**

不要：

```text
Conversation API
Chat API
AIOrchestrator integration
Context Builder
Memory
Regenerate
Auth
```

等待下一步指令。
