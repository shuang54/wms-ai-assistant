你现在开始执行：

# Phase 3.11 Step 25 — Tool Observability Read-only HTTP API

## 一、阶段目标

基于已经完成的：

```text
Step 20 Collector Retention
Step 21 Query Service
Step 22 Snapshot Read Model
Step 23 Architecture Audit
Step 24 Serialization Boundary
```

新增一个**最小只读 HTTP API**。

目标：

```text
HTTP GET
   ↓
API Layer
   ↓
ToolObservabilityQueryService
   ↓
Snapshot / Metrics
   ↓
Serialization
   ↓
JSON
```

本阶段第一次把 Tool Observability 暴露给 HTTP 客户端。

但是：

**只做 Read API。**

---

# 二、严格范围

本阶段只允许：

```text
新增 Tool Observability Read API
新增 API Response DTO（如果现有架构需要）
新增 API 测试
更新 API 文档
更新 architecture / evaluation 文档
```

禁止：

```text
Database Persistence
Redis
Kafka
Prometheus
OpenTelemetry
Dashboard
Frontend
WebSocket
SSE
Authentication redesign
Authorization redesign
Audit persistence
Event Bus
Background worker
Celery
Agent
MCP
LangGraph
Memory
Planning
```

---

# 三、开始编码前必须阅读

先阅读：

```text
backend/app/main.py
backend/app/api/
backend/app/api/orchestrator_chat.py
backend/app/api/tool_chat.py
backend/app/api/usage.py
```

以及：

```text
backend/app/services/tool_observability_query_service.py
backend/app/services/tool_observability_snapshot.py
backend/app/services/tool_observability_serialization.py
backend/app/services/tool_execution_metrics_service.py
```

测试：

```text
tests/test_tool_observability_query_service.py
tests/test_tool_observability_snapshot.py
tests/test_tool_observability_serialization.py
tests/test_api_*.py
```

重点确认现有 API 风格：

```text
Router
Request DTO
Response DTO
create_app()
/api prefix
错误处理
HTTP status convention
TestClient fixture
```

**必须复用现有 API 模式。**

不要重新设计 API framework。

---

# 四、第一版只提供两个 GET Endpoint

新增：

```text
GET /api/observability/tools
```

以及：

```text
GET /api/observability/tools/metrics
```

不要增加其他 Endpoint。

---

# 五、Endpoint 1：Tool Execution Records

```text
GET /api/observability/tools
```

返回：

```json
{
  "records": [
    {
      "request_id": "...",
      "round": 1,
      "tool_name": "get_inventory",
      "started_at": "2026-09-26T10:20:30.123456+00:00",
      "finished_at": "2026-09-26T10:20:30.125000+00:00",
      "duration_ms": 1.574,
      "success": true,
      "project_id": "project-a",
      "tool_call_id": null,
      "error_code": null,
      "error_type": null
    }
  ]
}
```

注意：

最终字段必须严格来自：

```text
ToolExecutionSnapshot
```

不能直接返回：

```text
ToolExecutionRecord
```

不能直接返回：

```text
Collector
```

不能返回内部 Service 对象。

---

# 六、Endpoint 2：Metrics

```text
GET /api/observability/tools/metrics
```

返回：

```json
{
  "total_count": 10,
  "success_count": 8,
  "failure_count": 2,
  "success_rate": 0.8,
  "failure_rate": 0.2,
  "total_duration_ms": 123.45,
  "average_duration_ms": 12.345,
  "max_duration_ms": 40.0
}
```

空数据必须保持当前 Metrics 语义：

```json
{
  "total_count": 0,
  "success_count": 0,
  "failure_count": 0,
  "success_rate": null,
  "failure_rate": null,
  "total_duration_ms": 0.0,
  "average_duration_ms": null,
  "max_duration_ms": null
}
```

绝对不要把：

```text
None
```

转换成：

```text
0
```

---

# 七、API 不允许直接访问 Collector

这是本阶段最重要的 Architecture Boundary。

API：

```text
可以
  ↓
ToolObservabilityQueryService
```

API：

```text
禁止
  ↓
InMemoryToolExecutionCollector
```

API：

```text
禁止
  ↓
ToolExecutionRecord
ToolExecutionService
ToolRegistry
Tool Handler
AIOrchestrator
```

也就是说：

```text
API
 ↓
QueryService
 ↓
Read Model
```

必须保持。

---

# 八、Composition Root

需要决定：

```text
ToolObservabilityQueryService
```

在哪里创建。

优先检查当前：

```text
backend/app/api/orchestrator_chat.py
```

已有：

```text
_TOOL_EXECUTION_COLLECTOR
_TOOL_EXECUTION_OBSERVER
```

不要重新创建 Collector。

**整个应用必须继续共享 Step 20 的同一个 Collector。**

禁止：

```python
InMemoryToolExecutionCollector()
```

在 observability API module 中再次创建。

正确方向：

```text
Application Composition Root
        │
        ├── Collector
        ├── Observer
        ├── Orchestrator
        └── QueryService
```

如果现有 Composition Root 不适合直接暴露 QueryService：

可以新增一个极小的 Composition Root accessor / assembly。

但是：

**不要重新创建 Collector。**

---

# 九、推荐 API Response DTO

检查项目现有 API DTO 风格。

如果现有项目要求 Pydantic Response Model：

可以新增：

```text
ToolObservabilityRecordsResponse
ToolObservabilityMetricsResponse
```

例如：

```python
class ToolObservabilityRecordsResponse(BaseModel):
    records: list[dict[str, Any]]
```

以及：

```python
class ToolObservabilityMetricsResponse(BaseModel):
    total_count: int
    success_count: int
    failure_count: int
    success_rate: float | None
    failure_rate: float | None
    total_duration_ms: float
    average_duration_ms: float | None
    max_duration_ms: float | None
```

但：

**优先使用项目已有 DTO convention。**

不要为了本阶段创建复杂 Response Model hierarchy。

FastAPI 的 `response_model` 可以同时帮助校验、文档生成和限制返回字段，因此如果项目已有 Pydantic API DTO 模式，优先沿用。

---

# 十、Serialization Boundary

API 必须使用 Step 24：

```text
snapshot_to_dict()
metrics_to_dict()
```

不要自己重新实现：

```text
datetime.isoformat()
```

不要复制 Snapshot 字段映射。

不要在 API 中：

```python
record.__dict__
vars(record)
asdict(record)
```

正确：

```text
QueryService
 ↓
Snapshot
 ↓
snapshot_to_dict()
 ↓
Response
```

Metrics：

```text
QueryService
 ↓
MetricsSnapshot
 ↓
metrics_to_dict()
 ↓
Response
```

---

# 十一、API 不增加业务逻辑

API 只做：

```text
HTTP request
 ↓
QueryService
 ↓
Serialization
 ↓
HTTP response
```

不要在 API 中计算：

```text
success_rate
failure_rate
average_duration
max_duration
```

不要排序。

不要过滤。

不要分页。

不要聚合。

这些全部保持现有 Query / Metrics 语义。

---

# 十二、当前版本不要加 Query Parameters

第一版：

```text
GET /api/observability/tools
```

不要：

```text
?project_id=
?tool_name=
?request_id=
?limit=
?offset=
?from=
?to=
?sort=
```

原因：

Step 25 只验证：

```text
HTTP Read Boundary
```

不是设计 Query DSL。

---

# 十三、Error Handling

如果 QueryService 当前没有可能抛出的业务异常：

保持简单。

如果发现：

```text
unexpected internal error
```

必须遵循项目当前 API error convention。

不要新增：

```text
ObservabilityException hierarchy
```

不要返回：

```text
traceback
exception message
internal module path
database information
```

---

# 十四、Security

HTTP Response 不得包含：

```text
API key
password
authorization
token
DSN
database_url
connection
session
engine
SQL
prompt
LLM response
Tool arguments
Tool result data
Tool handler
Tool registry
Collector internals
```

特别注意：

```text
ToolExecutionSnapshot
```

只有 11 个字段。

Metrics：

```text
不得包含 request_id
project_id
tool_name
```

保持 Step 24 的 security contract。

---

# 十五、测试

新增：

```text
tests/test_tool_observability_api.py
```

至少覆盖：

## Records API

1. GET 200
2. empty collector → `records=[]`
3. one successful Tool record
4. one failed Tool record
5. multiple records
6. retention respected
7. evicted record not returned
8. datetime JSON string
9. None fields remain null
10. response field whitelist
11. API does not expose Collector
12. API does not expose Record internals

## Metrics API

13. GET 200
14. empty metrics
15. success/failure metrics
16. None semantics
17. response field whitelist
18. metrics excludes identifiers

## Architecture

19. API imports QueryService
20. API does not import Collector
21. API does not import ToolExecutionService
22. API does not import ToolRegistry
23. API does not import Tool Handler
24. API uses serialization boundary
25. API does not calculate metrics

## Composition

26. API uses same application Collector
27. Tool execution creates Record
28. GET records sees newly executed Tool record
29. GET metrics reflects Tool execution
30. RAG execution does not create Tool record

## Security

31. no SQL
32. no secrets
33. no traceback
34. no Tool arguments
35. no Tool result data

目标：

```text
30~40 tests
```

不要为了数量重复测试。

---

# 十六、API Integration Test

至少做一个真正的：

```text
TestClient
 ↓
POST /api/...
 ↓
Tool execution
 ↓
GET /api/observability/tools
```

验证：

```text
Tool 执行
    ↓
Collector
    ↓
QueryService
    ↓
HTTP GET
    ↓
JSON
```

而不是直接构造 Collector 后测试 API。

---

# 十七、不要使用真实数据库

本阶段默认：

```text
RUN_DB_TESTS 不需要
```

API tests 使用：

```text
Fake / Stub Tool
```

或者项目已有安全 Tool test fixture。

如果必须使用真实 Tool：

只复用已有 DB-gated fixture。

不要新建数据库表。

不要 INSERT 新测试数据。

---

# 十八、不要调用真实 LLM

默认：

```text
DeepSeek = 0
SiliconFlow = 0
```

使用现有 Fake / Stub。

不要新增 real LLM test。

---

# 十九、Architecture Contract

增加：

```text
C27 — Tool Observability HTTP Read Boundary
```

建议：

```text
C27.1 GET records is read-only
C27.2 GET metrics is read-only
C27.3 API → QueryService only
C27.4 API does not → Collector
C27.5 API does not → ToolExecutionService
C27.6 API does not → ToolRegistry
C27.7 API does not → Tool Handler
C27.8 API uses Snapshot Read Model
C27.9 API uses Serialization Boundary
C27.10 API does not calculate Metrics
C27.11 API does not create Collector
C27.12 API uses application Collector
C27.13 no Tool execution from GET
C27.14 no DB
C27.15 no LLM
C27.16 no persistence
C27.17 response has no secrets
C27.18 response has no Tool args/results
C27.19 metrics has no identifiers
C27.20 no query DSL
```

继续复用：

```text
tests/test_tool_chat_architecture_contract.py
```

不要创建第三个 Architecture Contract 文件。

---

# 二十、Architecture 文档

更新：

```text
docs/architecture.md
```

新增：

```text
§8.43 Tool Observability HTTP Read Boundary
```

架构：

```text
Tool Execution
      ↓
ToolExecutionRecord
      ↓
Observer
      ↓
Collector
      ↓
QueryService
      ↓
Snapshot / Metrics
      ↓
Serialization
      ↓
FastAPI Read API
      ↓
JSON
```

明确禁止：

```text
FastAPI
   X
   ↓
Collector
```

以及：

```text
FastAPI
   X
   ↓
ToolExecutionService
```

---

# 二十一、API 文档

更新：

```text
docs/api.md
```

新增：

```text
GET /api/observability/tools
GET /api/observability/tools/metrics
```

记录：

### `/api/observability/tools`

```text
Method: GET
Auth: 当前项目现有 API 机制
Side Effect: None
Persistence: None
```

Response 示例。

### `/api/observability/tools/metrics`

同样记录。

特别说明：

```text
数据来源：
Application-lifetime InMemory Collector

Retention：
当前 Collector max_records

Persistence：
None

Multi-process：
每个 process 独立内存数据
```

不要在本阶段增加认证机制。

---

# 二十二、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.11 Step 25 — Tool Observability HTTP Read Boundary.md
```

记录：

1. Scope
2. Endpoints
3. Architecture
4. QueryService Boundary
5. Serialization
6. Security
7. Tests
8. Limitations

明确：

```text
Persistence = NOT IMPLEMENTED
Dashboard = NOT IMPLEMENTED
Prometheus = NOT IMPLEMENTED
OpenTelemetry = NOT IMPLEMENTED
Redis/Kafka = NOT IMPLEMENTED
```

---

# 二十三、测试命令

先：

```powershell
python -m pytest -q tests/test_tool_observability_api.py
```

然后：

```powershell
python -m pytest -q tests/test_tool_observability_api.py tests/test_tool_observability_serialization.py tests/test_tool_observability_query_service.py tests/test_tool_observability_snapshot.py
```

然后：

```powershell
python -m pytest -q
```

如果需要 DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

不要使用：

```text
RUN_DB_TESTS=1 pytest
```

---

# 二十四、编译 / LSP / lint

执行：

```powershell
python -m compileall -q backend tests
```

要求：

```text
0 errors
```

检查新增 API 文件：

```text
LSP = 0 errors
LSP = 0 warnings
```

如果项目已有 lint：

使用现有 lint。

不要安装新的 lint 工具。

---

# 二十五、DB / Network / LLM

最终报告必须明确：

```text
DB writes = 0
Network = 0
Real LLM = 0
```

如果 DB-gated 测试因为已有：

```text
knowledge fixture/data-state coupling
```

导致无法全量完成：

不要修改它。

记录：

```text
Pre-existing DB fixture/data-state coupling
```

---

# 二十六、失败处理

如果发现 API 层必须修改：

```text
main.py
create_app()
existing API conventions
```

才能接入：

允许做**最小必要修改**。

但是：

不要修改：

```text
AIOrchestrator
ToolExecutionService
Collector
QueryService
Snapshot
Metrics
```

只是为了让 API 测试通过。

如果发现已有 API architecture 不适合接入：

**先停止并报告，不要重构 API 架构。**

---

# 二十七、最终汇报格式

完成后严格：

```text
Phase 3.11 Step 25 COMPLETE

1. 新增文件
2. 修改文件
3. Endpoints
4. Records API
5. Metrics API
6. QueryService Boundary
7. Serialization Boundary
8. C27 Contract
9. API Tests
10. Full Tests
11. compile / LSP / lint
12. DB writes
13. Network / LLM
14. Production Code Changes
15. 当前限制
```

最后输出：

```text
Architecture:

Tool Execution
      ↓
ToolExecutionRecord
      ↓
Observer
      ↓
Collector
      ↓
QueryService
      ↓
Snapshot / Metrics
      ↓
Serialization
      ↓
FastAPI
      ↓
JSON
```

以及：

```text
Forbidden:

FastAPI
   X → Collector
   X → ToolExecutionService
   X → ToolRegistry
   X → Tool Handler
```

最后：

**立即停止。**

不要进入 Step 26。

不要开发 Database Persistence。

不要开发 Dashboard。

不要接 Redis / Kafka / Prometheus / OpenTelemetry。

不要开发 Agent / MCP / LangGraph / Memory / Planning。
