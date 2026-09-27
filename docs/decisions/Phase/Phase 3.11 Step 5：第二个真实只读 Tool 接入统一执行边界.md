你现在开始实现：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.11 Step 5：第二个真实只读 Tool 接入统一执行边界

## 一、阶段目标

Phase 3.11 前四步已经完成：

```text
Tool Selection Unification
        ↓
Tool Execution Boundary
        ↓
Multi-Parameter Argument Contract
```

现在验证：

> `ToolExecutionService` 是否真的可以作为多个真实 Tool 的统一执行边界。

本阶段只新增：

**第二个真实、只读、业务明确的 Tool。**

最终验证：

```text
Question
 ↓
AI Router
 ↓
RouteDecision(tool_name)
 ↓
AIOrchestrator
 ↓
Argument Extraction
 ↓
ToolExecutionService
 ↓
ToolRegistry
 ↓
Schema Validation
 ↓
Tool A / Tool B
 ↓
ToolResult
```

重点不是增加复杂业务能力。

重点是证明：

**第二个真实 Tool 不需要在 Orchestrator 中重新实现一套执行逻辑。**

---

# 二、开始前必须先阅读真实代码

不要假设接口。

重点阅读：

```text
backend/app/tools/
backend/app/services/ai_router_service.py
backend/app/services/ai_orchestrator_service.py
backend/app/services/tool_execution_service.py
backend/app/tools/registry.py
backend/app/api/
tests/
```

同时搜索：

```text
get_inventory
get_work_order
ToolDefinition
ToolRegistry
ToolExecutionService
RouteDecision
tool_name
```

确认当前已经存在的 Tool。

---

# 三、第二个 Tool 的选择规则

优先级：

### 第一选择

寻找当前项目已经存在的：

```text
get_work_order
```

或者其他已有只读业务 Tool。

如果它目前只是 Mock：

**先检查是否存在真实 DB schema 和现有测试 fixture 可以支持它。**

### 第二选择

如果已有 Tool 都不适合真实化：

选择一个当前真实数据库已经存在、能够安全只读查询的简单业务对象。

例如：

```text
work_order
purchase_order
sales_order
material
warehouse
```

但必须：

**先根据真实 Schema 判断。**

不要猜字段。

不要虚构表。

不要为了 Step 5 修改数据库结构。

---

# 四、严格范围

## 允许

* 新增一个真实只读 Tool
* 新增 Tool Schema
* 新增最小 Argument Extraction
* 新增 Tool 单元测试
* 新增 ToolExecutionService 集成测试
* 新增 Orchestrator E2E 测试
* 必要的测试 fixture
* 新增 evaluation 文档
* 更新 architecture 文档

## 禁止

不要修改：

```text
ToolExecutionService
ToolRegistry 核心机制
Router 核心匹配算法
RouteDecision contract
RAG
Text-to-SQL
SQL Validator
SQL Executor
Database Schema
```

除非测试发现明确 bug。

不要：

```text
Agent
LangGraph
MCP
Memory
Planning
Multi-Agent
Loop
自动重规划
```

不要修改：

```text
ToolChatService
```

不要引入 LLM 参数解析。

不要实现通用 NLP Parser。

---

# 五、第二个 Tool 必须满足

必须是：

```text
Read Only
Deterministic
Parameterized
Schema Validated
Project Scoped
```

不得支持：

```text
INSERT
UPDATE
DELETE
DROP
ALTER
TRUNCATE
```

不得执行：

```text
shell
HTTP
filesystem
arbitrary SQL
```

数据库查询必须：

```text
bound parameters
read-only transaction
statement timeout
```

如果当前项目已有对应安全模式：

**直接复用。**

---

# 六、推荐业务方向：get_work_order

如果真实 Schema 支持，优先实现：

```text
get_work_order
```

最小参数不要超过两个。

例如：

```text
work_order_no: str
```

或者：

```text
work_order_no: str
material_code: str | None
```

但必须以真实数据库 Schema 为准。

不要为了“多参数”而强行设计两个参数。

如果真实业务最稳定的是：

```text
work_order_no
```

那就只使用：

```text
work_order_no
```

---

# 七、ToolDefinition

新增第二个 Tool Definition。

例如：

```text
name = "get_work_order"
```

必须包含：

```text
description
aliases
input_schema
handler
capability
```

Schema 必须明确：

```text
required
properties
types
additionalProperties
```

如果当前 ToolDefinition 的真实字段结构不同：

**严格按照当前项目已有 contract。**

不要重新设计 ToolDefinition。

---

# 八、Argument Extraction

只做最小确定性规则。

例如：

```text
查询工单 WO-202609-001
```

提取：

```python
{
    "work_order_no": "WO-202609-001"
}
```

可以支持少量明确表达：

```text
工单 WO-202609-001
查询工单号 WO-202609-001
work_order WO-202609-001
work_order_no=WO-202609-001
```

不要实现：

```text
自然语言理解
模糊匹配
LLM extraction
通用 parser
```

如果现有项目已经有通用的确定性提取工具：

优先复用。

不要复制大量正则。

---

# 九、Router Contract

Router 仍然是：

```text
Tool Selection Authority
```

必须返回：

```python
RouteDecision(
    route=RouteType.TOOL,
    tool_name="get_work_order",
    ...
)
```

Router 不得：

```text
执行 Tool
查询 DB
解析 SQL
访问 Session
```

如果当前 Router 的 metadata 足够完成匹配：

只增加：

```text
Tool capability metadata
```

不要重构 Router。

---

# 十、Orchestrator

Orchestrator 只负责：

```text
RouteDecision
    ↓
tool_name
    ↓
argument extraction
    ↓
ToolExecutionService.execute()
```

不要增加：

```text
Tool handler
DB query
SQL
retry
loop
fallback
LLM argument extraction
```

特别验证：

### get_inventory

仍然：

```text
tool_name = get_inventory
```

### get_work_order

现在：

```text
tool_name = get_work_order
```

二者都进入：

```text
ToolExecutionService
```

而不是：

```text
if tool == "get_inventory":
    ...

if tool == "get_work_order":
    ...
```

不要在 Orchestrator 中出现 Tool-specific execution branching。

---

# 十一、ToolExecutionService

**原则上零修改。**

必须证明：

```text
Tool A
 ↓
ToolExecutionService
 ↓
Registry
```

以及：

```text
Tool B
 ↓
ToolExecutionService
 ↓
Registry
```

使用完全相同的执行路径。

如果为了支持第二个 Tool 必须修改 ToolExecutionService：

先判断是否是现有实现 bug。

不要为了 Step 5 扩展职责。

---

# 十二、ToolRegistry

保持：

```text
Schema Authority
Execution Authority
```

测试：

### 正常参数

```text
work_order_no
```

通过。

### 缺少 required 参数

Registry 拒绝。

### unknown field

例如：

```python
{
    "work_order_no": "WO-001",
    "password": "xxx"
}
```

必须拒绝。

### 类型错误

例如：

```python
{
    "work_order_no": 123
}
```

必须拒绝。

不要在 Orchestrator 复制 Schema Validation。

---

# 十三、Security Test

第二个 Tool 至少增加：

```text
SQL injection
unknown argument
missing required argument
invalid argument type
```

例如：

```text
work_order_no = "WO-001' OR '1'='1"
```

必须通过参数绑定机制处理。

不要使用：

```python
f"SELECT ... WHERE work_order_no = '{value}'"
```

---

# 十四、Project Context

第二个 Tool 必须继续支持：

```text
project_id
```

不能硬编码：

```text
vietnam-wms
```

如果当前项目已经有：

```text
ProjectContextProvider
```

直接复用。

测试至少验证：

```text
project A → A context
project B → B context
```

如果当前 DB 不支持真正的 A/B 双项目：

使用 Fake ProjectContext 做轻量隔离测试。

不要创建第二套完整数据库。

---

# 十五、Tool Result

第二个 Tool 必须返回当前统一：

```text
ToolResult
```

不得新建：

```text
WorkOrderResult
InventoryResult
```

作为新的执行返回 contract。

ToolResult 至少验证：

```text
success
data
error
metadata
```

具体字段以当前真实 DTO 为准。

---

# 十六、测试设计

新增：

```text
tests/test_get_work_order_tool.py
```

或者按照当前项目已有 Tool 测试目录结构。

至少覆盖：

### Definition

```text
name
description
schema
capability
```

### Arguments

```text
正常
缺失
unknown field
invalid type
SQL injection
```

### Handler

```text
正常查询
project_id
read-only
```

### Registry

```text
schema validation
handler invocation
ToolResult
```

### ToolExecutionService

验证：

```text
get_inventory
get_work_order
```

都经过同一个 ExecutionService。

不要修改 Service，只通过测试证明复用。

### Orchestrator E2E

例如：

```text
查询工单 WO-001
```

验证：

```text
route == TOOL
tool_name == get_work_order
```

并验证：

```text
RAG = 0
TextToSQL = 0
```

如果 DB 条件允许：

验证最终：

```text
ToolResult.success == True
```

---

# 十七、真实 DB 测试

只有真实 Schema 已经存在并且测试基础设施支持时才增加。

优先：

```text
RUN_DB_TESTS=1
```

测试：

```text
Question
 ↓
Orchestrator
 ↓
Router
 ↓
ToolExecutionService
 ↓
Registry
 ↓
get_work_order
 ↓
PostgreSQL
```

禁止：

```text
ALTER TABLE
CREATE permanent table
INSERT production data
```

如果需要测试数据：

优先使用已有 fixture / rollback。

如果真实 DB 不具备必要结构：

**不要修改数据库。**

直接报告：

```text
database-level get_work_order = NOT SUPPORTED
```

然后完成非 DB 的完整边界测试。

---

# 十八、Tool Selection Regression

新增一个重要测试：

```text
test_multiple_tools_are_selected_by_router
```

验证：

```text
库存问题
    ↓
tool_name = get_inventory
```

工单问题：

```text
工单问题
    ↓
tool_name = get_work_order
```

并确认：

```text
get_inventory 不会误执行 get_work_order
get_work_order 不会误执行 get_inventory
```

不要修改 Router 的核心优先级，只验证当前 contract。

---

# 十九、禁止 Tool-to-Tool

明确增加测试：

```text
get_inventory
    ↓
不能调用
    ↓
get_work_order
```

以及：

```text
get_work_order
    ↓
不能调用
    ↓
get_inventory
```

Tool Handler 不得拥有：

```text
ToolRegistry
ToolExecutionService
AIOrchestrator
```

依赖。

这是为了保持：

```text
Question
 ↓
Router
 ↓
ONE Tool
```

而不是：

```text
Tool A
 ↓
Tool B
 ↓
Tool C
```

---

# 二十、文档

新增：

```text
docs/evaluation/Phase 3.11 Step 5 — Multiple Real Tools.md
```

至少记录：

## 1. 第二个 Tool

```text
Tool Name
Purpose
Parameters
Project Scope
Read-only
```

## 2. Selection

```text
Question
→ Router
→ tool_name
```

## 3. Execution

```text
tool_name
→ Orchestrator
→ ToolExecutionService
→ Registry
→ Tool
```

## 4. Security

记录：

```text
unknown field → rejected
invalid type → rejected
SQL injection → safely handled
write operation → unavailable
```

## 5. Boundary

明确：

```text
Router = selection
Orchestrator = orchestration
ToolExecutionService = execution boundary
ToolRegistry = schema/execution authority
Tool = business implementation
```

## 6. Limitations

如实记录：

```text
LLM argument extraction = NOT IMPLEMENTED
Generic NLP parser = NOT IMPLEMENTED
ToolChatService migration = NOT IMPLEMENTED
Multi-step Tool Calling = NOT IMPLEMENTED
```

---

# 二十一、Architecture

更新：

```text
docs/architecture.md
```

增加第二个 Tool 后的最终结构：

```text
                    Question
                       │
                       ▼
                AIOrchestrator
                       │
                       ▼
                    Router
                       │
                  RouteDecision
                  tool_name
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
       get_inventory       get_work_order
             │                   │
             └─────────┬─────────┘
                       ▼
             ToolExecutionService
                       │
                       ▼
                 ToolRegistry
                       │
                 Schema Validation
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
          Tool A               Tool B
             │                   │
             └─────────┬─────────┘
                       ▼
                   ToolResult
```

强调：

```text
ToolExecutionService
```

是两个真实 Tool 的共同执行边界。

---

# 二十二、测试命令

Windows PowerShell。

先运行定向：

```powershell
python -m pytest -q tests/test_get_work_order_tool.py
```

以及：

```powershell
python -m pytest -q tests/test_tool_execution_service.py
```

然后运行相关 Tool / Router / Orchestrator 测试。

最后：

```powershell
python -m pytest -q
```

如果真实 DB 测试需要：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

完成后如有必要：

```powershell
python -m compileall backend
```

并检查：

```text
LSP diagnostics
lint
```

---

# 二十三、失败处理

如果发现：

```text
Router failure
ToolExecutionService failure
Registry failure
Tool failure
DB failure
```

先定位根因。

不要为了通过测试修改核心架构。

特别是：

如果发现第二个 Tool 无法自然接入现有：

```text
ToolExecutionService
```

先报告：

```text
现有边界缺陷
影响
根因
建议
```

不要直接扩展 Service 职责。

---

# 二十四、完成标准

必须证明：

```text
Tool A = get_inventory
Tool B = 第二个真实只读 Tool
```

二者：

```text
Router
  ↓
RouteDecision(tool_name)
  ↓
AIOrchestrator
  ↓
ToolExecutionService
  ↓
ToolRegistry
  ↓
Tool
```

完全共用执行边界。

并且：

```text
Tool A 不调用 Tool B
Tool B 不调用 Tool A
Tool 不调用 Orchestrator
Tool 不调用 Router
Tool 不调用 ToolExecutionService
```

---

# 二十五、最终报告

完成后严格按照：

```text
【Phase 3.11 Step 5 COMPLETE】

1. 第二个 Tool
- Name：
- Purpose：
- Parameters：
- 是否真实 DB：

2. 新增文件

3. 修改文件

4. Tool Selection
- get_inventory：
- second tool：

5. Tool Execution Boundary
- 是否共用 ToolExecutionService：

6. Schema Validation
- required：
- unknown field：
- invalid type：

7. Security
- SQL injection：
- write operation：
- project isolation：

8. Tests
- directed：
- full no DB：
- full DB：

9. compile / LSP / lint

10. DB writes
- result：

11. Network
- result：

12. 发现的问题

13. 当前限制

14. 最终架构

最后：

**Step 5 到此停止。**

不要进入 Step 6。
不要迁移 ToolChatService。
不要引入 LLM 参数解析。
不要引入 Agent。
不要引入 MCP。
不要引入 LangGraph。
不要实现 Multi-step Tool Calling。
```

补充约束：如果勘察后发现当前真实数据库没有任何适合安全接入的第二个只读业务表，**不要为了完成 Step 5 虚构 Tool**；停在勘察结果并报告即可。Python 的 `dataclass(frozen=True)` 等不可变 DTO 行为属于标准库现有机制，本步骤无需重新设计 DTO。

完成后立即停止，等待下一步指令。
