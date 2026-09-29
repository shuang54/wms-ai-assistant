# Phase 3.12 Step 57 — LLM Usage Production DB Safety Audit

> Audit + 最小验证。**生产代码改动 = 0**（未发现 Step 56 安全 bug）·
> DB Schema 改动 = 0 · 未新增 index / migration / runtime config / 连接池优化。
> 未做压力测试 / benchmark / load test；未连接真实生产库。
> 新增：`tests/test_llm_usage_production_db_safety.py`（10 离线 + 3 DB-gated）。

---

## 1. Connection Lifecycle（真实源码链路）

```text
LLM request（业务链路，已完成响应）
    ↓  client.py :: LLMClient._observe_call → _emit_accounting
sink = DatabaseLLMAccountingSink（**进程级单例**，Step 56）
    ↓  arecord()（可选异步入口；getattr 探测）
LLMUsagePersistenceRuntimeBridge.persist_async()
    ↓  await asyncio.to_thread(service.persist, observation)     ← 线程边界
worker thread
    ↓
LLMUsagePersistenceService.persist()（usage=None → 不写入、不建 Session）
    ↓  assistant_request_id = current_assistant_request_id()
LLMUsageRepository.create()
    ↓  factory = self._get_session_factory()（sessionmaker，进程级缓存）
    ↓  with factory() as session, session.begin():
Session（**每次记录新建**）
    ↓  BEGIN（session.begin() 上下文）
INSERT ... ON CONFLICT DO NOTHING  →  RETURNING id
    ↓  COMMIT（块尾；异常 → ROLLBACK）
Session.close()（with 退出）
    ↓
Connection returned to QueuePool（已归还；实测 checkedout() 回落）
```

## 2. Session Lifecycle

```text
成功：Session created → begin → INSERT → COMMIT → close   （实测：began=1, committed=1, closed=1）
失败：Session created → begin → INSERT 抛 SQLAlchemyError → ROLLBACK → close
      （实测：rolled_back=1, closed=1；随后抛 LLMUsageRepositoryError）
per-record：连续 3 次记录 → 3 个不同 Session（len({id(s)}) == 3）· 全部 closed=1
无 global Session：静态检查（本阶段新增 + 既有 TestNoSharedRuntimeResources）——
    persistence service / runtime bridge / repository / client 内均无
    sessionmaker( / create_engine( / SessionLocal = / global_session
无 Session 泄漏：Session 关闭数 == 创建数（每次尝试）；sink 上不存在
    _session / _engine / _connection / _transaction 属性
```

## 3. Transaction Lifecycle

```text
边界：`with factory() as session, session.begin():` —— **单语句隐式事务**
    * 从上下文进入即 BEGIN，块正常结束自动 COMMIT；
    * 块内异常自动 ROLLBACK（无手工 commit / rollback / 嵌套事务）；
    * 事务只在一次 INSERT 期间存在（毫秒级）⇒ **无长事务 / 无跨请求事务**
    * 幂等冲突（ON CONFLICT DO NOTHING）走正常提交路径（返回 None，非错误）
```

## 4. Pool Configuration（运行期只读实测）

```text
Engine（backend/app/db/session.py :: _build_engine）
    create_engine(url, echo=settings.database.echo, pool_size=…,
                  max_overflow=…, pool_pre_ping=True, future=True)

实测（QueuePool）：
    pool_size        = 5         （settings.database.pool_size）
    max_overflow     = 10        （settings.database.max_overflow）
    pool_pre_ping    = True
    pool_timeout     = 30.0 s    （**SQLAlchemy 默认**；未显式配置）
    pool_recycle     = -1        （**未配置** → SQLAlchemy 默认）
    echo             = False

服务器侧（只读）：max_connections = 100 · state='idle in transaction' = 0
                 · 应用连接数 = 1（空闲时）· pool.checkedout() = 0（无泄漏）

本阶段**未新增** pool_timeout / pool_recycle 配置（静态断言：
session.py 不含这两个标识符）。
```

## 5. Concurrency Analysis（**静态**；无压力测试）

```text
前提事实：
    * 每次 LLM 调用 → 最多 1 次 accounting 尝试（await，**非** fire-and-forget）
    * 每次尝试 = 1 个 Session = 最多 1 个连接，持有时长 = 1 次 INSERT 往返
    * 同步 INSERT 在 `asyncio.to_thread` 的 worker 线程执行 → **不阻塞 event loop**
    * 池上限：5（常驻）+ 10（overflow）= **15 并发连接**；超出 → 等待 pool_timeout=30s

N 并发 /api/ai/chat 的最大 DB 连接压力 = min(N, 15)（当 N > 15 → 最多 15 个在用，
其余尝试在池中排队）：
    * N ≤ 15：无排队（假定其它路径不占用池）
    * 15 < N ≤ 30 左右：部分 accounting 尝试等待；每次持有时长极短 → 通常瞬时释放
    * N 很大 或 DB 慢 / 被其它路径（Tool / RAG / Trace 读 / 业务 SQL）占用：
      等待可达 **pool_timeout = 30s**

20 并发（静态回答）：
    是否排队        → 是（>15，最多 5 个尝试排队等待）
    是否等待 pool   → 是（等待时长取决于并发持有时长）
    是否超时        → 若等待 > 30s → 抛 sqlalchemy.exc.TimeoutError
    是否阻塞 event loop → **否**（写入在 worker 线程；主协程只是 await 该尝试）
    是否 connection exhausted → 可能，但**后果仅为 accounting 失败**（warning，
      业务结果不受影响；库存/LLM 语义不变）
    服务器侧安全边际 → 15 ≪ max_connections=100（单实例）

无法从仓库确定的部分（标注）：**需要运行环境实际配置确认**
    * 生产实例数 / 每实例 worker 进程数（多实例共享同一 PostgreSQL 时 15×instances 才是真实上限）
    * 其它路径（Text-to-SQL 执行、Tool handler、Assistant Trace 读）同时占用池的比例
    * `asyncio.to_thread` 的默认 ThreadPoolExecutor 上限（CPython 默认
      min(32, cpu+4)）在真实并发下的排队表现
```

## 6. Failure Isolation（DB 慢 / 失败不影响 LLM 语义）

| 失败形态 | 异常来源 | 归因 | 是否影响 LLM 业务 | 是否重试 |
| --- | --- | --- | --- | --- |
| 连接超时（不可达 DSN） | psycopg OperationalError | `LLMUsageRepositoryError` | ✗（实测：LLM 成功 + 1 次调用 + warning） | ✗ |
| 连接被断（broken connection） | SQLAlchemyError | `LLMUsageRepositoryError` | ✗ | ✗ |
| INSERT 失败 / 约束冲突 | SQLAlchemyError | `LLMUsageRepositoryError`（事务已回滚） | ✗ | ✗ |
| 事务失败 | SQLAlchemyError | 同上（自动 ROLLBACK） | ✗ | ✗ |
| **池超时**（pool_timeout） | `sqlalchemy.exc.TimeoutError`（⊂ SQLAlchemyError） | `LLMUsageRepositoryError` | ✗ | ✗ |
| 观测/记账 sink 自身异常 | 任意 Exception | sink 收敛 warning | ✗ | ✗ |
| 事件循环取消（CancelledError） | asyncio | `arecord` 单独捕获 → warning | ✗ | ✗ |

```text
收敛层级（四层，均未被本阶段修改）：
    ① build_llm_observation_safe  → warning + return
    ② LLMClient._emit_accounting  → try/except → warning
    ③ DatabaseLLMAccountingSink   → record / arecord 各自 try/except → warning
    ④ LLMUsageRepository          → SQLAlchemyError → LLMUsageRepositoryError（ROLLBACK）
结论：**所有已知 DB 失败路径均已隔离**，未发现未隔离路径（无需修改生产代码）。
```

## 7. Idempotency

```text
Repository.create()：INSERT ... ON CONFLICT DO NOTHING RETURNING id
唯一约束（实测 DDL）：
    uq_llm_usage_record_request_id
        UNIQUE btree (request_id) WHERE request_id IS NOT NULL
同 provider request_id 两次持久化：
    → 第 2 次 INSERT 被忽略 → 返回 **None**（正常幂等结果，非错误）
    → 不抛异常 → 不影响 LLM 业务结果
    → DB 仍只有 **1 行**（first-write-wins；实测：2 次 /api/ai/chat 相同 provider id → 1 行）
    → 第 2 次的 Assistant Trace 的 llm_usage 为 []（冲突被忽略的可见后果，符合契约）
进程内不做去重（无 dict / set / lock / cache）——幂等完全由 PostgreSQL 保证
```

## 8. assistant_request_id Boundary

```text
request_id            = **Provider 请求 ID**（幂等键；参与唯一约束）
assistant_request_id  = **Assistant Trace 关联 ID**（仅关联，**不参与幂等**）
实测索引：
    ix_llm_usage_record_assistant_request_id → **非唯一** btree (assistant_request_id)
断言：所有 UNIQUE 索引中都不含 assistant_request_id（本阶段新增 DB 测试）
⇒ 未新增 unique constraint；未把关联键变成幂等键。
```

## 9. No DATABASE_URL Behavior（Step 56 行为保持）

```text
DATABASE_URL 未配置 → get_engine() is None → 默认 Client 使用 **NoopAccountingSink**
    · 无 DB 连接 · 无 usage 持久化 · 无警告噪声 · LLM 正常工作
    （Step 56 已测：test_no_db_environment_keeps_noop；本阶段静态断言接线只在默认 Client）
本阶段未新增任何配置项 / 环境变量。
```

## 10. Singleton / Reset

```text
长生命周期（进程级）：default LLMClient · default accounting sink
短生命周期（每次记录）：Session · Transaction · Connection（见 §1~§3）
reset_default_llm_client() → 同时清空 client 与 sink 单例（下次访问重建）
    · 旧 sink 不会被 accessor 返回；旧 Session / 旧 engine **从不被 sink 持有**
engine 缓存独立（db.session.reset_engine_cache()，测试辅助）
实测（Step 56 + 本阶段）：client/sink 身份稳定；reset 后身份变化且新 Client 绑定新 sink
```

## 11. Security

```text
ai_ops.llm_usage_record 列（实测 9 列，顺序一致）：
    id · request_id · provider · model · prompt_tokens · completion_tokens ·
    total_tokens · created_at · assistant_request_id
不写入：prompt · messages · system_prompt · tool_calls · SQL · RAG chunks ·
    ToolResult · raw response · API key / Authorization / password / DATABASE_URL ·
    exception message（DB 中无）· stack trace（DB 中无）
失败信息只进入**日志**（warning，含 error_type / exc_info 策略不变），不入库
```

## 12. Limitations

```text
* 无压力测试 / benchmark（本阶段明确禁止）→ §5 为**静态**上限分析
* 单实例视角：多实例部署时池上限应为 15 × 实例数（需运维确认，见 §5）
* pool_timeout（30s 默认）与 pool_recycle（-1）未调优 → 属环境决策，本阶段不优化
* worker 线程池上限 / 排队行为未实测
* 未评估长事务阻塞（例如未来引入批量写或运维 REINDEX 时对池的挤占）
* 「20 并发」回答基于当前配置与代码结构；真实流量分布（LLM 延迟占比）未测量
```
