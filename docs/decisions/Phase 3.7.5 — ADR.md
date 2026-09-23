# Phase 3.7.5 — ADR：SQL Validator（只读 SQL 安全校验）

日期：2026-09-23
状态：已实施（Phase 3.7.5）

## Context

下一阶段（3.7.6）LLM 将生成 SQL，但 LLM 输出不可信，
不能生成后直接执行。在

```text
LLM → ??? → Database
```

之间必须有一个**独立、纯内存、可测试**的安全边界，先把
"这条 SQL 允不允许执行"判断清楚。

## Decision

引入 `SQLValidator`
（`backend/app/services/sql_validator_service.py`）：

```text
LLM generated SQL
        ↓
SQLValidator（本阶段）
        ↓
安全 / 合法
        ↓
Future Read-only SQL Executor（后续阶段）
```

同时引入新依赖 **sqlglot**（>=25,<28）：成熟纯 Python SQL
parser，支持 PostgreSQL 方言，能把 SQL 解析成结构化 AST。
选择它而不是自写正则 / 自实现 grammar 的原因：SQL 语法复杂
（CTE / 子查询 / UNION / 注释 / 引号标识符），基于文本的
判断必然可被绕过（如 `SELECT 'DELETE'` 字面量），AST 是唯一
可靠的判断依据。sqlglot 无 DB / LLM 依赖，只解析字符串。

## Rules

校验规则（全部基于 AST，非文本搜索）：

```text
SELECT only        （root 必须 Select / SetOperation；
                    树内任何位置出现 Insert/Update/Delete/Merge/
                    Create/Alter/Drop/Truncate/Grant/Revoke/Copy/
                    Transaction/Commit/Rollback/Command → 拒绝）
single statement   （多条语句一律拒绝，即使全是 SELECT）
no SELECT INTO     （args["into"] 显式拒绝）
known tables       （引用表必须存在于 DatabaseSchema，如提供）
allowed tables     （白名单交集语义；裸表名唯一解析，歧义拒绝）
LIMIT required     （最外层必须有整数 LIMIT；子查询 LIMIT 不算数）
LIMIT <= max_rows  （默认 1000）
dangerous funcs    （pg_sleep* / pg_advisory_* / pg_read_* /
                    pg_terminate_* / dblink* / lo_import /
                    lo_export / setval / nextval / pg_notify → 拒绝）
```

错误码体系（`SQLValidationCode`）：EMPTY_SQL / INVALID_SQL /
MULTI_STATEMENT / NON_READ_ONLY / DANGEROUS_OPERATION /
TABLE_NOT_ALLOWED / UNKNOWN_TABLE / ROW_LIMIT_REQUIRED /
ROW_LIMIT_EXCEEDED / UNSUPPORTED_SQL。每个错误消息明确说明
发生了什么（如 "SQL contains forbidden statement: DELETE"）。

## Important

Validator：

```text
不执行 SQL（DB writes = 0，不 import 任何 ORM / session）
不修改 SQL（缺 LIMIT → ROW_LIMIT_REQUIRED，由 Generator 重新生成）
不生成 SQL
不调用 LLM / Embedding
```

## Conservative Principle

无法安全判断 → **拒绝**：

- parser 不认识的语法 → INVALID_SQL（不是 500）
- 非整数常量 LIMIT → UNSUPPORTED_SQL
- 裸表名在多个 schema / 多个白名单项中歧义 → 拒绝，不猜
- 已知危险函数按前缀族拒绝；任意用户自定义 volatile 函数
  不在本层拦截范围（最终防线是未来 Executor 的**只读数据库
  账号**——纵深防御，任何一层被绕过都不致命）

## Future

- Phase 3.7.6：Text-to-SQL Generator（依赖本 Validator 做自检
  / 重试循环）
- 再后续：Read-only SQL Executor（只读账号 + 参数化执行 +
  结果规模二次保护）
- 未来如需换 parser（如pgsql-parser），上层只依赖
  `SQLValidator` Protocol，实现零改动
