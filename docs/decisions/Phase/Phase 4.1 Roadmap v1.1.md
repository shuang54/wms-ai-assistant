# Phase 4.1 Roadmap v1.1

> 建立原因：**Architecture Recovery**（非 Feature Change）。
> 触发：`Phase 4.1 Step 41–51 Architecture Recovery Audit` → `Roadmap Decision = CHANGE REQUIRED`。
> 版本规则：**Previous roadmap = No formally persisted v1.0 artifact found.**
> （`docs/architecture.md` 最新章节 = §8.78 / Phase 3.12 Step 100；Phase 4.1 Step 41+ 零条目；
> `docs/decisions/Phase/` 下无 Phase 4.1 roadmap 文件。不伪造 v1.0。）

---

## 一、Phase 4.1 总目标

```text
Conversation + Evidence Foundation
```

覆盖：

```text
Persistence · Provenance · Association · Idempotency · Transaction
Lifecycle · Conversation Integration · Security · Evaluation · Release
```

总原则：

* Persistence Foundation 仅由 **Repository** 承担（无 Service / 无 API / 无 Runtime 改动）；
* Evidence 是**评估资产（Evaluation Artifact）**，不是 Conversation 的附属对象；
* 每一步只解决一个边界，禁止跨 Step 顺手实现。

---

## 二、Step 37–40 历史事实（COMPLETE，已冻结）

| Step  | 主题                            | 状态      |
| ----- | ------------------------------- | --------- |
| 37    | Evidence / Annotation Persistence | COMPLETE  |
| 38    | Provenance Persistence            | COMPLETE  |
| 39    | Evidence ↔ Annotation Association | COMPLETE  |
| 40    | Transaction / Idempotency Hardening | COMPLETE |

### Step 37 — Evidence / Annotation Persistence

真实结果（PostgreSQL schema `ai_ops`）：

```text
ai_ops.evidence_record              （PK evidence_id）
ai_ops.evidence_annotation_record   （PK annotation_id）
```

Evidence 状态机（Step 36 §3/§5 冻结）**已随本 Step 落地**：

```text
IMPORTED → PERSISTED → ANNOTATED → REVIEWED → FINALIZED
```

由 `EVIDENCE_STATUS_TRANSITIONS` 强制：禁止跳级、`FINALIZED` 为终态；
`EvidenceRepository.update_status()` 已实现迁移校验（非法 → `InvalidEvidenceStatusTransitionError`）。

### Step 38 — Provenance Persistence

真实设计（**无独立 Provenance Table**）：

```text
Provenance = Evidence.dataset_version + Evidence.source_type
```

Read Model：`EvidenceProvenanceRow` / `EvidenceWithProvenance`（frozen、不泄漏 ORM/Session）。
`project_id` 仅为业务上下文，**不是** tenant / authorization boundary。

### Step 39 — Evidence ↔ Annotation Association

真实结果：

```text
Annotation.evidence_id ──FK──▶ Evidence.evidence_id   （ON DELETE CASCADE）
```

* 实测 `pg_constraint`：`evidence_annotation_record_evidence_id_fkey` 真实存在；
* `Cross-Evidence Isolation = PASS`；`Invalid evidence_id = REJECTED`；
* `case_id` **不是** Association Key（相同 case_id 可分属不同 Evidence）；
* **DB Schema Changes = 0**（FK 已于 Step 37 建立；未新增 ORM relationship）。

### Step 40 — Transaction / Idempotency Hardening

真实结果：

```text
Idempotency Key = (source_type, dataset_version)        ← 组合键
Repository      = first-write-wins（命中既有则原样返回）
DB              = UNIQUE(source_type, dataset_version)  ← uq_evidence_source_dataset
Transaction     = Repository owns（with factory() as session, session.begin():）
```

* 顺序重复 → `count = 1`；并发竞争 → DB UNIQUE 为最终边界；
* 事务内失败 → 整体 ROLLBACK，无半成品残留；
* **DB Schema Changes = 0 · Production Code Changes = 0**。

---

## 三、Phase 4.1 Roadmap v1.1 结构

```text
Phase 4.1
│
├── Persistence Foundation（COMPLETE）
│   ├── Step 37  Evidence / Annotation Persistence        COMPLETE
│   ├── Step 38  Provenance Persistence                  COMPLETE
│   ├── Step 39  Evidence ↔ Annotation Association       COMPLETE
│   └── Step 40  Transaction / Idempotency Hardening     COMPLETE
│
├── Lifecycle Foundation
│   ├── Step 41  Annotation Review Transition
│   ├── Step 42  Finalization Contract
│   └── Step 43  Lifecycle Consistency
│
├── Conversation Integration
│   ├── Step 44  Conversation ↔ Evidence Contract
│   ├── Step 45  Conversation ↔ Evidence Persistence
│   └── Step 46  Real Conversation Evidence E2E
│
└── Verification / Release
    ├── Step 47  Security / Boundary Audit
    ├── Step 48  Regression / Traceability
    ├── Step 49  Release Readiness
    ├── Step 50  Commit / Push / PR
    └── Step 51  Merge Verification
```

---

## 四、Step 41 — Annotation Review Transition（重新定义）

**废弃旧表述**：`Step 41 = Evidence Review State`。

废弃理由：`Evidence REVIEWED` 已存在；`IMPORTED → PERSISTED → ANNOTATED → REVIEWED → FINALIZED`
与 `EVIDENCE_STATUS_TRANSITIONS` 已在 Step 36 冻结、Step 37 实现。Step 41 若再"建立 Evidence Review State"
将造成重复实现。

**新定义**：

```text
Step 41 = Annotation Review Transition
```

目标：

```text
Annotation.review_status :  DRAFT → REVIEWED
```

必须解决：

```text
合法迁移 · 非法迁移 · Repository update method · 迁移失败语义
```

**明确不解决**：

```text
Evidence → REVIEWED        （属 Step 43）
Evidence → FINALIZED       （属 Step 42）
Conversation               （属 Step 44/45）
```

**禁止自行决定**「Evidence ANNOTATED → REVIEWED 是否要求所有 Annotation = REVIEWED」：
当前契约 **UNDEFINED**，该规则归 **Step 43** 冻结。

建议 `Database Evolution = 0`（`review_status` 列已存在，仅补 Repository 迁移逻辑与校验）。

---

## 五、Step 42 — Evidence Finalization Contract（重新定义）

`FINALIZED = terminal state` **已由 `EVIDENCE_STATUS_TRANSITIONS` 实现**，因此不构成 Step 42 的任务。

Step 42 真正任务 —— 冻结：

```text
FINALIZATION PRECONDITION
FINALIZATION TRANSITION
FINALIZATION POSTCONDITION
FINALIZED IMMUTABILITY
FORBIDDEN TRANSITIONS
```

必须回答：

```text
1. Evidence 什么时候可以 FINALIZED？
2. Annotation 是否必须全部 REVIEWED？
3. FINALIZED 后 Annotation 是否还能修改？
4. FINALIZED 后 Provenance 是否还能修改？
5. FINALIZED 后 Evidence status 是否只能保持 FINALIZED？
```

无充分证据 → 写 `UNDEFINED` + `Decision Required`，不得猜测。
（当前 1–4 均为 UNDEFINED；仅 5 已由 TRANSITIONS 支持：`FINALIZED → ()`。）

---

## 六、Step 43 — Evidence / Annotation Lifecycle Consistency

冻结 `Evidence.status` × `Annotation.review_status` 组合关系，形成 **Allowed State Matrix**：

```text
PERSISTED + DRAFT
ANNOTATED + DRAFT
ANNOTATED + REVIEWED
REVIEWED  + DRAFT
REVIEWED  + REVIEWED
FINALIZED + DRAFT
FINALIZED + REVIEWED
```

每个组合标注 `ALLOWED` / `FORBIDDEN` / `UNDEFINED`；
当前真实状态：**全部 UNCONSTRAINED**（Repository 不做任何组合校验）→ 需由 Step 43 冻结。

### Step 41–43 Dependency（Sequential）

```text
Step 41 → Step 42 → Step 43
```

* Step 43 需要知道 Annotation 能否迁移到 REVIEWED（Step 41）；
* Step 42 需要知道 Review 是否为 Finalization 前置条件（由 Step 43 冻结后回填确认）；
* Step 43 最终冻结两者组合关系。

---

## 七、Step 44 — Conversation ↔ Evidence Integration Contract

只负责（**Design Only**）：

```text
Relationship Contract · Ownership · Reference Semantics
Cardinality · Identity · Lifecycle Interaction
```

**不实现** DB / API / Runtime。

### R4 Decision Matrix（基于 Step 23 / 26 / 27 / 31 证据）

| Question                            | Decision  | 理由                                                                                      |
| ----------------------------------- | --------- | ----------------------------------------------------------------------------------------- |
| Conversation → multiple Evidence    | UNDEFINED | 无契约；Step 27 §十二 禁止 `conversation_id` 作为 Evidence identity，不能反推 ownership       |
| Evidence → multiple Conversation    | UNDEFINED | 无契约；未见任何 Evidence→Conversation 引用定义                                             |
| Evidence reusable                   | **DECIDED: Reusable** | ① 幂等键 `(source_type, dataset_version)` 为 **dataset 级**；② Step 23：dataset 级统计 `conversation_count / turn_count`，且不保存实际 conversation content；③ Step 27：Evidence identity = `case_id`，与 conversation 解耦；④ 当前 Conversation↔Evidence 零引用而 Evidence 完整可用 |
| Owner                               | **DECIDED: Evidence 自有（不被 Conversation 拥有）** | Step 27 §十二 / Production ID Isolation：conversation_id 等生产 ID 不得作为 Evidence identity、不得进入 Provenance |
| Reference                           | **DECIDED: REFERENCE（非 OWNERSHIP）** | 同上；且 Evidence 生命周期（IMPORTED→FINALIZED）与 Conversation 状态机无交集                 |

### 硬约束（来自 Step 27，Step 45 必须遵守）

```text
禁止：conversation_id / assistant_request_id / turn_id / provider_request_id
      → 作为 Evidence Artifact Identity
      → 进入 Provenance
```

→ **Step 45 不得在 `evidence_record` 上新增 `conversation_id` 列或 FK。**

### Candidate Data Model（仅候选，现在不建表）

1. **优先候选：No DB FK** —— Evidence 保持独立；Conversation 侧仅在运行时以只读方式引用
   `evidence_id`，不建约束（最符合 Production ID Isolation）。
2. **次选候选：关联表** `conversation_evidence(conversation_id, evidence_id)` ——
   仅在 Step 44 论证确需持久化关联时采用；仍不得把 conversation_id 写入 Evidence 本体。

---

## 八、Step 45 — Conversation ↔ Evidence Persistence

依据 Step 44 最终 Contract 实现：

```text
DB Model · FK · Association Table · Repository · Indexes · Constraints · Read Model
```

具体模型当前：**UNDEFINED**（待 Step 44）。
若采用关联表，需同时明确：cardinality、唯一约束、Read Model（frozen、不泄漏 ORM/Session）。

---

## 九、Step 46 — Real Conversation Evidence E2E

```text
Conversation → AI Runtime → Evidence → Conversation ↔ Evidence persistence → Read Back
```

验证重点：`Identity · Association · Isolation · Persistence · Read Back`。
**不重复测试** RAG / Tool / Text-to-SQL（除非 Integration 本身需要）。

---

## 十、Step 47 — Security / Boundary Audit

覆盖：`Evidence · Annotation · Provenance · Conversation · Conversation Evidence`。
增加：`Cross-Conversation Isolation · Cross-Evidence Isolation`。
仍然：**RBAC = NOT IN SCOPE · Multi-tenancy = NOT IN SCOPE**。
禁止泄漏：API Key / Password / DATABASE_URL / Connection String / Authorization /
Session / Connection / Engine / ORM / Raw Content / File Path。

---

## 十一、Step 48 — Regression / Traceability

建立 `Step → Contract → Implementation → Test → Evidence` 追溯矩阵，覆盖 **Step 37–47**，
并补齐 Step 39 / Step 40 此前缺失的 roadmap traceability。**不修改 Matrix baseline**。

---

## 十二、Step 49 — Release Readiness

检查：`Architecture · Tests · Security · Database · Migration · Rollback · Docs · Git · Secrets ·
Production DB Writes`。最终必须 `Production DB Writes = 0`。

---

## 十三、Step 50 — Commit / Push / PR

```text
Phase 4.1 branch → all checks green → Push → PR → Required Checks → Merge
```

现状：Step 37/38/39/40 各有 commit，仍 **not pushed / not PR / not merged**。本次不执行。

---

## 十四、Step 51 — Merge Verification

```text
origin/main → Phase 4.1 merge → Full Regression → Matrix → compileall → Architecture Verification
```

确认：`Phase 3 unaffected` · `Phase 4.1 complete`。

---

## 十五、Dependency Graph

```text
Step 37 → 38 → 39 → 40 → 41 → 42 → 43   【Sequential】Lifecycle 链
Step 44 → 45 → 46                        【Sequential】
Step 47                                  【Dependency: 46】
Step 48 / 49                             【Dependency: 41–47】
Step 50 → 51                             【Sequential】
```

**Parallel-eligible**：`{41–43}` 与 `{44–46}` 数据域不同，可并行推进；
但 46 若依赖 `FINALIZED` 语义，则 46 → 需待 42/43（**Conditional Dependency**）。

---

## 十六、Data Ownership Matrix

| 数据                              | Owner                                  | 创建 | 修改            | 最终化              | 查询 |
| --------------------------------- | -------------------------------------- | ---- | --------------- | ------------------- | ---- |
| Evidence                          | `EvidenceRepository`（唯一）           | ✔    | `status` only   | 迁移可用 / 规则 UNDEFINED | ✔ |
| Annotation                        | `EvidenceRepository`（唯一）           | ✔    | **无**（待 Step 41） | UNDEFINED       | by evidence_id |
| Provenance                        | Evidence 内嵌列（Step 38 冻结）        | 随 Evidence | **禁止**   | 随 Evidence         | ✔ |
| Conversation                      | ConversationRepository / Service（既有） | ✔  | ✔               | 既有 status         | ✔ |
| Conversation Evidence Reference   | **不存在**（待 Step 44/45）            | —    | —               | —                   | — |

---

## 十七、Service Boundary Matrix

| 能力                    | Repository      | Service     | Runtime     | API         |
| ----------------------- | --------------- | ----------- | ----------- | ----------- |
| Evidence CRUD           | ✔               | **Deferred** | —          | **Deferred** |
| Annotation CRUD         | ✔（无 update）  | **Deferred** | —          | **Deferred** |
| Review                  | 待 Step 41      | **Deferred** | —          | **Deferred** |
| Finalization            | 待 Step 42      | **Deferred** | —          | **Deferred** |
| Conversation Evidence   | 待 Step 45      | **Deferred** | 待 Step 44 | **Deferred** |

原则：**不因未来可能需要 Service，而现在创建 Service。**

---

## 十八、Database Evolution Matrix

| Step | New Table | New Column | New FK | New Index | Constraint |
| ---- | :-------: | :--------: | :----: | :-------: | :--------: |
| 37   | 2         | —          | 1      | 2         | 2 UNIQUE（含幂等键） |
| 38   | 0         | 0          | 0      | 0         | 0 |
| 39   | 0         | 0          | 0      | 0         | 0 |
| 40   | 0         | 0          | 0      | 0         | 0 |
| 41   | 0（建议） | 0          | 0      | 0         | 0 |
| 42   | 0（建议） | 0          | 0      | 0         | 0 |
| 43   | 0（建议） | 0          | 0      | 0         | 0 |
| 44   | 0（Design Only） | 0   | 0      | 0         | 0 |
| 45   | **待定**（0 或 1 关联表） | 待定 | 待定 | 待定 | 待定 |
| 46   | 0         | 0          | 0      | 0         | 0 |
| 47–51| 0         | 0          | 0      | 0         | 0 |

→ Step 41–44 建议 Schema = 0；**Step 45 是唯一可能的 DDL 点**。

---

## 十九、Security Boundary

* Read Model 全部 frozen，实测不含 Session / Connection / Engine / ORM 实例；
* 异常消息仅含约束与 key 描述，无凭据；
* `project_id` = 业务上下文，**不是** tenant / authorization boundary；
* Step 27 冻结的生产 ID 隔离（conversation_id / assistant_request_id / turn_id / provider_request_id）
  继续有效。

---

## 二十、Non-Goals（Phase 4.1 全程禁止）

```text
Agent · Multi-Agent · MCP · Workflow Engine · Memory · Planning · Autonomous Loop
RBAC · Multi-tenancy · Chat UI · Streaming · Long-term Memory · Tool marketplace
```

无 `Roadmap Change Required` 项。

---

## 二十一、Architecture Gaps

| ID  | Gap                                              | Impact                              | Required Step | Blocking |
| --- | ------------------------------------------------ | ----------------------------------- | ------------- | -------- |
| G-A | Annotation `review_status` 无迁移能力             | Review 无法落地                      | 41            | YES      |
| G-B | Evidence REVIEWED ⇄ Annotation REVIEWED 关系未定义 | 状态一致性无法保证                   | 43            | YES      |
| G-C | FINALIZED 后置不可变性未定义                      | Finalization 语义不完整              | 42            | YES      |
| G-D | Conversation ↔ Evidence 关系未设计                | Step 45 无法定模型                   | 44            | YES      |
| G-E | Evidence 无 Service / API                        | 仅 Repository 可调用                 | Deferred      | NO       |
| G-G | Step 39/40 未进入 roadmap 文档                    | 追溯链断裂                           | 48            | NO       |

---

## 二十二、G3 / G4 Status

```text
G3 = Inventory Query（requirements.md §G3）      BLOCKED
G4 = Purchase Order Query（requirements.md §G4） BLOCKED
```

Phase 4.1 Step 41–51 **不解锁 G3 / G4**（沿用 Step 22 / 27 / 31 / 32 结论）。

---

## 二十三、Document Drift（已记录，本次最小处理）

* **Drift 1**：`docs/architecture.md` 无 Phase 4.1 Step 41+ 条目（止于 §8.78 / Phase 3.12 Step 100）
  → 本次**不重写**架构文档；由 Step 51 前做最小同步（新增 Phase 4.1 小节 + 指向本文档）。
* **Drift 2**：`docs/requirements.md` 与本次冻结 Contract **无冲突**（G3/G4 定义未被改动）
  → 不修改。
* 判断依据：本次为 **execution roadmap**，按项目惯例落 `docs/decisions/Phase/`。

---

## 二十四、本次 Roadmap 冻结边界

```text
Production Code Changes = 0 · ORM Changes = 0 · DB Schema Changes = 0
Tests Added = 0 · Tests Modified = 0 · DB Writes = 0
Git Commit = 0 · Push = 0 · PR = 0 · Merge = 0
```

仅新增/更新 `docs/` 文档。

---

## 二十五、Freeze

```text
Phase 4.1 Roadmap v1.1 = FROZEN（条件冻结）
```

冻结范围：Step 37–40 历史事实、Step 41–43 重新定义、Step 44–51 定义、依赖图、
数据/服务/DB/安全边界、Non-Goals、Gaps、G3/G4。

**未冻结（明确留给后续 Step）**：Annotation Review 迁移规则细节、Finalization 五问答案、
Allowed State Matrix 的 ALLOWED/FORBIDDEN 判定、Conversation ↔ Evidence 具体数据模型。
