# Phase 5 — Real DeepSeek Agent E2E Smoke Recovery

项目：

```text
D:\coding\ai\wms-ai-assistant
```

## 一、目标

承接上一轮：

```text
Provider-level DeepSeek Tool Calling        VERIFIED
Agent Authorization                         VERIFIED
Real Tool Handler Dispatch                  VERIFIED
Tool Execution                              BLOCKED
```

上一轮唯一阻塞：

```text
public.inventory 不存在
```

本阶段只解决：

> 在**不修改生产代码、不新增业务数据库表、不伪造数据**的前提下，找到当前环境中已有的真实只读 Tool，完成一次真正的：

```text
DeepSeek
 ↓
TOOL_CALL
 ↓
Agent Authorization
 ↓
Real Tool
 ↓
role=tool
 ↓
Second LLM Call
 ↓
FINAL
 ↓
Agent COMPLETED
 ↓
AIOrchestrationResult
```

---

# 二、绝对禁止

禁止：

```text
修改 AgentRuntime
修改 AgentLLM
修改 Provider
修改 DeepSeekProvider
修改 OpenAICompatibleClient
修改 Router
修改 Orchestrator
修改 Tool Handler
修改 Prompt
新增 production Tool
新增 public.inventory
新增 work_order
INSERT 测试业务数据
修改 DB Schema
修改 SQL Validator
修改 Executor
修改 Conversation
修改 Evidence
修改 Observability
```

禁止：

```text
Thinking Mode
Retry
Fallback
Streaming
Memory
Planning
HITL
Multi-Agent
LangGraph
MCP
Persistence
```

如果找不到可用的真实只读 Tool：

**直接报告 NOT VERIFIED，停止。**

不要为了通过 Smoke 创造条件。

---

# 三、第一步：盘点当前真实 Tool

先阅读：

```text
backend/app/tools/
backend/app/services/tool_execution_service.py
backend/app/projects/
```

搜索：

```text
ToolDefinition
ToolRegistry
handler
tool_name
ProjectCapabilities
GET_INVENTORY_DEFINITION
GET_WORK_ORDER_DEFINITION
```

输出当前实际注册的 Tool：

```text
Tool Name
Description
Parameters
Project Capability
Handler
是否只读
是否依赖不存在的业务表
```

特别检查：

```text
get_inventory
get_work_order
```

如果它们都依赖当前不存在的业务表：

**不要修改它们。**

继续检查是否存在其他：

```text
只读
已有
稳定
无需外部网络
无需写 DB
```

的 Tool。

---

# 四、第二步：选择真实 Tool

选择规则：

```text
优先级：

1. 当前项目已有真实 Tool
2. 只读
3. 不依赖不存在的业务表
4. 不产生 DB write
5. 参数简单
6. 可以被 DeepSeek 自然理解
```

不得创建新 Tool。

如果存在多个：

选择最简单的一个。

---

# 五、第三步：确认 Tool 数据源

如果 Tool 查询数据库：

只允许：

```text
SELECT
```

确认：

```text
DB writes = 0
```

可以使用只读查询：

```sql
information_schema
pg_catalog
```

但：

**不要修改 Tool Handler。**

如果现有 Tool 本身就是业务查询：

使用其现有实现。

---

# 六、第四步：设计 Smoke Question

Question 必须直接对应真实 Tool。

例如：

```text
查询当前知识库有多少篇文档。
```

如果没有对应 Tool：

不要强行使用这个问题。

根据实际 Tool schema 设计。

要求：

```text
一个问题
一个 Tool
一个 Tool Call
```

不要多跳。

---

# 七、第五步：Real LLM

保持：

```text
model = deepseek-chat
thinking = disabled
```

使用当前 `.env`。

禁止输出：

```text
API Key
Authorization
Base URL 中的敏感信息
```

---

# 八、第六步：限制调用

使用：

```text
max_steps = 2
max_tool_calls = 1
max_llm_calls = 2
```

如果当前 Policy 对：

```text
max_execution_time_ms
max_cost_tokens
```

有最小合法值要求：

遵循当前 contract。

---

# 九、第七步：强制单 Tool

如果当前 DeepSeek Chat Completion 支持：

```text
tool_choice
```

可以在**测试层**使用指定 Tool Choice 强制模型选择已经确认存在的真实 Tool。

但是：

### 不允许修改生产 Provider。

如果项目当前 Provider contract 没有暴露 `tool_choice`：

**不要为了 Smoke 修改 Provider contract。**

继续使用：

```text
tools=[authorized_tool]
```

让 DeepSeek 自然选择。

DeepSeek 当前 Chat Completion API 支持 `auto`、`required` 以及指定具体 function 的 `tool_choice`；不过本项目现阶段不要为了测试而扩大 Provider contract。

---

# 十、第八步：验证第一轮

必须得到：

```text
tool_calls = 1
```

验证：

```text
tool_name
tool_call_id exists
arguments is dict
arguments schema valid
usage available / unavailable
finish_reason
```

不要输出完整 tool_call_id。

只报告：

```text
tool_call_id = present
```

---

# 十一、第九步：真实 Tool Authorization

验证：

```text
tool_name
    ↓
Agent allowed_tools
    ↓
ProjectCapabilities
```

必须全部 PASS。

然后：

```text
ToolExecutionService
```

执行。

记录：

```text
handler_calls = 1
```

---

# 十二、第十步：Tool Result

这是本次最重要的新验证。

Tool 成功后：

必须构造：

```text
assistant
  tool_calls=[...]
```

然后：

```text
role=tool
tool_call_id=<same id>
content=<untrusted result>
```

验证：

```text
assistant.tool_calls[0].id
==
tool_message.tool_call_id
```

必须：

```text
PASS
```

同时 Tool Result 必须包含：

```text
【TOOL RESULT — UNTRUSTED DATA】
```

并且：

```text
不是 system
不是 developer
不是 policy
```

DeepSeek 官方 Tool Calls 文档明确采用这种 assistant tool call 后追加 `role=tool` 结果，再发起下一轮请求的方式。

---

# 十三、第十一步：第二次 DeepSeek

第二次必须真实发生：

```text
llm_calls = 2
```

第二次消息必须包含：

```text
assistant.tool_calls
role=tool
tool_call_id
```

第二次不得再次调用 Tool。

期望：

```text
tool_calls = empty
content != empty
```

通常期望：

```text
finish_reason = stop
```

如果实际 API 返回其他合法值：

记录真实值。

不要修改生产逻辑。

---

# 十四、第十二步：Agent Result

最终：

```text
status = COMPLETED
content != empty
tool_calls = 1
llm_calls = 2
```

并验证：

```text
tokens_used
token_budget_unverifiable
```

严格遵循真实 Provider usage。

不允许估算 token。

---

# 十五、第十三步：Orchestrator Result

通过：

```text
AIOrchestrator
```

验证：

```text
route = AGENT
```

以及：

```text
metadata.agent_status
metadata.agent_tool_calls
metadata.agent_llm_calls
metadata.request_id
```

不能包含：

```text
prompt
messages
tool result
api key
authorization
```

---

# 十六、第十四步：DB Safety

如果真实 Tool 使用数据库：

测试前后检查：

```text
DB writes = 0
```

不得：

```text
INSERT
UPDATE
DELETE
CREATE TABLE
DROP TABLE
ALTER TABLE
```

如果 Tool 目标表不存在：

**不要创建。**

直接：

```text
NOT VERIFIED
```

---

# 十七、Real LLM Test

继续使用：

```text
@pytest.mark.real_llm
```

测试文件：

```text
tests/test_agent_real_llm_smoke.py
```

不要增加新的 real LLM 测试框架。

---

# 十八、默认测试必须零真实调用

执行：

```powershell
Remove-Item Env:RUN_REAL_LLM_TESTS -ErrorAction SilentlyContinue
python -m pytest -q
```

必须：

```text
real_llm = skipped
```

并且：

```text
real DeepSeek calls = 0
```

---

# 十九、Real Smoke

只有明确开启：

```powershell
$env:RUN_REAL_LLM_TESTS="1"
python -m pytest -m real_llm tests/test_agent_real_llm_smoke.py -q -s
```

才允许真实 DeepSeek。

---

# 二十、不要修改 pytest 默认行为

允许：

```text
pytest.ini
```

只保留：

```text
real_llm marker
```

不得：

```text
默认启用 real_llm
默认联网
默认 DB 写入
```

---

# 二十一、如果仍然没有合适 Tool

如果盘点结果：

```text
get_inventory → public.inventory 不存在
get_work_order → public.work_order 不存在
其他 Tool → 不满足只读/稳定/真实数据条件
```

那么：

**不要继续实现。**

报告：

```text
REAL AGENT TOOL CALLING = NOT VERIFIED

Reason:
Current environment has no usable existing real read-only Tool.

Production changes:
0

DB schema changes:
0
```

停止。

---

# 二十二、如果找到可用 Tool 但 DeepSeek 没有调用

不要立即修改 Prompt。

记录：

```text
Provider Tool Calling = VERIFIED
Model Tool Selection = NOT VERIFIED
```

停止。

---

# 二十三、如果 Tool 成功但第二轮失败

分类：

```text
Tool Result Injection / Message Construction
Second LLM Call
Provider Response
Final Decision
```

不要修改生产代码。

先报告具体失败位置。

---

# 二十四、回归测试

至少：

```powershell
python -m pytest tests/test_agent_llm_adapter.py -q
python -m pytest tests/test_agent_runtime.py -q
python -m pytest tests/test_agent_orchestrator_integration.py -q
python -m compileall -q backend tests scripts
```

然后：

```powershell
Remove-Item Env:RUN_REAL_LLM_TESTS -ErrorAction SilentlyContinue
python -m pytest -q
```

---

# 二十五、Git

如果只是：

```text
测试文件调整
```

可以提交：

```text
test: verify real DeepSeek agent tool calling
```

如果需要修改生产代码：

**不要 commit。**

如果 Smoke 未验证成功：

不要创建“成功”提交。

不要：

```text
push
PR
merge
rebase
```

---

# 二十六、最终报告

严格输出：

```text
【Phase 5 REAL DEEPSEEK AGENT E2E RECOVERY】

1. 当前可用真实 Tool
2. Tool 数据源
3. Smoke Question
4. First LLM Call
5. Tool Authorization
6. Tool Execution
7. assistant.tool_calls
8. role=tool
9. tool_call_id consistency
10. Second LLM Call
11. Final Decision
12. Agent Result
13. Orchestrator Result
14. Token Usage
15. DB writes
16. Offline Regression
17. Real LLM Call Count
18. compileall
19. Git Diff
20. Commit
21. Failure / Limitation
22. Conclusion
```

Conclusion 只能：

```text
REAL AGENT TOOL CALLING = VERIFIED
```

或者：

```text
REAL AGENT TOOL CALLING = NOT VERIFIED
```

---

# 二十七、强制 STOP

完成后立即停止。

不要进入：

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

**本阶段只解决真实 DeepSeek Agent Tool Calling E2E 验证。**
