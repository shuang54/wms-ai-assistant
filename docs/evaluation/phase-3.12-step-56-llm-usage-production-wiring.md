# Phase 3.12 Step 56 — LLM Usage Production Wiring

> 实现范围：**生产默认 LLM Client** 接入 `DatabaseLLMAccountingSink`。
> 修改：`backend/app/llm/client.py`（唯一生产改动点）。
> 新增：`tests/test_llm_usage_production_wiring.py`（离线，14）·
> `tests/test_llm_usage_production_wiring_e2e_db.py`（DB-gated，9）。
> 未新增 API / 表 / 列 / 索引 / migration / runtime config / 环境变量；
> 未改 Prompt / Router / RAG Core / Tool Core / Validator / Executor /
> Assistant Trace API contract。

---

## 1. Before

```text
get_default_llm_client()            （生产默认 Client；RAG / Tool / Text-to-SQL 懒加载）
    ↓
create_llm_client(settings.llm)     （未传 accounting_sink）
    ↓
LLMClient.__init__: accounting_sink or **NoopAccountingSink()**
    ↓
LLM 调用成功 → _emit_accounting → Noop.record() → return None
    ↓
ai_ops.llm_usage_record             ← **不写入**
    ↓
Assistant Trace llm_usage[] = []    （Step 55 审计结论）
```

## 2. After

```text
get_default_llm_client()            （进程级单例；不变）
    ↓
get_default_accounting_sink()       （新增；进程级单例，与 Client 同生命周期）
    ├── get_engine() is None（DATABASE_URL 未配置）
    │       → NoopAccountingSink            （旧行为；不产生告警噪声）
    └── DB 已配置（生产）
            → DatabaseLLMAccountingSink
                    ↓
            LLMUsagePersistenceService
                    ↓
            LLMUsagePersistenceRuntimeBridge（asyncio.to_thread 线程边界）
                    ↓
            LLMUsageRepository.create() → INSERT ... ON CONFLICT DO NOTHING
                    ↓
            ai_ops.llm_usage_record     ← 1 次成功 LLM 调用 = 1 行
                    ↓
GET /api/observability/assistant-trace/{A} → llm_usage[]

不变（刻意）：
    create_llm_client(settings.llm)  → 仍为 **NoopAccountingSink**
    （显式构造 / 单元测试 / 评估脚本行为完全不受影响）
    reset_default_llm_client()       → 同时重置 client 与 sink 单例（测试辅助）
```

```text
接线位置（唯一）：backend/app/llm/client.py::get_default_llm_client()
    accounting_sink=get_default_accounting_sink()
新增公开函数：get_default_accounting_sink()（单例读取；已加入 __all__）
DB 判定复用既有语义：get_engine() is None ⇔ DATABASE_URL 未配置 ⇔ DB 功能禁用
（**不新增**任何配置项 / 环境变量；无 DB 环境行为与 Step 55 之前逐字节一致）
```

## 3. Correlation

```text
Assistant Trace ID（Orchestrator 生成，唯一）
    new_request_id() → assistant_trace_scope(A)
        ↓
    LLMUsagePersistenceService.persist()
        assistant_request_id = current_assistant_request_id()   → **A**
        ↓
    llm_usage_record.assistant_request_id = A

Provider request_id（与上者**不同**、互不覆盖）
    provider 响应 id（如 chatcmpl-…）→ observation.request_id
        ↓
    llm_usage_record.request_id = <Provider 请求 ID>
```

```text
E2E 实测（断言，非推断）：
    row.assistant_request_id == 响应 metadata.request_id（A，uuid 形态）
    row.request_id           == MockTransport 提供的 provider id（step56-llm-1）
    row.request_id != row.assistant_request_id          ← 两者确实不同
    sink 不生成 / 不回填 / 不覆盖 assistant_request_id（scope 未绑定 → NULL）
```

## 4. E2E

```text
真实链路（只 Fake 外部边界）：
    POST /api/ai/chat  →  真实 FastAPI app / Router / Orchestrator
        ↓  真实 RagService（仅替换 Vector Search / ContextBuilder）
    **生产默认 LLM Client**（get_default_llm_client()，真实接线）
        ↓  httpx.MockTransport（0 网络 / 0 DeepSeek）
    DatabaseLLMAccountingSink → Repository → ai_ops.llm_usage_record
        ↓
    GET /api/observability/assistant-trace/{A} → llm_usage[]（HTTP contract 未变）

实测结果：
    RAG 请求          HTTP 200 · provider 请求 1 次 · usage 行 1 条
                      · trace llm_usage 1 条（id == DB id）· rag_executions 1 条
    空检索            HTTP 200 · LLM 0 次 · usage 0 条 · rag_executions 1 条
    Tool 请求         HTTP 200 · LLM 0 次 · usage 0 条 · tool_executions 1 条
    Text-to-SQL 重试  2 次真实 LLM 请求 → 2 条 usage（每条 1 个 provider id）
    Refusal           1 次真实 LLM 请求 → 1 条 usage（attempts=1 · 未进 Validator/Executor）
```

## 5. Failure Isolation

```text
① LLM 业务失败（HTTP 500 / ConnectTimeout）
       → 既有错误语义不变（/api/ai/chat 500）· provider 仅被调用 1 次（**无 retry**）
       → usage 行 = **0**（usage=None 不落库；本表是 usage storage 而非 audit log）

② Accounting 写入失败（Repository.create 强制抛 LLMUsageRepositoryError）
       → HTTP **200**（LLM 业务结果不变）· provider 仅被调用 1 次（**无 retry**）
       → usage 行 = 0 · Assistant Trace 仍可读（llm_usage=[]，RAG 段正常）
       → 失败只在 sink/client 内部收敛为 warning（四层防御未改动）

不引入：retry / sleep / backoff / queue / fallback / 后台任务
```

## 6. Security

```text
写入字段（严格白名单，未扩大）：
    request_id(Provider) · provider · model · prompt_tokens · completion_tokens ·
    total_tokens · assistant_request_id · created_at(server now()) · id
不写入：prompt · messages · system prompt · tool definitions · SQL · RAG chunks ·
    tool arguments · ToolResult.data · raw response · API key / authorization /
    password / database URL / headers · traceback
测试证据：
    离线：set(repository 收到 kwargs) == 白名单（精确相等）；值不含消息原文 / 凭据
    DB：E2E 只断言上述列值；ORM 列集合未变（9 列，与 Step 52 一致）
    Assistant Trace 响应字段未变（llm_usage 仍 9 字段）
```

## 7. Tests

```text
新增 tests/test_llm_usage_production_wiring.py（离线；0 DB / 0 网络）   14 passed
    接线（DB 已配置 → Database sink · DB 未配置 → Noop · 显式工厂仍 Noop ·
    静态：接线只在默认 Client 工厂）· 单例（client/sink 同生命周期 · reset 重建）·
    一次 call 一条记录（2 次 call → 2 条）· 关联（scope → assistant_request_id；
    provider id 独立；scope 外 → None）· 白名单字段 · 失败隔离 + 不重试 ·
    usage=None 不写入

新增 tests/test_llm_usage_production_wiring_e2e_db.py（RUN_DB_TESTS=1）  9 passed
    默认 Client 接线 · RAG E2E（HTTP → usage row → Trace 读回）· 空检索 ·
    Tool 路径 · LLM 失败（500 / timeout）· Accounting 失败隔离 · Text-to-SQL
    重试 2 calls → 2 rows · Refusal 1 call → 1 row

复用（未新增重复测试）：tests/test_llm_usage_persistence*.py（one call = one row /
    usage=None 不写 / 隔离）· test_llm_accounting_*（默认 Noop 契约）·
    test_assistant_trace_*（Trace 读边界）

全量：无 DB 4119 passed / 489 skipped（0 failed）
      DB   4567 passed / 41 skipped（0 failed）
      （Step 55 基线 4105 / 4544 → +14 离线 + 9 DB）
```

## 8. Limitations

```text
* 生产默认 sink 的**真实 DeepSeek** 链路未验证（本阶段 Fake transport；0 网络调用）
* Router LLM fallback 在默认装配下仍不可达（未注入 llm_client）→ 该路径的 usage 未覆盖
* `get_engine() is None` 判定在**运行期**求值一次并缓存（进程内 sink 单例）；
  若运行期才配置 DATABASE_URL，需要重启进程（与既有 Engine 缓存行为一致）
* DB 不可达但已配置（连接失败）时：每次 LLM 调用会产生一次 warning 日志
  （失败隔离保证业务结果不变；未引入抑制/采样机制）
* 未做连接池压力 / 延迟压测（每次记录 1 连接 + 1 事务，见 Step 55 §14）
* DB-gated E2E 使用 httpx.MockTransport（不覆盖真实网络异常分布）
```
