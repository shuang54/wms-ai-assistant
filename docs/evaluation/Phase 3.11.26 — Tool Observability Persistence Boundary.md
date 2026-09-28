# Phase 3.11.26 — Tool Observability Persistence Boundary（Survey & Design）

> Step 26 = **Survey + 设计**。
> **未实现**任何持久化：0 新表 / 0 migration / 0 Repository / 0 ORM Model /
> 0 DB 连接；生产代码 0 修改。

---

## 1. Survey（真实代码）

### 1.1 数据库访问入口

```text
backend/app/db/session.py
    get_engine()            懒加载 Engine；DATABASE_URL 为空 → None（应用仍可启动）
    get_session_factory()   sessionmaker[Session]（autoflush=False, expire_on_commit=False）
    get_db()                FastAPI Depends 生成器（调用方负责 commit / rollback）
    ping_database()         SELECT 1
backend/app/db/base.py              DeclarativeBase（SQLAlchemy 2.x）
backend/app/db/init_db.py           CREATE EXTENSION vector + CREATE SCHEMA ai_ops
                                    + Base.metadata.create_all()（**无 Alembic**）
backend/app/db/models/              3 个 ORM Model
backend/app/db/llm_usage_repository.py   项目**唯一** Repository
```

### 1.2 ORM Model 位置

```text
backend/app/db/models/
    knowledge_document.py     public schema（业务数据）
    knowledge_chunk.py        public schema（业务数据；含 vector）
    llm_usage_record.py       ai_ops schema（AI 内部运维；与业务隔离）
```

### 1.3 Repository

```text
文件：backend/app/db/llm_usage_repository.py
类：  LLMUsageRepository / LLMUsageRepositoryError / LLMUsageRecordRow
职责：最小数据访问（create 幂等写入 / get_by_request_id / list_records 只读分页）
```

### 1.4 Repository 是否暴露 Session？

```text
否 —— Repository(session_factory=None)，接收 **session_factory**
     （不是 Engine、不是已开 Session）；
     Session 在 Repository 内部创建：with factory() as session, session.begin():
     读路径：with factory() as session:（无 begin()）
```

### 1.5 Persistence 模式有无（实测）

```text
UnitOfWork    无（0 处）
Transaction   有（粒度 = 单次 Repository 操作：session.begin()）
Repository    有（仅 llm_usage_repository 一个）
Service       有（Persistence / Query / Analytics / Aggregation）
Migration     无 Alembic（init_db() + create_all + 显式幂等 DDL 扩展点）
```

### 1.6 其它 DB 使用点（非 Repository）

```text
services/vector_search_service.py        → session_factory
services/knowledge_ingestion_service.py  → session_factory
services/sql_executor_service.py         → Engine（只读 SQL）
services/schema_explorer_service.py      → Engine（元数据）
projects/engine_provider.py              → create_engine（per-project）
tools/get_inventory.py                   → Engine（业务只读 SQL）
tools/get_work_order.py                  → Engine（业务只读 SQL）
api/usage.py                             → Query Service（+ RepositoryError 映射 502）
```

---

## 2. Current Architecture

```text
Tool Execution
    ↓
ToolExecutionRecord（frozen；11 字段）
    ↓
ToolExecutionObserver（Protocol；failure-isolated）
    ↓
InMemoryToolExecutionCollector（process-local；max_records=1000；FIFO）
    ↓
ToolObservabilityQueryService（READ ONLY）
    ↓
Snapshot / Metrics → Serialization → HTTP Read API → JSON
```

静态基线核验（C28~C32 实测，本次全部 PASS）：

```text
tool_execution_service.py            DB 相关 import = 0
in_memory_tool_execution_collector.py DB 相关 import = 0
tool_observability_query_service.py   DB 相关 import = 0
api/tool_observability.py             DB 相关 import = 0
tools/*                               未 import 任何 Observability 模块
```

---

## 3. Persistence Options

| 选项 | 链路 | 优点 | 缺点 | 违反约束 |
| ---- | ---- | ---- | ---- | -------- |
| A | ToolExecutionService → Repository | 最直接 | 执行边界获得 DB 依赖；DB 失败与执行失败耦合 | ❌ C28；`Execution → Database` |
| B | ToolExecutionObserver → PersistenceAdapter | 复用既有端口；失败隔离天然继承；执行链零改动 | 需新增 Adapter | ✅ 无 |
| C | Collector → Repository | 改动面最小 | Collector 变成写入者；测试也被迫带 DB | ❌ C30 |
| D | QueryService → Repository | 复用只读入口 | 写能力进入查询层；GET 会写库 | ❌ C31 |

---

## 4. Recommended Boundary

```text
ToolExecutionService（无 DB）
    ↓
ToolExecutionRecord
    ↓
ToolExecutionObserver（端口不变）
    ├── InMemoryToolExecutionCollector        （现状：runtime store）
    └── PersistenceAdapter（未来；本阶段不实现）
            ↓
        PersistenceService（字段映射 / 规则）
            ↓
        Repository（ai_ops schema；session_factory 注入）
            ↓
        PostgreSQL

输入类型：ToolExecutionRecord（不是 Snapshot / dict）
读侧：持久读由独立 Query Service 承载；现有 QueryService 保持 READ ONLY
```

设计思想复用 LLM Usage（Phase 3.10.14~17）：

```text
* 独立 schema ai_ops（避免被 Text-to-SQL 当业务表）
* Repository 最小职责（显式列 / 不返回 ORM 对象 / 无聚合 / 无业务校验）
* 失败隔离（warning；不影响主业务；无 retry / queue）
* NULL ≠ 0（缺失原样存）；不在应用层做去重 / 幂等 / 缓存
* 主键沿用 BIGINT 自增
```

---

## 5. Security

```text
落库字段 = ToolExecutionRecord 的 11 个字段（不多不少）
禁存：API key / password / Authorization / database_url / connection /
      Session / prompt / LLM response / tool arguments / ToolResult.data /
      exception message / traceback

Audit 结论：当前 ToolExecutionRecord **已足够安全**
      （frozen + 字段白名单 + error_type 仅类名 + 无 error 正文）
      → **未发现需要修改 Record 的问题；未修改 Record。**
```

---

## 6. Multi-process

```text
当前行为：Collector = process-local 单例（Composition Root 创建）
          workers > 1 → GET /api/observability/tools 只看到**本 worker** 数据
目标行为：持久化后应看到全部 worker 的记录（共享 PostgreSQL）
过渡：内存 = 短期 runtime 视图；数据库 = 长期视图（本阶段不合并）
```

---

## 7. Failure Isolation

```text
原则：Observability failure ≠ Tool execution failure（继承 Step 15）
    DB write failed → warning → ToolResult 原样返回
    无 retry / fallback / 重跑 / 队列 / 异步持久化（本阶段不实现）
```

---

## 8. Retention

```text
现状：DEFAULT_MAX_RECORDS = 1000（deque FIFO；进程内）
结论：Runtime retention 与 Persistent retention **必须分离**
      内存 1000 = 防无界增长的**安全边界**（不是数据保留策略）
      数据库长期窗口 = 未来独立决策（清理 / 归档 policy 本阶段不设计）
```

---

## 9. Tests

```text
tests/test_tool_observability_persistence_architecture.py        16 passed
    C28  ToolExecutionService：无 SQLAlchemy / Repository / DB Session
    C29  Tool Handler：不依赖 Observability Persistence（可依赖业务 Engine）
    C30  Collector：无 SQLAlchemy / Repository / DB Session
    C31  QueryService：无 DB 写能力（无 commit/flush/execute/insert；无写方法）
    C32  Observability API：不 import Repository / SQLAlchemy
    +    Survey 锁定：LLM Usage "Sink → Service → Repository" 分层存在
    +    Survey 锁定：无 Tool Execution ORM Model / 无 Persistence Adapter /
                     无 migration 目录
    +    方向：Execution → Observability（不 → Database）
```

---

## 10. Next Step

```text
Future step may implement the selected Persistence Adapter.
（本阶段到此停止；未指定、也不会自动进入下一阶段。）
```
