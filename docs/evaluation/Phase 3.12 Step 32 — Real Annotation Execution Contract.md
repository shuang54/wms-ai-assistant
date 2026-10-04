# Phase 3.12 Step 32 — Real Annotation Execution Contract

- 阶段：Phase 3.12（Conversation / Evidence 契约链延续；对应 Phase 4.1 Step 24~31）
- 类型：**Contract only**（只冻结"真实人工标注如何执行"；**不执行真实人工标注**）
- 前置：Step 24（Annotation Schema）+ Step 28（AnnotatedEvidence）+ Step 29（Review Procedure）
  + Step 30（Finalization）+ Step 31（Real Evidence Import Contract）

---

## 1. Purpose

为什么需要独立的执行 Contract：

```text
已有契约只覆盖：
  * annotation 结构（Step 24）
  * annotation workflow 结构（Step 28）
  * review / comparison 流程（Step 29）
  * finalization 完整性（Step 30）
  * evidence import 边界（Step 31）

缺失的一环：
  已经把 candidate case 交给两个独立 annotator 时，
  如何抽样（sampling）、如何保证独立性（independence）、
  如何保证输入隔离（input isolation）、如何保证不修改证据（immutability）。
```

本阶段冻结该环：

```text
REAL_DEIDENTIFIED Evidence Candidate
        ↓
Case Sampling（explicit / deterministic / auditable）
        ↓
Annotator 1 Input ─┐
Annotator 2 Input ─┴─ Independent Annotation（human；本阶段不产生）
        ↓
Step 29 Compare（复用）
        ↓
Agreement / Disagreement → Domain Review（复用）
        ↓
Step 30 Finalization（复用）
        ↓
Future G3 Evidence（本阶段不产生）
```

不重新设计任何已有 Contract；只新增 Execution Contract。

---

## 2. Sampling

```text
explicit deterministic case selection
```

规则：

```text
* 优先显式 case_id 列表（按传入顺序，auditable）；
* 需要从全集生成时：sorted(case_id) → [start : start + limit]；
* 禁止：random.random() / random.sample() / time-based / LLM sampling；
* 禁止引入随机 seed；
* Sampling 只 select，**不得修改 evidence**：
  不修改 case / 不删除 turns / 不重写 content / 不脱敏 / 不 normalize /
  不 merge / 不 split / 不改 role / 不改 project_id /
  不自动删除"困难案例"。
```

Sampling 校验（failure codes）：

```text
dataset_version 空            → INVALID_CASE
case_id 空                    → INVALID_CASE
case_id 重复（请求列表内）     → DUPLICATE_CASE_ID
case 不存在                   → CASE_NOT_FOUND
requested version != evidence → DATASET_VERSION_MISMATCH（不自动转换）
```

结果 DTO（frozen）：

```text
AnnotationSamplingResult
    dataset_version
    case_ids
    sample_count
```

---

## 3. Annotator Independence

```text
annotator_1 != annotator_2
```

否则：

```text
ANNOTATOR_NOT_INDEPENDENT
```

---

## 4. Input Isolation

```text
A1 不看到 A2
A2 不看到 A1
```

规则：

```text
* Annotator 1 输入 = case + annotation instructions；
* Annotator 2 输入 = 同一个 case + 同一份 annotation instructions；
* 禁止进入另一个 annotator 的输入：
    previous_annotation · review_hint · agreement_hint · disagreement_hint ·
    annotator_id · annotation · review_status · comparison_outcome；
* annotator_id 只作为 execution metadata，**不是**待标注内容的一部分；
* 输入字段白名单：dataset_version / case_id / project_id / turns（turn: role / content）。
```

违规：

```text
ANNOTATOR_INPUT_LEAK
```

---

## 5. Versioning

```text
dataset_version      = Evidence version
annotation_version   = Annotation schema / revision version
```

例如：

```text
dataset_version = wms-prod-2026-10
annotation_version = annotation-v1
```

两者不能混用；Execution Result **不含** annotation_version（标注版本属 annotation 产物）。

---

## 6. Revision

```text
禁止 overwrite existing reviewed annotation
必须 new annotation_version（annotation-v1 → annotation-v2）
原版本保持 immutable
```

（复用 Step 28 `AnnotatedEvidence.revise(...)` —— 不重新实现。）

---

## 7. Security

禁止包含：

```text
API_KEY · Authorization · password · DATABASE_URL · SQL ·
stack_trace · raw_llm_response · embedding · vector · tool_raw_payload
```

（复用 Step 28 / Step 31 的安全扫描规则 —— 不建立第二套 scanner。）

`project_id` 只是业务上下文标识：

```text
project_id ≠ authorization / tenant_id / security boundary
```

（本阶段不新增 project authorization。）

---

## 8. Current State

```text
Synthetic only
No real annotation
G1 blocked
G3 blocked
```

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 Evidence = BLOCKED
G4 = BLOCKED
ContextSelector = BLOCKED
Selection Strategy = BLOCKED
```

即：Execution Contract PASS 不改变任何 Gate；
synthetic fixture **绝对不会**晋升为 REAL_DEIDENTIFIED。

---

## 9. Deferred

```text
Real annotator onboarding（真实标注员入驻）      = Deferred
Real evidence annotation（真实证据标注执行）      = Deferred
Domain review execution（真实领域评审执行）       = Deferred
G3 evidence readiness（G3 证据就绪）              = Deferred
G1 evaluation / G2 measurement / G4 strategy selection = Deferred
ContextSelector / SelectionPolicy / SelectionBudget    = Deferred
Annotation UI / Storage / API                          = Deferred
```

---

## 附：证据锚点

```text
tests/test_real_annotation_execution_contract.py
    → Sampling（explicit / sorted window / duplicate / missing / version mismatch）/
      Independence（A1 != A2）/ Input Isolation（白名单 + 双向不可见 + leak 拒绝）/
      Immutability（evidence 不变）/ Versioning（dataset vs annotation）/
      Revision（新 annotation_version）/ Security / 无自动标注 / 无自动 Review /
      Step 29 兼容 / G1~G4 不升级 / Production 边界 / 文档
tests/fixtures/conversation_context/real_annotation_execution_cases.yaml
    → 4 cases + 6 场景（SYNTHETIC_ONLY）
```
