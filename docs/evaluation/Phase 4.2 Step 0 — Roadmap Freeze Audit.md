# Phase 4.2 Step 0 — Roadmap Freeze Audit

> 四方一致性检查：**Roadmap ↔ Contract ↔ Repository ↔ Git**
> 本步骤**不实现任何 Phase 4.2 功能**；未修改 backend / tests / API / DB / Prompt。

---

## 1. Roadmap Consistency

来源：`docs/decisions/Phase/Phase 4.2 Roadmap v1.0.md`（仓库实际内容为准）。

| # | Step（仓库实际名称） | 状态 |
| - | -------------------- | ---- |
| 1 | Conversation Message Idempotency Contract | PLANNED |
| 2 | AI Result → ConversationTurn 语义冻结 | PLANNED |
| 3 | AI Result → Evidence 映射契约（Evidence Builder Contract） | PLANNED |
| 4 | Evidence Runtime Integration（生产创建） | PLANNED |
| 5 | ConversationEvidence Runtime Association | PLANNED |
| 6 | Conversation Runtime Transaction Boundary | PLANNED |
| 7 | Multi-turn Context 边界 | PLANNED |
| 8 | Trace Correlation | PLANNED（**可 DEFERRED**） |
| 9 | Conversation Runtime E2E | PLANNED |
| 10 | Security / Boundary Audit | PLANNED |
| 11 | Regression / Traceability | PLANNED |
| 12 | Release Readiness | PLANNED |
| 13 | Git Closeout（Commit / Push / PR / Merge / Verification） | PLANNED |

一致性：Step 数量 13，与 Roadmap Analysis 输出一致；每个 Step 均含
目标 / 依赖 / INPUT / OUTPUT / 代码归属 / DB / API / 安全影响 / 风险标记。
**无**"完成 Conversation Runtime 全部功能"式的大步骤。

---

## 2. Phase 4.1 Contract Inheritance（代码实测）

| 契约 | 实测结果 | 状态 |
| ---- | -------- | ---- |
| Evidence 列集合 | evidence_id · dataset_version · source_type · de_identification_attested · de_identification_method · status · created_at · updated_at | **PRESERVED** |
| Annotation 列集合 | annotation_id · evidence_id · case_id · annotation_version · annotator_id · review_status · created_at · updated_at | **PRESERVED** |
| Evidence identity 不含会话/请求 ID | 源码命中仅 1 处 = **注释**（"不是 conversation_id"）；列集合 0 命中 | **PRESERVED** |
| Provenance 字段 | `EvidenceProvenanceRow` = dataset_version + source_type | **PRESERVED** |
| Conversation ↔ Evidence | REFERENCE（关联表 conversation_evidence；父表互不持有对方 ID） | **PRESERVED** |
| assistant_request_id | `ConversationTurn` FK 仅指向 conversation；assistant_request_id **非 FK** | **CORRELATION ONLY** |
| 幂等键 | (source_type, dataset_version) 组合唯一 | **PRESERVED** |
| 状态机 | Evidence 5 态 + FINALIZED terminal；Annotation DRAFT→REVIEWED | **PRESERVED** |

→ Phase 4.2 Roadmap **未覆盖 / 未弱化**任何 Phase 4.1 冻结契约；
Roadmap 中 `assistant_request_id` 明确不得升级为 FK / Evidence identity / provenance。

---

## 3. Open Decisions（逐项标记，**本步骤不关闭任何项**）

| ID | Decision | Status |
| -- | -------- | ------ |
| OD-12 | turn-level provenance（Phase 4.1 继承） | **DEFERRED** |
| OD-14 | Conversation message idempotency | **OPEN** |
| OD-15 | Concurrent message ordering | **OPEN** |
| OD-16 | AI Result persistence semantics | **OPEN** |
| OD-17 | Evidence Builder ownership | **OPEN** |
| OD-18 | Runtime transaction boundary | **OPEN** |
| OD-19 | AI failure / refusal semantics | **OPEN** |
| OD-20 | Context window / budget | **OPEN** |
| OD-21 | Evidence ↔ Trace association | **OPEN**（可 DEFERRED，待 Step 8 决策） |

---

## 4. Production Runtime Gaps（冻结事实）

```text
ChatApplicationService → Real AIOrchestrator → AI Result → Assistant Turn
                                                              = CURRENT（真实生产路径）

AI Result → Evidence                                          = MISSING
Evidence → ConversationEvidence（生产调用）                    = MISSING
```

证据：

* `EvidenceBuilder` / `EvidenceFactory` / `EvidenceMapper` / `AIResultToEvidence` = **0 命中**；
* `create_association()` 调用方 = **仅 Repository 自身 + tests**，**无生产调用方**；
* `execute_message()` 真实流程中**不含**任何 Evidence / Association 写入。

---

## 5. Dependency Verification

```text
Step 1 → Step 2 → Step 3 → Step 4 → Step 5 → Step 6   （顺序依赖，已确认）
Step 7 可独立设计，但必须在 Step 9（E2E）前汇合         （已确认）
Step 8 可 DEFERRED（不因完整性强行实现）                （已确认）
Step 9 → 10 → 11 → 12 → 13                             （顺序依赖）
```

Parallel-eligible：Step 4/5 与 Step 7；Step 8 可后置。
**无**循环依赖、**无**未声明前置。

---

## 6. DB Strategy

```text
Phase 4.2 默认：DB CHANGE = NO
```

* Roadmap 未引入新表 / 新迁移 / 新列；
* Step 1 若确需业务幂等键，须单独论证（优先不加列，保持 UNDEFINED 直至决策）；
* Step 3–5 明确 `DB CHANGE: NO`（复用现有 Evidence / ConversationEvidence 表）；
* 本次 Audit 未产生任何 DDL。

---

## 7. API Strategy

现有（保持不变）：

```text
POST   /api/conversations
GET    /api/conversations/{id}
GET    /api/conversations/{id}/messages
POST   /api/conversations/{id}/archive
POST   /api/conversations/{id}/messages
```

```text
GET /api/conversations（列表） = DEFERRED
```

Phase 4.2 优先在 Application/Service 层完成 Runtime；
Application Service ≠ HTTP API，不为"未来方便"提前新增 API。

---

## 8. Step 1 Readiness

| 条件 | 结果 |
| ---- | ---- |
| Roadmap = consistent | **YES**（13 Step，边界清晰，无大步骤） |
| Phase 4.1 contracts = preserved | **YES**（代码实测 8 项全部 PRESERVED） |
| OD-14 = OPEN | **YES**（未关闭、未实现） |
| No production code changed | **YES**（git diff 为空） |
| No DB changed | **YES** |
| No API changed | **YES** |

→ **Step 1 = READY**（但本步骤不开始 Step 1，等待明确指令）。
