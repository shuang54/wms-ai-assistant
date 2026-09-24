# Phase 3.7.7 — ADR：Read-only SQL Executor

日期：2026-09-24
状态：已实施（Phase 3.7.7）

## Context

Text-to-SQL 链路已有 Generator（3.7.6）与 Validator（3.7.5），
但验证过的 SQL 还不能安全执行。需要一个**运行时执行边界**：
即使 Validator 出现漏洞或被绕过，数据库也不会被写入。

## Decision

新增 `SQLExecutor`
（`backend/app/services/sql_executor_service.py`）：

```text
validated SQL
    ↓ ① 重新调用 SQLValidator（TOCTOU：验证的字符串
    ↓    就是最终执行的字符串）
    ↓ ② AUTOCOMMIT 连接上显式 BEGIN READ ONLY
    ↓ ③ SET LOCAL statement_timeout（事务级，不污染连接池）
    ↓ ④ fetchmany(max_rows + 1)（行数第二层保护）
    ↓ ⑤ 结果大小保护 max_result_bytes（粗粒度估算）
    ↓ ⑥ ROLLBACK 归还连接
PostgreSQL
    ↓
SQLExecutionResult（纯 Python：columns / rows / row_count /
                     truncated / execution_time_ms）
```

执行线程化（`asyncio.to_thread`），不阻塞事件循环；复用现有
`db.session.get_engine()`，不重建 Engine / 连接串。

## Security（纵深防御）

```text
Prompt                        = 软约束（引导 LLM）
SQLValidator                  = SQL 静态安全边界（3.7.5，本阶段零改动）
SQLExecutor                   = 运行时边界（READ ONLY 事务 +
                                statement_timeout + 行数/大小上限）
只读数据库账号（wms_ai_readonly）= 部署层边界（部署时配置，
                                当前测试环境用事务级 READ ONLY 替代）
```

即使 Validator 被绕过（测试用 Fake 放行 CREATE），
`BEGIN READ ONLY` 仍在数据库端拒绝写操作——运行时防线已验证。

## Exceptions（消息脱敏）

```text
SQLExecutorError
├── SQLExecutorInputError        输入非法（验证/数据库访问前拒绝）
├── SQLExecutorValidationError   重新验证未通过（携带错误码，零 DB 接触）
├── SQLExecutorTimeoutError      statement_timeout 已在 DB 端取消
├── SQLExecutorDatabaseError     只暴露底层异常类名，绝不透传
│                                可能含 DATABASE_URL / 密码的消息
└── SQLExecutorUnavailableError  DATABASE_URL 未配置
```

## Responsibilities

```text
TextToSQLGenerator  负责生成 SQL（3.7.6）
SQLValidator        负责静态验证（3.7.5，未修改）
SQLExecutor         负责安全执行（本阶段）
AI Router           未来决定是否走 Text-to-SQL（未实现）
```

## Config

`SQLExecutorSettings`（挂现有 Settings，钳制防滥用）：
`SQL_EXECUTOR_TIMEOUT_SECONDS`（默认 10，[1, 600]）、
`SQL_EXECUTOR_MAX_ROWS`（默认 1000，[1, 100000]）、
`SQL_EXECUTOR_MAX_RESULT_BYTES`（默认 1MB，[64KB, 64MB]）。

## Known Limitations（后续优化点）

- 结果大小保护为粗粒度估算（字段字符串化长度），非流式：
  数据仍可能先完整传输到应用进程再截断；超大结果需要
  cursor 流式 / 分页时再优化；
- 专用只读数据库账号属部署层配置，未在本阶段代码中强制
  （部署时为 Executor 配置仅 SELECT 权限账号即可闭环）。

## Future

- Phase 3.7.8+（如规划）：AI Router（RAG / Tools / Text-to-SQL
  路由）、API 端点、结果自然语言化——均未开始。
