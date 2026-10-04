你现在开始执行：

# Phase 4.1 Step 4：Conversation Persistence / Repository / Transaction Design Audit

## 一、阶段目标

Phase 4.1 Step 1～3 已经完成：

```text
Step 1
Conversation Architecture
        ↓
Step 2
Conversation / Turn Data Model
        ↓
Step 3
Conversation Service Boundary
```

当前已经冻结：

```text
Conversation
ConversationTurn
ConversationService
ConversationRepository
AIOrchestrator
Assistant Trace
Assistant Timeline
Assistant Outcome
```

本阶段仍然：

**只做 Design / Audit，不实现生产代码。**

唯一目标：

> **确定 Conversation / ConversationTurn 持久化层应该如何落地，以及 Repository / Transaction / ORM / Migration 的最小实现边界。**

最终形成：

```text
ConversationService
        ↓
ConversationRepository
        ↓
SQLAlchemy ORM
        ↓
PostgreSQL
```

并明确：

```text
Conversation transaction
        ≠
AI / LLM transaction
```

---

# 二、严格禁止

本阶段禁止：

```text
修改 backend/app/
修改 API
修改 AIOrchestrator
修改 RAG
修改 Tool
修改 LLM
修改 Trace
修改 Timeline
修改 Outcome
```

禁止实际创建：

```text
ORM Model
Repository
Migration
Database Table
Foreign Key
Index
```

禁止：

```text
Alembic migration
PostgreSQL write
DB seed
DeepSeek
SiliconFlow
Network
```

禁止：

```text
Conversation API
Chat API
Context Builder
Memory
Regenerate API
Authentication
Authorization
client_message_id
```

本阶段只产生：

```text
tests/
docs/
```

---

# 三、必须先阅读真实数据库实现

先阅读：

```text
backend/app/db/
```

尤其搜索：

```text
Base
DeclarativeBase
Mapped
mapped_column
relationship
ForeignKey
Index
UniqueConstraint
server_default
created_at
updated_at
status
BIGINT
String
Text
```

重点检查已有 ORM：

```text
backend/app/db/models/
backend/app/db/*.py
```

确认：

1. Base 从哪里来
2. ORM Model 命名规范
3. schema 如何指定
4. PostgreSQL schema 是否使用 `ai_ops`
5. UUID / String PK 的既有实践
6. BIGINT 自增的既有实践
7. timestamp 类型
8. timezone 是否统一
9. enum / string status 的实践
10. index / constraint 的命名规范
11. migration 当前是否使用 Alembic
12. init_db 如何创建表
13. test DB 如何初始化
14. repository 如何管理 Session

**不要根据之前阶段报告猜。**

---

# 四、必须检查已有 Repository Pattern

重点阅读：

```text
backend/app/db/
```

寻找：

```text
*_repository.py
```

重点检查：

```text
create(...)
get_by_id(...)
list_by_...
update_...
delete_...
```

确认：

### Repository 是否：

```text
接收 Session
```

还是：

```text
内部创建 Session
```

确认：

```text
commit
flush
rollback
refresh
```

到底由谁负责。

这非常重要。

---

# 五、Transaction Ownership

根据真实 Repository Pattern，最终必须明确：

### 方案 A

```text
Service
 ↓
Repository
 ↓
Session
 ↓
commit
```

或者：

### 方案 B

```text
Service
 ↓
Repository
 ↓
Session
```

由 Service / Unit of Work：

```text
commit
```

本阶段不要自行选择。

**必须遵循当前项目已有 Repository / Session 风格。**

---

# 六、Conversation ORM Model

根据 Step 2 冻结的模型，设计 ORM：

## Conversation

字段必须对应：

```text
conversation_id
project_id
created_at
updated_at
status
```

重点确认：

```text
conversation_id
```

是否：

```text
String(128)
primary_key=True
nullable=False
```

以及：

```text
project_id
String
nullable=False
```

不要新增：

```text
title
user_id
tenant_id
metadata
last_message
message_count
```

这些全部延期。

---

# 七、ConversationTurn ORM

严格对应 Step 2：

```text
turn_id
conversation_id
role
content
assistant_request_id
created_at
```

重点：

```text
turn_id
```

必须使用当前项目 PostgreSQL BIGINT 自增实践。

不要使用：

```text
UUID
String
assistant_request_id
```

作为 Turn PK。

---

# 八、Foreign Key

确认：

```text
conversation_turn.conversation_id
    ↓
conversation.conversation_id
```

是否应该使用：

```text
FOREIGN KEY
```

根据当前项目 DB 设计规范判断。

如果使用：

必须明确：

```text
ON DELETE
```

语义。

Step 2 已冻结：

> Conversation 物理删除时删除 Conversation + Turns，但不得级联删除 Observability Records。

因此：

```text
Conversation
    ↓
Turn
```

可以考虑 cascade。

但是：

```text
Turn
    ↓
assistant_request_id
    ↓
Observability
```

**绝对不要建立 DB FK。**

因为：

```text
Observability lifecycle
≠
Conversation lifecycle
```

---

# 九、Delete Semantics

虽然本阶段不实现 Delete API，但必须设计：

```text
Conversation physical delete
```

应该：

```text
Conversation
    ↓
Turns
```

一起删除。

但是：

```text
LLMUsageRecord
ToolExecutionRecord
RagExecutionRecord
AssistantOutcomeRecord
```

全部保留。

原因：

```text
assistant_request_id
```

只是 correlation，不是 Conversation FK。

---

# 十、Index

只设计 Step 2 已冻结的最小 Index：

```text
PRIMARY KEY conversation_id
```

以及：

```text
(conversation_id, created_at)
```

用于：

```text
list_turns_by_conversation_id()
```

不要新增：

```text
project_id index
status index
assistant_request_id index
role index
created_at global index
```

除非真实现有架构明确要求。

---

# 十一、Ordering

Repository 必须保证：

```sql
ORDER BY
    created_at ASC,
    turn_id ASC
```

不能只：

```sql
ORDER BY created_at
```

因为 timestamp 可能相同。

也不能：

```sql
ORDER BY turn_id
```

作为业务语义依据。

最终设计：

```text
created_at
    +
turn_id
```

---

# 十二、Status

Conversation：

```text
ACTIVE
ARCHIVED
```

保持：

```text
String(32)
```

不要创建：

```text
Enum
```

如果当前项目既有 status 统一使用 string，则继续保持。

禁止新增：

```text
DELETED
CLOSED
CANCELLED
SUSPENDED
```

---

# 十三、Role

Step 2：

```text
USER
ASSISTANT
```

本阶段确认当前 ORM / DB 中如何表示：

```text
String
```

还是已有 Enum Pattern。

如果没有统一 enum infrastructure：

优先：

```text
String
```

不要新增复杂 Enum。

---

# 十四、Repository Contract

冻结：

```text
create
get_by_conversation_id
update_status
append_turn
list_turns_by_conversation_id
```

必须检查 Repository：

### create

```text
创建 Conversation
```

### get

```text
不存在 → None
```

### update_status

```text
不存在 → NotFound
```

还是：

```text
None
```

必须与现有 Repository 风格一致。

### append_turn

Step 3 冻结：

```text
原子：
Insert Turn
+
Update Conversation.updated_at
```

因此必须确认：

> 这两个 DB 操作是否在同一个 transaction boundary。

---

# 十五、append_turn 原子性

必须设计：

```text
BEGIN
  INSERT conversation_turn
  UPDATE conversation.updated_at
COMMIT
```

如果：

```text
INSERT success
UPDATE failed
```

则：

```text
ROLLBACK
```

不能留下：

```text
Turn exists
updated_at unchanged
```

---

# 十六、Archive 原子性

设计：

```text
Conversation.status:
ACTIVE → ARCHIVED
```

并：

```text
updated_at = now
```

必须是一个原子操作。

第二次：

```text
ARCHIVED → ARCHIVED
```

保持幂等。

---

# 十七、Append Archived

必须明确：

```text
Conversation.status == ARCHIVED
```

调用：

```text
append_turn()
```

结果：

```text
ConversationArchivedError
```

并且：

```text
DB write = 0
```

不要：

```text
insert turn
→ 后面 rollback
```

如果可以在写入前判断，更符合当前边界。

---

# 十八、Project Binding

Conversation 创建时：

```text
project_id
```

写入 DB。

之后：

```text
get
append
archive
list
```

均：

```text
by conversation_id
```

不要允许：

```text
client project_id
```

覆盖已有绑定。

本阶段只验证 Contract。

不实现 Authorization。

---

# 十九、ConversationService 与 Repository

最终：

```text
ConversationService
    ↓
ConversationRepository
```

Service 不允许：

```text
Session
select
insert
update
delete
engine
```

Repository 不允许：

```text
AIOrchestrator
RagService
ToolChatService
LLM
FastAPI
HTTPException
```

保持：

```text
Service = business lifecycle
Repository = persistence
```

---

# 二十、ORM 与 DTO 分离

ORM Model：

```text
SQLAlchemy
```

Repository 对外：

```text
Frozen Row DTO
```

禁止：

```text
ORM object
```

从 Repository 直接返回给 Service。

禁止：

```text
Session
```

逃逸。

---

# 二十一、Migration Strategy

只审计当前 migration strategy。

确认项目到底：

```text
Alembic
```

还是：

```text
create_all
```

或其他机制。

本阶段不创建 migration。

文档必须记录：

```text
Future migration strategy:
...
```

并明确：

```text
Step 4 does not execute migration.
```

---

# 二十二、Test DB Strategy

检查当前测试：

```text
tests/conftest.py
```

以及 DB fixtures。

明确：

```text
Conversation DB tests
```

未来应该如何：

```text
create schema
create tables
transaction
rollback
cleanup
```

本阶段不要创建实际表。

---

# 二十三、Persistence Security

Conversation 持久化层禁止保存：

```text
API key
password
Authorization
headers
database URL
LLM prompt
system prompt
tool definitions
raw SQL
RAG chunk content
embedding
DB Session
ORM Session
raw provider response
```

Conversation Turn `content`：

只允许：

```text
用户可见的普通 user/assistant 内容
```

本阶段只做静态 Contract Test。

---

# 二十四、Contract Tests

新增：

```text
tests/test_conversation_persistence_contract.py
```

纯离线。

至少覆盖：

### ORM design

```text
Conversation fields
ConversationTurn fields
```

### PK

```text
conversation_id → String(128) PK
turn_id → BIGINT
```

### FK

```text
Turn → Conversation
```

### No Observability FK

确认：

```text
assistant_request_id
```

不是 ForeignKey。

### Index

确认：

```text
conversation_id + created_at
```

存在。

### Status

只有：

```text
ACTIVE
ARCHIVED
```

### Role

只有：

```text
USER
ASSISTANT
```

### Ordering

```text
created_at ASC
turn_id ASC
```

### Repository

五个方法。

### Service

五个方法。

### Security

禁止字段 / dependency。

### Transaction

append_turn atomicity contract。

### Delete

Conversation delete ≠ observability delete。

### Migration

当前 strategy 正确记录。

---

# 二十五、AST Audit

继续使用：

```text
AST
```

检查：

### ConversationService

不得出现：

```text
sqlalchemy
Session
select
insert
update
delete
engine
```

### ConversationRepository

不得出现：

```text
AIOrchestrator
RagService
Tool
LLM
FastAPI
HTTPException
```

### ORM Model

不得 import：

```text
FastAPI
AI
LLM
RAG
Tool
```

---

# 二十六、文档

新增：

```text
docs/evaluation/Phase 4.1 Step 4 — Conversation Persistence Audit.md
```

必须包含：

1. Current DB architecture
2. ORM conventions
3. Repository conventions
4. Transaction ownership
5. Conversation ORM
6. Turn ORM
7. FK
8. Index
9. Ordering
10. Status / Role
11. Delete semantics
12. Project binding
13. Security
14. Migration strategy
15. Test DB strategy
16. Deferred Implementation

明确：

```text
Production Code = 0
DB Schema = unchanged
Migration = 0
DB Writes = 0
Network = 0
LLM = 0
```

---

# 二十七、测试命令

只执行：

```powershell
python -m pytest -q tests/test_conversation_persistence_contract.py
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

不要执行：

```text
RUN_DB_TESTS
```

不要：

```text
python -m pytest -q
```

不要：

```text
alembic upgrade
```

不要：

```text
init_db
```

---

# 二十八、Production Changes

必须保持：

```text
backend/app = unchanged
backend/app/api = unchanged
backend/app/db = unchanged
migrations = unchanged
.github = unchanged
```

只允许：

```text
tests/
docs/
```

---

# 二十九、完成后报告

严格：

```text
Phase 4.1 Step 4 COMPLETE

1. Current DB Architecture
2. ORM Convention
3. Repository Convention
4. Transaction Ownership
5. Conversation ORM Design
6. Turn ORM Design
7. FK
8. Index
9. Ordering
10. Status / Role
11. Delete Semantics
12. Project Binding
13. Security
14. Migration Strategy
15. Test DB Strategy
16. Contract Tests
17. compileall
18. Production Changes
19. Deferred Implementation
```

最终：

```text
Production Code = 0
DB Schema = unchanged
Migration = 0
DB Writes = 0
Network = 0
LLM = 0

Phase 4.1 Step 4 READY
Phase 4.1 Step 4 STOP
```

完成后立即停止。

不要实现 ORM。

不要实现 Repository。

不要实现 Migration。

不要实现 Conversation API。

不要实现 Chat API。

等待下一步。
