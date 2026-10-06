# Phase 4.1 Step 43：Evidence × Annotation Lifecycle Consistency

项目：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

现在开始执行：

**Phase 4.1 Step 43：Lifecycle Consistency Matrix**

前置状态：

```text
Step 41
Annotation Review Transition
DRAFT → REVIEWED
```

已经完成。

```text
Step 42
Finalization Contract
REVIEWED → FINALIZED
FINALIZED = terminal
```

已经完成。

但目前仍缺少：

```text
Evidence.status
        ×
Annotation.review_status
```

之间的正式业务一致性契约。

本阶段唯一目标：

> **冻结 Evidence Lifecycle 与 Annotation Review Lifecycle 的合法/非法组合矩阵，并确认哪些状态组合需要跨对象联动。**

---

# 二、严格范围

本阶段允许：

* 审计 Evidence 状态机
* 审计 Annotation review 状态机
* 审计 Step 41 / Step 42 现有行为
* 审计已有测试
* 建立 Evidence × Annotation 状态矩阵
* 明确 FROZEN / FORBIDDEN / UNDEFINED
* 必要时新增最小 contract audit tests
* 更新 Lifecycle Contract
* 更新 Roadmap v1.1

本阶段禁止：

```text
Finalization Service
Evidence Service
Annotation Service
API
Conversation
Conversation Evidence
Workflow
Agent
MCP
Memory
Planning
RBAC
Multi-tenancy
Chat UI
```

禁止新增：

```text
DB table
DB column
index
unique constraint
foreign key
migration
```

禁止修改：

```text
AI Router
AI Orchestrator
RAG
Tool
Text-to-SQL
SQL Validator
SQL Executor
```

禁止提前实现：

```text
Conversation ↔ Evidence
```

---

# 三、Step 43 核心问题

必须回答：

> Evidence 的生命周期状态，与 Annotation 的 review 状态之间，到底允许出现哪些组合？

不能使用：

```text
当前代码能做到
```

直接推导：

```text
业务上允许
```

必须区分：

```text
Observed
Contract
```

例如：

```text
当前代码允许：

Evidence = FINALIZED
Annotation = DRAFT
```

不能直接写：

```text
FINALIZED + DRAFT = 合法
```

除非存在明确契约依据。

---

# 四、开始前必须阅读

先阅读：

```text
backend/app/db/models/evidence_record.py
backend/app/db/models/evidence_annotation_record.py
backend/app/db/evidence_repository.py
```

然后：

```text
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
```

检查已有测试：

```text
tests/test_evidence_persistence_db.py
tests/test_evidence_persistence_boundary.py
tests/test_evidence_provenance_persistence_db.py
tests/test_evidence_annotation_association_db.py
tests/test_evidence_annotation_review_db.py
tests/test_evidence_finalization_contract_db.py
```

同时全局搜索：

```text
EVIDENCE_STATUS_TRANSITIONS
EVIDENCE_STATUS_VALUES
ANNOTATION_REVIEW_VALUES
ANNOTATION_REVIEW_TRANSITIONS
update_status
update_annotation_review_status
create_annotation
FINALIZED
REVIEWED
ANNOTATED
DRAFT
```

---

# 五、冻结当前两个独立状态机

首先分别记录。

## Evidence

当前已知：

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

并确认真实：

```text
FINALIZED
    ↓
terminal
```

---

## Annotation

Step 41 已冻结：

```text
DRAFT
   ↓
REVIEWED
```

并且：

```text
REVIEWED
   ↓
terminal
```

禁止：

```text
REVIEWED → DRAFT
REVIEWED → REVIEWED
```

---

# 六、建立第一版状态矩阵

必须明确审计以下组合：

| Evidence.status | Annotation.review_status |
| --------------- | ------------------------ |
| PERSISTED       | DRAFT                    |
| PERSISTED       | REVIEWED                 |
| ANNOTATED       | DRAFT                    |
| ANNOTATED       | REVIEWED                 |
| REVIEWED        | DRAFT                    |
| REVIEWED        | REVIEWED                 |
| FINALIZED       | DRAFT                    |
| FINALIZED       | REVIEWED                 |

如果代码/数据库实际还存在：

```text
IMPORTED + Annotation
```

也要记录。

不要因为当前业务上可能不应该存在，就假设它不存在。

---

# 七、每个组合必须分类

每一个组合必须标记：

```text
FROZEN
FORBIDDEN
UNDEFINED
```

并同时记录：

```text
Observed Behavior
Contract Evidence
Decision
```

推荐表格：

| Evidence  | Annotation | Observed | Contract | Decision | Evidence |
| --------- | ---------- | -------- | -------- | -------- | -------- |
| PERSISTED | DRAFT      | ...      | ...      | ...      | ...      |
| PERSISTED | REVIEWED   | ...      | ...      | ...      | ...      |
| ANNOTATED | DRAFT      | ...      | ...      | ...      | ...      |
| ANNOTATED | REVIEWED   | ...      | ...      | ...      | ...      |
| REVIEWED  | DRAFT      | ...      | ...      | ...      | ...      |
| REVIEWED  | REVIEWED   | ...      | ...      | ...      | ...      |
| FINALIZED | DRAFT      | ...      | ...      | ...      | ...      |
| FINALIZED | REVIEWED   | ...      | ...      | ...      | ...      |

---

# 八、重点分析：Evidence → ANNOTATED

必须特别分析：

```text
Evidence = PERSISTED
Annotation = DRAFT
```

以及：

```text
Evidence = ANNOTATED
Annotation = DRAFT
```

以及：

```text
Evidence = ANNOTATED
Annotation = REVIEWED
```

重点回答：

> Annotation 创建是否应该自动推动 Evidence → ANNOTATED？

Step 37 已经存在：

```text
create_annotation → ANNOTATED
```

不要重新实现。

需要确认的是：

**这是已经冻结的业务契约，还是只是历史实现行为？**

如果已经有明确历史契约：

```text
FROZEN
```

否则：

```text
Observed
```

不要自行升级为 FROZEN。

---

# 九、重点分析：Evidence = REVIEWED

必须分析：

```text
REVIEWED + DRAFT
```

是否允许。

以及：

```text
REVIEWED + REVIEWED
```

是否允许。

必须回答：

> Evidence 进入 REVIEWED 是否要求全部 Annotation 都 REVIEWED？

如果无法从：

```text
代码
测试
Lifecycle Contract
Roadmap
历史决策
```

推出：

必须：

```text
UNDEFINED
```

不能猜。

---

# 十、重点分析：FINALIZED

这是 Step 42 与 Step 43 的交界。

必须分析：

```text
FINALIZED + DRAFT
FINALIZED + REVIEWED
```

但是注意：

Step 42 已经明确：

```text
FINALIZED = terminal
```

Step 42 同时确认：

```text
FINALIZED 后 Annotation 修改目前没有 enforcement
```

因此必须区分：

### Observed

当前代码：

```text
FINALIZED + DRAFT
```

可能仍然可以出现。

### Contract

是否允许：

```text
FINALIZED + DRAFT
```

需要 Step 43 正式决定。

如果没有足够证据：

```text
UNDEFINED
```

不要因为“看起来不合理”直接改成 FORBIDDEN。

---

# 十一、不要把“当前代码没有阻止”当成“允许”

这是本阶段最重要的规则。

例如：

```text
Repository 没有检查：
if evidence.status == FINALIZED:
    reject
```

只能证明：

```text
Observed = allowed by implementation
```

不能证明：

```text
Contract = allowed
```

必须分开记录。

---

# 十二、分析跨对象联动

必须明确：

## Annotation → Evidence

是否存在：

```text
DRAFT → REVIEWED
```

之后自动推动：

```text
Evidence → REVIEWED
```

如果是：

```text
all annotations reviewed
```

才推进 Evidence：

必须记录完整条件。

---

## Evidence → Annotation

是否存在：

```text
Evidence REVIEWED
```

之后：

```text
禁止新增 Annotation
```

或者：

```text
允许新增 Annotation
```

如果不确定：

```text
UNDEFINED
```

---

## FINALIZED → Annotation

是否：

```text
FINALIZED
```

之后：

```text
禁止创建 Annotation
禁止修改 Annotation
```

如果当前没有明确契约：

```text
UNDEFINED
```

---

# 十三、不要创建新 Service

即使发现需要：

```text
EvidenceLifecycleService
AnnotationLifecycleService
FinalizationService
```

也不要创建。

只记录：

```text
Future Implementation
```

例如：

```text
Implementation Deferred:
Lifecycle enforcement should be implemented in a future service/repository
after contract freeze.
```

但不要现在写代码。

---

# 十四、状态矩阵的推荐最终结构

最终文档至少形成：

```text
Evidence × Annotation Lifecycle Matrix
```

建议：

| Evidence  | Annotation | Decision |
| --------- | ---------- | -------- |
| PERSISTED | DRAFT      | ?        |
| PERSISTED | REVIEWED   | ?        |
| ANNOTATED | DRAFT      | ?        |
| ANNOTATED | REVIEWED   | ?        |
| REVIEWED  | DRAFT      | ?        |
| REVIEWED  | REVIEWED   | ?        |
| FINALIZED | DRAFT      | ?        |
| FINALIZED | REVIEWED   | ?        |

其中 Decision 只能是：

```text
FROZEN
FORBIDDEN
UNDEFINED
```

---

# 十五、特别注意 PERSISTED + REVIEWED

这个状态很容易被忽略。

如果：

```text
Annotation.review_status = REVIEWED
```

但：

```text
Evidence.status = PERSISTED
```

是否允许？

不要根据直觉判断。

必须从：

```text
Step 37
Step 41
Step 42
Lifecycle Contract
```

判断。

如果没有明确规则：

```text
UNDEFINED
```

---

# 十六、特别注意 ANNOTATED + REVIEWED

这个状态很可能是一个重要正常状态：

```text
Evidence = ANNOTATED
Annotation = REVIEWED
```

但不能直接假设：

```text
Annotation REVIEWED
→ Evidence automatically REVIEWED
```

除非现有契约明确规定。

因此要分别记录：

```text
State combination
```

和：

```text
Transition trigger
```

---

# 十七、Transition Matrix

除了状态组合，还要记录跨对象 transition。

至少审计：

```text
Annotation:
DRAFT → REVIEWED
```

是否触发：

```text
Evidence:
ANNOTATED → REVIEWED
```

以及：

```text
Evidence:
REVIEWED → FINALIZED
```

是否要求：

```text
Annotation:
all REVIEWED
```

最终形成：

```text
Annotation Transition
        ↓
Evidence Transition?
```

而不是简单把两个状态机合并成一个。

---

# 十八、测试要求

Step 43 是 Contract Freeze。

不要为了覆盖矩阵而大量修改生产代码。

优先使用已有测试。

如果已有测试足够证明：

```text
Observed behavior
```

就不要重复造。

可以新增一个：

```text
tests/test_evidence_lifecycle_consistency_db.py
```

但仅在现有测试不足时新增。

建议最多覆盖：

### Case 1

```text
PERSISTED + DRAFT
```

### Case 2

```text
ANNOTATED + DRAFT
```

### Case 3

```text
ANNOTATED + REVIEWED
```

### Case 4

```text
REVIEWED + DRAFT
```

### Case 5

```text
REVIEWED + REVIEWED
```

### Case 6

```text
FINALIZED + DRAFT
```

### Case 7

```text
FINALIZED + REVIEWED
```

但：

**只有在这些测试能提供有价值的 Contract Evidence 时才新增。**

不要为了“7 个组合”机械增加测试。

---

# 十九、测试必须区分 Observed / Contract

测试名称和 assertion 必须明确。

例如：

```python
test_finalized_annotation_creation_is_currently_observed_behavior()
```

如果只是记录：

```text
当前代码允许
```

不要写：

```python
test_finalized_annotation_creation_is_allowed()
```

因为这会把实现事实误写成业务契约。

---

# 二十、DB Schema

必须保持：

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

---

# 二十一、Documentation

允许更新：

```text
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
```

Lifecycle Contract 新增：

```text
## Evidence × Annotation Lifecycle Consistency
```

至少包含：

```text
1. Independent State Machines
2. State Combination Matrix
3. Cross-object Transition Rules
4. Observed vs Contract
5. Frozen Decisions
6. Forbidden Decisions
7. Undefined Decisions
8. Future Enforcement
```

---

# 二十二、Open Decisions

如果以下任何问题无法从现有证据确定：

必须明确建立 Open Decision。

重点：

```text
OD-3
Finalization preconditions
```

```text
OD-4
Finalized Annotation immutability
```

```text
OD-7
Finalization trigger ownership
```

以及本阶段可能新增：

```text
OD-8
Evidence REVIEWED 是否要求所有 Annotation REVIEWED
```

```text
OD-9
Annotation REVIEWED 是否自动推进 Evidence REVIEWED
```

```text
OD-10
FINALIZED 是否禁止创建/修改 Annotation
```

不要为了让矩阵全部有答案而强行关闭这些 Decision。

---

# 二十三、Roadmap 更新

更新：

```text
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
```

Step 43 状态必须明确：

```text
Step 43 = COMPLETE
```

同时记录：

```text
Lifecycle Matrix = FROZEN / PARTIAL / UNDEFINED
```

如果存在未决架构问题：

不要写：

```text
COMPLETE = all decisions solved
```

应该记录：

```text
Step 43 COMPLETE
Open Decisions remain for future implementation
```

因为本阶段完成的是：

```text
Contract Freeze
```

不是：

```text
Implementation
```

---

# 二十四、不要提前进入 Step 44

禁止处理：

```text
Conversation
ConversationTurn
conversation_id
turn_id
assistant_request_id
Conversation Evidence
Evidence Reference
```

Step 44 才负责：

```text
Conversation ↔ Evidence Integration Contract
```

---

# 二十五、Git

本阶段：

```text
NO COMMIT
NO PUSH
NO PR
NO MERGE
```

不要为了：

```text
working-tree guard
```

而提交。

Step 50 才统一 Git 收口。

---

# 二十六、测试命令

PowerShell：

先运行已有相关测试：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q `
  tests/test_evidence_persistence_db.py `
  tests/test_evidence_annotation_review_db.py `
  tests/test_evidence_finalization_contract_db.py
```

如果新增 Step 43 测试：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_evidence_lifecycle_consistency_db.py
```

然后：

```powershell
python -m pytest -q
```

再：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests scripts
```

Matrix：

```powershell
python scripts/run_matrix_gate.py
```

---

# 二十七、失败处理

如果出现：

```text
Evidence REVIEWED
Annotation DRAFT
```

等状态组合问题：

不要立即修改 Repository。

先判断：

```text
Observed
Contract
Implementation Gap
```

如果是：

```text
Implementation Gap
```

而 Contract 已经明确：

**记录问题。**

只有当前阶段明确允许的最小 Contract implementation 才能修改。

如果修改会涉及：

```text
EvidenceRepository
AnnotationRepository
Service
API
```

必须停止并报告。

---

# 二十八、最终报告格式

完成后严格输出：

```text
【Phase 4.1 Step 43 COMPLETE】

1. Audit Result
2. Evidence State Machine
3. Annotation State Machine
4. Lifecycle Matrix
5. Cross-object Transition Rules
6. Observed vs Contract
7. FROZEN Decisions
8. FORBIDDEN Decisions
9. UNDEFINED / Open Decisions
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
21. Step 44 Readiness
```

其中第 4 项必须包含完整矩阵：

```text
Evidence × Annotation
```

第 5 项必须明确：

```text
Annotation DRAFT → REVIEWED
        ↓
Evidence 是否推进 REVIEWED
```

以及：

```text
Evidence REVIEWED → FINALIZED
        ↓
是否要求 Annotation 全部 REVIEWED
```

---

# 二十九、完成标准

Step 43 满足以下条件即可 COMPLETE：

```text
Evidence State Machine = FROZEN
Annotation State Machine = FROZEN
Lifecycle Matrix = documented
Observed ≠ Contract = clearly separated
Cross-object transition = explicitly documented
Open Decisions = explicitly tracked
DB Schema Changes = 0
Production DB Writes = 0
No Service/API created
No Conversation work
Matrix baseline unchanged
```

即使部分业务规则仍然：

```text
UNDEFINED
```

也可以完成 Step 43。

因为：

> **Step 43 的目标是冻结已知契约并显式暴露未知契约，而不是强行消灭所有 UNKNOWN。**

---

# 三十、最终停止

Step 43 完成后：

**立即停止。**

不要执行：

```text
Step 44
Step 45
Step 46
```

不要设计 Conversation ↔ Evidence。

不要创建 association table。

不要创建 Service。

不要创建 API。

等待下一步指令。
