# Phase 4.1 Step 30 — Annotation Evidence Finalization

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Contract only**（Finalization / Integrity Gate；不实现真实 G3 Evidence）
- 前置：Step 24（Rubric）+ Step 25（Procedure/Gate）+ Step 28（Workflow）+ Step 29（Review）

---

## 1. Purpose

```text
Reviewed Annotation
        ↓
Finalization / Integrity Check
        ↓
AnnotationEvidenceIntegrityResult
        ↓
G3 Evidence Input Contract（未来）
```

解决一个问题：

> 什么样的 AnnotatedEvidence 才能被认为是"完整、版本一致、Review 状态合法、
> 可以进入未来 G3 Evidence Gate"的候选证据？

结果契约（test-local）：

```text
AnnotationEvidenceIntegrityResult
    valid
    dataset_version
    source_type
    cases_checked
    cases_finalized
    blocked_cases
    failure_codes（少量结构化代码）
```

---

## 2. Integrity Rules

### dataset_version

```text
全 case 的 dataset_version 必须与 evidence.dataset_version 一致；
不一致 → DATASET_VERSION_MISMATCH（禁止自动 convert / coerce / upgrade / downgrade）。
```

### annotation_version

```text
* 允许每 case 不同（annotation revision identity ≠ dataset version）；
* 同一 case 只允许**一个**有效 REVIEWED 版本；
* 同 case 出现多个 REVIEWED 版本 → DUPLICATE_REVIEWED_VERSION
  （禁止 max(annotation_version) 选 winner）。
```

### review_status

```text
只有 REVIEWED 可以 Finalize；
DRAFT / DISPUTED → UNREVIEWED_CASE（不能 DRAFT → Finalized / DISPUTED → Finalized）。
```

### dependency

```text
保持 Step 24 五维结构；至少一个 True（全 False → INVALID_DEPENDENCY）；
不得出现未知字段与 score / confidence / weight / probability（→ FORBIDDEN_FIELD）。
```

### impact

```text
保持四维 true / false / unknown；
unknown 保持 unknown（不等于 missing，不归一化为 false）。
```

### reference

```text
只允许 FULL_HISTORY / DOMAIN_EXPERT_VALIDATED；
LLM_GENERATED / INFERRED / AUTO_SELECTED → INVALID_REFERENCE。
```

### business outcome

```text
只允许 6 类（ANSWER_CONTENT / TOOL_SELECTION / TOOL_ARGUMENTS /
SQL_SEMANTICS / ROUTE / REFUSAL）；
SUCCESS / FAILED / EMPTY / LLM_ERROR / RAG_ERROR / TOOL_ERROR → INVALID_BUSINESS_OUTCOME。

注意：Assistant Outcome ≠ Business Outcome。
```

### review state / independence

```text
AGREEMENT → review_status = REVIEWED（domain_review_required = false）；
DISAGREEMENT → 必须有 DOMAIN_REVIEW_REQUIRED 证据与最终 REVIEWED；
DISAGREEMENT 但无 Domain Review → REVIEW_STATE_MISMATCH；
带 Domain Review 的 case 必须 annotator_1 != annotator_2
（否则 ANNOTATOR_NOT_INDEPENDENT；Step 29 independence contract 不放宽）。
```

### raw dataset

```text
Finalized Evidence 不复制 conversation / turns / content / full history /
candidate history；只通过 dataset_version + case_id 引用 Raw Evidence。
```

---

## 3. Block Conditions

主要 BLOCKED 条件（failure codes，少量结构化）：

```text
DATASET_VERSION_MISMATCH       case 与 evidence 版本不一致（或 raw artifact 不一致）
UNREVIEWED_CASE                DRAFT / DISPUTED
DUPLICATE_REVIEWED_VERSION     同 case 多个 REVIEWED annotation_version
INCOMPLETE_CASE                缺 dependency / reference / impact / review
INVALID_REFERENCE              非法 reference_type
INVALID_DEPENDENCY             全 False / 非法结构
INVALID_IMPACT                 非法 impact 值
INVALID_BUSINESS_OUTCOME       非法 business outcome
REVIEW_STATE_MISMATCH          DISAGREEMENT 无 Domain Review
ANNOTATOR_NOT_INDEPENDENT      DISAGREEMENT case 的 annotator_1 == annotator_2
FORBIDDEN_FIELD                prompt / SQL / raw LLM response / API key /
                               embedding / score / confidence / weight / probability 等
```

不自动填 `unknown` / `false` / `None`；不建立复杂 error taxonomy（仅上述 11 个代码）。

统计口径（integrity statistics，**不是** quality score）：

```text
cases_checked · cases_finalized · cases_blocked
```

禁止：accuracy / score / quality score / winner / ranking。

---

## 4. G3 Relationship

```text
Finalization PASS
    ≠
G3 Evidence READY
```

因为：

```text
G1 BLOCKED
```

即：

```text
Synthetic Evidence → all REVIEWED → Finalization PASS
        ↓
G3 Evidence 仍 = BLOCKED
```

原因：

```text
synthetic procedure validation  ≠  real evidence readiness
```

Step 25 的 Gate 语义不改变；本阶段也不实现真实 G3 Evidence。

---

## 5. Synthetic Limitation

```text
Synthetic fixtures only
No real WMS annotation
```

```text
* fixture（annotation_finalization_cases.yaml）为 12 场景 synthetic 定义；
* 不含真实客户 / 供应商 / 订单 / 库存 / 员工 / 生产数据；
* 不是 raw evidence artifact（不含 conversation content）；
* 所有 PASS 只代表"procedure / integrity 契约自洽"，
  不代表任何真实 WMS 证据可用。
```

---

## 6. Production Isolation

```text
Production Code = 0
```

```text
* backend/app/**/*.py 不含 annotation_finalization / annotation_review /
  annotation_comparison / domain_review / annotator 模块；
* 不含 AnnotationEvidenceIntegrityResult / CaseFinalizationMetadata /
  finalize_annotated_evidence 等实现；
* 生产模块不 import tests / docs / evaluation；
* Conversation / Chat / AIOrchestrator / RAG / Tool / TextToSQL 运行时文件
  无 annotation / annotator / domain_review 语义；
* 未新增数据库表 / Migration / Annotation API / Annotation Storage。
```

当前 Gate 状态：

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED
```

---

## 附：证据锚点

```text
tests/test_conversation_context_annotation_finalization.py
    → PASS / DRAFT / DISPUTED / dataset mismatch / duplicate reviewed version /
      missing dependency / missing impact / invalid reference / invalid outcome /
      domain review required + completed / annotator independence / forbidden field /
      probability fields / deterministic / synthetic G3 remains BLOCKED
tests/test_conversation_context_annotation_finalization_architecture.py
    → production 无 finalization 实现；模块命名边界；无 tests/docs/evaluation import
tests/fixtures/conversation_context/annotation_finalization_cases.yaml
    → 12 场景 synthetic finalization fixture（14 条记录）
```
