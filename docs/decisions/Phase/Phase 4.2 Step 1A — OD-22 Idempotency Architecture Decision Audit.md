你现在开始执行：

# Phase 4.2 Step 1A — OD-22 Idempotency Architecture Decision Audit

项目：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

本 Step 只解决：

```text
OD-22：
是否接受为实现 Message Idempotency
新增 API 字段 + DB 持久化 + UNIQUE 约束？
```

Step 1 已经证明：

```text
OD-14 = OPEN
OD-22 = OPEN
```

当前事实：

```text
客户端请求目前只有 content
conversation_turn 没有 idempotency_key
assistant_request_id 在 AI 执行阶段才生成
assistant_request_id = CORRELATION ONLY
```

因此本 Step 必须进一步回答：

> **应该采用什么最小、可长期维护的 Idempotency Architecture？**

---

# 二、严格模式

本 Step：

```text
ONLY ANALYSIS + ARCHITECTURE DECISION
```

禁止：

```text
❌ 修改 backend
❌ 修改 tests
❌ 修改 ORM
❌ 修改数据库
❌ 修改 API
❌ 修改 AIOrchestrationResult
❌ 修改 ChatApplicationService
❌ 修改 ConversationTurn
❌ 修改 Evidence
❌ 修改 ConversationEvidence
❌ Evidence Builder
❌ Step 2
```

不得执行：

```text
CREATE TABLE
ALTER TABLE
CREATE INDEX
INSERT
UPDATE
DELETE
```

不得修改：

```text
docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md
```

不得修改：

```text
Phase 4.1 Roadmap
Phase 4.1 Lifecycle Contract
```

---

# 三、先阅读真实实现

重新检查：

```text
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/services/conversation_context_builder.py
backend/app/db/models/conversation_turn.py
backend/app/db/conversation_repository.py
backend/app/api/
```

以及：

```text
tests/
```

重点寻找：

```text
execute_message
ConversationTurn
assistant_request_id
request_id
transaction
session.begin
commit
rollback
unique
idempot
duplicate
```

同时阅读：

```text
docs/evaluation/Phase 4.2 Step 1 — Message Idempotency Contract.md
```

必须以 Step 1 的真实结论为基础。

---

# 四、先冻结已知事实

不得重新推翻以下事实：

```text
assistant_request_id
=
CORRELATION ONLY
```

并且：

```text
assistant_request_id
≠
idempotency_key
```

原因已经由 Step 1 证明：

```text
assistant_request_id
    ↓
AIOrchestrator.execute()
    ↓
new_request_id()
```

发生在：

```text
USER Turn INSERT
```

之后。

因此它无法承担：

```text
HTTP retry identity
```

---

# 五、比较两个主要架构

必须重点比较：

## Architecture A：ConversationTurn 承担 Idempotency

候选：

```text
conversation_turn
├── turn_id
├── conversation_id
├── role
├── content
├── assistant_request_id
├── idempotency_key
└── created_at
```

唯一约束：

```text
UNIQUE(conversation_id, idempotency_key)
```

但必须进一步分析：

### 问题

如果：

```text
idempotency_key
```

只存在 USER Turn：

那么如何表示：

```text
PROCESSING
SUCCEEDED
FAILED
```

以及：

```text
第一次请求已经执行成功
但 HTTP response 丢失
```

第二次请求如何直接获得：

```text
AIOrchestrationResult
```

而不是再次调用 AI？

必须明确回答。

---

# 六、Architecture B：独立 Message Request / Idempotency Record

候选：

```text
conversation_message_request
```

例如：

```text
request_id
conversation_id
idempotency_key
request_fingerprint
status
response_data / result_reference
created_at
updated_at
```

唯一约束：

```text
UNIQUE(conversation_id, idempotency_key)
```

状态可能是：

```text
PROCESSING
SUCCEEDED
FAILED
```

但：

**不要直接接受这个方案。**

必须分析它是否会：

```text
过度设计
重复 ConversationTurn
引入新的持久化模型
提前解决 Step 2 / Step 6
```

---

# 七、必须比较职责边界

建立表格：

```text
Capability
A: ConversationTurn
B: MessageRequest
```

至少比较：

```text
幂等身份
请求状态
payload fingerprint
并发控制
重复请求识别
成功结果重放
失败重试
HTTP response replay
Conversation isolation
生命周期
数据清理
与 ConversationTurn 的关系
与 AIOrchestrationResult 的关系
与 Evidence 的关系
```

---

# 八、特别分析“结果重放”

这是本 Step 最重要的问题。

当前：

```text
AIOrchestrationResult
```

是：

```text
route
content
data
metadata
```

并且当前 Step 1 已确认：

```text
AIOrchestrationResult 不修改
```

因此必须分析：

第一次：

```text
Request K1
    ↓
AIOrchestrationResult
    ↓
ASSISTANT Turn
    ↓
HTTP Response
```

如果 HTTP response 丢失：

```text
Retry K1
```

服务器应该如何得到第一次结果？

候选：

### A

从：

```text
ASSISTANT ConversationTurn
```

重建响应。

### B

从：

```text
MessageRequest
```

保存的 response snapshot 恢复。

### C

重新执行 AI。

必须比较。

特别注意：

```text
C = 不能作为成功请求的 duplicate replay 策略
```

否则“幂等”只防止了数据库重复，却仍然重复执行 AI。

---

# 九、必须分析失败语义

Step 1 已确定：

```text
成功 → duplicate replay
失败 → 允许 retry
```

因此分析：

```text
FAILED
```

到底由什么表示？

当前：

```text
ConversationTurn
```

没有：

```text
status
error_code
result
```

所以必须回答：

> 如果只增加 `idempotency_key`，是否足够表达失败？

如果不足：

不要马上增加字段。

明确：

```text
Architecture A requires additional state semantics.
```

然后比较 Architecture B。

---

# 十、必须分析 in-flight 并发

场景：

```text
A: K1
B: K1
```

同时进入。

必须避免：

```text
A → AI execute
B → AI execute
```

必须形成明确状态：

```text
K1 = PROCESSING
```

然后 B：

```text
duplicate + processing
```

必须决定：

### Policy 1

B 等待 A 完成。

### Policy 2

B 返回：

```text
409 / 202
```

并要求客户端稍后 retry。

### Policy 3

B 直接读取最终结果。

但只有 A 已经：

```text
SUCCEEDED
```

时才可以。

不要凭感觉选择。

分析当前项目：

```text
FastAPI
同步/异步 Service
SQLAlchemy transaction
AI call latency
```

判断哪种最适合当前阶段。

---

# 十一、必须分析 Request Fingerprint

Step 1 已确定：

```text
same key + different payload
=
reject
```

因此必须回答：

如何判断：

```text
K1 + "查询库存"
```

与：

```text
K1 + "查询销售订单"
```

不同？

候选：

```text
SHA-256(canonical request payload)
```

但不要立即实现。

必须确定：

```text
fingerprint input
```

至少考虑：

```text
conversation_id
content
project_id
```

以及是否应该包含：

```text
idempotency_key
```

答案通常是：

```text
idempotency_key 不应该进入 fingerprint
```

因为 fingerprint 是为了判断：

```text
same key
是否真的代表 same request
```

而不是判断 key 本身。

---

# 十二、必须分析 API 位置

比较：

### Option A

JSON：

```json
{
  "content": "查询库存",
  "idempotency_key": "..."
}
```

### Option B

HTTP Header：

```text
Idempotency-Key: ...
```

必须分析：

```text
FastAPI request model
OpenAPI
client retry
日志
DTO
domain boundary
```

不要只从“写起来方便”选择。

要求最终明确：

```text
API contract
```

是否应该：

```text
Idempotency-Key Header
```

还是：

```text
body.idempotency_key
```

如果选择 Header：

说明为什么它更适合作为：

```text
request metadata
```

而不是业务 message content。

---

# 十三、必须分析 API 兼容性

当前客户端：

```text
POST /api/conversations/{id}/messages
{
  "content": "..."
}
```

如果增加：

```text
Idempotency-Key
```

必须明确：

### 是否 required？

如果 required：

```text
旧客户端
→ 400
```

是否接受？

### 是否 optional？

如果 optional：

```text
没有 key
→ 旧行为？
```

这会不会导致：

```text
同一个 API
部分请求有幂等
部分请求没有幂等
```

最终必须给出明确策略。

---

# 十四、必须分析 DB 设计

至少比较：

## A1

```text
conversation_turn.idempotency_key
```

## A2

```text
conversation_message_request
```

## A3

只使用：

```text
conversation_turn.turn_id
```

明确排除不能满足需求的方案。

尤其回答：

> 为什么 A1 足够 / 不足？

以及：

> 为什么 A2 是必要的 / 过度设计？

不要因为“独立表更专业”就选择 B。

---

# 十五、必须分析与 Step 2 的关系

Step 2 是：

```text
AI Result → Assistant Turn Semantics
```

必须明确：

```text
Step 1A 是否需要先定义 Assistant Turn 如何与 Request 关联？
```

以及：

```text
idempotency_key
```

是否进入：

```text
ConversationTurn
```

如果最终选择独立 Request Record：

则需要明确：

```text
Request
   ↓
User Turn
   ↓
AI Result
   ↓
Assistant Turn
```

之间的关系。

如果选择 ConversationTurn：

则需要明确：

```text
User Turn
   └── idempotency_key
```

如何对应：

```text
Assistant Turn
```

---

# 十六、必须分析与 Step 6 Transaction Boundary 的关系

当前 Runtime：

```text
TX1
USER Turn
commit

AI execution

TX2
ASSISTANT Turn
commit
```

这会造成：

```text
USER Turn exists
AI fails
ASSISTANT Turn absent
```

必须判断：

> Message Idempotency 是否必须改变当前 TX1 / TX2？

不要提前改变。

只记录：

```text
Step 6 decision dependency
```

例如：

```text
Idempotency reservation
    ↓
TX1?
    ↓
AI execution
    ↓
TX2?
```

但本 Step 不实现。

---

# 十七、必须分析 crash window

至少分析：

### Crash A

```text
User Turn commit
↓
process crash
↓
AI never executes
```

### Crash B

```text
AI execution complete
↓
process crash
↓
Assistant Turn not committed
```

### Crash C

```text
Assistant Turn committed
↓
HTTP response lost
```

三个场景必须分别说明：

```text
retry behavior
```

尤其：

```text
Crash B
```

最危险。

因为：

```text
AI 已经执行
但数据库没有记录成功结果
```

如果重新执行：

```text
可能产生第二次 AI side effect
```

当前 AI Runtime 主要是 RAG / Tool / Text-to-SQL，未来 Tool 可能有副作用。

因此必须记录：

```text
ambiguous outcome
```

是否属于本阶段解决范围。

---

# 十八、必须保持 Evidence Boundary

无论最终采用 A 还是 B：

禁止：

```text
Evidence.idempotency_key
Evidence.conversation_id
Evidence.turn_id
Evidence.assistant_request_id
```

也禁止：

```text
Evidence provenance
=
conversation runtime identity
```

必须保持：

```text
Conversation
    ↓
Message Request / Turn
    ↓
AI Result
    ↓
Evidence
    ↓
ConversationEvidence
```

其中：

```text
Evidence
```

仍然是独立 Domain Object。

---

# 十九、最终决策标准

只有满足以下条件，才可以：

```text
OD-22 = CLOSED
```

必须明确：

```text
1. API location
2. Required / Optional
3. Key scope
4. Persistence owner
5. UNIQUE boundary
6. Payload fingerprint
7. Duplicate completed behavior
8. Duplicate processing behavior
9. Failed request behavior
10. Conflict behavior
11. Concurrent behavior
12. Crash behavior
13. Response replay strategy
14. Conversation isolation
15. DB migration necessity
16. Step 2 dependency
17. Step 6 dependency
18. Evidence boundary
```

任何一个仍然：

```text
UNDEFINED
```

则：

```text
OD-22 = OPEN
OD-14 = OPEN
Step 2 = BLOCKED
```

不要为了推进而关闭。

---

# 二十、输出文档

如果完成架构决策，只允许新增：

```text
docs/evaluation/Phase 4.2 Step 1A — OD-22 Idempotency Architecture Decision.md
```

不要修改：

```text
Phase 4.2 Roadmap v1.0
```

不要修改 Step 1 文档。

---

# 二十一、文档结构

必须：

```text
# Phase 4.2 Step 1A — OD-22 Idempotency Architecture Decision

## 1. Decision Scope

## 2. Current Runtime Facts

## 3. Architecture A — ConversationTurn

## 4. Architecture B — MessageRequest

## 5. Architecture Comparison

## 6. API Contract

## 7. Idempotency State Model

## 8. Request Fingerprint

## 9. Duplicate / Conflict / Retry

## 10. Concurrent Requests

## 11. Crash Windows

## 12. DB Design

## 13. Step 2 Dependency

## 14. Step 6 Dependency

## 15. Evidence Boundary

## 16. Decision

## 17. Consequences

## 18. Open Questions
```

---

# 二十二、推荐输出的 Decision 形式

不要写模糊结论：

```text
以后可以考虑……
```

必须明确：

```text
Decision:
Architecture = A / B
API = ...
Persistence = ...
Unique Key = ...
Fingerprint = ...
Completed Duplicate = ...
In-flight Duplicate = ...
Failed Request = ...
Conflict = ...
```

如果不能确定：

```text
Decision = OPEN
```

---

# 二十三、测试设计

本 Step 不要求真正实现测试。

但必须在文档中给出未来测试：

```text
T1 same key + same payload
T2 same key + different payload
T3 different key + same content
T4 same key across conversations
T5 sequential duplicate
T6 concurrent duplicate
T7 first request failure + retry
T8 crash before assistant persistence
T9 response lost + retry
T10 assistant_request_id remains correlation-only
```

---

# 二十四、最终报告

完成后严格输出：

```text
【Phase 4.2 Step 1A COMPLETE】

1. OD-22
2. Architecture A vs B
3. API Contract
4. Persistence
5. Unique Constraint
6. Request Fingerprint
7. Duplicate Behavior
8. In-flight Behavior
9. Failure / Retry
10. Crash Window
11. Step 2 Dependency
12. Step 6 Dependency
13. Evidence Boundary
14. Decision
15. 新增文件
16. 修改文件
17. Backend
18. DB
19. API
20. Step 2 Readiness
```

最后：

```text
Phase 4.2 Step 1A = COMPLETE
OD-22 = CLOSED / OPEN
OD-14 = CLOSED / OPEN
Step 2 = READY / BLOCKED
STOP
```

**不要实现 Step 2。**
**不要修改生产代码。**
**不要修改数据库。**
**不要进入 Evidence Builder。**
