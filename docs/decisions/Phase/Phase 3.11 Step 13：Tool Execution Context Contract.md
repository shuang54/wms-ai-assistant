你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.11 Step 13：Tool Execution Context Contract

## 一、阶段目标

Phase 3.11 Step 12 已完成 Tool Runtime Failure Contract。

当前执行链：

```text
ToolChatService
    ↓
ToolExecutionService
    ↓
Capability
    ↓
ToolRegistry
    ↓
Real get_inventory
    ↓
PostgreSQL
    ↓
ToolResult
```

本阶段唯一目标：

**为 Tool 执行建立一个最小、稳定、不可变的 Execution Context。**

最终形成：

```text
ToolChatService
    ↓
ToolExecutionService
    ↓
ToolExecutionContext
    ├── request_id
    ├── project_id
    ├── tool_call_id
    └── round
    ↓
ToolRegistry
    ↓
Tool
```

---

# 二、严格范围

## 允许

* 新增 `ToolExecutionContext`
* 为 ToolExecutionService 增加 context 参数
* ToolChatService 创建并传递 context
* 增加 context contract tests
* 增加少量测试辅助代码
* 更新 architecture / evaluation 文档
* 使用 Fake / Scripted LLM
* 使用现有真实 `get_inventory` 做最小验证

## 禁止

不要修改：

```text
AI Router
AIOrchestrator
ToolArgumentExtractor
ToolRegistry 核心行为
get_inventory Handler
get_inventory SQL
Business Semantic
Database Schema
ProjectRegistry
```

不要：

```text
数据库落库
Tool Audit Table
Tool History Table
Dashboard
Usage Analytics
Retry
Circuit Breaker
Timeout Orchestration
Parallel Tool Calling
Agent
LangGraph
MCP
Memory
Planning
```

不要增加：

```text
get_work_order
第三个 Tool
```

不要修改 SQL。

不要修改真实 Tool 的业务逻辑。

---

# 三、Step 1：先阅读真实代码

先阅读：

```text
backend/app/services/tool_execution_service.py
backend/app/services/tool_chat_service.py
backend/app/api/tool_chat.py
backend/app/tools/registry.py
backend/app/tools/base.py
backend/app/tools/get_inventory.py
```

以及：

```text
tests/test_tool_chat_execution_boundary.py
tests/test_tool_chat_capability.py
tests/test_tool_chat_project_context.py
tests/test_tool_chat_real_get_inventory.py
tests/test_tool_runtime_failure_contract.py
tests/test_tool_chat_architecture_contract.py
```

重点确认：

1. 当前 ToolCall DTO 的真实字段
2. 当前 `ToolChatService` 每一轮如何计数
3. 当前 `ToolExecutionService.execute()` 签名
4. 当前 `project_id` 从哪里进入 ExecutionService
5. 当前 API 是否已有 request identifier
6. 当前 Fake / Scripted LLM 是否已经提供稳定 ToolCall id

**不要假设字段名称。**

---

# 四、ToolExecutionContext DTO

新增一个最小 frozen DTO。

推荐位置：

```text
backend/app/services/tool_execution_context.py
```

名称：

```python
ToolExecutionContext
```

至少包含：

```text
request_id
project_id
tool_call_id
round
```

建议类型：

```text
request_id: str
project_id: str | None
tool_call_id: str | None
round: int
```

但必须根据现有项目真实类型调整。

---

# 五、Context Contract

必须满足：

## 1. Immutable

```text
frozen = True
```

创建之后不能修改：

```python
context.request_id = "xxx"
```

必须失败。

---

## 2. request_id

用途：

**标识一次用户请求 / ToolChat execution。**

要求：

* 非空
* 稳定
* 同一次 `ToolChatService.chat()` 中保持不变
* 不包含 secret
* 不使用数据库 ID
* 不依赖 Tool 名称

不要增加数据库 UUID 表。

如果项目已经存在 request_id 工具/DTO：

**优先复用。**

不要创建第二套 request ID 系统。

---

# 六、project_id

如果当前 ToolChatService 已经接受：

```text
project_id
```

那么：

```text
ToolExecutionContext.project_id
```

必须与当前授权作用域保持一致。

例如：

```text
project-a
```

执行：

```text
get_inventory
```

Context：

```text
project_id = project-a
```

不能：

```text
project-a
```

经过 ToolChatService 后变成：

```text
project-b
```

---

# 七、tool_call_id

必须来自：

**LLM Function Calling ToolCall 的真实 id。**

不要重新生成一个新的 Tool ID 替换它。

例如 LLM 返回：

```text
call_123
```

那么：

```text
ToolExecutionContext.tool_call_id
=
call_123
```

必须保持一致。

如果当前 ToolCall DTO 没有稳定 ID：

先检查真实实现。

如果当前协议确实没有：

**不要自行修改 LLM provider contract。**

可以：

```text
tool_call_id = None
```

并在限制中记录。

---

# 八、round

表示：

**当前 ToolChat Multi-Step 的第几轮 Tool Execution。**

例如：

```text
第一轮:
round = 1

第二轮:
round = 2

第三轮:
round = 3
```

要求：

* 从 1 开始
* 每轮递增
* 同一轮内如果当前架构允许多个 ToolCall，必须根据现有 contract 处理
* 当前项目实际上规定每轮最多一个 ToolCall，因此保持：

```text
one round = one Tool execution
```

不要修改 Multi-Step 规则。

---

# 九、Context 生命周期

一个用户请求：

```text
POST /api/chat/with-tools
```

进入：

```text
ToolChatService.chat()
```

应该形成：

```text
request_id = R1
```

然后：

```text
Round 1
ToolExecutionContext(
    request_id=R1,
    project_id=project-a,
    tool_call_id=call_001,
    round=1
)

Round 2
ToolExecutionContext(
    request_id=R1,
    project_id=project-a,
    tool_call_id=call_002,
    round=2
)
```

注意：

```text
request_id
```

相同。

```text
tool_call_id
round
```

不同。

---

# 十、不要把 Context 放入 Tool arguments

非常重要。

当前：

```text
Tool arguments
```

仍然只能包含：

```json
{
  "material_code": "MAT-001"
}
```

不能变成：

```json
{
  "material_code": "MAT-001",
  "request_id": "...",
  "project_id": "...",
  "tool_call_id": "...",
  "round": 1
}
```

原因：

**Execution Context 与 Business Tool Arguments 是两个不同概念。**

Tool Handler 不应该因为增加 Observability Context 而改变业务参数 schema。

---

# 十一、不要把 Context 放进 LLM Message

不要把：

```text
request_id
project_id
tool_call_id
round
```

自动注入：

```text
system message
user message
assistant message
tool message
```

这些属于：

```text
Runtime Execution Context
```

不是 Prompt Context。

特别是：

```text
project_id
```

不能因为加入 Context 就变成 LLM 可以控制的字段。

Project authorization 仍然由服务器端控制。

---

# 十二、ToolExecutionService

修改：

```text
backend/app/services/tool_execution_service.py
```

目标：

让：

```text
execute()
```

可以接收：

```text
ToolExecutionContext
```

例如概念上：

```python
execute(
    tool_name,
    *,
    arguments=None,
    context=None,
)
```

但：

**以当前真实 API 风格为准。**

不要机械照抄。

---

# 十三、ExecutionService 的职责

增加 Context 后：

ToolExecutionService 负责：

```text
Capability
+
Execution Context 接收
+
Registry Execution
```

但：

**不要让 ExecutionService 自己生成 request_id。**

也不要：

```text
retry
logging framework
audit persistence
```

它只是：

```text
Execution Boundary
```

---

# 十四、Registry 不负责 Context

保持：

```text
ToolExecutionService
    ↓
ToolRegistry.execute(tool_name, arguments)
```

不要修改 Registry contract，让 Registry 接收：

```text
request_id
project_id
round
```

本阶段：

**Registry 不知道 Execution Context。**

这样可以保持：

```text
Registry = Tool Definition + Schema + Handler dispatch
```

而：

```text
ExecutionService = Runtime Boundary
```

---

# 十五、Tool Handler 不接 Context

保持：

```text
get_inventory(arguments)
```

而不是：

```text
get_inventory(arguments, context)
```

禁止修改：

```text
get_inventory
```

因为 Context 属于执行层，不属于业务参数。

---

# 十六、ToolChatService

修改：

```text
backend/app/services/tool_chat_service.py
```

让它成为：

**Context 创建者。**

一次：

```text
chat()
```

创建：

```text
request_id
```

然后每一轮 ToolCall：

```text
tool_call_id = ToolCall.id
round = current_round
project_id = execution scope
```

构造：

```text
ToolExecutionContext
```

传给：

```text
ToolExecutionService.execute()
```

---

# 十七、request_id 生成策略

优先检查当前项目是否已经有：

```text
request_id
correlation_id
trace_id
```

如果已有：

**复用。**

如果没有：

可以使用标准：

```python
uuid.uuid4()
```

生成。

要求：

* 每次 `chat()` 一个 request_id
* 同一次 chat 多轮 ToolCall 共享 request_id
* 不暴露给 LLM
* 不写数据库
* 不改变 API Response contract

不要新增 request-id middleware。

不要新增 HTTP header。

不要扩展 API DTO。

---

# 十八、API 层

本阶段：

```text
backend/app/api/tool_chat.py
```

原则上：

**不要修改 API contract。**

如果 request_id 可以完全在 Service 内生成：

保持 API 零修改。

只有在真实实现中发现 API 已经拥有 request_id：

才允许向下传递。

不要为了 Context 强行增加：

```text
request_id
```

HTTP request body 字段。

---

# 十九、测试

新增：

```text
tests/test_tool_execution_context.py
```

至少覆盖：

## Test 1

Context 创建成功。

## Test 2

Context frozen。

## Test 3

request_id 非空。

## Test 4

project_id 正确。

## Test 5

tool_call_id 正确。

## Test 6

round 从 1 开始。

## Test 7

不同 ToolCall 产生不同 tool_call_id。

## Test 8

同一次 chat：

```text
request_id 相同
```

## Test 9

多轮：

```text
round = 1, 2, 3
```

## Test 10

Tool arguments 不包含 Context。

## Test 11

LLM messages 不包含 Context。

## Test 12

Registry 不接收 Context。

## Test 13

Handler 不接收 Context。

---

# 二十、ToolChat E2E Context Test

增加最小集成测试：

```text
Fake LLM
 ↓
ToolCall(call_001)
 ↓
ToolExecutionService
 ↓
Real/Fake Tool
```

验证 Context：

```text
request_id = R
project_id = project-a
tool_call_id = call_001
round = 1
```

第二轮：

```text
request_id = R
project_id = project-a
tool_call_id = call_002
round = 2
```

最终断言：

```text
request_id_1 == request_id_2

tool_call_id_1 != tool_call_id_2

round_1 == 1
round_2 == 2
```

---

# 二十一、不要依赖真实 DB 做全部 Context 测试

绝大部分 Context 测试：

使用：

```text
Fake Tool
Recording ExecutionService
Recording Tool
Scripted LLM
```

即可。

只增加：

**1 个真实 get_inventory DB regression**

确认 Context 的加入没有破坏：

```text
ToolExecutionService
→ Real get_inventory
→ PostgreSQL
```

不要重复 Step 11/12 的所有 DB 测试。

---

# 二十二、Architecture Contract

修改：

```text
tests/test_tool_chat_architecture_contract.py
```

增加：

```text
C15 Tool Execution Context Boundary
```

至少锁定：

```text
ToolChatService
    ↓
creates ToolExecutionContext
    ↓
ToolExecutionService
```

并且：

```text
Registry
    X
does not own context
```

```text
Tool Handler
    X
does not own context
```

```text
LLM
    X
cannot control project_id
```

```text
Tool arguments
    X
do not contain runtime context
```

---

# 二十三、Context Security

增加测试：

Context 中不能出现：

```text
API key
password
DATABASE_URL
Authorization
Bearer
connection string
```

Context 只允许：

```text
request_id
project_id
tool_call_id
round
```

如果需要扩展字段：

**本阶段不要扩展。**

---

# 二十四、Immutability

不仅：

```text
ToolExecutionContext
```

需要 frozen。

还要确保：

```text
ToolExecutionService
```

不会修改传入 Context。

例如：

```text
context.round = 2
```

不能发生。

每轮必须：

```text
new context
```

而不是：

```text
mutate existing context
```

---

# 二十五、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11 Step 13 — Tool Execution Context Contract.md
```

记录：

## 1. Context Schema

```text
request_id
project_id
tool_call_id
round
```

## 2. Lifecycle

```text
Chat
 ↓
request_id
 ↓
Round 1
 ↓
ToolCall ID
 ↓
Context
 ↓
Execution
 ↓
Round 2
 ↓
ToolCall ID
 ↓
Context
```

## 3. Boundary

```text
ToolChatService
    ↓
ToolExecutionContext
    ↓
ToolExecutionService
    ↓
ToolRegistry
    ↓
Tool
```

## 4. Isolation

记录：

```text
LLM cannot control project_id
Tool arguments don't contain context
Registry doesn't own context
Handler doesn't own context
```

## 5. Tests

记录：

```text
Context DTO = PASS
Lifecycle = PASS
Multi-Step = PASS
Security = PASS
Real Tool Regression = PASS
```

## 6. Limitations

必须明确：

```text
No persistence
No audit log
No dashboard
No tracing backend
No request middleware
```

---

# 二十六、测试命令

先执行：

```powershell
python -m pytest -q `
tests/test_tool_execution_context.py `
tests/test_tool_chat_service.py `
tests/test_tool_chat_execution_boundary.py `
tests/test_tool_chat_architecture_contract.py `
tests/test_tool_runtime_failure_contract.py
```

然后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_tool_execution_context.py
```

然后：

```powershell
python -m pytest -q
```

最后：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

如果本次 DB 全量仍然因为环境 TCP 30 秒延迟无法完成：

**如实记录，不要修改代码解决环境问题。**

编译：

```powershell
python -m compileall -q backend
```

继续使用现有 LSP。

不要安装新的 lint 工具。

---

# 二十七、失败处理

如果发现：

```text
ToolCall ID 无法获取
request_id 已存在另一套机制
project_id 无法安全传递
ToolExecutionService API 与 Context 不兼容
```

不要立即重构。

先定位真实原因。

如果需要改变：

```text
LLM Provider contract
ToolRegistry contract
API contract
Tool Handler contract
```

**立即停止并报告。**

本阶段不允许扩大范围。

---

# 二十八、Baseline

Phase 3.11 Step 12：

```text
Full no DB:
2780 passed / 334 skipped

Full DB:
未完成（环境 TCP 延迟）

Production backend:
0 修改

DB writes:
0
```

目标：

```text
既有 no-DB 测试全部通过
新增 Context 测试全部通过
Real get_inventory regression 通过
```

---

# 二十九、最终报告格式

完成后严格按照：

```text
【Phase 3.11 Step 13 COMPLETE】

1. 新增文件
2. 修改文件
3. ToolExecutionContext
4. request_id
5. project_id
6. tool_call_id
7. round
8. Context Lifecycle
9. ToolChatService
10. ToolExecutionService
11. ToolRegistry Isolation
12. Tool Handler Isolation
13. LLM Context Isolation
14. Arguments Isolation
15. Immutability
16. Security
17. Architecture Contract C15
18. Real get_inventory Regression
19. 测试结果
20. compile / LSP
21. DB writes
22. Network
23. Real LLM
24. 当前限制
25. 是否修改 API / Registry / Tool
```

最后固定输出：

```text
Real Tool = get_inventory
get_work_order = DEFERRED
Third Tool = NOT ADDED

ToolExecutionContext = IMPLEMENTED

request_id = PASS
project_id = PASS
tool_call_id = PASS
round = PASS

Persistence = NOT IMPLEMENTED
Audit Log = NOT IMPLEMENTED
Dashboard = NOT IMPLEMENTED
Tracing Backend = NOT IMPLEMENTED

Agent = NOT IMPLEMENTED
MCP = NOT IMPLEMENTED
LangGraph = NOT IMPLEMENTED
Memory = NOT IMPLEMENTED
Planning = NOT IMPLEMENTED

Phase 3.11 Step 13 完成。

立即停止。
不要进入 Step 14。
```

# 三十、最重要规则

严格执行：

```text
阅读
→ Context Contract
→ DTO
→ ToolChatService 接入
→ ExecutionService 接入
→ Isolation Tests
→ C15
→ Real Tool Regression
→ Full Regression
→ 汇报
→ STOP
```

**本阶段不做任何持久化。**

**本阶段不做 Audit。**

**本阶段不做 Dashboard。**

**本阶段不接 get_work_order。**

**本阶段不进入 Agent / MCP / LangGraph。**

完成后立即停止，等待下一步指令。
