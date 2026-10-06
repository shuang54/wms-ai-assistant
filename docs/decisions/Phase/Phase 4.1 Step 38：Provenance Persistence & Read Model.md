你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 4.1 Step 38：Provenance Persistence & Read Model

## 一、阶段目标

Phase 4.1 Step 37 已经完成：

```text
Evidence
    ↓
PostgreSQL
    ↓
Evidence Record
```

本阶段只解决：

**将已经冻结的 EvidenceProvenance 真正绑定到 Evidence，并能够从 PostgreSQL 读取回来。**

目标：

```text
Evidence
   ↓
Provenance
   ↓
PostgreSQL
   ↓
Read Back
```

最终证明：

```text
Persisted Evidence
        +
Persisted Provenance
        ↓
Read Model
        ↓
Evidence → Provenance
```

---

# 二、严格禁止重新设计 Provenance

Step 27 已经冻结：

```text
EvidenceProvenance
├── dataset_version
└── source_type
```

因此：

**不得重新设计 EvidenceProvenance。**

不得增加：

```text
source_ref
content_ref
locator
file_path
raw_content
url
document_path
```

也不得建立第二套：

```text
Provenance
EvidenceProvenance
SourceProvenance
DatasetProvenance
```

等重复模型。

必须复用当前项目已经存在的：

```text
EvidenceProvenance
```

---

# 三、开始编码前必须先阅读真实代码

不要假设接口。

先检查：

```text
backend/app/
tests/
docs/
```

重点搜索：

```text
EvidenceProvenance
dataset_version
source_type
evidence_id
EvidenceRecord
EvidenceRepository
```

同时检查 Step 37 当前真实实现：

```text
backend/app/db/models/evidence_record.py
backend/app/db/models/evidence_annotation_record.py
backend/app/db/evidence_repository.py
backend/app/db/models/__init__.py
```

以及：

```text
tests/test_evidence_persistence_db.py
tests/test_evidence_persistence_boundary.py
```

必须确认：

1. `EvidenceProvenance` 当前定义在哪里。
2. 当前哪些代码消费 `EvidenceProvenance`。
3. 当前 `EvidenceRecord` 字段。
4. 当前 Repository transaction 方式。
5. 当前 PostgreSQL schema。
6. 当前测试 fixture。
7. 当前 `create_all()` 初始化方式。
8. 当前是否已经存在可以复用的 provenance persistence 结构。

---

# 四、Step 38 的唯一核心关系

必须最终形成：

```text
EvidenceRecord
      │
      │ 1 : 1
      ↓
EvidenceProvenance
```

具体数据库实现方式：

**必须先根据现有代码审计结果决定。**

优先级：

### 优先方案

如果当前 `EvidenceProvenance` 本质上就是 Evidence 的不可分离元数据：

可以直接扩展：

```text
ai_ops.evidence_record
```

保存：

```text
dataset_version
source_type
```

但必须确认：

* 与 Step 36/37 冻结契约一致
* 不破坏已有 Evidence 幂等 key
* 不产生重复 provenance
* 不新增第二个 Provenance 对象

### 备选方案

只有在现有架构明确要求 Provenance 独立生命周期时，才建立：

```text
ai_ops.evidence_provenance_record
```

并建立：

```text
evidence_id
    ↓
provenance
```

FK。

**不要为了“看起来更规范”而额外建表。**

---

# 五、不要猜数据库设计

执行以下审计：

```text
EvidenceProvenance
    ↓
当前字段
    ↓
当前消费者
    ↓
Step 27 Contract
    ↓
Step 37 Evidence Schema
```

然后确定最小实现。

如果审计发现存在冲突：

**停止编码并报告。**

报告：

```text
冲突位置：
当前实现：
Step 27 冻结契约：
Step 37 当前 schema：
建议：
```

不要自行修改旧契约。

---

# 六、Repository 要求

继续复用：

```text
EvidenceRepository
```

不要创建：

```text
ProvenanceRepository
```

除非当前项目已有明确 Repository 分层要求，并且审计证明必须独立存在。

Repository 最终至少能够：

```text
create evidence + provenance
        ↓
read evidence + provenance
```

以及：

```text
get_by_id(evidence_id)
```

能够获得：

```text
Evidence
+
EvidenceProvenance
```

不得暴露：

```text
SQLAlchemy Session
Connection
Engine
```

给上层。

---

# 七、事务要求

必须继续遵守 Step 37 的事务模型：

```text
Repository
    ↓
transaction
    ↓
Evidence
    +
Provenance
```

不能出现：

```text
Evidence commit
      ↓
Provenance commit
```

这种两个独立 transaction。

要求：

```text
Evidence + Provenance
```

作为一个原子 persistence unit。

如果 provenance 写入失败：

```text
ROLLBACK
```

不能留下半条 Evidence。

---

# 八、Idempotency

Step 37 已经冻结：

```text
source_type + dataset_version
```

作为 Evidence import idempotency key。

Step 38 不得创建第二套幂等规则。

验证：

```text
same source_type
+
same dataset_version
```

再次 persistence：

```text
same evidence_id
same provenance
no duplicate row
```

---

# 九、Provenance 一致性

必须验证：

```text
dataset_version
```

以及：

```text
source_type
```

在：

```text
input
→ ORM
→ PostgreSQL
→ Repository
→ Read Model
```

整个链路中保持一致。

禁止：

```text
None
unknown
fake-source
generated-source
```

等默认值替代真实 Provenance。

如果缺少必要 Provenance：

**必须按照现有契约拒绝 persistence。**

不要自动补值。

---

# 十、Read Model

增加最小读取能力。

目标：

```python
evidence = repository.get_by_id(evidence_id)
```

能够获得：

```text
evidence.evidence_id
evidence.dataset_version
evidence.source_type
```

如果 Provenance 是独立实体：

则必须能够：

```text
evidence
  ↓
provenance
```

读取。

Read Model 不得携带：

```text
database connection
session
internal SQL object
```

不得返回 SQLAlchemy Row / Result 给业务层。

---

# 十一、跨 Evidence 隔离

至少建立：

```text
Evidence A
dataset_version = v1
source_type = source-a

Evidence B
dataset_version = v2
source_type = source-b
```

验证：

```text
read(A) != B provenance
read(B) != A provenance
```

禁止：

```text
global provenance
shared provenance
latest provenance
```

等错误关联方式。

---

# 十二、PostgreSQL DB Test

新增或扩展：

```text
tests/test_evidence_provenance_persistence_db.py
```

如果当前测试结构更适合直接扩展：

```text
tests/test_evidence_persistence_db.py
```

则优先复用现有文件。

必须使用：

```text
RUN_DB_TESTS=1
```

真实 PostgreSQL。

---

## DB Test 1：Create

创建：

```text
Evidence
+
Provenance
```

验证：

```text
INSERT successful
```

---

## DB Test 2：Read Back

通过：

```text
EvidenceRepository
```

读取。

验证：

```text
evidence_id
dataset_version
source_type
```

完全一致。

---

## DB Test 3：Cross Evidence Isolation

创建：

```text
Evidence A
Evidence B
```

分别读取。

确认 provenance 不串。

---

## DB Test 4：Idempotency

重复相同：

```text
source_type
dataset_version
```

确认：

```text
same evidence_id
no duplicate Evidence
no duplicate Provenance
```

---

## DB Test 5：Rollback

构造：

```text
Evidence persistence
+
Provenance persistence failure
```

验证：

```text
ROLLBACK
```

最终：

```text
Evidence count unchanged
Provenance count unchanged
```

---

## DB Test 6：Invalid Provenance

缺失：

```text
dataset_version
```

或者：

```text
source_type
```

根据当前冻结契约测试拒绝。

不得生成：

```text
unknown
default
fake
```

---

# 十三、Security Test

继续保持 Step 37 安全边界。

Provenance persistence 中：

禁止出现：

```text
api_key
password
authorization
database_url
connection_string
llm_secret
token
```

以及：

```text
raw_content
source_ref
content_ref
locator
file_path
```

如果只是 provenance 元数据：

**不要因为测试方便而保存原始文档内容。**

---

# 十四、Schema 约束

如果新增独立 Provenance 表：

必须有：

```text
PRIMARY KEY
FOREIGN KEY evidence_id → evidence_record.evidence_id
```

如果是一对一：

必须由数据库层保证唯一性。

不要只依赖 Python：

```text
if exists:
```

SQLAlchemy 支持在数据库层通过 FK 和 UniqueConstraint 表达这类关系约束。

如果直接扩展：

```text
evidence_record
```

则必须验证现有：

```text
UNIQUE(source_type, dataset_version)
```

仍然保持。

---

# 十五、禁止修改的核心模块

本阶段不得修改：

```text
AI Router
AI Orchestrator
RAG
Tool Registry
Text-to-SQL
SQL Validator
SQL Executor
RelevantTableSelector
DatabaseContextComposer
Business Semantic
Conversation Runtime
```

也不得新增：

```text
Agent
MCP
Workflow
Memory
Planning
Multi-Agent
Chat API
Chat UI
```

---

# 十六、不要提前实现 Step 39

Step 39 才负责：

```text
Evidence ↔ Annotation Association
```

因此 Step 38：

**不得实现 Annotation Association 新逻辑。**

允许使用已有：

```text
evidence_id
```

验证 Evidence 本身的 provenance 关联。

但不要实现：

```text
annotation → provenance
annotation → dataset
annotation → review
```

这些属于后续阶段。

---

# 十七、文档

新增：

```text
docs/evaluation/Phase 4.1 Step 38：Provenance Persistence Evaluation.md
```

记录：

```text
1. Contract
2. Current Implementation Audit
3. Schema Decision
4. ORM
5. Repository
6. Transaction
7. Idempotency
8. Read Model
9. Cross Evidence Isolation
10. Security
11. DB Tests
12. Regression
13. Limitations
```

新增：

```text
docs/decisions/Phase/Phase 4.1 Step 38：Provenance Persistence Implementation.md
```

记录：

```text
Context
Decision
Alternatives
Schema
Transaction
Security
Non-goals
Verification
```

特别记录：

```text
为什么选择扩展 evidence_record
```

或者：

```text
为什么必须建立独立 provenance record
```

必须基于真实代码审计，而不是主观偏好。

---

# 十八、测试顺序

严格按顺序执行：

### Gate 1

```powershell
python -m pytest -q tests/test_evidence_persistence_boundary.py
```

### Gate 2

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_evidence_persistence_db.py
```

以及 Step 38 provenance DB tests。

### Gate 3

```powershell
python -m pytest -q
```

必须：

```text
0 failed
0 errors
```

### Gate 4

```powershell
python scripts/run_matrix_gate.py
```

必须：

```text
Status: PASS
Exit code: 0
```

### Gate 5

```powershell
python -m compileall backend
```

必须成功。

---

# 十九、DB 写入规则

测试数据库允许：

```text
本测试创建的 Evidence
本测试创建的 Provenance
```

测试结束：

```text
删除本测试创建记录
```

并验证：

```text
count_before == count_after
```

不得：

```text
TRUNCATE
```

不得删除：

```text
非本测试创建数据
```

最终报告必须明确：

```text
Production DB Writes = 0
DB Test Writes = self-created records only
```

---

# 二十、Git 要求

当前不要求：

```text
push
PR
merge
```

本阶段只要求：

```text
local commit
```

如果实现完成：

创建一个 Step 38 commit。

建议：

```text
feat: Phase 4.1 Step 38 Provenance Persistence
```

不要修改 Step 37 已有 commit。

---

# 二十一、失败规则

如果发现：

```text
EvidenceProvenance contract conflict
Schema conflict
Repository transaction conflict
DB architecture conflict
```

立即停止。

不要：

```text
修改 Step 27 contract
修改 Step 37 schema
删除历史测试
skip 测试
降低 guard 强度
修改核心 AI Runtime
```

最终报告：

```text
问题：
证据：
影响：
建议：
```

---

# 二十二、最终报告

完成后严格按照：

```text
【Phase 4.1 Step 38 COMPLETE】

1. Provenance Contract
2. Current Implementation Audit
3. Schema Decision
4. ORM
5. Repository
6. Transaction
7. Idempotency
8. Read Model
9. Cross Evidence Isolation
10. PostgreSQL
11. DB Tests
12. Security
13. Regression
14. Matrix Gate
15. Production Code Changes
16. DB Schema Changes
17. DB Test Writes
18. Production DB Writes
19. New Files
20. Modified Files
21. Problems Found
22. Problems Fixed
23. Current Limitations
24. Git Commit
25. Next Step
```

必须明确：

```text
Production DB Writes = 0
```

---

# 二十三、最终架构目标

完成后必须能够证明：

```text
Evidence
   │
   ├── evidence_id
   ├── dataset_version
   ├── source_type
   │
   └── Provenance
          │
          ├── dataset_version
          └── source_type
                ↓
            PostgreSQL
                ↓
            Repository
                ↓
             Read Back
```

---

# 二十四、STOP 条件

完成 Step 38 后：

**立即停止。**

不要进入：

```text
Step 39
```

不要：

```text
push
PR
merge
Agent
MCP
Workflow
Memory
Chat API
```

等待下一条指令。
