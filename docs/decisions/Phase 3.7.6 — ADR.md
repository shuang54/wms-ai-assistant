# Phase 3.7.6 — ADR：Text-to-SQL Generator

日期：2026-09-24
状态：已实施（Phase 3.7.6）

## Context

自然语言问题需要转换成 PostgreSQL 查询。前置阶段已就绪：
RelevantTableSelector（选表）、DatabaseContextComposer（组装上下文）、
SQLValidator（安全校验）。缺的只是中间的生成环节。

## Decision

新增 `TextToSQLGenerator`
（`backend/app/services/text_to_sql_service.py`）：

```text
Question + DatabaseContext + AllowedTables
        ↓ TextToSQLService（复用 LLMClient.chat）
LLM 生成 SQL
        ↓ extract_sql（纯文本提取，零安全判断）
SQLValidator（Phase 3.7.5，硬边界）
        ↓ 通过 → TextToSQLResult(validated=True)
        ↓ 拒绝 → 携带错误码重试（预算内）
预算耗尽 → 异常（绝不返回失败 SQL）
```

Prompt 为外部模板文件（`backend/app/prompts/text_to_sql_*.txt`），
不硬编码在 Python 中；上层只依赖 `TextToSQLGenerator` Protocol。

## Responsibilities

```text
Generator: 生成 SQL + 提取 + 预算内重试（本阶段）
Validator: 安全校验（3.7.5，未改动一行）
Executor:  未来负责执行（未实现；纵深防御再叠只读账号）
```

## Retry

默认 `max_attempts = 3`（`TEXT_TO_SQL_MAX_ATTEMPTS`，钳制 [1, 10]）。
Retry prompt 只包含上一轮 SQL + Validator 错误码/消息 + 完整上下文，
不堆叠完整历史。LLM 网络 / 响应异常（LLMError）直接透传，
不做无意义重试。连续空输出预算耗尽 → `TextToSQLGenerationError`；
校验连续拒绝预算耗尽 → `TextToSQLRetryExceededError`
（异常不携带失败 SQL，防止被上层误执行）。

## Important

```text
Generator 不执行 SQL（模块无 ORM / engine / execute 代码路径）
Validator 不执行 SQL（3.7.5 原则，本阶段未改动）
```

## Security

```text
Prompt 是软约束：引导 LLM（SELECT-only / allowed tables / LIMIT /
不发明字段 / 问题视为数据而非指令——Prompt Injection 防护声明）
Validator 是硬约束：LLM 真的生成 DELETE / 越权表 / 无 LIMIT，
一律拒绝并重试；用户问题注入"删除库存表"也不例外
```

Prompt 中绝不出现 API Key / DATABASE_URL / 密码 / 内部实现细节
（测试静态断言锁定）。

## Future

- 下一阶段（Executor 之前可先做 Router 决策）：Read-only SQL
  Executor（只读账号 + 参数化执行 + 结果规模保护）
- AI Router：在 RAG / Tools / Text-to-SQL 三条能力间路由
- Embedding-based Selector、Schema 缓存等优化仍按 3.7.4 ADR 规划
