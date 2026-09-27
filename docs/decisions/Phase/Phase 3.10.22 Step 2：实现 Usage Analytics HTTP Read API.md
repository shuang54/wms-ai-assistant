你现在继续实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.10.22 Step 2：实现 Usage Analytics HTTP Read API

## 一、目标

基于 Step 1 已确认的真实项目结构，实现：

```text
GET /api/usage/analytics
```

完整链路：

```text
HTTP GET
    ↓
FastAPI Router
    ↓
Query Parameters
    ↓
LLMUsageQueryFilter
    ↓
LLMUsageAnalyticsReadFacade
    ↓
LLMUsageQueryRuntime
    ↓
LLMUsageAnalyticsService
    ↓
PostgreSQL
```

本 Step 同时增加 API 集成测试。

---

# 二、严格范围

## 允许

修改：

```text
backend/app/api/
backend/app/main.py
tests/
```

新增必要 API DTO / mapper。

可以新增：

```text
backend/app/api/usage.py
```

或者按照项目真实 API 目录规范选择合适文件名。

## 禁止

不要修改：

```text
LLMUsageAnalyticsReadFacade
LLMUsageAnalyticsService
LLMUsageAggregationService
LLMUsageQueryRuntime
LLMUsageQueryService
LLMUsageRepository
LLMUsageRecord
```

不要修改：

```text
Observation
Accounting
Persistence
Idempotency
Router
RAG
Tool
Text-to-SQL
SQLValidator
SQLExecutor
```

禁止：

```text
Dashboard
Frontend
Billing
Cost
Cache
Queue
Worker
Outbox
Authentication
RBAC
```

本 Step 不增加任何数据库结构。

---

# 三、先确认 Step 1 结果

开始编码前再次读取真实：

```text
backend/app/services/llm_usage_analytics_facade.py
backend/app/services/llm_usage_query_runtime.py
backend/app/services/llm_usage_query_service.py
```

以及：

```text
backend/app/main.py
backend/app/api/
```

不要复制 Step 1 的假设。

---

# 四、创建 API Router

按照项目现有 API Router 风格创建 Usage Analytics Router。

目标：

```text
GET /api/usage/analytics
```

由于 `main.py` 已经统一：

```python
app.include_router(router, prefix="/api", ...)
```

必须遵循现有注册方式。

注意：

如果 Router 本身已经设置 `/usage` prefix：

```text
router prefix = "/usage"
```

则 endpoint 应该是：

```text
@router.get("/analytics")
```

最终：

```text
/api/usage/analytics
```

不要造成：

```text
/api/api/usage/analytics
```

或者：

```text
/api/usage/usage/analytics
```

---

# 五、Query Parameters

必须支持 Step 1 确认的 7 个参数：

```text
request_id
provider
model
created_at_from
created_at_to
limit
offset
```

最终：

```text
GET /api/usage/analytics
    ?provider=deepseek
    &model=deepseek-chat
    &limit=50
    &offset=0
```

但必须保持：

**HTTP 层不复制 Domain Filter 验证逻辑。**

---

# 六、Query Parameter DTO

项目已经使用：

```text
FastAPI
Pydantic 2.x
```

可以使用项目当前规范。

如果没有现成更适合的 Query DTO，可以创建类似：

```python
class UsageAnalyticsQueryParams(BaseModel):
    ...
```

然后：

```text
HTTP Query
    ↓
Pydantic Query DTO
    ↓
LLMUsageQueryFilter
```

如果使用 FastAPI 0.115+ 的 Query Parameter Model：

```python
Annotated[UsageAnalyticsQueryParams, Query()]
```

必须先确认与项目现有风格一致。

FastAPI 0.115+ 原生支持 Pydantic Query Parameter Model，并会自动从 query string 提取并验证字段。

---

# 七、重要：不要复制 Filter Contract

HTTP DTO 可以做 HTTP 层的类型转换。

但是不要重新实现：

```text
limit 1~100
offset >= 0
空白 provider 拒绝
空白 model 拒绝
created_at 必须 tz-aware
created_at_from <= created_at_to
```

这些规则仍然属于：

```text
LLMUsageQueryFilter
```

HTTP 层负责：

```text
HTTP string
    ↓
datetime
    ↓
LLMUsageQueryFilter
```

Domain Filter 负责最终业务校验。

如果构造：

```python
LLMUsageQueryFilter(...)
```

抛出：

```text
LLMUsageQueryInputError
```

按照 Step 1 确认的异常映射转换成：

```text
HTTP 422
```

不要复制第二套业务校验。

---

# 八、时间参数

支持：

```text
created_at_from
created_at_to
```

必须保持 timezone-aware 语义。

例如：

```text
2026-09-01T00:00:00+07:00
```

如果 FastAPI/Pydantic 能够先解析成 datetime：

```text
str
 ↓
datetime
 ↓
LLMUsageQueryFilter
```

即可。

不要自己写复杂 datetime parser。

如果输入：

```text
2026-09-01T00:00:00
```

最终必须由现有 Filter Contract 拒绝。

---

# 九、构造 LLMUsageQueryFilter

API 层完成 Query DTO 到 Domain Filter 的映射：

```text
UsageAnalyticsQueryParams
        ↓
LLMUsageQueryFilter
```

要求：

字段一一对应：

```text
request_id
provider
model
created_at_from
created_at_to
limit
offset
```

不要遗漏字段。

不要增加额外字段。

---

# 十、调用 Facade

API 的核心逻辑应该尽可能简单：

```python
query_filter = LLMUsageQueryFilter(...)
snapshot = await facade.query_snapshot(query_filter)
return _to_usage_analytics_response(snapshot)
```

不要在 API 层做：

```text
Aggregation
Grouping
SUM
COUNT
Provider grouping
Model grouping
Pagination calculation
```

这些全部已经属于 Application / Analytics 层。

---

# 十一、API Response DTO

按照现有项目：

```text
Service DTO = frozen dataclass
API DTO = Pydantic BaseModel
```

创建 HTTP Response DTO。

结构镜像：

```text
LLMUsageAnalyticsSnapshot
├── total
├── by_provider
├── by_model
└── by_provider_model
```

例如概念结构：

```text
UsageAnalyticsResponse
├── total: UsageAggregateResponse
├── by_provider: list[ProviderUsageAggregateResponse]
├── by_model: list[ModelUsageAggregateResponse]
└── by_provider_model: list[ProviderModelUsageAggregateResponse]
```

具体字段：

**必须读取真实 DTO 后一一对应。**

不要凭记忆创建字段。

---

# 十二、Response Mapper

按照现有项目：

```text
_to_chat_response(...)
_to_xxx_response(...)
```

模式实现：

```text
_to_usage_analytics_response(...)
```

如果项目已有 mapper 文件组织规范，遵循项目规范。

Mapper 只负责：

```text
Service DTO
    ↓
API DTO
```

禁止在 Mapper 中做：

```text
业务计算
排序
聚合
分页
数据库操作
```

---

# 十三、NULL 语义

必须保持现有 Analytics Contract。

例如：

```text
provider = None
model = None
```

HTTP JSON：

```json
null
```

不能：

```text
"unknown"
""
"N/A"
```

也不能过滤掉 NULL group。

同样：

```text
prompt_tokens_known
completion_tokens_known
total_tokens_known
```

必须原样返回。

---

# 十四、Immutability

不要修改 Service DTO。

API 层只是创建新的 Pydantic Response DTO。

要求：

```text
Service DTO
    ↓
Response DTO
```

不能：

```text
Service DTO
    ↓
修改原对象
```

---

# 十五、Error Mapping

按照 Step 1 确认的规则：

```text
LLMUsageQueryInputError
    ↓
HTTP 422

LLMUsageRepositoryError
    ↓
HTTP 502

LLMUsageAnalyticsInputError
    ↓
HTTP 500

LLMUsageAggregationInputError
    ↓
HTTP 500
```

未知异常：

```text
原样 raise
    ↓
Starlette / FastAPI
    ↓
500
```

不要：

```python
except Exception:
    return HTTPException(...)
```

不要吞异常。

不要返回：

```json
{
  "success": false
}
```

除非项目已有统一错误响应规范。

---

# 十六、Facade Dependency

必须复用 Step 1 已确认的模块级单例风格。

目标：

```text
LLMUsageQueryService
        ↓
LLMUsageQueryRuntimeBridge
        ↓
LLMUsageAnalyticsReadFacade
```

构造一次。

不要每个 request 创建：

```text
Repository
Session
Engine
Runtime
Analytics
Facade
```

如果项目已有 dependency module：

**复用它。**

如果没有：

按照项目当前模块级实例模式实现最小依赖。

---

# 十七、main.py

把 Router 注册到：

```text
create_app()
```

遵循：

```python
app.include_router(...)
```

不要：

```text
修改 create_app 架构
```

不要创建第二个 FastAPI App。

不要改变：

```text
app = create_app()
```

现有模式。

---

# 十八、API Tests

创建：

```text
tests/test_usage_analytics_api.py
```

或者遵循项目已有测试文件命名。

必须使用真实：

```python
TestClient
```

FastAPI 官方 TestClient 可以直接测试 ASGI application，而不需要真实网络/socket 连接。

---

# 十九、测试 1：正常返回

Fake Facade 返回一个真实结构的：

```text
LLMUsageAnalyticsSnapshot
```

调用：

```text
GET /api/usage/analytics
```

验证：

```text
status_code == 200
```

以及：

```text
total
by_provider
by_model
by_provider_model
```

完整存在。

---

# 二十、测试 2：Filter 透传

发送：

```text
request_id
provider
model
created_at_from
created_at_to
limit
offset
```

Fake / Recording Facade 记录收到的 Filter。

验证：

```text
HTTP
 ↓
LLMUsageQueryFilter
```

字段完全一致。

尤其：

```text
limit
offset
datetime
```

必须正确。

---

# 二十一、测试 3：单次 Facade 调用

验证：

```text
Facade.query_snapshot()
```

只调用：

```text
1 次
```

不能：

```text
query_summary()
query_by_provider()
query_by_model()
query_by_provider_model()
```

分别调用。

HTTP endpoint 必须直接使用：

```text
query_snapshot()
```

因为 Snapshot 已经包含四个视图。

---

# 二十二、测试 4：Response Mapping

构造：

```text
total
by_provider
by_model
by_provider_model
```

验证 HTTP JSON 与 Service DTO 一致。

特别验证：

```text
None → null
```

以及：

```text
*_known = false
```

保持不变。

---

# 二十三、测试 5：空数据

Facade 返回：

```text
LLMUsageAnalyticsSnapshot.empty()
```

或者项目实际对应的零值 Snapshot。

验证：

```json
{
  "total": {
    "total_requests": 0
  },
  "by_provider": [],
  "by_model": [],
  "by_provider_model": []
}
```

具体完整字段以真实 DTO 为准。

---

# 二十四、测试 6：Invalid Filter

测试至少：

```text
limit=0
limit=101
offset=-1
```

以及：

```text
created_at_from > created_at_to
```

验证：

```text
HTTP 422
```

不要要求 HTTP 层自己实现这些规则。

---

# 二十五、测试 7：Facade Error

Fake Facade：

```python
raise RuntimeError(...)
```

验证：

按照项目现有测试模式：

```text
未知 RuntimeError
→ 500
```

同时：

```text
LLMUsageRepositoryError
→ 502
```

如果已有统一异常 handler：

按照真实 handler 行为测试。

---

# 二十六、测试 8：NULL Group

构造：

```text
ProviderUsageAggregate(provider=None, ...)
ModelUsageAggregate(model=None, ...)
ProviderModelUsageAggregate(provider=None, model=None, ...)
```

验证：

HTTP：

```json
"provider": null
```

而不是：

```json
"provider": "unknown"
```

---

# 二十七、测试 9：Pagination

请求：

```text
?limit=10&offset=20
```

验证 Facade 收到：

```text
limit == 10
offset == 20
```

不要测试：

```text
global_total
```

因为当前 Contract 没有这个概念。

---

# 二十八、测试 10：禁止 API 直接访问 DB

对 API 模块做静态检查。

确认不存在：

```text
Session
AsyncSession
Engine
Connection
create_engine
text(
SELECT
INSERT
UPDATE
DELETE
Repository
```

API 只能依赖：

```text
Facade
Filter
DTO
Mapper
```

如果项目现有 API 模块本来存在其他合法 DB 代码：

不要误删。

静态检查只针对本次新增 Usage Analytics API 模块。

---

# 二十九、测试已有 API

新增 API 后至少执行：

```bash
python -m pytest -q tests/test_usage_analytics_api.py
```

然后：

```bash
python -m pytest -q tests/test_llm_usage_analytics_facade.py tests/test_llm_usage_analytics.py tests/test_llm_usage_aggregation.py
```

确认新 API 没有破坏既有 Usage Analytics 链路。

---

# 三十、不要运行真实 DB

本 Step API 测试默认：

```text
Fake Facade
```

不需要：

```text
RUN_DB_TESTS=1
```

不要调用：

```text
PostgreSQL
DeepSeek
SiliconFlow
```

API contract 测试必须：

```text
DB writes = 0
Network = 0
```

---

# 三十一、文档暂不扩展

Step 1 已经创建：

```text
docs/evaluation/Phase 3.10.22 — HTTP Read API Contract.md
docs/architecture.md §8.22
```

本 Step 不需要再次大规模修改文档。

如果实现结果与 Contract 完全一致：

只允许做最小实现状态更新。

如果发现 Contract 与真实实现不一致：

**先停止并报告，不要擅自修改 Contract 迁就代码。**

---

# 三十二、验证

完成实现后执行：

```bash
python -m pytest -q tests/test_usage_analytics_api.py
```

然后：

```bash
python -m pytest -q tests/test_llm_usage_analytics_facade.py tests/test_llm_usage_analytics.py tests/test_llm_usage_aggregation.py tests/test_llm_usage_query.py tests/test_llm_usage_query_runtime.py
```

然后：

```bash
python -m compileall backend tests
```

并检查：

```text
LSP / Diagnostics
```

要求：

```text
0 failed
0 compile errors
0 diagnostics
```

---

# 三十三、Git Diff

执行：

```bash
git status --short
git --no-pager diff --stat
```

确认：

允许：

```text
backend/app/api/...
backend/app/main.py
tests/test_usage_analytics_api.py
```

不允许出现：

```text
services/llm_usage_analytics_service.py
services/llm_usage_aggregation_service.py
services/llm_usage_query_runtime.py
services/llm_usage_query_service.py
db/llm_usage_repository.py
```

等核心模块的非必要修改。

---

# 三十四、最终报告

严格按照：

```text
【Phase 3.10.22 Step 2 COMPLETE】

1. 新增文件
2. 修改文件
3. Endpoint
4. Query Parameters
5. Query Filter Mapping
6. Facade Integration
7. Response DTO
8. Response Mapping
9. Error Mapping
10. NULL Semantics
11. Pagination Semantics
12. API Tests
13. 测试结果
14. DB Access
15. DB writes
16. Network
17. compileall
18. LSP / Diagnostics
19. Git Diff
20. 发现并修复的问题
21. 当前限制
```

最后输出：

```text
Architecture:

GET /api/usage/analytics
        ↓
Usage Analytics Router
        ↓
LLMUsageQueryFilter
        ↓
LLMUsageAnalyticsReadFacade
        ↓
LLMUsageQueryRuntime
        ↓
LLMUsageAnalyticsService
        ↓
PostgreSQL
```

以及：

```text
Dashboard      = NOT IMPLEMENTED
Frontend       = NOT IMPLEMENTED
Billing        = NOT IMPLEMENTED
Authentication = NOT CHANGED
Cache          = NOT IMPLEMENTED
Queue/Worker   = NOT IMPLEMENTED
```

## 最重要

**Step 2 完成后立即停止。**

不要进入 Step 3。

不要做 DB Integration。

不要做 Dashboard。

不要做 Frontend。

不要做 Authentication。

不要做 Billing。

等待下一步指令。
