# Phase 3.12 Step 58 — Real LLM Production Usage Smoke

> 真实 DeepSeek + **生产默认 Client** + `DatabaseLLMAccountingSink` + Assistant Trace 读取。
> **Production code changes = 0** · DB Schema changes = 0 · 无新增配置项 / 索引 / API。
> 新增：`tests/test_llm_usage_real_llm_smoke.py`（**默认 SKIPPED**，显式 opt-in）。
> 真实 LLM 请求：**2 次运行 × 1 次请求**（同一最小问题；首次 pass，第二次带证据输出）。

---

## 1. Environment

```text
provider            = deepseek
model（配置值）      = deepseek-chat
model（**真实响应**） = deepseek-flash      ← usage 行记录的是响应模型（既有契约：
                                            model 取自 observation，不取自 config）
base_url hostname   = api.deepseek.com（scheme https）
api_key             = configured（**已掩码**；不打印值 / 不打印 headers）
DB environment      = 本地开发 PostgreSQL 16.15（docker-compose；**非生产库**）
embedding key       = configured（本 smoke 未使用）
```

## 2. Smoke Request

```text
question = "请只回答：OK"      （最小问题；不触发 SQL / Tool / 知识库检索）
real LLM requests = 1 / 次运行  （无重试；provider 侧只收到 1 个请求）
answer   = "OK"
```

## 3. Production Client

```text
get_default_llm_client()                       ← 生产默认 Client（**未** new 新实例）
    assert not isinstance(client, MockLLMClient)          ✅ 真实 DeepSeek provider
    inner._accounting_sink is get_default_accounting_sink()
        is DatabaseLLMAccountingSink                      ✅ **不是** Noop
（未使用 create_llm_client(..., NoopAccountingSink())；未替换 sink）
```

## 4. Provider Request

```text
llm_usage_record.request_id = "9368cae4…e73020"（len = 36，UUID 形态）
来源：真实响应体的 ``id`` 字段（client.py:637 `request_id = data.get("id")`
      → LLMResponse.metadata["request_id"] → observation.request_id → DB 列）
**不是**应用生成：应用只在 trace scope 生成 assistant_request_id；
      provider 未暴露时契约允许 None，本 smoke 未触发该分支（真实暴露）
```

## 5. Usage Persistence

```text
before_count = 0
after_count  = 1                 （after = before + 1 ✅ 恰好 1 条）
persisted row（真实值）：
    id                   = 2（自增序列）
    provider             = deepseek
    model                = deepseek-flash
    prompt_tokens        = 9
    completion_tokens    = 1
    total_tokens         = 10
    created_at           = 2026-09-29 09:20:52.865967+00:00
    assistant_request_id = step58-smoke-688eeb1b
    request_id           = 9368cae4…e73020（provider id）
cleanup（finally，精确条件 DELETE）：
    DELETE ... WHERE assistant_request_id = A AND provider = 'deepseek'  （不使用 TRUNCATE）
final_count = 0 = before_count ✅ · residue = 0
```

## 6. Assistant Correlation

```text
assistant_request_id = step58-smoke-688eeb1b     （应用 new_request_id → trace scope）
provider request_id  = 9368cae4…e73020           （真实响应 id）
断言：request_id != assistant_request_id        ✅ **两者不同**（不覆盖、不混用）
```

## 7. Assistant Trace

```text
GET /api/observability/assistant-trace/{A}   （真实 FastAPI app + 真实读边界）
    → 200 ✅
    → assistant_request_id == A ✅
    → llm_usage 长度 = 1 ✅（item.id == DB row.id · item.provider == deepseek ·
      item.request_id == 9368cae4…e73020 · item.total_tokens == 10）
HTTP contract 未改变（仍为 assistant_request_id + 3 个列表）
```

## 8. Security

```text
usage 行列集合 == 白名单 9 列（id · request_id · provider · model ·
    prompt_tokens · completion_tokens · total_tokens · created_at · assistant_request_id）
行值中不含：提问原文 / raw response / messages / prompt / API key（"sk-"）/
    Authorization（"Bearer "）/ DATABASE_URL（"postgresql://"）
报告与测试输出不含：API key / Authorization / 完整 .env / 完整 headers
    （仅 provider · model · base_url hostname · api_key = configured）
```

## 9. Limitations

```text
* real DeepSeek **只测了 2 次运行**（同一最小问题；首次 pass、第二次带证据输出）—— 未压测 / 未 benchmark
* 非生产数据库（本地开发 PostgreSQL）；未验证生产库延迟与连接池表现
* 未覆盖真实 LLM 的业务 HTTP 路由（Router / RAG 检索 / Tool / Text-to-SQL 的**真实** LLM 调用）；
  本 smoke 走最短真实 Client 路径 + 真实 Trace **读**端点（符合本阶段范围）
* 端点实际服务模型为 deepseek-flash（与配置 deepseek-chat 不同）；单一模型未做对比
* 受网络 / 限流 / provider 可用性影响：环境问题 → SKIPPED（ENVIRONMENT UNAVAILABLE），
  不伪装为 PASS；应用问题（LLM 成功但未持久化 / correlation 错误）→ FAILED
* opt-in 机制沿用项目既有 `RUN_REAL_LLM_TEST`（并接受 `RUN_REAL_LLM_TESTS` 别名）；
  不引入 pytest marker（pytest.ini 为 `--strict-markers`），默认套件 real_llm = 0 / network = 0
```
