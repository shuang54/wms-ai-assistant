你现在继续实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.11 Step 3：Tool Selection Contract Unification

## 一、目标

Step 1 已确认当前存在两套 Tool Selection：

```text
AI Router
    ↓
_match_tool()
```

以及：

```text
AI Orchestrator
    ↓
_resolve_tool_name()
```

Step 2 已建立：

```text
ToolExecutionService
```

本 Step 的唯一目标：

**消除 Orchestrator 中第二套 Tool Selection 算法，让 Router 成为 Tool 选择的唯一来源。**

最终目标：

```text
Question
    ↓
AIOrchestrator
    ↓
AI Router
    ↓
RouteDecision
    ↓
Tool Name
    ↓
ToolExecutionService
    ↓
ToolRegistry
```

---

# 二、严格范围

允许：

```text
修改 AIRouterService
修改 AIOrchestratorService
新增/修改 Tool Selection 相关测试
更新 architecture.md
新增 evaluation 文档
```

禁止：

```text
修改 ToolRegistry 核心执行逻辑
修改现有 Tool
新增 Tool
修改 RAG
修改 Text-to-SQL
修改 SQL Validator
修改 SQL Executor
修改 ToolChatService
```

禁止：

```text
Agent
LangGraph
MCP
Memory
Planning
Multi-Agent
Loop
自主重规划
```

---

# 三、第一原则

本 Step 必须先阅读真实代码。

重点阅读：

```text
backend/app/services/ai_router_service.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/tool_execution_service.py
backend/app/tools/
tests/test_ai_router.py
tests/test_ai_orchestrator.py
```

不要假设 `_match_tool()` 和 `_resolve_tool_name()` 的具体实现。

---

# 四、确认 Router 当前输出

明确真实：

```text
RouteDecision
```

是否已经包含：

```text
tool_name
```

如果已经包含：

```python
RouteDecision(
    route=TOOL,
    tool_name="get_inventory",
    ...
)
```

则直接复用。

如果没有：

评估最小修改：

```text
RouteDecision
    ↓
tool_name
```

但：

**不要重新设计 RouteDecision。**

只增加 Tool Selection 所必须的字段。

---

# 五、确定唯一责任

最终职责必须变成：

### AI Router

负责：

```text
Question
    ↓
选择 Route
    ↓
如果 TOOL
    ↓
选择具体 Tool
```

输出：

```text
RouteDecision
```

---

### AI Orchestrator

负责：

```text
RouteDecision
    ↓
读取 tool_name
    ↓
提取 arguments
    ↓
ToolExecutionService
```

不再：

```text
重新选择 Tool
```

---

### ToolExecutionService

负责：

```text
tool_name
    ↓
capability
    ↓
Registry.execute
```

---

### ToolRegistry

负责：

```text
Schema Validation
    ↓
Handler
    ↓
ToolResult
```

---

# 六、删除 Orchestrator 第二套选择

当前类似：

```text
_resolve_tool_name()
```

需要分析是否可以删除。

目标：

```text
❌ _resolve_tool_name()
```

改成：

```text
decision.tool_name
```

例如：

```python
tool_name = decision.tool_name
```

然后：

```text
arguments = _extract_tool_arguments_from_question(...)
```

最后：

```text
tool_execution_service.execute(
    tool_name,
    arguments=arguments,
)
```

---

# 七、不要移动参数解析

本 Step：

**不解决自然语言参数解析。**

继续保留：

```text
_extract_tool_arguments_from_question()
```

也就是说：

```text
Router
    ↓
Tool Selection

Orchestrator
    ↓
Question → arguments
```

这种职责暂时可以接受。

本 Step 只解决：

```text
Tool Selection duplication
```

---

# 八、Tool Selection 必须保持现有行为

这是本 Step 最重要的兼容性要求。

现有：

```text
get_inventory
```

必须继续正常选择。

特别测试：

```text
查询 MAT-001 的库存
```

必须仍然：

```text
Route = TOOL
Tool = get_inventory
Arguments.material_code = MAT-001
```

不要因为重构导致：

```text
TOOL → RAG
TOOL → TEXT_TO_SQL
```

---

# 九、重点处理 Selection 不一致问题

Step 1 已经发现：

```text
_match_tool()
```

和：

```text
_resolve_tool_name()
```

使用不同算法。

本 Step 必须写测试证明：

对于相同输入：

```text
question
tool definitions
```

Router 选择结果就是最终 Tool。

不要再让 Orchestrator 二次判断。

---

# 十、Router 输出不应该暴露 Handler

保持 Step 1 的设计：

Router 只知道：

```text
ToolDefinition
    name
    description
    aliases
```

不要让 Router 获得：

```text
handler
registry internals
database
engine
session
```

---

# 十一、未知 Tool

增加测试：

Router 返回：

```text
tool_name = "unknown_tool"
```

然后：

```text
AIOrchestrator
    ↓
ToolExecutionService
    ↓
ToolRegistry
```

必须保持当前未知 Tool 的安全行为。

不要：

```text
fallback
```

不要：

```text
自动选择另一个 Tool
```

不要：

```text
重新调用 LLM
```

---

# 十二、Capability 行为保持不变

继续验证：

```text
Tool Selected
    ↓
Capability Denied
    ↓
403 semantics
```

不能因为 Tool Selection 重构导致：

```text
403 → 500
```

---

# 十三、RouteDecision 兼容性

如果修改 RouteDecision：

必须确认：

```text
RAG
TEXT_TO_SQL
```

不受影响。

例如：

```text
RAG:
tool_name = None

TEXT_TO_SQL:
tool_name = None

TOOL:
tool_name = "get_inventory"
```

如果当前 DTO 已经能够表达这种状态：

**不要修改 DTO。**

---

# 十四、测试要求

新增/修改测试至少覆盖：

### 1. Router selects tool

```text
Question
    ↓
Router
    ↓
TOOL + get_inventory
```

---

### 2. Orchestrator trusts Router

构造一个场景：

```text
Router → get_inventory
```

但如果 Orchestrator 仍然执行自己的 `_resolve_tool_name()`，测试必须能够发现。

目标：

```text
Orchestrator
    ↓
只使用 RouteDecision.tool_name
```

---

### 3. No second selection

可以使用 Fake Router：

```text
FakeRouter returns:
TOOL / get_inventory
```

Registry 中只允许：

```text
get_inventory
```

确认 Orchestrator 不再重新匹配其他 Tool。

---

### 4. Unknown Tool

```text
tool_name = unknown
```

必须安全失败。

---

### 5. Capability denied

保持现有 403 语义。

---

### 6. RAG regression

确认：

```text
RAG
```

完全不受影响。

---

### 7. Text-to-SQL regression

确认：

```text
TEXT_TO_SQL
```

完全不受影响。

---

# 十五、不要增加新的 Tool

本 Step：

```text
New Tool = 0
```

继续只使用：

```text
get_inventory
```

以及现有测试 Fake Handler。

---

# 十六、不要修改 ToolChatService

明确：

```text
ToolChatService = NOT MODIFIED
```

它仍然：

```text
LLM Function Calling
    ↓
ToolRegistry.execute
```

本 Step 不做第二条链路统一。

---

# 十七、安全要求

Router：

```text
NO DB
NO Handler
NO Engine
NO Session
NO SQL
```

Orchestrator：

```text
NO Tool Handler
NO DB
NO SQL
```

ToolExecutionService：

```text
NO DB
NO SQL
NO LLM
NO HTTP
NO Shell
```

ToolRegistry：

继续作为：

```text
唯一 Tool Execution Authority
```

---

# 十八、文档

新增：

```text
docs/evaluation/Phase 3.11 Step 3 — Tool Selection Unification.md
```

记录：

```text
1. Previous Architecture
2. Duplicate Selection Problem
3. New Selection Contract
4. Router Responsibility
5. Orchestrator Responsibility
6. ToolExecutionService Responsibility
7. Compatibility
8. Security
9. Tests
10. Known Limitations
```

更新：

```text
docs/architecture.md
```

真实架构：

```text
Question
    ↓
AIOrchestrator
    ↓
AIRouterService
    ↓
RouteDecision
    ├── RAG
    ├── TOOL + tool_name
    │       ↓
    │   ToolExecutionService
    │       ↓
    │   ToolRegistry
    │       ↓
    │   Handler
    │       ↓
    │   ToolResult
    │
    └── TEXT_TO_SQL
```

---

# 十九、测试命令

先：

```powershell
python -m pytest -q tests/test_ai_router.py tests/test_ai_orchestrator.py tests/test_tool_execution_service.py
```

然后：

```powershell
python -m pytest -q tests/test_tool_framework.py tests/test_get_inventory_tool.py tests/test_tool_chat_service.py
```

最后：

```powershell
python -m pytest -q
```

如果 DB 测试受到影响：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

---

# 二十、质量检查

执行：

```powershell
python -m compileall backend tests
```

并执行项目现有 LSP / lint。

要求：

```text
0 failed
0 diagnostics
```

---

# 二十一、最终报告

严格输出：

```text
【Phase 3.11 Step 3 COMPLETE】

1. 新增文件
2. 修改文件
3. Router
4. RouteDecision
5. Orchestrator
6. ToolExecutionService
7. ToolRegistry
8. Tool Selection
9. Capability
10. RAG Regression
11. Text-to-SQL Regression
12. ToolChatService
13. Security
14. Tests
15. Full Regression
16. DB Tests
17. compile / lint
18. DB writes
19. Network
20. 发现的问题
21. 当前限制
```

最终明确：

```text
Tool Selection Unification = IMPLEMENTED
Tool Execution Boundary = IMPLEMENTED
Natural Language Argument Parsing = NOT IMPLEMENTED
Multi-parameter Tool = NOT IMPLEMENTED
ToolChatService Migration = NOT IMPLEMENTED
Agent = NOT IMPLEMENTED
MCP = NOT IMPLEMENTED
Multi-step Orchestration = NOT IMPLEMENTED
```

**Step 3 完成后立即停止。**

不要进入 Step 4。

不要新增第二个 Tool。

不要实现自然语言多参数解析。

不要修改 ToolChatService。

等待下一步指令。
