# Phase 3.11.28 — Tool Observability Persistence Integration

> Step 28 把 Step 27 的持久化底座接到 `ToolExecutionObserver` 端口：
> **Observer → Persistence 已连接**，且 Persistence 失败绝不影响 Tool 执行。
>
> `QueryService → PostgreSQL = NOT IMPLEMENTED` ·
> `HTTP History API = NOT IMPLEMENTED` · `Retention = NOT IMPLEMENTED` ·
> `Dashboard = NOT IMPLEMENTED`

---

## 1. Architecture

```text
ToolExecutionRecord
        ↓
CompositeObserver（fan-out；子 observer 互相隔离）
        ├── InMemoryCollector      （runtime store；API 的内存视图）
        └── PersistenceAdapter     （infrastructure boundary）
                 ↓
          PersistenceService       （application persistence operation）
                 ↓
             Repository            （database access；Step 27）
                 ↓
          ai_ops.tool_execution_record
```

```text
backend/app/services/tool_execution_persistence_service.py
    ToolExecutionPersistenceService.persist(record) -> ToolExecutionRecordRow
        * 输入就是 ToolExecutionRecord（不转 dict / JSON / Snapshot）
        * 不做校验 / 聚合 / 序列化 / metrics
        * 不吞异常（失败隔离由 Adapter 负责，与 LLM Usage 分工一致）

backend/app/services/tool_execution_persistence_adapter.py
    ToolExecutionPersistenceAdapter.on_execution(record) -> None
        * 实现 ToolExecutionObserver 协议（不新增第二套接口）
        * 不 import SQLAlchemy / ORM / Repository / Session；不建 Session
        * 失败 → warning（白名单字段）→ 静默丢弃；无 retry / queue / async
    CompositeToolExecutionObserver(*observers).on_execution(record)
        * 最小 fan-out；单个子 observer 失败**不阻断**后续

backend/app/api/orchestrator_chat.py（Composition Root）
    _TOOL_EXECUTION_COLLECTOR                     （Step 19；仍唯一）
    _TOOL_EXECUTION_PERSISTENCE_SERVICE           （Step 28 新增）
    _TOOL_EXECUTION_PERSISTENCE_ADAPTER           （Step 28 新增）
    _TOOL_EXECUTION_OBSERVER = Composite(collector, adapter)
```

---

## 2. Failure Isolation（本阶段最重要契约）

```text
Persistence success
    → DB 行写入；Tool 执行结果不变

Persistence failure（DB 不可用 / insert 失败 / DATABASE_URL 缺失）
    → Adapter 捕获 → warning 日志 → 静默丢弃
    → ToolResult 不变；不 retry / 不 fallback / 不重跑 Tool

Memory success + Persistence failure
    → Memory 记录存在；Tool 执行不受影响

Memory failure + Persistence success
    → Persistence 仍收到记录（Composite 不在失败处停止）

Memory failure + Persistence failure
    → 两者都隔离；Tool 执行不受影响（执行边界 Step 15 亦有一层隔离）
```

测试（真实断言，非文档描述）：

```text
Case A  两者成功                → 两个 observer 都被调用
Case B  Memory 成功 + DB 失败   → collector.records() 有 1 条；ToolResult 不变
Case C  Memory 失败 + DB 成功   → service.calls == [record]
Case D  两者失败                → on_execution 不抛异常
E2E     DB 失败                 → route=tool；data.success=True；
                                  Memory 1 条；DB 0 行
```

---

## 3. DB Integration

```text
tests/test_tool_execution_persistence_integration.py      10 passed（RUN_DB_TESTS=1）

Case 1  success record          → 1 行；request_id / success / duration_ms 正确
Case 2  failure record          → success=False + error_code="invalid_argument"
Case 3  nullable（4 个 None）   → 全部 NULL
Case 4  同 request round=1/2    → 2 行（id 顺序 1,2）
Case 5  Repository 抛异常        → Adapter 不传播；DB 0 行
Case 5b 同上                     → repository.create 恰好调用 1 次（无 retry）

rows written：仅 synthetic（test-persist-request-001 / test-persist-request-multi）
rollback：Repository 内部事务（session.begin()）；失败不留半条记录
DB residue：0（teardown TRUNCATE 本表；未清 ai_ops.llm_usage_record / public.*）
```

---

## 4. E2E（真实 AIOrchestrator → Tool → Observer → Memory + PostgreSQL）

```text
Question：查询物料 MAT-001 当前库存
Route：TOOL（FakeRouter 命中；真实 AIOrchestratorService.execute()）
Tool：get_inventory（synthetic handler → {"qty": 250.0}；不读真实 WMS 数据）
Tool Result：success=True / data={"qty": 250.0} / handler 恰好调用 1 次
Memory Record：1 条（tool_name=get_inventory；tool_call_id=None；round=1）
Persistent Record：1 行；request_id **等于** Memory 记录的 request_id
                   （同一次 execute；round=1；success=True）

RAG（采购入库怎么操作？）：route=rag；handler 0 次；Memory 0；DB **0**
TEXT_TO_SQL（统计当前知识库文档数量）：route=text_to_sql；handler 0 次；
        Memory 0；DB **0**（未把 SQL Executor 当 Tool）
```

---

## 5. Security

```text
落库字段 = ToolExecutionRecord 的 11 个字段（Step 27 列白名单已锁定）
无 tool arguments / ToolResult.data / SQL / prompt / LLM response /
  API key / password / DATABASE_URL / traceback

日志（Persistence 失败）：只允许
    tool_name / request_id / round / project_id / error_type（异常**类名**）
    不使用 logger.exception（无 traceback）；
    不输出 arguments / result / SQL / 连接串 / 凭据
测试：日志文本断言（禁止 SQL / postgresql:// / password / api_key /
      Authorization / Bearer / Traceback）；exc_info 为 None
```

---

## 6. API

```text
HTTP API unchanged
     GET /api/observability/tools           （契约 / 字段 / 语义未变）
     GET /api/observability/tools/metrics

API remains memory-only
     QueryService → InMemoryCollector（未接 DB）

Database is persistent history only
     本阶段无任何 HTTP 读取数据库的路径（History API = NOT IMPLEMENTED）
```

---

## 7. 测试

```text
tests/test_tool_execution_persistence_service.py            18 passed
tests/test_tool_execution_persistence_adapter.py            20 passed
tests/test_tool_execution_persistence_integration.py        10 passed（DB-gated）
tests/test_tool_observability_persistence_architecture.py   35 passed（含 C34 = 11）
    既有契约同步（Step 28 有意改变"观测出口 == Collector"这一现状）：
        Step 19 composition（3 处）→ 断言 fan-out 的第一个子 observer 是
                                     同一个 Collector
        C21.4（Step 21）→ 同上
        C33.6（Step 27）→ Repository 唯一消费方 = PersistenceService
    （均为"当前状态"契约；历史 Evaluation 文档未改写）
全量 no DB                                     3569 passed / 357 skipped（0 failed）
  （Step 27 基线 3520 / 347 → +49 = 18 + 20 + 11 C34；skipped +10 = 新增 DB 文件）
全量 DB-gated：定向套件全绿（见下）；整套仍受既有 knowledge 数据状态耦合影响
python -m compileall -q backend tests          OK（0 errors / 0 warnings）
LSP（新增 / 修改文件）                          0 error / 0 warning
```

---

## 8. 修改边界

```text
新增：backend/app/services/tool_execution_persistence_service.py
      backend/app/services/tool_execution_persistence_adapter.py
      tests/test_tool_execution_persistence_service.py
      tests/test_tool_execution_persistence_adapter.py
      tests/test_tool_execution_persistence_integration.py（DB-gated）
      docs/evaluation/Phase 3.11.28 — …md
修改：backend/app/api/orchestrator_chat.py（fan-out 装配 + 文档说明）
      tests/test_tool_observability_composition.py（3 处断言 → fan-out）
      tests/test_tool_architecture_contract.py（C21.4 同步）
      tests/test_tool_observability_persistence_architecture.py（+C34；C33.6 同步）
      docs/decisions/ADR-3.11.26-…md（+ Implementation Status）
      docs/architecture.md（§8.46）
未修改：ToolExecutionRecord / ToolExecutionService / ToolExecutionObserver /
        InMemoryCollector / QueryService / Snapshot / Metrics /
        Serialization / HTTP API / Router / Tool Handler / ToolRegistry /
        Repository / ORM Model / LLM Usage 链路
```

---

## 9. 限制

```text
* QueryService → PostgreSQL = NOT IMPLEMENTED（API 仍只读内存）
* HTTP History API = NOT IMPLEMENTED（无分页 / 过滤 / 排序 / 时间范围）
* Retention = NOT IMPLEMENTED（数据库长期保存；内存 max_records=1000 独立）
* Dashboard / Prometheus / OpenTelemetry = NOT IMPLEMENTED
* 同步单行写入（无 batch / queue / worker / to_thread / outbox）
* 多进程：每个 worker 各自写库（写入侧共享数据库），
  读侧（API）仍是 process-local 内存视图
* 无 DB 写失败重试（刻意）：失败仅 warning
* DB-gated 全套件受既有 knowledge 数据状态耦合影响（Pre-existing）
* lint unavailable（环境未安装 ruff / flake8）
```
