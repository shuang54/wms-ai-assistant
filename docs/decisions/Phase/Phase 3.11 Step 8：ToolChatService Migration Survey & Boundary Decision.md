你现在开始实现：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.11 Step 8：ToolChatService Migration Survey & Boundary Decision

## 一、阶段目标

Phase 3.11 Step 1～7 已经完成。

当前主 Tool 架构：

```text
Question
   ↓
Router
   ↓
RouteDecision(tool_name)
   ↓
AIOrchestrator
   ↓
ToolArgumentExtractor
   ↓
ToolExecutionService
   ↓
ToolRegistry
   ↓
Real Tool
   ↓
ToolResult
```

但项目仍存在第二条历史 Tool 链路：

```text
ToolChatService
   ↓
LLM Function Calling
   ↓
ToolRegistry
   ↓
Mock Tools
   ↓
LLM
```

本 Step 的唯一目标：

> **完整勘察 ToolChatService，确定它与 Phase 3.11 新 Tool Boundary 的关系，并形成迁移决策。**

本阶段：

**只勘察、测试、记录、设计。**

**不迁移 ToolChatService。**

---

# 二、严格范围

## 允许

* 阅读 ToolChatService
* 阅读它依赖的 LLM / Tool / Registry / DTO
* 新增 survey 测试
* 新增 architecture/evaluation 文档
* 新增静态依赖分析
* 新增迁移决策记录
* 必要时增加极少量 characterization tests

## 禁止

绝对不要修改：

```text
ToolChatService 核心行为
ToolExecutionService
ToolRegistry
AIOrchestrator
AI Router
ToolArgumentExtractor
get_inventory
get_work_order
RAG
Text-to-SQL
SQL Validator
SQL Executor
```

不要：

```text
迁移 ToolChatService
删除 ToolChatService
改变 ToolChatService API
改变 Function Calling
改变 mock tools
增加第三个 Real Tool
增加 LLM Argument Extraction
增加 Agent
增加 MCP
增加 LangGraph
增加 Memory
增加 Planning
增加 Multi-step Orchestration
```

如果发现严重架构问题：

**记录，不修复。**

---

# 三、Step 1：完整阅读 ToolChatService

必须阅读：

```text
backend/app/services/tool_chat_service.py
```

以及它所有直接依赖。

重点搜索：

```text
ToolChatService
tool_chat
function calling
tool_calls
function_call
ToolRegistry
mock_tools
LLM client
LLM response
ToolResult
```

同时阅读：

```text
backend/app/api/
tests/
```

找出所有调用 ToolChatService 的入口。

---

# 四、建立调用图

输出并记录：

```text
API
 ↓
Service
 ↓
LLM
 ↓
Tool Selection
 ↓
Tool Execution
 ↓
Tool Result
 ↓
LLM
```

必须明确每一层真实代码。

不要根据文件名猜。

---

# 五、确认 ToolChatService 当前真实职责

必须逐项确认：

### 1. 是否负责 Tool Selection？

例如：

```text
LLM Function Calling
```

是否决定：

```text
tool_name
```

---

### 2. 是否负责 Argument Extraction？

确认 arguments 是：

```text
LLM structured arguments
```

还是：

```text
ToolChatService 自己解析
```

---

### 3. 是否负责 Schema Validation？

确认是否直接：

```text
ToolRegistry.execute()
```

以及 Registry 是否仍然是唯一 Schema Authority。

---

### 4. 是否直接执行 Tool？

确认：

```text
ToolChatService
    ↓
ToolRegistry.execute
```

是否绕过：

```text
ToolExecutionService
```

---

### 5. 是否存在多轮循环？

例如：

```text
LLM
 ↓
Tool
 ↓
LLM
 ↓
Tool
 ↓
LLM
```

记录：

```text
max_rounds
loop condition
termination condition
```

---

# 六、确认当前 ToolChatService 使用的 Tool

列出：

```text
Tool Name
Definition
Input Schema
Handler
Real / Mock
Read / Write
```

特别确认：

```text
get_inventory
get_work_order
```

是否已经进入 ToolChatService。

如果没有：

记录：

```text
ToolChatService currently uses separate mock tools
```

不要强行接入。

---

# 七、确认 Function Calling Contract

阅读当前 LLM client contract。

确认：

```text
tools schema
tool_calls
arguments
JSON decoding
function name
```

记录真实结构。

重点回答：

```text
LLM 提供：
    tool_name
    arguments

还是：

LLM 提供：
    function call
        ↓
ToolChatService 再做一次映射
```

---

# 八、确认错误处理

逐项测试/阅读：

```text
LLM error
invalid tool name
invalid arguments
ToolRegistry error
Tool execution error
malformed JSON arguments
missing tool call
max rounds
```

记录：

```text
Exception
ToolResult
LLM retry
Loop termination
HTTP response
```

不要修改行为。

---

# 九、确认 Security Boundary

重点检查 ToolChatService 是否可以：

```text
绕过 ToolRegistry
绕过 Schema Validation
绕过 Capability
绕过 ProjectContext
直接执行 Handler
```

也检查：

```text
LLM 是否可以指定任意 tool_name
LLM 是否可以传 unknown fields
LLM 是否可以传任意 arguments
LLM 是否可以触发第二个 Tool
```

最终形成：

```text
Security Boundary:
PASS / PARTIAL / FAIL
```

但：

**不要修改。**

---

# 十、与新 Tool Boundary 对照

建立表格：

| 能力                       | 新 Tool Pipeline       | ToolChatService |
| ------------------------ | --------------------- | --------------- |
| Tool Selection           | Router                | ?               |
| Tool Argument Extraction | ToolArgumentExtractor | ?               |
| Schema Validation        | ToolRegistry          | ?               |
| Capability               | ToolExecutionService  | ?               |
| Tool Execution           | ToolExecutionService  | ?               |
| Tool Result              | ToolResult            | ?               |
| Multi-step               | 禁止                    | ?               |
| LLM                      | Router fallback 可选    | ?               |
| Project Context          | Orchestrator          | ?               |

全部根据真实代码填写。

不要凭架构目标填写。

---

# 十一、重点分析：是否可以直接复用 ToolExecutionService

回答：

```text
ToolChatService
    ↓
ToolExecutionService
```

是否可行。

分析：

### 情况 A

如果当前：

```text
ToolChatService
    ↓
Registry.execute
```

而：

```text
ToolExecutionService
    ↓
Registry.execute
```

两者参数 contract 完全兼容：

记录：

```text
Migration feasibility = HIGH
```

---

### 情况 B

如果 ToolChatService 需要：

```text
multi-round
LLM generated arguments
tool call ID
conversation state
```

而 ToolExecutionService 只负责：

```text
capability + single execution
```

则记录：

```text
ToolExecutionService should remain single-execution boundary
ToolChatService must own loop/orchestration
```

不要把 Loop 塞进 ToolExecutionService。

---

# 十二、非常重要：不要把 ToolExecutionService 做成 Agent Runtime

如果 ToolChatService 存在：

```text
LLM
 ↓
Tool
 ↓
LLM
 ↓
Tool
```

不要为了迁移而修改：

```text
ToolExecutionService
```

让它支持：

```text
loop
retry
replanning
multi-step
```

正确边界应该仍然是：

```text
ToolExecutionService
=
ONE Tool execution
```

而如果未来保留 ToolChatService：

```text
ToolChatService
=
LLM Function Calling orchestration
```

二者职责不能混合。

---

# 十三、分析 ToolChatService 是否仍有存在价值

不要直接下结论。

根据代码事实分析：

### 可能情况 A

它只是历史 Mock Demo。

如果：

```text
API only demo
mock tools only
no production caller
```

记录：

```text
Candidate for deprecation
```

---

### 可能情况 B

它是独立的 LLM Function Calling API。

如果：

```text
有独立 API
有真实调用方
有测试
有业务用途
```

记录：

```text
Keep as separate capability
```

---

### 可能情况 C

它未来可以成为：

```text
LLM Function Calling Adapter
```

记录：

```text
Can reuse ToolExecutionService
```

但：

**本 Step 不实施。**

---

# 十四、Characterization Tests

如果当前 ToolChatService 测试不足：

新增：

```text
tests/test_tool_chat_service_characterization.py
```

只测试现有行为。

至少覆盖：

```text
single tool call
multiple tool calls
invalid tool
invalid arguments
tool error
LLM error
max rounds
final answer
```

如果已有测试覆盖：

**不要重复。**

---

# 十五、静态 Boundary Test

新增：

```text
tests/test_tool_chat_architecture_contract.py
```

只要当前项目结构适合。

检查：

### 1.

ToolChatService 不直接：

```text
Tool Handler
```

如果当前已经直接调用 Handler：

记录：

```text
CURRENT VIOLATION
```

不要修。

### 2.

ToolChatService 是否直接：

```text
ToolRegistry.execute
```

记录：

```text
YES / NO
```

### 3.

是否经过：

```text
ToolExecutionService
```

记录：

```text
YES / NO
```

### 4.

是否存在：

```text
multi-step loop
```

记录：

```text
YES / NO
```

---

# 十六、Migration Options

最终只允许形成三个候选方案之一：

## Option A — Deprecate

```text
ToolChatService
        ↓
历史 Mock / Demo
        ↓
逐步删除
```

适用于：

```text
没有生产调用
没有独立业务价值
```

---

## Option B — Reuse New Execution Boundary

```text
LLM
 ↓
ToolChatService
 ↓
ToolExecutionService
 ↓
ToolRegistry
 ↓
Tool
```

其中：

```text
ToolChatService
=
Function Calling orchestration
```

```text
ToolExecutionService
=
single Tool execution
```

---

## Option C — Keep Separate

```text
AIOrchestrator Pipeline
        ↓
Router
        ↓
ToolArgumentExtractor
        ↓
ToolExecutionService
```

另一条：

```text
ToolChatService
        ↓
LLM Function Calling
        ↓
ToolExecutionService
        ↓
ToolRegistry
```

两条路径共享：

```text
ToolExecutionService
ToolRegistry
ToolResult
```

但不强行合并 Orchestration。

---

# 十七、推荐判断标准

不要按“代码更少”选择。

按照：

```text
真实调用方
职责边界
Security
Project Context
测试覆盖
未来维护成本
```

做事实分析。

最终明确：

```text
Recommended next architecture:
A / B / C
```

这里只允许做**架构建议**，不修改代码实现。

---

# 十八、文档

新增：

```text
docs/evaluation/Phase 3.11 Step 8 — ToolChatService Survey.md
```

内容至少：

```text
1. Current ToolChatService
2. Call Graph
3. Current Tools
4. Function Calling Contract
5. Argument Contract
6. Schema Validation
7. Capability Boundary
8. Execution Boundary
9. Multi-step Behavior
10. Security
11. Comparison with New Tool Pipeline
12. Migration Options
13. Recommended Architecture
14. Migration Preconditions
15. Risks
```

---

# 十九、Architecture 更新

更新：

```text
docs/architecture.md
```

增加：

```text
§8.26 ToolChatService Boundary Survey
```

必须明确区分：

```text
主业务链路
```

与：

```text
历史 / Function Calling 链路
```

不要把两条链路画成已经统一。

---

# 二十、禁止修改生产逻辑

本 Step 理想状态：

```text
AI Router              unchanged
RouteDecision          unchanged
AIOrchestrator         unchanged
ToolArgumentExtractor  unchanged
ToolExecutionService   unchanged
ToolRegistry           unchanged
ToolChatService        unchanged
Tools                  unchanged
```

允许修改：

```text
tests
architecture docs
evaluation docs
```

如果为了测试必须修改生产逻辑：

**停止并报告。**

---

# 二十一、测试命令

Windows PowerShell：

```powershell
python -m pytest -q tests/test_tool_chat_service.py
```

如果文件不存在：

不要创建重复测试，先查找实际 ToolChatService 测试文件。

然后：

```powershell
python -m pytest -q tests/test_tool_chat_service_characterization.py
```

如果新增。

再：

```powershell
python -m pytest -q
```

如果需要 DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

最后：

```powershell
python -m compileall -q backend
```

检查：

```text
LSP diagnostics
```

不要安装新的 lint 工具。

---

# 二十二、完成标准

必须得到一份明确的事实报告：

```text
ToolChatService
├── 当前入口
├── 当前 Tool
├── Selection
├── Argument
├── Schema
├── Capability
├── Execution
├── Loop
├── Security
└── Project Context
```

以及：

```text
新 Tool Pipeline
        vs
ToolChatService
```

的对照结果。

最终必须明确：

```text
Migration Option:
A / B / C
```

以及：

```text
下一步真正迁移前需要满足什么条件。
```

---

# 二十三、最终报告格式

完成后严格：

```text
【Phase 3.11 Step 8 COMPLETE】

1. ToolChatService 当前职责
- ...

2. 当前调用链
- ...

3. 当前 Tool
- ...

4. Function Calling
- ...

5. Argument
- ...

6. Schema Validation
- ...

7. Capability
- ...

8. Execution Boundary
- ...

9. Multi-step
- ...

10. Security
- ...

11. Project Context
- ...

12. 与新 Tool Pipeline 对比
- ...

13. Characterization Tests
- ...

14. Architecture Contract
- ...

15. Migration Options
- A:
- B:
- C:

16. Recommended Architecture
- ...

17. Migration Preconditions
- ...

18. Tests
- directed：
- full no DB：
- full DB：

19. compile / LSP / lint

20. DB writes
- result：

21. Network
- result：

22. 核心代码是否修改
- Router：
- Orchestrator：
- ToolArgumentExtractor：
- ToolExecutionService：
- ToolRegistry：
- ToolChatService：
- Tools：

23. 当前限制

最后：

**Step 8 到此停止。**

不要进入 Step 9。
不要执行 ToolChatService Migration。
不要增加第三个 Tool。
不要引入 Agent。
不要引入 MCP。
不要引入 LangGraph。
不要引入 Memory。
不要引入 Planning。
不要实现 Multi-step Tool Calling。
```

**完成 Step 8 后立即停止，等待下一步指令。**
