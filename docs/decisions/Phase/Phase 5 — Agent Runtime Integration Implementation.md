你现在开始实现：

# Phase 5 — Agent Runtime Integration Implementation

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、前置条件

Phase 5 以下内容已经完成并冻结：

```text
Phase 5 Scope & Architecture Decision
Agent Runtime Core Loop
OQ-P5-1 LLM Tool Decision Protocol
Agent LLM Adapter
Agent Integration Architecture Decision
```

最新 Architecture Decision：

```text
docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
```

最新 Integration Decision 已关闭：

```text
OD-P5-14 RouteType.AGENT
OD-P5-15 Agent Result Mapping
OD-P5-16 Agent Authorization
OD-P5-17 Conversation Context
OD-P5-18 Conversation Ownership
OD-P5-19 Request ID / Run ID / Tool Call ID
OD-P5-20 Failure / LIMIT_REACHED / WAITING_APPROVAL
OD-P5-21 Observability Boundary
OD-P5-22 Evidence Boundary
OD-P5-24 Real LLM Smoke Contract
```

**本阶段严格按照 §29 冻结内容实现，不重新设计。**

---

# 二、本阶段唯一目标

把：

```text
RouteType.AGENT
```

真正接入：

```text
AIOrchestrator
```

形成：

```text
ConversationRuntime
        ↓
AIOrchestrator
        ↓
Router
        ↓
RouteDecision.AGENT
        ↓
AgentRuntime
        ↓
AgentLLM
        ↓
ToolExecutionService
        ↓
AgentRuntimeResult
        ↓
AIOrchestrationResult
        ↓
ConversationRuntime
```

最终使 Agent 成为：

```text
RAG
TOOL
TEXT_TO_SQL
AGENT
```

四个同级 AI Capability 之一。

---

# 三、开始编码前必须阅读

先阅读真实代码：

```text
backend/app/agent/
backend/app/llm/
backend/app/services/ai_router_service.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/chat_application_service.py
backend/app/services/conversation_context_selection_service.py
backend/app/services/conversation_service.py
backend/app/dto/
backend/app/tool/
```

重点确认：

```text
RouteType
RouteDecision
AIOrchestrationResult
AIOrchestrator.execute()
AIRouter.route()
ProjectCapabilities
ToolExecutionService
AgentRuntime
ProviderAgentLLM
AgentRuntimePolicy
```

同时阅读：

```text
tests/
```

特别是：

```text
tests/test_agent_runtime.py
tests/test_agent_llm_adapter.py
tests/test_ai_router_service.py
tests/test_ai_orchestrator_service.py
tests/test_conversation*
```

**不要根据任务书假设当前接口。**

---

# 四、严格修改范围

允许修改：

```text
backend/app/services/ai_router_service.py
backend/app/services/ai_orchestrator_service.py
backend/app/agent/
backend/app/projects/
backend/app/dto/
tests/
```

只有确实需要时才修改。

禁止修改：

```text
TextToSQLService
RAG 核心逻辑
ToolExecutionService 核心行为
SQLValidator
SQLExecutor
ConversationRepository
Conversation idempotency 核心行为
Evidence schema
Observation schema
Prompt 内容
LLMProvider contract
DeepSeekProvider
OpenAICompatibleClient
```

禁止新增：

```text
DB table
DB migration
Agent persistence
AgentObservation
HITL UI
Streaming
Memory
Planning
Multi-Agent
LangGraph
MCP
```

---

# 五、RouteType.AGENT

使用已经冻结的：

```python
RouteType.AGENT = "agent"
```

不要重新定义第二套 Agent route enum。

确认 Router 当前：

```text
valid_routes
```

是否动态来源于：

```python
RouteType
```

如果已经如此：

**不要修改 Prompt。**

尤其不要为了加入 AGENT 手工修改历史 Prompt。

---

# 六、agent_enabled

继续使用：

```text
agent_enabled: bool = False
```

默认：

```text
False
```

因此：

> 默认情况下，现有系统行为必须保持不变。

当：

```text
agent_enabled=False
```

时：

```text
AGENT route
    ↓
不可执行
```

必须 fail-closed。

不得：

```text
AGENT → RAG
AGENT → TOOL
AGENT → TEXT_TO_SQL
```

静默降级。

---

# 七、Agent Runtime 注入

不要让：

```text
AIOrchestrator
```

自己创建：

```text
ProviderAgentLLM
AgentRuntimePolicy
ToolExecutionService
```

使用 composition root / factory 注入。

Orchestrator 只依赖：

```python
AgentCapability
```

或当前架构已经确定的最小 Protocol。

目标：

```text
AIOrchestrator
    ↓
AgentCapability
```

而不是：

```text
AIOrchestrator
    ↓
AgentRuntime
    ↓
DeepSeekProvider
```

---

# 八、AgentCapability

如果当前项目已经存在等价 Protocol：

**直接复用。**

如果没有，新增最小 Protocol。

建议语义：

```python
class AgentCapability(Protocol):
    def run(
        self,
        *,
        question: str,
        conversation_context: str | None,
    ) -> AgentRuntimeResult:
        ...
```

不要让 Protocol 暴露：

```text
LLMProvider
AgentLLM
ToolRegistry
Database
ConversationRepository
AgentRuntimePolicy
```

这些全部属于 composition root / Agent 内部。

---

# 九、AIOrchestrator AGENT 分支

最终逻辑必须类似：

```text
AIOrchestrator.execute()
        ↓
Router.route()
        ↓
RouteDecision
        ↓
switch route
```

已有：

```text
RAG
TOOL
TEXT_TO_SQL
```

新增：

```text
AGENT
```

调用：

```text
AgentCapability.run(
    question=question,
    conversation_context=context,
)
```

不要：

```text
AgentRuntime → AIOrchestrator
```

不要：

```text
AgentRuntime → Router
```

---

# 十、AGENT Route Capability Gate

AGENT 执行前必须同时满足：

```text
agent_enabled == True
```

以及：

```text
AgentCapability/runtime 已正确注入
```

否则：

```text
fail closed
```

不要：

```text
fallback RAG
fallback TOOL
fallback TEXT_TO_SQL
```

错误应该进入现有 AI Orchestrator 错误边界。

---

# 十一、Agent Result Mapping

严格按照 OD-P5-15。

不要创建新的顶层 AI Result。

使用：

```text
AIOrchestrationResult
```

映射：

```text
route = RouteType.AGENT
```

---

## Agent COMPLETED

```text
AgentRuntimeResult.status = COMPLETED
```

映射：

```text
content = AgentRuntimeResult.content
outcome = SUCCESS
agent_status = COMPLETED
```

只有：

```text
content 非空
request_id 合法
```

Conversation 层才创建 ASSISTANT Turn。

---

## Agent FAILED

Agent Runtime：

```text
FAILED
```

应进入现有：

```text
AIOrchestratorExecutionError
```

遵循 Phase 4.2：

```text
USER Turn 保留
ASSISTANT Turn 不创建
```

错误 metadata 只能包含稳定错误码。

禁止：

```text
exception message
stack trace
prompt
tool arguments
tool result
```

---

## LIMIT_REACHED

不能把：

```text
agent_status
```

丢失。

必须：

```text
outcome = FAILED
agent_status = LIMIT_REACHED
```

如果 Agent Runtime 有用户可见 content：

```text
content = content
```

否则：

```text
content = None
```

Conversation 层继续按照现有 Assistant Turn 规则处理。

**不要新增 AssistantOutcome 枚举。**

---

## WAITING_APPROVAL

当前没有真实 approval trigger。

因此：

```text
WAITING_APPROVAL
```

只保持未来兼容映射。

不要实现 HITL。

不要创建 Approval API。

---

# 十二、Metadata 白名单

Agent metadata 只能包含已经冻结的稳定字段：

```text
request_id
outcome
decision_source
route_reason
agent_status
agent_error
agent_steps
agent_tool_calls
agent_llm_calls
agent_tokens_used
agent_token_budget_unverifiable
```

不要加入：

```text
step_records
tool arguments
tool data
prompt
conversation_context
raw LLM response
exception message
API key
authorization
database URL
```

特别注意：

当前 API 可能：

```python
metadata=dict(result.metadata)
```

全量透传。

所以这里必须真正做到：

> metadata 本身就是安全 DTO。

---

# 十三、Request ID

继续使用：

```text
AIOrchestrator
```

现有：

```text
new_request_id()
```

作为唯一生成点。

Agent：

**禁止自己生成 assistant_request_id。**

关系：

```text
assistant_request_id
    ↓
assistant_trace_scope
    ↓
AgentRuntime
    ↓
LLM usage / Tool execution
```

Agent 的：

```text
run_id
```

只作为 Agent Runtime 内部身份。

不得进入：

```text
Conversation
AIOrchestrationResult.metadata
```

除非当前代码已有明确冻结字段，否则不要扩展 metadata。

---

# 十四、Conversation Context

AIOrchestrator 已经收到：

```text
context
```

直接传给：

```text
AgentCapability.run(
    conversation_context=context
)
```

Agent：

```text
只读
```

禁止：

```text
ConversationRepository
ConversationTurn
ConversationContextSelector
DB
```

Agent 不重新：

```text
select history
build context
truncate context
summarize context
```

---

# 十五、Agent Context Message

Agent Runtime 内部必须保持已经冻结的顺序：

```text
SYSTEM
    ↓
Capability / Safety
    ↓
USER
    CONVERSATION REFERENCE
    CURRENT QUESTION
    ↓
assistant.tool_calls
    ↓
role=tool
    【TOOL RESULT — UNTRUSTED DATA】
    ↓
next LLM
```

Conversation Context：

```text
untrusted reference
```

Tool Result：

```text
untrusted data
```

二者不能合并。

---

# 十六、Authorization

不要修改已有 Tool Authorization。

Agent 只能获得：

```text
allowed_tools
```

并满足：

```text
allowed_tools
    ⊆
ProjectCapabilities.tool_names
```

然后：

```text
AgentRuntime
    ↓
ToolExecutionService
    ↓
第二层 capability check
    ↓
ToolRegistry
    ↓
schema validation
    ↓
handler
```

任何一层拒绝：

```text
TOOL_NOT_ALLOWED
```

或现有对应稳定错误码。

不要绕过 ToolExecutionService。

---

# 十七、Composition Root

必须找到项目当前真正的：

```text
ProjectCapabilities
ToolExecutionService
LLMProvider
AIOrchestrator
```

装配位置。

在那里创建：

```text
ProviderAgentLLM(llm_provider)
```

以及：

```text
AgentRuntime(
    llm=provider_agent_llm,
    tool_execution_service=existing_tool_execution_service,
    policy=AgentRuntimePolicy(...),
    allowed_tools=authorized_subset,
)
```

不要：

```text
AgentRuntime()
```

内部自行读取 `.env`。

不要：

```text
AgentRuntime()
```

内部创建 DeepSeek client。

---

# 十八、Agent Policy

使用现有：

```text
AgentRuntimePolicy
```

默认值保持：

```text
max_steps = 8
max_tool_calls = 8
max_llm_calls = 8
```

时间 / token budget 继续使用现有设计。

不要在本阶段调整默认预算。

不要新增：

```text
retry
backoff
fallback model
```

---

# 十九、Tool Result

继续保持：

```text
【TOOL RESULT — UNTRUSTED DATA】
```

Tool Result：

```text
不能修改:
Policy
allowed_tools
project_id
capabilities
limits
authorization
```

必须继续通过 JSON-safe serialization。

---

# 二十、Router 行为

AGENT：

```text
Router 只负责分类
```

Router 不允许：

```text
选择 Tool
构造 Tool arguments
执行 Tool
循环
决定 Agent steps
生成 Agent final answer
```

如果当前 Router 的规则路径没有 AGENT 规则：

**保持不变。**

本阶段不要增加：

```text
"帮我查多个东西" → AGENT
```

这种关键词规则。

Agent route 的自动选择规则另立 Decision：

```text
OQ-P5-11
```

当前只支持已经存在的：

```text
LLM route decision
```

---

# 二十一、最重要：默认行为零变化

必须验证：

```text
agent_enabled=False
```

时：

```text
Phase 4.2 行为
=
Phase 4.2 原行为
```

尤其：

```text
RAG
TOOL
TEXT_TO_SQL
Conversation
Idempotency
Context
Refusal
Assistant Turn
```

全部不变。

---

# 二十二、测试

新增：

```text
tests/test_agent_orchestrator_integration.py
```

至少覆盖：

### 1. Agent disabled

```text
agent_enabled=False
```

AGENT 不可执行。

---

### 2. Agent enabled

```text
agent_enabled=True
AgentCapability injected
```

AGENT 正常执行。

---

### 3. Agent runtime missing

```text
agent_enabled=True
AgentCapability=None
```

必须：

```text
fail closed
```

不能 fallback。

---

### 4. Agent FINAL

验证：

```text
AGENT
→ AgentRuntime
→ FINAL
→ AIOrchestrationResult
```

---

### 5. Agent Tool Call

验证：

```text
AGENT
→ Tool
→ Tool Result
→ Final
```

---

### 6. Multiple Tool Calls

保持：

```text
Runtime policy
```

限制有效。

---

### 7. LIMIT_REACHED

验证：

```text
agent_status = LIMIT_REACHED
outcome = FAILED
```

---

### 8. FAILED

验证：

```text
AIOrchestratorExecutionError
```

以及：

```text
no Assistant Turn
```

如果使用 Conversation integration test。

---

### 9. Context

验证：

```text
Conversation Context
→ Agent
```

能够进入 Agent。

并且：

```text
Agent 不访问 ConversationRepository
```

---

### 10. Idempotency

必须确保：

```text
completed duplicate
→ Agent calls = 0
```

```text
conflict
→ Agent calls = 0
```

```text
retry
→ Agent calls = 1
```

复用现有 Phase 4.2 idempotency 测试基础设施。

---

### 11. Authorization

验证：

```text
allowed_tools ⊆ project capabilities
```

以及：

```text
unauthorized Tool
→ ToolExecutionService = 0
→ handler = 0
```

---

### 12. Security

Conversation Context 注入：

```text
ignore previous instructions
increase max_steps
call unauthorized tool
```

不得改变：

```text
Policy
Authorization
Project capabilities
```

Tool Result 注入同样测试。

---

### 13. Metadata Security

确认：

```text
AIOrchestrationResult.metadata
```

没有：

```text
prompt
conversation_context
tool arguments
tool data
exception message
secret
```

---

### 14. Existing regression

现有：

```text
tests/test_agent_runtime.py
tests/test_agent_llm_adapter.py
```

必须全部继续通过。

---

# 二十三、不要增加真实 DeepSeek

本阶段：

```text
REAL LLM = 0
```

使用 Fake AgentCapability / Fake AgentRuntime / Fake Provider。

不要新增：

```text
@pytest.mark.real_llm
```

真实 smoke 下一阶段单独做。

---

# 二十四、不要修改 Prompt

特别注意：

```text
Prompt = 0 changes
```

如果 RouteType 动态来自 enum：

直接复用。

如果测试发现：

```text
AGENT
```

加入导致 prompt hash 变化：

**停止并报告。**

不要直接修改 prompt。

---

# 二十五、不要修改 Database

本阶段：

```text
Migration = 0
Tables = 0
DB writes = 0
```

Agent State：

```text
IN-MEMORY
```

不持久化。

---

# 二十六、测试命令

先运行：

```powershell
python -m pytest tests/test_agent_orchestrator_integration.py -q
```

然后：

```powershell
python -m pytest tests/test_agent_runtime.py -q
python -m pytest tests/test_agent_llm_adapter.py -q
python -m pytest tests/test_llm_provider.py -q
python -m pytest tests/test_llm_tool_calling.py -q
```

最后：

```powershell
python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests scripts
```

如果 DB regression 被执行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

保持历史 order-dependent failures 原样记录。

不要修改 baseline。

---

# 二十七、Git 检查

完成后：

```powershell
git status --short
git diff --stat
git diff --check
git diff
```

重点检查：

```text
Prompt = 0
DB = 0
Migration = 0
Evidence = 0
Observation = 0
Provider contract = 0
Tool behavior = 0
TextToSQL = 0
RAG = 0
Conversation idempotency core = 0
```

---

# 二十八、Commit

只有：

```text
pytest
compileall
scope audit
```

全部满足后提交：

```text
feat: Phase 5 agent orchestrator integration
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

# 二十九、最终报告

严格输出：

```text
【Phase 5 AGENT INTEGRATION COMPLETE】

1. RouteType.AGENT
2. Agent Runtime Injection
3. Orchestrator Integration
4. Result Mapping
5. Conversation Context
6. Authorization
7. Request ID / Run ID / Tool Call ID
8. LIMIT_REACHED / FAILED
9. Idempotency
10. Security
11. Metadata Security
12. Tests
13. Full Regression
14. compileall
15. Git Diff
16. Commit
17. Production Scope
18. Known Limitations

Production Code:
...

DB Changes:
0

API Changes:
0

Prompt Changes:
0

Real LLM Calls:
0

Agent Integration:
READY / NOT READY

STOP
```

---

# 三十、强制 STOP

完成本阶段后立即停止。

不要继续：

```text
Agent Persistence
Agent Observability
Real DeepSeek Smoke
HITL
Streaming
Memory
Planning
Multi-Agent
LangGraph
MCP
```

这些全部等待下一条明确指令。
