# Phase 6 Step 2 — Workflow Persistence Implementation

## 一、前置状态

当前架构状态：

```text
Phase 5 Agent Runtime
    = FROZEN

Phase 6 ADR
    = ACCEPTED

Phase 6 Step 1 Domain
    = COMPLETE
    = 186 passed / 0 failed

Phase 6.0 Architecture Discovery
    = FROZEN
    = 12 个 Deferred Decisions
    = Architecture Gate PASS

当前进入：

Phase 6 Step 2 = Persistence
```

Phase 6.0 已明确冻结：

```text
Workflow Persistence
    = Option A
    = 独立 3 表

ai_ops.workflow_run
ai_ops.workflow_node_run
ai_ops.workflow_approval
```

Workflow Checkpoint：

```text
存在 Recovery Control State 概念
但不建立 workflow_checkpoint 独立表

Checkpoint State
    → 承载于 workflow_run
    → state_version CAS
```

---

# 二、本 Step 唯一目标

将 Phase 6 Step 1 的纯 Domain 契约落地为：

```text
Domain
  ↓
ORM Models
  ↓
Repository
  ↓
Idempotent DDL
  ↓
Real PostgreSQL
  ↓
DB Integration Tests
```

本 Step **不实现 Workflow Runtime**。

---

# 三、必须先阅读

开始编码前必须先阅读真实代码：

```text
backend/app/workflow/
backend/app/db/
backend/app/db/models/
backend/app/db/repositories/
backend/app/db/
backend/app/services/
tests/
```

重点检查现有 Agent Persistence 实现：

```text
AgentRun
AgentStep
AgentDecision
AgentCheckpoint
AgentToolResult
AgentApproval
```

以及：

```text
对应 ORM
Repository
DDL / init_db
DB fixtures
DB tests
cleanup / residue 检查
```

同时阅读：

```text
docs/decisions/Phase/Phase 6 — Workflow Runtime Architecture Decision.md
docs/decisions/Phase/Phase 6 — Workflow Runtime Architecture Discovery.md
```

**不要假设现有接口。**

必须优先复用项目已有 Persistence / Repository / DB Test 基础设施。

---

# 四、严格范围

## 允许

新增：

```text
Workflow ORM Models
Workflow Repositories
Workflow DB fixtures
Workflow Persistence tests
必要的幂等 DDL
```

允许修改：

```text
init_db / DB bootstrap
```

但只能为了注册 Workflow persistence。

---

## 禁止

本 Step 禁止修改：

```text
Workflow Runtime
Workflow Engine
Workflow Executor
Workflow Application Service
Workflow API
Workflow Read API
Scheduler
Worker
Background Job
Temporal
LangGraph
MCP
Agent Runtime
AI Router
AIOrchestrator
ToolExecutionService
ToolDefinition
Conversation
Evidence
```

禁止：

```text
workflow_checkpoint 表
generic execution 表
复用 AgentRun / AgentStep / AgentCheckpoint
```

禁止：

```text
生产数据库写入
```

---

# 五、Persistence 三表

必须实现：

```text
ai_ops.workflow_run
ai_ops.workflow_node_run
ai_ops.workflow_approval
```

---

# 六、workflow_run

表示：

> 一个 Workflow Definition 的一次执行实例。

至少必须持久化：

```text
id / workflow_run_id
workflow_id
workflow_version
request_id
project_id

state
state_version

current_node_id
next_action

created_at
started_at
completed_at
updated_at
```

如果现有项目已有统一：

```text
UUID
timestamp
request_id
```

类型和命名规范：

**优先对齐现有项目。**

不要为了 Workflow 创建第二套基础类型。

---

# 七、Workflow Run Identity

必须保证：

```text
workflow_id
workflow_version
workflow_run_id
request_id
project_id
```

语义严格区分。

特别注意：

```text
workflow_run_id
≠
request_id
```

其中：

```text
workflow_run_id
```

是 Workflow Execution Identity。

而：

```text
request_id
```

是调用 / correlation identity。

同一个 request 未来可能产生不同 execution。

不要用 request_id 替代 workflow_run_id。

---

# 八、workflow_version

必须持久化。

原因：

```text
Run 创建：
workflow v1
    ↓
代码发布
    ↓
current = v2
    ↓
旧 Run resume
```

必须仍然使用：

```text
v1
```

不得：

```text
resume → resolve_current()
```

Persistence 层必须能够完整保存：

```text
workflow_id
workflow_version
```

不得只保存：

```text
workflow_id
```

---

# 九、project_id

必须：

```text
NOT NULL
```

Workflow Persistence 不允许：

```text
project_id = NULL
```

不要从客户端信任 project_id。

本 Step 只负责持久化字段和约束。

ProjectContext 的服务器端解析属于后续 Application / Runtime 层。

---

# 十、state_version CAS

这是本 Step 的核心一致性要求之一。

`workflow_run` 必须存在：

```text
state_version
```

建议初始：

```text
0
```

每次 Workflow Control State 成功更新：

```text
state_version + 1
```

必须支持：

```text
expected_state_version
```

的 CAS 更新语义。

例如：

```sql
UPDATE ai_ops.workflow_run
SET
    state = :new_state,
    state_version = state_version + 1,
    updated_at = :now
WHERE
    workflow_run_id = :run_id
    AND state_version = :expected_version;
```

然后：

```text
affected_rows == 1
    → success

affected_rows == 0
    → concurrency conflict
```

注意：

本 Step 只实现 Repository 层的 persistence primitive。

不要在本 Step 实现 Workflow Runtime transition。

---

# 十一、Checkpoint

禁止创建：

```text
workflow_checkpoint
```

表。

Workflow checkpoint state：

```text
current_node_id
next_action
state
state_version
pending information
cancellation signal
attempt progression
```

按照 Phase 6.0 决策：

```text
承载在 workflow_run
```

Repository 应能持久化这些控制状态。

不要复制 AgentCheckpoint：

```text
AgentCheckpoint.claim_token
AgentCheckpoint.pending_tool_call
AgentCheckpoint.resume semantics
```

Workflow 与 Agent checkpoint 语义完全不同。

---

# 十二、workflow_node_run

表示：

> Workflow 中一个 Node 的一次 attempt。

必须支持：

```text
workflow_run_id
node_id
attempt
node_type
state
```

以及必要的：

```text
started_at
completed_at
created_at
updated_at

input / output
error_code
error_message
```

具体字段必须根据现有 Domain DTO 和项目 ORM 规范确定。

---

# 十三、Attempt 语义

必须保证：

```text
node A attempt 1
    ↓
FAILED

retry

node A attempt 2
    ↓
SUCCEEDED
```

数据库必须保留：

```text
attempt 1
attempt 2
```

不得：

```text
UPDATE attempt 1 → SUCCEEDED
```

---

# 十四、Node Identity Constraint

必须建立稳定唯一约束。

核心身份：

```text
workflow_run_id
node_id
attempt
```

必须唯一。

例如：

```text
UNIQUE (
    workflow_run_id,
    node_id,
    attempt
)
```

确保不会产生两个：

```text
same run
same node
same attempt
```

---

# 十五、workflow_approval

表示：

> Workflow Approval Gate 的持久化状态。

必须与：

```text
Agent Tool Approval
```

完全分离。

至少包含：

```text
approval_id
workflow_run_id
node_id
status
resolved_at
```

根据现有审计规范可增加：

```text
created_at
updated_at
resolved_by
resolution_reason
```

但不要凭空增加复杂字段。

优先依据现有项目的 Approval / audit pattern。

---

# 十六、Approval 状态

必须严格：

```text
PENDING
   ├── APPROVED
   └── REJECTED
```

终态不可逆。

必须保证：

```text
PENDING
```

不能有：

```text
resolved_at
```

而：

```text
APPROVED / REJECTED
```

必须有：

```text
resolved_at
```

如果数据库层无法表达全部业务约束，则至少通过：

```text
Repository
+
DB tests
```

锁定。

---

# 十七、Approval Single Winner

Resolve Approval 必须具备：

```text
conditional UPDATE
```

保证：

```text
PENDING → APPROVED
```

和：

```text
PENDING → REJECTED
```

不会同时成功。

必须验证：

```text
first resolver → success
second resolver → conflict / no-op
```

不要：

```text
SELECT
↓
判断 PENDING
↓
UPDATE
```

形成明显 TOCTOU race。

优先：

```text
UPDATE ...
WHERE status = 'PENDING'
```

---

# 十八、Foreign Keys

如果项目现有 DB 架构允许：

建立合理 FK：

```text
workflow_node_run.workflow_run_id
    → workflow_run.workflow_run_id
```

```text
workflow_approval.workflow_run_id
    → workflow_run.workflow_run_id
```

是否建立其他 FK：

根据现有 Agent 表规范决定。

不要为了“理论完整”大量增加跨模块 FK。

---

# 十九、Index

至少考虑：

```text
workflow_run:
    project_id
    request_id
    state
    created_at

workflow_node_run:
    workflow_run_id
    workflow_run_id + node_id
    state

workflow_approval:
    workflow_run_id
    status
```

具体索引必须根据现有数据库规范和实际 Query Pattern 决定。

不要无意义堆索引。

---

# 二十、Repository

Repository 必须保持：

```text
Persistence only
```

不要把：

```text
Workflow transition logic
Retry logic
Node execution
Agent execution
Tool execution
```

放入 Repository。

允许提供类似：

```text
create_workflow_run
get_workflow_run
update_workflow_run_cas
create_node_run
get_node_runs
get_latest_node_attempt
create_approval
resolve_approval
```

实际命名：

**对齐现有 Agent Repository 风格。**

---

# 二十一、事务边界

检查现有项目 Repository / Session pattern。

不要创建新的 DB abstraction。

重点确保：

```text
WorkflowRun create
NodeRun create
Approval resolve
CAS update
```

符合现有：

```text
Session
transaction
commit
rollback
```

规范。

不要在 Repository 中隐式启动复杂业务事务。

---

# 二十二、DDL / init_db

项目没有 Alembic 时：

继续使用现有：

```text
Base.metadata.create_all
+
现有幂等 DDL
```

必须满足：

```text
第一次初始化 → 成功
第二次初始化 → 成功
```

不得：

```text
DROP TABLE
```

不得：

```text
DROP SCHEMA
```

不得：

```text
破坏已有 Agent 表
```

---

# 二十三、DB Tests

新增 Workflow Persistence DB tests。

必须使用：

```text
RUN_DB_TESTS=1
```

门控。

PowerShell：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest ...
```

---

# 二十四、DB 测试必须覆盖

至少：

### 1. WorkflowRun CRUD

```text
create
read
update
```

---

### 2. Workflow version persistence

验证：

```text
v1
```

不会丢失。

---

### 3. Project isolation

验证：

```text
project-A
project-B
```

数据不会错误交叉。

---

### 4. state_version CAS

测试：

```text
version = 0

CAS(expected=0)
→ success
version = 1

CAS(expected=0)
→ conflict
```

---

### 5. Node attempt

测试：

```text
attempt=1
attempt=2
```

都可以存在。

重复：

```text
same run + node + attempt
```

必须失败。

---

### 6. Approval lifecycle

测试：

```text
PENDING
→ APPROVED
```

以及：

```text
PENDING
→ REJECTED
```

---

### 7. Approval race

模拟：

```text
resolver A
resolver B
```

只有一个成功。

---

### 8. Approval invalid state

验证：

```text
PENDING + resolved_at
```

被拒绝。

---

### 9. FK / integrity

删除或非法引用时：

```text
DB integrity error
```

必须符合设计。

---

### 10. Init idempotency

执行：

```text
init_db()
init_db()
```

第二次必须成功。

---

### 11. Residue

测试结束必须：

```text
Workflow rows = 0
```

不得污染其他测试。

---

# 二十五、Security / Isolation

Persistence 层不得存储：

```text
API Key
Password
Authorization Header
Database URL
Connection String
SQLAlchemy Session
Connection Object
```

`input/output/error metadata` 如果存在：

必须经过：

```text
JSON-safe boundary
```

不能直接 dump Python Object。

---

# 二十六、不要复制 Agent Persistence

重点比较：

```text
AgentRun
AgentStep
AgentDecision
AgentCheckpoint
AgentToolResult
AgentApproval
```

Workflow Persistence 可以参考其：

```text
命名
Repository
Session
测试
```

但是：

**不得共享语义模型。**

最终必须保持：

```text
WorkflowRun ≠ AgentRun
WorkflowNodeRun ≠ AgentStep
WorkflowCheckpoint ≠ AgentCheckpoint
WorkflowApproval ≠ AgentApproval
```

---

# 二十七、测试命令

首先：

```powershell
python -m pytest -q tests/test_workflow_*.py
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_workflow_*_db.py
```

然后完整：

```powershell
python -m pytest -q
```

如果 DB 测试是全量门控：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

同时：

```text
compileall
git diff --check
```

如果项目已有 lint / LSP 检查：

一并执行。

---

# 二十八、成功标准

必须达到：

```text
Workflow Persistence tests
= PASS

DB tests
= PASS

Full offline tests
= PASS

Full DB tests
= PASS

compileall
= PASS

git diff --check
= PASS
```

且：

```text
Agent tests
= unchanged

Agent Runtime
= unchanged

AIOrchestrator
= unchanged

AI Router
= unchanged

ToolExecutionService
= unchanged
```

---

# 二十九、Git

本 Step 开始前：

```text
git status --short
```

记录 baseline。

完成后：

```text
git diff --check
git diff --stat
git status --short
```

不要：

```text
push
PR
merge
rebase
```

默认：

**不要自动 commit。**

完成后等待确认。

---

# 三十、最终报告

严格使用：

```text
【Phase 6 Step 2 COMPLETE】

1. 新增文件
2. 修改文件
3. workflow_run
4. workflow_node_run
5. workflow_approval
6. Repository
7. DDL / init_db
8. CAS
9. Approval concurrency
10. DB Tests
11. Full Tests
12. Boundary Audit
13. Agent Regression
14. DB writes
15. Git Status
16. Step 3 Readiness

STOP.
```

最后必须明确：

```text
Phase 6 Step 2 = COMPLETE
Phase 6 Step 3 = NOT STARTED
```

**立即停止。**

不要进入 Runtime。

不要实现 WorkflowEngine。

不要实现 Scheduler。

不要实现 API。

不要进入 Phase 6.3。

不要安装 Temporal / LangGraph / MCP。
