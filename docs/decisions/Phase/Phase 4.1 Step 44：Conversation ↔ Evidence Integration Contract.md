你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 4.1 Step 44：Conversation ↔ Evidence Integration Contract

## 一、阶段目标

本阶段只解决一个问题：

> **冻结 Conversation 与 Evidence 之间的关系、所有权、引用语义、基数、身份边界和生命周期交互规则。**

当前已经完成：

```text
Step 37  Evidence / Annotation Persistence
Step 38  Provenance Persistence
Step 39  Evidence ↔ Annotation Association
Step 40  Idempotency / Transaction Hardening
Step 41  Annotation Review Transition
Step 42  Finalization Contract
Step 43  Evidence × Annotation Lifecycle Consistency
```

现在进入：

```text
Step 44
Conversation ↔ Evidence
Integration Contract
```

本阶段是：

```text
Architecture / Contract Design
```

不是：

```text
Database Implementation
Service Implementation
API Implementation
Runtime Integration
```

---

# 二、必须严格遵守的范围

## 允许

* 阅读现有 Conversation / Evidence / Annotation 实现
* 搜索现有 Conversation 与 Evidence 的引用关系
* 分析已有 `conversation_id`
* 分析已有 `turn_id`
* 分析已有 `assistant_request_id`
* 分析 Evidence 的 `dataset_version`
* 分析 Evidence 的 `source_type`
* 冻结 Conversation ↔ Evidence 所有权语义
* 冻结 Reference / Ownership 语义
* 冻结 cardinality
* 冻结 identity boundary
* 冻结 lifecycle interaction
* 更新 Lifecycle Contract
* 更新 Phase 4.1 Roadmap
* 新增最小契约审计测试（如果确实有必要）
* 新增 Step 44 任务 / decision 文档

## 禁止

本阶段禁止修改：

```text
Evidence ORM
Annotation ORM
Conversation ORM
ConversationTurn ORM
EvidenceRepository
ConversationRepository
ConversationService
AIOrchestrator
AI Router
RAG
Tool
Text-to-SQL
```

禁止：

```text
新增数据库表
新增 FK
新增 conversation_id 到 evidence_record
新增 evidence_id 到 conversation
新增 evidence_id 到 conversation_turn
新增 association table
新增 Service
新增 API
新增 Runtime Integration
```

禁止：

```text
Conversation → Evidence 自动持久化
Evidence → Conversation 自动回写
```

禁止：

```text
Agent
MCP
Workflow
Memory
Planning
Multi-Agent
Chat UI
Streaming
RBAC
Multi-tenancy
```

禁止 Git：

```text
commit
push
PR
merge
```

Git 收口仍然属于 Step 50。

---

# 三、开始前必须阅读真实代码

不要根据历史描述直接假设接口。

先阅读：

```text
backend/app/db/models/conversation.py
backend/app/db/models/conversation_turn.py
backend/app/db/models/evidence_record.py
backend/app/db/models/evidence_annotation_record.py

backend/app/db/conversation_repository.py
backend/app/db/evidence_repository.py

backend/app/services/
backend/app/api/
```

重点搜索：

```text
conversation_id
turn_id
assistant_request_id
evidence_id
Evidence
EvidenceRecord
Annotation
dataset_version
source_type
```

同时阅读：

```text
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
```

以及：

```text
docs/architecture.md
docs/requirements.md
docs/decisions/
```

---

# 四、Step 27 / 31 / 36–43 的约束必须继承

尤其检查历史 Evidence Identity / Provenance Contract。

已经冻结：

```text
Evidence Provenance
=
dataset_version
+
source_type
```

Evidence 不允许使用：

```text
conversation_id
turn_id
assistant_request_id
provider_request_id
```

作为：

```text
Evidence identity
Evidence provenance
```

因此本阶段：

**绝对不要为了建立 Conversation ↔ Evidence 关系而把 `conversation_id` 加入 `evidence_record`。**

这是本阶段最重要的边界之一。

---

# 五、当前已知数据模型

先确认真实代码是否仍然如此。

## Conversation

当前预期：

```text
ai_ops.conversation

conversation_id
project_id
status
created_at
updated_at
```

## ConversationTurn

当前预期：

```text
conversation_turn

turn_id
conversation_id
role
content
assistant_request_id
created_at
```

其中：

```text
assistant_request_id
```

目前只是：

```text
correlation identifier
```

不是 Evidence identity。

---

# 六、Evidence

当前预期：

```text
ai_ops.evidence_record

evidence_id
dataset_version
source_type
de_identification_attested
de_identification_method
status
created_at
updated_at
```

Provenance：

```text
dataset_version
+
source_type
```

已经冻结。

不要修改。

---

# 七、Evidence 与 Conversation 的关系必须回答六个问题

本阶段必须明确：

## Q1：Ownership

问题：

> Evidence 是 Conversation-owned，还是独立的 Evidence-owned 数据？

当前架构倾向：

```text
Evidence = Independent Domain Object
```

Conversation 只是：

```text
Reference
```

不是：

```text
Owner
```

必须通过真实代码 / 历史决策文档确认。

如果证据不足：

```text
UNDEFINED
```

不要猜。

---

# 八、Q2：Conversation → Evidence Cardinality

必须确定：

```text
一个 Conversation 可以引用多少 Evidence？
```

候选：

```text
0..1
0..N
1..N
```

重点考虑：

一个 Conversation 可能经历：

```text
Turn 1
  ↓
RAG Evidence A

Turn 2
  ↓
Text-to-SQL Evidence B

Turn 3
  ↓
Tool Evidence C
```

因此不能因为一个 Conversation 当前只有一个 Evidence 就推导：

```text
Conversation → 1 Evidence
```

必须有契约依据。

优先判断：

```text
Conversation → 0..N Evidence
```

是否与现有架构一致。

如果最终确定：

```text
0..N
```

必须记录为：

```text
FROZEN
```

而不是：

```text
Observed
```

---

# 九、Q3：Evidence → Conversation Cardinality

必须回答：

> 一个 Evidence 是否可以被多个 Conversation 引用？

候选：

```text
0..1
0..N
```

重点：

Evidence 是否是可复用对象。

当前架构历史决策倾向：

```text
Evidence = Reusable
```

因此很可能：

```text
Evidence → 0..N Conversation
```

但必须通过：

```text
Step 27
Step 31
Step 36–43
```

的真实文档和实现确认。

如果证据不足：

```text
UNDEFINED
```

---

# 十、Q4：Reference vs Ownership

必须明确：

```text
Conversation
     ↓
  Reference
     ↓
 Evidence
```

而不是：

```text
Conversation
     ↓
 owns
     ↓
 Evidence
```

要求：

### Conversation 删除

不能自动推导：

```text
DELETE Evidence
```

### Evidence 删除

也不能自动推导：

```text
DELETE Conversation
```

### Conversation 生命周期

不能改变：

```text
Evidence.status
```

### Evidence 生命周期

不能自动改变：

```text
Conversation.status
```

除非现有正式契约明确规定。

如果没有：

```text
FROZEN = no cascade lifecycle coupling
```

---

# 十一、Q5：Identity Boundary

这是本阶段非常重要的一项。

必须冻结：

## Conversation Identity

```text
conversation_id
```

## Turn Identity

```text
turn_id
```

## Request Correlation

```text
assistant_request_id
```

## Evidence Identity

```text
evidence_id
```

## Evidence Provenance

```text
dataset_version
source_type
```

这些身份不得混淆。

明确禁止：

```text
conversation_id = evidence_id
turn_id = evidence_id
assistant_request_id = evidence_id
```

也禁止：

```text
conversation_id
```

成为：

```text
Evidence provenance
```

---

# 十二、Q6：Lifecycle Interaction

必须回答以下问题：

## 1. Conversation 创建

是否自动创建 Evidence？

预期：

```text
NO
```

如果无正式契约：

```text
FROZEN = no automatic Evidence creation
```

---

## 2. Conversation Turn 创建

是否自动创建 Evidence？

不要假设。

当前 Step 44 只冻结：

```text
Conversation Turn ≠ Evidence
```

如果未来 Runtime 要建立关联：

属于：

```text
Step 45 / Step 46
```

---

## 3. Evidence 创建

是否自动绑定当前 Conversation？

不能因为运行时恰好有：

```text
conversation_id
```

就把它写入 Evidence。

当前阶段：

```text
Evidence identity independent from Conversation
```

---

## 4. Evidence Review

Evidence：

```text
ANNOTATED
→
REVIEWED
```

不能自动修改：

```text
Conversation.status
```

除非有正式契约。

---

## 5. Evidence Finalization

Evidence：

```text
REVIEWED
→
FINALIZED
```

不能自动：

```text
close Conversation
complete Conversation
archive Conversation
```

除非未来契约明确规定。

---

# 十三、Q7：Association Model

这是 Step 44 最关键的架构决策之一。

需要比较至少两个候选。

---

## Candidate A：直接 FK

例如：

```text
evidence_record.conversation_id
```

或者：

```text
conversation.evidence_id
```

分析：

### 优点

```text
简单
查询直接
```

### 缺点

无法自然表达：

```text
Conversation → multiple Evidence
Evidence → multiple Conversation
```

如果 Evidence 可复用，则模型受限。

因此需要明确记录：

```text
适用性
限制
是否符合当前 Domain Contract
```

---

## Candidate B：Association Table

例如未来：

```text
conversation_evidence
```

可能包含：

```text
conversation_id
evidence_id
created_at
```

注意：

**Step 44 只讨论，不创建。**

分析：

```text
Conversation 0..N Evidence
Evidence 0..N Conversation
```

是否更自然。

如果选择 Candidate B：

只能记录：

```text
Candidate / Recommended Model
```

不能实施。

---

## Candidate C：No Persistence Association

即：

```text
Conversation
    |
    | runtime reference only
    ↓
Evidence
```

分析是否足以满足：

```text
Step 44
Step 45
Step 46
```

的需求。

如果未来 Step 45 / Step 46 明确要求：

```text
persistent read-back
```

则 Candidate C 很可能不足。

但 Step 44 不应提前实现。

---

# 十四、Association Semantics

如果最终决定未来采用：

```text
conversation_evidence
```

必须定义：

## Association 是什么？

不是：

```text
Evidence ownership
```

而是：

```text
Evidence Reference
```

语义：

```text
Conversation references Evidence
```

而不是：

```text
Conversation owns Evidence
```

---

# 十五、Duplicate Association

未来关联是否允许重复：

例如：

```text
Conversation A
    ↓
Evidence E
```

再次引用：

```text
Conversation A
    ↓
Evidence E
```

候选：

### A

允许重复。

### B

幂等：

```text
(conversation_id, evidence_id)
```

唯一。

### C

允许重复，但增加：

```text
turn_id
```

区分引用位置。

必须结合未来用途分析。

不要直接实现。

---

# 十六、Turn-level Reference

必须单独分析：

```text
Conversation
   ↓
ConversationTurn
   ↓
Evidence
```

是否需要：

```text
ConversationTurn ↔ Evidence
```

关系。

注意：

这与：

```text
Conversation ↔ Evidence
```

不是同一个问题。

必须回答：

### 方案 1

只关联：

```text
Conversation ↔ Evidence
```

### 方案 2

关联：

```text
ConversationTurn ↔ Evidence
```

### 方案 3

两者都关联。

分析：

```text
Query / Answer / Evidence
```

的可追溯性。

但是：

**本阶段不新增字段。**

---

# 十七、未来 E2E 要求

Step 44 必须明确 Step 46 未来需要验证：

```text
Conversation
    ↓
AI Runtime
    ↓
AI Result
    ↓
Evidence
    ↓
Conversation Evidence Reference
    ↓
Read-back
```

至少需要验证：

```text
identity
association
isolation
persistence
read-back
```

但：

**Step 44 不实现 E2E。**

---

# 十八、Conversation Isolation

必须冻结未来安全边界：

```text
Conversation A
    ↓
Evidence A
```

不能因为：

```text
Evidence B
```

存在于数据库中，就自动被：

```text
Conversation A
```

读取。

未来 Step 46 / 47 必须验证：

```text
A cannot read B
```

但：

**Step 44 不实现权限系统。**

RBAC / Multi-tenancy 仍然 OUT OF SCOPE。

---

# 十九、Evidence Reuse

如果 Evidence 是：

```text
Reusable
```

必须明确：

```text
Evidence E
   ├── Conversation A
   ├── Conversation B
   └── Conversation C
```

是合法架构模型。

这意味着：

```text
Conversation deletion
```

不能直接：

```text
DELETE Evidence E
```

因为其他 Conversation 可能仍然引用 E。

因此：

```text
Reference ≠ Ownership
```

必须成为 Lifecycle Contract 的正式内容。

---

# 二十、删除 / 生命周期规则

必须冻结以下原则：

| 操作                         | 默认契约               |
| -------------------------- | ------------------ |
| Delete Conversation        | 不删除 Evidence       |
| Delete Evidence            | 不删除 Conversation   |
| Conversation status change | 不自动改变 Evidence     |
| Evidence status change     | 不自动改变 Conversation |
| Annotation review          | 不自动改变 Conversation |
| Evidence finalization      | 不自动完成 Conversation |

如果某一项无法从现有契约得到：

标记：

```text
UNDEFINED
```

不要猜。

---

# 二十一、Step 44 不做什么

不要因为分析发现未来需要：

```text
conversation_evidence
```

就现在创建：

```text
ai_ops.conversation_evidence
```

不要因为发现需要：

```text
turn_id
```

就修改：

```text
conversation_turn
```

不要因为未来要：

```text
GET /conversations/{id}/evidence
```

就创建 API。

不要因为未来要：

```text
ConversationEvidenceService
```

就创建 Service。

Step 44 的交付物是：

```text
Contract
```

不是：

```text
Implementation
```

---

# 二十二、文档修改

更新：

```text
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
```

新增章节：

```text
## Conversation ↔ Evidence Integration Contract
```

建议至少包含：

```text
1. Domain Objects
2. Ownership
3. Reference Semantics
4. Cardinality
5. Identity Boundary
6. Association Model Candidates
7. Turn-level Reference
8. Lifecycle Interaction
9. Reuse Semantics
10. Delete Semantics
11. Conversation Isolation
12. Future Persistence Contract
13. Step 45 Boundary
14. Step 46 Boundary
15. Open Decisions
```

---

# 二十三、Roadmap 更新

更新：

```text
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
```

Step 44 必须记录：

```text
Step 44 = COMPLETE
```

并记录：

```text
Conversation ↔ Evidence Contract = FROZEN / PARTIAL
```

如果仍存在：

```text
OD
```

明确列出。

不要为了让 Step 44 “看起来完成”而关闭未知决策。

---

# 二十四、Open Decisions

如果审计后仍无法确定，登记新的：

```text
OD-11
Conversation ↔ Evidence association cardinality

OD-12
Turn-level Evidence reference

OD-13
Duplicate association semantics
```

编号如果现有文档已经使用其他编号：

**以真实文档为准。**

不要重复编号。

如果某个问题已有正式决定：

不要重新创建 OD。

---

# 二十五、测试

本阶段原则：

> **契约优先，不为没有运行时行为的东西制造大量测试。**

优先复用现有测试。

如果需要新增测试，只允许做：

```text
contract / model boundary audit
```

例如：

```text
Evidence 不存在 conversation_id
Conversation 不存在 evidence_id
ConversationTurn identity 与 Evidence identity 独立
```

如果代码审计已经足够证明，不要机械增加测试。

推荐文件：

```text
tests/test_conversation_evidence_contract.py
```

只有确认有必要才创建。

---

# 二十六、DB Schema

最终必须确认：

```text
DB Schema Changes = 0
```

检查：

```text
CREATE TABLE
ALTER TABLE
ADD COLUMN
DROP COLUMN
CREATE INDEX
CREATE UNIQUE
CREATE FK
```

全部：

```text
0
```

特别检查：

```text
conversation_evidence
```

不得被创建。

---

# 二十七、安全检查

确认：

Conversation ↔ Evidence 契约中没有新增：

```text
API key
password
DATABASE_URL
connection string
authorization header
token
```

并确认：

```text
project_id
```

仍然不是：

```text
tenant boundary
```

不要在 Step 44 引入 RBAC / Multi-tenancy。

---

# 二十八、不要修改 Architecture 主文档

除非审计发现：

```text
docs/architecture.md
```

存在必须同步的事实错误。

默认：

```text
architecture.md = 不修改
```

本阶段主要更新：

```text
Lifecycle Contract
Roadmap
```

如果发现 architecture drift：

只记录：

```text
Architecture drift found
→ deferred to Step 51 minimal sync
```

不要提前大规模重写。

---

# 二十九、测试命令

首先运行已有 Conversation / Evidence 测试。

根据实际项目路径执行。

然后：

```powershell
python -m pytest -q
```

再：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

如果新增 Step 44 测试：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_conversation_evidence_contract.py
```

最后：

```powershell
python -m compileall -q backend tests scripts
```

以及：

```powershell
python scripts/run_matrix_gate.py
```

注意：

Step 42 / 43 已确认存在的：

```text
3 个 DB baseline failures
```

如果仍然出现：

```text
test_db_residue_is_zero
test_baseline_matches_step89_actual_execution
test_gate_passes_when_current_matches_baseline
```

不要为了 Step 44 修改 baseline。

先判断是否仍然是同一个 pre-existing issue。

---

# 三十、Git

严格禁止：

```text
git commit
git push
gh pr create
merge
```

Step 37–44 的 Git 收口统一留给：

```text
Step 50
```

---

# 三十一、最终报告格式

完成后严格按照：

```text
【Phase 4.1 Step 44 COMPLETE】

1. Audit Result
2. Conversation Model
3. Evidence Model
4. Ownership
5. Reference Semantics
6. Cardinality
7. Identity Boundary
8. Association Model
9. Turn-level Reference
10. Lifecycle Interaction
11. Evidence Reuse
12. Delete Semantics
13. Conversation Isolation
14. Open Decisions
15. Modified Files
16. DB Schema
17. Tests
18. Full Regression
19. Matrix
20. DB Writes
21. Security
22. Git Status
23. Problems Found
24. Problems Fixed
25. Current Limitations
26. Step 45 Readiness
```

最终必须明确：

```text
Step 44 = COMPLETE
Step 45 = NEXT
```

---

# 三十二、Architecture Boundary

最终输出必须能够形成：

```text
Conversation
    │
    │ references
    ▼
Evidence
    │
    ├── Provenance
    │     ├── dataset_version
    │     └── source_type
    │
    └── Annotation
          └── Review
```

并明确：

```text
Conversation ≠ Evidence Owner
Conversation ≠ Evidence Identity
Conversation ≠ Evidence Provenance
ConversationTurn ≠ Evidence Identity
assistant_request_id ≠ Evidence Identity
```

如果未来选择 Association Table，则只能冻结为：

```text
Conversation
      │
      │ reference
      ▼
conversation_evidence
      │
      ▼
Evidence
```

**Step 44 不创建这张表。**

---

# 三十三、停止条件

完成：

```text
Contract Audit
↓
Ownership
↓
Cardinality
↓
Identity
↓
Reference
↓
Lifecycle
↓
Association Candidate
↓
Open Decisions
↓
Lifecycle Contract
↓
Roadmap
↓
Tests
↓
Regression
```

之后：

**立即停止。**

不要进入 Step 45。

不要创建 `conversation_evidence`。

不要创建 Evidence Service。

不要创建 Conversation Evidence API。

不要接入 AI Runtime。

不要开发 Chat UI。

不要开发 Agent。

不要开发 MCP。

不要进入 Step 46。

只有完成 Step 44 并由我确认后，才进入 Step 45。
