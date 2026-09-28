你现在开始执行 **Phase 3.12 Step 39 — Assistant Trace HTTP Read API**。

## 一、阶段目标

基于已经完成的：

```text
Step 37
LLMUsageQueryService
        ↓
LLM Trace Read Boundary
```

以及：

```text
Step 38
AssistantTraceQueryService
        ↓
AssistantTraceView
```

现在只增加一个：

**只读 HTTP API**

目标：

```text
HTTP GET
   ↓
Assistant Trace API
   ↓
AssistantTraceQueryService
   ↓
┌─────────────────────┐
│ LLM Usage[]         │
│ Tool Execution[]    │
└─────────────────────┘
   ↓
AssistantTraceView
   ↓
JSON
```

本阶段不要增加新的 Trace 数据存储。

不要增加新的 Repository。

不要增加新的数据库表。

不要修改 Trace Read Model。

---

# 二、开始前必须先阅读

先阅读真实代码：

```text
backend/app/services/assistant_trace_query_service.py
backend/app/services/llm_usage_query_service.py
backend/app/services/tool_observability_query_service.py

backend/app/api/
backend/app/main.py

tests/test_assistant_trace_query_service.py
tests/test_assistant_trace_query_service_db.py
```

同时检查现有 HTTP 风格：

```text
/api/usage/analytics
/api/observability/tools
/api/observability/tools/history
/api/observability/tools/metrics
/api/observability/tools/metrics/persistent
/api/ai/chat
```

确认：

1. Router 如何注册。
2. API DTO 如何定义。
3. Service DTO → API DTO 如何映射。
4. HTTP 错误如何处理。
5. 现有 API 的测试写法。
6. `create_app()` 如何注册 router。
7. 是否已有适合的 observability router。

**不要假设接口。**

---

# 三、严格范围

## 允许

允许修改：

```text
backend/app/api/
backend/app/main.py
```

允许新增：

```text
tests/test_assistant_trace_api.py
docs/evaluation/Phase 3.12 Step 39 — Assistant Trace HTTP Read API.md
```

如果现有 API router 已经适合承载该 endpoint：

**优先复用，不要新增无意义 router。**

---

# 四、明确禁止

本阶段禁止修改：

```text
AIOrchestrator
AI Router
RAG
Tool Execution
Tool Observability Core
LLM Provider
LLM Usage Repository
LLM Usage Query Service
AssistantTraceQueryService
Text-to-SQL
SQL Validator
SQL Executor
```

禁止新增：

```text
Trace Repository
Trace Table
Trace ORM
Trace Migration
Trace Collector
Span
OpenTelemetry
Conversation
Memory
Agent
MCP
Streaming
Dashboard
WebSocket
```

不要重新实现 Trace 查询逻辑。

HTTP 层必须调用：

```text
AssistantTraceQueryService.get_trace()
```

不能直接调用：

```text
Repository
SQLAlchemy
Collector
ToolExecutionService
LLM Provider
```

---

# 五、Endpoint 设计

新增：

```http
GET /api/observability/assistant-trace/{assistant_request_id}
```

如果当前项目已有更统一的 trace URL 规范，可以根据真实代码采用最自然的等价路径。

但不要同时创建多个 endpoint。

推荐：

```text
GET /api/observability/assistant-trace/{assistant_request_id}
```

例如：

```http
GET /api/observability/assistant-trace/req-123
```

---

# 六、Request 参数

只允许：

```text
assistant_request_id
```

不增加：

```text
route
provider
model
tool_name
from
to
limit
offset
page
sort
```

Step 38 已经定义了 Trace Read Model。

本阶段不要增加过滤/分页/排序能力。

---

# 七、Input Validation

HTTP 层可以使用 FastAPI Path 参数的基础约束。

但真正的业务校验仍然由：

```text
AssistantTraceQueryService
```

负责。

必须保持 Step 38 的规则：

```text
None
""
"   "
非 str
bytes
list
> 128
```

最终不能进入下游 Query Service。

对于正常 HTTP 请求：

```text
长度 1~128
```

正常调用：

```text
AssistantTraceQueryService.get_trace()
```

不要在 API 层复制完整 validation 逻辑。

---

# 八、Response DTO

不要直接把：

```text
AssistantTraceView
```

作为 FastAPI response model，除非当前项目已有明确支持这种 Service DTO 直接暴露的模式。

优先新增 API DTO：

```text
AssistantTraceResponse
```

以及：

```text
LLMUsageTraceResponse
ToolExecutionTraceResponse
```

具体字段必须严格根据现有 DTO：

### LLM

复用 Step 37 的安全 9 字段：

```text
id
assistant_request_id
request_id
provider
model
prompt_tokens
completion_tokens
total_tokens
created_at
```

### Tool

复用 Step 22 的安全 11 字段。

**必须读取真实 `ToolExecutionSnapshot` 定义后再写字段。**

不要凭记忆重新设计。

---

# 九、Response 安全边界

HTTP response 只能包含：

```text
assistant_request_id
llm_usage[]
tool_executions[]
```

LLM 中只允许 Step 37 的 9 字段。

Tool 中只允许 Step 22 的 11 字段。

禁止出现：

```text
prompt
messages
raw_response
arguments
result.data
SQL
database connection
Session
API key
password
authorization
database URL
secret
```

也禁止暴露内部 Python DTO 的任意字段。

不要使用：

```python
vars(...)
asdict(...)
__dict__
model_dump()
```

来隐式生成 response。

必须显式字段映射。

---

# 十、FastAPI Response Model

使用项目当前 Pydantic 版本和 DTO 风格。

建议：

```python
class LLMUsageTraceResponse(BaseModel):
    ...

class ToolExecutionTraceResponse(BaseModel):
    ...

class AssistantTraceResponse(BaseModel):
    assistant_request_id: str
    llm_usage: list[LLMUsageTraceResponse]
    tool_executions: list[ToolExecutionTraceResponse]
```

Endpoint：

```python
@router.get(
    "/observability/assistant-trace/{assistant_request_id}",
    response_model=AssistantTraceResponse,
)
```

如果项目 router 已经带：

```text
/api
```

则不要重复写 `/api`。

以真实 `main.py` 注册方式为准。

使用 response model 作为 HTTP 数据过滤边界；FastAPI 会根据 response model 对返回数据进行验证、序列化和字段过滤。

---

# 十一、Service 调用

API 层只负责：

```text
HTTP request
 ↓
Path parameter
 ↓
AssistantTraceQueryService.get_trace()
 ↓
API DTO mapping
 ↓
HTTP response
```

不要在 API 层做：

```text
排序
去重
聚合
过滤
数据库查询
Collector 查询
业务判断
Trace 合并
```

---

# 十二、Dependency / Wiring

Step 38 当前：

```text
AssistantTraceQueryService
    ├── LLMUsageQueryService
    └── ToolObservabilityQueryService
```

其中 Tool Query Service 当前是：

```text
runtime memory collector
```

并且 Step 38 明确：

```text
Tool read boundary 必须显式注入
```

因此检查当前项目 API 装配方式。

不要创建第二个 Collector。

不要让 HTTP API 创建新的 Collector。

必须确保：

```text
HTTP API
    ↓
应用级 AssistantTraceQueryService
    ↓
现有 ToolObservabilityQueryService
    ↓
现有 Runtime Collector
```

如果当前模块化 singleton / router 装配方式无法安全获得同一个 Tool Query Service：

**先停止并报告。**

不要为了让 API 测试通过而重新创建 Collector。

---

# 十三、Empty Trace Semantics

严格保持 Step 38：

如果：

```text
assistant_request_id = "not-exist"
```

返回：

```http
200 OK
```

body：

```json
{
  "assistant_request_id": "not-exist",
  "llm_usage": [],
  "tool_executions": []
}
```

不要返回：

```text
404
```

因为：

```text
不存在记录
```

在当前 Trace Read Model 中表示：

```text
empty trace
```

而不是 HTTP resource-not-found。

---

# 十四、Ordering

API 层不得重新排序。

必须保持：

### LLM

Step 37：

```text
created_at ASC
id ASC
```

### Tool

Step 38：

```text
Collector 写入顺序
```

HTTP DTO 映射只能：

```text
tuple → list
```

不能改变顺序。

---

# 十五、Error Handling

不要吞异常。

例如：

```text
LLMUsageRepositoryError
```

必须继续按照当前项目已有 API 错误规范处理。

不要：

```python
except Exception:
    return empty_trace
```

绝对禁止。

否则：

```text
DB failure
```

会被错误解释成：

```text
No Trace
```

如果项目当前没有专门的 `AssistantTrace` HTTP exception：

复用现有 API error convention。

不要新增复杂异常体系。

---

# 十六、API 测试

新增：

```text
tests/test_assistant_trace_api.py
```

至少覆盖：

## Test 1：LLM only

```text
LLM = 2
Tool = 0
```

返回：

```text
200
```

并验证字段。

---

## Test 2：Tool only

```text
LLM = 0
Tool = 1
```

返回：

```text
200
```

---

## Test 3：LLM + Tool

```text
LLM = 2
Tool = 1
```

返回：

```text
200
```

---

## Test 4：Empty

```text
not-exist
```

验证：

```text
200
llm_usage == []
tool_executions == []
```

---

## Test 5：Input validation

测试：

```text
empty
whitespace
>128
```

验证：

```text
不调用下游
```

并按照项目现有 HTTP validation semantics 断言状态码。

不要强行规定新的 status code。

---

## Test 6：Error propagation

模拟：

```text
AssistantTraceQueryService
```

抛出已有下游异常。

验证：

API 不把它转换成：

```text
200 empty
```

---

## Test 7：Security

响应中不得出现：

```text
prompt
messages
arguments
raw_response
secret
password
api_key
database_url
sql
```

---

## Test 8：Explicit mapping

使用一个带有额外字段的 Fake DTO。

确认：

额外字段不会出现在 HTTP response。

---

## Test 9：Existing API Regression

至少验证：

```text
/api/ai/chat
/api/usage/analytics
/api/observability/tools
/api/observability/tools/history
/api/observability/tools/metrics
/api/observability/tools/metrics/persistent
```

没有因为新增 endpoint 被破坏。

不要重复整个项目 E2E。

---

# 十七、OpenAPI

验证：

```text
GET /api/observability/assistant-trace/{assistant_request_id}
```

出现在：

```text
/openapi.json
```

并且 response schema 正确。

必须没有：

```text
password
api_key
arguments
raw_response
sql
```

等字段。

---

# 十八、DB 测试

本阶段不需要新增新的数据库查询。

可以复用 Step 38 DB fixture 验证：

```text
PostgreSQL LLM Usage
+
Runtime Tool Collector
        ↓
HTTP API
```

如果已有 DB-gated API 测试基础设施：

新增少量集成测试即可。

至少验证：

```text
LLM correlation
Tool correlation
cross-request isolation
empty trace
```

禁止：

```text
INSERT 真实业务数据
TRUNCATE
修改 schema
新增 migration
```

测试数据必须定向清理。

---

# 十九、HTTP 层不要新增缓存

本阶段不要：

```text
Redis
LRU
TTL
in-memory trace cache
```

每次：

```text
GET
 ↓
AssistantTraceQueryService
```

直接读取当前数据源。

---

# 二十、不要新增 Pagination

单个：

```text
assistant_request_id
```

天然是一个 trace scope。

本阶段不增加：

```text
page
page_size
limit
offset
cursor
```

如果未来 Tool Runtime retention 导致数据量过大，再单独设计。

---

# 二十一、测试命令

Windows PowerShell：

```powershell
python -m pytest -q tests/test_assistant_trace_api.py
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_assistant_trace_api_db.py
```

如果项目已有统一 DB-gated API 测试文件结构，则遵循现有方式。

Compile：

```powershell
python -m compileall -q backend tests
```

如果已有 lint：

继续使用。

不要因为 lint 不存在而安装新工具。

---

# 二十二、Architecture 文档

新增：

```text
docs/evaluation/Phase 3.12 Step 39 — Assistant Trace HTTP Read API.md
```

并在：

```text
docs/architecture.md
```

增加一个小节。

记录：

```text
HTTP
 ↓
AssistantTrace API
 ↓
AssistantTraceQueryService
 ├── LLMUsageQueryService → PostgreSQL
 └── ToolObservabilityQueryService → Runtime Memory
```

明确：

```text
No Trace Table
No Trace Repository
No OpenTelemetry
No Conversation
No Memory
```

并说明：

```text
LLM 与 Tool 当前来自不同数据源。
```

---

# 二十三、Git Diff 审计

完成后检查：

```powershell
git status --short
git diff --stat
git diff
```

确认：

只能看到本阶段必要变化。

重点确认没有误修改：

```text
AIOrchestrator
AI Router
RAG
ToolExecutionService
LLM Provider
Text-to-SQL
SQL Validator
SQL Executor
```

---

# 二十四、完成标准

必须满足：

```text
GET /api/observability/assistant-trace/{assistant_request_id}
        ↓
AssistantTraceQueryService
        ↓
AssistantTraceView
        ↓
Safe API DTO
        ↓
JSON
```

并且：

```text
LLM data
    ← PostgreSQL

Tool data
    ← Runtime Memory
```

保持 Step 38 的数据源设计。

---

# 二十五、最终报告

完成后严格按照：

```text
Phase 3.12 Step 39 COMPLETE

1. Endpoint
2. API DTO
3. Service Wiring
4. Empty Trace
5. Error Handling
6. Security
7. OpenAPI
8. Tests
9. DB Tests
10. compile / lint
11. Existing API Regression
12. Git Diff
13. 当前限制
```

最后明确：

```text
Step 39 完成。

HTTP Trace Read API 已建立。

没有新增：
- Trace DB
- Trace Repository
- OpenTelemetry
- Conversation
- Memory
- Agent
- MCP
- Streaming
- Dashboard

立即 STOP。
不要进入 Step 40。
```
