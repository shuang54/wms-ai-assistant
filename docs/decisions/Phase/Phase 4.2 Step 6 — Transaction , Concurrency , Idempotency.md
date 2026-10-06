你现在开始正式实施：

# Phase 4.2 Step 6 — Transaction / Concurrency / Idempotency

项目：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

在已经冻结的：

```text
Step 1
Step 1A
Step 2
Step 4A
Step 5A
```

基础上，正式实现：

```text
Message Idempotency
+
Transaction Boundary
+
Concurrency Protection
+
Duplicate Message Replay
```

本阶段是 Phase 4.2 第一个允许修改：

```text
Backend
DB Schema
API
Tests
```

的实现阶段。

---

# 二、已经冻结的架构，不得重新设计

以下决策已经 CLOSED，直接实现，不要重新讨论。

## 1. Idempotency Architecture

```text
Architecture A

conversation_turn.idempotency_key
        +
UNIQUE(conversation_id, idempotency_key)
```

---

## 2. API

使用：

```http
Idempotency-Key: <client-key>
```

作为 HTTP Header。

Body 保持：

```json
{
  "content": "..."
}
```

不要把 `idempotency_key` 放进 JSON Body。

FastAPI 使用 `Header` 声明请求头；Header 参数可以直接设置默认值和长度校验。

---

## 3. Key

约束：

```text
max length = 128
```

处理：

```text
Header 不存在 → no idempotency
Header = whitespace → no idempotency
Header = non-empty ≤128 → enabled
Header >128 → HTTP 422
```

服务端：

```text
不得生成 key
不得修改 key
不得 trim 后改变客户端 key
```

注意：

可以使用：

```python
key.strip()
```

判断空白，但对于合法 key：

**不得 silently rewrite 原始 key 后再持久化。**

如果项目当前规范明确要求规范化，则必须先记录，否则保持原值。

---

# 三、OD-26 已关闭

正式语义：

```text
EMPTY
=
AI 正常执行完成
+
没有可展示 content
```

但是：

```text
EMPTY ≠ idempotency completed
```

因为：

```text
没有 ASSISTANT Turn
→ 没有可重放消息
→ NOT_COMPLETED
→ 同 key 可以 retry
```

因此：

```text
completed
=
USER Turn exists
AND
ASSISTANT Turn exists
```

---

# 四、Duplicate Replay 已冻结

OD-34：

```text
CLOSED
Option A — Message Replay
```

同：

```text
conversation_id
+
idempotency_key
+
same payload
+
USER Turn + ASSISTANT Turn exists
```

则：

```text
不执行 AI
不创建 USER Turn
不创建 ASSISTANT Turn
返回 HTTP 200
```

---

# 五、Duplicate Response Contract

当前：

```text
ConversationMessageResponse
```

Step 6 唯一 DTO 微调：

```text
route: str
```

改为：

```text
route: str | None = None
```

字段名集合不得改变。

Duplicate response：

```json
{
  "route": null,
  "content": "<persisted assistant content>",
  "data": null,
  "metadata": {
    "request_id": "<persisted assistant_request_id>",
    "idempotent_replay": true
  }
}
```

注意：

### route

必须：

```text
null
```

禁止：

```text
"replay"
"unknown"
"conversation"
"duplicate"
"chat"
```

也禁止从 Trace / LLM / RAG / Tool / SQL observation 推断。

---

### data

必须：

```text
null
```

原因：

当前 ConversationTurn 没有持久化 runtime data。

不要新增 result snapshot。

当前已接受：

```text
KL-2
Text-to-SQL data 不参与 duplicate message replay
```

---

### metadata

只允许增加：

```json
"idempotent_replay": true
```

并且：

```text
request_id
```

来自：

```text
ASSISTANT Turn.assistant_request_id
```

不得重新生成 request_id。

不得加入：

```text
turn_id
conversation_id
idempotency_key
outcome
```

---

# 六、Idempotency 状态机

实现必须遵循：

```text
                         ┌─────────────────────┐
                         │ No USER Turn        │
                         └──────────┬──────────┘
                                    │
                                    │ first request
                                    ▼
                         ┌─────────────────────┐
                         │ USER Turn exists    │
                         │ ASSISTANT absent    │
                         └──────────┬──────────┘
                                    │
                          ┌─────────┴──────────┐
                          │                    │
                       retry                 AI success
                          │                    │
                          ▼                    ▼
                 same USER Turn       ASSISTANT Turn
                                           exists
                                               │
                                               ▼
                                          COMPLETED
                                               │
                                               ▼
                                           replay
```

其中：

```text
USER exists + ASSISTANT absent
=
NOT_COMPLETED
```

覆盖：

```text
EMPTY
REFUSED + empty
FAILED exception
Crash A
Crash B
TX2 failure
```

---

# 七、Same Key / Different Payload

必须使用：

```text
SHA-256(conversation_id + content)
```

进行 payload comparison。

不需要新增 fingerprint 数据库字段。

已有 USER Turn：

```text
idempotency_key
content
```

可以重新计算 fingerprint。

---

## Case A

```text
same conversation
same key
same content
USER + ASSISTANT exists
```

结果：

```text
200
duplicate replay
AI = 0
```

---

## Case B

```text
same conversation
same key
different content
```

结果：

```text
409
IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD
```

不得执行 AI。

不得创建第二条 USER Turn。

---

## Case C

```text
different conversation
same key
```

结果：

```text
independent
```

---

## Case D

```text
same content
different key
```

结果：

```text
independent
```

---

## Case E

```text
no Idempotency-Key
```

保持历史行为：

```text
independent
```

不得自动生成 key。

---

# 八、Transaction Boundary

保持当前整体模型：

```text
TX1
BEGIN
    create USER Turn
COMMIT

AI execution

TX2
BEGIN
    create ASSISTANT Turn
COMMIT
```

不要把：

```text
AIOrchestrator.execute()
```

放进数据库 transaction。

不要持有 DB transaction 跨越：

```text
LLM
RAG
Tool
Text-to-SQL
```

调用。

---

# 九、第一请求流程

有 Idempotency-Key：

```text
POST
 ↓
validate Header
 ↓
load Conversation
 ↓
check archived
 ↓
check idempotency key
 ↓
if existing:
    compare payload
else:
    create USER Turn
 ↓
COMMIT TX1
 ↓
build history/context
 ↓
AIOrchestrator.execute()
 ↓
AI Result
 ↓
if displayable content + request_id:
    TX2
    create ASSISTANT Turn
    COMMIT
 ↓
return response
```

---

# 十、关键要求：Retry 不得污染 Context

这是本阶段非常重要的要求。

当：

```text
USER Turn 已存在
ASSISTANT Turn 不存在
```

因为 retry：

```text
same idempotency key
```

不能再创建 USER Turn。

同时：

**当前这个 USER Turn 不得作为历史 context 再次注入本次 AI。**

否则：

```text
existing USER turn
+
current retry message
```

会导致模型看到重复 user message。

必须沿用当前：

```text
ConversationContextBuilder
```

的既有排除当前 turn 逻辑。

如果当前实现通过：

```text
turn_id
```

排除当前消息：

retry 时必须继续使用原始 USER Turn 的：

```text
turn_id
```

而不是创建一个新的 turn。

---

# 十一、ASSISTANT Turn

ASSISTANT Turn：

```text
idempotency_key = NULL
```

这是硬规则。

Idempotency-Key：

```text
只写 USER Turn
```

不能写 ASSISTANT Turn。

---

# 十二、Concurrency

必须依赖数据库：

```text
UNIQUE(
    conversation_id,
    idempotency_key
)
```

作为最终一致性保证。

不能仅：

```text
SELECT → if none → INSERT
```

然后认为并发安全。

两个并发请求：

```text
Request A
Request B
```

同时看到：

```text
no USER Turn
```

也只能最终：

```text
one USER Turn
```

另一个必须捕获：

```text
UNIQUE violation
```

---

# 十三、Unique Violation

当前 Step 4A 已经确认：

```text
build_turn_insert
```

没有：

```text
ON CONFLICT
```

且当前 API 只把：

```text
ConversationRepositoryError
```

映射为：

```text
500
```

本阶段必须修正。

唯一约束冲突不得返回 500。

根据真实项目异常体系，建立最小映射：

```text
UNIQUE(conversation_id,idempotency_key)
violation
        ↓
idempotency conflict
        ↓
409
```

但不要把所有数据库 IntegrityError 都粗暴映射成 409。

只识别：

```text
conversation_turn
+
idempotency_key unique constraint
```

对应的冲突。

---

# 十四、409 场景

至少支持：

### 1. Same key / different payload

```text
409
IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD
```

---

### 2. In-flight / unresolved concurrency

OD-33 已 OPEN。

本阶段允许采用：

```text
retryable
```

策略。

如果当前状态无法可靠区分：

```text
in-flight
vs
failed
vs
crashed
```

不要新增 status 字段。

按照 OD-33 当前决策：

```text
default retry
```

但必须保证：

```text
不会产生第二条 USER Turn
```

---

# 十五、Duplicate 查找顺序

实现时必须避免：

```text
先创建 USER
再发现 duplicate
```

推荐：

```text
load conversation
 ↓
if idempotency_key:
    find existing USER Turn by:
        conversation_id
        idempotency_key
 ↓
if existing:
    compare content
    if mismatch:
        409
    if ASSISTANT exists:
        replay
    else:
        retry existing USER Turn
else:
    create USER Turn
```

注意：

这个查询必须保持 concurrency-safe。

最终依然依赖：

```text
UNIQUE
```

而不是应用层查询作为唯一保证。

---

# 十六、Completed Duplicate 查询

判断：

```text
USER Turn
+
ASSISTANT Turn
```

必须使用：

```text
conversation_id
+
existing USER Turn.id
```

寻找对应 Assistant Turn。

不要通过：

```text
assistant_request_id
```

反向推断 Conversation。

因为：

```text
assistant_request_id
```

仍然只是 correlation ID。

---

# 十七、Response DTO 修改

只允许：

```text
route: str
```

变成：

```text
route: str | None = None
```

不要增加：

```text
idempotency_replayed
```

这种新的顶层 DTO 字段。

Replay 标记只能放：

```text
metadata["idempotent_replay"] = true
```

保持 Step 5A 冻结契约。

---

# 十八、DB Schema

新增：

```text
conversation_turn.idempotency_key
```

类型：

```text
VARCHAR(128) NULL
```

新增：

```text
UNIQUE(conversation_id, idempotency_key)
```

历史：

```text
NULL
```

必须保持兼容。

禁止：

```text
NOT NULL
backfill
NULLS NOT DISTINCT
```

---

# 十九、init_db

当前：

```text
Base.metadata.create_all(checkfirst=True)
```

无法保证：

```text
已有 conversation_turn
```

自动增加新 column / constraint。

因此必须沿用项目现有：

```text
ensure_assistant_request_id_column
```

之类的幂等 DDL 机制。

新增：

```text
ensure_conversation_turn_idempotency_key_column
```

以及：

```text
ensure_conversation_turn_idempotency_unique_constraint
```

或者根据真实项目已有 DDL helper 风格实现。

要求：

```text
第一次启动 → 创建
第二次启动 → no-op
```

不得破坏已有数据。

---

# 二十、不要引入 Migration Framework

本阶段：

```text
NO Alembic
NO new migration framework
```

沿用项目当前：

```text
init_db
idempotent DDL
```

策略。

---

# 二十一、测试必须同步更新

Step 4A 已经发现：

```text
test_conversation_persistence_db.py
```

中存在硬编码 schema/index 断言。

必须更新：

```text
T-1
columns: 6 → 7

T-2
indexes: 1 → 2
```

但不要简单删除断言。

应该明确验证：

```text
idempotency_key
UNIQUE(conversation_id,idempotency_key)
```

---

# 二十二、必须新增测试

至少覆盖：

## API

```text
no header
valid header
blank header
>128 header
```

---

## First Request

```text
first request + key
USER Turn created
ASSISTANT Turn created
```

---

## Duplicate

```text
same key
same content
→ 200
→ AI call = 0
→ USER count unchanged
→ ASSISTANT count unchanged
→ metadata.idempotent_replay = true
→ route = null
→ data = null
```

---

## Conflict

```text
same key
different content
→ 409
→ AI call = 0
→ no second USER
```

---

## Retry

```text
USER exists
ASSISTANT missing
same key
→ AI executes
→ no second USER
→ existing USER Turn reused
```

至少覆盖：

```text
EMPTY
FAILED
TX2 failure
```

---

# 二十三、Context Retry Test

这是强制测试。

构造：

```text
USER Turn(id=U1)
ASSISTANT Turn absent
```

retry：

```text
same idempotency key
```

验证：

```text
ContextBuilder
```

不会把：

```text
U1
```

重复加入当前请求上下文。

最终：

```text
previous history
+
current message U1
```

只能出现一次。

---

# 二十四、Concurrency Test

至少一个 DB-gated test：

```text
two concurrent requests
same conversation
same idempotency key
same content
```

最终：

```text
USER Turn count = 1
```

并且：

```text
ASSISTANT Turn count ≤ 1
```

注意：

由于 Step 4A 已知：

```text
Crash B
```

存在 AI side-effect ambiguity。

不要声称本测试解决了：

```text
AI exactly-once execution
```

只能证明：

```text
DB idempotency identity
+
USER Turn persistence
```

不会产生 duplicate USER Turn。

---

# 二十五、No-Key Regression

必须确认：

```text
没有 Idempotency-Key
```

时：

```text
历史 API 行为保持一致
```

至少：

```text
two same content requests
```

仍然可以创建：

```text
two independent USER Turns
```

---

# 二十六、Cross Conversation

```text
Conversation A
key = abc

Conversation B
key = abc
```

必须：

```text
independent
```

数据库 constraint 必须是：

```text
UNIQUE(conversation_id,idempotency_key)
```

而不是：

```text
UNIQUE(idempotency_key)
```

---

# 二十七、ASSISTANT key

必须测试：

```text
ASSISTANT Turn.idempotency_key is NULL
```

即使 USER Turn：

```text
idempotency_key = abc
```

ASSISTANT 也不能复制：

```text
abc
```

---

# 二十八、Security

禁止 response 暴露：

```text
database URL
password
API key
connection string
DB session
SQLAlchemy connection
```

Replay metadata 只能包含：

```text
request_id
idempotent_replay
```

---

# 二十九、Evidence

本阶段完全不处理：

```text
Evidence
ConversationEvidence
Runtime Evidence
dataset_version
```

重复请求：

```text
不创建 Evidence
```

保持：

```text
Evidence = Source / Provenance Unit
```

Runtime Evidence：

```text
DEFERRED
```

---

# 三十、测试顺序

先执行针对性测试：

```powershell
python -m pytest -q tests/test_conversation_persistence_db.py
```

然后：

```powershell
python -m pytest -q tests/
```

DB 测试：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

如果项目当前有独立 DB test marker / fixture：

优先遵循现有机制。

PowerShell 不要使用：

```text
RUN_DB_TESTS=1 pytest
```

---

# 三十一、Compile / Lint

执行：

```powershell
python -m compileall backend tests
```

如果项目已有 lint：

继续使用项目现有 lint 命令。

不要为了本阶段新增 lint framework。

---

# 三十二、Migration / DB Residue

必须确认：

```text
production DB writes = 0
```

测试 DB：

允许 schema change：

```text
conversation_turn.idempotency_key
UNIQUE constraint
```

但不得：

```text
删除历史 Conversation
删除历史 Turn
修改历史 content
```

必须验证：

```text
历史 NULL idempotency_key rows remain valid
```

---

# 三十三、Git Diff 审计

完成后：

```powershell
git diff --stat
git diff -- backend
git diff -- tests
git status
```

确认没有：

```text
Prompt changes
Router changes
Orchestrator redesign
RAG changes
Tool changes
Text-to-SQL changes
Evidence changes
ConversationEvidence changes
```

如果出现范围外修改：

立即停止并报告。

---

# 三十四、失败处理

如果测试发现：

```text
existing architecture bug
```

不要为了测试通过而扩大 Step 6。

尤其禁止：

```text
新增 execution status
新增 MessageRequest table
新增 AI Result persistence
修改 Evidence
修改 Orchestrator
```

如果发现真正需要超出 Step 6 范围的设计变化：

立即停止并报告：

```text
Problem
Impact
Root Cause
Required Architecture Decision
```

---

# 三十五、Step 6 完成条件

必须全部满足：

```text
[ ] Idempotency-Key Header
[ ] 128 length validation
[ ] conversation_turn.idempotency_key
[ ] UNIQUE(conversation_id,idempotency_key)
[ ] init_db idempotent DDL
[ ] same key + same payload duplicate
[ ] same key + different payload 409
[ ] retry reuses USER Turn
[ ] no duplicate USER Turn
[ ] ASSISTANT key NULL
[ ] duplicate route = null
[ ] duplicate data = null
[ ] duplicate metadata.idempotent_replay = true
[ ] duplicate AI calls = 0
[ ] retry context does not duplicate USER Turn
[ ] concurrency DB protection
[ ] no-key regression
[ ] cross-conversation isolation
[ ] historical NULL compatibility
[ ] Evidence unchanged
[ ] compileall
[ ] full regression
```

---

# 三十六、最终报告

完成后严格输出：

```text
【Phase 4.2 Step 6 COMPLETE】

1. Implementation
2. Idempotency-Key
3. DB Schema
4. Transaction Boundary
5. Duplicate Replay
6. Retry
7. Conflict / 409
8. Concurrency
9. Context Retry
10. API Contract
11. Tests
12. DB Tests
13. compile/lint
14. DB residue
15. Security
16. Evidence Boundary
17. KL-1 / KL-2
18. Open Decisions
19. 修改文件
20. 新增文件
21. Git Diff
22. 当前限制
```

必须明确：

```text
AI exactly-once execution
```

如果仍受 KL-1 限制，明确写：

```text
NOT GUARANTEED
```

不要声称已经解决。

---

# 三十七、Step 6 STOP

完成后：

```text
STOP
```

不要进入：

```text
Phase 4.2 Step 7
```

不要开始：

```text
Multi-turn Context
```

不要开始：

```text
Runtime E2E
```

不要开始：

```text
Evidence Runtime
```

不要进入下一阶段。

**只完成 Step 6 并汇报。**
