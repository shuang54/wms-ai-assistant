# Phase 4.1 Step 26 — Real WMS Evidence Input Boundary Audit

继续 Phase 4.1。

当前状态：

```text
Step 21
→ Synthetic WMS Multiturn Dataset

Step 22
→ Selection Strategy Decision Gate

Step 23
→ Data Source Audit
→ G1 = BLOCKED

Step 24
→ Annotation / Impact Rubric

Step 25
→ Evidence Collection Procedure
→ G1 = BLOCKED
→ G2 = INSUFFICIENT
→ G3 Contract = READY
→ G3 Evidence = BLOCKED
→ G4 = BLOCKED
→ Selection Strategy = BLOCKED
```

本步骤只解决：

> **明确未来 Real De-identified WMS Evidence 应该以什么最小结构进入 Evaluation Layer。**

本阶段：

**不导入真实数据。**

**不实现脱敏工具。**

**不实现 ContextSelector。**

**不实现任何 History Selection Strategy。**

---

# 一、严格目标

冻结一个：

```text
Real WMS Evidence Input Boundary
```

最终：

```text
External / Offline De-identified Artifact
        ↓
Evidence Dataset Loader
        ↓
EvidenceDatasetValidator
        ↓
G1
        ↓
G2 / G3 / G4
```

本阶段只定义：

```text
Artifact Schema
Loader Contract
Validation Contract
Privacy Boundary
Provenance Boundary
```

不要运行真实数据。

---

# 二、严格禁止

禁止：

```text
真实 WMS 数据导入 repo
真实客户数据
真实供应商数据
真实订单号
真实库存数据
真实员工信息
真实手机号
真实邮箱
真实数据库导出
```

禁止新增：

```text
deidentify.py
anonymize.py
redact.py
```

禁止：

```text
ContextSelector
SelectionPolicy
Recent N
Recent N+First
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

禁止：

```text
DeepSeek
SiliconFlow
PostgreSQL
生产数据库
网络请求
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

---

# 三、开始前必须阅读

阅读当前：

```text
tests/test_conversation_context_evidence_collection_procedure.py
tests/test_conversation_context_evidence_collection_procedure_architecture.py

tests/test_conversation_context_rubric.py
tests/test_conversation_context_evidence_data_audit.py

tests/fixtures/conversation_context/wms_multiturn_conversations.yaml

docs/evaluation/Phase 4.1 Step 25 — Evidence Collection Procedure.md
```

重点确认 Step 25 已经冻结的：

```text
EvidenceDatasetValidator
G1
G2
G3
G4
EvidenceCollectionReport
```

不要重复实现。

---

# 四、Real Evidence Artifact Schema

本阶段只定义一个：

```text
Real WMS Evidence Artifact
```

建议最小结构：

```yaml
dataset_version: "..."
source_type: REAL_DEIDENTIFIED

conversations:
  - case_id: "..."
    project_id: "..."
    turns:
      - role: user
        content: "..."
      - role: assistant
        content: "..."
      - role: user
        content: "..."
```

严格要求：

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

---

# 五、Real Artifact 不包含 Evaluation Derived Fields

Real Evidence Artifact 中禁止预先写入：

```text
annotation
reference
impact
strategy_id
selected_turns
selection_result
score
ranking
winner
tokens
```

原因：

```text
Raw Evidence
    ↓
Evaluation
```

不能：

```text
Raw Evidence
    ↓
预先带结论
```

---

# 六、Real Artifact 与 Synthetic Dataset 分离

不要修改：

```text
tests/fixtures/conversation_context/wms_multiturn_conversations.yaml
```

当前 Synthetic Dataset 保持：

```text
SYNTHETIC_ONLY
```

Real Artifact 使用：

```text
REAL_DEIDENTIFIED
```

两个 source type 必须严格区分。

禁止：

```text
SYNTHETIC_ONLY
→ REAL_DEIDENTIFIED
```

自动转换。

---

# 七、Loader Contract

新增一个：

```text
test-local loader contract
```

例如：

```python
load_real_wms_evidence_artifact(path)
```

但：

**只定义 Contract / Test Stub。**

不要真正读取仓库外文件。

Loader 未来应该：

```text
external artifact
    ↓
parse
    ↓
EvidenceDatasetValidator
```

当前只验证：

```text
path / source_type / schema
```

不要增加 IO framework。

---

# 八、Source Provenance

Real Artifact 必须具备最小 provenance：

```text
dataset_version
source_type
```

本阶段不增加：

```text
customer
factory
employee
database
server
host
file_path
absolute_path
credential
```

不要把：

```text
D:\...
C:\...
\\server\...
postgresql://...
```

写入 Evaluation Report。

---

# 九、Privacy Boundary

冻结：

```text
Real WMS data must be de-identified outside repository.
```

进入 Evaluation Layer 的只能是：

```text
REAL_DEIDENTIFIED
```

本阶段不负责：

```text
识别个人信息
自动脱敏
自动匿名化
自动替换真实订单号
```

如果发现未来真实数据无法证明已经脱敏：

```text
G1 = BLOCKED
```

不能“默认认为安全”。

---

# 十、Evidence Validation

复用 Step 25：

```text
EvidenceDatasetValidator
```

必须检查：

### Dataset

```text
dataset_version 非空
source_type 合法
conversations 非空
```

### Conversation

```text
case_id 非空
project_id 非空
turns 非空
```

### Turns

```text
role ∈ {user, assistant}
content 非空
last turn = user
```

### Security

不能出现：

```text
API key
Authorization
DATABASE_URL
postgresql://
postgres://
psycopg2.connect
password
```

---

# 十一、Real Artifact 不能伪造 G1

严格：

```text
source_type = SYNTHETIC_ONLY
→ G1 BLOCKED

source_type = REAL_DEIDENTIFIED
+ valid artifact
+ no sensitive
+ sample_count > 0
+ conversation_count > 0
+ turn_count > 0
→ G1 READY
```

测试：

```text
synthetic cannot upgrade to real
```

---

# 十二、G2 输入边界

如果未来：

```text
G1 = READY
```

G2 才可以：

```text
turn_count
character_count
P50
P90
P95
P99
Max
```

当前仍然：

```text
G1 = BLOCKED
```

所以本步骤：

**不要生成真实 G2 statistics。**

只测试：

```text
G2 blocked until G1
```

---

# 十三、G3 输入边界

G3 未来使用：

```text
Real Evidence
    +
Human Annotation
```

但：

```text
Real Evidence Artifact
```

本身不能包含 annotation。

因此未来：

```text
Artifact
    ↓
Annotation Workflow
    ↓
Annotated Evidence
```

本步骤不实现 Annotation Workflow。

---

# 十四、G4 输入边界

G4 需要：

```text
Full History Reference
+
Candidate Selected History
+
Outcome Comparison
```

但：

```text
Real Evidence Artifact
```

本身只保存历史。

不要在 artifact 中保存：

```text
selected_history
candidate_history
strategy_id
```

这些属于未来 evaluation execution。

---

# 十五、Project Isolation

Real Artifact 中保留：

```text
project_id
```

但它只是：

```text
evaluation grouping / context identity
```

不是：

```text
authorization
```

当前系统仍没有 project-level authorization。

文档明确：

```text
project_id != security boundary
```

不要在本步骤增加 auth。

---

# 十六、Conversation ID

Real Artifact：

**不要使用生产 conversation_id。**

建议：

```text
case_id
```

作为 Evaluation Identity。

不要出现：

```text
conversation_id
assistant_request_id
turn_id
provider_request_id
```

因为这些属于生产运行时 correlation identifiers。

---

# 十七、Turn ID

Real Artifact 中：

不要保存：

```text
database turn_id
```

Evaluation Artifact 只依赖：

```text
turn position
```

即：

```text
turns[0]
turns[1]
turns[2]
```

当前用户 turn：

```text
last turn
```

由 Evaluation Layer 判断。

---

# 十八、Content Boundary

Real Artifact 允许：

```text
user content
assistant content
```

但 Evaluation Report 禁止输出：

```text
完整 content
完整 answer
完整 SQL
完整 RAG chunk
```

Report 只输出：

```text
counts
percentages
distributions
status
```

---

# 十九、Evidence Version

增加：

```text
dataset_version
```

用于：

```text
同一数据集不同版本之间的可追溯性。
```

不要增加：

```text
git_sha
machine_id
hostname
absolute_path
```

这些属于运行环境信息，不属于 Evidence Contract。

---

# 二十、Test Fixture

不要新增真实数据文件。

可以在：

```text
tests/test_conversation_context_real_evidence_boundary.py
```

中使用**极小 synthetic stand-in**模拟：

```text
REAL_DEIDENTIFIED
```

但必须明确：

```text
This is contract simulation only.
It is NOT real WMS evidence.
```

测试名称和变量名不要造成真实数据错觉。

---

# 二十一、测试要求

新增：

```text
tests/test_conversation_context_real_evidence_boundary.py
```

覆盖：

### 1

Valid artifact schema。

### 2

Missing dataset_version → reject。

### 3

Invalid source_type → reject。

### 4

SYNTHETIC_ONLY cannot become REAL_DEIDENTIFIED。

### 5

REAL_DEIDENTIFIED + valid counts → G1 READY（仅 contract simulation）。

### 6

Sensitive field → G1 BLOCKED。

### 7

Empty conversations → G1 BLOCKED。

### 8

Empty turns → reject。

### 9

Invalid role → reject。

### 10

Last turn not user → reject。

### 11

Duplicate case_id → reject。

### 12

Production conversation_id → reject if present。

### 13

Production turn_id → reject if present。

### 14

Production assistant_request_id → reject if present。

### 15

Production provider_request_id → reject if present。

### 16

Absolute path → reject。

### 17

DATABASE_URL / postgres URL → reject。

### 18

No evaluation-derived fields。

禁止：

```text
annotation
impact
strategy_id
selected_turns
score
ranking
winner
```

### 19

G2 cannot run when G1 BLOCKED。

### 20

G3 cannot run when G1 BLOCKED。

### 21

G4 cannot run when G1 BLOCKED。

### 22

Deterministic validation。

---

# 二十二、Architecture Audit

新增：

```text
tests/test_conversation_context_real_evidence_boundary_architecture.py
```

确认：

### Production

```text
backend/app/
```

不能出现：

```text
real evidence loader
deidentify tool
anonymize tool
evaluation artifact importer
```

### Tests

允许：

```text
test-local loader
contract DTO
validation helper
```

### Forbidden imports

Test helper 不得依赖：

```text
sqlalchemy
psycopg
redis
celery
kafka
```

也不得：

```text
backend.app.db
```

### No runtime coupling

Real Evidence Contract：

不得 import：

```text
ConversationService
ChatApplicationService
AIOrchestrator
RAG
Tool
TextToSQL
```

---

# 二十三、Documentation

新增：

```text
docs/evaluation/Phase 4.1 Step 26 — Real WMS Evidence Input Boundary.md
```

严格包含：

## 1. Purpose

Real Evidence 进入 Evaluation Layer 的边界。

## 2. Artifact Schema

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

## 3. Forbidden Fields

完整列出：

```text
conversation_id
assistant_request_id
turn_id
provider_request_id
annotation
impact
strategy_id
selected_turns
score
ranking
winner
tokens
credentials
paths
```

## 4. Privacy

```text
De-identification happens outside repo.
```

## 5. G1

```text
REAL_DEIDENTIFIED
→ READY only if valid.
```

## 6. G2/G3/G4

全部依赖：

```text
G1 READY
```

## 7. Project Isolation

```text
project_id != authorization
```

## 8. Synthetic

明确：

```text
Step21 dataset remains SYNTHETIC_ONLY.
```

## 9. Runtime Isolation

Evaluation Artifact：

```text
does not enter production runtime.
```

## 10. Deferred

```text
Real data import
De-identification
Annotation workflow
Selection Strategy
ContextSelector
```

---

# 二十四、测试命令

只运行：

```powershell
python -m pytest -q tests/test_conversation_context_real_evidence_boundary.py
```

然后：

```powershell
python -m pytest -q tests/test_conversation_context_real_evidence_boundary_architecture.py
```

然后：

```powershell
python -m compileall -q backend tests
```

不要运行：

```text
RUN_DB_TESTS=1
pytest -q
DeepSeek
SiliconFlow
PostgreSQL
Network
```

---

# 二十五、Git Diff

检查：

```powershell
git status --short
git diff --stat
git diff
```

必须：

```text
backend/app = unchanged
Conversation runtime = unchanged
Step21 dataset = unchanged
Step22 gate = unchanged
Step24 rubric = unchanged
Step25 procedure = unchanged
```

允许：

```text
tests/
docs/evaluation/
```

---

# 二十六、最终报告

严格：

```text
Phase 4.1 Step 26 完成报告

1. Real Evidence Boundary
2. Artifact Schema
3. Dataset Validation
4. Privacy Boundary
5. Provenance
6. Production Runtime Isolation
7. Production ID Isolation
8. G1
9. G2
10. G3
11. G4
12. Synthetic Dataset
13. Project Isolation
14. Security
15. Tests
16. Compileall
17. Git Diff
18. Production Code
19. DB / Network / LLM
20. Current Limitations

G1 = BLOCKED
G2 = INSUFFICIENT
G3 Contract = READY
G3 Evidence = BLOCKED
G4 = BLOCKED
Selection Strategy = BLOCKED

Phase 4.1 Step 26 READY
Phase 4.1 Step 26 STOP
```

---

# 二十七、硬停止

完成后立即停止。

不要进入：

```text
真实数据导入
ContextSelector
SelectionPolicy
Recent N
Hybrid
Relevance
Tokenizer
max_tokens
Truncation
Summary
Memory
Agent
MCP
```

只完成：

```text
Real WMS Evidence Input Boundary
```

等待下一步指令。
