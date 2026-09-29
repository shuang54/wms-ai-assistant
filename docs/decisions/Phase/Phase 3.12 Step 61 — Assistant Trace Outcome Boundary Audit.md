你现在开始执行：

# Phase 3.12 Step 61 — Assistant Trace Outcome Boundary Audit

## 一、阶段目标

本阶段只做：

> **Assistant Trace 最终业务 Outcome 边界审计。**

Step 60 已经证明：

```text
RAG
Tool
Text-to-SQL
```

三条路径可以正确关联到：

```text
assistant_request_id
```

Step 61 需要回答：

> 当前 Assistant Trace 除了知道“调用了什么”，是否已经能够可靠表达“这次 AI 请求最终是什么结果”？

重点审计：

```text
Request
   ↓
Route
   ↓
LLM / RAG / Tool / Text-to-SQL
   ↓
Business Result
   ↓
Success / Failure / Refusal / Empty
```

**本阶段只审计，不实现 Outcome。**

---

# 二、严格范围

允许：

```text
新增 audit tests
新增 evaluation 文档
必要的测试 fixture
阅读并分析现有 DTO / Service / API
```

禁止修改：

```text
AIOrchestrator
AIRouter
RagService
ToolChatService
TextToSQLService
LLMClient
LLMProvider
SQLValidator
SQLExecutor
ProjectContext
Assistant Trace Service
Assistant Trace API
LLM Usage
Tool Execution
RAG Execution
数据库 Schema
数据库 Index
Prompt
```

禁止新增：

```text
status
outcome
error_code
result_status
trace_status
success
```

等生产字段。

本阶段不要为了“完善 Trace”而修改 HTTP contract。

---

# 三、开始前必须阅读

先阅读真实代码：

```text
backend/app/services/ai_orchestrator_service.py
backend/app/api/orchestrator_chat.py

backend/app/services/assistant_trace_service.py

backend/app/dto/
backend/app/api/
```

以及：

```text
backend/app/services/rag_service.py
backend/app/services/tool_chat_service.py
backend/app/services/text_to_sql_service.py
```

重点搜索：

```text
AIOrchestrationResult
route
content
data
metadata
error
HTTPException
status
success
refusal
```

同时检查：

```text
tests/
docs/evaluation/
```

---

# 四、建立当前 Outcome 模型

不要设计新模型。

先从当前真实：

```text
AIOrchestrationResult
```

推导当前实际上能够表达哪些状态。

至少检查：

### 1. 正常 RAG

```text
route = RAG
content != empty
```

### 2. 正常 Tool

```text
route = TOOL
data/content
```

### 3. 正常 Text-to-SQL

```text
route = TEXT_TO_SQL
data/content
```

### 4. Empty

例如：

```text
RAG 没有检索结果
```

确认当前：

```text
HTTP status
route
content
data
metadata
```

具体是什么。

不要猜。

---

# 五、Refusal Outcome

重点审计当前已有 refusal contract。

检查：

```text
destructive request
        ↓
refusal
```

确认实际：

```text
route
content
metadata
HTTP status
LLM calls
Validator calls
Executor calls
```

特别确认：

```text
refusal
≠
transport failure
```

并且：

```text
refusal
≠
generic 500
```

如果当前已有专门字段/route：

记录实际情况。

不要新增字段。

---

# 六、Failure Outcome

分别检查：

## LLM failure

例如：

```text
Provider → 500
```

当前 API：

```text
HTTP status = ?
```

确认 Trace：

```text
Trace exists?
LLM usage exists?
RAG/Tool record exists?
```

---

## RAG failure

确认：

```text
RAG execution observation
```

在异常情况下是否已经记录。

以及：

```text
HTTP error
```

是否能够与：

```text
assistant_request_id
```

关联。

---

## Tool failure

确认：

```text
ToolExecutionRecord
```

异常情况下：

```text
status
error
request_id
```

目前到底保存了什么。

---

## Text-to-SQL failure

确认：

```text
Generator failure
Validator failure
Executor failure
```

三类情况当前是否能区分。

如果现有系统无法区分：

**只记录为 limitation。**

不要修改。

---

# 七、当前 Trace 能否回答这些问题

建立一个矩阵：

| 问题               | 当前是否能回答 | 数据来源              |
| ---------------- | ------- | ----------------- |
| 请求走了什么 Route     | ?       | AI result         |
| 是否调用 LLM         | ?       | LLM Usage         |
| 调用了多少次 LLM       | ?       | LLM Usage         |
| 是否调用 RAG         | ?       | RAG Execution     |
| 是否调用 Tool        | ?       | Tool Execution    |
| Tool 执行是否成功      | ?       | Tool Record       |
| RAG 是否成功         | ?       | RAG Record / HTTP |
| Text-to-SQL 是否成功 | ?       | 现有 Result         |
| 最终 HTTP 是否成功     | ?       | API               |
| 最终业务结果是否为空       | ?       | Result            |
| 是否 refusal       | ?       | Result            |
| 为什么失败            | ?       | Error / Record    |
| 最终返回内容           | ?       | AI API Response   |

**必须根据代码实际情况填写。**

---

# 八、区分三个概念

非常重要。

不要混淆：

```text
LLM Success
```

```text
Tool Success
```

```text
Assistant Request Success
```

例如：

```text
LLM = SUCCESS
Tool = SUCCESS
Assistant = FAILURE
```

理论上可能发生：

```text
LLM 成功生成
→ Tool 执行成功
→ 最终业务结果组装失败
```

反过来也可能：

```text
LLM = SUCCESS
→ Validator reject
→ semantic retry
→ 第二次成功
→ Assistant = SUCCESS
```

所以：

> **底层 execution success ≠ Assistant-level outcome。**

本阶段重点就是确认当前系统有没有能力表达这个区别。

---

# 九、Route 与 Outcome 不要混淆

检查：

```text
route = RAG
```

并不等于：

```text
success = true
```

同样：

```text
route = TEXT_TO_SQL
```

也不等于：

```text
SQL result correct
```

因此审计：

```text
route
```

目前只是：

> 能力选择结果

而不是：

> 最终业务 Outcome

记录这一点。

---

# 十、Metadata 审计

检查当前：

```text
AIOrchestrationResult.metadata
```

实际有哪些字段。

特别关注：

```text
request_id
attempts
provider
model
execution_time
```

等。

列出：

```text
Field
Meaning
Source
Safe?
```

不要添加新字段。

---

# 十一、安全边界

确认当前 Outcome/Trace 没有为了表达错误而泄露：

```text
API key
Authorization
password
DATABASE_URL
prompt
messages
raw provider response
SQL
Tool arguments
ToolResult.data
RAG chunk content
stack trace
```

尤其检查：

```text
exception message
```

是否可能通过 API 暴露。

如果存在风险：

**不要直接修改。**

记录：

```text
Security finding
Layer
Impact
```

然后 STOP。

---

# 十二、Audit Tests

建议新增：

```text
tests/test_assistant_trace_outcome_audit.py
```

如果已有更合适的测试文件，则遵循现有结构。

至少覆盖：

### Test 1

正常 RAG：

```text
route
content
request_id
trace
```

### Test 2

正常 Tool：

```text
route
tool record
trace
```

### Test 3

正常 Text-to-SQL：

```text
route
LLM usage
trace
```

### Test 4

Refusal：

```text
refusal
```

### Test 5

LLM failure：

```text
HTTP error
```

### Test 6

RAG failure：

```text
HTTP error
RAG observation
```

### Test 7

Tool failure：

```text
HTTP error
Tool record
```

### Test 8

Empty result：

```text
HTTP status
content/data
```

这些测试主要是：

> **读取并断言当前行为。**

不要为了测试而修改生产逻辑。

---

# 十三、不要创建 Outcome DTO

本阶段明确禁止新增：

```text
AssistantOutcome
AssistantTraceOutcome
TraceStatus
OutcomeStatus
```

等 DTO。

如果审计发现确实需要：

记录：

```text
Future design candidate
```

但不要实现。

---

# 十四、是否需要下一阶段增加 Outcome

最终给出判断：

### Option A

如果当前已有足够信息：

```text
Outcome information = SUFFICIENT
```

则：

```text
Do not add Outcome layer yet
```

### Option B

如果明显缺少：

```text
Assistant-level success/failure/refusal/empty
```

则：

```text
Outcome boundary = MISSING
```

但只提出：

```text
Future Step Candidate
```

不要实现。

---

# 十五、测试运行

执行：

```powershell
python -m pytest -q tests/test_assistant_trace_outcome_audit.py
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

如果 lint unavailable：

```text
lint = unavailable
```

不要安装工具。

---

# 十六、Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff
```

必须确认：

```text
Production code changes = 0
API contract changes = 0
DB schema changes = 0
Index changes = 0
Prompt changes = 0
```

如果出现 production code 修改：

**立即 STOP 并报告。**

---

# 十七、Evaluation 文档

新增：

```text
docs/evaluation/phase-3.12-step-61-assistant-trace-outcome-audit.md
```

记录：

## 1. Scope

```text
Outcome boundary audit
No production implementation
```

## 2. Current Result Contract

记录：

```text
route
content
data
metadata
```

## 3. Outcome Matrix

记录：

```text
RAG
Tool
Text-to-SQL
Refusal
Empty
LLM failure
RAG failure
Tool failure
```

## 4. Current Trace Capability

明确：

```text
What Trace can answer
What Trace cannot answer
```

## 5. Security

确认没有：

```text
secrets
prompt
SQL
raw response
internal stack trace
```

## 6. Decision

只能是：

```text
SUFFICIENT
```

或者：

```text
OUTCOME_BOUNDARY_MISSING
```

如果是后者：

不要实现，只记录未来候选。

---

# 十八、最终报告

严格按照：

```text
Phase 3.12 Step 61 完成报告

1. Audit Scope
2. Current AIOrchestrationResult
3. RAG Outcome
4. Tool Outcome
5. Text-to-SQL Outcome
6. Refusal Outcome
7. Empty Outcome
8. Failure Outcome
9. Trace Capability Matrix
10. Metadata Audit
11. Security
12. Tests
13. DB Tests
14. compileall
15. Git Diff
16. Production Code Changes
17. DB Schema Changes
18. Outcome Decision
19. Current Limitations

Phase 3.12 Step 61 STOP
```

---

# 十九、强制 STOP

完成后：

**立即停止。**

不要实现：

```text
Outcome DTO
Pagination
Cursor
Unified Timeline
Conversation
Memory
Agent
MCP
OpenTelemetry
Dashboard
Cost Tracking
```

不要修改生产代码。

等待下一步指令。
