# Phase 4.1 Step 28：Real Evidence Annotation Workflow Contract Audit

## 一、阶段目标

Phase 4.1 Step 27 已完成：

```text
REAL_DEIDENTIFIED Evidence Artifact
        ↓
Dataset Validation
        ↓
Provenance
        ↓
G1
```

当前：

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED
```

本阶段只解决：

> **未来真实 WMS Evidence 如何进入 G3 Annotation Workflow，以及 Annotation 结果如何与原始 Dataset Version 建立可追溯关系。**

本阶段仍然：

```text
只做 Contract
只做离线测试
只做文档
不导入真实数据
不实现 Annotation UI
不实现 ContextSelector
```

---

# 二、严格禁止

禁止：

```text
真实 WMS 数据
真实客户/供应商/订单/库存/生产数据
真实脱敏文件
DeepSeek
SiliconFlow
PostgreSQL
Network
```

禁止：

```text
ContextSelector
SelectionPolicy
Recent N
Recent N + First
Hybrid
Relevance
Tokenizer
max_tokens
max_chars
max_turns
Truncation
Summary
Memory
LLM Judge
自动评分
```

禁止修改：

```text
Conversation ORM
ConversationRepository
ConversationService
ChatApplicationService
ConversationContextBuilder
AIOrchestrator
RAG
Tool
TextToSQL
Router
Prompt
API
```

禁止新增：

```text
数据库表
Migration
生产 Annotation Service
生产 Evidence Repository
Annotation API
```

---

# 三、开始前必须阅读

阅读：

```text
tests/test_conversation_context_evidence_collection_procedure.py
tests/test_conversation_context_evaluation_rubric.py
tests/test_conversation_context_real_evidence_boundary.py
tests/test_conversation_context_evidence_provenance.py

docs/evaluation/
    Phase 4.1 Step 24 — Context Dependency & Impact Annotation Rubric.md
    Phase 4.1 Step 25 — Evidence Collection Procedure.md
    Phase 4.1 Step 26 — Real WMS Evidence Input Boundary.md
    Phase 4.1 Step 27 — Evidence Provenance & Version Freeze Audit.md
```

必须复用 Step 24 已冻结的：

```text
ContextDependencyAnnotation
ContextImpactAnnotation
Business Outcome taxonomy
Reference quality rules
Disagreement rules
```

不要重新设计 Annotation Schema。

---

# 四、核心链路

冻结未来概念：

```text
REAL_DEIDENTIFIED Artifact
        ↓
Dataset Validation
        ↓
Provenance Validation
        ↓
G1 READY
        ↓
Annotation Workflow
        ↓
Annotated Evidence
        ↓
G3 Evidence READY
```

本阶段只定义：

```text
Annotation Workflow Contract
```

不实现真实 Workflow。

---

# 五、Raw Artifact 与 Annotation 必须分离

明确：

```text
Raw Evidence Artifact
        ≠
Annotated Evidence
```

Raw Artifact 只包含 Step 26 Schema：

```text
dataset_version
source_type
conversations
case_id
project_id
turns
role
content
```

Raw Artifact 禁止：

```text
annotation
reference
impact
strategy_id
selected_turns
selection_result
```

---

# 六、Annotated Evidence 最小结构

本阶段只在 tests/docs 中定义未来结构：

```text
AnnotatedEvidence
```

建议最小字段：

```text
dataset_version
annotation_version
source_type
cases
```

其中：

```text
cases:
  - case_id
    dependency_annotation
    reference_annotation
    impact_annotation
```

注意：

**不要把完整原始 conversation content 再复制一份到 Annotated Evidence。**

通过：

```text
dataset_version
case_id
```

引用原始 Evidence。

---

# 七、Annotation Version

明确：

```text
dataset_version
    ↓
原始数据身份

annotation_version
    ↓
标注规则/标注结果身份
```

两者不能互相替代。

例如：

```text
dataset_version = wms-conversation-v1
annotation_version = context-annotation-v1
```

同一个 Dataset 可以有：

```text
annotation-v1
annotation-v2
```

用于修正标注。

禁止修改原始 Dataset Version。

---

# 八、Dependency Annotation

复用 Step 24：

```text
early_constraint
middle_decision
recent_context
old_topic
standalone
```

允许多标签。

例如：

```text
early_constraint=true
middle_decision=true
recent_context=false
old_topic=false
standalone=false
```

禁止强制 one-hot。

---

# 九、Impact Annotation

复用：

```text
business_outcome_changed
critical_constraint_lost
entity_changed
intent_changed
```

值：

```text
true
false
unknown
```

规则保持：

```text
unknown
```

不能自动变成：

```text
false
```

---

# 十、Business Outcome

继续使用 Step 24：

```text
ANSWER_CONTENT
TOOL_SELECTION
TOOL_ARGUMENTS
SQL_SEMANTICS
ROUTE
REFUSAL
```

禁止新增：

```text
SUCCESS
FAILED
EMPTY
LATENCY
TOKEN_COST
```

这些不是 Context Selection Impact Taxonomy。

---

# 十一、Reference Contract

每个需要 Impact Evaluation 的 case 必须明确：

```text
reference_type
```

允许：

```text
FULL_HISTORY
DOMAIN_EXPERT_VALIDATED
```

本阶段推荐：

```text
FULL_HISTORY
```

作为 reference condition。

但必须明确：

> Full history 只是 reference condition，不等于业务正确性的绝对证明。

---

# 十二、禁止 LLM 自动当 Judge

禁止：

```text
LLM → 自动生成 annotation
```

禁止：

```text
LLM → 自动决定 impact
```

禁止：

```text
LLM → 自动决定 strategy winner
```

如果未来使用 LLM 辅助：

必须：

```text
LLM suggestion
      ↓
Human / Domain Expert Review
      ↓
Final Annotation
```

本阶段不实现 LLM 辅助。

---

# 十三、Annotator Identity

未来 Annotated Evidence 必须能够区分：

```text
annotator
```

但本阶段：

**不要使用真实个人姓名。**

建议只定义：

```text
annotator_id
```

格式约束：

```text
非空
稳定
不可包含邮箱
不可包含姓名
不可包含手机号
```

例如：

```text
domain-reviewer-01
```

---

# 十四、Review Status

定义最小状态：

```text
DRAFT
REVIEWED
DISPUTED
```

禁止：

```text
APPROVED
REJECTED
AUTO
```

原因：

当前重点是：

```text
是否完成标注
是否经过复核
是否存在争议
```

不要把 Review Status 变成业务质量评分。

---

# 十五、Disagreement

复用 Step 24：

如果两个 Annotator 对同一个：

```text
case_id
```

产生不同结果：

```text
DISAGREEMENT
```

不能：

```text
majority vote
```

不能：

```text
average
```

不能：

```text
自动选择
```

必须：

```text
domain review
```

本阶段只冻结 Contract。

---

# 十六、Annotation Immutability

一旦：

```text
AnnotatedEvidence
```

状态：

```text
REVIEWED
```

不得修改：

```text
dataset_version
annotation_version
dependency_annotation
impact_annotation
```

如果需要修正：

创建新的：

```text
annotation_version
```

而不是覆盖旧版本。

---

# 十七、Dataset / Annotation Version Mismatch

如果：

```text
AnnotatedEvidence.dataset_version = V1
```

但实际引用：

```text
Artifact.dataset_version = V2
```

必须：

```text
DATASET_VERSION_MISMATCH
```

不得生成：

```text
G3 READY
```

---

# 十八、Case ID Mismatch

如果：

```text
AnnotatedEvidence.case_id
```

不存在于：

```text
Artifact.case_id
```

必须：

```text
CASE_NOT_FOUND
```

不能自动创建 Case。

---

# 十九、Annotation Completeness

G3 READY 前必须：

```text
每个 evaluated case
    ↓
dependency annotation
    ↓
reference annotation
    ↓
impact annotation
    ↓
review status
```

全部存在。

如果缺失：

```text
G3 = BLOCKED
```

禁止：

```text
missing annotation → false
```

禁止：

```text
missing impact → unknown automatically
```

---

# 二十、Annotation Evidence 与 G3

未来：

```text
G3 Evidence = READY
```

至少需要：

```text
G1 READY
+
Annotated Evidence exists
+
dataset_version match
+
case IDs valid
+
annotation complete
+
review status valid
```

本阶段真实状态保持：

```text
G1 BLOCKED
G3 Evidence BLOCKED
```

---

# 二十一、Security Boundary

Annotation Contract 禁止保存：

```text
API key
Authorization
password
DATABASE_URL
SQL
stack trace
raw LLM response
tool raw payload
embedding
vector
```

允许：

```text
case_id
project_id
dataset_version
annotation_version
dependency flags
impact flags
business outcome
annotator_id
review_status
```

注意：

`project_id` 只是 grouping/context identity：

```text
project_id != authorization
```

---

# 二十二、No Production Coupling

本阶段必须保证：

```text
ConversationService
ChatApplicationService
ConversationContextBuilder
AIOrchestrator
RAG
Tool
TextToSQL
```

都不依赖：

```text
Annotation
AnnotatedEvidence
EvidenceProvenance
```

Annotation 属于：

```text
offline Evaluation Layer
```

不是：

```text
production runtime
```

---

# 二十三、测试

新增：

```text
tests/test_conversation_context_annotation_workflow.py
```

至少覆盖：

### Schema

```text
valid annotated evidence
missing dataset_version
missing annotation_version
invalid source_type
missing cases
```

### Version

```text
dataset_version match
dataset_version mismatch
annotation version independent
```

### Case

```text
valid case_id
unknown case_id
duplicate annotation case
```

### Dependency

```text
single label
multi-label
all false invalid
standalone
```

### Impact

```text
true
false
unknown
```

确认：

```text
unknown != false
```

### Reference

```text
FULL_HISTORY
DOMAIN_EXPERT_VALIDATED
invalid reference type
```

### Review

```text
DRAFT
REVIEWED
DISPUTED
```

### Disagreement

```text
DISAGREEMENT
no majority vote
```

### Immutability

```text
REVIEWED cannot mutate
new annotation_version required
```

### Completeness

```text
missing dependency → BLOCKED
missing reference → BLOCKED
missing impact → BLOCKED
missing review → BLOCKED
```

### Gating

```text
G1 BLOCKED
→ G3 Evidence BLOCKED
```

---

# 二十四、Architecture Audit

新增：

```text
tests/test_conversation_context_annotation_workflow_architecture.py
```

AST 检查：

```text
backend/app/
```

不得出现：

```text
AnnotationService
AnnotatedEvidence
AnnotationWorkflow
EvidenceAnnotation
```

等生产实现。

同时：

```text
ConversationService
ChatApplicationService
AIOrchestrator
```

不得 import：

```text
tests
annotation workflow
evaluation annotation
```

---

# 二十五、文档

新增：

```text
docs/evaluation/Phase 4.1 Step 28 — Real Evidence Annotation Workflow Contract.md
```

必须包含：

## 1. Workflow

```text
REAL_DEIDENTIFIED Artifact
        ↓
G1
        ↓
Annotation
        ↓
Review
        ↓
Annotated Evidence
        ↓
G3
```

## 2. Raw vs Annotated

明确两者分离。

## 3. Versioning

```text
dataset_version
annotation_version
```

## 4. Dependency Annotation

5 个维度。

## 5. Impact Annotation

4 个维度 + unknown。

## 6. Business Outcome

6 个类型。

## 7. Reference

FULL_HISTORY / DOMAIN_EXPERT_VALIDATED。

## 8. Review

DRAFT / REVIEWED / DISPUTED。

## 9. Disagreement

必须 domain review。

## 10. Security

允许/禁止字段。

## 11. Production Isolation

Annotation 不进入 runtime。

## 12. G3 Gate

完整条件。

## 13. Deferred

```text
真实 Annotation
Annotation UI
Annotation Storage
真实 G3
G4 Evaluation
ContextSelector
SelectionPolicy
```

---

# 二十六、验证命令

只运行：

```powershell
python -m pytest -q tests/test_conversation_context_annotation_workflow.py
```

然后：

```powershell
python -m pytest -q tests/test_conversation_context_annotation_workflow_architecture.py
```

然后：

```powershell
python -m compileall -q backend tests
```

不运行：

```text
RUN_DB_TESTS
DeepSeek
SiliconFlow
Network
```

---

# 二十七、Git Diff

确认：

```text
Production code = 0
DB schema = 0
DB writes = 0
Network = 0
LLM = 0
```

允许：

```text
tests/
docs/
```

禁止：

```text
backend/app/annotation/
backend/app/evaluation/annotation*
backend/app/db/annotation*
```

---

# 二十八、最终报告

严格：

```text
Phase 4.1 Step 28 完成报告

1. Annotation Workflow
2. Raw vs Annotated
3. dataset_version
4. annotation_version
5. Dependency Annotation
6. Impact Annotation
7. Business Outcome
8. Reference Contract
9. Review Status
10. Disagreement
11. Immutability
12. Completeness
13. G3
14. Security
15. Production Isolation
16. Tests
17. Architecture Audit
18. Compileall
19. Git Diff
20. Production Code
21. DB / Network / LLM
22. Current Limitations

G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED

Phase 4.1 Step 28 READY
Phase 4.1 Step 28 STOP
```

---

# 二十九、硬停止

完成后立即停止。

不要进入：

```text
Step 29 Real G2
Step 30 G4 Evaluation
ContextSelector
SelectionPolicy
Memory
Summary
Tokenizer
```

等待下一步指令。
