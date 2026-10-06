# Phase 4.1 Step 49：Release Readiness Audit

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

Phase 4.1 Step 37～48 已完成：

```text
Persistence
Provenance
Annotation
Idempotency
Review
Finalization Contract
Lifecycle Matrix
Conversation Contract
Conversation Evidence
Real Conversation E2E
Security Boundary
Regression / Traceability
```

本阶段唯一目标：

> 判断 Phase 4.1 当前状态是否满足进入 Step 50 Git Closeout / Push / PR / Merge 的条件。

本阶段：

**不开发新功能。**

不修改生产架构。

不优化业务逻辑。

不修改历史 baseline 只为了让测试全绿。

最终输出：

```text
RELEASE READY
```

或者：

```text
RELEASE BLOCKED
```

---

# 二、核心判断原则

本阶段必须区分四种状态：

```text
BLOCKER
NON-BLOCKER
DEFERRED
OUT_OF_SCOPE
```

定义：

## BLOCKER

如果问题会导致：

```text
Frozen Contract 被破坏
Security Boundary 被破坏
数据完整性不成立
Persistence 不可靠
Regression 无法证明
Release artifact 不可追溯
存在明确生产风险
```

则：

```text
BLOCKER
```

不能进入 Step 50。

---

## NON-BLOCKER

问题存在，但：

```text
不破坏 Frozen Contract
不破坏 Security
不影响 Phase 4.1 已声明能力
有明确历史原因
有独立 issue / baseline 解释
```

可以：

```text
NON-BLOCKER
```

允许继续。

---

## DEFERRED

已经明确：

```text
未来阶段处理
```

例如：

```text
OD-12 turn-level provenance
Evidence Builder
```

必须有明确记录。

---

## OUT_OF_SCOPE

明确不属于 Phase 4.1：

```text
RBAC
Multi-tenancy
ACL
Object-level Authorization
Agent
MCP
Memory
Workflow
Chat UI
Streaming
```

不能因为没有实现而判定 Release Blocker。

---

# 三、开始前必须阅读

首先阅读：

```text
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
docs/evaluation/Phase 4.1 Step 48 — Regression Traceability Audit.md
```

然后阅读：

```text
docs/evaluation/
docs/decisions/Phase/
```

搜索：

```text
Phase 4.1
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
Step 48
```

不要假设 Step 48 报告内容。

以真实文件为准。

---

# 四、Git 工作区审计

先执行：

```powershell
git status --short
```

然后：

```powershell
git diff --stat
```

然后：

```powershell
git diff --name-only
```

然后：

```powershell
git log --oneline --decorate -20
```

确认：

1. Step 37～48 的变更是否全部存在。
2. 是否存在未预期的生产代码修改。
3. 是否存在未追踪文件。
4. 是否存在与 Phase 4.1 无关的修改。
5. 是否存在误修改历史文件。

---

# 五、禁止 Git 操作

Step 49：

```text
禁止 git add
禁止 git commit
禁止 git push
禁止 git reset
禁止 git checkout
禁止 git clean
禁止 git merge
```

Step 50 才处理 Git。

---

# 六、Architecture Readiness

确认 Phase 4.1 没有突破：

```text
AI Runtime
    ↓
AI Result
    ↓
Evidence
    ↓
Conversation Reference
```

当前 Evidence 仍然是：

```text
Independent Domain Object
```

Conversation 仍然不是 Evidence Owner。

确认没有出现：

```text
Conversation → Evidence ownership
Evidence → Conversation ownership
```

---

# 七、Persistence Readiness

确认以下表存在且结构符合 Frozen Contract：

```text
ai_ops.evidence_record
ai_ops.evidence_annotation_record
ai_ops.conversation
ai_ops.conversation_turn
ai_ops.conversation_evidence
```

只允许：

```text
READ-ONLY catalog inspection
```

不要执行：

```text
CREATE
ALTER
DROP
TRUNCATE
DELETE
UPDATE
INSERT
```

确认：

```text
Evidence persistence = PASS
Annotation persistence = PASS
Conversation persistence = PASS
ConversationEvidence persistence = PASS
```

---

# 八、Evidence Readiness

确认 Evidence Contract：

```text
evidence_id
dataset_version
source_type
de_identification_attested
de_identification_method
status
created_at
updated_at
```

确认：

```text
FINALIZED
```

仍然是 terminal state。

确认 Provenance 仍严格：

```text
dataset_version
source_type
```

不得出现：

```text
conversation_id
turn_id
assistant_request_id
provider_request_id
```

---

# 九、Annotation Readiness

确认：

```text
Annotation
    ↓
DRAFT
    ↓
REVIEWED
```

Review transition：

```text
DRAFT → REVIEWED
```

仍然合法。

：

```text
REVIEWED → DRAFT
```

仍然非法。

确认 Annotation：

```text
evidence_id
```

仍然绑定 Evidence。

---

# 十、Idempotency Readiness

确认 Evidence：

```text
(source_type, dataset_version)
```

仍是唯一幂等键。

确认：

```text
duplicate
→
existing evidence
```

不会产生新的 Evidence。

确认：

```text
ConversationEvidence
```

使用：

```text
(conversation_id, evidence_id)
```

作为复合唯一标识。

确认 duplicate association：

```text
first-write-wins
```

---

# 十一、Transaction Readiness

确认 Repository transaction boundary：

```python
with factory() as session, session.begin():
```

仍然存在。

至少验证：

```text
Evidence create
Evidence update
Annotation create
Annotation review
ConversationEvidence create
```

没有出现：

```text
partial commit
cross-session hidden commit
raw SQL bypass
```

---

# 十二、Conversation Readiness

确认：

```text
Conversation
ConversationTurn
```

仍然独立。

Conversation 不直接拥有：

```text
evidence_id
annotation_id
```

ConversationEvidence 仍然是独立 association：

```text
Conversation
      ↕
ConversationEvidence
      ↕
Evidence
```

---

# 十三、ConversationEvidence Readiness

确认真实 DB：

```text
ai_ops.conversation_evidence
```

结构：

```text
conversation_id
evidence_id
created_at
```

并且：

```text
PRIMARY KEY
(conversation_id, evidence_id)
```

双 FK：

```text
conversation_id → conversation
evidence_id → evidence_record
```

删除：

```text
Conversation
```

只删除 association。

不得删除 Evidence。

删除：

```text
Evidence
```

只删除 association。

不得删除 Conversation。

---

# 十四、Step 46 Production Runtime Gap

重点检查：

Step 46 当前：

```text
AI Result → Evidence
```

是：

```text
test-composed
```

而不是：

```text
production EvidenceBuilder
```

必须将其分类。

如果当前 Phase 4.1 Roadmap 明确要求：

```text
production runtime 自动创建 Evidence
```

则：

```text
BLOCKER
```

如果 Roadmap 只要求：

```text
验证 Conversation → AI Runtime → Evidence → Association
```

而生产 Evidence Builder 属于后续阶段：

```text
DEFERRED
```

**不要凭感觉判断。**

必须根据：

```text
Roadmap v1.1
Lifecycle Contract
Step 46 evaluation
Step 48 traceability
```

进行判定。

最终在报告中明确：

```text
Evidence Builder:
BLOCKER / DEFERRED
```

以及证据。

---

# 十五、OD-12 Audit

确认：

```text
OD-12
turn-level provenance
```

当前：

```text
DEFERRED
```

检查：

```text
conversation_evidence
```

没有偷偷加入：

```text
turn_id
assistant_request_id
provider_request_id
```

如果没有：

```text
PASS
```

不要为了 Release Readiness 提前关闭 OD-12。

---

# 十六、Security Readiness

重新验证 Step 47 的核心安全边界。

至少检查：

```text
Conversation → Evidence ownership
Evidence → Conversation ownership
Cross-conversation isolation
Evidence reuse
Annotation isolation
Delete cascade
Repository boundary
Read Model boundary
Sensitive metadata
Identity boundary
Provenance boundary
```

重点检查：

```text
api_key
apikey
password
secret
token
authorization
database_url
connection_string
dsn
```

不得进入：

```text
Evidence
Annotation
ConversationEvidence
Read Model
AI Result metadata
```

---

# 十七、Production Code Security Review

对 Step 37～48 修改的 production code 做一次 diff-based review。

重点：

```text
backend/app/db/
backend/app/db/models/
backend/app/services/
```

检查：

```text
raw SQL
credential leakage
unexpected logging
session leakage
connection leakage
authorization bypass
unsafe cascade
unexpected writes
```

OWASP 的安全代码审查建议在重大 release 前复核架构边界、输入验证、数据流、业务逻辑、错误处理和配置，并对修改过的组件做 diff-based review。

---

# 十八、Traceability Readiness

读取 Step 48：

```text
docs/evaluation/Phase 4.1 Step 48 — Regression Traceability Audit.md
```

确认：

```text
Contract Drift = 0
Orphan Implementation = 0
Orphan Test = 0
Traceability Gap = 0
```

如果 Step 49 没有新的代码修改：

这些结果应该保持不变。

---

# 十九、Full Regression

执行：

```powershell
python -m pytest -q
```

记录：

```text
passed
failed
skipped
errors
```

---

# 二十、DB Regression

执行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

记录：

```text
passed
failed
skipped
errors
```

重点判断：

是否仍然只有：

```text
test_db_residue_is_zero
test_baseline_matches_step89_actual_execution
test_gate_passes_when_current_matches_baseline
```

如果出现任何**新增失败**：

```text
BLOCKER
```

不要修改测试。

不要修改 baseline。

---

# 二十一、历史 3 个 DB Baseline Failure

如果仍然只有：

```text
3 failed
```

则分别执行：

```powershell
python -m pytest -q tests/<对应测试文件>::<对应测试>
```

确认单测试行为。

然后：

```text
RUN_DB_TESTS=1
```

重新执行相关测试。

确认它们与 Step 42～48 的历史状态一致。

最终分类：

```text
PRE_EXISTING NON-BLOCKER
```

前提：

```text
不是 Step 49 引入
不是 Phase 4.1 新失败
不影响 Frozen Contract
不影响 Security
不影响真实 DB persistence tests
```

---

# 二十二、Focused Regression

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

要求：

```text
NEW FAILURE = 0
```

---

# 二十三、Contract Regression

执行：

```powershell
python -m pytest -q `
  tests/test_conversation_architecture_contract.py `
  tests/test_conversation_model_contract.py `
  tests/test_conversation_persistence_contract.py `
  tests/test_evidence_persistence_boundary.py `
  tests/test_phase_4_1_traceability.py
```

要求：

```text
FAILED = 0
```

---

# 二十四、Matrix Gate

执行：

```powershell
python scripts/run_matrix_gate.py
```

要求：

```text
Status: PASS
Exit code: 0
```

如果失败：

分类：

```text
new regression
historical baseline
environment issue
```

不要直接修改 Gate。

---

# 二十五、Compile

执行：

```powershell
python -m compileall -q backend tests scripts
```

要求：

```text
COMPILEALL OK
```

---

# 二十六、Lint / Static Check

如果项目已经存在：

```text
ruff
flake8
mypy
pyright
eslint
```

使用当前项目已经存在的命令。

不要新安装工具。

不要新增 lint configuration。

如果当前项目没有 lint：

记录：

```text
Lint = NOT CONFIGURED
```

不要把它伪造成 PASS。

---

# 二十七、Documentation Readiness

确认以下文档存在：

```text
Phase 4.1 Roadmap v1.1
Phase 4.1 Lifecycle Contract
Step 48 Traceability Audit
Step 47 Security Boundary Audit
Step 46 Real Conversation Evidence E2E
```

确认：

```text
Roadmap
↔
Lifecycle Contract
↔
Implementation
↔
Tests
↔
Evaluation
```

没有明显矛盾。

---

# 二十八、Release Artifact Audit

确认不存在：

```text
API Key
DeepSeek Key
SiliconFlow Key
password
database URL
connection string
authorization header
```

出现在：

```text
docs/
tests/
reports/
evaluation/
```

如果发现：

```text
BLOCKER
```

如果只是：

```text
fake placeholder
```

例如：

```text
<API_KEY>
```

不算泄露。

---

# 二十九、Database Residue

检查：

```text
Evidence
Annotation
Conversation
ConversationTurn
ConversationEvidence
```

不得存在：

```text
unexpected production/test residue
```

Step 49 自己：

```text
DB writes = 0
```

---

# 三十、API Readiness

确认 Step 49 没有修改：

```text
API contract
DTO
request schema
response schema
```

Phase 4.1 不因 Release Readiness 新增 API。

如果发现 API 已发生未记录变化：

```text
BLOCKER
```

---

# 三十一、Release Decision Matrix

新增：

```text
docs/evaluation/Phase 4.1 Step 49 — Release Readiness Audit.md
```

必须建立：

| Area                 | Status            | Classification   | Evidence           |
| -------------------- | ----------------- | ---------------- | ------------------ |
| Architecture         | PASS              | —                | Roadmap            |
| Persistence          | PASS              | —                | DB tests           |
| Provenance           | PASS              | —                | Contract tests     |
| Annotation           | PASS              | —                | DB tests           |
| Idempotency          | PASS              | —                | DB tests           |
| Lifecycle            | PASS              | —                | Lifecycle tests    |
| Conversation         | PASS              | —                | Contract tests     |
| ConversationEvidence | PASS              | —                | DB tests           |
| Real E2E             | PASS              | —                | Step46             |
| Security             | PASS              | —                | Step47             |
| Traceability         | PASS              | —                | Step48             |
| Full Regression      | PASS / HISTORICAL | —                | pytest             |
| DB Regression        | PASS / HISTORICAL | —                | pytest             |
| Matrix               | PASS              | —                | matrix gate        |
| Compile              | PASS              | —                | compileall         |
| DB Writes            | 0                 | —                | DB audit           |
| Evidence Builder     | ?                 | DEFERRED/BLOCKER | Step46/roadmap     |
| OD-12                | DEFERRED          | DEFERRED         | Lifecycle Contract |

---

# 三十二、Release Blocker 清单

明确列出：

```text
## BLOCKERS

- None
```

或者：

```text
## BLOCKERS

1. ...
```

不得把：

```text
DEFERRED
OUT_OF_SCOPE
PRE_EXISTING
```

混入 BLOCKERS。

---

# 三十三、Non-blocker 清单

例如：

```text
## NON-BLOCKERS

1. 3 historical DB baseline failures
```

必须解释：

```text
为什么不是本阶段引入
为什么不破坏 Frozen Contract
为什么不影响核心 DB tests
```

---

# 三十四、Deferred 清单

至少检查：

```text
OD-12 turn-level provenance
Evidence Builder
```

只记录真实状态。

不要擅自关闭。

---

# 三十五、Out-of-scope 清单

保持：

```text
RBAC
Multi-tenancy
ACL
Object-level Authorization
Agent
MCP
Memory
Workflow
Chat UI
Streaming
```

---

# 三十六、Release Readiness 判定

只有：

```text
BLOCKERS = 0
```

才允许：

```text
RELEASE READY
```

即使：

```text
NON-BLOCKER > 0
DEFERRED > 0
OUT_OF_SCOPE > 0
```

也不自动阻塞。

但必须记录。

---

# 三十七、如果 Release Blocked

如果发现 Blocker：

立即停止。

不要：

```text
修复
commit
push
PR
merge
进入 Step 50
```

报告：

```text
RELEASE BLOCKED

Blocker:
Evidence:
Impact:
Root cause:
Recommended next action:
```

---

# 三十八、如果 Release Ready

如果：

```text
BLOCKERS = 0
```

输出：

```text
PHASE 4.1 RELEASE READY
```

但：

**不要执行 Git Closeout。**

Step 50 才执行：

```text
commit
push
PR
merge
```

---

# 三十九、不要在 Step 49 修改生产逻辑

Step 49 如果发现：

```text
代码可以更好
架构可以优化
Repository 可以重构
Evidence Builder 可以实现
RBAC 可以增加
```

全部：

```text
NOT IN SCOPE
```

除非属于明确 Blocker。

---

# 四十、最终报告格式

完成后严格按照：

```text
【Phase 4.1 Step 49 COMPLETE】

1. Release Scope
2. Architecture Readiness
3. Persistence Readiness
4. Lifecycle Readiness
5. Conversation Readiness
6. ConversationEvidence Readiness
7. E2E Readiness
8. Security Readiness
9. Traceability Readiness
10. Regression
11. DB Regression
12. Matrix
13. Compile / Lint
14. Documentation
15. DB Schema
16. DB Residue
17. Production DB Writes
18. Release Blockers
19. Non-blockers
20. Deferred
21. Out-of-scope
22. Evidence Builder Decision
23. OD-12 Decision
24. Git Status
25. Final Decision
```

最终必须明确：

```text
Final Decision:

RELEASE READY
```

或者：

```text
Final Decision:

RELEASE BLOCKED
```

如果是：

```text
RELEASE READY
```

最后：

```text
Next:
Phase 4.1 Step 50 — Git Closeout / Push / PR / Merge
```

然后：

**立即停止。**

不要执行 Step 50。

不要 commit。

不要 push。

不要 PR。

不要 merge。

不要进入 Phase 4.2。

不要开发 Agent / MCP / Memory / Workflow。
