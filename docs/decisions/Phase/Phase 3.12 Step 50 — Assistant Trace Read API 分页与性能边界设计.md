# Phase 3.12 Step 50 — Assistant Trace Read API 分页与性能边界设计

## 一、阶段目标

在 Phase 3.12 Step 48 / 49 已完成的基础上，只分析：

> 当前 Assistant Trace Read API 在持久化数据增长后，是否需要分页、数量上限、查询边界和性能保护。

本阶段：

**只做 Design / Audit。**

不要立即实现。

当前 API：

```text
GET /api/observability/assistant-trace/{assistant_request_id}
```

当前返回：

```text
{
  assistant_request_id,
  llm_usage[],
  tool_executions[],
  rag_executions[]
}
```

Step 49 已确认：

```text
Unified Timeline = DEFER
```

因此本阶段不要重新讨论 Unified Timeline。

---

# 二、开始前必须阅读

阅读真实代码：

```text
backend/app/api/assistant_trace.py
backend/app/services/assistant_trace_query_service.py

backend/app/services/llm_usage_query_service.py
backend/app/services/tool_execution_persistent_query_service.py
backend/app/services/rag_execution_persistent_query_service.py

backend/app/db/llm_usage_repository.py
backend/app/db/tool_execution_repository.py
backend/app/db/rag_execution_repository.py
```

以及：

```text
backend/app/db/models/
docs/architecture.md
docs/evaluation/
tests/
```

重点确认：

1. 当前三个 QueryService 是否存在默认查询上限。
2. Repository 是否可能返回无限数量。
3. 当前 SQL 是否使用明确排序。
4. request_id 查询是否有数据库索引。
5. Assistant Trace Response 是否可能无限膨胀。
6. LLM Usage / Tool / RAG 三个数据源是否存在不同的增长速度。
7. 当前 API 是否存在 pagination contract。
8. 当前测试是否已经覆盖大量记录场景。

---

# 三、当前问题

现在：

```text
assistant_request_id
        ↓
LLMUsageQueryService
        ↓
ToolExecutionPersistentQueryService
        ↓
RagExecutionPersistentQueryService
        ↓
一次性组合完整 Trace
```

理论上一个 request_id 可能对应：

```text
LLM usage      N 条
Tool execution N 条
RAG execution  N 条
```

当前 API 没有显式 pagination。

需要确认：

> 当前系统是否有可能让一个 Trace Response 无限增长。

不要假设未来一定会有 Agent。

只分析当前架构 + 合理未来扩展。

---

# 四、必须回答的问题

## 1. 当前是否存在无限查询风险

检查三个 Repository：

```text
LLM
Tool
RAG
```

确认：

```text
是否 LIMIT
是否 OFFSET
是否 cursor
是否固定 max records
```

如果没有：

明确记录：

```text
Current Read Boundary:
unbounded by request_id
```

---

## 2. 当前一个 request_id 正常情况下最多会产生多少记录

根据真实代码分析：

```text
RAG
Tool
LLM
```

不要凭经验猜。

例如：

```text
RAG route:
LLM ≤ 1
RAG ≤ 1

Tool route:
Tool ≤ 1
LLM ≤ 1

Text-to-SQL:
LLM ≤ N（受当前 semantic retry 限制）
Tool = 0
RAG = 0
```

必须以真实代码为准。

---

## 3. 当前是否真的需要 pagination

不要因为“数据库 API 通常需要分页”就直接决定实现。

分析：

```text
当前最大记录规模
+
未来 Agent / Multi-step 可能性
+
Trace UI 使用方式
+
单 request_id 查询特征
```

最终给出：

```text
IMPLEMENT
```

或：

```text
DEFER
```

---

# 五、分页方案比较

只做设计比较，不实现。

至少比较：

### Option A

```text
固定 LIMIT
```

例如：

```text
max 100
```

优点：

```text
简单
兼容现有 API
```

缺点：

```text
超过限制后可能丢数据
```

---

### Option B

```text
page + page_size
```

例如：

```text
?page=1&page_size=50
```

分析：

```text
API contract
Repository
DTO
排序稳定性
```

---

### Option C

```text
cursor pagination
```

分析：

```text
created_at + id
id
```

是否能够形成稳定 cursor。

特别注意：

Tool / RAG 当前 Trace View 不暴露 DB id。

不能因为 pagination 方便就把：

```text
database id
```

重新暴露给 API。

---

# 六、重要：安全边界

继续保持 Step 48 的安全约束。

Trace API：

## 可以暴露

```text
assistant_request_id
timestamps
duration
counts
provider
model
token usage
tool name
round
success
error code/type
RAG counts
chunk_ids
document_ids
```

## 不得暴露

```text
query
question
answer
prompt
messages
raw_response
SQL
embedding
similarity
password
API key
database URL
authorization
ORM
Session
traceback
database primary key
```

Pagination implementation：

**不能为了 cursor 而重新暴露数据库主键。**

---

# 七、排序要求

必须审查当前排序。

当前：

```text
LLM:
created_at ASC, id ASC

Tool:
id ASC

RAG:
id ASC
```

分析：

### LLM

当前已经有：

```text
created_at + id
```

属于稳定排序。

### Tool / RAG

当前：

```text
id ASC
```

但是：

```text
id
```

不会暴露到 Trace DTO。

因此：

> 内部可以继续使用 DB id 作为稳定排序，但不能把 id 放进 HTTP Response。

---

# 八、Trace Response 是否需要改变

设计两种方案：

### 方案 1

保持：

```text
AssistantTraceResponse
```

不变。

内部固定：

```text
MAX_RECORDS
```

超过后：

```text
truncated = true
```

但如果新增字段：

必须考虑 backward compatibility。

---

### 方案 2

增加 pagination DTO：

```text
AssistantTraceResponse
{
    ...
    pagination: ...
}
```

或者：

```text
llm_usage
tool_executions
rag_executions
```

分别分页。

分析哪种更合理。

注意：

**本阶段不修改 DTO。**

---

# 九、不要做统一分页

不要简单设计成：

```text
page=1
```

然后把：

```text
LLM
Tool
RAG
```

三段混在一起分页。

因为 Step 49 已经明确：

```text
三段独立语义
```

而且没有统一时间线。

因此必须分析：

```text
每个 source 独立分页
```

是否比：

```text
整个 Trace 统一分页
```

更符合当前架构。

---

# 十、性能分析

分析：

```text
GET /assistant-trace/{A}
```

会触发：

```text
LLM query
Tool query
RAG query
```

需要确认：

1. 是否三次独立 SQL。
2. 是否 request_id index。
3. 是否显式 SELECT。
4. 是否可能读取过多 JSONB。
5. 是否 ORM lazy loading。
6. 是否 N+1。
7. 是否需要 projection DTO。
8. 是否需要数据库 LIMIT。

禁止：

```text
Redis
cache
Elasticsearch
OpenTelemetry
Event Bus
Kafka
```

本阶段不引入新基础设施。

---

# 十一、测试设计

本阶段不要求实现分页，因此不要为了凑数量新增大量测试。

只设计测试建议。

至少分析未来如果实施，应覆盖：

```text
empty trace
single record
multiple records
exact page boundary
over page boundary
stable ordering
cross-request isolation
invalid page
invalid page_size
max page_size
database failure
security fields
```

如果现有代码已经有相关测试：

记录：

```text
Existing coverage
```

不要重复写测试。

---

# 十二、Architecture 文档

新增：

```text
docs/evaluation/Phase 3.12 Step 50 — Assistant Trace Read Scalability Design.md
```

建议包含：

```text
1. Current State
2. Read Path
3. Record Growth Analysis
4. Current Query Bounds
5. Pagination Options
6. Sorting
7. Cursor Safety
8. Security Boundary
9. Performance
10. API Compatibility
11. Recommendation
12. Future Trigger
13. Verification
```

并在：

```text
docs/architecture.md
```

增加简短结论。

不要修改生产代码。

---

# 十三、明确禁止

本阶段禁止：

```text
❌ 修改 AssistantTraceResponse
❌ 修改 AssistantTraceQueryService
❌ 修改 Repository
❌ 修改数据库 Schema
❌ 新增 pagination API
❌ 新增 cursor API
❌ 新增 DB index
❌ 新增缓存
❌ Redis
❌ OpenTelemetry
❌ Event Bus
❌ Kafka
❌ Streaming
❌ WebSocket
❌ Agent
❌ Memory
❌ MCP
❌ Conversation
❌ Unified Timeline
```

---

# 十四、验证

执行：

```powershell
python -m compileall -q backend tests
```

检查：

```text
production code = 0 modifications
DB schema = 0
API contract = 0
runtime = 0
```

执行：

```powershell
git status --short
git diff --stat
```

确认只有：

```text
docs/
```

发生变化。

如果发现生产代码因为之前工作树已有修改：

**不要覆盖。**

只报告。

---

# 十五、最终报告

严格报告：

```text
Phase 3.12 Step 50 COMPLETE

1. Current Trace Read Path
2. LLM / Tool / RAG 当前最大记录可能性
3. 当前是否存在 unbounded query
4. 当前数据库 index
5. 当前排序
6. Pagination Option A
7. Pagination Option B
8. Cursor Option
9. Security Analysis
10. Performance Analysis
11. API Compatibility
12. Recommendation
13. Future Trigger
14. 新增文档
15. 修改文件
16. Tests
17. compileall
18. Git diff
19. Production Code Modified
20. DB Schema Modified

Recommendation 必须明确：

IMPLEMENT
或
DEFER
```

如果当前记录规模受现有 Orchestrator 单路由和 retry 上限天然约束，可以合理选择：

```text
DEFER
```

不要为了“完整 API”而提前引入 pagination。

---

# 十六、STOP

完成 Phase 3.12 Step 50 后：

**立即停止。**

不要进入 Step 51。

不要实现 Pagination。

不要进入 Conversation。

不要进入 Memory。

不要进入 Agent。

等待下一步指令。
