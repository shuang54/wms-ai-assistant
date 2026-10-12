你现在开始执行：

# Phase 5 — Agent Runtime Integration Architecture Decision

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、当前状态

Phase 5 当前已经完成：

```text
Phase 5 Scope & Architecture Decision
        ↓
Agent Runtime Core Loop
        ↓
OQ-P5-1 LLM Tool Decision Protocol
        ↓
Agent LLM Adapter
```

当前已经存在：

```text
AgentRuntime
AgentRuntimePolicy
AgentDecision
AgentStep
AgentRun Result
ProviderAgentLLM
LLMProvider.chat(messages, tools=...)
ToolExecutionService
Tool Registry
Project Capability Whitelist
```

当前已经验证：

```text
AgentRuntime
    ↓
AgentLLM
    ↓
LLMProvider.chat()
    ↓
LLMResponse
    ↓
AgentDecision
    ↓
ToolExecutionService
    ↓
Tool Result
    ↓
LLM
```

但是：

```text
Router
Orchestrator
Conversation Runtime
Conversation Context
AIOrchestrationResult
Assistant Turn
Idempotency
Observability
```

仍然没有与 Agent Runtime 接线。

---

# 二、本阶段唯一目标

本阶段：

**只做 Architecture Decision。**

不要实现 Router。

不要修改 Orchestrator。

不要修改 Conversation Runtime。

不要新增数据库。

不要写 Agent integration production code。

本阶段需要一次性冻结：

```text
Agent
    ↕
Router
    ↕
AIOrchestrator
    ↕
Conversation Runtime
```

之间的责任边界。

最终形成一个明确的：

```text
Agent Integration Contract
```

供下一阶段直接实现。

---

# 三、开始前必须阅读真实代码

先阅读，不要修改：

```text
backend/app/agent/
backend/app/llm/
backend/app/services/ai_router_service.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/chat_application_service.py
backend/app/services/chat_context_builder.py
backend/app/services/conversation_context_selection_service.py
backend/app/dto/
backend/app/api/
backend/app/tool/
```

重点检查：

```text
RouteType
RouteDecision
AIOrchestrationResult
AIOrchestrator.execute()
AIRouter.route()
ChatApplicationService.execute_message()
Conversation Context
Assistant Turn persistence
Idempotency replay
ProjectCapabilities
ToolExecutionService
```

同时检查：

```text
tests/
```

不要根据之前的报告假设当前接口。

以真实代码为准。

---

# 四、本阶段必须解决的 8 个问题

必须正式关闭以下问题。

---

## OQ-P5-5：Agent Result → AIOrchestrationResult

必须确定：

```text
AgentRuntimeResult
        ↓
AIOrchestrationResult
```

如何映射。

至少明确：

```text
Agent COMPLETED
Agent FAILED
Agent LIMIT_REACHED
Agent WAITING_APPROVAL
Agent CANCELLED
```

分别对应什么：

```text
AIOrchestrationResult.route
AIOrchestrationResult.content
AIOrchestrationResult.data
AIOrchestrationResult.metadata
```

特别注意：

不要修改：

```text
AIOrchestrationResult
```

如果当前 DTO 可以承载 Agent 结果：

**优先复用。**

不要为了 Agent 新建另一套顶层 AI Result。

---

# 五、OQ-P5-7：RouteType.AGENT

必须决定：

是否新增：

```text
RouteType.AGENT
```

如果新增，必须明确：

```text
RAG
TOOL
TEXT_TO_SQL
AGENT
```

之间的关系。

重点回答：

### 1. Agent 是 Capability 还是 Runtime？

结论应保持：

```text
Agent = AI Capability
AgentRuntime = Agent Capability Runtime
```

不是：

```text
Agent = Router
Agent = Orchestrator
```

---

### 2. Router 是否负责 Agent 内部决策？

答案应该明确：

```text
Router
    ↓
只决定进入 Agent
```

而：

```text
AgentRuntime
    ↓
决定 Tool Call / Final
```

禁止：

```text
Router → Agent Tool → Router
```

禁止递归：

```text
Orchestrator
    ↓
Agent
    ↓
Orchestrator
```

---

# 六、Agent Capability Authorization

必须冻结：

```text
ProjectCapabilities
        ↓
Router
        ↓
AGENT
        ↓
AgentRuntime
        ↓
Agent allowed_tools
        ↓
ToolExecutionService
```

重点：

**Agent 不能因为被路由到 AGENT 就自动获得项目全部 Tool。**

必须保持：

```text
Agent allowed_tools
    ⊆
ProjectCapabilities.tool_names
```

然后：

```text
Agent Runtime
    ↓
ToolExecutionService
```

继续执行第二层授权。

最终：

```text
Project Capability
    +
Agent Capability
    +
Runtime Step Authorization
    +
ToolExecutionService Authorization
```

不能因为增加 Agent 而减少现有安全检查。

---

# 七、OQ-P5-8：Conversation Context

必须决定 Agent 如何消费 Phase 4.2 已经建立的：

```text
conversation_context
```

当前 Phase 4.2 已冻结：

```text
MAX_TURNS = 20
MAX_CONTEXT_CHARS = 12000
```

以及：

```text
current question
+
conversation_context
```

的边界。

本阶段必须回答：

### Agent 是否接收 Conversation Context？

建议：

```text
YES
```

但是：

```text
Conversation Runtime
        ↓
选择 context
        ↓
AIOrchestrator
        ↓
AgentRuntime
```

而不是 Agent 自己查询：

```text
ConversationRepository
ConversationTurn
Database
```

---

# 八、Agent Context Boundary

冻结：

```text
Conversation Runtime
    owns conversation context
```

Agent：

```text
receives conversation_context
```

Agent 不允许：

```text
读取 Conversation DB
读取 ConversationTurn
读取 Idempotency
读取 ConversationRepository
```

Agent 不负责：

```text
Conversation history selection
Conversation windowing
Conversation persistence
Idempotency
Assistant Turn
```

---

# 九、Agent 内部 Context Placement

必须明确：

```text
system instruction
    ↓
agent capability / safety rules
    ↓
conversation context
    ↓
current question
```

Tool Result：

```text
role=tool
【TOOL RESULT — UNTRUSTED DATA】
```

不得把：

```text
conversation_context
```

与：

```text
tool result
```

混为同一信任等级。

---

# 十、Agent Context 是否进入每一轮

必须冻结：

```text
conversation_context
```

在 Agent Loop 中的行为。

建议：

```text
Agent round 1
    system
    conversation_context
    current question

Agent round 2
    system
    conversation_context
    current question
    assistant tool_call
    tool result

Agent round 3
    system
    conversation_context
    current question
    previous tool call/result
```

也就是说：

> Conversation Context 是本次 Agent Run 的只读输入，不因为 Tool Result 而被重新选择。

不要每一轮重新查询 Conversation。

不要每一轮重新运行 Conversation Context Selector。

---

# 十一、Agent 是否修改 Conversation

结论必须明确：

```text
AgentRuntime
    ❌ 不创建 ConversationTurn
    ❌ 不写 Conversation
    ❌ 不处理 Idempotency-Key
    ❌ 不决定 Assistant Turn
```

仍然由：

```text
ChatApplicationService
```

负责：

```text
USER turn
    ↓
AIOrchestrator
    ↓
AIOrchestrationResult
    ↓
ASSISTANT turn
```

因此：

```text
AgentRun
```

是：

```text
AI execution
```

不是：

```text
Conversation message
```

---

# 十二、Idempotency

必须明确：

Agent 不知道：

```text
Idempotency-Key
```

也不自己处理重复请求。

仍然：

```text
POST /conversations/{id}/messages
        ↓
ChatApplicationService
        ↓
Idempotency
        ↓
AIOrchestrator
        ↓
Agent
```

重复请求：

```text
completed duplicate
    ↓
replay persisted Assistant message
    ↓
Agent 不执行
```

Agent 内部不新增第二套 idempotency。

---

# 十三、Agent Failure Mapping

必须建立明确表格。

例如：

| Agent Runtime    | AI Result      |
| ---------------- | -------------- |
| COMPLETED        | 正常结果           |
| FAILED           | FAILED / error |
| LIMIT_REACHED    | 明确的 limit 状态   |
| WAITING_APPROVAL | 明确等待状态         |
| CANCELLED        | 明确取消状态         |

重点：

```text
LIMIT_REACHED
```

不能简单伪装成：

```text
FAILED
```

否则上层无法区分：

```text
Agent 出错
```

和：

```text
Agent 正常运行但达到策略边界
```

---

# 十四、Assistant Turn Mapping

必须明确：

### COMPLETED

如果：

```text
content != empty
request_id valid
```

则：

```text
ASSISTANT turn
```

### LIMIT_REACHED

如果 Runtime 能产生用户可见内容：

允许 Assistant Turn。

如果没有：

```text
no Assistant Turn
```

### FAILED

遵循 Phase 4.2：

```text
no Assistant Turn
```

USER Turn 保留。

### WAITING_APPROVAL

本阶段：

**不实现 HITL UI。**

只冻结语义。

可以：

```text
no Assistant Turn
```

或者明确一个 future-compatible result。

不能假装已经支持 Approval。

---

# 十五、Request ID

必须明确：

```text
request_id
```

仍由：

```text
AIOrchestrator
```

负责建立 / 传递。

Agent 不生成：

```text
assistant_request_id
```

Agent 内部：

```text
tool_call_id
```

只是：

```text
LLM tool call correlation
```

不能与：

```text
assistant_request_id
```

混淆。

建议最终关系：

```text
assistant_request_id
        │
        ├── AgentRun
        │
        ├── AgentStep
        │
        ├── LLM observation
        │
        └── Tool execution

tool_call_id
        │
        └── 单轮 LLM ↔ Tool correlation
```

---

# 十六、Agent Run Identity

本阶段必须决定：

Agent 是否需要：

```text
run_id
```

建议：

```text
YES
```

但第一版：

```text
in-memory AgentRun
```

即可。

`run_id` 用于：

```text
一次 AgentRuntime.execute()
```

的内部生命周期。

不要把它变成：

```text
Conversation ID
assistant_request_id
idempotency_key
```

它们是不同概念。

---

# 十七、Observability Boundary

本阶段不要实现 Observability。

只冻结未来关系：

```text
assistant_request_id
        ↓
AgentRun
        ↓
AgentStep
        ↓
LLM / Tool
```

未来可以：

```text
AgentRun
    ↓
AgentStep
    ↓
LLMObservation / ToolObservation
```

但是本阶段：

```text
NO DB
NO new observation table
NO migration
```

不要修改当前 4 张 observation tables。

---

# 十八、Evidence Boundary

继续保持 Phase 4.1：

```text
Evidence = Source / Provenance Unit
```

因此：

```text
AgentRun ≠ Evidence
ToolResult ≠ Evidence
AgentFinalAnswer ≠ Evidence
```

本阶段不实现：

```text
Agent → Evidence
Tool Result → Evidence
```

如果未来需要：

```text
Agent Evidence Candidate
```

作为单独 Architecture Decision。

---

# 十九、Orchestrator Integration

必须冻结最终调用：

```text
AIOrchestrator.execute()
        ↓
Router.route()
        ↓
RouteDecision
        ↓
AGENT
        ↓
AgentRuntime.execute()
        ↓
AgentRuntimeResult
        ↓
AIOrchestrationResult
```

禁止：

```text
AgentRuntime → AIOrchestrator
```

禁止：

```text
AgentRuntime → Router
```

禁止：

```text
AgentRuntime → ConversationRepository
```

禁止：

```text
AgentRuntime → Database Session
```

禁止：

```text
AgentRuntime → HTTP client
```

---

# 二十、Router Decision Responsibility

必须明确：

Router 只负责：

```text
Question
+
Project capabilities
+
Conversation context (if existing Router fallback requires it)
        ↓
RouteDecision
```

如果：

```text
route = AGENT
```

Router 不负责：

```text
Tool selection
Tool arguments
Agent steps
Agent loop
Agent final answer
```

这些全部属于：

```text
AgentRuntime
```

---

# 二十一、Agent 与普通 Tool Route 的区别

必须明确：

```text
TOOL
```

表示：

```text
一次固定能力调用
```

而：

```text
AGENT
```

表示：

```text
受策略限制的多步 Tool Calling
```

例如：

```text
TOOL:
question
 ↓
tool
 ↓
result
```

Agent：

```text
question
 ↓
LLM
 ↓
tool
 ↓
LLM
 ↓
tool
 ↓
LLM
 ↓
final
```

但仍然：

```text
max_steps
max_tool_calls
max_llm_calls
time
token
```

全部受 RuntimePolicy 限制。

---

# 二十二、不要扩大 Agent 能力

本 Decision 明确：

Agent 当前只支持：

```text
read-only tools
```

禁止：

```text
DELETE
UPDATE
INSERT
DDL
文件写入
HTTP side effect
发送消息
修改业务数据
```

即使未来 Tool Registry 出现写 Tool：

```text
Agent capability whitelist
```

默认也不能获得。

---

# 二十三、Tool Result Security

保持：

```text
Tool Result
    ↓
UNTRUSTED DATA
```

Conversation Context：

```text
untrusted user-originated historical data
```

因此 Agent LLM 输入中：

```text
System/Safety
    >
Agent Capability
    >
Conversation Context
    >
Current Question
    >
Tool Result
```

如果项目现有安全层级定义略有不同：

以当前已冻结安全边界为准，但必须保证：

**Tool Result 和 Conversation Context 都不能修改 Policy / Authorization。**

---

# 二十四、真实 DeepSeek Smoke

本阶段：

**不执行真实 DeepSeek。**

只记录未来 smoke contract：

```text
real_llm
    ↓
Agent
    ↓
one real Tool
    ↓
tool result
    ↓
second LLM call
    ↓
final answer
```

要求未来至少验证：

```text
tool_calls
tool_call_id
assistant.tool_calls
role=tool
usage
finish_reason
multi-round
```

当前不调用 API。

---

# 二十五、Architecture Decision 输出

修改：

```text
docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
```

只允许：

**append-only**

新增一个完整章节，例如：

```text
§29 Agent Runtime Integration Boundary
```

至少包含：

```text
29.1 RouteType.AGENT
29.2 Agent Result Mapping
29.3 Agent Capability Authorization
29.4 Conversation Context
29.5 Conversation Ownership
29.6 Idempotency
29.7 Request ID / Run ID / Tool Call ID
29.8 Failure / Limit / Approval Semantics
29.9 Observability Boundary
29.10 Evidence Boundary
29.11 Orchestrator Boundary
29.12 Router Responsibility
29.13 Security Boundary
29.14 Real LLM Smoke Contract
29.15 Rejected Alternatives
29.16 Open Questions
```

---

# 二十六、Open Questions

完成 Decision 后：

只允许保留真正无法在当前阶段安全决定的问题。

例如：

```text
OQ-P5-2 Agent State Persistence
OQ-P5-4 max_cost pricing source
OQ-P5-6 cancellation trigger
OQ-P5-9 duplicate same Tool call
OQ-P5-10 run-level summary
```

但是：

如果本 Decision 已经可以合理关闭：

```text
RouteType
Result mapping
Conversation integration
Context
Authorization
```

就必须关闭。

不要为了以后方便而留下本来可以决定的问题。

---

# 二十七、禁止修改

本阶段禁止修改：

```text
backend/app/agent/*.py
backend/app/services/ai_router_service.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/chat_application_service.py
backend/app/services/conversation_service.py
backend/app/api/
backend/app/db/
backend/app/dto/
```

禁止：

```text
DB migration
API change
Prompt change
Tool change
Provider change
Conversation behavior change
Evidence schema change
Observation schema change
```

本阶段只能：

```text
Architecture Decision
```

---

# 二十八、验证

执行：

```powershell
python -m pytest tests/test_agent_runtime.py -q
python -m pytest tests/test_agent_llm_adapter.py -q
python -m pytest tests/test_llm_provider.py -q
python -m pytest tests/test_llm_tool_calling.py -q
python -m pytest -q
python -m compileall backend tests scripts
```

不得调用：

```text
DeepSeek
DB
Network
```

如果完整 DB regression 存在历史 order-dependent failure：

保持原样记录。

不要修改 baseline。

---

# 二十九、Git

完成后：

```powershell
git status --short
git diff --stat
git diff --check
```

提交：

```text
docs: close Phase 5 agent integration architecture decision
```

不要：

```text
push
PR
merge
rebase
force push
```

---

# 三十、最终报告

严格使用：

```text
【Phase 5 AGENT INTEGRATION ARCHITECTURE DECISION COMPLETE】

1. RouteType.AGENT
2. Agent ↔ Orchestrator
3. Router Responsibility
4. Agent Result Mapping
5. Conversation Context
6. Conversation Ownership
7. Idempotency
8. Authorization
9. Request ID / Run ID / Tool Call ID
10. Failure / LIMIT_REACHED / WAITING_APPROVAL
11. Observability Boundary
12. Evidence Boundary
13. Security Boundary
14. Real DeepSeek Smoke Contract
15. Closed OQ
16. Remaining OQ
17. Tests
18. compileall
19. Git Diff
20. Commit

Production Code Changes:
0

DB Changes:
0

API Changes:
0

Prompt Changes:
0

Real LLM Calls:
0

Phase 5 Agent Integration Architecture:
READY

STOP
```

---

# 三十一、强制停止

完成后：

**立即 STOP。**

不要实现：

```text
RouteType.AGENT
Router
Orchestrator
Conversation integration
Agent persistence
Observability
Real DeepSeek smoke
HITL
Streaming
Memory
Planning
Multi-Agent
LangGraph
MCP
```

下一步必须等待新的明确实现指令。
