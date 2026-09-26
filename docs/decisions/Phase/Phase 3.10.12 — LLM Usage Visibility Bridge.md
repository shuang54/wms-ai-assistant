# Phase 3.10.12 — LLM Usage Visibility Bridge

## 一、阶段目标

Phase 3.10.11 已经完成：

```text
LLMResponse / str
        ↓
LLMObservation
        ↓
AccountingSink
        ↓
LLMUsage | None
```

当前存在一个明确限制：

```text
LLMResponse 路径
    ↓
有 usage
    ↓
Accounting 可以获得 usage
```

但是：

```text
generate() -> str
chat(messages) -> str
        ↓
usage 不可见
        ↓
Observation.usage = None
        ↓
Accounting = None
```

本阶段只解决：

> **在不破坏现有 `generate() -> str` / `chat(messages) -> str` 返回契约的前提下，让 request lifecycle 可以获得 Provider 返回的 Usage。**

最终目标：

```text
Provider
   ↓
LLMResponse
   ├── content
   ├── usage
   ├── model
   └── finish_reason
        ↓
Existing compatibility boundary
        ↓
str return
        ↓
Observation / Accounting
```

注意：

**业务调用方仍然拿到 `str`。**

---

# 二、严格范围

## 允许

* 修改 `backend/app/llm/client.py`
* 修改 `backend/app/llm/provider.py`，仅在实际需要时
* 修改 `backend/app/llm/deepseek_provider.py`，仅在实际需要时
* 修改 `backend/app/llm/observability.py`，仅在实际需要时
* 新增/修改 tests
* 修改 `docs/architecture.md`

## 禁止

不要修改：

```text
RAG
Tool Framework
Router
Orchestrator
Text-to-SQL
Validator
Executor
Project Context
Business Semantic
Database Schema
```

禁止：

```text
Usage Database
Cost Database
Billing
Dashboard
Aggregation
Langfuse
OpenTelemetry
Redis
Kafka
```

禁止修改：

```text
LLMUsage
LLMObservation
LLMCost
LLMPricing
```

已有 DTO Contract 除非测试证明存在明确缺陷。

---

# 三、开始编码前必须阅读

必须先阅读：

```text
backend/app/llm/client.py
backend/app/llm/provider.py
backend/app/llm/deepseek_provider.py
backend/app/llm/observability.py
backend/app/llm/accounting.py

tests/test_llm_response.py
tests/test_llm_observability.py
tests/test_llm_observation_integration.py
tests/test_llm_accounting.py
tests/test_llm_accounting_integration.py
tests/test_llm_accounting_lifecycle.py

docs/architecture.md
```

重点确认：

```text
LLMResponse
LLMProvider.generate()
LLMProvider.chat()
LLMClient.generate()
LLMClient.chat()
_chat_impl()
_observe_call()
_emit_observation()
_emit_accounting()
```

还必须搜索：

```text
.generate(
.chat(
LLMClient(
create_llm_client(
```

确认现有调用方到底依赖什么。

---

# 四、核心原则

本阶段最重要的原则：

> **增加 Usage 可见性，但不改变业务返回值。**

例如现在：

```python
result = await client.chat(messages)
```

仍然必须：

```python
result == "hello"
```

不能变成：

```python
result == LLMResponse(...)
```

也不能变成：

```python
result == {
    "content": "...",
    "usage": ...
}
```

---

# 五、推荐架构

优先实现内部 request-scoped response：

```text
Provider
    ↓
LLMResponse
    ↓
Client compatibility layer
    ├── content → existing caller
    └── usage → observation/accounting
```

即：

```text
                    LLMResponse
                    /          \
                   /            \
             content             usage
                ↓                  ↓
          existing str       Observation
          contract               ↓
                             AccountingSink
```

业务调用方只看到：

```text
str
```

生命周期内部可以看到：

```text
LLMResponse
```

---

# 六、不要新增全局状态

禁止：

```python
client.last_response
client.last_usage
client.last_observation
global_usage
```

禁止任何：

```text
singleton mutable usage
```

必须保持：

```text
request-scoped
```

---

# 七、如果现有代码已经具备内部 Response

优先复用。

例如如果当前：

```text
_chat_impl()
```

内部已经能够得到：

```text
LLMResponse
```

但最终：

```text
return response.content
```

则：

**不要重写 Provider。**

只需要确保：

```text
response
   ↓
observation
   ↓
accounting
```

在返回 `content` 前完成。

---

# 八、如果现有 Provider API 只有 str

如果检查后发现：

```text
LLMProvider.generate() -> str
LLMProvider.chat() -> str
```

内部完全拿不到：

```text
usage
model
finish_reason
```

才考虑增加**内部兼容 API**。

例如概念：

```text
_internal_chat_response()
```

或者：

```text
_chat_impl_response()
```

具体名称必须依据当前代码选择。

要求：

```text
public existing API
        ↓
unchanged

internal response path
        ↓
LLMResponse
```

禁止直接修改所有业务调用方。

---

# 九、Public Contract 不变

必须验证：

```text
generate() -> str
chat(messages) -> str
chat(messages, tools=...) -> LLMResponse
```

如果当前真实 Contract 与上述不同：

**以实际代码为准，不要强行套用。**

本阶段不能破坏已有：

```text
Phase 2
Phase 3.6
Phase 3.10.x
```

的 API。

---

# 十、Usage Visibility

对于 Provider 返回：

```python
LLMResponse(
    content="hello",
    usage=LLMUsage(
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
    ),
)
```

验证：

```text
public result == "hello"
```

同时：

```text
Observation.usage is response.usage
```

并：

```text
Accounting == response.usage
```

即：

```text
1 request
→ 1 internal response
→ 1 observation
→ 1 accounting
```

---

# 十一、Model / Finish Reason

本阶段不仅验证 Usage。

如果 Provider response 有：

```text
model
finish_reason
```

必须继续保持 Observation Contract：

```text
observation.model == response.model
observation.finish_reason == response.finish_reason
```

不要从配置文件重新推断：

```text
model
```

也不要覆盖 Provider 返回值。

---

# 十二、Request ID

如果 Provider response：

```text
id
```

已经能够形成：

```text
metadata["request_id"]
```

必须保持现有规则。

禁止：

```text
uuid.uuid4()
```

伪造 request ID。

如果没有真实 request ID：

```text
None
```

---

# 十三、Generate() 路径

重点测试：

```text
generate()
```

Provider 返回完整：

```text
LLMResponse
```

内部：

```text
content
usage
model
finish_reason
```

最终：

```python
result = await client.generate(...)
```

仍然：

```text
result == content
```

同时 Accounting 能获得：

```text
LLMUsage
```

---

# 十四、No-tools Chat

测试：

```text
chat(messages)
```

如果当前 Contract 返回：

```text
str
```

仍然：

```text
str
```

但 lifecycle：

```text
Observation.usage != None
Accounting != None
```

这是本阶段最重要的验证之一。

---

# 十五、Tool Calling

工具调用已有：

```text
LLMResponse
```

路径。

确认不能因为本阶段修改而出现：

```text
tool_calls 丢失
```

验证：

```text
Tool call round
    ↓
usage #1
    ↓
Observation #1
    ↓
Accounting #1

Final round
    ↓
usage #2
    ↓
Observation #2
    ↓
Accounting #2
```

两次 request 独立。

---

# 十六、T2S

T2S 当前大量使用：

```text
str
```

本阶段如果成功解决：

```text
str → lifecycle usage
```

则验证：

```text
T2S generation
    ↓
str returned to existing T2S code
    +
usage recorded
```

因此：

```text
TextToSQL
```

不需要修改。

禁止修改 T2S Service。

---

# 十七、RAG

同样验证：

```text
RAG
    ↓
LLM Client
    ↓
str
```

业务代码继续拿：

```text
str
```

但是：

```text
Observation
    ↓
Usage
    ↓
Accounting
```

如果 RAG 本身当前调用路径确实使用该 Client。

禁止修改 RAG。

---

# 十八、Router

Router 如果调用 LLM：

```text
Router
    ↓
LLM
    ↓
str
```

保持 Router 不变。

Accounting 应该自动发生于：

```text
LLM Client lifecycle
```

而不是 Router 自己：

```text
record_usage()
```

---

# 十九、Failure

如果 Provider：

```text
5xx
timeout
429
```

失败：

```text
LLMRequestError
```

保持现有：

```text
Observation.success=False
Observation.usage=None
Accounting=None
```

不要根据 partial response 猜测 usage。

---

# 二十、Retry

Transport retry 机制保持不变。

如果当前没有自动 transport retry：

不要新增。

如果现有：

```text
retry
```

则每一次真实 LLM request：

```text
request #1 → observation/accounting #1
request #2 → observation/accounting #2
```

禁止：

```text
retry usage
```

自动合并。

---

# 二十一、Semantic Retry

T2S semantic retry：

```text
generation #1
    ↓
Validator reject

generation #2
```

必须：

```text
2 requests
2 observations
2 accounting events
```

如果最终某一轮是 str：

仍然必须获得 usage。

---

# 二十二、Refusal

Refusal 仍然：

```text
success=True
```

如果 Provider response 有 usage：

```text
Observation.usage != None
Accounting != None
```

不得因为：

```text
refusal
```

把 usage 丢掉。

---

# 二十三、Mock Provider

更新 Mock / Fake Provider 测试基础设施时：

必须保持：

```text
Mock
```

默认不伪造：

```text
usage
model
request_id
cost
latency
```

如果测试需要 Usage：

必须显式构造：

```text
LLMResponse(
    content=...,
    usage=...
)
```

不要改变默认 Mock 行为。

---

# 二十四、Backward Compatibility

必须验证：

```text
LLMClient()
create_llm_client()
```

现有调用方式全部继续工作。

特别检查：

```text
create_llm_client()
create_llm_client(api_key=...)
create_llm_client(provider=...)
```

以实际代码为准。

禁止因为 Usage Bridge 强制要求新参数。

---

# 二十五、Security

Usage Bridge 只允许访问：

```text
LLMResponse
LLMUsage
model
finish_reason
metadata whitelist
```

禁止访问：

```text
API key
Authorization header
raw HTTP headers
prompt secrets
database credentials
SQL
RAG chunks
tool arguments
raw SDK object
```

不要为了获取 Usage 保存完整 Provider response。

---

# 二十六、性能

本阶段不增加：

```text
network request
database request
sleep
retry
serialization
JSON parsing
```

如果已有内部 `LLMResponse`：

直接复用。

不要：

```text
LLMResponse
→ JSON
→ parse
→ LLMResponse
```

---

# 二十七、测试

新增：

```text
tests/test_llm_usage_visibility.py
```

至少包含：

### 1. generate() usage visibility

```text
Provider → LLMResponse
public → str
observation → usage
accounting → usage
```

### 2. no-tools chat usage visibility

同上。

### 3. model preservation

### 4. finish_reason preservation

### 5. request_id preservation

### 6. Usage=None

### 7. Partial Usage

### 8. Failure

### 9. Tool Calling

### 10. T2S semantic retry

### 11. Refusal

### 12. Concurrency

至少：

```text
5 concurrent requests
```

### 13. Existing caller compatibility

验证旧业务调用方仍然拿到：

```text
str
```

### 14. No global state

AST / behavioral test：

禁止：

```text
last_usage
last_response
global usage
```

### 15. No DB / network

Accounting visibility 本身不得额外产生网络或数据库调用。

---

# 二十八、Integration Tests

重点使用：

```text
Scripted Provider
MockTransport
```

不要调用：

```text
DeepSeek
SiliconFlow
```

默认测试：

```text
real LLM = 0
embedding API = 0
```

---

# 二十九、Architecture 文档

修改：

```text
docs/architecture.md
```

新增：

```text
§8.12 LLM Usage Visibility Bridge
```

明确：

```text
Provider Response
        ↓
LLMResponse
        ↓
Compatibility Boundary
        ├──→ existing str API
        │
        └──→ Observation
                 ↓
             Accounting
```

并明确：

> Public business API remains backward compatible.

> Provider Usage is consumed internally at the request lifecycle boundary.

> No Usage persistence is introduced.

> No automatic Cost calculation is introduced.

> No aggregation is introduced.

---

# 三十、禁止事项

绝对禁止：

```text
修改 T2S
修改 RAG
修改 Router
修改 Orchestrator
修改 Tool Framework
修改 Validator
修改 Executor

创建 Usage DB
创建 Cost DB
创建 Billing
创建 Dashboard
接入 Langfuse
接入 OpenTelemetry
接入 Redis
接入 Kafka
接真实 Pricing
自动计算 Cost
自动 aggregation
```

---

# 三十一、测试命令

先：

```powershell
python -m pytest tests/test_llm_usage_visibility.py -q
```

然后：

```powershell
python -m pytest tests/test_llm_response.py tests/test_llm_observability.py tests/test_llm_accounting.py tests/test_llm_accounting_integration.py tests/test_llm_accounting_lifecycle.py -q
```

然后：

```powershell
python -m pytest -q
```

然后：

```powershell
python -m compileall backend tests scripts
```

最后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

---

# 三十二、Regression Baseline

Phase 3.10.11：

```text
Default:
2117 passed
267 skipped
0 failed

DB:
2343 passed
41 skipped
0 failed
```

允许：

```text
+ new tests
```

不允许：

```text
old tests decrease
old tests modified to hide regression
```

---

# 三十三、最终验收标准

必须全部满足：

```text
□ generate() public return contract unchanged
□ no-tools chat public return contract unchanged
□ tool calling contract unchanged
□ Provider Usage 可进入 lifecycle
□ Observation 可以获得 Usage
□ Accounting 可以获得 Usage
□ identity chain 正确
□ model preservation
□ finish_reason preservation
□ request_id preservation
□ Usage=None 正确
□ Partial Usage 正确
□ Failure 不伪造 Usage
□ Tool Calling request-level 独立
□ T2S retry request-level 独立
□ Refusal 正确
□ 5 concurrent requests 无串线
□ 无 global last_usage
□ 无 global last_response
□ 无 DB
□ 无额外 network
□ 无 Cost 自动计算
□ 无 Pricing
□ 无 Aggregation
□ 无 Persistence
□ 无 Billing
□ 旧业务调用方兼容
□ 全量 pytest 通过
□ DB regression 通过
□ compileall 通过
```

---

# 三十四、最终报告格式

完成后严格报告：

```text
【Phase 3.10.12 COMPLETE】

1. 修改文件
2. Usage Visibility Architecture
3. generate() Usage
4. no-tools chat Usage
5. Tool Calling
6. T2S
7. RAG
8. Router
9. Refusal
10. Failure
11. Model / Finish Reason / Request ID
12. Concurrency
13. Backward Compatibility
14. Security
15. Network / DB
16. Tests
17. Regression
18. Production Path
19. Git Diff
20. 当前限制

Phase 3.10.12 READY
Phase 3.10.12 STOP
```

如果发现现有 Provider Contract 无法在不破坏兼容性的情况下提供 Usage：

不要强行改业务。

报告：

```text
NOT READY

问题：
当前 Provider Contract 无法安全暴露 Usage。

根因：
...

影响：
...

最小候选方案：
...
```

**完成后立即 STOP。**

不要进入 Phase 3.10.13。

不要接真实 Pricing。

不要建 Usage 数据库。

不要做 Dashboard。

不要做 Billing。

不要接 Langfuse / OpenTelemetry。
