# Phase 3.10.14 — Usage Persistence 评估

## Environment

```text
Python        3.13.2
PostgreSQL    16.15 (Debian 16.15-1.pgdg12+2)
pytest        8.x（asyncio_mode=auto）
Driver        SQLAlchemy 2.x + psycopg 3（同步 Session）
DB 开启方式   RUN_DB_TESTS=1（未设置时全部 DB 测试 skip）
```

## Schema

```text
schema: ai_ops            ← 独立 schema，不在业务 schema public
table:  ai_ops.llm_usage_record
```

| column | type | null | 说明 |
|---|---|---|---|
| id | BIGINT (PK, autoincrement) | NOT NULL | 记录主键（沿用项目 BIGINT 规范） |
| request_id | VARCHAR(128) | NULL | 来自 `LLMObservation.request_id`；缺失 NULL，**不生成 UUID** |
| provider | VARCHAR(64) | NULL | 来自 `LLMObservation.provider`；不填 unknown/default |
| model | VARCHAR(128) | NULL | 来自 `LLMObservation.model`（实际响应）；**不用 settings.model 兜底** |
| prompt_tokens | INTEGER | NULL | Provider 事实；**NULL ≠ 0** |
| completion_tokens | INTEGER | NULL | Provider 事实；**NULL ≠ 0** |
| total_tokens | INTEGER | NULL | 原样保存；不重算 prompt + completion |
| created_at | TIMESTAMPTZ | NOT NULL | server_default=now()；本条记录产生时间（非 LLM 开始时间） |

索引：`ix_llm_usage_record_created_at`（唯一索引，保持最小）。
无唯一约束（request_id 允许 NULL，不伪造幂等）。

**为什么用独立 schema（关键设计）**：
`PostgreSQLMetadataProvider.inspect(schema="public")` 会读取 public 下
全部基表作为 Text-to-SQL 的业务 schema。把 `llm_usage_record` 放进 public
会导致（a）业务表枚举的既有 DB 测试失败；（b）LLM 可能把内部运维表
当成业务表生成查询。因此本表与业务数据物理隔离在 `ai_ops`。

## Cases

| # | Case | 结果 | 说明 |
|---|---|---|---|
| 1 | Table exists | PASS | `ai_ops.llm_usage_record` 存在；且不在 `public` |
| 2 | Full Usage | PASS | 100 / 20 / 120 正确保存；request_id/provider/model 正确 |
| 3 | Partial Usage | PASS | `None / 200 / None` → `NULL / 200 / NULL`（不是 0） |
| 4 | None Usage | PASS | sink 不报错，**不写入行**（本表是 Usage Storage，非 audit log） |
| 5 | Provider | PASS | 原样保存；`None → NULL`（无 unknown/default 填充） |
| 6 | Model | PASS | 来自实际响应 `deepseek-chat`，配置值 `configured-model` 未入库 |
| 7 | Request ID | PASS | 原样保存；缺失为 NULL（未出现伪造 UUID） |
| 8 | Tool Calling | PASS | 2 轮 → **2 行**（110 / 230），未合并为聚合记录 |
| 9 | T2S Retry | PASS | 2 次请求 → **2 行**（120 / 175），request-level；无 retry 聚合字段 |
| 10 | RAG | PASS | 业务答案 `rag answer` 不变；新增 1 行 usage |
| 11 | Refusal | PASS | `status=refusal`、`attempts=1` 语义不变；usage 未因 refusal 丢弃（1 行） |
| 12 | Concurrency | PASS | 5 并发 → 5 行；request_id / tokens 一一对应，无串线 |
| 13 | Persistence Failure | PASS | 仓储抛错 → 业务结果不变、HTTP 调用仍为 1（无 retry）、事务回滚 |
| 14 | DB Residue | PASS | 每测试后 TRUNCATE；结束时 `llm_usage_record` 行数为 0 |

## Security

```text
No Prompt              ✔ 表无 prompt / messages / system_prompt 列
No SQL                 ✔ 无 sql 列
No RAG content         ✔ 无 rag_content / chunk 列
No Tool args/results   ✔ 无 tool_args / tool_result 列
No Secrets             ✔ 无 api_key / password / authorization / headers / raw_response
No Cost / Pricing      ✔ 无 price / cost / currency 列（本阶段不落库）
```

列名集合经测试锁定为精确白名单（多/少任一列即失败）。
持久化只读取 `observation.usage / request_id / provider / model`。

## Default Behavior

```text
create_llm_client()                → 仍为 NoopAccountingSink，不连数据库
显式注入 DatabaseLLMAccountingSink → 才写入 llm_usage_record
MockLLMClient                      → 行为不变
```

## Idempotency

```text
Idempotency is NOT guaranteed in this phase（at-least-once / caller-controlled）。
同一 observation 被 record() 两次会产生两条记录；request_id 允许 NULL，
不依赖唯一约束伪造幂等。
```

## Final

```text
Usage Persistence      = PASS
Cost Persistence       = NOT IMPLEMENTED
Billing                = NOT IMPLEMENTED
Aggregation            = NOT IMPLEMENTED
Pricing Registry       = NOT IMPLEMENTED
Dashboard              = NOT IMPLEMENTED
Telemetry Platform     = NOT IMPLEMENTED
```

## Test Results

```text
tests/test_llm_usage_persistence.py（RUN_DB_TESTS 未设置）: 17 skipped
tests/test_llm_usage_persistence.py（RUN_DB_TESTS=1）    : 17 passed

pytest（默认）        : 2153 passed, 284 skipped, 0 failed
pytest（RUN_DB_TESTS=1）: 2396 passed, 41 skipped, 0 failed
compileall            : OK
```
