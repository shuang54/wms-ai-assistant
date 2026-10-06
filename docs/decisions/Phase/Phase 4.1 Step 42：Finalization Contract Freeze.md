# Phase 4.1 Step 42：Finalization Contract Freeze

项目：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

现在开始执行：

**Phase 4.1 Step 42：Finalization Contract Freeze**

Step 41 已完成：

```text
Annotation.review_status

DRAFT
  ↓
REVIEWED
```

并已经冻结：

```python
ANNOTATION_REVIEW_TRANSITIONS = {
    DRAFT: (REVIEWED,),
    REVIEWED: (),
}
```

Step 42 的目标不是实现 Finalization。

唯一目标：

> **审计当前 Evidence Finalization 真实实现，并冻结 Evidence `FINALIZED` 的完整业务契约。**

本阶段必须回答：

```text
Evidence 什么时候可以 FINALIZED？
FINALIZED 后什么可以改？
FINALIZED 后什么禁止改？
谁负责触发？
Annotation 是否必须全部 REVIEWED？
Provenance 是否可以修改？
```

最终形成：

```text
FINALIZATION CONTRACT = FROZEN
```

---

# 二、严格执行原则

本阶段：

## 允许

* 阅读现有 Evidence / Annotation / Repository / Lifecycle Contract
* 搜索现有 FINALIZED 实现
* 验证当前代码真实行为
* 更新 Lifecycle Contract
* 更新 Roadmap v1.1 的 Step 42 状态
* 新增极少量 contract audit test（仅用于证明现有行为，不实现新能力）

## 禁止

不要实现：

```text
Finalization Service
Finalization API
Conversation
Conversation Evidence
Evidence Service
Annotation Service
Workflow
Agent
MCP
Memory
Planning
RBAC
Multi-tenancy
Chat API
```

不要新增：

```text
DB table
DB column
index
unique constraint
foreign key
migration
```

不要修改：

```text
EvidenceRepository
AnnotationRepository
Evidence ORM
Annotation ORM
AI Runtime
Router
Orchestrator
RAG
Tool
Text-to-SQL
SQL Validator
SQL Executor
```

除非审计过程中发现：

**当前代码事实与已经冻结的历史契约矛盾。**

如果发现矛盾：

**先停止实现，只报告问题。**

不要为了完成 Step 42 修改核心代码。

---

# 三、开始前必须阅读真实代码

不要假设实现。

首先阅读：

```text
backend/app/db/models/evidence_record.py
backend/app/db/models/evidence_annotation_record.py
backend/app/db/evidence_repository.py
backend/app/db/models/__init__.py
```

然后阅读：

```text
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
```

同时搜索整个项目：

```text
FINALIZED
finalized
update_status
EVIDENCE_STATUS_TRANSITIONS
ANNOTATION_REVIEW_TRANSITIONS
review_status
annotation_version
de_identification_attested
de_identification_method
dataset_version
source_type
```

确认：

1. Evidence FINALIZED 当前是否已经存在真实 transition。
2. FINALIZED 是否已经是 terminal。
3. FINALIZED 是否已经有 Repository enforcement。
4. 是否存在任何代码已经阻止 FINALIZED 后修改。
5. 是否存在 Annotation FINALIZED 后修改逻辑。
6. 是否存在 Provenance FINALIZED 后修改逻辑。
7. 是否存在任何 Service/API 触发 Finalization。
8. 是否存在隐藏的 finalization precondition。
9. 是否存在历史测试已经约束 Finalization。

---

# 四、Step 42 已知事实

以下事实来自前置 Step，不能重新解释为新的发现。

Evidence 生命周期已经存在：

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

其中：

```text
FINALIZED
```

已经是 terminal。

现有 Evidence 状态值域和 transition 机制已经存在。

Step 42：

**不重新实现 Evidence state machine。**

---

# 五、Step 42 必须冻结的五个问题

必须逐项回答。

---

## Q1：Finalization Preconditions

定义：

> Evidence 从 `REVIEWED` 进入 `FINALIZED` 前，必须满足什么条件？

重点检查：

```text
Evidence.status == REVIEWED
```

是否足够？

还是必须：

```text
Evidence.status == REVIEWED
AND
所有 Annotation.review_status == REVIEWED
```

或者存在其他条件。

不要凭经验决定。

必须根据：

```text
现有代码
已有测试
Phase 4.1 历史设计
Roadmap
Lifecycle Contract
```

进行判断。

如果现有证据不足：

```text
UNDEFINED
```

并明确记录：

```text
decision required
```

**禁止自行猜测。**

---

# 六、Q2：Annotation Review Requirement

明确：

```text
FINALIZED
```

是否要求：

```text
所有 Annotation = REVIEWED
```

必须区分：

### 情况 A

```text
Evidence REVIEWED
+
所有 Annotation REVIEWED
→ FINALIZED
```

### 情况 B

```text
Evidence REVIEWED
→ FINALIZED

Annotation review status
不影响 Finalization
```

### 情况 C

当前无法确定：

```text
UNDEFINED
```

如果无法从现有架构得到答案：

**不要选择 A 或 B。**

---

# 七、Q3：Finalized Immutability

这是本阶段重点。

审计 FINALIZED 后以下对象是否允许修改：

## Evidence

```text
dataset_version
source_type
de_identification_attested
de_identification_method
status
```

## Provenance

当前 Provenance 定义：

```text
dataset_version
+
source_type
```

没有独立 Provenance 表。

确认：

```text
FINALIZED 后 Provenance 是否 immutable？
```

## Annotation

检查：

```text
annotation_version
annotator_id
case_id
review_status
```

确认：

```text
FINALIZED 后 Annotation 是否允许：
DRAFT → REVIEWED
```

以及是否允许：

```text
创建新 Annotation
修改已有 Annotation
```

如果当前没有实现：

不要新增实现。

只冻结契约。

---

# 八、Q4：Finalization Trigger Ownership

必须明确：

> 谁负责把 Evidence 从 `REVIEWED` 推进到 `FINALIZED`？

候选：

```text
EvidenceRepository
EvidenceService
FinalizationService
Conversation
API
Workflow
人工操作
```

但不要因为未来可能需要 Service 就提前创建 Service。

如果当前架构：

```text
Repository
```

只是 persistence boundary，

那么记录：

```text
Finalization trigger owner = UNDEFINED
```

也是合法结果。

如果已经存在明确实现，则记录真实 owner。

---

# 九、Q5：FINALIZED Terminal

这个已经是：

```text
FROZEN = YES
```

必须保持：

```text
FINALIZED → FINALIZED
```

不是一个新的合法业务 transition。

并且：

```text
FINALIZED → IMPORTED
FINALIZED → PERSISTED
FINALIZED → ANNOTATED
FINALIZED → REVIEWED
```

全部禁止。

不要新增状态。

不要新增：

```text
ARCHIVED
CANCELLED
REOPENED
APPROVED
REJECTED
```

---

# 十、形成 Finalization Contract

最终在：

```text
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
```

中更新 Step 42 对应章节。

建议结构：

```markdown
## Finalization Contract

### Status

FROZEN

### Preconditions

...

### Transition

REVIEWED → FINALIZED

### Postconditions

...

### Immutability

...

### Annotation Requirement

...

### Provenance Requirement

...

### Trigger Ownership

...

### Forbidden Transitions

...

### Open Decisions

...
```

---

# 十一、非常重要：不要替 Step 43 做决定

Step 42 与 Step 43 必须保持边界。

Step 42：

```text
定义 Finalization Contract
```

Step 43：

```text
定义 Evidence.status
×
Annotation.review_status
合法组合矩阵
```

因此 Step 42 不要提前冻结：

```text
PERSISTED + DRAFT
ANNOTATED + DRAFT
ANNOTATED + REVIEWED
REVIEWED + DRAFT
REVIEWED + REVIEWED
FINALIZED + DRAFT
FINALIZED + REVIEWED
```

这些组合的完整合法性属于：

**Step 43。**

Step 42 只回答：

```text
什么条件允许进入 FINALIZED
```

而不是一次性定义整个状态矩阵。

---

# 十二、不要替 Step 44 做决定

禁止在 Step 42 中设计：

```text
Conversation ↔ Evidence
```

禁止：

```text
conversation_id
turn_id
assistant_request_id
```

进入 Evidence identity / provenance。

Step 44 才负责：

```text
Conversation ↔ Evidence Integration Contract
```

---

# 十三、不要修改数据库

本阶段：

```text
DB Schema Changes = 0
```

禁止：

```sql
ALTER TABLE
CREATE TABLE
ADD COLUMN
CREATE INDEX
CREATE UNIQUE
CREATE FOREIGN KEY
```

如果为了表达 Finalization Contract 而产生数据库设计需求：

**记录为 future decision，不实现。**

---

# 十四、测试要求

Step 42 是 Contract Freeze，因此测试重点不是新增 Finalization 功能。

如果当前已经存在 FINALIZED transition enforcement：

可以增加或检查最小 contract audit tests：

```text
REVIEWED → FINALIZED
FINALIZED → REVIEWED rejected
FINALIZED → ANNOTATED rejected
FINALIZED → PERSISTED rejected
FINALIZED → IMPORTED rejected
```

如果这些测试已经存在：

**直接复用，不重复造测试。**

如果当前 Repository 已经实现：

```text
REVIEWED → FINALIZED
```

不要修改实现。

如果当前没有 Finalization API / Service：

不要新增。

---

# 十五、Contract Evidence

最终报告必须把每个决策分类：

```text
FROZEN
DECIDED
UNDEFINED
```

禁止：

```text
推测
默认
未来应该
通常来说
```

尤其是：

```text
所有 Annotation 是否必须 REVIEWED
FINALIZED 后 Annotation 是否 immutable
Finalization trigger owner
```

如果代码无法证明：

```text
UNDEFINED
```

---

# 十六、Documentation 更新范围

允许修改：

```text
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
```

不允许修改：

```text
docs/architecture.md
docs/requirements.md
```

除非发现真实架构矛盾。

即使发现矛盾：

**先停止并报告。**

不要顺手修改架构文档。

---

# 十七、Step 42 不应解决的问题

以下全部留给后续：

```text
Evidence → REVIEWED 的前置条件
完整 Evidence × Annotation 状态矩阵
Conversation ↔ Evidence
Conversation Evidence persistence
Evidence Service
Finalization Service
Finalization API
RBAC
Multi-tenancy
Agent
MCP
Workflow
Memory
Planning
```

特别是：

```text
Evidence REVIEWED
```

到底要求多少 Annotation REVIEWED：

如果需要完整状态矩阵分析：

**Step 43 决定。**

---

# 十八、Git 约束

本阶段：

```text
NO COMMIT
NO PUSH
NO PR
NO MERGE
```

不要为了让历史 working-tree guard 变绿而提交。

Step 41 已经证明：

```text
working-tree guard
        ↓
offline regression
        ↓
matrix
```

存在历史级联。

因此：

如果再次出现：

```text
backend working tree is unmodified
```

而原因只是本阶段存在合法未提交修改：

记录：

```text
Historical Guard / NO COMMIT
```

不要修改 baseline。

真正的 Git 收口属于：

```text
Step 50
```

---

# 十九、测试命令

PowerShell 环境。

先运行：

```powershell
python -m pytest -q tests/test_evidence_annotation_review_db.py
```

然后如果 Step 42 新增了测试：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_<step42>.py
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests scripts
```

---

# 二十、最终报告格式

完成后严格使用：

```text
【Phase 4.1 Step 42 COMPLETE】

1. Audit Result
2. Existing Finalization Implementation
3. Finalization Preconditions
4. Annotation Review Requirement
5. Finalized Immutability
6. Provenance Immutability
7. Finalization Trigger Ownership
8. Terminal State
9. Contract Decision Matrix
10. Modified Files
11. DB Schema
12. Tests
13. Full Regression
14. Matrix
15. DB Writes
16. Security
17. Git Status
18. Problems Found
19. Problems Fixed
20. Current Limitations
21. Step 43 Readiness
```

其中：

```text
Contract Decision Matrix
```

至少：

| Decision                          | Status             | Evidence         |
| --------------------------------- | ------------------ | ---------------- |
| REVIEWED → FINALIZED              | FROZEN / UNDEFINED | 代码/文档/测试         |
| FINALIZED terminal                | FROZEN             | 现有 state machine |
| All annotations REVIEWED required | FROZEN / UNDEFINED | evidence         |
| Finalized Annotation immutable    | FROZEN / UNDEFINED | evidence         |
| Provenance immutable              | FROZEN / UNDEFINED | evidence         |
| Trigger owner                     | FROZEN / UNDEFINED | evidence         |

---

# 二十一、最终停止条件

如果：

```text
Finalization Contract = FROZEN
```

则：

**Step 42 COMPLETE。**

然后：

**立即停止。**

不要执行：

```text
Step 43
Step 44
Step 45
```

不要修改 AI Runtime。

不要创建 Service。

不要创建 API。

不要创建数据库结构。

等待下一步指令。
