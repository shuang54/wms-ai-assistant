# Phase 3.11.27 — Tool Observability Persistence Model & Repository

> Step 27 只实现 **Persistence 底座**（ORM Model + Repository + 测试）。
> **不接入** Observer / Adapter / Service / QueryService / HTTP API。
>
> `Runtime Integration = NOT IMPLEMENTED`

---

## 1. Model

```text
backend/app/db/models/tool_execution_record.py

class ToolExecutionRecordModel(Base)
    __tablename__ = "tool_execution_record"
    __table_args__ = (... , {"schema": "ai_ops"})

命名理由：与 services 层的 frozen DTO ``ToolExecutionRecord`` 同名会冲突，
         故 ORM 类加 ``Model`` 后缀（内部 Row DTO 为
         ``ToolExecutionRecordRow``），三者职责分离：
             ToolExecutionRecord        （领域事实 / frozen DTO）
             ToolExecutionRecordModel   （ORM / 数据库行）
             ToolExecutionRecordRow     （Repository 返回的内部只读 record）
```

## 2. Database Table

```text
ai_ops.tool_execution_record        （**不是** public）

理由：Tool Execution 是 AI 内部运维数据，不是 WMS 业务数据；
      避免 Text-to-SQL 的 inspect(schema="public") 把它当业务表
      （沿用 LLMUsageRecord 的既有结论）。

初始化：沿用项目现有模式（无 Alembic）
      init_db() → CREATE SCHEMA IF NOT EXISTS ai_ops
                → Base.metadata.create_all()（models/__init__ 已导出本 Model）
```

实测（真实 PostgreSQL）：

```text
ai_ops tables:  ['llm_usage_record', 'tool_execution_record']
in public:      False
columns:        id, request_id, round, tool_name, started_at, finished_at,
                duration_ms, success, project_id, tool_call_id,
                error_code, error_type            （12 = 主键 + 11）
started_at:     timestamp with time zone
duration_ms:    double precision
```

## 3. Fields（严格 = ToolExecutionRecord 11 字段）

| Record 字段 | 类型 | 列 | 可空 |
| ----------- | ---- | -- | ---- |
| request_id | str | String(128) | NOT NULL |
| round | int | Integer | NOT NULL |
| tool_name | str | String(128) | NOT NULL |
| started_at | datetime（tz-aware） | DateTime(timezone=True) | NOT NULL |
| finished_at | datetime（tz-aware） | DateTime(timezone=True) | NOT NULL |
| duration_ms | float | Float | NOT NULL |
| success | bool | Boolean | NOT NULL |
| project_id | str \| None | String(128) | NULL |
| tool_call_id | str \| None | String(128) | NULL |
| error_code | str \| None | String(64) | NULL |
| error_type | str \| None | String(64) | NULL |

```text
主键：id BIGINT 自增（与 LLMUsageRecord.id 同风格）
      request_id **不是**主键 / **不是**唯一键：一次 request 可含多次执行
禁列：tool_arguments / tool_result / sql / prompt / llm_response /
      exception_message / traceback / api_key / database_url（均未出现）
```

## 4. Indexes（最小；逐条理由）

```text
ix_tool_execution_record_request_id
    理由：Repository 已实现 get_by_request_id()（精确查询）
ix_tool_execution_record_started_at
    理由：时间序 / 未来时间范围与"最近执行"排序

**未建**（本阶段不预建）：project_id / tool_name
    理由：对应过滤查询尚未实现（Step 27 §八：不为"以后可能用到"建索引），
          等真正实现时再评估（可考虑复合索引）。
```

## 5. Repository API

```text
backend/app/db/tool_execution_repository.py

class ToolExecutionRepository
    __init__(session_factory: sessionmaker[Session] | None = None)
    create(record: ToolExecutionRecord) -> ToolExecutionRecordRow
    get_by_request_id(request_id: str) -> list[ToolExecutionRecordRow]

class ToolExecutionRepositoryError(Exception)     # 写入 / 查询失败（已回滚）
@dataclass(frozen=True) ToolExecutionRecordRow    # 内部只读 record（非 ORM）
TOOL_EXECUTION_READ_COLUMNS                       # 显式列（禁用 SELECT *）

未提供（属后续 Query / Retention）：
    list_by_project / list_by_tool / list_by_time / metrics / aggregation /
    pagination / search / delete / cleanup
```

## 6. Transaction Boundary

```text
写入：session_factory → Session → session.begin() → INSERT → COMMIT
      （与 LLMUsageRepository.create() 完全一致）
失败：SQLAlchemyError → ROLLBACK → ToolExecutionRepositoryError
      （调用方**不**负责 commit / rollback；begin() 上下文管理器负责）
读取：with factory() as session:（不开写事务）

实测：create() 进入 begin() 1 次；读不进入 begin()；不手动 commit/rollback。
```

## 7. Security

```text
* ORM 列 == 白名单(11) + 主键（断言，防未来漂移）
* 无敏感列（arguments / result / sql / prompt / response / api_key /
  password / authorization / token / database_url / dsn / traceback ...）
* 表在 ai_ops，不在 public（DB 实测：in_public = False）
* Repository 不做 JSON serialization（无 json import / 无 dumps）
* Repository 不做 aggregation（public API 仅 2 个方法；无 count/sum/group_by）
* Repository 不返回 ORM 对象（返回 frozen ToolExecutionRecordRow）
```

## 8. Unit Tests

```text
tests/test_tool_execution_repository.py                      29 passed（0 DB）
    1 Record→Row / 2 11 字段 / 3 None→NULL / 4 timezone / 5 success=True /
    6 success=False / 7 error_code / 8 error_type / 9 project_id=None /
    10 tool_call_id=None / 11 duration_ms /
    12 异常映射（写 / 读 / DB 未配置）/ 13 不返回 ORM /
    14 无 JSON 序列化 / 15 无聚合
    + 事务边界（create 用 begin；读不开事务；不手动 commit/rollback）
    + get_by_request_id（稳定排序 id ASC / 空结果 / 显式列）
    + 安全（列白名单 / 无敏感列 / ai_ops / request_id 非唯一 / 最小索引 /
            models.get_all_models() 已注册）
```

## 9. DB Tests

```text
tests/test_tool_execution_repository_db.py        10 passed（RUN_DB_TESTS=1）
                                                  10 skipped（默认）
    Case 1 insert one record          → 行存在（id > 0）
    Case 2 同一 request_id 多条        → 2 行（request_id ≠ 主键）
    Case 3 success record             → success = True
    Case 4 failure record             → success = False + error_type
    Case 5 nullable fields            → 4 个可空列均为 NULL
    Case 6 timezone                   → tz-aware，utcoffset = 0，值 == 原值
    Case 7 get_by_request_id          → 2 条按 round 1,2；其它 request 不受影响
    Case 8 rollback                   → 写入失败 → 表内 0 行（无半条记录）
    + 表不在 public / 在 ai_ops
    + information_schema 列集合无敏感列

数据：synthetic only（test-request-001 / get_inventory / test-project）
      未读取真实库存 / 工单 / WMS 数据；未调用 get_inventory / get_work_order
```

## 10. DB Residue

```text
0
    每个用例 teardown：TRUNCATE ai_ops.tool_execution_record RESTART IDENTITY
    （只清本表，不清整个 ai_ops；未执行任何针对 public / 生产数据的 DELETE）
    独立核验：SELECT COUNT(*) FROM ai_ops.tool_execution_record = 0
```

## 11. Runtime Integration

```text
NOT IMPLEMENTED（Step 27 不接入）

    ToolExecutionService 仍然 → ToolRegistry（不是 → Repository）
    ToolExecutionObserver 仍然 → InMemoryCollector（不是 → Repository）
    HTTP API（GET /api/observability/tools · /metrics）契约未变
    PersistenceAdapter / PersistenceService 未创建（Deferred）
    执行链 / 观测链未 import tool_execution_repository（AST 全 backend 扫描）
```

---

## 12. 修改边界

```text
新增：backend/app/db/models/tool_execution_record.py（ORM Model）
      backend/app/db/tool_execution_repository.py（Repository）
      tests/test_tool_execution_repository.py（29）
      tests/test_tool_execution_repository_db.py（10，DB-gated）
      docs/evaluation/Phase 3.11.27 — …md
修改：backend/app/db/models/__init__.py（导出 Model，供 create_all 发现）
      tests/test_tool_observability_persistence_architecture.py（+C33 8 项；
          Step 26 的"无 Model"断言同步为"仅 ai_ops 一个 Model"）
      docs/decisions/ADR-3.11.26-…md（+ Implementation Status）
      docs/architecture.md（§8.45）
未修改：ToolExecutionService / Observer / Collector / QueryService /
        Snapshot / Metrics / Serialization / HTTP API / Router /
        Tool Handler / Registry / LLM Usage Repository / LLM Usage Model /
        Text-to-SQL / RAG / Orchestrator / init_db.py
```
