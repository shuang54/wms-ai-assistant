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

## 四、Finalization Contract（Step 42 任务，本次 UNDEFINED）

| # | 问题                                          | Decision    | 依据                                     |
| - | --------------------------------------------- | ----------- | ---------------------------------------- |
| 1 | Evidence 什么时候可以 FINALIZED？              | **UNDEFINED** | 无契约；仅知迁移 `REVIEWED → FINALIZED` |
| 2 | Annotation 是否必须全部 REVIEWED？             | **UNDEFINED** | 无契约；Step 41–51 Audit 明确禁止自行规定 |
| 3 | FINALIZED 后 Annotation 是否还能修改？         | **UNDEFINED** | 无契约（当前无 Annotation 更新方法，故实际不可改，但不构成契约） |
| 4 | FINALIZED 后 Provenance 是否还能修改？         | **UNDEFINED** | Step 38 已冻结 Provenance 不可改，但是否覆盖 FINALIZED 后场景未定义 |
| 5 | FINALIZED 后 Evidence status 是否只能保持 FINALIZED？ | **FROZEN: YES** | `EVIDENCE_STATUS_TRANSITIONS[FINALIZED] = ()` |

→ 1–4 标记 `Decision Required`，由 **Step 42** 冻结；**本次不填答案**。

---

## 五、Allowed State Matrix（Step 43 任务，本次记录真实状态）

真实状态说明：Repository 当前**不做任何** `Evidence.status × Annotation.review_status`
组合校验，因此所有组合在技术上均 **UNCONSTRAINED（当前不会被拒绝）**。

| Evidence  | Annotation | 当前实际       | 契约判定（待 Step 43） |
| --------- | ---------- | -------------- | ---------------------- |
| PERSISTED | DRAFT      | 允许（无校验） | **UNDEFINED**          |
| ANNOTATED | DRAFT      | 允许（无校验） | **UNDEFINED**          |
| ANNOTATED | REVIEWED   | 允许（无校验） | **UNDEFINED**          |
| REVIEWED  | DRAFT      | 允许（无校验） | **UNDEFINED**          |
| REVIEWED  | REVIEWED   | 允许（无校验） | **UNDEFINED**          |
| FINALIZED | DRAFT      | 允许（无校验） | **UNDEFINED**（应评估为 FORBIDDEN） |
| FINALIZED | REVIEWED   | 允许（无校验） | **UNDEFINED**          |

禁止猜测：上表"契约判定"列**不得**在 Step 43 之前被填充。

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

以上**禁止**在对应 Step 之前被实现或暗中固化。

---

## 九、本次冻结边界

```text
Production Code Changes = 0 · ORM Changes = 0 · DB Schema Changes = 0
Tests Added = 0 · Tests Modified = 0 · DB Writes = 0
Git Commit = 0 · Push = 0 · PR = 0 · Merge = 0
```

本文档只记录契约与 `UNDEFINED`，不修改任何实现。
