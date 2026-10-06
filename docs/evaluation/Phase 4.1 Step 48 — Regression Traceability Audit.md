# Phase 4.1 Step 48 — Regression Traceability Audit

> 本阶段**不新增业务能力**：只做 Step 37–47 的 Regression + Traceability 审计。
> 新增测试：`tests/test_phase_4_1_traceability.py`（12 项，静态 + 只读 catalog）。

---

## 1. Scope

```text
Step 37–47：Persistence · Lifecycle · Conversation Integration · E2E · Security
```

追溯链：

```text
Frozen Contract → Implementation → Test → Regression → Evidence
```

检查项：Contract Drift · 孤立实现 · 孤立测试 · 覆盖假阳性 ·
历史 Guard 漂移 · 未声明边界 · DB Schema 未声明变更。

---

## 2. Step 37–47 Traceability Matrix

| Step | Contract | Implementation | Test | DB | Security | Status |
| ---- | -------- | -------------- | ---- | -- | -------- | ------ |
| 37 | Evidence / Annotation 持久化 | `EvidenceRepository` + ORM（evidence_record / evidence_annotation_record） | `test_evidence_persistence_db.py` | YES | YES | **PASS** |
| 38 | Provenance = dataset_version + source_type | `EvidenceRepository` read model + `validate_provenance` | `test_evidence_provenance_persistence_db.py` | YES | YES | **PASS** |
| 39 | Evidence ↔ Annotation FK | FK + `list_annotations(evidence_id)` | `test_evidence_annotation_association_db.py` | YES | YES | **PASS** |
| 40 | 幂等键 (source_type, dataset_version) + 事务 | Repository first-write-wins + DB UNIQUE | `test_evidence_transaction_idempotency_db.py` | YES | YES | **PASS** |
| 41 | Annotation DRAFT→REVIEWED | `update_annotation_review_status` + `ANNOTATION_REVIEW_TRANSITIONS` | `test_evidence_annotation_review_db.py` | YES | YES | **PASS** |
| 42 | Finalization Contract | 迁移终态 + 无联动（契约冻结） | `test_evidence_finalization_contract_db.py` | YES | YES | **PASS** |
| 43 | Lifecycle Consistency Matrix | 状态机 + 组合矩阵（契约冻结） | `test_evidence_lifecycle_consistency_db.py` | YES | YES | **PASS** |
| 44 | Conversation ↔ Evidence Contract | 契约文档（无 DB 变更） | `test_conversation_evidence_contract.py` | N/A | YES | **PASS** |
| 45 | 关联持久化 | `conversation_evidence` + `ConversationEvidenceRepository` | `test_conversation_evidence_persistence_db.py` | YES | YES | **PASS** |
| 46 | Real Conversation E2E | `ChatApplicationService` + 真实 Repository | `test_conversation_evidence_e2e_db.py` | YES | YES | **PASS** |
| 47 | Security / Boundary | 既有边界（未新增授权系统） | `test_phase_4_1_security_boundary_db.py` | YES | YES | **PASS** |

每行均由**真实代码 + 真实测试**复核（非复制历史表格）。

---

## 3. Frozen Contract Inventory

| Contract | 冻结处 | 当前状态 |
| -------- | ------ | -------- |
| Evidence 列集合（8 列） | Step 37 | 与 ORM 一致（`test_37`） |
| Provenance = dataset_version + source_type | Step 38 | 未被会话 ID 污染（`test_38`） |
| Annotation.evidence_id FK | Step 39 | FK 指向 evidence_record（`test_39`） |
| 幂等键组合唯一 | Step 40 | UniqueConstraint(source_type, dataset_version)（`test_40`） |
| ANNOTATION_REVIEW_TRANSITIONS | Step 41 | `{DRAFT:(REVIEWED,), REVIEWED:()}`（`test_41`） |
| EVIDENCE_STATUS_TRANSITIONS | Step 42/43 | 5 态线性 + FINALIZED 终态（`test_42`） |
| Conversation/Evidence 互不持有对方 ID | Step 44 | 双向无字段（`test_44`） |
| conversation_evidence 结构 + 复合 PK | Step 45 | 3 列 + 双 FK + PK（`test_45`） |
| OD-11（0..N×0..N）· OD-13（幂等） | Step 45 | FROZEN |
| OD-12（turn-level） | Step 46 | **DEFERRED**（非永久拒绝） |

---

## 4. Contract → Test Matrix

| Frozen Contract | 对应测试 | 覆盖 |
| --------------- | -------- | ---- |
| Evidence provenance | `test_evidence_provenance_persistence_db.py` | YES |
| Annotation FK / cross-evidence 隔离 | `test_evidence_annotation_association_db.py` | YES |
| 幂等 / 事务回滚 / 并发 | `test_evidence_transaction_idempotency_db.py` | YES |
| Annotation review 迁移 | `test_evidence_annotation_review_db.py` | YES |
| FINALIZED terminal | `test_evidence_finalization_contract_db.py` | YES |
| Lifecycle 组合可达性 | `test_evidence_lifecycle_consistency_db.py` | YES |
| 关联持久化 / 幂等 / 删除语义 | `test_conversation_evidence_persistence_db.py` | YES |
| 真实 E2E + read-back | `test_conversation_evidence_e2e_db.py` | YES |
| 安全边界 / 负例 | `test_phase_4_1_security_boundary_db.py` | YES |
| 身份 / 契约常量一致性 | `test_phase_4_1_traceability.py`（本 Step） | YES |

→ 每个 Frozen Contract 至少 1 个对应测试；**无 Traceability Gap**。

---

## 5. Test → Contract Audit（是否存在验证旧契约的测试）

* Step 45 Guard Reconciliation 已将 4 个历史 Guard
  （architecture / model / persistence / evidence boundary）迁移为
  **scope-aware 白名单 + 精确集合比较**；
* 未发现仍在验证"Evidence 持久化不得存在"之类**已废弃契约**的测试；
* 未删除任何测试、未 skip、未 xfail、未降低断言强度。

---

## 6. Implementation → Contract Audit（孤立实现）

`ConversationEvidenceRepository` public 方法：

```text
create_association        → Step 45（OD-13 幂等 + 父校验）
get_association           → Step 45
list_evidence_ids         → Step 45（OD-11）
list_conversation_ids     → Step 45（OD-11 · Evidence Reusable）
```

→ 4 个方法均有契约与测试对应；**Orphan Critical Implementation = 0**。

---

## 7. Database Schema Traceability

`ai_ops` 表 → Step 映射（只读 catalog 核对）：

| 表 | Step |
| -- | ---- |
| `evidence_record` | 37 |
| `evidence_annotation_record` | 37 / 39 |
| `conversation` | 44（既有，Phase 4.1 Step 5） |
| `conversation_turn` | 44（既有） |
| `conversation_evidence` | 45 |

→ 无 Phase 4.1 未声明的新表；本阶段 **DB writes = 0**（仅只读 catalog 查询）。

---

## 8. Security Traceability

* Step 47 Security Matrix（15 边界 + 6 负例）全部 PASS；
* Step 48 复核：Evidence/Annotation 核心文件**零** `conversation_id` /
  `turn_id` / `assistant_request_id` / `provider_request_id` 污染；
* Read Model 仍 frozen、无 ORM/Session 泄漏；三 Repository 仍 ORM-only（无裸 SQL）；
* **RBAC / Multi-tenancy / Object-level Authorization = OUT_OF_SCOPE /
  FUTURE GOVERNANCE**（不计为 Phase 4.1 failure）。

---

## 9. Regression Results

| 集合 | 结果 |
| ---- | ---- |
| Traceability（本 Step） | 12 passed（带 DB）/ 11 passed + 1 skipped（无 DB） |
| DB Persistence 集合（6 文件） | 56 passed |
| Guard 集合（4 文件） | 148 passed |
| Full Regression（不带 DB） | **0 failed** |
| DB Regression（RUN_DB_TESTS=1） | 3 failed（**pre-existing**，见 §10） |
| Matrix Gate | Status **PASS** · Exit 0 |
| compileall | **OK** |
| Real LLM | SKIP（未调用 DeepSeek / SiliconFlow） |

---

## 10. Known Pre-existing Failures

```text
test_db_residue_is_zero
test_baseline_matches_step89_actual_execution
test_gate_passes_when_current_matches_baseline
```

* 仅在 `RUN_DB_TESTS=1` **全量**上下文出现；单独/隔离运行通过；
* Step 42–47 均存在，非本阶段引入；
* 分类：**PRE_EXISTING**；未修改 baseline、未 skip、未 xfail、未改这三个测试。

---

## 11. Traceability Gaps

| 项 | 分类 |
| -- | ---- |
| OD-12 turn-level reference | **DEFERRED** |
| Evidence Builder / 生产 AI Result → Evidence 映射 | **DEFERRED**（当前 test-composed） |
| RBAC / Multi-tenancy / ACL / Object-level Authorization | **OUT_OF_SCOPE** |
| Agent / MCP / Memory / Workflow / Chat UI / Streaming | **OUT_OF_SCOPE** |
| 3 个 DB baseline 测试 | **PRE_EXISTING** |

→ 无 `FROZEN` 但未 `TESTED` 的关键缺口。

---

## 12. Final Status

```text
Step 37–47 Contract Traceability = PASS（11/11）
Frozen Contract Drift            = 0
Unauthorized Production Module   = 0
Orphan Critical Implementation   = 0
Orphan Critical Test             = 0
DB Schema Unexpected Change      = 0
Security Regression              = 0
Matrix                           = PASS
Compile                          = PASS
Production DB Writes             = 0
Pre-existing DB baseline         = 3（记录，不阻塞）
```

**Step 48 = COMPLETE**
