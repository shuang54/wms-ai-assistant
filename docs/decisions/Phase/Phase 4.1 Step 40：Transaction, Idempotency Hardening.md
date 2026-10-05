你现在开始实现：

`D:\coding\ai\wms-ai-assistant`

# Phase 4.1 Step 40：Transaction / Idempotency Hardening

---

# 一、阶段目标

本阶段唯一目标：

**验证并强化 Evidence Persistence 的事务边界与幂等行为。**

前置状态：

```text
Step 37
Evidence / Annotation Persistence
        ↓
Step 38
Provenance Persistence
        ↓
Step 39
Evidence ↔ Annotation Association
        ↓
Step 40
Transaction / Idempotency Hardening
```

当前已经确认：

```text
Evidence
    │
    └── evidence_id
            ↓
       Annotation
```

真实 PostgreSQL FK 已存在。

Step 40 不重新实现：

```text
Evidence Persistence
Annotation Persistence
Provenance Persistence
Evidence Association
```

只验证：

```text
Idempotency
Transaction Atomicity
Rollback
Duplicate Prevention
Failure Isolation
```

---

# 二、核心目标

必须最终证明：

```text
同一个 Evidence Idempotency Key
        ↓
重复提交
        ↓
不会产生第二条 Evidence
```

当前 Idempotency Key：

```text
(source_type, dataset_version)
```

即：

```text
source_type + dataset_version
```

---

# 三、开始编码前必须先 Audit

不要直接修改代码。

先阅读：

```text
backend/app/db/evidence_repository.py
backend/app/db/models/evidence_record.py
backend/app/db/models/evidence_annotation_record.py
backend/app/db/models/__init__.py

tests/test_evidence_persistence_db.py
tests/test_evidence_provenance_persistence_db.py
tests/test_evidence_annotation_association_db.py

docs/requirements.md
docs/architecture.md
docs/decisions/
```

搜索：

```text
idempot
source_type
dataset_version
UniqueConstraint
unique
IntegrityError
session.begin
transaction
rollback
commit
```

---

# 四、必须回答 Audit 问题

在修改任何生产代码之前明确：

## 1. Idempotency 当前在哪里实现？

确认：

```text
(source_type, dataset_version)
```

是否已经存在数据库：

```text
UNIQUE
```

约束。

不要只看 Repository。

必须检查 PostgreSQL：

```text
pg_constraint
```

确认真实 UNIQUE constraint。

---

## 2. Repository 当前如何处理重复？

检查：

```text
create_evidence()
find_by_idempotency_key()
```

以及所有相关调用。

判断：

```text
重复请求
```

当前行为是：

```text
A. 返回已有 Evidence
B. 抛异常
C. 数据库 IntegrityError
D. 产生重复记录
```

必须实测。

---

## 3. Transaction 当前由谁拥有？

Step 37 已经确认：

```python
with factory() as session, session.begin():
```

Repository owns transaction。

本阶段：

**优先保持这个设计。**

不要把 transaction ownership 移到 Service。

不要新增：

```text
TransactionService
UnitOfWork
TransactionManager
```

---

# 五、Step 40 Frozen Contract

冻结：

## Idempotency Key

```text
source_type
dataset_version
```

组合唯一：

```text
(source_type, dataset_version)
```

---

## Evidence Identity

重复请求不得生成：

```text
new evidence_id
```

即：

```text
Request A
source_type = knowledge_base
dataset_version = v1

Request B
source_type = knowledge_base
dataset_version = v1
```

必须：

```text
same logical Evidence
```

不能：

```text
Evidence A
Evidence B
```

---

# 六、Idempotency 行为

优先检查当前项目已有设计。

如果当前设计已经规定：

```text
find existing → return existing
```

则继续复用。

不要自行改变行为。

如果当前设计规定：

```text
duplicate → reject
```

则继续保持。

**Step 40 的目标是验证当前 contract，而不是重新设计 API。**

---

# 七、测试 Case 1：Sequential Duplicate

创建：

```text
Evidence A
```

使用：

```text
source_type = "knowledge_base"
dataset_version = "step40-v1"
```

再次创建完全相同：

```text
source_type = "knowledge_base"
dataset_version = "step40-v1"
```

验证：

```text
database count = 1
```

如果当前 contract 是返回已有 Evidence：

```text
evidence_id_1 == evidence_id_2
```

如果当前 contract 是 reject：

```text
second call rejected
```

但无论哪一种：

```text
count = 1
```

绝不能产生：

```text
count = 2
```

---

# 八、测试 Case 2：Different Dataset Version

创建：

```text
source_type = knowledge_base
dataset_version = v1
```

再创建：

```text
source_type = knowledge_base
dataset_version = v2
```

必须允许两条 Evidence。

即：

```text
knowledge_base + v1
knowledge_base + v2
```

是两个不同 Idempotency Key。

验证：

```text
evidence_id_v1 != evidence_id_v2
```

---

# 九、测试 Case 3：Different Source Type

创建：

```text
source_type = knowledge_base
dataset_version = v1
```

再创建：

```text
source_type = document
dataset_version = v1
```

必须允许。

因为：

```text
(source_type, dataset_version)
```

是组合 Key。

不能错误实现成：

```text
dataset_version UNIQUE
```

---

# 十、测试 Case 4：Transaction Rollback

这是本阶段最重要测试之一。

必须构造：

```text
BEGIN
 ↓
Evidence INSERT
 ↓
后续操作失败
 ↓
ROLLBACK
```

验证：

```text
Evidence 不存在
```

即：

```text
count_before
    ==
count_after
```

不能出现：

```text
Evidence 已存在
但后续操作失败
```

导致半成品。

---

# 十一、Rollback 测试方式

优先复用当前 Repository transaction pattern。

不要为了测试修改生产代码。

可以通过测试构造一个事务内故意失败的操作，例如：

```text
Evidence INSERT
        ↓
故意触发 NOT NULL / FK / UNIQUE violation
        ↓
Exception
        ↓
ROLLBACK
```

然后重新查询数据库。

必须证明：

```text
失败事务中的 Evidence
没有残留
```

---

# 十二、测试 Case 5：Annotation Atomicity

验证：

```text
Evidence
   +
Annotation
```

如果当前 Repository/API 有能力在同一事务中完成相关写入：

必须验证：

```text
Evidence success
Annotation success
```

或者：

```text
Evidence failure
Annotation failure
```

不能出现：

```text
Evidence exists
Annotation missing
```

这种半完成状态。

但是：

**不要为了测试强行新增 Evidence+Annotation Service。**

如果当前架构没有一个跨 Repository 的原子操作：

只记录：

```text
Current architecture does not expose cross-repository atomic transaction.
```

不要扩大 Step 40。

---

# 十三、Repository Transaction Boundary

检查：

```text
create_evidence()
create_annotation()
update_status()
```

是否各自拥有自己的：

```text
session.begin()
```

如果当前 Repository 已经明确：

```text
Repository owns transaction
```

继续保持。

禁止在 Step 40 中重构整个 transaction architecture。

---

# 十四、并发 Idempotency

这是本阶段必须考虑但不要过度工程化的测试。

需要验证：

```text
Request A
source_type = X
dataset_version = Y

Request B
source_type = X
dataset_version = Y
```

同时执行时：

最终数据库不能出现：

```text
2 Evidence
```

必须：

```text
1 Evidence
```

数据库 UNIQUE constraint 是最终安全边界。

---

# 十五、并发测试策略

如果当前测试基础设施允许：

使用两个独立 Session / transaction。

模拟：

```text
T1
T2
```

同时尝试插入：

```text
(source_type, dataset_version)
```

验证最终：

```text
one success
one duplicate/rejected
```

或者：

```text
one existing Evidence returned
one rejected
```

具体取决于当前 contract。

最重要：

```text
DB count = 1
```

---

# 十六、不要使用脆弱的 sleep 测试

禁止：

```python
time.sleep(1)
```

来“制造并发”。

如果当前测试环境无法稳定实现并发：

可以只验证：

```text
UNIQUE constraint
+
sequential duplicate
```

并在报告中记录：

```text
Concurrency stress test not implemented
```

不要为了测试并发引入复杂基础设施。

---

# 十七、PostgreSQL Constraint 实测

直接查询：

```text
pg_constraint
```

确认：

```text
Evidence
(source_type, dataset_version)
```

存在真实 UNIQUE constraint。

要求：

```text
Database constraint = PASS
```

不要只检查 ORM：

```python
UniqueConstraint(...)
```

---

# 十八、数据库写入边界

测试开始前：

```text
count_before
```

测试结束后：

```text
count_after
```

所有测试数据必须：

```text
self-created
```

不要：

```text
UPDATE existing production data
DELETE existing production data
TRUNCATE
```

禁止：

```text
生产 WMS 数据
生产业务表
```

最终：

```text
Production DB Writes = 0
```

---

# 十九、Security

Step 40 不得新增任何敏感字段。

检查：

```text
API key
password
database URL
connection string
authorization
token
secret
```

不得进入：

```text
Evidence
Annotation
logs
Read Model
metadata
exceptions
```

---

# 二十、是否修改生产代码

默认目标：

```text
Production code changes = 0
```

如果 Audit 发现：

```text
当前实现确实存在 idempotency / transaction bug
```

才允许最小修复。

如果需要修改：

必须先明确：

```text
Problem
Root Cause
Minimal Fix
```

然后再修改。

禁止为了“测试更容易通过”改变 contract。

---

# 二十一、历史 Guard

搜索：

```text
tests/test_conversation_context_*evidence*
tests/test_conversation_context_*annotation*
tests/test_*idempot*
tests/test_*transaction*
```

如果出现旧 Guard：

不要删除。

如果 Guard 只允许：

```text
Step 37
Step 38
Step 39
```

而现在 Step 40 合法增加：

```text
idempotency
transaction
```

只做最小 scope-aware migration。

不要扩大 Guard 白名单。

---

# 二十二、测试文件

优先：

```text
tests/test_evidence_transaction_idempotency_db.py
```

如果当前项目已有合适测试文件：

可以扩展。

至少包含：

```text
test_00_idempotency_constraint
test_01_sequential_duplicate
test_02_different_dataset_version
test_03_different_source_type
test_04_transaction_rollback
test_05_concurrent_duplicate
```

如果跨 Repository atomicity 无现有 API：

不要新增假的 API。

---

# 二十三、测试命令

先：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_evidence_transaction_idempotency_db.py
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_evidence_persistence_db.py tests/test_evidence_provenance_persistence_db.py tests/test_evidence_annotation_association_db.py tests/test_evidence_transaction_idempotency_db.py
```

然后完整回归：

```powershell
python -m pytest -q
```

如果完整 DB suite 需要环境变量：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

再执行：

```powershell
python -m compileall backend
```

以及：

```text
LSP
lint
```

---

# 二十四、Matrix

执行：

```powershell
python scripts/run_matrix_gate.py
```

要求：

```text
Status: PASS
Exit code: 0
```

不要修改：

```text
Matrix baseline
offline baseline
historical expected counts
```

如果失败：

先分类：

```text
Implementation Bug
Test Bug
Historical Guard
Matrix Cascade
```

再处理真正根因。

---

# 二十五、Git

执行前：

```bash
git status --short
git branch --show-current
git log -1 --oneline
```

执行后再次检查。

只允许：

```text
Step 40 changes
```

不要：

```text
push
PR
merge
```

除非用户明确要求。

如果项目当前工作流要求每 Step commit：

使用：

```text
feat: Phase 4.1 Step 40 Transaction Idempotency Hardening
```

否则保留工作区。

---

# 二十六、最终报告

完成后严格使用：

```text
【Phase 4.1 Step 40 COMPLETE】

1. Contract Audit
2. Idempotency Contract
3. PostgreSQL Unique Constraint
4. Sequential Duplicate
5. Different Dataset Version
6. Different Source Type
7. Transaction Rollback
8. Concurrent Duplicate
9. Annotation Atomicity
10. Production Code Changes
11. Security
12. DB Writes
13. Regression
14. Matrix
15. Git
16. Problems Found
17. Problems Fixed
18. Limitations
19. Next Step
```

明确输出：

```text
Idempotency = PASS
Transaction Rollback = PASS
Duplicate Prevention = PASS
Cross-Key Isolation = PASS
Production DB Writes = 0
```

---

# 二十七、完成边界

Step 40 完成后：

```text
Evidence Persistence       PASS
Annotation Persistence     PASS
Provenance Persistence     PASS
Evidence Association       PASS

Idempotency                 PASS
Transaction Boundary        PASS
Rollback                    PASS
Duplicate Prevention        PASS
```

但以下仍然：

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

完成 Step 40 后立即停止。

不要开始：

```text
Step 41 Review State
```

不要实现：

```text
Review
Finalization
Conversation Evidence
Evidence Workflow
Agent
MCP
Memory
Chat API
```

等待下一条指令。
