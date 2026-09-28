# Phase 3.12 Step 45 — RAG Persistence Boundary Audit

> **只做审计 + 契约设计**：不建表 / 不 migration / 不建 Repository / 不接
> Assistant Trace / 不新增 HTTP API / 不改生产运行时代码
> （0 production runtime change；契约只存在于测试与本文档）。
>
> 目的：先把 **RAG 持久化的数据契约与安全边界**固定下来，避免 Step 46 同时改
> Schema + Repository + Trace 导致范围失控。

---

## 1. Current State

```text
RAG Runtime Observation（Step 43/44，已在生产接线）
    POST /api/ai/chat → AIOrchestratorService（request_id = A）
        → RagService（应用级实例；observer = 应用级 Collector）
        → RagExecutionObservation（13 字段；frozen）
        → InMemoryRagExecutionCollector（deque(maxlen=1000)；进程内）
        → RagObservabilityQueryService（只读；内部，无 HTTP API）

现状性质：**纯运行时**（进程重启即丢失 / 多 worker 各自一份视图）
```

## 2. Target State（Step 46 目标，本阶段**不实现**）

```text
RagExecutionObservation（frozen；13 字段；Runtime 与 Persistent 共用同一 DTO 语义）
        ↓ RagExecutionObserver（既有端口；不新造生命周期）
CompositeRagExecutionObserver（未来；与 Tool 侧同构）：
        ├── InMemoryRagExecutionCollector（Runtime；现状不变）
        └── RagExecutionPersistenceAdapter（未来；失败只 warning）
                    ↓
            RagExecutionPersistenceService（未来；DTO → Record 显式映射）
                    ↓
            RagExecutionRepository（未来；SQLAlchemy 写/读；bound params）
                    ↓
            PostgreSQL：ai_ops.rag_execution_record（未来；**当前不存在**）
                    ↓
            RagExecutionPersistentQueryService.list_by_request_id()（未来；只读）
```

## 3. Fields（逐字段审计）

| # | 字段 | 持久化 | nullable | 长度限制 | 索引 | 泄露风险 | 说明 |
| - | --- | --- | --- | --- | --- | --- | --- |
| 1 | `request_id` | ✅ | ❌ NOT NULL | VARCHAR(128)（= `ASSISTANT_REQUEST_ID_MAX_LENGTH`） | ✅ **第一优先级** | 无（不透明 uuid） | Assistant Trace ID；唯一关联键 |
| 2 | `started_at` | ✅ | ❌ NOT NULL | timestamp(tz) | ✅ 第二优先级（时间排序 / 未来 retention） | 无 | 观测起点 |
| 3 | `finished_at` | ✅ | ❌ NOT NULL | timestamp(tz) | ❌ | 无 | 观测终点（≥ started_at） |
| 4 | `duration_ms` | ✅ | ❌ NOT NULL | float | ❌ | 无 | 端到端耗时 |
| 5 | `result_count` | ✅ | ❌ NOT NULL | int ≥ 0 | ❌ | 无 | Vector Search 候选数 |
| 6 | `used_chunks_count` | ✅ | ❌ NOT NULL | int ≥ 0 | ❌ | 无 | 纳入 Context 的片段数 |
| 7 | `top_k` | ✅ | ❌ NOT NULL | int ≥ 0 | ❌ | 无 | 本次生效 top_k（配置相关，非业务数据） |
| 8 | `context_truncated` | ✅ | ❌ NOT NULL | bool | ❌ | 无 | 是否截断 |
| 9 | `context_chars` | ✅ | ❌ NOT NULL | int ≥ 0 | ❌ | 无 | Context 字符数 |
| 10 | `reranker_used` | ✅ | ❌ NOT NULL | bool | ❌ | 无 | 配置开关事实 |
| 11 | `rerank_elapsed_ms` | ✅ | ✅ **NULL 允许** | float ≥ 0 | ❌ | 无 | 未用 Reranker → NULL（**不是 0**） |
| 12 | `chunk_ids` | ✅ | ✅ NULL/空允许 | int[]（JSON / 数组列） | ❌ | **低**（identifier；经授权读取） | 去重 + 首次出现顺序 |
| 13 | `document_ids` | ✅ | ✅ NULL/空允许 | int[] | ❌ | **低** | 同上 |

```text
共同规则：
    · 13 字段 **1:1** 进入持久化（列名不变）；不新增/不改名/不丢字段；
    · 主键 `id`（数据库自增）—— 沿用 Tool / LLM 既有主键模式（读侧沿用 `id` 命名，
      **不**新增 `record_id` 字段）；观测 DTO 不持有主键；
    · runtime 的 nullable 语义原样保留（`rerank_elapsed_ms` NULL ≠ 0）；
    · chunk_ids / document_ids 是**引用 ID**（Step 45 明确允许持久化），
      但不得随之保存任何正文 / similarity / embedding。
```

## 4. Forbidden Fields（永久安全边界）

```text
query · answer · chunk content · document content · similarity · embedding ·
prompt · LLM messages · raw LLM response · SQL · database connection ·
credentials · Authorization · api_key · password · database_url ·
traceback · ORM Session / Engine / Repository 对象
project_id · tool_call_id（RAG 观测不承载这两者；保持现状）
```

```text
强制手段（契约测试锁定）：
    1. 字段白名单：`dataclasses.fields()` == 13 契约字段（顺序一致）；
    2. 序列化白名单：`to_dict()` 键集合 == 同一 13 字段；
    3. **显式字段映射**（无 vars() / __dict__ / asdict / model_dump）；
    4. 行为验证：真实 pipeline（Fake 边界；query / chunk 正文为 sentinel）
       → 序列化结果不含任何 sentinel；
    5. `similarity` 刻意不进入观测（Step 42 结论；本阶段复核维持）。
```

## 5. Correlation

```text
字段名结论：**`request_id`**（沿用 Tool 侧命名；不新增第二套 correlation id）

对照既有三处（同一值 = Assistant Trace ID = A）：

    LLMUsageRecord.assistant_request_id      ← 因 request_id 已被 Provider id 占用
    ToolExecutionRecord.request_id           ← Tool 无 provider id → 直接使用
    RagExecutionObservation.request_id       ← 本阶段确认沿用同一命名
    （未来）RagExecutionPersistentRecord.request_id = A

唯一来源：AIOrchestratorService.execute() 的 new_request_id()（Step 35/36）
        → assistant_trace_scope(A)
        → RagService 读取 current_assistant_request_id()（**不生成 / 不推断**）
测试证据：AST —— rag_service 只调用 current_assistant_request_id（无 uuid /
        new_request_id）；观测模块不存在任何 id 生成；行为 —— scope(A)/scope(B)
        两次执行得到 request_id == A / B；长度上限 == 128（与 LLM 列一致）。
```

## 6. Failure Policy

```text
Observability failure ≠ Business failure（沿用 Step 43/44 与 Tool 侧同构策略）

Runtime（现状，已实测）：
    Collector.record 抛错 → warning → /api/ai/chat 仍 200（RAG 成功）

Persistent（未来 Step 46 必须一致）：
    Repository / DB 写入失败 → Adapter 收敛为 **warning**（仅记 error_type），
    **绝不**穿透到 RAG 业务结果；**不静默吞掉**（有日志、有 error_type）；
    **不**新增 retry / queue / 后台任务。

Read 侧（未来）：
    empty result → `[]`（不是错误）
    DB failure   → typed error（`RagExecutionRepositoryError`）——**绝不**降级为 `[]`
    非法 request_id → 校验先于查询（ValueError / TypeError；Repository 0 次调用）
（既有同构证据：ToolExecutionPersistentQueryService.list_by_request_id 的
  空 → [] / 失败 → ToolExecutionRepositoryError / 非法 → 校验先于查询，均已实测。）
```

## 7. Read Boundary（设计，不实现）

```text
RagExecutionPersistentQueryService（未来）
    list_by_request_id(request_id: str) -> list[RagExecutionPersistentRecord]
        ├── 服务层方法名：list_by_request_id（与 Tool 侧一致）
        ├── 仓储层方法名：get_by_request_id（既有只读命名，精确匹配）
        ├── 返回：显式映射的安全字段（含 id；无 query / content / similarity …）
        ├── 顺序：按 id ASC（落库顺序 = 执行顺序；本层不重排序）
        ├── 只读：无写 / 无事务开启 / 不与 Runtime Collector 合并
        └── 无分页：request 作用域查询（一次请求的观测条目天然有界）
```

## 8. Index（设计，不 migration）

```text
第一优先级：request_id（Assistant Trace 的主查询键）
第二优先级：started_at（时间排序 / 未来 retention 策略）
命名沿用既有约定：ix_<table>_<column>（如 ix_llm_usage_record_created_at）

明确**不**建：query / document_id / chunk_id / project_id / tool_name 索引
（没有明确查询需求；避免投机索引）
唯一约束：第一版 **无**（不做幂等约束）——观测是事件；
    Tool 侧同样无唯一约束；LLM 侧的部分唯一索引是"Provider 幂等"需求，不适用
```

## 9. Retention / Capacity（设计，不实现）

```text
Runtime capacity ≠ Persistent retention
    · Runtime：deque(maxlen=1000)（内存窗口；重启即失）
    · Persistent：**不简单复制 1000** —— retention 属策略问题（数据量、合规、
      查询窗口），本阶段只登记"未来需要 retention policy"，不实现 TTL /
      cron / cleanup worker / partition / 后台任务。
```

## 10. Assistant Trace

```text
**本阶段不修改 Assistant Trace**（Step 45 only defines RAG persistence boundary）：
    · AssistantTraceQueryService / AssistantTraceResponse /
      /api/observability/assistant-trace/{request_id} —— 全部保持不变；
    · 契约测试断言 AssistantTraceResponse 仍严格
      {assistant_request_id, llm_usage, tool_executions}；
    · RAG 与 Assistant Trace 的集成 **deferred**（另开阶段决定）。
```

## 11. Tests

```text
tests/test_rag_persistent_observation_contract.py   32 passed（纯离线；0 DB）
    FieldContract(5)：13 字段顺序 / to_dict 键 / 列集合 = 13 + id /
        主键沿用既有自增模式（两种 Row 首字段均为 id）/ 观测无 id 字段
    ForbiddenFields(4)：字段白名单 / 序列化白名单 / 模块声明 / 真实 pipeline
        sentinel 不泄露（query + chunk 正文）
    RequestIdCorrelation(5)：scope(A)/(B) 行为验证 / 唯一来源（AST）/
        观测模块不生成 id / 长度上限 128 / 命名与 Tool 侧一致
    SerializationDeterminism(4)：去重 + 首次出现顺序 / 重复序列化稳定 /
        JSON 往返保序 / id 为非负 int
    ErrorSemantics(4)：空 → [] / 失败 → typed error（实测既有 Tool 边界）/
        非法输入不触达 Repository / RAG 设计镜像同一语义
    IndexDesign(4)：request_id 优先 + started_at / 无投机索引 / 无唯一约束 /
        命名沿用既有约定
    DeferredBoundaries(6)：无 rag 表 / 无 rag 持久化模块 / deferred 清单 /
        写端口 = 既有 observer 接口 / Assistant Trace + HTTP API 未变 /
        未新增生产 DTO 或 Repository 文件
```

## 12. Deferred（本阶段明确不做）

```text
No DB table          · No migration        · No Repository
No SQLAlchemy Model  · No DB Session       · No PostgreSQL write / read
No Persistence Adapter（只设计 Composite 形态）
No Assistant Trace integration             · No HTTP API
No retention / TTL / cleanup / partition
No Conversation / Memory / Agent / MCP / OpenTelemetry / Streaming / Dashboard
```

## 13. Limitations

```text
* 契约为**设计态**：尚未有任何实现可供端到端验证（Step 46 才落地）
* `chunk_ids` / `document_ids` 的数组列形态（JSONB vs int[]）与序列化细节
  留待 Step 46 决策（本阶段只固定"去重 + 顺序 + 非负 int"语义）
* 幂等 / 重复写入策略未定（第一版无唯一约束；如需幂等须单独论证）
* retention 策略未定（明确 defer）
* 多 worker 下 Runtime 视图仍是每进程一份（既有 Tool Runtime 的同一限制）
* lint unavailable（环境限制）
```
