# Phase 3.8.1：Project Context 数据源切换 — ADR

> **实际实施结果与技术决策记录**。
>
> 解决架构限制：project_id 此前只进入 ProjectContext 的 label，
> Schema / Text-to-SQL / SQL Executor / Tool 实际使用的数据库并未切换。
> 本阶段实现 `project_id → ProjectRegistry → DataSource → DatabaseEngineProvider
> → Engine` 的受控解析链，并让同一 Engine 贯穿项目内所有数据库消费点。

---

## 1. 当前问题（Phase 3.8.1 之前）

`api/orchestrator_chat.py::_build_orchestrator_for_project_id` 中的
`_ProjectedProvider` 只把 project_id 写进 ProjectContext 的 label，然后：

- Schema 仍 inspect 全局 engine 的 **public** schema；
- SQL Executor 仍用全局 engine；
- get_inventory Tool 仍查 `public.inventory`。

即 project_id 对**数据**完全不生效。另外存在两个真实接线缺陷：

1. `AIOrchestratorService._run_text_to_sql` 在事件循环内同步调用
   `provider.resolve()`（内部 `asyncio.run`）→
   `DefaultProjectContextProvider` 在 async 上下文必然 RuntimeError
   （provider docstring 声称"由 to_thread 包裹"，代码未包；既有测试
   均注入 Fake provider，未覆盖此路径）；
2. `SQLExecutorService` 不设置 `search_path`，非 public schema 的
   LLM 生成 SQL（裸表名）无法解析。

## 2. 架构（接入后）

```text
project_id（HTTP，可选）
    ↓
ProjectRegistry.get(project_id)                （服务器端注册表）
    ↓ ProjectRegistration{ context, schema_name }
DataSource(name, type)                         （非敏感身份）
    ↓
DatabaseEngineProvider.get_engine(...)         （受控连接解析）
    ↓ Engine
同一 Engine 贯穿：
    ├── SchemaExplorerService      → DatabaseSchema（该项目 schema）
    ├── DefaultProjectContextProvider（Schema → Selector → Composer）
    ├── SQLExecutorService         → READ ONLY 执行 + SET LOCAL search_path
    └── get_inventory Handler      → 该项目 schema 的库存表
    ↓
AIOrchestratorService（Router / RAG / T2S Generator 复用 base）
```

- `project_id` 未提供 → `_default_orchestrator`（不查注册表，旧行为）；
- `project_id` = 配置默认值（vietnam-wms）→ 默认注册表命中，依赖与全局
  默认完全等价（primary 数据源 + public schema）；
- 未注册 project_id → `ProjectNotFoundError` → HTTP 404。

## 3. ProjectRegistry（新增 `backend/app/projects/registry.py`）

- `ProjectRegistration`（frozen）：`context: ProjectContext` +
  `schema_name: str`（业务 schema；进 SQL 的标识符，严格白名单校验
  `^[A-Za-z_][A-Za-z0-9_]*$`）；**不含** password / url / token（有测试
  锁定 dataclass 字段白名单，防未来意外引入敏感字段）。
- `ProjectRegistry` Protocol：`get(project_id) -> ProjectRegistration`
  （最小接口，未来可换数据库 / YAML / 配置中心实现）。
- `InMemoryProjectRegistry`：代码级注册（任务书 §十八 MVP 约定）；
  重复注册 / 未注册 / 空 / 非法格式 project_id 均为 clear error；
  `unregister` 供测试清理。
- `get_default_project_registry()`：进程级惰性单例，默认只含配置默认项目
  （`settings.project.project_id` → primary + public），保证兼容性。

## 4. DataSource → Engine（新增 `backend/app/projects/engine_provider.py`）

- `DatabaseEngineProvider` Protocol：`get_engine(data_source) -> Engine`。
- `DefaultDatabaseEngineProvider`：
  - **"primary" 键委托 `db.session.get_engine()`**（共享现有全局连接池，
    不重复建池；DATABASE_URL 为空 → 清晰错误）；
  - 其他键：`register_connection(key, DatabaseSettings)` 服务器端注册后
    独立构建 Engine，进程内按键缓存（同一键返回同一实例）；
  - **不接受任何 URL 字符串参数**：凭据只经 `DatabaseSettings` 从服务器端
    进入；`DataSource` 只有 name/type 两个字段（测试锁定）；
  - 保留键 "primary" 不可覆盖。
- 生产第二数据库：本阶段只提供 `register_connection` 接口，未接入
  （任务书 §十八）。

## 5. Orchestrator 工厂（新增 `backend/app/services/project_orchestrator_factory.py`）

`build_orchestrator_for_project(project_id, *, base, registry, engine_provider)`：

- registry 解析注册条目 → provider 解析 Engine（不可用 →
  `AIOrchestratorUnavailableError` → 503）；
- 构造 per-project：`SchemaExplorerService(engine)` +
  `DefaultProjectContextProvider(project_context, explorer, schema_name)` +
  `SQLExecutorService(engine)` + Tool Registry
  （`GetInventoryHandler(engine, inv_settings(schema=项目 schema), project_id)`）；
- **复用 base**：RagService（知识库全局，任务书 §十三）、TextToSQLService
  （纯 LLM + Validator，无 engine 依赖）、TableSelector、ContextComposer；
- Router 新实例（capability 适配 per-project Tool Registry，路由规则零改动）；
- 位于 Service 层的原因：API 层 AST 级禁令
  （`test_api_module_does_not_import_forbidden_services`）禁止
  `orchestrator_chat.py` import `sql_executor_service` 等底层服务，
  函数内延迟 import 同样命中；API 层只 import 本工厂 + `projects.registry`。

`api/orchestrator_chat.py` 的 `_build_orchestrator_for_project_id`
变为工厂的薄封装（`_ProjectedProvider` 删除）；`ProjectNotFoundError`
在 chat() 端点映射 **HTTP 404**（API 协议最小修改：responses 新增 404 描述）。

## 6. 安全边界

- 用户只能提交 `project_id`；**不能**通过 HTTP 传 database_url / host /
  port / password（无该字段，注入面为零）；
- project_id 只能选择**服务器端已注册**的数据源（未注册 → 404）；
- ProjectRegistration / ProjectContext / DataSource 全链路无敏感字段
  （单元测试锁定字段白名单）；
- SQL Executor 的全部安全机制**原样保留**：READ ONLY 事务 /
  statement_timeout / max_rows / 结果大小保护 / re-validation；
- 新增 `SET LOCAL search_path`（事务级，见 §8）前先在 `_validate_inputs`
  用标识符白名单校验 `schema.schema_name`（防伪造 DatabaseSchema 注入）；
- 跨项目访问：project-a 的 SQL 引用 `project_b.inventory` →
  Validator UNKNOWN_TABLE 拒绝（见 §9 测试）。

## 7. 修复的两个接线缺陷（最小修改，均有回归测试）

1. **`_run_text_to_sql` 的 asyncio bug**：`resolve()` 改为
   `await asyncio.to_thread(...)`（与 provider docstring 声明一致；
   不改任何业务逻辑）；
2. **Executor 的 search_path**：`schema.schema_name` 非 None 时在
   BEGIN READ ONLY 后执行 `SET LOCAL search_path TO "<schema>"`
   （标识符位置不接受绑定参数，故先白名单校验再加引号内插），使
   LLM 生成的裸表名与 Validator 的 schema 归一化处于同一命名空间。
   schema=None 时行为与旧版完全一致。

## 8. 数据源切换验证（tests/test_project_datasource_isolation.py，RUN_DB_TESTS=1）

单 PostgreSQL 实例用两个 schema 模拟两个数据源（任务书 §六）：

```text
schema project_a: inventory(material_code='10001', qty=100)
schema project_b: inventory(material_code='10001', qty=999)
```

结果（16/16 passed）：

| 测试 | 结果 |
|---|---|
| Registry 解析 project-a/b → 不同 schema | ✅ |
| Schema Explorer 分别只见各自 schema 的表 | ✅ |
| **Tool E2E**：project-a → qty 100 / project-b → qty 999（route=tool，无 LLM） | ✅ |
| **Text-to-SQL E2E**：Fake Generator（记录 context）+ 真实 Validator/Executor → project-a 100 / project-b 999 | ✅ |
| **跨项目安全**：`SELECT * FROM project_b.inventory` 在 project-a 上下文 → SQLExecutorValidationError | ✅ |
| 跨项目安全：T2S 路径被诱导生成跨项目 SQL → AIOrchestratorExecutionError | ✅ |
| 跨项目安全：project-a Tool 查 project-b 独有物料 → qty=0 | ✅ |
| **API E2E**：POST /api/ai/chat project-a→100 / project-b→999（route=tool） | ✅ |
| API E2E：未知 project_id → 404 | ✅ |
| API E2E：不传 project_id → 默认 Orchestrator（旧行为） | ✅ |
| API E2E：project_id=vietnam-wms → 与默认等价 | ✅ |

Text-to-SQL 的 LLM Generator 用确定性 Fake（记录 database_context /
allowed_tables / schema_name 并返回固定 SQL）——验证的是 **Schema 选择 →
Validator → Executor 的数据源切换**（真实 LLM 生成属于 Phase 3.7.x E2E
范畴；Fake 返回值不影响对数据源切换本身的判定）。

## 9. 测试结果汇总

| 命令 | 结果 |
|---|---|
| `pytest -q` | ✅ **1171 passed, 165 skipped** |
| `RUN_DB_TESTS=1 pytest -q` | ✅ **1298 passed, 38 skipped** |
| `python -m compileall backend` | ✅ 通过 |
| LSP / lint | ✅ 0 错误 |

单元测试（不依赖 DB）：
- `tests/test_project_registry.py`：16 项（known/unknown/empty/非法格式/
  重复注册/unregister/schema 标识符校验/frozen/敏感字段白名单/默认注册表）
- `tests/test_engine_provider.py`：14 项（缓存 per key、两连接两 Engine、
  未注册拒绝、primary 委托全局 Engine、保留键不可覆盖、URL 为空拒绝、
  无 URL 字符串参数等）

数据清理：teardown `DROP SCHEMA ... CASCADE`；实测跑完后
`information_schema.schemata` 无 `project_%` 残留。

## 10. 修改 / 新增文件清单

| 操作 | 路径 | 说明 |
|---|---|---|
| 新增 | `backend/app/projects/registry.py` | ProjectRegistration + ProjectRegistry Protocol + InMemoryProjectRegistry + 默认注册表 |
| 新增 | `backend/app/projects/engine_provider.py` | DatabaseEngineProvider Protocol + Default 实现（primary 委托全局 Engine） |
| 新增 | `backend/app/services/project_orchestrator_factory.py` | per-project Orchestrator 组装（Service 层） |
| 修改 | `backend/app/api/orchestrator_chat.py` | `_build_orchestrator_for_project_id` 委托工厂；`ProjectNotFoundError` → 404；删除 `_ProjectedProvider` |
| 修改 | `backend/app/services/ai_orchestrator_service.py` | `_run_text_to_sql` 的 `resolve()` 改 `to_thread`（接线 bug 修复，1 行 + 注释） |
| 修改 | `backend/app/services/sql_executor_service.py` | `_execute_read_only` 支持 `SET LOCAL search_path`（schema_name 白名单校验） |
| 新增 | `tests/test_project_registry.py` | Registry 单元测试（16 项） |
| 新增 | `tests/test_engine_provider.py` | Engine Provider 单元测试（14 项） |
| 新增 | `tests/test_project_datasource_isolation.py` | 数据源隔离集成测试（16 项，RUN_DB_TESTS=1） |
| 新增 | 本 ADR | — |

**未修改**（任务书 §二禁令）：AI Router 路由规则、RAG / Reranker 核心逻辑、
SQL Validator 安全规则、Tool Framework 业务能力、Text-to-SQL Generator、
`/api/chat`、数据库结构（无迁移）、RAG 知识库（继续全局共享）。

## 11. 已知限制

1. **单实例 schema 隔离 vs 真实多库**：测试用同一 PG 实例的两个 schema
   验证切换；`DefaultDatabaseEngineProvider.register_connection` 已支持
   独立 DATABASE_URL，但真实第二生产库接入留待后续阶段。
2. **InMemoryProjectRegistry**：进程内代码级注册，重启即重置；多进程
   部署（uvicorn workers）时各进程注册表需一致（生产建议配置文件 /
   数据库注册中心，本阶段只设计接口）。
3. **RAG 未按项目拆分**：知识库全局共享（任务书 §十三 明确保持现状）；
   project-a 的 RAG 问题可能检索到全局知识。
4. **get_inventory 的表名/字段名沿用全局配置**：只切换 schema
   （`WMS_INVENTORY_TABLE` 等仍全局）；两个项目表结构必须同构。
5. **API 协议变更**：未注册 project_id 从"静默接受"变为 404 —— 这是
   任务书 §八 要求的行为，但属于外部可见变更。
6. Text-to-SQL 隔离测试的 Generator 是 Fake（真实 LLM 的 SQL 生成质量
   不影响数据源切换判定，但未在真实 LLM 下重复验证双项目路径）。

## 12. 后续方向（仅记录，不在本阶段实施）

- 生产多数据库连接配置（YAML / 环境变量 / 数据库注册中心）；
- RAG 知识库按项目隔离（project_id → 独立 knowledge 空间）；
- 注册中心与权限系统联动（project → 允许的用户 / 角色）；
- 更多项目级 Tool（get_purchase_order 等接入同一工厂模式）。
