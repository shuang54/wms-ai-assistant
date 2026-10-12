# Phase 5 — OQ-P5-1 LLM Tool Decision Protocol Architecture Decision

你现在不要写生产代码。

本阶段只解决一个架构问题：

> **Agent Runtime 应该如何从现有 LLM Provider 获得稳定、严格、可验证的 `FINAL / TOOL_CALL` Decision。**

项目：

```text
D:\coding\ai\wms-ai-assistant
```

当前状态：

```text
Phase 5 Scope & Architecture Decision
        ↓
Phase 5 Core Loop MVP
        ↓
AgentRuntime 已完成
        ↓
AgentLLM seam 已存在
        ↓
下一步：关闭 OQ-P5-1
```

当前 Core Loop 已验证：

```text
AgentRuntime
    ↓
AgentDecision
    ├── FINAL
    └── TOOL_CALL
           ↓
      Authorization
           ↓
   ToolExecutionService
           ↓
       Tool Result
           ↓
          LLM
```

但当前：

```text
AgentLLM
```

还是内部 seam，尚未正式接入：

```text
LLMProvider
```

---

# 一、严格 STOP 范围

本阶段：

**只做 Architecture Decision。**

允许：

```text
阅读代码
分析 Provider Contract
分析 DeepSeek Provider
分析 LLMResponse
分析现有 Structured Output
分析 AgentDecision
比较 Decision Protocol
形成 Architecture Decision
更新 Phase 5 Decision 文档
```

禁止：

```text
❌ 修改 LLMProvider
❌ 修改 DeepSeekProvider
❌ 修改 OpenAICompatibleClient
❌ 修改 AgentRuntime
❌ 修改 AgentDecision
❌ 修改 ToolExecutionService
❌ 修改 Router
❌ 修改 AIOrchestrator
❌ 修改 Conversation
❌ 修改 RAG
❌ 修改 Text-to-SQL
❌ 修改 Prompt
❌ 修改 SQL Validator
❌ 修改 SQL Executor
❌ 修改 DB
❌ 新增 migration
❌ 调用真实 DeepSeek
❌ 调用网络
❌ 新增 Agent API
❌ Streaming
❌ MCP
❌ LangGraph
```

**不要实现任何代码。**

---

# 二、先阅读真实代码

必须先阅读：

```text
backend/app/llm/provider.py
backend/app/llm/deepseek_provider.py
backend/app/llm/client.py
backend/app/llm/structured.py
backend/app/llm/models.py

backend/app/agent/runtime.py
backend/app/agent/decision.py
backend/app/agent/models.py
backend/app/agent/policy.py

backend/app/services/tool_execution_service.py
backend/app/services/tool_argument_extractor.py

tests/test_llm_provider.py
tests/test_llm_structured.py
tests/test_agent_runtime.py
```

同时阅读：

```text
docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
```

重点确认真实情况：

1. `LLMProvider` 当前到底提供什么接口。
2. `DeepSeekProvider` 返回什么。
3. `LLMResponse` 是否已经有：

   * content
   * model
   * finish_reason
   * usage
   * metadata
4. Structured Output 当前已经支持到什么程度。
5. 当前 Provider 是否能够传递 response format。
6. 当前 DeepSeek endpoint 是否支持 JSON / structured output。
7. 当前 AgentDecision parser 的输入是什么。
8. 当前 AgentRuntime 是否已经假定 JSON。
9. 当前项目是否已有统一 JSON schema / Pydantic parsing。
10. 当前 Tool Registry 是否能够生成稳定的 Tool schema。

**以代码事实为准，不要根据文件名猜测。**

---

# 三、需要回答的核心问题

最终必须明确：

```text
AgentRuntime
      ↓
AgentLLM
      ↓
LLMProvider
      ↓
DeepSeekProvider
      ↓
DeepSeek API
```

其中：

> **哪一层负责把 LLM 输出约束成 AgentDecision？**

以及：

> **哪一层负责把 Agent 可用 Tool 描述提供给 LLM？**

以及：

> **哪一层负责最终验证 LLM 返回的 Tool Name + Arguments？**

---

# 四、候选方案

至少比较以下三个方案。

---

## Option A：Structured JSON Decision

模型只返回：

```json
{
  "type": "FINAL",
  "content": "库存共有 120 件。"
}
```

或者：

```json
{
  "type": "TOOL_CALL",
  "tool_name": "get_inventory",
  "arguments": {
    "material_code": "MAT001"
  }
}
```

链路：

```text
LLM
 ↓
Structured JSON
 ↓
AgentDecision.parse()
 ↓
AgentRuntime
```

优势：

```text
简单
Provider Contract 改动小
Agent Runtime 控制强
Tool Authorization 仍由 Runtime 负责
```

缺点：

```text
依赖模型 JSON 遵循能力
Tool schema 需要额外注入 Prompt / schema
```

---

# 五、Option B：Provider-native Function Calling

链路：

```text
AgentRuntime
 ↓
LLMProvider.chat(...)
 ↓
tools=[...]
 ↓
LLM
 ↓
tool_calls
 ↓
Provider
 ↓
AgentDecision
```

优势：

```text
Tool arguments 结构更明确
模型天然知道 Tool schema
减少 JSON parsing
```

缺点：

```text
现有 LLMProvider contract 可能需要扩展
DeepSeek compatibility 需要确认
不同 Provider 的 Tool Calling response 可能存在差异
Provider 层可能开始承担 Agent-specific contract
```

重点判断：

> 如果选择这个方案，是否会让通用 `LLMProvider` 被 Agent Runtime 绑死？

---

# 六、Option C：Hybrid

例如：

```text
Provider
    ↓
structured response
    ↓
AgentDecision
```

同时在 Agent Runtime 提供：

```text
Tool definitions
```

或者未来允许 Provider-native tool calls。

要求明确：

```text
MVP 使用哪一种
未来是否允许升级
升级是否需要新的 Architecture Decision
```

禁止为了“兼容未来”现在实现两套。

---

# 七、必须考虑 Provider Abstraction

重点分析：

当前：

```text
LLMProvider
```

是通用 AI Core 基础设施。

而：

```text
AgentDecision
```

是 Agent Runtime 专属概念。

因此必须回答：

是否允许：

```text
LLMProvider
    ↓
AgentDecision
```

？

还是应该保持：

```text
LLMProvider
    ↓
LLMResponse
    ↓
Agent Runtime
    ↓
AgentDecision
```

原则上优先保持：

```text
LLMProvider
```

不知道：

```text
Agent
Tool Authorization
RuntimePolicy
AgentDecision
```

不要让底层 Provider 依赖 Agent。

---

# 八、Tool Schema Boundary

必须明确 Tool Schema 放在哪里。

目标：

```text
Project Capabilities
       ↓
Allowed Tools
       ↓
Tool Schema
       ↓
LLM Decision
```

但是：

```text
LLM看到Tool
    ≠
LLM拥有Tool权限
```

最终权限仍然：

```text
Agent Runtime
      ↓
Authorization
      ↓
ToolExecutionService
```

因此：

即使 LLM 返回：

```json
{
  "type": "TOOL_CALL",
  "tool_name": "delete_inventory"
}
```

也必须：

```text
TOOL_NOT_ALLOWED
```

不能执行。

---

# 九、Tool Arguments Boundary

必须明确：

```text
LLM generated arguments
        ↓
AgentDecision
        ↓
ToolExecutionService
        ↓
existing Tool schema validation
```

Agent Runtime：

```text
不信任 arguments
```

Provider：

```text
不负责业务权限
```

Tool Handler：

```text
不直接暴露给 LLM
```

---

# 十、Structured Output 安全要求

无论最终选择哪个方案，都必须保持：

```text
LLM output
    ↓
strict parsing
    ↓
AgentDecision
```

不能：

```text
fuzzy
regex guess
auto repair
auto tool correction
```

例如：

```text
"get_inventory please"
```

不能自动猜：

```text
TOOL_CALL get_inventory
```

必须明确失败。

---

# 十一、Decision Schema

Architecture Decision 必须明确最终 schema。

至少：

```json
{
  "type": "FINAL",
  "content": "..."
}
```

以及：

```json
{
  "type": "TOOL_CALL",
  "tool_name": "...",
  "arguments": {}
}
```

需要明确：

```text
tool_call_id
```

是否属于：

```text
Required
Optional
Not supported
```

以及为什么。

---

# 十二、FINAL Decision

必须明确：

```text
FINAL
```

是否允许：

```text
content = ""
```

当前 Core Loop 已经规定：

```text
content 必须非空
```

Architecture Decision 必须保持一致，除非发现真实架构问题。

---

# 十三、TOOL_CALL Decision

必须明确：

```text
tool_name
arguments
```

是否允许：

```text
null
missing
extra fields
nested arbitrary JSON
```

以及：

```text
arguments
```

最终由谁做 schema validation。

---

# 十四、Invalid Decision

必须明确：

```text
Invalid JSON
Unknown type
Missing field
Wrong field type
FINAL + tool_name
TOOL_CALL + content
Unknown tool
Invalid arguments
```

分别属于：

```text
Parse Failure
Authorization Failure
Tool Argument Failure
```

还是统一：

```text
Agent Decision Failure
```

不要重新实现错误系统。

优先复用当前：

```text
AgentErrorCode
```

---

# 十五、LLM Retry

必须保持：

```text
Invalid Decision
    ↓
FAILED
```

本阶段：

**不增加自动 LLM Retry。**

不能：

```text
invalid JSON
 ↓
重新调用 LLM
```

不能：

```text
unknown tool
 ↓
重新让 LLM 猜
```

不能：

```text
invalid arguments
 ↓
自动修正
```

---

# 十六、Provider Contract 保护

Architecture Decision 必须明确：

如果未来采用 Tool Calling：

不能直接把：

```text
AgentDecision
```

塞进：

```text
LLMProvider
```

必须保持：

```text
Provider = 通用 LLM transport / response abstraction
AgentRuntime = Agent-specific decision interpretation
```

---

# 十七、DeepSeek Compatibility

检查当前 DeepSeek Provider / API 实际支持情况。

不要只根据网上资料。

以当前项目已经使用的：

```text
DeepSeek
deepseek-chat
OpenAI-compatible HTTP
```

实际实现能力为准。

如果无法在离线代码审查阶段证明某个 Provider-native capability：

记录：

```text
UNVERIFIED
```

不要假设支持。

本阶段禁止为了验证而调用真实 API。

---

# 十八、Decision

最终必须只选择：

```text
Option A
```

或者：

```text
Option B
```

或者：

```text
Option C
```

不能写：

```text
A/B 都可以
```

必须给出明确：

```text
Selected Architecture
```

以及：

```text
Why
Trade-offs
Future Migration Path
```

---

# 十九、推荐的评估标准

比较：

| 维度                   | Option A | Option B | Option C |
| -------------------- | -------: | -------: | -------: |
| Provider Contract 改动 |          |          |          |
| Agent Runtime 独立性    |          |          |          |
| Tool Schema          |          |          |          |
| Tool Authorization   |          |          |          |
| DeepSeek 兼容性         |          |          |          |
| 严格解析                 |          |          |          |
| 多 Provider 未来扩展      |          |          |          |
| 实现复杂度                |          |          |          |
| 安全边界                 |          |          |          |
| 当前项目适配度              |          |          |          |

不要做数字评分。

使用：

```text
Low
Medium
High
```

或者文字描述即可。

---

# 二十、必须检查现有测试影响

分析：

```text
tests/test_agent_runtime.py
tests/test_llm_provider.py
tests/test_llm_structured.py
```

说明未来正式接入后：

```text
哪些测试需要新增
哪些测试需要保持
哪些测试不能修改
```

但本阶段：

**不要修改测试代码。**

---

# 二十一、文档修改

只允许修改：

```text
docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
```

采用：

```text
append-only
```

新增：

```text
§ OQ-P5-1 Decision
```

不要修改前面的历史决策文字。

至少包含：

```text
Decision
Selected Option
Decision Protocol
Provider Boundary
Tool Schema Boundary
Authorization Boundary
Error Boundary
Retry Boundary
DeepSeek Compatibility
Migration Path
Rejected Alternatives
```

---

# 二十二、必须形成的最终架构

最终必须明确类似：

```text
AgentRuntime
      │
      │ Agent-specific
      ▼
AgentDecision
      │
      │
      ▼
LLM Response
      │
      ▼
LLMProvider
      │
      ▼
DeepSeekProvider
      │
      ▼
OpenAI-compatible HTTP
```

或者根据你的最终选择给出实际架构。

关键原则：

```text
Provider 不知道 Agent Policy
Provider 不知道 Tool Authorization
Provider 不知道 Conversation
Provider 不知道 Project Permission

AgentRuntime 负责：
Decision
Policy
Authorization
Loop
Tool Result
```

---

# 二十三、测试要求

本阶段不新增运行时代码。

可以执行：

```powershell
python -m pytest tests/test_agent_runtime.py -q
python -m pytest tests/test_llm_provider.py -q
python -m pytest tests/test_llm_structured.py -q
python -m compileall backend tests scripts
```

要求：

```text
No production code changes
No DB
No network
No DeepSeek
```

---

# 二十四、Git

完成后：

```powershell
git status --short
git diff --stat
git diff
```

只允许：

```text
docs/decisions/Phase/Phase 5 — Scope & Architecture Decision.md
```

发生修改。

commit：

```text
docs: close OQ-P5-1 LLM tool decision protocol
```

不要：

```text
push
PR
merge
rebase
```

---

# 二十五、最终报告

严格按照：

```text
【Phase 5 OQ-P5-1 DECISION COMPLETE】

1. Current Provider Contract

2. Problem

3. Options Compared
- Option A
- Option B
- Option C

4. Selected Architecture
- Option:
- Decision Protocol:
- Why:

5. AgentDecision Boundary

6. Provider Boundary

7. Tool Schema Boundary

8. Authorization Boundary

9. Error Boundary

10. Retry Boundary

11. DeepSeek Compatibility

12. Rejected Alternatives

13. Future Migration Path

14. Files
- Modified:
- Added:

15. Tests
- Agent Runtime:
- LLM Provider:
- Structured:
- compileall:

16. Git
- Commit:
- Push:
- Working tree:

17. Next Step
只描述下一阶段，不执行。

18. STOP
```

---

# 二十六、强制 STOP

完成 OQ-P5-1 后：

**立即停止。**

不要：

```text
实现真实 AgentLLM
接 DeepSeek
修改 Provider
修改 AgentRuntime
接 Router
接 Orchestrator
接 Conversation
做 Agent API
做 Observability
做 Persistence
```

下一步必须基于本 Architecture Decision 再开始。

**本阶段目标只有一个：**

```text
OQ-P5-1 CLOSED
```

完成后 STOP。
