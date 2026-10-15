# DB-gated 测试运行指南（专用测试数据库）

阶段：Phase 7 Recovery M2.3 A2 验收配套文档
状态：`Docker Desktop 当前不可用 ⇒ 本文档尚未实际执行过，步骤未经实跑验证`

---

## 1. 为什么需要专用测试数据库

A2 的 DB 级断言（服务端 `PROJECT_ID` 过滤、`project_id IS NULL` 不可见、跨项目
拒绝、Trace / Timeline 真实读取、只读性、DB residue）必须打在**独立测试库**上：

- 应用库 `wms_ai`（`docker-compose.yml` → `wms-ai-postgres`，宿主机 5432）是开发库，
  **禁止**作为测试库；
- `tests/conftest.py` 的会话级残留守卫会 `DELETE FROM ai_ops.* WHERE id > :水位`
  （`engine.begin()` 写操作）——指向应用库时会删数据；
- 全量 DB 套件会写入 / 清理四张观测表。

## 2. 隔离边界

| 维度 | 应用库（禁止用于测试） | 测试库（`docker-compose.test.yml`） |
| --- | --- | --- |
| 容器名 | `wms-ai-postgres` | `wms-ai-postgres-test` |
| 数据库名 | `wms_ai` | `wms_ai_test` |
| 命名卷 | `wms-ai-postgres-data` | `wms-ai-postgres-test-data` |
| 宿主机端口 | 5432 | 5433 |
| 用户 / 密码 | `POSTGRES_USER` / `POSTGRES_PASSWORD` | `WMS_TEST_DB_USER` / `WMS_TEST_DB_PASSWORD`（默认 `wms_test` / `wms_test_pw`） |

`docker-compose.test.yml` **不引用**、**不修改** `docker-compose.yml` 与仓库根 `.env`。

## 3. 环境变量优先级（实测结论）

`backend/app/config.py`：

```text
L26   load_dotenv()                     # override=False ⇒ 真实环境变量优先于 .env
L29-34 _get_str(): os.getenv(key) 为 None 或 "" 时返回 default（默认 ""）
L939  settings = Settings()             # 模块级实例化 ⇒ 导入时求值
```

`backend/app/db/session.py`：

```text
L33-34  _engine / _SessionLocal 模块级缓存（懒加载，非导入时创建）
L62-71  get_engine(): url 为空 ⇒ 返回 None；否则构造并缓存 Engine
L95-99  reset_engine_cache(): 测试辅助，清空缓存
```

### 关键坑（Windows + PowerShell）

**`$env:DATABASE_URL=""` 会"删除"该环境变量**（Windows `SetEnvironmentVariable`
语义：空串 = 删除）。实测：

```powershell
$env:PROBE_EMPTY=""
python -c "import os; print(os.getenv('PROBE_EMPTY'))"   # → None（不是 ""）
```

因此"把 `DATABASE_URL` 置空"**不能**禁用数据库——子进程看不到该变量，
`load_dotenv()` 转而加载 `.env` 中的应用库配置，`get_engine()` 仍返回指向
**`wms_ai`** 的 Engine。**任何"空变量隔离 DB"的做法都不可靠。**

### 唯一可靠的注入方式

给进程设置一个**非空**的 `DATABASE_URL`，且必须在导入 `backend.app.config`
**之前**生效（`settings = Settings()` 在导入时求值）。真实环境变量优先于 `.env`，
因此会覆盖应用库配置：

```powershell
$env:DATABASE_URL="postgresql+psycopg://wms_test:wms_test_pw@localhost:5433/wms_ai_test"
```

**不需要**修改 `session.py` / `config.py` / 共享 Engine，也**不需要**
`dependency_overrides`。`get_engine()` 会从 `settings.database.url` 读到测试库 URL，
所有 Repository / 残留守卫随之只作用于 `wms_ai_test`。

## 4. 运行步骤（需 Docker 可用；当前未验证）

```powershell
# 0) 前置检查
docker ps                                    # 必须成功；失败则停止，不得改用应用库
docker compose -f docker-compose.test.yml config   # 配置校验（无需 daemon）

# 1) 启动专用测试库（独立容器 / 卷 / 端口）
docker compose -f docker-compose.test.yml up -d
docker compose -f docker-compose.test.yml ps       # 确认 healthy

# 2) 注入测试库 URL 并初始化 schema（幂等 create_all）
$env:DATABASE_URL="postgresql+psycopg://wms_test:wms_test_pw@localhost:5433/wms_ai_test"
python -m backend.app.db.init_db

# 2b) 证明进程用的是测试库（必做，见 §5）
python -c "from backend.app.config import settings; print(settings.database.url)"
# 期望输出必须包含 :5433/wms_ai_test

# 3) 运行 DB-gated 测试
$env:RUN_DB_TESTS="1"
python -m pytest -q tests/test_tool_observability_history_api_db.py
python -m pytest -q tests/test_tool_observability_history_filtering_db.py
python -m pytest -q tests/test_tool_observability_persistent_metrics_db.py
python -m pytest -q tests/test_assistant_trace_api_db.py
python -m pytest -q tests/test_assistant_timeline_db_e2e.py
python -m pytest -q tests/test_assistant_timeline_concurrency_db_e2e.py

# 4) residue 复查（在测试库上）
#    由 tests/conftest.py::_observability_residue_check 在会话收尾自动断言；
#    如需手动复查，连接 wms_ai_test 后对四张 ai_ops 观测表执行 COUNT(*)。

# 5) 清理（仅测试库卷）
docker compose -f docker-compose.test.yml down -v
```

**禁止**：不带 `-f docker-compose.test.yml` 执行 `docker compose down -v`
（会删除应用库卷 `wms-ai-postgres-data`）。

## 5. A2 验收清单（在上述环境验证）

| # | 验收项 | 对应测试 |
| --- | --- | --- |
| 1 | A2-04/05 使用服务端 `PROJECT_ID` 过滤 | `test_tool_observability_history_filtering_db.py::test_server_scope_limits_visible_records`、`test_tool_observability_persistent_metrics_db.py::test_all_records` |
| 2 | `project_id IS NULL` 记录不可见 | `test_tool_observability_history_api_db.py::test_null_project_record_is_invisible` |
| 3 | 跨项目查询被拒绝 | `test_tool_observability_history_filtering_db.py::test_client_project_id_is_ignored` / `test_out_of_scope_project_records_are_invisible` |
| 4 | Trace / Timeline 真实 DB 读取 | `test_assistant_trace_api_db.py`、`test_assistant_timeline_db_e2e.py`、`test_assistant_timeline_concurrency_db_e2e.py` |
| 5 | 查询前后记录状态不变、只读 | 各 DB 文件的 teardown residue 断言 + `conftest.py` 残留守卫 |
| 6 | 残留守卫仅作用于测试库 | 由 §3 的非空 `DATABASE_URL` 注入保证（Engine 指向 `wms_ai_test`） |
| 7 | DB residue = 0 | `conftest.py::_observability_residue_check`（会话收尾零残留断言） |

## 6. 当前阻塞

Docker Desktop 未运行（`dockerDesktopLinuxEngine` pipe 不存在）⇒ §4 全部步骤
**未执行**，DB 级验收 **UNVERIFIED**。恢复 Docker 后按本文档执行即可，无需改代码。
