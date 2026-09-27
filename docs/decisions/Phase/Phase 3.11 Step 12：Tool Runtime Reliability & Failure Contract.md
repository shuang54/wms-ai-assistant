你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.11 Step 12：Tool Runtime Reliability & Failure Contract

## 一、阶段目标

Phase 3.11 Step 11 已经完成：

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
    ↓
LLM
    ↓
Final Answer
```

本阶段不增加新的业务 Tool。

唯一目标：

**验证当前 Tool Runtime 在正常、拒绝、异常、边界情况下具有稳定且明确的失败契约。**

重点验证：

```text
Function Calling
        ↓
ToolExecutionService
        ↓
Capability
        ↓
ToolRegistry
        ↓
Real Tool
```

出现异常时：

**不能绕过执行边界、不能静默降级、不能错误重试、不能泄露内部信息。**

---

# 二、严格范围

## 允许

* 新增 Tool Runtime regression tests
* 新增少量测试辅助代码
* 新增 evaluation 文档
* 完善已有 Tool Failure Contract 测试
* 使用 Fake / Scripted LLM
* 使用现有 PostgreSQL fixture
* 使用真实 `get_inventory`
* 使用现有 Mock Tool 验证 Service-level failure semantics

## 禁止

不要修改：

```text
AI Router
AIOrchestrator
ToolArgumentExtractor
ToolRegistry 核心 schema 行为
get_inventory SQL
get_inventory Handler
ToolExecutionService 核心执行逻辑
Business Semantic
Database Schema
```

禁止：

```text
Agent
LangGraph
MCP
Memory
Planning
Multi-Agent
Retry
自动修复
自动重规划
Parallel Tool Calling
```

不要接入：

```text
get_work_order
第三个 Tool
```

不要增加：

```text
audit table
request_id persistence
tool_call_log table
```

不要修改数据库结构。

不要增加真实 LLM 调用。

---

# 三、开始前必须先阅读

先阅读真实实现：

```text
backend/app/services/tool_chat_service.py
backend/app/services/tool_execution_service.py
backend/app/tools/registry.py
backend/app/tools/get_inventory.py
backend/app/api/tool_chat.py
backend/app/projects/
```

以及现有：

```text
tests/test_tool_chat_service.py
tests/test_tool_chat_execution_boundary.py
tests/test_tool_chat_capability.py
tests/test_tool_chat_project_context.py
tests/test_tool_chat_real_get_inventory.py
tests/test_tool_chat_architecture_contract.py
```

重点确认当前真实异常类型和错误转换方式。

**不要假设错误类型。**

---

# 四、建立 Tool Failure Matrix

先根据真实代码建立如下矩阵。

## Case A：Capability Denied

```text
LLM
 ↓
ToolCall(get_inventory)
 ↓
ToolExecutionService
 ↓
Capability denied
```

必须：

```text
AIOrchestratorCapabilityError
HTTP 403
Handler = 0
DB = 0
```

不能：

```text
ToolResult(False)
```

不能继续下一轮 LLM。

---

# 五、Case B：Unknown Tool

Fake LLM 返回：

```text
tool_name = "unknown_tool"
```

验证：

```text
ToolExecutionService
 ↓
Registry / execution boundary
 ↓
Tool not registered
```

要求确认当前真实行为。

重点验证：

1. 不猜测 Tool
2. 不 fallback 到其他 Tool
3. 不执行任何 Handler
4. 不访问 PostgreSQL
5. 不修改 ToolCall name
6. 不重新调用 LLM 试图“修复”

如果当前实现已有明确错误类型：

**复用，不新增错误类型。**

---

# 六、Case C：Missing Required Argument

Fake LLM：

```json
{
  "tool_name": "get_inventory",
  "arguments": {}
}
```

验证：

```text
ToolRegistry schema validation
 ↓
reject
```

必须：

```text
Handler = 0
SQL = 0
DB = 0
```

不要让：

```text
ToolHandler
```

自己兜底。

Registry 仍然是 Schema Validation 唯一入口。

---

# 七、Case D：Unknown Argument

Fake LLM：

```json
{
  "tool_name": "get_inventory",
  "arguments": {
    "material_code": "MAT-001",
    "fake_field": "xxx"
  }
}
```

验证：

```text
Registry reject
```

要求：

```text
SQL = 0
Handler = 0
```

不能静默删除：

```text
fake_field
```

不能自动修复参数。

---

# 八、Case E：Invalid Argument Type

如果真实 Tool Schema 支持：

```text
material_code: string
```

Fake LLM 返回：

```json
{
  "material_code": 123
}
```

验证：

```text
Schema Validation Reject
```

要求：

```text
Handler = 0
DB = 0
```

不要让 Python 自动 coercion 掩盖 Schema 错误。

如果当前 Registry 的 schema validator 本身允许该类型：

**记录真实行为，不要为了本测试修改 Registry。**

---

# 九、Case F：Handler Business Validation Failure

使用真实：

```text
get_inventory
```

构造一个：

```text
material_code
```

能够通过 JSON Schema，

但无法通过 Handler 业务字符集校验的值。

例如：

```text
' OR 1=1 --
```

验证：

```text
Registry schema = PASS
Handler validation = REJECT
SQL = 0
```

确认：

```text
ToolResult(False)
```

或者当前真实错误契约。

不要修改 Handler。

---

# 十、Case G：Real PostgreSQL Read Failure

如果现有测试基础设施能够安全模拟 DB failure：

例如：

```text
connection unavailable
```

或现有 Fake Engine / Repository failure mechanism。

优先复用。

验证：

```text
Tool
 ↓
DB failure
 ↓
ToolExecutionService
 ↓
ToolResult(False)
```

重点确认：

1. 不暴露 connection string
2. 不暴露 password
3. 不暴露 SQLAlchemy Connection
4. 不暴露完整数据库异常 traceback
5. 不进入无限 retry
6. 不自动重新执行 Tool

如果当前架构没有安全、稳定的 DB failure injection：

**不要为了测试强行修改 DB 层。**

可以跳过，并在文档记录：

```text
DB failure injection unavailable
```

---

# 十一、Case H：Tool Failure → Multi-Step

验证现有：

```text
ToolChatService
```

Multi-Step 行为。

例如：

```text
LLM
 ↓
get_inventory → failure
 ↓
ToolResult(False)
 ↓
LLM
 ↓
Final Answer
```

确认当前设计：

**Tool failure 可以作为 ToolResult 返回给 LLM，并按照现有 Multi-Step contract 继续。**

但注意：

Capability Denied 与普通 Tool failure 必须区分：

```text
Capability Denied
→ Exception
→ 403
→ 不继续 LLM

Tool Execution Failure
→ ToolResult(False)
→ 按现有 ToolChatService contract 处理
```

不要改变这个语义。

---

# 十二、Case I：Budget Boundary

验证：

```text
max_tool_rounds
```

当前范围：

```text
[1, 20]
```

验证至少：

```text
max_rounds = 1
max_rounds = 20
```

确认：

1. 不超过预算
2. Tool 不多执行
3. LLM 不多调用
4. budget exceeded 使用现有错误
5. 不自动 retry

不要修改 budget 逻辑。

---

# 十三、Case J：Multiple Tool Calls

如果当前 ToolChatService 已经规定：

```text
>1 ToolCall / round
→ MultipleToolCallsError
```

增加/确认一个 regression test。

Fake LLM 一轮返回：

```text
get_inventory
get_inventory
```

验证：

```text
MultipleToolCallsError
```

并确认：

```text
Real Handler = 0
DB = 0
```

即：

**拒绝发生在任何 Tool Execution 之前。**

---

# 十四、Case K：Malformed Tool Call

复用现有：

```text
LLMToolCallFormatError
```

测试：

```text
invalid arguments JSON
```

或当前真实 parser 能够稳定构造的 malformed ToolCall。

要求：

```text
Parser Reject
↓
No Tool Execution
↓
No DB
```

不要修改 parser。

---

# 十五、Real get_inventory DB Regression

增加一个最小真实 DB regression：

```text
project-a
    ↓
get_inventory
    ↓
MAT-001
    ↓
250.0
```

确认仍然：

```text
SELECT only
READ ONLY
bound parameter
statement timeout
rollback
```

不要重复 Step 11 的全部测试。

本阶段只做：

**核心 happy-path regression。**

---

# 十六、Security Regression

确认以下全部保持：

```text
Unknown Tool         → reject
Missing argument     → reject
Unknown argument     → reject
Invalid type         → reject
Injection            → reject
Capability denied    → reject
Multiple Tool Calls  → reject
Malformed Tool Call  → reject
```

并确认：

```text
DB writes = 0
```

---

# 十七、错误信息安全

增加一个专门测试：

任何 Tool Failure / DB Failure / Validation Failure 的：

```text
ToolResult.error
HTTP error
exception message
metadata
```

都不能包含：

```text
API key
password
database URL
connection string
Authorization header
```

如果现有实现已经做到了：

**只增加测试，不修改生产代码。**

如果发现真实泄露：

**停止并报告，不要为了测试随意重构。**

---

# 十八、Architecture Contract

在：

```text
tests/test_tool_chat_architecture_contract.py
```

增加：

```text
C14 Tool Failure Boundary
```

至少锁定：

```text
Capability denied
    → no Handler

Schema rejected
    → no Handler

Multiple ToolCall
    → no Tool execution

Malformed ToolCall
    → no Tool execution

Unknown Tool
    → no fallback Tool
```

同时确认：

```text
ToolChatService
    → ToolExecutionService
    → ToolRegistry
    → Tool
```

仍然是唯一执行链。

---

# 十九、不要新增生产 Tool

本阶段明确：

```text
Real Tool:
    get_inventory

Deferred:
    get_work_order
```

不要修改：

```text
get_work_order
```

不要注册：

```text
第三个 Tool
```

Step 12 完成后：

```text
Real Tools = 1
```

保持不变。

---

# 二十、测试文件

新增：

```text
tests/test_tool_runtime_failure_contract.py
```

建议：

```text
15~25 tests
```

重点覆盖：

```text
Capability
Unknown Tool
Missing Args
Unknown Args
Invalid Type
Handler Validation
DB Failure（如果可安全注入）
Tool Failure + Multi-Step
Budget
Multiple Tool Calls
Malformed Tool Call
Security
Error Sanitization
```

不要为了数量重复已有测试。

---

# 二十一、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11 Step 12 — Tool Runtime Failure Contract.md
```

记录：

## 1. Scope

```text
Real Tool = get_inventory
Real DB = PostgreSQL
Fake LLM = ScriptedLLMClient
```

## 2. Failure Matrix

例如：

```text
Case                    Result
Capability denied       PASS
Unknown Tool            PASS
Missing argument        PASS
Unknown argument        PASS
Invalid type             PASS
Handler validation      PASS
DB failure              PASS / SKIP
Multiple ToolCall       PASS
Malformed ToolCall      PASS
Budget                  PASS
Injection               PASS
Error sanitization      PASS
```

## 3. Execution Boundary

```text
ToolChatService
    ↓
ToolExecutionService
    ↓
Capability
    ↓
ToolRegistry
    ↓
Real Tool
```

## 4. DB Safety

```text
DB writes = 0
```

## 5. Current Limitations

如实记录。

---

# 二十二、测试命令

先执行定向：

```powershell
python -m pytest -q `
tests/test_tool_runtime_failure_contract.py `
tests/test_tool_chat_service.py `
tests/test_tool_chat_execution_boundary.py `
tests/test_tool_chat_capability.py `
tests/test_tool_chat_project_context.py `
tests/test_tool_chat_architecture_contract.py
```

真实 DB：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_tool_runtime_failure_contract.py
```

然后：

```powershell
python -m pytest -q
```

再：

```powershell
$env:RUN_DB_TESTS="1"
python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend
```

继续使用项目现有 LSP 检查。

不要安装新的 lint 工具。

---

# 二十三、失败处理

如果发现：

```text
Capability failure
Tool failure
Registry failure
DB failure
Error leakage
```

不要立即修改核心代码。

先定位：

```text
ToolChatService
ToolExecutionService
ToolRegistry
get_inventory
API
```

如果属于已有实现缺陷：

先报告：

```text
问题：
影响：
根因：
是否修改：
```

只有确认是本阶段必须修复的安全/契约缺陷，才允许最小修改。

---

# 二十四、Baseline

Phase 3.11 Step 11 baseline：

```text
Full no DB:
2743 passed / 327 skipped

Full DB:
3029 passed / 41 skipped

DB writes:
0

Network:
0

compileall:
clean
```

Step 12 不应破坏已有测试。

---

# 二十五、最终报告格式

完成后严格按照：

```text
【Phase 3.11 Step 12 COMPLETE】

1. 新增文件
2. 修改文件
3. Failure Contract
4. Capability Denied
5. Unknown Tool
6. Argument Validation
7. Handler Validation
8. DB Failure
9. Multi-Step Failure
10. Budget Boundary
11. Multiple ToolCall
12. Malformed ToolCall
13. Error Sanitization
14. Security Regression
15. Real get_inventory Regression
16. Architecture Contract C14
17. 测试结果
18. DB writes
19. Network
20. Real LLM
21. 当前限制
22. 是否修改核心代码
```

最后固定写：

```text
Real Tool = get_inventory
get_work_order = DEFERRED
Third Tool = NOT ADDED

Agent = NOT IMPLEMENTED
MCP = NOT IMPLEMENTED
LangGraph = NOT IMPLEMENTED
Memory = NOT IMPLEMENTED
Planning = NOT IMPLEMENTED

Phase 3.11 Step 12 完成。

立即停止。
不要进入 Step 13。
```

# 二十六、最重要规则

严格执行：

```text
阅读
→ Failure Matrix
→ Tests
→ Security Regression
→ Real DB Regression
→ Architecture Contract
→ Full Regression
→ 汇报
→ STOP
```

**完成 Phase 3.11 Step 12 后立即停止，等待下一步指令。**
