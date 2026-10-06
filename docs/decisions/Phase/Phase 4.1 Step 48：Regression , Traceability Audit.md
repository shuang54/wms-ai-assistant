# Phase 4.1 Step 48：Regression / Traceability Audit

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

本阶段不新增业务能力。

唯一目标：

> 对 Phase 4.1 Step 37～47 已完成的 Persistence / Lifecycle / Conversation / E2E / Security 进行一次最终 Regression + Traceability Audit。

最终建立：

```text
Requirement
    ↓
Frozen Contract
    ↓
Implementation
    ↓
Test
    ↓
Regression
    ↓
Evidence
```

并确认：

```text
没有 Contract Drift
没有孤立实现
没有孤立测试
没有测试覆盖假阳性
没有历史 Guard 漂移
没有新增未声明边界
```

---

# 二、严格范围

## 允许

```text
新增 Traceability / Regression 测试
新增 Traceability Matrix 文档
新增少量测试辅助代码
修正明确的历史 Guard scope 漂移
```

## 禁止

本阶段禁止新增：

```text
Agent
Multi-Agent
MCP
Memory
Planning
Workflow
RBAC
ACL
Multi-tenancy
Policy Engine
Chat UI
Streaming
```

禁止修改：

```text
AI Router
AI Orchestrator
RAG
Tool Framework
Text-to-SQL
SQL Validator
SQL Executor
Evidence 核心状态机
Annotation 核心状态机
Conversation Runtime
```

除非发现明确的 Contract / Security / Regression bug。

如果发现真实 bug：

**停止修改，先报告。**

---

# 三、开始前必须完整阅读

不要直接写 Matrix。

先阅读：

```text
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
```

以及 Step 37～47 的实际 evaluation / decision 文档。

重点查找：

```text
Step 37
Step 38
Step 39
Step 40
Step 41
Step 42
Step 43
Step 44
Step 45
Step 46
Step 47
```

然后阅读实际实现：

```text
backend/app/db/evidence_repository.py
backend/app/db/conversation_evidence_repository.py
backend/app/db/models/evidence.py
backend/app/db/models/evidence_annotation.py
backend/app/db/models/conversation.py
backend/app/db/models/conversation_evidence.py
backend/app/db/models/__init__.py
```

以及：

```text
backend/app/services/ai_orchestrator_service.py
backend/app/services/ai_router_service.py
backend/app/
```

---

# 四、阅读现有测试

重点阅读：

```text
tests/test_evidence_persistence_db.py
tests/test_evidence_provenance_persistence_db.py
tests/test_evidence_annotation_association_db.py
tests/test_conversation_evidence_persistence_db.py
tests/test_conversation_evidence_e2e_db.py
tests/test_phase_4_1_security_boundary_db.py
```

以及：

```text
tests/test_conversation_architecture_contract.py
tests/test_conversation_model_contract.py
tests/test_conversation_persistence_contract.py
tests/test_evidence_persistence_boundary.py
```

同时搜索：

```text
Phase 4.1
Step 37
Step 38
Step 39
...
Step 47
```

确认当前测试是否真的对应当前 Contract。

---

# 五、建立 Phase 4.1 Traceability Matrix

新增：

```text
docs/evaluation/Phase 4.1 Step 48 — Regression Traceability Audit.md
```

建立：

| Step | Contract                         | Implementation                        | Test               | DB  | Security | Status |
| ---- | -------------------------------- | ------------------------------------- | ------------------ | --- | -------- | ------ |
| 37   | Evidence persistence             | EvidenceRepository / ORM              | persistence DB     | YES | YES      | PASS   |
| 38   | Provenance                       | EvidenceRepository read model         | provenance DB      | YES | YES      | PASS   |
| 39   | Annotation association           | FK / Repository                       | association DB     | YES | YES      | PASS   |
| 40   | Idempotency / transaction        | Repository                            | transaction DB     | YES | YES      | PASS   |
| 41   | Annotation review                | Repository transition                 | review tests       | YES | YES      | PASS   |
| 42   | Finalization contract            | lifecycle implementation              | finalization tests | YES | YES      | PASS   |
| 43   | Lifecycle matrix                 | lifecycle contract                    | lifecycle tests    | YES | YES      | PASS   |
| 44   | Conversation contract            | Conversation model                    | contract tests     | YES | YES      | PASS   |
| 45   | ConversationEvidence persistence | Repository / ORM                      | persistence DB     | YES | YES      | PASS   |
| 46   | Real Conversation E2E            | ChatApplicationService + repositories | E2E DB             | YES | YES      | PASS   |
| 47   | Security boundary                | existing boundaries                   | security DB        | YES | YES      | PASS   |

不要只复制表格。

每一行必须根据真实代码和真实测试重新确认。

---

# 六、Step 37 Traceability

确认 Step 37：

```text
Evidence
Annotation
```

真实持久化存在：

```text
ai_ops.evidence_record
ai_ops.evidence_annotation_record
```

确认：

```text
EvidenceRepository
```

确实拥有：

```text
create
get
update_status
idempotency
annotation creation
```

确认测试覆盖：

```text
FK
CRUD
idempotency
rollback
annotation association
```

最终：

```text
Step 37 = PASS
```

---

# 七、Step 38 Traceability

确认 Provenance Contract：

```text
dataset_version
source_type
```

没有被后续 Step 修改成：

```text
conversation_id
turn_id
assistant_request_id
```

确认：

```text
EvidenceProvenanceRow
EvidenceWithProvenance
EvidenceRepository.validate_provenance()
```

与 Contract 一致。

检查测试：

```text
valid provenance
invalid provenance
missing provenance
read-back
security
```

最终：

```text
Step 38 = PASS
```

---

# 八、Step 39 Traceability

确认：

```text
Evidence
    ↓ FK
Annotation
```

仍然成立。

确认：

```text
evidence_id
```

是 Annotation 的真实 owner reference。

确认：

```text
cross-evidence isolation
invalid evidence rejection
```

仍有测试覆盖。

不要因为 Step 44/45 出现 Conversation，而给 Annotation 添加：

```text
conversation_id
turn_id
```

如果没有这种字段：

这是正确状态。

最终：

```text
Step 39 = PASS
```

---

# 九、Step 40 Traceability

确认：

```text
(source_type, dataset_version)
```

仍然是 Evidence idempotency key。

确认：

```text
first-write-wins
DB UNIQUE
Repository transaction
rollback
concurrent duplicate protection
```

仍然存在。

尤其检查：

```text
dataset_version alone
```

没有错误地变成唯一键。

最终：

```text
Step 40 = PASS
```

---

# 十、Step 41 Traceability

确认 Annotation review：

```text
DRAFT
    ↓
REVIEWED
```

仍然是：

```text
one-way transition
```

确认：

```text
REVIEWED
→
DRAFT
```

没有被意外允许。

确认：

```text
Annotation REVIEWED
```

不会未经 Contract 授权自动推进：

```text
Evidence REVIEWED
```

最终：

```text
Step 41 = PASS
```

---

# 十一、Step 42 Traceability

确认 Evidence：

```text
IMPORTED
→
PERSISTED
→
ANNOTATED
→
REVIEWED
→
FINALIZED
```

仍然保持。

确认：

```text
FINALIZED
```

是 terminal state。

同时不要擅自把 Step 43 尚未冻结的：

```text
all annotations reviewed
```

变成新的实现规则。

如果相关规则仍是：

```text
UNDEFINED
```

保持：

```text
UNDEFINED
```

最终：

```text
Step 42 = PASS
```

---

# 十二、Step 43 Traceability

重新检查 Lifecycle Matrix。

必须与当前：

```text
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
```

一致。

重点：

```text
Evidence state machine
Annotation state machine
```

以及：

```text
PERSISTED + DRAFT
ANNOTATED + DRAFT
ANNOTATED + REVIEWED
REVIEWED + DRAFT
REVIEWED + REVIEWED
FINALIZED + DRAFT
FINALIZED + REVIEWED
```

不要自行重新定义这些组合。

只验证：

```text
implementation
↔
documented contract
```

一致。

最终：

```text
Step 43 = PASS
```

---

# 十三、Step 44 Traceability

确认：

```text
Conversation
```

仍然是独立 Domain Object。

确认：

```text
Conversation → Evidence
```

是：

```text
REFERENCE
```

而不是：

```text
OWNERSHIP
```

确认：

```text
Conversation delete
```

不会删除 Evidence。

确认：

```text
Evidence delete
```

不会删除 Conversation。

确认：

```text
conversation_id
```

没有进入 Evidence identity / provenance。

最终：

```text
Step 44 = PASS
```

---

# 十四、Step 45 Traceability

确认真实表：

```text
ai_ops.conversation_evidence
```

结构仍然是：

```text
conversation_id
evidence_id
created_at
```

确认：

```text
PRIMARY KEY
(conversation_id, evidence_id)
```

仍然存在。

确认：

```text
ConversationEvidenceRepository
```

仍然：

```text
owns transaction
first-write-wins
parent validation
read-back
```

确认 Guard：

```text
association_persistence_modules
```

仍然只有：

```text
conversation_evidence.py
conversation_evidence_repository.py
```

最终：

```text
Step 45 = PASS
```

---

# 十五、Step 46 Traceability

确认真实运行链路：

```text
Conversation
    ↓
ConversationTurn
    ↓
ChatApplicationService
    ↓
ConversationContextBuilder
    ↓
FakeOrchestrator
    ↓
AIOrchestrationResult
    ↓
Evidence
    ↓
ConversationEvidence
    ↓
PostgreSQL
    ↓
new Session Read-back
```

注意：

FakeOrchestrator 是测试替身，不代表 production runtime 是 Fake。

确认 Step 46 的目的仍然是：

```text
真实 Conversation runtime entry
+
真实 PostgreSQL persistence
+
真实 read-back
```

确认：

```text
OD-12 = DEFERRED
```

仍然成立。

不得在 Step 48 偷偷把：

```text
turn_id
```

加入数据库。

最终：

```text
Step 46 = PASS
```

---

# 十六、Step 47 Traceability

确认 Security Matrix 仍然覆盖：

```text
Identity
Provenance
ConversationEvidence
Cross-conversation
Evidence reuse
Annotation
Delete/Cascade
Repository
Read Model
Sensitive metadata
Guard
Negative cases
```

确认：

```text
Security Findings = 0
```

仍然成立。

注意：

```text
RBAC
Multi-tenancy
Object-level Authorization
```

仍然是：

```text
OUT OF SCOPE / FUTURE GOVERNANCE
```

不要在 Traceability 阶段把它们变成 Phase 4.1 failure。

最终：

```text
Step 47 = PASS
```

---

# 十七、Frozen Contract Drift Audit

这是本阶段最重要的检查之一。

搜索：

```text
Evidence
Annotation
Conversation
ConversationEvidence
Provenance
Lifecycle
```

检查是否出现新的：

```text
conversation_id
turn_id
assistant_request_id
provider_request_id
```

进入不允许的模型。

重点检查：

```text
backend/app/db/models/
backend/app/db/
backend/app/services/
backend/app/api/
```

不要只检查 tests。

---

# 十八、Contract → Test 完整性

建立一个简单规则：

每一个 Frozen Contract 必须至少有一个对应测试。

例如：

```text
Evidence provenance
→ provenance persistence test

Annotation FK
→ association test

ConversationEvidence PK
→ persistence test

Conversation isolation
→ E2E/security test

FINALIZED terminal
→ lifecycle test
```

如果发现：

```text
Contract exists
but no test
```

不要立即实现。

记录：

```text
Traceability Gap
```

然后判断是否属于：

```text
Step 48 required
```

如果只是未来能力：

标记：

```text
DEFERRED
```

---

# 十九、Test → Contract 完整性

反向检查：

> 有没有测试正在验证一个已经不存在的旧 Contract？

特别关注历史测试名称和 assertions。

例如：

```text
old Evidence persistence must NOT exist
```

如果现在 Step 37 已经正式实现：

这种 Guard 必须已经迁移或删除。

但是：

**不要为了让测试绿而删除测试。**

应该确认它是否已经迁移为：

```text
scope-aware guard
```

---

# 二十、Implementation → Contract 完整性

扫描新增生产文件：

```text
backend/app/db/models/conversation_evidence.py
backend/app/db/conversation_evidence_repository.py
```

确认：

每一个 public method 都能找到对应 Contract。

例如：

```text
create_association
→ Step 45

get_association
→ Step 45

list_evidence_ids
→ Step 45

list_conversation_ids
→ Step 45
```

如果发现：

```text
production method
```

没有任何 Roadmap / Contract / Test 对应：

记录：

```text
Orphan Implementation
```

不要立即删除。

---

# 二十一、Test Orphan Audit

检查：

```text
tests/
```

Step 37–47 新增测试。

确认：

每个测试至少对应：

```text
Step
Contract
Security boundary
Regression rule
```

如果某个测试没有明确归属：

不要删除。

标记：

```text
Orphan Test
```

然后判断是否只是辅助测试。

---

# 二十二、Database Schema Traceability

确认当前 `ai_ops` 相关表：

```text
evidence_record
evidence_annotation_record
conversation
conversation_turn
conversation_evidence
```

分别能够映射到：

```text
Step 37
Step 39
Step 44
Step 44
Step 45
```

确认没有 Phase 4.1 未声明的新表。

运行数据库 catalog inspection。

禁止：

```text
DROP
ALTER
CREATE
```

这里只读。

---

# 二十三、Production DB Write Audit

确认 Step 48 自己：

```text
DB writes = 0
```

Traceability Audit 尽可能使用：

```text
static inspection
readonly catalog inspection
existing tests
```

不要为了检查 schema 创建测试数据。

---

# 二十四、Regression Test Matrix

建立：

```text
Phase 4.1 Regression Matrix
```

至少包含：

```text
Static / Offline
DB Persistence
Conversation E2E
Security
Lifecycle
Guard
Full Regression
Matrix Gate
Compile
```

结果：

```text
PASS
FAIL
SKIP
PRE-EXISTING
```

必须区分：

```text
本阶段新失败
```

与：

```text
历史 pre-existing failure
```

---

# 二十五、当前已知 DB Baseline 问题

当前已知：

```text
test_db_residue_is_zero
test_baseline_matches_step89_actual_execution
test_gate_passes_when_current_matches_baseline
```

在：

```text
RUN_DB_TESTS=1
```

全量环境中持续出现。

历史状态：

```text
Step 42
Step 43
Step 44
Step 45
Step 46
Step 47
```

均存在。

因此：

**不要修改 baseline。**

不要修改这三个测试。

不要把它们标记：

```text
xfail
skip
```

不要为了 Step 48 伪造 PASS。

本阶段只记录：

```text
Pre-existing DB baseline issue
```

同时验证它们是否仍然：

```text
single-test / isolated context = PASS
```

---

# 二十六、Full Regression

执行：

```powershell
python -m pytest -q
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

必须分别记录：

```text
passed
failed
skipped
errors
```

不要只报告“pytest failed”。

---

# 二十七、重点 Regression Collection

执行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q `
  tests/test_evidence_persistence_db.py `
  tests/test_evidence_provenance_persistence_db.py `
  tests/test_evidence_annotation_association_db.py `
  tests/test_conversation_evidence_persistence_db.py `
  tests/test_conversation_evidence_e2e_db.py `
  tests/test_phase_4_1_security_boundary_db.py
```

然后：

```powershell
python -m pytest -q `
  tests/test_conversation_architecture_contract.py `
  tests/test_conversation_model_contract.py `
  tests/test_conversation_persistence_contract.py `
  tests/test_evidence_persistence_boundary.py
```

---

# 二十八、Matrix Gate

执行：

```powershell
python scripts/run_matrix_gate.py
```

必须记录：

```text
Status
Exit code
```

如果：

```text
Status: PASS
Exit code: 0
```

则：

```text
Matrix = PASS
```

如果失败：

先分类。

不要修改 baseline。

---

# 二十九、Compile

执行：

```powershell
python -m compileall -q backend tests scripts
```

要求：

```text
COMPILEALL OK
```

---

# 三十、Git 状态

只读取：

```powershell
git status --short
git diff --stat
git diff --name-only
```

禁止：

```text
git add
git commit
git push
git reset
git checkout
git clean
```

Step 50 才处理。

---

# 三十一、不要修改 Step 45 遗留文件

当前工作区可能仍然存在：

```text
tests/test_conversation_architecture_contract.py
tests/test_conversation_model_contract.py
tests/test_conversation_persistence_contract.py
tests/test_evidence_persistence_boundary.py
```

这些是 Step 45 Guard Reconciliation 的合法修改。

Step 48 不要重新格式化、重构或移动这些文件。

---

# 三十二、不要重新实现测试基础设施

如果现有：

```text
fixtures
DB helpers
cleanup helpers
assertion helpers
matrix scripts
```

已经存在：

**复用。**

pytest 本身的 fixture 机制就是为了提供可复用、显式、可组合的测试上下文；不要为了 Traceability 再造第二套 fixture 生命周期。([docs.pytest.org](https://docs.pytest.org/en/latest/explanation/fixtures.html?utm_source=chatgpt.com))

---

# 三十三、Traceability Gap 分类

如果发现缺口，只允许分成：

```text
FROZEN
IMPLEMENTED
TESTED
DEFERRED
OUT_OF_SCOPE
PRE_EXISTING
```

不要自行发明：

```text
NEW
TODO
MAYBE
```

等模糊状态。

---

# 三十四、Step 48 不解决以下问题

即使发现，也只记录：

```text
OD-12 turn-level provenance
RBAC
Multi-tenancy
ACL
Object-level authorization
Evidence Builder
Production AI Result → Evidence mapping
Chat UI
Streaming
Agent
MCP
Memory
Workflow
```

这些不是 Step 48 的实现目标。

---

# 三十五、Traceability Document

新增：

```text
docs/evaluation/Phase 4.1 Step 48 — Regression Traceability Audit.md
```

结构：

```text
# Phase 4.1 Step 48 — Regression Traceability Audit

## 1. Scope

## 2. Step 37–47 Traceability Matrix

## 3. Frozen Contract Inventory

## 4. Contract → Test Matrix

## 5. Test → Contract Audit

## 6. Implementation → Contract Audit

## 7. Database Schema Traceability

## 8. Security Traceability

## 9. Regression Results

## 10. Known Pre-existing Failures

## 11. Traceability Gaps

## 12. Final Status
```

---

# 三十六、Final Status 判定

只有以下条件全部满足才允许：

```text
Step 48 = COMPLETE
```

条件：

```text
Step 37 Contract Traceability     PASS
Step 38 Contract Traceability     PASS
Step 39 Contract Traceability     PASS
Step 40 Contract Traceability     PASS
Step 41 Contract Traceability     PASS
Step 42 Contract Traceability     PASS
Step 43 Contract Traceability     PASS
Step 44 Contract Traceability     PASS
Step 45 Contract Traceability     PASS
Step 46 Contract Traceability     PASS
Step 47 Contract Traceability     PASS

Frozen Contract Drift             0
Unauthorized Production Module    0
Orphan Critical Implementation    0
Orphan Critical Test              0
DB Schema Unexpected Change       0
Security Regression               0
Matrix                            PASS
Compile                           PASS
Production DB writes              0
```

历史 DB baseline 问题可以：

```text
PRE_EXISTING
```

不阻塞 Step 48，但必须明确记录。

---

# 三十七、如果发现 Contract Drift

如果发现：

```text
Implementation != Contract
```

或者：

```text
Test != Contract
```

或者：

```text
Document != Implementation
```

不要自动修复。

输出：

```text
TRACEABILITY FINDING

Step:
Contract:
Implementation:
Test:
Mismatch:
Impact:
Recommended action:
```

然后：

**STOP。**

---

# 三十八、如果全部通过

不要修改核心代码。

只新增：

```text
docs/evaluation/Phase 4.1 Step 48 — Regression Traceability Audit.md
```

以及必要的：

```text
tests/test_phase_4_1_traceability.py
```

测试数量保持最小。

不要建立 benchmark framework。

不要建立 HTML report。

不要建立新的 database table。

---

# 三十九、最终报告格式

完成后严格按照：

```text
【Phase 4.1 Step 48 COMPLETE】

1. Traceability Scope
2. Step 37–47 Matrix
3. Frozen Contract Inventory
4. Contract → Test
5. Test → Contract
6. Implementation → Contract
7. Database Schema Traceability
8. Security Traceability
9. Contract Drift
10. Orphan Implementation
11. Orphan Test
12. Traceability Gaps
13. Full Regression
14. DB Regression
15. Matrix
16. Compile
17. DB Residue
18. Production DB Writes
19. Real LLM
20. 修改文件
21. 当前限制
```

最终必须给出：

```text
Phase 4.1 Traceability:

Step 37  → PASS
Step 38  → PASS
Step 39  → PASS
Step 40  → PASS
Step 41  → PASS
Step 42  → PASS
Step 43  → PASS
Step 44  → PASS
Step 45  → PASS
Step 46  → PASS
Step 47  → PASS

Contract Drift = 0
Security Regression = 0
Unexpected DB Schema = 0
Production DB Writes = 0
```

最后：

**立即停止。**

不要进入 Step 49。

不要开发新功能。

不要开发 Agent。

不要开发 MCP。

不要开发 Memory。

不要开发 Workflow。

不要实现 RBAC。

不要做 Git Push / PR / Merge。

Step 49 才进入 Release Readiness Audit。
