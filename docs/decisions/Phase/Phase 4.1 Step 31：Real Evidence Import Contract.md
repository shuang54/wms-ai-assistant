你现在开始执行：

# Phase 4.1 Step 31：Real Evidence Import Contract

## 一、阶段目标

Phase 4.1 Step 30 已完成：

```text
Reviewed Annotation
        ↓
Finalization / Integrity Check
        ↓
AnnotationEvidenceIntegrityResult
        ↓
Future G3 Input Contract
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

> **未来真实 Evidence 如何安全、可追溯地进入 Evaluation Pipeline。**

本阶段只建立：

```text
Future Real Evidence
        ↓
Import Contract
        ↓
Provenance / De-identification / Schema Validation
        ↓
G1 Candidate Input
```

**不要导入真实 WMS 数据。**

**不要执行真实 G1。**

---

# 二、严格范围

## 允许

只允许：

```text
tests/
docs/evaluation/
```

必要时可以新增 test-local：

```text
RealEvidenceImportContract
RealEvidenceImportResult
```

但必须放在：

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
真实 WMS 数据
真实客户/供应商/订单/库存/员工数据
生产数据库
DeepSeek
SiliconFlow
Network
DB
ContextSelector
SelectionPolicy
SelectionBudget
G2
G4
真实 Annotation
Annotation UI
Annotation Storage
Annotation API
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
Step 30 finalization
```

---

# 三、开始前必须阅读

先阅读：

```text
tests/test_conversation_selection_evidence_data_audit.py

tests/test_conversation_context_annotation_workflow.py
tests/test_conversation_context_annotation_review.py
tests/test_conversation_context_annotation_finalization.py

docs/evaluation/Phase 4.1 Step 25 — Evidence Procedure.md
docs/evaluation/Phase 4.1 Step 26 — Real Evidence Artifact.md
docs/evaluation/Phase 4.1 Step 27 — Evidence Provenance.md
docs/evaluation/Phase 4.1 Step 28 — Annotation Workflow.md
docs/evaluation/Phase 4.1 Step 29 — Annotation Review Procedure.md
docs/evaluation/Phase 4.1 Step 30 — Annotation Evidence Finalization.md
```

必须复用已有 Contract。

不要重新设计：

```text
dataset_version
source_type
EvidenceProvenance
AnnotatedEvidence
CaseAnnotation
ReviewStatus
```

---

# 四、Real Evidence Import 的定位

本阶段明确区分：

```text
Import
≠
G1 Evaluation
```

Import 只负责：

```text
输入格式正确
来源声明正确
版本声明正确
脱敏声明存在
结构符合 Evidence Artifact
```

Import 成功：

```text
Candidate accepted
```

不代表：

```text
G1 READY
```

更不代表：

```text
G3 READY
```

---

# 五、Source Type

未来真实 Evidence 必须：

```text
source_type = REAL_DEIDENTIFIED
```

禁止 Import Contract 接受：

```text
SYNTHETIC_ONLY
```

作为 Real Evidence Import。

注意：

现有 Synthetic Fixture 仍然可以继续用于测试 Contract。

不要删除 Synthetic。

---

# 六、Dataset Version

Import 必须要求：

```text
dataset_version
```

满足：

```text
non-empty
stable
explicit
```

禁止：

```text
auto-generated
timestamp-derived
random
inferred
```

例如：

```text
wms-v1
wms-2026-09
```

可以作为显式声明。

本阶段不规定具体生产命名规则。

---

# 七、Evidence Artifact Schema

未来 Real Evidence Artifact 继续使用 Step 26：

```yaml
dataset_version: "..."
source_type: REAL_DEIDENTIFIED

conversations:
  - case_id: "..."
    project_id: "..."
    turns:
      - role: USER
        content: "..."
      - role: ASSISTANT
        content: "..."
```

允许字段：

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

不要增加：

```text
annotation
reference
impact
strategy
score
ranking
winner
selection
token_estimate
assistant_request_id
conversation_id
turn_id
provider_request_id
```

---

# 八、De-identification Attestation

Real Evidence Import 必须要求一个明确的：

```text
de_identification_attestation
```

本阶段只定义 Contract，不实现真实脱敏系统。

建议最小字段：

```text
attested = true
method = "external_process"
```

可以根据现有 Step 25/26 Contract 调整。

禁止：

```text
attested = false
missing attestation
auto-assume de-identified
```

没有 Attestation：

```text
IMPORT_BLOCKED
```

---

# 九、禁止生产身份信息

Real Evidence Artifact 中禁止出现：

```text
姓名
手机号
邮箱
身份证
银行卡
家庭地址
精确个人联系方式
```

以及明显的生产系统敏感信息：

```text
API Key
Authorization
Password
DATABASE_URL
数据库连接串
内部 Token
```

测试使用 synthetic toxic payload。

不要使用真实个人信息测试。

---

# 十、Production Correlation ID

继续 Step 26 规则：

Real Evidence Artifact 禁止直接携带：

```text
conversation_id
assistant_request_id
turn_id
provider_request_id
```

原因：

Evaluation Dataset 应该与生产 Trace identity 解耦。

如果未来确实需要 correlation：

另建受控 mapping layer。

本阶段不实现。

---

# 十一、Project ID

允许：

```text
project_id
```

因为它是 Evidence context/grouping 信息。

但是：

```text
project_id
```

不是：

```text
authorization
```

不要在本阶段新增：

```text
tenant_id
user_id
owner_id
permission
ACL
```

---

# 十二、Content Contract

允许：

```text
USER content
ASSISTANT content
```

但必须：

```text
string
non-null
```

空字符串是否允许，按照 Step 26 已冻结规则。

不要重新设计。

禁止：

```text
raw prompt object
tool payload
raw LLM response
embedding
vector
SQL result object
ORM object
```

---

# 十三、Role Contract

只允许：

```text
USER
ASSISTANT
```

禁止：

```text
SYSTEM
TOOL
FUNCTION
DEVELOPER
```

Evaluation Evidence 只保留：

```text
user-visible conversation
```

---

# 十四、Case ID

要求：

```text
case_id
```

在同一个：

```text
dataset_version
```

中唯一。

例如：

```text
v1/case-001
v1/case-002
```

同一 case_id 出现在不同 dataset_version：

```text
允许
```

同一 dataset_version 出现重复：

```text
IMPORT_BLOCKED
```

不要自动去重。

不要：

```text
first wins
last wins
```

---

# 十五、Conversation Integrity

每个 conversation：

必须：

```text
case_id
project_id
turns
```

并且：

```text
turns
```

至少包含一个：

```text
USER
```

如果只有：

```text
ASSISTANT
```

必须：

```text
IMPORT_BLOCKED
```

本阶段不要要求一定存在 ASSISTANT。

因为未来可能存在：

```text
user-only
```

样本。

---

# 十六、Turn Integrity

每个 turn：

```text
role
content
```

必须存在。

禁止：

```text
turn_id
assistant_request_id
```

等生产 correlation 字段。

不要在 Import 阶段自动生成新的生产 ID。

---

# 十七、Provenance

Import Result 必须保留：

```text
EvidenceProvenance
```

或者复用 Step 27 的 test-local provenance contract。

至少：

```text
dataset_version
source_type
```

不要新增：

```text
machine_id
hostname
username
absolute_path
git_sha
DATABASE_URL
database_host
container_id
```

---

# 十八、Import Result

建议新增：

```text
RealEvidenceImportResult
```

最小字段：

```text
accepted
dataset_version
source_type
cases_imported
blocked_cases
failure_codes
provenance
```

建议 failure codes 控制在少量：

```text
INVALID_SOURCE_TYPE
MISSING_DATASET_VERSION
DATASET_VERSION_MISMATCH
MISSING_DEIDENTIFICATION_ATTESTATION
DUPLICATE_CASE_ID
INVALID_ROLE
INVALID_TURN
INVALID_CONVERSATION
FORBIDDEN_FIELD
PRODUCTION_CORRELATION_ID
```

不要建立几十个错误类型。

---

# 十九、Import Must Not Modify Input

输入 Artifact：

```text
immutable
```

Import：

```text
read-only
```

禁止：

```text
自动修复
自动去重
自动删字段
自动脱敏
自动补字段
自动改 role
```

发现问题：

```text
BLOCKED
```

而不是：

```text
修复后继续
```

---

# 二十、Security

继续复用：

```text
validate_no_forbidden_annotation_fields
```

或者 Step 26/28 已有安全 Contract。

不要重新建立第二套 forbidden-field scanner。

测试至少：

```text
prompt
SQL
API_KEY
Authorization
password
DATABASE_URL
stack_trace
raw_response
embedding
vector
tool_raw_payload
```

均：

```text
BLOCKED
```

---

# 二十一、Synthetic Test Fixtures

新增：

```text
tests/fixtures/conversation_context/real_evidence_import_cases.yaml
```

只能是 synthetic。

至少覆盖：

### Case 1

```text
valid REAL_DEIDENTIFIED
attestation=true
→ PASS
```

### Case 2

```text
SYNTHETIC_ONLY
→ BLOCKED
```

### Case 3

```text
missing dataset_version
→ BLOCKED
```

### Case 4

```text
missing attestation
→ BLOCKED
```

### Case 5

```text
duplicate case_id
→ BLOCKED
```

### Case 6

```text
SYSTEM role
→ BLOCKED
```

### Case 7

```text
production correlation ID
→ BLOCKED
```

### Case 8

```text
SQL / prompt / API key payload
→ BLOCKED
```

### Case 9

```text
multiple dataset versions mixed
→ BLOCKED
```

### Case 10

```text
USER-only conversation
→ PASS
```

### Case 11

```text
assistant-only conversation
→ BLOCKED
```

### Case 12

```text
input contains extra unknown field
→ BLOCKED
```

不要 silently ignore unknown fields。

---

# 二十二、G1 Relationship

这是本阶段最重要的 Gate。

Import：

```text
PASS
```

只表示：

```text
Real Evidence Candidate structurally valid
```

不能改变：

```text
G1 = BLOCKED
```

除非未来存在真实：

```text
REAL_DEIDENTIFIED
```

数据 + 正式 Attestation + G1 evaluation。

本阶段：

```text
G1 Evaluation = 0
```

不要修改 Step 25 G1。

---

# 二十三、G2 / G3 / G4

本阶段全部保持：

```text
G2 = INSUFFICIENT
G3 Evidence = BLOCKED
G4 = BLOCKED
```

即使 synthetic import PASS。

不要：

```text
Synthetic → G1
Synthetic → G2
Synthetic → G3
```

---

# 二十四、Tests

新增：

```text
tests/test_conversation_context_real_evidence_import.py
tests/test_conversation_context_real_evidence_import_architecture.py
```

至少覆盖：

```text
valid import
source type
dataset version
attestation
duplicate case
invalid role
invalid turn
invalid conversation
production correlation ID
forbidden field
mixed version
user-only
assistant-only
unknown field
input immutability
deterministic result
G1 remains blocked
```

建议：

```text
15～25 tests
```

不要机械堆数量。

---

# 二十五、Architecture Audit

继续检查：

```text
backend/app/
```

必须：

```text
Production Code = 0
```

不得新增：

```text
real_evidence
evidence_import
evaluation_import
```

等生产模块。

test-local 实现只能位于：

```text
tests/
```

---

# 二十六、Documentation

新增：

```text
docs/evaluation/Phase 4.1 Step 31 — Real Evidence Import Contract.md
```

内容：

## 1. Purpose

```text
Real Evidence
    ↓
Import Contract
```

## 2. Required Fields

```text
dataset_version
source_type
de_identification_attestation
conversations
```

## 3. Forbidden Fields

完整列出：

```text
production IDs
secrets
prompt/raw response
SQL
embedding
```

## 4. Provenance

复用 Step 27。

## 5. Import vs G1

明确：

```text
Import PASS ≠ G1 READY
```

## 6. Synthetic Limitation

```text
Synthetic only
No real WMS import
```

## 7. Deferred

```text
Real data
G1 evaluation
G2
G4
ContextSelector
```

---

# 二十七、测试命令

只执行：

```powershell
python -m pytest -q tests/test_conversation_context_real_evidence_import.py
python -m pytest -q tests/test_conversation_context_real_evidence_import_architecture.py
```

然后：

```powershell
python -m compileall -q backend tests
```

回归：

```powershell
python -m pytest -q tests/test_conversation_context_annotation_finalization.py
python -m pytest -q tests/test_conversation_context_annotation_finalization_architecture.py
python -m pytest -q tests/test_conversation_context_annotation_review.py
```

不要：

```text
RUN_DB_TESTS
全量 pytest
真实 WMS 数据
LLM
Network
```

---

# 二十八、Git Diff

检查：

```powershell
git status --short
git diff --stat
git diff
```

确认：

```text
Production Code = 0
DB Schema = 0
DB Writes = 0
Network = 0
LLM = 0
Tokenizer = 0
Prompt = unchanged
Step 21 dataset = unchanged
Step 24~30 contracts = unchanged
```

允许：

```text
tests/
docs/evaluation/
```

---

# 二十九、最终报告

严格：

```text
Phase 4.1 Step 31 完成报告

1. Real Evidence Import Contract
2. Source Type
3. Dataset Version
4. De-identification Attestation
5. Evidence Artifact Schema
6. Production Correlation ID
7. Security
8. Case / Turn Integrity
9. Provenance
10. Import Result
11. Input Immutability
12. Synthetic Fixtures
13. G1 Relationship
14. G2 / G3 / G4
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

Phase 4.1 Step 31 READY
Phase 4.1 Step 31 STOP
```

---

# 三十、硬停止

完成后立即 STOP。

不要进入：

```text
Step 32 Real Annotation
Step 33 G2
Step 34 G4
ContextSelector
SelectionPolicy
SelectionBudget
```

除非收到明确下一步指令。

本阶段只建立：

```text
Future Real Evidence
        ↓
Import Contract
        ↓
G1 Candidate Input
```
