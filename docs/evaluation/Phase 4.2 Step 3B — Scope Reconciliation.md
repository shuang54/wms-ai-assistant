# Phase 4.2 Step 3B — Scope Reconciliation

> 只做 Scope / Architecture / Roadmap / Open Decision 协调；**不写代码、不改 DB、不改 API**。
> 依据：Step 0 / 1 / 1A / 2 / 3 / 3A 结论 + Phase 4.1 冻结契约。

---

## 1. Frozen Status（本阶段输入）

```text
Step 0  COMPLETE · Step 1 COMPLETE · Step 1A COMPLETE · Step 2 COMPLETE
Step 3 COMPLETE · Step 3A COMPLETE · Step 4 BLOCKED
OD-26 OPEN · OD-30 OPEN · OD-31 OPEN · OD-32 OPEN（本阶段均不关闭）
```

---

## 2. Revised Phase 4.2 Scope

```text
Phase 4.2 = Production Conversation Runtime Foundation
```

```text
Message Idempotency
        ↓
AI Result → Assistant Turn Semantics
        ↓
Conversation Context（多轮）
        ↓
Transaction / Concurrency（Turn 一致性）
        ↓
Real Conversation Runtime E2E
        ↓
Security / Regression / Release
```

**Runtime Evidence 不再是 Phase 4.2 完成条件**（OD-31 / OD-32 未来解决）。

---

## 3. Evidence Boundary（冻结）

```text
Evidence = Source / Provenance Unit（治理与评审对象）
职责：Dataset · Provenance · Lifecycle · Annotation · Review · Conversation Reference
不是：AI Answer · Runtime Result · Tool Result · RAG Chunk · SQL Result
```

当前实现路径：

```text
AI Result → Assistant Turn        ✅ CURRENT
AI Result → Trace / Observability ✅ CURRENT（request_id 关联）
AI Result → Runtime Evidence Artifact  ⛔ DEFERRED
```

---

## 4. Step 4 Reconciliation

| # | 问题 | 结论 |
| - | ---- | ---- |
| 1 | 原目标 | 生产 Runtime 创建 Evidence（AI Result → Evidence） |
| 2 | 仍成立 | Evidence **持久化层**已就绪（Phase 4.1 Step 37–45） |
| 3 | 依赖 Runtime Evidence | **全部依赖**（dataset_version 与内容承载均缺） |
| 4 | 因 Architecture C 失效 | 是 —— 无合法输入可让 Runtime 产生 Evidence |
| 5 | 是否删除 | **否**（保留历史决定，确保可追溯） |
| 6 | 是否重命名 | 否 |
| 7 | 是否延期 | **是 → DEFERRED** |

```text
Original Step 4: Evidence Runtime Integration
Reconciled     : DEFERRED — 依赖 OD-31（Artifact 架构）与 OD-32（Runtime Dataset Version）
Reason         : Evidence = reusable Source/Provenance Unit；
                 当前 Runtime 无 dataset_version、无 runtime artifact 持久化
```

---

## 5. Step 5 Reconciliation（ConversationEvidence Association）

* ConversationEvidence **持久化层**已在 Phase 4.1 Step 45 完成（Repository + 表 + 约束）；
* Runtime 侧调用依赖"A Runtime 产生了 Evidence"，但 Runtime Evidence 已 DEFERRED；
* 因此：ConversationEvidence 继续作为 **Phase 4.1 Foundation** 存在，
  Runtime Association 与 Step 4 一并延期。

```text
Step 5 = DEFERRED（与 Step 4 同因）
```

---

## 6. Step 6 Reconciliation（Transaction Boundary）

* 重新聚焦 **Conversation 自身**（不含 Evidence）：

```text
TX1 USER Turn → AI execution → TX2 ASSISTANT Turn
```

* 解决：USER/ASSISTANT Turn 一致性 · 幂等保留位置 · 并发重复请求 · 失败重试语义
* **不**解决 Evidence 事务一致性（随 Step 4 延期）

```text
Step 6 = MODIFY + KEEP（范围收敛为 Conversation Runtime 事务；移除 Evidence 事务部分）
```

---

## 7. Step 7 Reconciliation（Multi-turn Context）

* 独立于 Runtime Evidence；当前链路真实存在：
  `previous turns → ConversationContextBuilder → AIOrchestrator`
* 需明确：context window / boundary / history ordering / 当前 turn 是否进入 / 失败行为

```text
Step 7 = KEEP（Phase 4.2 核心目标）
```

---

## 8. Step 8 Reconciliation（Trace Correlation）

* `request_id` 已能关联 LLM Usage / RAG / Tool / Text-to-SQL（Phase 3 既有能力）；
* `request_id` 被契约**禁止**进入 Evidence identity / provenance；
* 因此不为 Evidence 扩展 Trace 关联。

```text
Step 8 = MODIFY（收敛为"验证 Conversation → request_id → Trace 已有关联"；不做新扩展）+ KEEP
```

---

## 9. Step 9 Reconciliation（Real Conversation Runtime E2E）

重新定义（不含 Evidence）：

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

原 `AI Result → Evidence → ConversationEvidence` 段落移至未来 Runtime Evidence Phase。

```text
Step 9 = MODIFY + KEEP
```

---

## 10. Step 10–13 Reconciliation

| Step | 结论 | 原因 |
| ---- | ---- | ---- |
| 10 Security | **KEEP** | 覆盖 Conversation / Turn / Context / Trace / Repository；不含 Runtime Evidence 部分 |
| 11 Traceability | **KEEP** | 追溯矩阵覆盖已实施 Step；延期的 Step 记录为 DEFERRED |
| 12 Release Readiness | **KEEP** | 完成条件按修订后 Scope 判定 |
| 13 Git Closeout | **KEEP** | 流程不变 |

---

## 11–14. Open Decision 状态（均不关闭）

```text
OD-26 OPEN — EMPTY 是否视为 idempotency completed（Step 1A × Step 2 联合决策）
OD-30 OPEN — EMPTY 是否允许 Evidence Builder 独立消费 data（随 Step 3/4 延期后仍待定）
OD-31 OPEN — Runtime Evidence Artifact Architecture（需独立 Artifact 层？候选 Architecture B）
OD-32 OPEN — Runtime Dataset Version Contract（RAG / Tool / DB snapshot 统一契约）
```

OD-32 明确禁止：`request_id` / `conversation_id` / `turn_id` / `timestamp` / `UUID` /
`idempotency_key` 冒充 dataset_version。

---

## 15. Revised Completion Criteria（Phase 4.2）

```text
Message Idempotency Contract        （契约 + 实施，OD-14/OD-22 授权后）
AI Result → Assistant Turn Semantics PASS（OD-16 / OD-19 CLOSED）
Multi-turn Context Boundary          PASS
Transaction / Concurrency（Turn）    PASS
Real Conversation Runtime E2E        PASS
Trace Correlation（Conversation↔Trace）PASS
Security                             PASS
Regression / Traceability            PASS
Release                              PASS
Production DB writes                 0
```

**不再包含**：Runtime Evidence Integration · Runtime ConversationEvidence Association
（均 DEFERRED，随 OD-31 / OD-32）。

---

## 16. Roadmap Change

Roadmap v1.0 需修订（Step 4 / 5 / 8 / 9 定义与 Scope 变化）→
以**追加 Reconciliation 章节**方式修订（保留 Original，不删除历史决定）。
