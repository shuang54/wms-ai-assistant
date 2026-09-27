# Phase 3.10.20 — Usage Analytics Contract

## 1. Scope

```text
Phase 3.10.20
LLM Usage Analytics Contract
```

```text
Pure in-memory
No DB
No Network
No API
No Dashboard
No Billing
```

在 Query + Aggregation 之上组合 Usage 统计能力；**不是新的数据库层**。

---

## 2. Implementation

```text
LLMUsageAnalyticsService      （新增：组合层）
LLMUsageAnalyticsSnapshot     （新增：快照 DTO）
LLMUsageAnalyticsError        （新增：根异常）
LLMUsageAnalyticsInputError   （新增：输入非法异常）
```

复用：

```text
LLMUsageAggregationService    （3.10.19：sum / count / grouping）
LLMUsageAggregate             （3.10.19：summary 直接复用，不复制 DTO）
ProviderUsageAggregate / ModelUsageAggregate / ProviderModelUsageAggregate
```

明确：**没有重新实现 sum / grouping / NULL 处理 / 排序算法**——
全部委托给 `LLMUsageAggregationService`。

---

## 3. Snapshot

```text
Iterable[LLMUsageRecordView]
    ↓  _materialize（generator 只消费一次）
tuple snapshot
    ↓
summary / provider / model / provider-model（四视图共享同一份快照）
```

generator 只被消费一次：`snapshot()` 先 `tuple(records)` 物化，
四个视图基于同一份不可变快照计算，不会出现"第一次消费后其它视图为空"。

---

## 4. Aggregation Views

```text
Summary         → LLMUsageAggregate
Provider        → tuple[ProviderUsageAggregate, ...]
Model           → tuple[ModelUsageAggregate, ...]
Provider+Model  → tuple[ProviderModelUsageAggregate, ...]
```

---

## 5. Consistency

验证（`_assert_consistent` + 专项测试）：

```text
Σ by_provider.total_requests
= Σ by_model.total_requests
= Σ by_provider_model.total_requests
= total.total_requests
```

token 同理：

```text
Σ by_provider.prompt_tokens = Σ by_model.prompt_tokens
= Σ by_provider_model.prompt_tokens = total.prompt_tokens
（completion / total 同）
```

前提：`None` provider / model 作为独立分组参与统计，不被丢弃。

---

## 6. NULL

```text
NULL ≠ 0
```

* token 为 `None`（未知 / 不可用）→ 不计入求和，也绝不当作 0；
* `*_known = False` 表示：该字段存在 NULL / unknown，
  **当前 sum 只是已知值之和**；
* 分组视图的 `known` 标志由该分组自己的记录决定；
* 例：`100 + None + 200 → prompt_tokens = 300 且 prompt_tokens_known = False`。

---

## 7. Determinism

```text
A B C  与  C A B  → 完全相同的 Analytics Result
```

`summary` / `by_provider` / `by_model` / `snapshot` 均验证输入顺序无关；
分组按 key 升序、`None` 分组在该维度排在最后（复用 3.10.19 排序 Contract）。

---

## 8. Immutability

```text
Snapshot        = frozen
Aggregate DTO   = frozen
Grouping result = tuple
```

`LLMUsageAnalyticsSnapshot`、`LLMUsageAggregate`、
`ProviderUsageAggregate`、`ModelUsageAggregate`、
`ProviderModelUsageAggregate` 全部 `@dataclass(frozen=True)`；
赋值抛 `FrozenInstanceError`；无缓存 / 无 singleton / 无全局可变状态。

---

## 9. Cost

```text
Cost    = NOT INCLUDED
Billing = NOT IMPLEMENTED
```

Analytics 只回答"用了多少"，不回答"花了多少钱"；
不接 `LLMPricing` / `LLMCost` / `calculate_llm_cost()`；
无 price / currency / cost 字段。

---

## 10. Test Result

```text
tests/test_llm_usage_analytics.py
31 passed
0 failed
```

```text
DB Access = 0
Network   = 0
```

纯内存测试，直接构造 `LLMUsageRecordView`，无 RUN_DB_TESTS /
无 SQLAlchemy Session / 无 PostgreSQL。

---

## 11. Necessary Fix

```text
上一轮分块写入遗漏异常类定义：
    LLMUsageAnalyticsError
    LLMUsageAnalyticsInputError
```

修复方式：

```text
仅补全异常类定义（§十六 Contract）
未改变 Analytics 逻辑
```

`__all__` 与 `_materialize()` 引用了这两个异常但类体未写入，导致
测试 import 失败（`ImportError: cannot import name 'LLMUsageAnalyticsInputError'`）；
补全后 31 个测试全部通过。

---

## 12. Current Limitations

```text
仅 Usage Analytics（summary / provider / model / provider+model）
无时间序列
无时间分桶（hour / day / week / month）
无 Cost
无 Billing
无 Dashboard
无 API
无缓存
无增量聚合
```
