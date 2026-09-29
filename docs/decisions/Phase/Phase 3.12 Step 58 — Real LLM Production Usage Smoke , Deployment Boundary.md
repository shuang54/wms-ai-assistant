# Phase 3.12 Step 58 — Real LLM Production Usage Smoke / Deployment Boundary

你现在开始执行：

`D:\coding\ai\wms-ai-assistant`

---

# 一、阶段目标

Phase 3.12 Step 56 已完成生产 LLM Usage 接线。

Phase 3.12 Step 57 已完成：

```text
Session
Transaction
Connection Pool
DB Failure Isolation
Idempotency
Singleton
Security
```

本阶段只做：

> **真实 DeepSeek + Production Default LLM Client + DatabaseLLMAccountingSink 的一次真实 Smoke 验证。**

目标：

```text
Real DeepSeek
      ↓
Production Default LLMClient
      ↓
DatabaseLLMAccountingSink
      ↓
ai_ops.llm_usage_record
      ↓
Assistant Trace
```

重点证明：

> Step 56 的 production wiring 不只是 MockTransport 下成立，而是真实 OpenAI-compatible HTTP 请求也能够完成 Usage Persistence 与 Assistant Trace correlation。

---

# 二、严格范围

允许：

```text
tests/
scripts/
docs/evaluation/
```

必要时允许极小的：

```text
docs/architecture.md
```

生产代码原则：

```text
Production code changes = 0
```

如果真实 Smoke 发现生产代码 bug：

**立即停止并报告。**

不要为了让 Smoke 通过修改生产代码。

---

# 三、禁止事项

禁止修改：

```text
LLMClient
LLMProvider
DeepSeekProvider
TextToSQLService
RagService
ToolChatService
AIRouterService
AIOrchestratorService
LLMUsageRepository
AssistantTrace
RAG Observability
Tool Observability
Prompt
```

禁止新增：

```text
API
database table
database column
database index
migration
runtime config
retry
fallback
queue
background worker
pagination
timeline
conversation
memory
agent
MCP
OpenTelemetry
dashboard
cost tracking
```

禁止：

```text
生产代码优化
连接池优化
真实 WMS 数据
生产数据库
压力测试
benchmark
```

---

# 四、首先确认真实 DeepSeek 配置

阅读：

```text
backend/app/core/config.py
backend/app/llm/client.py
backend/app/llm/deepseek_provider.py
```

确认当前真实配置来源：

```text
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
model
```

不要打印：

```text
API Key
Authorization
完整 headers
```

只允许输出：

```text
provider
model
base_url hostname
```

API Key 必须保持 masked。

例如：

```text
api_key = configured
```

不要显示具体值。

---

# 五、真实 Smoke 必须显式 Opt-in

非常重要：

默认：

```text
pytest
```

不得调用真实 DeepSeek。

不要让：

```powershell
python -m pytest -q
```

产生真实 LLM 请求。

Smoke 必须明确 opt-in。

如果项目已有：

```text
@pytest.mark.real_llm
```

优先复用。

如果已有 real LLM smoke runner：

优先复用。

不要重新创建第二套机制。

---

# 六、如果已有 Real LLM Smoke

先搜索：

```text
real_llm
DeepSeek
DEEPSEEK_API_KEY
MockTransport
httpx
@pytest.mark
```

如果已有可靠 smoke：

直接扩展最小测试：

```text
production usage persistence
```

不要重复创建 Client。

---

# 七、如果没有可靠 Smoke

可以新增：

```text
tests/test_llm_usage_real_llm_smoke.py
```

但必须满足：

默认：

```text
SKIPPED
```

只有显式环境变量才运行，例如：

```text
RUN_REAL_LLM_TESTS=1
```

不要新增项目运行时配置。

这里的：

```text
RUN_REAL_LLM_TESTS
```

只能作为：

**测试启动开关**

不能进入 production settings。

---

# 八、真实 Smoke 测试内容

只做：

```text
1 个真实 LLM request
```

不要：

```text
10 requests
20 requests
100 requests
```

问题必须非常简单。

例如：

```text
请只回答：OK
```

不要让模型：

```text
查询数据库
生成 SQL
调用 Tool
检索知识库
```

本 Smoke 只验证：

```text
LLM transport
+
usage accounting
+
assistant_request_id correlation
```

---

# 九、不要通过真实业务 Router

优先选择最短真实 production Client 路径。

但是必须使用：

```text
get_default_llm_client()
```

不能自己：

```text
new OpenAICompatibleClient()
```

不能：

```text
create_llm_client(..., NoopAccountingSink())
```

必须真正走：

```text
get_default_llm_client()
```

从而验证：

```text
DatabaseLLMAccountingSink
```

确实被生产默认 Client 使用。

---

# 十、Assistant Request Scope

如果直接测试 Client：

必须使用现有：

```text
assistant_trace_scope()
```

建立：

```text
assistant_request_id = A
```

然后：

```text
client.chat()
```

最终：

```text
llm_usage_record.assistant_request_id = A
```

不要自己修改 sink。

---

# 十一、Provider Request ID

真实 DeepSeek 返回的：

```text
request_id
```

如果当前 DeepSeek/OpenAI-compatible response 有：

```text
response metadata request_id
```

必须检查：

```text
llm_usage_record.request_id
```

是否保存该 Provider request ID。

不要生成：

```text
uuid
```

代替真实 Provider request ID。

如果真实 Provider 当前没有提供 request_id：

允许：

```text
request_id = None
```

但必须记录：

```text
real provider did not expose request_id
```

不要伪造。

---

# 十二、Usage 验证

真实 Smoke 成功后查询：

```text
ai_ops.llm_usage_record
```

找到：

```text
assistant_request_id = A
```

必须存在：

```text
exactly 1 row
```

验证：

```text
provider
model
prompt_tokens
completion_tokens
total_tokens
created_at
assistant_request_id
```

均符合当前真实 response。

如果真实 Provider 没有某个 usage 字段：

按照当前 `LLMUsage` Contract 保持：

```text
None
```

不要人为填充。

---

# 十三、Assistant Trace 验证

如果测试环境能够构造真实 `/api/ai/chat`：

优先：

```text
POST /api/ai/chat
```

使用真实 DeepSeek。

获取：

```text
assistant_request_id = A
```

然后：

```text
GET /api/observability/assistant-trace/{A}
```

验证：

```text
200
```

并：

```text
llm_usage >= 1
```

如果无法安全通过 HTTP API 运行真实 DeepSeek：

可以：

```text
get_default_llm_client()
+
assistant_trace_scope()
+
LLMUsageQueryService
```

验证 correlation。

不要为了 Smoke 强行修改 API。

---

# 十四、真实 DB

本 Smoke 可以使用：

```text
开发/测试 PostgreSQL
```

但绝对禁止：

```text
生产数据库
```

禁止使用真实 WMS 业务数据。

只允许写入：

```text
1 条 synthetic LLM usage record
```

测试结束必须精确删除：

```text
assistant_request_id = A
```

如果 provider request_id 有固定前缀：

同时增加：

```text
provider = expected provider
```

等安全条件。

禁止：

```text
TRUNCATE
```

禁止：

```text
DELETE entire table
```

---

# 十五、Smoke 前后 Row Count

记录：

```text
before_count
```

Smoke：

```text
1 real LLM call
```

之后：

```text
after_count
```

理论：

```text
after_count = before_count + 1
```

然后 cleanup：

```text
final_count = before_count
```

必须：

```text
residue = 0
```

---

# 十六、Failure Handling

真实 Smoke 可能因为：

```text
API key
network
DeepSeek unavailable
rate limit
timeout
```

失败。

必须区分：

### Environment failure

例如：

```text
API Key missing
network unavailable
```

记录：

```text
SMOKE SKIPPED / ENVIRONMENT UNAVAILABLE
```

不要修改代码。

### Application failure

例如：

```text
LLM succeeded
but usage persistence failed
```

记录：

```text
SMOKE FAILED
```

并停止。

不要修改 production code。

---

# 十七、绝对不能把 API Key 写入报告

报告允许：

```text
DEEPSEEK_API_KEY = configured
```

不允许：

```text
sk-xxxxx
```

不允许：

```text
Authorization: Bearer ...
```

不允许：

```text
完整 .env
```

---

# 十八、不要修改真实 LLM Prompt

本 Smoke 不允许修改：

```text
production prompt
```

只允许使用：

```text
最小测试问题
```

---

# 十九、真实 Smoke 与默认测试隔离

默认：

```text
python -m pytest -q
```

必须：

```text
real_llm = 0
network = 0
```

只有：

```powershell
$env:RUN_REAL_LLM_TESTS="1"; python -m pytest -q tests/test_llm_usage_real_llm_smoke.py -m real_llm
```

才允许：

```text
real_llm = 1
network > 0
```

如果项目已有不同命令：

遵循现有机制。

---

# 二十、测试分类

至少区分：

```text
Offline tests
DB-gated tests
Real-LLM smoke
```

不要混在一起。

例如：

```text
Offline:
4129+ passed

DB:
4580+ passed

Real LLM:
1 passed
```

或者：

```text
Real LLM:
SKIPPED — environment unavailable
```

---

# 二十一、Security

真实 Smoke 后检查：

```text
ai_ops.llm_usage_record
```

不得出现：

```text
prompt
messages
system prompt
raw response
authorization
API key
password
DATABASE_URL
headers
SQL
RAG content
Tool arguments
```

只检查当前 schema/columns。

---

# 二十二、编译与 Regression

完成后：

```powershell
python -m pytest -q
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

最后：

```powershell
python -m compileall -q backend tests scripts
```

Real LLM Smoke：

```powershell
$env:RUN_REAL_LLM_TESTS="1"; python -m pytest -q tests/test_llm_usage_real_llm_smoke.py -m real_llm
```

注意：

Real LLM Smoke 不得成为默认 pytest 的一部分。

---

# 二十三、Git Diff

执行：

```powershell
git status --short
git diff --stat
git diff
```

确认：

```text
Production code changes = 0
```

如果需要新增测试：

只允许：

```text
tests/
docs/evaluation/
```

不要出现：

```text
backend/app/
```

修改。

---

# 二十四、Evaluation Document

新增：

```text
docs/evaluation/phase-3.12-step-58-real-llm-usage-smoke.md
```

记录：

## 1. Environment

```text
provider
model
base_url hostname
DB environment
```

不要记录 secrets。

## 2. Smoke Request

记录：

```text
question = minimal smoke question
```

## 3. Production Client

确认：

```text
get_default_llm_client()
DatabaseLLMAccountingSink
```

## 4. Provider Request

记录：

```text
provider request_id
```

如果存在。

## 5. Usage Persistence

记录：

```text
before
after
persisted row
cleanup
```

## 6. Assistant Correlation

记录：

```text
assistant_request_id
provider request_id
```

明确：

```text
assistant_request_id != provider request_id
```

如果真实 Provider request_id 存在。

## 7. Assistant Trace

记录：

```text
GET /api/observability/assistant-trace/{A}
```

结果。

## 8. Security

记录安全检查。

## 9. Limitations

说明：

```text
real DeepSeek only tested once
not load tested
not production DB
```

---

# 二十五、完成标准

必须满足：

```text
[ ] Production default client used
[ ] DatabaseLLMAccountingSink used
[ ] Real DeepSeek request succeeded
[ ] Exactly 1 usage record persisted
[ ] assistant_request_id correct
[ ] provider request_id correct if exposed
[ ] IDs remain distinct
[ ] Assistant Trace can read usage
[ ] No secrets persisted
[ ] Cleanup residue = 0
[ ] Offline pytest passes
[ ] DB pytest passes
[ ] compileall passes
[ ] Real smoke isolated from default pytest
[ ] Production code changes = 0
```

如果真实 DeepSeek 环境不可用：

允许最终：

```text
Real Smoke = SKIPPED
Reason = environment unavailable
```

但不能伪造 PASS。

---

# 二十六、最终报告

严格：

```text
Phase 3.12 Step 58 COMPLETE

1. Real LLM Environment
2. Production Default Client
3. DatabaseLLMAccountingSink
4. Real DeepSeek Request
5. Provider Request ID
6. Assistant Request ID
7. Usage Persistence
8. Assistant Trace
9. Security
10. Cleanup / DB Residue
11. Offline Tests
12. DB Tests
13. Real LLM Smoke
14. compileall
15. Git Diff
16. Current Limitations
```

最后：

```text
Real LLM Production Usage Smoke = PASS / SKIPPED

Production Code Changes = 0

LLM Usage Persistence = VERIFIED / NOT VERIFIED

Phase 3.12 Step 58 STOP
```

**完成后立即停止。**

不要进入 Step 59。

不要做 Pagination。

不要做 Unified Timeline。

不要做 Conversation。

不要做 Memory。

不要做 Agent。

不要做 MCP。

不要做 OpenTelemetry。

不要做 Dashboard。

不要做 Cost Tracking。
