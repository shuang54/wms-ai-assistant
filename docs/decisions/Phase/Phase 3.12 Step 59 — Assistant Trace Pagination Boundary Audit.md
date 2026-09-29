你现在开始执行：

# Phase 3.12 Step 59 — Assistant Trace Pagination Boundary Audit

## 一、阶段目标

本阶段**只做 Assistant Trace 分页边界审计**。

不要立即实现 Pagination。

目标是回答：

> 当前 `Assistant Trace` 的数据规模、排序、查询方式是否已经到了必须分页的程度？

需要明确：

```text
当前 Trace 最大记录规模
        ↓
LLM Usage
Tool Executions
RAG Executions
        ↓
当前排序方式
        ↓
当前数据库查询方式
        ↓
是否存在实际性能/可扩展性风险
        ↓
Pagination 是否应该进入后续阶段
```

**本阶段是 Audit，不是 Pagination Implementation。**

---

# 二、开始前必须阅读

先阅读真实代码：

```text
backend/app/services/assistant_trace_service.py
backend/app/services/llm_usage_query_service.py
backend/app/services/tool_execution_query_service.py
backend/app/services/rag_execution_query_service.py

backend/app/db/llm_usage_repository.py
backend/app/db/tool_execution_repository.py
backend/app/db/rag_execution_repository.py

backend/app/api/assistant_trace.py
```

同时搜索：

```text
assistant-trace
list_by_assistant_request_id
list_by_request_id
llm_usage
tool_executions
rag_executions
order_by
created_at
started_at
limit
offset
```

确认当前真实实现。

---

# 三、严格禁止

本阶段禁止修改：

```text
AIOrchestrator
LLMProvider
LLMClient
TextToSQLService
RagService
ToolChatService
Router
SQL Validator
SQL Executor
Prompt
Project Context
LLM Usage Schema
Tool Execution Schema
RAG Execution Schema
```

禁止：

```text
新增 Pagination 参数
新增 limit/offset API 参数
修改 Assistant Trace HTTP contract
修改数据库表结构
新增数据库索引
新增 migration
修改查询语义
修改排序语义
```

禁止：

```text
Conversation
Memory
Agent
MCP
Unified Timeline
OpenTelemetry
Dashboard
Cost Tracking
```

本阶段不要实现这些能力。

---

# 四、审计 Assistant Trace HTTP Contract

确认当前：

```text
GET /api/observability/assistant-trace/{assistant_request_id}
```

真实 response。

记录：

```text
assistant_request_id
llm_usage[]
tool_executions[]
rag_executions[]
```

确认：

1. 是否存在 pagination
2. 是否存在 limit
3. 是否存在 offset
4. 是否存在 cursor
5. 是否存在 total
6. 是否存在 has_more
7. 是否存在 next_cursor

如果不存在，明确记录：

```text
Pagination = NOT IMPLEMENTED
```

不要修改。

---

# 五、审计单个 Trace 的理论最大规模

根据当前代码真实逻辑计算。

重点检查：

## 1. LLM Usage

当前一个 Assistant Request 最多可能产生多少 LLM calls？

特别检查：

```text
TextToSQL semantic retry
```

例如：

```text
attempt 1
attempt 2
...
```

不要猜。

找到当前实际：

```text
MAX_LLM_CALLS_PER_ASSISTANT_REQUEST
```

如果不是硬编码上限，也记录：

```text
No explicit application-level upper bound
```

---

## 2. Tool Executions

检查一个 Assistant Request 是否：

```text
只能执行一个 Tool
```

还是：

```text
可能多个 Tool
```

当前 Phase 3.x 的设计中如果仍然是单 Tool execution：

明确记录：

```text
max = 1
```

不要为了未来 Agent 场景假设多个。

---

## 3. RAG Executions

检查：

```text
一个 Assistant Request
```

当前是否只产生：

```text
0 or 1
```

个 RAG execution。

根据真实代码确认。

---

# 六、计算当前 Trace 最大记录规模

形成一个简单表格：

```text
Component | Current Max | Source
LLM Usage | ? | code/test
Tool      | ? | code/test
RAG       | ? | code/test
```

然后：

```text
Total Trace Records = LLM + Tool + RAG
```

例如：

```text
Normal:
LLM = 1
Tool = 0
RAG = 0
Total = 1

Text-to-SQL retry:
LLM = 2
Tool = 0
RAG = 0
Total = 2

RAG:
LLM = 1
Tool = 0
RAG = 1
Total = 2

Tool:
LLM = 0
Tool = 1
RAG = 0
Total = 1
```

具体数字必须来自当前真实代码。

---

# 七、审计查询排序

重点确认：

## LLM Usage

当前是否：

```text
ORDER BY created_at ASC/DESC
```

或者：

```text
ORDER BY id
```

## Tool Execution

确认：

```text
started_at
created_at
id
```

到底使用什么排序。

## RAG Execution

确认：

```text
started_at
id
```

到底使用什么排序。

记录：

```text
Ordering = ...
Stable = YES/NO
```

---

# 八、检查排序稳定性

非常重要。

如果当前：

```sql
ORDER BY created_at
```

但多个记录可能拥有相同 timestamp：

需要判断是否存在：

```text
tie-breaker
```

例如：

```sql
ORDER BY created_at ASC, id ASC
```

如果没有：

记录：

```text
Ordering stability risk = POSSIBLE
```

但是：

**不要修改 SQL。**

本阶段只报告。

---

# 九、审计 OFFSET 是否有必要

不要因为“以后数据会变多”就直接做分页。

结合当前真实数据规模：

```text
DB row count
```

检查：

```text
ai_ops.llm_usage_record
ai_ops.tool_execution_record
ai_ops.rag_execution_record
```

当前开发数据库如果仍然：

```text
0 rows
```

记录：

```text
Current persistent volume = 0
```

同时使用测试/evaluation evidence 推导：

```text
per-request trace size
```

不要把：

```text
全表数据量
```

和：

```text
单个 assistant_request 的 trace 数据量
```

混为一谈。

---

# 十、审计索引

检查当前 Trace 查询条件是否已经有索引。

例如：

```text
LLM Usage:
assistant_request_id

Tool:
request_id

RAG:
request_id
```

记录：

```text
Query Predicate
Existing Index
Index Type
```

不要新增索引。

如果已经有：

```text
B-tree
```

记录即可。

---

# 十一、审计 N+1 / 查询次数

确认：

```text
GET /assistant-trace/{id}
```

当前需要执行多少次 DB query。

例如：

```text
LLM Usage → 1 query
Tool → 1 query
RAG → 1 query
```

总计：

```text
3 queries
```

如果使用：

```text
3 independent query services
```

记录。

不要为了优化而重构。

---

# 十二、审计返回 payload

检查当前 Trace response 是否包含：

```text
SQL
Prompt
Messages
RAG content
Secrets
```

确认当前只返回：

```text
metadata
```

以及：

```text
usage
tool execution metadata
rag execution metadata
```

特别确认：

```text
LLMUsage
ToolExecution
RagExecution
```

没有因为 Trace 聚合而扩大安全边界。

---

# 十三、提出 Pagination 建议，但不要实现

最终根据实际结果给出：

### Option A

```text
当前规模足够小
→ 暂不需要 Pagination
```

### Option B

```text
需要 Pagination
→ 下一阶段设计
```

如果建议 Pagination：

只提出设计方向：

```text
Assistant Trace
    ↓
independent pagination per component
```

或者：

```text
Unified timeline pagination
```

但不要实现。

特别不要现在决定：

```text
OFFSET
```

还是：

```text
cursor
```

除非当前代码/数据已经证明需要。

---

# 十四、关于 OFFSET / Cursor

报告中可以记录 PostgreSQL 层面的事实：

`LIMIT/OFFSET` 可以实现分页，但大的 OFFSET 需要数据库计算并跳过前面的记录，因此如果未来 Trace 数据规模显著增长，需要重新评估 pagination strategy。

不要因为这一点就在本阶段实现 cursor pagination。

---

# 十五、测试

本阶段只允许：

```text
新增 audit test
```

如果已有测试足够：

```text
不要新增测试
```

可以新增：

```text
tests/test_assistant_trace_pagination_audit.py
```

只验证：

```text
current response has no pagination
current max trace size assumptions
query count
ordering
index availability
```

禁止修改生产行为。

---

# 十六、运行测试

执行：

```powershell
python -m pytest -q
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

再：

```powershell
python -m compileall -q backend tests scripts
```

如果 lint unavailable：

记录：

```text
lint = unavailable
```

不要安装新工具。

---

# 十七、Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff
```

必须确认：

```text
Production code changes = 0
DB schema changes = 0
API contract changes = 0
Prompt changes = 0
```

如果新增 audit test：

只允许：

```text
tests/...
```

如果新增文档：

只允许：

```text
docs/evaluation/...
docs/architecture.md
```

---

# 十八、Evaluation 文档

新增：

```text
docs/evaluation/phase-3.12-step-59-assistant-trace-pagination-audit.md
```

必须记录：

## 1. Scope

```text
Audit only
No pagination implementation
```

## 2. Current HTTP Contract

```text
GET /api/observability/assistant-trace/{assistant_request_id}
```

## 3. Current Trace Size

```text
LLM max
Tool max
RAG max
Total max
```

## 4. Ordering

```text
LLM:
Tool:
RAG:
```

## 5. Ordering Stability

```text
Stable:
Potential risk:
```

## 6. DB Queries

```text
LLM query count
Tool query count
RAG query count
Total
```

## 7. Indexes

```text
LLM:
Tool:
RAG:
```

## 8. Current DB Volume

```text
LLM rows:
Tool rows:
RAG rows:
```

## 9. Pagination Decision

明确：

```text
DEFER
```

或者：

```text
NEXT STEP REQUIRED
```

但不要实现。

## 10. Security

确认：

```text
No prompt
No SQL
No RAG content
No secrets
```

---

# 十九、最终报告

严格按照：

```text
Phase 3.12 Step 59 完成报告

1. Audit Scope
2. HTTP Contract
3. Trace Size
4. Ordering
5. Ordering Stability
6. DB Query Count
7. Index Audit
8. Current DB Volume
9. Payload Security
10. Pagination Decision
11. Tests
12. compileall
13. Git Diff
14. Production Code Changes
15. DB Schema Changes
16. Current Limitations

Phase 3.12 Step 59 STOP
```

---

# 二十、STOP

完成后：

**立即停止。**

不要实现 Pagination。

不要实现 Cursor。

不要实现 Unified Timeline。

不要进入 Conversation。

不要进入 Memory。

不要进入 Agent。

不要进入 MCP。

不要进入 OpenTelemetry。

不要进入 Dashboard。

等待下一步指令。
