# Phase 3.12 Step 55 — LLM Usage Production Wiring Audit

> **Audit / Design only**。未改 `get_default_llm_client()` / `create_llm_client()` /
> `DatabaseLLMAccountingSink` / `LLMUsageRepository` / Assistant Trace / RAG / Tool /
> TextToSQL / Router / Orchestrator；未新增 API / 表 / Index / migration / runtime config；
> 未调用真实 LLM；**DB writes = 0**；生产代码改动 = 0。
>
> **结论**：`Implementation exists`（sink + service + repository + bridge + tests 全部就绪）
> 但 `Production wiring exists` = **NO**（默认装配使用 `NoopAccountingSink`，
> 这是**刻意设计**而非 bug）。最小接线点已明确 ⇒
> **Production LLM Usage Persistence: DESIGN READY**。

---

## 1. Audit Scope

```text
问题：生产默认 LLM Client 是否真的接入 DatabaseLLMAccountingSink？
      Assistant Trace 的 llm_usage[] 在生产是否为空？
范围：只读源码 + 全仓搜索 + 既有测试证据；不实现接线
产出：调用链事实 · sink 选择点 · 生命周期 · 双写风险 · 失败路径 ·
      隔离语义 · 环境矩阵 · 最小接线点 · Session 设计 · 安全字段 · Recommendation
```

## 2. Current LLM Call Path（生产默认路径 + sink 选择点）

```text
HTTP POST /api/ai/chat
   ↓
api/orchestrator_chat.py（模块级单例 AIOrchestratorService）
   ↓
AIRouterService / RagService / TextToSQLService / ToolChatService
   │  （三条服务各自懒加载默认 client；Router 默认 **无** client）
   ↓
backend/app/llm/client.py :: get_default_llm_client()          ← 模块级单例
   ↓
create_llm_client(settings.llm)                                 ← **未传 accounting_sink**
   ├── api_key 非空 → DeepSeekProvider(OpenAICompatibleClient(
   │        ..., observation_sink=None, accounting_sink=None))
   └── api_key 为空 → MockLLMClient（不产生 Observation / Accounting）
   ↓
LLMClient.__init__:
   self._observation_sink = observation_sink or **NoopObservationSink()**
   self._accounting_sink  = accounting_sink  or **NoopAccountingSink()**   ★ 选择点
   ↓
await chat() → _chat_impl
   ├── 成功 → _observe_call(started_at, response=result)
   └── 失败 → _observe_call(started_at, error=exc) → raise（原异常）
   ↓
_emit_observation（同步 sink） + _emit_accounting（可选 arecord）
   ↓
sink = NoopAccountingSink → record() → return None     ★ 生产默认在此终止
   ↓
ai_ops.llm_usage_record                               ← **不写入**
```

```text
NoopAccountingSink 被选择的唯一位置：llm/client.py::LLMClient.__init__
    （accounting_sink=None → Noop；生产装配从未传值 ⇒ 恒为 Noop）
DatabaseLLMAccountingSink 被选择的唯一位置：显式注入（见 §4）
```

## 3. get_default_llm_client()

```text
1. 是否 singleton？           是（模块级 `_default_client`；进程级缓存）
2. 每次调用重新创建？         否（首次懒加载后复用同一实例）
3. 是否缓存？                 是（`reset_default_llm_client()` 供测试清缓存）
4. Provider 是否复用？        是（同一 LLMProvider 实例被 RAG / TextToSQL / ToolChat 共享）
5. Accounting Sink 在哪里传入？`create_llm_client(settings.llm)` —— **不传**
                              ⇒ `LLMClient.__init__` 的 `None → NoopAccountingSink`
6. DatabaseLLMAccountingSink 是否被引用？生产代码**从不实例化**：
                              唯一出现处 = `llm_usage_persistence_service.py:151`
                              的**类 docstring 示例**（"用法（显式接入，默认不启用）"）；
                              其余全部在 tests/（见 §12）
7. Noop sink 是默认值还是 fallback？**默认值**（`accounting_sink or NoopAccountingSink()`）；
                              非异常兜底
8. 是否依赖 RUN_DB_TESTS？    否（该 flag 只控制测试收集；与 client 无关）
9. 是否依赖 environment？     仅依赖 `settings.llm`（api_key / base_url / model / provider / timeout）；
                              **无** accounting / usage / persistence 相关环境变量
10. production 与 test path 是否不同？**不同**：test 显式注入 sink（§12），production 走默认 Noop
```

## 4. Accounting Sink Selection

```text
接口：llm/observability.py
    LLMAccountingSink(Protocol).record(observation)        ← 契约（可能抛，由 client 吞）
    NoopAccountingSink.record() → return None              ← 默认实现

实现：services/llm_usage_persistence_service.py
    DatabaseLLMAccountingSink
        __init__(persistence_service=None, repository=None, runtime_bridge=None)
            默认构造 LLMUsagePersistenceService + LLMUsagePersistenceRuntimeBridge
        record(observation)   同步入口：service.persist() → 异常收敛为 warning
        arecord(observation)  异步入口：await bridge.persist_async() → to_thread →
                              同一 persist() → 异常收敛为 warning（含 CancelledError）

选择点：唯一 —— `LLMClient.__init__` 的 `accounting_sink or NoopAccountingSink()`
    生产默认：accounting_sink = None ⇒ **Noop**
    显式接入（当前仅测试 / 未来运维）：create_llm_client(..., accounting_sink=DatabaseLLMAccountingSink())
```

**为什么默认 Noop（源码中的设计理由，非缺陷）**：

```text
llm_usage_persistence_service.py 模块 docstring：
    "* **默认不接入 Client**：`create_llm_client()` 默认仍是 NoopAccountingSink，
      不会自动连数据库（§二十五）。"
client.py：
    "Phase 3.10.11：Accounting sink（与 Observation sink 相互独立；…默认 No-op）"
    "Phase 3.10.7：Observation sink … 默认 No-op——不持久化、不外发"
Project 既有一致原则：
    · 无 DATABASE_URL 时应用必须仍可用（main.py 不 init_db；get_engine() → None）
    · 观测 / 记账失败绝不改变业务结果；默认不引入 DB 依赖
    · 既有 Phase 3.10.x 契约：sink 生命周期与业务解耦、显式接入
```

## 5. Sink Lifecycle

```text
进程启动
   ↓ （首次业务调用触发懒加载）
get_default_llm_client() → create_llm_client() → LLMClient(accounting_sink=?)
   ↓ 单例缓存 → sink 若已接线则**每进程一个实例**（无 per-request sink）
每次 Provider 请求（成功 / 失败）
   ↓
chat() → _observe_call() → _emit_accounting()
   ↓ 若 sink 有 arecord → await（推荐路径，Step 3.10.15 线程边界）
   ↓ 否则 → sync record()
Repository / Session：**每次记录独立创建与关闭**（无长生命周期 Session，见 §14）
循环：无 —— 每次请求 emit 恰好一次；无队列 / 无后台任务 / 无 retry
```

## 6. Database Write Boundary

```text
LLMClient._emit_accounting(observation)
   ↓ DatabaseLLMAccountingSink.arecord()
LLMUsagePersistenceRuntimeBridge.persist_async()  → await asyncio.to_thread(...)
   ↓ worker thread
LLMUsagePersistenceService.persist(observation)
   ├── observation is None → None（不写入）
   ├── usage = consume_usage(observation)  → usage is None → **None（不写入）**
   └── assistant_request_id = current_assistant_request_id()   ← Trace 关联键
   ↓
LLMUsageRepository.create(request_id=provider_id, provider, model,
                          prompt/completion/total_tokens, assistant_request_id)
   ↓ with factory() as session, session.begin():   ← 新 Session + 新事务
INSERT ... ON CONFLICT DO NOTHING                          （request_id 幂等）
   ↓ RETURNING id
ai_ops.llm_usage_record                                    （1 行 / 1 次成功调用）

double insert 风险检查（结论：**不存在**）
    · emit 位置唯一：`LLMClient.chat()` → `_observe_call` → `_emit_accounting`（1 次）
    · DeepSeekProvider.chat() 仅 `return await self._client.chat(...)`（**不**独立 emit）
    · RagService / ToolChatService / TextToSQLService / Router / Tool Handler
      **均不接触 accounting sink**（全仓检索：sink 引用只在 client.py 与
      llm_usage_persistence_service.py）
    · 同一次 provider 请求即使被重试 / 重复持久化 → DB `ON CONFLICT DO NOTHING`
      （first-write-wins，返回 None = 正常幂等，不是错误）
```

## 7. One LLM Call → One Usage Record（既有测试证据，未新增测试）

```text
tests/test_llm_usage_persistence.py（真实 Client + MockTransport + 真实 sink）
    test_full_usage_persisted              —— 1 次调用 → 1 行（全字段）
    test_two_requests_two_rows             —— 2 次调用 → 2 行（不合并 / 不丢失）
    test_two_rounds_two_rows               —— 2 轮 Tool Calling → 2 行
    test_five_concurrent_requests_five_rows—— 5 并发 → 恰好 5 行
    test_refusal_usage_persisted_once      —— refusal 输出仍恰好 1 行
    test_usage_none_writes_no_row          —— usage=None → 0 行
    test_failure_observation_writes_no_row —— 失败 Observation → 0 行
    test_persistence_failure_keeps_business_result —— 持久化失败不影响业务结果
    test_default_client_writes_nothing     —— **默认 client（未配 sink）写 0 行**
tests/test_llm_usage_persistence_idempotency.py
    test_create_returns_none_on_duplicate_request_id / test_insert_uses_on_conflict_do_nothing
tests/test_llm_usage_persistence_runtime.py
    线程边界 / arecord 语义（含 CancelledError 收敛）
（本阶段**复用**既有证据，未新增重复测试）
```

## 8. Assistant Request Correlation

```text
AIOrchestratorService.execute()
    request_id = new_request_id()                  ← Assistant Trace ID（唯一生成点，Step 35）
    with assistant_trace_scope(request_id):        ← contextvar（Step 36）
        Router → RAG / Tool / Text-to-SQL（其中所有 LLM 调用都在 scope 内）
            ↓
        LLMUsagePersistenceService.persist()
            assistant_request_id = current_assistant_request_id()   ← 只读取，不生成
            ↓
        llm_usage_record.assistant_request_id = A

两套 ID 严格分离（不混淆、不覆盖）：
    request_id            = **Provider 请求 ID**（observation.request_id，来自 provider 响应）
    assistant_request_id  = **Assistant Trace ID**（contextvar；未绑定 → NULL）
    · persist() 只读 contextvar，**绝不**生成 / 回填 / 覆盖 observation.request_id
    · 旧链路（/api/chat · /api/rag/answer · /api/chat/with-tools）无 scope
      → assistant_request_id = NULL（永不被 Trace 匹配）
```

## 9. RAG / Tool / Text-to-SQL Interaction

```text
Accounting 归属：**实际 LLM Client 调用**（唯一 emit 点），不属于业务服务 —— 已确认：
    RagService        ：只调 client.chat()（LLM 生成），**不**调 sink
    TextToSQLService  ：generate() 的每次 attempt 各调一次 client.chat()，**不**调 sink
    ToolChatService   ：每轮一次 client.chat(tools=...)，**不**调 sink
    Router            ：LLM fallback 一次 client.chat()，**不**调 sink
    Tool Handler      ：完全不调用 LLM（get_inventory / get_work_order 仅 SQL）

⇒ same LLM request → 恰好 1 条 usage；不存在"业务层再记一次"的重复记账。
   推论（与 Step 54 一致）：一次 /api/ai/chat 的 usage 行数
       RAG 路由 = 1（检索非空时；空检索 0）
       TOOL 路由 = 0
       TEXT_TO_SQL 路由 ≤ max_attempts（默认 3）
   附：默认装配下 Router 的 llm_client=None（三处 AIRouterService(...) 均不注入）
       → `_route_via_llm` 抛 AIRouterClassificationError("LLM 客户端未配置")
       → route() 捕获后走 conservative RAG（source="fallback"）
       ⇒ **Router LLM fallback 在当前默认装配下不可达**（不产生 usage 记录）
```

## 10. Failure Path（当前事实，未修改）

```text
LLM 调用失败（timeout / 429 / 5xx / config error / response parse error）
   ↓ chat() except BaseException → _observe_call(error=exc) → **原异常照常抛出**
   ↓ build_llm_observation_safe(error=...) → Observation（usage = None）
   ↓ _emit_accounting()
   ↓ Database sink（若接线）：persist() → consume_usage() → None → **返回 None（0 行）**
   ↓ Noop sink（生产默认）：return None

结论：
    * 失败调用**不产生** usage 行（usage=None 不落库——本表是 usage storage，
      不是 request audit log；模块 docstring 明确 §十五）
    * 失败仍会向 observation sink 发一次 failure observation（生产为 Noop 观测）
    * 原始异常类型 / 传播路径完全不变（本阶段只记录，未改行为）
```

## 11. Accounting Failure Isolation

```text
四层防御（均已存在，本阶段未修改）：
    ① build 层：build_llm_observation_safe 抛错 → warning + return（不影响业务）
    ② client 层：_emit_accounting 的 try/except → warning（含 exc_info）
    ③ sink 层：record() / arecord() 捕获 Exception → warning；arecord 额外捕获
       asyncio.CancelledError（已完成的业务结果不被可选持久化取消）
    ④ repository 层：SQLAlchemyError → LLMUsageRepositoryError（事务已回滚）

语义保证：
    Observability / Accounting failure ≠ LLM business failure
    DB accounting failure **不触发** LLM retry / sleep / backoff / 队列（无此类代码）
    既有测试：test_persistence_failure_keeps_business_result（复用，不新增）
```

## 12. Environment Matrix

| Environment | LLM Client | Accounting Sink | DB Persistence |
| --- | --- | --- | --- |
| Unit Test（默认套件） | `MockLLMClient`（无 api_key）或注入 Fake Provider / 真实 `OpenAICompatibleClient` + `httpx.MockTransport` | 未注入 → **Noop** | ✗ |
| DB Test（`RUN_DB_TESTS=1`） | 测试内显式构造真实 Client + MockTransport | **`DatabaseLLMAccountingSink()`**（4 个文件显式注入：`test_llm_usage_persistence.py` ×13 · `..._idempotency.py` ×9 · `..._runtime.py` ×1 · `test_assistant_trace_rag_integration_e2e_db.py` ×1） | ✅（测试库；request_id 幂等；用例自带清理） |
| Default App | `get_default_llm_client()` 单例 | **Noop** | ✗ |
| Production | **与 Default App 相同**（无独立配置 / 无独立装配 / 无环境变量开关） | **Noop** | ✗ |

```text
Production follows default application path.
（main.py / api/ / services/ 中无任何数据库 sink 装配；config.py 无 accounting / usage /
 persistence / sink 配置项 —— 全仓检索为空）
```

## 13. Production Wiring Point（只设计，不实现）

```text
候选 1（**推荐**）llm/client.py :: get_default_llm_client()
    形态（未来）：默认 client 构造时按需注入 DatabaseLLMAccountingSink()
        · 唯一默认 client 工厂，RAG / TextToSQL / ToolChat 全部经此获得 client
        · 单一改动点；不触碰任何业务服务签名
        · 无 DATABASE_URL 时仍安全：Repository 抛 LLMUsageRepositoryError
          → sink 收敛为 warning（应用照常可用）
    注意：**不要**改 create_llm_client() 的默认语义 —— 大量既有测试直接调用它
          （test_llm_provider / test_llm_accounting_* / test_llm_observability /
            real smoke*），默认落库会改变这些测试的副作用与离线可运行性

候选 2 app factory（main.py create_app()）
    现状：main.py 只有 FastAPI + include_router，**不创建任何 LLM Client**；
    服务是懒加载 get_default_llm_client() ⇒ 在此注入需要新增依赖穿透
    （把 client 传入 Orchestrator / Router / RAG / TextToSQL）= 更大改动。不推荐。

明确禁止（与 accounting 归属冲突）：在 TextToSQLService / RagService /
    ToolChatService / AIRouterService 内部注入 sink。

未来可选配置（**本阶段不新增**）：在候选 1 处引入显式开关（如
    "有 DATABASE_URL 即接线 / 或独立 enable flag"）—— 属 potential future
    configuration，需与运维确认后再设计；本阶段不实现、不加环境变量。
```

## 14. Session / Transaction Analysis

```text
现状（sink 接线后即生效，无需另改）：
    record / arecord → persist → Repository.create
        with factory() as session, session.begin():
            INSERT ... RETURNING id
    · **per-record Session**（每次记录新建、块结束即关闭）
    · commit 由 `session.begin()` 上下文管理器在块尾自动完成；异常 → 自动 rollback
    · **无** request-scoped session、无跨请求复用、无 session 泄漏 / 连接泄漏 / 事务泄漏
    · 同步 INSERT 被 `asyncio.to_thread` 移出 event loop 线程（Step 3.10.15）
      ⇒ 不阻塞事件循环；每次持久化占用 1 个连接（来自连接池）极短时间

设计风险（只记录，不优化）：
    R1 每次 usage 记录 = 1 次连接获取 + 1 个事务；高 LLM 吞吐时受
       pool_size / max_overflow 约束（并发峰值可能出现等待）
    R2 arecord 由业务 await（非 fire-and-forget）⇒ LLM 请求延迟包含一次
       worker 线程调度 + 1 次 INSERT；异常仍被吞掉，不改变业务结果
    R3 若未来改为 request-scoped session（复用同一事务），会引入长事务 /
       跨 await 的 session 生命周期问题 —— **不建议**（现状 per-record 更安全）
    R4 幂等由 DB 唯一索引保证（request_id），无需在进程内去重
```

## 15. Security Boundary

```text
写入字段（= ORM 列白名单，未扩大）：
    request_id（**Provider** 请求 ID）· provider · model ·
    prompt_tokens · completion_tokens · total_tokens ·
    assistant_request_id（Trace 关联键）· created_at（server now()）· id（主键）
    + schema/table：ai_ops.llm_usage_record

明确**不写入**（源码级确认：persist 只读 usage / request_id / provider / model）：
    prompt · messages · raw response / raw SDK response · API key / authorization /
    password / database URL · SQL · RAG chunks / chunk content · tool arguments /
    ToolResult.data · traceback

既有测试证据：test_insert_columns_match_security_whitelist（INSERT 列 == 白名单）；
    test_columns_are_approved_only（表列集合受控）
```

## 16. Recommendation

```text
Production LLM Usage Persistence: **DESIGN READY**

区分两件事：
    Implementation exists        = ✅（sink / persistence service / runtime bridge /
                                     repository / 幂等 / 失败隔离 / 测试全部就绪）
    Production wiring exists     = ❌（默认装配 NoopAccountingSink；刻意设计，非 bug）

后果（事实陈述）：
    · 生产默认 Assistant Trace 的 llm_usage[] 为 **[]**（Tool / RAG 段正常持久化）
    · Step 36 的 assistant_request_id 关联在当前生产装配下**无数据可关联**

建议（待后续阶段决定，本阶段不实施）：
    1. 采用 §13 候选 1 作为唯一接线点（llm/client.py 默认 client）
    2. 接线前明确：DB 不可用时的行为（已天然安全）、是否需开关、观测性
       （接线后 LLM 调用将产生 DB 写：需与运维确认连接池与容量）
    3. 不改 create_llm_client() 默认语义（保护既有测试与离线可运行性）
    4. 接线后应补 1 个契约测试（默认装配下 → 1 次 LLM 调用 = 1 行 usage，
       且在无 DB 环境下不报错）—— 属未来阶段

No production code changed.（本阶段未修改任何生产代码）
```

## 17. Limitations

```text
* 未在真实生产装配下运行端到端验证（默认 sink 为 Noop ⇒ 观察不到写入；
  若强行接线属 §十三 的未来阶段）
* "生产 = 默认路径" 的判断基于仓库内装配证据（main.py / api / services / config），
  不含仓库外的部署编排（本仓库无应用 Dockerfile / 部署脚本）
* 失败路径的 usage=None 行为来自源码与既有测试，未做真实 provider 故障注入
* 连接池压力 / 延迟影响为**设计层面的定性分析**，未做压测（明确不做性能优化）
* 未来配置开关（是否启用 / 何时启用）未评估（本阶段禁止新增 runtime config）
* 本阶段未新增测试、未改生产代码；接线点结论为设计提案
```
