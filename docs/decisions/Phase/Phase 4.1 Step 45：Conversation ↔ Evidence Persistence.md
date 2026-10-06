你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 4.1 Step 45：Conversation ↔ Evidence Persistence

## 一、阶段目标

Phase 4.1 Step 44 已经完成：

```text
Conversation ↔ Evidence Integration Contract
```

现在进入：

```text
Step 45
Conversation ↔ Evidence
Persistence
```

本阶段唯一目标：

> **把 Step 44 已冻结的 Conversation → Evidence Reference 关系落到 PostgreSQL，并提供最小、明确、可验证的持久化边界。**

最终形成：

```text
Conversation
    │
    │ 0..N reference
    ▼
conversation_evidence
    │
    │ N..1
    ▼
Evidence
```

注意：

```text
Conversation ≠ Evidence Owner
Conversation ≠ Evidence Identity
Conversation ≠ Evidence Provenance
```

Evidence 仍然是独立、可复用的 Domain Object。

---

# 二、Step 45 开始前必须重新阅读

不要假设接口。

阅读真实代码：

```text
backend/app/db/models/conversation.py
backend/app/db/models/conversation_turn.py
backend/app/db/models/evidence_record.py
backend/app/db/models/evidence_annotation_record.py

backend/app/db/conversation_repository.py
backend/app/db/evidence_repository.py

backend/app/db/models/__init__.py
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

重点搜索：

```text
conversation_id
evidence_id
turn_id
assistant_request_id
dataset_version
source_type
create_all
Repository
transaction
```

---

# 三、Step 44 已冻结的边界必须继承

Step 44 已明确：

```text
Evidence = Independent Domain Object
Conversation → Evidence = REFERENCE
Evidence = Reusable
No cascade lifecycle coupling
```

因此：

## 禁止

不要修改：

```text
evidence_record
```

增加：

```text
conversation_id
```

也不要修改：

```text
conversation
```

增加：

```text
evidence_id
```

更不要修改：

```text
conversation_turn
```

增加：

```text
evidence_id
```

本阶段使用：

```text
conversation_evidence
```

建立关联。

---

# 四、Step 45 必须先冻结 OD-11 / OD-12 / OD-13

Step 44 留下：

```text
OD-11
Conversation ↔ Evidence cardinality

OD-12
Turn-level reference

OD-13
Duplicate association semantics
```

在写任何 ORM / SQL 之前：

**必须先根据真实代码和现有契约完成决策。**

---

# 五、OD-11：Association Cardinality

目标：

```text
Conversation → Evidence
Evidence → Conversation
```

推荐冻结：

```text
Conversation → 0..N Evidence
Evidence → 0..N Conversation
```

理由：

1. Evidence 已被定义为 Reusable Artifact。
2. Conversation 本身可以有多个业务事件 / AI 结果。
3. 一个 Evidence 不应该因为被一个 Conversation 引用而失去复用能力。
4. 直接 FK 无法自然表达双向 0..N。

如果真实代码或历史契约发现冲突：

不要强行采用推荐值。

应记录：

```text
Observed
Contract
Decision
```

然后再决定。

---

# 六、OD-12：Turn-level Reference

必须决定：

```text
Conversation ↔ Evidence
```

是否同时需要：

```text
ConversationTurn ↔ Evidence
```

本阶段默认建议：

```text
Step 45：
只持久化 Conversation ↔ Evidence

Step 46：
根据真实 AI Runtime E2E 再决定是否需要 Turn-level association
```

原因：

当前 Step 45 的核心目标是：

```text
Conversation Evidence Reference Persistence
```

不是完整：

```text
Question → Answer → Evidence provenance
```

如果 Step 44 审计后没有足够证据证明 Turn-level 必须进入持久化模型：

可以冻结：

```text
OD-12 = DEFERRED TO STEP 46
```

但必须明确：

```text
不是永久拒绝
不是忽略
而是延后到真实 Runtime E2E 决策
```

如果现有正式契约已经明确需要 Turn-level：

则必须按真实契约实施。

---

# 七、OD-13：Duplicate Association

需要决定：

同一个：

```text
conversation_id
+
evidence_id
```

重复建立关联时如何处理。

推荐：

```text
IDEMPOTENT
```

即：

```text
Conversation A
    ↓
Evidence E
```

重复写入：

```text
A → E
A → E
```

最终数据库仍只有：

```text
A → E
```

因此：

```text
UNIQUE(conversation_id, evidence_id)
```

推荐作为数据库约束。

理由：

1. Step 40 已经冻结 Repository / DB 双层幂等原则。
2. 当前 association 是 Reference，不是 Event。
3. 当前没有 turn-level identity，因此不能靠 turn_id 区分重复引用。
4. 重复 association 没有业务价值。

如果审计发现现有契约要求重复引用：

不要使用本推荐。

---

# 八、Association Table

如果 OD-11/13 确认采用推荐设计：

创建：

```text
ai_ops.conversation_evidence
```

最小字段：

```text
conversation_id
evidence_id
created_at
```

主键 / 唯一约束：

推荐：

```text
UNIQUE(conversation_id, evidence_id)
```

是否增加独立：

```text
conversation_evidence_id
```

必须根据现有项目 ORM / Repository 习惯判断。

**不要无意义增加 ID。**

如果关联记录本身不需要独立身份：

可以使用复合主键：

```text
(conversation_id, evidence_id)
```

但必须与项目现有 ORM 风格一致。

---

# 九、Foreign Key

必须使用真实 PostgreSQL FK：

```text
conversation_evidence.conversation_id
    → conversation.conversation_id
```

以及：

```text
conversation_evidence.evidence_id
    → evidence_record.evidence_id
```

必须明确 ON DELETE 行为。

Step 44 已冻结：

```text
Conversation 删除
    ≠
Evidence 删除
```

因此不能：

```text
Conversation delete
→ Evidence delete
```

同样：

```text
Evidence delete
→ Conversation delete
```

也不允许。

Association row 可以根据父对象删除语义选择：

```text
ON DELETE CASCADE
```

但必须注意：

这只表示：

```text
删除 Conversation
→ 删除 association row
```

不是：

```text
删除 Conversation
→ 删除 Evidence
```

同理：

```text
删除 Evidence
→ 删除 association row
```

不是：

```text
删除 Evidence
→ 删除 Conversation
```

如果使用 CASCADE，必须通过真实 DB 测试验证。

---

# 十、Repository Ownership

Association 的数据库访问必须有明确 Owner。

推荐：

```text
ConversationEvidenceRepository
```

或者如果当前项目 Repository 设计明确要求：

```text
ConversationRepository
```

负责 association。

必须根据现有代码风格决定。

不要同时让：

```text
ConversationRepository
EvidenceRepository
ConversationEvidenceRepository
```

重复拥有同一张表。

必须只有一个明确 persistence owner。

---

# 十一、Repository API

只实现当前 Step 45 必需的最小 API。

推荐能力：

```text
create_association(
    conversation_id,
    evidence_id
)
```

```text
get_association(
    conversation_id,
    evidence_id
)
```

```text
list_evidence_ids(
    conversation_id
)
```

```text
list_conversation_ids(
    evidence_id
)
```

具体命名必须遵循现有项目风格。

不要提前实现：

```text
Service
API
Runtime
Workflow
```

---

# 十二、Repository Transaction

必须延续 Step 37–40 的 Repository transaction boundary。

类似：

```python
with factory() as session, session.begin():
    ...
```

但：

**必须根据真实现有代码实现，不要复制粘贴猜测。**

Repository 必须保证：

```text
success
    ↓
commit
```

失败：

```text
exception
    ↓
rollback
```

不能产生半关联状态。

---

# 十三、Duplicate Association

测试：

```text
create(A, E)
create(A, E)
```

期望：

```text
只有一个 association
```

不能：

```text
两个重复 rows
```

不能依赖 Python 代码单独防重复。

必须同时有：

```text
Repository protection
+
Database UNIQUE
```

如果项目现有 Repository 的 first-write-wins / idempotent 模式适合复用，应优先复用。

---

# 十四、Invalid Conversation

测试：

```text
conversation_id = nonexistent
evidence_id = valid
```

必须失败。

数据库：

```text
association count unchanged
```

---

# 十五、Invalid Evidence

测试：

```text
conversation_id = valid
evidence_id = nonexistent
```

必须失败。

数据库：

```text
association count unchanged
```

---

# 十六、Cross-Conversation Isolation

至少：

```text
Conversation A
Conversation B

Evidence E
Evidence F
```

建立：

```text
A → E
B → F
```

验证：

```text
list_evidence(A)
    == E

list_evidence(B)
    == F
```

不能：

```text
A → F
B → E
```

---

# 十七、Evidence Reuse

必须验证：

```text
Conversation A → Evidence E
Conversation B → Evidence E
```

这是合法的。

最终：

```text
list_conversations(E)
    == {A, B}
```

不能因为 E 已被 A 使用：

```text
reject B → E
```

---

# 十八、Conversation Multiple Evidence

验证：

```text
Conversation A
    ├── Evidence E
    ├── Evidence F
    └── Evidence G
```

必须能够持久化。

最终：

```text
list_evidence(A)
    == {E, F, G}
```

---

# 十九、No Cascade Lifecycle Coupling

必须验证：

## Conversation status change

例如：

```text
ACTIVE
→
ARCHIVED
```

不会：

```text
Evidence.status change
```

---

## Evidence status change

例如：

```text
ANNOTATED
→
REVIEWED
```

不会：

```text
Conversation.status change
```

---

## Evidence Finalization

```text
REVIEWED
→
FINALIZED
```

不会：

```text
Conversation
→
ARCHIVED
```

---

# 二十、Delete Semantics

必须真实 PostgreSQL 验证。

## Case A

```text
Conversation A
    ↓
Evidence E
```

删除 Conversation A：

期望：

```text
association row = deleted
Evidence E = remains
```

---

## Case B

```text
Conversation A
    ↓
Evidence E
```

删除 Evidence E：

期望：

```text
association row = deleted
Conversation A = remains
```

如果项目当前没有 Evidence 删除能力：

可以使用测试层直接删除父记录进行 FK 行为验证。

但：

**不得修改 Repository 的生产删除 API 只是为了测试。**

---

# 二十一、Read Model

如果项目已有 frozen read model 风格：

例如：

```text
EvidenceProvenanceRow
EvidenceWithProvenance
AnnotationRow
```

Association read model 应保持：

```text
frozen
```

不能暴露：

```text
SQLAlchemy Session
Connection
Engine
ORM object
Result
```

例如可以设计：

```text
ConversationEvidenceReference
```

字段只包含：

```text
conversation_id
evidence_id
created_at
```

如果实际项目已有更合适的命名：

遵循现有命名。

---

# 二十二、Security Boundary

Association read model / Repository result：

禁止泄漏：

```text
API key
password
DATABASE_URL
connection string
authorization header
token
SQLAlchemy Session
SQLAlchemy Connection
Engine
```

不要把 Evidence 原始内容自动带入 Association Result。

不要把：

```text
raw_content
```

加入 association 表。

---

# 二十三、不要加入 Turn-level 字段

如果 OD-12 被决定延期：

不要增加：

```text
turn_id
assistant_request_id
```

到：

```text
conversation_evidence
```

尤其不要因为：

```text
ConversationTurn
```

已经存在，就顺手加入：

```text
turn_id
```

这是未来 Step 46 的问题。

---

# 二十四、不要加入 provenance 字段

禁止在：

```text
conversation_evidence
```

中加入：

```text
dataset_version
source_type
```

因为它们属于：

```text
Evidence Provenance
```

不是：

```text
Association Identity
```

查询时通过：

```text
evidence_id
```

访问 Evidence。

---

# 二十五、数据库结构

本阶段预计：

```text
DB Schema Changes = 1 new table
```

新增：

```text
ai_ops.conversation_evidence
```

除此之外：

```text
evidence_record = unchanged
evidence_annotation_record = unchanged
conversation = unchanged
conversation_turn = unchanged
```

不得：

```text
ALTER existing business tables
```

不得修改：

```text
WMS tables
ERP tables
MOM tables
```

---

# 二十六、create_all / Migration

项目当前使用：

```text
Base.metadata.create_all()
```

而不是 Alembic。

因此：

不要突然引入 Alembic。

按照现有项目模式注册：

```text
ConversationEvidenceRecord
```

到：

```text
backend/app/db/models/__init__.py
```

然后由现有：

```text
create_all()
```

创建。

但必须先确认真实项目当前初始化机制。

---

# 二十七、Production DB Safety

本阶段所有测试数据必须：

```text
self-created
```

并且：

```text
self-cleaned
```

禁止：

```text
TRUNCATE
```

禁止：

```text
DELETE FROM ai_ops.conversation_evidence
```

这种全表清理。

优先：

```text
WHERE conversation_id = test_id
```

或：

```text
WHERE evidence_id = test_id
```

精确删除。

最终：

```text
DB residue = 0
```

---

# 二十八、测试文件

建议：

```text
tests/test_conversation_evidence_persistence_db.py
```

至少覆盖：

```text
1. table exists
2. FK conversation
3. FK evidence
4. create association
5. duplicate idempotency
6. invalid conversation
7. invalid evidence
8. conversation → multiple evidence
9. evidence → multiple conversation
10. cross-conversation isolation
11. delete conversation → association removed, evidence remains
12. delete evidence → association removed, conversation remains
13. status changes do not cascade
14. read model security
```

不要机械地必须写 14 个 test。

如果一个测试能够完整覆盖一个契约，可以合并。

---

# 二十九、E2E Persistence

至少有一个完整测试：

```text
Conversation
    ↓
Evidence
    ↓
Association Repository
    ↓
PostgreSQL
    ↓
Read back
```

必须证明：

```text
write
↓
commit
↓
new session
↓
read
↓
same association
```

不能只在同一个 SQLAlchemy Session 内验证。

---

# 三十、Transaction Rollback

必须增加：

```text
create association
+
forced failure
```

验证：

```text
association does not remain
```

如果 duplicate UNIQUE violation 是自然测试场景，也可以利用：

```text
transaction
→ UNIQUE violation
→ rollback
```

验证没有半成品。

不要人工模拟一个不存在的 transaction API。

---

# 三十一、No Production Runtime Integration

Step 45 不得修改：

```text
AIOrchestrator
AI Router
RAG
Tool
Text-to-SQL
Conversation Runtime
API
```

也就是说：

现在：

```text
Conversation
```

不会自动产生：

```text
conversation_evidence
```

真正的：

```text
Conversation
→ AI Runtime
→ Evidence
→ Association
```

属于：

```text
Step 46
```

---

# 三十二、Step 46 Boundary

Step 45 完成后：

```text
Persistence
    ↓
ready
```

但还没有：

```text
Runtime Integration
```

Step 46 才验证：

```text
Conversation
    ↓
AI Runtime
    ↓
AI Result
    ↓
Evidence
    ↓
Conversation Evidence Association
    ↓
Read-back
```

Step 45 不提前做。

---

# 三十三、Documentation

更新：

```text
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
```

新增：

```text
## Conversation ↔ Evidence Persistence
```

至少记录：

```text
1. Association Table
2. Cardinality
3. Ownership
4. Reference Semantics
5. Unique Constraint
6. Foreign Keys
7. Delete Semantics
8. Transaction Boundary
9. Turn-level Boundary
10. Provenance Boundary
11. Security Boundary
12. Step 46 Runtime Boundary
```

---

# 三十四、Roadmap

更新：

```text
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
```

Step 45 状态：

```text
Step 45 = COMPLETE
```

记录：

```text
Conversation ↔ Evidence Persistence = COMPLETE
```

以及：

```text
DB Schema = 1 association table
```

如果 OD-12 仍然 deferred：

明确记录：

```text
Turn-level Evidence association = deferred to Step 46
```

不要把它伪装成已经解决。

---

# 三十五、Architecture 文档

默认：

```text
docs/architecture.md
```

不要修改。

如果发现架构文档与真实实现出现明确事实冲突：

只记录：

```text
Architecture drift
→ deferred to Step 51
```

不要在 Step 45 大规模同步 Architecture。

---

# 三十六、Full Regression

先运行：

```powershell
python -m pytest -q tests/test_conversation_evidence_persistence_db.py
```

然后：

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
python -m compileall -q backend tests scripts
```

以及：

```powershell
python scripts/run_matrix_gate.py
```

---

# 三十七、既有 3 个 DB Baseline Failure

Step 42–44 已确认：

```text
test_db_residue_is_zero
test_baseline_matches_step89_actual_execution
test_gate_passes_when_current_matches_baseline
```

可能继续出现。

如果出现：

**不要修改 baseline。**

不要修改无关测试。

先确认：

```text
是否与 Step 42/43/44 完全相同
```

如果相同：

记录：

```text
pre-existing baseline issue
```

如果出现新的 failure：

必须定位是否由 Step 45 引入。

---

# 三十八、Git

禁止：

```text
git commit
git push
PR
merge
```

Step 37–49 仍然属于本地开发验证阶段。

Step 50 才统一 Git 收口。

---

# 三十九、最终报告格式

完成后严格使用：

```text
【Phase 4.1 Step 45 COMPLETE】

1. Pre-implementation Decisions
2. OD-11
3. OD-12
4. OD-13
5. Association Model
6. Ownership
7. Reference Semantics
8. Cardinality
9. ORM
10. Repository
11. Foreign Keys
12. Unique Constraint
13. Delete Semantics
14. Transaction / Idempotency
15. Read Model
16. Tests
17. Full Regression
18. Matrix
19. DB Schema
20. DB Writes
21. Security
22. Modified Files
23. Git Status
24. Problems Found
25. Problems Fixed
26. Current Limitations
27. Step 46 Readiness
```

必须明确：

```text
Step 45 = COMPLETE
Step 46 = NEXT
```

---

# 四十、最终架构

Step 45 完成后，应形成：

```text
Conversation
    │
    │ 0..N
    ▼
conversation_evidence
    │
    │ N..1
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

核心原则：

```text
Conversation
    ≠
Evidence Owner

Conversation
    ≠
Evidence Identity

Conversation
    ≠
Evidence Provenance
```

并且：

```text
Conversation A
    ├── Evidence E
    ├── Evidence F
    └── Evidence G

Conversation B
    └── Evidence E
```

是合法的。

而：

```text
Conversation A delete
```

只能导致：

```text
A → E/F/G association removed
```

不能导致：

```text
E/F/G deleted
```

---

# 四十一、停止条件

完成：

```text
OD-11/12/13
↓
Association Model
↓
ORM
↓
Repository
↓
FK
↓
Unique
↓
Transaction
↓
PostgreSQL E2E
↓
Isolation
↓
Delete Semantics
↓
Regression
↓
Matrix
↓
Documentation
```

之后：

**立即停止。**

不要进入 Step 46。

不要接 AI Runtime。

不要修改 AIOrchestrator。

不要修改 Conversation API。

不要创建 Chat API。

不要开发 Agent。

不要开发 MCP。

不要做 Turn-level Runtime Integration。

不要继续扩展 Conversation 功能。

只有完成 Step 45 并由我确认后，才进入 Step 46。
