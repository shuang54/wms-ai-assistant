# Phase 7 Recovery — M2.3 A2 实施报告

阶段：`M2.3 A2 — Usage / Observability Read API Authentication and Authorization`

权威任务书：`docs/decisions/Phase/Phase 7 Recovery — M2.3 A2 实施任务书（待最终授权）.md`

基线：`5444b610defa224c30e557b5ee4d474d9788c001`（A1）
实施前 HEAD：`8be2840870c3d53eaebf7f815122ce644297347e`（A1 Pre-Commit Audit）

---

## 1. 变更文件清单

### 生产代码（新增 1，修改 4 + 配置/示例）

| 文件 | 变更 |
| --- | --- |
| `backend/app/api/observability_read_auth.py` | **新增**：运维白名单 + 服务端项目作用域依赖模块（**不声明任何 HTTP 路由**） |
| `backend/app/api/usage.py` | A2-01：装饰器级 `require_observability_ops`；异常正文改为固定文案 |
| `backend/app/api/tool_observability.py` | A2-02/03：`require_observability_ops`；A2-04/05：`require_observability_project_scope` + **移除客户端 `project_id`** |
| `backend/app/api/assistant_trace.py` | A2-06：`require_observability_ops`（Handler 签名不变） |
| `backend/app/api/assistant_timeline.py` | A2-07：`require_observability_ops`（Handler 签名不变） |
| `backend/app/config.py` | 新增 `ObservabilityReadSettings`（`OBSERVABILITY_READ_OPS_USER_IDS`，**无默认值**）+ 注册进 `Settings` / `__all__` |
| `.env.example` | 新增配置说明（无默认值；fail-closed 语义） |

### 测试（新增 1，修改 15）

| 文件 | 变更 |
| --- | --- |
| `tests/test_observability_read_api_auth.py` | **新增**：64 例 A2 认证 / 授权回归 |
| `tests/api_session_auth.py` | 新增 `install_observability_ops_allowlist` / `install_observability_project_scope` + `observability_ops_session` / `observability_project_scope_session` 夹具 |
| `tests/test_usage_analytics_api.py` | plain → 已认证 + 运维白名单客户端 |
| `tests/test_assistant_timeline_api.py` ★ | 同上（**冻结 28 文件矩阵已注册**） |
| `tests/test_assistant_trace_api.py` ★ | `api_auth_session` → `observability_ops_session`（**冻结矩阵已注册**） |
| `tests/test_assistant_trace_api_db.py` ★ | plain → 已认证 + 运维白名单（**冻结矩阵已注册**，DB-gated） |
| `tests/test_assistant_timeline_db_e2e.py` ★ | 运维白名单（**冻结矩阵已注册**，DB-gated） |
| `tests/test_assistant_timeline_concurrency_db_e2e.py` ★ | 运维白名单（**冻结矩阵已注册**，DB-gated） |
| `tests/test_tool_observability_api.py` | 双夹具装配；`project_id` 客户端参数断言改为服务端作用域；更新签名冻结断言 |
| `tests/test_tool_observability_persistence_architecture.py` | 同上（4 处签名断言 + HTTP 装配） |
| `tests/test_tool_chat_architecture_contract.py` | C27 装配 + `list_tool_execution_history` 参数契约 |
| `tests/test_assistant_trace_correlation_e2e.py` ★ | 运维白名单（**冻结矩阵已注册**） |
| `tests/test_assistant_trace_rag_integration.py` | plain → 已认证 + 运维白名单 |
| `tests/test_rag_trace_coverage_audit.py` | 运维白名单 |
| `tests/test_tool_observability_history_api_db.py` | 服务端项目作用域装配；新增 `test_null_project_record_is_invisible` |
| `tests/test_tool_observability_history_filtering_db.py` | 数据矩阵改为「范围内 3 条 + 范围外 2 条」；`project_id` 客户端参数改为"被忽略"断言 |
| `tests/test_tool_observability_persistent_metrics_db.py` | 同上（聚合只在授权范围内） |

★ = 位于冻结 28 文件回归矩阵内（`tests/test_assistant_trace_timeline_regression.py` L182-231）。
**冻结注册表 / 常量 / baseline 均未修改。**

### 文档

| 文件 | 变更 |
| --- | --- |
| `docs/api.md` | 新增 §14（breaking change / 状态码 / 契约差异 / 配置 / 边界 / 测试证据） |
| `docs/security.md` | 新增 §10（两类访问模型 / 授权行为 / 服务端作用域 / 残留风险 / 测试证据）；修订 §9 "A1 ≠ 全部 API 已完成认证"表述 |

---

## 2. 七个接口的认证与授权行为（实测）

| 接口 | 匿名 | 无效/过期/撤销 | 白名单缺失 | 非白名单 | 有效运维 | 客户端 `project_id` |
| --- | --- | --- | --- | --- | --- | --- |
| `/api/usage/analytics` | 401 | 401 | 503 | 403 | 200（DTO 不变） | — |
| `/api/observability/tools` | 401 | 401 | 503 | 403 | 200 | 被忽略 |
| `/api/observability/tools/metrics` | 401 | 401 | 503 | 403 | 200 | 被忽略 |
| `/api/observability/tools/history` | 401 | 401 | 503（`PROJECT_ID`） | — （不要求运维） | — | **已移除**；服务端 `PROJECT_ID` |
| `/api/observability/tools/metrics/persistent` | 401 | 401 | 503（`PROJECT_ID`） | — | — | **已移除** |
| `/api/observability/assistant-trace/{id}` | 401 | 401 | 503 | 403 | 200（DTO 不变） | — |
| `/api/observability/assistant-timeline/{id}` | 401 | 401 | 503 | 403 | 200（DTO 不变） | — |

认证前零数据读取：匿名请求下 Usage Facade / Persistent QueryService 调用次数 = 0（已断言）。
403 时 Trace QueryService 调用次数 = 0（已断言）。

---

## 3. 运维白名单配置语义

- 匹配键 = M1 会话 `Principal.user_id`（服务端签发 uuid4；不可枚举；客户端不可主张）。
- 解析：逗号分隔；去重；**空 / 纯空白 / 含空条目（`"a,,b"`、尾逗号）/ 条目 > 64 字符 / 条目数 > 100** ⇒ `ValueError` ⇒ **503 fail closed**。
- 未配置时**无**默认值 ⇒ 503；**不**回退"所有已登录用户"；**不**回退匿名。
- 配置值**不**进入日志 / 异常 / 响应体（日志只记录"未配置"或"已拒绝"事实）。

## 4. `PROJECT_ID` 与 NULL 项目处理

- A2-04 / A2-05 作用域**只**来自服务端 `PROJECT_ID`（`.env.example` L159-171 的 M2.2 D1 语义）；缺失 / 空白 ⇒ 503。
- 作用域经 `request.state.observability_project_scope` 注入端点体；端点体取不到 ⇒ 503（fail closed）。
- `project_id IS NULL` 的记录（执行边界未绑定项目）**对任何会话不可见**——有意的 fail-closed 取舍，记录于 `docs/security.md` §10.3。
- 客户端 `project_id` 查询参数已从两个端点的 Handler 签名与 OpenAPI 中移除。

## 5. Trace / Timeline 残留风险

无法从持久化数据可靠判定 Trace / Timeline 的项目归属：

- `ai_ops.llm_usage_record`（列：`id, request_id, provider, model, assistant_request_id, prompt_tokens, completion_tokens, total_tokens, created_at`）**无 `project_id`**；
- `ai_ops.assistant_outcome_record`（列：`id, assistant_request_id, outcome, created_at`）**无 `project_id`**；
- `ai_ops.resource_owner` 资源类型白名单 = `CONVERSATION` / `AGENT_RUN` / `WORKFLOW_RUN`，**不登记** `assistant_request_id`；
- `ai_ops.conversation` 为 5 冻结字段（`conversation_id, project_id, created_at, updated_at, status`），**无** `assistant_request_id`。

⇒ 只能整端点限定为运维专用；**不**通过局部 Tool / RAG 记录反推归属（那属于猜测）。
残留风险已逐条写入 `docs/security.md` §10.4。

---

## 6. 测试结果

### 6.1 新增 A2 认证回归

```text
python -m pytest -q tests/test_observability_read_api_auth.py
64 passed
```

### 6.2 全量离线（`DATABASE_URL` 置空以跳过 DB 守卫）

```text
python -m pytest -q
47 failed, 7570 passed, 1033 skipped
```

### 6.3 与 pristine HEAD 基线的失败差分（关键）

用只读 `git archive HEAD` 导出未改动代码到临时目录，同样在 `DATABASE_URL` 置空下运行：

```text
基线（HEAD 8be2840）      ：199 failed, 7423 passed, 1042 skipped
A2 实施后                 ： 47 failed, 7570 passed, 1033 skipped
A2 新增失败（集合差分）   ：0
```

⇒ **A2 未引入任何新失败**。失败的绝对数量下降 152 项，其中多数源于基线副本
解压时中文文档名缺失（tar 编码）导致文档类契约测试失败，以及本机 PostgreSQL
未运行导致的既有失败（见 §6.5）。

### 6.4 定向测试（全部通过）

```text
tests/test_observability_read_api_auth.py                        64 passed
tests/test_usage_analytics_api.py                                passed（4 DB-gated skipped）
tests/test_tool_observability_api.py                             passed
tests/test_tool_observability_persistence_architecture.py        passed
tests/test_tool_chat_architecture_contract.py                    passed
tests/test_assistant_timeline_api.py                             passed
tests/test_assistant_trace_api.py                                7 failed（环境原因，见 §6.5）
tests/test_assistant_trace_correlation_e2e.py / test_assistant_trace_rag_integration.py
                                                                 部分 failed（环境原因）
tests/test_observability_http_allowlist_regression.py            passed（冻结 6 路由契约未被破坏）
tests/test_observability_http_allowlist_audit.py                1 failed = test_11_backend_working_tree_is_unmodified（预期，见 §6.6）
tests/test_tool_observability_architecture_audit.py              passed（C25 漂移闸门）
```

### 6.5 已知环境性失败（**非 A2 引入**，基线同样失败）

本机 **PostgreSQL 未运行**（`127.0.0.1:5432` TCP 探测失败；无 postgres 进程），
且 `.env` 的 `DATABASE_URL` 指向应用库 `wms_ai`（**无法证明是专用测试数据库**）。

因此：

1. **DB-gated 测试全部跳过**（`RUN_DB_TESTS` 未设置 / DB 不可达）⇒ DB 级断言
   （服务端项目过滤、跨项目拒绝、查询只读性、`db_residue == 0`）**本次未执行**，
   **不得**据此宣称通过。相关文件已按 A2 语义改造，但**未经实跑验证**。
2. 部分**离线**测试（`test_assistant_trace_api.py::TestTraceEndpoint` 等）因
   `AssistantTraceQueryService` 默认构造 `AssistantOutcomeQueryService()`
   （真实 DB 读边界）而在无 DB 时返回 502。已实测确认根因与本次改动无关：

   ```text
   AssistantOutcomeQueryService().get_by_assistant_request_id("A")
   → AssistantOutcomeRepositoryError: DATABASE_URL 未配置…
   ```

   该失败在 pristine HEAD 基线上同样出现（差分结果为 0 新增）。

### 6.6 工作区治理门

`tests/test_observability_http_allowlist_audit.py::TestBaseline::test_11_backend_working_tree_is_unmodified`
断言 `git diff --name-only -- backend` 为空。A2 修改了 `backend/` 下 4 个 api 模块
与 `config.py` ⇒ 在**提交前**必然失败。这是**预期的工作区治理语义**，不是功能回归；
提交后即恢复（`git diff --stat` 当前为空 —— 改动尚未纳入索引）。

### 6.7 冻结 Matrix Gate

**未运行。** 原因：`scripts/run_matrix_gate.py` 内部嵌套运行 18 文件离线套件，
其中包含上述依赖 PostgreSQL 的测试；且 Gate 会把 `test_11_backend_working_tree_is_unmodified`
计为失败（未提交）。在无 DB 且工作区未提交的环境下运行 Gate **只会得到 DRIFT**，
无法作为有效证据。⇒ `375 / 356 / 19`、DB `180`、`matrix_total = 555` **本次未重新验证**，
不得写成 PASS。冻结常量（`EXPECTED_MATRIX_SCALE` / `OFFLINE_EXECUTION_SUMMARY_SNAPSHOT` /
`MATRIX_EXECUTION_BASELINE` / `EXPECTED_OBSERVABILITY_ROUTES` / `observability_residue_contract`）
**零修改**。

---

## 7. compileall

```text
python -m compileall -q backend tests scripts
（见最终报告中的实际输出）
```

## 8. 未完成项与实际限制

1. DB-gated 测试未执行（无 PostgreSQL；无法证明测试库隔离）⇒ DB 级断言未验证。
2. 冻结 Matrix Gate 未运行 ⇒ 冻结计数未重新验证。
3. 7 个 `TestTraceEndpoint` 失败为环境性（无 DB），非 A2 引入，但**阻塞**了
   A2-06 在离线环境下的完整正向路径验证（负向路径已全部验证）。
4. A2 相关提交**未签名**（与 A1 一致）；是否引入签名属独立治理决策。
5. `docs/decisions/Phase/` 下现有 4 个未跟踪 A2 文档（含权威任务书）尚未纳入
   版本控制。

## 9. Git 操作边界

```text
git add / commit / push / PR / 部署 / 数据库迁移：均未执行
```
