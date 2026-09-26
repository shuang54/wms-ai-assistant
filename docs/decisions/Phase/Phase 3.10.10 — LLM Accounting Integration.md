# Phase 3.10.10 — LLM Accounting Integration

## 一、阶段目标

将 Phase 3.10.9 已完成的：

```text
LLMUsage
LLMPricing
LLMCost
calculate_llm_cost()
token_accounting_from_usage()
```

与 Phase 3.10.7 / 3.10.8 的 Observation 生命周期建立最小集成。

本阶段只解决：

```text
实际 LLM Request
        ↓
LLMResponse / LLMObservation
        ↓
LLMUsage
        ↓
Token Accounting
```

不解决：

```text
真实 Provider Pricing
Cost Persistence
Billing
Dashboard
Aggregation
```

核心目标：

> **一次 LLM request 产生的 Observation，可以可靠地关联到对应的 Token Accounting。**

---

# 二、先阅读实际代码

必须先阅读：

```text
backend/app/llm/client.py
backend/app/llm/provider.py
backend/app/llm/deepseek_provider.py
backend/app/llm/observability.py
backend/app/llm/accounting.py

tests/test_llm_observability.py
tests/test_llm_observation_integration.py
tests/test_llm_accounting.py
tests/test_llm_response.py

docs/architecture.md
```

搜索：

```text
LLMObservation
LLMObservationSink
LLMUsage
token_accounting_from_usage
calculate_llm_cost
LLMCost
LLMPricing
```

同时检查：

```text
所有 LLMObservationSink 的调用点
所有 LLMObservation 的构造点
所有 LLMUsage 的来源
```

必须基于实际代码决定最小接入点。

---

# 三、核心架构

目标结构：

```text
                   Actual LLM Request
                           │
                           ▼
                      LLMResponse
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
        LLMObservation               LLMUsage
              │                         │
              │                         ▼
              │                  Token Accounting
              │                         │
              └────────────┬────────────┘
                           ▼
                 request-scoped record
```

注意：

> Observation 与 Accounting 是两个独立 Contract，但它们描述的是同一次 request。

---

# 四、不要修改 LLMObservation

禁止向：

```text
LLMObservation
```

增加：

```text
token_accounting
cost
pricing
currency
input_cost
output_cost
```

Phase 3.10.6 的 Observation Contract 保持不变。

原因：

```text
Observation
=
LLM request runtime facts
```

而：

```text
Accounting
=
usage interpretation
```

两者职责不同。

---

# 五、不要修改 LLMResponse

禁止增加：

```text
accounting
cost
pricing
```

到：

```text
LLMResponse
```

已有：

```text
LLMResponse
    ↓
LLMUsage
```

关系保持不变。

---

# 六、Integration 的最小形式

优先考虑在 Observation 生命周期旁路建立：

```text
LLMObservation
LLMUsage
```

之间的 request-scoped 关联。

例如概念上：

```text
response
    ↓
usage = response.usage
    ↓
token_accounting_from_usage(usage)
```

但：

**不要强行把 Accounting 放进 `LLMObservation`。**

如果需要新的测试 DTO / wrapper：

必须先判断是否真的必要。

优先：

```text
现有 DTO + 测试 helper
```

而不是增加第三套 production DTO。

---

# 七、Accounting 的输出

本阶段重点验证：

```text
token_accounting_from_usage(response.usage)
```

能够得到：

```text
LLMUsage | None
```

并保持：

```text
None → None
partial → 原样
full → 原样
```

禁止：

```text
Accounting
    ↓
重新计算 total_tokens
```

也禁止：

```text
Accounting
    ↓
token estimation
```

---

# 八、Observation 与 Accounting 的关联

推荐测试语义：

```text
response.usage
        │
        ├──────────────→ observation.usage
        │
        └──────────────→ token_accounting_from_usage()
```

确认：

```text
observation.usage is response.usage
```

并且：

```text
accounting == response.usage
```

对于 partial usage：

```text
prompt_tokens
completion_tokens
total_tokens
```

必须保持原样。

---

# 九、Success Request

测试：

```text
LLM request
    ↓
LLMResponse
    ↓
Observation
    ↓
Accounting
```

至少验证：

```text
observation.success is True
observation.usage is response.usage
accounting is not None
```

并确认：

```text
accounting.prompt_tokens
    ==
response.usage.prompt_tokens
```

```text
accounting.completion_tokens
    ==
response.usage.completion_tokens
```

```text
accounting.total_tokens
    ==
response.usage.total_tokens
```

---

# 十、Usage=None

测试：

```text
LLMResponse.usage = None
```

结果：

```text
observation.usage is None
token_accounting_from_usage(None) is None
```

禁止：

```text
estimate tokens
```

禁止：

```text
0 tokens
```

禁止：

```text
fake usage
```

---

# 十一、Partial Usage

至少测试：

### A

```text
prompt_tokens=100
completion_tokens=None
total_tokens=None
```

Accounting：

```text
prompt_tokens=100
completion_tokens=None
total_tokens=None
```

### B

```text
prompt_tokens=None
completion_tokens=200
total_tokens=None
```

保持原样。

### C

如果现有 `LLMUsage` Contract 允许其他 partial 组合：

全部保持原有 Contract。

不要新增自动补全规则。

---

# 十二、Generate() Contract

当前：

```text
generate() -> str
```

必须继续保持。

由于 `str` 路径本身没有：

```text
usage
model
finish_reason
```

因此：

```text
token accounting
```

不能凭空产生。

对于：

```text
generate() -> str
```

如果实际代码没有 response usage：

```text
accounting = None
```

即可。

禁止修改：

```text
generate() -> LLMResponse
```

---

# 十三、Tool Calling

验证：

```text
LLM
 ↓
tool_calls
 ↓
LLM
 ↓
final answer
```

如果有两个实际 LLM requests：

```text
Observation 1
    ↓
Accounting 1

Observation 2
    ↓
Accounting 2
```

不能把两次 usage 合并成：

```text
Accounting total
```

本阶段不做 aggregation。

---

# 十四、T2S Semantic Retry

验证：

```text
LLM request #1
    ↓
usage #1
    ↓
Observation #1
    ↓
Accounting #1

Validator rejection

LLM request #2
    ↓
usage #2
    ↓
Observation #2
    ↓
Accounting #2
```

必须保持 request-level：

```text
1 request = 1 observation = 1 accounting record
```

不要增加：

```text
retry_count
total_retry_tokens
```

等字段。

---

# 十五、Refusal

验证：

```text
Refusal
```

仍然：

```text
1 LLM request
1 Observation
1 Accounting
```

前提是该 response 提供 usage。

如果没有 usage：

```text
1 Observation
0 Accounting data
```

也完全正常。

Refusal 本身：

```text
success=True
```

不改变。

---

# 十六、Failure Request

如果 LLM request 失败：

```text
LLMRequestError
```

没有：

```text
LLMResponse
```

因此：

```text
usage=None
accounting=None
```

Observation：

```text
success=False
```

保持现有 Contract。

不要根据异常估算 token。

---

# 十七、Sink Integration

Phase 3.10.7 已有：

```text
LLMObservationSink
```

本阶段不要修改 Sink 的核心 Contract。

如果需要让测试验证：

```text
Observation
+
Accounting
```

优先在测试层完成：

```text
CollectingObservationSink
```

而不是创建：

```text
AccountingSink
TelemetrySink
UsageSink
CostSink
```

等新的生产基础设施。

---

# 十八、不要引入 Aggregation

禁止：

```text
request 1 usage
request 2 usage
request 3 usage
        ↓
daily total
```

本阶段只做：

```text
request-level
```

不做：

```text
session-level
user-level
project-level
day-level
month-level
```

aggregation。

---

# 十九、Cost

虽然已有：

```text
LLMPricing
LLMCost
calculate_llm_cost()
```

本阶段**不要接真实价格**。

可以使用 synthetic pricing 测试：

```text
input_price = Decimal("1")
output_price = Decimal("2")
currency = "TEST"
```

验证：

```text
Observation
    +
Usage
    +
Synthetic Pricing
        ↓
LLMCost
```

但这个计算只能发生在：

```text
unit/integration test
```

或者明确的纯函数测试中。

不得让生产 LLM Client 自动计算 Cost。

---

# 二十、Production Path

本阶段完成后：

```text
LLM Client
    ↓
Observation
```

仍然是唯一默认生产行为。

不要变成：

```text
LLM Client
    ↓
Observation
    ↓
Accounting
    ↓
Cost
```

然后影响业务。

Accounting 可以被：

```text
explicit caller
```

调用。

但不得自动执行：

```text
cost calculation
```

---

# 二十一、测试文件

优先新增：

```text
tests/test_llm_accounting_integration.py
```

如果当前测试结构更适合，也可以扩展：

```text
tests/test_llm_observation_integration.py
```

至少覆盖：

### 1. Success + Full Usage

```text
1 request
1 observation
1 accounting
```

---

### 2. Success + Partial Usage

验证不补全。

---

### 3. Success + Usage None

```text
accounting=None
```

---

### 4. Tool Calling

```text
2 requests
2 observations
2 accounting records
```

如果实际链路为两次请求。

---

### 5. T2S Retry

```text
2 requests
2 observations
2 accounting records
```

---

### 6. Refusal

```text
1 request
1 observation
1 accounting
```

如果 usage 存在。

---

### 7. Failure

```text
1 failed request
1 failure observation
0 accounting
```

---

### 8. Generate

确认：

```text
generate() -> str
```

不凭空生成 usage/accounting。

---

### 9. Accounting / Observation Isolation

修改/故意破坏 Accounting：

不得影响：

```text
LLM response
business result
observation
```

---

### 10. No Cost Auto Calculation

确认普通 LLM request：

不会自动调用：

```text
calculate_llm_cost()
```

可以使用 monkeypatch / spy 验证。

---

# 二十二、Security

确认 Accounting 只接触：

```text
LLMUsage
LLMPricing
```

不得接触：

```text
prompt
messages
SQL
RAG chunks
tool arguments
tool results
raw response
headers
API key
database context
```

测试可以使用“毒化对象”验证。

---

# 二十三、Concurrency

验证：

```text
5 concurrent requests
```

得到：

```text
5 observations
5 usage/accounting associations
```

不得：

```text
request A
    ↓
accounting B
```

如果当前 Accounting 是纯函数且 request-scoped 参数传递，这应该天然成立。

不要增加共享状态。

---

# 二十四、禁止修改

本阶段原则上只允许：

```text
backend/app/llm/
tests/
docs/architecture.md
```

优先：

```text
tests + docs
```

如果发现生产代码确实需要修改：

必须：

1. 先确认当前 Contract 无法满足；
2. 最小修改；
3. 不改变已有返回契约；
4. 不改变 Observation Contract；
5. 不改变 Provider Contract。

禁止修改：

```text
Prompt
Validator
Executor
Router strategy
Project Context
RAG retrieval
Tool definitions
T2S semantics
AI API semantics
```

---

# 二十五、禁止新增基础设施

严格禁止：

```text
OpenTelemetry
Prometheus
Grafana
Langfuse
Sentry
Redis
Kafka
Billing
Invoice
Dashboard
Database persistence
Pricing API
Exchange Rate API
```

禁止：

```text
real provider pricing
```

禁止：

```text
automatic cost calculation in client
```

---

# 二十六、文档

修改：

```text
docs/architecture.md
```

新增：

```text
§8.10 LLM Accounting Integration
```

明确：

```text
LLMResponse
    ↓
LLMUsage
    ├──→ Observation
    └──→ Token Accounting
```

以及：

```text
LLMPricing
    +
LLMUsage
    ↓
LLMCost
```

明确：

> Accounting 是 request-level usage interpretation。

> Cost calculation 仍然是显式、纯函数行为。

> 当前生产 Client 不自动计算 Cost。

> 当前没有 Aggregation / Persistence / Billing。

---

# 二十七、测试命令

先运行新增测试：

```powershell
python -m pytest tests/test_llm_accounting_integration.py -q
```

然后：

```powershell
python -m pytest tests/test_llm_accounting.py tests/test_llm_observability.py -q
```

然后完整：

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

# 二十八、回归基线

Phase 3.10.9：

```text
Default:
2080 passed
267 skipped
0 failed

DB:
2306 passed
41 skipped
0 failed
```

本阶段新增测试可以增加 passed 数量。

但是：

```text
旧测试不得减少
旧测试不得修改
不得隐藏 regression
```

---

# 二十九、Git Diff

完成后：

```powershell
git status --short
git diff --stat
git diff
```

重点确认：

```text
□ 无 Prompt 修改
□ 无 Validator 修改
□ 无 Executor 修改
□ 无 Router 修改
□ 无 Project Context 修改
□ 无 Ground Truth 修改
□ 无新第三方依赖
□ 无 migration
□ 无 secrets
□ 无真实价格
□ 无数据库写入
□ 无 aggregation
□ 无 billing
```

---

# 三十、最终报告

完成后使用：

```text
Phase 3.10.10 完成报告

1. 修改文件

2. Accounting Integration Architecture

3. Full Usage

4. Partial Usage

5. Usage=None

6. Tool Calling

7. T2S Semantic Retry

8. Refusal

9. Failure

10. Generate Contract

11. Cost Isolation

12. Concurrency

13. Security

14. Tests

15. Regression

16. Network / DB

17. Production Path

18. Git Diff

19. 未完成项

Phase 3.10.10 READY

Phase 3.10.10 STOP
```

如果存在任何 Contract regression：

```text
Phase 3.10.10 NOT READY
```

不要修改旧测试来掩盖问题。

---

# 三十一、最终验收标准

全部满足才能 READY：

```text
□ LLMUsage 仍然只有一个定义
□ Observation Contract 不变
□ LLMResponse Contract 不变
□ Accounting 与 Observation request-level 对齐
□ 1 request → 1 Observation → 1 Accounting
□ Full usage 正确关联
□ Partial usage 原样保留
□ usage=None → 无 Accounting
□ Tool Calling request 独立 accounting
□ T2S retry request 独立 accounting
□ Refusal request 独立 accounting
□ Failure request 无虚构 usage
□ generate() 不虚构 usage
□ Cost 不自动进入生产 Client
□ 不自动调用真实 Pricing
□ 不做 aggregation
□ 不做 persistence
□ 不做 billing
□ 无 prompt/SQL/RAG/tool 数据泄漏
□ 无共享状态
□ 并发无交叉
□ 全量 pytest 通过
□ DB regression 通过
□ compileall 通过
□ Git diff 干净
```

**完成后立即 STOP。**

不要自动进入 Phase 3.10.11。
