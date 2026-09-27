# Phase 3.10.15 — LLM Usage Persistence Runtime Hardening

> 目标：验证并最小化 LLM Usage Persistence 对**异步业务请求链路**
> 的运行时影响。不重新设计 Persistence，不引入消息队列，
> 不实现异步数据库体系。

---

## 1. Runtime Problem

Phase 3.10.14 的持久化链路是正确的，但存在一个明确的运行时限制：

```text
async LLM request (OpenAICompatibleClient.chat)
      ↓ _observe_call → _emit_accounting
      ↓ sink.record(observation)
同步 SQLAlchemy DB write      ← 直接跑在 event loop 线程
      ↓
可能短暂阻塞 event loop
```

确认方式（读代码 + 测试实证）：

* `OpenAICompatibleClient.chat()`（`backend/app/llm/client.py`）是
  `async def`，内部在 HTTP 完成后直接调用 `_emit_accounting()`；
* `_emit_accounting()` 里 `accounting_sink.record(observation)` 是同步调用，
  `DatabaseLLMAccountingSink` → `LLMUsagePersistenceService.persist()` →
  `LLMUsageRepository.create()` → `with factory() as session, session.begin():`
  全是同步 SQLAlchemy；
* 结论：**是** —— 同步 DB 写入发生在 async 请求的 event loop 线程。

---

## 2. Solution

最小 runtime adapter（`asyncio.to_thread`，项目已在
`SqlExecutorService` / `get_inventory` 使用同一边界）：

```text
Async LLM Request
        ↓
DatabaseLLMAccountingSink.arecord()          （await，非 fire-and-forget）
        ↓
LLMUsagePersistenceRuntimeBridge.persist_async()
        ↓  await asyncio.to_thread(service.persist, observation)
worker thread
        ↓  session_factory() → 新 Session → commit / rollback
ai_ops.llm_usage_record
```

实际落地的三处改动：

| 位置 | 改动 |
|---|---|
| `backend/app/services/llm_usage_persistence_runtime.py` | 新增：唯一线程边界（`asyncio.to_thread` 包一层同步 persist） |
| `backend/app/services/llm_usage_persistence_service.py` | `DatabaseLLMAccountingSink` 新增 optional `arecord()`；`record()` 契约与语义完全不变 |
| `backend/app/llm/client.py` | `_emit_accounting` / `_observe_call` 改为 coroutine：sink 有 `arecord()` 则 await，否则退回原同步 `record()` |

不改的东西：Repository（仍同步）、Persistence Service 逻辑、
字段白名单、Retry 语义、默认 `NoopAccountingSink`、
`MockLLMClient` 行为、RAG / T2S / Tool / Router / Orchestrator /
Validator / Executor / Prompt / Schema Explorer。

---

## 3. Event Loop Test

测试：`tests/test_llm_usage_persistence_runtime.py::TestEventLoopRemainsSchedulable`
（Fake Repository，`time.sleep(0.2)` 模拟 DB 写入；heartbeat 间隔 20ms）。

| 场景 | heartbeat tick 数 | 最大 tick 间隔 | 结论 |
|---|---|---|---|
| 同步持久化直接跑在 event loop（基准） | 极少（写入完成后才恢复） | ≥ 0.15s | 阻塞明显 |
| runtime bridge（`asyncio.to_thread`） | 多个 tick 持续推进 | < 0.1s | **不阻塞** |

```text
blocking = PASS
metrics  = heartbeat ticks + max gap（轻量级验证，非 benchmark）
```

---

## 4. Concurrency

```text
5 concurrent LLM requests
5 observations
5 persistence operations
5 correct request_id
5 correct usage
cross contamination = none
```

覆盖：

* `TestConcurrency::test_five_concurrent_requests_five_persistence_ops`
* `TestConcurrency::test_concurrent_persistence_never_runs_on_loop_thread`
* DB：`TestRuntimeBridgeWithDatabase::test_five_concurrent_persistence_rows`
  （真实 PostgreSQL，5 rows）

---

## 5. Session Isolation

```text
shared session = false
global Session / global Connection / global transaction = none
```

* `TestSessionIsolation` 用 TrackingSessionFactory 证明：每次持久化
  在 worker 线程内创建**新的** Session，用完即关闭；
  一个 Session 只写一行，5 次 → 5 个互不相同的 Session；
* 静态检查（AST）：client / runtime bridge / persistence service /
  repository 四个模块均无模块级 Session / Connection / Transaction，
  且线程边界（`to_thread` 调用）只出现在 runtime bridge。

---

## 6. Failure Isolation

```text
DB failure
→ business result unchanged
→ no LLM retry
→ no persistence retry
```

`TestPersistenceFailure`：repository 抛错时

* `chat()` 返回值不变（`"business answer"`）；
* HTTP 调用次数 = 1（LLM retry = 0）；
* repository 调用次数 = 1（persistence retry = 0）；
* 非仓储类异常（`RuntimeError`）同样被边界收敛，不外泄。

---

## 7. Retry

```text
LLM retry        = 0
Persistence retry = 0
backoff / sleep / queue = none
```

持久化不进入 LLM transport retry、Text-to-SQL semantic retry、
任何 attempts 计数；T2S retry 的 2 次请求仍然产生 2 条 usage 记录
（既有 `tests/test_llm_usage_persistence.py` 验证，本阶段未改动）。

---

## 8. Cancellation

明确语义：

* 持久化窗口内的 caller cancellation 由 persistence boundary 吸收
  （`CancelledError` → warning 后返回）；
* 因此**已经完成的** LLM Business Result 不会被持久化取消破坏
  （测试：`TestCancellation::test_cancellation_does_not_destroy_business_result`）；
* worker 线程在自己的独立事务里结束（独立 Session，无共享资源）。

Known Limitation（不扩大设计）：

* 持久化仍在业务返回之前被 await —— 本阶段改善的是"不占用 event loop
  线程"，而不是"从关键路径移除"；后者需要 queue / worker / outbox，
  属于本阶段明确禁止的基础设施。

---

## 9. Security

字段白名单保持不变（两处流入 Repository 的字段集完全一致）：

```text
request_id / provider / model / prompt_tokens / completion_tokens / total_tokens
（id / created_at 由 DB 产生）
```

仍然不落库：prompt / messages / system_prompt / SQL / RAG content /
tool args / tool result / raw response / headers / API key / password /
authorization / database URL / exception message / stack trace /
cost / price / currency。

`backend/app/llm/observability.py` 仍然不依赖 DB
（AST 检查：无 sqlalchemy / psycopg / db / repository import）。

---

## 10. Default Production Path

```text
create_llm_client()
    → NoopAccountingSink        （默认不变）

普通开发运行
    → 不写 ai_ops.llm_usage_record
    → 不触发任何线程边界

显式 DatabaseLLMAccountingSink
    → 才启用持久化
```

`MockLLMClient` 行为不变（仍不伪造 usage / request_id / model / cost）。

---

## 11. Tests

```powershell
python -m pytest tests/test_llm_usage_persistence_runtime.py -q
    → 37 passed, 1 skipped（DB 测试需 RUN_DB_TESTS=1）

python -m pytest -q
    → 2190 passed, 285 skipped, 0 failed   （baseline 2153 / 284）

$env:RUN_DB_TESTS="1"; python -m pytest -q
    → 2434 passed, 41 skipped, 0 failed    （baseline 2396 / 41）

python -m compileall backend tests scripts
    → OK
```

新增测试文件：`tests/test_llm_usage_persistence_runtime.py`
（覆盖任务书 §二十九 全部 15 项 + DB 并发回归）。未删除或修改既有测试。

---

## 12. DB Residue

```text
0 rows
```

DB 测试自带 `TRUNCATE ai_ops.llm_usage_record RESTART IDENTITY CASCADE`，
全量 DB 回归后实际查询：

```text
select count(*) from ai_ops.llm_usage_record → 0
```

---

## 13. Known Limitations

1. 持久化仍在 return 前 await（不占用 event loop，但仍在关键路径上）；
2. 线程池为 `asyncio` 默认 ThreadPoolExecutor，未做池大小调优
   （本阶段不做 queue / worker）；
3. 无幂等保证（与 Phase 3.10.14 一致）；
4. 未提供 persistence 级别的可观测指标（不做 Prometheus / tracing）。
