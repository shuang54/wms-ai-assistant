现在继续实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.11 Step 2：最小 ToolExecutionService

## 一、目标

根据 Step 1 的真实代码勘察结果：

当前：

```text
AIOrchestrator
    ↓
_run_tool
    ├── Tool 选择
    ├── capability 检查
    ├── 参数提取
    ├── Registry.execute
    ├── ToolResult → content
    └── metadata
```

存在两个问题：

1. Orchestrator 自己存在第二套 Tool 选择逻辑。
2. 参数提取硬编码为 `material_code` 正则，只支持当前单参数 Tool。

本 Step 建立一个：

```text
ToolExecutionService
```

但只做**最小边界抽取**。

---

# 二、严格范围

允许：

```text
新增 ToolExecutionService
新增 ToolExecutionService 测试
最小修改 AIOrchestrator，使其调用 ToolExecutionService
更新 architecture.md
新增 Step 2 evaluation 文档
```

禁止：

```text
修改 AIRouterService
修改 ToolRegistry 核心行为
修改现有 Tool
新增 Tool
修改 RAG
修改 Text-to-SQL
修改 SQL Validator
修改 SQL Executor
修改 API Contract
```

禁止：

```text
Agent
LangGraph
MCP
Memory
Planning
Multi-Agent
自主 Loop
```

特别禁止：

**不要实现通用 Agent Tool Calling。**

---

# 三、先阅读，不要立即编码

重新阅读：

```text
backend/app/services/ai_orchestrator_service.py
backend/app/tools/registry.py
```

以及 Step 1 已确认的 Tool 相关测试。

确认：

```text
ToolDefinition
ToolResult
ToolRegistry.execute()
```

真实接口。

然后再实现。

---

# 四、ToolExecutionService 职责

新 Service 只负责：

```text
Tool Name
    ↓
Capability Check
    ↓
Arguments
    ↓
ToolRegistry.execute()
    ↓
ToolResult
```

推荐接口：

```python
execute(
    *,
    tool_name: str,
    arguments: dict[str, object],
    allowed_capabilities: set[str] | None = None,
) -> ToolResult
```

具体类型名称必须根据项目现有 DTO 调整。

---

# 五、非常重要：不要继续复制 Router 的 Tool 选择算法

当前：

```text
AIRouterService
    ↓
_match_tool()
```

以及：

```text
AIOrchestrator
    ↓
_resolve_tool_name()
```

存在两套选择逻辑。

本 Step：

**不要修改 Router。**

但是：

`ToolExecutionService` 不允许再实现第三套 Tool 选择算法。

因此：

```text
ToolExecutionService
```

只接受：

```text
tool_name
```

而不是：

```text
question
```

即：

```text
❌ execute(question)

✅ execute(tool_name, arguments)
```

这样避免继续扩大职责。

---

# 六、参数提取怎么处理

当前 Orchestrator：

```text
_extract_tool_arguments_from_question()
```

只能正则提取：

```text
material_code
```

本 Step：

**不要设计通用自然语言参数解析器。**

也不要引入 LLM。

也不要引入复杂 JSON Schema 推理。

保持当前行为兼容。

可以暂时：

```text
AIOrchestrator
    ↓
现有 _extract_tool_arguments_from_question()
    ↓
ToolExecutionService
    ↓
Registry.execute()
```

也就是说：

本 Step 只是把：

```text
执行 + capability + result
```

从 Orchestrator 中抽出来。

参数构造暂时仍属于 Orchestrator。

---

# 七、Capability Check

当前 `_check_capability` 的真实逻辑需要移动到：

```text
ToolExecutionService
```

目标：

```text
AIOrchestrator
    ↓
ToolExecutionService
    ↓
_check_capability
    ↓
Registry
```

如果 capability 不允许：

保持现有异常类型和错误语义。

**不要改变 HTTP 层看到的错误码。**

---

# 八、Registry.execute 仍然是最终执行入口

绝对不要：

```text
ToolExecutionService
    ↓
Tool Handler
```

必须：

```text
ToolExecutionService
    ↓
ToolRegistry.execute()
    ↓
validate_arguments()
    ↓
Handler
```

保持：

```text
Registry = Tool execution authority
```

不改变现有安全边界。

---

# 九、ToolResult 原样返回

不要重新创建 ToolResult。

例如：

```python
result = registry.execute(...)
return result
```

避免：

```text
ToolResult
 ↓
重新包装
 ↓
新的 ToolResult
```

除非现有接口必须转换。

---

# 十、Error Handling

保持 Step 1 发现的现有行为。

### ToolError

仍然由：

```text
ToolRegistry
```

转换成：

```text
ToolResult(success=False)
```

### capability rejection

保持当前 Orchestrator 的错误语义。

### unexpected Exception

不要吞掉。

保持当前错误传播机制。

不要新增：

```text
retry
fallback
loop
```

---

# 十一、AIOrchestrator 最小修改

修改：

```text
backend/app/services/ai_orchestrator_service.py
```

目标：

从：

```text
_run_tool()
    ├── resolve
    ├── capability
    ├── extract
    ├── registry.execute
    └── result conversion
```

变成：

```text
_run_tool()
    ├── resolve tool name
    ├── extract arguments
    └── ToolExecutionService.execute()
```

也就是说：

```text
AIOrchestrator
```

仍然负责：

```text
Router
Tool name resolution
Question → arguments
Orchestration result
```

而：

```text
ToolExecutionService
```

负责：

```text
Capability
Tool Registry execution
ToolResult
```

---

# 十二、不要改变 Tool Selection 行为

这是本 Step 的核心回归要求。

对于现有：

```text
get_inventory
```

必须保持：

```text
Question
    ↓
Router
    ↓
TOOL
    ↓
get_inventory
```

结果完全兼容。

特别测试：

```text
material_code
```

参数提取结果不能改变。

---

# 十三、测试

新增：

```text
tests/test_tool_execution_service.py
```

但优先复用项目已有测试模式。

至少覆盖：

### 1. successful execution

```text
ToolExecutionService
    ↓
Registry
    ↓
Fake/Existing Handler
    ↓
ToolResult(success=True)
```

---

### 2. capability allowed

允许执行。

---

### 3. capability rejected

确认：

```text
Tool 不执行
```

---

### 4. Registry ToolError

确认：

```text
ToolResult(success=False)
```

---

### 5. unexpected exception

确认异常行为与现有 Registry / Orchestrator 语义一致。

---

### 6. arguments 原样传递

例如：

```python
{
    "material_code": "MAT-001"
}
```

必须原样传递给：

```text
Registry.execute()
```

不要在 ExecutionService 中修改参数。

---

# 十四、Orchestrator Regression

至少运行现有：

```text
tests/test_ai_orchestrator.py
tests/test_ai_router.py
tests/test_tool_framework.py
tests/test_get_inventory_tool.py
tests/test_tool_chat_service.py
```

以及：

```text
tests/test_tool_execution_service.py
```

确保现有行为不变。

---

# 十五、ToolChatService 暂时不要改

当前存在第二条链：

```text
ToolChatService
    ↓
LLM Function Calling
    ↓
Registry.execute()
    ↓
Tool
```

本 Step：

**不要把 ToolChatService 迁移过来。**

不要同时改两条链。

否则本 Step 会从：

```text
最小 Boundary 抽取
```

变成：

```text
Tool Framework 重构
```

这不是本阶段目标。

---

# 十六、不要修改 Router

明确：

```text
backend/app/services/ai_router_service.py
```

本 Step：

```text
MODIFIED = NO
```

Router 仍然：

```text
Question
 ↓
Rule-first
 ↓
LLM fallback
 ↓
RouteDecision
```

保持不变。

---

# 十七、架构文档

更新：

```text
docs/architecture.md
```

增加真实架构：

```text
Question
    ↓
AIOrchestrator
    ↓
AI Router
    ↓
RouteDecision
    ↓
Tool
    ↓
ToolExecutionService
    ↓
ToolRegistry
    ↓
Argument Validation
    ↓
Handler
    ↓
ToolResult
```

注意：

不要声称已经解决：

```text
Tool Selection duplication
```

因为 Router 与 Orchestrator 的两套选择逻辑本 Step 不处理。

准确写：

```text
Tool Execution Boundary = introduced
Tool Selection Unification = NOT IMPLEMENTED
Natural Language Argument Extraction = NOT IMPLEMENTED
```

---

# 十八、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11 Step 2 — Tool Execution Boundary.md
```

记录：

```text
1. Goal
2. Existing Problem
3. New Boundary
4. ToolExecutionService Responsibility
5. Registry Responsibility
6. Orchestrator Responsibility
7. Capability Validation
8. Argument Flow
9. Error Flow
10. ToolResult Flow
11. Tests
12. Regression
13. Security
14. Known Limitations
```

---

# 十九、安全要求

确认：

```text
ToolExecutionService
```

本身：

```text
NO SQL
NO DB Session
NO Engine
NO HTTP
NO Shell
NO File IO
NO LLM
NO API Key
NO Password
```

它只能依赖：

```text
ToolRegistry
```

以及必要的纯配置/DTO。

---

# 二十、禁止过度设计

不要创建：

```text
ToolExecutionContext
ToolExecutionPipeline
ToolExecutionMiddleware
ToolExecutionPolicyEngine
ToolExecutionOrchestrator
ToolExecutionManager
ToolExecutionFactory
```

本 Step：

**一个 Service 就够了。**

---

# 二十一、测试命令

先：

```powershell
python -m pytest -q tests/test_tool_execution_service.py
```

然后：

```powershell
python -m pytest -q tests/test_ai_orchestrator.py tests/test_ai_router.py tests/test_tool_framework.py tests/test_get_inventory_tool.py tests/test_tool_chat_service.py
```

最后：

```powershell
python -m pytest -q
```

如果修改涉及 DB 测试，再执行：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

并使用项目现有 DB fixture。

---

# 二十二、质量检查

执行：

```powershell
python -m compileall backend
```

如果项目已有 LSP / lint 检查，继续执行。

要求：

```text
0 failed
0 diagnostics
```

---

# 二十三、最终报告

严格输出：

```text
【Phase 3.11 Step 2 COMPLETE】

1. 新增文件
2. 修改文件
3. ToolExecutionService
4. Orchestrator 改动
5. Router
6. ToolRegistry
7. ToolChatService
8. Capability
9. Arguments
10. ToolResult
11. Security
12. Tests
13. Full Regression
14. DB Tests
15. compile / lint
16. DB writes
17. Network
18. 发现的问题
19. 当前限制
```

最终架构：

```text
Question
    ↓
AIOrchestrator
    ↓
AI Router
    ↓
RouteDecision
    ↓
Tool
    ↓
ToolExecutionService
    ↓
ToolRegistry
    ↓
Argument Validation
    ↓
Handler
    ↓
ToolResult
    ↓
AIOrchestrationResult
```

并明确：

```text
Tool Selection Unification = NOT IMPLEMENTED
Natural Language Argument Parsing = NOT IMPLEMENTED
ToolChatService Migration = NOT IMPLEMENTED
Agent = NOT IMPLEMENTED
MCP = NOT IMPLEMENTED
Multi-step Orchestration = NOT IMPLEMENTED
```

**Step 2 完成后立即停止。**

不要进入 Step 3。

不要新增第二个 Tool。

不要修改 Router。

不要修改 ToolChatService。

等待下一步指令。
