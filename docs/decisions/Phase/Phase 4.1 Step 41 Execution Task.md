你现在开始执行：

`D:\coding\ai\wms-ai-assistant`

当前阶段：

```text
Phase 4.1
Step 41 — Annotation Review Transition
```

---

# 一、阶段目标

Phase 4.1 Roadmap v1.1 已经冻结。

当前已经完成：

```text
Step 37 Evidence / Annotation Persistence
Step 38 Provenance Persistence
Step 39 Evidence ↔ Annotation Association
Step 40 Transaction / Idempotency Hardening
```

本阶段只实现：

```text
Annotation Review Transition
```

核心状态迁移：

```text
DRAFT
  ↓
REVIEWED
```

目标：

> 让 Annotation 具备明确、可验证、Repository-owned 的 Review Status 迁移能力。

---

# 二、绝对范围

本阶段只允许涉及：

```text
Annotation.review_status
AnnotationRepository / EvidenceRepository 中现有 Annotation ownership
Annotation review transition
Annotation review transition tests
必要的 Phase 4.1 documentation
```

优先复用现有：

```text
EvidenceRepository
EvidenceAnnotationRecord
AnnotationRow
ANNOTATION_REVIEW_VALUES
```

不要新建 AnnotationService。

---

# 三、绝对禁止

本阶段禁止修改：

```text
Evidence ORM
Conversation ORM
Provenance
Evidence 状态机
Evidence status transitions
Finalization
Conversation ↔ Evidence
AI Runtime
AI Orchestrator
Router
RAG
Tool
Text-to-SQL
API
```

禁止：

```text
新增 Service
新增 API
新增数据库表
新增数据库字段
新增 FK
新增 Index
新增 UNIQUE
修改现有 Schema
修改 Evidence status
修改 Conversation
```

禁止提前实现：

```text
Step 42
Step 43
Step 44
Step 45
Step 46
```

尤其禁止自行决定：

```text
Evidence REVIEWED 是否要求所有 Annotation = REVIEWED
```

该问题属于：

```text
Step 43
```

当前仍然：

```text
UNDEFINED
```

---

# 四、开始编码前：必须先审计真实代码

不要假设当前实现。

先阅读：

```text
backend/app/db/models/evidence_annotation_record.py
backend/app/db/models/evidence_record.py
backend/app/db/evidence_repository.py
backend/app/db/models/__init__.py
```

然后搜索：

```text
ANNOTATION_REVIEW_VALUES
review_status
create_annotation
list_annotations
get_annotation
update_annotation
EvidenceAnnotationRecord
AnnotationRow
```

同时阅读现有测试：

```text
tests/test_evidence_persistence_db.py
tests/test_evidence_provenance_persistence_db.py
tests/test_evidence_annotation_association_db.py
```

以及 Step 40 测试：

```text
tests/
```

定位：

```text
EvidenceRepository
```

目前 Annotation 的真实 ownership。

---

# 五、Audit Gate

在修改代码之前，必须确认并记录：

## 1. Annotation 当前 review_status

确认当前字段：

```text
review_status
```

允许值：

```text
DRAFT
REVIEWED
```

以当前代码真实常量为准。

---

## 2. 当前是否已有 update method

搜索：

```text
update_annotation
update_review_status
review_annotation
```

如果已经存在：

**不要重复实现。**

先验证现有行为是否满足本 Step。

---

## 3. 当前是否已有 Review Transition Contract

确认是否存在：

```text
ANNOTATION_REVIEW_TRANSITIONS
```

或者等价结构。

如果不存在：

需要设计最小 Transition Contract。

---

# 六、Step 41 Contract

本阶段冻结：

```text
Annotation Review Transition
```

最小状态机：

```text
DRAFT → REVIEWED
```

终态：

```text
REVIEWED
```

默认不得允许：

```text
REVIEWED → DRAFT
```

也不得允许：

```text
REVIEWED → REVIEWED
```

如果现有业务代码或 Decision 文档存在不同定义：

**停止并报告冲突，不要自行覆盖。**

---

# 七、重要：不要扩展 Annotation 生命周期

本阶段只处理：

```text
review_status
```

不要新增：

```text
APPROVED
REJECTED
FINALIZED
ARCHIVED
CANCELLED
```

不要增加：

```text
reviewed_at
reviewer_id
review_comment
review_reason
```

除非当前真实架构已经存在这些字段。

本阶段不扩展数据模型。

---

# 八、Repository API

如果当前 Repository 没有 Annotation update method：

新增最小方法。

建议语义：

```python
update_annotation_review_status(
    annotation_id,
    review_status,
) -> AnnotationRow
```

具体命名以当前 Repository 风格为准。

要求：

```text
Repository owns transaction
```

保持 Step 37/40 的既有模式：

```python
with factory() as session, session.begin():
    ...
```

不要引入：

```text
Service transaction
Unit of Work
新的 transaction abstraction
```

---

# 九、Transition Validation

Repository 必须拒绝非法迁移。

至少：

```text
DRAFT → REVIEWED
    PASS
```

```text
REVIEWED → DRAFT
    REJECT
```

```text
REVIEWED → REVIEWED
    REJECT
```

如果传入未知状态：

```text
UNKNOWN → REVIEWED
```

必须：

```text
REJECT
```

如果当前系统已经有专门异常类型：

优先复用。

否则创建最小、语义明确的异常。

不要重构现有异常体系。

---

# 十、失败语义

失败时：

```text
数据库状态不得改变
```

例如：

```text
REVIEWED → DRAFT
```

执行失败后：

```text
review_status == REVIEWED
```

必须保持不变。

不要：

```text
先 UPDATE
再验证
```

必须先验证 Transition。

---

# 十一、Annotation 不存在

如果：

```text
annotation_id
```

不存在：

必须按照当前 Repository 的错误语义处理。

优先复用：

```text
AnnotationNotFoundError
```

或当前项目已有等价异常。

不要静默返回：

```text
None
```

也不要创建新 Annotation。

---

# 十二、Evidence 边界

更新 Annotation Review Status 时：

必须保持：

```text
evidence_id
case_id
annotation_version
annotator_id
created_at
updated_at
```

不被错误修改。

特别验证：

```text
evidence_id 不改变
```

不能因为 Review 操作改变 Annotation 所属 Evidence。

---

# 十三、Evidence Status 不得被联动修改

这是本阶段非常重要的边界。

执行：

```text
Annotation DRAFT
    ↓
Annotation REVIEWED
```

不得自动执行：

```text
Evidence PERSISTED
    ↓
Evidence ANNOTATED
```

也不得：

```text
Evidence ANNOTATED
    ↓
Evidence REVIEWED
```

更不得：

```text
Evidence → FINALIZED
```

本阶段：

```text
Annotation review update
=
Annotation-only mutation
```

---

# 十四、事务边界

保持：

```text
Repository owns transaction
```

一次 Review Update：

```text
BEGIN
 ↓
load Annotation
 ↓
validate transition
 ↓
update review_status
 ↓
commit
```

非法迁移：

```text
BEGIN
 ↓
load
 ↓
reject
 ↓
ROLLBACK / no mutation
```

不要新增跨 Repository transaction。

不要修改 Evidence transaction architecture。

---

# 十五、测试文件

新增：

```text
tests/test_evidence_annotation_review_db.py
```

如果项目已有更合适的测试文件：

遵循当前结构。

测试必须使用真实 PostgreSQL。

因为本阶段验证的是：

```text
Repository
+
ORM
+
Transaction
+
Real DB
```

但：

```text
DB Schema = 0
```

不允许新增 migration。

---

# 十六、DB Test Cases

至少覆盖：

## Case 1 — DRAFT → REVIEWED

创建：

```text
Evidence
Annotation(review_status=DRAFT)
```

执行 Review。

验证：

```text
review_status == REVIEWED
```

PASS。

---

## Case 2 — REVIEWED → DRAFT

先：

```text
DRAFT → REVIEWED
```

然后尝试：

```text
REVIEWED → DRAFT
```

必须：

```text
REJECT
```

并确认数据库仍然：

```text
REVIEWED
```

---

## Case 3 — REVIEWED → REVIEWED

再次提交：

```text
REVIEWED
```

必须：

```text
REJECT
```

数据库状态保持：

```text
REVIEWED
```

---

## Case 4 — Unknown Status

例如：

```text
APPROVED
```

如果不属于当前合法状态：

必须：

```text
REJECT
```

数据库不得变化。

---

## Case 5 — Annotation Not Found

使用不存在的：

```text
annotation_id
```

验证：

```text
NotFound
```

并且：

```text
DB = unchanged
```

---

## Case 6 — Evidence Boundary

Review Annotation 后验证：

```text
annotation.evidence_id
```

保持不变。

---

## Case 7 — Evidence Status Isolation

创建：

```text
Evidence.status = ANNOTATED
Annotation.review_status = DRAFT
```

执行：

```text
Annotation DRAFT → REVIEWED
```

确认：

```text
Evidence.status == ANNOTATED
```

不得自动变化。

---

## Case 8 — Annotation Field Isolation

Review 后确认：

```text
case_id
annotation_version
annotator_id
evidence_id
created_at
```

保持原值。

只有允许变化的字段：

```text
review_status
updated_at
```

如果当前时间字段更新机制已有明确行为，按现有 Contract 验证。

---

## Case 9 — Cross-Evidence Isolation

创建：

```text
Evidence A
Annotation A

Evidence B
Annotation B
```

Review A。

确认：

```text
Annotation A = REVIEWED
Annotation B = DRAFT
```

---

## Case 10 — Persistence / Read Back

更新完成后：

使用新的 Repository read：

```text
get_annotation()
```

重新读取。

确认：

```text
REVIEWED
```

确实持久化。

不要只检查内存对象。

---

# 十七、Unit Test

如果 Transition Logic 被抽成纯函数：

可以增加纯单元测试：

```text
DRAFT → REVIEWED = PASS
REVIEWED → DRAFT = FAIL
REVIEWED → REVIEWED = FAIL
UNKNOWN → REVIEWED = FAIL
```

但不要为了测试而重复实现两套状态机。

优先测试真实 Repository 行为。

---

# 十八、不要修改现有 Evidence Tests

历史测试：

```text
Step 37
Step 38
Step 39
Step 40
```

保持原样。

除非：

```text
Step 41 新增合法行为
```

导致已有测试中的：

```text
历史错误假设
```

需要最小迁移。

如果发现这种情况：

先报告。

不要批量修改历史测试。

---

# 十九、DB 写入边界

测试可以写入：

```text
self-created Evidence
self-created Annotation
```

但必须：

```text
cleanup
```

或者使用项目已有事务 rollback / fixture 机制。

pytest fixture 应优先复用现有项目基础设施；fixture 本身适合提供一致的测试环境并负责 teardown。

禁止：

```text
TRUNCATE
DELETE FROM entire table
DROP TABLE
DROP SCHEMA
```

禁止修改真实 WMS 业务表。

最终：

```text
Production DB writes = 0
```

---

# 二十、Schema Gate

本阶段必须明确：

```text
DB Schema Changes = 0
```

不要：

```text
CREATE TABLE
ALTER TABLE
ADD COLUMN
DROP COLUMN
ADD INDEX
ADD UNIQUE
ADD FK
```

Step 41 是：

```text
behavioral persistence change
```

不是：

```text
schema evolution
```

---

# 二十一、Security Boundary

Review API / Repository 不得泄露：

```text
API key
password
DATABASE_URL
connection string
authorization header
Session
Connection
Engine
ORM object
```

返回：

```text
AnnotationRow
```

或当前项目已有安全 Read Model。

如果当前：

```text
AnnotationRow
```

已经满足：

不要修改。

---

# 二十二、Read Model Boundary

确认：

```text
AnnotationRow
```

仍然是：

```text
frozen
```

并且不包含：

```text
Session
Connection
Engine
ORM
metadata
registry
```

不要因为增加 update method 而把 ORM 对象暴露给调用者。

---

# 二十三、Updated_at

如果当前 Repository 已经有统一更新时间策略：

必须复用。

如果没有：

**不要为了 Step 41 新建完整 timestamp framework。**

只有在现有模型 Contract 已经明确要求：

```text
updated_at
```

时才更新。

否则报告当前行为，不自行扩展。

---

# 二十四、异常 Contract

如果当前已有：

```text
InvalidEvidenceStatusTransitionError
```

不要拿它直接表示 Annotation Review 错误，除非语义明确一致。

优先：

```text
InvalidAnnotationReviewTransitionError
```

但只有在项目异常设计确实需要新异常时才增加。

异常应该表达：

```text
Annotation review transition invalid
```

不要把：

```text
API key
SQL
DB connection
exception message
```

放进异常对外输出。

---

# 二十五、Documentation

Step 41 完成后，只允许做最小文档同步。

优先修改：

```text
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
```

记录已经实际实现的：

```text
Annotation Review Transition

DRAFT → REVIEWED
```

以及：

```text
REVIEWED → DRAFT = FORBIDDEN
REVIEWED → REVIEWED = FORBIDDEN
```

如果现有 Lifecycle Contract 中这些仍然是：

```text
UNDEFINED
```

只把：

```text
Annotation Transition
```

这一部分更新为已冻结事实。

**不要提前冻结：**

```text
Evidence REVIEWED prerequisite
Finalization prerequisite
Allowed Evidence/Annotation matrix
Conversation ↔ Evidence
```

Roadmap v1.1 中：

```text
OD-1
```

对应的 Annotation Review Transition 可以在 Step 41 完成后关闭。

---

# 二十六、不要修改 Architecture 大章节

除非实际实现产生：

```text
architecture-level change
```

否则不要大规模修改：

```text
docs/architecture.md
```

如果需要同步：

只增加最小、可追溯的 Step 41 记录。

不要重写 Phase 3 Architecture。

---

# 二十七、测试命令

首先只运行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_evidence_annotation_review_db.py
```

确认 Step 41 专项测试通过后，再运行：

```powershell
python -m pytest -q
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

最后：

```powershell
python -m compileall backend tests scripts
```

如果项目已有 lint：

继续执行现有 lint。

---

# 二十八、Regression Gate

必须确认：

```text
Step 37 DB tests PASS
Step 38 DB tests PASS
Step 39 DB tests PASS
Step 40 DB tests PASS
Step 41 DB tests PASS
```

并确认：

```text
Full regression
=
0 failed
0 errors
```

如果出现失败：

先分类：

```text
Step 41 implementation bug
Historical guard
Test infrastructure
Environment
```

不要为了绿色测试而修改无关代码。

---

# 二十九、Git Diff Audit

完成后执行：

```powershell
git status --short
git diff --stat
git diff
```

重点确认：

```text
Production code changes = only Annotation review transition
ORM changes = 0
Schema changes = 0
Evidence state machine changes = 0
Conversation changes = 0
Service additions = 0
API changes = 0
Prompt changes = 0
Router changes = 0
RAG changes = 0
Text-to-SQL changes = 0
```

---

# 三十、Git 操作

本阶段：

```text
NO COMMIT
NO PUSH
NO PR
NO MERGE
```

不要创建 Commit。

当前 Step 37–40 的 commits：

```text
remain local
```

Step 50 才处理 Git。

---

# 三十一、发现现有 Bug 的处理

如果审计发现：

```text
EvidenceRepository
Annotation ORM
Annotation read model
```

存在与 Step 41 直接相关的真实 bug：

先判断：

```text
是否阻塞 Step 41？
```

如果是：

停止并报告。

如果是：

```text
Step 41 必须修复才能完成
```

允许最小修复。

但：

禁止顺手修：

```text
Step 42
Step 43
Conversation
Service
API
```

---

# 三十二、Step 41 完成判定

只有同时满足：

```text
DRAFT → REVIEWED = PASS

REVIEWED → DRAFT = REJECT

REVIEWED → REVIEWED = REJECT

UNKNOWN → valid = REJECT

Annotation Not Found = REJECT

Evidence ownership unchanged

Evidence status unchanged

Cross-Evidence isolation PASS

Persistence read-back PASS

Schema changes = 0

Production DB writes = 0

Full regression = PASS

compileall = PASS

Security boundary = PASS
```

才允许：

```text
Step 41 = COMPLETE
```

---

# 三十三、最终报告格式

严格输出：

```text
【Phase 4.1 Step 41 COMPLETE】

1. Audit Result
2. Contract
3. Modified Files
4. Annotation Review Transition
5. Illegal Transition Handling
6. Repository Transaction Boundary
7. Evidence Isolation
8. DB Schema
9. Security Boundary
10. Tests
11. Full Regression
12. compileall / lint
13. DB Writes
14. Git Status
15. Documentation
16. Problems Found
17. Problems Fixed
18. Current Limitations
19. Step 42 Readiness
```

其中：

### Step 42 Readiness

只能写：

```text
Step 42 = NEXT
```

不得开始 Step 42。

---

# 三十四、最终 STOP

完成 Step 41 后：

**立即 STOP。**

不要：

```text
进入 Step 42
设计 Finalization
冻结 Evidence/Annotation State Matrix
设计 Conversation ↔ Evidence
创建 ConversationEvidence
创建 Service
创建 API
Push
PR
Merge
```

本阶段唯一目标：

```text
Annotation

DRAFT
  ↓
REVIEWED
```

完成即停止。
