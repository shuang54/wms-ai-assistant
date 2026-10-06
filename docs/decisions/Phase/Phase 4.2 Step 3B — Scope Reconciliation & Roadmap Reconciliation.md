你现在开始执行：

# Phase 4.2 Step 3B — Scope Reconciliation & Roadmap Reconciliation

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

Phase 4.2 Step 3A 已经确认：

```text
Evidence = Source / Provenance Unit
```

不是：

```text
Runtime AI Result
```

同时确认：

```text
Runtime Evidence = DEFERRED
Runtime Dataset Version = 当前不存在
```

因此原 Phase 4.2：

```text
Step 4 Evidence Runtime Integration
```

已经无法按照原始定义直接实施。

本阶段唯一目标：

> **重新协调 Phase 4.2 的 Scope、Open Decisions 与 Roadmap，使后续 Step 不再建立在错误的 Evidence Runtime 假设上。**

本阶段只做：

```text
Scope Reconciliation
Architecture Reconciliation
Roadmap Reconciliation
Open Decision Reconciliation
```

不写代码。

---

# 二、必须先阅读

阅读：

```text
docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md

docs/evaluation/Phase 4.2 Step 0 — Roadmap Freeze Audit.md
docs/evaluation/Phase 4.2 Step 1 — Message Idempotency Contract.md
docs/evaluation/Phase 4.2 Step 1A — OD-22 Idempotency Architecture Decision.md
docs/evaluation/Phase 4.2 Step 2 — AI Result Assistant Turn Semantics.md
docs/evaluation/Phase 4.2 Step 3 — Evidence Builder Contract.md
docs/evaluation/Phase 4.2 Step 3A — Evidence Runtime Capability Decision.md
```

同时检查 Phase 4.1 已冻结的：

```text
Evidence
Annotation
ConversationEvidence
Conversation
ConversationTurn
```

---

# 三、必须确认当前状态

冻结：

```text
Step 0  COMPLETE
Step 1  COMPLETE
Step 1A COMPLETE
Step 2  COMPLETE
Step 3  COMPLETE
Step 3A COMPLETE
```

当前：

```text
Step 4 BLOCKED
```

并确认：

```text
OD-26 OPEN
OD-30 OPEN
OD-31 OPEN
OD-32 OPEN
```

不得偷偷关闭任何 Open Decision。

---

# 四、核心架构结论

必须冻结：

```text
Evidence
=
Source / Provenance Unit
```

其职责：

```text
Dataset
Provenance
Lifecycle
Annotation
Review
Conversation Reference
```

不是：

```text
AI Answer
Runtime Result
Tool Result
RAG Chunk
SQL Result
```

---

# 五、明确 Phase 4.2 当前不做 Runtime Evidence

冻结：

```text
AI Result
    ↓
Assistant Turn
```

以及：

```text
AI Result
    ↓
Trace / Observability
```

当前不实现：

```text
AI Result
    ↓
Runtime Evidence Artifact
```

因此：

```text
Runtime Evidence
=
DEFERRED
```

---

# 六、重新分析原 Step 4

原 Step 4：

```text
Evidence Runtime Integration
```

必须重新判断。

回答：

1. 原 Step 4 的目标是什么？
2. 哪些目标仍然成立？
3. 哪些目标依赖 Runtime Evidence？
4. 哪些目标已经因为 Architecture C 而失去意义？
5. 是否应该删除？
6. 是否应该重命名？
7. 是否应该延期到未来 Phase？

不得直接删除历史文档。

如果需要调整 Roadmap：

保留：

```text
Original Decision
Reconciliation Decision
Reason
```

确保历史可追溯。

---

# 七、重新分析后续 Step

逐个检查：

```text
Step 5 ConversationEvidence Association Runtime
Step 6 Transaction Boundary
Step 7 Multi-turn Context Boundary
Step 8 Trace Correlation
Step 9 Real Conversation Runtime E2E
Step 10 Security
Step 11 Regression / Traceability
Step 12 Release Readiness
Step 13 Git Closeout
```

对每个 Step 判断：

```text
KEEP
MODIFY
DEFER
REMOVE
BLOCKED
```

并说明原因。

特别检查：

## Step 5

如果 Runtime 不产生 Evidence：

```text
AI Result → Evidence
```

不存在。

那么：

```text
ConversationEvidence association
```

是否仍需要 Runtime Integration？

必须从当前业务语义判断。

---

# 八、ConversationEvidence 必须单独分析

Phase 4.1 已冻结：

```text
Conversation
    ↕
ConversationEvidence
    ↕
Evidence
```

Evidence 是 reusable domain object。

如果没有 Runtime Evidence：

需要判断：

```text
ConversationEvidence
```

是否仍属于：

```text
Phase 4.2 Conversation Runtime
```

还是应该保持为：

```text
Phase 4.1 Foundation
```

并继续等待未来 Runtime Evidence Artifact。

不要修改数据库。

---

# 九、Step 6 Transaction Boundary

必须检查：

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

Step 6 原本可能因为：

```text
Evidence
ConversationEvidence
```

而需要重新考虑。

现在 Runtime Evidence 被延期后：

必须重新判断：

```text
Step 6
```

是否仍然需要。

如果仍然需要：

明确它解决的是：

```text
USER / ASSISTANT Turn consistency
Idempotency
Concurrent requests
```

还是 Evidence consistency。

不要混在一起。

---

# 十、Step 7 Multi-turn Context

Step 7 不应该因为 Evidence Runtime 被延期而自动延期。

必须独立判断：

当前：

```text
Conversation
 ↓
previous turns
 ↓
ConversationContextBuilder
 ↓
AIOrchestrator
```

需要明确：

```text
context window
context boundary
history ordering
current turn inclusion
assistant failure behavior
```

如果仍然是 Phase 4.2 的核心目标：

```text
KEEP
```

否则：

```text
DEFER
```

必须给出原因。

---

# 十一、Step 8 Trace Correlation

Step 2 已确认：

```text
assistant_request_id
=
correlation only
```

并且：

```text
request_id
≠
Evidence identity
```

因此重新判断：

```text
Step 8
```

是否仍然有必要。

如果 Trace 已经能够通过：

```text
request_id
```

关联：

```text
LLM
RAG
Tool
Text-to-SQL
```

则不要为了 Evidence 再扩展。

---

# 十二、Step 9 Real Conversation Runtime E2E

必须重新定义最终 E2E：

当前应该证明：

```text
POST Message
    ↓
USER Turn
    ↓
Conversation Context
    ↓
AIOrchestrator
    ↓
AI Result
    ↓
ASSISTANT Turn
    ↓
Trace / Observability
```

而不是：

```text
AI Result
    ↓
Evidence
    ↓
ConversationEvidence
```

后者属于未来 Runtime Evidence Phase。

---

# 十三、OD-31

定义：

```text
OD-31 Runtime Evidence Artifact Architecture
```

状态：

```text
OPEN / DEFERRED
```

内容至少说明：

```text
为什么需要
当前为什么不实现
未来候选 Architecture B
与 Evidence Governance Record 的边界
```

不要关闭。

---

# 十四、OD-32

定义：

```text
OD-32 Runtime Dataset Version Contract
```

状态：

```text
OPEN / DEFERRED
```

必须明确：

```text
RAG dataset version
Tool dataset version
Database snapshot/version
```

未来需要统一 Runtime Contract。

禁止：

```text
request_id
conversation_id
turn_id
timestamp
UUID
idempotency_key
```

冒充 dataset_version。

---

# 十五、OD-26 / OD-30

保持：

```text
OD-26 OPEN
OD-30 OPEN
```

解释：

它们与：

```text
EMPTY
AI Result
Idempotency
Evidence Builder
```

有关。

当前不要因为 Runtime Evidence 被延期而自动解决。

---

# 十六、Roadmap 修改原则

如果 Roadmap 需要修改：

不要删除原始路线。

使用：

```text
Original
Reconciled
```

例如：

```text
Original Step 4:
Evidence Runtime Integration

Reconciled:
Deferred — Runtime Evidence is outside current Phase 4.2 scope.
```

并保留原因：

```text
Evidence is a reusable Source / Provenance Unit.
Current Runtime lacks dataset_version and runtime artifact persistence.
```

---

# 十七、Phase 4.2 新目标

重新定义 Phase 4.2：

```text
Phase 4.2
Production Conversation Runtime Foundation
```

核心：

```text
Message Idempotency
        ↓
AI Result → Assistant Turn Semantics
        ↓
Conversation Context
        ↓
Transaction / Concurrency
        ↓
Real Conversation Runtime
        ↓
Security / Regression
```

不再把：

```text
Runtime Evidence
```

作为本阶段完成条件。

---

# 十八、禁止事项

本阶段禁止：

```text
❌ 修改 Evidence Model
❌ 新增 Evidence Artifact
❌ 新增 dataset_version
❌ 修改 ConversationEvidence
❌ 修改 Annotation
❌ 修改 ChatApplicationService
❌ 修改 AIOrchestrator
❌ 修改 RAG
❌ 修改 Tool
❌ 修改 Text-to-SQL
❌ 修改 API
❌ DB migration
❌ DB write
❌ Runtime implementation
```

---

# 十九、唯一允许新增/修改文件

允许：

```text
docs/evaluation/Phase 4.2 Step 3B — Scope Reconciliation.md
```

以及：

```text
docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md
```

**但只有当实际发现 Roadmap 必须修订时才能修改 Roadmap。**

如果不需要修改：

```text
Roadmap = 0 modification
```

不要为了产生 diff 而修改。

---

# 二十、完成标准

必须回答：

```text
1. Step 4 是否继续存在？
2. 如果存在，职责是什么？
3. Runtime Evidence 是否属于 Phase 4.2？
4. ConversationEvidence Runtime 是否仍属于 Phase 4.2？
5. Step 5 是否 KEEP / MODIFY / DEFER？
6. Step 6 是否 KEEP / MODIFY / DEFER？
7. Step 7 是否 KEEP / MODIFY / DEFER？
8. Step 8 是否 KEEP / MODIFY / DEFER？
9. Step 9 如何重新定义？
10. Step 10–13 是否受影响？
11. OD-31 状态？
12. OD-32 状态？
13. OD-26 状态？
14. OD-30 状态？
15. 新的 Phase 4.2 completion criteria？
16. Step 4 下一步是否 READY？
```

---

# 二十一、最终报告格式

严格：

```text
【Phase 4.2 Step 3B COMPLETE】

1. Phase 4.2 Revised Scope
2. Evidence Boundary
3. Step 4 Reconciliation
4. Step 5 Reconciliation
5. Step 6 Reconciliation
6. Step 7 Reconciliation
7. Step 8 Reconciliation
8. Step 9 Reconciliation
9. Step 10–13 Reconciliation
10. OD-26
11. OD-30
12. OD-31
13. OD-32
14. Revised Completion Criteria
15. Roadmap Change
16. 新增文件
17. 修改文件
18. Backend
19. Tests
20. DB
21. API
22. Next Step
```

最终：

```text
Step 3B = COMPLETE
```

并明确：

```text
Step 4 = READY
```

或者：

```text
Step 4 = BLOCKED
```

如果 Step 4 仍然 BLOCKED：

必须指出真正 blocker。

---

# 二十二、最终 STOP

完成 Step 3B 后：

**立即停止。**

不要执行：

```text
Step 4
Step 5
Evidence Runtime
DB Migration
API implementation
```

本阶段只完成：

```text
Phase 4.2 Scope
        ↓
Evidence Boundary
        ↓
Roadmap Reconciliation
        ↓
确定真正下一步
```
