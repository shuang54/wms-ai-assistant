你现在开始执行：

# Phase 4.2 Step 4A — OD-26 + Idempotency Implementation Authorization

项目：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

本阶段**只做架构决策与实施前置审计**。

不要实现 Idempotency。

不要修改 Backend。

不要修改 API。

不要修改 DB。

不要执行 Migration。

不要修改 Evidence。

不要进入 Step 6 实现。

唯一目标：

> 关闭或明确 OD-26，并确认 Phase 4.2 Step 6 开始实施前所需的 Idempotency 契约、数据库变更、API Header、并发语义是否已经完整冻结。

---

# 二、先阅读

必须阅读：

```text
docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md

docs/evaluation/Phase 4.2 Step 1 — Message Idempotency Contract.md

docs/evaluation/Phase 4.2 Step 1A — OD-22 Idempotency Architecture Decision.md

docs/evaluation/Phase 4.2 Step 2 — AI Result Assistant Turn Semantics.md

docs/evaluation/Phase 4.2 Step 3B — Scope Reconciliation.md
```

然后阅读真实实现：

```text
backend/app/services/chat_application_service.py
backend/app/api/
backend/app/db/models/
backend/app/repositories/
tests/
```

重点确认当前：

```text
POST /api/conversations/{conversation_id}/messages
```

真实请求模型、Route、Service、ConversationTurn ORM、Repository、事务边界和测试基础设施。

---

# 三、冻结当前已经确定的契约

不得重新讨论以下内容：

## Idempotency Architecture

```text
Architecture = A

Persistence:
conversation_turn.idempotency_key

Scope:
conversation

Constraint:
UNIQUE(conversation_id, idempotency_key)

API:
Idempotency-Key HTTP Header

Fingerprint:
SHA-256(conversation_id + content)

Evidence:
不包含 idempotency_key
```

---

# 四、OD-26：EMPTY Completion Semantics

分析当前：

```text
AIOrchestrationResult.content
AIOrchestrationResult.data
AIOrchestrationResult.metadata
AIOrchestrationResult.outcome
```

以及：

```text
ChatApplicationService
```

确定：

```text
EMPTY
```

的正式语义。

至少比较以下两个方案：

### Option A

```text
EMPTY = completed
```

含义：

AI 已完成请求，只是没有可展示的 assistant content。

同一个 Idempotency-Key 再次请求：

```text
duplicate
```

不重新执行 AI。

---

### Option B

```text
EMPTY = not completed
```

含义：

没有形成 Assistant Turn，因此请求仍然可以 retry。

同一个 Idempotency-Key：

```text
允许重新执行 AI
```

---

# 五、必须从业务语义而不是实现方便性判断

重点分析：

```text
Assistant Turn
```

当前定义是：

> 用户可见的 assistant message。

因此判断：

```text
AI execution completed
```

是否必须等价于：

```text
Assistant Turn persisted
```

不要因为当前数据库没有 execution status 就直接得出结论。

---

# 六、必须分析这些场景

建立明确矩阵：

| Scenario                       | USER Turn |          AI | Assistant Turn | Retry |
| ------------------------------ | --------: | ----------: | -------------: | ----: |
| SUCCESS + content              |         ✅ |           ✅ |              ✅ |     ❌ |
| SUCCESS + empty                |         ✅ |           ✅ |              ❌ |     ? |
| REFUSED + content              |         ✅ |           ✅ |              ✅ |     ❌ |
| REFUSED + empty                |         ✅ |           ✅ |              ❌ |     ? |
| FAILED exception               |         ✅ | ❌/exception |              ❌ |     ✅ |
| USER persisted + process crash |         ✅ |     unknown |              ❌ |     ? |
| AI completed + TX2 failed      |         ✅ |           ✅ |              ❌ |     ? |

重点说明：

```text
Retry
```

的判断依据。

---

# 七、特别分析 Crash B

Step 1A 已经确定存在：

```text
AI executed
    ↓
process crash
    ↓
ASSISTANT Turn 未提交
```

此时：

```text
USER Turn = exists
ASSISTANT Turn = absent
```

但：

```text
AI may already have executed
```

因此如果 retry：

```text
AI may execute twice
```

这是当前架构的已知 ambiguity。

必须明确：

1. OD-26 是否能够解决？
2. 如果不能解决，属于哪个层次的问题？
3. 是否需要 Step 6 处理？
4. 是否属于已知 limitation？

不要通过伪造 execution status 来解决。

---

# 八、确认 Step 6 的事务目标

Step 6 不再处理 Evidence。

只处理：

```text
USER Turn
    ↓
AI execution
    ↓
ASSISTANT Turn
```

以及：

```text
Idempotency
Concurrency
Retry
Transaction
```

确认以下事务模型是否仍然成立：

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

不要在本阶段修改这个模型。

---

# 九、确认 Idempotency DB 变更

确认 Step 6 实施前需要：

```text
conversation_turn.idempotency_key VARCHAR(...)
```

以及：

```text
UNIQUE(conversation_id, idempotency_key)
```

确认：

### Historical rows

已有：

```text
idempotency_key = NULL
```

必须保持兼容。

确认 PostgreSQL 对：

```text
UNIQUE(conversation_id, idempotency_key)
```

与 NULL 的行为是否符合当前设计。

不要执行 migration。

---

# 十、确认 API Contract

确认：

```http
Idempotency-Key: <client-key>
```

是：

```text
Optional Header
```

而不是：

```text
JSON body
```

Body：

```json
{
  "content": "..."
}
```

不增加：

```text
idempotency_key
```

字段。

确认没有必要新增：

```text
MessageRequest
```

独立表。

---

# 十一、冲突语义必须冻结

确认：

### Same conversation + same key + same content

```text
duplicate
```

### Same conversation + same key + different content

```text
IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD
```

### Different conversation + same key

```text
independent
```

### Same content + different key

```text
independent
```

### In-flight request

沿用 Step 1A：

```text
409 Conflict
```

但必须说明如何在当前 DB 模型中可靠识别：

```text
in-flight
```

如果当前模型无法可靠区分：

```text
in-flight
vs
failed/not-completed
```

必须明确记录为 Step 6 implementation concern。

不要偷偷增加状态字段。

---

# 十二、必须确认 Evidence 完全不参与

本阶段明确：

```text
Idempotency-Key
conversation_id
turn_id
assistant_request_id
```

全部不得进入：

```text
Evidence identity
Evidence provenance
Evidence dataset_version
```

Evidence 当前仍然：

```text
Source / Provenance Unit
```

Runtime Evidence：

```text
DEFERRED
```

OD-31 / OD-32：

```text
OPEN
```

不得关闭。

---

# 十三、最终判断

必须明确给出：

```text
OD-26 = CLOSED
```

或者：

```text
OD-26 = OPEN
```

如果可以关闭：

必须给出最终规范：

```text
EMPTY semantics:
...

Idempotency behavior:
...

Retry behavior:
...

Crash behavior:
...
```

如果无法安全关闭：

必须说明：

```text
为什么不能关闭
缺什么事实
应该由哪个 Step 解决
```

不要为了推进项目强行关闭。

---

# 十四、Step 6 Readiness

最终判断：

```text
Step 6 = READY
```

或者：

```text
Step 6 = BLOCKED
```

必须列出所有 blocker。

特别检查：

```text
OD-26
OD-22
Idempotency DB migration authorization
API Header contract
Transaction semantics
Concurrency semantics
```

---

# 十五、允许修改文件

只允许新增：

```text
docs/evaluation/Phase 4.2 Step 4A — OD-26 Decision.md
```

如果确实需要：

```text
docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md
```

只能采用追加方式。

不得删除历史内容。

---

# 十六、禁止

本阶段禁止：

```text
❌ backend 修改
❌ tests 修改
❌ DB migration
❌ DB schema 修改
❌ API 修改
❌ ChatApplicationService 修改
❌ ConversationTurn ORM 修改
❌ Repository 修改
❌ Evidence 修改
❌ Runtime Evidence
❌ ConversationEvidence Runtime
❌ Step 6 implementation
```

---

# 十七、验证

只做静态审计：

```text
git diff -- backend tests
```

必须：

```text
0
```

检查：

```text
git status
```

确认没有意外代码修改。

---

# 十八、完成报告

严格输出：

```text
【Phase 4.2 Step 4A COMPLETE】

1. OD-26
2. EMPTY semantics
3. Idempotency behavior
4. Retry semantics
5. Crash semantics
6. Concurrency semantics
7. API contract
8. DB contract
9. Evidence boundary
10. Step 6 Readiness
11. Open Decisions
12. 新增文件
13. Backend
14. Tests
15. DB
16. API
```

最后明确：

```text
Next Step:
Step 6 — Transaction / Concurrency
```

或者：

```text
Step 6 BLOCKED
```

**完成后立即停止。**

不要实施 Step 6。
不要实施 Idempotency。
不要修改数据库。
不要进入 Step 7。
