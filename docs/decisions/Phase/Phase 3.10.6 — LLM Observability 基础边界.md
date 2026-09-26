# Phase 3.10.6 — LLM Observability 基础边界

## 一、目标

建立 AI Core 内部统一的：

> LLM Call Observability Contract

解决：

```text
一次 LLM 调用发生了什么？
```

能够被统一描述：

```text
provider
model
latency
success / failure
finish_reason
usage
request_id
```

本阶段只建立**内部 DTO + 生命周期边界 + 测试**。

不建立完整 Observability 系统。

---

# 二、开始前必须阅读

先阅读实际代码，不要假设结构：

```text
backend/app/llm/client.py
backend/app/llm/provider.py
backend/app/llm/deepseek_provider.py
backend/app/llm/retry.py
backend/app/llm/structured.py

tests/test_llm_response.py
tests/test_llm_provider.py
tests/test_llm_structured.py

backend/app/services/text_to_sql_service.py
backend/app/services/rag_service.py
backend/app/services/tool_chat_service.py
backend/app/services/ai_router_service.py

docs/architecture.md
```

同时搜索：

```text
LLMResponse
LLMRequestError
LLMConfigError
LLMResponseError
generate(
chat(
time
latency
logging
```

必须以当前代码为准。

---

# 三、本阶段核心架构

建立：

```text
LLM Request
     │
     ▼
LLM Provider
     │
     ▼
LLMResponse / Exception
     │
     ▼
LLM Observation
```

最终形成：

```text
                    ┌────────────────────┐
                    │    LLMProvider     │
                    └─────────┬──────────┘
                              │
                       LLM call starts
                              │
                              ▼
                       LLM call finishes
                              │
                  ┌───────────┴───────────┐
                  │                       │
                success                 failure
                  │                       │
                  ▼                       ▼
            LLMResponse              Exception
                  │                       │
                  └───────────┬───────────┘
                              ▼
                      LLMObservation
```

---

# 四、新增内部 DTO

建议新增：

```text
backend/app/llm/observability.py
```

如果实际代码结构更适合其他位置，可以根据现有架构调整，但不要无理由扩大范围。

定义：

```text
LLMObservation
```

建议字段：

```text
provider: str | None
model: str | None

latency_ms: float | None

success: bool

finish_reason: str | None

usage: LLMUsage | None

request_id: str | None
```

可以根据当前代码实际情况增加必要字段，但不要提前设计完整 tracing schema。

---

# 五、字段语义

## 5.1 provider

来自：

```text
LLMResponse.metadata["provider"]
```

或者当前实际 Provider Contract 中已经确定的 provider 来源。

不能重新猜测。

---

## 5.2 model

优先来自：

```text
LLMResponse.model
```

不是配置中的 model。

失败情况下，如果没有 response：

```text
model = None
```

不要强行填配置值。

---

## 5.3 latency_ms

表示：

> 本次 LLM 调用从开始到结束所经过的时间。

单位：

```text
milliseconds
```

必须：

```text
>= 0
```

不允许：

```text
negative
NaN
Infinity
```

如果当前调用边界无法可靠获取 latency：

```text
None
```

不要伪造。

---

# 六、success

定义：

```text
success = True
```

仅表示：

> Provider 成功返回一个符合当前 LLMResponse Contract 的结果。

异常：

```text
LLMRequestError
LLMConfigError
LLMResponseError
LLMToolCallFormatError
LLMStructuredOutputError
```

等如何分类必须根据当前代码实际异常边界判断。

不要在本阶段重新设计异常体系。

---

# 七、failure observation

失败调用也应该能够产生 Observation。

例如：

```text
LLMObservation(
    provider="deepseek",
    model=None,
    latency_ms=1234.5,
    success=False,
    finish_reason=None,
    usage=None,
    request_id=None,
)
```

但是：

**绝对不要把异常 message 原文直接塞入 Observation。**

尤其禁止：

```text
api_key
authorization
password
database_url
prompt
messages
raw response
headers
```

---

# 八、Exception 类型

如果当前异常体系能够安全提供类型信息，可以记录：

```text
error_type: str | None
```

例如：

```text
LLMRequestError
LLMConfigError
LLMResponseError
```

但：

**不要记录完整 exception message。**

不要记录 stack trace。

不要记录 raw provider response。

如果加入 `error_type`：

```text
type(exc).__name__
```

即可。

---

# 九、usage

直接复用：

```text
LLMUsage
```

禁止重新创建 token 字段。

即：

```text
LLMObservation.usage
    ↓
LLMUsage
```

保持：

```text
prompt_tokens
completion_tokens
total_tokens
```

一致。

---

# 十、request_id

直接复用：

```text
LLMResponse.metadata["request_id"]
```

没有则：

```text
None
```

绝对禁止：

```text
uuid.uuid4()
```

伪造 Provider request ID。

---

# 十一、finish_reason

成功：

```text
LLMResponse.finish_reason
```

失败：

```text
None
```

未知值：

```text
原样保留
```

不要重新建立枚举。

---

# 十二、Observation 不得包含 Prompt

这是非常重要的安全边界。

禁止：

```text
messages
system_prompt
user_prompt
tool definitions
SQL
RAG chunks
```

进入 `LLMObservation`。

本阶段只记录：

> 调用元数据，而不是调用内容。

---

# 十三、不要记录 Cost

即使已经有：

```text
prompt_tokens
completion_tokens
```

也不要加入：

```text
input_price
output_price
cost
currency
total_cost
```

因为：

```text
usage != cost
```

Cost Tracking 留到独立阶段。

---

# 十四、不要建立 Logger Framework

本阶段：

**不要重构 logging。**

不要新增：

```text
structlog
loguru
OpenTelemetry
Langfuse
Prometheus
Grafana
Sentry
```

不要新增依赖。

Observation 只是：

> 内部结构化数据对象。

暂时不规定它最终写到哪里。

---

# 十五、不要持久化

本阶段禁止：

```text
DB table
Redis
file
SQLite
PostgreSQL
Kafka
```

保存 Observation。

不要新增 migration。

不要增加数据库写入。

---

# 十六、Observation 生命周期

建议提供一个非常小的内部 helper，例如：

```text
start_observation()
finish_observation()
```

或者：

```text
observe_llm_call(...)
```

具体实现必须根据当前 Client / Provider 架构决定。

不要为了一个 DTO 创建复杂 framework。

目标：

```text
call starts
    ↓
record start time
    ↓
Provider call
    ↓
record response / exception
    ↓
calculate latency
    ↓
create LLMObservation
```

---

# 十七、关键原则：不要改变 LLM 行为

Observation 必须是：

```text
side-effect-free
```

或者至少不能影响 LLM 主流程。

例如：

```text
Observation 创建失败
```

不能导致：

```text
LLM request failed
```

也不能导致：

```text
retry
fallback
HTTP 500
```

原则：

> Observability failure must never become business failure.

---

# 十八、Retry 边界

严格保持 Phase 3.10.2：

```text
Transport retry:
无自动 retry

Business retry:
TextToSQL validator retry
```

Observation 不允许增加：

```text
retry
sleep
backoff
fallback
```

如果同一个业务调用产生多个实际 LLM requests，未来可以分别观察。

本阶段不要改变现有 retry 行为。

---

# 十九、测试

新增：

```text
tests/test_llm_observability.py
```

至少覆盖：

### Test 1

成功调用：

```text
success=True
```

### Test 2

失败调用：

```text
success=False
```

### Test 3

latency：

```text
0
1
100.5
```

合法。

### Test 4

非法 latency：

```text
-1
NaN
Infinity
```

拒绝。

### Test 5

model：

来自：

```text
LLMResponse.model
```

不是 config model。

### Test 6

provider：

来自现有 metadata/provider Contract。

### Test 7

usage：

复用：

```text
LLMUsage
```

不是复制字段。

### Test 8

request_id：

来自真实 response metadata。

不存在：

```text
None
```

不生成 UUID。

### Test 9

finish_reason：

保持原始值。

### Test 10

failure：

失败 Observation：

```text
success=False
```

并且：

```text
usage=None
request_id=None
```

如果实际 response 信息不存在。

### Test 11

error_type：

如果实现了：

```text
LLMRequestError
```

得到：

```text
"LLMRequestError"
```

不得包含 exception message。

### Test 12

secret isolation：

构造包含：

```text
api_key
authorization
password
headers
prompt
messages
raw_response
database_url
```

的异常/response。

确认 Observation 中完全不存在。

### Test 13

cost isolation：

确认：

```text
cost
price
currency
```

不存在。

### Test 14

DB isolation：

Observation 创建过程中：

```text
DB calls = 0
```

### Test 15

network isolation：

Observation 本身：

```text
network calls = 0
```

### Test 16

observability failure isolation：

模拟 Observation 构造/记录异常。

确认不能改变原 LLM response / business result。

---

# 二十、与现有业务回归

必须验证：

```text
Text-to-SQL
RAG
Tool Calling
Router
AI API
Project Context
Refusal
Structured Response
```

特别检查：

```text
Refusal:
attempts = 1
validator = 0
executor = 0
LLM calls = 1
```

不要因为加入 Observation 导致 refusal 流程发生变化。

---

# 二十一、业务代码修改边界

原则：

```text
TextToSQLService        不改
RagService              不改
ToolChatService         不改
AIRouterService         不改
AIOrchestrator          不改
Validator               不改
Executor                不改
Prompt                  不改
Project Context         不改
```

如果 Observation 必须挂在 LLM Client 才能准确取得 latency：

可以在：

```text
backend/app/llm/client.py
```

做最小修改。

不要把 Observation 逻辑散落到业务服务。

---

# 二十二、Documentation

在：

```text
docs/architecture.md
```

新增：

```text
§8.6 LLM Observability Contract
```

只说明：

1. Observation 是内部 DTO
2. 记录调用元数据
3. 不记录 Prompt / SQL / RAG 内容
4. 不记录 secrets
5. usage 与 cost 分离
6. Observation failure 不影响业务
7. 当前不持久化
8. 当前不接 OpenTelemetry / Prometheus / Langfuse

保持简洁。

---

# 二十三、严格禁止

本阶段禁止进入：

```text
Cost Tracking
Streaming
Fallback
Model Router
Multi-provider routing
Prompt optimization
OpenTelemetry
Prometheus
Grafana
Langfuse
Sentry
Tracing
Metrics exporter
DB persistence
Redis
Kafka
Billing
```

也不要提前设计完整的 Metrics API。

---

# 二十四、测试命令

默认：

```powershell
python -m pytest -q
```

然后：

```powershell
python -m compileall backend tests scripts
```

如果项目支持：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

执行完整 regression。

如果出现真实外部 embedding API 网络抖动：

1. 记录失败
2. 单独重跑
3. 确认是否为外部问题
4. 不修改代码绕过测试

---

# 二十五、Git Diff

完成后：

```powershell
git status --short
git diff --stat
git diff
```

确认：

```text
没有 Prompt 修改
没有 Validator 修改
没有 Executor 修改
没有 Router 策略修改
没有 Project Context 修改
没有历史 ground truth 修改
没有无关依赖
没有数据库 migration
没有 secrets
```

---

# 二十六、完成报告

最终只输出：

```text
Phase 3.10.6 完成报告

1. 修改文件

2. LLMObservation Contract

3. Success / Failure Contract

4. Latency Contract

5. Usage / Model / Provider / Request ID

6. Security Boundary

7. Retry Boundary

8. Observability Failure Isolation

9. Tests

10. Regression

11. Network / DB

12. Production Path

13. Git Diff

14. 未完成项

Phase 3.10.6 READY

Phase 3.10.6 STOP
```

如果存在任何代码问题：

```text
Phase 3.10.6 NOT READY
```

不要自动进入下一阶段。

# 二十七、最终原则

本阶段完成后，架构应该具备：

```text
LLMResponse
    │
    ├── business result
    │
    └── metadata
            │
            ▼
      LLMObservation
            │
            ├── provider
            ├── model
            ├── latency
            ├── success
            ├── finish_reason
            ├── usage
            └── request_id
```

但是：

```text
Observation
    ≠
Logging System

Observation
    ≠
Metrics System

Observation
    ≠
Tracing System

Usage
    ≠
Cost
```

**完成本阶段后立即 STOP。**
