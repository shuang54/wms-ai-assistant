你现在开始执行：

# Phase 4.1 Step 47：Security / Boundary Audit

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

本阶段只做：

```text
Security Audit
Boundary Audit
Security Regression
```

目标：

验证 Phase 4.1 当前已经形成的：

```text
Conversation
ConversationTurn
AI Runtime
Evidence
Annotation
ConversationEvidence
Repository
Read Model
```

之间不存在已经能够被当前架构证明的：

```text
cross-conversation leakage
cross-evidence leakage
identity boundary violation
ORM/session leakage
sensitive metadata leakage
unauthorized persistence
repository bypass
boundary whitelist drift
```

安全审计重点采用：

```text
object isolation
least privilege
explicit boundary
deny-by-default where a boundary already exists
```

不要在本阶段新增完整 Authorization/RBAC 系统。

OWASP 建议对象级访问控制应针对具体资源执行，而不能仅因为调用方知道对象 ID 就默认允许访问。

---

# 二、严格范围

## 允许

```text
新增 Security / Boundary tests
新增少量测试 fixture
新增 Security Evaluation 文档
必要时修正明确的 Guard scope
```

## 禁止

不要修改：

```text
AI Router
AI Orchestrator
RAG
Tool Framework
Text-to-SQL
SQL Validator
SQL Executor
Evidence State Machine
Annotation State Machine
Conversation Runtime
```

除非发现明确的真实安全 bug。

禁止新增：

```text
RBAC
ABAC
ACL
Multi-tenancy
Auth Service
Permission Service
Policy Engine
JWT
SSO
```

这些属于后续 Enterprise Governance。

---

# 三、开始前必须阅读

先阅读真实实现：

```text
backend/app/services/ai_orchestrator_service.py
backend/app/services/ai_router_service.py

backend/app/db/evidence_repository.py
backend/app/db/conversation_evidence_repository.py

backend/app/db/models/evidence.py
backend/app/db/models/evidence_annotation.py
backend/app/db/models/conversation.py
backend/app/db/models/conversation_evidence.py

backend/app/db/models/__init__.py
```

再阅读：

```text
tests/test_conversation_evidence_e2e_db.py
tests/test_conversation_evidence_persistence_db.py
tests/test_evidence_persistence_db.py
tests/test_evidence_provenance_persistence_db.py
tests/test_evidence_annotation_association_db.py
```

再阅读已有 Boundary / Security Guards：

```text
tests/test_evidence_persistence_boundary.py
tests/test_conversation_architecture_contract.py
tests/test_conversation_model_contract.py
tests/test_conversation_persistence_contract.py
```

以及：

```text
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
```

---

# 四、先做 Security Inventory

不要先写测试。

先建立当前真实安全边界清单。

至少确认：

```text
Conversation
ConversationTurn
Evidence
Annotation
ConversationEvidence
EvidenceRepository
ConversationEvidenceRepository
Read Models
AIOrchestrator
```

分别回答：

```text
谁创建？
谁读取？
谁修改？
谁删除？
谁持有 ID？
谁可以跨对象引用？
谁拥有 transaction？
```

把结果记录到：

```text
docs/evaluation/Phase 4.1 Step 47 — Security Boundary Audit.md
```

---

# 五、Security Boundary Matrix

建立以下 Matrix：

| Boundary                           | Expected                  | Test |
| ---------------------------------- | ------------------------- | ---- |
| Conversation → Evidence            | REFERENCE                 | PASS |
| Evidence → Conversation            | REFERENCE                 | PASS |
| Conversation → Annotation          | indirect only             | PASS |
| Evidence → Annotation              | FK-owned                  | PASS |
| Conversation A → Evidence B        | reject / absent           | PASS |
| Conversation B → Evidence A        | reject / absent           | PASS |
| Evidence ID → Conversation         | only explicit association | PASS |
| assistant_request_id → Evidence ID | forbidden                 | PASS |
| assistant_request_id → provenance  | forbidden                 | PASS |
| Evidence → Conversation ownership  | forbidden                 | PASS |
| Conversation delete → Evidence     | preserve                  | PASS |
| Evidence delete → Conversation     | preserve                  | PASS |
| Read Model → ORM/session           | forbidden                 | PASS |
| Metadata → credentials             | forbidden                 | PASS |
| Repository → raw SQL bypass        | forbidden                 | PASS |

如果某项当前没有正式 contract：

标记：

```text
UNDEFINED
```

不要擅自冻结新的架构规则。

---

# 六、Identity Boundary Audit

这是 Step 47 的核心。

确认以下 ID 永远不是同一个 Domain Identity：

```text
conversation_id
turn_id
evidence_id
annotation_id
assistant_request_id
provider_request_id
dataset_version
case_id
```

至少测试：

```text
assistant_request_id != evidence_id
conversation_id != evidence_id
turn_id != evidence_id
annotation_id != evidence_id
```

注意：

这里不是要求所有值数学上永远不同。

而是验证：

> 系统不会把一个 ID 当成另一个 Domain Object 的 identity。

特别检查：

```text
assistant_request_id
```

只能作为 runtime correlation。

不得成为：

```text
Evidence identity
Evidence provenance
ConversationEvidence key
```

---

# 七、Evidence Provenance Boundary

Step 38 已冻结：

```text
Evidence Provenance
=
dataset_version
+
source_type
```

Step 47 必须确认：

以下内容不会进入 provenance：

```text
conversation_id
turn_id
assistant_request_id
provider_request_id
case_id
raw_content
```

测试：

```text
create Evidence
→ get_provenance
→ assert exact fields
```

必须保持：

```text
dataset_version
source_type
```

其他 runtime identity 不得污染 provenance。

---

# 八、ConversationEvidence Boundary

确认当前关联表仍然严格是：

```text
conversation_id
evidence_id
created_at
```

禁止出现：

```text
turn_id
assistant_request_id
provider_request_id
dataset_version
source_type
raw_content
```

测试：

```python
actual_columns == {
    "conversation_id",
    "evidence_id",
    "created_at",
}
```

必须保持精确集合。

不要使用：

```text
issubset
<=
startswith
directory wildcard
```

来弱化检查。

---

# 九、Cross-Conversation Isolation

创建：

```text
Conversation A
Conversation B
```

以及：

```text
Evidence A
Evidence B
```

建立：

```text
A ↔ Evidence A
B ↔ Evidence B
```

验证：

```text
A → Evidence A
A ↛ Evidence B

B → Evidence B
B ↛ Evidence A
```

然后反向通过 Repository：

```text
Evidence A → Conversation A
Evidence A ↛ Conversation B
```

如果当前 Repository API 只能返回显式关联：

保持现状，不增加 authorization API。

---

# 十、Evidence Reuse 不等于 Ownership

测试：

```text
Conversation A ↔ Evidence X
Conversation B ↔ Evidence X
```

确认：

```text
Evidence X
```

只有一个 record。

同时：

```text
A
```

不能因此读取 B 的其他 Evidence。

也就是说：

```text
Evidence reusable
≠
Conversation resources shared
```

---

# 十一、Annotation Boundary

验证：

```text
Annotation.evidence_id
```

只能引用真实 Evidence。

测试：

```text
invalid evidence_id
→ reject
```

并验证：

```text
annotation count unchanged
```

然后：

```text
Evidence A
Annotation A1

Evidence B
Annotation B1
```

验证：

```text
list_annotations(A)
→ A1

list_annotations(B)
→ B1
```

不能交叉。

---

# 十二、Annotation → Conversation

确认当前系统没有：

```text
Annotation.conversation_id
Annotation.turn_id
```

也没有隐式：

```text
Annotation → Conversation ownership
```

Step 47 不要新增这些字段。

如果未来需要：

```text
Conversation → Annotation
```

应该通过：

```text
Conversation
→ Evidence
→ Annotation
```

当前保持这个边界。

---

# 十三、Delete / Cascade Security

真实 PostgreSQL 验证：

## Case A

删除：

```text
Conversation A
```

必须：

```text
conversation_evidence row → deleted
Evidence → remains
Annotation → remains
```

如果 Annotation 依赖 Evidence，则继续遵循现有 Evidence FK 语义。

## Case B

删除：

```text
Evidence X
```

必须：

```text
conversation_evidence row → deleted
Conversation → remains
```

不能发生：

```text
Evidence delete
→ Conversation delete
```

或者：

```text
Conversation delete
→ Evidence delete
```

---

# 十四、Repository Boundary

检查 production code：

```text
backend/app/db/
```

确认：

```text
ConversationEvidence
Evidence
Annotation
```

的 persistence 都经过各自 Repository。

禁止新增：

```text
service → Session.execute(INSERT ...)
service → raw SQL INSERT
api → Session
api → direct ORM write
```

本阶段不要修改 Repository architecture。

只审计现状。

---

# 十五、Transaction Boundary

确认：

```text
EvidenceRepository
ConversationEvidenceRepository
```

仍然由 Repository 自己拥有 transaction。

检查类似：

```python
with factory() as session, session.begin():
```

的现有 contract。

不要在 Step 47 创建：

```text
GlobalSession
SharedSession
RequestSession
```

不要把多个 Repository 强行合并成一个 transaction abstraction。

Step 40 已明确当前 Repository transaction boundary。

---

# 十六、Read Model Security

重点检查：

```text
EvidenceProvenanceRow
EvidenceWithProvenance
AnnotationRow
ConversationEvidenceReference
```

必须：

```text
frozen
```

并且不能持有：

```text
Session
Connection
Engine
ORM instance
SQLAlchemy Result
registry
metadata
```

可以继续使用当前已有 security tests。

如果已经存在：

**优先复用。**

不要重新实现一套 introspection framework。

---

# 十七、Sensitive Data Boundary

对以下对象做字段扫描：

```text
AIOrchestrationResult
EvidenceWithProvenance
AnnotationRow
ConversationEvidenceReference
```

禁止出现：

```text
api_key
password
secret
token
authorization
database_url
connection_string
dsn
```

大小写不敏感扫描。

如果项目已有统一敏感字段检测：

直接复用。

---

# 十八、AI Result Metadata Boundary

检查：

```text
AIOrchestrationResult.metadata
```

不能因为 Step 46：

```text
Conversation
→ AI Runtime
→ Evidence
```

而偷偷加入：

```text
conversation_id
turn_id
evidence_id
database_url
credentials
```

除非这些字段本来就在 frozen contract 中。

特别注意：

```text
assistant_request_id
```

可以作为 runtime correlation，

但不得被转换成：

```text
evidence_id
```

---

# 十九、Cross-Project Boundary

Phase 4.1 还没有正式 Multi-tenancy / RBAC。

因此不要创造“项目权限系统”。

但是仍然要测试：

如果当前对象带有：

```text
project_id
```

则不能在现有 contract 下错误地把：

```text
project A Conversation
```

自动关联到：

```text
project B Evidence
```

如果当前 Repository 根本没有 project-aware association：

不要新增 project authorization。

只记录：

```text
Cross-project authorization = NOT YET IMPLEMENTED
```

并明确：

```text
RBAC / Multi-tenancy = OUT OF SCOPE
```

---

# 二十、API Boundary Audit

检查：

```text
backend/app/api/
```

确认当前 API：

```text
does not directly expose SQLAlchemy Session
does not directly write Evidence
does not directly write ConversationEvidence
```

如果当前 API 尚未暴露这些资源：

这是允许的。

不要为了 Step 47 新增 API。

---

# 二十一、Guard Boundary Audit

重新确认：

```text
test_conversation_architecture_contract.py
test_conversation_model_contract.py
test_conversation_persistence_contract.py
test_evidence_persistence_boundary.py
```

仍然保持：

```text
explicit whitelist
exact set comparison
unknown-file rejection
```

特别确认 Step 45 的：

```text
association_persistence_modules
```

仍然只有：

```text
conversation_evidence.py
conversation_evidence_repository.py
```

不得新增其他 association 文件而不触发 Guard。

---

# 二十二、Negative Security Cases

至少做以下 Negative Cases。

## Case 1

```text
Conversation A
Evidence B
```

尝试读取：

```text
A → B
```

必须不能产生隐式关联。

## Case 2

```text
Evidence A
Conversation B
```

尝试：

```text
B → A
```

必须不能产生隐式关联。

## Case 3

不存在 Evidence：

```text
create_annotation(fake_evidence_id)
```

必须 reject。

## Case 4

不存在 Conversation：

```text
create_association(fake_conversation_id, evidence_id)
```

必须 reject。

## Case 5

不存在 Evidence：

```text
create_association(conversation_id, fake_evidence_id)
```

必须 reject。

## Case 6

重复 association：

```text
create(A, E)
create(A, E)
```

只能存在一条。

---

# 二十三、Security Tests

优先新增：

```text
tests/test_phase_4_1_security_boundary_db.py
```

如果项目已经有更合适的 Security Audit 文件：

遵循现有结构。

不要创建大型 security framework。

建议覆盖：

```text
test_identity_boundaries
test_evidence_provenance_boundary
test_conversation_evidence_columns
test_cross_conversation_isolation
test_evidence_reuse_without_cross_leakage
test_annotation_evidence_isolation
test_delete_cascade_boundary
test_read_model_security
test_sensitive_metadata_boundary
test_repository_boundary
```

---

# 二十四、测试数据

必须：

```text
test-only
```

使用真实 PostgreSQL。

运行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q ...
```

禁止：

```text
production DB
real WMS data
TRUNCATE
DROP SCHEMA
full-table DELETE
```

清理必须精确到本次测试创建的 ID。

---

# 二十五、DB Residue

测试前记录：

```text
conversation
conversation_turn
evidence_record
evidence_annotation_record
conversation_evidence
```

测试后再次记录。

必须：

```text
before == after
```

如果项目已有：

```text
test_db_residue_is_zero
```

保持原样。

不要修改 baseline。

---

# 二十六、真实 LLM

默认：

```text
SKIP
```

禁止：

```text
pytest -q
```

调用：

```text
DeepSeek
SiliconFlow
```

Step 47 是 security boundary audit，不需要真实模型。

---

# 二十七、不要做 Authorization 系统

如果审计发现：

```text
当前系统没有 RBAC
当前系统没有用户身份
当前系统没有 tenant
```

这不是 Step 47 bug。

最终报告写：

```text
RBAC = OUT OF SCOPE
Multi-tenancy = OUT OF SCOPE
Object-level authorization = FUTURE GOVERNANCE
```

当前 Step 47 只验证：

```text
repository/object identity isolation
```

不是实现完整授权系统。

OWASP 的对象级授权要求每次针对具体对象做授权检查；但当前项目尚未进入 RBAC/Governance 阶段，因此这里不要把未来权限体系提前塞进 Phase 4.1。

---

# 二十八、如果发现真实 Security Bug

如果发现：

```text
cross-conversation leak
evidence leak
credential leak
ORM leak
repository bypass
unexpected cascade
identity confusion
```

不要直接修改。

先报告：

```text
Security Finding
Affected Boundary
Reproduction
Impact
Root Cause
Existing Contract
Proposed Minimal Fix
```

然后 STOP。

只有用户明确授权修复，才进入下一步。

---

# 二十九、测试顺序

先：

```powershell
python -m pytest -q tests/test_phase_4_1_security_boundary_db.py
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_phase_4_1_security_boundary_db.py
```

再运行相关集合：

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
python -m pytest -q
```

最后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

---

# 三十、Compile / Matrix

执行：

```powershell
python -m compileall -q backend tests scripts
```

然后：

```powershell
python scripts/run_matrix_gate.py
```

如果出现历史：

```text
test_db_residue_is_zero
test_baseline_matches_step89_actual_execution
test_gate_passes_when_current_matches_baseline
```

不要修改。

分类为：

```text
pre-existing baseline issue
```

---

# 三十一、Git

禁止：

```text
git add
git commit
git push
git checkout
git reset
git clean
```

Step 50 才进行 Git Closeout。

只检查：

```powershell
git status --short
git diff --stat
git diff
```

---

# 三十二、Evaluation 文档

新增：

```text
docs/evaluation/Phase 4.1 Step 47 — Security Boundary Audit.md
```

至少记录：

## 1. Security Scope

```text
Conversation
Evidence
Annotation
ConversationEvidence
AI Runtime
Repository
Read Model
```

## 2. Boundary Matrix

记录：

```text
Boundary
Expected
Actual
Result
```

## 3. Negative Cases

记录：

```text
Case
Expected
Actual
Result
```

## 4. Identity Boundary

明确：

```text
conversation_id ≠ evidence_id
turn_id ≠ evidence_id
assistant_request_id ≠ evidence_id
```

语义层面成立。

## 5. Sensitive Data

记录：

```text
credential leakage = 0
ORM leakage = 0
connection leakage = 0
```

## 6. Authorization Scope

明确：

```text
RBAC = OUT OF SCOPE
Multi-tenancy = OUT OF SCOPE
Full object authorization = FUTURE GOVERNANCE
```

## 7. Findings

如果没有：

```text
Security Findings = 0
```

---

# 三十三、Step 47 完成判定

必须满足：

```text
Identity Boundary                 PASS
Evidence Provenance Boundary     PASS
ConversationEvidence Boundary    PASS
Cross Conversation Isolation     PASS
Evidence Reuse Boundary          PASS
Annotation Boundary              PASS
Delete/Cascade Boundary          PASS
Repository Boundary              PASS
Read Model Boundary              PASS
Sensitive Metadata Boundary      PASS
Guard Boundary                   PASS
Negative Security Cases          PASS
DB Residue                       0
Production DB Writes             0
Matrix                            PASS
Compile                           PASS
```

并且：

```text
RBAC = NOT IMPLEMENTED
Multi-tenancy = NOT IMPLEMENTED
```

不算失败。

---

# 三十四、最终报告格式

完成后严格输出：

```text
【Phase 4.1 Step 47 COMPLETE】

1. Security Scope
2. Security Boundary Matrix
3. Identity Boundary
4. Evidence Provenance Boundary
5. ConversationEvidence Boundary
6. Cross-Conversation Isolation
7. Evidence Reuse
8. Annotation Boundary
9. Delete / Cascade Boundary
10. Repository Boundary
11. Read Model Security
12. Sensitive Data Boundary
13. Guard Boundary
14. Negative Security Cases
15. DB Tests
16. Full Regression
17. DB Regression
18. Matrix
19. Compile
20. DB Residue
21. Production DB Writes
22. Real LLM
23. Security Findings
24. 修改文件
25. 当前限制
```

最后输出：

```text
Security Boundary:

Conversation
    ↓
AI Runtime
    ↓
AI Result
    ↓
Evidence
    ↓
ConversationEvidence
    ↓
PostgreSQL
    ↓
Read Model

Cross-object leakage = 0
Identity confusion = 0
Sensitive data leakage = 0
ORM/session leakage = 0
Unauthorized persistence = 0
```

然后：

**立即停止。**

不要进入 Step 48。

不要开发 RBAC。

不要开发 Multi-tenancy。

不要开发 Agent。

不要开发 MCP。

不要开发 Memory。

不要开发 Workflow。

不要进行 Git Push / PR / Merge。
