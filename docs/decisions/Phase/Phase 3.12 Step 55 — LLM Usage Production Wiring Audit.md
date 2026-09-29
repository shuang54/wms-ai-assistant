你现在开始执行：

# Phase 3.12 Step 55 — LLM Usage Production Wiring Audit

## 一、阶段目标

本阶段只审计一个已经在 Step 54 中确认的现状：

> **当前生产默认 LLM Client 是否真正接入 DatabaseLLMAccountingSink，导致 Assistant Trace 的 `llm_usage[]` 在生产环境可能为空。**

Step 54 已发现：

```text
get_default_llm_client()
    ↓
默认 Noop Accounting Sink
    ↓
LLM Usage 不持久化
    ↓
Assistant Trace
    ↓
llm_usage[] = []
```

本阶段只回答：

```text
1. 当前 LLM Usage persistence 在生产路径是否真的接线？
2. 哪个 composition root 创建默认 LLM Client？
3. 为什么默认使用 Noop？
4. 如果生产需要持久化，正确接线点在哪里？
5. 是否存在重复记录 / 重复 sink / 双写风险？
6. 接线是否会影响现有 LLM 调用行为？
```

**只做 Audit / Design。**

本阶段禁止直接修改生产代码。

---

# 二、严格范围

允许：

```text
阅读源码
全仓搜索
调用现有只读测试
检查依赖关系
检查 composition root
检查 LLM accounting sink
检查 Assistant Trace
新增 Audit 文档
必要时新增极少量 contract test
```

禁止：

```text
修改 LLM Client
修改 LLM Provider
修改 DatabaseLLMAccountingSink
修改 Usage Repository
修改 Usage QueryService
修改 Assistant Trace API
修改 RAG
修改 Tool Observability
修改 Router
修改 Orchestrator
新增 API
新增 DB 表
新增 Index
新增 migration
真实 DeepSeek 调用
生产数据库写入
```

尤其：

**不要因为发现 Noop Sink 就直接修改 `get_default_llm_client()`。**

先完成审计。

---

# 三、开始前必须阅读

先阅读：

```text
backend/app/llm/client.py
backend/app/llm/provider.py
backend/app/llm/deepseek_provider.py
```

继续阅读：

```text
backend/app/services/llm_usage*
backend/app/db/llm_usage_repository.py
backend/app/db/models/llm_usage_record.py
```

搜索：

```text
DatabaseLLMAccountingSink
Noop
LLMAccountingSink
get_default_llm_client
get_llm_client
LLMClient(
emit_accounting
LLMUsageRecord
```

然后检查：

```text
backend/app/main.py
backend/app/api/
backend/app/services/
```

确定：

```text
默认 LLM Client 在哪里创建？
谁负责 dependency injection？
谁创建 Database Session / Repository？
```

---

# 四、建立真实调用链

必须画出当前真实 production path：

```text
HTTP
 ↓
API
 ↓
Service
 ↓
LLM Provider
 ↓
LLM Client
 ↓
Accounting Sink
 ↓
?
```

特别标明：

```text
NoopAccountingSink
```

或者：

```text
DatabaseLLMAccountingSink
```

到底在哪一步被选择。

不要根据设计文档猜。

以源码为准。

---

# 五、检查 get_default_llm_client()

重点审计：

```text
get_default_llm_client()
```

必须回答：

```text
1. 是否 singleton？
2. 是否每次调用重新创建？
3. 是否缓存？
4. Provider 是否复用？
5. Accounting Sink 是在哪里传入？
6. DatabaseLLMAccountingSink 是否被引用？
7. Noop sink 是默认值还是 fallback？
8. 是否依赖 RUN_DB_TESTS？
9. 是否依赖 environment？
10. production path 是否和 test path 不同？
```

如果：

```text
DatabaseLLMAccountingSink
```

只出现在：

```text
tests/
```

必须明确报告。

---

# 六、检查 Accounting Sink 生命周期

阅读：

```text
LLMAccountingSink
DatabaseLLMAccountingSink
NoopLLMAccountingSink
```

确认：

```text
LLM call
 ↓
emit accounting
 ↓
sink.record(...)
```

然后回答：

```text
Database sink 使用什么 Repository？
Repository 使用什么 Session？
Session 生命周期谁负责？
```

重点寻找：

```text
session leak
connection leak
transaction leak
commit responsibility
rollback responsibility
```

本阶段只审计，不修改。

---

# 七、检查数据库写入边界

确认：

```text
LLM call
 ↓
DatabaseLLMAccountingSink
 ↓
LLMUsageRepository
 ↓
INSERT ai_ops.llm_usage_record
```

是否存在：

```text
double insert
```

例如：

```text
LLMClient emit
+
Provider emit
```

导致一次 LLM Call 写两条记录。

必须确认：

```text
one underlying LLM request
→
one usage record
```

如果当前已经有测试证明：

```text
one call = one record
```

复用现有测试。

不要重新实现。

---

# 八、检查 Assistant Trace Correlation

继续确认：

```text
assistant_request_id
```

的来源。

当前应该是：

```text
AIOrchestrator
    ↓
assistant_request_id = A
    ↓
LLM execution context
    ↓
DatabaseLLMAccountingSink
    ↓
llm_usage_record.assistant_request_id = A
```

确认：

```text
provider request_id
```

仍然独立保存。

不得混淆：

```text
assistant_request_id
vs
provider request_id
```

---

# 九、检查 RAG / Tool 是否重复计算

特别检查：

```text
RAG
Tool
TextToSQL
```

是否各自又调用：

```text
LLMAccountingSink
```

导致：

```text
same LLM request
→
multiple usage records
```

目标确认：

```text
Accounting belongs to actual LLM Client call
```

而不是：

```text
business service
```

重复记账。

---

# 十、检查失败路径

审计：

```text
LLM success
LLM timeout
LLM 429
LLM 5xx
LLM config error
LLM response error
```

确认 accounting 行为。

重点回答：

```text
失败调用是否会记录 usage？
```

如果没有 response：

```text
usage = None
```

是否仍记录一次 failure observation / accounting？

不要修改行为。

只记录当前事实。

---

# 十一、检查 Accounting Failure Isolation

必须确认：

```text
LLM request
 ↓
LLM response
 ↓
accounting sink
 ↓
DB failure
```

不会导致：

```text
LLM business result
→ failure
```

也不能：

```text
DB accounting failure
→ retry LLM
```

继续保持之前的可靠性边界：

```text
Observability/accounting failure
≠
LLM business failure
```

如果当前代码已有 best-effort 行为：

记录并复用。

---

# 十二、检查生产路径与测试路径差异

建立表：

| Environment | LLM Client | Accounting Sink | DB Persistence |
| ----------- | ---------- | --------------- | -------------- |
| Unit Test   | ?          | ?               | ?              |
| DB Test     | ?          | ?               | ?              |
| Default App | ?          | ?               | ?              |
| Production  | ?          | ?               | ?              |

如果 Production 和 Default App 没有独立 configuration：

明确：

```text
Production follows default application path.
```

不要假设部署环境。

---

# 十三、检查是否存在正确的接线点

本阶段不要修改。

只找出：

> 如果未来要把 DatabaseLLMAccountingSink 正确接入生产，最小、最自然的 composition root 是哪里？

候选：

```text
main.py
app factory
LLM client factory
service composition root
```

必须根据真实代码选择。

禁止：

```text
在 TextToSQLService 里注入
在 RagService 里注入
在 ToolChatService 里注入
在 Router 里注入
```

Accounting 属于：

```text
LLM infrastructure / composition root
```

---

# 十四、评估数据库 Session 设计

如果未来 production 使用：

```text
DatabaseLLMAccountingSink
```

必须确认它是否需要：

```text
request-scoped session
```

还是：

```text
每次 record 创建独立 session
```

分析：

```text
LLM request latency
DB insert latency
connection pool
transaction
```

但：

**不要进行性能优化。**

只记录设计风险。

---

# 十五、禁止为了接线新增运行时配置

本阶段：

```text
不要新增：
LLM_USAGE_ENABLED
LLM_ACCOUNTING_ENABLED
```

也不要新增：

```text
USE_DB_ACCOUNTING
ENABLE_USAGE_PERSISTENCE
```

先判断当前架构是否已有配置入口。

如果未来需要配置：

只记录：

```text
potential future configuration
```

不要实现。

---

# 十六、Security Audit

确认：

```text
DatabaseLLMAccountingSink
```

不会写入：

```text
prompt
messages
API key
authorization
password
database URL
raw response
SQL
RAG chunks
tool arguments
```

允许：

```text
provider
model
token usage
created_at
request_id
assistant_request_id
```

以当前真实 DTO / ORM 为准。

不要扩大字段。

---

# 十七、Tests

本阶段优先复用已有测试。

运行：

```powershell
python -m pytest -q tests/test_llm_usage*
```

如果 wildcard 不可靠，使用实际文件名。

继续：

```powershell
python -m pytest -q tests/test_assistant_trace*
```

然后：

```powershell
python -m compileall -q backend tests scripts
```

如果成本可接受：

```powershell
python -m pytest -q
```

---

# 十八、DB Writes

默认：

```text
DB writes = 0
```

本阶段 Audit 不允许新增数据。

如果运行已有 DB-gated 测试：

必须确认：

```text
test writes
→ cleanup
→ residue = 0
```

不要运行生产数据库。

---

# 十九、Audit 文档

新增：

```text
docs/evaluation/phase-3.12-step-55-llm-usage-production-wiring-audit.md
```

至少包含：

```text
1. Audit Scope
2. Current LLM Call Path
3. get_default_llm_client()
4. Accounting Sink Selection
5. Sink Lifecycle
6. Database Write Boundary
7. One Call → One Usage Record
8. Assistant Request Correlation
9. RAG / Tool / Text-to-SQL Interaction
10. Failure Path
11. Accounting Failure Isolation
12. Environment Matrix
13. Production Wiring Point
14. Session / Transaction Analysis
15. Security Boundary
16. Recommendation
17. Limitations
```

---

# 二十、Recommendation

最终必须给出：

```text
Production LLM Usage Persistence:
READY / NOT READY / DESIGN READY
```

注意：

如果发现：

```text
DatabaseLLMAccountingSink
```

已经存在，但默认 production path 使用 Noop：

不要直接判断“代码有 bug”。

可能是：

```text
production wiring 尚未完成
```

应区分：

```text
Implementation exists
vs
Production wiring exists
```

如果正确的最小接线点已经明确，但本阶段禁止修改：

可以写：

```text
DESIGN READY
```

并明确：

```text
No production code changed.
```

---

# 二十一、禁止事项

本阶段禁止：

```text
❌ 修改 get_default_llm_client()
❌ 修改 DatabaseLLMAccountingSink
❌ 修改 LLMUsageRepository
❌ 修改 Assistant Trace
❌ 修改 LLM Usage API
❌ 修改 RAG
❌ 修改 Tool
❌ 修改 TextToSQL
❌ 新增 API
❌ 新增 DB 表
❌ 新增 Index
❌ 新增 runtime config
❌ DeepSeek
❌ 生产 DB
❌ migration
❌ Pagination
❌ Unified Timeline
```

尤其：

> 本阶段只确认生产接线是否缺失，以及未来最小接线点在哪里。

不要实际完成 production wiring。

---

# 二十二、Git Diff

完成后执行：

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
LLM behavior = unchanged
Prompt = unchanged
```

允许：

```text
新增 Audit 文档
```

其他修改必须解释。

---

# 二十三、最终报告

严格输出：

```text
Phase 3.12 Step 55 完成汇报

1. Audit Scope
2. Current LLM Call Path
3. get_default_llm_client()
4. Accounting Sink
5. Sink Lifecycle
6. DB Write Boundary
7. One LLM Call → One Usage Record
8. Assistant Request Correlation
9. RAG / Tool / Text-to-SQL
10. Failure Path
11. Accounting Failure Isolation
12. Environment Matrix
13. Production Wiring Point
14. Session / Transaction
15. Security
16. Recommendation
17. Tests
18. compileall
19. DB writes
20. Git Diff
21. Current Limitations
```

最后：

```text
Phase 3.12 Step 55 STOP
```

---

# 二十四、强制 STOP

完成 Step 55 后立即停止。

不要进入：

```text
Step 56
Production wiring implementation
Pagination
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
Phase 3.12 Step 55 完成报告
```

等待下一步指令。
