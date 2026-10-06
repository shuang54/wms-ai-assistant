你现在继续操作：

`D:\coding\ai\wms-ai-assistant`

当前：

```text
Phase 4.1
```

Step 37–40 已完成：

```text
Step 37 Evidence / Annotation Persistence
Step 38 Provenance Persistence
Step 39 Evidence ↔ Annotation Association
Step 40 Transaction / Idempotency Hardening
```

刚刚已经完成：

```text
Phase 4.1 Step 41–51 Architecture Recovery Audit
```

Audit 结论：

```text
Roadmap Decision = CHANGE REQUIRED
```

因此：

# 本次任务不是实现 Step 41

本次只完成：

```text
R1 Roadmap Recovery
R2 Step 41–43 Definition Recovery
R3 Lifecycle Contract Freeze
R4 Conversation ↔ Evidence Contract Decision
```

最终形成：

```text
Phase 4.1 Roadmap v1.1
```

并正式冻结。

---

# 一、绝对禁止

本次禁止：

```text
Evidence ORM 修改
Annotation ORM 修改
Conversation ORM 修改
Repository 修改
Service 新增
API 新增
Runtime 修改
数据库 Schema 修改
测试新增
测试修改
Git Commit
Push
PR
Merge
```

本次是：

**Architecture / Roadmap / Decision Documentation Only**

允许修改：

```text
docs/requirements.md
docs/architecture.md
docs/decisions/
```

但必须先判断哪些文档应该修改。

不要为了记录而大规模重写现有文档。

---

# 二、R1：落盘 Phase 4.1 Roadmap

新增正式 Roadmap 文档：

```text
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
```

如果当前项目已有统一 Roadmap 目录：

优先遵循现有结构。

---

# 三、Roadmap v1.1 必须记录

## Phase 4.1 总目标

明确：

```text
Conversation + Evidence Foundation
```

以及：

```text
Persistence
Provenance
Association
Idempotency
Transaction
Lifecycle
Conversation Integration
Security
Evaluation
Release
```

---

# 四、冻结 Step 37–40 历史事实

Roadmap v1.1 必须明确：

```text
Step 37 = COMPLETE
Step 38 = COMPLETE
Step 39 = COMPLETE
Step 40 = COMPLETE
```

分别记录：

## Step 37

```text
Evidence / Annotation Persistence
```

真实结果：

```text
ai_ops.evidence_record
ai_ops.evidence_annotation_record
```

---

## Step 38

```text
Provenance Persistence
```

真实设计：

```text
Provenance
=
Evidence.dataset_version
+
Evidence.source_type
```

没有独立 Provenance Table。

---

## Step 39

```text
Evidence ↔ Annotation Association
```

真实：

```text
Annotation.evidence_id
        ↓
FK
        ↓
Evidence.evidence_id
```

并且：

```text
Cross-Evidence Isolation = PASS
```

---

## Step 40

```text
Transaction / Idempotency Hardening
```

真实：

```text
(source_type, dataset_version)
```

是组合幂等键。

Repository：

```text
first-write-wins
```

DB：

```text
UNIQUE(source_type, dataset_version)
```

---

# 五、R2：重新定义 Step 41

原始定义：

```text
Step 41 = Evidence Review State
```

必须废弃这个表述。

因为：

```text
Evidence REVIEWED
```

已经存在。

Step 36/37 已经实现：

```text
IMPORTED
→ PERSISTED
→ ANNOTATED
→ REVIEWED
→ FINALIZED
```

以及：

```text
EVIDENCE_STATUS_TRANSITIONS
```

因此新定义：

# Step 41 = Annotation Review Transition

目标：

```text
Annotation
    DRAFT
      ↓
   REVIEWED
```

需要解决：

```text
review_status transition
```

包括：

```text
合法迁移
非法迁移
Repository update method
```

但不要解决：

```text
Evidence → REVIEWED
Evidence → FINALIZED
Conversation
```

---

# 六、Step 41 不得自行决定 Evidence Review 前置条件

必须明确：

```text
Evidence ANNOTATED
        ↓
Evidence REVIEWED
```

是否要求：

```text
所有 Annotation = REVIEWED
```

当前：

```text
UNDEFINED
```

因此：

**Step 41 不允许冻结这个规则。**

它属于：

```text
Step 43
```

---

# 七、R2：重新定义 Step 42

Step 42：

# Evidence Finalization Contract

Step 42 不只是：

```text
FINALIZED = terminal state
```

因为这个已经由现有：

```text
EVIDENCE_STATUS_TRANSITIONS
```

实现。

Step 42 的真正任务：

冻结：

```text
FINALIZATION PRECONDITION
FINALIZATION TRANSITION
FINALIZATION POSTCONDITION
FINALIZED IMMUTABILITY
FORBIDDEN TRANSITIONS
```

至少必须回答：

```text
1. Evidence 什么时候可以 FINALIZED？
2. Annotation 是否必须全部 REVIEWED？
3. FINALIZED 后 Annotation 是否还能修改？
4. FINALIZED 后 Provenance 是否还能修改？
5. FINALIZED 后 Evidence status 是否只能保持 FINALIZED？
```

如果没有足够证据：

不要猜。

写：

```text
UNDEFINED
```

并明确：

```text
Decision Required
```

---

# 八、R2：重新定义 Step 43

Step 43：

# Evidence / Annotation Lifecycle Consistency

Step 43 负责冻结：

```text
Evidence.status
+
Annotation.review_status
```

组合关系。

必须形成：

```text
Allowed State Matrix
```

至少覆盖：

```text
PERSISTED + DRAFT
ANNOTATED + DRAFT
ANNOTATED + REVIEWED
REVIEWED + DRAFT
REVIEWED + REVIEWED
FINALIZED + DRAFT
FINALIZED + REVIEWED
```

每个状态：

```text
ALLOWED
FORBIDDEN
UNDEFINED
```

不得猜测。

---

# 九、Step 41–43 Dependency

冻结：

```text
Step 41
Annotation Review Transition
        ↓
Step 42
Finalization Contract
        ↓
Step 43
Lifecycle Consistency
```

原因：

```text
Step 43
需要知道 Annotation 能否 REVIEWED

Step 42
需要知道 Review 是否是 Finalization 前置条件

Step 43
最终冻结两者组合关系
```

因此默认：

```text
41 → 42 → 43
```

Sequential。

---

# 十、R3：冻结 Lifecycle Contract

建立正式 Decision：

```text
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
```

如果项目已有更适合的 Decision 文件：

遵循现有命名规则。

内容必须包括：

```text
Evidence State Machine
Annotation Review State
Transition Rules
Finalization Contract
Allowed State Matrix
Forbidden State Matrix
Immutability
```

但是：

**只冻结当前已经能够由架构证据支持的部分。**

对于没有定义的部分：

必须明确：

```text
UNDEFINED
```

---

# 十一、重要：不要为了完成文档强行设计业务规则

禁止自行规定：

```text
所有 Annotation REVIEWED
→ Evidence 才能 REVIEWED
```

也禁止自行规定：

```text
FINALIZED
→ Annotation 永久不可修改
```

除非：

```text
requirements
architecture
existing decisions
existing tests
business semantics
```

能够证明。

如果没有：

必须记录：

```text
Open Architecture Decision
```

并明确由后续 Step 冻结。

---

# 十二、R4：Conversation ↔ Evidence Contract

这是本次最重要的设计决策。

先不要改代码。

先分析真实业务语义。

必须回答三个问题：

## Question 1

一个 Conversation 是否可能产生多个 Evidence？

例如：

```text
Conversation
 ├── RAG Evidence
 ├── Tool Evidence
 └── SQL Evidence
```

---

## Question 2

一个 Evidence 是否可能被多个 Conversation 引用？

例如：

```text
Evidence E1
   ├── Conversation A
   ├── Conversation B
   └── Conversation C
```

---

## Question 3

Evidence 是：

```text
一次性 Conversation Artifact
```

还是：

```text
Reusable Enterprise Artifact
```

---

# 十三、不要只根据当前代码回答 R4

当前代码：

```text
Conversation → Evidence = 0
```

所以代码只能说明：

```text
CURRENTLY UNIMPLEMENTED
```

不能说明：

```text
未来业务关系
```

必须结合：

```text
requirements.md
architecture.md
decisions/
Phase 3 AI Runtime
RAG
Tool
Text-to-SQL
Evaluation
Evidence
Conversation
```

分析 Evidence 的业务语义。

---

# 十四、R4 Decision Matrix

输出：

| Question                         | Evidence |
| -------------------------------- | -------- |
| Conversation → multiple Evidence | ?        |
| Evidence → multiple Conversation | ?        |
| Evidence reusable                | ?        |
| Owner                            | ?        |
| Reference                        | ?        |

每个答案必须：

```text
DECIDED
```

或者：

```text
UNDEFINED
```

并附理由。

---

# 十五、如果最终判断是 1:N

例如：

```text
Conversation
   │
   ├── Evidence A
   ├── Evidence B
   └── Evidence C
```

则 Step 45 可以考虑：

```text
ConversationEvidence
```

或者：

```text
Evidence.conversation_id
```

但：

**现在不实现。**

只记录：

```text
Candidate Data Model
```

---

# 十六、如果最终判断是 N:M

例如：

```text
Conversation A ─┐
                ├── Evidence X
Conversation B ─┘
```

则 Step 45 候选：

```text
conversation_evidence
```

关联表。

但：

**现在仍然不建表。**

---

# 十七、如果 Evidence 是 Reusable Artifact

必须特别关注：

```text
Evidence
```

是否应该独立于：

```text
Conversation
```

存在。

这种情况下：

```text
Conversation
      ↓
Evidence Reference
```

更可能是：

```text
reference
```

而不是：

```text
ownership
```

但仍然必须根据项目语义判断。

---

# 十八、Step 44 重新定义

Step 44：

# Conversation ↔ Evidence Integration Contract

只负责：

```text
Relationship Contract
Ownership
Reference Semantics
Cardinality
Identity
Lifecycle Interaction
```

不实现 DB。

不实现 API。

不实现 Runtime。

---

# 十九、Step 45 重新定义

Step 45：

# Conversation ↔ Evidence Persistence

根据 Step 44 的最终 Contract：

实现：

```text
DB Model
FK
Association Table
Repository
Indexes
Constraints
Read Model
```

具体模型现在：

```text
UNDEFINED
```

必须等待 Step 44。

---

# 二十、Step 46

保持：

# Real Conversation Evidence E2E

完整：

```text
Conversation
   ↓
AI Runtime
   ↓
Evidence
   ↓
Conversation ↔ Evidence persistence
   ↓
Read Back
```

重点验证：

```text
Identity
Association
Isolation
Persistence
Read Back
```

不重复测试：

```text
RAG
Tool
Text-to-SQL
```

除非 Integration 本身需要。

---

# 二十一、Step 47

保持：

# Security / Boundary Audit

覆盖：

```text
Evidence
Annotation
Provenance
Conversation
Conversation Evidence
```

并增加：

```text
Cross-Conversation Isolation
Cross-Evidence Isolation
```

仍然：

```text
RBAC = NOT IN SCOPE
Multi-tenancy = NOT IN SCOPE
```

---

# 二十二、Step 48

保持：

# Regression / Traceability

建立：

```text
Step
 ↓
Contract
 ↓
Implementation
 ↓
Test
 ↓
Evidence
```

覆盖：

```text
37–47
```

并补齐：

```text
Step 39
Step 40
```

之前缺失的 roadmap traceability。

---

# 二十三、Step 49

保持：

# Release Readiness

必须检查：

```text
Architecture
Tests
Security
Database
Rollback
Docs
Secrets
Git
Production DB Writes
```

最终：

```text
Production DB Writes = 0
```

---

# 二十四、Step 50

保持：

# Commit / Push / PR

冻结 Git 流程：

```text
Phase 4.1 branch
        ↓
all checks green
        ↓
Push
        ↓
PR
        ↓
Required Checks
        ↓
Merge
```

当前：

```text
Step 37/38/39/40 commits
```

仍然：

```text
not pushed
not PR
not merged
```

本次不执行。

---

# 二十五、Step 51

保持：

# Merge Verification

最终：

```text
origin/main
     ↓
Phase 4.1 merge
     ↓
Full Regression
     ↓
Matrix
     ↓
compileall
     ↓
Architecture Verification
```

确认：

```text
Phase 3 unaffected
Phase 4.1 complete
```

---

# 二十六、Roadmap v1.1 最终结构

必须形成：

```text
Phase 4.1
│
├── Persistence Foundation
│   ├── Step 37 Evidence / Annotation Persistence      COMPLETE
│   ├── Step 38 Provenance Persistence                COMPLETE
│   ├── Step 39 Evidence ↔ Annotation Association    COMPLETE
│   └── Step 40 Transaction / Idempotency             COMPLETE
│
├── Lifecycle Foundation
│   ├── Step 41 Annotation Review Transition
│   ├── Step 42 Finalization Contract
│   └── Step 43 Lifecycle Consistency
│
├── Conversation Integration
│   ├── Step 44 Conversation ↔ Evidence Contract
│   ├── Step 45 Conversation ↔ Evidence Persistence
│   └── Step 46 Real Conversation Evidence E2E
│
└── Verification / Release
    ├── Step 47 Security / Boundary Audit
    ├── Step 48 Regression / Traceability
    ├── Step 49 Release Readiness
    ├── Step 50 Commit / Push / PR
    └── Step 51 Merge Verification
```

---

# 二十七、Roadmap Version Rule

必须明确：

```text
Previous:
Phase 4.1 Roadmap v1.0
```

如果历史 v1.0 不存在：

不要伪造。

写：

```text
Previous roadmap:
No formally persisted v1.0 artifact found.
```

本次建立：

```text
Phase 4.1 Roadmap v1.1
```

原因：

```text
Architecture Recovery
```

而不是：

```text
Feature Change
```

---

# 二十八、Requirements / Architecture 文档是否修改

Audit：

判断：

```text
docs/requirements.md
docs/architecture.md
```

是否需要同步。

原则：

### 如果只是 Phase 4.1 execution roadmap

优先：

```text
docs/decisions/Phase/
```

不要大规模修改：

```text
requirements.md
architecture.md
```

### 如果发现 requirements / architecture 与最终冻结 Contract 冲突

必须记录：

```text
Document Drift
```

然后：

```text
最小修改
```

不要重写整份架构文档。

---

# 二十九、Git / DB / Test

本次必须保证：

```text
Production Code Changes = 0
ORM Changes = 0
DB Schema Changes = 0
Tests Added = 0
Tests Modified = 0
DB Writes = 0
Git Commit = 0
Push = 0
PR = 0
Merge = 0
```

---

# 三十、最终输出

严格使用：

```text
【Phase 4.1 Roadmap v1.1 FREEZE】

1. Roadmap Recovery
2. Step 37–40 Historical State
3. Step 41 Revised Definition
4. Step 42 Revised Definition
5. Step 43 Revised Definition
6. Lifecycle Contract
7. Conversation ↔ Evidence Decision
8. Step 44 Definition
9. Step 45 Definition
10. Step 46 Definition
11. Step 47 Definition
12. Step 48 Definition
13. Step 49 Definition
14. Step 50 Definition
15. Step 51 Definition
16. Dependency Graph
17. Data Ownership
18. Service Boundary
19. Database Evolution
20. Security Boundary
21. Non-Goals
22. Architecture Gaps
23. G3 / G4
24. Document Changes
25. Roadmap Version
26. Freeze Decision
27. Recommended Next Step
```

最终必须明确：

```text
Roadmap v1.1 = FROZEN
```

或者：

```text
Roadmap v1.1 = NOT FROZEN
```

如果：

```text
NOT FROZEN
```

必须列出阻塞原因。

---

# 三十一、最终 STOP

如果：

```text
Roadmap v1.1 = FROZEN
```

下一步才允许：

```text
Step 41 Execution Task
```

否则：

**STOP。**

绝对不要开始 Step 41 编码。
