你现在开始执行：

# Phase 4.2 Step 0 — Roadmap Freeze Audit

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

## 一、目标

Phase 4.2 Roadmap Analysis 已完成。

现在只做：

```text
Roadmap
    ↓
Contract
    ↓
Repository
    ↓
Git
```

四方一致性检查。

本步骤：

**不实现任何 Phase 4.2 功能。**

---

## 二、禁止

禁止：

```text
修改 backend
修改 tests
修改 API
修改 DB
修改 Prompt
修改 AIOrchestrator
修改 ChatApplicationService
实现 EvidenceBuilder
实现消息幂等
实现 Context Window
实现 Trace
```

禁止：

```text
git commit
git push
git merge
git rebase
git reset
git clean
```

---

## 三、检查 Phase 4.2 Roadmap

读取：

```text
docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md
```

确认：

```text
Step 1  Message Idempotency
Step 2  AI Result → Turn Semantics
Step 3  Evidence Builder Contract
Step 4  Evidence Runtime Integration
Step 5  ConversationEvidence Association
Step 6  Transaction Boundary
Step 7  Multi-turn Context Boundary
Step 8  Trace Correlation
Step 9  Real Conversation Runtime E2E
Step 10 Security
Step 11 Traceability
Step 12 Release Readiness
Step 13 Git Closeout
```

如果实际 Roadmap 中名称不同：

以仓库文档为准。

---

## 四、检查依赖关系

必须确认：

```text
Step 1
  ↓
Step 2
  ↓
Step 3
  ↓
Step 4
  ↓
Step 5
  ↓
Step 6
```

以及：

```text
Step 7
```

可以独立于 Evidence Integration 设计，但最终必须在 Step 9 E2E 汇合。

确认：

```text
Step 8
```

是否可以 Deferred。

如果可以：

明确：

```text
DEFERRED
```

不要为了完整性强行实现。

---

## 五、检查 Open Decisions

确认以下决策仍然存在：

```text
OD-14 Message Idempotency
OD-15 Concurrent Message Ordering
OD-16 AI Result Persistence Semantics
OD-17 Evidence Builder Ownership
OD-18 Runtime Transaction Boundary
OD-19 AI Failure / Refusal Semantics
OD-20 Context Window / Budget
OD-21 Evidence ↔ Trace Association
```

逐项标记：

```text
OPEN
DEFERRED
CLOSED
```

禁止本步骤自行关闭。

---

## 六、确认 Phase 4.1 Contract 不被 Phase 4.2 Roadmap 覆盖

特别检查：

### Evidence Identity

不得加入：

```text
conversation_id
turn_id
assistant_request_id
provider_request_id
```

### Provenance

仍然：

```text
dataset_version
source_type
```

### Conversation ↔ Evidence

仍然：

```text
REFERENCE
```

不是：

```text
OWNERSHIP
```

### assistant_request_id

仍然：

```text
CORRELATION ONLY
```

不能升级成：

```text
FK
Evidence Identity
Provenance
```

---

## 七、确认数据库策略

Phase 4.2 当前默认：

```text
DB CHANGE = NO
```

确认 Roadmap 没有偷偷加入：

```text
new table
new migration
new column
```

如果某个 Step 确实需要 DB：

必须保持：

```text
UNDEFINED
```

并列入对应 Step 的 Contract Decision。

---

## 八、确认 API 策略

当前已有：

```text
POST /conversations
GET /conversations/{id}
GET /conversations/{id}/messages
POST /conversations/{id}/archive
POST /conversations/{id}/messages
```

确认：

```text
GET /conversations
```

仍然：

```text
DEFERRED
```

不要因为 Roadmap Freeze 新增 API。

---

## 九、确认 Production Runtime Gap

冻结以下事实：

```text
ChatApplicationService
        ↓
Real AIOrchestrator
        ↓
AI Result
        ↓
Assistant Turn
```

= CURRENT

而：

```text
AI Result
        ↓
Evidence
```

= MISSING

以及：

```text
Evidence
        ↓
ConversationEvidence
```

生产调用：

```text
MISSING
```

---

## 十、确认 Step 46 的性质

必须明确：

Step 46：

```text
Real Conversation Evidence E2E
```

证明：

```text
Conversation
→ ChatApplicationService
→ FakeOrchestrator
→ AI Result
→ Test-composed Evidence
→ ConversationEvidence
→ PostgreSQL
```

因此：

```text
Step 46 ≠ Production Evidence Runtime
```

不能把 Step 46 当成已经完成生产集成的证据。

---

## 十一、生成 Freeze Audit

只允许新增：

```text
docs/evaluation/Phase 4.2 Step 0 — Roadmap Freeze Audit.md
```

内容：

```text
1. Roadmap consistency
2. Phase 4.1 contract inheritance
3. Open Decisions
4. Production runtime gaps
5. Dependency verification
6. DB strategy
7. API strategy
8. Step 1 readiness
```

---

## 十二、Step 1 Readiness Gate

最终必须回答：

```text
Step 1 Message Idempotency
```

是否可以开始。

只有满足：

```text
Roadmap = consistent
Phase 4.1 contracts = preserved
OD-14 = OPEN
No production code changed
No DB changed
No API changed
```

才能：

```text
Step 1 READY
```

---

## 十三、最终报告

严格输出：

```text
【Phase 4.2 Step 0 COMPLETE】

1. Roadmap
2. Contract inheritance
3. Open Decisions
4. Runtime gaps
5. Dependency graph
6. DB strategy
7. API strategy
8. Step 46 boundary
9. Files added
10. Git status
11. Step 1 readiness
```

最后：

```text
Phase 4.2 Step 0 = COMPLETE

Step 1 = READY

STOP
```

不要开始 Step 1。
