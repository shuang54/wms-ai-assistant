你现在开始执行：

# Phase 3.12 Step 36 — LLM Usage Trace Correlation

## 一、阶段目标

在 Phase 3.12 Step 35 已完成：

```text
Assistant request_id
        ↓
/api/ai/chat metadata.request_id
        ↓
ToolExecutionRecord.request_id
```

的基础上，本阶段只解决一个问题：

> **让当前 LLM Usage Record 能够关联到同一个 Assistant request_id。**

最终形成：

```text
POST /api/ai/chat
        ↓
AIOrchestratorService.execute()
        ↓
Assistant request_id = A
        │
        ├── LLM Call
        │      ↓
        │   LLM Usage Record
        │      assistant_request_id = A
        │
        └── Tool Call
               ↓
            ToolExecutionRecord
               request_id = A
```

本阶段仍然不是 Conversation / Memory / Agent。

---

# 二、开始前必须阅读

先不要修改代码。

阅读当前真实实现：

```text
backend/app/services/ai_orchestrator_service.py

backend/app/services/
    llm*
    *llm*
    
backend/app/db/
    llm_usage_repository.py
    models/
        llm_usage_record.py

backend/app/api/
    orchestrator_chat.py
    usage*.py

tests/
```

重点确认：

1. 当前 LLM 调用入口在哪里。
2. 当前 LLM Usage Record 如何生成。
3. 当前 `LLMUsageRecord` 的真实字段。
4. 当前 `LLMUsageRepository` 如何保存。
5. 当前 Accounting Sink / Persistence Service 如何工作。
6. 当前 `provider_request_id` 的真实含义。
7. 当前 LLM Usage 是否已经存在类似 correlation 字段。
8. 当前 DeepSeek / OpenAI-compatible client 如何传递调用上下文。
9. `/api/usage/analytics` 是否依赖当前 DTO/schema。
10. 现有测试中的 Fake LLM / Fake Usage Sink 如何复用。

**优先复用现有 LLM Usage infrastructure。**

不要重新实现 Usage Persistence。

---

# 三、严格范围

## 允许

允许修改：

```text
AIOrchestrator → LLM Usage 调用链中必要的 correlation 代码
LLM Usage Record / Repository / Persistence DTO
相关 tests
相关 architecture/evaluation docs
```

如果现有数据库模型设计允许增加一个字段，可以增加：

```text
assistant_request_id
```

但必须先确认当前 Repository / Model 架构。

---

# 四、禁止

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
异步队列
重试系统
Trace Span
Trace Parent/Child
```

禁止：

```text
修改 AI Router
修改 RAG 核心逻辑
修改 ToolExecutionService
修改 ToolExecutionRecord
修改 Tool Observability
修改 Text-to-SQL
修改 SQL Validator
修改 SQL Executor
```

禁止：

```text
重新设计 request_id
```

Step 35 已经确定：

```python
AIOrchestratorService.execute()
    ↓
new_request_id()
```

仍然是唯一 Assistant Trace ID 来源。

---

# 五、两个 ID 必须严格区分

当前系统至少存在两类 ID：

## 1. Assistant request_id

表示：

```text
一次 /api/ai/chat 请求
```

来源：

```text
AIOrchestratorService.execute()
```

例如：

```text
A = 550e8400-e29b-41d4-a716-446655440000
```

---

## 2. provider_request_id

表示：

```text
LLM Provider / DeepSeek 的请求 ID
```

例如：

```text
chatcmpl-xxxx
```

或者当前项目实际 provider 返回值。

两者不能互相替代。

最终：

```text
LLMUsageRecord

assistant_request_id = A
provider_request_id  = P
```

必须同时存在。

---

# 六、第一步：先做字段审计

确认当前：

```text
LLMUsageRecord
```

实际结构。

如果已经存在类似：

```text
request_id
trace_id
correlation_id
```

不要重复新增。

判断：

```text
这个字段到底表示什么？
```

如果已有字段就是 Assistant request_id：

**直接复用。**

如果当前只有：

```text
provider_request_id
```

再考虑新增：

```text
assistant_request_id
```

---

# 七、推荐数据模型

如果确认需要新增字段：

```text
assistant_request_id
```

建议：

```text
String / VARCHAR
nullable = True
```

原因：

历史 Usage Record 可能来自：

```text
/api/chat
/api/rag/answer
/api/chat/with-tools
```

这些旧路径没有 Assistant Trace Contract。

因此：

**不能要求历史数据全部拥有 assistant_request_id。**

新链路：

```text
/api/ai/chat
```

应该写：

```text
assistant_request_id = current request_id
```

旧链路：

```text
assistant_request_id = NULL
```

---

# 八、不要修改 provider_request_id

绝对不要：

```text
provider_request_id = assistant_request_id
```

也不要：

```text
覆盖 provider request id
```

必须保持：

```text
assistant_request_id = Assistant Trace
provider_request_id = Provider Trace
```

这是两个不同维度。

---

# 九、Correlation 数据流

目标：

```text
AIOrchestrator.execute()
        │
        │ request_id = A
        ↓
LLM Call
        │
        ↓
LLM Usage Accounting
        │
        ↓
LLMUsageRecord
        │
        ├── assistant_request_id = A
        └── provider_request_id = P
```

不要让 API 层：

```text
API → 修改 Usage Record
```

也不要：

```text
API → 读取 Usage → 再补 request_id
```

Correlation 必须在：

```text
Orchestrator / LLM Usage application boundary
```

完成。

---

# 十、LLM Usage Persistence

继续复用当前：

```text
Execution
 ↓
Accounting Sink
 ↓
Persistence Service
 ↓
Repository
 ↓
PostgreSQL
```

不要重新建立：

```text
AssistantUsageRepository
```

不要重新建立：

```text
AssistantTraceRepository
```

本阶段只给现有 Usage Persistence 增加 correlation 信息。

---

# 十一、失败隔离

保持当前 Usage Observability 的原则：

如果：

```text
LLM Usage Persistence failure
```

不能影响：

```text
AIOrchestrator
LLM Answer
ToolResult
RAG Answer
Text-to-SQL Result
```

即：

```text
Usage persistence failure
        ↓
warning / existing error isolation
        ↓
Assistant request continues normally
```

不要新增 retry / queue。

---

# 十二、API Contract

### `/api/ai/chat`

保持 Step 35：

```json
{
  "route": "...",
  "content": "...",
  "data": {},
  "metadata": {
    "request_id": "..."
  }
}
```

不要新增：

```text
assistant_request_id
```

因为：

```text
metadata.request_id
```

已经是对客户端暴露的 Assistant Trace ID。

---

# 十三、Usage Analytics API

检查：

```text
GET /api/usage/analytics
```

本阶段：

**默认不要修改现有 response structure。**

尤其不要为了 correlation 直接把：

```text
assistant_request_id
```

加入 analytics aggregate response。

本阶段只保证：

```text
数据库 Usage Record
```

能够保存 correlation。

未来如果需要：

```text
GET /api/usage/by-request
```

另开阶段。

---

# 十四、数据库 Migration / init_db

先确认当前项目数据库 schema 管理方式。

如果当前项目使用：

```text
Base.metadata.create_all()
```

并且没有 Alembic：

按照现有项目测试/开发机制处理。

不要引入 Alembic。

不要新增 migration framework。

如果必须更新测试数据库：

使用当前项目既有初始化方式。

不要手工修改生产数据库。

---

# 十五、必须增加的测试

新增/修改测试至少覆盖：

## Test 1：Assistant request_id 进入 Usage Record

模拟：

```text
AIOrchestrator request_id = A
LLM provider_request_id = P
```

最终 Usage Record：

```text
assistant_request_id == A
provider_request_id == P
```

---

## Test 2：两个 ID 不相等

明确断言：

```text
assistant_request_id != provider_request_id
```

如果 Fake Provider 返回相同字符串，也不要依赖它。

测试数据必须显式使用不同 ID。

---

## Test 3：同一次 Assistant Request 的多次 LLM Call

如果当前链路允许一次 request 产生多个 LLM Usage Record：

例如：

```text
request_id = A

LLM Call 1 → provider P1
LLM Call 2 → provider P2
```

则：

```text
Usage 1 assistant_request_id = A
Usage 2 assistant_request_id = A
```

如果当前 `/api/ai/chat` 单次只产生一个 LLM Usage Record：

不要为了测试强行增加第二次调用。

只验证当前真实行为。

---

## Test 4：不同 Assistant Request 不串

```text
request A → Usage assistant_request_id = A

request B → Usage assistant_request_id = B
```

必须：

```text
A != B
```

不能出现：

```text
B → A
```

---

## Test 5：旧 Usage Record

模拟历史数据：

```text
assistant_request_id = NULL
```

读取 / analytics：

必须仍然正常。

不能因为新增字段导致历史数据读取失败。

---

## Test 6：Tool Correlation

真实 Tool E2E：

```text
/api/ai/chat
        ↓
request_id = A
        ↓
ToolExecutionRecord.request_id = A
        ↓
LLMUsageRecord.assistant_request_id = A
```

最终三个地方：

```text
Chat metadata.request_id
==
ToolExecutionRecord.request_id
==
LLMUsageRecord.assistant_request_id
```

---

## Test 7：RAG

验证：

```text
/api/ai/chat
route = RAG

metadata.request_id = A

LLMUsageRecord.assistant_request_id = A
```

RAG 不应该产生 Tool Record。

---

## Test 8：Text-to-SQL

验证：

```text
/api/ai/chat
route = TEXT_TO_SQL

metadata.request_id = A

LLMUsageRecord.assistant_request_id = A
```

不要新增 Tool Record。

---

# 十六、真实 DB 测试

如果当前 LLM Usage 已经有 DB fixture：

优先复用。

DB test 验证：

```text
Assistant Request
        ↓
LLM Usage persistence
        ↓
PostgreSQL
        ↓
assistant_request_id = expected
provider_request_id = expected
```

必须：

```text
DB writes = expected test writes only
DB residue = 0
```

测试结束清理测试数据。

---

# 十七、不要调用真实 DeepSeek

默认测试：

```text
0 DeepSeek
0 SiliconFlow
0 external LLM
```

使用：

```text
Fake LLM
Fake Accounting Sink
Fake Provider
```

如果项目已有真实 LLM smoke：

不要修改其默认行为。

---

# 十八、Security

`assistant_request_id` 本身可以持久化。

但：

```text
LLM prompt
LLM messages
Tool arguments
Tool result
SQL
API Key
Authorization
Database URL
password
```

仍然禁止进入 LLM Usage Record。

最终 Usage Record 只增加：

```text
assistant_request_id
```

不能因为 correlation 顺便保存 request payload。

---

# 十九、Architecture Test

新增：

```text
C41 — LLM Usage Trace Correlation
```

至少验证：

```text
C41.1 Assistant request_id → LLM Usage
C41.2 provider_request_id 保持独立
C41.3 API 不生成 request_id
C41.4 Orchestrator 是 Assistant Trace 唯一来源
C41.5 Tool Record 与 LLM Usage 可通过同一 request_id 关联
C41.6 RAG/T2S 不产生 Tool Record
C41.7 历史 Usage NULL correlation 仍兼容
C41.8 Usage persistence failure 不影响 Assistant execution
C41.9 不新增 prompt/tool args/sql/secrets
```

---

# 二十、文档

新增：

```text
docs/evaluation/Phase 3.12 Step 36 — LLM Usage Trace Correlation.md
```

至少记录：

```text
Current Problem
Trace ID definitions
Data Model
Correlation Flow
Backward Compatibility
Failure Isolation
Security
Tests
Limitations
```

Architecture 文档增加一个小节：

```text
Assistant Trace → LLM Usage → Tool Execution
```

不要大规模重写 architecture.md。

---

# 二十一、不要新增 HTTP API

本阶段：

```text
NO NEW ENDPOINT
```

现有：

```text
/api/ai/chat
/api/usage/analytics
/api/observability/tools
/api/observability/tools/history
/api/observability/tools/metrics/persistent
```

全部保持 endpoint 不变。

---

# 二十二、验证

执行：

```powershell
python -m pytest -q
```

如果 DB：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

编译：

```powershell
python -m compileall -q backend tests
```

LSP：

```text
0 error
0 warning
```

如果 lint 未安装：

```text
明确记录 unavailable
```

---

# 二十三、最终必须证明

最终应该能够证明：

```text
                    Assistant Trace
                         │
                   request_id = A
                         │
          ┌──────────────┴──────────────┐
          ↓                             ↓
   LLM Usage Record              Tool Execution Record
   assistant_request_id = A      request_id = A
   provider_request_id = P
```

并且：

```text
/api/ai/chat metadata.request_id
        ==
LLMUsageRecord.assistant_request_id
        ==
ToolExecutionRecord.request_id
```

在 Tool 路径成立。

RAG / Text-to-SQL：

```text
/api/ai/chat metadata.request_id
        ==
LLMUsageRecord.assistant_request_id
```

成立。

---

# 二十四、最终汇报格式

完成后只汇报：

```text
Phase 3.12 Step 36 COMPLETE

1. Trace Correlation
2. 修改文件
3. LLMUsageRecord
4. provider_request_id / assistant_request_id
5. Tool Correlation
6. RAG Correlation
7. Text-to-SQL Correlation
8. Backward Compatibility
9. Security
10. Tests
11. DB Tests
12. compile / LSP / lint
13. DB residue
14. API 是否变化
15. 新增字段
16. 当前限制
```

最后：

```text
Assistant Trace:

request_id
    │
    ├── LLMUsageRecord.assistant_request_id
    │       └── provider_request_id
    │
    └── ToolExecutionRecord.request_id
```

**完成 Phase 3.12 Step 36 后立即 STOP。**

不要进入 Step 37。

不要开发 Conversation。
不要开发 Memory。
不要开发 Agent。
不要开发 MCP。
不要开发 Streaming。
不要开发 Dashboard。
不要新增 HTTP API。
