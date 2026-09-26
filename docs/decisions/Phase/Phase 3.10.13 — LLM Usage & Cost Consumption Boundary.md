## Phase 3.10.13 — LLM Usage & Cost Consumption Boundary

## 一、阶段目标

基于 Phase 3.10.12 已完成的：

```text
Provider
  ↓
LLMResponse
  ↓
Compatibility Boundary
  ↓
LLMObservation
  ↓
AccountingSink
  ↓
LLMUsage
```

本阶段解决：

> **Usage / Cost 数据未来由谁消费，以及消费边界是什么。**

目标不是持久化。

目标不是计费。

目标是建立一个清晰、稳定、request-level 的：

```text
Usage / Cost Consumption Boundary
```

最终：

```text
LLM Request
    ↓
LLMObservation
    ↓
LLMUsage
    ↓
Accounting Consumer
    ├── usage
    └── optional cost
```

其中：

```text
Usage = 已发生的事实
Cost = 显式计算结果
Persistence = 未来能力
```

---

# 二、严格范围

## 允许

```text
backend/app/llm/accounting.py
backend/app/llm/observability.py
backend/app/llm/client.py
tests/
docs/architecture.md
```

必要时可以新增：

```text
backend/app/llm/accounting_consumer.py
```

但：

**只有实际需要才新增。**

---

## 禁止

绝对禁止：

```text
数据库
Redis
Kafka
消息队列
Billing
Dashboard
真实 Pricing API
Langfuse
OpenTelemetry
Prometheus
Grafana
用户级统计
项目级统计
Session Aggregation
日/月统计
```

禁止修改：

```text
RAG
Tool Framework
Router
Orchestrator
T2S
Validator
Executor
Project Context
Business Semantic
```

---

# 三、开始编码前必须阅读

先阅读：

```text
backend/app/llm/accounting.py
backend/app/llm/observability.py
backend/app/llm/client.py

tests/test_llm_accounting.py
tests/test_llm_accounting_integration.py
tests/test_llm_accounting_lifecycle.py
tests/test_llm_usage_visibility.py

docs/architecture.md
```

搜索：

```text
LLMUsage
LLMPricing
LLMCost
token_accounting_from_usage
calculate_llm_cost
LLMAccountingSink
LLMObservation
```

确认：

```text
accounting.py
```

当前真实 Contract。

不要重新定义已有 DTO。

---

# 四、核心概念

本阶段正式固定：

```text
LLMUsage
=
Provider usage fact
```

例如：

```text
prompt_tokens
completion_tokens
total_tokens
```

它不是：

```text
estimated tokens
```

不是：

```text
calculated tokens
```

不是：

```text
billing tokens
```

---

# 五、Cost 定义

Cost 必须保持：

```text
LLMUsage
     +
LLMPricing
     ↓
LLMCost
```

即：

```text
Usage
=
事实

Pricing
=
外部输入

Cost
=
计算结果
```

不能：

```text
Usage
    ↓
自动猜 Pricing
```

---

# 六、Consumption Boundary

如果当前代码结构适合，建立一个最小：

```text
Accounting Consumer
```

概念：

```python
consume_usage(
    observation: LLMObservation
) -> LLMUsage | None
```

或者：

```python
consume_accounting(
    observation: LLMObservation
) -> ...
```

具体 API 名称必须根据实际代码选择。

要求：

```text
纯函数
request-level
无状态
无 IO
```

---

# 七、禁止重复 DTO

不要创建：

```text
UsageRecord
TokenUsageRecord
LLMTokenRecord
AccountingRecord
```

等与：

```text
LLMUsage
```

重复的数据结构。

除非当前架构存在明确必要性。

优先：

```text
LLMObservation
      ↓
LLMUsage
```

---

# 八、Usage Consumer

Consumer 最小职责：

```text
Observation
   ↓
observation.usage
   ↓
LLMUsage | None
```

不得：

```text
重新计算 total_tokens
```

不得：

```text
prompt_tokens + completion_tokens
```

不得：

```text
estimate
```

不得：

```text
round
```

不得：

```text
fill missing
```

---

# 九、Identity Contract

对于：

```text
observation.usage != None
```

要求：

```python
consume_usage(observation) is observation.usage
```

保持 identity。

不能创建：

```text
new LLMUsage(...)
```

复制一份。

这样可以保证：

```text
Provider
 ↓
LLMResponse.usage
 ↓
Observation.usage
 ↓
Consumer
```

是同一个 request-level usage object。

---

# 十、Usage=None

：

```text
observation.usage is None
```

结果：

```text
consume_usage(observation) is None
```

不能：

```text
0
```

不能：

```text
LLMUsage(0,0,0)
```

不能估算。

---

# 十一、Partial Usage

保持：

```text
prompt_tokens=None
completion_tokens=200
total_tokens=None
```

原样。

Consumer 不负责补全。

---

# 十二、Cost Consumer

本阶段可以增加一个纯函数：

```text
usage + pricing → cost
```

但已有：

```text
calculate_llm_cost()
```

因此：

**优先复用，不新增重复实现。**

例如：

```text
consume_cost(
    observation,
    pricing
)
```

如果实际需要，也必须只是：

```text
observation
    ↓
usage
    ↓
calculate_llm_cost(usage, pricing)
```

---

# 十三、Cost 必须显式 Pricing

调用：

```text
calculate_llm_cost()
```

必须显式传入：

```text
LLMPricing
```

禁止：

```text
model
 ↓
hard-coded price
```

禁止：

```text
DeepSeek
 ↓
自动获取价格
```

禁止：

```text
USD → CNY
```

---

# 十四、Partial Cost

保持 Phase 3.10.9 语义。

### Full

```text
prompt
completion
total
```

→ input/output/total cost。

### Prompt only

→ input cost。

### Completion only

→ output cost。

### None

→ cost None。

不要：

```text
missing = 0
```

---

# 十五、Currency

必须继续要求：

```text
LLMPricing.currency
```

显式提供。

例如测试：

```text
TEST
```

禁止默认：

```text
USD
```

禁止自动：

```text
CNY
VND
```

---

# 十六、Provider Price

禁止新增：

```text
deepseek-chat = ...
deepseek-reasoner = ...
```

等价格表。

本阶段：

```text
Provider
    ↓
Usage
```

而：

```text
Pricing
```

由调用方提供。

---

# 十七、Client 不负责 Cost

确认：

```text
LLMClient
```

不会：

```text
calculate_llm_cost()
```

不会：

```text
LLMPricing
```

不会：

```text
LLMCost
```

Client 只负责：

```text
LLM Request
Observation
Accounting lifecycle
```

---

# 十八、AccountingSink 与 Consumer

现有：

```text
LLMAccountingSink
```

继续保持：

```text
record(observation)
```

本阶段如果增加 Consumer：

推荐：

```text
AccountingSink
      ↓
Consumer
```

但：

**不要强制修改 Sink Contract。**

如果现有：

```text
CollectingAccountingSink
```

已经可以完成消费验证，就直接复用。

---

# 十九、禁止 Sink 自动持久化

即使以后：

```text
AccountingSink
```

可能接：

```text
Database
Kafka
Telemetry
```

本阶段也不能。

默认：

```text
NoopAccountingSink
```

继续存在。

---

# 二十、测试 Usage Consumption

新增：

```text
tests/test_llm_accounting_consumption.py
```

至少：

### 1. Full Usage

```text
Observation
 ↓
Consumer
 ↓
same LLMUsage
```

---

### 2. Partial Usage

确认原样。

---

### 3. None

确认 None。

---

### 4. Identity

明确：

```text
consumer_result is observation.usage
```

---

### 5. Immutability

确认 Consumer 不修改：

```text
LLMUsage
```

---

# 二十一、Cost Consumption Tests

测试：

```text
Observation
+
Synthetic LLMPricing
↓
LLMCost
```

例如：

```text
input = 1 / 1M
output = 2 / 1M
currency = TEST
```

Usage：

```text
1M input
2M output
3M total
```

结果：

```text
input_cost = 1
output_cost = 4
total_cost = 5
```

继续使用：

```text
Decimal
```

禁止：

```text
float
```

---

# 二十二、Cost=None

测试：

```text
observation.usage = None
```

结果：

```text
cost = None
```

即使：

```text
pricing
```

存在，也不能产生：

```text
0
```

---

# 二十三、Pricing=None

如果消费 API 允许：

```text
pricing=None
```

结果：

```text
cost=None
```

不能：

```text
guess pricing
```

如果现有 `calculate_llm_cost()` 不接受 None：

则不要修改已有 Contract，只在消费层：

```text
pricing is None
    ↓
return None
```

---

# 二十四、Security

Consumer 只能读取：

```text
observation.usage
```

以及显式：

```text
LLMPricing
```

不得访问：

```text
prompt
messages
SQL
RAG chunks
tool args
tool results
raw response
headers
API key
database URL
```

可以使用毒化对象测试。

---

# 二十五、No Side Effects

Consumer 必须是：

```text
pure
```

禁止：

```text
HTTP
DB
Redis
Kafka
filesystem
sleep
retry
```

可以使用 AST 检查。

---

# 二十六、Concurrency

增加：

```text
5 concurrent observations
```

验证：

```text
consumer(A) is usage(A)
consumer(B) is usage(B)
...
```

禁止：

```text
shared state
```

---

# 二十七、Tool Calling

验证：

```text
Observation 1
    ↓
Consumer 1

Observation 2
    ↓
Consumer 2
```

不要合并。

---

# 二十八、T2S Retry

验证：

```text
Observation 1
    ↓
Usage 1

Observation 2
    ↓
Usage 2
```

仍然：

```text
request-level
```

不要生成：

```text
total retry tokens
```

---

# 二十九、RAG / Router

只做集成回归：

```text
RAG
Router
T2S
```

确认 Usage Consumer 不影响业务结果。

禁止修改这些业务模块。

---

# 三十、Backward Compatibility

确保：

```text
LLMClient
create_llm_client
LLMObservationSink
LLMAccountingSink
NoopAccountingSink
```

原 API 继续工作。

Consumer 是：

```text
optional
```

不是：

```text
required
```

---

# 三十一、Architecture Documentation

修改：

```text
docs/architecture.md
```

新增：

```text
§8.13 LLM Usage & Cost Consumption Boundary
```

明确：

```text
LLMResponse
    ↓
LLMObservation
    ↓
LLMUsage
    ↓
Usage Consumer
```

Cost：

```text
LLMUsage
    +
Explicit LLMPricing
    ↓
LLMCost
```

同时明确：

```text
No persistence
No aggregation
No billing
No automatic pricing
No automatic cost
```

---

# 三十二、测试命令

先：

```powershell
python -m pytest tests/test_llm_accounting_consumption.py -q
```

然后：

```powershell
python -m pytest tests/test_llm_accounting.py tests/test_llm_accounting_integration.py tests/test_llm_accounting_lifecycle.py tests/test_llm_usage_visibility.py -q
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

# 三十三、Phase 3.10.12 Baseline

默认：

```text
2134 passed
267 skipped
0 failed
```

DB：

```text
2360 passed
41 skipped
0 failed
```

新增测试可以增加 passed。

但是：

```text
旧测试不得为了本阶段修改语义
```

---

# 三十四、Git Diff

检查：

```powershell
git status --short
git diff --stat
git diff
```

必须确认：

```text
□ 无 RAG 修改
□ 无 Router 修改
□ 无 Tool Framework 修改
□ 无 T2S 修改
□ 无 Validator 修改
□ 无 Executor 修改
□ 无 Project Context 修改
□ 无 DB migration
□ 无 DB 写入
□ 无真实 Pricing
□ 无 Billing
□ 无 Aggregation
□ 无 Persistence
□ 无第三方依赖
```

---

# 三十五、最终验收

全部满足：

```text
□ Usage Consumer 存在且职责单一
□ Consumer request-level
□ Consumer 无状态
□ Consumer 无 IO
□ Consumer 不复制 LLMUsage
□ Full Usage 正确
□ Partial Usage 正确
□ None 正确
□ Identity 保持
□ Usage 不可变
□ Cost 复用现有 calculate_llm_cost
□ Pricing 必须显式
□ 无自动 Pricing
□ 无自动 Cost
□ 无 float 计算
□ Decimal 保持
□ Tool Calling 独立
□ T2S Retry 独立
□ RAG / Router 不受影响
□ 5 并发无串线
□ Security 通过
□ Backward Compatibility 通过
□ pytest 通过
□ DB regression 通过
□ compileall 通过
```

---

# 三十六、最终报告

完成后严格：

```text
【Phase 3.10.13 COMPLETE】

1. 修改文件
2. Usage Consumption Boundary
3. Usage Consumer
4. Full / Partial / None
5. Identity
6. Cost Consumption
7. Pricing
8. Tool Calling
9. T2S Retry
10. RAG / Router
11. Concurrency
12. Security
13. Side Effects
14. Tests
15. Regression
16. Network / DB
17. Production Path
18. Git Diff
19. 当前限制

Phase 3.10.13 READY
Phase 3.10.13 STOP
```

如果发现 Consumer 必须引入新的复杂基础设施才能成立：

**立即停止并报告，不要扩大 Phase。**

---

# 三十七、硬停止

完成后：

**立即 STOP。**

不要：

```text
Usage Database
Cost Database
Billing
Dashboard
Real Pricing
Telemetry Platform
Langfuse
OpenTelemetry
```

这些全部留到后续明确阶段。
