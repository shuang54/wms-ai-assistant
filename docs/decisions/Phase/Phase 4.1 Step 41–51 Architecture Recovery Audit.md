你现在不要实现任何新功能。

当前项目：

`D:\coding\ai\wms-ai-assistant`

当前阶段：

```text
Phase 4.1
```

当前已经完成：

```text
Step 37  Evidence / Annotation Persistence
Step 38  Provenance Persistence
Step 39  Evidence ↔ Annotation Association
Step 40  Transaction / Idempotency Hardening
```

当前要求：

# Phase 4.1 Step 41–51 Architecture Recovery Audit

---

# 一、任务目标

本次任务不是编码。

唯一目标：

**重新审计并冻结 Step 41–51 的完整技术路线、依赖关系、数据边界、职责边界和完成条件。**

禁止：

```text
修改生产代码
修改 ORM
修改数据库
新增测试
新增 Service
新增 API
新增 Workflow
新增 Agent
新增 MCP
新增 Memory
```

本次只允许：

```text
阅读代码
阅读测试
搜索契约
分析架构
形成 Roadmap Audit 文档
```

---

# 二、为什么现在必须做 Recovery Audit

Step 37–40 已经形成：

```text
Evidence Persistence
        ↓
Provenance
        ↓
Annotation Association
        ↓
Idempotency
        ↓
Transaction Boundary
```

这意味着 Persistence Foundation 已经基本稳定。

后续：

```text
Step 41
Step 42
Step 43
```

将开始进入：

```text
Evidence Lifecycle
```

再往后：

```text
Step 44
Step 45
Step 46
```

进入：

```text
Conversation ↔ Evidence
```

最后：

```text
Step 47
Step 48
Step 49
Step 50
Step 51
```

进入：

```text
Security
Regression
Release
Git
Merge Verification
```

如果现在不重新确认边界，很容易出现：

```text
Review
Finalization
Conversation
Workflow
Service
```

之间职责重叠。

所以本次必须先做 Architecture Recovery。

---

# 三、第一步：读取当前真实 Roadmap

先阅读：

```text
docs/
docs/requirements.md
docs/architecture.md
docs/decisions/
```

搜索：

```text
Phase 4.1
Step 41
Step 42
Step 43
Step 44
Step 45
Step 46
Step 47
Step 48
Step 49
Step 50
Step 51
Review
Finalization
Conversation Evidence
Evidence Lifecycle
```

同时搜索：

```text
G3
G4
Evidence
Annotation
Provenance
Conversation
```

不要假设历史 Roadmap 内容仍然完全有效。

---

# 四、第二步：读取当前真实实现

必须阅读：

```text
backend/app/db/models/evidence_record.py
backend/app/db/models/evidence_annotation_record.py
backend/app/db/evidence_repository.py
backend/app/db/models/__init__.py
```

以及：

```text
backend/app/services/
backend/app/api/
backend/app/projects/
```

重点搜索：

```text
Evidence
Annotation
Provenance
Conversation
Trace
AIOrchestrationResult
```

---

# 五、第三步：读取 Step 27–40 的契约来源

重点寻找：

```text
EvidenceProvenance
EvidenceRecord
EvidenceAnnotationRecord
EvidenceWithProvenance
AnnotationRow
```

搜索：

```text
dataset_version
source_type
evidence_id
annotation_id
case_id
annotation_version
annotator_id
review_status
status
created_at
updated_at
```

建立一个当前真实数据模型：

```text
Evidence
Annotation
Provenance
Conversation
```

不要根据记忆重建。

必须根据代码和测试实际结果。

---

# 六、第四步：建立当前 Persistence Model

输出当前真实模型：

```text
Evidence
├── evidence_id
├── dataset_version
├── source_type
├── de_identification_attested
├── de_identification_method
├── status
├── created_at
└── updated_at

Annotation
├── annotation_id
├── evidence_id
├── case_id
├── annotation_version
├── annotator_id
├── review_status
├── created_at
└── updated_at
```

如果实际字段不同：

**以真实代码为准。**

不要自行修改字段。

---

# 七、第五步：冻结当前状态机

重点检查：

```text
Evidence.status
Annotation.review_status
```

历史契约中：

Evidence：

```text
IMPORTED
    ↓
PERSISTED
    ↓
ANNOTATED
    ↓
REVIEWED
    ↓
FINALIZED
```

Annotation：

```text
DRAFT
    ↓
REVIEWED
```

现在必须确认：

1. 这些状态是否已经存在于真实 ORM？
2. 是否只是测试契约？
3. 是否已经存在 Repository 状态修改方法？
4. 是否存在状态迁移约束？
5. 当前 Step 37–40 是否已经错误地实现了部分生命周期？

如果发现：

```text
status
review_status
```

已经有真实实现：

不要重复设计。

如果只有 contract：

记录为未来 Step 的实现。

---

# 八、第六步：定义 Step 41

Step 41 目标候选：

```text
Evidence Review State
```

但必须根据 Audit 确认。

Step 41 应该只解决：

```text
Review State
```

不应该解决：

```text
Finalization
Conversation
Workflow
```

需要明确：

```text
Evidence.review_status
Annotation.review_status
```

之间是否存在关系。

必须回答：

> Evidence REVIEWED 是否意味着所有 Annotation 都 REVIEWED？

如果没有现有契约：

**不要自行规定。**

记录：

```text
Contract Gap
```

然后提出最小设计建议。

---

# 九、第七步：定义 Step 42

Step 42：

```text
Finalization
```

必须分析：

```text
什么叫 FINALIZED？
谁可以触发？
Finalized 后 Evidence 是否 immutable？
Annotation 是否还能修改？
Provenance 是否还能修改？
```

尤其检查：

```text
Step 40 transaction
Step 41 review
Step 42 finalization
```

三者之间的依赖。

不要实现。

只建立：

```text
Precondition
Transition
Postcondition
Forbidden Transition
```

例如：

```text
DRAFT
 ↓
REVIEWED
 ↓
FINALIZED
```

但如果当前 contract 不支持该状态机：

不要直接冻结这个例子。

必须以真实代码/requirements/decisions 为依据。

---

# 十、第八步：定义 Step 43

Step 43：

```text
Lifecycle Consistency
```

重点不是增加状态。

重点是验证：

```text
Evidence.status
Annotation.review_status
Finalization
```

之间不会出现非法组合。

建立：

```text
Allowed State Matrix
```

例如：

| Evidence  | Annotation | 是否允许 |
| --------- | ---------- | ---- |
| PERSISTED | DRAFT      | ?    |
| ANNOTATED | DRAFT      | ?    |
| REVIEWED  | REVIEWED   | ?    |
| FINALIZED | REVIEWED   | ?    |
| FINALIZED | DRAFT      | ?    |

**所有 `?` 必须根据当前 contract 得出。**

禁止猜测。

---

# 十一、第九步：定义 Step 44

Step 44：

```text
Conversation Evidence Integration Contract
```

这是整个 Phase 4.1 的关键边界。

必须找到当前：

```text
Conversation
ConversationContext
ConversationTrace
AIOrchestrationResult
Evidence
```

之间的真实关系。

建立：

```text
Conversation
     ↓
AI Runtime
     ↓
AI Result
     ↓
Evidence Reference
```

重点回答：

> Conversation 是 Evidence 的 owner，还是 Evidence 只是 Conversation 的引用？

不要直接新增外键。

必须先分析。

---

# 十二、第十步：定义 Step 45

Step 45：

```text
Conversation ↔ Evidence Persistence
```

需要明确数据关系。

候选模型可能包括：

```text
Conversation
    └── evidence_id
```

或者：

```text
ConversationEvidence
    ├── conversation_id
    └── evidence_id
```

或者其他模型。

**禁止现在决定。**

必须根据：

```text
当前 Conversation model
当前 Evidence model
历史 Step 27 contract
Conversation Trace
AI result
```

综合判断。

特别检查：

```text
一个 Conversation
是否可以有多个 Evidence？

一个 Evidence
是否可以被多个 Conversation 引用？

Evidence 是一次性产物还是可复用对象？
```

这些答案会直接决定：

```text
FK
Association Table
Unique Constraint
```

---

# 十三、第十一步：定义 Step 46

Step 46：

```text
Real Conversation Evidence E2E
```

目标：

```text
真实 Conversation
      ↓
AI Runtime
      ↓
Evidence
      ↓
Persistence
      ↓
Read Back
```

但不能重新测试：

```text
RAG
Tool
Text-to-SQL
```

除非当前 Conversation 集成确实需要。

Step 46 的重点：

```text
Conversation
+
AI Result
+
Evidence
+
Persistence
```

不是重新做 AI Runtime。

---

# 十四、第十二步：定义 Step 47

Step 47：

```text
Security / Boundary Audit
```

必须覆盖：

```text
Evidence
Annotation
Provenance
Conversation
```

检查：

```text
API Key
Password
DATABASE_URL
Connection String
Authorization Header
Session
Connection
Engine
ORM
Raw Content
File Path
```

不得泄漏。

还需要检查：

```text
Cross-Conversation
Cross-Evidence
Cross-Project
```

但：

**RBAC / Multi-tenancy 尚未进入 Phase 4.1。**

不要偷偷实现 RBAC。

---

# 十五、第十三步：定义 Step 48

Step 48：

```text
Regression / Traceability
```

目标不是新增功能。

而是建立：

```text
Roadmap Step
     ↓
Contract
     ↓
Implementation
     ↓
Test
     ↓
Evidence
```

Traceability Matrix。

至少覆盖：

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

确保：

```text
每一个 Step
都有明确测试证据
```

不要修改 Matrix baseline。

---

# 十六、第十四步：定义 Step 49

Step 49：

```text
Release Readiness
```

必须检查：

```text
Architecture
Tests
Security
DB
Migration
Rollback
Docs
Git
Secrets
Production Writes
```

重点确认：

```text
Production DB writes = 0
```

以及：

```text
No untracked production files
No secret leakage
No forbidden architecture
```

---

# 十七、第十五步：定义 Step 50

Step 50：

```text
Commit / Push / PR
```

这里必须冻结 Git 流程。

当前实际状态：

```text
phase4.1
```

历史已经有：

```text
Step 37 commit
Step 38 commit
Step 39 commit
Step 40 commit
```

但：

```text
未 push
未 PR
未 merge
```

本次只审计：

```text
最终应该如何合并
```

不要执行 Git 操作。

---

# 十八、第十六步：定义 Step 51

Step 51：

```text
Merge Verification
```

最终验证：

```text
origin/main
      ↓
merge
      ↓
phase4.1
      ↓
full regression
      ↓
Matrix
      ↓
compileall
      ↓
architecture verification
```

最终必须能够证明：

```text
Phase 4.1
```

完整进入：

```text
main
```

且没有破坏 Phase 3。

---

# 十九、建立完整 Dependency Graph

最终必须输出：

```text
Step 37
   ↓
Step 38
   ↓
Step 39
   ↓
Step 40
   ↓
Step 41
   ↓
Step 42
   ↓
Step 43
   ↓
Step 44
   ↓
Step 45
   ↓
Step 46
   ↓
Step 47
   ↓
Step 48
   ↓
Step 49
   ↓
Step 50
   ↓
Step 51
```

但是如果实际 Audit 发现可以并行：

必须明确标注：

```text
Sequential
Parallel
Dependency
```

不要为了形式强行线性化。

---

# 二十、建立 Data Ownership Matrix

必须输出：

| 数据                              | Owner | 创建 | 修改 | 最终化 | 查询 |
| ------------------------------- | ----- | -- | -- | --- | -- |
| Evidence                        | ?     | ?  | ?  | ?   | ?  |
| Annotation                      | ?     | ?  | ?  | ?   | ?  |
| Provenance                      | ?     | ?  | ?  | ?   | ?  |
| Conversation                    | ?     | ?  | ?  | ?   | ?  |
| Conversation Evidence Reference | ?     | ?  | ?  | ?   | ?  |

所有 `?` 必须通过当前代码 / contract / architecture 分析。

---

# 二十一、建立 Service Boundary Matrix

必须分析：

```text
Repository
Service
Runtime
API
```

例如：

| 能力                    | Repository | Service | Runtime | API |
| --------------------- | ---------- | ------- | ------- | --- |
| Evidence CRUD         | ?          | ?       | -       | ?   |
| Annotation CRUD       | ?          | ?       | -       | ?   |
| Review                | ?          | ?       | -       | ?   |
| Finalization          | ?          | ?       | -       | ?   |
| Conversation Evidence | ?          | ?       | ?       | ?   |

重点：

**不要因为未来需要 Service，就现在创建 Service。**

如果当前架构还没有必要：

明确：

```text
Deferred
```

---

# 二十二、建立 State Transition Matrix

必须最终形成：

```text
Evidence.status
```

以及：

```text
Annotation.review_status
```

的合法状态转换。

格式：

```text
Current
   ↓
Event
   ↓
Next
```

同时列出：

```text
Forbidden transitions
```

如果 contract 缺失：

明确写：

```text
UNDEFINED
```

不要自行填答案。

---

# 二十三、建立 Database Evolution Matrix

必须明确：

| Step | New Table | New Column | New FK | New Index | Constraint |
| ---- | --------: | ---------: | -----: | --------: | ---------: |
| 37   |         ? |          ? |      ? |         ? |          ? |
| 38   |         ? |          ? |      ? |         ? |          ? |
| 39   |         0 |          0 |      0 |         0 |          0 |
| 40   |         0 |          0 |      0 |         0 |          0 |
| 41   |         ? |          ? |      ? |         ? |          ? |
| 42   |         ? |          ? |      ? |         ? |          ? |
| 43   |         ? |          ? |      ? |         ? |          ? |
| 44   |         ? |          ? |      ? |         ? |          ? |
| 45   |         ? |          ? |      ? |         ? |          ? |
| 46   |         ? |          ? |      ? |         ? |          ? |

注意：

Step 39 / Step 40 已经确认：

```text
DB Schema Changes = 0
```

后续不要为了方便测试随意修改 schema。

---

# 二十四、建立 Non-Goals Matrix

必须确认剩余 Phase 4.1 仍然禁止：

```text
Agent
Multi-Agent
MCP
Workflow Engine
Memory
Planning
Autonomous Loop
RBAC
Multi-tenancy
Chat UI
Streaming
Long-term Memory
Tool marketplace
```

如果某项确实成为 Phase 4.1 必需：

不能直接加入。

必须记录：

```text
Roadmap Change Required
```

---

# 二十五、识别 Architecture Gaps

重点寻找：

```text
G1
G2
G3
G4
```

以及其他：

```text
missing contract
missing service
missing API
missing persistence
missing lifecycle rule
```

每个 Gap 必须：

```text
Gap
Impact
Required Step
Blocking?
```

例如：

```text
G3
Evidence lifecycle service absent
Impact = Finalization cannot be exposed safely
Required = Step 42
Blocking = YES
```

不要直接修。

---

# 二十六、判断 Step 41–51 是否需要修改 Roadmap

最终必须回答：

```text
Roadmap v1.0
是否仍然成立？
```

只能输出：

```text
UNCHANGED
```

或者：

```text
CHANGE REQUIRED
```

如果：

```text
CHANGE REQUIRED
```

必须先停止。

不要开始 Step 41。

因为：

**Roadmap 必须先变更，再执行 Step。**

---

# 二十七、输出最终 Recovery Report

严格使用：

```text
【Phase 4.1 Step 41–51 ARCHITECTURE RECOVERY AUDIT】

1. Current Phase State
2. Existing Persistence Model
3. Existing Lifecycle Model
4. Evidence State Machine
5. Annotation State Machine
6. Step 41 Definition
7. Step 42 Definition
8. Step 43 Definition
9. Step 44 Definition
10. Step 45 Definition
11. Step 46 Definition
12. Step 47 Definition
13. Step 48 Definition
14. Step 49 Definition
15. Step 50 Definition
16. Step 51 Definition
17. Dependency Graph
18. Data Ownership Matrix
19. Service Boundary Matrix
20. Database Evolution Matrix
21. Security Boundary
22. Non-Goals
23. Architecture Gaps
24. G3 / G4 Status
25. Roadmap Decision
26. Recommended Next Step
```

---

# 二十八、重要限制

本次任务：

```text
Production Code Changes = 0
DB Schema Changes = 0
Tests Added = 0
Tests Modified = 0
Git Commit = 0
Push = 0
PR = 0
Merge = 0
```

这是一次：

**Architecture Recovery Audit**

不是 implementation。

---

# 二十九、最终 STOP 条件

完成报告后：

**立即停止。**

如果：

```text
Roadmap = UNCHANGED
```

下一阶段才允许定义：

```text
Step 41 Execution Task
```

如果：

```text
Roadmap = CHANGE REQUIRED
```

则：

**不要执行 Step 41。**

只报告需要修改的 Roadmap。
