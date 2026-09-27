你现在开始实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.10.22：LLM Usage Analytics HTTP Read API

## Step 1：现状勘察 + API Contract 设计

### 一、阶段目标

Phase 3.10.21 已经完成：

```text
Query Repository
    ↓
Query Service
    ↓
Query Runtime
    ↓
Usage Aggregation
    ↓
Usage Analytics
    ↓
LLMUsageAnalyticsReadFacade
```

本阶段后续目标是增加一个：

**只读 HTTP Application API**

最终形成：

```text
HTTP Request
    ↓
FastAPI API
    ↓
LLMUsageAnalyticsReadFacade
    ↓
Query Runtime
    ↓
Usage Analytics
    ↓
PostgreSQL
```

但本 Step：

**只做现状勘察、依赖确认、API Contract 设计。**

暂时不要修改 Python 代码。

---

# 二、严格禁止

本 Step 禁止：

```text
修改 Facade
修改 Analytics
修改 Aggregation
修改 Query Runtime
修改 Query Service
修改 Repository
修改数据库
修改 SQL
增加 Dashboard
增加 Frontend
增加 Billing
增加 Cache
增加 Queue
增加 Worker
增加 Outbox
增加认证系统
增加 RBAC
增加复杂权限系统
```

不要为了 HTTP API 修改已有核心 Service。

---

# 三、首先阅读真实项目

必须先阅读：

```text
backend/app/
backend/app/api/
backend/app/services/
backend/app/db/
tests/
```

重点检查：

```text
backend/app/api/
backend/app/main.py
backend/app/app.py
backend/app/__init__.py
```

如果不存在这些文件，按照项目真实结构寻找 FastAPI Application 入口。

同时检查：

```text
pyproject.toml
requirements.txt
requirements-dev.txt
```

确认：

1. 是否使用 FastAPI
2. FastAPI Application 在哪里创建
3. Router 如何注册
4. 现有 API 的命名规范
5. 现有 response model / DTO 规范
6. 现有 query parameter 处理方式
7. 现有异常处理方式
8. 现有 HTTP status code 规范
9. 现有 API 测试方式
10. 是否已有 TestClient / httpx fixture
11. 是否已有 API dependency injection
12. 是否已有 authentication middleware
13. 是否已有 `/health`、`/api/...` 等路径规范

---

# 四、重点检查现有 API

搜索：

```text
APIRouter
FastAPI
include_router
Depends
HTTPException
response_model
TestClient
AsyncClient
```

不要假设项目一定使用某种结构。

记录真实结果：

```text
FastAPI Application：
Router：
Router Registration：
API Prefix：
API Test Infrastructure：
Dependency Injection：
Exception Handling：
Authentication：
```

---

# 五、检查 Facade 真实接口

阅读：

```text
backend/app/services/llm_usage_analytics_facade.py
```

确认：

```python
LLMUsageAnalyticsReadFacade
```

真实公开方法：

```text
query_snapshot()
query_summary()
query_by_provider()
query_by_model()
query_by_provider_model()
```

确认每个方法真实参数和返回 DTO。

不要重新定义业务逻辑。

HTTP 层未来只能：

```text
HTTP Params
    ↓
LLMUsageQueryFilter
    ↓
Facade
```

不能：

```text
HTTP API
    ↓
Repository
```

---

# 六、检查 Query Filter

阅读真实：

```text
LLMUsageQueryFilter
```

确认当前支持的过滤字段：

```text
provider
model
request_id
created_at
limit
offset
```

以上只是示例。

**必须以真实代码为准。**

记录：

```text
字段：
类型：
是否 Optional：
默认值：
验证规则：
limit 最大值：
offset 规则：
```

不要在 HTTP 层复制第二套 Filter 逻辑。

---

# 七、检查 Analytics Snapshot DTO

阅读：

```text
LLMUsageAnalyticsSnapshot
LLMUsageAggregate
ProviderUsageAggregate
ModelUsageAggregate
ProviderModelUsageAggregate
```

确认真实字段。

重点：

```text
Snapshot
├── total
├── by_provider
├── by_model
└── by_provider_model
```

不要修改这些 DTO。

---

# 八、确定 HTTP API Contract

根据真实代码设计最小只读 API。

优先考虑：

```text
GET /api/usage/analytics
```

由一个 API 返回：

```text
LLMUsageAnalyticsSnapshot
```

也就是说：

```text
GET /api/usage/analytics
        ↓
query_snapshot()
```

这是本阶段优先方案。

暂时不要创建：

```text
/api/usage/providers
/api/usage/models
/api/usage/summary
/api/usage/dashboard
```

除非现有项目 API 设计明确要求拆分。

---

# 九、HTTP Query Parameters

不要自行发明参数。

根据真实：

```text
LLMUsageQueryFilter
```

决定 HTTP 参数映射。

例如最终可能类似：

```text
GET /api/usage/analytics
    ?provider=deepseek
    &model=deepseek-chat
    &limit=50
    &offset=0
```

但：

**具体字段必须根据真实 Filter 决定。**

记录：

```text
HTTP Parameter
        ↓
LLMUsageQueryFilter Field
```

例如：

```text
provider → provider
model    → model
limit    → limit
offset   → offset
```

如果某字段不应该暴露给 HTTP：

明确记录原因。

---

# 十、Pagination 语义必须保持不变

Phase 3.10.21 已经明确：

```text
Analytics = 当前 Filter 命中的记录集合
```

不是：

```text
全库 Analytics
```

因此 HTTP API 不允许偷偷增加：

```text
global_total
total_count
database_count
COUNT(*)
```

除非真实现有 Contract 已经存在。

必须保持：

```text
HTTP limit / offset
        ↓
LLMUsageQueryFilter
        ↓
Query Runtime
        ↓
当前 page records
        ↓
Analytics Snapshot
```

---

# 十一、错误映射设计

检查现有：

```text
LLMUsageQueryError
LLMUsageAnalyticsError
LLMUsageAnalyticsInputError
```

以及真实异常体系。

本 Step 只设计：

```text
Domain/Application Error
        ↓
HTTP Status
```

例如：

```text
Invalid Query
    → 4xx

Internal Application Error
    → 5xx
```

但不要擅自决定最终 status code。

先根据项目已有 API exception handling 规范确定。

尤其禁止：

```python
except Exception:
    return ...
```

不能吞掉异常。

---

# 十二、Dependency Injection 设计

确认 HTTP API 应该如何获得：

```text
LLMUsageAnalyticsReadFacade
```

优先使用项目已有 dependency injection 机制。

禁止在每个 HTTP 请求里：

```python
LLMUsageAnalyticsReadFacade(...)
```

然后自己重新拼：

```text
Repository
Query Service
Runtime
Analytics
```

HTTP 层应该依赖：

```text
Facade
```

而不是数据库。

---

# 十三、Security Boundary

设计完成后必须确认：

API 层：

```text
NO SQL
NO SQLAlchemy Session
NO Engine
NO Connection
NO Repository
NO DB write
NO LLM call
NO API key
NO password
NO database URL
```

HTTP API 只是：

```text
Request
 ↓
Filter
 ↓
Facade
 ↓
Response
```

---

# 十四、Response DTO

检查项目是否已经有：

```text
Pydantic
dataclass
response model
```

如果已经有统一 API DTO 规范：

**必须复用。**

不要因为 HTTP API 而修改：

```text
LLMUsageAnalyticsSnapshot
```

也不要把核心 frozen DTO 改成 HTTP DTO。

如果确实需要：

```text
Domain DTO
    ↓
HTTP Response DTO
```

只记录设计方案。

本 Step 不实现。

---

# 十五、API Test Strategy

检查现有测试基础设施：

```text
TestClient
AsyncClient
httpx
pytest fixtures
FastAPI dependency override
```

设计后续测试至少包括：

```text
1. GET /api/usage/analytics 正常返回
2. Query 参数正确进入 Filter
3. Facade 被调用一次
4. 返回 Snapshot
5. Invalid Filter
6. Facade Error
7. 空数据
8. NULL provider/model
9. Pagination
10. API 不直接访问 DB
```

但是：

**本 Step 不写这些测试。**

只确认已有测试机制。

---

# 十六、API Contract 文档

本 Step 可以创建：

```text
docs/evaluation/Phase 3.10.22 — HTTP Read API Contract.md
```

只记录设计，不记录不存在的实现结果。

文档至少包含：

```text
1. Scope
2. Existing API Infrastructure
3. Target Endpoint
4. HTTP Method
5. Query Parameters
6. Filter Mapping
7. Response Contract
8. Error Mapping
9. Dependency Boundary
10. Security Boundary
11. Pagination Semantics
12. Test Strategy
13. Explicit Non-Goals
```

---

# 十七、Architecture 文档

只有在确认现有架构后，再在：

```text
docs/architecture.md
```

增加：

```text
§8.22 Usage Analytics HTTP Read API
```

内容只记录：

```text
HTTP API
    ↓
Read Facade
    ↓
Query Runtime
    ↓
Analytics
```

不要写 Dashboard / Billing 等尚未实现的能力。

---

# 十八、测试要求

本 Step 原则：

**Python implementation = 0 changes**

允许：

```text
docs/architecture.md
docs/evaluation/Phase 3.10.22 — HTTP Read API Contract.md
```

如果发现现有 API 基础设施存在问题：

不要修改。

记录：

```text
发现问题：
影响：
建议：
```

---

# 十九、最终报告

完成后严格输出：

```text
【Phase 3.10.22 Step 1 COMPLETE】

1. Existing FastAPI Infrastructure
2. Application Entry
3. Router Registration
4. Existing API Conventions
5. Facade Interface
6. Query Filter
7. Analytics Snapshot
8. Proposed Endpoint
9. Query Parameter Mapping
10. Response Contract
11. Error Mapping
12. Dependency Injection
13. Security Boundary
14. Pagination Semantics
15. Test Infrastructure
16. 修改文件
17. 未修改 Python 文件
18. 发现的问题
19. 当前限制
```

最后给出：

```text
Target Architecture:

HTTP GET
   ↓
Usage Analytics API
   ↓
LLMUsageAnalyticsReadFacade
   ↓
LLMUsageQueryRuntime
   ↓
LLMUsageAnalyticsService
   ↓
PostgreSQL
```

并明确：

```text
Implementation = NOT STARTED
Dashboard      = NOT IMPLEMENTED
Billing        = NOT IMPLEMENTED
Frontend       = NOT IMPLEMENTED
Authentication = NOT CHANGED
```

### 最重要

**本 Step 完成后立即停止。**

不要开始写 API Python 代码。

不要进入 Step 2。

等待我检查 Step 1 的结果后，再决定下一步。
