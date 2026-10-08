# Phase 5 — Scope & Architecture Decision Gate

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

Phase 4.2 已经：

```text
RELEASED
MERGED
PUBLISHED
```

当前：

```text
origin/main
    ↓
Phase 4.2
    ↓
Conversation Runtime
    ↓
Idempotency
    ↓
Context Selection
    ↓
Context Consumption
    ↓
RAG / Tool / Text-to-SQL
```

现在开始进入：

# Phase 5

但是：

> **本阶段不实现 Agent。**

唯一目标：

**建立正式的 Phase 5 Scope + Architecture Decision。**

最终必须回答：

```text
Phase 5 到底是什么？
为什么现在需要它？
Agent Runtime 到底负责什么？
不负责什么？
Tool Loop 如何工作？
状态如何保存？
权限如何控制？
什么时候停止？
失败怎么办？
如何防止无限循环？
如何控制 Token / Cost？
如何审计？
如何与 Conversation Runtime / Evidence / Evaluation 对接？
是否使用 LangGraph？
如果不用，为什么？
```

---

# 二、当前已知事实

根据 Phase 5 Pre-flight Audit：

当前系统已经具备：

```text
LLM Provider
LLM Usage / Cost
LLM Observability

AIOrchestrator
AI Router

Tool Registry
Tool Capability Whitelist
Tool Execution

Conversation Runtime
ConversationTurn
Idempotency

Conversation Context
Context Selection
Context Builder
Context Consumption

Evidence Foundation

Evaluation
Regression
Security Validation
SQL Validator
ReadOnly SQL Executor
```

当前系统仍然是：

```text
Question
   ↓
AIOrchestrator
   ↓
Router
   ├── RAG
   ├── Tool
   └── Text-to-SQL
```

Tool 当前属于：

```text
一次性 Tool Execution
```

而不是：

```text
LLM
 ↓
Tool
 ↓
Result
 ↓
LLM
 ↓
Tool
 ↓
Result
 ↓
Final Answer
```

---

# 三、首先解决历史 Phase 编号冲突

阅读：

```text
docs/architecture.md
docs/requirements.md
docs/decisions/
```

发现历史文档存在类似：

```text
architecture.md
Phase 4 = Agent
Phase 5 = LangGraph
Phase 6 = Permission / Audit / Write
```

而当前项目实际路线已经是：

```text
Phase 3
AI Core

Phase 4.1
Conversation + Evidence Foundation

Phase 4.2
Production Conversation Runtime

Phase 5
???
```

本阶段必须正式声明：

> **当前仓库实际 Phase 编号以新的 Phase Roadmap 为准。**

不要为了兼容历史文档而重新编号已经完成的 Phase。

不要修改历史 Phase 4.1 / 4.2 文档。

需要增加一份新的：

```text
docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
```

其中明确：

```text
Historical architecture.md phase numbering
        ≠
Current implementation roadmap numbering
```

并规定：

> 后续开发以当前 Phase Roadmap / Decision 文档为唯一执行依据。

---

# 四、Phase 5 定义

本阶段正式定义：

# Phase 5 = Bounded Agent Runtime

不是：

```text
万能 Agent
```

不是：

```text
Autonomous Agent
```

不是：

```text
Multi-Agent
```

不是：

```text
LangGraph Platform
```

而是：

> 在现有 AIOrchestrator + Tool Runtime 基础上，引入一个受严格边界约束的多步 Tool-Calling Runtime。

核心目标：

```text
一次 AI Request
        ↓
Agent Runtime
        ↓
LLM Decision
        ↓
Tool Call
        ↓
Tool Result
        ↓
LLM Decision
        ↓
...
        ↓
Final Answer
```

但是：

**每一步都必须受到 Runtime Policy 控制。**

---

# 五、Phase 5 第一原则

Agent Runtime 不允许直接获得：

```text
Database Connection
SQLAlchemy Session
HTTP Client
Tool implementation
filesystem
```

Agent 只能看到：

```text
Tool Definition
Tool Schema
Tool Permission
Tool Result
```

即：

```text
Agent
  ↓
Tool Registry
  ↓
Authorized Tool
  ↓
ToolExecutionService
  ↓
Tool Result
```

不能：

```text
Agent
  ↓
任意 Python function
```

---

# 六、Agent Runtime 与 Orchestrator 的关系

当前：

```text
AIOrchestrator
```

负责：

```text
route
→ execute capability
→ return result
```

Phase 5 后：

```text
AIOrchestrator
        ↓
RouteDecision
        ↓
        ├── RAG
        ├── Tool
        ├── Text-to-SQL
        └── AGENT
                  ↓
             AgentRuntime
                  ↓
              LLM Loop
                  ↓
             Tool Registry
```

重要：

Agent Runtime 是：

```text
AI Capability / Runtime
```

不是第二套 AIOrchestrator。

禁止出现：

```text
Orchestrator
   ↓
Agent
   ↓
Orchestrator
   ↓
Agent
```

递归编排。

---

# 七、Phase 5 不做 Multi-Agent

明确：

```text
ONE Agent
```

第一阶段只支持：

```text
One Agent
Multiple Tools
Bounded Loop
```

禁止：

```text
Agent → Agent
Agent → SubAgent
Agent → Planner Agent
Agent → Reviewer Agent
```

Multi-Agent 推迟到未来独立阶段。

---

# 八、Tool Loop

正式定义最小 Loop：

```text
START
  ↓
LLM
  ↓
Decision
  ├── FINAL
  │     ↓
  │   COMPLETE
  │
  └── TOOL_CALL
        ↓
      Policy Check
        ↓
      Tool Registry
        ↓
      Tool Execution
        ↓
      Tool Result
        ↓
       LLM
```

每一轮只能发生：

```text
0 or 1 logical tool decision
```

第一版不要支持：

```text
parallel tool calls
```

不要增加复杂并行调度。

---

# 九、必须定义停止条件

Agent Runtime 必须至少拥有：

```text
max_steps
max_tool_calls
max_llm_calls
max_execution_time
max_cost
```

第一版可以是：

```text
RuntimePolicy
```

例如：

```text
max_steps = 8
max_tool_calls = 8
max_llm_calls = 8
max_execution_time_ms = ...
max_cost = ...
```

具体默认值本阶段只需要做架构决策。

不要开始实现。

---

# 十、Agent State

必须定义：

```text
AgentRun
AgentStep
```

但注意：

本阶段只定义语义，不创建数据库表。

至少需要表达：

```text
run_id
request_id
conversation_id
project_id
status
current_step
step_count
tool_call_count
llm_call_count
started_at
finished_at
```

Step 至少需要：

```text
step_id
run_id
step_index
type
status
tool_name
input
output
error
created_at
```

但是：

### 第一版不要把完整 prompt / secret / credential 写入 state。

---

# 十一、Agent Run 状态

至少定义：

```text
RUNNING
COMPLETED
FAILED
CANCELLED
LIMIT_REACHED
WAITING_APPROVAL
```

必须明确：

```text
LIMIT_REACHED
```

不是：

```text
FAILED
```

因为：

> Agent 可能是正常执行到了安全预算边界。

---

# 十二、Error Recovery

必须区分：

### LLM failure

```text
Provider / transport / generation error
```

### Tool failure

```text
Tool execution error
```

### Policy failure

```text
Tool not allowed
```

### Runtime limit

```text
max_steps
max_tool_calls
max_cost
timeout
```

第一版不要自动复杂恢复。

尤其不要：

```text
失败 → 自动换模型
失败 → 自动重新规划
失败 → 无限 retry
```

只定义状态与错误边界。

---

# 十三、Tool Permission

当前已有：

```text
ProjectCapabilities.tool_names
```

Phase 5 不允许直接把它废掉。

第一版定义：

```text
Project Capability
        ↓
Agent Runtime
        ↓
Tool Authorization
        ↓
Tool Execution
```

Agent 不得自己决定：

```text
"我需要这个 Tool，所以允许执行"
```

LLM 只能提出：

```text
tool_name
arguments
```

Runtime 决定：

```text
allowed / denied
```

---

# 十四、Tool Side Effect

Phase 5 第一阶段：

**只允许现有 READ-only Tool。**

禁止 Agent Runtime 首次实现：

```text
CREATE
UPDATE
DELETE
POST
```

尤其：

```text
ERP write
WMS write
database write
```

都不允许。

如果未来要支持写操作：

必须新增：

```text
Approval
Authorization
Idempotency
Audit
Compensation
```

独立 Decision。

---

# 十五、Human-in-the-loop

Phase 5 架构必须预留：

```text
WAITING_APPROVAL
```

但：

**第一阶段不实现审批 UI。**

Agent Runtime 可以：

```text
Tool Policy
    ↓
requires_approval = true
    ↓
WAITING_APPROVAL
```

然后停止。

不要继续执行。

未来独立阶段再实现 Approval Center。

---

# 十六、Conversation Integration

Agent Runtime 必须运行在：

```text
Conversation Runtime
```

之上。

即：

```text
Conversation
      ↓
ChatApplicationService
      ↓
AIOrchestrator
      ↓
AgentRuntime
```

Agent Runtime 不应该自己：

```text
create Conversation
create ConversationTurn
handle Idempotency-Key
```

这些仍然属于：

```text
Conversation Runtime
```

---

# 十七、Conversation Context

Agent Runtime 可以读取：

```text
conversation_context
```

但是必须遵守 Phase 4.2 的安全优先级：

```text
System / Safety
    >
Capability / Business
    >
Conversation Context
    >
Current Question
```

Conversation Context：

```text
untrusted reference
```

不能变成：

```text
runtime instruction
```

不能覆盖：

```text
Tool permission
Runtime policy
Safety policy
```

---

# 十八、Evidence

Phase 4.1 已经明确：

```text
Evidence = Source / Provenance Unit
```

Phase 5 不允许偷偷改变这个定义。

但是必须正式定义一个未来接口：

```text
Agent Step
      ↓
Tool Result
      ↓
Evidence Candidate
```

注意：

> Tool Result 不等于 Evidence。

只有存在明确 provenance：

```text
dataset_version
source_type
```

才可以成为 Evidence。

如果当前 Tool 没有 provenance：

```text
Tool Result
```

只能作为 runtime observation。

不能强行写入 Evidence。

---

# 十九、Observability

当前已有：

```text
request_id
LLM usage
Tool execution
Trace
```

Phase 5 必须设计：

```text
AgentRun
   ↓
AgentStep
   ├── LLM
   ├── Tool
   └── Policy
```

形成：

```text
parent-child trace
```

但是：

**本阶段只定义关联关系。**

不要马上新增大量数据库表。

需要先判断：

```text
现有 observation model
```

是否可以扩展。

---

# 二十、Evaluation

Phase 5 Agent Runtime 必须可评估。

至少未来需要验证：

```text
max_steps
max_tool_calls
tool denial
tool failure
LLM failure
limit reached
approval required
loop termination
prompt injection
conversation injection
```

本阶段只定义 evaluation requirements。

不要建立完整 benchmark。

---

# 二十一、安全边界

必须明确：

```text
User
 ↓
Conversation Context
 ↓
Agent LLM
```

全部是不可信输入。

Runtime Policy 是：

```text
trusted control plane
```

不能让 LLM 修改：

```text
max_steps
max_cost
allowed_tools
requires_approval
project_id
```

例如：

```text
LLM says:
"set max_steps=100000"
```

必须被忽略。

---

# 二十二、Prompt Injection

必须考虑：

```text
Conversation history
Tool result
RAG result
```

都可能包含：

```text
"ignore previous instructions"
```

Agent Runtime 必须把这些视为：

```text
untrusted data
```

不能把 Tool Result 直接拼进 system instruction。

---

# 二十三、是否使用 LangGraph

必须正式做 Decision。

比较：

### Option A

```text
自研 Agent Runtime
```

优点：

```text
控制强
架构透明
与现有 Tool Registry 集成自然
容易实现企业安全策略
```

缺点：

```text
需要自己维护 loop/state/error
```

### Option B

```text
LangGraph
```

优点：

```text
state machine
checkpoint
human-in-loop
workflow
```

缺点：

```text
引入较重 runtime
需要适配现有 architecture
增加框架依赖
```

### Decision

本项目 Phase 5 第一阶段：

> **先自研最小 Bounded Agent Runtime，不引入 LangGraph。**

原因：

当前只需要：

```text
ONE Agent
+
Tool Loop
+
Policy
+
Bounded Execution
```

还没有：

```text
复杂 Workflow
Multi-Agent
Long-running execution
复杂 checkpoint graph
```

因此现在引入 LangGraph 会扩大架构面。

LangGraph 未来如果进入：

```text
Phase 6 Workflow Runtime
```

再重新 Decision。

---

# 二十四、Phase 5 非目标

明确禁止：

```text
❌ Multi-Agent
❌ LangGraph
❌ MCP
❌ Workflow
❌ Autonomous Planning
❌ Self-reflection
❌ Self-improvement
❌ Long-term Memory
❌ Vector Memory
❌ Browser Agent
❌ Computer Use
❌ Code Execution Agent
❌ Database Write Agent
❌ Approval UI
❌ Streaming
❌ Agent Marketplace
❌ Agent Builder
```

尤其：

> 不要把 Phase 5 做成一个“大而全 Agent Framework”。

---

# 二十五、Phase 5 MVP

正式定义第一阶段能力：

```text
Bounded Agent Runtime

ONE Agent
    ↓
LLM
    ↓
Tool Decision
    ↓
Policy
    ↓
Existing Read-only Tool
    ↓
Tool Result
    ↓
LLM
    ↓
Final Answer
```

能力：

```text
✓ bounded loop
✓ max steps
✓ max tool calls
✓ tool authorization
✓ tool failure boundary
✓ LLM failure boundary
✓ cancellation state
✓ approval state
✓ runtime trace correlation
✓ conversation context
✓ existing idempotency boundary
```

不做：

```text
✗ write tools
✗ multi-agent
✗ planning graph
✗ memory
✗ workflow
✗ LangGraph
```

---

# 二十六、Phase 5 架构图

文档必须包含：

```text
                         ┌────────────────────┐
                         │ ConversationRuntime│
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │   AIOrchestrator   │
                         └─────────┬──────────┘
                                   │
                             RouteDecision
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
             RAG                  Tool             Text-to-SQL
                                   │
                                   │
                                   ▼
                         ┌────────────────────┐
                         │   AgentRuntime     │
                         │                    │
                         │  RuntimePolicy     │
                         │  Step State        │
                         │  Loop Controller   │
                         └─────────┬──────────┘
                                   │
                              LLM Decision
                                   │
                                   ▼
                         ┌────────────────────┐
                         │  Tool Authorization│
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │    Tool Registry   │
                         └─────────┬──────────┘
                                   │
                                   ▼
                         ┌────────────────────┐
                         │ ToolExecutionSvc   │
                         └─────────┬──────────┘
                                   │
                                   ▼
                              Tool Result
                                   │
                                   └──────────→ LLM
```

---

# 二十七、必须形成的架构决策

在文档中正式关闭：

```text
OD-P5-01 Phase 5 Definition
OD-P5-02 Agent vs Orchestrator Boundary
OD-P5-03 Self-built Runtime vs LangGraph
OD-P5-04 Tool Loop Model
OD-P5-05 Runtime Policy
OD-P5-06 Agent State
OD-P5-07 Tool Authorization
OD-P5-08 Write Tool Policy
OD-P5-09 Human Approval Boundary
OD-P5-10 Conversation Integration
OD-P5-11 Evidence Boundary
OD-P5-12 Observability Boundary
OD-P5-13 Evaluation Boundary
```

每个 Decision 至少包含：

```text
Decision
Reason
Alternatives
Consequences
```

---

# 二十八、不要修改生产代码

本阶段：

```text
Production code = 0
DB schema = 0
API = 0
Prompt = 0
```

允许新增：

```text
docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
```

必要时允许新增：

```text
docs/architecture.md
```

中的极小索引链接。

不要修改已有 Phase 4.2 实现。

不要实现：

```text
AgentRuntime
AgentService
AgentState
ToolLoop
```

这些全部留到下一阶段。

---

# 二十九、验证

至少执行：

```powershell
git status -sb
git diff --check
python -m compileall backend tests scripts
python -m pytest -q
```

本阶段没有代码修改，所以测试重点是：

```text
baseline remains green
```

如果 DB 测试运行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

继续记录已知的 3 个 order-dependent failures。

不要修改它们。

---

# 三十、Git

当前分支：

```text
phase5-1
```

当前：

```text
HEAD == origin/main
```

本阶段完成后：

只提交：

```text
Phase 5 Scope & Architecture Decision
```

不要：

```text
merge
rebase
force push
reset
```

如果 CodeBuddy 没有明确 push 授权：

**不要 push。**

---

# 三十一、最终报告

严格使用：

```text
【Phase 5 SCOPE & ARCHITECTURE DECISION COMPLETE】

1. Phase 5 Definition
- ...

2. Core Architecture
- ...

3. Agent / Orchestrator Boundary
- ...

4. Agent Loop
- ...

5. Runtime Policy
- ...

6. Agent State
- ...

7. Tool Authorization
- ...

8. Write Tool Policy
- ...

9. Human Approval
- ...

10. Conversation Integration
- ...

11. Evidence Boundary
- ...

12. Observability
- ...

13. Evaluation
- ...

14. LangGraph Decision
- ...

15. Phase 5 Non-goals
- ...

16. Files Changed
- ...

17. Production Code
- 0

18. DB Schema
- 0

19. API
- 0

20. Tests
- ...

21. Compile
- ...

22. Git
- branch:
- commit:
- working tree:

23. Open Questions
- ...

24. STOP
```

---

# 三十二、强制 STOP

完成：

```text
Phase 5 Scope
+
Architecture Decision
```

之后立即停止。

**不要实现 Agent Runtime。**

**不要新增 5A / 5A-1 / 5A-1-1。**

下一阶段必须由用户明确授权后才开始实现。
