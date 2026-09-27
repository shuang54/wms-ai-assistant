# Phase 3.10.17 — Usage Query Read Boundary

## 1. Query Contract

```text
get_by_request_id(request_id) → LLMUsageRecordView | None
list(...)                     → list[LLMUsageRecordView]
query(filter)                 → list[LLMUsageRecordView]
```

入口：`backend/app/services/llm_usage_query_service.LLMUsageQueryService`

* `get_by_request_id()`：命中返回一条；未命中返回 `None`；
  Phase 3.10.16 的 unique partial index 保证最多 1 行——
  **不使用 `.first()` 静默丢弃多余结果**，命中多行即报
  `LLMUsageRepositoryError`（契约破坏）；
* `list()` / `query()`：返回 `list[LLMUsageRecordView]`；无结果返回 `[]`；
* 不做 `sum_tokens()` / `average_tokens()` / `daily_usage()` /
  `monthly_usage()` / `GROUP BY` 业务统计（Analytics 属于后续阶段）。

---

## 2. Filters

```text
request_id
provider
model
created_at_from
created_at_to
```

组合语义：`WHERE ... AND ...`，**全部由 PostgreSQL 执行**
（不在 Python 里取回全表再过滤）。

不支持 `user_id / project_id / tenant_id / session_id / trace_id /
status / cost / currency / token_range`——表上没有这些字段，
本阶段不为 Query 修改数据库结构。

非法输入在进入数据库之前被拒绝（`LLMUsageQueryInputError`）：
空字符串 / 空白串 / 非字符串、naive datetime、`from > to`。

---

## 3. Pagination

```text
limit:  1 ~ 100，默认 50
offset: >= 0，默认 0
```

`limit = 0 / -1 / 101 / 999999999 / bool / float / str` 全部 reject；
`offset = -1 / bool / str` 全部 reject；`offset` 上限不做限制
（项目暂无统一规范，§十二）。分页由 `Select.limit()` / `.offset()` 实现。

---

## 4. Ordering

```text
ORDER BY created_at DESC, id DESC
```

* 无过滤条件也照样排序 + 分页（默认查全部）；
* `created_at` 相同的记录按 `id DESC` 稳定排序
  （真实 PostgreSQL 测试：req-004 先于 req-003）。

---

## 5. DTO

`LLMUsageRecordView`（`@dataclass(frozen=True)`，沿用项目 DTO 约定，
不引入 Pydantic / 新 DTO framework）：

```text
fields:
    id                int
    request_id        str | None
    provider          str | None
    model             str | None
    prompt_tokens     int | None
    completion_tokens int | None
    total_tokens      int | None
    created_at        datetime（timezone-aware）
```

immutability：

```text
view.provider = "x"   → FrozenInstanceError
view.total_tokens = 1 → FrozenInstanceError
```

security：

```text
不含 Session / Connection / Engine / password / api_key /
authorization / prompt / messages / response / SQL / RAG context /
tool arguments / database URL / cost / price / currency；
ORM 对象不得离开 db 层（Repository 返回内部 LLMUsageRecordRow）。
```

---

## 6. Read-only

Repository 只新增 `SELECT`：

```text
显式 8 列（无 SELECT *）
WHERE / ORDER BY / LIMIT / OFFSET 全部下推 PostgreSQL
读路径 `with factory() as session:`（不 begin 写事务）
```

测试断言（真实 PostgreSQL，8 条 fixture 数据）：

```text
INSERT = 0
UPDATE = 0
DELETE = 0
row_count_before == row_count_after == 8
```

---

## 7. Tests

```text
Empty            = PASS（0 rows → []，get → None）
All              = PASS（8 rows，created_at DESC）
request_id       = PASS（req-003 → exactly 1）
Not Found        = PASS（req-not-exist → None / []）
Provider         = PASS（deepseek → 4 rows）
Model            = PASS（deepseek-chat → 3 rows）
Combined Filter  = PASS（provider + model → 3 rows）
Time Range       = PASS（含 from / to 边界；from > to → reject）
Pagination       = PASS（limit=2 的三页互不重叠；offset=100 → []）
Stable Ordering  = PASS（同 created_at → id DESC）
DTO Security     = PASS（字段白名单 + 无敏感属性）
Immutability     = PASS（FrozenInstanceError）
Session Isolation= PASS（5 queries → 5 独立 Session）
Error Mapping    = PASS（SQLAlchemyError → LLMUsageRepositoryError，原样透传）
Repository SQL   = PASS（显式列 / WHERE 下推 / 稳定排序 / 无 SELECT *）
public schema    = PASS（knowledge_* 前后一致，无新表）
```

执行结果：

```powershell
python -m pytest -q tests/test_llm_usage_query.py
    → 55 passed, 12 skipped（DB 测试需 RUN_DB_TESTS=1）

$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_llm_usage_query.py
    → 67 passed

python -m pytest -q tests/test_llm_usage_persistence.py \
    tests/test_llm_usage_persistence_runtime.py \
    tests/test_llm_usage_persistence_idempotency.py \
    tests/test_llm_usage_query.py
    → 108 passed, 39 skipped

python -m pytest -q
    → 2261 passed, 306 skipped, 0 failed

$env:RUN_DB_TESTS="1"; python -m pytest -q
    → 2526 passed, 41 skipped, 0 failed

python -m compileall backend tests scripts
    → OK（0 diagnostics）
```

---

## 8. Not implemented（本阶段刻意不做）

```text
Usage Query = PASS
Analytics   = NOT IMPLEMENTED
Billing     = NOT IMPLEMENTED
Dashboard   = NOT IMPLEMENTED
HTTP API    = NOT IMPLEMENTED
Permission  = NOT IMPLEMENTED
Queue/Worker/Outbox = NOT IMPLEMENTED
```

---

## 9. Known Limitations

1. 无聚合能力（count/sum/avg/GROUP BY 不作为业务 API）；
2. 无权限过滤（接 HTTP API 时再建边界）；
3. 未为 `provider` / `model` 查询新增索引（先观察真实 Query Pattern）；
4. `request_id IS NULL` 的行只能通过 `provider/model/时间范围` 过滤，
   无法按 request_id 唯一定位（有意设计）；
5. 无 total count 返回（分页只有 `limit/offset`，不做 COUNT 业务 API）。
