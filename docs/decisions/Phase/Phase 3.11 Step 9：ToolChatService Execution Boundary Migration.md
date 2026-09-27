你现在开始实现：

# Phase 3.11 Step 9：ToolChatService Execution Boundary Migration

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

Step 8 已经完成 ToolChatService 勘察，结论是：

```text
ToolChatService
    ↓
ToolRegistry.execute
```

当前直接执行，绕过：

```text
ToolExecutionService
```

这是 Step 8 唯一明确发现的执行边界问题。

本阶段只解决：

> **让 ToolChatService 复用现有 ToolExecutionService，统一 Tool 执行入口。**

目标架构：

```text
ToolChatService
    ↓
ToolExecutionService
    ↓
ToolRegistry
    ↓
Tool Handler
```

同时保持：

```text
LLM Function Calling
        ↓
ToolChatService
        ↓
while multi-step loop
```

不变。

---

# 二、严格范围

## 允许

允许：

* 修改 `ToolChatService`
* 修改 `tool_chat.py` 的服务装配
* 新增/修改 ToolChatService migration tests
* 更新 Step 9 evaluation 文档
* 更新 architecture 文档
* 为测试注入 `ToolExecutionService`
* 必要时增加极少量测试 fixture

## 禁止

本阶段禁止修改：

```text
AI Router
AIOrchestrator
ToolArgumentExtractor
ToolRegistry
Tool Handler
Tool Definition
真实 get_inventory
真实 get_work_order
```

禁止：

```text
Agent
LangGraph
MCP
Memory
Planning
Retry
Cache
Parallel Tool Calling
```

禁止增加：

```text
第二个 Multi-Step Runtime
新的 Tool Loop
新的 Tool Selection
新的 Argument Parser
```

禁止解决：

```text
Capability
ProjectContext
真实 Tool 接入
权限系统
审计系统
```

这些留到后续独立 Step。

---

# 三、第一步：先阅读真实代码

不要假设接口。

阅读：

```text
backend/app/services/tool_chat_service.py
backend/app/services/tool_execution_service.py
backend/app/api/tool_chat.py
backend/app/tools/registry.py
backend/app/tools/mock_tools.py
backend/app/llm/tool_schema.py
```

以及：

```text
tests/test_tool_chat_service.py
tests/test_tool_chat_api.py
tests/test_tool_chat_service_characterization.py
tests/test_tool_chat_architecture_contract.py
```

确认：

1. ToolChatService 当前如何创建。
2. ToolRegistry 当前如何传入。
3. ToolExecutionService 当前构造函数。
4. ToolExecutionService 当前 `execute()` 签名。
5. ToolChatService 当前多步循环位置。
6. ToolChatService 当前 ToolResult 错误处理。
7. API 当前如何创建 ToolChatService。
8. 现有测试是否依赖具体 Registry。
9. 是否存在 module-level singleton。
10. 是否存在已有 Fake / Recording ToolExecutionService。

**优先复用已有测试基础设施。**

不要重新造一套 Mock Framework。

---

# 四、目标架构

迁移前：

```text
ToolChatService
    │
    ├── definitions_to_openai_tools()
    │
    ├── LLM
    │
    ├── ToolCall
    │
    └── registry.execute()
            ↓
        ToolRegistry
```

迁移后：

```text
ToolChatService
    │
    ├── definitions_to_openai_tools()
    │
    ├── LLM
    │
    ├── ToolCall
    │
    └── ToolExecutionService.execute()
            ↓
        ToolRegistry
```

注意：

**ToolChatService 仍然负责 Multi-Step Orchestration。**

ToolExecutionService 仍然只负责：

```text
ONE Tool execution
```

绝对不能把 while loop 移进 ToolExecutionService。

---

# 五、ToolExecutionService 注入

优先采用依赖注入，而不是在每次 ToolCall 时临时 new。

例如概念上：

```python
ToolChatService(
    ...,
    execution_service=ToolExecutionService(registry)
)
```

具体接口必须根据当前真实代码决定。

不要强行照搬示例。

要求：

```text
ToolChatService
    ↓
持有 execution_service
```

而不是：

```text
每次 ToolCall
    ↓
new ToolExecutionService(...)
```

---

# 六、Registry 来源保持不变

非常重要。

当前：

```text
ToolChatService.chat(
    message,
    registry=registry
)
```

不要为了 Step 9 大规模修改接口。

如果当前 API / Service contract 是：

```python
chat(message, *, registry)
```

则保持兼容。

允许：

```text
registry
    ↓
ToolExecutionService
```

在本次调用链建立执行边界。

如果需要重新构造：

```text
ToolExecutionService(registry)
```

可以做，但不要修改 Registry 本身。

---

# 七、Capability 本阶段明确不解决

Step 8 已确认：

```text
ToolChatService 当前没有 Capability
```

本阶段不要加入：

```text
capabilities
project_id
ProjectContext
```

原因：

当前 ToolChatService 使用的是：

```text
Mock Tools
```

本阶段目标只是：

```text
统一 Execution Boundary
```

因此可以暂时：

```text
ToolExecutionService(registry)
```

保持当前：

```text
capabilities=None
```

语义。

但必须在 evaluation 文档中明确记录：

```text
Capability enforcement intentionally deferred.
```

不要把：

```text
capabilities=None
```

描述成已经完成安全权限控制。

---

# 八、Function Calling 行为必须完全保持

以下行为不得改变：

## 1. Tool Selection

仍然：

```text
LLM → ToolCall.name
```

ToolChatService 不增加 Router。

---

## 2. Argument

仍然：

```text
LLM → ToolCall.arguments
```

ToolChatService 不增加：

```text
ToolArgumentExtractor
```

也不解析自然语言。

---

## 3. Schema Validation

仍然：

```text
ToolExecutionService
    ↓
ToolRegistry
    ↓
validate_arguments
```

ToolChatService 不直接调用：

```text
validate_arguments()
```

---

## 4. Multi-Step

仍然：

```text
while True
```

保持：

```text
max_rounds
```

保持：

```text
每轮最多一个 ToolCall
```

保持：

```text
无 ToolCall → 正常结束
预算耗尽 → ToolCallingBudgetExceededError
多个 ToolCall → MultipleToolCallsError
```

---

# 九、ToolResult 错误行为必须保持

这是本阶段最重要的回归点之一。

原行为：

```text
ToolRegistry.execute()
        ↓
ToolResult(success=False)
        ↓
role=tool
        ↓
LLM继续
```

迁移后必须仍然：

```text
ToolExecutionService.execute()
        ↓
ToolResult(success=False)
        ↓
role=tool
        ↓
LLM继续
```

不要因为增加 ExecutionService 而改变：

```text
ToolError
ToolResult
LLM continuation
```

语义。

---

# 十、异常边界

特别检查：

```text
AIOrchestratorCapabilityError
```

不要让 ToolChatService 获得 AIOrchestrator 的异常依赖。

当前 ToolExecutionService 为了兼容主链路，存在 delayed import：

```text
ai_orchestrator_service
```

Step 9 不要顺便解决这个架构问题。

如果 ToolExecutionService 在：

```text
capabilities=None
```

情况下完全不会触发 CapabilityError，

则保持现状即可。

不要扩大本阶段范围。

---

# 十一、API 行为必须保持

现有：

```text
POST /api/chat/with-tools
```

必须保持：

```text
Path 不变
Request DTO 不变
Response DTO 不变
HTTP status 不变
错误结构不变
```

不要修改 API contract。

API 只需要调整：

```text
ToolChatService
    ↓
ToolExecutionService
```

的组装方式。

---

# 十二、测试新增

新增：

```text
tests/test_tool_chat_execution_boundary.py
```

至少覆盖：

## Test 1：ToolChatService 使用 ExecutionService

使用 Recording/Fake ExecutionService：

```text
LLM
 ↓
ToolCall
 ↓
RecordingExecutionService
```

验证：

```text
execution_service.execute()
```

被调用。

并验证：

```text
registry.execute()
```

不会由 ToolChatService 直接调用。

---

## Test 2：arguments 原样传递

例如：

```json
{
  "material_code": "MAT-001",
  "warehouse_code": "A01"
}
```

验证：

```text
ToolChatService
        ↓
ToolExecutionService
```

收到完全相同的：

```python
arguments
```

不能：

```text
rename
drop
add
normalize
```

---

## Test 3：ToolResult 原样返回

Fake ExecutionService：

```text
ToolResult(
    success=True,
    data={...}
)
```

验证最终 LLM 收到的：

```text
role=tool
```

内容与原行为一致。

---

## Test 4：Tool failure continuation

Fake ExecutionService：

```text
ToolResult(success=False)
```

验证：

```text
LLM
 ↓
Tool
 ↓
failure
 ↓
LLM继续
```

仍然成立。

---

## Test 5：Multi-Step

模拟：

```text
LLM → Tool A
LLM → Tool B
LLM → final answer
```

验证：

```text
ExecutionService.execute()
```

调用 2 次。

并确认：

```text
ToolExecutionService
```

本身没有 loop。

---

## Test 6：Budget

模拟：

```text
LLM持续要求 Tool
```

验证：

```text
ToolCallingBudgetExceededError
```

仍然发生。

不要改变 budget semantics。

---

## Test 7：Multiple Tool Calls

LLM 返回：

```text
2 个 ToolCall
```

验证：

```text
MultipleToolCallsError
```

并确认：

```text
ExecutionService.execute()
```

调用次数：

```text
0
```

---

## Test 8：Malformed Tool Call

保持 Step 8 characterization：

```text
LLMToolCallFormatError
```

必须：

```text
ExecutionService.execute() == 0
```

---

# 十三、Architecture Contract

修改：

```text
tests/test_tool_chat_architecture_contract.py
```

更新 C2。

Step 8：

```text
C2:
ToolChatService → ToolRegistry.execute
CURRENT VIOLATION
```

Step 9 应变成：

```text
C2:
ToolChatService → ToolExecutionService → ToolRegistry
PASS
```

增加静态检查：

```text
ToolChatService
```

源码中不得直接调用：

```text
registry.execute(...)
```

允许调用：

```text
execution_service.execute(...)
```

同时反向确认：

```text
ToolExecutionService
```

仍然：

```text
ONE Tool
```

不得出现：

```text
while
for multi-tool orchestration
LLM
ToolChatService
```

---

# 十四、保持 Selection / Argument Boundary

必须保证 Step 3 / Step 6 的架构边界不被破坏。

最终：

```text
ToolChatService
    ├── LLM Selection
    ├── LLM Arguments
    └── Multi-Step Loop
            ↓
      ToolExecutionService
            ↓
       ToolRegistry
            ↓
          Tool
```

不要出现：

```text
Router
    ↓
ToolChatService
```

也不要出现：

```text
ToolChatService
    ↓
ToolArgumentExtractor
```

更不要出现：

```text
ToolExecutionService
    ↓
LLM
```

---

# 十五、必须保留两条链路

完成后架构应该明确为：

## 链路 A：AI Orchestrator

```text
Question
 ↓
AIOrchestrator
 ↓
Router
 ↓
RouteDecision.tool_name
 ↓
ToolArgumentExtractor
 ↓
ToolExecutionService
 ↓
ToolRegistry
 ↓
Tool
```

特点：

```text
ONE Tool
```

---

## 链路 B：ToolChatService

```text
User Message
 ↓
ToolChatService
 ↓
LLM Function Calling
 ↓
ToolCall
 ↓
ToolExecutionService
 ↓
ToolRegistry
 ↓
Tool
 ↓
ToolResult
 ↓
LLM
 ↓
下一轮
```

特点：

```text
Multi-Step
```

---

# 十六、不要合并两个 Orchestration

这是本阶段最重要的架构规则：

不要尝试：

```text
ToolChatService → AIOrchestrator
```

也不要：

```text
AIOrchestrator → ToolChatService
```

更不要：

```text
Router + Function Calling
```

混合成一个新的复杂层。

两个 Orchestrator 保持独立。

共享的只有：

```text
ToolRegistry
ToolResult
ToolExecutionService
Tool Handler
```

其中：

```text
ToolExecutionService
```

是统一的单 Tool Execution Boundary。

---

# 十七、API / Singleton

检查：

```text
backend/app/api/tool_chat.py
```

当前 module-level：

```text
_tool_chat_service
_tool_registry
```

如果当前结构适合：

可以改成：

```text
_tool_execution_service
```

但不要为了依赖注入重构整个 API。

保持：

```text
module-level singleton
```

风格。

不要引入：

```text
FastAPI Depends
```

不要因为 FastAPI 支持 Dependency Injection 就重构现有项目。

项目当前已经明确使用 module-level singleton 风格，本阶段保持一致。

---

# 十八、禁止修改 Mock Tool 行为

不要修改：

```text
get_inventory mock
get_work_order mock
```

Tool Definition 不变。

Tool Schema 不变。

Tool Handler 不变。

Mock 返回值不变。

---

# 十九、Step 8 Characterization 必须保留

不要删除：

```text
tests/test_tool_chat_service_characterization.py
```

Step 8 的 characterization 是迁移前行为基线。

迁移完成后：

```text
Step 8 characterization
```

应该全部通过。

如果因为 ExecutionService 引入导致测试变化：

优先修改测试中的依赖注入方式，而不是改变生产行为。

---

# 二十、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11 Step 9 — ToolChatService Execution Boundary Migration.md
```

记录：

## 1. Migration Before

```text
ToolChatService
 ↓
ToolRegistry.execute
```

## 2. Migration After

```text
ToolChatService
 ↓
ToolExecutionService
 ↓
ToolRegistry.execute
```

## 3. Behavior Preserved

记录：

```text
Function Calling = PASS
Argument forwarding = PASS
Schema Validation = PASS
Multi-Step = PASS
Budget = PASS
Multiple Tool Call rejection = PASS
Tool failure continuation = PASS
Malformed ToolCall = PASS
```

## 4. Capability

明确：

```text
Capability = NOT IMPLEMENTED
```

原因：

```text
Step 9 only unifies execution boundary.
```

## 5. Project Context

明确：

```text
ProjectContext = NOT IMPLEMENTED
```

## 6. Real Tools

明确：

```text
Real Tool integration = NOT IMPLEMENTED
```

---

# 二十一、安全要求

迁移前后都必须保证：

```text
ToolChatService
    ↓
ToolExecutionService
    ↓
ToolRegistry
```

仍然不能：

```text
ToolChatService → Handler
ToolChatService → SQL
ToolChatService → DB
ToolChatService → HTTP
ToolChatService → Shell
ToolChatService → File
```

必须保持：

```text
ToolChatService
```

不知道 Tool Handler 实现细节。

---

# 二十二、测试命令

先运行定向：

```powershell
python -m pytest -q tests/test_tool_chat_execution_boundary.py tests/test_tool_chat_service.py tests/test_tool_chat_api.py tests/test_tool_chat_architecture_contract.py tests/test_tool_chat_service_characterization.py
```

然后：

```powershell
python -m pytest -q
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

最后：

```powershell
python -m compileall -q backend
```

如果项目已有 LSP 检查：

继续执行。

不要安装新的 lint 工具。

---

# 二十三、回归要求

必须确认：

```text
Step 7 全量基线：
2909 passed / 41 skipped
```

Step 9 后：

允许：

```text
新增测试数量增加
```

但：

```text
既有测试不得失败
```

特别关注：

```text
tests/test_tool_chat_service.py
tests/test_tool_chat_api.py
tests/test_ai_orchestrator.py
tests/test_tool_execution_service.py
tests/test_tool_architecture_contract.py
```

---

# 二十四、失败处理

如果发现：

```text
ToolChatService
```

无法直接复用：

```text
ToolExecutionService
```

不要修改 ToolExecutionService 去适配 Multi-Step。

正确做法：

停止并报告：

```text
兼容性问题：
原因：
最小解决方案：
是否需要架构决策：
```

不要自行扩大范围。

---

如果发现：

```text
Capability
ProjectContext
```

需要改变才能完成迁移：

也不要在本阶段实现。

记录：

```text
Deferred:
Capability
ProjectContext
```

---

# 二十五、最终报告

完成后严格按照：

```text
【Phase 3.11 Step 9 COMPLETE】

1. 修改文件
2. Execution Boundary
3. ToolChatService 行为回归
4. Multi-Step
5. ToolResult
6. Architecture Contract
7. API
8. Capability
9. Project Context
10. Tests
11. compile / LSP
12. DB writes
13. Network
14. 是否修改 ToolExecutionService
15. 当前限制
```

重点给出：

```text
Before:

ToolChatService
    ↓
ToolRegistry.execute


After:

ToolChatService
    ↓
ToolExecutionService
    ↓
ToolRegistry.execute
```

以及：

```text
AIOrchestrator
    ↓
ToolExecutionService
    ↓
ToolRegistry

ToolChatService
    ↓
ToolExecutionService
    ↓
ToolRegistry
```

最后明确：

```text
Capability = Deferred
ProjectContext = Deferred
Real Tool = Deferred
Agent = NOT IMPLEMENTED
MCP = NOT IMPLEMENTED
LangGraph = NOT IMPLEMENTED
Memory = NOT IMPLEMENTED
Planning = NOT IMPLEMENTED
```

**Phase 3.11 Step 9 完成后立即停止。**

不要进入 Step 10。

不要接入真实 Tool。

不要增加第三个 Tool。

不要实现 Capability。

不要实现 ProjectContext。

不要开发 Agent / MCP / LangGraph。

等待下一步指令。
