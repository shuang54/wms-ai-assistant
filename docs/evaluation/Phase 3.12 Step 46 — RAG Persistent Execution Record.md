# Phase 3.12 Step 46 — RAG Persistent Execution Record

> 把 Step 43/44 已生产接线的 RAG Runtime Observation **持久化到 PostgreSQL**
> （Step 45 契约的落地实现）。**不接 Assistant Trace**；不改 Router /
> Orchestrator / RAG 核心 / LLM Usage / Tool Execution / 旧 API。
>
> 0 DeepSeek / 0 网络；DB 写入 = 测试专用；DB residue = 0。

---

## 1. PostgreSQL Table

```text
ai_ops.rag_execution_record（1 张新表；与 public 业务 schema 隔离）

id                 BIGINT  PRIMARY KEY AUTOINCREMENT   ← 数据库生成（观测 DTO 不持有）
request_id         VARCHAR(128) NOT NULL               ← Assistant Trace ID（= A）
started_at         TIMESTAMPTZ  NOT NULL
finished_at        TIMESTAMPTZ  NOT NULL
duration_ms        FLOAT        NOT NULL
result_count       INTEGER      NOT NULL
used_chunks_count  INTEGER      NOT NULL
top_k              INTEGER      NOT NULL
context_truncated  BOOLEAN      NOT NULL
context_chars      INTEGER      NOT NULL
reranker_used      BOOLEAN      NOT NULL
rerank_elapsed_ms  FLOAT        NULL                   ← NULL ≠ 0（未用 Reranker）
chunk_ids          JSONB        NOT NULL               ← 项目既有 JSONB 方案
document_ids       JSONB        NOT NULL

索引（最小；命名沿用 ix_<table>_<column>）：
    ix_rag_execution_record_request_id   （Assistant Trace 主查询键）
    ix_rag_execution_record_started_at   （时间序 / 未来 retention）
未建：chunk_id / document_id / project_id / query / similarity（无查询需求）
唯一约束：无（观测是事件；一次 request 允许多行）

建表机制：`Base.metadata.create_all()`（Model 已在 models/__init__.py 注册）
    —— 未引入 Alembic / 未手工 DDL / 未修改生产 DB。
```

## 2. ORM Model

```text
backend/app/db/models/rag_execution_record.py
    RAG_EXECUTION_SCHEMA = "ai_ops" · RAG_EXECUTION_TABLE = "rag_execution_record"
    RAG_EXECUTION_PERSISTED_FIELDS = 13 字段（顺序 = 契约顺序）
    RagExecutionRecordModel（__table_args__：2 个索引 + {"schema": "ai_ops"}）
    JSONB：chunk_ids / document_ids（仅 ID；无正文 / 无 similarity / 无 embedding）
```

## 3. Repository

```text
backend/app/db/rag_execution_repository.py
    RagExecutionRepositoryError        （DB failure 的 typed error）
    RagExecutionRecordRow              （frozen；id + 13 字段 = 14；非 ORM）
    RAG_EXECUTION_READ_COLUMNS         （显式列；禁 SELECT *）
    RagExecutionRepository(session_factory=None)
        create(observation)            → INSERT ... RETURNING（内部事务）
        build_insert(observation)      （唯一写 SQL 构造点；bound params）
        get_by_request_id(request_id)  → WHERE request_id = :p ORDER BY id ASC
        build_request_select(request_id)（唯一读 SQL 构造点）
映射：逐字段显式；tuple → JSON 数组；读回 JSONB → tuple（顺序保持）
失败：SQLAlchemyError → RagExecutionRepositoryError（**绝不**返回 []）
校验：读路径 request_id 非 str → TypeError；空/空白/超长 → ValueError（DB 之前）
```

## 4. Persistence Service

```text
backend/app/services/rag_execution_persistence_service.py
    RagExecutionPersistenceService.persist(observation) → RagExecutionRecordRow
    * 最薄一层：不建 SQL / 不建 Session / 不操作 ORM query / 不吞异常
    * 输入就是 Observation（不转 dict / 不经 Snapshot / 不重新计算计数）
```

## 5. Persistence Adapter

```text
backend/app/services/rag_execution_persistence_adapter.py
    RagExecutionPersistenceAdapter（实现 RagExecutionObserver.record()）
    * 成功 → 返回 RagExecutionRecordRow（便于测试/装配断言）
    * 失败 → **不抛出**；warning 日志（error_type + request_id；无 traceback、
      无 payload、无 credentials）；RAG 业务结果不受影响
```

## 6. Composite Observer

```text
backend/app/services/composite_rag_execution_observer.py
    CompositeRagExecutionObserver(*observers).record(observation)
    * 依次 fan-out（顺序 = 装配顺序）；子 observer 互相隔离：
      Persistence 失败不影响 Memory 观测；Memory 失败不跳过持久化；
    * 单个子 observer 抛错 → warning（observer_type + error_type + request_id），
      后续 observer **仍会被调用**（与 CompositeToolExecutionObserver 同构）
```

## 7. Production Wiring

```text
backend/app/services/rag_observability_runtime.py（应用级单实例；唯一创建点）
    _RAG_EXECUTION_COLLECTOR          = InMemoryRagExecutionCollector()
    _RAG_EXECUTION_PERSISTENCE_SERVICE = RagExecutionPersistenceService()
    _RAG_EXECUTION_PERSISTENCE_ADAPTER = RagExecutionPersistenceAdapter(service)
    _RAG_EXECUTION_OBSERVER            = CompositeRagExecutionObserver(
                                             collector, adapter)
    _RAG_SERVICE                       = RagService(observer=_RAG_EXECUTION_OBSERVER)
    _RAG_PERSISTENT_QUERY_SERVICE      = RagExecutionPersistentQueryService()

    accessors：get_observed_rag_service() · get_rag_execution_collector() ·
        get_rag_execution_persistence_adapter() ·
        get_rag_execution_persistent_query_service() ·
        get_rag_observability_query_service()

生命周期：一进程 = 一 Collector = 一 Service = 一 Adapter = 一 RagService
    = 一 QueryService（不每请求新建；DB session 由 Repository 按操作创建）
```

## 8. Persistence E2E

```text
tests/test_rag_runtime_observability_e2e_db.py（RUN_DB_TESTS=1；8 passed）
    TestClient(create_app()) → POST /api/ai/chat（真实 Composition Root /
    Orchestrator / Router / RagService / Composite / Adapter / Service /
    Repository / PostgreSQL；Fake 仅 Vector Search + LLM transport）
    实测：200 · route=rag · metadata.request_id=A ·
          ai_ops.rag_execution_record 出现 request_id=A（1 行；字段一致：
          result_count=2 / chunk_ids=(101,102) / rerank NULL / context_chars=64）
    Runtime 与 Persistent 两个视图**并列**（同一 request_id，互不合并）
```

## 9. Cross Request Isolation

```text
POST A → 行 A；POST B → 行 B（A ≠ B）
    query(A) → 仅 A（1 行）· query(B) → 仅 B（1 行）· 表新增 = 2
```

## 10. Non-RAG Isolation

```text
TOOL 请求（Fake handler + Tool 持久化写边界关闭）
    → route=tool · RAG 表新增 = **0**（不产生假 RAG 记录）
```

## 11. Legacy API

```text
/api/rag/answer · /api/chat → 200（边界替换为 Fake）
    → RAG 表新增 = **0**（旧链路无 assistant_request_id；未人为生成）
```

## 12. Failure Isolation

```text
生产装配下 Adapter.record 抛 RuntimeError：
    POST /api/ai/chat → 200 · route=rag · content 正常 · metadata.request_id 存在
    DB 未落行 · warning 日志（error_type=RuntimeError，不静默吞掉）
（单元层：Adapter 失败 → None + warning；Composite 失败 → 后续 observer 仍执行）
```

## 13. Restart-like Test

```text
POST RAG → A（落库）
    → clear InMemoryRagExecutionCollector（模拟重启）
    → Runtime 查询 A → ()
    → **新建** RagExecutionPersistentQueryService → A 仍在（DB 行）
    ⇒ Persistent Observation ≠ Runtime Memory
```

## 14. Security

```text
表 / Row DTO / SQL 均**不含**：query · answer · content · similarity ·
    embedding · prompt · messages · raw_response · SQL · password · api_key ·
    authorization · database_url · project_id · tool_call_id
chunk_ids / document_ids 只保存 **ID**（JSONB 数组；去重 + 首次出现顺序）
日志白名单：只允许 request_id / error_type / observer_type（无 payload / 无
    traceback；未使用 logger.exception）
显式字段映射（无 vars() / __dict__ / asdict / model_dump；无自动序列化）
```

## 15. Tests

```text
tests/test_rag_execution_persistence.py          37 passed（纯离线；0 DB）
    ORM（列 / 主键 / 可空 / schema / 索引 / JSONB）· Repository（映射 / INSERT
    与 SELECT 形态 / bound params / 注入串 / typed error / 校验先于 DB）·
    Service·Adapter（委派 / 失败隔离 + 安全日志）· Composite（fan-out /
    互相隔离 / 契约校验）· QueryService（空 / 失败 / 校验先于 Repository /
    只读 / Row 白名单）· Security（无敏感值 / 无敏感标识符）
tests/test_rag_execution_persistence_db.py       18 passed（RUN_DB_TESTS=1）
    insert / read（13 字段 + NULL 语义 + 顺序）/ 多条（A=2、B=1、id ASC）/
    empty / invalid（校验先于查询）/ failure semantics（typed error，非 []）/
    Adapter 真实链路 + 真实失败隔离 / QueryService / restart-like / residue=0
tests/test_rag_runtime_observability_e2e_db.py    8 passed（RUN_DB_TESTS=1）
契约 / 运行时时序测试同步：
    test_rag_persistent_observation_contract.py（32；Deferred → 实现态一致性）
    test_rag_runtime_observability.py（44）· ..._e2e.py（16；持久化边界 Fake）
    test_rag_trace_coverage_audit.py（22；仅契约定义的持久化存在）
```

## 16. DB Residue

```text
清理方式：定向 DELETE（by request_id 前缀 / by id > baseline）；
    **未使用 TRUNCATE**；未触碰任何真实数据
最终：ai_ops.rag_execution_record 测试行 = 0（fixture teardown 断言）
    llm_usage_record = 0 · tool_execution_record = 0
```

## 17. Deferred（本阶段明确不做）

```text
Assistant Trace 集成 · RAG Query / Metrics HTTP API · retention / TTL /
cleanup / partition · 幂等约束 · 聚合指标 · 多 worker 共享视图
Conversation / Memory / Agent / MCP / OpenTelemetry / Streaming / Dashboard
```

## 18. Limitations

```text
* 写入是**同步** best-effort（一次执行一行；失败只 warning；无 retry / queue）
* 无 retention：表会持续增长（Step 45 已登记；策略属后续阶段）
* 无幂等约束：同一 request 若被重复执行会产生多行（第一版按"事件"语义）
* 无 HTTP 读端点：`RagExecutionPersistentQueryService` 仅供内部调用
* Runtime 视图仍是每进程一份（多 worker 各自内存），Persistent 侧天然共享
* 未接 Assistant Trace：`/api/observability/assistant-trace/{id}` 仍不含 RAG
* lint unavailable（环境限制）
```
