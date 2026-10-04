# Phase 4.1 Step 28 — Real Evidence Annotation Workflow Contract

- 阶段：Phase 4.1（Conversation / Chat 会话层）
- 类型：**Contract only**（只定义 annotation workflow 契约；不导入真实数据）
- 前置：Step 24（Rubric）+ Step 25（Procedure）+ Step 26（Input Boundary）+ Step 27（Provenance）

---

## 1. Workflow

```text
REAL_DEIDENTIFIED Artifact
        ↓
Dataset Validation（Step 26）
        ↓
Provenance Validation（Step 27）
        ↓
G1 READY
        ↓
Annotation（Annotator → ContextDependencyAnnotation）
        ↓
Review（DRAFT / REVIEWED / DISPUTED）
        ↓
Annotated Evidence
        ↓
G3
```

本阶段只定义 Annotation Workflow Contract；不实现真实 Workflow / UI / Storage / API。

---

## 2. Raw vs Annotated

```text
Raw Evidence Artifact  ≠  Annotated Evidence
```

Raw Artifact 只包含 Step 26 Schema：

```text
dataset_version · source_type · conversations ·
case_id · project_id · turns · role · content
```

Raw Artifact 禁止：

```text
annotation · reference · impact · strategy_id · selected_turns · selection_result
```

Annotated Evidence **不复制** conversation content，
只通过 `dataset_version + case_id` 引用原始证据。

---

## 3. Versioning

```text
dataset_version     → 原始数据身份
annotation_version  → 标注规则/标注结果身份
```

```text
dataset_version = wms-conversation-v1
annotation_version = context-annotation-v1
```

规则：

```text
* 同一 Dataset 可以有 annotation-v1 / annotation-v2（用于修正标注）；
* 禁止修改原始 Dataset Version；
* annotation_version 不覆盖 dataset_version（两者必须同时保留）。
```

---

## 4. Dependency Annotation

复用 Step 24（5 个维度，允许**多标签**，禁止强制 one-hot）：

```text
early_constraint
middle_decision
recent_context
old_topic
standalone
```

示例：

```text
early_constraint = true
middle_decision = true
recent_context  = false
old_topic       = false
standalone      = false
```

约束：**all-false 非法**（必须 standalone 或至少一个依赖维度为真）。

---

## 5. Impact Annotation

复用 Step 24（4 个维度 + unknown）：

```text
business_outcome_changed
critical_constraint_lost
entity_changed
intent_changed
```

值域：

```text
true · false · unknown
```

规则：

```text
unknown 不能自动变成 false；
unknown != false（UNKNOWN 与 NO_IMPACT 是不同分类）。
```

---

## 6. Business Outcome

继续使用 Step 24 的 6 类：

```text
ANSWER_CONTENT · TOOL_SELECTION · TOOL_ARGUMENTS ·
SQL_SEMANTICS · ROUTE · REFUSAL
```

禁止新增：

```text
SUCCESS · FAILED · EMPTY · LATENCY · TOKEN_COST
（这些不是 Context Selection Impact Taxonomy。）
```

---

## 7. Reference

每个需要 Impact Evaluation 的 case 必须声明 `reference_type`：

```text
FULL_HISTORY
DOMAIN_EXPERT_VALIDATED
```

本阶段推荐 `FULL_HISTORY` 作为 reference condition。

必须明确：

```text
Full history 只是 reference condition，不等于业务正确性的绝对证明。
```

（Reference quality 规则沿用 Step 24：须 human-created 或 domain-expert-validated。）

---

## 8. Review

最小状态：

```text
DRAFT
REVIEWED
DISPUTED
```

禁止：

```text
APPROVED · REJECTED · AUTO
```

原因：当前重点是"是否完成标注 / 是否经过复核 / 是否存在争议"，
不是业务质量评分。

整体状态推导：任一 case DISPUTED → DISPUTED；全部 REVIEWED → REVIEWED；否则 DRAFT。

---

## 9. Disagreement

两个 Annotator 对同一 `case_id` 产生不同结果：

```text
DISAGREEMENT
```

不能：

```text
majority vote · average · 自动选择
```

必须：

```text
domain review
```

---

## 10. Security

禁止保存：

```text
API key · Authorization · password · DATABASE_URL ·
SQL · stack trace · raw LLM response · tool raw payload · embedding · vector
```

允许：

```text
case_id · project_id · dataset_version · annotation_version ·
dependency flags · impact flags · business outcome · annotator_id · review_status
```

说明：

```text
project_id 只是 grouping / context identity：

project_id != authorization
```

Annotator identity 使用 anonymized `annotator_id`（如 `domain-reviewer-01`）：
非空 / 稳定 / 无邮箱 / 无姓名 / 无手机号形态。

---

## 11. Production Isolation

```text
Annotation 属于 offline Evaluation Layer，不是 production runtime。
```

```text
* ConversationService / ChatApplicationService / ConversationContextBuilder /
  AIOrchestrator / RAG / Tool / TextToSQL 都不依赖 Annotation / AnnotatedEvidence /
  EvidenceProvenance（AST 审计锁定）；
* backend/app 中不存在 AnnotationService / AnnotatedEvidence / AnnotationWorkflow /
  EvidenceAnnotation（当前 "annotation" 泛词在 production 零命中）；
* 不新增数据库表 / Migration / 生产 Annotation Service / 生产 Evidence Repository /
  Annotation API。
```

LLM 辅助边界：

```text
LLM suggestion
      ↓
Human / Domain Expert Review
      ↓
Final Annotation
```

禁止 LLM 自动生成 annotation / 自动决定 impact / 自动决定 winner；本阶段不实现 LLM 辅助。

---

## 12. G3 Gate

未来 `G3 Evidence = READY` 至少需要：

```text
G1 READY
+ Annotated Evidence exists
+ dataset_version match（不一致 → DATASET_VERSION_MISMATCH）
+ case IDs valid（不存在 → CASE_NOT_FOUND；不自动创建 case）
+ annotation complete（dependency + reference + impact + review 全部存在）
+ review status valid
```

Completeness 缺失（任一 case 缺 dependency / reference / impact / review）：

```text
G3 = BLOCKED
```

禁止：

```text
missing annotation → false
missing impact → unknown（自动填充）
```

当前真实状态保持：

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED
```

---

## 13. Deferred

```text
真实 Annotation（人工标注执行）        = Deferred
Annotation UI                          = Deferred
Annotation Storage                     = Deferred
真实 G3（G3 Evidence READY）            = Deferred
G4 Evaluation                          = Deferred
ContextSelector / SelectionPolicy      = Deferred
Recent N / Recent N+First / Hybrid / Relevance = Deferred
Tokenizer / max_tokens / max_chars / max_turns = Deferred
Truncation / Summary / Memory          = Deferred
LLM Judge / 自动评分                    = Deferred
```

---

## 附：证据锚点

```text
tests/test_conversation_context_annotation_workflow.py
    → Schema / Version / Case / Dependency / Impact / Outcome / Reference /
      Review / Disagreement / Immutability / Completeness / G3 Gate / Security
tests/test_conversation_context_annotation_workflow_architecture.py
    → production 无 annotation 实现；runtime 无 annotation 依赖；契约仅 test-local
docs/evaluation/Phase 4.1 Step 27 — Evidence Provenance & Version Freeze Audit.md
    → dataset_version / source_type（本 Step 的输入）
```
