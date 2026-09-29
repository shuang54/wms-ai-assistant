你现在开始执行：

# Phase 3.12 Step 54 — Assistant Trace 数据量与响应上限 Audit

## 一、阶段目标

本阶段只做：

> **审计 Assistant Trace 当前返回数据量、单请求记录数量以及未来分页的必要性。**

本阶段：

**只做 Audit / Design。**

禁止：

```text
修改 API contract
增加 page
增加 page_size
增加 cursor
增加 OFFSET
增加 LIMIT
修改 QueryService
修改 Repository
修改 DB Schema
新增 Index
```

目标是回答：

```text
当前 Assistant Trace 是否真的需要 Pagination？
如果需要，什么时候需要？
未来应该采用什么分页边界？
```

---

# 二、当前 Assistant Trace

当前 API：

```text
GET /api/observability/assistant-trace/{assistant_request_id}
```

响应：

```text
{
    assistant_request_id,
    llm_usage[],
    tool_executions[],
    rag_executions[]
}
```

三个列表分别来自：

```text
LLM Usage
Tool Execution
RAG Execution
```

当前没有：

```text
page
page_size
cursor
total
has_more
truncated
```

---

# 三、开始前必须阅读

先阅读真实代码：

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

继续检查 DTO：

```text
AssistantTraceResponse
LLMUsageTraceView
ToolExecutionTraceView
RagExecutionTraceView
```

以及：

```text
tests/test_assistant_trace*
tests/test_llm_usage*
tests/test_tool_execution*
tests/test_rag_execution*
```

不要根据文件名猜测。

---

# 四、确认当前 SQL

必须确认三个查询真实执行方式。

## LLM Usage

确认类似：

```sql
SELECT ...
FROM ai_ops.llm_usage_record
WHERE assistant_request_id = :assistant_request_id
ORDER BY created_at ASC, id ASC
```

## Tool Execution

确认：

```sql
SELECT ...
FROM ai_ops.tool_execution_record
WHERE request_id = :request_id
ORDER BY id ASC
```

## RAG Execution

确认：

```sql
SELECT ...
FROM ai_ops.rag_execution_record
WHERE request_id = :request_id
ORDER BY id ASC
```

必须记录：

```text
是否显式 SELECT
是否 JOIN
是否 N+1
是否 lazy loading
是否 LIMIT
是否 OFFSET
是否 cursor
是否 bound parameter
```

---

# 五、当前单请求最大记录数

不要只看平均值。

根据当前真实代码推导：

## 1. RAG

检查：

```text
AIOrchestrator
RagService
RagExecutionObserver
```

判断：

```text
一次 /api/ai/chat
```

最多产生多少条：

```text
rag_execution_record
```

---

## 2. Tool

检查：

```text
AIOrchestrator
ToolChatService
ToolExecutionObserver
```

区分：

```text
/api/ai/chat
```

与：

```text
/api/chat/with-tools
```

不要混淆两个 request_id 生命周期。

分别记录：

```text
AIOrchestrator Tool route:
max records / request = ?

ToolChatService:
max records / request = ?
```

---

## 3. LLM Usage

检查：

```text
TextToSQLService
MAX_ATTEMPTS
retry
fallback
```

以及：

```text
RAG
Tool
Router
```

推导：

```text
/api/ai/chat
```

单个 assistant_request_id 最多关联多少：

```text
llm_usage_record
```

必须基于真实代码，而不是猜测。

---

# 六、计算理论最大 Trace 大小

建立一个简单表：

```text
Route              LLM Usage   Tool   RAG
------------------------------------------------
RAG                  ?          0      ?
TOOL                 ?          ?      0
TEXT_TO_SQL          ?          0      0
```

然后给出：

```text
maximum records per assistant_request_id
```

注意：

不要为了“安全”随便写：

```text
100
1000
10000
```

必须来自：

```text
当前代码上限
```

如果某一项理论上无上限：

明确写：

```text
UNBOUNDED
```

并说明原因。

---

# 七、检查是否存在隐藏增长路径

搜索：

```text
request_id
assistant_request_id
ToolExecutionRecord
RagExecutionObservation
LLMUsageRecord
```

确认是否存在：

```text
loop
while
retry
MAX_TOOL_ROUNDS
MAX_ATTEMPTS
fallback
multiple provider calls
parallel execution
```

重点确认：

```text
一次 Assistant Trace
```

是否可能随着业务逻辑产生大量 records。

不要修改任何限制。

本阶段只记录现状。

---

# 八、检查当前响应大小

如果当前环境允许只读分析，可以使用：

```text
现有测试数据
```

不要插入生产数据。

至少估算：

```text
Trace response JSON
```

在以下情况下的大致大小：

```text
1 record
10 records
50 records
100 records
```

如果无法可靠估算：

写：

```text
response size: not empirically measured
```

不要伪造 KB 数值。

---

# 九、检查当前真实数据库规模

如果可以安全连接当前开发数据库：

只读检查：

```sql
SELECT COUNT(*)
FROM ai_ops.llm_usage_record;

SELECT COUNT(*)
FROM ai_ops.tool_execution_record;

SELECT COUNT(*)
FROM ai_ops.rag_execution_record;
```

同时可以检查：

```text
pg_total_relation_size
```

禁止：

```text
INSERT
UPDATE
DELETE
TRUNCATE
DDL
```

如果数据库为空：

明确记录：

```text
current dev volume = 0
```

不要用空数据库推导生产规模。

---

# 十、评估三种分页方案

本阶段只比较，不实现。

## Option A：固定上限 + truncated

例如未来：

```text
llm_usage: max N
tool_executions: max N
rag_executions: max N
```

响应增加：

```text
truncated: true
```

优点：

```text
简单
API 变化小
适合当前单请求 Trace
```

缺点：

```text
超过上限无法继续读取
```

---

## Option B：page / page_size

例如：

```text
?page=1&page_size=50
```

优点：

```text
简单直观
```

缺点：

```text
三个独立列表如何分页？
是否每个列表独立 page？
还是统一 page？
```

特别注意：

当前 Trace 是：

```text
LLM
Tool
RAG
```

三个独立数据源。

不能直接假设一个：

```text
page
```

就能自然解决三个列表。

---

## Option C：Cursor / Keyset Pagination

例如：

```text
cursor
```

但当前：

```text
ToolExecutionTraceView
RagExecutionTraceView
```

没有暴露数据库 primary key。

而三个数据源排序键不同：

```text
LLM:
created_at ASC, id ASC

Tool:
id ASC

RAG:
id ASC
```

因此不要在本阶段直接设计 cursor contract。

只分析：

```text
未来如果采用 cursor：
需要什么稳定排序键？
是否需要暴露 opaque cursor？
是否需要统一 event id？
```

---

# 十一、重点分析 OFFSET 风险

PostgreSQL 官方文档指出：

```text
OFFSET rows
```

之前的记录仍然需要由服务器计算/跳过，因此较大的 OFFSET 可能效率较低。

因此本阶段明确：

```text
不要因为“分页”这个词就默认使用 OFFSET。
```

特别是：

```text
assistant trace
```

未来如果数据量增长，应重新评估：

```text
OFFSET
vs
keyset/cursor
```

---

# 十二、当前结论

必须根据真实代码给出：

```text
Pagination Status:
REQUIRED / NOT REQUIRED / DEFER
```

推荐当前阶段优先考虑：

```text
DEFER
```

但只有在真实记录上限足够小的情况下才能这样结论。

如果发现：

```text
单 request 可能产生大量 records
```

则必须报告：

```text
Pagination design should be scheduled before further Trace API expansion.
```

不要自行进入实现阶段。

---

# 十三、定义未来触发条件

建立明确的 Trigger。

例如：

```text
Trigger A:
单 assistant_request_id > 10 records

Trigger B:
Trace JSON response > 64 KB

Trigger C:
前端需要浏览完整历史

Trigger D:
出现 multi-round Tool Calling

Trigger E:
出现 Agent / loop

Trigger F:
出现真正统一 timeline
```

这些数字/条件如果没有当前项目依据，可以标记为：

```text
proposed engineering trigger
```

不要伪装成当前系统限制。

---

# 十四、安全审计

确认分页设计不能导致：

```text
跨 request_id 查询
```

例如：

```text
request_id=A
```

只能返回：

```text
A
```

不能因为：

```text
page
offset
cursor
```

而读取：

```text
B
```

尤其检查未来 cursor 必须绑定：

```text
assistant_request_id
```

不能成为全局 cursor。

本阶段只记录设计要求。

---

# 十五、禁止修改

本阶段禁止修改：

```text
backend/app/api/assistant_trace.py
backend/app/services/assistant_trace_query_service.py
backend/app/services/llm_usage_query_service.py
backend/app/services/tool_execution_persistent_query_service.py
backend/app/services/rag_execution_persistent_query_service.py
backend/app/db/*
```

禁止：

```text
新增 API 参数
新增 response 字段
新增分页 DTO
新增 cursor
新增 index
修改 SQL
修改排序
修改 trace contract
```

除非 Audit 发现明确安全 bug。

如果发现 bug：

**停止并报告，不要顺手修复。**

---

# 十六、Audit 文档

新增：

```text
docs/evaluation/phase-3.12-step-54-trace-pagination-audit.md
```

至少包含：

```text
1. Current Trace API
2. Current SQL
3. Record bounds
4. RAG max records
5. Tool max records
6. LLM max records
7. Theoretical maximum
8. Current DB volume
9. Response size
10. Hidden growth paths
11. Option A
12. Option B
13. Option C
14. OFFSET risk
15. Pagination trigger
16. Security boundary
17. Recommendation
18. Limitations
```

保持简洁。

---

# 十七、测试

本阶段不要求新增大量测试。

执行：

```powershell
python -m pytest -q tests/test_assistant_trace*
```

如果 PowerShell glob 不生效，改为：

```powershell
python -m pytest -q tests/test_assistant_trace_rag_integration.py
```

根据实际文件执行。

然后：

```powershell
python -m compileall -q backend tests scripts
```

如果成本可接受：

```powershell
python -m pytest -q
```

---

# 十八、DB writes

必须：

```text
DB writes = 0
```

本阶段只能：

```text
SELECT
```

禁止：

```text
INSERT
UPDATE
DELETE
TRUNCATE
ALTER
CREATE
DROP
REINDEX
```

---

# 十九、Git Diff

完成后：

```powershell
git status --short
git diff --stat
git diff
```

确认：

```text
生产代码 = 0
API contract = unchanged
DB schema = unchanged
Index count = unchanged
```

---

# 二十、最终报告

严格输出：

```text
Phase 3.12 Step 54 完成汇报

1. Audit Scope
2. Current Trace API
3. Current SQL
4. RAG max records
5. Tool max records
6. LLM max records
7. Theoretical maximum
8. Current DB volume
9. Response size
10. Hidden growth paths
11. Pagination Options
12. OFFSET Risk
13. Pagination Trigger
14. Security Boundary
15. Recommendation
16. Tests
17. compileall
18. DB writes
19. Git Diff
20. Current Limitations
```

最后：

```text
Phase 3.12 Step 54 STOP
```

---

# 二十一、强制 STOP

完成 Step 54 后：

**立即停止。**

不要：

```text
Step 55
Pagination implementation
Unified Timeline
Conversation
Memory
Agent
MCP
OpenTelemetry
Dashboard
```

不要修改：

```text
LLM Usage API
Assistant Trace API
Tool Observability
RAG Observability
```

只返回：

```text
Phase 3.12 Step 54 完成报告
```

等待下一步指令。
