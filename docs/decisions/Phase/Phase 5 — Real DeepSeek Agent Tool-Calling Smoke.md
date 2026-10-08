你现在开始执行：

# Phase 5 — Real DeepSeek Agent Tool-Calling Smoke

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、目标

本阶段只验证：

> 当前已经完成的 Agent Runtime + AgentLLM Adapter + DeepSeek Provider Function Calling，在真实 DeepSeek API 下是否能够完成一次完整的多轮 Tool Calling。

目标链路：

```text
Real DeepSeek
      ↓
AgentRuntime
      ↓
AgentLLM
      ↓
LLMProvider.chat(tools=...)
      ↓
DeepSeek tool_calls
      ↓
Agent Runtime authorization
      ↓
Existing read-only Tool
      ↓
Tool Result
      ↓
role=tool
      ↓
DeepSeek 第二次调用
      ↓
FINAL
```

这是 **Smoke / Compatibility Verification**。

不是新功能开发。

---

# 二、严格禁止

本阶段禁止修改：

```text
AgentRuntime 核心逻辑
AgentLLM Adapter
LLMProvider contract
DeepSeekProvider
OpenAICompatibleClient
Router
Orchestrator
Conversation
Conversation Context
ToolExecutionService
Tool Registry
RAG
Text-to-SQL
SQL Validator
SQL Executor
Prompt
DB Schema
Evidence
Observability Schema
API contract
```

禁止：

```text
Retry
Fallback
Streaming
Thinking Mode
Memory
Planning
Multi-Agent
HITL
LangGraph
MCP
Persistence
```

如果真实 Smoke 失败：

**先记录真实失败，不要为了让 Smoke 通过而修改生产代码。**

---

# 三、先阅读

阅读：

```text
backend/app/agent/
backend/app/llm/
backend/app/tool/
backend/app/services/ai_orchestrator_service.py
backend/app/services/ai_router_service.py
backend/app/projects/
```

以及：

```text
tests/test_agent_llm_adapter.py
tests/test_agent_runtime.py
tests/test_agent_orchestrator_integration.py
tests/test_llm_tool_calling.py
```

确认当前真实 Tool Registry 中：

```text
至少一个安全、只读、稳定、无需外部网络的 Tool
```

优先选择：

```text
get_inventory
```

或：

```text
get_work_order
```

但必须以**当前实际 Tool 名称和 schema**为准。

不要假设。

---

# 四、Real LLM 标记

新增：

```text
@pytest.mark.real_llm
```

或者复用当前项目已经存在的 real LLM marker。

要求：

```text
默认 pytest：
SKIP
```

不能让：

```powershell
python -m pytest -q
```

调用 DeepSeek。

---

# 五、API Key

使用项目当前：

```text
.env
```

中的现有 DeepSeek 配置。

不要：

```text
打印 API Key
修改 API Key
写死 API Key
提交 API Key
输出 Authorization Header
```

测试报告不得包含：

```text
DEEPSEEK_API_KEY
Authorization
Bearer ...
```

---

# 六、真实调用数量

Smoke 必须明确限制：

```text
max LLM calls = 2
max tool calls = 1
```

不要让测试因为异常进入无限循环。

推荐：

```text
AgentRuntimePolicy(
    max_steps=2,
    max_tool_calls=1,
    max_llm_calls=2,
    max_execution_time_ms=<合理值>,
    max_cost_tokens=<合理值>,
)
```

具体数值如果当前 Policy contract 有更合适的最小合法值，遵循真实 contract。

---

# 七、不要调用 Thinking Mode

本 Smoke：

```text
thinking = disabled
```

或者保持当前 Client 的明确 non-thinking 配置。

不要增加：

```text
reasoning_content
thinking
reasoning_effort
```

相关处理。

原因：

DeepSeek 官方文档说明，Thinking Mode Tool Calling 后续请求必须完整传递 `reasoning_content`；当前 Agent Runtime 尚未把这条协议纳入本阶段，因此不要偷偷打开 Thinking Mode。

---

# 八、工具选择

Smoke 必须使用：

```text
一个真实、只读、无外部副作用的现有 Tool
```

例如：

```text
get_inventory
```

如果当前项目实际 Tool 名称不同：

使用实际 Tool。

不得新增 Tool。

不得修改 Tool。

不得创建 fake Tool。

---

# 九、Smoke Question

问题必须简单、明确、能够让模型自然选择目标 Tool。

例如，如果实际存在：

```text
get_inventory
```

可以使用：

```text
查询物料 MAT001 当前库存。
```

如果实际 Tool 是：

```text
get_work_order
```

则选择一个当前 Tool schema 可以直接回答的问题。

不要使用：

```text
复杂多跳业务问题
```

不要测试：

```text
规划
```

不要测试：

```text
自主选择多个 Tool
```

本阶段只验证：

```text
ONE Tool
```

---

# 十、必须验证第一次 LLM Response

第一次真实 LLM 调用必须观察：

```text
LLMResponse
```

确认：

```text
tool_calls != empty
```

并且：

```text
len(tool_calls) == 1
```

验证：

```text
tool_call.id
tool_call.name
tool_call.arguments
```

全部存在。

---

# 十一、验证 Tool Name

确认：

```text
tool_call.name
```

属于：

```text
Agent allowed_tools
```

并且：

```text
Agent allowed_tools
    ⊆
ProjectCapabilities.tool_names
```

不能因为真实 LLM 输出了未授权 Tool 就直接执行。

如果 DeepSeek 选择了不存在/未授权 Tool：

必须：

```text
TOOL_NOT_ALLOWED
```

或者当前稳定对应错误码。

**绝对不能执行 Handler。**

---

# 十二、验证 Tool Arguments

确认：

```text
tool_call.arguments
```

是：

```text
dict
```

并且通过：

```text
ToolRegistry.validate_arguments()
```

验证。

特别记录：

```text
material_code
```

或实际 Tool 参数。

禁止：

```text
eval()
```

禁止：

```text
字符串拼接 SQL
```

禁止：

```text
自动修复参数
```

---

# 十三、验证 Tool Handler

记录：

```text
handler execution count
```

必须：

```text
1
```

不能：

```text
0
>1
```

确认执行的是：

```text
existing Tool
```

不是测试 fake。

---

# 十四、验证 Tool Result

Tool Result 必须进入 Agent LLM 消息：

```text
role = "tool"
```

并且：

```text
tool_call_id
```

必须与第一次：

```text
assistant.tool_calls[0].id
```

完全一致。

Content 必须继续包含：

```text
【TOOL RESULT — UNTRUSTED DATA】
```

不要把 Tool Result 变成：

```text
system
```

不要放进 system prompt。

---

# 十五、验证第二次 LLM

第二次真实调用必须发生：

```text
LLM calls = 2
```

第二次输入必须包含：

```text
assistant.tool_calls
+
role=tool
```

第二次不能重新生成一个无关 Tool。

期望最终：

```text
tool_calls = empty
finish_reason = stop
content != empty
```

如果当前 DeepSeek 返回的合法 finish_reason 不同：

记录真实值，不修改生产代码。

---

# 十六、验证最终 Agent Result

最终：

```text
AgentRuntimeResult.status
```

必须：

```text
COMPLETED
```

并且：

```text
content != empty
tool_calls = 1
llm_calls = 2
```

如果 token usage 存在：

```text
tokens_used > 0
token_budget_unverifiable = false
```

如果 Provider 实际没有返回 usage：

不要伪造：

```text
token usage
```

应保持：

```text
token_budget_unverifiable = true
```

---

# 十七、验证 Orchestrator Integration

Smoke 最好通过：

```text
AIOrchestrator
```

入口运行。

最终：

```text
AIOrchestrationResult.route == AGENT
```

验证：

```text
metadata.request_id
metadata.agent_status
metadata.agent_llm_calls
metadata.agent_tool_calls
```

存在并正确。

不要直接只测试：

```text
AgentRuntime
```

因为 Agent Runtime 已经有 57 个离线测试。

这次目标是：

> 验证真实 LLM + Agent Adapter + Runtime + Tool + Orchestrator 整条链。

---

# 十八、Conversation Smoke

本阶段**不要把 Conversation API 作为必需入口**。

可以：

```text
AIOrchestrator
```

直接调用。

原因：

本 Smoke 的唯一目标是：

```text
真实 DeepSeek Tool Calling
```

不要把：

```text
Conversation
Idempotency
Assistant Turn
```

同时引入导致问题定位困难。

这些已经有离线 integration tests。

---

# 十九、安全验证

Smoke 至少验证：

### 1. unauthorized Tool

如果模型尝试：

```text
delete_inventory
```

必须：

```text
TOOL_NOT_ALLOWED
```

并且：

```text
handler calls = 0
```

但：

**不要为了诱导模型调用 delete_inventory 而改变 Prompt。**

如果真实模型没有选择未授权 Tool：

记录：

```text
not exercised in this smoke
```

不要伪造。

---

### 2. Tool Result injection

可以使用真实 Tool 返回的正常结果。

不要修改生产 Tool 来注入恶意数据。

Injection 已由离线 Agent Runtime 测试覆盖。

本阶段不为了覆盖率改生产 Tool。

---

# 二十、Smoke 输出

测试必须输出一个安全 summary：

```text
Real Agent Tool Calling Smoke

Provider:
Model:

Question:

LLM calls: 2 / 2
Tool calls: 1 / 1

First decision:
  type: TOOL_CALL
  tool_name: <name>
  tool_call_id: <id-presence only>
  arguments_valid: PASS

Tool:
  authorized: PASS
  executed: PASS

Second decision:
  type: FINAL
  finish_reason: <value>

Agent:
  status: COMPLETED
  token_usage: AVAILABLE / UNAVAILABLE

Security:
  unauthorized_tool: NOT_EXERCISED
  tool_result_untrusted: PASS

Result:
PASS / FAIL
```

**不要输出：**

```text
API Key
Authorization
完整 prompt
完整 conversation context
完整 tool result
```

`tool_call_id`可以只报告：

```text
present
```

不要泄露完整 ID。

---

# 二十一、失败分类

如果失败，必须区分：

```text
A. API Authentication
B. Network
C. Provider Response
D. Tool Call Parsing
E. Tool Schema
F. Tool Authorization
G. Tool Execution
H. Second LLM Call
I. Final Decision
J. Token Usage
K. Agent Runtime
```

不要看到：

```text
Real LLM failed
```

就修改 Agent。

---

# 二十二、如果真实 DeepSeek 不支持当前 Tool

如果出现：

```text
Tool Call 不产生
```

不要修改：

```text
AgentRuntime
AgentLLM
Provider
Prompt
```

先记录：

```text
LIVE_TOOL_CALLING_UNVERIFIED
```

并报告：

```text
离线协议 PASS
真实 API Tool Calling 未达到可验证状态
```

然后 STOP。

---

# 二十三、测试命令

先：

```powershell
python -m pytest tests/test_agent_llm_adapter.py -q
python -m pytest tests/test_agent_runtime.py -q
python -m pytest tests/test_agent_orchestrator_integration.py -q
```

然后执行：

```powershell
$env:RUN_REAL_LLM_TESTS="1"
python -m pytest -m real_llm tests/test_agent_real_llm_smoke.py -q
```

如果项目当前已有其他 real LLM 环境变量 / marker：

优先复用实际机制。

不要新增复杂测试框架。

---

# 二十四、默认测试必须继续零真实调用

执行：

```powershell
Remove-Item Env:RUN_REAL_LLM_TESTS -ErrorAction SilentlyContinue
python -m pytest -q
```

确认：

```text
real_llm = skipped
```

并且：

```text
0 real DeepSeek calls
```

---

# 二十五、compile

执行：

```powershell
python -m compileall -q backend tests scripts
```

---

# 二十六、Git Scope

允许：

```text
tests/test_agent_real_llm_smoke.py
```

以及：

```text
必要的 test configuration
```

禁止修改：

```text
production code
```

除非发现：

```text
真实 Provider compatibility bug
```

如果发现：

**停止并报告，不要现场修复。**

本阶段原则：

```text
Smoke first
Fix later
```

---

# 二十七、Git Commit

如果 Smoke PASS：

```text
test: Phase 5 real DeepSeek agent tool calling smoke
```

不要：

```text
push
PR
merge
rebase
force push
```

如果 Smoke FAIL：

不要 commit 修改生产代码。

可以保留测试文件，但最终必须明确：

```text
NOT READY
```

---

# 二十八、最终报告

严格：

```text
【Phase 5 REAL DEEPSEEK AGENT SMOKE COMPLETE】

1. Provider / Model
2. Smoke Question
3. First LLM Call
4. Tool Call
5. Tool Authorization
6. Tool Execution
7. Tool Result Message
8. Second LLM Call
9. Final Agent Result
10. Token Usage
11. Security
12. Offline Regression
13. Real LLM Call Count
14. compileall
15. Git Diff
16. Commit
17. Failure / Limitation
18. Conclusion
```

结论只能是：

```text
REAL AGENT TOOL CALLING = VERIFIED
```

或者：

```text
REAL AGENT TOOL CALLING = NOT VERIFIED
```

不要在 Smoke 失败时声称：

```text
DeepSeek 不支持 Tool Calling
```

除非有直接 API evidence。

---

# 二十九、强制 STOP

无论 PASS / FAIL：

**本阶段结束立即 STOP。**

不要继续：

```text
Agent Persistence
Agent Observability
HITL
Streaming
Memory
Planning
Multi-Agent
LangGraph
MCP
```

等待下一条明确指令。
