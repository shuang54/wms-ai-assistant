# Phase 3.10.9 — LLM Token & Cost Accounting Contract

## 一、阶段目标

建立 LLM Token / Cost Accounting 的基础 Contract。

本阶段只解决：

```text
LLMResponse
    ↓
LLMUsage
    ↓
Token Accounting
    ↓
Cost Calculation Contract
```

目标是让系统未来可以支持：

```text
DeepSeek
OpenAI
其他 OpenAI-compatible Provider
不同 Model
不同 Pricing Scheme
```

但本阶段：

> **不连接真实价格，不计算真实账单，不持久化。**

---

# 二、先阅读现有实现

必须先阅读：

```text
backend/app/llm/client.py
backend/app/llm/provider.py
backend/app/llm/deepseek_provider.py
backend/app/llm/observability.py

tests/test_llm_response.py
tests/test_llm_observability.py
tests/test_llm_provider.py

docs/architecture.md
```

搜索：

```text
LLMUsage
usage
model
provider
metadata
LLMObservation
```

必须确认当前：

```text
LLMUsage
```

的实际定义和所有调用位置。

不要重新定义已有 `LLMUsage`。

---

# 三、核心设计

本阶段必须严格区分：

```text
Token Usage
```

与：

```text
Cost
```

架构：

```text
Provider Response
       │
       ▼
   LLMUsage
       │
       ▼
 Token Accounting
       │
       ├── prompt tokens
       ├── completion tokens
       └── total tokens
```

未来才是：

```text
LLMUsage
   +
Pricing
   ↓
Cost
```

因此：

> `LLMUsage` 不应该知道价格。

---

# 四、LLMUsage Contract

检查当前：

```text
LLMUsage
```

并保持已有字段：

```text
prompt_tokens
completion_tokens
total_tokens
```

类型：

```text
int | None
```

已有规则必须保持：

```text
None
```

表示 provider 没有提供对应数据。

---

# 五、Token Accounting DTO

如果现有代码没有合适的 accounting DTO，可以新增：

```text
backend/app/llm/accounting.py
```

定义一个最小、不可变的：

```text
LLMTokenAccounting
```

建议字段：

```text
prompt_tokens
completion_tokens
total_tokens
```

类型：

```text
int | None
```

要求：

```text
@dataclass(frozen=True)
```

但是：

**如果发现现有 `LLMUsage` 已经完全满足 Accounting Contract，不要为了抽象而新增重复 DTO。**

优先复用。

---

# 六、Accounting 的核心原则

Token Accounting 不应该重新解析 provider response。

禁止：

```text
raw SDK response
    ↓
Accounting
```

正确：

```text
raw SDK response
    ↓
OpenAICompatibleClient
    ↓
LLMUsage
    ↓
Accounting
```

也就是说：

> Provider SDK isolation 仍然由 Client 负责。

---

# 七、Token 总量规则

当前 `LLMUsage` 已有：

```text
prompt_tokens
completion_tokens
total_tokens
```

建立统一规则。

如果三者都有：

```text
total_tokens
==
prompt_tokens + completion_tokens
```

如果 provider 已经违反该规则：

按照现有 3.10.4 / 3.10.5 Contract：

```text
usage invalid
→ usage=None
```

不要在 Accounting 层偷偷修正：

```text
total_tokens =
prompt_tokens + completion_tokens
```

因为这会改变 Provider Contract。

---

# 八、Partial Usage

允许：

```text
prompt_tokens=100
completion_tokens=None
total_tokens=None
```

Accounting 必须保留：

```text
prompt_tokens=100
completion_tokens=None
total_tokens=None
```

不要自动补：

```text
total_tokens=100
```

除非现有 Contract 已明确允许。

本阶段：

> 不推断 provider 未提供的数据。

---

# 九、Cost Contract

建立费用计算的最小抽象。

如果当前项目不存在：

```text
LLMPricing
```

可以新增：

```text
LLMPricing
```

但它只描述价格：

```text
input_price_per_1m_tokens
output_price_per_1m_tokens
```

例如：

```text
LLMPricing(
    input_price_per_1m_tokens=...,
    output_price_per_1m_tokens=...,
)
```

注意：

**本阶段不要填写 DeepSeek / OpenAI 的真实价格。**

测试可以使用：

```text
input = 1.0
output = 2.0
```

这样的 synthetic pricing。

---

# 十、Cost DTO

如果确实需要新增：

```text
LLMCost
```

建议最小字段：

```text
input_cost
output_cost
total_cost
currency
```

其中：

```text
currency
```

必须明确。

不要默认为：

```text
CNY
```

不要因为当前用户/公司环境是中国或越南而推断币种。

测试可以显式使用：

```text
USD
```

---

# 十一、Cost Calculation

建立纯函数：

```text
calculate_llm_cost(
    usage,
    pricing,
)
```

要求：

```text
pure function
```

即：

```text
same usage
+
same pricing
=
same result
```

不允许：

```text
database
network
environment
global mutable state
```

---

# 十二、计算公式

如果数据完整：

```text
input_cost =
prompt_tokens / 1_000_000
× input_price_per_1m_tokens
```

```text
output_cost =
completion_tokens / 1_000_000
× output_price_per_1m_tokens
```

```text
total_cost =
input_cost + output_cost
```

注意：

不要把：

```text
total_tokens
```

直接乘一个统一价格。

因为 input/output 价格可能不同。

---

# 十三、Partial Usage 的 Cost 行为

必须明确：

### 情况 A

```text
prompt_tokens
completion_tokens
```

都有：

可以计算完整 Cost。

---

### 情况 B

只有：

```text
prompt_tokens
```

则：

```text
input_cost
```

可以计算。

但：

```text
output_cost
total_cost
```

应该保持：

```text
None
```

不要把缺失的 output 当成 0。

---

### 情况 C

只有：

```text
completion_tokens
```

则：

```text
output_cost
```

可以计算。

```text
input_cost
total_cost
```

为：

```text
None
```

---

### 情况 D

没有任何 usage：

```text
usage=None
```

则：

```text
cost=None
```

---

# 十四、不要修改 LLMResponse

禁止增加：

```text
cost
price
currency
input_cost
output_cost
```

到：

```text
LLMResponse
```

当前：

```text
LLMResponse
```

Contract 保持不变。

---

# 十五、不要修改 LLMObservation

同样禁止增加：

```text
cost
price
currency
```

到：

```text
LLMObservation
```

原因：

```text
Observation
```

描述：

> 一次 LLM Request 的运行事实。

而：

```text
Cost
```

依赖：

> 外部 Pricing Policy。

例如：

```text
同一个 request
```

可能存在：

```text
Pricing A
Pricing B
```

因此：

```text
Observation ≠ Cost
```

---

# 十六、Pricing 不应该放进 Provider

禁止：

```text
DeepSeekProvider
    ↓
price
```

Provider 只负责：

```text
LLM call
```

Pricing 是独立领域。

推荐：

```text
LLM Provider
    │
    ▼
LLMResponse
    │
    ▼
LLMUsage

Pricing
    │
    ▼
Cost Calculator
```

---

# 十七、Provider / Model 标识

未来 Cost 计算需要知道：

```text
provider
model
```

但本阶段不要把它们硬编码进价格表。

例如禁止：

```text
if model == "deepseek-chat":
    price = ...
```

禁止：

```text
if provider == "deepseek":
    ...
```

本阶段只让：

```text
LLMPricing
```

作为显式输入。

---

# 十八、Validation

`LLMPricing` 必须验证：

```text
input_price_per_1m_tokens >= 0
output_price_per_1m_tokens >= 0
```

禁止：

```text
negative price
NaN
Inf
bool
string
```

如果使用 Pydantic：

严格验证类型。

如果使用 dataclass：

提供明确 validation。

---

# 十九、Cost 精度

不要使用：

```text
float
```

直接作为最终财务金额的长期 Contract，除非现有项目明确规定。

优先考虑：

```text
Decimal
```

例如：

```text
Decimal("1.0")
```

原因：

费用计算属于金额语义。

但不要引入：

```text
money library
```

等新依赖。

Python 标准库 `decimal.Decimal` 足够。

---

# 二十、Rounding

本阶段不要擅自设计企业财务 rounding policy。

可以保持：

```text
Decimal
```

原始精度。

不要自动：

```text
round(..., 2)
```

因为：

```text
billing rounding
display rounding
accounting rounding
```

不是同一个概念。

文档明确：

> 本阶段 Cost 是计算 Contract，不定义最终账单展示精度。

---

# 二十一、Provider Usage 缺失

必须保持现有语义：

```text
provider doesn't return usage
        ↓
usage=None
        ↓
cost=None
```

不要发第二次请求。

不要估算 token。

禁止：

```text
len(prompt)
```

等方式估算真实 token。

---

# 二十二、Tests

新增：

```text
tests/test_llm_accounting.py
```

如果现有测试结构更合适，也可以扩展已有测试。

至少覆盖：

### 1. Full usage

```text
prompt=1_000_000
completion=2_000_000
```

synthetic pricing：

```text
input=1
output=2
```

结果：

```text
input_cost=1
output_cost=4
total_cost=5
```

---

### 2. Partial input usage

确认：

```text
input_cost != None
output_cost is None
total_cost is None
```

---

### 3. Partial output usage

确认：

```text
input_cost is None
output_cost != None
total_cost is None
```

---

### 4. Missing usage

```text
usage=None
```

结果：

```text
cost=None
```

---

### 5. Zero tokens

```text
prompt=0
completion=0
total=0
```

结果：

```text
cost=0
```

---

### 6. Large tokens

验证：

```text
10_000_000+
```

不会 overflow。

---

### 7. Decimal precision

验证：

```text
Decimal
```

结果稳定。

---

### 8. Negative pricing

必须拒绝。

---

### 9. NaN

必须拒绝。

---

### 10. Infinity

必须拒绝。

---

### 11. Boolean

必须拒绝。

例如：

```text
True
```

不能被当成：

```text
1
```

---

### 12. String

例如：

```text
"1.0"
```

按照严格 Contract：

**不要隐式转换。**

---

### 13. Negative token usage

如果进入 Accounting：

必须拒绝。

但是不要修改已有 `LLMUsage` Contract。

---

### 14. Total mismatch

例如：

```text
prompt=100
completion=200
total=999
```

应保持与现有 `LLMUsage` Contract 一致。

不要在 Cost 层修复。

---

### 15. Currency

显式：

```text
USD
```

确认结果保留。

禁止自动转换：

```text
USD → CNY
```

---

### 16. Pure Function

同样输入：

```text
usage
pricing
```

多次调用：

```text
result1 == result2
```

---

### 17. No network

Accounting 测试：

```text
network = 0
```

---

### 18. No DB

Accounting 测试：

```text
DB = 0
```

---

# 二十三、Regression

必须保证：

```text
LLMUsage
LLMResponse
LLMObservation
Provider
Client
RAG
Tool
T2S
Router
Orchestrator
```

现有行为不变。

运行：

```powershell
python -m pytest -q
```

然后：

```powershell
python -m compileall backend tests scripts
```

然后：

```powershell
$env:RUN_DB_TESTS="1"; python -m pytest -q
```

---

# 二十四、禁止事项

本阶段严格禁止：

```text
真实 Provider Pricing
实时价格 API
数据库 Cost 表
Cost Persistence
Billing
Invoice
Dashboard
OpenTelemetry
Prometheus
Grafana
Langfuse
Redis
Kafka
Currency Conversion
Exchange Rate API
```

禁止修改：

```text
Prompt
Validator
Executor
Router
RAG retrieval
Tool definitions
Project Context
T2S semantics
```

禁止增加：

```text
cost
price
currency
```

到：

```text
LLMResponse
LLMObservation
```

---

# 二十五、文档

修改：

```text
docs/architecture.md
```

增加：

```text
§8.9 LLM Token & Cost Accounting Contract
```

必须明确：

```text
LLMResponse
    ↓
LLMUsage
```

与：

```text
LLMUsage + LLMPricing
    ↓
LLMCost
```

是两个不同层次。

并明确：

> 本阶段没有真实 Provider Pricing。

> Cost 计算是纯函数。

> 不进行持久化。

> 不进行账单结算。

> 不进行货币转换。

---

# 二十六、Git Diff

完成后执行：

```powershell
git status --short
git diff --stat
git diff
```

检查：

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
```

---

# 二十七、最终报告

完成后按照以下格式输出：

```text
Phase 3.10.9 完成报告

1. 修改文件

2. LLMUsage Contract

3. Token Accounting Contract

4. Pricing Contract

5. Cost Calculation

6. Partial Usage

7. Decimal / Precision

8. Validation

9. Tests

10. Regression

11. Network / DB

12. Production Path

13. Git Diff

14. 未完成项

Phase 3.10.9 READY

Phase 3.10.9 STOP
```

如果存在任何 Contract regression：

```text
Phase 3.10.9 NOT READY
```

不要通过修改旧测试来隐藏问题。

---

# 二十八、最终验收标准

全部满足才能 READY：

```text
□ LLMUsage 没有被重复定义
□ Token Accounting 与 Provider 解耦
□ Pricing 与 Provider 解耦
□ Cost 与 Observation 解耦
□ LLMResponse Contract 不变
□ LLMObservation Contract 不变
□ Full usage 正确计算
□ Partial usage 不错误补全
□ usage=None → cost=None
□ Zero token 正确
□ Decimal 计算
□ Negative price 拒绝
□ NaN 拒绝
□ Infinity 拒绝
□ bool 拒绝
□ string 隐式转换被拒绝
□ negative token 拒绝
□ total mismatch 遵循既有 Contract
□ currency 显式
□ 不自动 rounding
□ 不真实查询价格
□ 不估算 token
□ 不持久化
□ 无网络
□ 无 DB
□ 全量测试通过
□ DB regression 通过
□ compileall 通过
□ Git diff 干净
```

**完成后立即 STOP。**

不要自动进入 Phase 3.10.10。
