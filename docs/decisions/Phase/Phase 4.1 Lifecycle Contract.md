# Phase 4.1 Lifecycle Contract

> 依据：`Phase 4.1 Roadmap v1.1`（R1/R2）、`Phase 4.1 Step 41–51 Architecture Recovery Audit`。
> 原则：**只冻结当前能由架构证据支持的部分**；无证据处一律写 `UNDEFINED` 并标注
> `Decision Required`，**不自行设计业务规则**。

---

## 一、Evidence State Machine（已实现 / FROZEN）

来源：Step 36 §3/§5 冻结，Step 37 落地于
`backend/app/db/models/evidence_record.py`（`EVIDENCE_STATUS_VALUES` / `EVIDENCE_STATUS_TRANSITIONS`）。

```text
IMPORTED → PERSISTED → ANNOTATED → REVIEWED → FINALIZED
```

| Current   | Event        | Next      | 状态     |
| --------- | ------------ | --------- | -------- |
| IMPORTED  | persist      | PERSISTED | FROZEN   |
| PERSISTED | annotate     | ANNOTATED | FROZEN   |
| ANNOTATED | review       | REVIEWED  | FROZEN   |
| REVIEWED  | finalize     | FINALIZED | FROZEN   |
| FINALIZED | —            | —         | 终态     |

强制规则（真实代码支持）：

```text
1. 禁止跳级（TRANSITIONS 白名单）
2. FINALIZED 为终态（TRANSITIONS[FINALIZED] = ()）
3. Repository.update_status() 执行校验，非法迁移 → InvalidEvidenceStatusTransitionError
4. 无记录 → EvidenceNotFoundError
```

**本 Step 不重新设计该状态机。**

---

## 二、Annotation Review State（值域存在 / 迁移 UNDEFINED）

来源：`backend/app/db/models/evidence_annotation_record.py`。

```text
ANNOTATION_REVIEW_VALUES = (DRAFT, REVIEWED)
```

当前真实能力：

```text
review_status 仅在 create_annotation() 时给定（默认 DRAFT）
无 TRANSITIONS 常量      → UNDEFINED
无 Repository 迁移方法   → UNDEFINED（Gap G-A）
```

**Step 41 已实现并冻结**（Repository：`EvidenceRepository.update_annotation_review_status`）：

| Current  | Event  | Next     | 状态                         |
| -------- | ------ | -------- | ---------------------------- |
| DRAFT    | review | REVIEWED | **FROZEN: ALLOWED**（Step 41） |
| REVIEWED | revert | DRAFT    | **FROZEN: FORBIDDEN**（Step 41） |
| REVIEWED | review | REVIEWED | **FROZEN: FORBIDDEN**（Step 41，重复评审） |
| DRAFT    | —      | 未知状态（如 APPROVED） | **FROZEN: FORBIDDEN**（Step 41） |

* `REVIEWED` = **终态**（`ANNOTATION_REVIEW_TRANSITIONS[REVIEWED] = ()`）；
* 契约常量：`ANNOTATION_REVIEW_TRANSITIONS`（`models/evidence_annotation_record.py`）；
* 未扩展状态值域（无 APPROVED / REJECTED / FINALIZED / ARCHIVED / CANCELLED），
  未新增 `reviewed_at` / `reviewer_id` / `review_comment` 等字段。

---

## 三、Transition Rules（冻结范围）

### Evidence（FROZEN）

```text
合法   = EVIDENCE_STATUS_TRANSITIONS[current] 白名单内的迁移
非法   = 跳级 / 越级 / 从 FINALIZED 出发的任何迁移
失败   = InvalidEvidenceStatusTransitionError（typed，非静默）
无记录 = EvidenceNotFoundError
事务   = Repository owns（with factory() as session, session.begin():）
```

### Annotation（Step 41 已 FROZEN）

```text
合法迁移   = DRAFT → REVIEWED（唯一）
非法迁移   = REVIEWED → DRAFT · REVIEWED → REVIEWED · 任何未知状态
失败语义   = InvalidAnnotationReviewTransitionError（typed，继承自
             EvidenceRepositoryError）；Annotation 不存在 → AnnotationNotFoundError
验证顺序   = 先验证迁移 → 后写入（禁止先 UPDATE 再验证；失败时 DB 状态不变）
事务边界   = Repository owns（with factory() as session, session.begin():）
联动禁止   = 不推进 Evidence 状态（PERSISTED→ANNOTATED / ANNOTATED→REVIEWED 属 Step 43）
字段隔离   = 仅 review_status + updated_at 可变；evidence_id / case_id /
             annotation_version / annotator_id / created_at 保持不变
```

### 跨实体触发规则（UNDEFINED → Step 43）

```text
Annotation 全部 REVIEWED
    ⇒ 是否允许 / 触发 Evidence ANNOTATED → REVIEWED ？        UNDEFINED
Evidence FINALIZED
    ⇒ 是否阻止任何 Annotation 写入 ？                          UNDEFINED（Step 42）
```

---

## 四、Finalization Contract（Step 42：审计 + 冻结）

### Status

```text
PARTIALLY FROZEN
```

已冻结部分来自**现有代码证据**；无证据处保持 `UNDEFINED`（**不猜测、不代为决定**）。
证据来源：真实 PostgreSQL 行为审计 `tests/test_evidence_finalization_contract_db.py`（13 passed）。

### Transition

```text
REVIEWED → FINALIZED            FROZEN: ALLOWED（唯一入边）
```

### Preconditions（Q1）

```text
契约判定：UNDEFINED（decision required）
```

实测事实（`test_01` / `test_02`）：

```text
Annotation 全部 DRAFT  → REVIEWED → FINALIZED 成功
Annotation 全部 REVIEWED → REVIEWED → FINALIZED 成功
```

→ 当前实现**不存在**任何 Finalization 前置条件校验；但"是否应当要求"**无契约依据**，
故本项保持 `UNDEFINED`，不自行规定为 A（要求）或 B（不要求）。

### Annotation Review Requirement（Q2）

```text
契约判定：UNDEFINED（A / B 均不可由现有架构推出）
```

* 情况 A（要求全部 REVIEWED）：无代码 / 测试 / 历史决策支持；
* 情况 B（不影响 Finalization）：亦无契约声明；
* 现有事实仅能证明"当前不校验"，**不等于**契约选择 B。
→ 保持 `UNDEFINED`；与 `Evidence → REVIEWED` 前置条件一并归 **Step 43**（完整状态矩阵）。

### Postconditions / Immutability（Q3）

| 对象                                              | 契约判定    | 证据                                                        |
| ------------------------------------------------- | ----------- | ----------------------------------------------------------- |
| Evidence `status` 保持 FINALIZED                   | **FROZEN**  | `EVIDENCE_STATUS_TRANSITIONS[FINALIZED] = ()`（`test_03`）   |
| `dataset_version` / `source_type`                  | **FROZEN: 不可改** | Repository **无**任何修改方法（`test_07`）            |
| `de_identification_attested` / `_method`           | **FROZEN: 不可改** | 同上（无更新入口）                                    |
| Annotation 新建                                    | **UNDEFINED** | observed：当前**允许**（`test_05`）→ 无 enforcement      |
| Annotation `DRAFT → REVIEWED`                      | **UNDEFINED** | observed：当前**允许**（`test_06`）→ 无 enforcement      |
| Annotation `annotation_version` / `annotator_id` / `case_id` | **FROZEN: 不可改** | Repository 无字段级更新方法（仅 review_status 可迁移） |

→ `test_05` / `test_06` 锁定的是 **observed behavior**，不是契约主张；
不可变性契约仍 `UNDEFINED`，留给后续 Step（需 Roadmap 决策）。

### Provenance Requirement（Q6）

```text
FROZEN: FINALIZED 后 Provenance 不可修改
```

依据：Provenance = Evidence 内嵌的 `dataset_version + source_type`（Step 38 冻结，无独立表）；
Repository 无任何 Provenance 写入方法（`test_07`）；FINALIZED 前后读回一致（`test_08`）。
→ 该结论由"无写入入口"这一**架构事实**支持，而非推测。

### Trigger Ownership（Q4）

```text
契约判定：UNDEFINED
```

* 不存在 `finalize()` / `finalize_evidence()` / `mark_finalized()` 专用入口（`test_09`）；
* 不存在 Finalization Service / API / Workflow；
* 当前唯一触发路径 = 通用 `EvidenceRepository.update_status(id, FINALIZED)`；
* Repository 是 persistence boundary，**不是**业务触发者 → 业务 owner 未定义。
→ 记录 `Finalization trigger owner = UNDEFINED`；**不提前创建 Service**。

### Forbidden Transitions（Q5）

```text
FINALIZED → IMPORTED    FORBIDDEN
FINALIZED → PERSISTED   FORBIDDEN
FINALIZED → ANNOTATED   FORBIDDEN
FINALIZED → REVIEWED    FORBIDDEN
FINALIZED → FINALIZED   FORBIDDEN（不是新的合法业务 transition）
```

全部经 `InvalidEvidenceStatusTransitionError` 拒绝（`test_03` 参数化覆盖 5 条）。
不新增 `ARCHIVED` / `CANCELLED` / `REOPENED` / `APPROVED` / `REJECTED` 状态。

### Open Decisions

| ID   | Open Decision                                          | 归属     |
| ---- | ------------------------------------------------------ | -------- |
| OD-3 | Finalization Precondition（是否要求 Annotation 全 REVIEWED） | 后续 Step（与 Step 43 矩阵一并决定） |
| OD-4 | FINALIZED 后 Annotation 新建 / review 是否禁止           | 后续 Step |
| OD-7 | Finalization 业务触发者（Service / API / 人工）          | 后续 Step |

以上**禁止**在对应决策落地前被实现或暗中固化。

---

## 五、Evidence × Annotation Lifecycle Consistency（Step 43）

### 1. Independent State Machines

```text
Evidence   : IMPORTED → PERSISTED → ANNOTATED → REVIEWED → FINALIZED（FINALIZED 终态）
Annotation : DRAFT → REVIEWED（REVIEWED 终态）
```

两者**不合并**为单一状态机；只定义"组合合法性"与"跨对象联动"。

### 2. State Combination Matrix（Observed vs Contract）

> Observed = 真实 PostgreSQL 实测；Contract = 契约判定。
> **"当前代码未阻止" ≠ "业务允许"** —— 两列严格分离。

| Evidence  | Annotation | Observed                          | Contract Evidence                                    | Decision |
| --------- | ---------- | --------------------------------- | ---------------------------------------------------- | -------- |
| IMPORTED  | DRAFT      | **可达**（`test_02`：不推进）      | Step 37 未规定 IMPORTED 场景                          | **UNDEFINED** |
| PERSISTED | DRAFT      | **不可达**（`test_01`：立即推进 ANNOTATED） | Step 37 决策文档 §Transaction 推进契约 + TRANSITIONS | **FORBIDDEN**（不可达） |
| PERSISTED | REVIEWED   | **不可达**（同上；review 须先有 annotation） | 同上                                          | **FORBIDDEN**（不可达） |
| ANNOTATED | DRAFT      | 可达（Step 41 `_create_annotation`） | Step 37 §Transaction：annotation 创建后 Evidence=ANNOTATED、初始 DRAFT | **FROZEN** |
| ANNOTATED | REVIEWED   | 可达（Step 41 `test_07`）          | Step 41 契约：review 不联动 Evidence                  | **FROZEN** |
| REVIEWED  | DRAFT      | 可达（Step 42 `_create_reviewed`） | 无契约：是否要求"全部 Annotation REVIEWED"未定义（OD-8） | **UNDEFINED** |
| REVIEWED  | REVIEWED   | 可达（Step 42 `test_02`）          | 无契约（同 OD-8）                                     | **UNDEFINED** |
| FINALIZED | DRAFT      | 可达（Step 42 `test_01`）          | OD-4 / OD-10 未决                                     | **UNDEFINED** |
| FINALIZED | REVIEWED   | 可达（Step 42 `test_02`）          | OD-3 未决                                             | **UNDEFINED** |

矩阵结论：`Lifecycle Matrix = PARTIAL`（3 项 FROZEN / 2 项 FORBIDDEN / 4 项 UNDEFINED）。

### 3. Cross-object Transition Rules

| # | 跨对象问题                                        | Observed                                   | Contract | Open |
| - | ------------------------------------------------- | ------------------------------------------ | -------- | ---- |
| 1 | Annotation `DRAFT → REVIEWED` 是否推进 Evidence → REVIEWED | **不推进**（`test_03`，Evidence 保持 ANNOTATED） | **UNDEFINED** | OD-9 |
| 2 | Evidence `REVIEWED → FINALIZED` 是否要求全部 Annotation REVIEWED | **不要求**（Step 42 `test_01`：全 DRAFT 也可 FINALIZED） | **UNDEFINED** | OD-3 / OD-8 |
| 3 | Evidence `REVIEWED` 后是否禁止新增 Annotation      | 无 enforcement（未阻止）                    | **UNDEFINED** | OD-10 |
| 4 | Evidence `FINALIZED` 后是否禁止创建 / 修改 Annotation | 无 enforcement（Step 42 `test_05/06`：当前允许） | **UNDEFINED** | OD-4 / OD-10 |
| 5 | Annotation 创建是否推进 Evidence → ANNOTATED        | **推进**（PERSISTED 时；IMPORTED 时不推进）  | **FROZEN**（Step 37 §Transaction） | — |

### 4. Observed vs Contract（核心纪律）

```text
Repository 没有 "if evidence.status == FINALIZED: reject"
  ⇒ 只能证明 Observed = allowed by implementation
  ⇒ 不能证明 Contract = allowed
```

因此本文件中所有 `observed` 前缀测试（Step 42 `test_05/06`、Step 43 `test_02/03`）
**只锁定实现事实**，一旦契约变更需随之更新；它们不构成业务许可。

### 5. Frozen Decisions

```text
ANNOTATED + DRAFT      FROZEN（Step 37 §Transaction）
ANNOTATED + REVIEWED   FROZEN（Step 41：review 不联动）
Annotation 创建推进 Evidence → ANNOTATED（PERSISTED 时）  FROZEN（Step 37 §Transaction）
```

### 6. Forbidden Decisions

```text
PERSISTED + DRAFT      FORBIDDEN（不可达：创建 annotation 立即推进 ANNOTATED）
PERSISTED + REVIEWED   FORBIDDEN（不可达，同上）
FINALIZED → 任何状态    FORBIDDEN（Step 42）
REVIEWED  → DRAFT      FORBIDDEN（Annotation，Step 41）
```

### 7. Undefined / Open Decisions

```text
IMPORTED  + DRAFT      UNDEFINED（observed 可达；Step 37 未规定 IMPORTED 场景）
REVIEWED  + DRAFT      UNDEFINED（OD-8）
REVIEWED  + REVIEWED   UNDEFINED（OD-8）
FINALIZED + DRAFT      UNDEFINED（OD-4 / OD-10）
FINALIZED + REVIEWED   UNDEFINED（OD-3）
```

新增 Open Decision（与 §八 表一致）：

| ID   | Open Decision                                        | 归属     |
| ---- | ---------------------------------------------------- | -------- |
| OD-8 | Evidence REVIEWED 是否要求所有 Annotation REVIEWED      | 后续 Step |
| OD-9 | Annotation REVIEWED 是否自动推进 Evidence REVIEWED      | 后续 Step |
| OD-10 | FINALIZED 是否禁止创建 / 修改 Annotation                | 后续 Step |

### 8. Future Enforcement（Deferred，不实现）

```text
Implementation Deferred:
Lifecycle enforcement（组合校验 / 跨对象联动 / FINALIZED 后不可变性）
should be implemented in a future service/repository after contract freeze。
```

* 本 Step **未**创建 EvidenceService / AnnotationService / FinalizationService / API；
* 未新增表 / 列 / 索引 / 约束 / 外键；
* 未修改 EvidenceRepository / ORM（矩阵中 FROZEN 项已由既有实现满足）。

---

## 六、Forbidden State Matrix（部分 FROZEN）

**已 FROZEN（有代码证据）**：

```text
Evidence: 任何跳级迁移            → InvalidEvidenceStatusTransitionError
Evidence: FINALIZED → 任何状态    → 禁止（终态）
Evidence: 状态值越界              → validate_evidence_status 拒绝
```

**UNDEFINED（待 Step 41/42/43）**：

```text
Annotation: REVIEWED → DRAFT（回退）               FROZEN: 禁止（Step 41）
Annotation: REVIEWED → REVIEWED（重复评审）         FROZEN: 禁止（Step 41）
Annotation: 未知状态（如 APPROVED）                 FROZEN: 禁止（Step 41）
Annotation: 同 (evidence_id, case_id, annotation_version) 重复  已有 UNIQUE 约束（FROZEN）
Evidence FINALIZED 状态下新增 / 修改 Annotation     UNDEFINED
Evidence FINALIZED 状态下修改 Provenance            UNDEFINED
Review 前置条件未满足时 Evidence → REVIEWED         UNDEFINED
```

---

## 七、Immutability

**已 FROZEN**：

```text
Read Model（EvidenceRow / AnnotationRow / EvidenceWithProvenance / EvidenceProvenanceRow）
  = frozen dataclass，不得原地修改
Provenance（dataset_version / source_type）= Step 38 冻结，不可修改
Revision 语义（Step 28）= 产生新对象，旧对象保持不变
```

**UNDEFINED**：

```text
FINALIZED Evidence 的 Annotation 不可变性        UNDEFINED（Step 42）
FINALIZED Evidence 本身的字段不可变性            UNDEFINED（Step 42）
Annotation REVIEWED 后是否不可变                 UNDEFINED（Step 41）
```

---

## 八、Open Architecture Decision（必须显式记录）

| ID   | Open Decision                                              | 归属 Step |
| ---- | ---------------------------------------------------------- | --------- |
| OD-1 | ~~Annotation `review_status` 合法/非法迁移与失败语义~~ **CLOSED（Step 41）** | 41 |
| OD-2 | ~~`REVIEWED` 是否为 Annotation 终态~~ **CLOSED（Step 41）：是，终态** | 41 |
| OD-3 | Finalization Precondition（是否要求全部 Annotation REVIEWED） | 42        |
| OD-4 | FINALIZED 后 Annotation / Provenance 不可变性               | 42        |
| OD-5 | Allowed State Matrix 的 ALLOWED / FORBIDDEN 判定            | 43        |
| OD-6 | Conversation ↔ Evidence 数据模型（No FK vs 关联表）          | 44 → 45   |
| OD-7 | Finalization 业务触发者（Service / API / 人工）               | 后续 Step |
| OD-8 | Evidence REVIEWED 是否要求所有 Annotation REVIEWED             | 后续 Step |
| OD-9 | Annotation REVIEWED 是否自动推进 Evidence REVIEWED             | 后续 Step |
| OD-10 | FINALIZED 是否禁止创建 / 修改 Annotation                      | 后续 Step |

以上**禁止**在对应 Step 之前被实现或暗中固化。

---

## 九、本次冻结边界

```text
Production Code Changes = 0 · ORM Changes = 0 · DB Schema Changes = 0
Tests Added = 0 · Tests Modified = 0 · DB Writes = 0
Git Commit = 0 · Push = 0 · PR = 0 · Merge = 0
```

本文档只记录契约与 `UNDEFINED`，不修改任何实现。

---

## 六、Conversation ↔ Evidence Integration Contract（Step 44）

> Step 44 交付物 = **Contract**，不是 Implementation。
> **未建表 / 未加列 / 未加 FK / 未加 Service / 未加 API / 未接 Runtime。**

### 1. Domain Objects（真实代码）

```text
Conversation      : conversation_id · project_id · status(ACTIVE / ARCHIVED) · created_at · updated_at
ConversationTurn  : turn_id · conversation_id(FK → conversation) · role · content
                    assistant_request_id（correlation，**非 FK**、非 Evidence identity）· created_at
Evidence          : evidence_id · dataset_version · source_type · de_identification_*
                    status(IMPORTED/PERSISTED/ANNOTATED/REVIEWED/FINALIZED) · created_at · updated_at
Annotation        : annotation_id · evidence_id(FK → evidence_record) · case_id · annotation_version
                    annotator_id · review_status(DRAFT/REVIEWED) · created_at · updated_at
```

实测（本 Step）：Conversation 侧**无** evidence_id；Evidence/Annotation 侧**无**
conversation_id / turn_id / assistant_request_id / provider_request_id。

### 2. Ownership（Q1）

```text
FROZEN: Evidence = Independent Domain Object
        Conversation ≠ Evidence Owner
```

依据（三重）：

1. Step 27 §十二 Artifact Identity 明确禁止 conversation_id 等作为 Evidence identity；
2. Evidence 生命周期（IMPORTED→…→FINALIZED）与 Conversation 生命周期（ACTIVE/ARCHIVED）
   值域互不相交、互不引用；
3. Evidence 幂等键为 **dataset 级** `(source_type, dataset_version)`，与单次会话无关。

### 3. Reference Semantics（Q4）

```text
FROZEN: Conversation → Evidence = REFERENCE（非 OWNERSHIP）
FROZEN: no cascade lifecycle coupling
```

```text
Conversation         ──references──▶  Evidence
（不是 owns；不构成 identity；不构成 provenance）
```

无契约规定任何联动 → 按 §十 默认：不级联删除、不级联状态变更。

### 4. Cardinality（Q2 / Q3）

```text
Observed  = 0（当前不存在任何持久化关联）
Contract  = UNDEFINED  → OD-11
Recommended（**非 FROZEN**，仅供 Step 45 参考）
  Conversation → Evidence : 0..N
  Evidence → Conversation : 0..N（由 Evidence = Reusable 推论）
```

判定说明：业务直觉（一次会话可产生 RAG / Tool / SQL 多类 Evidence）**不构成契约**；
当前数据库亦无任何关联可观测 → 如实记录 `UNDEFINED`，不因"看起来应该 0..N"而写 FROZEN。

### 5. Identity Boundary（Q5）

```text
FROZEN（Step 27 + Step 31 + 本 Step 测试）
```

| 身份                  | 用途                    | 禁止混淆                         |
| --------------------- | ----------------------- | -------------------------------- |
| `conversation_id`     | Conversation identity   | ≠ evidence_id · ≠ Evidence provenance |
| `turn_id`             | Turn identity           | ≠ evidence_id · ≠ Evidence provenance |
| `assistant_request_id`| 请求 correlation（非 FK）| ≠ evidence_id · ≠ Evidence provenance |
| `evidence_id`         | Evidence identity       | ≠ conversation_id                |
| `case_id`             | Evidence 内样本标识      | ≠ conversation_id（Step 27）      |
| `dataset_version` + `source_type` | Evidence provenance | **不得**含任何会话 ID（Step 27/38） |

测试锁定：`tests/test_conversation_evidence_contract.py`（8 passed，离线）。

### 6. Association Model Candidates（Q7）

| Candidate | 模型                                    | 评估                                                                 | 结论 |
| --------- | --------------------------------------- | -------------------------------------------------------------------- | ---- |
| A         | `evidence_record.conversation_id` / `conversation.evidence_id` | **违反 Step 27**（会话 ID 进入 Evidence 本体）；且无法表达 0..N | **REJECTED** |
| B         | 关联表 `conversation_evidence(conversation_id, evidence_id, …)` | 可表达 0..N × 0..N；保持两边模型干净（不污染 Evidence identity） | **RECOMMENDED（未实现）** |
| C         | 无持久化关联（runtime reference only）   | 无法满足 Step 46 要求的 persistent read-back                          | **INSUFFICIENT** |

若未来采用 B，其语义是 **Evidence Reference**（不是 ownership）。

### 7. Turn-level Reference

```text
UNDEFINED → OD-12
```

方案 1（仅 Conversation ↔ Evidence）/ 方案 2（仅 Turn ↔ Evidence）/ 方案 3（两者）
的可追溯性取舍留待决策；本 Step **不新增字段**、不修改 `conversation_turn`。
当前事实：`assistant_request_id` 已存在于 turn 上并可作为 correlation，
但它**不是** Evidence identity，也不构成关联约束。

### 8. Lifecycle Interaction（Q6）

```text
FROZEN: NO automatic Evidence creation（Conversation 创建 / Turn 创建均不创建 Evidence）
FROZEN: Evidence 创建不得因运行时存在 conversation_id 而写入 Evidence
FROZEN: Evidence ANNOTATED → REVIEWED 不改变 Conversation.status
FROZEN: Evidence REVIEWED → FINALIZED 不 close / complete / archive Conversation
FROZEN: Annotation review 不改变 Conversation.status
```

依据：当前实现无任何跨写入（实测 Conversation repository/service 零 evidence 引用）；
且无契约规定联动 → 按 §十二 记为 FROZEN（无自动行为）。

### 9. Evidence Reuse Semantics

```text
FROZEN: Evidence = Reusable Enterprise / Evaluation Artifact（Roadmap v1.1 §七 R4）
```

```text
Evidence E
   ├── Conversation A
   ├── Conversation B
   └── Conversation C        ← 合法架构模型（未来采用关联表时）
```

推论：Conversation 删除**不得**级联删除 Evidence（其他 Conversation 可能仍引用）。

### 10. Delete Semantics（§二十 表）

| 操作                          | 默认契约                        |
| ----------------------------- | ------------------------------- |
| Delete Conversation           | **不删除** Evidence             |
| Delete Evidence               | **不删除** Conversation         |
| Conversation status change    | **不自动改变** Evidence.status  |
| Evidence status change        | **不自动改变** Conversation.status |
| Annotation review             | **不自动改变** Conversation.status |
| Evidence finalization         | **不自动完成 / 关闭 / 归档** Conversation |

全部判定：`FROZEN = no cascade`（依据 Reference ≠ Ownership + 当前零联动实现）。

### 11. Conversation Isolation（未来安全边界）

```text
Conversation A 只能读取其引用的 Evidence；
不得因 Evidence B 存在于库中就被 Conversation A 读取。
```

* 验证责任：**Step 46（E2E）/ Step 47（Security Audit）**；
* `project_id` 仍为业务上下文，**不是** tenant / authorization boundary；
* **RBAC / Multi-tenancy = OUT OF SCOPE**（本 Step 不实现权限系统）。

### 12. Future Persistence Contract（Step 45 输入）

```text
若采用 Candidate B（推荐，未实现）：
  conversation_evidence(conversation_id, evidence_id, …)
  · 语义 = Evidence Reference（非 ownership）
  · 不得把 conversation_id 写入 evidence_record（Step 27 硬约束）
  · 需明确：cardinality（OD-11）· turn-level（OD-12）· duplicate 语义（OD-13）
  · Read Model 必须 frozen、不泄漏 Session / Connection / Engine / ORM
```

### 13. Step 45 Boundary

```text
Step 45 = Conversation ↔ Evidence Persistence（实现）
前置：本 Contract + OD-11 / OD-12 / OD-13 的决策
范围：DB Model · FK · 关联表 · Repository · Indexes · Constraints · Read Model
禁止：Service / API / Runtime 接线
```

### 14. Step 46 Boundary

```text
Step 46 = Real Conversation Evidence E2E
链路：Conversation → AI Runtime → AI Result → Evidence → 关联持久化 → Read-back
验证：identity · association · isolation · persistence · read-back
不重测：RAG / Tool / Text-to-SQL（除非集成本身需要）
```

### 15. Open Decisions

| ID    | Open Decision                                            | 归属     |
| ----- | -------------------------------------------------------- | -------- |
| OD-11 | Conversation ↔ Evidence 关联基数（0..N × 0..N 是否确认）    | Step 45 前 |
| OD-12 | 是否需要 Turn-level（ConversationTurn ↔ Evidence）引用     | Step 45 前 |
| OD-13 | 重复引用语义（幂等唯一 / 允许重复 / 以 turn_id 区分）        | Step 45 前 |

以上均**未**在本 Step 关闭；Step 44 不创建 `conversation_evidence`、不创建 Service / API。
