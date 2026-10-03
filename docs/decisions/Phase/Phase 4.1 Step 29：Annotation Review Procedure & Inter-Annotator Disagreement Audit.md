# Phase 4.1 Step 29：Annotation Review Procedure & Inter-Annotator Disagreement Audit

## 一、阶段目标

Phase 4.1 Step 28 已完成：

```text
REAL_DEIDENTIFIED Artifact
        ↓
G1
        ↓
Annotation Workflow Contract
        ↓
Annotated Evidence
        ↓
G3
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

> **如何对未来真实 Evidence 执行 Annotation、如何进行第二人复核，以及如何确定 disagreement。**

本阶段仍然：

```text
只做离线 Procedure Contract
只使用 synthetic contract fixtures
不导入真实 WMS 数据
不执行真实 Annotation
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
Annotation API
生产 Annotation Service
Annotation Repository
```

---

# 三、开始前必须阅读

阅读：

```text
tests/test_conversation_context_annotation_workflow.py
tests/test_conversation_context_annotation_workflow_architecture.py

tests/test_conversation_context_evidence_collection_procedure.py
tests/test_conversation_context_evaluation_rubric.py
tests/test_conversation_context_real_evidence_boundary.py
tests/test_conversation_context_evidence_provenance.py
```

以及：

```text
docs/evaluation/
Phase 4.1 Step 24 — Multi-turn Context Evaluation Rubric.md
Phase 4.1 Step 25 — Evidence Collection Procedure.md
Phase 4.1 Step 28 — Real Evidence Annotation Workflow Contract.md
```

必须复用已有：

```text
ContextDependencyAnnotation
ContextImpactAnnotation
Business Outcome taxonomy
Reference Contract
Review Status
Disagreement semantics
```

不要重新设计这些 DTO。

---

# 四、冻结 Annotation Procedure

未来正式流程：

```text
Case
 ↓
Annotator 1
 ↓
Draft Annotation
 ↓
Annotator 2 / Reviewer
 ↓
Independent Review
 ↓
Compare
 ├── AGREEMENT
 └── DISAGREEMENT
        ↓
   Domain Review
        ↓
   Final Reviewed Annotation
```

本阶段只定义流程。

不实现人员系统。

---

# 五、Annotator 1

第一位标注员负责：

```text
dependency_annotation
reference_annotation
impact_annotation
```

必须基于：

```text
case
current_turn
candidate history
full history reference
```

进行标注。

禁止使用：

```text
LLM 自动判断
数据库查询
生产业务系统
Prompt hidden information
```

---

# 六、Annotator 2

第二位标注员必须：

> **独立重新判断，而不是先看 Annotator 1 的答案。**

禁止：

```text
copy
reuse
majority
```

目的：

```text
测量 annotation disagreement
```

---

# 七、Comparison Contract

比较：

```text
Annotator 1
        vs
Annotator 2
```

至少比较：

```text
dependency_annotation
reference_type
impact_annotation
business_outcome
```

如果全部一致：

```text
AGREEMENT
```

如果任意一个关键字段不同：

```text
DISAGREEMENT
```

不要定义：

```text
80% agreement
```

之类模糊规则。

---

# 八、Disagreement

如果：

```text
DISAGREEMENT
```

必须进入：

```text
DOMAIN_REVIEW_REQUIRED
```

禁止：

```text
majority vote
average
automatic merge
LLM judge
random choice
```

---

# 九、Domain Review

Domain Review 可以重新查看：

```text
full history
current turn
candidate history
reference condition
Annotator 1
Annotator 2
```

然后产生：

```text
FINAL_REVIEW
```

本阶段只定义：

```text
DOMAIN_REVIEW_REQUIRED
FINAL_REVIEW
```

不实现真实 Reviewer UI。

---

# 十、Review Status

继续使用 Step 28：

```text
DRAFT
REVIEWED
DISPUTED
```

推荐状态转换：

```text
DRAFT
  ↓
REVIEWED
```

或者：

```text
DRAFT
  ↓
DISPUTED
  ↓
DOMAIN REVIEW
  ↓
REVIEWED
```

禁止：

```text
REVIEWED
  ↓
直接覆盖
```

修正必须产生新的：

```text
annotation_version
```

---

# 十一、Dependency Annotation Comparison

因为 Dependency 是多标签：

```text
A:
early_constraint=true
middle_decision=true

B:
early_constraint=true
middle_decision=false
```

必须：

```text
DISAGREEMENT
```

不能：

```text
部分相同 → AGREEMENT
```

Comparison 必须按完整结构比较。

---

# 十二、Impact Comparison

Impact 是：

```text
true
false
unknown
```

三值。

例如：

```text
A = unknown
B = false
```

必须：

```text
DISAGREEMENT
```

绝对不能：

```text
unknown → false
```

---

# 十三、Business Outcome Comparison

继续使用：

```text
ANSWER_CONTENT
TOOL_SELECTION
TOOL_ARGUMENTS
SQL_SEMANTICS
ROUTE
REFUSAL
```

例如：

```text
A = SQL_SEMANTICS
B = TOOL_ARGUMENTS
```

必须：

```text
DISAGREEMENT
```

---

# 十四、Reference Comparison

Reference：

```text
FULL_HISTORY
DOMAIN_EXPERT_VALIDATED
```

如果：

```text
A = FULL_HISTORY
B = DOMAIN_EXPERT_VALIDATED
```

必须：

```text
DISAGREEMENT
```

不能认为二者都是 reference 所以相同。

---

# 十五、Annotation Quality

本阶段不要计算：

```text
accuracy
score
quality_score
weighted_score
```

只统计：

```text
cases_reviewed
agreements
disagreements
domain_review_required
final_reviewed
```

允许计算：

```text
agreement_rate
```

但只作为**描述性统计**，不能作为模型/标注员排名。

---

# 十六、Agreement Rate

如果：

```text
cases = 0
```

则：

```text
agreement_rate = N/A
```

不能：

```text
0%
```

如果：

```text
cases = 10
agreements = 8
```

可以报告：

```text
80%
```

但不要：

```text
annotator A = 80 分
annotator B = 90 分
```

---

# 十七、Synthetic Procedure Fixture

新增测试 fixture：

```text
tests/fixtures/conversation_context/annotation_review_cases.yaml
```

只允许：

```text
synthetic
non-production
WMS-realistic
```

每个 case 至少：

```yaml
id
project_id
turns
```

不要加入：

```text
real customer
real supplier
real order
real inventory
real employee
```

建议至少覆盖：

```text
agreement
dependency disagreement
impact disagreement
outcome disagreement
reference disagreement
domain review
```

---

# 十八、测试

新增：

```text
tests/test_conversation_context_annotation_review.py
```

至少覆盖：

### Agreement

```text
identical annotations → AGREEMENT
```

### Dependency

```text
different multi-label set → DISAGREEMENT
```

### Impact

```text
true vs false → DISAGREEMENT
unknown vs false → DISAGREEMENT
```

### Outcome

```text
SQL_SEMANTICS vs TOOL_SELECTION → DISAGREEMENT
```

### Reference

```text
FULL_HISTORY vs DOMAIN_EXPERT_VALIDATED → DISAGREEMENT
```

### Complete disagreement

多个字段不同：

```text
仍然只产生一个 DISAGREEMENT
```

### Domain Review

```text
DISAGREEMENT
→ DOMAIN_REVIEW_REQUIRED
→ FINAL_REVIEW
```

### No majority vote

AST 检查：

```text
majority_vote
majority
average
pick_winner
auto_select
```

不得存在。

### Versioning

```text
reviewed annotation
→ revision
→ new annotation_version
```

原对象不变。

### Agreement rate

```text
0 case → N/A
10/10 → 100%
8/10 → 80%
```

---

# 十九、G3 Gate

本阶段不改变 G3 Gate。

保持：

```text
G1 BLOCKED
→ G3 Evidence BLOCKED
```

即使：

```text
synthetic annotation
```

全部 REVIEWED：

也不能升级：

```text
G3 READY
```

Synthetic 只能验证 Procedure。

---

# 二十、Security

Annotation Review Contract 禁止：

```text
API key
Authorization
password
DATABASE_URL
SQL
raw LLM response
tool raw payload
embedding
vector
stack trace
```

允许：

```text
case_id
project_id
dataset_version
annotation_version
annotator_id
review_status
dependency flags
impact flags
business outcome
reference_type
```

---

# 二十一、Production Isolation

新增 Architecture Audit：

```text
tests/test_conversation_context_annotation_review_architecture.py
```

AST 检查：

```text
backend/app/
```

不得出现：

```text
AnnotationReviewService
AnnotationComparisonService
DomainReviewService
AnnotatorService
```

以及：

```text
annotation_review
annotation_comparison
domain_review
```

生产模块不得 import：

```text
tests
annotation review
evaluation review
```

---

# 二十二、文档

新增：

```text
docs/evaluation/Phase 4.1 Step 29 — Annotation Review Procedure.md
```

必须包含：

## 1. Procedure

```text
Annotator 1
    ↓
Annotator 2
    ↓
Comparison
    ├── AGREEMENT
    └── DISAGREEMENT
            ↓
      Domain Review
            ↓
       FINAL REVIEW
```

## 2. Independence

第二位标注员不能先看第一位结果。

## 3. Comparison

Dependency / Impact / Outcome / Reference。

## 4. Disagreement

禁止 majority vote / average / auto-select。

## 5. Review Status

DRAFT / REVIEWED / DISPUTED。

## 6. Versioning

dataset_version / annotation_version。

## 7. Statistics

只做 descriptive statistics。

## 8. G3

Synthetic procedure 不升级 G3。

## 9. Security

允许/禁止字段。

## 10. Production Isolation

Annotation Review 不进入 runtime。

## 11. Deferred

```text
真实 Annotator
真实 Reviewer
Annotation UI
Annotation Storage
真实 G3
G4 Evaluation
ContextSelector
SelectionPolicy
```

---

# 二十三、验证命令

只运行：

```powershell
python -m pytest -q tests/test_conversation_context_annotation_review.py
```

然后：

```powershell
python -m pytest -q tests/test_conversation_context_annotation_review_architecture.py
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

# 二十四、Git Diff

必须：

```text
Production code = 0
DB schema = 0
DB writes = 0
Network = 0
LLM = 0
```

只允许：

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

# 二十五、最终报告

严格：

```text
Phase 4.1 Step 29 完成报告

1. Annotation Procedure
2. Annotator Independence
3. Comparison
4. Agreement
5. Disagreement
6. Domain Review
7. Review Status
8. Versioning
9. Dependency
10. Impact
11. Business Outcome
12. Reference
13. Agreement Statistics
14. G3
15. Security
16. Production Isolation
17. Tests
18. Architecture Audit
19. Compileall
20. Git Diff
21. Production Code
22. DB / Network / LLM
23. Current Limitations

G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED

Phase 4.1 Step 29 READY
Phase 4.1 Step 29 STOP
```

# 二十六、硬停止

完成后：

**立即 STOP。**

不要进入：

```text
Step 30 Real G2
Step 31 G4 Evaluation
ContextSelector
SelectionPolicy
Memory
Summary
Tokenizer
```

等待下一步指令。
