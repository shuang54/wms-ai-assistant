你现在开始执行：

# Phase 4.1 Step 30：Annotation Evidence Finalization & Integrity Gate

## 一、阶段目标

Phase 4.1 Step 29 已完成：

```text
Annotator 1
    ↓
Independent Annotator 2
    ↓
Compare
    ├── AGREEMENT
    └── DISAGREEMENT
            ↓
      DOMAIN_REVIEW_REQUIRED
            ↓
       FINAL_REVIEW
```

现在只解决一个问题：

> **什么样的 AnnotatedEvidence 才能被认为是“完整、版本一致、Review 状态合法、可以进入未来 G3 Evidence Gate”的候选证据？**

本阶段只建立：

```text
Reviewed Annotation
        ↓
Finalization / Integrity Check
        ↓
AnnotatedEvidenceIntegrityResult
        ↓
G3 Evidence Input Contract
```

**不实现真实 G3 Evidence。**

---

# 二、严格范围

## 允许

只允许修改/新增：

```text
tests/
docs/evaluation/
```

如果当前 Step 29 的 test-local contract 必须增加一个极小的：

```text
finalization / integrity helper
```

可以放在：

```text
tests/
```

内部。

---

## 禁止

禁止修改：

```text
backend/app/
```

禁止：

```text
ContextSelector
SelectionPolicy
SelectionBudget
G2
G4
真实 WMS 数据
真实 Annotation
Annotation UI
Annotation Storage
Annotation API
Database
Migration
DeepSeek
SiliconFlow
Network
```

禁止修改：

```text
Step 21 dataset
Step 24 annotation contract
Step 25 evidence gate
Step 26 evidence artifact
Step 27 provenance
Step 28 workflow
Step 29 review procedure
```

Step 24～29 历史行为必须保持。

---

# 三、开始前必须阅读

先阅读：

```text
tests/test_conversation_context_annotation_workflow.py
tests/test_conversation_context_annotation_workflow_architecture.py

tests/test_conversation_context_annotation_review.py
tests/test_conversation_context_annotation_review_architecture.py

tests/test_conversation_selection_evidence_data_audit.py
```

以及：

```text
docs/evaluation/Phase 4.1 Step 28 — Annotation Workflow.md
docs/evaluation/Phase 4.1 Step 29 — Annotation Review Procedure.md
```

重点理解：

```text
AnnotatedEvidence
CaseAnnotation
ReviewStatus
ComparisonOutcome
ReviewStatistics
dataset_version
annotation_version
source_type
```

不要重新定义这些 DTO。

---

# 四、Finalization 的核心原则

未来只有：

```text
REVIEWED
```

状态的 Case 才可能进入：

```text
Finalized Annotation Evidence
```

以下全部不能 Finalize：

```text
DRAFT
DISPUTED
```

不要：

```text
DRAFT → Finalized
DISPUTED → Finalized
```

---

# 五、Dataset Version Integrity

必须验证：

```text
AnnotatedEvidence.dataset_version
```

与所有 CaseAnnotation 使用的：

```text
dataset_version
```

完全一致。

例如：

```text
Evidence dataset_version = v1

Case A = v1
Case B = v1
Case C = v2
```

必须：

```text
BLOCKED
```

不能自动：

```text
convert
coerce
upgrade
downgrade
```

---

# 六、Annotation Version Integrity

同一个 Finalized Evidence 中：

允许：

```text
case A annotation_version = v3
case B annotation_version = v2
case C annotation_version = v7
```

因为 annotation_version 是：

> annotation revision identity

不是 dataset version。

但是：

每个 Case 必须只有一个最终有效的：

```text
REVIEWED
```

版本。

如果同一个 case 同时存在：

```text
v2 REVIEWED
v3 REVIEWED
```

必须：

```text
BLOCKED
```

不能自动选择最新版本。

不能：

```text
max(annotation_version)
```

作为 winner。

---

# 七、Case Completeness

每个进入 Finalized Evidence 的 Case 必须具备：

```text
case_id
dependency_annotation
reference_annotation
impact_annotation
review_status = REVIEWED
annotation_version
```

缺任何一项：

```text
BLOCKED
```

不能自动填：

```text
unknown
false
None
```

尤其：

```text
impact unknown
```

必须保持：

```text
unknown
```

但：

```text
unknown
```

本身不代表：

```text
missing
```

---

# 八、Reference Integrity

Reference 必须继续只允许 Step 24 已冻结类型：

```text
FULL_HISTORY
DOMAIN_EXPERT_VALIDATED
```

禁止：

```text
LLM_GENERATED
INFERRED
AUTO_SELECTED
```

如果出现非法 reference：

```text
BLOCKED
```

---

# 九、Dependency Integrity

Dependency 必须保持 Step 24 五维结构：

```text
early_constraint
middle_decision
recent_context
old_topic
standalone
```

规则：

```text
至少一个 True
```

不能：

```text
全部 False
```

不能出现未知字段。

不能出现：

```text
score
confidence
weight
probability
```

---

# 十、Impact Integrity

Impact 必须保持：

```text
business_outcome_changed
critical_constraint_lost
entity_changed
intent_changed
```

每个字段：

```text
True
False
Unknown
```

其中：

```text
Unknown != False
```

不要进行：

```text
unknown → false
```

自动归一化。

---

# 十一、Business Outcome Integrity

继续使用 Step 24 冻结的六类：

```text
ANSWER_CONTENT
TOOL_SELECTION
TOOL_ARGUMENTS
SQL_SEMANTICS
ROUTE
REFUSAL
```

禁止：

```text
SUCCESS
FAILED
EMPTY
LLM_ERROR
RAG_ERROR
TOOL_ERROR
```

注意：

```text
Assistant Outcome
```

和：

```text
Business Outcome
```

不是同一个概念。

---

# 十二、Review Integrity

最终：

```text
AGREEMENT
```

必须已经：

```text
review_status = REVIEWED
```

并且：

```text
domain_review_required = false
```

如果：

```text
DISAGREEMENT
```

必须存在：

```text
DOMAIN_REVIEW_REQUIRED
```

以及最终：

```text
REVIEWED
```

不能：

```text
DISAGREEMENT + REVIEWED
```

但没有 Domain Review 证据。

本阶段只验证结构性契约，不实现真实 Domain Reviewer。

---

# 十三、Annotator Independence

Finalized Case 如果来自：

```text
DISAGREEMENT
```

必须能够确认：

```text
annotator_1 != annotator_2
```

不能：

```text
same annotator
```

Step 29 已经冻结的 independence contract 不得放宽。

---

# 十四、No Hidden Information

Finalized Evidence 仍然不能包含：

```text
prompt
messages
SQL
raw LLM response
raw tool payload
API key
Authorization
password
DATABASE_URL
stack trace
embedding
vector
```

继续复用：

```text
validate_no_forbidden_annotation_fields
```

不要重新实现第二套 security scanner。

---

# 十五、Finalized Evidence 不复制 Raw Dataset

非常重要。

Finalized Evidence 不允许重新嵌入：

```text
conversation
turns
content
full history
candidate history
```

只保存：

```text
dataset_version
source_type
case annotations
review metadata
```

Raw Evidence 仍然通过：

```text
dataset_version + case_id
```

引用。

---

# 十六、Finalization Result

增加一个 test-local 结果契约，例如：

```text
AnnotationEvidenceIntegrityResult
```

最小字段：

```text
valid
dataset_version
source_type
cases_checked
blocked_cases
```

如果需要原因：

```text
failure_codes
```

但：

**不要建立复杂 error taxonomy。**

建议只使用结构化的少量：

```text
DATASET_VERSION_MISMATCH
UNREVIEWED_CASE
DUPLICATE_REVIEWED_VERSION
INCOMPLETE_CASE
INVALID_REFERENCE
INVALID_DEPENDENCY
INVALID_IMPACT
INVALID_BUSINESS_OUTCOME
REVIEW_STATE_MISMATCH
ANNOTATOR_NOT_INDEPENDENT
FORBIDDEN_FIELD
```

不要增加几十个错误类型。

---

# 十七、Synthetic Fixtures

新增或扩展：

```text
tests/fixtures/conversation_context/annotation_finalization_cases.yaml
```

至少覆盖：

### Case 1

```text
全部 REVIEWED
dataset_version 一致
→ PASS
```

### Case 2

```text
存在 DRAFT
→ BLOCKED
```

### Case 3

```text
存在 DISPUTED
→ BLOCKED
```

### Case 4

```text
dataset_version mismatch
→ BLOCKED
```

### Case 5

```text
同 case 两个 REVIEWED annotation_version
→ BLOCKED
```

### Case 6

```text
缺 dependency
→ BLOCKED
```

### Case 7

```text
缺 impact
→ BLOCKED
```

### Case 8

```text
非法 reference
→ BLOCKED
```

### Case 9

```text
非法 business outcome
→ BLOCKED
```

### Case 10

```text
DISAGREEMENT
但没有最终 Domain Review
→ BLOCKED
```

### Case 11

```text
DISAGREEMENT
+ Domain Review
+ FINAL_REVIEW
+ REVIEWED
→ PASS
```

### Case 12

```text
包含 forbidden field
→ BLOCKED
```

---

# 十八、不要统计 Accuracy

本阶段禁止输出：

```text
accuracy
score
quality score
winner
ranking
```

可以统计：

```text
cases_checked
cases_finalized
cases_blocked
```

这是：

> integrity statistics

不是：

> model / annotator quality score

---

# 十九、G3 Gate

这是本阶段最重要的验证。

当前：

```text
G1 = BLOCKED
```

因此：

即使：

```text
Synthetic Evidence
→ all REVIEWED
→ Finalization PASS
```

也不能：

```text
G3 Evidence = READY
```

必须仍然：

```text
G3 Evidence = BLOCKED
```

原因：

```text
synthetic procedure validation
```

不等于：

```text
real evidence readiness
```

不能改变 Step 25 的 Gate 语义。

---

# 二十、Tests

新增：

```text
tests/test_conversation_context_annotation_finalization.py
tests/test_conversation_context_annotation_finalization_architecture.py
```

至少覆盖：

```text
PASS
DRAFT BLOCKED
DISPUTED BLOCKED
dataset mismatch
duplicate reviewed version
missing dependency
missing impact
invalid reference
invalid outcome
domain review required
domain review completed
forbidden field
synthetic G3 remains BLOCKED
deterministic
```

建议：

```text
15～25 tests
```

不要机械堆数量。

---

# 二十一、Architecture Audit

继续保持：

```text
production backend = 0 annotation-specific code
```

AST 检查：

```text
backend/app/**/*.py
```

不得出现新的：

```text
finalization
annotation_review
annotation_comparison
domain_review
annotator
```

也不得 import：

```text
tests
docs
evaluation
```

---

# 二十二、Documentation

新增：

```text
docs/evaluation/Phase 4.1 Step 30 — Annotation Evidence Finalization.md
```

说明：

## 1. Purpose

```text
Review → Finalized Evidence
```

## 2. Integrity Rules

```text
dataset_version
annotation_version
review_status
dependency
impact
reference
business outcome
```

## 3. Block Conditions

列出主要 BLOCKED 条件。

## 4. G3 Relationship

明确：

```text
Finalization PASS
    ≠
G3 Evidence READY
```

因为：

```text
G1 BLOCKED
```

## 5. Synthetic Limitation

明确：

```text
Synthetic fixtures only
No real WMS annotation
```

## 6. Production Isolation

```text
Production Code = 0
```

---

# 二十三、测试命令

只执行：

```powershell
python -m pytest -q tests/test_conversation_context_annotation_finalization.py
python -m pytest -q tests/test_conversation_context_annotation_finalization_architecture.py
```

然后：

```powershell
python -m compileall -q backend tests
```

再回归：

```powershell
python -m pytest -q tests/test_conversation_context_annotation_review.py
python -m pytest -q tests/test_conversation_context_annotation_review_architecture.py
```

不要：

```text
RUN_DB_TESTS
```

不要：

```text
全量 pytest
```

不要：

```text
真实 LLM
```

---

# 二十四、Git Diff

检查：

```powershell
git status --short
git diff --stat
git diff
```

必须确认：

```text
Production Code = 0
DB Schema = 0
DB Writes = 0
Network = 0
LLM = 0
Tokenizer = 0
Prompt = unchanged
Step 21 dataset = unchanged
Step 24 contract = unchanged
Step 25 G1/G2/G3/G4 = unchanged
Step 28 workflow = unchanged
Step 29 review procedure = unchanged
```

允许：

```text
tests/
docs/evaluation/
```

---

# 二十五、最终报告

严格：

```text
Phase 4.1 Step 30 完成报告

1. Finalization Contract
2. Dataset Version Integrity
3. Annotation Version Integrity
4. Case Completeness
5. Dependency Integrity
6. Impact Integrity
7. Reference Integrity
8. Business Outcome Integrity
9. Review Integrity
10. Annotator Independence
11. Security
12. Synthetic Fixtures
13. Finalization Result
14. G3 Gate
15. Tests
16. Architecture Audit
17. compileall
18. Git Diff
19. Production Code
20. DB / Network / LLM
21. Current Limitations

G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED

Phase 4.1 Step 30 READY
Phase 4.1 Step 30 STOP
```

---

# 二十六、硬停止

完成后立即 STOP。

不要进入：

```text
Step 31 Real Evidence Import
Step 32 Real Annotation
Step 33 G2
Step 34 G4
ContextSelector
SelectionPolicy
SelectionBudget
```

除非收到明确的下一步指令。

本阶段只建立：

```text
Reviewed Annotation
        ↓
Integrity Finalization
        ↓
Future G3 Input Contract
```
