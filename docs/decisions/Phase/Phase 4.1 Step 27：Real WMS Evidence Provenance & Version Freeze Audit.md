你现在开始执行：

# Phase 4.1 Step 27：Real WMS Evidence Provenance & Version Freeze Audit

## 一、阶段目标

Phase 4.1 Step 26 已完成：

```text
Real WMS Evidence Artifact Boundary
        ↓
Dataset Validation
        ↓
G1
        ↓
G2 / G3 / G4
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

本阶段只解决一个问题：

> **当未来第一份 REAL_DEIDENTIFIED WMS Evidence Artifact 进入 Evaluation Layer 时，如何证明它“是哪一版、来自哪次采样、是否被替换、是否与报告对应”。**

本阶段：

**只做 Provenance / Version / Integrity Contract Audit。**

不导入真实数据。

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
生产 Evidence Loader
生产 Evidence Repository
```

---

# 三、开始前必须阅读

阅读：

```text
tests/test_conversation_context_real_evidence_boundary.py
tests/test_conversation_context_real_evidence_boundary_architecture.py

tests/test_conversation_context_evidence_procedure.py
tests/test_conversation_context_rubric.py

docs/evaluation/Phase 4.1 Step 26 — Real WMS Evidence Input Boundary.md
```

重点确认 Step 26 已冻结的：

```text
dataset_version
source_type
case_id
project_id
turns
role
content
```

以及：

```text
G1
G2
G3
G4
```

不要重新设计 Step 26 Artifact Schema。

---

# 四、Provenance 的职责

本阶段明确：

```text
Artifact Schema
    ≠
Provenance
```

Artifact 描述：

```text
有什么数据
```

Provenance 描述：

```text
这份数据是哪一版
```

---

# 五、冻结最小 Provenance Contract

只增加**测试/文档层面的概念**：

```text
EvidenceProvenance
```

最小字段：

```text
dataset_version
source_type
```

其中：

```text
dataset_version
```

必须：

```text
非空
稳定
可比较
```

例如：

```text
wms-conversation-v1
wms-conversation-v2
```

本阶段不要规定：

```text
semantic versioning
Git SHA
timestamp
machine id
hostname
absolute path
database id
```

---

# 六、禁止把运行环境当 Provenance

明确禁止：

```text
hostname
machine_id
username
absolute_path
DATABASE_URL
database_host
server
container_id
git_sha
```

进入：

```text
EvidenceProvenance
```

原因：

> 这些属于运行环境/部署信息，不是 Evaluation Dataset 身份。

如果未来需要审计运行环境：

另建独立 Execution Metadata Contract。

本阶段不做。

---

# 七、Dataset Version 不等于 Application Version

明确测试：

```text
dataset_version != app_version
```

禁止：

```text
dataset_version = project version
```

禁止自动从：

```text
package version
git tag
commit SHA
```

生成 dataset_version。

Dataset version 必须由 Evidence Dataset 本身声明。

---

# 八、Source Type

继续使用 Step 26：

```text
REAL_DEIDENTIFIED
SYNTHETIC_ONLY
```

规则：

```text
SYNTHETIC_ONLY
    ↓
永远不能成为 REAL_DEIDENTIFIED
```

禁止：

```text
promote
coerce
convert
auto_upgrade
```

---

# 九、Provenance Immutability

一旦：

```text
EvidenceArtifact
```

进入：

```text
Evaluation Run
```

其：

```text
dataset_version
source_type
```

不得在运行过程中修改。

不要：

```text
runtime override
```

不要：

```text
environment variable override
```

不要：

```text
CLI override
```

不要：

```text
project config override
```

---

# 十、Version Mismatch

定义未来 Evaluation 的最小行为：

如果：

```text
Artifact.dataset_version = V1
```

而：

```text
Evaluation Report.dataset_version = V2
```

则：

```text
VERSION_MISMATCH
```

不能：

```text
继续生成真实 G2/G3/G4 结果
```

本阶段只定义 Contract。

不要实现完整 Evaluation Runner。

---

# 十一、Source Type Mismatch

如果：

```text
Artifact.source_type = SYNTHETIC_ONLY
```

但：

```text
Evaluation declares REAL_DEIDENTIFIED
```

必须：

```text
SOURCE_TYPE_MISMATCH
```

不能继续。

反过来也一样。

---

# 十二、Artifact Identity

不要使用：

```text
conversation_id
assistant_request_id
turn_id
provider_request_id
```

作为：

```text
Evidence Artifact Identity
```

继续使用：

```text
case_id
```

作为 Evaluation case identity。

---

# 十三、Case ID Stability

验证：

同一 Dataset Version 内：

```text
case_id
```

必须唯一。

同一个：

```text
case_id
```

可以出现在：

```text
V1
V2
```

但这代表不同 Dataset Version 的不同数据快照。

不要要求全局永久唯一。

---

# 十四、Deterministic Version Contract

同一个 Artifact：

```text
dataset_version = V1
source_type = REAL_DEIDENTIFIED
cases = same
```

重复加载：

```text
provenance
```

必须完全一致。

不要自动生成：

```text
timestamp
UUID
random hash
```

作为 Dataset Version。

---

# 十五、Hash

本阶段**不要实现内容 hash**。

只分析：

```text
hash
```

未来可能有价值，但必须先解决：

```text
canonical serialization
normalization
encoding
field ordering
content normalization
```

否则 hash 容易产生：

```text
same dataset
→ different hash
```

本阶段明确：

```text
Artifact Integrity Hash = DEFERRED
```

---

# 十六、Report Binding

未来 Evaluation Report 必须能够表达：

```text
dataset_version
source_type
```

但：

**不要修改 Step 25 Report Schema。**

本阶段只新增：

```text
Proposed Report Provenance Contract
```

例如：

```text
dataset_version
source_type
g1_status
g2_status
g3_status
g4_status
```

如果：

```text
G1 = BLOCKED
```

则：

```text
G2/G3/G4
```

仍然不能出现伪造统计。

---

# 十七、Provenance 与 Annotation

明确：

```text
Provenance
    ↓
Dataset identity

Annotation
    ↓
Human/domain interpretation
```

两者分离。

禁止：

```text
annotation_version
```

覆盖：

```text
dataset_version
```

未来 Annotated Evidence 应同时保留：

```text
dataset_version
annotation_version
```

但本阶段只做设计，不创建真实 Annotated Evidence。

---

# 十八、Provenance 与 G1

G1 继续：

```text
SYNTHETIC_ONLY → BLOCKED
```

REAL_DEIDENTIFIED：

必须至少：

```text
dataset_version
source_type
valid artifact
de-identification attestation
sample_count > 0
conversation_count > 0
turn_count > 0
```

才能：

```text
G1 = READY
```

本阶段不创建真实 artifact，因此：

```text
G1 = BLOCKED
```

保持不变。

---

# 十九、测试

新增：

```text
tests/test_conversation_context_evidence_provenance.py
```

至少覆盖：

### Provenance

```text
valid dataset_version
empty dataset_version
whitespace dataset_version
source_type valid
source_type invalid
```

### Version

```text
V1 == V1
V1 != V2
```

### Source Type

```text
REAL_DEIDENTIFIED == REAL_DEIDENTIFIED
SYNTHETIC_ONLY != REAL_DEIDENTIFIED
```

### Mismatch

```text
VERSION_MISMATCH
SOURCE_TYPE_MISMATCH
```

### Identity

```text
case_id stable
case_id duplicate rejected
```

### Immutability

```text
dataset_version cannot be overridden
source_type cannot be overridden
```

### Environment Isolation

禁止：

```text
hostname
machine_id
git_sha
absolute_path
DATABASE_URL
database_host
```

进入 Provenance。

### Production ID Isolation

继续禁止：

```text
conversation_id
assistant_request_id
turn_id
provider_request_id
```

### Gating

```text
G1 BLOCKED
→ G2 INSUFFICIENT
→ G3 Evidence BLOCKED
→ G4 BLOCKED
```

---

# 二十、Architecture Audit

新增：

```text
tests/test_conversation_context_evidence_provenance_architecture.py
```

AST 检查：

```text
backend/app/
```

不得新增：

```text
EvidenceProvenance
EvidenceArtifact
RealEvidenceLoader
DatasetVersionResolver
```

等生产实现。

同时确认：

```text
ConversationService
ChatApplicationService
AIOrchestrator
RAG
Tool
TextToSQL
```

没有 provenance 依赖。

---

# 二十一、文档

新增：

```text
docs/evaluation/Phase 4.1 Step 27 — Evidence Provenance & Version Freeze Audit.md
```

必须包含：

## 1. Current State

```text
Real Evidence Artifact
    ↓
Step 26 Validation
    ↓
G1
```

## 2. Provenance

```text
dataset_version
source_type
```

## 3. Version Semantics

```text
dataset_version != app_version
```

## 4. Mismatch

```text
VERSION_MISMATCH
SOURCE_TYPE_MISMATCH
```

## 5. Identity

```text
case_id
```

## 6. Production ID Isolation

禁止四类 production IDs。

## 7. Environment Isolation

禁止运行环境信息。

## 8. Hash

```text
DEFERRED
```

## 9. Annotation Separation

```text
dataset_version
≠
annotation_version
```

## 10. G1/G2/G3/G4

保持当前：

```text
G1 BLOCKED
G2 INSUFFICIENT
G3 Contract READY
G3 Evidence BLOCKED
G4 BLOCKED
```

## 11. Deferred

```text
Real Artifact
Artifact Hash
Annotation Workflow
G2 Statistics
G4 Evaluation
ContextSelector
SelectionPolicy
```

---

# 二十二、验证命令

只运行：

```powershell
python -m pytest -q tests/test_conversation_context_evidence_provenance.py
```

然后：

```powershell
python -m pytest -q tests/test_conversation_context_evidence_provenance_architecture.py
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

# 二十三、Git Diff

确认：

```text
Production code = 0
DB schema = 0
DB writes = 0
Network = 0
LLM = 0
```

允许新增：

```text
tests/
docs/
```

不允许新增：

```text
backend/app/evidence/
backend/app/services/evidence*
backend/app/db/evidence*
```

---

# 二十四、最终报告

严格：

```text
Phase 4.1 Step 27 完成报告

1. Provenance Boundary
2. dataset_version
3. source_type
4. Version Semantics
5. Version Mismatch
6. Source Type Mismatch
7. Case Identity
8. Production ID Isolation
9. Environment Isolation
10. Hash
11. Annotation Separation
12. G1
13. G2
14. G3
15. G4
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

Phase 4.1 Step 27 READY
Phase 4.1 Step 27 STOP
```

---

# 二十五、硬停止

完成后：

**立即 STOP。**

不要进入：

```text
Step 28 Annotation Workflow
Step 29 Real G2
Step 30 G4 Evaluation
ContextSelector
SelectionPolicy
Memory
Summary
Tokenizer
```

等待下一步指令。
