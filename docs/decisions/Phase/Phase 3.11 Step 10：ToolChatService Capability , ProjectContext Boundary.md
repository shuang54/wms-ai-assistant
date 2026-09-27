你现在开始实现：

# Phase 3.11 Step 10：ToolChatService Capability / ProjectContext Boundary

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

Phase 3.11 Step 9 已完成：

```text
AIOrchestrator
    ↓
ToolExecutionService
    ↓
ToolRegistry
    ↓
Tool
```

以及：

```text
ToolChatService
    ↓
LLM Function Calling
    ↓
Multi-Step Loop
    ↓
ToolExecutionService
    ↓
ToolRegistry
    ↓
Tool
```

Step 8 留下两个明确的架构缺口：

```text
1. ToolChatService 没有 Capability
2. ToolChatService 没有 ProjectContext
```

Step 10 只解决：

> **让 ToolChatService 的 Multi-Step Function Calling 也受到项目级 Tool Capability 限制，并明确 project_id 的传递边界。**

最终形成：

```text
User Request
    ↓
ToolChatService
    ↓
LLM Function Calling
    ↓
ToolExecutionService
    ↓
Capability Check
    ↓
ToolRegistry
    ↓
Tool
```

同时：

```text
project_id
    ↓
ToolExecutionService
```

能够作为执行上下文进入执行边界。

---

# 二、严格范围

## 允许

允许修改：

```text
backend/app/services/tool_chat_service.py
backend/app/services/tool_execution_service.py
backend/app/api/tool_chat.py
```

允许新增：

```text
ProjectContext / capability 相关最小 DTO
tests
evaluation document
architecture document
```

允许修改：

```text
tests/test_tool_chat_*.py
tests/test_tool_execution_service.py
tests/test_tool_architecture_contract.py
```

必要时可以修改现有项目 capability/provider 的最小装配代码。

---

# 三、严格禁止

本阶段禁止：

```text
真实 Tool 接入
get_inventory DB 行为修改
get_work_order DB 行为修改
ToolRegistry 核心逻辑重构
Router 修改
AIOrchestrator 修改
ToolArgumentExtractor 修改
LLM Provider 修改
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
Autonomous Replanning
```

禁止：

```text
多 Tool 并行
新的 Tool Selection
新的 Argument Extraction
```

禁止修改：

```text
数据库结构
数据库表
SQL
Business Semantic
```

---

# 四、第一步：先阅读现有 Project Capability 实现

不要假设。

先阅读：

```text
backend/app/services/tool_execution_service.py
backend/app/services/tool_chat_service.py
backend/app/api/tool_chat.py
backend/app/services/ai_orchestrator_service.py
backend/app/projects/
backend/app/tools/
```

重点确认：

1. 当前 `ToolExecutionService.capabilities` 的真实类型。
2. 当前 Capability 是如何表示 Tool 白名单的。
3. 当前 AIOrchestrator 如何获得 project capability。
4. 当前 `project_id` 在项目中是否已有统一 DTO。
5. 当前是否已有 ProjectContextProvider。
6. 当前是否已有 ProjectCapabilities。
7. 当前 capability deny 的真实异常类型。
8. 当前 API 对 403 的映射。
9. 是否已经存在可复用的 ProjectContext fixture。
10. 是否已经存在测试用 Fake ProjectContext。

**优先复用现有实现。**

不要重新设计一套 Capability Framework。

---

# 五、先定义最小边界

目标：

```text
ToolChatService
```

负责：

```text
LLM Function Calling
Multi-Step Loop
```

不负责：

```text
Capability 判断
Project Tool Authorization
```

这些必须继续属于：

```text
ToolExecutionService
```

因此：

```text
ToolChatService
    ↓
ToolExecutionService
    ↓
Capability
    ↓
ToolRegistry
```

---

# 六、Project ID 来源

这是本阶段重点。

先调查当前 API：

```text
POST /api/chat/with-tools
```

当前 Request DTO 是否已有：

```text
project_id
```

如果已有：

直接复用。

如果没有：

不要立即修改公共 API。

先检查：

```text
AIOrchestrator
ProjectContext
Tool API
```

是否已有标准 project context 获取方式。

如果项目已有统一 ProjectContext：

优先通过已有方式获得。

只有确认：

```text
ToolChatService 无法获得 project scope
```

才允许对 API 增加最小：

```text
project_id: str
```

并同步：

```text
Request DTO
API mapping
tests
docs/api.md
OpenAPI tests
```

不要增加复杂 context object。

---

# 七、最小 ProjectContext

如果项目当前没有适合的上下文对象：

优先创建最小 immutable DTO：

```python
ProjectContext(
    project_id: str,
    project_name: str | None = None,
)
```

要求：

```text
frozen
immutable
```

不要加入：

```text
user
permissions
roles
session
database connection
request
LLM
registry
```

Step 10 只需要：

```text
project_id
```

其他字段不要为了“未来扩展”提前加入。

---

# 八、Capability 语义

必须保持和 AIOrchestrator 当前语义一致。

例如：

```text
project capability:
get_inventory
get_work_order
```

则：

```text
允许 → ToolExecutionService.execute()
```

不允许：

```text
ToolExecutionService
    ↓
AIOrchestratorCapabilityError
```

必须保持现有 403 语义。

不要新建第二种：

```text
ToolChatCapabilityError
```

如果当前项目已有统一 CapabilityError：

直接复用。

---

# 九、ToolChatService 不允许自己判断 Capability

禁止：

```python
if tool_name not in capabilities:
    ...
```

也禁止：

```python
if tool_name == "get_inventory":
    ...
```

ToolChatService 不知道：

```text
哪些 Tool 被允许
```

它只知道：

```text
ToolCall
```

然后：

```text
ToolExecutionService.execute()
```

---

# 十、ExecutionService 新边界

目标：

```text
ToolExecutionService(
    registry,
    capabilities,
    project_id,
)
```

但具体参数必须根据现有实现决定。

不要机械照搬。

要求：

```text
ToolExecutionService
```

成为唯一：

```text
Capability Enforcement Point
```

即：

```text
AIOrchestrator
      ↓
ToolExecutionService
      ↓
Capability Check

ToolChatService
      ↓
ToolExecutionService
      ↓
Capability Check
```

---

# 十一、Capability Denied 行为

必须验证：

```text
ToolChatService
    ↓
LLM requests forbidden tool
    ↓
ToolExecutionService
    ↓
Capability denied
```

要求：

```text
Handler 不执行
```

即：

```text
handler_calls == 0
```

---

# 十二、ToolResult 还是 Exception？

这一点必须严格根据主链路当前真实行为决定。

先阅读：

```text
AIOrchestrator
ToolExecutionService
API
```

如果主链路当前是：

```text
Capability denied
    ↓
AIOrchestratorCapabilityError
    ↓
HTTP 403
```

那么 ToolChatService 不要自行转换成：

```text
ToolResult(False)
```

除非现有 ToolChatService 的 API contract 明确要求继续向 LLM 返回 ToolResult。

必须分析：

```text
Step 8 原行为
+
Step 9 行为
+
AIOrchestrator 403 contract
```

然后选择最小一致方案。

---

# 十三、非常重要：不要把 403 静默吞掉

禁止：

```text
Capability denied
    ↓
ToolResult(success=False)
    ↓
LLM继续
```

如果项目安全语义定义：

```text
Capability denied = authorization failure
```

那么不能把它降级成普通 Tool failure。

原因：

```text
Tool failure
```

和：

```text
Authorization failure
```

不是同一种错误。

必须保留现有主链路的语义。

---

# 十四、Project Scope

如果：

```text
project_id = vietnam-wms
```

则：

```text
ToolExecutionService
```

只能使用：

```text
vietnam-wms
```

对应的 capability。

不要让：

```text
project A
```

使用：

```text
project B
```

的 Tool whitelist。

---

# 十五、不要在 Tool Handler 内重复 Capability

禁止新增：

```text
get_inventory handler:
    if project_id ...
```

Capability 不属于 Tool Handler。

正确：

```text
ToolChatService
    ↓
ToolExecutionService
    ↓
Capability
    ↓
ToolRegistry
    ↓
Handler
```

---

# 十六、测试：Capability

新增：

```text
tests/test_tool_chat_capability.py
```

至少：

## Test 1

允许 Tool：

```text
get_inventory
```

验证：

```text
LLM
→ ToolChatService
→ ExecutionService
→ Registry
→ Handler
```

成功。

---

## Test 2

禁止 Tool：

```text
get_work_order
```

验证：

```text
ExecutionService
→ Capability denied
```

并且：

```text
Handler = 0
```

---

## Test 3

未知 Tool：

```text
unknown_tool
```

验证：

```text
Capability
+
Registry
```

不会产生：

```text
fallback
retry
guess
```

---

## Test 4

ToolChatService 不包含 Capability 判断。

使用 AST：

检查：

```text
tool_chat_service.py
```

不得包含：

```text
if tool_name in capabilities
if tool_name not ...
capability whitelist
```

---

# 十七、测试：ProjectContext

至少：

```text
tests/test_tool_chat_project_context.py
```

覆盖：

### A

```text
project-a
```

允许：

```text
get_inventory
```

### B

```text
project-b
```

不允许：

```text
get_inventory
```

验证：

```text
A → success
B → denied
```

---

# 十八、测试：Context 不泄漏

确保：

```text
Tool Handler
```

仍然只收到：

```python
arguments
```

不要突然把：

```text
project_id
request_id
user_id
tool_call_id
```

塞进 Handler 参数。

ProjectContext 属于：

```text
Execution Boundary
```

不是 Tool Handler arguments。

---

# 十九、API 测试

如果 API 增加：

```text
project_id
```

则测试：

```text
POST /api/chat/with-tools
```

至少：

1. project_id 正常
2. project_id 不存在
3. project capability denied
4. HTTP status
5. error body
6. OpenAPI schema

如果当前 API 已经存在 ProjectContext：

不要增加重复字段。

---

# 二十、保持 Multi-Step

必须验证：

```text
Tool A
 ↓
Tool B
 ↓
Final Answer
```

仍然能够：

```text
ToolChatService
    ↓
ExecutionService(A)
    ↓
ExecutionService(B)
```

每次：

```text
ONE Tool execution
```

不能把：

```text
ToolExecutionService
```

改成：

```text
Multi-Step Engine
```

---

# 二十一、预算必须保持

验证：

```text
TOOL_MAX_ROUNDS
```

仍然：

```text
1 ~ 20
```

以及：

```text
budget exceeded
```

仍然在：

```text
Tool Execution
```

之前发生。

---

# 二十二、两个 Orchestrator 继续隔离

最终：

```text
AIOrchestrator
    ↓
Router
    ↓
ToolArgumentExtractor
    ↓
ToolExecutionService
    ↓
ToolRegistry
```

和：

```text
ToolChatService
    ↓
LLM Function Calling
    ↓
ToolExecutionService
    ↓
ToolRegistry
```

共享：

```text
ToolExecutionService
ToolRegistry
ToolResult
```

不共享：

```text
Router
ToolArgumentExtractor
```

不要改变这个结构。

---

# 二十三、Architecture Contract

更新：

```text
tests/test_tool_chat_architecture_contract.py
```

增加：

```text
C11 Capability Boundary
```

要求：

```text
ToolChatService
    ↓
ToolExecutionService
```

而：

```text
Capability
```

只能存在：

```text
ToolExecutionService
```

或现有统一 ProjectCapability Provider。

---

增加：

```text
C12 Project Context Boundary
```

要求：

```text
ProjectContext
```

不能进入：

```text
ToolChatService LLM arguments
```

也不能进入：

```text
Tool Handler arguments
```

只能作为：

```text
Execution Context
```

存在。

---

# 二十四、禁止真实 Tool

即使：

```text
get_inventory
```

已经具备真实 DB Tool：

本阶段：

```text
不要把它接入 ToolChatService。
```

继续使用：

```text
Mock Tools
```

这样可以把：

```text
Capability
ProjectContext
```

和：

```text
真实数据库
```

完全分开验证。

---

# 二十五、数据库

本阶段：

```text
DB writes = 0
```

最好：

```text
DB access = 0
```

ToolChatService 继续只使用 Mock Tool。

---

# 二十六、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11 Step 10 — ToolChatService Capability & ProjectContext.md
```

记录：

## 1. Before

```text
ToolChatService
    ↓
ExecutionService(capabilities=None)
```

## 2. After

```text
ToolChatService
    ↓
ExecutionService
    ↓
Capability
    ↓
Registry
```

## 3. Capability

```text
Allowed Tool = PASS
Denied Tool = PASS
Handler bypass = PASS
```

## 4. ProjectContext

```text
project-a = PASS
project-b = PASS
cross-project capability = DENIED
```

## 5. Multi-Step

```text
PASS
```

## 6. API

```text
API contract = PASS
```

## 7. Security

```text
Capability = centralized
Project scope = centralized
Tool Handler = no authorization logic
```

---

# 二十七、测试命令

先：

```powershell
python -m pytest -q tests/test_tool_chat_capability.py tests/test_tool_chat_project_context.py tests/test_tool_chat_execution_boundary.py tests/test_tool_chat_service.py tests/test_tool_chat_api.py tests/test_tool_chat_architecture_contract.py
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

LSP 按当前项目已有方式检查。

不要安装新的 lint 工具。

---

# 二十八、失败处理

如果发现：

```text
现有 Capability API
```

无法支持：

```text
project_id
```

不要重新设计整个权限系统。

报告：

```text
Capability API gap
ProjectContext API gap
最小所需修改
```

如果发现需要修改：

```text
Router
AIOrchestrator
ToolRegistry
真实 Tool
```

立即停止。

不要自行扩大范围。

---

# 二十九、最终报告

严格按照：

```text
【Phase 3.11 Step 10 COMPLETE】

1. 修改文件
2. Capability Boundary
3. ProjectContext Boundary
4. ToolChatService
5. ToolExecutionService
6. Multi-Step
7. API
8. Architecture Contract
9. Tests
10. compile / LSP
11. DB writes
12. Network
13. Real Tool
14. 当前限制
```

最后明确：

```text
ToolChatService
    ↓
LLM Function Calling
    ↓
ToolExecutionService
    ↓
Capability
    ↓
ProjectContext
    ↓
ToolRegistry
    ↓
Mock Tool
```

以及：

```text
AIOrchestrator
    ↓
ToolExecutionService
    ↓
Capability
    ↓
ProjectContext
    ↓
ToolRegistry
```

必须确认：

```text
Capability = centralized
ProjectContext = execution boundary
Tool Handler = no authorization logic
```

最后：

```text
Real Tool = NOT IMPLEMENTED
Agent = NOT IMPLEMENTED
MCP = NOT IMPLEMENTED
LangGraph = NOT IMPLEMENTED
Memory = NOT IMPLEMENTED
Planning = NOT IMPLEMENTED
```

**Phase 3.11 Step 10 完成后立即停止。**

不要进入 Step 11。

不要接入真实 Tool。

不要增加第三个 Tool。

不要修改 Router。

不要修改 AIOrchestrator。

等待下一步指令。
