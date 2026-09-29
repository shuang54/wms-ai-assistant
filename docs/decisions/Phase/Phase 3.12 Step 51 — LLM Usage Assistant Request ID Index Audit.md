# Phase 3.12 Step 51 — LLM Usage Assistant Request ID Index Audit

## 一、阶段目标

针对 Phase 3.12 Step 50 发现的问题：

```text
ai_ops.llm_usage_record.assistant_request_id
```

当前被：

```text
LLMUsageQueryService.list_by_assistant_request_id()
```

用于 Assistant Trace 查询，但数据库目前没有该字段的专用索引。

本阶段只做：

> **索引必要性确认 + 最小索引设计。**

第一步：

**不要直接修改数据库。**

先阅读真实实现、确认字段与查询方式、确认现有索引，再决定：

```text
IMPLEMENT
```

或：

```text
DEFER
```

---

# 二、严格范围

允许检查：

```text
backend/app/db/models/llm_usage_record.py
backend/app/db/llm_usage_repository.py
backend/app/services/llm_usage_query_service.py
backend/app/services/assistant_trace_query_service.py

tests/test_llm_usage_repository.py
tests/test_llm_usage_query_service.py
tests/test_assistant_trace*
```

以及：

```text
数据库 migration / schema 文件
docs/architecture.md
docs/evaluation/
```

---

# 三、禁止

本阶段禁止：

```text
❌ 修改 AssistantTraceResponse
❌ 修改 AssistantTraceQueryService
❌ 修改 LLMUsageQueryService 查询语义
❌ 修改 Tool Trace
❌ 修改 RAG Trace
❌ 修改 API
❌ 修改 request_id 生成方式
❌ 修改 LLM Usage DTO
❌ 修改 LLM Usage Repository 查询结果
❌ 修改数据库数据
❌ 删除已有索引
❌ 新增复合索引
❌ 新增缓存
❌ Pagination
❌ Cursor
❌ Redis
❌ OpenTelemetry
❌ Event Bus
```

本阶段只允许：

```text
设计/验证一个 assistant_request_id 索引是否有必要。
```

---

# 四、先阅读真实查询

重点确认：

```python
list_by_assistant_request_id(assistant_request_id)
```

实际 SQL / SQLAlchemy 查询是否类似：

```sql
WHERE assistant_request_id = :assistant_request_id
ORDER BY created_at ASC, id ASC
```

必须确认真实代码。

不要根据历史报告猜测。

---

# 五、确认字段定义

确认：

```text
assistant_request_id
```

的：

```text
类型
长度
nullable
是否唯一
是否 foreign key
```

尤其确认：

```text
assistant_request_id 可以 NULL
```

因为当前 ToolChat 等历史路径可能没有该字段。

不要把它改成：

```text
NOT NULL
```

不要增加 UNIQUE。

---

# 六、确认现有索引

读取真实数据库模型 / migration，确认：

```text
ix_llm_usage_record_created_at
uq_llm_usage_record_request_id
```

以及其它所有索引。

明确：

```text
uq_llm_usage_record_request_id
```

对应的是：

```text
provider request_id
```

还是：

```text
assistant_request_id
```

不能仅根据索引名称判断。

---

# 七、查询模式分析

当前 Trace：

```text
GET /api/observability/assistant-trace/{A}
```

会执行：

```text
LLMUsageQueryService
    ↓
WHERE assistant_request_id = A
ORDER BY created_at ASC, id ASC
```

分析：

### 查询条件

```text
assistant_request_id = ?
```

### 排序

```text
created_at ASC
id ASC
```

因此考虑：

```text
INDEX (assistant_request_id)
```

和：

```text
INDEX (assistant_request_id, created_at, id)
```

两个方案。

---

# 八、索引方案比较

## Option A

```sql
CREATE INDEX ix_llm_usage_record_assistant_request_id
ON ai_ops.llm_usage_record (assistant_request_id);
```

优点：

```text
最小
符合当前过滤条件
写放大较低
简单
```

缺点：

```text
查询仍可能需要额外排序
```

---

## Option B

```sql
CREATE INDEX ix_llm_usage_record_assistant_request_id_created_at_id
ON ai_ops.llm_usage_record (
    assistant_request_id,
    created_at,
    id
);
```

优点：

```text
过滤 + 排序一起支持
```

缺点：

```text
索引更大
写入成本更高
当前每个 assistant_request_id 只有少量记录
收益可能很小
```

---

# 九、必须结合真实数据规模判断

检查当前数据库：

```text
llm_usage_record 总行数
```

以及：

```text
assistant_request_id IS NOT NULL
```

的数量。

如果允许读取 DB：

只读执行统计 SQL。

例如：

```sql
SELECT COUNT(*)
FROM ai_ops.llm_usage_record;
```

以及：

```sql
SELECT COUNT(*)
FROM ai_ops.llm_usage_record
WHERE assistant_request_id IS NOT NULL;
```

不要修改任何数据。

如果当前环境没有 DB：

明确报告：

```text
DB unavailable
```

不要猜数量。

---

# 十、EXPLAIN 分析

如果 DB 测试环境可用，可以针对真实查询做：

```sql
EXPLAIN
SELECT ...
FROM ai_ops.llm_usage_record
WHERE assistant_request_id = :request_id
ORDER BY created_at ASC, id ASC;
```

重点观察：

```text
Seq Scan
Index Scan
Sort
```

不要使用：

```text
EXPLAIN ANALYZE
```

如果不需要实际执行。

优先：

```text
EXPLAIN
```

---

# 十一、注意：不要因为出现 Seq Scan 就立即认为有问题

PostgreSQL 查询优化器可能在小表上选择：

```text
Seq Scan
```

即使已经存在索引。

这是正常的。

因此不要简单写：

```text
Seq Scan = failure
```

必须结合：

```text
table size
estimated rows
query selectivity
```

判断。

---

# 十二、是否需要复合索引

重点判断：

当前单个：

```text
assistant_request_id
```

通常只对应：

```text
1～4 条 LLM usage
```

如果真实情况确实如此：

优先：

```text
Option A
```

而不是：

```text
Option B
```

不要为了理论上的 ORDER BY 优化增加复合索引。

---

# 十三、Migration 设计

如果最终推荐：

```text
IMPLEMENT
```

只设计 migration。

不要执行 migration。

目标名称建议：

```text
ix_llm_usage_record_assistant_request_id
```

字段：

```text
assistant_request_id
```

不要：

```text
UNIQUE
```

因为一个 Assistant Request 允许：

```text
多个 LLM usage records
```

---

# 十四、NULL 行为

确认：

```text
assistant_request_id IS NULL
```

的历史记录不受影响。

索引应该允许：

```text
NULL
```

不修改历史数据。

不做 backfill。

---

# 十五、安全边界

确认新增索引不会改变：

```text
API response
DTO
Trace semantics
request_id
```

索引只影响：

```text
query execution plan
```

不应该改变业务结果。

---

# 十六、测试设计

如果最终实施：

建议只增加：

```text
repository/query regression
```

不需要大量测试。

至少确认：

```text
assistant_request_id exact match
cross-request isolation
NULL assistant_request_id
stable ordering
empty result
```

不要为了一个索引新增几十个测试。

---

# 十七、性能验证标准

如果实施：

验证：

```text
查询条件仍然完全相同
返回结果完全相同
排序完全相同
```

索引前后：

```text
row count = same
result = same
```

不要求：

```text
必须 Index Scan
```

因为 PostgreSQL 优化器会根据成本选择执行计划。

---

# 十八、最终文档

新增：

```text
docs/evaluation/Phase 3.12 Step 51 — LLM Usage Request ID Index Audit.md
```

内容：

```text
1. Current Query
2. Field Definition
3. Existing Indexes
4. Data Volume
5. EXPLAIN
6. Option A
7. Option B
8. Recommendation
9. Migration Design
10. Test Design
11. Security / Compatibility
12. Verification
```

并在：

```text
docs/architecture.md
```

增加一小段：

```text
LLM Usage Assistant Request ID Index
```

只记录最终结论。

---

# 十九、最终报告

严格报告：

```text
Phase 3.12 Step 51 COMPLETE

1. Current Query
2. assistant_request_id 字段定义
3. Existing Indexes
4. Data Volume
5. EXPLAIN Result
6. Option A
7. Option B
8. Recommendation
9. Migration Design
10. Security
11. API Compatibility
12. Tests
13. compileall
14. Git diff
15. Production Code Modified
16. DB Schema Modified
```

Recommendation 必须明确：

```text
IMPLEMENT
```

或：

```text
DEFER
```

如果推荐 IMPLEMENT：

**本阶段仍然只完成设计，不执行 migration。**

---

# 二十、STOP

完成 Phase 3.12 Step 51 后：

**立即停止。**

不要执行 migration。

不要修改 API。

不要进入 Pagination。

不要进入 Conversation。

不要进入 Memory。

不要进入 Agent。

等待下一步指令。
