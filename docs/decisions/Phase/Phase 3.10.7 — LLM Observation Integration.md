# Phase 3.10.7 — LLM Observation Integration

## 一、目标

将 Phase 3.10.6 已建立的：

```text
LLMObservation
build_llm_observation_safe()
```

接入实际 LLM Client 调用生命周期。

最终实现：

```text
LLM call
   │
   ├── start timer
   │
   ▼
Provider / Client
   │
   ├── success → LLMResponse
   │
   └── failure → Exception
   │
   ▼
LLMObservation
```

本阶段只解决：

> **一次真实 LLM 调用能够产生一个可靠的 Observation。**

不解决 Observation 的存储、发送、查询、展示。

---

# 二、开始前必须阅读

先阅读实际代码：

```text
backend/app/llm/client.py
backend/app/llm/provider.py
backend/app/llm/deepseek_provider.py
backend/app/llm/observability.py
backend/app/llm/retry.py
backend/app/llm/structured.py

tests/test_llm_observability.py
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
LLMObservation
build_llm_observation_safe
LLMResponse
LLMRequestError
LLMConfigError
LLMResponseError
generate(
chat(
```

**必须先理解当前 Client / Provider 调用链，再决定具体接入点。**

不要假设 `client.py` 一定是唯一调用入口。

---

# 三、核心架构

目标：

```text
                    Business Services
                           │
                           ▼
                    LLMProvider
                           │
                           ▼
              OpenAICompatibleClient
                           │
                 ┌─────────┴─────────┐
                 │                   │
              start                finish
                 │                   │
                 ▼                   ▼
             LLM call          Response/Error
                 │                   │
                 └─────────┬─────────┘
                           ▼
                 build_llm_observation_safe
                           │
                           ▼
                   LLMObservation
```

Observation 是旁路信息：

```text
LLM result ───────────────→ Business
      │
      └───────────────────→ Observation
```

不得变成：

```text
LLM result → Observation → Business
```

也就是说：

> Observation 不能成为业务调用成功的前置条件。

---

# 四、Observation 的获取方式

先检查 3.10.6 的实际实现。

如果当前已有合适的 helper：

```text
build_llm_observation_safe(...)
```

直接复用。

不要重新创建第二套 Observation builder。

如果 helper 当前是纯函数：

```text
response + exception + started_at
        ↓
LLMObservation
```

继续保持纯函数。

---

# 五、关键问题：Observation 如何暴露

本阶段必须解决：

> Client 产生了 Observation，调用方如何获得它？

但是**不能破坏已有 API Contract**。

当前已有：

```text
generate() → str

chat(messages) → str

chat(messages, tools=...) → LLMResponse
```

这些返回值必须保持不变。

因此：

### 禁止

```text
generate() → tuple[str, LLMObservation]
```

禁止：

```text
chat() → {
    response: ...,
    observation: ...
}
```

禁止把 Observation 塞入：

```text
LLMResponse.metadata
```

因为 3.10.5 已经确定 metadata 白名单。

---

# 六、推荐的最小暴露方案

根据当前代码选择最小侵入方式。

优先考虑：

```text
LLMClient / OpenAICompatibleClient
        │
        ├── existing public return value
        │
        └── last observation / callback / sink
```

但必须先检查当前架构。

如果使用 callback/sink：

```text
ObservationSink
```

也只能做最小 Contract。

不要建立复杂 event bus。

如果使用：

```text
get_last_observation()
```

必须考虑并发安全。

**不要使用简单实例字段保存全局 last observation，除非当前 Client 生命周期和并发模型明确保证安全。**

---

# 七、并发安全

必须重点检查：

```text
一个 Client 实例
多个并发 LLM request
```

不能发生：

```text
Request A
   ↓
observation = A

Request B
   ↓
observation = B

Request A 读取 observation
   ↓
错误得到 B
```

因此：

### 禁止

未经证明安全的：

```text
self.last_observation
```

作为并发请求的唯一存储。

如果需要让调用方获得 Observation，优先使用：

```text
request-scoped
callback
sink
context
```

等不会互相覆盖的方式。

但不要引入复杂 async context framework。

---

# 八、Observation 生命周期

对于一次成功调用：

```text
t0 = perf_counter()

response = actual_llm_call()

t1 = perf_counter()

observation = build_llm_observation_safe(
    response=response,
    started_at=t0,
)
```

得到：

```text
success=True
latency_ms >= 0
model=response.model
provider=response.metadata["provider"]
usage=response.usage
finish_reason=response.finish_reason
request_id=response.metadata["request_id"]
```

---

# 九、失败调用

对于：

```text
actual_llm_call()
```

抛出异常：

```text
t0
  ↓
LLM call
  ↓
Exception
  ↓
t1
  ↓
Observation
```

得到：

```text
success=False
latency_ms >= 0
error_type=type(exc).__name__
```

没有 response 时：

```text
model=None
finish_reason=None
usage=None
request_id=None
```

provider 必须遵守 3.10.6 的既定规则：

```text
metadata/provider
    >
caller explicit provider
    >
None
```

不要从配置猜测。

---

# 十、异常必须继续向上传播

非常重要：

Observation 接入后：

```text
LLM call raises LLMRequestError
```

仍然必须：

```text
LLMRequestError
    ↓
original caller
```

不能变成：

```text
LLMRequestError
    ↓
Observation
    ↓
return None
```

也不能：

```text
Observation failure
    ↓
replace original exception
```

正确结构：

```text
try:
    response = call()
except Exception as exc:
    observe_failure(...)
    raise
```

如果 Observation 本身失败：

```text
observe_failure(...)
```

不得覆盖原始 exception。

---

# 十一、成功结果必须原样返回

Observation 接入前：

```text
response = client.chat(...)
```

接入后：

```text
response = client.chat(...)
observation = ...
return response
```

response 必须保持：

```text
identity / type / fields
```

不被 Observation 修改。

尤其：

```text
LLMResponse
tool_calls
usage
metadata
content
```

都不能因为 Observation 被复制、重建或改变。

---

# 十二、generate() 特别处理

当前：

```text
generate() → str
```

必须继续保持。

如果内部实际调用：

```text
chat()
```

需要根据实际代码判断 Observation 是否会重复生成。

禁止：

```text
generate
  ↓
chat
  ↓ observation A
generate
  ↓ observation B
```

导致同一个实际 Provider request 被记录两次。

原则：

> **一次实际 LLM Provider 请求，对应最多一个 Observation。**

---

# 十三、Tool Calling

必须确认：

```text
chat(messages, tools=...)
```

仍然：

```text
LLMResponse
```

Observation 应该正确记录：

```text
model
finish_reason="tool_calls"
usage
request_id
latency
success=True
```

但不得修改：

```text
tool_calls
```

不得影响：

```text
LLMToolCallFormatError
```

的既有边界。

---

# 十四、Structured Response

如果 Structured Response 的调用链是：

```text
LLM
 ↓
LLMResponse.content
 ↓
parse_structured_response()
```

Observation 应该记录：

> LLM request 本身

而不是：

> Structured Parser 的执行时间

本阶段不把：

```text
JSON parsing latency
Pydantic validation latency
```

加入 LLM latency。

---

# 十五、Retry

这是本阶段最容易误改的地方。

保持 Phase 3.10.2：

```text
Transport retry:
无自动 retry

Business retry:
T2S validator retry
```

如果实际发生多个 Provider requests：

```text
Request 1 → Observation 1
Request 2 → Observation 2
```

这是允许的。

不要把多个实际 request 合并成一个假的 Observation。

不要新增 retry。

不要新增 fallback。

---

# 十六、Refusal

必须验证：

```text
Destructive request
    ↓
Refusal
```

当前：

```text
attempts=1
llm_calls=1
validator=0
executor=0
```

保持不变。

如果 Refusal LLM request 成功：

```text
Observation.success=True
```

这是正常的。

因为：

> refusal 是业务结果，不是 LLM 调用失败。

不要把 refusal 标记为：

```text
success=False
```

---

# 十七、Observation Sink

如果当前架构需要一个接收 Observation 的机制，可以定义最小接口：

```text
LLMObservationSink
```

例如概念上：

```text
record(observation: LLMObservation) -> None
```

要求：

```text
No-op sink
```

可以作为默认实现。

但是：

**只有当前架构确实需要时才添加。**

不要为了“未来扩展”提前建立：

```text
EventBus
EventManager
ObserverManager
TelemetryManager
```

等大型抽象。

---

# 十八、默认行为

生产默认情况下：

```text
LLMObservation
    ↓
可被获取/接收
    ↓
不持久化
    ↓
不发送外部服务
```

不要改变当前：

```text
.env
config
database
```

语义。

不要增加：

```text
OBSERVABILITY_ENABLED
```

等配置开关，除非当前实现确实需要。

本阶段不是设计 Feature Flag。

---

# 十九、测试

新增或扩展：

```text
tests/test_llm_observability.py
tests/test_llm_provider.py
tests/test_llm_response.py
```

至少覆盖：

### 1. Success observation

真实调用一次：

```text
observation.success is True
```

### 2. Latency

确认：

```text
latency_ms >= 0
```

### 3. Model

确认：

```text
observation.model == response.model
```

不是 config model。

### 4. Usage identity

确认：

```text
observation.usage is response.usage
```

如果 response 有 usage。

### 5. Metadata

确认：

```text
request_id
provider
```

来源正确。

### 6. Failure observation

Fake Provider 抛出：

```text
LLMRequestError
```

确认：

```text
observation.success is False
observation.error_type == "LLMRequestError"
```

同时：

```text
LLMRequestError
```

仍然继续向上抛出。

### 7. Original exception preservation

确认：

```text
raised_exception is original_exception
```

或等价语义。

### 8. Observation failure isolation

让 Observation builder/sink 故意失败。

确认：

成功调用仍然成功。

失败调用仍然抛出原始 exception。

### 9. Tool Calling

确认：

```text
tool_calls
finish_reason
```

不发生改变。

### 10. generate()

确认：

```text
generate() -> str
```

仍然成立。

且一次实际 LLM request 最多一个 Observation。

### 11. chat()

确认：

```text
chat(messages) -> str
chat(messages, tools=...) -> LLMResponse
```

不变。

### 12. Structured Response

确认：

```text
LLMResponse.content
    ↓
parse_structured_response
```

仍然通过。

### 13. Refusal

确认：

```text
attempts=1
validator=0
executor=0
llm_calls=1
```

### 14. Concurrent requests

至少构造两个并发/交错调用场景，确认：

```text
Request A → Observation A
Request B → Observation B
```

不会交叉。

如果当前实现无法安全支持 Observation 获取方式：

**不要通过共享 `last_observation` 强行实现。**

---

# 二十、禁止为了测试修改业务逻辑

测试应使用：

```text
Fake Provider
Fake Client
MockTransport
```

不要修改：

```text
TextToSQLService
RagService
ToolChatService
AIRouterService
AIOrchestrator
```

来适配测试。

---

# 二十一、Security

继续保持：

```text
Observation
    ≠
Prompt logging
```

禁止记录：

```text
messages
system_prompt
user_prompt
SQL
RAG chunks
tool definitions
API key
Authorization
headers
password
database URL
raw SDK response
```

Observation integration 不得扩大 3.10.6 的字段白名单。

---

# 二十二、Network / DB

默认单元测试：

```text
LLM network = 0
DB = 0
```

使用：

```text
MockTransport
Fake Provider
in-memory
```

DB regression 如果项目支持：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

不得新增数据库写入。

不得保存 Observation。

---

# 二十三、文档

修改：

```text
docs/architecture.md
```

增加：

```text
§8.7 LLM Observation Integration
```

简要描述：

```text
LLM call
    ↓
Observation lifecycle
    ↓
LLMObservation
```

明确：

1. 一次实际 Provider request 最多一个 Observation
2. success/failure 都可产生 Observation
3. Observation failure 不影响业务
4. 原有 return contract 不变
5. 不持久化
6. 不外发
7. 不做 cost calculation
8. 不做 tracing

不要提前写下一阶段架构。

---

# 二十四、严格禁止

本阶段禁止：

```text
Cost Tracking
Streaming
Fallback
Model Router
Multi-provider routing
OpenTelemetry
Prometheus
Langfuse
Grafana
Sentry
Kafka
Redis
DB persistence
Billing
Metrics exporter
Trace exporter
Prompt logging
```

尤其不要顺手把：

```text
LLMObservation
```

变成：

```text
logging system
```

---

# 二十五、测试命令

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

执行完整回归。

如果出现外部 embedding API 网络抖动：

1. 记录失败
2. 单独重跑
3. 判断是否外部问题
4. 不通过修改代码绕过测试

---

# 二十六、回归范围

必须验证：

```text
Phase 3.9.x
Phase 3.10.1
Phase 3.10.2
Phase 3.10.3
Phase 3.10.4
Phase 3.10.5
Phase 3.10.6

Text-to-SQL
RAG
Tool Calling
Router
AI API
Project Context
Refusal
Structured Response
```

特别确认：

```text
Refusal:
attempts=1
validator=0
executor=0
llm_calls=1
```

---

# 二十七、Git Diff

完成后：

```powershell
git status --short
git diff --stat
git diff
```

重点检查：

```text
client.py
provider.py
deepseek_provider.py
observability.py
tests
docs
```

确认：

```text
没有 Prompt 修改
没有 Validator 修改
没有 Executor 修改
没有 Router 策略修改
没有 Project Context 修改
没有历史 ground truth 修改
没有新依赖
没有 migration
没有 secrets
没有无关文件
```

---

# 二十八、完成报告

最终只输出：

```text
Phase 3.10.7 完成报告

1. 修改文件

2. Observation Integration Architecture

3. Success Observation

4. Failure Observation

5. Latency Measurement

6. Observation Exposure / Sink

7. Concurrency Safety

8. Retry / Refusal Boundary

9. Tool Calling / Structured Response

10. Tests

11. Regression

12. Network / DB

13. Production Path

14. Git Diff

15. 未完成项

Phase 3.10.7 READY

Phase 3.10.7 STOP
```

如果任何核心 Contract 或回归失败：

```text
Phase 3.10.7 NOT READY
```

不要宣布完成。

---

# 二十九、最终验收标准

只有同时满足以下条件才能 READY：

```text
□ 一次实际 LLM request 最多一个 Observation
□ Success request 有 Observation
□ Failure request 有 Observation
□ 原始 exception 正常继续抛出
□ Observation failure 不影响业务
□ latency 正确且 >= 0
□ model 来自实际 response
□ usage 复用 LLMUsage
□ provider/request_id 来源正确
□ Tool Calling 不变
□ generate() / chat() return contract 不变
□ Refusal 行为不变
□ 无共享 last_observation 并发污染
□ 无 Prompt / SQL / RAG 内容泄漏
□ 无 secrets 泄漏
□ 无 DB persistence
□ 无外部 telemetry
□ 无 cost calculation
□ 全量测试通过
```

**完成后立即 STOP，不自动进入 Phase 3.10.8。**
