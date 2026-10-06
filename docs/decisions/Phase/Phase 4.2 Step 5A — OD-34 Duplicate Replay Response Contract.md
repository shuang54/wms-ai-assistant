你现在开始执行：

# Phase 4.2 Step 5A — OD-34 Duplicate Replay Response Contract

项目：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

本阶段只解决：

```text
OD-34 = Duplicate Replay Response Contract
```

核心问题：

> 当同一个 `conversation_id + Idempotency-Key` 已经完成，并且已经存在 USER Turn + ASSISTANT Turn 时，第二次请求应该返回什么？

当前已经确定：

```text
completed
=
USER Turn exists
+
ASSISTANT Turn exists
```

duplicate 请求：

```text
不重新执行 AI
不创建第二条 USER Turn
不创建第二条 ASSISTANT Turn
```

但是当前 `ConversationMessageResponse` 的：

```text
route: str
```

为必填字段，而 `conversation_turn` 只保存 assistant `content`，没有保存：

```text
route
data
metadata
AIOrchestrationResult
```

因此必须在本阶段冻结 duplicate response 语义。

---

# 二、严格禁止

本阶段：

```text
❌ 不修改 Backend
❌ 不修改 API
❌ 不修改 DB
❌ 不执行 Migration
❌ 不修改 ConversationTurn ORM
❌ 不修改 ChatApplicationService
❌ 不修改 ConversationMessageResponse
❌ 不实现 Idempotency
❌ 不实现 Step 6
❌ 不实现并发控制
❌ 不实现 Header
❌ 不修改 Evidence
❌ 不修改 ConversationEvidence
```

只做：

```text
阅读
→ 分析
→ 方案比较
→ OD-34 决策
→ 文档冻结
→ STOP
```

---

# 三、先阅读真实实现

必须阅读：

```text
backend/app/api/
backend/app/services/chat_application_service.py
backend/app/db/models/
backend/app/repositories/
tests/
```

重点定位：

```text
POST /api/conversations/{conversation_id}/messages
```

对应：

```text
Request DTO
Response DTO
Route
ChatApplicationService
ConversationRepository
ConversationTurn ORM
```

确认真实：

```text
ConversationMessageResponse
ConversationTurn
ChatApplicationService.execute_message()
```

不要假设接口。

---

# 四、重新确认当前 Response Contract

明确记录当前 response：

```text
ConversationMessageResponse
```

至少确认：

```text
conversation_id
turn_id
role
content
route
data
metadata
```

实际字段以代码为准。

特别确认：

```text
route 是否 required
data 是否 optional
metadata 是否 optional
```

如果实际结构不同，以真实代码为准。

---

# 五、确认当前 ASSISTANT Turn 持久化边界

确认当前 ASSISTANT Turn 实际持久化内容：

```text
role = ASSISTANT
content
assistant_request_id
created_at
...
```

确认是否保存：

```text
route
data
metadata
outcome
```

如果没有：

**不要为了 duplicate response 而增加这些字段。**

---

# 六、分析 Duplicate Response 的三个候选方案

必须正式比较以下方案。

---

## Option A：Duplicate 返回“Message Replay”

语义：

```text
duplicate request
        ↓
找到已有 USER Turn
        ↓
找到已有 ASSISTANT Turn
        ↓
直接返回已持久化 Assistant Message
```

此时：

```text
content = persisted assistant content
```

但：

```text
route = ?
data = ?
metadata = ?
```

必须明确哪些字段可以：

```text
null
```

哪些字段必须：

```text
重新定义为 optional
```

如果当前 response contract 不允许，则记录为 API Contract Change。

---

# 七、Option B：Duplicate 返回独立 Response Shape

例如：

```text
Normal Message Response
```

与：

```text
Duplicate Replay Response
```

使用不同 DTO。

分析：

```text
POST
  ↓
第一次请求
  → Normal Response

POST + same Idempotency-Key
  → Replay Response
```

重点分析：

* 是否破坏现有 API contract
* HTTP status 是否需要变化
* 前端是否需要额外判断
* OpenAPI 是否出现两个 response schema
* 是否值得引入新的 DTO

不要实现，只分析。

---

# 八、Option C：持久化 AI Result Metadata

分析是否应该给 ConversationTurn 增加：

```text
route
data
metadata
```

或者：

```text
assistant_result
```

等字段，使 duplicate 可以完整重建：

```text
AIOrchestrationResult
```

必须明确指出：

这会不会导致：

```text
ConversationTurn
```

从：

```text
User-visible Message
```

变成：

```text
AI Execution Result Store
```

如果会，必须指出这是架构边界变化。

不得因为 duplicate response 方便而直接选择。

---

# 九、必须分析“Route 必填”的真实原因

确认：

```text
ConversationMessageResponse.route
```

为什么目前是：

```text
required str
```

检查：

```text
API client
tests
OpenAPI
frontend
```

是否存在依赖：

```text
route is always non-null
```

特别检查：

```text
EMPTY
```

当前已经可以：

```text
HTTP 200
无 ASSISTANT Turn
```

因此说明：

```text
EMPTY
```

本身可能不会经过正常 MessageResponse。

但 duplicate 只发生在：

```text
ASSISTANT Turn exists
```

所以分析：

> duplicate 是否真的需要完整的 AI route？

不要预设答案。

---

# 十、核心原则

本阶段必须冻结：

> Idempotency 的 duplicate replay 是“消息重放”，还是“AI Result 重放”。

优先区分：

```text
Message Replay
```

和：

```text
Execution Result Replay
```

不要混淆。

当前 Phase 4.2 已冻结：

```text
ConversationTurn = user-visible conversation message
AIOrchestrationResult = runtime execution result
```

除非出现新的架构决策，否则：

```text
不要把 AIOrchestrationResult 全量塞入 ConversationTurn。
```

---

# 十一、HTTP Status

分析 duplicate 应该：

```text
200 OK
```

还是：

```text
409 Conflict
```

还是：

```text
其他
```

必须从 Idempotency 语义判断。

当前 Step 1A 已确定：

```text
same key + same payload
→ duplicate
→ 不重新执行
```

因此重点分析：

> duplicate 是成功重放，还是冲突？

不要因为“幂等”三个字直接猜测。

---

# 十二、Response Metadata

分析 duplicate 是否需要：

```text
metadata.idempotent_replay = true
```

或者：

```text
metadata.replayed = true
```

或者完全不暴露 replay 状态。

必须考虑：

```text
API contract
可观测性
前端行为
安全
```

如果需要新增 metadata：

只记录为设计决策。

不要修改代码。

---

# 十三、Route 的处理必须禁止伪造

明确禁止：

```text
route = "replay"
route = "unknown"
route = "conversation"
route = "duplicate"
route = "chat"
```

等人为字符串。

原因：

`route` 表示真实 AI 路由结果。

不能为了满足：

```text
route: str
```

而伪造。

如果当前 response schema 无法表达真实 duplicate：

必须进行 API contract 微调。

---

# 十四、Data 的处理

分析 duplicate 时：

第一次 AI Result：

```text
content
data
route
metadata
```

但 Assistant Turn 只有：

```text
content
```

因此：

```text
data
```

是否需要 replay？

特别关注：

```text
Text-to-SQL
```

例如第一次：

```text
content = "查询结果如下"
data = [...]
route = "text_to_sql"
```

duplicate 如果只有：

```text
content
```

那么数据结果是否丢失？

必须明确。

不要假设：

```text
data
```

一定不重要。

---

# 十五、根据真实 API 使用方式判断

检查当前 API 是否已经存在：

```text
Conversation Message
```

的调用方。

例如：

```text
frontend
tests
docs
```

分析：

```text
data
route
metadata
```

是否被客户端依赖。

如果找不到 frontend：

明确记录：

```text
当前仓库未发现可验证的前端消费方。
```

不要猜。

---

# 十六、推荐决策原则

如果真实代码验证后发现：

```text
ConversationMessageResponse
```

本质上是：

```text
AI Runtime Result DTO
```

则重新记录风险。

如果真实代码验证后发现：

```text
ConversationMessageResponse
```

本质上是：

```text
Conversation Message DTO
```

那么优先考虑：

```text
Duplicate = Message Replay
```

而不是 Result Replay。

最终必须根据代码事实决定。

---

# 十七、必须形成决策表

最终文档必须有：

| 方案                  | Response 完整性 | DB 变更 | API 变更 | 架构影响 | 推荐/不推荐 |
| ------------------- | ------------ | ----- | ------ | ---- | ------ |
| A Message Replay    | ...          | ...   | ...    | ...  | ...    |
| B Separate DTO      | ...          | ...   | ...    | ...  | ...    |
| C Persist AI Result | ...          | ...   | ...    | ...  | ...    |

注意：

这里的“推荐/不推荐”只针对**架构方案决策**，不是产品功能评价。

---

# 十八、最终关闭 OD-34

必须明确：

```text
OD-34 = CLOSED
```

或者：

```text
OD-34 = OPEN
```

如果 CLOSED：

必须写出完整规范：

```text
Duplicate Trigger:
...

HTTP Status:
...

Response:
...

content:
...

route:
...

data:
...

metadata:
...

Replay Marker:
...

AI Execution:
...

USER Turn:
...

ASSISTANT Turn:
...
```

---

# 十九、G-2 Authorization

OD-34 CLOSED 后，明确：

```text
G-2 = READY
```

或者：

```text
G-2 = BLOCKED
```

G-2 至少包含：

```text
Idempotency-Key Header
Duplicate Response Contract
409 In-flight
422 invalid key
same-key different-payload conflict
```

注意：

本阶段只判断 readiness。

**不要实现。**

---

# 二十、与 Step 6 的边界

本阶段结束后：

```text
Step 5A
    ↓
OD-34 CLOSED
    ↓
G-2 READY
    ↓
Step 6
Transaction / Concurrency
```

Step 6 才允许：

```text
Header
Idempotency DB column
UNIQUE
409 mapping
retry
concurrency
TX1/TX2
```

本阶段全部禁止。

---

# 二十一、Open Decisions

必须保留：

```text
OD-22 = OPEN
OD-23 = OPEN
OD-24 = CLOSED
OD-25 = CLOSED
OD-26 = CLOSED
OD-30 = OPEN
OD-31 = OPEN
OD-32 = OPEN
OD-33 = OPEN
```

新增：

```text
OD-34
```

Step 5A 最终必须关闭或明确继续 OPEN。

---

# 二十二、Evidence Boundary

再次确认：

```text
Idempotency-Key
conversation_id
turn_id
assistant_request_id
```

都不能进入：

```text
Evidence identity
Evidence provenance
dataset_version
```

当前：

```text
Evidence = Source / Provenance Unit
Runtime Evidence = DEFERRED
OD-31 = OPEN
OD-32 = OPEN
```

不要修改。

---

# 二十三、允许修改文件

只允许新增：

```text
docs/evaluation/Phase 4.2 Step 5A — OD-34 Duplicate Replay Response Contract.md
```

如果 Roadmap 需要记录：

```text
docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md
```

只能：

```text
append-only
```

不得删除历史内容。

---

# 二十四、禁止修改

最终检查：

```text
backend = 0
tests = 0
DB = 0
API = 0
```

使用：

```powershell
git diff -- backend
git diff -- tests
git status
```

确认没有意外代码修改。

---

# 二十五、最终报告

严格输出：

```text
【Phase 4.2 Step 5A COMPLETE】

1. OD-34
2. Duplicate semantics
3. Message Replay / Result Replay
4. Response Contract
5. HTTP Status
6. route/data/metadata
7. Replay Marker
8. AI Execution
9. USER Turn
10. ASSISTANT Turn
11. G-2 Readiness
12. Open Decisions
13. Evidence Boundary
14. 新增文件
15. Roadmap
16. Backend
17. Tests
18. DB
19. API
```

最后：

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
不要实现 Idempotency。
不要修改 DB。
不要修改 API。
不要进入 Step 7。
