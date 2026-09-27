你现在继续实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.11：AI Tool Execution Boundary

## Step 1：现状勘察

### 一、阶段目标

Phase 3.10.22 已完成：

```text
LLM Usage
    ↓
Observation
    ↓
Accounting
    ↓
Persistence
    ↓
Query
    ↓
Analytics
    ↓
Read Facade
    ↓
HTTP API
```

现在回到 AI Assistant 核心执行链。

目标是逐步建立：

```text
User Question
    ↓
AI Orchestrator
    ↓
AI Router
    ↓
Tool Decision
    ↓
Tool Execution Boundary
    ↓
Tool Registry
    ↓
Tool
    ↓
Tool Result
    ↓
AI Orchestrator
```

本 Step：

**只阅读和分析现有 Tool 执行实现。**

禁止修改 Python 代码。

---

# 二、严格禁止

本 Step 不允许：

```text
修改 AI Orchestrator
修改 AI Router
修改 Tool Framework
修改 Tool Registry
修改 Tool
修改 RAG
修改 Text-to-SQL
修改 SQL Validator
修改 SQL Executor
修改 Database Schema
修改 Business Semantic
```

禁止新增：

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

禁止：

```text
Dashboard
Frontend
Billing
Authentication
Queue
Worker
```

不要为了本 Step 创建新的 Tool。

---

# 三、首先阅读 AI Orchestrator

阅读真实：

```text
backend/app/services/ai_orchestrator_service.py
```

确认：

```text
AIOrchestratorService
```

真实：

1. execute 方法
2. 输入 DTO
3. 输出 DTO
4. Router 调用方式
5. RAG 调用方式
6. Tool 调用方式
7. Text-to-SQL 调用方式
8. 异常传播
9. metadata
10. execution time / latency
11. LLM 调用位置
12. Tool 调用位置

画出现有真实调用链。

---

# 四、阅读 Router

阅读：

```text
backend/app/services/ai_router_service.py
```

确认：

```text
RouteDecision
RouteType
```

或者项目中的实际名称。

记录：

```text
RAG
TOOL
TEXT_TO_SQL
```

以及其他可能存在的 Route。

重点确认：

1. Router 是否只负责分类。
2. Router 是否直接执行 Tool。
3. Router 是否调用 LLM。
4. Router 是否知道 Tool 参数。
5. Router 是否知道数据库。
6. Router 是否知道 RAG。
7. Router 输出是否 immutable。
8. Router 错误如何传播。

---

# 五、搜索 Tool Framework

搜索整个：

```text
backend/app/
```

查找：

```text
ToolRegistry
Tool
register_tool
execute_tool
ToolResult
ToolDefinition
ToolSchema
```

不要假设名字。

找到真实实现后记录：

```text
Tool Registry：
Tool Definition：
Tool Execution：
Tool Result：
Parameter Validation：
```

---

# 六、阅读所有现有 Tool

列出当前项目真正存在的 Tool。

例如可能存在：

```text
Tool A
Tool B
Tool C
```

不要自己猜。

对每一个记录：

```text
Tool Name
输入 Schema
输出 Schema
是否只读
是否 DB Access
是否 HTTP Access
是否写操作
是否需要 ProjectContext
```

特别检查是否存在：

```text
INSERT
UPDATE
DELETE
```

或者其他副作用。

---

# 七、重点检查 Tool 参数验证

确认 Tool 参数到底在哪里验证：

```text
LLM
 ↓
Router
 ↓
Tool
```

还是：

```text
LLM
 ↓
ToolRegistry
 ↓
Pydantic Schema
 ↓
Tool
```

或者：

```text
LLM
 ↓
Tool Framework
 ↓
JSON Schema
```

必须找真实代码。

重点回答：

```text
非法参数在哪里被拒绝？
```

例如：

```json
{
  "warehouse_id": 123
}
```

如果实际要求：

```text
warehouse_id: str
```

最终由哪一层拒绝？

---

# 八、检查 Tool Registry 生命周期

确认 Registry 是：

```text
Singleton
Factory
Dependency Injection
Per Request
Module Global
```

以及：

```text
Tool 注册发生在哪里？
```

检查：

```text
main.py
application startup
service constructor
module import
```

不要修改。

---

# 九、检查 Orchestrator → Tool 调用边界

这是本 Step 最重要的部分。

明确当前真实链路：

```text
Question
 ↓
Orchestrator
 ↓
Router
 ↓
?
 ↓
Tool Registry
 ↓
?
 ↓
Tool
```

回答：

1. 谁选择 Tool？
2. 谁构造 Tool 参数？
3. 谁验证 Tool 参数？
4. 谁执行 Tool？
5. 谁处理 Tool Exception？
6. 谁生成 Tool Result？
7. 谁把 Tool Result 返回给 Orchestrator？
8. Tool 是否可以直接调用其他 Tool？
9. Tool 是否可以直接调用 LLM？
10. Tool 是否可以直接访问数据库？

---

# 十、检查是否存在隐式多步 Tool Calling

重点搜索：

```text
while
for
retry
loop
recursion
tool_calls
function_call
```

确认当前是否存在：

```text
Tool
 ↓
Tool
 ↓
Tool
```

或者：

```text
Tool
 ↓
LLM
 ↓
Tool
```

如果不存在：

明确记录：

```text
Multi-step Tool Calling = NOT IMPLEMENTED
```

如果已经存在：

不要修改。

准确记录真实行为。

---

# 十一、检查 Tool Security Boundary

确认 Tool 是否可以：

```text
写数据库
删除数据
修改数据
调用任意 HTTP URL
执行任意 SQL
读取任意文件
执行 Shell
```

不要只看 Tool 名称。

必须阅读实际 implementation。

---

# 十二、检查 ProjectContext

阅读：

```text
backend/app/projects/
```

确认 Tool 是否依赖：

```text
ProjectContext
project_id
project_name
database
semantic
```

重点确认：

```text
Tool 是否把 vietnam-wms 写死？
```

如果存在：

```python
project_id == "vietnam-wms"
```

记录。

不要修改。

---

# 十三、检查 API Tool Chat

阅读：

```text
backend/app/api/tool_chat.py
```

以及：

```text
backend/app/api/orchestrator_chat.py
```

确认：

```text
HTTP
 ↓
Orchestrator
 ↓
Tool
```

与：

```text
HTTP
 ↓
Tool Service
```

是否存在两套不同链路。

重点确认：

**最终真实 Tool 执行入口到底是哪一个。**

---

# 十四、检查测试

搜索：

```text
tests/
```

重点：

```text
test_ai_orchestrator*
test_ai_router*
test_tool*
test_*registry*
test_*chat*
```

列出当前 Tool 相关测试。

记录：

```text
Unit Tests
Integration Tests
Fake Tool
Fake Registry
Fake LLM
DB Tests
```

重点判断：

是否已经存在可以复用的：

```text
Fake Tool
Recording Tool
Exploding Tool
Fake Registry
```

**不要重新创建。**

---

# 十五、检查 Tool 与 Usage Observability 的关系

确认 Tool 调用是否经过：

```text
LLM Observation
LLM Accounting
LLM Usage Persistence
```

重点：

Tool 本身可能不产生 LLM usage。

但是：

```text
User
 ↓
LLM
 ↓
Tool
 ↓
LLM
```

如果当前只有：

```text
LLM
 ↓
Tool
```

记录。

不要修改。

---

# 十六、最终绘制真实架构

必须根据代码画：

```text
Current Architecture:

Question
   ↓
AIOrchestrator
   ↓
Router
   ↓
?
   ↓
Tool Registry
   ↓
?
   ↓
Tool
   ↓
?
```

不要使用假设。

---

# 十七、判断是否真的需要新增 Boundary

本 Step 最重要的结论不是“要写多少代码”。

而是判断：

```text
当前 Tool Execution 是否已经存在清晰 Application Boundary？
```

分成：

### 情况 A

已经存在：

```text
Orchestrator
 ↓
ToolExecutionService
 ↓
Registry
 ↓
Tool
```

则：

**不要重复造 Boundary。**

---

### 情况 B

当前是：

```text
Orchestrator
 ↓
Registry
 ↓
Tool
```

且职责混杂：

```text
参数构造
参数验证
执行
异常
结果转换
```

则记录需要建立 Boundary。

---

### 情况 C

当前 Tool Framework 已经足够清晰：

则本阶段不需要新增代码。

---

# 十八、文档

本 Step 允许新增：

```text
docs/evaluation/Phase 3.11 — Tool Execution Boundary.md
```

记录：

```text
1. Current Architecture
2. Existing Tool Registry
3. Existing Tools
4. Tool Input Validation
5. Tool Execution
6. Tool Result
7. Error Handling
8. Project Context
9. Security Boundary
10. Multi-step Tool Calling
11. Existing Tests
12. Usage Observability Relationship
13. Boundary Assessment
14. Recommended Next Step
```

可以更新：

```text
docs/architecture.md
```

但只允许记录真实现状。

---

# 十九、测试

本 Step：

```text
Python implementation = 0 changes
```

可以运行已有测试确认 baseline。

至少执行：

```bash
python -m pytest -q
```

如果全量测试时间过长：

至少运行 Tool / Orchestrator 相关测试。

不要为了本 Step 添加测试。

---

# 二十、最终报告

严格输出：

```text
【Phase 3.11 Step 1 COMPLETE】

1. AIOrchestrator
2. AI Router
3. Tool Registry
4. Existing Tools
5. Tool Input Validation
6. Tool Execution Boundary
7. Tool Result
8. Error Handling
9. Project Context
10. Security Boundary
11. Multi-step Tool Calling
12. Tool Tests
13. Usage Observability Relationship
14. Current Architecture
15. Boundary Assessment
16. 新增文件
17. 修改文件
18. 测试结果
19. 发现的问题
20. 当前限制
21. Recommended Next Step
```

最后必须给出真实架构：

```text
Question
   ↓
AIOrchestrator
   ↓
AI Router
   ├── RAG
   ├── Tool
   └── Text-to-SQL
         ↓
       ?
         ↓
       Tool
```

并明确：

```text
Implementation = NOT STARTED
```

## 最重要

**Step 1 完成后立即停止。**

不要创建 ToolExecutionService。

不要修改 Orchestrator。

不要修改 Router。

不要创建新 Tool。

不要进入 Step 2。

等待我检查 Step 1 结果后，再决定是否建立 Tool Execution Boundary。
