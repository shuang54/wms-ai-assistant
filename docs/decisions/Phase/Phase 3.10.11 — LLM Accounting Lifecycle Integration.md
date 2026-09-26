# Phase 3.10.11 — LLM Accounting Lifecycle Integration

## 一、阶段目标

基于 Phase 3.10.10 已验证的：

```text
LLMResponse
    ↓
LLMUsage
    ├──→ LLMObservation
    └──→ Token Accounting
```

本阶段进行第一次**最小生产路径集成**。

目标：

> LLM request 完成后，在现有 Observation 生命周期中，同时产生 request-level Accounting 信息。

最终形成：

```text
LLM Request
    ↓
LLM Client
    ↓
LLMResponse / str
    ↓
LLMObservation
    ├──→ ObservationSink
    │
    └──→ AccountingSink
            ↓
        LLMUsage
```

注意：

本阶段只记录：

```text
request-level token usage
```

不做：

```text
真实价格
LLMCost 自动计算
数据库持久化
用户级统计
项目级统计
Dashboard
Billing
Aggregation
```

---

# 二、开始编码前必须阅读

必须阅读实际代码：

```text
backend/app/llm/client.py
backend/app/llm/observability.py
backend/app/llm/accounting.py
backend/app/llm/provider.py
backend/app/llm/deepseek_provider.py

tests/test_llm_observability.py
tests/test_llm_observation_integration.py
tests/test_llm_accounting.py
tests/test_llm_accounting_integration.py

docs/architecture.md
```

同时搜索：

```text
LLMObservationSink
NoopObservationSink
build_llm_observation_safe
token_accounting_from_usage
LLMUsage
LLMObservation
```

还要检查：

```text
所有 LLM Client 创建点
所有 observation_sink 注入点
create_llm_client()
```

不要假设现有接口。

---

# 三、核心设计原则

本阶段必须保持：

```text
Observation = request runtime facts

Accounting = request token facts
```

不要把 Accounting 字段塞进：

```text
LLMObservation
LLMResponse
```

禁止新增：

```text
observation.token_usage
observation.cost
observation.pricing
```

也禁止：

```text
response.accounting
response.cost
```

---

# 四、最小生产集成

优先采用：

```text
LLMObservationSink
        +
Accounting Sink
```

或者：

```text
ObservationSink
        ↓
request-scoped accounting callback
```

具体采用哪一种：

**必须先根据当前实际 `observability.py` / `client.py` 结构选择最小改动方案。**

不要为了形式上创建 AccountingSink 而过度抽象。

---

# 五、推荐目标结构

如果当前架构适合，推荐：

```python
class LLMAccountingSink(Protocol):
    def record(self, observation: LLMObservation) -> None:
        ...
```

并提供：

```python
class NoopAccountingSink:
    def record(self, observation: LLMObservation) -> None:
        ...
```

但是：

**只有当前代码确实需要独立 Sink Contract 时才创建。**

如果通过现有 Observation Sink 就可以干净完成：

```text
Observation
    ↓
explicit accounting derivation
```

则不要新增第二套 Sink。

---

# 六、强制要求：默认行为不变

如果用户没有配置 Accounting：

```text
accounting_sink = None
```

必须等价于：

```text
Noop
```

因此：

```text
现有业务代码
    ↓
LLM Client
```

行为必须与 Phase 3.10.10 完全一致。

不能因为本阶段增加 Accounting：

```text
LLM request
↓
额外网络请求
↓
额外数据库请求
↓
额外延迟
```

---

# 七、Accounting 必须 request-scoped

禁止：

```text
client.last_usage
client.last_accounting
global accounting
global observation
```

禁止任何共享可变状态。

正确模型：

```text
Request A
    ↓
Observation A
    ↓
Accounting A

Request B
    ↓
Observation B
    ↓
Accounting B
```

---

# 八、LLMResponse 路径

对于：

```text
LLMResponse
```

并且：

```text
response.usage != None
```

必须能够形成：

```text
observation.usage is response.usage
```

并通过现有：

```text
token_accounting_from_usage()
```

获得：

```text
accounting is response.usage
```

不得重新创建一份完全相同的 Usage DTO，除非现有 API 明确要求。

优先保持 identity：

```text
observation.usage is response.usage
accounting is response.usage
```

---

# 九、Usage=None

如果：

```text
response.usage is None
```

Accounting Sink：

```text
record(observation)
```

可以被调用，但不能生成：

```text
0 tokens
```

不能估算。

不能抛异常。

结果应当是：

```text
accounting = None
```

---

# 十、Generate / str 路径

现有：

```text
generate() -> str
chat(messages) -> str
```

Contract 不变。

如果没有 `LLMUsage`：

```text
observation.usage = None
accounting = None
```

不得修改：

```text
generate() -> LLMResponse
```

不得从字符串估算 token。

---

# 十一、Failure

如果 LLM request 失败：

```text
LLMRequestError
```

仍然：

```text
Observation:
    success=False
    usage=None
```

Accounting：

```text
None
```

不能：

```text
estimate usage
```

不能：

```text
create fake accounting
```

Failure Observation 的现有异常行为必须保持不变。

---

# 十二、Sink Failure Isolation

这是本阶段重点。

如果：

```text
AccountingSink.record()
```

内部发生异常：

例如：

```text
RuntimeError("accounting sink failure")
```

不得影响：

```text
LLM response
```

不得改变：

```text
business result
```

不得把原本成功的 LLM request 变成：

```text
LLM failure
```

推荐行为：

```text
LLM Request
    ↓
success
    ↓
Observation
    ↓
AccountingSink
    ↓
ERROR
    ↓
warning/log
    ↓
正常返回原 LLM result
```

但：

**必须保留原有 Observation Sink failure isolation 语义。**

不要让 Accounting 的异常覆盖业务异常。

---

# 十三、Observation Sink 与 Accounting Sink 隔离

测试：

```text
ObservationSink = failing
AccountingSink = normal
```

以及：

```text
ObservationSink = normal
AccountingSink = failing
```

两种情况下都要确认：

```text
LLM business result
```

不受影响。

---

# 十四、Concurrency

必须增加真实 request-level 并发测试：

```text
5 concurrent LLM requests
```

每个 request 使用不同：

```text
prompt_tokens
completion_tokens
total_tokens
request_id
```

验证：

```text
Observation A ↔ Accounting A
Observation B ↔ Accounting B
...
```

不能发生：

```text
A usage → B accounting
```

---

# 十五、Tool Calling

现有 Tool Calling：

```text
LLM request #1
    ↓
tool call

LLM request #2
    ↓
final answer
```

如果两个 response 都包含 usage：

必须：

```text
2 Observations
2 Accounting records
```

禁止合并：

```text
Accounting.total_tokens
```

禁止：

```text
request #1 + request #2
```

在本阶段自动聚合。

---

# 十六、T2S Semantic Retry

必须验证：

```text
Request #1
    ↓
Observation #1
    ↓
Accounting #1

Validator Reject

Request #2
    ↓
Observation #2
    ↓
Accounting #2
```

必须保持：

```text
1 request = 1 accounting event
```

不要增加：

```text
retry_count
retry_tokens
total_retry_cost
```

---

# 十七、Refusal

现有 refusal：

```text
1 LLM request
↓
refusal business result
```

保持：

```text
success=True
```

如果有 usage：

```text
1 Observation
1 Accounting
```

如果没有：

```text
1 Observation
0 Accounting data
```

不得把 refusal 当成 LLM failure。

---

# 十八、Cost 严格隔离

本阶段：

**不得在生产 Client 自动调用：**

```text
calculate_llm_cost()
```

禁止：

```text
LLMPricing
```

进入默认 Client。

禁止真实价格。

禁止：

```text
USD
CNY
VND
```

等生产货币自动计算。

允许测试中使用：

```text
synthetic pricing
```

但必须继续保持：

```text
Cost = explicit calculation
```

---

# 十九、不要持久化

禁止新增：

```text
accounting table
usage table
cost table
```

禁止：

```text
SQL INSERT
```

禁止：

```text
Redis
Kafka
PostgreSQL
```

用于 Accounting。

本阶段：

```text
Accounting Sink
```

最多是：

```text
in-memory test sink
```

或者：

```text
Noop
```

---

# 二十、不要做 Aggregation

禁止：

```text
daily_tokens
monthly_tokens
project_tokens
user_tokens
session_tokens
```

禁止：

```text
sum()
```

等聚合逻辑进入生产路径。

---

# 二十一、测试文件

优先新增：

```text
tests/test_llm_accounting_lifecycle.py
```

如果当前项目结构更合理，也可以扩展：

```text
tests/test_llm_observation_integration.py
```

建议至少：

```text
1. LLMResponse → Observation → Accounting
2. Usage=None
3. Partial Usage
4. generate() str
5. Failure
6. AccountingSink failure isolation
7. ObservationSink failure isolation
8. Observation + Accounting sink independence
9. Tool Calling
10. T2S Retry
11. Refusal
12. Concurrent requests
13. No automatic cost
14. No persistence
15. No aggregation
```

---

# 二十二、测试 AccountingSink

如果新增：

```text
LLMAccountingSink
```

必须测试：

```text
record(observation)
```

只读取：

```text
observation.usage
```

并调用：

```text
token_accounting_from_usage()
```

不得读取：

```text
messages
prompt
SQL
RAG chunks
tool arguments
tool results
raw response
API key
headers
```

---

# 二十三、Security

Accounting 生命周期中只能出现：

```text
LLMObservation
LLMUsage
```

必要情况下：

```text
LLMPricing
LLMCost
```

不得泄漏：

```text
API key
Authorization
password
database URL
SQL
prompt
tool arguments
RAG chunks
raw provider response
HTTP headers
```

如果日志存在：

不得打印：

```text
usage object
```

中的任何秘密字段，因为 Usage 本身不应该包含秘密。

同时不要打印完整 request / response。

---

# 二十四、性能

本阶段不做性能优化。

只验证：

```text
Accounting derivation
```

不会产生：

```text
network
database
sleep
retry
```

可以增加 AST / monkeypatch 测试：

```text
Accounting lifecycle
    ↓
0 network
0 DB
0 sleep
```

---

# 二十五、允许修改范围

优先：

```text
backend/app/llm/observability.py
backend/app/llm/accounting.py
backend/app/llm/client.py
tests/
docs/architecture.md
```

如果可以完全通过：

```text
observability.py
```

完成，不要修改：

```text
provider.py
deepseek_provider.py
```

禁止修改：

```text
Router
Orchestrator
RAG
Tool Framework
T2S
Validator
Executor
Project Context
Business Semantic
Database Schema
```

---

# 二十六、Provider Contract

严格保持：

```text
LLMProvider
```

现有 Contract。

禁止增加：

```text
accounting
pricing
cost
```

到 Provider Protocol。

Provider 只负责：

```text
LLM request
→
provider response
```

Accounting 属于：

```text
client/request lifecycle
```

而不是 Provider。

---

# 二十七、Factory

检查：

```text
create_llm_client()
```

如果需要支持：

```text
accounting_sink
```

只能：

```text
optional parameter
```

并且：

```text
None
```

必须保持旧行为。

已有调用：

```python
create_llm_client()
```

不得失效。

不要强制修改所有调用点。

---

# 二十八、Backward Compatibility

以下代码必须继续工作：

```python
LLMClient(...)
```

```python
create_llm_client()
```

```python
create_llm_client(api_key=...)
```

以及现有：

```python
LLMObservationSink
NoopObservationSink
```

任何旧测试失败：

**不要修改旧测试适配新设计。**

先定位 Contract regression。

---

# 二十九、Architecture Documentation

修改：

```text
docs/architecture.md
```

新增：

```text
§8.11 LLM Accounting Lifecycle
```

明确：

```text
LLM Request
    ↓
LLMResponse
    ↓
LLMObservation
    ↓
ObservationSink

LLMObservation.usage
    ↓
token_accounting_from_usage()
    ↓
AccountingSink
```

并明确：

```text
Accounting = request-level
Cost = explicit calculation
Aggregation = not implemented
Persistence = not implemented
Billing = not implemented
```

---

# 三十、测试命令

先：

```powershell
python -m pytest tests/test_llm_accounting_lifecycle.py -q
```

然后：

```powershell
python -m pytest tests/test_llm_accounting.py tests/test_llm_accounting_integration.py tests/test_llm_observability.py -q
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

# 三十一、Phase 3.10.10 基线

默认：

```text
2097 passed
267 skipped
0 failed
```

DB：

```text
2323 passed
41 skipped
0 failed
```

本阶段新增测试允许增加 passed 数量。

但是：

```text
旧测试不得减少
旧测试不得修改
旧行为不得改变
```

---

# 三十二、Git Diff

完成后执行：

```powershell
git status --short
git diff --stat
git diff
```

确认：

```text
□ 没有 Prompt 修改
□ 没有 Router 修改
□ 没有 RAG 修改
□ 没有 Tool Framework 修改
□ 没有 T2S 修改
□ 没有 Validator 修改
□ 没有 Executor 修改
□ 没有 Project Context 修改
□ 没有数据库 migration
□ 没有数据库写入
□ 没有新第三方依赖
□ 没有真实价格
□ 没有 Billing
□ 没有 Aggregation
□ 没有 Persistence
```

---

# 三十三、最终报告

完成后严格按照：

```text
【Phase 3.10.11 COMPLETE】

1. 修改文件
2. Accounting Lifecycle Architecture
3. LLMResponse → Observation → Accounting
4. Usage=None
5. Partial Usage
6. Generate Contract
7. Failure
8. Tool Calling
9. T2S Retry
10. Refusal
11. Sink Failure Isolation
12. Concurrency
13. Cost Isolation
14. Security
15. Network / DB
16. Tests
17. Regression
18. Production Path
19. Git Diff
20. 当前限制

Phase 3.10.11 READY
Phase 3.10.11 STOP
```

如果发现必须修改核心业务行为才能完成：

```text
Phase 3.10.11 NOT READY
```

并报告：

```text
问题
根因
影响
建议
```

不要自行扩大范围。

---

# 三十四、最终停止条件

全部满足：

```text
□ Accounting 进入 request lifecycle
□ 1 request = 1 accounting event
□ Observation 与 Accounting request-level 对齐
□ 无共享状态
□ 无 concurrency cross-talk
□ Usage=None 正确处理
□ Partial Usage 正确处理
□ Failure 不虚构 usage
□ Tool Calling 独立 accounting
□ T2S Retry 独立 accounting
□ Refusal 正确处理
□ Sink failure 不影响业务结果
□ Cost 不自动计算
□ 无真实 Pricing
□ 无 Persistence
□ 无 Aggregation
□ 无 Billing
□ 无 Secrets 泄漏
□ 无 Network
□ 无 DB
□ Backward Compatibility 通过
□ 全量 pytest 通过
□ DB regression 通过
□ compileall 通过
```

**完成后立即 STOP。**

不要进入 Phase 3.10.12。

不要接真实 Pricing。

不要建 Usage 数据库。

不要做 Dashboard。

不要接 Langfuse / OpenTelemetry。

不要进入 Billing / Cost Platform。
