你现在开始执行：

# Phase 3.12 Step 32：Real Annotation Execution Contract

## 一、阶段目标

基于 Phase 3.12 Step 24～31 已冻结的：

```text
ContextDependencyAnnotation
ContextImpactAnnotation
AnnotationEvidence
Annotation Procedure
Finalization Contract
Real Evidence Import Contract
```

本阶段只解决一个问题：

> **冻结未来“真实脱敏数据人工标注”应该如何执行，但本阶段不执行真实人工标注。**

目标流程：

```text
REAL_DEIDENTIFIED Evidence Candidate
        ↓
Case Sampling
        ↓
Annotator 1 Input
        ↓
Annotator 2 Input
        ↓
Independent Annotation
        ↓
Step 29 Compare
        ↓
Agreement / Disagreement
        ↓
Domain Review
        ↓
Step 30 Finalization
        ↓
Future G3 Evidence
```

本阶段仍然：

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 = Evidence BLOCKED
G4 = BLOCKED
ContextSelector = BLOCKED
```

---

# 二、严格范围

## 允许

只允许修改：

```text
tests/
docs/
```

优先：

```text
tests/fixtures/conversation_context/
tests/test_real_annotation_execution_contract.py
docs/evaluation/
```

如果已有更合适的 test-local 文件结构，遵循现有结构。

---

## 禁止

本阶段禁止修改：

```text
backend/app/
```

禁止：

```text
生产代码
Conversation
AIOrchestrator
RAG
Tool
TextToSQL
LLM
Router
ContextSelector
```

禁止：

```text
真实 WMS 数据
真实 PII
真实人工标注
真实数据库
DeepSeek
网络
```

禁止：

```text
G1
G2
G4
Selection Strategy
```

禁止新增：

```text
数据库表
Repository
ORM Model
API
UI
Annotation Storage
```

---

# 三、开始前必须阅读

先阅读 Phase 3.12：

```text
Step 24
Step 25
Step 26
Step 27
Step 28
Step 29
Step 30
Step 31
```

重点确认：

```text
Dependency Annotation
Impact Annotation
Evidence Provenance
Annotation Procedure
Annotation Comparison
Domain Review
Finalization
Real Evidence Import
```

不要重新设计已有 Contract。

---

# 四、核心原则

本阶段不是重新设计 Annotation Schema。

必须复用：

```text
Step 24 Annotation Schema
Step 28 AnnotatedEvidence
Step 29 Annotation Procedure
Step 30 Finalization
Step 31 Real Evidence Import
```

只新增：

```text
Execution Contract
```

即：

> 如何把一个已经通过 Real Evidence Import Contract 的 candidate case，安全地交给两个独立 annotator。

---

# 五、Case Sampling Contract

未来真实数据可能有：

```text
N cases
```

本阶段定义：

```text
sample_cases(...)
```

但必须是：

```text
deterministic
explicit
auditable
```

禁止：

```text
random.random()
random.sample()
time-based sampling
LLM sampling
```

---

## 推荐

优先使用：

```text
explicit case_id list
```

例如：

```text
["case_001", "case_007", "case_013"]
```

如果需要从全集生成 sample：

必须使用：

```text
sorted(case_id)
```

再按显式：

```text
start
limit
```

选择。

不要引入随机 seed。

---

# 六、Sampling 不得修改 Evidence

Sampling 只能：

```text
select cases
```

不能：

```text
modify case
remove turns
rewrite content
redact content
normalize content
merge cases
split cases
change roles
change project_id
```

也不能：

```text
自动删除“困难案例”
```

---

# 七、Sampling Result

新增 test-local DTO，例如：

```text
AnnotationSamplingResult
```

只包含最小字段：

```text
dataset_version
case_ids
sample_count
```

如果已有 DTO 风格更适合，可以复用。

要求：

```text
frozen
```

---

# 八、Sampling Validation

必须检查：

### 1. dataset_version

不能为空。

### 2. case_id

不能为空。

### 3. case_id 唯一

重复：

```text
DUPLICATE_CASE_ID
```

### 4. case 必须存在

不存在：

```text
CASE_NOT_FOUND
```

### 5. 不允许跨 dataset version

如果：

```text
requested dataset_version != evidence.dataset_version
```

：

```text
DATASET_VERSION_MISMATCH
```

不能自动转换。

---

# 九、Annotator Independence

两个 annotator：

```text
annotator_1
annotator_2
```

必须：

```text
annotator_1 != annotator_2
```

否则：

```text
ANNOTATOR_NOT_INDEPENDENT
```

---

# 十、Annotator Input Isolation

这是本阶段最重要的 Contract。

Annotator 1 输入：

```text
case
+
annotation instructions
```

Annotator 2 输入：

```text
same case
+
same annotation instructions
```

但是：

```text
Annotator 1
```

不能看到：

```text
Annotator 2 annotation
```

并且：

```text
Annotator 2
```

不能看到：

```text
Annotator 1 annotation
```

禁止：

```text
previous_annotation
review_hint
agreement_hint
disagreement_hint
```

进入另一个 annotator 的输入。

---

# 十一、不得自动生成 Annotation

本阶段：

```text
Execution Contract
```

不能自己产生：

```text
early_constraint=true
recent_context=true
business_outcome=...
```

禁止：

```text
LLM judge
heuristic label
keyword label
majority vote
```

Annotation 必须来自未来：

```text
human annotator
```

---

# 十二、Annotation Input DTO

可以定义 test-local：

```text
AnnotatorCaseInput
```

建议：

```text
dataset_version
case_id
project_id
turns
```

但注意：

不要把：

```text
annotator_id
previous_annotation
review_status
```

放进另一个 annotator 的 case 内容中。

`annotator_id` 可以作为 execution metadata，但不能成为待标注内容的一部分。

---

# 十三、Input Immutability

Annotator Input 不允许修改原 Evidence。

测试：

```text
input_before == input_after
```

未来 annotator 的修改只能产生：

```text
new Annotation object
```

不能修改：

```text
raw evidence
```

---

# 十四、Version Contract

必须严格区分：

```text
dataset_version
annotation_version
```

例如：

```text
dataset_version = wms-prod-2026-10
annotation_version = annotation-v1
```

规则：

```text
dataset_version
=
Evidence version

annotation_version
=
Annotation schema/revision version
```

不能混用。

---

# 十五、Revision

如果未来修改某个 case 的 annotation：

禁止：

```text
overwrite existing reviewed annotation
```

必须：

```text
new annotation_version
```

例如：

```text
annotation-v1
→ annotation-v2
```

原版本保持 immutable。

---

# 十六、Execution Result

新增 test-local：

```text
RealAnnotationExecutionResult
```

保持非常小。

建议：

```text
dataset_version
cases_presented
annotator_1
annotator_2
independence_valid
isolation_valid
blocked_cases
failure_codes
```

不要增加：

```text
score
accuracy
agreement_rate
winner
ranking
quality_score
```

因为这些属于后续 Evidence Analysis。

---

# 十七、Failure Codes

保持简单。

至少：

```text
DATASET_VERSION_MISMATCH
CASE_NOT_FOUND
DUPLICATE_CASE_ID
ANNOTATOR_NOT_INDEPENDENT
ANNOTATOR_INPUT_LEAK
INVALID_CASE
```

不要创建几十个 taxonomy。

---

# 十八、禁止泄露

Annotator execution contract 不得包含：

```text
API_KEY
Authorization
password
DATABASE_URL
SQL
stack_trace
raw_llm_response
embedding
vector
tool_raw_payload
```

复用 Step 28 / Step 31 的安全扫描规则。

---

# 十九、Project ID

继续保持：

```text
project_id
```

只是业务上下文标识。

不要把它解释为：

```text
authorization
tenant_id
security boundary
```

不要在本阶段新增：

```text
project authorization
```

---

# 二十、Synthetic Fixture

因为当前：

```text
G1 = BLOCKED
```

所以不能使用真实 WMS evidence。

新增：

```text
tests/fixtures/conversation_context/real_annotation_execution_cases.yaml
```

注意：

文件名可以叫：

```text
real_annotation_execution
```

但内容必须明确：

```text
SYNTHETIC_ONLY
```

不要伪装成真实数据。

---

# 二十一、Fixture 至少覆盖

建议 6 个 scenario：

```text
1. normal independent annotation
2. duplicate case
3. missing case
4. dataset version mismatch
5. same annotator
6. annotator input leakage
```

不要增加大量案例。

---

# 二十二、Contract Tests

新增：

```text
tests/test_real_annotation_execution_contract.py
```

至少测试：

### Sampling

```text
explicit case ids
deterministic order
duplicate case
missing case
version mismatch
```

### Independence

```text
annotator1 != annotator2
```

### Isolation

确认：

```text
annotator1 input
```

不包含：

```text
annotator2 output
```

以及反向。

### Immutability

确认：

```text
raw evidence unchanged
```

### Version

确认：

```text
dataset_version != annotation_version
```

不能混用。

### Security

确认：

```text
forbidden fields rejected
```

---

# 二十三、与 Step 29 / Step 30 集成

不要重新实现：

```text
compare_annotations()
resolve_annotation_status()
finalize_annotated_evidence()
```

只验证：

```text
Execution Contract output
```

可以作为：

```text
Step 29 input
```

但本阶段不真正完成一套完整 annotation。

---

# 二十四、禁止自动 Review

本阶段不得自动：

```text
AGREEMENT
DISAGREEMENT
REVIEWED
DISPUTED
```

这些只能来自：

```text
annotator comparison / domain review
```

本阶段只验证：

```text
Execution Contract
```

---

# 二十五、G3 状态

即使：

```text
Execution Contract = PASS
```

仍然：

```text
G1 = BLOCKED
G2 = INSUFFICIENT
G3 = EVIDENCE BLOCKED
G4 = BLOCKED
ContextSelector = BLOCKED
```

绝对不要把 synthetic fixture 晋升成：

```text
REAL_DEIDENTIFIED
```

---

# 二十六、Documentation

新增：

```text
docs/evaluation/Phase 3.12 Step 32 — Real Annotation Execution Contract.md
```

内容：

## 1. Purpose

为什么需要独立人工标注执行 Contract。

## 2. Sampling

```text
explicit deterministic case selection
```

## 3. Annotator Independence

```text
A1 != A2
```

## 4. Input Isolation

```text
A1 不看到 A2
A2 不看到 A1
```

## 5. Versioning

```text
dataset_version
annotation_version
```

## 6. Revision

```text
new annotation_version
```

## 7. Security

禁止敏感字段。

## 8. Current State

```text
Synthetic only
No real annotation
G1 blocked
G3 blocked
```

## 9. Deferred

```text
Real annotator onboarding
Real evidence annotation
Domain review execution
G3 evidence readiness
```

---

# 二十七、验证命令

运行：

```powershell
python -m pytest -q tests/test_real_annotation_execution_contract.py
```

然后回归：

```powershell
python -m pytest -q tests/test_annotation*
```

以及：

```powershell
python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests scripts
```

---

# 二十八、Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff
```

确认：

```text
backend/app = unchanged
DB schema = unchanged
DB writes = 0
DeepSeek = 0
Network = 0
Real WMS data = 0
```

允许修改：

```text
tests/
docs/
```

---

# 二十九、最终报告

严格：

```text
Phase 3.12 Step 32 完成报告

1. Sampling Contract
2. Annotator Independence
3. Input Isolation
4. Versioning
5. Revision
6. Security
7. Failure Codes
8. Synthetic Fixture
9. Step 29 Compatibility
10. Step 30 Compatibility
11. Tests
12. Full Regression
13. compileall
14. Production Code
15. DB
16. G1
17. G2
18. G3
19. G4
20. Deferred Items

Phase 3.12 Step 32 READY
Phase 3.12 Step 32 STOP
```

---

# 三十、硬停止

完成后立即停止。

不要进入：

```text
Step 33
Real Annotation
Real WMS Data
ContextSelector
G1 promotion
G2 measurement
G4 strategy selection
```

只冻结：

```text
Real Annotation Execution Contract
```

等待下一步指令。
