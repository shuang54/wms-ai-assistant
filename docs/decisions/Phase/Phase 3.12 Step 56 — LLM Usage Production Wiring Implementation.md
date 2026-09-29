# Phase 3.12 Step 56 — LLM Usage Production Wiring Implementation

你现在开始实现：

`D:\coding\ai\wms-ai-assistant`

## 一、阶段目标

在 Phase 3.12 Step 55 审计结论基础上：

**将现有 `DatabaseLLMAccountingSink` 正式接入生产默认 LLM Client。**

目标：

```text
POST /api/ai/chat
        ↓
AIOrchestrator
        ↓
LLMProvider
        ↓
LLMClient
        ↓
DatabaseLLMAccountingSink
        ↓
LLMUsageRepository
        ↓
ai_ops.llm_usage_record
        ↓
Assistant Trace
        ↓
GET /api/observability/assistant-trace/{assistant_request_id}
        ↓
llm_usage[]
```

最终验证：

> 生产默认 `/api/ai/chat` 产生的 LLM 调用能够持久化到 `ai_ops.llm_usage_record`，并通过 Assistant Trace 查询出来。

---

# 二、严格范围

## 允许修改

优先只允许：

```text
backend/app/llm/client.py
backend/app/api/orchestrator_chat.py
```

如果真实代码结构证明必须修改其他生产装配位置：

允许最小修改。

允许新增：

```text
tests/test_llm_usage_production_wiring.py
tests/test_llm_usage_production_wiring_e2e_db.py

docs/evaluation/phase-3.12-step-56-llm-usage-production-wiring.md
```

如果已有测试可以完整覆盖，不要为了凑文件而新增。

---

# 三、明确禁止

不要修改：

```text
TextToSQLService
RagService
ToolChatService
AIRouterService
AIOrchestratorService
SQLValidator
SQLExecutor
Prompt
LLMProvider contract
DeepSeekProvider
LLMUsageRepository contract
AssistantTraceQueryService
Assistant Trace API contract
RAG Observability
Tool Observability
```

不要新增：

```text
runtime config
environment variable
API
database table
database column
database index
migration framework
retry
fallback
streaming
pagination
timeline
conversation
memory
agent
MCP
OpenTelemetry
dashboard
metrics API
```

---

# 四、开始前必须重新阅读

先阅读真实代码：

```text
backend/app/llm/client.py
backend/app/llm/provider.py
backend/app/llm/deepseek_provider.py

backend/app/services/llm_usage_persistence_service.py
backend/app/db/llm_usage_repository.py
backend/app/db/models/llm_usage_record.py

backend/app/api/orchestrator_chat.py
backend/app/api/assistant_trace.py

backend/app/services/assistant_trace_query_service.py

tests/test_llm_usage*
tests/test_assistant_trace*
```

重点确认 Step 55 的审计结论仍然准确。

不要假设接口没有变化。

---

# 五、生产接线位置

Step 55 推荐：

```text
llm/client.py :: get_default_llm_client()
```

作为唯一生产默认接线点。

优先实现：

```text
get_default_llm_client()
        ↓
DatabaseLLMAccountingSink(...)
        ↓
create_llm_client(..., accounting_sink=sink)
```

要求：

### 1. 不修改 `create_llm_client()` 默认行为

仍然允许：

```python
create_llm_client(settings.llm)
```

得到：

```text
NoopAccountingSink
```

保持既有测试和其他调用方兼容。

---

### 2. 只改变生产默认 Client

目标：

```text
get_default_llm_client()
```

使用：

```text
DatabaseLLMAccountingSink
```

而不是：

```text
NoopAccountingSink
```

---

# 六、DatabaseLLMAccountingSink 生命周期

必须确认：

```text
process
  ↓
get_default_llm_client()
  ↓
DatabaseLLMAccountingSink
```

原则：

> 默认 Client 是进程级单例，因此 accounting sink 也不要每个 request 创建。

禁止：

```text
每次 HTTP request
    ↓
new DatabaseLLMAccountingSink
```

禁止：

```text
每次 LLM call
    ↓
new DatabaseLLMAccountingSink
```

应该保持：

```text
process
    ↓
default LLM client
    ↓
one accounting sink
```

---

# 七、Database Session

继续复用现有：

```text
DatabaseLLMAccountingSink
    ↓
LLMUsagePersistenceService
    ↓
LLMUsageRepository
    ↓
with factory() as session
with session.begin()
```

不要改成：

```text
global Session
```

不要创建：

```text
long-lived transaction
```

不要改变 Repository 的 transaction contract。

---

# 八、最重要：DB 不可用不能影响 LLM

这是本阶段最高优先级测试。

模拟：

```text
DatabaseLLMAccountingSink
        ↓
Database failure
```

要求：

```text
LLM business result = SUCCESS
```

同时：

```text
accounting = warning / isolated failure
```

不得：

```text
LLM request fail
HTTP 500
HTTP 502
TextToSQL retry
RAG retry
Tool retry
```

必须保持 Step 55 已确认的：

```text
Observability / Accounting failure
        ↓
does not become business failure
```

---

# 九、不能增加 Retry

尤其注意：

```text
DB accounting failure
```

不能触发：

```text
LLM retry
```

不能：

```text
sleep
backoff
queue
fallback
```

本阶段完全不实现 retry。

---

# 十、assistant_request_id

必须保持：

```text
Provider request_id
```

与：

```text
Assistant Trace ID
```

严格分离。

生产链路：

```text
AIOrchestrator
    ↓
assistant_request_id = A
    ↓
assistant_trace_scope(A)
    ↓
LLMClient
    ↓
DatabaseLLMAccountingSink
    ↓
current_assistant_request_id()
    ↓
LLMUsageRecord.assistant_request_id = A
```

不要在 sink 内生成新的 assistant request ID。

不要覆盖：

```text
LLMUsageRecord.request_id
```

`request_id` 继续保存 Provider request ID。

---

# 十一、一次 LLM Call 只产生一次 Usage Record

必须验证：

```text
LLMClient.chat()
        ↓
_emit_accounting()
        ↓
DatabaseLLMAccountingSink
        ↓
one repository create
```

禁止：

```text
LLMClient → accounting
DeepSeekProvider → accounting
RagService → accounting
TextToSQLService → accounting
ToolChatService → accounting
```

业务层不得重复记账。

---

# 十二、Success Path

增加真实 DB-gated E2E：

使用：

```text
Fake / MockTransport LLM
```

不要调用真实 DeepSeek。

流程：

```text
POST /api/ai/chat
        ↓
production default LLM client
        ↓
DatabaseLLMAccountingSink
        ↓
PostgreSQL
```

验证：

```text
HTTP = 200
```

并且：

```text
ai_ops.llm_usage_record
```

新增：

```text
1 row
```

其中：

```text
assistant_request_id = API response metadata.request_id
```

同时：

```text
request_id = provider request id
```

不能相同推断。

必须实际检查。

---

# 十三、Assistant Trace E2E

在同一个测试中：

### Step 1

调用：

```text
POST /api/ai/chat
```

获取：

```text
assistant_request_id = A
```

---

### Step 2

调用：

```text
GET /api/observability/assistant-trace/{A}
```

验证：

```text
200
```

并且：

```text
llm_usage.length >= 1
```

---

### Step 3

验证：

```text
llm_usage[].assistant_request_id
```

如果 HTTP DTO 当前没有该字段：

**不要为了测试修改 API contract。**

通过数据库或当前 QueryService 能力验证关联关系。

---

# 十四、RAG / Tool / Text-to-SQL

至少验证：

## RAG

```text
/api/ai/chat
→ RAG
→ LLM
→ usage record
→ assistant trace
```

## Tool

如果当前 `/api/ai/chat` Tool 路径会产生 LLM：

验证：

```text
Tool path
→ LLM
→ exactly one usage record per actual LLM call
```

不要修改 Tool Observability。

---

## Text-to-SQL

验证：

```text
Text-to-SQL
→ LLM
→ usage
```

如果发生：

```text
semantic retry
```

则允许：

```text
2+ actual LLM calls
→ 2+ usage records
```

这是正确行为。

关键是：

> 每一个实际 LLM request 对应最多一条 usage record。

---

# 十五、Refusal

必须回归：

```text
destructive request
        ↓
refusal
```

验证：

```text
attempts = 1
LLM calls = 1
usage rows = 1
Validator = 0
Executor = 0
```

不要让 production accounting 接线改变 refusal 行为。

---

# 十六、Failure Path

使用 MockTransport / Fake Client 模拟：

```text
timeout
429
500
invalid response
```

验证：

### LLM business failure

保持当前已有异常语义。

同时：

```text
usage row = 0
```

如果当前设计是：

```text
usage=None
```

则不要新增失败 usage record。

---

# 十七、Accounting Failure

专门测试：

```text
LLM succeeds
        ↓
DatabaseLLMAccountingSink fails
```

验证：

```text
LLM result still succeeds
```

以及：

```text
HTTP 仍然成功
```

同时：

```text
no LLM retry
```

---

# 十八、Default Client Singleton

增加测试：

```text
client1 = get_default_llm_client()
client2 = get_default_llm_client()
```

确认：

```text
client1 is client2
```

并确认：

```text
accounting sink
```

没有每次重新创建。

如果当前结构无法直接访问 sink：

通过行为测试证明即可。

---

# 十九、Existing Explicit Client Compatibility

必须确认：

```text
create_llm_client(settings.llm)
```

仍然：

```text
NoopAccountingSink
```

或者保持当前既有行为。

不要因为生产 wiring 改动而影响：

```text
unit tests
mock clients
explicit client creation
```

---

# 二十、Security

继续保持 Step 55 安全边界。

Usage Record 不得出现：

```text
prompt
messages
system prompt
tool definitions
SQL
RAG chunks
tool arguments
ToolResult.data
raw response
API key
authorization
password
database URL
headers
traceback
```

测试至少检查：

```text
ORM columns
serialized usage response
Assistant Trace response
```

---

# 二十一、数据库测试数据

只能使用：

```text
测试数据库
```

不得使用真实 WMS 数据。

测试结束后：

```text
cleanup
```

必须保证：

```text
test residue = 0
```

至少检查：

```text
ai_ops.llm_usage_record
```

如果测试还触发：

```text
tool_execution_record
rag_execution_record
```

也必须清理。

---

# 二十二、禁止修改 DB Schema

本阶段：

```text
NO MIGRATION
NO NEW TABLE
NO NEW COLUMN
NO NEW INDEX
```

Step 52 已经完成：

```text
assistant_request_id B-tree index
```

不要重复处理。

---

# 二十三、测试命令

先运行专项：

```powershell
python -m pytest -q tests/test_llm_usage_production_wiring.py
```

然后 DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_llm_usage_production_wiring_e2e_db.py
```

然后：

```powershell
python -m pytest -q
```

再：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

最后：

```powershell
python -m compileall -q backend tests scripts
```

如果项目 lint 仍未安装：

记录：

```text
lint unavailable
```

不要临时引入新的 lint 工具。

---

# 二十四、Git Diff

完成后执行：

```powershell
git status --short
git diff --stat
git diff
```

确认：

```text
生产 wiring = only intended change
```

不得出现：

```text
Prompt 修改
TextToSQL 修改
Validator 修改
Executor 修改
Router 修改
RAG Core 修改
Tool Core 修改
API contract 修改
DB schema 修改
```

---

# 二十五、Evaluation Document

新增：

```text
docs/evaluation/phase-3.12-step-56-llm-usage-production-wiring.md
```

记录：

## 1. Before

```text
Default Client
    ↓
NoopAccountingSink
    ↓
No DB usage record
```

## 2. After

```text
Default Client
    ↓
DatabaseLLMAccountingSink
    ↓
LLMUsageRepository
    ↓
ai_ops.llm_usage_record
```

## 3. Correlation

```text
Assistant Trace ID
    ↓
assistant_request_id
```

以及：

```text
Provider request_id
    ↓
request_id
```

明确两者不是同一个 ID。

## 4. E2E

记录：

```text
/api/ai/chat
→ usage record
→ Assistant Trace
```

## 5. Failure isolation

记录：

```text
DB accounting failure
→ LLM business result unchanged
```

## 6. Security

记录不持久化敏感字段。

## 7. Tests

记录测试结果。

---

# 二十六、完成标准

必须全部满足：

```text
[ ] Production default Client 使用 DatabaseLLMAccountingSink
[ ] create_llm_client() 默认行为未改变
[ ] Default Client 仍是 singleton
[ ] 一次 LLM call → 一条 usage record
[ ] assistant_request_id 正确关联 Assistant Trace
[ ] provider request_id 保持独立
[ ] RAG usage 正常
[ ] Tool usage 正常
[ ] Text-to-SQL usage 正常
[ ] Semantic retry 的每次真实 LLM call 都有独立 usage
[ ] Refusal usage 正常
[ ] LLM failure 不产生错误 usage row
[ ] Accounting failure 不影响 LLM business result
[ ] Accounting failure 不触发 retry
[ ] No secrets persisted
[ ] DB residue = 0
[ ] Full pytest PASS
[ ] DB pytest PASS
[ ] compileall PASS
```

---

# 二十七、最终汇报格式

完成后严格只报告：

```text
Phase 3.12 Step 56 COMPLETE

1. Production Wiring
2. Modified Files
3. Added Files
4. Default Client
5. Accounting Sink
6. Assistant Request Correlation
7. One LLM Call → One Usage Record
8. RAG
9. Tool
10. Text-to-SQL
11. Refusal
12. Failure Isolation
13. Security
14. Tests
15. DB Tests
16. DB Residue
17. compileall
18. Git Diff
19. Current Limitations
```

最后：

```text
Production LLM Usage Persistence = YES

Assistant Trace LLM Usage = YES

Accounting Failure Isolation = YES

Phase 3.12 Step 56 STOP
```

**完成后立即停止。**

不要进入 Step 57。

不要做 Pagination。

不要做 Unified Timeline。

不要做 Conversation。

不要做 Memory。

不要做 Agent。

不要做 MCP。

不要做 OpenTelemetry。

不要做 Dashboard。

不要做 Cost Tracking。
