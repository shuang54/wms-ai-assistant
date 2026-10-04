你现在开始执行：

# Phase 4.1 Step 5：Conversation Persistence Layer 实现

## 一、阶段目标

Phase 4.1 Step 1～4 已完成并冻结：

```text
Conversation Architecture
        ↓
Conversation Data Model
        ↓
Conversation Service Boundary
        ↓
Persistence / Repository Design
```

本阶段开始第一次修改生产代码。

唯一目标：

> **实现 Conversation / ConversationTurn 的最小 PostgreSQL Persistence Layer。**

完整目标：

```text
ConversationService
        ↓
ConversationRepository
        ↓
Conversation ORM
        ↓
PostgreSQL
```

本阶段：

**只实现 Persistence Layer。**

不要实现 Conversation API。

不要接入 Chat API。

不要修改 AIOrchestrator。

不要实现 Context Builder。

---

# 二、允许修改范围

允许新增/修改：

```text
backend/app/db/models/conversation.py
backend/app/db/models/conversation_turn.py

backend/app/db/conversation_repository.py
backend/app/db/models/__init__.py

backend/app/db/init_db.py
```

如果当前真实项目结构不同：

> 严格按照 Step 4 已审计的实际路径调整。

允许新增：

```text
tests/test_conversation_persistence_db.py
```

必要时：

```text
docs/evaluation/Phase 4.1 Step 5 — Conversation Persistence Implementation.md
```

---

# 三、严格禁止

本阶段禁止修改：

```text
backend/app/api/
backend/app/services/ai_orchestrator_service.py
backend/app/services/rag_service.py
backend/app/services/tool_chat_service.py
backend/app/llm/
```

禁止实现：

```text
Conversation API
Chat API
Context Builder
Memory
Regenerate
Authentication
Authorization
client_message_id
Conversation Query API
Trace API
Timeline API
```

禁止修改：

```text
LLM Usage
Tool Execution
RAG Execution
Assistant Outcome
Assistant Trace
Assistant Timeline
```

禁止：

```text
Alembic
DeepSeek
SiliconFlow
Network
生产数据迁移
```

---

# 四、Conversation ORM

严格按照 Step 4：

## Conversation

字段：

```text
conversation_id
project_id
created_at
updated_at
status
```

要求：

```text
conversation_id:
String(128)
PRIMARY KEY
NOT NULL
```

```text
project_id:
String(128)
NOT NULL
```

```text
created_at:
DateTime(timezone=True)
server_default=func.now()
NOT NULL
```

```text
updated_at:
DateTime(timezone=True)
server_default=func.now()
NOT NULL
```

```text
status:
String(32)
server_default="ACTIVE"
NOT NULL
```

只允许：

```text
ACTIVE
ARCHIVED
```

不要增加其他业务字段。

---

# 五、ConversationTurn ORM

字段：

```text
turn_id
conversation_id
role
content
assistant_request_id
created_at
```

要求：

```text
turn_id:
BigInteger
PRIMARY KEY
autoincrement
```

```text
conversation_id:
String(128)
NOT NULL
FOREIGN KEY → conversation.conversation_id
ON DELETE CASCADE
```

```text
role:
String(32)
NOT NULL
```

只允许：

```text
USER
ASSISTANT
```

```text
content:
Text
NOT NULL
```

```text
assistant_request_id:
String(128)
NULL
```

```text
created_at:
DateTime(timezone=True)
server_default=func.now()
NOT NULL
```

---

# 六、禁止 assistant_request_id FK

绝对不要：

```text
conversation_turn.assistant_request_id
    ↓
llm_usage_record
```

也不要：

```text
assistant_outcome_record
tool_execution_record
rag_execution_record
```

建立 ForeignKey。

它只是：

```text
Correlation ID
```

不是数据库生命周期依赖。

---

# 七、Index

只创建：

```text
PRIMARY KEY conversation_id
PRIMARY KEY turn_id
```

以及：

```text
ix_conversation_turn_conversation_id_created_at
```

索引：

```text
(conversation_id, created_at)
```

不要增加其他索引。

---

# 八、ORM Registration

按照当前：

```text
backend/app/db/models/__init__.py
```

真实模型注册方式加入：

```text
Conversation
ConversationTurn
```

确保：

```python
Base.metadata
```

能够发现两个模型。

不要修改已有模型注册行为。

---

# 九、Schema

Step 4 已确认：

Conversation 不应进入：

```text
public
```

使用独立 schema。

优先：

```text
ai_ops
```

如果当前项目已有专门的 conversation schema 规范，则遵循实际规范。

重点：

```text
Conversation
ConversationTurn
```

必须位于同一个非业务 schema。

不要进入：

```text
public
```

因为这两个表属于 AI application infrastructure，而不是 WMS Business Schema。

---

# 十、init_db

当前项目没有 Alembic。

继续使用：

```text
Base.metadata.create_all(...)
```

但是需要确保：

```text
schema exists
```

因此在 Conversation schema 需要独立 schema 时：

使用项目已有的：

```text
ensure_*_schema()
```

风格。

不要引入：

```text
Alembic
migration framework
```

不要修改其他表。

---

# 十一、Repository

创建：

```text
backend/app/db/conversation_repository.py
```

严格实现 Step 4 冻结的 5 个方法：

```text
create
get_by_conversation_id
update_status
append_turn
list_turns_by_conversation_id
```

不要增加：

```text
delete
find_by_project
search
count
pagination
```

除非实现过程中发现当前 Service Contract 无法成立。

默认不要增加。

---

# 十二、Repository Constructor

遵循 Step 4 已确认的现有 Repository Pattern：

```python
ConversationRepository(session_factory=None)
```

如果：

```text
session_factory is None
```

则使用：

```text
_get_session_factory()
```

当前项目 DB 未配置：

必须抛：

```text
ConversationRepositoryError
```

不要：

```text
return []
```

不要：

```text
return None
```

---

# 十三、Repository Return DTO

不要返回 ORM。

定义最小 frozen Row DTO，例如：

```text
ConversationRow
ConversationTurnRow
```

如果项目已有统一 Row DTO Pattern：

**复用现有 Pattern。**

Row 必须：

```text
frozen
immutable
```

不能包含：

```text
Session
ORM instance
Connection
Engine
```

---

# 十四、create

`create()`：

输入：

```text
conversation_id
project_id
```

或者根据 Step 3 Service→Repository 的最终冻结签名实现。

注意：

> Conversation ID 的生成属于 Service，而不是 Repository。

Repository 不应该：

```text
uuid.uuid4()
```

Service 负责：

```text
generate conversation_id
↓
Repository.create()
```

---

# 十五、get_by_conversation_id

不存在：

```text
None
```

存在：

```text
ConversationRow
```

必须显式选择需要的字段。

不要：

```text
SELECT *
```

遵循当前 repository 的：

```text
build_*()
```

SQL construction pattern。

---

# 十六、update_status

输入：

```text
conversation_id
status
```

必须：

```text
updated_at = now
```

如果不存在：

```text
ConversationNotFoundError
```

注意：

Repository 层如果当前项目统一将 DB error 转成：

```text
ConversationRepositoryError
```

则保持既有错误风格。

Service 层再映射为：

```text
ConversationNotFoundError
```

不要跨层混用 Service Exception。

---

# 十七、append_turn

这是本阶段最重要的 Repository 方法。

必须在：

```text
ONE DATABASE TRANSACTION
```

中完成：

```text
INSERT conversation_turn
        ↓
UPDATE conversation.updated_at
        ↓
COMMIT
```

如果任意一步失败：

```text
ROLLBACK
```

不能留下：

```text
Turn exists
updated_at unchanged
```

SQLAlchemy `Session.begin()` context manager 在正常退出时提交、异常时回滚，适合作为这里的事务边界。

---

# 十八、append_turn Archived

Repository 不承担完整业务语义，但必须保证：

```text
Conversation.status == ARCHIVED
```

不能继续插入 Turn。

如果当前 Step 4 Contract 要求：

```text
write before reject = 0
```

那么必须：

```text
SELECT conversation
        ↓
check status
        ↓
ARCHIVED
        ↓
reject
```

不要：

```text
INSERT
↓
rollback
```

最终 Service 层负责暴露：

```text
ConversationArchivedError
```

---

# 十九、list_turns_by_conversation_id

严格：

```sql
ORDER BY
    created_at ASC,
    turn_id ASC
```

返回：

```text
tuple[ConversationTurnRow, ...]
```

不存在 Conversation：

按照 Step 3 冻结：

```text
ConversationNotFoundError
```

不要：

```text
[]
```

把不存在与“存在但无 Turn”混为一谈。

存在但没有 Turn：

```text
()
```

---

# 二十、ConversationService

如果 Step 3 当前已经只是 Contract Tests：

本阶段可以实现：

```text
backend/app/services/conversation_service.py
```

但只实现冻结的 5 个方法：

```text
create_conversation()
get_conversation()
archive_conversation()
append_turn()
list_turns()
```

不得增加第六个业务方法。

---

# 二十一、ConversationService create

流程：

```text
project_id
    ↓
validate format
    ↓
generate conversation_id
    ↓
Repository.create()
    ↓
ConversationRow
```

ID 必须服务端生成。

客户端不能提供：

```text
conversation_id
status
created_at
updated_at
```

---

# 二十二、ConversationService archive

流程：

```text
get conversation
    ↓
not found → ConversationNotFoundError
    ↓
ACTIVE → update_status(ARCHIVED)
    ↓
ARCHIVED → no-op / idempotent
```

第二次 archive：

```text
NO ERROR
```

---

# 二十三、ConversationService append_turn

严格：

```text
get conversation
    ↓
not found → NotFound
    ↓
ARCHIVED → ArchivedError
    ↓
validate role
    ↓
validate assistant_request_id
    ↓
Repository.append_turn()
```

USER：

```text
assistant_request_id = None
```

ASSISTANT：

```text
assistant_request_id != None
```

不要让 Repository 自己推断 role。

Service 是业务规则 owner。

---

# 二十四、Service / Repository Error Boundary

Service 对外：

```text
ConversationNotFoundError
ConversationArchivedError
ConversationServiceError
```

Repository 对内：

```text
ConversationRepositoryError
```

不要把 SQLAlchemy：

```text
IntegrityError
OperationalError
SQLAlchemyError
```

泄漏到 Service / API。

---

# 二十五、测试

新增：

```text
tests/test_conversation_persistence_db.py
```

DB-gated：

```text
RUN_DB_TESTS=1
```

至少测试：

### Conversation

```text
create
get
archive
archive idempotent
not found
```

### Turn

```text
append USER
append ASSISTANT
list
ordering
empty
```

### Validation

```text
USER + assistant_request_id → reject
ASSISTANT + None → reject
empty content → reject
invalid role → reject
```

### Archive

```text
archive
append after archive → reject
```

### Atomicity

模拟：

```text
INSERT succeeds
UPDATE fails
```

确认：

```text
Turn rollback
```

### Delete

如果当前 DB test fixture 能安全验证：

```text
Conversation delete
→ Turn deleted
→ observability records remain
```

但不要新增复杂 delete API。

---

# 二十六、Database Isolation

Conversation DB tests 必须：

```text
RUN_DB_TESTS=1
```

才运行。

离线：

```text
SKIP
```

不要让：

```text
pytest -q
```

访问 PostgreSQL。

---

# 二十七、Residue

测试完成后检查：

```text
conversation
conversation_turn
```

测试产生的数据：

```text
must be 0
```

并确认：

```text
llm_usage_record
tool_execution_record
rag_execution_record
assistant_outcome_record
```

没有被删除。

---

# 二十八、Security

Conversation content 测试至少包括：

```text
normal user text
normal assistant text
```

同时确认：

```text
API key
password
Authorization
database URL
raw SQL
RAG chunk
embedding
LLM prompt
```

不会被 Repository 自动保存。

注意：

本阶段不要做复杂 DLP。

只保证：

> Repository 不会自行扩展字段保存这些内容。

---

# 二十九、Regression

先运行：

```powershell
python -m pytest -q tests/test_conversation_persistence_contract.py
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_conversation_persistence_db.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要运行全量 pytest。

不要运行 DeepSeek。

---

# 三十、Migration

本阶段：

```text
Migration = 0
```

如果当前 `init_db()`：

```text
Base.metadata.create_all()
```

能够创建新表：

允许通过现有 init_db 机制创建。

但不要：

```text
Alembic
```

不要生成 migration file。

---

# 三十一、Production Scope

允许修改：

```text
backend/app/db/
backend/app/services/conversation_service.py
backend/app/db/models/
```

不允许：

```text
backend/app/api/
AI Core
RAG
Tool
LLM
Trace
Timeline
Outcome
```

---

# 三十二、Documentation

新增：

```text
docs/evaluation/Phase 4.1 Step 5 — Conversation Persistence Implementation.md
```

记录：

```text
ORM
Repository
Service
Transaction
DB Tests
Residue
Security
```

以及：

```text
Migration = 0
API = unchanged
AI Core = unchanged
RAG = unchanged
Tool = unchanged
LLM = unchanged
```

---

# 三十三、完成报告

严格：

```text
Phase 4.1 Step 5 COMPLETE

1. ORM
2. Repository
3. ConversationService
4. Transaction
5. Conversation CRUD
6. Turn CRUD
7. Archive
8. Ordering
9. Project Binding
10. Error Boundary
11. DB Tests
12. Residue
13. Security
14. compileall
15. Production Changes
16. Deferred
```

最终：

```text
Conversation Persistence = READY
Conversation API = NOT IMPLEMENTED
Chat API = UNCHANGED
AI Core = UNCHANGED
Trace / Timeline = UNCHANGED

Phase 4.1 Step 5 READY
Phase 4.1 Step 5 STOP
```

完成后立即停止。

不要实现 Conversation API。

不要实现 Chat API。

不要修改 AIOrchestrator。

不要实现 Context Builder。

不要实现 Memory。

不要实现 Regenerate。
