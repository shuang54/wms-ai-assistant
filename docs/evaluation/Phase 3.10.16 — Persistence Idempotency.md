# Phase 3.10.16 — Persistence Idempotency

## 1. Goal

```text
request_id-based persistence idempotency
```

解决的问题：

```text
同一个 request_id
重复持久化 N 次
        ↓
PostgreSQL 最终只存在 1 条记录
```

幂等必须是**数据库权威**（PostgreSQL unique partial index），
不是应用内存权威。

---

## 2. Contract

```text
request_id != NULL
→ max 1 row
（INSERT ... ON CONFLICT DO NOTHING）

request_id == NULL
→ no idempotency guarantee
（每次持久化都允许产生新行；不为伪造幂等 key 生成
  UUID / hash / timestamp / provider + model 合成值）
```

* **First-write-wins**：重复 request_id 只允许 `DO NOTHING`，
  禁止 `DO UPDATE`（不得覆盖 provider / model / token）；
* duplicate 不是错误：Repository 返回 `None`，
  Service / Sink 不抛异常、不 retry、不影响 LLM 业务结果；
* 幂等不在 Runtime Bridge / Service / Sink 里：无 dict / set / lock /
  cache / lru_cache / Redis。

---

## 3. Database

| 项 | 值 |
|---|---|
| Index Name | `uq_llm_usage_record_request_id` |
| Schema | `ai_ops` |
| Table | `llm_usage_record` |
| Column | `request_id` |
| Predicate | `request_id IS NOT NULL` |
| Unique | `true` |

实际 DDL（`init_db.ensure_request_id_idempotency_index()` 执行，幂等）：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_usage_record_request_id
ON ai_ops.llm_usage_record (request_id)
WHERE request_id IS NOT NULL;
```

为什么必须显式 DDL：`Base.metadata.create_all(checkfirst=True)` 对
**已存在**的表整体跳过，不会补建后来新增的索引。因此 Model
（`__table_args__`）保证全新库，`init_db()` 保证既有库。

写入语句（`LLMUsageRepository.build_idempotent_insert()`，实测编译结果）：

```sql
INSERT INTO ai_ops.llm_usage_record
    (request_id, provider, model, prompt_tokens, completion_tokens, total_tokens)
VALUES (...)
ON CONFLICT (request_id) WHERE request_id IS NOT NULL
DO NOTHING
RETURNING ai_ops.llm_usage_record.id
```

已有库保护（§二十一）：检测到重复 `request_id` → 明确报告
（示例 request_id + 条数）→ `RuntimeError` 停止；
**不 DELETE / 不 MERGE / 不 UPDATE**，不引入 Alembic / migration。

---

## 4. Sequential Test

```text
3 same request_id
→ 1 row
```

`TestIdempotencyDatabaseBehavior::test_sequential_duplicate_keeps_one_row`

---

## 5. Concurrent Test

```text
5 concurrent same request_id（经 Runtime Bridge → to_thread）
→ 1 row
```

`TestIdempotencyDatabaseBehavior::test_concurrent_duplicate_keeps_one_row`
（断言 PostgreSQL 行数 = 1，而不是只断言 repository 被调用 5 次）

---

## 6. Different IDs

```text
3 request_ids
→ 3 rows
```

`test_different_request_ids_keep_three_rows`（确认不会误判重复）

---

## 7. NULL IDs

```text
3 NULL request_ids
→ 3 rows
```

`test_null_request_ids_keep_three_rows`（NULL 不参与幂等）

---

## 8. First-write-wins

```text
duplicate does not UPDATE existing row
```

`test_first_write_wins_no_update`：

* 第一次：`request_id=chatcmpl-idem-fww`，`10/20/30`，`deepseek-test`；
* 第二次（异步路径）：同 request_id，但 `100/200/300`、
  `another-provider` / `another-model`；
* 结果：仍 1 行，值保持 `10/20/30`、`deepseek-test` / `deepseek-chat`。

---

## 9. Security

```text
persisted fields unchanged
```

`INSERT` 列 + 绑定参数精确等于：

```text
request_id / provider / model / prompt_tokens / completion_tokens / total_tokens
```

（`id` / `created_at` 由数据库产生。）无 prompt / messages / response /
SQL / RAG context / tool arguments / API key / password / authorization /
database URL / cost / price / currency。幂等没有扩大字段集。

---

## 10. Final Result

```text
Sequential Idempotency  = PASS
Concurrent Idempotency  = PASS（5 并发 → 1 row，真实 PostgreSQL）
NULL request_id         = PASS（3 次 → 3 rows）
Different request_id    = PASS（3 个 → 3 rows）
First-write-wins        = PASS（无 UPDATE）
No DO UPDATE            = PASS（编译后 SQL 断言）
Persistence Failure     = PASS（duplicate 不是异常，业务结果不变）
Security whitelist      = PASS
DB Index                = PASS（pg_indexes / pg_index.indisunique）
No app-memory dedupe    = PASS（5 次调用全部抵达 Repository + 静态检查）
No metrics              = PASS（无 duplicate_count / latency / rate）
No new dependency       = PASS（无 Redis / cachetools / Celery）
No new business table   = PASS
public schema unchanged = PASS（knowledge_document / knowledge_chunk 前后一致）
DB writes after cleanup = 0
```

测试执行结果：

```powershell
python -m pytest tests/test_llm_usage_persistence_idempotency.py -q
    → 16 passed, 9 skipped（DB 测试需 RUN_DB_TESTS=1）

$env:RUN_DB_TESTS="1"; python -m pytest tests/test_llm_usage_persistence_idempotency.py -q
    → 25 passed

python -m pytest -q
    → 2206 passed, 294 skipped, 0 failed

$env:RUN_DB_TESTS="1"; python -m pytest -q
    → 2459 passed, 41 skipped, 0 failed

python -m compileall backend tests scripts
    → OK（0 diagnostics）
```

---

## 11. Known Limitations

1. `request_id IS NULL` 无幂等（有意设计）——mock provider / 未返回 id
   的 provider 仍然可能重复落库；
2. 无 conflict resolution：同 request_id 的第二次数据被静默忽略
   （只记录 warning 级日志语义，不做对账）；
3. 无 migration framework：索引由 `init_db()` 幂等 DDL 补齐，
   重复数据需人工处理；
4. 无 persistence metrics（duplicate count / latency / success rate）。
