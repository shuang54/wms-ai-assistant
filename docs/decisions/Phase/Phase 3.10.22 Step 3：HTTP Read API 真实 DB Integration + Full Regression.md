你现在继续实现项目：

`D:\coding\ai\wms-ai-assistant`

# Phase 3.10.22 Step 3：HTTP Read API 真实 DB Integration + Full Regression

## 一、目标

Phase 3.10.22 Step 2 已经完成：

```text
GET /api/usage/analytics
        ↓
Usage Analytics Router
        ↓
LLMUsageQueryFilter
        ↓
LLMUsageAnalyticsReadFacade
        ↓
LLMUsageQueryRuntime
```

本 Step 不新增功能。

唯一目标：

**使用真实 PostgreSQL 验证完整 HTTP → DB → Analytics → JSON 链路，并完成全项目回归。**

最终验证：

```text
HTTP GET
   ↓
FastAPI
   ↓
Usage Analytics API
   ↓
LLMUsageQueryFilter
   ↓
LLMUsageAnalyticsReadFacade
   ↓
LLMUsageQueryRuntime
   ↓
LLMUsageQueryService
   ↓
LLMUsageRepository
   ↓
PostgreSQL
   ↓
LLMUsageRecordView
   ↓
Usage Analytics
   ↓
HTTP JSON
```

---

# 二、严格禁止

本 Step：

**不修改业务代码。**

禁止：

```text
修改 usage.py
修改 main.py
修改 Facade
修改 Analytics
修改 Aggregation
修改 Query Runtime
修改 Query Service
修改 Repository
修改 DB Model
修改数据库结构
```

禁止：

```text
Dashboard
Frontend
Billing
Cost
Authentication
RBAC
Cache
Queue
Worker
Outbox
```

如果真实 DB Integration 暴露出代码 bug：

**先停止并报告。**

不要为了让测试通过而修改核心代码。

---

# 三、首先确认当前状态

阅读：

```text
backend/app/api/usage.py
backend/app/main.py
tests/test_usage_analytics_api.py
```

确认 Step 2 实际实现。

不要假设当前代码与报告完全一致。

---

# 四、运行 API 单元测试

先执行：

```bash
python -m pytest -q tests/test_usage_analytics_api.py
```

要求：

```text
29 passed
0 failed
```

如果数字发生变化，以当前实际测试数量为准。

如果失败：

**停止。**

不要继续 DB Integration。

---

# 五、运行 Usage Chain Regression

执行：

```bash
python -m pytest -q `
  tests/test_usage_analytics_api.py `
  tests/test_llm_usage_analytics_facade.py `
  tests/test_llm_usage_analytics.py `
  tests/test_llm_usage_aggregation.py `
  tests/test_llm_usage_query.py `
  tests/test_llm_usage_query_runtime.py
```

PowerShell 如果换行有问题，可以直接执行一行：

```bash
python -m pytest -q tests/test_usage_analytics_api.py tests/test_llm_usage_analytics_facade.py tests/test_llm_usage_analytics.py tests/test_llm_usage_aggregation.py tests/test_llm_usage_query.py tests/test_llm_usage_query_runtime.py
```

要求：

```text
0 failed
```

---

# 六、开启 DB Integration

按照项目当前方式：

```powershell
$env:RUN_DB_TESTS="1"
```

然后：

```powershell
python -m pytest -q
```

不要直接：

```text
RUN_DB_TESTS=1 pytest
```

因为当前环境是 Windows PowerShell。

---

# 七、真实 HTTP API DB 测试

如果当前项目已经存在 DB Integration fixture：

**优先复用。**

不要重新创建：

```text
PostgreSQL fixture
Session fixture
Engine fixture
```

---

# 八、增加最小 API DB Integration 测试

如果现有测试结构允许，可以增加一个最小的真实 DB API 测试。

建议：

```text
tests/test_usage_analytics_api.py
```

增加：

```text
@pytest.mark.db
```

或者项目已有的等价 DB marker。

如果项目没有 marker：

不要为了这个测试新增复杂测试框架。

---

# 九、真实 DB API Test Case

目标：

```text
GET /api/usage/analytics
```

使用真实：

```text
Query Runtime
Query Service
Repository
PostgreSQL
```

禁止 Fake Facade。

---

# 十、DB Test 数据

首先检查：

```text
ai_ops.llm_usage_record
```

是否已有可读取测试数据。

如果已有：

**直接查询。**

不要插入新数据。

如果没有数据：

不要为了 API 测试直接向数据库写生产数据。

优先复用项目已有 DB fixture / seed / transaction rollback 机制。

---

# 十一、API DB 正常查询

调用：

```text
GET /api/usage/analytics
```

验证：

```text
HTTP 200
```

并验证 JSON 至少包含：

```text
total
by_provider
by_model
by_provider_model
```

具体字段以真实 DTO 为准。

---

# 十二、验证 HTTP Filter → DB

至少验证：

```text
limit
offset
provider
model
```

例如：

```text
GET /api/usage/analytics?limit=10&offset=0
```

确认最终 Query Runtime 收到正确 Filter。

然后：

```text
GET /api/usage/analytics?provider=deepseek
```

确认真实数据库查询受到 provider filter 影响。

如果数据库当前没有 deepseek 数据：

不要人为插入数据。

可以只验证请求成功 / 返回空结果，具体以当前测试数据决定。

---

# 十三、验证时间过滤

如果 DB 测试数据存在时间范围：

测试：

```text
created_at_from
created_at_to
```

例如：

```text
GET /api/usage/analytics?created_at_from=...&created_at_to=...
```

验证：

```text
HTTP
 ↓
datetime
 ↓
LLMUsageQueryFilter
 ↓
Query Service
 ↓
PostgreSQL
```

时间参数必须保持 timezone-aware。

不要新增 HTTP 层时间处理逻辑。

---

# 十四、验证 NULL Group

如果 DB 中存在：

```text
provider IS NULL
model IS NULL
```

验证：

```json
null
```

仍然保留。

不要：

```text
unknown
N/A
""
```

如果当前 DB 没有 NULL 数据：

**不要为了测试修改数据库。**

已有 Fake API test 已经覆盖 NULL 语义即可。

---

# 十五、验证 Pagination

执行：

```text
GET /api/usage/analytics?limit=1&offset=0
```

验证：

```text
total.total_requests <= 1
```

并确认：

```text
by_provider
by_model
by_provider_model
```

均来自当前 page records。

不能出现：

```text
global_total
total_count
```

---

# 十六、验证 DB Read Only

这是本 Step 最重要的安全检查之一。

在真实 DB Integration 前后检查：

```text
ai_ops.llm_usage_record
```

行数。

记录：

```text
count_before
count_after
```

必须：

```text
count_before == count_after
```

最终报告：

```text
DB writes = 0
```

---

# 十七、检查 SQL 行为

不要修改 SQL。

只检查真实执行链是否：

```text
SELECT
```

没有：

```text
INSERT
UPDATE
DELETE
DROP
ALTER
TRUNCATE
```

如果项目已有 SQL logging：

可以读取日志确认。

不要为了日志修改核心代码。

---

# 十八、DB 未配置行为

测试当前环境下：

```text
DB 未配置
```

是否仍符合 Step 2 Contract：

```text
LLMUsageRepositoryError
    ↓
HTTP 502
```

如果当前测试环境已经配置 DB：

不要破坏环境配置。

只检查已有错误处理测试是否仍然通过。

---

# 十九、API 与旧 API 回归

执行：

```bash
python -m pytest -q `
  tests/test_health.py `
  tests/test_chat_api.py `
  tests/test_rag_api.py `
  tests/test_tool_chat_api.py `
  tests/test_usage_analytics_api.py
```

如果真实文件名不同：

按照项目实际文件名执行。

要求：

```text
0 failed
```

---

# 二十、完整 Usage 链路

执行：

```bash
python -m pytest -q `
  tests/test_llm_usage_persistence.py `
  tests/test_llm_usage_persistence_idempotency.py `
  tests/test_llm_usage_query.py `
  tests/test_llm_usage_query_runtime.py `
  tests/test_llm_usage_aggregation.py `
  tests/test_llm_usage_analytics.py `
  tests/test_llm_usage_analytics_facade.py `
  tests/test_usage_analytics_api.py
```

如果文件名不同，以实际项目为准。

---

# 二十一、全量回归

先关闭 DB 环境变量：

```powershell
Remove-Item Env:RUN_DB_TESTS -ErrorAction SilentlyContinue
```

然后：

```powershell
python -m pytest -q
```

要求：

```text
0 failed
```

---

# 二十二、再次执行 DB 全量回归

重新：

```powershell
$env:RUN_DB_TESTS="1"
```

然后：

```powershell
python -m pytest -q
```

要求：

```text
0 failed
```

记录：

```text
Passed
Skipped
Failed
```

不要编造数字。

---

# 二十三、Compileall

执行：

```bash
python -m compileall backend tests
```

要求：

```text
0 errors
```

---

# 二十四、LSP / Diagnostics

检查：

```text
backend/app/api/usage.py
backend/app/main.py
tests/test_usage_analytics_api.py
```

要求：

```text
0 diagnostics
```

如果出现环境级 warning：

区分：

```text
warning
diagnostic
error
```

不要把 warning 伪装成 error。

---

# 二十五、Git Diff

执行：

```bash
git status --short
```

以及：

```bash
git --no-pager diff --stat
```

重点确认：

允许：

```text
backend/app/api/usage.py
backend/app/main.py
tests/test_usage_analytics_api.py
docs/architecture.md
docs/evaluation/...
```

不应该出现：

```text
services/llm_usage_analytics_service.py
services/llm_usage_aggregation_service.py
services/llm_usage_query_runtime.py
services/llm_usage_query_service.py
db/llm_usage_repository.py
```

等核心模块被意外修改。

---

# 二十六、最终安全边界

确认最终：

```text
HTTP API
    ↓
Read Facade
    ↓
Query Runtime
    ↓
Read Repository
    ↓
PostgreSQL
```

API 本身：

```text
SQL = 0
DB Write = 0
LLM Call = 0
Network = 0
```

DB Integration 只允许：

```text
SELECT
```

---

# 二十七、最终 Architecture

确认最终架构：

```text
LLM Request
    ↓
Observation
    ↓
Accounting
    ↓
Persistence
    ↓
Idempotency
    ↓
PostgreSQL
    ↓
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
Usage Analytics Read Facade
    ↓
HTTP Read API
    ↓
GET /api/usage/analytics
    ↓
Future Dashboard / Admin / AI Ops
```

注意：

```text
Future Dashboard / Admin / AI Ops
```

只是消费者位置。

本 Step 不实现。

---

# 二十八、文档

如果真实 DB Integration 与 Step 1 / Step 2 Contract 完全一致：

可以在：

```text
docs/evaluation/Phase 3.10.22 — HTTP Read API Contract.md
```

增加一个非常小的：

```text
## Integration Verification
```

记录：

```text
HTTP API = PASS
Real PostgreSQL = PASS
Read-only = PASS
DB writes = 0
```

不要重新设计 Contract。

---

# 二十九、最终报告

严格使用：

```text
【Phase 3.10.22 Step 3 COMPLETE】

1. HTTP API Integration
2. Real PostgreSQL
3. Query Parameters
4. Query Filter
5. Facade
6. Analytics
7. Response
8. Pagination
9. NULL Semantics
10. Read-only Verification
11. DB writes
12. API Regression
13. Usage Chain Regression
14. Full Test
15. DB Integration Test
16. compileall
17. LSP / Diagnostics
18. Network
19. Git Diff
20. 发现并修复的问题
21. 当前限制
```

然后给出：

```text
Architecture:

LLM Request
    ↓
Observation
    ↓
Accounting
    ↓
Persistence
    ↓
PostgreSQL
    ↓
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
Usage Analytics Read Facade
    ↓
GET /api/usage/analytics
    ↓
HTTP JSON
```

最终状态：

```text
HTTP Read API = IMPLEMENTED
PostgreSQL Read Integration = VERIFIED
DB writes = 0
Dashboard = NOT IMPLEMENTED
Frontend = NOT IMPLEMENTED
Billing = NOT IMPLEMENTED
Authentication = NOT CHANGED
Cache = NOT IMPLEMENTED
Queue/Worker = NOT IMPLEMENTED
Outbox = NOT IMPLEMENTED
```

## 最重要

**Step 3 完成后立即停止。**

不要进入 Phase 3.10.23。

不要开发 Dashboard。

不要开发 Frontend。

不要开发 Billing。

不要开发 Authentication。

不要继续扩展 Usage API。

等待下一步指令。
