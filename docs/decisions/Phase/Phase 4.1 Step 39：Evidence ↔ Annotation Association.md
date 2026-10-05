你现在开始实现：

`D:\coding\ai\wms-ai-assistant`

# Phase 4.1 Step 39：Evidence ↔ Annotation Association

## 一、阶段目标

本阶段唯一目标：

**建立并验证 Evidence 与 Annotation 之间真实、可约束、可查询、可隔离的关联关系。**

当前状态：

* Step 37：Evidence / Annotation Persistence 已完成
* Step 38：Provenance Persistence 已完成
* Step 39：只处理 Evidence ↔ Annotation Association
* 不重新实现 Evidence Persistence
* 不重新实现 Annotation Persistence
* 不重新实现 Provenance
* 不提前实现 Review / Finalization / Conversation Integration

目标关系：

```text
Evidence
   │
   │ evidence_id
   ↓
Annotation
   ├── annotation_id
   ├── evidence_id
   ├── case_id
   ├── annotation_version
   ├── annotator_id
   └── review_status
```

核心原则：

**Annotation 必须通过真实 `evidence_id` 与 Evidence 关联。**

禁止通过以下字段作为 Evidence 主关联：

```text
case_id
dataset_version
source_type
annotation_id
文本匹配
字符串推断
```

---

# 二、严格执行顺序

必须严格按照：

```text
1. Contract Audit
        ↓
2. Architecture Audit
        ↓
3. Existing Implementation Audit
        ↓
4. Association Design Decision
        ↓
5. Minimal Implementation
        ↓
6. PostgreSQL E2E
        ↓
7. Cross-Evidence Isolation
        ↓
8. Regression
        ↓
9. Git Audit
        ↓
10. STOP
```

不要跳过 Audit。

尤其不要一开始就修改 ORM。

---

# 三、开始编码前必须阅读

首先阅读真实代码：

```text
backend/app/db/models/evidence_record.py
backend/app/db/models/evidence_annotation_record.py
backend/app/db/evidence_repository.py
backend/app/db/models/__init__.py

tests/test_evidence_persistence_db.py
tests/test_evidence_provenance_persistence_db.py

tests/test_conversation_context_annotation_workflow_architecture.py
tests/test_conversation_context_*annotation*
tests/test_conversation_context_*evidence*

docs/requirements.md
docs/architecture.md
docs/decisions/
```

如果 glob 不支持：

```text
先搜索 annotation
再搜索 evidence_id
再搜索 EvidenceAnnotationRecord
再搜索 association
```

同时搜索：

```text
annotation_id
evidence_id
case_id
annotation_version
annotator_id
review_status
EvidenceAnnotationRecord
EvidenceRecord
ForeignKey
relationship
back_populates
association
```

---

# 四、必须回答的 Audit 问题

在修改代码前先明确：

## 1. 当前 Annotation 是否已经存在 FK？

确认：

```text
Annotation.evidence_id
```

是否已经定义为：

```text
ForeignKey("ai_ops.evidence_record.evidence_id")
```

或者项目当前真实等价写法。

---

## 2. FK 是否真实存在于 PostgreSQL？

不要只看 ORM。

使用 PostgreSQL metadata / information_schema / pg_constraint 检查：

```text
ai_ops.evidence_annotation_record
        ↓
evidence_id
        ↓
ai_ops.evidence_record.evidence_id
```

确认真实数据库约束。

---

## 3. 当前 ORM 是否已经存在 relationship？

检查是否存在：

```python
EvidenceRecord.annotations
```

以及：

```python
EvidenceAnnotationRecord.evidence
```

如果已经存在：

**优先复用。**

不要重复添加。

---

## 4. 当前 Repository 是否已经通过 evidence_id 查询 Annotation？

检查：

```text
list_annotations_by_evidence_id
get_annotation
create_annotation
```

等真实接口。

确认其关联逻辑是否真正使用：

```text
evidence_id
```

而不是：

```text
case_id
```

或者其他业务字段。

---

# 五、Association Contract

Step 39 冻结以下契约：

## Evidence

主键：

```text
evidence_id
```

## Annotation

主键：

```text
annotation_id
```

关联字段：

```text
evidence_id
```

关系：

```text
Evidence 1 ─── N Annotation
```

也就是说：

```text
一个 Evidence
可以拥有多个 Annotation

一个 Annotation
只能属于一个 Evidence
```

因此：

```text
Annotation.evidence_id
```

必须指向真实存在的：

```text
Evidence.evidence_id
```

---

# 六、关联边界

必须验证：

```text
Annotation A
    ↓
Evidence A
```

不能出现：

```text
Annotation A
    ↓
Evidence B
```

也不能出现：

```text
Annotation A
    ↓
不存在的 Evidence
```

因此至少需要测试：

```text
valid association
invalid evidence_id
cross-evidence isolation
```

---

# 七、是否新增 ORM relationship

不要预设必须增加：

```python
EvidenceRecord.annotations
```

也不要预设必须增加：

```python
EvidenceAnnotationRecord.evidence
```

先根据当前代码判断。

决策原则：

### 情况 A

如果当前已经存在真实 FK + Repository association：

```text
不新增 ORM relationship
```

只补充 E2E / association tests。

---

### 情况 B

如果存在 FK，但没有 ORM relationship，而当前架构确实需要对象级关联：

可以最小增加：

```python
EvidenceRecord.annotations
```

以及：

```python
EvidenceAnnotationRecord.evidence
```

如果采用双向关系：

使用：

```text
back_populates
```

保持双方关系一致。

不要引入：

```text
cascade
delete-orphan
```

除非当前已有明确架构要求。

不要借 Step 39 引入生命周期删除行为。

---

### 情况 C

如果数据库没有真实 FK：

这是明确的 Step 39 缺陷。

最小修复：

```text
Annotation.evidence_id
        ↓
ForeignKey
        ↓
Evidence.evidence_id
```

然后通过现有 `create_all()` 机制建立测试数据库约束。

但是：

**不要修改生产数据库。**

---

# 八、禁止改变的内容

本阶段禁止修改：

```text
EvidenceProvenance
dataset_version
source_type

review_status 的业务语义
Finalization
Conversation Evidence
RAG
Tool
Text-to-SQL
Router
Orchestrator

Agent
MCP
Workflow
Memory
Planning
Multi-Agent
RBAC
Multi-tenancy
Chat API
```

也不要新增：

```text
AnnotationService
EvidenceService
AssociationService
ProvenanceService
```

除非 Audit 发现当前架构已经明确要求，并且没有更小实现方式。

默认：

**Repository 层完成 Association 即可。**

---

# 九、Repository 行为验证

检查当前 Repository。

至少保证：

```text
create_annotation(
    evidence_id=...
)
```

不会允许不存在的 Evidence 被成功持久化。

同时：

```text
list_annotations_by_evidence_id(evidence_id)
```

只返回该 Evidence 的 Annotation。

不能出现：

```text
查询 Evidence A
返回 Evidence B 的 Annotation
```

---

# 十、PostgreSQL E2E 测试

新增测试文件：

```text
tests/test_evidence_annotation_association_db.py
```

如果当前项目已经有合适的 DB 测试文件：

优先扩展已有测试。

不要重复创建测试基础设施。

---

# 十一、测试 Case 1：正常 Association

创建：

```text
Evidence A
```

例如：

```text
evidence_id = evidence-a
dataset_version = test-association-v1
source_type = knowledge_base
```

然后创建：

```text
Annotation A1
evidence_id = evidence-a
```

验证：

```text
Annotation A1.evidence_id == Evidence A.evidence_id
```

并通过 Repository 查询：

```text
list_annotations_by_evidence_id(evidence-a)
```

能够得到：

```text
A1
```

---

# 十二、测试 Case 2：一个 Evidence 多个 Annotation

创建：

```text
Evidence A
```

然后：

```text
Annotation A1
Annotation A2
Annotation A3
```

全部：

```text
evidence_id = evidence-a
```

验证：

```text
Evidence A
    ├── A1
    ├── A2
    └── A3
```

查询：

```text
list_annotations_by_evidence_id(evidence-a)
```

必须返回三个 Annotation。

不要改变 Annotation 的：

```text
annotation_id
annotation_version
review_status
```

语义。

---

# 十三、测试 Case 3：Cross-Evidence Isolation

创建：

```text
Evidence A
Evidence B
```

创建：

```text
Annotation A1 → Evidence A
Annotation A2 → Evidence A

Annotation B1 → Evidence B
Annotation B2 → Evidence B
```

查询：

```text
Evidence A
```

只能得到：

```text
A1
A2
```

不能得到：

```text
B1
B2
```

查询：

```text
Evidence B
```

只能得到：

```text
B1
B2
```

这是本阶段最重要的业务边界测试之一。

---

# 十四、测试 Case 4：Invalid Evidence

创建：

```text
Annotation X
evidence_id = "does-not-exist"
```

验证：

```text
reject
```

如果当前数据库 FK 直接触发：

```text
IntegrityError
```

可以接受。

但必须确认：

```text
Annotation X
```

没有残留数据库记录。

最终：

```text
count_before == count_after
```

---

# 十五、测试 Case 5：case_id 不能替代 evidence_id

如果当前 Annotation 存在：

```text
case_id
```

必须验证：

两个 Annotation 可以拥有相同：

```text
case_id
```

但分别属于：

```text
Evidence A
Evidence B
```

查询仍然必须依据：

```text
evidence_id
```

而不是：

```text
case_id
```

也就是说：

```text
case_id
```

不是 Evidence Association Key。

---

# 十六、测试 Case 6：Association Read Model

如果当前 Repository 已经提供：

```text
EvidenceWithProvenance
```

或者其他 Evidence Read Model：

不要把 SQLAlchemy ORM 对象泄漏出去。

如果新增 Association Read Model：

必须保持：

```text
frozen / immutable
```

并且不能包含：

```text
Session
Connection
Engine
ORM object
SQLAlchemy Result
```

但是：

**如果当前架构不需要新的 Read Model，不要为了 Step 39 强行新增。**

---

# 十七、Transaction 要求

Step 37 已经定义 Repository transaction ownership。

继续复用：

```python
with factory() as session, session.begin():
```

不要新增第二套 transaction pattern。

Association 写入必须遵守现有 Repository transaction boundary。

不要在测试中人为模拟：

```text
commit A
commit B
```

这种拆分事务。

---

# 十八、Production DB 写入零容忍

测试前记录：

```text
count_before
```

测试后记录：

```text
count_after
```

只允许测试自己创建的数据存在于测试数据库。

不得：

```text
INSERT production WMS data
UPDATE production data
DELETE production data
```

最终报告：

```text
Production DB writes = 0
```

---

# 十九、Schema 变更规则

优先确认当前 Step 37 已经存在：

```text
evidence_id FK
```

如果已经存在：

```text
DB Schema changes = 0
```

不要为了增加 ORM relationship 而增加数据库结构。

如果不存在真实 FK：

才允许最小增加 FK。

但必须：

```text
先报告发现
再最小修改
再 DB test
```

禁止顺手增加：

```text
index
cascade
unique constraint
new table
new schema
```

除非现有架构明确要求。

---

# 二十、历史 Guard 审计

重点检查：

```text
tests/test_conversation_context_*annotation*
tests/test_conversation_context_*evidence*
```

Step 37 / Step 38 已经出现过历史 Guard 与新实现冲突。

因此本阶段：

**不要删除 Guard。**

如果 Guard 已经落后于当前 roadmap：

只做：

```text
scope-aware guard migration
```

原则：

```text
允许 Step 39 当前合法实现存在
仍然禁止未来未授权能力存在
```

例如继续禁止：

```text
Annotation Service
Annotation Workflow
Conversation Integration
Review Workflow
Finalization Workflow
```

不要把整个 guard 放宽。

---

# 二十一、不要修改 Matrix Baseline

如果历史测试出现：

```text
Matrix
Baseline
Offline Suite
```

不要为了 Step 39 修改 baseline。

如果出现失败：

先分类：

```text
A Implementation Bug
B Test Bug
C Historical Guard
D Matrix Cascade
```

然后处理真正根因。

---

# 二十二、测试命令

首先：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_evidence_annotation_association_db.py
```

然后：

```powershell
python -m pytest -q tests/test_evidence_persistence_db.py tests/test_evidence_provenance_persistence_db.py tests/test_evidence_annotation_association_db.py
```

然后完整回归：

```powershell
python -m pytest -q
```

如果项目现有 DB Integration 需要：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

另外：

```powershell
python -m compileall backend
```

检查：

```text
LSP
lint
```

要求：

```text
0 failed
0 errors
0 unexpected diagnostics
```

---

# 二十三、Git 工作区检查

执行前：

```bash
git status --short
git branch --show-current
git log -1 --oneline
```

执行后再次检查。

确认：

```text
Step 39 changes only
```

不要：

```text
push
PR
merge
```

除非用户另外要求。

---

# 二十四、Commit

如果当前项目阶段仍然要求每 Step 一个 commit：

使用：

```text
feat: Phase 4.1 Step 39 Evidence Annotation Association
```

commit 前确认：

```text
tests pass
compileall pass
git diff reviewed
no secrets
no production DB writes
```

如果当前工作流没有要求立即 commit：

保留工作区状态并报告。

不要擅自 push。

---

# 二十五、最终报告格式

完成后严格按照：

```text
【Phase 4.1 Step 39 COMPLETE】

1. Contract Audit
2. Association Design
3. ORM Changes
4. Repository Changes
5. PostgreSQL FK
6. Association E2E
7. Cross-Evidence Isolation
8. Invalid Evidence Test
9. case_id Boundary
10. Security
11. DB Writes
12. Regression
13. Matrix
14. Git
15. Problems Found
16. Problems Fixed
17. Limitations
18. Next Step
```

其中必须明确：

```text
Evidence → Annotation = PASS
Annotation → Evidence FK = PASS
Cross-Evidence Isolation = PASS
Invalid evidence_id = REJECTED
Production DB Writes = 0
```

---

# 二十六、最终架构状态

完成后应形成：

```text
Evidence
   │
   │ evidence_id
   │
   ▼
Annotation
   │
   ├── annotation_id
   ├── evidence_id
   ├── case_id
   ├── annotation_version
   ├── annotator_id
   └── review_status
```

Provenance 仍然：

```text
Evidence
   ├── dataset_version
   └── source_type
```

不要把：

```text
Provenance
```

错误地建成：

```text
Annotation → Provenance
```

或者：

```text
Annotation → dataset_version
```

---

# 二十七、Step 39 完成边界

本阶段完成意味着：

```text
Evidence Persistence       PASS
Annotation Persistence     PASS
Provenance Persistence     PASS
Evidence ↔ Annotation      PASS
Cross-Evidence Isolation   PASS
Invalid FK                 PASS
Production DB Writes       0
```

但以下仍然必须保持：

```text
Review              NOT IMPLEMENTED
Finalization        NOT IMPLEMENTED
Conversation        NOT IMPLEMENTED
Evidence Workflow   NOT IMPLEMENTED
Agent               NOT IMPLEMENTED
MCP                 NOT IMPLEMENTED
Memory              NOT IMPLEMENTED
```

---

# 二十八、STOP

完成 Step 39 后：

**立即停止。**

不要进入：

```text
Step 40
```

除非用户明确下达下一步指令。

最终只报告 Step 39。

不要开发：

```text
Review
Finalization
Conversation Evidence
Agent
MCP
Workflow
Chat API
```
