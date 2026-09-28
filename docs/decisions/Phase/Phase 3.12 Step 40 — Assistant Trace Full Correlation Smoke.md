你现在开始执行 **Phase 3.12 Step 40 — Assistant Trace Full Correlation Smoke**。

# 一、阶段目标

本阶段**不新增核心架构能力**。

唯一目标：

> 验证 Phase 3.12 已建立的 `assistant_request_id` 是否能够从 `/api/ai/chat` 成功贯穿到 LLM Usage 和 Tool Execution，并最终通过 Assistant Trace HTTP API 查询回来。

完整链路：

```text
POST /api/ai/chat
        ↓
AIOrchestrator
        ↓
assistant_request_id = A
        │
        ├──────────────→ LLM Usage
        │                  assistant_request_id = A
        │
        └──────────────→ Tool Execution
                           request_id = A
                                  ↓
GET /api/observability/assistant-trace/A
                                  ↓
                         AssistantTraceResponse
```

最终验证：

```text
一个 Assistant Request
        ↓
一个 request_id
        ↓
LLM Usage + Tool Execution
        ↓
同一个 Trace API
```

---

# 二、严格范围

本阶段允许：

```text
新增 smoke / integration tests
新增少量测试 fixture
新增 evaluation 文档
必要时修改测试装配代码
```

原则：

**优先测试现有生产代码，不修改生产逻辑。**

---

# 三、明确禁止

不要修改：

```text
AIOrchestrator
AI Router
RAG
ToolExecutionService
Tool Observability Core
LLM Usage Repository
LLM Usage Query Service
AssistantTraceQueryService
Assistant Trace API
LLM Provider
Text-to-SQL
SQL Validator
SQL Executor
```

除非测试明确发现已有 bug。

禁止新增：

```text
OpenTelemetry
Trace DB
Trace Repository
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

本阶段也：

**不要优化任何性能。**

---

# 四、开始前必须阅读

先阅读：

```text
backend/app/api/orchestrator_chat.py
backend/app/api/assistant_trace.py

backend/app/services/ai_orchestrator_service.py
backend/app/services/llm_usage_query_service.py
backend/app/services/tool_observability_query_service.py
backend/app/services/assistant_trace_query_service.py

backend/app/db/llm_usage_repository.py

tests/
```

重点确认：

1. `/api/ai/chat` 如何生成 `assistant_request_id`。
2. response metadata 如何返回 `assistant_request_id`。
3. LLM Usage 如何获得 `assistant_request_id`。
4. Tool Execution 如何记录 request_id。
5. 当前测试如何 Fake LLM。
6. 当前测试如何 Fake Tool。
7. 当前 DB fixture 如何创建和清理 LLM Usage。
8. 当前 Tool Collector 如何注入。
9. Assistant Trace API 如何取得应用级 Query Service。

**不要假设已有接口。**

---

# 五、Smoke Case 设计

至少建立三个核心 Case。

## Case A：LLM Only

```text
POST /api/ai/chat
```

使用一个不会触发 Tool 的安全问题。

验证：

```text
HTTP 200
assistant_request_id != empty
```

然后：

```text
GET /api/observability/assistant-trace/{assistant_request_id}
```

验证：

```text
200
assistant_request_id 相同

llm_usage >= 1
tool_executions == []
```

---

# 六、Case B：Tool Only / Tool Path

使用当前项目已有的安全只读 Tool 测试能力。

不要新增业务 Tool。

通过：

```text
POST /api/ai/chat
```

让 Orchestrator 走已有 Tool 路径。

验证：

```text
assistant_request_id = A
```

然后：

```text
GET /api/observability/assistant-trace/A
```

验证：

```text
tool_executions >= 1
```

并确认：

```text
每个 Tool Execution.request_id == A
```

LLM Usage 是否存在：

**按照真实当前执行链路判断，不要人为要求必须 0。**

如果 Tool 路径本身会产生 LLM Usage：

```text
LLM Usage >= 1
Tool Execution >= 1
```

这是正常的。

---

# 七、Case C：LLM + Tool

这是本阶段最重要的 Case。

构造一个能够触发现有 Tool 的请求。

流程：

```text
POST /api/ai/chat
        ↓
Router
        ↓
Tool
        ↓
LLM / Tool existing execution
        ↓
assistant_request_id = A
```

然后：

```text
GET /api/observability/assistant-trace/A
```

验证：

```text
response.assistant_request_id == A

llm_usage:
    所有 row.assistant_request_id == A

tool_executions:
    所有 row.request_id == A
```

并且：

```text
A 不包含 B 的任何数据
```

---

# 八、Correlation 强断言

这是本阶段核心。

不能只断言：

```text
trace 不为空
```

必须明确验证：

## 1. Assistant API

```text
POST /api/ai/chat
```

返回：

```text
metadata.request_id = A
```

## 2. LLM Usage

查询 Trace：

```text
trace.llm_usage
```

每一条：

```text
assistant_request_id == A
```

## 3. Tool Execution

查询 Trace：

```text
trace.tool_executions
```

每一条：

```text
request_id == A
```

## 4. Trace API

```text
trace.assistant_request_id == A
```

最终必须形成：

```text
                 A
                 │
       ┌─────────┴─────────┐
       ↓                   ↓
   LLM Usage          Tool Execution
assistant_request_id   request_id
       = A                 = A
       │                   │
       └─────────┬─────────┘
                 ↓
          Assistant Trace
```

---

# 九、Cross Request Isolation

必须创建两个不同 request：

```text
Request A
Request B
```

分别：

```text
POST /api/ai/chat
```

得到：

```text
A
B
```

然后：

```text
GET /api/observability/assistant-trace/A
GET /api/observability/assistant-trace/B
```

验证：

```text
Trace A:
    LLM assistant_request_id == A
    Tool request_id == A

Trace B:
    LLM assistant_request_id == B
    Tool request_id == B
```

不能：

```text
A → B data
B → A data
```

---

# 十、真实数据库验证

如果当前测试基础设施允许：

```text
RUN_DB_TESTS=1
```

增加少量 DB-gated integration。

真实链路：

```text
/api/ai/chat
        ↓
LLM Usage persistence
        ↓
PostgreSQL
        ↓
AssistantTraceQueryService
        ↓
Assistant Trace API
```

但：

**不要使用生产数据库。**

只允许使用现有测试 PostgreSQL。

测试前后：

```text
count_before
count_after
```

如果测试使用专门 provider prefix：

```text
step40-
```

必须定向清理。

最终：

```text
DB residue = 0
```

---

# 十一、Tool Runtime Memory 验证

注意当前 Tool 数据源是：

```text
Runtime InMemory Collector
```

不是：

```text
Persistent Tool History
```

因此验证：

```text
POST /api/ai/chat
        ↓
Tool execution
        ↓
Application Collector
        ↓
AssistantTraceQueryService
        ↓
Trace API
```

不要把：

```text
/api/observability/tools/history
```

的数据和 Trace API 数据强行合并。

本阶段保持 Step 38/39 的设计：

```text
LLM → PostgreSQL
Tool → Runtime Memory
```

---

# 十二、禁止 Fake Trace API

非常重要。

测试不能：

```python
fake_trace = AssistantTraceResponse(...)
```

然后直接测试 serializer。

至少一个核心测试必须：

```text
POST /api/ai/chat
        ↓
真实 application wiring
        ↓
GET /api/observability/assistant-trace/{request_id}
```

真正经过：

```text
AIOrchestrator
LLM Usage correlation
Tool Collector
AssistantTraceQueryService
Assistant Trace API
```

如果由于测试环境无法完整启动某个真实依赖：

允许 Fake：

```text
LLM Provider
```

或者：

```text
Tool handler
```

但不能 Fake：

```text
assistant_request_id propagation
LLM Usage correlation
Tool request_id
AssistantTraceQueryService
Assistant Trace API
```

---

# 十三、真实 LLM 策略

默认：

```text
0 real LLM
```

不要因为叫 Smoke Test 就自动调用 DeepSeek。

如果当前项目已经存在：

```text
@pytest.mark.real_llm
```

可以增加一个**可选** real LLM smoke。

但：

```text
pytest -q
```

必须：

```text
0 real LLM
```

如果没有可靠的 real LLM test infrastructure：

**不要新增。**

---

# 十四、Security Regression

通过完整链路后，再验证 Trace API：

```text
POST /api/ai/chat
→ request_id
→ GET trace
```

返回仍然不能出现：

```text
prompt
messages
raw_response
tool arguments
ToolResult.data
SQL
password
API key
database URL
authorization
session
connection
traceback
internal_debug
```

不要只测试静态 API DTO。

必须至少有一个：

```text
真实 application Trace
```

经过 HTTP response 的安全字段断言。

---

# 十五、API Regression

确认原有 API 没有变化：

```text
/api/ai/chat
/api/usage/analytics
/api/observability/tools
/api/observability/tools/history
/api/observability/tools/metrics
/api/observability/tools/metrics/persistent
```

尤其：

```text
/api/ai/chat
```

response：

```text
route
content
data
metadata
```

原有字段保持不变。

本阶段只验证：

```text
metadata.request_id
```

可以被 Trace API 继续使用。

不要增加新的 response 字段。

---

# 十六、测试文件

推荐新增：

```text
tests/test_assistant_trace_correlation_e2e.py
```

如果项目已有更适合的 E2E / integration 目录，遵循现有结构。

至少包含：

```text
test_llm_only_correlation
test_tool_correlation
test_llm_and_tool_correlation
test_cross_request_isolation
test_trace_security
```

DB：

```text
tests/test_assistant_trace_correlation_e2e_db.py
```

如果现有 DB-gated 组织方式不同，遵循现有结构。

---

# 十七、不要增加新的 Trace DTO

本阶段必须复用：

```text
AssistantTraceView
AssistantTraceResponse
LLMUsageTraceResponse
ToolExecutionTraceResponse
```

不要再创建：

```text
AssistantTraceE2EResponse
AssistantCorrelationResponse
TraceSmokeResponse
```

避免 DTO 膨胀。

---

# 十八、不要增加新的 Endpoint

本阶段只使用：

```text
POST /api/ai/chat

GET /api/observability/assistant-trace/{assistant_request_id}
```

禁止新增：

```text
/api/trace
/api/assistant/trace
/api/trace/full
/api/trace/debug
/api/assistant/correlation
```

---

# 十九、Evaluation 文档

新增：

```text
docs/evaluation/Phase 3.12 Step 40 — Assistant Trace Correlation Smoke.md
```

记录：

## 1. Test Architecture

```text
/api/ai/chat
      ↓
AIOrchestrator
      ↓
assistant_request_id
      ├── LLM Usage
      └── Tool Execution
              ↓
GET /api/observability/assistant-trace/{id}
```

## 2. Cases

```text
LLM Only
Tool
LLM + Tool
Cross Request
Security
```

## 3. Correlation

记录：

```text
assistant_request_id
LLM assistant_request_id
Tool request_id
```

但不要记录：

```text
API Key
password
database URL
credentials
prompt
raw response
tool arguments
```

---

# 二十、Tests

先运行：

```powershell
python -m pytest -q tests/test_assistant_trace_correlation_e2e.py
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_assistant_trace_correlation_e2e_db.py
```

Compile：

```powershell
python -m compileall -q backend tests
```

如果已有 lint：

继续使用。

不要因为 lint 不存在而安装新工具。

---

# 二十一、Git Diff

完成后：

```powershell
git status --short
git diff --stat
git diff
```

确认没有修改：

```text
AIOrchestrator core logic
AI Router
RAG
ToolExecutionService
LLM Provider
Text-to-SQL
SQL Validator
SQL Executor
```

如果为了测试发现生产 bug：

**立即停止并报告，不要自行修核心逻辑。**

---

# 二十二、完成标准

必须证明：

```text
POST /api/ai/chat
        ↓
assistant_request_id = A
        ↓
┌──────────────────────────────┐
│ LLM Usage                    │
│ assistant_request_id = A     │
└──────────────────────────────┘

┌──────────────────────────────┐
│ Tool Execution               │
│ request_id = A               │
└──────────────────────────────┘

        ↓

GET /api/observability/assistant-trace/A
        ↓
正确返回两类数据
        ↓
无跨请求污染
        ↓
无敏感字段泄漏
```

---

# 二十三、最终报告

严格按照：

```text
Phase 3.12 Step 40 COMPLETE

1. Full Correlation Chain
2. LLM Only
3. Tool
4. LLM + Tool
5. Cross Request Isolation
6. Security
7. DB Tests
8. Full Tests
9. compile / lint
10. Real LLM
11. DB residue
12. Git Diff
13. 当前限制
```

最后明确：

```text
Step 40 完成。

已验证：
/api/ai/chat
    ↓
assistant_request_id
    ↓
LLM Usage + Tool Execution
    ↓
Assistant Trace API

未新增：
- Trace DB
- OpenTelemetry
- Conversation
- Memory
- Agent
- MCP
- Streaming
- Dashboard

立即 STOP。
不要进入 Step 41。
```
