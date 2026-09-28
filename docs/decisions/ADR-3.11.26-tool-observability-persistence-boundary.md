# ADR-3.11.26 — Tool Observability Persistence Boundary

## Status

**Proposed（设计已接受；实现分阶段推进）**

> Step 26：**只记录边界设计**，不实现任何持久化。

### Implementation Status（Step 27 追加；Step 28 更新）

```text
Step 27:
ORM Model + Repository implemented.

    ai_ops.tool_execution_record（ORM: ToolExecutionRecordModel）
    ToolExecutionRepository.create() / get_by_request_id()
    事务边界 / 回滚 / 内部 Row DTO / 安全列白名单 —— 已实现并通过测试
    初始化沿用 init_db() + create_all（无 Alembic）
    DB 集成测试（RUN_DB_TESTS=1）通过；DB residue = 0

Step 28:
PersistenceService = implemented
    ToolExecutionPersistenceService.persist(record) -> Row
    （输入就是 ToolExecutionRecord；不吞异常）

PersistenceAdapter = implemented
    ToolExecutionPersistenceAdapter.on_execution(record)
    （实现 Observer 协议；失败 → warning；不 import DB）

Observer Integration = implemented
    Composition Root 装配 fan-out：
        CompositeToolExecutionObserver(Collector, PersistenceAdapter)
    子 observer 互相隔离（Memory 失败不影响 Persistence，反之亦然）

Step 29:
Persistent Query Boundary = implemented
    ToolExecutionPersistentQueryService.list_recent(limit=...)
        → Repository.list_recent()（ORDER BY started_at DESC, id DESC LIMIT n）
        → ToolExecutionSnapshot（显式映射；主键 id 不外泄）
    只读（SELECT only）；DB failure → RepositoryError（不是 []）；
    limit 1~1000（模块级常量；非法 → ValueError）

Runtime Query（ToolObservabilityQueryService → InMemoryCollector）**未变**；
两者并列，**不合并**（避免去重 / 排序 / 窗口 / 分页语义冲突）

Step 30:
Persistent History HTTP API = implemented
    GET /api/observability/tools/history?limit=100
        → ToolExecutionPersistentQueryService.list_recent(limit)
        → ToolExecutionSnapshot × 11 字段（ISO 8601）
        → {"items": [...]}
    只读（GET only）；空库 → 200 + items=[]；limit 非法 → 422；
    DB 失败 → 502（**不回退**内存视图）
    Runtime API（GET /api/observability/tools · /metrics）契约未变

Step 31:
Persistent History Pagination = implemented
    GET /api/observability/tools/history?limit=100&offset=0
        → PersistentQueryService.list_recent(limit, offset)
        → Repository: ORDER BY started_at DESC, id DESC LIMIT n OFFSET m
        → {"items": [...], "limit": …, "offset": …}
    仅 LIMIT + OFFSET（无 cursor / keyset / total_count / COUNT）
    offset 非法 → 422（不触达 Service / Repository / DB）；
    空页 / 超大 offset → 200 + items=[]；
    DB 失败 → 502（无 fallback）
    Runtime 端点（0 参数）与 Runtime 行为未变

Step 32:
Persistent History Filtering = implemented
    GET /api/observability/tools/history
        ?project_id=project-a&tool_name=get_inventory&success=true
        &limit=20&offset=0
        → Repository: WHERE <exact filters>（bound parameters；AND）
                       ORDER BY started_at DESC, id DESC
                       LIMIT n OFFSET m
    仅**精确**匹配（无 LIKE / ILIKE / regex / fuzzy / 全文检索 / 时间范围）；
    success=False **≠** 未提供（None = 不过滤）；
    非法值 → 422（不触达 Service / Repository / DB）；无匹配 → 200 + items=[]；
    DB 失败 → 502（无 fallback）；Snapshot 仍严格 11 字段
    Runtime 端点（0 参数）与 Runtime 行为未变

Step 33:
Persistent Metrics = implemented
    GET /api/observability/tools/metrics/persistent
        ?project_id=…&tool_name=…&success=…
        → ToolExecutionPersistentQueryService.metrics(...)
        → Repository.get_metrics()  （SQL: count(*) / count(*) FILTER /
                                      sum / avg / max；无 WHERE 时省略）
        → ToolExecutionMetricsSnapshot（8 字段；复用既有 DTO）
    空数据集：计数 0；rates / avg / max = None（不是 0）；SUM 归一 0.0
    精确过滤（与 History 一致；无 LIKE / regex / fuzzy / 时间范围）；
    非法值 → 422；无匹配 → 200 + 计数 0；DB 失败 → 502（无 fallback）
    无 limit / offset（聚合单行）；Runtime Metrics 未变（仍读内存）

Retention = Deferred
Dashboard = Deferred
Prometheus = Deferred
OpenTelemetry = Deferred

运行时状态（Step 28 结束）：
    ToolExecutionService → ToolRegistry（未变；无 DB 依赖）
    ToolExecutionObserver → Composite（Memory + Persistence）
    HTTP API（GET /api/observability/tools · /metrics）契约未变，
        仍只读内存（Database = persistent history only）
    Persistence 失败被 Adapter 隔离 → ToolResult 不变
```

---

## Context

Step 25 完成后 Tool Observability 链路：

```text
Tool Execution
    ↓
ToolExecutionRecord（frozen，11 字段）
    ↓
ToolExecutionObserver（Protocol）
    ↓
InMemoryToolExecutionCollector（process-local，max_records=1000，FIFO）
    ↓
ToolObservabilityQueryService（只读）
    ↓
Snapshot / Metrics → Serialization → HTTP Read API → JSON
```

问题：Collector 是**进程内存**：

```text
应用重启  → records 丢失
workers>1 → 每个 worker 只看到自己的数据
无历史     → 无法回答"昨天成功率是多少"
```

---

## Current Architecture（Survey：真实代码）

### 数据库访问入口

```text
backend/app/db/session.py
    get_engine()            Engine（懒加载；DATABASE_URL 为空 → None）
    get_session_factory()   sessionmaker[Session]
    get_db()                FastAPI Depends（生成器；调用方负责 commit/rollback）
    ping_database()         SELECT 1 健康探测

backend/app/db/base.py             DeclarativeBase（SQLAlchemy 2.x）
backend/app/db/models/             ORM Model（knowledge_document /
                                   knowledge_chunk / llm_usage_record）
backend/app/db/init_db.py          create_all（无 Alembic；显式扩展点）
backend/app/db/llm_usage_repository.py   项目**唯一**的 Repository
```

### Repository 形态（实测）

```python
class LLMUsageRepository:
    def __init__(self, session_factory: sessionmaker[Session] | None = None): ...
    # 内部：with factory() as session, session.begin(): ...
```

* Repository 接收 **session_factory**（不是 Engine、不是已开 Session）；
* 事务是 **Repository 内部**的 `session.begin()`（一次操作一个 Session）；
* 读路径不开写事务（`with factory() as session:`，无 `begin()`）；
* 返回内部 plain record（`LLMUsageRecordRow`），**不返回 ORM 对象**；
* 失败 → `LLMUsageRepositoryError`（已回滚）→ 上层转 warning。

### 是否存在 Unit of Work（实测）

```text
UnitOfWork       无（全仓库 0 处 unit_of_work / uow）
Transaction      有，但粒度 = 单次 Repository 操作（session.begin()）
Repository       有，仅 1 个（llm_usage_repository）
Service          有（Persistence Service / Query Service / Analytics…）
Migrations       无 Alembic；用 init_db() + create_all + 显式幂等 DDL
```

### 其它 DB 使用点（非 Repository 形态）

```text
services/vector_search_service.py       直接用 session_factory
services/knowledge_ingestion_service.py 直接用 session_factory
services/sql_executor_service.py        Engine（只读 SQL）
services/schema_explorer_service.py     Engine（元数据）
projects/engine_provider.py             create_engine（per-project 数据源）
tools/get_inventory.py                  Engine（业务只读 SQL）
tools/get_work_order.py                 Engine（业务只读 SQL）
```

→ 结论：项目**没有统一 Repository 层**；Repository 只在 LLM Usage
（Phase 3.10.14 起）这一条"运维事实"链路上被引入。

---

## LLM Usage Persistence Analysis（Phase 3.10.14~3.10.17）

### 真实链路（实测）

```text
LLM Client（execution）
    ↓ accounting_sink.record(observation)      ← 可插拔 Sink（默认 Noop）
DatabaseLLMAccountingSink（services/llm_usage_persistence_service.py）
    ↓
LLMUsagePersistenceService.persist()           ← 映射 + 规则（不改数值）
    ↓
LLMUsageRepository.create()                    ← SQLAlchemy（同步）
    ↓ with factory() as session, session.begin()
ai_ops.llm_usage_record
```

（async 路径额外经 `LLMUsagePersistenceRuntimeBridge` 用
`asyncio.to_thread` 跨线程边界；**不改**持久化语义。）

### A. LLMUsageRecord 与 ToolExecutionRecord 是否同一种 Operational Event？

**是同一类（"运维事实 / Operational Fact"），但语义不同源：**

| 维度 | LLMUsageRecord | ToolExecutionRecord |
| ---- | -------------- | ------------------- |
| 产生者 | LLM Client（Provider 响应） | ToolExecutionService（ToolResult） |
| 粒度 | 一次 LLM 请求 | 一次 Tool 执行 |
| 是否业务数据 | 否（AI 内部运维） | 否（AI 内部运维） |
| 敏感字段 | 禁存 prompt / response / API Key | 禁存 arguments / SQL / result.data |
| 幂等身份 | `request_id`（Provider 真实 ID；缺失 NULL） | `request_id` + `round` + `tool_call_id` |
| 落库 schema | `ai_ops` | 应同样落在 `ai_ops` |

→ **可复用的设计思想**（强烈建议沿用）：

1. **独立 schema `ai_ops`**：避免被 Text-to-SQL 的 `inspect(schema="public")`
   当作业务表（LLM 可能生成针对运维表的查询）；
2. **Repository 只做最小数据访问**：显式列、不走 `SELECT *`、
   返回内部 Row record 而不是 ORM 对象；
3. **失败隔离**：持久化失败 → warning，绝不影响主业务结果；
4. **NULL ≠ 0**：缺失值原样存 NULL，不填充 / 不重算；
5. **不在应用层做去重 / 幂等 / 缓存**：交给数据库约束；
6. 主键沿用 BIGINT 自增（项目既有规范）。

### B. Execution → Repository 还是 Execution → Service → Repository？

**实测：Execution → Sink（Port）→ Persistence Service → Repository。**

```text
Execution Layer（LLM Client）
    ↓ 只依赖 LLMAccountingSink 抽象（默认 NoopAccountingSink）
DatabaseLLMAccountingSink（适配边界；DB 依赖只出现在这一层与 db/）
    ↓
LLMUsagePersistenceService（字段映射 / "usage=None 不写" 等规则）
    ↓
LLMUsageRepository（SQL）
```

→ 对 Tool Observability 的启示：不能让 Execution 直连 Repository；
应复用**既有的 Observer 端口**（`ToolExecutionObserver`），
在端口之后再加 Persistence Service → Repository 两层。

### C. Repository 是否含 business logic / aggregation / validation / serialization？

| 项 | 实测 | 说明 |
| -- | ---- | ---- |
| business logic | **无** | 只做 INSERT / SELECT；"usage=None 不写"在 Service 层 |
| aggregation | **无**（明确排除） | 不提供 sum_tokens / monthly_usage / daily_usage |
| validation | **无业务校验** | 仅依赖 DB 约束（partial unique index / NOT NULL） |
| serialization | **无** | 返回 `LLMUsageRecordRow`（内部 record）；DTO 转换在上层 |

---

## Tool Observability 当前边界分析

### 1. 未来 Persistence Adapter 的输入应该是什么？

| 候选 | 结论 | 理由 |
| ---- | ---- | ---- |
| `ToolExecutionRecord` | **推荐** | 内部执行事实（权威字段 + 已过校验）；frozen；11 字段白名单已锁定 |
| `ToolExecutionSnapshot` | 否 | 是**对外 Read Model**（可能被未来 API 演进改动），不应成为存储契约 |
| `dict` | 否 | 无类型、无校验、易漂移 |

→ **Persistence Adapter 应接收 `ToolExecutionRecord`**（与
`LLMUsagePersistenceService` 接收 `LLMObservation` 的位置对称）；
读侧仍由 Snapshot / Metrics Read Model 承载。

### 2. Persistence 放在哪一层？

| 选项 | 优点 | 缺点 | 违反的约束 |
| ---- | ---- | ---- | ---------- |
| **A. ToolExecutionService → Repository** | 写入最直接 | 执行边界获得 DB 依赖；执行失败/DB 失败耦合 | ❌ C28（Execution 不得依赖 SQLAlchemy/Repository）；把 `Execution → Observability` 变成 `Execution → Database` |
| **B. ToolExecutionObserver → PersistenceAdapter** | 复用既有端口；Observer 已 failure-isolated；不改执行边界；与 LLM Usage 的 Sink 形态一致 | 需要新增 Adapter（本阶段不实现） | ✅ 无违反 |
| **C. Collector → Repository** | 改动面小 | Collector 从"内存收集器"变成"持久化写入者"；Collector 获得 DB 依赖；测试用 Collector 也被迫带 DB | ❌ C30（Collector 不得依赖 SQLAlchemy/Repository/Session）；破坏 Collector 的纯内存定位 |
| **D. QueryService → Repository** | 复用现有只读查询入口 | QueryService 是 **READ ONLY**（C23.9 / C31）；写能力进入查询层；GET 请求会写库 | ❌ C31（Query 不得执行写操作）；把只读 API 变成写 API |

### Decision

**推荐 Boundary = Option B（Observer → PersistenceAdapter → Repository）**

```text
ToolExecutionService（执行；无 DB）
    ↓
ToolExecutionRecord（frozen）
    ↓
ToolExecutionObserver（Protocol；既有端口，不改）
    ├── InMemoryToolExecutionCollector（现状：runtime store）
    └── ToolObservabilityPersistenceAdapter（未来：新增，本阶段不实现）
            ↓
        ToolExecutionPersistenceService（字段映射 / 规则）
            ↓
        ToolExecutionRecordRepository（SQL；ai_ops schema）
            ↓
        PostgreSQL
```

* Observer 端口**不变**（`on_execution(record)`），Adapter 只是另一个实现；
* 失败隔离天然继承（Step 15 已在执行边界把 observer 异常收敛为 warning）；
* 执行链**零改动**（C28 保持）；Collector **零改动**（C30 保持）；
* QueryService 保持 READ ONLY（C31）：持久化读侧未来由独立
  **Persistent Query Service** 承担，而不是把 Repository 塞进现有 QueryService。

---

## Consequences

```text
正：
  * Execution 永不依赖数据库（C28 永久成立）
  * Collector 保持纯内存 / 可测试（C30）
  * QueryService 保持只读（C31）
  * API 只经 QueryService（C32）
  * 与 LLM Usage 的"Sink → Service → Repository"形态一致，认知成本低
负：
  * 多一个 Adapter 组件（组合在 Composition Root；Collector 与 Adapter
    并列挂在 observer 位置，需要显式装配，不是自动生效）
  * 写入是 per-record 单条事务（与 LLM Usage 一致；不做批量 / outbox）
  * 持久化后需要独立的 retention / 清理策略（本阶段不设计）
```

---

## Security

```text
落库字段必须等于 ToolExecutionRecord 的 11 个字段：
    request_id / round / tool_name / started_at / finished_at /
    duration_ms / success / project_id / tool_call_id / error_code / error_type

禁存：API key / password / Authorization header / database_url /
      connection string / SQLAlchemy Session / DB connection /
      LLM prompt / LLM response / tool arguments / ToolResult.data /
      exception message / traceback

结论：当前 ToolExecutionRecord **已经足够安全**（11 字段白名单 + frozen +
      error_type 只允许 ToolError 类名、error 正文不写入）。
      → **不需要修改 Record**（本次 Audit 未发现需要改 Record 的问题）。

附加：表应放在 `ai_ops` schema（与 public 业务 schema 物理隔离，
      避免被 Text-to-SQL schema 发现当作业务表——复用 LLM Usage 的结论）。
```

---

## Failure Isolation

```text
原则（继承 Step 15）：Observability failure ≠ Tool execution failure

    DB write failed
        ↓
    PersistenceAdapter 捕获（Repository 异常 → warning）
        ↓
    ToolResult 原样返回 / 不 retry / 不 fallback / 不重跑

* 不引入 retry / sleep / backoff / queue / outbox / 异步持久化
  （与 LLM Usage §二十二 / §二十三 一致）
* 不因持久化失败改变 HTTP 观测 API 的内存读路径
* 若未来需要 async 写入，可复用 LLMUsagePersistenceRuntimeBridge 的
  `asyncio.to_thread` 形态（**本阶段不实现**）
```

---

## Multi-process

```text
当前行为（实测/设计事实）：
    Collector = process-local（模块级单例，由 Composition Root 创建）
    workers > 1 → GET /api/observability/tools **只能看到本 worker 的记录**
                 （每个 worker 独立内存、独立 FIFO 窗口）

Persistence 后的目标行为：
    GET /api/observability/tools 应能看到**全部 worker**的记录
    （共享 PostgreSQL 存储）

过渡期策略：
    * 内存 Collector 继续作为 **runtime / 短期视图**（本 worker 近期执行）
    * 持久存储作为 **长期视图**（跨进程 / 跨重启）
    * 两者在 Query 层的合并方式见 §Query Boundary Design（Composite 与否）
      —— **本阶段不实现**
```

---

## Retention

```text
现状（Step 20）：
    DEFAULT_MAX_RECORDS = 1000（deque(maxlen=N)，FIFO，进程内）

设计结论（本阶段只做结论，不实现 policy）：
    Runtime retention（内存）        与 Persistent retention（数据库）
    **必须分离**：

        内存  1000 条  = 防内存无界增长的**安全边界**（不是数据策略）
        数据库 长期     = 真正的历史 / 审计窗口（需要独立的清理策略）

    * 加入持久化后，**不应**把内存 max_records 当作"数据保留策略"；
      内存窗口变小（甚至关闭）都不应影响持久数据；
    * 数据库侧 retention / 归档 / 清理属于未来独立决策（含 GDPR 类考量），
      本阶段不设计、不实现。
```

---

## Query Boundary Design

```text
是否需要 CompositeStore（Memory + PostgreSQL）？

分析：
    * 现状 QueryService 只依赖具体 InMemoryToolExecutionCollector
      （Step 21 §十三允许；Audit Step 23 记为"未来持久化时再抽象"）
    * 合并内存 + 持久数据会引入：去重（同一 record 两边都有）、
      排序（时间序 vs 插入序）、窗口裁剪 —— 复杂度高、语义易错

建议（Deferred）：
    短期：Query 层保持单一数据源（现状=内存；持久化后=持久存储），
          **不**在同一端点混合两个来源；
    若确需"近期实时 + 历史"合一，再引入显式的
    ``ObservabilityStore`` 抽象（Memory / PostgreSQL 两个实现），
    并明确去重键（request_id + round + tool_call_id）后实现。
    —— 本阶段**不实现** CompositeStore。
```

---

## Future Migration Path

```text
1. （未来 Step）新增 ORM Model：ai_ops.tool_execution_record
   （字段 = Record 11 项 + id/created_at；索引最小：started_at / request_id）
2. （未来 Step）新增 Repository：ToolExecutionRecordRepository
   （session_factory 注入；事务内部化；返回内部 Row record）
3. （未来 Step）新增 Persistence Service + Observer Adapter
   （ToolExecutionObserver 实现；失败 → warning）
4. （未来 Step）Composition Root 装配：observer = 内存 Collector + Adapter
   （或按配置选择；执行链与 Collector 代码不变）
5. （未来 Step）持久读侧：独立 Query Service / API 端点（新增端点，
   不修改现有 GET /api/observability/tools 契约）
6. init_db()：显式 `CREATE SCHEMA IF NOT EXISTS ai_ops`（已存在）+ create_all
```

---

## 本阶段不做（明确）

```text
❌ PersistenceAdapter / Repository / ORM Model / migration / 表
❌ Collector → SQLAlchemy / Session
❌ ToolExecutionService / Record / Observer / QueryService / Snapshot /
   Metrics / HTTP API 的任何修改
❌ Redis / Kafka / Celery / 后台线程 / 消息队列 / 异步持久化
❌ Dashboard / Prometheus / OpenTelemetry
❌ retention policy 实现 / CompositeStore 实现
❌ 为"验证设计"连接数据库
```
