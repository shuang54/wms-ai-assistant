# Phase 3.12 Step 63 — Assistant Outcome 最小生产实现

## 一、阶段目标

基于 Phase 3.12 Step 61 / Step 62 的审计结果：

```text
OUTCOME_BOUNDARY_MISSING
```

本阶段正式实现：

> **最小 Assistant-level Outcome Contract。**

只解决：

```text
AIOrchestrator
      ↓
Assistant Outcome
      ↓
/api/ai/chat
      ↓
metadata.outcome
```

固定四种 Outcome：

```text
SUCCESS
EMPTY
REFUSED
FAILED
```

本阶段不实现：

```text
error_class
Trace Outcome persistence
Unified Timeline
Pagination
Cursor
```

---

# 二、严格范围

## 允许修改

原则上只允许：

```text
backend/app/services/ai_orchestrator_service.py
backend/app/api/orchestrator_chat.py
backend/app/dto/
tests/
docs/architecture.md
```

如果当前项目已有最自然的 Result/DTO 文件，应优先复用。

可以新增一个非常小的：

```text
AssistantOutcome
```

但只有当前代码确实需要类型安全时才新增。

---

## 禁止修改

禁止修改：

```text
RAG 核心逻辑
Tool Framework
TextToSQLService
TextToSQL Generator
SQLValidator
SQLExecutor
LLMProvider
LLMClient
Assistant Trace persistence
LLM Usage
Tool Execution persistence
RAG Execution persistence
Project Context
Router 核心路由策略
Prompt
数据库 Schema
数据库 Index
```

禁止：

```text
DeepSeek API
生产数据库写入
OpenTelemetry
Prometheus
Langfuse
Dashboard
```

---

# 三、开始前必须阅读

先阅读真实代码：

```text
backend/app/services/ai_orchestrator_service.py
backend/app/api/orchestrator_chat.py

backend/app/dto/
backend/app/services/assistant_trace_service.py
```

以及：

```text
backend/app/services/rag_service.py
backend/app/services/tool_chat_service.py
backend/app/services/text_to_sql_service.py
```

重点确认当前：

```text
AIOrchestrationResult
ChatResponse
RouteDecision
```

真实字段和异常行为。

不要假设接口。

---

# 四、Outcome Contract

实现固定：

```python
SUCCESS
EMPTY
REFUSED
FAILED
```

如果使用 Enum：

```text
AssistantOutcome
```

必须：

```text
stable
small
serializable
```

不要增加：

```text
UNKNOWN
PARTIAL
TIMEOUT
CANCELLED
RETRYING
```

这些不是本阶段的 Business Outcome。

---

# 五、Outcome 判定责任

Outcome 属于：

> **Assistant / Orchestrator 层。**

不要放到：

```text
RagService
ToolService
TextToSQLService
LLMProvider
```

原因：

```text
LLM success
Tool success
RAG success
Executor success
```

都只是底层执行状态。

最终：

```text
Assistant Outcome
```

必须由 Orchestrator 层统一决定。

---

# 六、判定输入白名单

Outcome 判定只能使用：

```text
HTTP / execution exception state
route
refused
tool_success
rag_used_chunks
```

以及当前 Orchestrator 已经产生的业务 Result。

禁止直接使用：

```text
exception.message
prompt
messages
SQL
RAG chunks
Tool arguments
Tool raw result
API key
Authorization
DATABASE_URL
stack trace
raw provider response
```

特别注意：

> 不要通过 content 文本匹配来判断 Outcome。

禁止：

```python
if "失败" in content:
```

禁止：

```python
if "没有找到" in content:
```

---

# 七、判定优先级

保持 Step 62 已确定：

```text
REFUSED
    ↓
FAILED
    ↓
EMPTY
    ↓
SUCCESS
```

即：

```text
REFUSED > FAILED > EMPTY > SUCCESS
```

但是不要把这个实现成复杂 priority framework。

一个简单、明确的判定函数即可。

---

# 八、REFUSED

当前已知：

```text
HTTP 200
route=text_to_sql
metadata.refused=true
data=None
Validator=0
Executor=0
LLM=1
```

必须：

```text
outcome = REFUSED
```

即使未来某个组合同时存在其他失败信号：

```text
refused=true
```

优先保持：

```text
REFUSED
```

---

# 九、FAILED

以下必须得到：

```text
FAILED
```

### 1. LLM / Provider exception

```text
HTTP 500
```

### 2. RAG runtime failure

```text
HTTP 500
```

### 3. Text-to-SQL retry exhausted

```text
HTTP 500
```

### 4. SQL execution failure

```text
HTTP 500
```

### 5. Tool failure

当前特殊情况：

```text
HTTP 200
metadata.tool_success=false
```

必须：

```text
outcome = FAILED
```

不能因为 HTTP 200 而返回：

```text
SUCCESS
```

### 6. Capability disabled

当前：

```text
HTTP 403
```

必须：

```text
FAILED
```

---

# 十、EMPTY

当前明确场景：

```text
RAG
rag_used_chunks=0
LLM calls=0
HTTP 200
```

必须：

```text
outcome = EMPTY
```

注意：

只有在：

```text
RAG route
```

以及：

```text
rag_used_chunks == 0
```

的上下文下才能使用 EMPTY。

禁止：

```text
row_count == 0 → EMPTY
```

因为 Step 62 已确定：

```text
T2SQL row_count=0
```

仍然可以代表：

```text
SUCCESS
```

例如：

```text
查询结果为空
```

本身就是有效业务结果。

---

# 十一、SUCCESS

满足：

```text
不是 REFUSED
不是 FAILED
不是 EMPTY
```

且请求正常完成。

例如：

### RAG

```text
chunks >= 1
answer generated
```

→

```text
SUCCESS
```

### Tool

```text
tool_success=true
```

→

```text
SUCCESS
```

### Text-to-SQL

```text
execution success
```

→

```text
SUCCESS
```

### Text-to-SQL row_count=0

→

```text
SUCCESS
```

不要因为：

```text
row_count=0
```

判定 EMPTY。

---

# 十二、重要：不要从 HTTPException 重新构造 Outcome

如果当前：

```text
AIOrchestratorService.execute()
```

发生异常：

不要让 API 层通过：

```text
HTTP status
```

重新猜测完整业务 Outcome。

优先让：

```text
Orchestrator
```

在业务执行边界决定：

```text
Outcome
```

然后 API 层只负责：

```text
Result → HTTP response
```

如果当前架构无法做到这一点而需要最小调整：

先检查是否可以通过现有 Result / metadata 传递。

不要重构异常体系。

---

# 十三、推荐实现方式

优先采用：

```text
AIOrchestrationResult
        │
        ├── route
        ├── content
        ├── data
        └── metadata
                │
                └── outcome
```

最终：

```text
AIOrchestrationResult.metadata["outcome"]
```

例如：

```json
{
  "route": "rag",
  "content": "...",
  "data": null,
  "metadata": {
    "request_id": "...",
    "outcome": "SUCCESS"
  }
}
```

本阶段：

> **不修改顶层 ChatResponse envelope。**

保持：

```text
{
  route,
  content,
  data,
  metadata
}
```

这样最大限度保持 backward compatibility。

---

# 十四、Metadata 安全

`metadata.outcome` 只能是：

```text
SUCCESS
EMPTY
REFUSED
FAILED
```

禁止：

```text
exception
error_message
stacktrace
raw_error
SQL
prompt
messages
```

不要顺便加入：

```text
error_class
```

Step 62 已经决定：

```text
error_class = future optional extension
```

---

# 十五、不要修改现有 metadata 语义

保留：

```text
request_id
decision_source
route_reason
knowledge_scope
rag_used_chunks
tool_name
tool_success
row_count
truncated
execution_time_ms
selected_tables
project_id
refused
```

本阶段只是增加：

```text
outcome
```

不要删除或重命名已有字段。

---

# 十六、Tool Failure 特别处理

当前：

```text
Tool handler failure
↓
HTTP 200
↓
tool_success=false
```

必须验证：

```text
metadata.outcome == FAILED
```

但：

```text
HTTP status
```

保持：

```text
200
```

不要为了 Outcome 而把 Tool failure 改成 HTTP 500。

这是本阶段非常重要的 backward compatibility 要求。

---

# 十七、RAG Empty 特别处理

当前：

```text
HTTP 200
route=rag
rag_used_chunks=0
LLM=0
```

增加：

```text
metadata.outcome=EMPTY
```

不要修改：

```text
content
HTTP status
```

不要调用 LLM。

---

# 十八、Refusal 特别处理

保持：

```text
HTTP 200
route=text_to_sql
refused=true
data=None
```

新增：

```text
metadata.outcome=REFUSED
```

同时保持：

```text
LLM calls=1
Validator=0
Executor=0
```

---

# 十九、T2SQL Retry

当前：

```text
attempt1
→ Validator reject
→ attempt2
→ valid
→ execution
```

最终：

```text
outcome=SUCCESS
```

不要：

```text
outcome=RETRY
```

不要：

```text
outcome=PARTIAL
```

LLM usage 仍然记录每次真实调用。

---

# 二十、T2SQL Retry Exhausted

当前：

```text
LLM attempts exhausted
→ HTTP 500
```

必须：

```text
outcome=FAILED
```

如果当前失败路径无法返回 `AIOrchestrationResult`：

可以让 API 层使用一个最小的失败 metadata：

```text
metadata.outcome=FAILED
```

但：

**不要改变现有 detail 文本结构。**

---

# 二十一、异常路径的 API Contract

如果当前异常响应是：

```json
{
  "detail": "AI 能力执行失败: ..."
}
```

本阶段：

优先保持：

```text
detail
```

完全兼容。

如果要增加 outcome：

只能在确认不会破坏现有客户端的前提下进行。

例如不要直接改成：

```json
{
  "detail": "...",
  "outcome": "FAILED"
}
```

除非当前 API response contract 已经支持额外字段。

否则：

> 对异常 HTTP response，本阶段允许只在内部 Outcome 计算；正常 200 Result 才通过 `metadata.outcome` 暴露。

如果这导致失败 Outcome 暂时不能对客户端完整暴露：

记录 limitation。

---

# 二十二、测试要求

新增或修改：

```text
tests/test_assistant_outcome.py
```

至少覆盖：

### SUCCESS

```text
RAG success
Tool success
T2SQL success
T2SQL row_count=0
```

### EMPTY

```text
RAG chunks=0
```

### REFUSED

```text
refused=true
```

### FAILED

```text
Tool success=false + HTTP 200
LLM failure
RAG failure
T2SQL retry exhausted
SQL execution failure
Capability disabled
```

---

# 二十三、优先测试真实 API 边界

测试：

```text
POST /api/ai/chat
```

不要只调用：

```text
calculate_outcome()
```

因为最终目标是：

```text
AIOrchestrator
→ Result
→ API
→ metadata.outcome
```

必须至少有一组真实 FastAPI TestClient 测试。

---

# 二十四、Backward Compatibility Tests

确认：

### 顶层字段没有变化

```text
ChatResponse.model_fields
```

仍然：

```text
route
content
data
metadata
```

### 原 metadata 保留

不要因为增加 outcome 删除：

```text
request_id
tool_success
refused
rag_used_chunks
```

### HTTP Status 保持

尤其：

```text
Tool failure = 200
Refusal = 200
RAG empty = 200
```

不要改变。

---

# 二十五、Security Tests

构造包含：

```text
API_KEY
Authorization
password
DATABASE_URL
SQL
prompt
messages
exception message
stack trace
```

的输入。

确认：

```text
metadata.outcome
```

只能是四个枚举之一。

并且：

```text
metadata
```

不因为 Outcome 计算而泄露这些内容。

---

# 二十六、Determinism

相同业务结果：

```text
same input
same execution state
```

必须：

```text
same outcome
```

不要：

```text
random
time
LLM judge
```

---

# 二十七、Concurrency

增加轻量测试：

```text
5 concurrent requests
```

确认：

```text
request A outcome
request B outcome
...
```

不会串线。

不要引入新的 global mutable state。

---

# 二十八、文档

修改：

```text
docs/architecture.md
```

新增：

```text
§8.14 Assistant Outcome Contract
```

内容简洁：

```text
Assistant Outcome
    ├── SUCCESS
    ├── EMPTY
    ├── REFUSED
    └── FAILED
```

并说明：

```text
Outcome ≠ HTTP status
Outcome ≠ LLM status
Outcome ≠ Tool status
Outcome ≠ RAG status
Outcome ≠ SQL execution status
```

以及：

```text
Current external representation:
metadata.outcome
```

同时明确：

```text
error_class = not implemented
Trace persistence = not implemented
```

---

# 二十九、不要实现 Trace Outcome

虽然 Step 62 推荐：

```text
Assistant Outcome Read Model
```

但本阶段不要做。

不要修改：

```text
assistant_trace_service.py
```

不要新增：

```text
assistant_outcome_record
```

不要新增数据库表。

Step 63 只建立：

```text
Runtime Outcome
```

---

# 三十、不要实现 Error Class

本阶段禁止：

```text
LLM_ERROR
RAG_ERROR
TOOL_ERROR
TEXT_TO_SQL_ERROR
SQL_EXECUTION_ERROR
```

不要冻结 enum。

只保留：

```text
FAILED
```

---

# 三十一、测试命令

先运行：

```powershell
python -m pytest -q tests/test_assistant_outcome.py
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests scripts
```

如果 lint unavailable：

```text
lint = unavailable
```

不要安装新工具。

---

# 三十二、Git Diff

完成后：

```powershell
git status --short
git diff --stat
git diff
```

必须确认：

```text
Production changes:
only Assistant Outcome boundary

No:
RAG core change
Tool core change
T2SQL core change
Validator change
Executor change
LLM Provider change
DB schema change
Prompt change
Trace persistence change
```

---

# 三十三、最终验收

必须满足：

```text
□ Outcome 四态固定
□ SUCCESS 正确
□ EMPTY 正确
□ REFUSED 正确
□ FAILED 正确
□ Tool failure 200 + FAILED
□ RAG empty 200 + EMPTY
□ Refusal 200 + REFUSED
□ T2SQL row_count=0 + SUCCESS
□ T2SQL retry success + SUCCESS
□ T2SQL retry exhausted + FAILED
□ LLM failure + FAILED
□ RAG failure + FAILED
□ SQL execution failure + FAILED
□ Capability disabled + FAILED
□ 顶层 API envelope 未变化
□ metadata.outcome 增量增加
□ 原 metadata 保留
□ 无 error_class
□ 无 Trace persistence
□ 无 DB migration
□ 无 Prompt 修改
□ 无新的全局状态
□ 并发无串线
□ Security 通过
□ Backward compatibility 通过
```

---

# 三十四、最终报告

严格按照：

```text
Phase 3.12 Step 63 完成报告

1. 修改文件
2. Assistant Outcome Contract
3. SUCCESS
4. EMPTY
5. REFUSED
6. FAILED
7. Outcome 判定责任
8. HTTP Compatibility
9. metadata.outcome
10. Tool Failure
11. RAG Empty
12. Refusal
13. T2SQL Retry
14. T2SQL Failure
15. Security
16. Concurrency
17. Tests
18. DB Tests
19. compileall
20. Git Diff
21. Production Code Changes
22. DB Schema Changes
23. API Contract Changes
24. Current Limitations

Phase 3.12 Step 63 READY
Phase 3.12 Step 63 STOP
```

---

# 三十五、硬停止

完成后立即停止。

不要进入：

```text
Step 64 Trace Outcome Persistence
Step 65 Unified Timeline
Step 66 Pagination
Step 67 Conversation
Step 68 Memory
Agent
MCP
OpenTelemetry
Dashboard
```

只完成：

```text
Runtime Assistant Outcome
```

等待下一步指令。
