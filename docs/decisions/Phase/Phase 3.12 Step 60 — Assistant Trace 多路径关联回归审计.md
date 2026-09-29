你现在开始执行：

# Phase 3.12 Step 60 — Assistant Trace 多路径关联回归审计

## 一、阶段目标

本阶段只做：

> **Assistant Trace 在三条 AI 业务路径下的关联完整性回归审计。**

验证：

```text
/api/ai/chat
     │
     ├── RAG
     │     ├── LLM Usage
     │     └── RAG Execution
     │
     ├── TOOL
     │     └── Tool Execution
     │
     └── TEXT_TO_SQL
           ├── LLM Usage
           └── SQL semantic retry
```

最终确认：

```text
一个 assistant_request_id
        ↓
只能关联本次请求产生的 Observability records
```

并且：

```text
RAG request
    ≠ Tool request
    ≠ Text-to-SQL request
```

不存在跨请求污染。

---

# 二、重要：本阶段只审计，不扩展架构

允许：

```text
新增测试
新增 evaluation 文档
必要的测试 fixture
```

禁止修改：

```text
AIOrchestrator
AIRouter
RagService
ToolChatService
TextToSQLService
LLMClient
LLMProvider
SQLValidator
SQLExecutor
ProjectContext
Observability DTO
Observability Repository
Assistant Trace HTTP contract
数据库 Schema
索引
Prompt
```

如果发现真实 production bug：

**停止修改并报告根因。**

不要为了通过测试修改生产逻辑。

---

# 三、开始前必须阅读

先阅读真实实现：

```text
backend/app/api/orchestrator_chat.py

backend/app/services/ai_orchestrator_service.py
backend/app/services/ai_router_service.py
backend/app/services/rag_service.py
backend/app/services/tool_chat_service.py
backend/app/services/text_to_sql_service.py

backend/app/services/assistant_trace_service.py

backend/app/services/llm_usage_query_service.py
backend/app/services/tool_execution_query_service.py
backend/app/services/rag_execution_query_service.py
```

以及：

```text
tests/
docs/evaluation/
```

重点寻找已有：

```text
RAG E2E
Tool E2E
Text-to-SQL E2E
Assistant Trace E2E
LLM Usage correlation
Tool correlation
RAG correlation
Refusal regression
```

优先复用已有 Fake / fixture。

不要重新造 Fake。

---

# 四、测试入口必须统一

三条路径全部必须从：

```text
/api/ai/chat
```

或者：

```text
AIOrchestratorService.execute()
```

进入。

不要直接调用：

```text
RagService
ToolService
TextToSQLService
```

作为最终 E2E 验证入口。

允许底层 service 在测试内部作为 dependency / spy / fake。

---

# 五、Path A：RAG

构造一个确定能够走 RAG 的请求。

优先复用当前已有 RAG E2E fixture。

验证：

```text
HTTP 200
route = RAG
metadata.request_id = A
```

然后查询：

```text
GET /api/observability/assistant-trace/{A}
```

确认：

```text
assistant_request_id = A

llm_usage:
    1 条（如果本路径调用 LLM）

rag_executions:
    1 条

tool_executions:
    0 条
```

具体 LLM 数量必须根据当前真实 RAG 路径确认。

不要猜。

---

# 六、Path B：Tool

构造当前已有的安全 Tool 测试请求。

验证：

```text
HTTP 200
route = TOOL
metadata.request_id = B
```

Assistant Trace：

```text
assistant_request_id = B

tool_executions:
    1 条

rag_executions:
    0 条

llm_usage:
    根据当前真实 Tool 路径确认
```

特别确认：

```text
ToolExecutionRecord.request_id == B
```

不是：

```text
provider request_id
```

也不是另一个 Assistant Request ID。

---

# 七、Path C：Text-to-SQL

使用现有 Fake LLM / Fake Generator，避免真实 DeepSeek。

构造一个能够稳定进入 Text-to-SQL 的请求。

验证：

```text
route = TEXT_TO_SQL
metadata.request_id = C
```

如果当前语义 retry 会产生多次 LLM calls：

验证：

```text
llm_usage:
    N 条
```

其中：

```text
N
```

必须与当前实际 retry 行为一致。

不要修改 retry。

验证：

```text
tool_executions = 0
rag_executions = 0
```

---

# 八、三请求 Cross-Request Isolation

这是本阶段最重要的测试。

依次执行：

```text
Request A → RAG
Request B → Tool
Request C → Text-to-SQL
```

得到：

```text
A
B
C
```

分别查询：

```text
Trace(A)
Trace(B)
Trace(C)
```

必须：

```text
Trace(A)
    只包含 A 的 records

Trace(B)
    只包含 B 的 records

Trace(C)
    只包含 C 的 records
```

严格断言：

```text
A records 中不存在 B
A records 中不存在 C

B records 中不存在 A
B records 中不存在 C

C records 中不存在 A
C records 中不存在 B
```

---

# 九、Provider Request ID 与 Assistant Request ID

再次明确验证两种 ID：

```text
assistant_request_id
provider request_id
```

必须：

```text
assistant_request_id != provider request_id
```

并且：

```text
Assistant Trace
    ↓
assistant_request_id
    ↓
LLM Usage
    ↓
assistant_request_id
```

而：

```text
LLM Usage.request_id
```

继续保持：

```text
真实 Provider request_id
```

不能互相覆盖。

---

# 十、RAG request_id

确认 RAG Execution Record：

```text
request_id == assistant_request_id
```

例如：

```text
RAG Execution
    request_id = A
```

必须能被：

```text
Assistant Trace(A)
```

读取。

不要修改 RAG schema。

---

# 十一、Tool request_id

确认 Tool Execution Record：

```text
request_id == assistant_request_id
```

能够：

```text
Assistant Trace(B)
```

正确读取。

特别注意：

如果 Tool 内部有其他 request_id：

不要把它错误当成 Assistant Trace ID。

以当前真实 contract 为准。

---

# 十二、Text-to-SQL 多 LLM Call

如果当前：

```text
attempt 1
→ Validator reject
→ attempt 2
```

则：

```text
LLM Usage
```

应该出现：

```text
2 records
```

每条记录：

```text
assistant_request_id = C
```

但：

```text
provider request_id
```

可以不同。

这是正常情况。

不要把多个 provider request_id 合并。

---

# 十三、Empty Trace

验证一个没有：

```text
LLM
Tool
RAG
```

记录的请求。

例如当前项目已有 empty / refusal case。

查询：

```text
Trace(D)
```

确认：

```text
200

assistant_request_id = D

llm_usage = []
tool_executions = []
rag_executions = []
```

如果 refusal 已经规定：

```text
llm_calls = 1
```

则按照当前真实 refusal contract 验证。

不要人为改变 refusal 行为。

---

# 十四、Failure Trace

使用当前已有 failure fixture。

验证：

```text
下游失败
    ↓
HTTP error
```

同时确认：

```text
Trace
```

不会：

```text
错误地返回成功
```

也不会：

```text
把其他 request 的 record
```

挂到当前 trace。

特别检查：

```text
RAG failure
Tool failure
LLM failure
```

已有测试能复用则不要重复实现。

---

# 十五、Trace Security

三条路径全部确认：

```text
LLM Usage
```

不包含：

```text
prompt
messages
raw response
API key
Authorization
```

Tool 不包含：

```text
arguments
ToolResult.data
SQL
stack trace
```

RAG 不包含：

```text
query
answer
chunk content
embedding
similarity
```

不要因为做聚合 Trace 而扩大 payload。

---

# 十六、测试设计

优先新增一个：

```text
tests/test_assistant_trace_multi_path_e2e.py
```

如果现有 E2E 测试结构更合适，则遵循现有结构。

建议至少覆盖：

```text
1. RAG trace correlation
2. Tool trace correlation
3. Text-to-SQL trace correlation
4. Cross-request isolation
5. Provider vs assistant request id
6. Empty trace
7. Failure trace
8. Security payload
```

全部尽可能复用现有 fixture。

---

# 十七、数据库策略

默认：

```text
RUN_DB_TESTS=0
```

不得依赖真实数据库。

如果需要验证持久化关联：

使用：

```text
RUN_DB_TESTS=1
```

并使用当前测试数据库。

禁止：

```text
生产数据库
真实 WMS 数据
TRUNCATE
全表 DELETE
```

测试结束：

```text
DB residue = 0
```

如果测试使用 transaction rollback：

优先复用现有机制。

---

# 十八、真实 LLM

本阶段默认：

```text
Real DeepSeek = OFF
```

不要因为验证 Trace 而重新跑真实 LLM。

如果已有 Step 58 smoke：

保持：

```text
RUN_REAL_LLM_TEST
```

独立。

不要把 Step 60 测试绑定到真实 LLM。

---

# 十九、运行测试

先：

```powershell
python -m pytest -q tests/test_assistant_trace_multi_path_e2e.py
```

然后：

```powershell
python -m pytest -q
```

DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

如果项目 lint 不可用：

```text
lint = unavailable
```

不要安装新工具。

---

# 二十、Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff
```

必须确认：

```text
Production code changes = 0
API contract changes = 0
DB schema changes = 0
Index changes = 0
Prompt changes = 0
```

如果存在 production code modification：

**停止并报告。**

---

# 二十一、Evaluation 文档

新增：

```text
docs/evaluation/phase-3.12-step-60-assistant-trace-multi-path.md
```

记录：

## 1. Scope

```text
Multi-path correlation audit
No production implementation
```

## 2. RAG

```text
request_id
LLM usage
RAG execution
Tool = 0
```

## 3. Tool

```text
request_id
Tool execution
RAG = 0
```

## 4. Text-to-SQL

```text
request_id
LLM usage count
Tool = 0
RAG = 0
```

## 5. Cross-request isolation

```text
A
B
C
```

分别记录：

```text
no contamination
```

## 6. Empty / Failure

记录实际结果。

## 7. Security

记录 payload boundary。

## 8. Final

```text
RAG correlation = PASS
Tool correlation = PASS
Text-to-SQL correlation = PASS
Cross-request isolation = PASS
Security = PASS
```

---

# 二十二、如果发现问题

不要修。

先报告：

```text
Issue:
Layer:
Impact:
Expected:
Actual:
Production code involved:
```

例如：

```text
Issue: RAG trace request_id mismatch
Layer: RagExecutionPersistenceAdapter
Impact: Trace cannot read RAG record
Expected: request_id == assistant_request_id
Actual: ...
```

然后立即 STOP。

---

# 二十三、最终报告

严格按照：

```text
Phase 3.12 Step 60 完成报告

1. Audit Scope
2. RAG Correlation
3. Tool Correlation
4. Text-to-SQL Correlation
5. Cross-request Isolation
6. Provider / Assistant Request ID
7. Empty Trace
8. Failure Trace
9. Security
10. Tests
11. DB Tests
12. compileall
13. Git Diff
14. Production Code Changes
15. DB Schema Changes
16. Current Limitations

Phase 3.12 Step 60 STOP
```

---

# 二十四、强制 STOP

完成后：

**立即停止。**

不要：

```text
Pagination
Cursor
Unified Timeline
Conversation
Memory
Agent
MCP
OpenTelemetry
Dashboard
Cost Tracking
```

不要修改生产代码。

等待下一步指令。
