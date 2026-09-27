# Phase 3.10.21 — Usage Analytics Read Facade

## 1. Scope

```text
Phase 3.10.21
Usage Analytics Application Read Facade
```

Pure in-memory composition；No DB / No Network / No API /
No Dashboard / No Billing；不实现任何新的聚合算法。

---

## 2. Existing Implementation

`backend/app/services/llm_usage_analytics_facade.py`（以真实代码为准）：

```text
LLMUsageAnalyticsReadFacade(query_runtime, analytics_service=None)

async query_snapshot(query_filter=None)   → LLMUsageAnalyticsSnapshot
async query_summary(query_filter=None)    → LLMUsageAggregate
async query_by_provider(query_filter=None)
async query_by_model(query_filter=None)
async query_by_provider_model(query_filter=None)

properties: query_runtime / analytics_service
```

构造校验：`query_runtime` 为 None 或缺少 `query_async()` → `TypeError`；
`analytics_service=None` → 默认 `LLMUsageAnalyticsService()`。

复用（未修改）：`LLMUsageQueryRuntimeBridge` / `LLMUsageAnalyticsService` /
`LLMUsageQueryFilter` / 既有 frozen DTO
（`LLMUsageAggregate` / `ProviderUsageAggregate` / `ModelUsageAggregate` /
`ProviderModelUsageAggregate` / `LLMUsageAnalyticsSnapshot`）。

---

## 3. Application Read Boundary

```text
Query Repository         → 数据库读取
Query Service            → Query Contract / Filter / Pagination
Query Runtime            → async → sync DB boundary
Analytics Service        → Analytics / Aggregation composition
Analytics Read Facade    → Application Read Boundary（只组合，不计算）
```

Facade 不负责数据库访问：

```text
禁止：Facade → Repository → SQLAlchemy Session
禁止：Facade 直接使用 SQL / Engine / Connection / PostgreSQL
```

---

## 4. Query → Analytics Composition

```text
Facade
    ↓ await query_runtime.query_async(query_filter)   （Runtime = 1 次）
records: list[LLMUsageRecordView]
    ↓ analytics_service.snapshot(records)
LLMUsageAnalyticsSnapshot
```

Facade 不复制 Query Service / Aggregation Service / Analytics Service
的任何逻辑（无 `tuple(records)` 重复实现 Snapshot）。

---

## 5. Snapshot

```text
query_snapshot() 只执行 1 次 Runtime 查询
    ↓
LLMUsageAnalyticsSnapshot（frozen）
    ├── total              LLMUsageAggregate
    ├── by_provider        tuple[ProviderUsageAggregate, ...]
    ├── by_model           tuple[ModelUsageAggregate, ...]
    └── by_provider_model  tuple[ProviderModelUsageAggregate, ...]
```

**不是四次数据库查询。** Snapshot 物化（`Iterable → tuple`）
的唯一实现仍在 `LLMUsageAnalyticsService`，Facade 无 `tuple(records)`。

---

## 6. Pagination Semantics

```text
Facade Analytics = 当前 Query Filter 对应记录集合的 Analytics
```

* `limit = 100` → Analytics 覆盖查询得到的 100 条记录，
  **不是全库全局统计**；
* 本阶段没有 `COUNT(*)` / `global_total` / `total_count`；
* `LLMUsageQueryFilter` 原样传递（含 limit / offset），
  无第二套 FacadeFilter / FacadePageSize / DEFAULT_PAGE_SIZE。

---

## 7. Convenience Methods

```text
query_summary() / query_by_provider() / query_by_model() /
query_by_provider_model()
```

属于 **convenience read methods**（各自一次 Runtime 查询，
委托 Analytics Service 对应方法）。
完整 Analytics 推荐入口：`query_snapshot()`——
一次 Query 即可得到完整四视图。

---

## 8. Error Propagation

```text
Runtime error   → Facade → caller（原样）
Analytics error → Facade → caller（原样）
```

Facade 不吞异常、不包装、不转换成 None。

---

## 9. Immutability

```text
LLMUsageAnalyticsSnapshot        = frozen
LLMUsageAggregate                = frozen
ProviderUsageAggregate           = frozen
ModelUsageAggregate              = frozen
ProviderModelUsageAggregate      = frozen
分组结果                          = tuple
```

Facade 原样返回既有 frozen DTO / tuple，
绝不重新包装成 dict / list 可变结构。

---

## 10. Security Boundary

Facade 返回的数据仍只能是 §8.19 的 Usage 白名单字段
（`id / request_id / provider / model / prompt_tokens /
completion_tokens / total_tokens / created_at`），不包含：

```text
API Key / Password / Database URL / Connection String /
Authorization Header / SQLAlchemy Session / Connection /
Engine / Raw SQL
```

Facade 只是 application read boundary；
测试使用 AST Name/Attribute 检查（非全文扫描）确认
无 DB / 基础设施 import、无第二套分页常量、
无 Session / Engine / SQL 标识符。

---

## 11. Tests

```text
Facade tests:
    python -m pytest -q tests/test_llm_usage_analytics_facade.py
    → 19 passed, 0 failed

Facade + Analytics + Aggregation:
    → 73 passed, 0 failed
```

（真实结果，Step 1 已验证；无其它编造的测试数字。）

---

## 12. DB / Network

```text
DB Access = 0
DB writes = 0
Network   = 0
```

Step 1 使用 Fake / Stub Runtime 进行 Application Composition 验证；
没有 PostgreSQL / SQLAlchemy Session / Repository / LLM API / HTTP。

---

## 13. Files

```text
新增：backend/app/services/llm_usage_analytics_facade.py
新增：tests/test_llm_usage_analytics_facade.py
文档：docs/architecture.md（§8.21）
文档：docs/evaluation/Phase 3.10.21 — Usage Analytics Read Facade.md
```

文件来源说明：本阶段开始时发现 Facade implementation 与其测试文件
已经存在于工作区。本 Step 未重新生成或修改 Python 文件，
仅基于真实代码进行验证与文档化。

---

## 14. Limitations

```text
1. 尚无 HTTP API
2. 尚无 Dashboard
3. 尚无 Billing / Cost
4. 尚无 Cache
5. 尚无 Queue / Worker
6. Facade 仍然是内部 Application Boundary
7. Pagination Analytics 代表当前查询页，而非全局 Analytics
```