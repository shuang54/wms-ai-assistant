# Phase 5 Exit Audit — Agent Runtime 架构收口与 Phase 6 边界冻结

## 一、阶段目标

Phase 5 P-7 / P-8 已完成：

```text
AgentRun
AgentStep
AgentDecision
AgentToolResult
AgentCheckpoint
AgentApproval
Resume
Tool Replay
HITL
Cancellation
Agent Read API
Agent Timeline
```

本阶段不再增加 Agent Runtime 功能。

唯一目标：

> 对 Phase 5 做最终 Architecture Exit Audit，确认 Agent Runtime 是否达到冻结条件，并明确 Phase 6 Workflow Runtime 的边界。

---

# 二、核心原则

本阶段：

```text
Audit
→ Identify
→ Classify
→ Minimal Fix if required
→ Regression
→ Architecture Freeze
→ STOP
```

禁止：

```text
Audit
→ 顺手开发新功能
→ 再增加 Agent 能力
→ 再增加 P-10/P-11/P-12
```

---

# 三、开始前必须阅读

阅读：

```text
backend/app/agent/
backend/app/services/ai_orchestrator_service.py
backend/app/services/agent_read_service.py
backend/app/api/agent_read.py

backend/app/db/models/agent_run.py
backend/app/db/models/agent_step.py
backend/app/db/models/agent_decision.py
backend/app/db/models/agent_tool_result.py
backend/app/db/models/agent_checkpoint.py
backend/app/db/models/agent_approval.py

backend/app/db/*agent*_repository.py

tests/
docs/architecture.md
docs/decisions/Phase/
```

同时检查：

```powershell
git log --oneline --decorate -30
git status --short
```

---

# 四、Phase 5 最终能力清单

逐项确认：

```text
1. Agent Decision
2. Tool Authorization
3. Tool Execution
4. Tool Result Persistence
5. AgentRun
6. AgentStep
7. AgentDecision
8. AgentCheckpoint
9. Tool Replay
10. Resume
11. HITL Approval
12. Cooperative Cancellation
13. Agent Read API
14. Agent Timeline
```

每项输出：

```text
READY
PARTIAL
BLOCKED
DEFERRED
```

不得使用模糊描述。

---

# 五、必须建立 Architecture Matrix

新增：

```text
docs/decisions/Phase/Phase 5 — Agent Runtime Exit Audit.md
```

建立：

| Capability         | Phase 5 State | Source of Truth | Runtime Owner | Persistence | Deferred |
| ------------------ | ------------- | --------------- | ------------- | ----------- | -------- |
| Agent Loop         |               |                 |               |             |          |
| Tool Call          |               |                 |               |             |          |
| Tool Authorization |               |                 |               |             |          |
| Tool Result        |               |                 |               |             |          |
| Replay             |               |                 |               |             |          |
| Resume             |               |                 |               |             |          |
| HITL               |               |                 |               |             |          |
| Cancellation       |               |                 |               |             |          |
| Read API           |               |                 |               |             |          |
| Timeline           |               |                 |               |             |          |

必须根据真实代码填写。

---

# 六、Source of Truth Audit

明确：

```text
AgentRun
    =
Run Lifecycle

AgentStep
    =
Step Projection

AgentDecision
    =
Decision Replay Source

AgentToolResult
    =
Tool Result Replay Source

AgentCheckpoint
    =
Recovery Control State

AgentApproval
    =
Human Approval State

Conversation
    =
Conversation State

Evidence
    =
Source / Provenance
```

禁止出现：

```text
Timeline = Source of Truth
Read API = Source of Truth
AgentRuntime memory = durable Source of Truth
Conversation = Agent checkpoint
Evidence = Agent execution log
```

---

# 七、HITL Project Ownership OQ

重点审计 P-8 暴露的问题：

```text
PAUSED HITL
+
没有 Tool Result
+
agent_decision / checkpoint / approval 没有 project_id
```

确认：

```text
Read API
→ 是否能够安全判断 Project Ownership
```

当前行为：

```text
无法证明归属
→ 404
→ fail-closed
```

首先判断：

### A

这是合理的 Phase 5 安全边界：

```text
保持 404
→ 明确记录为 Deferred
```

或者：

### B

这是 Phase 5 必须修复的 Project Ownership 缺口。

如果选择 B：

只允许最小方案。

例如：

```text
AgentCheckpoint 增加 project_id
```

或其他能够与当前 Source of Truth 一致的最小方案。

禁止：

```text
新增 User
新增 RBAC
新增 Auth Framework
新增 Project Permission Framework
```

这些属于 Phase 7。

如果无法证明哪个方案正确：

**不要修改。**

记录：

```text
DEFERRED
```

---

# 八、Timeline Audit

确认 Timeline 是：

```text
Read Projection
```

而不是：

```text
Event Store
```

必须保持：

```text
NO timeline table
NO event_store
NO event sourcing
```

---

# 九、Timeline 事实完整性

逐项检查：

```text
RUN_STARTED
DECISION_RECORDED
APPROVAL_REQUIRED
APPROVAL_RESOLVED
TOOL_EXECUTED
STEP_COMPLETED
RUN_COMPLETED
RUN_FAILED
RUN_CANCELLED
RUN_LIMIT_REACHED
```

确认每一个事件都有：

```text
真实持久化事实
```

不能通过推测制造：

```text
STEP_STARTED
LLM_REQUEST_STARTED
LLM_REQUEST_FINISHED
```

如果没有真实时间事实：

```text
继续不生成
```

---

# 十、Timeline Ordering

确认：

```text
occurred_at
event_rank
step_index
event_type
```

形成 deterministic ordering。

必须确认：

```text
相同输入
→ 相同 Timeline
```

即使 Repository：

```text
正序
逆序
```

返回，也必须得到相同结果。

---

# 十一、Sequence 语义

确认：

```text
sequence
```

只是：

```text
projection index
```

不是：

```text
event_id
```

不是：

```text
database primary key
```

不是：

```text
global ordering id
```

文档必须明确。

---

# 十二、Pagination Limit

确认：

```text
1 <= limit <= 100
offset >= 0
```

Read API：

```text
server-side cap
```

不得：

```text
limit → SQL unrestricted
```

---

# 十三、Read API Mutation Audit

对：

```text
backend/app/api/agent_read.py
backend/app/services/agent_read_service.py
```

进行 AST / source audit。

禁止出现：

```text
create
insert
update
delete
execute
resume
approve
cancel
LLM call
Tool call
Runtime execution
```

Read API 必须：

```text
DB Read
→ DTO
→ Response
```

---

# 十四、Project Isolation Audit

验证：

```text
project A
    ↓
A Run = visible
B Run = invisible
```

以及：

```text
run.project_id is NULL
```

时：

```text
fallback / fail-closed
```

必须符合 P-8 当前真实实现。

不要为了方便放宽隔离。

---

# 十五、Security Audit

递归扫描所有 Read DTO / API Response。

禁止：

```text
claim_token
tool arguments
prompt
system prompt
messages
context
RAG chunks
SQL
database_url
api_key
authorization
password
raw_response
exception body
stack trace
headers
tool result payload
tool result ref
decision content
```

确认：

```text
DTO
+
mapper
+
response_model
```

三层边界都存在。

---

# 十六、Agent Runtime Boundary

确认 Agent Runtime：

```text
DOES
```

```text
LLM decision
Tool authorization
Tool execution orchestration
Loop control
Replay
Resume
HITL
Cancellation
```

Agent Runtime：

```text
DOES NOT
```

```text
Conversation ownership
DB direct access
HTTP API
Evidence
Project RBAC
Workflow definition
Multi-Agent orchestration
```

---

# 十七、Phase 5 与 Phase 6 边界

最终必须冻结：

```text
Phase 5
=
Bounded Agent Runtime
```

核心：

```text
LLM decides
    ↓
Tool
    ↓
LLM decides
    ↓
Tool
    ↓
FINAL
```

而：

```text
Phase 6
=
Workflow Runtime
```

负责：

```text
deterministic business flow
branch
condition
approval gate
parallelism
retry policy
scheduled execution
long-running workflow
```

不要把 Workflow 能力塞进 Agent Runtime。

现代 Agent 架构也通常把“模型驱动的 Agent Loop”和“开发者定义的 Workflow”作为不同控制流模式。

---

# 十八、禁止事项

本阶段禁止：

```text
❌ Agent UI
❌ Streaming
❌ SSE
❌ WebSocket
❌ Background Agent
❌ Workflow Runtime
❌ Multi-Agent
❌ LangGraph
❌ MCP
❌ A2A
❌ Memory
❌ RBAC
❌ User Permission
❌ Artifact Store
❌ Event Store
❌ Metrics Platform
❌ OpenTelemetry
❌ Kafka
❌ Redis
```

---

# 十九、只允许最小修复

如果 Audit 发现真正的 Phase 5 blocker：

允许：

```text
最小代码修复
+
最小测试
+
架构文档更新
```

禁止：

```text
顺便重构
顺便优化
顺便抽象
顺便新增能力
```

---

# 二十、Regression

必须运行：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

Compile：

```powershell
python -m compileall backend tests scripts
```

Git：

```powershell
git diff --check
git status --short
git diff --stat
```

---

# 二十一、Phase 5 Regression Gate

必须明确记录：

```text
P-6 Resume        PASS
P-7 HITL          PASS
P-7 Cancellation  PASS
P-8 Read API      PASS
P-8 Timeline      PASS
Tool Replay       PASS
Decision Replay   PASS
Project Isolation PASS
Security Boundary PASS
```

如果任何一项失败：

```text
Phase 5 = NOT READY
```

不要进入 Phase 6。

---

# 二十二、最终 Architecture Decision

如果全部通过：

新增：

```text
docs/decisions/Phase/Phase 5 — Agent Runtime Architecture Frozen.md
```

内容明确：

```text
Phase 5 Agent Runtime = FROZEN
```

冻结：

```text
Agent Loop
Decision Contract
Tool Authorization
Tool Execution boundary
AgentRun
AgentStep
AgentDecision
AgentToolResult
AgentCheckpoint
AgentApproval
Resume
Replay
Cancellation
Read API
Timeline
```

未来如果需要修改：

必须新建 Architecture Decision。

不能直接偷偷修改。

---

# 二十三、Commit

如果有最小修复：

```powershell
git add backend tests docs
git commit -m "chore: Phase 5 agent runtime exit audit"
```

如果完全没有代码修改：

可以只提交：

```text
Architecture Exit Audit
Architecture Frozen
```

不要 push。

不要 PR。

不要 merge。

---

# 二十四、最终报告

严格输出：

```text
【Phase 5 ARCHITECTURE FROZEN】

1. Phase 5 Capability Matrix
2. Source of Truth Matrix
3. HITL Ownership OQ
4. Cancellation Audit
5. Replay / Resume Audit
6. Read API Audit
7. Timeline Audit
8. Security Audit
9. Project Isolation
10. Regression
11. DB Regression
12. compile / lint
13. Git Diff
14. Minimal Fixes
15. Deferred Items
16. Phase 6 Boundary
17. Architecture State
18. Commit
```

最终状态：

```text
AgentRun                 FROZEN
AgentStep                FROZEN
AgentDecision            FROZEN
AgentToolResult          FROZEN
AgentCheckpoint          FROZEN
AgentApproval            FROZEN
Resume                   FROZEN
Replay                   FROZEN
HITL                     FROZEN
Cancellation             FROZEN
Agent Read API           FROZEN
Agent Timeline           FROZEN
```

---

# 二十五、强制 STOP

完成后：

```text
STOP
```

不要：

```text
进入 Agent UI
进入 Streaming
进入 Workflow Runtime
进入 Multi-Agent
进入 LangGraph
进入 MCP
```

Phase 5 完成后，下一阶段必须重新进行 Architecture Decision。

**不要自动开始 Phase 6。**
