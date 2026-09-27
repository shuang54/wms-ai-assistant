# Phase 3.10.19 — Usage Aggregation Contract

## 1. Scope

```text
Pure in-memory aggregation
```

* 0 DB / 0 Network / 0 SQL / 0 Session / 0 Repository；
* 不修改数据库、不新增表、不新增字段、不引入依赖；
* Aggregation 不知道 PostgreSQL 的存在。

入口：`backend/app/services/llm_usage_aggregation_service.py`

```text
LLMUsageAggregationService
    ├── aggregate(records)                    → LLMUsageAggregate
    ├── aggregate_by_provider(records)        → tuple[ProviderUsageAggregate, ...]
    ├── aggregate_by_model(records)           → tuple[ModelUsageAggregate, ...]
    └── aggregate_by_provider_model(records)  → tuple[ProviderModelUsageAggregate, ...]
```

---

## 2. DTO

```python
@dataclass(frozen=True)
class LLMUsageAggregate:
    total_requests: int
    prompt_tokens: int            # 已知值之和
    completion_tokens: int
    total_tokens: int             # 独立观测值求和（不重算）
    prompt_tokens_known: bool     # True = 该列全部已知
    completion_tokens_known: bool
    total_tokens_known: bool
```

分组 DTO（frozen tuple，不对外暴露裸 dict）：

```text
ProviderUsageAggregate(provider: str | None, aggregate)
ModelUsageAggregate(model: str | None, aggregate)
ProviderModelUsageAggregate(provider: str | None, model: str | None, aggregate)
```

`None` 维度构成独立分组（原样保留，不合并成 "unknown" 字符串）。

---

## 3. Basic aggregation

```text
3 records（§六示例）
    prompt     = 100 / 200 / 300
    completion =  50 / 100 / 200
    total      = 150 / 300 / 500
→ total_requests = 3
→ prompt_tokens = 600
→ completion_tokens = 350
→ total_tokens = 950
```

* 空 `[]` → `total_requests = 0`、token 和 0、`*_known = True`
  （合法结果，不 None、不抛异常）；
* 单条记录 → requests = 1、tokens = 原值；
* 支持 list / tuple / generator 输入。

---

## 4. Grouping

```text
provider          → deepseek / openai / ...（含 None 独立分组）
model             → deepseek-chat / gpt-4o / ...
provider + model  → (deepseek, deepseek-chat) / ...
```

内部允许 dict，对外只返回**排序后的 frozen tuple**。

---

## 5. NULL semantics

```text
NULL ≠ 0
```

* token = `None`（未知 / 不可用）→ **不计入求和，也绝不当作 0**；
* `100 + NULL + 200 → prompt_tokens = 300 且 prompt_tokens_known = False`
  （未知信息通过 `*_known` 显式保留，不丢失）；
* `*_known = True` → 该列所有记录均已知，求和覆盖全部记录；
* 显式 `0` 是已知值（与 None 语义不同）；
* 全部为 NULL 的列 → 和为 0 且 `*_known = False`。

---

## 6. Ordering

```text
分组按 key 升序；None 分组在该维度排在最后
provider+model → (provider ASC, model ASC)
```

* 排序键同时携带值本身作为 tiebreaker（否则非 NULL key 全部同序，
  会退化为 dict insertion order）；
* 输入顺序无关：`A B C` 与 `C A B` 得到完全相同的聚合结果
  （aggregate 与三种分组均验证）；
* 同一输入恒得同一输出（deterministic）。

---

## 7. Cost

```text
NOT INCLUDED
```

* 不接 `LLMPricing` / `LLMCost` / `calculate_llm_cost()`；
* Aggregate 无 `currency / price / cost` 字段；
* 不做 `Usage × Price`，不默认任何 provider 价格；
* Billing 属于后续独立阶段。

---

## 8. Database

```text
DB access = 0
DB writes = 0
```

* 测试全部直接构造 `LLMUsageRecordView`（无需 RUN_DB_TESTS）；
* 静态检查：模块不 import sqlalchemy / psycopg / redis / celery /
  kafka / backend.app.db / backend.app.llm；代码标识符无
  session / execute / engine / currency / price / cost；
* 不去重：两条完全相同的记录 → `total_requests = 2`。

---

## 9. Tests

```powershell
python -m pytest -q tests/test_llm_usage_aggregation.py
    → 23 passed

python -m pytest -q tests/test_llm_usage_query.py tests/test_llm_usage_query_runtime.py tests/test_llm_usage_aggregation.py
    → 103 passed, 19 skipped

python -m pytest -q
    → 2309 passed, 313 skipped, 0 failed

$env:RUN_DB_TESTS="1"; python -m pytest -q
    → 2581 passed, 41 skipped, 0 failed

python -m compileall backend tests scripts
    → OK（0 diagnostics）
```

覆盖清单（§十五）：

```text
Empty                    = PASS
Single record            = PASS
Multiple records         = PASS
NULL token（None ≠ 0）   = PASS
Provider grouping        = PASS
Model grouping           = PASS
Provider+Model grouping  = PASS
Input order independence = PASS
Duplicate records        = PASS（不去重）
Immutability             = PASS
total 独立求和（§十三）  = PASS
非法输入                 = PASS（LLMUsageAggregationInputError）
无 DB / SQL / Cost 依赖  = PASS（AST 静态检查）
```

---

## 10. 未修改项

```text
Repository / Query Service / Query Runtime = 未修改
Persistence / Idempotency / Accounting / Observation = 未修改
LLM Client / RAG / Router / Tool / Text-to-SQL = 未修改
无新表 / 无新字段 / 无新依赖 / 无 API / 无 Dashboard
```

---

## 11. Known Limitations

1. 只有 sum / count（无 avg / min / max / 百分位）；
2. 分组维度仅 provider / model（表上没有 user / project / 时间分桶字段）；
3. 无缓存 / 无增量更新（每次全量重算，纯函数）；
4. 无 Cost（后续 Billing 阶段再接 `LLMPricing`）；
5. `None` 维度分组排在最后是本 Contract 的约定（不是字典序）。
