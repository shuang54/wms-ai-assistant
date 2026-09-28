你现在开始执行 **Phase 3.12 Step 41 — Persistent Tool Trace Read Integration**。

# 一、阶段目标

Phase 3.12 Step 40 已经验证：

```text
POST /api/ai/chat
        ↓
assistant_request_id = A
        ├── LLM Usage → PostgreSQL
        └── Tool Execution → Runtime Memory
        ↓
GET /api/observability/assistant-trace/A
```

当前最大限制是：

```text
LLM Usage
    ↓
PostgreSQL
    ↓
持久化

Tool Execution
    ↓
Runtime InMemory Collector
    ↓
进程内
```

因此本阶段只解决：

> **让 Assistant Trace 能够读取已经存在的 Persistent Tool Execution Record。**

最终目标：

```text
Assistant Request A
        │
        ├── LLM Usage
        │      ↓
        │   PostgreSQL
        │
        └── Tool Execution
               ↓
            PostgreSQL
```

然后：

```text
AssistantTraceQueryService
        ↓
AssistantTraceView
        ↓
Assistant Trace API
```

---

# 二、重要原则

本阶段不是重新设计 Tool Observability。

Phase 3.11 已经完成：

```text
ToolExecutionRecord
ToolExecutionRepository
ToolExecutionPersistenceService
Persistent Tool History API
Persistent Tool Metrics API
```

**优先复用已有 Persistent Tool Execution Read Boundary。**

不要重新实现 SQL。

不要重新实现 Repository。

不要重新创建 Tool Execution 表。

---

# 三、开始前必须阅读

先阅读真实代码：

```text
backend/app/services/tool_observability_query_service.py
backend/app/db/
backend/app/services/
backend/app/api/
```

重点寻找 Phase 3.11 已存在的：

```text
Persistent Tool Execution Repository
Persistent Tool Execution Query Service
ToolExecutionRecord
ToolExecutionSnapshot
/api/observability/tools/history
```

同时阅读：

```text
backend/app/services/assistant_trace_query_service.py
backend/app/api/assistant_trace.py
```

以及：

```text
tests/test_assistant_trace_query_service.py
tests/test_assistant_trace_api.py
tests/test_assistant_trace_correlation_e2e.py
```

确认当前：

```text
Runtime Tool Snapshot
```

与：

```text
Persistent Tool Record
```

的字段和语义。

**不要假设两者完全相同。**

---

# 四、严格范围

允许修改：

```text
AssistantTraceQueryService
Tool Observability Read Boundary
Assistant Trace API
相关测试
evaluation 文档
architecture 文档
```

如果现有 Persistent Tool Read Boundary 已经足够：

**只接入，不修改。**

允许新增极少量：

```text
Tool Persistent Read DTO
```

但只有在现有 DTO 无法安全复用时才允许。

---

# 五、明确禁止

禁止：

```text
Trace DB
Trace Repository
Trace Table
OpenTelemetry
Span
Conversation
Memory
Agent
MCP
Streaming
Dashboard
Redis
Cache
```

禁止修改：

```text
AIOrchestrator
AI Router
ToolExecutionService
LLM Provider
Text-to-SQL
SQL Validator
SQL Executor
```

禁止修改：

```text
ToolExecutionRecord
```

的数据库结构。

禁止 Migration。

禁止新增数据库表。

---

# 六、关键设计决策

当前 Assistant Trace：

```text
LLM → PostgreSQL
Tool → Runtime Memory
```

本阶段目标：

```text
LLM → PostgreSQL
Tool → Persistent PostgreSQL
```

因此 Assistant Trace 的主要读取路径变成：

```text
AssistantTraceQueryService
        │
        ├── LLMUsageQueryService
        │       ↓
        │    PostgreSQL
        │
        └── ToolObservabilityQueryService
                ↓
          Persistent Tool Read Boundary
                ↓
             PostgreSQL
```

---

# 七、不要同时合并 Runtime + Persistent

非常重要。

不能：

```text
Runtime Tool Records
+
Persistent Tool Records
↓
concat
```

否则会产生：

```text
duplicate Tool Execution
```

本阶段 Assistant Trace：

**默认以 Persistent Tool Record 为唯一 Tool 数据源。**

Runtime Collector 继续存在，用于：

```text
/api/observability/tools
```

等现有 Runtime API。

不要删除 Runtime Collector。

不要改变现有 Runtime API。

---

# 八、为什么选择 Persistent

Assistant Trace 是：

```text
historical request trace
```

而不是：

```text
current process runtime snapshot
```

因此：

```text
GET /api/observability/assistant-trace/A
```

应该能够在：

```text
进程重启
多 worker
```

情况下继续查询已经持久化的 Tool Execution。

本阶段只验证这个能力。

---

# 九、Persistent Tool Read Boundary

如果 Phase 3.11 已存在：

```text
list_by_request_id()
```

直接复用。

如果不存在：

允许在：

```text
ToolExecutionRepository
ToolObservabilityQueryService
```

增加：

```text
list_by_request_id(request_id)
```

但必须遵循：

```text
API
 ↓
AssistantTraceQueryService
 ↓
ToolObservabilityQueryService
 ↓
Repository
```

AssistantTraceQueryService：

**不得直接 import Repository。**

---

# 十、Tool Trace DTO

必须先确认已有：

```text
ToolExecutionSnapshot
```

与 Persistent Record 的差异。

如果 Persistent Record 已经可以安全映射到：

```text
ToolExecutionSnapshot
```

直接复用。

如果不能：

新增一个内部 read DTO，例如：

```text
PersistentToolExecutionView
```

但：

**不要把 ORM Model 暴露给 AssistantTraceQueryService。**

---

# 十一、安全字段

Persistent Tool Trace 最终仍然只能进入现有安全字段：

```text
request_id
round
tool_name
started_at
finished_at
duration_ms
success
project_id
tool_call_id
error_code
error_type
```

不得进入：

```text
arguments
result
result.data
SQL
prompt
messages
raw_response
secret
password
api_key
database_url
authorization
Session
Connection
```

如果 Persistent Record 有更多字段：

**只做显式安全映射。**

禁止：

```python
vars(...)
asdict(...)
__dict__
model_dump()
```

---

# 十二、Ordering

Persistent Tool Execution 使用数据库已有稳定排序。

优先使用：

```text
started_at ASC
id ASC
```

如果 Phase 3.11 已经定义了其他稳定 ordering：

**复用现有语义，不重新定义。**

AssistantTraceQueryService：

**不再次排序。**

---

# 十三、Empty Semantics

继续保持 Step 38 / 39：

```text
不存在 assistant_request_id
        ↓
200
        ↓
{
  "assistant_request_id": "...",
  "llm_usage": [],
  "tool_executions": []
}
```

不要：

```text
404
```

---

# 十四、Error Handling

Persistent DB 不可用时：

**不能返回空 Tool Trace。**

例如：

```text
Tool PostgreSQL unavailable
        ↓
Repository error
        ↓
AssistantTraceQueryService
        ↓
HTTP 5xx
```

不能：

```python
except Exception:
    return []
```

否则：

```text
DB failure
```

会被错误解释为：

```text
没有 Tool Execution
```

---

# 十五、Step 40 Regression

必须保留 Step 40 行为。

重新验证：

```text
LLM only
Tool only
LLM + Tool
Cross request
Security
```

但现在：

```text
Tool Execution
```

读取来源应该变成：

```text
Persistent PostgreSQL
```

而不是：

```text
Runtime Collector
```

---

# 十六、最重要的 E2E

增加：

```text
test_assistant_trace_reads_persisted_tool_execution
```

流程：

```text
POST /api/ai/chat
        ↓
真实 ToolExecutionService
        ↓
Persistent Tool Execution
        ↓
清空 Runtime Collector
        ↓
GET /api/observability/assistant-trace/A
        ↓
仍然能够得到 Tool Execution
```

这是本阶段最重要的证明。

也就是说：

即使：

```text
Runtime Collector = empty
```

Trace API 仍然能够返回：

```text
tool_executions >= 1
```

因为数据来自 PostgreSQL。

---

# 十七、Restart-like Test

不需要真的重启服务器。

可以模拟：

```text
POST /api/ai/chat
        ↓
Persistent Tool Record created
        ↓
创建新的 Runtime Collector
        ↓
创建新的 ToolObservabilityQueryService
        ↓
AssistantTraceQueryService
        ↓
GET Trace
```

验证：

```text
Tool Trace still exists
```

这样可以证明：

```text
Trace ≠ Runtime Memory
```

---

# 十八、Cross Request Isolation

必须验证：

```text
A
B
```

两个 request。

数据库：

```text
Tool A
Tool B
```

查询：

```text
Trace A
Trace B
```

确保：

```text
A only → A
B only → B
```

---

# 十九、DB Tests

新增/扩展：

```text
tests/test_assistant_trace_persistent_tool_db.py
```

如果现有测试组织更合理，则遵循现有结构。

至少：

```text
1. persisted Tool only
2. LLM + persisted Tool
3. cross request
4. empty
5. runtime collector empty but persistent record exists
6. security fields
```

使用：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

---

# 二十、不要删除 Runtime Tool Observability

现有：

```text
/api/observability/tools
```

仍然保持 Runtime semantics。

现有：

```text
/api/observability/tools/history
```

仍然保持 Persistent semantics。

Assistant Trace：

```text
/api/observability/assistant-trace/{assistant_request_id}
```

现在改成：

```text
Persistent Tool + Persistent LLM
```

三者职责：

```text
Runtime API
    ↓
当前进程实时观察

History API
    ↓
Tool 历史记录

Assistant Trace API
    ↓
一次 Assistant Request 的历史链路
```

---

# 二十一、API Contract

不要修改：

```text
GET /api/observability/assistant-trace/{assistant_request_id}
```

response 结构保持：

```json
{
  "assistant_request_id": "...",
  "llm_usage": [],
  "tool_executions": []
}
```

不要增加：

```text
source
runtime
persistent
trace_id
span_id
```

这些字段。

数据源是内部实现细节。

---

# 二十二、不要引入 OpenTelemetry

虽然未来可能需要：

```text
OpenTelemetry
TraceId
SpanId
```

但本阶段：

**不要做。**

当前：

```text
assistant_request_id
```

已经能够满足应用层 correlation。

OTel 应该作为单独架构阶段，而不是混入 Persistent Tool Trace。

---

# 二十三、测试要求

至少覆盖：

### Unit

```text
Persistent Tool read boundary
request_id exact match
ordering
empty
error propagation
security mapping
```

### Assistant Trace

```text
LLM only
Tool only
LLM + Tool
cross request
runtime empty + persistent present
```

### API

```text
same endpoint
same response schema
same empty semantics
same error semantics
```

### Architecture

确保：

```text
AssistantTraceQueryService
    ↓
ToolObservabilityQueryService
    ↓
Persistent Tool Read Boundary
```

而不是：

```text
AssistantTraceQueryService
    ↓
Repository
```

---

# 二十四、Full Regression

执行：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

Compile：

```powershell
python -m compileall -q backend tests
```

检查：

```text
LSP
lint
```

不要安装新 lint 工具。

---

# 二十五、DB Residue

测试数据使用：

```text
step41-
```

provider 或其他已有安全测试标识。

测试结束：

```text
Tool Execution residue = 0
LLM Usage residue = 0
```

不得：

```text
TRUNCATE
```

不得删除真实业务数据。

不得修改 schema。

---

# 二十六、Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff
```

重点检查没有误改：

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

如果发现生产 bug：

**立即停止并报告。**

不要为了测试通过修改核心逻辑。

---

# 二十七、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.12 Step 41 — Persistent Tool Trace Integration.md
```

记录：

## Architecture

```text
Assistant Request
       │
       ├── LLM Usage
       │      ↓
       │   PostgreSQL
       │
       └── Tool Execution
              ↓
           PostgreSQL
```

## Data Sources

```text
LLM → ai_ops.llm_usage_record
Tool → ai_ops.tool_execution_record
```

## Runtime APIs

```text
/api/observability/tools
```

仍然：

```text
Runtime Memory
```

## History API

```text
/api/observability/tools/history
```

仍然：

```text
Persistent Tool History
```

## Assistant Trace API

```text
/api/observability/assistant-trace/{assistant_request_id}
```

现在：

```text
Persistent LLM + Persistent Tool
```

---

# 二十八、完成后报告

严格：

```text
Phase 3.12 Step 41 COMPLETE

1. Persistent Tool Read Boundary
2. AssistantTraceQueryService
3. Data Source
4. Runtime vs Persistent
5. LLM + Tool Trace
6. Cross Request Isolation
7. Restart-like Test
8. Security
9. API Regression
10. DB Tests
11. Full Tests
12. compile / lint
13. DB residue
14. Git Diff
15. 当前限制
```

最后：

```text
Step 41 完成。

Assistant Trace 已从：

LLM → PostgreSQL
Tool → Runtime Memory

升级为：

LLM → PostgreSQL
Tool → PostgreSQL

Runtime Tool API 保持原语义。

History API 保持原语义。

Assistant Trace API 保持原 endpoint / response contract。

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
不要进入 Step 42。
```
