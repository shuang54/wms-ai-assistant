你现在开始实现：

# Phase 4.1 Step 46：Real Conversation Evidence E2E

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

本阶段第一次验证：

**Conversation → AI Runtime → AI Result → Evidence → ConversationEvidence → PostgreSQL → Read-back**

目标不是增加新的 AI 能力。

目标是证明：

> 已有 Conversation、AI Runtime、Evidence Persistence、ConversationEvidence Persistence 可以在真实运行链路中闭环。

最终必须形成可重复执行的真实 E2E 测试。

---

# 二、必须先阅读，禁止直接编码

先阅读真实代码：

```text
backend/app/services/ai_orchestrator_service.py
backend/app/services/ai_router_service.py

backend/app/services/rag_service.py
backend/app/services/text_to_sql_service.py
backend/app/services/sql_executor_service.py

backend/app/db/evidence_repository.py
backend/app/db/conversation_evidence_repository.py

backend/app/db/models/evidence.py
backend/app/db/models/evidence_annotation.py
backend/app/db/models/conversation.py
backend/app/db/models/conversation_evidence.py

backend/app/services/
backend/app/api/

tests/
```

重点寻找并确认：

```text
Conversation 创建入口
ConversationTurn 创建入口
Conversation Service / Repository
AIOrchestrator.execute() 真实签名
AIOrchestrationResult 真实结构
现有 Fake LLM
现有 Fake RAG
现有 Fake Tool
现有 DB fixture
现有 Evidence fixture
现有 Conversation fixture
现有 DB cleanup 机制
```

同时阅读：

```text
docs/decisions/Phase/Phase 4.1 Roadmap v1.1.md
docs/decisions/Phase/Phase 4.1 Lifecycle Contract.md
```

---

# 三、严格禁止

本阶段禁止：

```text
Agent
Multi-Agent
MCP
LangGraph
Memory
Planning
Workflow
RBAC
Multi-tenancy
Chat UI
Streaming
Autonomous Planning
```

禁止修改：

```text
AI Router 核心行为
AI Orchestrator 核心行为
RAG 核心算法
Tool Framework
Text-to-SQL
SQL Validator
SQL Executor
Evidence 核心状态机
Annotation 状态机
```

除非 E2E 明确发现真实 bug。

如果发现真实 bug：

**先停止并报告，不要为了通过测试而修改核心代码。**

---

# 四、本阶段核心链路

必须验证：

```text
Conversation
    ↓
ConversationTurn
    ↓
AIOrchestrator
    ↓
AI Result
    ↓
Evidence
    ↓
ConversationEvidence
    ↓
PostgreSQL
    ↓
Repository Read-back
```

注意：

Step 46 不要求一定使用真实 DeepSeek。

默认继续使用：

```text
Fake LLM / Fake Runtime dependency
```

以保证：

```text
pytest -q
```

不产生真实 LLM API 调用。

如果现有项目已有可靠的 Fake AI Runtime，则优先复用。

---

# 五、第一步：确定真实 Conversation → AI Runtime 入口

先找到当前项目实际入口。

不要自己发明：

```python
conversation.run()
```

之类的新 API。

必须使用当前真实：

```text
Conversation
+
AIOrchestrator
```

接口。

确认：

```text
Conversation
    ↓
ConversationTurn
    ↓
AIOrchestrator.execute(...)
```

之间当前是否已经存在 service/repository 边界。

如果 Conversation 当前没有自动调用 AIOrchestrator：

**不要为了 Step 46 创建新的生产 runtime orchestration API。**

可以在 E2E 测试中按照当前真实 Service 组合方式调用。

---

# 六、Evidence 生成边界

Step 46 必须确认：

AI Result 到 Evidence 的映射方式。

首先寻找项目中是否已有：

```text
Evidence Builder
Evidence Factory
Evidence Mapper
Evidence Persistence Service
```

如果已有：

**必须复用。**

如果不存在：

不要立即创建复杂 Evidence Service。

可以在测试层使用当前已冻结的 Evidence persistence contract，验证：

```text
AI Result
    ↓
Evidence persistence input
    ↓
EvidenceRepository
```

但必须在最终报告中明确：

```text
Evidence creation orchestration:
existing / test-composed / missing
```

不要偷偷引入新的业务层。

---

# 七、Evidence 内容边界

Step 46 不得改变 Step 36–38 已冻结的 Evidence schema。

Evidence 只能继续使用已有：

```text
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
source_type
```

禁止新增：

```text
conversation_id
turn_id
assistant_request_id
provider_request_id
raw_content
file_path
source_ref
content_ref
```

到 Evidence identity / provenance。

---

# 八、ConversationEvidence Association

使用 Step 45 已实现的：

```text
ConversationEvidenceRepository
```

真实持久化：

```text
conversation_id
evidence_id
created_at
```

必须通过真实 Repository 创建：

```text
Conversation
    +
Evidence
    ↓
ConversationEvidenceRepository.create_association()
```

不能：

```sql
INSERT INTO ai_ops.conversation_evidence ...
```

绕过 Repository。

---

# 九、OD-12：Turn-level Reference

这是 Step 46 的一个正式设计决策。

Step 44 已冻结：

```text
Conversation → Evidence = REFERENCE
Evidence → Conversation = REFERENCE
```

Step 45 已实现：

```text
conversation_evidence
```

当前没有：

```text
turn_id
assistant_request_id
```

Step 46 必须通过真实运行链路判断：

> 是否真的需要 Evidence ↔ ConversationTurn 的持久化关联？

---

# 十、OD-12 判定方法

不要先入为主。

通过真实 E2E 检查：

```text
Conversation
  ├── Turn A
  ├── Turn B
  └── Turn C
       ↓
      AI Result
       ↓
     Evidence
```

回答以下问题：

### Q1

只通过：

```text
conversation_id + evidence_id
```

能否唯一定位：

> 该 Evidence 属于哪个 Conversation？

如果 YES：

继续 Q2。

### Q2

系统是否需要回答：

> “这个 Evidence 是由哪一个具体 Turn 产生的？”

如果当前 Phase 4.1 已有真实产品需求或现有 API contract 要求：

```text
turn-level provenance
```

则：

**不要偷偷添加字段。**

先停止并报告：

```text
OD-12 requires schema/API decision.
```

不要继续扩展 DB。

### Q3

如果当前真实 E2E 只要求：

```text
Conversation
    ↔
Evidence
```

而不要求：

```text
Turn
    ↔
Evidence
```

则：

```text
OD-12 = DEFERRED
```

保持 Step 45 的：

```text
conversation_evidence
```

不增加：

```text
turn_id
assistant_request_id
```

---

# 十一、E2E Case 1：单 Conversation / 单 Evidence

创建：

```text
Conversation A
Turn A1
```

执行 AI Runtime。

产生一个确定性的 AI Result。

创建 Evidence。

创建：

```text
Conversation A ↔ Evidence A
```

然后 Read-back。

验证：

```text
Conversation A
    ↓
Evidence A
```

必须成立。

同时：

```text
Evidence A
```

必须仍然可以独立通过：

```text
EvidenceRepository.get_by_id()
```

读取。

---

# 十二、E2E Case 2：一个 Conversation 多个 Evidence

同一个：

```text
Conversation A
```

执行两个独立的 AI interactions：

```text
Turn A1 → Evidence A1
Turn A2 → Evidence A2
```

创建：

```text
Conversation A ↔ Evidence A1
Conversation A ↔ Evidence A2
```

验证：

```text
list_evidence_ids(conversation_a)
```

得到：

```text
[A1, A2]
```

并且：

```text
A1 != A2
```

不得因为 Conversation 相同而覆盖。

---

# 十三、E2E Case 3：Evidence Reuse

创建：

```text
Conversation A
Conversation B
Evidence X
```

关联：

```text
Conversation A ↔ Evidence X
Conversation B ↔ Evidence X
```

验证：

```text
list_conversation_ids(Evidence X)
```

得到：

```text
[A, B]
```

并且：

```text
Evidence X
```

仍然只有一个 Evidence record。

不得复制成：

```text
Evidence XA
Evidence XB
```

这验证 Step 44 已冻结的：

```text
Evidence = Reusable
```

---

# 十四、E2E Case 4：Conversation Isolation

创建：

```text
Conversation A
Conversation B

Evidence A
Evidence B
```

建立：

```text
A ↔ Evidence A
B ↔ Evidence B
```

验证：

```text
list_evidence_ids(A)
```

不能包含：

```text
Evidence B
```

并且：

```text
list_evidence_ids(B)
```

不能包含：

```text
Evidence A
```

必须验证：

```text
Cross-conversation leakage = 0
```

---

# 十五、E2E Case 5：Read-back

这是 Step 46 必须存在的测试。

不要只验证：

```python
repository.create_association(...)
```

成功。

必须：

```text
create
 ↓
commit
 ↓
new Repository / new Session
 ↓
read
```

验证 PostgreSQL 中真实存在：

```text
Conversation
Evidence
ConversationEvidence
```

并能够重新读取。

不要使用同一个 SQLAlchemy Session 的 identity map 作为唯一证明。

---

# 十六、E2E Case 6：AI Result Identity Boundary

验证：

```text
AI Result
```

中的：

```text
assistant_request_id
```

如果存在：

它只能作为：

```text
runtime correlation
```

不能直接成为：

```text
evidence_id
```

也不能写入：

```text
Evidence provenance
```

除非现有 frozen contract 已明确允许。

---

# 十七、E2E Case 7：Association Idempotency

同一个：

```text
conversation_id
evidence_id
```

重复创建：

```text
create_association(A, X)
create_association(A, X)
```

验证：

```text
只有一条 association
```

并且：

```text
no duplicate
```

复用 Step 45 的 first-write-wins / composite PK 语义。

不要修改 Repository。

---

# 十八、E2E Case 8：Invalid Association

尝试：

```text
Conversation 不存在
+
Evidence 存在
```

必须失败。

再测试：

```text
Conversation 存在
+
Evidence 不存在
```

必须失败。

验证：

```text
conversation_evidence
```

不会产生 orphan row。

---

# 十九、E2E Case 9：Parent Delete Semantics

确认 Step 45 的删除语义在真实 E2E 环境仍然成立。

删除：

```text
Conversation
```

结果：

```text
ConversationEvidence association → deleted
Evidence → remains
```

删除：

```text
Evidence
```

结果：

```text
ConversationEvidence association → deleted
Conversation → remains
```

不得出现：

```text
Conversation delete → Evidence delete
```

或者：

```text
Evidence delete → Conversation delete
```

---

# 二十、E2E Case 10：Runtime Route Boundary

Step 46 不重新测试 Router 的全部能力。

只验证：

Conversation 进入 AI Runtime 后：

```text
AIOrchestrator
```

产生的实际结果可以进入：

```text
Evidence persistence
```

并建立：

```text
ConversationEvidence
```

如果本次使用 Fake RAG：

验证：

```text
route == RAG
```

即可。

如果项目当前已有其他稳定 Fake Runtime：

可以使用当前已有测试 infrastructure。

禁止为了 Step 46 新增复杂 Fake Framework。

---

# 二十一、真实 PostgreSQL

Step 46 必须使用真实 PostgreSQL 做 persistence/read-back。

环境：

```text
RUN_DB_TESTS=1
```

测试数据库：

```text
PostgreSQL
ai_ops
```

不要使用：

```text
SQLite
mock repository
in-memory fake DB
```

来替代最终 persistence E2E。

---

# 二十二、测试数据

测试数据必须：

```text
test-only
```

禁止写入：

```text
真实 WMS 数据
生产数据库
开发业务数据
```

优先使用现有 DB fixture。

如果必须创建：

```text
Conversation
ConversationTurn
Evidence
```

使用唯一测试 ID。

测试结束：

```text
precise cleanup
```

禁止：

```text
TRUNCATE
DROP SCHEMA
DELETE entire table
```

---

# 二十三、DB Residue

Step 46 结束后检查：

```text
conversation
conversation_turn
evidence_record
evidence_annotation_record
conversation_evidence
```

测试创建的数据必须全部清理。

不能影响历史 baseline。

最终：

```text
Production DB writes = 0
Test DB residue = 0
```

如果项目现有 DB residue guard 有历史失败：

分类记录。

不要修改 baseline。

---

# 二十四、Security Boundary

验证：

```text
ConversationEvidenceReference
```

仍然是：

```text
frozen
```

不能暴露：

```text
Session
Connection
Engine
ORM instance
SQLAlchemy Result
```

Evidence read model 同样保持原有安全边界。

E2E Result / metadata 不得出现：

```text
API key
password
database URL
connection string
authorization header
```

---

# 二十五、禁止修改 DTO

如果：

```text
AIOrchestrationResult
Conversation
ConversationTurn
Evidence
Annotation
ConversationEvidenceReference
```

现有结构已经满足测试：

**不要修改。**

特别禁止为了 Step 46 添加：

```text
conversation_id
turn_id
evidence_id
```

到不该拥有它们的 DTO。

---

# 二十六、如果发现 Turn-level 缺口

如果真实 E2E 证明：

```text
conversation_id + evidence_id
```

无法满足当前业务所要求的：

```text
turn-level traceability
```

立即停止。

最终报告写：

```text
OD-12 BLOCKER

Observed requirement:
...

Current persistence:
conversation_id + evidence_id

Missing:
turn_id / equivalent reference

Action:
NO schema change in Step 46.
Decision required before implementation.
```

不要自行新增：

```text
turn_id
```

---

# 二十七、测试文件

优先新增：

```text
tests/test_conversation_evidence_e2e_db.py
```

如果当前项目已经存在更合适的 Conversation E2E 文件：

遵循现有结构。

不要重复创建多个类似 E2E 文件。

---

# 二十八、测试结构建议

建议按照：

```python
class TestConversationEvidenceE2E:
    ...
```

或者当前项目现有风格。

测试名称至少清晰表达：

```text
test_conversation_to_evidence_roundtrip
test_multiple_evidence_per_conversation
test_evidence_reuse_across_conversations
test_conversation_isolation
test_persistence_readback
test_association_idempotency
test_invalid_parent_reference_rejected
test_parent_delete_preserves_other_domain_object
test_runtime_result_identity_boundary
test_turn_level_reference_requirement
```

不要为了数量机械拆测试。

---

# 二十九、Fake Runtime 原则

如果当前已有：

```text
FakeLLM
FakeRAG
FakeTool
FakeRouter
```

优先复用。

如果没有：

不要创建一个新的大型 Fake Runtime framework。

允许增加：

```text
一个极小的测试 fixture
```

前提：

```text
只服务 Step 46
不进入 production
不改变真实 interface
```

---

# 三十、Real LLM

默认：

```text
Real LLM = SKIP
```

禁止让：

```powershell
python -m pytest -q
```

调用 DeepSeek。

如果项目已经存在：

```text
@pytest.mark.real_llm
```

则保持默认 skip。

不要新增真实 API key 配置。

---

# 三十一、测试顺序

先执行：

```powershell
python -m pytest -q tests/test_conversation_evidence_e2e_db.py
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_conversation_evidence_e2e_db.py
```

然后相关 persistence：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q `
  tests/test_conversation_evidence_persistence_db.py `
  tests/test_evidence_persistence_db.py `
  tests/test_evidence_provenance_persistence_db.py `
  tests/test_evidence_annotation_association_db.py `
  tests/test_conversation_evidence_e2e_db.py
```

最后：

```powershell
python -m pytest -q
```

以及：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

---

# 三十二、Compile / Matrix

执行：

```powershell
python -m compileall -q backend tests scripts
```

然后：

```powershell
python scripts/run_matrix_gate.py
```

如果 Matrix 因为当前 Git working tree 状态失败：

分类记录。

不要提前 commit。

不要修改 baseline。

---

# 三十三、Git

本阶段：

```text
NO COMMIT
NO PUSH
NO PR
NO MERGE
```

只检查：

```powershell
git status --short
git diff --stat
git diff
```

Step 50 才统一处理 Git closeout。

---

# 三十四、发现 Bug 的处理

如果测试发现：

```text
Conversation bug
Evidence bug
Repository bug
Orchestrator bug
```

先分类：

```text
Layer
Symptom
Root cause
Existing contract
Step 46 requirement
```

只有明确属于：

```text
existing implementation bug
```

且修复是：

```text
minimal
contract-preserving
```

才允许修改。

否则：

**STOP + REPORT。**

---

# 三十五、Step 46 完成判定

必须满足：

```text
Conversation → AI Runtime                  PASS
AI Result → Evidence                       PASS
Evidence → ConversationEvidence           PASS
PostgreSQL persistence                     PASS
New-session read-back                      PASS
Multiple Evidence                          PASS
Evidence reuse                             PASS
Conversation isolation                     PASS
Association idempotency                    PASS
Invalid parent rejection                   PASS
Delete semantics                           PASS
Identity boundary                          PASS
Security boundary                          PASS
OD-12 decision                             PASS / DEFERRED
DB residue                                 0
Production DB writes                       0
Real LLM default                           SKIP
```

---

# 三十六、Evaluation 文档

新增：

```text
docs/evaluation/Phase 4.1 Step 46 — Real Conversation Evidence E2E.md
```

记录：

## 1. Runtime Path

```text
Conversation
↓
ConversationTurn
↓
AIOrchestrator
↓
AI Result
↓
Evidence
↓
ConversationEvidence
↓
PostgreSQL
↓
Read-back
```

## 2. E2E Cases

记录：

```text
Case
Expected
Actual
Result
```

至少：

```text
single conversation
multiple evidence
evidence reuse
conversation isolation
read-back
idempotency
invalid parent
delete semantics
identity boundary
OD-12
```

## 3. Persistence

记录：

```text
Evidence ID
Conversation ID
Association
Read-back
```

不要记录：

```text
API Key
password
database URL
credentials
```

## 4. OD-12

明确写：

```text
TURN_LEVEL_REFERENCE = DEFERRED
```

或者：

```text
TURN_LEVEL_REFERENCE = BLOCKED
```

不得模糊描述。

## 5. Security

记录：

```text
Sensitive metadata leakage = 0
ORM/session leakage = 0
Unauthorized persistence = 0
```

---

# 三十七、最终报告格式

完成后严格：

```text
【Phase 4.1 Step 46 COMPLETE】

1. Runtime Path
2. 新增文件
3. 修改文件
4. Conversation → AI Runtime
5. AI Result → Evidence
6. ConversationEvidence Association
7. PostgreSQL Persistence
8. Read-back
9. Multiple Evidence
10. Evidence Reuse
11. Conversation Isolation
12. Idempotency
13. Delete Semantics
14. Identity Boundary
15. Security
16. OD-12
17. DB Tests
18. Full Regression
19. DB Regression
20. Matrix
21. Compile
22. DB residue
23. Production DB writes
24. Real LLM
25. API / DTO 是否修改
26. 发现的问题
27. 当前限制
```

最后必须输出：

```text
Architecture E2E:

Conversation
    ↓
ConversationTurn
    ↓
AIOrchestrator
    ↓
AI Result
    ↓
Evidence
    ↓
ConversationEvidence
    ↓
PostgreSQL
    ↓
Read-back
```

然后：

**立即停止。**

不要进入 Step 47。

不要开发 Agent。

不要开发 MCP。

不要开发 Memory。

不要开发 Workflow。

不要开发 Chat UI。

不要做 Git Push / PR / Merge。

Step 50 才统一进行 Git Closeout。
