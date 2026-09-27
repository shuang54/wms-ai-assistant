# Phase 3.10.18 — Usage Query Async Runtime

## 1. Goal

```text
Prevent synchronous Usage Query DB I/O from blocking async event loop
```

不改造数据库体系：Repository 仍同步、驱动仍 psycopg，只新增一层
**线程边界**（与 Phase 3.10.15 Persistence Runtime 同构）。

---

## 2. Architecture

```text
Async Caller
    ↓
Query Runtime Bridge（backend/app/services/llm_usage_query_runtime.py）
    ↓
asyncio.to_thread()
    ↓
Query Service（同步，def 不变）
    ↓
Repository（同步 SQLAlchemy，SQL 不变）
    ↓
PostgreSQL
```

```text
Bridge API:
    query_async(filter=None)
    list_records_async(...)
    get_by_request_id_async(request_id)
```

* 不写 SQL、不持有 Session、不感知 Repository；
* 同步 `LLMUsageQueryService.query()` / `list_records()` /
  `get_by_request_id()` 保持 `def`（CLI / Test / sync code 可直接用）；
* 无 AsyncEngine / AsyncSession / async_sessionmaker / asyncpg；
* 无 `create_task` / `ensure_future` / fire-and-forget。

---

## 3. Event Loop

复用 Phase 3.10.15 已有的 heartbeat helper（未新建 benchmark 框架）：

| 场景 | heartbeat tick | 最大 gap | 结论 |
|---|---|---|---|
| 直接同步查询（基准，200ms） | 极少 | ≥ 0.15s | **BLOCKING** |
| `query_async()`（Runtime Bridge） | 持续推进（≥ 4） | < 0.1s | **PASS** |

```text
Direct Sync Query = BLOCKING
Runtime Bridge    = PASS
event loop blocking = PASS
```

---

## 4. Concurrency

```text
5 async queries = PASS
```

`asyncio.gather` 5 次 `get_by_request_id_async()`：5 个结果，
`request_id / provider / model / total_tokens` 一一对应，无串线；
并发查询全部在 worker 线程执行（不在 event loop 线程）。

---

## 5. Session Isolation

```text
5 queries
→ 5 Sessions（互不相同）
→ 5 closed Sessions
```

Session 由 Repository 在 worker 线程内部 `session_factory()` 创建，
`WITH factory() as session:` 用完即关；不在 event loop 线程创建，
不跨线程 / 跨 task 共享。

---

## 6. Error

```text
Query Error Propagation = PASS
```

* `LLMUsageRepositoryError` 原样传播到 awaiter；
* 参数非法（`LLMUsageQueryInputError`）原样传播；
* 仓储抛出底层异常时同样冒泡；
* **禁止** catch → `return []`，禁止降级为 warning
  （Query 是显式读操作，失败必须可观察；与 Persistence 的
  best-effort 语义刻意不同）。

---

## 7. Cancellation

实际验证出的 Contract：

```text
Query cancellation → 允许向 caller 传播
```

测试：query 运行期间 `task.cancel()` → `CancelledError` 传播给调用方
（`"cancelled"`），**不**复制 Persistence 的 absorb 语义；
取消后 worker 内已创建的 Session 正常关闭（无残留状态）。

理由：Query 是主动读取请求，调用方取消即不需要结果；
Persistence 是已发生事实的记账，因此两者语义刻意不同。

---

## 8. Security

```text
DTO unchanged
```

* 返回仍然是 `LLMUsageRecordView`（frozen dataclass）；
* 字段仍为 `id / request_id / provider / model / prompt_tokens /
  completion_tokens / total_tokens / created_at`（timezone-aware）；
* Bridge 不新增任何字段；无 Session / Connection / Engine /
  SQLAlchemy Row / ORM Model / password / api_key / authorization /
  prompt / messages / SQL / RAG / tool / database_url / cost /
  price / currency；
* 无全局 Session / 结果缓存 / LRU / dict / set。

---

## 9. DB Writes

```text
INSERT = 0
UPDATE = 0
DELETE = 0
```

* Fake 测试：只读仓储的 `create()` 被调用即失败（从未被调用）；
* 真实 PostgreSQL：async get / list / filter / pagination /
  ordering / 5 并发查询后 `row_count_before == row_count_after`；
* 测试结束 `TRUNCATE ai_ops.llm_usage_record` → residue = 0。

---

## 10. Tests

```powershell
python -m pytest -q tests/test_llm_usage_query_runtime.py
    → 25 passed, 7 skipped（DB 测试需 RUN_DB_TESTS=1）

$env:RUN_DB_TESTS="1"; python -m pytest -q tests/test_llm_usage_query_runtime.py
    → 32 passed

python -m pytest -q tests/test_llm_usage_query.py tests/test_llm_usage_query_runtime.py
    → 80 passed, 19 skipped

python -m pytest -q tests/test_llm_usage_persistence.py \
    tests/test_llm_usage_persistence_runtime.py \
    tests/test_llm_usage_persistence_idempotency.py \
    tests/test_llm_usage_query.py tests/test_llm_usage_query_runtime.py
    → 133 passed, 46 skipped

python -m pytest -q
    → 2286 passed, 313 skipped, 0 failed

$env:RUN_DB_TESTS="1"; python -m pytest -q
    → 2558 passed, 41 skipped, 0 failed

python -m compileall backend tests scripts
    → OK（0 diagnostics）
```

未重复 Phase 3.10.17 的 filter validation / DTO security /
SQL compilation 测试（§二十六）。

---

## 11. 未修改项（本阶段刻意不动）

```text
Repository SQL / 显式 8 列 / ORDER BY / request_id 唯一契约 = 未修改
LLMUsageQueryFilter / LLMUsageRecordView Contract = 未修改
Persistence / Runtime / Idempotency / LLM Client / Observation /
Accounting / RAG / Router / Tool / T2S / Validator / Executor = 未修改
无新表 / 无新字段 / 无新依赖 / 无 API / 无 Dashboard / 无 Metrics
```

---

## 12. Known Limitations

1. 仍是"调用边界 async"，数据库 I/O 本身是同步 + 线程池
   （线程池为 asyncio 默认 ThreadPoolExecutor，未做池调优）；
2. 取消后 worker 线程内的查询会执行完（无法中断 PostgreSQL 查询），
   结果被丢弃；
3. 未做查询结果缓存（本阶段明确不做）；
4. 无连接级背压 / 并发上限控制（未引入 Queue / Worker）。
