你现在开始实现：

# Phase 3.11 Step 11：ToolChatService Real `get_inventory` Integration

项目：

```text
D:\coding\ai\wms-ai-assistant
```

---

# 一、阶段目标

Phase 3.11 Step 10 已完成：

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
```

但是链路 B 当前仍然：

```text
ToolChatService
    ↓
Mock Tool
```

本阶段只做一件事：

> **将现有真实只读 `get_inventory` Tool 接入 ToolChatService 的 Function Calling 链路。**

最终验证：

```text
User
 ↓
POST /api/chat/with-tools
 ↓
ToolChatService
 ↓
LLM Function Calling
 ↓
ToolCall(get_inventory)
 ↓
ToolExecutionService
 ↓
Capability
 ↓
ToolRegistry
 ↓
真实 get_inventory Handler
 ↓
PostgreSQL
 ↓
ToolResult
 ↓
LLM
 ↓
Final Answer
```

---

# 二、严格范围

## 允许

允许修改：

```text
backend/app/api/tool_chat.py
backend/app/services/tool_chat_service.py
```

以及：

```text
tests/
docs/evaluation/
docs/architecture.md
docs/api.md
```

必要时允许修改：

```text
ProjectRegistry / ProjectRegistration
```

的**最小测试装配**，但不能改变其核心行为。

允许：

* 将现有真实 `get_inventory` 注册到 ToolChatService 使用的 Registry
* 增加真实 PostgreSQL integration test
* 增加 Fake LLM / Scripted LLM 测试
* 增加 DB fixture（必须遵守现有测试基础设施）
* 增加零写入验证
* 更新 evaluation / architecture 文档

---

# 三、严格禁止

本阶段禁止修改：

```text
AI Router
AIOrchestrator
ToolArgumentExtractor
ToolExecutionService 核心逻辑
ToolRegistry 核心逻辑
get_inventory SQL
get_inventory Handler 核心业务逻辑
Business Semantic
Database Schema
```

禁止：

```text
get_work_order 真实接入
第三个 Tool
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

禁止：

```text
自动 SQL
Text-to-SQL
RAG
```

禁止修改数据库结构。

禁止：

```text
INSERT
UPDATE
DELETE
DROP
ALTER
TRUNCATE
```

---

# 四、第一步：先阅读真实实现

不要假设当前接口。

阅读：

```text
backend/app/tools/get_inventory.py
backend/app/tools/registry.py
backend/app/services/tool_execution_service.py
backend/app/services/tool_chat_service.py
backend/app/api/tool_chat.py
backend/app/projects/
```

重点确认：

1. 真实 `get_inventory` ToolDefinition。
2. 真实 `get_inventory` Handler。
3. 当前 Handler 所需参数。
4. 当前真实 DB 表。
5. 当前 project scope。
6. 当前数据库连接方式。
7. 当前 READ ONLY transaction 机制。
8. 当前 statement timeout。
9. 当前 material_code whitelist。
10. 当前 warehouse_code 行为。
11. 当前 Tool Registry 如何注册真实 Tool。
12. 当前 ProjectRegistry 如何提供 capability。
13. 当前测试数据库 fixture。
14. 是否已有真实 `get_inventory` integration tests。

**优先复用现有实现和 fixture。**

---

# 五、不要重新实现 get_inventory

非常重要。

真实 Tool 已经存在：

```text
backend/app/tools/get_inventory.py
```

本阶段不是开发新的 Tool。

必须复用：

```text
GET_INVENTORY_DEFINITION
get_inventory Handler
现有 SQL
现有 validation
现有 DB access
```

不要复制一份：

```text
get_inventory_for_tool_chat
```

不要新建：

```text
chat_get_inventory
```

不要复制 SQL。

---

# 六、Registry 设计

Step 10 当前链路 B 使用：

```text
_tool_registry
```

并注册 Mock Tools。

本阶段需要确认：

```text
ToolChatService
```

是否可以安全地使用：

```text
真实 get_inventory
```

而不破坏：

```text
Mock get_inventory
```

---

如果当前真实 Tool 和 Mock Tool 使用：

```text
相同 tool_name = get_inventory
```

禁止：

```text
同一个 Registry 同时注册两个同名 Tool
```

不要靠：

```text
last registration wins
```

解决。

必须明确：

```text
ToolChatService production registry
```

使用哪个 Definition / Handler。

---

# 七、推荐最小装配

优先考虑：

```text
ToolChatService Registry
    ↓
get_inventory REAL
```

并保留：

```text
Mock Tool
```

作为：

```text
unit test / characterization test
```

而不是让：

```text
production API
```

在同一个 Registry 中同时暴露：

```text
Mock get_inventory
Real get_inventory
```

---

# 八、Project Capability

当前 Step 10 已经建立：

```text
project_id
    ↓
ProjectRegistry
    ↓
ProjectRegistration.capabilities
    ↓
ToolExecutionService
```

因此真实 Tool 接入后：

```text
project_id = vietnam-wms
```

必须明确：

```text
get_inventory
```

是否已经在该 Project 的 Tool capability whitelist 中。

如果没有：

不要偷偷修改默认 capability。

先检查现有项目配置。

如果确实需要新增：

只做最小项目测试/开发配置修改，并在最终报告明确：

```text
get_inventory capability enabled for test project
```

---

# 九、不要取消 Capability

禁止：

```text
ToolChatService
    ↓
ToolExecutionService(capabilities=None)
```

来绕过 Step 10。

真实 Tool 必须经过：

```text
Capability
```

即：

```text
ToolChatService
    ↓
ToolExecutionService
    ↓
Capability
    ↓
Registry
    ↓
Real Tool
```

---

# 十、真实 DB 安全边界

真实 `get_inventory` 必须保持现有安全机制。

必须确认：

```text
READ ONLY transaction
+
bound parameters
+
statement timeout
+
rollback
```

全部仍然成立。

本阶段不得削弱任何一个。

---

# 十一、第一条真实 E2E

新增：

```text
tests/test_tool_chat_real_get_inventory.py
```

先实现最小真实 DB case。

使用：

```text
RUN_DB_TESTS=1
```

门控。

默认：

```text
pytest -q
```

不能访问 PostgreSQL。

---

# 十二、真实 E2E 流程

使用 Scripted/Fake LLM。

不要调用真实 DeepSeek。

Fake LLM 第一轮返回：

```text
ToolCall:
name = get_inventory
arguments = {
    "material_code": "MAT-001"
}
```

然后：

```text
ToolChatService
 ↓
ToolExecutionService
 ↓
ToolRegistry
 ↓
REAL get_inventory
 ↓
PostgreSQL
```

第二轮 Fake LLM 返回最终答案。

---

# 十三、数据库测试数据

不要假设：

```text
MAT-001
```

一定存在。

先检查现有测试数据库 fixture。

如果项目已经存在：

```text
inventory fixture
```

优先复用。

如果不存在：

允许创建最小测试 fixture。

但：

```text
BEGIN
INSERT fixture
测试
ROLLBACK
```

或者严格按照项目现有 DB isolation 机制。

禁止：

```text
开发数据库
生产数据库
```

写入测试数据。

---

# 十四、真实 SQL 不允许 Fake

这是本阶段核心。

必须是真实：

```text
get_inventory Handler
```

真实：

```text
SQL
```

真实：

```text
SQLAlchemy / psycopg
```

真实：

```text
PostgreSQL
```

不能：

```text
Fake Tool
Fake Handler
Fake SQL Executor
```

---

# 十五、Execution Boundary 验证

测试必须确认：

```text
ToolChatService
```

没有：

```text
registry.execute()
```

仍然只能：

```text
execution_service.execute()
```

---

同时：

```text
ToolExecutionService
```

仍然只执行一个 Tool。

---

# 十六、Capability Denied E2E

增加一个 DB-gated case：

```text
project_id = project-without-inventory
```

Capability：

```text
get_inventory = denied
```

Fake LLM 请求：

```text
get_inventory
```

验证：

```text
403 / capability exception
```

并且：

```text
Handler = 0
DB query = 0
```

注意：

**这里不需要 PostgreSQL 真正执行 SQL。**

如果现有 ProjectRegistry 可以在非 DB 测试中验证，则优先非 DB。

---

# 十七、真实 Tool Success

真实 DB case 必须验证：

```text
ToolCall
 ↓
ToolExecutionService
 ↓
ToolRegistry
 ↓
Real Handler
 ↓
PostgreSQL
 ↓
ToolResult(success=True)
```

并确认最终：

```text
AIOrchestrationResult
```

或者：

```text
ToolChatResponse
```

中的：

```text
tool_calls
```

正确记录：

```text
get_inventory
```

---

# 十八、Argument Forwarding

Fake LLM 返回：

```json
{
  "material_code": "MAT-001",
  "warehouse_code": "A01"
}
```

如果当前真实 DB Tool 支持：

```text
warehouse_code
```

则验证两个参数：

```text
LLM
 ↓
ToolCall.arguments
 ↓
ToolExecutionService
 ↓
ToolRegistry
 ↓
Handler
```

保持完全一致。

如果当前真实 DB schema 不支持 warehouse dimension：

**不要强行测试 warehouse_code。**

只使用当前真实支持的参数。

---

# 十九、Schema Validation

增加：

```text
unknown field
```

例如：

```json
{
  "material_code": "MAT-001",
  "fake_field": "xxx"
}
```

验证：

```text
ToolRegistry
 ↓
reject
```

必须：

```text
Real Handler = 0
DB = 0
```

不要让非法参数进入真实 DB。

---

# 二十、Tool Handler Injection

至少增加：

```text
material_code = "' OR 1=1 --"
```

如果当前 Handler whitelist 已经拒绝：

验证：

```text
Handler reject
DB = 0
```

如果拒绝发生在 Registry validation 层：

也可以。

关键：

```text
不得执行危险 SQL
```

---

# 二十一、DB Writes = 0

这是本阶段硬性要求。

测试前：

记录：

```text
关键表 count_before
```

测试后：

记录：

```text
count_after
```

必须：

```text
count_before == count_after
```

如果当前测试 DB 有事务 rollback：

优先使用现有 isolation。

最终报告：

```text
DB writes = 0
```

---

# 二十二、SQL 操作审计

不需要新增复杂审计系统。

但测试至少确认：

真实 `get_inventory` SQL 是：

```text
SELECT
```

并且：

```text
INSERT = 0
UPDATE = 0
DELETE = 0
DDL = 0
```

可以使用现有 SQLAlchemy event / test hook。

如果项目没有现成机制：

不要为了这个测试引入复杂 SQL auditing framework。

---

# 二十三、API E2E

如果当前：

```text
POST /api/chat/with-tools
```

已经可以注入 Fake LLM：

增加一个 DB-gated API test。

流程：

```text
HTTP
 ↓
ToolChatService
 ↓
Real get_inventory
 ↓
PostgreSQL
```

但：

```text
LLM = Fake
```

不要真实网络。

---

# 二十四、不要测试真实 DeepSeek

本阶段：

```text
Real LLM = NO
```

不要新增：

```text
RUN_REAL_LLM
```

如果项目已有：

```text
RUN_REAL_TOOL_CALLING_TEST
```

继续保持：

```text
SKIP
```

不要把它加入默认测试。

---

# 二十五、不要接入 get_work_order

即使：

```text
get_work_order
```

已经存在真实实现：

本阶段明确：

```text
Real get_work_order = DEFERRED
```

只接：

```text
get_inventory
```

一个真实 Tool。

---

# 二十六、Mock Tool 测试不得删除

保持：

```text
tests/test_tool_chat_service.py
tests/test_tool_chat_api.py
tests/test_tool_chat_service_characterization.py
tests/test_tool_chat_execution_boundary.py
```

继续通过。

不要因为真实 Tool 接入而删除 Mock Tool 测试。

Mock 测试继续承担：

```text
Function Calling
Multi-Step
Budget
Error
Architecture
```

真实 DB 测试只承担：

```text
Real Tool
Real DB
Execution Integration
```

---

# 二十七、Architecture Contract

更新：

```text
tests/test_tool_chat_architecture_contract.py
```

增加：

```text
C13 Real Tool Execution
```

要求：

```text
ToolChatService
    ↓
ToolExecutionService
    ↓
ToolRegistry
    ↓
Real Tool
```

禁止：

```text
ToolChatService
    ↓
Real Handler
```

禁止：

```text
ToolChatService
    ↓
SQL
```

禁止：

```text
ToolChatService
    ↓
DB
```

---

# 二十八、Tool Registry Contract

确认：

```text
get_inventory
```

只能出现一个有效 Definition。

如果 production Registry 使用 Real Handler：

则：

```text
Mock get_inventory
```

不能覆盖 Real Handler。

测试 Registry 可以继续使用 Mock。

---

# 二十九、Project Isolation

至少测试：

```text
project-a
```

允许：

```text
get_inventory
```

而：

```text
project-b
```

不允许。

验证：

```text
project-a
 → Tool executes

project-b
 → capability denied
 → Handler 0
 → DB 0
```

不要创建第二个完整 PostgreSQL project database。

只测试已有 ProjectRegistry / capability configuration。

---

# 三十、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11 Step 11 — Real get_inventory Integration.md
```

记录：

## 1. Architecture

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
```

## 2. Tool

```text
Tool = get_inventory
Real Handler = YES
Read Only = YES
```

## 3. LLM

```text
Fake LLM
Real LLM = NO
```

## 4. DB

```text
PostgreSQL = YES
DB writes = 0
```

## 5. Security

```text
Capability = PASS
Schema validation = PASS
Unknown arguments = REJECT
Injection input = REJECT
Handler bypass = PASS
```

## 6. Multi-Step

记录：

```text
ToolCall
 ↓
Real Tool
 ↓
ToolResult
 ↓
Final Answer
```

---

# 三十一、不要修改核心 Tool

如果真实 `get_inventory` 在本阶段出现：

```text
schema mismatch
parameter mismatch
project mismatch
DB fixture mismatch
```

不要立即修改 Tool。

先报告：

```text
发现问题：
现有实现：
实际错误：
根因：
是否需要核心修改：
```

只有能够确认是本阶段必须修复的真实 bug，才允许最小修改。

---

# 三十二、测试命令

首先运行非 DB：

```powershell
python -m pytest -q tests/test_tool_chat_capability.py tests/test_tool_chat_project_context.py tests/test_tool_chat_execution_boundary.py tests/test_tool_chat_service.py tests/test_tool_chat_api.py tests/test_tool_chat_architecture_contract.py
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_tool_chat_real_get_inventory.py
```

然后：

```powershell
python -m pytest -q
```

最后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

以及：

```powershell
python -m compileall -q backend
```

LSP 使用当前项目已有检查方式。

不要安装新的 lint 工具。

---

# 三十三、回归要求

Step 10 基线：

```text
2733 passed / 317 skipped
3009 passed / 41 skipped
```

Step 11 后：

允许增加测试。

但：

```text
既有测试不得失败
```

尤其：

```text
ToolChatService
ToolExecutionService
AIOrchestrator
ToolRegistry
ToolArgumentExtractor
Capability
ProjectContext
```

相关测试必须全部通过。

---

# 三十四、最终报告

严格按照：

```text
【Phase 3.11 Step 11 COMPLETE】

1. 修改文件
2. Real get_inventory
3. ToolChatService
4. ToolExecutionService
5. Capability
6. ProjectContext
7. PostgreSQL
8. Function Calling
9. Argument
10. Schema Validation
11. Security
12. Project Isolation
13. Multi-Step
14. API
15. Tests
16. compile / LSP
17. DB writes
18. Network
19. Real LLM
20. 当前限制
```

重点给出：

```text
Before:

ToolChatService
    ↓
ToolExecutionService
    ↓
Mock Tool
```

变成：

```text
After:

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
```

同时保留：

```text
Mock Tool
```

用于单元测试。

最终明确：

```text
Real get_inventory = PASS
Real get_work_order = DEFERRED
Capability = PASS
ProjectContext = PASS
DB writes = 0
Real LLM = NO
```

以及：

```text
Agent = NOT IMPLEMENTED
MCP = NOT IMPLEMENTED
LangGraph = NOT IMPLEMENTED
Memory = NOT IMPLEMENTED
Planning = NOT IMPLEMENTED
```

**Phase 3.11 Step 11 完成后立即停止。**

不要进入 Step 12。

不要接入 `get_work_order`。

不要增加第三个 Tool。

不要实现 Agent / MCP / LangGraph / Memory / Planning。

等待下一步指令。
