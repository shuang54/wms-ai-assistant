你现在开始执行：

# Phase 3.12 Step 37 — Assistant Trace Read Boundary

## 一、阶段目标

Phase 3.12 Step 36 已完成：

```text
Assistant request_id
        │
        ├── LLMUsageRecord.assistant_request_id
        │       └── provider_request_id
        │
        └── ToolExecutionRecord.request_id
```

本阶段只解决一个问题：

> **为已经存在的 `assistant_request_id` 建立 LLM Usage 的只读查询边界。**

目标不是建立完整 Trace 系统。

最终只需要能够：

```text
assistant_request_id
        ↓
LLM Usage Repository
        ↓
LLM Usage Read Service
        ↓
LLM Usage Records
```

---

# 二、严格范围

允许：

```text
LLM Usage Repository
LLM Usage Read Service / Query Service
LLM Usage Read DTO
相关 unit tests
相关 DB tests
少量 architecture / evaluation 文档
```

如果现有项目已经存在合适的：

```text
LLMUsageAnalyticsReadFacade
LLMUsageQueryService
LLMUsageRepository
```

必须优先扩展现有边界。

**不要重新创建第二套 LLM Usage Repository。**

---

# 三、禁止

本阶段禁止：

```text
Conversation
Memory
Agent
MCP
Planning
Streaming
OpenTelemetry
Prometheus
Dashboard
Kafka
Redis
Celery
Trace Span
Trace Parent/Child
```

禁止：

```text
修改 AI Router
修改 AI Orchestrator
修改 ToolExecutionRecord
修改 Tool Observability
修改 Text-to-SQL
修改 RAG
修改 LLM Provider
```

禁止：

```text
重新设计 assistant_request_id
```

Step 36 已确定：

```text
assistant_request_id = AIOrchestrator request_id
```

保持不变。

---

# 四、先阅读现有实现

先不要修改代码。

阅读：

```text
backend/app/db/llm_usage_repository.py
backend/app/db/models/llm_usage_record.py

backend/app/services/
    llm_usage*
    *llm_usage*

backend/app/api/
    usage*.py

tests/
```

重点确认：

1. 当前 LLM Usage Repository 的 read 方法。
2. 当前 `LLM_USAGE_READ_COLUMNS`。
3. 当前 `LLMUsageRecordRow`。
4. 当前 `LLMUsageAnalyticsReadFacade`。
5. 当前 `/api/usage/analytics` 如何读取数据。
6. 当前是否已经存在类似：

   ```text
   get_by_request_id()
   list_by_request_id()
   ```
7. 当前数据库索引情况。
8. 当前 DB 测试 fixture。

**先复用现有 Read Boundary。**

---

# 五、核心设计

新增一个只读方法：

```text
list_by_assistant_request_id(
    assistant_request_id: str,
)
```

职责：

```text
assistant_request_id
        ↓
SQL WHERE assistant_request_id = :assistant_request_id
        ↓
LLM Usage Rows
```

要求：

* 参数必须 bound parameter
* 不允许字符串拼接 SQL
* 不允许 Python 全表过滤
* 不允许读取 ORM Model 后直接返回
* 不允许返回 SQLAlchemy Session
* 不允许返回 ORM 对象

---

# 六、排序

必须使用确定性排序。

优先：

```sql
ORDER BY created_at ASC, id ASC
```

如果当前表的真实字段/Repository 结构不同：

使用现有可用字段，但必须保证：

```text
同一 assistant_request_id
→ 返回顺序稳定
```

不要依赖数据库默认返回顺序。

---

# 七、空值语义

必须区分：

### assistant_request_id = NULL

这些是：

```text
历史 Usage
旧 API
未绑定 Trace Scope
```

查询：

```text
assistant_request_id = "A"
```

**不能返回 NULL 数据。**

不要实现：

```text
NULL OR assistant_request_id = A
```

只返回精确匹配：

```text
assistant_request_id = A
```

---

# 八、输入校验

`assistant_request_id` 至少要求：

```text
str
非空
strip 后非空
长度符合当前 VARCHAR(128)
```

建议：

```text
"" → ValueError
"   " → ValueError
None → ValueError / TypeError
超过 128 → ValueError
```

不要自动：

```text
truncate
normalize
generate
```

尤其不能：

```text
缺少 request_id → 自动生成新的 UUID
```

---

# 九、返回 DTO

如果当前项目已有：

```text
LLMUsageRecordRow
```

优先复用。

如果现有 Row 明确禁止增加字段：

不要为了 Step 37 修改 analytics Row。

可以新增专门：

```text
LLMUsageTraceRecord
```

但必须先确认是否真的需要。

推荐最小字段：

```text
id
assistant_request_id
provider_request_id / request_id
provider
model
prompt_tokens
completion_tokens
total_tokens
created_at
```

注意：

`request_id` 当前代表：

```text
Provider Request ID
```

不要改名成：

```text
trace_id
```

---

# 十、Repository Boundary

Repository 增加：

```text
list_by_assistant_request_id()
```

只负责：

```text
DB Query
→ Row DTO
```

不负责：

```text
aggregation
business interpretation
HTTP
trace composition
tool lookup
```

SQL 必须显式选择字段。

禁止：

```text
SELECT *
```

---

# 十一、Read Service

如果当前已有：

```text
LLMUsageAnalyticsReadFacade
```

优先扩展它。

否则新增最小：

```text
LLMUsageTraceReadService
```

职责：

```text
validate input
      ↓
repository.list_by_assistant_request_id()
      ↓
return read DTO
```

不要：

```text
自动查 Tool
自动查 RAG
自动查 Conversation
自动查其他 Trace
```

---

# 十二、不要新增 HTTP API

本阶段：

```text
NO NEW ENDPOINT
```

不要新增：

```text
GET /api/usage/by-request
GET /api/trace
GET /api/assistant/trace
```

原因：

Step 37 只建立：

```text
Application Read Boundary
```

HTTP API 留到后续单独阶段。

---

# 十三、测试要求

至少覆盖：

## Test 1：正常查询

数据库：

```text
A → Usage 1
A → Usage 2
B → Usage 3
NULL → Usage 4
```

查询：

```text
A
```

只能得到：

```text
Usage 1
Usage 2
```

---

## Test 2：不存在

```text
assistant_request_id = "not-exist"
```

返回：

```text
[]
```

不是异常。

---

## Test 3：NULL 不匹配

```text
assistant_request_id = "A"
```

不得返回：

```text
assistant_request_id = NULL
```

---

## Test 4：排序

同一个 A：

```text
id=10 created_at=t2
id=11 created_at=t1
id=12 created_at=t2
```

验证最终排序：

```text
created_at ASC
id ASC
```

或当前项目实际采用的确定性排序规则。

---

## Test 5：输入校验

覆盖：

```text
None
""
"   "
>128 chars
```

全部在 DB 查询之前失败。

---

## Test 6：SQL Injection

测试输入：

```text
'A' OR '1'='1
```

以及：

```text
A'; DROP TABLE ai_ops.llm_usage_record; --
```

必须：

```text
只作为参数值
不会扩大查询范围
不会执行额外 SQL
```

---

## Test 7：Historical NULL

插入历史数据：

```text
assistant_request_id = NULL
```

查询正常：

```text
list_by_assistant_request_id("A")
```

不会因为新列导致读取异常。

---

## Test 8：多个 Provider Request

同一个 Assistant request：

```text
A
 ├── P1
 ├── P2
 └── P3
```

必须全部返回：

```text
assistant_request_id = A
```

且：

```text
provider_request_id
```

分别保持原值。

---

# 十四、真实 DB Test

使用现有：

```text
RUN_DB_TESTS=1
```

机制。

PowerShell：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

DB 测试只使用测试数据。

测试前后：

```text
DB residue = 0
```

不要：

```text
TRUNCATE
```

不要删除真实历史数据。

使用项目已有测试清理机制。

---

# 十五、Security

查询结果只能包含已有 LLM Usage 字段：

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

绝对不能返回：

```text
prompt
messages
raw_response
api_key
authorization
password
database_url
tool_args
tool_result
sql
rag_content
chunk_content
```

如果当前 Repository 已经通过：

```text
LLM_USAGE_READ_COLUMNS
```

控制字段：

继续复用。

---

# 十六、Architecture Test

新增：

```text
C42 — Assistant Trace LLM Usage Read Boundary
```

至少验证：

```text
C42.1 assistant_request_id exact match
C42.2 NULL 不参与匹配
C42.3 Repository 使用 bound parameter
C42.4 Repository 不 SELECT *
C42.5 Repository 返回 Row DTO
C42.6 Read Service 不访问 ORM / Session
C42.7 不新增 HTTP endpoint
C42.8 provider_request_id 语义保持不变
C42.9 Historical NULL 数据兼容
C42.10 不读取 prompt / messages / secrets
```

---

# 十七、文档

新增：

```text
docs/evaluation/Phase 3.12 Step 37 — Assistant Trace LLM Usage Read Boundary.md
```

记录：

```text
Scope
Current Problem
Read Boundary
Query Semantics
Null Semantics
Ordering
Security
Tests
Limitations
```

Architecture 文档只增加一个小节：

```text
Assistant Trace
    ↓
LLM Usage Read Boundary
```

不要大规模修改。

---

# 十八、不要做这些事情

本阶段明确不要：

```text
❌ 新增 /api/usage/by-request
❌ 新增 /api/trace
❌ 聚合 Tool + LLM
❌ 聚合 RAG + LLM
❌ Conversation
❌ Memory
❌ Dashboard
❌ OpenTelemetry
❌ Trace Span
❌ Prometheus
❌ Redis
❌ Kafka
❌ 修改已有 Analytics Response
```

---

# 十九、验证

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

检查：

```text
LSP = 0 error
LSP = 0 warning
```

lint 如果未安装：

```text
记录 unavailable
```

---

# 二十、最终必须证明

最终形成：

```text
Assistant Trace A
        │
        ├── LLM Usage
        │      assistant_request_id = A
        │      provider_request_id = P1
        │
        ├── LLM Usage
        │      assistant_request_id = A
        │      provider_request_id = P2
        │
        └── Tool Execution
               request_id = A
```

但是：

**Step 37 不负责把这些记录聚合成一个 Trace DTO。**

只建立：

```text
A
 ↓
LLM Usage Read Boundary
```

---

# 二十一、最终汇报格式

完成后只汇报：

```text
Phase 3.12 Step 37 COMPLETE

1. Read Boundary
2. 修改文件
3. Repository
4. Read Service
5. Query semantics
6. NULL semantics
7. Ordering
8. Security
9. Tests
10. DB Tests
11. compile / LSP / lint
12. DB residue
13. API 是否变化
14. 当前限制
```

最后：

```text
Assistant Trace A
        ↓
LLMUsageRecord.assistant_request_id = A
        ↓
list_by_assistant_request_id(A)
        ↓
LLM Usage Records
```

**完成 Step 37 后立即 STOP。**

不要进入 Step 38。
不要开发 Trace API。
不要开发 Conversation。
不要开发 Memory。
不要开发 Agent。
不要开发 MCP。
不要开发 Streaming。
不要开发 Dashboard。
