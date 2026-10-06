你现在开始执行：

# Phase 4.2 Step 1 — Message Idempotency Contract

项目：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

本 Step **只解决 OD-14：消息幂等契约**。

目标不是马上实现完整幂等机制，而是：

> 基于当前真实 Conversation Runtime，定义“同一条用户消息重复提交时，系统必须如何行为”。

必须明确：

```text
什么是同一条消息？
谁生成幂等键？
幂等键保存在哪里？
重复请求如何识别？
重复请求返回什么？
并发重复请求如何处理？
AI 失败后能否重试？
不同 Conversation 是否隔离？
是否需要 DB Schema Change？
```

本阶段优先：

**阅读 → 分析 → 契约设计 → 测试设计 → 决策**

如果现有架构已经能够安全实现，则可以提出最小实现方案；但**除非下面明确要求，不要直接修改生产代码。**

---

# 二、严格禁止

本 Step 禁止：

```text
❌ Evidence Builder
❌ Evidence Runtime
❌ ConversationEvidence Runtime Association
❌ AI Result 持久化实现
❌ 修改 AIOrchestrationResult
❌ 修改 Evidence identity
❌ 修改 Evidence provenance
❌ 修改 ConversationEvidence
❌ 修改 Router
❌ 修改 Orchestrator
❌ 修改 RAG
❌ 修改 Tool
❌ 修改 Text-to-SQL
❌ 修改 SQL Validator
❌ 修改 SQL Executor
❌ 新增 HTTP API
❌ 新增 Conversation List API
❌ Agent
❌ MCP
❌ Memory
❌ Workflow
❌ Planning
```

除非发现**为了完成 OD-14 必须进行的最小代码修改**，否则生产代码保持不变。

---

# 三、Step 1 必须先阅读真实代码

不要假设接口。

重点阅读：

```text
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/services/conversation_context_builder.py
backend/app/db/models/conversation.py
backend/app/db/models/conversation_turn.py
backend/app/db/conversation_repository.py
backend/app/api/
```

然后继续检查：

```text
tests/
```

重点搜索：

```text
execute_message
assistant_request_id
request_id
ConversationTurn
conversation_turn
message
idempot
duplicate
concurrent
```

同时阅读：

```text
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md
docs/evaluation/Phase 4.2 Step 0 — Roadmap Freeze Audit.md
```

---

# 四、首先还原当前真实消息流程

必须画出当前实际代码路径：

```text
HTTP / Application Entry
        ↓
ChatApplicationService.execute_message()
        ↓
Conversation Read
        ↓
USER Turn INSERT
        ↓
History Read
        ↓
ConversationContextBuilder
        ↓
AIOrchestrator
        ↓
AIOrchestrationResult
        ↓
ASSISTANT Turn INSERT
        ↓
Return Result
```

明确标记：

```text
CURRENT
MISSING
UNDEFINED
```

特别确认：

### User Turn

当前 USER Turn 是否有：

```text
turn_id
conversation_id
role
content
assistant_request_id
created_at
```

以及：

**是否存在业务级 message/request idempotency key。**

---

# 五、重点分析 assistant_request_id

这是本 Step 最重要的问题之一。

当前 Phase 4.1 已冻结：

```text
assistant_request_id = CORRELATION ONLY
```

因此：

**不得直接把 assistant_request_id 改造成 Evidence identity 或业务幂等键。**

但是必须分析：

```text
assistant_request_id
```

能不能：

1. 作为消息请求的唯一 correlation ID？
2. 作为客户端幂等 key？
3. 作为服务端生成的 request ID？
4. 是否每次重复 HTTP 请求都会生成新的 assistant_request_id？
5. 是否在 USER Turn 创建前已经存在？
6. 是否能够识别重复 USER message？
7. 是否适合作为 `(conversation_id, assistant_request_id)` 唯一键？

不要预设答案。

必须以真实代码为准。

---

# 六、定义“同一条消息”

必须明确至少三种情况。

## Case A：完全相同的 HTTP 请求

例如：

```text
conversation_id = C1
message = "查询库存"
idempotency_key = K1
```

第一次：

```text
K1 → execute
```

第二次：

```text
K1 → duplicate
```

必须定义第二次是否：

```text
重新执行 AI
```

还是：

```text
直接返回第一次结果
```

以及是否：

```text
禁止创建第二个 USER Turn
禁止创建第二个 ASSISTANT Turn
禁止创建第二份 Evidence
```

---

## Case B：内容相同，但请求不同

例如：

```text
K1 + "查询库存"
K2 + "查询库存"
```

这是：

```text
两个不同请求
```

除非当前架构另有正式契约。

不能因为：

```text
content 相同
```

就自动认为是 duplicate。

---

## Case C：同一个 key，但 content 不同

例如：

```text
K1 + "查询库存"
```

第一次已经提交。

随后：

```text
K1 + "查询销售订单"
```

必须定义：

```text
IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD
```

还是其他明确错误。

不能静默执行第二条消息。

---

# 七、幂等键候选方案

必须至少分析以下方案。

## Option A

客户端提供：

```text
idempotency_key
```

例如：

```text
UUID
```

服务端使用：

```text
conversation_id + idempotency_key
```

作为业务唯一边界。

---

## Option B

使用：

```text
assistant_request_id
```

作为幂等键。

重点分析：

```text
它现在是谁生成？
什么时候生成？
是否由客户端传入？
是否每次请求重新生成？
是否已经被 Phase 4.1 定义为 correlation-only？
```

---

## Option C

使用：

```text
turn_id
```

不推荐默认采用，但必须解释为什么。

重点：

```text
turn_id 通常是数据库持久化后的身份，
不是客户端请求幂等键。
```

---

## Option D

根据：

```text
conversation_id + role + content + timestamp
```

计算 hash。

重点分析为什么这种方案存在：

```text
误判
重复消息无法区分
时间窗口问题
```

不要默认使用。

---

# 八、必须给出明确 Recommendation

完成分析后必须形成：

```text
OD-14 Recommendation
```

包含：

```text
Idempotency Key:
Scope:
Owner:
Persistence:
Duplicate Behavior:
Conflict Behavior:
Concurrency Behavior:
Failure Retry Behavior:
```

例如：

```text
Idempotency Key = Client-provided request key
Scope = conversation
Owner = client request
Persistence = User Turn
Duplicate = return existing execution result
Conflict = reject
```

但：

**不要直接采用这个例子。**

必须根据真实代码分析。

---

# 九、并发问题

必须重点设计：

```text
Request A
Request B
```

同时使用：

```text
conversation_id = C1
idempotency_key = K1
```

可能发生：

```text
A → check duplicate → none
B → check duplicate → none
A → execute
B → execute
```

这会导致：

```text
2 USER turns
2 AI executions
2 ASSISTANT turns
未来可能产生 2 Evidence
```

必须分析如何防止。

候选机制：

```text
DB UNIQUE
application lock
transaction isolation
SELECT FOR UPDATE
atomic insert
```

但：

**本 Step 不要马上实现。**

只需要确定：

> 哪一层必须最终保证幂等。

---

# 十、必须分析 DB Schema Change

当前 Roadmap：

```text
DB CHANGE = NO
```

本 Step 不允许为了方便直接加字段。

必须检查：

当前：

```text
conversation_turn
```

是否已经存在可以承担幂等职责的字段。

如果没有：

必须明确：

```text
Current Schema Insufficient
```

然后回答：

```text
是否必须增加字段？
增加什么字段？
为什么现有字段不能承担？
是否可以通过独立 persistence mechanism？
```

如果确实需要 schema change：

**不要实施。**

只输出：

```text
OD-14 requires future DB change
```

并说明最小候选设计。

---

# 十一、重复请求返回语义

必须定义：

### Scenario 1

第一次执行成功：

```text
K1 → SUCCESS
```

第二次：

```text
K1
```

应该：

```text
return previous result
```

还是：

```text
409 duplicate
```

必须选择一个契约。

---

### Scenario 2

第一次 AI 执行失败：

```text
K1 → AI FAILURE
```

第二次：

```text
K1
```

是否允许：

```text
retry
```

必须明确。

否则很容易出现：

```text
第一次失败
→ User Turn 已保存
→ 第二次被当成 duplicate
→ 永远无法重试
```

---

### Scenario 3

第一次：

```text
USER Turn 成功
AI 执行中断
ASSISTANT Turn 未创建
```

第二次同 key：

必须定义是否允许恢复。

---

# 十二、Conversation Isolation

必须验证：

```text
C1 + K1
C2 + K1
```

是否允许。

推荐分析：

```text
同一个 key 在不同 Conversation 是否属于不同请求？
```

必须明确。

不要使用：

```text
global idempotency key
```

除非有明确理由。

---

# 十三、状态机

建议最终明确消息请求生命周期，例如：

```text
RECEIVED
   ↓
PROCESSING
   ↓
SUCCEEDED
```

失败：

```text
PROCESSING
   ↓
FAILED
```

但是：

**不要直接新增数据库状态字段。**

这里只分析：

> 是否需要显式请求状态。

如果当前系统没有 request state persistence：

明确记录：

```text
state persistence = UNDEFINED
```

不要自行扩展。

---

# 十四、Evidence 边界

必须确认：

即使未来：

```text
Message Idempotency
        ↓
AI Result
        ↓
Evidence
```

也不能把：

```text
conversation_id
turn_id
assistant_request_id
```

写进：

```text
Evidence identity
Evidence provenance
```

必须保持：

```text
Evidence
├── evidence_id
├── dataset_version
├── source_type
└── ...
```

与：

```text
ConversationEvidence
```

独立关联。

---

# 十五、测试设计

本 Step 需要设计测试，但默认不要大量实现。

至少定义以下测试矩阵：

### T1

```text
same conversation
same idempotency key
same payload
→ duplicate
```

### T2

```text
same conversation
different idempotency key
same content
→ independent requests
```

### T3

```text
same conversation
same key
different payload
→ conflict
```

### T4

```text
different conversation
same key
same payload
→ isolated
```

### T5

```text
first request succeeds
second duplicate
→ no second AI execution
```

### T6

```text
first request fails
second same key
→ according to retry contract
```

### T7

```text
concurrent same-key requests
→ exactly one logical execution
```

### T8

```text
assistant_request_id changes
```

验证：

```text
assistant_request_id
≠
idempotency key
```

如果最终契约确实这样定义。

---

# 十六、必须检查现有测试基础设施

寻找并复用：

```text
FakeOrchestrator
Fake LLM
Conversation fixtures
DB fixtures
Transaction fixtures
```

不要重新创建一套测试基础设施。

---

# 十七、最终输出

新增：

```text
docs/evaluation/Phase 4.2 Step 1 — Message Idempotency Contract.md
```

只允许新增这一个分析文档。

默认：

```text
backend = 0
tests = 0
DB schema = 0
API = 0
```

如果发现确实必须修改代码才能证明契约：

**停止并报告，不要自行进入实现。**

---

# 十八、文档必须包含

```text
1. Current Message Flow
2. Current ConversationTurn Contract
3. assistant_request_id Analysis
4. Definition of Same Message
5. Idempotency Key Options
6. Recommended Contract
7. Duplicate Behavior
8. Conflict Behavior
9. Failure / Retry Behavior
10. Concurrent Request Behavior
11. Conversation Isolation
12. DB Schema Assessment
13. Evidence Boundary
14. Test Matrix
15. Open Questions
16. Decision Status
```

最终明确：

```text
OD-14 = CLOSED
```

只有在：

```text
Key
Scope
Persistence
Duplicate
Conflict
Failure
Concurrency
Isolation
```

全部有明确答案时，才能 CLOSED。

如果任何一项仍然没有足够证据：

```text
OD-14 = OPEN
```

不要为了推进 Roadmap 强行关闭。

---

# 十九、最终报告格式

完成后严格：

```text
【Phase 4.2 Step 1 COMPLETE】

1. Current Message Flow
2. assistant_request_id 结论
3. Idempotency Key 选择
4. Duplicate 行为
5. Conflict 行为
6. Failure / Retry
7. Concurrent Request
8. Conversation Isolation
9. DB Schema
10. Evidence Boundary
11. Test Matrix
12. OD-14 状态
13. 新增文件
14. 修改文件
15. Backend
16. DB
17. API
18. Step 2 Readiness
```

最后：

```text
Phase 4.2 Step 1 = COMPLETE
Step 2 = READY / BLOCKED
STOP
```

不要进入 Step 2。

不要实现 Evidence Builder。

不要修改 ChatApplicationService。
