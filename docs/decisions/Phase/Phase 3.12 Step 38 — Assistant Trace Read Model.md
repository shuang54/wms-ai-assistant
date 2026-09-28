你现在开始执行：

# Phase 3.12 Step 38 — Assistant Trace Read Model

## 一、阶段目标

Phase 3.12 Step 35～37 已经完成：

```text
Assistant request_id
        │
        ├── LLMUsageRecord.assistant_request_id
        │       └── LLMUsageQueryService.list_by_assistant_request_id()
        │
        └── ToolExecutionRecord.request_id
                └── ToolObservabilityQueryService
```

本阶段只完成：

> **建立一个应用层只读 `AssistantTraceQueryService`，将同一个 Assistant request_id 对应的 LLM Usage 与 Tool Execution 组合成一个 Trace Read Model。**

目标：

```text
Assistant request_id = A
        │
        ├── LLM Usage[]
        │
        └── Tool Execution[]
                ↓
        AssistantTraceView
```

这是：

```text
Read Model / Query Composition
```

不是：

```text
Trace System
```

---

# 二、严格范围

允许：

```text
backend/app/services/assistant_trace_query_service.py
```

如果现有架构已有合适位置，则使用最自然的位置。

允许新增：

```text
AssistantTraceView
AssistantTraceLLMUsageView
AssistantTraceToolExecutionView
```

或者复用现有：

```text
LLMUsageTraceRecordView
ToolExecutionSnapshot
```

**优先复用现有 DTO。**

允许新增：

```text
tests/test_assistant_trace_query_service.py
tests/test_assistant_trace_query_service_db.py
```

允许新增：

```text
docs/evaluation/Phase 3.12 Step 38 — Assistant Trace Read Model.md
```

允许对：

```text
docs/architecture.md
```

增加一个很小的架构说明。

---

# 三、明确禁止

本阶段禁止：

```text
Conversation
Memory
Agent
MCP
Planning
Streaming
Dashboard
OpenTelemetry
Prometheus
Redis
Kafka
Celery
Trace Span
Trace Parent/Child
```

禁止：

```text
新增 HTTP API
```

禁止修改：

```text
AIOrchestrator
AI Router
RAG
ToolExecutionService
ToolExecutionRecord
Tool Observability
LLM Provider
Text-to-SQL
SQL Validator
SQL Executor
LLM Usage 写入逻辑
```

禁止修改：

```text
/api/ai/chat
/api/usage/analytics
/api/observability/tools
/api/observability/tools/history
/api/observability/tools/metrics
/api/observability/tools/metrics/persistent
```

---

# 四、开始前先阅读

先不要修改。

阅读：

```text
backend/app/services/llm_usage_query_service.py
backend/app/services/tool_observability_query_service.py
backend/app/services/tool_execution_persistent_query_service.py

backend/app/db/llm_usage_repository.py
backend/app/db/tool_execution_repository.py

backend/app/services/assistant_trace.py

tests/
```

重点确认：

1. LLM Usage Read DTO。
2. Tool Execution Snapshot。
3. 两边的查询入口。
4. 两边的异常类型。
5. 两边是否已经做 input validation。
6. 两边是否返回稳定排序。
7. 两边是否存在 DB / memory 两种数据源。
8. 是否能够在 Service 层组合，而不访问 Repository/Session。

---

# 五、最重要的架构规则

`AssistantTraceQueryService`：

**只能依赖 Read Service / Query Service。**

正确：

```text
AssistantTraceQueryService
        │
        ├── LLMUsageQueryService
        │
        └── ToolObservabilityQueryService
```

禁止：

```text
AssistantTraceQueryService
        ↓
LLMUsageRepository
```

或者：

```text
AssistantTraceQueryService
        ↓
ToolExecutionRepository
```

也禁止：

```text
AssistantTraceQueryService
        ↓
SQLAlchemy Session
```

也禁止：

```text
AssistantTraceQueryService
        ↓
ORM Model
```

这是本阶段最重要的 Architecture Boundary。

---

# 六、Trace View 设计

建议最小：

```python
@dataclass(frozen=True)
class AssistantTraceView:
    assistant_request_id: str
    llm_usage: tuple[LLMUsageTraceRecordView, ...]
    tool_executions: tuple[ToolExecutionSnapshot, ...]
```

如果项目 DTO 风格不允许直接这样设计：

按照现有 frozen DTO 风格调整。

关键要求：

```text
immutable
explicit fields
no ORM
no Session
no Repository
no raw SQL
```

---

# 七、为什么使用 tuple

内部 Read Model 推荐：

```text
tuple
```

而不是：

```text
list
```

原因：

```text
Trace View = read-only snapshot
```

避免调用方修改：

```text
trace.llm_usage
trace.tool_executions
```

如果项目现有 DTO 统一使用 list：

可以遵循项目既有风格。

**不要为了 tuple 改动大量现有代码。**

---

# 八、LLM Usage 查询

调用：

```text
LLMUsageQueryService.list_by_assistant_request_id(A)
```

不要重新查询数据库。

返回：

```text
LLMUsageTraceRecordView[]
```

保持 Step 37 的顺序：

```text
created_at ASC
id ASC
```

不要重新排序。

---

# 九、Tool 查询

这里必须先确认当前：

```text
ToolObservabilityQueryService
```

到底查询：

```text
memory runtime collector
```

还是：

```text
persistent DB
```

### 如果当前 QueryService 只有 Runtime Memory

本阶段：

**只使用现有 Runtime Read Boundary。**

不要为了 Step 38 新增 DB 查询。

### 如果当前已经有 Persistent Query Service

可以根据现有架构选择：

```text
persistent
```

但必须明确记录数据源。

### 最重要

不要：

```text
Runtime Tool Records
+
Persistent Tool Records
```

直接合并。

否则可能产生：

```text
duplicate Tool Execution
```

---

# 十、Tool Correlation 查询方式

Tool Execution 当前已经支持：

```text
request_id
```

如果现有：

```text
ToolObservabilityQueryService
```

支持：

```text
by_request_id()
```

直接复用。

如果没有：

**不要修改 Tool Observability Repository/API 来扩展查询。**

可以先检查当前 API：

```text
records()
by_request_id()
by_project()
by_tool()
metrics()
```

如果已有：

```text
by_request_id(A)
```

直接使用。

如果没有，则：

### 本阶段允许

给现有：

```text
ToolObservabilityQueryService
```

增加一个**只读的 request_id 查询方法**。

但是：

```text
只允许扩展 Service
```

如果需要新增 Repository 查询：

**先停止并报告，不要自行扩展 DB 层。**

原因：

Step 38 的重点是组合已有 Trace Read Boundary，而不是再次扩大 Persistence Scope。

---

# 十一、不存在记录的语义

对于：

```text
assistant_request_id = A
```

可能出现：

### Case A

```text
LLM = 1
Tool = 0
```

例如：

```text
RAG
```

合法。

---

### Case B

```text
LLM = 1
Tool = 1
```

例如：

```text
TOOL
```

合法。

---

### Case C

```text
LLM = 0
Tool = 0
```

也必须允许。

例如：

```text
LLM usage disabled
Tool not executed
```

不能把：

```text
[]
[]
```

解释为错误。

---

# 十二、不能根据 route 猜测

`AssistantTraceQueryService`：

**不能接受 route 参数。**

也不能：

```text
if route == RAG:
    ...
elif route == TOOL:
    ...
```

因为 Trace Read Model 应该根据：

```text
assistant_request_id
```

读取事实。

而不是重新执行 Orchestrator 的业务判断。

---

# 十三、不能执行任何业务逻辑

禁止：

```text
重新 Router
重新调用 LLM
重新执行 Tool
重新执行 SQL
重新查询 RAG
```

Trace Query：

```text
Read only
```

---

# 十四、异常语义

LLM Usage Query Service 已有：

```text
RepositoryError
InputError
```

Tool Query Service 也有自己的错误。

本阶段：

**不要设计新的复杂错误体系。**

如果一个下游 Query Service 失败：

直接向上传递当前已有异常。

不要：

```text
return []
```

掩盖数据库错误。

例如：

```text
DB unavailable
        ↓
AssistantTraceQueryService
        ↓
raise existing error
```

而不是：

```text
DB unavailable
        ↓
[]
```

否则会把：

```text
系统错误
```

误认为：

```text
没有 Trace
```

---

# 十五、Input Validation

Assistant Trace Service 自己至少应该验证：

```text
assistant_request_id
```

保持 Step 37 语义：

```text
str
strip 非空
<= 128
```

不要重新生成 UUID。

不要 truncate。

不要 normalize。

如果现有 `assistant_trace.py` 已经有公共 validation helper：

直接复用。

---

# 十六、Security

Trace View 只能包含：

### LLM

```text
id
assistant_request_id
provider_request_id
provider
model
prompt_tokens
completion_tokens
total_tokens
created_at
```

### Tool

当前：

```text
ToolExecutionSnapshot
```

的 11 个安全字段。

禁止加入：

```text
prompt
messages
raw_response
tool arguments
tool result data
SQL
database connection
Session
API key
password
authorization
database URL
```

---

# 十七、Projection 必须显式

不要：

```python
vars(record)
```

不要：

```python
record.__dict__
```

不要：

```python
asdict(record)
```

不要：

```python
model_dump()
```

如果项目当前安全边界明确禁止隐式投影：

继续保持。

---

# 十八、测试要求

至少：

## Test 1：LLM only

```text
A
LLM = 2
Tool = 0
```

返回：

```text
assistant_request_id = A
llm_usage = 2
tool_executions = 0
```

---

## Test 2：Tool only

```text
A
LLM = 0
Tool = 1
```

合法。

---

## Test 3：LLM + Tool

```text
A
LLM = 2
Tool = 1
```

必须：

```text
所有记录都属于 A
```

---

## Test 4：Empty

```text
A
LLM = 0
Tool = 0
```

返回空集合，不是异常。

---

## Test 5：不存在

```text
not-exist
```

返回：

```text
AssistantTraceView(
    assistant_request_id="not-exist",
    llm_usage=(),
    tool_executions=(),
)
```

---

## Test 6：不串 Trace

```text
A
LLM(A)
Tool(A)

B
LLM(B)
Tool(B)
```

查询 A：

不能出现：

```text
B
```

---

## Test 7：LLM ordering

输入：

```text
P2
P1
P3
```

最终保持：

```text
created_at ASC
id ASC
```

---

## Test 8：Tool ordering

保持 Tool Query Service 当前定义的稳定排序。

不要在 Trace Service 二次排序。

---

## Test 9：Input validation

覆盖：

```text
None
""
"   "
>128
```

必须在下游 Query Service 之前失败。

---

## Test 10：下游异常透传

模拟：

```text
LLM Query failure
```

验证：

```text
AssistantTraceQueryService
```

不会返回：

```text
[]
```

而是透传已有异常。

Tool Query failure 同理。

---

## Test 11：Security

静态/字段测试确认：

```text
Trace View
```

没有：

```text
prompt
messages
sql
tool_args
tool_result
session
connection
secret
```

---

## Test 12：Architecture

新增：

```text
C43
```

至少验证：

```text
C43.1 只依赖 Query Service
C43.2 不依赖 Repository
C43.3 不依赖 SQLAlchemy
C43.4 不访问 Session
C43.5 不生成 request_id
C43.6 不执行 Tool
C43.7 不执行 LLM
C43.8 不新增 HTTP endpoint
C43.9 immutable result
C43.10 不暴露 secrets
```

---

# 十九、DB Tests

如果 Tool 使用：

```text
persistent DB
```

才增加 DB-gated test。

如果当前 Tool Query 使用 Runtime Memory：

则：

```text
LLM Usage → DB
Tool → Memory
```

可以使用 Fake/Fixture 分别测试。

不要为了 Step 38 强行把 Runtime Tool Query 改成 DB。

---

# 二十、不要创建 Trace Repository

明确：

```text
NO AssistantTraceRepository
```

不要新增：

```text
assistant_trace_record
```

不要新增：

```text
assistant_trace table
```

不要新增 migration。

Trace 是：

```text
Query Composition
```

不是新的 persistence model。

---

# 二十一、不要新增 HTTP API

本阶段：

```text
NO NEW ENDPOINT
```

不要：

```text
GET /api/trace
GET /api/assistant/trace
GET /api/usage/by-request
```

Step 37 已经明确：

```text
HTTP Read API = future
```

Step 38 不提前做。

---

# 二十二、文档

新增：

```text
docs/evaluation/Phase 3.12 Step 38 — Assistant Trace Read Model.md
```

必须记录：

```text
Scope
Read Model
Data Sources
Dependency Boundary
Empty Semantics
Ordering
Security
Error Propagation
Tests
Limitations
```

Architecture 增加：

```text
AssistantTraceQueryService
       │
       ├── LLMUsageQueryService
       └── ToolObservabilityQueryService
```

明确：

```text
No Repository
No ORM
No SQL
No HTTP
No persistence
```

---

# 二十三、验证

执行：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests
```

LSP：

```text
0 error
0 warning
```

lint：

如果未安装：

```text
unavailable
```

---

# 二十四、最终必须证明

完成后形成：

```text
Assistant Trace A
        │
        ├── LLMUsageQueryService
        │       ↓
        │   LLM Usage[]
        │
        └── ToolObservabilityQueryService
                ↓
            Tool Execution[]
                │
                ↓
        AssistantTraceView
```

并明确：

```text
AssistantTraceQueryService
```

只是：

```text
Read Model Composition
```

不是：

```text
Trace System
```

---

# 二十五、最终汇报格式

完成后只汇报：

```text
Phase 3.12 Step 38 COMPLETE

1. Trace Read Model
2. 修改/新增文件
3. AssistantTraceView
4. LLM Data Source
5. Tool Data Source
6. Dependency Boundary
7. Empty Semantics
8. Ordering
9. Security
10. Error Propagation
11. Tests
12. DB Tests
13. compile / LSP / lint
14. DB residue
15. API 是否变化
16. 当前限制
```

最后：

```text
Assistant Trace A
        │
        ├── LLM Usage[]
        │
        └── Tool Execution[]
                ↓
        AssistantTraceView
```

**完成 Step 38 后立即 STOP。**

不要进入 Step 39。
不要开发 Trace API。
不要开发 Conversation。
不要开发 Memory。
不要开发 Agent。
不要开发 MCP。
不要开发 Streaming。
不要开发 Dashboard。
