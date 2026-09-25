# SQL Executor Security E2E — Phase 3.9.8

> Phase 3.9.7 证明了「危险 SQL → 真实 Validator → Reject → Executor 0 次调用」。
> 本阶段回答下一个问题：
>
> **Validator 被绕过后，真实 `SQLExecutorService` 自身是否仍有第二道防线？**

---

## 1. Scope

```text
- Executor          : REAL —— SQLExecutorService（真实实现，未修改）
- Validator         : NOT USED —— 不使用 SQLValidatorService（§十六）
- LLM               : NO（未调用 DeepSeek / 任何真实模型）
- Database          : 测试 PostgreSQL only（非生产库）
- Schema            : 隔离测试 schema `t2s_exec_sec`（非 public 业务表）
- Production logic  : 未修改（Executor / Validator / Generator / Runner / Prompt 原样）
- Dataset / Snapshot: 未修改（3.9.3 Dataset、3.9.5 / 3.9.6 / 3.9.7 产物均未改动）
- New dependencies  : 无
```

### 为什么必须绕开 Executor 自带的 re-validation

读源码（`sql_executor_service.py` L294-307）可知，Executor **自身内建**一层
re-validation（TOCTOU 防护）。若直接调用默认 Executor，DELETE 会在到达数据库前
就被 `SQLExecutorValidationError` 拦下，测到的仍是「Validator 层」而非数据库层防线。

因此本阶段通过**既有依赖注入点** `SQLExecutorService(validator=...)` 注入放行型桩
Validator，使 SQL 真正抵达 `BEGIN READ ONLY` 事务：

- **未修改生产代码**，未新增 `security_mode` / `allow_write` / `skip_validation`
  之类参数（§十七）；
- 与项目既有 `tests/test_sql_executor_service.py` 的 `FakeValidator(decisions=[True])`
  惯例一致。

---

## 2. 当前 Executor 的真实安全机制（读源码所得，非假设）

| 机制 | 真实实现 |
| --- | --- |
| Transaction mode | AUTOCOMMIT 连接上显式 `BEGIN READ ONLY`，`finally: ROLLBACK` |
| Statement timeout | `SET LOCAL statement_timeout = <seconds*1000>`（事务级，不污染连接池） |
| Max rows | `fetchmany(max_rows + 1)`；多取的 1 行仅用于判截断，返回 `max_rows` |
| Result size limit | `_materialize()` 按 `max_result_bytes` 粗粒度累加；**第一行即超限时保留该行并截断**（MVP 策略） |
| Rollback behavior | `finally` 中无条件 `ROLLBACK`（成败都执行） |
| Connection cleanup | `with engine.connect()` 归还连接池 |
| search_path | `SET LOCAL search_path TO "<schema>"`，标识符经 `^[A-Za-z_][A-Za-z0-9_]*$` 白名单校验 |
| Exception 转换 | 只暴露底层异常**类名**，不透传含 DATABASE_URL / 密码的消息 |
| 并发 | `asyncio.to_thread` 线程池执行，不阻塞事件循环 |

---

## 3. Security Matrix（实测）

| Test | Executor | DB Mutation | Result |
| --- | ---: | --- | --- |
| `DELETE FROM <隔离表>` | **Reject**（`SQLExecutorDatabaseError`） | No | PASS |
| `UPDATE <隔离表> SET name=...` | **Reject** | No（`id=1` 仍 `row_1`） | PASS |
| `INSERT INTO <隔离表> VALUES (999,...)` | **Reject** | No（行数不变） | PASS |
| `CREATE TABLE <隔离 schema>.ddl_probe`（DDL） | **Reject** | No（对象未创建，原表仍在） | PASS |
| `SELECT current_setting('transaction_read_only')` | Accept | Read only | PASS（返回 `on`） |
| `SELECT id, name ... LIMIT 1` | **Accept** | Read only | PASS（1 行，未截断） |
| max_rows（10 行表，`max_rows=3`） | **Limited** | No | PASS（3 行 + `truncated=True`） |
| oversized result（~100KB / 64KB 上限） | **Truncate** | No | PASS（`truncated=True`，未把完整结果当成功） |
| statement timeout（`pg_sleep(2)` / 1s） | **Reject**（`SQLExecutorTimeoutError`） | No | PASS（~1s 取消，总耗时 < 5s） |
| connection recovery（失败后 `SELECT 1`） | **Reusable** | No | PASS（返回 `((1,),)`） |
| 全部写尝试后聚合校验 | — | **No**（行数/值/对象全不变） | PASS |

---

## 4. Security Boundary（两层独立防护）

```text
              ┌── Validator（3.9.7）── reject ── X
Dangerous SQL ┤
              └── Executor（3.9.8）── BEGIN READ ONLY ── DB reject ── ROLLBACK ── X
```

两层防护**相互独立**：

- 第一层（3.9.7）：SQL 静态校验，拒绝后 Executor 根本不被调用；
- 第二层（3.9.8）：即使第一层被完全绕过，数据库只读事务仍在**服务端**拒绝写操作，
  并 `ROLLBACK`，不留任何持久变化。

> 需要如实说明：Executor 自身还内建了第三层（re-validation，TOCTOU 防护）。
> 本阶段为隔离数据库层防线而将其绕过；默认路径下 DELETE 会在该层就被
> `SQLExecutorValidationError` 拒绝。

---

## 5. What this proves

1. 在测试覆盖的写操作类型（DELETE / UPDATE / INSERT / DDL-CREATE）中，
   **真实 `SQLExecutorService` 在 Validator 被绕过后仍能阻止写操作**；
   拦截来自 PostgreSQL 的 READ ONLY 事务，而非应用层字符串判断；
2. 事务确实处于只读状态（数据库自身 `transaction_read_only = on`，黑盒验证）；
3. 只读 SELECT 仍正常执行 → Executor 不是「拒绝一切」；
4. 行数上限与结果大小上限生效（`max_rows` 截断 / `max_result_bytes` 截断）；
5. 语句超时生效且异常后连接可继续使用（不会停留在 aborted transaction）；
6. 任何写尝试后，隔离表数据**零持久变化**（行数、`id=1` 值、对象存在性均校验）。

---

## 6. What this does NOT prove

- 未覆盖所有 SQL 注入 / 权限提升变体；
- **未验证部署层的只读数据库账号**（ADR 中列为第四层，本阶段未涉及）；
- 未验证 `max_result_bytes` 的精确字节语义（实现为粗粒度估算，
  且「第一行即超限」时按 MVP 策略保留该行）；
- 未验证并发 / 长事务 / 连接池耗尽等场景；
- 未验证 `SQLValidatorService`（本阶段刻意不使用）；
- 不声称「系统已经绝对安全」——两层防护成立 ≠ 全链路无风险。

---

## 7. Test Inventory（11 项）

文件：`tests/test_text_to_sql_executor_security_e2e.py`（唯一新增代码文件），
全部由 `RUN_DB_TESTS=1` 开启（未开启时 11 项 skip，默认不消耗数据库）。

| # | 测试 | 覆盖点 |
| --- | --- | --- |
| 1 | `test_executor_rejects_delete_in_read_only_transaction` | DELETE → Reject + 数据不变 + 异常脱敏 |
| 2 | `test_executor_rejects_update_in_read_only_transaction` | UPDATE → Reject + 目标值不变 |
| 3 | `test_executor_rejects_insert_in_read_only_transaction` | INSERT → Reject + 无新行 |
| 4 | `test_executor_rejects_ddl_in_read_only_transaction` | DDL → Reject + 对象未创建 |
| 5 | `test_executor_transaction_is_read_only` | `transaction_read_only = on`（§九 黑盒） |
| 6 | `test_executor_allows_read_only_select` | 正向：SELECT 成功 |
| 7 | `test_executor_enforces_max_rows` | 10 行 → max_rows=3 |
| 8 | `test_executor_enforces_result_size_limit` | 结果大小保护 |
| 9 | `test_executor_enforces_statement_timeout` | pg_sleep 超时 |
| 10 | `test_executor_connection_recovers_after_failed_query` | 异常后连接可用 |
| 11 | `test_no_persistent_mutation_after_all_write_attempts` | 聚合：零持久变化 |

与既有 `tests/test_sql_executor_service.py::TestRealDatabase` 的关系：
既有测试已覆盖 DDL 拦截 / 超时 / 行数上限（面向 `public` schema）；
本阶段补齐 **DML（DELETE/UPDATE/INSERT）在隔离 schema 上的 before-after 数据校验**、
**事务只读状态证明**、**真实 DB 上的结果大小保护**与**连接恢复**，
并按 §十九 要求不把 `public.knowledge_document` 作为写目标。
